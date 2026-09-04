# Push-gate detection of committed artifacts lacking an issue number in filename/content

**Issue:** #382
**Status:** spec-pending-review

## Overview / Problem Statement

`refine-push` and `plan-push-and-advance` (`workflows/archon-dark-factory.yaml`) gate the
push + gate-label step on `scripts/push_gate_check.sh` finding a committed artifact under
`docs/superpowers/specs/` / `docs/superpowers/plans/` that is associated with the issue. The
association test only looks at (a) the issue number delimited in the artifact's **filename**,
or (b) `#<num>` in the artifact's **content**. Nothing requires the refine/plan agent to put the
number in either place — only the **commit message** is mandated by convention.

On 2026-08-31 (issue #381), the agent committed a 230-line spec as
`docs/superpowers/specs/2026-08-31-handoff-a5-review-followups-design.md` — no `381` in the
filename, zero occurrences of `381` in the body — with the issue number only in the commit
subject (`docs(spec): #381 handoff A5 review follow-ups — tests, docs, edge cases`). The gate
declared this a "silent death" (`df-refine-failure`), applied no label, and the scheduler
re-dispatched the same refine run every poll (~$2.27 / ~8 min of the Claude window per cycle)
until an operator applied `spec-pending-review` by hand.

`push_gate_check.sh` behaved exactly per its documented contract; the artifact was simply
written in a shape the contract doesn't cover. This is a `#212`-class safety check — the fix
must strictly widen what counts as "detected", and must not introduce any path where a
genuinely missing artifact is reported as present (fail-closed must hold).

## Requirements

(Captured from the two-round brainstorming Q&A below.)

1. **Detector-side widening is the load-bearing fix.** `push_gate_check.sh` must also
   recognize an artifact whose only issue-number reference is a **commit subject** on the
   branch, without weakening any existing detection path. A prompt instruction to an LLM
   agent (producer-side) is advisory and cannot alone guarantee the gate stays correct on
   every run — the script must be deterministic.
2. **Fail-closed invariant, stated structurally:** the file path `push_gate_check.sh` prints
   must always be a member of `git diff -z --name-only origin/main...HEAD -- "$ARTIFACT_PREFIX"`
   (the existing three-dot candidate list). No code path may print anything else. This makes
   the existing regression tests (`test_commits_beyond_main_but_no_artifact_reproduces_212`,
   `test_uncommitted_artifact_file_not_detected`) pass without modification — an empty
   candidate list makes every new check a no-op.
3. **Per-commit association, not a global fallback.** A commit-subject match may only
   associate a file that was actually touched by *that* commit under the artifact prefix.
   A global "any commit on the branch mentions `#<num>` → report the first candidate file"
   fallback is rejected: a refine/plan branch commonly carries a side commit mentioning a
   different issue (e.g. `Depends on:`/`Refs:` conventions, or a `memory: lessons from issue
   #N` commit), and the global form would mis-associate that unrelated file — trading the
   infinite-retry bug for a silently-mislabeled-issue bug, the exact failure class `#212`
   exists to prevent.
4. **Existing behavior for anything that already passes must be byte-identical.** The new
   commit-subject pass runs strictly after the existing filename/content pass and only when
   that pass finds nothing, so it cannot change which file is reported for inputs that match
   today.
5. **Producer-side mandate is required, not optional hygiene.** Three other DAG call sites —
   `workflows/archon-dark-factory.yaml:393`/`:908` (budget telemetry, best-effort) and,
   critically, `:1007`-`:1008` (the PR-push archive step, which `git mv`s the spec/plan into
   `docs/archive/`) — do their own narrower `grep -rl "#${ISSUE}"` **content-only** detection
   and never read commit messages. An artifact that only self-identifies via commit subject
   would pass the widened push gate but then be invisible to the archive step and never get
   archived. `commands/dark-factory-refine.md` and `commands/dark-factory-plan.md` must
   therefore mandate a `**Issue:** #<num>` line in the artifact body (the format every
   existing spec/plan in the repo already uses informally) so the content-only call sites
   keep working regardless of the push-gate change.
6. Must not touch `push_gate_check.sh`'s existing exit-0-always contract, its numeric-only
   guard on `$ISSUE_NUM`, or its `origin/main` (not local `main`) convention.
7. Regression tests must pin: (a) the `#382` reproducer — artifact committed with numberless
   filename/content but `#<num>` in the commit subject → detected; (b) the discriminator
   between per-commit and global association — an unrelated commit merely mentioning the
   issue number must not cause an unrelated file to be reported; (c) all nine existing tests
   in `tests/test_push_gate_check.py` continue to pass unmodified.

## Architecture / Approach

### `scripts/push_gate_check.sh`: two sequential passes

Inside the existing `if [ "$HAS_COMMITS" -gt 0 ]` block:

**Pass 1 (unchanged).** Iterate the three-dot diff candidate list
(`git diff -z --name-only origin/main...HEAD -- "$ARTIFACT_PREFIX"`) exactly as today —
filename-delimited-number match, or `grep -Eq "#${ISSUE_NUM}\b"` on content; first match wins,
print, `exit 0`.

**Pass 2 (new).** Only reached when pass 1 finds nothing:

1. Collect commit SHAs on the branch: `git log --format=%H origin/main..HEAD` (two-dot — "on
   HEAD, not on origin/main", matching the existing `git rev-list --count origin/main..HEAD`
   guard so `HAS_COMMITS > 0` and "commit list non-empty" agree). This is deliberately a
   **different range shape** from pass 1's file-list diff, which stays three-dot per the
   existing header comment and the `#250` memory (`codebase-patterns.md`) — the file-list
   question ("which files did this branch touch") and the commit-list question ("which commits
   are uniquely this branch's own") are different questions; do not unify the two ranges.
2. For each SHA, read its subject only: `git show -s --format=%s "$_sha"`. Match with the
   same explicit non-digit-delimited form used for the filename check —
   `[[ "$_subj" =~ \#${ISSUE_NUM}([^0-9]|$) ]]` — not full commit-message body (`git log
   --grep` would also match trailer lines like `Depends on: #N`, producing false positives
   from unrelated issue numbers this repo's own conventions put in bodies).
3. For each matching SHA, list its prefix-scoped paths:
   `git diff-tree --no-commit-id -r -z --name-only "$_sha" -- "$ARTIFACT_PREFIX"`. Accumulate
   into a set. Additionally require `git cat-file -e "HEAD:$_file"` (the path must still exist
   at HEAD) so a file added then deleted on the branch cannot be reported as a live artifact —
   this guard applies only to pass 2's new paths, not retrofitted onto pass 1.
4. Re-walk the pass-1 candidate list **in its original diff order** and print the first
   candidate present in the pass-2 set. Same "first in diff order" tie-break as pass 1, so
   output stays deterministic regardless of commit order.
5. `exit 0` unconditionally, as today, whether or not pass 2 found anything.

Implementation notes to carry into the diff:
- Build the loop without a `cmd1 | cmd2` pipeline for the commit-subject match (use
  `git log --format=%H` piped only into a `while read` populated via process substitution,
  then `git show -s --format=%s` per SHA in the loop body) — `set -uo pipefail` is already on,
  and a piped `grep` with no match would make its command substitution exit non-zero. There's
  no `set -e` today so this isn't a live bug, but the shape should not become status-sensitive
  if `set -e` is ever added later.
- Emit a stderr trace on a pass-2 association (`push_gate_check: associated <path> via commit
  subject <sha>` to stderr) for operator debuggability — must go to stderr, not stdout, so it
  cannot be captured into `$SPEC_FILE`/`$PLAN_FILE` at
  `workflows/archon-dark-factory.yaml:433`/`:482`.
- A merge commit's subject matching yields no `diff-tree -r` output without `-m`/`-c` — this
  is acceptable fail-closed behavior; refine/plan branches are linear in current practice.

### `commands/dark-factory-refine.md` / `commands/dark-factory-plan.md`: producer-side mandate

Add to refine's Phase 5 (Spec Writing) and plan's Phase 2 (Plan Writing) conventions list: the
spec/plan must include a `**Issue:** #<num>` line (matching the format already used by every
existing file under `docs/superpowers/specs/` and `docs/superpowers/plans/` — plans currently
write the fuller `omniscient/dark-factory#<num>` form, which also satisfies `#<num>` matching).
Add this check to each command's self-review step (refine Phase 5 step 4 / plan's Phase 2
conventions) so a missing line is caught and fixed inline before commit, the same way the
existing placeholder/consistency/scope checks work.

## Alternatives Considered

- **Global commit-subject fallback (any matching commit on the branch → report first candidate
  file).** Rejected: mis-associates an unrelated file when the branch carries a side commit
  referencing a different issue number (common via `Depends on:`/`Refs:` conventions or a
  `memory: lessons from issue #N` commit) — turns the infinite-retry bug into a
  silently-mislabeled-issue bug, the `#212` failure class this check exists to prevent.
- **`git log --grep` against full commit messages instead of subject-only.** Rejected: matches
  trailer/body lines (`Depends on: #N`) as readily as the author-controlled subject line,
  reintroducing the same cross-issue false-positive risk as the global fallback.
- **Producer-side mandate only (no detector change).** Rejected: relies on an LLM instruction
  holding on every run with no deterministic backstop; does not fix runs that already
  committed a numberless artifact under the old convention, and the `#212`-class check must
  not depend on prompt compliance to stay correct.
- **Widening the archive-step and telemetry `grep -rl "#${ISSUE}"` call sites to share
  `push_gate_check.sh`'s logic.** Real consistency gap (identified during Q&A — the archive
  step at `workflows/archon-dark-factory.yaml:1007`-`:1008` only does content-only detection
  and would still miss a commit-subject-only artifact), but out of scope for this S-sized
  ticket: it touches three additional DAG call sites and the PR-time archive path, not the
  refine/plan-time gate this issue is about. Filed as a follow-up (see Open Questions).

## Open Questions (non-blocking)

- Should `workflows/archon-dark-factory.yaml`'s budget-telemetry (`:393`, `:908`) and
  archive-step (`:1007`-`:1008`) `grep -rl "#${ISSUE}"` call sites be consolidated onto
  `push_gate_check.sh` (or a shared detection library) so all four call sites agree on what
  counts as "this issue's artifact"? Recommend a follow-up ticket; not blocking here because
  requirement 5 (producer-side mandate) keeps those call sites correct without modifying them.

## Assumptions

- Refine/plan branches produced by the factory are linear (no merge commits) — pass 2's
  `git diff-tree -r` (without `-m`/`-c`) fail-closed behavior on a merge commit is acceptable
  under this assumption.
- `origin/main` is always fetched and reachable in the environments `push_gate_check.sh` runs
  in today (existing assumption, unchanged by this ticket).
