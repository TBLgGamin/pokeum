"""Fractional region crops on the rectified card.

Every crop is expressed as a fraction of the canonical card (see
:mod:`app.core.constants`), so the same box works regardless of the pixel size a
card was rectified to. These crops feed the signals: the artwork window feeds the
embedding, the bottom strip feeds OCR, the symbol zone feeds symbol matching, and
the variant boxes feed the rule-based variant checks.
"""

from __future__ import annotations

import numpy as np

from app.core import constants
from app.core.constants import ERA_WOTC, FractionBox


def crop_fraction(image: np.ndarray, box: FractionBox) -> np.ndarray:
    """Crop a fractional box ``(left, top, right, bottom)`` from an image.

    Args:
        image: Image array of shape ``(H, W, ...)``.
        box: Fractions in ``[0, 1]`` of width/height.

    Returns:
        The cropped sub-image (a view where possible). At least a 1x1 region is
        returned even for a degenerate box.
    """
    height, width = image.shape[:2]
    left, top, right, bottom = box
    x0 = max(0, min(width - 1, int(round(left * width))))
    x1 = max(x0 + 1, min(width, int(round(right * width))))
    y0 = max(0, min(height - 1, int(round(top * height))))
    y1 = max(y0 + 1, min(height, int(round(bottom * height))))
    return image[y0:y1, x0:x1]


def artwork(card: np.ndarray) -> np.ndarray:
    """Return the upper-central artwork window of a rectified card."""
    return crop_fraction(card, constants.ARTWORK_BOX)


def bottom_strip(card: np.ndarray) -> np.ndarray:
    """Return the bottom strip carrying the collector number and set code."""
    return crop_fraction(card, constants.BOTTOM_STRIP_BOX)


def title_band(card: np.ndarray) -> np.ndarray:
    """Return the top band carrying the card's printed name (and stage/HP)."""
    return crop_fraction(card, constants.TITLE_BAND_BOX)


def symbol_zone(card: np.ndarray, era: str) -> np.ndarray:
    """Return the set-symbol region for the given era's layout."""
    box = constants.SYMBOL_ZONE_WOTC if era == ERA_WOTC else constants.SYMBOL_ZONE_MODERN
    return crop_fraction(card, box)


def reverse_holo_body(card: np.ndarray) -> np.ndarray:
    """Return the card-body region used to judge reverse-holo foil texture."""
    return crop_fraction(card, constants.REVERSE_HOLO_BODY_BOX)


def first_edition_stamp(card: np.ndarray) -> np.ndarray:
    """Return the lower-left art region where a WOTC 1st Edition stamp sits."""
    return crop_fraction(card, constants.FIRST_EDITION_STAMP_BOX)


def art_frame_right(card: np.ndarray) -> np.ndarray:
    """Return the right edge of the art frame, where a drop shadow appears."""
    return crop_fraction(card, constants.ART_FRAME_RIGHT_BOX)


def promo_stamp(card: np.ndarray) -> np.ndarray:
    """Return the lower-left artwork region where promo stamps commonly appear."""
    return crop_fraction(card, constants.PROMO_STAMP_BOX)
