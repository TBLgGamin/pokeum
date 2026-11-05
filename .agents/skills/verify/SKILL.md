---
name: verify
description: Run the project's full local quality gate (Ruff lint, format check, mypy, compileall, Bandit, pytest) and report one pass/fail summary; auto-fix what is mechanical. Use before review, after scaffolding, or when the user asks whether the code is clean.
---

# Run the quality gate

Run each check and collect the results (do not stop at the first failure):

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m compileall app main.py
python -m bandit -c pyproject.toml -r app main.py
python -m pytest
```

Then:

1. **Auto-fix the mechanical findings**: `python -m ruff check --fix .` and `python -m ruff format .`, then re-run the failed checks.
2. **Report one summary table** — check, pass/fail, one-line cause for each failure — followed by the relevant output excerpt for anything still failing. No wall of raw logs.
3. Fix remaining failures only when the cause is unambiguous (e.g. a missing import, a stale test you just renamed); otherwise report them and let the user decide.

Notes:

- `python -m pip_audit -r requirements.txt` needs network access; run it only when the user asks for the dependency audit or a pin was changed.
- This is the same gate CI runs — a clean `/verify` means CI's tooling checks will pass. The git pre-commit hook only covers Ruff on the staged Python files plus the `.env`, semver and OpenWiki guards (`/ship` covers those).
- `/verify` is the deliberate, full pre-ship pass (Ruff, mypy, compileall, Bandit, pytest), not something to run after each edit — while iterating, run Ruff and pytest on just the files you touched.
- When iterating on test failures, loop with `python -m pytest --lf` (last failures only) and finish with one full `python -m pytest`.
