---
name: openwiki-init
description: Initialize OpenWiki documentation from scratch for the current repository — deep discovery, then write openwiki/quickstart.md plus focused section pages, each with a frontmatter contract (sources globs, read-when hint) that makes the wiki machine-addressable. Use when the user asks to initialize/bootstrap/create the wiki or generate first-time repository documentation, or when openwiki/ does not exist yet. For refreshing an existing wiki use openwiki-update instead.
---

# OpenWiki — initialize documentation

You are acting as OpenWiki: an expert technical writer, software architect, and product analyst. Build first-pass documentation in `openwiki/` for a repository that has no wiki yet (or whose wiki should be rebuilt — confirm with the user before overwriting substantial existing `openwiki/` content).

REQUIRED reading before starting (in the sibling `openwiki` skill directory):

1. `../openwiki/references/structure.md` — structure, **page frontmatter contract**, quality rules, writing goals, AGENTS.md convention.
2. `../openwiki/references/discipline.md` — research, subagent, planning, git, existing-docs, and security discipline.

Helper: `../openwiki/scripts/openwiki-meta.mjs` (paths relative to this skill directory; run it from the repo root).

## Procedure

1. **Context.** Run `node ../openwiki/scripts/openwiki-meta.mjs context`. It's fine if it reports no prior metadata — that's expected for init.
2. **Inventory.** Build a repository inventory: existing docs, entrypoints, package/config files, major domain folders, routing, data/schema files, tests/evals, operational scripts. Use 1–2 Explore subagents (max 3–4 for clearly independent domains) per the subagent discipline — read-only, narrow briefs, findings with source paths.
3. **Git evidence.** Use recent commit history and targeted `git log`/`git show`/`git blame` on high-signal files to understand why major workflows and business rules exist — not just what the code contains.
4. **Plan.** Write temporary `openwiki/_plan.md`: intended pages, **the `sources` globs each page will claim**, source evidence per page, remaining questions. The globs are the wiki's contract with the repo — check they collectively cover the primary source tree at the right altitude, without overlaps so broad (e.g. `src/**` on one page) that impact tracking becomes meaningless.
5. **Write.** Create `openwiki/quickstart.md` first (overview + links to every section), then the section pages. **Every page gets frontmatter**: `title`, `sources`, `read-when` (omit `verified` — tooling stamps it at record time; quickstart needs only `title`). Obey all structure and quality rules: ≤ ~8 pages unless the repo is tiny (then fewer — quickstart + 1–2 pages), 300–1,500 words per page, no thin pages, no single-file directories without clear justification, one canonical home per concept, inline source references, change-oriented guidance for future agents.
6. **Existing docs.** If the repo already has substantial documentation, make the wiki an opinionated map and synthesis layer over it — link, don't duplicate. Flag stale docs where they conflict with source.
7. **Agent files.** Apply the AGENTS.md convention from `structure.md` (top-level `AGENTS.md` only, exact section format — it teaches consuming agents to use the `map` command).
8. **Review & clean.** Re-inspect the `openwiki/` tree; merge or remove stubs and low-value directories. Delete `openwiki/_plan.md`.
9. **Gate.** Run `node ../openwiki/scripts/openwiki-meta.mjs lint` and fix ALL errors (re-run until clean). Sanity-check routing with a couple of `map <important-source-file>` calls — important files should route to sensible pages.
10. **Record.** Run `node ../openwiki/scripts/openwiki-meta.mjs record init` (stamps `.last-update.json` and `verified` on every page).
11. **Report.** Summarize pages created (paths), the source coverage of the wiki, lint result, key findings, and open questions. Do not paste subagent reports.
