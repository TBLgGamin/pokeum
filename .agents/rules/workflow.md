# Agent workflow (always applies)

How to work in this repo efficiently. The repo's only automation is one git pre-commit hook (`.agents/git-hooks/`); everything else below is discipline you keep yourself.

## Orient before you edit

- Check `git status` and the current branch once at the start of a task, then trust it — don't re-derive them every few steps.
- Before modifying source files, look up their docs: `node .agents/skills/openwiki/scripts/openwiki-meta.mjs map <file...>`. Read only the pages it names; don't crawl the wiki. A page marked STALE means trust source over doc.
- Read targeted (specific files/line ranges, Grep for symbols) rather than whole directories. Delegate broad multi-file exploration to a read-only subagent and keep only its conclusions.

## Know the guard rails

- **Format and lint what you touch**: nothing runs on save. Before staging, run `python -m ruff check --fix <files>` and `python -m ruff format <files>` on the Python files you changed (not the whole repo) — the pre-commit hook rejects staged Python that fails either.
- `.env` and its variants hold real secrets: never read or edit them. Change `.env.example` and ask the developer to mirror keys; the pre-commit hook refuses to commit them.
- **`git commit` is guarded by the pre-commit hook** (`.agents/git-hooks/pre_commit.py`, enabled once per clone with `git config core.hooksPath .agents/git-hooks`). It refuses: staged `.env` files; app code (`app/`, `main.py`) without a `pyproject.toml` semver bump; Ruff findings in staged Python; and any commit while an OpenWiki page is stale (its source files changed since it was last verified) and not staged along (updating the stale pages and staging them **with** the code satisfies it — re-stamp via `record update --pages` after, in a metadata-only commit). Escape hatches, each with a stated reason, set as environment variables for that one commit: `VERSION_OK=1`, `OPENWIKI_SKIP=1`. `/ship` walks all of this.
- **Never `git commit --no-verify`**: it skips the hook entirely, and the guards above exist only there (CI runs the full gate regardless).

## Keep output lean

- Tooling is configured for terse output (`pytest -q -ra`, concise ruff) — don't add `-v`/`--verbose` unless actually diagnosing, and drop it afterwards.
- Run tools quiet and colour-free: export `NO_COLOR=1`, `PY_COLORS=0`, `PYTHONUTF8=1`, `PYTHONDONTWRITEBYTECODE=1` and `PIP_DISABLE_PIP_VERSION_CHECK=1` in the agent's shell — ANSI codes and pip nags are wasted tokens.
- When a command can answer with less (e.g. `git log --oneline -5` vs full log, `pytest tests/test_x.py::test_y` vs the whole suite), prefer less.
- Debug loop: after a failing test run, iterate with `python -m pytest --lf` (last failures only) until green, then one full `python -m pytest` to confirm.
- Don't paste long tool output back into the conversation; summarize and reference.
- Temp scripts, probe files, and one-off outputs go in gitignored `.agents/scratch/` — never the repo root. A clean `git status` keeps staging and the commit hook accurate.

## Definition of done

- Behaviour changed → the matching wiki page is updated in the same piece of work (the pre-commit hook enforces this).
- The verify gate passes: `python -m ruff check . && python -m ruff format --check . && python -m mypy && python -m pytest` (CI runs the same set — "passed locally" must mean "will pass in CI").
- New settings followed the checklist in `config.md`; new behaviour has offline tests per `tests.md`.
- Commit via the `/ship` skill conventions: Conventional Commits, heredoc message, semver bump in `pyproject.toml` once per shippable unit.
