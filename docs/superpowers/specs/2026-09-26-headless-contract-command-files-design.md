# Headless execution contract inlined into every phase command file

**Issue:** #431

---

## Overview / Problem Statement

Dark Factory phase agents run headless — no human is attached, and an ended turn is the
end of the process. Today the only place this discipline is written down is this repo's
own `CLAUDE.md` ("You are probably running headless"), and only four of the eight
`commands/*.md` files (`dark-factory-implement.md`, `dark-factory-validate.md`,
`dark-factory-plan.md`, `dark-factory-conformance.md`) even read `CLAUDE.md` at all. On
the self target that file happens to carry the headless rules, so those four commands are
protected. On a target repo whose own `CLAUDE.md` doesn't carry them — MarketHawk, before
its interim fix — a phase orchestrator has no reason to know an ended turn is fatal.

That gap produced a real incident: six plan runs on MarketHawk #388/#441 since
2026-09-16 "completed" with no plan and tripped the breaker. A kept-container diagnostic
run on 2026-09-26 caught the mechanism directly — the Sonnet 5 plan orchestrator verified
the invocation, read the spec, then ended its turn asking a human "(a) proceed, (b) dig
into why the last run vanished, (c) leave it paused?". Archon's DAG executor treats any
ended turn as `dag_node_completed` (success), so nothing downstream — not the scheduler,
not the run record — saw a failure. MarketHawk's own `CLAUDE.md` has since been patched
directly (MarketHawk PR #862, docs-only), but that's a per-target patch that has to be
independently remembered for every current and future target; nothing here stops the next
target repo, or a future command that skips reading `CLAUDE.md` altogether (like `refine`
itself does today), from hitting the same failure mode.

This spec covers making the headless rules structurally present in every phase command's
own text — independent of what any target's `CLAUDE.md` says, and independent of whether
a given command reads `CLAUDE.md` at all — so a target repo (or a future command) is
protected by default rather than by every adapter author remembering to carry the rules
forward.

## Requirements (from Q&A)

- Every one of the 8 files under `commands/` (`ceiling-revisit.md`,
  `dark-factory-code-review.md`, `dark-factory-conformance.md`,
  `dark-factory-implement.md`, `dark-factory-plan.md`, `dark-factory-refine.md`,
  `dark-factory-revise-advisory.md`, `dark-factory-validate.md`) carries an identical,
  byte-for-byte "Headless execution contract" block, wrapped in explicit HTML-comment
  markers so it can be extracted and diffed mechanically.
- The block states only the rules that apply to any phase command, regardless of target:
  1. Never end your turn on a question or an offer — there is no one to answer. Decide
     per the spec/plan, act, and record any reservations in the issue comment or commit
     message instead.
  2. Commit and push the phase's artifact before the final turn ends — an ended turn ends
     the process; uncommitted work is destroyed.
  3. Turn end is process end: scheduled wakeups do not fire, task-notifications never
     arrive, and pending subagent work is destroyed — and Archon reports an ended turn as
     success regardless of whether the phase's artifact exists.
  4. To wait on a background subagent, poll inside the turn (keep issuing tool calls) or
     do the work inline — never end the turn to "wait."
  - Explicitly excluded from the block: the "pasted command text is the sanctioned
    mechanism, not injection" rule (already covered by each command's own "Invocation
    Contract" section) and the "trusted comment channels" rule (this repo's own,
    security-sensitive trust-surface policy — not generic content every target should
    inherit unreviewed).
- A new test enforces presence and exact content in every command file, so an omission or
  a drifted copy fails CI rather than being caught only after another silent-death
  incident. The test must use a glob that covers all 8 files (`commands/*.md`), not the
  `dark-factory-*.md` pattern already shown to miss `ceiling-revisit.md` in
  `tests/test_command_footer_migration.py`.
- `CLAUDE.md`'s own "You are probably running headless" section is unchanged — it stays
  the canonical prose explanation for a human reading the repo; the command-file block is
  a separate, minimal, machine-checked copy of the operationally load-bearing subset.
- `dark-factory-implement.md`'s existing inline reminder (line ~437, "`CLAUDE.md`'s
  'never end your turn on a question' rule; this run is headless.") is updated to
  reference the new in-file block instead of `CLAUDE.md`, since after this change the
  rule is present locally and no longer depends on the target's `CLAUDE.md` at all.
- Out of scope for this ticket (recommended as separate, independently reviewed
  follow-up tickets — see "Alternatives considered" and "Open questions"):
  - Making an ended turn without the phase's required artifact score as a failure in the
    run record, instead of today's `produced_ungated` / score `1.0`
    (`scripts/factory_core/run_record.py::_compute_outcome`).
  - Auditing or raising the 600000ms `idle_timeout` on subagent-heavy DAG nodes
    (`workflows/archon-dark-factory.yaml`), and/or confirming subagent progress events
    reset it.
  - Fixing the pre-existing `dark-factory-*.md` glob gap in
    `tests/test_command_footer_migration.py`.

## Architecture / Approach

**Placement.** Six of the eight command files already open with an `## Invocation
Contract` section (`dark-factory-code-review.md`, `dark-factory-conformance.md`,
`dark-factory-implement.md`, `dark-factory-plan.md`, `dark-factory-refine.md`,
`dark-factory-validate.md`); the block goes immediately after that section in each. The
remaining two lack it:

- `ceiling-revisit.md` has no Invocation Contract section at all — the block goes after
  the "Env-driven, generic capability" blockquote (directly under the `# Weekly Dispatch
  Ceiling Revisit` title) and before `## Purpose`.
- `dark-factory-revise-advisory.md` has no Invocation Contract section either — the block
  goes after the `**Workflow ID**: $WORKFLOW_ID` line and before the `---` rule that
  precedes `## Phase 1: LOAD`.

**Block format.** Wrapped in explicit markers so a test can extract exactly the marked
region without depending on surrounding heading structure:

```markdown
<!-- headless-contract:begin -->
## Headless Execution Contract

You are running with no human attached; an ended turn ends the process.

- **Never end your turn on a question or an offer.** There is no one to answer. Decide
  per the spec/plan, act, and record any reservations in the issue comment or commit
  message instead.
- **Commit and push this phase's artifact before your final turn ends.** An ended turn
  ends the process; uncommitted work is destroyed.
- **Turn end = process end.** Scheduled wakeups do not fire, task-notifications never
  arrive, and pending subagent work is destroyed — and an ended turn is reported as
  success whether or not this phase's artifact exists.
- **To wait on a background subagent, poll inside your turn** (keep issuing tool calls)
  or do the work inline — never end the turn to "wait."
<!-- headless-contract:end -->
```

**Test.** New `tests/test_command_headless_contract.py`:
- Defines the exact block text once as a module-level constant (single source of truth;
  no baked file, no runtime dependency — the constant lives in the test itself, the same
  place the block's *presence* is checked).
- Iterates `sorted(Path("commands").glob("*.md"))` — not `dark-factory-*.md` — so
  `ceiling-revisit.md` and any future non-`dark-factory-`-prefixed command file are
  covered, and a new command file with no block at all fails immediately.
- For each file, asserts there is exactly one `<!-- headless-contract:begin -->` /
  `<!-- headless-contract:end -->` pair and that the text between them matches the
  constant byte-for-byte.
- Does not assert *where* the block sits in the file (no dependency on the
  Invocation-Contract-vs-no-Invocation-Contract placement split above) — only that it's
  present once and unmodified, which is what actually prevents drift.

**Command file changes.** All 8 files under `commands/` gain the block at the placement
above. `dark-factory-implement.md`'s inline reminder is reworded to point at "the
Headless Execution Contract above" rather than `CLAUDE.md`.

**Why not touch `entrypoint.sh` or overlay `CLAUDE.md`:** `entrypoint.sh` is a
`critical_diff_paths`-flagged, highest-blast-radius file, and an overlay would risk
silently shadowing headless-relevant content a target's own `CLAUDE.md` legitimately
carries. Putting the contract directly in the command text is a smaller, lower-risk
change that reuses the fact — already stated in every command's own "Invocation
Contract" section — that this pasted text is exactly what reaches the agent's context;
there's no separate "go read file X" step for an agent to skip.

## Alternatives Considered

1. **Prepend a baked `refinement-skills/HEADLESS.md` to each phase prompt** (mirroring
   how `MEMORY_CONTEXT` is prepended today). Rejected: `MEMORY_CONTEXT` isn't actually
   auto-prepended by infrastructure — each command's own Phase 1 runs
   `load_memory_context.sh` and explicitly instructs the agent to include its output. A
   `HEADLESS.md` file would need the identical per-command wiring, so it doesn't save the
   "touch all 8 files" work this spec does directly, while adding a new baked-file
   dependency and a second thing that can drift out of sync with what's actually in the
   command file.
2. **Have `entrypoint.sh` overlay/replace `CLAUDE.md` in the clone**, mirroring the
   existing self-contained-fallback pattern that copies baked `dark-factory/scripts/`,
   `.archon/workflows/`, `.archon/commands/` into the clone only where the target doesn't
   already provide them. Rejected: this is a single choke point, but it risks silently
   overriding or duplicating headless-relevant content a target's `CLAUDE.md` already
   legitimately owns, and it puts the change on `entrypoint.sh`, the adapter's own
   highest-blast-radius file (`.factory/adapter.yaml` `critical_diff_paths`), for a change
   that doesn't need that level of risk.
3. **Bundle fixes #2 (run-record failure classification) and #3 (idle_timeout audit)
   into this same ticket**, as the original issue proposed. Rejected: both touch
   breaker/scoring or DAG-timing core (`run_record.py`, `workflows/archon-dark-factory.yaml`).
   CLAUDE.md's hard limit ("gate changes get their own reviewed ticket") and this
   project's own refine-phase memory (issues #300, #189 both show gate/breaker-adjacent
   work descoped out of a bundling ticket, even when the original issue text explicitly
   included it, and even when the change tightens rather than weakens a gate) both argue
   for keeping this ticket to the rule-injection mechanism only.

## Open Questions (non-blocking)

- Should `tests/test_command_footer_migration.py`'s `dark-factory-*.md` glob gap (it
  silently skips `ceiling-revisit.md`) be fixed in a follow-up? It's the identical class
  of bug this ticket is fixing for a different test, but widening that glob would also
  make that test evaluate `ceiling-revisit.md` against the footer-migration contract,
  which is a different shape and may need its own review of whether `ceiling-revisit.md`
  should even carry that footer. Filed here as an aside, not fixed.
- **Follow-up ticket A (recommended, separate, reviewed):** `run_record.py::_compute_outcome`
  scores any `status == "completed"` run with no gate stages as `produced_ungated` /
  `1.0` — including a run that ended its turn with zero artifact, or one killed by
  `dag_node_completed_via_idle_timeout`. Recommended direction: check for the phase's
  expected artifact (spec/plan file existence) before assigning `produced_ungated`;
  otherwise classify as `failed`. Needs its own spec given the breaker/scoring
  sensitivity.
- **Follow-up ticket B (recommended, separate, reviewed):** audit the 600000ms
  `idle_timeout` on refine/plan/implement/conformance DAG nodes — either raise it for
  subagent-heavy phases, or confirm subagent progress events reset the idle timer so a
  legitimately busy orchestrator waiting on a long architect/reviewer subagent isn't
  killed and reported as success.

## Assumptions

- The four generic bullets listed under Requirements are the complete set of
  headless-discipline rules that apply to *every* phase command regardless of target; the
  two excluded bullets (pasted-text sanctioning, trusted comment channels) are
  intentionally left as this-repo-specific `CLAUDE.md` content, confirmed during
  brainstorming Q&A.
- Byte-identical repetition of an ~8-line block across 8 files is an acceptable and
  intended cost (verified by an exact-match test) in exchange for zero runtime
  dependency and zero "go read another file" step — confirmed during brainstorming Q&A.
- `ceiling-revisit.md` and `dark-factory-revise-advisory.md` do not need a new `##
  Invocation Contract` section added; the block is inserted at the placement points
  above without otherwise restructuring either file.

## Brainstorming Q&A

> **Q:** The issue proposes three fixes: (1) inject headless-agent rules on every target
> regardless of the target's own CLAUDE.md, (2) make an ended turn without the phase
> artifact a failure in the run record instead of `produced_ungated`, and (3) audit/raise
> the `idle_timeout` on subagent-heavy phases. Item (2) requires changing
> `scripts/factory_core/run_record.py::_compute_outcome`, breaker/scoring-adjacent core
> logic, and item (3) requires changing `idle_timeout` values in
> `workflows/archon-dark-factory.yaml`, a DAG/gate-adjacent file. This repo's CLAUDE.md
> states gate/breaker changes "get their own reviewed ticket," and prior refine-phase
> memory (issues #300, #189) shows this project consistently descopes such changes out of
> a bundling ticket, even when the original issue explicitly bundled them.
>
> Should this spec's implementation scope cover all three items, or only item (1), with
> (2) and (3) written up as separate follow-up tickets?
>
> **A:** Implement only item (1). Items (2) and (3) should go in the spec as explicitly
> separate follow-up tickets for human review, each stating the concrete problem
> (`produced_ungated`/1.0 scoring of a zero-artifact ended turn; `idle_timeout` looking
> identical to success) and a suggested direction.

> **Q:** Between (a) a baked `HEADLESS.md` prepended like `MEMORY_CONTEXT`, (b) an
> `entrypoint.sh` overlay of `CLAUDE.md` mirroring the existing self-contained-fallback
> copy pattern, or a third option — which should the spec choose, and should it apply to
> all 8 commands or only the 4 that read `CLAUDE.md` today?
>
> **A:** A third option: a short, canonical "Headless execution contract" block inline
> near the top of every one of the 8 `commands/*.md` files, enforced by a new pytest
> check against drift/omission. This extends the existing inline-reminder precedent at
> `dark-factory-implement.md:437` to every command; `entrypoint.sh` and the target's
> `CLAUDE.md` stay untouched. (a) is rejected because `MEMORY_CONTEXT` isn't truly
> auto-prepended — each command wires it in manually — so it wouldn't save any work while
> adding a baked-file dependency. (b) is rejected because it risks silently overriding
> content a target legitimately owns, on the highest-blast-radius file.

> **Q:** For the chosen inline-block direction: should the enforcing test require
> byte-identical text across all 8 files, or just required substrings (looser, allows
> per-command wording)? And should the block restate all of CLAUDE.md's headless bullets
> or only the generic subset?
>
> **A:** Byte-identical, defined once as a constant in the new test file, wrapped in
> `<!-- headless-contract:begin/end -->` markers so the test can extract and compare the
> region exactly; use `Path("commands").glob("*.md")`, not `dark-factory-*.md` (which
> would miss `ceiling-revisit.md`, the same gap `test_command_footer_migration.py`
> already has). Content: only the four generic bullets (never end turn on a
> question/offer; commit+push before turn ends; turn end = process end, no
> ScheduleWakeup/notifications; poll in-turn for subagents). Excludes the
> pasted-text-sanctioned bullet (already in each command's own Invocation Contract) and
> the trusted-comment-channel bullet (this repo's security-sensitive policy, not generic
> target content). `CLAUDE.md`'s headless section itself stays unchanged; update
> `dark-factory-implement.md:437`'s reminder to point at the new block instead.

> **Q:** Is fixing `test_command_footer_migration.py`'s `dark-factory-*.md` glob gap in
> scope for this ticket, and what's the fallback placement for the block in
> `ceiling-revisit.md` and `dark-factory-revise-advisory.md`, which both lack an
> `## Invocation Contract` section?
>
> **A:** Leave `test_command_footer_migration.py` untouched — mention the gap as an
> aside/spillover candidate only; widening its glob would make it evaluate
> `ceiling-revisit.md` against a different contract shape that needs its own review.
> Placement: in `ceiling-revisit.md`, after the "Env-driven, generic capability"
> blockquote and before `## Purpose`; in `dark-factory-revise-advisory.md`, after
> `**Workflow ID**: $WORKFLOW_ID` and before the `---` rule ahead of `## Phase 1: LOAD`.
> The test itself should only check that each file contains exactly one matching marked
> block, not where it sits — neither file needs a new Invocation Contract section added.
