"""Sub-pixel refinement of a detected card quad.

``approxPolyDP`` snaps the card outline to contour vertices, which sit on the
dilated Canny edge and round off at the card's rounded corners: the warp then
clips or pads a few pixels along every side, which shifts every fractional crop
box. This module re-fits each side as a straight line through the strongest
gradient found along its normal, and intersects adjacent lines for corners that
are exact to a fraction of a pixel.

The refit is a guarded improvement: when a side has too little edge evidence or
the refined quad moves implausibly far, the input quad is returned unchanged.
"""

from __future__ import annotations

import cv2
import numpy as np

from app.core import constants


def refine_quad(gray: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Refit a quad's four sides to the image gradient.

    Args:
        gray: Single-channel image the quad was detected in.
        quad: ``(4, 2)`` corners in cyclic order (either direction).

    Returns:
        The refined ``(4, 2)`` ``float32`` quad in the same cyclic order, or the
        input quad when the refit is not trustworthy.
    """
    pts = np.asarray(quad, dtype=np.float32).reshape(4, 2)
    smooth = cv2.GaussianBlur(gray, (3, 3), 0)
    grad_x = cv2.Sobel(smooth, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(smooth, cv2.CV_32F, 0, 1, ksize=3)
    lines: list[tuple[np.ndarray, np.ndarray]] = []
    for index in range(4):
        line = _fit_side(grad_x, grad_y, pts[index], pts[(index + 1) % 4])
        if line is None:
            return pts
        lines.append(line)
    refined = np.empty_like(pts)
    for index in range(4):
        corner = _intersect(lines[index - 1], lines[index])
        if corner is None:
            return pts
        refined[index] = corner
    diagonal = float(np.linalg.norm(pts[2] - pts[0]))
    moved = float(np.max(np.linalg.norm(refined - pts, axis=1)))
    if moved > constants.REFINE_MAX_SHIFT_FRACTION * diagonal:
        return pts
    if not cv2.isContourConvex(refined.reshape(4, 1, 2)):
        return pts
    return refined


def _fit_side(
    grad_x: np.ndarray, grad_y: np.ndarray, start: np.ndarray, end: np.ndarray
) -> tuple[np.ndarray, np.ndarray] | None:
    """Fit one side as ``(point, direction)`` through the strongest edge response.

    Samples the side away from its corners (rounded corners bend the outline
    there), searches along the side's normal for the gradient peak, and fits a
    robust line through the sub-pixel peaks.
    """
    vector = end - start
    length = float(np.linalg.norm(vector))
    if length < constants.REFINE_MIN_SIDE_PX:
        return None
    tangent = vector / length
    normal = np.array([-tangent[1], tangent[0]], dtype=np.float32)
    # Gradient strength across this side only: a parallel texture edge inside
    # the artwork does not compete with the card border.
    across = np.abs(grad_x * normal[0] + grad_y * normal[1])
    reach = max(3, round(constants.REFINE_SEARCH_FRACTION * length))
    offsets = np.arange(-reach, reach + 1, dtype=np.float32)
    fractions = np.linspace(0.15, 0.85, constants.REFINE_SAMPLES, dtype=np.float32)
    anchors = start[None, :] + fractions[:, None] * vector[None, :]
    coords = anchors[:, None, :] + offsets[None, :, None] * normal[None, None, :]
    profile = cv2.remap(
        across,
        coords[..., 0].astype(np.float32),
        coords[..., 1].astype(np.float32),
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0.0,
    )
    peaks = np.argmax(profile, axis=1)
    strength = profile[np.arange(len(peaks)), peaks]
    found: list[np.ndarray] = []
    for row, (peak, value) in enumerate(zip(peaks, strength, strict=True)):
        if value < constants.REFINE_MIN_GRADIENT or peak in (0, len(offsets) - 1):
            continue
        left, mid, right = profile[row, peak - 1 : peak + 2]
        denom = left - 2.0 * mid + right
        delta = 0.5 * (left - right) / denom if denom < 0 else 0.0
        found.append(anchors[row] + (offsets[peak] + delta) * normal)
    if len(found) < constants.REFINE_SAMPLES // 2:
        return None
    samples = np.array(found, dtype=np.float32)
    fitted = cv2.fitLine(samples, cv2.DIST_HUBER, 0, 0.01, 0.01)
    vx, vy, x0, y0 = (float(value) for value in np.asarray(fitted).reshape(4))
    # A real border puts the peaks on one straight line; texture or noise
    # scatters them, and a line through scatter is worse than the contour.
    residuals = np.abs((samples[:, 0] - x0) * vy - (samples[:, 1] - y0) * vx)
    if float(np.median(residuals)) > constants.REFINE_MAX_RESIDUAL_PX:
        return None
    return (
        np.array([x0, y0], dtype=np.float32),
        np.array([vx, vy], dtype=np.float32),
    )


def _intersect(
    first: tuple[np.ndarray, np.ndarray], second: tuple[np.ndarray, np.ndarray]
) -> np.ndarray | None:
    """Return the intersection of two ``(point, direction)`` lines, or ``None``."""
    (p, r), (q, s) = first, second
    cross = float(r[0] * s[1] - r[1] * s[0])
    if abs(cross) < 1e-6:
        return None
    t = float((q[0] - p[0]) * s[1] - (q[1] - p[1]) * s[0]) / cross
    return (p + t * r).astype(np.float32)
