#!/usr/bin/env bash
# Determine whether a committed spec/plan artifact for an issue exists on commits this
# branch has made beyond main. Used by refine-push/plan-push-and-advance
# (workflows/archon-dark-factory.yaml) to gate the push+label step on the artifact
# actually existing, rather than on the upstream command node merely being reported
# "completed" — a killed/parked agent can be misreported as completed (#212).
#
# Usage: push_gate_check.sh <artifact-prefix> <issue-number>
#   <artifact-prefix>  path prefix to search, e.g. "docs/superpowers/specs/"
#   <issue-number>     issue number to match via "#<issue-number>" in file content
#
# Stdout: path of the first matching committed file, or nothing if none found.
# Exit: always 0 — "no artifact" is a valid outcome for the caller to branch on, not a
# script error. `pipefail` is enabled below (harmless here: no `set -e`, and the
# trailing `exit 0` is unconditional), so a `grep -l` finding no match still leaves the
# script printing the correct (empty) result and exiting 0.
#
# Intentionally uses `main...HEAD` (merge-base three-dot) for the file-list diff below,
# NOT the two-dot `main..HEAD` form used by push-and-pr's OOS content-identity check
# (memory: codebase-patterns.md, issue #250) — that two-dot form answers "does this
# file's content differ from main's current tip", a different question from "which
# files did this branch touch since it forked", which is what this check needs.
set -uo pipefail

ARTIFACT_PREFIX="${1:?Usage: push_gate_check.sh <artifact-prefix> <issue-number>}"
ISSUE_NUM="${2:?Usage: push_gate_check.sh <artifact-prefix> <issue-number>}"

HAS_COMMITS=$(git rev-list --count main..HEAD 2>/dev/null || echo 0)
if [ "$HAS_COMMITS" -gt 0 ]; then
  git diff --name-only main...HEAD -- "$ARTIFACT_PREFIX" 2>/dev/null \
    | xargs -r grep -l "#${ISSUE_NUM}\b" 2>/dev/null | head -1
fi
exit 0
