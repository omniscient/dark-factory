# Close-dispatch guard — stop the unguarded, unbudgeted Close loop in review triage

**Issue:** #414

## Overview / Problem statement

`stage_review_triage()` in `scheduler.sh` classifies new PR comments on "In Review" items
(cached per `(issue, latest-comment-id)` since #402, so a repeat classification costs zero
Haiku calls) and dispatches on the verdict. Its `MERGE` case has neither of the two
safeguards its sibling `CONTINUE` case has:

```bash
# scheduler.sh:1121-1136 (current)
case "$VERDICT" in
  MERGE)
    if dispatch "Close issue #${ISSUE}"; then          # no is_issue_running guard
      DISPATCHED="Close issue #${ISSUE}"
    fi
    ;;
  CONTINUE)
    if ! is_issue_running "$ISSUE"; then                # guarded
      if dispatch "Continue issue #${ISSUE}"; then
        DISPATCHED="Continue issue #${ISSUE}"
        reset_retry "$ISSUE"
      fi
    fi
    ;;
  SKIP) ;;
esac
```

Two consequences, both now free to repeat because #402 made the cache hit cost nothing:

1. **Stacking.** A slow or stuck `Close` container can be joined by a second `Close`
   dispatch for the same issue on the very next poll.
2. **Unbounded, silent retries.** If a `Close` run keeps failing to move the issue off "In
   Review" (merge conflict, branch protection, a flaky `gh pr merge`), every subsequent poll
   re-dispatches another `Close` container, forever, at zero classification cost and with no
   circuit breaker to stop it or surface it to a human.

While locating this code, we found a second, structurally identical instance of the same
defect in the same file: `end_gate_check()` (the `direct-to-pr` auto-merge end-gate, called
from inside `stage_review_triage` — scheduler.sh:1112 — and short-circuiting the `MERGE` case
above via `continue` when it fires) has:

```bash
# scheduler.sh:323-355 (current)
end_gate_check() {
  ...
  case "$review_state" in
    APPROVED)
      if dispatch "Close issue #${issue_num}"; then     # no is_issue_running guard
        DISPATCHED="Close issue #${issue_num}"
      fi
      return 0
      ;;
    CHANGES_REQUESTED)
      if ! is_issue_running "$issue_num"; then           # guarded
        if dispatch "Continue issue #${issue_num}"; then
          DISPATCHED="Continue issue #${issue_num}"
          reset_retry "$issue_num"
        fi
      fi
      return 0
      ;;
  esac
}
```

For a `direct-to-pr` issue this is actually the *worse* case: the PR's `APPROVED` review
state is sticky, so a failing `Close` re-dispatches on every poll without even needing a new
comment to re-trigger it.

Found by the operator review of PR #413.

## Requirements (from Q&A)

Full Q&A dialogue is in the "Refinement Pipeline" issue comment. Resolutions below are
load-bearing.

1. **Both Close dispatch sites are in scope: `stage_review_triage`'s `MERGE` case and
   `end_gate_check`'s `APPROVED` case.** The issue text names only the former, but the latter
   is part of the same call path (`end_gate_check` is called from inside
   `stage_review_triage`, scheduler.sh:1112) and is reached first for `direct-to-pr` issues —
   fixing only the named case would leave the worse instance of the exact same bug open on
   the very path the issue's own economics argument relies on. A spillover ticket would just
   re-litigate a decision this spec already makes. This is the dedicated, reviewed ticket for
   this scheduler/breaker surface (not a side effect of unrelated work), so CLAUDE.md's "never
   weaken a gate as a side effect" limit does not block addressing both — this *adds* breaker
   coverage where none exists.

2. **Guard shape: `is_issue_running` first, matching `CONTINUE`/`CHANGES_REQUESTED`.** Both
   Close dispatch sites skip immediately if a container for the issue is already running —
   this alone kills the "stacking" half of the problem and costs nothing (same primitive
   already used everywhere else in this file).

3. **Retry budget: extend the existing breaker pattern to both Close sites, as one shared
   counter.** `scheduler.sh` already has four call sites doing exactly this —
   `evaluate_stop <issue> <phase> <ceiling>` → `trip_to_blocked` on `stopped=true`, else
   dispatch — for phases `implement` (`stage_blocked_retry`), `plan` (`stage_plan`), `refine`
   (`stage_refine`), and `resolve` (`stage_conflict_resolve`). A new phase, `close`, extends
   the same pattern:
   - Both sites call one new shared helper, `dispatch_close <issue_num>` (defined once in
     `scheduler.sh`, alongside `end_gate_check`/`dispatch`), so the two paths cannot drift
     apart the way `MERGE`/`APPROVED` already have:
     ```bash
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
     ```
     `MERGE` and `APPROVED` both become `if dispatch_close "$ISSUE"; then DISPATCHED="Close issue #${ISSUE}"; fi`.
   - **One counter for both sites.** Both dispatch the identical `Close issue #N` workflow, so
     a failure from either path counts against the same budget: phase string `"close"`, state
     key `"$ISSUE:close"` (`breaker.py::_make_key` turns any phase other than the
     special-cased `"implement"` into `f"{issue_num}:{phase}"`, so this is a brand new,
     independent counter — it is not touched by `CONTINUE`/`CHANGES_REQUESTED`'s bare-key
     `reset_retry "$ISSUE"`, which clears the `implement` counter only).
   - **Ceiling: reuse `$MAX_RETRIES`** (`scheduler.max_retries`, config default 3) — the same
     ceiling `stage_conflict_resolve`'s `resolve` phase uses. No new config knob.
   - **No `reset_retry` for `close`, ever.** Resetting the bare `implement` counter from the
     Close path would let a failing Close mask real implement failures; there is no "fresh
     input arrived" re-trigger event for `close` the way `spec_advance_check` provides for
     `refine`/`plan` — once a Close actually succeeds, the issue leaves "In Review" and
     neither triage path looks at it again, so the counter simply goes stale (consistent with
     how `resolve`/`plan`/`refine`'s counters behave once their respective phases complete).
   - **No `check_failure_signature` for `close` in this ticket.** The two-strikes
     same-signature early trip is not required to close the forever-loop gap and there is no
     evidence yet that the Close workflow writes a failure-signature drop file; add it later
     if that changes.
   - **`breaker.py::trip_to_blocked`'s `retry_cmds` dict needs a `"close"` entry**
     (`"close": f"Close issue #{issue_num}"`). Without it, the trip comment's "re-run
     manually" command falls through to the wrong default, `Fix issue #{issue_num}` — a
     command that does not exist in this file's dispatch vocabulary.
   - Update the usage comment above `trip_to_blocked()` (scheduler.sh:388,
     `implement|plan|refine|resolve`) to list `close` too.

4. **`end_gate_check`'s `APPROVED` case must still `return 0`** whether `dispatch_close`
   skips (already running), trips (ceiling reached), or dispatches — exactly as today, so
   control never falls through to comment classification and double-dispatches Close for the
   same issue in the same poll cycle. `CHANGES_REQUESTED` is untouched; it already matches the
   `CONTINUE` pattern.

## Architecture / Approach

Add one helper, `dispatch_close()`, next to `end_gate_check`/`dispatch` in `scheduler.sh`,
implementing the guard → breaker-check → dispatch sequence from Requirement 3. Replace the
bodies of `stage_review_triage`'s `MERGE` case (scheduler.sh:1122-1125) and
`end_gate_check`'s `APPROVED` case (scheduler.sh:334-339) with calls to it, preserving each
call site's existing `DISPATCHED=`/`return 0` surroundings. Add the `"close"` entry to
`breaker.py::trip_to_blocked`'s `retry_cmds` dict and to the `trip_to_blocked()` usage
comment's phase list.

This is a same-shape, low-risk extension of a pattern already proven at four other call
sites in this file — no new state-file schema, no new config knob, no new CLI surface.

### Tests (TDD, `tests/test_scheduler.sh`)

Mirroring the existing stubbed `evaluate_stop`/`trip_to_blocked` pattern used for the
`resolve`/`implement`/`plan`/`refine` phases elsewhere in this file:

1. `MERGE` verdict is skipped (no dispatch) while `is_issue_running` reports true.
2. `APPROVED` end-gate is skipped (no dispatch, still `return 0`) while `is_issue_running`
   reports true.
3. `MERGE` verdict trips to Blocked with phase `close` once `evaluate_stop` reports
   `stopped=true` at the ceiling; `trip_to_blocked "$ISSUE" "close" ...` is asserted.
4. `APPROVED` end-gate trips to Blocked with phase `close` at the ceiling, and still
   `return 0` without falling through to further classification.
5. Neither `close`-phase dispatch touches the bare-key `implement` counter (`reset_retry` is
   never called from either Close path; a stubbed `reset_retry` records zero calls across
   both tests).
6. `dispatch_close` is called with the same issue from both `MERGE` and `APPROVED` and both
   increments land on the same `evaluate_stop` key (asserted via the stubbed call log, not by
   re-deriving `_make_key`).

## Alternatives considered

1. **Minimal fix: add only the `is_issue_running` guard, defer retry-budget consumption to a
   separate ticket.** Rejected — this is the literal minimum the issue's item 1 asks for, but
   it leaves the "repeats forever at zero cost" half of the problem (the actual economic
   concern the issue raises) completely open, on both Close paths. The issue's own item 2
   explicitly asks this ticket to decide the retry-budget question, not defer it.
2. **Fix only `stage_review_triage`'s `MERGE` case, file `end_gate_check` as a spillover
   ticket.** Rejected per Requirement 1 — `end_gate_check` is reached first, from inside the
   same function, on the same bug, and is strictly worse for `direct-to-pr` issues (sticky
   `APPROVED` state re-fires every poll with no new comment needed). Splitting it would also
   require inventing the shared counter design twice or reconciling two independently-chosen
   phase names later.
3. **Duplicate the guard+breaker logic inline at both call sites instead of one shared
   helper.** Rejected — `MERGE`/`CONTINUE` and `APPROVED`/`CHANGES_REQUESTED` already drifted
   out of sync once (this issue is the result); a single `dispatch_close()` helper is the
   change that prevents a third occurrence of the same asymmetry.
4. **Add `check_failure_signature` two-strikes early-trip for `close` now, matching
   `resolve`/`plan`/`refine`/`implement`.** Deferred, not rejected outright — there is no
   current evidence the Close workflow writes a failure-signature drop file, so the early-trip
   path would never fire; add it in a follow-up once that's established, to keep this change
   minimal and testable.

## Open questions (non-blocking)

- Should the Close workflow (`entrypoint.sh`'s handling of the `"Close issue #N"` command)
  start writing a failure-signature drop file on a failed merge attempt, enabling the
  two-strikes early trip (Alternative 4) later? Worth a follow-up issue once this ships and
  there's real signal on *why* Close attempts fail.
- `trip_to_blocked`'s board-move + `needs-discussion`/`factory-regression` labelling is
  identical across phases; this spec does not change that behavior for `close` beyond adding
  it to the `retry_cmds` map. If a `close` trip ever needs a different human remediation
  message, that's a separate, later ticket.

## Assumptions (flagged)

- `MAX_RETRIES` (`scheduler.max_retries`, default 3) is the right ceiling for `close`, by
  analogy with `resolve`'s identical reuse of `$MAX_RETRIES` rather than the refine-side
  `$REFINE_MAX_RETRIES`. Both `close` and `resolve` act on an already-implemented PR/issue,
  not on the pre-implementation refine/plan loop.
- No existing test currently exercises a successful `MERGE`/`APPROVED` dispatch's absence of
  a `reset_retry` call for `close` — Requirement 3's "no reset, ever" rule is new behavior,
  not a regression guard for something previously tested.
