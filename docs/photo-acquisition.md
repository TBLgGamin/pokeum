# Photo acquisition: sources, provenance, and what we may do with them

`docs/real-photo-data.md` establishes the label problem: no public dataset
labels photographs by *exact printing*. This document covers the first half of
the answer — getting candidate photographs in from outside
the app, with provenance attached, so an independent annotator can decide what
each one depicts.

Two tools, one contract:

| Tool | Writes | Decides |
| --- | --- | --- |
| `scripts/acquire_photos.py` | `data/photos/staging/**`, `data/photos/ledger.jsonl` | where bytes came from and under what licence |
| `scripts/annotate_photos.py` | `data/photos/labelled/**`, `review.jsonl`, `annotations.jsonl` | which printing an image shows |

Acquisition never assigns a label. Annotation never fetches anything. The
staging directory and the ledger are the whole interface between them.

## Layout

```
data/photos/
    staging/<source>/<image_id>.<ext>    # bytes exactly as fetched
    staging/<source>/<image_id>.json     # provenance record, one per image
    ledger.jsonl                         # append-only, one JSON object per acquired image
```

`image_id` is the first 16 hex characters of the SHA-256 of the image's
canonical source URL. Consequences worth stating:

- **Idempotent.** The same URL always maps to the same id, so an image already
  named in the ledger is skipped before anything is fetched.
- **Resumable.** `--limit` bounds one run; running again continues where the
  ledger left off. There is no "resume file" to corrupt.
- **Stable.** A source URL that expires (a signed CDN link) must never be the
  identity. Archive-derived images use the dataset's page URL plus the member
  path instead; see the per-source notes.
- **Filename-safe.** The id is hex, so it can never contain `__`, which is what
  `app.recognize.eval.truth_from_name` splits the annotator's
  `{card_id}__{image_id}` output on.

Bytes are staged exactly as fetched: no re-encode, no resize. The `sha256` in
the record is the hash of the file on disk, which is what makes a provenance
claim checkable a year later.

### Record fields

The sidecar JSON and the ledger line are the same object:

| Field | Meaning |
| --- | --- |
| `image_id` | stable hash of `source_url` (above) |
| `source` | plugin name, also the staging subdirectory |
| `source_url` | canonical URL of the image itself |
| `page_url` | human-facing page the image belongs to |
| `title` | listing, post, or archive-member text; may be `""` |
| `licence` | SPDX-ish identifier, a `LicenseRef-`/bespoke string, or literally `unknown` |
| `attribution` | the credit the source states, including the licence URL where the licence requires a link |
| `fetched_at` | ISO-8601 UTC |
| `sha256`, `bytes`, `content_type` | of the staged file |
| `hint_card_id`, `hint_set_code`, `hint_number` | what the source *text* suggests, or `""` |

The ledger is opened for append and never rewritten. A run writes the image,
then the sidecar, then the ledger line — in that order, so an interrupted run
leaves at worst an unrecorded file the next run re-fetches, never a ledger line
for bytes that are not on disk.

### Hints are not labels

`hint_*` is a starting point for the annotator and nothing more. Three rules
produce it, strongest first:

1. Text that *is* a catalogue card id (`base1-4`) is taken as that printing.
2. A collector number from `app.signals.ocr.parse_collector_number`, reused
   verbatim, so "Base Set Charizard 4/102" yields `4` exactly as the OCR signal
   would read it off the card.
3. A set code: every uppercase 2–5 letter token is considered and the first one
   the catalogue knows wins. (`app.signals.ocr.parse_set_code` returns only the
   *first* uppercase token, which is right for a card's bottom strip and useless
   for a post title beginning "MAIL DAY", so the regex for titles lives in
   `scripts/acquire_photos.py` instead.)

A hint is only populated where the source text is about a single card. Dataset
members and multi-card board scenes get `""` — a filename that says nothing
about the printing must not look like weak evidence that it does.

## Sources

`python scripts/acquire_photos.py --list-sources` prints the registry with each
source's credential, hint behaviour, and recorded licence. Common flags:
`--limit N` (0 for no limit), `--out DIR` (default `data/photos`), `--dry-run`
(prints what it would fetch, writes nothing), `--rate-limit SECONDS` (default
1.0, the minimum gap between HTTP requests).

### local

A directory of **your own** photographs. No network, no credential; the default
source and the only unconditionally usable one.

```bash
python scripts/acquire_photos.py --source local --path ~/card-photos --limit 0 --dry-run
python scripts/acquire_photos.py --source local --path ~/card-photos --limit 0 \
    --licence own-work --attribution "Your Name (own photograph)"
```

A directory states no licence, so the default recorded value is literally
`unknown`. Declare what is true with `--licence`/`--attribution`; for photos you
took, `own-work` is the honest value. The filename stem becomes both `title` and
hint text, so naming a file after the printing (`base1-4.jpg`) gives the
annotator the strongest possible prior.

### roboflow

The Roboflow Universe dataset
[`card-scanner-e4knh/pokemon-card-identification-htpkd`](https://universe.roboflow.com/card-scanner-e4knh/pokemon-card-identification-htpkd)
— 5,058 real photographs under CC BY 4.0. This is the default `--query`.

```bash
python scripts/acquire_photos.py --source roboflow --limit 0
python scripts/acquire_photos.py --source roboflow --limit 0 \
    --query card-scanner-e4knh/pokemon-card-identification-htpkd/2
```

- **Credential:** `ROBOFLOW_API_KEY` is *optional*. Public Universe datasets
  download anonymously; the key is only needed for a private dataset or when
  anonymous access is rate-limited, and the tool says exactly that if the API
  answers 401/403.
- **Version:** omit it and the newest generated version is resolved from the
  project metadata; pass `workspace/project/version` to pin one.
- **Licence recorded:** `CC-BY-4.0`, taken from what the project states.
  Attribution is `<workspace>/<project> v<version> (Roboflow Universe),
  CC-BY-4.0, https://creativecommons.org/licenses/by/4.0/`. CC BY 4.0 requires
  credit *and* a link to the licence, so the link is part of the recorded
  attribution rather than a footnote. That attribution is a licence obligation,
  not decoration: it has to survive into anything derived from these images.
- **Labels:** the dataset carries **detection bounding boxes only** — one
  `pokemon_card` class, no set, no number, no card identity. Every record
  therefore has empty `hint_card_id`/`hint_set_code`/`hint_number`. **These
  photos feed detection and rectification robustness testing
  (`app/vision/detect.py`, `app/vision/rectify.py`) and self-supervised domain
  adaptation. They are never printing labels.** Making them printing-labelled
  data means a human annotating 5,058 images, which is exactly the labour
  `docs/real-photo-data.md` says nobody has done.
- **Identity:** members are identified by the Universe page URL plus the member
  path, because the export link itself is signed and expires.
- **Note:** an export serves the version's *preprocessed* images (the resize and
  augmentation the dataset owner configured), not the originals. There is no API
  for the originals.

### tcgar

The TCG-AR academic real evaluation set: 51 photographs of real board scenes,
832 oriented card annotations, from [`ULiege-VIULab/tcg-ar`](https://github.com/ULiege-VIULab/tcg-ar).

```bash
python scripts/acquire_photos.py --source tcgar --limit 0
```

- **Credential:** none.
- **Where the images come from:** the repository ships the manual annotations
  but *not* the photographs. `docs/DATA.md` upstream lists the real evaluation
  set (169 MB) as a Google Drive download and `scripts/download_assets.py` holds
  the file id; this tool uses that id directly and honours the same
  `TCGAR_REALSET_URL` override (a bare Drive id or a `/d/<id>/` share URL). If
  upstream rotates the link, set that variable and re-run — nothing else
  changes. A download that comes back as HTML instead of a zip is refused with
  that message rather than staged.
- **Licence recorded:** `GPL-3.0-only` — the repository's own statement
  ("Released under the GNU General Public License v3.0"; no "or later" clause is
  stated). Attribution carries the citation: Cioppa et al. 2026, *TCG-AR:
  Real-Time Multi-View Augmented Reality for Trading Card Game Streaming*,
  [arXiv:2607.02090](https://arxiv.org/abs/2607.02090). **Cite it in any
  published result that used these images.**
- **Labels:** the upstream `annotations_identity/<n>.txt` files give, per card
  instance, an oriented quad plus a card id — but every frame holds up to twenty
  cards, so there is no per-image hint to give and all `hint_*` fields stay
  empty. The member name is kept in `title` so the matching annotation file is
  one lookup away in
  `assets/AI database/real/annotations_identity/`. Those ids are name/set-level
  and say nothing about finish, edition, or language, so they are hints at best
  and **must never be accepted as printing labels.** Use this set to measure
  detection and rectification on cluttered, occluded, sleeved real boards — the
  sim2real gap — not to train identity.

### reddit

Image posts from [r/PokemonTCG](https://www.reddit.com/r/PokemonTCG/) and
[r/pokemoncards](https://www.reddit.com/r/pokemoncards/).

```bash
pip install praw    # dev-only, deliberately not in requirements.txt
export REDDIT_CLIENT_ID=...     # https://www.reddit.com/prefs/apps (script app)
export REDDIT_CLIENT_SECRET=...
python scripts/acquire_photos.py --source reddit --limit 200
python scripts/acquire_photos.py --source reddit --limit 200 --query "base set charizard"
```

- **Credential:** `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET`, both required.
  `praw` is imported lazily and its absence is reported with the `pip install`
  line, not a stack trace.
- **What is taken:** direct image posts and galleries. Gallery images are
  rebuilt as permanent `https://i.redd.it/<media_id>.<ext>` URLs, because the
  preview URLs in `media_metadata` are signed and change between calls, which
  would break ledger identity. A link to an off-site page is skipped rather than
  followed: this tool acquires from sources whose terms it has read.
- **Licence recorded:** `reddit-user-content` — not an SPDX identifier, and it
  never will be. The [Reddit User Agreement](https://www.reddit.com/policies/user-agreement)
  grants Reddit a "worldwide, royalty-free, perpetual, irrevocable,
  non-exclusive, transferable, and sublicensable licence" over submitted
  content, and that grant covers machine-learning use, so **internal training
  and evaluation are defensible**. The uploader keeps their copyright, so
  **redistribution is not**: these images are never republished, never shipped
  in a release, and never exported as a public dataset. Attribution records
  `u/<author> via r/<subreddit>` so any individual photo can be traced back and
  removed on request.
- **Hints:** the post title, which is genuinely informative on these subreddits
  ("Base Set Charizard 4/102"). It is still only a hint — titles are wrong often
  enough (Base Set versus Base Set 2 is the classic) that the annotator decides.
- **Expect labour.** The realistic yield is a few hundred to a couple of
  thousand usable images after filtering, with 20–40 hours of curation.

## Sources we deliberately do not implement

Do not add these back. Each line is the reason, not an opinion.

| Source | Why not |
| --- | --- |
| **eBay** (Browse API) | The [eBay API License Agreement](https://developer.ebay.com/join/api-license-agreement) states you may not use eBay Content "to train algorithms, conduct machine learning, develop synthetic data sets, train large learning models, and/or train artificial intelligence systems". That is precisely this pipeline's purpose, so eBay is off-limits regardless of whether a key is available. |
| **TCGplayer** (API) | API access is closed to new applicants ("TCGplayer is no longer granting new API access at this time"). Using grandfathered keys or scraping around the closure is a ToS violation. |
| **Cardmarket** | Automated scraping of the site is prohibited by its terms, and the third-party API wrappers get their data by doing exactly that. |
| **PSA / CGC / Beckett** | Scraping restrictions, plus the domain is wrong: cert photos show a graded slab (holder, label, QR code), not a bare card, so training or evaluating a bare-card recognizer on them measures the wrong thing. |

Also already ruled out in `docs/real-photo-data.md`: Kaggle card sets (renders or
a real/fake binary, no printing labels) and whole-catalogue scrapes (the same
official renders `python main.py sync` already downloads).

## Licence and redistribution position

- **Internal training and evaluation only.** Acquired photographs are used to
  train and measure this recognizer. They are not republished, not shipped in a
  release, and not exported as a public dataset. An exported encoder contains no
  imagery.
- **The ledger is the compliance mechanism.** It exists so that any image's
  provenance and licence can be produced on demand, and so that a takedown
  request is a ledger query: find the `image_id` (or the `page_url`, or the
  `attribution`), delete the two staged files and any labelled copy, and the
  removal is complete and evidenced. That is only possible because the ledger is
  append-only and the `sha256` matches the bytes on disk.
- **`licence: "unknown"` is a restriction, not a blank.** It means the source
  stated nothing, so nothing may be assumed: internal evaluation only, no
  redistribution, no publication of the image, and a citation or permission
  check before any published result leans on it. If you know the real licence,
  record it (`--licence`) rather than leaving `unknown` in place.
- **Attribution obligations travel with the data.** `CC-BY-4.0` requires credit
  and a licence link; both are in the record. Anything derived from those images
  has to carry the same credit.
- **Card artwork remains third-party.** Every photograph, whatever its own
  licence, depicts artwork owned by Nintendo / The Pokémon Company. A permissive
  photo licence is permission to use the *photograph*, never permission to
  redistribute the artwork.

## Quality and politeness gates

All enforced in one place, so no plugin can skip them:

- **One HTTP path.** Every networked source fetches through one `HttpFetcher`:
  a declared User-Agent
  (`pokeum-photo-acquire (+https://github.com/TBLgGamin/pokeum)`) and a minimum
  gap between requests (`--rate-limit`, default one second). There is no retry
  loop: a failed image is a counted rejection and the next run retries it,
  because a retry storm against a third party is exactly what politeness means
  to avoid.
- **Byte cap.** 12 MB per image (checked against `Content-Length` before the
  body is read, and again while reading); 4 GB for a dataset archive, streamed
  to a temporary file instead of held in memory.
- **Image-only.** The response content type must be one of JPEG, PNG, WebP, or
  BMP — deliberately the same set `app.recognize.eval` scores, so an acquired
  image cannot become a labelled file that `pokeum eval` then ignores. HTML
  behind an image-looking URL is refused before the body is read.
- **Redirects followed by hand.** At most three hops, HTTP(S) only, with the
  content-type gate applied to the final response, so a redirect cannot smuggle
  in non-image content.
- **Really an image.** Every payload is decoded with `app.vision.imaging`
  before staging; anything that fails is a counted rejection.
- **Big enough to use.** Images whose long side is under 200 px are refused:
  search and preview endpoints serve thumbnails no signal can work with.
- **Archives cannot escape.** Member names are never used as output paths — a
  staged file is always `<image_id>.<ext>` — so a hostile archive writes
  nothing outside `staging/<source>/`.

Rejections are counted per reason in the run summary and are *not* written to
the ledger, so a re-run re-examines them. That is intentional: a rejection is
not an acquisition, and a transient 503 should not be remembered as a decision.

## Do not

- **Do not bulk-scrape a site whose terms forbid it.** The table above is the
  list that has already been checked; anything new gets checked the same way
  before code is written, and the finding goes in that table.
- **Do not republish acquired images.** No public dataset, no release asset, no
  sample images in the repository or in documentation.
- **Do not commit `data/photos/**`.** It is already ignored: `.gitignore:24`
  ignores the whole `/data/` tree ("Reference data: downloaded TCGdex images,
  SQLite catalogue, embedding index, and exported models"), which covers
  `data/photos/staging/**`, the ledger, and the annotator's output. Verified, not
  assumed — `git check-ignore data/photos/ledger.jsonl` confirms it.
- **Do not hand-edit or rewrite `ledger.jsonl`.** Append-only is what makes it
  evidence. To drop an image, delete its staged files and record the removal;
  rewriting history destroys the provenance of everything else in the file.
- **Do not treat a hint as a label**, and do not "fix" an annotation by copying
  `hint_card_id` into it. The annotator's independence is the only thing that
  makes the resulting labels worth anything.
- **Do not put credentials in the repository.** `EBAY_*` is gone for good;
  `ROBOFLOW_API_KEY`, `REDDIT_CLIENT_ID`, and `REDDIT_CLIENT_SECRET` live in your
  shell or in `.env`, which is git-ignored and never committed.

## What this actually buys

Realistic volumes, from the source survey:

| Source | Volume | Labour | Label granularity |
| --- | --- | --- | --- |
| `roboflow` | 5,058, one-time | minutes | none (detection boxes) |
| `tcgar` | 51 scenes / 832 instances, one-time | minutes | name + set, per instance, multi-card frames |
| `reddit` | 500–2,000 usable | 20–40 hours of curation | post title, unverified |
| `local` | however many you photograph | your own time | whatever you name the files |

The conclusion is the same one `docs/real-photo-data.md` reaches, now with the
external options actually enumerated: no external source is labelled by exact
printing, and the two openly licensed ones are not labelled by card identity at
all. External acquisition buys real-photo *domain* coverage — detection,
rectification, glare, sleeves, board clutter, self-supervised adaptation — and a
candidate pool for annotation. The exact-printing label the encoder fine-tune
actually trains on comes from the annotator (`scripts/annotate_photos.py`), not
from the sources themselves.
