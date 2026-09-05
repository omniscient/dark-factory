#!/usr/bin/env bash
# Copy this ticket's refine-branch spec/plan onto the current (freshly forked) branch.
# Run from setup-branch (workflows/archon-dark-factory.yaml) on its two genuine
# fresh-fork paths only — never on branch reuse, never on setup-branch-resolve (#387).
#
# Usage: transfer_refine_artifacts.sh <issue-number>
#
# Non-fatal by design: every path prints a SPEC_TRANSFER: ... line to stdout and exits
# 0, matching push_gate_check.sh/oos_excise.sh's fail-open contract. A miss here is not
# an error — conformance's existing NO_SPEC=true advisory fallback is the safety net.
set -uo pipefail

ISSUE="${1:-}"

case "$ISSUE" in
  ''|*[!0-9]*)
    echo "transfer_refine_artifacts: usage: transfer_refine_artifacts.sh <issue-number>" >&2
    exit 0
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

git fetch origin >/dev/null 2>&1 || true

# Most-recently-committed refine branch first (#387 R6: a title re-slug between refine
# and plan dispatches can leave more than one refine/issue-N-* branch on origin).
_refine_branches=()
while IFS= read -r _ref; do
  [ -n "$_ref" ] && _refine_branches+=("$_ref")
done < <(git for-each-ref --sort=-committerdate --format='%(refname:short)' "refs/remotes/origin/refine/issue-${ISSUE}-*" 2>/dev/null)

if [ "${#_refine_branches[@]}" -eq 0 ]; then
  echo "SPEC_TRANSFER: none (no refine/issue-${ISSUE}-* branch on origin)"
  exit 0
fi

REFINE_REF="${_refine_branches[0]}"
if [ "${#_refine_branches[@]}" -gt 1 ]; then
  echo "transfer_refine_artifacts: ${#_refine_branches[@]} refine/issue-${ISSUE}-* branches found, selecting most recent: $REFINE_REF" >&2
fi

STAGED=0
for PREFIX in docs/superpowers/specs/ docs/superpowers/plans/; do
  FILE=$(bash "$SCRIPT_DIR/push_gate_check.sh" "$PREFIX" "$ISSUE" "$REFINE_REF")
  if [ -z "$FILE" ]; then
    continue
  fi

  # Resurrection guard (Architect Review Cycle 1): a brand new fork created after the
  # previous feat branch was merged/deleted, while its refine/issue-N-* branch still
  # exists on origin, must not re-add a file at its pre-archive path — push-and-pr's
  # next `git mv "$FILE" docs/archive/` would then collide with an existing destination.
  # Specs and plans are archived into one flat docs/archive/ and routinely share a
  # basename (e.g. the same design doc name under both specs/ and plans/), so this must
  # only fire when $FILE itself is genuinely gone from origin/main *and* the archived
  # blob at that basename is actually this file's content — not merely a same-named
  # sibling artifact that happened to get archived already.
  _archived_path="docs/archive/$(basename -- "$FILE")"
  if ! git cat-file -e "origin/main:${FILE}" 2>/dev/null \
    && git cat-file -e "origin/main:${_archived_path}" 2>/dev/null; then
    _refine_blob=$(git rev-parse "${REFINE_REF}:${FILE}" 2>/dev/null || true)
    _archived_blob=$(git rev-parse "origin/main:${_archived_path}" 2>/dev/null || true)
    if [ -n "$_refine_blob" ] && [ "$_refine_blob" = "$_archived_blob" ]; then
      echo "transfer_refine_artifacts: skipping $FILE — already archived on origin/main as ${_archived_path}" >&2
      continue
    fi
  fi

  git cat-file -e "${REFINE_REF}:$FILE" 2>/dev/null || continue

  git checkout "$REFINE_REF" -- "$FILE"
  git add "$FILE"
  # Only count it as staged if checkout+add actually produced a real change — a file
  # byte-identical to what this branch already inherited from main stages nothing, and
  # the SPEC_TRANSFER: line (Requirement 5's greppable signal) must not claim a commit
  # that didn't happen (Architect Review Cycle 1).
  if ! git diff --cached --quiet -- "$FILE"; then
    STAGED=$((STAGED + 1))
  fi
done

if [ "$STAGED" -gt 0 ]; then
  if git commit -m "docs(#${ISSUE}): copy spec/plan onto the implementation branch" >/dev/null; then
    echo "SPEC_TRANSFER: ${STAGED} file(s) from ${REFINE_REF}"
  else
    echo "SPEC_TRANSFER: none (commit failed)"
  fi
else
  echo "SPEC_TRANSFER: none (no matching spec/plan found on ${REFINE_REF} for #${ISSUE})"
fi

exit 0
