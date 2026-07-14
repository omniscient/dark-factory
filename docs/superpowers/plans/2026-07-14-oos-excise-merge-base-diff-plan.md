# Plan: Fix `oos_excise.sh` to Diff Against the Merge Base, Not Raw `origin/main`

**Issue:** omniscient/dark-factory#266
**Spec:** `docs/superpowers/specs/2026-07-13-oos-excise-merge-base-diff-design.md`
**Status:** draft — pending review
**Direct-to-PR:** No (gate change — reviewed on its own per CLAUDE.md)

---

## Goal

`scripts/oos_excise.sh` currently detects "out of scope" files with a raw two-dot
`git diff --name-only origin/main HEAD`, which compares the *current tips* of
`origin/main` and the working branch. When `origin/main` moves forward while a branch is
in flight (another ticket merges), every file that merge touched shows up in that diff —
even files the working branch never committed a change to. The excise loop then either
noisily "restores" those files from `origin/main`, or — when a later merge to `main`
deleted a file the branch inherited unchanged at its fork point — permanently deletes it
from the working branch. This fired for real on 2026-07-13, deleting
`scripts/factory_core/providers/*` from the #251 branch.

The fix is a single-line change: switch the detection diff from two-dot
(`origin/main HEAD`) to three-dot (`origin/main...HEAD`) — git's built-in merge-base
diff, already used by every other gate command in this repo
(`dark-factory-validate.md`, `dark-factory-conformance.md`, `dark-factory-code-review.md`,
`dark-factory-implement.md`). The restore-vs-delete logic that follows detection is
correct as written and is not touched.

## Architecture

No architectural change — this is a one-line semantic fix inside an existing, isolated
gate script plus test coverage for the fork-point scenario it fixes.

**Note on `.archon/memory/codebase-patterns.md`'s two-dot `PATTERN` entry (from #250):**
that entry recommends two-dot `git diff origin/main HEAD -- <file>` for testing whether
one *already-identified* file's content is net-identical to `main`'s current tip (a
single-file content-equality check — "is this specific file's content the same as
main's, so a revert would be a no-op"). It is not about which *set* of files counts as
"changed by this branch," which is the question `oos_excise.sh`'s detection line answers.
Three-dot is the correct operator for the set-membership question and is what every
other gate in this repo already uses for it; this task does not touch or contradict the
#250 memory entry's actual (narrower) scope.

## Tech Stack

Bash (`scripts/oos_excise.sh`), Python/pytest (`tests/test_oos_excise.py`), git plumbing
(two-dot vs. three-dot diff, `git merge-base`).

## File Structure

| File | Change |
|---|---|
| `scripts/oos_excise.sh` | Line 22: `origin/main HEAD` → `origin/main...HEAD` |
| `tests/test_oos_excise.py` | Add 2 fork-point regression tests to `TestOosExciseScript`; delete `TestOosExciseBehaviorParity` entirely (2 test methods + 3 helper methods) |

---

## Task 1: Add fork-point regression tests (RED)

Add two new tests to `TestOosExciseScript` in `tests/test_oos_excise.py` that reproduce
the exact bug shapes from the issue's evidence. Both must fail against the current
(two-dot) script.

**Files:** `tests/test_oos_excise.py`

### Step 1.1 — Write the "independent addition" regression test

Add this test method inside `class TestOosExciseScript:`, immediately after
`test_stdout_contains_only_filenames` (before `test_missing_allowed_prefixes_arg_fails`):

```python
    def test_main_only_new_file_after_fork_not_flagged(self, git_repo, tmp_path):
        """A file added by an independent commit pushed to origin/main after the
        branch forked (simulating another ticket merging) must not be flagged as
        OOS, even though it now differs between origin/main's tip and HEAD."""
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()

        # Simulate another ticket merging to main after this branch forked: a
        # second clone of the same bare origin adds a file outside the allowed
        # prefix and pushes it directly to main.
        bare_url = git("remote", "get-url", "origin", cwd=str(git_repo)).stdout.strip()
        other = tmp_path / "other_clone_add"
        git("clone", bare_url, str(other), cwd=str(tmp_path))
        git("config", "user.email", "other@test.com", cwd=str(other))
        git("config", "user.name", "Other", cwd=str(other))
        other_file = other / "scripts" / "factory_core" / "providers" / "new_provider.py"
        other_file.parent.mkdir(parents=True, exist_ok=True)
        other_file.write_text("new provider\n")
        git("add", "scripts/factory_core/providers/new_provider.py", cwd=str(other))
        git("commit", "-m", "unrelated: add provider", cwd=str(other))
        git("push", "origin", "main", cwd=str(other))

        # The working branch fetches the moved origin/main ref (as the container
        # does) but makes no commit touching the new file itself.
        git("fetch", "origin", cwd=str(git_repo))

        # The branch's own in-scope commit, unrelated to the new file.
        (git_repo / "docs").mkdir(exist_ok=True)
        (git_repo / "docs" / "spec.md").write_text("spec\n")
        git("add", "docs/spec.md", cwd=str(git_repo))
        git("commit", "-m", "spec", cwd=str(git_repo))

        env = base_env(artifacts)
        result = run_script("docs/", "refine", env, git_repo)
        assert result.returncode == 0, result.stderr
        assert "scripts/factory_core/providers/new_provider.py" not in result.stdout
        assert not (
            git_repo / "scripts" / "factory_core" / "providers" / "new_provider.py"
        ).exists(), "File added only on origin/main should not be pulled into the branch"
```

### Step 1.2 — Write the "independent deletion" regression test

Add this test method directly after the one from Step 1.1:

```python
    def test_main_only_deletion_after_fork_not_excised_from_branch(self, git_repo, tmp_path):
        """A file inherited unchanged at the branch's fork point, later deleted by
        an independent origin/main-only commit, must not be deleted from the
        working branch. Regression test for the #251/#266 providers/* incident."""
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()

        # File exists at the fork point (committed and pushed before the branch
        # or main move any further).
        inherited = git_repo / "scripts" / "factory_core" / "providers" / "existing_provider.py"
        inherited.parent.mkdir(parents=True, exist_ok=True)
        inherited.write_text("existing provider\n")
        git("add", "scripts/factory_core/providers/existing_provider.py", cwd=str(git_repo))
        git("commit", "-m", "add existing provider", cwd=str(git_repo))
        git("push", "origin", "main", cwd=str(git_repo))

        # A separate, later-merging ticket removes the file from main.
        bare_url = git("remote", "get-url", "origin", cwd=str(git_repo)).stdout.strip()
        other = tmp_path / "other_clone_delete"
        git("clone", bare_url, str(other), cwd=str(tmp_path))
        git("config", "user.email", "other@test.com", cwd=str(other))
        git("config", "user.name", "Other", cwd=str(other))
        git("rm", "scripts/factory_core/providers/existing_provider.py", cwd=str(other))
        git("commit", "-m", "unrelated: remove provider", cwd=str(other))
        git("push", "origin", "main", cwd=str(other))

        git("fetch", "origin", cwd=str(git_repo))

        # The working branch's own commit, unrelated to that file, outside the
        # allowed prefix (so the gate has something to legitimately excise too).
        (git_repo / "docs").mkdir(exist_ok=True)
        (git_repo / "docs" / "spec.md").write_text("spec\n")
        git("add", "docs/spec.md", cwd=str(git_repo))
        git("commit", "-m", "spec", cwd=str(git_repo))

        env = base_env(artifacts)
        result = run_script("docs/", "plan", env, git_repo)
        assert result.returncode == 0, result.stderr
        assert "scripts/factory_core/providers/existing_provider.py" not in result.stdout
        assert inherited.exists(), "Inherited file was wrongly deleted from the working branch"
```

### Step 1.3 — Verify both new tests fail (RED) against the current script

```bash
cd /workspace/dark-factory
python -m pytest tests/test_oos_excise.py -k "test_main_only_new_file_after_fork_not_flagged or test_main_only_deletion_after_fork_not_excised_from_branch" -v
```

Expected: both tests **FAIL**.
- `test_main_only_new_file_after_fork_not_flagged` fails on the second assert: the file
  gets restored (created) in the working tree by branch (a) of the excise loop.
- `test_main_only_deletion_after_fork_not_excised_from_branch` fails on the second
  assert: `inherited.exists()` is `False` — the current two-dot script deletes it via
  branch (b) of the excise loop, reproducing the exact `providers/*` incident.

### Step 1.4 — Commit the failing tests

```bash
git add tests/test_oos_excise.py
git commit -m "test(oos-excise): add fork-point regression tests (#266)"
```

---

## Task 2: Fix the detection line (GREEN)

**Files:** `scripts/oos_excise.sh`

### Step 2.1 — Change line 22 from two-dot to three-dot

In `scripts/oos_excise.sh`, change:

```bash
OOS_FILES=$(git diff --name-only origin/main HEAD 2>/dev/null | while read -r f; do
```

to:

```bash
OOS_FILES=$(git diff --name-only origin/main...HEAD 2>/dev/null | while read -r f; do
```

This is the only line that changes. `git diff A...B` is git's built-in shorthand for
`git diff $(git merge-base A B) B` — no explicit merge-base computation is needed. The
restore-vs-delete logic (lines 34–43) stays exactly as written: it correctly resolves
against `origin/main`'s current tip (not the merge-base), which is the intended
"align the branch to main" behavior for files the branch's own commits actually touched.

### Step 2.2 — Verify the two new tests now pass (GREEN)

```bash
cd /workspace/dark-factory
python -m pytest tests/test_oos_excise.py -k "test_main_only_new_file_after_fork_not_flagged or test_main_only_deletion_after_fork_not_excised_from_branch" -v
```

Expected: both tests **PASS**.

### Step 2.3 — Verify the full existing test file still passes

```bash
cd /workspace/dark-factory
python -m pytest tests/test_oos_excise.py -v
```

Expected: all tests pass, including the pre-existing `TestOosExciseScript` tests
(`test_new_oos_file_is_excised`, `test_existing_oos_file_restored_from_origin`, etc.) —
none of their fixtures push additional commits to `origin/main` after the branch forks,
so `merge-base(origin/main, HEAD)` equals `origin/main`'s tip in every one of them and
three-dot agrees with two-dot. This confirms requirement 3 (files the branch's own
commits touch are still excised exactly as before) without needing new tests for it.

The `TestOosExciseBehaviorParity` tests are still present and will still pass here too
(their fixtures also never advance `main` post-fork) — they are removed in Task 3 for
being obsolete, not because they fail.

### Step 2.4 — Commit the fix

```bash
git add scripts/oos_excise.sh
git commit -m "fix(oos-excise): diff against merge base, not raw origin/main tip (#266)"
```

---

## Task 3: Remove the obsolete parity test class

**Files:** `tests/test_oos_excise.py`

### Step 3.1 — Delete `TestOosExciseBehaviorParity` entirely

Remove the whole `class TestOosExciseBehaviorParity:` block from
`tests/test_oos_excise.py` — both test methods (`test_parity_no_oos_files`,
`test_parity_with_oos_new_file`) and all three helper methods
(`_run_inline_oos_side_effects`, `_build_bare_origin`, `_clone`). This class asserted the
script's output is byte-identical to the old inline two-dot block it was extracted from;
now that the script's semantics are deliberately changed (two-dot → three-dot), "still
matches the buggy two-dot inline block" is no longer an invariant worth holding, and
keeping it would leave a tested, blessed copy of the buggy two-dot form in the tree.

The file should end after the `test_missing_commit_noun_arg_fails` method of
`TestOosExciseScript` (plus the two new Task 1 tests already added above it) — nothing
follows.

### Step 3.2 — Verify the full suite is still green with the class removed

```bash
cd /workspace/dark-factory
python -m pytest tests/test_oos_excise.py -v
```

Expected: all remaining tests pass (the two new fork-point tests plus the original
`TestOosExciseScript` tests); `TestOosExciseBehaviorParity` no longer appears in the
collected test list.

### Step 3.3 — Run the full project test suite

```bash
cd /workspace/dark-factory
python -m pytest tests/ -v
```

Expected: all tests pass (no other file references `TestOosExciseBehaviorParity` or its
helpers — this class is private to `tests/test_oos_excise.py`).

### Step 3.4 — Commit the removal

```bash
git add tests/test_oos_excise.py
git commit -m "test(oos-excise): remove obsolete two-dot parity test class (#266)"
```

---

## Verification Summary

| Requirement (from spec) | Verified by |
|---|---|
| 1. Detection considers only the branch's own commits | Task 1 Step 1.1 / Task 2 Step 2.2 |
| 2. Inherited-then-upstream-deleted files are never excised | Task 1 Step 1.2 / Task 2 Step 2.2 |
| 3. Branch's own out-of-scope changes still excised as before | Task 2 Step 2.3 (existing tests unchanged, still pass) |
| 4. Fork-point regression coverage added | Task 1 |
| 5. Scope stays `scripts/oos_excise.sh` + tests only | File Structure table above — no other files touched |
