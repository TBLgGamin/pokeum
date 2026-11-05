"""Build and load the retrieval index over the reference catalogue.

Two kinds of precomputed artifacts back recognition:

* perceptual hashes, stored per card in the SQLite catalogue;
* embedding matrices (full card and artwork crop), stored as ``.npy`` files
  alongside a ``row_ids.json`` that maps matrix rows back to card ids.

Building is incremental and idempotent: only cards missing hashes/embeddings are
processed, so syncing a new set and re-running ``build`` is fast. Swapping the
embedder (a different ``identifier``) invalidates the matrices and forces a full
rebuild, because embeddings from different encoders are not comparable.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from app.core import constants
from app.reference.store import ReferenceStore
from app.signals import hashes
from app.signals.base import Embedder
from app.signals.embedding import EmbeddingIndex, embed_images
from app.vision import regions
from app.vision.imaging import load_image
from app.vision.rectify import center_crop_to_card

logger = logging.getLogger(__name__)

# Bumped when the manifest's own key set changes; a mismatch disables embeddings
# rather than trusting a layout this code cannot interpret.
MANIFEST_VERSION = 1
_PROGRESS_EVERY = 200


def index_dir(data_dir: str | Path) -> Path:
    """Return the directory holding embedding matrices and row ids."""
    return Path(data_dir) / "index"


def _paths(data_dir: str | Path) -> tuple[Path, Path, Path]:
    """Return the ``(emb_full, emb_art, row_ids)`` artifact paths."""
    root = index_dir(data_dir)
    return root / "emb_full.npy", root / "emb_art.npy", root / "row_ids.json"


def manifest_path(data_dir: str | Path) -> Path:
    """Return the path of the index manifest describing the built artifacts."""
    return index_dir(data_dir) / "manifest.json"


def load_manifest(data_dir: str | Path) -> dict[str, Any] | None:
    """Return the parsed index manifest, or ``None`` when absent or unreadable.

    Args:
        data_dir: Root directory holding the ``index/`` artifacts.

    Returns:
        The manifest mapping, or ``None`` when the file is missing or not valid
        JSON.
    """
    path = manifest_path(data_dir)
    if not path.is_file():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logger.warning("index manifest %s unreadable: %s", path, error)
        return None
    if not isinstance(parsed, dict):
        logger.warning("index manifest %s is not an object", path)
        return None
    return parsed


def _atomic_save_npy(path: Path, array: np.ndarray) -> None:
    """Write a NumPy array to ``path`` atomically (temp file then replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    # Write through a file handle: np.save() would otherwise append ".npy" to a
    # path that does not already end in it, misplacing the temp file.
    with open(tmp, "wb") as handle:
        np.save(handle, array)
    os.replace(tmp, path)


def _atomic_write_text(path: Path, text: str) -> None:
    """Write text to ``path`` atomically (temp file then replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_row_ids(data_dir: str | Path) -> list[str]:
    """Return the card-id row order of the embedding matrices, or ``[]``."""
    _, _, row_ids_path = _paths(data_dir)
    if not row_ids_path.is_file():
        return []
    return list(json.loads(row_ids_path.read_text(encoding="utf-8")))


def build_index(
    store: ReferenceStore,
    embedder: Embedder,
    data_dir: str | Path,
    *,
    full: bool = False,
) -> dict[str, int]:
    """Compute missing hashes and embeddings and persist the index.

    Args:
        store: Catalogue to read cards from and write hashes to.
        embedder: Encoder used for the embedding matrices.
        data_dir: Root directory for index artifacts.
        full: Recompute everything, ignoring what already exists.

    Returns:
        Counts ``{"hashed", "embedded"}`` describing the work performed.
    """
    with_images = store.cards_with_images()
    manifest = load_manifest(data_dir)
    embedder_changed = manifest is None or manifest.get("embedder_id") != embedder.identifier
    rebuild_all = full or embedder_changed
    if embedder_changed and not full:
        logger.info("embedder changed to %s; rebuilding all embeddings", embedder.identifier)

    existing_ids = [] if rebuild_all else load_row_ids(data_dir)
    embedded_set = set(existing_ids)
    need_hash = {cid for cid, _ in with_images} if full else set(store.card_ids_missing_hashes())
    need_embed = [cid for cid, _ in with_images if cid not in embedded_set]
    need_embed_set = set(need_embed)

    new_full: dict[str, np.ndarray] = {}
    new_art: dict[str, np.ndarray] = {}
    # Full card and artwork crop travel together so one session run covers both.
    pending: list[tuple[str, np.ndarray, np.ndarray]] = []
    hashed = 0

    def flush() -> None:
        """Embed the pending crops in one batch and record their vectors."""
        if not pending:
            return
        images = [image for _, card, art in pending for image in (card, art)]
        vectors = embed_images(embedder, images)
        for position, (card_id, _, _) in enumerate(pending):
            new_full[card_id] = vectors[2 * position]
            new_art[card_id] = vectors[2 * position + 1]
        pending.clear()

    for processed, (card_id, image_path) in enumerate(with_images, start=1):
        do_hash = card_id in need_hash
        do_embed = card_id in need_embed_set
        if not (do_hash or do_embed):
            continue
        try:
            card = center_crop_to_card(load_image(image_path))
        except (FileNotFoundError, ValueError) as error:
            logger.warning("skipping %s: %s", card_id, error)
            continue
        if do_hash:
            store.set_hashes(card_id, hashes.compute_hashes(card))
            hashed += 1
        if do_embed:
            pending.append((card_id, card, regions.artwork(card)))
            if len(pending) >= constants.INDEX_EMBED_BATCH:
                flush()
        if processed % _PROGRESS_EVERY == 0:
            logger.info("indexed %d/%d cards", processed, len(with_images))
    flush()

    embedded = _persist_embeddings(data_dir, embedder, existing_ids, need_embed, new_full, new_art)
    logger.info("index build done: %d hashed, %d embedded", hashed, embedded)
    return {"hashed": hashed, "embedded": embedded}


def _persist_embeddings(
    data_dir: str | Path,
    embedder: Embedder,
    existing_ids: list[str],
    new_ids: list[str],
    new_full: dict[str, np.ndarray],
    new_art: dict[str, np.ndarray],
) -> int:
    """Merge new embedding rows with any existing matrices and save them."""
    added = [cid for cid in new_ids if cid in new_full]
    if not added and not existing_ids:
        return 0
    full_path, art_path, _ = _paths(data_dir)
    full_rows = [new_full[cid] for cid in added]
    art_rows = [new_art[cid] for cid in added]
    row_ids = list(existing_ids) + added

    full_matrix = _stack_with_existing(full_path, existing_ids, full_rows)
    art_matrix = _stack_with_existing(art_path, existing_ids, art_rows)

    _atomic_save_npy(full_path, full_matrix)
    _atomic_save_npy(art_path, art_matrix)
    _, _, row_ids_path = _paths(data_dir)
    _atomic_write_text(row_ids_path, json.dumps(row_ids))
    # Written last: the manifest is the "these artifacts are complete and belong
    # to this encoder" receipt, so a crash mid-write leaves it stale, not lying.
    _atomic_write_text(
        manifest_path(data_dir),
        json.dumps(
            {
                "manifest_version": MANIFEST_VERSION,
                "embedder_id": embedder.identifier,
                "dim": int(full_matrix.shape[1]),
                "rows": len(row_ids),
                "input_size": constants.EMBED_INPUT_SIZE,
                "artwork_box": list(constants.ARTWORK_BOX),
                "built_at": datetime.now(UTC).isoformat(),
            }
        ),
    )
    return len(added)


def _stack_with_existing(
    path: Path, existing_ids: list[str], new_rows: list[np.ndarray]
) -> np.ndarray:
    """Vertically stack an existing matrix (if any) with freshly computed rows."""
    parts: list[np.ndarray] = []
    if existing_ids and path.is_file():
        parts.append(np.load(path))
    if new_rows:
        parts.append(np.vstack(new_rows).astype(np.float32))
    if not parts:
        return np.zeros((0, 0), dtype=np.float32)
    return np.vstack(parts).astype(np.float32)


def load_embedding_indexes(
    data_dir: str | Path,
    embedder_id: str,
) -> tuple[EmbeddingIndex | None, EmbeddingIndex | None]:
    """Load the full-card and artwork embedding indexes, or ``(None, None)``.

    Args:
        data_dir: Root directory holding the ``index/`` artifacts.
        embedder_id: Identifier of the encoder that will query the index; rows
            built by any other encoder are not comparable and are refused.

    Returns:
        The full-card and artwork indexes, or ``(None, None)`` when the
        artifacts are missing or inconsistent with the manifest.
    """
    full_path, art_path, _ = _paths(data_dir)
    row_ids = load_row_ids(data_dir)
    if not row_ids or not full_path.is_file() or not art_path.is_file():
        return None, None
    full = np.load(full_path)
    art = np.load(art_path)
    reason = _manifest_mismatch(data_dir, embedder_id, row_ids, full, art)
    if reason is not None:
        logger.warning("embedding index unusable (%s); embedding signals disabled", reason)
        return None, None
    return EmbeddingIndex(row_ids, full), EmbeddingIndex(row_ids, art)


def _manifest_mismatch(
    data_dir: str | Path,
    embedder_id: str,
    row_ids: list[str],
    full: np.ndarray,
    art: np.ndarray,
) -> str | None:
    """Return why the on-disk index cannot be trusted, or ``None`` when it can."""
    manifest = load_manifest(data_dir)
    if manifest is None:
        return "manifest missing"
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        return f"manifest_version {manifest.get('manifest_version')!r} unsupported"
    if manifest.get("embedder_id") != embedder_id:
        return f"embedder_id mismatch {manifest.get('embedder_id')!r} != {embedder_id!r}"
    rows = manifest.get("rows")
    if not (rows == len(row_ids) == full.shape[0] == art.shape[0]):
        return (
            f"row count mismatch manifest={rows!r} ids={len(row_ids)} "
            f"matrices={full.shape[0]}/{art.shape[0]}"
        )
    dim = manifest.get("dim")
    if full.ndim != 2 or art.ndim != 2 or not (dim == full.shape[1] == art.shape[1]):
        return f"dim mismatch manifest={dim!r} matrices={full.shape[1:]}/{art.shape[1:]}"
    return None


def load_hash_index(store: ReferenceStore) -> hashes.HashIndex:
    """Load a :class:`~app.signals.hashes.HashIndex` from the store's hashes."""
    return hashes.HashIndex.from_store_rows(list(store.iter_hashes()))
