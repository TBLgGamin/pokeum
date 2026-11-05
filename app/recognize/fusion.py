"""Fuse per-signal scores into ranked candidates and a decision.

The maths here is deliberately pure — dictionaries and floats, no NumPy, no image
code — so every rule (weight renormalization, the OCR boost/penalty, the
confidence thresholds) is directly unit-testable.

Dense signals (embeddings, hash, symbol) contribute a weighted average, computed
only over the signals that actually ran so a missing model never zeroes a card.
OCR then multiplies each candidate up or down depending on whether its collector
number agrees, but never eliminates anyone outright.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from app.core import constants
from app.models import (
    Candidate,
    CardRef,
    NameObservation,
    OcrObservation,
    RecognitionStatus,
    SignalScore,
)

# Canonical dense-signal names and their fusion weights.
SIGNAL_EMB_FULL = "emb_full"
SIGNAL_EMB_ART = "emb_art"
SIGNAL_HASH = "hash"
SIGNAL_SYMBOL = "symbol"

SIGNAL_WEIGHTS: dict[str, float] = {
    SIGNAL_EMB_FULL: constants.WEIGHT_EMBED_FULL,
    SIGNAL_EMB_ART: constants.WEIGHT_EMBED_ART,
    SIGNAL_HASH: constants.WEIGHT_HASH,
    SIGNAL_SYMBOL: constants.WEIGHT_SYMBOL,
}

Resolver = Callable[[str], CardRef | None]


def _score_maps(
    dense: Mapping[str, Sequence[SignalScore]],
) -> tuple[dict[str, dict[str, float]], list[str]]:
    """Return per-signal ``{card_id: score}`` maps and the list of present signals."""
    maps: dict[str, dict[str, float]] = {}
    present: list[str] = []
    for name, scores in dense.items():
        if not scores or name not in SIGNAL_WEIGHTS:
            continue
        maps[name] = {s.card_id: s.score for s in scores}
        present.append(name)
    return maps, present


def _ocr_factor(card_id: str, ocr: OcrObservation | None, consistent_ids: frozenset[str]) -> float:
    """Return the multiplicative OCR adjustment for one candidate."""
    if ocr is None or not ocr.is_useful or ocr.confidence <= 0:
        return 1.0
    if card_id in consistent_ids:
        return 1.0 + constants.OCR_BOOST * ocr.confidence
    return 1.0 - constants.OCR_PENALTY * ocr.confidence


def _name_factor(card_id: str, name: NameObservation | None) -> float:
    """Return the multiplicative title-name adjustment for one candidate.

    Boost-only: a name family is large and a misread name is common enough
    that pushing everyone else down would turn one bad read into a wrong
    answer, while lifting the family only ever settles a near-tie.
    """
    if name is None or card_id not in name.card_ids:
        return 1.0
    return 1.0 + constants.NAME_BOOST * name.strength


def fuse(
    dense: Mapping[str, Sequence[SignalScore]],
    resolve: Resolver,
    *,
    ocr: OcrObservation | None = None,
    ocr_consistent_ids: frozenset[str] = frozenset(),
    name: NameObservation | None = None,
    top_k: int = constants.DEFAULT_TOP_K,
) -> tuple[RecognitionStatus, list[Candidate]]:
    """Combine signal scores into ranked candidates and an outcome status.

    Args:
        dense: Mapping of dense-signal name to its ``SignalScore`` shortlist.
        resolve: Turns a card id into a :class:`CardRef` (``None`` to drop it).
        ocr: OCR observation, if any, applied as a soft multiplier.
        ocr_consistent_ids: Card ids whose collector number matches the OCR read.
        name: Title-band name read, if any, applied as a boost-only multiplier
            to its name family.
        top_k: Maximum number of candidates to return.

    Returns:
        The decision status and the ranked candidates (best first, up to
        ``top_k``). The list is empty only when no signal produced anything.
    """
    maps, present = _score_maps(dense)
    if not present:
        return RecognitionStatus.NO_MATCH, []
    denom = sum(SIGNAL_WEIGHTS[signal] for signal in present)

    candidate_ids = {cid for signal in present for cid in maps[signal]}
    scored: list[tuple[str, float, dict[str, float]]] = []
    for cid in candidate_ids:
        per_signal = {signal: maps[signal].get(cid, 0.0) for signal in present}
        weighted = sum(SIGNAL_WEIGHTS[signal] * per_signal[signal] for signal in present)
        raw = (
            (weighted / denom) * _ocr_factor(cid, ocr, ocr_consistent_ids) * _name_factor(cid, name)
        )
        scored.append((cid, raw, per_signal))

    scored.sort(key=lambda item: item[1], reverse=True)
    top_raw = scored[0][1] if scored else 0.0
    if top_raw <= 0.0:
        return RecognitionStatus.NO_MATCH, []

    quality = min(top_raw, 1.0)
    candidates: list[Candidate] = []
    for cid, raw, per_signal in scored:
        card = resolve(cid)
        if card is None:
            continue
        confidence = (raw / top_raw) * quality
        candidates.append(Candidate(card=card, confidence=confidence, per_signal=per_signal))
        if len(candidates) >= top_k:
            break

    status = _decide(candidates)
    return status, candidates


def combine_frames(
    frames: Sequence[Sequence[Candidate]], top_k: int = constants.DEFAULT_TOP_K
) -> tuple[RecognitionStatus, list[Candidate]]:
    """Merge several frames' ranked candidates of the same card into one answer.

    A card's combined confidence is its mean over the frames, counting a frame
    that did not rank it as 0: one glared frame that liked a different card
    cannot outvote two clean frames that agree, and a card seen in only one
    frame cannot look as sure as one every frame found.

    Args:
        frames: Each frame's candidates, best first.
        top_k: Maximum number of candidates to return.

    Returns:
        The decision status and the combined ranked candidates. Each keeps the
        per-signal breakdown of the frame where it scored highest.
    """
    if not frames:
        return RecognitionStatus.NO_MATCH, []
    totals: dict[str, float] = {}
    best: dict[str, Candidate] = {}
    for candidates in frames:
        for candidate in candidates:
            card_id = candidate.card.card_id
            totals[card_id] = totals.get(card_id, 0.0) + candidate.confidence
            if card_id not in best or candidate.confidence > best[card_id].confidence:
                best[card_id] = candidate
    ranked = sorted(totals, key=lambda card_id: totals[card_id], reverse=True)[:top_k]
    combined = [
        Candidate(
            card=best[card_id].card,
            confidence=totals[card_id] / len(frames),
            per_signal=best[card_id].per_signal,
        )
        for card_id in ranked
    ]
    return _decide(combined), combined


def _decide(candidates: list[Candidate]) -> RecognitionStatus:
    """Classify the outcome from the ranked candidates' confidences."""
    if not candidates:
        return RecognitionStatus.NO_MATCH
    top = candidates[0].confidence
    runner_up = candidates[1].confidence if len(candidates) > 1 else 0.0
    if top >= constants.CONFIDENT_THRESHOLD and (top - runner_up) >= constants.CONFIDENT_MARGIN:
        return RecognitionStatus.CONFIDENT
    if top >= constants.UNCERTAIN_THRESHOLD:
        # Equal names mean the same artwork family (a reprint or an alternate
        # printing). OCR agreement would already have pulled the right printing
        # clear, so a tie here says OCR did not read: ask the user to pick.
        if (
            len(candidates) > 1
            and candidates[0].card.name == candidates[1].card.name
            and (top - runner_up) < constants.CONFIDENT_MARGIN
        ):
            return RecognitionStatus.AMBIGUOUS
        return RecognitionStatus.UNCERTAIN
    return RecognitionStatus.NO_MATCH
