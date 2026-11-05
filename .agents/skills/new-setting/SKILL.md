---
name: new-setting
description: Add a configuration value to the project the right way — classify it (environment setting or design constant) and walk the full checklist so none of the places is forgotten. Use whenever a new environment variable or constant is introduced.
argument-hint: "[SETTING_NAME]"
---

# Add a configuration value

Target: `$ARGUMENTS` (ask if no name was given). First classify, then follow the matching checklist below.

## 1. Classify the value

- **Varies per environment** (endpoint, log level, API key) → it is a *setting*; continue with step 2.
- **Fixed design choice** (limit, batch size, schedule) → it is a *constant*: add it to `app/core/constants.py`, UPPER_CASE, grouped per domain, with a comment explaining the choice. Done — the settings files are not involved.

## 2. The two places for a setting — both of them, every time

1. **Accessor in `app/core/config.py`** — `config.require("NAME")` when security- or correctness-critical (fail fast), `config.get("NAME", default)` otherwise. Never `os.environ` at call sites.
2. **`.env.example`** — add the key with a placeholder value so the next developer knows it exists. Never commit real values; `.env` itself is gitignored and off-limits to the agent (never read or edit it; the pre-commit hook refuses to commit it) — tell the user which key to mirror there.

## 3. Close out

Use the new accessor at the call sites, then run `/verify`.
