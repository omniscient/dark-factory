#!/usr/bin/env bash
# Regression test for close-preview's stale-container teardown check (issue #230).
# Mirrors tests/test_preview_differentiator.sh's convention: run_teardown_check is a
# maintained copy of workflows/archon-dark-factory.yaml's close-preview node (lines
# ~210-217) — keep both in sync on any future edit to that block.
# Run: bash tests/test_close_preview_teardown.sh
set -uo pipefail

PASSED=0; FAILED=0
assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else
    echo "  FAIL: $desc — expected='$expected' got='$actual'" >&2; FAILED=$((FAILED+1))
  fi
}

assert_contains() {
  local desc="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -qF "$needle"; then
    echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else
    echo "  FAIL: $desc — expected to find '$needle' in output" >&2; FAILED=$((FAILED+1))
  fi
}

assert_not_contains() {
  local desc="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -qF "$needle"; then
    echo "  FAIL: $desc — did not expect '$needle' in output" >&2; FAILED=$((FAILED+1))
  else
    echo "  PASS: $desc"; PASSED=$((PASSED+1))
  fi
}

# =====================================================================
# close-preview: stale-container teardown check
# =====================================================================
echo "--- close-preview: stale-container teardown check ---"

run_teardown_check() {
  local stale="$1"
  (
    STALE="$stale"
    if [ -n "$STALE" ]; then
      echo "WARNING: Stale preview containers remain after teardown (continuing):" >&2
      echo "$STALE" >&2
    else
      echo "close-preview: teardown verified — no mh-preview-99 containers remain"
    fi
  )
}

# No stale containers → verified message, exit 0
OUT=$(run_teardown_check "" 2>&1); RC=$?
assert_eq "empty STALE → exit 0" "0" "$RC"
assert_contains "empty STALE → teardown verified message" "teardown verified" "$OUT"
assert_not_contains "empty STALE → no WARNING" "WARNING" "$OUT"

# Stale containers present → WARNING + continue (exit 0), not ERROR + exit 1
OUT=$(run_teardown_check "mh-preview-99-backend-1" 2>&1); RC=$?
assert_eq "non-empty STALE → exit 0 (fail-soft, not exit 1)" "0" "$RC"
assert_contains "non-empty STALE → WARNING message" "WARNING: Stale preview containers remain after teardown" "$OUT"
assert_contains "non-empty STALE → container name in output" "mh-preview-99-backend-1" "$OUT"
assert_not_contains "non-empty STALE → no ERROR" "ERROR" "$OUT"

# =====================================================================
# Summary
# =====================================================================
echo ""
echo "============================="
echo "Results: ${PASSED} passed, ${FAILED} failed"
echo "============================="
[ "$FAILED" -eq 0 ] && exit 0 || exit 1
