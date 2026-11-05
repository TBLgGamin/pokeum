# Embedder fine-tune harness

One-time metric-learning fine-tune of DINOv2-S so retrieval survives glare,
perspective, and blur. It trains **photography invariance, never card
identities** — new sets still require zero training, only `sync` + `index
build`. Design rationale lives in `openwiki/training.md`; the deployed encoder
contract is `app/signals/embedding.py` (`OnnxEmbedder`).

This package is standalone: it never imports `app/` and `app/` never imports
it. It runs on a GPU box (recon'd: A100-80GB, preinstalled CUDA torch), not on
the dev machine — which is why it has no offline unit tests; its verification
is `smoke_test.py` run on the box.

## Runbook

```bash
# 1. Deploy from the dev box (tars training/, scp, venv with
#    --system-site-packages to inherit the box's CUDA torch, pip install).
bash training/deploy.sh tnr-0

# 2. Smoke test on the box (~5 min): fetch 3 tiny sets -> cache -> hub model
#    download -> 4 epochs -> export -> torch-vs-onnxruntime parity. All stages
#    must print PASS.
ssh tnr-0 'cd ~/pokeum && source .venv/bin/activate && python -m training.smoke_test'

# 3. Full run (inside tmux so an SSH drop cannot kill it):
ssh tnr-0
tmux new -s train
cd ~/pokeum && source .venv/bin/activate
python -m training.fetch_data --out training_data          # ~15-20 min, all EN sets, low-res
python -m training.cache --data training_data              # ~2 min -> cache/images.npy
python -m training.train --data training_data --run-dir runs/ft   # ~45-60 min
python -m training.export_onnx --checkpoint runs/ft/best.pt \
    --out runs/ft/dinov2s-ft-v1.onnx --data training_data   # export + parity gate

# 4. Monitor from the dev box:
ssh tnr-0 'tail -n 20 ~/pokeum/runs/ft/metrics.csv'

# 5. Bring the artifact home and promote it:
scp tnr-0:~/pokeum/runs/ft/dinov2s-ft-v1.onnx data/models/
# point EMBED_MODEL_PATH at it (or replace the default path), then:
python main.py index build --full
```

Resume an interrupted run with `python -m training.train --resume runs/ft`
(checkpoints carry model+optimizer+scheduler+epoch+RNG).

## Guards built into training

- **Epoch-0 frozen baseline** is evaluated before any training step — every
  later eval must beat it; if the combined val metric ever falls *below* it,
  the run aborts (the "LR destroyed the pretrained transfer" signature).
- Early stop after 5 evals without improvement; `best.pt` selected by
  `0.5*full_top1 + 0.5*art_top1` (mirrors the app's fusion weights).
- Collapse monitor (mean off-diagonal gallery cosine), clean-gallery drift
  report, non-finite-loss abort, one-shot batch halving on CUDA OOM.

## Choosing a backbone

`--backbone` picks the encoder from the registry in `training/backbones.py`; the
choice is stored in the checkpoint's config, so `evaluate.py` and
`export_onnx.py` rebuild the matching model without being told again.

| backbone | patch | weights |
| --- | --- | --- |
| `dinov2_vits14` (default) | 14 | fetched by torch.hub (Apache-2.0) |
| `dinov3_vits16` | 16 | local `.pth` via `--backbone-weights` |
| `dinov3_vits16plus` | 16 | local `.pth` via `--backbone-weights` |

DINOv3 weights are gated: request them through the download form linked from
[`facebookresearch/dinov3`](https://github.com/facebookresearch/dinov3) and pass
the file you receive — nothing downloads them for you. They carry the custom
**DINOv3 licence**, not Apache-2.0; read it before redistributing an exported
artifact (the released `dinov2s-ft-v1.onnx` is an Apache-2.0 DINOv2 derivative,
a DINOv3 export would not be).

```bash
python -m training.train --data training_data --run-dir runs/ft-v3 \
    --backbone dinov3_vits16 --backbone-weights ~/weights/dinov3_vits16.pth
```

A frozen (untrained) DINOv3 exports straight from any box with torch, for a
cheap first comparison:

```bash
python scripts/export_embedder.py --repo facebookresearch/dinov3 \
    --model dinov3_vits16 --weights ~/weights/dinov3_vits16.pth \
    --out data/models/dinov3s16-frozen.onnx
```

Nothing is promoted on opinion: `scripts/bakeoff.py` re-indexes the catalogue
with each candidate, scores it on the labelled eval set, benches its latency,
and prints the comparison table (first model listed = the baseline the working
index is restored to).

```bash
python scripts/bakeoff.py --models data/models/dinov2s-ft-v1.onnx \
    data/models/dinov3s16-frozen.onnx --eval-dir data/eval/real
```

The app's default encoder stays `dinov2s-ft-v1.onnx` until a bake-off shows a
candidate winning on `top1_accuracy` without raising `wrong_confident`.

## Growing the real-photo set

Both views of a training pair currently come from the official render, with the
photography *simulated* (`augment.py`). Real photos labelled by **exact
printing** are what simulation cannot supply — no public Pokémon dataset labels
at that granularity, and unlabelled photos cannot enter a metric-learning
objective at all. The survey, the licence position, and the volume targets are in
[`docs/real-photo-data.md`](../docs/real-photo-data.md); the pipeline that
acquires and labels photos is `scripts/acquire_photos.py` →
`scripts/annotate_photos.py` ([`docs/photo-acquisition.md`](../docs/photo-acquisition.md)).

```bash
# score the encoder on the labelled real photos
python main.py eval data/photos/labelled --out data/photos/labelled-report.json
```

## Versioning the artifact

The app's `OnnxEmbedder.identifier` is `onnx:<filename>:<sha256[:12]>`, so any
change to the weights is what tells `index build` to re-embed the catalogue —
even if the filename is reused. Still export under a **new filename**
(`dinov2s-ft-v1.onnx`, `-v2`, ...) so two artifacts are distinguishable by
humans, not just by hash. The parity gate (torch-vs-onnxruntime cosine > 0.999
on real card images, app-exact preprocessing) must pass before an artifact
leaves the box.

## Keeping in sync with the app

`training/config.py` duplicates `ARTWORK_BOX` and the ImageNet normalization
from `app/core/constants.py` / `app/signals/embedding.py` (the no-import rule
cuts both ways). If either changes in the app, mirror it here and retrain.
