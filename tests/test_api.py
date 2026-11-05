"""Unit tests for the HTTP API (app/api) using a fake, index-free recognizer."""

from __future__ import annotations

import io
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api.server import create_app
from app.core import constants
from app.models import RecognitionResult, RecognitionStatus
from app.recognize.pipeline import Recognizer
from app.reference.store import ReferenceStore
from app.signals.hashes import HashIndex


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (20, 28), (200, 40, 40)).save(buffer, "PNG")
    return buffer.getvalue()


def _recognizer(tmp_path: Path) -> Recognizer:
    store = ReferenceStore(tmp_path / "ref.db")
    store.upsert_set(
        set_id="s1",
        name="Set One",
        series="X",
        release_date="2020-01-01",
        card_count_total=1,
        card_count_official=1,
        set_code="ST1",
        symbol_url=None,
        synced_at="now",
    )
    store.upsert_card(
        card_id="s1-1",
        set_id="s1",
        name="Alpha",
        number="1",
        number_total=1,
        rarity="Common",
        release_year=2020,
        image_url=None,
        has_reverse=False,
        has_first_edition=False,
        has_holo=False,
        has_normal=True,
    )
    store.upsert_card(
        card_id="s1-2",
        set_id="s1",
        name="Beta",
        number="2",
        number_total=2,
        rarity="Rare",
        release_year=2020,
        image_url="https://assets.tcgdex.net/en/x/s1/2/low.webp",
        has_reverse=False,
        has_first_edition=False,
        has_holo=False,
        has_normal=True,
    )
    return Recognizer(store, hash_index=HashIndex([], {}))


def test_health_reports_card_count(tmp_path: Path) -> None:
    with TestClient(create_app(_recognizer(tmp_path))) as client:
        body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["cards_indexed"] == 2
    assert body["embedder_loaded"] is False
    assert body["embedder_id"] == "none"


def test_identify_accepts_image_upload(tmp_path: Path) -> None:
    with TestClient(create_app(_recognizer(tmp_path))) as client:
        response = client.post(
            "/api/identify", files={"file": ("card.png", _png_bytes(), "image/png")}
        )
    assert response.status_code == 200
    assert response.json()["status"] == "no_match"  # empty index -> nothing to match


def test_identify_require_detection_reports_no_card(tmp_path: Path) -> None:
    with TestClient(create_app(_recognizer(tmp_path))) as client:
        response = client.post(
            "/api/identify?require_detection=true",
            files={"file": ("card.png", _png_bytes(), "image/png")},
        )
    # The solid-colour test image has no card quad: with the flag the API
    # reports that instead of guessing from the whole frame.
    assert response.status_code == 200
    assert response.json()["status"] == "no_card_detected"


def test_identify_accepts_guide_margin(tmp_path: Path) -> None:
    with TestClient(create_app(_recognizer(tmp_path))) as client:
        response = client.post(
            "/api/identify?guide_margin=0.15",
            files={"file": ("card.png", _png_bytes(), "image/png")},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "no_match"


def test_identify_detection_failure_dumps_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dump_dir = tmp_path / "debug"
    monkeypatch.setenv("SCAN_DEBUG_DIR", str(dump_dir))
    with TestClient(create_app(_recognizer(tmp_path))) as client:
        response = client.post(
            "/api/identify?require_detection=true",
            files={"file": ("card.png", _png_bytes(), "image/png")},
        )
    assert response.status_code == 200
    saved = list(dump_dir.glob("nocard-*.jpg"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == _png_bytes()


def test_identify_rejects_non_image(tmp_path: Path) -> None:
    with TestClient(create_app(_recognizer(tmp_path))) as client:
        response = client.post(
            "/api/identify", files={"file": ("x.txt", b"not an image", "text/plain")}
        )
    assert response.status_code == 400


def _frames(count: int) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("files", (f"f{i}.png", _png_bytes(), "image/png")) for i in range(count)]


def test_burst_without_a_card_in_any_frame_reports_no_card(tmp_path: Path) -> None:
    with TestClient(create_app(_recognizer(tmp_path))) as client:
        response = client.post("/api/identify/burst?require_detection=true", files=_frames(3))
    assert response.status_code == 200
    assert response.json()["status"] == "no_card_detected"


def test_burst_rejects_too_many_frames(tmp_path: Path) -> None:
    with TestClient(create_app(_recognizer(tmp_path))) as client:
        response = client.post("/api/identify/burst", files=_frames(constants.BURST_MAX_FRAMES + 1))
    assert response.status_code == 400


def test_card_image_missing_returns_404(tmp_path: Path) -> None:
    with TestClient(create_app(_recognizer(tmp_path))) as client:
        # Card exists but has neither cached image nor CDN URL; unknown card also 404s.
        assert client.get("/api/cards/s1-1/image").status_code == 404
        assert client.get("/api/cards/nope/image").status_code == 404


def test_card_image_redirects_to_cdn_when_local_missing(tmp_path: Path) -> None:
    with TestClient(create_app(_recognizer(tmp_path))) as client:
        response = client.get("/api/cards/s1-2/image", follow_redirects=False)
    # No local image cache, but the catalogue knows the CDN URL: redirect there.
    assert response.status_code == 307
    assert response.headers["location"] == "https://assets.tcgdex.net/en/x/s1/2/low.webp"
    assert "max-age" in response.headers["cache-control"]


def test_card_detail_includes_image_url(tmp_path: Path) -> None:
    with TestClient(create_app(_recognizer(tmp_path))) as client:
        with_url = client.get("/api/cards/s1-2").json()
        without_url = client.get("/api/cards/s1-1").json()
    assert with_url["image_url"] == "https://assets.tcgdex.net/en/x/s1/2/low.webp"
    assert without_url["image_url"] is None


def test_get_card_found_and_missing(tmp_path: Path) -> None:
    with TestClient(create_app(_recognizer(tmp_path))) as client:
        found = client.get("/api/cards/s1-1")
        missing = client.get("/api/cards/does-not-exist")
    assert found.status_code == 200
    assert found.json()["card_id"] == "s1-1"
    assert missing.status_code == 404


class _BlockingRecognizer(Recognizer):
    """Recognizer whose identify() parks until the test releases it."""

    def __init__(self, store: ReferenceStore) -> None:
        super().__init__(store, hash_index=HashIndex([], {}))
        self.entered = threading.Event()
        self.release = threading.Event()

    def identify(self, *args: object, **kwargs: object) -> RecognitionResult:
        self.entered.set()
        self.release.wait(timeout=10)
        return RecognitionResult(status=RecognitionStatus.NO_MATCH)


def test_identify_sheds_load_beyond_the_concurrency_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IDENTIFY_MAX_CONCURRENCY", "1")
    monkeypatch.setattr(constants, "IDENTIFY_QUEUE_TIMEOUT_SECONDS", 0.2)
    recognizer = _BlockingRecognizer(ReferenceStore(tmp_path / "ref.db"))
    with TestClient(create_app(recognizer)) as client:
        first: list[int] = []

        def occupy() -> None:
            first.append(
                client.post(
                    "/api/identify", files={"file": ("card.png", _png_bytes(), "image/png")}
                ).status_code
            )

        holder = threading.Thread(target=occupy)
        holder.start()
        assert recognizer.entered.wait(timeout=5)

        shed = client.post("/api/identify", files={"file": ("card.png", _png_bytes(), "image/png")})
        assert shed.status_code == 503
        assert shed.headers["Retry-After"] == "1"

        recognizer.release.set()
        holder.join(timeout=10)
    assert first == [200]
