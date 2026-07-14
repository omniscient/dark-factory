# Adapter Authoring Guide

This is the requirements checklist for adding a new Tracker, CodeHost, or model-endpoint
adapter to Dark Factory (expands `docs/provider-abstraction-design.md` §12). Every table below
is sourced directly from that design doc's numbered sections — cited inline — so this guide and
the design doc cannot silently drift; if they disagree, the design doc is authoritative.

## Overview

Dark Factory has three independent, pluggable provider axes (`docs/provider-abstraction-design.md`
§1, §4.1), all living under `scripts/factory_core/providers/`:

```
providers/
  __init__.py          # get_tracker(), get_codehost() — env-based selection
  tracker/base.py       # Tracker ABC
  tracker/github.py     # GitHubTracker — reference implementation
  codehost/base.py      # CodeHost ABC
  codehost/github.py    # GitHubCodeHost — reference implementation
  cli.py                 # thin CLI: python scripts/factory_core/providers/cli.py tracker|codehost
```

Selection is env-driven, each defaulting to today's GitHub-only behavior (design doc §4):
`FACTORY_TRACKER` (default `github`), `FACTORY_CODEHOST` (default `github`),
`FACTORY_MODEL_PROVIDER` (default `anthropic`). Every adapter's own connection config and
secrets live in the gitignored instance env (`deploy/instance.env`) — **never** in the
committed adapter module.

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

All ids are **opaque strings** everywhere (design doc principle 5) — never coerce to `int`
anywhere in a conforming implementation, including in test fixtures.

### Canonical vocabulary (frozen contract, design doc §5.2)

- **Seven canonical statuses:** `ready, in_progress, in_review, blocked, done, backlog, refined`.
  Your adapter maps each to its own representation via the seven `FACTORY_STATUS_*` env vars
  (`FACTORY_STATUS_READY`, `FACTORY_STATUS_IN_PROGRESS`, `FACTORY_STATUS_IN_REVIEW`,
  `FACTORY_STATUS_BLOCKED`, `FACTORY_STATUS_DONE`, `FACTORY_STATUS_BACKLOG`,
  `FACTORY_STATUS_REFINED`) — GitHub uses single-select option IDs; a Jira adapter would use
  status *names* under the same seven variables. No new mapping surface is introduced per adapter.
- **Required label vocabulary:** `ready-for-agent, spec-pending-review, plan-pending-review,
  needs-discussion, factory-regression, above-ceiling-work, direct-to-pr, epic, ready-for-human,
  merged-with-edits, regression`. Your tracker must support hyphenated labels.

### Required env (per-adapter example: Jira, design doc §5.4)

`JIRA_BASE_URL`, `JIRA_PROJECT_KEY`, `JIRA_TOKEN` (secret), `JIRA_EPIC_LINK_FIELD`, plus the
seven `FACTORY_STATUS_*` vars holding Jira status *names*. Your adapter documents its own
equivalent list following this shape: base URL, project/namespace key, auth token, any
custom-field mapping the axis needs, and the seven status vars.

### Test bar

1. Contract tests — parametrize your `Tracker` into a shared contract-test suite alongside
   `GitHubTracker` (mirrors this ticket's `CodeHost` contract harness at
   `tests/test_provider_codehost_contract.py` — apply the same structural/opaque-ID pattern to
   `Tracker`).
2. Live smoke checklist (run once against a real instance before shipping): create → label →
   comment → transition through all seven canonical statuses → resolve.

## Code-host adapter

### Required methods (design doc §6.1, ~11 methods)

| Method | Purpose |
|---|---|
| `remote_url()` | auth-embedded clone/push URL |
| `find_change_for(branch)` → id | PR/MR open for a branch |
| `open_change(source, target, title, body, draft)` → id | create PR/MR |
| `update_change_body(id, body)` | backfill close-keyword |
| `mark_ready(id)` | draft → ready for review |
| `merge_change(id, strategy, delete_branch)` | merge |
| `get_change_checks(id)` → `[{name, bucket, ...}]` | CI gate |
| `get_change_mergeable(id)` → enum | conflict gate |
| `get_change_reviews(id)` → state | approval gate |
| `get_change_inline_comments(id)` → list | review feedback |
| `close_keyword(issue_id)` → str | close-on-merge snippet **iff tracker == host**, else `""` |

Plain `git` (clone/branch/commit/push/fetch/diff) is host-agnostic and stays **outside** this
contract (design doc principle 3) — the only git-adjacent method here is `remote_url()`.

### `remote_url()` — auth-embedded URL requirement

Must embed the token directly in the URL so plain `git push`/`fetch` authenticate without any
credential helper. GitHub's form: `https://$TOKEN@github.com/<slug>`. GitLab's form:
`https://oauth2:$TOKEN@gitlab.com/<slug>` (see `GitLabCodeHost.remote_url()` below).

### Draft / ready / merge / checks / reviews mapping expectations

Map your host's terms onto the interface's neutral names — e.g. GitLab: MRs replace PRs, a
`Draft:` title prefix replaces the draft flag, the pipelines API replaces checks, the approvals
API replaces reviews (design doc §6.3).

### `close_keyword()` contract

Returns the body snippet that auto-closes an issue on merge (e.g. `"Closes #42"`) **only when
your code host is also the tracker** (design doc §6.4) — otherwise it must return `""`. Getting
this wrong emits a dead close-keyword the tracker can never see.

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
- **Gateway path:** `databricks` and `openai` don't speak the Anthropic Messages shape, so a
  reference gateway presents `/v1/messages` and routes to the real backend. Set
  `ANTHROPIC_BASE_URL` → the gateway, `ANTHROPIC_AUTH_TOKEN` → the gateway key. Real backend
  secrets live in the gateway config, never in the factory.

### Model-alias mapping (design doc §7.3)

Model names already in the code (`claude-opus-4-8`, `sonnet`, `haiku`, …) stay as tier tokens.
The gateway maps each tier to a real backend model per provider in one config file
(`deploy/gateway/config.yaml`) — `anthropic` is pass-through (parity); `databricks` routes to
the governed Claude endpoint; `openai` routes to the chosen GPT model.

### Cost/quality caveats (design doc §7.5)

- **Cost goes approximate off-Anthropic.** Token counts still flow; dollar figures are
  mislabeled once the backend isn't Anthropic-billed.
- **GPT quality is empirical**, not assumed equivalent to Claude — Databricks-Claude is
  risk-free (identical model, different endpoint); OpenAI is a measured, separate question.

### Preflight requirements

`anthropic` → token present; `databricks`/`openai` → gateway reachable and holding backend
creds; `bedrock`/`vertex` → cloud credentials present. Boot-time preflight is **hard-fail**
(design doc §9) — a misconfigured instance must exit loudly at startup, not fail mid-run.

## Cross-axis concerns

- **Safe failure / fail-open posture (design doc §9).** Degradable tracker ops
  (`get_rate_budget`, `get_status_limits`) return safe defaults. A failed `set_status` or a
  missing status-transition edge logs and leaves the item where it is — same posture as a
  failed board-move today. Boot-time provider preflight is the one hard-fail exception.
- **Idempotency.** `upsert_comment(id, marker, body)` must be a true upsert (create if the
  marker-tagged comment is absent, update in place otherwise) so re-runs never spam a tracker.
- **Secret handling.** Provider secrets live only in the gitignored instance env
  (`deploy/instance.env`), never committed, never hardcoded in an adapter module.
- **Rollback.** Reverting a bad adapter is reverting its selection env var
  (`FACTORY_TRACKER` / `FACTORY_CODEHOST` / `FACTORY_MODEL_PROVIDER`) back to its default — no
  code rollback required if the adapter module itself is otherwise inert when unselected.
- **Mixed-provider close flow (design doc §6.4).** When tracker and code host are the *same*
  provider (e.g. both GitHub), `close_keyword()` in the PR/MR body auto-closes the issue for
  free. When they differ (e.g. Jira tracker + GitHub host), there is no such automatic link, so
  the orchestrator performs the close explicitly and in order:

  ```
  host.merge_change(id) succeeds  →  tracker.resolve_item(issue_id)
  ```

  This is exactly why `close_keyword()` must return `""` when `tracker != host`: emitting a
  dead `"Closes #N"` snippet the tracker can never observe would silently break this invariant.

## Worked example: GitLab CodeHost seam proof

`scripts/factory_core/providers/codehost/gitlab.py` implements `GitLabCodeHost`, a real,
importable `CodeHost` subclass that proves the ABC (§6.1) is not GitHub-shaped, without a live
GitLab instance. It is parametrized into the shared contract suite at
`tests/test_provider_codehost_contract.py` alongside `GitHubCodeHost`, and has its own unit
tests at `tests/test_provider_codehost_gitlab.py`.

| `CodeHost` method | GitLab equivalent (design doc §6.3) | This stub |
|---|---|---|
| `remote_url()` | `https://oauth2:$TOKEN@gitlab.com/<slug>` | **Real** — `GITLAB_TOKEN` + `GITLAB_BASE_URL` (defaults to `gitlab.com`) |
| `close_keyword(issue_id)` | `"Closes #N"` iff GitLab is also the tracker, else `""` | **Real** |
| `find_change_for(branch)` | MR list `?source_branch=` | `NotImplementedError` |
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
