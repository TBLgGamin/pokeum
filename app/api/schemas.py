"""Pydantic response models for the HTTP API.

These mirror the shape of :meth:`app.models.RecognitionResult.as_dict`, so a
route can build its ``dict`` and let FastAPI validate it against the declared
``response_model``. Keeping the models here gives the OpenAPI schema real types
without duplicating the conversion logic.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SetOut(BaseModel):
    """A card's set, as returned by the API."""

    id: str
    name: str
    code: str | None = None


class CardBase(BaseModel):
    """Fields shared by every card representation."""

    card_id: str
    name: str
    set: SetOut
    number: str
    rarity: str | None = None
    image_url: str | None = None


class VariantOut(BaseModel):
    """A print-variant guess for the matched card."""

    kind: str
    present: bool
    confidence: float


class CardOut(CardBase):
    """A scored candidate card."""

    confidence: float
    signals: dict[str, float] = Field(default_factory=dict)


class MatchOut(CardOut):
    """The winning candidate, including its variant guesses."""

    variants: list[VariantOut] = Field(default_factory=list)


class OcrOut(BaseModel):
    """The OCR observation used during fusion."""

    number: str | None = None
    number_total: int | None = None
    set_code: str | None = None
    confidence: float = 0.0


class NameOut(BaseModel):
    """The card name read from the title band and used during fusion."""

    name: str
    similarity: float
    confidence: float


class IdentifyResponse(BaseModel):
    """The full response of the identify endpoint."""

    status: str
    match: MatchOut | None = None
    alternates: list[CardOut] = Field(default_factory=list)
    ocr: OcrOut | None = None
    name: NameOut | None = None


class HealthResponse(BaseModel):
    """Liveness and readiness summary."""

    status: str
    cards_indexed: int
    embedder_loaded: bool
    embedder_id: str


class CardDetailResponse(CardBase):
    """Catalogue detail for a single card."""
