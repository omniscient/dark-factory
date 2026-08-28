# Contract-driven execution and trajectory conformance — spike decision report

**Issue:** omniscient/dark-factory#311
**Status:** spec-only deliverable; amended 2026-08-28 after the operator's spec-gate review
(handoff section for #197/#198, gate/run-record/breaker inventory, owner-conflict ruling, fact
corrections). Per operator instruction (issue comment, 2026-08-28): "Refine
as a spec-only deliverable where the ticket says spike; spec gate will be reviewed by the
operator." This document **is** the spike's durable decision report — no separate comment-only
report and no implementation follow through this ticket. Precedent for rendering a paper-spike's
full evaluation directly in the refine-phase spec (rather than a methodology-now/verdict-later
split) is `.archon/memory/architecture.md` (issue #189, 2026-08-21). That precedent covers the
desk-research portion of this spike — deliverables 1-5 and 7-10, whose evidence base is the cited
book plus this repo's own inspectable history; deliverable 6 (the baseline/ablation) requires live
replay execution and is deferred to #240 with the named-gap note in the deliverables checklist
below.
**Parent context:** epic #194 (loop-contract architecture). Sibling/downstream tickets referenced
throughout, none of which this document modifies: #301 (adapter `loops:` schema extension, A1.5),
#197 (independent contract/trajectory verifier), #198 (successful-stop / completion predicate),
#240 (replay/evalset substrate and economics), #190 (state governance — already shipped, see
below), #242 (behavioral-state decay baseline — already shipped, see below).

---

## Overview / Problem Statement

Issue #311 is a research spike evaluating *Agentic Design Patterns* (Antonio Gulli) against Dark
Factory's architecture. Source scope: only *Agentic Design Patterns* (plus arXiv 2606.30306,
"Always-On Agents", indirectly via #190's shipped scorecard) is cited in this spec; the research
roadmap's other sources (Loop Engineering, Metacognition) are not cited and are not evaluated here. The issue body already contains a thorough 21-pattern taxonomy mapping,
evidence-quality classification, and backlog-ownership decision, independently corroborated by
three prior section reviews recorded in its own comments. That analysis's headline conclusion
holds up against current `main` (re-verified below) and this refine pass does not relitigate it:
**use the book as a taxonomy/checklist, not adoption guidance; do not create a new epic; the
highest-value synthesis is treating a resolved execution contract plus observable trajectory
conformance as the missing piece that let #300 (a silently-missing mandatory report) pass as
complete.**

What this refine pass adds, and the reason it was worth a spec rather than a straight republish
of the issue body:

1. **The issue's own "Current-state evidence" section is stale and incomplete.** It was written
   against commit `37b76d5e` and states flatly that "current code contains no `completion_contract`,
   `trajectory_contract`, `delivery_pending`, required-deliverable manifest, or general
   report-acknowledgement gate." That specific claim is still true on current `main` (`f283346`,
   re-verified by direct grep — no hits). But the surrounding claim that this is uncharted territory
   is not: **#190 and #242 have shipped substantial adjacent infrastructure** — a structured
   governed-event schema, five deterministic trajectory checks with an advisory scorer, and a
   labeled historical-incident fixture corpus with an anti-future-leakage methodology. #242 shipped
   three days before the issue's 2026-07-19 inspection and was missed; #190 merged on 2026-08-25
   (PR #353), five weeks after the inspection, so the issue could not have cited it — but this
   spike's Wave-2 consumers (#197/#198/#301/#240) must. See
   Architecture/Approach below for what exists and exactly what gap remains.
2. **Two scope-discipline questions needed resolving** before the spike's schema/harness
   recommendations could be written down safely: how prescriptive to be about #301's field-naming
   sequencing, and how prescriptive to be about #240's replay-substrate mechanism. Both are answered
   in Brainstorming Q&A below and shape the Architecture/Approach section's framing.

---

## Requirements

Distilled from the issue's own acceptance criteria and spike-deliverables list, the operator's
spec-only-deliverable instruction, and the Q&A below. This spec is complete when:

1. All 21 book patterns remain classified (existing / backlog-owned / bounded-adoption /
   future-only / rejected) — carried over from the issue body, re-affirmed, not re-derived.
2. The "Current-state evidence" section is re-verified against current `main`, not trusted from the
   issue's July inspection, and corrected where stale (Requirement driven by the `.archon/memory`
   AVOID entry for issue #182: "Always re-verify line-number citations in an issue body/prior spec
   against current main before planning work on a long-lived entrypoint.sh ticket." — applied here
   to state citations generally.)
3. Every piece of adjacent infrastructure already in the repo (event schemas, checkers, fixture
   corpora, promotion-criteria precedent) that materially changes what #240/#197/#198/#301 would
   otherwise build from scratch is inventoried and cited, per the `codebase-patterns.md` AVOID entry
   for issue #48 ("before declaring historical data 'not measurable,' check whether a durable sink
   already exists") and the general instruction that AVOID entries are especially relevant to spec
   decisions.
4. The schema-delta recommendation to #301 and the replay-substrate recommendation to #240 are both
   framed as non-binding evidence + recommendation, never as a diff or a mandate — per Q&A below and
   per the `.archon/memory` AVOID entry for issue #189 ("a refine spec may still design mechanics on
   paper... but must frame the whole design as recommended follow-up-ticket content needing separate
   human review, and must not draft diffs against existing gate files").
5. No recommendation in this document, if followed literally, would let #301 or #240 ship
   Done-transition-blocking or gate-changing behavior — that stays exclusively with #197/#198, each
   getting their own reviewed ticket, per CLAUDE.md's hard limit and the AVOID entry recorded during
   #300's refinement for exactly this Hermes-Agent-authored-completion-contract situation.
6. This document is the spike's complete, final "durable decision report" — the issue's own
   "Spike deliverables" list (10 items) is satisfied by sections of this spec, mapped explicitly in
   a checklist (see end of Architecture/Approach).
7. Out of scope: any code, schema, config, or test change. This refine command's scope boundary
   (docs/superpowers/specs/ and .archon/memory/ only) makes this a hard constraint, not a judgment
   call.

---

## Brainstorming Q&A

> **Q:** Given (a) the issue explicitly says its contract-shape YAML sketch is "a research
> candidate, not an approved schema" that #301 must refine, (b) the repo already has a precedented
> reserved-field carve-out pattern (`_RESERVED_LOOP_FIELDS = {"memory_intervention": "#241"}`,
> `_RESERVED_TOP_FIELDS = {"mechanism_candidates": ...}` in `scripts/factory_core/adapter.py`,
> shipped exactly this way during #195's own refinement per a recorded AVOID entry), and (c) #311
> cannot itself touch code — should this spec *recommend* that #301 ship a reserved-key carve-out
> (e.g. `contract`/`verification` loop keys, hard-rejected, naming this ticket family) as an
> immediate first step ahead of the full schema, or stay silent on sequencing and describe only the
> target end-state shape?
>
> **A:** Recommend the carve-out, but frame it explicitly as a non-binding sequencing suggestion in
> its own labeled subsection, not a schema decision. Grounds: the issue's own spike deliverable #2
> ("exact schema delta proposed for #301, with parity-when-absent behavior") already calls for
> exactly this — a reserved-key carve-out *is* minimal parity-when-absent behavior (absent → today's
> semantics unchanged; present → targeted `AdapterError` naming the tracking ticket), so staying
> silent under-delivers a named deliverable. The precedent is exact, not analogous: `#195`'s own
> refinement recorded ("ship a minimal reserved-key-rejection carve-out... and file the substantive
> extension as an immediate follow-up ticket") after five Hermes-Agent research proposals arrived
> post-spec, and that's the same situation #301 will face when it picks up #311's output. Overstep
> risk is structurally bounded because #301 owns final names/nesting/whether to take the
> recommendation at all, and #311 emits no code regardless. **Hard constraint carried into the
> recommendation text:** scope it to *inert validation only* (reject the reserved names with a
> pointer comment) — never suggest #301 ship any Done-transition-blocking or completion-predicate
> enforcement, which stays with #197/#198 per the #300 AVOID entry and CLAUDE.md's "gate changes get
> their own reviewed ticket."

> **Q:** #240 owns building the actual replay/evalset substrate. The repo already has
> `evals/factory-failures.jsonl` (raw exit_code+postmortem capture, no structured trajectory) and,
> as later verified during this same research pass, `evals/state-governance/` (#190) and
> `evals/behavioral-state/` (#242) — considerably more structured prior art than initially surfaced.
> Should this spec's description of the "locked historical replay set" (spike deliverable #5)
> explicitly recommend #240 evaluate reusing/extending this existing substrate, or stay silent on
> implementation substrate and describe only fixture *content* requirements?
>
> **A:** Include the substrate evidence as a non-binding recommendation, not a design mandate —
> staying silent would itself violate this repo's own refine-phase rules, not just be conservative.
> Two AVOID entries bind this directly: the issue-#48 entry (always check whether a durable sink
> already exists before declaring data "not measurable" — directly on point for #311's
> tokens/cost/wall-clock metrics, which `entrypoint.sh`'s `post_cost_report()` already posts under
> the `dark-factory-cost-report` marker, minable the same way conformance/code-review verdicts are);
> and the issue-#50 entry (verify a prior spike's claimed artifacts actually exist in the tree before
> citing them as precedent — everything cited below was opened directly, not inferred from a commit
> message). The distinction that keeps this out of #240's territory: state the *gap*, not the
> *mechanism*. Legitimate: "the substrate must carry structured per-event trajectories;
> `factory-failures.jsonl` today carries only `{exit_code, free-text postmortem}` and cannot support
> required-event precision/recall as-is — a gap #240 must close, and `evals/state-governance/`'s
> event envelope is one candidate starting shape, already built for a different but related
> purpose." Overstepping would be prescribing that #240 literally extend a specific file with a
> specific new field. Put fixture *content* requirements (named incidents, anti-leakage rules) under
> Requirements (binding); put the substrate-reuse framing under Alternatives Considered / Open
> Questions (non-binding, #240 free to reject with justification).

---

## Architecture / Approach

### Current-state evidence (re-verified against `main` `f283346`, superseding the issue's `37b76d5e`-era snapshot)

- `main` has advanced more than twenty PRs past the issue's inspection point `37b76d5e`
  (2026-07-18). Gate surfaces did change: #183 consolidated the budget gates (PR #322), #184
  single-sourced the safety defaults (PR #324), #185 added scheduler seams (PR #320), #190 itself
  landed (PR #353), the #189/#207 spikes were archived (PRs #351/#350), and four of the six named
  replay incidents have since been repaired (#271 → PR #328, #279 → PR #325, #280 → PR #323,
  #293 → PR #338). The last point matters for fixture construction: each incident's pre-fix
  behaviour must be reconstructed from the incident issue and its fix PR, not from current code.
  The issue's architectural analysis (ownership, taxonomy, no new epic) is unaffected; its
  "nothing exists here yet" framing and its inventory need the corrections below.
- `grep` for `completion_contract`, `trajectory_contract`, `delivery_pending` across the tree
  returns zero hits in code; the only mentions are the archived #300 spec/plan
  (`docs/archive/2026-07-17-cost-report-durable-sink-fix-{design,plan}.md`) listing them as
  deferred to #197/#198. The issue's narrowest claim is confirmed, not stale.
- `scripts/factory_core/adapter.py` (#195, epic #194 A1) validates `loops:` with exactly the
  11-field shape the issue cites (`name, purpose, trigger, inputs, outputs, artifacts, verifier,
  stop_condition, failure_behavior, side_effect_level, handoff`), and already carries the reserved-
  key extension mechanism this spec recommends reusing (see below).
- **Not cited in the issue, materially relevant:** `evals/state-governance/` (#190, "Add an
  always-on state governance scorecard," itself independently inspired by a *different* paper —
  arXiv 2606.30306, "Always-On Agents" — the same paper-spike-to-shipped-tool pattern #311 is now
  repeating for a second source). It defines a structured event envelope —
  `{event_id, idempotency_key, operation, state_type, entity_id, authority{actor, permission_epoch,
  approval_record}, scope{repo, issue, pr, agent_role}, provenance{source, trust_tier, run_id,
  commit}, mutability{status, supersedes, conflicts_with}, recoverability{transaction_id,
  rollback_handle, external_effects}, actionability}` — plus five deterministic checks
  (`authority_monotonicity`, `scope_non_expansion`, `deletion_propagation`,
  `provenance_preservation`, `rollback_traceability`) implemented in `scripts/state_governance_audit.py`,
  which is explicitly **advisory only, wired to no caller, always exits 0** (docstring, verified).
  Its own design spec (`docs/archive/2026-08-21-state-governance-scorecard-design.md`) already
  recorded a promotion methodology — advisory → **conformance-input** (scheduler-gate explicitly
  *rejected* on architectural grounds: `state-lineage.jsonl` events are produced *during* a run, so
  a pre-dispatch scheduler gate could only ever read stale prior-run state) — with a quantified bar
  (`false_positive_rate == 0%` over ≥10 bench issues, `check_failure_rate ≤ 10%`, per-check
  `advisory|conformance_input` mode knob, 5-run monitoring window, tiered rollback) mirroring the
  already-applied `token_optimization` Observe→Enforce procedure. It also reserves
  `harness_economics` (#234) and `memory_intervention` (#241) as enum identifiers with no fixtures —
  the same reserved-carve-out discipline recommended below for #301.
- **Also not cited, also material:** `evals/behavioral-state/` (#242, "Behavioral State Decay —
  Baseline Fixture Set and Incidence Report," committed 2026-07-16 — *three days before* the
  issue's own inspection date). Ten fixtures across seven categories
  (`requirement-forgotten, environment-fact-ignored, failed-command-repeated, diagnosis-lost,
  subgoal-abandoned, policy-violated-before-side-effect, phase-handoff-loses-state`), each sourced
  from a real, independently verifiable dark-factory or MarketHawk incident (issue/commit/verifier
  signal) — two of the ten (`policy-violated-before-side-effect-02`, `phase-handoff-loses-state-01`)
  are sourced from **#212**, the same "gate label applied without checking artifact" incident class
  #311's own memory review flagged as directly on point for the completion-contract problem. Its
  rubric (`evals/behavioral-state/rubric.md`) already implements the *exact* anti-future-leakage
  mechanism spike deliverable #5 asks for: every fixture carries a `pivot_event_index`, separates
  `prefix` (what a replayed agent may see) from `suffix` (hindsight verdict, label-only, never
  injected into replay), and requires independent re-verifiability of both the establishing and
  pivot events plus a later verifier signal before a fixture is admissible.

#### Existing gate, verdict, run-record, delivery, and stop surfaces (deliverable #1 — the part the issue's inventory omitted)

All paths verified on `main` `f283346`.

| Surface | Where | What it does today | Relevance to #197/#198 |
|---|---|---|---|
| Verdict contract | `scripts/gate_lib.sh::emit_verdict` | Writes `STATUS / GATE_TYPE / FINDINGS_COUNT / SEVERITY` to `$ARTIFACTS_DIR/conformance.md` / `review.md` | The shape every new verifier should emit; #197's scope already names it |
| Verdict gate | `scripts/verdict_gate_check.sh`; DAG nodes `conformance-gate`, `review-gate` | Reads the durable verdict file, not the node's exit code; missing/unparseable file = BLOCK (#212, #271) | Fail-closed on a missing verdict already exists — #197 inherits it rather than inventing it |
| Deterministic gates | `scripts/budget_gate.sh` (`budget-*` / `enforce-budget-*` nodes), `scripts/gate_blast_radius.py` (`validate`), `scripts/push_gate_check.sh`, `scripts/oos_excise.sh` | Budget ceilings, blast radius, push preconditions, out-of-scope excision | Path scope (allowed/forbidden) is already enforced by OOS excision at conformance; a contract's `scope` block must reference it, not duplicate it |
| Run record | `scripts/factory_core/run_record.py::_parse_artifact_stage` → `runs.jsonl` + Seq; `emit_health_event` (e.g. `factory.cost_report.missing`) | Per-stage verdict persistence; health events for non-stage failures | The durable sink for verifier verdicts; the cost-report health event is the existing #300-class signal |
| Delivery | `entrypoint.sh::post_cost_report` (marker `<!-- dark-factory-cost-report -->`); phase comment markers | Issue-comment delivery of reports | The only "delivery ack" that exists today is a marker's presence among the issue's comments |
| Board / labels | `scripts/factory_core/board.py`; label semantics in `CLAUDE.md` | Status transitions, gate labels | A Done transition is a board move with no completion predicate in front of it |
| Breaker | `scripts/factory_core/breaker.py` (`max_retries`, `record_failure_signature`, `trip_to_blocked`) | Per issue:phase retry ceiling; trips to Blocked + `needs-discussion` | #198's declared enforcement point |
| Autopilot | `scripts/factory_core/epic_autopilot.py` (`daily_cap`, `confidence_floor`, `should_advance`) | Grace-timer advance decisions on gate labels | A third, independent completion/advance authority |
| Loop schema | `scripts/factory_core/adapter.py` `loops:` (`outputs`, `artifacts`, `verifier`, `stop_condition`, `failure_behavior`) | Declared and validated, enforced by nothing (parity-only, #195) | The fields a contract binds to |

#### Owner conflicts this spike must name (operator-flagged)

**Failure classification has four owners today:**

1. `scripts/factory_core/error_signature.py::classify` — class-prefixed enum (`substantive:` /
   `environmental:`), written by `entrypoint.sh::_write_error_signature` to a per-issue drop file;
2. `scripts/factory_core/breaker.py::record_failure_signature` — consumes the drop file; "stuck"
   when two consecutive `substantive:` signatures match exactly;
3. `scripts/factory_core/post_mortem.py` — LLM free-text postmortem appended to
   `evals/factory-failures.jsonl`;
4. `scripts/factory_core/main_red_fixer.py::classify_scope` / `ci_status` and
   `scripts/factory_core/rescue.py::assess` — CI-red and blocked-PR triage.

**Completion / stop has three owners today:**

1. `breaker.py` `max_retries` → `trip_to_blocked` — scheduler-side; reads prior-run state
   (attempt counts);
2. gate BLOCK via `verdict_gate_check.sh` — mid-run, artifact-based; halts the DAG before
   push/merge;
3. `epic_autopilot.py` `daily_cap` / `confidence_floor` / `should_advance` — advance decisions on
   gate labels.

(`scripts/factory_core/session_window.py`'s pause is an environmental fourth that stops dispatch;
it is not a completion authority and is left alone.)

**Ruling (recommended to #197/#198; binding only once their own specs adopt it):**

- A trajectory/contract verdict is a Gate-2-class BLOCK (stop owner 2): emitted in `emit_verdict`
  shape, consumed by the `verdict_gate_check.sh` pattern. It never increments the breaker retry
  counter directly and is never a scheduler predicate — #190's rationale applies verbatim (events
  are produced during the run; a pre-dispatch check could only read stale prior-run state).
- #198's `max_iterations` / `max_tokens` / `deadline` stay breaker-side (stop owner 1) precisely
  because they read prior-run state. This is the reconciliation between #190's "never
  scheduler-gate" and #198's "enforce in `breaker.py` + the dispatch path": the two rules govern
  different signal classes (mid-run trajectory evidence vs. cross-run counters) and do not conflict.
- #198's external predicate is a check-only command emitting `emit_verdict` shape, evaluated
  mid-run by the verdict-gate pattern; its BLOCK is then recorded by the breaker and routed per the
  loop's declared `failure_behavior`.
- A contract-verifier BLOCK is also written through `error_signature` as one new class,
  `substantive:contract_violation`, so failure owner 2 (breaker stuck-detection) sees repeated
  contract failures without a fifth classifier being added. Failure owners 3 and 4 are unchanged;
  the postmortem stays free-text evidence and is never a verdict input.
- Autopilot (stop owner 3) must treat a missing or BLOCKED contract verdict exactly as it treats a
  BLOCKED conformance verdict: no advance. That is one input condition on `should_advance`, owned
  by #198.

**Net correction to the issue's framing:** the "systemic gap" is real and narrower than the issue's
own evidence suggested. The missing piece is specifically (a) a *completion/deliverable* contract
schema — #190/#242 cover state-lineage and behavioral-decay trajectories, neither covers "did the
required artifact reach a durable sink and get acknowledged" — and (b) promotion of any of this from
advisory to something that can legitimately block a Done transition, which #190's own spec already
concluded cannot be a scheduler gate and must be conformance-input instead. Both conclusions
strengthen, not weaken, the issue's original recommendation to route this through #301 (schema) +
#197/#198 (verifier/stop) rather than a new #194 child.

### Recommendation to #301: reserved-key carve-out (non-binding, sequencing only)

Per Q&A above, recommend — not mandate — that #301's own refinement consider shipping, as a
minimal-diff first step ahead of full schema design, a reserved-key carve-out mirroring
`_RESERVED_LOOP_FIELDS`/`_RESERVED_TOP_FIELDS`: reserve candidate names (e.g. `contract`,
`verification` as new per-loop-entry keys) so any adapter (including MarketHawk's) that starts using
those names before #301 lands gets a targeted `AdapterError` naming the tracking ticket instead of
silent unknown-field passthrough or an uninformative generic rejection. **Explicit exclusion:** this
recommendation covers inert validation only. It must not be read as license for #301 to ship any
Done-blocking or completion-predicate logic — that is #197/#198's exclusive territory. #301 owns the
final field names, nesting location, and whether to act on this recommendation at all.

### Recommendation to #240: extend, evaluate, or replace existing substrate (non-binding)

Per Q&A above, recommend — not mandate — that #240's own refinement evaluate the existing
`evals/state-governance/` event envelope and `evals/behavioral-state/` fixture-and-rubric
methodology as candidate starting points before building parallel new tooling, specifically because:
the anti-future-leakage mechanism deliverable #5 requires (prefix/suffix separation,
independent-reverifiability gate) is already implemented and battle-tested in
`evals/behavioral-state/rubric.md`; the structured-event schema deliverable #4 requires has a working
precedent in `state-governance`'s envelope, though it was built for entity-mutation lineage
(authority/scope/provenance/recoverability), not for tool-call/action/handoff trajectories, and
would need extension, not just reuse, to cover #311's "required event occurred / occurred
before-after / handoff contained required fields" checks. `evals/factory-failures.jsonl` (raw
exit_code + free-text postmortem) and `evals/token_opt_eval.py`/`evals/skill_flow_eval.py`
(scenario-scoring evaluators, not trajectory-arm comparators) are named as adjacent but
lower-fidelity candidates — whether their scorecard shape can be adapted to the required A/B/C/D
per-arm false-block-rate comparison is left as an open question for #240, not asserted here.
`entrypoint.sh`'s existing `post_cost_report()` (marker `dark-factory-cost-report`, live since
2026-05-27) is flagged as the already-durable source for the required tokens/cost/wall-clock metrics
— #240 does not need to build new cost capture, only mine what already posts to each run's issue.

### Handoff to #197 and #198 (spike deliverable #3)

The issue's translation — required events, partial ordering, forbidden-event rules, handoff field
checks, all evaluated against *observable* actions/tool-calls/handoffs rather than raw
chain-of-thought — stands as written. This section adds what #197 and #198 need before they can be
planned: a definition of "contract" in this factory's terms, a DAG placement, a desk walk-through
of the #300 incident, explicit verdicts, and the promotion path.

#### What a "contract" is in this factory

A resolved execution/completion contract is the join of things that already exist plus one
net-new element:

- the approved spec under `docs/superpowers/specs/` (located by `commands/dark-factory-conformance.md`
  Phase 2 — the same lookup the conformance verifier would reuse);
- the plan's file list — the scope, already enforced by OOS excision at conformance;
- the loop entry's existing `outputs` / `artifacts` / `verifier` / `stop_condition` /
  `failure_behavior` fields (`scripts/factory_core/adapter.py`; declared and validated today,
  enforced by nothing);
- **net-new:** `required_deliverables[]`, each `{id, durable_sink, evidence_predicate,
  required_delivery_ack}` — the issue's candidate `contract:` / `verification:` YAML is the shape
  #301 refines, and the reserved-key carve-out above is its first slice.

Only the last item does not exist; the rest is present and not joined up. "Contract satisfied"
therefore means: every required deliverable's `evidence_predicate` holds against its `durable_sink`,
and every deliverable with `required_delivery_ack: true` has an observable acknowledgement.

#### Where trajectory conformance is checked in the DAG

A new verdict artifact (working name `$ARTIFACTS_DIR/contract.md`, `GATE_TYPE: contract`) in
`emit_verdict` shape, produced by a check-only verifier node placed between `validate` and
`push-and-pr` alongside `conformance` — a sibling of Gate 2, not a scheduler predicate — and
consumed by the existing `verdict_gate_check.sh` pattern with the same fail-closed semantics as
`conformance-gate`: a missing or unparseable verdict is a BLOCK. The verifier consumes only
observable inputs: the artifacts directory, `runs.jsonl` and health events, git state on the
branch, and issue comments for delivery acks. It never reads agent self-report text ("done",
"pushed") as evidence. Ordering and forbidden-event checks consume the redacted observable event
schema below; until #240 lands a structured trajectory substrate, the required-deliverable and
delivery-ack checks alone are implementable from existing sinks and are the recommended first slice.
Two evaluation points, one artifact shape: branch-phase deliverables (spec, plan, code, tests) are
checked at the Gate-2 sibling node; run-end deliverables whose sink is written after push (the cost
report, the `report` node's comment) are checked by `entrypoint.sh`'s post-run path immediately
before the Done/board transition, which is where `run_record.py` already records the outcome.

#### Desk walk-through: #300 against the proposed contract

- Deliverable `cost-report`: `durable_sink` = issue comment; `evidence_predicate` = a comment
  carrying `<!-- dark-factory-cost-report -->` exists for this run; `required_delivery_ack: true`.
- Pre-fix behaviour (`37b76d5e` era): `post_cost_report` failed silently, no comment was posted,
  and the run reached Done because completion was inferred from node exit status.
- Under the contract: the run-end evaluation point checks the predicate before the Done
  transition; no marker →
  `STATUS: BLOCKED`, `FINDINGS_COUNT: 1`, `SEVERITY: high`; `verdict_gate_check.sh` blocks the
  Done transition; the breaker records `substantive:contract_violation`; `failure_behavior` moves
  the issue to Blocked + `needs-discussion`. The #300 failure is caught before a success transition.
- Existing signal reused: `run_record.py::emit_health_event('factory.cost_report.missing')`
  (shipped by #300's repair) becomes the predicate's input rather than a parallel detector.
- Two valid alternative trajectories pass without exact-path matching: (a) the cost report posted
  once by `post_cost_report` at the end of the run; (b) a per-node partial report posted earlier
  and updated in place under the same marker. The predicate checks marker presence, not the path
  that produced it, so neither exact ordering nor a single implementation path is required for
  this deliverable class.

#### Verdicts

**#197: PROCEED as scoped**, with two inherited acceptance criteria: (i) a loop's verifier
consumes only observable events/artifacts, never agent self-report; (ii) a missing/failed required
deliverable (the #300 class) fails closed before handoff.

**#198: PROCEED, re-scoped** — the external-predicate stop condition MUST be a
contract-satisfaction check (required deliverables reached a durable sink + ack), not only
iteration/token/deadline caps. Inherited acceptance criteria: "agent says done" is never a stop
condition, and the #300 fixture halts the loop.

**Promotion path for both:** advisory → conformance-input per the #190 precedent — quantified bar
`false_positive_rate == 0%` over ≥10 bench issues, `check_failure_rate ≤ 10%`, per-check
`advisory|conformance_input` mode knob, 5-run monitoring window, tiered rollback (Tier-1 master
kill / Tier-2 per-check revert); blocking only via their own reviewed tickets. Whether a
completion-contract verifier's promotion path is architecturally identical to the state-governance
scorecard's is answered by the owner ruling above: yes for trajectory/contract checks (mid-run,
never a scheduler gate); no for #198's cap-class stops, which are legitimately breaker-side.

### Redacted observable event schema / no chain-of-thought capture

Unchanged from the issue body: record observable actions, tool calls, inputs/outputs, evidence
references, bounded rationale codes, confidence provenance, and verifier outcomes — never raw hidden
reasoning. This is consistent with `state-governance`'s existing envelope design, which already
carries no free-text reasoning field, only structured operation/scope/provenance/actionability data.

### Spike deliverables checklist (issue's 10-item list → where satisfied in this spec)

1. Contract/evidence/trajectory schema inventory — "Current-state evidence" above.
2. Exact schema delta proposed for #301, parity-when-absent — "Recommendation to #301" above.
3. Verifier delta for #197 / successful-stop delta for #198 — "Handoff to #197 and #198" above:
   contract definition, DAG placement, #300 desk walk-through, explicit PROCEED verdicts with
   inherited acceptance criteria, and the promotion path.
4. Partial-order trajectory representation and redacted event schema — "Redacted observable event
   schema" above, plus the `state-governance` envelope as a starting candidate shape.
5. Historical fixture list and anti-future-leakage rules — "Recommendation to #240" above; five of
   the six named incidents (#300, #271, #279, #293, #305) are absent from both existing corpora and
   remain #240's to build; #280 already exists as
   `evals/behavioral-state/fixtures/environment-fact-ignored-02.json` and should be reused, not
   duplicated; but the *methodology* for admitting them safely
   already exists in `evals/behavioral-state/rubric.md`.
6. Baseline/ablation results with quality and economics together — explicitly **not attempted** in
   this spec; this ticket has no live-execution or benchmark budget (refine-phase scope boundary),
   and unlike issue #189's precedent, this spike's evidence base is *not* solely already-published
   material — the ablation requires running real replay arms, which is #240/#197/#198 work, not a
   desk-research synthesis this spec can render. Recorded here as a named gap, not silently dropped.
7. Failure-mode analysis (ambiguous contracts, stale sources, missing inputs, verifier/report
   outage) — carried over unchanged from the issue body; no new evidence gathered this pass.
8. Promotion stages — "Handoff to #197 and #198" above (Verdicts), reusing #190's
   advisory→conformance-input precedent.
9. Rollback/disable path and migration behavior — #190's tiered-rollback precedent (Tier-1 master
   kill / Tier-2 per-check revert) is offered as reusable shape; specific migration behavior for a
   completion contract is #301/#197/#198's to define.
10. Final decision on a new implementation child — **no** (unchanged from the issue's own
    conclusion). This spike does not surface anything #301/#197/#198/#240 collectively cannot
    absorb; if anything, the discovery that #190/#242 already cover meaningful ground *strengthens*
    the case against a new epic.

### Issue acceptance criteria → disposition

| # | Issue acceptance criterion | Disposition |
|---|---|---|
| 1 | All 21 patterns classified | Satisfied — issue-body table, re-affirmed (Requirement 1) |
| 2 | Paper claims kept distinct from observed evidence and proposed mechanisms | Satisfied — issue's evidence-quality table; "Current-state evidence" here is code-verified; the handoff section is labeled as proposals |
| 3 | #300 caught by the replay contract before a success transition | Satisfied on paper (desk walk-through); live replay confirmation deferred to #240 |
| 4 | Two valid alternative trajectories pass without exact-path matching | Satisfied on paper for the deliverable class (walk-through); general trajectory arms deferred to #240 |
| 5 | No raw chain-of-thought / secrets / unrestricted payloads persisted | Satisfied — redacted observable event schema; verifier reads observable inputs only |
| 6 | Self-reflection, LLM confidence, LLM-as-a-Judge cannot authorize completion or side effects | Satisfied — owner ruling and #197 inherited AC (i) |
| 7 | Missing mandatory evidence fails closed in replay; optional items need explicit policy + absence reason | Fail-closed: satisfied by design (verdict-gate pattern). Replay evidence: deferred to #240. Optional-item policy (`required_delivery_ack: false` + an `absence_reason`): recommended to #301, not designed here |
| 8 | Economics and quality evaluated together | Deferred to #240 (deliverable 6); the cost source is identified (`post_cost_report`) |
| 9 | Existing Superpowers, Archon workflow, provider abstraction, memory-v2, scheduler, gates, human promotion remain authoritative | Satisfied — nothing here displaces them (see Recommendation) |
| 10 | No production behaviour change before a reviewed implementation spec and human promotion gate | Satisfied — spec-only; see Recommendation's non-authorization statement |

---

## Alternatives Considered

1. **Post the report only as an issue comment, per the issue body's own original "do not create a
   repository research Markdown file" instruction.** Rejected per the operator's explicit override
   ("Refine as a spec-only deliverable... spec gate will be reviewed by the operator") and per the
   `#189` AVOID precedent for rendering a spike's full decision in the refine-phase spec itself.
2. **Defer the whole report to a later "implement" phase**, mirroring the Mem0 spike's (#50)
   methodology-now/verdict-later split. Rejected: that split's rationale (refine cannot install
   packages or run live benchmarks; any such change is reverted by the OOS excision gate) doesn't
   generalize here beyond item 6 above (the ablation), which is called out as a named gap rather than
   used to justify deferring the entire report.
3. **Silently fold the #190/#242 discovery into a rewritten "no gap exists" verdict**, since so much
   adjacent infrastructure already exists. Rejected: both are advisory-only, fixture-only (no
   live-capture wiring per #190's own named follow-up), and neither covers the specific
   deliverable-completion/acknowledgement contract #300 exposed. The gap is narrower, not closed.
4. **Draft the reserved-key carve-out or replay-fixture content directly as code/tests in this
   ticket**, since both are small. Rejected: this refine command's scope boundary permits only
   `docs/superpowers/specs/` and `.archon/memory/` outputs; #301 and #240 own their own
   plan/implement cycles regardless of how small the diff would be.

---

## Open Questions (Non-blocking)

- Whether `evals/state-governance`'s event envelope should be extended in place (new
  `state_type`/`operation` values for tool-call/handoff/deliverable events) or whether trajectory
  conformance needs a structurally different envelope — left to #197/#240's own context assembly.
- Whether `evals/token_opt_eval.py`/`evals/skill_flow_eval.py`'s scorecard shape can be adapted for
  A/B/C/D per-arm comparison, or whether a new comparator is warranted — flagged, not resolved.

---

## Assumptions

- The operator's "spec-only deliverable" instruction means this spec itself is the ticket's final
  artifact through the spec-pending-review gate; no plan/implement phase is expected to follow this
  ticket specifically (consistent with the issue's own "do not create a new epic" / "post the
  durable decision report" framing, now satisfied by this document instead of a bare comment).
- "Report" content that duplicates the issue body verbatim (the 21-pattern table, non-goals,
  acceptance criteria, evidence-quality table) is intentionally *not* re-transcribed into this spec —
  it remains authoritative on the issue itself and is referenced, not copied, to avoid a
  synchronization hazard between two documents saying the same thing.
- Disposition on approval: merge the refine branch, archive this spec under `docs/archive/` (as the
  #189/#207 spike specs were on 2026-08-22), close #311, and dispatch #197 then #198 — no plan or
  implement phase for #311 itself.
- `evals/state-governance` and `evals/behavioral-state` file contents were read directly (envelope
  fields, check names, manifest, rubric methodology, `git log --diff-filter=A` issue attribution) —
  not inferred from directory names or commit messages alone, per the #50 AVOID entry's verification
  bar.

---

## Recommendation

**Proceed with #197, then #198, on the handoff above; no new #194 child.** #301 takes the
reserved-key carve-out as its first slice; #240 evaluates the existing `evals/` substrate before
building anew. This spec does not itself authorize any change to `gate_*`, `workflows/`,
`config/config.yaml`, `scripts/factory_core/breaker.py`, budgets, or tool allow/deny lists; every
mechanism above is follow-up-ticket content for #197/#198/#301/#240 under their own reviewed specs.
