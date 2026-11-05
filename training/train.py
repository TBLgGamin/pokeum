"""NT-Xent fine-tune loop for the card embedder (DINOv2-S by default).

Per step: sample a batch of cards, build two views on-GPU — a near-clean
gallery-like view and a heavy photo-simulation view (per card, 60/40 full-card
vs artwork-crop mode, same mode for both views) — and minimize symmetric
NT-Xent between their projections. bf16 autocast, cosine LR with warmup,
layer-wise LR decay, grad clipping.

Built-in guards (see the design in openwiki/training.md):

* epoch-0 eval = the frozen baseline every later eval must beat;
* abort when the combined val metric falls below that baseline (the
  "LR killed the transfer" signature);
* early stop after ``early_stop_evals`` evals without improvement;
* one-shot batch halving on CUDA OOM instead of dying mid-run.

Checkpoints (``last.pt`` every epoch, ``best.pt`` on val improvement) carry
model+optimizer+scheduler+epoch+RNG states and the config, so ``--resume``
continues a run losslessly. Metrics stream to ``<run_dir>/metrics.csv``.

Usage::

    python -m training.train --data training_data --run-dir runs/ft
    python -m training.train --resume runs/ft
    python -m training.train --backbone dinov3_vits16 --backbone-weights dinov3_vits16.pth
"""

from __future__ import annotations

import argparse
import csv
import logging
import math
import random
import sys
from pathlib import Path

import numpy as np
import torch

from training import augment
from training.backbones import BACKBONES
from training.config import TrainConfig
from training.dataset import CardImages, epoch_batches
from training.evaluate import embed_clean_gallery, evaluate
from training.model import FineTuneModel, nt_xent, param_groups, preprocess

logger = logging.getLogger(__name__)


def _seed_everything(seed: int) -> None:
    """Seed python, numpy, and torch."""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def _lr_lambda(cfg: TrainConfig, total_steps: int) -> object:
    """Return the warmup+cosine LR multiplier function."""

    def _fn(step: int) -> float:
        if step < cfg.warmup_steps:
            return (step + 1) / cfg.warmup_steps
        progress = (step - cfg.warmup_steps) / max(1, total_steps - cfg.warmup_steps)
        floor = cfg.min_lr / cfg.backbone_lr
        return floor + (1 - floor) * 0.5 * (1 + math.cos(math.pi * min(1.0, progress)))

    return _fn


def _make_views(
    batch: torch.Tensor, cfg: TrainConfig, gen: torch.Generator
) -> tuple[torch.Tensor, torch.Tensor]:
    """Build the (near-clean, heavy) view pair with per-card crop modes."""
    use_art = (torch.rand((batch.shape[0],), generator=gen) < cfg.art_crop_prob).to(batch.device)
    clean = augment.clean_view(batch, cfg.aug, gen)
    heavy = augment.heavy_view(batch, cfg.aug, gen)
    # Crop mode applies identically to both views of a card; the heavy side gets
    # crop-edge jitter (rectification error), the clean side is exact.
    clean_art = augment.artwork_crop(clean, 0.0, gen)
    heavy_art = augment.artwork_crop(heavy, cfg.aug.art_crop_jitter, gen)
    mask = use_art.view(-1, 1, 1, 1).float()
    return clean * (1 - mask) + clean_art * mask, heavy * (1 - mask) + heavy_art * mask


def _save_checkpoint(
    path: Path,
    model: FineTuneModel,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LambdaLR,
    epoch: int,
    best_combined: float,
    baseline_combined: float,
    cfg: TrainConfig,
) -> None:
    """Write a resumable checkpoint."""
    import dataclasses

    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "model": model.state_dict(),
            "optimizer": optimizer.state_dict(),
            "scheduler": scheduler.state_dict(),
            "epoch": epoch,
            "best_combined": best_combined,
            "baseline_combined": baseline_combined,
            "torch_rng": torch.get_rng_state(),
            "config": dataclasses.asdict(cfg),
        },
        path,
    )


def train(cfg: TrainConfig, resume_dir: str | None = None) -> dict[str, float]:
    """Run the fine-tune to completion (or early stop / abort).

    Args:
        cfg: The training configuration.
        resume_dir: Run directory holding ``last.pt`` to resume from.

    Returns:
        The best eval metrics observed.
    """
    _seed_everything(cfg.seed)
    device = torch.device(cfg.device)
    run_dir = Path(resume_dir or cfg.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg.save(run_dir / "config.json")

    images = CardImages.load(cfg.data_dir)
    model = FineTuneModel(cfg).to(device)
    optimizer = torch.optim.AdamW(param_groups(model, cfg), betas=(0.9, 0.999))
    steps_per_epoch = max(1, len(images) // cfg.batch_cards)
    scheduler = torch.optim.lr_scheduler.LambdaLR(
        optimizer, _lr_lambda(cfg, steps_per_epoch * cfg.epochs)
    )

    start_epoch = 0
    best_combined = -1.0
    baseline_combined = -1.0
    last_path = run_dir / "last.pt"
    if resume_dir and last_path.is_file():
        state = torch.load(last_path, map_location="cpu", weights_only=False)
        model.load_state_dict(state["model"])
        optimizer.load_state_dict(state["optimizer"])
        scheduler.load_state_dict(state["scheduler"])
        start_epoch = int(state["epoch"]) + 1
        best_combined = float(state.get("best_combined", -1.0))
        baseline_combined = float(state.get("baseline_combined", -1.0))
        torch.set_rng_state(state["torch_rng"])
        logger.info("resumed from epoch %d", start_epoch)

    csv_path = run_dir / "metrics.csv"
    csv_new = not csv_path.exists()
    csv_file = open(csv_path, "a", newline="", encoding="utf-8")
    writer = csv.writer(csv_file)
    if csv_new:
        writer.writerow(["epoch", "step", "loss", "lr", "eval_json"])

    # Epoch-0 frozen baseline: the number the fine-tune must beat, and the
    # floor for the abort guard.
    baseline_gallery: torch.Tensor | None = None
    if start_epoch == 0:
        baseline = evaluate(model, images, cfg, device)
        baseline_combined = baseline["combined"]
        baseline_gallery = embed_clean_gallery(model, images, device)
        logger.info("frozen baseline: %s", baseline)
        writer.writerow([0, 0, "", "", str(baseline)])
        csv_file.flush()

    data_gen = torch.Generator().manual_seed(cfg.seed + start_epoch)
    aug_gen = torch.Generator().manual_seed(cfg.seed * 7 + 1 + start_epoch)
    evals_since_best = 0
    batch_cards = cfg.batch_cards
    global_step = start_epoch * steps_per_epoch
    best_metrics: dict[str, float] = {}

    model.train()
    for epoch in range(start_epoch, cfg.epochs):
        epoch_loss = 0.0
        batches = 0
        for indices in epoch_batches(len(images), batch_cards, data_gen):
            try:
                batch = images.batch(indices, device)
                view_a, view_b = _make_views(batch, cfg, aug_gen)
                with torch.autocast(device_type=device.type, dtype=torch.bfloat16):
                    za = model(preprocess(view_a))
                    zb = model(preprocess(view_b))
                    loss = nt_xent(za.float(), zb.float(), cfg.temperature)
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.grad_clip)
                optimizer.step()
                scheduler.step()
            except torch.cuda.OutOfMemoryError:
                if batch_cards <= cfg.batch_cards // 2:
                    raise
                batch_cards = cfg.batch_cards // 2
                torch.cuda.empty_cache()
                logger.critical("CUDA OOM: halving batch to %d and continuing", batch_cards)
                continue

            if not torch.isfinite(loss):
                logger.critical("non-finite loss at step %d; aborting", global_step)
                raise RuntimeError("non-finite loss")
            epoch_loss += loss.item()
            batches += 1
            global_step += 1
            if global_step % 10 == 0:
                lr_now = optimizer.param_groups[0]["lr"]
                logger.info(
                    "epoch %d step %d loss %.4f lr %.2e", epoch, global_step, loss.item(), lr_now
                )
                writer.writerow([epoch, global_step, f"{loss.item():.4f}", f"{lr_now:.3e}", ""])
                csv_file.flush()

        mean_loss = epoch_loss / max(1, batches)
        logger.info("epoch %d done: mean loss %.4f", epoch, mean_loss)
        writer.writerow([epoch, global_step, f"{mean_loss:.4f}", "", ""])
        csv_file.flush()

        if (epoch + 1) % cfg.eval_every_epochs == 0 or epoch == cfg.epochs - 1:
            metrics = evaluate(model, images, cfg, device, baseline_gallery)
            logger.info("epoch %d eval: %s", epoch, metrics)
            writer.writerow([epoch, global_step, f"{mean_loss:.4f}", "", str(metrics)])
            csv_file.flush()
            if baseline_combined >= 0 and metrics["combined"] < baseline_combined:
                logger.critical(
                    "val combined %.4f fell below frozen baseline %.4f - LR likely "
                    "destroyed the pretrained transfer; aborting (best.pt preserved)",
                    metrics["combined"],
                    baseline_combined,
                )
                break
            if metrics["combined"] > best_combined:
                best_combined = metrics["combined"]
                best_metrics = metrics
                evals_since_best = 0
                _save_checkpoint(
                    run_dir / "best.pt",
                    model,
                    optimizer,
                    scheduler,
                    epoch,
                    best_combined,
                    baseline_combined,
                    cfg,
                )
                logger.info("new best combined %.4f -> best.pt", best_combined)
            else:
                evals_since_best += 1
                if evals_since_best >= cfg.early_stop_evals:
                    logger.info("early stop: %d evals without improvement", evals_since_best)
                    _save_checkpoint(
                        last_path,
                        model,
                        optimizer,
                        scheduler,
                        epoch,
                        best_combined,
                        baseline_combined,
                        cfg,
                    )
                    break

        _save_checkpoint(
            last_path, model, optimizer, scheduler, epoch, best_combined, baseline_combined, cfg
        )

    csv_file.close()
    logger.info(
        "training done: best combined %.4f (baseline %.4f)", best_combined, baseline_combined
    )
    return best_metrics


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run training."""
    parser = argparse.ArgumentParser(description="Fine-tune the card embedder")
    parser.add_argument("--data", default="training_data")
    parser.add_argument("--run-dir", default="runs/ft")
    parser.add_argument("--resume", default=None, help="run dir with last.pt to resume")
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch-cards", type=int, default=None)
    parser.add_argument("--eval-queries", type=int, default=None)
    parser.add_argument("--device", default="cuda")
    parser.add_argument(
        "--backbone",
        choices=sorted(BACKBONES),
        default=None,
        help=f"torch.hub encoder to fine-tune (default: {TrainConfig.backbone})",
    )
    parser.add_argument(
        "--backbone-weights",
        default=None,
        help="local .pth for gated backbones (DINOv3)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.resume:
        cfg = TrainConfig.load(Path(args.resume) / "config.json")
    else:
        cfg = TrainConfig(data_dir=args.data, run_dir=args.run_dir, device=args.device)
    if args.epochs is not None:
        cfg.epochs = args.epochs
    if args.batch_cards is not None:
        cfg.batch_cards = args.batch_cards
    if args.eval_queries is not None:
        cfg.eval_queries = args.eval_queries
    if args.backbone is not None:
        cfg.backbone = args.backbone
    if args.backbone_weights is not None:
        cfg.backbone_weights = args.backbone_weights

    train(cfg, resume_dir=args.resume)
    return 0


if __name__ == "__main__":
    sys.exit(main())
