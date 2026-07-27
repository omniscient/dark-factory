#!/usr/bin/env bash
# Regression test for issue #332: smoke gate must not increment per-ticket counters on red main.
# Run: bash tests/test_smoke_gate.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Isolated temp state dir shared across subshells via filesystem
SMOKE_STATE_DIR=$(mktemp -d /tmp/smoke-state-XXXXXX)
SCHEDULER_STATE_DIR="$SMOKE_STATE_DIR"
export SMOKE_STATE_DIR SCHEDULER_STATE_DIR

# Fake clone dir: smoke_gate.sh uses ${CLONE_DIR}/frontend and ${CLONE_DIR}/backend
CLONE_DIR=$(mktemp -d /tmp/smoke-clone-XXXXXX)
mkdir -p "$CLONE_DIR/frontend" "$CLONE_DIR/backend"
export CLONE_DIR OWNER="omniscient"

STUB_LOG=$(mktemp /tmp/smoke-stubs-XXXXXX.log)
export STUB_LOG
# 0=pass 1=fail; subshells inherit these as exported vars
TSC_FAIL=0
PY_FAIL=0
export TSC_FAIL PY_FAIL

# Source smoke_gate.sh to define functions without auto-executing.
# Will fail here (file not yet created) — that is the expected TDD failure.
SMOKE_GATE_SOURCE_ONLY=1 source "$SCRIPT_DIR/../smoke_gate.sh"

# Stubs — defined after source so they override any real definitions.
# Subshells created with ( ) inherit functions from the parent.
# shellcheck disable=SC2317
npx() { echo "npx $*" >> "$STUB_LOG"; return "$TSC_FAIL"; }
# shellcheck disable=SC2317
python() {
  echo "python $*" >> "$STUB_LOG"
  if echo "$*" | grep -q "import app"; then
    # Mimic import-time Settings() (#190/#365): app.main cannot even import
    # unless the harness supplies the required env vars (JWT >= 32 chars) —
    # regardless of whether the code itself is green.
    local jwt="${JWT_SECRET_KEY:-}"
    if [ -z "${DATABASE_URL:-}" ] || [ -z "${POLYGON_API_KEY:-}" ] || [ "${#jwt}" -lt 32 ]; then
      echo "python_import_env_missing" >> "$STUB_LOG"
      return 1
    fi
    return "$PY_FAIL"
  fi
  return 0
}
# shellcheck disable=SC2317
# tracker create (the regression-ticket creation, issue #249) is stubbed here rather
# than let through to the real providers CLI, since that CLI would shell out to a real
# `gh` binary directly (bash function stubs are invisible to a Python subprocess).
python3() {
  echo "python3 $*" >> "$STUB_LOG"
  if echo "$*" | grep -q "tracker create"; then
    echo "999"
    return 0
  fi
  python "$@"
}
# GH_LIST_OUTPUT: space-separated issue numbers the stub emits (one per line) for
# `gh issue list` — emulates _smoke_list_open_red_issues's post---jq output.
GH_LIST_OUTPUT=""
export GH_LIST_OUTPUT
# shellcheck disable=SC2317
gh() {
  echo "gh $*" >> "$STUB_LOG"
  if echo "$*" | grep -q "issue list"; then
    # shellcheck disable=SC2086
    [ -n "${GH_LIST_OUTPUT:-}" ] && printf '%s\n' $GH_LIST_OUTPUT
  fi
  return 0
}
# Functions that must NOT be called on red main
# shellcheck disable=SC2317
increment_retry()  { echo "increment_retry $*"  >> "$STUB_LOG"; }
# shellcheck disable=SC2317
trip_to_blocked()  { echo "trip_to_blocked $*"  >> "$STUB_LOG"; }
# shellcheck disable=SC2317
set_board_status() { echo "set_board_status $*" >> "$STUB_LOG"; }

PASSED=0; FAILED=0
assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else
    echo "  FAIL: $desc — expected='$expected' got='$actual'" >&2; FAILED=$((FAILED+1))
  fi
}
assert_file_exists() {
  if [ -f "$2" ]; then echo "  PASS: $1"; PASSED=$((PASSED+1))
  else echo "  FAIL: $1 — file absent: $2" >&2; FAILED=$((FAILED+1)); fi
}
assert_file_absent() {
  if [ ! -f "$2" ]; then echo "  PASS: $1"; PASSED=$((PASSED+1))
  else echo "  FAIL: $1 — file unexpectedly exists: $2" >&2; FAILED=$((FAILED+1)); fi
}

echo "=== Smoke Gate Regression Test (#332) ==="

# ---- Phase 1: Red main ----
echo ""
echo "--- Phase 1: Red main (tsc fails) ---"
TSC_FAIL=1; PY_FAIL=0; export TSC_FAIL PY_FAIL
> "$STUB_LOG"
rm -f "${SMOKE_STATE_DIR}/main-is-red" "${SMOKE_STATE_DIR}/main-is-red-issue"

# run_smoke_gate exits 0 on red; run in subshell so test continues after exit 0
(run_smoke_gate) || true

assert_file_exists "sentinel file created" "${SMOKE_STATE_DIR}/main-is-red"
assert_file_exists "issue number file created" "${SMOKE_STATE_DIR}/main-is-red-issue"
assert_file_exists "recheck throttle stamp created on red (#365)" "${SMOKE_STATE_DIR}/main-red-last-recheck"
PY_CREATES=$(grep -c "python3.*tracker create" "$STUB_LOG" 2>/dev/null || true)
assert_eq "tracker create called once on first red" "1" "$PY_CREATES"

# Idempotency: second gate pass on same red main → update comment, not a second create
(run_smoke_gate) || true
PY_CREATES2=$(grep -c "python3.*tracker create" "$STUB_LOG" 2>/dev/null || true)
assert_eq "idempotency: only one tracker create after two red passes" "1" "$PY_CREATES2"
GH_COMMENTS=$(grep -c "gh.*issue comment" "$STUB_LOG" 2>/dev/null || true)
assert_eq "idempotency: update comment posted on second red pass" "1" "$GH_COMMENTS"

# Per-ticket blast radius: no retry/block/board calls for any ticket
BLAST_CALLS=$(grep -cE "increment_retry|trip_to_blocked|set_board_status Blocked|needs-discussion" \
              "$STUB_LOG" 2>/dev/null || true)
assert_eq "no per-ticket retry/block/board calls on red main" "0" "$BLAST_CALLS"

# ---- Phase 2: Green main after red ----
echo ""
echo "--- Phase 2: Green main after red (sentinel cleanup) ---"
TSC_FAIL=0; PY_FAIL=0; export TSC_FAIL PY_FAIL
> "$STUB_LOG"
# Sentinel and issue file from Phase 1 are still present — gate must clean them up

GATE_RC=0
(run_smoke_gate) || GATE_RC=$?
assert_eq "gate exits/returns 0 on green" "0" "$GATE_RC"
assert_file_absent "sentinel removed on green" "${SMOKE_STATE_DIR}/main-is-red"
assert_file_absent "issue number file removed on green" "${SMOKE_STATE_DIR}/main-is-red-issue"
assert_file_absent "recheck throttle stamp removed on green (#365)" "${SMOKE_STATE_DIR}/main-red-last-recheck"
GH_CLOSES=$(grep -c "gh.*issue close" "$STUB_LOG" 2>/dev/null || true)
assert_eq "gh issue close called once" "1" "$GH_CLOSES"

# ---- Phase 3: Intent guard ----
echo ""
echo "--- Phase 3: Intent guard — smoke gate skipped for refine/plan/close ---"
TSC_FAIL=1; export TSC_FAIL
> "$STUB_LOG"
rm -f "${SMOKE_STATE_DIR}/main-is-red" "${SMOKE_STATE_DIR}/main-is-red-issue"

# Mirror the entrypoint.sh intent guard: only fix/continue/deconflict/recheck trigger the gate
for INTENT in refine plan close; do
  export INTENT
  if [ "$INTENT" = "fix" ] || [ "$INTENT" = "continue" ] || [ "$INTENT" = "deconflict" ] || [ "$INTENT" = "recheck" ]; then
    (run_smoke_gate) || true
  fi
done

TSC_CALLS=$(grep -cE "npx|tsc" "$STUB_LOG" 2>/dev/null || true)
assert_eq "no tsc calls for refine/plan/close" "0" "$TSC_CALLS"
assert_file_absent "no sentinel created for skip intents" "${SMOKE_STATE_DIR}/main-is-red"

# ---- Phase 4: Recheck intent (#365) — runs the gate and clears red state on green ----
echo ""
echo "--- Phase 4: Recheck intent clears latched red state on green ---"
TSC_FAIL=0; PY_FAIL=0; export TSC_FAIL PY_FAIL
> "$STUB_LOG"
touch "${SMOKE_STATE_DIR}/main-is-red" "${SMOKE_STATE_DIR}/main-red-last-recheck"
echo "999" > "${SMOKE_STATE_DIR}/main-is-red-issue"

INTENT=recheck; export INTENT
if [ "$INTENT" = "fix" ] || [ "$INTENT" = "continue" ] || [ "$INTENT" = "deconflict" ] || [ "$INTENT" = "recheck" ]; then
  (run_smoke_gate) || true
fi

TSC_CALLS4=$(grep -c "npx" "$STUB_LOG" 2>/dev/null || true)
assert_eq "recheck intent runs the gate (tsc called)" "1" "$TSC_CALLS4"
assert_file_absent "sentinel removed by green recheck" "${SMOKE_STATE_DIR}/main-is-red"
assert_file_absent "throttle stamp removed by green recheck" "${SMOKE_STATE_DIR}/main-red-last-recheck"
GH_CLOSES4=$(grep -c "gh.*issue close" "$STUB_LOG" 2>/dev/null || true)
assert_eq "regression ticket closed by green recheck" "1" "$GH_CLOSES4"

# ---- Phase 5: Green sweep — orphaned tickets closed with NO local state ----
echo ""
echo "--- Phase 5: Green sweep closes orphaned open tickets despite lost state files ---"
TSC_FAIL=0; PY_FAIL=0; export TSC_FAIL PY_FAIL
> "$STUB_LOG"
rm -f "${SMOKE_STATE_DIR}/main-is-red" "${SMOKE_STATE_DIR}/main-is-red-issue" \
      "${SMOKE_STATE_DIR}/main-red-last-recheck"
GH_LIST_OUTPUT="264 283 297"; export GH_LIST_OUTPUT
INTENT=fix; export INTENT

(run_smoke_gate) || true

GH_CLOSES5=$(grep -c "gh issue close" "$STUB_LOG" 2>/dev/null || true)
assert_eq "green sweep closes ALL open marker tickets (no state file)" "3" "$GH_CLOSES5"
for N in 264 283 297; do
  CLOSED_N=$(grep -c "gh issue close $N " "$STUB_LOG" 2>/dev/null || true)
  assert_eq "green sweep closed #$N" "1" "$CLOSED_N"
done

# ---- Phase 6: Red dedup — adopt oldest open ticket, close duplicates, no create ----
echo ""
echo "--- Phase 6: Red with lost state file adopts oldest open ticket and closes dupes ---"
TSC_FAIL=1; PY_FAIL=0; export TSC_FAIL PY_FAIL
> "$STUB_LOG"
rm -f "${SMOKE_STATE_DIR}/main-is-red" "${SMOKE_STATE_DIR}/main-is-red-issue"
GH_LIST_OUTPUT="264 283 297"; export GH_LIST_OUTPUT

(run_smoke_gate) || true

PY_CREATES6=$(grep -c "python3.*tracker create" "$STUB_LOG" 2>/dev/null || true)
assert_eq "no new ticket created while one is already open" "0" "$PY_CREATES6"
ADOPT_COMMENT=$(grep -c "gh issue comment 264 " "$STUB_LOG" 2>/dev/null || true)
assert_eq "still-red comment lands on the adopted (oldest) ticket #264" "1" "$ADOPT_COMMENT"
DUP_CLOSES=$(grep -c "gh issue close" "$STUB_LOG" 2>/dev/null || true)
assert_eq "duplicate tickets closed on red (283, 297)" "2" "$DUP_CLOSES"
CANON_CLOSED=$(grep -c "gh issue close 264 " "$STUB_LOG" 2>/dev/null || true)
assert_eq "adopted canonical ticket NOT closed" "0" "$CANON_CLOSED"
assert_eq "state file re-seeded with adopted ticket" "264" "$(cat "${SMOKE_STATE_DIR}/main-is-red-issue" 2>/dev/null)"

GH_LIST_OUTPUT=""; export GH_LIST_OUTPUT

# ---- Summary ----
echo ""
echo "Results: $PASSED passed, $FAILED failed"
rm -rf "$SMOKE_STATE_DIR" "$CLONE_DIR" "$STUB_LOG"
[ "$FAILED" -eq 0 ] || exit 1
