"""Unit tests for index build/load (app/reference/index.py)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from app.reference import index as index_module
from app.reference.store import ReferenceStore
from app.signals.embedding import HistogramEmbedder
from app.vision.imaging import load_image
from app.vision.rectify import center_crop_to_card


def _add_card(store: ReferenceStore, tmp_path: Path, card_id: str, seed: int) -> None:
    store.upsert_card(
        card_id=card_id,
        set_id="s1",
        name=card_id,
        number=card_id.split("-")[-1],
        number_total=10,
        rarity=None,
        release_year=2020,
        image_url="http://x",
        has_reverse=False,
        has_first_edition=False,
        has_holo=False,
        has_normal=True,
    )
    # Distinct textured images so both hashing and embedding can separate them
    # (solid colours produce degenerate, colliding perceptual hashes).
    rng = np.random.default_rng(seed)
    array = rng.integers(0, 256, size=(280, 200, 3), dtype=np.uint8)
    path = tmp_path / f"{card_id}.png"
    Image.fromarray(array).save(path)
    store.set_image_path(card_id, str(path))


def _seed(tmp_path: Path) -> ReferenceStore:
    store = ReferenceStore(tmp_path / "ref.db")
    store.upsert_set(
        set_id="s1",
        name="Set One",
        series="X",
        release_date="2020-01-01",
        card_count_total=3,
        card_count_official=3,
        set_code="ST1",
        symbol_url=None,
        synced_at="now",
    )
    _add_card(store, tmp_path, "s1-1", 1)
    _add_card(store, tmp_path, "s1-2", 2)
    return store


def test_build_index_hashes_and_embeds(tmp_path: Path) -> None:
    store = _seed(tmp_path)
    counts = index_module.build_index(store, HistogramEmbedder(), tmp_path)
    assert counts == {"hashed": 2, "embedded": 2}
    assert store.card_ids_missing_hashes() == []
    manifest = index_module.load_manifest(tmp_path)
    assert manifest is not None
    assert manifest["embedder_id"] == "histogram-v1"
    assert manifest["rows"] == 2


def test_loaded_index_retrieves_correct_card(tmp_path: Path) -> None:
    store = _seed(tmp_path)
    embedder = HistogramEmbedder()
    index_module.build_index(store, embedder, tmp_path)

    full_index, art_index = index_module.load_embedding_indexes(tmp_path, embedder.identifier)
    assert full_index is not None and art_index is not None
    card = center_crop_to_card(load_image(tmp_path / "s1-1.png"))
    assert full_index.query(embedder.embed(card), top_k=1)[0].card_id == "s1-1"

    hash_index = index_module.load_hash_index(store)
    assert hash_index.query(card, top_k=1)[0].card_id == "s1-1"


def test_incremental_build_only_adds_new_card(tmp_path: Path) -> None:
    store = _seed(tmp_path)
    index_module.build_index(store, HistogramEmbedder(), tmp_path)
    _add_card(store, tmp_path, "s1-3", 3)
    counts = index_module.build_index(store, HistogramEmbedder(), tmp_path)
    assert counts == {"hashed": 1, "embedded": 1}
    assert len(index_module.load_row_ids(tmp_path)) == 3


def test_index_refused_for_other_embedder(tmp_path: Path) -> None:
    store = _seed(tmp_path)
    index_module.build_index(store, HistogramEmbedder(), tmp_path)

    assert index_module.load_embedding_indexes(tmp_path, "onnx:other:000000000000") == (None, None)


def test_index_refused_when_row_ids_disagree_with_matrices(tmp_path: Path) -> None:
    store = _seed(tmp_path)
    embedder = HistogramEmbedder()
    index_module.build_index(store, embedder, tmp_path)

    row_ids_path = index_module.index_dir(tmp_path) / "row_ids.json"
    row_ids_path.write_text(json.dumps(["s1-1"]), encoding="utf-8")

    assert index_module.load_embedding_indexes(tmp_path, embedder.identifier) == (None, None)
