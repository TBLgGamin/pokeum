"""Compare embedder artifacts head to head on a labelled eval set (dev-only tool).

For every candidate ``.onnx`` encoder: point :data:`EMBED_MODEL_PATH` at it,
re-embed the catalogue (the identifier differs, so ``build_index`` rebuilds on
its own), score it with the recognizer's accuracy scorecard, bench its warm
latency, and write one JSON report per model. A markdown table summarises the
run at the end.

The first ``--models`` entry is the baseline: the index is rebuilt with it once
more after the last candidate, so the working install is left exactly as it
started. Every model costs a full re-embed of the catalogue (ten-plus minutes on
CPU), which is why this lives in ``scripts/`` and never runs in CI.

Run it from the repository root::

    python scripts/bakeoff.py --models data/models/dinov2s-ft-v1.onnx \
        data/models/dinov2s-ft-e3.onnx --eval-dir data/eval/real
"""

# ruff: noqa: E402 - the sys.path bootstrap has to run before the app imports.
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# Dev scripts are run as files from a checkout, which puts `scripts/` on the
# path instead of the repository root; `app` is not an installed package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import config
from app.core.logging_config import configure
from app.recognize.bench import run_bench
from app.recognize.eval import evaluate_folder
from app.recognize.factory import open_store
from app.reference.index import build_index
from app.signals.embedding import load_embedder

# Mirrors the extensions app.recognize.eval accepts; the bench reuses the first
# labelled image of the eval folder so every model is timed on the same photo.
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".bmp")

# One image, one request at a time: this bench compares encoders, so queueing
# must not blur the numbers. Load behaviour is `pokeum bench`'s job, not this.
BENCH_REQUESTS = 10
BENCH_CONCURRENCY = 1

TABLE_COLUMNS = (
    "model",
    "top1",
    "artwork_top1",
    "wrong_confident",
    "abstained",
    "p50 ms",
    "p95 ms",
)


def rebuild_index(model: Path, data_dir: str) -> None:
    """Point the app at ``model`` and re-embed the catalogue with it.

    Args:
        model: The ``.onnx`` encoder to index with.
        data_dir: Reference data root holding the store and index files.
    """
    os.environ["EMBED_MODEL_PATH"] = str(model)
    store = open_store(data_dir)
    try:
        counts = build_index(store, load_embedder(model), data_dir)
    finally:
        store.close()
    print(f"{model.name}: indexed {counts['hashed']} hashed, {counts['embedded']} embedded")


def first_image(eval_dir: Path) -> Path | None:
    """Return the first labelled image in ``eval_dir``, or ``None`` when it has none.

    Args:
        eval_dir: Folder of ``{card_id}__*`` labelled images.

    Returns:
        The alphabetically first image path, or ``None``.
    """
    images = sorted(
        p for p in eval_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
    )
    return images[0] if images else None


def score_model(model: Path, eval_dir: Path, data_dir: str, out_dir: Path) -> dict[str, Any]:
    """Rebuild the index for one model, score it, bench it, and write its report.

    Args:
        model: Encoder artifact to measure.
        eval_dir: Folder of ``{card_id}__*`` labelled images.
        data_dir: Reference data root.
        out_dir: Directory the per-model JSON report is written to.

    Returns:
        The scorecard, with the load bench nested under ``"bench"``.
    """
    rebuild_index(model, data_dir)
    report: dict[str, Any] = {"model": model.name}
    report.update(evaluate_folder(eval_dir, data_dir=data_dir))

    image = first_image(eval_dir)
    if image is None:
        print(f"{model.name}: no images in {eval_dir}, skipping the bench")
    else:
        # Nested, not merged: evaluate_folder reports its own latency_ms over the
        # whole folder, and the two measurements must not overwrite each other.
        report["bench"] = run_bench(
            image, data_dir=data_dir, requests=BENCH_REQUESTS, concurrency=BENCH_CONCURRENCY
        )

    out_path = out_dir / f"{model.stem}.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"{model.name}: wrote {out_path}")
    return report


def markdown_table(reports: list[dict[str, Any]]) -> str:
    """Render the per-model reports as one markdown comparison table.

    Args:
        reports: Scorecards in the order the models were measured.

    Returns:
        The table as a newline-joined string.
    """
    lines = [
        "| " + " | ".join(TABLE_COLUMNS) + " |",
        "| " + " | ".join("---" for _ in TABLE_COLUMNS) + " |",
    ]
    for report in reports:
        latency = report.get("bench", {}).get("latency_ms", {})
        lines.append(
            f"| {report['model']} | {report['top1_accuracy']} "
            f"| {report['artwork_top1_accuracy']} | {report['wrong_confident']} "
            f"| {report['abstained']} "
            f"| {latency.get('p50', '-')} | {latency.get('p95', '-')} |"
        )
    return "\n".join(lines)


def main() -> None:
    """Parse arguments and run the bake-off."""
    parser = argparse.ArgumentParser(description="Compare embedder artifacts on a labelled set")
    parser.add_argument(
        "--models",
        nargs="+",
        required=True,
        help="ONNX encoders to compare; the first is the baseline the index is restored to",
    )
    parser.add_argument("--eval-dir", default="data/eval/real", help="labelled eval image folder")
    parser.add_argument("--data-dir", default=None, help="reference data root (default: DATA_DIR)")
    parser.add_argument("--out", default="data/bakeoff", help="directory for the JSON reports")
    args = parser.parse_args()
    configure()

    models = [Path(path) for path in args.models]
    missing = [str(model) for model in models if not model.is_file()]
    if missing:
        # load_embedder silently falls back to the histogram encoder for a
        # missing file, which would score a typo instead of a model.
        parser.error(f"model file(s) not found: {', '.join(missing)}")
    eval_dir = Path(args.eval_dir)
    if not eval_dir.is_dir():
        parser.error(f"eval dir not found: {eval_dir}")

    data_dir = args.data_dir or config.data_dir()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    reports = [score_model(model, eval_dir, data_dir, out_dir) for model in models]
    if len(models) > 1:
        print(f"restoring the working index to {models[0].name}")
        rebuild_index(models[0], data_dir)

    print()
    print(markdown_table(reports))


if __name__ == "__main__":
    main()
