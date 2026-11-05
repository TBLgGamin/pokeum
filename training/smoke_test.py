"""Staged end-to-end smoke test of the training harness on the box.

Proves the plumbing — fetch, cache, model load, train, eval, export, parity —
on a tiny dataset in well under ten minutes. It deliberately does NOT assert
retrieval improvement: two epochs on ~100 cards prove the pipes, not learning.

Each stage prints ``[stage] PASS``/``[stage] FAIL: reason``; the first failure
aborts with a nonzero exit code.

Usage (on the training box)::

    python -m training.smoke_test
"""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

SMOKE_DIR = Path("smoke_data")
SMOKE_RUN = Path("runs/smoke")
# Small sets that actually carry image URLs on TCGdex (many old promo sets do
# not): Detective Pikachu (18), Celebrations Classic (24), Futsal 2020 (5).
SMOKE_SETS = "fut2020,det1,cel25"
MIN_IMAGES = 12
SMOKE_EPOCHS = 4
LOSS_IMPROVEMENT = 0.9  # final epoch mean loss must be < this x first epoch's


def _fail(stage: str, reason: str) -> int:
    """Print a stage failure and return the exit code."""
    sys.stdout.write(f"[{stage}] FAIL: {reason}\n")
    return 1


def _ok(stage: str, detail: str = "") -> None:
    """Print a stage pass."""
    suffix = f" ({detail})" if detail else ""
    sys.stdout.write(f"[{stage}] PASS{suffix}\n")
    sys.stdout.flush()


def run() -> int:
    """Run all smoke stages; return a process exit code."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if SMOKE_RUN.exists():
        shutil.rmtree(SMOKE_RUN)

    # Stage 1: fetch tiny sets.
    from training.fetch_data import main as fetch_main

    if fetch_main(["--out", str(SMOKE_DIR), "--sets", SMOKE_SETS]) != 0:
        return _fail("fetch", "fetcher returned nonzero")
    image_count = len(list((SMOKE_DIR / "images").glob("*")))
    if image_count < MIN_IMAGES:
        return _fail("fetch", f"only {image_count} images (< {MIN_IMAGES})")
    _ok("fetch", f"{image_count} images")

    # Stage 2: build the cache blob.
    from training.cache import build_cache, load_cache

    cached = build_cache(SMOKE_DIR)
    card_ids, images = load_cache(SMOKE_DIR)
    if cached < MIN_IMAGES or len(card_ids) != images.shape[0]:
        return _fail("cache", f"cached {cached}, ids {len(card_ids)}, rows {images.shape[0]}")
    _ok("cache", f"{cached} cards, shape {images.shape}")

    # Stage 3: load the model (front-loads the torch.hub download).
    import torch

    from training.config import TrainConfig
    from training.model import FineTuneModel

    if not torch.cuda.is_available():
        return _fail("model", "CUDA not available")
    probe_cfg = TrainConfig(freeze_blocks=4)
    model = FineTuneModel(probe_cfg).to("cuda")
    with torch.no_grad():
        probe = model.features(torch.zeros(1, 3, 224, 224, device="cuda"))
    feature_dim = model.feature_dim
    if tuple(probe.shape) != (1, feature_dim):
        return _fail("model", f"probe shape {tuple(probe.shape)} != (1, {feature_dim})")
    del model
    torch.cuda.empty_cache()
    _ok("model", f"{probe_cfg.backbone} -> (1, {feature_dim})")

    # Stage 4: two training epochs on the tiny set.
    from training.train import train

    cfg = TrainConfig(
        data_dir=str(SMOKE_DIR),
        run_dir=str(SMOKE_RUN),
        batch_cards=8,
        epochs=SMOKE_EPOCHS,
        eval_every_epochs=SMOKE_EPOCHS,
        eval_queries=len(card_ids),
        warmup_steps=2,
    )
    try:
        train(cfg)
    except Exception as error:  # noqa: BLE001 - smoke test reports, not raises
        return _fail("train", f"{type(error).__name__}: {error}")
    losses = _epoch_losses(SMOKE_RUN / "metrics.csv")
    if not losses:
        return _fail("train", "no loss rows in metrics.csv")
    if losses[-1] != losses[-1] or losses[-1] >= losses[0] * LOSS_IMPROVEMENT:
        return _fail(
            "train", f"loss {losses[0]:.3f} -> {losses[-1]:.3f} (needs <{LOSS_IMPROVEMENT}x)"
        )
    checkpoint = SMOKE_RUN / "last.pt"
    if not checkpoint.is_file():
        return _fail("train", "no checkpoint written")
    _ok("train", f"loss {losses[0]:.3f} -> {losses[-1]:.3f}")

    # Stage 5: ONNX export.
    from training.export_onnx import export

    onnx_path = SMOKE_RUN / "smoke.onnx"
    try:
        backbone = export(checkpoint, onnx_path)
    except Exception as error:  # noqa: BLE001
        return _fail("export", f"{type(error).__name__}: {error}")
    if not onnx_path.is_file() or onnx_path.stat().st_size < 1e6:
        return _fail("export", "artifact missing or implausibly small")
    _ok("export", f"{onnx_path.stat().st_size / 1e6:.1f} MB")

    # Stage 6: torch-vs-onnxruntime parity.
    from training.export_onnx import parity_check

    if not parity_check(backbone, onnx_path, SMOKE_DIR):
        return _fail("parity", "min cosine below threshold")
    _ok("parity")

    sys.stdout.write("SMOKE TEST PASSED\n")
    return 0


def _epoch_losses(csv_path: Path) -> list[float]:
    """Return the loss column values from metrics.csv, in order."""
    import csv as csv_module

    losses: list[float] = []
    with open(csv_path, encoding="utf-8") as handle:
        for row in csv_module.DictReader(handle):
            if row.get("loss"):
                try:
                    losses.append(float(row["loss"]))
                except ValueError:
                    continue
    return losses


def main() -> int:
    """Entry point."""
    return run()


if __name__ == "__main__":
    sys.exit(main())
