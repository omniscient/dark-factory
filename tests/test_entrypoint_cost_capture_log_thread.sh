#!/usr/bin/env bash
# Static guard (#453): both `run-record assemble` call sites in entrypoint.sh hand the
# tee'd `archon workflow run` log ($TMP_OUT) to assemble for per-node status, the
# on_failure call resolves the workflow YAML from the clone like the success path does,
# and the success path deletes $TMP_OUT only after assembling. Text-only — this test
# never sources or executes entrypoint.sh.
#
# Run: bash tests/test_entrypoint_cost_capture_log_thread.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENTRYPOINT="${SCRIPT_DIR}/../entrypoint.sh"
FAIL=0

pass() { echo "  PASS: $1"; }
fail() { echo "  FAIL: $1"; FAIL=1; }

# Same on_failure extraction as test_cost_report_harness_economics.sh (the range runs to
# _resolve_merge_conflicts and so also spans the small helpers defined between them).
ON_FAILURE_BODY=$(sed -n '/^on_failure() {$/,/^_resolve_merge_conflicts() {$/p' "$ENTRYPOINT")

if echo "$ON_FAILURE_BODY" | grep -qF -- '--archon-log-file "${TMP_OUT:-}"'; then
  pass 'on_failure assemble passes --archon-log-file "${TMP_OUT:-}"'
else
  fail 'on_failure assemble does not pass --archon-log-file "${TMP_OUT:-}"'
fi

if echo "$ON_FAILURE_BODY" | grep -qF -- '--clone-dir "${CLONE_DIR:-.}"'; then
  pass 'on_failure assemble passes --clone-dir "${CLONE_DIR:-.}"'
else
  fail 'on_failure assemble does not pass --clone-dir "${CLONE_DIR:-.}"'
fi

SUCCESS_START=$(grep -n '^# --- Capture archon cost data and assemble run record' "$ENTRYPOINT" | head -1 | cut -d: -f1)
if [ -z "$SUCCESS_START" ]; then
  fail "success-path cost-capture block not found"
else
  SUCCESS_TAIL=$(tail -n +"$SUCCESS_START" "$ENTRYPOINT")
  if echo "$SUCCESS_TAIL" | grep -qF -- '--archon-log-file "$TMP_OUT"'; then
    pass 'success-path assemble passes --archon-log-file "$TMP_OUT"'
  else
    fail 'success-path assemble does not pass --archon-log-file "$TMP_OUT"'
  fi
  ASSEMBLE_LINE=$(echo "$SUCCESS_TAIL" | grep -n 'run-record assemble' | head -1 | cut -d: -f1)
  RM_LINE=$(echo "$SUCCESS_TAIL" | grep -nE 'rm -f .*"\$TMP_OUT"' | head -1 | cut -d: -f1)
  if [ -n "$ASSEMBLE_LINE" ] && [ -n "$RM_LINE" ] && [ "$RM_LINE" -gt "$ASSEMBLE_LINE" ]; then
    pass 'success path deletes $TMP_OUT after assemble'
  else
    fail 'success path does not delete $TMP_OUT after assemble'
  fi
fi

# The workflow-run loop's success exit must no longer delete $TMP_OUT right before
# `break` — assemble would then read a deleted log.
if awk 'prev ~ /^  rm -f "\$TMP_OUT"$/ && $0 ~ /^  break$/ {found=1} {prev=$0} END {exit !found}' "$ENTRYPOINT"; then
  fail 'workflow-run loop still deletes $TMP_OUT before break'
else
  pass 'workflow-run loop keeps $TMP_OUT through break'
fi

echo ""
[ "$FAIL" -eq 0 ] && echo "OK" || echo "FAILED"
[ "$FAIL" -eq 0 ]
