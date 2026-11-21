# Deterministic, Durable Blast-Radius Approval

**Issue:** #551
**Related:** #374 (stateless `HUMAN_REQUIRED` cannot be approved), #407 (no deterministic
consumer of `blast.md`), #373 (missing conformance verdict), #411 (conformance agent
refusals), #180 and #484 (the two runs that triggered this ticket; both needed
hand-opened PRs — #549, #550)
**Not a `direct-to-pr` candidate** (per the issue: this touches gate sequencing)

---

## Overview / Problem Statement

The blast-radius gate (`scripts/gate_blast_radius.py`, invoked from `commands/dark-factory-validate.md`
Phase 0, lines 44-119) is a pure, deterministic, path-based classifier. It recomputes
`STATUS` from scratch on every `validate` run — it has no memory of a prior human review.
When it flags a triggering file (hotspot / migration-seed / skill-security /
`.factory/adapter.yaml` boundary-escalation) or an over-budget diff size, it writes
`STATUS: HUMAN_REQUIRED` to `$ARTIFACTS_DIR/blast.md`, and `validate`'s Phase 0
unconditionally posts a "Blast-Radius Gate — BLOCKED" comment, adds `needs-discussion`,
sets the board to `Blocked`, and `exit 1`s — even when a human already reviewed and
signed off on that exact diff in a previous run.

Two independent defects compound into an unrecoverable loop (observed twice on #180,
2026-11-20, and again on #484):

1. **Statelessness (#374).** There is no durable record of a human sign-off. Lifting
   `needs-discussion` (the only action the blocked comment currently describes) lets the
   scheduler dispatch `Continue`, which re-runs `validate`, which re-flags the same
   already-reviewed files and re-adds the label. The block can never be durably cleared
   for an unchanged diff — only worked around by hand-opening a PR outside the factory
   entirely, which is what actually happened for both #180 and #484.
2. **No deterministic consumer of `blast.md` (#407).** `validate`'s own `exit 1` happens
   inside a `command:`-type DAG node's own Bash tool call, and (per the documented
   `#212`/`#271` quirk — see `commands/dark-factory-conformance.md:708-712` and
   `scripts/verdict_gate_check.sh`'s header comment) such an internal `exit 1` does
   **not** reliably surface as node failure to the Archon DAG executor. The DAG keeps
   going to `conformance` in the same run. Because nothing explicitly told the
   `conformance` phase agent that this isn't its decision to make, it used its own
   judgment on #180 to notice the stale block (label present, board `Blocked`,
   `blast.md: HUMAN_REQUIRED`) and refuse to run Gate 2 — "the prerequisite blast-radius
   gate does not appear to have been genuinely resolved" — correctly distrusting the
   operator's sign-off comment as a comment-channel authorization (CLAUDE.md: comment
   channels may never authorize a `gate_*` change) but then *halting itself* instead of
   just doing its own job and trusting that enforcement lives elsewhere. This is the
   anti-pattern the issue calls out directly: "the conformance node should not be the
   place where an LLM decides whether a prior gate was genuinely resolved."

Both defects, and their exact mechanism, are independently corroborated by
`docs/run-lifecycle.md` (as-built architecture notes), items 3 and 4 (first written before
this ticket existed): "The blast-radius block has no enforcing DAG node" and "The
blast-radius comment names a command that does not exist" (the comment tells the
operator to re-run `"Validate issue #N"`, but `entrypoint.sh:103`'s intent regex has no
`validate` keyword, so that string silently falls through to the `fix` default —
`INTENT=${INTENT:-fix}`, `entrypoint.sh:104` — re-running the *entire* implement
pipeline from scratch rather than just re-validating).

## Requirements (from Q&A)

Governed throughout by the issue's own "Scope decision (owner, 2026-11-20)," itself an
owner-authorized mandate for this spec to design the real mechanics (not defer them to a
follow-up ticket) and by CLAUDE.md's hard limits (never weaken an existing gate as a side
effect; a comment alone may never authorize a `gate_*` change).

1. **Durable, diff-bound approval record.** A blast-radius approval must be recorded in a
   mechanically checked, durable store — not an ephemeral per-run artifact
   (`$ARTIFACTS_DIR` does not survive across separate container dispatches) and not the
   git branch itself (a record committed onto the reviewed branch would be part of the
   very diff it approves, would merge into `main`, and `conformance`'s scope enforcement
   would flag it as an out-of-scope file). It lives at
   `${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}/blast-approvals/<issue>.json` — the
   same `scheduler_state` Docker volume `run-compose.yml` already mounts into **both**
   the `dark-factory` run-container service (lines 58-59) and `factory-model-proxy`
   (lines 86-87), and that `entrypoint.sh`/`run_record.py` already use for cross-run
   state (`runs.jsonl`, the session-window pause sentinel). A run container reads the
   file as a normal local path; the operator's host-side tooling reaches the same volume
   via `docker exec "$DF_SCHEDULER" ...`, exactly like `.claude/skills/operator/scripts/lib.sh`'s
   existing `window_state()` does today.
2. **Approval scoped to "this diff only," per triggering surface, not the whole PR.**
   The approval must not carry over to a materially different diff, but it also must not
   be invalidated by unrelated later commits (a conformance reconcile-cycle fix, a
   `revise-advisory` commit) that never touch the files that triggered the gate — that
   would simply reconstruct today's loop one level down. The record stores, per
   triggered file path, `git diff <merge-base main HEAD> -- <path> | git patch-id --stable`
   (patch-id, not a raw blob/diff hash, so the approval survives a rebase or an
   unrelated merge-from-main that leaves the flagged file's own changes untouched) plus
   the approved `lines_changed` total (for the size trigger) and the approved HEAD SHA.
3. **Human-run, local tool is the only channel — never a comment, never a phase agent.**
   A new sibling script, `.claude/skills/operator/scripts/approve-blast.sh`, modeled
   directly on the existing `.claude/skills/operator/scripts/approve.sh` (the project's
   established precedent for "durable, mechanically checked, human-run gate approval,"
   already shipped for `spec-pending-review`/`plan-pending-review`). It independently
   recomputes the current trigger set from the fetched branch HEAD before writing
   anything — it never trusts a stale `blast.md`.
4. **Strengthen only.** This changes nothing about which paths/sizes trigger the gate
   (`migration_seed_auth_patterns`, the hotspot floor, the size budget are untouched —
   explicitly out of scope per the issue), nothing about `boundary-escalation`'s semantic
   diff logic, and nothing about the global `scheduler.sh` `SKIP_LABELS` circuit breaker.
   It adds exactly one new, narrowly diff-bound, human-recorded override path for a gate
   that today has *no* way to be durably cleared at all — every other gate/breaker/budget
   is unchanged.
5. **Deterministic enforcement; `conformance` stops adjudicating.** A dedicated DAG-level
   gate node (mirroring the proven `conformance-gate`/`docs-conformance-gate` pattern,
   `scripts/verdict_gate_check.sh`) becomes the thing that actually stops the pipeline on
   an unresolved `HUMAN_REQUIRED` — not `validate`'s own internal `exit 1`, and not any
   phase agent's judgment call. `commands/dark-factory-conformance.md` is updated to say
   explicitly that it must never inspect `blast.md`, `needs-discussion`, or board status
   to decide whether to run its review.

## Architecture / Approach

### A. `scripts/gate_blast_radius.py` — approval matching (new, deterministic)

Add two new args: `--issue <N>` and `--approvals-dir <path>` (default
`${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}/blast-approvals`). After computing the
existing `triggered`/`boundary_findings`/`size_trigger` sets exactly as today, and before
deciding the final `status`:

1. Load `<approvals-dir>/<issue>.json` if present (fail-open to "no approval" on any
   read/parse error — this can only ever make the gate *more* conservative, never less).
2. For every path in the current trigger set (`TRIGGERED_FILES`, including
   `.factory/adapter.yaml` when boundary-escalation fired), recompute
   `git diff <merge-base main HEAD> -- <path> | git patch-id --stable` and compare
   against the record's `files[path]`.
3. The approval **matches** iff: every currently-triggered path is present in the
   record's `files` map with an identical patch-id, **and** (only when the size trigger
   is active) current `lines_changed <= record.lines_changed`. A currently-triggered
   path that is *not* in the record (a newly-triggered file that wasn't part of the
   approved set) always fails the match — the current trigger set must be a subset of
   what was approved, not merely overlap with it.
4. On a match: override `status` to `PASS` (reusing the existing gating vocabulary
   `verdict_gate_check.sh` already understands — no change needed there) and emit an
   additional `APPROVAL: matched (record approved <record.head_sha> at <record.approved_at>)`
   line.
5. On no record, or a record that no longer matches: `status` stays `HUMAN_REQUIRED` as
   today, and the script emits one of: `APPROVAL: none` (no record at all), or
   `APPROVAL: stale (new trigger: <path>)` / `APPROVAL: stale (changed since approval: <path>)`
   / `APPROVAL: stale (lines <cur> > approved <n>)` (a record exists but no longer
   covers the current diff) — mechanical output, not agent judgment, so `validate`'s
   Phase 0 can surface the *right* explanation to the operator without itself making any
   decision.

This keeps `gate_blast_radius.py` a pure, testable, stdin-driven classifier exactly as it
is today — `tests/test_blast_radius.py` gets a new section of cases for the approval-match
logic (approved/stale/new-trigger/size-exceeded/missing-record), following the file's
existing `run_script` fixture conventions.

### B. `commands/dark-factory-validate.md` Phase 0 — two additive changes

1. Pass `--issue "$ISSUE_NUM"` to the existing gate invocation (line ~77-84). No other
   change to the invocation itself.
2. Inside the existing `if [ "$BLAST_STATUS" = "HUMAN_REQUIRED" ]` branch (line 92),
   **before** posting the comment: push the current branch to `origin` with a verified
   push (reusing the `refine-push`/`plan-push-and-advance` pattern already in
   `workflows/archon-dark-factory.yaml` — 30s timeout, remote-SHA verification on
   timeout, `df-push-failure`-style handling on real failure) — **without** opening a PR.
   This is required because, for `new`/`continue`-intent runs, nothing pushes the
   feature branch to `origin` before this point today (confirmed: the only earlier
   pushes in the DAG are `refine-push`/`plan-push-and-advance`, refine/plan intents
   only, and `push-resolve`, resolve intent only — `workflows/archon-dark-factory.yaml`
   lines 556-613, 687, 1274-1293). Once a dedicated `blast-gate` node (below) stops the
   DAG right after `validate`, a blocked diff's branch would otherwise never reach
   `origin` in that run, and the ephemeral container's local commits would be destroyed
   on exit — leaving the human operator nothing to fetch, hash, or approve. This push is
   additive only: PR creation remains exactly where it is today, gated behind
   `conformance-gate`/`docs-conformance-gate` at `push-and-pr`.
3. Rewrite the blocked-comment body. Remove the current "remove the `needs-discussion`
   label... then re-run `docker compose --profile factory run --rm dark-factory
   \"Validate issue #$ISSUE_NUM\"`" instructions (both halves are wrong: removing the
   label alone no longer suffices once durable approval exists, and `"Validate issue
   #N"` silently falls through to the `fix` intent default per `entrypoint.sh:104` — a
   pre-existing defect this rewrite also fixes, documented independently in
   `docs/run-lifecycle.md` item 4). Replace with:
   - The pushed branch name and HEAD SHA (from step 2), for the operator to review.
   - An instruction to run `.claude/skills/operator/scripts/approve-blast.sh <N> --sha <HEAD>`
     locally after reviewing — that script records the approval **and** removes
     `needs-discussion` itself, in that order; removing the label by hand is explicitly
     called out as wrong (it would just re-enable the scheduler's Blocked-retry dispatch
     into another doomed `validate` re-run with no durable approval behind it).
   - When `blast.md`'s `APPROVAL:` line is `stale (...)` rather than `none`, surface that
     reason ("Previously approved at `<sha>`; no longer matches because ...") so the
     operator knows this is a delta re-review, not a first-time review.

### C. New DAG node: `blast-gate` (`workflows/archon-dark-factory.yaml`)

```yaml
# Verdict gate: block conformance/push-and-pr/push-resolve when validate's blast-radius
# verdict isn't PASS/SKIPPED/ERROR — validate's own internal exit 1 does not reliably
# surface to the DAG executor (#212, #271; same class of problem conformance-gate and
# docs-conformance-gate already solve one gate later). (#407/#551)
- id: blast-gate
  bash: |
    bash "${CLONE_DIR:-.}/dark-factory/scripts/verdict_gate_check.sh" \
      "$ARTIFACTS_DIR/blast.md" \
      "$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")" \
      "Blast-Radius Gate"
  depends_on: [validate]
  when: "$parse-intent.output.intent == 'new' || $parse-intent.output.intent == 'continue' || $parse-intent.output.intent == 'resolve'"
  timeout: 30000
```

`scripts/verdict_gate_check.sh` needs **no changes**: it already only proceeds on
`STATUS` in `{PASS, SKIPPED, ERROR}` — `HUMAN_REQUIRED` already falls to its blocking
branch, which (correctly) checks for a live `needs-discussion` label and exits 1 quietly
without double-posting a comment, since `validate`'s Phase 0 already posted its own. A
missing `blast.md` (e.g. `validate` itself didn't run) fails closed through the same
path, which strengthens rather than weakens the gate.

Re-point dependents of `validate` that exist purely to sequence after it (not for a
direct artifact need) onto `blast-gate` instead:
- `budget-conformance`'s `depends_on: [validate]` (line 1137) → `[blast-gate]`, which
  transitively gates `conformance` → `conformance-gate` → `push-and-pr` and everything
  chained after it (`code-review`, `status-in-review`, `report`).
- `push-resolve`'s `depends_on: [validate]` (line 1291) → `[blast-gate]` — otherwise a
  `resolve`-intent run could still push a blocked diff to its existing PR with no gate at
  all.

`validate` itself keeps its existing `trigger_rule: none_failed_min_one_success`
OR-join entry in `scripts/check_workflow_dag.py`'s `REQUIRED_OR_JOIN_NODES` unchanged —
`blast-gate` has a single upstream dependency (`validate`), so it is not itself an
OR-join and needs no `trigger_rule` override.

### D. New operator script: `.claude/skills/operator/scripts/approve-blast.sh`

```
approve-blast.sh N --sha <head>
```

Sources `lib.sh` like every other operator script. Run only by a human, locally, against
the real repo/board state — never by a phase agent, never triggered by an issue comment.

1. **Preconditions** (mirroring `approve.sh`'s existing checks): `needs-discussion`
   present on `#N`; board status is `Blocked`; no live run container for `#N`
   (`issue_run_container`); the `feat/issue-N-*` (or `refine/.../resolve` as applicable)
   branch exists on `origin` and its fetched HEAD equals `--sha` exactly, via explicit
   remote-tracking refs (never `FETCH_HEAD`).
2. **Recompute, never trust a stale artifact.** Run `gate_blast_radius.py` locally
   against the fetched branch HEAD (same `--base-ref $(git merge-base main HEAD)` the
   DAG uses) to get the *current* `TRIGGER`/`TRIGGERED_FILES`/`LINES_CHANGED`. Refuse
   (`die`) if the fresh result is not `HUMAN_REQUIRED` — there is nothing to approve.
   Print the trigger, the triggered files, and each file's diff for the operator to
   actually review before approving.
3. **Write the record** — fields: `issue`, `head_sha`, `base_ref`, `approved_at`,
   `approved_by` (local `git config user.name`), `lines_changed`, `trigger`, `files:
   {path: patch_id}`, plus `approvals_count` and `previous_head_sha` for an audit trail
   (the full history otherwise lives only in the audit-mirror comment from step 4; a
   single always-overwritten record is correct here — see Alternatives Considered).
   Written atomically (temp file + `mv`) via `docker exec "$DF_SCHEDULER" ...` against
   `${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}/blast-approvals/<N>.json`, then read
   back to confirm.
4. **Post an audit-mirror comment** on the issue (factory marker footer) — record only,
   carries no authority itself.
5. **Remove `needs-discussion` last** (after the record is durably written and
   confirmed) — this is what re-enables the scheduler's existing Blocked-retry dispatch
   (Priority 3 in `scheduler.sh`); the board stays `Blocked` (no board-status move is
   needed or performed — unlike `approve.sh`'s spec/plan flow, there is no
   `Backlog`→`Refined`/`Refined`→`Ready` transition here, since the issue is mid-implementation).

### E. `commands/dark-factory-conformance.md` — stop adjudicating prior gates

Add an explicit line (near Phase 1 LOAD, where the command already states what it does
and does not do): **conformance must never read `blast.md`, the `needs-discussion`
label, or board status to decide whether to run its review.** Enforcement of the
blast-radius gate is `blast-gate`'s job (item C) and `validate`'s Phase 0 (item B); by the
time `conformance` is dispatched, the DAG has already decided it's allowed to run.
`conformance`'s own Phase 5 BLOCKED note (lines 708-712), which already correctly
attributes real enforcement to DAG gate nodes rather than agent judgment for the
conformance-vs-spec check itself, is the template for this new line's phrasing — it
should not read as contradicting that existing note.

No equivalent change is needed in `commands/dark-factory-code-review.md` or
`commands/dark-factory-docs-conformance.md`: neither contains any reasoning about
`blast.md`, board status, or a prior gate's resolution (confirmed by inspection), and
`blast-gate` already protects both transitively (`code-review` depends on `push-and-pr`,
`docs-conformance` depends on `conformance-gate` — neither can be dispatched on an
unresolved `HUMAN_REQUIRED`). If the same symptom is ever observed in either phase, it
gets its own ticket rather than speculative preemptive changes here.

## Alternatives Considered

1. **Commit the approval record onto the branch itself** (e.g.
   `.archon/approvals/blast.json`), analogous to how `docs/superpowers/specs/*.md` lives
   on the branch for `approve.sh` to check. Rejected: the record would be part of the
   very diff it approves (its own commit would need to be excluded from the hash it's
   validating against — a self-referential mess), it would merge into `main` as
   permanent clutter, and `conformance`'s scope enforcement would flag it as an
   out-of-scope file needing excision on every run.
2. **Record the approval as a structured marker in an issue comment** (e.g. a hash
   embedded in a `<!-- blast-approval: ... -->` HTML comment). Rejected outright by the
   issue's own scope decision: "An issue comment alone must not be sufficient" — and
   CLAUDE.md already establishes that comment-channel input (even from a trusted,
   maintainer-authorized channel) may never authorize a `gate_*` change. A comment can
   still *mirror* the approval for human-readable audit (item D.4), but it is never the
   authority.
3. **Extend `approve.sh` with a third `blast` kind** instead of a new sibling script.
   Rejected: `approve.sh`'s entire flow (`from`/`to` board-status move, checking for a
   `docs/superpowers/{spec,plan}s/*.md` file on a `refine/...` branch) is specific to the
   spec/plan gates and doesn't transfer — a blast approval needs no board move, checks a
   different branch-naming convention, and needs to recompute and display a live trigger
   set rather than check a static file's existence.
4. **Hash the whole PR diff** rather than per-triggered-file patch-ids. Rejected: it
   would reconstruct today's loop one level down — any later commit (a conformance
   reconcile-cycle fix, a `revise-advisory` commit, even an unrelated whitespace fix)
   anywhere in the diff would invalidate a still-entirely-valid approval of the actually
   risky files.
5. **Leave the DAG topology unchanged; rely on `conformance` always running plus the
   pre-existing `close-preview` label check** (the issue's Direction 2, option (b)). This
   was seriously considered — it requires zero DAG changes and the needs-discussion label
   is already a global `scheduler.sh` dispatch-blocker. Rejected because it still lets a
   still-blocked diff progress through `conformance` → `push-and-pr` → `code-review` →
   `status-in-review` in the *same* run (the #212/#271 `exit 1`-doesn't-propagate quirk
   applies to `validate` exactly as much as it does to `conformance`/`code-review`), which
   would move the board to `In Review` while the diff is still blast-blocked — a real,
   observable state-correctness regression, not just wasted conformance budget. It also
   leaves #407 ("no deterministic consumer of `blast.md`") literally unaddressed; the
   only reader of `blast.md` would remain `report`, for display only.
6. **Store a list/union of historical approvals** rather than a single always-overwritten
   record. Rejected: `approve-blast.sh` always recomputes and re-approves the *entire*
   current trigger set on every run (files that haven't changed since a prior approval
   naturally get the same patch-id again), so the latest record always correctly
   describes everything a human has signed off on as of that approval. A union risks an
   old entry for a file whose content later changed silently surviving in the merged set
   and confusing the audit trail; the one place real history is needed (an audit log) is
   already served by the mirrored issue comments, not the mechanically-checked record.

## Open Questions (non-blocking)

- Whether `docs/run-lifecycle.md` items 3 and 4 (and any other as-built notes describing
  the current stateless/non-deterministic behavior) should be updated or removed as part
  of implementing this spec, or left as historical record with a forward pointer to this
  design. Recommendation for the plan phase: update them in place, since they describe
  defects this spec fixes.
- Exact wording/formatting of the rewritten blocked-comment (item B.3) and the audit
  comment (item D.4) is left to the implementing agent, following the existing
  `"Blast-Radius Gate — BLOCKED"` comment's tone and the factory-marker footer
  convention already used throughout `commands/*.md`.
- Whether `gate_blast_radius.py`'s approval-matching code should additionally reject a
  record whose `base_ref`/merge-base no longer matches current `main` (handling the edge
  case where `main` itself changed the content of a triggered file between approval and
  re-validation). Low-probability edge case (that file would also need to have changed on
  `main`, independently, in the same window) — left as an explicit assumption below rather
  than a hard requirement, to avoid over-engineering a rare case.

## Assumptions (flagged)

- `git patch-id --stable` is available in the factory image's git version (standard git
  plumbing command since git 1.x; not verified against the exact image git version as
  part of this spec — a plan-phase/implement-phase check, not a design risk).
- The `scheduler_state` Docker volume (`run-compose.yml` lines 58-59, 86-87, 112-113) is
  mounted with read-write access into the `dark-factory` run-container service in every
  deployed instance (self-target and MarketHawk) — this spec assumes parity with the
  self-target's `run-compose.yml` shown here; the plan phase should confirm the
  MarketHawk instance's compose file mounts the same volume into its equivalent service
  before this is assumed to generalize.
- `approve-blast.sh`'s `docker exec "$DF_SCHEDULER" ...` write path assumes the scheduler
  container is reachable and running from the operator's host at approval time, exactly
  as every other `lib.sh`-based operator script already assumes (`window_state()`,
  `status.sh`, `usage.sh`, `runs.sh`).
- This spec does not change `blast_radius.enabled`'s existing kill-switch semantics
  (base-ref-only read, never the working tree) — approval matching is an orthogonal,
  additive check that only ever runs when the existing logic would otherwise emit
  `HUMAN_REQUIRED`, and never when the gate is disabled.
