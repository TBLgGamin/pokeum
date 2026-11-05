"""Unit tests for the reference store (app/reference/store.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.reference.store import ReferenceStore, normalize_number


def _store(tmp_path: Path) -> ReferenceStore:
    store = ReferenceStore(tmp_path / "ref.db")
    store.upsert_set(
        set_id="sv02",
        name="Paldea Evolved",
        series="Scarlet & Violet",
        release_date="2023-06-09",
        card_count_total=193,
        card_count_official=193,
        set_code="PAL",
        symbol_url=None,
        synced_at="now",
    )
    return store


def test_normalize_number_strips_leading_zeros() -> None:
    assert normalize_number("025") == "25"
    assert normalize_number(" H1 ") == "H1"


def test_upsert_and_get_card_roundtrip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert_card(
        card_id="sv02-025",
        set_id="sv02",
        name="Pikachu",
        number="25",
        number_total=193,
        rarity="Common",
        release_year=2023,
        image_url="http://x/25/high.png",
        has_reverse=True,
        has_first_edition=False,
        has_holo=False,
        has_normal=True,
    )
    card = store.get_card("sv02-025")
    assert card is not None
    assert card.name == "Pikachu"
    assert card.set_name == "Paldea Evolved"
    assert card.set_code == "PAL"
    assert card.era == "modern"
    assert card.has_reverse is True
    assert card.image_url == "http://x/25/high.png"
    store.close()


def test_find_card_ids_by_number_normalizes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert_card(
        card_id="sv02-025",
        set_id="sv02",
        name="Pikachu",
        number="25",
        number_total=193,
        rarity=None,
        release_year=2023,
        image_url=None,
        has_reverse=False,
        has_first_edition=False,
        has_holo=False,
        has_normal=True,
    )
    assert store.find_card_ids_by_number("025", 193) == ["sv02-025"]
    assert store.find_card_ids_by_number("025", 999) == []
    store.close()


def test_wotc_era_from_release_year(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert_card(
        card_id="base-4",
        set_id="sv02",
        name="Charizard",
        number="4",
        number_total=102,
        rarity="Rare Holo",
        release_year=1999,
        image_url=None,
        has_reverse=False,
        has_first_edition=True,
        has_holo=True,
        has_normal=False,
    )
    card = store.get_card("base-4")
    assert card is not None
    assert card.era == "wotc"
    store.close()


def test_hashes_roundtrip_and_missing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert_card(
        card_id="sv02-1",
        set_id="sv02",
        name="Card",
        number="1",
        number_total=193,
        rarity=None,
        release_year=2023,
        image_url="http://x",
        has_reverse=False,
        has_first_edition=False,
        has_holo=False,
        has_normal=True,
    )
    store.set_image_path("sv02-1", str(tmp_path / "1.png"))
    assert store.card_ids_missing_hashes() == ["sv02-1"]
    store.set_hashes("sv02-1", {"phash": "ff00", "dhash": "00ff"})
    assert store.card_ids_missing_hashes() == []
    stored = dict(store.iter_hashes())
    assert stored["sv02-1"]["phash"] == "ff00"
    store.close()


def _insert_simple_card(store: ReferenceStore, card_id: str, number: str) -> None:
    store.upsert_card(
        card_id=card_id,
        set_id="sv02",
        name=card_id,
        number=number,
        number_total=193,
        rarity=None,
        release_year=2023,
        image_url=None,
        has_reverse=False,
        has_first_edition=False,
        has_holo=False,
        has_normal=True,
    )


def test_get_cards_resolves_known_and_skips_unknown(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _insert_simple_card(store, "sv02-1", "1")
    _insert_simple_card(store, "sv02-2", "2")
    resolved = store.get_cards(["sv02-1", "sv02-2", "nope", "sv02-1"])
    assert set(resolved) == {"sv02-1", "sv02-2"}
    assert resolved["sv02-2"].name == "sv02-2"
    store.close()


def test_get_cards_empty_input(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.get_cards([]) == {}
    store.close()


def test_get_cards_spans_chunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.reference import store as store_module

    monkeypatch.setattr(store_module, "_SQL_IN_CHUNK", 2)
    store = _store(tmp_path)
    ids = [f"sv02-{n}" for n in range(5)]
    for n, card_id in enumerate(ids):
        _insert_simple_card(store, card_id, str(n))
    resolved = store.get_cards(ids)
    assert set(resolved) == set(ids)
    store.close()


def test_image_paths_skips_uncached_and_unknown(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _insert_simple_card(store, "sv02-1", "1")
    _insert_simple_card(store, "sv02-2", "2")
    store.set_image_path("sv02-1", r"data\images\sv02\sv02-1.webp")

    # sv02-2 exists but was never downloaded; "nope" does not exist at all.
    resolved = store.image_paths(["sv02-1", "sv02-2", "nope"])

    assert resolved == {"sv02-1": str(Path("data/images/sv02/sv02-1.webp"))}
    assert store.image_paths([]) == {}
    store.close()


def test_known_set_codes_excludes_null(tmp_path: Path) -> None:
    store = _store(tmp_path)  # sv02 has set_code "PAL"
    store.upsert_set(
        set_id="nocode",
        name="Codeless Set",
        series=None,
        release_date=None,
        card_count_total=None,
        card_count_official=None,
        set_code=None,
        symbol_url=None,
        synced_at="now",
    )
    assert store.known_set_codes() == frozenset({"PAL"})
    store.close()


def test_meta_get_set(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.get_meta("missing") is None
    store.set_meta("embedder_id", "histogram-v1")
    assert store.get_meta("embedder_id") == "histogram-v1"
    store.close()


# A reference.db built on Windows records backslash-separated paths; the same
# file is shipped to a Linux server, where a backslash is not a separator.


def test_symbol_templates_normalize_windows_paths(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_symbol_path("sv02", r"data\symbols\sv02.webp")
    templates = store.symbol_templates()
    assert [path for _set_id, _era, path in templates] == [str(Path("data/symbols/sv02.webp"))]
    store.close()


def test_cards_with_images_normalize_windows_paths(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _insert_simple_card(store, "sv02-1", "1")
    store.set_image_path("sv02-1", r"data\images\sv02\sv02-1.webp")
    assert store.cards_with_images() == [("sv02-1", str(Path("data/images/sv02/sv02-1.webp")))]
    store.close()


def test_get_card_normalizes_image_path_and_keeps_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _insert_simple_card(store, "sv02-1", "1")
    card = store.get_card("sv02-1")
    assert card is not None and card.image_path is None

    store.set_image_path("sv02-1", r"data\images\sv02\sv02-1.webp")
    card = store.get_card("sv02-1")
    assert card is not None
    assert card.image_path == str(Path("data/images/sv02/sv02-1.webp"))
    store.close()


def test_posix_paths_are_unchanged(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_symbol_path("sv02", "data/symbols/sv02.webp")
    assert store.symbol_templates()[0][2] == str(Path("data/symbols/sv02.webp"))
    store.close()
