# Spike: Evaluate Archon Web UI as Dark Factory Cockpit and Manual Dispatch Surface

**Issue:** omniscient/dark-factory#207
**Status:** spike spec — this refine pass answers the investigation scope directly by reading
Archon's own source (`/opt/archon`, pinned commit `74372446d1c5f07101dfff61c44be8895cca30db` per
`Dockerfile`) and Dark Factory's deployed architecture (`entrypoint.sh`, `scheduler.sh`,
`deploy/docker-compose.yml`, `run-compose.yml`, `scripts/factory_core/`). Findings below are cited to
specific files/lines, not general knowledge about Archon. No code, workflow, or config changes happen
as a side effect of this ticket — see Scope Boundary in `commands/dark-factory-refine.md`.
**Related:** #234 (Harness Economics Ledger/CPM), #208.

---

## Overview / Problem Statement

Dark Factory already depends on Archon as a headless CLI workflow runner: `entrypoint.sh` shells out to
`archon workflow run archon-dark-factory "$ARGUMENTS"` inside an ephemeral, per-issue Docker container,
and separately to `archon workflow cost --last --json --quiet` for cost data. Archon also ships a
Web UI/dashboard (`archon serve`) for chatting with agents, browsing workflow runs, and building
workflows visually. This spike asks whether that Web UI can become a safe **cockpit** (read-only
observability) and/or **manual dispatch surface** for Dark Factory, without bypassing the scheduler,
the GitHub Project board state machine, WIP limits, the dispatch ceiling, retry/circuit-breaker
behavior, the target-adapter contract, or conformance/code-review gates.

The answer this spec reaches: **yes, in a narrow and specific shape** — a read-only cockpit fed by
Dark Factory's own already-durable artifacts (not Archon's), and manual dispatch expressed as a
board/label write (not a direct container invocation) — with a shared Archon-native run/event store and
in-Web-UI workflow editing both recommended against for now, for reasons that are architectural, not
merely cautious.

---

## Requirements

Distilled from the issue's investigation scope/acceptance criteria and the Q&A below.

1. The spike must verify Archon Web UI/server assumptions (run storage, project registration, auth,
   local paths, credentials) against Dark Factory's *actual* deployed architecture, citing source —
   not describe Archon's design in the abstract.
2. The spike must confirm whether `workflows/archon-dark-factory.yaml` can be displayed separately
   from being executed, and state precisely why direct Web UI execution of the live workflow is unsafe
   or unsupported.
3. Any recommended read-only cockpit must source data from a store that is durable and already shared
   across runs today — not from state that dies with the ephemeral container that produced it.
4. Any recommended manual-dispatch design must route through `scheduler.sh`'s existing gated dispatch
   path (WIP limit, ceiling, retry/breaker, session-window backoff) with **zero new direct-invocation
   code path** — per the issue's explicit non-goal ("do not bypass ... any gate").
5. Any recommended design must not expose secrets (GitHub token, Claude credentials, Docker socket)
   through the Web UI, and must account for Archon's server having no built-in authentication.
6. The spec must include a compatibility matrix (works as-is / works with adapter / not recommended), a
   phased architecture recommendation, a security/permissions checklist, a list of required Dark
   Factory and Archon-side changes, and a recommendation on filing follow-up epics/issues (definitions
   only — this ticket does not file them).
7. Per CLAUDE.md and `.factory/adapter.yaml`, any follow-up work touching `scheduler.sh` dispatch logic,
   gates, or budgets must be scoped as its own reviewed ticket, never bundled into a "cockpit" ticket.

---

## Architecture / Approach

### 1. Current-state compatibility (verified against source)

| Question | Finding |
|---|---|
| Can Web UI load/display `workflows/archon-dark-factory.yaml`? | **Not as committed.** Archon's workflow discovery (`packages/workflows/src/workflow-discovery.ts`) only scans bundled defaults, `~/.archon/workflows/` (home), and `<cwd>/.archon/workflows/` (repo-scoped) — never a top-level `workflows/` directory. Dark Factory's own DAG is never read from its committed path; `entrypoint.sh:574-577` copies the baked `/opt/dark-factory/workflows/` into `$CLONE_DIR/.archon/workflows/` at container start and then `_exclude_in_clone`s that copy so it can never be committed. Nothing durable exists at the path Archon expects. |
| Can it show DAG structure without executing? | **Yes, but only against the committed file, read out-of-band of any live container.** `.github/workflows/ci.yml` already parses `workflows/archon-dark-factory.yaml` at its committed root path via `scripts/check_workflow_dag.py`/`check_workflow_when.py` — the same file a read-only renderer should point at. Rendering a live container's `.archon/workflows/` copy is explicitly out of scope: that copy is throwaway and never reflects a reviewed state. |
| What does Archon assume about run storage / project registration / auth / credentials? | Archon defaults to a **per-process SQLite file** (`~/.archon/archon.db`, auto-created) when `DATABASE_URL` is unset — confirmed unset everywhere in `entrypoint.sh`. It also supports Postgres with a real schema: `remote_agent_codebases` (project registration), `remote_agent_conversations`, `remote_agent_workflow_runs`, `remote_agent_workflow_events` (`/opt/archon/migrations/001_initial_schema.sql`, `008_workflow_runs.sql`, `012_workflow_events.sql`). The server API (`packages/server/src/routes/api.ts`) has **no built-in auth middleware**; a source comment reads "CORS for Web UI — allow-all is fine for a single-developer tool." A separate opt-in `auth-service` (bcrypt + signed cookie, its own Dockerfile/compose profile) exists to bolt on basic auth but is not wired into Dark Factory's `deploy/` today. |
| Does the current CLI invocation produce Web-UI-consumable events/artifacts? | **No, and it cannot as currently invoked.** Every phase run is a fresh container; with no `DATABASE_URL`, any `remote_agent_workflow_runs`/`events` rows Archon writes live in that container's throwaway SQLite file and are destroyed at container exit. There is no cross-run persistence or sharing via Archon's own store today. |
| Is `archon workflow cost --last --json --quiet` enough for cost visualization? | It's what `entrypoint.sh:837` already captures per-run, but it is **narrower** than what Dark Factory already computes: `scripts/factory_core/run_record.py`'s `_compute_harness_economics()` persists `cost_per_task`, `tokens_per_task`, `factory_cpm`, `retry_spend`, `failure_spend`, and `ledger_mechanics` into `run-records/<run_id>.json`, and `scripts/reconcile_cost_reports.py` already reconciles cost-report comments against it. The Archon CLI cost command should stay an *input* to that pipeline, not a replacement for it. |

**Why direct Web UI execution of the live workflow is unsafe:** even setting aside the discovery-path
mismatch above, Archon's server has no auth and allow-all CORS ("fine for a single-developer tool," by
its own admission), while `scheduler.sh`'s `dispatch()` shells `docker compose run` against the Docker
socket on the factory host — the same host holding the GitHub token and Claude credentials. Any network
path that lets Archon's Web UI trigger that invocation directly is unauthenticated remote code
execution on the factory host. This is a categorical objection, not a configuration gap to be tuned.

### 2. Recommended architecture (phased)

**Phase 1 — Read-only cockpit, fed by Dark Factory's own durable artifacts.**
Dark Factory already has a host-durable, cross-run, multi-writer store that Archon's SQLite/Postgres
choice is trying to solve a problem we don't have: `deploy/docker-compose.yml` declares a named volume
(`dark_factory_state`) mounted at `/var/lib/dark-factory` in the scheduler, and `run-compose.yml` mounts
the same volume into every dispatched run container. `scripts/factory_core/run_record.py` writes
`runs.jsonl` and per-run `run-records/<run_id>.json` there, including the harness-economics block noted
above. A thin, read-only exporter should surface: GitHub Project board state (existing tracker
provider), issue/PR comments, `runs.jsonl` + `run-records/*.json`, and reconciled cost reports — no new
infrastructure, no new secrets, nothing added to the factory host's trust boundary. This satisfies the
Hermes-Agent PM comment's economics ask (cost/task, tokens/task, retry/failure spend, model routing)
as a *reader* of existing ledgers, per that comment's own non-goal ("never the source of truth").
Read-only DAG visualization (rendering the committed `workflows/archon-dark-factory.yaml`, never a
live container's copy) is a Phase-1-adjacent nice-to-have, satisfying investigation-scope item 1.
The cockpit should consume a documented, versioned projection of these artifacts (mirroring the
existing `policy_version` field in run records) rather than reading `/var/lib/dark-factory` file
internals directly, so the underlying format stays free to evolve.

**Phase 2 — Manual dispatch as a board/label write, never a direct invocation.**
`scheduler.sh` is the sole authority that ever invokes `docker compose --profile factory run`, and its
poll loop (default 60s) is where every gate lives — `factory_at_capacity()`, retry/signature checks,
`has_skip_label`, the dispatch ceiling, main-red handling, session-window backoff. A UI, adapter, or
companion service that calls dispatch itself must either re-implement all of that (a second, drifting
copy of safety logic) or bypass it — there is no third option, and CLAUDE.md's "never weaken safety
gates as a side effect of another change" rule forecloses the first. The correct design instead treats
"manual dispatch" as *giving an existing interaction a better button*: reuse existing label triggers
where one already exists (e.g. a "Refine this" action applies `ready-for-agent`, requiring zero
`scheduler.sh` changes), and introduce a new one-shot "requested" marker only for phases with no
existing trigger today. Deduplication has two layers that must not be conflated: `is_issue_running()`
(`scheduler.sh:159-163`) already prevents a second concurrent container on the same issue (runtime
dedup, free at every call site); a sticky request marker additionally needs to be **consumed by
`scheduler.sh` at dispatch time** (label removed / field cleared) so it doesn't re-fire on the next poll
after the container exits (request dedup). That marker-consumption change is gate-adjacent — it touches
`scheduler.sh`'s dispatch path — and must be its own reviewed ticket per CLAUDE.md, not bundled here.
The UI must surface `Requested` as a state distinct from `Running` (dispatch is bounded by
`poll_interval` and `factory_wip_limit`, so it will not be instant), and should surface the scheduler's
own skip reason (already logged, e.g. `skip=factory_at_capacity running=N/M`) so "why isn't my thing
running" has a visible answer instead of a mystery.

**Phase 3 — Shared Archon-native run/event store: deferred, conditional, not indefinite.**
Standing up Postgres early would require a `DATABASE_URL` credential injected into every ephemeral,
least-trusted run container, a long-lived Postgres instance (deploy-surface changes are human-only per
CLAUDE.md), and — because Archon's API has no auth — makes the opt-in `auth-service` a hard
prerequisite it doesn't have today. None of that is justified while Dark Factory's own artifacts
already cover Phase 1. The one real gap those artifacts have: `run-record.json` is assembled at run end
(`entrypoint.sh` success path ~line 843, failure path ~line 466), so there is no live, in-flight,
step-level progress view. **Explicit trigger to revisit:** if Phase 1 in operation shows operators
genuinely need sub-run liveness, the cheaper next step is emitting stage events mid-run to a
Dark-Factory-owned sink (`run_record.py` already POSTs per-stage events to Seq via `SEQ_URL` — extending
that is a smaller lift than adopting Archon's schema and its infrastructure tail). Only if that too
proves insufficient does adopting Archon's Postgres schema become worth its cost.

**Phase 4 — Guarded workflow-builder support: not recommended, with a reopen trigger, not a permanent no.**
Any real edit path must terminate in a commit to `workflows/archon-dark-factory.yaml` on a branch, with
a PR. Archon's workflow builder (`packages/web/src/components/workflows/WorkflowBuilder.tsx`) saves to `<cwd>/.archon/workflows/`; the only place Dark Factory ever materializes that directory is the run container's throwaway copy (`entrypoint.sh:574-577`), which
`entrypoint.sh:577` explicitly excludes from ever being committed — an edit made there is unreachable
from any commit and dies with the container. This is the same failure class as a prior memory
precedent on this repo (#212: DAG-gating logic must check the committed file on the branch, never an
ephemeral artifact, to avoid "label applied with nothing to review") — a builder that appears to edit
the DAG while the edit can never reach review is an action that looks governed and isn't. Even a
hypothetical future PR-emitting builder would still need to clear a higher bar than a diff-level PR:
`workflows/` sits in `.factory/adapter.yaml`'s `critical_diff_paths` alongside `scheduler.sh`,
`entrypoint.sh`, `Dockerfile`, and `deploy/`, and CLAUDE.md requires gate-adjacent changes get their own
reviewed ticket. **Reopen trigger:** revisit only if Archon's builder gains an upstream, git-backed mode
that commits to a branch in the target repo and opens a PR through the normal review path — at which
point it is evaluated as its own ticket, not assumed safe by extension of this spike.

### 3. Compatibility matrix

| Archon Web UI feature | Verdict |
|---|---|
| View workflow run history / cost data | Works with adapter — via a Dark-Factory-owned exporter reading `run-records/*.json` + `runs.jsonl` + reconciled cost reports, not Archon's own DB |
| Live in-flight step/token/timeline view | Not recommended (for now) — no durable per-step data exists on either side yet; conditional Phase 3 trigger above |
| Read-only DAG visualization | Works with adapter — render the committed `workflows/archon-dark-factory.yaml` directly, read-only, independent of any container |
| Manual "run this phase" button | Works with adapter — implemented as a board/label write consumed by the existing scheduler poll loop, not a direct dispatch call |
| Chat-driven ad hoc workflow execution over this repo | Not recommended — no auth on Archon's server, and it would run outside the scheduler's WIP/ceiling/retry gates entirely |
| Visual workflow builder / editor for `archon-dark-factory.yaml` | Not recommended — edits are unreachable from any commit; see Phase 4 |
| Archon's own Postgres-backed workflow_runs/workflow_events store | Not recommended as a first step — duplicates and is poorer than existing `run_record.py` data; see Phase 3 |

### 4. Security / permissions checklist

- [ ] Cockpit reads only Dark Factory's own artifacts (board, comments, run-records, cost reports);
      it never reads Archon's SQLite/Postgres tables as a source of truth.
- [ ] Manual dispatch never causes any component other than `scheduler.sh` to invoke
      `docker compose --profile factory run` or otherwise start a factory container.
- [ ] No GitHub token, Claude credential, or Docker socket access is exposed through the Web UI or any
      companion service — the cockpit's read path and the dispatch path's write path are both
      expressible as scoped GitHub API calls, not host-level access.
- [ ] If Archon's Web UI/server is ever network-exposed (even to an internal network), it sits behind
      the existing opt-in `auth-service` (or an equivalent authenticated reverse proxy) — never bound
      openly, given the server ships no built-in auth and allow-all CORS by design.
- [ ] Any change to `scheduler.sh` dispatch logic (marker consumption, new label semantics) ships as its
      own reviewed ticket, distinct from cockpit/exporter work, per CLAUDE.md.
- [ ] `deploy/instances/**` and `.github/workflows/publish.yml` are not touched by any cockpit-related
      change (CLAUDE.md hard limit, unconditionally).
- [ ] Workflow-builder write paths stay unimplemented; if ever revisited (Phase 4 reopen trigger), the
      resulting edit path must land through the standard branch/PR/review flow — never a live edit to a
      running container's `.archon/workflows/` copy.

### 5. Required changes (definitions only — not filed by this ticket)

**Child issue A — "Read-only Dark Factory cockpit exporter" (Phase 1).**
Size: M. Labels: `enhancement`, `observability`, `foundation`. No dependency. Scope: a thin read-only
exporter/adapter surfacing board state, issue/PR comments, `runs.jsonl` + `run-records/*.json`, and
reconciled cost reports in a documented, versioned projection; read-only rendering of the committed
`workflows/archon-dark-factory.yaml`. Includes the harness-economics surfacing asked for in the Hermes
PM comment (cost/task, tokens/task, cached vs uncached, retry/failure spend, phase/persona spend),
scoped strictly as a reader of existing ledgers. `Related: #234, #208`.

**Child issue B — "Manual phase-dispatch request marker" (Phase 2).**
Size: S or M (avoid "refactor"/"migration"/"architectur"/"performance" in the title — those keywords
trigger `dispatch_ceiling`'s size-M park-in-Blocked behavior). Labels: `enhancement`, `foundation`.
`Depends on: #<child-issue-A-number>`. Scope: a one-shot "requested" marker (label or field) for phases
with no existing trigger label, consumed by `scheduler.sh` at dispatch time so it doesn't re-fire on the
next poll; the cockpit UI surfaces `Requested`/`Running`/skip-reason states by reading the same board
state Phase 1 already exports. This is the ticket that actually touches `scheduler.sh` dispatch logic
and therefore gets its own focused review.

No issue is filed for Phase 3 (conditional, deferred — see trigger above) or Phase 4 (recommended
against — see reopen trigger above); both are recorded here so the decision and its reasoning survive
even though no ticket tracks them yet.

---

## Alternatives Considered

1. **Adopt Archon's Postgres-backed run/event store immediately, for both cockpit and live timeline.**
   Rejected for Phase 1: requires new infrastructure and a new secret injected into every ephemeral,
   least-trusted run container, and produces a second, poorer copy of data `run_record.py` already
   computes better — directly conflicting with the PM comment's "never the source of truth" non-goal.
2. **Let the Web UI (or a new companion service) call `docker compose --profile factory run` directly,
   with its own WIP-limit/dedup logic.** Rejected: duplicates safety logic that already lives in
   `scheduler.sh`, creating exactly the kind of drifting second copy CLAUDE.md's "never weaken safety
   gates" rule exists to prevent, and requires exposing Docker-socket-level access to an unauthenticated
   server.
3. **Support live editing of `workflows/archon-dark-factory.yaml` via Archon's WorkflowBuilder component now,
   with a "PR review" step bolted on.** Rejected: any such edit path is unreachable from a real commit
   under the current container lifecycle, and even a hypothetical future version would need to clear a
   higher review bar (`critical_diff_paths`) than the issue's own "PR-reviewed" framing implies.

---

## Open Questions (non-blocking)

- What is the actual demand for live in-flight step-level progress once Phase 1 ships? This spike
  defers Phase 3 on the assumption that operators can tolerate end-of-run visibility; that assumption
  should be revisited with real usage, not re-litigated in the abstract.
- Should the Phase-1 exporter live inside this repo (`scripts/factory_core/`) or as a small separate
  companion package? Both are compatible with the architecture above; the choice doesn't change any of
  the security or gating conclusions and is left to the implementation ticket.

---

## Assumptions (flagged)

- Archon's pinned commit (`74372446d1c5f07101dfff61c44be8895cca30db`, `feat/workflow-cost-tracking`) is
  representative of the Web UI's current design; a future re-pin that changes auth, discovery, or
  storage behavior would need this spike's compatibility matrix re-verified, not assumed to still hold.
- `deploy/` changes (standing up Postgres, wiring `auth-service`) are described here only to inform the
  Phase 3 trigger; per CLAUDE.md, `deploy/instances/**` remains human-only regardless of this spike's
  conclusions.
