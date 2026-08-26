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

# entrypoint.sh hardcodes /opt/dark-factory/scripts/* for identity and the providers
# CLI, which only exists in the factory image. Point both at the repo checkout so this
# test runs on a bare CI runner (mirrors tests/test_entrypoint_current_run.sh).
_REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export IDENTITY_SH="${IDENTITY_SH:-$_REPO_DIR/scripts/identity.sh}"
export FACTORY_PROVIDERS_CLI="${FACTORY_PROVIDERS_CLI:-$_REPO_DIR/scripts/factory_core/providers/cli.py}"
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

echo "--- A: matched (structured rate_limit_event line, real pino shape, status=rejected) ---"
SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-sw-statedir-XXXXXX)
NOW=$(date -u +%s)
RESET_EPOCH=$((NOW+600))
TMP_OUT=$(mktemp /tmp/ep-sw-out-XXXXXX)
printf 'some claude output\n{"level":40,"time":%s000,"rateLimitInfo":{"status":"rejected","resetsAt":%s,"rateLimitType":"five_hour"},"msg":"claude.rate_limit_event"}\n' \
  "$NOW" "$RESET_EPOCH" > "$TMP_OUT"

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
echo "--- A2: unmatched (structured rate_limit_event line, status=allowed) — direct #332 regression lock ---"
rm -f "${SCHEDULER_STATE_DIR}/session-window-paused"
TMP_OUT_ALLOWED=$(mktemp /tmp/ep-sw-out-allowed-XXXXXX)
printf 'some claude output\n{"level":40,"rateLimitInfo":{"status":"allowed","resetsAt":%s,"rateLimitType":"five_hour"},"msg":"claude.rate_limit_event"}\n' \
  "$((NOW+18000))" > "$TMP_OUT_ALLOWED"
_handle_session_window_pause "$TMP_OUT_ALLOWED"
RC_ALLOWED=$?
assert_eq "status=allowed only → returns 1 (falls through)" "1" "$RC_ALLOWED"
assert_true "no sentinel written for status=allowed" \
  "[ ! -f '${SCHEDULER_STATE_DIR}/session-window-paused' ]"
rm -f "$TMP_OUT_ALLOWED"

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

# Stubs archon (the "archon workflow cost" call inside on_failure()'s always-run
# cost-capture block) once, shared by every section below (D-H) that calls on_failure()
# directly — not just section D.
archon() { echo "{}"; return 0; }
export -f archon

echo ""
echo "--- D: on_failure() guard — matched signal suppresses post-mortem/board-claim, posts pause comment ---"
SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-sw-statedir-d-XXXXXX)
export SCHEDULER_STATE_DIR
ARTIFACTS_DIR=$(mktemp -d /tmp/ep-sw-artifacts-d-XXXXXX)
export ARTIFACTS_DIR
ISSUE_NUM=292
INTENT=fix
RUN_ID=test-run-d1
RUN_STARTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
SESSION_WINDOW_BACKOFF_ENABLED=true

POST_MORTEM_CALLS=0
run_post_mortem() {
  POST_MORTEM_CALLS=$((POST_MORTEM_CALLS+1))
  post_or_update_comment "$DF_POST_MORTEM_MARKER" "${DF_POST_MORTEM_MARKER}
stub post-mortem"
}
BOARD_STATUS_CALLS=0
set_board_status() { BOARD_STATUS_CALLS=$((BOARD_STATUS_CALLS+1)); return 0; }
COMMENT_LOG_DIR=$(mktemp -d /tmp/ep-sw-comments-d-XXXXXX)
post_or_update_comment() {
  local marker="$1" body="$2"
  local safe
  safe=$(echo "$marker" | tr -cd 'a-zA-Z0-9')
  echo "$body" > "${COMMENT_LOG_DIR}/${safe}.md"
}
COST_REPORT_CALLS=0
post_cost_report() { COST_REPORT_CALLS=$((COST_REPORT_CALLS+1)); }

RESET_EPOCH_D=$(( $(date -u +%s) + 600 ))
TMP_OUT=$(mktemp /tmp/ep-sw-out-d-XXXXXX)
printf 'some claude output\n{"level":40,"rateLimitInfo":{"status":"rejected","resetsAt":%s,"rateLimitType":"five_hour"},"msg":"claude.rate_limit_event"}\n' \
  "$RESET_EPOCH_D" > "$TMP_OUT"

false
on_failure
# Required for sections E-H's fallthrough path: on_failure()'s existing (unchanged)
# archon-cost-capture block does its own internal `set +e; ...; set -e` around the
# `archon workflow cost` call, which leaks `set -e` back into THIS shell once
# on_failure() returns, since it's a plain function call, not a subshell (harmless in
# production — on_failure only ever runs once via the ERR trap right before the
# container exits). In section D specifically this is a no-op today (the guard
# `return`s before ever reaching that block) — reset anyway so this section stays
# resilient if a future edit changes that, and so the pattern stays uniform across D-H.
set +e

assert_eq "run_post_mortem NOT called on matched session-window path" "0" "$POST_MORTEM_CALLS"
assert_eq "set_board_status NOT called on matched session-window path" "0" "$BOARD_STATUS_CALLS"
assert_eq "post_cost_report still called exactly once" "1" "$COST_REPORT_CALLS"
assert_true "pause comment posted under the session-window marker" \
  "[ -f '${COMMENT_LOG_DIR}/dfsessionwindowpause.md' ]"
assert_true "pause comment exists and does not claim a board move" \
  "[ -f '${COMMENT_LOG_DIR}/dfsessionwindowpause.md' ] && ! grep -q 'Blocked' '${COMMENT_LOG_DIR}/dfsessionwindowpause.md'"
assert_true "pause comment exists and does not include a retry snippet" \
  "[ -f '${COMMENT_LOG_DIR}/dfsessionwindowpause.md' ] && ! grep -q 'Retry' '${COMMENT_LOG_DIR}/dfsessionwindowpause.md'"
assert_true "no df-post-mortem comment produced" \
  "[ ! -f '${COMMENT_LOG_DIR}/dfpostmortem.md' ]"
assert_true "no df-factory-failure comment produced" \
  "[ ! -f '${COMMENT_LOG_DIR}/dffactoryfailure.md' ]"
assert_true "runs.jsonl records a paused stage" \
  "grep -q '\"stage\": \"paused\"' '${SCHEDULER_STATE_DIR}/runs.jsonl'"
assert_true "runs.jsonl records no failed stage for this run" \
  "! grep -q '\"stage\": \"failed\"' '${SCHEDULER_STATE_DIR}/runs.jsonl'"
assert_true "no error signature written on the paused path" \
  "[ ! -d '${SCHEDULER_STATE_DIR}/error-signatures' ]"
assert_true "pause comment includes a classification summary line" \
  "grep -q 'Classification: matched' '${COMMENT_LOG_DIR}/dfsessionwindowpause.md'"
assert_true "pause comment classification names the rejected status" \
  "grep -q 'status=rejected' '${COMMENT_LOG_DIR}/dfsessionwindowpause.md'"
assert_true "runs.jsonl paused record includes matched_pattern detail" \
  "grep -q 'claude.rate_limit_event' '${SCHEDULER_STATE_DIR}/runs.jsonl'"

rm -f "$TMP_OUT"
rm -rf "$SCHEDULER_STATE_DIR" "$ARTIFACTS_DIR" "$COMMENT_LOG_DIR"

echo ""
echo "--- D2: on_failure() guard — substring-path classification summary (branch label, no backticks in matched text) ---"
SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-sw-statedir-d2-XXXXXX)
export SCHEDULER_STATE_DIR
ARTIFACTS_DIR=$(mktemp -d /tmp/ep-sw-artifacts-d2-XXXXXX)
export ARTIFACTS_DIR
ISSUE_NUM=292
INTENT=fix
RUN_ID=test-run-d2
RUN_STARTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
SESSION_WINDOW_BACKOFF_ENABLED=true

run_post_mortem() { :; }
set_board_status() { return 0; }
COMMENT_LOG_DIR=$(mktemp -d /tmp/ep-sw-comments-d2-XXXXXX)
post_or_update_comment() {
  local marker="$1" body="$2"
  local safe
  safe=$(echo "$marker" | tr -cd 'a-zA-Z0-9')
  echo "$body" > "${COMMENT_LOG_DIR}/${safe}.md"
}
post_cost_report() { :; }

TMP_OUT=$(mktemp /tmp/ep-sw-out-d2-XXXXXX)
printf "You've hit your session limit · resets 11:10pm (UTC)\n" > "$TMP_OUT"

false
on_failure
set +e  # see the comment on section D's on_failure() call for why this is required

assert_true "substring-path pause comment names the usage/session-limit branch with an offset" \
  "grep -q 'usage/session-limit branch) at offset' '${COMMENT_LOG_DIR}/dfsessionwindowpause.md'"
assert_true "substring-path matched text has no stray backtick in the comment" \
  "! grep -qE 'matched \`[^\`]*\`[^\`]*\`' '${COMMENT_LOG_DIR}/dfsessionwindowpause.md'"

rm -f "$TMP_OUT"
rm -rf "$SCHEDULER_STATE_DIR" "$ARTIFACTS_DIR" "$COMMENT_LOG_DIR"

echo ""
echo "--- E: on_failure() guard — falls through to normal failure path when \$TMP_OUT is unset ---"
SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-sw-statedir-e-XXXXXX)
export SCHEDULER_STATE_DIR
ARTIFACTS_DIR=$(mktemp -d /tmp/ep-sw-artifacts-e-XXXXXX)
export ARTIFACTS_DIR
ISSUE_NUM=292
INTENT=fix
RUN_ID=test-run-e1
RUN_STARTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
unset TMP_OUT

POST_MORTEM_CALLS=0
run_post_mortem() { POST_MORTEM_CALLS=$((POST_MORTEM_CALLS+1)); }
BOARD_STATUS_CALLS=0
set_board_status() { BOARD_STATUS_CALLS=$((BOARD_STATUS_CALLS+1)); return 0; }
COMMENT_LOG_DIR=$(mktemp -d /tmp/ep-sw-comments-e-XXXXXX)
post_or_update_comment() {
  local marker="$1" body="$2"
  local safe
  safe=$(echo "$marker" | tr -cd 'a-zA-Z0-9')
  echo "$body" > "${COMMENT_LOG_DIR}/${safe}.md"
}
COST_REPORT_CALLS=0
post_cost_report() { COST_REPORT_CALLS=$((COST_REPORT_CALLS+1)); }

false
on_failure
set +e  # see the comment on section D's on_failure() call for why this is required

assert_eq "run_post_mortem IS called when \$TMP_OUT is unset (normal failure path)" "1" "$POST_MORTEM_CALLS"
assert_eq "set_board_status IS called when \$TMP_OUT is unset" "1" "$BOARD_STATUS_CALLS"
assert_true "df-factory-failure comment produced" \
  "[ -f '${COMMENT_LOG_DIR}/dffactoryfailure.md' ]"
assert_true "runs.jsonl records a failed stage" \
  "grep -q '\"stage\": \"failed\"' '${SCHEDULER_STATE_DIR}/runs.jsonl'"

rm -rf "$SCHEDULER_STATE_DIR" "$ARTIFACTS_DIR" "$COMMENT_LOG_DIR"

echo ""
echo "--- E2: on_failure() guard — falls through when \$TMP_OUT is stale (set but the file is already gone) ---"
SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-sw-statedir-e2-XXXXXX)
export SCHEDULER_STATE_DIR
ARTIFACTS_DIR=$(mktemp -d /tmp/ep-sw-artifacts-e2-XXXXXX)
export ARTIFACTS_DIR
ISSUE_NUM=292
INTENT=fix
RUN_ID=test-run-e2-1
RUN_STARTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
# This is the case that actually occurs in production: after the main retry loop
# completes, $TMP_OUT still holds its mktemp path but the file itself has already been
# rm -f'd — this is the sole reason the guard's `[ -f "$TMP_OUT" ]` half exists.
TMP_OUT=$(mktemp /tmp/ep-sw-out-e2-XXXXXX)
rm -f "$TMP_OUT"

POST_MORTEM_CALLS=0
run_post_mortem() { POST_MORTEM_CALLS=$((POST_MORTEM_CALLS+1)); }
BOARD_STATUS_CALLS=0
set_board_status() { BOARD_STATUS_CALLS=$((BOARD_STATUS_CALLS+1)); return 0; }
COMMENT_LOG_DIR=$(mktemp -d /tmp/ep-sw-comments-e2-XXXXXX)
post_or_update_comment() {
  local marker="$1" body="$2"
  local safe
  safe=$(echo "$marker" | tr -cd 'a-zA-Z0-9')
  echo "$body" > "${COMMENT_LOG_DIR}/${safe}.md"
}
COST_REPORT_CALLS=0
post_cost_report() { COST_REPORT_CALLS=$((COST_REPORT_CALLS+1)); }

false
on_failure
set +e  # see the comment on section D's on_failure() call for why this is required

assert_eq "run_post_mortem IS called when \$TMP_OUT is stale" "1" "$POST_MORTEM_CALLS"
assert_eq "set_board_status IS called when \$TMP_OUT is stale" "1" "$BOARD_STATUS_CALLS"
assert_true "no session-window pause comment produced for a stale \$TMP_OUT" \
  "[ ! -f '${COMMENT_LOG_DIR}/dfsessionwindowpause.md' ]"

unset TMP_OUT
rm -rf "$SCHEDULER_STATE_DIR" "$ARTIFACTS_DIR" "$COMMENT_LOG_DIR"

echo ""
echo "--- F: on_failure() guard — kill-switch off falls through even on a matched signal ---"
SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-sw-statedir-f-XXXXXX)
export SCHEDULER_STATE_DIR
ARTIFACTS_DIR=$(mktemp -d /tmp/ep-sw-artifacts-f-XXXXXX)
export ARTIFACTS_DIR
ISSUE_NUM=292
INTENT=fix
RUN_ID=test-run-f1
RUN_STARTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
SESSION_WINDOW_BACKOFF_ENABLED=false

POST_MORTEM_CALLS=0
run_post_mortem() { POST_MORTEM_CALLS=$((POST_MORTEM_CALLS+1)); }
BOARD_STATUS_CALLS=0
set_board_status() { BOARD_STATUS_CALLS=$((BOARD_STATUS_CALLS+1)); return 0; }
COMMENT_LOG_DIR=$(mktemp -d /tmp/ep-sw-comments-f-XXXXXX)
post_or_update_comment() {
  local marker="$1" body="$2"
  local safe
  safe=$(echo "$marker" | tr -cd 'a-zA-Z0-9')
  echo "$body" > "${COMMENT_LOG_DIR}/${safe}.md"
}
COST_REPORT_CALLS=0
post_cost_report() { COST_REPORT_CALLS=$((COST_REPORT_CALLS+1)); }

RESET_ISO_F=$(date -u -d "@$(( $(date -u +%s) + 600 ))" +%Y-%m-%dT%H:%M:%SZ)
TMP_OUT=$(mktemp /tmp/ep-sw-out-f-XXXXXX)
printf 'some claude output\n{"event":"claude.rate_limit_event","resetsAt":"%s"}\n' \
  "$RESET_ISO_F" > "$TMP_OUT"

false
on_failure
set +e  # see the comment on section D's on_failure() call for why this is required

assert_eq "run_post_mortem IS called when kill-switch is off" "1" "$POST_MORTEM_CALLS"
assert_true "df-factory-failure comment produced when kill-switch is off" \
  "[ -f '${COMMENT_LOG_DIR}/dffactoryfailure.md' ]"
assert_true "no session-window pause comment produced when kill-switch is off" \
  "[ ! -f '${COMMENT_LOG_DIR}/dfsessionwindowpause.md' ]"
SESSION_WINDOW_BACKOFF_ENABLED=true

rm -f "$TMP_OUT"
rm -rf "$SCHEDULER_STATE_DIR" "$ARTIFACTS_DIR" "$COMMENT_LOG_DIR"

echo ""
echo "--- G: on_failure() — set_board_status failure renders the 'attempted but failed' text ---"
SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-sw-statedir-g-XXXXXX)
export SCHEDULER_STATE_DIR
ARTIFACTS_DIR=$(mktemp -d /tmp/ep-sw-artifacts-g-XXXXXX)
export ARTIFACTS_DIR
ISSUE_NUM=292
INTENT=fix
RUN_ID=test-run-g1
RUN_STARTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
unset TMP_OUT

run_post_mortem() { :; }
set_board_status() { return 1; }
COMMENT_LOG_DIR=$(mktemp -d /tmp/ep-sw-comments-g-XXXXXX)
post_or_update_comment() {
  local marker="$1" body="$2"
  local safe
  safe=$(echo "$marker" | tr -cd 'a-zA-Z0-9')
  echo "$body" > "${COMMENT_LOG_DIR}/${safe}.md"
}
post_cost_report() { :; }

false
on_failure
set +e  # see the comment on section D's on_failure() call (Task 1) for why this is required

assert_true "failure comment says the board update was attempted but failed" \
  "grep -q 'Attempted to move the issue to \*\*Blocked\*\*, but the board update failed' '${COMMENT_LOG_DIR}/dffactoryfailure.md'"
assert_true "failure comment does NOT falsely claim the move succeeded" \
  "! grep -q '^Issue has been moved to \*\*Blocked\*\*\.\$' '${COMMENT_LOG_DIR}/dffactoryfailure.md'"

rm -rf "$SCHEDULER_STATE_DIR" "$ARTIFACTS_DIR" "$COMMENT_LOG_DIR"

echo ""
echo "--- H: on_failure() — genuine failure with a successful board move posts both markers with the true claim ---"
SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-sw-statedir-h-XXXXXX)
export SCHEDULER_STATE_DIR
ARTIFACTS_DIR=$(mktemp -d /tmp/ep-sw-artifacts-h-XXXXXX)
export ARTIFACTS_DIR
ISSUE_NUM=292
INTENT=fix
RUN_ID=test-run-h1
RUN_STARTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
unset TMP_OUT

run_post_mortem() {
  post_or_update_comment "$DF_POST_MORTEM_MARKER" "${DF_POST_MORTEM_MARKER}
stub post-mortem"
}
set_board_status() { return 0; }
COMMENT_LOG_DIR=$(mktemp -d /tmp/ep-sw-comments-h-XXXXXX)
post_or_update_comment() {
  local marker="$1" body="$2"
  local safe
  safe=$(echo "$marker" | tr -cd 'a-zA-Z0-9')
  echo "$body" > "${COMMENT_LOG_DIR}/${safe}.md"
}
post_cost_report() { :; }

false
on_failure
set +e  # see the comment on section D's on_failure() call (Task 1) for why this is required

assert_true "df-post-mortem comment produced on a genuine failure" \
  "[ -f '${COMMENT_LOG_DIR}/dfpostmortem.md' ]"
assert_true "df-factory-failure comment produced on a genuine failure" \
  "[ -f '${COMMENT_LOG_DIR}/dffactoryfailure.md' ]"
assert_true "failure comment claims the (true) successful board move" \
  "grep -q 'Issue has been moved to \*\*Blocked\*\*\.' '${COMMENT_LOG_DIR}/dffactoryfailure.md'"

rm -rf "$SCHEDULER_STATE_DIR" "$ARTIFACTS_DIR" "$COMMENT_LOG_DIR"

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
