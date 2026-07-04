#!/usr/bin/env bash
set -euo pipefail
source scripts/hooks.sh
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
export CLONE_DIR="$TMP" ARTIFACTS_DIR="$TMP/art"; mkdir -p "$ARTIFACTS_DIR"
# 1) missing hook, non-gate → default no-op success
run_hook validate || { echo "FAIL: missing non-gate hook must succeed"; exit 1; }
# 2) target hook is discovered and runs with the env contract
mkdir -p "$TMP/.factory/hooks"
printf '#!/bin/sh\necho "$CLONE_DIR" > "$ARTIFACTS_DIR/hook-ran"\n' > "$TMP/.factory/hooks/validate"
chmod +x "$TMP/.factory/hooks/validate"
run_hook validate
grep -q "$TMP" "$ARTIFACTS_DIR/hook-ran" || { echo "FAIL: hook env"; exit 1; }
# 3) gate propagates failure
printf '#!/bin/sh\nexit 3\n' > "$TMP/.factory/hooks/smoke-gate"; chmod +x "$TMP/.factory/hooks/smoke-gate"
if run_hook --gate smoke-gate; then echo "FAIL: gate must propagate"; exit 1; fi
echo PASS
