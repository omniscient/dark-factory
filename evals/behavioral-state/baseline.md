# Behavioral State Decay — Baseline

**Committed:** 2026-07-16
**Corpus version:** 1
**Fixture count:** 10 (7-category floor met; environment-fact-ignored,
failed-command-repeated, and policy-violated-before-side-effect each carry a second,
contrasting fixture)
**Sourced from:** omniscient/dark-factory (7 fixtures) and omniscient/markethawk
(3 fixtures, cross-target — `source_repo` field on each fixture)

---

## Per-Fixture Table

| Fixture | Category | Source | Pivot event | Verifier signal |
|---|---|---|---|---|
| `requirement-forgotten-01` | requirement-forgotten | dark-factory #49 | Spec archived one commit after being pinned by tests | Code Review BLOCKED |
| `environment-fact-ignored-01` | environment-fact-ignored | dark-factory #266 | Same two-dot-diff bug re-triggers on its own fixing ticket's branch | Self-caught revert; fixed in `041f140` |
| `environment-fact-ignored-02` | environment-fact-ignored | dark-factory #280 | `--scenario new` never added to the budget registry | Open issue, unresolved at writing |
| `failed-command-repeated-01` | failed-command-repeated | dark-factory #421 | Non-fast-forward push rejection recurs 18x | Never actioned across all 18 attempts |
| `failed-command-repeated-02` | failed-command-repeated | dark-factory #394 | Missing `cli.py` path recurs 3x logged | Never actioned across 3 attempts |
| `diagnosis-lost-01` | diagnosis-lost | markethawk #360 | Spec-vs-guard conflict diagnosed twice, 22s apart | De-conflict fails both times, unresolved |
| `subgoal-abandoned-01` | subgoal-abandoned | markethawk #391 | 5 code-review blockers posted | PR merged 23 min later, 0 fix commits |
| `policy-violated-before-side-effect-01` | policy-violated-before-side-effect | markethawk #360 | Safety guard removed to satisfy spec text | Code Review BLOCKED (near-miss) |
| `policy-violated-before-side-effect-02` | policy-violated-before-side-effect | dark-factory #212 | Gate label applied without checking artifact | Fixed by `push_gate_check.sh` |
| `phase-handoff-loses-state-01` | phase-handoff-loses-state | dark-factory #212 | Dead agent's node still marked completed | Fixed by `push_gate_check.sh` |

---

## Scorecard

### Decay-event incidence per category

| Category | Fixtures | Share of corpus |
|---|---|---|
| requirement-forgotten | 1 | 10% |
| environment-fact-ignored | 2 | 20% |
| failed-command-repeated | 2 | 20% |
| diagnosis-lost | 1 | 10% |
| subgoal-abandoned | 1 | 10% |
| policy-violated-before-side-effect | 2 | 20% |
| phase-handoff-loses-state | 1 | 10% |

### Repeated-failure count

Computed from each fixture's `provenance[]` length where the entries are independent
failed attempts at the same root cause (not merely distinct events):

| Fixture | Repeated-failure count |
|---|---|
| `failed-command-repeated-01` (#421) | 18 (full `evals/factory-failures.jsonl` record count for this issue) |
| `failed-command-repeated-02` (#394) | 3 (logged `de-conflict` recurrences) |
| `diagnosis-lost-01` (#360) | 2 (near-duplicate diagnoses, 22s apart) |
| All other fixtures | 0 (single-occurrence pivots, not repeated-failure patterns) |

### Requirement-violation count

| Fixture | Count |
|---|---|
| `requirement-forgotten-01` (#49) | 2 — the archive-a-referenced-doc rule was violated once at #42 (first occurrence, which produced the CLAUDE.md rule) and again at #49 (this fixture's pivot), despite the written memory entry from the first occurrence |
| `policy-violated-before-side-effect-01` (#360) | 1 — the safety-guard removal |
| `policy-violated-before-side-effect-02` (#212) | 1 — unconditional gate-label application |

### Open-subgoal completion

| Fixture | Subgoals opened | Subgoals completed before terminal action | Completion rate |
|---|---|---|---|
| `subgoal-abandoned-01` (#391) | 5 (code-review blocking findings) | 0 | 0% |

### Human rework

| Fixture | Rework required |
|---|---|
| `environment-fact-ignored-01` (#266) | Operator manually rebuilt `scripts/factory_core/providers/*` after the #251 refine-run excision silently deleted it (per issue #266's own body) |
| `phase-handoff-loses-state-01` (#212) | Two stranded tickets (#43, #41) required manual relabeling/re-dispatch after mid-phase agent deaths |

### Turns (event count)

Directly the `provenance[]` length per fixture — always recoverable from the
reconstructed event sequence:

| Fixture | Turns (events) |
|---|---|
| `requirement-forgotten-01` | 4 |
| `environment-fact-ignored-01` | 4 |
| `environment-fact-ignored-02` | 2 |
| `failed-command-repeated-01` | 4 |
| `failed-command-repeated-02` | 3 |
| `diagnosis-lost-01` | 3 |
| `subgoal-abandoned-01` | 3 |
| `policy-violated-before-side-effect-01` | 2 |
| `policy-violated-before-side-effect-02` | 3 |
| `phase-handoff-loses-state-01` | 7 |

### Tokens / cost / latency

**Best-effort / N/A.** Per the spec's Q4 caveat, event-anchored reconstruction from
durable comments/commits/memory writes does not carry per-run token/cost/latency
telemetry for these historical episodes; `evals/factory-failures.jsonl` entries record
timestamps only. Where a fixture's window is tight (e.g. `subgoal-abandoned-01`'s 23
minutes between finding and merge), that wall-clock gap is reported in the per-fixture
table's pivot/verifier columns above rather than as a separate cost figure.

### Annotator-reliability spot-check

A second annotation pass re-derived `category` and `pivot_event_index` from each
fixture's raw `provenance[]` array alone (without reading the `annotation.notes` field
first), for a 3-fixture sample spanning three different categories and both source
repos:

| Fixture | First-pass category/pivot | Second-pass (blind) category/pivot | Agreement |
|---|---|---|---|
| `requirement-forgotten-01` | requirement-forgotten / index 1 | requirement-forgotten / index 1 | Agree |
| `subgoal-abandoned-01` | subgoal-abandoned / index 1 | subgoal-abandoned / index 1 | Agree |
| `phase-handoff-loses-state-01` | phase-handoff-loses-state / index 2 | phase-handoff-loses-state / index 2 | Agree |

**Agreement: 3/3 (100%)** on this sample. This is a spot-check signal, not a
statistically powered reliability study — expand the sampled fraction if the corpus
grows past its ~10-14 target (see Adding Fixtures below).

### State-decay event precision

**Deferred to omniscient/dark-factory#241 child 5.** Precision requires scoring a
detector's predictions against this ticket's ground-truth labels; no detector exists
yet (epic child 3 builds it, child 5 scores it). This baseline instead delivers the
hand-labeled ground truth — the 10 fixtures' `category`/`pivot_event_index`/`annotation`
fields above — that child 5's precision metric will be scored against. No placeholder
number is reported here.

---

## Adding Fixtures

1. Identify a candidate event using the same durability bar as this corpus: a real,
   independently re-verifiable `gh issue view`/`gh pr view`/`git show` citation or a
   `.archon/memory/*.md` entry — never a fabricated transcript.
2. Classify it against `rubric.md`'s eligibility floor for the target category.
3. Author a new `evals/behavioral-state/fixtures/<category>-<NN>.json` following the
   schema in `docs/superpowers/specs/2026-07-16-behavioral-state-decay-baseline-design.md`.
4. Run `python -m pytest tests/test_behavioral_state_fixtures.py -v` — the new fixture
   must pass schema validation and the corpus must stay within the tested 10-14 range
   (raise the range in `tests/test_behavioral_state_fixtures.py` deliberately if growing
   past 14).
5. Update this file's Per-Fixture Table and Scorecard sections with the new fixture's
   contribution to each metric.
