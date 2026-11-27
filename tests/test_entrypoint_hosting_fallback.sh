#!/usr/bin/env bash
# Verifies _cli_with_hosting_fallback (#633): on a verified-local run, a clone cli.py that
# rejects --hosting-location (exit 2) is retried through the baked
# /opt/dark-factory/scripts/factory_core/cli.py with the identical argv, flag included — never
# without it. Any other exit status is returned as is. A cloud run never uses the helper, and
# its argv is unchanged.
# Run: bash tests/test_entrypoint_hosting_fallback.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export IDENTITY_SH="$REPO_ROOT/scripts/identity.sh"
export FACTORY_PROVIDERS_CLI="$REPO_ROOT/scripts/factory_core/providers/cli.py"
export GH_TOKEN="stub-token" CLAUDE_CODE_OAUTH_TOKEN="stub-token"

git() {
  case "$1" in
    status) [ "${STUB_DIRTY:-false}" = "true" ] && echo " M some/file.py" ;;
  esac
  return 0
}
gh() { echo "stub"; return 0; }
docker() { return 0; }
claude() { echo "stub"; return 0; }
export -f git gh docker claude

WORK=$(mktemp -d /tmp/ep-hf-XXXXXX)
# #362: both before the source line (tests/test_run_record_hermetic.sh).
CURRENT_RUN_DIR="$WORK/run"
export CURRENT_RUN_DIR
SCHEDULER_STATE_DIR="$WORK/state"
export SCHEDULER_STATE_DIR
export HOME="$WORK/home"
mkdir -p "$CURRENT_RUN_DIR" "$SCHEDULER_STATE_DIR" "$HOME"

ENTRYPOINT_SOURCE_ONLY=1 source "$REPO_ROOT/entrypoint.sh"
trap - ERR
set +e; set +u; set +o pipefail

# The clone's cli.py is a stub that records its argv. HF_CLONE_MODE: reject (exit 2 on
# --hosting-location, argparse's usage text on stderr), exit2 (exit 2 on any argv), fail
# (exit 1), accept (exit 0).
CLONE_DIR="$WORK/clone"
mkdir -p "$CLONE_DIR/dark-factory/scripts/factory_core"
export HF_ARGV_LOG="$WORK/argv.log"
cat > "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" <<'PY'
import os, sys
with open(os.environ["HF_ARGV_LOG"], "a") as f:
    f.write("clone " + " ".join(sys.argv[1:]) + "\n")
mode = os.environ.get("HF_CLONE_MODE", "reject")
if mode == "reject" and "--hosting-location" in sys.argv:
    print("usage: cli.py [-h] ...", file=sys.stderr)
    print("cli.py: error: unrecognized arguments: --hosting-location local", file=sys.stderr)
    sys.exit(2)
if mode == "exit2":
    sys.exit(2)
if mode == "fail":
    print("clone failure", file=sys.stderr)
    sys.exit(1)
print("clone ran")
PY
# The baked copy does not exist on a CI runner: route that one path to this checkout's real
# cli.py and record its argv.
python3() {
  if [ "${1:-}" = /opt/dark-factory/scripts/factory_core/cli.py ]; then
    shift
    echo "baked $*" >> "$HF_ARGV_LOG"
    command python3 "$REPO_ROOT/scripts/factory_core/cli.py" "$@"
    return
  fi
  command python3 "$@"
}

PASSED=0; FAILED=0
assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else echo "  FAIL: $desc — expected='$expected' got='$actual'" >&2; FAILED=$((FAILED+1)); fi
}
reset() { : > "$HF_ARGV_LOG"; rm -rf "$SCHEDULER_STATE_DIR"; mkdir -p "$SCHEDULER_STATE_DIR"; }

ISSUE_NUM=633
RUN_STARTED_AT=""   # elapsed and commits both 0: a deterministic argv
TEXT="$WORK/out.txt"
echo "HTTP 429 Too Many Requests" > "$TEXT"

echo "--- A: local, clone rejects the flag: error-signature-write falls back, flag intact ---"
reset; export HF_CLONE_MODE=reject; DF_ENDPOINT_HOSTING=local
_write_error_signature plan 1 "$TEXT"
SIG="$SCHEDULER_STATE_DIR/error-signatures/633.plan.sig"
assert_eq "signature written through the baked CLI" "substantive:endpoint_unreachable:1" \
  "$(command python3 -c "import json; print(json.load(open('$SIG'))['signature'])" 2>/dev/null)"
assert_eq "two attempts: the clone's, then the baked copy's" "clone baked" \
  "$(cut -d' ' -f1 "$HF_ARGV_LOG" | tr '\n' ' ' | sed 's/ $//')"
assert_eq "every attempt carries --hosting-location local" "2" \
  "$(grep -c -- '--hosting-location local$' "$HF_ARGV_LOG")"
assert_eq "both attempts have the identical argv" "1" \
  "$(cut -d' ' -f2- "$HF_ARGV_LOG" | sort -u | wc -l | tr -d ' ')"

echo ""
echo "--- B: local, clone rejects the flag: session-window-check falls back, no pause ---"
reset; DF_ENDPOINT_HOSTING=local SESSION_WINDOW_BACKOFF_ENABLED=true
TMP_OUT="$WORK/sw.out"
printf "You've hit your session limit · resets 11:10pm (UTC)\n" > "$TMP_OUT"
_handle_session_window_pause "$TMP_OUT" 2>"$WORK/sw.err"
assert_eq "returns 1: falls through to the failure path" "1" "$?"
assert_eq "no session-window-check failure warning" "0" \
  "$(grep -c 'session-window-check failed' "$WORK/sw.err")"
assert_eq "no sentinel written" "no" \
  "$([ -f "$SCHEDULER_STATE_DIR/session-window-paused" ] && echo yes || echo no)"
assert_eq "clone, then baked, both with the flag" "2" \
  "$(grep -c -- '--hosting-location local$' "$HF_ARGV_LOG")"
reset
OUT=$(_cli_with_hosting_fallback session-window-check --tmp-out "$TMP_OUT" \
  --state-dir "$SCHEDULER_STATE_DIR" --buffer-minutes 5 --fallback-minutes 30 \
  --hosting-location local)
assert_eq "helper returns the baked copy's status" "0" "$?"
assert_eq "helper prints the baked copy's output" "1" "$(grep -c '^matched=false' <<<"$OUT")"
assert_eq "the rejected attempt's usage text is dropped" "0" "$(grep -c 'unrecognized arguments' <<<"$OUT")"

echo ""
echo "--- C: local, clone fails for another reason: no fallback ---"
reset; export HF_CLONE_MODE=fail
OUT=$(_cli_with_hosting_fallback error-signature-write --hosting-location local)
assert_eq "exit 1 is returned as is" "1" "$?"
assert_eq "the clone's own output is printed" "clone failure" "$OUT"
assert_eq "only the clone's copy ran" "clone" "$(cut -d' ' -f1 "$HF_ARGV_LOG" | tr '\n' ' ' | sed 's/ $//')"

echo ""
echo "--- D: local, the clone has no cli.py at all: falls back (python3 exits 2) ---"
reset; mv "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" "$WORK/cli.py.away"
_write_error_signature plan 1 "$TEXT"
assert_eq "signature written through the baked CLI" "substantive:endpoint_unreachable:1" \
  "$(command python3 -c "import json; print(json.load(open('$SIG'))['signature'])" 2>/dev/null)"
assert_eq "baked copy ran with the flag" "1" "$(grep -c -- '^baked .*--hosting-location local$' "$HF_ARGV_LOG")"
mv "$WORK/cli.py.away" "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py"

echo ""
echo "--- E: cloud: the helper is not used and the argv is unchanged ---"
reset; export HF_CLONE_MODE=accept; DF_ENDPOINT_HOSTING=cloud
STUB_DIRTY=true _write_error_signature plan 1 "$TEXT"
assert_eq "error-signature-write: one call, the clone's, today's argv" \
  "clone error-signature-write --issue 633 --phase plan --exit-code 1 --text-file $TEXT --elapsed-seconds 0 --commits-since-start 0 --worktree-dirty --delivery-failure-max-seconds 30 --state-dir $SCHEDULER_STATE_DIR" \
  "$(cat "$HF_ARGV_LOG")"
reset
_handle_session_window_pause "$TMP_OUT" 2>/dev/null
assert_eq "session-window-check: one call, the clone's, today's argv" \
  "clone session-window-check --tmp-out $TMP_OUT --state-dir $SCHEDULER_STATE_DIR --buffer-minutes 5 --fallback-minutes 30" \
  "$(cat "$HF_ARGV_LOG")"
reset; export HF_CLONE_MODE=exit2
_write_error_signature plan 1 "$TEXT"
assert_eq "cloud never falls back, even if the clone's copy exits 2" "clone" \
  "$(cut -d' ' -f1 "$HF_ARGV_LOG" | tr '\n' ' ' | sed 's/ $//')"

rm -rf "$WORK"
echo ""
echo "Results: $PASSED passed, $FAILED failed"
[ "$FAILED" -eq 0 ]
