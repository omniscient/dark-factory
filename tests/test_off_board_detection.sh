#!/usr/bin/env bash
# Unit test for scheduler.sh's off-board ready-for-agent detection (issue #418).
# Run: bash tests/test_off_board_detection.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCHED="$SCRIPT_DIR/../scheduler.sh"

docker() { return 0; }
export -f docker

export GH_TOKEN="${GH_TOKEN:-stub-token}"
export CLAUDE_CODE_OAUTH_TOKEN="${CLAUDE_CODE_OAUTH_TOKEN:-stub-token}"
export SCHEDULER_SOURCE_ONLY=1
export SCHEDULER_STATE_DIR="$(mktemp -d /tmp/sched-offboard-test-statedir-XXXXXX)"
export FACTORY_CORE_CLI="$SCRIPT_DIR/../scripts/factory_core/cli.py"
export FACTORY_REPO_SLUG="omniscient/dark-factory"

cleanup() {
  rm -rf "$SCHEDULER_STATE_DIR"
}
trap cleanup EXIT

BOARD_ITEMS='{"items":[{"content":{"number":101,"title":"on board","type":"Issue"},"labels":["ready-for-agent"],"status":"Backlog"}]}'

# --- Case 1: an open ready-for-agent issue (#102) absent from the board ---
gh() {
  if echo "$*" | grep -q 'issue list'; then
    echo '[{"number":101},{"number":102}]'
    return 0
  fi
  echo "unexpected gh call: $*" >&2
  return 1
}
export -f gh

source "$SCHED"

OFF_BOARD="$(off_board_ready_issues "$BOARD_ITEMS")"
if [ "$OFF_BOARD" != "102" ]; then
  echo "FAIL: expected off-board issue 102, got: $OFF_BOARD" >&2
  exit 1
fi
echo "PASS: off_board_ready_issues finds issue #102 missing from the board"

# --- Case 2: every open ready-for-agent issue is already on the board ---
gh() {
  if echo "$*" | grep -q 'issue list'; then
    echo '[{"number":101}]'
    return 0
  fi
  return 1
}
export -f gh

OFF_BOARD="$(off_board_ready_issues "$BOARD_ITEMS")"
if [ -n "$OFF_BOARD" ]; then
  echo "FAIL: expected no off-board issues, got: $OFF_BOARD" >&2
  exit 1
fi
echo "PASS: off_board_ready_issues returns empty when the board is healthy"

# --- Case 3: the gh issue list lookup itself fails ---
gh() {
  if echo "$*" | grep -q 'issue list'; then
    return 1
  fi
  return 1
}
export -f gh

if off_board_ready_issues "$BOARD_ITEMS" >/dev/null 2>/dev/null; then
  echo "FAIL: expected off_board_ready_issues to return non-zero on a gh failure" >&2
  exit 1
fi
echo "PASS: off_board_ready_issues returns non-zero when gh issue list fails"

# --- Case 4: gh issue list returns a full (200) page -- the count can't be trusted ---
gh() {
  if echo "$*" | grep -q 'issue list'; then
    python3 -c 'import json; print(json.dumps([{"number": n} for n in range(200)]))'
    return 0
  fi
  return 1
}
export -f gh

if off_board_ready_issues "$BOARD_ITEMS" >/dev/null 2>/dev/null; then
  echo "FAIL: expected off_board_ready_issues to fail closed on a full (200) page" >&2
  exit 1
fi
echo "PASS: off_board_ready_issues fails closed when the ready-for-agent page is full"

# --- Case 5 (operator gate, blocking correction): under set -euo pipefail + set -E
# (both active in this sourced shell -- scheduler.sh:2-3), the main-loop idiom
# `if ! OFF_BOARD=$(off_board_ready_issues ...); then` must NOT kill this shell when the
# function returns non-zero. This executes the real idiom rather than grepping for it --
# a source-grep alone would not have caught the set -e regression the operator gate found.
gh() { return 1; }
export -f gh

REACHED_AFTER=false
if ! OFF_BOARD=$(off_board_ready_issues "$BOARD_ITEMS"); then
  RESULT_LINE="skip=nothing_to_do off_board_check=failed"
elif [ -n "$OFF_BOARD" ]; then
  RESULT_LINE="skip=queue_unreachable off_board=BUG_SHOULD_NOT_REACH"
else
  RESULT_LINE="skip=nothing_to_do"
fi
REACHED_AFTER=true

if [ "$REACHED_AFTER" != "true" ] || [ "$RESULT_LINE" != "skip=nothing_to_do off_board_check=failed" ]; then
  echo "FAIL: the if-condition-capture idiom did not survive a gh failure under set -e: $RESULT_LINE" >&2
  exit 1
fi
echo "PASS: 'if ! OFF_BOARD=\$(off_board_ready_issues ...)' survives a gh failure under set -euo pipefail"

# --- Case 6 (companion to Case 5): Case 5 proves the idiom is safe in isolation, but a
# copy of the idiom inside this test file cannot catch a future scheduler.sh regression
# back to the unsafe `OFF_BOARD=$(...); [ $? -ne 0 ]` form -- only reading the real file
# can. Guard the actual source.
if ! grep -qF 'if ! OFF_BOARD=$(off_board_ready_issues "$BOARD_ITEMS"); then' "$SCHED"; then
  echo "FAIL: scheduler.sh's idle branch no longer uses the set -e-safe 'if ! OFF_BOARD=\$(...)' capture idiom" >&2
  exit 1
fi
echo "PASS: scheduler.sh's idle branch still uses the set -e-safe capture idiom"

# --- Case 7: the single-off-board-issue rendering from the spec's own Testing section
# ("skip=queue_unreachable off_board=1 issues=#N"), run through the identical
# grep -c . / head -10 / sed 's/^/#/' / paste -sd, - pipeline scheduler.sh's idle branch
# uses (Task 6) -- not a hand-built string.
gh() {
  if echo "$*" | grep -q 'issue list'; then
    echo '[{"number":101},{"number":102}]'
    return 0
  fi
  return 1
}
export -f gh

OFF_BOARD="$(off_board_ready_issues "$BOARD_ITEMS")"
OFF_BOARD_COUNT=$(echo "$OFF_BOARD" | grep -c .)
OFF_BOARD_LIST=$(echo "$OFF_BOARD" | head -10 | sed 's/^/#/' | paste -sd, -)
[ "$OFF_BOARD_COUNT" -gt 10 ] && OFF_BOARD_LIST="${OFF_BOARD_LIST},+$((OFF_BOARD_COUNT - 10))more"
LOG_LINE="skip=queue_unreachable off_board=${OFF_BOARD_COUNT} issues=${OFF_BOARD_LIST}"
if [ "$LOG_LINE" != "skip=queue_unreachable off_board=1 issues=#102" ]; then
  echo "FAIL: expected 'skip=queue_unreachable off_board=1 issues=#102', got: $LOG_LINE" >&2
  exit 1
fi
echo "PASS: single off-board issue renders 'skip=queue_unreachable off_board=1 issues=#102'"

# --- Case 8: spec Requirement 8 -- cap the issues= list at 10, then "+N more" -- exercised
# against a real 12-issue off_board_ready_issues() result through the same pipeline.
gh() {
  if echo "$*" | grep -q 'issue list'; then
    python3 -c 'import json; print(json.dumps([{"number": n} for n in range(201, 213)]))'
    return 0
  fi
  return 1
}
export -f gh

OFF_BOARD="$(off_board_ready_issues "$BOARD_ITEMS")"
OFF_BOARD_COUNT=$(echo "$OFF_BOARD" | grep -c .)
OFF_BOARD_LIST=$(echo "$OFF_BOARD" | head -10 | sed 's/^/#/' | paste -sd, -)
[ "$OFF_BOARD_COUNT" -gt 10 ] && OFF_BOARD_LIST="${OFF_BOARD_LIST},+$((OFF_BOARD_COUNT - 10))more"
LOG_LINE="skip=queue_unreachable off_board=${OFF_BOARD_COUNT} issues=${OFF_BOARD_LIST}"
EXPECTED="skip=queue_unreachable off_board=12 issues=#201,#202,#203,#204,#205,#206,#207,#208,#209,#210,+2more"
if [ "$LOG_LINE" != "$EXPECTED" ]; then
  echo "FAIL: expected '$EXPECTED', got: $LOG_LINE" >&2
  exit 1
fi
echo "PASS: 12 off-board issues cap the issues= list at 10 with a '+2more' suffix"

echo "PASS: all off-board detection cases"
