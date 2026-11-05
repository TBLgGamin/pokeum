---
title: Development workflow
sources: ["app/core/config.py", "app/core/logging_config.py", "tests/**", ".env.example", ".agents/**", "AGENTS.md", "CLAUDE.md", ".gitattributes", ".github/**", ".pre-commit-config.yaml", "pyproject.toml", "requirements.txt", "requirements-dev.txt", "scripts/fresh-start.sh", "scripts/fresh-start.ps1", "scripts/claude-headroom.sh", "scripts/claude-headroom.ps1"]
read-when: "changing settings/logging, tests, dependencies, quality-gate config, CI, the git pre-commit hook, agent rules/skills (.agents/), coding conventions, or the fresh-start/detach script"
verified: 388a26a04ef7
---

# Development workflow

How this repo keeps the codebase consistent: the settings/constants split, the
conventions that are enforced (not just suggested), and the gate that checks all
of it locally and in CI. These conventions predate the recognizer — they came
from the vva-python-template skeleton pokeum grew out of — and still govern every
change. The domain code they apply to is documented in
[recognition-pipeline](recognition-pipeline.md),
[reference-data](reference-data.md), and [service-and-cli](service-and-cli.md).

## Settings vs. constants

Every configurable value is one of exactly two kinds, and each has exactly one
home (`.agents/rules/config.md`):

- **Varies per environment** (endpoints, keys, log level) → a named accessor in
  [`app/core/config.py`](../app/core/config.py). `config.require("NAME")` for
  values that are security- or correctness-critical and must fail fast
  (`MissingSettingError`) rather than run with a silently empty value;
  `config.get("NAME", default)` otherwise. `os.environ` is never read outside
  `config.py` — every call site goes through a named accessor.
- **Fixed design decision** (a limit, a batch size, a schedule) → a named
  constant in [`app/core/constants.py`](../app/core/constants.py), grouped by
  domain, with a comment explaining the choice. Changed only through a reviewed
  code change, never by an environment variable.

Adding a new setting is a two-place checklist, every time: the accessor in
`config.py`, and a key with a placeholder value in `.env.example`. The
`/new-setting` skill (`.agents/skills/new-setting/SKILL.md`) walks this exact
checklist and classifies a new value into the right bucket first.

`python main.py ...` loads a local `.env` automatically at startup
(python-dotenv in `main.py`); variables already set in the shell or platform
always win, so deployments configured via the environment behave unchanged.

## Coding conventions (`.agents/rules/conventions.md`)

- English throughout: code, comments, docstrings, log messages.
- Type hints are mandatory; Google-style docstrings are required on every
  module, function, and class (enforced by Ruff's `D` rules and mypy — `tests/`
  is exempt from the docstring requirement since test names carry the
  documentation).
- Logging goes through `logger = logging.getLogger(__name__)` with `%`-style
  lazy arguments — never f-strings in a log call, never `print`. `INFO` marks
  milestones, `DEBUG` is everything the code does.
  [`app/core/logging_config.py`](../app/core/logging_config.py) is the single
  place that configures format/level/routing: one stderr handler, JSON output
  when `LOG_JSON=true`, third-party library logging pinned to `WARNING` even in
  debug so "debug" means *our* code.
- Comments explain *why*, not *what*.
- `app/core/` cannot import from the rest of `app/` (it's the bottom layer).
- Dependencies in `requirements.txt` / `requirements-dev.txt` are pinned
  (`==`); bumping a pin means running `pip_audit` and the test suite before
  committing.

## Tests (`.agents/rules/tests.md`)

The suite under `tests/` is **offline-only** — no network, no external
services. Anything that needs a running service doesn't belong there.
Environment-dependent code (like `app/core/config.py`) is tested by
monkeypatching settings (`monkeypatch.setenv`/`delenv`), never by reading a
developer's real environment — see
[`tests/test_config.py`](../tests/test_config.py) for the pattern. The
recognizer's tests keep the same discipline: signals sit behind Protocols so
pure logic (`tests/test_fusion.py`, `tests/test_temporal.py`,
`tests/test_ocr_parse.py`, `tests/test_rerank.py`, and the dev-script decision
ladder in `tests/test_photo_annotate.py`, which loads `scripts/annotate_photos.py`
by path) is tested with fakes, sync is tested with
`httpx.MockTransport` (`tests/test_sync.py`), and image tests build synthetic
images in-process — no network, no model downloads. There is no fixed coverage
target: cover logic where mistakes hurt, skip trivial pass-throughs.

## The quality gate

The same six checks run locally via the `/verify` skill (or
`pre-commit run --all-files`) and in CI (`.github/workflows/ci.yml` runs this
exact list, plus a `detect-secrets` step, on pushes to `main`/`dev` and on PRs,
plus a frontend job running `npm ci`, `npm run build` and `npm test`):

```
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m compileall app main.py
python -m bandit -c pyproject.toml -r app main.py
python -m pytest
```

`pre-commit run --all-files` additionally runs `detect-secrets-hook` (baseline
in `.secrets.baseline`); `pip_audit` runs on dependency bumps. Tool versions are
pinned in `requirements-dev.txt` so every environment uses identical versions.

The Vue frontend (`frontend/`) has its own two checks — `npm run build`
(vue-tsc type-check + Vite build) and `npm test` (vitest over
`src/**/*.test.ts` in a jsdom environment, configured in
`frontend/vitest.config.ts` without the app's Vite plugin chain) — which are
not part of the Python gate; run them when touching `frontend/`. The tests
cover pure helpers in `src/lib` (the frame-quality metrics behind
auto-capture, `quality.test.ts`) and the `useAutoCapture` composable
(`src/composables/useAutoCapture.test.ts`, fake timers) — matching the same fakes-and-pure-logic
discipline as the Python suite. See [scan-frontend](scan-frontend.md).

- **Git pre-commit hook** (`.agents/git-hooks/`): the commit-time guards. One-time
  setup per clone is `git config core.hooksPath .agents/git-hooks`; *Agent
  automation* below lists what it blocks.
- **`.pre-commit-config.yaml`**: the full gate (Ruff, mypy, Bandit,
  detect-secrets, pytest) for on-demand `pre-commit run --all-files`. All hooks
  are `language: system`, so they run the same pinned tools instead of
  pre-commit managing its own environments. It is not installed as the git
  hook: `pre-commit install` refuses to run while `core.hooksPath` is set.
- **`/verify` skill**: runs the full gate, auto-fixes mechanical findings
  (`ruff check --fix`, `ruff format`), and reports one pass/fail summary.
- **`/ship` skill**: the commit → push → PR flow. Conventional Commit messages
  via heredoc, a semver bump in `pyproject.toml` per shippable change (`feat`
  → minor, `fix`/other → patch, `BREAKING CHANGE` → major). `dev` is the
  working trunk; `main` is the published default branch on GitHub that `dev`
  merges into per release. External PRs target `main`; day-to-day solo work
  commits directly to `dev`.

## Agent automation (`.agents/`)

All agent configuration lives in one folder, `.agents/`, shared by every coding
agent and by humans. There are no agent-harness hooks, so nothing depends on
which agent runs; [`AGENTS.md`](../AGENTS.md) is the entry doc and `CLAUDE.md` is
a one-line pointer to it.

- `.agents/rules/` — the always-on rules (workflow, conventions, config, tests),
  summarised in the sections above.
- `.agents/skills/` — the task skills: `/verify`, `/ship`, `/new-setting`,
  `/runbook-add`, `/tune`, and the `/openwiki*` family.
- `.agents/scratch/` — gitignored scratch space for temp scripts, probes and
  one-off outputs.
- `.agents/git-hooks/` — the enforcement layer. The extensionless `pre-commit`
  entry (pinned to LF in `.gitattributes`: sh chokes on CRLF, also on Git for
  Windows) runs `pre_commit.py`, which inspects the *staged* changes and blocks
  the commit, printing the fix for each finding, when:
  - `.env` or `.env.*` (except `.env.example`) is staged;
  - app code (`app/`, `main.py`) is staged without a semver bump of `version` in
    `pyproject.toml` (escape hatch: `VERSION_OK=1`; also needed for docs/chore/
    test-type commits and amends, because a git hook cannot see the commit
    message);
  - staged Python fails `ruff check` or `ruff format --check`;
  - an OpenWiki page is stale (its source files changed since it was last
    verified) and not staged with the commit — every stale page being staged
    satisfies it (escape hatch: `OPENWIKI_SKIP=1` with a stated reason). This
    runs the openwiki `impact` command and fails open when node is missing.

  The escape hatches are environment variables for one commit. `git commit
  --no-verify` skips the hook entirely — agents never use it, and CI runs the
  full gate regardless. Nothing formats on save, so agents run `ruff check
  --fix` / `ruff format` on the files they touched before staging.

The automation layer maintains itself through two skills: `/runbook-add` turns a
newly diagnosed failure into a greppable runbook entry in
[troubleshooting.md](troubleshooting.md), and `/tune` audits recent session
transcripts for friction (permission prompts, rediscovery, waste) and proposes
rule, hook or runbook changes.

Tool output is deliberately terse everywhere (pytest `-q -ra --no-header`,
Ruff `output-format = "concise"`, and the rules ask agents to run with
`NO_COLOR=1`): everything a tool prints is context the agent pays for. The
optional [`scripts/claude-headroom.ps1`](../scripts/claude-headroom.ps1)/`.sh`
wrappers start Claude Code behind the Headroom compression proxy (pinned in
`requirements-dev.txt`) for a further token cut on log-heavy sessions.

## Detaching from the template

[`scripts/fresh-start.sh`](../scripts/fresh-start.sh) (and the PowerShell
equivalent `fresh-start.ps1`) removes the cloned `.git` directory — dropping
the template's history and remote — and re-initializes the repo on branch
`dev` with a single "Initial commit". The working tree is left untouched. Run
it once, right after cloning, before starting real work — and enable the commit
hook afterwards (`git config core.hooksPath .agents/git-hooks`), since the reset
drops `.git/config`.
