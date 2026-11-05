"""FastAPI application factory.

The recognizer is expensive to build (it loads indexes and models), so it is
constructed once during the app's lifespan and stashed on ``app.state`` for every
request to share. Tests inject a ready-made recognizer to skip that cost.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.api.static import SPAStaticFiles
from app.core import config
from app.recognize.factory import build_recognizer
from app.recognize.pipeline import Recognizer

logger = logging.getLogger(__name__)

_API_VERSION = "2.0.0"


def create_app(recognizer: Recognizer | None = None) -> FastAPI:
    """Create the FastAPI application.

    Args:
        recognizer: A pre-built recognizer to serve (used in tests). When
            omitted, one is built from configured reference data on startup.

    Returns:
        The configured application.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.recognizer = recognizer or build_recognizer()
        # Recognition is CPU-bound: past a small cap, extra concurrency only
        # inflates every request's latency, so the cap sheds load instead.
        app.state.identify_semaphore = asyncio.Semaphore(config.identify_max_concurrency())
        logger.info("recognizer loaded into app state")
        yield

    app = FastAPI(title="pokeum card recognizer", version=_API_VERSION, lifespan=lifespan)
    # Open CORS: the service is a local, account-less recognizer, and the
    # frontend may be served from another origin (dev server, tunnel). Nothing
    # here is credentialed, so a wildcard is safe.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router, prefix="/api")

    # Single-origin serving: the built frontend at the root makes the deployed
    # service one hostname for both app and API — no cross-origin setup, and
    # the client's default "/api" base works unchanged. Absent dist dir (pure
    # API deployments, dev machines without a build) simply skips the mount.
    dist = config.frontend_dist_dir()
    if dist and Path(dist).is_dir():
        app.mount("/", SPAStaticFiles(directory=dist, html=True), name="spa")
        logger.info("serving frontend from %s", dist)
    return app
