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

# #362: entrypoint.sh's current-run.json write happens unconditionally at source
# time (entrypoint.sh:117-121), before the ENTRYPOINT_SOURCE_ONLY guard returns --
# export a scratch CURRENT_RUN_DIR before sourcing so this test can never clobber
# the real /var/lib/dark-factory/current-run.json (mirrors
# tests/test_entrypoint_current_run.sh:32-33).
CURRENT_RUN_DIR=$(mktemp -d /tmp/ep-sw-rundir-XXXXXX)
export CURRENT_RUN_DIR

# #362 isolation snapshot: capture the real ledger's state before sourcing so the
# end-of-file assertions can prove this test never wrote to it, without depending
# on absolute counts (the shared production path already carries unrelated rows).
BEFORE_TESTRUN_COUNT=0
[ -f /var/lib/dark-factory/runs.jsonl ] && BEFORE_TESTRUN_COUNT=$(grep -c '"run_id": "test-run-' /var/lib/dark-factory/runs.jsonl 2>/dev/null)

ENTRYPOINT_SOURCE_ONLY=1 source "$SCRIPT_DIR/../entrypoint.sh"
SOURCE_TIME_RUN_ID="$RUN_ID"

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
export SCHEDULER_STATE_DIR
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
assert_true "session-window-pause-signature-write drop file written for issue+phase" \
  "[ -f '${SCHEDULER_STATE_DIR}/error-signatures/35.implement.sig' ]"
SIG_JSON=$(cat "${SCHEDULER_STATE_DIR}/error-signatures/35.implement.sig" 2>/dev/null || echo '{}')
assert_eq "drop file carries the pause signature" \
  "environmental:session_window_pause" \
  "$(echo "$SIG_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("signature",""))')"

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

echo ""
echo "--- A7: session-window-pause-signature-write skipped when ISSUE_NUM is unset ---"
A7_STATE_DIR=$(mktemp -d /tmp/ep-sw-statedir-a7-XXXXXX)
SAVED_STATE_DIR="$SCHEDULER_STATE_DIR"
SAVED_ISSUE_NUM="$ISSUE_NUM"
SCHEDULER_STATE_DIR="$A7_STATE_DIR"
unset ISSUE_NUM
_handle_session_window_pause "$TMP_OUT"
RC_A7=$?
assert_eq "matched → still returns 0 with ISSUE_NUM unset" "0" "$RC_A7"
assert_true "no drop file written when ISSUE_NUM is unset" \
  "[ ! -d '${A7_STATE_DIR}/error-signatures' ]"
ISSUE_NUM="$SAVED_ISSUE_NUM"
SCHEDULER_STATE_DIR="$SAVED_STATE_DIR"
rm -rf "$A7_STATE_DIR"

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
assert_true "session-window pause signature written for the paused run" \
  "[ -f '${SCHEDULER_STATE_DIR}/error-signatures/292.implement.sig' ]"
assert_eq "pause signature drop file carries the pause classification, not a classify() output" \
  "environmental:session_window_pause" \
  "$(python3 -c "import json; print(json.load(open('${SCHEDULER_STATE_DIR}/error-signatures/292.implement.sig'))['signature'])")"
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

# --- #586 end-to-end harness --------------------------------------------------------------
# Runs entrypoint.sh itself as a child process, not sourced: the main loop sits after the
# ENTRYPOINT_SOURCE_ONLY return. Every external command is stubbed and every hardcoded
# /opt/dark-factory path is redirected to this checkout, so it runs on a bare CI runner and
# reaches no network. $FACTORY_PROVIDERS_CLI is a stub answering preflight and tracker
# verbs; `model-endpoints hosting` prints $E2E_HOSTING_OUT when set, else runs this
# checkout's real resolver.
_e2e_run() {  # _e2e_run <dir> <archon-output-file>; sets E2E_RC
  local dir="$1"
  mkdir -p "$dir/home" "$dir/state" "$dir/run" "$dir/tmp"
  cat > "$dir/providers_cli.py" <<'PY'
import os, runpy, sys
with open(os.path.join(os.environ["E2E_DIR"], "providers.log"), "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
args = sys.argv[1:]
if args[:2] == ["model-endpoints", "hosting"]:
    if "E2E_HOSTING_OUT" in os.environ:
        print(os.environ["E2E_HOSTING_OUT"])
    else:
        sys.argv[0] = os.environ["E2E_REAL_PROVIDERS_CLI"]
        runpy.run_path(sys.argv[0], run_name="__main__")
elif args[:1] == ["tracker"]:
    print("{}")
PY
  (
    export E2E_DIR="$dir" E2E_REPO="$REPO_ROOT" E2E_ARCHON_OUT="$2"
    export E2E_REAL_PROVIDERS_CLI="$REPO_ROOT/scripts/factory_core/providers/cli.py"
    export IDENTITY_SH="$REPO_ROOT/scripts/identity.sh" FACTORY_PROVIDERS_CLI="$dir/providers_cli.py"
    export FACTORY_SCRIPTS_DIR="$REPO_ROOT/scripts" FACTORY_CONFIG_PATH="$REPO_ROOT/config/config.yaml"
    export FACTORY_CLONE_DIR="$dir/clone" HOME="$dir/home" TMPDIR="$dir/tmp"
    export SCHEDULER_STATE_DIR="$dir/state" CURRENT_RUN_DIR="$dir/run"
    # SEQ_URL: a refused local port, so run_record.py's best-effort Seq post fails at once
    # instead of waiting out its 5s timeout on every record.
    export GH_TOKEN=stub-token CLAUDE_CODE_OAUTH_TOKEN=stub-token SEQ_URL=http://127.0.0.1:9
    unset ARTIFACTS_DIR TMP_OUT DF_ENDPOINT_HOSTING FACTORY_BLAST_HOLD_FILE RUN_ID ISSUE_NUM INTENT
    unset SESSION_WINDOW_BACKOFF_ENABLED
    git() {
      if [ "${1:-}" = clone ]; then
        mkdir -p "$3/dark-factory" "$3/.archon/workflows" "$3/.archon/commands"
        ln -s "$E2E_REPO/scripts" "$3/dark-factory/scripts"
      fi
      return 0
    }
    gh() { echo stub; return 0; }
    docker() { return 0; }
    claude() { echo stub; return 0; }
    pre-commit() { return 0; }
    archon() {
      if [ "${1:-} ${2:-}" = "workflow run" ]; then cat "$E2E_ARCHON_OUT"; return 1; fi
      echo "{}"; return 0
    }
    sleep() { echo slept >> "$E2E_DIR/sleep.log"; exit 98; }
    _e2e_path() {
      case "$1" in
        /opt/dark-factory/scripts/factory_core/providers/cli.py) echo "$FACTORY_PROVIDERS_CLI" ;;
        /opt/dark-factory/*) echo "$E2E_REPO${1#/opt/dark-factory}" ;;
        *) echo "$1" ;;
      esac
    }
    source() { local f; f=$(_e2e_path "$1"); shift; builtin source "$f" "$@"; }
    cp() { local a=() x; for x in "$@"; do a+=("$(_e2e_path "$x")"); done; command cp "${a[@]}"; }
    python3() {
      local a=() x
      for x in "$@"; do a+=("$(_e2e_path "$x")"); done
      case " ${a[*]} " in
        *" session-window-check "*|*" rate-limit-match "*|*" error-signature-write "*)
          printf '[%s]' "${a[@]}" >> "$E2E_DIR/argv.log"; echo >> "$E2E_DIR/argv.log" ;;
      esac
      PYTHONPATH="$(_e2e_path "${PYTHONPATH:-}")" command python3 "${a[@]}"
    }
    export -f git gh docker claude pre-commit archon sleep _e2e_path source cp python3
    timeout 120 bash "$REPO_ROOT/entrypoint.sh" "Plan issue #586"
  ) >"$dir/stdout.log" 2>&1
  E2E_RC=$?
}
_e2e_sig() {
  python3 -c "import json; print(json.load(open('$1/state/error-signatures/586.plan.sig'))['signature'])" 2>/dev/null
}

echo ""
echo "--- M: #586 end to end, verified-local, exhaustion text — counted failure, no pause ---"
E2E_DIR_M=$(mktemp -d /tmp/ep-sw-e2e-m-XXXXXX)
printf "You've hit your session limit · resets 11:10pm (UTC)\n" > "$E2E_DIR_M/archon.out"
E2E_HOSTING_OUT=hosting=local _e2e_run "$E2E_DIR_M" "$E2E_DIR_M/archon.out"
assert_eq "exits non-zero with the workflow's exit code (not a timeout, not the sleep)" "1" "$E2E_RC"
assert_true "never reached the legacy sleep" "[ ! -f '$E2E_DIR_M/sleep.log' ]"
assert_true "no sentinel written" "[ ! -f '$E2E_DIR_M/state/session-window-paused' ]"
assert_eq "signature the scheduler consumes is the counted local class" \
  "substantive:endpoint_unreachable:1" "$(_e2e_sig "$E2E_DIR_M")"
assert_eq "hosting resolved exactly once, through \$FACTORY_PROVIDERS_CLI" "1" \
  "$(grep -c '^model-endpoints hosting$' "$E2E_DIR_M/providers.log")"
assert_true "hosting resolved right after the provider check, before anything else" \
  "[ \"\$(sed -n 2p '$E2E_DIR_M/providers.log')\" = 'model-endpoints hosting' ]"
assert_true "no pause comment posted" \
  "! grep -q 'df-session-window-pause' '$E2E_DIR_M/providers.log'"
rm -rf "$E2E_DIR_M"

echo ""
echo "--- J: #586 verified-local hosting — no sentinel, no pause signature, returns 1 ---"
SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-sw-statedir-j-XXXXXX)
export SCHEDULER_STATE_DIR
ISSUE_NUM=586
INTENT=plan
RUN_ID=test-run-j1
SESSION_WINDOW_BACKOFF_ENABLED=true
TMP_OUT=$(mktemp /tmp/ep-sw-out-j-XXXXXX)
printf "You've hit your session limit · resets 11:10pm (UTC)\n" > "$TMP_OUT"
DF_ENDPOINT_HOSTING=local
_handle_session_window_pause "$TMP_OUT"
RC_J=$?
assert_eq "local exhaustion text → returns 1 (falls through to the failure path)" "1" "$RC_J"
assert_true "local: no sentinel written" "[ ! -f '${SCHEDULER_STATE_DIR}/session-window-paused' ]"
assert_true "local: no session-window pause signature written" \
  "[ ! -f '${SCHEDULER_STATE_DIR}/error-signatures/586.plan.sig' ]"
assert_true "local: no paused run-record row" \
  "! grep -q '\"stage\": \"paused\"' '${SCHEDULER_STATE_DIR}/runs.jsonl' 2>/dev/null"
DF_ENDPOINT_HOSTING=cloud
_handle_session_window_pause "$TMP_OUT"
RC_J2=$?
assert_eq "cloud: same text still pauses (returns 0)" "0" "$RC_J2"
assert_true "cloud: sentinel written" "[ -f '${SCHEDULER_STATE_DIR}/session-window-paused' ]"
rm -f "$TMP_OUT"
rm -rf "$SCHEDULER_STATE_DIR"

# K/K2 share one body: on_failure() (the ERR-trap call site) on a verified-local run.
_k_on_failure_local() {  # _k_on_failure_local <kill-switch> <label>
  SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-sw-statedir-k-XXXXXX)
  export SCHEDULER_STATE_DIR
  ARTIFACTS_DIR=$(mktemp -d /tmp/ep-sw-artifacts-k-XXXXXX)
  export ARTIFACTS_DIR
  ISSUE_NUM=586
  INTENT=fix
  RUN_ID=test-run-k-$2
  RUN_STARTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  SESSION_WINDOW_BACKOFF_ENABLED=$1
  DF_ENDPOINT_HOSTING=local
  POST_MORTEM_CALLS=0
  run_post_mortem() { POST_MORTEM_CALLS=$((POST_MORTEM_CALLS+1)); }
  BOARD_STATUS_CALLS=0
  set_board_status() { BOARD_STATUS_CALLS=$((BOARD_STATUS_CALLS+1)); return 0; }
  COMMENT_LOG_DIR=$(mktemp -d /tmp/ep-sw-comments-k-XXXXXX)
  post_or_update_comment() {
    local marker="$1" body="$2"
    local safe
    safe=$(echo "$marker" | tr -cd 'a-zA-Z0-9')
    echo "$body" > "${COMMENT_LOG_DIR}/${safe}.md"
  }
  post_cost_report() { :; }
  TMP_OUT=$(mktemp /tmp/ep-sw-out-k-XXXXXX)
  printf "You've hit your session limit · resets 11:10pm (UTC)\n" > "$TMP_OUT"

  false
  on_failure
  set +e  # see the comment on section D's on_failure() call for why this is required

  assert_eq "[$2] run_post_mortem IS called (counted failure, not a pause)" "1" "$POST_MORTEM_CALLS"
  assert_eq "[$2] set_board_status IS called" "1" "$BOARD_STATUS_CALLS"
  assert_true "[$2] df-factory-failure comment produced" \
    "[ -f '${COMMENT_LOG_DIR}/dffactoryfailure.md' ]"
  assert_true "[$2] no session-window pause comment" \
    "[ ! -f '${COMMENT_LOG_DIR}/dfsessionwindowpause.md' ]"
  assert_true "[$2] no sentinel written" "[ ! -f '${SCHEDULER_STATE_DIR}/session-window-paused' ]"
  assert_true "[$2] runs.jsonl records a failed stage" \
    "grep -q '\"stage\": \"failed\"' '${SCHEDULER_STATE_DIR}/runs.jsonl'"
  assert_eq "[$2] signature is the counted local endpoint class" \
    "substantive:endpoint_unreachable:1" \
    "$(python3 -c "import json; print(json.load(open('${SCHEDULER_STATE_DIR}/error-signatures/586.implement.sig'))['signature'])" 2>/dev/null)"
  rm -f "$TMP_OUT"
  rm -rf "$SCHEDULER_STATE_DIR" "$ARTIFACTS_DIR" "$COMMENT_LOG_DIR"
  DF_ENDPOINT_HOSTING=cloud
  SESSION_WINDOW_BACKOFF_ENABLED=true
}
echo ""
echo "--- K: #586 on_failure() on a verified-local run — counted failure, never the pause branch ---"
_k_on_failure_local true kill-switch-on
echo ""
echo "--- K2: #586 same with the kill-switch off ---"
_k_on_failure_local false kill-switch-off

# --- #586 main-loop harness ---------------------------------------------------------------
# Runs the real main loop -- its text extracted from entrypoint.sh, not re-typed -- in a
# subshell with the ERR trap disarmed. In production on_failure() ends every non-pause
# failure first (it re-enables errexit), so this is the only way to execute the loop's own
# rate-limit-match branch, which the #586 bash guard sits on.
_loop_run() {  # _loop_run <dir> <archon-output-file>; sets LOOP_RC
  local dir="$1" out="$2"
  mkdir -p "$dir"
  (
    trap - ERR
    archon() { cat "$out"; return 1; }
    sleep() { echo slept >> "$dir/sleep.log"; exit 98; }
    run_post_mortem() { echo post_mortem >> "$dir/calls.log"; }
    _handle_blast_hold() { return 1; }
    python3() {
      case " $* " in
        *" session-window-check "*|*" rate-limit-match "*|*" error-signature-write "*)
          printf '[%s]' "$@" >> "$dir/argv.log"; echo >> "$dir/argv.log" ;;
      esac
      command python3 "$@"
    }
    eval "$(awk '/^while true; do$/,/^done$/' "$REPO_ROOT/entrypoint.sh")"
    exit 97  # the loop broke out: never expected for a failed workflow
  ) >"$dir/stdout.log" 2>&1
  LOOP_RC=$?
}
_loop_case() {  # _loop_case <hosting> <kill-switch> <label>; leaves LOOP_DIR behind
  LOOP_DIR=$(mktemp -d /tmp/ep-sw-loop-XXXXXX)
  SCHEDULER_STATE_DIR="$LOOP_DIR/state"; mkdir -p "$SCHEDULER_STATE_DIR"
  export SCHEDULER_STATE_DIR
  ARTIFACTS_DIR="$LOOP_DIR/artifacts"; mkdir -p "$ARTIFACTS_DIR"
  export ARTIFACTS_DIR
  ISSUE_NUM=586
  INTENT=plan
  RUN_ID=test-run-loop-$3
  RUN_STARTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  DF_ENDPOINT_HOSTING=$1
  SESSION_WINDOW_BACKOFF_ENABLED=$2
  printf "You've hit your session limit · resets 11:10pm (UTC)\n" > "$LOOP_DIR/archon.out"
  _loop_run "$LOOP_DIR" "$LOOP_DIR/archon.out"
  DF_ENDPOINT_HOSTING=cloud
  SESSION_WINDOW_BACKOFF_ENABLED=true
}
_loop_sig() {
  python3 -c "import json; print(json.load(open('$LOOP_DIR/state/error-signatures/586.plan.sig'))['signature'])" 2>/dev/null
}

echo ""
echo "--- L: #586 main loop, verified-local, kill-switch off — exits counted, never sleeps ---"
_loop_case local false l1
assert_eq "exits with the workflow's own exit code" "1" "$LOOP_RC"
assert_true "never reached the legacy sleep" "[ ! -f '$LOOP_DIR/sleep.log' ]"
assert_true "rate-limit-match never invoked (bash guard short-circuits)" \
  "! grep -q 'rate-limit-match' '$LOOP_DIR/argv.log' 2>/dev/null"
assert_true "post-mortem ran" "grep -q post_mortem '$LOOP_DIR/calls.log'"
assert_eq "signature is the counted local endpoint class" "substantive:endpoint_unreachable:1" "$(_loop_sig)"
assert_true "no sentinel written" "[ ! -f '$LOOP_DIR/state/session-window-paused' ]"
rm -rf "$LOOP_DIR"

echo ""
echo "--- L2: #586 main loop, verified-local, kill-switch on — same counted exit ---"
_loop_case local true l2
assert_eq "exits with the workflow's own exit code" "1" "$LOOP_RC"
assert_true "never reached the legacy sleep" "[ ! -f '$LOOP_DIR/sleep.log' ]"
assert_true "session-window-check got --hosting-location local" \
  "grep -q '\[session-window-check\].*\[--hosting-location\]\[local\]' '$LOOP_DIR/argv.log'"
assert_true "rate-limit-match never invoked" "! grep -q 'rate-limit-match' '$LOOP_DIR/argv.log'"
assert_eq "signature is the counted local endpoint class" "substantive:endpoint_unreachable:1" "$(_loop_sig)"
assert_true "no sentinel written" "[ ! -f '$LOOP_DIR/state/session-window-paused' ]"
rm -rf "$LOOP_DIR"

echo ""
echo "--- L3: #586 parity — cloud with the kill-switch off keeps the legacy sleep loop ---"
_loop_case cloud false l3
assert_eq "reached the legacy sleep (stub exits 98)" "98" "$LOOP_RC"
assert_true "rate-limit-match invoked" "grep -q 'rate-limit-match' '$LOOP_DIR/argv.log'"
rm -rf "$LOOP_DIR"

echo ""
echo "--- M2: #586 end to end, verified-local, rejected structured event — same counted failure ---"
E2E_DIR_M2=$(mktemp -d /tmp/ep-sw-e2e-m2-XXXXXX)
printf '{"level":40,"rateLimitInfo":{"status":"rejected","resetsAt":%s,"rateLimitType":"five_hour"},"msg":"claude.rate_limit_event"}\n' \
  "$(( $(date -u +%s) + 600 ))" > "$E2E_DIR_M2/archon.out"
E2E_HOSTING_OUT=hosting=local _e2e_run "$E2E_DIR_M2" "$E2E_DIR_M2/archon.out"
assert_eq "exits non-zero" "1" "$E2E_RC"
assert_true "no sentinel written" "[ ! -f '$E2E_DIR_M2/state/session-window-paused' ]"
assert_eq "signature is the counted local class" \
  "substantive:endpoint_unreachable:1" "$(_e2e_sig "$E2E_DIR_M2")"
rm -rf "$E2E_DIR_M2"

echo ""
echo "--- M3: #586 parity, end to end — cloud hosting, same exhaustion text still pauses ---"
E2E_DIR_M3=$(mktemp -d /tmp/ep-sw-e2e-m3-XXXXXX)
printf "You've hit your session limit · resets 11:10pm (UTC)\n" > "$E2E_DIR_M3/archon.out"
E2E_HOSTING_OUT=hosting=cloud _e2e_run "$E2E_DIR_M3" "$E2E_DIR_M3/archon.out"
assert_eq "exits 0 (paused)" "0" "$E2E_RC"
assert_true "sentinel written" "[ -f '$E2E_DIR_M3/state/session-window-paused' ]"
assert_eq "pause signature written" "environmental:session_window_pause" "$(_e2e_sig "$E2E_DIR_M3")"
rm -rf "$E2E_DIR_M3"

echo ""
echo "--- M4: #586 fail closed, end to end — resolver output other than exactly hosting=local is cloud ---"
for out in "hosting=local-ish" "HOSTING=local" ""; do
  E2E_DIR_M4=$(mktemp -d /tmp/ep-sw-e2e-m4-XXXXXX)
  printf "You've hit your session limit · resets 11:10pm (UTC)\n" > "$E2E_DIR_M4/archon.out"
  E2E_HOSTING_OUT="$out" _e2e_run "$E2E_DIR_M4" "$E2E_DIR_M4/archon.out"
  assert_true "resolver printed '${out}' → paused as cloud" \
    "[ '$E2E_RC' = 0 ] && [ -f '$E2E_DIR_M4/state/session-window-paused' ]"
  rm -rf "$E2E_DIR_M4"
done

# --- #586 argv parity -----------------------------------------------------------------------
# Golden argv recorded from main before #586, one [token] per argument; temp paths and the
# elapsed seconds are normalized. No --hosting-location token may appear on the cloud path.
_argv_norm() {  # _argv_norm <dir> <argv.log>
  sed -e "s#$1/tmp/tmp\.[A-Za-z0-9]*#<TMP_OUT>#g" -e "s#$1#<DIR>#g" \
      -e 's#\[--elapsed-seconds\]\[[0-9]*\]#[--elapsed-seconds][<N>]#' "$2"
}
GOLDEN_E2E_FAIL='[<DIR>/clone/dark-factory/scripts/factory_core/cli.py][session-window-check][--tmp-out][<TMP_OUT>][--state-dir][<DIR>/state][--buffer-minutes][5][--fallback-minutes][30]
[<DIR>/clone/dark-factory/scripts/factory_core/cli.py][error-signature-write][--issue][586][--phase][plan][--exit-code][1][--text-file][<TMP_OUT>][--elapsed-seconds][<N>][--commits-since-start][0][--delivery-failure-max-seconds][30][--state-dir][<DIR>/state]'
for variant in unset set-resolves-cloud; do
  echo ""
  echo "--- N: #586 argv parity end to end (FACTORY_MODEL_ENDPOINT ${variant}) ---"
  E2E_DIR_N=$(mktemp -d /tmp/ep-sw-e2e-n-XXXXXX)
  echo "HTTP 429 Too Many Requests" > "$E2E_DIR_N/archon.out"
  if [ "$variant" = unset ]; then
    (unset FACTORY_MODEL_ENDPOINT FACTORY_MODEL_REGISTRY_SHA256; _e2e_run "$E2E_DIR_N" "$E2E_DIR_N/archon.out"; exit "$E2E_RC")
  else
    # A real fixture local entry with no digest: the real resolver fails closed to cloud.
    (export FACTORY_MODEL_ENDPOINT=local-same-host FACTORY_MODEL_REGISTRY_PATH="$REPO_ROOT/tests/fixtures/model_registry"
     unset FACTORY_MODEL_REGISTRY_SHA256; _e2e_run "$E2E_DIR_N" "$E2E_DIR_N/archon.out"; exit "$E2E_RC")
  fi
  assert_eq "failed run exits 1" "1" "$?"
  assert_eq "session-window-check + error-signature-write argv byte-identical to main" \
    "$GOLDEN_E2E_FAIL" "$(_argv_norm "$E2E_DIR_N" "$E2E_DIR_N/argv.log")"
  assert_true "no --hosting-location token anywhere" "! grep -q 'hosting-location' '$E2E_DIR_N/argv.log'"
  rm -rf "$E2E_DIR_N"
done

echo ""
echo "--- N2: #586 argv parity in the main loop (rate-limit-match runs only there) ---"
GOLDEN_LOOP_FAIL='[<DIR>/dark-factory/scripts/factory_core/cli.py][session-window-check][--tmp-out][<TMP_OUT>][--state-dir][<DIR>/state][--buffer-minutes][5][--fallback-minutes][30]
[<DIR>/dark-factory/scripts/factory_core/cli.py][rate-limit-match][--tmp-out][<TMP_OUT>]
[<DIR>/dark-factory/scripts/factory_core/cli.py][error-signature-write][--issue][586][--phase][plan][--exit-code][1][--text-file][<TMP_OUT>][--elapsed-seconds][<N>][--commits-since-start][0][--delivery-failure-max-seconds][30][--state-dir][<DIR>/state]'
LOOP_DIR=$(mktemp -d /tmp/ep-sw-loop-n2-XXXXXX)
SCHEDULER_STATE_DIR="$LOOP_DIR/state"; mkdir -p "$SCHEDULER_STATE_DIR"; export SCHEDULER_STATE_DIR
ARTIFACTS_DIR="$LOOP_DIR/artifacts"; mkdir -p "$ARTIFACTS_DIR"; export ARTIFACTS_DIR
ISSUE_NUM=586; INTENT=plan; RUN_ID=test-run-loop-n2
RUN_STARTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
DF_ENDPOINT_HOSTING=cloud; SESSION_WINDOW_BACKOFF_ENABLED=true
echo "HTTP 429 Too Many Requests" > "$LOOP_DIR/archon.out"
SAVED_TMPDIR="${TMPDIR-}"; export TMPDIR="$LOOP_DIR/tmp"; mkdir -p "$TMPDIR"
_loop_run "$LOOP_DIR" "$LOOP_DIR/archon.out"
if [ -n "$SAVED_TMPDIR" ]; then TMPDIR="$SAVED_TMPDIR"; else unset TMPDIR; fi
assert_eq "loop exits 1" "1" "$LOOP_RC"
assert_eq "all three call sites' argv byte-identical to main" \
  "$GOLDEN_LOOP_FAIL" \
  "$(_argv_norm "$LOOP_DIR" "$LOOP_DIR/argv.log" | sed "s#\[$(dirname "$REPO_ROOT")/#[<DIR>/#")"
rm -rf "$LOOP_DIR"

echo ""
echo "--- I: isolation -- the real /var/lib/dark-factory ledger was never touched (#362) ---"
if [ -f /var/lib/dark-factory/runs.jsonl ]; then
  AFTER_TESTRUN_COUNT=$(grep -c '"run_id": "test-run-' /var/lib/dark-factory/runs.jsonl 2>/dev/null)
  assert_eq "runs.jsonl test-run-* row count unchanged" "$BEFORE_TESTRUN_COUNT" "$AFTER_TESTRUN_COUNT"
fi
if [ -f /var/lib/dark-factory/current-run.json ]; then
  AFTER_CURRENT_RUN_ID=$(python3 -c "import json; print(json.load(open('/var/lib/dark-factory/current-run.json')).get('run_id',''))" 2>/dev/null || echo "")
  assert_true "current-run.json was not overwritten with this run's source-time run_id" \
    "[ '$AFTER_CURRENT_RUN_ID' != '$SOURCE_TIME_RUN_ID' ]"
fi
rm -rf "$CURRENT_RUN_DIR"

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
