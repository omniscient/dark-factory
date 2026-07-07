# Internal seams for the scheduler poll loop — stage functions + sourceable predicate lib (#185)

**Issue:** #185 · **Status:** spec-pending-review

## Overview

`scheduler.sh` (1185 lines) drives the factory's backlog board via one 385-line
`while true` poll loop (`:800-1185`) with nine priority stages (P0, P0.6, P1.5, P1,
P2, P3, P4, P5, P6) inlined as sequential `while read item` blocks, plus a tenth,
unnumbered block — the orphaned-`In progress` sweep (`:898-918`) — that the issue's
own problem statement doesn't enumerate but is the same shape (board-mutating,
non-dispatching, loop-inlined). Three symptoms:

- The `MAIN_IS_RED` skip-guard is copy-pasted three times (`:936`, `:1006`, `:1056`)
  for the three dispatch stages it gates (P1.5, P2, P3).
- ~200 lines of business-logic predicates (`:237-448`) — `has_direct_to_pr_label`,
  `get_size_label`, `is_above_ceiling`, `is_below_ceiling`,
  `elapsed_minutes_since_marker`, `spec_advance_check`, `plan_advance_check`,
  `end_gate_check`, `has_new_comment_after_report`, and others — are pure
  jq-item-in/decision-out functions, but the only way to unit-test them today is
  `SCHEDULER_SOURCE_ONLY=1`-sourcing the entire 1185-line `scheduler.sh` with `gh`
  and `docker` stubbed. `tests/test_scheduler.sh` is 917 lines largely because of
  this indirection.
- Each priority stage's control flow (guard, loop, dispatch) is inlined in the loop
  body — there is no seam to unit-test a single stage's decision logic, or to vary
  one stage without touching the 385-line loop.

The fix is a pure internal restructure — same env vars, same board behaviour, same
external interface — done as mechanical moves: extract the ~200 predicate lines into
a new sourceable `scripts/scheduler_lib.sh` (mirroring the existing `scripts/gate_lib.sh`
precedent), turn each of the ten loop-inlined blocks into a `stage_*` function, and
replace the inlined priority bodies with a small ordered dispatch table that applies
the `MAIN_IS_RED` guard once, declaratively, per stage.

## Requirements

Distilled from the issue's acceptance criteria and the brainstorming Q&A below.

1. **Predicates move to `scripts/scheduler_lib.sh`, sourceable standalone** (issue
   AC #1; Q&A #3). In scope for the move — every function in the current `:237-448`
   range that is a decision function with **no `dispatch`/`set_board_status`/state
   side effect**, including the ones that do a read-only `gh` lookup (defining a
   function that calls `gh` has no effect until the function is actually invoked,
   so sourcing the lib never touches the network regardless):
   - `has_refine_skip_label`, `has_opt_in_refine_label`, `has_direct_to_pr_label`
   - `get_size_label`, `is_above_ceiling`, `has_above_ceiling_label`, `is_below_ceiling`
   - `has_skip_label`, `get_issue_number`
   - `factory_at_capacity`
   - `has_new_comment_after_report` (read-only `gh issue view --json comments`
     lookup + regex classification, no dispatch/board-mutation side effect —
     confirmed in scope for the move by Q&A #3)

   `elapsed_minutes_since_marker` stays in `scheduler.sh`: it is a read-only `gh`
   helper like `has_new_comment_after_report`, but its only callers,
   `spec_advance_check` and `plan_advance_check`, both stay in `scheduler.sh`
   (Requirement 3) because they call `dispatch`/`set_board_status` directly — moving
   just the helper away from its two call sites adds an indirection with no
   testability payoff. `end_gate_check` also stays, for the same
   `dispatch`/`set_board_status`-calling reason. This mirrors `gate_lib.sh`'s own
   scope note ("do not add gate-specific logic here — only the shared primitives")
   — `scheduler_lib.sh` holds decision functions with no orchestration side effect,
   not the `dispatch`-calling advance/gate functions that already have their own
   test coverage via `SCHEDULER_SOURCE_ONLY=1`.

   `scheduler_lib.sh` follows the `gate_lib.sh` shape exactly: no `set -euo
   pipefail` (it is sourced, must not alter the caller's shell options), self-locates
   via `SCHEDULER_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"`.

2. **Ten `stage_*` functions, one per existing loop-inlined block, extracted 1:1**
   (issue AC #2; Q&A #1). The issue's own illustrative names
   (`stage_main_red`, `stage_autopilot`, `stage_ceiling_revisit`, `stage_advance`,
   `stage_dispatch`) are not a target taxonomy — `stage_ceiling_revisit` in
   particular names an unrelated periodic Archon command
   (`scripts/ceiling_revisit.py`, driven by `commands/ceiling-revisit.md`) that
   never runs inside this loop. The binding requirement is the acceptance criteria,
   not the parenthetical name list. 1:1 extraction is the most mechanical,
   lowest-risk mapping and keeps each step a pure code-move:

   | Stage function | Current block | Dispatches? | MAIN_IS_RED-gated? |
   |---|---|---|---|
   | `stage_ci_gate` | P0, `:823-858` | no | no (runs every cycle) |
   | `stage_rescue_blocked` | P0.6, `:860-885` | no | no (runs every cycle) |
   | `stage_orphan_sweep` | unnumbered, `:898-918` | no | no (runs every cycle) |
   | `stage_conflict_resolve` | P1.5, `:931-969` | yes | yes |
   | `stage_review_triage` | P1, `:971-1003` | yes | no |
   | `stage_ready_implement` | P2, `:1005-1053` | yes | yes |
   | `stage_blocked_retry` | P3, `:1055-1091` | yes | yes |
   | `stage_plan` | P4, `:1093-1126` | yes | no |
   | `stage_refine` | P5, `:1128-1164` | yes | no |
   | `stage_epic_autopilot` | P6, `:1166-1174` | yes | yes (only runs when red is *false*; see Requirement 4) |

   `stage_*` functions are defined in `scheduler.sh` (after the
   `SCHEDULER_SOURCE_ONLY` return point — see Requirement 3), **not** moved into
   `scheduler_lib.sh`: every one of them calls `dispatch`, `gh`, `set_board_status`,
   or reads loop-scoped globals (`$DISPATCHED`, `$BOARD_ITEMS`, `$CI_BLOCKED`,
   `$RESCUED`, `$REFINE_RUNNING`). Only the pure predicates they call internally
   come from the lib.

3. **`scheduler.sh`'s `SCHEDULER_SOURCE_ONLY` guard sources `scheduler_lib.sh`
   before returning**, so `SCHEDULER_SOURCE_ONLY=1 source scheduler.sh` still yields
   every predicate (now defined in the lib) plus the `gh`/`docker`-calling helpers
   that stay behind in `scheduler.sh` (`dispatch`, `is_issue_running`,
   `fetch_board_items`, `trip_to_blocked`, `spec_advance_check`, etc.) — unchanged
   from today's sourcing contract (Q&A #3). Concretely, `:739-741` becomes:
   ```bash
   source "${SCHEDULER_LIB_DIR:-$(dirname "${BASH_SOURCE[0]:-$0}")/scripts}/scheduler_lib.sh"
   if [ "${SCHEDULER_SOURCE_ONLY:-0}" = "1" ]; then
     return 0
   fi
   ```
   The six existing tests that do `SCHEDULER_SOURCE_ONLY=1 source "$SCHED"` where
   `$SCHED` resolves to `scheduler.sh` (`test_scheduler.sh`,
   `test_dispatch_ceiling.sh`, `test_config_deletion.sh`, `test_159_regression.sh`,
   `test_has_new_comment_after_report.sh`, `test_scheduler_pagination.sh`) are
   **not repointed at `scheduler_lib.sh`** — each exercises at least one function
   that stays in `scheduler.sh` (e.g. `test_scheduler.sh` uses `dispatch`,
   `is_issue_running`, `fetch_board_items`, `trip_to_blocked`;
   `test_scheduler_pagination.sh` uses `fetch_board_items`; `test_159_regression.sh`
   uses `dispatch`/`trip_to_blocked`), so sourcing only the lib would leave them
   with undefined functions. In most cases these six files need **zero** changes;
   any touch is limited to the transitive behaviour of the existing source line,
   satisfying issue AC #4's "updated only for sourcing mechanics" (Q&A #3).

   The five grep-based static-assertion tests that reference `scheduler.sh`'s text
   without sourcing it (`test_scheduler_autopilot_guard.sh`,
   `test_scheduler_ceiling.sh`, `test_scheduler_main_red_fixer.sh`,
   `test_epic_autopilot_config.sh`, `test_identity.sh`) are unaffected: the strings
   they grep for (e.g. `EPIC_AUTOPILOT_ENABLED`, the epic-autopilot CLI call) still
   appear in `scheduler.sh`, just inside a function body instead of inline in the
   loop.

4. **New test coverage lives in two new files, not retrofitted into the six**
   (Q&A #3):
   - `tests/test_scheduler_lib.sh` — sources `scripts/scheduler_lib.sh` directly
     (bypassing `scheduler.sh` entirely), covering every predicate now in the lib.
     This is the concrete payoff the issue names ("tests source ~200 lines, not
     1184") and directly satisfies issue AC #1's "direct shell tests."
   - `tests/test_scheduler_stages.sh` — sources `scheduler.sh` with
     `SCHEDULER_SOURCE_ONLY=1` (stage functions and the dispatch table are defined
     after the guard, so this file needs the full source, gh/docker-stubbed same as
     `test_scheduler.sh`) and covers the dispatch-table behaviour: stage ordering,
     the single `MAIN_IS_RED` guard applying correctly per stage, and the
     `DISPATCHED`-break short-circuit. New coverage, not a port of existing
     assertions — the existing six files keep testing what they test today.

5. **Exactly one `MAIN_IS_RED` guard, expressed as a declarative per-stage marker**
   (issue AC #3). Two parallel data structures drive the loop:
   ```bash
   STAGE_ORDER=(stage_conflict_resolve stage_review_triage stage_ready_implement \
                stage_blocked_retry stage_plan stage_refine stage_epic_autopilot)
   declare -A STAGE_RED_SENSITIVE=(
     [stage_conflict_resolve]=true  [stage_review_triage]=false
     [stage_ready_implement]=true   [stage_blocked_retry]=true
     [stage_plan]=false             [stage_refine]=false
     [stage_epic_autopilot]=true
   )
   for stage in "${STAGE_ORDER[@]}"; do
     [ -n "$DISPATCHED" ] && break
     if [ "${STAGE_RED_SENSITIVE[$stage]}" = "true" ] && [ "$MAIN_IS_RED" = "true" ]; then
       echo "[$(date -u +%FT%TZ)] main_red_gate=skip_${stage}"
       continue
     fi
     "$stage"
   done
   ```
   `stage_ci_gate`, `stage_rescue_blocked`, and `stage_orphan_sweep` are **not** in
   `STAGE_ORDER` — see Requirement 6 for why they stay outside the dispatch-table
   loop entirely, not merely marked non-red-sensitive. Bash associative arrays
   require bash ≥4; the factory image is `ubuntu:26.04` (confirmed in `Dockerfile`
   line 1), which ships bash 5.x, so no compatibility concern.

6. **The capacity guard, and the three blocks that run before it
   (`stage_ci_gate`, `stage_rescue_blocked`) and the one immediately after it
   (`stage_orphan_sweep`), stay outside the `STAGE_ORDER` dispatch-table loop**,
   called directly and unconditionally in their current relative order. Rationale:
   the capacity guard (`:891-896`) does `continue` on the **outer** `while true`
   loop (restart the whole cycle, `sleep "$POLL_INTERVAL"`) — a control-flow jump
   the dispatch table's `for`/`break` cannot express without inventing a
   sentinel-return-code protocol solely to cover one call site. The capacity guard
   is also not "priority logic" in the sense the issue's AC #2 means (it is a
   concurrency/WIP guard, the same category as `check_rate_limit` and
   `fetch_board_items`, both of which also stay inline) — AC #2 requires "no
   *priority* logic inline in the loop," and none of these four blocks is one of
   the nine numbered priorities. `stage_ci_gate`/`stage_rescue_blocked`/
   `stage_orphan_sweep` are still extracted into named functions (satisfying
   Requirement 2's 1:1 mapping and improving their own testability/locality), just
   invoked as three plain function calls rather than through the data-driven table:
   ```bash
   stage_ci_gate
   stage_rescue_blocked

   FACTORY_RUNNING=$(count_factory_running)
   if factory_at_capacity "$FACTORY_RUNNING"; then
     echo "[$(date -u +%FT%TZ)] skip=factory_at_capacity running=${FACTORY_RUNNING}/${FACTORY_WIP_LIMIT}"
     sleep "$POLL_INTERVAL"
     continue
   fi

   stage_orphan_sweep

   MAIN_IS_RED=false
   [ -f "${SCHEDULER_STATE_DIR}/main-is-red" ] && MAIN_IS_RED=true
   if [ "$MAIN_IS_RED" = "true" ]; then
     echo "[$(date -u +%FT%TZ)] main_red_gate=active action=skip_implement_dispatch"
     main_red_recheck_check
     main_red_fixer_check
   fi

   for stage in "${STAGE_ORDER[@]}"; do
     ...
   done
   ```
   `main_red_recheck_check`/`main_red_fixer_check` (the self-clear/autofix hooks,
   `:201-236`) are left as-is, called directly from the `MAIN_IS_RED=true` branch —
   they are not one of the nine priorities either, and already sit behind a single
   non-duplicated guard today, so they have none of the friction Requirement 5
   fixes. Renaming them to `stage_*` and folding them into the table is explicitly
   left to the implementer as a non-blocking follow-up (see Open Questions) rather
   than specified here, to keep this step mechanical.

7. **`stage_epic_autopilot`'s starved-only condition (`[ -z "$DISPATCHED" ]`,
   currently checked at `:1170`) stays inside the function body**, not hoisted into
   the dispatch table. The table's per-stage `MAIN_IS_RED` marker and the
   `DISPATCHED`-break both already gate it correctly (P6 only runs if the loop
   reaches it with `DISPATCHED` still empty, which is exactly "starved"), so the
   function's own internal check becomes redundant defense-in-depth, not new logic.
   Leave it in place rather than deleting it — removing a currently-passing
   redundant guard is exactly the kind of non-mechanical change the issue warns
   against ("land it as mechanical moves ... not a rewrite").

8. **External interface is byte-identical**: same env vars
   (`POLL_INTERVAL`, `MAX_RETRIES`, `FACTORY_WIP_LIMIT`, ...), same board-status
   transitions, same log line formats (`[$(date -u +%FT%TZ)] ...`), same cycle
   summary line at the end of the loop. No behavior change is observable from
   outside the process — this is the acceptance bar for issue AC #4 ("all existing
   `test_scheduler*.sh` tests pass unchanged in behaviour").

9. **Out of scope: migrating stage decision logic into `factory_core` (Python)**
   (Q&A #2). The issue names this as a "longer-term alternative worth considering
   during planning," immediately followed by "⚠️ this is a pure-bash restructure ...
   land it as mechanical moves ... not a rewrite." Every acceptance criterion is
   bash-specific (`scripts/scheduler_lib.sh` sourceable, a `stage_*` dispatch
   table, one `MAIN_IS_RED` guard, existing `test_scheduler*.sh` tests "updated only
   for sourcing mechanics") and would be unsatisfiable under a Python migration,
   which would replace those tests wholesale rather than adjust their sourcing.
   This spec delivers only the bash-internal restructure; the Python migration is
   left as a follow-up ticket (see Alternatives Considered #1).

## Architecture / Approach

### File layout

```
scripts/scheduler_lib.sh   (new — ~200 lines, pure predicates, Requirement 1)
scheduler.sh                (predicates removed, replaced by `source .../scheduler_lib.sh`;
                              ten stage_* functions added; loop body replaced by
                              three direct calls + capacity guard + seven-entry
                              dispatch table, Requirements 2, 5, 6)
tests/test_scheduler_lib.sh    (new, Requirement 4)
tests/test_scheduler_stages.sh (new, Requirement 4)
```

### Sequencing (land as reviewable, mechanical steps — issue's explicit ask)

1. **Extract the lib.** Create `scripts/scheduler_lib.sh` with the ten predicates
   from Requirement 1, each moved verbatim (no logic changes). Add
   `source ".../scripts/scheduler_lib.sh"` to `scheduler.sh` immediately before the
   `SCHEDULER_SOURCE_ONLY` check, delete the now-duplicate definitions from
   `scheduler.sh`. Add `tests/test_scheduler_lib.sh`. Run the existing six
   `SCHEDULER_SOURCE_ONLY=1`-sourcing tests to confirm zero behavior change
   (Requirement 3).
2. **Extract the ten stage functions**, one commit/step at a time in loop order,
   each a pure code-move of the existing block body into a named function with no
   control-flow change (same `while IFS= read -r item; do ... done < <(echo "$X" |
   jq -c '.[]')` body, same early-`continue`/`break` semantics preserved inside the
   function). After each extraction, replace the call site in the loop with a
   direct function call (no dispatch table yet) and confirm `test_scheduler.sh`
   still passes — this isolates "did the move change behavior" from "does the
   dispatch table work" as two separately reviewable/bisectable changes.
3. **Introduce the dispatch table.** Replace the seven direct calls to the
   `MAIN_IS_RED`-relevant stages (`stage_conflict_resolve`, `stage_review_triage`,
   `stage_ready_implement`, `stage_blocked_retry`, `stage_plan`, `stage_refine`,
   `stage_epic_autopilot`) with the `STAGE_ORDER`/`STAGE_RED_SENSITIVE` loop from
   Requirement 5. `stage_ci_gate`, `stage_rescue_blocked`, and `stage_orphan_sweep`
   remain direct calls per Requirement 6 and are unaffected by this step. Add
   `tests/test_scheduler_stages.sh`.
4. **Full regression pass**: run every `test_scheduler*.sh` file plus
   `test_dispatch_ceiling.sh`, `test_config_deletion.sh`, `test_159_regression.sh`,
   `test_has_new_comment_after_report.sh`, `test_scheduler_pagination.sh`,
   `test_scheduler_autopilot_guard.sh`, `test_scheduler_ceiling.sh`,
   `test_scheduler_main_red_fixer.sh`, `test_epic_autopilot_config.sh`,
   `test_identity.sh` to confirm the full existing suite is green.

## Alternatives considered

1. **Migrate stage decision logic into `factory_core` (pure Python core + injected
   IO, the `epic_autopilot.py` shape) instead of, or in addition to, the bash
   restructure.** Rejected for this issue (Requirement 9, Q&A #2) — every
   acceptance criterion is bash-specific and the issue's own closing warning
   ("pure-bash restructure ... not a rewrite") is unambiguous. This spec records
   the decision to defer, not evaluate, the Python path: file a separate follow-up
   issue for it once the bash seams land and the real boundary between "pure
   decision logic" and "IO" in each stage is visible from working code, rather than
   guessed at up front.

2. **Fold `stage_ci_gate`/`stage_rescue_blocked`/`stage_orphan_sweep` into the
   `STAGE_ORDER` table with a `DISPATCHES=false` marker (in addition to
   `RED_SENSITIVE`), rather than leaving them as direct calls outside the table.**
   Considered because it's more uniform (every extracted block goes through the
   same mechanism) and was the shape floated in early Q&A. Rejected because the
   capacity guard's `continue`-the-outer-loop control flow sits physically between
   `stage_rescue_blocked` and `stage_orphan_sweep`, and none of the three needs the
   `MAIN_IS_RED` marker (they aren't red-sensitive) or the `DISPATCHED`-break (they
   never dispatch) — a `DISPATCHES` marker would exist solely to be `false` for
   exactly these three, adding a second data structure to avoid three function
   calls. Three direct calls in fixed order is more mechanical and lower-risk than
   inventing a second table dimension.

3. **Give the dispatch-table loop a sentinel return code so a stage can request
   "abort this cycle" (covering the capacity guard uniformly with everything
   else).** Rejected — no stage other than the capacity guard ever needs this, and
   the capacity guard itself isn't a priority stage (Requirement 6). Adding a
   return-code protocol to the table for a single, non-priority caller is scope
   creep against "mechanical moves ... not a rewrite."

## Open questions (non-blocking)

- Whether `main_red_recheck_check`/`main_red_fixer_check` should eventually become
  `stage_main_red`-style functions folded into a data-driven "runs only when main
  is red" table (the mirror image of `STAGE_RED_SENSITIVE`), for full symmetry with
  the rest of the loop. Left to the implementer / a future ticket — they already
  sit behind one guard today (no copy-paste friction), so folding them in is a
  readability nicety, not something any acceptance criterion requires.
- Whether to file the `factory_core` migration follow-up ticket now or after this
  issue lands. Recommend after — the real pure-core/IO boundary per stage will be
  much clearer once the bash-side stage functions exist as concrete extraction
  points.

## Assumptions (flagged)

- **[ASSUMPTION]** `docs/superpowers/specs/` does not exist in the working tree at
  the start of this refinement — the directory and prior sibling spec files
  (`2026-07-07-single-source-safety-defaults-design.md` and others from the same
  2026-07-06 architecture-deepening review batch) were already merged and removed
  from `main` by the time this branch was cut. This spec is written as a new file,
  following the same header/section format as those prior specs (confirmed via
  `git show` on their last-known commits).
- **[ASSUMPTION]** "Same board behaviour" (issue Solution section) means the
  refactor is verified by the existing test suite passing and by code-review
  diffing each extraction step against the pre-refactor block, not by a new
  end-to-end / integration test against a live GitHub board — none of the existing
  `test_scheduler*.sh` tests exercise a live board today (all stub `gh`/`docker`),
  and adding one is out of proportion to an internal-seams refactor.
- **[ASSUMPTION]** The `STAGE_RED_SENSITIVE` associative array and `STAGE_ORDER`
  indexed array are declared with function-local-safe global scope (top-level
  `declare -A` before the loop, not `local` inside a function) since the dispatch
  table is driven directly from the outer `while true` loop body, matching how
  `WIP_DATA`/`MAX_IN_PROGRESS`/etc. are already declared as loop-external globals
  today.
