# Implementation Plan: Automatic transfer of refine-branch spec/plan artifacts onto the feat branch

**Issue:** #387
**Spec:** `docs/superpowers/specs/2026-09-05-refine-artifact-transfer-design.md`

## Goal

`setup-branch` (`workflows/archon-dark-factory.yaml`) forks `feat/issue-N-*` from `main` on
every fresh `new`/`continue` implement dispatch. The ticket's own spec/plan live only on the
sibling `refine/issue-N-*` branch and never transfer, so Gate 2 (conformance) silently
downgrades to `NO_SPEC=true` advisory-only review. This plan extends
`scripts/push_gate_check.sh` with an optional ref argument, adds a new
`scripts/transfer_refine_artifacts.sh` that reuses it against the refine branch, wires that
script into `setup-branch`'s two genuine fresh-fork paths only, and corrects the now-stale
`[PATTERN]` memory entry (issue #42) that currently instructs agents to copy the files by hand.

## Architecture

```
scripts/push_gate_check.sh <prefix> <issue> [<ref>=HEAD]     (Task 1: +optional 3rd arg)
        │ reused by (new 3rd arg = refine branch tip)
        ▼
scripts/transfer_refine_artifacts.sh <issue-number>          (Task 2: new)
  1. git fetch origin (best-effort)
  2. resolve most-recently-committed refs/remotes/origin/refine/issue-<N>-* (or no-op)
  3. push_gate_check.sh against that ref for docs/superpowers/{specs,plans}/
  4. skip a candidate file whose basename already exists under docs/archive/ on
     origin/main (resurrection guard — see Architect Review Cycle 1 below)
  5. git checkout <ref> -- <file>; git add <file>; count it staged only if
     `git diff --cached --quiet -- <file>` reports an actual change
  6. commit "docs(#N): copy spec/plan onto the implementation branch" only if the
     staged count is > 0
  7. always prints a SPEC_TRANSFER: ... line and exits 0
        │ invoked only when NEW_BRANCH=true
        ▼
workflows/archon-dark-factory.yaml :: setup-branch                (Task 3: modified)
  intent=new            -> checkout -b (always fresh)      -> NEW_BRANCH=true -> transfer
  intent=continue, fresh -> checkout -b fallback (no remote) -> NEW_BRANCH=true -> transfer
  intent=continue, reuse -> checkout existing remote branch -> NEW_BRANCH=false -> no transfer
  setup-branch-resolve  -> untouched (checks out an existing branch, never forks)

.archon/memory/codebase-patterns.md                            (Task 4: corrected)
  issue #42 [PATTERN] entry: "implement phase must copy manually" -> "setup-branch copies
  automatically via transfer_refine_artifacts.sh (#387); no manual copy needed"
```

## Tech Stack

Bash (`set -uo pipefail`, no `set -e`, matching `push_gate_check.sh`/`oos_excise.sh`
convention) for both scripts; `pytest` + `subprocess` git fixtures (matching
`tests/test_push_gate_check.py`/`tests/test_oos_excise.py`) for script tests; `pyyaml`-backed
static-content assertions (matching `tests/test_push_gate_dag.py`) for the DAG node change. No
new dependencies.

## File Structure

| File | Change |
|---|---|
| `docs/superpowers/specs/2026-09-05-refine-artifact-transfer-design.md` | **Copied** (Task 0) — this ticket's own spec, refine-branch-only |
| `docs/superpowers/plans/2026-09-05-refine-artifact-transfer-plan.md` | **Copied** (Task 0) — this plan file, same reason |
| `scripts/push_gate_check.sh` | **Modified** — optional 3rd `<ref>` argument (default `HEAD`), threaded through all 4 use sites |
| `tests/test_push_gate_check.py` | **Modified** — 4 new tests for the ref argument |
| `scripts/transfer_refine_artifacts.sh` | **New** — locates and copies the refine-branch spec/plan onto the current branch |
| `tests/test_transfer_refine_artifacts.py` | **New** — script tests in isolation |
| `workflows/archon-dark-factory.yaml` | **Modified** — `setup-branch` node: `NEW_BRANCH` flag + transfer-script call on both fresh-fork paths, timeout 15000→30000 |
| `tests/test_push_gate_dag.py` | **Modified** — new test class asserting fresh-fork-only invocation, `setup-branch-resolve` untouched |
| `.archon/memory/codebase-patterns.md` | **Modified** — issue #42 `[PATTERN]` entry corrected to describe the automatic transfer |

Not touched: `commands/dark-factory-plan.md` (Requirement 1 — no producer-side Task 0
mandate), `commands/dark-factory-conformance.md`'s `NO_SPEC=true` fallback logic,
`setup-branch-resolve`, `push-and-pr`'s archive step, `scripts/oos_excise.sh`,
`scripts/check_workflow_dag.py`'s `REQUIRED_OR_JOIN_NODES` (no new DAG edges introduced).

## Out of Scope

Per the spec's Alternatives Considered and Open Questions — explicitly not addressed by this
plan:
- No producer-side Task 0 mandate added to `commands/dark-factory-plan.md`.
- No absence-gated transfer (would resurrect archived specs on `continue` — rejected in the spec).
- No hard-fail on missing refine branch (legitimate no-refine cases exist; `NO_SPEC=true` is
  the designed fallback).
- No backfill for the five already-affected tickets (#381, #384, #382, #358, #354).
- No consolidation of the four `push_gate_check.sh` call sites onto a shared association
  helper (spec's first Open Question — a follow-up candidate, not this ticket's job).
- **`push-and-pr`'s unconditional `git mv "$SPEC_FILE" docs/archive/` is not changed by this
  plan** (spec Section 4, "Downstream: no changes needed"). That node has never distinguished a
  living/durable spec from a completed-workflow one — it already had this gap before this
  ticket (the PR #215 CI break the memory `[PATTERN]` entry documents happened via a *manual*
  copy, not an automatic one). Automatic transfer raises how often a spec reaches that code
  (every fresh fork, not only when an agent complied with the manual-copy pattern), but fixing
  `push-and-pr`'s archive step to recognize a living spec is a separate, pre-existing bug
  outside this ticket's `setup-branch`-only scope per the spec — a follow-up ticket candidate,
  not addressed here. Task 2's resurrection guard (added after Architect Review Cycle 1, below)
  closes the one sub-case that *is* this ticket's responsibility: never re-introducing a file at
  its pre-archive path when `docs/archive/<basename>` already exists on `origin/main`.

## Architect Review Cycle 1 (resolved)

The Phase 3 architect flagged two script-correctness issues, now folded into Task 2 above:
1. **STAGED accounting**: the original draft incremented `STAGED` whenever `push_gate_check.sh`
   found a candidate file, without checking whether `git checkout`+`git add` actually produced a
   diff — a refine-branch file byte-identical to what the fork already inherited from `main`
   would print a false `SPEC_TRANSFER: N file(s)` line with no real commit. Fixed by gating the
   count (and the commit) on `git diff --cached --quiet -- <file>`.
2. **Archive resurrection**: fresh-fork gating (spec R4) prevents resurrection on a `continue`
   dispatch against the *same* branch, but not a brand new fork created after the previous feat
   branch was merged/deleted while its `refine/issue-N-*` branch still exists on origin — that
   redispatch would re-add a file at `docs/superpowers/{specs,plans}/` that `main` already
   carries under `docs/archive/`, and the next `push-and-pr` `git mv` would hit an existing
   destination. Fixed by skipping any candidate file whose basename already exists under
   `docs/archive/` on `origin/main`.
Documentation/narration-only defects (wrong expected-output counts) were also fixed in place in
Tasks 1, 3, and 4 below; they are not called out separately.

## Architect Review Cycle 2 (resolved)

Re-review (against the cycle-1-revised plan, executed empirically rather than read-only) found
one substantive defect that survived cycle 1, plus a still-wrong narration count:
1. **Pass-1 content match read the working tree, not the ref (Task 1)**: `push_gate_check.sh`'s
   pass-1 association is `grep -Eq "#${ISSUE_NUM}\b" -- "$_file"`, which opens the file directly
   off disk — correct only when `REF=HEAD`. For a not-checked-out refine branch (the entire
   reason this ticket adds the `<ref>` argument), the file is absent from the worktree, so pass 1
   always misses and transfer would have silently depended on the refine commit subject
   happening to carry `#N` (pass 2) — undermining spec Requirement 2, which names the content
   match as the *primary* mechanism. This was not one of the spec's four listed `HEAD` sites; it
   is a fifth site the spec's implementation sketch missed. Fixed in Task 1 Step 1.3 by replacing
   the disk read with `git show "${REF}:$_file" 2>/dev/null | grep -Eq "#${ISSUE_NUM}\b"`, and
   pinned by a new Task 2 test
   (`test_copies_via_content_association_when_commit_subject_has_no_issue_number`) that transfers
   a spec whose commit subject carries no issue number at all.
2. **Task 3 Step 3.2 count, again**: still miscounted after the cycle-1 fix —
   `test_branch_reuse_path_does_not_set_new_branch_true` already passes against today's
   `setup-branch` (vacuously: `NEW_BRANCH` doesn't exist yet, so it's trivially absent from the
   reuse branch), making the correct split 4 fail/error, 3 pass, not 5/2. Corrected in place.

## Architect Review Cycle 3 (resolved — final cycle, human review requested)

Re-review confirmed both cycle-2 fixes empirically (31/31 Task 1+2 tests green including the two
that pin the pass-1 ref fix; 4-fail/3-pass reproduced exactly for Task 3's pre-change state, then
26/26 green including `test_dag_validator_passes` post-change; full suite 2046 passed;
`check_workflow_dag.py`/`smoke_gate.sh` both exit 0) but found one more substantive defect in the
cycle-2 fix itself:

3. **`git show ... | grep -Eq ...` under `set -uo pipefail` silently misses large files**: `grep
   -q` exits on its first match and closes the pipe; if `git show` is still writing (any match
   within roughly the first 80KB on this repo's real artifacts, since `**Issue:** #N` sits on
   line 3 of every spec/plan), `git show` dies with `SIGPIPE` (exit 141) and `pipefail` makes the
   whole pipeline report 141 — the `if` evaluates false even though `grep` matched. Verified
   deterministically against a real 91.5KB plan already in this repo (5/5 MISS with the bare
   pipe, 6/6 hit once fixed) and a flapping zone around 82-86KB. Because the fix reads the same
   pass-1 code path used by the default `REF=HEAD` case, this was not just a gap in the new
   transfer feature — it was a **regression on the existing `refine-push`/`plan-push-and-advance`
   push gates** for any large spec/plan whose commit subject doesn't carry `#N`, which CLAUDE.md
   prohibits ("never weaken a safety gate as a side effect of another change"). Fixed by wrapping
   the `git show` call: `{ git show "${REF}:$_file" 2>/dev/null || true; } | grep -Eq
   "#${ISSUE_NUM}\b"` — the `|| true` absorbs the SIGPIPE exit so only `grep`'s own status
   reaches the `if`. Pinned by a new large-artifact regression test in Task 1
   (`test_large_artifact_past_pipe_buffer_still_detected`).

This plan has now gone through the maximum 3 architect review cycles (`conformance.
max_reconcile_cycles`-equivalent cap for Phase 3). Per `commands/dark-factory-plan.md` Phase 3
step 4, it is posted to the issue with the full review dialogue and the `needs-discussion` label
rather than auto-proceeding to Phase 3.5/4 — even though this cycle's single finding was fixed
in place with a verified, minimal patch (not a design-level disagreement), a fourth automated
architect pass is outside this phase's cycle budget. A human approving this plan as-is (or
re-running the plan command to get a fresh cycle-1-3 sequence starting from this already-fixed
baseline) is the intended next step.

---

## Task 0: Copy this ticket's spec and plan onto the implementation branch

**Files:** `docs/superpowers/specs/2026-09-05-refine-artifact-transfer-design.md`,
`docs/superpowers/plans/2026-09-05-refine-artifact-transfer-plan.md`

This is the one-off bootstrap case the spec's Open Questions section flags as out of scope for
the automated fix: `feat/issue-387-*` forks from today's `main`, which does not yet contain the
`transfer_refine_artifacts.sh` script this very ticket adds — so #387's own implementation run
cannot benefit from its own fix and needs the same manual copy that #381/#384/#382/#358/#354
each required. Copy both files onto the feat branch and commit them before starting Task 1.

### Steps

1. Copy the two files from the refine branch (name derivation mirrors
   `workflows/archon-dark-factory.yaml`'s `setup-refine-branch` step):

```bash
ISSUE=387
SLUG=$(jq -r '.title // "feature"' "$ARTIFACTS_DIR/issue.json" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | head -c 40)
REFINE_BRANCH="refine/issue-${ISSUE}-${SLUG}"
git fetch origin "$REFINE_BRANCH"
git checkout "origin/$REFINE_BRANCH" -- \
  docs/superpowers/specs/2026-09-05-refine-artifact-transfer-design.md \
  docs/superpowers/plans/2026-09-05-refine-artifact-transfer-plan.md
```

   If the computed `REFINE_BRANCH` doesn't exist on origin (slug drift), fall back to:

```bash
git fetch origin
git checkout "origin/$(git branch -r | grep -oE 'origin/refine/issue-387-[a-z0-9-]+' | head -1 | sed 's#origin/##')" -- \
  docs/superpowers/specs/2026-09-05-refine-artifact-transfer-design.md \
  docs/superpowers/plans/2026-09-05-refine-artifact-transfer-plan.md
```

2. Verify both files landed, then commit:

```bash
test -f docs/superpowers/specs/2026-09-05-refine-artifact-transfer-design.md && \
test -f docs/superpowers/plans/2026-09-05-refine-artifact-transfer-plan.md && echo OK
git add docs/superpowers/specs/2026-09-05-refine-artifact-transfer-design.md \
  docs/superpowers/plans/2026-09-05-refine-artifact-transfer-plan.md
git commit -m "docs(#387): copy spec/plan onto the implementation branch"
```

---

## Task 1: `scripts/push_gate_check.sh` — optional third `<ref>` argument

**Files:** `tests/test_push_gate_check.py`, `scripts/push_gate_check.sh`

### Step 1.1 — Write failing tests

Add to `tests/test_push_gate_check.py`, inside `class TestPushGateCheckScript` (after
`test_commit_subject_match_on_deleted_file_not_reported`). First widen the shared `run_script`
helper to accept an optional ref (keeps every existing 2-arg call site byte-for-byte unaffected):

```python
def run_script(prefix: str, issue: str, cwd: Path, ref: str | None = None) -> subprocess.CompletedProcess:
    args = ["bash", str(SCRIPT), prefix, issue]
    if ref is not None:
        args.append(ref)
    return subprocess.run(args, capture_output=True, text=True, cwd=str(cwd))
```

Then add the new tests:

```python
    def test_explicit_ref_checks_that_ref_not_head(self, git_repo):
        """A third <ref> argument must be diffed/searched instead of HEAD — the
        artifact can live on a ref that is not currently checked out."""
        other_branch = "refine/issue-212-other"
        git("branch", other_branch, cwd=str(git_repo))
        git("checkout", other_branch, cwd=str(git_repo))
        spec_dir = git_repo / "docs" / "superpowers" / "specs"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "2026-07-15-example-design.md"
        spec_file.write_text("# Design\n\n**Issue:** #212\n")
        git("add", "docs/superpowers/specs/2026-07-15-example-design.md", cwd=str(git_repo))
        git("commit", "-m", "spec", cwd=str(git_repo))
        git("checkout", "refine/issue-212-test", cwd=str(git_repo))

        # HEAD (refine/issue-212-test) has no commits beyond main -> empty without a ref.
        result_head = run_script("docs/superpowers/specs/", "212", git_repo)
        assert result_head.stdout.strip() == ""

        result_ref = run_script("docs/superpowers/specs/", "212", git_repo, ref=other_branch)
        assert result_ref.returncode == 0, result_ref.stderr
        assert result_ref.stdout.strip() == "docs/superpowers/specs/2026-07-15-example-design.md"

    def test_omitted_ref_arg_still_defaults_to_head(self, git_repo):
        """Backward compatibility: a 2-arg call (no ref) must behave exactly as before."""
        spec_dir = git_repo / "docs" / "superpowers" / "specs"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "2026-07-15-example-design.md"
        spec_file.write_text("# Design\n\n**Issue:** #212\n")
        git("add", "docs/superpowers/specs/2026-07-15-example-design.md", cwd=str(git_repo))
        git("commit", "-m", "spec", cwd=str(git_repo))

        result = run_script("docs/superpowers/specs/", "212", git_repo)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "docs/superpowers/specs/2026-07-15-example-design.md"

    def test_explicit_head_ref_matches_omitted_ref(self, git_repo):
        """Passing 'HEAD' explicitly as the 3rd arg must be equivalent to omitting it."""
        spec_dir = git_repo / "docs" / "superpowers" / "specs"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "2026-07-15-example-design.md"
        spec_file.write_text("# Design\n\n**Issue:** #212\n")
        git("add", "docs/superpowers/specs/2026-07-15-example-design.md", cwd=str(git_repo))
        git("commit", "-m", "spec", cwd=str(git_repo))

        result = run_script("docs/superpowers/specs/", "212", git_repo, ref="HEAD")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "docs/superpowers/specs/2026-07-15-example-design.md"

    def test_nonexistent_ref_returns_empty_and_exits_zero(self, git_repo):
        """A ref that doesn't exist on origin/locally must yield empty output, exit 0 —
        never a script error (the fail-closed contract extends to a bad ref)."""
        result = run_script("docs/superpowers/specs/", "212", git_repo, ref="origin/refine/issue-999999-nope")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""

    def test_large_artifact_past_pipe_buffer_still_detected(self, git_repo):
        """Regression test for Architect Review Cycle 3: pass 1's content match must
        not silently miss a file larger than a pipe buffer. `**Issue:** #212` is
        placed on line 3 (matching every real spec/plan in this repo, where the
        `**Issue:**` line is always near the top) followed by ~120KB of padding, so
        `grep -q`'s early match races the pipe against `git show` still writing —
        exposing the set -uo pipefail + SIGPIPE trap if the `|| true` guard around
        `git show` is missing. Runs multiple times: the failure mode is
        size/timing-dependent, not 100% deterministic on every miss."""
        spec_dir = git_repo / "docs" / "superpowers" / "specs"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "2026-07-15-large-design.md"
        padding = ("y" * 100 + "\n") * 1200  # ~120KB
        spec_file.write_text(f"# Design\n\n**Issue:** #212\n{padding}")
        git("add", "docs/superpowers/specs/2026-07-15-large-design.md", cwd=str(git_repo))
        git("commit", "-m", "spec", cwd=str(git_repo))

        for _ in range(5):
            result = run_script("docs/superpowers/specs/", "212", git_repo)
            assert result.returncode == 0, result.stderr
            assert result.stdout.strip() == "docs/superpowers/specs/2026-07-15-large-design.md"
```

### Step 1.2 — Verify fail

```bash
python -m pytest tests/test_push_gate_check.py -v -k "explicit_ref or omitted_ref or explicit_head_ref or nonexistent_ref or large_artifact"
```
Expected: `test_omitted_ref_arg_still_defaults_to_head` and `test_explicit_head_ref_matches_omitted_ref`
already pass (they don't exercise new behavior — the 3rd arg is silently ignored today, so an
omitted or explicit-`HEAD` ref behaves identically before and after Step 1.3). `test_explicit_ref_checks_that_ref_not_head`
fails (the script has no 3rd-arg handling, `run_script`'s extra arg is silently ignored, so it
diffs `HEAD` instead of `other_branch` and finds nothing). `test_nonexistent_ref_returns_empty_and_exits_zero`
also already passes today (the ignored 3rd arg means it diffs `HEAD`, which has no commits beyond
`origin/main` in the fixture, so it's empty regardless) — this is a non-regression safety net, not
a red-then-green test; it must keep passing after Step 1.3 too (a bad ref still yields `HAS_COMMITS=0`
via the `2>/dev/null || echo 0` fallback, still empty, still exit 0). `test_large_artifact_past_pipe_buffer_still_detected`
is a third non-regression safety net in the same vein — it already passes today (the current
script reads `$_file` directly off disk with no pipe involved, so there is nothing for `SIGPIPE`
to kill) and must still pass once Step 1.3 introduces the `git show | grep` pipe (Architect
Review Cycle 3 found that a *naive* pipe implementation reintroduces this exact miss under
`set -uo pipefail`; the `{ ... || true; }` wrapper in Step 1.3's code block is what keeps it
green — see that step for why). Confirm all three keep passing in Step 1.4.

### Step 1.3 — Implement

Edit `scripts/push_gate_check.sh`. First, update the header comment (the `# Usage:` block,
currently lines 8-19) to document the new argument:

```bash
# Usage: push_gate_check.sh <artifact-prefix> <issue-number> [<ref>]
#   <artifact-prefix>  path prefix to search, e.g. "docs/superpowers/specs/"
#   <issue-number>     issue number to match via "#<issue-number>" in file content, or
#                       "<issue-number>" delimited by non-digits in the filename (e.g.
#                       "...issue-212-...md") — a correctly committed artifact that only
#                       names the issue in its filename must still be detected. As a
#                       second pass (#382), an artifact whose only issue-number
#                       reference is a commit subject on this branch is also detected,
#                       but only when that specific commit touched the reported file
#                       under the artifact prefix — never a global "any commit
#                       mentions the number" fallback (see #212 in the pass-2 code
#                       comment below for why).
#   <ref>               (optional, default HEAD) git ref to inspect instead of the
#                       currently checked-out HEAD — e.g. a not-checked-out remote
#                       refine branch (#387). Every existing 2-arg caller is
#                       byte-for-byte unaffected.
```

Update the two arg-guard error strings to mention the new optional argument:

```bash
ARTIFACT_PREFIX="${1:?Usage: push_gate_check.sh <artifact-prefix> <issue-number> [<ref>]}"
ISSUE_NUM="${2:?Usage: push_gate_check.sh <artifact-prefix> <issue-number> [<ref>]}"
```

Then add `REF` right after the existing arg guards (after the `ISSUE_NUM` block, before
`HAS_COMMITS=...`):

```bash
REF="${3:-HEAD}"
```

Then substitute `REF` (quoted, matching the script's existing defensive-quoting style) for the
four hard-coded `HEAD` occurrences:

```bash
HAS_COMMITS=$(git rev-list --count "origin/main..${REF}" 2>/dev/null || echo 0)
```

```bash
  done < <(git diff -z --name-only "origin/main...${REF}" -- "$ARTIFACT_PREFIX" 2>/dev/null)
```

```bash
  done < <(git log --format=%H "origin/main..${REF}" 2>/dev/null)
```

```bash
        if git cat-file -e "${REF}:$_touched" 2>/dev/null; then
```

**A fifth site, not in the spec's four-site list, must also change.** Pass 1's content match
currently reads the *working tree* directly, not the ref:

```bash
    if [[ "$_base" =~ (^|[^0-9])${ISSUE_NUM}([^0-9]|$) ]] \
      || grep -Eq "#${ISSUE_NUM}\\b" -- "$_file" 2>/dev/null; then
```

`grep -- "$_file"` opens the file off disk as currently checked out — correct only when
`REF=HEAD`. For a not-checked-out `REF` (a refine branch — the entire reason this argument
exists), the file is absent from the worktree (or, worse, present with unrelated content from
whatever branch actually is checked out), so pass 1 always misses and association silently
falls through to pass 2's commit-subject heuristic. Since spec Requirement 2 names the content
`#N` match as the *primary* mechanism, a refine commit subject that doesn't happen to carry
`#N` (e.g. a plain `docs: add design spec`) would transfer nothing — reproducing the exact
`NO_SPEC=true` failure this ticket exists to eliminate. Replace the disk read with a ref-scoped
git-blob read:

```bash
    if [[ "$_base" =~ (^|[^0-9])${ISSUE_NUM}([^0-9]|$) ]] \
      || { git show "${REF}:$_file" 2>/dev/null || true; } | grep -Eq "#${ISSUE_NUM}\\b"; then
```

**The `{ ... || true; }` wrapper is required, not optional style** (Architect Review Cycle 3):
under this script's `set -uo pipefail`, a bare `git show ... | grep -Eq ...` lets `pipefail`
propagate `git show`'s exit status when `grep -q` finds its match early and closes the pipe —
`git show` is then killed by `SIGPIPE` (exit 141), and `pipefail` makes the *whole pipeline*
report 141 even though `grep` actually matched, flipping the `if` to false. This is
deterministic on any match that occurs within the first pipe-buffer's worth of a large file and
non-deterministic near that boundary — reproduced empirically on this repo's own real artifacts
(`**Issue:** #N` sits on line 3 of every spec/plan, so any file past roughly 80KB triggers it).
This repo already has 15 spec/plan files over 58KB, 4 of them over 90KB. Left unguarded, this
would not just under-serve the new transfer path — it silently regresses the *existing*
`REF=HEAD` callers (`refine-push`, `plan-push-and-advance`), flipping a real "artifact found"
into a spurious miss and blocking the push gate for any large spec/plan whose commit subject
happens not to carry `#N`. The `|| true` inside the braces absorbs `git show`'s SIGPIPE exit so
only `grep`'s own exit status determines the `if`, exactly as intended.

For the default `REF=HEAD` path this reads the committed blob instead of the working-tree copy
— strictly more consistent with the script's "committed artifact" contract than before (an
uncommitted local edit to an otherwise-matching file can no longer flip the pass-1 verdict), and
it changes no existing test's outcome (verified: all pre-existing `test_push_gate_check.py`
tests, including `test_uncommitted_artifact_file_not_detected`, are unaffected — that test's
file is never committed in the first place, so it never reaches the three-dot candidate list
regardless of which read pass 1 uses).

No other logic changes — the exit-0-always contract, the numeric-only `ISSUE_NUM` guard, and the
`origin/main` (not local `main`) convention are untouched.

### Step 1.4 — Verify pass

```bash
python -m pytest tests/test_push_gate_check.py -v
```
Expected: all tests pass (existing 2-arg tests unaffected; the 4 new tests pass, including
`test_explicit_ref_checks_that_ref_not_head` — which requires the pass-1 git-blob-read fix
above; it stays red if only the four spec-listed sites are patched).

### Step 1.5 — Commit

```bash
git add scripts/push_gate_check.sh tests/test_push_gate_check.py
git commit -m "feat(#387): push_gate_check.sh accepts an optional ref argument"
```

---

## Task 2: New script `scripts/transfer_refine_artifacts.sh`

**Files:** `tests/test_transfer_refine_artifacts.py`, `scripts/transfer_refine_artifacts.sh`

### Step 2.1 — Write failing tests

Create `tests/test_transfer_refine_artifacts.py`:

```python
"""Tests for scripts/transfer_refine_artifacts.sh — copies a ticket's refine-branch
spec/plan onto the freshly forked feat branch (#387)."""
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "transfer_refine_artifacts.sh"


def run_script(issue: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), issue],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def git(*args, cwd, **kwargs):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, **kwargs)


@pytest.fixture()
def git_repo(tmp_path):
    """Bare-origin + working-tree fixture, same shape as test_push_gate_check.py's,
    except the working tree starts on a fresh feat branch with no refine branch yet."""
    bare = tmp_path / "bare"
    work = tmp_path / "work"
    bare.mkdir()
    git("init", "--bare", str(bare), cwd=str(tmp_path))
    subprocess.run(
        ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
        cwd=str(bare), capture_output=True,
    )
    git("clone", str(bare), str(work), cwd=str(tmp_path))
    git("config", "user.email", "test@test.com", cwd=str(work))
    git("config", "user.name", "Test", cwd=str(work))
    (work / "README.md").write_text("root\n")
    git("add", "README.md", cwd=str(work))
    git("commit", "-m", "init", cwd=str(work))
    git("push", "origin", "HEAD:main", cwd=str(work))
    git("branch", "--set-upstream-to=origin/main", "main", cwd=str(work))
    git("checkout", "-b", "feat/issue-212-test", cwd=str(work))
    return work


def _push_refine_branch(work, tmp_path, issue, slug, spec=True, plan=True, committer_offset=None):
    """Clones the same bare origin into a scratch dir, commits a refine branch with an
    optional spec/plan, and pushes it. Returns the branch name."""
    origin_url = git("remote", "get-url", "origin", cwd=str(work)).stdout.strip()
    scratch = tmp_path / f"refine_push_{issue}_{slug}"
    git("clone", origin_url, str(scratch), cwd=str(tmp_path))
    git("config", "user.email", "refine@test.com", cwd=str(scratch))
    git("config", "user.name", "Refine", cwd=str(scratch))
    branch = f"refine/issue-{issue}-{slug}"
    git("checkout", "-b", branch, cwd=str(scratch))
    env = None
    if committer_offset is not None:
        import os
        env = os.environ.copy()
        env["GIT_COMMITTER_DATE"] = committer_offset
        env["GIT_AUTHOR_DATE"] = committer_offset
    if spec:
        d = scratch / "docs" / "superpowers" / "specs"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"2026-09-05-{slug}-design.md").write_text(f"# Design\n\n**Issue:** #{issue}\n")
        git("add", ".", cwd=str(scratch))
        git("commit", "-m", f"docs(#{issue}): spec", cwd=str(scratch), env=env)
    if plan:
        d = scratch / "docs" / "superpowers" / "plans"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"2026-09-05-{slug}-plan.md").write_text(f"# Plan\n\n**Issue:** #{issue}\n")
        git("add", ".", cwd=str(scratch))
        git("commit", "-m", f"docs(#{issue}): plan", cwd=str(scratch), env=env)
    git("push", "origin", f"HEAD:{branch}", cwd=str(scratch))
    return branch


class TestTransferRefineArtifactsScript:
    def test_script_exists(self):
        assert SCRIPT.exists(), f"Script not found: {SCRIPT}"

    def test_script_syntax_valid(self):
        result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        assert result.returncode == 0, f"Syntax error: {result.stderr}"

    def test_bad_issue_arg_is_noop_exit_zero(self, tmp_path):
        result = subprocess.run(
            ["bash", str(SCRIPT), "not-a-number"], capture_output=True, text=True, cwd=str(tmp_path)
        )
        assert result.returncode == 0, result.stderr

    def test_no_refine_branch_prints_none_and_exits_zero(self, git_repo):
        result = run_script("212", git_repo)
        assert result.returncode == 0, result.stderr
        assert "SPEC_TRANSFER: none" in result.stdout
        assert "no refine/issue-212-* branch" in result.stdout

    def test_copies_both_spec_and_plan_when_both_exist(self, git_repo, tmp_path):
        _push_refine_branch(git_repo, tmp_path, "212", "test")
        result = run_script("212", git_repo)
        assert result.returncode == 0, result.stderr
        assert "SPEC_TRANSFER: 2 file(s)" in result.stdout
        spec = git_repo / "docs" / "superpowers" / "specs" / "2026-09-05-test-design.md"
        plan = git_repo / "docs" / "superpowers" / "plans" / "2026-09-05-test-plan.md"
        assert spec.exists() and plan.exists()
        status = git("status", "--porcelain", cwd=str(git_repo)).stdout
        assert status.strip() == "", f"working tree not clean after commit: {status}"
        log = git("log", "--oneline", "-1", cwd=str(git_repo)).stdout
        assert "docs(#212): copy spec/plan onto the implementation branch" in log

    def test_copies_via_content_association_when_commit_subject_has_no_issue_number(self, git_repo, tmp_path):
        """Spec Requirement 2's *primary* association mechanism is the file's own
        '#N' content match (push_gate_check.sh pass 1), with commit-subject
        association (pass 2) only as a fallback. This must work even when the
        refine-branch commit subject carries no issue number at all — the case
        that exposed the pass-1 working-tree-vs-ref bug in Architect Review Cycle 2."""
        origin_url = git("remote", "get-url", "origin", cwd=str(git_repo)).stdout.strip()
        scratch = tmp_path / "refine_content_only_212"
        git("clone", origin_url, str(scratch), cwd=str(tmp_path))
        git("config", "user.email", "refine@test.com", cwd=str(scratch))
        git("config", "user.name", "Refine", cwd=str(scratch))
        git("checkout", "-b", "refine/issue-212-test", cwd=str(scratch))
        d = scratch / "docs" / "superpowers" / "specs"
        d.mkdir(parents=True, exist_ok=True)
        (d / "2026-09-05-test-design.md").write_text("# Design\n\n**Issue:** #212\n")
        git("add", ".", cwd=str(scratch))
        git("commit", "-m", "docs: add design spec", cwd=str(scratch))  # no #212 in subject
        git("push", "origin", "HEAD:refine/issue-212-test", cwd=str(scratch))

        result = run_script("212", git_repo)
        assert result.returncode == 0, result.stderr
        assert "SPEC_TRANSFER: 1 file(s)" in result.stdout
        assert (git_repo / "docs" / "superpowers" / "specs" / "2026-09-05-test-design.md").exists()

    def test_copies_only_spec_when_plan_missing(self, git_repo, tmp_path):
        _push_refine_branch(git_repo, tmp_path, "212", "test", spec=True, plan=False)
        result = run_script("212", git_repo)
        assert result.returncode == 0, result.stderr
        assert "SPEC_TRANSFER: 1 file(s)" in result.stdout
        assert (git_repo / "docs" / "superpowers" / "specs" / "2026-09-05-test-design.md").exists()
        assert not (git_repo / "docs" / "superpowers" / "plans" / "2026-09-05-test-plan.md").exists()

    def test_copies_only_plan_when_spec_missing(self, git_repo, tmp_path):
        _push_refine_branch(git_repo, tmp_path, "212", "test", spec=False, plan=True)
        result = run_script("212", git_repo)
        assert result.returncode == 0, result.stderr
        assert "SPEC_TRANSFER: 1 file(s)" in result.stdout
        assert not (git_repo / "docs" / "superpowers" / "specs" / "2026-09-05-test-design.md").exists()
        assert (git_repo / "docs" / "superpowers" / "plans" / "2026-09-05-test-plan.md").exists()

    def test_refine_branch_with_neither_file_is_noop(self, git_repo, tmp_path):
        _push_refine_branch(git_repo, tmp_path, "212", "test", spec=False, plan=False)
        result = run_script("212", git_repo)
        assert result.returncode == 0, result.stderr
        assert "SPEC_TRANSFER: none" in result.stdout
        assert "no matching spec/plan found" in result.stdout

    def test_multiple_refine_branches_picks_most_recent(self, git_repo, tmp_path):
        _push_refine_branch(
            git_repo, tmp_path, "212", "older",
            committer_offset="2026-08-01T00:00:00",
        )
        _push_refine_branch(
            git_repo, tmp_path, "212", "newer",
            committer_offset="2026-09-01T00:00:00",
        )
        result = run_script("212", git_repo)
        assert result.returncode == 0, result.stderr
        assert "SPEC_TRANSFER: 2 file(s) from origin/refine/issue-212-newer" in result.stdout
        assert (git_repo / "docs" / "superpowers" / "specs" / "2026-09-05-newer-design.md").exists()
        assert not (git_repo / "docs" / "superpowers" / "specs" / "2026-09-05-older-design.md").exists()

    def test_missing_issue_arg_is_noop_exit_zero(self, tmp_path):
        result = subprocess.run(
            ["bash", str(SCRIPT)], capture_output=True, text=True, cwd=str(tmp_path)
        )
        assert result.returncode == 0, result.stderr

    def test_file_identical_to_branch_is_not_falsely_reported_as_staged(self, git_repo, tmp_path):
        """Architect Review Cycle 1: if the refine-branch file is byte-identical to what
        the fresh fork already inherited from main (e.g. re-run after a partial prior
        transfer), checkout+add produces no real diff — the script must not claim a
        commit happened. No new commit, and the reported count must be 0/none."""
        spec_dir = git_repo / "docs" / "superpowers" / "specs"
        spec_dir.mkdir(parents=True)
        (spec_dir / "2026-09-05-test-design.md").write_text("# Design\n\n**Issue:** #212\n")
        git("add", "docs/superpowers/specs/2026-09-05-test-design.md", cwd=str(git_repo))
        git("commit", "-m", "docs(#212): spec already present on this branch", cwd=str(git_repo))
        before = git("rev-list", "--count", "HEAD", cwd=str(git_repo)).stdout.strip()

        _push_refine_branch(git_repo, tmp_path, "212", "test", spec=True, plan=False)
        # Overwrite the refine branch's copy with byte-identical content so the diff is empty.
        origin_url = git("remote", "get-url", "origin", cwd=str(git_repo)).stdout.strip()
        scratch = tmp_path / "refine_identical_212_test"
        git("clone", origin_url, str(scratch), cwd=str(tmp_path))
        git("config", "user.email", "refine@test.com", cwd=str(scratch))
        git("config", "user.name", "Refine", cwd=str(scratch))
        git("checkout", "refine/issue-212-test", cwd=str(scratch))
        (scratch / "docs" / "superpowers" / "specs" / "2026-09-05-test-design.md").write_text(
            "# Design\n\n**Issue:** #212\n"
        )
        git("add", ".", cwd=str(scratch))
        git("commit", "--allow-empty", "-m", "docs(#212): identical content", cwd=str(scratch))
        git("push", "origin", "HEAD:refine/issue-212-test", cwd=str(scratch))

        result = run_script("212", git_repo)
        assert result.returncode == 0, result.stderr
        assert "SPEC_TRANSFER: none" in result.stdout
        after = git("rev-list", "--count", "HEAD", cwd=str(git_repo)).stdout.strip()
        assert after == before, "no commit should be made when nothing actually changed"

    def test_skips_file_already_archived_on_main(self, git_repo, tmp_path):
        """Architect Review Cycle 1: a redispatch that forks a brand new feat branch
        after the previous one was merged (spec already archived under docs/archive/
        on main) must not resurrect the pre-archive path — that would collide with
        push-and-pr's next git mv attempt."""
        archived = git_repo / "docs" / "archive" / "2026-09-05-test-design.md"
        archived.parent.mkdir(parents=True, exist_ok=True)
        archived.write_text("# Design (archived)\n\n**Issue:** #212\n")
        git("add", "docs/archive/2026-09-05-test-design.md", cwd=str(git_repo))
        git("commit", "-m", "docs: archive spec/plan for issue #212", cwd=str(git_repo))
        git("push", "origin", "HEAD:main", cwd=str(git_repo))

        _push_refine_branch(git_repo, tmp_path, "212", "test", spec=True, plan=False)

        result = run_script("212", git_repo)
        assert result.returncode == 0, result.stderr
        assert "SPEC_TRANSFER: none" in result.stdout
        assert not (git_repo / "docs" / "superpowers" / "specs" / "2026-09-05-test-design.md").exists()
```

### Step 2.2 — Verify fail

```bash
python -m pytest tests/test_transfer_refine_artifacts.py -v
```
Expected: `test_script_exists` and every other test fail (`ENOENT` / script not found) — the
script doesn't exist yet.

### Step 2.3 — Implement

Create `scripts/transfer_refine_artifacts.sh`:

```bash
#!/usr/bin/env bash
# Copy this ticket's refine-branch spec/plan onto the current (freshly forked) branch.
# Run from setup-branch (workflows/archon-dark-factory.yaml) on its two genuine
# fresh-fork paths only — never on branch reuse, never on setup-branch-resolve (#387).
#
# Usage: transfer_refine_artifacts.sh <issue-number>
#
# Non-fatal by design: every path prints a SPEC_TRANSFER: ... line to stdout and exits
# 0, matching push_gate_check.sh/oos_excise.sh's fail-open contract. A miss here is not
# an error — conformance's existing NO_SPEC=true advisory fallback is the safety net.
set -uo pipefail

ISSUE="${1:-}"

case "$ISSUE" in
  ''|*[!0-9]*)
    echo "transfer_refine_artifacts: usage: transfer_refine_artifacts.sh <issue-number>" >&2
    exit 0
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

git fetch origin >/dev/null 2>&1 || true

# Most-recently-committed refine branch first (#387 R6: a title re-slug between refine
# and plan dispatches can leave more than one refine/issue-N-* branch on origin).
_refine_branches=()
while IFS= read -r _ref; do
  [ -n "$_ref" ] && _refine_branches+=("$_ref")
done < <(git for-each-ref --sort=-committerdate --format='%(refname:short)' "refs/remotes/origin/refine/issue-${ISSUE}-*" 2>/dev/null)

if [ "${#_refine_branches[@]}" -eq 0 ]; then
  echo "SPEC_TRANSFER: none (no refine/issue-${ISSUE}-* branch on origin)"
  exit 0
fi

REFINE_REF="${_refine_branches[0]}"
if [ "${#_refine_branches[@]}" -gt 1 ]; then
  echo "transfer_refine_artifacts: ${#_refine_branches[@]} refine/issue-${ISSUE}-* branches found, selecting most recent: $REFINE_REF" >&2
fi

STAGED=0
for PREFIX in docs/superpowers/specs/ docs/superpowers/plans/; do
  FILE=$(bash "$SCRIPT_DIR/push_gate_check.sh" "$PREFIX" "$ISSUE" "$REFINE_REF")
  if [ -z "$FILE" ]; then
    continue
  fi

  # Resurrection guard (Architect Review Cycle 1): a brand new fork created after the
  # previous feat branch was merged/deleted, while its refine/issue-N-* branch still
  # exists on origin, must not re-add a file at its pre-archive path — push-and-pr's
  # next `git mv "$FILE" docs/archive/` would then collide with an existing destination.
  _archived_path="docs/archive/$(basename -- "$FILE")"
  if git cat-file -e "origin/main:${_archived_path}" 2>/dev/null; then
    echo "transfer_refine_artifacts: skipping $FILE — already archived on origin/main as ${_archived_path}" >&2
    continue
  fi

  git checkout "$REFINE_REF" -- "$FILE"
  git add "$FILE"
  # Only count it as staged if checkout+add actually produced a real change — a file
  # byte-identical to what this branch already inherited from main stages nothing, and
  # the SPEC_TRANSFER: line (Requirement 5's greppable signal) must not claim a commit
  # that didn't happen (Architect Review Cycle 1).
  if ! git diff --cached --quiet -- "$FILE"; then
    STAGED=$((STAGED + 1))
  fi
done

if [ "$STAGED" -gt 0 ]; then
  git commit -m "docs(#${ISSUE}): copy spec/plan onto the implementation branch" >/dev/null
  echo "SPEC_TRANSFER: ${STAGED} file(s) from ${REFINE_REF}"
else
  echo "SPEC_TRANSFER: none (no matching spec/plan found on ${REFINE_REF} for #${ISSUE})"
fi

exit 0
```

Make it executable:

```bash
chmod +x scripts/transfer_refine_artifacts.sh
```

### Step 2.4 — Verify pass

```bash
python -m pytest tests/test_transfer_refine_artifacts.py -v
```
Expected: all tests pass.

### Step 2.5 — Commit

```bash
git add scripts/transfer_refine_artifacts.sh tests/test_transfer_refine_artifacts.py
git commit -m "feat(#387): add transfer_refine_artifacts.sh"
```

---

## Task 3: Wire the transfer script into `setup-branch`'s fresh-fork paths

**Files:** `tests/test_push_gate_dag.py`, `workflows/archon-dark-factory.yaml`

### Step 3.1 — Write failing DAG test

Add to `tests/test_push_gate_dag.py` (after the existing `TestArtifactLookupNodes` class):

```python
class TestSetupBranchTransfersRefineArtifacts:
    """#387: setup-branch must call transfer_refine_artifacts.sh on its two genuine
    fresh-fork paths (intent=new; intent=continue's no-remote-branch fallback), and
    must NOT call it on branch reuse or on setup-branch-resolve."""

    def test_calls_transfer_script(self):
        bash = _workflow_nodes()["setup-branch"]["bash"]
        assert "transfer_refine_artifacts.sh" in bash

    def test_transfer_call_is_inside_new_branch_guard(self):
        bash = _workflow_nodes()["setup-branch"]["bash"]
        lines = bash.split("\n")
        guard_idx = next(i for i, l in enumerate(lines) if 'NEW_BRANCH" = "true"' in l)
        fi_idx = next(i for i in range(guard_idx, len(lines)) if lines[i].strip() == "fi")
        guarded_block = "\n".join(lines[guard_idx:fi_idx])
        assert "transfer_refine_artifacts.sh" in guarded_block, (
            "transfer_refine_artifacts.sh must run only inside the NEW_BRANCH guard"
        )
        assert "|| true" in guarded_block, (
            "the transfer call must be || true-guarded (defense-in-depth on top of "
            "the script's own unconditional exit 0)"
        )

    def test_both_checkout_b_sites_set_new_branch_true(self):
        bash = _workflow_nodes()["setup-branch"]["bash"]
        assert bash.count('git checkout -b "$BRANCH"') == 2, (
            "setup-branch must retain both checkout -b sites (new-intent path, "
            "continue's no-remote-branch fallback)"
        )
        assert bash.count("NEW_BRANCH=true") == 2, (
            "both checkout -b sites must set NEW_BRANCH=true"
        )

    def test_branch_reuse_path_does_not_set_new_branch_true(self):
        bash = _workflow_nodes()["setup-branch"]["bash"]
        reuse_idx = bash.index('git fetch origin "$BRANCH" 2>/dev/null && git checkout "$BRANCH"')
        else_idx = bash.index("else", reuse_idx)
        reuse_branch = bash[reuse_idx:else_idx]
        assert "NEW_BRANCH=true" not in reuse_branch

    def test_setup_branch_resolve_untouched(self):
        bash = _workflow_nodes()["setup-branch-resolve"]["bash"]
        assert "transfer_refine_artifacts.sh" not in bash
        assert "NEW_BRANCH" not in bash

    def test_setup_branch_depends_on_and_when_unchanged(self):
        node = _workflow_nodes()["setup-branch"]
        assert node["depends_on"] == ["parse-intent", "fetch-issue"]
        assert node["when"] == "$parse-intent.output.intent == 'new' || $parse-intent.output.intent == 'continue'"

    def test_setup_branch_timeout_raised_for_network_call(self):
        # #387: transfer_refine_artifacts.sh adds a git fetch + two push_gate_check.sh
        # passes over the refine branch's full commit history; 15s was already tight
        # for a fresh checkout -b, raised to 30s to give the added network round trip
        # headroom (same rationale as #358's push-and-pr label-failure timeout raise).
        node = _workflow_nodes()["setup-branch"]
        assert node["timeout"] == 30000
```

### Step 3.2 — Verify fail

```bash
python -m pytest tests/test_push_gate_dag.py -v -k TestSetupBranchTransfersRefineArtifacts
```
Expected: 4 of the 7 new tests fail or error against today's `setup-branch` (no `NEW_BRANCH`
flag, no `transfer_refine_artifacts.sh` call, `timeout: 15000`):
- `test_calls_transfer_script` fails (`AssertionError` — no such string in the bash yet).
- `test_transfer_call_is_inside_new_branch_guard` errors (`StopIteration` — `next(...)` finds no
  line containing `NEW_BRANCH" = "true"` yet).
- `test_both_checkout_b_sites_set_new_branch_true` fails on its *second* assert only — the first
  (`bash.count('git checkout -b "$BRANCH"') == 2`) already passes today; `NEW_BRANCH=true` occurs
  0 times, not 2.
- `test_setup_branch_timeout_raised_for_network_call` fails (`15000 != 30000`).

The remaining 3 already pass today and must keep passing unchanged:
- `test_branch_reuse_path_does_not_set_new_branch_true` — today's `continue`-reuse branch (line
  306: `git fetch origin "$BRANCH" 2>/dev/null && git checkout "$BRANCH" || git checkout -b
  "$BRANCH"`) already contains no `NEW_BRANCH=true` between that line and the next `else`, since
  `NEW_BRANCH` doesn't exist at all yet — the assertion holds vacuously today and holds for real
  once Step 3.3 lands.
- `test_setup_branch_resolve_untouched` (that node is never touched by this plan).
- `test_setup_branch_depends_on_and_when_unchanged` (this plan only edits the node's `bash:` body
  and `timeout:`, never its `depends_on`/`when`).

### Step 3.3 — Implement

Edit `workflows/archon-dark-factory.yaml`'s `setup-branch` node (currently lines 297-313):

```yaml
  - id: setup-branch
    bash: |
      ISSUE=$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")
      INTENT=$parse-intent.output.intent
      SLUG=$(jq -r '.title // "feature"' "$ARTIFACTS_DIR/issue.json" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | head -c 40)
      BRANCH="feat/issue-${ISSUE}-${SLUG}"
      echo "Setting up branch for issue #${ISSUE}..."

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
    depends_on: [parse-intent, fetch-issue]
    when: "$parse-intent.output.intent == 'new' || $parse-intent.output.intent == 'continue'"
    timeout: 30000
```

`setup-branch-resolve` (lines 334-349) is untouched — it checks out an existing branch and
never forks from `main`, so no transfer is needed there.

### Step 3.4 — Verify pass

```bash
python -m pytest tests/test_push_gate_dag.py -v
```
Expected: all tests pass, including `test_dag_validator_passes` (no OR-join/edge changes —
`setup-branch` is not in `check_workflow_dag.py`'s `REQUIRED_OR_JOIN_NODES`).

### Step 3.5 — Commit

```bash
git add workflows/archon-dark-factory.yaml tests/test_push_gate_dag.py
git commit -m "feat(#387): setup-branch transfers refine-branch spec/plan on fresh fork"
```

---

## Task 4: Correct the stale `[PATTERN]` memory entry (issue #42)

**Files:** `.archon/memory/codebase-patterns.md`

No test covers this file's content (verified: only structural/routing tests reference
`codebase-patterns.md` by name, not by content — `tests/test_memory_write.py`,
`tests/test_memory_retrieve.py`, `tests/test_conformance_memory_write.sh`). Direct edit only,
per Requirement 8.

### Steps

1. Replace the current `[PATTERN]` line (the live entry, NOT the `[INVALID: ...]` line directly
   above it — that historical record stays untouched per the spec-approval note):

   Current (line 6 of the file):
   ```
   - [PATTERN] When a refine-phase spec/plan was approved on a sibling `refine/issue-N-...` branch, the implement phase must itself copy `docs/superpowers/specs/*.md` and `docs/superpowers/plans/*.md` onto the `feat/issue-N-...` branch and commit them — they do not transfer automatically. A later archive step must rename only the *plan* into `docs/archive/`; the design spec stays at its durable `docs/superpowers/specs/` path if it is the ticket's living policy/reference deliverable (not just a completed workflow artifact) — archiving both broke CI on PR #215 (test + README both pin the spec path). <!-- issue:#42 date:2026-07-10 expires:2027-01-10 source:implement -->
   ```

   New:
   ```
   - [PATTERN] When a refine-phase spec/plan was approved on a sibling `refine/issue-N-...` branch, `setup-branch` (`workflows/archon-dark-factory.yaml`) automatically copies `docs/superpowers/specs/*.md` and `docs/superpowers/plans/*.md` onto the freshly forked `feat/issue-N-...` branch via `scripts/transfer_refine_artifacts.sh` (#387) — manual copying is no longer necessary. A later archive step must rename only the *plan* into `docs/archive/`; the design spec stays at its durable `docs/superpowers/specs/` path if it is the ticket's living policy/reference deliverable (not just a completed workflow artifact) — archiving both broke CI on PR #215 (test + README both pin the spec path). <!-- issue:#42 date:2026-07-10 expires:2027-01-10 source:implement -->
   ```

2. Verify the edit and that nothing else in the file changed unexpectedly:

```bash
# "must itself copy" still appears once, on the untouched [INVALID: ...] historical
# line (line 5) — only the live [PATTERN] line (line 6) is edited, so a bare
# whole-file grep for the old phrase must NOT drop to 0; anchor to the [PATTERN] line.
grep -c "must itself copy" .archon/memory/codebase-patterns.md   # expect: 1 (the [INVALID] line only)
grep -c '^- \[PATTERN\].*must itself copy' .archon/memory/codebase-patterns.md   # expect: 0
grep -c "automatically copies" .archon/memory/codebase-patterns.md   # expect: 1
git diff --stat .archon/memory/codebase-patterns.md   # expect: 1 file, 1(+) 1(-)
python -m pytest tests/test_memory_write.py tests/test_memory_retrieve.py -v
```

3. Commit:

```bash
git add .archon/memory/codebase-patterns.md
git commit -m "docs(#387): correct issue #42 memory entry to describe automatic transfer"
```

---

## Task 5: Full suite + smoke gate

### Step 5.1 — Run

```bash
python -m pytest tests/ -v
bash smoke_gate.sh
python3 scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
```

Expected: full green; `check_workflow_dag.py` prints nothing and exits 0.

### Step 5.2 — Commit (only if a fixup was needed)

If Step 5.1 is fully green with no further code changes needed, skip — nothing to commit.
