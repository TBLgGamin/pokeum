"""Retrieval evaluation mirroring the deployed task.

Gallery = clean-render backbone embeddings of every cached card. Queries = a
fixed subset (``eval_queries`` cards, ``eval_seed``) pushed through the heavy
photo-simulation stack with a dedicated reseeded generator and a fixed batch
size, so every eval scores bit-identical distorted queries — comparable across
epochs and runs.

Two tracks are scored because one encoder serves two fusion signals:

* **full**: whole-card gallery vs whole-card queries;
* **art**: artwork-crop gallery vs (jittered) artwork-crop queries.

The combined score ``0.5*full_top1 + 0.5*art_top1`` selects the best
checkpoint. Extra diagnostics: mean true-pair cosine, top1-top2 margin, a
collapse monitor (mean off-diagonal gallery cosine), and clean-gallery drift
vs the epoch-0 frozen baseline.

Standalone usage::

    python -m training.evaluate --data training_data --checkpoint runs/ft/best.pt
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

import torch

from training import augment
from training.config import TrainConfig
from training.dataset import CardImages
from training.model import FineTuneModel, preprocess

logger = logging.getLogger(__name__)

EVAL_BATCH = 256


def _embed_all(
    model: FineTuneModel,
    images: CardImages,
    device: torch.device,
    *,
    art: bool,
    heavy_indices: torch.Tensor | None = None,
    cfg: TrainConfig | None = None,
) -> torch.Tensor:
    """Embed cards with backbone features, optionally cropped/augmented.

    Args:
        model: The model (already in eval mode).
        images: The cached catalogue.
        device: Compute device.
        art: Crop the artwork window before embedding.
        heavy_indices: When given, embed only these rows and push them through
            the heavy augmentation stack with a deterministic generator.
        cfg: Required when ``heavy_indices`` is given (aug parameters + seed).

    Returns:
        ``(N, D)`` L2-normalized float32 embeddings on ``device``.
    """
    if heavy_indices is not None:
        assert cfg is not None
        rows = heavy_indices
        gen = torch.Generator().manual_seed(cfg.eval_seed)
    else:
        rows = torch.arange(len(images))
        gen = torch.Generator().manual_seed(0)  # art-crop jitter is disabled below anyway

    chunks: list[torch.Tensor] = []
    for start in range(0, len(rows), EVAL_BATCH):
        idx = rows[start : start + EVAL_BATCH]
        batch = images.batch(idx, device)
        if heavy_indices is not None and cfg is not None:
            batch = augment.heavy_view(batch, cfg.aug, gen)
            if art:
                batch = augment.artwork_crop(batch, cfg.aug.art_crop_jitter, gen)
        elif art:
            batch = augment.artwork_crop(batch, 0.0, gen)
        with torch.autocast(
            device_type=device.type, dtype=torch.bfloat16, enabled=device.type == "cuda"
        ):
            feats = model.features(preprocess(batch))
        chunks.append(torch.nn.functional.normalize(feats.float(), dim=-1))
    return torch.cat(chunks) if chunks else torch.zeros((0, 1), device=device)


def _retrieval_metrics(
    gallery: torch.Tensor, queries: torch.Tensor, query_rows: torch.Tensor
) -> dict[str, float]:
    """Score top-1/top-5 retrieval of queries against the gallery."""
    sims = queries @ gallery.t()  # (Q, N)
    top5 = sims.topk(min(5, gallery.shape[0]), dim=1).indices
    truth = query_rows.to(sims.device).view(-1, 1)
    top1_acc = (top5[:, :1] == truth).any(dim=1).float().mean().item()
    top5_acc = (top5 == truth).any(dim=1).float().mean().item()
    true_cos = sims.gather(1, truth).mean().item()
    two = sims.topk(2, dim=1).values
    margin = (two[:, 0] - two[:, 1]).mean().item()
    return {
        "top1": round(top1_acc, 4),
        "top5": round(top5_acc, 4),
        "true_cos": round(true_cos, 4),
        "margin": round(margin, 4),
    }


def evaluate(
    model: FineTuneModel,
    images: CardImages,
    cfg: TrainConfig,
    device: torch.device,
    baseline_gallery: torch.Tensor | None = None,
) -> dict[str, float]:
    """Run the full two-track retrieval eval.

    Args:
        model: Model to evaluate (switched to eval mode here).
        images: Cached catalogue.
        cfg: Training configuration (eval subset size/seed, aug ranges).
        device: Compute device.
        baseline_gallery: Epoch-0 clean full-card gallery for the drift report.

    Returns:
        Flat metric dict: ``full_top1/full_top5/art_top1/art_top5/combined``,
        diagnostics, and ``clean_drift`` when a baseline is given.
    """
    was_training = model.training
    model.eval()
    query_count = min(cfg.eval_queries, len(images))
    query_rows = torch.randperm(
        len(images), generator=torch.Generator().manual_seed(cfg.eval_seed)
    )[:query_count]

    with torch.no_grad():
        gallery_full = _embed_all(model, images, device, art=False)
        queries_full = _embed_all(
            model, images, device, art=False, heavy_indices=query_rows, cfg=cfg
        )
        full = _retrieval_metrics(gallery_full, queries_full, query_rows)

        gallery_art = _embed_all(model, images, device, art=True)
        queries_art = _embed_all(model, images, device, art=True, heavy_indices=query_rows, cfg=cfg)
        art = _retrieval_metrics(gallery_art, queries_art, query_rows)

        sample = gallery_full[: min(1000, gallery_full.shape[0])]
        off_diag = sample @ sample.t()
        off_diag.fill_diagonal_(0)
        n = off_diag.shape[0]
        collapse = (off_diag.sum() / max(1, n * (n - 1))).item()

        metrics: dict[str, float] = {
            "full_top1": full["top1"],
            "full_top5": full["top5"],
            "full_margin": full["margin"],
            "art_top1": art["top1"],
            "art_top5": art["top5"],
            "art_margin": art["margin"],
            "combined": round(0.5 * full["top1"] + 0.5 * art["top1"], 4),
            "gallery_mean_cos": round(collapse, 4),
        }
        if baseline_gallery is not None and baseline_gallery.shape == gallery_full.shape:
            drift = (gallery_full * baseline_gallery).sum(dim=1).mean().item()
            metrics["clean_drift_cos"] = round(drift, 4)

    if collapse > cfg.collapse_warn_cosine:
        logger.warning("possible embedding collapse: mean gallery cosine %.3f", collapse)
    if was_training:
        model.train()
    return metrics


def embed_clean_gallery(
    model: FineTuneModel, images: CardImages, device: torch.device
) -> torch.Tensor:
    """Return the clean full-card gallery (used to pin the epoch-0 baseline)."""
    model.eval()
    with torch.no_grad():
        return _embed_all(model, images, device, art=False)


def main(argv: list[str] | None = None) -> int:
    """Standalone eval of a checkpoint."""
    parser = argparse.ArgumentParser(description="Evaluate a fine-tune checkpoint")
    parser.add_argument("--data", default="training_data")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    device = torch.device(args.device)
    state = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    raw_config = state.get("config")
    cfg = TrainConfig.from_dict(raw_config) if isinstance(raw_config, dict) else TrainConfig()
    cfg.device = args.device
    model = FineTuneModel(cfg).to(device)
    model.load_state_dict(state["model"])
    images = CardImages.load(args.data)
    metrics = evaluate(model, images, cfg, device)
    sys.stdout.write(json.dumps(metrics, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
