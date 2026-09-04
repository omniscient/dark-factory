# Propagate add_label/remove_label failure through the Tracker provider, cli.py, and the gate DAG nodes

**Issue:** omniscient/dark-factory#358
**Status:** new spec, first refinement pass.
**Parent context:** exact sibling of #335 (`set_status`, shipped as PR #352) in the functions #335's
own Open Questions explicitly scoped out. See
`docs/archive/2026-08-22-board-status-failure-propagation-design.md` for the precedent this spec
mirrors throughout.

---

## Overview / Problem Statement

`GitHubTracker.add_label`/`remove_label` (`scripts/factory_core/providers/tracker/github.py:152-162`)
run `gh issue edit --add-label`/`--remove-label` with `capture_output=True` and never inspect the
return code. `cli.py:_tracker_label` (`providers/cli.py:58-63`) calls them and propagates nothing —
the CLI process always exits 0. The two DAG nodes that apply the gate labels,
`refine-push` and `plan-push-and-advance` (`workflows/archon-dark-factory.yaml:427-517`), call
`tracker label --add ...` unconditionally and then unconditionally print
`"... (spec-pending-review gate applied)"` / `"... (plan-pending-review gate applied)"` — regardless
of whether the label actually reached GitHub.

Consequence, observed live 2026-08-28: the #342 and #334 plan runs pushed their plans and printed
the "gate applied" echo while GitHub's API pool was rate-exhausted; no label reached GitHub. The
scheduler read "Refined status + no `plan-pending-review` label" as "not yet gated" and re-dispatched
both plans in a loop, stopped only by an operator hand-labeling the issues. The same failure recurred
live on #341 at 01:41Z under the same exhaustion condition.

This spec makes that failure signal reach both `cli.py`'s exit code (mirroring #352's `set_status`
fix mechanically) and the two DAG nodes that call it (a new decision this spec makes, since
`set_status` had no equivalent DAG-node consumer to update — `entrypoint.sh`'s `BOARD_MOVE_OK` guard
already existed and needed no edit).

---

## Requirements

Distilled from the issue's stated fix direction and the Q&A below:

1. `GitHubTracker.add_label`/`remove_label` return `bool` (`True` iff `gh issue edit` exited 0),
   instead of `None`. On failure, print the captured `gh` stderr to stderr (mirroring
   `board._item_edit_status`'s `f"board: item-edit failed for {item_id}: {r.stderr.strip()}"`
   convention) — requires switching both calls to `capture_output=True, text=True`.
2. `Tracker.add_label`/`remove_label` (`providers/tracker/base.py:41-47`) — the ABC's declared
   return type widens from `None` to `bool`, with the abstract method docstring stating the
   contract: `True` iff the `gh`/API call succeeded; `False` on failure, no raise.
3. `JiraTracker.add_label`/`remove_label` (`providers/tracker/jira.py:174-186`) implement the same
   `bool` contract for parity, since `tests/test_tracker_contract.py` parametrizes over both
   trackers. Both already call `self._request(...)`, which raises `RuntimeError` on an HTTP error —
   that existing transport-failure idiom is untouched (same treatment as `JiraTracker.set_status` in
   #352); the methods simply `return True` after the `_request` call succeeds, since there is no
   Jira-side "soft failure" case analogous to `set_status`'s "no matching transition."
4. `cli.py:_tracker_label` (`providers/cli.py:58-63`) checks each `add_label`/`remove_label` call's
   return: if any operation returns `False`, print
   `ERROR: one or more label operations failed for issue <id>` to stderr and `sys.exit(1)` after
   attempting every requested add/remove (do not short-circuit on the first failure — a caller that
   passes both `--add` and `--remove` in one invocation wants both attempted). Wrap the whole loop so
   a `RuntimeError` from `JiraTracker` is caught, printed as `ERROR: {e}`, and also exits 1 — same
   convention as `_tracker_set_status` (#352 Requirement 7).
5. `breaker.py:341` (`tracker.add_label` inside `trip_to_blocked`) and `epic_autopilot.py:530,547`
   (`get_tracker().add_label(...)` inside `advance`/`promote_epic`) keep their exact current
   fire-and-forget contract — they call `add_label` for its side effect and never inspect a return
   value today; the widened `bool` return is simply unused by them, zero behavior change. This
   mirrors #352 Requirement 3's treatment of `board.set_board_status()`'s four direct callers.
6. `workflows/archon-dark-factory.yaml`'s `refine-push` and `plan-push-and-advance` nodes: the
   `tracker label --add <gate-label>` call becomes an `if`/`else` (matching the existing
   `set-status` warn-advisory pattern already used elsewhere in this same file, e.g.
   `status-in-review`'s
   `if python3 ... tracker set-status ...; then echo "Moved ..."; else echo "WARNING: ... — check
   board state manually"; fi`):
   - On success: unchanged echo, `"Pushed $BRANCH for issue #$ISSUE (<label> gate applied)"`.
   - On failure: print `"WARNING: <label> failed to apply for #$ISSUE — <spec|plan> pushed to
     $BRANCH but gate label missing; check board state manually"` to stdout, **and** upsert a new
     durable marker comment on the issue (`<!-- df-gate-label-failure -->`, distinct from the
     existing `<!-- df-refine-failure -->` marker — that marker means "no artifact was produced,
     retry is safe," and must not be overloaded to also mean "artifact exists, only the label
     failed") stating the branch the spec/plan was actually pushed to and the literal `gh issue
     edit <id> --add-label <label>` remediation command. The comment-post call is `|| true`-guarded
     so a best-effort comment (itself possibly hitting the same exhausted API) can never fail an
     otherwise-successful push node.
   - The node does **not** `exit 1` on a label-application failure in either branch — the push
     already succeeded and is the load-bearing side effect; failing the node here would be the
     "loudly strand it" outcome the issue explicitly forbids. This is a deliberate divergence from
     `_tracker_set_status`'s CLI-level `exit 1` (Requirement 4): the *CLI* must report failure
     truthfully so a caller who checks the exit code can see it, but *these two DAG-node callers*
     choose to catch that failure and degrade to warn-advisory rather than propagate it into DAG
     node failure. (`_tracker_set_status`'s callers — e.g. `status-in-review`, the `close` node —
     already establish this same "CLI exits non-zero, DAG node stays warn-advisory" split for board
     moves; this requirement applies the identical split to labels.)
7. `board.post_or_update_comment`/`Tracker.upsert_comment` (the issue's aside — "`upsert_comment`
   deserves the same audit") is explicitly **out of scope** for this ticket. See Open Questions.
8. Tests (TDD, per CLAUDE.md):
   - `tests/test_provider_tracker_parity.py`: extend `test_add_label_matches_breaker_trip_to_blocked`
     and `test_remove_label_matches_scheduler_advance_path` to assert a `True` return on success; add
     `test_add_label_returns_false_and_prints_stderr_on_gh_failure` and
     `test_remove_label_returns_false_and_prints_stderr_on_gh_failure` (non-zero `gh` exit →
     `False`, stderr captured and printed, no raise).
   - `tests/test_provider_tracker_jira.py`: extend `test_add_label_reads_then_puts_merged_labels` and
     `test_remove_label_reads_then_puts_without_it` to assert a `True` return.
   - `tests/test_tracker_contract.py`: extend `test_label_add_and_remove_round_trip` to assert both
     calls return `True` against the fake controller (parametrized over both trackers).
   - `tests/test_provider_cli.py`: add `test_tracker_label_exits_0_on_success`,
     `test_tracker_label_prints_error_and_exits_1_on_any_failure` (one of several add/remove calls
     fails → still attempts the rest, then exits 1), and
     `test_tracker_label_catches_runtime_error_and_exits_1` — following the existing
     `test_tracker_set_status_*` trio in the same file.
   - `tests/test_push_gate_dag.py`: add a parametrized test (over both `node_id`s, reusing the
     existing `@pytest.mark.parametrize` fixture) asserting the label call is now guarded by an
     `if`/`else` (e.g. `f"if python3 ... tracker label --id \"$ISSUE\" --add {label}"` appears in the
     node body), that `df-gate-label-failure` appears distinct from `df-refine-failure`, and that the
     warning branch is `|| true`-guarded. Existing assertions in this file
     (`test_node_gates_push_and_label_behind_artifact_check`'s `bash.find(f"--add {label}")`, etc.)
     must stay green unedited — the `--add <label>` substring position relative to `push_gate_check.sh`
     and `git push` is unchanged, only wrapped in a conditional.

---

## Brainstorming Q&A

> **Q:** The issue's Root Cause section adds an aside — "`upsert_comment` deserves the same audit" —
> without stating whether fixing `upsert_comment`'s swallow-failure bug is in scope for this ticket
> or a flagged follow-up. The issue title names only `add_label`/`remove_label`; #335's own Open
> Questions already listed `_tracker_label`, `_tracker_comment` (i.e. `upsert_comment`),
> `_tracker_resolve`, and the `_codehost_*` verbs as separate, individually-deferred follow-ups, and
> its Alternatives Considered explicitly rejected bundling multiple verb fixes into one ticket. Given
> that `upsert_comment` also has a materially different failure shape than a single `gh issue edit`
> call — it delegates to `board.post_or_update_comment`, a three-step GET-then-create-or-PATCH
> sequence whose GET failure today silently degrades to `comment_id = ""`, i.e. a rate-exhausted
> lookup causes a **duplicate** comment to be posted instead of the existing one being updated,
> a distinct hazard from a missing label — should this ticket fix `add_label`/`remove_label` only, or
> also fix `upsert_comment`?
>
> **A:** Fix `add_label`/`remove_label` only. Record the `upsert_comment`/`post_or_update_comment`
> swallow-failure bug — including the swallowed-GET duplicate-comment hazard specifically — as an
> explicitly out-of-scope follow-up (see Open Questions), to be filed as its own ticket. The title,
> the root-cause line citations, and the reproduction are all about gate labels; the `upsert_comment`
> aside reads as a flagged note, not a requirement. This also keeps faith with #335's own precedent
> of deferring these verbs individually rather than bundling them, and with CLAUDE.md's scope
> discipline ("touch only what the plan lists").

> **Q:** The Fix section says the DAG-node callers should "make the success echo conditional and
> warn-advisory on failure so a label miss cannot fail an otherwise-successful run silently OR
> loudly strand it." Two mechanics need pinning down: (1) given container stdout/stderr is not
> monitored live in a headless run (CLAUDE.md), should the warning be a log-level echo only, or also
> a durable, human-visible marker comment on the issue (as the existing "no committed artifact"
> failure branch in the same node already does)? (2) Given GH rate exhaustion is often transient,
> should the DAG node retry the label call a few times before giving up, or is a single attempt with
> a truthful warning sufficient, leaving recovery to a human or a future reconciliation mechanism?
>
> **A:** (1) Both — a log echo (matching the existing `set-status` warn-advisory precedent already
> used elsewhere in this file, e.g. `status-in-review`'s
> `if ...; then echo "Moved ..."; else echo "WARNING: ... — check board state manually"; fi`) **and**
> a durable marker comment via the same `tracker comment --marker` upsert primitive the node already
> uses for its other failure branch. A missed gate label is worse than a stale board column: per the
> incident it drives the scheduler into a re-dispatch loop, and nobody reads container logs on a
> headless run. Use a marker distinct from `<!-- df-refine-failure -->` (that marker specifically
> means "no artifact was produced, retry is safe" and must not be overloaded), stating the branch the
> artifact was actually pushed to and the literal `gh issue edit` remediation command. Guard the
> comment call with `|| true` so a best-effort comment can never itself fail the node.
> (2) No retry in this ticket — single attempt. Both nodes carry `timeout: 30000`; a retry loop long
> enough to outlast real exhaustion (the incident spanned minutes across three runs) would blow the
> node timeout and convert a label miss into the exact "loudly strand it" outcome being fixed, while
> a retry short enough to fit the timeout wouldn't address the actual failure mode. No shared
> `gh`-call backoff helper exists yet to hang a retry on (checked: `session_window.py`'s
> retry/backoff logic is for a different concern — Claude session-window limits, not `gh` CLI rate
> limits). Real recovery belongs on the scheduler side — reconciling "Refined + committed spec/plan
> on branch + missing gate label" by re-applying the label rather than re-dispatching the phase — and
> is out of scope here (see Open Questions).

---

## Architecture / Approach

**`scripts/factory_core/providers/tracker/github.py`**

```python
def add_label(self, id: str, name: str) -> bool:
    r = subprocess.run(
        ["gh", "issue", "edit", id, "--repo", identity.SLUG, "--add-label", name],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"github: add-label {name!r} failed for #{id}: {r.stderr.strip()}", file=sys.stderr)
    return r.returncode == 0

def remove_label(self, id: str, name: str) -> bool:
    r = subprocess.run(
        ["gh", "issue", "edit", id, "--repo", identity.SLUG, "--remove-label", name],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"github: remove-label {name!r} failed for #{id}: {r.stderr.strip()}", file=sys.stderr)
    return r.returncode == 0
```

**`scripts/factory_core/providers/tracker/base.py`**

- `add_label`/`remove_label` signatures widen to `-> bool`; docstring states the contract (`True`
  iff the operation succeeded; `False` on failure, never raises for a transport failure at this
  layer — Jira's `RuntimeError` remains the one documented exception, per `set_status`'s existing
  docstring precedent for the same distinction).

**`scripts/factory_core/providers/tracker/jira.py`**

- `add_label`/`remove_label` each gain a trailing `return True` after their existing `_request(...)`
  call; no other change. A transport failure still propagates via `_request`'s `RuntimeError`.

**`scripts/factory_core/providers/cli.py`**

```python
def _tracker_label(args):
    tracker = get_tracker()
    ok = True
    try:
        for name in (args.add or []):
            if not tracker.add_label(args.id, name):
                ok = False
        for name in (args.remove or []):
            if not tracker.remove_label(args.id, name):
                ok = False
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    if not ok:
        print(f"ERROR: one or more label operations failed for issue {args.id}", file=sys.stderr)
        sys.exit(1)
```

**`scripts/factory_core/breaker.py`, `scripts/factory_core/epic_autopilot.py`**

- No change. `tracker.add_label(...)` calls stay fire-and-forget; the widened `bool` return is
  simply not consumed.

**`workflows/archon-dark-factory.yaml`** (`refine-push`, `plan-push-and-advance`)

```bash
if [ -n "$SPEC_FILE" ]; then
  git push -u origin "$BRANCH"
  if python3 "$_PCLI" tracker label --id "$ISSUE" --add spec-pending-review; then
    echo "Pushed $BRANCH for issue #$ISSUE (spec-pending-review gate applied)"
  else
    echo "WARNING: spec-pending-review failed to apply for #$ISSUE — spec pushed to $BRANCH but gate label missing; check board state manually"
    _FOOTER=$(python3 "$_PCLI_FACTORY_CORE" marker refinement 2>/dev/null || echo "")
    _WARN_BODY="<!-- df-gate-label-failure -->
## Refinement Pipeline — Gate Label Missing

The spec was pushed to \`$BRANCH\` but the \`spec-pending-review\` label failed to apply (likely a
transient GitHub API/rate-limit failure). This issue will not auto-advance until the label is
applied.

**Remediation:** \`gh issue edit $ISSUE --add-label spec-pending-review\`

---
${_FOOTER}"
    TMPFILE=$(mktemp /tmp/gate-label-failure-XXXXXX.md)
    printf '%s' "$_WARN_BODY" > "$TMPFILE"
    python3 "$_PCLI" tracker comment --id "$ISSUE" --marker "<!-- df-gate-label-failure -->" --body-file "$TMPFILE" || true
    rm -f "$TMPFILE"
  fi
else
  ... # unchanged
fi
```

`plan-push-and-advance` gets the identical shape with `plan-pending-review` / `docs/superpowers/plans/`
/ "plan" substituted, matching its existing sibling structure line-for-line.

---

## Alternatives Considered

1. **Also fix `upsert_comment`/`post_or_update_comment` in this ticket.** Rejected per Q&A — the
   issue's title, root-cause citations, and reproduction are scoped to labels; `upsert_comment` has
   a materially different, multi-step failure shape (including a distinct duplicate-comment hazard)
   that deserves its own design pass, not a copy-paste of this mechanical `bool`-return fix. Matches
   #335's own precedent of deferring each swallow-failure verb individually.
2. **Retry the label call inside the DAG node before falling back to warn-advisory.** Rejected per
   Q&A — the 30s node timeout can't accommodate a retry loop long enough to outlast the observed
   multi-minute exhaustion window without itself becoming the "loudly strand it" failure mode; a
   shorter retry wouldn't address the real failure. No shared backoff helper exists to hang this on
   today. Recovery belongs in a scheduler-side reconciliation follow-up instead.
3. **Have the DAG node `exit 1` on a label-application failure** (matching `_tracker_set_status`'s
   CLI-level `sys.exit(1)` one-for-one at the node level too). Rejected — the push already
   succeeded and is the node's load-bearing side effect; failing the whole node here would strand
   the run without recovering anything, which is exactly the outcome the issue's Fix section rules
   out ("...cannot fail an otherwise-successful run silently OR loudly strand it"). The CLI itself
   still exits non-zero (Requirement 4) so any caller that *does* want to treat a label miss as fatal
   can; these two specific DAG-node callers choose not to, the same "CLI-signals, caller-decides"
   split `_tracker_set_status`'s existing DAG-node callers (`status-in-review`, `close`) already
   establish for board moves.

---

## Open Questions (Non-blocking)

- Whether `board.post_or_update_comment` / `Tracker.upsert_comment` warrants its own follow-up
  ticket for the same swallow-failure audit — yes, and additionally its GET-failure path
  (`board.py:87`, `comment_id = r.stdout.strip() if r.returncode == 0 else ""`) causes a
  rate-exhausted lookup to silently post a **duplicate** comment instead of updating the existing
  one, a distinct hazard worth calling out explicitly in that future ticket's issue body.
- Whether the scheduler should gain a reconciliation check — "Refined/Planned status + a committed
  spec/plan already on the issue's branch + the corresponding gate label absent" — that re-applies
  the missing label on a later poll instead of relying solely on this ticket's warn-advisory comment
  for a human to notice and remediate. This is the more durable fix for the dispatch-loop failure
  mode the issue describes, but is a `scheduler.sh` behavior change and belongs in its own ticket.
- Whether the remaining swallow-failure `cli.py` verbs (`_tracker_resolve`, `_codehost_*` mutation
  verbs) still warrant the same audit — carried over unchanged from #335's original Open Questions,
  still unaddressed.

---

## Assumptions

- No other production code path calls `get_tracker().add_label(...)`/`remove_label(...)` directly
  other than `breaker.py:341`, `epic_autopilot.py:530,547`, and `cli.py:_tracker_label` (verified via
  repo-wide grep) — so widening the return type has no blast radius beyond the tests listed in
  Requirement 8 and the two DAG nodes in Requirement 6.
- `gh issue edit`'s stderr is safe to print to the container's stderr (no secrets expected in a `gh`
  CLI error message for this call shape) — same assumption #352 already made for `board._item_edit_status`.
- The workflow DAG validator (`scripts/check_workflow_dag.py`, exercised by
  `tests/test_push_gate_dag.py::test_dag_validator_passes`) does not constrain `bash:` block content
  beyond YAML well-formedness, so wrapping the label call in an `if`/`else` and adding a second
  `tracker comment` call needs no validator change.
