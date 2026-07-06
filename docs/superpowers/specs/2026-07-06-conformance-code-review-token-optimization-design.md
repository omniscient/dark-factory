# Design Spec: Wire conformance + code-review into the token-optimization path

**Status:** pending review
**Source issue:** #19 (sibling of #18, "Raise doc-slicing component-resolution hit-rate")
**Author:** Dark Factory Refinement Pipeline

---

## Overview / Problem Statement

The `conformance` (Gate 2) and `code-review` (Gate 3) scenarios show **0% context savings**
in the 2026-07-03 full-corpus eval (`dark-factory/evals/results/token-opt-eval-2026-07-03.json`
— all 44 conformance/code-review rows have `baseline_tokens == opt_tokens`). This is not an
optimizer bug: these two scenarios were simply never registered for architecture-slicing or
memory-context retrieval in the first place, so there is nothing for the optimizer to trim, and
baseline vs. optimized packs are byte-identical by construction.

Root cause, traced to source:

- `_SECTION_REGISTRY` in `dark-factory/scripts/context_budget.py:26-33` is the single source of
  truth for which context sections a scenario's pack includes — it is imported directly by
  `context_pack.py` (`from context_budget import _SECTION_REGISTRY`, `context_pack.py:24-25`),
  which both the live per-run telemetry node and `evals/token_opt_eval.py`'s `assemble_pack`
  simulation depend on. Today:
  - `conformance`: `["skill_prompts", "spec", "implementation_md", "diff"]`
  - `code-review`: `["skill_prompts", "issue_context", "diff"]`
  - Neither includes `architecture_md` or `memory_context`. `implement` (the reference
    scenario), by contrast, includes both.
- Because `evals/token_opt_eval.py`'s baseline-vs-optimized split for a scenario/issue varies
  only the `labels` argument (`eval_issue_scenario`, `token_opt_eval.py:145-213`), and `labels`
  only affects the `architecture_md` section, a scenario whose registry entry never includes
  `architecture_md` produces identical baseline/optimized packs no matter what — hence the
  trivially-0% rows.
- Separately, conformance/code-review never call `memory_retrieve.py` / `load_memory_context.sh`
  for **reading** memory context (they only use `gate_lib.sh`'s `write_memory_entry` /
  `route_memory_file` to **write** new `[AVOID]` entries after a gate verdict). This is a real,
  currently-total functional gap, independent of the token-accounting problem above.

### A load-bearing finding that reframes the issue's premise

Architecture-slicing is **not actually live** anywhere today — including for the reference
`implement` scenario. `architecture_slice.slice_architecture()` is invoked only from inside
`context_budget.py` (telemetry) and `context_pack.py` (eval simulation); it is never called as
a script, and no command file (`commands/dark-factory-*.md`) consumes its sliced output. Each
command's Phase 1 LOAD does a literal, full `Read ARCHITECTURE.md` / `Read CLAUDE.md`. The only
**genuinely live** optimizations in the whole pipeline today are:

1. **Memory top-k** — `load_memory_context.sh` → `memory_retrieve.py`, called for real from
   `refine`/`plan`/`implement`'s Phase 1, and really does cap what enters the prompt.
2. **Diff ranking** — `diff_rank.py`, already called directly by both `conformance` and
   `code-review`.

This matters because it changes what "route through the same optimizer path the implement
scenario uses" can mean in practice (see Approach, below) — verified and confirmed with the
product owner in Q&A round 1.

Additionally, this repo's own `.factory/adapter.yaml` has `components: {}` (no `ARCHITECTURE.md`
exists in dark-factory yet), so architecture-slicing will always report `component_unresolved`
/ full-doc fallback for dark-factory's own self-hosted runs, regardless of this ticket. Real
component-slice savings depend on a target repo with a populated component map (e.g.
MarketHawk) and on sibling issue #18 (component-resolution hit-rate, currently 23%, still open).

---

## Requirements (from Q&A)

1. Add an `architecture_md` row to `conformance` and `code-review`'s `_SECTION_REGISTRY` entry
   so the (single, imported) registry gives both the live telemetry node and the eval harness
   something real to slice — this alone makes `evals/token_opt_eval.py` (unchanged) measure
   non-trivial baseline-vs-optimized deltas for these two scenarios, since it already loops
   over all 5 `ENFORCEMENT_SCENARIOS` and already varies `labels` per scenario/issue.
2. Do **not** build new live architecture-content injection into the conformance-reviewer or
   code-reviewer subagent prompts. That capability doesn't exist even for `implement` today; it
   is out of scope here and gated on issue #18.
3. Make memory top-k **genuinely live** for `conformance` and `code-review` — a real, valuable,
   currently-total gap, not just a telemetry fix. Both gates should load relevant past
   `[PATTERN]`/`[AVOID]`/`[FIX]` lessons into their reviewer subagent's prompt, the same way
   `refine`/`plan`/`implement` already do via `load_memory_context.sh`.
4. **Comment digest is explicitly descoped** (Non-Goal) — see Alternatives Considered.
5. `evals/token_opt_eval.py` requires **no code changes** — its scenario loop and baseline/
   optimized split are already scenario-agnostic; the registry fix is sufficient. (This
   simplifies the issue's scope item 3, which assumed harness code changes were needed.)
6. No change to enforcement mode, budgets, or diff-ranking behavior (issue's scope item 4 —
   enforcement "stays as-is").
7. Fail-open / safety invariants unchanged: safety-sensitive labels still force full-doc
   fallback (`architecture_slice._check_safety_fallback`, unaffected by this change); diff
   critical-tier stays cap-immune (`diff_rank.py`, untouched); any optimizer error still widens
   to full context, never narrows.

---

## Architecture / Approach

### 1. Registry fix (single edit point, telemetry + eval parity for architecture)

In `dark-factory/scripts/context_budget.py`, extend `_SECTION_REGISTRY`:

```python
"conformance": ["skill_prompts", "spec", "implementation_md", "diff", "architecture_md", "memory_context"],
"code-review":  ["skill_prompts", "issue_context", "diff", "architecture_md", "memory_context"],
```

No other code changes are needed in `context_budget.py` or `context_pack.py` — `build_budget()`
and `assemble_pack()` already dispatch `architecture_md`/`memory_context` generically per
section name, and `context_pack.py` imports `_SECTION_REGISTRY` from `context_budget.py` (not a
duplicate copy), so this is genuinely a one-place edit.

Effect: `evals/token_opt_eval.py`'s existing `eval_issue_scenario()` (unchanged) calls
`assemble_pack(labels=[])` for baseline and `assemble_pack(labels=<real issue labels>)` for
optimized, for every scenario in `ENFORCEMENT_SCENARIOS` including `conformance`/`code-review`
(already true today — that's *why* the 2026-07-03 eval already has rows for them, just all
zero). Once `architecture_md` exists in their registry entries, a real component may resolve
differently between the two label sets on a repo with a populated component map, producing a
real, non-trivial `savings_pct` — satisfying Acceptance criterion 1 without touching the eval
script.

### 2. Live component signals in the DAG budget nodes (telemetry accuracy, exceeds strict parity — justified)

Neither `budget-conformance` nor `budget-code-review` (in `workflows/archon-dark-factory.yaml`)
currently pass `--labels` or `--changed-files` to `context_budget.py` — and neither does
`budget-refine`/`budget-plan` today (a separate, pre-existing gap, likely part of #18's
component-resolution work; not fixed here). Left as-is, the **live** per-run
`context-budget.json` / cost-report comment for conformance/code-review would show
`component_unresolved` fallback forever, even though the **eval** (which builds its own labels
list independently, in-process) would show real numbers — a confusing split between what the
eval reports and what a live run reports.

Since both budget nodes already compute a diff (`git diff main...HEAD > "$DIFF_TMP"`) and both
already have `$ARTIFACTS_DIR/issue.json` (which carries `labels`), wiring these two extra flags
costs nothing new to fetch:

- `budget-conformance`: add `--changed-files` (from `git diff --name-only main...HEAD`) and
  `--labels` (from `jq -r '.labels[].name' "$ARTIFACTS_DIR/issue.json"`, read into a bash array
  so label names containing spaces/colons — e.g. `priority: should-have` — survive as single
  argv elements). `--spec-file` is already passed when a spec is found.
- `budget-code-review`: same — `--changed-files` from its existing `$DIFF_TMP`, `--labels` from
  `issue.json`. No `--spec-file` (code-review has no spec-file concept).
- Both: keep passing `--memory-file "$ARTIFACTS_DIR/memory-context.md"` for schema consistency
  with the other 3 scenarios, even though — per the documented behavior at
  `context_budget.py`'s `memory_context` handler — this will always read `dropped/empty` at
  budget-node time, because the budget node runs *before* the command session that actually
  writes `memory-context.md`. This is pre-existing, intentional, documented behavior for all
  five scenarios, not a new limitation introduced here.

This is a deliberate, narrow exception to "match `implement`'s current wiring exactly": it's a
reporting-only change (architecture stays telemetry-only per Requirement 2 — no prompt content
changes, no new behavior to break), it costs nothing new to compute, and it's the only way live
telemetry for these two gates will ever show non-fallback numbers on a repo with a populated
component map. If a reviewer prefers strict 1:1 parity with `refine`/`plan`'s current (unwired)
state instead, dropping this sub-step only affects the *live cost-report comment's* accuracy —
it does not affect the eval-measured acceptance criterion, which is satisfied by the registry
fix alone.

### 3. Live memory top-k for conformance and code-review

The `PHASE_SOURCE_MAP` in `memory_retrieve.py:42-48` already has two unused phase keys that were
evidently pre-provisioned for exactly this: `"validate": {"conformance"}` (surfaces memory
entries authored with `source:conformance`) and `"review": {"code-review"}` (surfaces entries
authored with `source:code-review`). `"validate"` is already consumed today by
`commands/dark-factory-validate.md` (a *different*, earlier gate) to let Gate 1 pre-empt known
conformance failures; `"review"` is defined but has zero consumers today.

Changes:

- **`commands/dark-factory-conformance.md`**, Phase 1 LOAD: add
  ```bash
  REPO_ROOT=$(git rev-parse --show-toplevel)
  MEMORY_CONTEXT=$(bash "${REPO_ROOT}/dark-factory/scripts/load_memory_context.sh" validate)
  ```
  (mirrors the existing `load_memory_context.sh <phase>` call pattern used by
  refine/plan/implement). If empty, proceed without it — same fail-open phrasing already used
  elsewhere.
- **`commands/dark-factory-code-review.md`**, Phase 1 LOAD: same, with phase `review`:
  ```bash
  MEMORY_CONTEXT=$(bash "${REPO_ROOT}/dark-factory/scripts/load_memory_context.sh" review)
  ```
- Both commands: pass `$MEMORY_CONTEXT` into the reviewer subagent prompt as a new
  `$MEMORY_CONTEXT` placeholder (distinct from `$ARTIFACT_CONTENT`/`$DIFF_CONTENT`), so the
  reviewer can visually separate "past advisory lessons" from "the thing being reviewed."
- **`refinement-skills/conformance-reviewer-prompt.md`** and
  **`refinement-skills/code-review-reviewer-prompt.md`**: add a `$MEMORY_CONTEXT` placeholder
  under `## Context`, with a short framing note that entries are advisory (past `[PATTERN]` /
  `[AVOID]` / `[FIX]` lessons from prior gate runs on similar files) and should inform — but
  never override — the spec/diff itself. Update the `## Input` list in each prompt to mention
  it.

No changes needed to `memory_retrieve.py`, `load_memory_context.sh`, `gate_lib.sh`, or the
`PHASE_SOURCE_MAP`/`PHASE_AGENT_ID` tables — the plumbing already exists; this only wires two
new callers into it.

### 4. Eval harness (`evals/token_opt_eval.py`)

No code changes. `ENFORCEMENT_SCENARIOS` already includes `conformance`/`code-review`
(`token_opt_eval.py:49`), and the per-issue loop already calls `eval_issue_scenario` for every
scenario in that list (`token_opt_eval.py:615-618`) — that is precisely why the 2026-07-03 eval
already has (zero-valued) rows for these two scenarios. Once section 1's registry fix lands, a
plain re-run (`python3 evals/token_opt_eval.py --clone-dir <repo-with-populated-components>`)
will produce real deltas with no further code changes.

### 5. Docs

Update `docs/dark-factory-token-optimization.md`'s "Active Features" description to note that
`architecture_md` (telemetry) and `memory_context` (live) are now tracked for
`conformance`/`code-review`, matching the registry change.

---

## Alternatives Considered

**A. Full live architecture-content injection into conformance/code-review reviewer prompts
(build the sliced-ARCHITECTURE.md consumption that doesn't exist even for `implement`).**
Rejected — exceeds what "parity with implement" actually means once you check what implement
does today (telemetry-only); doubles as new, unrequested functionality gated on issue #18's
still-open component-resolution work; none of the three acceptance criteria require it.

**B. Route raw issue comments (or a new digest) into conformance/code-review.** Rejected —
neither scenario loads comment content today, so there is no existing unoptimized baseline to
"route through an optimizer" (the entire mechanic this ticket is about); it would be net-new
context growth, not savings; for conformance specifically it risks re-surfacing scope the spec
deliberately excluded, fighting conformance's own scope-enforcement mandate. See Non-Goals.

**C. Leave conformance/code-review's live budget nodes without `--labels`/`--changed-files`
(strict parity with refine/plan's current unwired state).** Considered and rejected in favor of
wiring them (Approach §2) — the data is already fetched in both nodes, the change is
telemetry-only (no prompt-content risk), and without it the live cost-report would never agree
with what the eval measures. Flagged as an explicit judgment call above in case a reviewer
prefers strict parity instead.

---

## Non-Goals

- **Comment digest / issue-comment routing for conformance or code-review.** The issue's scope
  item 1 lists "comment digest" by analogy to `implement`, but (a) `comment_digest.py` is
  purpose-built around the factory-boundary-marker / `continue`-intent re-run cadence and has no
  natural home in these single-pass gates, (b) `implement` itself doesn't actually use the
  digest for `new`-intent runs (its registry row is raw `comments`, only read under `continue`
  intent) — so there is nothing to mirror, and (c) neither conformance nor code-review loads
  comment content today, so there's no existing baseline to optimize. If PR-thread / reconcile-
  dialogue context for these gates is ever wanted, it should be its own ticket.
- **Raising architecture component-resolution hit-rate.** That's issue #18. This ticket only
  makes conformance/code-review capable of participating in slicing when a component *does*
  resolve; it does not change resolution logic or rates.
- **Wiring `--labels`/`--changed-files` into `budget-refine`/`budget-plan`.** Pre-existing gap,
  out of scope; likely folds into #18.
- **Changing enforcement mode, per-scenario budgets, or diff-ranking behavior.** Unchanged, per
  issue scope item 4.

---

## Open Questions (non-blocking)

- Should the calibration re-run (`token_opt_eval.py --calibrate`) after this lands be against
  MarketHawk (the only repo with a populated `components:` map today) rather than dark-factory
  itself, given dark-factory's own `components: {}` guarantees 0% architecture savings on
  self-hosted runs regardless of this fix? (The eval's `--clone-dir` already defaults to
  `/workspace/markethawk`, suggesting this was always the intended target — but worth an
  explicit confirmation before the next factory run re-runs the eval.)
- Should `PHASE_AGENT_ID`'s `"validate"`/`"review"` labels in `memory-trace.json` be renamed to
  something clearer (e.g. `conformance-agent`/`code-review-agent`) now that they're actually
  consumed by the gates they're named for, or is the existing naming (chosen for an unrelated
  reason — `"validate"` reads conformance-sourced entries for the *validate* command) left
  as-is to avoid an unrelated rename? Purely cosmetic; does not block this ticket.

---

## Assumptions

- The `evals/results/token-opt-eval-2026-07-03.json` corpus (22 issues × 5 scenarios) was
  generated against a repo with a populated `ARCHITECTURE.md`/`components:` map (MarketHawk),
  not dark-factory-self, consistent with `token_opt_eval.py --clone-dir`'s default of
  `/workspace/markethawk`.
- `PHASE_SOURCE_MAP`'s `"validate"`/`"review"` phase keys in `memory_retrieve.py` were
  pre-provisioned for this exact use case (conformance/code-review self-referential memory
  reads) and are safe to reuse as-is, since `"review"` currently has zero consumers and
  `"validate"`'s existing consumer (`dark-factory-validate.md`) is unaffected by adding a second
  consumer of the same phase filter.
- Bench-suite quality regression testing (Acceptance criterion 3, "no conformance/code-review
  quality regression... spot pass^k") is satisfied by this change being additive-only to the
  reviewer prompts (an extra advisory `$MEMORY_CONTEXT` section that may be empty) and
  telemetry-only for architecture — no change to the conformance/code-review verdict logic,
  output format, or blocking thresholds.
