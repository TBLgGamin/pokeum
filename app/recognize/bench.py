"""In-process load benchmark for the recognition pipeline.

Answers the two questions the accuracy scorecard cannot: how long one
recognition takes on this machine, and what happens to throughput when several
run at once. It drives the same :class:`~app.recognize.pipeline.Recognizer` the
API serves, so its numbers are the ones that justify the ONNX thread counts and
the server's concurrency cap.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

from app.recognize.eval import latency_summary
from app.recognize.factory import build_recognizer
from app.recognize.pipeline import Recognizer
from app.vision.imaging import load_image

logger = logging.getLogger(__name__)


def run_bench(
    image: str | Path,
    *,
    data_dir: str | None = None,
    requests: int = 20,
    concurrency: int = 2,
) -> dict[str, Any]:
    """Recognize one image repeatedly and report latency and throughput.

    Args:
        image: Path to the image to recognize on every call.
        data_dir: Reference data root; defaults to the configured directory.
        requests: How many recognitions to run after the warm-up call.
        concurrency: How many recognitions may be in flight at once.

    Returns:
        A report dict with ``requests``, ``concurrency``, ``wall_s``,
        ``throughput_rps`` and a ``latency_ms`` summary.
    """
    recognizer = build_recognizer(data_dir=data_dir)
    frame = load_image(image)
    # Warm-up: first call pays lazy ONNX/OCR initialisation, which would
    # otherwise land entirely in the first measured sample.
    recognizer.identify(frame)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="bench") as pool:
        latencies = list(pool.map(lambda _: _timed_identify(recognizer, frame), range(requests)))
    wall = time.perf_counter() - started

    logger.info("bench done: %d request(s) at concurrency %d in %.2fs", requests, concurrency, wall)
    summary = latency_summary(latencies)
    return {
        "requests": requests,
        "concurrency": concurrency,
        "wall_s": round(wall, 3),
        "throughput_rps": round(requests / wall, 2) if wall > 0 else 0.0,
        "latency_ms": {"p50": summary["p50"], "p95": summary["p95"], "max": summary["max"]},
    }


def _timed_identify(recognizer: Recognizer, frame: np.ndarray) -> float:
    """Run one recognition and return its duration in milliseconds."""
    started = time.perf_counter()
    recognizer.identify(frame)
    return (time.perf_counter() - started) * 1000.0
