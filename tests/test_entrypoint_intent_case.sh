#!/usr/bin/env bash
# Verifies entrypoint.sh's explicit command → INTENT case (#633): every command string the
# scheduler issues keeps its intent; an unrecognized command, and the two #587 strings that
# are recognized but not yet available, exit non-zero before any clone, tracker or model call;
# nothing inherited from the environment changes the intent; and both pre-clone provider-CLI
# calls receive the derived intent as --intent.
# Run: bash tests/test_entrypoint_intent_case.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EP="$REPO_ROOT/entrypoint.sh"

WORK=$(mktemp -d /tmp/ep-intent-XXXXXX)
# #362: CURRENT_RUN_DIR is exported before entrypoint.sh is ever sourced
# (tests/test_run_record_hermetic.sh).
CURRENT_RUN_DIR="$WORK/run"
export CURRENT_RUN_DIR
SCHEDULER_STATE_DIR="$WORK/state"
export SCHEDULER_STATE_DIR
mkdir -p "$CURRENT_RUN_DIR" "$SCHEDULER_STATE_DIR" "$WORK/home"
export HOME="$WORK/home" FACTORY_CLONE_DIR="$WORK/clone"
export IDENTITY_SH="$REPO_ROOT/scripts/identity.sh"
export GH_TOKEN="stub-token" CLAUDE_CODE_OAUTH_TOKEN="stub-token"
export CALLS_LOG="$WORK/calls.log"
# Hermetic inside the factory image too: a sourced "Fix issue #N" moves the board through the
# baked providers CLI (set_board_status), which exists there. An unknown tracker makes that
# call fail before any network request.
export FACTORY_TRACKER="none-633"

# Every external a run could reach records its call. The providers CLI is a stub: it logs
# its argv and answers the hosting verb, so no registry, tracker or network is touched.
cat > "$WORK/providers_cli.py" <<'PY'
import os, sys
with open(os.environ["CALLS_LOG"], "a") as f:
    f.write("providers " + " ".join(sys.argv[1:]) + "\n")
if sys.argv[1:3] == ["model-endpoints", "hosting"]:
    print("hosting=cloud")
PY
export FACTORY_PROVIDERS_CLI="$WORK/providers_cli.py"
git() { echo "git $*" >> "$CALLS_LOG"; return 0; }
gh() { echo "gh $*" >> "$CALLS_LOG"; return 0; }
docker() { echo "docker $*" >> "$CALLS_LOG"; return 0; }
claude() { echo "claude $*" >> "$CALLS_LOG"; return 0; }
archon() { echo "archon $*" >> "$CALLS_LOG"; return 0; }
export -f git gh docker claude archon

PASSED=0; FAILED=0
assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else echo "  FAIL: $desc — expected='$expected' got='$actual'" >&2; FAILED=$((FAILED+1)); fi
}

# _source_intent <command> [NAME=VALUE ...]: sources entrypoint.sh with <command> as its only
# argument in a child shell (ENTRYPOINT_SOURCE_ONLY=1, so it returns before the clone) and
# prints the INTENT it derived. The NAME=VALUE pairs are placed in that child's environment.
# Sets SRC_RC; the sourced file's stderr is in $WORK/src.err.
_source_intent() {
  local cmd="$1"; shift
  : > "$CALLS_LOG"
  SRC_OUT=$(env "$@" ENTRYPOINT_SOURCE_ONLY=1 bash -c \
    'source "$1/entrypoint.sh" "$2" >/dev/null 2>"$3"; echo "$INTENT"' _ "$REPO_ROOT" "$cmd" "$WORK/src.err")
  SRC_RC=$?
}

# _run <command>: runs entrypoint.sh itself (not sourced) with <command>. Sets RUN_RC; stdout
# and stderr land in $WORK/run.out / $WORK/run.err.
_run() {
  : > "$CALLS_LOG"
  timeout 60 bash "$EP" "$@" >"$WORK/run.out" 2>"$WORK/run.err"
  RUN_RC=$?
}

# The derivation this change replaces (entrypoint.sh before #633), for the "same as before"
# comparison.
_old_intent() {
  local ARGUMENTS="$1" INTENT
  INTENT=$(echo "$ARGUMENTS" | grep -oiP '^\s*\K(fix|continue|close|refine|plan|deconflict|recheck)' | head -1 | tr '[:upper:]' '[:lower:]' || true)
  INTENT=${INTENT:-fix}
  case "$ARGUMENTS" in
    "Fix main"|"fix main") INTENT="fix-main" ;;
    "Audit deps"|"audit deps") INTENT="audit-deps" ;;
  esac
  echo "$INTENT"
}

echo "--- A: every scheduler-issued command string keeps its intent ---"
while IFS='|' read -r cmd expected; do
  _source_intent "$cmd"
  assert_eq "'$cmd' -> $expected" "$expected" "$SRC_OUT"
  assert_eq "'$cmd' -> same intent as before #633" "$(_old_intent "$cmd")" "$SRC_OUT"
  assert_eq "'$cmd': preflight receives --intent $expected" \
    "providers preflight --intent $expected" "$(sed -n 1p "$CALLS_LOG")"
  assert_eq "'$cmd': model-endpoints hosting receives --intent $expected" \
    "providers model-endpoints hosting --intent $expected" "$(sed -n 2p "$CALLS_LOG")"
done <<'EOF'
Fix issue #12|fix
Continue issue #12|continue
Continue|continue
Close issue #12|close
Refine issue #12|refine
Plan issue #12|plan
Deconflict issue #12|deconflict
Recheck main|recheck
Fix main|fix-main
Audit deps|audit-deps
fix main|fix-main
audit deps|audit-deps
fix issue #3|fix
PLAN ISSUE #3|plan
Fixup #3|fix
EOF

echo ""
echo "--- B: sourced with no argument (the test-harness case) stays fix ---"
: > "$CALLS_LOG"
# `set --` first: a bare `source` keeps the caller's positional parameters.
SRC_OUT=$(ENTRYPOINT_SOURCE_ONLY=1 bash -c \
  'root="$1"; set --; source "$root/entrypoint.sh" >/dev/null 2>&1; echo "$INTENT"' _ "$REPO_ROOT")
assert_eq "no argument, sourced -> fix" "fix" "$SRC_OUT"

echo ""
echo "--- C: unrecognized commands exit non-zero before any call ---"
while IFS= read -r cmd; do
  _run "$cmd"
  assert_eq "'$cmd' exits 1" "1" "$RUN_RC"
  assert_eq "'$cmd' makes no clone, tracker, provider or model call" "" "$(cat "$CALLS_LOG")"
  assert_eq "'$cmd' names itself on stderr" "1" "$(grep -c "^Unrecognized command: '$cmd'$" "$WORK/run.err")"
  assert_eq "'$cmd' prints the usage on stderr" "1" "$(grep -c '^Usage:' "$WORK/run.err")"
done <<'EOF'
Implement issue #3
Resolve issue #3
#3
 Fix issue #3
Audit Deps
Classify preview (helper) #
Classify preview (helper) #5 extra
Classify preview (helper) #5x
classify preview (helper) #5
CLASSIFY PREVIEW (HELPER) #5
Verify preview classification #
Verify preview classification #5 extra
verify preview classification #5
EOF
_source_intent "Implement issue #3"
assert_eq "sourced, unrecognized: exits 1 too" "1" "$SRC_RC"
assert_eq "sourced, unrecognized: no INTENT printed" "" "$SRC_OUT"

echo ""
echo "--- D: no argument, or an empty one, prints usage and exits 1 before any call ---"
_run
assert_eq "no argument exits 1" "1" "$RUN_RC"
assert_eq "no argument: usage on stdout" "1" "$(grep -c '^Usage:' "$WORK/run.out")"
assert_eq "no argument: no call" "" "$(cat "$CALLS_LOG")"
_run ""
assert_eq "empty argument exits 1" "1" "$RUN_RC"
assert_eq "empty argument: no call" "" "$(cat "$CALLS_LOG")"

echo ""
echo "--- E: the two #587 strings are recognized and not available ---"
for cmd in "Classify preview (helper) #5" "Verify preview classification #5"; do
  _run "$cmd"
  assert_eq "'$cmd' exits 1" "1" "$RUN_RC"
  assert_eq "'$cmd': 'not available' on stderr" "1" "$(grep -c 'not available' "$WORK/run.err")"
  assert_eq "'$cmd': no clone, tracker, provider or model call" "" "$(cat "$CALLS_LOG")"
done
_run "Classify preview (helper) #5"
assert_eq "the helper string names the helper intent" "1" \
  "$(grep -c "'classify-preview-helper'" "$WORK/run.err")"
_run "Verify preview classification #5"
assert_eq "the checker string names the checker intent" "1" \
  "$(grep -c "'classify-preview-checker'" "$WORK/run.err")"

echo ""
echo "--- F: nothing inherited from the environment changes the intent ---"
_source_intent "Plan issue #5" ARGUMENTS="Classify preview (helper) #5" \
  INTENT=classify-preview-helper INTENT_NOT_YET_AVAILABLE=0
assert_eq "inherited ARGUMENTS/INTENT are ignored" "plan" "$SRC_OUT"
assert_eq "inherited ARGUMENTS/INTENT: preflight still gets --intent plan" \
  "providers preflight --intent plan" "$(sed -n 1p "$CALLS_LOG")"
_source_intent "Plan issue #5" INTENT_NOT_YET_AVAILABLE=1
assert_eq "inherited INTENT_NOT_YET_AVAILABLE=1 is ignored" "plan" "$SRC_OUT"
_source_intent "Classify preview (helper) #5" INTENT_NOT_YET_AVAILABLE=0
assert_eq "inherited INTENT_NOT_YET_AVAILABLE=0 does not make the helper available" "1" "$SRC_RC"
assert_eq "inherited INTENT_NOT_YET_AVAILABLE=0: no call" "" "$(cat "$CALLS_LOG")"

echo ""
echo "--- G: an inherited nocasematch does not widen any match ---"
assert_eq "sanity: BASHOPTS=nocasematch reaches a child bash" "on" \
  "$(env BASHOPTS=nocasematch bash -c 'shopt -q nocasematch && echo on')"
_source_intent "FIX MAIN" BASHOPTS=nocasematch
assert_eq "nocasematch: 'FIX MAIN' is fix, not fix-main" "fix" "$SRC_OUT"
_source_intent "AUDIT DEPS" BASHOPTS=nocasematch
assert_eq "nocasematch: 'AUDIT DEPS' is unrecognized" "1" "$SRC_RC"
_source_intent "CLASSIFY PREVIEW (HELPER) #5" BASHOPTS=nocasematch
assert_eq "nocasematch: 'CLASSIFY PREVIEW (HELPER) #5' is unrecognized" "1" "$SRC_RC"
assert_eq "nocasematch: ... and names itself as unrecognized" "1" \
  "$(grep -c '^Unrecognized command:' "$WORK/src.err")"

rm -rf "$WORK"
echo ""
echo "Results: $PASSED passed, $FAILED failed"
[ "$FAILED" -eq 0 ]
