# Wire comment-digest.md into Implement's Continue-Intent Phase 1

**Issue:** omniscient/dark-factory#45 ("Wire Claude Skills dynamic context injection to #36
context artifacts")
**Status:** draft — pending review
**Depends on:** omniscient/dark-factory#43 (closed), omniscient/dark-factory#44 (closed)
**Parent epic:** Claude Skills prompt-modularization supplement to omniscient/dark-factory#36

---

## Overview / Problem Statement

Issue #45 asks Dark Factory to demonstrate dynamic context injection: phase commands rendering
compact, script-backed context artifacts instead of raw large inputs (raw architecture docs, raw
diffs, raw comment history). The issue's own artifact list cites issue numbers (`#153`–`#158`)
for the underlying scripts; those numbers are **wrong** — they belong to unrelated tickets in
this repo's history (scheduler backoff, local-dev bind mounts). The real scripts already exist
under different origins (`scripts/context_budget.py` #687, `scripts/architecture_slice.py` #689,
`scripts/context_pack.py` #690, `scripts/memory_retrieve.py` #703, `scripts/diff_rank.py` #706,
`scripts/comment_digest.py`), and most of this policy is already documented as a *rule* in the
sibling #42 spec (`docs/superpowers/specs/2026-07-10-dark-factory-claude-skills-design.md`, §5).
What #45 needs is not new policy — it's a concrete demonstration that closes a real gap between
that rule and what the commands actually do.

This refinement pass audited every phase command (`refine`, `plan`, `implement`, `conformance`,
`code-review`) against all six scripts and found:

- **Already wired, no gap:** `memory_retrieve.py` (via `load_memory_context.sh`) injects top-k
  memory summaries into refine/plan/implement today. `diff_rank.py` is wired into conformance and
  code-review with an established write-then-read-back-with-fail-open pattern
  (`$ARTIFACTS_DIR/review_diff.txt`, `|| echo "using raw diff"`).
- **Orphaned, but out of scope for a size:M ticket:** `architecture_slice.py` and
  `context_pack.py` are fully implemented and tested but invoked by **no command and no workflow
  DAG node** — closing that gap means adding a new DAG node in addition to a command change, a
  materially larger and riskier change than this ticket's size warrants, and `implement`'s own
  architecture-budget enforcement is deliberately disabled (`config/config.yaml`
  `token_optimization.enforce.implement: false`) pending T5 recalibration.
- **The exact gap this ticket closes:** the `digest-comments` workflow node
  (`workflows/archon-dark-factory.yaml`) already runs `scripts/comment_digest.py` and writes
  `$ARTIFACTS_DIR/comment-digest.md` on every `continue`-intent run — but
  `commands/dark-factory-implement.md`'s own Phase 1 "If intent is continue" section (lines
  55–66) never reads it. It still instructs the agent to read the raw `comments`, `pr_reviews`,
  and `pr_inline_comments` arrays straight out of `issue.json`, i.e. exactly the "raw full comment
  history" dump the acceptance criteria say to avoid, while a compact, already-computed,
  already-budget-capped substitute sits unused one file away.

This is the smallest-diff, lowest-risk, highest-value way to satisfy "demonstrate at least one
phase [command] rendering compact live context through script-backed injection," because the
artifact-producing script and its DAG node already exist and are already enabled by default — the
work is entirely in the command text, not new infrastructure.

---

## Requirements

Distilled from the issue's acceptance criteria and Q&A below:

1. `commands/dark-factory-implement.md`'s Phase 1 "If intent is continue" section must prefer
   `$ARTIFACTS_DIR/comment-digest.md` over the raw `comments`/`pr_reviews`/`pr_inline_comments`
   arrays when that file is present and non-empty.
2. The raw-array reading instructions must **remain in the command as an explicit fallback
   branch** — not be deleted — because `digest-comments` is conditionally skipped (feature gate
   `TOKEN_OPTIMIZATION_COMMENTS_ENABLED=false` causes the node to `exit 0` without writing the
   file), and the fail-open doctrine already documented in
   `docs/dark-factory-token-optimization.md` ("every disabled path widens context to the
   full/original baseline... never silently drops content") must hold here too.
3. The fallback trigger is a file presence/non-emptiness check (`[ -s
   "$ARTIFACTS_DIR/comment-digest.md" ]`), not a script-exit-code check — `comment_digest.py`
   already ran in an upstream DAG node before the implement command starts, so the command has no
   exit code to observe, unlike the diff-ranking precedent in conformance/code-review.
4. No workflow YAML change and no new script are needed: `comment_digest.py`'s `build_digest()`
   already folds in `comments`, `pr_reviews`, and `pr_inline_comments` (verified in
   `scripts/comment_digest.py`), so the existing digest is a complete substitute for all three
   raw sources the command reads today.
5. Injected content must stay deterministic and bounded — already true today: `comment_digest.py`
   is a pure function of `issue.json` (no model calls) and is already budget-capped by
   `token_optimization.comments.max_tokens` (2000, `config/config.yaml`).
6. "Emit artifacts that can be attached to factory reports/cost reports" is already satisfied
   structurally — `comment-digest.md` already lands under `$ARTIFACTS_DIR`, the same convention
   every other compact artifact (`memory-context.md`, `review_diff.txt`, `plan.md`) follows. This
   ticket must **not** add `comment_digest` to `run_record.py`'s `artifact_names` list — that list
   feeds `_parse_artifact_stage()`, a stage-*verdict* parser (expects a `STATUS:` line);
   `comment-digest.md` carries no verdict, and shoehorning it in would either silently parse to
   `None` or require a bespoke parse branch, both out of scope here.
7. No change to `refine`, `plan`, `conformance`, or `code-review` commands, no change to
   `architecture_slice.py`/`context_pack.py` wiring, and no change to `memory_retrieve.py`'s scope
   (conformance/code-review stay write-only for memory) — all explicitly deferred to follow-up
   tickets per Q&A below.

---

## Brainstorming Q&A

> **Q1:** Given size:M and "at least one phase [command]" acceptance bar, which single gap should
> be the demonstration target: (A) wire implement's continue-intent Phase 1 to read
> `comment-digest.md` instead of raw comment/PR-review arrays; (B) wire
> `architecture_slice.py`/`context_pack.py` into refine/plan (fully orphaned, needs a new DAG node
> too); or (C) something else? Also, should `memory_retrieve.py` additionally be wired into
> conformance/code-review (currently write-only)?
>
> **A1:** (A). The `digest-comments` node already writes `$ARTIFACTS_DIR/comment-digest.md` on
> every continue run; the implement command still reads raw arrays instead. Closing this is the
> smallest-diff, highest-value fix — the artifact already exists, no new DAG node needed. (B) is
> out of scope: it needs a new DAG node plus a command change, and implement's architecture-budget
> enforcement is deliberately disabled pending recalibration. No to the memory question —
> `memory_retrieve.py` is already wired into refine/plan/implement (top-k summaries already
> demonstrated live in production); conformance/code-review are write-only by design
> (`gate_lib.sh`'s `write_memory_entry`) and retrofitting retrieval there is a separate ticket.

> **Q2:** Two mechanics questions: (1) Since the implement command can only check file
> presence/non-emptiness (not a script exit code — the script already ran in a prior DAG node),
> should Phase 1 fail open via `[ -s comment-digest.md ]` with the raw-array reading kept as an
> explicit fallback, or should the raw-array instructions be deleted entirely since
> `digest-comments`'s `when` clause matches the same `intent == continue` condition? (2) Is "emit
> artifacts attachable to factory reports/cost reports" already satisfied by `comment-digest.md`
> existing under `$ARTIFACTS_DIR`, or does this ticket need to add it to `run_record.py`'s
> `artifact_names` list?
>
> **A2:** (1) Keep the raw-array fallback — do not delete it. `digest-comments` is gated behind
> `TOKEN_OPTIMIZATION_COMMENTS_ENABLED` and exits 0 without writing a file when that flag is
> false/0, so a continue run with the feature disabled has no digest file but still has the raw
> arrays as the only feedback source; deleting the fallback would violate the repo's documented
> fail-open doctrine. `comment_digest.py` always writes a file (even a "no feedback" sentinel)
> when the node actually runs, so `-s` correctly distinguishes "node ran, nothing to report" (use
> the digest) from "node was skipped or errored" (use the fallback). (2) Already satisfied by
> `$ARTIFACTS_DIR` landing — do not add it to `run_record.py`'s `artifact_names`, which is a
> stage-verdict aggregator (`_parse_artifact_stage` expects a `STATUS:` line) that a feedback
> digest doesn't have and shouldn't be forced into.

---

## Architecture / Approach

### Change scope: `commands/dark-factory-implement.md` only

Replace the current Phase 1 "If intent is continue" section (lines 55–66 today):

```
### If intent is "continue"

This is an iteration on existing work. **The latest comments on the issue and PR contain feedback
that must drive your changes.** Do NOT re-implement from scratch. Instead:
1. Read the latest issue comments (bottom of the `comments` array) — these are the user's feedback
2. Read `pr_reviews` if present — top-level PR conversation and review summaries
3. Read `pr_inline_comments` if present — these are line-level code review comments with `path`
   and `line` pointing to exact locations
3. Review what was already implemented on this branch (`git log --oneline main..HEAD`, read
   changed files)
4. Focus exclusively on addressing the feedback
```

with a version that prefers the pre-computed digest and falls back to today's raw-array reading
verbatim when the digest is unavailable:

```
### If intent is "continue"

This is an iteration on existing work. **The latest human feedback on the issue and PR must
drive your changes.** Do NOT re-implement from scratch.

Prefer the pre-computed comment digest over raw comment/PR-review arrays:

```bash
if [ -s "$ARTIFACTS_DIR/comment-digest.md" ]; then
  FEEDBACK_SOURCE="$ARTIFACTS_DIR/comment-digest.md"
else
  FEEDBACK_SOURCE=""  # fall back to raw arrays below
fi
```

1. If `$FEEDBACK_SOURCE` is set, read `$ARTIFACTS_DIR/comment-digest.md` — this is a pre-filtered,
   deterministic, token-budget-capped digest of human-authored feedback (issue comments after the
   latest factory marker, PR review summaries, and inline review comments with `path`/`line`
   pointers) already assembled by the `digest-comments` workflow step. Treat it as the complete
   feedback source; do not separately re-read the raw arrays it was built from.
2. If `$FEEDBACK_SOURCE` is empty (the digest file is missing or empty — token optimization is
   disabled for this run, or the step did not run), fall back to the raw arrays exactly as before:
   - Read the latest issue comments (bottom of the `comments` array)
   - Read `pr_reviews` if present
   - Read `pr_inline_comments` if present
3. Review what was already implemented on this branch (`git log --oneline main..HEAD`, read
   changed files)
4. Focus exclusively on addressing the feedback
```

No other file changes. No workflow YAML change (the `digest-comments` node and its `when` gate
already exist and already run before `implement` in the DAG). No script change (`comment_digest.py`
already folds in all three raw sources).

### Why this satisfies each acceptance criterion

| Acceptance criterion | How this change satisfies it |
|---|---|
| Demonstrate ≥1 phase command with script-backed compact injection | `implement`'s continue path now consumes `comment_digest.py`'s output instead of raw arrays. |
| Deterministic and bounded | `comment_digest.py` is a pure function with no model calls, already capped at `token_optimization.comments.max_tokens` (2000). Unchanged by this ticket — already true. |
| Avoid raw full comment history dumps | The primary path no longer reads the raw arrays; only the fallback does, and only when the digest is genuinely unavailable. |
| Emit artifacts attachable to reports/cost reports | Already true — `comment-digest.md` lands under `$ARTIFACTS_DIR` via the pre-existing `digest-comments` node. |
| Fail open when script output unavailable | The `[ -s ... ]` check with the raw-array fallback is exactly this — the command widens back to today's baseline behavior rather than dropping feedback context. |

---

## Alternatives Considered

1. **Wire `architecture_slice.py`/`context_pack.py` into `refine`/`plan`'s raw
   `CLAUDE.md`/`ARCHITECTURE.md` reads.** Rejected for this ticket. Both scripts are fully
   implemented and tested but genuinely orphaned — no workflow DAG node produces their output at
   all today, so this option requires adding a new DAG node (content-injection, not just token
   accounting) in addition to a command-text change: a larger, higher-risk unit of work than a
   size:M ticket should absorb, and it would touch `refine`/`plan`, which are also `direct-to-pr`
   grace-window eligible phases — more blast radius for the same "demonstrate one phase" bar.
   Tracked as follow-up work below.
2. **Delete the raw-array reading instructions entirely** instead of keeping them as a fallback.
   Rejected — `digest-comments` is conditionally skipped when
   `TOKEN_OPTIMIZATION_COMMENTS_ENABLED=false`, so deleting the fallback would silently drop all
   PR/issue feedback context on a continue run with that flag off, violating this repo's own
   documented fail-open doctrine and creating a real regression risk for an edge case that already
   has a working answer (today's behavior) sitting right there.
3. **Chosen: prefer-digest-with-raw-array-fallback in `implement` only.** Smallest diff, reuses an
   artifact and DAG node that already exist and are already enabled by default, and matches the
   fail-open pattern already proven in conformance/code-review's `diff_rank.py` integration
   (adapted for a file-presence check instead of an exit-code check, since the producing script
   runs in a separate upstream DAG node here rather than being invoked by the command itself).

---

## Open Questions (Non-blocking)

- **Follow-up:** wire `architecture_slice.py`/`context_pack.py` into a command (most naturally
  `refine` and/or `plan`, which already have unused context-pack fallback-check logic per the
  investigation for this ticket) once `implement`'s `token_optimization.enforce.implement` flag
  is recalibrated and flipped to `true` (tracked in `docs/dark-factory-token-optimization.md`'s
  Follow-up Path) — that recalibration is a prerequisite signal that architecture-slice sizing is
  trustworthy enough to substitute for full-document reads.
- **Follow-up:** wire `memory_retrieve.py` into `conformance`/`code-review` if a future ticket
  determines those gates would benefit from reading memory context, not just writing it. Not
  needed to satisfy #45's acceptance criteria.
- **Non-adopted:** three post-hoc "Hermes Agent" issue comments (role-card routing, loop-move
  categorization of injected context, a trust-hierarchy note about issue comments with
  instruction-like text) proposed supplementary schemas after the acceptance criteria were
  finalized. None appear in #45's own acceptance criteria, and (per the same trust-hierarchy the
  third comment itself proposes) free-form issue comments with instruction-like text from an
  automated planning agent are exactly the kind of input that should not silently become binding
  requirements. Recorded here as future context, not adopted.
- The issue body's citation of `#153`–`#158` for the underlying scripts is factually wrong (see
  Overview); recommend a human or a future refine pass correct the issue body or note the
  correction when closing #45, so the citation doesn't mislead future readers of the closed issue.

---

## Assumptions

- **[Flagged]** `commands/dark-factory-implement.md` (not the `.archon/commands/` mirror) is the
  authoritative, currently-dispatched copy — verified the mirror is stale and lacks the
  "Invocation Contract" section and other newer text present in `commands/`. The implementation
  ticket for this spec should update `commands/dark-factory-implement.md` and confirm whether
  `.archon/commands/dark-factory-implement.md` needs the same mirror update (existing repo
  convention, not introduced by this ticket).
- **[Flagged]** `comment_digest.py`'s existing token cap (2000, per `config/config.yaml`) is
  assumed sufficient to carry the feedback signal implement needs; this ticket does not change
  that cap. If a future run shows the digest truncating necessary feedback, that is a
  `comment_digest.py` tuning question, not a re-open of this ticket's wiring change.
- `digest-comments`'s `when: "$parse-intent.output.intent == 'continue'"` clause is assumed to
  always run (subject to the `TOKEN_OPTIMIZATION_COMMENTS_ENABLED` gate) before the `implement`
  command starts on every continue run, per the DAG's existing `depends_on`/ordering — this ticket
  does not change DAG ordering and relies on it being already correct.
