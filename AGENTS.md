# pokeum

All coding-agent configuration lives in one place, `.agents/`, shared by every agent (Claude Code, Codex, ...) and by humans:

- `.agents/rules/` — the project rules (workflow, conventions, config, tests). They are always in effect: read all four at the start of a session.
- `.agents/skills/` — task skills (`/verify`, `/ship`, `/new-setting`, `/runbook-add`, `/tune`, `/openwiki*`).
- `.agents/git-hooks/` — the git pre-commit hook that enforces the repo's guards (below).
- `.agents/scratch/` — gitignored scratch space for temp scripts, probes, and one-off outputs; never the repo root.

`CLAUDE.md` is only a pointer to this file.

## Guardrails & automation

The repo enforces its discipline with one real git hook instead of agent-harness hooks, so it works the same for any agent or human. One-time setup per clone:

    git config core.hooksPath .agents/git-hooks

- **The pre-commit hook** (`.agents/git-hooks/pre_commit.py`) checks the staged changes and blocks the commit with a message telling you the fix when: `.env`/`.env.*` files (except `.env.example`) are staged; app code (`app/`, `main.py`) is staged without a `pyproject.toml` version bump (escape hatch: `VERSION_OK=1`); staged Python fails `ruff check` or `ruff format --check`; or an OpenWiki page is stale (its source files changed since it was last verified) and is not staged with the commit — update the pages and stage them with the code (escape hatch for a deliberate, explained exception: `OPENWIKI_SKIP=1`). The escape hatches are environment variables for that one commit.
- **Never `git commit --no-verify`** — it skips the hook entirely, and CI runs the full gate anyway.
- **Nothing formats on save**: run `python -m ruff check --fix <files>` and `python -m ruff format <files>` on the Python files you touched before staging.
- **`.env` and variants hold real secrets**: never read or edit them; change `.env.example` instead and ask the developer to mirror keys.
- **Optional token saver**: start sessions through `scripts/claude-headroom.ps1`/`.sh` to run behind the Headroom compression proxy (installed via `requirements-dev.txt`; running the agent CLI directly always works too).

## OpenWiki

This repository has documentation located in the /openwiki directory, built for both humans and coding agents.

Start here:
- [OpenWiki quickstart](openwiki/quickstart.md)

Targeted lookup — before modifying source files, find the relevant docs for exactly those files:

    node .agents/skills/openwiki/scripts/openwiki-meta.mjs map <file...>

(If that script is not present in this checkout, fall back to reading the quickstart and following its links. Each wiki page's frontmatter lists the source globs it covers and a read-when hint.)

Pages marked STALE by the tool have had their source files change since last verification — trust current source over the doc, and consider running an OpenWiki update.

### Maintaining the wiki

The wiki is generated and maintained by the openwiki skills in `.agents/skills/`:

- `/openwiki` — **autopilot, the only command to remember**: assesses the wiki's state and does everything needed — initializes if no wiki exists, updates stale pages after code changes, and folds in a quality/prune pass when health signals call for it. A "wiki is current" no-op result is normal.
- `/openwiki-init`, `/openwiki-update`, `/openwiki-review` — force exactly one mode, for when you want just that.

Do not hand-edit `openwiki/.last-update.json` or the `verified:` frontmatter lines — the toolkit stamps those. Substantive wiki edits outside these skills are fine, but keep each page's `sources:` globs accurate.
