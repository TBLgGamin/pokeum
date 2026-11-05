"""Export the fine-tuned backbone to ONNX and verify parity with the app.

The exported graph must be byte-compatible with what the app's ``OnnxEmbedder``
expects (and what ``scripts/export_embedder.py`` produces): input ``"image"``
(dynamic batch, 3x224x224 fp32), output ``"features"`` (batch, 384), opset 17,
TorchScript exporter (``dynamo=False`` pinned — the known-good path for the
dinov2 hub code).

The parity gate is mandatory: 16 cached card images are embedded (a) by the
torch model on CPU fp32 using the app's *exact* preprocessing — PIL
squash-resize to 224 with the default bicubic filter, /255, ImageNet mean/std —
re-implemented here because training/ never imports app/, and (b) by
onnxruntime on the exported file. Per-image cosine must exceed 0.999 or the
artifact is rejected.

Ship the artifact under a NEW filename per export (``dinov2s-ft-v1.onnx``,
``-v2``, ...) so humans can tell exports apart; the app's
``OnnxEmbedder.identifier`` is ``onnx:<filename>:<sha256[:12]>``, so any change
to the weights triggers the index rebuild even under a reused filename.

Usage::

    python -m training.export_onnx --checkpoint runs/ft/best.pt \
        --out runs/ft/dinov2s-ft-v1.onnx --data training_data
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from training.config import IMAGENET_MEAN, IMAGENET_STD, INPUT_SIZE, TrainConfig
from training.model import FineTuneModel

logger = logging.getLogger(__name__)

PARITY_IMAGES = 16
PARITY_MIN_COSINE = 0.999


class _SingleInputBackbone(torch.nn.Module):
    """Constrain the export signature to exactly one tensor input.

    DINOv2's ``forward(x, masks=None)`` makes torch 2.12's exporter (dynamo
    path) emit ``masks`` as a second required graph input, which breaks the
    app's single-input contract. Wrapping pins the traced signature to
    ``image`` only.
    """

    def __init__(self, backbone: torch.nn.Module) -> None:
        super().__init__()
        self.backbone = backbone

    def forward(self, image: torch.Tensor) -> torch.Tensor:
        """Return backbone features for one image batch."""
        out: torch.Tensor = self.backbone(image)
        return out


def _app_preprocess(image_u8: np.ndarray) -> np.ndarray:
    """Replicate the app's OnnxEmbedder preprocessing exactly.

    PIL squash-resize to (INPUT_SIZE, INPUT_SIZE) with PIL's default filter,
    /255, ImageNet mean/std, NCHW float32.

    Args:
        image_u8: One RGB uint8 image ``(H, W, 3)``.

    Returns:
        ``(1, 3, INPUT_SIZE, INPUT_SIZE)`` float32 tensor input.
    """
    pil = Image.fromarray(image_u8, mode="RGB").resize((INPUT_SIZE, INPUT_SIZE))
    arr = np.asarray(pil, dtype=np.float32) / 255.0
    arr = (arr - np.array(IMAGENET_MEAN, dtype=np.float32)) / np.array(
        IMAGENET_STD, dtype=np.float32
    )
    return np.transpose(arr, (2, 0, 1))[None].astype(np.float32)


def export(checkpoint: Path, out_path: Path) -> torch.nn.Module:
    """Export the checkpoint's backbone to ONNX at ``out_path``.

    Args:
        checkpoint: A ``best.pt``/``last.pt`` written by training.
        out_path: Destination ``.onnx`` file.

    Returns:
        The loaded fp32 CPU backbone (reused by the parity check).
    """
    state = torch.load(checkpoint, map_location="cpu", weights_only=False)
    raw_config = state.get("config")
    cfg = TrainConfig.from_dict(raw_config) if isinstance(raw_config, dict) else TrainConfig()
    model = FineTuneModel(cfg)
    model.load_state_dict(state["model"])
    backbone = model.backbone.float().eval()

    dummy = torch.zeros(1, 3, INPUT_SIZE, INPUT_SIZE, dtype=torch.float32)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        _SingleInputBackbone(backbone),
        dummy,
        str(out_path),
        input_names=["image"],
        output_names=["features"],
        dynamic_axes={"image": {0: "batch"}, "features": {0: "batch"}},
        opset_version=17,
        dynamo=False,
    )
    logger.info("exported %s (%.1f MB)", out_path, out_path.stat().st_size / 1e6)
    return backbone


def parity_check(backbone: torch.nn.Module, onnx_path: Path, data_dir: Path) -> bool:
    """Compare torch vs onnxruntime embeddings on cached card images.

    Args:
        backbone: The exported fp32 CPU backbone.
        onnx_path: The exported artifact.
        data_dir: Training data root (for the image cache).

    Returns:
        ``True`` when every per-image cosine exceeds ``PARITY_MIN_COSINE``.
    """
    import onnxruntime as ort

    from training.cache import load_cache

    _, images = load_cache(data_dir)
    count = min(PARITY_IMAGES, len(images))
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])

    worst = 1.0
    max_abs = 0.0
    for i in range(count):
        tensor = _app_preprocess(np.asarray(images[i]))
        with torch.no_grad():
            torch_vec = backbone(torch.from_numpy(tensor)).numpy().ravel()
        ort_vec = session.run(None, {"image": tensor})[0].ravel()
        cos = float(
            np.dot(torch_vec, ort_vec)
            / (np.linalg.norm(torch_vec) * np.linalg.norm(ort_vec) + 1e-12)
        )
        worst = min(worst, cos)
        max_abs = max(max_abs, float(np.max(np.abs(torch_vec - ort_vec))))

    logger.info("parity over %d images: min cosine %.6f, max abs diff %.5f", count, worst, max_abs)
    return worst > PARITY_MIN_COSINE


def main(argv: list[str] | None = None) -> int:
    """Export a checkpoint and gate on the parity check."""
    parser = argparse.ArgumentParser(description="Export fine-tuned backbone to ONNX")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--out", required=True, help="output .onnx path (use a NEW versioned name)")
    parser.add_argument("--data", default="training_data")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    out_path = Path(args.out)
    backbone = export(Path(args.checkpoint), out_path)
    if not parity_check(backbone, out_path, Path(args.data)):
        logger.critical("parity check FAILED - artifact rejected, do not deploy %s", out_path)
        return 1
    logger.info("parity check passed - %s is safe to deploy", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
