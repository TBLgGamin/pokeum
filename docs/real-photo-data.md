# Real photo data: why labelled real photos are the missing piece

The encoder fine-tune (`training/`, rationale in `openwiki/training.md`) is
**metric learning over exact printings**. Every training row needs a label at
`card_id` granularity — not "a Pokémon card", not "Charizard", but
`base1-4` as distinct from `base2-4` — because that is the distinction
recognition has to make at inference: similarity is always
`query(photo) x gallery(clean render)` of *one printing*.

Today both views of a pair come from the official render (`training/fetch_data.py`
downloads them, `training/augment.py` simulates the photography). Real photos are
the one thing simulation cannot supply: the actual distribution of glare,
sleeves, phone optics, and hand tilt. So the question is where labelled real
photos come from.

## The public options, and why none of them substitutes

| Source | Size | Label it carries | Usable as fine-tune data? |
| --- | --- | --- | --- |
| Roboflow [`card-scanner-e4knh/pokemon-card-identification`](https://universe.roboflow.com/card-scanner-e4knh/pokemon-card-identification-htpkd) | 5,058 images (CC BY 4.0) | object detection: `pokemon_card` bounding boxes | No — a box says *where* a card is, never *which* printing |
| Kaggle [`ongshujian/real-and-fake-pokemon-cards`](https://www.kaggle.com/datasets/ongshujian/real-and-fake-pokemon-cards) | ~400 images | real vs. counterfeit | No — a binary authenticity class, no printing identity |
| Kaggle [`priyamchoksi/pokemon-cards`](https://www.kaggle.com/datasets/priyamchoksi/pokemon-cards), [`ellimaaac/pokemon-tcg-all-image-cards`](https://www.kaggle.com/datasets/ellimaaac/pokemon-tcg-all-image-cards), HF [`TheFusion21/PokemonCards`](https://huggingface.co/datasets/TheFusion21/PokemonCards) (13.1k rows of `image_url`/`caption`/`name`/`set_name`) | whole-catalogue scrapes | card id / attributes, on **official renders** | No — these *are* the clean art `python main.py sync` already downloads from TCGdex; they duplicate the gallery side of the pair and contain no photo domain at all |

The scale gap is just as decisive as the label gap. The catalogue this app
indexes is ~21.7k printings (`data/index/manifest.json` `rows`). Even if the
5,058-image detection set were labelled by printing, that is 0.23 photos per
printing — and it is not labelled by printing.

There is no public Pokémon dataset of *photographs labelled with the exact
printing*, because producing one means a human confirming set and collector
number per photo.

## What unlabelled real photos *are* good for

1. **Detection and rectification robustness.** `app/vision/detect.py` +
   `app/vision/rectify.py` only need "there is a card in this frame"; a
   bounding-box dataset is directly usable as a test set for quad-finding on
   cluttered backgrounds, held cards, and sleeves. This is the one legitimate use
   of the Roboflow set here.
2. **Self-supervised domain adaptation.** DINO-style SSL needs no labels, so
   unlabelled card photos can pre-adapt the backbone to photo statistics before
   the supervised, printing-labelled fine-tune runs on top. Complementary to the
   fine-tune, never a replacement for it.
3. **Nothing in the identity-metric objective.** Without an exact-printing
   label, a photo can be neither a positive pair nor a mined hard negative. The
   only way to use it there is a pseudo-label — the model's own guess — which
   trains the encoder on its own mistakes.

## Volume needed to matter

Design targets, not measurements — the point at which each tier becomes
worthwhile follows from what the numbers can resolve, and the current labelled
real set is 28 images:

| Labelled real photos | What it unlocks |
| --- | --- |
| ~300-500 | A **real-photo eval set**. The binomial standard error on a top-1 rate drops from about 9pp at n=28 (`sqrt(0.25/28)`) to about 2.5pp at n=400, which is the first point at which a one-point accuracy change is not noise. Accuracy claims stop resting on synthetic damage. |
| ~2,000-5,000 | **Mixed-view training**: replace a fraction of the simulated photo views with real ones, so glare, sleeve reflection, and tilt come from the real distribution instead of `augment.py`'s model of it. |
| ~20,000+ (about one per printing) | **Real photo x render positives across the catalogue**, plus enough verified hard negatives for genuine hard-negative mining rather than random negatives. |

Each tier is reached by acquiring and annotating photos with the pipeline below.

## Acquiring and annotating at scale (measured, not projected)

Acquisition is `scripts/acquire_photos.py` (sources, licences and the ledger:
[photo-acquisition.md](photo-acquisition.md)); annotation is
`scripts/annotate_photos.py`. The division of labour is the point:

1. **Acquire** → `data/photos/staging/<source>/` plus an append-only
   `ledger.jsonl` carrying each image's licence, attribution and source URL, so
   provenance is a query and a takedown is a ledger lookup.
2. **Annotate automatically** — detect, rectify, read the printed collector
   number, resolve it against the catalogue. The annotator never builds a
   recognizer and never touches the embedding index: a label from the model that
   will be trained on it is a pseudo-label that teaches the encoder its own
   mistakes. The printed number plus the catalogue is an *independent* labeller.
3. **Adjudicate the remainder with vision agents.** Whatever the number cannot
   settle alone lands in `review.jsonl` with its candidate printings and the
   staged image path. An agent (or human) looks at the photo, picks a candidate
   or reports what it read, and `--apply-decisions` writes the labels back.

### Measured on the existing 28-photo real set

The whole loop was run against `data/eval/real`, staged under neutral filenames
so no truth could leak into the hints:

| Stage | Labelled | Precision |
| --- | --- | --- |
| Automatic (number + catalogue) | 13 / 28 (recall 0.464) | **1.000** (13/13) |
| After vision-agent adjudication of the 11 review rows | 24 / 28 (recall 0.857) | **1.000** (24/24) |

Three agents adjudicated the queue in parallel; all 11 rows were decided
correctly, 9 by choosing a candidate (the tied printings differ by Pokémon name,
which is legible in the photo) and 2 by reporting a read that the catalogue
resolved. The 4 remaining photos were *rejected*, not mislabelled: no card quad
and nothing read at all. Reproduce the automatic half with
`python scripts/annotate_photos.py --validate-against data/eval/real`.

Two properties make this safe to scale:

- **An adjudicator never supplies a card id it invented.** A decision row may
  carry `card_id`, but it may instead carry `number` / `number_total` /
  `set_text`, and `resolve_adjudicated_read` maps that to an id through the
  catalogue — an ambiguous read is skipped with a counted reason rather than
  guessed.
- **Precision is protected, recall is the variable.** Every rung of the ladder
  refuses rather than guesses (sub-threshold OCR confidence, unknown ids, and
  ties with nothing to settle them), which is why automatic precision is 1.000
  at recall 0.464. Agents raise recall; they cannot lower precision below what
  their own reading accuracy allows, and nothing is accepted without either a
  catalogue-resolved number or a candidate the catalogue offered.

### What this does *not* solve

Volume still comes from somewhere. The survey behind
[photo-acquisition.md](photo-acquisition.md) found that the large marketplace
sources are closed to this use (eBay's API licence explicitly forbids using its
content to train models), the openly licensed sets carry no printing labels, and
what remains is ~500-2,000 curated community photos.
The pipeline above is what makes those photos cheap to label once you have the
pixels; it does not conjure the pixels.
