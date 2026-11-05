---
name: ship
description: Commit, push, and open a pull request the team way — Conventional Commits written via heredoc, semver bump in pyproject.toml, branch hygiene, and a standard PR format. Use when work is ready to commit or go up for review; arguments pick the scope.
argument-hint: "[commit|push|pr]"
---

# Commit, push, and PR

Scope from `$ARGUMENTS`: `commit` stops after committing, `push` after pushing, `pr` (or nothing) runs the full flow up to and including the pull request.

## 0. Preflight

- Run the `/verify` gate first and fix findings — the pre-commit hook rejects Python that fails Ruff and CI runs the full gate. Never use `--no-verify`: it skips the hook (every guard below lives there) and CI still fails.
- Review `git status` and `git diff` before staging. Stage deliberately (`git add <paths>`); never `git add .` blindly. `.env` is gitignored — if it somehow shows up, stop and tell the user.
- **Know the commit guard** — the git pre-commit hook (`.agents/git-hooks/pre_commit.py`, enabled once per clone with `git config core.hooksPath .agents/git-hooks`) runs four deterministic checks on what you staged and reports every finding at once, each with its fix:
  1. Staged `.env` / `.env.*` (other than `.env.example`) → unstage it (`git restore --staged <file>`); new keys with placeholder values belong in `.env.example`.
  2. App code (`app/`, `main.py`) without a `pyproject.toml` version bump → bump per section 3, or set `VERSION_OK=1` when the bump rides in a later commit of the same unit. (Also needed for `docs`/`chore`/`ci`/`test`/`build`/`style` commits that touch `app/` — a git hook cannot see the commit message.)
  3. Ruff lint/format findings in staged Python → `python -m ruff check --fix <files>` and `python -m ruff format <files>`, then re-stage.
  4. An OpenWiki page is stale (its source files changed since it was last verified) and not staged along → update the affected pages and stage them **with** the code (or run `/openwiki`). After a mixed code+wiki commit, re-stamp and commit the metadata:
     `node .agents/skills/openwiki/scripts/openwiki-meta.mjs record update --pages <pages>` → `git add openwiki && git commit -m "docs(openwiki): re-stamp verified heads"`. Deliberate exception: set `OPENWIKI_SKIP=1` and say why.

  The escape hatches are environment variables for that one commit — bash: `VERSION_OK=1 git commit ...`; PowerShell: `$env:VERSION_OK = "1"; git commit ...` (then `Remove-Item Env:VERSION_OK`).

## 1. Branch

- **Solo project**: committing and pushing to `dev` (the working trunk) is allowed; `main` is the published default branch that `dev` merges into per release.
- **Team project**: work on a branch named `<type>/<short-slug>` (`feat/csv-export`, `fix/parse-edge-case`); the PR targets `main`. Ask which mode applies if it is not evident from the repo (branch protection, existing PRs).

## 2. Commit — Conventional Commits, via heredoc

Message in **English**, imperative mood. Format: `<type>(<scope>): <summary>` — types `feat`, `fix`, `docs`, `refactor`, `test`, `perf`, `build`, `ci`, `chore`; scope is the touched area (`core`, `example`, `docs`). Body explains the _why_, wrapped at ~72 columns. Breaking changes get a `BREAKING CHANGE:` footer.

Always write multiline messages with a heredoc, never chained `-m` flags:

```bash
git commit -m "$(cat <<'EOF'
feat(core): add retry helper for transient failures

Centralises the backoff policy so callers stop hand-rolling sleeps.
EOF
)"
```

PowerShell equivalent: a single-quoted here-string (`@'` … `'@`, closing token at column 0).

One commit = one logical change; split unrelated work into separate commits.

## 3. Version — semver in `pyproject.toml`

Bump `version` once per shippable unit (PR or push to `dev`), driven by the highest-impact change included:

- `BREAKING CHANGE` → **major**
- `feat` → **minor**
- `fix` / everything else → **patch**

Include the bump in the (last) commit; don't make a separate "bump version" commit. When a unit spans multiple code commits, set `VERSION_OK=1` on the earlier ones and carry the bump in the final commit — the hook checks each commit individually.

## 4. Push

- `git push -u origin <branch>` for new branches; plain `git push` thereafter.
- Never force-push a shared branch. After rebasing your **own** branch only, use `git push --force-with-lease`.

## 5. Pull request

- **Title**: same shape as a commit summary — `feat(core): add retry helper`.
- **Body in English** (public repository), written via heredoc, following `.github/PULL_REQUEST_TEMPLATE.md`: *What & why*, *How tested*, and the gate checklist.
- Remote is **GitHub**: create the PR directly with `gh pr create` (heredoc body).

## 6. Report

End with: branch, commit subject(s), version bump (old → new), and the PR link or ready-to-paste PR text.
