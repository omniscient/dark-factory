# Skill-Modularized Dark Factory Prompt Flow Evaluation

**Issue:** omniscient/dark-factory#48
**Status:** draft — pending review
**Extends:** omniscient/dark-factory#161 (token-quality evaluation) / its implementation, #672
**Depends on:** omniscient/dark-factory#43, #44, #45 (all CLOSED/merged)
**Parent epic:** Claude Skills prompt-modularization supplement to omniscient/dark-factory#36

---

## Overview / Problem Statement

#43, #44, and #45 modularized parts of Dark Factory's prompt flow toward `.claude/skills/`
(reference skills backed by clone-live rubric/config files), but they did **not** modularize the
flow uniformly:

- `conformance` and `code-review` gained a genuine **runtime-swappable artifact**: their reviewer
  rubric resolves clone-live-first from `.claude/skills/{conformance,code-review}/RUBRIC.md`,
  falling back to the baked `/opt/refinement-skills/{conformance,code-review}-reviewer-prompt.md`
  copy only if the clone-live file is absent (`commands/dark-factory-conformance.md`,
  `commands/dark-factory-code-review.md`). `plan`'s Phase 3.5 plan-vs-spec check reads the same
  conformance RUBRIC the same way.
- `refine` and `plan`'s own narrative content only got **prose deduplication** (#43): the
  Archon-dispatched command shrank overlapping text and `refinement-skills/orchestrator-prompt.md`
  / `product-owner-prompt.md` became thin persona stubs. There is no swappable prompt artifact —
  `.claude/skills/refinement/` holds only `config.yaml`, no `SKILL.md`.
- `implement`'s `continue-intent` sub-flow (issue.json `intent: "continue"`) got a **context-injection
  swap** (#45): `comment-digest.md`, a script-produced compact artifact, replaced raw comment-array
  reads in Phase 1.

This asymmetry means "current flow vs. skill-modularized flow" is not one comparison — it is two
structurally different comparisons wearing the same label. This spec defines a two-tier evaluation
methodology that produces a real, tier-honest scorecard and a rollout recommendation, matching #48's
acceptance criteria, without overstating what a confounded historical diff can prove.

---

## Requirements (distilled from acceptance criteria + Q&A)

1. Cover all six named scenarios: refine, plan, implement, continue (implement's continue-intent
   sub-flow — confirmed via `commands/dark-factory-implement.md`, there is no separate top-level
   "continue" command), conformance, and review (= code-review, per
   `.claude/skills/code-review/SKILL.md`'s own "Gate 3 (dark-factory-code-review)" description).
2. Use a **two-tier evidence model**, not a uniform mechanism:
   - **Tier 1 (controlled toggle A/B):** conformance, code-review, plan's Phase 3.5 sub-check.
     Same historical issue/diff, clone-live RUBRIC.md present vs. forced-absent.
   - **Tier 2 (observational before/after-commit):** refine, plan's own narrative, implement/continue.
     Real historical runs bucketed around the #43 (refine/plan-narrative) and #45 (continue's
     comment-digest) merge boundaries; explicitly confounded (different issues, complexity, and
     unrelated intervening commits) and labeled as such.
3. Source historical data by dimension, not by a single blended pool:
   - Qualitative/causal judgments (spec/plan quality, implementation correctness,
     conformance/review safety, missed constraints) → **dark-factory self-target** runs, because
     the prompts under test are dark-factory's own.
   - Quantitative deltas (token count, tool-call count, runtime) → **MarketHawk + `bench/suite.json`**
     population, for statistical volume, reusing the existing `evals/token_opt_eval.py` /
     `scripts/fetch_scorecard.py` machinery that already defaults there.
4. Execution mechanism defaults to **mining artifacts from real historical runs** (PR comments,
   verdicts, cost reports via `fetch_scorecard.py`'s date-windowed PR mining), not a full paid live
   replay campaign. A small, hard-budget-capped set of live toggle spot-checks (order 3–5 pairs, on
   already-closed self-target issues) supplies causal confirmation for Tier 1 findings. A full
   multi-phase paid replay campaign is explicitly out of scope for this ticket (follow-up, see
   Open Questions).
5. `over/under-triggering of skills` is measured **only** where skill selection is genuinely
   model-mediated. Conformance/code-review RUBRIC resolution is deterministic file-presence — record
   `N/A — deterministic resolution` for that dimension there rather than fabricating a signal.
6. The rollout recommendation ladder (no-go / advisory-only / default-on for selected phases /
   broader rollout) is **gated by evidence tier**: only Tier 1 phases (conformance, code-review) can
   receive a "default-on" recommendation from this evaluation. Tier 2 phases (refine, plan-narrative,
   continue) receive at most an advisory/readiness verdict plus a statement of what a real Tier-1-grade
   toggle would require for them.
7. Preserve existing behavior per the parent epic: no changes to labels, scheduler semantics,
   project statuses, conformance/review gates, cost-report format, or epic/sub-issue handling. This
   is an evaluation, not a mechanism change.
8. Deliverable of this ticket's implement phase: the methodology (this spec), a scorecard schema,
   the mining-based harness (reusing `fetch_scorecard.py` and the same reporting shape as
   `evals/reports/token-opt-scorecard-*.md`), the budget-capped spot-check set, and a written
   rollout recommendation — mirroring how #161/#672 delivered a spec plus a script and committed
   scorecard rather than a standing service.

---

## Architecture / Approach

### 1. Scenario → tier → mechanism → data-source map

| Scenario | Modularization type (#43/44/45) | Tier | Comparison mechanism | Primary data source |
|---|---|---|---|---|
| refine | Prose dedup (#43) | 2 | Before/after `#43` merge boundary, historical runs | dark-factory self-target (qualitative); token/runtime deltas from either target where available |
| plan (own narrative) | Prose dedup (#43) | 2 | Before/after `#43` merge boundary | dark-factory self-target |
| plan (Phase 3.5 check) | Clone-live RUBRIC toggle (#44) | 1 | Toggle A/B on same issue/diff | dark-factory self-target (causal), MarketHawk+bench (volume) |
| implement (new-intent) | No skill change identified | — | Not evaluated (out of scope — no #43/44/45 touch point) | — |
| implement (continue-intent) | comment-digest.md injection (#45) | 2 | Before/after `#45` merge boundary | dark-factory self-target (qualitative), MarketHawk+bench (volume) |
| conformance | Clone-live RUBRIC toggle (#44) | 1 | Toggle A/B on same issue/diff | dark-factory self-target (causal), MarketHawk+bench (volume) |
| code-review (review) | Clone-live RUBRIC toggle (#44) | 1 | Toggle A/B on same issue/diff | dark-factory self-target (causal), MarketHawk+bench (volume) |

`implement`'s `new`-intent path is included in the table for completeness but is **not** part of
this evaluation: no #43/#44/#45 change touches it, so there is nothing to A/B.

### 2. Tier 1 methodology — controlled toggle A/B

For conformance, code-review, and plan's Phase 3.5 sub-check:

1. Select a small set (3–5) of already-closed, already-merged self-target dark-factory issues whose
   PRs exercised the relevant gate (conformance and/or code-review ran and posted a verdict).
2. For each, reconstruct the two arms by toggling presence of the relevant clone-live file
   (`.claude/skills/{conformance,code-review}/RUBRIC.md`) — present (skill-modularized arm) vs.
   forced-absent so the command falls back to the baked `/opt/refinement-skills/*-reviewer-prompt.md`
   copy (current-flow arm) — holding the diff/issue input constant.
3. Run both arms under a hard per-pair token budget cap (mirroring `bench/run_suite.sh`'s
   `BENCH_TOKEN_BUDGET`), record token count, tool-call count, runtime, and the verdict
   (tier/severity for code-review; CONFORMS/MINOR/MATERIAL for conformance).
4. This is the only mechanism in this evaluation that supports a causal claim: same input, one
   variable changed.
5. Supplement with mined historical verdicts: real conformance/code-review PR comments from before
   vs. after the #44 merge boundary, pulled via `fetch_scorecard.py`-style date-windowed PR mining,
   to widen the qualitative-safety sample beyond the spot-check pairs (this population is
   confounded the same way Tier 2 is, so it corroborates rather than substitutes for the toggle
   pairs).

### 3. Tier 2 methodology — observational before/after-commit

For refine, plan's own narrative, and implement/continue:

1. Identify the merge boundary commit for the relevant change (#43 for refine/plan-narrative, #45
   for continue's `comment-digest.md` wiring).
2. Use `scripts/fetch_scorecard.py`'s date-windowed factory-PR mining (pointed at
   `omniscient/dark-factory` and, for volume dimensions, `omniscient/markethawk`) to pull a
   population of runs from before and after the boundary.
3. Compare token/tool-call/runtime distributions and qualitative outcomes (spec/plan quality,
   missed constraints, factory-regression / scope-spillover / needs-discussion label incidence)
   between the two populations.
4. **Explicitly document confounds** in the scorecard: different issues, different complexity,
   unrelated commits landing in the same window, and any known one-off incidents (e.g. entries in
   `evals/factory-failures.jsonl`) that could skew a bucket.
5. Findings here support an advisory/readiness verdict, never a "default-on" recommendation.

### 4. Metrics and dimension applicability

| Dimension | Tier 1 (conformance/code-review/plan-3.5) | Tier 2 (refine/plan-narrative/continue) |
|---|---|---|
| Token count | Measured directly (toggle pairs) + mined population | Measured from mined population only |
| Tool-call count | Measured directly + mined | Measured from mined population |
| Runtime | Measured directly + mined | Measured from mined population |
| Spec/plan quality | N/A (not spec/plan-producing phases) except plan-3.5's own verdict quality | Measured qualitatively (self-target only) |
| Implementation correctness | N/A (reviewer phases, not implementers) | N/A for refine/plan; measured for continue via post-fix test outcomes where available |
| Conformance/review safety | Measured directly (verdict deltas) | N/A |
| Missed constraints | Measured directly (spot-check review) | Measured qualitatively (self-target only) |
| Skill over/under-triggering | `N/A — deterministic resolution` | N/A (none of these phases have model-mediated skill routing today; recorded as N/A, not fabricated) |

### 5. Deliverables

- A scorecard document under `evals/reports/` (naming convention consistent with
  `evals/reports/token-opt-scorecard-*.md`), containing the per-scenario tier, mechanism, sample
  set, measured deltas, and confound notes.
- A rollout recommendation section mapping each scenario to one of: no-go / advisory-only /
  default-on / broader-rollout, with the tier gate applied (§ Requirements #6).
- The harness itself (mining + spot-check runner) as a script under `evals/`, reusing
  `fetch_scorecard.py` for population mining rather than reimplementing PR-date-windowing.

### 6. Rollout recommendation ladder (tier-gated)

- **conformance, code-review:** eligible for no-go / advisory-only / **default-on**, based on
  Tier 1 toggle-pair + mined-verdict evidence. (Note: both are already live on `main` since #44/#45
  merged — "default-on" here means *confirming* the existing default is safe to keep, not a new
  rollout.)
- **plan's Phase 3.5 check:** same Tier 1 treatment, folded into the conformance verdict since it
  shares the same RUBRIC.md artifact.
- **refine, plan's own narrative, implement/continue:** capped at advisory/readiness — this
  evaluation cannot license a default-on claim for them without a real toggle mechanism, which does
  not exist today. The readiness verdict should state what building one would require (e.g., a
  clone-live/baked swap for refine's orchestrator/product-owner prompts, analogous to #44's RUBRIC
  pattern) as a candidate follow-up.

---

## Alternatives Considered

1. **Full paid live A/B replay across all six phases and both target repos.** Rejected — cost
   (a single `BENCH_MODE=full` parity run is ~$26 per `docs/parity-p2.md`; a full campaign across
   phases × arms × sample size is disproportionate to a size:M spec-and-cheap-harness ticket) and
   unnecessary given mined historical data plus a small spot-check set already supports a tier-honest
   recommendation.
2. **Uniform mechanism: check out one pre-#43/#44/#45 worktree and diff everything at once.**
   Rejected — conflates three independent, differently-shaped changes (prose dedup, RUBRIC toggle,
   context-injection swap) plus every unrelated intervening commit into one undifferentiated blob,
   destroying per-skill attribution. Strictly worse than per-phase commit boundaries.
3. **Narrow true A/B scope to conformance+code-review only, declare refine/plan/implement fully
   out of scope.** Rejected — the acceptance criteria explicitly enumerate all six scenarios for
   run selection; dropping three fails the ticket on its face. Chosen instead: keep coverage for
   all six, but downgrade the evidence claim (Tier 2) rather than omit the phases.
4. **Chosen: two-tier hybrid (toggle A/B where a real swappable artifact exists; observational
   before/after elsewhere), with mining as the default execution mechanism and a small budget-capped
   spot-check set for causal confirmation, gated rollout ladder.** Preserves full acceptance-criteria
   coverage, keeps cost proportionate to a size:M ticket, and keeps the rollout recommendation
   honest about what each tier can and cannot prove.

---

## Open Questions (Non-blocking)

- **Full paid live-replay campaign** (beyond the small spot-check set) is recommended as a follow-up
  ticket, budget-gated, if the Tier 1 spot-checks and mined data disagree or leave the conformance/
  code-review default-on question unresolved.
- **A real toggle mechanism for refine's own prompts** (clone-live/baked swap, analogous to #44's
  RUBRIC pattern) does not exist today; building one is a candidate follow-up if this evaluation's
  Tier 2 readiness assessment for refine recommends further investment.
- Formal closure of #41 (prompt/procedure surface inventory) remains open per the #42 policy spec's
  own note; this evaluation's scenario inventory (§1) can serve as partial input if #41 is picked up
  later, but is not a substitute for it.
- **Discovered during this refine pass (not part of this ticket's scope):** `dark-factory/scripts/oos_excise.sh`
  computes out-of-scope files via `git diff --name-only origin/main HEAD` (main's *current* tip),
  not the branch's merge-base. When a long-lived refine/plan/implement branch falls behind `main`
  (e.g. other tickets merge while this branch is open), files `main` gained after the branch's fork
  point are misidentified as this branch's out-of-scope additions and get checked out **onto** the
  branch (mislabeled as "should not have been created/modified" even though they're legitimate,
  already-reviewed upstream content). The fix is to diff against `$(git merge-base origin/main HEAD)`
  instead of `origin/main` directly. Recommend filing this as its own bug ticket; not fixed here per
  the refine phase's scope boundary (only `docs/superpowers/specs/` and `.archon/memory/` are
  authorized outputs for this run).

---

## Assumptions

- **[Flagged]** `scripts/fetch_scorecard.py`'s factory-PR fingerprinting (commit author email match)
  is assumed to reliably distinguish factory-authored PRs from human PRs in both
  `omniscient/dark-factory` and `omniscient/markethawk` history; this evaluation reuses that
  assumption rather than re-verifying it.
- **[Flagged]** The #43 and #45 merge-boundary commits are assumed to be clean bucketing points for
  Tier 2 (i.e., no other prompt-flow-relevant change landed in the same window that would
  contaminate the before/after split beyond what's already disclosed as a confound). If a reviewer
  identifies a contaminating commit, the affected bucket's window should be narrowed.
- The self-target (`omniscient/dark-factory`) historical population is assumed large enough to
  supply 3–5 usable Tier 1 spot-check pairs and a meaningful Tier 2 qualitative sample; if it is
  not, the harness should fall back to widening the window rather than silently substituting
  MarketHawk data for qualitative dimensions (per Requirement #3's dimension-based sourcing split).
