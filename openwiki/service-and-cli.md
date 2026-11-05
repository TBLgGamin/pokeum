---
title: Service, CLI & webcam
sources: ["app/cli.py", "app/api/**", "main.py", "app/recognize/factory.py", "app/recognize/webcam.py", "app/recognize/eval.py", "app/recognize/bench.py", "scripts/make_synthetic_eval.py"]
read-when: "changing the CLI commands, the FastAPI endpoints, webcam scanning, the recognizer factory/wiring, or the eval/bench harnesses"
verified: 388a26a04ef7
---

# Service, CLI & webcam

The three ways to drive the recognizer, plus the wiring that builds it. The
recognition logic itself is in [recognition-pipeline](recognition-pipeline.md);
this page is the surface around it.

## Building a recognizer (`app/recognize/factory.py`)

`build_recognizer()` is the single construction path: it reads configuration,
opens the store, loads the hash and embedding indexes, and constructs the heavy
collaborators (embedder, OCR engine, symbol matcher). The CLI and API both go
through it so they share one path; **tests build `Recognizer` directly with
fakes and never touch this module**. Missing embedding indexes simply disable the
embedding signals rather than failing, and OCR degrades to `None` if
unavailable — the service still starts.

**Startup warm-up.** `build_recognizer(..., warm=True)` — the default, and what
every caller uses today — finishes by running one throwaway `identify` on a
mid-grey noise frame (`_warm_up`). ONNX Runtime allocates its arenas and
RapidOCR loads its sub-models on *first inference*, not at construction, so
without this the first real scan pays several hundred milliseconds of
initialisation that has nothing to do with it. The frame is noise on purpose:
no card quad is found, so the whole-frame path runs every signal, and it runs
on the recognizer's own executor threads — the same ones the first request will
use. The cost is paid once, visibly (`recognizer warmed in N ms`), and it can
never block startup: a warm-up failure is logged and the recognizer is returned
cold. `warm=False` exists for construction paths where first-call latency does
not matter and the extra recognition would be pure overhead.

## CLI (`app/cli.py`, entered via `main.py`)

`main.py` is a thin dispatch into `app.cli.main()`. Heavy modules are imported
lazily inside each handler, so `--help` and unrelated commands stay fast. Results
are written to stdout; progress and diagnostics go through logging to stderr.

| Command | What it does |
|---|---|
| `sync [--set ID] [--full] [--no-details]` | Fetch reference data from TCGdex (see [reference-data](reference-data.md)) |
| `index [build] [--full]` | Compute hashes + embeddings over synced images |
| `identify IMAGE [--top-k N] [--json] [--no-ocr]` | Recognize one image file |
| `scan [--camera N]` | Live webcam recognition |
| `serve [--host H] [--port P]` | Start the FastAPI service |
| `eval FOLDER [--top-k N] [--out PATH]` | Score recognition over labelled images |
| `bench IMAGE [--requests N] [--concurrency C]` | Measure recognition latency and throughput |

`--data-dir` overrides the data root for any command. `identify` prints a
human-readable summary by default (card, set, number, confidence, variants,
alternates) or a JSON object with `--json`.

## FastAPI service (`app/api/`)

`create_app()` ([`server.py`](../app/api/server.py)) builds the app; the
recognizer is expensive to construct, so it is built once during the app's
lifespan and stashed on `app.state` for every request to share. Tests inject a
ready-made recognizer to skip that cost.

The OpenAPI document carries `_API_VERSION` (`2.0.0`), kept in step with the
package version: v2 is a breaking surface change — the `ambiguous` status and
`503` load shedding on `/identify`.

Routes ([`routes.py`](../app/api/routes.py)) are thin — decode, call the shared
recognizer, return its own dict for FastAPI to validate against the response
models in [`schemas.py`](../app/api/schemas.py). **All API routes are mounted
under `/api`** so the built frontend can be served from the same origin at the
root (the client's default `/api` base then works identically in dev and prod):

- `GET /api/health` → `{status, cards_indexed, embedder_loaded, embedder_id}`.
  `embedder_loaded` reports whether the embedding *indexes* are loaded and
  usable; `embedder_id` is the encoder's own identity
  (`Recognizer.embedder_id` — `onnx:<filename>:<sha256[:12]>` for an ONNX
  model, `histogram-v1` for the NumPy fallback, `"none"` only for an
  embedder-less recognizer). The id is content-addressed on purpose: one health
  call says which weights are answering, and comparing it against the index
  manifest's `embedder_id` says whether the index was built from them (see
  [reference-data](reference-data.md)).
- `POST /api/identify` (multipart `file`, query `top_k`, `require_detection`,
  `guide_margin`) → the recognition result; `400` when the upload cannot be
  decoded as an image. Decode + recognition run via `run_in_threadpool` (they
  are CPU-bound; inline they would stall every concurrent request), and the
  upload is capped to `INGEST_MAX_SIDE` first. **Recognition is bounded**: an
  `asyncio.Semaphore(IDENTIFY_MAX_CONCURRENCY)` (default 2, created in the
  lifespan) admits requests, and one that waits longer than
  `IDENTIFY_QUEUE_TIMEOUT_SECONDS` (2.0 s) is shed with `503`
  `recognizer busy` plus `Retry-After: 1` rather than queued. The reasoning:
  recognition is CPU-bound on a fixed core count, so past a small cap extra
  concurrency buys no throughput and only inflates every request's latency —
  a scan UI would rather retry in a second than watch one crawl. The slot is
  released in a `finally`, so a decode failure never leaks it.
  `require_detection=true` answers `no_card_detected` when no card quad is
  found. A guided camera capture instead sends its symmetric margin as
  `guide_margin`: detection remains preferred, but the recognizer can recover
  the known inner guide and only accepts that fallback when fusion is
  confident. Pre-cropped photo uploads keep the whole-frame fallback.
- `POST /api/identify/burst` (multipart `files[]`, same query parameters) →
  one recognition result from 1–`BURST_MAX_FRAMES` (4) frames of the same card;
  `400` for a bad frame count or an undecodable frame. The scan UI sends it
  when a single frame was not decisive. The whole burst holds **one**
  semaphore slot (and sheds `503` the same way), so a burst costs the capacity
  of one request, not N; frames are decoded and capped like `/identify`. The
  merge rules are in
  [recognition-pipeline](recognition-pipeline.md#multi-frame-bursts).
  The response (like `/identify`) carries `name`, the title-band read that
  fed fusion, next to `ocr`.
- `GET /api/cards/{card_id}` → catalogue detail (includes the card's TCGdex CDN
  `image_url`, which every candidate in `/api/identify` responses also
  carries); `404` when unknown
- `GET /api/cards/{card_id}/image` → a 320px JPEG thumbnail of the card,
  derived lazily from the cached reference image into `data/thumbs/` and served
  with immutable cache headers. When the server has no local image cache the
  endpoint **redirects (307) to the card's TCGdex CDN URL** instead, so
  deployments never need to carry `data/images/`; `404` only when the card has
  neither.

**Single-origin serving:** when the configured `FRONTEND_DIST_DIR` (default
`./frontend/dist`) exists, `create_app()` mounts it at the root via
`SPAStaticFiles` ([`static.py`](../app/api/static.py)) — unknown non-API paths
fall back to `index.html` (client-side routes, installed-PWA launches), unknown
`/api/*` paths stay JSON 404s, and `index.html`/service-worker files are served
`no-cache` so deployed updates propagate while Vite's hashed assets stay
long-cached. Empty setting or missing directory skips the mount (pure-API
deployments, dev machines without a build).

CORS is wide open on purpose: the service is account-less, and the frontend may
load from another origin (dev server, tunnel). Start it with
`python main.py serve` (binds `API_HOST:API_PORT`, defaults `127.0.0.1:8000`).

## Scan frontend (`frontend/`)

The Vue PWA that drives the API from a phone camera — auto-capture, the
printing chooser, the on-device collection and CSV export — has its own page:
[scan-frontend](scan-frontend.md). It is the only client that uses
`guide_margin`.

## Webcam (`app/recognize/webcam.py`)

`run_webcam()` reads frames from an OpenCV capture device, processes ~5 fps,
runs each frame through `Recognizer.identify(require_detection=True)`, and feeds
results to the pure `TemporalAggregator` (see
[recognition-pipeline](recognition-pipeline.md)). Only stable identifications are
emitted — one JSON object per card to stdout. This is runtime glue and stays
thin; the decision logic is in the aggregator. The headless OpenCV build has no
preview window, which is why emissions go to stdout rather than an overlay.

## Eval harness (`app/recognize/eval.py`)

`evaluate_folder()` recognizes every `{card_id}__*.jpg` labelled image in a
folder and reports the full scorecard: top-1 / top-k accuracy, `artwork_top1`
and `artwork_top1_accuracy` (the top pick's card *name* equals the truth
card's — name equality is the same-artwork proxy used throughout v2, so
"right artwork, wrong printing" is scored apart from "wrong card"), verdict
counts per status (`confident` / `uncertain` / `ambiguous` / `no_match` /
`no_card_detected`), `wrong_confident` (a `CONFIDENT` verdict whose top pick
is not the truth — the one number that must never grow, because a confident
mistake is the only failure the UI cannot walk back), `abstained` (`no_match`
or `no_card_detected`), mean confidence, mean margin over the runner-up, a
`latency_ms` summary (`p50` / `p95` / `mean` / `max` over the per-image
timings, nearest-rank percentiles so no NumPy is needed) and the list of
misses. It is the acceptance instrument for the recognition milestones: it
makes a change's effect on real photos measurable rather than guessed. Run it
with `python main.py eval <folder>`; `--out PATH` writes the same JSON report
to a file as well, so two runs can be diffed instead of eyeballed.

Labelled test sets live under `data/eval/` (gitignored), all scored the same
way: `synthetic/` and `scene/` come from
`scripts/make_synthetic_eval.py` (PIL-based distortions, deliberately
independent of the training augmentations). `--scene` additionally places each
damaged card on a cluttered background at a random scale and tilt, with 25%
turned 90/180/270° (`data/eval/scene`, files `{card_id}__scene.jpg`), so it
exercises detection and orientation handling rather than only the signals.
`real/` holds real-world listing photos named `{card_id}__*.jpg`. Labelled
photos produced by the acquire/annotate pipeline (see
[`docs/real-photo-data.md`](../docs/real-photo-data.md) and
[training](training.md)) use the same naming and can be scored the same way.

## Load bench (`app/recognize/bench.py`)

`run_bench()` answers what the accuracy scorecard cannot: how long one
recognition takes on this machine, and what happens to throughput when several
run at once. It builds one recognizer via the same factory the API uses (which
warms it), loads the image once, runs its own untimed `identify` on the actual
bench image before measuring — the factory's noise frame warms the models, this
one also warms the caches this image will touch — then drives `requests` timed
recognitions through a
`ThreadPoolExecutor(max_workers=concurrency)` and reports `wall_s`,
`throughput_rps` and `latency_ms` (`p50`/`p95`/`max`, via the same
`latency_summary()` the eval report uses). Run it with
`python main.py bench IMAGE [--requests N] [--concurrency C]`. This is the
measurement that justifies the ONNX thread counts and
`IDENTIFY_MAX_CONCURRENCY`: if concurrency 2 does not beat concurrency 1 on
`throughput_rps`, the cap belongs at 1.

## Changing this area

- Keep routes thin: no recognition logic in `routes.py` — extend the pipeline
  instead. The API test (`tests/test_api.py`) uses `TestClient` with a fake,
  index-free recognizer and asserts the happy path, a bad upload, lookups, and
  load shedding past the concurrency cap (`Retry-After` included).
- New config values follow the settings checklist in [workflow](workflow.md):
  an accessor in `app/core/config.py` and a key in `.env.example`.
- CLI output goes to stdout via `sys.stdout.write` (results are the product);
  logging stays on stderr.
