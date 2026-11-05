"""Generate a synthetic photo-damage eval set from cached reference images.

Takes clean card renders from ``data/images/`` and produces distorted copies —
perspective jitter, brightness/contrast shift, a glare blob, blur, and JPEG
re-compression — named ``{card_id}__distorted.jpg`` so ``pokeum eval`` can score
them. Deterministic for a given seed, and implemented with PIL only (independent
of the training harness's torch augmentations, so it is not the model grading
its own homework).

``--scene`` additionally places each damaged card on a cluttered background at
a random scale and in-plane rotation (a quarter of them upside down or
sideways), so the set exercises detection and orientation handling rather than
only the signals; files are then named ``{card_id}__scene.jpg``.

Usage::

    python scripts/make_synthetic_eval.py --out data/eval/synthetic --count 20
    python scripts/make_synthetic_eval.py --out data/eval/scene --count 150 --scene
"""

from __future__ import annotations

import argparse
import io
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


def _perspective_coeffs(
    src: list[tuple[float, float]], dst: list[tuple[float, float]]
) -> list[float]:
    """Return PIL PERSPECTIVE transform coefficients mapping ``src`` to ``dst``."""
    matrix = []
    vector = []
    for (x, y), (u, v) in zip(src, dst, strict=True):
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        vector += [u, v]
    solution = np.linalg.solve(np.array(matrix, dtype=float), np.array(vector, dtype=float))
    return [float(c) for c in solution]


def distort(img: Image.Image, rng: random.Random, *, perspective: bool = True) -> Image.Image:
    """Apply photo-like damage to one clean card render.

    Args:
        img: The clean RGB card image.
        rng: Seeded generator driving every random choice.
        perspective: Jitter the corners. Scene mode turns this off: the jitter
            leaves black wedges at the corners, which would hand the detector a
            fake rectangular silhouette once the card sits on a background.

    Returns:
        The distorted image (post JPEG round-trip).
    """
    width, height = img.size
    if perspective:
        jitter = 0.05
        corners = [(0, 0), (width, 0), (width, height), (0, height)]
        moved = [
            (x + rng.uniform(-jitter, jitter) * width, y + rng.uniform(-jitter, jitter) * height)
            for x, y in corners
        ]
        img = img.transform(
            (width, height), Image.PERSPECTIVE, _perspective_coeffs(moved, corners), Image.BICUBIC
        )
    img = ImageEnhance.Brightness(img).enhance(rng.uniform(0.6, 1.25))
    img = ImageEnhance.Contrast(img).enhance(rng.uniform(0.75, 1.2))

    glare = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(glare)
    cx = rng.uniform(0.2, 0.8) * width
    cy = rng.uniform(0.2, 0.8) * height
    radius = rng.uniform(0.15, 0.4) * width
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=140)
    glare = glare.filter(ImageFilter.GaussianBlur(radius / 2))
    img = Image.composite(Image.new("RGB", (width, height), (255, 255, 255)), img, glare)

    img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.5, 1.8)))
    buffer = io.BytesIO()
    img.save(buffer, "JPEG", quality=rng.randint(45, 75))
    return Image.open(io.BytesIO(buffer.getvalue())).convert("RGB")


def _background(size: tuple[int, int], rng: random.Random) -> Image.Image:
    """Return a noisy, blotchy table-like background (no card-shaped edges)."""
    width, height = size
    base = tuple(rng.randint(40, 200) for _ in range(3))
    noise = np.random.default_rng(rng.randint(0, 2**31)).normal(0, 18, (height, width, 3))
    array = np.clip(np.array(base, dtype=float) + noise, 0, 255).astype(np.uint8)
    canvas = Image.fromarray(array).filter(ImageFilter.GaussianBlur(1.5))
    draw = ImageDraw.Draw(canvas)
    for _ in range(rng.randint(3, 8)):
        x, y = rng.uniform(0, width), rng.uniform(0, height)
        radius = rng.uniform(0.03, 0.15) * width
        color = tuple(rng.randint(0, 255) for _ in range(3))
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=color)
    return canvas.filter(ImageFilter.GaussianBlur(4))


def place_in_scene(card: Image.Image, rng: random.Random) -> Image.Image:
    """Paste a (damaged) card onto a background at a random pose.

    Rotation is mostly a hand-held tilt (±20°); a quarter of the cards are
    instead turned by 90°, 180° or 270° plus tilt, the cases a fixed-orientation
    pipeline gets wrong. The card fills 35-65% of the frame's short side.
    """
    frame = (960, 1280) if rng.random() < 0.5 else (1280, 960)
    canvas = _background(frame, rng)
    if rng.random() < 0.25:
        angle = rng.choice([90, 180, 270]) + rng.uniform(-10, 10)
    else:
        angle = rng.uniform(-20, 20)
    target_h = rng.uniform(0.35, 0.65) * min(frame) / 0.716
    scale = min(target_h, min(frame) * 0.95) / card.height
    card = card.resize((round(card.width * scale), round(card.height * scale)), Image.BICUBIC)
    rotated = card.convert("RGBA").rotate(angle, expand=True, resample=Image.BICUBIC)
    max_x = max(0, frame[0] - rotated.width)
    max_y = max(0, frame[1] - rotated.height)
    canvas.paste(rotated, (rng.randint(0, max_x), rng.randint(0, max_y)), rotated)
    buffer = io.BytesIO()
    canvas.save(buffer, "JPEG", quality=rng.randint(60, 85))
    return Image.open(io.BytesIO(buffer.getvalue())).convert("RGB")


def main() -> None:
    """Parse arguments and write the distorted eval set."""
    parser = argparse.ArgumentParser(description="Generate a synthetic eval set")
    parser.add_argument("--images", default="data/images", help="cached reference images root")
    parser.add_argument("--out", default="data/eval/synthetic", help="output directory")
    parser.add_argument("--count", type=int, default=20, help="number of cards to distort")
    parser.add_argument("--seed", type=int, default=7, help="random seed")
    parser.add_argument(
        "--scene", action="store_true", help="place cards on a background at random poses"
    )
    args = parser.parse_args()

    rng = random.Random(args.seed)
    root = Path(args.images)
    paths = sorted(p for p in root.glob("*/*") if p.suffix in {".png", ".webp", ".jpg"})
    rng.shuffle(paths)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "scene" if args.scene else "distorted"
    for path in paths[: args.count]:
        image = distort(Image.open(path).convert("RGB"), rng, perspective=not args.scene)
        if args.scene:
            image = place_in_scene(image, rng)
        image.save(out_dir / f"{path.stem}__{suffix}.jpg", quality=90)
    print(f"wrote {min(args.count, len(paths))} {suffix} images to {out_dir}")


if __name__ == "__main__":
    main()
