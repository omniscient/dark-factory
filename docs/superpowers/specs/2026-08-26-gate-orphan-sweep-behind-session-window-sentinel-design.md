# Gate stage_orphan_sweep behind the session-window-paused sentinel

**Issue:** omniscient/dark-factory#334
**Related:** scoped out of #292 ("spec excludes scheduler.sh"); retry-counter consumption during a
paused run is tracked separately in #341 — not this ticket's scope.

---

## Overview / Problem Statement

`scheduler.sh`'s main poll loop calls `stage_orphan_sweep()` (scheduler.sh:1323) *before* it reads
the session-window-paused sentinel (scheduler.sh:1325-1346). `stage_orphan_sweep` moves any
"In progress" board item with no matching running Docker container to Blocked, posting an
"Orphaned Run Recovered ... died without its error handler executing" comment.

A session-window pause is not a crash: `entrypoint.sh`'s `_handle_session_window_pause()`
deliberately writes the pause sentinel, records a `paused` run-record entry, and lets the container
exit cleanly (its error handler *did* run) — by design, per the comment at `entrypoint.sh:455-458 (pause branch in `on_failure()`) and entrypoint.sh:477-478 (pause comment body)`,
so "the scheduler reconciles this issue's board state on its next poll." Because the sweep runs
before the sentinel is read, that first reconciliation poll sees "no running container" and
misclassifies the paused run as orphaned, before the very next few lines of the same cycle would
have told it otherwise.

**Reproduced live** (issue comment, 2026-08-21 21:15Z, self instance, on #332's implement run):

```
[21:15:25Z] sweep=orphaned_in_progress issue=#332 action=move_to_blocked
[21:15:27Z] session_window_gate=active resume_at=2026-08-21T21:45:02Z
```

The pause comment (`df-session-window-pause`) had posted at 21:15:03Z; the sweep fired 22 seconds
later, 2 seconds before the sentinel read. Net effect: the ticket carried a false "died without its
error handler executing" comment, consumed a retry-counter increment, and resumed at 21:45 by being
re-dispatched from Blocked as a retry — re-running the entire implement plan from Task 1 instead of
continuing.

This ticket fixes only the ordering/gating bug: **the sweep must know about an active session-window
pause before it decides an item is orphaned.** The comment wording and retry-counter side effects of
the mis-sweep are explicitly out of scope (see Non-Goals).

---

## Requirements

Distilled from the issue and the Q&A below:

1. The session-window-paused sentinel read (scheduler.sh:1325-1346) moves to run **before**
   `stage_orphan_sweep`, so `SESSION_WINDOW_PAUSED` is known at sweep time. It must still run before
   the main-is-red block (scheduler.sh:1348-1361), preserving the existing comment's guarantee that
   `main_red_recheck_check`/`main_red_fixer_check` also honor the pause.
2. `stage_orphan_sweep` is gated through the existing declarative guard table (`STAGE_GUARD` /
   `STAGE_SKIP_ACTION` / `dispatch_stage()`, scheduler.sh:1176-1214, added in #185) rather than a new
   bespoke inline `if`: add `[stage_orphan_sweep]=paused_only` to `STAGE_GUARD` and
   `[stage_orphan_sweep]=skip_orphan_sweep` to `STAGE_SKIP_ACTION`, and call it as
   `dispatch_stage stage_orphan_sweep` instead of the current bare `stage_orphan_sweep`.
3. `stage_orphan_sweep` is **not** added to `STAGE_ORDER`. That array is the dispatch cascade
   (honors `DISPATCHED`, `CI_BLOCKED`, retry counters); non-dispatch stages
   (`stage_ci_gate`, `stage_rescue_blocked`, `stage_epic_autopilot`) already stay outside it by
   convention, and `tests/test_scheduler.sh` (R7b) asserts this explicitly for
   `stage_epic_autopilot`. `dispatch_stage` is called directly for `stage_orphan_sweep`, same as the
   existing per-stage calls inside the `STAGE_ORDER` loop, just from outside that loop.
4. The guard type is `paused_only`, **not** `red_or_paused`: a genuinely dead container (host
   restart, OOM/SIGKILL) must still be recovered to Blocked while main is red — that reconciliation
   dispatches nothing and burns no session window, unlike the `red_or_paused`-guarded dispatch
   stages.
5. The guard **defers, it does not suppress**: once the sentinel expires or is absent,
   `stage_orphan_sweep` must run normally on the very next poll cycle, sweeping any item that is
   genuinely orphaned (including the just-resumed item, if its container still hasn't been
   redispatched). This is the intended resume-reconciliation path, not a case to special-case away.
6. No change to `stage_orphan_sweep`'s own body (the "Orphaned Run Recovered" comment text, the
   `set_board_status` call, or any retry-counter accounting elsewhere) — only when it runs.
7. Test coverage added to `tests/test_scheduler.sh`, mirroring the existing R1-R6 `dispatch_stage`
   guard tests:
   - `dispatch_stage stage_orphan_sweep` is a no-op (no `set_board_status`/`gh issue comment` call
     observed) when `SESSION_WINDOW_PAUSED=true`.
   - `dispatch_stage stage_orphan_sweep` still sweeps normally when `SESSION_WINDOW_PAUSED=false`
     (regression guard: unpaused orphan recovery is unchanged).
   - `dispatch_stage stage_orphan_sweep` still sweeps when `MAIN_IS_RED=true` and
     `SESSION_WINDOW_PAUSED=false` (confirms `paused_only`, not `red_or_paused`).
   - `STAGE_ORDER` does not contain `stage_orphan_sweep` (mirrors the existing R7b pattern for
     `stage_epic_autopilot`).

---

## Brainstorming Q&A

> **Q:** `scheduler.sh` already has a declarative `STAGE_GUARD`/`STAGE_SKIP_ACTION` table (added in
> #185) that gates several dispatch stages behind `MAIN_IS_RED`/`SESSION_WINDOW_PAUSED` via
> `dispatch_stage()`. `stage_orphan_sweep` is called directly, outside `STAGE_ORDER`, before
> `SESSION_WINDOW_PAUSED` is even computed. Should the fix fold it into `STAGE_ORDER` via the
> existing table, or keep it a standalone call with a new inline guard?
>
> **A:** Neither exactly — reuse the table's guard mechanism, but keep the stage out of
> `STAGE_ORDER`. `STAGE_ORDER` is documented at its call site as "guard-table-driven **dispatch**"
> — the cascade whose members honor `DISPATCHED`, `CI_BLOCKED`, and retry counters.
> `stage_orphan_sweep` is board reconciliation, not a dispatch decision, and the established pattern
> is that non-dispatch stages (`stage_ci_gate`, `stage_rescue_blocked`, `stage_epic_autopilot`) stay
> direct calls — `tests/test_scheduler.sh` R7b even asserts `stage_epic_autopilot` is *not* in
> `STAGE_ORDER` as a requirement. The recommended shape: move the sentinel-read block to immediately
> before the sweep (still before the main-is-red block, preserving its "read BEFORE main-is-red"
> comment), add `[stage_orphan_sweep]=paused_only` / `[stage_orphan_sweep]=skip_orphan_sweep` to the
> two tables, and call `dispatch_stage stage_orphan_sweep` instead of the bare call — without
> touching `STAGE_ORDER`. This reuses #185's single declarative evaluation site, gets the same
> greppable log-line convention other guards use, and is unit-testable via `SCHEDULER_SOURCE_ONLY=1`
> exactly like the existing R1-R6 tests. Guard type is `paused_only`, not `red_or_paused`: a
> genuinely dead run should still be reconciled while main is red — the sweep dispatches nothing and
> burns no session window, and the downstream `stage_blocked_retry` is already
> `red_or_paused`-guarded, so nothing gets re-dispatched into a red main anyway. One thing the spec
> must state explicitly: this guard *defers*, it does not suppress — the sweep must still fire
> normally once the sentinel expires, since that is the intended resume-reconciliation path
> (`entrypoint.sh`'s pause-branch comment: "the scheduler reconciles this issue's board state on its
> next poll"). If the comment text or retry-counter side of the mis-sweep should also be fixed, that
> should be an explicit separate requirement, not something that rides in silently on this ticket's
> one-line ordering fix.

> **Q:** The session-window-paused sentinel is a single global file (not per-issue), and
> `stage_orphan_sweep` loops over *all* "In progress" items in one pass. Under a `paused_only` guard
> on the whole stage, the entire sweep is skipped for every in-progress item while paused, not just
> the one that triggered the pause — today `factory_wip_limit` defaults to 1, so at most one item is
> ever in-progress while paused, making this moot. Should the fix scope to this global/coarse gate
> as correct-for-now, or require per-issue precision (skip only the paused issue, still sweep other
> genuinely orphaned in-progress items)?
>
> **A:** Scope to the global/coarse gate; document the assumption rather than build per-issue
> precision. `config/config.yaml` ships `factory_wip_limit: 1`; `entrypoint.sh` enforces it as a hard
> abort; `docs/archive/2026-07-13-scheduler-session-window-backoff-design.md:222` already states
> "`factory_wip_limit` stays at `1` (or low) for the dark-factory self-target instance" as a standing
> architectural assumption the whole sentinel design is built on. Nothing in open specs/plans
> proposes raising it. The coarse gate is also arguably the *semantically* correct one: a Claude Max
> session-window exhaustion is account-wide, not container-scoped, so under a hypothetical
> `factory_wip_limit>1` every concurrent run would hit the same wall near-simultaneously — "no
> container + sentinel active" would describe all of them, not just the triggering issue. Per-issue
> precision would also require the sentinel to carry an issue number (currently a bare epoch integer,
> validated with `grep -qE '^[0-9]+$'` and treated as corrupt otherwise), touching
> `scripts/factory_core/session_window.py`, `entrypoint.sh`, and that corruption check — a
> disproportionately larger, riskier change than this ticket's one-line reordering bug calls for. The
> cost is bounded and self-healing regardless: the guard defers, not suppresses, so a genuinely
> orphaned *other* issue (in a hypothetical multi-WIP future) gets swept on the first poll after
> `resume_at` — worst case is one extra WIP slot held until the window resets.

---

## Architecture / Approach

**1. Reorder: sentinel read moves before the sweep** (scheduler.sh, inside the main poll loop, after
the factory-capacity guard which is unaffected):

```bash
  # --- Read session-window-paused sentinel (written by entrypoint.sh on a detected
  # Claude Max session-window exhaustion, #35) — self-clearing, no recheck dispatch
  # needed since the resume time is already known from the embedded epoch. Read BEFORE
  # stage_orphan_sweep so a run paused this cycle isn't misclassified as orphaned before
  # its pause is known (#334), and BEFORE the main-is-red block below so
  # main_red_recheck_check/main_red_fixer_check can also honor the pause. ---
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
  # cycle after resume_at or once the sentinel is absent. ---
  dispatch_stage stage_orphan_sweep

  # --- Read main-is-red sentinel (written by smoke_gate.sh in dispatched containers) ---
  ...
```

**2. Extend the guard tables** (scheduler.sh:1176-1190):

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
# STAGE_ORDER is unchanged — stage_orphan_sweep is NOT added to it (see Requirement 3).
```

`dispatch_stage()` itself (scheduler.sh:1196-1214) needs no change — its existing `paused_only`
branch already produces the right behavior and log line
(`session_window_paused=true action=skip_orphan_sweep`) for any stage name passed to it, called from
inside or outside the `STAGE_ORDER` loop.

**3. Tests** (`tests/test_scheduler.sh`, alongside the existing R1-R8 `dispatch_stage` guard tests):
add cases asserting `dispatch_stage stage_orphan_sweep` is skipped under
`SESSION_WINDOW_PAUSED=true`, still fires under `SESSION_WINDOW_PAUSED=false` (with or without
`MAIN_IS_RED=true`), and that `stage_orphan_sweep` is absent from `STAGE_ORDER`.

---

## CI-enforcement note (reviewer amendment, 2026-08-27)

`.github/workflows/ci.yml` does not run `tests/test_scheduler.sh`, so the new R-tests are
locally runnable but CI-unenforced. The plan's final verification must run
`bash tests/test_scheduler.sh` explicitly and state this gap; wiring the scheduler suite into
CI is a separate ticket (it must first be proven portable on a bare runner, as
`tests/test_entrypoint_session_window.sh` had to be for #355) — do not add the ci.yml line as a
side effect of this ticket.

## Alternatives Considered

1. **Bespoke inline guard** (`if [ "$SESSION_WINDOW_PAUSED" != "true" ]; then stage_orphan_sweep; fi`)
   instead of reusing the declarative table. Rejected: it would duplicate the log-line convention and
   guard logic #185 already centralized, and would not be exercisable via the existing
   `SCHEDULER_SOURCE_ONLY=1` `dispatch_stage` test harness without extra scaffolding.
2. **Add `stage_orphan_sweep` to `STAGE_ORDER`.** Rejected per Q&A: it is board reconciliation, not a
   dispatch decision, and would incorrectly subject it to `DISPATCHED`/`CI_BLOCKED` short-circuiting
   that the other `STAGE_ORDER` members rely on for dispatch-slot bookkeeping — semantics that don't
   apply to a sweep that dispatches nothing.
3. **`red_or_paused` guard type** (skip the sweep on main-red too). Rejected: a genuinely dead
   container should still be recovered to Blocked while main is red; suppressing orphan recovery
   during a main-red incident would leave stale "In progress" items stuck with no path back onto the
   board until main goes green.
4. **Per-issue-scoped sentinel** (embed the paused issue number, skip only that issue in the sweep).
   Rejected per Q&A as disproportionate to this ticket's scope and to the current
   `factory_wip_limit: 1` reality; noted as a future-conditional follow-up instead (see Assumptions).

---

## Open Questions (Non-blocking)

- Whether the "Orphaned Run Recovered ... died without its error handler executing" comment should
  be worded differently for the case where a paused run's item is later swept for real (e.g. the
  container simply was never redispatched after `resume_at`) — not required by this ticket's stated
  fix ("gate the sweep behind the sentinel"); tracked as a possible follow-up, not blocking here.
- The retry-counter consumption by a mis-swept paused run is #341's scope, not this ticket's.

---

## Assumptions

- `scheduler.factory_wip_limit` stays at `1` for the dark-factory self-target instance, as it does
  today (`config/config.yaml:6`) and as the #35 session-window-backoff design already assumes
  (`docs/archive/2026-07-13-scheduler-session-window-backoff-design.md:222`). The global (not
  per-issue) `paused_only` gate on `stage_orphan_sweep` is exactly correct under this assumption. If
  `factory_wip_limit` is ever raised above 1, the sentinel would need to carry the paused issue
  number for the sweep to be scoped per-issue instead of globally — out of scope here, called out as
  a condition for revisiting this design, not in-scope work.
- `dispatch_stage` and the `STAGE_GUARD`/`STAGE_SKIP_ACTION` tables are already defined earlier in
  `scheduler.sh` than both the relocated sentinel-read block and the sweep call site (both sit inside
  the `while true` poll loop, after all the tables/functions are defined above it) — no forward-
  reference or definition-ordering issue exists in moving the call site.
