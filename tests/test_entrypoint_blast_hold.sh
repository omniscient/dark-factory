#!/usr/bin/env bash
# Verifies _handle_blast_hold() (#551): with $ARTIFACTS_DIR/blast-hold.json present it
# records a blast_hold run-record row, posts the cost report and returns 0 -- writing no
# error signature and leaving retry counters untouched. Without the marker it returns 1
# so the failure path runs unchanged. The main retry loop is not executable here
# (mirrors tests/test_entrypoint_session_window.sh), so its call order is pinned by a
# static check at the end.
# Run: bash tests/test_entrypoint_blast_hold.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export IDENTITY_SH="${IDENTITY_SH:-$REPO_ROOT/scripts/identity.sh}"
export FACTORY_PROVIDERS_CLI="${FACTORY_PROVIDERS_CLI:-$REPO_ROOT/scripts/factory_core/providers/cli.py}"
export GH_TOKEN="stub-token"
export CLAUDE_CODE_OAUTH_TOKEN="stub-token"

git() { return 0; }
export -f git
gh() { echo "stub-title"; return 0; }
export -f gh
docker() { return 0; }
export -f docker
claude() { echo "stub"; return 0; }
export -f claude

# _handle_blast_hold corroborates the marker against the tracker's real needs-discussion
# label (providers/cli.py tracker get --fields labels) before trusting it. Stub only that
# one provider call -- real python3 still runs factory_core/cli.py run-record record (so
# the run-record assertions below stay meaningful) and anything else unrelated.
_REAL_PY3="$(command -v python3)"
export _REAL_PY3
export STUB_TRACKER_LABELS='{"labels":[{"name":"needs-discussion"}]}'
python3() {
  case "$*" in
    *providers/cli.py*tracker\ get*) echo "$STUB_TRACKER_LABELS"; return 0 ;;
    *) "$_REAL_PY3" "$@" ;;
  esac
}
export -f python3

# Never inherit a live run's hold-marker path (#551 architect cycle 2).
unset FACTORY_BLAST_HOLD_FILE
CURRENT_RUN_DIR=$(mktemp -d /tmp/ep-bh-rundir-XXXXXX)
export CURRENT_RUN_DIR
SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-bh-statedir-XXXXXX)
export SCHEDULER_STATE_DIR

ENTRYPOINT_SOURCE_ONLY=1 source "$SCRIPT_DIR/../entrypoint.sh"

trap - ERR
set +e; set +u; set +o pipefail

# Same TARGET-PATH layout trick as tests/test_entrypoint_session_window.sh.
CLONE_DIR="$(dirname "$REPO_ROOT")"
ISSUE_NUM=551
INTENT=continue
RUN_ID=test-run-bh-1
export SCHEDULER_STATE_DIR

PASSED=0; FAILED=0
check() {
  if eval "$2"; then echo "  PASS: $1"; PASSED=$((PASSED+1));
  else echo "  FAIL: $1" >&2; FAILED=$((FAILED+1)); fi
}

COST_REPORT_CALLS=0
post_cost_report() { COST_REPORT_CALLS=$((COST_REPORT_CALLS+1)); }
SIG_CALLS=0
_write_error_signature() { SIG_CALLS=$((SIG_CALLS+1)); }
POST_MORTEM_CALLS=0
run_post_mortem() { POST_MORTEM_CALLS=$((POST_MORTEM_CALLS+1)); }

echo "--- A: marker present -> hold, not a failure ---"
ARTIFACTS_DIR=$(mktemp -d /tmp/ep-bh-artifacts-XXXXXX)
echo '{"issue":551,"head_sha":"abc123def","exit":1}' > "$ARTIFACTS_DIR/blast-hold.json"
echo '{"retries":{"551":2}}' > "$SCHEDULER_STATE_DIR/scheduler-state.json"
BEFORE=$(cat "$SCHEDULER_STATE_DIR/scheduler-state.json")
_handle_blast_hold
RC=$?
check "returns 0" '[ "$RC" = 0 ]'
check "cost report posted once" '[ "$COST_REPORT_CALLS" = 1 ]'
check "no error signature written" '[ "$SIG_CALLS" = 0 ] && [ ! -d "$SCHEDULER_STATE_DIR/error-signatures" ]'
check "no post-mortem" '[ "$POST_MORTEM_CALLS" = 0 ]'
check "retry counters untouched" '[ "$(cat "$SCHEDULER_STATE_DIR/scheduler-state.json")" = "$BEFORE" ]'
ROW=$(grep '"run_id": "test-run-bh-1"' "$SCHEDULER_STATE_DIR/runs.jsonl" 2>/dev/null | tail -1)
check "blast_hold run-record row" '[ "$(echo "$ROW" | jq -r .stage)" = blast_hold ]'
check "verdict human_required" '[ "$(echo "$ROW" | jq -r .verdict)" = human_required ]'
check "row carries the held head" '[ "$(echo "$ROW" | jq -r .detail.head_sha)" = abc123def ]'

echo "--- A2: marker at FACTORY_BLAST_HOLD_FILE, ARTIFACTS_DIR elsewhere (the production layout) ---"
# Archon gives each bash node its own ARTIFACTS_DIR, so writer and reader never share it.
NODE_DIR=$(mktemp -d /tmp/ep-bh-node-XXXXXX)
echo '{"issue":551,"head_sha":"feedface0","exit":1}' > "$NODE_DIR/blast-hold.json"
rm -f "$ARTIFACTS_DIR/blast-hold.json"
COST_REPORT_CALLS=0
FACTORY_BLAST_HOLD_FILE="$NODE_DIR/blast-hold.json" _handle_blast_hold
RC=$?
check "returns 0 from the exported path" '[ "$RC" = 0 ] && [ "$COST_REPORT_CALLS" = 1 ]'
FACTORY_BLAST_HOLD_FILE="$NODE_DIR/missing.json" _handle_blast_hold
RC=$?
check "exported path wins over ARTIFACTS_DIR (absent there -> 1)" '[ "$RC" = 1 ]'
rm -rf "$NODE_DIR"

echo "--- A2b: marker present but the tracker never got needs-discussion -> not trusted ---"
echo '{"issue":551,"head_sha":"nolabel","exit":1}' > "$ARTIFACTS_DIR/blast-hold.json"
COST_REPORT_CALLS=0
STUB_TRACKER_LABELS='{"labels":[]}' _handle_blast_hold
RC=$?
check "uncorroborated marker → returns 1" '[ "$RC" = 1 ] && [ "$COST_REPORT_CALLS" = 0 ]'

echo "--- A3: a marker for another issue is ignored ---"
echo '{"issue":77,"head_sha":"0ther","exit":1}' > "$ARTIFACTS_DIR/blast-hold.json"
COST_REPORT_CALLS=0
_handle_blast_hold
RC=$?
check "other issue's marker → returns 1" '[ "$RC" = 1 ] && [ "$COST_REPORT_CALLS" = 0 ]'
SAVED_ISSUE="$ISSUE_NUM"; unset ISSUE_NUM
echo '{"issue":551,"head_sha":"x","exit":1}' > "$ARTIFACTS_DIR/blast-hold.json"
_handle_blast_hold
RC=$?
check "ISSUE_NUM unset → returns 1" '[ "$RC" = 1 ]'
ISSUE_NUM="$SAVED_ISSUE"

echo "--- B: no marker -> falls through ---"
rm -f "$ARTIFACTS_DIR/blast-hold.json"
COST_REPORT_CALLS=0
_handle_blast_hold
RC=$?
check "returns 1" '[ "$RC" = 1 ]'
check "no cost report from the hold path" '[ "$COST_REPORT_CALLS" = 0 ]'

echo "--- C: ARTIFACTS_DIR unset -> falls through ---"
SAVED="$ARTIFACTS_DIR"; unset ARTIFACTS_DIR
_handle_blast_hold
RC=$?
check "returns 1" '[ "$RC" = 1 ]'
ARTIFACTS_DIR="$SAVED"

echo "--- D: main loop checks the hold right after the session-window pause ---"
LOOP=$(awk '/^while true; do$/,/^done$/' "$REPO_ROOT/entrypoint.sh")
SW=$(printf '%s\n' "$LOOP" | grep -n '_handle_session_window_pause "\$TMP_OUT"' | head -1 | cut -d: -f1)
BH=$(printf '%s\n' "$LOOP" | grep -n '_handle_blast_hold' | head -1 | cut -d: -f1)
PM=$(printf '%s\n' "$LOOP" | grep -n 'run_post_mortem' | head -1 | cut -d: -f1)
SIG=$(printf '%s\n' "$LOOP" | grep -n '_write_error_signature' | head -1 | cut -d: -f1)
check "hold check present in the loop" '[ -n "$BH" ]'
check "after the session-window check" '[ "$SW" -lt "$BH" ]'
check "before post-mortem and the error signature" '[ "$BH" -lt "$PM" ] && [ "$BH" -lt "$SIG" ]'
EXPORT=$(grep -n '^export FACTORY_BLAST_HOLD_FILE="\$ARTIFACTS_DIR/blast-hold.json"$' "$REPO_ROOT/entrypoint.sh" | head -1 | cut -d: -f1)
LOOP_START=$(grep -n '^while true; do$' "$REPO_ROOT/entrypoint.sh" | head -1 | cut -d: -f1)
check "FACTORY_BLAST_HOLD_FILE exported before the workflow loop" '[ -n "$EXPORT" ] && [ "$EXPORT" -lt "$LOOP_START" ]'
check "hold branch exits 0" '[ -n "$BH" ] && printf "%s\n" "$LOOP" | sed -n "${BH},$((BH+3))p" | grep -q "exit 0"'

rm -rf "$ARTIFACTS_DIR" "$SCHEDULER_STATE_DIR" "$CURRENT_RUN_DIR"
echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
