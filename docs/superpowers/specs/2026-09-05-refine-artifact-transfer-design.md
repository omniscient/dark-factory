# Automatic transfer of refine-branch spec/plan artifacts onto the feat branch

**Issue:** #387
**Status:** spec-pending-review

## Overview / Problem Statement

`setup-branch` (`workflows/archon-dark-factory.yaml`) forks `feat/issue-N-*` from `main` at the
start of every `new`/`continue` implement dispatch. The ticket's own spec and plan, however, live
only on the sibling `refine/issue-N-*` branch — they were never merged to `main` and do not
transfer to the new fork automatically. Gate 2 (conformance, `commands/dark-factory-conformance.md`
Phase 2) locates the spec by scanning `docs/superpowers/specs/` in the local clone (falling back
through the "Plan Generated" comment and `refinement-status.md`); if the file is missing from the
feat branch, all three lookups miss and conformance sets `NO_SPEC=true`, silently downgrading to an
advisory-only review against the raw issue body instead of a blocking review against the approved
spec — the strongest gate in the pipeline goes quiet exactly when it should be checking the most
detail.

Five consecutive plans hit this: #381 (self-caught by its own architect, which added a manual
"Task 0" copy step), #384, #382, #358, #354 (each needed an identical operator amendment commit,
`docs(#N): copy spec/plan onto the implementation branch`, applied by hand after the plan gate).
The `[PATTERN]` memory lesson from issue #42 (`.archon/memory/codebase-patterns.md`) already
documents the requirement, but a memory entry only helps when the planning agent both retrieves it
and acts on it — empirically 1 in 5 runs. A prose reminder cannot be the fix for a mechanical,
every-single-run transfer step.

## Requirements

(Captured from the brainstorming Q&A below.)

1. **Structural fix only — no producer-side Task 0 mandate.** The transfer must happen
   automatically in `setup-branch`; `commands/dark-factory-plan.md` is not changed to require a
   Task 0 template. A second prose instruction would still depend on agent compliance (the exact
   failure mode this ticket exists to remove) while adding a second surface, plan tokens, and a
   self-review check to maintain — the fix has to live where it is guaranteed to run.
2. **Reuse `scripts/push_gate_check.sh` for artifact association**, per the issue's own
   direction, rather than a second, independent, simpler matcher (e.g. filename-contains-issue-
   number). Every current spec/plan filename follows the `YYYY-MM-DD-slug-design.md` convention
   with no issue number in it at all — a filename-only matcher would find nothing in the common
   case. Real association is `push_gate_check.sh`'s existing two-pass logic (content `#N` match,
   falling back to per-commit-subject association, #382) — reimplementing it a second time risks
   the two copies drifting apart.
3. **`push_gate_check.sh` must accept an optional ref argument.** The script is hard-coded to
   diff/list against `HEAD`, but the artifact to transfer lives on the (not checked out) refine
   branch, not on the newly-forked feat branch. Add a third, optional argument (a git ref,
   defaulting to `HEAD`) so every existing caller is unaffected and the new transfer step can pass
   the refine branch's remote-tracking ref.
4. **Gate the transfer on "did `setup-branch` just fork a new branch from `main`", not on
   "is the spec currently absent from HEAD".** An absence-based check is actively wrong: once a
   branch has been through `push-and-pr`, that node `git mv`s the spec/plan into `docs/archive/`
   (`workflows/archon-dark-factory.yaml`'s archive step), after which `push_gate_check.sh`
   correctly reports empty at `HEAD` for that prefix. An absence-gated transfer on a later
   `continue` dispatch against the same branch would re-copy (resurrect) the archived spec/plan
   from the refine branch, and then collide the next time `push-and-pr` tries to `git mv` it into
   `docs/archive/` (destination already exists) — turning a review-quality bug into a hard node
   failure. Fresh-fork-only gating makes the missing-spec state structurally impossible for every
   branch created after this fix ships; a `continue` branch inherits the transfer commit from its
   own first run, so no absence check is ever needed.
5. **Non-fatal when no refine branch exists.** Some issues never go through refine (a
   human-authored spec committed directly to the feat branch, or a `direct-to-pr` ticket). The
   transfer step must log a visible, structured, greppable line (e.g. `SPEC_TRANSFER: none (no
   refine/issue-N-* branch on origin)`) and continue — never fail the `setup-branch` node.
   Conformance's existing `NO_SPEC=true` advisory fallback remains the safety net for this case,
   unchanged.
6. **Deterministic branch selection when multiple refine branches exist.** `setup-refine-branch`
   re-slugs the branch name from the issue's live title on every refine/plan dispatch, so a title
   edit between the refine and plan runs can leave more than one `refine/issue-N-*` branch on
   origin. Selection must not rely on `ls-remote`/lexical ordering; pick the most recently
   committed branch and log which one was used.
7. **The copy commit must self-identify the issue in its subject** (matching the convention
   already established by the four manual amendment commits: `docs(#N): copy spec/plan onto the
   implementation branch`), so `push_gate_check.sh`'s pass-2 commit-subject association can still
   find the file even in the edge case where the spec/plan body text itself never mentions the
   issue number.
8. **The stale memory guidance must be corrected as part of implementation.** The `[PATTERN]`
   entry from issue #42 (`.archon/memory/codebase-patterns.md`) currently instructs the *implement
   phase* (i.e., the agent) to manually copy and commit the artifacts. Once this ships, that copy
   happens automatically before the agent ever starts; the entry should be updated (not left to
   mislead future agents into re-doing manual Task 0 work) to describe the automatic transfer and
   note that manual copying is no longer needed.
9. Regression tests must cover: `push_gate_check.sh`'s new ref argument (explicit ref,
   omitted-arg backward compatibility, nonexistent ref → empty result, still-exit-0); the new
   transfer script/step in isolation (finds and copies both files, copies only the one that
   exists, no-ops cleanly when no refine branch exists, picks the most recent of multiple refine
   branches); and a DAG-level static-content test (mirroring `tests/test_push_gate_dag.py`'s
   convention of asserting on the node's `bash:` string rather than executing it) confirming
   `setup-branch` invokes the transfer step only on the fresh-fork paths.

## Architecture / Approach

### 1. `scripts/push_gate_check.sh`: optional third `<ref>` argument

Add `REF="${3:-HEAD}"` and substitute `REF` for every existing hard-coded `HEAD` (all four use
sites): `git rev-list --count origin/main..${REF}`, `git diff -z --name-only origin/main...${REF}
-- "$ARTIFACT_PREFIX"`, `git log --format=%H origin/main..${REF}`, and the pass-2 existence probe
`git cat-file -e "${REF}:$_touched"`. No other logic changes — the exit-0-always contract, the
numeric-only guard on `$ISSUE_NUM`, and the `origin/main` (not local `main`) convention are
untouched, and every existing caller (which passes only two arguments) is byte-for-byte unaffected.

### 2. New script: `scripts/transfer_refine_artifacts.sh <issue-number>`

A small, independently testable script (matching the existing pattern of dedicated scripts called
from DAG bash nodes, e.g. `push_gate_check.sh`, `oos_excise.sh`), run from the *currently checked
out* branch (the freshly forked `feat/issue-N-*`):

1. Validate `$1` is numeric (same guard style as `push_gate_check.sh`); exit 0 on a bad value.
2. `git fetch origin` (best-effort; already fetched earlier in the run in practice).
3. Resolve candidate refine branches: `git for-each-ref --sort=-committerdate --format='%(refname:short)' "refs/remotes/origin/refine/issue-${ISSUE}-*"`. If empty, print `SPEC_TRANSFER: none (no refine/issue-${ISSUE}-* branch on origin)` and exit 0. If more than one, log (stderr) which one was chosen and proceed with the most recent.
4. For each of `docs/superpowers/specs/` and `docs/superpowers/plans/`, call
   `push_gate_check.sh "$PREFIX" "$ISSUE" "$REFINE_REF"` (the new third argument) to find the
   associated file on the refine branch tip. If found, `git checkout "$REFINE_REF" -- "$FILE"; git
   add "$FILE"`.
5. If anything was staged, commit: `git commit -m "docs(#${ISSUE}): copy spec/plan onto the
   implementation branch"`. Print `SPEC_TRANSFER: <n> file(s) from <ref>`. If nothing was staged,
   print `SPEC_TRANSFER: none (no matching spec/plan found on <ref> for #<issue>)`.
6. `set -uo pipefail`, never `set -e`; every path ends in an explicit `exit 0` so a caller can
   `|| true` it defensively without masking anything, and a transfer miss never fails the
   `setup-branch` node.

### 3. `setup-branch`: call the transfer script only on a genuine fresh fork

`setup-branch` currently has two branch-creation code paths — the `intent=new` path (always `git
checkout -b`) and the `intent=continue` path's fallback (`git fetch ... && git checkout ... ||
git checkout -b`, taken when no remote feat branch exists yet). Both are genuine forks from `main`
and must trigger the transfer; the existing-branch-reuse path (`continue` successfully checking
out an already-pushed feat branch) must not. Track this with an explicit flag set in both
`checkout -b` call sites (not derived from `$INTENT` alone, since `continue` can still hit
`checkout -b` on its fallback):

```bash
NEW_BRANCH=false
if [ "$INTENT" = "continue" ]; then
  if git fetch origin "$BRANCH" 2>/dev/null && git checkout "$BRANCH"; then
    :
  else
    git checkout -b "$BRANCH"
    NEW_BRANCH=true
  fi
else
  git checkout -b "$BRANCH"
  NEW_BRANCH=true
fi

if [ "$NEW_BRANCH" = "true" ]; then
  bash "${CLONE_DIR:-.}/dark-factory/scripts/transfer_refine_artifacts.sh" "$ISSUE" || true  # TARGET-PATH
fi

echo "$BRANCH"
```

The `|| true` is defense-in-depth on top of the script's own unconditional `exit 0` — a transfer
failure must never abort branch setup, since conformance's `NO_SPEC=true` fallback is the designed
safety net for "no spec found."

`setup-branch-resolve` (the `resolve` intent, which checks out an *existing* feat branch to merge
main in) is untouched — it never forks a new branch from `main`, so no transfer is needed there.

### 4. Downstream: no changes needed

`push-and-pr`'s archive step and the budget-telemetry nodes already call `push_gate_check.sh`
against `HEAD` on the feat branch with no ref argument (defaulting to the unchanged `HEAD`
behavior) — once the spec/plan is committed onto the feat branch by the transfer step, they find
and archive it exactly as they do today for any other committed artifact. `commands/dark-factory-
conformance.md`'s `NO_SPEC=true` fallback logic is also untouched — it remains correct for the
genuine no-refine-branch case.

### 5. `.archon/memory/codebase-patterns.md`: correct the now-stale `[PATTERN]` entry

Update the issue-#42 entry so it describes the automatic transfer (and that manual copying is no
longer necessary) instead of instructing the implement-phase agent to do it by hand.

## Alternatives Considered

- **Producer-side Task 0 mandate in `commands/dark-factory-plan.md`** (the issue's fallback
  option). Rejected as the primary fix: the issue's own evidence is 1-in-5 compliance today even
  with a `[PATTERN]` memory entry already surfacing the requirement; a second prose instruction
  buys similar partial coverage at the cost of a second surface to maintain, for a mechanical step
  that has no reason to depend on an LLM remembering it.
- **Absence-gated transfer** (run the copy on every `setup-branch` dispatch when
  `push_gate_check.sh` reports the spec/plan missing at `HEAD`, instead of gating on "just forked
  a new branch"). Rejected: incorrectly resurrects an already-archived spec/plan on a `continue`
  dispatch that runs after `push-and-pr` has `git mv`'d it into `docs/archive/`, and then collides
  with the next archive attempt (destination path already exists) — converts a review-quality bug
  into a hard node failure. Fresh-fork-only gating has no such failure mode and is sufficient,
  since every branch created after this ships inherits the transfer commit from its own first run.
- **Independent, simpler filename-based matching for the new transfer step** (skip reusing
  `push_gate_check.sh`). Rejected: no current spec/plan filename contains an issue number, so this
  would find nothing in the common case; the only real association logic is
  `push_gate_check.sh`'s existing content/commit-subject passes, and reimplementing that logic a
  second time is a drift risk for no benefit.
- **Hard-fail `setup-branch` when no refine branch is found.** Rejected: legitimate no-refine-
  branch cases exist (human-authored spec, `direct-to-pr`, a deleted refine branch after an
  earlier merge); failing the whole implement dispatch over a missing spec turns an advisory-
  review degradation into full per-issue retry churn against the breaker, for a condition Gate 2
  already surfaces visibly via `NO_SPEC=true`.

## Open Questions (non-blocking)

- Should the four existing `grep -rl "#${ISSUE}"` / `push_gate_check.sh` call sites across the
  DAG eventually consolidate on a single shared association helper so ref-handling and pass-2
  logic can't drift between callers? Out of scope here (this ticket only adds an optional
  argument, it doesn't restructure the call sites); worth a follow-up if a future ticket touches
  this area again.
- The five already-affected tickets (#381, #384, #382, #358, #354) are unaffected by this fix —
  their spec/plan artifacts are already committed and, in most cases, already archived. No
  backfill is proposed; this ticket only prevents recurrence going forward.

## Assumptions

- Refine/plan branches produced by the factory are linear (no merge commits) — inherited from
  `push_gate_check.sh`'s existing pass-2 assumption, unchanged by the new ref argument.
- `origin` is fetched and reachable in every environment `setup-branch` runs in today (existing
  assumption for the rest of the node, unchanged).
- The transfer step running once, synchronously, inside `setup-branch` (rather than as a separate
  DAG node) is acceptable: `implement` already depends transitively on `setup-branch` via
  `update-codeindex` → `budget-implement` → `enforce-budget-implement`, so no new DAG edges,
  `depends_on` entries, or `trigger_rule` (OR-join) considerations are introduced by this change —
  keeping it inline avoids `scripts/check_workflow_dag.py`'s OR-join validation surface entirely,
  consistent with the issue's framing of this as "additive setup, not a gate change."
