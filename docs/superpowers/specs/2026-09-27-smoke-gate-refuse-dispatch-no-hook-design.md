# Smoke-gate: refuse dispatch (don't latch main-red) when a target declares no hook

**Issue:** #436

## Overview / Problem statement

The built-in smoke-gate default (`smoke_gate.sh::_default_smoke_gate`, invoked by
`scripts/hooks.sh::run_hook` whenever `${CLONE_DIR}/.factory/hooks/smoke-gate` is
absent or non-executable) runs a hardcoded MarketHawk check: `npx tsc` in `frontend/`
and `python -c "import app.main"` in `backend/`. Any target repo that does not share
that exact layout fails this check on its very first dispatched run. The failure is
then treated exactly like a genuinely red `main` — `_smoke_on_red` writes the global
`main-is-red` sentinel, files a regression ticket, and **halts all dispatch for the
instance**, even though nothing about the target's `main` branch is actually broken;
the target simply hasn't added its own hook yet.

An operator note on this issue (2026-09-26, owner account) sets the required framing:
this is **not** a "relax/skip the check" fix. The main-red gate itself is correct and
stays as-is for any target that has (or should have) a working smoke check. What must
change is the *no-hook* case specifically: it must fail fast, per the affected run,
with an actionable message telling the operator which hook to add and where — instead
of being silently absorbed into the shared main-red sentinel that pauses every other
ticket for an unrelated reason.

## Requirements (from Q&A)

Two questions were brainstormed with a product-owner review of the codebase (full
dialogue in the pipeline comment); the resolutions below are load-bearing.

1. **Trigger condition: hook absence alone.** The new path fires whenever
   `${CLONE_DIR}/.factory/hooks/smoke-gate` is missing or not executable — full stop.
   No "does this look like MarketHawk" layout heuristic (e.g. sniffing for
   `frontend/tsconfig.app.json` + `backend/app/main.py`) is introduced. Rationale:
   the operator note's own trigger condition is "declares no smoke-gate hook," not
   layout; every target this factory currently dispatches to is expected to supply
   its own hook — `docs/onboarding-new-target.md` step 3b already marks
   `.factory/hooks/smoke-gate` **required**, this repo (self-target) ships its own
   (`.factory/hooks/smoke-gate`, pytest + workflow-DAG checks), and MarketHawk's own
   cutover checklist (`docs/cutover-markethawk.md` step 2.5) already lists "Adapter
   hooks (`smoke-gate`, `validate`) running without errors" as an exit criterion —
   i.e. MarketHawk is expected to supply its own hook too, not lean on the built-in
   default. A layout heuristic would add a first-of-its-kind detection helper to
   cover a target that, per current onboarding contract, shouldn't exist.
2. **Mechanism: a new, separate code path — never the `main-is-red` machinery.**
   `_smoke_on_red`/`_smoke_on_green` and the `main-is-red` sentinel/regression-ticket
   machinery are for a genuinely red `main` and must not fire for a missing hook. The
   no-hook path must not write to `SMOKE_STATE_DIR`, must not call `_smoke_on_red` or
   `_smoke_on_green`, and must not file or touch any regression ticket. A shared
   global "pause everything" signal for the no-hook case is explicitly out of scope
   for this ticket (it would be a scheduler-dispatch policy change, gate-adjacent per
   CLAUDE.md's "Hard limits," and needs its own reviewed ticket if ever wanted).
3. **Outcome: fail the run, don't exit 0 clean.** Unlike a red `main` (not the
   ticket's fault — clean `exit 0`, no per-ticket blast radius, per `tests/
   test_smoke_gate.sh`'s existing assertion), a missing hook is a static target
   misconfiguration that reproduces identically on every run until a human commits
   the hook. Silently returning success (or a clean no-op halt) would leave the
   ticket stuck in **In Progress** with no signal — the exact "stranded, mislabeled
   state" CLAUDE.md warns about. The new path returns non-zero from `run_hook --gate
   smoke-gate`, propagating through `entrypoint.sh`'s existing `trap on_failure ERR`
   machinery unchanged:
   - `fix`/`continue`: `on_failure` moves the ticket straight to **Blocked** and
     posts the `FACTORY_FAILURE_MARKER` comment (existing behavior, no new code).
   - `deconflict`: `on_failure` posts the `REFINE_FAILURE_MARKER`-style comment;
     board Blocked transition is handled by the scheduler's existing retry counter
     (`trip_to_blocked`), matching how `deconflict` failures are already handled —
     not special-cased differently by this ticket.
   - `recheck`: has no `ISSUE_NUM`/ticket context (it exists solely to re-test
     whether `main` went green); it gets the log line and health event only, with no
     comment or board change. (In practice `recheck` is dispatched only when a
     `main-is-red` sentinel exists, and this path never writes that sentinel, so a
     no-hook target will not actually cause `recheck` dispatches — noted for
     completeness.)
4. **Message delivery — loud log, health-event telemetry, and (indirectly) the
   ticket via the existing failure-comment path:**
   - A `[smoke_gate]`-prefixed stderr message naming the missing/non-executable
     path (`.factory/hooks/smoke-gate`), the starter template
     (`templates/new-target/.factory/hooks/smoke-gate`), and the onboarding doc
     section (`docs/onboarding-new-target.md` step 3b) — and stating explicitly that
     `main` was **not checked** and **has not** been marked red. This message lands
     in the run transcript that `run_post_mortem`/the existing failure-comment path
     already surfaces to the ticket for `fix`/`continue`/`deconflict` — no new
     comment-marker plumbing is added by this ticket.
   - A non-blocking `run-record health-event` (`factory.smoke_gate.hook_missing`)
     for recurrence-detection telemetry, following the existing pattern used
     elsewhere in `entrypoint.sh`/`scripts/shims/*`.
   - No regression ticket is filed and no `main-is-red`-style comment is posted —
     this is a per-run failure, not an instance-wide halt.

## Architecture / approach

- `smoke_gate.sh`: add a new function (e.g. `_smoke_hook_missing`) alongside
  `_smoke_check_main`/`_default_smoke_gate`. It logs the actionable message
  described above, emits the health event, and returns non-zero. It touches no
  sentinel/state files and calls neither `_smoke_on_red` nor `_smoke_on_green`.
  `_smoke_check_main`, `_default_smoke_gate`, and the public `run_smoke_gate`
  wrapper are **left in place, unchanged** — `tests/test_smoke_gate.sh` exercises
  `run_smoke_gate` directly to test the on-red/on-green sentinel state machine in
  isolation from hook-presence detection, and that machinery is still correct and
  needed for any caller that wants to invoke the MarketHawk-parity check explicitly.
- `scripts/hooks.sh::run_hook`: in the `smoke-gate`-and-no-target-hook branch,
  replace the `_default_smoke_gate "$@"` call with `_smoke_hook_missing "$@"`. This
  is the entire behavioral pivot — every other branch of `run_hook` (target hook
  present, non-smoke-gate hooks) is untouched.
- Update the stale `smoke_gate.sh` header comment ("Parity invariant: MarketHawk
  needs zero hooks") and the README `Hooks` table's smoke-gate row (currently
  "Built-in default: tsc + backend import checks (MarketHawk)") to describe the new
  behavior instead of the old parity assumption.
- Rollout precondition (operational, not code): before this ships to the MarketHawk
  instance, confirm MarketHawk's `main` actually has an executable
  `.factory/hooks/smoke-gate` committed — `docs/cutover-markethawk.md` step 2.5
  implies it does, but this repo's clone cannot verify MarketHawk's own tree
  directly. If it turns out MarketHawk does *not* yet have one, MarketHawk itself
  would hit the new fail-fast path and need its hook added first — this is the
  intended, correct behavior per Requirement 1, not a regression to guard against
  in code.
- Tests: add a new phase to `tests/test_smoke_gate.sh`, following its existing
  stub-and-assert conventions, exercising `_smoke_hook_missing` (or `run_hook`'s
  dispatch into it) directly:
  - returns non-zero;
  - creates no sentinel/state files (`main-is-red`, `main-red-last-recheck`,
    `main-is-red-issue`);
  - makes no `tracker create`/`gh issue` regression-ticket calls;
  - the emitted message names the hook path, the template path, and the onboarding
    step.

## Alternatives considered

1. **Skip the check with a loud log line, proceed as green** (the issue body's
   original proposal). Rejected per the operator note: this reads as relaxing/
   skipping the gate rather than refusing dispatch, and CLAUDE.md's "never weaken
   safety gates... as a side effect of another change" hard limit applies — a
   silently-passing check for an unverified target is exactly the kind of weakening
   that rule exists to prevent.
2. **Layout-detect MarketHawk and only run the tsc/import check when that layout is
   present, otherwise skip.** Rejected for the same reason as (1) — it is still a
   "skip" outcome for the non-MarketHawk case — plus it requires inventing a new
   layout-sniffing heuristic that no other part of the codebase has, for a
   compatibility scenario (an undeclared target somehow relying on the built-in
   check) that Requirement 1's research found no evidence of.
3. **Reuse the `main-is-red` global sentinel with different wording.** Rejected:
   conflates two semantically different conditions (main is actually broken vs. the
   factory doesn't know how to check this target yet) under one signal, and would
   pause dispatch for every *other* ticket on an instance because of one
   misconfigured target — the opposite of "per-run" fail-fast the operator asked
   for.

## Open questions (non-blocking)

- Whether MarketHawk's `main` currently has an executable `.factory/hooks/smoke-gate`
  should be confirmed operationally before/at rollout (see Architecture's rollout
  precondition); this spec does not gate implementation on that confirmation since
  the correct behavior (fail fast and say so) is safe either way.
- Issues #222 and #406, cited in the issue body as "same class" (MarketHawk-parity
  defaults trapping new targets in other subsystems), are explicitly out of scope
  here and are left as separate tickets.

## Assumptions

- No target currently depends on the built-in MarketHawk-shaped tsc/python-import
  check actually running for real (i.e., no non-MarketHawk target is passing this
  check today by coincidentally matching the layout) — based on Phase 3 codebase
  search finding no other reference to this check being relied upon, and both known
  real targets (this repo, MarketHawk) already shipping or being expected to ship
  their own hook.
- `entrypoint.sh`'s existing `trap on_failure ERR` per-intent handling (immediate
  Blocked for `fix`/`continue`; comment + scheduler retry counter for `deconflict`;
  no ticket context for `recheck`) is the correct, already-established mechanism for
  "fail this run visibly" and does not need new per-intent branching added on top of
  it for this ticket.
