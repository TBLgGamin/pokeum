"""Unit tests for title-name matching (app/signals/name.py) and its fusion boost."""

from __future__ import annotations

from app.models import CardRef, NameObservation, SignalScore
from app.recognize import fusion
from app.signals.name import NameMatcher

_CATALOGUE = [
    ("a-1", "Pikachu"),
    ("a-2", "Pikachu"),
    ("b-1", "Pikachu ex"),
    ("c-1", "Pikachu on the Ball"),
    ("d-1", "Team Magma's Baltoy"),
    ("d-2", "Baltoy"),
    ("e-1", "Mew"),
    ("e-2", "Mewtwo"),
    ("f-1", "Squawkabilly ex"),
    ("g-1", "Slaking"),
    ("g-2", "Vigoroth"),
    ("h-1", "Flabébé"),
]


def _matcher() -> NameMatcher:
    return NameMatcher(_CATALOGUE)


def test_stage_label_and_hp_on_the_title_line_are_ignored() -> None:
    observed = _matcher().match([("TAGE2 Slaking", 0.98), ("HP 150", 0.9)])
    assert observed is not None
    assert observed.name == "Slaking" and observed.similarity == 1.0


def test_evolves_from_line_never_supplies_the_name() -> None:
    # The only name on screen besides the card's own is the pre-evolution's.
    observed = _matcher().match([("Evolves from Vigoroth", 1.0)])
    assert observed is None


def test_longest_exact_name_wins() -> None:
    observed = _matcher().match([("BASIC", 1.0), ("Pikachu on the Ball", 0.98)])
    assert observed is not None
    assert observed.name == "Pikachu on the Ball"
    assert observed.card_ids == frozenset({"c-1"})


def test_dropped_space_and_accents_still_match_exactly() -> None:
    matcher = _matcher()
    squawk = matcher.match([("Squawkabillyex", 0.94)])
    assert squawk is not None and squawk.name == "Squawkabilly ex"
    flabebe = matcher.match([("Flabebe", 0.9)])
    assert flabebe is not None and flabebe.name == "Flabébé"


def test_family_covers_suffixes_and_owner_prefixes_but_not_substrings() -> None:
    matcher = _matcher()
    pikachu = matcher.match([("Pikachu", 1.0)])
    assert pikachu is not None
    assert pikachu.card_ids == frozenset({"a-1", "a-2", "b-1", "c-1"})
    baltoy = matcher.match([("Baltoy", 1.0)])
    assert baltoy is not None and baltoy.card_ids == frozenset({"d-1", "d-2"})
    mew = matcher.match([("Mew", 1.0)])
    assert mew is not None and mew.card_ids == frozenset({"e-1"})


def test_fuzzy_match_needs_a_long_key_and_high_similarity() -> None:
    matcher = _matcher()
    misread = matcher.match([("Squawkabilly ox", 0.9)])
    assert misread is not None and misread.name == "Squawkabilly ex"
    assert misread.similarity < 1.0
    # Short names are one OCR slip from another real name: no fuzzy guess.
    assert matcher.match([("Mow", 1.0)]) is None


def _card(card_id: str, name: str) -> CardRef:
    return CardRef(card_id=card_id, name=name, set_id="s", set_name="S", number="1")


def test_name_boost_lifts_the_family_over_a_close_rival() -> None:
    cards = {"x": _card("x", "Ponyta"), "y": _card("y", "Rapidash")}
    dense = {fusion.SIGNAL_EMB_FULL: [SignalScore("y", 0.80), SignalScore("x", 0.78)]}
    name = NameObservation(
        raw_text="Ponyta", name="Ponyta", similarity=1.0, confidence=1.0, card_ids=frozenset({"x"})
    )

    _, without = fusion.fuse(dense, cards.get)
    _, boosted = fusion.fuse(dense, cards.get, name=name)

    assert without[0].card.card_id == "y"
    assert boosted[0].card.card_id == "x"
