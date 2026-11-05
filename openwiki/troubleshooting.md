---
title: Troubleshooting runbook
sources: ["app/core/**", "main.py", ".env.example"]
read-when: "a command failed with an error you recognize from this repo, or you are adding a newly diagnosed failure mode"
verified: 388a26a04ef7
---

# Troubleshooting runbook

Known failure signatures in this repo and their one-line fixes. When a command
fails with an error you don't immediately understand, search this page for the
error text before debugging. Keep entries in exactly this two-line shape (a
`pattern:` line with a backtick-wrapped regex, immediately followed by a `fix:`
line) so the page stays greppable and the `/runbook-add` skill can verify new
patterns.

When you diagnose a new repo-specific failure that took real effort to
understand, add an entry — that is how the runbook earns its keep. Keep
patterns specific enough not to false-positive on unrelated output, and keep
fixes to one actionable line. Delete entries whose failure mode no longer
exists.

## Missing configuration

pattern: `MissingSettingError`
fix: A required setting is unset - add the key to your environment/.env (see .env.example for the full key list and app/core/config.py for which accessor requires it).

## Environment not installed

pattern: `No module named (ruff|mypy|pytest|bandit|pre_commit|pip_audit)`
fix: Dev tooling is not installed in this environment - activate the venv and run `pip install -r requirements-dev.txt`.

pattern: `No module named 'app'`
fix: Run from the repository root (imports are rooted there), not from a subdirectory.

## Recognizer

pattern: `cannot (read image|decode image bytes)`
fix: OpenCV could not load the input - check the path exists and points to a real image file (identify), or that the uploaded bytes are a valid image (API /identify returns 400 for this).

pattern: `No space left on device`
fix: The disk filled - usually a sync downloading oversized card images; verify TCGDEX_IMAGE_QUALITY/EXTENSION are low/webp in constants AND that stored image_url values in reference.db actually end in low.webp (URLs are baked in at first sync; re-syncs reuse them).

pattern: `recognizer busy|identify shed: no slot`
fix: The /identify concurrency cap shed the request (503 + `Retry-After: 1`) after waiting IDENTIFY_QUEUE_TIMEOUT_SECONDS for a free slot - retry after a second, or raise IDENTIFY_MAX_CONCURRENCY (default 2) if `python main.py bench <image> --concurrency N` shows this box gains throughput from more parallel scans.

## Quality gate

pattern: `would be reformatted`
fix: Ruff's formatter would change these files and nothing formats on save - run `python -m ruff format <files>` (or `python -m ruff format .` for the whole repo), then re-stage; the pre-commit hook and CI both enforce this.

pattern: `error: Cannot find implementation or library stub`
fix: mypy cannot resolve an import - if it is a third-party package without stubs this is normally silenced by ignore_missing_imports in pyproject.toml; check the module name for a typo first.

## Git & hooks

pattern: `pre-commit: commit blocked|pre-commit hook.*failed|hook script failed`
fix: The commit hook found real findings - read the output above (it names the fix for each check: staged .env file, missing version bump, Ruff findings, stale OpenWiki) and fix them; never retry with --no-verify, which skips the hook while CI still fails.

pattern: `detect-secrets.*Potential secrets`
fix: detect-secrets flagged staged content - if it is a real secret, remove it; if it is a false positive (e.g. a hash), add a `pragma: allowlist secret` comment or extend the exclude, never commit a real credential.
