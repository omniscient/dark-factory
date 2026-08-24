# Propagate set_board_status failure through the Tracker provider and cli.py

**Issue:** omniscient/dark-factory#335
**Status:** new spec, first refinement pass.
**Parent context:** Gate 3 code-review finding on PR #330 (#292), operator-revised 2026-07-31.
Directly follows #292, which added `entrypoint.sh`'s `BOARD_MOVE_OK` guard but explicitly scoped
`cli.py`/provider changes out of that ticket.

---

## Overview / Problem Statement

`entrypoint.sh`'s `set_board_status()` helper (added by #292) shells out to
`python3 cli.py tracker set-status --id <id> --status <status>` and branches on its exit code via
`set_board_status "blocked" 2>/dev/null || BOARD_MOVE_OK=false` (`entrypoint.sh:506`, also used at
`:154` and `:743`). That guard is dormant today because the Python process it calls can never exit
non-zero for a failed board move:

- `cli.py:_tracker_set_status` (`providers/cli.py:47-48`) calls `get_tracker().set_status(...)` and
  discards the return value — the process always exits 0 unless an unhandled exception is raised.
- `GitHubTracker.set_status` (`providers/tracker/github.py:141-145`) calls
  `board._find_item_by_number`; if that returns `""`, it returns silently (no exception, no
  signal).
- `board._find_item_by_number` (`board.py:22-39`) returns `""` both when `gh project item-list`
  exits non-zero (transport/auth/rate-limit failure) **and** when the item genuinely isn't on the
  board — the same signal for two different situations.
- `board._item_edit_status` (`board.py:42-50`) runs `gh project item-edit` with
  `capture_output=True` and no `check=True`; its exit code is always discarded.

Consequence (per the issue): a failed board move still renders "Issue has been moved to
**Blocked**." in the failure comment. The 2026-07-16 incident scenario (board stuck in **In
review** while the issue is actually blocked) is therefore still misreported even after #292.

This spec covers making that failure signal actually reach `entrypoint.sh`, without disturbing the
five other, unrelated call sites that already depend on today's fire-and-forget behavior: the four
direct Python callers of `board.set_board_status()` (`breaker.py`, `rescue.py`, `deconflict.py`,
`epic_autopilot.py`) plus `scripts/factory_core/cli.py:11-13 _board_move`, which is
`scheduler.sh:372-374 set_board_status()`'s path (callers at `scheduler.sh:267`, `308`, `759`,
`820`, `955`). `scheduler.sh` is not touched and sees no behavior change.

---

## Requirements

Distilled from the issue's fix direction and the Q&A below:

1. `board._item_edit_status` reports whether `gh project item-edit` actually succeeded (its exit
   code), instead of discarding it. On failure, the captured `gh` stderr is printed so a human
   reading container logs can see why where stderr is not suppressed (`entrypoint.sh:154`, DAG
   nodes; see Q&A 2) (requires switching its `subprocess.run` call to `text=True`).
2. `board._find_item_by_number`'s transport-failure case (`gh project item-list` exits non-zero or
   its JSON is unparseable) becomes distinguishable, internally, from its genuine "item not on the
   board" case. The existing **public** `find_board_item(issue_num) -> str` wrapper keeps returning
   `""` for both cases, unchanged — `tests/test_factory_core_board.py::test_find_board_item_wrong_number`
   and `::test_find_board_item_gh_failure` both assert `== ""` today and must stay green with no
   edit.
3. `board.set_board_status(issue_num, option_id) -> None` (the pre-existing function called
   fire-and-forget by `breaker.py:159`, `rescue.py:99`, `deconflict.py:212`, and
   `epic_autopilot.py:544` — none of which handle exceptions) keeps its exact current contract:
   still returns `None`, still never raises, still silently proceeds on failure. It may call the
   same lower-level helpers as the fixed path but must not surface their new success/failure signal
   to its callers. Zero behavior change for these four call sites, nor for the fifth caller of
   the same function — `scripts/factory_core/cli.py:11-13 _board_move`, which is
   `scheduler.sh:372-374 set_board_status()`'s path (callers at `scheduler.sh:267`, `308`, `759`,
   `820`, `955`). `scheduler.sh` is not touched and sees no behavior change.
4. `Tracker.set_status(self, id: str, canonical: str) -> bool` (`providers/tracker/base.py:30`) —
   the ABC's declared return type widens from `None` to `bool`. `True` means the item's status
   actually changed; `False` covers both "item not found on the board / no valid transition" and "a
   genuine transport failure occurred" — neither case raises an exception at the tracker level.
5. `GitHubTracker.set_status` implements the widened contract using the signals from Requirements 1
   and 2: returns `False` without raising when the item isn't found (preserving
   `tests/test_tracker_contract.py::test_set_status_unknown_item_is_safe_noop` and
   `tests/test_provider_tracker_parity.py::test_set_status_opaque_id_never_reaches_int`), returns
   `True` only when `_item_edit_status` reports success, `False` if it reports failure.
6. `JiraTracker.set_status` implements the same `bool` contract for parity, since
   `tests/test_tracker_contract.py` parametrizes over both trackers: returns `False` (no raise,
   existing fail-soft stderr message unchanged) when no valid transition exists — see
   `tests/test_provider_tracker_jira.py::test_set_status_missing_transition_edge_fails_soft` —
   and `True` after a successful transition POST. A genuine Jira HTTP failure keeps propagating via
   the `RuntimeError` `JiraTracker._request` already raises on `HTTPError`
   (`providers/tracker/jira.py:65-70`) — that is Jira's existing transport-failure idiom and is not
   converted into a `False` return.
7. `cli.py:_tracker_set_status` checks the returned bool: `True` → exit 0 (unchanged). `False` →
   print `ERROR: board move to '<status>' failed for issue <id>` to stderr and `sys.exit(1)`,
   matching the existing `_preflight` convention (`providers/cli.py:127-133`: collect problems,
   print each as `ERROR: ...`, `sys.exit(1)`). Additionally wrap the call so a `RuntimeError`
   raised by `JiraTracker._request` (`jira.py:70`) is caught, printed as `ERROR: {e}`, and also
   exits 1 — never a raw traceback.
8. No change to `entrypoint.sh`. Its `BOARD_MOVE_OK` guard already branches on the CLI process's
   exit code; it becomes live automatically once `cli.py` actually exits non-zero on failure. This
   matches the issue's stated fix direction verbatim. All three `entrypoint.sh` call sites (`:154`,
   `:506`, `:743`) are already guarded; the two unguarded DAG-node callers are handled by
   Requirement 8a.
8a. Append ` || echo "WARNING: board move to <status> failed for #$ISSUE — check board state
   manually"` to the two DAG-node invocations (`workflows/archon-dark-factory.yaml:257` and
   `:1195`) so a board-move failure stays advisory there — a successful merge/push must never be
   reported as a failed run. This is a two-line edit to non-gate status nodes, listed here
   explicitly so the conformance gate does not excise it as out of scope.
9. Out of scope (per Q&A, do not touch): `board.set_board_status()`'s four direct Python callers
   (`breaker.py`, `rescue.py`, `deconflict.py`, `epic_autopilot.py`) do not gain any new
   failure-handling logic — that is a separate, larger behavior change (see Alternatives
   Considered). Also out of scope: every other `cli.py` verb that shares the same
   swallow-failure `subprocess.run(capture_output=True)` pattern (`_tracker_label`,
   `_tracker_comment`, `_tracker_resolve`, `_codehost_*`, etc.) — this issue is scoped to
   board-status specifically; the same pattern elsewhere is a separate, already-implied follow-up.
   The shell callers of the `tracker set-status` verb (Assumptions, last bullet) are likewise not
   touched, except for the two DAG-node guards in Requirement 8a.
10. Tests (TDD, per CLAUDE.md):
    - `tests/test_factory_core_board.py`: existing `find_board_item`/`set_board_status` tests stay
      green unedited (Requirement 2/3). Add coverage for `_item_edit_status` returning `True`/`False`
      matching `gh`'s exit code, and for stderr being printed on failure.
    - `tests/test_factory_core_board.py`: a test for `_find_item_by_number_checked` covering
      `("", False)` on rc≠0, `("", False)` on unparseable JSON, and `("", True)` on an empty items
      list.
    - `tests/test_provider_tracker_parity.py` (GitHubTracker, subprocess-mocked): extend
      `test_set_status_resolves_canonical_and_calls_item_edit` to assert a `True` return on success;
      extend `test_set_status_opaque_id_never_reaches_int` to assert a `False` return; add a new test
      for `gh project item-edit` returning non-zero → `set_status` returns `False` without raising.
    - `tests/test_provider_tracker_jira.py`: extend `test_set_status_finds_transition_and_posts_its_id`
      to assert `True`; extend `test_set_status_missing_transition_edge_fails_soft` to assert `False`.
    - `tests/test_tracker_contract.py`: extend `test_set_status_moves_through_canonical_vocabulary` to
      assert the return is `True`; extend `test_set_status_unknown_item_is_safe_noop` to assert the
      return is `False` (not just "must not raise").
    - `tests/test_provider_cli.py`: add `test_tracker_set_status_exits_0_on_success` and
      `test_tracker_set_status_prints_error_and_exits_1_on_failure`, following the existing
      `test_preflight_ok_prints_ok_and_exits_0` / `test_preflight_failure_prints_every_problem_and_exits_1`
      pattern in the same file.
    - `tests/test_provider_cli.py`: `test_tracker_set_status_catches_runtime_error_and_exits_1` —
      a `RuntimeError` from `set_status` is printed as `ERROR: {e}` and exits 1, never a traceback.

---

## Brainstorming Q&A

> **Q:** Should this ticket also change `board.set_board_status()` (the second, pre-existing
> function called directly and fire-and-forget by `breaker.py`, `rescue.py`, `deconflict.py`, and
> `epic_autopilot.py`) so those callers also see propagated failures, or should the fix be scoped
> narrowly to the `cli.py` → `GitHubTracker.set_status` → `entrypoint.sh` `BOARD_MOVE_OK` path only,
> leaving `board.set_board_status()`'s existing fire-and-forget contract untouched for its four
> direct Python callers (none of which currently handle exceptions and would crash/break if a
> failure now propagated as a raised exception)?
>
> **A:** Scope narrowly to the `cli.py` → `GitHubTracker.set_status` → `BOARD_MOVE_OK` path; do not
> change `board.set_board_status()`'s fire-and-forget contract for its four direct callers. In
> `breaker.trip_to_blocked`, `deconflict._escalate`, and `epic_autopilot.promote_epic`, the board
> move is the first step of a multi-step remediation (labels, comments, child-issue labeling all
> follow it); if a failed board move raised there, the remediation would abort partway and leave the
> ticket in a strictly worse state than a stale board column. `rescue.py` is the same shape. Turning
> those into hard failures is a real behavior change needing its own retry/ordering design — a
> separate ticket. However, the shared private helpers (`_find_item_by_number`, `_item_edit_status`)
> must still distinguish transport failure from "item genuinely absent" internally, and
> `board.set_board_status()` must absorb/discard that signal so its return stays `None` with zero
> behavior change for its 4 callers. Treat `gh` rc != 0 / unparseable JSON as failure; "item
> genuinely not on the board" stays a non-raising no-op at the tracker level (per
> `test_set_status_unknown_item_is_safe_noop`), but `cli.py`'s `_tracker_set_status` should exit
> non-zero for **both** cases — an absent item is itself an anomaly worth surfacing (every issue the
> factory dispatches came *from* the board), and this doesn't violate the "must not raise" contract
> tests, which pin tracker-level behavior, not CLI exit code. This requires widening the `Tracker`
> ABC's `set_status` return annotation from `None` — authorized as required by the issue's stated fix
> direction; call it out in the spec as an intentional ABC touch so the conformance gate doesn't read
> it as scope creep. `JiraTracker` needs the same signal shape for contract parity (the shared test
> runs both trackers), keeping its existing fail-soft-with-stderr "no transition" behavior but now
> also reporting not-moved via the same success signal.

> **Q:** Given authorization to widen the `Tracker.set_status` return type: should the concrete
> mechanism be (a) `set_status` returns `bool` (`True`/`False`, no exception for either
> "not found" or "transport failure" case — `cli.py` checks the bool and exits 1 on `False`,
> printing an `ERROR: ...` line matching the `_preflight` convention), or (b) `set_status` still
> returns `None` but raises a new dedicated exception (e.g. `TrackerOperationError`) specifically for
> transport failures only, which `cli.py` catches and `board.set_board_status()` catches-and-swallows?
> Option (a) is a smaller, more mechanical diff but loses the failure-reason string for the CLI's
> stderr message; option (b) preserves a human-readable reason but adds a new exception class and
> requires `board.set_board_status()` to add a try/except it doesn't have today.
>
> **A:** Option (a) — `set_status` returns `bool`. The sibling `CodeHost` ABC in the same package
> already made this exact decision: `providers/codehost/base.py:30,39` declare
> `update_change_body(...) -> bool` and `merge_change(...) -> bool`, and
> `providers/codehost/github.py` implements both as `r = subprocess.run(...); return
> r.returncode == 0` — the established house idiom for a subprocess-backed provider mutation, in the
> directory next door on the same ABC pattern. Option (a) also gives `board.set_board_status()`'s
> four callers a genuinely zero-diff outcome: with a bool, "absorb the signal" means simply not using
> the return value, versus option (b) forcing a new `try/except TrackerOperationError: pass` into
> `board.set_board_status()` — a bare-swallow block whose only purpose is undoing the propagation just
> added, exactly the kind of construct a future Gate 3 review would flag. The "loses the reason
> string" objection is addressable without a new exception class: put the human-readable reason on
> stderr at the layer that has it (`board._item_edit_status` prints captured `gh` stderr on failure;
> `JiraTracker.set_status` already prints its "no transition" reason today at
> `providers/tracker/jira.py:161-164` — established precedent inside `Tracker` itself), and let the
> bool carry only the branch signal. `cli.py:_tracker_set_status` becomes: `if not
> get_tracker().set_status(...): print(f"ERROR: ...", file=sys.stderr); sys.exit(1)`. Do not add a
> `TrackerOperationError` class. Separately, `JiraTracker._request` already raises `RuntimeError` on
> `HTTPError` for genuine transport failures — `cli.py` should catch that `RuntimeError` too (print
> `ERROR: {e}`, exit 1) so a Jira transport failure doesn't surface as a raw traceback; this reuses
> the existing `RuntimeError` type rather than introducing a new one. Note for the conformance gate:
> `tracker set-status` becomes the first `tracker` CLI subcommand to exit non-zero on a provider
> result rather than `_print(...)`-ing it — deliberate, since `entrypoint.sh:506` and `:743` branch
> on exit code with stderr discarded (`2>/dev/null`, added by #292 / cf4dd59); keeping the bool off
> stdout avoids a stray `False`/`True` in the run log. Consequence: the `gh` stderr printed by
> `_item_edit_status` and `cli.py`'s `ERROR` line are visible only at `:154` and in the DAG nodes; on
> the `:506` Blocked path the failure is surfaced solely through `BOARD_NOTE`. Accepted for this
> ticket (no `entrypoint.sh` change, per the issue); dropping the `2>/dev/null` at `:506` is a
> follow-up.

---

## Architecture / Approach

**`scripts/factory_core/board.py`**

- `_item_edit_status(item_id: str, option_id: str) -> bool`: switch `subprocess.run(...,
  capture_output=True)` to `capture_output=True, text=True`; return `r.returncode == 0`; on failure
  print `f"board: item-edit failed for {item_id}: {r.stderr.strip()}"` to stderr.
- `_find_item_by_number` keeps its current signature/behavior (`-> str`, `""` on both transport
  failure and genuine absence) for its existing callers (`find_board_item`,
  `set_board_status`). Add a new private helper, e.g. `_find_item_by_number_checked(number: str) ->
  tuple[str, bool]` returning `(item_id_or_"", lookup_ok)`, where `lookup_ok` is `False` only when
  `gh project item-list` itself failed (non-zero rc or unparseable JSON) — `True` (with `item_id ==
  ""`) when the call succeeded but no matching item was found. `_find_item_by_number` becomes a thin
  wrapper: `return _find_item_by_number_checked(number)[0]`, so both existing behavior and the new
  checked variant share one implementation (no duplicated `gh` invocation).
- `set_board_status(issue_num, option_id) -> None`: unchanged call shape — uses
  `_find_item_by_number` (the unchecked wrapper) and `_item_edit_status`, ignoring the latter's now-`bool`
  return exactly as it ignored its previous `None` return. No new try/except needed since nothing it
  calls raises.

**`scripts/factory_core/providers/tracker/base.py`**

- `set_status(self, id: str, canonical: str) -> bool` (was `-> None`). Update the abstract method's
  docstring to state the contract: `True` iff the item's status actually changed; `False` for "not
  found" or "operation failed" — implementations must not raise for either case.

**`scripts/factory_core/providers/tracker/github.py`**

```python
def set_status(self, id: str, canonical: str) -> bool:
    item_id, lookup_ok = board._find_item_by_number_checked(id)
    if not lookup_ok or not item_id:
        return False
    return board._item_edit_status(item_id, identity.STATUS[canonical])
```

**`scripts/factory_core/providers/tracker/jira.py`**

- `set_status` returns `True` after issuing the transition POST; returns `False` (keeping its
  existing stderr message, unchanged wording) when no matching transition is found. The
  `_request`-raised `RuntimeError` path is untouched — a real Jira API failure still propagates as
  an exception, per Requirement 6.

**`scripts/factory_core/providers/cli.py`**

```python
def _tracker_set_status(args):
    try:
        ok = get_tracker().set_status(args.id, args.status)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    if not ok:
        print(f"ERROR: board move to {args.status!r} failed for issue {args.id}", file=sys.stderr)
        sys.exit(1)
```

**`entrypoint.sh`**: no change. `set_board_status()` (its bash wrapper) and the `BOARD_MOVE_OK`
guard at `:506` already branch on this process's exit code.

**`workflows/archon-dark-factory.yaml`** (`:257`, `:1195`): append the advisory
`|| echo "WARNING: ..."` guard per Requirement 8a — two lines, no other DAG change.

---

## Alternatives Considered

1. **Propagate failure through `board.set_board_status()`'s four direct callers too** (making
   `breaker.py`, `rescue.py`, `deconflict.py`, `epic_autopilot.py` branch on success/failure).
   Rejected per Q&A: none of these currently handle exceptions or a failure signal, and the board
   move is the first step of a multi-step remediation in three of the four — a failure there today
   silently continues to the labels/comments that make the ticket state visible to a human. Adding
   failure handling would be a genuine behavior/ordering change, not this issue's scope, and belongs
   in its own ticket if wanted.
2. **Signal failure via a new dedicated exception (`TrackerOperationError`)** instead of a `bool`
   return. Rejected per Q&A: the sibling `CodeHost` ABC already establishes `-> bool` as this
   codebase's idiom for subprocess-backed provider mutations; a new exception type would also force
   `board.set_board_status()` to grow a swallow-all `try/except` it doesn't need today, purely to
   undo the propagation just introduced.
3. **Fix all `cli.py` verbs sharing the same swallow-failure `subprocess.run` pattern** (labels,
   comments, resolve, codehost mutations) in this ticket, since they have the identical bug shape.
   Rejected: the issue's scope note explicitly limits this to board-status; CLAUDE.md's scope
   discipline says touch only what the plan/spec lists. Filed as a natural, separately-scoped
   follow-up (see Open Questions).

---

## Open Questions (Non-blocking)

- Whether the same silent-failure pattern in other `cli.py` verbs (`_tracker_label`,
  `_tracker_comment`, `_tracker_resolve`, the `_codehost_*` mutation verbs) warrants its own
  follow-up ticket. Not blocking this fix; flagged for a future issue since the pattern is
  structurally identical.

---

## Assumptions

- "Callers can branch on it" (the issue's stated fix direction) means the `cli.py` process exit
  code, which is exactly what `entrypoint.sh`'s existing `BOARD_MOVE_OK` guard already reads — no
  new IPC/output channel is needed.
- `gh project item-edit`'s stderr is safe to print to the container's stderr (no secrets expected in
  a `gh` CLI error message for this call shape) — consistent with how `JiraTracker.set_status`
  already prints its own diagnostic message today.
- The only Python caller of `Tracker.set_status` outside the trackers themselves is
  `cli.py:_tracker_set_status` (`JiraTracker.resolve_item` at `jira.py:210` calls `self.set_status`
  and ignores the return — unchanged). The CLI verb itself, however, has six shell callers whose
  exit-code handling changes: `entrypoint.sh:154` (`|| echo WARNING`), `:506`
  (`|| BOARD_MOVE_OK=false`), `:743` (`|| true`) — all already guarded;
  `workflows/archon-dark-factory.yaml:257` (close node, after PR merge) and `:1195`
  (status-in-review node) — NOT guarded (see Requirement 8a); and
  `commands/dark-factory-code-review.md:173`, `dark-factory-conformance.md:522`,
  `dark-factory-validate.md:97` — agent-executed, where a non-zero exit is reported to the agent,
  not fatal.
