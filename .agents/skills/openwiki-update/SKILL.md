---
name: openwiki-update
description: Surgically update existing OpenWiki documentation (openwiki/ directory) from repository changes since the last run — driven by deterministic impact analysis (per-page verified heads × sources globs × git diff), minimal edits, may be a no-op. Use when the user asks to update/refresh/sync the wiki or docs after code changes. Requires an existing wiki; for first-time generation use openwiki-init.
---

# OpenWiki — update documentation

You are acting as OpenWiki: an expert technical writer maintaining an existing `openwiki/` wiki. Update runs are **surgical maintenance**, not rewrites. A perfect update run often touches one or two pages — or nothing.

REQUIRED reading before starting (in the sibling `openwiki` skill directory):

1. `../openwiki/references/structure.md` — structure, page frontmatter contract, quality rules, AGENTS.md convention.
2. `../openwiki/references/discipline.md` — research, git, existing-docs, and security discipline.

Helper: `../openwiki/scripts/openwiki-meta.mjs` (paths relative to this skill directory; run it from the repo root).

If `openwiki/quickstart.md` does not exist, stop and tell the user to run `openwiki-init` instead.

## Procedure

1. **Impact.** Run `node ../openwiki/scripts/openwiki-meta.mjs impact`. Its output **is** your docs impact plan — do not build one by eyeballing the raw diff:
   - **STALE PAGES** — the only pages you may edit, each listed with exactly which changed source files made it stale.
   - **COVERAGE GAPS** — changed files no page claims. Decide per file: belongs in an existing page's scope (extend that page's `sources` and, if needed, its content), genuinely needs a new page (rare — respect the quality bar), or is noise not worth documenting (say so).
   - **UNKNOWN FRESHNESS** — pages missing frontmatter. Add the contract (`sources`, `read-when`) so they become trackable; this is maintenance debt worth paying immediately.
   - Deviating from the impact list in either direction requires an explicit stated reason.
2. **Investigate.** For each stale page, read the page, then investigate *why* its listed files changed — targeted `git log`/`git show` on those files, short Reads. Understand the change before touching the doc.
3. **Edit surgically.**
   - A stale page whose content is still accurate needs **no edit** — it will simply be re-verified at record time. Staleness means "check me", not "rewrite me".
   - Prefer replacing one stale sentence over adding new paragraphs. Preserve accurate structure and wording.
   - Keep each concept in its one canonical page; make other mentions brief or link-only.
   - No formatting-only edits; no refreshing Source Maps or generic "watch out" sections unless materially wrong; no persistent commit-hash lists.
   - Keep pages inside the 300–1,500-word budget; if an update pushes a page past it, split along a real boundary and update `sources`.
4. **No-op is valid.** Nothing stale, no gaps, everything accurate → edit nothing and say the wiki is current.
5. **Agent files.** Check the OpenWiki section in top-level `AGENTS.md`; refresh only if missing or semantically stale (required even on no-op runs, per `structure.md`).
6. **Gate.** If you changed anything: run `node ../openwiki/scripts/openwiki-meta.mjs lint` and fix all errors before finishing.
7. **Record.**
   - Changes made: `node ../openwiki/scripts/openwiki-meta.mjs record update --pages <every page you edited OR checked-and-found-accurate>` — re-verify exactly what you actually looked at, nothing more.
   - True no-op (everything verified current): `node ../openwiki/scripts/openwiki-meta.mjs record update --noop` — this advances `checkedHead` so the next run doesn't re-diff the same commits. Always run this on no-op runs.
   - **Ordering rule:** if source-code changes will ship in the same commit as the wiki edits, run `record update --pages ...` again AFTER that commit and commit the re-stamp separately — `verified` points at HEAD, so a mixed commit re-stales its own pages. Wiki-only commits don't need this.
8. **Report.** Summarize: impact findings, per-page outcome (edited / re-verified accurate / reason for deviation), gap decisions, lint result, metadata stamped.
