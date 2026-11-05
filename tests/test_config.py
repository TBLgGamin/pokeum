"""Unit tests for the settings accessors (app/core/config.py)."""

from __future__ import annotations

import pytest

from app.core import config


def test_get_returns_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOME_OPTIONAL_SETTING", raising=False)
    assert config.get("SOME_OPTIONAL_SETTING", "fallback") == "fallback"


def test_require_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOME_REQUIRED_SETTING", raising=False)
    with pytest.raises(config.MissingSettingError):
        config.require("SOME_REQUIRED_SETTING")


def test_require_raises_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_REQUIRED_SETTING", "")
    with pytest.raises(config.MissingSettingError):
        config.require("SOME_REQUIRED_SETTING")


def test_require_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_REQUIRED_SETTING", "value")
    assert config.require("SOME_REQUIRED_SETTING") == "value"


def test_log_json_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_JSON", "true")
    assert config.log_json() is True


def test_log_json_defaults_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_JSON", raising=False)
    assert config.log_json() is False


def test_data_dir_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATA_DIR", raising=False)
    assert config.data_dir() == "./data"


def test_tcgdex_base_url_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TCGDEX_BASE_URL", raising=False)
    assert config.tcgdex_base_url().startswith("https://")


def test_api_port_is_int(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_PORT", "9000")
    assert config.api_port() == 9000


def test_webcam_index_default_is_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEBCAM_INDEX", raising=False)
    assert config.webcam_index() == 0


def test_scan_debug_dir_defaults_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCAN_DEBUG_DIR", raising=False)
    assert config.scan_debug_dir() == ""
