#!/usr/bin/env bash
# Verifies on_failure()'s failure-class annotation (#238): the signature it already writes is
# handed to `run-record annotate-failure` exactly once, after the board move and the failure
# comment and before the cost report, under a 20 s bound. The order and argv of every existing
# call and the container's exit code are unchanged. Runs the real on_failure and
# _write_error_signature around the workflow loop's own construct, as
# tests/test_entrypoint_blast_hold.sh section E does.
# Run: bash tests/test_entrypoint_failure_signature.sh
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

unset FACTORY_BLAST_HOLD_FILE DF_ENDPOINT_HOSTING LEDGER_WRITE_WARNING
WORK=$(mktemp -d /tmp/ep-fs-XXXXXX)
mkdir -p "$WORK/rundir" "$WORK/state"
# Both exported at their first mention, before sourcing: tests/test_run_record_hermetic.sh
# lints exactly that form (df#300, #362).
export CURRENT_RUN_DIR="$WORK/rundir"
export SCHEDULER_STATE_DIR="$WORK/state"

ENTRYPOINT_SOURCE_ONLY=1 source "$SCRIPT_DIR/../entrypoint.sh"

trap - ERR
trap 'rm -rf "$WORK"' EXIT
set +e; set +u; set +o pipefail
# #645: on_failure keeps the run's session transcripts from $HOME/.claude/projects. This
# harness is not about that and must never copy the machine's own transcripts.
_keep_run_transcripts() { :; }

PASSED=0; FAILED=0
check() {
  if eval "$2"; then echo "  PASS: $1"; PASSED=$((PASSED+1));
  else echo "  FAIL: $1" >&2; FAILED=$((FAILED+1)); fi
}

CLONE_DIR="$WORK/clone"   # every python3 call is stubbed; only the path string matters
CLI="$CLONE_DIR/dark-factory/scripts/factory_core/cli.py"
ARTIFACTS_DIR="$WORK/artifacts"; mkdir -p "$ARTIFACTS_DIR"
RUN_ID=test-run-fs-1
RUN_STARTED_AT=""         # elapsed and commits 0: a deterministic signature-write argv
ARGUMENTS="Fix issue #238"
SIG_VALUE="substantive:build_failure:3"

# `timeout` execs python3 from PATH, so the annotate call reaches this stub and never the
# python3 function below, which serves every direct call.
BIN="$WORK/bin"; mkdir -p "$BIN"
cat > "$BIN/python3" <<'STUB'
#!/usr/bin/env bash
echo "annotate $*" >> "$FS_LOG"
case "${FS_ANNOTATE_MODE:-ok}" in
  exit1) exit 1 ;;
  exit2) exit 2 ;;
  hang) sleep 60 ;;
esac
exit 0
STUB
chmod +x "$BIN/python3"

_fs_py() {
  case "$*" in
    *" error-signature-write "*)
      echo "sigwrite $*" >> "$FS_LOG"
      # fail: what the verb does on an unwritable state volume (exit 1, empty stdout)
      [ "${FS_SIG_MODE:-ok}" = ok ] || return 1
      echo "signature=$SIG_VALUE"
      return 0 ;;
    *" run-record assemble "*) echo "assemble $*" >> "$FS_LOG" ;;
    *" run-record record "*) echo "record $*" >> "$FS_LOG" ;;
    *" marker "*) echo "*stub footer*" ;;
    *) echo "py $*" >> "$FS_LOG" ;;
  esac
  return 0
}

_trapped_run() {  # _trapped_run <case>; exit status is what the container would exit with
  FS_LOG="$WORK/log.$1"; : > "$FS_LOG"; export FS_LOG
  (
    python3() { _fs_py "$@"; }
    git() {  # FS_GIT_LOG_FAILS=yes: `git log` fails as it does outside a repository
      [ "${FS_GIT_LOG_FAILS:-no}" = yes ] && [ "${1:-}" = log ] && return 128
      return 0
    }
    archon() { [ "${2:-}" = run ] && return 3; return 1; }
    run_post_mortem() { echo "post_mortem" >> "$FS_LOG"; }
    set_board_status() { echo "board $*" >> "$FS_LOG"; }
    post_or_update_comment() { echo "comment" >> "$FS_LOG"; }
    post_cost_report() { echo "cost" >> "$FS_LOG"; }
    _handle_session_window_pause() { [ "${FS_PAUSE:-no}" = yes ]; }
    _handle_blast_hold() { return 1; }
    export PATH="$BIN:$PATH" TMPDIR="$WORK/tmp"
    mkdir -p "$TMPDIR"
    set -eo pipefail
    trap on_failure ERR
    set +e
    TMP_OUT="$WORK/archon.out"
    archon workflow run archon-dark-factory "$ARGUMENTS" 2>&1 | tee "$TMP_OUT"
    EXIT_CODE=${PIPESTATUS[0]}
    set -e
    if [ "$EXIT_CODE" -ne 0 ]; then
      if _handle_session_window_pause "$TMP_OUT"; then exit 0; fi
      echo "loop_branch" >> "$FS_LOG"
      exit 97
    fi
    exit 0
  ) > "$WORK/out.$1" 2>&1
}
_tags() { cut -d' ' -f1 "$WORK/log.$1" | tr '\n' ' ' | sed 's/ $//'; }
_line() { grep "^$2 " "$WORK/log.$1"; }
_count() { grep -c "^$2 " "$WORK/log.$1"; }

EXPECTED_ANNOTATE="annotate $CLI run-record annotate-failure --run-id $RUN_ID --out-file $ARTIFACTS_DIR/run-record.json --failure-signature $SIG_VALUE"
# Today's argv, written out (guards: these must not change).
EXPECTED_ASSEMBLE="assemble $CLI run-record assemble --run-id $RUN_ID --issue 238 --intent fix --started-at  --artifacts-dir $ARTIFACTS_DIR --archon-cost-json <TMP> --archon-cost-exit-code 1 --archon-cost-stderr-file <TMP> --status failed --out-file $ARTIFACTS_DIR/run-record.json --archon-log-file $WORK/archon.out --transcripts-dir $HOME/.claude/projects --clone-dir $CLONE_DIR --side-effect-level ${FACTORY_SIDE_EFFECT_LEVEL:-1} --side-effect-profile ${FACTORY_SIDE_EFFECT_PROFILE_VERSION:-unknown}"
EXPECTED_SIGWRITE="sigwrite $CLI error-signature-write --issue 238 --phase implement --exit-code 3 --text-file $WORK/archon.out --elapsed-seconds 0 --commits-since-start 0 --delivery-failure-max-seconds ${DELIVERY_FAILURE_MAX_SECONDS:-30} --state-dir $SCHEDULER_STATE_DIR"

echo "--- A: implement-path failure ---"
ISSUE_NUM=238; INTENT=fix
_trapped_run a; RC=$?
check "exit_code_is_the_workflows (3 in, 3 out)" '[ "$RC" = 3 ]'
check "annotate_runs_after_the_signature_write_with_its_value" \
  '[ "$(_count a annotate)" = 1 ] && [ "$(_line a annotate)" = "$EXPECTED_ANNOTATE" ]'
check "annotate_runs_after_the_comment_and_before_the_cost_report" \
  '[ "$(_tags a)" = "record assemble sigwrite post_mortem board comment annotate cost" ]'
check "assemble_argv_is_unchanged" \
  '[ "$(_line a assemble | sed "s#$WORK/tmp/tmp\.[A-Za-z0-9]*#<TMP>#g")" = "$EXPECTED_ASSEMBLE" ]'
check "signature_write_argv_and_position_are_unchanged" \
  '[ "$(_count a sigwrite)" = 1 ] && [ "$(_line a sigwrite)" = "$EXPECTED_SIGWRITE" ]'
check "loop_failure_branch_is_not_reached_after_the_trap" '! grep -q "^loop_branch" "$WORK/log.a"'
check "signature_line_is_still_printed" 'grep -qxF "signature=$SIG_VALUE" "$WORK/out.a"'

echo "--- B: annotate exits 1 or 2 (an older clone cli.py) -> exit code unchanged ---"
for mode in exit1 exit2; do
  FS_ANNOTATE_MODE=$mode _trapped_run "b-$mode"; RC=$?
  check "exit_code_is_the_workflows with annotate $mode" '[ "$RC" = 3 ]'
  check "board move and comment still made ($mode)" \
    '[ "$(_count "b-$mode" board)" = 1 ] && grep -qx comment "$WORK/log.b-$mode"'
done
unset FS_ANNOTATE_MODE

echo "--- C: the verb printed nothing (unwritable state volume) ---"
FS_SIG_MODE=fail _trapped_run c; RC=$?
check "no_signature_means_no_annotate" '[ "$(_count c annotate)" = 0 ]'
check "failure comment and board move still made" \
  '[ "$(_tags c)" = "record assemble sigwrite post_mortem board comment cost" ] && [ "$RC" = 3 ]'

echo "--- D: a hung annotate (20 s bound) ---"
START=$(date +%s)
FS_ANNOTATE_MODE=hang _trapped_run d; RC=$?
ELAPSED=$(( $(date +%s) - START ))
check "a_hung_annotate_does_not_stop_the_board_move_or_the_comment" \
  '[ "$(_tags d)" = "record assemble sigwrite post_mortem board comment annotate cost" ] && [ "$RC" = 3 ]'
check "bounded: done in under 40 s (took ${ELAPSED}s)" '[ "$ELAPSED" -lt 40 ]'

echo "--- E: refine/plan/deconflict branch ---"
INTENT=plan
_trapped_run e; RC=$?
check "plan path annotates after the comment" \
  '[ "$(_tags e)" = "record assemble sigwrite comment annotate cost" ] && [ "$RC" = 3 ]'
check "plan path passes the same signature" '[ "$(_line e annotate)" = "$EXPECTED_ANNOTATE" ]'
INTENT=fix

echo "--- F: session-window pause ---"
FS_PAUSE=yes _trapped_run f; RC=$?
check "pause_path_makes_no_annotate_call" \
  '[ "$(_count f annotate)" = 0 ] && [ "$(_count f sigwrite)" = 0 ] && [ "$RC" = 0 ]'

echo "--- G: an intent without an issue ---"
SAVED_ISSUE="$ISSUE_NUM"; unset ISSUE_NUM; INTENT=recheck
_trapped_run g; RC=$?
check "issue_less_intent_makes_no_annotate_call" \
  '[ "$(_tags g)" = "record assemble cost" ] && [ "$RC" = 3 ]'
ISSUE_NUM="$SAVED_ISSUE"; INTENT=fix

echo "--- I: git log fails (a failure before the clone exists: cwd is not a repository) ---"
# Today errexit + pipefail end the trap at _write_error_signature's `git log | wc -l`, in
# on_failure's own shell, before the board move and the comment (exit 128). Running the
# function in a command substitution would clear -e and change that; it must not change.
FS_GIT_LOG_FAILS=yes RUN_STARTED_AT=$(date -u -d '-5 minutes' +%Y-%m-%dT%H:%M:%SZ) _trapped_run i; RC=$?
check "failure_before_the_clone_behaves_as_today (record, assemble, exit 128)" \
  '[ "$(_tags i)" = "record assemble" ] && [ "$RC" = 128 ]'

echo "--- H: static — one annotate call, inside on_failure; call sites unchanged ---"
ANN=$(grep -n 'run-record annotate-failure' "$REPO_ROOT/entrypoint.sh" | cut -d: -f1)
ONF=$(grep -n '^on_failure() {$' "$REPO_ROOT/entrypoint.sh" | cut -d: -f1)
TRAP=$(grep -n '^trap on_failure ERR$' "$REPO_ROOT/entrypoint.sh" | cut -d: -f1)
check "exactly one annotate call site" '[ "$(printf "%s\n" "$ANN" | grep -c .)" = 1 ]'
check "it is inside on_failure" '[ "$ONF" -lt "$ANN" ] && [ "$ANN" -lt "$TRAP" ]'
check "bounded with timeout -k 5 20" \
  'sed -n "${ANN}p" "$REPO_ROOT/entrypoint.sh" | grep -q "timeout -k 5 20 python3"'
# Matches the exact 6-space-indented line on purpose: a re-indent of on_failure fails this
# guard, and then only needs the expected line updated.
check "both on_failure signature writes are still plain calls in the trap's own shell" \
  '[ "$(grep -cxF "      _write_error_signature \"\$(_failure_phase_for_intent)\" \"\$EXIT_CODE\" \"\${TMP_OUT:-}\"" "$REPO_ROOT/entrypoint.sh")" = 2 ]'

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
