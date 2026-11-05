"""Unit tests for the printing discriminator (app/recognize/rerank.py)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from app.core import constants
from app.models import Candidate, CardRef
from app.recognize.rerank import rerank_printings
from app.vision import regions

_NAME = "Pikachu"
# Side of the coarse grid every synthetic region is built from. Blocks (rather
# than per-pixel noise) survive the hashers' downsampling to 64x64, and 16 of
# them keeps adjacent dhash columns in different blocks so no hash kind agrees
# by construction between two unrelated patterns.
_BLOCKS = 16


def _pattern(height: int, width: int, seed: int) -> np.ndarray:
    """Return a deterministic blocky RGB pattern of the requested size."""
    rng = np.random.default_rng(seed)
    coarse = rng.integers(0, 256, size=(_BLOCKS, _BLOCKS, 3), dtype=np.uint8)
    rows = np.linspace(0, _BLOCKS - 1, height).astype(np.intp)
    cols = np.linspace(0, _BLOCKS - 1, width).astype(np.intp)
    return coarse[rows[:, None], cols[None, :]]


def _card(*, strip_seed: int, artwork_seed: int = 7, body_seed: int = 1) -> np.ndarray:
    """Build a canonical-size card from independently seeded regions.

    The bottom strip stands in for what actually differs between printings of
    one artwork; the artwork window stands in for what never does.
    """
    card = _pattern(constants.CARD_HEIGHT_PX, constants.CARD_WIDTH_PX, body_seed)
    art = regions.artwork(card)
    art[:] = _pattern(art.shape[0], art.shape[1], artwork_seed)
    strip = regions.bottom_strip(card)
    strip[:] = _pattern(strip.shape[0], strip.shape[1], strip_seed)
    return card


def _write(path: Path, card: np.ndarray) -> str:
    """Save a synthetic card as a lossless reference image and return its path."""
    Image.fromarray(card).save(path)
    return str(path)


def _candidate(card_id: str, name: str, confidence: float) -> Candidate:
    return Candidate(
        card=CardRef(
            card_id=card_id, name=name, set_id="s1", set_name="Set One", number=card_id[-1]
        ),
        confidence=confidence,
        per_signal={"hash": confidence},
    )


def test_matching_strip_is_promoted_with_a_positive_margin(tmp_path: Path) -> None:
    query = _card(strip_seed=11)
    leader = _candidate("s1-1", _NAME, 0.62)
    challenger = _candidate("s1-2", _NAME, 0.60)
    paths = {
        "s1-1": _write(tmp_path / "a.png", _card(strip_seed=22)),
        "s1-2": _write(tmp_path / "b.png", _card(strip_seed=11)),
    }

    ordered, margin = rerank_printings(query, [leader, challenger], paths)

    # Same objects, reordered: fusion's confidences must survive untouched.
    assert ordered[0] is challenger
    assert ordered[1] is leader
    assert 0.0 < margin <= 1.0


def test_identical_discriminating_regions_preserve_fusion_order(tmp_path: Path) -> None:
    query = _card(strip_seed=11)
    first = _candidate("s1-1", _NAME, 0.62)
    second = _candidate("s1-2", _NAME, 0.61)
    paths = {
        # The two references differ only in the artwork window, which the
        # discriminator must ignore: nothing here separates the printings.
        "s1-1": _write(tmp_path / "a.png", _card(strip_seed=11, artwork_seed=31)),
        "s1-2": _write(tmp_path / "b.png", _card(strip_seed=11, artwork_seed=32)),
    }

    ordered, margin = rerank_printings(query, [first, second], paths)

    assert [candidate.card.card_id for candidate in ordered] == ["s1-1", "s1-2"]
    assert margin == 0.0


def test_unreadable_reference_leaves_its_candidate_unranked(tmp_path: Path) -> None:
    query = _card(strip_seed=11)
    leader = _candidate("s1-1", _NAME, 0.62)
    challenger = _candidate("s1-2", _NAME, 0.60)
    paths = {
        "s1-1": str(tmp_path / "never-written.png"),
        "s1-2": _write(tmp_path / "b.png", _card(strip_seed=11)),
    }

    ordered, margin = rerank_printings(query, [leader, challenger], paths)

    assert ordered[0] is challenger
    assert ordered[1] is leader
    # One readable printing is a match, not a comparison: claim no margin.
    assert margin == 0.0


def test_other_artwork_is_never_promoted_into_the_tie(tmp_path: Path) -> None:
    query = _card(strip_seed=11)
    leader = _candidate("s1-1", _NAME, 0.62)
    challenger = _candidate("s1-2", _NAME, 0.60)
    other = _candidate("s1-3", "Charizard", 0.58)
    paths = {
        "s1-1": _write(tmp_path / "a.png", _card(strip_seed=22)),
        "s1-2": _write(tmp_path / "b.png", _card(strip_seed=11)),
        # A perfect region match, but a different artwork: out of the tie group.
        "s1-3": _write(tmp_path / "c.png", query),
    }

    ordered, margin = rerank_printings(query, [leader, challenger, other], paths)

    assert [candidate.card.card_id for candidate in ordered] == ["s1-2", "s1-1", "s1-3"]
    assert margin > 0.0


def test_references_matching_nothing_keep_fusion_order(tmp_path: Path) -> None:
    query = _card(strip_seed=11)
    leader = _candidate("s1-1", _NAME, 0.62)
    challenger = _candidate("s1-2", _NAME, 0.60)
    paths = {
        # Different body and strip: neither reference resembles the capture, so
        # the score gap between them is noise rather than evidence.
        "s1-1": _write(tmp_path / "a.png", _card(strip_seed=22, body_seed=98)),
        "s1-2": _write(tmp_path / "b.png", _card(strip_seed=33, body_seed=99)),
    }

    ordered, margin = rerank_printings(query, [leader, challenger], paths)

    assert [candidate.card.card_id for candidate in ordered] == ["s1-1", "s1-2"]
    assert margin == 0.0


def test_a_lone_candidate_is_returned_untouched(tmp_path: Path) -> None:
    query = _card(strip_seed=11)
    only = _candidate("s1-1", _NAME, 0.62)
    paths = {"s1-1": _write(tmp_path / "a.png", _card(strip_seed=11))}

    assert rerank_printings(query, [only], paths) == ([only], 0.0)
    assert rerank_printings(query, [], paths) == ([], 0.0)
