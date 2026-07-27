#!/usr/bin/env bash
# Unit tests for scripts/scheduler_lib.sh — sourced directly, no scheduler.sh/gh/docker
# stub scaffolding needed (mirrors scripts/gate_lib.sh / tests/test_memory_write_gate.sh).
# Run: bash tests/test_scheduler_lib.sh
set -uo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
source "${REPO_ROOT}/scripts/scheduler_lib.sh"

PASSED=0; FAILED=0
assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else
    echo "  FAIL: $desc — expected='$expected' got='$actual'" >&2; FAILED=$((FAILED+1))
  fi
}

# ---- has_refine_skip_label ----
echo "--- has_refine_skip_label ---"
REFINE_SKIP_LABELS="needs-discussion,epic,spec-pending-review,plan-pending-review"
assert_eq "needs-discussion label skips" \
  "0" "$(has_refine_skip_label '{"labels":["needs-discussion"]}'; echo $?)"
assert_eq "no skip label does not skip" \
  "1" "$(has_refine_skip_label '{"labels":["ready-for-agent"]}'; echo $?)"

# ---- has_opt_in_refine_label ----
echo "--- has_opt_in_refine_label ---"
assert_eq "ready-for-agent present" \
  "0" "$(has_opt_in_refine_label '{"labels":["ready-for-agent"]}'; echo $?)"
assert_eq "ready-for-agent absent" \
  "1" "$(has_opt_in_refine_label '{"labels":["bug"]}'; echo $?)"

# ---- has_direct_to_pr_label ----
echo "--- has_direct_to_pr_label ---"
DIRECT_TO_PR_LABEL=direct-to-pr
assert_eq "direct-to-pr present" \
  "0" "$(has_direct_to_pr_label '{"labels":["direct-to-pr"]}'; echo $?)"
assert_eq "direct-to-pr absent" \
  "1" "$(has_direct_to_pr_label '{"labels":["bug"]}'; echo $?)"

# ---- get_size_label / is_above_ceiling / has_above_ceiling_label / is_below_ceiling ----
echo "--- ceiling classification ---"
ABOVE_CEILING_KEYWORDS="migration|migrate|performance|perf|architectur|refactor"
ABOVE_CEILING_LABEL=above-ceiling
assert_eq "get_size_label XL" "XL" "$(get_size_label '{"labels":["size: XL"]}')"
assert_eq "get_size_label M" "M" "$(get_size_label '{"labels":["size: M"]}')"
assert_eq "is_above_ceiling: XL always" \
  "0" "$(is_above_ceiling '{"labels":["size: XL"],"content":{"title":"anything"}}'; echo $?)"
assert_eq "is_above_ceiling: M + refactor keyword" \
  "0" "$(is_above_ceiling '{"labels":["size: M"],"content":{"title":"refactor(x): y"}}'; echo $?)"
assert_eq "is_above_ceiling: M without keyword stays below" \
  "1" "$(is_above_ceiling '{"labels":["size: M"],"content":{"title":"add x"}}'; echo $?)"
assert_eq "has_above_ceiling_label present" \
  "0" "$(has_above_ceiling_label '{"labels":["above-ceiling"]}'; echo $?)"
assert_eq "is_below_ceiling: S" \
  "0" "$(is_below_ceiling '{"labels":["size: S"]}'; echo $?)"
assert_eq "is_below_ceiling: unlabelled treated as S" \
  "0" "$(is_below_ceiling '{"labels":[]}'; echo $?)"
assert_eq "is_below_ceiling: M is not below" \
  "1" "$(is_below_ceiling '{"labels":["size: M"]}'; echo $?)"

# ---- elapsed_minutes_since_marker (requires FACTORY_PROVIDERS_CLI; stub python3) ----
echo "--- elapsed_minutes_since_marker ---"
FACTORY_PROVIDERS_CLI=/dev/null
python3() { echo '[]'; }
export -f python3
assert_eq "no matching comment returns empty" \
  "" "$(elapsed_minutes_since_marker 1 'no-match')"

# ---- has_new_comment_after_report (requires FACTORY_PROVIDERS_CLI + BOT_RE; stub python3) ----
echo "--- has_new_comment_after_report ---"
FACTORY_PRODUCT_NAME="Dark Factory"
python3() { echo '[{"body":"Posted by Dark Factory Refinement Pipeline","createdAt":"2026-01-01T00:00:00Z"},{"body":"human feedback","createdAt":"2026-01-02T00:00:00Z"}]'; }
export -f python3
# BOT_RE is normally computed once per poll cycle before the loop (scheduler.sh:850);
# sourcing the lib standalone never runs that loop, so set it explicitly here, mirroring
# tests/test_has_new_comment_after_report.sh.
BOT_RE="Posted by ${FACTORY_PRODUCT_NAME} Refinement Pipeline|Posted by ${FACTORY_PRODUCT_NAME} Backlog Scheduler|Posted by ${FACTORY_PRODUCT_NAME} Dark Factory|Updated by ${FACTORY_PRODUCT_NAME} Dark Factory|dark-factory-cost-report|Posted by ${FACTORY_PRODUCT_NAME} Epic Autopilot"
assert_eq "human comment after report marker is yes" \
  "yes" "$(has_new_comment_after_report 1 'Posted by Dark Factory Refinement Pipeline')"

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
