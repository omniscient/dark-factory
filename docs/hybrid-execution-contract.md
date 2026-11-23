# Hybrid Model Execution Contract — Cloud + Local

**Issue:** #556

**Contract version:** `hybrid_contract: "1.0"`

**Status:** Accepted — the #556 PR merged and this is the accepted copy on `main`. The five
§4.11 decisions, carried at merge as provisional operator defaults pending owner confirmation,
were confirmed as written by the owner in #580 (2026-11-22); downstream implementation specs
(#557–#564, #239's re-refinement) may be written against this contract.

**Revised:** 2026-11-22 — spec-gate review amendments: harness-enforced checker independence
(§4.2), payload/destination predicate and default classes (§4.3), normative `role` values (§4.4),
minimum failure outcomes (§4.6), budget/breaker/session-window invariants (§4.10), v1 selected
path and provisional operator defaults (§4.11), risks and rollout/rollback rules (§4.12), #564 row
(§4.1), and a corrected #288 taxonomy citation (Alternatives 6).

**Revised (#580):** 2026-11-23 — post-merge independent-review amendments: acceptance status
(header, §4.11), checker-independence scope (§4.2, Invariant 3), session-window citation and
rollout precondition (§4.10, §4.12), credential invariant (§4.3, new Invariant 16),
helper-dispatch definition (§4.11 decision 3), `local_only`/local-pinned split (§4.3,
Invariants 4–7), mechanism owners (§4.2, §4.3, §4.7), §4.6/#238 interim enumeration,
capability-evidence storage/vocabulary (§4.5), `cost_usd` owner (§4.10), rollout alignment
(§4.12), keyword-signal scope (§4.3), and nits (Overview, About this document, §4.11
decision 2, Alternatives 4, Assumptions, Invariant 5, the test's line-wrapped-citation and
five-decisions checks, and the `docs/provider-abstraction-design.md` §7.5 pointer).

---

## About this document

This is the durable decision report #556 asked for. Everything from "Overview / Problem
Statement" down is the reviewed refinement spec for #556, reproduced verbatim, so "this
document" and "this spec" in that text both mean this file. The spec itself is archived to
`docs/archive/` when the #556 PR is pushed and is design history only; from then on this file
is authoritative. A changed decision is a reviewed revision of this file, not a downstream
re-decision (§4.11). This contract changes no code, config, gate, breaker, budget, deployment
or model default; the downstream tickets in §4.1 consume it, each through its own reviewed
refinement.

The body keeps its refinement-time wording. Statements about what "this phase" may output
describe the refinement phase that produced it. References to precedents "reproduced below"
(#189, #207) and to "§Scope boundary" point at refinement-time context that is not reproduced
here; this contract's own scope limits are Requirement 7 and §4.10. Where a Brainstorming Q&A
answer differs from §4.1–§4.12 as revised by #580, the section governs — notably Q1's
"different secret reference" proof, Q2's unscoped checker-endpoint check and "unknown on either
side" wording, and Q3(a)'s `qualified_roles` and result states.

## Invariants (index)

Each item restates a rule defined in the section it cites. Where the wording differs, the cited
section governs.

1. Endpoint isolation is per dispatched run container; one container never mixes cloud and
   local endpoints in-process (Q1, §4.2).
2. For a local-routed actor's work, an independent checker is always a separate dispatch,
   cloud-routed by default. A nested Agent-tool subagent of a local-routed parent is never a
   checker, and a verdict produced inside a local-routed container is void (§4.2).
3. This independence check applies only to a local-routed actor's work: for such work, a gate
   verdict is accepted only if the dispatch record of the run that produced it shows
   `hosting_location: cloud` and an `endpoint_id` different from the actor's dispatch, proven
   by the record, never by an agent's own claim; a row with `correlation: "unknown"` is never
   that proof. A cloud-routed actor's same-run checker (today's `conformance`/
   `docs-conformance`/`code-review` nodes, and `refine`/`plan`'s in-process subagent checkers)
   is unchanged by this contract (§4.2, §4.4).
4. A dispatch is allowed only if its *effective* payload class is a member of the target
   entry's `accepts_payload_classes`; an out-of-enum declared value, an invalid registry
   entry, or an effective class absent from `accepts_payload_classes` blocks and escalates. An
   absent declared class on a cloud dispatch defaults to `cloud_ok` and never blocks (§4.3).
5. Payload class is stricter-only: no label, comment or model output can loosen it; only
   operator-controlled config (gitignored instance config or a human-reviewed adapter key) can
   set or change the declared class (§4.3).
6. Every dispatch with `hosting_location: local` is `local-pinned` and never retries on, or
   falls back to, cloud — a later cloud run is always a new dispatch decision, never a
   fallback of the failed one. Every local failure, timeout or cancellation resolves to a #238
   failure class and abstains or escalates (§4.3, §4.6).
7. Neither a `local-pinned` dispatch's output nor any `local_only`-classed payload ever
   auto-merges; the merge is always a human action (§4.3; accepted operator default, §4.11
   decision 5).
8. Secrets are referenced by `secret_reference` and never appear in the correlation envelope,
   a ledger row, a run record, a log line or an issue comment (§4.3).
9. Capability evidence is authored only by #562's certification run against #561's pinned
   fixtures, never by a model's self-report; missing, expired, failed or mismatched evidence
   fails closed with no grace period (§4.5).
10. Correlation that cannot be attributed is recorded as `inferred_pointer` or `unknown`, never
    guessed (§4.4).
11. Every local endpoint-registry entry declares connect, time-to-first-token, idle and total
    timeout bounds, and the runtime adapter enforces them, not the agent (§4.6).
12. Minor schema-version bumps are additive; major bumps are a hard reject (§4.7).
13. Local runs count toward WIP limits, dispatch ceilings, the retry breaker, token budgets and
    the session-window pause exactly like cloud runs; local cost is `cost_usd: null`, never
    zero; a local 429 never writes the shared session-window pause sentinel (§4.10).
14. Neither this contract nor comment-channel input authorizes any change to budgets, the
    breaker, the session-window pause, `gate_*`, tool allow/deny lists, side-effect levels or
    `deploy/**` (§4.10).
15. Merging a downstream ticket activates nothing. Rollback is unsetting the local endpoint
    selection in operator-controlled config, which restores today's default byte for byte; an
    in-flight local run fails closed and is never re-run on cloud automatically (§4.12).
16. A local-routed run container holds no cloud model credential and sends none to the local
    endpoint; it holds no tracker or code-host write credential unless its own reviewed spec
    explicitly grants one (§4.3).

---

## Overview / Problem Statement

Dark Factory's model endpoint is today a single axis: `FACTORY_MODEL_PROVIDER` (default
`anthropic`), documented in `docs/provider-abstraction-design.md` §7. Every dispatched run
container gets exactly one `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` pair from
`.archon/.env`, read once by Claude Code at process start; `ANTHROPIC_DEFAULT_{OPUS,SONNET,
HAIKU,FABLE}_MODEL` (`run-compose.yml`) likewise resolve the four tier aliases once per
container. `scripts/factory_core/model_proxy.py` (shipped via #208) is an opt-in transparent
Anthropic passthrough plus a request ledger — it forwards unchanged and tags every row
`"persona": "unknown"`; it is not a translator, not a router, and does not switch backends.
`scripts/factory_core/providers/model.py` hard-fails preflight for `databricks`/`openai`
("requires the model gateway, which is not yet implemented") and has no behavioral class at
all, unlike the `Tracker`/`CodeHost` axes which have real ABCs and a registry
(`providers/__init__.py`).

The approved initiative (#202 delivery anchor, #234 economics owner) wants to add a **local**
inference endpoint as a third hosting location alongside today's cloud-only path, so that some
bounded factory work can run against operator-certified local hardware while independent
verification stays cloud-routed — without replacing the Claude Code / Archon harness, without
weakening any existing gate, and without the user's roadmap approval being treated as
pre-approval of a rollout. This ticket is Wave 0 of that roadmap: a reviewed, bounded
**decision report** — not an implementation — that separates endpoint identity, protocol,
hosting location, model identity, and per-invocation capability, and that downstream
implementation tickets (#557–#564, already filed and intentionally withheld from
`ready-for-agent`) can be built against. Per CLAUDE.md's hard limits and the architecture-memory
precedent on comment-channel proposals (#189, reproduced below), this document specifies
mechanics for not-yet-existing surfaces as follow-up-ticket content; it makes no code, config,
or gate changes itself — the scope boundary on this phase enforces that structurally (the only
permitted outputs are this file and `.archon/memory/` entries).

## Requirements

Distilled from the issue's acceptance criteria plus the brainstorming Q&A (full dialogue in the
Phase 6 issue comment):

1. Publish a durable decision report with current-state evidence, a responsibility map, explicit
   invariants, a compatibility matrix, versioned configuration/request/result/capability
   contracts, risks, and rollout/rollback rules. (§Architecture, all subsections; selected
   runtime/protocol path and tier resolution in §4.11; risks and rollout/rollback in §4.12)
2. Map the nine initiative slices and the existing #239/#238/#234 owners to acceptance
   predicates; keep protocol translation, route eligibility, and retry policy separate. (§4.1)
3. Specify how a local worker and a separately cloud-routed checker coexist, including nested
   Agent-alias behavior and unsupported-delegation refusal. (§4.2)
4. Define credential stripping, request-level run/phase/role attribution, unknown/missing
   telemetry handling, confidentiality checks before dispatch/fallback, and non-exporting outage
   behavior. (§4.3, §4.6)
5. Incorporate the accepted #288 decision — confidence-source separation, actor/monitor/verifier
   authority, fail-closed consequential routing. (§4.1, throughout)
6. List the exact spec delta for #239 and its re-refinement inputs; no stale routing spec may
   advance to implementation. (§4.9)
7. Obtain human acceptance of this contract before any downstream implementation spec is
   written; no runtime, gate, breaker, deployment, or model-default change in this ticket.
   (§Scope boundary, §Alternatives)

### Brainstorming Q&A

> **Q1 (endpoint-isolation granularity):** Given that `ANTHROPIC_BASE_URL` is a single env var
> set once per dispatched run container, and every in-process Agent-tool subagent inherits that
> same base URL from its parent process, true per-call endpoint switching inside one container
> process is not achievable with Claude Code's native mechanism today. Should the contract (a)
> define endpoint isolation only at container-dispatch granularity, or (b) require a new
> per-request routing layer so one container's agent tree can mix endpoints?
>
> **A:** Pick (a) for v1; treat (b) as a possible later mechanism, neither required nor ruled
> out. (a) matches the code as it stands (one `.archon/.env` per container, aliases also
> container-wide) and the gateway (b) would need was never built. (b) would also violate the
> #288 trust rules: a per-request router keyed on a self-set persona/role header is
> `self_report` provenance, which can never authorize a consequential action (selecting the
> "independent checker" endpoint is one), and co-locating local+cloud credentials in one process
> conflicts with #238's no-fail-open-to-cloud rule. The isolation unit must be a **separate
> dispatched run container with its own endpoint config**, not just a separate DAG node —
> multiple nodes inside one run share one `.archon/.env`. V1 rules: independence is proven by
> the dispatch record (different endpoint + different secret reference), never claimed by the
> agent; a local-routed orchestrator/worker may spawn nested subagents only for actor/worker
> work and must refuse any nested subagent claiming a checker/verifier role (abstain/escalate
> instead); the existing `opus` checker pin in `VERIFIER-CONTRACT.md` is a tier pin only and does
> not certify endpoint independence; a future per-request router (b) is named as candidate
> follow-up content for #558/#559, gated on an unforgeable routing signal, credential separation,
> and no local→cloud fallback, each requiring its own human-reviewed spec per the #189
> precedent.

> **Q2 (confidentiality/destination policy surface):** At what granularity is a dispatch marked
> "local-only, no cloud export allowed" vs "cloud-fallback-allowed," what is the default, and
> which existing mechanism (if any) should the contract reuse as the evaluation point?
>
> **A:** A v1 dispatch is allowed only when the work item's **payload class**
> (`cloud_ok`/`local_only`, set per issue/run; stricter-only — no label/comment/model output can
> loosen it, only operator-controlled gitignored instance config or a human-reviewed adapter key
> can grant `cloud_ok`) matches the **destination class** declared on the target's
> endpoint-registry entry (#557; each entry carries hosting location `local`/`cloud` and the
> payload classes it may receive). Checked once, deterministically, at dispatch time; unknown on
> either side blocks and escalates, never falls back to cloud. The isolation unit is the run
> container (per Q1), not a DAG-node/phase marking. A third check: a checker dispatch must use an
> endpoint different from the actor's, proven by the dispatch record. **Defaults:** existing
> cloud-path dispatches are unchanged (`github/github/anthropic` keeps working exactly as today,
> no new gate on current runs); any local-routed dispatch with no explicit class defaults to
> `local_only` and never retries on cloud on failure (matches #238); a `local_only` payload that
> cannot get independent cloud verification must escalate to a human (`ready-for-human`), never
> merge unverified. **Reuse:** `.factory/adapter.yaml`'s `safety.sensitive_keywords` may be
> reused as a *stricter-only* signal (a hit forces `local_only` + escalation, never authorizes
> anything); `side_effect.phase_levels` must NOT be reused — it governs which tools a phase may
> run, not where data goes. All new fields/checks (payload-class field, registry destination
> fields, the dispatch-time check itself) are specified here only as follow-up content for
> #557/#558/#238, never as a diff to `config/config.yaml`/`.factory/adapter.yaml`. The decision
> record extends the existing `run_record`/`harness_economics` ledger, not a new store.

> **Q3 (capability evidence / versioning / correlation / timeouts):**
>
> **A (a — capability evidence):** Only a deterministic harness run can author capability
> evidence: #561's pinned fixture suite, executed inside #562's human-gated operator
> certification run (`authority: human`, `signal_source: verifier_evidence`). A model's own
> capability claim is `self_report` and can never grant or extend evidence; a later observed
> runtime failure (`deterministic_check`) may only downgrade/revoke it. Minimum record fields:
> `schema_version`, `endpoint_id` (#557 registry key), model identity (served name + immutable
> digest/revision, not just an alias), runtime/serving-stack identity+version, `fixture_suite_
> version` (pinned commit/digest), a per-capability-dimension result map (`pass`/`fail`/
> `not_tested` — `not_tested` counts as `fail` for any role needing that dimension), qualified
> roles (worker only — never checker, since independent verification stays cloud-routed),
> `certified_by`/`certified_at`, `expires_at`, and an `invalidated_by` hook so a changed model
> digest, runtime version, fixture-suite version, or registry config auto-stales the record.
> Missing, expired, failed, or mismatched evidence fails closed exactly like a failed
> certification — no grace period; the dispatch blocks and escalates, never falls back to cloud.
> A future grace window, if ever wanted, is #564's canary content, not this contract's.
>
> **A (b — versioning):** Each of the four contracts (configuration, request, result, capability
> evidence) carries its own independent `schema_version` (`"MAJOR.MINOR"`, starting `"1.0"`) —
> they change at different rates with different owners (#557 owns configuration; #558 owns
> request/result; #561/#562 own capability evidence), so a shared number would force lockstep
> bumps. The contract additionally names one contract-set version for this document
> (`hybrid_contract: "1.0"`). Compatibility rule: a minor bump is additive (unknown fields
> ignored by readers); a major bump is a hard reject (fail closed, same posture as missing
> evidence). These versions are *specified* here only — mirroring how #288 specified
> `"schema_version": "1.0"` for its own unbuilt `monitor_control` block — and are implemented by
> #557/#558/#561 as new additive top-level fields on `run_record.py`, following the existing
> `harness_economics`/`memory_trace` extension idiom. No code or config changes land from #556.
>
> **A (c — correlation/timeouts/cancellation):** The existing `current-run.json` pointer-file
> correlation (`model_proxy.py`) stays as the fallback — Q1 already fixes one endpoint per
> container, so it is accurate per run — but is not sufficient alone: it cannot give phase/role
> per request, and the acceptance criteria require request-level run/phase/role attribution. The
> contract specifies a request-correlation envelope (`run_id`, `issue`, `phase`, `role`
> [actor/worker/checker], `endpoint_id`, per-request id) that the runtime adapter attaches as a
> header — the never-built §7 per-persona header, rescoped to #558. Missing/inferred correlation
> is recorded explicitly (`correlation: "inferred_pointer"` or `"unknown"`), never guessed, and
> such rows can never themselves count as independence evidence (Q1). Timeouts/cancellation:
> policy only, not numbers — exact bounds are deferred to #558 (adapter defaults) and #560
> (local deployment profile), since they depend on hardware nobody has certified yet (#562).
> Every local endpoint-registry entry must declare explicit connect / time-to-first-token / idle
> / total timeout bounds, separate from cloud (today's proxy upstream call has no total/idle
> bound at all — `aiohttp.ClientTimeout(total=None, sock_connect=10)`); the runtime adapter
> enforces them, not the agent; every timeout or cancellation resolves to one of #238's new
> failure classes and is written to the run record — never a silent hang, never an unclassified
> error, never a retry to cloud. Timeout/cancel behavior is itself a capability dimension in
> (a), so #561's fixtures must exercise it. Cloud/proxy timeouts are untouched — out of scope
> per CLAUDE.md's gate/budget hard limit.

## Architecture / Approach

### 4.1 Responsibility map — nine initiative slices to acceptance predicates

| Slice (roadmap owner) | What it owns | Acceptance predicate this contract sets |
|---|---|---|
| Endpoint registry, secrets, preflight (#557) | Named endpoints, hosting location, destination class, secret references, timeout bounds, `required_env()`-style preflight | A registry entry is valid only with: id, hosting location, destination class(es), model identity, timeout bounds, secret reference, `schema_version`. Preflight hard-fails on any missing field (mirrors today's `providers/model.py` posture for `databricks`/`openai`) |
| Runtime adapter/gateway (#558) | The minimum code path that dispatches a request to the v1 local endpoint (an Anthropic-Messages-compatible local server, no translation layer — §4.11), preserving proxy/ledger and cloud parity | Must preserve §4.3's credential-stripping and correlation-envelope contract; must enforce §4.6's timeout/cancellation policy; must not introduce in-process cross-endpoint mixing (Q1) |
| Worker/orchestrator/checker isolation (#559) | Enforcing that a checker dispatch is a separate container with a different endpoint | Consumes §4.2's dispatch-record independence proof; must refuse (not silently downgrade) a nested-subagent checker claim |
| Local deployment profile/runbook (#560) | Connectivity/resource controls, operator documentation | Owns the actual timeout/resource numbers Q3(c) defers; no production default changes |
| Capability/role-isolation fixtures (#561) | The pinned test suite #562 runs | Must cover every capability dimension in Q3(a)'s result map, including timeout/cancel behavior |
| Operator endpoint certification (#562) | The only valid author of capability evidence | `ready-for-human`, hard-blocked; never scheduler-dispatched; no production activation |
| Feature-demand routing (#239, re-refinement) | Which endpoint a given task class is eligible for | Consumes §4.9's exact spec delta; stays parked (`needs-discussion`) until this contract is accepted |
| Failure-policy alignment (#238) | Typed failure classes, retry/fallback bounds, no-export-on-outage | Consumes Q2's confidentiality default and Q3(c)'s failure-class requirement; this contract does not duplicate #238's taxonomy, only the invariants it must satisfy |
| Isolated/shadow economics comparison (#563, under #234) | Cost/quality measurement before promotion | Reads the versioned ledger extension in §4.7; not a prerequisite for #557–#562 |
| Local workload canary and cloud rollback drill (#564, under #234) | One operator-run, opt-in local workload and a rehearsed return to cloud | `ready-for-human`; never scheduler-dispatched; exercises §4.12's rollback rule end to end and records the outcome in the run record; no production default changes |

**Separation of concerns (requirement 2):** protocol translation (how a request's shape is
converted — #558's job), route eligibility (which task classes may use which endpoint — #239's
job), and retry policy (how a failure is classified and whether/where it retries — #238's job)
are three different tickets with three different owners. This contract defines the invariants
each must hold, not the mechanism any of them use internally.

### 4.2 Actor / worker / checker coexistence and nested Agent-alias behavior

Building on Q1:

- **Roles.** `actor` (does the work), `checker`/`verifier` (independently judges the work),
  `human` (accepts/rejects). A `monitor` role (fast/slow, per the #288 taxonomy) is advisory-only
  and distinct from all three — it never dispatches work itself. `worker` and `orchestrator` are
  kinds of `actor`, and `verifier` is a synonym for `checker`; the normative value set for the
  correlation envelope's `role` field is in §4.4.
- **Endpoint-role binding happens at dispatch, not at the Agent-tool call.** A dispatched run
  container is assigned exactly one endpoint (cloud or a certified local endpoint) and may host
  `actor`/`worker`/`orchestrator` roles for that dispatch. A local-routed container may never
  also host an independent `checker` for the same unit of work — that checker must be a
  **separate dispatch**, and by default remains cloud-routed (per the issue's "independent
  verification stays correctly cloud-routed" definition of done).
- **Nested Agent-tool subagents inherit their parent's endpoint unconditionally** (this is
  native Claude Code behavior, not something this contract can change). Consequently: a
  local-routed orchestrator spawning an Agent-tool subagent may only label that subagent as
  further actor/worker work. If a command's phase logic calls for an independent checker
  subagent (e.g. the `VERIFIER-CONTRACT.md` pattern used by `plan`/`conformance`/`code-review`),
  and the orchestrating container is local-routed, the orchestrator **must refuse the
  delegation** — it cannot spawn that checker in-process and call it independent — and instead
  record an `UNCERTAIN`/abstain outcome requesting a separately-dispatched, cloud-routed checker
  run.
- **The refusal is enforced by the harness, not by the model.** A local-routed orchestrator
  declining to spawn a checker is only `self_report`; it is not what the contract relies on. The
  testable rule is: **for work produced by a local-routed actor**, a gate verdict is accepted
  only if the dispatch record of the run that produced it has `hosting_location: cloud` and an
  `endpoint_id` different from the actor's dispatch (the dispatch record's schema —
  `hosting_location`, `endpoint_id`, `correlation` — is #558's Result-contract row, §4.7). This
  bullet is the single, normative statement of the proof; Invariant 3 restates it. A verdict
  produced inside a local-routed container is void — it can neither pass nor fail a gate — and
  the ticket escalates to `ready-for-human`. That check is a `deterministic_check`-provenance
  event evaluated outside the agent. A cloud-routed actor's checker that happens to run in the
  same dispatch (today's `conformance`/`docs-conformance`/`code-review` nodes, and
  `refine`/`plan`'s in-process subagent checkers) needs no separate dispatch and is unaffected:
  this rule changes nothing about today's all-cloud path.
- **Consequence for today's DAG.** `conformance`, `docs-conformance` and `code-review` are nodes
  of the same run (same container, same `.archon/.env`) as `implement`, and `refine`/`plan` spawn
  their checkers in-process. No existing gated phase can therefore be local-routed under this
  contract (§4.11 decision 3). Splitting gates into a separately dispatched run changes
  `scheduler.sh` dispatch and the workflow DAG; that is gate-adjacent, out of scope here, and
  requires its own human-reviewed spec.
- **The existing tier-pin pattern is preserved, not replaced.** `VERIFIER-CONTRACT.md`'s rule
  ("never let the gating checker subagent inherit the orchestrator's model") continues to govern
  *which alias* a checker subagent uses within one endpoint. This contract adds the orthogonal
  rule that an endpoint-independent checker additionally cannot be a nested subagent at all when
  the parent is local-routed.

### 4.3 Confidentiality, destination policy, and non-exporting outage behavior

Per Q2 and Q3(c):

- **Declared payload class** (`payload_class`: `cloud_ok` / `local_only`, optional) is set per
  issue/run only by operator-controlled config (gitignored instance config or a human-reviewed
  adapter key) — never by a label, comment, or model output (all of which are `self_report`
  under #288's taxonomy). It is stricter-only: no label, comment or model output can loosen it.
- **Destination class** is declared per endpoint-registry entry (#557): hosting location
  (`hosting_location`: `local`/`cloud`) and which payload classes it may receive
  (`accepts_payload_classes`, a non-empty subset of `cloud_ok`/`local_only`). A `cloud` entry
  may never list `local_only`; a `local` entry must list `local_only`. The payload-class field
  and the dispatch-time predicate below are #557's to implement (not #558, whose scope excludes
  eligibility decisions).
- **Effective class**, computed once, deterministically, at dispatch time: a
  `local`-destination dispatch is `local_only` unconditionally, whatever the declared class; a
  `cloud`-destination dispatch is the declared class if one is set, else `cloud_ok` — exactly
  as today's unmarked ticket (§4.11 decision 4). An absent declared class on a cloud dispatch
  never blocks.
- **`local-pinned`** is a derived, never-operator-set property of a dispatch record
  (`hosting_location: local`), distinct from `payload_class`. It carries two rules that hold
  for *every* local dispatch regardless of declared class: no cloud retry or fallback, ever
  (Invariant 6), and no auto-merge, ever (§4.11 decision 5). `payload_class` carries only
  confidentiality/export eligibility — this separates "never retries on cloud" (a
  `local-pinned` property) from "this payload is confidential" (a `payload_class` property),
  the two meanings `local_only` previously conflated.
- **Dispatch-time check:** a dispatch is allowed if and only if the *effective* class is a
  member of the target entry's `accepts_payload_classes`. Blocks + escalates on: a declared
  value outside `{cloud_ok, local_only}`; a registry entry with invalid/missing
  `hosting_location` or `accepts_payload_classes`; or an effective class absent from the
  target's `accepts_payload_classes` (e.g. a declared-`local_only` ticket aimed at a cloud
  entry). Any of these three cases always blocks and escalates — it is never silently
  rerouted to a default; only an absent *declared* class on a cloud dispatch has a default
  (`cloud_ok`), and that is not a missing/unknown case. This check's decision (payload class,
  endpoint id, secret reference, allow/deny, reason) is recorded in the existing run-record
  ledger extension (§4.7), never a new store.
- **May a cloud checker read the output of a `cloud_ok` ticket that ran locally?** Yes — a
  `local`-routed dispatch whose **declared** class is `cloud_ok` or absent is `local-pinned` (no
  retry, no auto-merge) but not confidential; a separate cloud-routed checker dispatch may read
  its output, since the output inherits the confidentiality of inputs that were already
  cloud-eligible. A declared-`local_only` dispatch's output may be checked only by a human or a
  separate local dispatch — never a cloud checker. **Consequence:** v1 helper dispatches (§4.11
  decision 3) are limited to work whose declared class is `cloud_ok` or absent, since that is
  the only case with a valid independent-checker path.
- **No auto-merge (§4.11 decision 5, restated — strictly stronger, weakens nothing):** neither
  a `local-pinned` dispatch's output nor any `local_only`-classed payload ever auto-merges (no
  `direct-to-pr` end-gate auto-merge, no grace-timer advance to merge); the merge is always a
  human action. The scheduler end-gate check that enforces this is not owned by #557–#564; it
  needs its own human-reviewed gate ticket (#585).
- **Non-exporting outage behavior:** a `local-pinned` dispatch that fails (outage, OOM,
  protocol incompatibility, timeout, stale/missing capability evidence) never retries against
  a cloud endpoint — this is a property of being local-pinned, not of `payload_class`. It
  records the failure under one of #238's new failure classes and abstains/escalates. Two
  local failures, a model's stated confidence, or text in the task itself cannot grant export
  permission for a future dispatch — only operator-controlled config can, and such a later
  cloud run is always a *new* dispatch decision, never a runtime fallback of the failed
  local-pinned dispatch itself.
- **Credential stripping:** secrets never travel in the request-correlation envelope (§4.4's
  header), in any ledger row, run record, log line or issue comment — they are referenced by `secret_reference` (a lookup key into
  gitignored instance/gateway config, per #202's existing invariant), never inlined. This mirrors
  `model_proxy.py`'s existing `redact_headers()` posture (`authorization`/`x-api-key`/`api-key`/
  `x-factory-*-(token|secret)` redaction) and extends it: any new per-persona/correlation header
  this contract specifies must be added to that redaction set by whichever ticket implements it.
- **Credential scope for a local-routed container.** A local-routed container is issued no
  cloud model credential at all (not merely stripped before forwarding) and sends none to the
  local endpoint. It is issued no tracker or code-host write credential (e.g. no `GH_TOKEN`
  with push/comment/label scope) unless its own reviewed implementation spec explicitly grants
  one — #557 and #558 already require this for the endpoint/runtime-adapter surfaces they own;
  this contract states it as a cross-cutting invariant so #559–#564 inherit it too.
- **Unqualified/unverifiable routes must abstain or escalate, never fail open.** This applies
  uniformly to: missing capability evidence (§4.5), payload/destination mismatch, correlation
  that cannot be attributed (§4.4), and any local endpoint timeout/cancellation (§4.6).

### 4.4 Request correlation

Per Q3(c): the request-correlation envelope is `{run_id, issue, phase, role, endpoint_id,
request_id}`. The runtime adapter (#558) is responsible for attaching it; `model_proxy.py`'s
existing `current-run.json` pointer-file read remains the fallback when a header is absent (e.g.
an older/non-adapter-routed call), recorded explicitly as `correlation: "inferred_pointer"`
rather than silently upgraded to a `deterministic_check`-equivalent confidence level. A row with
`correlation: "unknown"` can never be cited as the dispatch-record proof of independence
required by §4.2.

The envelope's `role` field takes exactly one of `actor`, `checker`, `monitor`. Workers and
orchestrators report `actor`; `verifier` is not a separate value; `human` is never a request
role. Any other value is recorded as `correlation: "unknown"`. This supersedes the looser
`[actor/worker/checker]` shorthand in the Q3(c) answer above.

### 4.5 Capability evidence

Per Q3(a). A capability-evidence record (schema below) may only be authored by #562's operator
certification run executing #561's pinned fixtures. See the full field list and fail-closed rule
in the Q3(a) answer above; reproduced here as the normative schema shape:

```text
capability_evidence:
  schema_version: "1.0"
  endpoint_id: <#557 registry key>
  model_identity: {served_name, digest_or_revision}
  runtime_stack: {name, version}
  fixture_suite_version: <pinned commit/digest>
  results: {<capability_dimension>: pass | fail | not_tested, ...}
  qualified_roles: [worker]          # never "checker" — see §4.2
  certified_by: <operator identity>
  certified_at: <ISO8601>
  expires_at: <ISO8601>
  invalidated_by: [model_digest, runtime_version, fixture_suite_version, registry_config]
```

Capability dimensions (minimum set #561's fixtures must cover): protocol compatibility, tool-call
fidelity, streaming behavior, context-length handling, role-isolation (confirms the endpoint
cannot be mistaken for an independent checker), and timeout/cancellation behavior (§4.6).

### 4.6 Timeouts and cancellation (policy)

Per Q3(c). Every local endpoint-registry entry must declare explicit connect / time-to-first-
token / idle / total timeout bounds — distinct from cloud, and mandatory (an entry without them
is invalid config and fails preflight, the same posture `providers/model.py` already uses for
`databricks`/`openai`). The runtime adapter enforces these bounds; the agent does not self-police
its own timeout. Every timeout or cancellation event resolves to one of #238's new failure
classes and is written to the run record — never a silent hang, never an unclassified error,
never an implicit retry to a different endpoint.

The class names are #238's to choose, but its taxonomy must distinguish at least these
local-endpoint outcomes: endpoint unreachable; timeout (connect, time-to-first-token, idle,
total); cancellation; protocol incompatibility; capability evidence missing, expired or
mismatched; destination-policy denial. None of them is retryable against a different endpoint.

### 4.7 Versioned contracts and ledger extension

Per Q3(b). Four independently-versioned contracts (`schema_version` each, `"MAJOR.MINOR"`,
starting `"1.0"`), plus one document-level `hybrid_contract: "1.0"` version for this spec itself:

| Contract | Owner | Carries |
|---|---|---|
| Configuration | #557 | Endpoint registry entry fields (§4.1, §4.6) |
| Request | #558 | Correlation envelope (§4.4) + payload class |
| Result | #558 | Outcome, failure class (if any), destination actually used |
| Capability evidence | #561/#562 | §4.5's schema |

Compatibility rule: minor bumps are additive and forward-compatible (unknown fields ignored);
major bumps are a hard reject, handled with the same fail-closed posture as missing capability
evidence or an unknown payload/destination class. All four are specified here only; #557/#558/
#561 implement them as new additive top-level fields on `run_record.py`'s existing per-run
record, following the `harness_economics`/`memory_trace` extension idiom — no new datastore
(consistent with the #207 architecture-memory entry below).

### 4.8 Compatibility matrix — helper boundary vs. full Claude-harness compatibility

The issue requires this contract to decide the boundary explicitly:

| Capability | Native cloud (Anthropic/Bedrock/Vertex) | Certified local endpoint (v1) |
|---|---|---|
| Actor/worker dispatch | Yes (today) | v1: checker-free helper dispatches only, once #557–#562 ship and the endpoint is certified; none of the existing gated phases (§4.11 decision 3) |
| Independent checker/verifier role | Yes (today) | **No — always a separate cloud dispatch** (§4.2) |
| Nested Agent-tool subagent fanout | Yes, inherits parent endpoint | Yes for further actor/worker work only; refused for checker claims (§4.2) |
| Tool-use / MCP fidelity | Assumed (reference implementation) | Must be proven by #561 fixtures before any dispatch (§4.5) — never assumed |
| Streaming | Yes | Must be proven by #561 fixtures |
| Cost/token accounting | Exact (Claude pricing) | Token counts recorded; `cost_usd` is `null` (never zero, never Claude-priced) until #558 extends the ledger per endpoint (§4.10; avoids the mislabeled-dollar Databricks/OpenAI caveat in `docs/provider-abstraction-design.md` §7.5) |
| Cloud fallback on failure | N/A (already cloud) | **Never automatic** — fails closed to abstain/escalate (§4.3) |

A native OpenAI/Databricks helper (already the explicit non-goal boundary in
`docs/provider-abstraction-design.md` §2) does not certify the whole Claude runtime, and neither
does a certified local endpoint: v1's boundary is a **bounded worker/actor helper role**, not
drop-in harness replacement. Any later expansion of the local endpoint's role set is itself a
reviewed-spec decision, not a consequence of this contract.

### 4.9 Exact spec delta for #239

#239 is correctly parked (`needs-discussion`, `Depends on: #556` + `Depends on: #288`) and must
not be re-refined before this contract is accepted. When it is re-refined, in addition to the
eleven items #288's archived spec §4.9 already lists, #239's spec must add:

12. Route eligibility for a `local` destination class is gated on §4.5's capability evidence for
    the specific endpoint being considered — never on the task's own text, a label, or a model's
    self-reported capability claim (restates Q3(a) in #239's own terms, since #239 owns the
    routing decision itself).
13. §4.3's payload/destination predicate is a hard gate #239's router evaluates before any cost
    consideration, never a preference it can override: a `local_only` payload is eligible for
    local entries only, and a `cloud_ok` payload routed to a local entry is treated as
    `local_only` from dispatch onward (no cloud retry). A cost saving alone never makes a task
    eligible for a `local` endpoint.
14. §4.2's rule that an independent checker is never a nested subagent, and never shares a
    dispatch with a local-routed actor, is a routing-time constraint #239 must enforce when it
    decides whether a task is eligible for local-endpoint dispatch at all (a task requiring a
    checker in the same dispatch is categorically ineligible for `local`).
15. §4.7's versioned request/result contracts are the data #239's calibration cohorts (per
    #288 §4.9 item 2) must be built from — #239 may not invent a parallel telemetry shape.

### 4.10 Budgets, breaker and session-window invariants

Local routing changes no budget, breaker, session-window or `gate_*` behaviour, and this contract
authorizes no change to any of them:

- Local-routed runs count toward WIP limits, dispatch ceilings and the retry breaker exactly like
  cloud runs. A local endpoint is not a way around any of them.
- Token budgets (`token_optimization.*`, `budget_gate.sh`) apply unchanged to local-routed runs.
- Local token counts are recorded. Local cost is recorded as `cost_usd: null` — never zero and
  never Claude-priced — so economics reports cannot read local work as free (§4.8).
- An active session-window pause keeps gating all dispatch, local included. Exempting local
  dispatches from the pause would be a scheduler change and is not part of this contract.
- A local endpoint's rate-limit or HTTP 429 must never write the shared session-window pause
  sentinel: `session_window.py` detects session-window exhaustion from usage/session/credit-
  exhaustion phrasing or a rejected structured `claude.rate_limit_event` marker
  (`is_session_window_failure`) — not from a bare HTTP 429 (a bare 429 only feeds the breaker's
  `environmental:rate_limit` signature via the separate, broader `RATE_LIMIT_RE`, never the
  stricter session-pause detector). A local endpoint that emits usage/session/credit-exhaustion
  phrasing, or a fabricated `claude.rate_limit_event` marker, in captured stdout could still trip
  the shared pause by coincidence. Such an event is a local failure under §4.6. The change that
  makes the session-window detector local-endpoint-aware touches session-window logic and
  therefore needs its own human-reviewed ticket (#586), a rollout precondition in §4.12, and may
  not land as a side effect of #557–#561.
- Any change a downstream ticket needs to budgets, the breaker, the session-window pause,
  `gate_*`, tool allow/deny lists, side-effect levels or `deploy/**` requires its own
  human-reviewed spec. Neither this contract nor comment-channel input authorizes one.

### 4.11 v1 selected path and accepted operator defaults

The issue requires this contract to select the minimum supported cloud + one local path and to
define model/tier resolution. The five decisions below are accepted operator defaults, confirmed
as written by the owner in #580. Downstream tickets build against them as written; a changed
decision is a revision of this contract, not a downstream re-decision.

1. **v1 local protocol** — *Accepted operator default (2026-11-22; confirmed by the owner
   in #580).* The cloud path is unchanged (`anthropic`, plus the existing native
   `bedrock`/`vertex` paths). The one supported local path is a local server that speaks the
   Anthropic Messages API, reached through `ANTHROPIC_BASE_URL`, with no translation layer. An
   OpenAI-shape or other non-Messages backend is out of scope for v1; #558 builds no translator.
2. **Tier resolution on a local endpoint** — *Accepted operator default (2026-11-22; confirmed
   by the owner in #580).* All four tier aliases
   (`ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU,FABLE}_MODEL`) resolve to the one certified local model
   named in the registry entry's model identity. An unset alias, or one that resolves to anything
   other than the certified model, fails preflight. Cloud alias pins in `run-compose.yml` are
   untouched.
3. **v1 local-eligible work** — *Accepted operator default (2026-11-22; confirmed by the
   owner in #580).* None of the existing gated phases (refine, plan, implement, conformance,
   docs-conformance, code-review) may be local-routed, because each runs its checker in the same
   container (§4.2). Only new **helper dispatches** are eligible — defined here concretely: a
   helper dispatch is a separately scheduled run container with its own endpoint config, doing
   non-gating work with no in-container checker, whose output is only an artifact until a
   cloud-routed check in a *different* dispatch accepts it (proven by the dispatch record,
   §4.2). In v1 a helper dispatch is limited to work whose declared payload class is `cloud_ok`
   or absent (§4.3). No ticket among #557–#564 builds the first concrete helper-dispatch
   instance — their scopes are registry/preflight, runtime adapter, route isolation, deploy
   profile, fixtures, certification, offline/shadow economics comparison, and the opt-in
   canary. #587 builds it, and depends on #585 (the no-auto-merge end-gate) and #586
   (local-aware session-window detection). Running a real phase locally needs gates split into
   a separate dispatch; that is its own reviewed gate ticket and is out of scope for this
   contract and for #557–#561.
4. **Default payload class** — *Accepted operator default (2026-11-22; confirmed by the owner
   in #580).* An unmarked ticket is `cloud_ok` on cloud, as today. Anything
   dispatched locally is `local_only` (§4.3).
5. **Merge authority for `local_only` work** — *Accepted operator default (2026-11-22;
   confirmed by the owner in #580).* `local_only` work never auto-merges; the merge is
   always a human action (§4.3).

### 4.12 Risks and rollout/rollback rules

| Risk | Control in this contract |
|---|---|
| A local model does lower-quality work unnoticed | Capability evidence before any dispatch (§4.5); v1 limited to checker-free helper dispatches (§4.11) |
| Private payload is exported to cloud | Membership predicate, stricter-only classes, fail-closed on unknown, no cloud retry (§4.3) |
| Checker independence is asserted rather than proven | Harness-side dispatch-record check; a local-routed verdict is void (§4.2) |
| A local fault pauses or miscounts cloud work | §4.10 invariants |
| Local work reads as free in economics reports | `cost_usd: null`, never zero (§4.10) |
| Evidence goes stale after a model or runtime change | `invalidated_by` and `expires_at`, no grace period (§4.5) |
| An endpoint credential leaks | References only; never in envelope, ledger, run record, log or comment (§4.3) |

**Rollout rules.** Downstream tickets are opted in one at a time, in the order of their
`Depends on:` lines. Merging any of them activates nothing: local endpoint selection is off by
default, and is enabled only by operator-controlled instance config (a human-only surface —
`deploy/instances/**` is never edited by a factory agent) after #562 has produced valid
capability evidence for that endpoint. #562 and #564 are human-run and never scheduler-dispatched.

**Rollback rule.** Rollback is unsetting the local endpoint selection in that operator-controlled
config. With it unset, behaviour is byte-identical to today's `github/github/anthropic` default;
no data migration is involved and run records are retained. A local run in flight at rollback
fails closed and escalates; it is never re-run on cloud automatically. #564 rehearses this rule
once before any wider use.

## Alternatives Considered

1. **Build the full per-request routing gateway now (Q1 option b)**, letting one container mix
   local and cloud endpoints per call. Rejected for v1: the gateway was never built (§11 step 5
   of `docs/provider-abstraction-design.md`), a per-request router keyed on an agent-set header
   is `self_report` provenance under #288 and can never authorize endpoint selection, and
   co-locating both backends' credentials in one process conflicts with #238's no-fail-open rule.
   Named as legitimate follow-up content for #558/#559, gated on an unforgeable signal and
   credential separation, each needing its own human-reviewed spec.
2. **Allow a bounded grace period for missing/expired capability evidence** rather than failing
   closed. Rejected — nothing in this initiative is in production yet (#562 is hard-blocked,
   #564 is a human canary), so a grace period only lets an uncertified or stale endpoint do real
   work with no cost saved. Left to #564 if an operator ever wants it once there is a production
   posture to protect.
3. **Reuse `.factory/adapter.yaml`'s `safety.sensitive_keywords` as the primary confidentiality
   gate**, rather than a dedicated payload-class field. Rejected as the sole mechanism — it is
   an advisory, evadable regex over ticket text, fine as a stricter-only signal (§4.3) but not
   an egress control; a dedicated, operator-controlled payload-class field is the actual gate.
4. **Reuse `side_effect.phase_levels` to express destination policy.** Rejected — that config
   governs which tools/commands a phase may execute (#196's unbuilt enforcement scope), an
   orthogonal concern to where model-request data is sent; conflating the two would make a
   confidentiality change look like (and require the review bar of) a side-effect/gate change.
5. **One shared `schema_version` across configuration/request/result/capability contracts.**
   Rejected — the four contracts have different owners (#557/#558/#558/#561-#562) and change at
   different rates; a shared version would force lockstep bumps across unrelated tickets.
6. **Trust a model's self-reported capability or confidence as dispatch authorization**
   (e.g., "I can handle this locally"). Rejected directly by the accepted #288 decision's
   `signal_source` taxonomy — `self_report` can never authorize a consequential action; only
   `verifier_evidence` (here, #562's certification, `authority: human`) may.
   (`empirical_calibration` is a value of #288 §4.9's `confidence_source` taxonomy for #239, not
   of `signal_source`.)

## Open Questions (non-blocking)

- Exact timeout/resource numbers for local endpoints (§4.6) — deferred to #558/#560, which have
  access to actual certified hardware this contract does not.
- Whether a future per-request routing layer (Q1 option b) is ever worth building, and under
  what unforgeable-signal design — left to a future #558/#559 follow-up spec if the
  container-granularity boundary proves too coarse in practice.
- Exact minimum-N and calibration-cohort population for #239's routing eligibility data —
  depends on real certified-endpoint telemetry that does not exist yet (§4.9 item 15).
- Whether `GITLAB`-style unregistered-stub precedent (`providers/codehost/gitlab.py`) is the
  right shape for a not-yet-certified local endpoint entry in #557's registry (declared but
  inert until certified) — left to #557's own refinement.

## Assumptions (flagged)

- This checkout's state (`docs/provider-abstraction-design.md`, `scripts/factory_core/
  providers/model.py`, `scripts/factory_core/model_proxy.py`, `run-compose.yml`) was read live on
  2026-11-21 and is assumed representative of `main`; the branch head at refinement time matches
  the merge-base with `main` (no stale-branch drift of the kind `docs/superpowers/specs/
  2026-11-19-effective-config-widening-design.md` had to correct for).
- Issues #202, #234, #238, #239, #288 (archived spec), #557–#564 were read live via `gh issue
  view` at refinement time (2026-11-21) and are assumed stable; if any has since been
  substantially re-scoped, §4.1's responsibility map and §4.9's delta should be re-checked
  against the current body before being relied on.
- "Independent verification stays correctly cloud-routed" (the issue's own definition of done)
  is read as a hard v1 invariant, not a target-state aspiration — §4.2 and §4.8 encode it as
  such throughout.
- The nine "initiative slices" in acceptance criterion 2 are read as the nine rows of §4.1's
  responsibility map, counting #563 and #564 (both under #234) as one slice shown as two rows
  (#557, #558, #559, #560, #561, #562, #239, #238, #563+#564); no
  other explicit enumeration of "nine slices" exists in the issue body or linked issues.
