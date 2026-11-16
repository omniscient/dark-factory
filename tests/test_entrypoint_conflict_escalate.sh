#!/usr/bin/env bash
# Verifies _conflict_escalate() (#485): the deconflict path's escalation helper exists,
# posts ONE explanatory issue comment carrying the reason, moves the board to Blocked
# (as factory_core/deconflict.py's _escalate does), and RETURNS 0 -- the call sites follow
# it with `exit 0`, and a non-zero return would fire the ERR trap (on_failure) first and
# post a second, generic comment.
# Run: bash tests/test_entrypoint_conflict_escalate.sh
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

CURRENT_RUN_DIR=$(mktemp -d /tmp/ep-ce-rundir-XXXXXX)
export CURRENT_RUN_DIR

ENTRYPOINT_SOURCE_ONLY=1 source "$SCRIPT_DIR/../entrypoint.sh"

trap - ERR
set +e; set +u; set +o pipefail

CLONE_DIR="$REPO_ROOT"
ISSUE_NUM=485
FACTORY_REPO_SLUG="acme/widgets"
OUT=$(mktemp /tmp/ep-ce-out-XXXXXX)

PASSED=0; FAILED=0
assert_true() {
  local desc="$1"; shift
  if eval "$1"; then echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else echo "  FAIL: $desc" >&2; FAILED=$((FAILED+1)); fi
}

# Record every gh / board call instead of touching the network.
gh() { printf 'GH %s\n' "$*" >> "$OUT"; return 0; }
set_board_status() { printf 'BOARD %s\n' "$1" >> "$OUT"; return 0; }

assert_true "_conflict_escalate is defined" "declare -F _conflict_escalate >/dev/null"

: > "$OUT"
_conflict_escalate "No feature branch matching feat/issue-485-* was found." >/dev/null 2>&1
RC=$?
assert_true "returns 0 (call sites follow with 'exit 0'; non-zero would fire the ERR trap)" "[ $RC -eq 0 ]"
assert_true "posts exactly one issue comment" "[ \$(grep -c '^GH issue comment' '$OUT') -eq 1 ]"
assert_true "comment targets this issue on the factory repo" "grep -q '^GH issue comment 485 --repo acme/widgets' '$OUT'"
assert_true "comment carries the reason" "grep -q 'No feature branch matching feat/issue-485' '$OUT'"
assert_true "board moved to blocked" "grep -q '^BOARD blocked' '$OUT'"

echo "--- $PASSED passed, $FAILED failed ---"
rm -rf "$OUT" "$CURRENT_RUN_DIR"
[ "$FAILED" -eq 0 ]
