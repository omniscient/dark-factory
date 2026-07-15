# Refine/plan push nodes: gate on artifact existence, not node completion

**Status:** design
**Date:** 2026-07-15
**Issue:** #212
**Build constraint:** `workflows/archon-dark-factory.yaml` is **baked** into the image
(`Dockerfile:122`, `COPY workflows/ /opt/dark-factory/workflows/`) and materialized into
`$CLONE_DIR/.archon/workflows/` at container start only when the clone doesn't already provide
its own copy (`entrypoint.sh:574-578`) — true today for the dark-factory self-target, whose
`.archon/workflows/` is untracked. This change needs `docker compose build` + a redeploy of the
running scheduler/run image before it takes effect; editing `workflows/archon-dark-factory.yaml`
in the clone alone does not change runtime behavior.

## Problem

The `refine` and `plan` command nodes (`workflows/archon-dark-factory.yaml`) fan out
product-owner/architect subagents and, on the current executor, can end their turn while that
work is still in flight — e.g. by scheduling a wakeup and waiting. Confirmed via a kept-container
transcript (issue #212, comment 3): "turn end = process end" in this executor, so the DAG node is
killed at a fixed wall-clock point regardless of pending subagent work, **and the killed node is
marked `dag_node_completed` (`anyFailed: false`)** — not failed.

`refine-push` (~L442) and `plan-push-and-advance` (~L460) depend only on their upstream command
node *completing* (`depends_on: [refine]` / `[plan]`, default trigger rule). Because the executor
misreports a killed node as completed, both push nodes run unconditionally after a silent death:
they push a branch with zero new commits (tip == main, no spec/plan file) and apply the gate label
(`spec-pending-review` / `plan-pending-review`) regardless of whether anything exists to review.

That combination is unrecoverable by the existing scheduler logic:

- `spec_advance_check`/`plan_advance_check` (`scheduler.sh:311`, `:346`) both require a "Posted
  by … Refinement Pipeline" report comment to do anything (feedback-reset path or
  `direct-to-pr` grace-timer path); a silently-killed run posts no such comment, so
  `elapsed_minutes_since_marker` returns `""` and the check waits forever.
- The item now carries the gate label, so it no longer reaches the Priority 4/5 backlog loops
  (`scheduler.sh:1156-1231`) that would otherwise retry it and eventually trip it to `Blocked`
  via `get_retry_count`/`trip_to_blocked`.
- Nothing is posted to the issue and nothing is written to `runs.jsonl` — the death is invisible
  everywhere except an incremented (but now unreachable) retry counter.

The label is a verification-gate artifact being applied without verifying the thing it attests
to: that a spec (or plan) actually exists for a reviewer to look at.

## Existing mechanism and its gap

`refine-push` and `plan-push-and-advance` are DAG `bash:` nodes. Unlike `entrypoint.sh`, they are
separate script invocations — they do not source `entrypoint.sh` and have no access to its
`REFINE_FAILURE_MARKER` constant or its `post_or_update_comment` helper (`entrypoint.sh:154-172`).
They already shell out to the same CLI other nodes use for tracker/codehost operations
(`scripts/factory_core/providers/cli.py`), which exposes the exact primitive needed:
`tracker comment --id <n> --marker <m> --body-file <f>` (idempotent upsert-by-marker, the same
one `post_or_update_comment` wraps).

Separately, `entrypoint.sh`'s own `on_failure` `ERR` trap — which *does* know how to post
`REFINE_FAILURE_MARKER` (`<!-- df-refine-failure -->`) — never fires for this failure mode. A
nonzero exit from `archon workflow run` is handled by entrypoint.sh's manual retry loop
(`entrypoint.sh:776-829`), which calls `run_post_mortem` (a no-op for `refine|plan|deconflict`,
`entrypoint.sh:180-182`) and then `exit "$EXIT_CODE"` directly — bypassing the `ERR` trap
entirely. So even a node that *does* correctly fail today produces no comment and no board change
through that path. The fix must not rely on that trap; it must act entirely from inside the
push nodes themselves.

The scheduler's own retry ceiling is, by contrast, already correct and needs no changes: Priority
5 (`scheduler.sh:1195-1231`, refine) and Priority 4 (`scheduler.sh:1156-1193`, plan) both
`get_retry_count`/`increment_retry`/`trip_to_blocked` an item that has *no* gate label and *no*
`needs-discussion` label, every poll. Withholding the gate label on a genuine artifact miss is
enough, by itself, to route the item back into that existing retry-then-block machinery.

## Decision

### 1. Artifact-gate check: git-aware, committed-file only

Gate on whether the current branch actually has a committed spec/plan file for this issue —
**not** the ephemeral `$ARTIFACTS_DIR/refinement-status.md` `STATUS:` marker (written to the
run's artifacts directory, never committed, and written by Phase 5/8 *after* the file commit —
so on the actual failure mode, mid-generation death, neither exists; but as a standalone gate the
marker only proves agent intent, not a reviewable artifact, so it must not be trusted alone or as
an `OR` alternative to the file check). A plain working-tree `grep -rl` (the pattern already used
elsewhere, e.g. `push-and-pr` L996-997, `budget-plan` L397) is insufficient on its own — it can't
distinguish "committed" from "written to disk, agent died before `git commit`" — so the check must
also confirm the branch carries commits beyond `main`:

```bash
# refine-push, replacing the unconditional push+label
ISSUE=$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")
BRANCH=$(git branch --show-current)
_PCLI="${CLONE_DIR:-.}/dark-factory/scripts/factory_core/providers/cli.py"

HAS_COMMITS=$(git rev-list --count main..HEAD 2>/dev/null || echo 0)
SPEC_FILE=""
if [ "$HAS_COMMITS" -gt 0 ]; then
  SPEC_FILE=$(git diff --name-only main...HEAD -- docs/superpowers/specs/ \
    | xargs -r grep -l "#${ISSUE}\b" 2>/dev/null | head -1)
fi

if [ -n "$SPEC_FILE" ]; then
  # existing behavior, unchanged
  git push -u origin "$BRANCH"
  python3 "$_PCLI" tracker label --id "$ISSUE" --add spec-pending-review
  echo "Pushed $BRANCH for issue #$ISSUE (spec-pending-review gate applied)"
else
  # see §2 and §3
fi
```

`plan-push-and-advance` mirrors this exactly against `docs/superpowers/plans/` and
`plan-pending-review`.

### 2. Distinguish a clean, already-communicated abort from a true silent death

`commands/dark-factory-refine.md` has two legitimate paths that produce no spec and correctly add
`needs-discussion` before exiting cleanly: the Phase 2 pre-flight (<20-char issue body) and the
Phase 4 `UNCERTAIN:` product-owner response. `commands/dark-factory-plan.md` has the structurally
identical pattern (architect-not-approved after 3 cycles; conformance divergence after
`MAX_CYCLES`). Both already post an explanatory comment and pause automation via the label — a
`df-refine-failure` comment on top of that would misrepresent a deliberate pause as a crash.

Distinguish the two cases by checking `needs-discussion` **live** at push time (a fresh tracker
query — the `$ARTIFACTS_DIR/issue.json` snapshot predates a label the command adds mid-run):

```bash
NEEDS_DISCUSSION=$(python3 "$_PCLI" tracker get --id "$ISSUE" --fields labels \
  | jq -r '.labels[].name' | grep -qx "needs-discussion" && echo yes || echo no)

if [ "$NEEDS_DISCUSSION" = "yes" ]; then
  echo "refine-push: no spec artifact, but needs-discussion already communicates why — skipping silently."
  # no push, no label, no failure comment
else
  # true silent death — see §3
fi
```

| Artifact exists? | `needs-discussion` live? | Push | Label | Failure comment |
|---|---|---|---|---|
| yes | — | yes | yes (unchanged) | no |
| no | yes | no | no | no (already communicated) |
| no | no | no | no | **yes** — this is #212 |

### 3. Failure comment: idempotent marker-upsert, reused across refine and plan

On a true silent-death miss, post via the same CLI primitive `post_or_update_comment` wraps,
inline (the push nodes cannot source `entrypoint.sh`):

```bash
else
  echo "refine-push: no committed spec found for issue #$ISSUE and no needs-discussion label — treating as silent death."
  TMPFILE=$(mktemp /tmp/refine-failure-XXXXXX.md)
  cat > "$TMPFILE" <<EOF
<!-- df-refine-failure -->
## Refinement Pipeline — Failed

The refine agent ended without producing a committed spec (\`docs/superpowers/specs/\`) for this
issue. No gate label was applied; this item remains eligible for automatic retry.

\`\`\`bash
# Retry manually if needed
docker compose --profile factory run --rm dark-factory "Refine issue #${ISSUE}"
\`\`\`

---
*Posted by Dark Factory Refinement Pipeline*
EOF
  python3 "$_PCLI" tracker comment --id "$ISSUE" --marker "<!-- df-refine-failure -->" --body-file "$TMPFILE"
  rm -f "$TMPFILE"
fi
```

Reuse the literal marker `<!-- df-refine-failure -->` for **both** refine and plan nodes (the
same string `entrypoint.sh:156` already defines and would otherwise apply to either intent) —
adjust only the comment body wording ("plan" vs "spec", `docs/superpowers/plans/`). Upsert-by-
marker (rather than a fresh one-shot `gh issue comment`, the convention used by other terminal
DAG-node failures like `preview_fail`) matters here specifically because this path is retried:
`REFINE_MAX_RETRIES` defaults to 3 per phase (`scheduler.sh:19`), so a naive one-shot comment
would post up to 3 near-identical failure comments per phase before the ceiling trips.

**No `git push` on a miss** — there is nothing to review, and leaving the (possibly still-empty)
local branch unpushed avoids an empty remote branch for the retry's `setup-refine-branch` to
needlessly re-fetch.

### 4. Exit code: `exit 0`

The node must exit 0 on a miss, in both the clean-abort and silent-death branches. Reasoning,
traced through `entrypoint.sh`'s retry loop (`:776-829`): a nonzero exit here makes
`archon workflow run` return nonzero, which lands in `run_post_mortem` (a no-op for
`refine|plan|deconflict`) and then a bare `exit "$EXIT_CODE"` — **not** the `ERR` trap, so no
comment, no board change, just a silent nonzero container exit. That is a worse version of the
exact symptom this ticket fixes. `exit 0` is also semantically correct: withholding the label on
a legitimate artifact miss is this node's intended behavior, not a node error. It leaves the item
exactly where the scheduler's Priority 4/5 backlog loops already know how to find and retry it
(§ "Existing mechanism and its gap" above) — no `scheduler.sh` changes required.

## Related, explicitly out of scope

- **`plan_advance_check`'s grace-timer marker mismatch** (issue #212, comment 2): it keys on the
  *spec* report marker ("Posted by … Refinement Pipeline") rather than a plan-specific one, so a
  `direct-to-pr` ticket could in principle auto-advance to Ready without a plan. This fix already
  closes the primary reachability path — with no gate label applied on a miss, `plan_advance_check`
  is never invoked for a plan-less ticket (it only runs from the `plan-pending-review &&
  direct-to-pr` branch, `scheduler.sh:1165-1168`) — but the marker itself is unfixed for a
  ticket that reaches that state some other way (e.g. a human hand-applying the label). Different
  file (`scheduler.sh`, not the DAG YAML), different mechanism (grace-timer matching, not
  push/label gating); left for its own ticket per CLAUDE.md scope discipline.
- **The implement-phase `push-and-pr` node** (`workflows/archon-dark-factory.yaml:970`) has the
  same unconditional-push shape and was reproduced with the same symptom (issue #212, comment 6:
  "feat branch byte-identical to main, no implementation.md, downstream nodes ran anyway"). That
  reproduction is explicitly attributed to a separate tracked issue (#208) in the same comment,
  not this one. Out of scope here.
- **The underlying executor bug** (a parked/killed node reported as `dag_node_completed` instead
  of failed) is infrastructure this repo doesn't own (the external `archon` DAG runtime, not
  `workflows/archon-dark-factory.yaml`). The interim mitigation — mandating in-turn polling
  instead of `ScheduleWakeup`-and-park in command instructions — already shipped in CLAUDE.md
  ("Turn end = process end" section, PR #221, issue #212 comment 4). This spec's artifact gate is
  the second, independent layer: even if a future orchestration change reintroduces a park-and-die
  pattern, or the executor misreports node status for an unrelated reason, the push nodes verify
  the actual deliverable rather than trusting node-completion status at all.

## Known limitations

- The `git rev-list --count main..HEAD` check assumes the node's working tree has `main` as a
  reachable local ref pointing at the branch point, matching the convention already relied on by
  `de-conflict` and `push-and-pr` (`main...HEAD`/`main..HEAD` diffs elsewhere in this same
  workflow file). No new assumption is introduced.
- A spec/plan file that is committed but does not contain the literal `#<issue-number>` text
  (e.g. a typo, or a differently-formatted reference) would still gate as "missing" and produce a
  false failure comment. This mirrors an existing limitation of the identical grep pattern already
  used by `push-and-pr`/`budget-plan` for spec/plan discovery — not a new fragility introduced by
  this change.
- If `tracker comment --marker` itself fails (e.g. transient API error), the failure comment is
  silently lost for that attempt; unlike `post_or_update_comment`'s callers in `entrypoint.sh`,
  which always wrap it in `|| true` and treat it as non-fatal, this design does the same — the
  node must not itself fail (exit nonzero) just because the *comment* about the failure couldn't
  be posted, per §4.

## Validation

- **Workflow DAG check** (`scripts/check_workflow_dag.py`, run via `python -m pytest tests/` per
  existing CI): confirm the modified `refine-push`/`plan-push-and-advance` node bodies still
  parse as valid DAG bash nodes and preserve existing `depends_on`/`when`/`timeout` fields.
- **New bash-level test** (mirroring the `SCHEDULER_SOURCE_ONLY`-style harness used by
  `tests/test_scheduler_*.sh`, adapted to extract and exercise the node's bash block directly, or
  a fixture-repo integration test under `tests/`): three scenarios per node —
  1. committed spec/plan file present → push + label applied, no failure comment.
  2. no file, `needs-discussion` present → no push, no label, no failure comment, node exits 0.
  3. no file, no `needs-discussion` → no push, no label, `df-refine-failure`/plan-equivalent
     comment posted via `tracker comment --marker`, node exits 0.
- **Idempotency check**: invoking the miss-branch (scenario 3) twice against the same issue
  produces one comment (edited in place), not two — assert via the `tracker comment --marker`
  upsert semantics already covered by existing CLI-level tests for that subcommand, if present,
  or a new one under `tests/` if not.
- **Manual (staging):** reproduce the original failure by killing a `refine` node mid-run (or
  simulating via a stub command node that exits 0 with a scheduled-but-unresolved wakeup),
  confirm no `spec-pending-review` label lands, confirm the `df-refine-failure` comment appears,
  confirm the scheduler's next poll re-dispatches "Refine issue #N" and increments the `:refine`
  retry counter as normal.

## Accepted trade-offs

- Reusing the single `df-refine-failure` marker string for both refine and plan phases means the
  two failure comments are only distinguishable by body text, not by marker — matching
  `entrypoint.sh`'s own existing (pre-#212) convention rather than introducing a new
  phase-specific marker, to minimize surface area for this fix.
- The grep-based `#<issue-number>` file-content match (shared with `push-and-pr`/`budget-plan`) is
  left as-is rather than hardened, to keep this change scoped to gating, not to fixing an
  unrelated pre-existing discovery-pattern fragility.

## Assumptions

- `scripts/factory_core/providers/cli.py`'s `tracker comment --marker` subcommand
  (`cli.py:170-172`) performs a find-by-marker-then-upsert, matching the semantics
  `entrypoint.sh:164-172`'s `post_or_update_comment` already relies on for the identical
  `<!-- df-refine-failure -->` marker in the `on_failure` trap path.
- `REFINE_MAX_RETRIES` (default 3, `scheduler.sh:19`) and the existing Priority 4/5
  retry-then-`trip_to_blocked` logic (`scheduler.sh:1156-1231`) are the intended terminal safety
  net for a *recurring* silent death; this spec does not change retry ceilings or add new
  breaker call sites.

## Open questions (non-blocking)

- Should the failure comment additionally surface the `refinement-status.md` `STATUS:` line
  (when the artifacts directory happens to still be present in that run's container) as
  diagnostic context, even though it plays no role in the gate decision itself? Not required for
  correctness; would only improve the comment's debuggability.
