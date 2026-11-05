---
name: tune
description: Self-audit the repo's agent setup against recent session friction — recurring permission prompts, repeatedly rediscovered facts that belong in rules or the runbook, commit-hook blocks that confused rather than helped — and propose concrete diffs to the rules, the pre-commit hook, AGENTS.md, or the runbook. Use periodically ("tune the repo", "reduce friction"), or after a session that felt slow or repetitive.
---

# Tune the agent setup

The repo's automation (the git pre-commit hook, rules, skills, runbook, wiki contracts) should absorb friction over time. This skill is the feedback loop: find where recent sessions lost time or tokens, turn each finding into a one-line config/rule change, apply what's approved.

## 1. Gather evidence

Look at the most recent session transcripts for THIS project (newest first, skip the current session). Your agent harness keeps them locally per project (usually as `*.jsonl` files); if you don't know where, ask the user.

Read them **cheaply**: Grep for friction signatures rather than reading transcripts whole. Signatures worth counting:

- **Permission prompts**: tool calls that required user approval — recurring safe read-only commands are allowlist candidates. (The allowlist lives in the user's own harness config, outside the repo: propose the entries, the user applies them.)
- **Blocked commits**: pre-commit hook output (`pre-commit: commit blocked`) — did the agent recover in one step? A guard that needed multiple retries needs a clearer message in `.agents/git-hooks/pre_commit.py` or a rule.
- **Rediscovery**: the same file read many times across sessions, the same error diagnosed twice (runbook candidate — hand off to `/runbook-add`), the same question answered repeatedly (rule/AGENTS.md candidate).
- **Waste**: whole-repo `ruff`/`mypy` sweeps where the touched files would do, verbose flags (`-v`, full `git log`), full pytest runs where `--lf` or a single test would do.

Also run `node .agents/skills/openwiki/scripts/openwiki-meta.mjs impact` — chronic coverage gaps mean the wiki contracts need widening.

## 2. Propose

Produce a short table: **finding → evidence (count/example) → proposed one-line change → target file**. Targets, in order of preference:

1. `.agents/rules/*.md` — one terse line in the right rule file; don't grow rules with prose.
2. `.agents/git-hooks/pre_commit.py` — a clearer block message, or a check worth adding or removing (keep it deterministic and fast).
3. `openwiki/troubleshooting.md` — via `/runbook-add`.
4. `AGENTS.md` — only for things every session needs in the first screen.
5. The user's harness allowlist (outside the repo) — read-only, deterministic commands only; never allowlist anything destructive.

Hard limits: propose at most ~5 changes per tune; each must trace to observed friction, not speculation. If nothing recurring surfaced, say the setup is tuned — that is a valid outcome.

## 3. Apply & close out

Apply the approved changes. If rules or the hook changed, the wiki's workflow page may go stale — update and re-stamp per the pre-commit hook's instructions, and ship as a `chore(agents):` commit. Report what was tuned and what measurable friction it removes.
