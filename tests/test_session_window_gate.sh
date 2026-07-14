#!/usr/bin/env bash
# Verifies the session-window-paused sentinel (#35) gates all five dispatch priority
# blocks (resolve, implement/ready, implement/blocked-retry, plan, refine) and the
# autopilot check, and self-clears once its embedded epoch passes. Mirrors
# test_scheduler_main_red_fixer.sh / test_dispatch_ceiling.sh style: static wiring
# checks (the main loop can't be executed under test) plus a real-behavior check of
# the sentinel read/self-clear snippet in isolation.
# Run: bash tests/test_session_window_gate.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCHED="$SCRIPT_DIR/../scheduler.sh"

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

echo "--- A: static gate wiring ---"
grep -q 'SCHEDULER_STATE_DIR}/session-window-paused' "$SCHED" \
  || { echo "FAIL: no session-window-paused sentinel read"; exit 1; }
echo "  PASS: sentinel read present"; PASSED=$((PASSED+1))

for hdr in "Priority 1.5:" "Priority 2:" "Priority 3:" "Priority 4:" "Priority 5:"; do
  block="$(awk -v h="$hdr" 'index($0,h){f=1} f{print} f&&/^  fi$/{exit}' "$SCHED")"
  if echo "$block" | grep -q 'SESSION_WINDOW_PAUSED'; then
    echo "  PASS: '$hdr' block gated by SESSION_WINDOW_PAUSED"; PASSED=$((PASSED+1))
  else
    echo "  FAIL: '$hdr' block missing SESSION_WINDOW_PAUSED gate" >&2; FAILED=$((FAILED+1))
  fi
done

block6="$(awk '/Priority 6: Epic Autopilot/{f=1} f{print} f&&/^  fi$/{exit}' "$SCHED")"
if echo "$block6" | grep -q 'SESSION_WINDOW_PAUSED.*false'; then
  echo "  PASS: autopilot guarded by session-window-green"; PASSED=$((PASSED+1))
else
  echo "  FAIL: autopilot not guarded by session-window-green" >&2; FAILED=$((FAILED+1))
fi

# Structural proxy for "no breaker call site reachable while paused": the gate check
# must appear before any get_retry_count/increment_retry call within each block. Only
# blocks that actually call the breaker qualify — Priority 2 (Ready/implement) dispatches
# "Fix" directly with no get_retry_count/increment_retry call, so it is excluded here
# (its SESSION_WINDOW_PAUSED gate is still covered by the wiring loop above); Priority 1.5
# (resolve) does call the breaker (get_retry_count/increment_retry on the ":resolve" key)
# and is included instead. These 4 are exactly the spec's "4 call sites."
for hdr in "Priority 1.5:" "Priority 3:" "Priority 4:" "Priority 5:"; do
  block="$(awk -v h="$hdr" 'index($0,h){f=1} f{print} f&&/^  fi$/{exit}' "$SCHED")"
  gate_ln=$(echo "$block" | grep -n 'SESSION_WINDOW_PAUSED' | head -1 | cut -d: -f1)
  retry_ln=$(echo "$block" | grep -n 'get_retry_count\|increment_retry' | head -1 | cut -d: -f1)
  if [ -n "$gate_ln" ] && [ -n "$retry_ln" ] && [ "$gate_ln" -lt "$retry_ln" ]; then
    echo "  PASS: '$hdr' gate precedes breaker call sites"; PASSED=$((PASSED+1))
  else
    echo "  FAIL: '$hdr' gate ($gate_ln) does not precede breaker calls ($retry_ln)" >&2
    FAILED=$((FAILED+1))
  fi
done

echo ""
echo "--- B: sentinel self-clear behavior ---"
# Mirrors the snippet inserted after the MAIN_IS_RED read in scheduler.sh — exercised
# directly since it lives inside the un-executable main poll loop.
# NOTE: this mirror uses RESUME_EPOCH as a local var name; the real inserted code in
# Step 5.3.1 uses SW_RESUME_EPOCH (namespaced to avoid colliding with other scheduler.sh
# vars). This tests a faithful copy of the logic, not the shipped line — the same
# tradeoff test_scheduler_main_red_fixer.sh accepts for other un-executable main-loop
# code. If Step 5.3.1's snippet changes, update this mirror to match.
sw_gate_check() {
  SESSION_WINDOW_PAUSED=false
  if [ -f "${SCHEDULER_STATE_DIR}/session-window-paused" ]; then
    RESUME_EPOCH=$(cat "${SCHEDULER_STATE_DIR}/session-window-paused" 2>/dev/null || echo 0)
    if [ "$(date +%s)" -lt "${RESUME_EPOCH:-0}" ]; then
      SESSION_WINDOW_PAUSED=true
    else
      rm -f "${SCHEDULER_STATE_DIR}/session-window-paused"
    fi
  fi
}

SCHEDULER_STATE_DIR=$(mktemp -d /tmp/sched-sw-statedir-XXXXXX)

sw_gate_check
assert_eq "no sentinel → not paused" "false" "$SESSION_WINDOW_PAUSED"

FUTURE=$(( $(date +%s) + 3600 ))
echo "$FUTURE" > "${SCHEDULER_STATE_DIR}/session-window-paused"
sw_gate_check
assert_eq "future epoch → paused" "true" "$SESSION_WINDOW_PAUSED"
assert_true "sentinel kept while future" "[ -f '${SCHEDULER_STATE_DIR}/session-window-paused' ]"

PAST=$(( $(date +%s) - 10 ))
echo "$PAST" > "${SCHEDULER_STATE_DIR}/session-window-paused"
sw_gate_check
assert_eq "past epoch → resumed" "false" "$SESSION_WINDOW_PAUSED"
assert_true "sentinel removed once expired" "[ ! -f '${SCHEDULER_STATE_DIR}/session-window-paused' ]"

rm -rf "$SCHEDULER_STATE_DIR"

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
