"""Card detection: find the card's quadrilateral in a photo or frame.

A classic contour approach — blur, edge-detect, find the largest convex quad with
a plausible card size and aspect. It handles the common case (a card on a
contrasting surface) without any trained model, which keeps new sets a pure data
operation. When nothing card-like is found the caller falls back to treating the
whole (already-cropped) image as the card.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

from app.core import constants
from app.vision import geometry
from app.vision.imaging import cap_long_side, to_gray
from app.vision.refine import refine_quad

logger = logging.getLogger(__name__)

_MAX_CONTOURS = 10
# approxPolyDP tolerances (fractions of the hull perimeter) tried on a
# contour's convex hull when the raw contour is not a clean 4-gon. Real cards
# have rounded corners, glare nicks, and finger overlaps that fragment the raw
# outline; the hull with a progressively looser fit still recovers the quad.
_HULL_EPSILONS = (0.02, 0.04, 0.08)


def detect_card_quad(image_rgb: np.ndarray) -> np.ndarray | None:
    """Detect the most card-like quadrilateral in an image.

    Args:
        image_rgb: RGB image to search.

    Returns:
        A ``(4, 2)`` ``float32`` array of corner points (TL, TR, BR, BL), or
        ``None`` when no plausible card quad is found.
    """
    # Edge detection needs contrast, not resolution: work on a capped copy and
    # scale the quad back so rectification still samples the original pixels.
    working = cap_long_side(image_rgb, constants.DETECT_MAX_SIDE)
    scale = image_rgb.shape[1] / float(working.shape[1])
    height, width = working.shape[:2]
    frame_area = float(height * width)
    gray = to_gray(working)
    blurred = cv2.bilateralFilter(gray, 9, 75, 75)
    # Auto-Canny: thresholds follow the scene's median brightness so dim or
    # washed-out frames still produce card edges (fixed 50/150 missed them).
    median = float(np.median(np.asarray(blurred, dtype=np.uint8)))
    lower = int(max(0.0, constants.CANNY_MEDIAN_LO * median))
    upper = int(min(255.0, constants.CANNY_MEDIAN_HI * median))
    edged = cv2.Canny(blurred, lower, upper)
    edged = cv2.dilate(edged, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    ranked = sorted(contours, key=cv2.contourArea, reverse=True)[:_MAX_CONTOURS]
    for contour in ranked:
        quad = _quad_from_contour(contour)
        if quad is not None and geometry.is_plausible_card(quad, frame_area):
            quad = refine_quad(gray, quad)
            return geometry.order_corners(quad * scale)
    logger.debug(
        "no plausible card quad among %d contours (working frame %dx%d, canny %d/%d)",
        len(ranked),
        width,
        height,
        lower,
        upper,
    )
    return None


def _quad_from_contour(contour: np.ndarray) -> np.ndarray | None:
    """Extract a convex 4-gon from a contour, tolerating ragged outlines.

    Args:
        contour: One OpenCV contour.

    Returns:
        A ``(4, 2)`` ``float32`` quad, or ``None`` when the contour cannot be
        reduced to one.
    """
    perimeter = cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
    if len(approx) == 4 and cv2.isContourConvex(approx):
        return approx.reshape(4, 2).astype(np.float32)
    # The raw outline was not a clean quad (rounded corners, glare nicks,
    # fingers crossing an edge). Its convex hull smooths those out; a looser
    # fit on the hull usually still lands on the card's four corners.
    hull = cv2.convexHull(contour)
    hull_perimeter = cv2.arcLength(hull, True)
    for epsilon in _HULL_EPSILONS:
        approx = cv2.approxPolyDP(hull, epsilon * hull_perimeter, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            return approx.reshape(4, 2).astype(np.float32)
    return None
