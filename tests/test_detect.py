"""Unit tests for card detection and rectification (app/vision)."""

from __future__ import annotations

import cv2
import numpy as np

from app.core import constants
from app.vision.detect import detect_card_quad
from app.vision.geometry import is_plausible_card, is_sideways
from app.vision.rectify import rectify_or_whole


def _frame_with_card() -> np.ndarray:
    frame = np.full((800, 600, 3), 30, dtype=np.uint8)
    # A bright portrait rectangle (~0.71 aspect) centred on a dark background.
    frame[190:610, 150:450] = 235
    return frame


def test_detects_card_quad() -> None:
    frame = _frame_with_card()
    quad = detect_card_quad(frame)
    assert quad is not None
    assert is_plausible_card(quad, float(frame.shape[0] * frame.shape[1]))


def test_blank_image_has_no_card() -> None:
    assert detect_card_quad(np.full((800, 600, 3), 120, dtype=np.uint8)) is None


def test_detects_card_with_clipped_corners() -> None:
    # Real cards have rounded corners: the raw contour is an octagon-ish shape,
    # not a clean 4-gon. The convex-hull pass must still recover the quad.
    frame = np.full((800, 600, 3), 30, dtype=np.uint8)
    frame[190:610, 150:450] = 235
    cut = 40
    for row in range(cut):
        width = cut - row
        frame[190 + row, 150 : 150 + width] = 30  # top-left corner
        frame[190 + row, 450 - width : 450] = 30  # top-right corner
        frame[609 - row, 150 : 150 + width] = 30  # bottom-left corner
        frame[609 - row, 450 - width : 450] = 30  # bottom-right corner
    quad = detect_card_quad(frame)
    assert quad is not None
    assert is_plausible_card(quad, float(frame.shape[0] * frame.shape[1]))


def test_detects_low_contrast_card() -> None:
    # A dim scene: card at 70 on a 40 background. Fixed Canny thresholds
    # (50/150) miss this edge entirely; median-adaptive thresholds must not.
    frame = np.full((800, 600, 3), 40, dtype=np.uint8)
    frame[190:610, 150:450] = 70
    quad = detect_card_quad(frame)
    assert quad is not None
    assert is_plausible_card(quad, float(frame.shape[0] * frame.shape[1]))


def test_detects_card_in_oversized_image_in_original_coords() -> None:
    # Larger than DETECT_MAX_SIDE: detection runs downscaled, but the returned
    # quad must be in the source image's coordinate system.
    frame = np.full((2200, 1650, 3), 30, dtype=np.uint8)
    top, bottom, left, right = 500, 1700, 400, 1250
    frame[top:bottom, left:right] = 235
    quad = detect_card_quad(frame)
    assert quad is not None
    expected = np.array([[left, top], [right, top], [right, bottom], [left, bottom]], np.float32)
    assert np.allclose(quad, expected, atol=12)


def _rounded_card_frame(corners: np.ndarray) -> np.ndarray:
    """Draw an anti-aliased card with rounded corners at sub-pixel corners."""
    frame = np.full((800, 600, 3), 40, dtype=np.uint8)
    shift = 4  # cv2 fixed-point bits: corners are drawn at 1/16 px precision
    cv2.fillConvexPoly(
        frame, np.round(corners * 16).astype(np.int32), (225, 225, 225), cv2.LINE_AA, shift
    )
    for corner in corners:
        # Knock the sharp corner off, as a real card's rounded corner does.
        cv2.circle(frame, tuple(int(v) for v in np.round(corner)), 9, (40, 40, 40), -1, cv2.LINE_AA)
    return frame


def test_refinement_recovers_the_corners_of_a_rounded_card() -> None:
    theta = np.deg2rad(12)
    rotation = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    upright = np.array([[-150.3, -210.6], [150.2, -210.6], [150.2, 210.4], [-150.3, 210.4]])
    truth = (upright @ rotation.T + [300.4, 400.7]).astype(np.float32)
    quad = detect_card_quad(_rounded_card_frame(truth))
    assert quad is not None
    # The contour quad is cut short by the rounded corners (8-10 px off here);
    # the line refit extrapolates the straight sides to the true corners, up to
    # the half-pixel bias of locating an anti-aliased step by its gradient peak.
    assert float(np.max(np.linalg.norm(quad - truth, axis=1))) < 1.5


def test_detects_a_card_lying_sideways() -> None:
    frame = np.full((600, 800, 3), 30, dtype=np.uint8)
    frame[150:450, 190:610] = 235  # 420 wide x 300 tall: a portrait card turned 90°
    quad = detect_card_quad(frame)
    assert quad is not None
    assert is_sideways(quad)
    card = rectify_or_whole(frame, quad)
    assert card.shape == (constants.CARD_HEIGHT_PX, constants.CARD_WIDTH_PX, 3)


def test_rectify_or_whole_without_quad_returns_canonical() -> None:
    card = rectify_or_whole(np.full((880, 630, 3), 100, dtype=np.uint8), None)
    assert card.shape == (constants.CARD_HEIGHT_PX, constants.CARD_WIDTH_PX, 3)


def test_rectify_or_whole_recovers_inner_guide() -> None:
    frame = np.full((130, 91, 3), (220, 20, 20), dtype=np.uint8)
    frame[15:115, 10:80] = (20, 220, 20)
    card = rectify_or_whole(frame, None, guide_margin=0.15)
    assert card.shape == (constants.CARD_HEIGHT_PX, constants.CARD_WIDTH_PX, 3)
    assert np.all(card == (20, 220, 20))


def test_rectify_or_whole_with_quad_returns_canonical() -> None:
    frame = _frame_with_card()
    quad = detect_card_quad(frame)
    card = rectify_or_whole(frame, quad)
    assert card.shape == (constants.CARD_HEIGHT_PX, constants.CARD_WIDTH_PX, 3)
