# spike(control): calibrate a metacognitive monitor-to-control protocol

**Issue:** omniscient/dark-factory#288
**Status:** spec-only deliverable per operator instruction (2026-08-28 comment): "Refine as a
spec-only deliverable where the ticket says spike; spec gate will be reviewed by the operator."
This document *is* the durable decision report the issue asks for; the Phase 6 issue comment
carries a summary of it plus this link, per this repo's existing refine-phase publish convention.
**Parent:** #241 (proactive execution-state memory). Cross-cutting with #234 (harness economics).
Must not turn the #241 memory worker into a universal planner (non-goal, preserved below).
**Primary consumer:** #239 (`feat(routing): feature-demand model routing and delegation capability
floors`) already carries `Depends on: #288` and `needs-discussion` on `main` — it is parked
correctly today and needs no further action from this spike beyond the spec delta in §4.9.

---

## Overview / Problem Statement

Liu et al., *Metacognition in LLMs: Foundations, Progress, and Opportunities* (arXiv
`2607.11881v1`, 2026-07-13) is a 47-page survey, not a new system. Its useful functional loop is:

```text
observable execution events -> monitoring -> bounded control decision -> action
  -> independent verification -> outcome/calibration update
```

**Paper-supported claims** (surveyed evidence — treat as literature, not Dark Factory results):

- Monitoring and control are separable capabilities; final-task accuracy does not measure
  self-evaluation quality.
- Self-reported confidence is elicitation-method-sensitive and frequently overconfident.
- Metacognitive sensitivity and reasoning ability can improve at different rates and vary by
  model/phase/domain/post-training/temperature.
- LLMs remain weak at allocating effort, deciding when to invoke tools, and judging whether more
  reasoning will help.
- A dual-speed pattern (cheap frequent monitoring + occasional slower model-assisted monitoring)
  is promising in the surveyed literature.
- Increased self-awareness can increase oversight-evasion/deception risk in principle, so monitor
  output must stay untrusted and bounded rather than self-authorizing.

**Dark Factory inference — not a result the paper demonstrates, this spike's own design work:**
Dark Factory can realize the useful *behavior* at the harness level — deterministic fast checks,
a read-only bounded slow-monitor advisory, and a policy envelope that maps advisory output to
already-existing gates — without assuming genuine model introspection and without trusting a
model's self-report as authorization. Everything in §4 below is this category unless labeled
"paper claim."

**Proposed experiments** (not run by this spike — desk research only, no code execution
available in the refine container): the replay/shadow/advisory/promotion program in §4.6–§4.11.

This spike is scoped as **spec-only research**: no code, no tests, no config changes. Its output
is this design document plus the exact deltas/amendments in §4.9–§4.10 for six sibling tickets.

---

## Current implementation and issue-ownership map

Verified directly against this checkout (`git rev-parse HEAD` at spike time), not assumed from
the issue body:

| Substrate | File | What it owns today | Gap relative to this spike |
|---|---|---|---|
| Per-run durable record | `scripts/factory_core/run_record.py` (`cmd_assemble`, `POLICY_VERSION = "1.0"`) | `runs.jsonl` + `run-records/<run_id>.json` on the `dark_factory_state` volume; stage nodes, verdicts, artifacts, tokens, cost, `harness_economics`, `memory_trace` — assembled **once, at run end** | No mid-run event stream; no monitor/control/outcome fields |
| Per-request cost/token ledger | `scripts/factory_core/model_proxy.py` (`append_ledger`, `post_seq_ledger`) | `request-ledger.jsonl`, size-rotated (`MAX_LEDGER_BYTES`/`BACKUP_COUNT`), `fcntl.flock`-appended, joined by `run_id`/`issue_number`/`intent`/`stage` — explicitly "not a second source of truth," rolled up into `run-record.json` at assemble time | Cost/token signal only; no evidence-coverage or strategy signal |
| Per-issue+phase repeated-failure detection | `scripts/factory_core/error_signature.py` (`classify`) + `breaker.py` (`record_failure_signature`, `trip_to_blocked`) | Classifies a failed run's output into a stable `substantive:*` / `environmental:*` signature; compares against the prior stored signature for the same `<issue>:<phase>` key in `scheduler-state.json`; on two consecutive substantive matches, trips to **Blocked** + `needs-discussion` label + comment | Coarse (4 substantive buckets + exit code), one signature per whole phase attempt (not intra-run/intra-step), and the existing control is **halt-and-escalate-to-human**, not `replan` |
| Dispatch-time breaker wiring | `scheduler.sh` (`check_failure_signature`, call sites at resolve/implement/plan/refine dispatch) | Host-side, deterministic, single-writer, survives container death | No hook for a slow (model-assisted) monitor; no event provenance beyond the sig string |
| Declarative loop metadata | `scripts/factory_core/adapter.py` (`_validate_loop`, `.factory/adapter.yaml` `loops:` block, epic #194 A1) | Per-named-loop `verifier`, `stop_condition`, `failure_behavior`, `side_effect_level` (int 1–6), `handoff` — schema exists; `memory_intervention` and `mechanism_candidates` keys are **reserved-and-rejected** pending #241 and a future Bilevel Autoresearch ticket | Side-effect *levels* are declared per loop but not yet *enforced* with permission profiles (that's #196, unbuilt) |
| Memory v2 | `.archon/memory/` + `scripts/memory_retrieve.py` | Bounded top-k memory context loaded at phase **start** only | No live behavioral-state decay observation during a run |
| Maker/checker gates | conformance + code-review DAG nodes | Independent post-hoc verification of two specific artifact classes | Not a general verifier abstraction yet (#197 targets this) |

**Issue ownership for the missing connective state** (current strategy, open subgoals,
acceptance-criterion evidence coverage, progress delta, normalized repeated-failure fingerprint,
knowledge staleness, confidence source/calibration, monitor anomaly, bounded control decision +
rationale, intervention-outcome delta):

| Missing signal | Owning ticket after this spike | Recoverable from existing historical records? |
|---|---|---|
| Repeated-failure fingerprint (coarse, per-phase-attempt) | Already exists (`error_signature.py`) | Yes — `scheduler-state.json` `<key>:sig`, and any `runs.jsonl` rows correlated by `run_id` |
| Repeated-failure fingerprint (fine, intra-run/tool-call) | #238 | No — no intra-run signal is persisted today |
| Failure-spend taxonomy / retry governance | #238 | Partial — `harness_economics` has cost, not a taxonomy |
| Confidence-source taxonomy, calibration cohorts | #239 rerun (§4.9) | No — no confidence signal is captured anywhere today |
| Memory intervention trigger/silence policy | #245 | No |
| Memory intervention provenance/confidence/security | #247 | No |
| Harness-swap / orchestration ablation replay | #240 | Partial — `harness_economics` gives cost/token deltas, not quality |
| Monitor/control/outcome event schema itself | This spike, §4.2 | N/A (new) |
| Side-effect-level *enforcement* (permission profiles) | #196 | N/A (schema exists, enforcement doesn't) |
| Verifier abstraction generalized across loops | #197 | N/A |
| Declarative stop conditions enforced by breaker | #198 | Partial — breaker already stops on repeated signature |
| State governance / provenance for injected advisory state | #190, #247 | Partial — #190 scorecard exists for other state classes |

---

## Requirements

Distilled from the issue's acceptance criteria (unchanged, reproduced for traceability) plus the
brainstorming Q&A below:

1. Paper claims, surveyed evidence, Dark Factory inference, and proposed experiments must stay
   clearly labeled and separated (§Overview, and every subsection below).
2. No raw model self-confidence may be treated as authorization for a consequential action.
3. Actor, monitor, verifier, and human authorities stay distinct — the slow monitor is read-only
   and cannot approve its own output, relax a gate, or declare `done`.
4. Fast deterministic baselines must include at minimum: same failure twice → an existing control
   response (§4.3 names the actual one); test/evidence absent → not done; a material acceptance
   criterion lacking evidence → verify; a current-fact claim without an authoritative source →
   retrieve/abstain.
5. Any replay design must avoid future-result leakage at the intervention pivot point (§4.6).
6. Evaluation must cover outcome quality, selective risk/coverage, high-confidence failure rate,
   false/unnecessary intervention rate, human rework, tokens/cost/latency, failure spend, and
   unsafe/bypassed actions (§4.7–§4.8).
7. Intervention utility (cost/risk-justified benefit) must be a named, measured axis, not implied.
8. Promotion path is offline replay → shadow → advisory → bounded default, with a kill switch and
   rollback metadata at every stage (§4.11).
9. No raw chain-of-thought may be persisted in the event schema — only observable signals,
   evidence references, structured diagnoses, decisions, and outcomes (§4.2).
10. No recursive runtime self-modification, self-approval, gate relaxation, or human-promotion
    bypass (this spike touches no gate files; every gate-adjacent design element below is framed
    as follow-up-ticket content per §4.5's own scoping note).
11. #239 stays parked until this spike's decision artifact is accepted and #239 is explicitly
    re-refined with the mandatory inputs the issue names (§4.9) — already true on `main` today.

### Brainstorming Q&A

> **Q1 (event schema storage location):** Should the new monitor/control event schema be new
> fields on `run_record.py`'s existing records, or a wholly separate store?
> **A:** Extend the existing run-record substrate; do not stand up a new store (no SQLite/
> Postgres/service/volume/secret). Two layers, both inside `run_record.py`'s writer: (1) an
> additive top-level key in `run-record.json`, computed in `cmd_assemble` alongside
> `harness_economics`/`memory_trace`, as the end-of-run rollup; (2) a sibling append-only JSONL
> on the same `dark_factory_state` volume — mirroring `request-ledger.jsonl`'s pattern exactly
> (same `flock` append idiom, same join keys, size rotation) — for the mid-run events that
> `run-record.json` (assembled once, at run end) cannot physically carry. This is the harness
> economics ledger's own precedent (`docs/archive/2026-07-16-harness-economics-ledger-cpm-design.md`:
> "extend `run-record.json`, not a new file") and the #207 architecture-memory boundary condition
> ("only worth revisiting [a new store] if operators need live in-flight step-level progress
> run-record.json cannot provide" — which is exactly this case, satisfied by the sibling-JSONL
> layer instead of a new store). Do **not** fold monitor rows into `runs.jsonl` itself — `runs.jsonl`
> readers (`reconcile_cost_reports.py`, `tests/test_entrypoint_session_window.sh`) already treat
> every `run_id`-bearing row as a stage stub; a new row shape there would silently corrupt those
> readers (mirrors the #190 state-governance scorecard's rejection of retrofitting `runs.jsonl`
> for the same reason).

> **Q2 (fast-monitor granularity for "same failure twice"):** Should the spec reuse the existing
> per-issue+phase `error_signature.py`/`breaker.py` mechanism as the v1 fast-monitor realization,
> or design new intra-run tracking now?
> **A:** Reuse it; do not design new intra-run tracking in this spike. #238's own acceptance
> criteria already own the finer-grained (intra-run, byte-identical tool-call repeat) signal —
> designing it here would create two owners for one concern. A new intra-run failure-tracking
> mechanism is a breaker-semantics change, which CLAUDE.md requires its own reviewed ticket for,
> not a side effect of a research spike. Two gaps to record honestly rather than paper over: (a)
> the existing control on a signature match is **halt-and-escalate-to-human** (`trip_to_blocked`:
> Blocked + `needs-discussion` + `factory-regression` + comment), not `replan` — v1 realizes the
> *prohibition* half of "same failure twice → replan," not the redirect half; remapping to
> `replan` is a policy-envelope change deferred to #238/a future gate ticket. (b) the v1
> fingerprint is coarse (4 substantive buckets + exit code, collisions possible on
> `substantive:unknown:*`) and carries no "strategy changed" field — the spec defines the
> normalized fingerprint as an extensible tuple whose v1 projection is exactly today's signature
> string, handing the strategy-delta field and finer granularity to #238.

> **Q3 (fixture/evaluation scope achievable inside refine):** Should the historical-fixture
> inventory and baseline/ablation matrix be full paper designs, or does this run need to produce
> filled-in evaluation results?
> **A:** Paper design for the *instance* layer (which run IDs qualify, lock mechanism) — refine
> cannot reach `SCHEDULER_STATE_DIR` (`/var/lib/dark-factory`, the durable state volume;
> `runs.jsonl`/`run-records/` are not in the repo tree), so naming specific historical runs here
> would be fabrication, the same evidentiary reason the Mem0 spike (#50) deferred its live
> evaluation rather than the #189 desk-research spike (which compared two already-in-container
> document sets and is not analogous). But the *schema* layer — which fields historical records
> actually carry, and which "missing connective state" signals are unrecoverable from any
> historical record — is fully answerable from the checked-out code (§Current-state table above)
> and must land in this run, not be deferred. Anti-leakage rules are policy, not data, and are
> written as finished normative rules below (§4.6), not a template. The baseline/ablation matrix
> is committed with named axes and `TBD` cells (§4.7), not fabricated numbers.

---

## Architecture / Approach

### 4.1 Design principles (paper claim → Dark Factory inference)

| Paper claim | Dark Factory inference (this spike's design) |
|---|---|
| Self-reported confidence is method-sensitive and often overconfident | No schema field lets `self_report` alone gate a control/permission decision (Req. 2) |
| Monitoring and control are separable | Two components: deterministic fast-monitor (checks) and read-only slow-monitor (advisory diagnosis); neither executes the control it recommends |
| Final accuracy ≠ self-evaluation quality | Metrics (§4.8) score the monitor/control loop itself (selective risk/coverage, high-confidence failure), not just downstream task success |
| Dual-speed monitoring is promising | Fast-monitor: deterministic, every dispatch. Slow-monitor: model-assisted, read-only, triggered only by a fast-monitor anomaly (bounded cost) |
| Increased self-awareness can raise oversight-evasion risk | Slow-monitor output is untrusted advisory state (governed like #190/#247's provenance model); it cannot self-approve, relax a gate, or mark `done` |

### 4.2 Event schema

**Layer 1 — end-of-run rollup**, additive top-level key in `run-record.json` (mirrors
`harness_economics`'s extension idiom):

```json
"monitor_control": {
  "schema_version": "1.0",
  "fast_monitor_fires": [
    {"rule": "repeated_failure_fingerprint", "fingerprint": "substantive:oos_files:1",
     "phase": "refine", "attempt": 2, "control": "escalate", "at": "<ISO8601>"}
  ],
  "slow_monitor_invocations": 0,
  "control_actions_taken": ["escalate"],
  "verify_outcomes": [],
  "availability": {"fast_monitor": true, "slow_monitor": "not_implemented"}
}
```

**Layer 2 — mid-run event stream**, sibling JSONL (`monitor-events.jsonl`) on `dark_factory_state`,
written through a sibling of `run_record.py`'s `_append_jsonl()` (`flock`-appended, size-rotated
like `request-ledger.jsonl`), one row per monitor fire or control decision:

| Field | Type | Notes |
|---|---|---|
| `event_type` | enum | `fast_monitor_fire` \| `slow_monitor_diagnosis` \| `control_decision` \| `verify_outcome` — **required discriminator** so `runs.jsonl` readers are never touched and this file's own readers never confuse row shapes |
| `run_id`, `issue_number`, `intent`, `stage` | — | Existing join keys, identical semantics to `request-ledger.jsonl` |
| `authority` | enum | `fast_monitor` \| `slow_monitor` \| `verifier` \| `human` — who produced this event (Req. 3) |
| `signal_source` | enum | `deterministic_check` \| `model_advisory` \| `self_report` \| `verifier_evidence` \| `source_disagreement` — provenance of any confidence-adjacent claim inside the event (Req. 2; feeds #239's confidence-source taxonomy) |
| `evidence_refs` | list[str] | Pointers (file:line, artifact path, run-record field) — never inlined chain-of-thought (Req. 9) |
| `diagnosis` | string \| null | Structured, short; slow-monitor only |
| `recommended_control` \| `control_taken` | enum | From the fixed set in §4.5 |
| `rationale` | string | One paragraph max, cites `evidence_refs` |
| `outcome_delta` | string \| null | Filled retroactively when the *next* event for the same `run_id` resolves — did the intervention change the trajectory (§4.8 intervention utility) |

No placeholder/anticipatory fields for unbuilt subsystems (mirrors the #234 Non-goals precedent
against stubbing `memory_intervention` keys ahead of #241) — this schema is not implemented by
this spike; it is handed to whichever future ticket builds the emitting subsystem, which extends
`run_record.py` additively at that time.

### 4.3 Deterministic fast-monitor rules (v1)

All rules below already exist or are one additive `run-record record` emission away from
existing dispatch code — nothing here changes `error_signature.py`, `breaker.py`, or gate
semantics:

| Rule | Realization | Control today | Owner for upgrade |
|---|---|---|---|
| Same substantive failure twice | `error_signature.py` classify + `breaker.record_failure_signature` at the 4 existing `scheduler.sh` dispatch sites | `escalate` (halt to Blocked + `needs-discussion`) — **not** `replan` | #238 (fingerprint + strategy-delta + possible `replan` remap) |
| Test/evidence absent for a material acceptance criterion | Conformance gate's existing scope/requirement checks | `verify`-shaped already (gate blocks) | #197 (generalize as declared verifier) |
| Current-fact claim without an authoritative source | Not implemented anywhere today | n/a | New — out of scope for this spike; name as a gap, do not design speculatively |
| Scope/diff drift | OOS excision gate (`oos_excise.sh`) | `excise` (revert) | Already shipped |
| Token/cost/time budget slope | `budget_enforce.py` / `token_optimization` config | `observe` or `enforce` per scenario | Already shipped |

### 4.4 Slow-monitor input/output contract

Read-only, model-assisted, triggered **only** by a fast-monitor anomaly (never runs unconditionally
— bounds its own token cost and avoids "longer reasoning = safer" per the paper's own caution).

- **Input:** the triggering fast-monitor event (Layer 2 row), the current run's Layer 1 rollup so
  far, and the same bounded memory-context/architecture-slice inputs a phase agent already
  receives — no new tool access, no write access to any file.
- **Output (structured, not free text):** `{diagnosis, missing_evidence: [...], candidate_strategies: [...], recommended_control: <one of §4.5's enum>, rationale, evidence_refs: [...], confidence_source: "model_advisory"}`.
- **Hard constraints:** cannot modify code, cannot approve its own output, cannot relax a gate,
  cannot issue a consequential side effect, cannot change its own policy, cannot declare `done`.
  `recommended_control` is advisory; only the existing gate/breaker/human surface it maps to
  (§4.5) can actually execute it. This is unimplemented by this spike — its I/O contract is the
  deliverable; the DAG node, model pin, and token budget are follow-up-ticket content (per the
  #189 precedent on gate-adjacent mechanics: design on paper, no diffs against gate files).

### 4.5 Control actions and permission/human-gate matrix

Eight control actions per the issue's own enumeration, mapped to **existing** enforcement points
(no new gate is created by this spike):

| Control | Existing surface it maps to today | Human gate |
|---|---|---|
| `continue` | Default (no-op) | None |
| `retrieve` | Memory v2 top-k retrieval (phase start only, today) | None |
| `replan` | Not implemented as a distinct control; closest existing behavior is a scheduler retry/re-dispatch | Retry ceiling (`breaker.get_retry_count`) |
| `switch_tool_or_model` | Not implemented; would be #239's routing layer once rerun | #239 rerun's capability floors |
| `verify` | Conformance/code-review maker/checker gates | Gate pass/fail is the human-visible signal |
| `abstain` | Refine's own `UNCERTAIN:` product-owner protocol; Phase 2 pre-flight `needs-discussion` | `needs-discussion` label |
| `escalate` | `breaker.trip_to_blocked` (Blocked + `needs-discussion` + `factory-regression` + comment) | Human review of Blocked ticket |
| `stop` | Scheduler dispatch-skip (WIP/ceiling/breaker gates already refuse to dispatch) | Scheduler poll loop |

Every named `.factory/adapter.yaml` `loops:` entry already declares `side_effect_level` (1–6),
`verifier`, `stop_condition`, `failure_behavior`, and `handoff` (epic #194 A1, `adapter.py`). This
spike recommends any future control-action ↔ permission enforcement (i.e., "which control actions
are auto-executable at which side-effect level") be designed as an **extension of that existing
per-loop schema**, not a parallel permission system — but the enforcement itself is #196's scope
("side-effect levels 1–6 with per-level enforced permission profiles"), currently unbuilt. Per the
#189 memory precedent on comment-channel proposals for not-yet-existing gates: this table is the
paper design; it is **not** a diff against `adapter.py`, `.factory/adapter.yaml`, or any
`gate_*` script, and must not be treated as authorizing one without its own reviewed ticket.

### 4.6 Historical fixture inventory and anti-leakage rules

**Schema layer (settled by this spike):** historical replay would join, per `run_id`:
`run-records/<run_id>.json` (stage/verdict/outcome + the new `monitor_control` rollup once built),
`monitor-events.jsonl` (the new Layer 2 stream), and `request-ledger.jsonl` (cost/token, subject to
its existing 100 MB × 3 rotation — bounding how far back request-level evidence survives). None of
these are reachable from the refine container (they live on `dark_factory_state`, not the clone),
so the *instance* layer (which run IDs qualify, minimum-N per phase) is deferred to a follow-up
ticket that runs with volume access.

**Anti-leakage rules (finished, normative — not deferred):**

1. **Cut rule:** the replay input for a given intervention pivot point includes only events with
   `at` timestamps strictly before that pivot's `control_decision` event.
2. **Redaction:** all post-pivot `verify_outcome` events, downstream gate verdicts, and the run's
   final `outcome.state` (`run_record.py`'s existing outcome computation) are redacted from any
   input the (fast or slow) monitor sees when a replay asks "what would the monitor have decided
   here."
3. **Time-ordered hold-out:** fixtures are partitioned by wall-clock run date, not shuffled — a
   fixture set trains/tunes only on runs strictly earlier than the runs it is evaluated against,
   preventing a policy from implicitly learning future calibration drift.
4. **Testable assertion:** for each replayed pivot, the only thing scored is whether the
   *pivot-time* recommendation, given only pre-pivot evidence, matches (or would have improved)
   the real outcome — never whether it matches the real run's actual downstream events.

### 4.7 Baseline/ablation matrix (named axes, cells TBD)

| Variant | Outcome quality | Selective risk/coverage | High-conf. failure rate | False/unnecessary intervention | Human rework | Tokens/cost/latency | Failure spend | Unsafe/bypassed actions | Intervention utility |
|---|---|---|---|---|---|---|---|---|---|
| No-monitor control | TBD — follow-up ticket | TBD | TBD | TBD | TBD | TBD | TBD | TBD | n/a |
| Reflection-only (sham baseline) | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Fast-monitor only | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Slow-monitor only | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Dual-monitor (fast+slow) | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |

The "reflection-only" row exists specifically to test the paper's own caution that longer
reasoning/self-critique is not evidence of better self-monitoring — it is a sham baseline this
program must beat, not a design Dark Factory should ship on its own.

### 4.8 Metrics

- **Calibration:** per-model/per-phase/per-task-class reliability of `slow_monitor_diagnosis`
  events against `verify_outcome` ground truth (feeds #239's calibration-cohort requirement).
- **Quality:** downstream task outcome (existing `run_record.py` outcome computation).
- **Selective risk/coverage:** accuracy conditional on the monitor choosing to act vs. abstain.
- **Economics:** existing `harness_economics` (tokens/cost/latency), plus monitor-attributable
  spend as its own line (so intervention cost is separable from baseline task cost).
- **Safety:** count of unsafe/bypassed actions (any control execution that skipped its mapped
  human gate in §4.5 — should always be zero by construction, since this spike creates no new
  auto-executing path).
- **Intervention utility:** `outcome_delta` (Layer 2 schema) aggregated — did the intervention's
  benefit exceed its token/latency/false-positive cost.

### 4.9 Exact spec delta for #239

#239's current body already contains a correct rerun gate ("Depends on: #288... Do not approve or
implement the stale routing spec before that rerun") and is labeled `needs-discussion` — no label
or comment action is required by this spike. When #239 is re-refined, its spec must add, verbatim:

1. A `confidence_source` field taxonomy — `self_report` \| `empirical_calibration` \| `verifier` \|
   `source_disagreement` — reusing this spike's Layer 2 `signal_source` enum (§4.2) rather than
   inventing a parallel one.
2. Per-model/per-phase/per-task-class/per-domain calibration cohorts, sourced from the metrics in
   §4.8 once real fixture data exists (§4.6) — #239 must not claim calibration data it cannot cite.
3. Risk/coverage and abstention behavior as first-class routing outcomes, not just average quality
   (mirrors §4.7's selective-risk column).
4. High-confidence-failure and false-escalation rates as routing-quality gates, not just cost.
5. Explicit separation of actor/monitor/verifier authority in the routing decision record (Req. 3).
6. `route_to_human` / `abstain` as legitimate routing outcomes, not failure modes to be minimized
   away.
7. An explicit statement that self-reported confidence alone (`signal_source: self_report`) **must
   not** authorize dispatch, promotion, or any side effect — only `empirical_calibration` or
   `verifier` evidence may (Req. 2, directly).
8. Economics of the extra monitoring/routing-change cost vs. avoided failure spend, using
   `harness_economics`'s existing fields, not a new cost model.
9. Fallback behavior when calibration data are absent or stale: fail closed to the current
   cheap-model-first default, never to an unvalidated higher-autonomy tier.
10. Fail-closed behavior for consequential decisions, and locked replay/shadow acceptance criteria
    per §4.11's promotion path — #239 may not skip stages.
11. No new provider/model gateway abstraction; must stay inside `docs/provider-abstraction-design.md`
    / #202's existing boundary (#239's own body already states this constraint).

### 4.10 Targeted amendments (not full reruns)

| Ticket | Amendment |
|---|---|
| #235 (harness economics ledger) | Log monitor/control event counts and monitor-attributable spend (§4.8) as an additional `harness_economics` sub-field once §4.2 Layer 1 exists — additive, no schema break. |
| #238 (failure-spend taxonomy) | Owns: normalized repeated-failure fingerprint (extensible tuple, v1 = today's signature string per Q2), intra-run/tool-call-level repeat detection, the strategy-change-before-retry precondition, and any `escalate`→`replan` control remap. This spike explicitly hands these to #238 rather than designing them itself. |
| #240 (harness-swap replay benchmark) | Add reflection-only, fast-monitor-only, slow-monitor-only, and dual-monitor replay variants (§4.7's rows) to its existing ablation harness; measure intervention utility (§4.8) alongside its current cost/token deltas. |
| #245 (memory intervention trigger/silence policy) | Expand trigger/silence policy to include progress-delta, evidence-coverage, and knowledge-boundary signals from §4.2's schema, while preserving the #241 memory worker's explicit non-planner boundary (non-goal, unchanged). |
| #246 (memory intervention A/B/ablation) | Add calibration, selective-risk, false-intervention, and high-confidence-failure metrics (§4.8) to its existing staged-rollout evaluation plan. |
| #247 (memory provenance/confidence/security) | Treat monitor/control state (§4.2) as governed, provenance-linked, untrusted advisory state — same authority model as injected memory reminders; #247 is the natural home for enforcing "slow-monitor output cannot self-approve" at the schema-validation layer. |

### 4.11 Promotion, disable, and rollback recommendation

Offline replay → shadow → advisory → bounded default, matching Req. 8 exactly:

1. **Offline replay** (needs #240's fixture-access follow-up, §4.6 instance layer): score against
   §4.7's matrix on historical fixtures with anti-leakage rules applied. Gate: dual-monitor must
   beat both no-monitor and reflection-only on intervention utility, with zero unsafe/bypassed
   actions.
2. **Shadow**: emit Layer 2 events during real runs without any control action being executed
   (`recommended_control` logged, never applied). Gate: calibration (§4.8) meets a pre-registered
   threshold across at least two phases.
3. **Advisory**: recommendations surface in the issue comment / run-record for human read, still
   no auto-execution. Gate: human-rework metric does not regress vs. pre-advisory baseline.
4. **Bounded default**: only the specific, already-existing controls in §4.5 (i.e., no `replan`
   remap, no new auto-executing routing) may go live by default, and only behind the same
   `enabled` kill-switch convention already used by `epic_autopilot`, `main_red_autofix`, and
   `conflict_resolution` in `config/config.yaml`.
   **Rollback:** disabling the kill-switch reverts to today's behavior exactly (fast-monitor rules
   already exist unchanged; nothing in this spike alters `breaker.py`/`error_signature.py`), so
   rollback is a one-line config flip with no data migration.

At every stage, a stalled/negative-signal result is itself a valid spike outcome — this design
does not presuppose the loop ships.

---

## Alternatives Considered

1. **New independent monitor/control store (SQLite/Postgres/new volume).** Rejected — duplicates
   `run_record.py`'s already-durable role, requires a new secret in every least-trusted run
   container, and the #207 architecture memory names the exact condition (in-flight step-level
   progress) under which revisiting is warranted; the sibling-JSONL layer satisfies that condition
   without the cost (§Q1).
2. **New intra-run failure-tracking mechanism built by this spike.** Rejected — #238 already owns
   that signal in its acceptance criteria; building it here creates dual ownership and is a
   breaker-semantics change arriving as a side effect of a research ticket, which CLAUDE.md
   requires its own ticket for (§Q2).
3. **Generic reflection prompt after every phase stage.** Rejected by the issue's own non-goals
   and by the paper's caution that longer reasoning/self-critique is not evidence of reliable
   self-monitoring — this is exactly what the reflection-only sham baseline (§4.7) exists to test
   against, not adopt directly.
4. **Trusting raw self-reported confidence as routing/promotion authorization.** Rejected — the
   paper's central calibration-variance finding and Req. 2 both rule this out; `signal_source` in
   §4.2 exists specifically so this can never be the only recorded evidence for a consequential
   decision.
5. **Filling in the baseline/ablation matrix and fixture inventory with fabricated example data
   to make the spec feel "complete."** Rejected per Q3 — refine cannot reach the data, and
   invented numbers are worse than an honest `TBD` with a named follow-up owner.

---

## Open Questions (non-blocking)

- Whether `replan` should ever fully replace `escalate` for the "same failure twice" fast-monitor
  rule, or whether escalate-first is the permanent correct default for a headless, human-out-of-
  the-loop factory — left to #238's own refinement once it has fingerprint data to reason about.
- Whether the slow-monitor's model pin should follow the `epic_autopilot`-style Opus-only pattern
  or the `cheap_model_first`/`escalation.opus_only_for` tiering already in `config/config.yaml` —
  left to the follow-up ticket that actually builds the slow-monitor DAG node.
- Exact minimum-N and phase-coverage targets for the fixture instance layer (§4.6) — left to the
  follow-up ticket with volume access, since refine cannot inspect the actual population size.

## Assumptions (flagged)

- `dark_factory_state` volume mount conventions, rotation sizes, and join-key semantics documented
  in existing memory entries and archived designs (harness economics, model proxy) are assumed
  still accurate as of 2026-08-28; not independently re-verified against the live volume (refine
  has no access to it).
- #239, #235, #238, #240, #245, #246, #247 titles/bodies/labels were read live via `gh issue view`
  at spike time (2026-08-28) and are assumed stable; if any has since been substantially re-scoped,
  the amendments in §4.9–§4.10 should be re-checked against the current body before being applied.
- This spec assumes the operator's "spec-only deliverable" instruction supersedes the issue body's
  "do not create a repository research Markdown document" line for the specific case of the
  refine-phase's own mandatory spec artifact (which the phase command requires regardless of
  ticket content) — the issue's "durable decision report" requirement is satisfied by this file
  plus the Phase 6 issue-comment summary, not by a second, separate research document.
