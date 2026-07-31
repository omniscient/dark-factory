# Gate `push-and-pr` and `status-in-review` on the conformance/code-review verdict file, not node completion

**Status:** design
**Date:** 2026-07-28
**Issue:** #271

## Problem

On 2026-07-13 (Fix run on #208), `validate`'s Phase 0 blast-radius gate
(`commands/dark-factory-validate.md` Phase 0) applied `needs-discussion` mid-run and exited 1.
`conformance` (Gate 2) and `code-review` (Gate 3) both observed the label and halted on their own
judgment rather than doing gate work. Despite that, `push-and-pr` still ran and created PR #270 —
an open (draft) PR for a run the factory itself had already decided needed human review, which then
sits invisible to every scheduler gate (#230, draft-PR blindness).

`push-and-pr` (`workflows/archon-dark-factory.yaml:989`) is `depends_on: [conformance]` with no
explicit `trigger_rule`, i.e. the default `all_success`. Mechanically that *should* have skipped
`push-and-pr` if `conformance` had failed. The root cause is that it didn't fail: `conformance` is
a `command:` node — a Claude agent turn, not a raw subprocess — and this run supplies direct,
reproduced evidence that a `command:` node's internal `exit 1` (run inside a Bash tool call during
the turn) does not reliably surface as the node's own completion/failure status to the DAG executor.
`validate`'s own Phase 0 already calls `exit 1` on `HUMAN_REQUIRED`
(`commands/dark-factory-validate.md:98`), and `conformance` depends on `validate` under the same
default `all_success` rule — if that `exit 1` had propagated, `conformance` would never have been
dispatched at all. It *was* dispatched (and chose, correctly per its own judgment, to halt), which
settles the issue's own "Question to answer first": the DAG's `depends_on` wiring is *shaped*
correctly, but it is trusting a completion signal that `command:` nodes cannot reliably produce.

This is the same bug class already fixed once in this repo: issue #212 found that a `command:` node
"closes the instant the agent ends its turn" and gets reported `dag_node_completed` (success)
regardless of internal logic, and fixed `refine-push`/`plan-push-and-advance` by gating on a durable,
independently-checkable artifact (a committed spec/plan file) instead of trusting node completion
(`docs/archive/2026-07-15-refine-plan-push-artifact-gate-design.md`). That design's own "out of
scope" section explicitly named this exact reproduction ("the implement-phase `push-and-pr` node has
the same unconditional-push shape... attributed to a separate tracked issue (#208)") — this issue is
that deferred follow-up.

## Requirements

(Settled via brainstorming with two product-owner passes; see rationale inline.)

1. **Do not attempt to fix `command:` node exit-code propagation.** That is Archon executor
   behavior this repo doesn't own (explicitly out of scope in the #212 design too). The fix must
   work entirely from durable signals the DAG's own `bash:` nodes can read.
2. **Gate on the verdict file each gate command already writes**, not a fresh subagent call.
   `commands/dark-factory-conformance.md` (Phase 2 step 2, Phase 4, Phase 5) and
   `commands/dark-factory-code-review.md` (Phase 1 step 2, Phase 6) already write a machine-readable
   `STATUS:` line via `emit_verdict()` (`scripts/gate_lib.sh:54`) to `$ARTIFACTS_DIR/conformance.md`
   and `$ARTIFACTS_DIR/review.md` respectively, inside the *same* container run `push-and-pr` and
   `status-in-review` execute in — unlike #212's spec/plan case, which spans separate container runs
   and therefore needed git-committed state, this signal can be a plain artifacts-dir file.
3. **Both edges are in scope, as one fix, not two tickets:**
   - `conformance` → `push-and-pr` (the literally-reported PR #270 symptom).
   - `code-review` → `status-in-review` (same bug class, one gate later). This is not scope creep:
     `commands/dark-factory-conformance.md:539` already states the intended halt contract as
     "prevents `push-and-pr` **and** `status-in-review` from running," and the issue's own "Why it
     matters" requires "no advance toward merge" — moving an issue to **In review** *is* the advance
     toward merge, and a code-review `BLOCKED` verdict happens strictly *after* `push-and-pr` has
     already (correctly) succeeded, so fixing only the conformance edge would still launder a
     code-review-blocked issue into "In review." Both edges also collapse into one small,
     reusable script rather than two divergent ad hoc checks.
   - Explicitly **not** in scope: the executor's own exit-code propagation, any rework of
     `trigger_rule`/`none_failed_min_one_success` semantics, and #230 (draft-PR blindness) itself —
     cross-referenced context only.
4. **Fail closed on ambiguity.** A missing or unparseable verdict file blocks, the same as an
   explicit `BLOCKED` — this is the actual shape of the reported bug (`conformance`'s ad hoc,
   CLAUDE.md-driven halt on a pre-existing label has no phase in `dark-factory-conformance.md` that
   writes `conformance.md`, so the reported run almost certainly produced no verdict file at all).
5. **Distinguish "already communicated" from "true silent death," but only for messaging, not for
   the pass/fail decision** (a correction from the #212 precedent, where the label *did* select the
   decision). Here, a missing verdict file always blocks either way; a live re-check of the
   `needs-discussion` label only decides whether the gate additionally posts its own failure
   comment (skip it if the label is already present — some upstream phase already explained why) or
   posts one (true silent death, nothing upstream explained anything).
6. **`resolve` intent must be unaffected.** `push-resolve` and `status-in-review`'s `resolve` path
   never produce a `conformance.md`/`review.md` in the first place; the new gate nodes must be
   `when`-restricted to `new`/`continue` exactly like `conformance`/`code-review` already are, so
   `resolve` runs never see them at all.

## Architecture

Two new linear `bash:` gate nodes, each a thin wrapper around one new generic script — the same
shape as the existing `enforce-budget-*` nodes wrapping `scripts/budget_gate.sh`, and sibling to
the #212-era `scripts/push_gate_check.sh`:

```
conformance ──▶ conformance-gate ──▶ push-and-pr ──▶ budget-code-review ──▶ ... ──▶ code-review ──▶ revise-advisory
                     │                                                                    │              │
              reads conformance.md                                                        └──▶ review-gate ◀┘
              STATUS: PASS|SKIPPED|ERROR → exit 0                                               reads review.md
              STATUS: BLOCKED|missing/unparseable → exit 1                                      same STATUS rule
                                                                                                       │
                                                                          push-and-pr ─────┐           ▼
                                                                          push-resolve ────┼──▶ status-in-review
                                                                          code-review ─────┤   (none_failed_min_one_success)
                                                                          revise-advisory ─┤
                                                                          review-gate ─────┘
```

### New script: `scripts/verdict_gate_check.sh`

```
Usage: verdict_gate_check.sh <verdict-file> <issue-number> <gate-label>
  <verdict-file>   e.g. $ARTIFACTS_DIR/conformance.md or $ARTIFACTS_DIR/review.md
  <issue-number>   for the live needs-discussion re-check and the silent-death comment
  <gate-label>     human string for the comment, e.g. "Conformance (Gate 2)"

Exit 0 (proceed) when the file exists and its STATUS: line is PASS, SKIPPED, or ERROR
  (ERROR only appears in review.md — code_review.fail_open's contract is "never block" per
  commands/dark-factory-code-review.md Phase 3 step 4 and the report node's own handling of it).
Exit 1 (block) when STATUS: is BLOCKED, or the file is missing/unparseable:
  - live-query the issue's current labels (fresh tracker call — $ARTIFACTS_DIR/issue.json
    predates any label a mid-run phase may have added, matching the #212 precedent's rationale
    for re-checking live rather than trusting the stale snapshot)
  - if BLOCKED, or missing+needs-discussion-present: exit 1 quietly — the originating phase
    (conformance Phase 5 / code-review Phase 6 / validate's blast-radius gate) already posted
    its own comment, applied needs-discussion, and moved the board to Blocked
  - if missing+needs-discussion-absent (true silent death, nothing upstream explained anything):
    post an idempotent marker-upsert comment via
    `tracker comment --id <n> --marker "<!-- df-push-gate-failure -->" --body-file <f>`
    (new marker — distinct from refine/plan's `<!-- df-refine-failure -->`, since this is a
    different failure family: implement/continue pipeline gates, not refine/plan artifact misses),
    then exit 1
```

Unlike `push_gate_check.sh` (which always exits 0 and lets its caller branch on stdout, because it
is *finding* an artifact for a node that does other work too), this script's own exit code **is**
the gate signal, so the calling node must not wrap it in `|| true`.

### DAG changes (`workflows/archon-dark-factory.yaml`)

- New node `conformance-gate`: `depends_on: [conformance]`, same `when` as `conformance`
  (`new`/`continue`), no `trigger_rule` (a plain linear default-`all_success` node — no change
  needed to `scripts/check_workflow_dag.py`'s `REQUIRED_OR_JOIN_NODES` sync tripwire).
  Body: `bash "${CLONE_DIR:-.}/dark-factory/scripts/verdict_gate_check.sh" "$ARTIFACTS_DIR/conformance.md" "$ISSUE" "Conformance (Gate 2)"`.
- `push-and-pr`: `depends_on` changes from `[conformance]` to `[conformance-gate]`. No other change —
  when `conformance-gate` fails, `push-and-pr` (and everything chained after it —
  `budget-code-review`, `code-review`, `revise-advisory`, `review-gate`) skips via the ordinary
  `all_success` cascade, matching the existing "`[push-and-pr] Skipped (trigger_rule)`" behavior the
  issue itself cites as the correct comparison case for a hard node failure.
- New node `review-gate`: `depends_on: [code-review, revise-advisory]`, same `when` as `code-review`
  (`new`/`continue`), no `trigger_rule`. `revise-advisory` always exits 0 itself (fail-open by design,
  `commands/dark-factory-revise-advisory.md` Phase 1 step 5: `STATUS != PASS → exit 0`), so this
  dependency is safe under plain `all_success`. Body: same script invocation against
  `$ARTIFACTS_DIR/review.md` and label `"Code Review (Gate 3)"`.
- `status-in-review`: `depends_on` changes from `[push-and-pr, push-resolve, code-review,
  revise-advisory]` to `[push-and-pr, push-resolve, code-review, revise-advisory, review-gate]`.
  The list is deliberately additive: keeping `code-review`/`revise-advisory` as direct dependencies
  preserves today's hard-failure blocking — an executor-level `code-review` failure must still
  block `status-in-review` directly, since cascade-skip failure propagation through `review-gate`
  at a `none_failed_min_one_success` join is unverified executor semantics. `trigger_rule:
  none_failed_min_one_success` and `when` stay exactly as-is — this OR-join already tolerates the
  `resolve` vs `new`/`continue` mutually-exclusive branches; adding the new gate as one more
  upstream edge doesn't change that shape, it only adds the missing block-on-`BLOCKED` case.
- `report`: **unchanged** (`depends_on: [status-in-review, code-review]`). When `review-gate` blocks
  `status-in-review`, `code-review` itself still "completed" (it always does, per the same
  exit-code-doesn't-propagate finding this whole ticket is about), so `report`'s OR-join still fires
  and posts the run summary showing the `BLOCKED` code-review section. When `conformance-gate`
  blocks earlier, the whole downstream chain including `code-review` is skipped, so `report` is
  skipped too and no summary posts — accepted (see Accepted trade-offs); `conformance`'s own Phase 5
  (or `validate`'s blast-radius Phase 0) already posted an explanatory comment in that case, and a
  true silent miss gets `verdict_gate_check.sh`'s own comment instead.

## Alternatives considered

1. **Fix it inline inside `push-and-pr`/`status-in-review` themselves** (mirroring #212's original
   *inline* shape, which patched `refine-push`/`plan-push-and-advance` directly rather than adding a
   separate node). Rejected: the halt here must cascade through more than one downstream node
   (`push-and-pr` *and* `status-in-review`, plus everything chained between them), and duplicating
   the same verdict-parsing/live-label-check/silent-death-comment logic inline in two unrelated
   nodes is more error-prone than one small reusable script consulted from two thin gate nodes. A
   dedicated gate node also gets the cascade "for free" from the existing default `all_success`
   trigger rule, with no changes needed to any of the nodes in between.
2. **Try to make the executor honor `command:` node internal exit codes.** Rejected per Requirement
   1 — out of this repo's control, and explicitly ruled out by the #212 precedent for the identical
   reason.
3. **Scope this ticket to the `conformance`→`push-and-pr` edge only, file the `code-review`→
   `status-in-review` edge as a follow-up.** Considered and rejected — see Requirement 3 for the
   full rationale (shared verdict-file mechanism, shared script, and the issue's own "no advance
   toward merge" bar covers both).

## Validation

- **New unit tests**, mirroring `tests/test_push_gate_check.py`'s convention for
  `scripts/push_gate_check.sh`: exercise `verdict_gate_check.sh` directly against a fixture
  `$ARTIFACTS_DIR` for each `STATUS:` value (`PASS`, `SKIPPED`, `ERROR`, `BLOCKED`), a missing file,
  and an unparseable file; assert the exit code and (for the missing-file branches) whether a
  `tracker comment --marker` call would fire, stubbing `tracker` the same way existing CLI-adjacent
  bash tests do.
- **New DAG-content test**, mirroring `tests/test_push_gate_dag.py`'s
  `TestPushGateNodes`/`test_dag_validator_passes` shape: assert `conformance-gate`/`review-gate`
  exist, call `verdict_gate_check.sh` against the right artifact path, check `needs-discussion`
  live, post the `<!-- df-push-gate-failure -->` marker on a true miss; assert `push-and-pr`'s
  `depends_on == ["conformance-gate"]` and `status-in-review`'s `depends_on == ["push-and-pr",
  "push-resolve", "code-review", "revise-advisory", "review-gate"]`; assert
  `python scripts/check_workflow_dag.py
  workflows/archon-dark-factory.yaml` (`check_workflow_dag.check()`) still returns no errors.
- **Manual (staging):** reproduce the original failure — force `conformance.md` to be absent (skip
  Phase 4/5 writes) with `needs-discussion` pre-applied, confirm `push-and-pr` is skipped and no PR
  is created; separately force `review.md` to `STATUS: BLOCKED`, confirm `status-in-review` is
  skipped and the issue stays on the board wherever `code-review` Phase 6 left it.

## Accepted trade-offs

- When `conformance-gate` blocks, the `report` run-summary comment does not post (the whole
  downstream chain, including `code-review`, is skipped). This mirrors the exact trade-off already
  accepted for the #212 fix (`refine-push`/`plan-push-and-advance` also skip their own summary on a
  gated miss) — the gate label/comment from the originating phase is the record of what happened,
  not `report`. Not fixed here to avoid widening this ticket into `report`'s own OR-join shape.
- `verdict_gate_check.sh` reuses one script for both gates rather than two smaller phase-specific
  scripts, trading a slightly more generic interface (`<verdict-file> <issue-number> <gate-label>`)
  for avoiding duplicated parsing/live-check/silent-death logic.

## Assumptions

- `scripts/factory_core/providers/cli.py`'s `tracker comment --marker` (idempotent upsert-by-marker)
  and `tracker get --id <n> --fields labels` behave as already relied on by `refine-push`/
  `plan-push-and-advance`/`close-preview` — no changes needed to that CLI.
- `commands/dark-factory-conformance.md`'s Phase 5 and `commands/dark-factory-code-review.md`'s
  Phase 6 already post their own Blocked comment, apply `needs-discussion`, and move the board to
  Blocked *before* their (unreliable) `exit 1` — confirmed by reading both files — so
  `verdict_gate_check.sh` does not need to duplicate that messaging on an explicit `STATUS: BLOCKED`
  read, only on the missing-file/true-silent-death path.
- The existing `exit 1` calls in `commands/dark-factory-conformance.md:539` and
  `commands/dark-factory-code-review.md:224` are left in place (harmless, and correct if a future
  executor change ever does propagate them) — not removed by this change, since removing them isn't
  required to fix the bug and doing so would be an unrelated cleanup.

## Open questions (non-blocking)

- Should `commands/dark-factory-conformance.md:539` and `commands/dark-factory-code-review.md:224`'s
  prose ("Exit non-zero — this prevents `push-and-pr`/`status-in-review` from running") be updated
  to note that the actual enforcement now lives in `conformance-gate`/`review-gate`, not the
  command's own exit code? Documentation-accuracy only, not required for correctness; can be folded
  into the same PR at low cost or left as a trivial follow-up.
