# Scheduler Internal Seams — Stage Functions + Sourceable Predicate Lib

**Issue:** omniscient/dark-factory#185
**Status:** draft — pending review
**Re-refine of:** a 2026-07-07 spec on a since-deleted branch, discarded per operator instruction
(2026-07-22 comment) because it was refined against a stale `main`. This document re-derives
every figure and line-anchor from current `main` rather than trusting the issue body or the
prior spec.

---

## Overview / Problem Statement

`scheduler.sh` runs the factory's single `while true` poll loop: each cycle it fetches the
project board, then walks nine numbered priority stages (P0, P0.6, P1.5, P1, P2, P3, P4, P5,
P6) plus one previously-unenumerated orphan-recovery sweep, all inlined as sequential
`while read item` / conditional blocks in one function-less body.

**Re-verification against current `main` (this pass):** the issue's headline claim — a
`MAIN_IS_RED` skip-guard copy-pasted verbatim across dispatch stages, and a large block of
item-predicate functions that can only be unit-tested by sourcing the entire file with
`gh`/`docker` stubbed — **still holds**. Unlike sibling ticket #181 (whose re-refinement found
its own headline claim had gone false), #185's core duplication is real and unresolved. Only
the specific figures were stale:

| Figure | Issue body / prior spec claimed | Current `main` (verified 2026-07-23) |
|---|---|---|
| `scheduler.sh` total lines | 1184 | **1297** |
| `while true` loop span | L799–1184 (385 lines) | **L848–1297 (~450 lines)** |
| `MAIN_IS_RED` guard duplication | "copy-pasted three times" at L935/1005/1055 | **Still exactly 3 verbatim copies**, now at **L1011, L1088, L1138** — identical string `if [ "$MAIN_IS_RED" = "true" ] || [ "$SESSION_WINDOW_PAUSED" = "true" ]` |
| Predicate-function block | "~200 lines" at L237–448 | **~208 lines at L236–443** — the estimate was already close; only line numbers drifted |
| `test_scheduler.sh` size | 917 lines | **1113 lines** |

The growth between the issue's original figures and today (+113 lines in `scheduler.sh`, +196
in its main test file) comes from unrelated features shipped since the issue was filed —
session-window-pause handling (#35/#305), the main-red-recheck/fixer self-clear (#365), and
dispatch-ceiling above-ceiling park-for-human handling (#339) — not from any change to the
duplication this ticket targets.

**Solution (unchanged in shape from the original proposal, re-anchored to current line
numbers):** move side-effect-free item predicates into a new `scripts/scheduler_lib.sh`
(sourceable standalone, mirroring `scripts/gate_lib.sh`); turn each loop-inlined priority block
into a named `stage_*` function; collapse the three verbatim-duplicated guard conditionals into
one declarative per-stage guard-type table.

---

## Requirements

Derived from the issue's acceptance criteria, re-verified against current `main`, and narrowed
by this pass's Q&A (full log below).

### R1 — `scripts/scheduler_lib.sh`: sourceable predicate library

Move exactly these 9 side-effect-free functions out of `scheduler.sh` (all currently in
L236–443, confirmed to have no `dispatch()`/`set_board_status()`/`gh` calls):
`has_refine_skip_label`, `has_opt_in_refine_label`, `has_direct_to_pr_label`, `get_size_label`,
`is_above_ceiling`, `has_above_ceiling_label`, `is_below_ceiling`,
`elapsed_minutes_since_marker`, `has_new_comment_after_report`.

Do **not** move `spec_advance_check`, `plan_advance_check`, `end_gate_check` — despite living in
the same source region, they call `dispatch()`, `set_board_status()`, `gh issue comment`, and
label mutation. They are micro-stages, not predicates; moving them into a sourceable lib would
smuggle dispatch machinery into a file whose whole purpose (mirroring `gate_lib.sh`) is to have
none. They stay in `scheduler.sh` as helpers called from `stage_review_triage`/`stage_plan`/
`stage_refine`.

Shape, following `scripts/gate_lib.sh`'s precedent exactly: no `set -euo pipefail` (sourced
files must not alter caller shell options), self-locates via
`SCHEDULER_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"`.

Do **not** move the two sentinel-read blocks (session-window-paused L968–989, main-is-red
L991–1004) into this lib. Both operate on filesystem sentinel state, not the item jq blob the
lib is scoped to, and both have side effects (self-healing `rm -f`; the main-is-red block
dispatches "Recheck main"/"Fix main"). Putting side-effecting, loop-shared-state mutators into a
pure-predicate-sourcing lib breaks the shape this ticket is asking the lib to have. Extracting
them to named `scheduler.sh`-local helper functions (not moved to the lib) is acceptable
optional polish; leaving them as inline top-of-loop code is equally acceptable. Do not treat
either choice as a conformance failure.

`scheduler.sh` sources the new lib **before** its `SCHEDULER_SOURCE_ONLY` early-return guard
(currently L787–789), in the same pre-guard region as the existing
`source .../scripts/identity.sh` (L13) — so every one of the 6 existing
`SCHEDULER_SOURCE_ONLY=1`-sourcing test files sees the moved functions with zero changes
required. Update the guard's stale comment ("the helper functions and constants above are now
defined") to reflect that some now live in the sourced lib.

### R2 — Ten named `stage_*` functions, 1:1 extraction, no consolidation

Extract each loop-inlined block into its own function, verbatim code moves (no logic changes):

| Function | Current block | Dispatches? | Current guard |
|---|---|---|---|
| `stage_ci_gate` | P0, L871–906 | No | none (runs every cycle) |
| `stage_rescue_blocked` | P0.6, L908–933 | No | `BLOCKED_RESCUE_ENABLED` only |
| `stage_orphan_sweep` | L946–966 (unenumerated 10th block) | No | none |
| `stage_conflict_resolve` | P1.5, L1006–1051 | Yes | `MAIN_IS_RED \|\| SESSION_WINDOW_PAUSED` (guard copy #1, L1011) |
| `stage_review_triage` | P1, L1053–1085 | Yes | **none** — not gated by `MAIN_IS_RED` or `SESSION_WINDOW_PAUSED` today |
| `stage_ready_implement` | P2, L1087–1135 | Yes | `MAIN_IS_RED \|\| SESSION_WINDOW_PAUSED` (guard copy #2, L1088) |
| `stage_blocked_retry` | P3, L1137–1180 | Yes | `MAIN_IS_RED \|\| SESSION_WINDOW_PAUSED` (guard copy #3, L1138) |
| `stage_plan` | P4, L1182–1226 | Yes | `SESSION_WINDOW_PAUSED` only — **not** gated by `MAIN_IS_RED` |
| `stage_refine` | P5, L1228–1275 | Yes | `SESSION_WINDOW_PAUSED` only — **not** gated by `MAIN_IS_RED` |
| `stage_epic_autopilot` | P6, L1277–1285 | Yes | runs only if nothing dispatched yet this cycle AND `MAIN_IS_RED=false` AND `SESSION_WINDOW_PAUSED=false` AND enabled — distinct one-line shape, not a block-wrapping guard |

`stage_orphan_sweep`'s promotion to a named function is new relative to the issue's own
enumeration (which only lists P0–P6) — the original 2026-07-07 spec caught this, and it still
holds: the block is stage-shaped (loops board items, calls `set_board_status`/`gh issue
comment`) and belongs alongside `stage_ci_gate`/`stage_rescue_blocked` as a non-dispatching,
always-run direct call.

The capacity guard (L935–944) is **not** a stage function. Its `continue` (L943) aborts the
entire poll cycle (sleep + restart the `while true`), not just one stage — modeling that in a
dispatch table would require inventing a sentinel halt-cycle return protocol for a single
caller. It stays a direct call between the non-dispatching stages and the dispatch table, exactly
where it runs today.

### R3 — One declarative guard mechanism, not a uniform guard

The acceptance criterion "exactly one `MAIN_IS_RED` guard" targets the **3 verbatim-identical
copies** of `if [ "$MAIN_IS_RED" = "true" ] || [ "$SESSION_WINDOW_PAUSED" = "true" ]` (guarding
`stage_conflict_resolve`, `stage_ready_implement`, `stage_blocked_retry`) — it does **not** mean
every stage receives the same guard. Current per-stage guard semantics are genuinely
heterogeneous (see R2 table) and must be preserved exactly:

- `stage_review_triage` (P1) has **no** guard today — it must keep running even when main is red
  or the session window is paused. Comment/merge triage for already-in-review work is
  independent of the red-main implementation freeze.
- `stage_plan` (P4) / `stage_refine` (P5) are gated by `SESSION_WINDOW_PAUSED` alone — plan
  generation and refinement don't touch the red main branch, so they are not `MAIN_IS_RED`-sensitive.
- `stage_epic_autopilot` (P6) has its own compound condition (starved + green + unpaused +
  enabled), structurally different from the other three.

Implementation approach: a per-stage guard-type table (e.g.
`declare -A STAGE_GUARD=([stage_conflict_resolve]=red_or_paused
[stage_review_triage]=none [stage_ready_implement]=red_or_paused
[stage_blocked_retry]=red_or_paused [stage_plan]=paused_only [stage_refine]=paused_only)`,
with `stage_epic_autopilot` handled by its own existing compound condition rather than folded
into the table) collapses the 3 duplicated conditionals into one evaluation site while leaving
every stage's actual skip/run behavior byte-identical to today. A conformance check for this
ticket should specifically verify P1 is still unguarded and P4/P5 are still
session-window-only — a uniform "all 7 stages check `MAIN_IS_RED`" implementation would be a
real behavior change and must be rejected.

This table covers 7 of the 10 stage functions (`stage_conflict_resolve`, `stage_review_triage`,
`stage_ready_implement`, `stage_blocked_retry`, `stage_plan`, `stage_refine`,
`stage_epic_autopilot`) — the dispatch-capable ones. The other 3 (`stage_ci_gate`,
`stage_rescue_blocked`, `stage_orphan_sweep`) plus the capacity guard run as unconditional direct
calls before the table, exactly as today.

The separate `MAIN_IS_RED`-triggered recheck/fixer dispatch at L996–1004
(`main_red_recheck_check`/`main_red_fixer_check`) is a conceptually distinct use of the
`MAIN_IS_RED` sentinel (self-healing trigger, not a stage skip-guard) and is out of scope for
the "exactly one guard" consolidation — it stays as-is.

### R4 — Test strategy

- All 6 existing `SCHEDULER_SOURCE_ONLY=1`-sourcing test files (`test_159_regression.sh`,
  `test_config_deletion.sh`, `test_dispatch_ceiling.sh`, `test_has_new_comment_after_report.sh`,
  `test_scheduler.sh`, `test_scheduler_pagination.sh`) stay pointed at `scheduler.sh`, unchanged
  in content — R1's pre-guard `source scheduler_lib.sh` makes this automatic.
- New required coverage: `tests/test_scheduler_lib.sh`, sourcing `scripts/scheduler_lib.sh`
  directly — the concrete payoff of R1, matching the `scripts/gate_lib.sh` /
  `tests/test_memory_write_gate.sh` precedent (plain `source`, exercise functions directly, no
  `gh`/`docker` stub scaffolding needed).
- New `stage_*`/dispatch-table coverage: extend `tests/test_scheduler.sh` with a new section
  (it already has lettered sections and full `gh`/`docker`/`python3` stub scaffolding) rather
  than creating a standalone `test_scheduler_stages.sh`. This matches an existing memory lesson
  (`.archon/memory/codebase-patterns.md`, issue #275): a standalone test file duplicates ~30-60
  lines of stub/source boilerplate and is only justified when the concern is too large to append.
  Only split out a separate file if the stage/table coverage turns out large enough to make
  `test_scheduler.sh` (already 1113 lines) unwieldy — a judgment call for planning/implementation,
  not fixed here.
- No new *behavioral* end-to-end tests. This is a pure internal-seam refactor targeting
  byte-identical external behavior (env vars, board transitions, log formats unchanged) — CLAUDE.md's
  "TDD for behavior changes" doesn't trigger because there is no behavior change. The existing 6
  sourcing test files, run unchanged, are the regression oracle that proves it.

### R5 — Explicitly out of scope: `factory_core` Python migration

The issue names "migrate stage decision logic into `factory_core` (pure core + injected IO, like
`epic_autopilot.py`)" as a "longer-term alternative worth considering during planning," then
immediately warns: "this is a pure-bash restructure ... land it as mechanical moves ... not a
rewrite." Every acceptance criterion is bash-specific and would be violated by a Python
migration (a sourceable bash lib, a `stage_*` bash dispatch table, tests passing "unchanged in
behaviour"). `stage_epic_autopilot` is already the fully-migrated precedent (a one-line shell to
`python3 $FACTORY_CORE_CLI epic-autopilot --once`) — the other 9 stages are not, and this ticket
does not change that. Record as considered-and-deferred; a follow-up ticket should scope the
Python migration itself.

### R6 — Safety-gate boundary (CLAUDE.md hard limit)

Every point where `scheduler.sh` touches circuit-breaker/board logic is already a thin one-line
adapter into `factory_core/breaker.py` / `factory_core/board.py` (`get_retry_count`,
`increment_retry`, `reset_retry`, `trip_to_blocked`, `check_failure_signature`,
`set_board_status`). This refactor relocates *where these call sites live* (inside `stage_*`
functions instead of inline in the loop) and never opens `factory_core/breaker.py`,
`factory_core/board.py`, or `factory_core/epic_autopilot.py`. No breaker/budget/gate logic is
touched, weakened, or restructured — satisfying the hard limit structurally, not by exception.

---

## Architecture / Approach

Land as four mechanical, separately-reviewable steps (unchanged shape from the original
proposal — still the right sequencing for a size:L operationally-critical file):

1. **Extract the lib.** Create `scripts/scheduler_lib.sh` with the 9 pure predicates (R1). Source
   it pre-guard in `scheduler.sh`. All 6 existing sourcing tests must pass with zero content
   changes; add `test_scheduler_lib.sh`.
2. **Extract stage functions.** Move each of the 10 blocks (R2) into a named `stage_*` function,
   each still called directly from the loop body in its current position/order — pure code-move,
   no table yet. Verify no behavior change via the existing suite after each extraction.
3. **Introduce the guard-type table.** Replace the 3 duplicated `MAIN_IS_RED`/
   `SESSION_WINDOW_PAUSED` conditionals with the `STAGE_GUARD`-driven dispatch loop over the 7
   dispatch-capable stages (R3), preserving each stage's distinct guard semantics exactly. The
   capacity guard, `stage_ci_gate`, `stage_rescue_blocked`, and `stage_orphan_sweep` remain direct
   calls before the table.
4. **Full regression pass.** All `test_scheduler*.sh`, `test_dispatch_ceiling.sh`,
   `test_config_deletion.sh`, `test_159_regression.sh`, `test_has_new_comment_after_report.sh`,
   `test_scheduler_pagination.sh`, `test_scheduler_autopilot_guard.sh`, `test_scheduler_ceiling.sh`,
   `test_scheduler_main_red_fixer.sh`, `test_epic_autopilot_config.sh`, `test_identity.sh`, plus
   `python -m pytest tests/ -v` and `smoke_gate.sh` (CI's exact gate per CLAUDE.md).

---

## Alternatives Considered

1. **Migrate stage decision logic into `factory_core` (Python, pure core + injected IO).**
   Rejected for this ticket — see R5. `stage_epic_autopilot` is proof the pattern works, but
   applying it to the other 9 stages is a rewrite of the most operationally critical file, is
   explicitly disclaimed by the issue, and would break the "tests pass unchanged in sourcing
   mechanics" AC by construction. Deferred to a follow-up ticket.
2. **Consolidate P0/P0.6 into a single `stage_gates` function** (both are non-dispatching,
   run-every-cycle gate/rescue blocks). Rejected — each owns a distinct cross-stage side-channel
   variable consumed later in the same cycle (`CI_BLOCKED` gates P1/P1.5 item skips; `RESCUED`
   gates P3 item skips), and merging them would obscure that data flow for no structural benefit.
   1:1 extraction is the most mechanical, lowest-risk move for a size:L file this critical.
3. **Apply the `MAIN_IS_RED`/`SESSION_WINDOW_PAUSED` guard uniformly to all 7 dispatch-capable
   stages** (simpler dispatch table, one boolean instead of a guard-type table). Rejected — P1
   has no guard today and P4/P5 are session-window-only; a uniform guard is a real behavior
   change (freezing comment triage and plan/refine dispatch under conditions they currently run
   through) and would violate the "byte-identical external behavior" requirement. See R3.

---

## Open Questions (non-blocking)

- Whether to extract the two sentinel-read blocks (session-window-paused, main-is-red) into named
  helper functions is left to planning/implementation discretion (R1) — either choice satisfies
  this spec.
- Whether stage/dispatch-table coverage needs its own `test_scheduler_stages.sh` file or can stay
  a section within `test_scheduler.sh` is left to implementation, gated on how large that
  coverage turns out to be (R4).

## Assumptions

- `docs/superpowers/specs/` contains no live spec for #185 in this checkout — the 2026-07-07
  spec lived only on the now-deleted original refine branch and is not present on `main` or this
  branch; this document is a fresh file, not an edit of a prior one.
- "Same board behaviour" / "byte-identical external behavior" is verified by the existing/new
  test suite plus code-review diffing each of the four extraction steps, not a new live-board
  integration test — none of the existing suite exercises a live board today, and this ticket
  does not introduce one.
- The `STAGE_GUARD` (or equivalent) associative array and `STAGE_ORDER`-equivalent iteration are
  declared as loop-external globals (`declare -A` before the `while true`), matching how
  `WIP_DATA`/`MAX_IN_PROGRESS` etc. are already declared today (L806–808).
