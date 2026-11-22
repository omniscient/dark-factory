# Close-dispatch guard + close-phase breaker — Implementation Plan

**Issue:** #414

**Revised:** 2026-11-22 (operator amendment after plan gate review)

> **For agentic workers:** Execute task-by-task, in order. Each task is TDD: write the failing
> test, watch it fail with the exact output shown, implement, watch it pass, commit. Touch only
> the eight files in the File Structure table.

**Goal:** Stop the unguarded, unbudgeted `Close issue #N` loop. Both Close-dispatch sites in
`scheduler.sh` (`stage_review_triage`'s `MERGE` case and `end_gate_check`'s `APPROVED` case)
must skip while a container for the issue is running, and must count against one shared
`close`-phase circuit breaker (ceiling `$MAX_RETRIES`) that trips the ticket to Blocked
instead of re-dispatching forever.

**Architecture:** One new helper, `dispatch_close <issue_num>`, defined in `scheduler.sh`
directly after `end_gate_check()` and before `# --- Dispatch ---`. It runs
`is_issue_running` → `evaluate_stop "$issue_num" "close" "$MAX_RETRIES"` (non-peek, so it
increments `N:close` itself) → `trip_to_blocked … close …` on `stopped=true`, else
`dispatch "Close issue #N"`. Both Close sites call it; neither calls `reset_retry`.
`breaker.py::trip_to_blocked` gains a `"close"` entry in `retry_cmds` so the trip comment's
re-run command is `Close issue #N`, not the `Fix issue #N` default. No new config knob, no
state-file schema change, no new CLI surface. Spec:
`docs/superpowers/specs/2026-11-22-close-dispatch-breaker-guard-design.md`.

**Tech Stack:** bash (`scheduler.sh`, `tests/test_scheduler.sh`), Python 3
(`scripts/factory_core/breaker.py`, pytest).

**Owner acknowledgement (from the spec):** this also guards the direct-to-pr end-gate auto-merge
dispatch, which the issue does not name; tighten-only; the PR is held for the owner's explicit
acknowledgement before merge. The implement agent does not write the PR description (it is a
fixed template in the workflow's `push-and-pr` node), so carry this sentence in the Task 4
commit message body and in the decisions bullet of `$ARTIFACTS_DIR/implementation.md`; the
operator adds it to the PR description before merge.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `scripts/factory_core/breaker.py` | Modify (1 line, `trip_to_blocked`'s `retry_cmds`) | Correct re-run command in a `close` trip comment |
| `tests/test_factory_core_breaker.py` | Modify (append 1 test after `test_trip_to_blocked_adds_both_labels`) | CI-run coverage: `close` trip comment + `N:close` cleared (spec test 7) |
| `scheduler.sh` | Modify (new `dispatch_close()`; `MERGE` case; `APPROVED` case; `trip_to_blocked` usage comment) | Guard + breaker for both Close sites |
| `tests/test_scheduler.sh` | Modify (new section `AB`, inserted directly above the `# Cleanup` section) | Spec tests 1-6 and 8, fail-open check, drift lock |
| `docs/scheduler.md` | Modify (line citations re-mapped; known-issue item 13; `N:close` counter row) | Reference doc stays true of the code (Task 5b) |
| `docs/features.md` | Modify (line citations re-mapped only) | Task 5b |
| `docs/configuration.md` | Modify (line citations re-mapped only) | Task 5b |
| `docs/run-lifecycle.md` | Modify (line citations re-mapped; `Close issue #N` row) | Task 5b |

Nothing else changes. In particular: `CONTINUE` and `CHANGES_REQUESTED` are untouched; no
`check_failure_signature` for `close` (spec Alternative 4, deferred); no config or workflow
edits; `deploy/**` and `.github/workflows/**` are never touched. The docs changes are exactly
those of Task 5b (mandatory): `dispatch_close` inserts 20 lines at `scheduler.sh:357` and Task 1
inserts one at `breaker.py:350`, which shifts about 190 line citations in the four reference
docs; `docs_refs_check.py` checks ranges only and would not notice.

## Facts the steps rely on (verified against this checkout)

- `tests/test_scheduler.sh` sources `scheduler.sh`, which runs `set -euo pipefail` — so
  `set -e` is **live in the test runner**. A bare call to a function that returns non-zero
  aborts the suite. Every return-code capture in this plan therefore uses
  `_AB_RC=0; f || _AB_RC=$?`, never `f; _AB_RC=$?`.
- The `# Cleanup` section near the end of `tests/test_scheduler.sh` deletes `$STATE_FILE`
  and `$STUB_LOG`, so the new section must be inserted **above** it (after section `Z`).
- A full `bash tests/test_scheduler.sh` run takes ~60 s. Give the Bash tool a 300000 ms timeout.
- **Baseline on this branch is not fully green:** `bash tests/test_scheduler.sh` reports
  `Results: 265 passed, 2 failed`, the two being
  `FAIL: G2: advance: set_board_status REFINED` and `FAIL: I2: advance: set_board_status READY`.
  They are pre-existing and unrelated (spec/plan auto-advance, sections G/I). Do not fix them
  here. They still appear in every "expected output" below; any *other* `FAIL:` line is a
  regression.
- `tests/test_scheduler.sh` is not CI-gated (`.github/workflows/ci.yml` runs only
  `tests/test_scheduler_lib.sh` among the scheduler shell tests). The pytest in Task 1 is the
  CI-executed coverage for this ticket; run the shell suite by hand.
- The existing drift lock `evaluate_stop wired 4x in scheduler.sh` greps
  `evaluate_stop "$ISSUE"`. `dispatch_close` passes `"$issue_num"`, so the count stays 4 — keep it that way.
- Section `J` (`J1: APPROVED → Close dispatched`) keeps passing after the change: at that
  point `is_issue_running` is the real function (the docker stub prints nothing → "not
  running") and the real `evaluate_stop` sees a fresh `{}` state file, so the first Close is
  under the ceiling and dispatches.
- If `python -m pytest` reports `No module named pytest`, install what CI installs:
  `pip install pytest pyyaml aiohttp` (or `python3 -m pip install --user pytest pyyaml aiohttp`).

---

## Task 1: `close` entry in `breaker.py::trip_to_blocked`'s `retry_cmds`

**Files:**
- Modify: `tests/test_factory_core_breaker.py` (append a test after `test_trip_to_blocked_adds_both_labels`)
- Modify: `scripts/factory_core/breaker.py` (`trip_to_blocked`, the `retry_cmds` dict)

- [ ] **Step 1: Write the failing test.** In `tests/test_factory_core_breaker.py`, find the end of
  `test_trip_to_blocked_adds_both_labels`:

```python
    assert any("needs-discussion" in c for c in edit_cmds)
    assert any("factory-regression" in c for c in edit_cmds)
```

  and insert directly after those two lines (two blank lines, then the new test — every name it
  uses, `subprocess`, `patch`, `increment_retry`, `get_retry_count`, `trip_to_blocked`, is
  already imported at the top of the file):

```python
def test_trip_to_blocked_close_phase_retry_cmd_and_clear(tmp_path, monkeypatch):
    # #414: a close-phase trip must name the Close workflow in its re-run command
    # (not the default `Fix issue #N`) and clear the N:close counter it tripped on.
    sf = tmp_path / "state.json"
    for _ in range(3):
        increment_retry("42:close", sf)
    calls = []
    monkeypatch.setattr(subprocess, "run",
        lambda cmd, **kw: (calls.append(cmd),
                           subprocess.CompletedProcess([], 0, stdout="", stderr=""))[1])
    with patch("factory_core.board.set_board_status"):
        trip_to_blocked(42, "close", "retry limit of 3 reached for close/merge", sf)
    bodies = [c[c.index("--body") + 1] for c in calls if "comment" in c and "--body" in c]
    assert len(bodies) == 1
    assert '"Close issue #42"' in bodies[0]
    assert "Fix issue #42" not in bodies[0]
    assert "(`close`)" in bodies[0]
    assert get_retry_count("42:close", sf) == 0
```

- [ ] **Step 2: Run it — must fail.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_factory_core_breaker.py -q -k close_phase
```

  Expected: `1 failed, 62 deselected`, with an `AssertionError` on the
  `'"Close issue #42"' in bodies[0]` line — the posted body ends in
  `dark-factory "Fix issue #42"` because `close` currently falls back to the default re-run command.

- [ ] **Step 3: Implement.** In `scripts/factory_core/breaker.py`, `trip_to_blocked`, change

```python
    retry_cmds = {
        "refine": f"Refine issue #{issue_num}",
        "plan": f"Plan issue #{issue_num}",
        "resolve": f"Deconflict issue #{issue_num}",
    }
```

  to

```python
    retry_cmds = {
        "refine": f"Refine issue #{issue_num}",
        "plan": f"Plan issue #{issue_num}",
        "resolve": f"Deconflict issue #{issue_num}",
        "close": f"Close issue #{issue_num}",
    }
```

  Nothing else in `breaker.py` changes — `trip_to_blocked` already ends with
  `reset_retry(key, state_file)` where `key = _make_key(issue_num, "close") == "42:close"`,
  which is what clears the counter on trip.

- [ ] **Step 4: Run it — must pass, and the rest of the file stays green.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_factory_core_breaker.py -q
```

  Expected: `63 passed`.

- [ ] **Step 5: Commit.**

```bash
git add scripts/factory_core/breaker.py tests/test_factory_core_breaker.py
git commit -m "fix(breaker): close-phase trip comment re-runs Close issue #N, not Fix (#414)"
```

---

## Task 2: `dispatch_close()` helper (guard → close breaker → dispatch)

**Files:**
- Modify: `tests/test_scheduler.sh` (new section `AB` above `# Cleanup`: stubs, AB7, AB8, restore)
- Modify: `scheduler.sh` (new function after `end_gate_check()`)

- [ ] **Step 1: Write the failing tests.** In `tests/test_scheduler.sh`, find the Cleanup section
  header (it directly follows section `Z`'s final `export -f get_pr_for_issue` line and a blank line):

```bash
# ==========================================
# Cleanup
# ==========================================
```

  Insert the following block **immediately above** that header's first `# ===…` line, keeping
  the blank line at the end of the block. Tasks 3 and 4 insert more tests into this section
  directly above the `# AB7:` comment; Task 5 adds the drift lock directly above `# Cleanup`.

```bash
# ==========================================
# AB: Close-dispatch guard + shared close-phase breaker (#414)
# ==========================================
# Both Close sites — stage_review_triage's MERGE case and end_gate_check's APPROVED
# case — route through dispatch_close (is_issue_running → evaluate_stop close →
# trip_to_blocked | dispatch). AB1-AB7 stub the breaker adapters and log every call;
# AB8 runs the real evaluate_stop against $STATE_FILE. scheduler.sh's `set -e` is live in
# this runner, so non-zero returns are captured as `_AB_RC=0; f || _AB_RC=$?`.
echo ""
echo "--- AB: dispatch_close — guard + close-phase breaker at both Close sites ---"
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
MAIN_IS_RED=false; SESSION_WINDOW_PAUSED=false; CI_BLOCKED=""
_AB_REAL_EVALUATE_STOP=$(declare -f evaluate_stop)
_AB_REAL_TRIP_TO_BLOCKED=$(declare -f trip_to_blocked)
_AB_REAL_RESET_RETRY=$(declare -f reset_retry)
# Generic python3 stub (same body section Y restores): end_gate_check reads the PR
# review state from PROVIDERS_CLI_OUTPUT via the providers CLI.
python3() {
  echo "python3 $*" >> "$STUB_LOG"
  case "$*" in
    *providers/cli.py*) [ -n "$PROVIDERS_CLI_OUTPUT" ] && printf '%s\n' "$PROVIDERS_CLI_OUTPUT"; return 0 ;;
    *) "$_REAL_PY3" "$@" ;;
  esac
}
AB_EVAL_OUT="stopped=false reason=none"
evaluate_stop()     { echo "evaluate_stop $*" >> "$STUB_LOG"; echo "$AB_EVAL_OUT"; }
trip_to_blocked()   { echo "trip_to_blocked $*" >> "$STUB_LOG"; }
reset_retry()       { echo "reset_retry $*" >> "$STUB_LOG"; }
dispatch()          { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
get_new_comments()  { echo '[{"id":"IC_414","author":{"login":"human"},"body":"ship it"}]'; }
classify_comments() { echo "classify_comments $1" >> "$STUB_LOG"; echo "MERGE"; }
get_pr_for_issue()  { echo "99"; }
is_issue_running()  { return 1; }
export -f python3 evaluate_stop trip_to_blocked reset_retry dispatch \
  get_new_comments classify_comments get_pr_for_issue is_issue_running

_AB_MERGE_711='[{"content":{"number":711,"title":"t"},"labels":[],"status":"In review"}]'
_AB_DTP_712='[{"content":{"number":712,"title":"t"},"labels":["direct-to-pr"],"status":"In review"}]'
_AB_DTP_712_ITEM=$(echo "$_AB_DTP_712" | jq -c '.[0]')
# AB7: evaluate_stop CLI error (empty output) → fails open and dispatches, same as the
# existing implement/plan/refine sites
> "$STUB_LOG"
AB_EVAL_OUT=""
_AB_RC=0; dispatch_close 717 || _AB_RC=$?
assert_eq "AB7a: empty evaluate_stop output → Close dispatched (fail open)" \
  "1" "$(grep -c 'dispatch Close issue #717' "$STUB_LOG" || true)"
assert_eq "AB7b: dispatch_close returns dispatch's exit code" "0" "$_AB_RC"
AB_EVAL_OUT="stopped=false reason=none"

# AB8: real evaluate_stop against $STATE_FILE (trip_to_blocked still stubbed) — three
# dispatches count on N:close, the fourth trips and does not dispatch
> "$STUB_LOG"; echo '{}' > "$STATE_FILE"
eval "$_AB_REAL_EVALUATE_STOP"; export -f evaluate_stop
for _ab_i in 1 2 3; do dispatch_close 718 || true; done
assert_eq "AB8a: three dispatch_close calls under the ceiling dispatch" \
  "3" "$(grep -c 'dispatch Close issue #718' "$STUB_LOG" || true)"
assert_eq "AB8b: attempts counted on the 718:close key" "3" "$(get_retry_count "718:close")"
assert_eq "AB8c: bare implement key untouched" "0" "$(get_retry_count "718")"
> "$STUB_LOG"
_AB_RC=0; dispatch_close 718 || _AB_RC=$?
assert_eq "AB8d: 4th call trips with phase close" \
  "1" "$(grep -c '^trip_to_blocked 718 close retry limit of 3 reached for close/merge$' "$STUB_LOG" || true)"
assert_eq "AB8e: no dispatch on trip" \
  "0" "$(grep -c 'dispatch Close issue #718' "$STUB_LOG" || true)"
assert_eq "AB8f: dispatch_close returns non-zero on trip" "1" "$_AB_RC"

# Restore stubs to section defaults
eval "$_AB_REAL_TRIP_TO_BLOCKED"; eval "$_AB_REAL_RESET_RETRY"
export -f evaluate_stop trip_to_blocked reset_retry
reset_python3_stub
get_pr_for_issue() { echo ""; }
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
is_issue_running() { return 1; }
export -f get_pr_for_issue dispatch is_issue_running
> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

```

- [ ] **Step 2: Run — must fail.**

```bash
bash tests/test_scheduler.sh 2>&1 | grep -E "FAIL|Results|not found"
```

  Expected (exit status 1; `NNNN` is a line number):

```
  FAIL: G2: advance: set_board_status REFINED — expected='1' got=''
  FAIL: I2: advance: set_board_status READY — expected='1' got=''
tests/test_scheduler.sh: line NNNN: dispatch_close: command not found
  FAIL: AB7a: empty evaluate_stop output → Close dispatched (fail open) — expected='1' got='0'
  FAIL: AB7b: dispatch_close returns dispatch's exit code — expected='0' got='127'
tests/test_scheduler.sh: line NNNN: dispatch_close: command not found      (×3)
  FAIL: AB8a: three dispatch_close calls under the ceiling dispatch — expected='3' got='0'
  FAIL: AB8b: attempts counted on the 718:close key — expected='3' got='0'
tests/test_scheduler.sh: line NNNN: dispatch_close: command not found
  FAIL: AB8d: 4th call trips with phase close — expected='1' got='0'
  FAIL: AB8f: dispatch_close returns non-zero on trip — expected='1' got='127'
Results: 267 passed, 8 failed
```

- [ ] **Step 3: Implement.** In `scheduler.sh`, find the end of `end_gate_check()` and the
  `# --- Dispatch ---` header that follows it:

```bash
    *)
      return 1
      ;;
  esac
}

# --- Dispatch ---
```

  Replace it with (adds the helper between the two; the function body is the spec's
  `dispatch_close` verbatim):

```bash
    *)
      return 1
      ;;
  esac
}

# --- Close dispatch: running-issue guard + close-phase circuit breaker (#414) ---
# The only place `Close issue #N` is dispatched — stage_review_triage's MERGE case and
# end_gate_check's APPROVED case both call it, so the two paths share one guard and one
# `N:close` counter (ceiling MAX_RETRIES). No reset_retry: the count is cleared only by
# trip_to_blocked. Returns non-zero when skipped or tripped, else dispatch's exit code —
# callers MUST use `if dispatch_close ...; then`.
dispatch_close() {
  local issue_num="$1"
  is_issue_running "$issue_num" && return 1
  local eval_result
  eval_result=$(evaluate_stop "$issue_num" "close" "$MAX_RETRIES")
  case "$eval_result" in
    *stopped=true*)
      trip_to_blocked "$issue_num" "close" "retry limit of ${MAX_RETRIES} reached for close/merge"
      return 1
      ;;
  esac
  dispatch "Close issue #${issue_num}"
}

# --- Dispatch ---
```

  Fail-open (spec Requirement 3): if `evaluate_stop` prints nothing (CLI error), the `case`
  matches nothing and the helper dispatches — the same behavior as the existing
  `implement`/`plan`/`refine` sites. AB7 pins this.

- [ ] **Step 4: Run — must pass.**

```bash
bash -n scheduler.sh && bash tests/test_scheduler.sh 2>&1 | grep -E "FAIL|AB|Results"
```

  Expected: AB7a, AB7b, AB8a–AB8f all `PASS`; the only `FAIL:` lines are G2 and I2;
  `Results: 273 passed, 2 failed`.

- [ ] **Step 5: Commit.**

```bash
git add scheduler.sh tests/test_scheduler.sh
git commit -m "feat(scheduler): dispatch_close — running guard + close-phase breaker (#414)"
```

---

## Task 3: Route `stage_review_triage`'s `MERGE` case through `dispatch_close`

**Files:**
- Modify: `tests/test_scheduler.sh` (section `AB`, above `# AB7:`)
- Modify: `scheduler.sh` (`stage_review_triage`, `MERGE)` case)

- [ ] **Step 1: Write the failing tests** (spec tests 1, 3, and the MERGE half of 5). In
  `tests/test_scheduler.sh` section `AB`, insert this block **immediately above** the line
  `# AB7: evaluate_stop CLI error (empty output) → fails open and dispatches, same as the`
  (keep the trailing blank line):

```bash
# AB1: MERGE verdict while a container for the issue is running → skipped before the
# breaker is consulted (no dispatch, no close attempt consumed)
> "$STUB_LOG"
is_issue_running() { return 0; }; export -f is_issue_running
IN_REVIEW="$_AB_MERGE_711"; DISPATCHED=""; PROVIDERS_CLI_OUTPUT=""
stage_review_triage
assert_eq "AB1a: MERGE + running → no Close dispatch" \
  "0" "$(grep -c 'dispatch Close issue #711' "$STUB_LOG" || true)"
assert_eq "AB1b: MERGE + running → breaker not consulted" \
  "0" "$(grep -c '^evaluate_stop 711' "$STUB_LOG" || true)"
assert_eq "AB1c: MERGE + running → DISPATCHED stays empty" "" "$DISPATCHED"
is_issue_running() { return 1; }; export -f is_issue_running

# AB3: MERGE verdict at the ceiling → trips with phase close, no dispatch
> "$STUB_LOG"
AB_EVAL_OUT="stopped=true reason=max_retries"
IN_REVIEW="$_AB_MERGE_711"; DISPATCHED=""; PROVIDERS_CLI_OUTPUT=""
stage_review_triage
assert_eq "AB3a: MERGE consults the close counter at MAX_RETRIES" \
  "1" "$(grep -c '^evaluate_stop 711 close 3$' "$STUB_LOG" || true)"
assert_eq "AB3b: MERGE at ceiling → trip_to_blocked phase close" \
  "1" "$(grep -c '^trip_to_blocked 711 close retry limit of 3 reached for close/merge$' "$STUB_LOG" || true)"
assert_eq "AB3c: MERGE at ceiling → no Close dispatch" \
  "0" "$(grep -c 'dispatch Close issue #711' "$STUB_LOG" || true)"
assert_eq "AB3d: MERGE at ceiling → DISPATCHED stays empty" "" "$DISPATCHED"
AB_EVAL_OUT="stopped=false reason=none"

# AB5 (MERGE half): under the ceiling → Close dispatched, DISPATCHED set
> "$STUB_LOG"
IN_REVIEW="$_AB_MERGE_711"; DISPATCHED=""; PROVIDERS_CLI_OUTPUT=""
stage_review_triage
assert_eq "AB5a: MERGE under ceiling → Close dispatched" \
  "1" "$(grep -c 'dispatch Close issue #711' "$STUB_LOG" || true)"
assert_eq "AB5b: MERGE under ceiling → DISPATCHED set" "Close issue #711" "$DISPATCHED"
assert_eq "AB5c: MERGE dispatch never calls reset_retry" \
  "0" "$(grep -c '^reset_retry' "$STUB_LOG" || true)"

```

- [ ] **Step 2: Run — must fail.**

```bash
bash tests/test_scheduler.sh 2>&1 | grep -E "FAIL|Results"
```

  Expected:

```
  FAIL: G2: advance: set_board_status REFINED — expected='1' got=''
  FAIL: I2: advance: set_board_status READY — expected='1' got=''
  FAIL: AB1a: MERGE + running → no Close dispatch — expected='0' got='1'
  FAIL: AB1c: MERGE + running → DISPATCHED stays empty — expected='' got='Close issue #711'
  FAIL: AB3a: MERGE consults the close counter at MAX_RETRIES — expected='1' got='0'
  FAIL: AB3b: MERGE at ceiling → trip_to_blocked phase close — expected='1' got='0'
  FAIL: AB3c: MERGE at ceiling → no Close dispatch — expected='0' got='1'
  FAIL: AB3d: MERGE at ceiling → DISPATCHED stays empty — expected='' got='Close issue #711'
Results: 277 passed, 8 failed
```

- [ ] **Step 3: Implement.** In `scheduler.sh`, `stage_review_triage`, change

```bash
      MERGE)
        if dispatch "Close issue #${ISSUE}"; then
          DISPATCHED="Close issue #${ISSUE}"
        fi
        ;;
```

  to

```bash
      MERGE)
        if dispatch_close "$ISSUE"; then
          DISPATCHED="Close issue #${ISSUE}"
        fi
        ;;
```

  Leave `CONTINUE)` and `SKIP)` exactly as they are; no `reset_retry` is added to `MERGE`.

- [ ] **Step 4: Run — must pass.**

```bash
bash tests/test_scheduler.sh 2>&1 | grep -E "FAIL|Results"
```

  Expected: only G2 and I2 fail; `Results: 283 passed, 2 failed`.

- [ ] **Step 5: Commit.**

```bash
git add scheduler.sh tests/test_scheduler.sh
git commit -m "fix(scheduler): MERGE verdict dispatches Close via dispatch_close (#414)"
```

---

## Task 4: Route `end_gate_check`'s `APPROVED` case through `dispatch_close`

**Files:**
- Modify: `tests/test_scheduler.sh` (section `AB`, above `# AB7:`, after Task 3's block)
- Modify: `scheduler.sh` (`end_gate_check`, `APPROVED)` case)

- [ ] **Step 1: Write the failing tests** (spec tests 2, 4, 6, and the APPROVED half of 5 —
  test 5's "zero `reset_retry` calls across both" is AB5c + AB5f). Insert this block
  **immediately above** the line
  `# AB7: evaluate_stop CLI error (empty output) → fails open and dispatches, same as the`
  (that is, after Task 3's AB5c assertion; keep the trailing blank line):

```bash
# AB2: APPROVED end-gate while a container for the issue is running → skipped, still
# returns 0, breaker not consulted
> "$STUB_LOG"
is_issue_running() { return 0; }; export -f is_issue_running
DISPATCHED=""; PROVIDERS_CLI_OUTPUT="APPROVED"
_AB_RC=0; end_gate_check 712 "$_AB_DTP_712_ITEM" || _AB_RC=$?
assert_eq "AB2a: APPROVED + running → end_gate_check still returns 0" "0" "$_AB_RC"
assert_eq "AB2b: APPROVED + running → no Close dispatch" \
  "0" "$(grep -c 'dispatch Close issue #712' "$STUB_LOG" || true)"
assert_eq "AB2c: APPROVED + running → breaker not consulted" \
  "0" "$(grep -c '^evaluate_stop 712' "$STUB_LOG" || true)"
is_issue_running() { return 1; }; export -f is_issue_running

# AB4: APPROVED end-gate at the ceiling → trips with phase close, returns 0, and
# stage_review_triage never falls through to comment classification
> "$STUB_LOG"
AB_EVAL_OUT="stopped=true reason=max_retries"
IN_REVIEW="$_AB_DTP_712"; DISPATCHED=""; PROVIDERS_CLI_OUTPUT="APPROVED"
stage_review_triage
assert_eq "AB4a: APPROVED at ceiling → trip_to_blocked phase close" \
  "1" "$(grep -c '^trip_to_blocked 712 close retry limit of 3 reached for close/merge$' "$STUB_LOG" || true)"
assert_eq "AB4b: APPROVED at ceiling → no Close dispatch" \
  "0" "$(grep -c 'dispatch Close issue #712' "$STUB_LOG" || true)"
assert_eq "AB4c: APPROVED at ceiling → no fall-through to classify_comments" \
  "0" "$(grep -c '^classify_comments 712' "$STUB_LOG" || true)"
_AB_RC=0; end_gate_check 712 "$_AB_DTP_712_ITEM" || _AB_RC=$?
assert_eq "AB4d: APPROVED at ceiling → end_gate_check still returns 0" "0" "$_AB_RC"
AB_EVAL_OUT="stopped=false reason=none"

# AB5 (APPROVED half): under the ceiling → Close dispatched, no reset_retry
> "$STUB_LOG"
IN_REVIEW="$_AB_DTP_712"; DISPATCHED=""; PROVIDERS_CLI_OUTPUT="APPROVED"
stage_review_triage
assert_eq "AB5d: APPROVED under ceiling → Close dispatched" \
  "1" "$(grep -c 'dispatch Close issue #712' "$STUB_LOG" || true)"
assert_eq "AB5e: APPROVED under ceiling → DISPATCHED set" "Close issue #712" "$DISPATCHED"
assert_eq "AB5f: APPROVED dispatch never calls reset_retry" \
  "0" "$(grep -c '^reset_retry' "$STUB_LOG" || true)"

# AB6: the same issue reaching Close via MERGE and then via APPROVED consults one
# shared counter — identical evaluate_stop argv both times
> "$STUB_LOG"
IN_REVIEW='[{"content":{"number":716,"title":"t"},"labels":[],"status":"In review"}]'
DISPATCHED=""; PROVIDERS_CLI_OUTPUT=""
stage_review_triage
IN_REVIEW='[{"content":{"number":716,"title":"t"},"labels":["direct-to-pr"],"status":"In review"}]'
DISPATCHED=""; PROVIDERS_CLI_OUTPUT="APPROVED"
stage_review_triage
assert_eq "AB6a: MERGE and APPROVED both consult evaluate_stop 716 close 3" \
  "2" "$(grep -c '^evaluate_stop 716 close 3$' "$STUB_LOG" || true)"
assert_eq "AB6b: no other evaluate_stop phase for 716" \
  "2" "$(grep -c '^evaluate_stop 716 ' "$STUB_LOG" || true)"
assert_eq "AB6c: both paths dispatched Close for 716" \
  "2" "$(grep -c 'dispatch Close issue #716' "$STUB_LOG" || true)"

```

- [ ] **Step 2: Run — must fail.**

```bash
bash tests/test_scheduler.sh 2>&1 | grep -E "FAIL|Results"
```

  Expected:

```
  FAIL: G2: advance: set_board_status REFINED — expected='1' got=''
  FAIL: I2: advance: set_board_status READY — expected='1' got=''
  FAIL: AB2b: APPROVED + running → no Close dispatch — expected='0' got='1'
  FAIL: AB4a: APPROVED at ceiling → trip_to_blocked phase close — expected='1' got='0'
  FAIL: AB4b: APPROVED at ceiling → no Close dispatch — expected='0' got='1'
  FAIL: AB6a: MERGE and APPROVED both consult evaluate_stop 716 close 3 — expected='2' got='1'
  FAIL: AB6b: no other evaluate_stop phase for 716 — expected='2' got='1'
Results: 291 passed, 7 failed
```

  (AB2a/AB4c/AB4d already pass — `end_gate_check` returns 0 and short-circuits today; they
  pin that the change keeps it so.)

- [ ] **Step 3: Implement.** In `scheduler.sh`, `end_gate_check`, change

```bash
    APPROVED)
      echo "[$(date -u +%FT%TZ)] end_gate issue=#${issue_num} pr=#${pr_num} state=APPROVED action=Close"
      if dispatch "Close issue #${issue_num}"; then
        DISPATCHED="Close issue #${issue_num}"
      fi
      return 0
      ;;
```

  to

```bash
    APPROVED)
      echo "[$(date -u +%FT%TZ)] end_gate issue=#${issue_num} pr=#${pr_num} state=APPROVED action=Close"
      if dispatch_close "$issue_num"; then
        DISPATCHED="Close issue #${issue_num}"
      fi
      return 0
      ;;
```

  `return 0` stays unconditional (spec Requirement 4): skip, trip, and dispatch all return 0,
  so `stage_review_triage` `continue`s and never classifies comments for the same issue in the
  same poll. `CHANGES_REQUESTED)` is untouched.

- [ ] **Step 4: Run — must pass.**

```bash
bash tests/test_scheduler.sh 2>&1 | grep -E "FAIL|Results|J1"
```

  Expected: `PASS: J1: APPROVED → Close dispatched`; only G2 and I2 fail;
  `Results: 296 passed, 2 failed`.

- [ ] **Step 5: Commit.**

```bash
git add scheduler.sh tests/test_scheduler.sh
git commit -m "fix(scheduler): direct-to-pr APPROVED end-gate dispatches Close via dispatch_close (#414)" \
  -m "Owner acknowledgement: this also guards the direct-to-pr end-gate auto-merge dispatch, which the issue does not name; tighten-only; the PR is held for the owner's explicit acknowledgement before merge."
```

---

## Task 5: Drift lock + `trip_to_blocked` usage comment

**Files:**
- Modify: `tests/test_scheduler.sh` (end of section `AB`, directly above `# Cleanup`)
- Modify: `scheduler.sh` (comment above `trip_to_blocked()`)

- [ ] **Step 1: Write the drift-lock test.** Insert **immediately above** the Cleanup header
  (`# ==========================================` / `# Cleanup`), that is, after section AB's
  `> "$STUB_LOG"; echo '{}' > "$STATE_FILE"` restore line and its blank line (keep the trailing blank line):

```bash
# Static drift lock: dispatch_close is the only raw Close dispatch, and both Close sites
# route through it — a third Close site would have to bypass this lock to go unguarded.
assert_eq "AB9a: exactly one raw Close dispatch in scheduler.sh (inside dispatch_close)" \
  "1" "$(grep -c 'dispatch "Close issue #' "$SCHED")"
assert_eq "AB9b: both Close sites route through dispatch_close" \
  "2" "$(grep -c 'if dispatch_close "' "$SCHED")"

```

- [ ] **Step 2: Confirm the lock bites.** It passes against the Task 4 tree, so prove it would
  catch a regression by running it once against the pre-ticket `scheduler.sh`, then restore:

```bash
cp scheduler.sh /tmp/sched-414.sh
git show origin/main:scheduler.sh > scheduler.sh
bash tests/test_scheduler.sh 2>&1 | grep -E "AB9"
cp /tmp/sched-414.sh scheduler.sh && git diff --quiet HEAD -- scheduler.sh && echo restored
```

  Expected:

```
  FAIL: AB9a: exactly one raw Close dispatch in scheduler.sh (inside dispatch_close) — expected='1' got='2'
  FAIL: AB9b: both Close sites route through dispatch_close — expected='2' got='0'
restored
```

  (If `origin/main` is unavailable, use `git show 5724b75:scheduler.sh`.) `restored` must
  print — the working-tree `scheduler.sh` is back to the committed Task 4 version. If the
  tool call dies between the overwrite and the restore, `git checkout -- scheduler.sh`
  recovers it (Task 4 is already committed).

- [ ] **Step 3: Update the usage comment.** In `scheduler.sh`, change

```bash
# Usage: trip_to_blocked <issue_num> <phase: implement|plan|refine|resolve> <reason>
```

  to

```bash
# Usage: trip_to_blocked <issue_num> <phase: implement|plan|refine|resolve|close> <reason>
```

- [ ] **Step 4: Run — must pass.**

```bash
bash -n scheduler.sh && bash tests/test_scheduler.sh 2>&1 | grep -E "FAIL|AB9|evaluate_stop wired|Results"
```

  Expected:

```
  FAIL: G2: advance: set_board_status REFINED — expected='1' got=''
  FAIL: I2: advance: set_board_status READY — expected='1' got=''
  PASS: evaluate_stop wired 4x in scheduler.sh
  PASS: AB9a: exactly one raw Close dispatch in scheduler.sh (inside dispatch_close)
  PASS: AB9b: both Close sites route through dispatch_close
Results: 298 passed, 2 failed
```

- [ ] **Step 5: Commit.**

```bash
git add scheduler.sh tests/test_scheduler.sh
git commit -m "test(scheduler): drift-lock Close dispatch to dispatch_close; list close in trip_to_blocked usage (#414)"
```

---

## Task 5b: Re-map the reference docs' line citations (mandatory)

**Files:**
- Modify: `docs/scheduler.md`, `docs/features.md`, `docs/configuration.md`, `docs/run-lifecycle.md`

Tasks 1-5 inserted 20 lines into `scheduler.sh` starting at old line 357 (the `dispatch_close`
block, directly above `# --- Dispatch ---`) and one line into `scripts/factory_core/breaker.py`
at old line 350 (the `"close"` entry). Every doc citation at or past those lines must move by
the same amount. The helper below does it mechanically; it lives in `/tmp` and must never be
committed.

- [ ] **Step 1: Confirm the shift.** Run:

```bash
grep -n '^# --- Close dispatch\|^dispatch_close()\|^# --- Dispatch ---\|^dispatch()\|^trip_to_blocked()\|^stage_review_triage()' scheduler.sh
git diff origin/main HEAD --numstat -- scheduler.sh scripts/factory_core/breaker.py
```

  Expected grep output, in file order: 357 (Close-dispatch header), 363 (`dispatch_close()`),
  377 (`# --- Dispatch ---`), 383 (`dispatch()`), 409 (`trip_to_blocked()`), 1125
  (`stage_review_triage()`). Expected numstat: `23 3 scheduler.sh` and
  `1 0 scripts/factory_core/breaker.py`. If any number differs (for example `main` moved under
  you), do not run the script with the arguments below: the insertion point or size is not what
  this task assumes — go to Step 4's fallback.

- [ ] **Step 2: Write the helper to `/tmp` (outside the repo).**

```bash
cat > /tmp/remap-414.py <<'PYEOF'
"""Shift doc line citations after an insertion. Usage:
   remap.py <target-basename> <first-shifted-old-line> <delta> <default-doc> <doc>...
Bare `:N` spans follow the last file named earlier in the same table row / paragraph;
in <default-doc> a row/paragraph that names no file yet means <target-basename>."""
import re, sys, pathlib
target, start, delta, default_doc = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
TOK = re.compile(r'`(?:(?P<path>[\w./-]*\w\.(?:sh|py|ya?ml|md|json|toml|env)|workflow|Dockerfile)(?::(?P<n1>\d[\d,\-]*))?|:(?P<n2>\d[\d,\-]*))`')
def shift(nums):
    return re.sub(r'\d+', lambda m: str(int(m.group()) + delta) if int(m.group()) >= start else m.group(), nums)
total = 0
for doc in sys.argv[5:]:
    p = pathlib.Path(doc); out = []; ctx = None; changed = 0
    for line in p.read_text(encoding='utf-8').split('\n'):
        if line.startswith('|') or not line.strip() or line.startswith('#'):
            ctx = target if doc == default_doc else None
        def rep(m):
            global ctx, changed
            if m.group('path'):
                nums = m.group('n1')
                if nums or doc != default_doc:
                    ctx = m.group('path').rsplit('/', 1)[-1]
                elif not nums:
                    return m.group()
            else:
                nums = m.group('n2')
            if ctx != target or not nums:
                return m.group()
            new = shift(nums)
            if new != nums:
                changed += 1
            return m.group().replace(':' + nums, ':' + new)
        out.append(TOK.sub(rep, line))
    p.write_text('\n'.join(out), encoding='utf-8', newline='\n')
    print(f"{doc}: {changed} citation(s) shifted"); total += changed
print(f"total: {total}")
PYEOF
```

- [ ] **Step 3: Run it — each command exactly once** (a second run shifts everything again;
  if that happens, `git checkout -- docs/` and start this step over):

```bash
python3 /tmp/remap-414.py scheduler.sh 357 20 docs/scheduler.md docs/scheduler.md docs/features.md docs/configuration.md docs/run-lifecycle.md
python3 /tmp/remap-414.py breaker.py 350 1 none docs/scheduler.md docs/features.md docs/configuration.md docs/run-lifecycle.md
```

  Expected output of the first command:

```
docs/scheduler.md: 113 citation(s) shifted
docs/features.md: 52 citation(s) shifted
docs/configuration.md: 19 citation(s) shifted
docs/run-lifecycle.md: 8 citation(s) shifted
total: 192
```

  Expected output of the second: `0`, `0`, `0`, `1` and `total: 1` (the one change is
  `breaker.py:333-383` → `breaker.py:333-384` in `docs/run-lifecycle.md`).

- [ ] **Step 4: Confirm by content, not by count alone.**

```bash
grep -n '^dispatch()\|^trip_to_blocked()\|^stage_rescue_blocked()\|^stage_conflict_resolve()\|^stage_review_triage()' scheduler.sh
grep -n -o 'stage_review_triage` (`scheduler.sh:[0-9]*\|stage_rescue_blocked` (`scheduler.sh:[0-9]*\|stage_conflict_resolve` (`scheduler.sh:[0-9]*' docs/features.md
grep -n -o 'scheduler.sh:[0-9,-]*' docs/run-lifecycle.md
```

  Expected: the first grep prints 383, 409, 1009, 1058, 1125; `docs/features.md` cites
  `stage_rescue_blocked` at `scheduler.sh:1009`, `stage_conflict_resolve` at
  `scheduler.sh:1058` and `stage_review_triage` at `scheduler.sh:1125`;
  `docs/run-lifecycle.md` shows `scheduler.sh:383-401`, `262,1390`, `298,1323`, `1203,1259`,
  `344,1149,1255`, `336,1143`, `1113`, `157`, `183`, `1113`.

  **Fallback — if any count in Step 3 or any line in Step 1 or Step 4 differs:** stop shifting
  blindly. `git checkout -- docs/`, then for each `scheduler.sh` / `breaker.py` citation in the
  four docs find where the cited code actually is now (`grep -n '<function or distinctive text>'
  scheduler.sh`) and cite that line. `git diff origin/main HEAD -- scheduler.sh` tells you
  exactly which old lines moved and by how much. Record in `$ARTIFACTS_DIR/implementation.md`
  that the mechanical re-map was not used and why.

- [ ] **Step 5: Three content edits** (the docs must describe the new behavior, not only point
  at the right lines). Line numbers below are the post-re-map ones.

  1. `docs/scheduler.md`, known-issue item 13. Replace the whole item — from the line
     ``13. **`Close` dispatches are not deduplicated against a running container.**`` through
     the line ending ``started while the first is still running (the verdict is cached, so it repeats).`` —
     with:

```markdown
13. **Resolved by #414: `Close` dispatches are deduplicated and budgeted.**
    `end_gate_check` (`:336`) and the `MERGE` branch (`:1143`) both call `dispatch_close`
    (`:363`), which skips while `is_issue_running` reports a container for the issue and
    counts each dispatch on `N:close` (ceiling `MAX_RETRIES`); the attempt after the ceiling
    trips the ticket to Blocked instead of dispatching.
```

  2. `docs/scheduler.md`, the retry-counter table. Directly after the row that starts with
     ``| `N:resolve` |`` insert:

```markdown
| `N:close` | Each `Close` dispatch through `dispatch_close` (`evaluate_stop`, `:367`) | `MAX_RETRIES` | Breaker trip |
```

  3. `docs/run-lifecycle.md`, the `Close issue #N` row of the dispatch table. Replace
     ``(`scheduler.sh:336,1143`)`` with
     ``(`scheduler.sh:374`, via `dispatch_close` from `:336`, `:1143`)``.

  Confirm: `grep -n 'if dispatch_close "\|dispatch "Close issue\|evaluate_stop "$issue_num"\|^dispatch_close()' scheduler.sh`
  prints 336, 363, 367, 374, 1143.

- [ ] **Step 6: Delete the helper, run the docs check.**

```bash
rm -f /tmp/remap-414.py
git status --short
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```

  Expected: `git status --short` lists exactly the four docs as modified and nothing untracked
  (no `remap-414.py` anywhere in the repo); `Docs reference check passed for 4 doc(s).`

- [ ] **Step 7: Commit.**

```bash
git add docs/scheduler.md docs/features.md docs/configuration.md docs/run-lifecycle.md
git commit -m "docs: re-map scheduler.sh citations after dispatch_close (#414)"
```

---

## Task 6: Full verification (no code changes)

**Files:** none modified.

- [ ] **Step 1: CI pytest.** `PYTHONPATH=scripts python -m pytest tests/ -q` → expected: all pass
  (dry run on `origin/main` 105ddc7 plus this plan: `3074 passed`; the exact count may differ slightly if `main` moved).
- [ ] **Step 2: CI scheduler shell test.** `bash tests/test_scheduler_lib.sh` → expected
  `Results: 20 passed, 0 failed`.
- [ ] **Step 3: CI docs/DAG checks.**

```bash
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```

  Expected: each reports a pass (e.g. `DAG trigger_rule check passed for 1 workflow file(s).`,
  `Docs reference check passed for 4 doc(s).`).
- [ ] **Step 4: Scope check.** Use the two-dot form (three-dot picks up files `main` merged
  independently after the branch forked):
  `git diff --stat origin/main HEAD -- . ':!docs/superpowers' ':!docs/archive' ':!.archon/memory'` → exactly
  `scheduler.sh`, `scripts/factory_core/breaker.py`, `tests/test_factory_core_breaker.py`,
  `tests/test_scheduler.sh`, `docs/scheduler.md`, `docs/features.md`, `docs/configuration.md`,
  `docs/run-lifecycle.md` — eight files, no others.
- [ ] **Step 5: Hand-off notes.** The implement agent does not write the PR description (the
  workflow's `push-and-pr` node builds it from a fixed template). In the decisions bullet of
  `$ARTIFACTS_DIR/implementation.md` record: the spec's owner-acknowledgement sentence verbatim
  (see the header above; it is also in the Task 4 commit body), that `tests/test_scheduler.sh` is
  not CI-gated and was run by hand (`298 passed, 2 failed` — G2/I2 pre-existing), and the
  deferred follow-up from the spec's open questions (Close workflow writing a failure-signature
  drop file → `check_failure_signature` for `close`). The operator adds the acknowledgement
  sentence to the PR description before merge.

---

## Spec coverage map

| Spec item | Where |
|---|---|
| R1 both Close sites in scope | Task 3 (MERGE), Task 4 (APPROVED) |
| R2 `is_issue_running` first | Task 2 helper; AB1b, AB2c (breaker not consulted while running) |
| R3 shared `dispatch_close`, phase `close`, `$MAX_RETRIES`, non-peek | Task 2; AB3a, AB6, AB8 |
| R3 no `reset_retry` for close | Tasks 3/4 add none; AB5c, AB5f |
| R3 trip clears `N:close` | Task 1 test (existing `reset_retry(key)` in `trip_to_blocked`) |
| R3 counts dispatches; fails open | AB8 (3 dispatch, 4th trips); AB7 |
| R3 `retry_cmds["close"]` | Task 1 |
| R3 usage comment lists `close` | Task 5 Step 3 |
| R3 no `check_failure_signature` for close | not added (by design) |
| R4 APPROVED always `return 0`, no fall-through | AB2a, AB4c, AB4d |
| Tests 1-6, 8 (`tests/test_scheduler.sh`) | AB1, AB2, AB3, AB4, AB5, AB6, AB8 |
| Test 7 (pytest, CI-run) | Task 1 |
| `evaluate_stop wired 4x` stays 4 | Task 5 Step 4 |
| Owner-acknowledgement note | Task 4 commit body; Task 6 Step 5 (`implementation.md`) |
| Reference docs stay true (plan-gate amendment; spec names no docs change) | Task 5b |
