"""Named constants: tuning knobs that are design decisions, not configuration.

Every magic number or fixed string that is shared, tuned, or load-bearing gets
a name here, grouped by domain, so reviewers see one overview of the knobs and
a change is one edit instead of a scavenger hunt.

The dividing rule between this module and :mod:`app.core.config`:

- varies per **environment** (endpoints, keys, log levels) → ``config.py``,
  read from the environment at runtime;
- a fixed **design decision** (limits, batch sizes, schedules) → a constant
  here, changed only through a reviewed code change.

A value used by exactly one module and meaningless outside it may stay a
module-level constant there; promote it here once a second module needs it or
it becomes something the team discusses.
"""

from __future__ import annotations

# A fractional box on a normalized image: (left, top, right, bottom), each in
# [0, 1] as a fraction of width/height. Independent of pixel size.
FractionBox = tuple[float, float, float, float]

# --- Canonical card geometry ------------------------------------------------
# A real Pokémon card is 63 mm x 88 mm. Rectified cards are warped to this pixel
# size before any signal runs, so every crop box below is resolution-independent
# and every card is compared at the same scale.
CARD_WIDTH_PX = 630
CARD_HEIGHT_PX = 880

# --- Card detection ---------------------------------------------------------
# A candidate contour is only accepted as a card when it is a convex quad that
# fills at least this fraction of the frame and has a plausible card aspect
# ratio (short side / long side, so sideways cards count). Values chosen to
# reject text blocks and table edges while tolerating perspective
# foreshortening.
DETECT_MIN_AREA_FRACTION = 0.05
CARD_ASPECT_MIN = 0.55
CARD_ASPECT_MAX = 0.85
# Shape gate: every corner within ~35 degrees of square (|cos| <= 0.57) and each
# pair of opposite sides within 1.6x of each other. Hand-held perspective stays
# well inside both; skewed hulls of background clutter do not.
DETECT_MAX_CORNER_COSINE = 0.57
DETECT_MIN_SIDE_RATIO = 0.6
# Quad refinement (app/vision/refine.py): each side is re-fitted through the
# gradient peaks found at REFINE_SAMPLES points, searching REFINE_SEARCH_FRACTION
# of the side length either way along its normal. A peak weaker than
# REFINE_MIN_GRADIENT (Sobel 3x3 on 8-bit gray; a visible border step is
# several hundred) is not evidence. A side whose peaks scatter more than
# REFINE_MAX_RESIDUAL_PX (median distance from the fitted line, at detection
# resolution) is texture, not a border, and a refit that moves any corner
# further than REFINE_MAX_SHIFT_FRACTION of the diagonal is a wrong lock; either
# way the contour quad is kept.
REFINE_SAMPLES = 24
REFINE_SEARCH_FRACTION = 0.02
REFINE_MIN_GRADIENT = 40.0
REFINE_MAX_RESIDUAL_PX = 1.0
REFINE_MAX_SHIFT_FRACTION = 0.03
REFINE_MIN_SIDE_PX = 20.0

# --- Orientation & framing hypotheses ----------------------------------------
# Portrait normalisation leaves a rectified card upright or upside down. The
# recognizer hashes every orientation and keeps the clearly strongest best
# match; when the hash vote is not decisive, the full-card embedding decides.
# Measured on 150 synthetic scene photos + 28 real photos: a decisive hash vote
# (>= 0.80, leading by >= 0.05) never picked a different orientation than the
# embedding did, and settled 40% of images without embedding extra views.
ORIENT_HASH_DECISIVE = 0.80
ORIENT_HASH_MARGIN = 0.05
# A frame wider than this (width / height) with no detected quad may hold a
# sideways card, so its 90 and 270 degree crops become hypotheses too.
ORIENT_LANDSCAPE_RATIO = 1.15
# Sleeve/border retry: when the chosen view's best full-card embedding score is
# below this (a true match scores ~0.97, an unrelated card ~0.8), the quad is
# shrunk by RECTIFY_INSET_FRACTION per side and kept if it matches better.
INSET_RETRY_BELOW = 0.92
RECTIFY_INSET_FRACTION = 0.03

# --- Region crops (fractions of the rectified card) -------------------------
# Upper-central artwork window: the most discriminative region shared by nearly
# all layouts. Used for the artwork-crop embedding signal.
ARTWORK_BOX: FractionBox = (0.06, 0.10, 0.94, 0.56)
# Bottom strip carrying the collector number (bottom-right) and, on modern
# cards, the set code (bottom-left). OCR reads this whole band.
BOTTOM_STRIP_BOX: FractionBox = (0.0, 0.90, 1.0, 1.0)
# Title band carrying the printed name, stage label and HP. Measured across
# eras (WOTC 1999 to SV 2024): the name sits between 3% and 12% of the card's
# height, starting a few percent in; the right quarter (HP, type icon) is cut.
TITLE_BAND_BOX: FractionBox = (0.04, 0.025, 0.74, 0.125)
# RapidOCR's document-oriented default enlarges the short side of every input
# to 736 px. Our strip is already rectified and only 88 px tall, so that default
# creates a wasteful ~5,270 px-wide detector tensor. A 320 px long-side cap
# retains collector-number accuracy while keeping the detector input compact.
OCR_DETECT_MAX_SIDE = 320
# OCR overlaps the DINO encoder in the recognition pool. Two intra-op workers
# keep the small detector busy without both ONNX sessions oversubscribing the
# machine; the graph itself has no useful inter-op parallelism.
OCR_INTRA_OP_THREADS = 2
OCR_INTER_OP_THREADS = 1
# Set-symbol location differs by era: modern cards place it near the number at
# the bottom; WOTC-era cards place it on the right, beside the description text.
SYMBOL_ZONE_MODERN: FractionBox = (0.55, 0.905, 0.82, 0.99)
SYMBOL_ZONE_WOTC: FractionBox = (0.80, 0.57, 0.975, 0.72)
# Card body outside the artwork, used to judge the reverse-holo foil texture.
REVERSE_HOLO_BODY_BOX: FractionBox = (0.06, 0.60, 0.94, 0.88)
# WOTC 1st Edition stamp sits at the lower-left of the art frame.
FIRST_EDITION_STAMP_BOX: FractionBox = (0.05, 0.40, 0.24, 0.62)
# Right edge of the art frame, where a non-shadowless card casts a drop shadow.
ART_FRAME_RIGHT_BOX: FractionBox = (0.86, 0.14, 0.98, 0.52)
# Lower-left of the artwork, a common promo-stamp position.
PROMO_STAMP_BOX: FractionBox = (0.06, 0.44, 0.36, 0.58)

# --- Ingest & detection performance ------------------------------------------
# Uploads are capped to this long side before recognition: a 12MP phone photo
# adds no signal for a 630x880 rectification but multiplies every filter and
# warp's cost. 2000px keeps rectification ~3x oversampled even when the card
# occupies only half the frame.
INGEST_MAX_SIDE = 2000
# Card detection runs on a copy capped to this long side; the found quad is
# scaled back to source coordinates so rectification still samples the original.
# bilateralFilter and Canny are O(pixels): 960px finds the same quads an order
# of magnitude faster on photos and leaves webcam frames (1080p) barely touched.
DETECT_MAX_SIDE = 960
# Canny thresholds as fractions of the blurred image's median brightness
# (classic auto-Canny). Fixed literals (50/150) miss card edges entirely in dim
# scenes — real webcam sessions showed detection never firing; median-relative
# thresholds track the scene's actual brightness. A flat image still yields no
# edges (zero gradient), so blank frames keep returning "no card".
CANNY_MEDIAN_LO = 0.66
CANNY_MEDIAN_HI = 1.33

# --- Perceptual hashing -----------------------------------------------------
# Hash side length; the hash is HASH_SIZE**2 bits. 16 → 256-bit hashes, a good
# balance between discrimination and tolerance to resampling.
HASH_SIZE = 16
HASH_BITS = HASH_SIZE * HASH_SIZE

# --- Embedding retrieval ----------------------------------------------------
# Square input the ONNX encoder expects (DINOv2 uses multiples of 14; 224 is the
# standard). The fallback HistogramEmbedder ignores this and uses its own size.
EMBED_INPUT_SIZE = 224
# The embedder is one large graph per request; intra-op parallelism is where its
# time goes. 4 leaves headroom beside the 2 OCR threads on a 6-core box.
EMBED_INTRA_OP_THREADS = 4
EMBED_INTER_OP_THREADS = 1
# Index build embeds reference images in batches: one session run per batch
# instead of two per card. 32 x 2 crops keeps peak memory well under 200 MB.
INDEX_EMBED_BATCH = 32
# How many top matches each dense signal (embedding, hash) contributes to fusion.
RETRIEVAL_SHORTLIST = 50
# OCR-filter second pass: when OCR reads a collector number, every catalogue
# card printed with that number is scored directly, even if it missed the dense
# shortlists — the right printing is often a near-duplicate that ranks 60th on
# pixels but is uniquely pinned by its number. Capped so a garbage read (a
# number thousands of cards share, e.g. "1") cannot turn the pass into a full
# rescan of the catalogue.
OCR_FILTER_MAX_CANDIDATES = 200

# --- Signal fusion ----------------------------------------------------------
# Weights of the dense similarity signals; renormalized over whichever signals
# are actually present for a given image (a missing model must not zero a card).
WEIGHT_EMBED_FULL = 0.40
WEIGHT_EMBED_ART = 0.30
WEIGHT_HASH = 0.20
WEIGHT_SYMBOL = 0.10
# OCR is never a hard gate (glare defeats it); it multiplies a candidate's score
# up when the parsed number/set agrees and down when it contradicts.
OCR_BOOST = 0.5
OCR_PENALTY = 0.4
# Title-name read (app/signals/name.py): a boost-only multiplier on the matched
# name family, scaled by match similarity x OCR confidence. Smaller than the
# collector-number boost because a name identifies a family, not a printing.
NAME_BOOST = 0.25
# Fuzzy name matches need at least this difflib similarity, and are only tried
# for keys of NAME_FUZZY_MIN_LENGTH+ characters: short names ("Mew", "Muk")
# are one OCR slip away from another real name, so they must hit exactly.
NAME_MATCH_MIN_SIMILARITY = 0.85
NAME_FUZZY_MIN_LENGTH = 6
# Name families above this size are not added to the candidate union (each
# costs a lookup per signal); the boost still applies to whoever is in it.
NAME_FAMILY_MAX_CANDIDATES = 250
# Multi-frame burst (/api/identify/burst): the scan UI sends up to this many
# frames of one card when a single frame was not decisive. Each frame is
# recognized with a deeper candidate list so a card one frame ranked low still
# counts towards the per-card mean confidence.
BURST_MAX_FRAMES = 4
BURST_FRAME_TOP_K = 10
# Decision thresholds on the normalized confidence of the top candidate.
CONFIDENT_THRESHOLD = 0.80
CONFIDENT_MARGIN = 0.10
UNCERTAIN_THRESHOLD = 0.50
# Default number of candidates returned to the caller.
DEFAULT_TOP_K = 5

# --- Webcam temporal aggregation --------------------------------------------
# Sliding window of recent per-frame results and the vote/quality bar a card
# must clear before a stable identification is emitted.
TEMPORAL_WINDOW = 6
TEMPORAL_STABLE_VOTES = 4
TEMPORAL_EMA_ALPHA = 0.4
# Consecutive card-free frames that reset the aggregator (card removed).
TEMPORAL_RESET_AFTER_EMPTY = 8
# Target frames-per-second to actually process from the capture stream.
SCAN_TARGET_FPS = 5

# --- Reference sync ---------------------------------------------------------
# TCGdex is latency-bound (~1.4 s/request) and tolerates well above this
# comfortably (verified at 32 during training-data fetch); 12 keeps a full
# catalogue sync under an hour while staying polite.
SYNC_CONCURRENCY = 12
SYNC_MAX_RETRIES = 3
SYNC_BACKOFF_SECONDS = 1.0
HTTP_TIMEOUT_SECONDS = 30.0
# TCGdex serves images from a base URL with no extension; append quality and
# extension. "low" + "webp" (~245x337 lossy, ~60 KB) is deliberate: every
# consumer of a reference image (224px embeddings, perceptual hashes, 320px UI
# thumbnails, 48px symbol templates) works at or below this size, and the full
# catalogue then costs ~1.5 GB instead of ~18 GB. Beware: the extension drives
# the encoding — "low.png" is still 600x825 LOSSLESS (~900 KB per card, filled
# the dev drive twice); only the webp variants are actually small.
TCGDEX_IMAGE_QUALITY = "low"
TCGDEX_IMAGE_EXTENSION = "webp"

# --- API image serving --------------------------------------------------------
# Width of the derived JPEG thumbnails the API serves to the scan UI; small
# enough to load instantly, large enough to be sharp at confirmation-sheet size.
CARD_THUMB_WIDTH = 320
# Waiting longer than this for a free recognition slot is worse UX than a fast
# 503 the client retries: the user would rather re-aim the card than stare.
IDENTIFY_QUEUE_TIMEOUT_SECONDS = 2.0

# --- Real-photo annotation --------------------------------------------------
# The annotator labels acquired photos from the printed collector number alone,
# so a low-confidence strip read (glare, blur, a crop that missed the strip) is
# the one failure mode a training set must not have: it produces a confidently
# wrong label. RapidOCR scores clean rectified-strip text well above this, so
# below it the read is a guess and the image is rejected rather than labelled.
PHOTO_LABEL_MIN_OCR_CONFIDENCE = 0.60
# Caps the candidate-id evidence list written to a review row. A garbage read
# like "1" matches thousands of cards, and a review row has to stay a one-look
# decision instead of a four-thousand-id dump.
PHOTO_LABEL_MAX_CANDIDATES = 20

# --- Card era ---------------------------------------------------------------
# Cards released in or before this year are treated as WOTC-era for variant
# gating (1st Edition / shadowless checks) and set-symbol placement.
WOTC_ERA_UNTIL_YEAR = 2003
ERA_WOTC = "wotc"
ERA_MODERN = "modern"

# --- Variant detection (heuristic, rule-based CV) ---------------------------
# These thresholds are empirical and tunable. A single still photo is weak
# evidence for a foil, so variant confidence is capped; the webcam path, which
# aggregates specular evidence across frames, is the stronger signal.
VARIANT_SINGLE_STILL_CONF_CAP = 0.85
# Reverse holo: high-frequency (foil sparkle) energy in the card body, above
# which the body is judged foiled. Energy is normalized by this scale.
REVERSE_HOLO_ENERGY_SCALE = 0.02
REVERSE_HOLO_MIN_ENERGY = 0.006
# 1st Edition: the black stamp is a compact dark mark; accept a dark-pixel
# fraction inside this band together with visible edges. The edge bar is far
# lower than the foil energy above — a stamp outline, not a sparkle field.
FIRST_EDITION_DARK_MIN = 0.04
FIRST_EDITION_DARK_MAX = 0.45
FIRST_EDITION_MIN_EDGE_ENERGY = 0.002
# Shadowless: the drop shadow right of the art frame raises brightness spread;
# below this standard deviation the card is judged shadowless.
SHADOWLESS_MAX_STD = 0.06
# Promo stamp: minimum blob score before asserting a stamp is present.
PROMO_STAMP_MIN_SCORE = 0.35

# --- Printing discriminator (second stage for AMBIGUOUS) --------------------
# Fusion's dense signals are dominated by the artwork, so two printings of one
# artwork tie. The discriminator re-scores that tie on the only regions that can
# differ: the bottom strip (collector number, set code, illustrator/copyright
# line) and the set-symbol zone. The strip is a large, text-dense band carrying
# three independent discriminators; the symbol zone is tiny and often identical
# between two printings in one set family, but it is the only discriminator a
# WOTC-era card has (its strip prints no set code). Hence 0.7 / 0.3, summing to 1
# so the combined score stays a similarity in [0, 1].
RERANK_STRIP_WEIGHT = 0.7
RERANK_SYMBOL_WEIGHT = 0.3
# Below this combined similarity the reference crops do not look like the
# query's own strip and symbol at all (glare, a bad rectification), so a reorder
# would be noise and fusion's order is kept. Two random 256-bit hashes already
# agree on ~0.5 of their bits, so this sits just above chance, while two
# printings of one artwork - near-identical layout - score far above it.
RERANK_MIN_SCORE = 0.6
# Leading same-name candidates the discriminator scores. Each costs one
# reference-image decode plus 15 hashes, so four bounds the worst case for a
# caller that asks for a long candidate list, without truncating any realistic
# same-artwork tie (the UI shows at most DEFAULT_TOP_K options anyway).
RERANK_TIE_CANDIDATES = 4
# Margin (winner minus runner-up discriminator score) above which the printing
# discriminator is trusted to settle an ambiguous tie outright. Measured on
# synthetic references: a clean strip match against a mismatch separates by
# ~0.34, while four mutually mismatching printings separate by ~0.03, so 0.10
# sits well clear of noise. Below it the tie is only reordered and the caller
# still chooses.
RERANK_PROMOTE_MARGIN = 0.10
