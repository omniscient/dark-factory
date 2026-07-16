#!/usr/bin/env bash
# Regression guard: on_failure must assemble a run-record (so outcome.state=="failed" is
# reachable), and post_cost_report() must render harness_economics without requiring the
# key to exist (older run-record.json files predate this field).
#
# Run: bash tests/test_cost_report_harness_economics.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENTRYPOINT="${SCRIPT_DIR}/../entrypoint.sh"
FAIL=0

# Extract each function's body by ranging to the NEXT top-level function header, not a
# fixed line-count window (`grep -A N` can miss content near the end of a long function)
# and not `/^}/` (both functions build multi-line bash strings containing a literal
# column-0 `}` as string content — e.g. post_cost_report's SAVINGS_BLOCK — which a naive
# `/^}/` sentinel matches too early, truncating the body). Current function order in
# entrypoint.sh (verified via `grep -n '^[a-zA-Z_][a-zA-Z0-9_]*() {$'`):
# ... post_cost_report() -> on_failure() -> _resolve_merge_conflicts() ...
ON_FAILURE_BODY=$(sed -n '/^on_failure() {$/,/^_resolve_merge_conflicts() {$/p' "$ENTRYPOINT")
POST_COST_REPORT_BODY=$(sed -n '/^post_cost_report() {$/,/^on_failure() {$/p' "$ENTRYPOINT")

# on_failure must call `run-record assemble --status failed` so a failed run gets a
# run-record.json (previously only `run-record record` ran on the failure path).
if echo "$ON_FAILURE_BODY" | grep -q -- '--status failed'; then
  echo "  PASS: on_failure calls run-record assemble --status failed"
else
  echo "  FAIL: on_failure does not assemble a failed run-record"
  FAIL=1
fi

# post_cost_report must read harness_economics with a `//` (jq alternative-operator)
# fallback, so a run-record.json without the key does not break rendering.
if echo "$POST_COST_REPORT_BODY" | grep -q 'harness_economics'; then
  echo "  PASS: post_cost_report references harness_economics"
else
  echo "  FAIL: post_cost_report does not render harness_economics"
  FAIL=1
fi

if echo "$POST_COST_REPORT_BODY" | grep 'harness_economics' | grep -q '//'; then
  echo "  PASS: harness_economics lookups use jq // fallback (absent-tolerant)"
else
  echo "  FAIL: harness_economics lookups are not absent-tolerant"
  FAIL=1
fi

echo ""
[ "$FAIL" -eq 0 ] && echo "OK" || echo "FAILED"
[ "$FAIL" -eq 0 ]
