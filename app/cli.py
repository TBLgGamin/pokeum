"""Command-line interface for the pokeum card recognizer.

Subcommands:

* ``sync`` — pull sets, cards, and images from TCGdex into the local store;
* ``index`` — compute hashes and embeddings over the synced images;
* ``identify`` — recognize the card in an image file;
* ``scan`` — run live recognition from a webcam;
* ``serve`` — start the FastAPI service;
* ``eval`` — score recognition over a folder of labelled images.

Heavy modules (vision, models, service) are imported inside their handlers so
``--help`` and unrelated commands stay fast. Results are written to stdout;
progress and diagnostics go through logging to stderr.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from app.core import config
from app.core.logging_config import configure

logger = logging.getLogger(__name__)


def _resolve_data_dir(args: argparse.Namespace) -> str:
    """Return the data directory from the flag or the configured default."""
    return args.data_dir or config.data_dir()


def _emit(text: str) -> None:
    """Write a line of program output to stdout."""
    sys.stdout.write(text + "\n")


def cmd_sync(args: argparse.Namespace) -> int:
    """Handle ``sync``: fetch reference data from TCGdex."""
    from app.recognize.factory import open_store
    from app.reference import sync as sync_module
    from app.reference.tcgdex import TCGdexClient

    data_dir = _resolve_data_dir(args)
    store = open_store(data_dir)
    client = TCGdexClient(config.tcgdex_base_url(), config.card_language())
    try:
        summary = sync_module.sync(
            store,
            client,
            data_dir,
            only_set=args.set,
            fetch_details=not args.no_details,
            force=args.full,
        )
    finally:
        client.close()
        store.close()
    _emit(
        f"synced {summary.sets_processed} set(s), {summary.cards_upserted} card(s), "
        f"{summary.images_downloaded} image(s), {summary.symbols_downloaded} symbol(s)"
    )
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    """Handle ``index [build]``: compute hashes and embeddings."""
    from app.recognize.factory import open_store
    from app.reference import index as index_module
    from app.signals.embedding import load_embedder

    data_dir = _resolve_data_dir(args)
    store = open_store(data_dir)
    embedder = load_embedder(config.embed_model_path())
    try:
        counts = index_module.build_index(store, embedder, data_dir, full=args.full)
    finally:
        store.close()
    _emit(f"indexed: {counts['hashed']} hashed, {counts['embedded']} embedded")
    return 0


def cmd_identify(args: argparse.Namespace) -> int:
    """Handle ``identify``: recognize the card in one image file."""
    from app.core import constants
    from app.recognize.factory import build_recognizer
    from app.vision.imaging import cap_long_side, load_image

    data_dir = _resolve_data_dir(args)
    recognizer = build_recognizer(data_dir=data_dir, with_ocr=not args.no_ocr)
    image = cap_long_side(load_image(args.image), constants.INGEST_MAX_SIDE)
    result = recognizer.identify(image, top_k=args.top_k)
    data = result.as_dict()
    _emit(json.dumps(data) if args.json else _format_result(data))
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    """Handle ``scan``: run live recognition from a webcam."""
    from app.recognize.factory import build_recognizer
    from app.recognize.webcam import run_webcam

    data_dir = _resolve_data_dir(args)
    recognizer = build_recognizer(data_dir=data_dir)
    camera = args.camera if args.camera is not None else config.webcam_index()
    run_webcam(recognizer, camera)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Handle ``serve``: start the FastAPI service."""
    import uvicorn

    from app.api.server import create_app
    from app.recognize.factory import build_recognizer

    data_dir = _resolve_data_dir(args)
    recognizer = build_recognizer(data_dir=data_dir)
    app = create_app(recognizer)
    host = args.host or config.api_host()
    port = args.port or config.api_port()
    _emit(f"serving on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    """Handle ``eval``: score recognition over a labelled image folder."""
    from app.recognize.eval import evaluate_folder

    data_dir = _resolve_data_dir(args)
    report = evaluate_folder(args.folder, data_dir=data_dir, top_k=args.top_k)
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        logger.info("wrote eval report to %s", args.out)
    _emit(text)
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    """Handle ``bench``: measure recognition latency and throughput."""
    from app.recognize.bench import run_bench

    data_dir = _resolve_data_dir(args)
    report = run_bench(
        args.image,
        data_dir=data_dir,
        requests=args.requests,
        concurrency=args.concurrency,
    )
    _emit(json.dumps(report, indent=2))
    return 0


def _format_result(data: dict) -> str:
    """Render a recognition result dictionary as readable text for the terminal."""
    match = data["match"]
    if match is None:
        return f"status: {data['status']} (no confident match)"
    lines = [
        f"status: {data['status']}",
        f"card:   {match['name']}  [{match['set']['name']} {match['number']}]",
        f"id:     {match['card_id']}   confidence: {match['confidence']}",
    ]
    variants = [v["kind"] for v in match.get("variants", []) if v["present"]]
    if variants:
        lines.append(f"variants: {', '.join(variants)}")
    if data["alternates"]:
        alts = ", ".join(f"{a['name']} ({a['confidence']})" for a in data["alternates"][:3])
        lines.append(f"alternates: {alts}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all subcommands."""
    parser = argparse.ArgumentParser(prog="pokeum", description="Pokémon card recognizer")
    parser.add_argument("--data-dir", default=None, help="override the data directory")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="fetch reference data from TCGdex")
    p_sync.add_argument("--set", default=None, help="sync only this set id")
    p_sync.add_argument("--no-details", action="store_true", help="skip per-card detail")
    p_sync.add_argument("--full", action="store_true", help="re-sync even current sets")
    p_sync.set_defaults(func=cmd_sync)

    p_index = sub.add_parser("index", help="build hashes and embeddings")
    p_index.add_argument("action", nargs="?", default="build", choices=["build"])
    p_index.add_argument("--full", action="store_true", help="recompute everything")
    p_index.set_defaults(func=cmd_index)

    p_id = sub.add_parser("identify", help="recognize a card image")
    p_id.add_argument("image", help="path to the image file")
    p_id.add_argument("--top-k", type=int, default=5, help="candidates")
    p_id.add_argument("--json", action="store_true", help="emit JSON")
    p_id.add_argument("--no-ocr", action="store_true", help="disable the OCR signal")
    p_id.set_defaults(func=cmd_identify)

    p_scan = sub.add_parser("scan", help="live webcam recognition")
    p_scan.add_argument("--camera", type=int, default=None, help="capture device index")
    p_scan.set_defaults(func=cmd_scan)

    p_serve = sub.add_parser("serve", help="start the HTTP service")
    p_serve.add_argument("--host", default=None, help="bind host")
    p_serve.add_argument("--port", type=int, default=None, help="bind port")
    p_serve.set_defaults(func=cmd_serve)

    p_eval = sub.add_parser("eval", help="score recognition over labelled images")
    p_eval.add_argument("folder", help="folder of '{card_id}__*.jpg' images")
    p_eval.add_argument("--top-k", type=int, default=5, help="top-k for accuracy")
    p_eval.add_argument("--out", default=None, help="also write the JSON report to this path")
    p_eval.set_defaults(func=cmd_eval)

    p_bench = sub.add_parser("bench", help="measure recognition latency and throughput")
    p_bench.add_argument("image", help="image recognized on every request")
    p_bench.add_argument("--requests", type=int, default=20, help="timed recognitions")
    p_bench.add_argument("--concurrency", type=int, default=2, help="requests in flight")
    p_bench.set_defaults(func=cmd_bench)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, configure logging, and dispatch to the subcommand.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    configure()
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = args.func
    return int(handler(args))
