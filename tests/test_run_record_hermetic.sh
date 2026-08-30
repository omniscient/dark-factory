#!/usr/bin/env bash
# Regression guard (df#300, tightened df#362): a bash test that shells out to
# `cli.py run-record record|assemble` or `cli.py error-signature-write` -- directly,
# or indirectly by sourcing entrypoint.sh and calling on_failure() /
# _handle_session_window_pause() / _write_error_signature() -- without first
# exporting SCHEDULER_STATE_DIR at its first mention will write to the real
# /var/lib/dark-factory path if ever run outside strict test isolation. A file that
# sources entrypoint.sh (ENTRYPOINT_SOURCE_ONLY=1) also inherits its unconditional,
# source-time current-run.json write (entrypoint.sh:117-121), so any such file that
# calls one of those three functions must export CURRENT_RUN_DIR before the `source`
# line too. This already happened once (two `test-run` stub rows landed in production
# runs.jsonl on 2026-07-17; #362 found ~91 more plus a clobbered current-run.json).
# This is a static guard over tests/*.sh, not a runtime check.
#
# Run: bash tests/test_run_record_hermetic.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FAIL=0

# A bare mention of one of these names inside a test file is always an actual
# invocation, never a redefinition -- their bodies live in entrypoint.sh, sourced,
# not redeclared by any test. Comment-only mentions (e.g. a header describing what
# the file verifies) are excluded by requiring the match to start before any '#'.
_calls_trigger_fn() {
  grep -qE '^[^#]*(on_failure|_handle_session_window_pause|_write_error_signature)' "$1"
}

_is_state_dir_candidate() {
  local f="$1"
  grep -qE 'run-record (record|assemble)|error-signature-write' "$f" && return 0
  grep -q 'ENTRYPOINT_SOURCE_ONLY=1' "$f" && _calls_trigger_fn "$f" && return 0
  return 1
}

# Spec item 4(c), literal: the current-run.json clobber at entrypoint.sh:117-121 happens
# unconditionally at source time, so EVERY file that sources entrypoint.sh is a candidate —
# no trigger-function narrowing here (that narrowing is only sound for SCHEDULER_STATE_DIR,
# whose writers live inside on_failure / _handle_session_window_pause).
_is_current_run_dir_candidate() {
  grep -q 'ENTRYPOINT_SOURCE_ONLY=1' "$1"
}

# Finds the first non-comment line matching $2 in file $1 and checks it's exported
# either on that same line (`export $2=...`) or via a bare `export $2` on the very
# next line -- the two-line form used by tests/test_entrypoint_current_run.sh:32-33
# and tests/test_entrypoint_cost_report_regression.sh:47-48.
_first_mention_is_exported() {
  local f="$1" var="$2"
  local first
  first=$(grep -nE "^[^#]*${var}" "$f" | head -1)
  [ -z "$first" ] && return 1
  local lineno="${first%%:*}" content="${first#*:}"
  echo "$content" | grep -qE "export[[:space:]]+${var}=" && return 0
  local next
  next=$(sed -n "$((lineno + 1))p" "$f")
  echo "$next" | grep -qE "^[[:space:]]*export[[:space:]]+${var}[[:space:]]*\$" && return 0
  return 1
}

# CURRENT_RUN_DIR must be exported strictly before the `source .../entrypoint.sh`
# line -- entrypoint.sh's current-run.json write runs at source time, so an export
# after sourcing is too late.
_current_run_dir_exported_before_source() {
  local f="$1"
  local src
  src=$(grep -nE 'source[[:space:]]+.*entrypoint\.sh' "$f" | head -1)
  [ -z "$src" ] && return 1
  local src_line="${src%%:*}"
  local first
  first=$(grep -nE '^[^#]*CURRENT_RUN_DIR' "$f" | head -1)
  [ -z "$first" ] && return 1
  local lineno="${first%%:*}"
  _first_mention_is_exported "$f" "CURRENT_RUN_DIR" && [ "$lineno" -lt "$src_line" ]
}

for f in "$SCRIPT_DIR"/test_*.sh; do
  base="$(basename "$f")"
  [ "$base" = "test_run_record_hermetic.sh" ] && continue

  # A file merely mentioning "run-record assemble" in a comment/echo (e.g. a static
  # source-text guard like test_cost_report_harness_economics.sh, which greps
  # entrypoint.sh's text but never executes it) poses no pollution risk. Only a test
  # that actually executes code capable of writing state -- by sourcing entrypoint.sh
  # (ENTRYPOINT_SOURCE_ONLY=1) or invoking cli.py directly -- needs the override.
  grep -qE 'ENTRYPOINT_SOURCE_ONLY=1|cli\.py' "$f" || continue

  if _is_state_dir_candidate "$f"; then
    if _first_mention_is_exported "$f" "SCHEDULER_STATE_DIR"; then
      echo "  PASS: $base exports SCHEDULER_STATE_DIR at its first mention"
    else
      echo "  FAIL: $base's first SCHEDULER_STATE_DIR mention is not exported (mention-without-export leaks to /var/lib/dark-factory)"
      FAIL=1
    fi
  fi

  if _is_current_run_dir_candidate "$f"; then
    if _current_run_dir_exported_before_source "$f"; then
      echo "  PASS: $base exports CURRENT_RUN_DIR before sourcing entrypoint.sh"
    else
      echo "  FAIL: $base does not export CURRENT_RUN_DIR before sourcing entrypoint.sh (current-run.json clobber risk at source time)"
      FAIL=1
    fi
  fi
done

echo ""
[ "$FAIL" -eq 0 ] && echo "OK" || echo "FAILED"
[ "$FAIL" -eq 0 ]
