---
title: Recognition pipeline
sources: ["app/models.py", "app/vision/**", "app/signals/**", "app/variants/**", "app/recognize/pipeline.py", "app/recognize/fusion.py", "app/recognize/rerank.py", "app/recognize/temporal.py", "app/core/constants.py"]
read-when: "changing detection/rectification, any recognition signal (hash, embedding, OCR, symbol), the fusion/confidence maths, variant detection, or webcam aggregation"
verified: 388a26a04ef7
---

# Recognition pipeline

How one image becomes a ranked, scored answer. The orchestrator is
[`app/recognize/pipeline.py`](../app/recognize/pipeline.py) (`Recognizer`);
every heavy collaborator is injected, so tests drive it with fakes and the
CLI/API build it once (see [service-and-cli](service-and-cli.md)).

```
image → detect card quad (+ sub-pixel refit) → orientation/framing hypotheses
      → choose view (hash vote, else embedding vote) → 630×880
      → dense signals ∥ OCR (strip + title) → score the union exactly
      → resolve cards → set symbol → fuse (+ name boost)
      → printing discriminator (ambiguous only) → variants
```

All tuning knobs (region boxes, hash config, fusion weights/thresholds, temporal
window) live in [`app/core/constants.py`](../app/core/constants.py) — change
behaviour there, not with scattered literals.

## Detection and rectification (`app/vision/`)

- [`detect.py`](../app/vision/detect.py) finds the card's quadrilateral with a
  classic OpenCV approach (bilateral blur → auto-Canny → contours → largest
  convex 4-gon that passes `is_plausible_card`). No trained model, so new
  sets cost nothing here. Detection runs on a copy capped to `DETECT_MAX_SIDE`
  (edges need contrast, not resolution) and the quad is scaled back to source
  coordinates, so rectification still samples the original pixels. Canny
  thresholds are median-adaptive (`CANNY_MEDIAN_LO/HI` × the blurred image's
  median) — fixed literals missed card edges entirely in dim scenes. When a raw
  contour isn't a clean convex 4-gon (rounded corners, glare nicks, fingers),
  its convex hull is re-approximated at progressively looser tolerances
  (`_HULL_EPSILONS`) before giving up. Frames that still fail detection can be
  dumped for inspection via `SCAN_DEBUG_DIR` (see `.env.example`).
- [`refine.py`](../app/vision/refine.py) refits the accepted quad before it is
  ordered and scaled. `approxPolyDP` snaps to contour vertices on the dilated
  Canny edge and rounds off at the card's rounded corners, so the warp clips or
  pads a few pixels per side and shifts every fractional crop box.
  `refine_quad` instead fits each side as a robust line through the strongest
  gradient peaks found along its normal (`REFINE_SAMPLES` points, searching
  `REFINE_SEARCH_FRACTION` of the side either way, only gradients across *this*
  side so artwork edges don't compete) and intersects adjacent lines for
  sub-pixel corners. It is a guarded improvement: a side shorter than
  `REFINE_MIN_SIDE_PX`, with peaks weaker than `REFINE_MIN_GRADIENT`, or whose
  median residual exceeds `REFINE_MAX_RESIDUAL_PX` keeps the contour quad, as
  does a refit moving any corner more than `REFINE_MAX_SHIFT_FRACTION` of the
  diagonal or losing convexity.
- Ingest is capped too: API uploads and CLI images pass through
  `imaging.cap_long_side(..., INGEST_MAX_SIDE)` before recognition — a 12MP
  photo adds nothing for a 630×880 rectification but multiplies every filter's
  cost.
- [`geometry.py`](../app/vision/geometry.py) is **pure NumPy** (corner ordering,
  area, aspect, plausibility) and carries the unit-tested maths.
  `order_corners` normalises *any* rotation to a portrait TL/TR/BR/BL: it walks
  the corners clockwise about the centroid, starts at a short side, and of the
  two short sides makes the higher one the top — so a card lying sideways is
  warped upright instead of squashed, an upright or tilted card keeps its
  orientation, and a card that is truly upside down comes out rotated 180° (the
  recognizer resolves that, below). `is_plausible_card` takes a quad if it fills
  `DETECT_MIN_AREA_FRACTION` of the frame, its aspect is in the card band **in
  either orientation**, and it is rectangle-like: every corner within
  `DETECT_MAX_CORNER_COSINE` of square and opposite sides within
  `DETECT_MIN_SIDE_RATIO` of each other, which rejects skewed hulls of
  background clutter whose aspect merely happens to fit.
  `is_sideways(quad)` says whether an ordered quad's top edge runs closer to
  vertical.
- [`rectify.py`](../app/vision/rectify.py) perspective-warps the quad to a
  canonical 630×880 card so every crop box lands in the same place. With no quad,
  a guided capture can recover the known inner card region from its symmetric
  `guide_margin`; an ordinary upload instead centre-crops the whole frame to card
  aspect. Guided fallback is deliberately stricter: an `UNCERTAIN` fusion result
  becomes `NO_MATCH`, so only a confident card bypasses perspective detection
  (an `AMBIGUOUS` result is exempt: the artwork did match, and the user can
  finish it by picking a printing).
  Strict callers can still set `require_detection=True` to receive
  `NO_CARD_DETECTED` rather than using either fallback. Which rectification to
  *use* is a hypothesis choice — see the next section.

## Orientation and framing hypotheses

Portrait normalisation leaves a quad upright or upside down, and detection can
lock onto the wrong thing, so `rectify.card_views` returns labelled candidate
views and `Recognizer._choose_view` picks one:

- `quad` and `quad-rot180` from a detected quad. If the quad is
  `is_sideways`, the whole-frame views stay in the running too (`frame`,
  `frame-rot180`): the landscape artwork window of an upright card that fills
  the frame looks exactly like a sideways card, and its outline touches the image
  edge so it is no contour. With no quad the views are the guide/whole-frame
  crop and its 180° twin; a landscape frame (width/height >
  `ORIENT_LANDSCAPE_RATIO`) with no guide adds `frame-rot90` / `frame-rot270`
  centre crops, since it may hold a sideways card.
- **Choice.** Every view is hashed and looked up; a *hash-decisive* winner
  (`ORIENT_HASH_DECISIVE` best score, leading by `ORIENT_HASH_MARGIN`) settles
  it with no embedding. Otherwise each view's full-card embedding is looked up
  and the best wins. The measured basis in `constants.py`: on scene + real
  photos a decisive hash vote never disagreed with the embedding and settled
  ~40% of images without embedding extra views.
- **Inset retry.** If the chosen `quad*` view's best full-card embedding score
  is below `INSET_RETRY_BELOW` (a true match is ~0.97, an unrelated card ~0.8),
  `rectify.inset_view` re-warps the quad shrunk by `RECTIFY_INSET_FRACTION` per
  side (for a detector that locked onto a sleeve or border) and keeps it if it
  scores higher; its label gains `+inset`.
- **Reuse.** The chosen `_View` carries its hash family and (when the embedding
  vote ran) its full-card embedding, so the dense stage doesn't recompute them;
  the common case costs only the extra hash lookups.
- `used_guide_fallback` (the stricter `UNCERTAIN → NO_MATCH` rule above) is
  derived from the *chosen* view's label — `frame*` with a `guide_margin` — not
  from whether a quad was found, since a frame view can beat a detected quad.
- [`regions.py`](../app/vision/regions.py) turns the fractional boxes in
  `constants.py` into crops: artwork window, bottom strip, title band
  (`title_band`, `TITLE_BAND_BOX`), symbol zone (era dependent), and the
  variant regions.
- [`imaging.py`](../app/vision/imaging.py) is the single place that turns files
  or upload bytes into the interchange format: a `uint8` RGB `(H, W, 3)` array
  (OpenCV's BGR is converted here).

## The signals (`app/signals/`)

Each signal scores catalogue cards in `[0, 1]` (the name read is a boost, not a
score). Concrete heavy implementations
are imported lazily; [`base.py`](../app/signals/base.py) declares the `Embedder`
and `OcrEngine` Protocols so pure-logic code and tests never load a model.

- **Hashes** ([`hashes.py`](../app/signals/hashes.py)) — a family of perceptual
  hashes (DCT `phash`, gradient `dhash`, per-RGB-channel `phash`). Matching is
  one XOR-and-popcount over byte-packed matrices; direct hex-to-byte loading
  keeps startup and memory proportional to the actual bits rather than an
  unpacked byte per bit. The closest hash across the family wins. Cheap and
  excellent on clean images, weak under glare/angle. Beside `query()`,
  `query_hashes(family)` scores the catalogue from an already-computed family
  and `scores_for(family, ids)` scores a named subset — both for the OCR filter
  pass below.
- **Embeddings** ([`embedding.py`](../app/signals/embedding.py)) — cosine
  retrieval over an L2-normalized matrix (`EmbeddingIndex`), one matmul, no ANN
  library. Two encoders share the `Embedder` interface: `OnnxEmbedder` (an
  exported DINOv2-small, robust) and `HistogramEmbedder` (pure-NumPy fallback,
  always available). Full-card and artwork-crop embeddings are separate signals,
  but run as **one batched forward pass**: `embed_images()` prefers an
  encoder's duck-typed `embed_batch` (the exported ONNX graph has a dynamic
  batch axis, probed at load) and falls back to looping `embed()`.
  `scores_for(vector, ids)` is its subset counterpart: one row gather, one
  matmul.
- **OCR** ([`ocr.py`](../app/signals/ocr.py)) — reads the bottom strip and
  parses the collector number and set code. `parse_collector_number`,
  `parse_set_code`, and `interpret_lines` are **pure and heavily tested**;
  `RapidOcrEngine` wraps the model. Because the input is an already rectified
  630×88 strip rather than a document, the text detector caps its long side at
  320px instead of enlarging the short side to RapidOCR's generic 736px default;
  it also skips the unnecessary 180-degree classifier and uses two ONNX workers
  to avoid contending with the concurrent embedder. OCR is never a hard *gate*
  (glare defeats it too often), but a successful read is both a retrieval path
  (next section) and a fusion multiplier. A parsed set code is only trusted
  if the catalogue actually prints it (`Recognizer._validate_set_code` against
  `store.known_set_codes()`): random uppercase scene text used to both skip the
  symbol signal and penalize every candidate via `is_useful`.
- **Name** ([`name.py`](../app/signals/name.py)) — OCR of the title band
  (`read_name`) matched against catalogue names (`NameMatcher`, built lazily
  from `store.card_names()`). It survives where the collector strip does not
  (glare on the bottom edge, a strip cut off by the frame) but names a *family*,
  not a printing. Matching runs on `name_key`, a space-free accent-free
  lowercase key, so a dropped space still hits. Every contiguous token window
  (≤5 tokens) of every line is tried, which discards stage labels and HP text
  sharing the line; lines starting `Evolves…` / `Put…` (another card's name) are
  skipped, as are stop tokens and bare numbers; the **longest exact window**
  wins (`Pikachu on the Ball` over `Pikachu`). Only if nothing hits exactly is a
  fuzzy match tried (`difflib`, ≥ `NAME_MATCH_MIN_SIMILARITY`), and only for
  keys of `NAME_FUZZY_MIN_LENGTH`+ characters — `Mew`/`Muk` are one slip from
  another real name and must be exact. The **family** is every card whose name
  contains the matched words as a whole-word run (`Charizard` covers
  `Charizard ex`, `Mew` does not cover `Mewtwo`). The result is a
  `NameObservation` (`models.py`; `strength` = similarity × OCR confidence).
- **Symbol** ([`symbol.py`](../app/signals/symbol.py)) — zero-mean normalized
  cross-correlation of the card's symbol-zone crop against per-set symbol
  templates, giving a per-set score the pipeline broadcasts to that set's
  candidates. Only consulted when OCR found no set code (the WOTC-era path).

## Union exact re-scoring (`_complete_union`)

Each dense signal returns only its own `RETRIEVAL_SHORTLIST` best cards, and
fusion reads "absent from a shortlist" as a score of 0 — so a card that is 51st
on hash but first on both embeddings would lose a fifth of its score for
nothing. `_complete_union` therefore builds the **union** of every candidate
any path surfaced and scores all of it exactly on every dense signal, via
`HashIndex.scores_for` and `EmbeddingIndex.scores_for` (one dot product / hash
distance per missing pair), appending to the dense lists so they reach fusion as
ordinary candidates. The union is:

1. every card in any dense shortlist;
2. the **OCR-consistent family** — `_ocr_consistent_ids` expands the read into
   every catalogue card printed with that number
   (`store.find_card_ids_by_number`), capped at `OCR_FILTER_MAX_CANDIDATES`
   (200);
3. the **name family** from the title read, capped at
   `NAME_FAMILY_MAX_CANDIDATES` (250).

Why the families: a reprint is a pixel-level near-duplicate of its siblings, so
the true printing can rank sixtieth on pixels and enter no shortlist while its
number (or name) pins it — and fusion can only re-weight candidates it was
handed. Two details keep it cheap and safe:

- **The query is computed once.** `_dense_signals` returns a `_Query` holding
  the hash family and both embedding vectors alongside the shortlists; the
  union pass reuses them instead of doubling the most expensive stage.
- **Families are capped.** A garbage read like `1` is shared by thousands of
  cards, and scoring them all would turn every misread into a full catalogue
  rescan; an over-cap family is skipped (it still multiplies whoever is already
  in the pool) and the signal stays a pure multiplier/boost.

## Latency inside `Recognizer.identify`

- With an injected `executor` (the factory passes a 2-worker
  `ThreadPoolExecutor`; `None` = serial), **OCR runs concurrently with the
  dense signals** — `_read_text` does the strip read and the title read as one
  executor task; ONNX Runtime and NumPy release the GIL, so wall time is
  roughly `max(dense, OCR)` instead of their sum. `RapidOcrEngine` serializes
  its shared reader with a lock.
- Candidate resolution is one chunked `store.get_cards(ids)` `IN` query, not a
  round-trip per id, and runs after the union pass so its additions resolve in
  that same query.
- Every `identify` emits a per-stage DEBUG timing line (`identify timings
  ms: ...`), and `LOG_LEVEL=debug` adds full per-scan diagnostics: detection
  outcome, each signal's top-5 with scores, the raw OCR lines and parsed
  number/set (plus how many cards agree), WOTC symbol-template scores, and the
  fused top-5 with per-signal contributions — enough to attribute a slow scan
  or see which signal pulled in a wrong pick.

## Fusion (`app/recognize/fusion.py`)

`fuse()` is **pure maths** — dictionaries and floats, no image code — so every
rule is directly testable:

- Dense signals contribute a weighted average, renormalized over the signals
  that actually ran, so a missing model never zeroes a card.
- OCR multiplies each candidate up when its collector number matches the read
  (`ocr_consistent_ids`) and down when it contradicts — never eliminating
  anyone. That same id set already joined the union pass, so by fusion time the
  consistent family is in the pool to be multiplied.
- The title name multiplies its family up by `1 + NAME_BOOST × strength`
  (`_name_factor`, `NAME_BOOST` = 0.25) and **never pushes anyone down**: a name
  identifies a family not a printing, and a misread name is common enough that
  a penalty would turn one bad read into a wrong answer, whereas a boost only
  settles near-ties.
- Confidence normalizes to the top raw score scaled by its absolute quality.
  The decision (`_decide`): `CONFIDENT` needs both
  `top >= CONFIDENT_THRESHOLD` **and** `top - runner_up >= CONFIDENT_MARGIN`;
  otherwise, from `top >= UNCERTAIN_THRESHOLD`, two candidates that share a
  `card.name` and sit inside `CONFIDENT_MARGIN` of each other give
  `AMBIGUOUS`, anything else `UNCERTAIN`; below the uncertain bar it is
  `NO_MATCH`.

`AMBIGUOUS` exists because "wrong printing of the right artwork" is a different
failure from "wrong card", and only the first one the user can finish. Equal
`card.name` is the same-artwork proxy throughout v2: reprints share a name and
the dense signals see one picture, so their scores *should* tie. What normally
separates them is OCR, which pulls its own printing into the pool and
multiplies it up while contradicting ones go down — so a tie here says OCR did
not read (glare, crop, low light), not that the signals disagree. That tie goes
to the printing discriminator below; only when it is inconclusive too does the
verdict stay `AMBIGUOUS` and the user pick, which is one tap. `_assemble` in
[`pipeline.py`](../app/recognize/pipeline.py) lists `AMBIGUOUS` as reportable
alongside `CONFIDENT`/`UNCERTAIN`, so the result still carries `match` +
`alternates` — the list the frontend's printing chooser renders (see
[scan-frontend](scan-frontend.md)).

The domain types it returns (`Candidate`, `RecognitionResult`, `SignalScore`,
`OcrObservation`, `NameObservation`) live in [`app/models.py`](../app/models.py);
their `as_dict()` methods produce the JSON the CLI and API emit.
`RecognitionResult.name` (API field `name`) is the title read that fed fusion.

## Printing discriminator (`app/recognize/rerank.py`)

An `AMBIGUOUS` verdict gets one more stage before the user is asked:
`_resolve_printing` hands the tie to `rerank_printings`, which re-scores the
leading run of same-name candidates (capped at `RERANK_TIE_CANDIDATES` = 4)
against each one's **cached reference image** (`ReferenceStore.image_paths`,
see [reference-data](reference-data.md)).

It compares only what *can* differ between printings of one illustration: the
bottom strip (collector number, set code, illustrator line) and both era
layouts' symbol zones — the query carries no era prior, so the best-agreeing
pair wins. The artwork window is excluded on purpose; identical by
construction, it could only dilute a decision about what surrounds it. Scoring
reuses the hash family from `hashes.py` with the aggregation inverted:
retrieval keeps the *closest* kind, discrimination *averages* the family,
because near-identical crops make every kind's small disagreement signal.
Strip and symbol combine as `RERANK_STRIP_WEIGHT` (0.7) +
`RERANK_SYMBOL_WEIGHT` (0.3). No model, no extra index: one reference decode
plus fifteen small hashes per candidate, only on the ambiguous path.

Two floors keep it honest:

- `RERANK_MIN_SCORE` (0.6) — when no readable reference resembles this
  capture's own strip and symbol at all (glare, a bad rectification, a stale
  cache), fusion's order is returned untouched rather than reshuffled on noise.
- `RERANK_PROMOTE_MARGIN` (0.10) — only a winner leading the runner-up by this
  much promotes the verdict to `CONFIDENT`; a smaller lead **reorders and stays
  `AMBIGUOUS`**, so the better guess heads the chooser but the user still
  decides.

An unreadable reference leaves its candidate unranked below the scored ones
rather than failing the scan, and `confidence`/`per_signal` are never
rewritten: the discriminator changes order and status, never fusion's
arithmetic.

## Variants (`app/variants/`)

Variant checks run **only on the winning card**, are rule-based CV (no training),
and each self-gates on era or a catalogue flag, returning `None` when it does not
apply. [`assess.py`](../app/variants/assess.py) runs them and keeps what fires:
reverse holo (foil texture in the card body,
[`reverse_holo.py`](../app/variants/reverse_holo.py)), 1st Edition stamp and
shadowless layout ([`wotc.py`](../app/variants/wotc.py)), and promo stamp
([`stamps.py`](../app/variants/stamps.py)). A single still is weak evidence for a
foil, so variant confidence is capped; the webcam path is the stronger signal.
Shared measurements are in [`features.py`](../app/variants/features.py).

## Temporal aggregation (`app/recognize/temporal.py`)

`TemporalAggregator` is **pure** — it consumes per-frame `RecognitionResult`s and
emits one when a card first becomes stable: it must win at least
`TEMPORAL_STABLE_VOTES` of a sliding window **and** clear the confidence bar via
an EMA. After a run of card-free frames (the card was removed) it resets, ready
for the next card. The webcam loop that feeds it is
[`webcam.py`](../app/recognize/webcam.py) (see [service-and-cli](service-and-cli.md)).

## Multi-frame bursts

When one frame is not decisive the scan UI sends several frames of the same card
to `POST /api/identify/burst` (see [service-and-cli](service-and-cli.md)).
`Recognizer.identify_burst` recognizes frames in order, each at a deeper list
(`max(top_k, BURST_FRAME_TOP_K)`) so a card one frame ranked low still counts,
and skips frames with no card. It **exits early** once a frame is `CONFIDENT`
and every frame so far has the same winner. Merging is
`fusion.combine_frames`: a card's confidence is its **mean over frames, with a
frame that didn't rank it counting 0**, so one glared frame that liked a
different card can't outvote two clean agreeing frames; status comes from the
same `_decide`. Two special cases: a **single usable frame** passes through
untouched (keeping a printing-discriminator promotion that confidences alone
can't express), and if **every frame was `CONFIDENT` on the combined winner**
the burst is promoted to `CONFIDENT` (the same reason). Variants, OCR and name
come from the frame most confident in the combined winner.

## Measured (CPU, this machine)

Baseline v2 before the orientation/refine/union/name/burst work versus after,
via `python main.py eval` on `data/eval/{real,synthetic,scene}` (see
[service-and-cli](service-and-cli.md)); `wrong_confident` was 0 in every run:

| Set | Before (top-1) | After (top-1) |
|---|---|---|
| real (28) | 27 | 28 |
| synthetic (20) | 20 | 20 |
| scene (150) | 101 | 127 |

The name signal alone added 5 confident-correct on scene with no top-1 change.
A 3-frame burst over 80 synthetic scene cards moved top-1 from 64 to 67 and
confident-correct from 35 to 40.

## Changing this area

- Reference images are low-res webp (~245×337, see
  [reference-data](reference-data.md)) — this is fine because every signal
  operates at or below that scale (embeddings 224px, hashes on the DCT's low
  frequencies, symbols 48px, the printing discriminator's strip/symbol crops).
  Don't add a signal that assumes hi-res references without revisiting that
  decision.
- Keep pure logic pure: `geometry`, `fusion`, `temporal`, and the OCR parsers
  have no heavy imports and are unit-tested directly — don't pull OpenCV or a
  model into them.
- Tune weights/thresholds in `constants.py`; the fusion tests assert the
  renormalization, OCR boost/penalty, and threshold behaviour.
- Region boxes are fractions of the canonical card — adjust them in
  `constants.py`, and every crop follows.
