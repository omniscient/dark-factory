# Docs conformance gate — verify documentation against the final implementation diff before PR publication

**Issue:** #535

---

## Overview / Problem Statement

Dark Factory has no mechanism that checks whether documentation actually describes a
change correctly. The only existing hook is `commands/dark-factory-implement.md` Phase 4
DOCUMENT (#55): it classifies `git diff main...HEAD --name-only` paths against a fixed
mapping table and updates the matched doc section — but if a path matches nothing, Phase
4 **silently skips**, with no accuracy check and no coverage check. Four reference docs
landed in #520–#523; later behavior changes in #519/#524/#525 drifted them, requiring a
separate catch-up PR (#533, docs-conformance's own motivating example). Nothing in the
pipeline would have caught that drift before merge, and nothing stops it recurring.

Both existing quality gates are diff-blind to documentation content: Gate 2
(`commands/dark-factory-conformance.md`, code-vs-spec) and Gate 3
(`commands/dark-factory-code-review.md`, post-publish code review) both build their diff
with `':!docs/*.md'` in the pathspec (Step 3.0.1 / mirrored in code-review) — the
`docs/` directory's Markdown content never reaches either reviewer (confirmed by the
`#399` exclusion-token tests, `tests/test_gate_diff_md_visibility.sh` et al. — note those
tests confirm `commands/*.md` policy Markdown is *not* excluded today, only `docs/*.md`
is). Gate 3 also runs after the PR is already published, so it is structurally the wrong
place to block an inaccurate-docs merge pre-publication even if its diff were widened.

This spec adds a **third, independent, read-only gate** — "Docs Conformance (Gate
2.5)" — that runs after Gate 2 and before `push-and-pr`, sees the full post-Gate-2 diff
(code, config, workflow, and policy Markdown *and* `docs/**`), and blocks the PR from
being published when the proposed documentation doesn't actually describe that diff, or
when a "no documentation impact" claim isn't defensible.

## Requirements (from Q&A)

1. **Own command + DAG node**, not a phase folded into Gate 2's session (Q&A 1):
   `commands/dark-factory-docs-conformance.md`, DAG node id `docs-conformance`, gated by
   a `docs-conformance-gate` bash node — mirroring the Gate 2/Gate 3 one-command-one-node
   pattern. Reasons: Gate 2's session has a reconcile loop that edits code, which would
   make a folded-in docs check neither independent nor read-only; `conformance.md`'s
   `STATUS:` line already has three consumers (`verdict_gate_check.sh`,
   `budget-conformance` telemetry, the `conformance:` config block) that all assume one
   verdict per file.
2. **Single-pass, read-only, no reconcile loop** (Q&A 2): the command spawns exactly one
   Opus-pinned reviewer subagent (Glob/Grep/Read only), parses its verdict, and writes
   `$ARTIFACTS_DIR/docs-conformance.md` once. No `max_reconcile_cycles`-equivalent knob.
   On material mismatch it posts evidence and exits; the branch stays unpublished; the
   only remediation path is a human edit or a subsequent `Continue` run, which reruns
   implement Phase 4 DOCUMENT, Gate 2, and this gate from scratch. The command's own
   session makes no edits/commits/pushes beyond writing its verdict file and posting its
   comment.
3. **Fail-closed verdict gate, not `verdict_gate_check.sh` reused unchanged** (issue
   body + Q&A 1): a new `scripts/docs_verdict_gate_check.sh` (or an explicit mode flag —
   see Architecture) that treats only `STATUS: PASS` or `STATUS: SKIPPED` as "proceed."
   `BLOCKED`, `ERROR`, and missing/unparseable all fail closed. This deliberately diverges
   from `verdict_gate_check.sh`'s existing PASS/SKIPPED/ERROR-all-proceed behavior, which
   is correct only for Gate 3's documented `code_review.fail_open: true` contract and is
   not reusable here unchanged.
4. **Doc-impact handoff, audited not trusted** (Q&A 3a): implement's Phase 4 DOCUMENT
   writes a durable `$ARTIFACTS_DIR/doc-impact.md` — per changed behavior, the doc
   file+section updated, or (when nothing is affected) an explicit `NO_IMPACT:` statement
   with reasons — plus a one-line pointer in `implementation.md`. The docs-conformance
   reviewer reads `doc-impact.md` only as a claim to audit: it independently derives what
   the diff requires first, then checks the claim against that independent view, so a
   false `NO_IMPACT:` claim is itself a reportable mismatch. Missing/empty/incomplete
   `doc-impact.md` fails closed as `BLOCKED`.
5. **Diff scope**: the reviewer's diff is `git diff main...HEAD` with the *same*
   exclusions Gate 2/3 use for noise (`*.lock`, `evals/*.md`, `bench/*.md`,
   `.archon/memory/**`) **minus** the `':!docs/*.md'` token — i.e. it is the one gate in
   the pipeline that can see doc content, specifically so it can compare doc claims
   against the code/config/workflow/policy-Markdown diff Gate 2/3 already see.
6. **Scope coverage**: both target structural docs (the existing Phase 4 mapping:
   `ARCHITECTURE.md`, `PROJECT_STRUCTURE.md`, `ENV_VARIABLES.md`, `README.md`,
   `CLAUDE.md` where touched) and Dark Factory's own reference docs (`docs/run-lifecycle.md`,
   `docs/scheduler.md`, `docs/features.md`, `docs/configuration.md`), plus policy Markdown
   (`commands/**`, `.claude/skills/**` rubrics) when its diff changes documented behavior.
7. **Must not fight the existing OOS doc exemption**: Gate 2's Phase 3.6.0 already treats
   every doc-file change as in-scope housekeeping that is never excised or ticketed. This
   gate only judges *accuracy*, never scope — it must not reintroduce doc-file OOS
   ticketing or excision.
8. **Naming**: branded "Gate 2.5" (gate-label string `"Docs Conformance (Gate 2.5)"`), not
   a renumbering of Gate 3 → Gate 4 (Q&A 3b) — the literal `"Code Review (Gate 3)"` string
   and "Gate 3" prose appear in enough call sites and tests that renumbering would be
   pure, high-risk churn for no behavioral benefit.
9. **`new`/`continue` only**, same as Gate 2/3 — `resolve` does not run implementation and
   has no diff to check.
10. **Remediation reruns both gates**: a subsequent `Continue` run must rerun Gate 2
    *and* this gate (never let a docs-only fix skip Gate 2) — satisfied automatically by
    both gates being plain DAG nodes on the `new`/`continue` branch with no run-level
    memoization.
11. **Tests**: cover a policy-Markdown-only change, a scheduler/config/artifact change, an
    ordinary target structural change, a docs-neutral change (defensible `NO_IMPACT`), and
    a deliberately misleading-but-present doc update — plus the gate's own fail-closed
    paths (missing/malformed/`ERROR`/timeout/refusal all block; no PR created in any case).
12. **Document the gate** in `docs/run-lifecycle.md`, `docs/features.md`,
    `docs/configuration.md`, and `/opt/refinement-skills/VERIFIER-CONTRACT.md`.
    `docs/scheduler.md` and `CLAUDE.md` are explicitly out of scope (Q&A 3b) — the
    scheduler never sees per-gate nodes and no new board status/label is introduced.

## Architecture / Approach

### New DAG nodes (`workflows/archon-dark-factory.yaml`)

Insert between the existing `conformance-gate` and `push-and-pr` nodes, `new`/`continue`
only:

```
conformance -> conformance-gate -> budget-docs-conformance -> enforce-budget-docs-conformance
  -> docs-conformance -> docs-conformance-gate -> push-and-pr
```

- `budget-docs-conformance` / `enforce-budget-docs-conformance`: bash nodes, identical
  shape to `budget-conformance`/`enforce-budget-conformance` (telemetry via
  `context_budget.py --scenario docs-conformance`, enforcement via
  `budget_gate.sh docs-conformance`), both already generic over `--scenario`.
- `docs-conformance`: `command: dark-factory-docs-conformance`, `denied_tools: []`
  (side_effect_level 5, matching every other phase today per `config/config.yaml`'s
  documented "tightening is a separate change" note), `depends_on:
  [enforce-budget-docs-conformance, conformance-gate]`, `when` identical to
  `conformance`'s.
- `docs-conformance-gate`: bash node running `scripts/docs_verdict_gate_check.sh
  "$ARTIFACTS_DIR/docs-conformance.md" "<issue-num>" "Docs Conformance (Gate 2.5)"`,
  `depends_on: [docs-conformance]`.
- `push-and-pr`'s `depends_on` gains `docs-conformance-gate` alongside `conformance-gate`.

### `scripts/docs_verdict_gate_check.sh`

A sibling of `verdict_gate_check.sh`, not a parameterized variant of it — the two have
different accept-lists for a specific, previously-documented reason (`ERROR` is Gate 3's
`fail_open` contract, not a generic "checker had trouble" signal), and conflating them
behind a flag risks a future edit flipping the flag and silently reintroducing
pass-through for a gate that must never have it. The new script:

- Reads the same `STATUS:` line out of `$ARTIFACTS_DIR/docs-conformance.md`.
- Treats `PASS` or `SKIPPED` as proceed (exit 0).
- Treats `BLOCKED`, `ERROR`, or missing/unparseable as block (exit 1), with the same
  live `needs-discussion` messaging-only check and idempotent
  `<!-- df-push-gate-failure -->` marker comment pattern as `verdict_gate_check.sh`
  (shared logic factored into `gate_lib.sh` if the duplication grows past this one
  difference — initial implementation may simply copy the ~60-line script and change the
  one `case` statement, per plan-phase judgment).

### `commands/dark-factory-docs-conformance.md`

Single-pass structure (no Phase 3.5 reconcile, unlike `dark-factory-conformance.md`):

1. **LOAD**: read `.claude/skills/refinement/config.yaml`'s `docs_conformance` block. If
   `docs_conformance.enabled: false` → write `STATUS: SKIPPED` and exit (mirrors Gate 2's
   kill-switch). Read `/opt/refinement-skills/VERIFIER-CONTRACT.md` for the model pin.
   Read `$ARTIFACTS_DIR/doc-impact.md` (the Phase 4 DOCUMENT handoff) — missing/empty is
   not an immediate abort here; it's passed to the reviewer as part of the evidence to
   audit, and an empty/missing file is itself a finding the reviewer can cite.
2. **BUILD DIFF**: `git diff main...HEAD -- ':!*.lock' ':!evals/*.md' ':!bench/*.md'
   ':!.archon/memory/**'` — i.e. Gate 2/3's exclusion list minus `':!docs/*.md'`. Also
   collect the baseline (`git show main:<path>`) and branch versions of every doc file
   the diff touches, so the reviewer can see before/after doc text directly rather than
   only a unified diff hunk.
3. **RENDER + SPAWN**: render a new `docs-conformance-reviewer-prompt.md` (baked under
   `/opt/refinement-skills/`, clone-live-first override under
   `.claude/skills/docs-conformance/RUBRIC.md` per the VERIFIER-CONTRACT clone-live-first
   pattern) with the diff, the doc before/after pairs, and `doc-impact.md`'s content.
   Spawn one subagent: `model: opus` (→ `claude-opus-5-5` per VERIFIER-CONTRACT), Glob/
   Grep/Read only, asking it to judge (a) is every doc claim about the change true of the
   actual diff, (b) is any affected behavior left undocumented, (c) if `doc-impact.md`
   claims `NO_IMPACT`, is that defensible given the independent view the reviewer formed
   in step (a)/(b). No Fable shadow (VERIFIER-CONTRACT only pairs shadows with the two
   conformance-reviewer pairs; this is a third, structurally simpler pair).
4. **PARSE VERDICT**: `✅ Conforms`/`⚠️ Minor deviations` → PASS; `⛔ Material
   divergence` → BLOCKED; unparseable/refusal/timeout/tool-error → `UNCERTAIN` → BLOCKED
   (never PASS, per VERIFIER-CONTRACT's refusal rule — and unlike Gate 2/3, there is no
   reconcile cycle to consume first, so this maps directly to BLOCKED on this one pass).
5. **WRITE VERDICT**: `scripts/gate_lib.sh::emit_verdict "docs-conformance" <STATUS>
   <count> <severity>` into `$ARTIFACTS_DIR/docs-conformance.md`, followed by the raw
   reviewer dialogue (same shape as `conformance.md`/`review.md`).
6. **ON BLOCKED**: post a `<!-- df-docs-conformance-blocked -->`-marked comment listing
   each mismatch (doc file+section, the false/missing claim, the contradicting diff
   hunk, a suggested correction), move the board to the state Gate 2's BLOCKED path uses
   (whatever `conformance`'s Phase 5 does today — reuse verbatim, not a new taxonomy),
   and add `needs-discussion` — per Requirement 10/12, no new label or status.
7. Exit (0 on PASS/SKIPPED path for forward-compat with the DAG node that actually gates;
   non-zero on BLOCKED, same forward-compat posture `dark-factory-conformance.md` Phase 5
   documents for its own `exit 1`).

### `commands/dark-factory-implement.md` Phase 4 changes

- Extend the path-classification step to also derive impact from **diff content**, not
  only changed paths: for files matched by the existing mapping table, Phase 4 behaves as
  today; additionally, Phase 4 must scan the diff for behavior changes with no mapping
  row (config semantics, a gate's accept-list, scheduler timing, a new/changed env var,
  policy Markdown under `commands/**`) and either update the relevant doc or record a
  reasoned no-impact rationale.
- Write `$ARTIFACTS_DIR/doc-impact.md`: one entry per changed behavior
  (`file -> doc updated at <doc>#<section>` or `file -> NO_IMPACT: <reason>`), plus an
  overall `NO_IMPACT: <reason>` line if literally nothing in the diff needed any doc
  change. Add a one-line pointer to it in `implementation.md`.
- Preserve the existing auto-generated-docs boundary (`docs/database-schema.md` etc. —
  never edit).

### Config (`.claude/skills/refinement/config.yaml`)

```yaml
docs_conformance:
  enabled: true
  block_on_material: true   # mirrors conformance's knob name for consistency; no cycles knob (single-pass)
```

`token_optimization.budgets`/`.enforce` gain a `docs-conformance` scenario entry
(hyphenated, matching the existing `code-review` scenario naming, not the config
section's own `docs_conformance` underscored key — the budget scenario string and the
config section key are already inconsistent for `code_review`/`code-review` today, so
this follows existing precedent rather than inventing new consistency).

### Interaction with Gate 2's OOS doc exemption

No change needed to `dark-factory-conformance.md` Phase 3.6.0/3.6.2 — they already treat
every doc-file OOS finding as in-scope housekeeping. This spec adds a gate that judges
*accuracy* downstream of that exemption; the two mechanisms don't overlap (one asks "is
this doc change allowed to exist," the other asks "is this doc change correct").

## Alternatives Considered

1. **Widen Gate 3's diff to include `docs/*.md` and add a docs dimension to its existing
   reviewer rubric**, instead of a new gate. Rejected: Gate 3 runs after the PR is
   already published (`push-and-pr` → `code-review`), so by the time it could flag a
   docs mismatch the branch is already public and mergeable-looking; the issue explicitly
   requires blocking *before* `push-and-pr`. It would also mean a single reviewer session
   is responsible for both code-quality judgment and docs-accuracy judgment, diluting
   both, and `review.md`'s `code_review.fail_open: true` advisory-on-error contract is
   wrong for a gate meant to fail closed.
2. **Deterministic-only checker** (lint/link-check/changed-path-matching, no LLM judge).
   Rejected: the issue explicitly asks whether doc *claims* are true of the actual
   behavior change — a semantic judgment no deterministic check (no existing link/lint
   tooling exists in this repo today, confirmed by `scripts/` having none) can make. Kept
   as a non-blocking supplement (see Open Questions).
3. **Fold the doc check into Gate 2 as an added phase**, sharing its reconcile loop and
   verdict file. Rejected per Q&A 1/2: breaks the "independent, read-only" requirement
   (Gate 2's session edits code) and the "one verdict per file" assumption three existing
   call sites already make.
4. **Give the new gate its own reconcile loop** (agent edits docs, re-checks, up to N
   cycles) like Gate 2's code-fix loop. Rejected per Q&A 2: an in-run doc edit would land
   after `conformance-gate` already passed, bypassing Gate 2 for exactly the kind of
   behavior-defining policy-Markdown change the issue puts in scope; and a checker that
   edits what it then re-grades is the self-attestation the issue explicitly rejects.
5. **Renumber Gate 3 → Gate 4** so the new gate is cleanly "Gate 3." Rejected per Q&A 3b:
   churns many existing literal "Gate 3" strings (DAG comments, test assertions, docs,
   `commands/dark-factory-code-review.md`) for no behavioral benefit; "Gate 2.5" is
   accurate (it runs between Gate 2 and Gate 3) and minimizes blast radius.

## Open Questions (non-blocking)

- Whether to eventually add deterministic supplementary checks (markdown link-check,
  anchor-reference validation) alongside the semantic reviewer — no such tooling exists
  in this repo today; the issue frames these as a supplement, never a substitute, so this
  can land as a later, separate enhancement without blocking this gate's core semantic
  check.
- Whether `docs_verdict_gate_check.sh`'s duplication with `verdict_gate_check.sh` (both
  ~60-line scripts differing only in their accept-list and messaging strings) should be
  refactored into one script with an explicit `--no-error-passthrough` style flag once a
  third consumer appears — deferred rather than decided now, to avoid the flag itself
  becoming a future accidental-pass-through vector for a two-consumer refactor nobody
  asked for yet.
- Exact current line numbers for every doc edit site cited in this spec's Architecture
  section (and in Q&A 3b) should be re-derived from `main` at plan/implement time rather
  than trusted verbatim — a known recurring failure mode for long-lived reference docs
  (prior lesson from issue #182's re-refinement, `.archon/memory/architecture.md`).

## Assumptions (flagged)

- The new gate's reviewer subagent, like Gate 2/3's, is pinned to `claude-opus-5-5` via
  the Agent tool's `opus` alias, per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s
  existing checker-invocation contract, which this spec extends with a new row rather
  than inventing a separate pinning mechanism.
- `scripts/gate_lib.sh::emit_verdict` and `scripts/factory_core/verdict.py`'s
  STATUS/GATE_TYPE/FINDINGS_COUNT/SEVERITY schema are reused unchanged — this spec adds a
  new *gate-check* script (acceptance criterion), not a new verdict schema.
- `doc-impact.md`'s exact field format is left to plan/implement to finalize against
  `implementation.md`'s existing conventions; this spec fixes its *contract* (per-change
  doc-or-no-impact entries, audited not trusted) but not its literal grammar.
- This ticket's own branch necessarily touches gate-adjacent surfaces
  (`workflows/archon-dark-factory.yaml`, a new `scripts/docs_verdict_gate_check.sh`,
  `config/config.yaml`-equivalent `.claude/skills/refinement/config.yaml`, and
  `VERIFIER-CONTRACT.md`). Per `CLAUDE.md`'s hard limits, this is acceptable because the
  change is going through the standard human-reviewed `spec-pending-review` → plan →
  implement → conformance → code-review pipeline (the "own reviewed ticket" CLAUDE.md
  requires for gate changes), not arriving via an unreviewed comment-channel shortcut —
  but the human spec reviewer should scrutinize this list specifically before approving.
