"""HTTP routes for the recognizer service.

Thin handlers: decode the request, call the shared recognizer held in
``app.state``, and hand back the recognizer's own dictionary for FastAPI to
validate against the response models. All the real work lives in the pipeline.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse

from app.api.schemas import (
    CardDetailResponse,
    HealthResponse,
    IdentifyResponse,
)
from app.core import config, constants
from app.recognize.pipeline import Recognizer
from app.reference import images
from app.vision.imaging import cap_long_side, decode_bytes

logger = logging.getLogger(__name__)

router = APIRouter()


def _recognizer(request: Request) -> Recognizer:
    """Return the recognizer stored on the application state."""
    return request.app.state.recognizer


def _decode_and_identify(
    recognizer: Recognizer,
    payload: bytes,
    top_k: int,
    require_detection: bool,
    guide_margin: float | None,
) -> dict:
    """Decode upload bytes, cap their size, and run recognition.

    Runs inside a worker thread: decode and recognition are CPU-bound, and an
    ``async`` handler doing them inline would stall every concurrent request.

    Args:
        recognizer: The shared recognizer.
        payload: Raw uploaded image bytes.
        top_k: Maximum number of candidates to return.
        require_detection: Report ``no_card_detected`` instead of falling back
            to treating the whole frame as a card.
        guide_margin: Background added around a centred scanner guide on each side.

    Returns:
        The recognition result as a plain dictionary.

    Raises:
        ValueError: When the bytes cannot be decoded as an image.
    """
    image = cap_long_side(decode_bytes(payload), constants.INGEST_MAX_SIDE)
    result = recognizer.identify(
        image,
        top_k=top_k,
        require_detection=require_detection,
        guide_margin=guide_margin,
    )
    data = result.as_dict()
    if data["status"] == "no_card_detected":
        _dump_failed_frame(payload)
    return data


def _dump_failed_frame(payload: bytes) -> None:
    """Save a detection-failed frame to ``SCAN_DEBUG_DIR`` for inspection.

    Diagnostic aid: when detection keeps failing on real captures, the saved
    JPEGs show exactly what the camera sent. No-op unless the setting is set;
    never allowed to break the request.
    """
    debug_dir = config.scan_debug_dir()
    if not debug_dir:
        return
    try:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        path = Path(debug_dir) / f"nocard-{stamp}.jpg"
        images.save_bytes(path, payload)
        logger.debug("saved detection-failed frame to %s", path)
    except OSError as error:
        logger.warning("could not save debug frame: %s", error)


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Report service readiness and how many cards are indexed."""
    recognizer = _recognizer(request)
    return HealthResponse(
        status="ok",
        cards_indexed=recognizer.store.count_cards(),
        embedder_loaded=recognizer.has_embeddings,
        embedder_id=recognizer.embedder_id,
    )


@router.post("/identify", response_model=IdentifyResponse)
async def identify(
    request: Request,
    file: UploadFile,
    top_k: int = Query(constants.DEFAULT_TOP_K, ge=1, le=50),
    require_detection: bool = Query(False),
    guide_margin: float | None = Query(None, ge=0.0, le=0.5),
) -> dict:
    """Identify the card in an uploaded image.

    Args:
        request: The incoming request (carries the recognizer).
        file: The uploaded image file.
        top_k: Maximum number of candidates to return.
        require_detection: When true, answer ``no_card_detected`` if no card
            quad is found instead of assuming the whole frame is a card.
        guide_margin: Background added around a centred scanner guide on each
            side. Guided scans use the inner guide when quad detection fails.

    Returns:
        The recognition result as a dictionary matching :class:`IdentifyResponse`.

    Raises:
        HTTPException: 400 when the upload cannot be decoded as an image, or
            503 when no recognition slot frees up in time.
    """
    payload = await file.read()
    semaphore: asyncio.Semaphore = request.app.state.identify_semaphore
    try:
        await asyncio.wait_for(
            semaphore.acquire(), timeout=constants.IDENTIFY_QUEUE_TIMEOUT_SECONDS
        )
    except TimeoutError as error:
        logger.warning(
            "identify shed: no slot within %.1fs", constants.IDENTIFY_QUEUE_TIMEOUT_SECONDS
        )
        raise HTTPException(
            status_code=503, detail="recognizer busy", headers={"Retry-After": "1"}
        ) from error
    try:
        return await run_in_threadpool(
            _decode_and_identify,
            _recognizer(request),
            payload,
            top_k,
            require_detection,
            guide_margin,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        semaphore.release()


def _decode_and_identify_burst(
    recognizer: Recognizer,
    payloads: list[bytes],
    top_k: int,
    require_detection: bool,
    guide_margin: float | None,
) -> dict:
    """Decode several frames of one card and recognize them as a burst.

    Raises:
        ValueError: When any frame cannot be decoded as an image.
    """
    frames = [cap_long_side(decode_bytes(p), constants.INGEST_MAX_SIDE) for p in payloads]
    result = recognizer.identify_burst(
        frames,
        top_k=top_k,
        require_detection=require_detection,
        guide_margin=guide_margin,
    )
    return result.as_dict()


@router.post("/identify/burst", response_model=IdentifyResponse)
async def identify_burst(
    request: Request,
    files: list[UploadFile],
    top_k: int = Query(constants.DEFAULT_TOP_K, ge=1, le=50),
    require_detection: bool = Query(False),
    guide_margin: float | None = Query(None, ge=0.0, le=0.5),
) -> dict:
    """Identify one card from 1-``BURST_MAX_FRAMES`` frames of it.

    The scan UI calls this when a single frame was not decisive: frames are
    recognized in order, merged by mean confidence, and recognition stops as
    soon as the frames agree confidently. A burst holds one recognition slot
    for its whole run, so it is shed (503) under load like a single identify.

    Raises:
        HTTPException: 400 for a bad frame count or an undecodable frame, or
            503 when no recognition slot frees up in time.
    """
    if not 1 <= len(files) <= constants.BURST_MAX_FRAMES:
        raise HTTPException(
            status_code=400,
            detail=f"send between 1 and {constants.BURST_MAX_FRAMES} frames",
        )
    payloads = [await upload.read() for upload in files]
    semaphore: asyncio.Semaphore = request.app.state.identify_semaphore
    try:
        await asyncio.wait_for(
            semaphore.acquire(), timeout=constants.IDENTIFY_QUEUE_TIMEOUT_SECONDS
        )
    except TimeoutError as error:
        logger.warning("burst shed: no slot within %.1fs", constants.IDENTIFY_QUEUE_TIMEOUT_SECONDS)
        raise HTTPException(
            status_code=503, detail="recognizer busy", headers={"Retry-After": "1"}
        ) from error
    try:
        return await run_in_threadpool(
            _decode_and_identify_burst,
            _recognizer(request),
            payloads,
            top_k,
            require_detection,
            guide_margin,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        semaphore.release()


@router.get("/cards/{card_id}", response_model=CardDetailResponse)
def get_card(request: Request, card_id: str) -> dict:
    """Return catalogue detail for one card.

    Raises:
        HTTPException: 404 when the card id is unknown.
    """
    card = _recognizer(request).store.get_card(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"unknown card: {card_id}")
    return card.as_dict()


@router.get("/cards/{card_id}/image", response_model=None)
def get_card_image(request: Request, card_id: str) -> FileResponse | RedirectResponse:
    """Serve a small cached thumbnail of one card (used by the scan UI).

    The hi-res reference PNG is ~1 MB; the UI needs ~320px. A JPEG thumbnail is
    derived lazily on first request, cached on disk, and served with immutable
    cache headers so repeat views never refetch. Deployments that don't carry
    the reference image cache redirect to the TCGdex CDN instead, so the
    endpoint works without ``data/images/`` on disk.

    Raises:
        HTTPException: 404 when the card is unknown, or when it has neither a
            cached image nor a known CDN URL.
    """
    card = _recognizer(request).store.get_card(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"no image for card: {card_id}")
    if not card.image_path or not Path(card.image_path).is_file():
        if card.image_url:
            return RedirectResponse(
                card.image_url,
                status_code=307,
                headers={"Cache-Control": "public, max-age=86400"},
            )
        raise HTTPException(status_code=404, detail=f"no image for card: {card_id}")
    thumb = images.ensure_thumbnail(
        Path(card.image_path),
        images.thumbnail_path(config.data_dir(), card_id),
        constants.CARD_THUMB_WIDTH,
    )
    return FileResponse(
        thumb,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )
