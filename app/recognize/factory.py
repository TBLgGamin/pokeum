"""Assemble a fully-wired :class:`~app.recognize.pipeline.Recognizer`.

This is the single place that reads configuration, opens the store, loads the
indexes, and constructs the heavy signal collaborators. The CLI and API both go
through here so they share one construction path; tests build recognizers
directly with fakes and never touch this module.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from app.core import config
from app.recognize.pipeline import Recognizer
from app.reference import index
from app.reference.store import ReferenceStore, reference_db_path
from app.signals.base import OcrEngine
from app.signals.embedding import load_embedder
from app.signals.symbol import SymbolMatcher

logger = logging.getLogger(__name__)


def open_store(data_dir: str | None = None) -> ReferenceStore:
    """Open the reference store under ``data_dir`` (or the configured default)."""
    root = data_dir or config.data_dir()
    return ReferenceStore(reference_db_path(root))


def build_recognizer(
    *,
    data_dir: str | None = None,
    store: ReferenceStore | None = None,
    with_ocr: bool = True,
    with_symbols: bool = True,
    warm: bool = True,
) -> Recognizer:
    """Build a recognizer from the reference data on disk.

    Args:
        data_dir: Data root; defaults to :func:`app.core.config.data_dir`.
        store: An already-open store to reuse; one is opened when omitted.
        with_ocr: Load the OCR engine (adds startup cost); disable to skip OCR.
        with_symbols: Load the set-symbol matcher.
        warm: Run one throwaway recognition so the first real request does not
            pay lazy model initialisation.

    Returns:
        A ready-to-use recognizer. Missing embedding indexes simply disable the
        embedding signals rather than failing.
    """
    root = data_dir or config.data_dir()
    store = store or open_store(root)

    embedder = load_embedder(config.embed_model_path())
    hash_index = index.load_hash_index(store)
    emb_full, emb_art = index.load_embedding_indexes(root, embedder.identifier)
    ocr_engine = _load_ocr() if with_ocr else None
    symbol_matcher = SymbolMatcher.from_store(store) if with_symbols else None

    logger.info(
        "recognizer ready: %d cards, %d hashes, embeddings=%s, ocr=%s, symbols=%d",
        store.count_cards(),
        len(hash_index),
        "yes" if emb_full is not None else "no",
        "yes" if ocr_engine is not None else "no",
        len(symbol_matcher) if symbol_matcher is not None else 0,
    )
    recognizer = Recognizer(
        store,
        hash_index=hash_index,
        embedder=embedder,
        emb_full_index=emb_full,
        emb_art_index=emb_art,
        ocr_engine=ocr_engine,
        symbol_matcher=symbol_matcher,
        # One long-lived pool per recognizer: OCR overlaps the dense signals
        # inside identify(), so two workers cover the two concurrent branches.
        executor=ThreadPoolExecutor(max_workers=2, thread_name_prefix="recognize"),
    )
    if warm:
        _warm_up(recognizer)
    return recognizer


def _warm_up(recognizer: Recognizer) -> None:
    """Run one throwaway recognition so the first real request is not the slowest.

    ONNX Runtime allocates its arenas and RapidOCR loads its sub-models on first
    inference. Paying that here moves multiple hundreds of milliseconds off the
    first user scan, and warms it on the recognizer's own executor threads.
    """
    started = time.perf_counter()
    # Mid-grey noise: no card quad, so the whole-frame path runs every signal.
    rng = np.random.default_rng(0)
    frame = rng.integers(96, 160, size=(546, 390, 3), dtype=np.uint8)
    try:
        recognizer.identify(frame)
    except Exception as error:  # noqa: BLE001 - warm-up must never block startup
        logger.warning("recognizer warm-up failed (%s); continuing cold", error)
        return
    logger.info("recognizer warmed in %.0f ms", (time.perf_counter() - started) * 1000)


def _load_ocr() -> OcrEngine | None:
    """Construct the RapidOCR engine, degrading to ``None`` if it is unavailable."""
    try:
        from app.signals.ocr import RapidOcrEngine

        return RapidOcrEngine()
    except Exception as error:  # noqa: BLE001 - OCR is optional; never block startup
        logger.warning("OCR engine unavailable (%s); continuing without OCR", error)
        return None
