# Dark Factory Claude Skills Conventions and Safety Policy

**Issue:** omniscient/dark-factory#42
**Status:** draft — pending review
**Depends on:** omniscient/dark-factory#41 (Inventory Dark Factory prompt surface — OPEN;
see [Inventory Basis](#inventory-basis) below, which substitutes for it in this pass)
**Parent epic:** Claude Skills prompt-modularization supplement to omniscient/dark-factory#36

---

## Overview / Problem Statement

Dark Factory has no documented convention for how Claude Code Skills should be used inside
the factory. In practice, the factory today runs almost entirely on a different mechanism —
**Archon commands** (`commands/*.md`, mirrored to `.archon/commands/*.md`, dispatched by a
DAG in `.archon/workflows/archon-dark-factory.yaml`) — and the one place that nominally uses
the `.claude/skills/` path (`.claude/skills/refinement/config.yaml`) has no `SKILL.md` and is
therefore not a discoverable Claude Code Skill at all. Prompt/persona files for the refinement
pipeline live in a separate `refinement-skills/` directory that is baked into the Docker image
at build time, decoupled from the clone-live config file that logically belongs beside it.

This creates two problems the issue asks us to resolve:

1. **No safety policy exists** for the case where Dark Factory *does* start using real,
   model-invoked Claude Skills — in particular no rule preventing a side-effecting skill
   (one that could merge, close, deploy, or otherwise mutate shared state) from being
   auto-triggered, and no limit on how broad a skill's tool grant can be.
2. **No naming/layout/injection convention** exists, so any future skill work has nothing to
   conform to, and the acceptance criteria's ban on raw-dump context injection has no place
   to be codified as a rule (even though the *scripts* that avoid raw dumps already exist and
   are already mandatory in practice via `token_optimization` enforcement).

This document defines both: the taxonomy and safety rules for Dark Factory's use of Claude
Code Skills, and the concrete consolidation this repo needs to comply with them.

This ticket produces the **policy document only**. The consolidation actions it recommends
(renaming `refinement-skills/` → `.claude/skills/refinement/`, adding `allowed-tools`
frontmatter, updating `.factory/adapter.yaml` exclusion lists) are called out explicitly as
**follow-up implementation tickets** under the parent epic, not implemented here — consistent
with this being a `size: M` (1-4h), `documentation` ticket and the parent epic's requirement
to preserve existing behavior.

---

## Inventory Basis

Issue #42 declares `Depends on: #41` (an inventory of the current prompt/procedure surface),
which is still open. There is no GitHub-native `blocked-by` relationship recorded, and nothing
in `scheduler.sh` / `factory_core/board.py` / `factory_core/deconflict.py` enforces text-only
"Depends on" mentions — the only machine-enforced block state is `STATUS_BLOCKED`, set by
lease/deconfliction conflicts, unrelated to this dependency note. This refinement pass proceeds
on that basis, reconstructing #41's scope inline so the decision is auditable. **#41 should
still be formally closed** with its own standalone inventory doc; if that formal pass surfaces
a prompt/procedure source missed here, the injection/tool-limit rules below should be revisited.

Verified prompt/procedure surface, as of this pass:

| Surface | Location | Live or baked? |
|---|---|---|
| Archon workflow DAG | `.archon/workflows/archon-dark-factory.yaml` | Clone-live |
| Phase commands (refine, plan, implement, conformance, code-review, validate, revise-advisory) | `commands/*.md` → mirrored `.archon/commands/*.md` | Clone-live |
| Maintenance command | `commands/ceiling-revisit.md` | Clone-live |
| Refinement pipeline personas (orchestrator, product-owner, architect, conformance-reviewer, code-review-reviewer prompts + `SKILL.md`) | `refinement-skills/` → baked to `/opt/refinement-skills/` | **Baked** (requires `docker compose --profile factory build`) |
| Refinement pipeline tunables | `.claude/skills/refinement/config.yaml` | Clone-live (already migrated ahead of its sibling prompts) |
| Inline prompt fragments / scenario routing | `entrypoint.sh` | Baked |
| Compact-artifact / context scripts | `dark-factory/scripts/{context_pack,context_budget,architecture_slice,comment_digest,diff_rank,memory_retrieve}.py` | Clone-live, self-contained-fallback-copied |
| Memory | `.archon/memory/*.md`, contract in `docs/dark-factory-memory-contract.md` | Clone-live |
| Self-target adapter | `.factory/adapter.yaml`, `config/config.yaml` | Clone-live |

This repo has **zero** real Claude Code Skills today (no `.claude/skills/<name>/SKILL.md`
exists), and no `CLAUDE.md`/`ARCHITECTURE.md` (those are target-repo concerns; Dark Factory is
product-agnostic post-extraction — see `docs/cutover-markethawk.md`, `README.md`).

---

## Requirements

Distilled from the issue's nine required policy areas and five acceptance criteria, refined
through Q&A (full log below):

1. Define a **phase command vs. reference skill** taxonomy that matches how the factory
   actually dispatches work today, without changing dispatch behavior.
2. **Structurally guarantee** that implement/merge/close/deploy-like actions cannot be
   model-auto-triggered — not just by convention, but by the dispatch mechanism itself.
3. Define **naming and directory layout** for reference skills, including the
   templates/references/scripts/assets graduation rule.
4. Define **`disable-model-invocation`** and **`user-invocable`** rules per category.
5. Define **`allowed-tools`** granularity that structurally bans `Bash(*)`-style grants, tiered
   by side-effect level, and state whether `disallowed-tools` is used.
6. Codify that **dynamic context injection must use the existing compact-artifact scripts**,
   not raw `cat`/`git diff`/comment dumps — this is a documentation requirement, since the
   scripts and their enforcement already exist in `token_optimization`.
7. Define **review expectations** for `.claude/skills/**`, `.claude/settings.json`, hooks, and
   plugin config, including a follow-up to close the gap in
   `.factory/adapter.yaml`'s `safety.hard_exclude_paths`.
8. Define **evaluation/rollout tiers** appropriate to what `bench/run_suite.sh` actually
   covers (implement-phase DAG replay), rather than mandating it universally.
9. Explicitly **scope out** speculative schemas proposed in later issue comments, recording
   them as non-blocking future work rather than silently adopting or silently dropping them.

---

## Brainstorming Q&A

> **Q1:** Issue #42 depends on #41 (still open). Should this refinement proceed using an
> inline-reconstructed inventory, or abstain until #41 formally lands?
>
> **A1:** Proceed. The dependency is a text-only hint with no GH-native blocking relationship
> and no enforcement in `scheduler.sh`/`board.py`/`deconflict.py`. The inline inventory
> (Archon commands vs. baked `refinement-skills/` vs. clone-live `config.yaml` vs.
> compact-artifact scripts vs. memory contract) reconstructs #41's declared scope against real
> files. Bake an "Inventory Basis" section into the spec so #41 can later be formally closed
> against it; if a formal pass finds something this one missed, revisit the injection/tool rules.

> **Q2:** Archon commands (deterministic, externally-dispatched DAG steps) and Claude Code
> Skills (model-invoked, description-matched) are fundamentally different mechanisms. What
> should the policy mandate for the seven phase commands + `ceiling-revisit`, given the parent
> epic requires preserving scheduler/DAG/gate semantics?
>
> **A2:** They stay exactly as they are — Archon commands, dispatched by `command:` id from
> `.archon/workflows/archon-dark-factory.yaml`, explicitly declared **not** Claude Code Skills
> and never model-invoked. This is not a compromise; it *is* the enforcement mechanism for
> "implement/merge/close/deploy must not auto-trigger" — Archon commands are structurally
> incapable of model-invocation, so the ban holds by construction rather than by a frontmatter
> flag that could be misconfigured. Real `.claude/skills/<name>/SKILL.md` becomes a genuinely
> separate **reference skill** category (shared personas, lookup docs, compact-injection
> scripts); any reference skill wrapping a side-effecting action must set
> `disable-model-invocation: true`. Zero behavioral change to the seven commands; only a doc-level
> category label plus hygiene rules applied by analogy.

> **Q3:** Given the reference-skill category from A2, and that compact-artifact scripts are
> cross-phase shared utilities, what naming/directory-layout convention should apply?
>
> **A3:** Consolidate `refinement-skills/` (today baked to `/opt/refinement-skills/` at Docker
> build time) into `.claude/skills/refinement/` as one real skill with
> `disable-model-invocation: true`. This finishes a migration that's already half-done —
> `config.yaml` already lives at `.claude/skills/refinement/config.yaml` and `entrypoint.sh` /
> `effective_config.py` already prefer it over the baked copy; the five persona prompt files
> are the only pieces still stranded in the baked location. The five prompts graduate into a
> `templates/` subdirectory (rule: **a supporting-file kind graduates into its own subdir at
> ≥3 files, or when ≥2 distinct kinds coexist**); `config.yaml` stays flat at the skill root
> since its path is hardcoded in ~15 places. Cross-phase compact-artifact scripts
> (`context_pack.py`, `architecture_slice.py`, `comment_digest.py`, `diff_rank.py`,
> `memory_retrieve.py`, `context_budget.py`) **stay top-level** in `dark-factory/scripts/`,
> referenced by path — a skill's `scripts/` subdir is for skill-*private* helpers only; making
> these cross-phase scripts "owned" by one skill would misrepresent their contract and break
> the hardcoded `dark-factory/scripts/…` references used throughout the DAG and every command.
> Naming: Archon commands keep the `dark-factory-<phase>` prefix; reference skills use a bare
> capability noun (`refinement`, not `dark-factory-refinement`) to keep the category boundary
> visible in the directory listing itself.

> **Q4:** What granularity should `allowed-tools`/`disallowed-tools` enumeration require to
> satisfy the ban on broad `Bash(*)`, and does it apply retroactively to existing commands?
>
> **A4:** Tier the rule to the skill's side-effect level, mirroring the existing docker-socket-proxy
> precedent in this codebase (`.archon/memory/dark-factory-ops.md`: exact verbs per consumer,
> never the whole API surface). Read-only skills get `Read, Grep, Glob` and, if shelling out,
> exact read verbs (`Bash(git diff:*)`). Anything posting to GitHub gets exact subcommands
> (`Bash(gh issue comment:*)`, never `Bash(gh:*)` — a family wildcard would silently include
> `gh pr merge`/`gh release create`, exactly what must stay gated). Filesystem/script execution
> is scoped to the script path (`Bash(python3 scripts/*.py:*)`), never the bare interpreter.
> Bare `Bash(*)` is banned unconditionally. This applies **prospectively only** — the seven
> Archon phase commands are exempt because they carry no Skill frontmatter at all (per A2) and
> retrofitting an allowlist onto their real, already-broad tool usage would be a behavior
> change the parent epic's preservation constraint forbids; that migration belongs to #41's
> formal follow-through, not this ticket. Use **`allowed-tools` only** — Claude Code's
> allow-listing is already deny-by-default, so `disallowed-tools` is redundant and, worse,
> invites the exact anti-pattern being banned (`allowed-tools: Bash(*)` + a blocklist).

> **Q5:** Should the policy adopt any of the four supplementary schemas proposed in later issue
> comments (Factory Role Card, loop-engineering fields, SKILL.md section canon,
> `mechanism_carrier` classification) as mandatory now?
>
> **A5:** No. Record all four in an Open Questions / Future Work section, non-blocking,
> explicitly out of scope for this ticket. None appear in #42's own acceptance criteria; they
> arrived as post-hoc comments from the same planning agent after the first refinement pass had
> already posted a spec. Acceptance criteria define "done" for this ticket; comments are inputs
> to weigh, not requirements to satisfy by default. Mandating a 15-field Role Card or a
> mechanism-carrier taxonomy for a reference-skill population of exactly one
> (`refinement`) fails YAGNI, which the pipeline's own approach-selection phase already treats
> as a hard filter. `side_effect_level` as a concept is already handled concretely by the A4
> tool-tiering; a separate mandatory field would create a second, redundant source of truth.

> **Q6:** Given this ticket produces a policy doc only (not the A2-A4 consolidation), what
> should it require for evaluation/rollout, and for review expectations on
> `.claude/skills/**` / `.claude/settings.json` / hooks / plugin config?
>
> **A6:** Tier evaluation by what's actually changing: pure doc/prompt reorganization relies on
> the gates that already run on every factory PR (`conformance:`, `code_review:` in
> `config.yaml`) — no bench run. Structural moves (the A3 consolidation itself) get a targeted
> smoke check (`smoke_gate.sh` + a refine/plan dry-run on a scratch issue), because the risk is
> "the harness can't find the skill/config," which a full bench sweep doesn't target and which
> costs real money per `docs/parity-p2.md`'s cost accounting. A behavior change to a phase
> command, or any new side-effecting reference skill, requires explicit human sign-off, with a
> bench parity run added only when the change plausibly touches implement-phase DAG behavior
> (the only population `bench/suite.json`'s oracles actually cover). For review expectations:
> the operative fail-closed gate for factory-self paths is `.factory/adapter.yaml`'s
> `safety.hard_exclude_paths` (read by `epic_autopilot.py`), not `config.yaml`'s
> `epic_autopilot.hard_exclude_paths` (a legacy fallback) — and `.claude/skills/**`,
> `.claude/settings.json`, and `.factory/hooks/**` are conspicuously absent from it today.
> Add them there (as a follow-up) for the human-required gate, and separately to
> `safety.critical_diff_paths` (consumed by `diff_rank.py`) so these diffs surface first in
> code-review rather than risk truncation — a review-visibility measure, not a blocking gate.

---

## Architecture / Policy

### 1. Taxonomy: Phase Commands vs. Reference Skills

Dark Factory has exactly two categories of "thing Claude executes," and they must not be
conflated:

| | **Phase commands** | **Reference skills** |
|---|---|---|
| Location | `commands/*.md` → `.archon/commands/*.md` | `.claude/skills/<name>/SKILL.md` |
| Dispatch | `command:` id in `.archon/workflows/archon-dark-factory.yaml`, externally triggered by the scheduler/DAG | Claude's own model-invoked skill router, matched on `description` (when not disabled) |
| Can be model-auto-triggered? | **No — structurally incapable.** No Claude Skill frontmatter exists on these files; nothing routes them through skill discovery. | Only if `disable-model-invocation` is unset/false. |
| Frontmatter today | `description`, `argument-hint` only | N/A — none exist yet |
| Examples | `dark-factory-refine`, `-plan`, `-implement`, `-conformance`, `-code-review`, `-validate`, `-revise-advisory`, `ceiling-revisit` | `refinement` (post-consolidation, see §2) |
| Change cadence | Clone-live; a `main` commit takes effect on the next factory run | Clone-live once consolidated (see §2); currently split across a clone-live config file and a build-time-baked prompt bundle |

**Rule:** implement/merge/close/deploy-like actions live in the phase-command category, full
stop. This is not a policy that must be separately enforced — it falls out of the dispatch
architecture itself, which is why it satisfies the acceptance criterion structurally rather
than by convention alone. A reference skill must never be given the responsibility a phase
command already owns; if a future skill *would* need merge/deploy capability, that is itself
the signal it must be a phase command (or explicitly justified and locked behind
`disable-model-invocation: true` plus human sign-off, per §4).

### 2. Reference Skill Naming, Layout, and the `refinement` Consolidation

**Naming:** a bare capability noun matching both `name:` in frontmatter and the directory name
(`refinement`, not `dark-factory-refinement`). Do not reuse the `dark-factory-<phase>` prefix
on skills — that prefix is reserved for Archon commands and the distinction must be visible in
the directory listing itself.

**Supporting-file layout and graduation rule:**

```
.claude/skills/<name>/
  SKILL.md              # required
  config.yaml            # optional — flat at root if a single canonical tunable file
  templates/             # prompt/document templates — graduate here at >= 3 files
  references/            # docs read on demand, must NOT auto-load into every invocation
  scripts/                # skill-PRIVATE helpers only — never cross-phase shared scripts
  assets/                 # non-text files
```

Graduation rule: **a supporting-file kind moves from flat-sibling into its own subdirectory
once it reaches 3 files of that kind, or once 2 or more distinct kinds coexist in the skill
directory.** Below that threshold, flat siblings are acceptable.

Cross-phase compact-artifact scripts (`dark-factory/scripts/*.py`) are never a skill's private
`scripts/`, regardless of this rule — they are referenced by path from any command or skill
that needs them, per §5.

**Concrete target state for the one skill this ticket's inventory identifies today:**

```
.claude/skills/refinement/
  SKILL.md                          # gains: disable-model-invocation: true
  config.yaml                       # unchanged location — path hardcoded in ~15 call sites
  templates/
    orchestrator-prompt.md
    product-owner-prompt.md
    architect-prompt.md
    conformance-reviewer-prompt.md
    code-review-reviewer-prompt.md
```

This retires the build-time-baked `refinement-skills/` → `/opt/refinement-skills/` path (which
today requires a full `docker compose --profile factory build` to pick up a one-line prompt
edit) in favor of the clone-live pattern `config.yaml` already uses. **This is a real,
deliberate behavior change** — persona prompts become editable per-target-clone without an
image rebuild — scoped to the reference-skill category only; it does not touch the phase
commands' "preserve existing behavior" guarantee from the parent epic. Implementing this move
(updating `Dockerfile`, the half-dozen `/opt/refinement-skills/<x>` references across command
files, `context_budget.py`'s `_SKILL_PROMPT_DIR`, and `test_code_review_prompt.py`) is a
**follow-up implementation ticket**, not part of this doc-only ticket.

### 3. `disable-model-invocation` and `user-invocable`

- **Phase commands:** N/A — they carry no Claude Skill frontmatter and are not subject to
  these fields at all (§1).
- **Reference skills that wrap any side-effecting action** (anything that posts to GitHub,
  writes outside its own skill directory, or shells out to mutate repo/PR state):
  `disable-model-invocation: true` is **mandatory by default**. This does not make the skill
  inert — it is still loadable and its supporting files still readable by whatever explicitly
  invokes it (an Archon command, a human) — it only removes Claude's own model-driven
  discovery/auto-trigger path.
  **Exception path:** a side-effecting reference skill may leave model-invocation enabled only
  with an explicit, written justification — a `# justification:` comment immediately above
  `disable-model-invocation` in the skill's frontmatter, naming why auto-trigger is safe for
  this specific action (e.g. the action is itself read-only-equivalent, or is already
  gated by a downstream human-approval step) — **and** Tier 2 human sign-off (§8) on the PR
  that introduces it. Absent both, `disable-model-invocation: true` is the default and no
  exception applies. Implement/merge/close/deploy-like actions specifically are expected to
  never clear this bar in practice, since §1 already places that responsibility structurally
  in the phase-command category; the exception path exists for narrower judgment calls (e.g.
  a skill that posts a read-only status comment), not as a general escape hatch.
- **`user-invocable`:** default `true` for reference skills — a human should be able to
  explicitly invoke a reference skill for local development or debugging (this already matches
  documented practice: `refinement-skills/SKILL.md` describes a manual `Refine issue #<number>`
  invocation path). Set `user-invocable: false` only for a skill that has no legitimate
  standalone/manual use case (e.g. a pure internal-lookup skill meant only to be read by
  another skill's prompt, never invoked directly).
- **Read-only reference skills** (pure lookup/reference, no side effects): `disable-model-invocation`
  may be omitted (default false / model-invokable), since there is no merge/deploy/mutation risk
  to gate.

### 4. `allowed-tools` / `disallowed-tools`

Applies to the reference-skill category only (§1 — phase commands have no Skill frontmatter to
attach this to; retrofitting them is explicitly out of scope per A4 and the preservation
constraint).

**Rule: enumerate exact tools/subcommands, tiered by side-effect level. Bare `Bash(*)` is
banned unconditionally, for every skill.**

| Side-effect level | Allowed pattern | Example |
|---|---|---|
| Read-only / exploration | Native `Read`, `Grep`, `Glob`; if shelling out, exact read verbs only | `Bash(git diff:*)`, `Bash(git rev-parse:*)` |
| Posts to GitHub | Exact subcommand, never the bare CLI | `Bash(gh issue comment:*)`, `Bash(gh issue edit:*)` — never `Bash(gh:*)` |
| Runs a script | Scoped to the script path, never the bare interpreter | `Bash(python3 scripts/*.py:*)` — never `Bash(python3:*)` |

- Family-level wildcards (`Bash(git:*)`, `Bash(gh:*)`) are **not** acceptable above the
  read-only tier — a family grant silently includes destructive/side-effecting subcommands
  (`gh pr merge`, `gh release create`) that must stay behind the phase-command boundary (§1).
- Use **`allowed-tools` only.** Do not add `disallowed-tools` — Claude Code's allow-listing is
  deny-by-default, so a companion blocklist is redundant at best and, at worst, enables the
  exact anti-pattern this rule bans (`allowed-tools: Bash(*)` narrowed by a `disallowed-tools`
  blocklist that a later edit can silently widen around).
- This is CI-checkable: a lint step (parallel to the existing `check_workflow_when.py` /
  `check_workflow_dag.py` static guards) can reject any `.claude/skills/**/SKILL.md` whose
  frontmatter contains `disallowed-tools`, or whose `allowed-tools` contains a bare-family or
  `Bash(*)` pattern. Wiring this lint into CI is a follow-up implementation item.

### 5. Dynamic Context Injection Policy

**Rule: dynamic context injection — in both phase commands and reference skills — must go
through the existing compact-artifact scripts. Raw `cat ARCHITECTURE.md`, raw `git diff`, and
raw issue-comment dumps are banned.**

This is a documentation requirement, not a new capability: the scripts and their enforcement
already exist and are already active by default (`docs/dark-factory-token-optimization.md`,
live since #664–#673, enforced for refine/plan/conformance/code-review since #733):

- `scripts/architecture_slice.py` — component-relevant sections only, not the full doc.
- `scripts/memory_retrieve.py` — top-k scored entries, not full memory files.
- `scripts/comment_digest.py` — a single digest, not raw comment history.
- `scripts/diff_rank.py` — risk-ranked/truncated diff, not the full `git diff`.
- `scripts/context_pack.py` / `scripts/context_budget.py` — assembly and budget enforcement
  across the above.

Any new reference skill that needs codebase/issue/diff context must call one of these (or a
new script following the same pattern — output written to a file under `$ARTIFACTS_DIR` and
read back, per the established pattern) rather than inlining a raw dump. This closes the loop
on the acceptance criterion by naming the existing mechanism as the mandatory one, rather than
leaving it as an unstated convention.

### 6. Compatibility with Archon Workflow Messages

No compatibility work is required beyond §1's taxonomy: Archon workflow messages
(`.archon/workflows/archon-dark-factory.yaml`) dispatch phase commands by `command:` id exactly
as today. Reference skills are never DAG nodes and never appear in workflow YAML; they are
loaded by whatever Claude Code session is running (a phase command's own subagents, or a human
in local dev). There is no new message format, gate, or DAG node introduced by this policy.

### 7. Review Expectations

Every PR touching `.claude/skills/**`, `commands/**` + `.archon/commands/**`,
`.claude/settings.json`, `.factory/hooks/**`, or plugin config gets:

- The standard `conformance:` and `code_review:` gates (unchanged, already run on every
  factory PR).
- Human sign-off if the change is a **behavior** change per §8's Tier 2 (new side-effecting
  skill, or any change to a phase command's actual tool usage/dispatch).

**Follow-up (non-blocking, tracked under the parent epic):** add `.claude/skills/**`,
`.claude/settings.json`, and `.factory/hooks/**` to `.factory/adapter.yaml`'s
`safety.hard_exclude_paths` — the actual fail-closed gate read by `epic_autopilot.py` (not
`config.yaml`'s `epic_autopilot.hard_exclude_paths`, which is a legacy fallback). These paths
are conspicuously absent today despite being exactly the self-modifying-factory surface that
exclusion list exists to protect, and adding them makes any change here always require a human
even under `allow_self_improvement: true`. Separately, add the same globs to
`safety.critical_diff_paths` so `diff_rank.py` surfaces these diffs first in code-review rather
than risking truncation — a visibility measure, distinct from the exclusion gate.

### 8. Evaluation and Rollout Expectations

| Tier | Change type | Requirement |
|---|---|---|
| 0 | Pure doc/prompt reorganization (renames, moves, wording) | Standard `conformance:` + `code_review:` gates only. No bench run. |
| 1 | Structural/consolidation (e.g. the §2 `refinement` move, an `allowed-tools` retrofit) | Tier 0, plus a targeted smoke check: `smoke_gate.sh` + a refine/plan dry run on a scratch issue. Not a full bench sweep — the risk is "the harness can't find the skill/config," which a targeted smoke check catches directly and cheaply. |
| 2 | Behavior change to a phase command, or any new side-effecting reference skill | Tier 1, plus explicit human sign-off. Add a `bench/run_suite.sh` parity run only if the change plausibly affects implement-phase DAG behavior — that is the only population `bench/suite.json`'s oracles cover (10 MarketHawk implement-phase golden-PR replays); mandating it for refine/plan-only changes tests the wrong thing at real cost (~$26/run per `docs/parity-p2.md`'s accounting). |

---

## Alternatives Considered

1. **Full migration** — move all seven phase commands (+ ceiling-revisit) into
   `.claude/skills/<phase>/SKILL.md` with `disable-model-invocation: true`, using the Skill
   file format purely as a governance convention while keeping DAG-based dispatch.
   **Rejected.** With model-invocation disabled, the SKILL.md would not function as a skill at
   all — it becomes a renamed command file with no discovery benefit, while creating a second
   loader path to maintain and risking a fight with `check_workflow_dag.py`'s validation of
   `command:` ids. Pure format churn on the single most safety-critical part of the pipeline,
   for zero behavioral benefit, and it would violate the parent epic's preservation
   constraint.
2. **Scope skills conventions to reference-only, and declare phase commands entirely
   out-of-policy** (no hygiene rules by analogy). **Rejected as too weak.** The acceptance
   criteria explicitly require allowed-tools limits, injection hygiene, and review expectations
   for whatever executes implement/merge/deploy; leaving phase commands as a bare omission
   would fail to address that even though they don't use Skill frontmatter.
3. **Chosen: taxonomy + hygiene-by-analogy** (this document). Phase commands keep their
   existing dispatch mechanism unchanged (satisfying preservation) and gain a documented
   category label plus injection/review rules applied by analogy, not by new frontmatter.
   Reference skills are a genuinely new, smaller category that gets the full frontmatter
   treatment (`disable-model-invocation`, `user-invocable`, `allowed-tools`) since that's where
   Claude's actual model-invoked discovery mechanism applies.

---

## Open Questions (Non-blocking)

Four schemas were proposed in issue comments posted after the first refinement pass (by the
same "Hermes Agent" planning agent that authored the issue), none of which appear in #42's own
acceptance criteria. Per Q5/A5, these are recorded here as future work, not adopted:

- **Factory Role Card schema** (name/phase/mission/responsibilities/non_responsibilities/
  side_effect_level/allowed_tools/forbidden_tools/inputs/outputs/output_schema/stop_condition/
  failure_mode/fallback_path/observability/eval_requirements) — revisit once more than one
  non-`refinement` reference skill exists; a 15-field contract for a population of one is
  premature.
- **Loop-engineering fields** (`loop_moves`, `failure_controls`, `cost_controls`,
  `human_checkpoint`) — relevant if/when a skill participates in an autonomous multi-move loop
  in its own right; not applicable to a manually/command-invoked reference skill today.
- **SKILL.md section canon** (Overview / When to Use / When Not to Use / Core Process / Common
  Rationalizations / Red Flags / Verification) — a reasonable documentation-structure
  convention to standardize once the skill population grows past one.
- **`mechanism_carrier` classification** (skill|prompt|workflow|evaluator|memory_schema|
  scheduler_policy|adapter_schema|script, with change/validation/rollback metadata) — the one
  with the clearest standalone value, since it targets distinguishing artifact-edits from
  mechanism-edits for the conformance/code-review gating this factory already invests in.
  Recommend tracking it as a follow-up against the conformance/code-review subsystem directly,
  not as skill frontmatter.

Also non-blocking:

- Formal closure of #41 with its own inventory document (this spec's "Inventory Basis" section
  is offered as a starting point, not a replacement).
- The `refinement` skill consolidation (§2), the `allowed-tools` retrofit lint (§4), and the
  `.factory/adapter.yaml` exclusion-list update (§7) are all follow-up implementation tickets
  under the parent epic — this ticket defines the policy they must conform to.

---

## Assumptions

- **[Flagged]** Claude Code's `allowed-tools` frontmatter semantics (deny-by-default,
  subcommand-pattern matching like `Bash(git diff:*)`) are assumed to work as documented by the
  platform; this repo has zero existing usage to verify against, since no skill has this
  frontmatter today.
- **[Flagged]** "Reference skill" as a category is assumed to remain singular
  (`refinement`) for the near term; the layout/graduation rules in §2 are designed to scale
  but have only been validated against that one real example.
- The GitHub issue's "Depends on: #41" is assumed non-blocking per A1's reasoning (no
  GH-native enforcement); if #41's eventual owner disagrees with that reading, this spec's
  Inventory Basis section should be reconciled with #41's formal output rather than treated as
  a substitute.
- `bench/suite.json`'s oracle set is assumed to remain implement-phase-only for the purposes of
  the §8 tiering; if refine/plan-phase oracles are added to the bench suite in the future, the
  Tier 2 rule should be revisited to include them.
