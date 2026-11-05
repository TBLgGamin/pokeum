# OpenWiki run discipline

How to research and behave during any openwiki run. These rules assume a coding agent's native tools (read, write, edit, glob, grep, shell, read-only exploration subagents) operating on real repository paths.

## Research discipline

- Ground every important claim in source files, existing docs, or git evidence you have actually inspected. Do not invent files, modules, APIs, business rules, or behavior.
- Do not exhaustively read every file. Inspect: the repository tree, package/config files, README-style files, entrypoints, routing files, database/schema files, and representative files for each major domain.
- Never glob `**/*` from the repository root. Use targeted discovery by directory and extension, excluding `.git`, `node_modules`, `dist`, `build`, cache directories, and the existing `openwiki/` output.
- Prefer Grep/Glob and short targeted Reads over full-file reads when files are large.
- Stay inside the target repository. Do not search parent directories or unrelated repositories.
- Create a strong, accurate, navigable wiki, then stop. It gets refined in later update runs — do not gold-plate the first pass.

## Subagent discipline

- You may use Explore subagents to parallelize read-only research when the repository has multiple substantial domains.
- Default to 1–2 subagents for large or unfamiliar repositories; use 3–4 only when the repo is clearly small/medium with naturally independent domains, or the user asks for deeper research.
- Subagents only inspect and summarize — they never write files. Give each a narrow brief (existing docs, runtime architecture, data/storage, UI/API surface, integrations, tests, business workflows) and ask for concise findings with source paths and open questions.
- Subagent reports are internal discovery notes. You synthesize the final docs and own all writes. Do not paste subagent reports into pages or the final response.

## Planning discipline

- After discovery and before writing final documentation, write a temporary `openwiki/_plan.md` listing: intended pages, source evidence for each page, and remaining questions.
- Delete `openwiki/_plan.md` before completing the run. Never leave it in the final wiki.

## Git discipline

- Use git to explain **why** code exists, not just what exists.
- During init: inspect recent commit history; use `git log`, `git show`, and `git blame` selectively on important files to understand how major workflows, entrypoints, and business rules evolved.
- During update: always inspect commits added since the previous successful run — the helper's `context` and `impact` commands compute this from the `checkedHead` recorded in `openwiki/.last-update.json` (falling back to `updatedAt`). Prefer their output over ad-hoc git archaeology; use raw git to understand *why* the listed files changed, not to re-derive *what* changed.
- Use `git status` and `git diff` to account for uncommitted local changes, especially those touching docs or important source files.
- Do not over-index on ancient history. Focus on recent commits and high-signal history for important files.

## Existing documentation discipline

- Treat existing READMEs, `docs/` trees, root documentation files, runbooks, and SKILL.md files as primary source material.
- Summarize and link to existing docs that are still useful — do not duplicate them wholesale.
- If existing docs conflict with source code or git history, call out the likely-stale documentation and prefer current source evidence.

## Security & scope rules

- Do not read or document secret values, credentials, private keys, tokens, or `.env` files. `.env.example` and other sample config may be read only if it contains placeholders.
- If a secret-bearing file seems relevant, document only that such configuration exists and where non-sensitive setup belongs.
- Keep all documentation under `openwiki/`. Do not modify source code outside `openwiki/` — the only exception is top-level `AGENTS.md`, and only for the OpenWiki reference section defined in `structure.md`.
