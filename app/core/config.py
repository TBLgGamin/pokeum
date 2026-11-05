"""Centralised access to application settings.

All configuration comes from the process environment, so the same code runs
unchanged across machines and deployments. Keeping every ``os.environ`` lookup
in this one module means the rest of the codebase reads configuration through
small, named accessors instead of scattering magic strings around. Set values
through your shell, your deployment platform, or a local ``.env`` file (see
``.env.example``).
"""

from __future__ import annotations

import os


class MissingSettingError(RuntimeError):
    """Raised when a required application setting is missing or empty."""


def get(name: str, default: str = "") -> str:
    """Return an optional setting, falling back to ``default`` when unset."""
    return os.environ.get(name, default)


def require(name: str) -> str:
    """Return a mandatory setting.

    Fails fast and loud: a function that depends on a value must never run
    with a silently empty one.

    Args:
        name: Environment variable to read.

    Returns:
        The configured value.

    Raises:
        MissingSettingError: When the setting is unset or empty.
    """
    value = os.environ.get(name)
    if not value:
        raise MissingSettingError(f"Required setting '{name}' is not configured")
    return value


# --- Logging --------------------------------------------------------------
def log_level() -> str:
    """Logging level: ``debug`` shows everything our code does, ``info`` milestones."""
    return get("LOG_LEVEL", "info")


def log_json() -> bool:
    """Emit logs as one-line JSON records when ``LOG_JSON=true`` (else human text)."""
    return get("LOG_JSON", "").strip().lower() == "true"


# --- Reference data & artifacts -------------------------------------------
def data_dir() -> str:
    """Root directory for the reference store, image cache, and index files."""
    return get("DATA_DIR", "./data")


def tcgdex_base_url() -> str:
    """Base URL of the TCGdex REST API used to sync card and set data."""
    return get("TCGDEX_BASE_URL", "https://api.tcgdex.net/v2")


def card_language() -> str:
    """Language code (e.g. ``en``) the reference data and OCR are built for."""
    return get("CARD_LANGUAGE", "en")


def embed_model_path() -> str:
    """Filesystem path to the ONNX image encoder; empty/missing enables the fallback."""
    return get("EMBED_MODEL_PATH", "./data/models/dinov2s-ft-v1.onnx")


# --- API service ----------------------------------------------------------
def api_host() -> str:
    """Interface the FastAPI service binds to."""
    return get("API_HOST", "127.0.0.1")


def api_port() -> int:
    """TCP port the FastAPI service listens on."""
    return int(get("API_PORT", "8000"))


def identify_max_concurrency() -> int:
    """How many recognitions may run at once before requests are shed with 503."""
    return int(get("IDENTIFY_MAX_CONCURRENCY", "2"))


def frontend_dist_dir() -> str:
    """Directory of the built frontend served at the root; empty disables serving."""
    return get("FRONTEND_DIST_DIR", "./frontend/dist")


# --- Webcam ---------------------------------------------------------------
def webcam_index() -> int:
    """OpenCV capture-device index used by ``pokeum scan``."""
    return int(get("WEBCAM_INDEX", "0"))


# --- Scan diagnostics -------------------------------------------------------
def scan_debug_dir() -> str:
    """Directory where frames that failed card detection are saved; empty disables."""
    return get("SCAN_DEBUG_DIR", "")
