---
name: openwiki-review
description: Audit an existing OpenWiki (openwiki/ directory) for quality — deterministic lint first (links, orphans, frontmatter contract, secrets, size budget), then editorial judgment (verify claims against source, merge stubs, prune duplication and bloat). Does not add new documentation areas. Use when the user asks to review/audit/clean up the wiki, complains docs feel stale or bloated, or after several update runs have accumulated drift.
---

# OpenWiki — quality review

You are acting as OpenWiki's editor. This is a **quality pass over the wiki itself**, not a documentation-generation run: the source of truth is the repository; the subject under review is `openwiki/`. You fix, merge, prune, and correct — you do not expand coverage into new areas (recommend `openwiki-update` or `openwiki-init` for that).

REQUIRED reading before starting (in the sibling `openwiki` skill directory):

1. `../openwiki/references/structure.md` — the structure, frontmatter contract, and quality bar pages are audited against.
2. `../openwiki/references/discipline.md` — research and security discipline (applies to verification reads too).

Helper: `../openwiki/scripts/openwiki-meta.mjs` (paths relative to this skill directory; run it from the repo root).

If `openwiki/quickstart.md` does not exist, stop and tell the user to run `openwiki-init` instead.

## Phase 1 — mechanical (let the tool do it)

Run `node ../openwiki/scripts/openwiki-meta.mjs lint`. Fix every ERROR deterministically — these need no editorial judgment:

- dead links → repair or remove
- orphan pages → link from quickstart (or merge/delete if the orphan is also low-value)
- missing/invalid frontmatter → add the contract (`title`, `sources`, `read-when`)
- `sources` globs matching no files → fix moved/renamed paths
- leftover `_plan.md` → delete
- secret-looking content → remove immediately

Then run `impact` once: pages with UNKNOWN FRESHNESS get frontmatter; a long STALE list is a signal to recommend `openwiki-update` after this review.

Triage lint WARNINGS (thin pages, oversized pages, missing read-when/verified) into phase 2 — they need judgment.

## Phase 2 — editorial (your judgment)

Work through the `openwiki/` tree:

1. **Accuracy.** Spot-check important claims on each page against current source (targeted Reads/Greps, git where helpful) — prioritize pages `impact` marked stale. Fix wrong claims; delete claims about code that no longer exists. Flag — don't guess at — anything you cannot verify.
2. **Contract quality.** Are `sources` globs at the right altitude (tight enough to be meaningful, complete enough to cover the page's subject)? Are `read-when` hints phrased so a mid-task coding agent can act on them? Fix weak ones.
3. **Structure.** Merge thin/stub pages into broader pages or quickstart, collapse single-file directories without a growth story, prefer headings over micro-pages, split oversized pages along real boundaries (updating `sources` on both halves).
4. **Duplication.** Each concept has one canonical home; reduce repeats elsewhere to a mention + link.
5. **Bloat.** Trim content with no explanatory value: raw file inventories duplicating the tree, stale commit-hash lists, boilerplate that says nothing repo-specific.
6. **Agent files.** Top-level `AGENTS.md` contains the exact OpenWiki section from `structure.md`; refresh only if missing or semantically stale.

Rules of engagement: verification reads are targeted — this is an edit pass, not a research pass. Preserve accurate wording; no formatting-only churn on pages you aren't otherwise correcting. When merging or deleting pages, update every link that pointed at them (including quickstart) and fold the removed page's `sources` into the surviving page.

## Finish

1. Re-run `lint` — the review must end lint-clean (errors = 0).
2. If content changed: `node ../openwiki/scripts/openwiki-meta.mjs record update --pages <pages you edited or verified accurate>`. Nothing changed and nothing verified: skip recording.
3. Report: lint findings fixed, editorial issues per category, pages merged/removed (paths), contract improvements, anything flagged unverifiable for the user, and whether a follow-up `openwiki-update` is recommended.
