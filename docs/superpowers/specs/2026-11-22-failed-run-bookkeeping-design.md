# Failed and Paused Workflow Runs Skip Bookkeeping — Fix `entrypoint.sh`'s Trap Gap

**Issue:** #526

---

## Overview / Problem Statement

`entrypoint.sh` installs an `ERR` trap, `on_failure` (entrypoint.sh:504-634), at line
635. From that point on, an *unguarded command that returns non-zero* fires it, and
`on_failure` is the only place that writes a `failed` run-record entry and assembles
`run-record.json`, posts the run's failure comment, moves `fix`/`continue` issues to
**Blocked**, and posts the cost report.

The main workflow loop (entrypoint.sh:1023-1078) wraps `archon workflow run
archon-dark-factory "$ARGUMENTS"` and inspects its captured exit code directly — it
never lets that non-zero exit code propagate as a *command failure* under
`set -euo pipefail`, because every branch that follows is itself a conditional
(`if`/`case`) or an explicit `exit`. Bash's `ERR` trap does not fire on an explicit
`exit` (the script says so itself at entrypoint.sh:649-651, confirmed with a bash
probe). The result, traced against `main`@`5724b75` and documented in
[`docs/run-lifecycle.md`, "Open questions and oddities"](../../run-lifecycle.md#open-questions-and-oddities),
items 6–9 and 15:

1. **(Item 6) A genuine workflow failure never reaches `on_failure`.** When `archon
   workflow run` itself exits non-zero (a gate blocking downstream of a node failure, a
   crashed node, anything), the loop's failure branch (entrypoint.sh:1071-1074) runs
   `run_post_mortem`, `_write_error_signature`, then `exit "$EXIT_CODE"` — a bare exit.
   None of `on_failure`'s bookkeeping runs: no `failed` run-record entry or assembled
   `run-record.json` (so `harness_economics`'s `outcome.state == "failed"` scoring is
   unreachable for this path), no board move to Blocked for `fix`/`continue`, no
   "Dark Factory Run — Failed" / "Refinement Pipeline — Failed" comment, and no cost
   report (`post_cost_report` is only ever called from `on_failure`'s two branches and
   the success path after the loop — never from here). The run already does call
   `_write_error_signature` on this path today, so the scheduler's breaker/retry
   accounting (`check_failure_signature`, `trip_to_blocked`) is not blind — but the
   per-run comment, board move and cost report are.

   Today's only safety net is `scheduler.sh`'s `stage_orphan_sweep`
   (scheduler.sh:1016-1031), and it only partially covers the gap: it fires on the
   *next poll*, only for an issue already sitting in **In progress** (which excludes
   `refine`/`plan`/`deconflict` entirely — entrypoint.sh:193 never moves those to In
   Progress), and it posts a generic "Dark Factory — Orphaned Run Recovered" comment
   that says the run "died without its error handler executing (e.g. a host restart or
   OOM/SIGKILL)" — not true for this path, which exits cleanly. No cost report is ever
   produced for the failed attempt either way.

2. **(Item 7) The session-window pause comment is unreachable in normal operation.**
   `on_failure`'s own pause branch (the "Paused (session window)" comment + cost
   report) is gated on `TMP_OUT` being set and `_handle_session_window_pause` matching
   — but the loop's own pause check (entrypoint.sh:1031-1034) calls that same function
   directly and, on a match, does `rm -f "$TMP_OUT"; exit 0` without ever reaching
   `on_failure`. `_handle_session_window_pause` already writes the pause sentinel and a
   `paused` run-record stage event; only the human-visible comment and cost report are
   missing from this (the only live) call path.

3. **(Item 8) The adapter-validity gate runs before the self-contained fallback copy.**
   `_adapter_invalid_refuse` (entrypoint.sh:769) runs before the fallback-copy block
   (entrypoint.sh:780-797) that populates `$CLONE_DIR/dark-factory/scripts` for a
   target that doesn't commit that tree in-repo. This self-target repo is exactly such
   a target: `dark-factory/scripts` is listed in `.git/info/exclude` and exists in a
   running container only because that fallback block creates it. Every bookkeeping
   call inside `on_failure`, `_write_error_signature`, `run_post_mortem` and
   `post_cost_report` invokes `$CLONE_DIR/dark-factory/scripts/factory_core/cli.py`
   unguarded (the file's own "TARGET-PATH" convention). So any failure that fires the
   `ERR` trap before the fallback copy has run — most immediately, the adapter gate's
   own `return 1` — crashes `on_failure` partway through on such a target, after the
   adapter-invalid comment (and, for `fix`/`continue`, the Blocked move) but before the
   generic failure comment and the cost report.

4. **(Item 9) An unguarded pipeline in the kill-switch retry path.** With
   `SESSION_WINDOW_BACKOFF_ENABLED=false`, entrypoint.sh:1040-1041 parses a reset time
   out of the transcript with two `grep -ioP ... | head -1` pipelines and no `|| true`.
   Under `set -euo pipefail`, a transcript with no "resets HH:MMam/pm" text makes
   `grep` exit 1, which fires the `ERR` trap instead of falling through to the intended
   `SLEEP_SECS=300` default three lines later. The script already guards an
   analogous case the same way elsewhere (entrypoint.sh:102) and its own comment there
   describes exactly this hazard.

5. **(Item 15, the bookkeeping-tracking line only) A misleading log line.**
   `on_failure`'s `fix`/`continue` branch (entrypoint.sh:606) logs `"...Moving issue
   #$ISSUE_NUM back to Ready..."` immediately before calling `set_board_status
   "blocked"` (entrypoint.sh:609) — the log text contradicts the transition it precedes.

### Expected behavior (from the issue)

> A failed run should always leave a comment, a run-record entry and a cost report. A
> paused run should leave a visible trace. Guard the two unguarded paths.

### Explicitly out of scope

- Items 1–5, 10–14 of the same "Open questions and oddities" list (tracked under
  #527/#528/#531/#532) and the other "Smaller inconsistencies" bullets under item 15
  (also #527/#531) — none of those are failure-bookkeeping gaps, and bundling them in
  would widen this change past what it was refined for.
- Any gate, breaker, or budget behavior change. In particular: the existing split
  where `on_failure` does *not* move `refine`/`plan`/`deconflict` issues to Blocked
  (entrypoint.sh:582-586 — the scheduler's `trip_to_blocked` owns that after N
  attempts) is preserved as-is for the newly-reachable workflow-failure path too; see
  Requirement 2.
- A comment on the issue from the project's sanctioned PM-analysis channel ("Hermes
  Agent / Product Manager") raises this ticket's *priority* (a `must-have` evidence
  prerequisite for a separate hybrid-execution milestone) and repeats the same
  no-gate/no-breaker-relaxation and no-scope-bundling constraints already in the issue
  body. It does not add or change any functional requirement below.

## Requirements (from Q&A)

1. **Extract `on_failure`'s bookkeeping body into a plain, directly-callable function**
   (not a re-trigger of the trap) that takes the exit code and the transcript path
   (`TMP_OUT`, possibly empty) as explicit arguments rather than reading `$?`/a global.
   Call it from both:
   - `on_failure` itself (today's only caller, for setup-time failures — pre-workflow,
     `TMP_OUT` unset), and
   - the loop's workflow-failure branch (entrypoint.sh:1071-1074), in place of its
     current direct `run_post_mortem` + `_write_error_signature` calls, just before the
     (unchanged) final `exit "$EXIT_CODE"`.

   Each side effect (the error-signature write, the post-mortem, the run-record
   write/assemble, the comment, the board move, the cost report) must run exactly
   once per failed run — remove the loop's now-redundant direct calls to
   `run_post_mortem`/`_write_error_signature` once the shared function performs them,
   so the breaker's consecutive-failure-signature accounting is not double-counted.

2. **Preserve the existing refine/plan/deconflict vs. fix/continue board-move split**
   for the newly-reachable workflow-failure path: still no board move for
   `refine`/`plan`/`deconflict` (the scheduler's retry/breaker counter owns Blocked
   after N attempts — moving early would outrun `trip_to_blocked()` and risk the
   issue being retried with the wrong intent); still an immediate move to **Blocked**
   plus the specific "Dark Factory Run — Failed" comment for `fix`/`continue`. This is
   a direct consequence of Requirement 1 reusing the same function, not a new branch.

3. **Make the session-window pause trace reachable from the only live call path.**
   Extract the pause-comment-and-cost-report half of `on_failure`'s branch 1 (the
   "Paused (session window)" comment plus `post_cost_report`) into its own small
   function and call it from the loop's own pause check (entrypoint.sh:1031-1034)
   right before its existing `rm -f "$TMP_OUT"; exit 0` — which stays a plain `exit 0`
   (a pause is not a failure, so it still should not run the Requirement 1 function).

4. **Reorder the self-contained fallback copy ahead of the adapter-validity gate.**
   Move the `dark-factory/scripts` / `.archon/workflows` / `.archon/commands`
   fallback-copy block (entrypoint.sh:780-797) to run before `_adapter_invalid_refuse`
   (entrypoint.sh:769), so `$CLONE_DIR/dark-factory/scripts/factory_core/cli.py`
   exists before anything past the clone can fail. Do not add an `/opt`-fallback
   branch to the clone-side `cli.py` call sites inside `on_failure` et al. — that would
   blur the file's existing, deliberate "TARGET-PATH" convention (baked path only
   pre-clone, clone path only post-clone; see the comments at entrypoint.sh:183-185 and
   every "TARGET-PATH" comment in the file) for a much larger surface than this bug
   needs. Document as a known, unchanged limitation (not a new requirement) that a
   failure between `trap on_failure ERR` (line 635) and the clone completing (`gh auth
   setup-git` at :758, `git clone` at :765) still has no clone-side `cli.py` to call —
   neither this nor the prior ordering covers that window, and closing it is out of
   scope for this ticket.

5. **Guard the two unguarded pipelines in the kill-switch retry path.** Add `|| true`
   to the `RESET_TIME=...` and `RESET_TZ=...` assignments at entrypoint.sh:1040-1041,
   matching the existing guard pattern and rationale at entrypoint.sh:100-102 (no
   match → empty variable → the existing `SLEEP_SECS=300` default three lines later
   still applies, instead of an uncaught `ERR` trap firing mid-pipeline).

6. **Fix the misleading log line.** Change `"Moving issue #$ISSUE_NUM back to
   Ready..."` at entrypoint.sh:606 to describe the actual transition (**Blocked**).

7. **Close the acknowledged test-coverage gap for the loop-to-bookkeeping wiring.**
   `tests/test_entrypoint_session_window.sh`'s header comment already states that the
   while-loop's call into `on_failure`/the pause path is "verified by code review ...
   not by this test" today. TDD for this change means:
   - Unit-level: the extracted Requirement-1 and Requirement-3 functions are defined
     before the `ENTRYPOINT_SOURCE_ONLY=1` guard (entrypoint.sh:753), so they can be
     sourced and called directly in a test with the existing stub pattern (`git`,
     `gh`, `docker`, `claude`, and the `providers/cli.py`/`factory_core/cli.py` calls
     they shell out to) — assert the comment, board-move and cost-report calls happen
     (or, for refine/plan/deconflict, don't move the board) for representative
     exit codes and intents.
   - Wiring-level: the main execution block (after line 753) is not sourced by the
     existing test harness and isn't expected to become so by this change. Add a
     lightweight static assertion (a small new `tests/test_*.sh`, following
     `tests/test_entrypoint_adapter_validate.sh`'s pattern of grepping line order in
     `entrypoint.sh`) that: the workflow-failure branch calls the Requirement-1
     function before its `exit "$EXIT_CODE"` and no longer calls
     `run_post_mortem`/`_write_error_signature` directly; the pause branch calls the
     Requirement-3 function before its `exit 0`; and the fallback-copy block
     (Requirement 4) appears before the `_adapter_invalid_refuse` call in the file.

## Architecture / Approach

Minimal, in-file refactor of `entrypoint.sh` — no new scripts, no config keys, no
workflow DAG changes:

- Rename nothing externally-visible; keep `on_failure` as the `trap ... ERR` target
  (so its behavior for every *other* existing trigger — clone failure, dependency
  install failure, the missing-smoke-hook gate, a failed push in the deconflict path —
  is unchanged), but move its current body (entrypoint.sh:507-633, both branches) into
  a new function, e.g. `_record_run_outcome <exit_code> <tmp_out>`, that reads neither
  `$?` nor a global for the exit code. `on_failure` becomes a two-line wrapper:
  capture `$?` into a local, then call `_record_run_outcome "$EXIT_CODE" "${TMP_OUT:-}"`.
- The loop's failure branch (entrypoint.sh:1030-1074) calls
  `_record_run_outcome "$EXIT_CODE" "$TMP_OUT"` in place of its current direct
  `run_post_mortem`/`_write_error_signature` calls, immediately before `rm -f
  "$TMP_OUT"; exit "$EXIT_CODE"`.
- Split `_record_run_outcome`'s existing pause branch out into its own function (e.g.
  `_record_session_window_pause`) so the loop's pause check (entrypoint.sh:1031-1034)
  can call it directly without going through the rest of `_record_run_outcome`; have
  `_record_run_outcome` call it too, for the one remaining caller path where
  `on_failure` fires with `TMP_OUT` already set to a transcript that happens to match a
  pause (defense in depth for a theoretical corner case — not expected to be hit from
  any live call site after Requirement 3 lands, but cheap to keep correct).
- Move the fallback-copy block (lines 780-797) to immediately after `cd "$CLONE_DIR"`
  (line 766) and before `_adapter_invalid_refuse` (line 769) — a pure line move, no
  logic change.
- Add `|| true` to the two `grep` pipelines at lines 1040-1041.
- Fix the log string at line 606.

No node in `workflows/archon-dark-factory.yaml` changes. No config key changes
(`config/config.yaml`'s `side_effect.*`, `conformance.*`, breaker/retry knobs are
untouched). `scheduler.sh` is untouched — `stage_orphan_sweep` and the breaker remain
exactly as they are today; they become a true last-resort safety net (host reboot,
OOM/SIGKILL — the case its own comment already describes) rather than the primary
mechanism for an ordinary workflow-run failure, but no behavior of either is modified.

### Documentation

`docs/run-lifecycle.md` traces the exact lines this spec changes (its "Open questions
and oddities" items 6-9 and 15, and table rows throughout sections 2 and 9). The
implementation must update that doc: remove/resolve items 6-9 and the item-15 bullet
this ticket fixes (the doc's own table already lists #526 as their tracking issue), and
correct the line-number references and behavior tables in sections 2 ("Before the
workflow") and 9 ("How a run ends") to match the new call structure. This is
`doc-impact.md`-mapped work for the implement phase, not a refine-phase deliverable.

## Alternatives Considered

1. **Re-trigger the `ERR` trap from the loop instead of extracting a plain function**
   (e.g. have the loop's failure branch run a failing command so `trap ... ERR` fires
   naturally). Rejected: fragile and implicit — the file's own comments already flag
   that `exit` doesn't fire `ERR`, and deliberately forcing a trap fire from a
   non-error control-flow point (the loop already *has* the exit code in hand; it does
   not need to rediscover it via a trap) adds a layer of indirection for no benefit
   over calling a function directly with the exit code as an argument.

2. **Give the workflow-failure branch its own, independent bookkeeping sequence**
   (duplicate the run-record/comment/board-move/cost-report logic rather than sharing
   `on_failure`'s). Rejected: doubles the maintenance surface for behavior that should
   be identical between a setup-time failure and a workflow-run failure (same intents,
   same board-move policy, same comment markers), and the current code already shows
   this duplication risk — `run_post_mortem`/`_write_error_signature` are already
   called from both places today, which is exactly how `_write_error_signature` could
   double-fire today if it weren't idiomatic to re-derive it in one place.

3. **Add an `/opt`-fallback to every clone-side `cli.py` call in `on_failure` et al.**
   (item 8, option (b) from Q&A) instead of reordering the fallback copy. Rejected:
   would quietly change which `cli.py` build `on_failure`'s bookkeeping
   runs for every target, breaking the file's existing, deliberate convention that
   clone-side paths are target-controlled post-clone and baked paths are factory-only
   pre-clone (the "TARGET-PATH" comments). Reordering is a two-line move that removes
   the failure mode at its source instead of papering over every call site.

4. **Have the workflow-failure path move `refine`/`plan`/`deconflict` straight to
   Blocked** (since, unlike a setup-time failure, a workflow failure for those intents
   now *can* carry real diagnostic content). Rejected: changes when the breaker's
   attempt counter and Blocked transition kick in, which is breaker/gate-adjacent
   behavior the issue (and CLAUDE.md) say this ticket must not touch — and an early
   Blocked would surface the issue to "Fix" retry logic with the wrong intent (the
   existing comment at entrypoint.sh:582-586 explains the current design is deliberate
   for exactly this reason).

## Open Questions (non-blocking)

- Whether a failure between `trap on_failure ERR` (line 635) and the clone completing
  (`gh auth setup-git`, `git clone`) should get any bookkeeping at all is unresolved —
  today it gets none (no clone, no `dark-factory/scripts`, nothing to call), and
  nothing in this ticket changes that. If this gap matters in practice, it needs its
  own ticket; it is not one of items 6-9/15 and the issue's own scope note says not to
  bundle unrelated surface into this change.
- Exact function names (`_record_run_outcome`, `_record_session_window_pause`) are this
  spec's suggestion, not a hard requirement; the implement phase may choose different
  names as long as the call structure (one shared, argument-driven function for the
  failure path, reachable from both `on_failure` and the loop) is preserved.

## Assumptions (flagged)

- The `on_failure`-authored comments, markers, and board-move semantics themselves are
  correct today and out of scope to change beyond Requirement 6's one-line log fix —
  this spec only changes *which call paths reach them*, not their content, except
  where the issue explicitly calls out content (the log line).
- `_write_error_signature`'s existing behavior on the workflow-failure path (called
  once, from the shared function, with the same arguments it receives today) is
  unchanged; this spec only removes the duplicate call the loop currently also makes
  directly, so breaker/retry signature accounting for already-passing tests
  (`tests/test_entrypoint_error_signature.sh`) should not change.
- No target adapter (`.factory/adapter.yaml`) configuration is read by any of these
  functions beyond what they already read today (none of the five fixes touches
  adapter-driven behavior).
