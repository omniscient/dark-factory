#!/usr/bin/env bash
# Safety invariant 3 (#180; originally #338's "deletion test"): the scheduler fails closed.
# read_config must exit 1 with "FATAL: cannot read config.yaml" when no config candidate
# exists, or when effective_config leaves a missing or empty run-config.env behind — and
# must otherwise export the resolved file's knobs with the env-wins rule.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCHED="$SCRIPT_DIR/../scheduler.sh"

PASSED=0; FAILED=0
_assert() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else
    echo "  FAIL: $desc — expected='$expected' got='$actual'" >&2; FAILED=$((FAILED+1))
  fi
}

WORK=$(mktemp -d /tmp/cfg-del-test-XXXXXX)
export SCHEDULER_STATE_DIR="$WORK/state"
export GH_TOKEN="${GH_TOKEN:-stub-token}" CLAUDE_CODE_OAUTH_TOKEN="${CLAUDE_CODE_OAUTH_TOKEN:-stub-token}"
# scheduler.sh runs `providers/cli.py preflight` at source time; the baked CLI it defaults
# to does not exist on a CI checkout, so stub it.
printf 'import sys\nsys.exit(0)\n' > "$WORK/providers_stub.py"
export FACTORY_PROVIDERS_CLI="$WORK/providers_stub.py"
# Set required config-driven vars so set -u is satisfied during sourcing
export POLL_INTERVAL=60 MAX_RETRIES=3 RATE_LIMIT_FLOOR=200 FACTORY_WIP_LIMIT=1
export MAIN_RED_RECHECK_ENABLED=true MAIN_RED_RECHECK_MINUTES=20 REFINE_WIP_LIMIT=2
export DIRECT_TO_PR_LABEL=direct-to-pr SPEC_GRACE_MINUTES=30 PLAN_GRACE_MINUTES=30
export CONFLICT_RESOLUTION_ENABLED=true DISPATCH_CEILING_ENABLED=true
export ABOVE_CEILING_LABEL=above-ceiling ABOVE_CEILING_KEYWORDS="migration|migrate"

SCHEDULER_SOURCE_ONLY=1 source "$SCHED" >/dev/null 2>&1
set +e  # scheduler.sh enabled errexit; every case below inspects its own exit status

# _read_config_with <candidate>... — run read_config in a subshell, with python3 wrapped so
# effective_config's scheduler mode searches only the given candidates. Prints the exit code.
_read_config_with() {
  CANDS=("$@")
  (
    python3() {
      local extra=() c
      for c in "${CANDS[@]}"; do extra+=(--candidate "$c"); done
      command python3 "$@" "${extra[@]}"
    }
    read_config
    echo "POLL_INTERVAL=${POLL_INTERVAL-<unset>}"
    echo "MAX_RETRIES=${MAX_RETRIES-<unset>}"
    echo "ABOVE_CEILING_KEYWORDS=${ABOVE_CEILING_KEYWORDS-<unset>}"
    echo "EPIC_AUTOPILOT_ENABLED=${EPIC_AUTOPILOT_ENABLED-<unset>}"
  ) > "$WORK/stdout.log" 2> "$WORK/stderr.log"
  echo $?
}
_out() { sed -n "s/^$1=//p" "$WORK/stdout.log"; }

echo "--- T1: no config candidate -> FATAL, exit 1 ---"
RC=$(_read_config_with "$WORK/nonexistent-1.yaml" "$WORK/nonexistent-2.yaml")
_assert "read_config exits 1" "1" "$RC"
_assert "FATAL line logged" "1" "$(grep -c '^FATAL: cannot read config.yaml$' "$WORK/stderr.log")"
_assert "no run-config.env written" "no" "$([ -e "$SCHEDULER_STATE_DIR/run-config.env" ] && echo yes || echo no)"

echo "--- T2: a stale run-config.env from a previous start does not mask the failure ---"
printf 'POLL_INTERVAL=1\n' > "$SCHEDULER_STATE_DIR/run-config.env"
RC=$(_read_config_with "$WORK/nonexistent-1.yaml")
_assert "read_config still exits 1" "1" "$RC"

echo "--- T3: module leaves an empty run-config.env -> FATAL, exit 1 ---"
RC=$( ( python3() { : > "$SCHEDULER_STATE_DIR/run-config.env"; }; read_config ) >/dev/null 2>"$WORK/stderr.log"; echo $? )
_assert "read_config exits 1" "1" "$RC"
_assert "FATAL line logged" "1" "$(grep -c '^FATAL: cannot read config.yaml$' "$WORK/stderr.log")"

echo "--- T4: config present -> knobs exported, env still wins ---"
FIRST="$WORK/project-config.yaml"
printf 'scheduler:\n  max_retries: 7\ndispatch_ceiling:\n  keywords: "first|only"\n' > "$FIRST"
BAKED="$WORK/baked-config.yaml"
printf 'scheduler:\n  poll_interval: 99\n  max_retries: 3\nepic_autopilot:\n  enabled: true\n' > "$BAKED"
unset POLL_INTERVAL ABOVE_CEILING_KEYWORDS
export MAX_RETRIES=9
RC=$(_read_config_with "$FIRST" "$BAKED")
_assert "read_config exits 0" "0" "$RC"
_assert "first file wins" "first|only" "$(_out ABOVE_CEILING_KEYWORDS)"
_assert "key absent from the first file is empty, not baked" "" "$(_out POLL_INTERVAL)"
_assert "env override wins" "9" "$(_out MAX_RETRIES)"
_assert "override logged" "1" "$(grep -c "^\[config\] MAX_RETRIES=9 (env override; config has '7')$" "$WORK/stderr.log")"
_assert "loaded-from names the first file" "1" "$(grep -c "^\[config\] loaded from ${FIRST}$" "$WORK/stdout.log")"

rm -rf "$WORK"
echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
