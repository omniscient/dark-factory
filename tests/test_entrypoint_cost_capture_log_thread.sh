#!/usr/bin/env bash
# Static guard (#453): both `run-record assemble` call sites in entrypoint.sh hand the
# tee'd `archon workflow run` log ($TMP_OUT) to assemble for per-node status, the
# on_failure call resolves the workflow YAML from the clone like the success path does,
# and the success path deletes $TMP_OUT only after assembling. #645: both calls also pass
# --transcripts-dir, the Archon run keeps its JSON log lines (no --quiet, no LOG_LEVEL),
# and _keep_run_transcripts runs on every exit path after the workflow started. This
# test never sources or executes entrypoint.sh; it evals only the _keep_run_transcripts
# function body, in a child bash, against a temporary HOME and state directory.
#
# Run: bash tests/test_entrypoint_cost_capture_log_thread.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENTRYPOINT="${SCRIPT_DIR}/../entrypoint.sh"
FAIL=0

pass() { echo "  PASS: $1"; }
fail() { echo "  FAIL: $1"; FAIL=1; }

# Same on_failure extraction as test_cost_report_harness_economics.sh (the range runs to
# _resolve_merge_conflicts and so also spans the small helpers defined between them).
ON_FAILURE_BODY=$(sed -n '/^on_failure() {$/,/^_resolve_merge_conflicts() {$/p' "$ENTRYPOINT")

if echo "$ON_FAILURE_BODY" | grep -qF -- '--archon-log-file "${TMP_OUT:-}"'; then
  pass 'on_failure assemble passes --archon-log-file "${TMP_OUT:-}"'
else
  fail 'on_failure assemble does not pass --archon-log-file "${TMP_OUT:-}"'
fi

if echo "$ON_FAILURE_BODY" | grep -qF -- '--clone-dir "${CLONE_DIR:-.}"'; then
  pass 'on_failure assemble passes --clone-dir "${CLONE_DIR:-.}"'
else
  fail 'on_failure assemble does not pass --clone-dir "${CLONE_DIR:-.}"'
fi

SUCCESS_START=$(grep -n '^# --- Capture archon cost data and assemble run record' "$ENTRYPOINT" | head -1 | cut -d: -f1)
if [ -z "$SUCCESS_START" ]; then
  fail "success-path cost-capture block not found"
else
  SUCCESS_TAIL=$(tail -n +"$SUCCESS_START" "$ENTRYPOINT")
  if echo "$SUCCESS_TAIL" | grep -qF -- '--archon-log-file "$TMP_OUT"'; then
    pass 'success-path assemble passes --archon-log-file "$TMP_OUT"'
  else
    fail 'success-path assemble does not pass --archon-log-file "$TMP_OUT"'
  fi
  ASSEMBLE_LINE=$(echo "$SUCCESS_TAIL" | grep -n 'run-record assemble' | head -1 | cut -d: -f1)
  RM_LINE=$(echo "$SUCCESS_TAIL" | grep -nE 'rm -f .*"\$TMP_OUT"' | head -1 | cut -d: -f1)
  if [ -n "$ASSEMBLE_LINE" ] && [ -n "$RM_LINE" ] && [ "$RM_LINE" -gt "$ASSEMBLE_LINE" ]; then
    pass 'success path deletes $TMP_OUT after assemble'
  else
    fail 'success path does not delete $TMP_OUT after assemble'
  fi
fi

# The workflow-run loop's success exit must no longer delete $TMP_OUT right before
# `break` — assemble would then read a deleted log.
if awk 'prev ~ /^  rm -f "\$TMP_OUT"$/ && $0 ~ /^  break$/ {found=1} {prev=$0} END {exit !found}' "$ENTRYPOINT"; then
  fail 'workflow-run loop still deletes $TMP_OUT before break'
else
  pass 'workflow-run loop keeps $TMP_OUT through break'
fi

# --- #645: transcripts reach assemble, and the log keeps its JSON node events ---------
if echo "$ON_FAILURE_BODY" | grep -qF -- '--transcripts-dir "${HOME}/.claude/projects"'; then
  pass 'on_failure assemble passes --transcripts-dir'
else
  fail 'on_failure assemble does not pass --transcripts-dir'
fi
if [ -n "${SUCCESS_TAIL:-}" ] && echo "$SUCCESS_TAIL" | grep -qF -- '--transcripts-dir "${HOME}/.claude/projects"'; then
  pass 'success-path assemble passes --transcripts-dir'
else
  fail 'success-path assemble does not pass --transcripts-dir'
fi
RUN_LINE=$(grep -E '^\s*archon workflow run ' "$ENTRYPOINT")
if [ -n "$RUN_LINE" ] && ! echo "$RUN_LINE" | grep -q -- '--quiet'; then
  pass 'archon workflow run is not --quiet (dag_node_* lines stay in $TMP_OUT)'
else
  fail 'archon workflow run is --quiet or missing'
fi
if grep -q 'LOG_LEVEL' "$ENTRYPOINT"; then
  fail 'entrypoint.sh sets LOG_LEVEL (would drop the info-level node events)'
else
  pass 'entrypoint.sh sets no LOG_LEVEL'
fi

# A target's older clone copy of the CLI rejects --transcripts-dir (exit 2): both calls go
# through the baked-copy fallback (#633's helper) or the run would get no record at all.
if echo "$ON_FAILURE_BODY" | grep -qE '^    _cli_with_hosting_fallback run-record assemble \\$'; then
  pass 'on_failure assemble goes through _cli_with_hosting_fallback'
else
  fail 'on_failure assemble bypasses the exit-2 fallback'
fi
if [ -n "${SUCCESS_TAIL:-}" ] && echo "$SUCCESS_TAIL" | grep -qE '^_cli_with_hosting_fallback run-record assemble \\$'; then
  pass 'success-path assemble goes through _cli_with_hosting_fallback'
else
  fail 'success-path assemble bypasses the exit-2 fallback'
fi

# --- #645: _keep_run_transcripts is called on every exit path after the workflow ran ---
calls_in() { echo "$1" | grep -c '_keep_run_transcripts || true'; }
PAUSE_BRANCH=$(echo "$ON_FAILURE_BODY" | sed -n '/_handle_session_window_pause "\$TMP_OUT"; then$/,/^    return$/p')
HOLD_BRANCH=$(echo "$ON_FAILURE_BODY" | sed -n '/&& _handle_blast_hold; then$/,/^    exit 0$/p')
FAIL_PATH=$(echo "$ON_FAILURE_BODY" | sed -n '/rm -f "\$FAIL_COST_JSON" "\$FAIL_COST_STDERR"/,/_keep_run_transcripts/p')
LOOP=$(sed -n '/^while true; do$/,/^done$/p' "$ENTRYPOINT")
LOOP_PAUSE=$(echo "$LOOP" | sed -n '/if _handle_session_window_pause "\$TMP_OUT"; then/,/exit 0/p')
LOOP_HOLD=$(echo "$LOOP" | sed -n '/if _handle_blast_hold; then/,/exit 0/p')
[ "$(calls_in "$PAUSE_BRANCH")" = 1 ] && pass 'on_failure pause branch keeps transcripts' || fail 'on_failure pause branch does not keep transcripts'
[ "$(calls_in "$HOLD_BRANCH")" = 1 ] && pass 'on_failure blast-hold branch keeps transcripts' || fail 'on_failure blast-hold branch does not keep transcripts'
echo "$FAIL_PATH" | grep -qF 'if [ -n "${TMP_OUT:-}" ]; then _keep_run_transcripts || true; fi' \
  && pass 'on_failure failure path keeps transcripts once the workflow ran' \
  || fail 'on_failure failure path does not keep transcripts'
[ "$(calls_in "$LOOP_PAUSE")" = 1 ] && pass 'run-loop pause branch keeps transcripts' || fail 'run-loop pause branch does not keep transcripts'
[ "$(calls_in "$LOOP_HOLD")" = 1 ] && pass 'run-loop blast-hold branch keeps transcripts' || fail 'run-loop blast-hold branch does not keep transcripts'
if [ -n "${SUCCESS_TAIL:-}" ]; then
  KEEP_LINE=$(echo "$SUCCESS_TAIL" | grep -n '^_keep_run_transcripts || true$' | head -1 | cut -d: -f1)
  if [ -n "$KEEP_LINE" ] && [ -n "$RM_LINE" ] && [ "$KEEP_LINE" -lt "$RM_LINE" ] && [ "$KEEP_LINE" -gt "$ASSEMBLE_LINE" ]; then
    pass 'success path keeps transcripts after assemble, before deleting $TMP_OUT'
  else
    fail 'success path does not keep transcripts between assemble and rm'
  fi
fi

# --- #645: _keep_run_transcripts behaviour (function body only, in a child bash) -------
KEEP_FN=$(sed -n '/^_keep_run_transcripts() {$/,/^}$/p' "$ENTRYPOINT")
if [ -z "$KEEP_FN" ]; then
  fail '_keep_run_transcripts() not found'
else
  T=$(mktemp -d /tmp/ep-keep-XXXXXX)
  P="$T/home/.claude/projects/-workspace-x"
  mkdir -p "$P/sess/subagents" "$P/dir.jsonl"
  echo '{"type":"assistant"}' > "$P/sess.jsonl"
  echo '{"type":"assistant","isSidechain":true}' > "$P/sess/subagents/agent-a1.jsonl"
  echo '{}' > "$P/sess/subagents/agent-a1.meta.json"
  echo '{"old":true}' > "$P/old.jsonl"; touch -d '2000-01-01' "$P/old.jsonl"
  mkfifo "$P/fifo.jsonl"
  keep() {  # $1 = state dir; runs like entrypoint.sh: set -euo pipefail, an ERR trap
    HOME="$T/home" SCHEDULER_STATE_DIR="$1" RUN_ID=run645 RUN_STARTED_AT=2026-01-01T00:00:00Z \
      timeout 20 bash -c 'set -euo pipefail; trap "echo ERR_TRAP_FIRED" ERR; eval "$1"; _keep_run_transcripts; echo "rc=$?"' _ "$KEEP_FN" 2>&1
  }
  OUT=$(keep "$T/state")
  D="$T/state/transcripts/run645/-workspace-x"
  { [ -f "$D/sess.jsonl.gz" ] && [ "$(zcat "$D/sess/subagents/agent-a1.jsonl.gz")" = '{"type":"assistant","isSidechain":true}' ]; } \
    && pass 'gzip copies kept with their path under ~/.claude/projects' || fail 'transcript copies missing'
  { [ ! -e "$D/old.jsonl.gz" ] && [ ! -e "$D/fifo.jsonl.gz" ] && [ ! -e "$D/sess/subagents/agent-a1.meta.json.gz" ] && [ ! -e "$D/dir.jsonl.gz" ]; } \
    && pass 'only regular *.jsonl files modified since the run started are kept' || fail 'kept a file outside the input set'
  [ "$(stat -c %a "$D/sess.jsonl.gz")" = 600 ] && [ "$(stat -c %a "$T/state/transcripts/run645")" = 700 ] \
    && [ "$(stat -c %a "$D/sess/subagents")" = 700 ] && pass 'files 0600, directories 0700' || fail 'wrong modes on kept transcripts'
  echo "$OUT" | grep -q '^rc=0$' && ! echo "$OUT" | grep -q ERR_TRAP_FIRED \
    && pass 'returns 0 without tripping the ERR trap' || fail "unexpected status: $OUT"
  [ "$(echo "$OUT" | grep -c 'Session transcripts store:')" = 1 ] && pass 'logs the store size once' || fail 'size line missing'
  echo "$OUT" | grep -q '"type"' && fail 'transcript content leaked to the log' || pass 'no transcript content in the log'
  echo sentinel | gzip > "$D/sess.jsonl.gz"
  keep "$T/state" >/dev/null
  [ "$(zcat "$D/sess.jsonl.gz")" = sentinel ] && pass 'never overwrites an existing copy' || fail 'overwrote an existing copy'
  touch "$T/not-a-dir"
  OUT=$(keep "$T/not-a-dir/state")
  echo "$OUT" | grep -q '^rc=0$' && echo "$OUT" | grep -q '^WARNING: could not keep' \
    && ! echo "$OUT" | grep -q ERR_TRAP_FIRED \
    && pass 'a failure logs one warning and leaves the exit status unchanged' || fail "failure path: $OUT"
  # Plan review: bounds and planted links.
  truncate -s 65M "$P/huge.jsonl"
  OUT=$(keep "$T/state2")
  { [ ! -e "$T/state2/transcripts/run645/-workspace-x/huge.jsonl.gz" ] && [ -f "$T/state2/transcripts/run645/-workspace-x/sess.jsonl.gz" ] \
      && echo "$OUT" | grep -q '^WARNING: could not keep' && echo "$OUT" | grep -q '^rc=0$'; } \
    && pass 'a transcript over 64 MiB is skipped with a warning; the others are kept' || fail "oversize: $OUT"
  rm -f "$P/huge.jsonl"
  mkdir -p "$T/state3/transcripts" "$T/elsewhere"; ln -s "$T/elsewhere" "$T/state3/transcripts/run645"
  OUT=$(keep "$T/state3")
  { [ -z "$(ls -A "$T/elsewhere")" ] && echo "$OUT" | grep -q '^WARNING: could not keep' && echo "$OUT" | grep -q '^rc=0$'; } \
    && pass 'a link planted at the run directory is refused, never followed' || fail "planted link: $OUT"
  mkdir -p "$T/state4"; ln -s "$T/elsewhere" "$T/state4/transcripts"
  OUT=$(keep "$T/state4")
  { [ -z "$(ls -A "$T/elsewhere")" ] && echo "$OUT" | grep -q '^rc=0$'; } \
    && pass 'a link planted at the store is refused' || fail "planted store link: $OUT"
  rm -rf "$T"
fi

echo ""
[ "$FAIL" -eq 0 ] && echo "OK" || echo "FAILED"
[ "$FAIL" -eq 0 ]
