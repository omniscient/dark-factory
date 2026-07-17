#!/usr/bin/env bash
# Verifies _write_error_signature() / _failure_phase_for_intent() (#33): the entrypoint
# helper computes elapsed/commits/dirty/artifact signals and hands them to
# factory_core/cli.py error-signature-write, which drops a classified signature file the
# scheduler later reads back via breaker.record_failure_signature().
# Run: bash tests/test_entrypoint_error_signature.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export GH_TOKEN="stub-token"
export CLAUDE_CODE_OAUTH_TOKEN="stub-token"

git() {
  case "$1" in
    log)
      # echo of an empty/unset $STUB_COMMITS still produces one blank line, so
      # "1,0p" (STUB_COMMIT_COUNT=0) would wrongly count as 1 commit via wc -l
      # downstream — guard explicitly instead of relying on the sed range alone.
      [ "${STUB_COMMIT_COUNT:-0}" -gt 0 ] && echo "$STUB_COMMITS" | sed -n '1,'"${STUB_COMMIT_COUNT:-0}"'p'
      ;;
    status) [ "${STUB_DIRTY:-false}" = "true" ] && echo " M some/file.py" ;;
    *) return 0 ;;
  esac
}
export -f git
gh() { echo "stub-title"; return 0; }
export -f gh
docker() { return 0; }
export -f docker
claude() { echo "stub"; return 0; }
export -f claude

ENTRYPOINT_SOURCE_ONLY=1 source "$SCRIPT_DIR/../entrypoint.sh"

trap - ERR
set +e; set +u; set +o pipefail

CLONE_DIR="$(dirname "$REPO_ROOT")"
ISSUE_NUM=33
RUN_ID=test-run-1

PASSED=0; FAILED=0
assert_true() {
  local desc="$1"; shift
  if eval "$1"; then echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else echo "  FAIL: $desc" >&2; FAILED=$((FAILED+1)); fi
}
assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else echo "  FAIL: $desc — expected='$expected' got='$actual'" >&2; FAILED=$((FAILED+1)); fi
}

echo "--- A: phase mapping ---"
INTENT=refine; assert_eq "refine -> refine" "refine" "$(_failure_phase_for_intent)"
INTENT=plan; assert_eq "plan -> plan" "plan" "$(_failure_phase_for_intent)"
INTENT=deconflict; assert_eq "deconflict -> resolve" "resolve" "$(_failure_phase_for_intent)"
INTENT=fix; assert_eq "fix -> implement" "implement" "$(_failure_phase_for_intent)"
INTENT=continue; assert_eq "continue -> implement" "implement" "$(_failure_phase_for_intent)"

echo ""
echo "--- B: delivery_failure classification (no commits, no artifact, fast) ---"
SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-es-statedir-XXXXXX)
ARTIFACTS_DIR=$(mktemp -d /tmp/ep-es-artifacts-XXXXXX)
RUN_STARTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
STUB_COMMIT_COUNT=0
STUB_DIRTY=false
INTENT=fix
_write_error_signature "implement" 1 ""
SIG_FILE="${SCHEDULER_STATE_DIR}/error-signatures/33.implement.sig"
assert_true "signature file written" "[ -f '$SIG_FILE' ]"
assert_true "classified environmental:delivery_failure" \
  "grep -q 'environmental:delivery_failure' '$SIG_FILE'"

echo ""
echo "--- C: substantive classification when a transcript + real work is present ---"
rm -rf "$SCHEDULER_STATE_DIR"; SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-es-statedir2-XXXXXX)
echo "placeholder" > "${ARTIFACTS_DIR}/implementation.md"
TMP_OUT=$(mktemp)
echo "FAILED tests/test_foo.py::test_bar - AssertionError" > "$TMP_OUT"
_write_error_signature "implement" 1 "$TMP_OUT"
SIG_FILE="${SCHEDULER_STATE_DIR}/error-signatures/33.implement.sig"
assert_true "classified substantive:test_failure" \
  "grep -q 'substantive:test_failure:1' '$SIG_FILE'"
rm -f "${ARTIFACTS_DIR}/implementation.md"

echo ""
echo "--- D: pre-existing workflow context files must NOT count as artifact_present (#279 regression) ---"
# issue.json / context-budget.json / token-opt-caps.env are written into ARTIFACTS_DIR by
# the workflow runner BEFORE the phase command ever executes — present on every run,
# including one where the agent did zero work. If these counted toward artifact_present,
# delivery_failure would never classify, which is exactly the false-positive early-trip
# the operator's carve-out (#279) was added to prevent.
rm -rf "$SCHEDULER_STATE_DIR"; SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-es-statedir3-XXXXXX)
echo '{"resolved_number":33}' > "${ARTIFACTS_DIR}/issue.json"
echo '{}' > "${ARTIFACTS_DIR}/context-budget.json"
_write_error_signature "implement" 1 ""
SIG_FILE="${SCHEDULER_STATE_DIR}/error-signatures/33.implement.sig"
assert_true "context-only artifacts still classify as delivery_failure" \
  "grep -q 'environmental:delivery_failure' '$SIG_FILE'"

echo ""
echo "--- E: run_post_mortem's factory-failures.jsonl must NOT count as artifact_present either ---"
rm -rf "$SCHEDULER_STATE_DIR"; SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-es-statedir4-XXXXXX)
echo '[{"issue":33}]' > "${ARTIFACTS_DIR}/factory-failures.jsonl"
_write_error_signature "implement" 1 ""
SIG_FILE="${SCHEDULER_STATE_DIR}/error-signatures/33.implement.sig"
assert_true "factory-failures.jsonl alone still classifies as delivery_failure" \
  "grep -q 'environmental:delivery_failure' '$SIG_FILE'"

rm -f "$TMP_OUT"
rm -rf "$SCHEDULER_STATE_DIR" "$ARTIFACTS_DIR"

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
