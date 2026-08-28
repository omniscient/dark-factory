# Implementation Plan: Roll Back the Retry-Budget Counter for a Confirmed Session-Window Pause

**Issue:** omniscient/dark-factory#341
**Spec:** `docs/superpowers/specs/2026-08-26-session-window-pause-retry-rollback-design.md`
**Depends on:** #344 (merged, `dc2e4ab`) — the structured-evidence session-window classifier this
plan's rollback correction relies on.
**Related:** #279 (`docs/archive/2026-07-28-delivery-failure-retry-exemption-design.md`) — this plan
extends the same drop-file/decision-point mechanism.

---

## Goal

`scheduler.sh` increments a per-issue-and-phase retry counter at **dispatch time**, before a run's
outcome is known. When a dispatch pauses mid-run for a genuine Claude 5h session-window exhaustion
(confirmed by #344's classifier), that increment is never corrected — the scheduler's next poll sees
an inflated counter indistinguishable from a real failure, which tripped #19 to Blocked after only
two real failed attempts (2026-08-21 incident).

This plan adds a **rollback**: `entrypoint.sh`'s `_handle_session_window_pause()` writes a new
`environmental:session_window_pause` error-signature drop file (via a new CLI subcommand, bypassing
the normal failure classifier). `scheduler.sh` reads that signature at its existing
ceiling-check checkpoint, in a new `rollback_paused_retry()` helper, and decrements the retry counter
by 1 (clamped at 0) **before** evaluating the new dispatch — so a pause nets to zero effect on the
retry budget. Applied uniformly to all four retry call sites: `stage_plan`, `stage_refine`,
`stage_blocked_retry` (implement), `stage_conflict_resolve` (resolve). No cap, no shadow counter, no
`reset_retry` change, no config knob — the global `session-window-paused` sentinel is the only bound
this needs (see spec Requirements 3, 8).

## Architecture

- **`scripts/factory_core/error_signature.py`**: new module-level constant
  `SESSION_WINDOW_PAUSE_SIGNATURE = "environmental:session_window_pause"`.
- **`scripts/factory_core/cli.py`**: new `session-window-pause-signature-write` subcommand that calls
  `write_signature()` directly with the constant, skipping `classify()` entirely (the classification
  already happened in `session_window.py`).
- **`entrypoint.sh`**: `_handle_session_window_pause()` gains one guarded call to the new subcommand,
  writing the drop file for the in-flight issue+phase before it returns 0.
- **`scheduler.sh`**: new `rollback_paused_retry()` helper (mirrors `retry_or_skip_delivery_failure()`'s
  style) and `session_window_pause_note()` helper (mirrors `delivery_skip_note()`), wired into all four
  retry call sites at their existing ceiling-check checkpoints.

## Tech Stack

Bash (`scheduler.sh`, `entrypoint.sh`), Python 3 (`scripts/factory_core/*.py`), existing test harnesses
(`bash tests/test_scheduler.sh`, `bash tests/test_entrypoint_session_window.sh`,
`python -m pytest tests/test_factory_core_error_signature.py`). No new dependencies.

**Known pre-existing baseline (verified on current `main`, unrelated to #341):**
`bash tests/test_scheduler.sh` is **not** fully green on `main` — it reports `181 passed, 2 failed`.
The two failures are `G2: advance: set_board_status REFINED` and `I2: advance: set_board_status READY`,
caused by `tests/test_scheduler.sh` referencing `${STATUS_REFINED}`/`${STATUS_READY}` while
`scheduler.sh` actually defines `FACTORY_STATUS_REFINED`/`FACTORY_STATUS_READY` — an unrelated,
pre-existing naming drift (`git diff origin/main HEAD` is empty for both files). Every "verify it
passes" checkpoint below for `tests/test_scheduler.sh` means **no new failures beyond this known
`G2`/`I2` pair** — not literally `0 failed`. Do not fix `G2`/`I2` as part of this ticket; that is
out-of-scope board-status code and belongs to its own ticket. `bash tests/test_entrypoint_session_window.sh`
IS fully green on `main` (`40 passed, 0 failed`) — its "verify it passes" checkpoints do mean literally
`0 failed`.

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/error_signature.py` | Add `SESSION_WINDOW_PAUSE_SIGNATURE` constant |
| `scripts/factory_core/cli.py` | Add `_session_window_pause_signature_write` + `session-window-pause-signature-write` subcommand |
| `entrypoint.sh` | `_handle_session_window_pause()` writes the new drop file |
| `scheduler.sh` | Add `rollback_paused_retry()`, `session_window_pause_note()`; wire into 4 call sites |
| `tests/test_factory_core_error_signature.py` | Constant + CLI end-to-end test |
| `tests/test_entrypoint_session_window.sh` | New drop-file assertions; fix 1 now-stale assertion |
| `tests/test_scheduler.sh` | New `rollback_paused_retry` unit section + wiring cases in existing T/U/V/W sections |

---

## Task 1: `error_signature.py` — add the pause-signature constant

**Files:** `scripts/factory_core/error_signature.py`, `tests/test_factory_core_error_signature.py`

### TDD steps

1. Write the failing test. Append to `tests/test_factory_core_error_signature.py`:

   ```python
   def test_session_window_pause_signature_constant():
       from factory_core.error_signature import SESSION_WINDOW_PAUSE_SIGNATURE
       assert SESSION_WINDOW_PAUSE_SIGNATURE == "environmental:session_window_pause"
   ```

2. Verify it fails:

   ```bash
   cd /workspace/dark-factory
   PYTHONPATH=scripts python -m pytest tests/test_factory_core_error_signature.py -k session_window_pause_signature_constant -v
   ```

   Expected: `ImportError: cannot import name 'SESSION_WINDOW_PAUSE_SIGNATURE'`.

3. Implement. In `scripts/factory_core/error_signature.py`, insert after the `_TEST_FAILURE_RE` constant
   (before `def classify`):

   ```python
   _TEST_FAILURE_RE = re.compile(
       r"assertionerror|failed \(errors=|failed \(failures=|pytest.*failed|tsc.*error ts\d+",
       re.IGNORECASE,
   )

   # Written directly by entrypoint.sh's _handle_session_window_pause() via a dedicated CLI
   # subcommand, bypassing classify() entirely — the pause classification already happened in
   # session_window.py before that function returns 0. Included here (not just as a bash literal)
   # so scheduler.sh's hardcoded comparison and this module's writer share one canonical source
   # a reader can find by grepping the module, mirroring how "environmental:delivery_failure" is
   # both classify()'s return value and retry_or_skip_delivery_failure()'s bash literal.
   SESSION_WINDOW_PAUSE_SIGNATURE = "environmental:session_window_pause"


   def classify(
   ```

   (i.e. add the new constant and blank lines between the existing `_TEST_FAILURE_RE = re.compile(...)`
   block and the existing `def classify(` line — do not otherwise modify either.)

4. Verify it passes:

   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_factory_core_error_signature.py -k session_window_pause_signature_constant -v
   ```

5. Commit:

   ```bash
   git add scripts/factory_core/error_signature.py tests/test_factory_core_error_signature.py
   git commit -m "feat(scheduler): add session_window_pause error-signature constant (#341 Task 1)"
   ```

---

## Task 2: `cli.py` — add the `session-window-pause-signature-write` subcommand

**Files:** `scripts/factory_core/cli.py`, `tests/test_factory_core_error_signature.py`

### TDD steps

1. Write the failing test. Append to `tests/test_factory_core_error_signature.py`:

   ```python
   def test_cli_session_window_pause_signature_write_end_to_end(tmp_path):
       result = subprocess.run(
           [_sys.executable, CLI, "session-window-pause-signature-write",
            "--issue", "19", "--phase", "plan",
            "--state-dir", str(tmp_path)],
           capture_output=True, text=True,
       )
       assert result.returncode == 0
       sig_file = tmp_path / "error-signatures" / "19.plan.sig"
       data = json.loads(sig_file.read_text())
       assert data == {
           "signature": "environmental:session_window_pause",
           "phase": "plan",
           "exit_code": 0,
       }


   def test_cli_session_window_pause_signature_write_independent_of_classify(tmp_path):
       # Direct write, not routed through classify() — arbitrary/absent text must not change
       # the outcome (contrast test_cli_error_signature_write_missing_text_file_is_empty_text,
       # which DOES route through classify() and yields environmental:delivery_failure).
       result = subprocess.run(
           [_sys.executable, CLI, "session-window-pause-signature-write",
            "--issue", "20", "--phase", "refine",
            "--state-dir", str(tmp_path)],
           capture_output=True, text=True,
       )
       assert result.returncode == 0
       sig_file = tmp_path / "error-signatures" / "20.refine.sig"
       assert json.loads(sig_file.read_text())["signature"] == "environmental:session_window_pause"
   ```

2. Verify it fails:

   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_factory_core_error_signature.py -k session_window_pause_signature_write -v
   ```

   Expected: non-zero exit, `argument command: invalid choice: 'session-window-pause-signature-write'`.

3. Implement. In `scripts/factory_core/cli.py`, add the handler function right after
   `_error_signature_write` (after its closing `print(f"signature={signature}")` line, before
   `def _cost_report_check`):

   ```python
   def _session_window_pause_signature_write(args):
       from factory_core.error_signature import SESSION_WINDOW_PAUSE_SIGNATURE, write_signature
       write_signature(args.issue, args.phase, SESSION_WINDOW_PAUSE_SIGNATURE, 0, Path(args.state_dir))
   ```

   Then add the subparser right after the `esw` (`error-signature-write`) block, before the `bcs`
   (`breaker-check-signature`) block:

   ```python
       esw.set_defaults(func=_error_signature_write)

       swp = sub.add_parser("session-window-pause-signature-write")
       swp.add_argument("--issue", type=int, required=True)
       swp.add_argument("--phase", required=True)
       swp.add_argument("--state-dir", default="/var/lib/dark-factory")
       swp.set_defaults(func=_session_window_pause_signature_write)

       bcs = sub.add_parser("breaker-check-signature")
   ```

4. Verify it passes:

   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_factory_core_error_signature.py -k session_window_pause_signature_write -v
   ```

5. Run the full python suite to confirm no regression:

   ```bash
   PYTHONPATH=scripts python -m pytest tests/test_factory_core_error_signature.py -v
   ```

6. Commit:

   ```bash
   git add scripts/factory_core/cli.py tests/test_factory_core_error_signature.py
   git commit -m "feat(scheduler): add session-window-pause-signature-write CLI subcommand (#341 Task 2)"
   ```

---

## Task 3: `entrypoint.sh` — write the pause signature from `_handle_session_window_pause()`

**Files:** `entrypoint.sh`, `tests/test_entrypoint_session_window.sh`

This is the one place a pre-existing test assertion goes stale: section D's "no error signature
written on the paused path" was true before this task and becomes false after — the drop file is now
the whole point of the paused path. Fixing that assertion is part of this task, not a follow-up.

### TDD steps

1. Write the failing tests. In `tests/test_entrypoint_session_window.sh`, insert directly after the
   existing section-A block (after the `resume epoch within 2s of resetsAt+buffer` assertion, i.e.
   right before the `echo ""` / `echo "--- A2: ..."` lines):

   ```bash
   assert_true "session-window-pause-signature-write drop file written for issue+phase" \
     "[ -f '${SCHEDULER_STATE_DIR}/error-signatures/35.implement.sig' ]"
   SIG_JSON=$(cat "${SCHEDULER_STATE_DIR}/error-signatures/35.implement.sig" 2>/dev/null || echo '{}')
   assert_eq "drop file carries the pause signature" \
     "environmental:session_window_pause" \
     "$(echo "$SIG_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("signature",""))')"
   ```

   Then insert a new section at the single exact point directly before the existing
   `rm -f "$TMP_OUT" "$TMP_OUT2"` line (which is immediately preceded by section C's
   `SESSION_WINDOW_BACKOFF_ENABLED=true` kill-switch restore — this insertion point is
   AFTER that restore has already run, so the kill-switch is back on when A7 executes; do
   not insert any earlier, e.g. directly after section C's last `assert_true`, which would
   run A7 while the kill-switch is still off and make the first assertion below fail):

   ```bash
   echo ""
   echo "--- A7: session-window-pause-signature-write skipped when ISSUE_NUM is unset ---"
   A7_STATE_DIR=$(mktemp -d /tmp/ep-sw-statedir-a7-XXXXXX)
   SAVED_STATE_DIR="$SCHEDULER_STATE_DIR"
   SAVED_ISSUE_NUM="$ISSUE_NUM"
   SCHEDULER_STATE_DIR="$A7_STATE_DIR"
   unset ISSUE_NUM
   _handle_session_window_pause "$TMP_OUT"
   RC_A7=$?
   assert_eq "matched → still returns 0 with ISSUE_NUM unset" "0" "$RC_A7"
   assert_true "no drop file written when ISSUE_NUM is unset" \
     "[ ! -d '${A7_STATE_DIR}/error-signatures' ]"
   ISSUE_NUM="$SAVED_ISSUE_NUM"
   SCHEDULER_STATE_DIR="$SAVED_STATE_DIR"
   rm -rf "$A7_STATE_DIR"
   ```

   Then fix the now-stale section-D assertion. Replace:

   ```bash
   assert_true "no error signature written on the paused path" \
     "[ ! -d '${SCHEDULER_STATE_DIR}/error-signatures' ]"
   ```

   with:

   ```bash
   assert_true "session-window pause signature written for the paused run" \
     "[ -f '${SCHEDULER_STATE_DIR}/error-signatures/292.implement.sig' ]"
   assert_eq "pause signature drop file carries the pause classification, not a classify() output" \
     "environmental:session_window_pause" \
     "$(python3 -c "import json; print(json.load(open('${SCHEDULER_STATE_DIR}/error-signatures/292.implement.sig'))['signature'])")"
   ```

2. Verify the new assertions fail (the drop-file ones) and the section-D fix fails too (against
   pre-Task-3 `entrypoint.sh`):

   ```bash
   bash tests/test_entrypoint_session_window.sh 2>&1 | grep -E "FAIL|Results"
   ```

   Expected: FAILs for "drop file written", "drop file carries the pause signature", "matched →
   still returns 0 with ISSUE_NUM unset" is fine (already true) but "no drop file written" trivially
   passes pre-change too — the meaningful new-behavior FAILs are the section-A and section-D ones.

3. Implement. In `entrypoint.sh`, inside `_handle_session_window_pause()`, insert the guarded call
   immediately before the existing `run-record record --stage paused` call:

   ```bash
     python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record record \
   ```

   becomes:

   ```bash
     if [ -n "${ISSUE_NUM:-}" ]; then
       python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" session-window-pause-signature-write \
         --issue "$ISSUE_NUM" \
         --phase "$(_failure_phase_for_intent)" \
         --state-dir "${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}" || true
     fi

     python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record record \
   ```

   (`_failure_phase_for_intent` is defined later in the same file but that's fine — it's called at
   runtime, after the whole file is sourced, not at source time.)

4. Verify it passes:

   ```bash
   bash tests/test_entrypoint_session_window.sh 2>&1 | tail -5
   ```

   Expected: `Results: N passed, 0 failed` with N larger than the pre-Task-3 count.

5. Commit:

   ```bash
   git add entrypoint.sh tests/test_entrypoint_session_window.sh
   git commit -m "fix(scheduler): write session_window_pause error signature on confirmed pause (#341 Task 3)"
   ```

---

## Task 4: `scheduler.sh` — add `rollback_paused_retry()`

**Files:** `scheduler.sh`, `tests/test_scheduler.sh`

### TDD steps

1. Write the failing tests. In `tests/test_scheduler.sh`, insert a new section right after section
   B2's existing block (after the `> "$STUB_LOG"` line that follows the K10 assertions, i.e. right
   before the `# ==========================================` / `# C: dispatch() exit-code capture`
   header):

   ```bash
   # ==========================================
   # B3: environmental:session_window_pause never trips the early-stuck breaker
   # ==========================================
   echo ""
   echo "--- B3: session_window_pause repeat never trips ---"
   echo '{}' > "$STATE_FILE"

   _drop_sig 53 plan "environmental:session_window_pause"
   check_failure_signature "53" "plan" > /dev/null
   _drop_sig 53 plan "environmental:session_window_pause"
   RESULT_PAUSE=$(check_failure_signature "53" "plan")
   assert_eq "environmental:session_window_pause repeat never trips" "1" \
     "$(echo "$RESULT_PAUSE" | grep -c 'stuck=false')"

   echo '{}' > "$STATE_FILE"; > "$STUB_LOG"

   # ==========================================
   # B4: rollback_paused_retry (#341 session-window pause rollback)
   # ==========================================
   echo ""
   echo "--- B4: rollback_paused_retry ---"
   echo '{}' > "$STATE_FILE"; > "$STUB_LOG"

   # B4a: non-pause signature is a no-op
   increment_retry "120:refine"
   rollback_paused_retry 120 refine "substantive:test_failure:1" "120:refine" 3
   assert_eq "B4a: non-pause signature does not decrement" "1" "$(get_retry_count "120:refine")"

   # B4b: empty signature is a no-op
   rollback_paused_retry 120 refine "" "120:refine" 3
   assert_eq "B4b: empty signature does not decrement" "1" "$(get_retry_count "120:refine")"

   # B4c: pause signature decrements by 1
   rollback_paused_retry 120 refine "environmental:session_window_pause" "120:refine" 3
   assert_eq "B4c: pause signature decrements the counter" "0" "$(get_retry_count "120:refine")"

   # B4d: clamps at 0 — a pause observed when the counter is already 0 (e.g. after a
   # reset_retry) does not go negative
   rollback_paused_retry 120 refine "environmental:session_window_pause" "120:refine" 3
   assert_eq "B4d: decrement clamps at 0" "0" "$(get_retry_count "120:refine")"

   # B4e: delegates to breaker-set-retry (not increment_retry) for the write
   > "$STUB_LOG"
   increment_retry "121:refine"
   increment_retry "121:refine"
   rollback_paused_retry 121 refine "environmental:session_window_pause" "121:refine" 3
   assert_eq "B4e: delegates to breaker-set-retry with the decremented value" \
     "1" "$(grep -c 'breaker-set-retry --key 121:refine --value 1' "$STUB_LOG" || echo 0)"

   # B4f: logs the decrement action to stderr with issue/phase/action/count
   > "$STUB_LOG"; echo '{}' > "$STATE_FILE"
   increment_retry "122:resolve"
   LOG_LINE=$(rollback_paused_retry 122 resolve "environmental:session_window_pause" "122:resolve" 3 2>&1 >/dev/null)
   assert_eq "B4f: log line names issue/phase/action/count" "1" \
     "$(echo "$LOG_LINE" | grep -c 'session_window_gate issue=#122 phase=resolve action=retry_decrement count=0/3')"

   > "$STUB_LOG"; echo '{}' > "$STATE_FILE"
   ```

2. Verify it fails:

   ```bash
   bash tests/test_scheduler.sh 2>&1 | grep -E "FAIL|command not found|Results"
   ```

   Expected: `rollback_paused_retry: command not found` errors and corresponding FAILs.

3. Implement. In `scheduler.sh`, insert the new function between `check_failure_signature()`'s
   closing `}` and the `# --- Skip the counted retry for a runner-side delivery failure (#279) ---`
   comment (i.e. immediately before `retry_or_skip_delivery_failure`'s block):

   ```bash
   # --- Session-window pause rollback (corrects history; unconditional, never deferred) ---
   # Usage: rollback_paused_retry <issue_num> <phase> <sig_value> <retry_key> <ceiling>
   # When sig_value is "environmental:session_window_pause" (written only by
   # entrypoint.sh's _handle_session_window_pause, gated by #344's structured-evidence
   # classifier), the prior dispatch for retry_key never reached a verdict — its optimistic
   # increment_retry is decremented (clamped at 0) so the caller's immediately-following
   # get_retry_count/ceiling-check/increment_retry sequence treats this attempt as if the
   # paused one had never counted. No-op for every other sig_value (including "").
   rollback_paused_retry() {
     local issue_num="$1" phase="$2" sig_value="$3" retry_key="$4" ceiling="$5"
     [ "$sig_value" = "environmental:session_window_pause" ] || return 0
     local cur new
     cur=$(get_retry_count "$retry_key")
     new=$(( cur > 0 ? cur - 1 : 0 ))
     STATE_FILE="$STATE_FILE" python3 "$FACTORY_CORE_CLI" breaker-set-retry --key "$retry_key" --value "$new"
     echo "[$(date -u +%FT%TZ)] session_window_gate issue=#${issue_num} phase=${phase} action=retry_decrement count=${new}/${ceiling}" >&2
   }

   ```

4. Verify it passes:

   ```bash
   bash tests/test_scheduler.sh 2>&1 | tail -5
   ```

   Expected: no *new* failures — `Results: N passed, 2 failed` where the 2 failures are exactly the
   pre-existing `G2`/`I2` pair (Tech Stack section above), and N is larger than the pre-Task-4 count.

5. Commit:

   ```bash
   git add scheduler.sh tests/test_scheduler.sh
   git commit -m "feat(scheduler): add rollback_paused_retry helper (#341 Task 4)"
   ```

---

## Task 5: `scheduler.sh` — add `session_window_pause_note()`

**Files:** `scheduler.sh`

This helper's actual output text (the `⏸️ The previous attempt was paused for a Claude
session-window exhaustion...` line) only gets real, executed test coverage once Tasks 6-7 call the
function itself — not an inline copy of its text — from `_run_refine_body`/`_run_plan_body` and grep
for its exact output. Task 6/7 below do call the real helper; do not let a later edit reintroduce an
inline literal in those test bodies, or this function reverts to having zero executed coverage (it
would then merely mirror the existing, similarly-untested `delivery_skip_note()` — acceptable as a
precedent, but not the intent here).

### Steps

1. In `scheduler.sh`, insert the new helper right after `delivery_skip_note()`'s closing `}`, before
   the `# --- Mergeable status for a PR: ...` comment. **Strip the 3-space markdown-list indent from
   every line of the code block below before pasting, preserving the code's own relative indentation**
   — the markdown list nesting below adds 3 spaces of indent for readability that is not part of the
   bash source, but
   `cat <<EOF` (not `<<-EOF`) requires the `EOF` terminator to start at column 0, and the two `>`
   blockquote body lines must have zero leading whitespace or GitHub renders the note as an indented
   code block instead of a blockquote (matching the existing `delivery_skip_note()` at
   `scheduler.sh:439-447`, which has the same two constraints):

   ```bash
   # --- Shared "previous attempt hit a confirmed session-window pause" issue-comment note (#341) ---
   # Callers must set PREV_SESSION_WINDOW_PAUSE (non-empty to include the note) before calling.
   session_window_pause_note() {
     if [ -n "$PREV_SESSION_WINDOW_PAUSE" ]; then
       cat <<EOF


   > ⏸️ The previous attempt was paused for a Claude session-window exhaustion and was not
   > counted against the retry budget.
   EOF
     fi
   }

   ```

2. Sanity-check the file still parses (`bash -n` catches a malformed heredoc terminator, though not a
   stray leading space on the blockquote body lines — visually confirm those two lines start with `>`
   at column 0, no indentation, before committing):

   ```bash
   bash -n scheduler.sh
   ```

3. Commit:

   ```bash
   git add scheduler.sh
   git commit -m "feat(scheduler): add session_window_pause_note comment helper (#341 Task 5)"
   ```

---

## Task 6: Wire `stage_refine`

**Files:** `scheduler.sh`, `tests/test_scheduler.sh`

### TDD steps

1. Update the failing test first. In `tests/test_scheduler.sh` section T, replace `_run_refine_body`
   in full:

   ```bash
   _run_refine_body() {
     local issue="$1"
     SIG_RESULT=$(check_failure_signature "$issue" "refine")
     SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
     if echo "$SIG_RESULT" | grep -q "stuck=true"; then
       trip_to_blocked "$issue" "refine" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
       return
     fi

     rollback_paused_retry "$issue" "refine" "$SIG_VALUE" "${issue}:refine" "$REFINE_MAX_RETRIES"

     PREV_SESSION_WINDOW_PAUSE=""
     [ "$SIG_VALUE" = "environmental:session_window_pause" ] && PREV_SESSION_WINDOW_PAUSE=1

     PREV_DELIVERY_SKIP=""
     DECISION=$(retry_or_skip_delivery_failure "$issue" "refine" "$SIG_VALUE" "${issue}:refine" "$REFINE_MAX_RETRIES" || echo "count")
     case "$DECISION" in
       skip) PREV_DELIVERY_SKIP=1 ;;
       trip:*) trip_to_blocked "$issue" "refine" "${DECISION#trip:}"; return ;;
       count|*)
         RETRIES=$(get_retry_count "${issue}:refine")
         if [ "$RETRIES" -ge "$REFINE_MAX_RETRIES" ]; then
           trip_to_blocked "$issue" "refine" "retry limit of ${REFINE_MAX_RETRIES} reached"
           return
         fi
         increment_retry "${issue}:refine"
         ;;
     esac

     DELIVERY_NOTE=""
     if [ -n "$PREV_DELIVERY_SKIP" ]; then
       DELIVERY_NOTE=" was not counted against the retry budget (runner-side delivery failure, #279)."
     fi
     SESSION_WINDOW_NOTE=$(session_window_pause_note)
     gh issue comment "$issue" --repo test/repo --body "Starting refine.${DELIVERY_NOTE}${SESSION_WINDOW_NOTE}" > /dev/null
     dispatch "Refine issue #${issue}" > /dev/null
   }
   ```

   Note this calls the real `session_window_pause_note()` from Task 5 (not an inline copy of its
   text) — it reads the `PREV_SESSION_WINDOW_PAUSE` global this body already sets above, so T4d below
   asserts against the helper's actual output, giving it real executed coverage.

   Then append new cases directly after T3's block (after `assert_eq "T3d: ..." ...` and its trailing
   `> "$STUB_LOG"`), before the `# U: stage_plan` header:

   ```bash
   # T4: dispatch → pause → resume leaves the normal retry counter net-unchanged (#341
   # acceptance test)
   echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
   _drop_sig 83 refine "substantive:test_failure:1"
   _run_refine_body 83
   assert_eq "T4a: first (pre-pause) dispatch increments as normal" "1" "$(get_retry_count "83:refine")"
   _drop_sig 83 refine "environmental:session_window_pause"
   _run_refine_body 83
   assert_eq "T4b: rollback + this dispatch's own increment net to no change" "1" "$(get_retry_count "83:refine")"
   assert_eq "T4c: dispatched again (not skipped, just not double-counted)" \
     "2" "$(grep -c 'dispatch Refine issue #83' "$STUB_LOG" || echo 0)"
   assert_eq "T4d: comment carries the real session_window_pause_note() output" \
     "1" "$(grep -c 'was paused for a Claude session-window exhaustion' "$STUB_LOG" || echo 0)"

   > "$STUB_LOG"; echo '{}' > "$STATE_FILE"

   # T5: clamp at 0 — a pause observed with no prior increment does not go negative and
   # does not block the next dispatch
   _drop_sig 84 refine "environmental:session_window_pause"
   _run_refine_body 84
   assert_eq "T5: counter clamped at 0, not negative, after the new dispatch's own +1" \
     "1" "$(get_retry_count "84:refine")"

   > "$STUB_LOG"; echo '{}' > "$STATE_FILE"

   # T6: the drop file is consumed exactly once — a second read without a new pause does
   # not see a stale pause signature
   _drop_sig 85 refine "substantive:test_failure:1"
   _run_refine_body 85
   _drop_sig 85 refine "environmental:session_window_pause"
   _run_refine_body 85
   assert_eq "T6a: one observed pause nets the counter unchanged (2 dispatches, 1 rollback)" \
     "1" "$(get_retry_count "85:refine")"
   SIG_RESULT_T6=$(check_failure_signature "85" "refine")
   assert_eq "T6b: drop file consumed — second read returns no signature" \
     "1" "$(echo "$SIG_RESULT_T6" | grep -c 'sig=$')"

   > "$STUB_LOG"
   ```

2. Verify it fails — or rather, confirm it does NOT yet pass for the right reason. Unlike Task 4's
   step 2, this new test's body (`_run_refine_body`) is a hand-kept reproduction of `stage_refine()`'s
   logic, not a call into `stage_refine()` itself — so once `rollback_paused_retry` (Task 4) and
   `session_window_pause_note` (Task 5) are committed, T4/T5/T6 already pass here, before
   `scheduler.sh`'s real `stage_refine()` is touched at all. That is expected, not a sign step 3 is
   unnecessary: this step only confirms the *test double* is correct in isolation; Task 10 step 4's
   `grep -c "rollback_paused_retry \"\$ISSUE\""` count is what actually gates that `stage_refine()`
   itself got wired. Run it anyway to catch typos in the new assertions themselves, and confirm no
   *new* failures beyond the pre-existing `G2`/`I2` baseline noted in this plan's Tech Stack section:

   ```bash
   bash tests/test_scheduler.sh 2>&1 | grep -E "FAIL|command not found|Results"
   ```

3. Implement. In `scheduler.sh`'s `stage_refine()`, this:

   ```bash
       SIG_RESULT=$(check_failure_signature "$ISSUE" "refine")
       SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
       if echo "$SIG_RESULT" | grep -q "stuck=true"; then
         trip_to_blocked "$ISSUE" "refine" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
         continue
       fi

       PREV_DELIVERY_SKIP=""
       DECISION=$(retry_or_skip_delivery_failure "$ISSUE" "refine" "$SIG_VALUE" "${ISSUE}:refine" "$REFINE_MAX_RETRIES" || echo "count")
   ```

   becomes:

   ```bash
       SIG_RESULT=$(check_failure_signature "$ISSUE" "refine")
       SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
       if echo "$SIG_RESULT" | grep -q "stuck=true"; then
         trip_to_blocked "$ISSUE" "refine" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
         continue
       fi

       rollback_paused_retry "$ISSUE" "refine" "$SIG_VALUE" "${ISSUE}:refine" "$REFINE_MAX_RETRIES"

       PREV_SESSION_WINDOW_PAUSE=""
       [ "$SIG_VALUE" = "environmental:session_window_pause" ] && PREV_SESSION_WINDOW_PAUSE=1

       PREV_DELIVERY_SKIP=""
       DECISION=$(retry_or_skip_delivery_failure "$ISSUE" "refine" "$SIG_VALUE" "${ISSUE}:refine" "$REFINE_MAX_RETRIES" || echo "count")
   ```

   And this (the "Starting…" comment body):

   ```bash
       FOOTER=$(python3 "$FACTORY_CORE_CLI" marker scheduler)
       DELIVERY_NOTE=$(delivery_skip_note)
       gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body "🧠 **Refinement Pipeline** — Starting brainstorming and spec generation.${DELIVERY_NOTE}

   ---
   ${FOOTER}" 2>/dev/null || true
   ```

   becomes:

   ```bash
       FOOTER=$(python3 "$FACTORY_CORE_CLI" marker scheduler)
       DELIVERY_NOTE=$(delivery_skip_note)
       SESSION_WINDOW_NOTE=$(session_window_pause_note)
       gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body "🧠 **Refinement Pipeline** — Starting brainstorming and spec generation.${DELIVERY_NOTE}${SESSION_WINDOW_NOTE}

   ---
   ${FOOTER}" 2>/dev/null || true
   ```

4. Verify it passes:

   ```bash
   bash tests/test_scheduler.sh 2>&1 | tail -5
   ```

   Expected: no *new* failures beyond the pre-existing `G2`/`I2` pair (Tech Stack section above).

5. Commit:

   ```bash
   git add scheduler.sh tests/test_scheduler.sh
   git commit -m "fix(scheduler): roll back the retry counter for a confirmed pause in stage_refine (#341 Task 6)"
   ```

---

## Task 7: Wire `stage_plan`

**Files:** `scheduler.sh`, `tests/test_scheduler.sh`

### TDD steps

1. Update the failing test first. In `tests/test_scheduler.sh` section U, replace `_run_plan_body` in
   full:

   ```bash
   _run_plan_body() {
     local issue="$1"
     SIG_RESULT=$(check_failure_signature "$issue" "plan")
     SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
     if echo "$SIG_RESULT" | grep -q "stuck=true"; then
       trip_to_blocked "$issue" "plan" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
       return
     fi

     rollback_paused_retry "$issue" "plan" "$SIG_VALUE" "${issue}:plan" "$REFINE_MAX_RETRIES"

     PREV_SESSION_WINDOW_PAUSE=""
     [ "$SIG_VALUE" = "environmental:session_window_pause" ] && PREV_SESSION_WINDOW_PAUSE=1

     PREV_DELIVERY_SKIP=""
     DECISION=$(retry_or_skip_delivery_failure "$issue" "plan" "$SIG_VALUE" "${issue}:plan" "$REFINE_MAX_RETRIES" || echo "count")
     case "$DECISION" in
       skip) PREV_DELIVERY_SKIP=1 ;;
       trip:*) trip_to_blocked "$issue" "plan" "${DECISION#trip:}"; return ;;
       count|*)
         RETRIES=$(get_retry_count "${issue}:plan")
         if [ "$RETRIES" -ge "$REFINE_MAX_RETRIES" ]; then
           trip_to_blocked "$issue" "plan" "retry limit of ${REFINE_MAX_RETRIES} reached"
           return
         fi
         increment_retry "${issue}:plan"
         ;;
     esac

     DELIVERY_NOTE=""
     if [ -n "$PREV_DELIVERY_SKIP" ]; then
       DELIVERY_NOTE=" was not counted against the retry budget (runner-side delivery failure, #279)."
     fi
     SESSION_WINDOW_NOTE=$(session_window_pause_note)
     gh issue comment "$issue" --repo test/repo --body "Starting plan.${DELIVERY_NOTE}${SESSION_WINDOW_NOTE}" > /dev/null
     dispatch "Plan issue #${issue}" > /dev/null
   }
   ```

   Note this calls the real `session_window_pause_note()` from Task 5 (not an inline copy of its
   text), same as Task 6's `_run_refine_body` — U4c below asserts against the helper's actual output.

   Then append new cases directly after U3's block (after `assert_eq "U3c: ..." ...`), before the
   `> "$STUB_LOG"` that precedes the `# V: stage_blocked_retry` header:

   ```bash
   # U4: dispatch → pause → resume leaves the plan retry counter net-unchanged
   echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
   _drop_sig 93 plan "substantive:test_failure:1"
   _run_plan_body 93
   assert_eq "U4a: first (pre-pause) dispatch increments as normal" "1" "$(get_retry_count "93:plan")"
   _drop_sig 93 plan "environmental:session_window_pause"
   _run_plan_body 93
   assert_eq "U4b: rollback + this dispatch's own increment net to no change" "1" "$(get_retry_count "93:plan")"
   assert_eq "U4c: comment carries the real session_window_pause_note() output" \
     "1" "$(grep -c 'was paused for a Claude session-window exhaustion' "$STUB_LOG" || echo 0)"

   > "$STUB_LOG"
   ```

2. Verify it fails — same caveat as Task 6 step 2: `_run_plan_body` is a hand-kept reproduction of
   `stage_plan()`, so U4 already passes here once Tasks 4-5 are committed, before `stage_plan()` itself
   is touched. Run it anyway to catch assertion typos, and confirm no *new* failures beyond the
   pre-existing `G2`/`I2` baseline (Tech Stack section above) — Task 10 step 4's grep count is the real
   gate on `stage_plan()`'s own wiring:

   ```bash
   bash tests/test_scheduler.sh 2>&1 | grep -E "FAIL|command not found|Results"
   ```

3. Implement. In `scheduler.sh`'s `stage_plan()`, this:

   ```bash
       SIG_RESULT=$(check_failure_signature "$ISSUE" "plan")
       SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
       if echo "$SIG_RESULT" | grep -q "stuck=true"; then
         trip_to_blocked "$ISSUE" "plan" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
         continue
       fi

       PREV_DELIVERY_SKIP=""
       DECISION=$(retry_or_skip_delivery_failure "$ISSUE" "plan" "$SIG_VALUE" "${ISSUE}:plan" "$REFINE_MAX_RETRIES" || echo "count")
   ```

   becomes:

   ```bash
       SIG_RESULT=$(check_failure_signature "$ISSUE" "plan")
       SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
       if echo "$SIG_RESULT" | grep -q "stuck=true"; then
         trip_to_blocked "$ISSUE" "plan" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
         continue
       fi

       rollback_paused_retry "$ISSUE" "plan" "$SIG_VALUE" "${ISSUE}:plan" "$REFINE_MAX_RETRIES"

       PREV_SESSION_WINDOW_PAUSE=""
       [ "$SIG_VALUE" = "environmental:session_window_pause" ] && PREV_SESSION_WINDOW_PAUSE=1

       PREV_DELIVERY_SKIP=""
       DECISION=$(retry_or_skip_delivery_failure "$ISSUE" "plan" "$SIG_VALUE" "${ISSUE}:plan" "$REFINE_MAX_RETRIES" || echo "count")
   ```

   And this (the "Starting…" comment body):

   ```bash
       FOOTER=$(python3 "$FACTORY_CORE_CLI" marker scheduler)
       DELIVERY_NOTE=$(delivery_skip_note)
       gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body "📋 **Refinement Pipeline** — Starting plan generation and architect validation.${DELIVERY_NOTE}

   ---
   ${FOOTER}" 2>/dev/null || true
   ```

   becomes:

   ```bash
       FOOTER=$(python3 "$FACTORY_CORE_CLI" marker scheduler)
       DELIVERY_NOTE=$(delivery_skip_note)
       SESSION_WINDOW_NOTE=$(session_window_pause_note)
       gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body "📋 **Refinement Pipeline** — Starting plan generation and architect validation.${DELIVERY_NOTE}${SESSION_WINDOW_NOTE}

   ---
   ${FOOTER}" 2>/dev/null || true
   ```

4. Verify it passes:

   ```bash
   bash tests/test_scheduler.sh 2>&1 | tail -5
   ```

   Expected: no *new* failures beyond the pre-existing `G2`/`I2` pair (Tech Stack section above).

5. Commit:

   ```bash
   git add scheduler.sh tests/test_scheduler.sh
   git commit -m "fix(scheduler): roll back the retry counter for a confirmed pause in stage_plan (#341 Task 7)"
   ```

---

## Task 8: Wire `stage_blocked_retry` (implement)

**Files:** `scheduler.sh`, `tests/test_scheduler.sh`

### TDD steps

1. Update the failing test first. In `tests/test_scheduler.sh` section V, replace
   `_run_blocked_retry_body` in full:

   ```bash
   _run_blocked_retry_body() {
     local issue="$1"
     SIG_RESULT=$(check_failure_signature "$issue" "implement")
     SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
     if echo "$SIG_RESULT" | grep -q "stuck=true"; then
       trip_to_blocked "$issue" "implement" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
       return
     fi

     rollback_paused_retry "$issue" "implement" "$SIG_VALUE" "$issue" "$MAX_RETRIES"

     DECISION=$(retry_or_skip_delivery_failure "$issue" "implement" "$SIG_VALUE" "$issue" "$MAX_RETRIES" || echo "count")
     case "$DECISION" in
       skip) ;;
       trip:*) trip_to_blocked "$issue" "implement" "${DECISION#trip:}"; return ;;
       count|*)
         RETRIES=$(get_retry_count "$issue")
         if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
           trip_to_blocked "$issue" "implement" "retry limit of ${MAX_RETRIES} reached"
           return
         fi
         increment_retry "$issue"
         ;;
     esac

     if [ -n "$(get_pr_for_issue "$issue")" ]; then
       dispatch "Continue issue #${issue}" > /dev/null
     else
       dispatch "Fix issue #${issue}" > /dev/null
     fi
   }
   ```

   Then append new cases directly after V3's block (after `assert_eq "V3c: ..." ...` and the
   `> "$STUB_LOG"` reset that follows it), before the `# W: stage_conflict_resolve` header:

   ```bash
   # V4: dispatch → pause → resume leaves the implement (bare-key) retry counter net-unchanged
   echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
   _drop_sig 103 implement "substantive:test_failure:1"
   _run_blocked_retry_body 103
   assert_eq "V4a: first (pre-pause) dispatch increments as normal" "1" "$(get_retry_count "103")"
   _drop_sig 103 implement "environmental:session_window_pause"
   _run_blocked_retry_body 103
   assert_eq "V4b: rollback + this dispatch's own increment net to no change" "1" "$(get_retry_count "103")"
   assert_eq "V4c: dispatched again" "2" "$(grep -c 'dispatch Fix issue #103' "$STUB_LOG" || echo 0)"

   > "$STUB_LOG"; echo '{}' > "$STATE_FILE"
   ```

2. Verify it fails — same caveat as Task 6 step 2: `_run_blocked_retry_body` is a hand-kept
   reproduction of `stage_blocked_retry()`, so V4 already passes here once Task 4 is committed, before
   `stage_blocked_retry()` itself is touched. Run it anyway to catch assertion typos, and confirm no
   *new* failures beyond the pre-existing `G2`/`I2` baseline (Tech Stack section above) — Task 10
   step 4's grep count is the real gate on `stage_blocked_retry()`'s own wiring:

   ```bash
   bash tests/test_scheduler.sh 2>&1 | grep -E "FAIL|command not found|Results"
   ```

3. Implement. In `scheduler.sh`'s `stage_blocked_retry()`, this:

   ```bash
       SIG_RESULT=$(check_failure_signature "$ISSUE" "implement")
       SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
       if echo "$SIG_RESULT" | grep -q "stuck=true"; then
         trip_to_blocked "$ISSUE" "implement" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
         continue
       fi

       DECISION=$(retry_or_skip_delivery_failure "$ISSUE" "implement" "$SIG_VALUE" "$ISSUE" "$MAX_RETRIES" || echo "count")
   ```

   becomes:

   ```bash
       SIG_RESULT=$(check_failure_signature "$ISSUE" "implement")
       SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
       if echo "$SIG_RESULT" | grep -q "stuck=true"; then
         trip_to_blocked "$ISSUE" "implement" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
         continue
       fi

       rollback_paused_retry "$ISSUE" "implement" "$SIG_VALUE" "$ISSUE" "$MAX_RETRIES"

       DECISION=$(retry_or_skip_delivery_failure "$ISSUE" "implement" "$SIG_VALUE" "$ISSUE" "$MAX_RETRIES" || echo "count")
   ```

   No comment-note change here — `stage_blocked_retry` has no per-dispatch comment (matches #279's
   existing implement asymmetry; spec Requirement 7c).

4. Verify it passes:

   ```bash
   bash tests/test_scheduler.sh 2>&1 | tail -5
   ```

   Expected: no *new* failures beyond the pre-existing `G2`/`I2` pair (Tech Stack section above).

5. Commit:

   ```bash
   git add scheduler.sh tests/test_scheduler.sh
   git commit -m "fix(scheduler): roll back the retry counter for a confirmed pause in stage_blocked_retry (#341 Task 8)"
   ```

---

## Task 9: Wire `stage_conflict_resolve` (resolve)

**Files:** `scheduler.sh`, `tests/test_scheduler.sh`

### TDD steps

1. Update the failing test first. In `tests/test_scheduler.sh` section W, replace `_run_resolve_body`
   in full:

   ```bash
   _run_resolve_body() {
     local issue="$1"
     SIG_RESULT=$(check_failure_signature "$issue" "resolve")
     SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
     if echo "$SIG_RESULT" | grep -q "stuck=true"; then
       trip_to_blocked "$issue" "resolve" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
       return
     fi

     rollback_paused_retry "$issue" "resolve" "$SIG_VALUE" "${issue}:resolve" "$MAX_RETRIES"

     RESOLVE_DELIVERY_SKIP=""
     if [ "$SIG_VALUE" = "environmental:delivery_failure" ]; then
       DPEEK=$(get_retry_count "${issue}:resolve:delivery" || echo 0)
       if [ "$DPEEK" -ge "$MAX_RETRIES" ]; then
         STATE_FILE="$STATE_FILE" python3 "$FACTORY_CORE_CLI" breaker-set-retry --key "${issue}:resolve" --value "$DPEEK"
         trip_to_blocked "$issue" "resolve" "same failure signature 'environmental:delivery_failure' recorded ${DPEEK} consecutive times (suspected runner prompt-delivery bug — see #279), retry budget exhausted"
         return
       fi
       RESOLVE_DELIVERY_SKIP=1
     else
       RETRIES=$(get_retry_count "${issue}:resolve")
       if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
         trip_to_blocked "$issue" "resolve" "retry limit of ${MAX_RETRIES} reached for conflict resolution"
         return
       fi
     fi

     PR_NUM=$(get_pr_for_issue "$issue")
     [ -z "$PR_NUM" ] && return

     MERGEABLE=$(check_pr_mergeable "$PR_NUM")
     case "$MERGEABLE" in
       CONFLICTING)
         if [ -n "$RESOLVE_DELIVERY_SKIP" ]; then
           increment_retry "${issue}:resolve:delivery" > /dev/null || true
         else
           increment_retry "${issue}:resolve" || true
         fi
         dispatch "Deconflict issue #${issue}" > /dev/null
         ;;
     esac
   }
   ```

   Then append new cases directly after W5's block (after `assert_eq "W5b: ..." ...`), before the
   `# ==========================================` / `# Cleanup` header:

   ```bash
   # W6: dispatch → pause → resume leaves the resolve retry counter net-unchanged (resolve's
   # ceiling-check/increment are not adjacent, unlike the other three sites — the rollback
   # must still land at the shared checkpoint)
   > "$STUB_LOG"; echo '{}' > "$STATE_FILE"
   _drop_sig 115 resolve "substantive:test_failure:1"
   _run_resolve_body 115
   assert_eq "W6a: first (pre-pause) dispatch increments as normal" "1" "$(get_retry_count "115:resolve")"
   _drop_sig 115 resolve "environmental:session_window_pause"
   _run_resolve_body 115
   assert_eq "W6b: rollback + this dispatch's own increment net to no change" "1" "$(get_retry_count "115:resolve")"
   assert_eq "W6c: dispatched again" "2" "$(grep -c 'dispatch Deconflict issue #115' "$STUB_LOG" || echo 0)"

   > "$STUB_LOG"; echo '{}' > "$STATE_FILE"

   # W7: clamp at 0 for resolve
   _drop_sig 116 resolve "environmental:session_window_pause"
   _run_resolve_body 116
   assert_eq "W7: counter clamped at 0, not negative, after the new dispatch's own +1" \
     "1" "$(get_retry_count "116:resolve")"

   > "$STUB_LOG"
   ```

2. Verify it fails — same caveat as Task 6 step 2: `_run_resolve_body` is a hand-kept reproduction of
   `stage_conflict_resolve()`, so W6/W7 already pass here once Task 4 is committed, before
   `stage_conflict_resolve()` itself is touched. Run it anyway to catch assertion typos, and confirm no
   *new* failures beyond the pre-existing `G2`/`I2` baseline (Tech Stack section above) — Task 10
   step 4's grep count is the real gate on `stage_conflict_resolve()`'s own wiring:

   ```bash
   bash tests/test_scheduler.sh 2>&1 | grep -E "FAIL|command not found|Results"
   ```

3. Implement. In `scheduler.sh`'s `stage_conflict_resolve()`, this:

   ```bash
       SIG_RESULT=$(check_failure_signature "$ISSUE" "resolve")
       SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
       if echo "$SIG_RESULT" | grep -q "stuck=true"; then
         trip_to_blocked "$ISSUE" "resolve" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
         continue
       fi

       # #279: the delivery-failure exemption's accounting (the "<key>:delivery" shadow
   ```

   becomes:

   ```bash
       SIG_RESULT=$(check_failure_signature "$ISSUE" "resolve")
       SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
       if echo "$SIG_RESULT" | grep -q "stuck=true"; then
         trip_to_blocked "$ISSUE" "resolve" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
         continue
       fi

       rollback_paused_retry "$ISSUE" "resolve" "$SIG_VALUE" "${ISSUE}:resolve" "$MAX_RETRIES"

       # #279: the delivery-failure exemption's accounting (the "<key>:delivery" shadow
   ```

   No comment-note change here either — `stage_conflict_resolve` has no per-dispatch comment (spec
   Requirement 7c).

4. Verify it passes:

   ```bash
   bash tests/test_scheduler.sh 2>&1 | tail -5
   ```

   Expected: no *new* failures beyond the pre-existing `G2`/`I2` pair (Tech Stack section above).

5. Commit:

   ```bash
   git add scheduler.sh tests/test_scheduler.sh
   git commit -m "fix(scheduler): roll back the retry counter for a confirmed pause in stage_conflict_resolve (#341 Task 9)"
   ```

---

## Task 10: Full verification pass

**Files:** none (verification only)

1. Run the full python suite:

   ```bash
   cd /workspace/dark-factory
   PYTHONPATH=scripts python -m pytest tests/ -v
   ```

   Expected: all pass, including the two new/extended tests in
   `tests/test_factory_core_error_signature.py`.

2. Run the two local-only bash suites this ticket touches (both `bash -n`-clean and functionally
   green — **note for the record**: `tests/test_scheduler.sh` is not wired into `.github/workflows/ci.yml` (pre-existing gap, same as the #279 tests it mirrors); `tests/test_entrypoint_session_window.sh` IS run by CI's tests job since PR #357, so Task 3's assertions get CI coverage, not something this ticket introduces or
   is expected to fix):

   ```bash
   bash -n scheduler.sh
   bash -n entrypoint.sh
   bash tests/test_scheduler.sh 2>&1 | tail -5
   bash tests/test_entrypoint_session_window.sh 2>&1 | tail -5
   ```

   Expected: `bash tests/test_entrypoint_session_window.sh` reports literal `0 failed`.
   `bash tests/test_scheduler.sh` reports exactly 2 failures — `G2: advance: set_board_status REFINED`
   and `I2: advance: set_board_status READY` — which are the pre-existing, unrelated baseline documented
   in this plan's Tech Stack section (confirmed present on `main` before this ticket's changes); any
   *other* failure is a real regression from this ticket's work and must be fixed before proceeding.

3. Run the DAG/when checks (no `workflows/*.yaml` change is made by this ticket, but confirm no
   accidental drift):

   ```bash
   python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
   python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
   ```

4. Grep-confirm the four call sites and both new helpers are wired exactly once each:

   ```bash
   grep -c "rollback_paused_retry \"\$ISSUE\"" scheduler.sh   # expect 4
   grep -c "^rollback_paused_retry()" scheduler.sh            # expect 1
   grep -c "^session_window_pause_note()" scheduler.sh        # expect 1
   grep -c "session_window_pause_note)" scheduler.sh          # expect 2 (stage_plan + stage_refine)
   ```

5. No commit for this task — it's a verification gate over Tasks 1-9's already-committed work. If any
   check fails, fix the specific task above and re-commit there rather than adding a catch-all fixup
   commit here.

---

## Notes for the conformance/architect reviewers

- All four call sites use the exact bash from the spec's Architecture section; no deviation.
- `stage_conflict_resolve`'s rollback call is unconditional and un-deferred (Task 9), unlike its
  delivery-failure exemption's two-step peek-then-increment shape — per spec Q&A, the rollback
  corrects *history* (already happened), not a *future* dispatch decision, so it doesn't inherit that
  site's deferral.
- No `config.yaml` change, no new `breaker.py` function, no `reset_retry` change — matches spec
  Requirement 8 exactly (this ticket adds no shadow counter for `reset_retry` to pop).
- Task 3 fixes one pre-existing test assertion in `tests/test_entrypoint_session_window.sh` (section
  D) that the spec's Requirement 5 makes stale; this is called out explicitly so it isn't mistaken for
  scope creep — it is the direct, unavoidable consequence of Requirement 5's drop-file write.
- This plan does not add a task copying `docs/superpowers/specs/*.md` / `docs/superpowers/plans/*.md`
  from this `refine/issue-341-*` branch onto the eventual `feat/issue-341-*` branch — per the `#42`
  memory PATTERN, that copy is the **implement phase's** responsibility (it runs there, on the
  feature branch, not here), not a plan-phase task.
