"""Configuration for the one-time embedder fine-tune.

One dataclass holds every knob of the run — data paths, view construction,
augmentation probabilities, optimization, and eval — so a run's exact recipe is
serializable as JSON next to its checkpoints and reproducible later.

Design summary (why these defaults):

* NT-Xent instance discrimination with a sharp temperature (0.05) because the
  negatives are fine-grained near-duplicates (same-set siblings, reprints).
* Asymmetric views: one near-clean (what the deployed gallery stores) and one
  heavy photo simulation (what a rectified phone photo looks like), because at
  inference similarity is always query(photo) x gallery(clean render).
* Partial fine-tune (freeze the early ViT blocks) with layer-wise LR decay —
  photography invariance is a mid/high-level adaptation, and a conservative LR
  protects the pretrained generality that makes DINOv2 useful.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path

# Fraction box of the artwork window on the canonical card. Source of truth is
# ARTWORK_BOX in app/core/constants.py — duplicated here because training/ must
# never import app/ (and vice versa); keep the two in sync by hand.
ARTWORK_BOX: tuple[float, float, float, float] = (0.06, 0.10, 0.94, 0.56)

# Cached card image size (H, W): preserves the 63:88 card aspect closely and
# leaves the artwork crop with real resolution before the 224 squash-resize.
CACHE_HEIGHT = 352
CACHE_WIDTH = 256

# Encoder input side; must equal the app's EMBED_INPUT_SIZE (224).
INPUT_SIZE = 224

# ImageNet normalization — must match app/signals/embedding.py exactly.
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


@dataclass
class TrainConfig:
    """Every knob of a fine-tune run.

    Attributes:
        data_dir: Root holding ``images/``, ``manifest.json``, and ``cache/``.
        run_dir: Output directory for checkpoints, metrics, and the config dump.
        backbone: torch.hub encoder key from ``training/backbones.py``.
        backbone_weights: Local ``.pth`` for gated backbones (DINOv3); empty
            when the entry point downloads its own weights.
        seed: Global seed for python/numpy/torch.
        batch_cards: Cards per step; each card contributes two views.
        epochs: Maximum epochs (early stopping may end the run sooner).
        art_crop_prob: Probability a card's pair uses the artwork crop mode.
        temperature: NT-Xent temperature.
        head_lr: Learning rate of the projection head.
        backbone_lr: Learning rate of the topmost unfrozen ViT block.
        layer_decay: Multiplicative LR decay per block, top block downward.
        freeze_blocks: Number of leading ViT blocks (and patch embed) to freeze.
        weight_decay: AdamW weight decay (norms and biases are exempt).
        warmup_steps: Linear LR warmup length in steps.
        min_lr: Cosine schedule floor.
        grad_clip: Global gradient-norm clip.
        eval_every_epochs: Run the retrieval eval every N epochs.
        eval_queries: Number of cards in the fixed eval query subset.
        eval_seed: Seed for the eval query subset and its deterministic augs.
        early_stop_evals: Stop after this many evals without improvement.
        collapse_warn_cosine: WARN when mean off-diagonal gallery cosine exceeds this.
        num_workers: Reserved for CPU-side loading knobs (augs run on GPU).
        device: Torch device string.
    """

    data_dir: str = "training_data"
    run_dir: str = "runs/ft"
    backbone: str = "dinov2_vits14"
    backbone_weights: str = ""
    seed: int = 42
    batch_cards: int = 512
    epochs: int = 40
    art_crop_prob: float = 0.4
    temperature: float = 0.05
    head_lr: float = 1e-3
    backbone_lr: float = 5e-5
    layer_decay: float = 0.8
    freeze_blocks: int = 4
    weight_decay: float = 0.04
    warmup_steps: int = 100
    min_lr: float = 1e-6
    grad_clip: float = 1.0
    eval_every_epochs: int = 2
    eval_queries: int = 2000
    eval_seed: int = 42
    early_stop_evals: int = 5
    collapse_warn_cosine: float = 0.8
    num_workers: int = 0
    device: str = "cuda"
    aug: AugConfig = field(default_factory=lambda: AugConfig())

    def save(self, path: str | Path) -> None:
        """Write the config as JSON for run provenance."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(dataclasses.asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def from_dict(cls, raw: dict) -> TrainConfig:
        """Rebuild a config from a plain dict (JSON dump or checkpoint field)."""
        data = dict(raw)
        aug = AugConfig(**data.pop("aug", {}))
        return cls(aug=aug, **data)

    @classmethod
    def load(cls, path: str | Path) -> TrainConfig:
        """Read a config back from its JSON dump."""
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


@dataclass
class AugConfig:
    """Probabilities and ranges of the heavy photo-simulation augmentations.

    Ranges are (low, high) uniform unless noted. The near-clean view uses only
    ``clean_scale`` and ``clean_brightness`` at probability 0.5 each.

    Attributes:
        perspective_p: Probability of the geometric block (perspective+rot+shift).
        corner_jitter: Max corner displacement as a fraction of the image side.
        rotate_deg: Max absolute rotation in degrees.
        translate_frac: Max absolute translation per axis, fraction of the side.
        scale_range: Multiplicative scale range.
        color_p: Probability of the colour/white-balance block.
        brightness_range: Multiplicative brightness range.
        contrast_range: Multiplicative contrast range.
        wb_gain_range: Per-channel white-balance gain range.
        saturation_range: Multiplicative saturation range.
        gamma_range: Gamma range.
        glare_p: Probability of 1-2 elliptical glare blobs.
        glare_sigma_frac: Gaussian sigma range per axis, fraction of width.
        glare_peak: Peak glare intensity range (screen-blended).
        band_p: Probability of a linear glare band (sleeve reflection).
        band_width_frac: Band width range, fraction of the height.
        band_intensity: Band intensity range.
        blur_gaussian_p: Probability of gaussian blur (exclusive with motion).
        blur_motion_p: Probability of motion blur.
        blur_sigma: Gaussian blur sigma range.
        motion_len: Motion blur kernel length range in pixels.
        noise_p: Probability of additive sensor noise.
        noise_sigma: Noise sigma range (on 0-1 images).
        mottle_p: Probability of low-frequency print-wear mottle.
        mottle_amp: Mottle amplitude (multiplicative, +/-).
        jpeg_p: Probability of JPEG artifact simulation (skipped if unavailable).
        jpeg_quality: JPEG quality range.
        occlude_p: Probability of 1-2 finger-occlusion blobs.
        occlude_area: Per-blob area range as a fraction of the image.
        art_crop_jitter: Fractional jitter on artwork-crop edges (heavy view only).
        clean_scale: Near-clean view scale range.
        clean_brightness: Near-clean view brightness range.
    """

    perspective_p: float = 0.9
    corner_jitter: float = 0.04
    rotate_deg: float = 4.0
    translate_frac: float = 0.03
    scale_range: tuple[float, float] = (0.92, 1.08)
    color_p: float = 0.9
    brightness_range: tuple[float, float] = (0.65, 1.3)
    contrast_range: tuple[float, float] = (0.7, 1.3)
    wb_gain_range: tuple[float, float] = (0.85, 1.15)
    saturation_range: tuple[float, float] = (0.7, 1.3)
    gamma_range: tuple[float, float] = (0.8, 1.25)
    glare_p: float = 0.5
    glare_sigma_frac: tuple[float, float] = (0.08, 0.35)
    glare_peak: tuple[float, float] = (0.25, 0.9)
    band_p: float = 0.25
    band_width_frac: tuple[float, float] = (0.05, 0.25)
    band_intensity: tuple[float, float] = (0.15, 0.5)
    blur_gaussian_p: float = 0.3
    blur_motion_p: float = 0.3
    blur_sigma: tuple[float, float] = (0.5, 2.5)
    motion_len: tuple[int, int] = (5, 15)
    noise_p: float = 0.5
    noise_sigma: tuple[float, float] = (0.005, 0.03)
    mottle_p: float = 0.3
    mottle_amp: float = 0.06
    jpeg_p: float = 0.3
    jpeg_quality: tuple[int, int] = (35, 80)
    occlude_p: float = 0.25
    occlude_area: tuple[float, float] = (0.02, 0.08)
    art_crop_jitter: float = 0.02
    clean_scale: tuple[float, float] = (0.97, 1.0)
    clean_brightness: tuple[float, float] = (0.95, 1.05)
