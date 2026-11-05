"""Unit tests for pure geometry helpers (app/vision/geometry.py)."""

from __future__ import annotations

import numpy as np

from app.vision import geometry


def test_order_corners_from_scrambled_input() -> None:
    # Given an axis-aligned rectangle whose corners are in a jumbled order,
    ordered = geometry.order_corners(
        np.array([[10, 90], [10, 10], [50, 90], [50, 10]], dtype=np.float32)
    )
    # they come back as TL, TR, BR, BL.
    assert ordered[0].tolist() == [10, 10]
    assert ordered[1].tolist() == [50, 10]
    assert ordered[2].tolist() == [50, 90]
    assert ordered[3].tolist() == [10, 90]


def test_polygon_area_of_rectangle() -> None:
    rect = np.array([[0, 0], [40, 0], [40, 88], [0, 88]], dtype=np.float32)
    assert geometry.polygon_area(rect) == 40 * 88


def test_aspect_ratio_portrait_card() -> None:
    rect = np.array([[0, 0], [63, 0], [63, 88], [0, 88]], dtype=np.float32)
    assert abs(geometry.aspect_ratio(rect) - 63 / 88) < 1e-5


def test_is_plausible_card_accepts_card_shape() -> None:
    frame_area = 600.0 * 800.0
    card = np.array([[150, 150], [450, 150], [450, 570], [150, 570]], dtype=np.float32)
    assert geometry.is_plausible_card(card, frame_area)


def test_is_plausible_card_rejects_tiny_region() -> None:
    frame_area = 600.0 * 800.0
    tiny = np.array([[0, 0], [20, 0], [20, 28], [0, 28]], dtype=np.float32)
    assert not geometry.is_plausible_card(tiny, frame_area)


def test_is_plausible_card_accepts_a_card_lying_sideways() -> None:
    frame_area = 600.0 * 800.0
    sideways = np.array([[50, 50], [470, 50], [470, 350], [50, 350]], dtype=np.float32)
    assert geometry.is_plausible_card(sideways, frame_area)
    assert geometry.is_sideways(geometry.order_corners(sideways))


def test_is_plausible_card_rejects_a_skewed_quad_in_the_card_band() -> None:
    # Overall aspect is card-like, but the corners are far from square: a hull of
    # background clutter, not a card under perspective.
    frame_area = 600.0 * 800.0
    skewed = np.array([[100, 100], [400, 300], [400, 700], [100, 500]], dtype=np.float32)
    assert not geometry.is_plausible_card(skewed, frame_area)


def _rotated_card(degrees: float) -> np.ndarray:
    """Return the corners of a 300x420 portrait card rotated about (400, 400)."""
    theta = np.deg2rad(degrees)
    rotation = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    upright = np.array([[-150, -210], [150, -210], [150, 210], [-150, 210]], dtype=np.float64)
    return (upright @ rotation.T + 400).astype(np.float32)


def test_order_corners_normalises_any_rotation_to_portrait() -> None:
    for degrees in (0, 30, 60, 90, 135, 200, 270, 330):
        ordered = geometry.order_corners(_rotated_card(degrees))
        top = float(np.linalg.norm(ordered[1] - ordered[0]))
        left = float(np.linalg.norm(ordered[3] - ordered[0]))
        assert abs(top - 300) < 1e-3 and abs(left - 420) < 1e-3, degrees
        assert abs(geometry.aspect_ratio(ordered) - 300 / 420) < 1e-4


def test_order_corners_keeps_a_tilted_card_upright() -> None:
    # Within ±45 degrees of upright the higher short side stays the top, so the
    # warp needs no 180-degree hypothesis to read the card the right way up.
    original = _rotated_card(-30)
    ordered = geometry.order_corners(original)
    np.testing.assert_allclose(ordered[0], original[0], atol=1e-3)
