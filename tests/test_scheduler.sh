#!/usr/bin/env bash
# Unit tests for scheduler.sh helpers.
# Run: bash tests/test_scheduler.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCHED="$SCRIPT_DIR/../scheduler.sh"
# Point at this checkout's own cli.py, not the image-baked /opt/dark-factory copy (which
# lacks any subcommand added since the last image build) — breaker-check-signature (#33)
# only exists here. Existing sections (breaker-trip et al.) happen to also exist in the
# baked copy, which is why this override wasn't needed until now (see
# test_scheduler_pagination.sh for the same pattern already in use).
export FACTORY_CORE_CLI="$SCRIPT_DIR/../scripts/factory_core/cli.py"

# ---- Stubs ----
STUB_LOG=$(mktemp /tmp/sched-test-stubs-XXXXXX.log)
gh()               { echo "gh $*"               >> "$STUB_LOG"; return 0; }
docker()           { echo "docker $*"           >> "$STUB_LOG"; return 0; }
git()              { echo "git $*"              >> "$STUB_LOG"; return 0; }
set_board_status() { echo "set_board_status $*" >> "$STUB_LOG"; return 0; }
# Default python3 stub (#249): calls into the new providers CLI are logged and never
# let through for real — that CLI shells out to a real `gh` binary directly (bash
# function stubs are invisible to a Python subprocess), unlike the old
# factory_core/cli.py breaker-*/board-move commands exercised elsewhere in this file,
# which are network-free and safe to delegate to the real interpreter. Sections that
# need specific providers-cli stdout set PROVIDERS_CLI_OUTPUT first; reset_python3_stub
# restores the silent default.
_REAL_PY3="$(command -v python3)"
export _REAL_PY3
PROVIDERS_CLI_OUTPUT=""
python3() {
  echo "python3 $*" >> "$STUB_LOG"
  case "$*" in
    *providers/cli.py*) [ -n "$PROVIDERS_CLI_OUTPUT" ] && printf '%s\n' "$PROVIDERS_CLI_OUTPUT"; return 0 ;;
    *) "$_REAL_PY3" "$@" ;;
  esac
}
reset_python3_stub() { PROVIDERS_CLI_OUTPUT=""; }
export -f gh docker git set_board_status python3

# ---- Subprocess-visible stubs ----
# The exported bash functions above are invisible to non-bash children: the python3 stub
# forwards every non-providers CLI call (FACTORY_CORE_CLI board-move / rescue-blocked /
# epic-autopilot / marker ...) to the real interpreter, which imports board.py and spawns
# `gh project item-list --limit 200` via PATH — a real GraphQL query costing ~101 points.
# Nine such calls per suite run (~900 points) exhausted the shared 5,000/hr GitHub GraphQL
# budget whenever an implement agent iterated on this suite inside a run container
# (2026-08-28: every push-and-pr for #334/#341/#342 failed on the drained pool).
# A PATH shim re-enters bash so the exported stub function handles the call instead.
# Child calls are logged to SHIM_LOG (not STUB_LOG) so the in-process call counts the
# sections below assert on are unchanged; the function runs with STUB_LOG=/dev/null.
STUB_BIN=$(mktemp -d /tmp/sched-test-bin-XXXXXX)
SHIM_LOG="$STUB_BIN/calls.log"; : > "$SHIM_LOG"; export SHIM_LOG
for _stub_cmd in gh docker git; do
  printf '#!/usr/bin/env bash
echo "%s $*" >> "$SHIM_LOG"
STUB_LOG=/dev/null %s "$@"
' "$_stub_cmd" "$_stub_cmd" > "$STUB_BIN/$_stub_cmd"
  chmod +x "$STUB_BIN/$_stub_cmd"
done
unset _stub_cmd
export PATH="$STUB_BIN:$PATH"

# ---- Source scheduler helpers only ----
# Point the state dir at a temp dir BEFORE sourcing: scheduler.sh derives STATE_FILE
# and RECHECK_STAMP_FILE from it (and mkdir-s it), so tests must not touch the real
# /var/lib/dark-factory.
SCHEDULER_STATE_DIR=$(mktemp -d /tmp/sched-test-statedir-XXXXXX)
export SCHEDULER_STATE_DIR
STATE_FILE=$(mktemp /tmp/sched-test-state-XXXXXX.json)
echo '{}' > "$STATE_FILE"
export STATE_FILE
# Set all config-driven vars explicitly: read_config runs after SCHEDULER_SOURCE_ONLY guard
# so these values won't be populated by config.yaml during test sourcing.
export POLL_INTERVAL=60
export MAX_RETRIES=3
export RATE_LIMIT_FLOOR=200
export FACTORY_WIP_LIMIT=1
export MAIN_RED_RECHECK_ENABLED=true
export MAIN_RED_RECHECK_MINUTES=20
export REFINE_WIP_LIMIT=2
export DIRECT_TO_PR_LABEL=direct-to-pr
export SPEC_GRACE_MINUTES=30
export PLAN_GRACE_MINUTES=30
export CONFLICT_RESOLUTION_ENABLED=true
export DISPATCH_CEILING_ENABLED=true
export ABOVE_CEILING_LABEL=above-ceiling
export ABOVE_CEILING_KEYWORDS="migration|migrate|performance|perf|architectur|refactor"
SCHEDULER_SOURCE_ONLY=1 source "$SCHED"
# Preserve the sourcing-time log: every section below truncates $STUB_LOG for its own
# assertions, so the one-time preflight call made during sourcing (before the
# SCHEDULER_SOURCE_ONLY guard) would otherwise be gone by the time section P checks it.
SOURCE_TIME_LOG=$(cat "$STUB_LOG")

# Re-stub set_board_status — scheduler.sh defines its own, overriding the export above
set_board_status() { echo "set_board_status $*" >> "$STUB_LOG"; return 0; }

# ---- Runner ----
PASSED=0; FAILED=0
assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else
    echo "  FAIL: $desc — expected='$expected' got='$actual'" >&2; FAILED=$((FAILED+1))
  fi
}

# ==========================================
# A: Retry helpers (should pass immediately)
# ==========================================
echo "--- A: Retry helpers ---"
echo '{}' > "$STATE_FILE"

assert_eq "unknown key returns 0"       "0" "$(get_retry_count "42:refine")"
increment_retry "42:refine"
assert_eq "after 1 increment"           "1" "$(get_retry_count "42:refine")"
increment_retry "42:refine"
assert_eq "after 2 increments"          "2" "$(get_retry_count "42:refine")"
reset_retry "42:refine"
assert_eq "after reset"                 "0" "$(get_retry_count "42:refine")"
increment_retry "42"
assert_eq "bare key independent"        "1" "$(get_retry_count "42")"
assert_eq ":refine unaffected by bare"  "0" "$(get_retry_count "42:refine")"

# ==========================================
# B: trip_to_blocked (thin adapter → breaker-trip CLI)
# ==========================================
# scheduler.sh's trip_to_blocked is a thin adapter that delegates to
# `factory_core/cli.py breaker-trip`. The board-move + needs-discussion/
# factory-regression labels + comment side effects live in factory_core/breaker.py
# and are covered by test_factory_core_breaker.py (they run in a python subprocess
# the bash stubs can't observe). Here we verify the adapter delegates with the right
# issue/phase and that the retry counter is reset.
echo ""
echo "--- B: trip_to_blocked ---"
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"

# python3 (the global default stub, top of file) tees into STUB_LOG while still
# running the real interpreter for non-providers-cli calls — breaker-trip's real
# logic resets the counter on the temp state file.
increment_retry "99:plan"
increment_retry "99:plan"
increment_retry "99:plan"

trip_to_blocked "99" "plan" "test reason"

assert_eq "delegates to breaker-trip CLI (issue+phase)" \
  "1" "$(grep -c 'breaker-trip --issue 99 --phase plan' "$STUB_LOG" || echo 0)"
assert_eq ":plan counter reset after trip" \
  "0" "$(get_retry_count "99:plan")"

echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
increment_retry "88:refine"
trip_to_blocked "88" "refine" "test"
assert_eq ":refine counter reset" "0" "$(get_retry_count "88:refine")"

echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
increment_retry "77"
trip_to_blocked "77" "implement" "test"
assert_eq "bare implement counter reset" "0" "$(get_retry_count "77")"

# ==========================================
# B2: check_failure_signature — early trip on 2nd consecutive substantive match
# ==========================================
echo ""
echo "--- B2: check_failure_signature early trip ---"
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"

_drop_sig() {
  local issue="$1" phase="$2" sig="$3"
  mkdir -p "${SCHEDULER_STATE_DIR}/error-signatures"
  printf '{"signature":"%s","phase":"%s","exit_code":1}' "$sig" "$phase" \
    > "${SCHEDULER_STATE_DIR}/error-signatures/${issue}.${phase}.sig"
}

_drop_sig 50 implement "substantive:test_failure:1"
RESULT1=$(check_failure_signature "50" "implement")
assert_eq "1st substantive match: not stuck" "1" "$(echo "$RESULT1" | grep -c 'stuck=false')"

_drop_sig 50 implement "substantive:test_failure:1"
RESULT2=$(check_failure_signature "50" "implement")
assert_eq "2nd consecutive substantive match: stuck" "1" "$(echo "$RESULT2" | grep -c 'stuck=true')"
assert_eq "stuck result carries the signature" "1" \
  "$(echo "$RESULT2" | grep -c 'sig=substantive:test_failure:1')"

echo '{}' > "$STATE_FILE"
_drop_sig 51 implement "environmental:delivery_failure"
check_failure_signature "51" "implement" > /dev/null
_drop_sig 51 implement "environmental:delivery_failure"
RESULT3=$(check_failure_signature "51" "implement")
assert_eq "environmental repeat never trips (mirrors #279)" "1" \
  "$(echo "$RESULT3" | grep -c 'stuck=false')"

echo '{}' > "$STATE_FILE"; > "$STUB_LOG"

# K10: refine/plan/resolve call sites early-trip via trip_to_blocked, bypassing MAX_RETRIES
_drop_sig 52 resolve "substantive:build_failure:1"
check_failure_signature "52" "resolve" > /dev/null
_drop_sig 52 resolve "substantive:build_failure:1"

SIG_RESULT=$(check_failure_signature "52" "resolve")
if echo "$SIG_RESULT" | grep -q "stuck=true"; then
  SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
  trip_to_blocked "52" "resolve" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
fi
assert_eq "K10: early trip delegates to breaker-trip (resolve)" \
  "1" "$(grep -c 'breaker-trip --issue 52 --phase resolve' "$STUB_LOG" || echo 0)"
K10_EXPECTED_REASON="same failure signature 'substantive:build_failure:1'"
assert_eq "K10: reason string embeds the signature" \
  "1" "$(grep -c -F "$K10_EXPECTED_REASON" "$STUB_LOG" || echo 0)"

> "$STUB_LOG"

# ==========================================
# B3: environmental:session_window_pause never trips the early-stuck breaker
# ==========================================
echo ""
echo "--- B3: session_window_pause repeat never trips ---"
echo '{}' > "$STATE_FILE"

_drop_sig 53 plan "environmental:session_window_pause"
check_failure_signature "53" "plan" > /dev/null
_drop_sig 53 plan "environmental:session_window_pause"
RESULT_PAUSE=$(check_failure_signature "53" "plan")
assert_eq "environmental:session_window_pause repeat never trips" "1" \
  "$(echo "$RESULT_PAUSE" | grep -c 'stuck=false')"

echo '{}' > "$STATE_FILE"; > "$STUB_LOG"

# ==========================================
# B4: rollback_paused_retry (#341 session-window pause rollback)
# ==========================================
echo ""
echo "--- B4: rollback_paused_retry ---"
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"

# B4a: non-pause signature is a no-op
increment_retry "120:refine"
rollback_paused_retry 120 refine "substantive:test_failure:1" "120:refine" 3
assert_eq "B4a: non-pause signature does not decrement" "1" "$(get_retry_count "120:refine")"

# B4b: empty signature is a no-op
rollback_paused_retry 120 refine "" "120:refine" 3
assert_eq "B4b: empty signature does not decrement" "1" "$(get_retry_count "120:refine")"

# B4c: pause signature decrements by 1
rollback_paused_retry 120 refine "environmental:session_window_pause" "120:refine" 3
assert_eq "B4c: pause signature decrements the counter" "0" "$(get_retry_count "120:refine")"

# B4d: clamps at 0 — a pause observed when the counter is already 0 (e.g. after a
# reset_retry) does not go negative
rollback_paused_retry 120 refine "environmental:session_window_pause" "120:refine" 3
assert_eq "B4d: decrement clamps at 0" "0" "$(get_retry_count "120:refine")"

# B4e: delegates to breaker-set-retry (not increment_retry) for the write
> "$STUB_LOG"
increment_retry "121:refine"
increment_retry "121:refine"
rollback_paused_retry 121 refine "environmental:session_window_pause" "121:refine" 3
assert_eq "B4e: delegates to breaker-set-retry with the decremented value" \
  "1" "$(grep -c 'breaker-set-retry --key 121:refine --value 1' "$STUB_LOG" || echo 0)"

# B4f: logs the decrement action to stderr with issue/phase/action/count
> "$STUB_LOG"; echo '{}' > "$STATE_FILE"
increment_retry "122:resolve"
LOG_LINE=$(rollback_paused_retry 122 resolve "environmental:session_window_pause" "122:resolve" 3 2>&1 >/dev/null)
assert_eq "B4f: log line names issue/phase/action/count" "1" \
  "$(echo "$LOG_LINE" | grep -c 'session_window_gate issue=#122 phase=resolve action=retry_decrement count=0/3')"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

# ==========================================
# C: dispatch() exit-code capture (fails until Task 3)
# ==========================================
echo ""
echo "--- C: dispatch() exit-code capture ---"
> "$STUB_LOG"

_orig_docker() { echo "docker $*" >> "$STUB_LOG"; return 0; }
docker() {
  echo "docker $*" >> "$STUB_LOG"
  echo "$*" | grep -q "compose.*run" && return 42
  return 0
}
export -f docker

EXIT_CODE=0
dispatch "Fix issue #1" || EXIT_CODE=$?
assert_eq "dispatch returns non-zero exit code" "42" "$EXIT_CODE"
assert_eq "dispatch does not pass --no-build (invalid flag for 'compose run')" \
  "0" "$(grep -c -- '--no-build' "$STUB_LOG" || true)"

docker() { echo "docker $*" >> "$STUB_LOG"; return 0; }
export -f docker

# ==========================================
# C2: dispatch() adds --profile factory-model-proxy only when the flag is set (#208)
# ==========================================
echo ""
echo "--- C2: dispatch() model-proxy profile flag ---"
> "$STUB_LOG"

docker() { echo "docker $*" >> "$STUB_LOG"; return 0; }
export -f docker

unset FACTORY_MODEL_PROXY_ENABLED
dispatch "Fix issue #2"
assert_eq "profile flag absent when FACTORY_MODEL_PROXY_ENABLED unset" \
  "0" "$(grep -c -- '--profile factory-model-proxy' "$STUB_LOG" || true)"

> "$STUB_LOG"
export FACTORY_MODEL_PROXY_ENABLED=true
dispatch "Fix issue #2"
assert_eq "profile flag present when FACTORY_MODEL_PROXY_ENABLED=true" \
  "1" "$(grep -c -- '--profile factory-model-proxy' "$STUB_LOG" || true)"
unset FACTORY_MODEL_PROXY_ENABLED

# ==========================================
# D: Opt-in label gate (fails until Task 8)
# ==========================================
echo ""
echo "--- D: Opt-in label gate ---"

ITEM_WITH='{"content":{"number":1},"labels":["ready-for-agent","needs-triage"],"status":"Backlog"}'
ITEM_WITHOUT='{"content":{"number":2},"labels":["needs-triage"],"status":"Backlog"}'

has_opt_in_refine_label "$ITEM_WITH"    \
  && assert_eq "item WITH label passes gate"    "0" "0" \
  || assert_eq "item WITH label passes gate"    "0" "1"
has_opt_in_refine_label "$ITEM_WITHOUT" \
  && assert_eq "item WITHOUT label blocked"     "0" "1" \
  || assert_eq "item WITHOUT label blocked"     "0" "0"

# ==========================================
# E: has_direct_to_pr_label
# ==========================================
echo ""
echo "--- E: has_direct_to_pr_label ---"

ITEM_DTP='{"content":{"number":10},"labels":["enhancement","direct-to-pr"],"status":"Backlog"}'
ITEM_NO_DTP='{"content":{"number":11},"labels":["enhancement","ready-for-agent"],"status":"Backlog"}'

has_direct_to_pr_label "$ITEM_DTP" \
  && assert_eq "item WITH direct-to-pr returns true" "0" "0" \
  || assert_eq "item WITH direct-to-pr returns true" "0" "1"

has_direct_to_pr_label "$ITEM_NO_DTP" \
  && assert_eq "item WITHOUT direct-to-pr returns false" "0" "1" \
  || assert_eq "item WITHOUT direct-to-pr returns false" "0" "0"

# ==========================================
# F: elapsed_minutes_since_marker
# ==========================================
echo ""
echo "--- F: elapsed_minutes_since_marker ---"

# Compute a timestamp 35 minutes in the past
_MARKER_EPOCH=$(( $(date -u +%s) - 35*60 ))
_MARKER_TS=$(date -u -d "@${_MARKER_EPOCH}" +%Y-%m-%dT%H:%M:%SZ)

PROVIDERS_CLI_OUTPUT=$(printf '[{"body":"Refinement Pipeline — Plan Generated","createdAt":"%s"}]' "$_MARKER_TS")

_ELAPSED=$(elapsed_minutes_since_marker "55" "Refinement Pipeline")
[ -n "$_ELAPSED" ] && [ "$_ELAPSED" -ge 34 ] \
  && assert_eq "elapsed ≥ 34 for 35-min-old marker" "0" "0" \
  || assert_eq "elapsed ≥ 34 for 35-min-old marker" "0" "1"

# No matching comment → returns ""
PROVIDERS_CLI_OUTPUT=$(printf '[{"body":"some other comment","createdAt":"%s"}]' "$_MARKER_TS")
_ELAPSED2=$(elapsed_minutes_since_marker "55" "Refinement Pipeline")
assert_eq "no matching marker returns empty" "" "$_ELAPSED2"

reset_python3_stub

# ==========================================
# G: Spec auto-advance (direct-to-pr)
# ==========================================
echo ""
echo "--- G: Spec auto-advance ---"
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
# Initialize variables that the main loop sets but tests don't have
REFINE_RUNNING=0
DISPATCHED=""

_ITEM_DTP_SPR='{"content":{"number":20},"labels":["direct-to-pr","spec-pending-review"],"status":"Backlog"}'
_ITEM_NODTP_SPR='{"content":{"number":21},"labels":["spec-pending-review"],"status":"Backlog"}'

# G1: flag + human comment → re-refine path (remove-label + dispatch Refine)
has_new_comment_after_report() { echo "yes"; }
elapsed_minutes_since_marker() { echo "99"; }
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
export -f has_new_comment_after_report elapsed_minutes_since_marker dispatch

spec_advance_check 20 "$_ITEM_DTP_SPR"
assert_eq "G1: re-refine: remove-label called" \
  "1" "$(grep -c -- '--remove spec-pending-review' "$STUB_LOG" || echo 0)"
assert_eq "G1: re-refine: Refine dispatched" \
  "1" "$(grep -c 'dispatch Refine issue #20' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"
# G2: flag + no comment + elapsed ≥ grace → advance (remove-label + set_board_status REFINED)
has_new_comment_after_report() { echo "no"; }
export SPEC_GRACE_MINUTES=30
elapsed_minutes_since_marker() { echo "35"; }
export -f has_new_comment_after_report elapsed_minutes_since_marker

spec_advance_check 20 "$_ITEM_DTP_SPR"
assert_eq "G2: advance: remove-label called" \
  "1" "$(grep -c -- '--remove spec-pending-review' "$STUB_LOG" || echo 0)"
assert_eq "G2: advance: set_board_status REFINED" \
  "1" "$(grep -c "set_board_status 20 ${STATUS_REFINED}" "$STUB_LOG" || echo 0)"

> "$STUB_LOG"
# G3: flag + no comment + elapsed < grace → no action
elapsed_minutes_since_marker() { echo "10"; }
export -f elapsed_minutes_since_marker

spec_advance_check 20 "$_ITEM_DTP_SPR"
assert_eq "G3: within-window: no set_board_status" \
  "0" "$(grep -c 'set_board_status' "$STUB_LOG" || true)"
assert_eq "G3: within-window: no dispatch" \
  "0" "$(grep -c 'dispatch' "$STUB_LOG" || true)"

> "$STUB_LOG"
# G4: no flag → no auto-advance (regression guard)
elapsed_minutes_since_marker() { echo "99"; }
export -f elapsed_minutes_since_marker

spec_advance_check 21 "$_ITEM_NODTP_SPR"
assert_eq "G4: no-flag regression: no advance" \
  "0" "$(grep -c 'set_board_status' "$STUB_LOG" || true)"

> "$STUB_LOG"
# G5: flag + needs-discussion → suppressed (no advance, even with elapsed ≥ grace)
_ITEM_DTP_SPR_ND='{"content":{"number":22},"labels":["direct-to-pr","spec-pending-review","needs-discussion"],"status":"Backlog"}'
elapsed_minutes_since_marker() { echo "99"; }
export -f elapsed_minutes_since_marker

spec_advance_check 22 "$_ITEM_DTP_SPR_ND"
assert_eq "G5: needs-discussion suppresses spec advance" \
  "0" "$(grep -c 'set_board_status' "$STUB_LOG" || true)"
assert_eq "G5: needs-discussion suppresses spec dispatch" \
  "0" "$(grep -c 'dispatch' "$STUB_LOG" || true)"

# Restore stubs
has_new_comment_after_report() { echo "no"; }
elapsed_minutes_since_marker() { echo ""; }
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
export -f has_new_comment_after_report elapsed_minutes_since_marker dispatch

# ==========================================
# H: Entry trigger — direct-to-pr admits Backlog items
# ==========================================
echo ""
echo "--- H: Entry trigger ---"

ITEM_DTP_ONLY='{"content":{"number":30},"labels":["direct-to-pr"],"status":"Backlog"}'
ITEM_RFA_ONLY='{"content":{"number":31},"labels":["ready-for-agent"],"status":"Backlog"}'
ITEM_NEITHER='{"content":{"number":32},"labels":["needs-triage"],"status":"Backlog"}'
ITEM_BOTH='{"content":{"number":33},"labels":["direct-to-pr","ready-for-agent"],"status":"Backlog"}'

# H1: direct-to-pr alone → passes entry gate
(has_opt_in_refine_label "$ITEM_DTP_ONLY" || has_direct_to_pr_label "$ITEM_DTP_ONLY") \
  && assert_eq "H1: direct-to-pr admits item" "0" "0" \
  || assert_eq "H1: direct-to-pr admits item" "0" "1"

# H2: ready-for-agent alone → still passes (unchanged)
(has_opt_in_refine_label "$ITEM_RFA_ONLY" || has_direct_to_pr_label "$ITEM_RFA_ONLY") \
  && assert_eq "H2: ready-for-agent still admits item" "0" "0" \
  || assert_eq "H2: ready-for-agent still admits item" "0" "1"

# H3: neither → blocked
(has_opt_in_refine_label "$ITEM_NEITHER" || has_direct_to_pr_label "$ITEM_NEITHER") \
  && assert_eq "H3: neither label is blocked" "0" "1" \
  || assert_eq "H3: neither label is blocked" "0" "0"

# H4: both labels → passes (direct-to-pr wins, no double-dispatch risk)
(has_opt_in_refine_label "$ITEM_BOTH" || has_direct_to_pr_label "$ITEM_BOTH") \
  && assert_eq "H4: both labels passes gate once" "0" "0" \
  || assert_eq "H4: both labels passes gate once" "0" "1"

# ==========================================
# I: Plan auto-advance (direct-to-pr)
# ==========================================
echo ""
echo "--- I: Plan auto-advance ---"
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
REFINE_RUNNING=0
DISPATCHED=""

_ITEM_DTP_PPR='{"content":{"number":40},"labels":["direct-to-pr","plan-pending-review"],"status":"Refined"}'
_ITEM_NODTP_PPR='{"content":{"number":41},"labels":["plan-pending-review"],"status":"Refined"}'

# I1: flag + human comment → re-plan
has_new_comment_after_report() { echo "yes"; }
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
export -f has_new_comment_after_report dispatch

plan_advance_check 40 "$_ITEM_DTP_PPR"
assert_eq "I1: re-plan: remove-label called" \
  "1" "$(grep -c -- '--remove plan-pending-review' "$STUB_LOG" || echo 0)"
assert_eq "I1: re-plan: Plan dispatched" \
  "1" "$(grep -c 'dispatch Plan issue #40' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"
# I2: flag + no comment + elapsed ≥ grace → advance to Ready
has_new_comment_after_report() { echo "no"; }
export PLAN_GRACE_MINUTES=30
elapsed_minutes_since_marker() { echo "35"; }
export -f has_new_comment_after_report elapsed_minutes_since_marker

plan_advance_check 40 "$_ITEM_DTP_PPR"
assert_eq "I2: advance: remove-label called" \
  "1" "$(grep -c -- '--remove plan-pending-review' "$STUB_LOG" || echo 0)"
assert_eq "I2: advance: set_board_status READY" \
  "1" "$(grep -c "set_board_status 40 ${STATUS_READY}" "$STUB_LOG" || echo 0)"

> "$STUB_LOG"
# I3: flag + no comment + elapsed < grace → no action
elapsed_minutes_since_marker() { echo "10"; }
export -f elapsed_minutes_since_marker

plan_advance_check 40 "$_ITEM_DTP_PPR"
assert_eq "I3: within-window: no set_board_status" \
  "0" "$(grep -c 'set_board_status' "$STUB_LOG" || true)"
assert_eq "I3: within-window: no dispatch" \
  "0" "$(grep -c 'dispatch' "$STUB_LOG" || true)"

> "$STUB_LOG"
# I4: no flag → no auto-advance (regression guard)
elapsed_minutes_since_marker() { echo "99"; }
export -f elapsed_minutes_since_marker

plan_advance_check 41 "$_ITEM_NODTP_PPR"
assert_eq "I4: no-flag regression: no advance" \
  "0" "$(grep -c 'set_board_status' "$STUB_LOG" || true)"

> "$STUB_LOG"
# I5: flag + needs-discussion → suppressed (no advance, even with elapsed ≥ grace)
_ITEM_DTP_PPR_ND='{"content":{"number":42},"labels":["direct-to-pr","plan-pending-review","needs-discussion"],"status":"Refined"}'
elapsed_minutes_since_marker() { echo "99"; }
export -f elapsed_minutes_since_marker

plan_advance_check 42 "$_ITEM_DTP_PPR_ND"
assert_eq "I5: needs-discussion suppresses plan advance" \
  "0" "$(grep -c 'set_board_status' "$STUB_LOG" || true)"
assert_eq "I5: needs-discussion suppresses plan dispatch" \
  "0" "$(grep -c 'dispatch' "$STUB_LOG" || true)"

# Restore
has_new_comment_after_report() { echo "no"; }
elapsed_minutes_since_marker() { echo ""; }
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
export -f has_new_comment_after_report elapsed_minutes_since_marker dispatch

# ==========================================
# J: End-gate auto-merge (direct-to-pr)
# ==========================================
echo ""
echo "--- J: End-gate auto-merge ---"
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
DISPATCHED=""

_ITEM_DTP_REVIEW='{"content":{"number":50},"labels":["direct-to-pr"],"status":"In review"}'
_ITEM_NODTP_REVIEW='{"content":{"number":51},"labels":[],"status":"In review"}'

# J1: flag + APPROVED → Close dispatched
get_pr_for_issue() { echo "99"; }
PROVIDERS_CLI_OUTPUT="APPROVED"
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
export -f get_pr_for_issue dispatch

end_gate_check 50 "$_ITEM_DTP_REVIEW"
assert_eq "J1: APPROVED → Close dispatched" \
  "1" "$(grep -c 'dispatch Close issue #50' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"
# J2: flag + CHANGES_REQUESTED → Continue dispatched
PROVIDERS_CLI_OUTPUT="CHANGES_REQUESTED"

end_gate_check 50 "$_ITEM_DTP_REVIEW"
assert_eq "J2: CHANGES_REQUESTED → Continue dispatched" \
  "1" "$(grep -c 'dispatch Continue issue #50' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"
# J3: flag + no actionable review → no dispatch (fall through)
PROVIDERS_CLI_OUTPUT=""

end_gate_check 50 "$_ITEM_DTP_REVIEW" || true
assert_eq "J3: no review → no dispatch" \
  "0" "$(grep -c 'dispatch' "$STUB_LOG" || true)"

> "$STUB_LOG"
# J4: no flag → no end-gate dispatch (regression guard)
PROVIDERS_CLI_OUTPUT="APPROVED"

end_gate_check 51 "$_ITEM_NODTP_REVIEW" || true
assert_eq "J4: no-flag: no end-gate dispatch" \
  "0" "$(grep -c 'dispatch Close' "$STUB_LOG" || true)"

# Restore
reset_python3_stub
get_pr_for_issue() { echo ""; }
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
export -f gh get_pr_for_issue dispatch

# ==========================================
# K: Priority 1.5 — conflict gate
# ==========================================
echo ""
echo "--- K: Priority 1.5 conflict gate ---"
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
DISPATCHED=""

_ITEM_REVIEW_A='{"content":{"number":60},"labels":[],"status":"In review"}'
_ITEM_REVIEW_B='{"content":{"number":61},"labels":[],"status":"In review"}'
_ITEM_REVIEW_C='{"content":{"number":62},"labels":["needs-discussion"],"status":"In review"}'

# K1: CONFLICTING → dispatch Deconflict
get_pr_for_issue() { echo "200"; }
check_pr_mergeable() { echo "CONFLICTING"; }
is_issue_running() { return 1; }
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
export -f get_pr_for_issue check_pr_mergeable is_issue_running dispatch

CONFLICT_RESOLUTION_ENABLED=true
CI_BLOCKED=""

# Simulate the P1.5 loop body for one item
ISSUE=$(get_issue_number "$_ITEM_REVIEW_A")
has_skip_label "$_ITEM_REVIEW_A" && SKIP=1 || SKIP=0
assert_eq "K1: no-skip-label item passes gate" "0" "$SKIP"

PR_NUM=$(get_pr_for_issue "$ISSUE")
MERGEABLE=$(check_pr_mergeable "$PR_NUM")
case "$MERGEABLE" in
  CONFLICTING)
    if ! is_issue_running "$ISSUE"; then
      if dispatch "Deconflict issue #${ISSUE}"; then
        DISPATCHED="Deconflict issue #${ISSUE}"
      fi
    fi
    ;;
esac
assert_eq "K1: CONFLICTING → Deconflict dispatched" \
  "1" "$(grep -c 'dispatch Deconflict issue #60' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"; DISPATCHED=""

# K2: UNKNOWN → no dispatch
check_pr_mergeable() { echo "UNKNOWN"; }
export -f check_pr_mergeable

ISSUE=$(get_issue_number "$_ITEM_REVIEW_B")
PR_NUM=$(get_pr_for_issue "$ISSUE")
MERGEABLE=$(check_pr_mergeable "$PR_NUM")
case "$MERGEABLE" in
  CONFLICTING)
    dispatch "Deconflict issue #${ISSUE}" || true
    ;;
  UNKNOWN)
    : # skip
    ;;
esac
assert_eq "K2: UNKNOWN → no dispatch" \
  "0" "$(grep -c 'dispatch' "$STUB_LOG" || true)"

> "$STUB_LOG"; DISPATCHED=""

# K3: MERGEABLE → no dispatch
check_pr_mergeable() { echo "MERGEABLE"; }
export -f check_pr_mergeable

ISSUE=$(get_issue_number "$_ITEM_REVIEW_A")
PR_NUM=$(get_pr_for_issue "$ISSUE")
MERGEABLE=$(check_pr_mergeable "$PR_NUM")
case "$MERGEABLE" in
  CONFLICTING)
    dispatch "Deconflict issue #${ISSUE}" || true
    ;;
esac
assert_eq "K3: MERGEABLE → no dispatch" \
  "0" "$(grep -c 'dispatch' "$STUB_LOG" || true)"

> "$STUB_LOG"; DISPATCHED=""

# K4: skip label → no dispatch even if CONFLICTING
check_pr_mergeable() { echo "CONFLICTING"; }
export -f check_pr_mergeable

ISSUE=$(get_issue_number "$_ITEM_REVIEW_C")
has_skip_label "$_ITEM_REVIEW_C" \
  && assert_eq "K4: needs-discussion is a skip label" "0" "0" \
  || assert_eq "K4: needs-discussion is a skip label" "0" "1"

if ! has_skip_label "$_ITEM_REVIEW_C"; then
  dispatch "Deconflict issue #${ISSUE}" || true
fi
assert_eq "K4: skip label suppresses deconflict dispatch" \
  "0" "$(grep -c 'dispatch' "$STUB_LOG" || true)"

> "$STUB_LOG"; DISPATCHED=""

# K5: CONFLICT_RESOLUTION_ENABLED=false → entire P1.5 block skipped
check_pr_mergeable() { echo "CONFLICTING"; }
export -f check_pr_mergeable

CONFLICT_RESOLUTION_ENABLED=false
if [ "${CONFLICT_RESOLUTION_ENABLED:-true}" = "true" ]; then
  dispatch "Deconflict issue #60" || true
fi
assert_eq "K5: kill-switch disables conflict gate" \
  "0" "$(grep -c 'dispatch' "$STUB_LOG" || true)"
CONFLICT_RESOLUTION_ENABLED=true

> "$STUB_LOG"; DISPATCHED=""

# K6: is_issue_running → no duplicate dispatch
is_issue_running() { return 0; }
export -f is_issue_running

ISSUE=$(get_issue_number "$_ITEM_REVIEW_A")
if ! is_issue_running "$ISSUE"; then
  dispatch "Deconflict issue #${ISSUE}" || true
fi
assert_eq "K6: running issue skipped" \
  "0" "$(grep -c 'dispatch' "$STUB_LOG" || true)"

# K7: check_pr_mergeable returns correct format
check_pr_mergeable() {
  local pr_num="$1"
  gh pr view "$pr_num" --repo "omniscient/markethawk" --json mergeable --jq '.mergeable' 2>/dev/null || echo "UNKNOWN"
}
gh() {
  case "$*" in
    *"pr view"*) echo "CONFLICTING" ;;
    *) echo "gh $*" >> "$STUB_LOG" ;;
  esac
  return 0
}
export -f check_pr_mergeable gh

_RESULT=$(check_pr_mergeable "99")
assert_eq "K7: check_pr_mergeable returns value from gh" "CONFLICTING" "$_RESULT"

> "$STUB_LOG"; DISPATCHED=""

# K8: P1.5-1 — increment_retry recorded after CONFLICTING dispatch
echo '{}' > "$STATE_FILE"
check_pr_mergeable() { echo "CONFLICTING"; }
is_issue_running() { return 1; }
export -f check_pr_mergeable is_issue_running

ISSUE=$(get_issue_number "$_ITEM_REVIEW_A")
PR_NUM=$(get_pr_for_issue "$ISSUE")
MERGEABLE=$(check_pr_mergeable "$PR_NUM")
case "$MERGEABLE" in
  CONFLICTING)
    if ! is_issue_running "$ISSUE"; then
      increment_retry "${ISSUE}:resolve" || true
      dispatch "Deconflict issue #${ISSUE}" || true
    fi
    ;;
esac
assert_eq "K8: increment_retry recorded after CONFLICTING dispatch" \
  "1" "$(get_retry_count "60:resolve")"

> "$STUB_LOG"; DISPATCHED=""

# K9: P1.5-5 — trip_to_blocked delegates to breaker-trip at MAX_RETRIES
echo "{\"60:resolve\": $MAX_RETRIES}" > "$STATE_FILE"

ISSUE=$(get_issue_number "$_ITEM_REVIEW_A")
RETRIES=$(get_retry_count "${ISSUE}:resolve")

# python3 (the global default stub) tees the breaker-trip delegation (the board-move
# to Blocked itself runs in breaker.py and is covered by test_factory_core_breaker.py).
if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
  trip_to_blocked "$ISSUE" "resolve" "retry limit of ${MAX_RETRIES} reached for conflict resolution"
else
  dispatch "Deconflict issue #${ISSUE}" || true
fi
assert_eq "K9: delegates to breaker-trip CLI (resolve)" \
  "1" "$(grep -c 'breaker-trip --issue 60 --phase resolve' "$STUB_LOG" || echo 0)"
assert_eq "K9: no dispatch on trip" \
  "0" "$(grep -c 'dispatch' "$STUB_LOG" || true)"
assert_eq "K9: retry counter reset to 0" \
  "0" "$(get_retry_count "60:resolve")"

# Restore stubs
gh() { echo "gh $*" >> "$STUB_LOG"; return 0; }
get_pr_for_issue() { echo ""; }
is_issue_running() { return 1; }
check_pr_mergeable() { echo "UNKNOWN"; }
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
export -f gh get_pr_for_issue is_issue_running check_pr_mergeable dispatch

# ==========================================
# L: factory_at_capacity / FACTORY_WIP_LIMIT
# ==========================================
echo ""
echo "--- L: factory_at_capacity ---"

assert_eq "L0: FACTORY_WIP_LIMIT=1 (test-provided)" "1" "${FACTORY_WIP_LIMIT:-}"

FACTORY_WIP_LIMIT=1
factory_at_capacity 0 \
  && assert_eq "L1: 0 running, limit 1 → below capacity" "0" "1" \
  || assert_eq "L1: 0 running, limit 1 → below capacity" "0" "0"
factory_at_capacity 1 \
  && assert_eq "L2: 1 running, limit 1 → at capacity" "0" "0" \
  || assert_eq "L2: 1 running, limit 1 → at capacity" "0" "1"

FACTORY_WIP_LIMIT=2
factory_at_capacity 1 \
  && assert_eq "L3: 1 running, limit 2 → below capacity" "0" "1" \
  || assert_eq "L3: 1 running, limit 2 → below capacity" "0" "0"
factory_at_capacity 2 \
  && assert_eq "L4: 2 running, limit 2 → at capacity" "0" "0" \
  || assert_eq "L4: 2 running, limit 2 → at capacity" "0" "1"
factory_at_capacity 3 \
  && assert_eq "L5: 3 running, limit 2 → at capacity" "0" "0" \
  || assert_eq "L5: 3 running, limit 2 → at capacity" "0" "1"

FACTORY_WIP_LIMIT=1

# ==========================================
# M: Main-red recheck self-clear (#365)
# ==========================================
echo ""
echo "--- M: main-red recheck self-clear ---"
> "$STUB_LOG"
DISPATCHED=""

dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
is_recheck_running() { return 1; }
export -f dispatch is_recheck_running

# M1: no stamp → due
rm -f "$RECHECK_STAMP_FILE"
recheck_due \
  && assert_eq "M1: no stamp → due" "0" "0" \
  || assert_eq "M1: no stamp → due" "0" "1"

# M2: fresh stamp → throttled
touch "$RECHECK_STAMP_FILE"
recheck_due \
  && assert_eq "M2: fresh stamp → throttled" "0" "1" \
  || assert_eq "M2: fresh stamp → throttled" "0" "0"

# M3: stale stamp (older than MAIN_RED_RECHECK_MINUTES=20) → due
touch -d "25 minutes ago" "$RECHECK_STAMP_FILE"
recheck_due \
  && assert_eq "M3: stale stamp → due" "0" "0" \
  || assert_eq "M3: stale stamp → due" "0" "1"

# M4: due → dispatches "Recheck main", sets DISPATCHED, refreshes the stamp
rm -f "$RECHECK_STAMP_FILE"; DISPATCHED=""
main_red_recheck_check
assert_eq "M4: Recheck main dispatched" \
  "1" "$(grep -c 'dispatch Recheck main' "$STUB_LOG" || echo 0)"
assert_eq "M4: DISPATCHED set" "Recheck main" "$DISPATCHED"
[ -f "$RECHECK_STAMP_FILE" ] \
  && assert_eq "M4: stamp refreshed" "0" "0" \
  || assert_eq "M4: stamp refreshed" "0" "1"

# M5: stamp fresh from M4 → throttled, no dispatch
> "$STUB_LOG"; DISPATCHED=""
main_red_recheck_check
assert_eq "M5: throttled → no dispatch" \
  "0" "$(grep -c 'dispatch' "$STUB_LOG" || true)"

# M6: recheck container already running → no dispatch even when due
> "$STUB_LOG"; DISPATCHED=""
rm -f "$RECHECK_STAMP_FILE"
is_recheck_running() { return 0; }
export -f is_recheck_running
main_red_recheck_check
assert_eq "M6: running recheck → no dispatch" \
  "0" "$(grep -c 'dispatch' "$STUB_LOG" || true)"

# M7: kill switch off → no dispatch even when due
> "$STUB_LOG"; DISPATCHED=""
is_recheck_running() { return 1; }
export -f is_recheck_running
MAIN_RED_RECHECK_ENABLED=false
main_red_recheck_check
assert_eq "M7: kill switch → no dispatch" \
  "0" "$(grep -c 'dispatch' "$STUB_LOG" || true)"
MAIN_RED_RECHECK_ENABLED=true

# Restore stubs
is_recheck_running() { return 1; }
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
export -f is_recheck_running dispatch

# ==========================================
# N: dependencies_met() — off-board fallback
# ==========================================
echo ""
echo "--- N: dependencies_met ---"
> "$STUB_LOG"

# Shared stub variables for this section
_N_BODY=""
_N_DEP200_STATE=""
_N_DEP200_GH_EXIT=0
_N_DEP201_STATE=""
_N_DEP202_STATE=""

# python3 stub: routes by --id; body call (issue 100) → _N_BODY; state calls → per-dep
# state var. _N_DEP200_GH_EXIT=1 simulates the underlying gh failure that
# GitHubTracker.get_item swallows internally and returns as {} (no state key).
python3() {
  echo "python3 $*" >> "$STUB_LOG"
  case "$*" in
    *"--id 100 --fields body"*)
      jq -n --arg body "$_N_BODY" '{body:$body}' ;;
    *"--id 200 --fields state"*)
      if [ "$_N_DEP200_GH_EXIT" -ne 0 ]; then echo '{}'; else jq -n --arg state "$_N_DEP200_STATE" '{state:$state}'; fi ;;
    *"--id 201 --fields state"*)
      jq -n --arg state "$_N_DEP201_STATE" '{state:$state}' ;;
    *"--id 202 --fields state"*)
      jq -n --arg state "$_N_DEP202_STATE" '{state:$state}' ;;
    *providers/cli.py*) return 0 ;;
    *) "$_REAL_PY3" "$@" ;;
  esac
}
export -f python3

_BOARD_EMPTY='{"items":[]}'
_BOARD_200_DONE='{"items":[{"content":{"number":200},"status":"Done"}]}'
_BOARD_200_WIP='{"items":[{"content":{"number":200},"status":"In Progress"}]}'
_BOARD_200_DONE_201_ABSENT='{"items":[{"content":{"number":200},"status":"Done"}]}'

# N1: no deps in body → returns 0
_N_BODY="No dependencies here"
> "$STUB_LOG"
dependencies_met "100" "$_BOARD_EMPTY" && _N_RET=0 || _N_RET=1
assert_eq "N1: no deps → returns 0" "0" "$_N_RET"

# N2: dep Done on board → returns 0, no dep_gate log
_N_BODY="Depends on: #200"
> "$STUB_LOG"
dependencies_met "100" "$_BOARD_200_DONE" && _N_RET=0 || _N_RET=1
assert_eq "N2: dep Done on board → returns 0" "0" "$_N_RET"
assert_eq "N2: Done dep is silent (no dep_gate log)" \
  "0" "$(grep -c 'dep_gate' "$STUB_LOG" || true)"

# N3: dep non-Done on board → returns 1, logs dep_gate
_N_BODY="Depends on: #200"
> "$STUB_LOG"
_N_OUTPUT=$(dependencies_met "100" "$_BOARD_200_WIP" 2>&1) && _N_RET=0 || _N_RET=1
assert_eq "N3: non-Done dep → returns 1" "1" "$_N_RET"
assert_eq "N3: non-Done dep → dep_gate logged" \
  "1" "$(echo "$_N_OUTPUT" | grep -c 'dep_gate' || true)"

# N4: dep off-board, gh state=CLOSED → returns 0, logs resolved=closed_off_board
_N_BODY="Depends on: #200"
_N_DEP200_STATE="CLOSED"
_N_DEP200_GH_EXIT=0
> "$STUB_LOG"
_N_OUTPUT=$(dependencies_met "100" "$_BOARD_EMPTY" 2>&1) && _N_RET=0 || _N_RET=1
assert_eq "N4: off-board CLOSED dep → returns 0" "0" "$_N_RET"
assert_eq "N4: off-board CLOSED → logs resolved=closed_off_board" \
  "1" "$(echo "$_N_OUTPUT" | grep -c 'resolved=closed_off_board' || true)"

# N5: dep off-board, gh state=OPEN → returns 1, logs dep_status=off_board
_N_BODY="Depends on: #200"
_N_DEP200_STATE="OPEN"
_N_DEP200_GH_EXIT=0
> "$STUB_LOG"
_N_OUTPUT=$(dependencies_met "100" "$_BOARD_EMPTY" 2>&1) && _N_RET=0 || _N_RET=1
assert_eq "N5: off-board OPEN dep → returns 1" "1" "$_N_RET"
assert_eq "N5: off-board OPEN → logs dep_status=off_board" \
  "1" "$(echo "$_N_OUTPUT" | grep -c 'dep_status=off_board' || true)"

# N6: dep off-board, gh state call fails/empty → returns 1 (safe direction)
_N_BODY="Depends on: #200"
_N_DEP200_STATE=""
_N_DEP200_GH_EXIT=1
> "$STUB_LOG"
dependencies_met "100" "$_BOARD_EMPTY" && _N_RET=0 || _N_RET=1
assert_eq "N6: off-board gh-failure dep → returns 1 (safe)" "1" "$_N_RET"

# N7: two deps — first Done on board, second off-board OPEN → returns 1
_N_BODY="$(printf 'Depends on: #200\nDepends on: #201')"
_N_DEP200_STATE=""
_N_DEP200_GH_EXIT=0
_N_DEP201_STATE="OPEN"
> "$STUB_LOG"
dependencies_met "100" "$_BOARD_200_DONE_201_ABSENT" && _N_RET=0 || _N_RET=1
assert_eq "N7: two deps, second off-board OPEN → returns 1" "1" "$_N_RET"

# N8: two deps — first Done on board, second off-board CLOSED → returns 0
_N_BODY="$(printf 'Depends on: #200\nDepends on: #201')"
_N_DEP200_STATE=""
_N_DEP200_GH_EXIT=0
_N_DEP201_STATE="CLOSED"
> "$STUB_LOG"
dependencies_met "100" "$_BOARD_200_DONE_201_ABSENT" && _N_RET=0 || _N_RET=1
assert_eq "N8: two deps, second off-board CLOSED → returns 0" "0" "$_N_RET"

# N9: fenced fake dep — closed fence does not count as a dependency
_N_BODY="$(printf '```\nDepends on: #999\n```\n')"
> "$STUB_LOG"
dependencies_met "100" "$_BOARD_EMPTY" && _N_RET=0 || _N_RET=1
assert_eq "N9: fenced fake dep → returns 0 (no real dep)" "0" "$_N_RET"

# N10: unclosed fence — everything after an unclosed ``` is dropped
_N_BODY="$(printf 'Some notes.\n```\nDepends on: #999\n')"
> "$STUB_LOG"
dependencies_met "100" "$_BOARD_EMPTY" && _N_RET=0 || _N_RET=1
assert_eq "N10: unclosed fence → returns 0 (no real dep)" "0" "$_N_RET"

# N11: inline code span — backtick-quoted example does not count
_N_BODY="See \`Depends on: #999\` for the old format."
> "$STUB_LOG"
dependencies_met "100" "$_BOARD_EMPTY" && _N_RET=0 || _N_RET=1
assert_eq "N11: inline code span → returns 0 (no real dep)" "0" "$_N_RET"

# N12: bold label, bold wraps label+colon — **Depends on:** #200
_N_BODY="**Depends on:** #200"
> "$STUB_LOG"
_N_OUTPUT=$(dependencies_met "100" "$_BOARD_200_WIP" 2>&1) && _N_RET=0 || _N_RET=1
assert_eq "N12: bold label (**Depends on:**) → returns 1" "1" "$_N_RET"
assert_eq "N12: bold label → dep_gate logged" \
  "1" "$(echo "$_N_OUTPUT" | grep -c 'dep_gate' || true)"

# N13: bold label, bold wraps only the word — **Depends on**: #200
_N_BODY="**Depends on**: #200"
> "$STUB_LOG"
dependencies_met "100" "$_BOARD_200_WIP" && _N_RET=0 || _N_RET=1
assert_eq "N13: bold label (**Depends on**:) → returns 1" "1" "$_N_RET"

# N14: plain label, bold ref — Depends on: **#200**
_N_BODY="Depends on: **#200**"
> "$STUB_LOG"
dependencies_met "100" "$_BOARD_200_WIP" && _N_RET=0 || _N_RET=1
assert_eq "N14: bold ref (Depends on: **#200**) → returns 1" "1" "$_N_RET"

# N15: multi-ref line — Depends on: #200, #201 blocks on both (mirrors N7)
_N_BODY="Depends on: #200, #201"
_N_DEP201_STATE="OPEN"
> "$STUB_LOG"
dependencies_met "100" "$_BOARD_200_DONE" && _N_RET=0 || _N_RET=1
assert_eq "N15: multi-ref line, second off-board OPEN → returns 1" "1" "$_N_RET"

# N16: Blocked-by section, all three bullet markers (-, *, +), mixed
# resolution paths — proves each marker is scanned and refs are checked
_N_BODY="$(printf '## Blocked by\n- #200\n* #201\n+ #202\n')"
_N_DEP201_STATE="CLOSED"
_N_DEP202_STATE="OPEN"
> "$STUB_LOG"
_N_OUTPUT=$(dependencies_met "100" "$_BOARD_200_DONE" 2>&1) && _N_RET=0 || _N_RET=1
assert_eq "N16: Blocked-by (-/*/+  markers) → returns 1" "1" "$_N_RET"
assert_eq "N16: Blocked-by → blocked_by=#202 logged" \
  "1" "$(echo "$_N_OUTPUT" | grep -c 'blocked_by=#202' || true)"

# N17: lowercase, deeper heading level — ### blocked by
_N_BODY="$(printf '### blocked by\n- #200\n')"
> "$STUB_LOG"
dependencies_met "100" "$_BOARD_200_WIP" && _N_RET=0 || _N_RET=1
assert_eq "N17: lowercase '### blocked by' heading → returns 1" "1" "$_N_RET"

# N18: a following heading of any level ends the section
_N_BODY="$(printf '## Blocked by\n- #200\n## Other\n- #201\n')"
_N_DEP201_STATE="OPEN"
> "$STUB_LOG"
dependencies_met "100" "$_BOARD_200_DONE" && _N_RET=0 || _N_RET=1
assert_eq "N18: heading ends Blocked-by section → returns 0 (#201 not scanned)" "0" "$_N_RET"

# N19: multi-ref bullet under Blocked by — mirrors N7 shape
_N_BODY="$(printf '## Blocked by\n- #200, #201\n')"
_N_DEP201_STATE="OPEN"
> "$STUB_LOG"
dependencies_met "100" "$_BOARD_200_DONE" && _N_RET=0 || _N_RET=1
assert_eq "N19: multi-ref bullet, second off-board OPEN → returns 1" "1" "$_N_RET"

# N20: body fetch fails → returns 0 (pre-existing behaviour)
# Override python3 so the body call for issue 100 returns non-zero
python3() {
  echo "python3 $*" >> "$STUB_LOG"
  case "$*" in
    *"--id 100 --fields body"*) return 1 ;;
    *providers/cli.py*) return 0 ;;
    *) "$_REAL_PY3" "$@" ;;
  esac
}
export -f python3
> "$STUB_LOG"
dependencies_met "100" "$_BOARD_EMPTY" && _N_RET=0 || _N_RET=1
assert_eq "N20: body fetch fails → returns 0" "0" "$_N_RET"

# Restore default python3 stub
reset_python3_stub

# ==========================================
# O: fetch_board_items paginates project items
# ==========================================
echo ""
echo "--- O: fetch_board_items pagination ---"

gh() {
  echo "gh $*" >> "$STUB_LOG"
  if echo "$*" | grep -q 'after: "CUR1"'; then
    cat <<'JSON'
{"data":{"node":{"items":{"pageInfo":{"hasNextPage":false,"endCursor":null},"nodes":[{"fieldValueByName":{"name":"Backlog"},"content":{"number":102,"title":"second page issue","labels":{"nodes":[{"name":"ready-for-agent"}]}}}]}}}}
JSON
  else
    cat <<'JSON'
{"data":{"node":{"items":{"pageInfo":{"hasNextPage":true,"endCursor":"CUR1"},"nodes":[{"fieldValueByName":{"name":"Backlog"},"content":{"number":101,"title":"first page issue","labels":{"nodes":[{"name":"ready-for-agent"}]}}}]}}}}
JSON
  fi
}
export -f gh
> "$STUB_LOG"

_BOARD_PAGE_RESULT=$(fetch_board_items)
assert_eq "fetch_board_items returns both pages" "2" "$(echo "$_BOARD_PAGE_RESULT" | jq '.items | length')"
assert_eq "fetch_board_items requests first: 100 on both pages" "2" "$(grep -c 'items(first: 100' "$STUB_LOG" || true)"
assert_eq "fetch_board_items requests the cursor page" "1" "$(grep -c 'after: "CUR1"' "$STUB_LOG" || true)"

gh() { echo "gh $*" >> "$STUB_LOG"; return 0; }
export -f gh

# ==========================================
# P: Provider preflight gate (#250)
# ==========================================
echo ""
echo "--- P: Provider preflight gate ---"

assert_eq "sourcing calls providers preflight before the poll loop" \
  "1" "$(echo "$SOURCE_TIME_LOG" | grep -c 'providers/cli.py preflight' || echo 0)"

# Isolated subshell (must not run in-process — a preflight failure trips
# `set -e` inside scheduler.sh, which would kill this test runner if sourced
# directly): stub python3 to fail the preflight call and confirm sourcing
# aborts non-zero, matching the legacy inline `exit 1` behavior.
set +e
PREFLIGHT_FAIL_OUT=$(bash -c '
  set -uo pipefail
  python3() {
    case "$*" in
      *providers/cli.py\ preflight*) echo "ERROR: GH_TOKEN is not set. Add it to .archon/.env" >&2; return 1 ;;
      *) command python3 "$@" ;;
    esac
  }
  export -f python3
  export SCHEDULER_STATE_DIR=$(mktemp -d /tmp/sched-test-statedir-XXXXXX)
  SCHEDULER_SOURCE_ONLY=1 source "'"$SCHED"'"
  echo "SHOULD_NOT_REACH_HERE"
' 2>&1)
PREFLIGHT_FAIL_EXIT=$?
set -e
assert_eq "preflight failure aborts sourcing (non-zero exit)" \
  "1" "$([ "$PREFLIGHT_FAIL_EXIT" -ne 0 ] && echo 1 || echo 0)"
assert_eq "preflight failure never reaches past validation" \
  "0" "$(echo "$PREFLIGHT_FAIL_OUT" | grep -c 'SHOULD_NOT_REACH_HERE')"

# ==========================================
# Q: get_items_by_status case-insensitive matching (#275)
# ==========================================
echo ""
echo "--- Q: get_items_by_status case-insensitive matching ---"

_Q_FIXTURE='{"items":[
  {"status":"In Review","content":{"number":501,"title":"review item","type":"Issue"}},
  {"status":"In Progress","content":{"number":502,"title":"progress item","type":"Issue"}}
]}'

_Q_IN_REVIEW=$(get_items_by_status "$_Q_FIXTURE" "In review")
assert_eq "Q1: get_items_by_status: call-site 'In review' matches board's 'In Review'" \
  "1" "$(echo "$_Q_IN_REVIEW" | jq 'length')"

_Q_IN_PROGRESS=$(get_items_by_status "$_Q_FIXTURE" "In progress")
assert_eq "Q2: get_items_by_status: call-site 'In progress' matches board's 'In Progress'" \
  "1" "$(echo "$_Q_IN_PROGRESS" | jq 'length')"

export -f get_items_by_status
_Q_FIXTURE_NULL='{"items":[{"status":null,"content":{"number":503,"title":"unassigned item","type":"Issue"}}]}'
_Q_NULL_OUT=$(bash -c 'set -euo pipefail; get_items_by_status "$1" "$2"' _ "$_Q_FIXTURE_NULL" "In review" 2>&1)
_Q_NULL_EXIT=$?
assert_eq "Q3: get_items_by_status: null status does not raise under set -e" "0" "$_Q_NULL_EXIT"
assert_eq "Q4: get_items_by_status: null status excluded from bucket" "0" "$(echo "$_Q_NULL_OUT" | jq 'length')"

# ==========================================
# R: Stage guard semantics (#185) — dispatch_stage must preserve per-stage heterogeneity
# ==========================================
echo ""
echo "--- R: Stage guard semantics ---"

# R1: stage_review_triage (P1) has no guard — dispatch_stage must still call it when MAIN_IS_RED=true.
MAIN_IS_RED=true; SESSION_WINDOW_PAUSED=false
IN_REVIEW='[{"content":{"number":901,"title":"t"},"labels":[]}]'
DISPATCHED=""; CI_BLOCKED=""
get_new_comments() { echo '[{"body":"please continue","author":"human"}]'; }
classify_comments() { echo "CONTINUE"; }
is_issue_running() { return 1; }
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
export -f get_new_comments classify_comments is_issue_running dispatch
: > "$STUB_LOG"
dispatch_stage stage_review_triage
assert_eq "R1: dispatch_stage(stage_review_triage) still dispatches Continue when MAIN_IS_RED=true" \
  "1" "$(grep -c 'dispatch Continue issue #901' "$STUB_LOG" || true)"

# R2: stage_plan (P4) is SESSION_WINDOW_PAUSED-only — dispatch_stage must still call it when only MAIN_IS_RED=true.
MAIN_IS_RED=true; SESSION_WINDOW_PAUSED=false; REFINED='[]'; REFINE_RUNNING=0
STDOUT_R2=$(dispatch_stage stage_plan 2>&1)
assert_eq "R2: dispatch_stage(stage_plan) does not skip when only MAIN_IS_RED is true" \
  "0" "$(echo "$STDOUT_R2" | grep -c 'action=skip_plan')"

# R3: dispatch_stage(stage_plan) DOES skip when SESSION_WINDOW_PAUSED=true.
MAIN_IS_RED=false; SESSION_WINDOW_PAUSED=true
STDOUT_R3=$(dispatch_stage stage_plan 2>&1)
assert_eq "R3: dispatch_stage(stage_plan) skips on session_window_paused" \
  "1" "$(echo "$STDOUT_R3" | grep -c 'action=skip_plan')"

# R4: dispatch_stage(stage_conflict_resolve) skips on MAIN_IS_RED=true (red-or-paused guard).
MAIN_IS_RED=true; SESSION_WINDOW_PAUSED=false; IN_REVIEW='[]'
STDOUT_R4=$(dispatch_stage stage_conflict_resolve 2>&1)
assert_eq "R4: dispatch_stage(stage_conflict_resolve) skips on main_red" \
  "1" "$(echo "$STDOUT_R4" | grep -c 'action=skip_deconflict')"

# R5: dispatch_stage(stage_ready_implement) skips on SESSION_WINDOW_PAUSED=true (red-or-paused guard).
MAIN_IS_RED=false; SESSION_WINDOW_PAUSED=true; READY='[]'
STDOUT_R5=$(dispatch_stage stage_ready_implement 2>&1)
assert_eq "R5: dispatch_stage(stage_ready_implement) skips on session_window_paused" \
  "1" "$(echo "$STDOUT_R5" | grep -c 'action=skip_implement')"

# R6: dispatch_stage(stage_blocked_retry) skips on MAIN_IS_RED=true (red-or-paused guard).
MAIN_IS_RED=true; SESSION_WINDOW_PAUSED=false; BLOCKED='[]'; RESCUED=""
STDOUT_R6=$(dispatch_stage stage_blocked_retry 2>&1)
assert_eq "R6: dispatch_stage(stage_blocked_retry) skips on main_red" \
  "1" "$(echo "$STDOUT_R6" | grep -c 'action=skip_blocked_retry')"

MAIN_IS_RED=false; SESSION_WINDOW_PAUSED=false

# R7: stage_epic_autopilot is excluded from the guard table (own compound condition, R3).
assert_eq "R7: stage_epic_autopilot not a STAGE_GUARD key" \
  "0" "$([ -v 'STAGE_GUARD[stage_epic_autopilot]' ] && echo 1 || echo 0)"
assert_eq "R7b: stage_epic_autopilot not in STAGE_ORDER" \
  "0" "$(printf '%s\n' "${STAGE_ORDER[@]}" | grep -c '^stage_epic_autopilot$')"

# R8: stage_review_triage is in STAGE_ORDER with guard type "none" (runs unconditionally
# through dispatch_stage, matching its already-guardless behavior).
assert_eq "R8: stage_review_triage is in STAGE_ORDER" \
  "1" "$(printf '%s\n' "${STAGE_ORDER[@]}" | grep -c '^stage_review_triage$')"
assert_eq "R8b: stage_review_triage guard type is none" \
  "none" "${STAGE_GUARD[stage_review_triage]}"

# R9: dispatch_stage(stage_orphan_sweep) is a no-op when SESSION_WINDOW_PAUSED=true — the
# guard must fire before the sweep's body (set_board_status/gh calls) ever runs (#334).
MAIN_IS_RED=false; SESSION_WINDOW_PAUSED=true
IN_PROGRESS='[{"content":{"number":961,"title":"t"},"labels":[]}]'
: > "$STUB_LOG"
STDOUT_R9=$(dispatch_stage stage_orphan_sweep 2>&1)
assert_eq "R9: dispatch_stage(stage_orphan_sweep) skips on session_window_paused" \
  "1" "$(echo "$STDOUT_R9" | grep -c 'action=skip_orphan_sweep')"
assert_eq "R9b: no set_board_status call when skipped" \
  "0" "$(grep -c 'set_board_status' "$STUB_LOG" || true)"
assert_eq "R9c: no gh comment call when skipped" \
  "0" "$(grep -c 'gh issue comment' "$STUB_LOG" || true)"

# R10: dispatch_stage(stage_orphan_sweep) still sweeps normally when SESSION_WINDOW_PAUSED=false
# (regression guard: unpaused orphan recovery is unchanged).
MAIN_IS_RED=false; SESSION_WINDOW_PAUSED=false
IN_PROGRESS='[{"content":{"number":962,"title":"t"},"labels":[]}]'
: > "$STUB_LOG"
STDOUT_R10=$(dispatch_stage stage_orphan_sweep 2>&1)
assert_eq "R10: dispatch_stage(stage_orphan_sweep) does not skip when unpaused" \
  "0" "$(echo "$STDOUT_R10" | grep -c 'action=skip_orphan_sweep')"
assert_eq "R10b: sweep runs and moves orphaned issue 962 to blocked" \
  "1" "$(echo "$STDOUT_R10" | grep -c 'sweep=orphaned_in_progress issue=#962 action=move_to_blocked')"
assert_eq "R10c: set_board_status called for issue 962" \
  "1" "$(grep -c 'set_board_status 962' "$STUB_LOG" || true)"

# R11: dispatch_stage(stage_orphan_sweep) still sweeps when MAIN_IS_RED=true and
# SESSION_WINDOW_PAUSED=false — confirms guard type is paused_only, not red_or_paused: a
# genuinely dead container must still be recovered to Blocked while main is red.
MAIN_IS_RED=true; SESSION_WINDOW_PAUSED=false
IN_PROGRESS='[{"content":{"number":963,"title":"t"},"labels":[]}]'
: > "$STUB_LOG"
STDOUT_R11=$(dispatch_stage stage_orphan_sweep 2>&1)
assert_eq "R11: dispatch_stage(stage_orphan_sweep) does not skip when only main_red is true" \
  "0" "$(echo "$STDOUT_R11" | grep -c 'action=skip_orphan_sweep')"
assert_eq "R11b: sweep runs and moves orphaned issue 963 to blocked under main_red" \
  "1" "$(echo "$STDOUT_R11" | grep -c 'sweep=orphaned_in_progress issue=#963 action=move_to_blocked')"

MAIN_IS_RED=false; SESSION_WINDOW_PAUSED=false; IN_PROGRESS='[]'

# R12: stage_orphan_sweep is board reconciliation, not a dispatch decision — it must stay out of
# STAGE_ORDER (mirrors R7b's pattern for stage_epic_autopilot) while still being a STAGE_GUARD
# key of type paused_only (unlike stage_epic_autopilot, which keeps its own compound condition).
assert_eq "R12: stage_orphan_sweep not in STAGE_ORDER" \
  "0" "$(printf '%s\n' "${STAGE_ORDER[@]}" | grep -c '^stage_orphan_sweep$')"
assert_eq "R12b: stage_orphan_sweep is a STAGE_GUARD key with type paused_only" \
  "paused_only" "${STAGE_GUARD[stage_orphan_sweep]:-}"

# R13: static source-text check — the session-window-paused sentinel read must appear BEFORE
# the stage_orphan_sweep dispatch call, which must appear BEFORE the main-is-red block, in
# scheduler.sh's actual poll-loop source. R9-R12 only prove the guard-table binding; this
# proves the physical reorder itself, which is otherwise unreachable under
# SCHEDULER_SOURCE_ONLY=1 (the poll loop never executes in tests) (#334). Each grep is
# `|| true`-guarded on its own assignment: scheduler.sh's `set -euo pipefail` (line 2) is
# inherited into this shell via `source`, so an ungated failing pipeline in a bare assignment
# (not just as a captured argument, which is what R9b/R9c/R10c above do) would abort the whole
# test run on a zero-match grep.
SENTINEL_LINE=$(grep -nF '[ -f "${SCHEDULER_STATE_DIR}/session-window-paused" ]; then' "$SCHED" | head -1 | cut -d: -f1) || true
SWEEP_CALL_LINE=$(grep -nF 'dispatch_stage stage_orphan_sweep' "$SCHED" | head -1 | cut -d: -f1) || true
MAIN_RED_LINE=$(grep -nF '[ -f "${SCHEDULER_STATE_DIR}/main-is-red" ]' "$SCHED" | head -1 | cut -d: -f1) || true
assert_eq "R13: sentinel read precedes stage_orphan_sweep dispatch (source order)" \
  "1" "$([ -n "$SENTINEL_LINE" ] && [ -n "$SWEEP_CALL_LINE" ] && [ "$SENTINEL_LINE" -lt "$SWEEP_CALL_LINE" ] 2>/dev/null && echo 1 || echo 0)"
assert_eq "R13b: stage_orphan_sweep dispatch precedes main-is-red block (source order)" \
  "1" "$([ -n "$SWEEP_CALL_LINE" ] && [ -n "$MAIN_RED_LINE" ] && [ "$SWEEP_CALL_LINE" -lt "$MAIN_RED_LINE" ] 2>/dev/null && echo 1 || echo 0)"

# ==========================================
# S: retry_or_skip_delivery_failure (#279 skip-retry-counter exemption)
# ==========================================
echo ""
echo "--- S: retry_or_skip_delivery_failure ---"
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"

assert_eq "S1: non-delivery signature returns count" "count" \
  "$(retry_or_skip_delivery_failure 60 refine "substantive:test_failure:1" "60:refine" 3)"
assert_eq "S1b: non-delivery signature creates no shadow counter" "0" \
  "$(get_retry_count "60:refine:delivery")"

assert_eq "S2: empty signature returns count" "count" \
  "$(retry_or_skip_delivery_failure 60 refine "" "60:refine" 3)"

echo '{}' > "$STATE_FILE"
D1=$(retry_or_skip_delivery_failure 61 refine "environmental:delivery_failure" "61:refine" 3)
assert_eq "S3: 1st delivery failure under cap returns skip" "skip" "$D1"
assert_eq "S3b: shadow counter incremented to 1" "1" "$(get_retry_count "61:refine:delivery")"
assert_eq "S3c: normal counter untouched" "0" "$(get_retry_count "61:refine")"

D2=$(retry_or_skip_delivery_failure 61 refine "environmental:delivery_failure" "61:refine" 3)
assert_eq "S4: 2nd delivery failure under cap returns skip" "skip" "$D2"
assert_eq "S4b: shadow counter incremented to 2" "2" "$(get_retry_count "61:refine:delivery")"

D3=$(retry_or_skip_delivery_failure 61 refine "environmental:delivery_failure" "61:refine" 3)
assert_eq "S5: 3rd delivery failure at cap returns a trip: decision" \
  "1" "$(echo "$D3" | grep -c '^trip:')"
assert_eq "S5b: trip reason names the consecutive count and #279" \
  "1" "$(echo "$D3" | grep -c "3 consecutive times.*#279")"
assert_eq "S5c: back-fill delegates to breaker-set-retry with the shadow count" \
  "1" "$(grep -c 'breaker-set-retry --key 61:refine --value 3' "$STUB_LOG" || echo 0)"
assert_eq "S5d: normal counter back-filled to 3" "3" "$(get_retry_count "61:refine")"

# S6: the diagnostic log line must go to stderr, not pollute the captured decision
echo '{}' > "$STATE_FILE"
D_CLEAN=$(retry_or_skip_delivery_failure 62 refine "environmental:delivery_failure" "62:refine" 5 2>/dev/null)
assert_eq "S6: decision value is exactly 'skip', not polluted by the log line" "skip" "$D_CLEAN"

# S7: reset_retry clears the shadow counter (#279 Requirement 5 / breaker.py Task 1)
echo '{}' > "$STATE_FILE"
retry_or_skip_delivery_failure 63 refine "environmental:delivery_failure" "63:refine" 3 > /dev/null
retry_or_skip_delivery_failure 63 refine "environmental:delivery_failure" "63:refine" 3 > /dev/null
assert_eq "S7: shadow counter at 2 before reset" "2" "$(get_retry_count "63:refine:delivery")"
reset_retry "63:refine"
assert_eq "S7b: shadow counter cleared by reset_retry" "0" "$(get_retry_count "63:refine:delivery")"

> "$STUB_LOG"

# ==========================================
# T: stage_refine — delivery-failure retry exemption wiring (#279)
# ==========================================
echo ""
echo "--- T: stage_refine — delivery-failure exemption ---"
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
gh() { echo "gh $*" >> "$STUB_LOG"; return 0; }
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
export -f gh dispatch

# Reproduces stage_refine's per-item body (matches this file's existing K-section
# convention of exercising the loop body directly rather than fixturing REFINE_RUNNING/
# REFINE_WIP_LIMIT/BACKLOG end-to-end). This section defines every stub it needs itself
# (gh, dispatch above) rather than relying on whatever an earlier section left bound.
_run_refine_body() {
  local issue="$1"
  SIG_RESULT=$(check_failure_signature "$issue" "refine")
  SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
  if echo "$SIG_RESULT" | grep -q "stuck=true"; then
    trip_to_blocked "$issue" "refine" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
    return
  fi

  rollback_paused_retry "$issue" "refine" "$SIG_VALUE" "${issue}:refine" "$REFINE_MAX_RETRIES"

  PREV_SESSION_WINDOW_PAUSE=""
  [ "$SIG_VALUE" = "environmental:session_window_pause" ] && PREV_SESSION_WINDOW_PAUSE=1

  PREV_DELIVERY_SKIP=""
  DECISION=$(retry_or_skip_delivery_failure "$issue" "refine" "$SIG_VALUE" "${issue}:refine" "$REFINE_MAX_RETRIES" || echo "count")
  case "$DECISION" in
    skip) PREV_DELIVERY_SKIP=1 ;;
    trip:*) trip_to_blocked "$issue" "refine" "${DECISION#trip:}"; return ;;
    count|*)
      RETRIES=$(get_retry_count "${issue}:refine")
      if [ "$RETRIES" -ge "$REFINE_MAX_RETRIES" ]; then
        trip_to_blocked "$issue" "refine" "retry limit of ${REFINE_MAX_RETRIES} reached"
        return
      fi
      increment_retry "${issue}:refine"
      ;;
  esac

  DELIVERY_NOTE=""
  if [ -n "$PREV_DELIVERY_SKIP" ]; then
    DELIVERY_NOTE=" was not counted against the retry budget (runner-side delivery failure, #279)."
  fi
  SESSION_WINDOW_NOTE=$(session_window_pause_note)
  gh issue comment "$issue" --repo test/repo --body "Starting refine.${DELIVERY_NOTE}${SESSION_WINDOW_NOTE}" > /dev/null
  dispatch "Refine issue #${issue}" > /dev/null
}

# T1: a substantive (non-delivery) failure increments the normal counter, no note
_drop_sig 80 refine "substantive:test_failure:1"
_run_refine_body 80
assert_eq "T1: normal counter incremented" "1" "$(get_retry_count "80:refine")"
assert_eq "T1b: no shadow counter created" "0" "$(get_retry_count "80:refine:delivery")"
assert_eq "T1c: dispatched" "1" "$(grep -c 'dispatch Refine issue #80' "$STUB_LOG" || echo 0)"
assert_eq "T1d: comment has no delivery-skip note" "0" "$(grep -c 'was not counted against the retry budget' "$STUB_LOG" || true)"

> "$STUB_LOG"

# T2: a delivery failure under cap dispatches without touching the normal counter,
# and the comment carries the delivery-skip note
echo '{}' > "$STATE_FILE"
_drop_sig 81 refine "environmental:delivery_failure"
_run_refine_body 81
assert_eq "T2: normal counter NOT incremented" "0" "$(get_retry_count "81:refine")"
assert_eq "T2b: shadow counter incremented to 1" "1" "$(get_retry_count "81:refine:delivery")"
assert_eq "T2c: dispatched" "1" "$(grep -c 'dispatch Refine issue #81' "$STUB_LOG" || echo 0)"
assert_eq "T2d: comment carries the delivery-skip note" "1" "$(grep -c 'was not counted against the retry budget' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"

# T3: REFINE_MAX_RETRIES consecutive delivery failures trip, back-filling the normal
# counter (asserted via the breaker-set-retry delegation — trip_to_blocked's own
# reset_retry zeroes the counter again immediately after, matching section B/K9's
# already-passing "counter reset after trip" assertions), and do NOT dispatch on the
# tripping attempt
echo '{}' > "$STATE_FILE"
for i in $(seq 1 "$REFINE_MAX_RETRIES"); do
  _drop_sig 82 refine "environmental:delivery_failure"
  _run_refine_body 82
done
assert_eq "T3: back-fill delegates to breaker-set-retry with the shadow count" \
  "1" "$(grep -c "breaker-set-retry --key 82:refine --value ${REFINE_MAX_RETRIES}" "$STUB_LOG" || echo 0)"
assert_eq "T3b: only REFINE_MAX_RETRIES-1 dispatches occurred (cap attempt trips, no dispatch)" \
  "$((REFINE_MAX_RETRIES - 1))" "$(grep -c 'dispatch Refine issue #82' "$STUB_LOG" || echo 0)"
assert_eq "T3c: breaker-trip delegated with the delivery-failure reason" \
  "1" "$(grep -c 'breaker-trip --issue 82 --phase refine' "$STUB_LOG" || echo 0)"
assert_eq "T3d: normal counter reset to 0 after trip (trip_to_blocked's existing reset_retry)" \
  "0" "$(get_retry_count "82:refine")"

> "$STUB_LOG"

# T4: dispatch → pause → resume leaves the normal retry counter net-unchanged (#341
# acceptance test)
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
_drop_sig 83 refine "substantive:test_failure:1"
_run_refine_body 83
assert_eq "T4a: first (pre-pause) dispatch increments as normal" "1" "$(get_retry_count "83:refine")"
_drop_sig 83 refine "environmental:session_window_pause"
_run_refine_body 83
assert_eq "T4b: rollback + this dispatch's own increment net to no change" "1" "$(get_retry_count "83:refine")"
assert_eq "T4c: dispatched again (not skipped, just not double-counted)" \
  "2" "$(grep -c 'dispatch Refine issue #83' "$STUB_LOG" || echo 0)"
assert_eq "T4d: comment carries the real session_window_pause_note() output" \
  "1" "$(grep -c 'was paused for a Claude session-window exhaustion' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

# T5: clamp at 0 — a pause observed with no prior increment does not go negative and
# does not block the next dispatch
_drop_sig 84 refine "environmental:session_window_pause"
_run_refine_body 84
assert_eq "T5: counter clamped at 0, not negative, after the new dispatch's own +1" \
  "1" "$(get_retry_count "84:refine")"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

# T6: the drop file is consumed exactly once — a second read without a new pause does
# not see a stale pause signature
_drop_sig 85 refine "substantive:test_failure:1"
_run_refine_body 85
_drop_sig 85 refine "environmental:session_window_pause"
_run_refine_body 85
assert_eq "T6a: one observed pause nets the counter unchanged (2 dispatches, 1 rollback)" \
  "1" "$(get_retry_count "85:refine")"
SIG_RESULT_T6=$(check_failure_signature "85" "refine")
assert_eq "T6b: drop file consumed — second read returns no signature" \
  "1" "$(echo "$SIG_RESULT_T6" | grep -c 'sig=$')"

> "$STUB_LOG"

# ==========================================
# U: stage_plan — delivery-failure retry exemption wiring (#279)
# ==========================================
echo ""
echo "--- U: stage_plan — delivery-failure exemption ---"
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
gh() { echo "gh $*" >> "$STUB_LOG"; return 0; }
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
export -f gh dispatch

_run_plan_body() {
  local issue="$1"
  SIG_RESULT=$(check_failure_signature "$issue" "plan")
  SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
  if echo "$SIG_RESULT" | grep -q "stuck=true"; then
    trip_to_blocked "$issue" "plan" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
    return
  fi

  rollback_paused_retry "$issue" "plan" "$SIG_VALUE" "${issue}:plan" "$REFINE_MAX_RETRIES"

  PREV_SESSION_WINDOW_PAUSE=""
  [ "$SIG_VALUE" = "environmental:session_window_pause" ] && PREV_SESSION_WINDOW_PAUSE=1

  PREV_DELIVERY_SKIP=""
  DECISION=$(retry_or_skip_delivery_failure "$issue" "plan" "$SIG_VALUE" "${issue}:plan" "$REFINE_MAX_RETRIES" || echo "count")
  case "$DECISION" in
    skip) PREV_DELIVERY_SKIP=1 ;;
    trip:*) trip_to_blocked "$issue" "plan" "${DECISION#trip:}"; return ;;
    count|*)
      RETRIES=$(get_retry_count "${issue}:plan")
      if [ "$RETRIES" -ge "$REFINE_MAX_RETRIES" ]; then
        trip_to_blocked "$issue" "plan" "retry limit of ${REFINE_MAX_RETRIES} reached"
        return
      fi
      increment_retry "${issue}:plan"
      ;;
  esac

  DELIVERY_NOTE=""
  if [ -n "$PREV_DELIVERY_SKIP" ]; then
    DELIVERY_NOTE=" was not counted against the retry budget (runner-side delivery failure, #279)."
  fi
  SESSION_WINDOW_NOTE=$(session_window_pause_note)
  gh issue comment "$issue" --repo test/repo --body "Starting plan.${DELIVERY_NOTE}${SESSION_WINDOW_NOTE}" > /dev/null
  dispatch "Plan issue #${issue}" > /dev/null
}

_drop_sig 90 plan "substantive:test_failure:1"
_run_plan_body 90
assert_eq "U1: normal counter incremented" "1" "$(get_retry_count "90:plan")"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

_drop_sig 91 plan "environmental:delivery_failure"
_run_plan_body 91
assert_eq "U2: normal counter NOT incremented" "0" "$(get_retry_count "91:plan")"
assert_eq "U2b: shadow counter incremented to 1" "1" "$(get_retry_count "91:plan:delivery")"
assert_eq "U2c: comment carries the delivery-skip note" "1" "$(grep -c 'was not counted against the retry budget' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

for i in $(seq 1 "$REFINE_MAX_RETRIES"); do
  _drop_sig 92 plan "environmental:delivery_failure"
  _run_plan_body 92
done
assert_eq "U3: back-fill delegates to breaker-set-retry with the shadow count" \
  "1" "$(grep -c "breaker-set-retry --key 92:plan --value ${REFINE_MAX_RETRIES}" "$STUB_LOG" || echo 0)"
assert_eq "U3b: breaker-trip delegated" \
  "1" "$(grep -c 'breaker-trip --issue 92 --phase plan' "$STUB_LOG" || echo 0)"
assert_eq "U3c: normal counter reset to 0 after trip" "0" "$(get_retry_count "92:plan")"

> "$STUB_LOG"

# U4: dispatch → pause → resume leaves the plan retry counter net-unchanged
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
_drop_sig 93 plan "substantive:test_failure:1"
_run_plan_body 93
assert_eq "U4a: first (pre-pause) dispatch increments as normal" "1" "$(get_retry_count "93:plan")"
_drop_sig 93 plan "environmental:session_window_pause"
_run_plan_body 93
assert_eq "U4b: rollback + this dispatch's own increment net to no change" "1" "$(get_retry_count "93:plan")"
assert_eq "U4c: comment carries the real session_window_pause_note() output" \
  "1" "$(grep -c 'was paused for a Claude session-window exhaustion' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"

# ==========================================
# V: stage_blocked_retry (implement) — delivery-failure retry exemption (#279)
# ==========================================
echo ""
echo "--- V: stage_blocked_retry — delivery-failure exemption ---"
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
get_pr_for_issue() { echo ""; }
export -f dispatch get_pr_for_issue

_run_blocked_retry_body() {
  local issue="$1"
  SIG_RESULT=$(check_failure_signature "$issue" "implement")
  SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
  if echo "$SIG_RESULT" | grep -q "stuck=true"; then
    trip_to_blocked "$issue" "implement" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
    return
  fi

  rollback_paused_retry "$issue" "implement" "$SIG_VALUE" "$issue" "$MAX_RETRIES"

  DECISION=$(retry_or_skip_delivery_failure "$issue" "implement" "$SIG_VALUE" "$issue" "$MAX_RETRIES" || echo "count")
  case "$DECISION" in
    skip) ;;
    trip:*) trip_to_blocked "$issue" "implement" "${DECISION#trip:}"; return ;;
    count|*)
      RETRIES=$(get_retry_count "$issue")
      if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
        trip_to_blocked "$issue" "implement" "retry limit of ${MAX_RETRIES} reached"
        return
      fi
      increment_retry "$issue"
      ;;
  esac

  if [ -n "$(get_pr_for_issue "$issue")" ]; then
    dispatch "Continue issue #${issue}" > /dev/null
  else
    dispatch "Fix issue #${issue}" > /dev/null
  fi
}

_drop_sig 100 implement "substantive:test_failure:1"
_run_blocked_retry_body 100
assert_eq "V1: normal counter incremented" "1" "$(get_retry_count "100")"
assert_eq "V1b: dispatched Fix (no PR)" "1" "$(grep -c 'dispatch Fix issue #100' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

_drop_sig 101 implement "environmental:delivery_failure"
_run_blocked_retry_body 101
assert_eq "V2: normal counter NOT incremented" "0" "$(get_retry_count "101")"
assert_eq "V2b: shadow counter incremented to 1" "1" "$(get_retry_count "101:delivery")"
assert_eq "V2c: dispatched" "1" "$(grep -c 'dispatch Fix issue #101' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

for i in $(seq 1 "$MAX_RETRIES"); do
  _drop_sig 102 implement "environmental:delivery_failure"
  _run_blocked_retry_body 102
done
assert_eq "V3: back-fill delegates to breaker-set-retry with the shadow count" \
  "1" "$(grep -c "breaker-set-retry --key 102 --value ${MAX_RETRIES}" "$STUB_LOG" || echo 0)"
assert_eq "V3b: breaker-trip delegated" \
  "1" "$(grep -c 'breaker-trip --issue 102 --phase implement' "$STUB_LOG" || echo 0)"
assert_eq "V3c: normal counter reset to 0 after trip" "0" "$(get_retry_count "102")"

> "$STUB_LOG"

# V4: dispatch → pause → resume leaves the implement (bare-key) retry counter net-unchanged
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
_drop_sig 103 implement "substantive:test_failure:1"
_run_blocked_retry_body 103
assert_eq "V4a: first (pre-pause) dispatch increments as normal" "1" "$(get_retry_count "103")"
_drop_sig 103 implement "environmental:session_window_pause"
_run_blocked_retry_body 103
assert_eq "V4b: rollback + this dispatch's own increment net to no change" "1" "$(get_retry_count "103")"
assert_eq "V4c: dispatched again" "2" "$(grep -c 'dispatch Fix issue #103' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

# ==========================================
# W: stage_conflict_resolve (resolve) — delivery-failure retry exemption (#279)
# ==========================================
echo ""
echo "--- W: stage_conflict_resolve — delivery-failure exemption ---"
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
get_pr_for_issue() { echo "500"; }
check_pr_mergeable() { echo "CONFLICTING"; }
export -f dispatch get_pr_for_issue check_pr_mergeable

_run_resolve_body() {
  local issue="$1"
  SIG_RESULT=$(check_failure_signature "$issue" "resolve")
  SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
  if echo "$SIG_RESULT" | grep -q "stuck=true"; then
    trip_to_blocked "$issue" "resolve" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
    return
  fi

  rollback_paused_retry "$issue" "resolve" "$SIG_VALUE" "${issue}:resolve" "$MAX_RETRIES"

  RESOLVE_DELIVERY_SKIP=""
  if [ "$SIG_VALUE" = "environmental:delivery_failure" ]; then
    DPEEK=$(get_retry_count "${issue}:resolve:delivery" || echo 0)
    if [ "$DPEEK" -ge "$MAX_RETRIES" ]; then
      STATE_FILE="$STATE_FILE" python3 "$FACTORY_CORE_CLI" breaker-set-retry --key "${issue}:resolve" --value "$DPEEK"
      trip_to_blocked "$issue" "resolve" "same failure signature 'environmental:delivery_failure' recorded ${DPEEK} consecutive times (suspected runner prompt-delivery bug — see #279), retry budget exhausted"
      return
    fi
    RESOLVE_DELIVERY_SKIP=1
  else
    RETRIES=$(get_retry_count "${issue}:resolve")
    if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
      trip_to_blocked "$issue" "resolve" "retry limit of ${MAX_RETRIES} reached for conflict resolution"
      return
    fi
  fi

  PR_NUM=$(get_pr_for_issue "$issue")
  [ -z "$PR_NUM" ] && return

  MERGEABLE=$(check_pr_mergeable "$PR_NUM")
  case "$MERGEABLE" in
    CONFLICTING)
      if [ -n "$RESOLVE_DELIVERY_SKIP" ]; then
        increment_retry "${issue}:resolve:delivery" > /dev/null || true
      else
        increment_retry "${issue}:resolve" || true
      fi
      dispatch "Deconflict issue #${issue}" > /dev/null
      ;;
  esac
}

# W1: substantive failure — unchanged behavior (normal counter increments on dispatch)
_drop_sig 110 resolve "substantive:test_failure:1"
_run_resolve_body 110
assert_eq "W1: normal counter incremented" "1" "$(get_retry_count "110:resolve")"
assert_eq "W1b: dispatched" "1" "$(grep -c 'dispatch Deconflict issue #110' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

# W2: delivery failure under cap — dispatches, shadow counter (not normal) increments
_drop_sig 111 resolve "environmental:delivery_failure"
_run_resolve_body 111
assert_eq "W2: normal counter NOT incremented" "0" "$(get_retry_count "111:resolve")"
assert_eq "W2b: shadow counter incremented to 1" "1" "$(get_retry_count "111:resolve:delivery")"
assert_eq "W2c: dispatched" "1" "$(grep -c 'dispatch Deconflict issue #111' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

# W3: MAX_RETRIES consecutive delivery failures dispatch (each incrementing the shadow
# counter), then the NEXT checkpoint peeks a shadow count already at the ceiling and
# trips without dispatching again — see the plan's "Accepted asymmetry" note for why
# this is MAX_RETRIES dispatches (not MAX_RETRIES-1, unlike refine/plan/implement).
for i in $(seq 1 "$MAX_RETRIES"); do
  _drop_sig 112 resolve "environmental:delivery_failure"
  _run_resolve_body 112
done
assert_eq "W3: shadow counter at MAX_RETRIES after MAX_RETRIES dispatches" \
  "$MAX_RETRIES" "$(get_retry_count "112:resolve:delivery")"
assert_eq "W3b: MAX_RETRIES dispatches occurred" \
  "$MAX_RETRIES" "$(grep -c 'dispatch Deconflict issue #112' "$STUB_LOG" || echo 0)"

_drop_sig 112 resolve "environmental:delivery_failure"
_run_resolve_body 112
assert_eq "W3c: the next checkpoint trips instead of dispatching again" \
  "$MAX_RETRIES" "$(grep -c 'dispatch Deconflict issue #112' "$STUB_LOG" || echo 0)"
assert_eq "W3d: back-fill delegates to breaker-set-retry with the shadow count" \
  "1" "$(grep -c "breaker-set-retry --key 112:resolve --value ${MAX_RETRIES}" "$STUB_LOG" || echo 0)"
assert_eq "W3e: breaker-trip delegated with the delivery-failure reason" \
  "1" "$(grep -c 'breaker-trip --issue 112 --phase resolve' "$STUB_LOG" || echo 0)"
assert_eq "W3f: normal counter reset to 0 after trip" "0" "$(get_retry_count "112:resolve")"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

# W4: no dispatch this cycle (MERGEABLE=UNKNOWN) → neither counter mutates
check_pr_mergeable() { echo "UNKNOWN"; }
export -f check_pr_mergeable
_drop_sig 113 resolve "environmental:delivery_failure"
_run_resolve_body 113
assert_eq "W4: shadow counter untouched when no dispatch occurs" "0" "$(get_retry_count "113:resolve:delivery")"
assert_eq "W4b: no dispatch" "0" "$(grep -c 'dispatch' "$STUB_LOG" || true)"
check_pr_mergeable() { echo "CONFLICTING"; }
export -f check_pr_mergeable

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

# W5: reset_retry clears the resolve shadow counter
_drop_sig 114 resolve "environmental:delivery_failure"
_run_resolve_body 114
assert_eq "W5: shadow counter at 1 before reset" "1" "$(get_retry_count "114:resolve:delivery")"
reset_retry "114:resolve"
assert_eq "W5b: shadow counter cleared" "0" "$(get_retry_count "114:resolve:delivery")"

> "$STUB_LOG"

# W6: dispatch → pause → resume leaves the resolve retry counter net-unchanged (resolve's
# ceiling-check/increment are not adjacent, unlike the other three sites — the rollback
# must still land at the shared checkpoint)
> "$STUB_LOG"; echo '{}' > "$STATE_FILE"
_drop_sig 115 resolve "substantive:test_failure:1"
_run_resolve_body 115
assert_eq "W6a: first (pre-pause) dispatch increments as normal" "1" "$(get_retry_count "115:resolve")"
_drop_sig 115 resolve "environmental:session_window_pause"
_run_resolve_body 115
assert_eq "W6b: rollback + this dispatch's own increment net to no change" "1" "$(get_retry_count "115:resolve")"
assert_eq "W6c: dispatched again" "2" "$(grep -c 'dispatch Deconflict issue #115' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

# W7: clamp at 0 for resolve
_drop_sig 116 resolve "environmental:session_window_pause"
_run_resolve_body 116
assert_eq "W7: counter clamped at 0, not negative, after the new dispatch's own +1" \
  "1" "$(get_retry_count "116:resolve")"

> "$STUB_LOG"

# ==========================================
# X: breaker-evaluate-stop wiring at all four retry sites (#198 R7)
# ==========================================
echo ""
echo "--- X: breaker-evaluate-stop wiring ---"

# X1: stage_blocked_retry (implement) — one evaluate_stop call, trips at ceiling
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
get_pr_for_issue() { echo ""; }
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
export -f get_pr_for_issue dispatch

_run_blocked_retry_ceiling_step() {
  local issue="$1"
  EVAL_RESULT=$(evaluate_stop "$issue" "implement" "$MAX_RETRIES")
  if echo "$EVAL_RESULT" | grep -q "stopped=true"; then
    trip_to_blocked "$issue" "implement" "retry limit of ${MAX_RETRIES} reached"
    return
  fi
  dispatch "Fix issue #${issue}" > /dev/null
}

for i in $(seq 1 "$MAX_RETRIES"); do _run_blocked_retry_ceiling_step 200; done
assert_eq "X1a: three evaluate_stop calls (implement)" \
  "3" "$(grep -c 'breaker-evaluate-stop --issue 200 --phase implement --ceiling 3' "$STUB_LOG" || echo 0)"
assert_eq "X1b: three dispatches, no trip yet" \
  "3" "$(grep -c 'dispatch Fix issue #200' "$STUB_LOG" || echo 0)"
> "$STUB_LOG"
_run_blocked_retry_ceiling_step 200
assert_eq "X1c: 4th call trips via breaker-trip with exact reason text" \
  "1" "$(grep -c 'breaker-trip --issue 200 --phase implement --reason retry limit of 3 reached' "$STUB_LOG" || echo 0)"
assert_eq "X1d: no dispatch on trip" "0" "$(grep -c 'dispatch Fix' "$STUB_LOG")"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

# X2: stage_plan — one evaluate_stop call, trips at REFINE_MAX_RETRIES
_run_plan_ceiling_step() {
  local issue="$1"
  EVAL_RESULT=$(evaluate_stop "$issue" "plan" "$REFINE_MAX_RETRIES")
  if echo "$EVAL_RESULT" | grep -q "stopped=true"; then
    trip_to_blocked "$issue" "plan" "retry limit of ${REFINE_MAX_RETRIES} reached"
    return
  fi
  dispatch "Plan issue #${issue}" > /dev/null
}
for i in $(seq 1 "$REFINE_MAX_RETRIES"); do _run_plan_ceiling_step 201; done
assert_eq "X2a: three evaluate_stop calls (plan)"   "3" "$(grep -c 'breaker-evaluate-stop --issue 201 --phase plan --ceiling 3' "$STUB_LOG" || echo 0)"
> "$STUB_LOG"
_run_plan_ceiling_step 201
assert_eq "X2b: exactly one evaluate_stop call on the tripping step (plan)"   "1" "$(grep -c 'breaker-evaluate-stop --issue 201 --phase plan --ceiling 3' "$STUB_LOG" || echo 0)"
assert_eq "X2: stage_plan trips via breaker-trip with exact reason text" \
  "1" "$(grep -c 'breaker-trip --issue 201 --phase plan --reason retry limit of 3 reached' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

# X3: stage_refine — one evaluate_stop call, trips at REFINE_MAX_RETRIES
_run_refine_ceiling_step() {
  local issue="$1"
  EVAL_RESULT=$(evaluate_stop "$issue" "refine" "$REFINE_MAX_RETRIES")
  if echo "$EVAL_RESULT" | grep -q "stopped=true"; then
    trip_to_blocked "$issue" "refine" "retry limit of ${REFINE_MAX_RETRIES} reached"
    return
  fi
  dispatch "Refine issue #${issue}" > /dev/null
}
for i in $(seq 1 "$REFINE_MAX_RETRIES"); do _run_refine_ceiling_step 202; done
assert_eq "X3a: three evaluate_stop calls (refine)"   "3" "$(grep -c 'breaker-evaluate-stop --issue 202 --phase refine --ceiling 3' "$STUB_LOG" || echo 0)"
> "$STUB_LOG"
_run_refine_ceiling_step 202
assert_eq "X3b: exactly one evaluate_stop call on the tripping step (refine)"   "1" "$(grep -c 'breaker-evaluate-stop --issue 202 --phase refine --ceiling 3' "$STUB_LOG" || echo 0)"
assert_eq "X3: stage_refine trips via breaker-trip with exact reason text" \
  "1" "$(grep -c 'breaker-trip --issue 202 --phase refine --reason retry limit of 3 reached' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

# X4: stage_conflict_resolve — peek mode: evaluate_stop called with --peek, no increment
# until the (untouched) CONFLICTING-branch increment_retry fires.
check_pr_mergeable() { echo "CONFLICTING"; }
get_pr_for_issue() { echo "500"; }
export -f check_pr_mergeable get_pr_for_issue

_run_resolve_ceiling_step() {
  local issue="$1"
  EVAL_RESULT=$(evaluate_stop "$issue" "resolve" "$MAX_RETRIES" --peek)
  if echo "$EVAL_RESULT" | grep -q "stopped=true"; then
    trip_to_blocked "$issue" "resolve" "retry limit of ${MAX_RETRIES} reached for conflict resolution"
    return
  fi
  PR_NUM=$(get_pr_for_issue "$issue")
  MERGEABLE=$(check_pr_mergeable "$PR_NUM")
  if [ "$MERGEABLE" = "CONFLICTING" ]; then
    increment_retry "${issue}:resolve" || true
    dispatch "Deconflict issue #${issue}" > /dev/null
  fi
}
for i in $(seq 1 "$MAX_RETRIES"); do _run_resolve_ceiling_step 203; done
assert_eq "X4a: three --peek evaluate_stop calls (resolve)" \
  "3" "$(grep -c 'breaker-evaluate-stop --issue 203 --phase resolve --ceiling 3 --peek' "$STUB_LOG" || echo 0)"
assert_eq "X4b: normal counter incremented by the unchanged CONFLICTING-branch call, not by peek" \
  "3" "$(get_retry_count "203:resolve")"
> "$STUB_LOG"
_run_resolve_ceiling_step 203
assert_eq "X4c: 4th call trips via breaker-trip with exact resolve reason text" \
  "1" "$(grep -c 'breaker-trip --issue 203 --phase resolve --reason retry limit of 3 reached for conflict resolution' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

# Restore stubs to section defaults
get_pr_for_issue() { echo ""; }
check_pr_mergeable() { echo "UNKNOWN"; }
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
export -f get_pr_for_issue check_pr_mergeable dispatch

# Static drift lock, mirroring the existing #341 precedent at l.1815
# (rollback_paused_retry wired 4x) — nothing else currently detects divergence
# between Task 11/12's real scheduler.sh edits and this section's hand-copied
# post-refactor bodies, and the R7 parity claim rests entirely on those copies
# staying in sync with the real call sites.
assert_eq "evaluate_stop wired 4x in scheduler.sh" "4" "$(grep -c 'evaluate_stop "\$ISSUE"' "$SCHED")"

# ==========================================
# Y: branch_exists_for_issue — helper-level git ls-remote probe (#371)
# ==========================================
echo ""
echo "--- Y: branch_exists_for_issue — git ls-remote probe ---"

# Y1: git prints a matching ref -> helper echoes the ref; the logged argv is captured
# via the subprocess PATH-shim (SHIM_LOG), not STUB_LOG — `timeout` execs a real `git`
# binary, not a bash function, so only a re-entrant PATH shim script (not an exported
# bash function) is visible to it. The URL embeds a fake token to prove requirement 6
# (never leaked) without touching a real credential.
# Section N (its --id-routing python3 override, and the N20 variant it leaves behind) permanently overrides the `python3` stub with its own
# --id-routing case and never restores the generic PROVIDERS_CLI_OUTPUT-echoing form —
# reset_python3_stub() only clears the variable, not the function body. Redefine the
# generic stub here so PROVIDERS_CLI_OUTPUT is honored again for this section.
python3() {
  echo "python3 $*" >> "$STUB_LOG"
  case "$*" in
    *providers/cli.py*) [ -n "$PROVIDERS_CLI_OUTPUT" ] && printf '%s\n' "$PROVIDERS_CLI_OUTPUT"; return 0 ;;
    *) "$_REAL_PY3" "$@" ;;
  esac
}
export -f python3
PROVIDERS_CLI_OUTPUT="https://x-access-token:ghs_zzfaketoken371@github.com/omniscient/dark-factory.git"
git() {
  echo "git $*" >> "$STUB_LOG"
  printf 'deadbeefcafefeed\trefs/heads/feat/issue-371-x\n'
  return 0
}
export -f git
> "$STUB_LOG"
Y1_OUT=$(branch_exists_for_issue 371)
assert_eq "Y1: helper echoes the matched ref" "refs/heads/feat/issue-371-x" "$Y1_OUT"
assert_eq "Y1b: git invoked with the expected ls-remote argv (via SHIM_LOG, not STUB_LOG)" \
  "1" "$(grep -c '^git ls-remote --heads https://x-access-token:ghs_zzfaketoken371@github.com/omniscient/dark-factory.git refs/heads/feat/issue-371-\*$' "$SHIM_LOG" || echo 0)"

# Y2: git ls-remote exits non-zero (transport error) -> helper echoes empty, no crash
git() { echo "git $*" >> "$STUB_LOG"; return 128; }
export -f git
Y2_OUT=$(branch_exists_for_issue 371)
assert_eq "Y2: git ls-remote failure -> empty" "" "$Y2_OUT"

# Y3: codehost remote-url itself returns empty (e.g. GH_TOKEN missing) -> empty, and git
# is never invoked at all (checked via a SHIM_LOG line-count delta, since SHIM_LOG is a
# suite-wide accumulating log with no reset hook).
PROVIDERS_CLI_OUTPUT=""
git() { echo "git $*" >> "$STUB_LOG"; return 0; }
export -f git
Y3_SHIM_BEFORE=$(wc -l < "$SHIM_LOG")
Y3_OUT=$(branch_exists_for_issue 371)
Y3_SHIM_AFTER=$(wc -l < "$SHIM_LOG")
assert_eq "Y3: empty remote-url -> empty" "" "$Y3_OUT"
assert_eq "Y3b: git never called when remote-url is empty" "$Y3_SHIM_BEFORE" "$Y3_SHIM_AFTER"

reset_python3_stub
git() { echo "git $*" >> "$STUB_LOG"; return 0; }
export -f git
> "$STUB_LOG"

# ==========================================
# Cleanup
# ==========================================
rm -f "$STATE_FILE" "$STUB_LOG"
rm -rf "$SCHEDULER_STATE_DIR"
echo ""
echo "--- #341 drift lock: rollback_paused_retry is wired at exactly the four spec sites (refine, plan, blocked_retry, conflict_resolve) ---"
assert_eq "rollback_paused_retry wired 4x in scheduler.sh" "4" "$(grep -c 'rollback_paused_retry "\$ISSUE"' "$SCHED")"

echo "--- Subprocess stub shim: gh spawned from a real python child must hit the stub, not the real binary ---"
_SHIM_OUT=$("$_REAL_PY3" -c 'import subprocess; r = subprocess.run(["gh", "project", "item-list", "1", "--format", "json"], capture_output=True, text=True); print(r.returncode, r.stdout.strip())')
assert_eq "python-spawned gh returns the stub's rc 0 and no output" "0 " "$_SHIM_OUT"
assert_eq "python-spawned gh call landed in SHIM_LOG" "1" "$(grep -c '^gh project item-list 1 --format json$' "$SHIM_LOG")"

echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
