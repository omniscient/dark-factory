#!/usr/bin/env bash
# Verifies _handle_session_window_pause() (#35): a matched failure writes the sentinel
# with the correct resume epoch and returns 0 — the caller (the while-loop rewire in
# Step 4.3.4) uses that 0 to exit clean before ever reaching on_failure/run_post_mortem
# or the success-path record assembly, but that call ordering itself lives in the
# un-executable main retry loop and is verified by code review of Step 4.3.4, not by
# this test. An unmatched failure (or the kill-switch off) returns 1, signalling the
# caller to fall through to the normal failure/sleep path unchanged.
# Run: bash tests/test_entrypoint_session_window.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

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

ENTRYPOINT_SOURCE_ONLY=1 source "$SCRIPT_DIR/../entrypoint.sh"

trap - ERR
set +e; set +u; set +o pipefail

# _handle_session_window_pause resolves cli.py at "$CLONE_DIR/dark-factory/scripts/..."
# (the TARGET-PATH convention — see entrypoint.sh's existing on_failure/post_cost_report
# calls). REPO_ROOT's own basename is "dark-factory", so its parent's "dark-factory"
# child IS REPO_ROOT — this holds both in this sandbox (.../dark-factory) and under
# GitHub Actions' checkout layout (.../dark-factory/dark-factory), so the real
# branch cli.py (with this task's session-window-check subcommand) resolves correctly
# without any bootstrap/copy step.
CLONE_DIR="$(dirname "$REPO_ROOT")"
ISSUE_NUM=35
INTENT=fix
RUN_ID=test-run-1

PASSED=0; FAILED=0
assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else
    echo "  FAIL: $desc — expected='$expected' got='$actual'" >&2; FAILED=$((FAILED+1))
  fi
}
assert_true() {
  local desc="$1"; shift
  if eval "$1"; then assert_eq "$desc" "0" "0"; else assert_eq "$desc" "0" "1"; fi
}

echo "--- A: matched (structured rate_limit_event line) ---"
SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-sw-statedir-XXXXXX)
NOW=$(date -u +%s)
RESET_ISO=$(date -u -d "@$((NOW+600))" +%Y-%m-%dT%H:%M:%SZ)
TMP_OUT=$(mktemp /tmp/ep-sw-out-XXXXXX)
printf 'some claude output\n{"event":"claude.rate_limit_event","resetsAt":"%s"}\n' \
  "$RESET_ISO" > "$TMP_OUT"

SESSION_WINDOW_BACKOFF_ENABLED=true
SESSION_WINDOW_BUFFER_MINUTES=5
SESSION_WINDOW_FALLBACK_MINUTES=30
_handle_session_window_pause "$TMP_OUT"
RC=$?
assert_eq "matched → returns 0" "0" "$RC"
assert_true "sentinel written" "[ -f '${SCHEDULER_STATE_DIR}/session-window-paused' ]"
SENTINEL_EPOCH=$(cat "${SCHEDULER_STATE_DIR}/session-window-paused" 2>/dev/null || echo 0)
EXPECTED_EPOCH=$((NOW + 600 + 300))
DIFF=$((SENTINEL_EPOCH - EXPECTED_EPOCH)); DIFF=${DIFF#-}
assert_true "resume epoch within 2s of resetsAt+buffer" "[ '$DIFF' -le 2 ]"

echo ""
echo "--- B: unmatched (unrelated failure) — falls through to normal failure path ---"
rm -f "${SCHEDULER_STATE_DIR}/session-window-paused"
TMP_OUT2=$(mktemp /tmp/ep-sw-out2-XXXXXX)
echo "some unrelated stack trace" > "$TMP_OUT2"
_handle_session_window_pause "$TMP_OUT2"
RC2=$?
assert_eq "unmatched → returns 1" "1" "$RC2"
assert_true "no sentinel written for unmatched failure" \
  "[ ! -f '${SCHEDULER_STATE_DIR}/session-window-paused' ]"

echo ""
echo "--- C: kill-switch off — falls through even on a matched signal ---"
SESSION_WINDOW_BACKOFF_ENABLED=false
_handle_session_window_pause "$TMP_OUT"
RC3=$?
assert_eq "kill-switch off → returns 1" "1" "$RC3"
assert_true "no sentinel written when kill-switch off" \
  "[ ! -f '${SCHEDULER_STATE_DIR}/session-window-paused' ]"
SESSION_WINDOW_BACKOFF_ENABLED=true

rm -f "$TMP_OUT" "$TMP_OUT2"
rm -rf "$SCHEDULER_STATE_DIR"

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
