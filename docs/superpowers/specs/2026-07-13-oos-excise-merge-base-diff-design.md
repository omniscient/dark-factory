# Fix `oos_excise.sh` to Diff Against the Merge Base, Not Raw `origin/main`

**Issue:** omniscient/dark-factory#266
**Status:** draft — pending review
**Direct-to-PR:** No (deliberately not requested — this is a gate change, reviewed on its own per CLAUDE.md)

---

## Overview / Problem Statement

`scripts/oos_excise.sh` decides which files a refine/plan run touched "out of scope" by
running `git diff --name-only origin/main HEAD` — a raw, two-dot comparison of the current
tips of `origin/main` and the working branch. This conflates two unrelated things:

1. Files the working branch's own commits actually changed (the thing the gate is supposed to
   police), and
2. Files `origin/main` changed independently, via other tickets merging after the working
   branch forked (irrelevant to this branch's scope, but shows up in the diff anyway because
   two-dot diff has no concept of a fork point).

When `main` moves forward while a branch is in flight, category (2) floods the "out of scope"
set with files the branch never touched. The excise loop then either force-restores them to
`origin/main`'s latest content (silently discarding nothing real, but noisy) or — the
destructive case — deletes them outright whenever `origin/main`'s current tip no longer
contains them (e.g. a later merge to `main` moved or removed that file). That destructive path
fired for real on 2026-07-13: a refine run on the issue #251 branch silently deleted
`scripts/factory_core/providers/*`, which existed at the branch's fork point but had been
removed from `main` by an unrelated merge in the interim; the branch had never touched those
files. A companion plan run on the same branch also excised the branch's own freshly-approved
spec before the agent caught and reverted it. Issue #208 is a suspected earlier casualty of the
same bug (a feat branch that ended up byte-identical to `main` with no implementation artifact).

Every other diff-based gate in this repo already avoids this trap: `dark-factory-conformance.md`,
`dark-factory-code-review.md`, and `dark-factory-implement.md` all diff `main...HEAD`
(three-dot / merge-base semantics), and `dark-factory-validate.md` diffs
`origin/main...HEAD --name-only` for exactly the "what did this branch itself change"
question. `oos_excise.sh` is the sole outlier still using the raw two-dot form.

## Requirements

Distilled from the issue and Q&A below:

1. `oos_excise.sh`'s out-of-scope detection must consider only files changed by commits made
   *on the working branch* since it diverged from `origin/main` — not files that differ merely
   because `origin/main` moved forward independently.
2. A file inherited unchanged from the branch's fork point, which a later independent merge to
   `main` deletes or moves, must never be flagged as out-of-scope and must never be deleted
   from the branch.
3. A file the branch's own commits create or modify, and that falls outside the caller's
   allowed-prefix scope, must still be excised exactly as today (restored from `origin/main`'s
   current tip if it exists there, deleted if it doesn't) — this is correct, intended
   behavior, not part of the bug.
4. Add regression test coverage for the specific fork-point scenario in the issue: a fixture
   repo where `origin/main` receives an additional commit *after* the working branch forks,
   touching a file outside the caller's allowed prefixes that the branch itself never commits
   a change to.
5. Scope stays exactly `scripts/oos_excise.sh` + its tests, per the issue and CLAUDE.md's rule
   that gate changes get their own ticket. No other gate, config, or command file changes.

## Brainstorming Q&A

> **Q1:** The existing `TestOosExciseBehaviorParity` test class exists specifically to assert
> the script's output is byte-identical to the OLD inline two-dot block it was extracted from.
> That parity premise becomes obsolete (and actively misleading) once the script's semantics
> change on purpose. Should the plan delete that class outright, or keep it but update its
> embedded "inline" snippet to three-dot semantics too?
>
> **A1:** Delete `TestOosExciseBehaviorParity` entirely; do not rewrite its inline snippet to
> three-dot. Its whole purpose was proving a behavior-preserving extraction — once the
> extraction is deliberately made behavior-*changing*, "still matches the old inline two-dot
> block" is the opposite of an invariant worth holding, and leaves a blessed, tested copy of
> the buggy two-dot form sitting in the tree. Rewriting the snippet to three-dot instead would
> just produce a tautology (two copies of the same algorithm agreeing) with no coverage value
> beyond what a direct behavioral assertion already gives. This repo's test convention for this
> file is behavioral (concrete repo state in, concrete observable outcome out — file excised /
> restored / `out-of-scope.md` contents / commit made / stdout-vs-stderr routing); the new
> fork-point regression belongs in `TestOosExciseScript` as ordinary behavioral tests, built on
> the existing `git_repo` fixture with an extra commit pushed to `origin/main` after the branch
> forks. Note: the existing parity tests would *not* actually fail on their own after the fix
> (their fixtures never advance `main` post-fork, so two-dot and three-dot agree there) — which
> is exactly why they should be deleted rather than kept as false-confidence scaffolding.

> **Q2:** The evidence describes files inherited at the branch's fork point
> (`scripts/factory_core/providers/*`) that a later upstream merge deleted from `main`, which
> the old two-dot detection wrongly treated as "new OOS files" and permanently removed. Once
> detection switches to merge-base/three-dot, those files simply never enter the excise loop
> (the branch never committed a change to them). Is changing the detection line sufficient, or
> should the delete-vs-restore branch ((b): delete when a flagged file is absent from
> `origin/main`'s current tip) also gain a merge-base guard as defense in depth?
>
> **A2:** Changing the detection line alone is sufficient and provably closes the evidence — a
> file only reaches the excise loop if it appears in the merge-base…HEAD diff, i.e. the branch's
> own commits touched it, so the inherited-and-later-upstream-deleted class is structurally
> unreachable after the fix. Do **not** add a merge-base guard to branch (b): the only way a
> file can still reach (b) post-fix is when the branch itself modified the file AND main
> independently deleted it — in that case, deleting the branch's out-of-scope resurrection of a
> file `main` intentionally removed is the *correct* alignment-to-main behavior; a guard that
> restored it instead would invert the gate's intent and reintroduce a file `main` deliberately
> dropped. (a)/(b) both intentionally resolve against `origin/main`'s current tip, not the
> merge-base, because the gate's job is aligning the branch to main, not to the stale fork
> point — that is existing, correct behavior and is unchanged by this fix.

## Architecture / Approach

**Change exactly one line** in `scripts/oos_excise.sh` (line 22), the `OOS_FILES` detection:

```bash
# Before (two-dot, raw tip comparison — the bug):
OOS_FILES=$(git diff --name-only origin/main HEAD 2>/dev/null | while read -r f; do

# After (three-dot, merge-base — matches dark-factory-validate.md's existing convention):
OOS_FILES=$(git diff --name-only origin/main...HEAD 2>/dev/null | while read -r f; do
```

`git diff A...B` is git's built-in shorthand for `git diff $(git merge-base A B) B` — it is not
necessary to compute and pass the merge-base explicitly; the three-dot form already means
exactly "what did commits reachable from B, but not from A, change." This is the same pattern
already used in `commands/dark-factory-validate.md` line 119
(`git diff --name-only origin/main...HEAD`).

The restore-vs-delete logic (lines 34–43: restore from `origin/main`'s current tip if the file
exists there, else delete) is **unchanged** — per A2, it is correct as written and resolving
against `origin/main`'s current tip (not the merge-base) is the intended "align to main"
behavior.

Factory containers perform a full (non-shallow) `git clone` (`entrypoint.sh` line 485, no
`--depth`), so `git merge-base origin/main HEAD` is always resolvable in this environment —
no defensive handling for a truncated/shallow history is needed.

### Test changes

- **Delete** `TestOosExciseBehaviorParity` and its two test methods
  (`test_parity_no_oos_files`, `test_parity_with_oos_new_file`) and its helper methods
  (`_run_inline_oos_side_effects`, `_build_bare_origin`, `_clone`), per Q1/A1.
- **Add** to `TestOosExciseScript`, using the existing `git_repo` fixture:
  - A test where, after the working branch forks from `origin/main`, an additional commit is
    pushed to `origin/main` (simulating another ticket merging) that touches a file outside the
    caller's allowed prefixes and that the working branch itself never commits a change to.
    Assert the script's stdout does **not** include that file and the file is left untouched
    in the working tree.
  - A test covering the same setup but where the file in question exists at the fork point and
    is then *removed* by the `origin/main`-only commit (the exact `factory_core/providers/*`
    evidence shape) — assert the file is **not** deleted from the working branch.
  - A test confirming a file the branch's own commits *do* modify, outside the allowed
    prefixes, is still excised exactly as before (regression guard for requirement 3 — this is
    already covered by existing `TestOosExciseScript` tests, but the plan should confirm none
    of those tests relied on `origin/main` staying static, since they will now run against the
    three-dot code path).

## Alternatives Considered

1. **Compute merge-base explicitly**: `MB=$(git merge-base origin/main HEAD); git diff
   --name-only "$MB" HEAD`. Functionally identical to the three-dot form. **Rejected** — the
   three-dot form is shorter, is git's canonical idiom for this exact question, and already
   has a precedent in `dark-factory-validate.md`; introducing a second spelling for the same
   operation would add inconsistency for no behavioral benefit.
2. **Also guard branch (b) with an explicit merge-base check** (defense in depth). **Rejected**
   per Q2/A2 — the guard is unreachable-by-construction for the evidence's failure class, and
   in the one scenario where it would fire, it would produce the wrong outcome (reintroducing a
   file `main` deliberately deleted).
3. **Chosen: single-line detection fix (two-dot → three-dot), restore/delete logic unchanged,
   parity tests deleted and replaced with fork-point-specific behavioral tests.** Minimal,
   scoped exactly to the reported bug, consistent with the diffing convention already used
   everywhere else in this repo's gates.

## Open Questions (Non-blocking)

- A `.archon/memory/codebase-patterns.md` entry from issue #250 recommends two-dot
  `git diff origin/main HEAD -- <file>` for testing whether a *specific, already-identified*
  file's content is genuinely different from `main`'s current tip (e.g. to decide whether a
  revert would be a no-op), and warns that three-dot can flag a file as changed even when its
  final content is net-identical to `main`. That entry is about a narrower single-file
  content-equality question and does not apply to `oos_excise.sh`'s changed-file-*set*
  detection (which is exactly the "what did the branch's own commits touch" question that
  three-dot answers correctly, and which is now uniform with `dark-factory-validate.md`,
  `dark-factory-conformance.md`, `dark-factory-code-review.md`, and `dark-factory-implement.md`
  — see Assumptions). No action needed for this ticket; flagged here so a future memory-hygiene
  pass can clarify the entry's scope if it causes confusion again.
- The nested untracked `dark-factory/` directory present in this branch's working tree (visible
  in `git status` at the start of this refinement run) is unrelated to this issue and is left
  untouched.

## Assumptions

- **[Flagged]** The `.archon/memory/codebase-patterns.md` PATTERN entry from #250 recommending
  two-dot diffing is read as scoped to single-file content-equality checks, not general
  changed-file-set detection, based on (a) its own wording ("test whether a file is truly
  out-of-scope... if empty, main already carries the same content" — an equality check, not a
  set-membership check) and (b) the overwhelming codebase precedent of three-dot/merge-base
  semantics for every other "what changed on this branch" question in `commands/*.md`. If this
  reading is wrong, the memory entry should be revisited rather than this fix reverted, since
  the fix is independently justified by the issue's reproduced evidence.
- This fix assumes factory containers always perform a full, non-shallow clone (confirmed in
  `entrypoint.sh`); if that ever changes to a shallow/`--depth` clone, `git merge-base` could
  fail to find a common ancestor and the script (running under `set -euo pipefail`) would abort
  the calling phase. That would be a pre-existing risk of the clone-depth choice, not something
  introduced by this fix, and is out of this ticket's scope.
