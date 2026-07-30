#!/usr/bin/env bash
# Maps the implement-phase INTENT (new|continue) to context_budget.py's --scenario
# vocabulary and invokes it. Backs the budget-implement workflow node — a faithful
# extraction of what used to be duplicated inline logic (#280), mirroring the #183
# budget_gate.sh extraction for the enforce-budget-* nodes.
#
# Usage: budget_context.sh
# Env:   ARTIFACTS_DIR (required) — must contain issue.json; context-budget.json is
#                   written here
#        CLONE_DIR (optional, default ".") — clone root; CWD is the clone root in
#                   bash workflow nodes, so "." is the correct default there
#        RUN_ID (optional, defaults to basename of ARTIFACTS_DIR — matches the
#                   inline fallback this script replaces)
#
# Exit codes:
#   0 = ran to completion, including when context_budget.py itself failed (fail-open —
#       budget telemetry must never block an implement dispatch)
#   1 = unknown/missing INTENT in issue.json (fail loud — a real bug, not telemetry
#       noise; matches the un-wrapped `case` guard this script replaces)
#   2 = usage error (ARTIFACTS_DIR unset or issue.json missing)

if [ -z "${ARTIFACTS_DIR:-}" ]; then
  echo "budget_context.sh: ARTIFACTS_DIR must be set" >&2
  exit 2
fi

if [ ! -f "$ARTIFACTS_DIR/issue.json" ]; then
  echo "budget_context.sh: $ARTIFACTS_DIR/issue.json not found" >&2
  exit 2
fi

_CLONE="${CLONE_DIR:-.}"
_RUN="${RUN_ID:-$(basename "${ARTIFACTS_DIR:-/tmp/budget}")}"
_SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"

ISSUE=$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")
INTENT=$(jq -r '.intent' "$ARTIFACTS_DIR/issue.json")

case "$INTENT" in
  new)      SCENARIO=implement ;;
  continue) SCENARIO=continue ;;
  *) echo "budget_context.sh: unexpected INTENT='$INTENT'; expected new or continue" >&2
     exit 1 ;;
esac

# memory-context.md is written inside the command session by memory_retrieve.py (Phase 1
# load), so it is reported as dropped/empty_or_missing here — expected, unchanged.
python3 "${_SCRIPT_DIR}/context_budget.py" \
  --scenario "$SCENARIO" \
  --issue-num "$ISSUE" \
  --run-id "$_RUN" \
  --artifacts-dir "$ARTIFACTS_DIR" \
  --clone-dir "$_CLONE" \
  --issue-json "$ARTIFACTS_DIR/issue.json" \
  --memory-file "$ARTIFACTS_DIR/memory-context.md" \
  --comment-digest-file "$ARTIFACTS_DIR/comment-digest.md" \
  --out "$ARTIFACTS_DIR/context-budget.json" || true

if [ ! -s "$ARTIFACTS_DIR/context-budget.json" ]; then
  echo "WARNING: budget_context.sh: $ARTIFACTS_DIR/context-budget.json was not written (context_budget.py invocation failed)" >&2
fi

exit 0
