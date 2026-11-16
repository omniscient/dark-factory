#!/usr/bin/env bash
# #492: entrypoint.sh refuses a run whose target adapter.yaml is invalid, and lets a
# valid or absent adapter proceed. Exercises _adapter_invalid_refuse (hermetic: stubbed
# gh/git/docker, a recording stub for the providers CLI, scratch state/clone dirs).
# Run: bash tests/test_entrypoint_adapter_validate.sh
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

CURRENT_RUN_DIR=$(mktemp -d /tmp/ep-av-rundir-XXXXXX)
export CURRENT_RUN_DIR
ENTRYPOINT_SOURCE_ONLY=1 source "$SCRIPT_DIR/../entrypoint.sh"
trap - ERR
set +e; set +u; set +o pipefail

PASSED=0; FAILED=0
assert_eq() {
  if [ "$2" = "$3" ]; then echo "  PASS: $1"; PASSED=$((PASSED+1))
  else echo "  FAIL: $1 — expected='$2' got='$3'" >&2; FAILED=$((FAILED+1)); fi
}

export FACTORY_SCRIPTS_DIR="${FACTORY_SCRIPTS_DIR:-$REPO_ROOT/scripts}"
SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-av-state-XXXXXX)
export SCHEDULER_STATE_DIR
STUB_DIR=$(mktemp -d /tmp/ep-av-stub-XXXXXX)
STUB_PY_DIR=$(cygpath -m "$STUB_DIR" 2>/dev/null || echo "$STUB_DIR")
CALLS="$STUB_DIR/calls.log"; CALLS_PY="$STUB_PY_DIR/calls.log"
cat > "$STUB_DIR/providers_cli.py" <<PY
import sys
open("$CALLS_PY", "a").write(" ".join(sys.argv[1:]) + "\n")
PY
FACTORY_PROVIDERS_CLI="$STUB_PY_DIR/providers_cli.py"

reset() { : > "$CALLS"; rm -f "$SCHEDULER_STATE_DIR/hook-warnings.log"; }
mk_clone() {  # $1 = adapter content, or "ABSENT"
  CLONE_DIR=$(mktemp -d /tmp/ep-av-clone-XXXXXX)
  CLONE_DIR=$(cygpath -m "$CLONE_DIR" 2>/dev/null || echo "$CLONE_DIR")
  if [ "$1" != "ABSENT" ]; then
    mkdir -p "$CLONE_DIR/.factory"; printf '%s\n' "$1" > "$CLONE_DIR/.factory/adapter.yaml"
  fi
}
BAD='safety: [unclosed'
GOOD='schema_version: 1'

echo "--- invalid adapter, intent fix: refused ---"
reset; mk_clone "$BAD"; INTENT=fix; ISSUE_NUM=7
_adapter_invalid_refuse 2>"$STUB_DIR/err"; assert_eq "returns 1" 1 $?
assert_eq "stderr names adapter-invalid" 1 "$(grep -c 'adapter-invalid' "$STUB_DIR/err")"
assert_eq "durable line written" 1 "$(grep -c 'adapter-invalid' "$SCHEDULER_STATE_DIR/hook-warnings.log")"
assert_eq "ticket comment attempted with marker" 1 "$(grep -c 'tracker comment --id 7 --marker <!-- df-adapter-invalid -->' "$CALLS")"

echo "--- invalid adapter, no issue number (fix-main): refused, no comment ---"
reset; INTENT=fix-main; ISSUE_NUM=""
_adapter_invalid_refuse 2>/dev/null; assert_eq "returns 1" 1 $?
assert_eq "no comment" 0 "$(grep -c . "$CALLS")"

for i in refine plan continue deconflict; do
  echo "--- invalid adapter, intent $i: refused ---"
  reset; INTENT=$i; ISSUE_NUM=7
  _adapter_invalid_refuse 2>/dev/null; assert_eq "$i returns 1" 1 $?
done

for i in close recheck; do
  echo "--- invalid adapter, intent $i: not covered, proceeds ---"
  reset; INTENT=$i; ISSUE_NUM=7
  _adapter_invalid_refuse 2>/dev/null; assert_eq "$i returns 0" 0 $?
  assert_eq "$i no comment" 0 "$(grep -c . "$CALLS")"
done

echo "--- valid adapter: proceeds ---"
reset; mk_clone "$GOOD"; INTENT=fix; ISSUE_NUM=7
_adapter_invalid_refuse 2>/dev/null; assert_eq "returns 0" 0 $?
assert_eq "no comment" 0 "$(grep -c . "$CALLS")"
[ ! -s "$SCHEDULER_STATE_DIR/hook-warnings.log" ]; assert_eq "no warning line" 0 $?

echo "--- absent adapter: proceeds ---"
reset; mk_clone ABSENT
_adapter_invalid_refuse 2>/dev/null; assert_eq "returns 0" 0 $?

echo "--- refusal is wired after the clone, before phase work ---"
ep="$REPO_ROOT/entrypoint.sh"
call_ln=$(grep -n '^_adapter_invalid_refuse$' "$ep" | head -1 | cut -d: -f1)
clone_ln=$(grep -n '^git clone "\$REPO_URL"' "$ep" | head -1 | cut -d: -f1)
smoke_ln=$(grep -n 'run_hook --gate smoke-gate' "$ep" | head -1 | cut -d: -f1)
[ -n "$call_ln" ] && [ "$call_ln" -gt "$clone_ln" ] && [ "$call_ln" -lt "$smoke_ln" ]
assert_eq "call between clone and smoke gate" 0 $?

echo "--- scheduler boot validates the mounted checkout, warn-only, before the main loop ---"
sch="$REPO_ROOT/scheduler.sh"
val_ln=$(grep -n 'factory_core.adapter --clone-dir /workspace/project --validate' "$sch" | head -1 | cut -d: -f1)
loop_ln=$(grep -n '^while true; do' "$sch" | tail -1 | cut -d: -f1)
[ -n "$val_ln" ] && [ "$val_ln" -lt "$loop_ln" ]
assert_eq "scheduler validates before main loop" 0 $?
grep -q 'ADAPTER INVALID' "$sch"
assert_eq "scheduler logs a WARNING line" 0 $?

echo "Passed: $PASSED  Failed: $FAILED"
[ "$FAILED" -eq 0 ]
