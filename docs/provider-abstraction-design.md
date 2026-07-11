# Provider Abstraction — Pluggable Tracker, Code Host, and Model Endpoint

- **Status:** Design (approved for planning)
- **Date:** 2026-07-07
- **Scope:** dark-factory (`omniscient/dark-factory`)
- **Related:** builds on the extraction identity/adapter contract (`scripts/identity.sh`, `scripts/factory_core/{identity,adapter,adapter_defaults}.py`); feeds the token-optimization / context-budget work.

## 1. Motivation

Dark Factory is monolithically coupled to GitHub (issues + projects + PRs), plain-`gh` tooling, and the Claude/Anthropic model backend driven by the Archon harness. To help other teams adopt it, three things must become swappable, and the *requirements* for each must be documented well enough that a new adapter is a bounded, well-specified task:

1. **Ticket tracking** — GitHub Issues → Jira (and others).
2. **Code hosting** — GitHub PRs → GitLab MRs (and others).
3. **Model endpoint** — Anthropic-direct → Databricks Model Serving (governed Claude) and OpenAI (and others).

These are three **orthogonal** axes on **one shared mechanism**. GitHub is playing tracker *and* code host today; the design separates those roles so operators can mix and match (Jira+GitHub, GitHub+GitLab, Jira+GitLab, any of them on Databricks/OpenAI).

## 2. Goals / Non-goals

**Goals**

- Two orthogonal provider interfaces (`Tracker`, `CodeHost`) plus a model-endpoint seam, each with a documented contract an adapter author implements.
- GitHub and Anthropic become *reference implementations* behind the seams, provably byte-identical to today.
- One real second tracker (**Jira Server/Data Center**) implemented and validated against a live instance.
- Model endpoint pluggable via a **reference gateway** that also serves as a per-persona request ledger for context-optimization; validated live against **Databricks** and **OpenAI**.
- An **adapter-authoring guide** (§12) that is the "requirements documentation."

**Non-goals (this spec)**

- A full GitLab code-host implementation (contract + sketch only; follow-up spec).
- A harness swap (Codex-the-CLI, or any non–Claude-Code agent loop). We keep the Claude Code / Archon harness and only redirect its model endpoint. Native-quality GPT agentic coding is explicitly out of scope.
- Replacing Archon's DAG, cost-tracking schema, or command/skill paradigm.

## 3. Core principles

1. **Two orthogonal providers, not one.** `Tracker` (issues/board/labels/comments/epics) and `CodeHost` (PR/MR, checks, reviews, remote-URL+auth) are independent. The only seam that spans them — GitHub's free `Closes #N` auto-close — becomes an explicit composition (§6.4).
2. **Parity invariant.** With no adapter file and default env, the selected providers resolve to `github`/`github`/`anthropic` and emit the **exact same `gh`/`git`/model calls as today**. Enforced by golden-argv tests. This is the safety net for a refactor that touches `scheduler.sh`, `entrypoint.sh`, `smoke_gate.sh`, the run DAG, and five `factory_core` modules.
3. **Option C seam placement.** Plain `git` (clone/branch/commit/push/fetch/diff) is *already* host-agnostic — it only needs a different remote URL. So we do **not** reimplement git behind a provider. `CodeHost` abstracts only the non-git hosted surface; `Tracker` abstracts the issue/board surface. This is the smallest seam that still yields one implementation per provider.
4. **Degradable operations.** Some operations (`get_rate_budget`, `get_status_limits`) can no-op with safe defaults so a new adapter has a low floor.
5. **Opaque string identifiers.** Issue IDs are strings everywhere (Jira keys like `PROJ-123`, not ints). This is the single most invasive thread through the refactor.

## 4. Shared mechanism

Identical across all three axes:

- **Selection** — new identity env vars, each defaulting to today's provider:
  - `FACTORY_TRACKER` (default `github`)
  - `FACTORY_CODEHOST` (default `github`)
  - `FACTORY_MODEL_PROVIDER` (default `anthropic`)
- **Config + secrets** — provider-specific connection config and secrets live in the gitignored instance env (`deploy/instance.env`), never in the committed adapter. Examples: `JIRA_BASE_URL`, `JIRA_PROJECT_KEY`, `JIRA_TOKEN`, `JIRA_EPIC_LINK_FIELD`; `GITLAB_BASE_URL`, `GITLAB_TOKEN`; gateway URL + key.
- **Status representation reuses `FACTORY_STATUS_*`.** The existing seven env vars already carry the per-instance status representation — GitHub single-select option IDs today, Jira status *names* under the Jira adapter. The provider interprets them. No new mapping surface.
- **Optional target-level overrides** — the already-reserved `adapter.yaml` keys `board:` / `repo:` / `labels:` (deep-merged, fail-open via `factory_core/adapter.py`) hold optional per-target tweaks (custom WIP limits, label-vocabulary overrides). Absent by default; not required for any adapter to function.
- **Boot preflight** — a provider-aware validator replaces today's single `CLAUDE_CODE_OAUTH_TOKEN || ANTHROPIC_API_KEY` check. Each provider declares its required env; a misconfigured instance dies loudly at startup, matching the current `GH_TOKEN` check.

### 4.1 Where the code lands

A new `scripts/factory_core/providers/` package:

```
providers/
  __init__.py          # get_tracker(), get_codehost(), get_model(), preflight()
  tracker/
    base.py            # Tracker ABC (§5.1)
    github.py          # GitHubTracker — wraps today's gh calls verbatim (parity)
    jira.py            # JiraTracker — Jira Server/DC REST v2 + Agile
  codehost/
    base.py            # CodeHost ABC (§6.1)
    github.py          # GitHubCodeHost — wraps today's gh pr calls verbatim (parity)
    gitlab.py          # GitLabCodeHost — sketch/stub (follow-up)
  model.py             # model-endpoint resolution, alias map, gateway preflight
  cli.py               # thin CLI: python -m factory_core.tracker / .codehost / .providers
```

- `board.py` is refactored to **delegate** to `get_tracker()` (its current `gh`-driven body becomes `GitHubTracker`). `identity.py`'s 7-key `STATUS` dict remains the canonical vocabulary.
- Bash (`scheduler.sh`, `entrypoint.sh`, `smoke_gate.sh`) and the run DAG (`workflows/archon-dark-factory.yaml`) replace inline *hosted* `gh`/`git` calls with thin CLI calls (`python -m factory_core.tracker …`, `… codehost …`). Plain `git` stays inline. This mirrors the pattern `board.py` already established (Python module + CLI, called from bash).

### 4.2 CLI surface (illustrative)

```
python -m factory_core.tracker list --statuses ready,in_progress [--labels ready-for-agent]
python -m factory_core.tracker get --id PROJ-123 --fields title,body,labels,status
python -m factory_core.tracker set-status --id PROJ-123 --status in_review
python -m factory_core.tracker label --id PROJ-123 --add ready-for-agent --remove needs-discussion
python -m factory_core.tracker comment --id PROJ-123 --marker "<marker>" --body-file c.md
python -m factory_core.tracker create --title "…" --body-file b.md --labels regression
python -m factory_core.tracker resolve --id PROJ-123
python -m factory_core.tracker children --epic PROJ-1

python -m factory_core.codehost remote-url
python -m factory_core.codehost find-change --branch feat/issue-PROJ-123-...
python -m factory_core.codehost open-change --source <b> --target main --title "…" --body-file b.md --draft
python -m factory_core.codehost mark-ready --id 42
python -m factory_core.codehost merge --id 42 --strategy merge --delete-branch
python -m factory_core.codehost checks --id 42
python -m factory_core.codehost mergeable --id 42
python -m factory_core.codehost reviews --id 42

python -m factory_core.providers preflight   # provider-aware boot validation
```

## 5. Axis 1 — Tracker

### 5.1 Interface

IDs are opaque strings. This operation set is the entire contract an adapter implements (add/remove label share a row below).

| Method | Purpose | GitHub reference | Jira Server/DC |
|---|---|---|---|
| `list_work_items(statuses, labels?)` | poll-loop discovery | GraphQL ProjectV2 paginate | JQL `project=KEY AND status IN(…)` |
| `get_item(id)` | title/body/state/labels/status | `gh issue view` | `GET /rest/api/2/issue/{key}` |
| `get_comments(id)` | read comment thread | `gh issue view --json comments` | `GET /issue/{key}/comment` |
| `get_children(epic_id)` | epic → children | GraphQL `subIssues` | Epic-Link JQL (§5.4) |
| `set_status(id, canonical)` | move to canonical status | `gh project item-edit` (option ID) | resolve transition-id → `POST /transitions` |
| `add_label(id, name)` / `remove_label(id, name)` | label state machine | `gh issue edit` | `PUT /issue/{key}` labels update |
| `upsert_comment(id, marker, body)` | idempotent marker comment | find-by-marker + PATCH/create | find-by-marker + PUT/POST |
| `create_item(title, body, labels)` → id | regression tickets | `gh issue create` | `POST /issue` |
| `resolve_item(id)` | explicit close-on-merge | (was `Closes #N`) | transition → Done |
| `get_status_limits()` → `{status:n}` | WIP limits (**degradable**) | status-option descriptions | adapter config / unlimited |
| `get_rate_budget()` | throttle poll loop (**degradable**) | `gh api rate_limit` | no-op |

### 5.2 Canonical vocabulary (frozen contract)

1. **Seven canonical statuses** — `ready, in_progress, in_review, blocked, done, backlog, refined` (already the `STATUS` dict keys). Each adapter owns the mapping to its representation, supplied via `FACTORY_STATUS_*`.
2. **Required label vocabulary** — workflow tokens the factory branches on, kept as literal label strings: `ready-for-agent, spec-pending-review, plan-pending-review, needs-discussion, factory-regression, above-ceiling-work, direct-to-pr, epic, ready-for-human, merged-with-edits, regression`. A tracker must support hyphenated labels (Jira labels forbid spaces; ours are already hyphenated).

### 5.3 GitHub reference

`GitHubTracker` is a mechanical extraction of the current calls from `board.py`, `scheduler.sh`, `entrypoint.sh`, `epic_autopilot.py`, `breaker.py`, `rescue.py`, `smoke_gate.sh`. It must emit identical `gh`/`gh api`/`gh api graphql` argv (golden tests, §10).

### 5.4 Jira Server / Data Center adapter

- **Discovery** via JQL (`GET /rest/api/2/search`, `project=KEY AND status IN(…) ORDER BY updated`) rather than the Agile board API — no board ID needed. WIP limits come from config, not Jira columns.
- **Status moves are transition-ID-based.** `set_status(id, canonical)`: (1) map canonical → status name via `FACTORY_STATUS_*`; (2) `GET /issue/{key}/transitions`; (3) find the transition whose target is that name; (4) `POST /issue/{key}/transitions` with its id. **Prerequisite on the Jira project:** the workflow must allow the transition edges the factory needs between those states — Jira workflows are directed graphs, not free movement. Required edges are documented as a setup step; a missing edge fails soft (logs, leaves status), same posture as a failed GitHub board-move today.
- **Issue keys** (`PROJ-123`) are the opaque string ID. Branches become `feat/issue-PROJ-123-<slug>`; PR/MR search keys off the sanitized key.
- **Labels / comments** map to native Jira `labels` (`PUT /issue/{key}`) and `/comment` (marker idempotency by body scan, exactly as `board.py` does now).
- **Epic → children via Epic Link.** Children are independent Story/Task issues linked to the epic through the Epic Link custom field (`JIRA_EPIC_LINK_FIELD`, e.g. `customfield_10008`). `get_children(epic)` = JQL `"cf[EPIC_LINK]" = <epic key> AND …`. This matches how the factory dispatches epic children as first-class, independently-runnable tickets (own branch/PR/status). Sub-tasks were rejected (tighter coupling, workflow restrictions).
- **Required env:** `JIRA_BASE_URL`, `JIRA_PROJECT_KEY`, `JIRA_TOKEN` (secret), `JIRA_EPIC_LINK_FIELD`, and the seven `FACTORY_STATUS_*` holding Jira status *names*.

## 6. Axis 2 — Code host

### 6.1 Interface

Plain git stays inline; the only git-adjacent method is `remote_url()`.

| Method | Purpose | GitHub reference | GitLab (designed-against) |
|---|---|---|---|
| `remote_url()` | auth-embedded clone/push URL | `https://$TOKEN@github.com/slug` | `https://oauth2:$TOKEN@gitlab.com/slug` |
| `find_change_for(branch)` → id | PR/MR for a branch | `gh pr list --search head:` | MR list `?source_branch=` |
| `open_change(src, dst, title, body, draft)` | create PR/MR | `gh pr create --draft` | `POST /merge_requests` (`Draft:` prefix) |
| `update_change_body(id, body)` | backfill close-keyword | `gh pr edit` | `PUT /merge_requests` |
| `mark_ready(id)` | draft → ready | `gh pr ready` | remove `Draft:` prefix |
| `merge_change(id, strategy, delete_branch)` | merge | `gh pr merge --merge` | `PUT /merge_requests/{id}/merge` |
| `get_change_checks(id)` → `[{name,status}]` | CI gate | `gh pr checks` | pipelines API |
| `get_change_mergeable(id)` → enum | conflict gate | `mergeable` | `merge_status`/`has_conflicts` |
| `get_change_reviews(id)` → state | approval gate | `gh pr view --json reviews` | approvals API |
| `get_change_inline_comments(id)` | review feedback | REST `/pulls/{n}/comments` | discussions API |
| `close_keyword(issue_id)` → str | close-on-merge snippet **iff tracker==host**, else `""` | `"Closes #N"` | `"Closes #N"` or `""` |

### 6.2 GitHub reference

`GitHubCodeHost` extracts the current `gh pr …` / `gh api …pulls…` calls and the token-in-URL remote construction from `entrypoint.sh`, `scheduler.sh`, `rescue.py`, `main_red_fixer.py`, and the run DAG. Golden-argv parity.

### 6.3 GitLab sketch (follow-up impl)

Enough to prove the seam fits: MRs replace PRs; `Draft:` title prefix replaces the draft flag; pipelines API replaces checks; approvals API replaces reviews; `oauth2:$TOKEN@` remote auth. No implementation this spec; the ABC + a stub with the mapping documented guards against a GitHub-shaped interface.

### 6.4 Cross-provider close-on-merge

When tracker == code host (both GitHub), `Closes #N` in the PR body auto-closes the issue — free. When they differ (Jira tracker + GitHub host), there is no auto-link, so the orchestrator does it explicitly:

```
host.merge_change(id) succeeds  →  tracker.resolve_item(issue_id)
```

`close_keyword(issue_id)` returns the body snippet only when tracker == host, else `""`, so the factory never emits a dead `Closes #N`. GitHub Projects' built-in "card → Done on close" automation, which the factory leans on today for the final Done, has no Jira equivalent — so `resolve_item` performs the Done transition explicitly.

## 7. Axis 3 — Model endpoint

### 7.1 The seam already exists in Claude Code

The harness is Archon driving Claude Code sessions. Claude Code natively honors `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` (and `CLAUDE_CODE_USE_BEDROCK` / `CLAUDE_CODE_USE_VERTEX` for Claude-on-cloud). Because every session, subagent, and the post-mortem `claude -p` call inherit that env, **endpoint-swapping is configuration, not a harness code change.**

### 7.2 Paths

- **Native fast paths (no gateway):** `anthropic` (default, parity), `bedrock`, `vertex` — all serve Claude via native Claude Code flags.
- **Gateway path:** `databricks` and `openai` do not speak the Anthropic Messages shape, so a reference **LiteLLM-style gateway** presents `/v1/messages` to Claude Code and routes to the real backend. The factory sets `ANTHROPIC_BASE_URL` → gateway and `ANTHROPIC_AUTH_TOKEN` → the gateway key; the **real Databricks/OpenAI secrets live in the gateway config, not smeared across the factory.**

### 7.3 Model names become logical aliases

The Claude model names already in the code (`claude-opus-4-8`, `sonnet`, `haiku`, `claude-haiku-4-5-20251001`, in `workflows/archon-dark-factory.yaml`, `commands/*.md`, `config/config.yaml`) stay as **tier tokens**. The gateway maps them per backend:

- `anthropic`: pass-through — **factory code byte-identical (parity).**
- `databricks`: → the governed Claude serving endpoint.
- `openai`: → the chosen GPT model.

All remapping lives in one gateway config file (`deploy/gateway/config.yaml`), overridable so an operator can point at their own gateway (e.g. a company Databricks AI Gateway) by changing only the base URL.

### 7.4 Reference gateway + per-persona observability ledger

We **ship** a reference gateway service in the run/compose stack (pre-wired alias map, works out of the box; base URL overridable — no lock-in). Beyond translation, alias-resolution, and secret custody, it is a **request ledger**: every model call from every persona flows through it, so it captures one record per call:

```
{ persona, model_alias, backend_model, prompt_tokens, completion_tokens,
  n_tools_offered, tool_names[], system_prompt_bytes, n_context_blocks,
  latency_ms, run_id, issue_id, timestamp }
```

This answers *"is each persona using the proper tools and carrying the proper context?"* as queryable data and feeds the token-optimization / context-budget work directly.

- **Persona attribution:** Archon sets a per-node env → Claude Code `ANTHROPIC_CUSTOM_HEADERS: "X-Factory-Persona: <node>"` → the gateway reads the header and tags the record. Requests without the header log as `persona=unknown` (graceful degradation — nothing is lost). Confirming Archon can set that env per node is a design risk to validate early; fallback is run_id+timing correlation.
- **Sink:** structured JSON logs to stdout for shipping to Seq (consistent with the existing stack); LiteLLM's own request DB is an optional richer store.

### 7.5 Selection, preflight, caveats

- **Selection:** `FACTORY_MODEL_PROVIDER` (default `anthropic`).
- **Preflight:** `anthropic` → token present (today's check); `databricks`/`openai` → gateway reachable and holds backend creds; `bedrock`/`vertex` → cloud creds present.
- **Caveat 1 — cost goes approximate off-Anthropic.** Archon prices token usage as Claude. Under Databricks/OpenAI the token *counts* still flow, but dollar figures are mislabeled; reports stay directional, not billing-accurate. (The gateway's own ledger can carry truer per-backend cost later.)
- **Caveat 2 — GPT quality is empirical.** Databricks-*Claude* is risk-free (identical model, different endpoint). OpenAI is the Claude-tuned harness *driving* GPT through a translation shim — it runs, but coding quality is a separate, measured question, not an assumed equivalence.

## 8. Scope boundaries

| Axis | Reference (refactor-to-parity) | Real 2nd impl (live-validated) | Designed-against (follow-up) |
|---|---|---|---|
| Tracker | GitHub Issues/Projects | **Jira Server/DC** | Linear, etc. |
| Code host | GitHub PRs | — | **GitLab MRs** (contract + sketch) |
| Model | Anthropic direct | **Reference gateway → Databricks-Claude + OpenAI** (both live) | Bedrock/Vertex native |

## 9. Error handling & fail-open

- **Parity invariant, test-enforced** (§2, §10).
- **Fail-open matches today.** Degradable ops return safe defaults; a failed `set_status` / missing Jira transition edge logs and leaves the item where it is — same as a failed `gh project item-edit` now. Adapter-config parse errors follow the existing `adapter.py` fail-open posture.
- **Boot-time preflight is hard-fail.** The selected providers validate required env at startup; a misconfigured instance exits loudly rather than failing mid-run.
- **Idempotency preserved.** Marker-comment upsert semantics are part of the contract, so re-runs don't spam any tracker.

## 10. Testing

- **Parity / golden-argv tests** — GitHub tracker & code host emit argv identical to the current inline calls. This is the guardrail for touching `scheduler.sh`, `entrypoint.sh`, `smoke_gate.sh`, the run DAG, and the five `factory_core` modules.
- **Contract tests** — one shared abstract suite run against every `Tracker` / `CodeHost` implementation using recorded HTTP fixtures (VCR-style), so Jira/GitLab are covered in CI with no live server.
- **Live validation (the end-to-end proofs):**
  - **Jira** against the live instance + a throwaway project: create → label → idempotent comment → transition through all seven statuses → Epic-Link children → resolve.
  - **Model** against **Databricks** (governed-Claude routing) and **OpenAI** (GPT) through the gateway: a real dispatched run per backend, plus assertion that the ledger captured persona/tool/context records.
- **Selection / config tests** — env resolves to the right provider; missing required env fails preflight clearly; defaults stay github/github/anthropic.

## 11. Implementation sequence (main stays green throughout)

1. `Tracker` / `CodeHost` ABCs + GitHub implementations wrapping the *exact* current calls, + parity tests. Zero behavior change.
2. Route bash / entrypoint / scheduler / run-DAG hosted calls through the provider CLIs. Still GitHub; parity tests hold.
3. `FACTORY_TRACKER` / `FACTORY_CODEHOST` / `FACTORY_MODEL_PROVIDER` selection + provider-aware boot preflight.
4. `JiraTracker` + fixtures + contract tests; live-validate against the Jira instance.
5. Reference model gateway (compose service + alias config + persona-ledger) + `FACTORY_MODEL_PROVIDER=databricks|openai`; live-validate both.
6. Adapter-authoring guide (§12) + GitLab `CodeHost` sketch proving the seam.

Each step is independently shippable; a phased implementation plan will decompose these into PRs (likely one epic with per-step tickets).

## 12. Adapter-authoring guide (the requirements documentation)

To add a **tracker**: implement the `Tracker` operations (§5.1), honor the canonical status + label vocabulary (§5.2), declare the env vars your adapter reads, map the seven canonical statuses via `FACTORY_STATUS_*`, and pass the shared contract suite (fixtures) + a live smoke of create/label/comment/transition/resolve.

To add a **code host**: implement the ~11 `CodeHost` methods (§6.1), provide an auth-embedded `remote_url()`, map draft/ready/merge/checks/reviews to your host's model, return `close_keyword` only when your host is also the tracker, declare env vars, and pass the contract suite.

To add a **model endpoint**: either (a) if it speaks Anthropic Messages or is a native Claude-Code backend (Bedrock/Vertex), set the endpoint env + preflight; or (b) add a gateway alias-map entry that routes the tier tokens to your backend and holds its credentials. Document the model-tier mapping and whether cost/quality caveats apply.

Every adapter: **default path unchanged** (parity), **secrets in instance env only**, **fail-open on degradable ops**, **hard-fail at preflight on missing required config**.

## 13. Open questions / risks

- **Archon per-node persona header** (§7.4) — confirm Archon can set `ANTHROPIC_CUSTOM_HEADERS` per node; if not, fall back to run_id+timing correlation and log `unknown`.
- **Jira workflow transition edges** (§5.4) — the target project's workflow must permit the required transitions; documented as a setup prerequisite, fails soft otherwise.
- **Databricks endpoint shape** — confirm whether the company's Databricks endpoint is OpenAI-compatible (needs the gateway's Anthropic-shape translation) vs. exposes an Anthropic-compatible surface (could be direct); the gateway covers either case.
- **Cost accuracy off-Anthropic** (§7.5) — accepted as directional; revisit if billing-accurate reporting is needed (gateway ledger is the path).
