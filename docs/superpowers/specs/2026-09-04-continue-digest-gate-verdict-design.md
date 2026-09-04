# Continue-run comment digest must surface factory-posted gate-verdict findings

**Issue:** #354 · **Depends on:** none · **Status:** spec-pending-review
**Date:** 2026-09-04

## Overview

`scripts/comment_digest.py` builds the deterministic, LLM-free feedback digest that
`commands/dark-factory-implement.md`'s continue path trusts as its "complete feedback
source" (line 69: *"do not separately re-read the raw arrays it was built from"*). Its
bot-noise filter (`_BOT_RE` / `_is_factory_comment`) treats *any* issue comment carrying a
factory footer marker (`*Posted by <product> Dark Factory*`, etc.) as noise to exclude from
human feedback.

That same footer is used by the Gate‑3 code-review "Code Review — Blocked" comment
(`commands/dark-factory-code-review.md:159`) and the Gate‑2 conformance "Spec Conformance —
Blocked" comment (`commands/dark-factory-conformance.md:503`) — the two comments that carry
the concrete finding a continue run exists to fix. Because they're posted by the factory
itself, they get classified as noise like a routine checkpoint comment: when a gate-verdict
comment is the most recent comment in the thread (the common case right after a block), it
becomes the digest's boundary marker (`last_factory_idx`) and is therefore both excluded from
`human_comments` *and* has nothing after it — the digest emits `"No human feedback found
after last factory marker."` The `summarize-feedback` DAG node (`workflows/archon-dark-
factory.yaml:161`) then collapses this to `{"summary": "No specific feedback found."}`, and
the continue run resumes blind.

PR #292 already fixed the equivalent problem for **inline** PR review comments (they are now
"kept in FULL, never boundary-filtered" per the comment at `scripts/comment_digest.py:143-
149`, with a regression test at `tests/test_comment_digest.py:254`). This spec closes the
remaining gap: the **issue-level** gate-verdict comment itself.

## Requirements

### R1 — New `_is_gate_verdict(body)` predicate, distinct from `_is_factory_comment`

Matches a comment body that:
1. Starts (after stripping leading whitespace) with one of the literal headings currently
   reachable via a `continue`-intent dispatch:
   - `## Code Review — Blocked` (`commands/dark-factory-code-review.md:159`)
   - `## Spec Conformance — Blocked` (`commands/dark-factory-conformance.md:503`) — this
     prefix-match also incidentally matches `commands/dark-factory-plan.md:140`'s `## Spec
     Conformance — Blocked (Plan)`, which is harmless dead code: that comment is posted only
     during the `plan` intent's internal reconcile loop, and `digest-comments` only runs
     `when: "$parse-intent.output.intent == 'continue'"` (`workflows/archon-dark-
     factory.yaml:153`), so it can never reach `comment_digest.py` in current usage.
2. **AND** contains the "factory" footer marker (`scripts/factory_core/identity.py`'s
   `marker("factory")`, the same string both templates end with).

Both conditions are required so a human quoting the heading text back in their own comment
(e.g. "the `## Code Review — Blocked` message is wrong about X") is never misrouted through
the gate-verdict path instead of the normal human-feedback path.

### R2 — Gate-verdict inclusion is position-independent (no boundary filtering)

`build_digest()` scans the full `comments` array (not just `comments[last_factory_idx+1:]`)
for the **latest** comment matching `_is_gate_verdict`, and — if found — always includes it in
the digest output, regardless of whether it falls before, at, or after `last_factory_idx`.

This mirrors the existing inline-comment carve-out exactly (`scripts/comment_digest.py:145-
149`) and, unlike an approach that only recovers a gate-verdict comment when it happens to
land at `last_factory_idx` itself, does not depend on knowing whether some other
factory-authored comment (e.g. the `report` DAG node, `workflows/archon-dark-
factory.yaml:1210`) posts *after* a Gate‑3 block within the same run.
That ordering is unresolved — a prior design doc for the same `none_failed_min_one_success`
join explicitly flags it as **"unverified executor semantics"**
(`docs/archive/2026-07-28-conformance-review-gate-node-design.md:151`) — so the fix must be
correct under either ordering, not tuned to one guess. See Alternatives considered (#1).

`last_factory_idx` / `boundary_ts` computation itself is **unchanged** — it still considers
gate-verdict comments as candidate boundary markers exactly as before, and still drives the
existing `pr_reviews` timestamp filter. Only what gets *included as feedback* changes, never
what other filters use as their cutoff.

### R3 — Two degenerate paths must also check for a gate verdict

Two branches in `build_digest()` currently short-circuit straight to a "no feedback" sentinel
without knowing a gate verdict exists:
- The `no_boundary` branch (`scripts/comment_digest.py:152-162`, no factory comment ever seen).
- The empty-sentinel branch (`not human_comments and not reviews and not inline`,
  `scripts/comment_digest.py:167-172`).

Both must additionally check `_is_gate_verdict` across the full comment array (per R2) before
falling back to the "no feedback" sentinel text.

### R4 — Rendered as its own labeled section, after the human sections

The gate-verdict content is rendered under its own heading, e.g. `### Gate verdict (factory-
posted, action required)`, placed **after** the "Issue comments" / "PR review comments"
sections in `_feedback_sections()`'s output order. This makes "human comments keep priority"
(the issue's own framing) a structural fact of the digest's layout, not incidental ordering.
Both are always included when both are present — the continue agent needs the concrete gate
finding regardless of whether a human also commented.

### R5 — No staleness/supersession guard (explicitly out of scope)

Because R2 makes inclusion position-independent, a `continue` run dispatched long after a
gate-verdict was already resolved by a later, unrecorded round could in principle resurface a
stale finding. This spec does not add a guard for that case:
- The issue's fix direction is scoped to eliminating the false negative ("the gate finding
  must never be dropped"); it does not raise staleness as a concern.
- Today's dispatch flow makes staleness rare in practice: a Gate‑3/Gate‑2 block adds
  `needs-discussion`, which halts all automation (CLAUDE.md, label semantics); the next
  `continue` dispatch on that issue is a direct, deliberate response to that specific block,
  not an unrelated later event.
- A guard would need to reason about the same unresolved `report`-node timing question R2 is
  designed to be robust against either way (see Open questions).

This is recorded as an accepted limitation, not silently dropped — see Open questions.

### R6 — Scope: `scripts/comment_digest.py` and its tests only

No changes to `workflows/archon-dark-factory.yaml` (the issue itself flags this as a
`critical_diff_paths`, human-reviewed surface) or to `commands/dark-factory-implement.md`
(its continue path already prefers `comment-digest.md` whenever it's non-empty, and the fix
makes that file non-empty in exactly the cases that mattered — no consumer-side change
needed).

### R7 — Tests

Add to `tests/test_comment_digest.py`:
1. Gate-verdict comment is the **last** comment in the thread (the primary reported case) →
   included, not swallowed as a silent boundary.
2. Gate-verdict comment followed by a **later, non-gate-verdict factory comment** (e.g. a
   routine `## Dark Factory Run —` report comment) → still included (defends against the
   unresolved report-node-timing question in R2, whichever way it resolves).
3. Gate-verdict comment reachable only through the `no_boundary` branch → included (R3).
4. The empty-sentinel branch, when a gate verdict is present → included, sentinel not emitted
   (R3).
5. A human comment that quotes `## Code Review — Blocked` verbatim but carries **no** factory
   footer → routed as ordinary human feedback, not matched by `_is_gate_verdict` (R1).
6. Two gate-verdict comments in the thread (e.g. two stacked blocks across cycles) → only the
   **latest** is surfaced, not both (avoids feeding the continue agent stale rounds).

Add a drift/sync test (new or in an existing DAG/command-consistency test file) that greps
`commands/*.md` for `gh issue comment` bodies whose heading matches `— Blocked` and asserts
that set equals `_is_gate_verdict`'s literal heading list — so a future third gate template
(e.g. a blast-radius gate) that starts posting issue-level "— Blocked" comments fails CI with
an explicit "register this heading" signal instead of silently bypassing the fix.

## Architecture / Approach

Illustrative sketch (not final code) inside `scripts/comment_digest.py`:

```python
_GATE_VERDICT_HEADINGS = (
    "## Code Review — Blocked",
    "## Spec Conformance — Blocked",
)

def _is_gate_verdict(body: str) -> bool:
    stripped = body.lstrip()
    return (
        any(stripped.startswith(h) for h in _GATE_VERDICT_HEADINGS)
        and _is_factory_comment(body)  # requires the factory footer too
    )

def _latest_gate_verdict(comments: list[dict]) -> dict | None:
    for c in reversed(comments):
        if _is_gate_verdict(c.get("body") or ""):
            return c
    return None
```

`build_digest()` calls `_latest_gate_verdict(comments)` once, independent of the
`last_factory_idx`/boundary computation, and — if non-`None` — appends a `### Gate verdict
(factory-posted, action required)` section built the same way `_feedback_sections()` already
renders issue comments (timestamp + body). This addition composes with all three existing
return paths (`no_boundary`, empty-sentinel, normal-with-boundary) rather than replacing their
control flow.

## Alternatives considered

1. **Only recover a gate-verdict comment when it lands exactly at `last_factory_idx`** (splice
   the boundary comment itself back in, boundary computation and slicing otherwise unchanged) —
   rejected (R2). This is the narrower, less invasive change, and it would fix the case where
   the gate-verdict comment truly is the last comment in the thread. But whether a later
   factory comment (e.g. the `report` node) posts within the same run after a Gate‑3 block —
   which would push the gate-verdict comment *before* the boundary again — is explicitly
   unverified executor behavior in this codebase's own prior design notes. A fix that only
   works under one guess about that ordering could silently fail to fix the reported bug.
2. **Pass `review_result.json` / the latest gate comment to the continue agent explicitly**
   (the issue's own alternate fix direction) — rejected. Bypasses the single-source-of-truth
   digest architecture the implement command's continue path already trusts and documents
   ("treat it as the complete feedback source; do not separately re-read the raw arrays it was
   built from," `commands/dark-factory-implement.md:69`). Would require a second file-existence
   branch in the implement command and duplicate logic per gate type (code-review vs.
   conformance), instead of one deterministic filter change.
3. **Broad/generic heading pattern** (e.g. `^## .+ — Blocked`) instead of the literal two-entry
   list — rejected (R1/R7). Risks silently reclassifying some future, unrelated factory comment
   that happens to end in "— Blocked" as continue-run-driving feedback. The literal list plus
   the drift test (R7) fails loudly in CI instead when a new gate type needs registering,
   matching this repo's scope-discipline convention of not building speculative generality.
4. **Add a staleness/supersession guard now** (e.g. treat a gate verdict as resolved once a
   later `report` comment shows `### Code Review\n\n✅ Passed`) — rejected for this ticket (R5).
   Not requested by the issue, and would require reasoning about the same unresolved
   report-node timing this spec is designed to be robust against either way; better done as a
   follow-up once real operational evidence shows it matters.

## Open questions (non-blocking)

- Whether the `report` DAG node actually posts a comment after `review-gate` exits 1 within the
  same run is unresolved Archon-executor `trigger_rule` behavior (`none_failed_min_one_
  success` at a genuinely failed, not skipped, dependency) — flagged as unverified in
  `docs/archive/2026-07-28-conformance-review-gate-node-design.md:151` and not resolved by this
  ticket. This spec's approach (R2) is deliberately correct under either answer; a future
  ticket that resolves the executor semantics directly could safely add the R5 staleness guard
  with real boundary conditions instead of speculative ones.
- If a future gate (e.g. a blast-radius or budget gate) starts posting an issue-level "—
  Blocked" comment, it needs a literal entry added to `_is_gate_verdict`'s heading list — the
  R7 drift test turns this into a required, visible change rather than a silent gap.

## Assumptions (flagged)

- **[ASSUMPTION]** The issue's cited evidence ("Evidence (#190, PR #353, 2026-08-24)") does not
  correspond to an actual Gate‑3-block/continue-run incident in this repository — `gh issue
  view 190` and `gh pr view 353` both resolve to an unrelated ticket ("Add always-on state
  governance scorecard for Dark Factory persistent state"), with no Gate‑3 block, no "Resuming
  work" comment, and no matching timeline. This does not affect the diagnosis or the fix design
  in this spec, both of which were derived and verified independently by reading `scripts/
  comment_digest.py`, `commands/dark-factory-code-review.md`, and `commands/dark-factory-
  conformance.md` directly, not from the cited evidence. Flagged so a reviewer doesn't spend
  time chasing a citation that won't check out.
- **[ASSUMPTION]** "Human comments keep priority" (issue's fix direction) is interpreted as
  presentation-order priority within the digest (human sections rendered before the gate-verdict
  section, per R4), not as an exclusivity rule where only one or the other appears.
