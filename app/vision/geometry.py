"""Pure geometric helpers for card detection and rectification.

Kept free of OpenCV so the corner ordering, area, and plausibility maths can be
unit-tested with plain NumPy arrays. Everything here operates on a quadrilateral
given as a ``(4, 2)`` array of ``(x, y)`` points.
"""

from __future__ import annotations

import numpy as np

from app.core import constants


def order_corners(points: np.ndarray) -> np.ndarray:
    """Order a quad's corners as TL, TR, BR, BL of a *portrait* card.

    The corners are walked clockwise around their centroid, then the start is
    chosen so the first edge is a short side: a card lying sideways is
    normalised to portrait instead of being warped into a squashed landscape.
    Of the two short sides, the one higher in the image becomes the top, so an
    upright or tilted card keeps its orientation; a card that is genuinely
    upside down comes out rotated 180 degrees, which the recognizer resolves by
    scoring both orientations.

    Args:
        points: A ``(4, 2)`` array of corner coordinates in any order.

    Returns:
        A ``(4, 2)`` ``float32`` array ordered TL, TR, BR, BL.
    """
    pts = np.asarray(points, dtype=np.float32).reshape(4, 2)
    center = pts.mean(axis=0)
    # Image y points down, so increasing atan2 angle walks clockwise on screen.
    clockwise = pts[np.argsort(np.arctan2(pts[:, 1] - center[1], pts[:, 0] - center[0]))]
    edges = np.linalg.norm(np.roll(clockwise, -1, axis=0) - clockwise, axis=1)
    start = 0 if edges[0] + edges[2] <= edges[1] + edges[3] else 1
    top_y = (clockwise[start, 1] + clockwise[(start + 1) % 4, 1]) / 2.0
    bottom_y = (clockwise[(start + 2) % 4, 1] + clockwise[(start + 3) % 4, 1]) / 2.0
    if bottom_y < top_y:
        start += 2
    return np.roll(clockwise, -start, axis=0).astype(np.float32)


def polygon_area(points: np.ndarray) -> float:
    """Return the absolute area of a polygon via the shoelace formula."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    x = pts[:, 0]
    y = pts[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0)


def aspect_ratio(points: np.ndarray) -> float:
    """Return the short-side/long-side ratio of a quad.

    Args:
        points: A ``(4, 2)`` quad; it is corner-ordered internally first.

    Returns:
        Mean top/bottom edge length divided by mean left/right edge length of
        the portrait-normalised quad, so a card scores the same (about 0.716)
        whether it lies upright or sideways.
    """
    tl, tr, br, bl = order_corners(points)
    top = float(np.linalg.norm(tr - tl))
    bottom = float(np.linalg.norm(br - bl))
    left = float(np.linalg.norm(bl - tl))
    right = float(np.linalg.norm(br - tr))
    height = (left + right) / 2.0
    if height <= 0:
        return 0.0
    return (top + bottom) / 2.0 / height


def is_sideways(points: np.ndarray) -> bool:
    """Return whether an ordered quad's top edge runs closer to vertical.

    True for a card lying on its side, and for landscape shapes inside a card
    (the artwork window has nearly the card's aspect ratio turned 90 degrees).
    """
    tl, tr = np.asarray(points, dtype=np.float32).reshape(4, 2)[:2]
    dx, dy = tr - tl
    return bool(abs(dy) > abs(dx))


def is_plausible_card(points: np.ndarray, frame_area: float) -> bool:
    """Return whether a quad looks like a card in a frame of ``frame_area``.

    A quad qualifies when it fills at least :data:`DETECT_MIN_AREA_FRACTION` of
    the frame and its aspect ratio lies within the card band (in either
    orientation: :func:`order_corners` normalises sideways cards to portrait).

    Args:
        points: Candidate quad.
        frame_area: Area of the full image in pixels.

    Returns:
        ``True`` if the quad passes the size, shape, and aspect tests.
    """
    if frame_area <= 0:
        return False
    area_fraction = polygon_area(points) / frame_area
    if area_fraction < constants.DETECT_MIN_AREA_FRACTION:
        return False
    if not _is_rectangle_like(order_corners(points)):
        return False
    ratio = aspect_ratio(points)
    return constants.CARD_ASPECT_MIN <= ratio <= constants.CARD_ASPECT_MAX


def _is_rectangle_like(ordered: np.ndarray) -> bool:
    """Return whether every corner is near-square and opposite sides comparable.

    A card under hand-held perspective stays close to a rectangle; a skewed
    quad hulled from background clutter or a card corner plus glare does not,
    even when its overall aspect happens to fall in the card band.
    """
    pts = np.asarray(ordered, dtype=np.float64).reshape(4, 2)
    sides = np.roll(pts, -1, axis=0) - pts
    lengths = np.linalg.norm(sides, axis=1)
    if np.any(lengths <= 0):
        return False
    for index in range(4):
        incoming, outgoing = -sides[index - 1], sides[index]
        cosine = float(np.dot(incoming, outgoing) / (lengths[index - 1] * lengths[index]))
        if abs(cosine) > constants.DETECT_MAX_CORNER_COSINE:
            return False
    ratio = constants.DETECT_MIN_SIDE_RATIO
    return bool(
        min(lengths[0], lengths[2]) / max(lengths[0], lengths[2]) >= ratio
        and min(lengths[1], lengths[3]) / max(lengths[1], lengths[3]) >= ratio
    )
