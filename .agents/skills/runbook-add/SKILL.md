---
name: runbook-add
description: Add a newly diagnosed failure mode to the troubleshooting runbook (openwiki/troubleshooting.md) in the exact greppable pattern/fix format, then verify the pattern actually matches. Use after diagnosing a repo-specific error that took real effort to understand, or when the user says "add this to the runbook".
argument-hint: "[short description of the failure]"
---

# Add a runbook entry

The runbook turns a one-time diagnosis into permanent knowledge: the next agent that hits the same failure searches `openwiki/troubleshooting.md` for the error text and finds the one-line `fix:` instead of re-diagnosing. A badly-formatted or over-broad entry is worse than none — follow this exactly.

## 1. Qualify

Only add entries that earn their keep:

- **Repo-specific** — generic Python errors an agent already understands don't belong here.
- **Recurring-by-nature** — misconfiguration, environment drift, tooling quirks. Not one-off typos.
- **Cost real diagnosis effort** — if the error message already says the fix, skip it.

Check the existing entries in `openwiki/troubleshooting.md` first — extend/sharpen an existing pattern rather than adding a near-duplicate.

## 2. Write the entry

Append under the fitting `##` section (or add one) in **exactly** this two-line shape:

```
pattern: `<regex that matches the failure output>`
fix: <one actionable line: what this means and what to do>
```

- The pattern is a Python regex searched against the failed command's full output. Make it **specific enough to never false-positive** on healthy output (match the exception name or a distinctive phrase, not a common word).
- The fix is ONE line, imperative, with the concrete file/command to act on.

## 3. Verify it matches

Test the pattern against a representative slice of the real error output before finishing:

```bash
echo '<paste a representative slice of the real error output>' | python -c "import re, sys; sys.exit(0 if re.search(sys.argv[1], sys.stdin.read()) else 1)" '<the pattern>'
```

Expect exit code 0. Then repeat with healthy output from the same command (a passing run) and confirm exit code 1 — a pattern that matches both is over-broad. Finally re-read the page and confirm the entry sits under the right `##` section in the exact two-line shape.

## 4. Close out

Run the wiki lint, re-stamp the page, and commit (a `docs:` commit — no version bump needed):

```bash
node .agents/skills/openwiki/scripts/openwiki-meta.mjs lint
node .agents/skills/openwiki/scripts/openwiki-meta.mjs record update --pages troubleshooting.md
```

Report: the entry added, the section it lives in, and the verification result.
