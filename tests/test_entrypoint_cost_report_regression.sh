#!/usr/bin/env bash
# Behavioral regression test (df#300) reproducing the exact reported failure
# signature: a completed run whose run-record.json has empty nodes[] (the bad
# Archon pin's symptom) previously made post_cost_report() log
# "Posting cost report to issue #N..." and then return 0 with ZERO gh calls and
# NO diagnostic — the run looked successful while silently posting nothing.
#
# This must be RED against the pre-fix code (silent `if [ -z "$RUN_ROWS" ]; then
# return; fi`) and GREEN after Task 3's loud-diagnostic fix lands.
#
# Run: bash tests/test_entrypoint_cost_report_regression.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export GH_TOKEN="stub-token"
export CLAUDE_CODE_OAUTH_TOKEN="stub-token"

GH_CALL_COUNT=0
git() { return 0; }
export -f git
gh() { GH_CALL_COUNT=$((GH_CALL_COUNT+1)); echo "stub-title"; return 0; }
export -f gh
docker() { return 0; }
export -f docker
claude() { echo "stub"; return 0; }
export -f claude
archon() { echo "{}"; return 0; }
export -f archon

ENTRYPOINT_SOURCE_ONLY=1 source "$SCRIPT_DIR/../entrypoint.sh"

trap - ERR
set +e; set +u; set +o pipefail

# CLONE_DIR/dark-factory resolves to REPO_ROOT — see test_entrypoint_session_window.sh
# for why this holds both in this sandbox and under GitHub Actions' checkout layout.
CLONE_DIR="$(dirname "$REPO_ROOT")"
SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-cr-statedir-XXXXXX)
export SCHEDULER_STATE_DIR
ARTIFACTS_DIR=$(mktemp -d /tmp/ep-cr-artifacts-XXXXXX)
ISSUE_NUM=300
INTENT=fix
RUN_ID=test-run-cr-1

PASSED=0; FAILED=0
assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else
    echo "  FAIL: $desc — expected='$expected' got='$actual'" >&2; FAILED=$((FAILED+1))
  fi
}

# Produce a REAL run-record.json via cli.py run-record assemble, simulating the
# exact bad-pin symptom (archon exits 127, empty stdout) rather than hand-authoring
# the fixture — this exercises Task 2's capture wiring and Task 4's durable sink
# end-to-end, not just Task 3's diagnostic in isolation.
FAIL_COST_JSON=$(mktemp /tmp/ep-cr-costjson-XXXXXX)
FAIL_COST_STDERR=$(mktemp /tmp/ep-cr-coststderr-XXXXXX)
: > "$FAIL_COST_JSON"
echo "archon: command not found" > "$FAIL_COST_STDERR"
python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record assemble \
  --run-id "$RUN_ID" \
  --issue "$ISSUE_NUM" \
  --intent "$INTENT" \
  --started-at "" \
  --artifacts-dir "$ARTIFACTS_DIR" \
  --archon-cost-json "$FAIL_COST_JSON" \
  --archon-cost-exit-code 127 \
  --archon-cost-stderr-file "$FAIL_COST_STDERR" \
  --out-file "$ARTIFACTS_DIR/run-record.json"
rm -f "$FAIL_COST_JSON" "$FAIL_COST_STDERR"

echo "--- Requirement 3/6: durable record written even though nodes[] is empty ---"
DURABLE_RECORD="${SCHEDULER_STATE_DIR}/run-records/${RUN_ID}.json"
if [ -f "$DURABLE_RECORD" ]; then
  echo "  PASS: durable run-record written at ${DURABLE_RECORD}"
  PASSED=$((PASSED+1))
else
  echo "  FAIL: durable run-record NOT written for an empty-nodes run" >&2
  FAILED=$((FAILED+1))
fi

echo "--- Reproduce: empty nodes[] must not silently succeed ---"
STDERR_FILE=$(mktemp /tmp/ep-cr-stderr-XXXXXX)
post_cost_report 2>"$STDERR_FILE"
RC=$?
assert_eq "post_cost_report returns 0 (non-fatal, run still completes)" "0" "$RC"
assert_eq "zero gh calls made (nothing to post)" "0" "$GH_CALL_COUNT"

if grep -q "ERROR: cost report has zero node rows" "$STDERR_FILE"; then
  echo "  PASS: loud ERROR diagnostic emitted to stderr"
  PASSED=$((PASSED+1))
else
  echo "  FAIL: no loud diagnostic found — this is the exact df#300 silent-skip bug" >&2
  FAILED=$((FAILED+1))
fi

if grep -q "archon_cost_capture.ok=False" "$STDERR_FILE" 2>/dev/null || grep -q "archon_cost_capture.ok=false" "$STDERR_FILE" 2>/dev/null; then
  echo "  PASS: diagnostic surfaces archon_cost_capture evidence"
  PASSED=$((PASSED+1))
else
  echo "  FAIL: diagnostic does not surface archon_cost_capture evidence" >&2
  FAILED=$((FAILED+1))
fi

rm -f "$STDERR_FILE"
rm -rf "$SCHEDULER_STATE_DIR" "$ARTIFACTS_DIR"

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
