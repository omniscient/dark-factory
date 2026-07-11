# Provider Abstraction — Tracker/CodeHost Contracts, GitHub Reference Adapters, and Parity Net

**Issue:** omniscient/dark-factory#248
**Status:** draft — pending review
**Parent epic:** omniscient/dark-factory#202
**Depends on:** omniscient/dark-factory#203 (merged — `docs/provider-abstraction-design.md`, the authoritative parent spec this ticket implements step 1 of)

---

## Overview / Problem Statement

`docs/provider-abstraction-design.md` (PR #203) lays out a six-step, independently-shippable
sequence (§11) to make Dark Factory's ticket-tracker, code-host, and model-endpoint providers
swappable. Issue #248 is **step 1 only**: introduce the `Tracker` and `CodeHost` abstract
contracts, extract today's GitHub logic into reference adapters that emit byte-identical calls
to what runs now, and build the golden-argv parity net that every later step (2–6) leans on as
its safety guardrail.

This ticket makes **zero behavior change**. No bash call site (`scheduler.sh`, `entrypoint.sh`,
`smoke_gate.sh`, `workflows/archon-dark-factory.yaml`) is rewired to call the new adapters —
that is spec step 2, a separate follow-up ticket. No `JiraTracker` or `GitLabCodeHost` is
implemented — those are steps 4 and 6. This ticket only builds the seam and proves, via tests,
that the seam is faithful to current behavior.

## Requirements

Distilled from the issue's acceptance criteria and refined through Q&A (log below):

1. `Tracker` ABC (`scripts/factory_core/providers/tracker/base.py`) defines all 11 operations
   from parent spec §5.1: `list_work_items`, `get_item`, `get_comments`, `get_children`,
   `set_status`, `add_label`, `remove_label`, `upsert_comment`, `create_item`, `resolve_item`,
   `get_status_limits` (degradable), `get_rate_budget` (degradable). IDs are opaque strings
   throughout — no `int()` coercion anywhere in the contract or the GitHub implementation.
2. `CodeHost` ABC (`scripts/factory_core/providers/codehost/base.py`) defines all 11 operations
   from parent spec §6.1: `remote_url`, `find_change_for`, `open_change`, `update_change_body`,
   `mark_ready`, `merge_change`, `get_change_checks`, `get_change_mergeable`,
   `get_change_reviews`, `get_change_inline_comments`, `close_keyword`. Plain `git`
   (clone/branch/commit/push/fetch/diff) stays inline, outside `CodeHost`, per parent spec §6.1
   / principle 3.
3. `GitHubTracker` and `GitHubCodeHost` (`.../tracker/github.py`, `.../codehost/github.py`) are
   mechanical extractions that emit **argv-identical** `gh`/`gh api`/`gh api graphql` calls to
   what runs today — proven by golden-argv parity tests, not asserted by inspection.
4. `board.py` is refactored to delegate to `get_tracker()` — its current `gh`-driven body
   becomes (or calls into) `GitHubTracker` — while **keeping its existing public function
   signatures** (`find_board_item`, `set_board_status`, `post_or_update_comment`) unchanged, so
   the existing `factory_core/cli.py` `board-move` subcommand and `scheduler.sh`'s
   `set_board_status()` shell wrapper (line 469–471) need **no changes** in this ticket.
5. `get_rate_budget()` / `get_status_limits()` are genuine working ports of the *fetch-and-parse*
   logic that exists today in `scheduler.sh` (`check_rate_limit`'s `gh api rate_limit` call;
   `fetch_wip_limits`/`get_column_limit`'s GraphQL Status-options query + `limit:\s*\d+`
   description parse, including its existing `999` fallback when no `limit:` is present) — not
   stubs. The bash-side *policy* (the `RATE_LIMIT_FLOOR` comparison and `sleep` in
   `check_rate_limit`) stays in `scheduler.sh`, untouched, in this ticket.
6. A new `scripts/factory_core/providers/cli.py` exposes thin CLI entry points
   (`python -m factory_core.tracker …`, `python -m factory_core.codehost …`) per parent spec
   §4.2, as new, additional surface — not wired into any existing bash call site yet. This is a
   distinct module from the existing general-purpose `scripts/factory_core/cli.py` (which
   already dispatches `board-move`, `deconflict`, `breaker-*`, etc.) and does not modify it.
7. Golden-argv parity tests cover **representative current paths**: one test per
   `Tracker`/`CodeHost` method that has an existing call site today. Methods with no current
   call site (e.g. anything that only `JiraTracker` would exercise) are covered by the ABC
   definition itself, not a parity test — full cross-adapter contract coverage is the later
   VCR-style suite from parent spec §10, out of scope here.
8. Opaque-ID round-trip (`"42"` and `"PROJ-123"`) is proven as a **type-level contract test**
   against the `GitHubTracker`/`GitHubCodeHost` argument-construction path (mocking
   `subprocess.run`), not against any Jira-specific behavior — `JiraTracker` doesn't exist yet
   and acceptance criteria explicitly forbid introducing one in this ticket.
9. Existing tests (`tests/test_factory_core_board.py` and the rest of the suite) stay green.

## Brainstorming Q&A

> **Q1:** The issue's scope bullets say to "Move current hosted GitHub operations into
> `GitHubTracker` and `GitHubCodeHost` wrappers without changing emitted calls" and "Add thin
> CLI entry points for tracker/codehost operations." The parent spec's phased Implementation
> sequence (§11) splits this across two separate, independently-shippable steps: (1) ABCs +
> GitHub implementations + parity tests, zero behavior change; (2) route bash/entrypoint/
> scheduler/run-DAG hosted calls through the provider CLIs. Does #248's scope include
> refactoring `board.py` to delegate to `get_tracker()`, while leaving the bash call sites
> (`scheduler.sh`, `entrypoint.sh`, `smoke_gate.sh`, the run DAG) untouched — i.e. #248 = spec
> step 1 only? Or does #248 also require rewiring those bash call sites in this same ticket?
>
> **A1:** #248 is spec step 1 only. Refactor `board.py` to delegate to `get_tracker()` (its
> gh-driven body becomes `GitHubTracker`) and *add* the thin CLI entry points, but leave the
> bash call sites (`scheduler.sh`, `entrypoint.sh`, `smoke_gate.sh`, the run DAG) untouched and
> still calling `gh`/`git` inline. The routing of those bash/DAG call sites through the new
> provider CLIs is spec step 2's explicit language, which §11 declares an independently-
> shippable step decomposed into a separate per-step ticket. Three signals confirm this: the
> issue says "**Add** thin CLI entry points," not "route existing call sites through them"; its
> acceptance criteria require only that adapters "emit byte/argv-equivalent calls for
> representative current paths" and that "default runtime behavior is unchanged"; and §10 frames
> the golden-argv parity tests as "the guardrail for touching scheduler.sh, entrypoint.sh,
> smoke_gate.sh, the run DAG" — #248 lands the guardrail so step 2 can safely do the touching
> later.

> **Q2:** Acceptance criterion: "GitHub reference adapters emit byte/argv-equivalent calls for
> representative current paths," verified via "golden-argv parity tests." The codebase's one
> existing parity-adjacent test file, `tests/test_factory_core_board.py`, monkeypatches
> `subprocess.run` and asserts on the constructed argv/behavior for `board.py`'s current gh
> calls. Should the new golden-argv parity tests for `GitHubTracker`/`GitHubCodeHost` (a) follow
> that same subprocess.run-monkeypatch-and-assert-argv pattern, (b) live in new files rather than
> being folded into `test_factory_core_board.py`, and (c) cover "representative current paths"
> as one test per method with an existing call site, rather than exhaustively testing every ABC
> method?
>
> **A2:** Yes to all three. (a) Follow `test_factory_core_board.py`'s
> `monkeypatch.setattr(subprocess, "run", ...)` + assert-on-argv approach — this is distinct from
> spec §10's separate VCR-style contract-test suite, which lands with Jira in step 4, not #248.
> (b) New files (`tests/test_provider_tracker_parity.py`, `tests/test_provider_codehost_parity.py`)
> — the repo's test-naming convention mirrors the module under test, and the adapters live under
> a new `providers/` tree. `test_factory_core_board.py` stays and stays green: after `board.py`
> delegates to `GitHubTracker`, those tests still exercise the same argv through `board.*` and
> serve as the delegation-didn't-break-anything check. (c) One test per method with an existing
> call site today (board.py's gh/GraphQL paths, plus the gh operations wrapped from
> scheduler.sh/entrypoint.sh/smoke_gate.sh). Methods with no current call site have no "current
> path" to be byte-equivalent to — full ABC-method coverage is the later VCR-style suite, out of
> scope for #248. The parity tests exercise the adapters directly (and via the refactored
> `board.py` for board paths), not by driving `scheduler.sh`/`entrypoint.sh` themselves, since
> rewiring those bash callers is step 2.

> **Q3:** Acceptance criterion: "Opaque IDs such as `42` and `PROJ-123` round-trip without
> integer coercion." Scope explicitly excludes any Jira implementation in this ticket, and
> `JiraTracker` itself is parent-spec step 4 (a separate future ticket). Given `JiraTracker`
> doesn't exist yet, should the opaque-ID round-trip requirement be verified as a type-level
> contract test — feed a Jira-shaped string like `"PROJ-123"` through the `Tracker`/`CodeHost` ABC
> method signatures and `GitHubTracker`/`GitHubCodeHost`'s argument-construction/argv-building
> code paths (mocking the `gh` subprocess call), asserting the string passes through unchanged
> with no `int()` coercion — rather than any live or mocked Jira-specific behavior?
>
> **A3:** Yes. The only vehicle available in #248 is the GitHub reference adapter, so the
> round-trip must be proven through the `Tracker`/`CodeHost` ABC signatures and the
> `GitHubTracker`/`GitHubCodeHost` argv-building paths — feeding both `"42"` and `"PROJ-123"` and
> asserting the string reaches the constructed argv unchanged, mocking `subprocess.run` exactly
> as `test_factory_core_board.py` does. The Jira-style key is a fixture value, not a Jira code
> path: it exercises the generic contract's tolerance of non-numeric IDs, which is the point of
> the opaque-strings design decision. Verifying live/mocked Jira behavior would violate the "No
> Jira/GitLab implementation is introduced in this ticket" acceptance criterion.

> **Q4:** The parent spec marks `get_rate_budget()` and `get_status_limits()` as "degradable"
> Tracker operations — "can no-op with safe defaults so a new adapter has a low floor." Today,
> real implementations of this logic exist only in bash, in `scheduler.sh`
> (`check_rate_limit`'s `gh api rate_limit` call, and `fetch_wip_limits`/`get_column_limit`'s
> GraphQL Status-options read). No `factory_core` Python module implements this today. Should
> `GitHubTracker.get_rate_budget()`/`get_status_limits()` (a) port the real existing bash logic
> into genuine working Python implementations (even though the bash call sites stay untouched
> per the step-1/step-2 split), or (b) ship as safe-default stubs, deferring real porting to the
> step-2 ticket?
>
> **A4:** (a) — port the real bash logic into genuine working Python implementations. Spec step
> 1 is explicit that the GitHub reference adapters "wrap the *exact* current calls" so that step
> 2 can route bash through the provider CLIs "still GitHub; parity tests hold." If these shipped
> as `None`/unlimited stubs, step 2 could not rewire `scheduler.sh`'s
> `check_rate_limit`/`fetch_wip_limits`/`get_column_limit` without silently disabling rate-limit
> backoff and WIP ceilings — breaking "zero behavior change" and leaving nothing for the
> golden-argv parity net to assert against. "Degradable" is a property of the contract/base ABC
> and *future* adapters (giving a Jira adapter a low floor via safe defaults), not license for
> the GitHub reference adapter to stub them. Two scoping notes: `get_rate_budget()` ports only
> the fetch/parse of `gh api rate_limit` (the `graphql` resource's `remaining`/`reset`, and the
> `used/limit` string used at `scheduler.sh:1227`) — the `RATE_LIMIT_FLOOR` comparison and
> `sleep` policy stays in the bash caller until step 2. `get_status_limits()` ports the GraphQL
> Status-options query plus the `limit:\s*\d+` description parse, **including** the existing
> `999` fallback — preserving that fallback is where degradability legitimately appears in the
> GitHub adapter, not a reason to no-op the whole method.

## Architecture / Approach

### Package layout (parent spec §4.1)

```
scripts/factory_core/providers/
  __init__.py          # get_tracker(), get_codehost(); no selection logic in #248 —
                        #   always returns GitHubTracker()/GitHubCodeHost() (step 3 adds
                        #   FACTORY_TRACKER/FACTORY_CODEHOST env selection)
  tracker/
    base.py             # Tracker ABC (§5.1) — 11 operations, opaque string IDs
    github.py           # GitHubTracker — wraps board.py's current gh calls verbatim
  codehost/
    base.py              # CodeHost ABC (§6.1) — 11 operations
    github.py            # GitHubCodeHost — wraps today's gh pr / gh api …pulls… calls
  cli.py                 # NEW thin CLI: python -m factory_core.tracker / .codehost
```

`jira.py` and `gitlab.py` are **not created** in this ticket (steps 4 and 6). The `base.py` ABCs
must be shaped so a later Jira/GitLab adapter is a bounded, additive task — no changes to the
ABC signature should be needed when those land.

### `Tracker`: `board.py` becomes a delegator, not a rewrite target for its callers

`board.py`'s three public functions have a **narrower** signature than the `Tracker` ABC:
`set_board_status(issue_num: int, option_id: str)` takes an already-resolved GitHub Status
option ID (every current caller in `scheduler.sh` passes `$FACTORY_STATUS_*` directly — see
call sites at `scheduler.sh:345,385,894,961,1076`). `Tracker.set_status(id, canonical)` takes an
opaque ID and a **canonical** status name (`ready`, `in_progress`, …) and must resolve
`canonical → option_id` via `identity.STATUS` internally.

Concretely: `GitHubTracker.set_status(id, canonical)` resolves `option_id =
identity.STATUS[canonical]` and then invokes the same underlying `gh project item-edit`
subprocess call `board.py` makes today; `board.py`'s `set_board_status(issue_num, option_id)`
keeps its exact current signature and behavior (either by staying the shared low-level
implementation that `GitHubTracker` calls into, or by becoming a one-line delegator to a
private helper shared with `GitHubTracker` — an implementation-plan-level choice, not a
spec-level one). Either way, `factory_core/cli.py`'s existing `board-move` subcommand and
`scheduler.sh`'s `set_board_status()` wrapper (line 469) call `board.set_board_status(issue_num,
option_id)` exactly as they do today — **no changes** to either in this ticket. This is the
concrete mechanism behind requirement 4.

`find_board_item` and `post_or_update_comment` fold into `GitHubTracker.get_item`-adjacent and
`GitHubTracker.upsert_comment` respectively, on the same terms.

### `CodeHost`: no existing Python home — the parity baseline is bash/YAML, not board.py

Unlike `Tracker`, most current `CodeHost`-shaped operations (`gh pr list --search head:`,
`gh pr create`, `gh pr merge`, `gh pr checks`, `gh pr view --json reviews`) live as **inline
strings in `workflows/archon-dark-factory.yaml`** (the run DAG) today, not in any
`factory_core` Python module — confirmed by grep: the DAG has ~12 `gh pr …`/`gh api …pulls…`
call sites; `rescue.py`/`main_red_fixer.py` only reference `gh pr checks` output shape in
docstrings, not construct the call. This means `GitHubCodeHost`'s parity tests cannot be
"assert an existing Python caller still works after delegation" (there is no existing Python
caller) — they must instead assert `GitHubCodeHost.find_change_for()` /
`.open_change()` / `.merge_change()` / etc. construct argv **identical to the literal strings
already hardcoded in the YAML**, i.e. the YAML is the golden baseline the test's expected-argv
constants are transcribed from. `entrypoint.sh` (11 call sites) contributes the remainder
(remote-URL-with-token construction, PR-related preflight). No YAML or bash file changes in
this ticket — the DAG keeps calling `gh` inline; only the new `GitHubCodeHost` Python class
proves it *would* emit the same calls.

### Golden-argv parity tests

Two new files, following `tests/test_factory_core_board.py`'s
`monkeypatch.setattr(subprocess, "run", ...)` + assert-on-constructed-argv convention:

- `tests/test_provider_tracker_parity.py` — one test per `Tracker` method with a current call
  site (via `board.py`'s existing paths, now routed through `GitHubTracker`), plus
  `get_rate_budget`/`get_status_limits` against `scheduler.sh`'s current `gh api rate_limit` /
  GraphQL Status-options argv and description-parsing behavior (including the `999` fallback).
- `tests/test_provider_codehost_parity.py` — one test per `CodeHost` method with a current call
  site, asserting argv equality against the transcribed YAML/bash constants described above.
- Both files include the opaque-ID contract test: `"42"` and `"PROJ-123"` fed through every
  method taking an ID, asserting the captured argv contains the literal string with no `int()`
  coercion anywhere in the path.

### CLI entry points

`scripts/factory_core/providers/cli.py` adds `python -m factory_core.tracker <verb>` /
`python -m factory_core.codehost <verb>` per parent spec §4.2's illustrative surface, backed by
`get_tracker()`/`get_codehost()` (which unconditionally return the GitHub adapters in this
ticket — env-based selection is step 3). This is new, additive surface; nothing existing calls
into it yet.

## Alternatives Considered

1. **Bundle step 1 and step 2 into #248** (also rewire `scheduler.sh`/`entrypoint.sh`/
   `smoke_gate.sh`/the run DAG to call the new CLIs now). Rejected: the parent spec explicitly
   scopes these as separate, independently-shippable steps specifically so that touching ~40
   live bash/YAML call sites is isolated behind its own parity-net-verified PR, not bundled with
   the higher-risk act of introducing the abstraction itself. Also matches this ticket's own
   acceptance criteria, which never mention bash/YAML file changes.
2. **Stub the degradable ops** (`get_rate_budget`/`get_status_limits`) rather than porting real
   logic. Rejected per Q4 — stubbing now would strand step 2 with no faithful implementation to
   route bash through, silently defeating rate-limit backoff and WIP ceilings the moment
   `scheduler.sh` is rewired, and gives the parity net nothing to verify for these two methods.
3. **Test opaque-ID handling against a real or fixture-mocked Jira adapter.** Rejected: no
   `JiraTracker` exists in this ticket and the acceptance criteria forbid introducing one; the
   opaque-string contract is a property of the ABC + GitHub reference adapter, provable without
   a second implementation.
4. **Fold new parity tests into `test_factory_core_board.py`.** Rejected per Q2 — the repo's
   test-naming convention mirrors the module under test, and `providers/` is a new module tree;
   keeping `test_factory_core_board.py` unchanged (and green) is itself part of proving the
   delegation didn't break anything.

## Open Questions (Non-blocking)

- The exact internal wiring between `board.py`'s retained public functions and `GitHubTracker`
  (does `board.py` become a thin delegator calling into `GitHubTracker`, or does `GitHubTracker`
  call into `board.py`'s retained low-level helpers?) is left to the implementation plan — both
  satisfy "zero behavior change to existing callers," and the choice doesn't affect the public
  contract, tests, or any other ticket in the sequence.
- Exact list of "representative current paths" for `GitHubCodeHost` parity tests (which of the
  ~12 YAML call sites + 11 `entrypoint.sh` call sites map to which of the 11 `CodeHost` methods)
  is an implementation-plan-level enumeration task, not re-derived here.

## Assumptions

- This ticket is dispatched to `Blocked` for human pairing at implementation time regardless of
  how tightly this spec scopes the work — `config/config.yaml`'s `dispatch_ceiling` policy parks
  any `size: L` ticket for human pairing independent of keyword match. This spec does not need to
  additionally hedge against fully-autonomous implementation risk.
- "Byte/argv-equivalent" in the acceptance criteria means the argv list passed to
  `subprocess.run` (or equivalent), not literal stdout/stderr byte equality — consistent with
  parent spec §2's "golden-argv tests" framing and the existing `test_factory_core_board.py`
  convention this ticket follows.
- No changes to `identity.py`'s `STATUS` dict, `.factory/adapter.yaml`, or
  `config/config.yaml` are needed for this ticket — canonical status vocabulary and env-var
  defaults are unchanged; provider *selection* (`FACTORY_TRACKER` etc.) is step 3.
