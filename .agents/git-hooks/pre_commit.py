"""Git pre-commit hook: the repo's commit-time guards, for every agent and human.

Enable it once per clone (the `pre-commit` sh script next to this file is the git
entry point and just runs this module)::

    git config core.hooksPath .agents/git-hooks

Four deterministic checks run against the *staged* changes. Every finding is
reported at once, then the commit is blocked (exit 1):

1. **No secrets** - `.env` and `.env.*` (except `.env.example`) are never committed.
2. **Version bump** - staged app code needs a semver bump of `version` in
   `pyproject.toml` (escape hatch: `VERSION_OK=1`).
3. **Ruff** - staged Python files must pass `ruff check` and `ruff format --check`.
4. **Docs travel with code** - the OpenWiki impact analysis must report no stale
   page, unless every stale page is itself staged (escape hatch: `OPENWIKI_SKIP=1`).

The escape hatches are environment variables for that one commit. A missing tool
(node, ruff) means no verdict, so that check is skipped with a note: the hook fails
open and CI still gates. `git commit --no-verify` skips this hook entirely; never
use it.

Ruff runs on the working-tree contents of the staged files, so a partially staged
file is judged by what is on disk, not by what will be committed.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
IMPACT_SCRIPT = ".agents/skills/openwiki/scripts/openwiki-meta.mjs"

# `.env` and any `.env.<suffix>` except `.env.example`, at any depth.
ENV_FILE = re.compile(r"(^|[\\/])\.env(\.(?!example$)[^\\/]*)?$")

MAX_LINES_PER_FINDING = 25


def run(args: list[str], timeout: float = 60.0) -> subprocess.CompletedProcess[str] | None:
    """Run a command from the repo root and capture its text output.

    Args:
        args: The command and its arguments.
        timeout: Seconds before the command is abandoned.

    Returns:
        The finished process, or None when it could not start or timed out.
    """
    try:
        return subprocess.run(
            args,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def note(message: str) -> None:
    """Print a non-blocking notice to stderr."""
    print(f"pre-commit: {message}", file=sys.stderr)


def indented(output: str) -> str:
    """Indent tool output under its finding header, capped to stay readable."""
    lines = output.strip().splitlines()
    if len(lines) > MAX_LINES_PER_FINDING:
        extra = len(lines) - MAX_LINES_PER_FINDING
        lines = [*lines[:MAX_LINES_PER_FINDING], f"... {extra} more line(s)"]
    return "\n".join(f"  {line}" for line in lines)


def staged_changes() -> dict[str, str]:
    """Map every staged path to its git status letter (A, M, D, T, ...).

    Renames are reported as a delete plus an add, so both paths count as touched.
    Returns an empty mapping when nothing is staged or git gives no answer.
    """
    result = run(["git", "diff", "--cached", "--name-status", "-z", "--no-renames"])
    if result is None or result.returncode != 0:
        return {}
    # `-z` output alternates status and path, NUL-terminated: "M\0a.py\0D\0b.py\0".
    fields = result.stdout.split("\0")
    return dict(zip(fields[1::2], fields[0::2], strict=False))


def check_env_files(changes: dict[str, str]) -> str | None:
    """Block staged env files that hold real secrets; staging a deletion is fine."""
    leaked = sorted(
        path for path, status in changes.items() if status != "D" and ENV_FILE.search(path)
    )
    if not leaked:
        return None
    return (
        f"Secrets: env files hold real secrets and must never be committed: {', '.join(leaked)}\n"
        "  Unstage with `git restore --staged <file>`; put new keys, with placeholder\n"
        "  values, in .env.example instead."
    )


def is_app_code(path: str) -> bool:
    """Return whether a path ships in the service: `app/` or `main.py`."""
    return path == "main.py" or path.startswith("app/")


def check_version_bump(changes: dict[str, str]) -> str | None:
    """Require a `pyproject.toml` version bump whenever app code is staged."""
    if os.environ.get("VERSION_OK") == "1" or not any(is_app_code(path) for path in changes):
        return None
    diff = run(["git", "diff", "--cached", "-U0", "--", "pyproject.toml"])
    if diff is None or re.search(r"^\+version\s*=", diff.stdout, re.MULTILINE):
        return None  # bumped, or git gave no verdict
    return (
        "Version: this commit changes app code but the version in pyproject.toml is not\n"
        "  bumped. Bump semver once per shippable unit (feat -> minor, fix -> patch,\n"
        "  BREAKING CHANGE -> major) and stage it. If the bump belongs to another commit\n"
        "  of this unit - or this is a docs/chore/ci/test/build/style commit or an\n"
        "  amend, which this hook cannot tell apart - set VERSION_OK=1 for this commit."
    )


def check_ruff(changes: dict[str, str]) -> str | None:
    """Run the Ruff lint and format checks over the staged Python files."""
    files = sorted(
        path
        for path, status in changes.items()
        if status != "D" and path.endswith(".py") and (REPO_ROOT / path).is_file()
    )
    if not files:
        return None
    if importlib.util.find_spec("ruff") is None:
        note(
            "ruff is not installed in this Python (pip install -r requirements-dev.txt); "
            "skipping the lint/format check, CI still gates"
        )
        return None
    findings = []
    for args in (["check"], ["format", "--check"]):
        result = run([sys.executable, "-m", "ruff", *args, "--force-exclude", *files])
        if result is not None and result.returncode != 0:
            findings.append(indented(result.stdout + result.stderr))
    if not findings:
        return None
    return (
        "Ruff: staged Python files have lint or format findings:\n"
        + "\n".join(findings)
        + "\n  Fix: python -m ruff check --fix <files> && python -m ruff format <files>,"
        " then re-stage."
    )


def check_openwiki(changes: dict[str, str]) -> str | None:
    """Block the commit while OpenWiki pages are stale and not staged along with it."""
    if os.environ.get("OPENWIKI_SKIP") == "1" or not (REPO_ROOT / IMPACT_SCRIPT).is_file():
        return None
    if not (REPO_ROOT / "openwiki").is_dir():
        return None
    node = shutil.which("node")
    if node is None:
        note("node not found; skipping the OpenWiki freshness check (CI still gates)")
        return None
    result = run([node, IMPACT_SCRIPT, "impact"], timeout=30.0)
    if result is None or "STALE PAGES (edit" not in result.stdout:
        return None  # no verdict, or nothing is stale

    stale = [
        line[2:].split()[0] for line in result.stdout.splitlines() if line.startswith("- openwiki/")
    ]
    # Docs riding along in the same commit are fine: when every stale page is itself
    # staged, the wiki update ships with the code that staled it. (Re-stamp with
    # `record update --pages` AFTER committing; that metadata-only commit touches just
    # openwiki/ and can never be stale.)
    if stale and all(page in changes for page in stale):
        return None
    return (
        "OpenWiki: stale pages (their source files changed since they were last verified): "
        f"{', '.join(stale) or '(see impact output)'}.\n"
        "  Update them and stage them with the code (or run /openwiki). After committing,\n"
        "  re-stamp and commit the metadata:\n"
        f"    node {IMPACT_SCRIPT} record update --pages <pages>\n"
        "  Deliberately committing without a docs update: set OPENWIKI_SKIP=1 and say why."
    )


CHECKS: tuple[Callable[[dict[str, str]], str | None], ...] = (
    check_env_files,
    check_version_bump,
    check_ruff,
    check_openwiki,
)


def main() -> int:
    """Run every check against the staged changes; block the commit on any finding.

    Returns:
        0 when the commit may proceed, 1 when at least one check found a problem.
    """
    changes = staged_changes()
    if not changes:
        return 0
    findings = [finding for check in CHECKS if (finding := check(changes))]
    if not findings:
        return 0
    print("pre-commit: commit blocked.\n", file=sys.stderr)
    print("\n\n".join(findings), file=sys.stderr)
    print("\nFix the findings and commit again. Never use --no-verify.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
