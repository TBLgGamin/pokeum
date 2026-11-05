"""Local reference catalogue backed by SQLite.

The store owns the schema and every read/write of card and set metadata plus the
perceptual hashes. It is deliberately a thin, synchronous wrapper over the
standard-library :mod:`sqlite3`: no ORM, no network, so it is trivially testable
against a temp-file database.

Embeddings do not live here — they are large float matrices kept as ``.npy``
files by :mod:`app.reference.index`. The store holds everything small and
queryable: the columns fusion and OCR validation need to filter on.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Iterator
from pathlib import Path

from app.core import constants
from app.models import CardRef

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"


def _portable_path(stored: str) -> str:
    """Return a cached-file path respelled for the running platform.

    Paths are recorded with the separator of whichever machine ran the sync, so
    a catalogue built on Windows carries backslashes that POSIX reads as
    ordinary filename characters rather than separators. Normalizing on read is
    what lets one prebuilt ``reference.db`` be shipped to either platform — the
    deployment flow does exactly that.

    Args:
        stored: Path exactly as recorded in the database.

    Returns:
        The same location, spelled with the current platform's separator.
    """
    # Backslash is a legal filename character on POSIX, but every path here is
    # one we generated ourselves under data/, so treating it as a separator is
    # unambiguous and beats silently losing the file on the other platform.
    return str(Path(stored.replace("\\", "/")))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    series TEXT,
    release_date TEXT,
    release_year INTEGER,
    card_count_total INTEGER,
    card_count_official INTEGER,
    set_code TEXT,
    symbol_url TEXT,
    symbol_path TEXT,
    synced_at TEXT
);
CREATE TABLE IF NOT EXISTS cards (
    id TEXT PRIMARY KEY,
    set_id TEXT NOT NULL,
    name TEXT NOT NULL,
    number TEXT NOT NULL,
    number_key TEXT,
    number_total INTEGER,
    rarity TEXT,
    era TEXT,
    release_year INTEGER,
    image_url TEXT,
    image_path TEXT,
    has_reverse INTEGER DEFAULT 0,
    has_first_edition INTEGER DEFAULT 0,
    has_holo INTEGER DEFAULT 0,
    has_normal INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_cards_set ON cards(set_id);
CREATE INDEX IF NOT EXISTS idx_cards_number ON cards(number_key, number_total);
CREATE TABLE IF NOT EXISTS hashes (
    card_id TEXT PRIMARY KEY,
    phash TEXT,
    dhash TEXT,
    phash_r TEXT,
    phash_g TEXT,
    phash_b TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

_HASH_COLUMNS = ("phash", "dhash", "phash_r", "phash_g", "phash_b")

# Ids per IN(...) query in get_cards; far below SQLite's default host-parameter
# limit (999 historically, 32k in modern builds) so any build is safe.
_SQL_IN_CHUNK = 500


def reference_db_path(data_dir: str | Path) -> Path:
    """Return the standard path of the reference database under ``data_dir``."""
    return Path(data_dir) / "reference.db"


def normalize_number(number: str) -> str:
    """Return a collator-friendly form of a collector number.

    Strips surrounding whitespace, upper-cases, and drops leading zeros from
    purely numeric ids so that ``025`` from OCR matches ``25`` in the catalogue.

    Args:
        number: A printed collector number or local id.

    Returns:
        The normalized key used for number-based lookups.
    """
    text = number.strip().upper()
    if text.isdigit():
        return str(int(text))
    return text


def era_for_year(year: int | None) -> str:
    """Return the era bucket (``wotc``/``modern``) for a set's release year."""
    if year is not None and year <= constants.WOTC_ERA_UNTIL_YEAR:
        return constants.ERA_WOTC
    return constants.ERA_MODERN


class ReferenceStore:
    """A SQLite-backed catalogue of sets, cards, and perceptual hashes."""

    def __init__(self, db_path: str | Path) -> None:
        """Open (creating if needed) the database and ensure the schema exists.

        Args:
            db_path: Path to the SQLite file. Its parent directory is created.
        """
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the FastAPI service runs sync routes in a
        # thread pool and sync uses worker threads. Access is read-mostly and
        # CPython's sqlite3 serializes calls, so sharing the connection is safe.
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        if self.get_meta("schema_version") is None:
            self.set_meta("schema_version", SCHEMA_VERSION)

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()

    def __enter__(self) -> ReferenceStore:
        """Enter a context manager, returning this store."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Close the connection when leaving the context manager."""
        self.close()

    # --- Meta ---------------------------------------------------------------
    def get_meta(self, key: str, default: str | None = None) -> str | None:
        """Return a stored meta value, or ``default`` when the key is absent."""
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        """Insert or replace a meta key/value pair."""
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    # --- Sets ---------------------------------------------------------------
    def upsert_set(
        self,
        *,
        set_id: str,
        name: str,
        series: str | None,
        release_date: str | None,
        card_count_total: int | None,
        card_count_official: int | None,
        set_code: str | None,
        symbol_url: str | None,
        synced_at: str,
    ) -> None:
        """Insert or update a set row. Preserves any existing ``symbol_path``."""
        year = _year_from_date(release_date)
        self._conn.execute(
            """
            INSERT INTO sets(id, name, series, release_date, release_year,
                             card_count_total, card_count_official, set_code,
                             symbol_url, synced_at)
            VALUES(:id, :name, :series, :date, :year, :total, :official, :code,
                   :symbol_url, :synced_at)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                series = excluded.series,
                release_date = excluded.release_date,
                release_year = excluded.release_year,
                card_count_total = excluded.card_count_total,
                card_count_official = excluded.card_count_official,
                set_code = excluded.set_code,
                symbol_url = excluded.symbol_url,
                synced_at = excluded.synced_at
            """,
            {
                "id": set_id,
                "name": name,
                "series": series,
                "date": release_date,
                "year": year,
                "total": card_count_total,
                "official": card_count_official,
                "code": set_code,
                "symbol_url": symbol_url,
                "synced_at": synced_at,
            },
        )
        self._conn.commit()

    def set_symbol_path(self, set_id: str, symbol_path: str) -> None:
        """Record the local path of a downloaded set-symbol image."""
        self._conn.execute("UPDATE sets SET symbol_path = ? WHERE id = ?", (symbol_path, set_id))
        self._conn.commit()

    def known_set_ids(self) -> set[str]:
        """Return the ids of all sets currently stored."""
        rows = self._conn.execute("SELECT id FROM sets").fetchall()
        return {row["id"] for row in rows}

    def known_set_codes(self) -> frozenset[str]:
        """Return every printed set code in the catalogue (stored uppercase).

        Used to validate OCR-read set codes: random uppercase scene text must
        not be mistaken for a real set code, because a "set code" observation
        both skips the symbol signal and penalizes every candidate in fusion.
        """
        rows = self._conn.execute(
            "SELECT DISTINCT set_code FROM sets WHERE set_code IS NOT NULL"
        ).fetchall()
        return frozenset(row["set_code"] for row in rows)

    def set_card_count(self, set_id: str) -> int | None:
        """Return the stored total card count for a set, if known."""
        row = self._conn.execute(
            "SELECT card_count_total FROM sets WHERE id = ?", (set_id,)
        ).fetchone()
        return row["card_count_total"] if row else None

    def sets_with_symbols(self) -> list[tuple[str, str]]:
        """Return ``(set_id, symbol_path)`` for every set with a cached symbol."""
        rows = self._conn.execute(
            "SELECT id, symbol_path FROM sets WHERE symbol_path IS NOT NULL"
        ).fetchall()
        return [(row["id"], row["symbol_path"]) for row in rows]

    def sets_needing_symbols(self) -> list[tuple[str, str]]:
        """Return ``(set_id, symbol_url)`` for sets with a URL but no cached symbol."""
        rows = self._conn.execute(
            "SELECT id, symbol_url FROM sets WHERE symbol_url IS NOT NULL AND symbol_path IS NULL"
        ).fetchall()
        return [(row["id"], row["symbol_url"]) for row in rows]

    def symbol_templates(self) -> list[tuple[str, str, str]]:
        """Return ``(set_id, era, symbol_path)`` for every set with a cached symbol."""
        rows = self._conn.execute(
            "SELECT id, release_year, symbol_path FROM sets WHERE symbol_path IS NOT NULL"
        ).fetchall()
        return [
            (row["id"], era_for_year(row["release_year"]), _portable_path(row["symbol_path"]))
            for row in rows
        ]

    # --- Cards --------------------------------------------------------------
    def upsert_card(
        self,
        *,
        card_id: str,
        set_id: str,
        name: str,
        number: str,
        number_total: int | None,
        rarity: str | None,
        release_year: int | None,
        image_url: str | None,
        has_reverse: bool,
        has_first_edition: bool,
        has_holo: bool,
        has_normal: bool,
    ) -> None:
        """Insert or update a card row. Preserves any existing ``image_path``."""
        self._conn.execute(
            """
            INSERT INTO cards(id, set_id, name, number, number_key, number_total,
                              rarity, era, release_year, image_url,
                              has_reverse, has_first_edition, has_holo, has_normal)
            VALUES(:id, :set_id, :name, :number, :number_key, :number_total,
                   :rarity, :era, :year, :image_url,
                   :reverse, :first_ed, :holo, :normal)
            ON CONFLICT(id) DO UPDATE SET
                set_id = excluded.set_id,
                name = excluded.name,
                number = excluded.number,
                number_key = excluded.number_key,
                number_total = excluded.number_total,
                rarity = excluded.rarity,
                era = excluded.era,
                release_year = excluded.release_year,
                image_url = excluded.image_url,
                has_reverse = excluded.has_reverse,
                has_first_edition = excluded.has_first_edition,
                has_holo = excluded.has_holo,
                has_normal = excluded.has_normal
            """,
            {
                "id": card_id,
                "set_id": set_id,
                "name": name,
                "number": number,
                "number_key": normalize_number(number),
                "number_total": number_total,
                "rarity": rarity,
                "era": era_for_year(release_year),
                "year": release_year,
                "image_url": image_url,
                "reverse": int(has_reverse),
                "first_ed": int(has_first_edition),
                "holo": int(has_holo),
                "normal": int(has_normal),
            },
        )
        self._conn.commit()

    def set_image_path(self, card_id: str, image_path: str) -> None:
        """Record the local path of a downloaded card image."""
        self._conn.execute("UPDATE cards SET image_path = ? WHERE id = ?", (image_path, card_id))
        self._conn.commit()

    def get_card(self, card_id: str) -> CardRef | None:
        """Return one card as a :class:`CardRef`, or ``None`` if unknown."""
        row = self._conn.execute(_GET_CARD_SQL, (card_id,)).fetchone()
        return _row_to_card(row) if row else None

    def get_cards(self, card_ids: Iterable[str]) -> dict[str, CardRef]:
        """Return every known card among ``card_ids``, keyed by card id.

        One chunked ``IN`` query instead of a round-trip per id — the recognizer
        resolves up to ~150 shortlist candidates per scan through this. Unknown
        ids are silently absent from the result.

        Args:
            card_ids: Card ids to resolve (duplicates are fine).

        Returns:
            Mapping of card id to :class:`CardRef` for the ids that exist.
        """
        unique = list(dict.fromkeys(card_ids))
        resolved: dict[str, CardRef] = {}
        for start in range(0, len(unique), _SQL_IN_CHUNK):
            chunk = unique[start : start + _SQL_IN_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            rows = self._conn.execute(
                "SELECT " + _CARD_SELECT + _CARD_FROM + f" WHERE c.id IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                resolved[row["id"]] = _row_to_card(row)
        return resolved

    def all_cards(self) -> list[CardRef]:
        """Return every card in the catalogue as :class:`CardRef` objects."""
        rows = self._conn.execute(_ALL_CARDS_SQL).fetchall()
        return [_row_to_card(row) for row in rows]

    def card_names(self) -> list[tuple[str, str]]:
        """Return ``(card_id, name)`` for every card, for title-name matching."""
        rows = self._conn.execute("SELECT id, name FROM cards").fetchall()
        return [(row["id"], row["name"]) for row in rows]

    def count_cards(self) -> int:
        """Return the number of cards stored."""
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()["n"])

    def cards_missing_image(self) -> list[tuple[str, str, str]]:
        """Return ``(card_id, set_id, image_url)`` for cards with a URL but no file.

        Includes the set id so callers can compute the cache path without another
        DB read (important when downloading from worker threads).
        """
        rows = self._conn.execute(
            "SELECT id, set_id, image_url FROM cards "
            "WHERE image_url IS NOT NULL AND image_path IS NULL"
        ).fetchall()
        return [(row["id"], row["set_id"], row["image_url"]) for row in rows]

    def cards_with_images(self) -> list[tuple[str, str]]:
        """Return ``(card_id, image_path)`` for every card with a cached image."""
        rows = self._conn.execute(
            "SELECT id, image_path FROM cards WHERE image_path IS NOT NULL"
        ).fetchall()
        return [(row["id"], _portable_path(row["image_path"])) for row in rows]

    def image_paths(self, card_ids: Iterable[str]) -> dict[str, str]:
        """Return the cached image path of every card among ``card_ids``.

        One chunked ``IN`` query in the style of :meth:`get_cards`: the printing
        discriminator resolves a handful of candidates per ambiguous scan and
        must not pay a round-trip each. Cards that exist but have no cached file
        are silently absent, exactly like unknown ids.

        Args:
            card_ids: Card ids to resolve (duplicates are fine).

        Returns:
            Mapping of card id to its platform-normalized cached image path.
        """
        unique = list(dict.fromkeys(card_ids))
        resolved: dict[str, str] = {}
        for start in range(0, len(unique), _SQL_IN_CHUNK):
            chunk = unique[start : start + _SQL_IN_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            # Only "?" placeholders are interpolated; every value is bound.
            rows = self._conn.execute(
                "SELECT id, image_path FROM cards "  # nosec B608
                f"WHERE image_path IS NOT NULL AND id IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                resolved[row["id"]] = _portable_path(row["image_path"])
        return resolved

    def find_card_ids_by_number(self, number: str, number_total: int | None = None) -> list[str]:
        """Return card ids whose collector number (and optional total) match.

        Used by OCR fusion to build the set of cards consistent with a parsed
        ``number/total``. The number is matched on its normalized key so OCR's
        ``025`` finds the catalogue's ``25``.

        Args:
            number: Collector number read from the card.
            number_total: Set total read after the slash, if any; when given it
                must match, which is what disambiguates reprints across sets.

        Returns:
            Matching card ids (possibly empty).
        """
        key = normalize_number(number)
        if number_total is not None:
            rows = self._conn.execute(
                "SELECT id FROM cards WHERE number_key = ? AND number_total = ?",
                (key, number_total),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id FROM cards WHERE number_key = ?", (key,)
            ).fetchall()
        return [row["id"] for row in rows]

    # --- Hashes -------------------------------------------------------------
    def set_hashes(self, card_id: str, values: dict[str, str]) -> None:
        """Insert or replace the perceptual hashes for one card.

        Args:
            card_id: Card the hashes belong to.
            values: Mapping of hash column name to hex string; missing columns
                are stored as ``NULL``.
        """
        payload = {col: values.get(col) for col in _HASH_COLUMNS}
        payload["card_id"] = card_id
        self._conn.execute(
            "INSERT INTO hashes(card_id, phash, dhash, phash_r, phash_g, phash_b) "
            "VALUES(:card_id, :phash, :dhash, :phash_r, :phash_g, :phash_b) "
            "ON CONFLICT(card_id) DO UPDATE SET "
            "phash=excluded.phash, dhash=excluded.dhash, phash_r=excluded.phash_r, "
            "phash_g=excluded.phash_g, phash_b=excluded.phash_b",
            payload,
        )
        self._conn.commit()

    def card_ids_missing_hashes(self) -> list[str]:
        """Return ids of cards that have an image but no stored hashes yet."""
        rows = self._conn.execute(
            "SELECT c.id FROM cards c LEFT JOIN hashes h ON c.id = h.card_id "
            "WHERE c.image_path IS NOT NULL AND h.card_id IS NULL"
        ).fetchall()
        return [row["id"] for row in rows]

    def iter_hashes(self) -> Iterator[tuple[str, dict[str, str]]]:
        """Yield ``(card_id, {column: hex})`` for every card with stored hashes."""
        rows = self._conn.execute(
            "SELECT card_id, phash, dhash, phash_r, phash_g, phash_b FROM hashes"
        ).fetchall()
        for row in rows:
            values = {col: row[col] for col in _HASH_COLUMNS if row[col] is not None}
            yield row["card_id"], values


_CARD_SELECT = (
    "c.id, c.name, c.set_id, s.name AS set_name, s.set_code, c.number, "
    "c.number_total, c.rarity, c.era, c.release_year, c.image_url, c.image_path, "
    "c.has_reverse, c.has_first_edition, c.has_holo, c.has_normal"
)
# Full statements are assembled once from the fixed column list above (never any
# user input), so queries pass a constant string to execute().
_CARD_FROM = " FROM cards c JOIN sets s ON c.set_id = s.id"
_GET_CARD_SQL = "SELECT " + _CARD_SELECT + _CARD_FROM + " WHERE c.id = ?"
_ALL_CARDS_SQL = "SELECT " + _CARD_SELECT + _CARD_FROM


def _row_to_card(row: sqlite3.Row) -> CardRef:
    """Build a :class:`CardRef` from a joined cards/sets row."""
    return CardRef(
        card_id=row["id"],
        name=row["name"],
        set_id=row["set_id"],
        set_name=row["set_name"],
        set_code=row["set_code"],
        number=row["number"],
        number_total=row["number_total"],
        rarity=row["rarity"],
        era=row["era"] or constants.ERA_MODERN,
        release_year=row["release_year"],
        image_url=row["image_url"],
        image_path=_portable_path(row["image_path"]) if row["image_path"] else None,
        has_reverse=bool(row["has_reverse"]),
        has_first_edition=bool(row["has_first_edition"]),
        has_holo=bool(row["has_holo"]),
        has_normal=bool(row["has_normal"]),
    )


def _year_from_date(release_date: str | None) -> int | None:
    """Extract a four-digit year from a ``YYYY-MM-DD`` date string, if present."""
    if not release_date or len(release_date) < 4 or not release_date[:4].isdigit():
        return None
    return int(release_date[:4])
