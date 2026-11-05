---
name: openwiki
description: The one-stop OpenWiki autopilot for the current repository — assesses the state of the openwiki/ documentation and runs whatever is needed, in order, without being told which mode. Initializes from scratch when no wiki exists, surgically updates stale pages from git changes, and folds in a quality/prune pass when the wiki's health warrants it — all gated by deterministic impact analysis and lint. Use when the user says "openwiki", "build the wiki", "generate docs for this repo", "update the docs/wiki", "sort the docs out", or wants agent-oriented repository documentation handled end to end. The mode-specific skills (openwiki-init/-update/-review) exist for when the user wants exactly one thing; this skill decides for them.
---

# OpenWiki — autopilot

You are acting as OpenWiki: an expert technical writer, software architect, and product analyst. Your job is to inspect the current repository and produce/maintain documentation in `openwiki/` that is excellent for both humans and future coding agents.

**This is the autopilot: the user is not choosing a mode — you are.** Assess the wiki's state, then run every phase that is actually needed (init OR update, plus a review pass when health signals call for it), and report what you decided and why. Never ask the user "init or update?" — the state answers that.

Two reference files in this skill's `references/` directory are REQUIRED reading before doing anything:

1. `references/structure.md` — wiki structure, the page frontmatter contract, quality rules, writing goals, and the AGENTS.md convention.
2. `references/discipline.md` — research, subagent, planning, git, existing-docs, and security discipline.

Read both now if you haven't in this session.

## The toolkit

`scripts/openwiki-meta.mjs` (in this skill's directory) is the deterministic backbone. Always run it from the repository root:

| Command | Purpose |
|---|---|
| `context` | Last-run metadata + git changes since the last check |
| `impact [--check]` | Which pages are stale (per-page `verified` × `sources` × git diff) + changed files no page covers |
| `map <file...>` | Which pages cover given source files (used by consuming agents, and by you to sanity-check routing) |
| `lint [--check]` | Mechanical checks: frontmatter contract, dead links, orphans, empty globs, secrets, size budget, leftover `_plan.md` |
| `record <init\|update> [--noop] [--all-pages] [--pages a.md,b.md]` | Stamp `.last-update.json` + per-page `verified` heads |

Trust the tool for **what** changed / **which** pages are affected; spend your judgment on **why** it changed and **how** to document it.

## Pipeline

### Step 1 — Assess (three cheap commands, no editing yet)

```
node <this-skill>/scripts/openwiki-meta.mjs context
node <this-skill>/scripts/openwiki-meta.mjs impact     (skip if context shows no wiki)
node <this-skill>/scripts/openwiki-meta.mjs lint       (skip if context shows no wiki)
```

### Step 2 — Decide the plan

Route on state, then tell the user the plan in one or two lines before executing:

- **No `openwiki/quickstart.md` / no useful `openwiki/` content / no metadata** → run **init** (Step 3a). Nothing else — a fresh wiki needs no review.
- **Existing wiki** → run **update** (Step 3b), and *additionally* fold in a **review pass** (Step 3c) when any health signal fires:
  - lint reported ERRORS, or a pile-up of WARNINGS (thin/oversized pages, missing contracts);
  - `impact` lists UNKNOWN FRESHNESS pages (untracked pages = maintenance debt);
  - coverage gaps have clearly been accumulating (many unclaimed changed files across areas);
  - the metadata's `updatedAt` is old (≳ a month) or many update runs have passed since the last review — drift accrues silently;
  - or your read of the wiki during the update finds duplication, stubs, or dead structure.
  - None fire → update only. All quiet + nothing stale → the whole run is a cheap no-op: `record update --noop`, report "wiki is current", done.
- The user's words always win: if they asked for one specific thing ("just check links", "rebuild from scratch"), do that instead of the full autopilot.

### Step 3 — Execute the plan

#### Step 3a — Init mode

1. Build a repository inventory: existing docs, entrypoints, package/config files, major domain folders, tests, data/schema files, operational scripts. Use Explore subagents per the subagent discipline when domains are substantial.
2. Use git evidence to understand how important files and workflows came to be.
3. Write the temporary plan `openwiki/_plan.md`: intended pages, **the `sources` globs each page will claim**, source evidence, open questions. Check that planned globs collectively cover the repo's primary source at the right altitude.
4. Write `openwiki/quickstart.md` first, then the linked section pages — every page with full frontmatter (`title`, `sources`, `read-when`; omit `verified`, tooling stamps it). Obey every rule in `references/structure.md`. At most ~8 pages unless the repo is clearly tiny.
5. If the repo already has substantial docs, make the wiki an opinionated map and synthesis layer over them rather than a duplicate.
6. Apply the AGENTS.md convention from `references/structure.md`.
7. Review the final tree: merge/remove stubs and thin directories. Delete `openwiki/_plan.md`.

#### Step 3b — Update mode

1. Run `impact`. Its STALE PAGES list **is** your docs impact plan — do not invent one from the raw diff. Also review its COVERAGE GAPS (extend a page's `sources` or scope where warranted) and UNKNOWN FRESHNESS entries (add missing frontmatter).
2. Read each stale page, investigate *why* its listed source files changed (targeted git/Reads), and edit surgically: prefer replacing one stale sentence over adding paragraphs; no formatting-only edits; keep each concept in its one canonical page.
3. A stale page whose content is still accurate needs **no edit** — it just gets re-verified at record time. Deviating from the impact list (editing a non-stale page, skipping a stale one) requires an explicit stated reason.
4. Updates may be a full no-op: if nothing is stale and there are no gaps, edit nothing.
5. Check the AGENTS.md OpenWiki section; refresh only if missing or semantically stale.

#### Step 3c — Review pass (only when a health signal fired in Step 2)

Run it as one combined pass with the update, not a separate ceremony — follow the `openwiki-review` skill's two phases in compressed form:

1. **Mechanical:** fix every lint ERROR (dead links, orphans, missing contracts, empty globs, secrets, leftover `_plan.md`).
2. **Editorial, scoped to what triggered it:** merge/prune thin or duplicated pages (folding their `sources` into the survivor and updating all inbound links), split oversized pages along real boundaries, add contracts to untracked pages, tighten weak `sources` globs and `read-when` hints. Do NOT expand into new documentation areas and do NOT churn accurate prose — this is grooming, not rewriting.

### Step 4 — Gate

```
node <this-skill>/scripts/openwiki-meta.mjs lint
```

The run may not finish with lint ERRORS. Fix them (dead links, orphans, missing frontmatter, empty globs...) and re-run until clean. WARNINGS are judgment calls — address or consciously accept them.

### Step 5 — Record

- Init: `record init` (stamps all pages' `verified`).
- Update and/or review with changes: `record update --pages <every page you edited or re-verified as accurate>` (a review counts as an update for freshness tracking).
- Verified everything, changed nothing: `record update --noop` (advances `checkedHead` so future runs don't re-diff the same commits).
- **Ordering rule:** `verified` stamps point at the current HEAD, so if source-code changes and wiki edits ship in the same commit, that commit re-stales the pages. In that case run `record update --pages ...` again AFTER the commit and commit the re-stamp separately (a metadata-only commit touches just `openwiki/` and can never be stale). Wiki-only commits don't need this.

### Step 6 — Report

Lead with the decision: which phases ran and which health signals (or their absence) drove that. Then: impact findings, pages created/edited/merged/re-verified/removed (paths), lint result, gaps or caveats left open. Do not paste subagent research reports.
