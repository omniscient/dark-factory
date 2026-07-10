# Refinement/Planning Prompt De-duplication and Playbook Clarification

**Issue:** omniscient/dark-factory#43 — Split refinement and planning prompts into concise phase skills
**Status:** draft — pending review
**Depends on:** omniscient/dark-factory#42 (Claude Skills conventions and safety policy — approved,
`docs/superpowers/specs/2026-07-10-dark-factory-claude-skills-design.md`)
**Parent epic:** Claude Skills prompt-modularization supplement to omniscient/dark-factory#36
**Related:** omniscient/dark-factory#41 (prompt surface inventory — `docs/archive/2026-07-10-dark-factory-prompt-surface-inventory-design.md`), which tagged the rows this spec resolves as "Related issue #43"

---

## Overview / Problem Statement

Dark Factory's refinement pipeline runs on two mechanisms that overlap by design but have drifted
into literal duplication: the Archon phase commands (`commands/dark-factory-refine.md` and
`commands/dark-factory-plan.md`, mirrored to `.archon/commands/`, dispatched live by the DAG every
run) and a bundle of five persona prompts in `refinement-skills/` (baked into the image at
`/opt/refinement-skills/` at Docker build time). Issue #41's inventory found that
`dark-factory-refine.md` and `refinement-skills/orchestrator-prompt.md` "describe the same 4-phase
brainstorm→spec process in similar prose" — both are read by the same agent in the same run, so the
duplication is pure token cost with no compensating benefit, charged against the `refine` scenario's
30,000-token budget (`config/config.yaml: token_optimization.budgets.refine`) on every single
refinement run.

Issue #43 asks to resolve this duplication into "clearer Dark Factory phase playbooks," while
preserving existing scheduler/Archon invocation semantics (`Refine issue #N`, `Plan issue #N`),
existing persona behavior (product-owner, orchestrator, architect, conformance-reviewer), and
consuming compact context-pack artifacts from #36 where available.

This ticket does **not** decide in a vacuum: two sibling issues resolved shortly before this
refinement pass, and this spec builds directly on both rather than re-litigating them:

- **#42** (approved) defines the binding taxonomy: Archon phase commands are structurally not
  Claude Skills (no Skill frontmatter, never model-invoked, dispatched only by `command:` id from
  `.archon/workflows/archon-dark-factory.yaml`) and must not become one. Real Claude Code Skills are
  a separate "reference skill" category; #42 §2 already designed a target consolidated state for the
  refinement prompt bundle (`refinement-skills/` → `.claude/skills/refinement/` with a `templates/`
  subdirectory) but explicitly scoped that physical move out of its own (doc-only) ticket, calling
  it "a follow-up implementation ticket."
- **#41** (informational, archived) inventoried every prompt surface and tagged
  `dark-factory-refine.md`, `dark-factory-plan.md`, and four of the five `refinement-skills/*`
  persona files "Related issue #43," with per-file recommendations this spec adopts directly (see
  Requirements).

---

## Requirements

Distilled from the issue's five acceptance criteria, refined through Q&A (full log below), read
together with #41/#42:

1. **AC "split into skills, or keep a compatible wrapper"** resolves to: **keep the wrapper.**
   Per #42's taxonomy, `dark-factory-refine.md`/`dark-factory-plan.md` cannot be "split into
   skills" — they are Archon commands, a structurally different, non-Skill mechanism. The two
   phase playbooks already exist as those two commands. `refinement-skills/` (trimmed per
   Requirement 2) remains the single shared-persona wrapper feeding both. No new
   `.claude/skills/dark-factory-*/` directories are created — that would collide with #42 §1's
   naming rule (the `dark-factory-<phase>` prefix is reserved for Archon commands; reference
   skills use a bare capability noun).
2. **Collapse the refine-side duplication, asymmetrically (not the plan side).**
   `dark-factory-refine.md` (clone-live, dispatched, holds the real operational detail: skip
   guard, scope boundary, memory rules, publish template, `claude-opus-4-8` model pin) becomes the
   sole canonical owner of the refine procedure. `refinement-skills/orchestrator-prompt.md` shrinks
   to a thin persona stub — its Phase 1–6 process narration and its never-substituted
   `$ISSUE_CONTEXT`/`$FEEDBACK` template section are dropped as redundant/vestigial; its one
   genuinely unique element (the "Focus questions on: purpose/scope/integration/data-model/UI-UX/
   error-handling" bullet list) is migrated into `dark-factory-refine.md`'s Phase 4, since nothing
   else states it today. `dark-factory-plan.md` and `refinement-skills/architect-prompt.md` are
   **not** collapsed — `architect-prompt.md` is a genuinely distinct, subagent-injected reviewer
   persona (not a duplicated procedure), and the plan/conformance Phase-3.5 reconcile-loop
   duplication #41 flagged is #44's scope, not #43's.
3. **Compatibility:** `Refine issue #N` / `Plan issue #N` invocation strings are unaffected — no
   change to Archon dispatch, `command:` ids, or the workflow DAG.
4. **Context-pack consumption (AC#4/#5):** add a presence-check + fallback pattern to both
   commands' Phase 1 LOAD, satisfying "use where available" and "fallback when absent"
   simultaneously — today "where available" is the empty set (see Requirement 6/Assumptions).
5. **Fix a real, adjacent bug while the files are open:** four of the five `refinement-skills/*`
   persona prompts hardcode "MarketHawk" as the product identity
   (`orchestrator-prompt.md:3`, `architect-prompt.md:1,3`, `product-owner-prompt.md:1,3`,
   `conformance-reviewer-prompt.md:1,3`) despite this being the self-hosting Dark Factory instance —
   cross-instance contamination that tells refine/plan subagents they're working on the wrong
   product. Fixed only in the files #43 actually touches (see Requirement 2 and Architecture §3);
   `code-review-reviewer-prompt.md` (only used by `dark-factory-code-review.md`, #44's phase, never
   loaded by refine or plan) is explicitly left alone.
6. **Explicit non-goals**, carried forward from Q&A so a reviewer doesn't read them as scope creep
   or oversight:
   - No physical rename/move of `refinement-skills/` → `.claude/skills/refinement/` (deferred to
     #42 §2's follow-up implementation ticket — confirm or file it, see Open Questions).
   - No `disable-model-invocation`/`allowed-tools` frontmatter added to anything (inert at the
     current baked path; part of the deferred move).
   - No expansion of `refinement-skills/SKILL.md` beyond its current concise Overview/Usage/
     What-It-Does/Configuration/Prompt-Files shape — the issue's own Scope section says "Keep
     `SKILL.md` concise," and #41 separately flagged this exact file as the one most at risk of
     becoming a second dispatch router on top of the Archon DAG's own dispatch.
   - No adoption of the four speculative schemas from later issue-comment suggestions (Factory Role
     Card, loop-engineering fields, SKILL.md section canon, `mechanism_carrier` classification) —
     #42 already deferred these as non-blocking future work for the same reasons (YAGNI for a
     reference-skill population of one); this spec applies that precedent rather than re-deciding
     it.
   - No change to `dark-factory-plan.md`'s Phase 3.5 reconcile-loop content (shared with
     `dark-factory-conformance.md`, #44's scope).

---

## Brainstorming Q&A

> **Q1:** #42's approved policy already designed the target consolidation
> (`refinement-skills/` → `.claude/skills/refinement/` + `templates/`, updating ~10 hardcoded
> `/opt/refinement-skills` references across `Dockerfile`, `entrypoint.sh`, `scheduler.sh`,
> `context_budget.py`, `context_pack.py`, `architecture_slice.py`, `memory_retrieve.py`, and 3 test
> files) and #41 tagged exactly these rows "Related issue #43." Should #43 execute that full
> physical consolidation now, or scope it out and only trim/de-duplicate prose in place?
>
> **A1:** Scope it out. #43 operates in place at `refinement-skills/` → `/opt/refinement-skills/`.
> The physical move stays the dedicated follow-up ticket #42 already anticipated: (a) #42 itself
> called it "a follow-up implementation ticket, not part of this doc-only ticket"; (b) none of
> #43's five ACs require the physical move — the context_pack/context_budget scripts already read
> the persona prompts by their current baked path, so the context-pack ACs are independent of
> directory location; (c) the move alone (directory rename + ~10 reference updates + Docker/CI
> verification) is roughly its own `size: M` and would blow #43's 1–4h budget combined with the
> prose work; (d) bundling a low-risk prose trim with a baked-image→clone-live mechanism migration
> under one conformance-gated diff violates CLAUDE.md's scope discipline ("touch only what the plan
> lists"). Record the deferred move as an explicit non-goal and confirm/file the follow-up ticket.

> **Q2:** Given A1 (operate in place), which file becomes canonical between
> `commands/dark-factory-refine.md` (+ mirrored `.archon/commands/`) and
> `refinement-skills/orchestrator-prompt.md`, and exactly what should each keep/drop? Does the same
> collapse logic apply symmetrically to `dark-factory-plan.md`/`architect-prompt.md`?
>
> **A2:** `dark-factory-refine.md` is canonical — it's the file the Archon DAG actually dispatches
> and reads live from the clone every run, and it already holds all the real operational detail
> (skip guard, scope boundary, `<20`-char pre-flight, memory-write rules with
> `[PROVISIONAL]`/`[INVALID]`/`AVOID` handling, re-run/feedback logic, `direct-to-pr` grace-timer,
> full publish-comment template, and the subagent-invocation spec with the `claude-opus-4-8` model
> pin). `orchestrator-prompt.md` has none of that; it only restates the same six phases in weaker
> prose. Drop its Phase 1–6 narration and Subagent Invocation block (fully redundant) and its
> `$ISSUE_CONTEXT`/`$FEEDBACK` template section (never substituted by anything — vestigial from a
> prior standalone-invocation design). Migrate its one unique element — the "Focus questions on"
> bullet list — into `dark-factory-refine.md` Phase 4, since the command doesn't currently state it.
> Keep the stub file present (do not delete it): `context_budget.py`'s `_SKILL_PROMPT_FILES` and
> `context_pack.py` enumerate this exact filename as a token-budget input, and deleting it is the
> deferred move ticket's job, not this one's. No collapse on the plan side: `architect-prompt.md`
> is a genuinely distinct, subagent-injected single-turn reviewer persona (checklist + fixed output
> format), not a duplicated procedure narration — structurally the opposite of
> `orchestrator-prompt.md`, which the orchestrating agent reads as its *own* process instructions.
> `dark-factory-plan.md`'s only flagged duplication (the Phase 3.5 reconcile loop, shared with
> `dark-factory-conformance.md`) is #44's scope. Noted in passing: `orchestrator-prompt.md` and
> `architect-prompt.md` both hardcode "MarketHawk" — a labeled decision for the spec, not silently
> fixed or silently left.

> **Q3 (three parts):** (A) What is the precise resolution for AC#1 given #42's taxonomy and A1/A2?
> (B) Does the 2026-07-01 context-pack presence-check pattern still hold now that `CLAUDE.md` exists
> but no DAG node produces `context-pack.md` for any scenario? (C) Should the MarketHawk-hardcoded
> wording be fixed in-scope, and should `SKILL.md` gain agent-skills-anatomy sections (When-to-use,
> Common-Rationalizations, Red-Flags)?
>
> **A3:** (A) Confirmed, stated as a decision not a punt: AC#1 resolves to "wrapper retained,"
> explicitly rejecting the "split into skills" branch on #42 §1 grounds (Archon commands cannot be
> Skills), with the deferred `.claude/skills/refinement/templates/` state cited as the future home.
> (B) The presence-check pattern still holds — CLAUDE.md existing only changes what a `## claude_md`
> section would *contain* if ever wired in, not whether the branch is needed. But the spec must
> state plainly that the fallback branch is **100% of live traffic today** (zero `context_pack`
> references anywhere in `.archon/workflows/archon-dark-factory.yaml`, grep-confirmed) and that
> `## architecture_md` is structurally always empty for this repo (no `ARCHITECTURE.md`) — this is
> forward-compatible plumbing satisfying the AC by construction, not a currently-measurable
> behavior change, and should be pinned by a test asserting the fallback path is taken when the
> artifact is absent, so it isn't mistaken for dead code in review. (C1) Fix the MarketHawk wording
> in-scope — #43 already opens `orchestrator-prompt.md` and `architect-prompt.md` for the exact
> lines carrying it; filing a one-line de-hardcoding separately is ceremony that leaves an active
> cross-instance contamination bug live for another ticket cycle. No `${FACTORY_PRODUCT_NAME}`-style
> substitution exists for these statically-read persona files (unlike `$ISSUE_CONTEXT`/`$QUESTION`,
> which the orchestrator does substitute) — introducing that machinery is new mechanism, out of
> scope for a prose-trim ticket; use instance-neutral wording instead. (C2) Keep `SKILL.md` at its
> current concise shape — the issue's own Scope section says so explicitly, #41 already flagged this
> file as the one most at risk of becoming a second dispatch router, and #42 deferred the
> "SKILL.md section canon" as non-adopted YAGNI. The only edit: update the "Prompt Files" list's
> one-line description of `orchestrator-prompt.md` to match its new stub role.

---

## Architecture / Approach

### 1. `commands/dark-factory-refine.md` (+ mirrored `.archon/commands/dark-factory-refine.md`)

**Phase 1 LOAD — add context-pack presence-check** (replaces the current unconditional
`Read CLAUDE.md`/`Read ARCHITECTURE.md` steps 1–2 with a presence-check that reads from the pack
when available, and step 4 to point at the trimmed prompt):

```
1. Check for a pre-assembled context pack: if `$ARTIFACTS_DIR/context-pack.md` exists, read its
   `## claude_md` and `## architecture_md` sections and use them in place of reading the source
   files directly. For any section that is empty or absent from the pack, fall back to reading
   the corresponding source file directly (`CLAUDE.md`, `ARCHITECTURE.md`) at the repo root.
   No DAG node currently produces `context-pack.md` for the `refine` scenario, so this branch
   currently always takes the fallback — this is intentional, forward-compatible plumbing for
   when omniscient/dark-factory#36 wires in a context-pack DAG node, not a currently-exercised
   optimization.
2. (merged into step 1)
3. The issue context has been fetched by the workflow. It is available in the conversation.
4. Read `/opt/refinement-skills/orchestrator-prompt.md` — a short persona stub; your full process
   instructions are Phases 1–6 below (this file), not a separate document.
5. Read `/opt/refinement-skills/product-owner-prompt.md` — you will pass this to subagents
6. Read `/opt/dark-factory/config/config.yaml` for pipeline configuration
7. Compute the affected file set and load memory context: (unchanged)
```

**Phase 4 BRAINSTORMING LOOP — add the migrated focus-questions guidance** (new bullet list
inserted after step 1 "Formulate one clarifying question at a time"):

```
   Focus questions on: purpose and success criteria; scope boundaries (what's in, what's out);
   integration points with existing code; data model decisions; UI/UX requirements (if
   applicable); error handling and edge cases.
```

No other phase of `dark-factory-refine.md` changes.

### 2. `commands/dark-factory-plan.md` (+ mirrored `.archon/commands/dark-factory-plan.md`)

**Phase 1 LOAD — same presence-check pattern, plus the `## spec` section:**

```
1. Check for a pre-assembled context pack: if `$ARTIFACTS_DIR/context-pack.md` exists, read its
   `## claude_md` section in place of reading `CLAUDE.md` directly, and its `## spec` section in
   place of the spec-file discovery glob. For any section empty or absent from the pack, fall
   back to the existing behavior (direct `CLAUDE.md` read; `docs/superpowers/specs/` glob for the
   spec). As with refine, no DAG node currently produces `context-pack.md` for the `plan`
   scenario — this is the same forward-compatible, currently-fallback-only plumbing.
2. (unchanged — architect-prompt.md read)
3-5. (unchanged spec discovery/read, now expressed as the fallback branch of step 1's presence-check)
```

No other phase of `dark-factory-plan.md` changes — Phase 3.5's reconcile loop is untouched
(#44's scope), and no split from `architect-prompt.md` occurs.

### 3. `refinement-skills/orchestrator-prompt.md` — reduced to a thin stub

Before: 82 lines (persona line, six-phase process narration, subagent-invocation spec, unused
`$ISSUE_CONTEXT`/`$FEEDBACK` template).

After (full replacement):

```markdown
# Refinement Orchestrator

You are the refinement orchestrator for Dark Factory's self-hosting and target-repo pipelines.
Your full process is defined by the `dark-factory-refine` phase command
(`.archon/commands/dark-factory-refine.md`) that is currently instructing you — this file exists
only to hold your persona identity and is read as a supporting reference, not a second procedure.

When spawning product-owner subagents to answer clarifying questions, follow the command's Phase 4
instructions exactly (question style, Agent tool invocation, model pin, UNCERTAIN: handling).
```

This also fixes the MarketHawk hardcoding (Requirement 5) by removing the product-specific sentence
entirely rather than substituting a different hardcoded name.

### 4. `refinement-skills/architect-prompt.md` — MarketHawk wording only

Line 1 `# Architect Reviewer — MarketHawk` → `# Architect Reviewer`.
Line 3 `You are an architect reviewing an implementation plan for the MarketHawk stock scanning
platform.` → `You are an architect reviewing an implementation plan for the target codebase.`
No other content changes — the review checklist (spec coverage, file-path consistency, task
decomposition, conventions, memory patterns, no-placeholders) and output format are unaffected.

### 5. `refinement-skills/product-owner-prompt.md` and `refinement-skills/conformance-reviewer-prompt.md` — MarketHawk wording only

Same class of fix as §4, applied narrowly to the product-identity line(s) only:
- `product-owner-prompt.md:1,3` — `# Product Owner — MarketHawk` / "You are the product owner for
  MarketHawk, a full-stack stock scanning platform that identifies pre-market volume spikes and
  unusual trading patterns." → generic product-owner framing with no hardcoded product name or
  domain description (the issue/codebase context passed in at invocation time already supplies the
  real target's domain specifics).
- `conformance-reviewer-prompt.md:1,3` — same treatment. This file is touched because
  `dark-factory-plan.md`'s Phase 3.5 loads it (shared with `dark-factory-conformance.md`, #44), so
  it is legitimately in #43's diff via the plan phase even though its Phase-3.5 *logic* is not
  being changed.

`refinement-skills/code-review-reviewer-prompt.md` is explicitly **not** touched — it is loaded
only by `dark-factory-code-review.md`, a phase neither refine nor plan invokes.

### 6. `refinement-skills/SKILL.md`

One-line edit only: the "Prompt Files" list's description of `orchestrator-prompt.md` changes from
"Persona for the brainstorming orchestrator (adjustable)" to "Persona stub for the brainstorming
orchestrator — full process lives in `dark-factory-refine.md`" so the manifest accurately describes
the trimmed file. No new sections added (Requirement 6).

---

## Alternatives Considered

1. **Execute the full #42 §2 physical consolidation now, bundled with the prose trim.**
   Rejected (Q1/A1) — different risk class (baked-image→clone-live delivery mechanism vs. prompt
   content), no AC requires it, would exceed the `size: M` budget combined with the prose work, and
   was explicitly deferred by #42 itself to its own follow-up ticket.
2. **Make `orchestrator-prompt.md` canonical and reduce `dark-factory-refine.md` to a thin
   dispatcher that just reads it.** Rejected (Q2/A2) — the Archon-dispatched, clone-live file
   already holds all real operational detail (memory rules, scope boundary, publish template,
   model pin); making it defer to a baked, harder-to-edit file would move the source of truth in
   the wrong direction and contradicts #42 §1 (Archon commands own their procedure; reference
   skills are supplementary).
3. **Symmetrically collapse the plan side too** (merge `architect-prompt.md` into
   `dark-factory-plan.md`, or extract the Phase 3.5 reconcile loop into a shared reference file
   now). Rejected (Q2/A2) — `architect-prompt.md` is a distinct subagent-injected persona with no
   duplicated procedure to collapse, and the Phase 3.5 sharing is with `dark-factory-conformance.md`,
   squarely #44's file, not #43's.
4. **Leave the MarketHawk hardcoding for a separate ticket** to keep #43's diff minimal.
   Rejected (Q3/A3-C1) — the exact lines are already open in this diff for the persona-stub and
   canonicalization work; deferring a one-line fix in files already being edited is ceremony that
   leaves a live cross-instance bug for another cycle.
5. **Expand `SKILL.md` with full agent-skills anatomy sections** (When-to-use/not,
   Common-Rationalizations, Red-Flags) per the later issue-comment suggestions. Rejected (Q3/A3-C2)
   — contradicts the issue's own "keep SKILL.md concise" instruction, risks the file becoming a
   second dispatch router per #41's own flag, and duplicates schemas #42 already deferred as YAGNI.

---

## Open Questions (Non-blocking)

- Does the #42 §2 physical-consolidation follow-up ticket (`refinement-skills/` →
  `.claude/skills/refinement/`) already exist as a filed GitHub issue? This spec assumes it should,
  per A1, but does not file it — confirm during implementation and file it if missing, so the
  deferred move isn't silently dropped.
- Should `dark-factory-plan.md`'s Phase 3.5 reconcile-loop extraction (shared with
  `dark-factory-conformance.md`) be scoped explicitly into #44, or does it need its own tracking
  ticket? Not decided here; #41 tagged it "#43, #44" but this spec's Q2/A2 places all of it in #44.
- The exact instance-neutral replacement wording for `product-owner-prompt.md`'s domain-description
  sentence (currently MarketHawk-specific: "identifies pre-market volume spikes and unusual trading
  patterns") is left to implementation — the spec requires it be generic/non-hardcoded but does not
  mandate exact prose.

---

## Assumptions

- **[Flagged]** No `${FACTORY_PRODUCT_NAME}`-style token-substitution mechanism reaches the
  statically-read `refinement-skills/*.md` persona files today (confirmed: only the Archon command
  files' "Posted by" footers use `${FACTORY_PRODUCT_NAME}`, and only `$ISSUE_CONTEXT`/
  `$QA_HISTORY`/`$QUESTION`/`$SPEC_CONTENT`/`$PLAN_CONTENT`/`$MEMORY_CONTEXT` are substituted into
  subagent prompts by the orchestrating command). Building that substitution pipeline is explicitly
  out of scope; the MarketHawk fix uses static, generic wording instead.
- **[Flagged]** The context-pack presence-check (Architecture §1/§2) has zero observable effect on
  current production runs, since no DAG node in `.archon/workflows/archon-dark-factory.yaml`
  produces `context-pack.md` for the `refine` or `plan` scenario as of this writing (grep-confirmed,
  zero references). It is included because AC#4/#5 require the capability to exist, satisfied by
  construction; a test should assert the fallback path is exercised so this isn't later mistaken for
  dead code.
- `CLAUDE.md` at the repo root (added by a just-merged sibling PR) means a `## claude_md`
  context-pack section would be non-empty if ever wired in; `ARCHITECTURE.md` still does not exist
  for this self-hosting repo, so `## architecture_md` remains structurally empty/fallback
  regardless of context-pack wiring.
- `context_budget.py`'s `_SKILL_PROMPT_FILES` list and the token-budget accounting for the `refine`
  scenario will reflect a smaller `orchestrator-prompt.md` after this change (net token reduction,
  not measured precisely here — re-verify via `scripts/token_estimate.py` during implementation).
- The follow-up physical-consolidation ticket referenced in Open Questions is assumed to exist or be
  fileable without conflicting with this spec's in-place approach; if it turns out to already be
  in progress with different file contents than assumed here, reconcile before implementing.
