## What & why

<!-- What changes, and what problem it solves. Link related issues. -->

## How tested

<!-- Gate results, new/updated tests, manual checks (device/browser for frontend changes). -->

## Checklist

- [ ] The PR title is a conventional commit (for example `fix(vision): ...`); it becomes the squash commit message
- [ ] `ruff check` / `ruff format --check` / `mypy` / `bandit` / `pytest` pass locally
- [ ] Frontend touched → `npm run build` passes
- [ ] New settings follow the checklist (accessor in `app/core/config.py` + key in `.env.example`)
- [ ] Affected `openwiki/` pages updated in this PR
- [ ] `version` bumped in `pyproject.toml` (feat → minor, fix → patch)
