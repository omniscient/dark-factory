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
echo PASS
