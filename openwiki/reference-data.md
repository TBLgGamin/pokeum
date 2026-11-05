---
title: Reference data & index
sources: ["app/reference/**", "scripts/export_embedder.py"]
read-when: "changing TCGdex sync, the SQLite catalogue schema, image caching, index build/load, or the ONNX embedder export"
verified: 388a26a04ef7
---

# Reference data & index

The catalogue and precomputed artifacts that recognition matches against. This
layer is what makes **a new set a pure data operation, never training** — the
core promise of the project (see [quickstart](quickstart.md)).

Everything lives under `DATA_DIR` (default `./data`, gitignored):

```
data/
  reference.db        SQLite catalogue: sets, cards, variant flags, perceptual hashes, meta
  images/{set}/*.webp cached card images (low-quality webp — see note below)
  symbols/{set}.webp  cached set-symbol images (for the WOTC symbol signal)
  thumbs/*.jpg        lazily derived 320px thumbnails the API serves to the scan UI
  index/emb_full.npy  full-card embedding matrix   } row order in
  index/emb_art.npy   artwork-crop embedding matrix } row_ids.json
  index/row_ids.json
  index/manifest.json encoder id, rows, dim — the "this index is complete" receipt
  models/*.onnx       exported encoder (EMBED_MODEL_PATH; default dinov2s-ft-v1.onnx)
```

**Image quality is deliberately `low` + `webp`** (~245×337, ~60 KB/card): every
consumer works at or below that size (224px embeddings, hashes, 320px thumbs,
48px symbols), and the full ~22k-card catalogue costs ~1.5 GB instead of ~18 GB.
Trap to remember: on TCGdex the *extension* drives the encoding — `low.png` is
still 600×825 lossless (~900 KB); only the webp variants are small.

**Servers don't need `thumbs/`, and need `images/` only for the discriminator**:
each card row stores its TCGdex CDN `image_url` (surfaced through `CardRef` and
the API), and the thumbnail endpoint redirects there when no local image
exists. `images/` is a build-time input for `index build` — and, since the
printing discriminator landed, a *query-time* one: an `AMBIGUOUS` scan rehashes
the tied candidates' cached renders to tell two printings apart (see
[recognition-pipeline](recognition-pipeline.md)). The degradation is graceful,
not fatal — with no cached file the discriminator is skipped and the verdict
stays `AMBIGUOUS` for the user to settle — but a deployment that drops
`images/` gives up every automatic printing decision. The irreducible minimum
is `reference.db`, `index/`, `symbols/` (the symbol signal loads them at
startup), and the ONNX model.

## The store (`app/reference/store.py`)

`ReferenceStore` is a thin, synchronous wrapper over the standard-library
`sqlite3` — no ORM, no network — so it is trivially testable against a temp-file
database. It owns the schema (`sets`, `cards`, `hashes`, `meta`) and every
read/write. It holds only small, queryable data; the large embedding matrices
live as `.npy` files instead.

Two details matter downstream:

- **`number_key`** — collector numbers are stored normalized (leading zeros
  stripped) so OCR's `025` matches the catalogue's `25`.
  `find_card_ids_by_number(number, total)` powers OCR fusion's consistency set.
- **`era`** — derived from the set's release year (`era_for_year`), used to gate
  variant checks and pick the set-symbol zone. WOTC-era ≈ released ≤ 2003.
- **Cached-file paths are respelled on read** — `image_path` and `symbol_path`
  are written with the separator of whichever machine ran the sync, so a
  `reference.db` built on Windows carries backslashes. `_portable_path` rewrites
  them for the running platform as they leave the store. Without it a
  Windows-built catalogue shipped to a Linux server silently loses every
  set-symbol template (POSIX reads `\` as a filename character, not a
  separator), killing the symbol signal while the rest of the pipeline looks
  healthy — exactly what happened in production before v1.7.1.
- **Batched reads for the hot path** — `get_cards(ids)` resolves a shortlist in
  chunked `IN` queries (one per 500 ids, not one per id), `image_paths(ids)`
  does the same for the printing discriminator's handful of tied candidates
  (cards with no cached file are simply absent, like unknown ids), and
  `known_set_codes()` returns the printed set codes the pipeline uses to
  validate OCR output (see [recognition-pipeline](recognition-pipeline.md)).

The connection is opened `check_same_thread=False`: the FastAPI service runs sync
routes in a thread pool and sync uses worker threads. Access is read-mostly and
CPython's `sqlite3` serializes calls, so sharing one connection is safe.

## Sync (`app/reference/sync.py`)

`sync()` pulls from [TCGdex](https://tcgdex.dev) — a free, keyless catalogue —
into the store and image cache. It is **incremental and idempotent**: a set whose
card count already matches is skipped, an image already on disk is not
re-downloaded, and every record is upserted so re-running only fills gaps.

- [`tcgdex.py`](../app/reference/tcgdex.py) separates transport
  (`TCGdexClient`, with bounded retries) from interpretation (pure `parse_set` /
  `parse_card` functions, unit-tested with no network). It also builds the
  extension-less image/symbol URLs TCGdex serves.
- Per-card detail fetches (for variant flags and rarity) and image downloads run
  through a bounded `ThreadPoolExecutor`; **all store writes happen on the main
  thread** — workers only do network/file IO and return data.
- [`images.py`](../app/reference/images.py) owns the on-disk layout and writes.

The fallback catalogue API is pokemontcg.io (different response shape — not a
drop-in); TCGdex is preferred.

## Index build & load (`app/reference/index.py`)

`build_index()` computes what recognition needs and persists it:

- **hashes** — the perceptual-hash family per card, written into the store;
- **embeddings** — full-card and artwork-crop vectors for every card, appended to
  the `.npy` matrices with a shared `row_ids.json`.

It is incremental: only cards missing hashes/embeddings are processed, and those
embeddings are computed in batches of `INDEX_EMBED_BATCH` cards (one encoder run
per batch instead of two per card). Matrix writes are atomic (temp file then
`os.replace`), and `index/manifest.json` is written **last**, as the receipt
that the artifacts are complete and belong to one encoder — keys
`manifest_version`, `embedder_id`, `dim`, `rows`, `input_size`, `artwork_box`,
`built_at`. A crash mid-write leaves a stale manifest, never a lying one.

Swapping the encoder changes `Embedder.identifier` — now
`onnx:<filename>:<sha256[:12]>`, so different weights under a reused filename
still count as a swap — and forces a full rebuild, because embeddings from
different encoders are not comparable. There is no `meta` key involved any more:
`load_embedding_indexes(data_dir, embedder_id)` validates against the manifest
and returns `(None, None)` — disabling the embedding signals with a warning
`embedding index unusable (<reason>)` — when the manifest is missing, unreadable,
of an unsupported `manifest_version`, or disagrees with the caller's
`embedder_id`, the row count, or the matrix width. `load_hash_index()` returns
the query-time hash index.

**The new-set workflow is therefore just:** `sync` (or `sync --set X`) then
`index build`. Minutes, no training.

## The encoder and `scripts/export_embedder.py`

The embedding signal uses a **frozen** encoder. `load_embedder()` returns an
`OnnxEmbedder` when `EMBED_MODEL_PATH` exists, else the pure-NumPy
`HistogramEmbedder` fallback (see [recognition-pipeline](recognition-pipeline.md)).

To get the robust path, export an encoder to ONNX **once** on a machine with
PyTorch (e.g. a training box) using the dev-only
[`scripts/export_embedder.py`](../scripts/export_embedder.py) — PyTorch is never
a runtime dependency. The default exports DINOv2-small (ViT-S/14, 384-d):

```
python scripts/export_embedder.py --out data/models/dinov2s.onnx
python main.py index build --full     # rebuild embeddings with the new encoder
```

The optional one-time fine-tune of that encoder for photography robustness
(synthetic glare/perspective/blur) lives in `training/` and is documented in
[training](training.md) — it learns *invariance to photography*, not card
identities, so new sets still need no training.

## Changing this area

- Schema changes go in `store.py`'s `_SCHEMA` with a bumped `SCHEMA_VERSION`;
  keep the `number_key`/`era` derivations in sync with OCR/variant expectations.
- Sync parsing is defensive against TCGdex field variation — extend the pure
  `parse_*` functions and their tests (`tests/test_sync.py` uses
  `httpx.MockTransport`, fully offline).
- Never make tests hit the network or download models; inject fakes and use
  synthetic images (`tests/test_index.py`).
