"""Decision-ladder tests for scripts/annotate_photos.py.

Offline by construction: a temporary catalogue, synthetic card photos, and a
fake OCR engine. No recognizer, no model, no network — which is also the point
of the tool under test: it labels from the printed number and the catalogue, not
from the encoder that will be trained on its output.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from app.recognize.eval import truth_from_name
from app.reference.store import ReferenceStore

# `scripts/` is a directory of runnable files, not a package, so the tool under
# test is loaded by path. It must be in sys.modules before it executes: its
# dataclasses resolve their own module while the class bodies run.
_MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "annotate_photos.py"
_SPEC = importlib.util.spec_from_file_location("annotate_photos", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
annotate = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = annotate
_SPEC.loader.exec_module(annotate)


class _FakeOcr:
    """OCR fake reading one fixed line, or nothing at all."""

    def __init__(self, text: str, confidence: float = 0.95) -> None:
        self._text = text
        self._confidence = confidence

    def read_text(self, image: np.ndarray) -> list[tuple[str, float]]:
        return [(self._text, self._confidence)] if self._text else []


def _catalogue(tmp_path: Path) -> ReferenceStore:
    store = ReferenceStore(tmp_path / "ref.db")
    for set_id, name, code in (("s1", "Set One", "STO"), ("s2", "Set Two", "STT")):
        store.upsert_set(
            set_id=set_id,
            name=name,
            series="X",
            release_date="2020-01-01",
            card_count_total=3,
            card_count_official=3,
            set_code=code,
            symbol_url=None,
            synced_at="now",
        )
    # "1/3" is printed only in Set One, so the number alone pins the printing;
    # "2/3" is printed in both sets, so it cannot.
    for set_id, number in (("s1", "1"), ("s1", "2"), ("s2", "2")):
        store.upsert_card(
            card_id=f"{set_id}-{number}",
            set_id=set_id,
            name=f"Card {number}",
            number=number,
            number_total=3,
            rarity=None,
            release_year=2020,
            image_url="http://x",
            has_reverse=False,
            has_first_edition=False,
            has_holo=False,
            has_normal=True,
        )
    return store


def _write_photo(path: Path, *, card: bool) -> None:
    """Write a card-shaped light rectangle on a dark ground: a detectable quad.

    With ``card=False`` the frame is flat, which yields no quad at all — the
    "there is nothing in this picture" case.
    """
    frame = np.full((800, 600, 3), 30, dtype=np.uint8)
    if card:
        frame[60:740, 60:540] = 225
        frame[690:735, 80:520] = 170  # a bottom strip, where the OCR fake "reads"
    Image.fromarray(frame).save(path)


def _stage(
    root: Path, image_id: str, *, source: str = "test", hint: str = "", card: bool = True
) -> Path:
    staging = root / "staging" / source
    staging.mkdir(parents=True, exist_ok=True)
    image = staging / f"{image_id}.jpg"
    _write_photo(image, card=card)
    record = {
        "image_id": image_id,
        "source": source,
        "source_url": f"https://example.invalid/{image_id}.jpg",
        "page_url": "https://example.invalid/listing",
        "title": "Card photo",
        "licence": "unknown",
        "attribution": "",
        "fetched_at": "2026-09-15T00:00:00+00:00",
        "sha256": "0" * 64,
        "bytes": image.stat().st_size,
        "content_type": "image/jpeg",
        "hint_card_id": hint,
        "hint_set_code": "",
        "hint_number": "",
    }
    (staging / f"{image_id}.json").write_text(json.dumps(record), encoding="utf-8")
    return image


def _annotate_one(
    tmp_path: Path,
    ocr: _FakeOcr,
    *,
    hint: str = "",
    image_id: str = "img-1",
    card: bool = True,
) -> tuple[Any, dict]:
    layout = annotate.Layout(tmp_path / "photos")
    _stage(layout.root, image_id, hint=hint, card=card)
    with _catalogue(tmp_path) as store:
        annotate.run_annotate(layout, store, ocr)
    records = list(annotate.iter_jsonl(layout.annotations))
    assert len(records) == 1
    return layout, records[0]


def _reviewed_pair(tmp_path: Path) -> tuple[Any, ReferenceStore]:
    """Stage two photos whose number is ambiguous, leaving both in review."""
    layout = annotate.Layout(tmp_path / "photos")
    _stage(layout.root, "img-a")
    _stage(layout.root, "img-b")
    store = _catalogue(tmp_path)
    annotate.run_annotate(layout, store, _FakeOcr("2/3"))
    return layout, store


def test_unique_printed_number_is_accepted(tmp_path: Path) -> None:
    _, record = _annotate_one(tmp_path, _FakeOcr("1/3"))
    assert record["decision"] == "accepted"
    assert record["card_id"] == "s1-1"
    assert record["agreement"] == "ocr_unique"
    assert record["ocr_number"] == "1"
    assert record["ocr_number_total"] == 3


def test_accepted_label_is_named_for_eval(tmp_path: Path) -> None:
    layout, _ = _annotate_one(tmp_path, _FakeOcr("1/3"), image_id="img-unique")
    written = sorted(layout.labelled.iterdir())
    assert [path.name for path in written] == ["s1-1__img-unique.jpg"]
    assert truth_from_name(written[0]) == "s1-1"


def test_ambiguous_number_plus_matching_hint_is_accepted(tmp_path: Path) -> None:
    _, record = _annotate_one(tmp_path, _FakeOcr("2/3"), hint="s2-2")
    assert record["decision"] == "accepted"
    assert record["card_id"] == "s2-2"
    assert record["agreement"] == "ocr_plus_hint"
    assert sorted(record["candidate_ids"]) == ["s1-2", "s2-2"]


def test_ambiguous_number_without_a_hint_goes_to_review(tmp_path: Path) -> None:
    layout, record = _annotate_one(tmp_path, _FakeOcr("2/3"), image_id="img-ambiguous")
    assert record["decision"] == "review"
    assert record["card_id"] == ""
    assert not layout.labelled.exists()
    rows = list(annotate.iter_jsonl(layout.review))
    assert len(rows) == 1
    assert rows[0]["image_id"] == "img-ambiguous"
    assert Path(rows[0]["image_path"]).is_file()
    assert [card["card_id"] for card in rows[0]["candidates"]] == ["s1-2", "s2-2"]


def test_hint_without_an_ocr_read_goes_to_review(tmp_path: Path) -> None:
    layout, record = _annotate_one(tmp_path, _FakeOcr(""), hint="s1-1")
    assert record["decision"] == "review"
    assert record["agreement"] == "hint_only"
    assert record["card_id"] == ""  # a source's text is never a label on its own
    assert record["hint_card_id"] == "s1-1"
    assert not layout.labelled.exists()


def test_hint_the_catalogue_does_not_know_is_rejected(tmp_path: Path) -> None:
    layout, record = _annotate_one(tmp_path, _FakeOcr(""), hint="swsh9-999")
    assert record["decision"] == "rejected"
    assert list(annotate.iter_jsonl(layout.review)) == []


def test_low_confidence_number_is_rejected(tmp_path: Path) -> None:
    layout, record = _annotate_one(tmp_path, _FakeOcr("1/3", confidence=0.2))
    assert record["decision"] == "rejected"
    assert "confidence" in record["reason"]
    assert not layout.labelled.exists()
    assert list(annotate.iter_jsonl(layout.review)) == []


def test_frame_with_no_card_and_no_read_is_rejected(tmp_path: Path) -> None:
    layout, record = _annotate_one(tmp_path, _FakeOcr(""), card=False)
    assert record["decision"] == "rejected"
    assert list(annotate.iter_jsonl(layout.review)) == []


def test_apply_decisions_labels_adjudicated_rows(tmp_path: Path) -> None:
    layout, store = _reviewed_pair(tmp_path)
    decisions = tmp_path / "decisions.jsonl"
    decisions.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in (
                {"image_id": "img-a", "card_id": "s1-2", "note": "Set One symbol"},
                {"image_id": "img-b", "card_id": ""},
                {"image_id": "img-gone", "card_id": "s1-1"},
            )
        ),
        encoding="utf-8",
    )
    with store:
        summary = annotate.run_apply(layout, store, decisions)

    assert summary.decisions["accepted"] == 1
    assert summary.decisions["rejected"] == 1
    assert summary.skipped["unknown image_id"] == 1
    assert [path.name for path in layout.labelled.iterdir()] == ["s1-2__img-a.jpg"]
    assert annotate.latest_decisions(layout.annotations) == {
        "img-a": "accepted",
        "img-b": "rejected",
    }
    applied = [
        record
        for record in annotate.iter_jsonl(layout.annotations)
        if record["image_id"] == "img-a"
    ][-1]
    assert applied["agreement"] == "adjudicated"
    assert applied["reason"] == "Set One symbol"


def test_applying_the_same_decisions_twice_labels_once(tmp_path: Path) -> None:
    layout, store = _reviewed_pair(tmp_path)
    decisions = tmp_path / "decisions.jsonl"
    decisions.write_text(json.dumps({"image_id": "img-a", "card_id": "s1-2"}) + "\n", "utf-8")
    with store:
        annotate.run_apply(layout, store, decisions)
        summary = annotate.run_apply(layout, store, decisions)

    assert summary.decisions == {}
    assert summary.skipped["not in review (accepted)"] == 1
    assert [path.name for path in layout.labelled.iterdir()] == ["s1-2__img-a.jpg"]


def test_apply_decisions_resolves_a_reported_read_instead_of_an_id(tmp_path: Path) -> None:
    layout, store = _reviewed_pair(tmp_path)
    decisions = tmp_path / "decisions.jsonl"
    # A vision reviewer reports what it read; the catalogue turns that into an
    # id, so the reviewer can never contribute an id that does not exist.
    decisions.write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in (
                {
                    "image_id": "img-a",
                    "number": "002",
                    "number_total": "3",
                    "set_text": "Set Two",
                    "note": "read off the strip",
                },
                {"image_id": "img-b", "number": "2", "number_total": "3"},
            )
        ),
        encoding="utf-8",
    )
    with store:
        summary = annotate.run_apply(layout, store, decisions)

    assert [path.name for path in layout.labelled.iterdir()] == ["s2-2__img-a.jpg"]
    assert summary.decisions["accepted"] == 1
    # The second row's read is ambiguous on its own, so it is skipped rather
    # than guessed: "2/3" exists in both sets.
    assert summary.skipped["read resolves to 2 printings"] == 1
