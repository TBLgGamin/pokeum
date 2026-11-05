"""Annotate acquired real photos into exact-printing labels (dev-only tool).

``scripts/acquire_photos.py`` stages photographs it fetched from public sources::

    data/photos/staging/<source>/<image_id>.<ext>   # bytes exactly as fetched
    data/photos/staging/<source>/<image_id>.json    # provenance record
    data/photos/ledger.jsonl                        # append-only acquisition log

This tool is the second half: it decides, per image, *which printing* is in the
frame, and writes::

    data/photos/labelled/{card_id}__{image_id}.<ext>  # accepted labels
    data/photos/review.jsonl                          # rows needing adjudication
    data/photos/annotations.jsonl                     # every decision, with evidence

``{card_id}__...`` is the naming :func:`app.recognize.eval.truth_from_name`
reads, so ``python main.py eval data/photos/labelled`` scores the folder
unchanged.

**Independence rule.** The annotator never builds a recognizer and never opens
the embedding indexes. A label produced by the model that will then be trained
on it is a *pseudo-label*: it teaches the encoder its own mistakes, agreeing
with itself on every printing it already confuses, which is precisely the pairs
metric learning must separate (``docs/real-photo-data.md``, "What unlabelled
real photos *are* good for", point 3). The collector number printed on the card
plus the local catalogue is an *independent* labeller — its evidence is the
card's own printing, not the encoder's opinion — so its agreements and
disagreements with the model stay informative. It also keeps this tool cheap and
index-free: no ONNX session, no manifest, no rebuild, just detection, one OCR
read of the bottom strip, and two SQLite lookups per image.

Decision ladder (first rule that fires wins), recorded as ``agreement``:

1. A collector number read below ``PHOTO_LABEL_MIN_OCR_CONFIDENCE`` ->
   ``rejected``. A sub-threshold number is positive evidence of a bad crop or a
   blurred strip; labelling from the source's text instead would be trusting a
   listing over the card, which is the pseudo-label trap one step removed.
2. The read resolves to exactly one catalogue printing -> ``accepted``,
   ``ocr_unique``.
3. The read resolves to several printings and the acquisition ``hint_card_id``
   is one of them -> ``accepted``, ``ocr_plus_hint``. The card's own number
   still did the work; the source text only chose among printings it allows.
4. The read resolves to several printings and nothing settles it -> ``review``.
5. The source suggested a card id this catalogue does not know -> ``rejected``:
   its metadata is out of step with ours, so there is nothing to confirm.
6. No usable number but the source suggested a catalogue card -> ``review``,
   ``hint_only``. A hint alone is never a label: listing titles are wrong often
   enough that accepting them would poison the set silently.
7. A number that matches no catalogue card, or a card in the frame whose strip
   read nothing -> ``review``, ``none``. A person can still label the photo.
8. No card quad *and* nothing read -> ``rejected``. Nothing in the frame says a
   card is there, and nothing says which one; it is not worth a reviewer's time.

Usage::

    python scripts/annotate_photos.py --dry-run
    python scripts/annotate_photos.py --source ebay --limit 200
    python scripts/annotate_photos.py --validate-against data/eval/real
    python scripts/annotate_photos.py --apply-decisions data/photos/adjudicated.jsonl

``--validate-against`` is the measurement mode: it runs the same ladder over a
folder whose filenames already carry the truth and reports how good the labels
actually are, so label quality is proven rather than assumed. Such a folder has
no acquisition sidecars, so there are no hints — validation measures the
OCR-plus-catalogue path alone, the conservative half of the ladder.

Measured that way over the 28 eBay photos in ``data/eval/real``: 13 accepted
labels, all 13 correct — precision 1.000, recall 0.464 — with 11 sent to review
because their printed ``number/total`` exists in two sets and 4 rejected as
unreadable. Precision is the number the ladder exists to protect; recall is what
``hint_card_id`` and the review queue are there to lift.

``--apply-decisions`` closes the loop: a vision agent or a human reads
``review.jsonl``, writes back one JSONL row per image (``image_id``,
``card_id``, optional ``note``), and this mode turns those into labels.

Accepted images are copied through byte for byte, keeping the source extension:
the staged bytes are already whatever the source served, and a re-encode here
would only add a second generation of loss.

Labelling changes nothing about an image's rights position: the bytes are
copied, the source's own ``licence`` and ``attribution`` are echoed verbatim
into every review row, and ``data/photos/labelled`` inherits whatever terms the
staged pile carries (``docs/photo-acquisition.md``, "Licence and redistribution
position" — internal training and evaluation only; ``licence: "unknown"`` is a
restriction rather than a blank, and ``reddit-user-content`` is never
republished). An adjudicator therefore sees the terms before spending a
decision, and nothing here turns a restricted image into a publishable one.
"""

# ruff: noqa: E402 - the sys.path bootstrap has to run before the app imports.
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

# Dev scripts are run as files from a checkout, which puts `scripts/` on the
# path instead of the repository root; `app` is not an installed package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import config, constants
from app.core.logging_config import configure
from app.models import CardRef, OcrObservation
from app.recognize.eval import truth_from_name
from app.recognize.factory import open_store
from app.reference.store import ReferenceStore, reference_db_path
from app.signals.base import OcrEngine
from app.signals.ocr import read_card
from app.vision.detect import detect_card_quad
from app.vision.imaging import cap_long_side, load_image
from app.vision.rectify import rectify_or_whole

logger = logging.getLogger(__name__)

DEFAULT_PHOTOS_DIR = "data/photos"

STAGING_DIRNAME = "staging"
LABELLED_DIRNAME = "labelled"
REVIEW_NAME = "review.jsonl"
ANNOTATIONS_NAME = "annotations.jsonl"

# Mirrors the extensions app.recognize.eval scores, so nothing is labelled that
# `pokeum eval` would then ignore.
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".bmp")

ACCEPTED = "accepted"
REVIEW = "review"
REJECTED = "rejected"

AGREEMENT_OCR_UNIQUE = "ocr_unique"
AGREEMENT_OCR_PLUS_HINT = "ocr_plus_hint"
AGREEMENT_HINT_ONLY = "hint_only"
AGREEMENT_NONE = "none"
# Written only by --apply-decisions: the evidence was a reviewer, not the strip.
AGREEMENT_ADJUDICATED = "adjudicated"


@dataclass(frozen=True)
class Layout:
    """The staging and annotation paths under one photos root.

    Attributes:
        root: Photos root, ``data/photos`` by default.
    """

    root: Path

    @property
    def staging(self) -> Path:
        """Directory acquisition writes fetched images and sidecars into."""
        return self.root / STAGING_DIRNAME

    @property
    def labelled(self) -> Path:
        """Directory accepted labels are written to, in eval naming."""
        return self.root / LABELLED_DIRNAME

    @property
    def review(self) -> Path:
        """Queue of images awaiting an agent's or a human's decision."""
        return self.root / REVIEW_NAME

    @property
    def annotations(self) -> Path:
        """Append-only log of every decision this tool has ever made."""
        return self.root / ANNOTATIONS_NAME


@dataclass(frozen=True)
class StagedImage:
    """One acquired image and the provenance record staged beside it.

    Attributes:
        image_id: Stable id of the image; the shared stem of both staged files.
        source: Acquisition plugin that fetched it (the staging subdirectory).
        path: Staged image file.
        record: Parsed provenance sidecar.
    """

    image_id: str
    source: str
    path: Path
    record: dict[str, Any]

    def text(self, key: str) -> str:
        """Return one provenance field as text, ``""`` when absent or not a string."""
        value = self.record.get(key)
        return value if isinstance(value, str) else ""


@dataclass(frozen=True)
class StripRead:
    """What the vision front-end could extract from one photo.

    Attributes:
        detected: Whether a card quadrilateral was found in the frame.
        observation: Interpretation of the rectified bottom strip; empty when
            nothing was detected and therefore nothing was rectified.
    """

    detected: bool
    observation: OcrObservation


@dataclass(frozen=True)
class Decision:
    """One annotation verdict and the evidence it was reached on.

    ``card_id`` is only ever a label this tool asserts, so it stays empty on
    every non-accepted decision; a suggestion a reviewer should consider travels
    in ``hint_card_id`` and ``candidate_ids`` instead, where nothing downstream
    can mistake it for a label.

    Attributes:
        image_id: Image the decision is about.
        card_id: The asserted printing, or ``""``.
        decision: ``accepted``, ``review``, or ``rejected``.
        reason: Short human-readable justification.
        agreement: Which evidence agreed; see the module docstring's ladder.
        hint_card_id: Printing the acquisition source's text suggested.
        ocr_number: Collector number read off the strip, or ``""``.
        ocr_number_total: Set total read after the slash, or ``None``.
        ocr_set_code: Set code read off the strip, or ``""``.
        ocr_confidence: OCR confidence of the contributing line.
        candidate_ids: Catalogue ids the read is consistent with, capped at
            ``PHOTO_LABEL_MAX_CANDIDATES``.
    """

    image_id: str
    card_id: str
    decision: str
    reason: str
    agreement: str
    hint_card_id: str = ""
    ocr_number: str = ""
    ocr_number_total: int | None = None
    ocr_set_code: str = ""
    ocr_confidence: float = 0.0
    candidate_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return the record written to ``annotations.jsonl``."""
        return {
            "image_id": self.image_id,
            "card_id": self.card_id,
            "decision": self.decision,
            "reason": self.reason,
            "ocr_number": self.ocr_number,
            "ocr_number_total": self.ocr_number_total,
            "ocr_set_code": self.ocr_set_code,
            "ocr_confidence": round(self.ocr_confidence, 3),
            "candidate_ids": list(self.candidate_ids),
            "hint_card_id": self.hint_card_id,
            "agreement": self.agreement,
        }


@dataclass
class RunSummary:
    """What an annotate or apply run decided, and what it refused and why.

    Attributes:
        decisions: Verdicts by name.
        skipped: Images never decided, counted by reason.
    """

    decisions: Counter[str] = field(default_factory=Counter)
    skipped: Counter[str] = field(default_factory=Counter)


@dataclass
class ValidationReport:
    """Label quality measured against filenames that already carry the truth.

    Attributes:
        total: Images with a parseable truth label.
        accepted: Images the ladder labelled.
        correct: Accepted labels equal to the filename truth.
        decisions: Verdicts by name.
        confusions: ``(filename, truth, labelled)`` for every wrong label.
        skipped: Files not measured, counted by reason.
    """

    total: int = 0
    accepted: int = 0
    correct: int = 0
    decisions: Counter[str] = field(default_factory=Counter)
    confusions: list[tuple[str, str, str]] = field(default_factory=list)
    skipped: Counter[str] = field(default_factory=Counter)

    @property
    def precision(self) -> float:
        """Share of accepted labels that match the truth: how wrong a label can be."""
        return self.correct / self.accepted if self.accepted else 0.0

    @property
    def recall(self) -> float:
        """Share of images that got a label at all: how much of a pile is usable."""
        return self.accepted / self.total if self.total else 0.0


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Yield every JSON object in a JSONL file, skipping lines that are not one.

    Args:
        path: File to read; a missing file yields nothing.

    Yields:
        One parsed object per usable line.
    """
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError as error:
                logger.warning("%s line %d is not JSON (%s); skipping", path, number, error)
                continue
            if isinstance(record, dict):
                yield record
            else:
                logger.warning("%s line %d is not a JSON object; skipping", path, number)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append one JSON object as a line to an append-only log.

    Opened per record rather than kept open for the run: an OCR read costs
    orders of magnitude more than the file handle, and a killed run then leaves
    every decision it already made on disk.

    Args:
        path: Log file; its parent is created on demand.
        record: Object to append.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def latest_decisions(path: Path) -> dict[str, str]:
    """Return each image's most recent verdict from the append-only log.

    A later row supersedes an earlier one for the same image. This is what makes
    a re-run idempotent (already-decided images are skipped) and what defines
    "in review state" for :func:`run_apply`; delete the log to re-annotate a
    whole staging pile, for instance after a catalogue sync.

    Args:
        path: ``annotations.jsonl``.

    Returns:
        Mapping of image id to its latest decision.
    """
    latest: dict[str, str] = {}
    for record in iter_jsonl(path):
        image_id = record.get("image_id")
        decision = record.get("decision")
        if isinstance(image_id, str) and isinstance(decision, str):
            latest[image_id] = decision
    return latest


def iter_staged(layout: Layout, *, source: str = "") -> Iterator[StagedImage]:
    """Yield every staged image that has a readable provenance record.

    The per-image sidecars are the input, not ``ledger.jsonl``: the ledger is
    acquisition's audit trail, and reading the sidecars lets annotation run
    against a staging tree that is still being filled.

    Args:
        layout: Photos root.
        source: When set, only this staging source is walked.

    Yields:
        Staged images in a stable (source, image id) order.
    """
    if not layout.staging.is_dir():
        logger.warning("no staging directory at %s", layout.staging)
        return
    for source_dir in sorted(path for path in layout.staging.iterdir() if path.is_dir()):
        if source and source_dir.name != source:
            continue
        for sidecar in sorted(source_dir.glob("*.json")):
            image = _image_beside(sidecar)
            if image is None:
                logger.warning("staged record %s has no image beside it", sidecar)
                continue
            record = _load_record(sidecar)
            if record is None:
                continue
            declared = record.get("image_id")
            if isinstance(declared, str) and declared and declared != sidecar.stem:
                # The filename is the addressable id (labels and adjudication
                # round-trip through it), so it wins; the mismatch is a bug in
                # whatever wrote the pair and is worth saying out loud.
                logger.warning(
                    "staged record %s declares image_id %r; using the filename %r",
                    sidecar,
                    declared,
                    sidecar.stem,
                )
            yield StagedImage(
                image_id=sidecar.stem,
                source=source_dir.name,
                path=image,
                record=record,
            )


def _image_beside(sidecar: Path) -> Path | None:
    """Return the staged image sharing a sidecar's stem, or ``None``."""
    for suffix in IMAGE_SUFFIXES:
        candidate = sidecar.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    return None


def _load_record(sidecar: Path) -> dict[str, Any] | None:
    """Parse a provenance sidecar, returning ``None`` when it is not a JSON object."""
    try:
        parsed = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        logger.warning("cannot read staged record %s (%s)", sidecar, error)
        return None
    if not isinstance(parsed, dict):
        logger.warning("staged record %s is not a JSON object", sidecar)
        return None
    return parsed


def read_strip(path: Path, engine: OcrEngine) -> StripRead:
    """Detect, rectify, and OCR the bottom strip of one photo.

    Args:
        path: Image file to read.
        engine: OCR engine run over the rectified strip.

    Returns:
        Whether a card quad was found, and the interpreted observation.

    Raises:
        FileNotFoundError: When the file cannot be decoded as an image.
    """
    image = cap_long_side(load_image(path), constants.INGEST_MAX_SIDE)
    quad = detect_card_quad(image)
    # Marketplace photos are very often already cropped to the card, so the
    # outline runs along the frame edge and contour detection finds no quad.
    # `rectify_or_whole` centre-crops those to the card aspect, which puts the
    # strip back where OCR expects it; measured on data/eval/real, this is the
    # difference between labelling most of a scraped pile and none of it. The
    # crop is a guess, but it cannot invent a label: the number it yields still
    # has to clear the confidence bar and resolve in the catalogue.
    card = rectify_or_whole(image, quad)
    return StripRead(detected=quad is not None, observation=read_card(engine, card))


def resolve_candidates(
    store: ReferenceStore, observation: OcrObservation, known_codes: frozenset[str]
) -> list[str]:
    """Return the catalogue printings one strip read is consistent with.

    The collector number is the primary key: ``number/total`` already separates
    most reprints across sets. A set code is applied only when the catalogue
    actually prints it — random uppercase scene text passes the set-code regex —
    and when it is applied it is applied strictly: a code matching none of the
    number's cards is an internally inconsistent read, and the empty result that
    yields is what stops it from labelling anything.

    Args:
        store: Reference catalogue.
        observation: Interpreted bottom-strip read.
        known_codes: Set codes the catalogue prints (``known_set_codes``).

    Returns:
        Matching card ids, sorted; empty when the read pins nothing.
    """
    if not observation.number:
        return []
    ids = sorted(store.find_card_ids_by_number(observation.number, observation.number_total))
    code = observation.set_code
    if not code or code not in known_codes:
        return ids
    cards = store.get_cards(ids)
    return [cid for cid in ids if (card := cards.get(cid)) is not None and card.set_code == code]


def is_catalogue_id(store: ReferenceStore, card_id: str) -> bool:
    """Report whether a card id supplied from outside names a card we know.

    Applied to acquisition hints and to adjudicated decisions alike. ``__`` is
    refused outright: the labelled filename splits on it, so such an id could
    never round-trip through :func:`app.recognize.eval.truth_from_name`, and it
    is also where path trickery in a scraped id would arrive.

    Args:
        store: Reference catalogue.
        card_id: Externally supplied id.

    Returns:
        ``True`` when the id is usable as a label.
    """
    if not card_id or "__" in card_id:
        return False
    return store.get_card(card_id) is not None


def decide(
    *,
    image_id: str,
    read: StripRead,
    candidate_ids: Sequence[str],
    hint_card_id: str,
    hint_known: bool,
) -> Decision:
    """Apply the decision ladder to one photo's evidence.

    Pure: every catalogue lookup has already happened, so the rules are directly
    unit-testable. The ladder itself is documented in the module docstring.

    Args:
        image_id: Image being decided.
        read: Detection flag and strip observation.
        candidate_ids: Every catalogue id the read is consistent with, in full
            (the recorded list is capped, the decision is not).
        hint_card_id: Printing the acquisition source's text suggested.
        hint_known: Whether that hint names a card in the catalogue.

    Returns:
        The verdict, carrying the evidence it was reached on.
    """
    observation = read.observation
    base = Decision(
        image_id=image_id,
        card_id="",
        decision=REJECTED,
        reason="",
        agreement=AGREEMENT_NONE,
        hint_card_id=hint_card_id,
        ocr_number=observation.number or "",
        ocr_number_total=observation.number_total,
        ocr_set_code=observation.set_code or "",
        ocr_confidence=observation.confidence,
        candidate_ids=tuple(candidate_ids[: constants.PHOTO_LABEL_MAX_CANDIDATES]),
    )
    if observation.number and observation.confidence < constants.PHOTO_LABEL_MIN_OCR_CONFIDENCE:
        return replace(
            base,
            decision=REJECTED,
            reason=(
                f"ocr confidence {observation.confidence:.2f} below "
                f"{constants.PHOTO_LABEL_MIN_OCR_CONFIDENCE:.2f}"
            ),
        )
    printed = _printed_number(observation)
    if len(candidate_ids) == 1:
        return replace(
            base,
            card_id=candidate_ids[0],
            decision=ACCEPTED,
            agreement=AGREEMENT_OCR_UNIQUE,
            reason=f"printed {printed} resolves to exactly one catalogue printing",
        )
    if len(candidate_ids) > 1:
        if hint_known and hint_card_id in candidate_ids:
            return replace(
                base,
                card_id=hint_card_id,
                decision=ACCEPTED,
                agreement=AGREEMENT_OCR_PLUS_HINT,
                reason=(
                    f"printed {printed} allows {len(candidate_ids)} printings; "
                    f"the source hint picks one of them"
                ),
            )
        return replace(
            base,
            decision=REVIEW,
            reason=(
                f"printed {printed} allows {len(candidate_ids)} printings and nothing settles it"
            ),
        )
    if hint_card_id and not hint_known:
        return replace(
            base,
            decision=REJECTED,
            reason=f"source hint {hint_card_id!r} is not a catalogue card id",
        )
    if hint_known:
        return replace(
            base,
            decision=REVIEW,
            agreement=AGREEMENT_HINT_ONLY,
            reason=f"source hint {hint_card_id} has no support from the printed strip",
        )
    if observation.number:
        return replace(base, decision=REVIEW, reason=f"printed {printed} matches no catalogue card")
    if read.detected:
        return replace(
            base,
            decision=REVIEW,
            reason="a card is in the frame but its strip read nothing and no hint was given",
        )
    return replace(
        base,
        decision=REJECTED,
        reason="no card detected in the frame and nothing read from it",
    )


def _printed_number(observation: OcrObservation) -> str:
    """Render a read collector number the way it is printed, for reasons and logs."""
    if not observation.number:
        return "?"
    if observation.number_total:
        return f"{observation.number}/{observation.number_total}"
    return observation.number


def annotate_image(
    staged: StagedImage, store: ReferenceStore, engine: OcrEngine, known_codes: frozenset[str]
) -> Decision:
    """Read one staged photo and decide what, if anything, it is labelled.

    Args:
        staged: Image and its provenance record.
        store: Reference catalogue.
        engine: OCR engine.
        known_codes: Set codes the catalogue prints.

    Returns:
        The verdict for this image.
    """
    read = read_strip(staged.path, engine)
    candidates = resolve_candidates(store, read.observation, known_codes)
    hint = staged.text("hint_card_id")
    return decide(
        image_id=staged.image_id,
        read=read,
        candidate_ids=candidates,
        hint_card_id=hint,
        hint_known=is_catalogue_id(store, hint),
    )


def label_path(layout: Layout, card_id: str, staged: StagedImage) -> Path:
    """Return where an accepted label is written, in eval-compatible naming."""
    return layout.labelled / f"{card_id}__{staged.image_id}{staged.path.suffix.lower()}"


def write_label(layout: Layout, card_id: str, staged: StagedImage) -> Path:
    """Copy a staged image to the labelled set under its asserted card id.

    Bytes are copied through untouched: they are already whatever the source
    served, and a re-encode here would add a second generation of loss for no
    gain.

    Args:
        layout: Photos root.
        card_id: Asserted printing.
        staged: Image to label.

    Returns:
        The written path.
    """
    path = label_path(layout, card_id, staged)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(staged.path.read_bytes())
    logger.debug("wrote %s", path)
    return path


def _card_summary(card: CardRef) -> dict[str, Any]:
    """Return the fields a reviewer needs to recognize a printing by eye."""
    return {
        "card_id": card.card_id,
        "name": card.name,
        "set_code": card.set_code,
        "set_name": card.set_name,
        "number": card.display_number,
    }


def review_record(
    decision: Decision, staged: StagedImage, cards: dict[str, CardRef]
) -> dict[str, Any]:
    """Build a review-queue row: the decision plus everything needed to settle it.

    A reviewer — an agent or a person — has to reach a verdict from this single
    object, so it carries the staged image path, where the image came from, and
    the candidate printings spelled out instead of as bare ids.

    Args:
        decision: The verdict that asked for review.
        staged: Image and its provenance record.
        cards: Catalogue entries for the decision's candidate ids.

    Returns:
        The object appended to ``review.jsonl``.
    """
    hint = cards.get(decision.hint_card_id)
    record = decision.as_dict()
    record.update(
        {
            "image_path": str(staged.path),
            "source": staged.source,
            "source_url": staged.text("source_url"),
            "page_url": staged.text("page_url"),
            "title": staged.text("title"),
            "licence": staged.text("licence"),
            "attribution": staged.text("attribution"),
            "candidates": [
                _card_summary(cards[cid]) for cid in decision.candidate_ids if cid in cards
            ],
            "hint_card": _card_summary(hint) if hint is not None else None,
        }
    )
    return record


def _review_cards(store: ReferenceStore, decision: Decision) -> dict[str, CardRef]:
    """Fetch the catalogue entries a review row spells out."""
    wanted = list(decision.candidate_ids)
    if decision.hint_card_id:
        wanted.append(decision.hint_card_id)
    return store.get_cards(wanted)


def run_annotate(
    layout: Layout,
    store: ReferenceStore,
    engine: OcrEngine,
    *,
    source: str = "",
    limit: int = 0,
    dry_run: bool = False,
) -> RunSummary:
    """Annotate every staged image that has no decision yet.

    Args:
        layout: Photos root.
        store: Reference catalogue.
        engine: OCR engine.
        source: When set, only this staging source is annotated.
        limit: Stop after this many freshly decided images (0 means all).
        dry_run: Decide and report without writing labels or logs.

    Returns:
        The run's :class:`RunSummary`.
    """
    summary = RunSummary()
    known_codes = store.known_set_codes()
    decided = latest_decisions(layout.annotations)
    fresh = 0
    for staged in iter_staged(layout, source=source):
        previous = decided.get(staged.image_id)
        if previous is not None:
            summary.skipped[f"already {previous}"] += 1
            continue
        if limit and fresh >= limit:
            break
        try:
            decision = annotate_image(staged, store, engine, known_codes)
        except (OSError, ValueError) as error:
            logger.warning("cannot read staged image %s (%s)", staged.path, error)
            summary.skipped["unreadable image"] += 1
            continue
        fresh += 1
        summary.decisions[decision.decision] += 1
        logger.info(
            "%s/%s: %s %s (%s)",
            staged.source,
            staged.image_id,
            decision.decision,
            decision.card_id or "-",
            decision.reason,
        )
        if dry_run:
            continue
        append_jsonl(layout.annotations, decision.as_dict())
        if decision.decision == ACCEPTED:
            write_label(layout, decision.card_id, staged)
        elif decision.decision == REVIEW:
            row = review_record(decision, staged, _review_cards(store, decision))
            append_jsonl(layout.review, row)
    return summary


def adjudicated_decision(image_id: str, card_id: str, note: str) -> Decision:
    """Build the annotation record for one adjudicated review row.

    Args:
        image_id: Image the reviewer decided on.
        card_id: The printing they asserted, or ``""`` for "not resolvable".
        note: Their justification, if they gave one.

    Returns:
        An ``adjudicated`` decision, accepted or rejected.
    """
    verdict = ACCEPTED if card_id else REJECTED
    default = "adjudicated from the review queue" if card_id else "adjudicated as not resolvable"
    return Decision(
        image_id=image_id,
        card_id=card_id,
        decision=verdict,
        reason=note or default,
        agreement=AGREEMENT_ADJUDICATED,
    )


def resolve_adjudicated_read(store: ReferenceStore, row: Mapping[str, object]) -> tuple[str, str]:
    """Resolve an adjudication row that reports what was read instead of an id.

    A vision reviewer can read a card's printed number and set name reliably, but
    asking it for a catalogue id invites an invented one. So a row may carry
    ``number`` (plus optional ``number_total`` and ``set_text``) and the
    catalogue does the resolution here, which can only ever yield a real id.

    Args:
        store: Reference catalogue.
        row: The adjudication row, already known to carry no ``card_id``.

    Returns:
        ``(card_id, skip_reason)``. An empty ``card_id`` with an empty reason
        means the row asserts "not resolvable" and is applied as a rejection; a
        non-empty reason means the row could not be applied at all.
    """
    number = str(row.get("number") or "").strip()
    if not number:
        return "", ""
    raw_total = str(row.get("number_total") or "").strip()
    total = int(raw_total) if raw_total.isdigit() else None
    set_text = str(row.get("set_text") or "").strip().lower()
    ids = sorted(store.find_card_ids_by_number(number.lstrip("0") or "0", total))
    cards = store.get_cards(ids)
    if set_text:
        cards = {
            cid: ref
            for cid, ref in cards.items()
            if set_text in f"{ref.set_name} {ref.set_code or ''} {ref.set_id}".lower()
        }
    if not cards:
        return "", "read resolves to no catalogue printing"
    if len(cards) > 1:
        return "", f"read resolves to {len(cards)} printings"
    return next(iter(cards)), ""


def run_apply(
    layout: Layout, store: ReferenceStore, decisions_file: Path, *, dry_run: bool = False
) -> RunSummary:
    """Apply adjudicated decisions from a JSONL file, closing the review loop.

    Each line carries ``image_id`` and ``card_id`` (empty meaning "not
    resolvable, drop it") and optionally ``note``. A non-empty ``card_id`` known
    to the catalogue labels the staged image exactly as an accepted annotation
    would; an empty one records a rejection. Rows that cannot be applied — an
    unknown image, an image that is not awaiting review, an id the catalogue
    does not know — are counted by reason instead of failing the run, so one bad
    line in an agent's output never costs the rest of the batch.

    Args:
        layout: Photos root.
        store: Reference catalogue.
        decisions_file: JSONL file of adjudicated rows.
        dry_run: Report what would be applied without writing.

    Returns:
        The run's :class:`RunSummary`.
    """
    summary = RunSummary()
    staged_by_id = {staged.image_id: staged for staged in iter_staged(layout)}
    state = latest_decisions(layout.annotations)
    for row in iter_jsonl(decisions_file):
        raw_id = row.get("image_id")
        image_id = raw_id if isinstance(raw_id, str) else ""
        if not image_id:
            summary.skipped["no image_id"] += 1
            continue
        staged = staged_by_id.get(image_id)
        if staged is None:
            summary.skipped["unknown image_id"] += 1
            continue
        if state.get(image_id) != REVIEW:
            summary.skipped[f"not in review ({state.get(image_id, 'never annotated')})"] += 1
            continue
        raw_card = row.get("card_id")
        card_id = raw_card if isinstance(raw_card, str) else ""
        if not card_id:
            card_id, resolve_reason = resolve_adjudicated_read(store, row)
            if resolve_reason:
                summary.skipped[resolve_reason] += 1
                continue
        if card_id and not is_catalogue_id(store, card_id):
            summary.skipped["unknown card_id"] += 1
            continue
        raw_note = row.get("note")
        note = raw_note if isinstance(raw_note, str) else ""
        decision = adjudicated_decision(image_id, card_id, note)
        summary.decisions[decision.decision] += 1
        # Later lines for the same image see the state this one just created,
        # so a duplicated row is skipped rather than labelled twice.
        state[image_id] = decision.decision
        logger.info("%s: %s %s (%s)", image_id, decision.decision, card_id or "-", decision.reason)
        if dry_run:
            continue
        append_jsonl(layout.annotations, decision.as_dict())
        if decision.decision == ACCEPTED:
            write_label(layout, card_id, staged)
    return summary


def run_validate(
    folder: Path, store: ReferenceStore, engine: OcrEngine, *, limit: int = 0
) -> ValidationReport:
    """Measure the ladder against filenames that already carry the truth.

    This is how the pipeline's label quality is proven instead of assumed: the
    same decision path runs over ``{card_id}__*`` images whose printing is
    already known, and every accepted label is compared with it. Such a folder
    has no acquisition sidecars, so there are no hints — what is measured is the
    OCR-plus-catalogue path alone, the half of the ladder that does not depend
    on a source's text.

    Args:
        folder: Directory of labelled images, one level deep like
            :func:`app.recognize.eval.evaluate_folder`.
        store: Reference catalogue.
        engine: OCR engine.
        limit: Stop after this many images (0 means all).

    Returns:
        The :class:`ValidationReport`.
    """
    report = ValidationReport()
    known_codes = store.known_set_codes()
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if limit and report.total >= limit:
            break
        truth = truth_from_name(path)
        if truth is None:
            report.skipped["no truth in filename"] += 1
            continue
        try:
            read = read_strip(path, engine)
        except (OSError, ValueError) as error:
            logger.warning("cannot read %s (%s)", path, error)
            report.skipped["unreadable image"] += 1
            continue
        decision = decide(
            image_id=path.stem,
            read=read,
            candidate_ids=resolve_candidates(store, read.observation, known_codes),
            hint_card_id="",
            hint_known=False,
        )
        report.total += 1
        report.decisions[decision.decision] += 1
        logger.info(
            "%s: %s %s (truth %s; %s)",
            path.name,
            decision.decision,
            decision.card_id or "-",
            truth,
            decision.reason,
        )
        if decision.decision != ACCEPTED:
            continue
        report.accepted += 1
        if decision.card_id == truth:
            report.correct += 1
        else:
            report.confusions.append((path.name, truth, decision.card_id))
    return report


def format_run_summary(summary: RunSummary, layout: Layout, *, dry_run: bool) -> str:
    """Render a run's counts as the tool's stdout summary.

    Args:
        summary: Counts from :func:`run_annotate` or :func:`run_apply`.
        layout: Photos root the paths are reported against.
        dry_run: Whether the run wrote anything.

    Returns:
        A multi-line summary.
    """
    verb = "would write" if dry_run else "wrote"
    total = sum(summary.decisions.values())
    decided = ", ".join(f"{name}: {count}" for name, count in sorted(summary.decisions.items()))
    skipped = ", ".join(f"{reason}: {count}" for reason, count in sorted(summary.skipped.items()))
    return "\n".join(
        [
            f"decided: {total} image(s) ({decided or 'none'})",
            f"labels: {verb} {summary.decisions[ACCEPTED]} image(s) to {layout.labelled}",
            f"review: {verb} {summary.decisions[REVIEW]} row(s) to {layout.review}",
            f"log: {verb} {total} record(s) to {layout.annotations}",
            f"skipped: {sum(summary.skipped.values())} ({skipped or 'none'})",
        ]
    )


def format_validation(report: ValidationReport, folder: Path) -> str:
    """Render a validation run as the tool's stdout summary.

    Args:
        report: Counts from :func:`run_validate`.
        folder: Folder that was measured.

    Returns:
        A multi-line summary ending in the confusion cases, if any.
    """
    decided = ", ".join(f"{name}: {count}" for name, count in sorted(report.decisions.items()))
    lines = [
        f"validated: {report.total} labelled image(s) from {folder}",
        f"precision: {report.precision:.3f} ({report.correct}/{report.accepted} accepted correct)",
        f"recall: {report.recall:.3f} ({report.accepted}/{report.total} images labelled)",
        f"decisions: {decided or 'none'}",
    ]
    if report.skipped:
        skipped = ", ".join(
            f"{reason}: {count}" for reason, count in sorted(report.skipped.items())
        )
        lines.append(f"skipped: {sum(report.skipped.values())} ({skipped})")
    if report.confusions:
        lines.append(f"confusions: {len(report.confusions)} accepted label(s) disagree with truth")
        lines.extend(
            f"  {name}: truth {truth}, labelled {labelled}"
            for name, truth, labelled in report.confusions
        )
    return "\n".join(lines)


def load_ocr_engine() -> OcrEngine:
    """Construct the OCR engine.

    The recognizer treats OCR as optional (``app.recognize.factory._load_ocr``
    degrades to ``None``) because its dense signals still work without it. Here
    OCR *is* the labeller: degrading would silently turn every image into a
    review row, so an unavailable engine is a hard error instead.

    Returns:
        The RapidOCR engine.
    """
    from app.signals.ocr import RapidOcrEngine

    return RapidOcrEngine()


def main() -> None:
    """Parse arguments and run the requested annotation mode."""
    parser = argparse.ArgumentParser(
        description="Annotate acquired real photos into exact-printing labels"
    )
    parser.add_argument(
        "--photos-dir", default=DEFAULT_PHOTOS_DIR, help="photos root holding staging/ and the logs"
    )
    parser.add_argument("--data-dir", default=None, help="reference data root (default: DATA_DIR)")
    parser.add_argument("--source", default="", help="only annotate this staging source")
    parser.add_argument(
        "--limit", type=int, default=0, help="stop after this many images (0 = all)"
    )
    parser.add_argument("--dry-run", action="store_true", help="decide and report, write nothing")
    parser.add_argument(
        "--validate-against",
        default=None,
        metavar="DIR",
        help="measure precision/recall over a folder of {card_id}__* images instead",
    )
    parser.add_argument(
        "--apply-decisions",
        default=None,
        metavar="FILE",
        help="apply adjudicated review rows (JSONL: image_id, card_id, note)",
    )
    args = parser.parse_args()
    configure()

    if args.validate_against is not None and args.apply_decisions is not None:
        parser.error("--validate-against and --apply-decisions are separate modes; pick one")

    data_dir = args.data_dir or config.data_dir()
    if not reference_db_path(data_dir).is_file():
        # ReferenceStore would create an empty catalogue, and every image would
        # then resolve to nothing — a typo must not look like a bad photo pile.
        parser.error(f"no reference catalogue at {reference_db_path(data_dir)}; run `sync` first")

    layout = Layout(Path(args.photos_dir))
    with open_store(data_dir) as store:
        if args.apply_decisions is not None:
            # Adjudication already happened; this mode needs no OCR engine.
            decisions_file = Path(args.apply_decisions)
            if not decisions_file.is_file():
                parser.error(f"decisions file not found: {decisions_file}")
            summary = run_apply(layout, store, decisions_file, dry_run=args.dry_run)
        elif args.validate_against is not None:
            folder = Path(args.validate_against)
            if not folder.is_dir():
                parser.error(f"validation folder not found: {folder}")
            report = run_validate(folder, store, load_ocr_engine(), limit=args.limit)
            print(format_validation(report, folder))
            return
        else:
            summary = run_annotate(
                layout,
                store,
                load_ocr_engine(),
                source=args.source,
                limit=args.limit,
                dry_run=args.dry_run,
            )

    print(format_run_summary(summary, layout, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
