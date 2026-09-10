# Close the board write path and detect off-board `ready-for-agent` issues

**Operator spec gate:** 2026-09-10 — approved with amendments. Two blocking defects, both of
which are this ticket's own bug class reappearing inside the fix: a `set -e` interaction that
would kill the poll loop instead of reporting, and an unpaginated `gh issue list` that would
silently under-report. Details in the disposition comment.

**Issue:** #418

## Overview / Problem statement

`scripts/factory_core/board.py` can look up a project item (`gh project item-list`) and move
its status (`gh project item-edit`), but it has no `add` path. None of the three factory-side
`gh issue create` call sites — `commands/dark-factory-conformance.md` (scope-spillover
tickets), and `commands/ceiling-revisit.md` (the XL-ceiling-revisit ticket and the
unconditional next-weekly-revisit ticket) — adds the freshly filed issue to the GitHub
Project board. `scheduler.sh`'s poll loop dispatches strictly off the board
(`fetch_board_items`/`get_items_by_status`), never off the raw issue list, so a filed issue
that never lands on the board is permanently invisible to dispatch regardless of its labels.

Whatever previously kept the board populated for issues up to #403 (most likely a GitHub
Projects built-in auto-add workflow) stopped silently. Every issue filed since — #404 through
#416, ten open issues including one carrying `ready-for-agent` (#415) — sat off-board for
~15 hours while the scheduler logged `skip=nothing_to_do` once a minute. `nothing_to_do` and
`queue_unreachable` produce an identical log line today; an operator (or the scheduler's own
health tooling) cannot tell a genuinely empty queue from a broken write path by reading the
log.

This is the same failure class as #395 (silent `runs.jsonl` append failure) and prior
operator-monitor incidents recorded in `.archon/memory/`: a mechanism became unavailable and
the system kept reporting the happy path. This spec closes both the write-path gap (issues
never reaching the board) and the read-path blind spot (the scheduler being unable to tell the
two idle states apart).

## Requirements (from Q&A)

Full brainstorming dialogue is in the refinement pipeline comment on #418. Load-bearing
resolutions:

1. **`board.py`'s new `add()` wraps `gh project item-add`, not a raw `addProjectV2ItemById`
   GraphQL mutation.** This matches the file's existing convention (`item-list`, `item-edit`
   are both `gh project` subcommand wraps, never raw `gh api graphql` for project-item CRUD)
   and has direct precedent in this repo:
   `docs/archive/2026-08-28-dispatch-ceiling-weekly-revisit-plan.md:756-757` already uses
   `gh project item-add <N> --owner <O> --url "$URL" --format json --jq .id` piped into
   `gh project item-edit`. It also avoids an extra round trip to resolve the issue's GraphQL
   node id (raw `addProjectV2ItemById` needs it; `item-add` takes the issue URL directly, and
   all three call sites already have that URL from `gh issue create`'s own stdout) — one fewer
   GraphQL call against a governed rate-limit budget (`config/config.yaml: rate_limit_floor`).
2. **`board.add()` is wired into all three `gh issue create` sites**, not just the one the
   Acceptance section names explicitly (conformance spillover). The Acceptance bar is a floor,
   not a fence — the issue's Fix section states "all three" as the intent, and closing only one
   site leaves two real, operator-facing ticket-filing paths with the identical bug. This
   matters more than it looks: neither `ceiling-revisit.md` ticket carries `ready-for-agent`
   (they're labeled `enhancement`/`priority: should-have`), so Part 2's detector below — which
   only watches `ready-for-agent` issues — provides **no backstop** for those two sites. Leaving
   them unwired would mean they're protected by neither half of the fix. The marginal cost is
   one `URL=$(gh issue create ...)` capture (conformance already captures it) plus one
   `board.add()` call at each site.
3. **Only `commands/ceiling-revisit.md` needs editing**, not `.archon/commands/ceiling-revisit.md`.
   The latter is an untracked runtime copy (`entrypoint.sh:675-678` copies
   `/opt/dark-factory/commands` into `$CLONE_DIR/.archon/commands` only when absent, and
   git-excludes the directory) — editing it would have no lasting effect.
4. **A failed `board.add()` call at any of the three sites is non-fatal to the phase it runs
   in.** All three command files already have a graceful-degradation posture for board/tracker
   operations (e.g. `ceiling-revisit.md`'s `XL_ACTION="skip-lookup-failed"` path). Print a
   stderr warning naming the issue number and continue; the created issue still exists (a human
   or Part 2's detector is the backstop), and aborting the phase over a board-hygiene failure
   would be a worse outcome than an off-board ticket.
5. **The scheduler's off-board check (Part 2) runs only on the idle branch** — immediately
   before the `skip=nothing_to_do` log line (`scheduler.sh:1616`) — not on every poll cycle. A
   cycle that dispatched work has already proven the board is reachable; the other early-exit
   log lines in the loop (`error=gh_api_failed`, `skip=factory_at_capacity`) already name a real
   cause and are left untouched. This matches the issue's own framing ("one extra
   `gh issue list` **per idle poll**").
6. **"Off-board" means absent from the full board snapshot, any status** — not a specific
   status subset. `fetch_board_items` already returns every status per cycle (Backlog, Ready,
   In progress, In review, Blocked, Done); comparing against its full number set (no second
   board fetch needed) is both the literal bug scenario (never added at all) and avoids false
   alarms for an issue that's legitimately `Done`/`Blocked`/etc. Compare against **open**
   `ready-for-agent` issues only (`gh issue list --state open --label ready-for-agent`); do not
   additionally filter by `needs-discussion` or other skip labels — the check reports a
   board-hygiene defect, not current dispatchability, and an issue that's off-board is broken
   whether or not something else currently also halts it.
7. **If the `gh issue list` lookup itself fails, do not silently fall back to a bare
   `skip=nothing_to_do`.** That would reproduce the exact bug class this ticket fixes. Emit
   `skip=nothing_to_do off_board_check=failed` instead, so a broken detector is itself visible.
8. **Cap the `issues=` list in the log line** (first ~10 numbers, then `+N more`) so a large
   repair backlog can't produce an unbounded log line.
9. **The detector function lives in `scheduler.sh`, next to `fetch_board_items`/
   `get_items_by_status`, not in `scripts/scheduler_lib.sh`.** `scheduler_lib.sh`'s own header
   states its contract explicitly: "pure, side-effect-free item-blob predicates only... Do NOT
   add dispatch()/set_board_status()/gh-mutating logic here." A helper that shells out to
   `gh issue list` and operates on the whole board snapshot (not a single item blob) fails both
   halves of that contract — confirmed by the design that created the split
   (`docs/archive/2026-07-23-scheduler-internal-seams-design.md`, R1: functions were admitted to
   `scheduler_lib.sh` specifically because they were confirmed to have no `gh` calls). This
   doesn't sacrifice testability: `tests/test_scheduler_pagination.sh` already unit-tests
   `fetch_board_items` itself via `SCHEDULER_SOURCE_ONLY=1 source scheduler.sh` plus an exported
   `gh()` stub; the new test follows the same pattern.
10. Neither half touches `gate_*`, the breaker, budgets, or `deploy/**` (Acceptance).

## Architecture / Approach

### Part 1 — `board.py add()` (write path)

New function in `scripts/factory_core/board.py`, alongside `find_board_item`/
`set_board_status`:

```python
def add_to_board(issue_num: int, issue_url: str) -> bool:
    r = subprocess.run(
        ["gh", "project", "item-add", str(PROJECT_NUMBER),
         "--owner", OWNER, "--url", issue_url, "--format", "json"],
        capture_output=True, text=True,
    )
    item_id = ""
    if r.returncode == 0:
        try:
            item_id = json.loads(r.stdout).get("id", "")
        except json.JSONDecodeError:
            item_id = ""
    if not item_id:
        print(f"board: item-add failed for #{issue_num}: {r.stderr.strip()}", file=sys.stderr)
        # item-add is idempotent (re-adding an already-present issue returns the
        # existing item rather than erroring), but don't trust a missing/unparseable
        # .id blindly -- fall back to a direct lookup before giving up.
        item_id, lookup_ok = _find_item_by_number_checked(str(issue_num))
        if not lookup_ok or not item_id:
            return False
    return _item_edit_status(item_id, STATUS_BACKLOG)
```

CLI exposure — new `board-add` verb in `scripts/factory_core/cli.py`, mirroring the existing
`board-move` verb:

```python
def _board_add(args):
    from factory_core.board import add_to_board
    ok = add_to_board(args.issue, args.url)
    if not ok:
        sys.exit(1)

...
ba = sub.add_parser("board-add")
ba.add_argument("--issue", type=int, required=True)
ba.add_argument("--url", required=True)
ba.set_defaults(func=_board_add)
```

### Part 2 — the three call sites (write-path integration)

**`commands/dark-factory-conformance.md`** (Step C, `create` action, around line 398-405):
after `SPILLOVER_URL=$(gh issue create ...)` and `SPILLOVER_NUM=$(basename "$SPILLOVER_URL")`,
add:

```bash
# TARGET-PATH
python3 dark-factory/scripts/factory_core/cli.py board-add \
  --issue "$SPILLOVER_NUM" --url "$SPILLOVER_URL" \
  || echo "scope-enforcement: WARNING board-add failed for spillover #${SPILLOVER_NUM}" >&2
```

(The `TARGET-PATH` marker moves to its own line: a `\` followed by whitespace and a comment does
**not** continue the line, so the original form would have broken the command at its first
argument.)

**`commands/ceiling-revisit.md`** (Phase 4, the `XL_ACTION="file"` branch, ~line 158, and
Phase 5's unconditional next-revisit issue, ~line 201): capture the `gh issue create` URL into
a variable (Phase 5 currently discards it) and add the same `board-add` call with a
site-appropriate warning message. Both sites keep their current output/comment behavior
unchanged; `board-add` is purely additive after the `gh issue create` call.

### Part 3 — scheduler self-check (read path)

New function in `scheduler.sh`, under the existing `# --- Board state ---` header, next to
`fetch_board_items`:

```bash
# Detects issues that carry ready-for-agent but never reached the board (this
# ticket's root cause, or any future write-path gap). Prints a comma-joined,
# '#'-prefixed, capped-at-10 issue list (empty string = none found); returns
# non-zero if the gh issue list lookup itself failed, so the caller can tell
# "queue is healthy" apart from "couldn't check."
off_board_ready_issues() {
  local board_items="$1" ready_json
  # --limit is REQUIRED: `gh issue list` defaults to 30 results and truncates SILENTLY.
  # A detector for a silently-truncated queue that is itself silently truncated is this
  # ticket's own bug class (operator gate; the same defect bit the operator's own monitor
  # earlier the same day with `--limit 40`). 200 is well clear of any plausible
  # ready-for-agent population; the guard below turns a future overflow into a loud
  # failure rather than an under-report.
  ready_json=$(gh issue list --repo "$FACTORY_REPO_SLUG" --state open \
    --label ready-for-agent --limit 200 --json number 2>/dev/null) || return 1
  echo "$ready_json" | jq -e 'type == "array"' >/dev/null 2>&1 || return 1
  # A full page means the cap may have truncated the result: report "cannot check"
  # rather than an under-count, per Requirement 7's own principle.
  [ "$(echo "$ready_json" | jq 'length')" -ge 200 ] && return 1
  echo "$ready_json" | jq -r --argjson board "$board_items" \
    '([.[].number] - [$board.items[].content.number]) | sort | .[]'
}
```

Main-loop integration, replacing the `else` branch at `scheduler.sh:1613-1617`:

**Blocking correction (operator gate).** `scheduler.sh:2` is `set -euo pipefail` and `:3` adds
`set -E`, which inherits the `ERR` trap into functions. Under those flags,
`OFF_BOARD=$(off_board_ready_issues "$BOARD_ITEMS")` is a *simple command*: when the substitution
returns non-zero, the shell **exits immediately**, firing `SCHED_UNHANDLED_ERR`. The following
`if [ $? -ne 0 ]` is unreachable, and a transient `gh issue list` failure would **kill the poll
loop** — taking the whole factory down in order to report that it could not check the queue. That
is a strictly worse outcome than the silent `nothing_to_do` this ticket set out to fix, and it is
Requirement 7's own hazard turned inside out.

The capture must be the `if` condition itself (or use `|| rc=$?`), so the non-zero path is handled
rather than fatal:

```bash
if [ -n "$DISPATCHED" ]; then
  echo "[$(date -u +%FT%TZ)] backlog=... dispatched=\"${DISPATCHED}\" main_red=${MAIN_IS_RED} graphql=${BUDGET}"
else
  # `if VAR=$(cmd); then` keeps a non-zero return non-fatal under `set -e`; a bare
  # `VAR=$(cmd)` followed by `[ $? -ne 0 ]` exits the scheduler instead (operator gate).
  if ! OFF_BOARD=$(off_board_ready_issues "$BOARD_ITEMS"); then
    echo "[$(date -u +%FT%TZ)] backlog=... skip=nothing_to_do off_board_check=failed main_red=${MAIN_IS_RED} graphql=${BUDGET}"
  elif [ -n "$OFF_BOARD" ]; then
    OFF_BOARD_COUNT=$(echo "$OFF_BOARD" | grep -c .)
    OFF_BOARD_LIST=$(echo "$OFF_BOARD" | head -10 | sed 's/^/#/' | paste -sd, -)
    [ "$OFF_BOARD_COUNT" -gt 10 ] && OFF_BOARD_LIST="${OFF_BOARD_LIST},+$((OFF_BOARD_COUNT - 10))more"
    echo "[$(date -u +%FT%TZ)] backlog=... skip=queue_unreachable off_board=${OFF_BOARD_COUNT} issues=${OFF_BOARD_LIST} main_red=${MAIN_IS_RED} graphql=${BUDGET}"
  else
    echo "[$(date -u +%FT%TZ)] backlog=... skip=nothing_to_do main_red=${MAIN_IS_RED} graphql=${BUDGET}"
  fi
fi
```

The new scheduler test must cover this directly: with a `gh()` stub that returns non-zero for
`issue list`, the loop body must still **reach and print** `off_board_check=failed` and continue.
A test that only asserts the string appears in the source would not catch the `set -e` exit.

(`backlog=...` stands in for the existing unchanged
`backlog=${BACKLOG_COUNT} refined=${REFINED_COUNT} in_progress=... in_review=... factory_running=... refine_running=...` prefix — every existing field stays byte-identical so operator tooling that greps this line is unaffected.)

### Testing

- `tests/test_factory_core_board.py`: new cases for `add_to_board` — success path (`item-add`
  returns an id, `item-edit` called with `STATUS_BACKLOG`), `item-add` failure with successful
  fallback lookup, `item-add` and fallback both failing (returns `False`, no `item-edit` call),
  following the file's existing `subprocess.run` stub pattern.
- A new scheduler test (e.g. `tests/test_off_board_detection.sh`), modeled on
  `tests/test_scheduler_pagination.sh`'s `SCHEDULER_SOURCE_ONLY=1` + exported `gh()` stub
  pattern: asserts that a board snapshot missing an open `ready-for-agent` issue produces
  `skip=queue_unreachable off_board=1 issues=#N` (satisfying Acceptance's scheduler-test
  requirement), and that a `gh issue list` stub failure produces
  `skip=nothing_to_do off_board_check=failed` rather than a bare `nothing_to_do`.

## Alternatives considered

- **Raw `addProjectV2ItemById` GraphQL mutation instead of `gh project item-add`.** Rejected:
  no precedent in this codebase for project-item CRUD, needs an extra call to resolve the
  issue's GraphQL node id that none of the three call sites currently has, and costs one more
  GraphQL call against a governed rate-limit budget for no behavioral benefit.
- **Scope this ticket to only the conformance spillover site** (the literal Acceptance line).
  Rejected: the two `ceiling-revisit.md` sites are not `ready-for-agent`-labeled, so Part 2
  cannot backstop them — deferring them would leave real, still-broken write paths with zero
  detection coverage, which is a worse outcome than the ticket's own "either half alone is
  worth shipping" framing.
- **Run the off-board check every poll cycle** (including cycles that dispatched work).
  Rejected: the issue specifically frames this as a per-idle-poll cost, a cycle that dispatched
  work has already proven the board reachable, and an unconditional extra `gh issue list` call
  every cycle is needless GraphQL spend for no additional signal.
- **Filter the off-board comparison by board status** (e.g. only flag issues missing from
  "Backlog" specifically). Rejected: would miss the literal bug (item never added to the
  project at all, i.e. absent from every status) and could produce confusing semantics for an
  issue that's on the board but, say, `Blocked`.
- **Put `off_board_ready_issues` in `scheduler_lib.sh`.** Rejected: violates that file's
  explicit "pure, side-effect-free item-blob predicates only, no `gh` calls" contract; the
  function shells out to `gh issue list` and operates on the whole board snapshot rather than a
  single item.

## Open questions (non-blocking)

- Scope-spillover tickets (`commands/dark-factory-conformance.md`) are labeled
  `needs-triage,scope-spillover`, never `ready-for-agent` — so Part 2's detector does not, and
  structurally cannot, backstop a `board.add()` failure at that specific site the way it does
  for a hypothetical future `ready-for-agent`-labeled creation path. Part 1's warning-on-failure
  plus the existing conformance PR comment thread is the only signal today. A future ticket
  could consider whether scope-spillover tickets need their own board-hygiene backstop (e.g. a
  periodic "spillover tickets with no board item" audit); out of scope here since it's a new
  detection mechanism, not a fix to the two identified gaps.
- Whether `gh project item-add`'s exit code and JSON shape are stable across the `gh` CLI
  version pinned in the factory image was not independently verified against a live API call in
  this refinement pass (no network access) — implementation should smoke-test the exact
  `--format json` output shape (`{"id": ...}` at top level, matching `item-edit`'s existing
  parsing) before relying on it.

## Verified at the operator spec gate (2026-09-10)

- `scripts/factory_core/board.py` really does expose `OWNER` (`:9`), `PROJECT_NUMBER` (`:11`),
  `STATUS_BACKLOG` (`:19`), `_find_item_by_number_checked` (`:23`) and `_item_edit_status` (`:56`),
  so the prescribed `add_to_board` composes from real names. Confirmed.
- `fetch_board_items` (`scheduler.sh:582`) returns
  `{items: [{content: {number, title, type}, labels, status}]}` and already applies
  `select(.content.number != null)` (`:619`), so `[$board.items[].content.number]` cannot yield
  nulls and needs no extra guard. The proposed `jq` array-difference is correct. Confirmed.
- The `skip=nothing_to_do` line is `scheduler.sh:1616`, and `BOARD_ITEMS` is assigned at `:1525`
  with its own `|| { ...; continue; }` guard — so the board snapshot is always valid JSON by the
  time the idle branch runs. Confirmed.
- `FACTORY_REPO_SLUG` is not assigned inside `scheduler.sh` but is supplied by the environment and
  used throughout (`:263`, `:299`, `:338`, `:501`, `:562`, `:576`, ...). The flagged assumption
  holds; no new env plumbing is needed. Confirmed.
- `scripts/scheduler_lib.sh`'s "pure, side-effect-free item-blob predicates only" contract is real,
  so placing `off_board_ready_issues` in `scheduler.sh` is right. Confirmed.

Still unverified, and correctly flagged by the spec: `gh project item-add --format json`'s exact
output shape. Smoke-test it before relying on `.id`.

## Assumptions (flagged)

- `gh project item-add`'s `--format json` output includes an `id` field at the top level,
  parseable the same way `_find_item_by_number_checked` parses `item-list`'s output elsewhere
  in `board.py` — based on the precedent snippet in
  `docs/archive/2026-08-28-dispatch-ceiling-weekly-revisit-plan.md:756-757`
  (`--format json --jq .id`), not independently re-verified against a live `gh` invocation.
  Flagged in Open questions above.
- `FACTORY_REPO_SLUG` is populated in `scheduler.sh`'s environment by the time the main loop
  runs (already used elsewhere in the file, e.g. `scheduler.sh:263`), so no new env plumbing is
  needed for `off_board_ready_issues`'s `gh issue list --repo` call.
