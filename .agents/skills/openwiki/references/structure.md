# OpenWiki structure & quality rules

All documentation lives under `openwiki/` at the repository root. Run metadata lives in `openwiki/.last-update.json` (managed by `scripts/openwiki-meta.mjs` — never hand-edit it, never document it as a wiki page).

## Required structure

- `openwiki/quickstart.md` is the entrypoint. It MUST exist and MUST contain:
  - A high-level overview: what the project is, what it does, how it is organized.
  - Links to every other wiki page/section, so a reader starting at quickstart can reach everything.
- When the repository is large enough to need section directories, create one directory per major documentation area — e.g. `architecture/`, `workflows/`, `domain/`, `api/`, `data-models/`, `operations/`, `integrations/`, `testing/` — using names that fit the repo, not this list verbatim.
- Each section directory contains focused Markdown pages. If a directory would contain only one short page, prefer a broader page or a heading inside `quickstart.md` instead.
- Include inline source-file references (`path/to/file.ts`) where they help readers verify claims or continue exploring. "Source Map" sections are optional — add one only when it materially improves navigation for that page.

## Page contract (frontmatter) — REQUIRED on every page

Every wiki page starts with a YAML frontmatter block that binds it to the source code it documents. This is what makes the wiki machine-addressable: the `impact` command computes staleness from it, and the `map` command routes coding agents to the right pages from the files they are editing.

```yaml
---
title: Authentication & sessions
sources: ["src/auth/**", "src/middleware/session.ts"]
read-when: "changing login, tokens, session storage, or the user model"
verified: 3bf84c9b354c
---
```

- `title` — human-readable page name.
- `sources` — the file globs this page documents. Supported syntax: `**` (any depth), `*` (within a path segment), bare directories (`src/auth` means `src/auth/**`). Write these carefully at init when your knowledge of the code is freshest; they are the page's contract with the repository. Keep them tight — a page claiming `src/**` defeats impact tracking.
- `read-when` — one line, phrased for a coding agent deciding whether to open this page mid-task.
- `verified` — the git head the content was last checked against. **Stamped by tooling (`record --pages`/`--all-pages`), never written by hand.** Omit it when creating a page; the record step adds it.
- `quickstart.md` is exempt from `sources`/`read-when` (it covers the whole repo) but still gets `title` and a tool-stamped `verified`.

Rules:
- Every changed source file should be claimed by some page's `sources` at the right altitude. The `impact` command reports unclaimed changed files as coverage gaps — treat persistent gaps as a signal to extend a page's scope or (rarely) add a page.
- When a run edits or verifies a page, that page's `verified` must be re-stamped via `record` in the same run. Never claim freshness you did not check.

## Section quality rules

- Do not create a directory unless it represents a real documentation area.
- A section directory should usually contain multiple substantive pages. A single-file directory is acceptable only when that page is substantial, has a clear domain boundary, and is likely to grow.
- Avoid thin pages. If a page would mostly be a stub, source map, or short note, merge it into `quickstart.md` or a broader section page.
- Prefer headings inside broader pages before creating many small directories.
- Each page must provide real explanatory value: what the area does, why it exists, where to start, what to watch out for, and key source references.
- For small repositories (~10 or fewer primary source files): `quickstart.md` plus at most 1–2 supporting pages. No section directories unless the boundary is clearly useful and likely to grow.
- Use at most ~8 pages on an initial run unless the repository is clearly tiny (fewer) or the user explicitly asks for more depth.
- **Page size budget:** target 300–1,500 words per page (lint warns below ~120 and above ~2,000). One page should be one cheap, complete read for an agent — if a page outgrows the budget, split it along a real domain boundary and update `sources` accordingly; if it can't reach the floor with substance, merge it.
- Before finishing any run, review the `openwiki/` tree. Merge, move, or remove low-value single-file directories and stub pages so the wiki stays navigable and maintainable.

## Writing goals

- Someone with zero knowledge of the repository should be able to start at `openwiki/quickstart.md` and understand what the project is, how it is organized, what it does, and where to go next.
- A future coding agent should be able to use the docs to make high-quality changes with less source exploration — and should be able to find the right page via `map` from the files it is editing, without crawling the whole wiki.
- Capture both technical details and business/product logic. Explain **why** important code exists, not only what files contain.
- Include change-oriented guidance for future agents: where to start, what to watch out for, and which tests or checks are relevant when changing each major area.
- Clear Markdown with stable relative links between pages. Organize like human documentation, not a raw file inventory.
- Keep docs concise enough to maintain. Give each concept ONE canonical home; other pages link to it rather than repeating it.
- Use git history for discovery, but do not include persistent commit-hash lists in pages unless a specific historical decision matters for future work.

## Agent instruction file convention

Unless the user explicitly says not to, ensure the repository's top-level agent instruction file references the wiki:

- Only consider the top-level `AGENTS.md` — the single source of agent instructions. Never edit nested ones. A top-level `CLAUDE.md`, if present, is just a one-line pointer to `AGENTS.md`: leave it alone.
- If `AGENTS.md` exists, add or update the OpenWiki reference section there.
- If it does not exist, create top-level `AGENTS.md` containing only the section.
- Replace/update an existing OpenWiki section instead of adding duplicates. Preserve all surrounding content.
- Do not touch the file just to normalize formatting if the existing section is already semantically correct.

Use this exact section structure every time:

```markdown
## OpenWiki

This repository has documentation located in the /openwiki directory, built for both humans and coding agents.

Start here:
- [OpenWiki quickstart](openwiki/quickstart.md)

Targeted lookup — before modifying source files, find the relevant docs for exactly those files:

    node .agents/skills/openwiki/scripts/openwiki-meta.mjs map <file...>

(If that script is not present in this checkout, fall back to reading the quickstart and following its links. Each wiki page's frontmatter lists the source globs it covers and a read-when hint.)

Pages marked STALE by the tool have had their source files change since last verification — trust current source over the doc, and consider running an OpenWiki update.
```
