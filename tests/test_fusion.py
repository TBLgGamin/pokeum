"""Unit tests for signal fusion (app/recognize/fusion.py)."""

from __future__ import annotations

from app.models import Candidate, CardRef, OcrObservation, RecognitionStatus, SignalScore
from app.recognize import fusion


def _cards(*ids: str) -> dict[str, CardRef]:
    return {
        cid: CardRef(card_id=cid, name=cid, set_id="s", set_name="S", number="1") for cid in ids
    }


def _resolve(cards: dict[str, CardRef]):
    return cards.get


def test_single_signal_single_card_is_confident() -> None:
    cards = _cards("a")
    status, ranked = fusion.fuse({fusion.SIGNAL_HASH: [SignalScore("a", 0.9)]}, _resolve(cards))
    assert status == RecognitionStatus.CONFIDENT
    assert ranked[0].card.card_id == "a"
    assert ranked[0].confidence == 0.9


def test_close_scores_are_uncertain() -> None:
    cards = _cards("a", "b")
    status, ranked = fusion.fuse(
        {fusion.SIGNAL_HASH: [SignalScore("a", 0.85), SignalScore("b", 0.80)]},
        _resolve(cards),
    )
    assert status == RecognitionStatus.UNCERTAIN
    assert [c.card.card_id for c in ranked] == ["a", "b"]


def test_weights_renormalize_over_present_signals() -> None:
    cards = _cards("a")
    # Only the embedding signal is present, so a perfect embedding score alone
    # should reach full confidence despite hash/symbol being absent.
    status, ranked = fusion.fuse({fusion.SIGNAL_EMB_FULL: [SignalScore("a", 1.0)]}, _resolve(cards))
    assert status == RecognitionStatus.CONFIDENT
    assert ranked[0].confidence == 1.0


def test_missing_card_in_one_signal_scores_zero_there() -> None:
    cards = _cards("a")
    # emb=1.0 but hash omits the card (score 0): weighted avg over both present
    # signals is 0.4/(0.4+0.2) = 0.667.
    _, ranked = fusion.fuse(
        {
            fusion.SIGNAL_EMB_FULL: [SignalScore("a", 1.0)],
            fusion.SIGNAL_HASH: [SignalScore("z", 0.5)],
        },
        {"a": cards["a"], "z": CardRef("z", "z", "s", "S", "2")}.get,
    )
    top = next(c for c in ranked if c.card.card_id == "a")
    assert abs(top.per_signal[fusion.SIGNAL_EMB_FULL] - 1.0) < 1e-9
    assert top.per_signal[fusion.SIGNAL_HASH] == 0.0


def test_ocr_boost_flips_a_tie() -> None:
    cards = _cards("a", "b")
    ocr = OcrObservation(raw_text="", number="1", number_total=99, confidence=1.0)
    status, ranked = fusion.fuse(
        {fusion.SIGNAL_EMB_FULL: [SignalScore("a", 0.6), SignalScore("b", 0.6)]},
        _resolve(cards),
        ocr=ocr,
        ocr_consistent_ids=frozenset({"b"}),
    )
    assert ranked[0].card.card_id == "b"
    assert status == RecognitionStatus.CONFIDENT


def test_no_signals_is_no_match() -> None:
    status, ranked = fusion.fuse({}, _resolve(_cards("a")))
    assert status == RecognitionStatus.NO_MATCH
    assert ranked == []


def _printings(*ids: str, name: str = "Pikachu") -> dict[str, CardRef]:
    """Return cards that share one artwork name across different printings."""
    return {
        cid: CardRef(card_id=cid, name=name, set_id=cid, set_name=cid.upper(), number="1")
        for cid in ids
    }


def test_close_scores_on_same_artwork_are_ambiguous() -> None:
    cards = _printings("a", "b")
    status, ranked = fusion.fuse(
        {fusion.SIGNAL_HASH: [SignalScore("a", 0.78), SignalScore("b", 0.74)]},
        _resolve(cards),
    )
    assert status == RecognitionStatus.AMBIGUOUS
    assert [c.card.card_id for c in ranked] == ["a", "b"]


def test_close_scores_on_different_artwork_stay_uncertain() -> None:
    cards = _cards("a", "b")
    status, _ = fusion.fuse(
        {fusion.SIGNAL_HASH: [SignalScore("a", 0.78), SignalScore("b", 0.74)]},
        _resolve(cards),
    )
    assert status == RecognitionStatus.UNCERTAIN


def test_clear_winner_on_same_artwork_is_confident() -> None:
    cards = _printings("a", "b")
    status, _ = fusion.fuse(
        {fusion.SIGNAL_HASH: [SignalScore("a", 0.90), SignalScore("b", 0.74)]},
        _resolve(cards),
    )
    assert status == RecognitionStatus.CONFIDENT


def _frame(cards: dict[str, CardRef], *scores: tuple[str, float]) -> list[Candidate]:
    return [Candidate(card=cards[cid], confidence=conf) for cid, conf in scores]


def test_combined_frames_follow_the_agreeing_majority() -> None:
    cards = _cards("a", "b")
    # One glared frame loves "b"; two clean frames agree on "a".
    frames = [
        _frame(cards, ("b", 0.95), ("a", 0.40)),
        _frame(cards, ("a", 0.85), ("b", 0.30)),
        _frame(cards, ("a", 0.88), ("b", 0.35)),
    ]
    status, combined = fusion.combine_frames(frames)
    assert combined[0].card.card_id == "a"
    assert abs(combined[0].confidence - (0.40 + 0.85 + 0.88) / 3) < 1e-9
    assert status is RecognitionStatus.UNCERTAIN


def test_a_card_seen_in_one_frame_only_is_diluted() -> None:
    cards = _cards("a", "b")
    frames = [_frame(cards, ("a", 0.90)), _frame(cards, ("b", 0.90))]
    status, combined = fusion.combine_frames(frames)
    assert [round(c.confidence, 3) for c in combined] == [0.45, 0.45]
    assert status is RecognitionStatus.NO_MATCH
