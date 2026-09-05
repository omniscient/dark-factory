# Shadow-run Claude Fable 5.1 as the conformance reviewer

**Issue:** #394
**Revised:** 2026-09-05 (operator amendment: R8 — no rubric edit; `.claude/skills/` is hard-excluded)

---

## Overview / Problem Statement

The conformance reviewer (`dark-factory-plan.md` Phase 3.5, plan-vs-spec; `dark-factory-conformance.md`
Phase 3, implementation-vs-spec) is a single-shot, input-heavy/output-light judgment step, pinned to
`claude-opus-4-8` today. It has a documented blind spot: five consecutive plans passed conformance while
missing the Task 0 spec-copy step (#387); #384 needed operator amendments at both gates; #196's plan
carries three deliberate divergences from its spec that only an operator adjudicated. Separately, the
plan node is 51% of factory spend across the last 80 runs, driven by architect reconcile cycles rather
than checker output.

This ticket trials Claude Fable 5.1 as a candidate replacement for the Opus-pinned conformance
reviewer — in **shadow mode only**: Fable runs alongside Opus with identical input, Opus alone gates,
and Fable's verdict is recorded for later comparison. It does not change which model gates, and it does
not touch the architect or product-owner pins, `gate_*`, breaker, budget, or tool-permission
configuration. A follow-up ticket (out of scope here) will compare ~10 plans' worth of Fable vs. Opus
verdicts against the operator's actual gate decisions and decide whether to flip the gate.

## Requirements

1. **Task 0 — availability check.** Before relying on Fable anywhere, confirm the Agent tool accepts
   `model: claude-fable-5-1` (the literal production pin string) under the factory OAuth token. If it
   is rejected, stop and report rather than silently falling back to Opus-only. (A preliminary
   interactive check during this refinement run confirmed the Agent tool's `model` parameter accepts a
   Fable identifier and returns successfully — but that check used the tool's short alias, not the
   literal `claude-fable-5-1` string the production command files pin. Task 0 must re-verify with the
   literal string in the real invocation path; treat the preliminary check as encouraging, not
   conclusive.)
2. **Shadow spawn mirrors every Opus spawn, including reconcile re-spawns.** At both integration points
   (`dark-factory-plan.md` Phase 3.5 step 5 and its reconcile loop step 8e;
   `dark-factory-conformance.md` Phase 3 step 4 and its Phase 3.5 reconcile loop step 6), spawn a
   second, shadow subagent immediately after the Opus subagent, with the identical rubric and identical
   `$ARTIFACT_KIND`/`$SPEC_CONTENT`/`$ARTIFACT_CONTENT` substitution Opus saw for that same cycle.
   First-pass-only shadowing would, on any ticket that reconciles, record a Fable verdict against a plan
   that no longer exists by the time the real gate decision is made — actively misleading data for the
   follow-up comparison. Cost is bounded: `conformance.max_reconcile_cycles` (default 3) already caps
   this at 4 shadow passes per gate in the worst case, ~1 in the common case.
3. **Non-interference — gate on Opus only.** The shadow subagent's output must never influence gating,
   the reconcile loop's verdict check, or Phase 3.6 out-of-scope excision:
   - Accumulate shadow output in a separate variable (`SHADOW_DIALOGUE`), never appended into
     `CONFORMANCE_DIALOGUE`. `dark-factory-conformance.md` Phase 3.6.2 scans `$CONFORMANCE_DIALOGUE`
     for `[OOS]` bullets to drive real excisions and spillover tickets; leaking Fable findings into
     that variable would let the shadow reviewer cause gating side effects.
   - A shadow spawn error, timeout, or refusal is recorded as `SHADOW_STATUS: UNCERTAIN` and never
     blocks, delays, or retries the Opus-gated flow.
4. **Structured, minable shadow fields — not prose-only.** The entire justification for shadow mode is
   the follow-up comparison; unstructured prose from a non-deterministic reviewer across ~10 runs is not
   practically minable. Emit, mirroring `emit_verdict`'s four-field shape:
   ```
   SHADOW_MODEL: <resolved model, e.g. claude-fable-5-1>
   SHADOW_STATUS: PASS|BLOCKED|UNCERTAIN
   SHADOW_FINDINGS_COUNT: <n>
   SHADOW_SEVERITY: none|low|medium|high|critical
   ```
   with a distinct `SHADOW_` prefix so `scripts/verdict_gate_check.sh` (`grep -m1 '^STATUS:'`) and
   `scripts/factory_core/verdict.py::parse_verdict` (`line.startswith("STATUS:")`, ignores unknown
   lines) are unaffected — never reuse the bare `STATUS:`/`GATE_TYPE:`/`SEVERITY:` prefixes for shadow
   data. Unparseable shadow output still emits the four lines with `SHADOW_STATUS: UNCERTAIN` (never
   omitted — a missing field is indistinguishable from "shadow never ran" and corrupts the follow-up's
   comparison denominator). Keep the raw Fable prose too, under a labelled "Shadow (Fable) Review"
   heading — the structured lines are for mining, the prose is what an operator reads when adjudicating
   a divergence.
5. **Durable placement, not just the ephemeral artifact.** `dark-factory-plan.md`'s "Plan Generated"
   comment already posts durably on every run; fold the shadow block into its existing
   `## Spec Conformance` section. `dark-factory-conformance.md` Phase 4 (PASS) today writes only to the
   per-run, container-local `$ARTIFACTS_DIR/conformance.md` and posts no issue comment at all — Phase 5
   (BLOCKED) is the only path that comments. Since PASS is the common case, conformance-phase shadow
   data written solely to `conformance.md` would be unminable exactly when it matters most. Add a small
   marker comment on the PASS path too, gated on the shadow having actually run, following the
   `<!-- dark-factory-cost-report -->` precedent (#48) with a distinct `<!-- df-shadow-review -->`
   marker.
6. **Verifier-contract update.** Replace `VERIFIER-CONTRACT.md`'s "always `claude-opus-4-8`" line with
   a per-checker pin table (unchanged for every existing pair; the plan/conformance conformance-reviewer
   pair additionally spawns a non-gating shadow subagent pinned to
   `${CONFORMANCE_SHADOW_MODEL:-claude-fable-5-1}` when that value is non-empty). Add the fail-closed
   clause: a refusal stop from any checker subagent — gating or shadow — maps to `UNCERTAIN`, never
   `PASS`. For gating checkers this slots into existing handling (refine: `UNCERTAIN:` →
   `needs-discussion`; plan/conformance: a refusal is treated as inconclusive, consuming a reconcile
   cycle rather than silently passing). For the shadow checker it maps to `SHADOW_STATUS: UNCERTAIN`
   per Requirement 4 and never blocks anything.
7. **Off switch, no deploy required.** `CONFORMANCE_SHADOW_MODEL` env var; empty disables the shadow
   spawn entirely (both integration points skip it, no `SHADOW_*` fields are emitted, no marker comment
   is posted). The trial defaults to *on*: `config.yaml`'s `conformance` block gains
   `shadow_model: claude-fable-5-1` as the baked default, consistent with the existing
   `# env: ... overrides` convention used elsewhere in that file. As with `main_red_autofix`, flipping
   the baked default in `config.yaml` requires an image rebuild and is *not* the trial's fast
   off-switch — the actual stop-the-trial lever is exporting `CONFORMANCE_SHADOW_MODEL=` (empty) via
   `.archon/.env`, which `entrypoint.sh` already reads into the container environment with no rebuild.
8. **Prompt hygiene — none in this ticket (operator amendment 2026-09-05).** The conformance rubric
   (`.claude/skills/conformance/RUBRIC.md`) and its byte-identical baked mirror
   (`refinement-skills/conformance-reviewer-prompt.md`) are **not** edited. `.claude/skills/` is in the
   adapter's `hard_exclude_paths` (`.factory/adapter.yaml`), so a factory implement cannot write there and
   the mirror must stay identical to it; and Requirement 2 depends on both models reading the identical
   rubric. Any rubric trim is a separate, evidence-based ticket after the shadow data exists.
9. **Out of scope** (restated from the issue, unchanged): which model gates (the follow-up flip); the
   architect or product-owner model pins; any `gate_*`, breaker, budget, or tool-permission change; the
   exact wording of a future, evidence-based rubric trim.

## Architecture / Approach

**Integration points (unchanged Opus flow, shadow spawn appended after):**

- `commands/dark-factory-plan.md` Phase 3.5 step 5 (first pass) and reconcile loop step 8e (each
  cycle): after the existing Opus subagent spawn and verdict parse, spawn a second subagent:
  - `description`: `"Conformance shadow (fable): plan vs spec (cycle N)"`
  - `model`: `${CONFORMANCE_SHADOW_MODEL:-claude-fable-5-1}` — skip the spawn entirely if this resolves
    to empty
  - `prompt`: the same resolved `RUBRIC_CONTENT` with the same `$ARTIFACT_KIND`/`$SPEC_CONTENT`/
    `$PLAN_CONTENT` substitution used for the Opus call this cycle
  - Read access: `Glob`/`Grep`/`Read`, per the checker-invocation contract
  - Append output to `SHADOW_DIALOGUE` (separate from `CONFORMANCE_DIALOGUE`) with the same
    `Cycle N:` header convention, so shadow cycles align one-to-one with Opus cycles for the follow-up
    comparison.
- `commands/dark-factory-conformance.md` Phase 3 Step 3.1 step 4 (first pass) and Phase 3.5 reconcile
  loop step 6 (each cycle): identical pattern, `$ARTIFACT_KIND=IMPLEMENTATION`.
- The shadow spawn is wrapped in error containment at both sites: any tool error, timeout, or refusal
  produces `SHADOW_STATUS: UNCERTAIN` with an empty or best-effort `SHADOW_FINDINGS_COUNT`/
  `SHADOW_SEVERITY`, and execution continues to the (unaffected) Opus verdict handling.

**Artifact and comment changes:**

- `dark-factory-conformance.md` Phase 4 (PASS) and Phase 5 (BLOCKED): after the existing
  `emit_verdict`/`VERDICT:`/`CYCLES:`/... lines, if the shadow ran this run, append the four
  `SHADOW_*` lines from Requirement 4, then `\n---\n\n` and `$SHADOW_DIALOGUE` under a
  `## Shadow (Fable) Review` heading — written to `$ARTIFACTS_DIR/conformance.md` exactly like the
  existing Opus dialogue block, just namespaced separately.
- `dark-factory-conformance.md` Phase 4 (PASS) additionally posts a small `gh issue comment` (only when
  the shadow ran) carrying the `SHADOW_*` fields and the shadow verdict line, marked
  `<!-- df-shadow-review -->`, so the common (PASS) case is minable — mirroring why
  `<!-- dark-factory-cost-report -->` exists. Phase 5 (BLOCKED) already posts a comment; fold the same
  block into it instead of adding a second comment.
- `dark-factory-plan.md` Phase 4: fold the `SHADOW_*` fields and the full `$SHADOW_DIALOGUE` into the
  existing "Plan Generated" comment's `## Spec Conformance` section, under a
  `### Shadow (Fable) Review` subheading — this comment already posts durably every run, so no new
  comment path is needed here.

**Verifier contract (`refinement-skills/VERIFIER-CONTRACT.md`):**

- Replace the single "always `claude-opus-4-8`" line with a per-checker table: refine/product-owner,
  plan/architect, code-review/reviewer stay `claude-opus-4-8` only; plan Phase 3.5 and conformance
  Phase 3's conformance-reviewer pair are `claude-opus-4-8` (gate) **+** shadow
  `${CONFORMANCE_SHADOW_MODEL:-claude-fable-5-1}` (advisory, skipped if empty).
- Add: "A refusal stop from any checker subagent — gating or shadow — maps to `UNCERTAIN`, never
  `PASS`." This is a general robustness fix to the existing contract (applies to every checker, not just
  the new shadow one) bundled into this ticket per the issue's own scope item 3.

**Config (`config/config.yaml`, `conformance:` block):**

```yaml
conformance:
  ...
  shadow_model: claude-fable-5-1  # env: CONFORMANCE_SHADOW_MODEL overrides — empty = no shadow
```

**Task 0:** first step of the plan or implement phase for this ticket, spawn a trivial one-line-reply
subagent with the literal `model: claude-fable-5-1` under the factory OAuth token; if rejected, stop and
report via the existing `UNCERTAIN`/`needs-discussion` path rather than proceeding with a silent
Opus-only fallback.

## Alternatives Considered

1. **Shadow only the first-pass artifact, skip reconcile re-spawns.** Rejected — on any ticket that
   reconciles, this records a Fable verdict against a plan/diff that no longer exists by the time Opus's
   real gate decision is made, which is actively misleading for the follow-up comparison rather than
   merely incomplete.
2. **Prose-only shadow section, no structured fields.** Rejected — the entire point of a shadow trial is
   the eventual comparison; regexing free prose from a non-deterministic reviewer across ~10 runs in
   practice means the comparison is done by hand or not at all.
3. **Trim the rubric's carve-out paragraphs now, to reduce Fable's prompt-following burden.** Rejected —
   the carve-outs (especially the security-sensitive one) exist to prevent under-flagging, which is
   exactly the gate's documented failure mode; shortening them as a side effect of a model trial violates
   CLAUDE.md's "never weaken safety gates as a side effect of another change." "Over-prescriptive" is
   also better evidenced empirically, after real shadow output exists.
4. **A Fable-specific rubric variant, distinct from the one Opus reads.** Rejected — the issue explicitly
   requires "identical rubric/input" so the two verdicts are comparable on the same basis; a fork would
   undermine the trial's own methodology.

## Open Questions (non-blocking)

- The exact wording/diff for a genuine "over-prescriptive for Fable" rubric trim is deferred to the
  follow-up (exit-criteria) ticket in Requirement 9, once real shadow output exists to point at.
- Whether the conformance-phase PASS-path marker comment should be suppressed once the trial concludes
  (flip or revert) is left to that follow-up ticket's disposition, not this one.

## Assumptions (flagged)

- `claude-fable-5-1` is accepted as a literal `model:` value by the production Agent-tool spawn path
  under the factory OAuth token. A same-session interactive check using the tool's short alias
  succeeded, which is encouraging but not conclusive for the literal ID string the command files will
  actually pin — Task 0 (Requirement 1) must confirm this for real before the shadow spawns are relied
  upon.
- `$ARTIFACTS_DIR` is per-run and container-local (`entrypoint.sh:140`), which is why Requirement 5's
  durable issue-comment channel is necessary for conformance-phase shadow data on the common (PASS)
  path.
- `conformance.max_reconcile_cycles` (default 3) is treated as the de facto cap on shadow spawns per
  gate too; this ticket does not introduce a separate shadow-cycle cap for what is a time-boxed trial.
