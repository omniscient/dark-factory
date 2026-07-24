# Plan — Scheduler Internal Seams: Stage Functions + Sourceable Predicate Lib

**Issue:** omniscient/dark-factory#185
**Spec:** [docs/superpowers/specs/2026-07-23-scheduler-internal-seams-design.md](../specs/2026-07-23-scheduler-internal-seams-design.md)
**Branch:** `refine/issue-185-refactor-scheduler---internal-seams-for-` (spec/plan); implementation lands on `feat/issue-185-*`

## Goal

Give `scheduler.sh`'s 385→~450-line `while true` poll loop internal seams without changing
any external behavior (env vars, board transitions, log formats byte-identical):

1. Move 9 side-effect-free item predicates into a new sourceable `scripts/scheduler_lib.sh`.
2. Extract the ten loop-inlined priority blocks into named `stage_*` functions, 1:1, no
   consolidation.
3. Collapse the three verbatim-duplicated `MAIN_IS_RED || SESSION_WINDOW_PAUSED` guard
   conditionals into one declarative per-stage guard-type table — preserving the
   genuinely heterogeneous guard semantics (P1 unguarded; P4/P5 paused-only; P6 its own
   compound condition) exactly.

## Architecture

- `scripts/scheduler_lib.sh` mirrors `scripts/gate_lib.sh`'s shape: no `set -euo pipefail`
  (sourced files must not alter caller shell options), self-locates via `BASH_SOURCE`.
  `scheduler.sh` sources it immediately after `scripts/identity.sh` (current L13), i.e.
  before the `SCHEDULER_SOURCE_ONLY` early-return guard — so all 6 existing
  `SCHEDULER_SOURCE_ONLY=1`-sourcing tests see the moved functions with zero content changes.
- The 9 predicates are **not** contiguous in `scheduler.sh` today: 8 of them sit at
  L236–309, but `spec_advance_check`/`plan_advance_check`/`end_gate_check` (L311–418, which
  stay in `scheduler.sh` — they call `dispatch()`/`set_board_status()`/`gh`) split them from
  the 9th, `has_new_comment_after_report` (L420–443). Both regions move; the middle region
  does not.
- Ten `stage_*` functions are defined in a new `# --- Stage functions (poll loop) ---`
  section inserted **before the `SCHEDULER_SOURCE_ONLY` early-return guard** (current
  L784–789) — the same pre-guard region R1 uses for `scheduler_lib.sh`, immediately after
  the last remaining helper function and before the guard's explanatory comment. This is
  required, not cosmetic: Task 7's new Section R tests call `stage_review_triage`,
  `stage_plan`, `stage_conflict_resolve`, `stage_ready_implement`, and
  `stage_blocked_retry` directly against `SCHEDULER_SOURCE_ONLY=1 source scheduler.sh`
  (`tests/test_scheduler.sh:65`), which returns at the guard — any function defined below
  it is invisible to every sourcing test. Bash functions read the caller's globals at call
  time, not definition time, so defining them earlier is safe even though they reference
  per-cycle globals (`$BOARD_ITEMS`, `$IN_REVIEW`, `$DISPATCHED`, `$MAIN_IS_RED`, ...) that
  are only assigned inside the loop body — the loop's inline call sites still invoke them
  in the same cycle-order position as today.
- The capacity guard (current L935–944) is **not** extracted — its `continue` aborts the
  whole poll cycle, not one stage; a function boundary doesn't change that in bash (break/
  continue cross function calls back into the caller's loop), but the spec is explicit that
  this guard stays fully inline, unchanged, not part of the `stage_*` set. Same for the two
  sentinel-read blocks (session-window-paused, main-is-red): the spec makes extracting them
  optional polish and this plan leaves them inline to keep the diff mechanical.
- Step 2 (function extraction) is a pure code move: the 5 currently-guarded stages
  (`stage_conflict_resolve`, `stage_ready_implement`, `stage_blocked_retry`, `stage_plan`,
  `stage_refine`) keep their `if`/`elif`/`else` guard **inside** the function body,
  verbatim, called unconditionally. Step 3 then pulls the guard **out** into
  `STAGE_GUARD`/`STAGE_SKIP_ACTION`/`STAGE_ORDER` plus a new `dispatch_stage()` helper —
  the single declarative evaluation site R3 asks for — covering 6 of the 7 dispatch-capable
  stages (`stage_review_triage` has `STAGE_GUARD=none` and runs through the same helper
  unconditionally, matching its already-guardless behavior); `stage_epic_autopilot` keeps
  its own compound one-line condition as a direct call after the loop, per the spec (its
  shape doesn't fit the table without inventing a case for it).
- `STAGE_GUARD`/`STAGE_SKIP_ACTION`/`STAGE_ORDER` and `dispatch_stage()` are declared in the
  same **pre-`SCHEDULER_SOURCE_ONLY`-guard** section as the ten `stage_*` functions — not
  alongside `WIP_DATA`/`MAX_IN_PROGRESS` (current L806–808), which sit *after* the guard and
  so are invisible to every sourcing test. This deviates from the spec's Assumptions section
  (which suggested the `WIP_DATA`-style post-guard placement) but not from any R1–R6
  requirement: the array is still a loop-external global, still exactly one evaluation site.
  The reason is testability — without a named, pre-guard-defined `dispatch_stage()` function
  wrapping the guard check, the guard logic would live only inside the inline
  `for _stage in "${STAGE_ORDER[@]}"; do ... done` loop in the `while true` body, which
  `SCHEDULER_SOURCE_ONLY=1` sourcing tests can never reach (same reasoning as the `stage_*`
  placement above) — Task 7's Section R would have nothing to call.

## Tech Stack

Pure Bash (scheduler.sh, scripts/*.sh), jq, existing `tests/test_scheduler*.sh` stub-based
harness (gh/docker/python3/set_board_status stubbed via exported bash functions).

## File Structure

| File | Change |
|---|---|
| `scripts/scheduler_lib.sh` | New — 9 predicates, sourced pre-guard by `scheduler.sh` |
| `scheduler.sh` | Modified — predicates removed, source line added, 10 `stage_*` functions added, loop body replaced with direct calls + guard-table dispatch loop |
| `tests/test_scheduler_lib.sh` | New — sources the lib directly, exercises the 9 predicates |
| `tests/test_scheduler.sh` | Modified — new `Section R` covering stage guard semantics (P1 unguarded, P4/P5 paused-only, P1.5/P2/P3 red-or-paused) |

---

## Task 0 — Baseline regression run

Confirm the full suite is green before any change, so later "verify pass" steps have a
trustworthy baseline.

```bash
cd /workspace/dark-factory
for t in test_159_regression test_config_deletion test_dispatch_ceiling \
         test_has_new_comment_after_report test_scheduler test_scheduler_pagination \
         test_scheduler_autopilot_guard test_scheduler_ceiling test_scheduler_main_red_fixer \
         test_epic_autopilot_config test_identity; do
  echo "=== $t ==="; bash "tests/${t}.sh" || echo "FAILED: $t"
done
python -m pytest tests/ -v
bash smoke_gate.sh
```

Expected: every listed script prints `Results: N passed, 0 failed` (or equivalent all-pass
summary) and exits 0; pytest and smoke_gate.sh both exit 0. Do not proceed to Task 1 if any
of these are already red — that is pre-existing breakage out of scope for #185.

---

## Step 1: Extract the predicate lib (R1)

### Task 1 — Write failing `tests/test_scheduler_lib.sh`

**Files:** `tests/test_scheduler_lib.sh` (new)

Write the test first — it sources `scripts/scheduler_lib.sh`, which doesn't exist yet, so
sourcing must fail.

```bash
#!/usr/bin/env bash
# Unit tests for scripts/scheduler_lib.sh — sourced directly, no scheduler.sh/gh/docker
# stub scaffolding needed (mirrors scripts/gate_lib.sh / tests/test_memory_write_gate.sh).
# Run: bash tests/test_scheduler_lib.sh
set -uo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
source "${REPO_ROOT}/scripts/scheduler_lib.sh"

PASSED=0; FAILED=0
assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else
    echo "  FAIL: $desc — expected='$expected' got='$actual'" >&2; FAILED=$((FAILED+1))
  fi
}

# ---- has_refine_skip_label ----
echo "--- has_refine_skip_label ---"
REFINE_SKIP_LABELS="needs-discussion,epic,spec-pending-review,plan-pending-review"
assert_eq "needs-discussion label skips" \
  "0" "$(has_refine_skip_label '{"labels":["needs-discussion"]}'; echo $?)"
assert_eq "no skip label does not skip" \
  "1" "$(has_refine_skip_label '{"labels":["ready-for-agent"]}'; echo $?)"

# ---- has_opt_in_refine_label ----
echo "--- has_opt_in_refine_label ---"
assert_eq "ready-for-agent present" \
  "0" "$(has_opt_in_refine_label '{"labels":["ready-for-agent"]}'; echo $?)"
assert_eq "ready-for-agent absent" \
  "1" "$(has_opt_in_refine_label '{"labels":["bug"]}'; echo $?)"

# ---- has_direct_to_pr_label ----
echo "--- has_direct_to_pr_label ---"
DIRECT_TO_PR_LABEL=direct-to-pr
assert_eq "direct-to-pr present" \
  "0" "$(has_direct_to_pr_label '{"labels":["direct-to-pr"]}'; echo $?)"
assert_eq "direct-to-pr absent" \
  "1" "$(has_direct_to_pr_label '{"labels":["bug"]}'; echo $?)"

# ---- get_size_label / is_above_ceiling / has_above_ceiling_label / is_below_ceiling ----
echo "--- ceiling classification ---"
ABOVE_CEILING_KEYWORDS="migration|migrate|performance|perf|architectur|refactor"
ABOVE_CEILING_LABEL=above-ceiling
assert_eq "get_size_label XL" "XL" "$(get_size_label '{"labels":["size: XL"]}')"
assert_eq "get_size_label M" "M" "$(get_size_label '{"labels":["size: M"]}')"
assert_eq "is_above_ceiling: XL always" \
  "0" "$(is_above_ceiling '{"labels":["size: XL"],"content":{"title":"anything"}}'; echo $?)"
assert_eq "is_above_ceiling: M + refactor keyword" \
  "0" "$(is_above_ceiling '{"labels":["size: M"],"content":{"title":"refactor(x): y"}}'; echo $?)"
assert_eq "is_above_ceiling: M without keyword stays below" \
  "1" "$(is_above_ceiling '{"labels":["size: M"],"content":{"title":"add x"}}'; echo $?)"
assert_eq "has_above_ceiling_label present" \
  "0" "$(has_above_ceiling_label '{"labels":["above-ceiling"]}'; echo $?)"
assert_eq "is_below_ceiling: S" \
  "0" "$(is_below_ceiling '{"labels":["size: S"]}'; echo $?)"
assert_eq "is_below_ceiling: unlabelled treated as S" \
  "0" "$(is_below_ceiling '{"labels":[]}'; echo $?)"
assert_eq "is_below_ceiling: M is not below" \
  "1" "$(is_below_ceiling '{"labels":["size: M"]}'; echo $?)"

# ---- elapsed_minutes_since_marker (requires FACTORY_PROVIDERS_CLI; stub python3) ----
echo "--- elapsed_minutes_since_marker ---"
FACTORY_PROVIDERS_CLI=/dev/null
python3() { echo '[]'; }
export -f python3
assert_eq "no matching comment returns empty" \
  "" "$(elapsed_minutes_since_marker 1 'no-match')"

# ---- has_new_comment_after_report (requires FACTORY_PROVIDERS_CLI; stub python3) ----
echo "--- has_new_comment_after_report ---"
FACTORY_PRODUCT_NAME="Dark Factory"
python3() { echo '[{"body":"Posted by Dark Factory Refinement Pipeline","createdAt":"2026-01-01T00:00:00Z"},{"body":"human feedback","createdAt":"2026-01-02T00:00:00Z"}]'; }
export -f python3
assert_eq "human comment after report marker is yes" \
  "yes" "$(has_new_comment_after_report 1 'Posted by Dark Factory Refinement Pipeline')"

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
```

**Verify fail:**

```bash
bash tests/test_scheduler_lib.sh
```

Expected: the `source` line reports `.../scripts/scheduler_lib.sh: No such file or
directory`; with no `set -e`, the script does not abort there, but every predicate call
after it hits "command not found" and every `assert_eq` fails, so the final `Results:`
line and `[ "$FAILED" -eq 0 ]` both report non-zero — exit 1 either way.

### Task 2 — Create `scripts/scheduler_lib.sh`, remove the 9 functions from `scheduler.sh`, source it pre-guard

**Files:** `scripts/scheduler_lib.sh` (new), `scheduler.sh` (modified)

Create `scripts/scheduler_lib.sh`:

```bash
#!/usr/bin/env bash
# Sourceable predicate library for scheduler.sh — pure, side-effect-free item-blob
# predicates only (mirrors scripts/gate_lib.sh's shape). Do NOT add dispatch()/
# set_board_status()/gh-mutating logic here; spec_advance_check/plan_advance_check/
# end_gate_check stay in scheduler.sh because they dispatch and mutate board state.
# Do NOT add set -euo pipefail: this file is sourced and must not alter caller shell options.

SCHEDULER_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

has_refine_skip_label() {
  local item="$1"
  local labels
  labels=$(echo "$item" | jq -r '.labels[]?' 2>/dev/null)
  IFS=',' read -ra SKIP_ARRAY <<< "$REFINE_SKIP_LABELS"
  for skip in "${SKIP_ARRAY[@]}"; do
    if echo "$labels" | grep -qi "$skip"; then
      return 0
    fi
  done
  return 1
}

has_opt_in_refine_label() {
  local item="$1"
  local labels
  labels=$(echo "$item" | jq -r '.labels[]?' 2>/dev/null)
  echo "$labels" | grep -qi "ready-for-agent"
}

has_direct_to_pr_label() {
  local item="$1"
  echo "$item" | jq -r '.labels[]?' 2>/dev/null | grep -qi "$DIRECT_TO_PR_LABEL"
}

# --- Dispatch ceiling classification (#339) ---
# Returns "S", "M", "L", or "" from the item's labels
get_size_label() {
  echo "$1" | jq -r '.labels[]?' 2>/dev/null | grep -oiE 'size: ?(xl|[sml])' | awk '{print toupper($NF)}' | head -1
}

# True (returns 0) if item is above the dispatch ceiling: size XL always, or size M
# when the title matches an ABOVE_CEILING_KEYWORDS pattern (escalation only — the
# keyword heuristic never demotes).
is_above_ceiling() {
  local item="$1" title size
  title=$(echo "$item" | jq -r '.content.title // ""' 2>/dev/null)
  size=$(get_size_label "$item")
  case "$size" in
    XL) return 0 ;;
    M) echo "$title" | grep -qiE "${ABOVE_CEILING_KEYWORDS}" && return 0 || return 1 ;;
    *) return 1 ;;
  esac
}

# True if item already carries the above-ceiling label (board-fetch snapshot)
has_above_ceiling_label() {
  echo "$1" | jq -r '.labels[]?' 2>/dev/null | grep -qi "^${ABOVE_CEILING_LABEL}$"
}

# True if item is S- or L-size, or has no size label (unlabelled is treated as S per spec)
is_below_ceiling() {
  local size
  size=$(get_size_label "$1")
  case "$size" in S|L|"") return 0 ;; *) return 1 ;; esac
}

# Returns minutes elapsed since the last comment matching $marker_re on the given issue.
# Returns "" if no matching comment exists or if the timestamp cannot be parsed.
elapsed_minutes_since_marker() {
  local issue_num="$1"
  local marker_re="$2"
  local comments
  comments=$(python3 "$FACTORY_PROVIDERS_CLI" tracker get-comments --id "$issue_num" 2>/dev/null) \
    || { echo ""; return; }
  local created_at
  created_at=$(echo "$comments" | jq -r --arg m "$marker_re" \
    '[.[] | select(.body | test($m))] | last | .createdAt // ""')
  [ -z "$created_at" ] && { echo ""; return; }
  local marker_epoch now_epoch
  marker_epoch=$(date -u -d "$created_at" +%s 2>/dev/null) || { echo ""; return; }
  now_epoch=$(date -u +%s)
  echo $(( (now_epoch - marker_epoch) / 60 ))
}

has_new_comment_after_report() {
  local issue_num="$1"
  local report_marker="$2"
  local comments
  comments=$(python3 "$FACTORY_PROVIDERS_CLI" tracker get-comments --id "$issue_num" 2>/dev/null) \
    || { echo "no"; return; }

  # A comment counts as reviewer feedback only if it appears AFTER the last spec report
  # AND is not one of our own automated comments. The dark factory posts its cost report
  # after the spec on the success path (entrypoint.sh post_cost_report), and the scheduler
  # posts pipeline-status comments — none are feedback, so re-running the spec on them
  # loops the pipeline (issue #124: cost report -> spurious second spec). Match on
  # footer/marker, NOT author: every comment is authored by the same PAT account.
  local bot_re="Posted by ${FACTORY_PRODUCT_NAME} Refinement Pipeline|Posted by ${FACTORY_PRODUCT_NAME} Backlog Scheduler|Posted by ${FACTORY_PRODUCT_NAME} Dark Factory|Updated by ${FACTORY_PRODUCT_NAME} Dark Factory|dark-factory-cost-report|Posted by ${FACTORY_PRODUCT_NAME} Epic Autopilot"

  local has_human
  has_human=$(echo "$comments" | jq --arg marker "$report_marker" --arg bot "$bot_re" '
    (to_entries | map(select(.value.body | test($marker))) | last | .key // -1) as $ridx
    | if $ridx == -1 then false
      else (to_entries | any(.key > $ridx and (.value.body | test($bot) | not)))
      end')

  if [ "$has_human" = "true" ]; then echo "yes"; else echo "no"; fi
}
```

In `scheduler.sh`, remove lines 236–309 (the `has_refine_skip_label` through
`elapsed_minutes_since_marker` block, including the `# --- Dispatch ceiling classification
(#339) ---` comment) — leave `spec_advance_check`/`plan_advance_check`/`end_gate_check`
(now-current L311–418, unaffected) in place — then remove `has_new_comment_after_report`
(now-current L420–443, immediately after `end_gate_check`).

Add the source line right after the existing `identity.sh` source (current L13):

```bash
source "$(dirname "${BASH_SOURCE[0]:-$0}")/scripts/identity.sh"
source "$(dirname "${BASH_SOURCE[0]:-$0}")/scripts/scheduler_lib.sh"
```

Update the stale `SCHEDULER_SOURCE_ONLY` guard comment (current L784–786):

```bash
# When sourced for testing (SCHEDULER_SOURCE_ONLY=1) stop here: the helper functions
# and constants above are now defined (the pure predicates via scripts/scheduler_lib.sh,
# sourced above; the rest inline), but the startup probes and poll loop below must
# not run (they would call gh and block forever in `while true`).
```

**Verify pass:**

```bash
bash tests/test_scheduler_lib.sh
```

Expected: `Results: 17 passed, 0 failed`, exit 0.

```bash
for t in test_159_regression test_config_deletion test_dispatch_ceiling \
         test_has_new_comment_after_report test_scheduler test_scheduler_pagination; do
  bash "tests/${t}.sh" || echo "FAILED: $t"
done
```

Expected: all 6 print `Results: N passed, 0 failed` unchanged from Task 0's baseline counts
— zero content changes to these files, so this is a pure regression check.

**Commit:**

```bash
git add scripts/scheduler_lib.sh tests/test_scheduler_lib.sh scheduler.sh
git commit -m "refactor(scheduler): extract sourceable predicate lib (step 1/4, #185)"
```

---

## Step 2: Extract stage functions (R2) — pure code moves, guards stay inline

Insert a new `# --- Stage functions (poll loop) ---` section **before the
`SCHEDULER_SOURCE_ONLY` early-return guard** (current L784–789) — immediately after
`has_new_comment_after_report` was removed in Task 2 (i.e. right where that function used
to end, now the tail of the helper-function region) and before the guard's explanatory
comment. This placement is required, not stylistic: `tests/test_scheduler.sh` and the other
5 sourcing tests do `SCHEDULER_SOURCE_ONLY=1 source scheduler.sh`, which `return 0`s at the
guard — any function defined below it is invisible to those tests, and Task 7's Section R
calls `stage_*` functions directly. Do **not** place this section near `while true; do`
(current L845) as originally drafted; that would put every `stage_*` definition after the
guard's `return 0` and break Task 7's tests. Each task below adds functions to the
pre-guard section and replaces the corresponding inline block, in place inside the loop, at
its original call site with a single call — same cycle order as today. Run the full
regression suite (Task 0's list) after each task; expect byte-identical `Results: N passed, 0
failed` counts and identical log-line shapes, since these are verbatim moves.

### Task 3 — `stage_ci_gate`, `stage_rescue_blocked`, `stage_orphan_sweep` (P0, P0.6, sweep)

**Files:** `scheduler.sh`

Add to the new stage-functions section:

```bash
# --- Priority 0: In Review items with failing CI (gate red PRs out of review) ---
# Runs on EVERY cycle, independent of the factory-concurrency guard below: a PR with
# red CI must not sit in review (a human could approve/merge it) just because the
# factory happens to be busy. This only sets board status + posts a comment (it never
# dispatches a factory container), so it is safe to run while a factory run is active.
# The branch-aware Blocked retry below later continues the existing PR branch and
# re-runs validate (pytest) to fix the failures. Cheap, so we gate every red ticket
# this cycle — no DISPATCHED/break.
stage_ci_gate() {
  CI_BLOCKED=""   # space-padded list of issues gated this cycle (Priority 1 skips them)
  while IFS= read -r item; do
    ISSUE=$(get_issue_number "$item")
    if has_skip_label "$item"; then continue; fi

    PR_NUM=$(get_pr_for_issue "$ISSUE")
    [ -z "$PR_NUM" ] && continue

    FAILED=$(failing_checks_for_pr "$PR_NUM")
    FAIL_COUNT=$(echo "$FAILED" | jq 'length')
    [ "$FAIL_COUNT" -eq 0 ] && continue

    echo "[$(date -u +%FT%TZ)] ci_gate issue=#${ISSUE} pr=#${PR_NUM} failing=${FAIL_COUNT} action=move_to_blocked"
    set_board_status "$ISSUE" "$FACTORY_STATUS_BLOCKED"

    FAIL_LIST=$(echo "$FAILED" | jq -r '.[] | "- [\(.name)](\(.link))"')
    gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body "## Dark Factory — CI Failing, Moved to Blocked

PR #${PR_NUM} has failing CI checks, so this ticket has been moved out of **In review** to **Blocked**. The factory will retry automatically, continue the existing PR branch, and attempt to fix the failures.

**Failing checks:**
${FAIL_LIST}

---
*Posted by ${FACTORY_PRODUCT_NAME} Backlog Scheduler*" 2>/dev/null || true

    CI_BLOCKED="${CI_BLOCKED} ${ISSUE} "
  done < <(echo "$IN_REVIEW" | jq -c '.[]')
}

# --- Priority 0.6: rescue Blocked items whose PR is already green + mergeable ---
# Inverse of Priority 0. A ticket can sit in Blocked (CI gate, circuit-breaker trip,
# orphaned-run sweep) while its PR is actually green and conflict-free. The Priority 3
# retry loop below would re-dispatch "Continue" on it — re-running the whole pipeline,
# burning the Max session window, re-hitting the same gate — until the retry counter
# exhausts and trip_to_blocked parks it FOREVER with a mergeable PR stranded. Instead,
# promote it to In review so the normal merge flow (human / "Close issue #N") takes it.
# Dispatch-free (only sets board status + marks the PR ready + comments), so it runs
# every cycle regardless of factory capacity, like Priority 0. RESCUED is consumed by
# Priority 3 so a just-rescued issue is not retried in the same cycle.
stage_rescue_blocked() {
  RESCUED=""
  if [ "${BLOCKED_RESCUE_ENABLED:-true}" = "true" ]; then
    while IFS= read -r item; do
      ISSUE=$(get_issue_number "$item")
      if has_skip_label "$item"; then continue; fi
      # Above-ceiling items are parked in Blocked by design (#339), not failed.
      if has_above_ceiling_label "$item"; then continue; fi
      if is_issue_running "$ISSUE"; then continue; fi
      RESCUE_OUT=$(python3 "$FACTORY_CORE_CLI" rescue-blocked --issue "$ISSUE" 2>/dev/null) || true
      if [ "$RESCUE_OUT" = "rescued" ]; then
        echo "[$(date -u +%FT%TZ)] blocked_rescue issue=#${ISSUE} action=promoted_to_in_review"
        RESCUED="${RESCUED} ${ISSUE} "
        reset_retry "$ISSUE" || true
      fi
    done < <(echo "$BLOCKED" | jq -c '.[]')
  fi
}

# --- Sweep: recover orphaned "In progress" items ---
# We reach here whenever a factory slot is free (capacity guard above). An issue in
# "In progress" whose container is alive is skipped by is_issue_running below; one
# with no container was abandoned mid-run. The usual
# failure path (entrypoint on_failure -> Blocked) cannot fire for untrappable
# deaths — host reboot, OOM/SIGKILL — so those issues would otherwise sit stuck
# forever and silently consume a WIP slot. Route them into the Blocked retry path,
# exactly what on_failure would have done. (Skip-labels let a human park an item.)
stage_orphan_sweep() {
  while IFS= read -r item; do
    ISSUE=$(get_issue_number "$item")
    if has_skip_label "$item"; then continue; fi
    if is_issue_running "$ISSUE"; then continue; fi
    echo "[$(date -u +%FT%TZ)] sweep=orphaned_in_progress issue=#${ISSUE} action=move_to_blocked"
    set_board_status "$ISSUE" "$FACTORY_STATUS_BLOCKED"
    gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body "## Dark Factory — Orphaned Run Recovered

This issue was left in **In progress** with no running factory container — the run died without its error handler executing (e.g. a host restart or OOM/SIGKILL). The scheduler has moved it to **Blocked** so it will be retried automatically.

---
*Posted by ${FACTORY_PRODUCT_NAME} Backlog Scheduler*" 2>/dev/null || true
  done < <(echo "$IN_PROGRESS" | jq -c '.[]')
}
```

Replace the three original inline blocks (current L871–906, L908–933, L946–966) at their
call sites with:

```bash
  # --- Priority 0: CI gate (see stage_ci_gate) ---
  stage_ci_gate

  # --- Priority 0.6: rescue Blocked (see stage_rescue_blocked) ---
  stage_rescue_blocked
```

and, after the capacity guard (unchanged, stays inline):

```bash
  # --- Sweep: recover orphaned "In progress" items (see stage_orphan_sweep) ---
  stage_orphan_sweep
```

**Verify pass:** run Task 0's full command list. Expect identical pass counts and log shapes.

**Commit:** `git commit -am "refactor(scheduler): extract stage_ci_gate/stage_rescue_blocked/stage_orphan_sweep (step 2/4, #185)"`

### Task 4 — `stage_conflict_resolve`, `stage_review_triage` (P1.5, P1)

**Files:** `scheduler.sh`

Add to the stage-functions section (guard stays inline for now):

```bash
# --- Priority 1.5: In Review items with merge conflicts (proactive auto-resolve) ---
# Runs every cycle after the factory guard. Scans in-review PRs for GitHub's
# CONFLICTING mergeability state and dispatches a deconflict run before any
# human comments are processed. Honors SKIP_LABELS, CI_BLOCKED, and is_issue_running.
# UNKNOWN is skipped — GitHub hasn't computed mergeability yet.
stage_conflict_resolve() {
  if [ "$MAIN_IS_RED" = "true" ] || [ "$SESSION_WINDOW_PAUSED" = "true" ]; then
    echo "[$(date -u +%FT%TZ)] main_red=${MAIN_IS_RED} session_window_paused=${SESSION_WINDOW_PAUSED} action=skip_deconflict"
  elif [ "${CONFLICT_RESOLUTION_ENABLED:-true}" = "true" ]; then
    while IFS= read -r item; do
      [ -n "$DISPATCHED" ] && break
      ISSUE=$(get_issue_number "$item")
      if has_skip_label "$item"; then continue; fi
      case "$CI_BLOCKED" in *" $ISSUE "*) continue ;; esac
      if is_issue_running "$ISSUE"; then continue; fi

      SIG_RESULT=$(check_failure_signature "$ISSUE" "resolve")
      if echo "$SIG_RESULT" | grep -q "stuck=true"; then
        SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
        trip_to_blocked "$ISSUE" "resolve" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
        continue
      fi

      RETRIES=$(get_retry_count "${ISSUE}:resolve")
      if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
        trip_to_blocked "$ISSUE" "resolve" "retry limit of ${MAX_RETRIES} reached for conflict resolution"
        continue
      fi

      PR_NUM=$(get_pr_for_issue "$ISSUE")
      [ -z "$PR_NUM" ] && continue

      MERGEABLE=$(check_pr_mergeable "$PR_NUM")
      case "$MERGEABLE" in
        CONFLICTING)
          echo "[$(date -u +%FT%TZ)] conflict_gate issue=#${ISSUE} pr=#${PR_NUM} mergeable=CONFLICTING action=dispatch_deconflict"
          increment_retry "${ISSUE}:resolve" || true
          if dispatch "Deconflict issue #${ISSUE}"; then
            DISPATCHED="Deconflict issue #${ISSUE}"
          fi
          ;;
        UNKNOWN)
          echo "[$(date -u +%FT%TZ)] conflict_gate issue=#${ISSUE} pr=#${PR_NUM} mergeable=UNKNOWN action=skip"
          ;;
      esac
    done < <(echo "$IN_REVIEW" | jq -c '.[]')
  fi
}

# --- Priority 1: In Review items with new comments (unblock existing work) ---
stage_review_triage() {
  while IFS= read -r item; do
    [ -n "$DISPATCHED" ] && break
    ISSUE=$(get_issue_number "$item")
    if has_skip_label "$item"; then continue; fi
    case "$CI_BLOCKED" in *" $ISSUE "*) continue ;; esac   # gated to Blocked this cycle

    if end_gate_check "$ISSUE" "$item"; then continue; fi

    NEW_COMMENTS=$(get_new_comments "$ISSUE")
    COMMENT_COUNT=$(echo "$NEW_COMMENTS" | jq 'length')
    if [ "$COMMENT_COUNT" -eq 0 ]; then continue; fi

    TITLE=$(echo "$item" | jq -r '.content.title')
    VERDICT=$(classify_comments "$ISSUE" "$TITLE" "$NEW_COMMENTS")

    case "$VERDICT" in
      MERGE)
        if dispatch "Close issue #${ISSUE}"; then
          DISPATCHED="Close issue #${ISSUE}"
        fi
        ;;
      CONTINUE)
        if ! is_issue_running "$ISSUE"; then
          if dispatch "Continue issue #${ISSUE}"; then
            DISPATCHED="Continue issue #${ISSUE}"
            reset_retry "$ISSUE"
          fi
        fi
        ;;
      SKIP) ;;
    esac
  done < <(echo "$IN_REVIEW" | jq -c '.[]')
}
```

Replace the two original inline blocks (current L1006–1051, L1053–1085) with:

```bash
  # --- Priority 1.5: conflict resolution (see stage_conflict_resolve) ---
  stage_conflict_resolve

  # --- Priority 1: in-review comment triage (see stage_review_triage) ---
  stage_review_triage
```

**Verify pass:** Task 0's full command list, identical pass counts.

**Commit:** `git commit -am "refactor(scheduler): extract stage_conflict_resolve/stage_review_triage (step 2/4, #185)"`

### Task 5 — `stage_ready_implement`, `stage_blocked_retry` (P2, P3)

**Files:** `scheduler.sh`

Add to the stage-functions section:

```bash
# --- Priority 2: Ready items (implement what's already refined+planned) ---
stage_ready_implement() {
  if [ "$MAIN_IS_RED" = "true" ] || [ "$SESSION_WINDOW_PAUSED" = "true" ]; then
    echo "[$(date -u +%FT%TZ)] main_red=${MAIN_IS_RED} session_window_paused=${SESSION_WINDOW_PAUSED} action=skip_implement"
  else
    while IFS= read -r item; do
      [ -n "$DISPATCHED" ] && break
      ISSUE=$(get_issue_number "$item")
      if has_skip_label "$item"; then continue; fi
      if [ "$IN_PROGRESS_COUNT" -ge "$MAX_IN_PROGRESS" ]; then break; fi
      if [ "$IN_REVIEW_COUNT" -ge "$MAX_IN_REVIEW" ]; then break; fi
      if ! dependencies_met "$ISSUE" "$BOARD_ITEMS"; then continue; fi
      if is_issue_running "$ISSUE"; then continue; fi

      # Dispatch ceiling (#339): park above-ceiling work for human pairing. The label
      # check stops the comment/board-move from repeating every poll cycle — the label
      # persists and comes back in the next fetch_board_items snapshot.
      if [ "${DISPATCH_CEILING_ENABLED:-true}" = "true" ] && is_above_ceiling "$item"; then
        if ! has_above_ceiling_label "$item"; then
          echo "[$(date -u +%FT%TZ)] ceiling_gate issue=#${ISSUE} action=above_ceiling_blocked"
          python3 "$FACTORY_PROVIDERS_CLI" tracker label --id "$ISSUE" \
            --add "$ABOVE_CEILING_LABEL" 2>/dev/null || true
          set_board_status "$ISSUE" "$FACTORY_STATUS_BLOCKED" || true
          gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body \
"## Scheduler — Above Dispatch Ceiling

This ticket has been classified as **above the autonomous dispatch ceiling** \
(size: XL, or size: M with a perf/architectural/migration title keyword).

Spec and plan are complete. **A human must pair on implementation.**

To proceed:
1. Remove the \`$ABOVE_CEILING_LABEL\` label.
2. Dispatch manually:
   \`\`\`bash
   docker compose --profile factory run --rm dark-factory \"Fix issue #${ISSUE}\"
   \`\`\`
   Or implement directly in a local worktree.

---
*Posted by ${FACTORY_PRODUCT_NAME} Backlog Scheduler*" 2>/dev/null || true
        fi
        continue
      fi

      if dispatch "Fix issue #${ISSUE}"; then
        DISPATCHED="Fix issue #${ISSUE}"
      fi
    done < <(echo "$READY" | jq -c '.[]')
  fi
}

# --- Priority 3: Blocked items (retry stuck work) ---
stage_blocked_retry() {
  if [ "$MAIN_IS_RED" = "true" ] || [ "$SESSION_WINDOW_PAUSED" = "true" ]; then
    echo "[$(date -u +%FT%TZ)] main_red=${MAIN_IS_RED} session_window_paused=${SESSION_WINDOW_PAUSED} action=skip_blocked_retry"
  else
    while IFS= read -r item; do
      [ -n "$DISPATCHED" ] && break
      ISSUE=$(get_issue_number "$item")
      if has_skip_label "$item"; then continue; fi
      # Promoted to In review by the Priority 0.6 rescue this cycle — don't re-dispatch
      # (its green PR is now in the merge flow; BLOCKED was snapshotted before the move).
      case "$RESCUED" in *" $ISSUE "*) continue ;; esac
      # Above-ceiling items in Blocked are parked by design (#339), not failed — the
      # retry loop must not auto-dispatch them.
      if has_above_ceiling_label "$item"; then continue; fi
      if is_issue_running "$ISSUE"; then continue; fi

      SIG_RESULT=$(check_failure_signature "$ISSUE" "implement")
      if echo "$SIG_RESULT" | grep -q "stuck=true"; then
        SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
        trip_to_blocked "$ISSUE" "implement" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
        continue
      fi

      RETRIES=$(get_retry_count "$ISSUE")
      if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
        trip_to_blocked "$ISSUE" "implement" "retry limit of ${MAX_RETRIES} reached"
        continue
      fi

      increment_retry "$ISSUE"
      # Branch-aware: a blocked item that already has a PR (e.g. red CI gated above, or a
      # continue run that failed mid-way) must be CONTINUED to reuse the existing branch.
      # Dispatching "Fix" would start a fresh branch that collides with the PR on push.
      if [ -n "$(get_pr_for_issue "$ISSUE")" ]; then
        if dispatch "Continue issue #${ISSUE}"; then
          DISPATCHED="Continue issue #${ISSUE}"
        fi
      else
        if dispatch "Fix issue #${ISSUE}"; then
          DISPATCHED="Fix issue #${ISSUE}"
        fi
      fi
    done < <(echo "$BLOCKED" | jq -c '.[]')
  fi
}
```

Replace the two original inline blocks (current L1087–1135, L1137–1180) with:

```bash
  # --- Priority 2: Ready → implement (see stage_ready_implement) ---
  stage_ready_implement

  # --- Priority 3: Blocked → retry (see stage_blocked_retry) ---
  stage_blocked_retry
```

**Verify pass:** Task 0's full command list, identical pass counts.

**Commit:** `git commit -am "refactor(scheduler): extract stage_ready_implement/stage_blocked_retry (step 2/4, #185)"`

### Task 6 — `stage_plan`, `stage_refine`, `stage_epic_autopilot` (P4, P5, P6)

**Files:** `scheduler.sh`

Add to the stage-functions section:

```bash
# --- Priority 4: Refined items (plan generation — advance refined work before pulling new backlog) ---
stage_plan() {
  if [ "$SESSION_WINDOW_PAUSED" = "true" ]; then
    echo "[$(date -u +%FT%TZ)] session_window_paused=true action=skip_plan"
  else
    while IFS= read -r item; do
      [ -n "$DISPATCHED" ] && break
      ISSUE=$(get_issue_number "$item")

      # Direct-to-PR plan auto-advance: handle before refine_skip_label blocks plan-pending-review
      if echo "$item" | jq -r '.labels[]?' 2>/dev/null | grep -qi "plan-pending-review" \
         && has_direct_to_pr_label "$item"; then
        if ! is_issue_running "$ISSUE" && [ "$REFINE_RUNNING" -lt "$REFINE_WIP_LIMIT" ]; then
          plan_advance_check "$ISSUE" "$item"
        fi
        continue
      fi

      if has_refine_skip_label "$item"; then continue; fi
      if is_issue_running "$ISSUE"; then continue; fi
      if [ "$REFINE_RUNNING" -ge "$REFINE_WIP_LIMIT" ]; then break; fi

      SIG_RESULT=$(check_failure_signature "$ISSUE" "plan")
      if echo "$SIG_RESULT" | grep -q "stuck=true"; then
        SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
        trip_to_blocked "$ISSUE" "plan" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
        continue
      fi

      RETRIES=$(get_retry_count "${ISSUE}:plan")
      if [ "$RETRIES" -ge "$REFINE_MAX_RETRIES" ]; then
        trip_to_blocked "$ISSUE" "plan" "retry limit of ${REFINE_MAX_RETRIES} reached"
        continue
      fi

      increment_retry "${ISSUE}:plan"
      gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body "📋 **Refinement Pipeline** — Starting plan generation and architect validation.

---
*Posted by ${FACTORY_PRODUCT_NAME} Backlog Scheduler*" 2>/dev/null || true
      if dispatch "Plan issue #${ISSUE}"; then
        DISPATCHED="Plan issue #${ISSUE}"
        REFINE_RUNNING=$((REFINE_RUNNING + 1))
      fi
    done < <(echo "$REFINED" | jq -c '.[]')
  fi
}

# --- Priority 5: Backlog items (refinement — prepare future work) ---
stage_refine() {
  if [ "$SESSION_WINDOW_PAUSED" = "true" ]; then
    echo "[$(date -u +%FT%TZ)] session_window_paused=true action=skip_refine"
  else
    while IFS= read -r item; do
      [ -n "$DISPATCHED" ] && break
      ISSUE=$(get_issue_number "$item")

      # Handle spec-pending-review items first (before skip-label check would filter them)
      ITEM_LABELS=$(echo "$item" | jq -r '.labels[]?' 2>/dev/null)
      if echo "$ITEM_LABELS" | grep -qi "spec-pending-review"; then
        if ! is_issue_running "$ISSUE" && [ "$REFINE_RUNNING" -lt "$REFINE_WIP_LIMIT" ]; then
          spec_advance_check "$ISSUE" "$item"
        fi
        continue
      fi

      if has_refine_skip_label "$item"; then continue; fi
      # Opt-in gate: only auto-refine Backlog items labelled ready-for-agent.
      # Unlabelled items are left for triage — humans add the label when the issue is ready.
      if ! has_opt_in_refine_label "$item" && ! has_direct_to_pr_label "$item"; then continue; fi
      if is_issue_running "$ISSUE"; then continue; fi
      if [ "$REFINE_RUNNING" -ge "$REFINE_WIP_LIMIT" ]; then break; fi

      SIG_RESULT=$(check_failure_signature "$ISSUE" "refine")
      if echo "$SIG_RESULT" | grep -q "stuck=true"; then
        SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
        trip_to_blocked "$ISSUE" "refine" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
        continue
      fi

      RETRIES=$(get_retry_count "${ISSUE}:refine")
      if [ "$RETRIES" -ge "$REFINE_MAX_RETRIES" ]; then
        trip_to_blocked "$ISSUE" "refine" "retry limit of ${REFINE_MAX_RETRIES} reached"
        continue
      fi

      increment_retry "${ISSUE}:refine"
      gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body "🧠 **Refinement Pipeline** — Starting brainstorming and spec generation.

---
*Posted by ${FACTORY_PRODUCT_NAME} Backlog Scheduler*" 2>/dev/null || true
      if dispatch "Refine issue #${ISSUE}"; then
        DISPATCHED="Refine issue #${ISSUE}"
        REFINE_RUNNING=$((REFINE_RUNNING + 1))
      fi
    done < <(echo "$BACKLOG" | jq -c '.[]')
  fi
}

# --- Priority 6: Epic Autopilot (starved self-unlock, #571) ---
# Runs ONLY when this cycle dispatched nothing (starved), main is green, and it is
# enabled. Reviews the refined, below-ceiling children of in-progress epics with Opus
# and advances the low-risk ones via direct-to-pr. Fail-soft: never abort the loop.
# Distinct one-line compound-condition shape — not folded into the STAGE_GUARD table.
stage_epic_autopilot() {
  if [ -z "$DISPATCHED" ] && [ "$MAIN_IS_RED" = "false" ] && [ "$SESSION_WINDOW_PAUSED" = "false" ] && [ "${EPIC_AUTOPILOT_ENABLED:-false}" = "true" ]; then
    AP_OUT=$(python3 "$FACTORY_CORE_CLI" epic-autopilot --once 2>&1) || true
    echo "[$(date -u +%FT%TZ)] ${AP_OUT}"
    case "$AP_OUT" in *"autopilot=advanced"*|*"autopilot=epic_started"*) DISPATCHED="$AP_OUT" ;; esac
  fi
}
```

Replace the three original inline blocks (current L1182–1226, L1228–1275, L1277–1285) with:

```bash
  # --- Priority 4: Refined → plan (see stage_plan) ---
  stage_plan

  # --- Priority 5: Backlog → refine (see stage_refine) ---
  stage_refine

  # --- Priority 6: Epic Autopilot (see stage_epic_autopilot) ---
  stage_epic_autopilot
```

**Verify pass:** Task 0's full command list, identical pass counts. This completes Step 2 —
all ten `stage_*` functions exist, are called directly in original cycle order, and no
behavior has changed (guards are still inline inside the 5 currently-guarded functions).

**Commit:** `git commit -am "refactor(scheduler): extract stage_plan/stage_refine/stage_epic_autopilot (step 2/4, #185)"`

---

## Step 3: Declarative per-stage guard table (R3)

### Task 7 — Write failing guard-semantics tests (Section R), then implement `dispatch_stage()` + the guard table

**Files:** `tests/test_scheduler.sh` (modified), `scheduler.sh` (modified)

`dispatch_stage()` — a new helper wrapping the guard check — becomes the real call path for
the 6 table-driven stages once this task lands (see Architecture: it must be defined
pre-guard, alongside the `stage_*` functions, so sourcing tests can call it). Section R
therefore targets `dispatch_stage` directly rather than the bare `stage_*` functions; write
it first, confirm it fails because `dispatch_stage` doesn't exist yet, then implement.

Append a new lettered section to `tests/test_scheduler.sh` (after Section Q's last assertion
at current L1104, before the `# ==========================================` / `# Cleanup`
banner at current L1106–1108):

```bash
# ==========================================
# R: Stage guard semantics (#185) — dispatch_stage must preserve per-stage heterogeneity
# ==========================================
echo ""
echo "--- R: Stage guard semantics ---"

# R1: stage_review_triage (P1) has no guard — dispatch_stage must still call it when MAIN_IS_RED=true.
MAIN_IS_RED=true; SESSION_WINDOW_PAUSED=false
IN_REVIEW='[{"content":{"number":901,"title":"t"},"labels":[]}]'
DISPATCHED=""; CI_BLOCKED=""
get_new_comments() { echo '[{"body":"please continue","author":"human"}]'; }
classify_comments() { echo "CONTINUE"; }
is_issue_running() { return 1; }
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
export -f get_new_comments classify_comments is_issue_running dispatch
: > "$STUB_LOG"
dispatch_stage stage_review_triage
assert_eq "R1: dispatch_stage(stage_review_triage) still dispatches Continue when MAIN_IS_RED=true" \
  "1" "$(grep -c 'dispatch Continue issue #901' "$STUB_LOG" || true)"

# R2: stage_plan (P4) is SESSION_WINDOW_PAUSED-only — dispatch_stage must still call it when only MAIN_IS_RED=true.
MAIN_IS_RED=true; SESSION_WINDOW_PAUSED=false; REFINED='[]'; REFINE_RUNNING=0
STDOUT_R2=$(dispatch_stage stage_plan 2>&1)
assert_eq "R2: dispatch_stage(stage_plan) does not skip when only MAIN_IS_RED is true" \
  "0" "$(echo "$STDOUT_R2" | grep -c 'action=skip_plan')"

# R3: dispatch_stage(stage_plan) DOES skip when SESSION_WINDOW_PAUSED=true.
MAIN_IS_RED=false; SESSION_WINDOW_PAUSED=true
STDOUT_R3=$(dispatch_stage stage_plan 2>&1)
assert_eq "R3: dispatch_stage(stage_plan) skips on session_window_paused" \
  "1" "$(echo "$STDOUT_R3" | grep -c 'action=skip_plan')"

# R4: dispatch_stage(stage_conflict_resolve) skips on MAIN_IS_RED=true (red-or-paused guard).
MAIN_IS_RED=true; SESSION_WINDOW_PAUSED=false; IN_REVIEW='[]'
STDOUT_R4=$(dispatch_stage stage_conflict_resolve 2>&1)
assert_eq "R4: dispatch_stage(stage_conflict_resolve) skips on main_red" \
  "1" "$(echo "$STDOUT_R4" | grep -c 'action=skip_deconflict')"

# R5: dispatch_stage(stage_ready_implement) skips on SESSION_WINDOW_PAUSED=true (red-or-paused guard).
MAIN_IS_RED=false; SESSION_WINDOW_PAUSED=true; READY='[]'
STDOUT_R5=$(dispatch_stage stage_ready_implement 2>&1)
assert_eq "R5: dispatch_stage(stage_ready_implement) skips on session_window_paused" \
  "1" "$(echo "$STDOUT_R5" | grep -c 'action=skip_implement')"

# R6: dispatch_stage(stage_blocked_retry) skips on MAIN_IS_RED=true (red-or-paused guard).
MAIN_IS_RED=true; SESSION_WINDOW_PAUSED=false; BLOCKED='[]'; RESCUED=""
STDOUT_R6=$(dispatch_stage stage_blocked_retry 2>&1)
assert_eq "R6: dispatch_stage(stage_blocked_retry) skips on main_red" \
  "1" "$(echo "$STDOUT_R6" | grep -c 'action=skip_blocked_retry')"

MAIN_IS_RED=false; SESSION_WINDOW_PAUSED=false

# R7: stage_epic_autopilot is excluded from the guard table (own compound condition, R3).
assert_eq "R7: stage_epic_autopilot not a STAGE_GUARD key" \
  "0" "$([ -v 'STAGE_GUARD[stage_epic_autopilot]' ] && echo 1 || echo 0)"
assert_eq "R7b: stage_epic_autopilot not in STAGE_ORDER" \
  "0" "$(printf '%s\n' "${STAGE_ORDER[@]}" | grep -c '^stage_epic_autopilot$')"

# R8: stage_review_triage is in STAGE_ORDER with guard type "none" (runs unconditionally
# through dispatch_stage, matching its already-guardless behavior).
assert_eq "R8: stage_review_triage is in STAGE_ORDER" \
  "1" "$(printf '%s\n' "${STAGE_ORDER[@]}" | grep -c '^stage_review_triage$')"
assert_eq "R8b: stage_review_triage guard type is none" \
  "none" "${STAGE_GUARD[stage_review_triage]}"
```

**Verify fail:**

```bash
bash tests/test_scheduler.sh 2>&1 | tail -30
```

Expected: non-zero exit, script aborts before printing the final `Results:` line. R1 fails
loudly (`dispatch_stage: command not found`); most of R2–R8 fail their `assert_eq` (some
merely mismatch — `${STAGE_ORDER[@]}` expanded inside `$(...)` on a never-declared array
silently yields empty rather than erroring); R8b's `${STAGE_GUARD[stage_review_triage]}` is
referenced *outside* a subshell, so it trips `set -u`'s unbound-variable check and kills the
script outright. Whichever assertions fail loudly vs. quietly, the net result — non-zero
exit, no clean `Results:` summary — confirms Section R exercises code that doesn't exist on
`scheduler.sh` yet.

Now implement. Add to the pre-guard stage-functions section (same place as the ten `stage_*`
functions, per the Architecture note — testability requires this, not the `WIP_DATA`-style
post-guard placement the spec's Assumptions section suggested):

```bash
# --- Stage guard-type table (#185): collapses the 3 verbatim MAIN_IS_RED ||
# SESSION_WINDOW_PAUSED conditionals into one declarative evaluation site. Guard types
# are genuinely heterogeneous — do not apply one guard uniformly (P1 is unguarded;
# P4/P5 are session-window-only; P6 keeps its own compound condition below the table).
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
STAGE_ORDER=(stage_conflict_resolve stage_review_triage stage_ready_implement stage_blocked_retry stage_plan stage_refine)

# The single declarative evaluation site R3 requires. Pre-guard-defined (unlike the bare
# for-loop it replaces at the call site) so SCHEDULER_SOURCE_ONLY=1 tests can exercise the
# guard directly, per-stage, without needing a live poll cycle.
dispatch_stage() {
  local _stage="$1"
  case "${STAGE_GUARD[$_stage]}" in
    red_or_paused)
      if [ "$MAIN_IS_RED" = "true" ] || [ "$SESSION_WINDOW_PAUSED" = "true" ]; then
        echo "[$(date -u +%FT%TZ)] main_red=${MAIN_IS_RED} session_window_paused=${SESSION_WINDOW_PAUSED} action=${STAGE_SKIP_ACTION[$_stage]}"
        return 0
      fi
      ;;
    paused_only)
      if [ "$SESSION_WINDOW_PAUSED" = "true" ]; then
        echo "[$(date -u +%FT%TZ)] session_window_paused=true action=${STAGE_SKIP_ACTION[$_stage]}"
        return 0
      fi
      ;;
    none) ;;
  esac
  "$_stage"
}
```

Strip the now-redundant guard out of the 5 guarded function bodies, leaving only the
previously-guarded logic (the `elif`/`else` body), e.g.:

```bash
stage_conflict_resolve() {
  [ "${CONFLICT_RESOLUTION_ENABLED:-true}" = "true" ] || return 0
  while IFS= read -r item; do
    [ -n "$DISPATCHED" ] && break
    ISSUE=$(get_issue_number "$item")
    if has_skip_label "$item"; then continue; fi
    case "$CI_BLOCKED" in *" $ISSUE "*) continue ;; esac
    if is_issue_running "$ISSUE"; then continue; fi

    SIG_RESULT=$(check_failure_signature "$ISSUE" "resolve")
    if echo "$SIG_RESULT" | grep -q "stuck=true"; then
      SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
      trip_to_blocked "$ISSUE" "resolve" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
      continue
    fi

    RETRIES=$(get_retry_count "${ISSUE}:resolve")
    if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
      trip_to_blocked "$ISSUE" "resolve" "retry limit of ${MAX_RETRIES} reached for conflict resolution"
      continue
    fi

    PR_NUM=$(get_pr_for_issue "$ISSUE")
    [ -z "$PR_NUM" ] && continue

    MERGEABLE=$(check_pr_mergeable "$PR_NUM")
    case "$MERGEABLE" in
      CONFLICTING)
        echo "[$(date -u +%FT%TZ)] conflict_gate issue=#${ISSUE} pr=#${PR_NUM} mergeable=CONFLICTING action=dispatch_deconflict"
        increment_retry "${ISSUE}:resolve" || true
        if dispatch "Deconflict issue #${ISSUE}"; then
          DISPATCHED="Deconflict issue #${ISSUE}"
        fi
        ;;
      UNKNOWN)
        echo "[$(date -u +%FT%TZ)] conflict_gate issue=#${ISSUE} pr=#${PR_NUM} mergeable=UNKNOWN action=skip"
        ;;
    esac
  done < <(echo "$IN_REVIEW" | jq -c '.[]')
}
```

Apply the same transform — delete the leading `if [ "$MAIN_IS_RED" = "true" ] || [
"$SESSION_WINDOW_PAUSED" = "true" ]; then echo ...; else` (or, for P4/P5, `if [
"$SESSION_WINDOW_PAUSED" = "true" ]; then echo ...; else`) and its closing `fi`, keeping the
former `else`-branch body unchanged and unindented one level — to the remaining four
guarded functions. `stage_review_triage` is unchanged — it already has no guard.

```bash
stage_ready_implement() {
  while IFS= read -r item; do
    [ -n "$DISPATCHED" ] && break
    ISSUE=$(get_issue_number "$item")
    if has_skip_label "$item"; then continue; fi
    if [ "$IN_PROGRESS_COUNT" -ge "$MAX_IN_PROGRESS" ]; then break; fi
    if [ "$IN_REVIEW_COUNT" -ge "$MAX_IN_REVIEW" ]; then break; fi
    if ! dependencies_met "$ISSUE" "$BOARD_ITEMS"; then continue; fi
    if is_issue_running "$ISSUE"; then continue; fi

    # Dispatch ceiling (#339): park above-ceiling work for human pairing. The label
    # check stops the comment/board-move from repeating every poll cycle — the label
    # persists and comes back in the next fetch_board_items snapshot.
    if [ "${DISPATCH_CEILING_ENABLED:-true}" = "true" ] && is_above_ceiling "$item"; then
      if ! has_above_ceiling_label "$item"; then
        echo "[$(date -u +%FT%TZ)] ceiling_gate issue=#${ISSUE} action=above_ceiling_blocked"
        python3 "$FACTORY_PROVIDERS_CLI" tracker label --id "$ISSUE" \
          --add "$ABOVE_CEILING_LABEL" 2>/dev/null || true
        set_board_status "$ISSUE" "$FACTORY_STATUS_BLOCKED" || true
        gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body \
"## Scheduler — Above Dispatch Ceiling

This ticket has been classified as **above the autonomous dispatch ceiling** \
(size: XL, or size: M with a perf/architectural/migration title keyword).

Spec and plan are complete. **A human must pair on implementation.**

To proceed:
1. Remove the \`$ABOVE_CEILING_LABEL\` label.
2. Dispatch manually:
   \`\`\`bash
   docker compose --profile factory run --rm dark-factory \"Fix issue #${ISSUE}\"
   \`\`\`
   Or implement directly in a local worktree.

---
*Posted by ${FACTORY_PRODUCT_NAME} Backlog Scheduler*" 2>/dev/null || true
      fi
      continue
    fi

    if dispatch "Fix issue #${ISSUE}"; then
      DISPATCHED="Fix issue #${ISSUE}"
    fi
  done < <(echo "$READY" | jq -c '.[]')
}

stage_blocked_retry() {
  while IFS= read -r item; do
    [ -n "$DISPATCHED" ] && break
    ISSUE=$(get_issue_number "$item")
    if has_skip_label "$item"; then continue; fi
    # Promoted to In review by the Priority 0.6 rescue this cycle — don't re-dispatch
    # (its green PR is now in the merge flow; BLOCKED was snapshotted before the move).
    case "$RESCUED" in *" $ISSUE "*) continue ;; esac
    # Above-ceiling items in Blocked are parked by design (#339), not failed — the
    # retry loop must not auto-dispatch them.
    if has_above_ceiling_label "$item"; then continue; fi
    if is_issue_running "$ISSUE"; then continue; fi

    SIG_RESULT=$(check_failure_signature "$ISSUE" "implement")
    if echo "$SIG_RESULT" | grep -q "stuck=true"; then
      SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
      trip_to_blocked "$ISSUE" "implement" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
      continue
    fi

    RETRIES=$(get_retry_count "$ISSUE")
    if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
      trip_to_blocked "$ISSUE" "implement" "retry limit of ${MAX_RETRIES} reached"
      continue
    fi

    increment_retry "$ISSUE"
    # Branch-aware: a blocked item that already has a PR (e.g. red CI gated above, or a
    # continue run that failed mid-way) must be CONTINUED to reuse the existing branch.
    # Dispatching "Fix" would start a fresh branch that collides with the PR on push.
    if [ -n "$(get_pr_for_issue "$ISSUE")" ]; then
      if dispatch "Continue issue #${ISSUE}"; then
        DISPATCHED="Continue issue #${ISSUE}"
      fi
    else
      if dispatch "Fix issue #${ISSUE}"; then
        DISPATCHED="Fix issue #${ISSUE}"
      fi
    fi
  done < <(echo "$BLOCKED" | jq -c '.[]')
}

stage_plan() {
  while IFS= read -r item; do
    [ -n "$DISPATCHED" ] && break
    ISSUE=$(get_issue_number "$item")

    # Direct-to-PR plan auto-advance: handle before refine_skip_label blocks plan-pending-review
    if echo "$item" | jq -r '.labels[]?' 2>/dev/null | grep -qi "plan-pending-review" \
       && has_direct_to_pr_label "$item"; then
      if ! is_issue_running "$ISSUE" && [ "$REFINE_RUNNING" -lt "$REFINE_WIP_LIMIT" ]; then
        plan_advance_check "$ISSUE" "$item"
      fi
      continue
    fi

    if has_refine_skip_label "$item"; then continue; fi
    if is_issue_running "$ISSUE"; then continue; fi
    if [ "$REFINE_RUNNING" -ge "$REFINE_WIP_LIMIT" ]; then break; fi

    SIG_RESULT=$(check_failure_signature "$ISSUE" "plan")
    if echo "$SIG_RESULT" | grep -q "stuck=true"; then
      SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
      trip_to_blocked "$ISSUE" "plan" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
      continue
    fi

    RETRIES=$(get_retry_count "${ISSUE}:plan")
    if [ "$RETRIES" -ge "$REFINE_MAX_RETRIES" ]; then
      trip_to_blocked "$ISSUE" "plan" "retry limit of ${REFINE_MAX_RETRIES} reached"
      continue
    fi

    increment_retry "${ISSUE}:plan"
    gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body "📋 **Refinement Pipeline** — Starting plan generation and architect validation.

---
*Posted by ${FACTORY_PRODUCT_NAME} Backlog Scheduler*" 2>/dev/null || true
    if dispatch "Plan issue #${ISSUE}"; then
      DISPATCHED="Plan issue #${ISSUE}"
      REFINE_RUNNING=$((REFINE_RUNNING + 1))
    fi
  done < <(echo "$REFINED" | jq -c '.[]')
}

stage_refine() {
  while IFS= read -r item; do
    [ -n "$DISPATCHED" ] && break
    ISSUE=$(get_issue_number "$item")

    # Handle spec-pending-review items first (before skip-label check would filter them)
    ITEM_LABELS=$(echo "$item" | jq -r '.labels[]?' 2>/dev/null)
    if echo "$ITEM_LABELS" | grep -qi "spec-pending-review"; then
      if ! is_issue_running "$ISSUE" && [ "$REFINE_RUNNING" -lt "$REFINE_WIP_LIMIT" ]; then
        spec_advance_check "$ISSUE" "$item"
      fi
      continue
    fi

    if has_refine_skip_label "$item"; then continue; fi
    # Opt-in gate: only auto-refine Backlog items labelled ready-for-agent.
    # Unlabelled items are left for triage — humans add the label when the issue is ready.
    if ! has_opt_in_refine_label "$item" && ! has_direct_to_pr_label "$item"; then continue; fi
    if is_issue_running "$ISSUE"; then continue; fi
    if [ "$REFINE_RUNNING" -ge "$REFINE_WIP_LIMIT" ]; then break; fi

    SIG_RESULT=$(check_failure_signature "$ISSUE" "refine")
    if echo "$SIG_RESULT" | grep -q "stuck=true"; then
      SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
      trip_to_blocked "$ISSUE" "refine" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
      continue
    fi

    RETRIES=$(get_retry_count "${ISSUE}:refine")
    if [ "$RETRIES" -ge "$REFINE_MAX_RETRIES" ]; then
      trip_to_blocked "$ISSUE" "refine" "retry limit of ${REFINE_MAX_RETRIES} reached"
      continue
    fi

    increment_retry "${ISSUE}:refine"
    gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body "🧠 **Refinement Pipeline** — Starting brainstorming and spec generation.

---
*Posted by ${FACTORY_PRODUCT_NAME} Backlog Scheduler*" 2>/dev/null || true
    if dispatch "Refine issue #${ISSUE}"; then
      DISPATCHED="Refine issue #${ISSUE}"
      REFINE_RUNNING=$((REFINE_RUNNING + 1))
    fi
  done < <(echo "$BACKLOG" | jq -c '.[]')
}
```

Replace the six direct calls added in Tasks 4–6 (`stage_conflict_resolve` through
`stage_refine`) with a loop over `dispatch_stage`, in the same position, immediately
followed by the unchanged `stage_epic_autopilot` call:

```bash
  # --- Priorities 1.5/1/2/3/4/5: guard-table-driven dispatch (see dispatch_stage) ---
  for _stage in "${STAGE_ORDER[@]}"; do
    dispatch_stage "$_stage"
  done

  # --- Priority 6: Epic Autopilot (see stage_epic_autopilot) ---
  stage_epic_autopilot
```

**Verify pass:**

```bash
bash tests/test_scheduler.sh 2>&1 | tail -30
for t in test_159_regression test_config_deletion test_dispatch_ceiling \
         test_has_new_comment_after_report test_scheduler_pagination \
         test_scheduler_autopilot_guard test_scheduler_ceiling test_scheduler_main_red_fixer \
         test_epic_autopilot_config test_identity; do
  bash "tests/${t}.sh" || echo "FAILED: $t"
done
```

Expected: `test_scheduler.sh` prints `Results: N passed, 0 failed` including all 10 new
Section R assertions (R1–R8b) — R1–R6 confirm `dispatch_stage` preserves each stage's exact
skip/run behavior (P1 unguarded, P4/P5 paused-only, P1.5/P2/P3 red-or-paused), R7/R7b/R8/R8b
confirm the table's shape matches R3 (`stage_epic_autopilot` excluded, `stage_review_triage`
included with guard type `none`). Every other file in the loop stays unchanged from
baseline.

**Commit:**

```bash
git add tests/test_scheduler.sh scheduler.sh
git commit -m "refactor(scheduler): declarative per-stage guard table via dispatch_stage() + regression coverage (step 3/4, #185)"
```

---

## Step 4: Full regression pass (R4, spec Architecture step 4)

### Task 8 — Verification only, no code changes

**Files:** none

```bash
cd /workspace/dark-factory
for t in test_159_regression test_config_deletion test_dispatch_ceiling \
         test_has_new_comment_after_report test_scheduler test_scheduler_pagination \
         test_scheduler_autopilot_guard test_scheduler_ceiling test_scheduler_main_red_fixer \
         test_epic_autopilot_config test_identity; do
  echo "=== $t ==="; bash "tests/${t}.sh" || echo "FAILED: $t"
done
bash tests/test_scheduler_lib.sh
python -m pytest tests/ -v
bash smoke_gate.sh
```

Expected: every script exits 0 with an all-pass summary; `pytest` and `smoke_gate.sh` both
exit 0 — matching CI's exact gate per CLAUDE.md. If anything regresses, fix it in a follow-up
commit on this branch before publishing (do not weaken or skip the failing assertion).

No commit for this task (verification only, unless a regression fix is needed, in which case
commit that fix with message `fix(scheduler): <what broke> (step 4/4 regression, #185)`).

---

## Out of scope (recorded per spec R5/R6)

- Migrating stage decision logic into `factory_core` (Python) — considered and deferred to a
  follow-up ticket; every AC here is bash-specific and would be violated by a rewrite.
- Any change to `factory_core/breaker.py`, `factory_core/board.py`,
  `factory_core/epic_autopilot.py` — this refactor only relocates existing thin adapter
  call-sites (`get_retry_count`, `increment_retry`, `reset_retry`, `trip_to_blocked`,
  `check_failure_signature`, `set_board_status`) into `stage_*` functions; it never opens
  those modules.
- Extracting the session-window-paused / main-is-red sentinel-read blocks into named
  helpers — left inline per the spec's explicit "either choice is acceptable" latitude, to
  keep this diff mechanical.
