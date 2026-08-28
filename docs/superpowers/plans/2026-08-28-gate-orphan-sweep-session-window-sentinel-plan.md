# Implementation Plan: Gate `stage_orphan_sweep` Behind the Session-Window Sentinel

**Issue:** omniscient/dark-factory#334
**Spec:** `docs/superpowers/specs/2026-08-26-gate-orphan-sweep-behind-session-window-sentinel-design.md`

---

## Goal

`scheduler.sh`'s main poll loop currently calls `stage_orphan_sweep` (scheduler.sh:1323) *before*
it reads the session-window-paused sentinel (scheduler.sh:1325-1346). A run that pauses itself
cleanly (Claude Max session-window exhaustion) is therefore misclassified as orphaned/crashed on
the very next poll and swept to Blocked with a false "died without its error handler executing"
comment — reproduced live on #332's implement run (issue #334 comment, 2026-08-21).

Fix: move the sentinel read before the sweep, and gate `stage_orphan_sweep` through the existing
`STAGE_GUARD`/`STAGE_SKIP_ACTION`/`dispatch_stage()` declarative table (#185) as a `paused_only`
stage, called directly (outside `STAGE_ORDER`, since it is board reconciliation, not a dispatch
decision). The guard must defer, not suppress: the sweep still fires normally once the sentinel
expires.

## Architecture

Single-file bash edit inside `scheduler.sh`'s main poll loop, plus new unit tests in
`tests/test_scheduler.sh` that exercise `dispatch_stage stage_orphan_sweep` directly via the
existing `SCHEDULER_SOURCE_ONLY=1` test harness — mirroring the existing R1-R8 `dispatch_stage`
guard tests exactly (same fixture shape, same stub log conventions).

Three changes, all in the main `while true` poll loop:
1. Move the session-window-paused sentinel-read block (current lines 1325-1346) to run
   immediately before the sweep call (current line 1323), still before the main-is-red block
   (current lines 1348-1361).
2. Add `[stage_orphan_sweep]=paused_only` to `STAGE_GUARD` and
   `[stage_orphan_sweep]=skip_orphan_sweep` to `STAGE_SKIP_ACTION` (scheduler.sh:1176-1190).
   `STAGE_ORDER` (scheduler.sh:1191) is **not** touched — `stage_orphan_sweep` stays a direct
   call outside the dispatch cascade, same convention as `stage_ci_gate`, `stage_rescue_blocked`,
   and `stage_epic_autopilot`.
3. Change the call site from the bare `stage_orphan_sweep` to `dispatch_stage stage_orphan_sweep`.

`dispatch_stage()` itself (scheduler.sh:1196-1214) needs no code change — its existing
`paused_only` branch already produces the correct behavior and log line
(`session_window_paused=true action=skip_orphan_sweep`) for any stage name passed to it.

Per `.archon/memory/dark-factory-ops.md`:
- `[PATTERN]` (#389): `$STUB_LOG` only captures stubbed external commands (`gh`, `docker`,
  `set_board_status`); to assert on the guard's own `echo`'d log line
  (`action=skip_orphan_sweep`), the tests below capture `dispatch_stage`'s stdout directly
  (`$(dispatch_stage stage_orphan_sweep 2>&1)`), not `$STUB_LOG` — same pattern R1-R8 already use.
- `[PATTERN]` (#338): all config-driven vars are already exported before `source "$SCHED"` at the
  top of `tests/test_scheduler.sh`; this change adds no new config knob, so no new export is
  needed.
- `[PATTERN]` (#33): `FACTORY_CORE_CLI` override is only needed when a test exercises a
  brand-new CLI subcommand; `marker scheduler` (called inside `stage_orphan_sweep`'s body) is
  pre-existing and already exercised safely under this same test file's `trip_to_blocked` tests
  (section B), so no new override is needed here.

## Tech Stack

Bash only (`scheduler.sh`, `tests/test_scheduler.sh`). No Python, no new dependencies, no config
schema changes.

---

## File Structure

| File | Change |
|---|---|
| `tests/test_scheduler.sh` | Modified — 5 new test groups / 12 new `assert_eq` assertions (R9-R13b) covering the `dispatch_stage stage_orphan_sweep` guard and source-order, inserted after the existing R8b assertion |
| `scheduler.sh` | Modified — reorder sentinel read before sweep; extend `STAGE_GUARD`/`STAGE_SKIP_ACTION`; call via `dispatch_stage` |

---

## Memory Context Applied

- `.archon/memory/codebase-patterns.md` `[AVOID]` (#292, "`stage_orphan_sweep` runs before the
  session-window sentinel gate, so a paused run gets swept to Blocked with a directly-contradicted
  'will be retried automatically' comment; fix: gate `stage_orphan_sweep` behind the pause sentinel
  — move it below the sentinel read and skip when `SESSION_WINDOW_PAUSED=true`"): this is the exact
  defect and exact fix shape this plan implements (Task 2 steps 1-2). Confirmed satisfied, not
  merely inapplicable — this memory scan initially missed the entry because the automated
  `load_memory_context.sh plan` retrieval scopes by files already changed on this branch (only the
  spec, not `scheduler.sh`, at the time this plan was written), so a targeted `grep` of
  `codebase-patterns.md` was run directly to confirm no `scheduler.sh`-relevant `[AVOID]` was
  missed.
- `.archon/memory/dark-factory-ops.md` `[PATTERN]` (#389, "capture function stdout, not
  `$STUB_LOG`, to assert on echo'd log lines"): baked into Task 1's tests below — all
  `action=skip_orphan_sweep` / `sweep=orphaned_in_progress` assertions grep captured stdout
  (`$STDOUT_R9` etc.), never `$STUB_LOG`.
- `.archon/memory/dark-factory-ops.md` `[PATTERN]` (#338, "export config-driven vars before
  sourcing"): confirmed not applicable — no new config var is introduced.
- `.archon/memory/dark-factory-ops.md` `[PATTERN]` (#33, "override `FACTORY_CORE_CLI` for
  brand-new CLI subcommands"): confirmed not applicable — `tests/test_scheduler.sh:13` already
  exports `FACTORY_CORE_CLI` unconditionally for the whole file, and `marker scheduler` (called
  inside `stage_orphan_sweep`'s body) is pre-existing, already safely exercised in this same file's
  `trip_to_blocked` tests (section B).

## Known Pre-Existing Failures (baseline, unrelated to this ticket)

Running `bash tests/test_scheduler.sh` on the unmodified branch today reports **181 passed, 2
failed**:
```
FAIL: G2: advance: set_board_status REFINED — expected='1' got=''
FAIL: I2: advance: set_board_status READY — expected='1' got=''
```
These two failures pre-exist on this branch, are unrelated to `stage_orphan_sweep`/session-window
gating, and are out of this ticket's scope (fixing them is not one of the spec's requirements).
Task 2's "verify passes" step below expects these same 2 failures to still be present — do not
attempt to fix them as part of this ticket.

---

## Task 1: Add failing tests for the `stage_orphan_sweep` guard

**Files:** `tests/test_scheduler.sh` (modified)

1. Insert the following block into `tests/test_scheduler.sh` immediately after the existing R8b
   assertion (`assert_eq "R8b: stage_review_triage guard type is none" ...`, currently the last
   line of the "R: Stage guard semantics" section, right before the `# S:` section header
   comment):

```bash
# R9: dispatch_stage(stage_orphan_sweep) is a no-op when SESSION_WINDOW_PAUSED=true — the
# guard must fire before the sweep's body (set_board_status/gh calls) ever runs (#334).
MAIN_IS_RED=false; SESSION_WINDOW_PAUSED=true
IN_PROGRESS='[{"content":{"number":961,"title":"t"},"labels":[]}]'
: > "$STUB_LOG"
STDOUT_R9=$(dispatch_stage stage_orphan_sweep 2>&1)
assert_eq "R9: dispatch_stage(stage_orphan_sweep) skips on session_window_paused" \
  "1" "$(echo "$STDOUT_R9" | grep -c 'action=skip_orphan_sweep')"
assert_eq "R9b: no set_board_status call when skipped" \
  "0" "$(grep -c 'set_board_status' "$STUB_LOG" || true)"
assert_eq "R9c: no gh comment call when skipped" \
  "0" "$(grep -c 'gh issue comment' "$STUB_LOG" || true)"

# R10: dispatch_stage(stage_orphan_sweep) still sweeps normally when SESSION_WINDOW_PAUSED=false
# (regression guard: unpaused orphan recovery is unchanged).
MAIN_IS_RED=false; SESSION_WINDOW_PAUSED=false
IN_PROGRESS='[{"content":{"number":962,"title":"t"},"labels":[]}]'
: > "$STUB_LOG"
STDOUT_R10=$(dispatch_stage stage_orphan_sweep 2>&1)
assert_eq "R10: dispatch_stage(stage_orphan_sweep) does not skip when unpaused" \
  "0" "$(echo "$STDOUT_R10" | grep -c 'action=skip_orphan_sweep')"
assert_eq "R10b: sweep runs and moves orphaned issue 962 to blocked" \
  "1" "$(echo "$STDOUT_R10" | grep -c 'sweep=orphaned_in_progress issue=#962 action=move_to_blocked')"
assert_eq "R10c: set_board_status called for issue 962" \
  "1" "$(grep -c 'set_board_status 962' "$STUB_LOG" || true)"

# R11: dispatch_stage(stage_orphan_sweep) still sweeps when MAIN_IS_RED=true and
# SESSION_WINDOW_PAUSED=false — confirms guard type is paused_only, not red_or_paused: a
# genuinely dead container must still be recovered to Blocked while main is red.
MAIN_IS_RED=true; SESSION_WINDOW_PAUSED=false
IN_PROGRESS='[{"content":{"number":963,"title":"t"},"labels":[]}]'
: > "$STUB_LOG"
STDOUT_R11=$(dispatch_stage stage_orphan_sweep 2>&1)
assert_eq "R11: dispatch_stage(stage_orphan_sweep) does not skip when only main_red is true" \
  "0" "$(echo "$STDOUT_R11" | grep -c 'action=skip_orphan_sweep')"
assert_eq "R11b: sweep runs and moves orphaned issue 963 to blocked under main_red" \
  "1" "$(echo "$STDOUT_R11" | grep -c 'sweep=orphaned_in_progress issue=#963 action=move_to_blocked')"

MAIN_IS_RED=false; SESSION_WINDOW_PAUSED=false; IN_PROGRESS='[]'

# R12: stage_orphan_sweep is board reconciliation, not a dispatch decision — it must stay out of
# STAGE_ORDER (mirrors R7b's pattern for stage_epic_autopilot) while still being a STAGE_GUARD
# key of type paused_only (unlike stage_epic_autopilot, which keeps its own compound condition).
assert_eq "R12: stage_orphan_sweep not in STAGE_ORDER" \
  "0" "$(printf '%s\n' "${STAGE_ORDER[@]}" | grep -c '^stage_orphan_sweep$')"
assert_eq "R12b: stage_orphan_sweep is a STAGE_GUARD key with type paused_only" \
  "paused_only" "${STAGE_GUARD[stage_orphan_sweep]:-}"

# R13: static source-text check — the session-window-paused sentinel read must appear BEFORE
# the stage_orphan_sweep dispatch call, which must appear BEFORE the main-is-red block, in
# scheduler.sh's actual poll-loop source. R9-R12 only prove the guard-table binding; this
# proves the physical reorder itself, which is otherwise unreachable under
# SCHEDULER_SOURCE_ONLY=1 (the poll loop never executes in tests) (#334). Each grep is
# `|| true`-guarded on its own assignment: scheduler.sh's `set -euo pipefail` (line 2) is
# inherited into this shell via `source`, so an ungated failing pipeline in a bare assignment
# (not just as a captured argument, which is what R9b/R9c/R10c above do) would abort the whole
# test run on a zero-match grep.
SENTINEL_LINE=$(grep -nF '[ -f "${SCHEDULER_STATE_DIR}/session-window-paused" ]; then' "$SCHED" | head -1 | cut -d: -f1) || true
SWEEP_CALL_LINE=$(grep -nF 'dispatch_stage stage_orphan_sweep' "$SCHED" | head -1 | cut -d: -f1) || true
MAIN_RED_LINE=$(grep -nF '[ -f "${SCHEDULER_STATE_DIR}/main-is-red" ]' "$SCHED" | head -1 | cut -d: -f1) || true
assert_eq "R13: sentinel read precedes stage_orphan_sweep dispatch (source order)" \
  "1" "$([ -n "$SENTINEL_LINE" ] && [ -n "$SWEEP_CALL_LINE" ] && [ "$SENTINEL_LINE" -lt "$SWEEP_CALL_LINE" ] 2>/dev/null && echo 1 || echo 0)"
assert_eq "R13b: stage_orphan_sweep dispatch precedes main-is-red block (source order)" \
  "1" "$([ -n "$SWEEP_CALL_LINE" ] && [ -n "$MAIN_RED_LINE" ] && [ "$SWEEP_CALL_LINE" -lt "$MAIN_RED_LINE" ] 2>/dev/null && echo 1 || echo 0)"
```

2. Verify the new tests fail against today's (unfixed) `scheduler.sh` — `stage_orphan_sweep` is
   not yet in `STAGE_GUARD` (so `dispatch_stage` treats it as guard type `none` and always runs
   it unconditionally, never emitting `action=skip_orphan_sweep`), and the call site is still the
   bare `stage_orphan_sweep` (so `grep -nF 'dispatch_stage stage_orphan_sweep'` finds no match at
   all, failing R13/R13b outright):

```bash
bash tests/test_scheduler.sh 2>&1 | grep -E 'FAIL:|Results:'
```

Expected output (the 2 pre-existing failures plus 6 new ones from R9/R9b/R9c/R12b/R13/R13b —
R10/R10b/R10c/R11/R11b already pass today because an unguarded `dispatch_stage` call runs the
sweep unconditionally, which happens to match the "still sweeps" assertions even before the fix):

```
FAIL: G2: advance: set_board_status REFINED — expected='1' got=''
FAIL: I2: advance: set_board_status READY — expected='1' got=''
FAIL: R9: dispatch_stage(stage_orphan_sweep) skips on session_window_paused — expected='1' got='0'
FAIL: R9b: no set_board_status call when skipped — expected='0' got='1'
FAIL: R9c: no gh comment call when skipped — expected='0' got='1'
FAIL: R12b: stage_orphan_sweep is a STAGE_GUARD key with type paused_only — expected='paused_only' got=''
FAIL: R13: sentinel read precedes stage_orphan_sweep dispatch (source order) — expected='1' got='0'
FAIL: R13b: stage_orphan_sweep dispatch precedes main-is-red block (source order) — expected='1' got='0'
Results: 187 passed, 8 failed
```

3. Commit:

```bash
git add tests/test_scheduler.sh
git commit -m "test(scheduler): add dispatch_stage(stage_orphan_sweep) guard tests (issue #334)"
```

---

## Task 2: Gate `stage_orphan_sweep` behind the session-window sentinel

**Files:** `scheduler.sh` (modified)

1. Extend `STAGE_GUARD` and `STAGE_SKIP_ACTION`. Current block (scheduler.sh:1176-1190):

```bash
declare -A STAGE_GUARD=(
  [stage_conflict_resolve]=red_or_paused
  [stage_review_triage]=none
  [stage_ready_implement]=red_or_paused
  [stage_blocked_retry]=red_or_paused
  [stage_plan]=paused_only
  [stage_refine]=paused_only
)
declare -A STAGE_SKIP_ACTION=(
  [stage_conflict_resolve]=skip_deconflict
  [stage_ready_implement]=skip_implement
  [stage_blocked_retry]=skip_blocked_retry
  [stage_plan]=skip_plan
  [stage_refine]=skip_refine
)
```

Replace with:

```bash
declare -A STAGE_GUARD=(
  [stage_conflict_resolve]=red_or_paused
  [stage_review_triage]=none
  [stage_ready_implement]=red_or_paused
  [stage_blocked_retry]=red_or_paused
  [stage_plan]=paused_only
  [stage_refine]=paused_only
  [stage_orphan_sweep]=paused_only
)
declare -A STAGE_SKIP_ACTION=(
  [stage_conflict_resolve]=skip_deconflict
  [stage_ready_implement]=skip_implement
  [stage_blocked_retry]=skip_blocked_retry
  [stage_plan]=skip_plan
  [stage_refine]=skip_refine
  [stage_orphan_sweep]=skip_orphan_sweep
)
```

`STAGE_ORDER` (scheduler.sh:1191, `STAGE_ORDER=(stage_conflict_resolve stage_review_triage
stage_ready_implement stage_blocked_retry stage_plan stage_refine)`) is left completely
unchanged — `stage_orphan_sweep` must not be added to it (Requirement 3 of the spec: it is board
reconciliation, not a dispatch decision).

2. Reorder the sentinel read before the sweep, and call the sweep via `dispatch_stage`. Current
   block (scheduler.sh:1322-1346):

```bash
  # --- Sweep: recover orphaned "In progress" items (see stage_orphan_sweep) ---
  stage_orphan_sweep

  # --- Read session-window-paused sentinel (written by entrypoint.sh on a detected
  # Claude Max session-window exhaustion, #35) — self-clearing, no recheck dispatch
  # needed since the resume time is already known from the embedded epoch. Read
  # BEFORE the main-is-red block below so main_red_recheck_check/main_red_fixer_check
  # can also honor the pause (they must not dispatch "Recheck main"/"Fix main" into an
  # exhausted window). ---
  SESSION_WINDOW_PAUSED=false
  if [ -f "${SCHEDULER_STATE_DIR}/session-window-paused" ]; then
    SW_RESUME_EPOCH=$(cat "${SCHEDULER_STATE_DIR}/session-window-paused" 2>/dev/null || echo 0)
    if ! echo "${SW_RESUME_EPOCH:-0}" | grep -qE '^[0-9]+$'; then
      echo "[$(date -u +%FT%TZ)] session_window_gate=corrupt_sentinel action=clear"
      rm -f "${SCHEDULER_STATE_DIR}/session-window-paused"
      SW_RESUME_EPOCH=0
    fi
    if [ "$(date +%s)" -lt "${SW_RESUME_EPOCH:-0}" ]; then
      SESSION_WINDOW_PAUSED=true
      SW_RESUME_ISO=$(date -u -d "@${SW_RESUME_EPOCH}" +%FT%TZ 2>/dev/null || echo "unknown")
      echo "[$(date -u +%FT%TZ)] session_window_gate=active resume_at=${SW_RESUME_ISO}"
    else
      rm -f "${SCHEDULER_STATE_DIR}/session-window-paused"
    fi
  fi
```

Replace with (sentinel-read block moved first, sweep moved after and called via
`dispatch_stage`):

```bash
  # --- Read session-window-paused sentinel (written by entrypoint.sh on a detected
  # Claude Max session-window exhaustion, #35) — self-clearing, no recheck dispatch
  # needed since the resume time is already known from the embedded epoch. Read BEFORE
  # stage_orphan_sweep so a run paused this cycle isn't misclassified as orphaned before
  # its pause is known (#334), and BEFORE the main-is-red block below so
  # main_red_recheck_check/main_red_fixer_check can also honor the pause (they must not
  # dispatch "Recheck main"/"Fix main" into an exhausted window). ---
  SESSION_WINDOW_PAUSED=false
  if [ -f "${SCHEDULER_STATE_DIR}/session-window-paused" ]; then
    SW_RESUME_EPOCH=$(cat "${SCHEDULER_STATE_DIR}/session-window-paused" 2>/dev/null || echo 0)
    if ! echo "${SW_RESUME_EPOCH:-0}" | grep -qE '^[0-9]+$'; then
      echo "[$(date -u +%FT%TZ)] session_window_gate=corrupt_sentinel action=clear"
      rm -f "${SCHEDULER_STATE_DIR}/session-window-paused"
      SW_RESUME_EPOCH=0
    fi
    if [ "$(date +%s)" -lt "${SW_RESUME_EPOCH:-0}" ]; then
      SESSION_WINDOW_PAUSED=true
      SW_RESUME_ISO=$(date -u -d "@${SW_RESUME_EPOCH}" +%FT%TZ 2>/dev/null || echo "unknown")
      echo "[$(date -u +%FT%TZ)] session_window_gate=active resume_at=${SW_RESUME_ISO}"
    else
      rm -f "${SCHEDULER_STATE_DIR}/session-window-paused"
    fi
  fi

  # --- Sweep: recover orphaned "In progress" items (see stage_orphan_sweep). Deferred,
  # not suppressed, while a session-window pause is active — fires normally the first
  # cycle after resume_at or once the sentinel is absent (#334). ---
  dispatch_stage stage_orphan_sweep
```

Note: the main-is-red block immediately below (scheduler.sh:1348-1361 today) is untouched and
still reads `SESSION_WINDOW_PAUSED` after this relocated block — no other line between the old
sweep call and the main-is-red block moves.

3. Verify the new tests pass and no regressions were introduced:

```bash
bash tests/test_scheduler.sh 2>&1 | grep -E 'FAIL:|Results:'
```

Expected output (only the 2 pre-existing, unrelated failures remain):

```
FAIL: G2: advance: set_board_status REFINED — expected='1' got=''
FAIL: I2: advance: set_board_status READY — expected='1' got=''
Results: 193 passed, 2 failed
```

4. Run the full local verification sweep (per CLAUDE.md conventions) to confirm no other suite
   regressed. This includes the sibling scheduler test files (they don't source-test this exact
   block, but `stage_orphan_sweep`/`dispatch_stage`/`STAGE_GUARD` are shared surface with their own
   guard/ceiling assertions, so a cheap regression check is worth running):

```bash
python -m pytest tests/ -v
bash tests/test_smoke_gate.sh
bash tests/test_scheduler_autopilot_guard.sh
bash tests/test_scheduler_ceiling.sh
bash tests/test_scheduler_main_red_fixer.sh
bash tests/test_scheduler_pagination.sh
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```

Expected: all pass (this change touches no Python, no DAG YAML, and no other bash entrypoint —
`tests/test_scheduler.sh` is not wired into `.github/workflows/ci.yml` today, so these are the
CI-enforced checks plus a same-surface regression sweep; `bash tests/test_scheduler.sh`'s own
pass/fail from step 3 above is a local/manual regression check, not CI-enforced — wiring it into
`ci.yml` is out of this ticket's scope per the spec's CI-enforcement note, and must first be
proven portable on a bare runner as a separate ticket). If the container/venv running this step
lacks `pytest` installed, run the pytest line inside the project's normal test container instead
of the bare host shell.

5. Commit:

```bash
git add scheduler.sh
git commit -m "fix(scheduler): gate stage_orphan_sweep behind session-window sentinel (issue #334)"
```

---

## Non-Goals (explicitly out of scope, per spec)

- No change to `stage_orphan_sweep`'s own body: the "Orphaned Run Recovered" comment text, the
  `set_board_status` call, or any retry-counter accounting — only when it runs.
- No per-issue-scoped sentinel (embedding an issue number) — the sentinel stays a single global
  bare epoch integer; the `paused_only` guard is correctly coarse-grained under the standing
  `factory_wip_limit: 1` assumption.
- No wiring of `tests/test_scheduler.sh` into `.github/workflows/ci.yml`.
- The retry-counter consumption by a mis-swept paused run (#341) and the "died without its error
  handler executing" comment wording for a later genuine sweep of a previously-paused item are
  both explicitly out of scope (tracked separately, not this ticket).
