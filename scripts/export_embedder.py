"""Export a frozen image encoder to ONNX for the recognizer (dev-only tool).

Run this once on a machine that has PyTorch installed (e.g. the training box) to
produce the ``.onnx`` file that :data:`EMBED_MODEL_PATH` points to. PyTorch is
never a runtime dependency of the app — only this offline export step needs it.

    python scripts/export_embedder.py --out data/models/dinov2s.onnx

The default exports DINOv2-small (ViT-S/14, 384-d features) from torch.hub. Any
encoder that maps an ``(N, 3, H, W)`` image batch to an ``(N, D)`` feature vector
works; point :data:`EMBED_MODEL_PATH` at the result and run ``pokeum index build``.

DINOv3 entry points take their weights as a local ``.pth`` file (Meta's download
form gates them, so nothing is fetched automatically)::

    python scripts/export_embedder.py --repo facebookresearch/dinov3 \
        --model dinov3_vits16 --weights <local.pth> --out data/models/dinov3s16-frozen.onnx

Swapping the encoder changes its identifier (``onnx:<filename>:<sha256[:12]>``),
which invalidates the existing embedding matrices, so ``index build`` will
rebuild them automatically — no per-set retraining is involved.
"""

from __future__ import annotations

import argparse
from pathlib import Path

_INPUT_SIZE = 224


def export(out_path: Path, hub_repo: str, hub_model: str, weights: str = "") -> None:
    """Export a torch.hub encoder to ONNX at ``out_path``.

    Args:
        out_path: Destination ``.onnx`` file.
        hub_repo: torch.hub repository, e.g. ``facebookresearch/dinov2``.
        hub_model: Model entry point, e.g. ``dinov2_vits14``.
        weights: Local ``.pth`` path for entry points whose weights are gated
            (the DINOv3 family); entries that download their own weights take
            the empty default.
    """
    import torch  # Imported here so the module loads without torch installed.

    if weights:
        model = torch.hub.load(hub_repo, hub_model, weights=weights)
    else:
        model = torch.hub.load(hub_repo, hub_model)
    model.eval()

    # DINOv2's forward(x, masks=None) makes modern torch exporters emit `masks`
    # as a second required graph input, breaking the app's single-input
    # contract. A wrapper pins the traced signature to one tensor.
    class _SingleInput(torch.nn.Module):
        """Constrain the export signature to exactly one tensor input."""

        def __init__(self, inner: torch.nn.Module) -> None:
            super().__init__()
            self.inner = inner

        def forward(self, image: torch.Tensor) -> torch.Tensor:
            """Return encoder features for one image batch."""
            out: torch.Tensor = self.inner(image)
            return out

    dummy = torch.zeros(1, 3, _INPUT_SIZE, _INPUT_SIZE, dtype=torch.float32)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        _SingleInput(model),
        dummy,
        str(out_path),
        input_names=["image"],
        output_names=["features"],
        dynamic_axes={"image": {0: "batch"}, "features": {0: "batch"}},
        opset_version=17,
    )


def main() -> None:
    """Parse arguments and run the export."""
    parser = argparse.ArgumentParser(description="Export an image encoder to ONNX")
    parser.add_argument("--out", default="data/models/dinov2s.onnx", help="output path")
    parser.add_argument("--repo", default="facebookresearch/dinov2", help="torch.hub repo")
    parser.add_argument("--model", default="dinov2_vits14", help="torch.hub entry point")
    parser.add_argument("--weights", default="", help="local .pth for gated weights (DINOv3)")
    args = parser.parse_args()
    export(Path(args.out), args.repo, args.model, args.weights)


if __name__ == "__main__":
    main()
