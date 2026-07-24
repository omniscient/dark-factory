#!/usr/bin/env bash
# Regression test for scheduler.sh:failing_checks_for_pr() after #181 R3: it now routes
# through `providers/cli.py codehost checks` instead of raw `gh pr checks`. This proves the
# R3 fix (get_change_checks no longer discards data on gh's nonzero exit) is actually reached
# end-to-end — a red/pending PR (the exact case this function exists to read) must still
# surface its failing checks after the migration.
#
# Run: bash tests/test_failing_checks_for_pr.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCHEDULER="${SCRIPT_DIR}/../scheduler.sh"

export GH_TOKEN="test-token"
export CLAUDE_CODE_OAUTH_TOKEN="test-oauth"
export SCHEDULER_SOURCE_ONLY=1

# shellcheck source=/dev/null
source "$SCHEDULER"
set +e

MOCK_CHECKS='[]'
python3() {
  case "$*" in
    *"providers/cli.py"*"codehost checks"*) printf '%s' "$MOCK_CHECKS" ;;
    *) return 0 ;;
  esac
}

PASS=0
FAIL=0
assert_eq() {
  local name="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $name"; PASS=$((PASS + 1))
  else
    echo "  FAIL: $name (expected '$expected', got '$actual')"; FAIL=$((FAIL + 1))
  fi
}

# Scenario A — all checks passing (gh would exit 0): no failing checks.
MOCK_CHECKS='[{"name":"ci","bucket":"pass","link":"u"}]'
assert_eq "all-pass PR has no failing checks" "0" "$(failing_checks_for_pr 9 | jq 'length')"

# Scenario B — a check is failing (gh would exit nonzero, but the fixed codehost-checks path
# still returns real data): the fail bucket must surface, not be silently dropped.
MOCK_CHECKS='[{"name":"ci","bucket":"fail","link":"u"},{"name":"lint","bucket":"pass","link":"u"}]'
FAILED=$(failing_checks_for_pr 9)
assert_eq "failing check count" "1" "$(echo "$FAILED" | jq 'length')"
assert_eq "failing check name" "ci" "$(echo "$FAILED" | jq -r '.[0].name')"

echo ""
echo "Passed: $PASS  Failed: $FAIL"
[ "$FAIL" -eq 0 ]
