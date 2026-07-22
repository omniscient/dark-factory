#!/usr/bin/env bash
# Regression guard: on_failure must assemble a run-record (so outcome.state=="failed" is
# reachable). The harness_economics-rendering assertions this file used to carry moved to
# factory_core.cost_report's unit tests (tests/test_cost_report.py, #182) — post_cost_report
# now delegates that rendering to Python and no longer has "harness_economics" in its own
# bash body for a static grep to find.
#
# Run: bash tests/test_cost_report_harness_economics.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENTRYPOINT="${SCRIPT_DIR}/../entrypoint.sh"
FAIL=0

# Extract on_failure's body by ranging to the NEXT top-level function header, not a fixed
# line-count window (`grep -A N` can miss content near the end of a long function).
ON_FAILURE_BODY=$(sed -n '/^on_failure() {$/,/^_resolve_merge_conflicts() {$/p' "$ENTRYPOINT")

# on_failure must call `run-record assemble --status failed` so a failed run gets a
# run-record.json (previously only `run-record record` ran on the failure path).
if echo "$ON_FAILURE_BODY" | grep -q -- '--status failed'; then
  echo "  PASS: on_failure calls run-record assemble --status failed"
else
  echo "  FAIL: on_failure does not assemble a failed run-record"
  FAIL=1
fi

echo ""
[ "$FAIL" -eq 0 ] && echo "OK" || echo "FAILED"
[ "$FAIL" -eq 0 ]
