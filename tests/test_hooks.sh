#!/usr/bin/env bash
set -euo pipefail
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
export CLONE_DIR="$TMP" ARTIFACTS_DIR="$TMP/art"; mkdir -p "$ARTIFACTS_DIR"
# SCHEDULER_STATE_DIR must be exported BEFORE source so smoke_gate.sh binds
# SMOKE_STATE_DIR to it at source time (not the container default).
export SCHEDULER_STATE_DIR="$TMP/state"; mkdir -p "$SCHEDULER_STATE_DIR"
# Stub gh so _smoke_on_red/_smoke_on_green cannot hit the network.
# shellcheck disable=SC2317
gh() { :; }
export -f gh
# Stub python3 too: _smoke_on_red creates the regression ticket via
# `python3 "$PROVIDERS_CLI" tracker create`. Left unstubbed, that call reaches the
# real providers CLI — harmless in CI (no token) but inside a factory run container
# it filed a genuine "main is red" ticket on every implement/validate test run (#348).
STUB_LOG=$(mktemp "$TMP/stubs-XXXXXX.log")
# shellcheck disable=SC2317
python3() {
  echo "python3 $*" >> "$STUB_LOG"
  if echo "$*" | grep -q "tracker create"; then
    echo "999"
    return 0
  fi
  python "$@"
}
export -f python3
export STUB_LOG
source scripts/hooks.sh
# 1) missing hook, non-gate → default no-op success
run_hook validate || { echo "FAIL: missing non-gate hook must succeed"; exit 1; }
# 2) target hook is discovered and runs with the env contract
mkdir -p "$TMP/.factory/hooks"
printf '#!/bin/sh\necho "$CLONE_DIR" > "$ARTIFACTS_DIR/hook-ran"\n' > "$TMP/.factory/hooks/validate"
chmod +x "$TMP/.factory/hooks/validate"
run_hook validate
grep -q "$TMP" "$ARTIFACTS_DIR/hook-ran" || { echo "FAIL: hook env"; exit 1; }
# 3) gate propagates failure for non-smoke-gate hooks
printf '#!/bin/sh\nexit 3\n' > "$TMP/.factory/hooks/validate"; chmod +x "$TMP/.factory/hooks/validate"
if run_hook --gate validate; then echo "FAIL: gate must propagate"; exit 1; fi
# 4) target smoke-gate hook is CHECK-ONLY: green path clears sentinel
#    SMOKE_STATE_DIR was bound from SCHEDULER_STATE_DIR at source time above.
touch "$SCHEDULER_STATE_DIR/main-is-red"
printf '#!/bin/sh\nexit 0\n' > "$TMP/.factory/hooks/smoke-gate"
chmod +x "$TMP/.factory/hooks/smoke-gate"
run_hook --gate smoke-gate
[ ! -f "$SCHEDULER_STATE_DIR/main-is-red" ] || { echo "FAIL: green hook must clear sentinel"; exit 1; }
# 5) red hook routes through _smoke_on_red: sentinel written, clean halt (exit 0)
printf '#!/bin/sh\nexit 1\n' > "$TMP/.factory/hooks/smoke-gate"
( run_hook --gate smoke-gate )   # subshell: _smoke_on_red exits 0
RC=$?
[ "$RC" = "0" ] || { echo "FAIL: red smoke-gate must clean-halt with exit 0"; exit 1; }
[ -f "$SCHEDULER_STATE_DIR/main-is-red" ] || { echo "FAIL: red hook must write sentinel"; exit 1; }
# The red path must create its ticket through the stub, never the real providers CLI.
CREATES=$(grep -c "python3.*tracker create" "$STUB_LOG" 2>/dev/null || true)
[ "$CREATES" = "1" ] || { echo "FAIL: expected exactly one stubbed tracker create on red, got ${CREATES}"; exit 1; }
[ "$(cat "$SCHEDULER_STATE_DIR/main-is-red-issue")" = "999" ] || { echo "FAIL: sentinel issue file must hold the stubbed ticket number"; exit 1; }
# --- #438: present-but-non-executable hooks (cases below run AFTER the exactly-one
# --- tracker-create assertion above; each case resets the sentinel/hook mode it needs).
WARN_LOG="$SCHEDULER_STATE_DIR/hook-warnings.log"
# Negative case B (no log noise): executable hooks in cases 2-5 took the direct-exec path,
# which never warns — so no durable hook-not-executable line may exist yet.
[ ! -e "$WARN_LOG" ] || { echo "FAIL: executable hooks must not write $WARN_LOG"; exit 1; }
# 6) non-executable validate hook: body still runs (via bash) with the env contract, a
#    hook-not-executable warning goes to stderr and one durable line to $WARN_LOG
rm -f "$ARTIFACTS_DIR/hook-ran"
printf '#!/usr/bin/env bash\necho "$CLONE_DIR" > "$ARTIFACTS_DIR/hook-ran"\n' > "$TMP/.factory/hooks/validate"
chmod -x "$TMP/.factory/hooks/validate"
ISSUE_NUM=438 run_hook validate 2> "$TMP/stderr-6"
grep -q "$TMP" "$ARTIFACTS_DIR/hook-ran" || { echo "FAIL: non-exec hook body must run with the env contract"; exit 1; }
grep -q "hook-not-executable path=$TMP/.factory/hooks/validate crlf=no" "$TMP/stderr-6" \
  || { echo "FAIL: non-exec hook must emit the hook-not-executable token + path on stderr"; exit 1; }
[ "$(grep -c "hook-not-executable $TMP/.factory/hooks/validate issue=438 crlf=no" "$WARN_LOG")" = "1" ] \
  || { echo "FAIL: non-exec hook must append exactly one durable line to $WARN_LOG"; exit 1; }
# 6b) exit-code propagation: a non-exec validate hook exiting 3 makes --gate return 3
printf '#!/usr/bin/env bash\nexit 3\n' > "$TMP/.factory/hooks/validate"
chmod -x "$TMP/.factory/hooks/validate"
RC=0; run_hook --gate validate 2>/dev/null || RC=$?
[ "$RC" = "3" ] || { echo "FAIL: --gate must propagate a non-exec hook's rc 3, got ${RC}"; exit 1; }
# 6c) CRLF is reported (detection only, never normalised)
printf '#!/usr/bin/env bash\r\ntrue\r\n' > "$TMP/.factory/hooks/validate"
chmod -x "$TMP/.factory/hooks/validate"
run_hook validate 2> "$TMP/stderr-6c"
grep -q "hook-not-executable path=$TMP/.factory/hooks/validate crlf=yes" "$TMP/stderr-6c" \
  || { echo "FAIL: CRLF non-exec hook must be reported as crlf=yes"; exit 1; }
grep -q $'\r' "$TMP/.factory/hooks/validate" || { echo "FAIL: run_hook must not normalise CRLF"; exit 1; }
# The built-in default's check is counted from here on, so any fall-through is observable.
# NOTE: this override persists to the end of the script.
DEFAULT_CHECKS="$TMP/default-checks.log"; : > "$DEFAULT_CHECKS"
# shellcheck disable=SC2317
_smoke_check_main() { echo ran >> "$DEFAULT_CHECKS"; return 0; }
# Capture what _smoke_on_red posts: new-ticket body (python3 tracker create --body-file)
# and follow-up comment (gh issue comment --body). Both stubs stay offline (#348).
RED_TEXT="$TMP/red-text.log"; : > "$RED_TEXT"
# shellcheck disable=SC2317
gh() { if [ "${1:-}" = "issue" ] && [ "${2:-}" = "comment" ]; then printf '%s\n' "$*" >> "$RED_TEXT"; fi; return 0; }
# shellcheck disable=SC2317
python3() {
  echo "python3 $*" >> "$STUB_LOG"
  if echo "$*" | grep -q "tracker create"; then
    local prev="" a
    for a in "$@"; do
      if [ "$prev" = "--body-file" ]; then cat "$a" >> "$RED_TEXT"; fi
      prev="$a"
    done
    echo "999"
    return 0
  fi
  python "$@"
}
# 7) non-executable smoke-gate GREEN clears the sentinel via _smoke_on_green
touch "$SCHEDULER_STATE_DIR/main-is-red"
printf '#!/usr/bin/env bash\nexit 0\n' > "$TMP/.factory/hooks/smoke-gate"
chmod -x "$TMP/.factory/hooks/smoke-gate"
run_hook --gate smoke-gate 2> "$TMP/stderr-7"
[ ! -f "$SCHEDULER_STATE_DIR/main-is-red" ] || { echo "FAIL: non-exec green hook must clear sentinel"; exit 1; }
grep -q "hook-not-executable path=$TMP/.factory/hooks/smoke-gate" "$TMP/stderr-7" \
  || { echo "FAIL: non-exec smoke-gate hook must warn"; exit 1; }
# 8) non-executable smoke-gate RED (fresh state: green above removed sentinel + issue file)
#    routes through _smoke_on_red: sentinel written, clean halt, new ticket names the hook
rm -f "$SCHEDULER_STATE_DIR/main-is-red" "$SCHEDULER_STATE_DIR/main-is-red-issue"
printf '#!/usr/bin/env bash\nexit 1\n' > "$TMP/.factory/hooks/smoke-gate"
chmod -x "$TMP/.factory/hooks/smoke-gate"
( run_hook --gate smoke-gate 2>/dev/null )   # subshell: _smoke_on_red exits 0
RC=$?
[ "$RC" = "0" ] || { echo "FAIL: non-exec red smoke-gate must clean-halt with exit 0"; exit 1; }
[ -f "$SCHEDULER_STATE_DIR/main-is-red" ] || { echo "FAIL: non-exec red hook must write sentinel"; exit 1; }
grep -q "hook-not-executable $TMP/.factory/hooks/smoke-gate" "$RED_TEXT" \
  || { echo "FAIL: red ticket body must name the non-exec hook"; exit 1; }
# 8b) still red with a ticket on file (main-is-red-issue=999) → the follow-up comment names it too
: > "$RED_TEXT"
( run_hook --gate smoke-gate 2>/dev/null )
grep -q "hook-not-executable $TMP/.factory/hooks/smoke-gate" "$RED_TEXT" \
  || { echo "FAIL: red follow-up comment must name the non-exec hook"; exit 1; }
[ ! -s "$DEFAULT_CHECKS" ] || { echo "FAIL: built-in default must not run while a non-empty hook file exists"; exit 1; }
# 9) Negative case A (a missing hook must not become a pass): absent, zero-byte, and
#    directory smoke-gate "hooks" all still run the built-in default, with no warning.
WARN_LINES=$(wc -l < "$WARN_LOG")
rm -f "$TMP/.factory/hooks/smoke-gate"
run_hook --gate smoke-gate 2> "$TMP/stderr-9a"
: > "$TMP/.factory/hooks/smoke-gate"
run_hook --gate smoke-gate 2> "$TMP/stderr-9b"
rm -f "$TMP/.factory/hooks/smoke-gate"; mkdir "$TMP/.factory/hooks/smoke-gate"
run_hook --gate smoke-gate 2> "$TMP/stderr-9c"
rmdir "$TMP/.factory/hooks/smoke-gate"
[ "$(wc -l < "$DEFAULT_CHECKS")" = "3" ] || { echo "FAIL: absent/empty/dir hook must run the built-in default"; exit 1; }
if grep -q "hook-not-executable" "$TMP/stderr-9a" "$TMP/stderr-9b" "$TMP/stderr-9c"; then
  echo "FAIL: absent/empty/dir hook must not emit hook-not-executable"; exit 1
fi
[ "$(wc -l < "$WARN_LOG")" = "$WARN_LINES" ] || { echo "FAIL: absent/empty/dir hook must not write $WARN_LOG"; exit 1; }
# 10) Negative case B (explicit): an executable hook never warns
printf '#!/bin/sh\nexit 0\n' > "$TMP/.factory/hooks/validate"
chmod +x "$TMP/.factory/hooks/validate"
run_hook --gate validate 2> "$TMP/stderr-10"
if grep -q "hook-not-executable" "$TMP/stderr-10"; then echo "FAIL: executable hook must not warn"; exit 1; fi
[ "$(wc -l < "$WARN_LOG")" = "$WARN_LINES" ] || { echo "FAIL: executable hook must not write $WARN_LOG"; exit 1; }
echo PASS
