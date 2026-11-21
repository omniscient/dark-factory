# Hybrid Model Execution Contract — Cloud + Local

**Issue:** #556

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
   contracts, risks, and rollout/rollback rules. (§Architecture, all subsections)
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
| Runtime adapter/gateway (#558) | The minimum code path that actually dispatches a request to a non-Anthropic-shape local backend, preserving proxy/ledger and cloud parity | Must preserve §4.3's credential-stripping and correlation-envelope contract; must enforce §4.2's timeout/cancellation policy; must not introduce in-process cross-endpoint mixing (Q1) |
| Worker/orchestrator/checker isolation (#559) | Enforcing that a checker dispatch is a separate container with a different endpoint | Consumes Q1's dispatch-record independence proof; must refuse (not silently downgrade) a nested-subagent checker claim |
| Local deployment profile/runbook (#560) | Connectivity/resource controls, operator documentation | Owns the actual timeout/resource numbers Q3(c) defers; no production default changes |
| Capability/role-isolation fixtures (#561) | The pinned test suite #562 runs | Must cover every capability dimension in Q3(a)'s result map, including timeout/cancel behavior |
| Operator endpoint certification (#562) | The only valid author of capability evidence | `ready-for-human`, hard-blocked; never scheduler-dispatched; no production activation |
| Feature-demand routing (#239, re-refinement) | Which endpoint a given task class is eligible for | Consumes §4.9's exact spec delta; stays parked (`needs-discussion`) until this contract is accepted |
| Failure-policy alignment (#238) | Typed failure classes, retry/fallback bounds, no-export-on-outage | Consumes Q2's confidentiality default and Q3(c)'s failure-class requirement; this contract does not duplicate #238's taxonomy, only the invariants it must satisfy |
| Isolated/shadow economics comparison (#563, under #234) | Cost/quality measurement before promotion | Reads the versioned ledger extension in §4.7; not a prerequisite for #557–#562 |

**Separation of concerns (requirement 2):** protocol translation (how a request's shape is
converted — #558's job), route eligibility (which task classes may use which endpoint — #239's
job), and retry policy (how a failure is classified and whether/where it retries — #238's job)
are three different tickets with three different owners. This contract defines the invariants
each must hold, not the mechanism any of them use internally.

### 4.2 Actor / worker / checker coexistence and nested Agent-alias behavior

Building on Q1:

- **Roles.** `actor` (does the work), `checker`/`verifier` (independently judges the work),
  `human` (accepts/rejects). A `monitor` role (fast/slow, per the #288 taxonomy) is advisory-only
  and distinct from all three — it never dispatches work itself.
- **Endpoint-role binding happens at dispatch, not at the Agent-tool call.** A dispatched run
  container is assigned exactly one endpoint (cloud or a certified local endpoint) and may host
  `actor`/`worker`/`orchestrator` roles for that dispatch. It may never also host an independent
  `checker` for the same unit of work — that checker must be a **separate dispatch**, and by
  default remains cloud-routed (per the issue's "independent verification stays correctly
  cloud-routed" definition of done).
- **Nested Agent-tool subagents inherit their parent's endpoint unconditionally** (this is
  native Claude Code behavior, not something this contract can change). Consequently: a
  local-routed orchestrator spawning an Agent-tool subagent may only label that subagent as
  further actor/worker work. If a command's phase logic calls for an independent checker
  subagent (e.g. the `VERIFIER-CONTRACT.md` pattern used by `plan`/`conformance`/`code-review`),
  and the orchestrating container is local-routed, the orchestrator **must refuse the
  delegation** — it cannot spawn that checker in-process and call it independent — and instead
  record an `UNCERTAIN`/abstain outcome requesting a separately-dispatched, cloud-routed checker
  run. This refusal is itself a `deterministic_check`-provenance event, not a judgment call by
  the orchestrator's own model.
- **The existing tier-pin pattern is preserved, not replaced.** `VERIFIER-CONTRACT.md`'s rule
  ("never let the gating checker subagent inherit the orchestrator's model") continues to govern
  *which alias* a checker subagent uses within one endpoint. This contract adds the orthogonal
  rule that an endpoint-independent checker additionally cannot be a nested subagent at all when
  the parent is local-routed.

### 4.3 Confidentiality, destination policy, and non-exporting outage behavior

Per Q2 and Q3(c):

- **Payload class** (`cloud_ok` / `local_only`) is set per issue/run, stricter-only, grantable
  only by operator-controlled config — never by a label, comment, or model output (all of which
  are `self_report` under #288's taxonomy).
- **Destination class** is declared per endpoint-registry entry (#557): hosting location
  (`local`/`cloud`) and which payload classes it may receive.
- **Dispatch-time check:** payload class must match destination class, evaluated once,
  deterministically, before dispatch. Unknown on either side → block + escalate. This check's
  decision (payload class, endpoint id, secret reference, allow/deny, reason) is recorded in the
  existing run-record ledger extension (§4.7), never a new store.
- **Non-exporting outage behavior:** a `local_only` dispatch that fails (outage, OOM, protocol
  incompatibility, timeout, stale/missing capability evidence) never retries against a cloud
  endpoint. It records the failure under one of #238's new failure classes and abstains/
  escalates. Two local failures, a model's stated confidence, or text in the task itself cannot
  grant export permission — only operator-controlled config can (same rule as the payload-class
  grant above).
- **Credential stripping:** secrets never travel in the request-correlation envelope (§4.3's
  header) or in any ledger row — they are referenced by `secret_reference` (a lookup key into
  gitignored instance/gateway config, per #202's existing invariant), never inlined. This mirrors
  `model_proxy.py`'s existing `redact_headers()` posture (`authorization`/`x-api-key`/`api-key`/
  `x-factory-*-(token|secret)` redaction) and extends it: any new per-persona/correlation header
  this contract specifies must be added to that redaction set by whichever ticket implements it.
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
| Actor/worker dispatch | Yes (today) | Yes, once #557–#562 ship and the endpoint is certified |
| Independent checker/verifier role | Yes (today) | **No — always a separate cloud dispatch** (§4.2) |
| Nested nested Agent-tool subagent fanout | Yes, inherits parent endpoint | Yes for further actor/worker work only; refused for checker claims (§4.2) |
| Tool-use / MCP fidelity | Assumed (reference implementation) | Must be proven by #561 fixtures before any dispatch (§4.5) — never assumed |
| Streaming | Yes | Must be proven by #561 fixtures |
| Cost/token accounting | Exact (Claude pricing) | Approximate until #558 extends the ledger per endpoint (mirrors the existing Databricks/OpenAI caveat in `docs/provider-abstraction-design.md` §7.5) |
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
13. Any routing outcome that would select a `local` endpoint for a task whose payload class is
    `cloud_ok`-only-by-default (i.e., no explicit `local_only`) must not be treated as a
    cost-saving opportunity by itself — §4.3's payload/destination match is a hard gate #239's
    router evaluates, not a preference it can override.
14. §4.2's rule that an independent checker is never a nested subagent, and never shares a
    dispatch with a local-routed actor, is a routing-time constraint #239 must enforce when it
    decides whether a task is eligible for local-endpoint dispatch at all (a task requiring a
    checker in the same dispatch is categorically ineligible for `local`).
15. §4.7's versioned request/result contracts are the data #239's calibration cohorts (per
    #288 §4.9 item 2) must be built from — #239 may not invent a parallel telemetry shape.

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
   `empirical_calibration`/`verifier_evidence` (here, #562's human certification) may.

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
  responsibility map (#557, #558, #559, #560, #561, #562, #239, #238, #563/#564 combined); no
  other explicit enumeration of "nine slices" exists in the issue body or linked issues.
