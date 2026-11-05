"""Selectable torch.hub backbones for the embedder fine-tune.

The harness trains and exports exactly one ViT encoder; *which* one is a config
value so a DINOv2-vs-DINOv3 bake-off is a flag rather than a fork. Every entry
must honour the deployed contract in ``app/signals/embedding.py``: a
``forward(image)`` that maps an ``(N, 3, 224, 224)`` float32 batch to an
``(N, D)`` feature vector (the normalized CLS token), exportable to ONNX at
opset 17 with a single graph input.

DINOv3 weights are gated behind Meta's download form and carry a custom DINOv3
licence, so this module never fetches them: the hub entry point takes a local
``.pth`` path supplied by ``--backbone-weights``. DINOv2 weights are Apache-2.0
and download themselves through the usual torch.hub cache.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch


@dataclass(frozen=True)
class BackboneSpec:
    """One selectable encoder and how to instantiate it.

    Attributes:
        hub_repo: torch.hub repository, e.g. ``facebookresearch/dinov3``.
        hub_model: Entry point within that repository.
        feature_dim: Width of the CLS feature the backbone returns.
        needs_weights: Whether the entry point requires a local weights file
            (true for the gated DINOv3 checkpoints).
    """

    hub_repo: str
    hub_model: str
    feature_dim: int
    needs_weights: bool


# ViT-S/14, ViT-S/16 and ViT-S+/16 all emit 384-d CLS features, so the projection
# head, the parity gate, and the app's embedding matrices carry over unchanged
# between all three — only the patch size and the pretraining recipe differ.
BACKBONES: dict[str, BackboneSpec] = {
    "dinov2_vits14": BackboneSpec("facebookresearch/dinov2", "dinov2_vits14", 384, False),
    "dinov3_vits16": BackboneSpec("facebookresearch/dinov3", "dinov3_vits16", 384, True),
    "dinov3_vits16plus": BackboneSpec("facebookresearch/dinov3", "dinov3_vits16plus", 384, True),
}


def load_backbone(name: str, weights: str = "") -> torch.nn.Module:
    """Instantiate a registered backbone from torch.hub.

    Args:
        name: Key into :data:`BACKBONES`.
        weights: Local ``.pth`` path for gated checkpoints; unused by entries
            that download their own weights.

    Returns:
        The instantiated backbone module (untrained head, pretrained encoder).

    Raises:
        ValueError: ``name`` is not registered, or the entry point needs a local
            weights file and none was given.
    """
    spec = BACKBONES.get(name)
    if spec is None:
        raise ValueError(f"unknown backbone {name!r}; choose from {sorted(BACKBONES)}")
    if spec.needs_weights and not weights:
        raise ValueError(
            f"backbone {name!r} needs --backbone-weights (local .pth from Meta's DINOv3 download)"
        )
    if spec.needs_weights:
        model: torch.nn.Module = torch.hub.load(spec.hub_repo, spec.hub_model, weights=weights)
    else:
        model = torch.hub.load(spec.hub_repo, spec.hub_model)
    return model
