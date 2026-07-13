# Roll Out Dark Factory Claude Skills Modularization with Compatibility Wrappers

**Issue:** omniscient/dark-factory#49
**Status:** living reference — not archived on completion (mirrors #42's spec; only this ticket's
*plan* doc is archived per `.archon/memory/codebase-patterns.md`'s PR #215 lesson)
**Depends on:** omniscient/dark-factory#48 (CLOSED — Evaluate skill-modularized Dark Factory prompts
against current prompts)
**Parent epic:** Claude Skills prompt-modularization supplement to omniscient/dark-factory#36
**Related, explicitly NOT this ticket's scope:** omniscient/dark-factory#218 (OPEN — physical move of
`refinement-skills/` → `.claude/skills/refinement/templates/`, deferred from #42 §2)

---

## Overview / Problem Statement

Issue #48 built an evaluation harness (`evals/skill_flow_eval.py`, `evals/skill_flow_scorecard.py`)
comparing Dark Factory's current production mechanism — an Archon command reading a persona/rubric
prompt file and substituting it into a manually-spawned Agent-tool subagent — against a
skill-modularized alternative, across five scenarios. Its committed scorecard
(`evals/reports/skill-modularization-scorecard-2026-07-13.md`) recommends **advisory-only**
(conformance, code_review) or **advisory-readiness** (refine, plan_narrative, continue) for every
scenario; no scenario is cleared for default-on or blocking skill-invocation, and the Tier 1 live
A/B spot-check that would substantiate a stronger recommendation could not run (missing
`ANTHROPIC_API_KEY` in that evaluation environment).

Issue #49 is the rollout ticket that acts on that evaluation: it must roll out the skill-based
prompt structure "without breaking existing Archon/Dark Factory commands, scheduler semantics, or
project board behavior," while explicitly starting advisory/manual rather than defaulting any
high-risk phase.

Three prior tickets already did the mechanism-building this ticket rolls out:

- **#42** (closed) defined the taxonomy — Archon phase commands (`commands/*.md`, dispatched by
  `command:` id, never model-invoked) vs. reference skills (`.claude/skills/<name>/`,
  model-invocable) — and deferred the physical consolidation of `refinement-skills/` to its own
  follow-up (now filed as **#218**, still open).
- **#43** (closed) trimmed the refine/plan personas in place at `refinement-skills/`
  (baked to `/opt/refinement-skills/` at Docker build time) and fixed their MarketHawk-hardcoded
  wording, without moving the directory.
- **#44** (closed) created `.claude/skills/conformance/` and `.claude/skills/code-review/`
  (`SKILL.md` + `RUBRIC.md`) and wired all three call sites
  (`commands/dark-factory-conformance.md`, `commands/dark-factory-plan.md` Phase 3.5,
  `commands/dark-factory-code-review.md`) plus `scripts/context_budget.py` to resolve the rubric
  **clone-live-first, falling back to the baked `/opt/refinement-skills/*-reviewer-prompt.md`
  copy** when the clone-live file is absent. This fallback is the only compatibility wrapper that
  exists in the repo today.

That fallback is currently verified only by three static tests
(`tests/test_conformance_command_rubric_fallback.py`, `tests/test_code_review_command.py`,
`tests/test_plan_command_conformance_rubric_fallback.py`) that assert the clone-live path string
appears before the baked path string inside the command markdown — none of them simulate the
clone-live file actually being absent and confirm the gate still runs correctly against the baked
copy. This ticket closes that gap.

**A live finding from this refinement pass, kept for the record:** the running container's
`/opt/refinement-skills/product-owner-prompt.md` and `orchestrator-prompt.md` are stale — they
still carry pre-#43 MarketHawk-hardcoded content, even though the git-tracked source at
`refinement-skills/*.md` (HEAD) already has #43's fix. This is a concrete, currently-live instance
of the baked-image staleness risk #42/#43 already called out ("editing either prompt requires a
full `docker compose --profile factory build`"). It directly motivates this ticket's rollback
section (Architecture §3) documenting that hazard, rather than treating it as a hypothetical.

---

## Requirements

Distilled from the issue's five acceptance criteria, refined through Q&A below, and scoped tightly
per the `size: S` budget:

1. Produce a **rollout-status runbook** — a living, durable reference doc (this spec itself, kept
   at its `docs/superpowers/specs/` path and never archived) — that records, per scenario, current
   rollout state, the factory's official advisory-only rollout stance, and explicit rollback steps.
2. Add **runtime tests** (not just static string-order assertions) that exercise the existing
   clone-live/baked-fallback wrapper for conformance and code-review: simulate the clone-live
   `RUBRIC.md` being absent and assert the gate still resolves and runs against the baked
   `/opt/refinement-skills/*-reviewer-prompt.md` copy.
3. Add a runtime test asserting that `scripts/context_budget.py`'s `skill_prompts` section (and, by
   extension, cost-report telemetry) still correctly labels the `conformance`/`code-review`
   scenario regardless of which path (clone-live or baked) resolved.
4. Leave the Tier 2 scenarios (refine, plan_narrative, continue) untouched — no skill-modularized
   alternative exists for them yet (`.claude/skills/refinement/` holds only `config.yaml`, no
   `SKILL.md`), and #48's scorecard rates them "advisory-readiness (confounded)," not even
   A/B-toggleable. The runbook documents this as "not yet rollout-eligible," not as an oversight.
5. Do **not** build any new wrapper mechanism, alias layer, or third fallback tier — #44's
   clone-live/baked-fallback is already the wrapper; the "add compatibility wrappers if skill names
   change" criterion is conditional and not triggered by this ticket's own diff, since no skill is
   renamed here (that belongs to #218). The runbook documents #44's wrapper as satisfying the
   criterion and names #218 as the ticket obligated to preserve/extend it when the physical move
   happens.
6. Do **not** modify `.archon/workflows/archon-dark-factory.yaml` or any `command:` id. "Preserve
   existing command messages" is satisfied by the diff's absence of any change to that file; the
   runbook states this invariant explicitly rather than relying on `check_workflow_dag.py` (which
   validates DAG structural integrity, not literal dispatch phrasing, and would misattribute the
   guarantee if cited).
7. Protect the runbook from accidental archival per CLAUDE.md's "never archive a doc that tests or
   README still reference" rule: add a `README.md` link (mirroring the existing #42 spec link) and
   a doc-shape test (mirroring `tests/test_claude_skills_policy_doc.py`'s pattern) that pins this
   spec's path.
8. State a forward obligation in the runbook: when #218 executes the physical
   `refinement-skills/` → `.claude/skills/refinement/templates/` move, it must update this runbook
   and preserve the clone-live/baked fallback for the renamed paths.

---

## Brainstorming Q&A

> **Q1:** Given #218 owns the physical rename (not this ticket), #44 already built the only
> compatibility wrapper that exists, and #48's scorecard clears no scenario for default-on
> skill-invocation, what should #49 concretely deliver at `size: S`?
>
> **A1:** A rollout-status runbook (per-scenario current state + explicit rollback steps, including
> the baked-copy staleness hazard) plus a small number of new runtime tests that actually exercise
> fallback behavior and telemetry labeling — scoped only to conformance/code-review, the two
> scenarios with a real clone-live/baked pair today. Building a first skill-modularized alternative
> for a Tier 2 scenario (rejected alternative) would create net-new production surface for a
> high-risk phase the scorecard didn't clear, blow the size budget, and risk a gate-adjacent change
> as a side effect. Documentation-only with no tests (also rejected) fails the "confirm...telemetry"
> criterion, which is a testable assertion, not a prose claim — and the sole existing wrapper is
> currently backed only by static string-order tests, so "confirm the wrapper works" is unbacked
> without a runtime test.

> **Q2:** Where should the rollout-status runbook live, given this repo's demonstrated convention
> that durable policy/reference docs (e.g. #42's spec) stay at their `docs/superpowers/specs/` path
> permanently and get cited by path from sibling tickets, while narrow one-shot implementation specs
> (#43, #44) get archived after merge?
>
> **A2:** As this ticket's own spec, explicitly marked living/not-archived in its status line
> (mirroring #42's pattern), with only #49's *plan* doc archived on completion. Rejected: a brand-new
> standalone doc outside `docs/superpowers/` (the `evals/reports/` analogy doesn't transfer — that
> corpus holds generated, immutable scorecards, not authored policy/runbook prose, and inventing a
> new top-level doc location for design/policy artifacts breaks with the repo's one demonstrated
> pattern). Also rejected: folding rollout status into the #42 policy spec itself — #42 is guarded by
> a content-asserting conformance test and is the epic-wide "what the rules are" doc; rollout status
> is mutable operational state that will churn every time a tier advances, and coupling it into a
> tested, stable policy doc invites spurious test churn and muddies two different concerns. Protect
> the path from accidental archival the same way #42 is protected: a README link plus a doc-shape
> test pinning the path.

> **Q3:** Given no skill is actually renamed within #49's own diff, and #44 already built the only
> wrapper, should "add compatibility wrappers if skill names change" require new wrapper code now
> (anticipating #218), and should "preserve existing command messages" get its own new test?
>
> **A3:** No new wrapper code. The criterion is explicitly conditional ("*if* skill names change")
> and the condition is not triggered by this ticket. The runbook documents that #44's existing
> mechanism already satisfies it, states plainly that #49 performs no rename, and names #218 as
> obligated to preserve/extend the wrapper for the renamed paths — converting the sequencing hazard
> into a documented, gate-protected obligation rather than speculative pre-built alias code (which
> would itself be exactly the kind of gate-adjacent scope creep the conformance gate exists to
> catch). "Preserve existing command messages" resolves to documentation-and-verification-only: the
> dispatch strings come from `command:` ids in `.archon/workflows/archon-dark-factory.yaml`, entirely
> orthogonal to internal rubric-path resolution, and this ticket's diff never touches that file. No
> new test is warranted since there is no behavior change to pin; the runbook attributes the
> guarantee to "this ticket introduces no change to `archon-dark-factory.yaml`," not to
> `check_workflow_dag.py` (which checks DAG structural integrity, not dispatch phrasing, and would
> be cited inaccurately).

---

## Architecture / Approach

### 1. This spec doubles as the rollout-status runbook

No separate runbook file is created. This document's own "Rollout Status" table (below) and
"Rollback Steps" section (§3) are the durable, living artifact future tickets read and update in
place. `README.md` gains a link to this spec (parallel to its existing #42 link at line 270), and a
new test (`tests/test_claude_skills_rollout_doc.py`, mirroring
`tests/test_claude_skills_policy_doc.py`'s shape) pins this file's existence and its Rollout Status
table's key claims, so CLAUDE.md's "never archive a doc that tests or README still reference" rule
mechanically protects it from the archive step that runs at ticket completion. When this ticket's
plan is archived, only the plan doc moves to `docs/archive/` — this spec stays at its current path
and gets its status line updated in place by future tiers/tickets rather than superseded by a new
dated file.

### Rollout Status (living — update in place when a tier advances)

| Scenario | Tier | Skill-modularized alternative exists? | Compatibility wrapper | Rollout state |
|---|---|---|---|---|
| conformance | 1 | Yes — `.claude/skills/conformance/RUBRIC.md` (#44) | Clone-live-first, baked-fallback (#44), now runtime-tested (this ticket) | **Advisory-only.** Current production behavior (Archon command reads the resolved rubric text and substitutes it into a manually-spawned subagent) is unchanged; no default-on native Skill-tool invocation. |
| code-review | 1 | Yes — `.claude/skills/code-review/RUBRIC.md` (#44) | Same as above | **Advisory-only**, same terms. |
| refine | 2 | **No** — `.claude/skills/refinement/` holds only `config.yaml`; personas remain baked-only at `/opt/refinement-skills/` | None | **Not rollout-eligible.** Blocked on a skill body existing at all (deferred to #218 + a future skill-build ticket) and on the Tier 1 live A/B spot-check re-running with credentials before any Tier 2 scenario is reconsidered. |
| plan_narrative | 2 | No | None | Not rollout-eligible, same reason. |
| continue | 2 | No | None | Not rollout-eligible, same reason. |

**Standing policy:** no scenario goes default-on or blocking on native Skill-tool invocation. The
scorecard's advisory recommendation (`evals/reports/skill-modularization-scorecard-2026-07-13.md`)
is the factory's official rollout stance until an authenticated Tier 1 live spot-check (the one that
errored on missing `ANTHROPIC_API_KEY` in #48's run) is re-run and produces a stronger signal.
Advancing any row in this table is itself a future ticket's job, not a change made silently by
editing this table without a corresponding evaluation.

### 2. New runtime tests (conformance/code-review only)

Two new test modules (exact filenames decided at plan time, e.g.
`tests/test_conformance_rubric_baked_fallback_runtime.py`,
`tests/test_code_review_rubric_baked_fallback_runtime.py`, or folded into the existing fallback test
files as additional test functions — implementer's choice, since both are equally consistent with
existing naming):

- **Fallback-runtime test:** with the clone-live `RUBRIC.md` made unavailable (e.g. a temp
  directory standing in for the clone, or monkeypatching the resolution helper — exact technique
  decided at plan time), assert the gate's rubric-resolution logic returns the baked
  `/opt/refinement-skills/*-reviewer-prompt.md` content rather than failing, an empty string, or
  silently degrading to a no-op. This directly exercises the behavior the three existing static
  tests never do.
- **Telemetry-labeling test:** call `scripts/context_budget.py`'s skill-prompt resolution
  (`_resolve_skill_prompt` / `_probe_skill_prompts`, or the CLI entrypoint) for the `conformance`
  and `code-review` scenarios under both the clone-live-present and clone-live-absent conditions,
  and assert the `skill_prompts` section is reported as `included` (not `dropped`) with the correct
  `scenario` key in both cases. This satisfies "confirm reports/cost telemetry still identify the
  phase and scenario" as a verification, not a documentation claim — `config/config.yaml`'s
  `token_optimization.budgets`/`enforce` blocks and `context_budget.py --scenario`'s required,
  validated argument already key by scenario name; this test confirms that labeling survives which
  rubric-source path resolved.

No changes to `_SECTION_REGISTRY`, `_SKILL_PROMPT_FILES`, or the resolution logic itself — this is
verification of existing behavior, not new plumbing.

### 3. Rollback steps

Documented here as the durable reference (per Requirement 1):

1. **To force the baked path for conformance or code-review** (e.g. if a clone-live `RUBRIC.md` edit
   is suspect): remove or rename `.claude/skills/conformance/RUBRIC.md` /
   `.claude/skills/code-review/RUBRIC.md` on the target branch. The command's existing
   clone-live-first/baked-fallback resolution (unchanged by this ticket) automatically falls back to
   `/opt/refinement-skills/{conformance,code-review}-reviewer-prompt.md` — no config flag, redeploy,
   or code change is needed; this is why #44 built resolution-by-file-presence rather than a runtime
   toggle.
2. **Baked-copy staleness hazard:** the container's `/opt/refinement-skills/*.md` copies are set at
   `docker compose --profile factory build` time (`Dockerfile:126`,
   `COPY refinement-skills/ /opt/refinement-skills/`) and do **not** update on a normal clone-live
   deploy. A container whose image predates a `refinement-skills/` source change (verified live
   during this refinement pass — the running container's baked `product-owner-prompt.md` and
   `orchestrator-prompt.md` still carried #43's pre-fix MarketHawk-hardcoded content while the
   git-tracked source already had the fix) will serve stale baked content as its fallback. Rollback
   step: rebuild the factory image (`docker compose --profile factory build`) before relying on the
   baked fallback as a rollback path, or verify baked/tracked parity with
   `diff <(git show HEAD:refinement-skills/<file>) /opt/refinement-skills/<file>` before trusting it.
   This ticket does not add automated staleness detection — that is out of scope (Alternatives §3)
   and should be filed as a follow-up if it becomes an operational problem.
3. **To fully undo this ticket:** revert its commits. Since it adds only tests and this doc (no
   production resolution-logic changes), reverting has zero effect on live gate behavior — the
   clone-live/baked-fallback mechanism (#44) is unchanged either way.
4. **Command-message / dispatch compatibility:** unaffected by any of the above — dispatch strings
   live in `.archon/workflows/archon-dark-factory.yaml`, untouched by this ticket or by either
   rollback step.

### 4. Forward obligation for #218

When #218 executes the physical move (`refinement-skills/` → `.claude/skills/refinement/templates/`,
per #42 §2), it must: (a) update the Rollout Status table above wherever paths change, (b) preserve a
clone-live/baked-equivalent fallback for the renamed paths (or explicitly retire the baked path only
once no target clone still needs it, per #44's own deferred-retirement note), and (c) re-run or
update the runtime tests added by this ticket if the paths they reference change.

---

## Alternatives Considered

1. **Build a first skill-modularized alternative for a Tier 2 scenario** (e.g. wire
   `dark-factory-refine.md` to a clone-live orchestrator prompt) so all five scenarios have some
   wrapper. **Rejected** (Q1/A1) — net-new production surface for a high-risk phase the scorecard
   did not clear, exceeds the `size: S` budget, and risks a gate-adjacent behavior change as a side
   effect of a rollout ticket.
2. **Documentation-only, no new tests.** **Rejected** (Q1/A1) — "confirm reports/cost telemetry
   still identify the phase and scenario" is a testable assertion; the existing wrapper is verified
   only by static string-order tests, so a prose-only "it works" claim leaves the criterion
   unbacked.
3. **A new standalone runbook doc outside `docs/superpowers/`, or folded into the #42 policy spec.**
   **Rejected** (Q2/A2) — no repo precedent for a new top-level policy-doc location, and coupling
   mutable rollout state into #42's tested, stable policy doc muddies two distinct concerns and
   invites spurious test churn on an unrelated tier-advancement edit.
4. **Proactively build a second fallback tier or config-alias mechanism anticipating #218's future
   rename.** **Rejected** (Q3/A3) — speculative gate-adjacent complexity for a sequencing risk that
   #218 (still open, not depended on by #49) is better positioned to own directly; a documented
   forward obligation in the living runbook is the cheaper, safer coordination mechanism.
5. **Add a new test pinning the literal `Refine issue #N`-style dispatch strings in
   `archon-dark-factory.yaml`.** Considered as an optional mechanical safeguard (Q3/A3 flagged it as
   "not required"). **Deferred, not adopted** — the requirement is satisfied by the diff's absence of
   any change to that file, which is directly visible in code review; adding a test for a file this
   ticket never touches is unnecessary defensive testing beyond what the acceptance criteria call
   for.

---

## Open Questions (Non-blocking)

- Exact technique for simulating clone-live-RUBRIC.md-absence in the new runtime tests (temp
  directory standing in for the clone root vs. monkeypatching a resolution helper) is left to
  implementation — both are consistent with existing test patterns in this repo and neither changes
  the requirement.
- Whether the two new runtime tests should be new files or added to the existing
  `test_conformance_command_rubric_fallback.py` / `test_code_review_command.py` modules is left to
  implementation; either satisfies Requirement 2/3.
- Re-running #48's Tier 1 live A/B spot-check with valid credentials (to potentially strengthen the
  conformance/code-review recommendation beyond "advisory-only") is explicitly out of scope for this
  ticket and not scheduled by it — it is a prerequisite noted in the Rollout Status table for anyone
  considering advancing a tier, not a task this ticket performs.
- Whether the baked-copy staleness hazard (§3.2) warrants an automated detection/guard (e.g. a smoke
  check comparing tracked vs. baked file hashes) is flagged as a plausible follow-up ticket, not
  adopted here — Q1/A1 and the `size: S` budget both argue for documenting the hazard rather than
  building detection tooling in this ticket.

---

## Assumptions

- **[Flagged]** #218 is assumed to still be the single, canonical owner of the physical
  `refinement-skills/` → `.claude/skills/refinement/templates/` move; if that ticket's scope has
  since changed or it has been closed as won't-fix, the Rollout Status table and Architecture §4's
  forward obligation should be reconciled against #218's actual current state before this spec is
  implemented.
- **[Flagged]** The exact mechanism for "simulating clone-live-file-absence" in the new runtime
  tests is assumed to be straightforward given the existing `_resolve_skill_prompt` /
  clone-live-first-baked-fallback pattern already reads by plain file path (no network/container
  dependency) — if the actual command-level resolution logic (as opposed to
  `context_budget.py`'s probe) turns out to require a live Agent-tool subagent spawn to exercise
  end-to-end, the test should target the resolution *logic* (path selection) rather than a full gate
  run, to stay within `size: S`.
- The scorecard's per-scenario Tier/recommendation values are treated as current and authoritative
  as of `evals/reports/skill-modularization-scorecard-2026-07-13.md`'s generation timestamp
  (2026-07-13T01:00:12Z); if a newer scorecard is generated before this ticket is implemented, the
  Rollout Status table should be reconciled against the newer one.
- No `ARCHITECTURE.md` exists for this self-hosting repo (confirmed this session), consistent with
  prior specs in this family (#43) — this spec relies on `CLAUDE.md`'s repo map and direct
  file/commit inspection instead.
