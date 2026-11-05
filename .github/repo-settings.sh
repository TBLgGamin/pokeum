#!/usr/bin/env bash
# Applies the standard GitHub settings to a repository. A template copies files,
# not settings, so run this once after "Use this template", and again whenever
# the visibility changes. It is safe to run more than once.
#
#   bash .github/repo-settings.sh              # the repository of the current directory
#   bash .github/repo-settings.sh owner/name   # any repository you administer
#
# Needs the GitHub CLI (gh), logged in as an admin of the repository.
#
# Every repository:  squash merge only, with the PR title and body as the commit;
#                    delete branches after merge; "Update branch" button; wiki and
#                    projects off; Dependabot alerts and security updates on.
# Public only:       secret scanning with push protection, private vulnerability
#                    reporting, and a ruleset that blocks force pushes to and
#                    deletion of the default branch. GitHub Free offers none of
#                    these for private repositories.
#
# Run from inside a fresh clone, it also fills in the {{PROJECT}} and {{YEAR}}
# placeholders left by the template. Review and commit that change yourself.
set -euo pipefail

repo="${1:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"
visibility="$(gh api "repos/$repo" --jq .visibility)"
echo "Applying $visibility settings to $repo"

gh api --silent -X PATCH "repos/$repo" \
  -F has_wiki=false \
  -F has_projects=false \
  -F has_discussions=false \
  -F allow_squash_merge=true \
  -F allow_merge_commit=false \
  -F allow_rebase_merge=false \
  -f squash_merge_commit_title=PR_TITLE \
  -f squash_merge_commit_message=PR_BODY \
  -F delete_branch_on_merge=true \
  -F allow_update_branch=true

gh api --silent -X PUT "repos/$repo/vulnerability-alerts"
gh api --silent -X PUT "repos/$repo/automated-security-fixes"

if [ "$visibility" = "public" ]; then
  gh api --silent -X PATCH "repos/$repo" --input - <<'JSON'
{"security_and_analysis": {
  "secret_scanning": {"status": "enabled"},
  "secret_scanning_push_protection": {"status": "enabled"}
}}
JSON
  gh api --silent -X PUT "repos/$repo/private-vulnerability-reporting"

  ruleset='{
    "name": "default branch",
    "target": "branch",
    "enforcement": "active",
    "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
    "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}]
  }'
  # Reuse a ruleset that already protects the default branch, whatever its name.
  id="$(gh api "repos/$repo/rulesets" --jq '.[] | select(.target == "branch") | .id' |
    while read -r candidate; do
      gh api "repos/$repo/rulesets/$candidate" \
        --jq 'select(.conditions.ref_name.include | index("~DEFAULT_BRANCH")) | .id'
    done | head -n 1)"
  if [ -n "$id" ]; then
    echo "$ruleset" | gh api --silent -X PUT "repos/$repo/rulesets/$id" --input -
  else
    echo "$ruleset" | gh api --silent -X POST "repos/$repo/rulesets" --input -
  fi

  for file in README.md LICENSE CONTRIBUTING.md CODE_OF_CONDUCT.md SECURITY.md; do
    if ! gh api --silent "repos/$repo/contents/$file" 2>/dev/null; then
      echo "warning: $file is missing; copy it from TBLgGamin/repo-template" >&2
    fi
  done
fi

# Fill in the template placeholders when run inside a clone of this repository,
# but never in the template repositories themselves.
if [ "$(gh api "repos/$repo" --jq .is_template)" = "false" ] &&
  top="$(git rev-parse --show-toplevel 2>/dev/null)" &&
  git -C "$top" remote get-url origin 2>/dev/null | grep -qi "github.com[:/]$repo\(\.git\)\?$"; then
  files="$(git -C "$top" grep -l -e '{{PROJECT}}' -e '{{YEAR}}' -- ':!.github/repo-settings.sh' || true)"
  if [ -n "$files" ]; then
    name="${repo#*/}"
    year="$(date +%Y)"
    while IFS= read -r file; do
      sed -i "s/{{PROJECT}}/$name/g; s/{{YEAR}}/$year/g" "$top/$file"
      echo "filled placeholders in $file"
    done <<<"$files"
    echo "Review the changes, then commit them."
  fi
fi

echo "Done. Set the description, website and topics under About on the repository page."
