# Behavioral State Decay — Baseline Fixture Set and Incidence Report

**Issue:** omniscient/dark-factory#242
**Status:** draft — pending review
**Parent epic:** omniscient/dark-factory#241 (Proactive execution-state memory — prevent behavioral state decay)
**Coordinates with:** omniscient/dark-factory#240 (harness-swap replay benchmark — matched discipline, separate schema/location)

---

## Overview / Problem Statement

Epic #241 proposes a proactive execution-state memory worker to counter **behavioral state
decay**: requirements, environment facts, failed attempts, diagnoses, discoveries, and open
subgoals that remain stored (or even remain in context) but stop influencing the action agent
when it matters. Before building any intervention machinery, the epic needs proof the failure
mode actually occurs in Dark Factory's own refine/plan/implement/validate/review pipeline, a
reviewable rubric for classifying it, and a locked, reusable fixture corpus the later A/B and
ablation child can replay without needing to reconstruct history itself.

This ticket is the baseline-establishing child only. It does not build the execution-state
bank, the two-phase memory worker, or any intervention agent — those are epic #241's children
2-4. It also does not run comparative A/B evaluation — that is child 5, which this ticket's
fixtures and ground-truth labels exist to feed.

This is a **refine-phase spec only**. It defines the rubric, fixture schema, corpus location,
and baseline report shape that a follow-up implement-phase ticket will build. No code, fixtures,
or tooling are created by this document.

---

## Requirements

Distilled from the issue body and Q&A below:

1. Define a reviewable annotation rubric covering the 7 decay categories named in the issue:
   requirement-forgotten, environment-fact-ignored, failed-command-repeated, diagnosis-lost,
   subgoal-abandoned, policy-violated-before-side-effect, phase-handoff-loses-state.
2. Produce a locked, versioned fixture set built from Dark Factory's own historical
   refine/plan/implement/validate/review traces — at least one fixture per category, ~10-14
   total.
3. Each fixture must be provenance-linked to durable, already-committed evidence (no invented
   transcripts) and must explicitly separate what was knowable *before* the decay's pivot event
   from the later verifier/outcome signal used only for labeling — so a future replay of the
   fixture's prefix cannot leak the answer to an intervention agent.
4. Record a baseline incidence and outcome-impact report over the fixture corpus (and, where
   feasible, the broader historical record) using only metrics computable without an
   intervention variant to compare against.
5. Deliverables live in a new `evals/behavioral-state/` subtree, following existing `evals/`
   conventions rather than `bench/`'s golden-PR replay schema.
6. Coordinate with #240 by matching its discipline (locked, versioned, provenance-tracked
   corpus with a committed report and gitignored raw results) rather than sharing its directory
   or manifest schema, since the two tickets measure different units (closed-issue golden-PR
   replay vs. within-run behavioral trajectories).

---

## Brainstorming Q&A

> **Q:** Where in the repo should this ticket's deliverables live — the annotation rubric, the
> fixture set, and the baseline incidence/outcome report? Should fixtures reuse or extend #240's
> `bench/` harness/suite.json conventions, or should they live in a new, independent location?
>
> **A:** Put the deliverables in a new, self-contained subtree under `evals/`, not under
> `bench/`. `bench/suite.json` is a golden-PR replay manifest keyed on `pre_pr_sha` +
> `golden_pr` + `oracle_tests`, with no slot for a within-run trajectory, a decay-event pivot
> turn, or per-category annotation — force-fitting behavioral-state fixtures into it would
> corrupt both. `evals/` is already home to trace-derived, rubric-scored work
> (`factory-failures.jsonl` is a locked historical trace corpus; `memory-quality-report.md` is
> exactly the shape of a baseline incidence/outcome report). Concrete layout:
> `evals/behavioral-state/rubric.md`, `evals/behavioral-state/fixtures/` (versioned, each
> fixture keeping any future/verifier outcome in a field separate from the runtime-visible
> prefix), `evals/behavioral-state/baseline.md` (modeled on `bench/baseline.md` and
> `evals/memory-quality-report.md`), and a gitignored `results/` for raw scoring output
> (`evals/.gitignore` already ignores `results/`). Coordinate with #240 by matching its
> discipline — a locked, versioned fixture corpus with a committed baseline report — not by
> sharing its manifest or directory; `bench/` is explicitly target-vendored
> (`dark-factory/bench/run_suite.sh` under a target clone like MarketHawk), while the `evals/`
> family is scored against this repo's own factory traces and stays self-contained.

> **Q:** Given that no full per-turn transcript exists durably for past runs (container
> artifacts are ephemeral; `evals/factory-failures.jsonl` is a thin terminal-state snapshot, not
> a trajectory), what should "produce representative fixtures from traces" actually mean for a
> size:M ticket — literal turn-by-turn synthesis, terminal-outcome-only scoping, or something
> else? Does the rubric need turn granularity at all?
>
> **A:** Event-anchored reconstruction, not literal per-turn synthesis and not terminal-only.
> Decay is definitionally a two-point-in-time phenomenon (state established at T_a, stops
> influencing action at T_b) — every one of the 7 categories requires that ordering, and a
> coarse before/after diff can't express "diagnosis lost across debugging turns." But literal
> per-turn synthesis would fabricate data that doesn't exist and break the locked-corpus
> discipline. The right unit is a durable, provenance-linked **event** — a timestamped phase
> comment (refinement report, conformance/code-review verdict, the `dark-factory-cost-report`
> marker comment), a commit/diff on the branch, a `source:`/`issue:`-tagged `.archon/memory/*.md`
> write, or a repeat-failure record in `evals/factory-failures.jsonl` — not a reconstructed LLM
> turn. Each fixture: (a) carries a pivot field keyed to an event index, (b) is marked
> `fidelity: reconstructed` so no fixture claims turn-level transcript fidelity, and (c) has an
> explicit prefix/suffix split — the annotator uses hindsight (the later verdict) to *label* the
> pivot, but records what was knowable at/before the pivot separately from that verdict, so a
> future replay of the prefix to an intervention agent cannot see the answer.

> **Q:** Should the implement-phase deliverable include a reusable fixture-extraction script
> that mines GitHub comments/commits/memory entries into the fixture format, or should fixtures
> be hand-authored/curated with no tooling, given the size:M budget? Is there a minimum fixture
> count that makes the set "representative," or should the exact count be left to implementer
> judgment?
>
> **A:** Hand-authored/curated markdown+JSON, no required extraction pipeline — deciding where
> the pivot sits and which future signal must be withheld is exactly the human annotation
> judgment the rubric exists to capture, so a scraper could produce a raw candidate but not a
> valid fixture. Precedent: `evals/memory-quality-report.md` was hand-curated from real issue
> numbers; even `bench/find_eligible.py` is an eligibility *detector*, not a fixture generator,
> and explicitly routes anything unverifiable to human review. An optional, non-required
> candidate-*surfacer* script (list events matching a category signature, emit stubs for human
> review — analogous to `find_eligible.py`) may be added if implementer budget allows, but is
> never authoritative over the annotation and is not a required deliverable. On count: hard
> floor of 1 fixture per each of the 7 categories (non-negotiable — the category set is the
> scope), total target ~10-14 so a few categories carry a second contrasting case (true positive
> + near-miss), mirroring `bench/suite.json`'s "locked, representative, not exhaustive" stance.
> Exact count above the floor is implementer judgment, bounded by a documented expansion path so
> the corpus can grow without re-refinement.

> **Q:** Epic #241's own A/B/ablation child (child 5) is what compares baseline, full-bank,
> always-inject, retrieval-only, and selective-intervention variants — this ticket only
> establishes the baseline, with no intervention agent yet. Of the 7 metrics listed in the issue
> body, which are computable now for a baseline-only report, and which are structurally
> impossible without an intervention variant to compare against? Should the not-yet-computable
> ones be explicitly deferred rather than stubbed?
>
> **A:** Six of seven are computable now, as baseline incidence plus within-corpus outcome
> impact — pass/outcome quality, repeated-failure count, requirement-violation count,
> open-subgoal completion, human rework, and turns/tokens/cost/latency — because they are
> directly observable properties of the historical trajectories themselves. "Outcome impact" is
> satisfied by stratifying the corpus (decayed vs. non-decayed trajectories, or before/after the
> pivot within a single fixture), a within-baseline comparison, not an intervention comparison.
> Caveat: turns is always recoverable from the reconstructed event sequence, but tokens/cost/
> latency should be marked best-effort since Q2's event-anchored reconstruction often won't carry
> that telemetry. The genuinely non-computable metric is **state-decay event precision** —
> precision scores a detector's predictions against ground truth, and no detector exists yet
> (that's epic child 3, scored in child 5). The spec explicitly defers it rather than stubbing a
> hollow number: this ticket instead delivers the hand-labeled decay-event ground truth (plus its
> incidence and annotator-reliability signal) that child 5's precision metric will later be
> scored against.

---

## Architecture / Approach

### Directory layout (implement-phase deliverable, not created by this spec)

```
evals/behavioral-state/
  rubric.md          # the 7-category annotation rubric + pivot/prefix-suffix methodology
  fixtures/           # locked, versioned fixture corpus (see schema below)
    <category>-<NN>.json
  baseline.md         # committed prose baseline: incidence + within-corpus outcome impact
  results/            # gitignored raw scoring output (matches evals/.gitignore convention)
```

### Fixture schema

Each fixture is a single JSON file recording one reconstructed decay episode:

```jsonc
{
  "id": "requirement-forgotten-01",
  "category": "requirement-forgotten",       // one of the 7 locked categories
  "version": 1,
  "fidelity": "reconstructed",               // always this value — no fixture claims
                                              // turn-level transcript fidelity
  "source_issue": 360,
  "source_repo": "omniscient/markethawk",    // or omniscient/dark-factory (self-target trace)
  "provenance": [                            // ordered, durable, linkable events —
                                              // NOT reconstructed LLM turns
    {"event": "refinement_report_comment", "url": "...", "timestamp": "2026-06-13T..."},
    {"event": "commit", "sha": "...", "timestamp": "..."},
    {"event": "conformance_verdict_comment", "url": "...", "timestamp": "..."},
    {"event": "memory_write", "file": ".archon/memory/backend-patterns.md", "issue": 360}
  ],
  "pivot_event_index": 2,                    // index into provenance[] marking where the
                                              // decayed state stopped influencing the action
  "prefix": {                                // knowable at/before the pivot — this is the
                                              // ONLY part a future replay may show an
                                              // intervention agent
    "established_state": "requirement text or fact, as first stated",
    "established_at_event_index": 0
  },
  "suffix": {                                // hindsight-only — used for labeling, never
                                              // injected into a runtime replay
    "outcome": "what actually happened once the state decayed",
    "verifier_signal": "e.g. conformance MATERIAL finding, code-review BLOCKED verdict"
  },
  "annotation": {
    "rubric_version": 1,
    "confidence": "high | medium",
    "notes": "free text — annotator reasoning"
  }
}
```

### Baseline report (`baseline.md`)

Modeled on `bench/baseline.md` and `evals/memory-quality-report.md`: a per-fixture table
(category, source issue, pivot event, outcome) plus a scorecard section reporting, over the
fixture corpus:

- Decay-event incidence per category
- Repeated-failure count, requirement-violation count, open-subgoal completion, human rework —
  all computed from the reconstructed provenance chain
- Turns (event count) always; tokens/cost/latency marked best-effort / N/A where the
  reconstructed evidence doesn't carry telemetry
- Annotator-reliability signal for the hand-labeled ground truth (e.g. a second reviewer
  spot-checks a sample and records agreement), since this ground truth is what child 5's
  state-decay event precision metric will later be scored against
- An explicit "Deferred to #241 child 5" note for state-decay event precision itself — not a
  stub number

### Rubric (`rubric.md`)

One section per category, each defining: the decay signature in terms of provenance-event
pairs (what establishes state, what should have used it, what evidence shows it didn't), the
minimum evidence required to label a candidate (vs. reject it as unverifiable — mirroring
`bench/find_eligible.py`'s eligibility-detector precedent), and worked examples once fixtures
exist.

---

## Alternatives Considered

1. **Reuse `bench/suite.json`'s golden-PR schema for behavioral-state fixtures.** Rejected —
   that schema is keyed on closed-issue/pre_pr_sha/oracle-test replay, a different unit of
   measurement than a within-run trajectory pivot; force-fitting would corrupt both ticket's
   corpora and confuse #240's own replay tooling.
2. **Build a fixture-mining script as part of this ticket.** Rejected for the required
   deliverable — annotation judgment (where the pivot sits, what must stay hidden from the
   replay prefix) is inherently human, and this ticket's refine-phase scope boundary forbids
   writing code regardless. Left as an optional, non-authoritative implement-phase accelerator.
3. **Require literal per-turn transcript fixtures.** Rejected — no durable per-turn record
   exists for historical runs; fabricating one would violate the locked/provenance-linked
   corpus discipline this ticket is meant to establish.
4. **Stub `state-decay event precision` with a placeholder value now.** Rejected — precision
   requires a detector variant that doesn't exist until epic child 3; a stub number would be
   meaningless and likely misread as a real baseline. The ground-truth labels this ticket
   produces are what makes the later metric computable, so those are delivered instead.

---

## Open Questions (Non-blocking)

- Should fixtures be drawn only from `omniscient/dark-factory` traces, or also from the
  `omniscient/markethawk` target instance (both are live Dark Factory deployments per
  CLAUDE.md)? The Q&A didn't resolve this explicitly; the fixture schema above includes a
  `source_repo` field so either or both can be represented. Recommend defaulting to whichever
  instance has richer/more-recent `evals/factory-failures.jsonl`-style records at
  implement time.
- Exact target count above the 7-category floor (10-14 suggested) should be finalized once the
  implementer surveys how many verifiable candidates actually exist per category — some
  categories (e.g. phase-handoff-loses-state) may have fewer durable examples than others.
- Whether the optional candidate-surfacer accelerator script is worth building within the M
  budget is an implementer call, not a spec requirement.

---

## Assumptions

- "Historical Dark Factory traces" means the durable secondary record (GitHub issue/PR
  comments, commit history, `.archon/memory/*.md` entries, `evals/factory-failures.jsonl`), not
  a per-turn agent transcript — confirmed in Q&A; no such transcript is committed anywhere.
- This ticket does not implement the execution-state bank, memory worker, or trigger policy
  (epic children 2-4) and does not run any intervention-variant comparison (child 5) — it is
  ground-truth and baseline-incidence work only.
- The annotation rubric is "reviewable" in the sense of being legible and auditable by a human
  reviewer (structured, provenance-linked, versioned) — not that every individual fixture
  requires live human sign-off before being committed, though the annotator-reliability
  spot-check in `baseline.md` provides a reliability signal.
