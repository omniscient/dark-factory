# Contract-driven execution and trajectory conformance — spike decision report

**Issue:** omniscient/dark-factory#311
**Status:** spec-only deliverable. Per operator instruction (issue comment, 2026-08-28): "Refine
as a spec-only deliverable where the ticket says spike; spec gate will be reviewed by the
operator." This document **is** the spike's durable decision report — no separate comment-only
report and no implementation follow through this ticket. Precedent for rendering a paper-spike's
full evaluation directly in the refine-phase spec (rather than a methodology-now/verdict-later
split) is `.archon/memory/architecture.md` (issue #189, 2026-08-21): that AVOID entry applies
whenever "the entire evidence base is already-published material" and no live benchmark is
required — true here (the evidence base is the cited book plus this repo's own inspectable
history).
**Parent context:** epic #194 (loop-contract architecture). Sibling/downstream tickets referenced
throughout, none of which this document modifies: #301 (adapter `loops:` schema extension, A1.5),
#197 (independent contract/trajectory verifier), #198 (successful-stop / completion predicate),
#240 (replay/evalset substrate and economics), #190 (state governance — already shipped, see
below), #242 (behavioral-state decay baseline — already shipped, see below).

---

## Overview / Problem Statement

Issue #311 is a research spike evaluating *Agentic Design Patterns* (Antonio Gulli) against Dark
Factory's architecture. The issue body already contains a thorough 21-pattern taxonomy mapping,
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
   is not: **#190 and #242 already shipped substantial adjacent infrastructure** — a structured
   governed-event schema, five deterministic trajectory checks with an advisory scorer, and a
   labeled historical-incident fixture corpus with an anti-future-leakage methodology — before or
   within days of this issue's own 2026-07-19 inspection. None of it is cited in the issue. See
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
   AVOID entry for issue #182: "always re-verify line-number/state citations in an issue
   body/prior spec against current main before planning work.")
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

- `main` has advanced past the issue's inspection point with five merged PRs, all Wave-0
  factory-stability fixes (scheduler session-window pause, dispatch-ceiling re-measurement, stage
  orphan sweep, a GraphQL-budget test leak) — none touch loop-contract or verifier surfaces, so the
  issue's architectural analysis is unaffected by drift, only its "nothing exists here yet" framing
  needs correction (below).
- `grep` for `completion_contract`, `trajectory_contract`, `delivery_pending` across the tree still
  returns zero hits outside this spec. The issue's narrowest claim is confirmed, not stale.
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

### Trajectory verification and successful-stop (context for #197/#198, no new recommendation)

The issue's own translation — required events, partial ordering, forbidden-event rules, handoff
field checks, all evaluated against *observable* actions/tool-calls/handoffs rather than raw
chain-of-thought — stands as written in the issue body and is not re-litigated here. The one
addition this spec makes: #190's already-recorded promotion methodology (advisory → conformance-input,
never scheduler-gate, quantified false-positive/check-failure bars, per-check mode knob, tiered
rollback) is offered to #197/#198 as directly reusable precedent for spike deliverable #8's
"promotion stages" requirement, rather than a promotion framework #197/#198 would otherwise have to
invent from scratch. Whether a completion-contract verifier's promotion path is architecturally
identical to a state-governance scorecard's (i.e., also produced mid-run, also unsuitable for a
pre-dispatch scheduler gate) is for #197/#198 to confirm, not assumed here.

### Redacted observable event schema / no chain-of-thought capture

Unchanged from the issue body: record observable actions, tool calls, inputs/outputs, evidence
references, bounded rationale codes, confidence provenance, and verifier outcomes — never raw hidden
reasoning. This is consistent with `state-governance`'s existing envelope design, which already
carries no free-text reasoning field, only structured operation/scope/provenance/actionability data.

### Spike deliverables checklist (issue's 10-item list → where satisfied in this spec)

1. Contract/evidence/trajectory schema inventory — "Current-state evidence" above.
2. Exact schema delta proposed for #301, parity-when-absent — "Recommendation to #301" above.
3. Verifier delta for #197 / successful-stop delta for #198 — "Trajectory verification and
   successful-stop" above (issue body's translation stands; this spec adds the promotion-precedent
   pointer only).
4. Partial-order trajectory representation and redacted event schema — "Redacted observable event
   schema" above, plus the `state-governance` envelope as a starting candidate shape.
5. Historical fixture list and anti-future-leakage rules — "Recommendation to #240" above; the six
   named incidents (#300, #271, #279, #280, #293, #305) are not yet present as fixtures in either
   existing corpus and remain #240's to build, but the *methodology* for admitting them safely
   already exists in `evals/behavioral-state/rubric.md`.
6. Baseline/ablation results with quality and economics together — explicitly **not attempted** in
   this spec; this ticket has no live-execution or benchmark budget (refine-phase scope boundary),
   and unlike issue #189's precedent, this spike's evidence base is *not* solely already-published
   material — the ablation requires running real replay arms, which is #240/#197/#198 work, not a
   desk-research synthesis this spec can render. Recorded here as a named gap, not silently dropped.
7. Failure-mode analysis (ambiguous contracts, stale sources, missing inputs, verifier/report
   outage) — carried over unchanged from the issue body; no new evidence gathered this pass.
8. Promotion stages — "Trajectory verification and successful-stop" above, reusing #190's
   advisory→conformance-input precedent.
9. Rollback/disable path and migration behavior — #190's tiered-rollback precedent (Tier-1 master
   kill / Tier-2 per-check revert) is offered as reusable shape; specific migration behavior for a
   completion contract is #301/#197/#198's to define.
10. Final decision on a new implementation child — **no** (unchanged from the issue's own
    conclusion). This spike does not surface anything #301/#197/#198/#240 collectively cannot
    absorb; if anything, the discovery that #190/#242 already cover meaningful ground *strengthens*
    the case against a new epic.

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
- Whether a completion-contract verifier's promotion path is architecturally forced into
  conformance-input the same way #190's scorecard was (mid-run event production vs. pre-dispatch
  scheduler timing) — plausible by analogy, not confirmed for #197/#198's specific design.

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
- `evals/state-governance` and `evals/behavioral-state` file contents were read directly (envelope
  fields, check names, manifest, rubric methodology, `git log --diff-filter=A` issue attribution) —
  not inferred from directory names or commit messages alone, per the #50 AVOID entry's verification
  bar.
