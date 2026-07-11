# Provider Abstraction — Route Bash/Entrypoint/Scheduler/Run-DAG Through Provider CLIs

**Issue:** omniscient/dark-factory#249
**Status:** draft — pending review
**Parent epic:** omniscient/dark-factory#202
**Depends on:** omniscient/dark-factory#248 (spec-approved, **not yet implemented** — introduces the
`Tracker`/`CodeHost` ABCs, GitHub reference adapters, and `scripts/factory_core/providers/cli.py`
that this ticket routes through). Per `CLAUDE.md`'s dependency gating, #249 cannot be dispatched to
implementation until #248 is Done — this spec can be written now, but the plan/implement phases must
wait.

---

## Overview / Problem Statement

`docs/provider-abstraction-design.md` (the authoritative parent spec, PR #203) lays out a six-step,
independently-shippable sequence (§11) to make Dark Factory's ticket-tracker and code-host providers
swappable. Issue #248 is step 1: it introduces the `Tracker`/`CodeHost` ABCs, GitHub reference
adapters, and the golden-argv parity net — with **zero call sites rewired**. Issue #249 is **step 2**:
route the actual inline `gh`/`gh api`/`gh api graphql` hosted operations in `scheduler.sh`,
`entrypoint.sh`, `smoke_gate.sh`, `workflows/archon-dark-factory.yaml`, and the remaining
non-`board.py` hosted calls in `epic_autopilot.py`, `breaker.py`, `rescue.py`, and `main_red_fixer.py`
through the provider layer #248 builds — still selecting GitHub/GitHub, preserving exact behavior,
verified by #248's golden-argv parity suite. Plain `git` (clone/branch/commit/push/fetch/diff/checkout)
is out of scope — it is already host-agnostic (parent spec §3 point 3).

## Requirements

Distilled from the issue's acceptance criteria and refined through Q&A (log below):

1. **Bash/YAML callers route through the CLI.** `scheduler.sh`, `entrypoint.sh`, `smoke_gate.sh`, and
   `workflows/archon-dark-factory.yaml` replace their inline `gh`/`gh api`/`gh api graphql` hosted
   calls with `python -m factory_core.tracker <verb>` / `python -m factory_core.codehost <verb>` CLI
   calls (parent spec §4.2). Plain `git` stays inline.
2. **Existing Python modules route through direct in-process calls, not the CLI.** `epic_autopilot.py`,
   `breaker.py`, `rescue.py`, and `main_red_fixer.py` — all of which call `gh` today via
   `subprocess.run(["gh", ...])` — are migrated to import and call `get_tracker()`/`get_codehost()`
   directly in-process, matching the pattern `board.py` already establishes under #248 (parent spec
   §4.1: "mirrors the pattern `board.py` already established"). None of these four modules spawns a
   subprocess to invoke the new CLI — that would be a Python module shelling out to a sibling Python
   CLI, the exact indirection the CLI/direct-import split exists to avoid. Callers that already
   delegate to `board.py` directly (`rescue.py:136-137`'s `board.set_board_status`/
   `board.post_or_update_comment`; `breaker.py`'s `from .board import set_board_status,
   STATUS_BLOCKED`) are unaffected by this ticket — `board.py`'s public signature does not change.
3. **`board.py` itself receives no changes in #249.** #248 fully refactors `board.py`'s three public
   functions (`find_board_item`, `set_board_status`, `post_or_update_comment`) to delegate to
   `GitHubTracker` while preserving their exact signatures — there is no inline hosted operation left
   in `board.py` for #249 to touch. #249's board-*related* work is exclusively at caller boundaries
   that reimplement board.py's logic inline instead of calling it — concretely, `entrypoint.sh`'s own
   bash-native `find_board_item()` / `set_board_status()` helpers (`entrypoint.sh:114-128`), which
   duplicate `board.py`'s `gh project item-list`/`item-edit` calls in bash. These route through
   `python -m factory_core.tracker set-status` per requirement 1, same as any other `entrypoint.sh`
   hosted call.
4. **`smoke_gate.sh`'s sentinel regression-ticket lifecycle migrates partially.** The on-red
   `gh issue create --label regression` and `gh issue comment` (filing/commenting on the sentinel
   ticket, `smoke_gate.sh:56-73`) route through `Tracker.create_item`/`upsert_comment`. The on-green
   `gh issue close "$REGR_NUM" --comment "..."` (`smoke_gate.sh:94-97`) **stays an inline `gh` call**
   in #249 — #248's frozen `Tracker.resolve_item(id)` takes only an opaque id, with no
   comment/message parameter, and splitting today's single atomic close-with-comment argv into a
   preceding `upsert_comment` call plus a bare `resolve_item` call would both (a) emit two argv
   sequences where the golden-argv parity suite expects one, and (b) misuse `resolve_item`'s
   documented purpose — the merge-triggered close-on-merge composition point (parent spec §6.4), not
   a general-purpose "close this issue" primitive. This is a **deliberate, documented exception**, not
   scope spillover: annotate the call site with an inline comment
   (`# TODO(#<follow-up>): no compliant Tracker op — see provider-abstraction §6.4 gap`) and file a
   follow-up ticket proposing a `Tracker` ABC extension (e.g. `close_item(id, comment=None)`) for
   #248's owners to evaluate — extending the frozen ABC is not #249's to do unilaterally.
5. **Golden-argv parity.** Every migrated call site's resulting `gh`/`gh api`/`gh api graphql` argv
   must be identical to today's inline call, verified by #248's parity suite, extended with new
   fixtures for call sites #248's initial suite didn't cover (e.g. `gh project item-edit` from
   `entrypoint.sh`, `gh pr create`/`merge`/`ready`/`checks` from the run DAG and
   `main_red_fixer.py`, the GraphQL epic-children query in `epic_autopilot.py`).
6. **No provider-selection changes.** `FACTORY_TRACKER`/`FACTORY_CODEHOST` env-based selection is
   parent spec step 3, a later ticket. `get_tracker()`/`get_codehost()` unconditionally resolve to the
   GitHub adapters per #248 throughout this ticket.
7. **Existing behavior is preserved exactly.** Scheduler statuses, WIP limits, marker-comment
   idempotency, retries, rescue promotion, epic children discovery, PR checks/reviews, and merge
   behavior are unchanged — enforced by keeping the existing bash/Python test suites
   (`scheduler`, `entrypoint`, `smoke-gate`, `rescue`, `epic-autopilot`, `run-record`, workflow DAG)
   green throughout, plus a GitHub/GitHub dry-run parity capture per the issue's "Run and verify"
   section.
8. **Hard dependency on #248.** #249 cannot be dispatched to plan/implement until #248 is Done — the
   `Tracker`/`CodeHost` ABCs, GitHub adapters, and `providers/cli.py` must exist and pass CI before any
   of #249's call sites have a provider layer to route through. This is enforced by the existing
   `Depends on: #248` issue-body mechanism (`CLAUDE.md`'s dependency gating); this spec does not need
   to separately re-implement that gate.

## Brainstorming Q&A

> **Q1:** The issue's Scope section lists all nine files — including the five **Python** modules
> (`factory_core/board.py`, `epic_autopilot.py`, `breaker.py`, `rescue.py`, `main_red_fixer.py`) —
> as needing to be replaced "with thin `python -m factory_core...` provider CLI calls." Taken
> literally, each Python module would spawn a `subprocess` to invoke `python -m
> factory_core.tracker/codehost`. But the parent spec explicitly says `board.py` delegates to
> `get_tracker()` as a direct in-process Python call, framing the CLI as existing specifically so
> **bash** callers can reach the same logic ("mirrors the pattern `board.py` already established
> (Python module + CLI, called from bash)"). Should `epic_autopilot.py`, `breaker.py`, `rescue.py`,
> and `main_red_fixer.py` (all currently calling `gh` via direct `subprocess.run`) be migrated to
> **import and call `get_tracker()`/`get_codehost()` directly**, with the issue's CLI phrasing read
> as shorthand for "route through the provider layer" rather than a literal subprocess mandate? Or
> is there a reason these four modules should genuinely shell out to the CLI like the bash files do?
>
> **A1:** Yes — the four Python modules should import and call `get_tracker()`/`get_codehost()`
> directly in-process, exactly as `board.py` does; they should not shell out to the CLI. Three
> independent signals confirm this: (1) parent spec §4.1 draws the line explicitly — `board.py`
> *delegates* in-process while bash/the run DAG use "thin CLI calls," explicitly "mirror[ing] the
> pattern `board.py` already established (Python module + CLI, called from bash)" — the CLI is the
> bridge for processes that can't import Python objects; a Python module spawning a Python CLI is
> the exact indirection that framing is designed to avoid. (2) §11 step 2 — which the issue cites as
> "implementation step 2" — reads "Route **bash / entrypoint / scheduler / run-DAG** hosted calls
> through the provider CLIs," conspicuously omitting the four Python modules. (3) The spec instead
> treats these modules as extraction *sources*: §5.3 lists `epic_autopilot.py`, `breaker.py`,
> `rescue.py` among files whose `gh` calls are "mechanically extracted" into `GitHubTracker`; §6.2
> does the same for `rescue.py`/`main_red_fixer.py` into `GitHubCodeHost`. These modules already
> parse `gh --json` output in-process, so consuming structured objects from an in-process adapter is
> strictly simpler than re-serializing through a CLI boundary — the `subprocess.run(["gh", ...])`
> calls are what's being replaced, not a pattern to preserve. Concrete call sites: `breaker.py:82-83,
> 104-105` (`gh issue edit`/`comment` → `Tracker.add_label`/`remove_label`/`upsert_comment`);
> `rescue.py:40-41,64-65,131-132` (`gh pr list`/`checks`/`ready` → `CodeHost.find_change_for`/
> `get_change_checks`/`mark_ready`); `main_red_fixer.py:207,220,234,238` (`gh pr create`/`checks`/
> `merge` → `CodeHost.open_change`/`get_change_checks`/`merge_change`, `gh issue comment` →
> `Tracker.upsert_comment`; its plain `git` calls stay inline); `epic_autopilot.py:355-358,396-528`
> (`gh api graphql` children query, `issue view`/`edit`/`comment` → `Tracker.get_children`/
> `get_item`/label/comment methods).

> **Q2:** #248's spec requirement 4 already refactors `board.py`'s three public functions to
> delegate to `GitHubTracker` while preserving their exact signatures, so `scheduler.sh`'s
> `set_board_status()` wrapper and `factory_core/cli.py`'s `board-move` subcommand need no changes
> in #248. Given that, does #249 need to make **any further changes to `board.py` itself**, or is
> `board.py`'s presence in #249's scope list better read as "the *callers* of `board.py` are what
> #249 may still touch," while `board.py`'s own internals are a completed, no-further-action
> dependency by the time #249 starts?
>
> **A2:** Correct — `board.py`'s own internals are a completed, no-further-action dependency;
> #249's residual board-related work, if any, is at the caller boundary, not inside `board.py`.
> The whole file is 78 lines: `STATUS_*` constants plus exactly the three functions #248 converts
> (`board.py:22-78`) — every hosted `gh` call in the module lives inside those three functions, so
> once #248 lands there is no *other* inline hosted operation left for #249 to route. The design
> doc treats `board.py` as the *exemplar* of the target pattern, not a target — step 2 (which #249
> implements) is scoped to "bash / entrypoint / scheduler / run-DAG hosted calls," i.e. the inline
> calls that don't yet go through a Python seam. The already-thin callers (`scheduler.sh:469-471`'s
> `board-move` wrapper; `rescue.py:136-137` and `epic_autopilot.py`'s direct `import board`) are not
> "inline hosted `gh` calls" — they're already routed through the module, and #248 explicitly
> reserves them as "no changes." Whether #249 additionally re-points these already-delegating
> callers at the new tracker CLI is a consolidation choice, not a parity requirement — given the
> acceptance criterion that scheduler statuses/marker comments/rescue/epic children "remain
   unchanged," the safest default is to leave them alone. #249's real board-*adjacent* work is
> limited to callers that duplicate board.py's logic inline instead of delegating to it —
> concretely `entrypoint.sh`'s own bash-native `find_board_item()`/`set_board_status()` helpers.

> **Q3:** `smoke_gate.sh`'s on-green `gh issue close "$REGR_NUM" --comment "..."` — closing a GitHub
> issue with an explanatory comment, as a standalone sentinel-ticket lifecycle action unrelated to
> any PR merge — has no clean match in #248's fixed 12-operation `Tracker` ABC (`resolve_item` is
> documented specifically for the merge-composition point, not a general "close this issue"
> primitive, and takes only `(id)`, no comment parameter). Should #249 (a) treat `resolve_item` as
> also covering this generic close case, splitting it into a preceding comment call plus a bare
> `resolve_item` call; (b) leave this one call site inline as a documented interface gap and file a
> follow-up to extend the ABC; or (c) something else?
>
> **A3:** (b), with a specific shape. Keep `gh issue close --comment` inline in `smoke_gate.sh` for
> #249; route only the create/comment halves of the sentinel lifecycle through the CLIs now. The
> `create_item`/`upsert_comment` mappings are clean and in-scope (parent spec §5.1 documents
> `create_item`'s literal purpose as "regression tickets"). But (a) cannot work without breaking the
> parity invariant: today's sentinel close is a single argv (`gh issue close ... --comment ...`);
> #248's frozen `resolve_item(id)` takes no comment parameter, so splitting into two calls emits two
> argv sequences where there is one today, failing the golden-argv suite and changing observable
> behavior (a separate comment event vs. an atomic close-with-comment) — exactly what #249's
> acceptance criteria forbid. Overloading `resolve_item`'s *meaning* is also wrong independent of
> parity: it's documented specifically as the merge-composition point (parent spec §6.4,
> `host.merge_change(id) succeeds → tracker.resolve_item(issue_id)`), and the sentinel lifecycle is
> explicitly not that. The design anticipates capability gaps as "designed-against follow-up" work
> (parent spec §8, §12), and the factory's own convention is to file spillover tickets for changes
> lacking a compliant home (`CLAUDE.md` scope discipline) rather than smuggle them in. The follow-up
> should propose a first-class primitive (e.g. `close_item(id, comment=None)`) as a `Tracker` ABC
> extension — #248's surface, not #249's to revise unilaterally. One open detail for that follow-up:
> whether `GitHubTracker.resolve_item`'s own parity fixture (#248) emits `gh issue close` at all, or
> is a no-op on GitHub (today's GitHub path closes via the `Closes #N` keyword + Projects
> card-→Done automation per §6.4) — doesn't change this ticket's scope, but the follow-up should
> reconcile it so a new `close_item` and `resolve_item` don't emit conflicting GitHub argv.

## Architecture / Approach

### Representative call-site → provider-operation mapping

Not exhaustive — full enumeration of all ~60 `gh` call sites across the nine files is an
implementation-plan-level task, mirroring #248's own precedent of deferring exhaustive
site-by-site enumeration to planning rather than re-deriving it in the spec.

| File | Current call (representative) | Target |
|---|---|---|
| `scheduler.sh:134,1227` | `gh api rate_limit` | `Tracker.get_rate_budget()` via CLI (bash policy/`sleep` stays inline) |
| `scheduler.sh:305-400,677-714` | `gh issue view/edit/comment`, `gh pr view --json reviews` | `Tracker.get_item/get_comments/add_label/upsert_comment`, `CodeHost.get_change_reviews` via CLI |
| `scheduler.sh:504,517,528,593` | `gh pr list --search head:`, `gh pr checks`, `gh api graphql` (WIP/status) | `CodeHost.find_change_for/get_change_checks`, `Tracker.get_status_limits` via CLI |
| `entrypoint.sh:114-128` | bash-native `find_board_item`/`set_board_status` (duplicates board.py) | `Tracker.set_status` via CLI (req. 3) |
| `entrypoint.sh:146-155,283-409` | `gh api .../comments` PATCH/POST, `gh issue comment` | `Tracker.upsert_comment` via CLI |
| `smoke_gate.sh:56-73` | `gh issue comment`, `gh issue create --label regression` | `Tracker.upsert_comment`/`create_item` via CLI |
| `smoke_gate.sh:94-97` | `gh issue close --comment` | **stays inline** (req. 4, documented exception) |
| `workflows/archon-dark-factory.yaml` (~25 sites) | `gh issue view/edit/comment`, `gh pr list/view/create/edit/merge/ready/checks`, `gh api .../pulls/.../comments`, `gh project item-edit` | `Tracker.*`/`CodeHost.*` via CLI, one-to-one per existing shell `run:` blocks |
| `epic_autopilot.py:355-358,396-528` | `_gh_json` wrapping `subprocess.run(["gh"] + args)`; `issue view/edit/comment` | `get_tracker()` direct calls (req. 2) |
| `breaker.py:82-105` | `gh issue edit --add-label`, `gh issue comment` | `get_tracker()` direct calls (req. 2) |
| `rescue.py:40-65,131-132` | `gh pr list --search head:`, `gh pr checks`, `gh pr ready` | `get_codehost()` direct calls (req. 2) |
| `main_red_fixer.py:207-238` | `gh pr create/checks/merge`, `gh issue comment` | `get_codehost()`/`get_tracker()` direct calls (req. 2); plain `git` stays inline |

### Two routing seams, one rule: bash/YAML → CLI, Python → direct import

The single governing rule (req. 1–2): anything that is *already* a bash script or YAML `run:` block
gets a `python -m factory_core.tracker/codehost <verb>` CLI call in place of its inline `gh`
invocation. Anything that is *already* Python imports `get_tracker()`/`get_codehost()` and calls the
returned object's method directly — no subprocess, no re-serialization through a CLI boundary. This
is not a new rule invented by this ticket; it is #248's own `board.py` treatment, generalized to the
four other Python modules that currently duplicate `board.py`'s `subprocess.run(["gh", ...])` style.

### Eliminating the entrypoint.sh/board.py duplication

`entrypoint.sh:114-128` currently reimplements `board.py`'s `find_board_item`/`set_board_status` logic
natively in bash (`gh project item-list` + `jq`, then `gh project item-edit`) rather than delegating
to `board.py` or the future CLI. This ticket replaces those two bash functions with a single
`python -m factory_core.tracker set-status --id "$ISSUE_NUM" --status in_progress` call (or
equivalent verb per #248's finalized CLI surface), removing the duplication rather than leaving two
independent implementations of the same board-move logic.

### Testing approach

Extend #248's golden-argv parity suite (`tests/test_provider_tracker_parity.py`,
`tests/test_provider_codehost_parity.py`) with fixtures for the call sites #248's initial "one test
per method with a current call site" pass covered via `board.py` only — e.g. `gh project item-edit`
as invoked from `entrypoint.sh`'s bash function (once it routes through the CLI), `gh pr
create`/`merge`/`ready` as invoked from the run DAG and `main_red_fixer.py`. Keep all existing
suites (`tests/test_factory_core_board.py`, scheduler/entrypoint/smoke-gate/rescue/epic-autopilot/
run-record tests, workflow DAG checks) green throughout — per the issue's "Run and verify" list —
plus a GitHub/GitHub dry-run parity capture comparing pre- and post-migration argv for a sample run.

## Alternatives Considered

1. **Extend #248's frozen `Tracker` ABC within #249 to close the `smoke_gate.sh` gap.** Rejected
   (Q3) — #248 is already spec-approved; revising its ABC is that ticket's/its owners' surface, not
   #249's to do unilaterally mid-ticket. A follow-up ticket is the sanctioned path.
2. **Route the four Python modules through CLI subprocess calls, taking the issue text literally.**
   Rejected (Q1) — wasteful Python-spawns-Python-CLI indirection, contradicted by the parent spec's
   explicit "mirrors board.py's pattern" framing and by these modules' existing direct `board.py`
   imports.
3. **Make further changes to `board.py` itself in #249** to "complete" its mention in the issue's
   scope list. Rejected (Q2) — #248 fully owns and completes `board.py`'s conversion; re-touching it
   in #249 would re-litigate an already-approved decision for no behavioral gain.

## Open Questions (Non-blocking)

- Exact enumeration of all ~60 call sites → CLI verb mapping is deferred to the implementation plan
  (mirrors #248's own precedent).
- Whether `GitHubTracker.resolve_item`'s parity fixture (#248) emits `gh issue close` or is a no-op
  on GitHub — affects how cleanly a future `close_item` extension composes; left to #248's fixture
  owners and the follow-up ticket from requirement 4.
- Exact CLI verb/flag names are illustrative per parent spec §4.2, not finalized until #248 ships its
  actual `providers/cli.py`; this spec's mapping table uses representative verb names and the plan
  phase should reconcile them against #248's landed CLI signatures.

## Assumptions

- This spec assumes #248's exact module layout
  (`scripts/factory_core/providers/{tracker,codehost}/{base,github}.py`, `providers/cli.py`) as its
  integration surface, per #248's own spec.
- Per `config/config.yaml`'s `dispatch_ceiling` policy, this is a `size: L` ticket and parks in
  Blocked for human pairing at implementation time regardless of scoping — consistent with #248's own
  assumption; this spec does not need to additionally hedge against fully-autonomous implementation
  risk.
- "Byte/argv-equivalent" parity means the argv list passed to `subprocess.run` (or equivalent), not
  literal stdout/stderr byte equality — consistent with #248's spec and the existing
  `test_factory_core_board.py` convention.
- The `smoke_gate.sh` close-with-comment gap (requirement 4) is the only call site in #249's scope
  without a compliant `Tracker`/`CodeHost` home; all other in-scope call sites map cleanly onto
  #248's 12-operation `Tracker` / 11-operation `CodeHost` contracts.
