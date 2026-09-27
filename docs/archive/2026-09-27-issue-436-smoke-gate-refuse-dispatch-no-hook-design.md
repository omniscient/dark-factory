# Smoke-gate: refuse dispatch (don't latch main-red) when a target declares no hook

**Issue:** #436

## Overview / Problem statement

The built-in smoke-gate default (`smoke_gate.sh::_default_smoke_gate`, invoked by
`scripts/hooks.sh::run_hook` whenever no target hook is *present* by `run_hook`'s own
test — `[ -f ] && [ -s ]` at `scripts/hooks.sh:31`, i.e. absent, a directory, or a
zero-byte placeholder) runs a hardcoded MarketHawk check: `npx tsc` in `frontend/`
and `python -c "import app.main"` in `backend/`. Any target repo that does not share
that exact layout fails this check on its very first dispatched run. The failure is
then treated exactly like a genuinely red `main` — `_smoke_on_red` writes the global
`main-is-red` sentinel, files a regression ticket, and **halts all implementation
dispatch (Priority 1.5/2/3) for the instance** — refine/plan/merge continue
(`scheduler.sh:1610-1611`; the regression-ticket body says the same at
`smoke_gate.sh:118`) — even though nothing about the target's `main` branch is
actually broken; the target simply hasn't added its own hook yet.

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

1. **Trigger condition: no hook *present*, by `run_hook`'s own presence test.** The
   new path fires only where `run_hook` falls back to the built-in default today —
   i.e. no target hook is *present* by `run_hook`'s own test (`[ -f ] && [ -s ]`,
   `scripts/hooks.sh:31`): absent, a directory, or a zero-byte placeholder. A
   present-but-non-executable hook is explicitly **unaffected**: it keeps #438/#454's
   `bash "$hook"` + `hook-not-executable` warning path (`scripts/hooks.sh:36-48`), and
   the implementation must not divert it into the new path. Both
   `scripts/hooks.sh:10-11` and `docs/onboarding-new-target.md:121-122` state the
   post-#454 rule: the built-in default runs only for an absent, directory or
   zero-byte hook.
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
   Verified 2026-09-27 via `gh api repos/<repo>/git/trees/main?recursive=1`: both live
   targets already ship `.factory/hooks/smoke-gate` on `main` at mode **100755** —
   MarketHawk 1461 bytes, jobfinder 407 bytes — so neither reaches the new path, and
   neither is affected by the non-executable carve-out above.
2. **Mechanism: a new, separate code path — never the `main-is-red` machinery.**
   `_smoke_on_red`/`_smoke_on_green` and the `main-is-red` sentinel/regression-ticket
   machinery are for a genuinely red `main` and must not fire for a missing hook. The
   no-hook path must not write to `SMOKE_STATE_DIR`, must not call `_smoke_on_red` or
   `_smoke_on_green`, and must not file or touch any regression ticket. A shared
   global "pause everything" signal for the no-hook case is explicitly out of scope
   for this ticket (it would be a scheduler-dispatch policy change, gate-adjacent per
   CLAUDE.md's "Hard limits," and needs its own reviewed ticket if ever wanted).
   **Accepted blast radius:** without the latch there is no instance-wide brake, so on
   a genuinely hook-less target the scheduler walks the whole board and every eligible
   ticket costs one dispatch, one failed container, one post-mortem
   (`entrypoint.sh:246`), a `set_board_status "blocked"` (`entrypoint.sh:633`), a
   failure comment and a cost report — then the retry loop re-dispatches it until
   `MAX_RETRIES` or the two-identical-signature stop (`scheduler.sh:1245`). Today's bug
   is self-limiting (one ticket, one bogus regression ticket) precisely because the
   latch stops Priority 1.5/2/3; this is strictly more expensive and is accepted by
   design, per the operator note's "fail fast, per the affected run" framing.
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
4. **Message delivery — three channels, two of them durable. Stderr alone is not a
   signal path:**
   - A `[smoke_gate]`-prefixed stderr message naming the missing hook path
     (`.factory/hooks/smoke-gate`), the starter template
     (`templates/new-target/.factory/hooks/smoke-gate`), and the onboarding doc
     section (`docs/onboarding-new-target.md` step 3b) — and stating explicitly that
     `main` was **not checked** and **has not** been marked red.
   - **A durable trace, because stderr is discarded by construction.** Dispatch is
     `run -d --rm` (`scheduler.sh:378`), so a run's stderr never reaches the
     scheduler log and dies with the container — `scripts/hooks.sh:38-40` states
     exactly this as the reason #438 added a durable log. The new path must append a
     `smoke-gate-hook-missing` line to `${SCHEDULER_STATE_DIR}/hook-warnings.log`
     reusing #438's guards verbatim: only when `[ -d "$state_dir" ]`, never `mkdir`
     (CI asserts `/var/lib/dark-factory` stays empty), never fatal
     (`scripts/hooks.sh:41-46`).
   - **A dedicated marker comment on the ticket carrying the actionable text.** This
     *is* new comment-marker plumbing and it is required, because no existing path
     can carry the message. `TMP_OUT` is first assigned at `entrypoint.sh:912`, after
     the gate call at `entrypoint.sh:791`, so `on_failure` passes an empty transcript
     to both `_write_error_signature` and `run_post_mortem` (`entrypoint.sh:611`,
     `:629`, `:631`); and `run_post_mortem` early-returns for `deconflict` altogether
     (`entrypoint.sh:251`). The generic `FACTORY_FAILURE_MARKER` (`entrypoint.sh:227`,
     posted `:637`) and `REFINE_FAILURE_MARKER` (`:226`, posted `:614`) bodies say
     only "exit code N". So add `SMOKE_HOOK_MISSING_MARKER` — an HTML comment marker
     in the same style as `FACTORY_FAILURE_MARKER` at `entrypoint.sh:227` — and post
     it from `_smoke_hook_missing` **only when `ISSUE_NUM` is non-empty** (so
     `recheck`, which has no ticket context, posts nothing), guarded
     `2>/dev/null || true` so a comment failure can never mask the refusal.
   - A non-blocking `run-record health-event` (`factory.smoke_gate.hook_missing`)
     for recurrence-detection telemetry, following the existing pattern used
     elsewhere in `entrypoint.sh`/`scripts/shims/*` (`entrypoint.sh:157-161`,
     `scripts/shims/gh:49`). **Emit it last**, after the stderr message, the durable
     line and the comment, and guard it `2>/dev/null || true`: `entrypoint.sh` runs
     under `set -euo pipefail` (`entrypoint.sh:2`), so an unguarded failing
     `python3 … health-event` placed earlier in the function would abort the function
     before the actionable message is ever printed.
   - No regression ticket is filed and no `main-is-red`-style comment is posted —
     this is a per-run failure, not an instance-wide halt.

## Architecture / approach

- `smoke_gate.sh`: add a new function (e.g. `_smoke_hook_missing`) alongside
  `_smoke_check_main`/`_default_smoke_gate`. It logs the actionable message
  described above, appends the durable `hook-warnings.log` line, posts the
  `SMOKE_HOOK_MISSING_MARKER` comment when `ISSUE_NUM` is set, emits the health
  event last, and returns non-zero. It touches no sentinel/state files and calls
  neither `_smoke_on_red` nor `_smoke_on_green`.
  `_smoke_check_main`, `_default_smoke_gate`, and the public `run_smoke_gate`
  wrapper are **left in place, unchanged** — `tests/test_smoke_gate.sh` exercises
  `run_smoke_gate` directly to test the on-red/on-green sentinel state machine in
  isolation from hook-presence detection, and that machinery is still correct and
  needed for any caller that wants to invoke the MarketHawk-parity check explicitly.
- **`_smoke_hook_missing` must `return 1`, never `exit`.** `entrypoint.sh` installs
  only `trap on_failure ERR` (`entrypoint.sh:659`) and no EXIT trap, and an ERR trap
  does not fire on `exit`. An `exit 1` copied from the neighbouring `_smoke_on_red`
  (which ends `exit 0`, `smoke_gate.sh:130`) would bypass the Blocked transition and
  the failure comment entirely and strand the ticket in **In Progress** — strictly
  worse than the bug this ticket fixes.
- `scripts/hooks.sh::run_hook`: in the `smoke-gate`-and-no-target-hook branch,
  replace the `_default_smoke_gate "$@"` call with `_smoke_hook_missing "$@"`. This
  is the entire behavioral pivot — every other branch of `run_hook` (target hook
  present, non-smoke-gate hooks) is untouched.
- Update the stale comment in the doc block above `_default_smoke_gate`
  (`smoke_gate.sh:161-164`; the offending sentence "Parity invariant: MarketHawk
  needs zero hooks" is at `smoke_gate.sh:163` — the file header at
  `smoke_gate.sh:2-5` stays accurate and is not the target) and the README `Hooks`
  table's smoke-gate row (`README.md:232`, currently "Built-in default: tsc + backend
  import checks (MarketHawk)") to describe the new behavior instead of the old parity
  assumption. Also update the two onboarding passages this ticket obsoletes — one of
  which the new message tells operators to go read:
  - `docs/onboarding-new-target.md:87-90` — "**Without this hook it runs MarketHawk's
    check** … latches `main-is-red`, files a regression ticket, and halts all dispatch
    (tracked in #436)".
  - `docs/onboarding-new-target.md:121-122` — "An empty hook file is treated as
    absent, so the built-in default runs."
- **Out of scope: `templates/new-target/.factory/hooks/smoke-gate`.** Its comment
  (lines 6-7, "Without this hook the factory runs MarketHawk's check…") is stale too,
  but `.factory/adapter.yaml`'s `safety.hard_exclude_paths` contains `.factory/hooks/`
  and the matcher is a substring test (`scripts/factory_core/epic_autopilot.py:73-75`,
  `if ex in p`), so that path falls inside the hard-excluded pattern. Leave the file
  untouched here; its two comment lines are a docs follow-up ticket.
- **Pre-merge checklist item (blocking, one command).** Before this merges, confirm
  every live target's `main` carries a non-empty, executable
  `.factory/hooks/smoke-gate`. This is verifiable from here: the earlier framing that
  "this repo's clone cannot verify MarketHawk's own tree directly" was wrong — the
  factory's `gh` has the scope, and the tree API reports the mode bit as well as the
  size, which is what Requirement 1's carve-out turns on.

  ```bash
  for R in omniscient/markethawk omniscient/jobfinder; do
    gh api "repos/${R}/git/trees/main?recursive=1" \
      --jq '.tree[] | select(.path==".factory/hooks/smoke-gate") | {mode, size}'
  done
  ```

  Expected: `{"mode":"100755","size":<non-zero>}` for each. **Checked 2026-09-27 and
  it passes for both** — MarketHawk `100755`/1461 bytes, jobfinder `100755`/407 bytes
  (`docs/cutover-markethawk.md` step 2.5 and `docs/onboarding-new-target.md:9` name
  these two as the live targets). Re-run it at rollout; if a target's hook is missing
  or zero-byte, that target hits the new fail-fast path and needs its hook committed
  first — the intended, correct behavior per Requirement 1, not a regression to guard
  against in code.
- Tests. Both files below are `.sh` tests. CI runs them because it lists them per
  file (`.github/workflows/ci.yml:16` `bash tests/test_hooks.sh`, `:17` `bash
  tests/test_smoke_gate.sh`); they also run inside the container. **They cannot be
  run on the Windows host** — a new `.sh` test that is not added to `ci.yml` never
  runs anywhere.
  - `tests/test_smoke_gate.sh` — add a new phase following its existing
    stub-and-assert conventions, exercising `_smoke_hook_missing` directly. (This
    file sources `smoke_gate.sh` with `SMOKE_GATE_SOURCE_ONLY=1` at line 27 and never
    sources `scripts/hooks.sh`, so it structurally cannot reach `run_hook`; the
    dispatch-level assertions belong in `tests/test_hooks.sh` below.) Assert:
    - returns non-zero;
    - creates no sentinel/state files (`main-is-red`, `main-red-last-recheck`,
      `main-is-red-issue`);
    - makes no `tracker create`/`gh issue` regression-ticket calls;
    - the emitted message names the hook path, the template path, and the onboarding
      step.
  - `tests/test_hooks.sh` — **this file must be re-authored; the change breaks it as
    it stands.** It runs under `set -euo pipefail` (line 2), and case 9 ("Negative
    case A (a missing hook must not become a pass)", lines 135-145) calls `run_hook
    --gate smoke-gate` bare at lines 139/141/143 and then asserts `[ "$(wc -l <
    "$DEFAULT_CHECKS")" = "3" ]` at line 145. After this change the first bare call
    returns non-zero, `set -e` kills the script before `echo PASS`, and the CI job at
    `.github/workflows/ci.yml:16` goes red.
    - Re-author case 9 (absent / zero-byte / directory) to assert the refusal instead
      of `DEFAULT_CHECKS == 3`: non-zero rc from `run_hook --gate smoke-gate`,
      `_smoke_check_main` never invoked, no `main-is-red` / `main-red-last-recheck` /
      `main-is-red-issue` written, no `tracker create`, and one `hook-warnings.log`
      line.
    - Keep cases 4-8 (hook present — executable and non-executable, red and green)
      asserting the unchanged red/green latch semantics **verbatim**. They are this
      ticket's MarketHawk no-regression proof and must not be edited.
    - Add a case asserting that a present-but-non-executable hook still reaches the
      `bash "$hook"` path with its `hook-not-executable` warning — the #438/#454
      regression guard for Requirement 1's carve-out.
    - Add an assertion that `run_hook smoke-gate` **without** `--gate` also fails.
      `scripts/hooks.sh:71` is `if [ "$gate" = "1" ]; then return "$rc"; else return
      0; fi`, so unlike `_smoke_on_red`'s `exit 0` a bare `return 1` is silently
      discarded by a non-gate caller and `main` would go unchecked with nothing said.
      Today the only caller uses `--gate` (`entrypoint.sh:791`); pair the assertion
      with a one-line comment at `scripts/hooks.sh:67` recording that the smoke-gate
      arm is `--gate`-only.

## Known limitations (accepted for this ticket)

- **The failure signature will read `environmental:delivery_failure`, and the
  breaker reason will name #279.** `on_failure` calls `_write_error_signature` with
  an empty transcript (`entrypoint.sh:611`, `:629` — `TMP_OUT` is not assigned until
  `entrypoint.sh:912`, well after the gate call at `:791`), and
  `entrypoint.sh:396-397` documents the consequence: such failures "classify as
  environmental:delivery_failure by construction (fast, zero commits, no artifact)".
  The scheduler then routes the ticket through `retry_or_skip_delivery_failure`
  (`scheduler.sh:1251`) and, on a second identical signature, trips with a reason
  naming the suspected runner prompt-delivery bug (`scheduler.sh:1107`) rather than
  the missing hook. Accepted for this ticket because the `SMOKE_HOOK_MISSING_MARKER`
  comment (Requirement 4) carries the real cause to the ticket. A distinct
  `configuration:*` signature is a follow-up ticket, not part of this change.

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

- ~~Whether MarketHawk's `main` currently has an executable
  `.factory/hooks/smoke-gate`~~ — **resolved 2026-09-27.** Checked with the
  Architecture section's `gh api …/git/trees/main?recursive=1` command; both live
  targets pass (MarketHawk `100755`/1461 bytes, jobfinder `100755`/407 bytes). The
  re-run stays on the pre-merge checklist because either target could regress the
  mode bit before rollout.
- Issues #222 and #406, cited in the issue body as "same class" (MarketHawk-parity
  defaults trapping new targets in other subsystems), are explicitly out of scope
  here and are left as separate tickets.

## Assumptions

- No target currently depends on the built-in MarketHawk-shaped tsc/python-import
  check actually running for real (i.e., no non-MarketHawk target is passing this
  check today by coincidentally matching the layout) — based on Phase 3 codebase
  search finding no other reference to this check being relied upon, and all three
  known real targets — this repo, MarketHawk, and `omniscient/jobfinder`
  (`docs/onboarding-new-target.md:9`) — verified as shipping their own hook at mode
  100755 (see Requirement 1's verification note).
- `entrypoint.sh`'s existing `trap on_failure ERR` per-intent handling (immediate
  Blocked for `fix`/`continue`; comment + scheduler retry counter for `deconflict`;
  no ticket context for `recheck`) is the correct, already-established mechanism for
  "fail this run visibly" and does not need new per-intent branching added on top of
  it for this ticket.
