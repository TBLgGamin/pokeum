"""Acquire candidate card photos into a provenance-tracked staging area (dev-only tool).

`docs/real-photo-data.md` establishes why no public dataset labels photographs by
*exact printing*, and why labelled real photos have to be built up. This tool is
the first half of that: it brings in candidate photos from sources
outside the app, records exactly where every byte came from, and hands them to
the annotator (`scripts/annotate_photos.py`), which decides what each one
actually depicts. Acquisition never assigns a label.

Layout written, and read by the annotator::

    data/photos/staging/<source>/<image_id>.<ext>   # bytes exactly as fetched
    data/photos/staging/<source>/<image_id>.json    # provenance record
    data/photos/ledger.jsonl                        # append-only, one line per image

``image_id`` is a truncated SHA-256 of the image's canonical source URL, so the
same URL always maps to the same id: an image already named in the ledger is
never fetched twice, which makes every run resumable and every re-run cheap. The
ledger is only ever appended to — rewriting it would destroy the provenance of
images already staged, and provenance is the whole reason the file exists.

Bytes are staged exactly as fetched (no re-encode, no resize): the ``sha256`` in
the record must match the file on disk, so any image's origin and licence can be
produced on demand and a takedown request is a ledger query.

Sources are plugins in one registry (:data:`SOURCES`), selected with
``--source``:

* ``local`` — a directory of the operator's own photographs. No network, no
  credential; the default, and the only unconditionally usable source.
* ``roboflow`` — the CC BY 4.0 Roboflow Universe dataset
  ``card-scanner-e4knh/pokemon-card-identification-htpkd`` (5,058 real photos).
  Detection boxes only, so it carries **no printing identity**: hints stay empty
  and the pile feeds detection/rectification robustness and self-supervised
  domain adaptation, never printing labels.
* ``tcgar`` — the TCG-AR academic real evaluation set (51 board scenes, GPL-3.0,
  cite the paper). Multi-card board photos; per-instance identities live in the
  upstream repository, not in a per-image hint.
* ``reddit`` — image posts from r/PokemonTCG and r/pokemoncards. Reddit's User
  Agreement grants a sublicensable licence that covers ML use, so internal
  training and evaluation are defensible; redistribution is not.

A source whose credential is missing fails with one message naming the variable
and the documentation section. Nothing is silently skipped or faked.

Networked sources go through one :class:`HttpFetcher`: a declared User-Agent, a
minimum gap between requests, a byte cap, an image-only content-type gate, and
redirects followed by hand so a redirect cannot smuggle in non-image content.

``hint_*`` fields are parsed from source text as a *starting point for the
annotator only*, and only where the source text is about a single card. A hint
is never a label: the annotator re-derives the printing from the image itself
and records its own evidence.

Sources deliberately **not** implemented (eBay, TCGplayer, Cardmarket,
PSA/CGC/Beckett), the licence position of every source above, and the do-not
list: ``docs/photo-acquisition.md``.
"""

# ruff: noqa: E402 - the sys.path bootstrap has to run before the app imports.
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import tempfile
import time
import zipfile
from collections import Counter
from collections.abc import Callable, Generator, Iterator
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, TextIO

import httpx

# Dev scripts are run as files from a checkout, which puts `scripts/` on the
# path instead of the repository root; `app` is not an installed package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import config, constants
from app.core.logging_config import configure
from app.recognize.factory import open_store
from app.reference.store import ReferenceStore, reference_db_path
from app.signals.ocr import parse_collector_number
from app.vision import imaging

logger = logging.getLogger(__name__)

DOC_PATH = "docs/photo-acquisition.md"

# Staging layout (contract with scripts/annotate_photos.py; do not change one
# side of it alone).
STAGING_DIRNAME = "staging"
LEDGER_FILENAME = "ledger.jsonl"

# The contract's value for "this source states no licence at all". Written
# literally, never left empty, so a record can never be mistaken for one whose
# licence was checked and found permissive.
LICENCE_UNKNOWN = "unknown"

# Content types accepted, and the extension each is staged under. Deliberately
# the same set app.recognize.eval scores (its _IMAGE_SUFFIXES): an acquired .gif
# would be invisible to `pokeum eval` once labelled, so it is refused at the
# gate instead of rotting in staging.
CONTENT_TYPE_SUFFIXES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}
SUFFIX_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

# Identity of an image is the hash of its source URL; 16 hex chars (64 bits) is
# collision-free far beyond any realistic pile and keeps filenames short. The id
# must never contain "__": the annotator writes `{card_id}__{image_id}` names
# that app.recognize.eval.truth_from_name splits on the first "__".
IMAGE_ID_LENGTH = 16

# A card photo over 12 MB is a mis-download, not a better photo: recognition
# caps uploads to INGEST_MAX_SIDE (2000 px) and the rectified card is 630x880.
MAX_IMAGE_BYTES = 12 * 1024 * 1024
# API pages are JSON documents of listing metadata; megabytes of them means the
# endpoint is not what we think it is.
MAX_JSON_BYTES = 8 * 1024 * 1024
# A whole dataset export, streamed to a temporary file rather than held in RAM.
# Roboflow's 5,058-image export and TCG-AR's 169 MB real set both fit easily.
MAX_ARCHIVE_BYTES = 4 * 1024**3
# Below this long side there is not enough card left to rectify and read; search
# and preview endpoints happily serve 225 px thumbnails, which would fill the
# pile with images no signal can use.
MIN_IMAGE_SIDE = 200
# Redirect hops allowed before a chain is treated as hostile or broken. Two are
# normal for storage back-ends (API host -> signed CDN URL).
MAX_REDIRECTS = 3

# Sent on every request: an operator who wants to block this tool must be able
# to, and a server admin reading logs must be able to find out who we are.
USER_AGENT = "pokeum-photo-acquire (+https://github.com/TBLgGamin/pokeum)"
# One request per second per source. Well inside every quota involved, and slow
# enough that a mistake in a query costs the source almost nothing.
DEFAULT_RATE_LIMIT_SECONDS = 1.0
# Politeness by default: a run acquires a bounded batch and is resumable, so
# "more" is another run rather than an unbounded first one. 0 disables the cap.
DEFAULT_LIMIT = 100
# Progress for long runs without one log line per image.
PROGRESS_EVERY = 50

# Uppercase runs that could be a printed set code. app.signals.ocr.parse_set_code
# returns only the *first* such token, which is right for a card's bottom strip
# and wrong for post titles stuffed with NM/PSA/LOT/GET; here every uppercase
# token is considered and the first one the catalogue knows wins. The collector
# number is the genuinely reusable rule, so parse_collector_number is imported.
TITLE_SET_CODE_RE = re.compile(r"\b([A-Z]{2,5})\b")

ROBOFLOW_API_URL = "https://api.roboflow.com"
ROBOFLOW_UNIVERSE_URL = "https://universe.roboflow.com"
ROBOFLOW_KEY_VAR = "ROBOFLOW_API_KEY"
# The surveyed dataset: 5,058 real card photographs under CC BY 4.0. A version
# is resolved from the project metadata when the slug omits one.
ROBOFLOW_DEFAULT_SLUG = "card-scanner-e4knh/pokemon-card-identification-htpkd"
# Every export format ships the same images; coco is the smallest annotation
# payload riding along with them.
ROBOFLOW_EXPORT_FORMAT = "coco"
# Roboflow states licences as prose; map the ones that have an SPDX identifier
# and fall back to a LicenseRef- form rather than inventing one.
ROBOFLOW_LICENCES = {
    "cc by 4.0": "CC-BY-4.0",
    "cc by-sa 4.0": "CC-BY-SA-4.0",
    "cc by-nc 4.0": "CC-BY-NC-4.0",
    "cc by-nc-sa 4.0": "CC-BY-NC-SA-4.0",
    "public domain": "CC0-1.0",
    "cc0 1.0": "CC0-1.0",
    "mit": "MIT",
    "apache 2.0": "Apache-2.0",
}
# CC BY 4.0's attribution clause requires a link to the licence, so the link is
# part of the attribution string itself, not a footnote in this file.
LICENCE_URLS = {
    "CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/",
    "CC-BY-SA-4.0": "https://creativecommons.org/licenses/by-sa/4.0/",
    "CC-BY-NC-4.0": "https://creativecommons.org/licenses/by-nc/4.0/",
    "CC-BY-NC-SA-4.0": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
    "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "GPL-3.0-only": "https://www.gnu.org/licenses/gpl-3.0.html",
}

TCGAR_REPO_URL = "https://github.com/ULiege-VIULab/tcg-ar"
# The repository ships the manual annotations but not the photographs: DATA.md
# lists the real evaluation set (169 MB) as a Google Drive download, and
# scripts/download_assets.py holds the file id below plus this env override
# (which upstream accepts as a bare id or a /d/<id>/ share URL).
TCGAR_REALSET_URL_VAR = "TCGAR_REALSET_URL"
TCGAR_REALSET_DRIVE_ID = "1q34Ka2J5w6Xz_wUnY-MzdBQmRqukZWbH"
# Upstream extracts that archive into `assets/AI database/real`; the same path
# is used to build a stable per-member URL.
TCGAR_MEMBER_PREFIX = "assets/AI database/real"
# README: "Released under the GNU General Public License v3.0". No "or later"
# clause is stated, so the -only form is what the repository actually says.
TCGAR_LICENCE = "GPL-3.0-only"
TCGAR_CITATION = (
    "Cioppa et al. 2026, TCG-AR: Real-Time Multi-View Augmented Reality for "
    "Trading Card Game Streaming, arXiv:2607.02090"
)
_DRIVE_SHARE_ID_RE = re.compile(r"/d/([A-Za-z0-9_-]+)")
# Drive's direct-download host; `confirm=t` skips the virus-scan interstitial
# that would otherwise answer a large public file with HTML.
DRIVE_DOWNLOAD_URL = "https://drive.usercontent.google.com/download"

REDDIT_CLIENT_ID_VAR = "REDDIT_CLIENT_ID"
REDDIT_CLIENT_SECRET_VAR = "REDDIT_CLIENT_SECRET"  # pragma: allowlist secret (env var name)
# The two subreddits where cards are photographed and named in the title.
REDDIT_SUBREDDITS = "PokemonTCG+pokemoncards"
# Reddit user content: the User Agreement's licence grant is sublicensable and
# covers ML use, so internal training/eval is defensible. Redistribution is not,
# which is why this is not an SPDX identifier and never will be.
REDDIT_LICENCE = "reddit-user-content"
REDDIT_IMAGE_HOST = "https://i.redd.it"


class AcquireError(RuntimeError):
    """One candidate could not be acquired; the run counts it and continues.

    Attributes:
        reason: Short, URL-free category used as the summary's tally key, so a
            thousand rejections do not become a thousand distinct reasons.
    """

    def __init__(self, message: str, reason: str = "") -> None:
        """Create an error with a message and an optional tally category.

        Args:
            message: Full, specific message (may name the URL).
            reason: Short category for the run summary; defaults to ``message``.
        """
        super().__init__(message)
        self.reason = reason or message


class SourceConfigError(RuntimeError):
    """A source cannot run at all: missing credential, missing input, bad slug.

    Raised before anything is fetched and reported as a usage error, because the
    fix is always something the operator has to do.
    """


@dataclass(frozen=True)
class Payload:
    """Image bytes exactly as a source served them, plus its content type."""

    data: bytes
    content_type: str


@dataclass(frozen=True)
class Candidate:
    """One image a source offers, before anything has been downloaded.

    Attributes:
        source_url: Canonical URL of the image itself. ``image_id`` is a hash of
            exactly this string, so it must be stable across runs — never a
            signed or time-limited URL.
        page_url: The human-facing page the image belongs to, for auditing.
        title: Listing, post, or member text the source provides; may be empty.
        hint_text: Text that is about *one* card and may therefore be parsed for
            a printing hint. Empty for sources whose text cannot identify a
            single printing (dataset members, multi-card board scenes).
        licence: SPDX-ish licence the source states, or :data:`LICENCE_UNKNOWN`.
        attribution: Credit line the source states, or empty.
        fetch: Returns the image bytes. Lazy, so a ledger skip or ``--dry-run``
            never costs a download.
    """

    source_url: str
    page_url: str
    title: str
    hint_text: str
    licence: str
    attribution: str
    fetch: Callable[[], Payload]


@dataclass(frozen=True)
class SourceRequest:
    """Everything a source plugin needs to enumerate candidates.

    Attributes:
        query: Search text (``reddit``) or dataset slug (``roboflow``).
        path: Input directory (``local``).
        licence: Licence stamped on records by sources that state none
            themselves (``local``).
        attribution: Attribution stamped on those same records.
        fetcher: Shared polite HTTP client.
    """

    query: str
    path: Path | None
    licence: str
    attribution: str
    fetcher: HttpFetcher


SourcePlugin = Callable[[SourceRequest], Generator[Candidate]]


@dataclass(frozen=True)
class Source:
    """One registered place candidate photos come from.

    Attributes:
        name: ``--source`` value.
        summary: One-line description shown by ``--list-sources``.
        candidates: Generator function yielding :class:`Candidate` objects.
        licence_position: What the acquired records will say about licensing.
        credentials: Environment variables the source needs, if any.
        needs_path: Whether ``--path`` is required.
        default_query: Query used when ``--query`` is omitted, if the source has
            a sensible default.
        hints: Whether the source's text can yield a printing hint at all.
    """

    name: str
    summary: str
    candidates: SourcePlugin
    licence_position: str
    credentials: tuple[str, ...] = ()
    needs_path: bool = False
    default_query: str = ""
    hints: bool = False


@dataclass
class AcquireSummary:
    """What one acquisition run staged, skipped, and refused."""

    acquired: int = 0
    duplicates: int = 0
    rejected: Counter[str] = field(default_factory=Counter)


@dataclass(frozen=True)
class TitleHints:
    """What source text suggests about the printing. Never a label.

    Attributes:
        set_code: A set code the catalogue knows, or empty.
        number: Normalized collector number, or empty.
        card_id: A single printing consistent with both, or empty when the text
            is ambiguous. The annotator treats this as a prior, not as truth.
    """

    set_code: str
    number: str
    card_id: str


def image_id_for(source_url: str) -> str:
    """Return the stable image id of a source URL.

    Args:
        source_url: Canonical URL of the image.

    Returns:
        Truncated SHA-256 hex digest, filename-safe and free of ``__``.
    """
    return hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:IMAGE_ID_LENGTH]


def require_credential(env_var: str, source: str) -> str:
    """Return a source's credential from the environment, or say how to set it.

    Args:
        env_var: Name of the environment variable holding the credential.
        source: Source name, for the message.

    Returns:
        The credential, stripped.

    Raises:
        SourceConfigError: When the variable is unset or empty.
    """
    value = config.get(env_var).strip()
    if not value:
        raise SourceConfigError(
            f"--source {source} needs {env_var} in the environment: export it in your shell, "
            f"or add it to .env (never committed), then re-run. What it is and how to get one: "
            f"{DOC_PATH}#{source}"
        )
    return value


def attribution_for(credit: str, licence: str) -> str:
    """Build an attribution string that satisfies an attribution licence.

    CC BY 4.0 requires credit *and* a link to the licence, so both belong in the
    recorded string rather than in a human's memory.

    Args:
        credit: Who to credit (dataset, workspace, author).
        licence: The SPDX-ish licence recorded alongside it.

    Returns:
        The attribution line, with the licence URL appended when one is known.
    """
    url = LICENCE_URLS.get(licence, "")
    parts = [part for part in (credit, licence, url) if part]
    return ", ".join(parts)


def _content_type(response: httpx.Response) -> str:
    """Return a response's bare, lowercased content type without parameters."""
    return response.headers.get("content-type", "").split(";")[0].strip().lower()


class HttpFetcher:
    """Polite, bounded HTTP access shared by every networked source.

    One object enforces, in one place, the rules that make third-party
    acquisition defensible: an honest User-Agent, a minimum gap between
    requests, a hard byte cap, an image-only content-type gate, and redirects
    followed by hand so no hop can deliver something other than an image.
    """

    def __init__(self, *, rate_limit: float, client: httpx.Client | None = None) -> None:
        """Create a fetcher.

        Args:
            rate_limit: Minimum seconds between requests.
            client: Pre-built httpx client (injected in tests). The default one
                has redirects disabled on purpose: :meth:`_open` inspects every
                hop itself.
        """
        self._rate_limit = max(0.0, rate_limit)
        self._client = client or httpx.Client(
            timeout=constants.HTTP_TIMEOUT_SECONDS,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT},
        )
        self._next_request_at = 0.0

    def close(self) -> None:
        """Close the underlying httpx client."""
        self._client.close()

    def _throttle(self) -> None:
        """Wait until the configured gap since the previous request has elapsed."""
        wait = self._next_request_at - time.monotonic()
        if wait > 0:
            logger.debug("rate limit: sleeping %.2fs", wait)
            time.sleep(wait)
        self._next_request_at = time.monotonic() + self._rate_limit

    @contextmanager
    def _open(
        self,
        url: str | httpx.URL,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Iterator[httpx.Response]:
        """Open a streaming GET, following redirects by hand.

        Args:
            url: URL to fetch.
            headers: Extra request headers.
            params: Query parameters; dropped after a redirect, whose Location
                already carries the query the server wants used.

        Yields:
            The final non-redirect response, status-checked, body unread.

        Raises:
            AcquireError: When the chain is too long, a hop has no or a
                non-HTTP location, or the server answers with an error status.
        """
        target = httpx.URL(url)
        for _ in range(MAX_REDIRECTS + 1):
            self._throttle()
            with self._client.stream("GET", target, headers=headers, params=params) as response:
                if not response.is_redirect:
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as error:
                        raise AcquireError(
                            f"GET {target} returned {response.status_code}",
                            reason=f"http {response.status_code}",
                        ) from error
                    yield response
                    return
                location = response.headers.get("location", "")
                if not location:
                    raise AcquireError(
                        f"{target} redirected without a location header", reason="broken redirect"
                    )
                target = target.join(location)
                params = None
                if target.scheme not in ("http", "https"):
                    raise AcquireError(
                        f"refusing redirect to non-HTTP URL {target}", reason="hostile redirect"
                    )
                logger.debug("following redirect to %s", target)
        raise AcquireError(f"too many redirects fetching {url}", reason="redirect loop")

    @staticmethod
    def _check_declared_size(response: httpx.Response, max_bytes: int) -> None:
        """Refuse an oversized body before reading it, when the size is declared.

        Args:
            response: Streaming response whose headers are available.
            max_bytes: Cap in bytes.

        Raises:
            AcquireError: When Content-Length already exceeds the cap.
        """
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > max_bytes:
            raise AcquireError(
                f"{response.url} declares {declared} bytes, over the {max_bytes} byte cap",
                reason="over byte cap",
            )

    def _read_capped(self, response: httpx.Response, max_bytes: int) -> bytes:
        """Read a response body, aborting as soon as it exceeds the cap.

        Args:
            response: Streaming response.
            max_bytes: Cap in bytes.

        Returns:
            The body bytes.

        Raises:
            AcquireError: When the body exceeds the cap.
        """
        self._check_declared_size(response, max_bytes)
        buffer = bytearray()
        for chunk in response.iter_bytes():
            buffer.extend(chunk)
            if len(buffer) > max_bytes:
                raise AcquireError(
                    f"{response.url} exceeds the {max_bytes} byte cap", reason="over byte cap"
                )
        return bytes(buffer)

    def get_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """GET and parse one JSON document from an API endpoint.

        Args:
            url: Endpoint URL.
            headers: Extra request headers (authorization).
            params: Query parameters.

        Returns:
            The parsed JSON value.

        Raises:
            AcquireError: When the endpoint errors or does not return JSON.
        """
        with self._open(url, headers=headers, params=params) as response:
            data = self._read_capped(response, MAX_JSON_BYTES)
        try:
            return json.loads(data)
        except ValueError as error:
            raise AcquireError(f"{url} did not return JSON: {error}", reason="bad json") from error

    def get_image(self, url: str) -> Payload:
        """GET one image, refusing anything that is not one.

        The content type is checked before the body is read, so a page of HTML
        behind an image-looking URL — directly or at the end of a redirect
        chain — costs nothing and is never staged.

        Args:
            url: Image URL.

        Returns:
            The fetched payload.

        Raises:
            AcquireError: When the response is not an image this pipeline can
                score, or exceeds the byte cap.
        """
        with self._open(url) as response:
            content_type = _content_type(response)
            if content_type not in CONTENT_TYPE_SUFFIXES:
                raise AcquireError(
                    f"{response.url} served {content_type or 'no content type'}, "
                    f"not an image format this pipeline can score",
                    reason="unsupported content type",
                )
            data = self._read_capped(response, MAX_IMAGE_BYTES)
        return Payload(data=data, content_type=content_type)

    def download(
        self,
        url: str,
        destination: Path,
        *,
        params: dict[str, Any] | None = None,
        max_bytes: int = MAX_ARCHIVE_BYTES,
    ) -> None:
        """Stream a large response (a dataset archive) to disk under a byte cap.

        Args:
            url: URL to download.
            destination: File the body is written to.
            params: Query parameters.
            max_bytes: Cap in bytes.

        Raises:
            AcquireError: When the response errors or exceeds the cap.
        """
        with self._open(url, params=params) as response:
            self._check_declared_size(response, max_bytes)
            written = 0
            with destination.open("wb") as handle:
                for chunk in response.iter_bytes():
                    written += len(chunk)
                    if written > max_bytes:
                        raise AcquireError(
                            f"{url} exceeds the {max_bytes} byte cap", reason="over byte cap"
                        )
                    handle.write(chunk)
        logger.info("downloaded %d bytes to %s", written, destination)


class Ledger:
    """The append-only record of every image this tool has ever acquired.

    The ledger is the index of record in two directions: an ``image_id`` already
    in it is never fetched again (that is what makes a run resumable), and it is
    the only place the provenance of the whole pile can be queried from. It is
    opened for append and never rewritten.
    """

    def __init__(self, path: Path) -> None:
        """Bind to a ledger path without creating it.

        Args:
            path: Path of the JSONL ledger.
        """
        self._path = path
        self._handle: TextIO | None = None

    @property
    def path(self) -> Path:
        """Filesystem path of the ledger."""
        return self._path

    def known_ids(self) -> set[str]:
        """Return every ``image_id`` already recorded.

        Malformed lines are warned about and ignored rather than repaired: the
        file is append-only, so a run must not touch what is already there.

        Returns:
            The set of known ids, empty when no ledger exists yet.
        """
        if not self._path.is_file():
            return set()
        known: set[str] = set()
        with self._path.open("r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    record = json.loads(text)
                except ValueError as error:
                    logger.warning("ledger line %d is not valid JSON (%s); ignoring", number, error)
                    continue
                image_id = record.get("image_id") if isinstance(record, dict) else None
                if isinstance(image_id, str) and image_id:
                    known.add(image_id)
        logger.debug("ledger %s knows %d image(s)", self._path, len(known))
        return known

    def append(self, record: dict[str, Any]) -> None:
        """Append one provenance record as a single JSON line.

        Flushed per record: a run interrupted halfway must leave every image it
        already staged accounted for.

        Args:
            record: The provenance record to write.
        """
        if self._handle is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self._path.open("a", encoding="utf-8")
        self._handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._handle.flush()

    def close(self) -> None:
        """Close the ledger handle if one was opened."""
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def _resolve_hint_card_id(
    store: ReferenceStore | None, number: str, total: int | None, set_code: str
) -> str:
    """Return the single printing consistent with a parsed number and set code.

    Args:
        store: Reference catalogue, or ``None`` when none is available.
        number: Normalized collector number, or empty.
        total: Set total parsed after the slash, if any.
        set_code: Set code the catalogue knows, or empty.

    Returns:
        A card id when exactly one printing matches, else an empty string. Never
        a guess between several: an ambiguous hint is worse than no hint.
    """
    if store is None or not number:
        return ""
    card_ids = store.find_card_ids_by_number(number, total)
    if set_code:
        cards = store.get_cards(card_ids)
        card_ids = [
            card_id
            for card_id in card_ids
            if card_id in cards and (cards[card_id].set_code or "") == set_code
        ]
    return card_ids[0] if len(card_ids) == 1 else ""


def parse_title_hints(text: str, store: ReferenceStore | None) -> TitleHints:
    """Read a printing hint out of source text.

    Three rules, cheapest and strongest first:

    1. Text that *is* a catalogue card id (``base1-4``) is taken as that
       printing. An operator who names their own files after the printing gets
       the strongest possible prior, and nothing else can match by accident.
    2. The collector number comes from
       :func:`app.signals.ocr.parse_collector_number`, reused verbatim, so
       "Base Set Charizard 4/102" yields number ``4`` exactly as the OCR signal
       would.
    3. The set code is matched by :data:`TITLE_SET_CODE_RE`, local to this
       module, because ``ocr.parse_set_code`` deliberately returns only the
       first uppercase token — correct for a card's bottom strip, useless for a
       post title beginning "MAIL DAY". Here every uppercase token is considered
       and the first one the catalogue knows as a set code wins.

    Args:
        text: Post title, filename, or other single-card text. Empty text yields
            empty hints, which is how sources without per-card text opt out.
        store: Reference catalogue used to validate a code and resolve a single
            printing, or ``None`` when no catalogue is available.

    Returns:
        The hints, with empty strings wherever nothing could be read.
    """
    if not text:
        return TitleHints(set_code="", number="", card_id="")
    card = store.get_card(text.strip()) if store is not None else None
    if card is not None:
        return TitleHints(set_code=card.set_code or "", number=card.number, card_id=card.card_id)
    number, total = parse_collector_number(text)
    known_codes = store.known_set_codes() if store is not None else frozenset()
    set_code = next(
        (token for token in TITLE_SET_CODE_RE.findall(text) if token in known_codes), ""
    )
    return TitleHints(
        set_code=set_code,
        number=number or "",
        card_id=_resolve_hint_card_id(store, number or "", total, set_code),
    )


def _read_local_image(path: Path, content_type: str) -> Payload:
    """Read one local file as a payload under the same byte cap as the network path.

    Args:
        path: File to read.
        content_type: Content type derived from the file's extension.

    Returns:
        The payload.

    Raises:
        AcquireError: When the file is larger than :data:`MAX_IMAGE_BYTES`.
    """
    size = path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise AcquireError(
            f"{path} is {size} bytes, over the {MAX_IMAGE_BYTES} byte cap", reason="over byte cap"
        )
    return Payload(data=path.read_bytes(), content_type=content_type)


def local_candidates(request: SourceRequest) -> Generator[Candidate]:
    """Yield every image file under a local directory.

    The source that needs no network and no credential: a directory of
    photographs the operator took themselves. Files are identified by their
    ``file://`` URL, so re-running over the same directory re-derives the same
    ids and the ledger skips everything already staged.

    The filename stem becomes both the ``title`` and the hint text, because it
    is the only text a local file carries and operators do name files
    "charizard base set 4-102". Files without an image extension are not
    candidates at all; a file *with* one whose bytes are not an image is a
    counted rejection.

    Args:
        request: Run options; ``path`` is walked and ``licence``/``attribution``
            are stamped on every record, since a directory states nothing itself.

    Yields:
        One candidate per image file, in sorted path order.

    Raises:
        SourceConfigError: When ``--path`` is missing or is not a directory.
    """
    root = request.path
    if root is None or not root.is_dir():
        raise SourceConfigError(
            f"--source local needs --path DIR pointing at a directory of your own photos "
            f"(got {root if root is not None else 'nothing'}); see {DOC_PATH}#local"
        )
    page_url = root.resolve().as_uri()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        content_type = SUFFIX_CONTENT_TYPES.get(path.suffix.lower())
        if content_type is None:
            logger.debug("skipping non-image file %s", path)
            continue
        yield Candidate(
            source_url=path.resolve().as_uri(),
            page_url=page_url,
            title=path.stem,
            hint_text=path.stem,
            licence=request.licence,
            attribution=request.attribution,
            fetch=partial(_read_local_image, path, content_type),
        )


def _read_zip_member(archive: zipfile.ZipFile, member: str, content_type: str) -> Payload:
    """Read one archive member as a payload.

    Args:
        archive: Open archive the member lives in.
        member: Member name.
        content_type: Content type derived from the member's extension.

    Returns:
        The payload.

    Raises:
        AcquireError: When the member is larger than :data:`MAX_IMAGE_BYTES`.
    """
    size = archive.getinfo(member).file_size
    if size > MAX_IMAGE_BYTES:
        raise AcquireError(
            f"{member} is {size} bytes, over the {MAX_IMAGE_BYTES} byte cap",
            reason="over byte cap",
        )
    return Payload(data=archive.read(member), content_type=content_type)


def _archive_candidates(
    archive_path: Path,
    *,
    page_url: str,
    url_prefix: str,
    licence: str,
    attribution: str,
) -> Generator[Candidate]:
    """Yield a candidate per image member of a downloaded archive.

    Member names are never used as output paths — a staged file is always
    ``<image_id>.<ext>`` — so a hostile archive cannot write outside staging.
    Hint text is empty: an archive member's path says nothing about which
    printing is in the frame.

    Args:
        archive_path: Local zip file.
        page_url: Human-facing page the dataset lives on.
        url_prefix: Prefix the member path is appended to, forming the stable
            per-image URL.
        licence: Licence the dataset states.
        attribution: Attribution the licence requires.

    Yields:
        One candidate per image member, in sorted member order. Payloads are
        read from the still-open archive, so candidates must be consumed as they
        are yielded (the driver does).

    Raises:
        AcquireError: When the download is not a zip archive at all.
    """
    if not zipfile.is_zipfile(archive_path):
        raise AcquireError(
            f"{page_url} did not return a zip archive (the download may be an HTML error page)",
            reason="bad archive",
        )
    with zipfile.ZipFile(archive_path) as archive:
        for member in sorted(archive.namelist()):
            content_type = SUFFIX_CONTENT_TYPES.get(Path(member).suffix.lower())
            if content_type is None:
                continue
            yield Candidate(
                source_url=f"{url_prefix}{member}",
                page_url=page_url,
                title=member,
                hint_text="",
                licence=licence,
                attribution=attribution,
                fetch=partial(_read_zip_member, archive, member, content_type),
            )


def _roboflow_slug(query: str) -> tuple[str, str, str]:
    """Split a Roboflow dataset slug into workspace, project, and version.

    Args:
        query: ``workspace/project`` or ``workspace/project/version``.

    Returns:
        ``(workspace, project, version)``; the version is empty when the slug
        omits it, in which case the caller resolves the latest one.

    Raises:
        SourceConfigError: When the slug names fewer than two parts or more
            than three.
    """
    parts = [part for part in query.split("/") if part]
    if len(parts) == 2:
        return parts[0], parts[1], ""
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    raise SourceConfigError(
        f"--source roboflow needs --query workspace/project[/version], e.g. --query "
        f"{ROBOFLOW_DEFAULT_SLUG}; got {query!r}. See {DOC_PATH}#roboflow"
    )


def _roboflow_get(fetcher: HttpFetcher, url: str, key: str) -> Any:
    """GET a Roboflow API document, translating an auth failure into advice.

    Public Universe datasets answer without a key; a private one, or a
    rate-limited anonymous caller, answers 401/403. That is the one case where
    ``ROBOFLOW_API_KEY`` is the fix, so it is named there rather than demanded
    up front.

    Args:
        fetcher: Shared HTTP client.
        url: API URL.
        key: API key, or empty for an anonymous call.

    Returns:
        The parsed JSON document.

    Raises:
        SourceConfigError: When the API refuses an anonymous or rejected caller.
        AcquireError: For any other transport or payload failure.
    """
    try:
        return fetcher.get_json(url, params={"api_key": key} if key else None)
    except AcquireError as error:
        if error.reason in ("http 401", "http 403"):
            raise SourceConfigError(
                f"roboflow refused the request ({error.reason}). Public datasets download "
                f"anonymously; set {ROBOFLOW_KEY_VAR} in the environment for private datasets or "
                f"when anonymous access is rate-limited. See {DOC_PATH}#roboflow"
            ) from error
        raise


def _roboflow_licence(metadata: Any) -> str:
    """Return the SPDX-ish licence a Roboflow project states.

    Args:
        metadata: Parsed ``/:workspace/:project`` response.

    Returns:
        An SPDX identifier when the stated licence has one, a ``LicenseRef-``
        form when it does not, or :data:`LICENCE_UNKNOWN` when none is stated.
    """
    project = metadata.get("project") if isinstance(metadata, dict) else None
    stated = str(project.get("license") or "").strip() if isinstance(project, dict) else ""
    if not stated:
        return LICENCE_UNKNOWN
    mapped = ROBOFLOW_LICENCES.get(stated.lower())
    if mapped:
        return mapped
    logger.warning("roboflow states licence %r, which has no SPDX identifier", stated)
    return f"LicenseRef-Roboflow-{re.sub(r'[^A-Za-z0-9.]+', '-', stated).strip('-')}"


def _roboflow_latest_version(metadata: Any, slug: str) -> str:
    """Return the newest generated version id of a Roboflow project.

    Args:
        metadata: Parsed ``/:workspace/:project`` response.
        slug: ``workspace/project``, for the error message.

    Returns:
        The version number as a string.

    Raises:
        SourceConfigError: When the project lists no generated version.
    """
    project = metadata.get("project") if isinstance(metadata, dict) else None
    versions = project.get("versions") if isinstance(project, dict) else None
    numbers: list[int] = []
    if isinstance(versions, list):
        for version in versions:
            entry = str(version.get("id", "")) if isinstance(version, dict) else str(version)
            tail = entry.rsplit("/", 1)[-1]
            if tail.isdigit():
                numbers.append(int(tail))
    if isinstance(versions, int):
        numbers = list(range(1, versions + 1))
    if not numbers:
        raise SourceConfigError(
            f"roboflow project {slug} lists no generated version; open its Universe page and "
            f"pass the version explicitly, e.g. --query {slug}/1. See {DOC_PATH}#roboflow"
        )
    return str(max(numbers))


def roboflow_candidates(request: SourceRequest) -> Generator[Candidate]:
    """Yield the images of a Roboflow Universe dataset export.

    Two API calls — the project metadata (for the stated licence and, when the
    slug omits it, the newest version) and the version export (for a download
    link) — then one archive download streamed to a temporary file. The archive
    is verified by magic bytes rather than by a content-type header, because
    signed storage links serve ``application/octet-stream``.

    Members are identified by the dataset's Universe page plus the member path,
    not by the signed download URL: that URL expires, and an id derived from it
    would re-download the whole dataset on every run.

    The default dataset carries detection boxes only, so no candidate gets hint
    text — these photos are material for detection and rectification robustness
    and for self-supervised domain adaptation, never printing labels.

    Args:
        request: Run options; ``query`` is ``workspace/project[/version]``.

    Yields:
        One candidate per image member.

    Raises:
        SourceConfigError: When the slug is malformed or the API refuses.
        AcquireError: When the export cannot be downloaded or is not an archive.
    """
    key = config.get(ROBOFLOW_KEY_VAR).strip()
    workspace, project, version = _roboflow_slug(request.query or ROBOFLOW_DEFAULT_SLUG)
    metadata = _roboflow_get(request.fetcher, f"{ROBOFLOW_API_URL}/{workspace}/{project}", key)
    licence = _roboflow_licence(metadata)
    if not version:
        version = _roboflow_latest_version(metadata, f"{workspace}/{project}")
        logger.info("roboflow: resolved latest version %s", version)
    export = _roboflow_get(
        request.fetcher,
        f"{ROBOFLOW_API_URL}/{workspace}/{project}/{version}/{ROBOFLOW_EXPORT_FORMAT}",
        key,
    )
    export_block = export.get("export") if isinstance(export, dict) else None
    link = str(export_block.get("link") or "") if isinstance(export_block, dict) else ""
    if not link:
        raise AcquireError(
            f"roboflow returned no export link for {workspace}/{project}/{version}; the version "
            f"may still be generating",
            reason="no export link",
        )
    page_url = f"{ROBOFLOW_UNIVERSE_URL}/{workspace}/{project}/dataset/{version}"
    credit = f"{workspace}/{project} v{version} (Roboflow Universe)"
    logger.info("roboflow: %s licensed %s", page_url, licence)
    with tempfile.TemporaryDirectory(prefix="pokeum-roboflow-") as tmp:
        archive_path = Path(tmp) / "export.zip"
        request.fetcher.download(link, archive_path)
        yield from _archive_candidates(
            archive_path,
            page_url=page_url,
            url_prefix=f"{page_url}#",
            licence=licence,
            attribution=attribution_for(credit, licence),
        )


def tcgar_candidates(request: SourceRequest) -> Generator[Candidate]:
    """Yield the images of the TCG-AR real evaluation set.

    51 board scenes captured for the TCG-AR paper. The repository ships the
    manual annotations but not the photographs: ``docs/DATA.md`` lists the real
    set as a Google Drive download, and ``scripts/download_assets.py`` holds the
    file id and the ``TCGAR_REALSET_URL`` override honoured here (a bare id or a
    ``/d/<id>/`` share URL, exactly as upstream parses it). No credential.

    Every frame holds up to twenty cards, so there is no per-image printing
    hint to give: the per-instance ids live upstream in
    ``assets/AI database/real/annotations_identity/<stem>.txt``, keyed by the
    member name kept in each record's ``title``.

    Args:
        request: Run options (none are required by this source).

    Yields:
        One candidate per image member.

    Raises:
        AcquireError: When the download is not the expected archive.
    """
    override = config.get(TCGAR_REALSET_URL_VAR).strip()
    match = _DRIVE_SHARE_ID_RE.search(override) if override else None
    file_id = match.group(1) if match else (override or TCGAR_REALSET_DRIVE_ID)
    logger.info("tcgar: fetching the real evaluation set (drive id %s)", file_id)
    with tempfile.TemporaryDirectory(prefix="pokeum-tcgar-") as tmp:
        archive_path = Path(tmp) / "real.zip"
        request.fetcher.download(
            DRIVE_DOWNLOAD_URL,
            archive_path,
            params={"id": file_id, "export": "download", "confirm": "t"},
        )
        yield from _archive_candidates(
            archive_path,
            page_url=TCGAR_REPO_URL,
            url_prefix=f"{TCGAR_REPO_URL}#{TCGAR_MEMBER_PREFIX}/",
            licence=TCGAR_LICENCE,
            attribution=attribution_for(f"TCG-AR ({TCGAR_CITATION})", TCGAR_LICENCE),
        )


def _reddit_gallery_urls(submission: Any) -> list[str]:
    """Return the stable image URLs of a Reddit gallery post.

    ``media_metadata`` also carries preview URLs, but those are signed and
    change between calls, which would break the ledger's identity. The media id
    plus its mime type rebuilds the permanent ``i.redd.it`` URL instead.

    Args:
        submission: A PRAW submission.

    Returns:
        One URL per gallery image, empty when the post has no usable metadata.
    """
    metadata = getattr(submission, "media_metadata", None)
    if not isinstance(metadata, dict):
        return []
    urls: list[str] = []
    for media_id, meta in metadata.items():
        if not isinstance(meta, dict) or meta.get("e") != "Image":
            continue
        suffix = CONTENT_TYPE_SUFFIXES.get(str(meta.get("m", "")).lower())
        if suffix is None:
            continue
        urls.append(f"{REDDIT_IMAGE_HOST}/{media_id}{suffix}")
    return urls


def _reddit_submission_candidates(submission: Any, fetcher: HttpFetcher) -> Generator[Candidate]:
    """Yield the image candidates of one Reddit submission.

    Only direct image posts and galleries are used. A link to an off-site page
    is skipped rather than followed: this tool acquires from sources whose terms
    it has read, not from wherever a post points.

    Args:
        submission: A PRAW submission.
        fetcher: Shared HTTP client used to fetch each image.

    Yields:
        One candidate per image the post holds.
    """
    title = str(getattr(submission, "title", "") or "")
    permalink = str(getattr(submission, "permalink", "") or "")
    page_url = f"https://www.reddit.com{permalink}" if permalink else ""
    author = getattr(submission, "author", None)
    subreddit = getattr(submission, "subreddit", None)
    credit = f"u/{author} via r/{subreddit}" if author else f"r/{subreddit}"
    urls = _reddit_gallery_urls(submission)
    if not urls:
        url = str(getattr(submission, "url", "") or "")
        if SUFFIX_CONTENT_TYPES.get(Path(httpx.URL(url).path).suffix.lower()) is not None:
            urls = [url]
    for url in urls:
        yield Candidate(
            source_url=url,
            page_url=page_url,
            title=title,
            hint_text=title,
            licence=REDDIT_LICENCE,
            attribution=credit,
            fetch=partial(fetcher.get_image, url),
        )


def reddit_candidates(request: SourceRequest) -> Generator[Candidate]:
    """Yield image posts from the Pokémon card subreddits.

    Listing goes through PRAW (the officially supported wrapper, which respects
    Reddit's rate limits); image bytes still come through :class:`HttpFetcher`,
    so the byte cap and the image-only gate apply to user content too. PRAW
    listings are lazy, so a small ``--limit`` costs one listing page.

    Post titles are the hint source — "Base Set Charizard 4/102" is a real hint
    and a common title — and the annotator decides what the image actually is.

    Args:
        request: Run options; ``query`` searches the subreddits when given,
            otherwise the newest posts are listed.

    Yields:
        One candidate per image in each post.

    Raises:
        SourceConfigError: When PRAW is not installed or credentials are unset.
    """
    try:
        import praw  # Dev-only dependency, imported here so no other source needs it.
    except ImportError as error:
        raise SourceConfigError(
            f"--source reddit needs praw: pip install praw (a dev-only dependency, deliberately "
            f"not in requirements.txt). See {DOC_PATH}#reddit"
        ) from error
    client_id = require_credential(REDDIT_CLIENT_ID_VAR, "reddit")
    client_secret = require_credential(REDDIT_CLIENT_SECRET_VAR, "reddit")
    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=USER_AGENT,
        check_for_async=False,
    )
    reddit.read_only = True
    subreddits = reddit.subreddit(REDDIT_SUBREDDITS)
    if request.query:
        logger.info("reddit: searching r/%s for %r", REDDIT_SUBREDDITS, request.query)
        listing = subreddits.search(request.query, limit=None)
    else:
        logger.info("reddit: listing new posts in r/%s", REDDIT_SUBREDDITS)
        listing = subreddits.new(limit=None)
    for submission in listing:
        yield from _reddit_submission_candidates(submission, request.fetcher)


SOURCES: dict[str, Source] = {
    "local": Source(
        name="local",
        summary="a directory of your own photographs (no network, no credential)",
        candidates=local_candidates,
        licence_position="whatever --licence/--attribution declare; 'unknown' when not declared",
        needs_path=True,
        hints=True,
    ),
    "roboflow": Source(
        name="roboflow",
        summary="a Roboflow Universe dataset export (5,058 real photos by default)",
        candidates=roboflow_candidates,
        licence_position="the licence the project states, e.g. CC-BY-4.0 (attribution required)",
        default_query=ROBOFLOW_DEFAULT_SLUG,
    ),
    "tcgar": Source(
        name="tcgar",
        summary="the TCG-AR academic real evaluation set (51 board scenes)",
        candidates=tcgar_candidates,
        licence_position=f"{TCGAR_LICENCE}; cite {TCGAR_CITATION}",
    ),
    "reddit": Source(
        name="reddit",
        summary=f"image posts from r/{REDDIT_SUBREDDITS}",
        candidates=reddit_candidates,
        licence_position=f"{REDDIT_LICENCE}; internal training/eval only, never republished",
        credentials=(REDDIT_CLIENT_ID_VAR, REDDIT_CLIENT_SECRET_VAR),
        hints=True,
    ),
}


def build_record(
    candidate: Candidate,
    payload: Payload,
    *,
    image_id: str,
    source: str,
    store: ReferenceStore | None,
) -> dict[str, Any]:
    """Validate one payload and build the provenance record for it.

    Validation is deliberately all in one place: whatever a plugin claims, an
    image only reaches staging if its content type is one the pipeline can
    score, its bytes actually decode, and it is big enough to be a card photo.

    Args:
        candidate: The candidate the payload came from.
        payload: Fetched bytes and content type.
        image_id: Stable id of the image.
        source: Plugin name.
        store: Reference catalogue used to resolve hints, or ``None``.

    Returns:
        The provenance record, written verbatim both as the sidecar JSON and as
        the ledger line.

    Raises:
        AcquireError: When the bytes are not a usable card photo.
    """
    if payload.content_type not in CONTENT_TYPE_SUFFIXES:
        raise AcquireError(
            f"{candidate.source_url} has content type {payload.content_type!r}, "
            f"not an image format this pipeline can score",
            reason="unsupported content type",
        )
    try:
        image = imaging.decode_bytes(payload.data)
    except ValueError as error:
        raise AcquireError(
            f"{candidate.source_url} is not a decodable image: {error}",
            reason="not a decodable image",
        ) from error
    height, width = image.shape[:2]
    if max(width, height) < MIN_IMAGE_SIDE:
        raise AcquireError(
            f"{candidate.source_url} is {width}x{height}, below the {MIN_IMAGE_SIDE}px minimum",
            reason="image too small",
        )
    hints = parse_title_hints(candidate.hint_text, store)
    return {
        "image_id": image_id,
        "source": source,
        "source_url": candidate.source_url,
        "page_url": candidate.page_url,
        "title": candidate.title,
        "licence": candidate.licence or LICENCE_UNKNOWN,
        "attribution": candidate.attribution,
        "fetched_at": datetime.now(UTC).isoformat(),
        "sha256": hashlib.sha256(payload.data).hexdigest(),
        "bytes": len(payload.data),
        "content_type": payload.content_type,
        "hint_card_id": hints.card_id,
        "hint_set_code": hints.set_code,
        "hint_number": hints.number,
    }


def acquire(
    candidates: Iterator[Candidate],
    *,
    source: str,
    out_dir: Path,
    ledger: Ledger,
    store: ReferenceStore | None,
    limit: int,
    dry_run: bool,
) -> AcquireSummary:
    """Fetch, validate, and stage every candidate a source offers.

    An image already named in the ledger is skipped before anything is fetched.
    Each accepted image is written as ``<image_id>.<ext>``, then its record as
    ``<image_id>.json``, then the ledger line — in that order, so an interrupted
    run leaves at worst an unrecorded file the next run re-fetches, never a
    ledger line for bytes that are not on disk.

    Args:
        candidates: Lazily enumerated candidates from a source plugin.
        source: Plugin name; also the staging subdirectory.
        out_dir: Photo root (``data/photos``).
        ledger: The append-only ledger.
        store: Reference catalogue used to resolve hints, or ``None``.
        limit: Stop after this many acquisitions; 0 means no limit.
        dry_run: Report what would be acquired, fetch and write nothing.

    Returns:
        The run's :class:`AcquireSummary`.
    """
    summary = AcquireSummary()
    known = ledger.known_ids()
    staging = out_dir / STAGING_DIRNAME / source
    for candidate in candidates:
        image_id = image_id_for(candidate.source_url)
        if image_id in known:
            logger.debug("%s already acquired; skipping %s", image_id, candidate.source_url)
            summary.duplicates += 1
            continue
        known.add(image_id)
        if dry_run:
            summary.acquired += 1
            print(f"would acquire {image_id} <- {candidate.source_url}")
        else:
            try:
                payload = candidate.fetch()
                record = build_record(
                    candidate, payload, image_id=image_id, source=source, store=store
                )
            except AcquireError as error:
                logger.warning("rejected %s", error)
                summary.rejected[error.reason] += 1
                continue
            suffix = CONTENT_TYPE_SUFFIXES[payload.content_type]
            staging.mkdir(parents=True, exist_ok=True)
            (staging / f"{image_id}{suffix}").write_bytes(payload.data)
            (staging / f"{image_id}.json").write_text(
                json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            ledger.append(record)
            summary.acquired += 1
            logger.debug("staged %s%s from %s", image_id, suffix, candidate.source_url)
            if summary.acquired % PROGRESS_EVERY == 0:
                logger.info("acquired %d image(s) so far", summary.acquired)
        if limit and summary.acquired >= limit:
            logger.info("reached --limit %d; re-run to continue where the ledger left off", limit)
            break
    return summary


def format_summary(
    summary: AcquireSummary, *, source: str, staging: Path, ledger: Path, dry_run: bool
) -> str:
    """Render an acquisition run's counts as the tool's stdout summary.

    Args:
        summary: Counts from :func:`acquire`.
        source: Plugin name the run used.
        staging: Directory the images were written to.
        ledger: Path of the ledger.
        dry_run: Whether the run wrote anything.

    Returns:
        A multi-line summary.
    """
    verb = "would acquire" if dry_run else "acquired"
    reasons = ", ".join(f"{reason}: {count}" for reason, count in sorted(summary.rejected.items()))
    lines = [
        f"{verb}: {summary.acquired} image(s) from {source} -> {staging}",
        f"skipped: {summary.duplicates} already in the ledger",
        f"rejected: {sum(summary.rejected.values())} ({reasons or 'none'})",
    ]
    if not dry_run:
        lines.append(f"ledger: {ledger}")
    return "\n".join(lines)


def describe_sources() -> str:
    """Render the source registry as help text.

    Returns:
        One block per registered source: what it is, what it needs, whether its
        text can yield a hint, and what its records will say about licensing.
    """
    blocks = ["sources (--source NAME):"]
    for source in SOURCES.values():
        needs = [f"env {var}" for var in source.credentials]
        if source.needs_path:
            needs.append("--path")
        # Continuation lines align under the summary column: two indent spaces
        # plus the 9-wide name field plus its separating space.
        pad = " " * 12
        lines = [
            f"  {source.name:<9} {source.summary}",
            f"{pad}needs: {', '.join(needs) or 'nothing'}",
        ]
        if source.default_query:
            lines.append(f"{pad}--query default: {source.default_query}")
        lines += [
            f"{pad}printing hints: {'parsed from source text' if source.hints else 'none'}",
            f"{pad}licence recorded: {source.licence_position}",
            f"{pad}docs: {DOC_PATH}#{source.name}",
        ]
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


def main() -> None:
    """Parse arguments and run one acquisition pass."""
    parser = argparse.ArgumentParser(
        description="Acquire candidate card photos into a provenance-tracked staging area",
        epilog=describe_sources(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source", default="local", choices=sorted(SOURCES), help="where photos come from"
    )
    parser.add_argument(
        "--query",
        default="",
        help="workspace/project[/version] (roboflow) or search text (reddit)",
    )
    parser.add_argument("--path", default=None, help="directory of images for --source local")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"max images to acquire this run, 0 for no limit (default: {DEFAULT_LIMIT})",
    )
    parser.add_argument("--out", default="data/photos", help="photo root (staging + ledger)")
    parser.add_argument(
        "--data-dir", default=None, help="reference data root, used only to resolve hints"
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=DEFAULT_RATE_LIMIT_SECONDS,
        help=f"minimum seconds between HTTP requests (default: {DEFAULT_RATE_LIMIT_SECONDS})",
    )
    parser.add_argument(
        "--licence",
        default=LICENCE_UNKNOWN,
        help="licence recorded for --source local images (e.g. own-work)",
    )
    parser.add_argument(
        "--attribution", default="", help="attribution recorded for --source local images"
    )
    parser.add_argument(
        "--list-sources", action="store_true", help="print the source registry and exit"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would be acquired, write nothing"
    )
    args = parser.parse_args()
    configure()

    if args.list_sources:
        print(describe_sources())
        return
    if args.limit < 0:
        parser.error("--limit cannot be negative (0 means no limit)")

    source = SOURCES[args.source]
    out_dir = Path(args.out)
    data_dir = args.data_dir or config.data_dir()
    with ExitStack() as stack:
        store: ReferenceStore | None = None
        if reference_db_path(data_dir).is_file():
            store = stack.enter_context(open_store(data_dir))
        else:
            # Hints are a convenience for the annotator, never a label, so a
            # missing catalogue degrades them instead of stopping the run.
            logger.warning(
                "no reference catalogue at %s; hint_card_id/hint_set_code will be empty",
                reference_db_path(data_dir),
            )
        fetcher = HttpFetcher(rate_limit=args.rate_limit)
        stack.callback(fetcher.close)
        ledger = Ledger(out_dir / LEDGER_FILENAME)
        stack.callback(ledger.close)
        request = SourceRequest(
            query=str(args.query),
            path=Path(args.path) if args.path else None,
            licence=str(args.licence) or LICENCE_UNKNOWN,
            attribution=str(args.attribution),
            fetcher=fetcher,
        )
        logger.info("acquiring from %s into %s", source.name, out_dir)
        try:
            with closing(source.candidates(request)) as candidates:
                summary = acquire(
                    candidates,
                    source=source.name,
                    out_dir=out_dir,
                    ledger=ledger,
                    store=store,
                    limit=int(args.limit),
                    dry_run=bool(args.dry_run),
                )
        except SourceConfigError as error:
            parser.error(str(error))

    print(
        format_summary(
            summary,
            source=source.name,
            staging=out_dir / STAGING_DIRNAME / source.name,
            ledger=ledger.path,
            dry_run=bool(args.dry_run),
        )
    )


if __name__ == "__main__":
    main()
