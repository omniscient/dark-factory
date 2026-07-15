#!/usr/bin/env bash
# Determine whether a committed spec/plan artifact for an issue exists on commits this
# branch has made beyond main. Used by refine-push/plan-push-and-advance
# (workflows/archon-dark-factory.yaml) to gate the push+label step on the artifact
# actually existing, rather than on the upstream command node merely being reported
# "completed" — a killed/parked agent can be misreported as completed (#212).
#
# Usage: push_gate_check.sh <artifact-prefix> <issue-number>
#   <artifact-prefix>  path prefix to search, e.g. "docs/superpowers/specs/"
#   <issue-number>     issue number to match via "#<issue-number>" in file content, or
#                       "<issue-number>" delimited by non-digits in the filename (e.g.
#                       "...issue-212-...md") — a correctly committed artifact that only
#                       names the issue in its filename must still be detected.
#
# Stdout: path of the first matching committed file, or nothing if none found.
# Exit: always 0 — "no artifact" is a valid outcome for the caller to branch on, not a
# script error. `pipefail` is enabled below (harmless here: no `set -e`, and the
# trailing `exit 0` is unconditional), so a `grep -l` finding no match still leaves the
# script printing the correct (empty) result and exiting 0.
#
# Intentionally uses `origin/main...HEAD` (merge-base three-dot) for the file-list diff
# below, NOT the two-dot `origin/main..HEAD` form used by push-and-pr's OOS
# content-identity check (memory: codebase-patterns.md, issue #250) — that two-dot form
# answers "does this file's content differ from main's current tip", a different question
# from "which files did this branch touch since it forked", which is what this check
# needs. Uses `origin/main` (not local `main`) to match scripts/oos_excise.sh and
# scripts/load_memory_context.sh — a clone config with no local `main` ref must not make
# this check fail closed.
set -uo pipefail

ARTIFACT_PREFIX="${1:?Usage: push_gate_check.sh <artifact-prefix> <issue-number>}"
ISSUE_NUM="${2:?Usage: push_gate_check.sh <artifact-prefix> <issue-number>}"

# Guard against a non-numeric issue number reaching the grep regex below (e.g. a
# stringified "null" from a bad tracker lookup) — fail closed with an empty result
# rather than let regex metacharacters silently alter matching.
case "$ISSUE_NUM" in
  ''|*[!0-9]*)
    exit 0
    ;;
esac

HAS_COMMITS=$(git rev-list --count origin/main..HEAD 2>/dev/null || echo 0)
if [ "$HAS_COMMITS" -gt 0 ]; then
  # NUL-delimited iteration (via `git diff -z` + `read -d ''`) so a committed path
  # containing whitespace is handled as a single filename, not split across args.
  while IFS= read -r -d '' _file; do
    _base=$(basename -- "$_file")
    # ISSUE_NUM is validated numeric-only above, so it is safe to interpolate directly
    # into these regexes (no metacharacter/injection risk from a malformed value).
    if [[ "$_base" =~ (^|[^0-9])${ISSUE_NUM}([^0-9]|$) ]] \
      || grep -Eq "#${ISSUE_NUM}\\b" -- "$_file" 2>/dev/null; then
      printf '%s\n' "$_file"
      exit 0
    fi
  done < <(git diff -z --name-only origin/main...HEAD -- "$ARTIFACT_PREFIX" 2>/dev/null)
fi
exit 0
