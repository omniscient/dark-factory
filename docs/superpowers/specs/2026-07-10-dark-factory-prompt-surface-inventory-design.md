# Dark Factory Prompt Surface Inventory

**Issue:** omniscient/dark-factory#41 — Inventory Dark Factory prompt surface and existing Claude
Skills actuals
**Epic:** omniscient/dark-factory#40 — Dark Factory Claude Skills modularization: phase playbooks
and prompt efficiency supplement
**Status:** Re-run. A prior spec was generated on 2026-07-01 against this same issue before this
repo was extracted from `omniscient/markethawk` (issue was numbered #692 there — see
`docs/cutover-markethawk.md`). That spec's two Q&A answers are preserved below and still hold.
Three comments were added after that spec was published, asking for three additional analytical
lenses; this run answers those via two new product-owner Q&A rounds and folds the result into an
updated version of the deliverable, re-run against this repo's actual (post-cutover) file layout.

## Overview

Dark Factory's prompt surface — the text an LLM actually reads to run a phase — is currently
spread across markdown command files, a large Archon workflow YAML, a small bundle of
"refinement-skills" prompts, an entrypoint shell script, and several config files, with
deterministic Python/shell scripts doing the context-shaping work around them. None of it is
expressed as a Claude Code Skill today. Before any of it is migrated to that model (the subject of
the sibling issues under #40), we need a precise, current inventory: what exists, how big it is,
what kind of thing it is, and where the actual duplication/gaps are — grounded in what already
shipped from #36 (token optimization) rather than duplicating that work.

## Requirements

(Distilled from the original Q&A, held from the 2026-07-01 run, plus this run's two new answers.)

- Token counts computed via `char / 4` (`scripts/token_estimate.py::estimate_tokens`), consistent
  with `context_budget.py` telemetry.
- Five-category taxonomy, fixed: always-needed fact, phase procedure, large reference,
  deterministic script, security-sensitive config. No sixth category; a straddling surface picks
  the dominant category and notes the secondary in the action column.
- A single Markdown migration-map table is the primary deliverable — not new sub-issues.
- The three new analytical lenses (role-card pattern, five loop-moves + failure modes, agent-skills
  anatomy) apply only to the 14 files that are actually prompts (8 phase commands + 6
  refinement-skills prompts) — not to scripts or config, which have no role-card/loop/anatomy
  semantics. They are folded into one additive table, not three separate ~40-row tables.
- The "small curated Dark Factory role set" is identified (candidate names + source prompts +
  rationale) but not authored in full — actual role-card content is follow-up implementation work,
  out of scope for a size:S inventory ticket.
- Overlap cross-reference: every #36 child issue mapped to the surfaces it addressed; every
  still-open #40 sibling (#42–#49) mapped to the migration-map rows it should absorb, so no new
  duplicate tickets get filed.
- No migrations implemented in this ticket.

## Brainstorming Q&A

> **Q1 (2026-07-01 run):** The issue calls for a "migration map for follow-up issues." What form
> should this take — (a) a Markdown table in the spec, (b) new GitHub sub-issues, or (c) prose
> grouped by theme?
>
> **A1:** Use (a) a Markdown table embedded in the spec as the primary deliverable, not new
> sub-issues. This is a `size: S` foundation/inventory ticket; its job is to produce an artifact
> downstream tickets consume. Auto-creating sub-issues risks duplicating work already tracked in
> #36–#675. One row per file/surface: `Source | ~Tokens | Classification | Recommended action |
> Target destination | Related issue`.

> **Q2 (2026-07-01 run):** Should token counts use `token_estimate.py` (char/4) or byte size as a
> proxy? Are the five classification categories fixed?
>
> **A2:** Mechanically identical — use the existing utility. Categories fixed at the five in the
> AC; if a surface straddles two, pick the dominant one and note the secondary in the action
> column. Do NOT add a sixth category.

> **Q3 (this run):** The re-run added three comments asking for three additional analytical
> lenses (role-card pattern, five loop-moves + failure modes, agent-skills anatomy) across ~40+
> candidate surfaces. Given the ticket is still `size: S`, should the spec (a) add all three as
> full ~40-row tables, (b) apply the lenses only to the actual prompt surfaces (8 commands + 6
> refinement-skills), keeping the original migration map unchanged, or (c) fold the lenses into the
> existing table's columns?
>
> **A3:** (b), with one refinement: fold all three lenses into a single additive table scoped to
> the 14 prompt surfaces, rather than three near-duplicate tables. Role-card semantics, loop moves,
> and agent-skills anatomy are prompt concepts — a deterministic script or a security-sensitive
> config file has no "anti-rationalization guard" or "role card," so forcing ~40 rows through those
> lenses produces mostly-null cells (busywork, not inventory) and would quadruple the document
> against the prior Q2 ruling against scope inflation. The original ~40-row migration map (Table 1)
> stays exactly as originally scoped and remains the primary deliverable; the new lenses appear
> only in Table 2 (14 rows), which is where the three comments' distinctions (role card vs
> phase-skill vs script-owner; discovery/handoff/verification/persistence/scheduling; anatomy gaps)
> actually carry signal.

> **Q4 (this run):** For the "small curated Dark Factory role set" (Agency Agents comment) — should
> the spec author full role-card content (identity/mission/critical rules/workflow/deliverables/
> success metrics/output style, fully written out) for each curated role, or only identify
> candidates with a one-line rationale each?
>
> **A4:** Identify and recommend candidates only; defer authoring. The deliverable for #41 is an
> inventory + migration map, and the ticket is explicitly `size: S` with no new prompt content in
> its AC. Authoring 3–5 full role cards is net-new prompt-engineering work that belongs to
> implementation, not audit — it would also pre-commit the factory to adopting a pattern
> (`agency-agents` role cards) that has zero prior references anywhere in this repo. The spec
> documents the role-card schema as a reusable pattern, scores existing prompts against it to
> expose gaps, and proposes a small curated role set as a table of candidate names mapped to source
> prompt(s) with a one-line rationale — no more. Actual card authoring is a follow-up
> implementation issue that this spec's migration map feeds into.

## Approach

Enumerate every surface named in the issue's Scope section against the actual current file layout
of this repo (which changed materially at the P3 markethawk→dark-factory cutover — see
`docs/cutover-markethawk.md`; several file paths referenced *inside* the prompt surface itself
still assume the pre-cutover nested layout, which is itself one of this inventory's findings, see
below). Compute `char/4` token counts per file. Classify each into the fixed five-category
taxonomy. Cross-reference against #36's now-closed children (#153–#164, all shipped) and #40's
still-open siblings (#42–#49, not yet started) so the migration map points at existing tickets
instead of inventing new ones.

### A note on repo layout vs. what the prompt surface assumes

This repo is the extracted, self-hosting `dark-factory` repo (see `docs/cutover-markethawk.md`).
The git-tracked top-level layout is `commands/`, `workflows/`, `refinement-skills/`, `scripts/`,
`config/`, `entrypoint.sh` — these get `COPY`'d into the Docker image at build time (see
`Dockerfile` lines 111–127) at `/opt/dark-factory/{scripts,workflows,commands}` and
`/opt/refinement-skills/`. But every phase command, the workflow YAML, and `entrypoint.sh` itself
still reference a `dark-factory/scripts/...` path prefix (42 occurrences) and a
`.claude/skills/refinement/config.yaml` config path (18 occurrences) — both are the **pre-cutover
MarketHawk nested-repo convention**, not this repo's own top-level layout. This is not a bug:
`entrypoint.sh` (lines 509–523) deliberately materializes `$CLONE_DIR/dark-factory/scripts` (copied
from the baked `/opt/dark-factory/scripts`) and `$CLONE_DIR/.archon/{workflows,commands}` at
container startup specifically so the self-hosting run of this repo satisfies those legacy paths
without having rewritten every command file. The `# TARGET-PATH` comments scattered through the
command files mark exactly these indirection points. It works, but it is a real, currently-load-
bearing piece of technical debt: the prompt surface hard-codes a path convention that differs from
its own repo's tracked layout, bridged by a physical-copy compatibility shim rather than a single
templated path. **This belongs in the migration map** (see row for `entrypoint.sh` / `TARGET-PATH`
indirection below) as a discrete, currently-non-blocking cleanup candidate, not folded into any
single command's row.

A second, related gap: 4 of the 8 phase commands (`refine`, `plan`, `implement`, `validate`) open
Phase 1 with "Read `CLAUDE.md`" — but this repo has no `CLAUDE.md` (confirmed: no file at the repo
root, and `.factory/adapter.yaml` explicitly notes "No ARCHITECTURE.md in this repo yet" for the
same reason). The read is a silent no-op when self-hosting, not a gate failure, since these are
advisory `Read X` instructions rather than existence-checked ones. The issue's own AC anticipates
this ("versus move to skills/supporting files... a future `CLAUDE.md`") — authoring that file is
tracked as its own migration-map row below, not assumed to already exist.

### Table 1 — Migration map (all surfaces in scope)

| Source | ~Tokens (raw) | Classification | Recommended action | Target destination | Related issue |
|---|---:|---|---|---|---|
| `workflows/archon-dark-factory.yaml` | 16,763 | phase-procedure | Per-phase message blocks already trimmed by #36 (context packs/budgets); large size is structural (67KB YAML, ~30 DAG nodes) — no further slimming without splitting the DAG itself. Leave as-is. | Archon workflow (unchanged) | #45 (context injection already wired for its message blocks) |
| `commands/dark-factory-refine.md` | 2,469 | phase-procedure | Heavily duplicates `refinement-skills/orchestrator-prompt.md` (both describe the same 4-phase brainstorm→spec process in similar prose — ~2,482 + ~934 tokens, much of it redundant). Collapse to one canonical source. | `refinement-skills/` phase skill (canonical) + thin command wrapper | #43 |
| `commands/dark-factory-plan.md` | 2,409 | phase-procedure | Keep as phase orchestrator; extract the Phase 3.5 conformance-reconcile loop block (shared verbatim shape with `dark-factory-conformance.md`'s Phase 3.5) into one shared supporting doc. | Phase skill + shared "reconcile loop" reference file | #43, #44 |
| `commands/dark-factory-implement.md` | 4,573 | phase-procedure | Largest command file. Its Phase 5 MEMORY UPDATE section (~1,500 tokens: write-bar, entry types, expiry, R4/R5 rules) is a fully general memory-governance protocol duplicated in spirit by the code-review/conformance memory-write blocks (`gate_lib.sh`'s `write_memory_entry`). Extract to one shared "memory protocol" reference; commands then just cite it. | Phase skill + `references/memory-protocol.md` | #44 (shares `gate_lib.sh` already) |
| `commands/dark-factory-conformance.md` | 5,351 | phase-procedure | Largest file in the surface. Bundles 5 distinct concerns (spec location, pre-triage/diff-ranking, adversarial review, OOS scope remediation, reconcile loop) in one file — the strongest single split candidate. | Split into conformance phase skill + `oos-remediation` reference (scripts already exist: `dedupe_oos.py`, `fmt_hunk_filter.py`) | #44 |
| `commands/dark-factory-code-review.md` | 2,280 | phase-procedure | Clean single-responsibility file already; low-priority migration. | Code-review phase skill | #44 |
| `commands/dark-factory-revise-advisory.md` | 1,169 | phase-procedure | See Table 2 — zero independent verification of its own output (Blind Loop candidate); flag for a verification step before any skill conversion, not just a mechanical move. | Phase skill (add verification step first) | #44 (verification gap not currently tracked by any open issue — new follow-up) |
| `commands/dark-factory-validate.md` | 2,017 | phase-procedure | Phase 3 FIX loop has no `MAX_CYCLES` bound (every sibling reconcile loop — plan, conformance — caps at 3). Add a cap before any skill conversion. | Phase skill (add cycle cap first) | New follow-up (not currently covered by #42–#49) |
| `commands/ceiling-revisit.md` | 1,689 | phase-procedure (secondary: deterministic — nearly all decision logic already lives in `ceiling_revisit.py`) | Prose here is mostly orchestration glue around an already-deterministic decision; good demotion candidate to a thin wrapper + script once skills exist. | Low-priority "ops" skill, or leave as command | Not covered by #36/#40 children — new low-priority follow-up |
| `refinement-skills/SKILL.md` | 389 | phase-procedure (secondary: reference/router) | Closest thing to Overview+Usage already; missing When-to-use/not, Common-Rationalizations, Red-Flags. Also the one file most at risk of becoming a second router on top of the Archon workflow's own dispatch (see Table 2). | `refinement-skills/` root `SKILL.md`, expanded per agent-skills anatomy | #42, #43 |
| `refinement-skills/orchestrator-prompt.md` | 931 | phase-procedure | See `dark-factory-refine.md` row — merge, don't duplicate. | Folded into refine phase skill | #43 |
| `refinement-skills/product-owner-prompt.md` | 507 | phase-procedure | Already the strongest anti-rationalization example in the repo (explicit `UNCERTAIN:` escape hatch with worked examples). Use as the template when authoring the curated role-card set (see below), not just migrated as-is. | Reusable "Product Owner" role card + phase skill | #43 |
| `refinement-skills/architect-prompt.md` | 705 | phase-procedure | Clean, tightly scoped; low-priority migration. | Reviewer role card + phase skill | #43, #44 |
| `refinement-skills/conformance-reviewer-prompt.md` | 1,261 | phase-procedure | Best reuse in the surface already — spawned identically by both `plan.md` and `conformance.md`, zero duplication. Model for how shared reviewer prompts should work post-migration. | Reviewer role card + phase skill | #44 |
| `refinement-skills/code-review-reviewer-prompt.md` | 853 | phase-procedure | Clean, tightly scoped, machine-parsed output; low-priority migration. | Reviewer role card + phase skill | #44 |
| `entrypoint.sh` | 8,575 | deterministic script (secondary: phase-procedure — inline prompt fragments, scenario routing, and the `TARGET-PATH` compatibility shim described above) | The `dark-factory/scripts` + `.archon/{workflows,commands}` materialization (lines 509–523) is real, working infra, not a bug — but it means every future path a phase command references needs a matching shim entry here. Consider a single `TARGET_PATH_PREFIX` env var/template substitution instead of physical copies, next time this file is touched. | No immediate action; note for #46 (security-sensitive surface) since this file governs what gets cloned/exposed to a factory run | #46 (new note, not yet in #46's stated scope) |
| `scheduler.sh` | 12,655 | deterministic script | No prompt content; governs board-state/dispatch policy. Out of scope for Skills migration — remains a script. | n/a | n/a |
| `smoke_gate.sh` | 1,279 | deterministic script | No prompt content. | n/a | n/a |
| `scripts/context_budget.py` | 3,992 | deterministic script | Already shipped, in active enforcement (`config/config.yaml` `token_optimization.enforce_budgets: true`). | n/a — done | #153, #164 |
| `scripts/context_pack.py` | 3,675 | deterministic script | Already shipped. | n/a — done | #154 |
| `scripts/architecture_slice.py` | 4,958 | deterministic script | Already shipped; currently falls back to full/no-doc mode for this repo since no `ARCHITECTURE.md` exists yet (see gap above). | n/a — done, but starved of input | #155 |
| `scripts/comment_digest.py` | 2,159 | deterministic script | Already shipped. | n/a — done | #157 |
| `scripts/diff_rank.py` | 5,281 | deterministic script | Already shipped, used by both conformance and code-review gates. | n/a — done | #158 |
| `scripts/memory_retrieve.py` | 6,015 | deterministic script | Already shipped, top-k capped (`max_entries: 8`, `max_tokens: 1500`). | n/a — done | #156 |
| `scripts/{memory_write,memory_import,memory_maintain}.py`, `gate_lib.sh`, `load_memory_context.sh` | ~10,627 | deterministic script | Memory-governance scripts backing the Phase 5 protocol duplicated in prose in `dark-factory-implement.md` (see that row). `memory_write.py` still hardcodes the `[AVOID]` tag (known limitation, tracked). | n/a | #652 (tag hardcode), #140 (memory v2) |
| `scripts/{gate_blast_radius,code_review_payload,dedupe_oos,fmt_hunk_filter}.py` | ~7,210 | deterministic script | Gate-support scripts; already the deterministic backbone `dark-factory-conformance.md`/`dark-factory-code-review.md` should delegate more prose to. | n/a | #44 |
| `scripts/{eval_agentmemory.sh,eval_memory_quality,fetch_scorecard,ceiling_revisit,budget_enforce}.py` | ~17,497 | deterministic script | Evaluation/ops scripts; out of Skills-migration scope. | n/a | #48, #161 |
| `scripts/{check_workflow_dag,check_workflow_when}.py`, `identity.sh`, `hooks.sh`, `agent_roles.sh`, `check_preview_creds.sh`, `oos_excise.sh`, `token_estimate.py`, `iii-config.agentmemory.yaml` | ~4,643 | deterministic script | CI/ops utilities; no prompt content. | n/a | n/a |
| `scripts/factory_core/` (13 files) | 23,075 | deterministic script | Self-target adapter, board, epic-autopilot, deconflict, breaker logic. No prompt content; out of Skills-migration scope. | n/a | #211 (epic_autopilot bug) references this package |
| `config/config.yaml` (mirrored, comment-stripped, at `.claude/skills/refinement/config.yaml` for runtime path compatibility — same values, see gap above) | 2,481 | security-sensitive config | Governs every gate's block/skip/enforce policy. Any Skills migration must preserve `disable-model-invocation`-equivalent guardrails around this file per #42/#46. | n/a — policy source of truth | #42, #46 |
| `.factory/adapter.yaml` | 416 | security-sensitive config | Explicitly self-documents two repo gaps this inventory also found (no `ARCHITECTURE.md`, no `CLAUDE.md`-aware components map). Already flagged as security-sensitive via `sensitive_keywords`/`hard_exclude_paths`. | n/a | #46 |
| `archon-config.yaml` | 18 | always-needed fact | Trivial (assistant + worktree base branch); no action needed. | n/a | n/a |
| `README.md` | 3,134 | always-needed fact (not currently loaded by any phase — gap) | No phase command reads `README.md` today. Candidate for inclusion once a `CLAUDE.md` is authored (see next row) rather than loaded standalone. | n/a | New follow-up |
| `CLAUDE.md` (does not exist in this repo) | 0 | always-needed fact (phantom — referenced by 4/8 phase commands, gap) | Author a dark-factory-specific `CLAUDE.md` — anticipated by this issue's own AC ("a future `CLAUDE.md`"). Until then, the 4 "Read CLAUDE.md" instructions are silent no-ops when self-hosting. | New file, new follow-up issue | New follow-up (not covered by #42–#49) |
| `ARCHITECTURE.md` (does not exist in this repo) | 0 | always-needed fact (phantom, gap) | Same as above; `.factory/adapter.yaml` already documents the fallback behavior in `architecture_slice.py`. | New file, new follow-up issue | #155 (consumer), new follow-up (author) |
| `.archon/memory/dark-factory-ops.md` | 4,436 | large reference (selectively retrieved, top-k capped by #156 — not always-loaded) | Working as designed; growing toward the 30-entry R4 cap on some sections. No action needed. | n/a | #156, #140 |
| `docs/{domain,cutover-markethawk,dark-factory-token-optimization,dark-factory-memory-contract,triage-labels,parity-p1,parity-p2}.md` (7 files) | 14,339 total (~2,048 avg) | large reference | Not loaded by any phase command or workflow node today (confirmed by grep — zero references). Legitimate standalone reference docs; no migration action needed, just noting they are outside the always-loaded budget. | n/a | n/a |

*(Total across present, in-scope files: ~72 surfaces, phase-procedure ≈43,366 tok across 15 files,
deterministic-script ≈111,641 tok across 45 files (many grouped above), security-sensitive-config
≈2,897 tok across 2 files, always-needed-fact ≈3,152 tok across 2 present files (+2 phantom),
large-reference ≈18,775 tok across 8 files. Deterministic scripts dominate raw byte count but are
never loaded into an LLM context wholesale — they only matter for factory maintainability, not
prompt budget, which is why the previous run's category tally emphasized phase-procedure /
always-needed-fact / large-reference as the actual budget-relevant categories. Re-verified
2026-07-10 against every row in Table 1, not a sample; see Assumptions.)*

### Table 2 — Prompt-surface analysis (14 rows: the actual LLM-facing prompts only)

Columns fold in all three of this run's new lenses: role-card shape (Agency Agents comment),
the five loop moves + failure-mode risk (Loop Engineering comment), and agent-skills anatomy gaps
+ router flag (agent-skills comment).

| Prompt | Phase | Role-card candidate | Phase-skill (how it runs) | Loop moves present | Failure-mode risk | Anatomy gaps | Router flag |
|---|---|---|---|---|---|---|---|
| `commands/dark-factory-refine.md` | refine | Phase Orchestrator | 6-phase brainstorm→spec pipeline | Discovery, Handoff (PO subagent), Verification (self-review scan), Persistence | Nodding Loop (mitigated by `UNCERTAIN:` escalation) | No When-to-use/not; no Common-Rationalizations; self-review only, not adversarial | No |
| `commands/dark-factory-plan.md` | plan | Phase Orchestrator | plan-write → architect review (≤3 cyc) → conformance review (≤3 cyc) → publish | Discovery, Handoff (2 independent Opus reviewers), Verification (adversarial ×2), Persistence, Scheduling (board move) | Low — two independent adversarial gates, well-delineated responsibilities | No When-to-use/not | No |
| `commands/dark-factory-implement.md` | implement | Phase Orchestrator | LOAD→PLAN→IMPLEMENT(TDD)→DOCUMENT→MEMORY→REPORT | Discovery, Verification (TDD checkpoints), Persistence (best-in-surface memory governance) | Blind Loop (locally — no subagent double-checks its own "tests pass" claim; systemically caught by downstream validate/conformance/code-review gates) | Excellent Common-Rationalizations guard (memory "write bar" 4-question filter); no When-to-use/not | No |
| `commands/dark-factory-conformance.md` | conformance (Gate 2) | Gate/Reviewer Orchestrator | LOAD→locate spec→triage diff→adversarial review→scope remediation→reconcile (≤3 cyc) | Discovery (4-tier spec lookup), Handoff (Opus reviewer), Verification (adversarial), Persistence, Scheduling (hard exit-1 gate) | Tangled Loop candidate — 5 distinct concerns bundled in one file (matches #44's stated split goal) | Strong false-positive guards (doc/formatter exemptions) act as anti-rationalization; no When-to-use/not | No (internal OOS action routing is fully deterministic via `dedupe_oos.py`, not a soft prompt-level router) |
| `commands/dark-factory-code-review.md` | code-review (Gate 3) | Gate/Reviewer Orchestrator | LOAD→rank diff→adversarial review→deterministic threshold→post/block | Discovery, Handoff (Opus reviewer), Verification (severity-tagged, deterministically thresholded — judgment/policy cleanly separated), Persistence, Scheduling | Low | No When-to-use/not | No |
| `commands/dark-factory-revise-advisory.md` | post-Gate-3 (advisory) | Advisory Fix-Agent Orchestrator | read findings → spawn Sonnet fixer → commit/push → report | Discovery, Handoff (Sonnet, appropriately cheaper) | **Blind Loop — the standout finding.** Zero independent verification of the fixer's own changes before push; only checks "did any file change," not "is the fix correct" | No Verification section at all; no guard against the fixer introducing a new bug | No |
| `commands/dark-factory-validate.md` | validate | Phase Orchestrator | blast-radius hard gate → empirical pytest/tsc/curl → **uncapped** fix loop → cleanup | Discovery, Verification (the only surface doing real empirical/evidence-based checks against a live preview, not LLM judgment) | **Unbounded-loop risk — the other standout finding.** Phase 3 "Repeat until all validations pass" has no `MAX_CYCLES`, unlike every sibling reconcile loop (plan, conformance) | Best-in-surface Verification; but the missing cycle cap is itself a Red-Flag this file should carry and doesn't | No |
| `commands/ceiling-revisit.md` | weekly ops (not per-issue) | Ops/Scheduler Analyst (no current role card) | fetch scorecard → deterministic decision → comment → conditional PR → unconditional next-issue filing | Discovery, Persistence, **Scheduling (strongest self-scheduling example in the surface — unconditionally files its own successor issue)** | Low (PR still requires human merge before taking effect) | Almost no LLM judgment left in this file — good demotion candidate, not really "agent-skills" shaped | No |
| `refinement-skills/SKILL.md` | refine/plan (both) | n/a (describes the bundle, isn't itself executed) | Descriptive Overview + Usage | n/a (descriptive, not procedural) | n/a | Closest existing file to Overview/Usage; **missing When-to-use/not, Common-Rationalizations, Red-Flags entirely** | **Yes — flagged.** Its intent-dispatch (refine vs plan) already overlaps the Archon workflow's own dispatch; avoid a second router here per the agent-skills comment |
| `refinement-skills/orchestrator-prompt.md` | refine | Phase Orchestrator (see refine row — merge candidate) | Duplicates `dark-factory-refine.md`'s phase breakdown near-verbatim | Discovery, Handoff (PO) | Same Nodding-Loop note as refine row | No When-to-use/not; Verification delegated to caller, not present here | No |
| `refinement-skills/product-owner-prompt.md` | refine (Q&A) | Domain Product Owner | Single-turn persona, not a loop | n/a | n/a | **Best-in-surface anatomy already** — explicit `UNCERTAIN:` escape hatch with worked positive/negative examples, i.e. an existing Common-Rationalizations + Red-Flags pattern in all but name | No |
| `refinement-skills/architect-prompt.md` | plan | Independent Reviewer/Gate | Single-turn mechanical-traceability reviewer | Verification (its whole purpose) | Low — explicitly defers conformance judgment to a sibling reviewer, avoiding Tangled Loop | "No Placeholders" section is a genuine anti-rationalization/evasion catcher; no When-to-use/not | No |
| `refinement-skills/conformance-reviewer-prompt.md` | plan + conformance (shared, zero duplication) | Independent Reviewer/Gate | Single-turn tiered-verdict reviewer | Verification (its whole purpose) | Low — best reuse example in the repo | Strong anti-false-positive exemptions; no When-to-use/not | No |
| `refinement-skills/code-review-reviewer-prompt.md` | code-review | Independent Reviewer/Gate | Single-turn severity-tagged reviewer | Verification (its whole purpose) | Low | Machine-parsed output is itself a verification aid; no When-to-use/not | No |

### Curated role set (candidates only — not authored this run, per A4)

| Candidate role | Source prompt(s) | Rationale |
|---|---|---|
| Phase Orchestrator | All 8 `commands/*.md` | Each already carries an implicit identity/mission/boundary (SCOPE BOUNDARY blocks, exit-code contracts) re-derived from prose every time; a literal role card would make the shared contract explicit and auditable. |
| Independent Reviewer / Gate | `architect-prompt.md`, `conformance-reviewer-prompt.md`, `code-review-reviewer-prompt.md` | All three already share one shape: narrow judgment scope, structured verdict, explicit deferral to a sibling reviewer. A shared role-card template would make that contract explicit and guard against scope creep between them. |
| Domain Product Owner | `product-owner-prompt.md` | Already the best-shaped anti-rationalization example in the repo; promoting it to a role card is mostly extracting its existing pattern as the template for any future Q&A persona. |
| Ops / Scheduler Analyst | `ceiling-revisit.md` (+ implicitly `scheduler.sh`) | As token-optimization work pushes more judgment out of prose into scripts, this file is the leading edge of a "thin prompt wrapper over a deterministic decision" pattern distinct from the Phase Orchestrator shape; worth its own minimal role card so future ops commands don't reinvent it. |

Per A4, no full role-card content (identity/mission/critical rules/workflow/deliverables/success
metrics/output style) is authored here — this table is the recommendation surface for a follow-up
implementation issue, most naturally scoped under #42 (Claude Skills conventions) or #43.

## Alternatives Considered

1. **One combined ~40-row table with all lenses as extra columns (option (a)/(c) from Q3).**
   Rejected per A3 — produces mostly-null cells for the 26 non-prompt surfaces and destroys the
   cross-surface comparison signal (failure-mode spotting, anatomy-gap spotting) that is the whole
   point of the three new lenses.
2. **File new GitHub sub-issues directly from this ticket for every migration-map row.** Rejected
   per A1 (2026-07-01) — this is a `size: S` inventory ticket; #40's siblings #42–#49 already cover
   the actionable work at the right granularity, and auto-filing would duplicate them.
3. **Author full role cards now.** Rejected per A4 — out of scope for an inventory ticket; the
   pattern (`agency-agents` role cards) has zero prior adoption in this repo and deserves its own
   design decision, not a side effect of an audit.

## Open Questions (non-blocking)

- Should the `entrypoint.sh` `TARGET-PATH` physical-copy shim be collapsed into a single templated
  path convention as part of #46, or does it stay as-is indefinitely since it currently works?
- Does authoring `CLAUDE.md`/`ARCHITECTURE.md` for this self-hosting repo belong under #40's epic
  or as an independent foundation ticket? Not currently owned by any open issue.
- Should `dark-factory-validate.md`'s missing `MAX_CYCLES` cap and `dark-factory-revise-advisory.md`'s
  missing verification step be filed as their own (non-Skills-migration) bug tickets now, given
  they're real gaps independent of whether/when the Skills migration happens? This spec surfaces
  them but does not file them (per A1's "no new sub-issues from this ticket" ruling).

## Assumptions

- Token estimates use `floor(len(text) / 4)` via `scripts/token_estimate.py`, computed 2026-07-10;
  re-run before citing exact numbers in implementation tickets, since several files (memory,
  config) mutate on every factory run.
- The "effective in-prompt" (post-slicing/post-capping) token figures reported in the 2026-07-01
  MarketHawk-repo spec (refine ~11,900 / plan ~10,300 / implement ~11,600 / conformance ~6,900 /
  code-review ~3,000 / validate ~5,900) are not re-verified here — they depend on live
  `context_budget.py` telemetry from actual runs, which is per-repo-instance data this inventory
  did not have access to for this newly-extracted repo. Treat those figures as historical
  (pre-cutover) context, not current fact.
- `architecture_slice.py` and the `CLAUDE.md`-reading Phase 1 steps are currently degraded (fallback
  / no-op) for this specific self-hosting repo because `ARCHITECTURE.md`/`CLAUDE.md` don't exist yet
  here — this is a property of *this* repo's self-hosting instance, not a defect in the scripts or
  commands themselves (which work correctly against external target repos like MarketHawk that do
  have these files).
