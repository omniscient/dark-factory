#!/usr/bin/env bash
# Regression guard (df#300): a bash test that shells out to
# `cli.py run-record record|assemble` or `cli.py error-signature-write` without first
# overriding SCHEDULER_STATE_DIR to a temp directory will write to the real
# /var/lib/dark-factory path if ever run outside strict test isolation — this
# already happened once (two `test-run` stub rows landed in production runs.jsonl
# on 2026-07-17). This is a static guard over tests/*.sh, not a runtime check.
#
# Run: bash tests/test_run_record_hermetic.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FAIL=0

for f in "$SCRIPT_DIR"/test_*.sh; do
  base="$(basename "$f")"
  [ "$base" = "test_run_record_hermetic.sh" ] && continue

  # A file merely mentioning "run-record assemble" in a comment/echo (e.g. a static
  # source-text guard like test_cost_report_harness_economics.sh, which greps
  # entrypoint.sh's text but never executes it) poses no pollution risk. Only a test
  # that actually executes code capable of writing state — by sourcing entrypoint.sh
  # (ENTRYPOINT_SOURCE_ONLY=1) or invoking cli.py directly — needs the override.
  grep -qE 'ENTRYPOINT_SOURCE_ONLY=1|cli\.py' "$f" || continue

  if grep -qE 'run-record (record|assemble)|error-signature-write' "$f"; then
    if grep -q 'SCHEDULER_STATE_DIR' "$f"; then
      echo "  PASS: $base sets SCHEDULER_STATE_DIR before invoking run-record/error-signature-write"
    else
      echo "  FAIL: $base invokes run-record/error-signature-write without a SCHEDULER_STATE_DIR override"
      FAIL=1
    fi
  fi
done

echo ""
[ "$FAIL" -eq 0 ] && echo "OK" || echo "FAILED"
[ "$FAIL" -eq 0 ]
