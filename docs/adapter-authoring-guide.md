# Adapter Authoring Guide

This is the requirements checklist for adding a new Tracker, CodeHost, or model-endpoint
adapter to Dark Factory (expands `docs/provider-abstraction-design.md` §12). Every table below
is sourced directly from that design doc's numbered sections — cited inline — so this guide and
the design doc cannot silently drift; if they disagree, the design doc is authoritative.
That convention covers design intent. The design doc predates several implementation
choices, so where either document disagrees with the code under
`scripts/factory_core/providers/`, the code is the contract an adapter has to satisfy.

## Overview

Dark Factory has three independent, pluggable provider axes (`docs/provider-abstraction-design.md`
§1, §4.1), all living under `scripts/factory_core/providers/`:

```
providers/
  __init__.py          # get_tracker(), get_codehost(), preflight() — env-based selection
  tracker/base.py       # Tracker ABC
  tracker/github.py     # GitHubTracker — reference implementation
  tracker/jira.py       # JiraTracker — Jira Server/Data Center, registered
  codehost/base.py      # CodeHost ABC
  codehost/github.py    # GitHubCodeHost — reference implementation
  codehost/gitlab.py    # GitLabCodeHost — seam proof, not registered (see worked example)
  model.py               # model-endpoint preflight checks (no behavioral ABC)
  cli.py                 # thin CLI: python scripts/factory_core/providers/cli.py tracker|codehost|preflight
```

Selection is env-driven, each defaulting to today's GitHub-only behavior (design doc §4):
`FACTORY_TRACKER` (default `github`), `FACTORY_CODEHOST` (default `github`),
`FACTORY_MODEL_PROVIDER` (default `anthropic`). A name is selectable only once it is in the
registry in `providers/__init__.py` (`_TRACKERS` holds `github` and `jira`; `_CODEHOSTS` holds
`github`); any other value fails boot preflight as unknown.

Every adapter's own connection config and secrets live in gitignored env files — **never**
in the committed adapter module. There are two such files, and a non-default provider needs
its selection variable and its provider env in **both**:

- `deploy/instance.env` configures the scheduler container only.
- `<PROJECT_DIR>/.archon/.env` is the `env_file` of every dispatched run container
  (`run-compose.yml`). Run containers do not read `deploy/instance.env`, and
  `run-compose.yml`'s `environment:` block passes the identity and `FACTORY_STATUS_*`
  variables through from the scheduler but not `FACTORY_TRACKER`, `FACTORY_CODEHOST`,
  `FACTORY_MODEL_PROVIDER` or any provider-specific variable such as `JIRA_*`.

Both the scheduler (`scheduler.sh`) and every run container (`entrypoint.sh`) run the same
boot preflight against their own environment. A provider selected only in
`deploy/instance.env` therefore drives the scheduler while run containers stay on the
`github`/`anthropic` defaults, and nothing reports the mismatch when the default credentials
are present ([#489](https://github.com/omniscient/dark-factory/issues/489)).

## Tracker adapter

### Required methods (design doc §5.1)

| Method | Purpose | Degradable? |
|---|---|---|
| `list_work_items(statuses, labels?)` | poll-loop discovery | no |
| `get_item(id)` | title/body/state/labels/status | no |
| `get_comments(id)` | read comment thread | no |
| `get_children(epic_id)` | epic → children | no |
| `set_status(id, canonical)` | move to canonical status | no |
| `add_label(id, name)` / `remove_label(id, name)` | label state machine | no |
| `upsert_comment(id, marker, body)` | idempotent marker comment | no |
| `create_item(title, body, labels)` → id | regression tickets | no |
| `resolve_item(id)` | explicit close-on-merge | no |
| `get_status_limits()` → `{status: n}` | WIP limits | **yes** — safe default `{}` |
| `get_rate_budget()` | throttle poll loop | **yes** — safe default `{"remaining": None, "reset": None, "used": None, "limit": None}` |
| `required_env()` (classmethod) → `list[str]` | env var names boot preflight requires to be set | **yes** — safe default `[]` |

All ids are **opaque strings** everywhere (design doc principle 5) — never coerce to `int`
anywhere in a conforming implementation, including in test fixtures.

Return contracts (`tracker/base.py`):

- `set_status`, `add_label` and `remove_label` return `bool`. `set_status` returns `False`,
  and must not raise, when the item is not on the board or has no valid transition to the
  target status. How an operation *failure* is signalled differs per provider:
  `GitHubTracker` returns `False` on a failed `gh` call, while `JiraTracker` lets the
  `RuntimeError` from its HTTP layer propagate and its `add_label`/`remove_label` never
  return `False`. Callers that need attempt-all-then-report behaviour catch `RuntimeError` as
  well as checking the return value.
- `create_item` returns the new item's opaque id; `upsert_comment` and `resolve_item`
  return nothing.

**`get_item` takes `fields`.** The ABC declares `get_item(id, fields: tuple | None = None)`,
which is what `providers/cli.py`'s `tracker get` calls. `fields` is a tuple of field names
such as `("body",)` or `("state",)`; the result contains exactly those keys, and `None` means
the adapter's default set. `state` is `"OPEN"` or `"CLOSED"` (`scheduler.sh` reads `body` and
`state` for `Depends on:` gating). `JiraTracker` returns `CLOSED` iff the canonical status is
`done`, and fetches `comments` only when asked.

### Canonical vocabulary (frozen contract, design doc §5.2)

- **Seven canonical statuses:** `ready, in_progress, in_review, blocked, done, backlog, refined`.
  Your adapter maps each to its own representation via the seven `FACTORY_STATUS_*` env vars
  (`FACTORY_STATUS_READY`, `FACTORY_STATUS_IN_PROGRESS`, `FACTORY_STATUS_IN_REVIEW`,
  `FACTORY_STATUS_BLOCKED`, `FACTORY_STATUS_DONE`, `FACTORY_STATUS_BACKLOG`,
  `FACTORY_STATUS_REFINED`) — GitHub uses single-select option IDs; `JiraTracker` uses
  status *names* under the same seven variables. No new mapping surface is introduced per adapter.
- **Required label vocabulary:** `ready-for-agent, spec-pending-review, plan-pending-review,
  needs-discussion, factory-regression, above-ceiling, direct-to-pr, epic, ready-for-human,
  merged-with-edits, regression`. Your tracker must support hyphenated labels.
- **Labels beyond that list.** The list above is the design doc's frozen set, not everything
  the factory touches. `scripts/bootstrap_target.sh`'s `LABELS` array is the list actually
  provisioned on a new GitHub target, and it also carries `spec-approved`, `needs-triage`,
  `needs-info`, `no-autopilot`, `scope-spillover`, `cost-report-lost`, `size: S|M|L|XL` and
  `priority: must-have|should-have|could-have`. Not every label is hyphen-only: the
  scheduler reads the size label with a colon and an optional space
  (`scripts/scheduler_lib.sh`, pattern `size: ?(xl|[sml])`), so a tracker has to round-trip
  label names containing `:` and a space, or map them. Two labels the code uses are in
  neither list and are not created by the bootstrap script: `manifest-intake` (applied by
  handoff intake, below) and `merged-with-edits` (read by `scripts/fetch_scorecard.py`).

### Required env (per-adapter example: Jira, design doc §5.4)

`JiraTracker.required_env()` returns `JIRA_BASE_URL`, `JIRA_PROJECT_KEY`, `JIRA_TOKEN`
(secret) and `JIRA_EPIC_LINK_FIELD`; boot preflight fails when any of the four is unset.
The seven `FACTORY_STATUS_*` vars are optional for Jira and hold status *names*, defaulting
to `Ready`, `In progress`, `In review`, `Blocked`, `Done`, `Backlog` and `Refined`; names
are compared case-insensitively. Two more optional variables exist: `JIRA_ISSUE_TYPE`
(default `Task`, the issue type `create_item` uses) and `JIRA_REQUEST_TIMEOUT_SECONDS`
(default `30`). Your adapter documents its own equivalent list following this shape: base
URL, project/namespace key, auth token, any custom-field mapping the axis needs, and the
seven status vars. Whatever `required_env()` returns is what preflight enforces.

### Test bar

1. Contract tests — parametrize your `Tracker` into the shared contract-test suite at
   `tests/test_tracker_contract.py`, which already runs `GitHubTracker` and `JiraTracker`
   through the same assertions against in-memory fixture backends (the same structural and
   opaque-ID pattern as the `CodeHost` harness at `tests/test_provider_codehost_contract.py`).
2. Live smoke checklist (run once against a real instance before shipping): create → label →
   comment → transition through all seven canonical statuses → resolve.

## Code-host adapter

### Required methods (design doc §6.1)

The design doc tables 11 methods; the ABC in `codehost/base.py` declares 12 abstract
methods, because `find_change_details` was added after the design. A subclass that
implements only the design doc's 11 cannot be instantiated.

| Method | Purpose |
|---|---|
| `remote_url()` | auth-embedded clone/push URL |
| `find_change_for(branch)` → id or `None` | PR/MR open for a branch (or branch prefix) |
| `find_change_details(branch, exact=False, repo=None)` → `{number, isDraft, mergeable}` or `None` | same lookup, returning draft and mergeable state |
| `open_change(source, target, title, body, draft)` → id | create PR/MR |
| `update_change_body(id, body)` → bool | backfill close-keyword |
| `mark_ready(id)` | draft → ready for review |
| `merge_change(id, strategy, delete_branch)` → bool | merge |
| `get_change_checks(id)` → `[{name, bucket, ...}]` | CI gate |
| `get_change_mergeable(id)` → enum | conflict gate |
| `get_change_reviews(id)` → state | approval gate |
| `get_change_inline_comments(id)` → list | review feedback |
| `close_keyword(issue_id)` → str | close-on-merge snippet **iff tracker == host**, else `""` |

`required_env()` is a non-abstract classmethod with the same meaning as on `Tracker`: the
env var names boot preflight requires (default `[]`).

Plain `git` (clone/branch/commit/push/fetch/diff) is host-agnostic and stays **outside** this
contract (design doc principle 3) — the only git-adjacent method here is `remote_url()`.

**The ABC declares every keyword the CLI passes.** `providers/cli.py` calls
`find_change_for(branch, exact=, repo=)`, `find_change_details(branch, exact=, repo=)`,
`open_change(..., draft=, repo=)`, `mark_ready(id, repo=)`,
`merge_change(id, strategy=, delete_branch=, repo=)`, `get_change_checks(id, fields=, repo=)`,
and `repo=` on `get_change_mergeable`, `get_change_reviews` and
`get_change_inline_comments`, so every implementation must accept them, with the ABC's
defaults: `repo` is an optional `owner/name` override, `exact` selects an exact branch match
instead of a prefix match, and `fields` is a comma-separated string of check fields (default
`name,bucket,link`). Parameter names must match the ABC (the change id parameter is `id`).
`tests/test_provider_signature_conformance.py` enforces this with `inspect.signature`, and the
contract suites call each verb through the CLI's argument shape.

### `remote_url()` — auth-embedded URL requirement

Must embed the token directly in the URL so plain `git push`/`fetch` authenticate without any
credential helper. GitHub's form: `https://$TOKEN@github.com/<slug>.git`. GitLab's form:
`https://oauth2:$TOKEN@gitlab.com/<slug>.git` (see `GitLabCodeHost.remote_url()` below).

### Draft / ready / merge / checks / reviews mapping expectations

Map your host's terms onto the interface's neutral names — e.g. GitLab: MRs replace PRs, a
`Draft:` title prefix replaces the draft flag, the pipelines API replaces checks, the approvals
API replaces reviews (design doc §6.3).

### `close_keyword()` contract

Returns the body snippet that auto-closes an issue on merge (e.g. `"Closes #42"`) **only when
your code host is also the tracker** (design doc §6.4) — otherwise it must return `""`. Getting
this wrong emits a dead close-keyword the tracker can never see.

That is the contract for a new adapter. The shipped code does not yet exercise it:
`GitHubCodeHost.close_keyword` returns `Closes #N` without checking `FACTORY_TRACKER`, and
nothing calls `close_keyword` — the workflow's PR-creation and backfill steps and
`scripts/factory_core/main_red_fixer.py` write `Closes #N` into the PR body directly.

### Test bar

Parametrize your `CodeHost` into `tests/test_provider_codehost_contract.py`, following the
`GitLabCodeHost` pattern below: pure-mapping methods (no HTTP dependency) run for real and must
pass; methods that require live host I/O may raise `NotImplementedError` during initial seam
work and are proven, via an executing test, to raise *that* exception on an opaque id — not
some other crash — until a full implementation replaces the stub.

## Model-endpoint adapter

### Native fast paths vs. gateway path (design doc §7.2)

- **Native (no gateway):** `anthropic` (default), `bedrock`, `vertex` — Claude Code speaks these
  natively via `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` or the `CLAUDE_CODE_USE_BEDROCK`/
  `CLAUDE_CODE_USE_VERTEX` flags. Endpoint-swapping is configuration, not a harness code change.
- **Gateway path (designed, not implemented):** `databricks` and `openai` don't speak the
  Anthropic Messages shape, so the design has a reference gateway present `/v1/messages` and
  route to the real backend, with `ANTHROPIC_BASE_URL` → the gateway, `ANTHROPIC_AUTH_TOKEN`
  → the gateway key, and real backend secrets in the gateway config, never in the factory.
  No gateway ships: selecting either provider fails boot preflight with "requires the model
  gateway, which is not yet implemented" (`providers/model.py`). The opt-in
  `factory_core.model_proxy` service in `run-compose.yml` is a pass-through to
  `api.anthropic.com` with a request ledger; it does no translation or alias routing (see
  `docs/dark-factory-model-proxy-rollout.md`).

### Model-alias mapping (design doc §7.3)

Phase commands and the workflow name models by alias (`opus`, `sonnet`, `haiku`, `fable`).
`run-compose.yml` pins each alias to a concrete model ID for every run container through
`ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`,
`ANTHROPIC_DEFAULT_HAIKU_MODEL` and `ANTHROPIC_DEFAULT_FABLE_MODEL`, each overridable from
the scheduler's environment; that file is the place to read the current IDs. The design's
per-provider mapping — one gateway config file translating each tier to a backend model,
with `anthropic` as pass-through, `databricks` routing to the governed Claude endpoint and
`openai` to the chosen GPT model — belongs to the unimplemented gateway, and the
`deploy/gateway/config.yaml` the design doc names does not exist.

### Cost/quality caveats (design doc §7.5)

- **Cost goes approximate off-Anthropic.** Token counts still flow; dollar figures are
  mislabeled once the backend isn't Anthropic-billed.
- **GPT quality is empirical**, not assumed equivalent to Claude — Databricks-Claude is
  risk-free (identical model, different endpoint); OpenAI is a measured, separate question.

### Preflight requirements

`providers/model.py` checks environment variables only; it makes no network call.

- `anthropic` → `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` is set.
- `bedrock` → `CLAUDE_CODE_USE_BEDROCK` and `AWS_REGION` are set, plus either a static key
  pair or one of the env signals of an AWS credential chain (profile, role, container
  credentials).
- `vertex` → `CLAUDE_CODE_USE_VERTEX`, `ANTHROPIC_VERTEX_PROJECT_ID` and `CLOUD_ML_REGION`
  are set, plus `GOOGLE_APPLICATION_CREDENTIALS` or an env signal of workload identity.
- `databricks` / `openai` → always fail (gateway not implemented, above).

Boot-time preflight is **hard-fail** (design doc §9) — a misconfigured instance must exit
loudly at startup, not fail mid-run. It runs in the scheduler and again in every run
container, each against its own environment (see Overview).

## Cross-axis concerns

- **Safe failure / fail-open posture (design doc §9).** Degradable tracker ops
  (`get_rate_budget`, `get_status_limits`) return safe defaults. A `set_status` with no
  valid transition edge logs, returns `False` and leaves the item where it is — same posture
  as a failed board-move today. A transport or API error is provider-dependent (returned as
  `False` by `GitHubTracker`, raised as `RuntimeError` by `JiraTracker`; see the Tracker
  return contracts above). Boot-time provider preflight is the one hard-fail exception.
- **Idempotency.** `upsert_comment(id, marker, body)` must be a true upsert (create if the
  marker-tagged comment is absent, update in place otherwise) so re-runs never spam a tracker.
- **Secret handling.** Provider secrets live only in the two gitignored env files —
  `deploy/instance.env` for the scheduler and `<PROJECT_DIR>/.archon/.env` for run
  containers (see Overview) — never committed, never hardcoded in an adapter module.
- **Rollback.** Reverting a bad adapter is reverting its selection env var
  (`FACTORY_TRACKER` / `FACTORY_CODEHOST` / `FACTORY_MODEL_PROVIDER`) back to its default in
  both env files — no code rollback required if the adapter module itself is otherwise inert
  when unselected. The scheduler copies `<PROJECT_DIR>/.archon/.env` into place once, at
  startup, so restart it after editing either file.
- **Mixed-provider close flow (design doc §6.4).** When tracker and code host are the *same*
  provider (e.g. both GitHub), `close_keyword()` in the PR/MR body auto-closes the issue for
  free. When they differ (e.g. Jira tracker + GitHub host), there is no such automatic link, so
  the orchestrator performs the close explicitly and in order:

  ```
  host.merge_change(id) succeeds  →  tracker.resolve_item(issue_id)
  ```

  This is exactly why `close_keyword()` must return `""` when `tracker != host`: emitting a
  dead `"Closes #N"` snippet the tracker can never observe would silently break this invariant.

  The explicit close is the designed flow, not shipped behaviour. No caller invokes
  `resolve_item` (the `tracker resolve` CLI verb exists but is unused) or `close_keyword`,
  and the workflow hardcodes `Closes #N` in the PR body, so a tracker other than the code
  host is not resolved on merge today
  ([#489](https://github.com/omniscient/dark-factory/issues/489) lists this among the
  provider-wiring gaps).

## Worked example: GitLab CodeHost seam proof

`scripts/factory_core/providers/codehost/gitlab.py` implements `GitLabCodeHost`, a real,
importable `CodeHost` subclass that proves the ABC (§6.1) is not GitHub-shaped, without a live
GitLab instance. It is parametrized into the shared contract suite at
`tests/test_provider_codehost_contract.py` alongside `GitHubCodeHost`, and has its own unit
tests at `tests/test_provider_codehost_gitlab.py`.

The class is importable but **not selectable**: `gitlab` is not in the `_CODEHOSTS` registry
in `providers/__init__.py`, so `FACTORY_CODEHOST=gitlab` fails boot preflight with
`Unknown FACTORY_CODEHOST 'gitlab'`. Its `required_env()` returns `["GITLAB_TOKEN"]`.

| `CodeHost` method | GitLab equivalent (design doc §6.3) | This stub |
|---|---|---|
| `remote_url()` | `https://oauth2:$TOKEN@gitlab.com/<slug>.git` | **Real** — `GITLAB_TOKEN` + `GITLAB_BASE_URL` (a bare host name, defaults to `gitlab.com`) |
| `close_keyword(issue_id)` | `"Closes #N"` iff GitLab is also the tracker, else `""` | **Real** — returns the snippet only when `FACTORY_TRACKER` is `gitlab`; no `gitlab` tracker is registered, so it returns `""` in every configuration that boots today |
| `find_change_for(branch)` | MR list `?source_branch=` | `NotImplementedError` |
| `find_change_details(branch, exact, repo)` | MR list `?source_branch=`, with draft and merge status | `NotImplementedError` |
| `open_change(...)` | `POST /merge_requests` with `Draft:` title prefix | `NotImplementedError` (the `Draft:`-prefix mapping itself is proven separately by the real, unit-tested `_draft_title`/`_strip_draft_prefix` helpers) |
| `update_change_body(id, body)` | `PUT /merge_requests` | `NotImplementedError` |
| `mark_ready(id)` | remove `Draft:` prefix | `NotImplementedError` |
| `merge_change(id, ...)` | `PUT /merge_requests/{id}/merge` | `NotImplementedError` |
| `get_change_checks(id)` | pipelines API | `NotImplementedError` |
| `get_change_mergeable(id)` | `merge_status`/`has_conflicts` | `NotImplementedError` |
| `get_change_reviews(id)` | approvals API | `NotImplementedError` |
| `get_change_inline_comments(id)` | discussions API | `NotImplementedError` |

Every id-taking method accepts an **opaque string** shaped like a real GitLab id
(`"group/project!42"`, never coerced to `int`) via the private `_validate_change_id` helper —
this, plus the two real pure-mapping methods above, is the executable proof the acceptance
criterion asks for. A full, live-validated GitLab implementation (real HTTP calls, tested
against a live GitLab instance) is explicitly out of scope for this guide's ticket and is filed
as a separate follow-up only if requested.

## Handoff manifest (A5)

A loop entry's `handoff.manifest` field (A1.5) points at a flat YAML file the target loop's own
tooling already wrote. Unlike `verification.verifier`, the manifest is **never executed** —
`scripts/factory_core/handoff.py` reads and validates it, never runs it. (The A1.5 spec's
`handoffs/triage_handoff.py`-shaped example predates this decision and is a stale illustration
in the archived spec; this section is the current, authoritative shape.)

### Schema

```yaml
schema_version: 1
artifact_id: scan-2026-08-30-001            # non-empty string, opaque, ^[A-Za-z0-9._-]+$, <=128 chars
producing_loop: nightly-scan-triage         # same charset and length limit; must match a loops[].name in .factory/adapter.yaml
side_effect_level: 2                        # int 1-5; must equal that loop's declared side_effect_level
verifier_verdict:                           # OPTIONAL, informational only -- never gated on
  path: artifacts/scan_verdict.md
source_references:                          # list of strings, <=50 items, <=512 chars each
  - scanner_output.json
acceptance_thresholds:                      # list of strings, same limits
  - "false_positive_rate < 0.05"
proposed_ticket:
  title: "Triage: 3 new findings in payments module"   # <=200 chars, no newlines/control chars
  body: |                                                # <=32 KiB, no fence lines, no provenance marker
    ## Findings
    ...
```

Unknown top-level keys (and unknown keys inside `verifier_verdict`/`proposed_ticket`) are a hard
rejection. The manifest file itself is capped at 256 KiB, checked before YAML parsing.

### Intake path

`scripts/factory_core/handoff.py intake` runs: R2 (schema validation) -> R3 (cross-check
`producing_loop`/`side_effect_level` against the adapter's `loops:` entries) -> R4 (runs the
loop's declared A3 verifier itself via `verifier.resolve_and_run` and gates on `STATUS: PASS`
only -- never trusts a verdict file the manifest merely references) -> R5 (creates a GitHub
issue via the existing `tracker create` primitive, labeled exactly `needs-triage,manifest-intake`
-- never `ready-for-agent`) -> R6 (records an accept/reject row to `runs.jsonl` via
`run_record.cmd_record`, in-process, for every manifest processed).

The created issue's title is the manifest's `proposed_ticket.title` prefixed with
`[intake] `. The second label is `FACTORY_MANIFEST_LABEL` (default `manifest-intake`); an
override must be a single token with no comma or whitespace and must not contain
`ready-for-agent`, `-pending-review`, `direct-to-pr` or the configured `DIRECT_TO_PR_LABEL`
value, otherwise intake fails with `internal_error`. `scripts/bootstrap_target.sh` does not
create `manifest-intake`, and `GitHubTracker.create_item` passes labels straight to
`gh issue create --label`, so create the label on the target before the first intake.

`scripts/factory_core/handoff.py validate --manifest-path <path>` runs the schema checks
alone (R2) and prints `manifest OK` or `manifest INVALID [<code>]: <message>`; it creates
nothing and writes no `runs.jsonl` row.

### Reason codes

| Code | Meaning |
|---|---|
| `schema_invalid` | Shape/type/required/unknown-key/file-size violation; also a manifest path that is absolute, escapes `CLONE_DIR`, or names a missing file |
| `unsafe_string` | A string rendered outside a fence contains a backtick or newline |
| `body_contains_fence` | `proposed_ticket.body` contains a fence line or the provenance closing marker |
| `unknown_producing_loop` | `producing_loop` matches no `loops[].name` in the adapter; also raised when `.factory/adapter.yaml` itself fails to load |
| `side_effect_level_mismatch` | Manifest's declared level != the loop's declared level |
| `producing_loop_factory_owned` | Loop's declared level >= 4 (factory-owned until #196) |
| `verifier_undeclared` | Loop entry has no `verification.verifier` to run |
| `verdict_not_passing` | Intake-produced verdict `STATUS` != `PASS` |
| `body_too_large` | Rendered issue body would exceed 60,000 chars |
| `issue_create_failed` | `create_issue` returned an empty/falsy result |
| `internal_error` | Any failure that is not itself an R2-R5 manifest rejection (e.g. an unwritable `--artifacts-dir`, a malformed `FACTORY_MANIFEST_LABEL` override) — still produces a `runs.jsonl` row (R6), fail-closed |

### Trust boundary

Only `handoff.py intake`, running with the factory's own tracker credentials, ever calls
`tracker create`. A manifest may set no labels beyond the fixed pair, no assignee/milestone/
project, and no dependency edges — the proposed ticket body is always rendered inside a fenced
code block (`scheduler.sh::_scan_body_for_deps` skips fenced code when scanning for
`Depends on:`), and every string rendered outside that fence is checked for backtick/newline
injection (`unsafe_string`). See
`docs/superpowers/specs/2026-08-30-artifact-handoff-manifest-a5-design.md` for the full design.

## Side-effect levels

Every `loops[]` entry declares `side_effect_level` (1–5; 6 is rejected at validation — out
of scope for v1, #194). `scripts/factory_core/side_effect.py` is the single source of
truth for what each level enforces:

| Level | Name | Layer A — tools removed | Layer B — shim denies | Net effect |
|---|---|---|---|---|
| 1 | read-only research | `Write`, `Edit`, `MultiEdit`, `NotebookEdit` | `git`: **allow-list** of read verbs only (`log`, `status`, `diff`, `show`, `branch` listing, `remote` `show`/`get-url`/`-v`, `fetch`, `blame`, `describe`, `rev-parse`, `ls-files`, `ls-tree`, `cat-file`, `shortlog`, `reflog`, `grep`, …); `commit`, `push`, `tag`, `remote add/set-url`, `branch` create/delete/move, `--output`/`-o` on `log`/`diff`/`show`/`format-patch`, and every unlisted verb are denied. `gh`: `view`, `list`, `status`, `search`, `api` GET without a body; the never-list applies, so `secret`, `auth`, `ssh-key`, `gpg-key` are denied even as `list`/`status` | Can read and run read-only commands. |
| 2 | artifact writing | none | `git`: `push`, `tag`, `remote set-url`, `send-pack`/`http-push`; `gh`: all mutating verbs (as level 1) | Writes files and local commits; nothing leaves the container. |
| 3 | GitHub ticket creation | none | `git`: as level 2; `gh`: as level 2 except `issue create`/`issue comment`/`issue edit` allowed | Can file and annotate issues; cannot modify code (no push, no PR). |
| 4 | code modification | none | `gh`: `pr create/merge/ready/review/close`, `release *`, `repo *`, `secret *`, `auth *`, `api` non-GET (plus the never-list); `git push` only to the run's own branch (`FACTORY_RUN_BRANCH`, else the checked-out branch) — **every** refspec judged, never `main`, never `--force`/`--delete`, never `--all`/`--mirror`/`--tags`/`--prune`/`--branches` or `+refspec`; `send-pack`/`http-push` denied | Commits and pushes its branch; cannot open or merge PRs. |
| 5 | PR creation | none | never-list only: `gh repo delete/archive/rename`, `gh secret *`, `gh auth *`, `gh ssh-key *`, `gh gpg-key *`, `gh api -X DELETE`, `git push --delete`/refspec deletions | Full PR-creation workflow (today's implement/push-and-pr behavior). |
| 6 | external production side effect | — | rejected at validation | Out of scope for v1. |

Enforcement modes: level 1 `git` is allow-list gated; levels 2–5 `git` are deny-list gated on the
remote-facing verbs above (local work such as `add`, `commit`, `checkout -b`, `stash` keeps
working); `gh` is allow-list gated at levels 1–3 and deny-list gated at 4–5. The never-list is
checked first at every level. The shims strip `git`'s global options (`-C`, `-c`, …) and `gh`'s
persistent flags (`-R`/`--repo`) before reading the verb, expand clustered short options
(`push -df`), and treat `gh api` with `-f`/`-F`/`--input` and no explicit method as POST.

`side_effect.effective_level(value)` fails closed: an undeclared, non-int, boolean, or
out-of-1–5-range level resolves to **1** (the most restrictive profile), never to an open
default. `FACTORY_OWNED_MIN_LEVEL = 4` — a loop declaring level ≥ 4 is factory-owned and is
rejected by the manifest-handoff path (`handoff.py::cross_check`) until a real loop runner
applies `side_effect.profile_for()` itself (tracked as #196's F3 follow-up).

The factory's own phases get their level from `side_effect.phase_levels` in
`config/config.yaml` (all `5` today), each overridable by a `SIDE_EFFECT_LEVEL_<PHASE>` env
var. A run container runs at one level for its whole life: the maximum over the phases its
intent executes (`side_effect.py`'s `effective-container-level`, called from
`entrypoint.sh`). An intent with no phases — `close`, `recheck`, or an unrecognised one —
resolves to level 1.

**What this does and does not claim.** The shim (`scripts/shims/git`, `scripts/shims/gh`)
is a `PATH` shim guarding the two CLIs a Bash-tool subprocess would otherwise reach
directly; a process invoking `/usr/bin/git` by absolute path bypasses it. v1 is a policy
boundary against mistaken or prompt-injected behavior, not a security boundary against a
deliberately hostile agent — see `docs/factory-target-boundary.md` (#201) for the full
trust-model writeup, and #196's spec (`docs/archive/2026-09-04-side-effect-levels-permission-profiles-a2-design.md`)
Known limitations section for the credential-scoping follow-up (F1) that closes this gap.

## The adapter file: what `.factory/adapter.yaml` validation accepts

The provider adapters above are Python modules selected by env. The *adapter file* is a
different thing: the target repo's `.factory/adapter.yaml`, loaded and validated by
`scripts/factory_core/adapter.py` and deep-merged over the built-in defaults. The README's
*Adapter contract* table describes what each key means; this section records what the
validator enforces.

### Checking a file

```bash
PYTHONPATH=<dark-factory>/scripts python -m factory_core.adapter --clone-dir <target> --validate
PYTHONPATH=<dark-factory>/scripts python -m factory_core.adapter --clone-dir <target> --get safety.critical_diff_paths
```

`--validate` prints `adapter OK`, or `adapter INVALID: <reason>` on stderr with exit code 1.
`--get <dotted.key>` prints the merged value (an empty line when the key is absent);
`--format keyvalue` prints a mapping as tab-separated `key<TAB>value` lines. `--clone-dir`
defaults to `$CLONE_DIR`, then the current directory.

**Run `--validate` yourself before committing; the factory also checks it for you.**
`entrypoint.sh` runs the same validation right after cloning the target and **refuses the
run** if the file is present but invalid: an `adapter-invalid` error on stderr, a line in
`hook-warnings.log` in the scheduler state dir, a `<!-- df-adapter-invalid -->` comment on the
ticket, and a failed run. This covers `fix`, `continue`, `refine`, `plan`, `deconflict` and
`fix-main`; `close` and `recheck` do not read adapter-driven behaviour and are not refused.
An absent file stays valid (built-in defaults apply). `scheduler.sh` validates the mounted
checkout once at boot and logs a `WARNING: ADAPTER INVALID` line, but keeps running (the
mounted copy can be stale; the run-start check is the enforcement point). The individual
readers (`factory_core/effective_config.py`, `scripts/gate_lib.sh`, ...) still fail open to
the built-in defaults, which are MarketHawk's, so that a gate never crashes on a bad file;
the boundary floor on `critical_diff_paths` and `migration_seed_auth_patterns` is unioned on
those fallback paths as well. Do not rely on the run-start refusal as your first check, since
it costs a failed ticket attempt
([#492](https://github.com/omniscient/dark-factory/issues/492)).

### Top-level keys

- Known keys: `schema_version`, `components`, `safety`, `memory_routing`, `deconflict`,
  `token_optimization`, `loops`.
- `components`, `safety`, `memory_routing`, `deconflict` and `token_optimization` must be
  mappings; `loops` must be a list; `schema_version` must be an integer and is otherwise
  inert (`loops:` is validated whenever present, whatever the version says).
- An unknown top-level key is not an error: it prints a warning and is carried through the
  merge.
- `mechanism_candidates` is reserved and rejected.
- Mappings merge key by key over the defaults; a list or scalar value replaces the default
  outright. The two exceptions are `safety.critical_diff_paths` and
  `safety.migration_seed_auth_patterns`: after every load, a factory-owned floor
  (`adapter_defaults.py`) is unioned into both, whatever the file declares, and a non-list
  value in either is treated as empty.
- `token_optimization` is live: `factory_core/effective_config.py` merges it over the baked
  `config/config.yaml` for each run.

### `loops[]` entries

Each entry is a mapping. Unknown fields at any level are errors, and loop names must be
unique.

| Field | Required | Shape |
|---|---|---|
| `name`, `purpose` | yes | non-empty strings |
| `side_effect_level` | yes | int 1–5 (6 is rejected) |
| `discovery` | yes | `trigger` (string), `inputs` (list of strings) — both required |
| `handoff` | yes | `manifest` (string), `outputs` (list of strings) — both required |
| `verification` | yes | `verifier`, `stop_condition` (strings) — both required |
| `persistence` | yes | `artifacts` (list of strings) — required |
| `scheduling` | yes | `failure_behavior` (string, required); `max_iterations`, `deadline_seconds` (ints ≥ 1, optional) |
| `role_card` | no | `name` (string, required); `output_schema`, `fallback_path` (strings); `responsibilities`, `non_responsibilities`, `observability` (lists of strings) |
| `economics` | no | `feature_demand`, `model_capability_floor` (strings); `context_offload_required` (bool) |
| `skills` | no | `primary`, `supplemental`, `forbidden`, `eval_cases` (lists of strings) |
| `budget_caps` | no; required at level ≥ 4 | `max_tokens` (int ≥ 1, required); `max_retry_spend` (int ≥ 1) |
| `human_checkpoint` | no; required at level ≥ 4 | non-empty string |

Reserved, rejected with a targeted message: the loop fields `memory_intervention` and
`contract`, and `role_card.allowed_tools` / `role_card.forbidden_tools` (tool allow/deny
declarations are permanently excluded from the adapter file). Each loop's `verifier` is also
checked at load by `verifier.assert_verifier_independent`. What these declarations do at run
time, and what they do not, is covered in `docs/factory-target-boundary.md`.
