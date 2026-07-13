# Provider Abstraction — Jira Server/DC Tracker Adapter, Fixtures, and Shared Contract Suite

**Issue:** omniscient/dark-factory#251
**Status:** draft — pending review
**Parent epic:** omniscient/dark-factory#202
**Depends on:** omniscient/dark-factory#250 (provider selection + boot preflight — **OPEN,
labeled `needs-discussion`**, unmerged; see [Dependency State](#dependency-state) below)

---

## Overview / Problem Statement

`docs/provider-abstraction-design.md` (parent spec, PR #203) lays out a six-step sequence
(§11) to make Dark Factory's ticket-tracker, code-host, and model-endpoint providers
swappable. Steps 1–2 (#248, #249 — `Tracker`/`CodeHost` ABCs, `GitHubTracker`/`GitHubCodeHost`
reference adapters, golden-argv parity net, bash/DAG call-site rewiring) are merged to
`main`. Issue #251 is **step 4**: implement `JiraTracker` for Jira Server/Data Center, so a
second real tracker exists behind the `Tracker` seam and the abstraction is proven, not just
designed.

Two things must land together, because the acceptance criteria treat them as one unit:

1. `JiraTracker` — a `Tracker`-conformant adapter that talks to Jira Server/DC's REST API v2
   and JQL search, using opaque issue keys (`PROJ-123`) instead of GitHub's integer issue
   numbers.
2. A **shared, implementation-agnostic behavioral contract test suite** — which does not exist
   yet anywhere in the repo today (§10's "one shared abstract suite run against every Tracker
   implementation" is still just a design-doc goal) — that both `GitHubTracker` and
   `JiraTracker` pass, using recorded (hand-fixtured) HTTP/CLI responses so CI needs no live
   Jira server.

This ticket does **not** wire `JiraTracker` into the provider registry (`FACTORY_TRACKER=jira`
is not selectable after this ticket — see [Dependency State](#dependency-state) and Q3), does
not touch `GitHubTracker`'s existing golden-argv parity tests, does not implement live Jira
certification (explicit non-goal, deferred to a dependent validation ticket), and does not
touch `CodeHost`/`GitLab` or the model-endpoint axis.

## Dependency State

`Depends on: #250` gates *implementation* dispatch only (per `CLAUDE.md` conventions), not
this refinement pass, so this spec is written now regardless of #250's status. But #250's
actual state materially shapes this ticket's scope, so it is recorded here for the plan phase:

- **#248** ("Tracker/CodeHost contracts, GitHub reference adapters, parity net") — CLOSED,
  merged to `main`. Landed `scripts/factory_core/providers/tracker/base.py` (the frozen
  `Tracker` ABC) and `scripts/factory_core/providers/tracker/github.py` (`GitHubTracker`).
- **#249** ("route scheduler/entrypoint/smoke-gate/run-DAG through provider CLIs") — CLOSED,
  merged to `main`.
- **#250** ("provider selection + boot preflight") — **OPEN, labeled `needs-discussion`**, not
  merged. An implementation already exists on the unmerged branch
  `feat/issue-250-feat-providers---add-provider-selection-` (PR #265), which adds a
  `_TRACKERS = {"github": GitHubTracker}` registry dict to `providers/__init__.py` and a
  `Tracker.required_env() -> list[str]` classmethod (default `[]`) to the ABC. **Neither of
  these exists on `main` today** — `main`'s `providers/__init__.py` still hardcodes
  `GitHubTracker()` and the ABC has no `required_env()`.

Because #250 is stuck pending human discussion, this ticket cannot assume it will have landed
by the time #251 is implemented. See Q3/A3 below for how this shapes scope.

## Requirements

Distilled from the issue's scope/acceptance criteria and refined through Q&A (full log below):

1. `JiraTracker` (`scripts/factory_core/providers/tracker/jira.py`) implements all ten
   required `Tracker` ABC methods against `main`'s current (pre-#250) ABC shape:
   `list_work_items`, `get_item`, `get_comments`, `get_children`, `set_status`, `add_label`,
   `remove_label`, `upsert_comment`, `create_item`, `resolve_item` — plus the two degradable
   methods `get_status_limits`/`get_rate_budget` with documented safe defaults appropriate to
   Jira (WIP limits from adapter config, not Jira; rate budget is a no-op per parent spec §5.1).
2. `JiraTracker` also implements `required_env() -> list[str]` returning
   `["JIRA_BASE_URL", "JIRA_PROJECT_KEY", "JIRA_TOKEN", "JIRA_EPIC_LINK_FIELD"]` **on the
   class**, ready for #250 (or a trivial follow-up) to wire up — but this ticket does not add
   `required_env()` to the `Tracker` ABC itself (that is #250's change) and does not touch
   `providers/__init__.py`. If `Tracker.required_env()` is absent on `main` at implementation
   time, `JiraTracker.required_env()` ships as a plain method with no `@abstractmethod`/base
   dependency, callable once #250 lands and the ABC gains the hook.
3. Discovery via JQL (`GET /rest/api/2/search`, `project=<JIRA_PROJECT_KEY> AND status
   IN(...)`) — no Jira Agile board ID is used or required.
4. Issue keys (e.g. `PROJ-123`) are opaque strings end-to-end — no `int()` coercion anywhere.
5. `set_status(id, canonical)` is transition-ID-based: map canonical → Jira status name via
   `FACTORY_STATUS_*`, `GET /issue/{key}/transitions`, find the transition whose `to.name`
   matches (case-insensitive), `POST /issue/{key}/transitions` with its id. A missing/
   unreachable transition edge **fails soft**: no exception, status left unchanged, and an
   actionable message printed to stderr (matching the existing repo-wide convention —
   `print(f"jira: ...", file=sys.stderr)`, as used in `adapter.py`/`effective_config.py` — not
   a new logging framework).
6. Labels map to native Jira `labels` (`PUT /issue/{key}`); comments use idempotent
   marker-comment upsert (find-by-marker body scan, then update-in-place or create), matching
   `board.py`'s existing idempotency semantics exactly.
7. `get_children(epic_id)` discovers Epic-Link children via JQL
   (`"cf[<JIRA_EPIC_LINK_FIELD_NUMBER>]" = <epic key> AND ...`) and returns an
   **adapter-neutral shape** — `[{"id": str, "status": <canonical>, "labels": [str, ...]}, ...]`
   — consistent with how `get_item()` represents an item, **not** a fabrication of
   `GitHubTracker`'s raw GraphQL envelope (`state: OPEN/CLOSED`, `labels: {"nodes": [...]}}`).
   See Q4/A4 and [Known Limitation](#known-limitation-get_children-shape-divergence) below.
8. `create_item`/`resolve_item` map to Jira issue creation and the Done transition
   respectively (Jira has no GitHub-Projects-style automatic card-move-on-close, so
   `resolve_item` must explicitly perform the Done transition, per parent spec §6.4).
9. All Jira HTTP calls go through stdlib `urllib.request` behind a single internal request
   helper (one seam: method + path + body → parsed JSON) — no new pip dependency
   (`requests`/`httpx` rejected; see Q1/A1).
10. A new shared, implementation-agnostic behavioral contract test suite
    (`tests/test_tracker_contract.py`) is authored and runs its assertions against **both**
    `GitHubTracker` (retrofitted with its own small `gh`-argv fixture set, existing golden-argv
    parity tests untouched) and `JiraTracker` (with new hand-rolled JSON fixtures). See Q2/A2.
11. Jira fixtures are synthetic, checked-in JSON files (no live Jira base URL, project key, or
    token committed anywhere).
12. `python -m pytest tests/ -v` stays green, including all existing `GitHubTracker`
    golden-argv parity tests, unchanged.

## Brainstorming Q&A

> **Q1:** For `JiraTracker`'s Jira REST API v2 calls, should the implementation use Python's
> stdlib `urllib.request` (zero new dependencies, matching the exact pattern already used by
> `epic_autopilot.py`/`main_red_fixer.py`/`run_record.py` elsewhere in `scripts/factory_core/`),
> or should it add `requests` (or another third-party HTTP client) as a new pip dependency? And
> for the "recorded HTTP fixtures" the issue calls for, should these be hand-rolled JSON
> response fixtures fed through a monkeypatched request function (mirroring the exact pattern
> `test_provider_tracker_parity.py` already uses for `GitHubTracker`), or should we add a
> VCR-style cassette library (`vcrpy`/`responses`) as a new test dependency?
>
> **A1:** Use stdlib `urllib.request` and hand-rolled JSON fixtures via monkeypatch — no new
> dependencies, on both the runtime and test sides. This repo has deliberately avoided a
> third-party HTTP client (no `requirements.txt`/`pyproject.toml` exist; the Dockerfile's `pip
> install` line carries only `codeindex`, `pre-commit`, `pyyaml`; every existing outbound HTTP
> call in `scripts/factory_core/` already uses `urllib.request`). Keep the raw HTTP behind a
> small internal request helper (one method taking method/path/body, returning parsed JSON) so
> the I/O boundary is a single monkeypatchable seam — the direct analog of
> `test_provider_tracker_parity.py`'s `monkeypatch.setattr(subprocess, "run", fake)` idiom.
> Neither `vcrpy` nor `responses` exists in the repo; adding either would be a new test
> dependency for no functional gain over checked-in JSON fixtures loaded by the monkeypatched
> seam.

> **Q2:** Issue #251's acceptance criterion says `JiraTracker` must pass "the same contract
> suite as `GitHubTracker`" — but no such generic *behavioral* contract suite currently exists
> (only `test_provider_tracker_base.py`'s ABC-shape check, and `test_provider_tracker_parity.py`'s
> GitHub-only golden-argv tests, which are inherently GitHub-specific). Should #251's scope
> include authoring a new, implementation-agnostic behavioral contract test file
> (`tests/test_tracker_contract.py`) run against both `GitHubTracker` and `JiraTracker`? Or does
> the criterion mean something narrower — just Jira-specific fixture tests plus continuing to
> satisfy the existing ABC-shape check?
>
> **A2:** Yes — authoring a new, implementation-agnostic behavioral contract suite is in scope
> for #251, and it must run against both `GitHubTracker` and `JiraTracker`. The design doc is
> unambiguous the deliverable is a *shared* suite, not per-implementation tests (§10: "one
> shared abstract suite run against every Tracker... implementation"; the "how to add a
> tracker" checklist requires passing "the shared contract suite"). Today no such suite exists,
> so #251 is the ticket that brings it into being. The suite asserts behavior through the
> `Tracker` interface only (transport-agnostic) — each implementation is exercised through its
> own fixture seam (`subprocess.run` monkeypatch for `GitHubTracker`, the `urllib.request`
> helper monkeypatch for `JiraTracker`), parametrized via a pytest fixture yielding
> `(tracker_instance, fixture_controller)` per implementation. `GitHubTracker`'s existing parity
> tests are not touched or removed; the new file sits alongside them. Assertions anchor to
> semantics the design doc already freezes: idempotent marker-comment upsert (update-in-place,
> no duplicate on a second call with the same marker), canonical status vocabulary mapping,
> `list_work_items` status/label filtering, label add/remove, and safe no-op when a status
> transition/target is unreachable.

> **Q3:** Should #251's implementation plan include registering `JiraTracker` in the provider
> registry (adding `"jira": JiraTracker` to the `_TRACKERS` dict, wiring `required_env()` so
> `FACTORY_TRACKER=jira` is actually selectable end-to-end), even though that registry file
> (`providers/__init__.py`) is the literal scope of #250, which is currently blocked on
> `needs-discussion` and unmerged? Options: (a) `JiraTracker` ships fully standalone
> (importable, ABC-conformant, contract-suite-passing) without touching
> `providers/__init__.py`, leaving registry wiring to #250 or a later ticket; (b) assume #250
> will have landed by #251's implementation time and include the registry addition as part of
> this ticket's scope.
>
> **A3:** (a) — build `JiraTracker` fully standalone; #251 must not touch
> `providers/__init__.py`. The `_TRACKERS` dict and `Tracker.required_env()` do not exist on
> `main` today (verified directly) — they live only on the unmerged #265 branch. Option (b)
> would force #251 to itself introduce #250's entire env-selection refactor, collide head-on
> with the in-flight PR, and mutate a gate-adjacent frozen ABC out of scope — a direct violation
> of `CLAUDE.md` scope discipline ("touch only what the plan lists; the conformance gate excises
> out-of-scope changes"). Concretely: ship `JiraTracker` importable and ABC-conformant against
> `main`'s current `Tracker` shape, passing the new contract suite; implement
> `JiraTracker.required_env()` on the class (ready, but unwired, since nothing calls it yet);
> document final registry wiring as deferred to #250 or a trivial follow-up once #250 lands.

> **Q4:** `get_children()` has no documented adapter-neutral return shape in the ABC, and its
> only real caller (`epic_autopilot.py`'s `_sub_issue_numbers`, **disabled by default** —
> `config.yaml`'s `epic_autopilot.enabled: false`) is hard-coupled to `GitHubTracker`'s raw
> GraphQL envelope (`state: "OPEN"/"CLOSED"`, `labels: {"nodes": [{"name": ...}]}`, `number`).
> Should `JiraTracker.get_children()` (a) fabricate that GitHub-shaped envelope purely so it
> would coincidentally also work if `epic_autopilot` were ever pointed at Jira, or (b) return an
> adapter-neutral/Jira-native shape (matching `get_item()`'s conventions) and treat
> `epic_autopilot.py`'s GitHub-shape coupling as a documented, separately-tracked gap, since
> `epic_autopilot.py` is not in #251's stated scope and is off by default?
>
> **A4:** (b) — return the adapter-neutral shape;
> `[{"id": "PROJ-124", "status": "<canonical>", "labels": ["ready-for-agent", ...]}, ...]`,
> consistent with how `get_item()`/`list_work_items()` already represent items. Fabricating
> GitHub's raw GraphQL envelope inside `JiraTracker` bakes one adapter's transport format into
> another permanently, to placate a caller that is disabled by default and isn't even wired to
> Jira (registry work is #250's, per Q3). `epic_autopilot.py`'s GitHub-envelope coupling is
> pre-existing and out of #251's stated scope (the issue's scope list does not mention
> `epic_autopilot.py`); fixing it is a separately-tracked follow-up (see
> [Known Limitation](#known-limitation-get_children-shape-divergence)). The shared contract
> suite's `get_children()` assertions are written at the behavioral level common to both
> implementations (returns a list; returns the epic's actual children; empty list when none) —
> it does not assert a byte-identical field schema across trackers, since `GitHubTracker`'s
> current envelope and the adapter-neutral shape are legitimately different representations of
> the same operation, and forcing them identical would require an out-of-scope `GitHubTracker`
> change.

## Architecture / Approach

**`scripts/factory_core/providers/tracker/jira.py`** — one new module, mirroring
`github.py`'s structure:

- `_JiraClient` (or equivalent private helper) — the single `urllib.request`-based seam:
  `_request(method, path, params=None, json_body=None) -> dict`. Builds the request with
  `Authorization: Bearer $JIRA_TOKEN` (or Basic, per Jira Server/DC convention — a plan-phase
  decision informed by which auth Jira Server/DC actually requires; Data Center commonly uses
  PAT bearer tokens), raises on non-2xx with the response body captured for the stderr log
  message, and is the one function the fixture tests monkeypatch.
- `_CANONICAL_TO_JIRA_STATUS` / `_JIRA_STATUS_TO_CANONICAL` — built from `FACTORY_STATUS_*`
  env vars at construction time, mirroring `GitHubTracker`'s
  `_CANONICAL_STATUS_NAMES`/`_STATUS_NAME_TO_CANONICAL` pattern, except values are Jira status
  *names* (strings) rather than GitHub project-field option IDs (per parent spec §4: "Jira
  status names under the Jira adapter").
- Each `Tracker` method issues one or more `_request()` calls:
  - `list_work_items` → `GET /rest/api/2/search` with a JQL string built from
    `JIRA_PROJECT_KEY` + mapped status names (+ label filter if `labels` passed).
  - `get_item`/`get_comments` → `GET /rest/api/2/issue/{key}` (comments via `expand=comment` or
    the dedicated `/comment` sub-resource — plan-phase to confirm which Jira Server/DC REST v2
    shape is simplest).
  - `get_children` → JQL `"cf[<field-number-from-JIRA_EPIC_LINK_FIELD>]" = <epic key> AND
    project=<JIRA_PROJECT_KEY>`, mapped to the adapter-neutral shape (Requirement 7).
  - `set_status` → `GET /issue/{key}/transitions` then `POST /issue/{key}/transitions`
    (Requirement 5's fail-soft transition lookup).
  - `add_label`/`remove_label` → read-modify-write `PUT /issue/{key}` on the `labels` field
    (Jira has no atomic add/remove-label endpoint in REST v2).
  - `upsert_comment` → `GET /issue/{key}/comment`, scan bodies for the marker (matching
    `board.py`'s existing scan-then-PATCH/POST idiom), then `PUT` or `POST` accordingly.
  - `create_item` → `POST /issue`; `resolve_item` → transition to the status mapped from
    canonical `done`.
  - `get_status_limits` → returns `{}` (WIP limits come from adapter config per parent spec
    §5.4, not from Jira — Jira has no per-status limit concept); `get_rate_budget` → returns
    the ABC's default no-op dict (`{"remaining": None, "reset": None, "used": None, "limit":
    None}`) since Jira Server/DC exposes no standard rate-budget endpoint (parent spec §5.1:
    "no-op").

**`tests/test_tracker_contract.py`** (new) — the shared behavioral suite (Requirement 10),
parametrized over `(GitHubTracker, github_fixture_controller)` and
`(JiraTracker, jira_fixture_controller)`. Each fixture controller is a small monkeypatch
harness that stubs the implementation's I/O seam (`subprocess.run` for GitHub, `_request` for
Jira) and returns canned data. Assertions cover the requirements enumerated above at the
`Tracker` interface level, not transport-level argv/URL shape (that stays in each
implementation's own parity/fixture file).

**`tests/fixtures/jira/*.json`** (new) — synthetic recorded responses: a search-result page, a
single issue, a transitions list, a comment thread, an Epic-Link JQL result — loaded by
`test_tracker_contract.py`'s Jira fixture controller and by `JiraTracker`-specific unit tests
for request-shape assertions (JQL string correctness, transition-id selection logic, marker
scan-before-post).

## Known Limitation: `get_children()` Shape Divergence

Per Q4/A4: `GitHubTracker.get_children()` returns a raw GitHub GraphQL envelope
(`{"number", "state": "OPEN"/"CLOSED", "labels": {"nodes": [...]}}`); `JiraTracker.get_children()`
returns an adapter-neutral shape (`{"id", "status": <canonical>, "labels": [str]}`). The sole
consumer, `epic_autopilot.py`'s `_sub_issue_numbers`, is hard-coded to the GitHub shape and
would silently treat every Jira child as filtered-out if pointed at `FACTORY_TRACKER=jira`
today. This is not a regression introduced by #251 — `epic_autopilot` is disabled by default
(`config.yaml`: `epic_autopilot.enabled: false`) and is not wired to Jira regardless (registry
selection is #250's scope, itself unmerged). **Follow-up recommendation:** a small ticket to
normalize `GitHubTracker.get_children()` to the adapter-neutral shape and update
`_sub_issue_numbers` accordingly, once #250 lands and epic autopilot's Jira compatibility
becomes a live concern.

## Alternatives Considered

1. **Third-party HTTP client (`requests`) + VCR-style cassette library (`vcrpy`).** More
   ergonomic request-building and industry-standard fixture recording. Rejected (Q1/A1): the
   repo has zero third-party HTTP/test dependencies today by deliberate pattern (Dockerfile,
   existing `urllib.request` call sites); adding two new dependencies for a single adapter
   cuts against that and the parity-invariant goal of minimizing surface touched by this axis
   of work.
2. **Jira Agile Board API for discovery**, mirroring GitHub Projects' board-item model more
   directly. Rejected per parent spec §5.4: requires a board ID (extra required config, extra
   coupling to a specific Jira product edition/config), where plain JQL search against
   `JIRA_PROJECT_KEY` needs no board at all and is simpler for an operator to stand up.
3. **Retrofit `GitHubTracker.get_children()` to the adapter-neutral shape within #251**, so both
   implementations share one schema from day one and the contract suite could assert byte-
   identical shape. Considered in Q4 as the "clean" resolution, but rejected in favor of the
   documented-limitation path: it would require touching `GitHubTracker`/`epic_autopilot.py`,
   neither of which is in #251's stated scope, risking conformance-gate excision and expanding
   a `size: L` ticket further into territory with its own (disabled-by-default, so low-urgency)
   blast radius.

## Open Questions (Non-blocking)

- Exact Jira Server/DC auth scheme (`Bearer` PAT vs. Basic auth with an API token) — a
  plan-phase detail; both are supported by adding the right header in `_request()`, and neither
  changes the adapter's external contract.
- Whether Jira Server/DC REST v2 exposes comments via `/issue/{key}?expand=comment` or the
  separate `/issue/{key}/comment` resource more conveniently for this adapter's needs — a
  plan-phase implementation detail, not a design fork.
- The exact custom-field-number JQL syntax (`cf[10008]` vs. a quoted field name) for Epic-Link
  discovery — depends on the specific `JIRA_EPIC_LINK_FIELD` value format documented by the
  operator; the plan phase should confirm against Jira Server/DC's JQL reference.

## Assumptions

- `FACTORY_STATUS_*` env vars will hold Jira status *names* (e.g. `"In Progress"`) under
  `FACTORY_TRACKER=jira`, per parent spec §4 — not Jira internal status IDs.
- `JIRA_EPIC_LINK_FIELD` is supplied by the operator as Jira's custom-field identifier for Epic
  Link (e.g. `customfield_10008`), consistent with parent spec §5.4's example.
- Live Jira workflow transition-edge configuration (ensuring the seven canonical status moves
  are all reachable in the target Jira project's workflow) is an operator setup concern,
  documented as a prerequisite, not validated by this ticket — live validation is explicitly
  the dependent ticket's job (issue's stated non-goal).
- No changes to `Tracker`/`CodeHost` ABCs, `providers/__init__.py`, `GitHubTracker`,
  `GitHubCodeHost`, or `epic_autopilot.py` are needed to satisfy #251's acceptance criteria, per
  Q3/A3 and Q4/A4.
