"""Domain types shared across the recognizer.

These are plain, immutable value objects: a reference card (:class:`CardRef`),
the raw outputs of individual signals (:class:`SignalScore`,
:class:`OcrObservation`), and the fused answer the pipeline returns
(:class:`Candidate`, :class:`VariantGuess`, :class:`RecognitionResult`).

They deliberately depend on nothing but the standard library and
:mod:`app.core`, so every layer above can speak the same vocabulary without
importing heavy vision or model code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RecognitionStatus(StrEnum):
    """Outcome class of a recognition attempt.

    Attributes:
        CONFIDENT: A single card cleared the confidence and margin thresholds.
        UNCERTAIN: A best guess exists but below the confident bar; alternates matter.
        AMBIGUOUS: The artwork is identified but two printings of it are too close
            to separate; the caller must choose.
        NO_MATCH: Nothing scored high enough to report.
        NO_CARD_DETECTED: No card-shaped region was found in the image.
    """

    CONFIDENT = "confident"
    UNCERTAIN = "uncertain"
    AMBIGUOUS = "ambiguous"
    NO_MATCH = "no_match"
    NO_CARD_DETECTED = "no_card_detected"


class VariantKind(StrEnum):
    """A print variant the pipeline can assert on the winning card."""

    REVERSE_HOLO = "reverse_holo"
    FIRST_EDITION = "first_edition"
    SHADOWLESS = "shadowless"
    PROMO_STAMP = "promo_stamp"


@dataclass(frozen=True, slots=True)
class CardRef:
    """A reference card as stored in the local catalogue.

    Attributes:
        card_id: Globally unique TCGdex id (e.g. ``swsh3-136``).
        name: Printed card name.
        set_id: Id of the set the card belongs to.
        set_name: Human-readable set name.
        set_code: Short set code printed on modern cards (e.g. ``PAL``), if known.
        number: Printed collector number within the set (its local id).
        number_total: Official card count printed after the slash, if known.
        rarity: Rarity string as reported by the catalogue, if known.
        era: Coarse era bucket used for variant gating (``wotc`` or ``modern``).
        release_year: Year the set was released, if known.
        image_url: Public TCGdex CDN URL of the card image, if known.
        image_path: Local path to the cached reference image, if downloaded.
        has_reverse: Whether the catalogue lists a reverse-holo variant.
        has_first_edition: Whether the catalogue lists a 1st Edition variant.
        has_holo: Whether the catalogue lists a holofoil variant.
        has_normal: Whether the catalogue lists a plain (non-foil) variant.
    """

    card_id: str
    name: str
    set_id: str
    set_name: str
    number: str
    set_code: str | None = None
    number_total: int | None = None
    rarity: str | None = None
    era: str = "modern"
    release_year: int | None = None
    image_url: str | None = None
    image_path: str | None = None
    has_reverse: bool = False
    has_first_edition: bool = False
    has_holo: bool = False
    has_normal: bool = True

    @property
    def display_number(self) -> str:
        """Return the collector number as printed, e.g. ``136/202`` or ``136``."""
        if self.number_total:
            return f"{self.number}/{self.number_total}"
        return self.number

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable summary of the card for API and CLI output."""
        return {
            "card_id": self.card_id,
            "name": self.name,
            "set": {"id": self.set_id, "name": self.set_name, "code": self.set_code},
            "number": self.display_number,
            "rarity": self.rarity,
            "image_url": self.image_url,
        }


@dataclass(frozen=True, slots=True)
class SignalScore:
    """One card's similarity score from one signal, normalized to ``[0, 1]``.

    Attributes:
        card_id: Reference card the score refers to.
        score: Similarity in ``[0, 1]``; higher means a closer match.
    """

    card_id: str
    score: float


@dataclass(frozen=True, slots=True)
class OcrObservation:
    """Text parsed from the card's bottom strip.

    Attributes:
        number: Collector number digits, e.g. ``136`` (leading zeros stripped).
        number_total: Set total printed after the slash, if read.
        set_code: Uppercase set code, e.g. ``PAL``, if read.
        raw_text: The raw OCR text, kept for debugging and confidence.
        confidence: OCR engine confidence in ``[0, 1]``.
    """

    raw_text: str
    number: str | None = None
    number_total: int | None = None
    set_code: str | None = None
    confidence: float = 0.0

    @property
    def is_useful(self) -> bool:
        """Whether the observation carries anything that can disambiguate a card."""
        return bool(self.number or self.set_code)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable view of the observation."""
        return {
            "number": self.number,
            "number_total": self.number_total,
            "set_code": self.set_code,
            "confidence": round(self.confidence, 3),
        }


@dataclass(frozen=True, slots=True)
class NameObservation:
    """A card name read from the title band and matched to the catalogue.

    Attributes:
        raw_text: The OCR line the name was found in.
        name: The matched catalogue name.
        similarity: How closely the read matched the name (1.0 = exact key).
        confidence: OCR engine confidence of that line in ``[0, 1]``.
        card_ids: Every card of the name family (the name, or the name plus
            whole-word suffixes such as "ex" or "V").
    """

    raw_text: str
    name: str
    similarity: float
    confidence: float
    card_ids: frozenset[str] = frozenset()

    @property
    def strength(self) -> float:
        """Return the evidence weight in ``[0, 1]``: similarity times OCR confidence."""
        return self.similarity * self.confidence

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable view of the observation."""
        return {
            "name": self.name,
            "similarity": round(self.similarity, 3),
            "confidence": round(self.confidence, 3),
        }


@dataclass(frozen=True, slots=True)
class VariantGuess:
    """A rule-based assertion about one print variant of the winning card.

    Attributes:
        kind: Which variant this guess concerns.
        present: Whether the variant is judged present on the imaged card.
        confidence: Confidence of the judgement in ``[0, 1]``.
    """

    kind: VariantKind
    present: bool
    confidence: float

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable view of the guess."""
        return {
            "kind": self.kind.value,
            "present": self.present,
            "confidence": round(self.confidence, 3),
        }


@dataclass(frozen=True, slots=True)
class Candidate:
    """A scored reference card produced by fusion.

    Attributes:
        card: The reference card.
        confidence: Fused, normalized confidence in ``[0, 1]``.
        per_signal: Raw contribution of each signal, keyed by signal name.
    """

    card: CardRef
    confidence: float
    per_signal: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable view of the candidate."""
        data = self.card.as_dict()
        data["confidence"] = round(self.confidence, 3)
        data["signals"] = {k: round(v, 3) for k, v in self.per_signal.items()}
        return data


@dataclass(frozen=True, slots=True)
class RecognitionResult:
    """The full answer for one image: the pick, its alternates, and variants.

    Attributes:
        status: Outcome class of the attempt.
        match: The winning candidate, or ``None`` when nothing was picked.
        alternates: Lower-ranked candidates, best first.
        variants: Variant guesses for the winning card.
        ocr: The OCR observation used during fusion, if any.
        name: The title-band name read used during fusion, if any.
    """

    status: RecognitionStatus
    match: Candidate | None = None
    alternates: tuple[Candidate, ...] = ()
    variants: tuple[VariantGuess, ...] = ()
    ocr: OcrObservation | None = None
    name: NameObservation | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable view of the whole result."""
        match = self.match.as_dict() if self.match else None
        if match is not None:
            match["variants"] = [v.as_dict() for v in self.variants]
        return {
            "status": self.status.value,
            "match": match,
            "alternates": [c.as_dict() for c in self.alternates],
            "ocr": self.ocr.as_dict() if self.ocr else None,
            "name": self.name.as_dict() if self.name else None,
        }
