# Push-gate detection of committed artifacts lacking an issue number in filename/content

**Issue:** #382
**Status:** plan-pending-review

## Goal

Stop `push_gate_check.sh` from reporting a genuinely committed+pushed spec/plan as missing
just because the issue number lives only in the commit subject (the exact #381 failure that
caused an infinite refine re-dispatch loop). Add a deterministic second detection pass to the
script (per-commit association only — never a global fallback, to avoid the #212-class
mis-association failure), and mandate a `**Issue:** #<num>` line in refine/plan output so the
three other content-only DAG call sites (budget telemetry, PR-push archive step) keep working
regardless of how the push gate associates the artifact.

## Architecture

Two independent, additive changes, both already fully designed in the approved spec
(`docs/superpowers/specs/2026-09-04-push-gate-commit-subject-detection-design.md`):

1. **`scripts/push_gate_check.sh`** gains a second sequential pass, reached only when the
   existing filename/content pass (unchanged) finds nothing. Pass 2 collects commits unique to
   this branch (`git log --format=%H origin/main..HEAD`), matches `#<num>` against each
   commit's **subject only**, and — for matching commits — intersects that commit's own
   `git diff-tree` paths under the artifact prefix against the existing three-dot candidate
   list. The printed path is always drawn from that same candidate list, so the fail-closed
   invariant holds structurally: a script that finds nothing today still finds nothing.
2. **`commands/dark-factory-refine.md`** (Phase 5) and **`commands/dark-factory-plan.md`**
   (Phase 2) gain a conventions bullet + self-review check requiring a `**Issue:** #<num>`
   line in the artifact body, so `workflows/archon-dark-factory.yaml`'s other three
   content-only `grep -rl "#${ISSUE}"` call sites (budget telemetry at `:393`/`:908`, and the
   PR-push archive step at `:1007`-`:1008`) keep working even for an artifact that
   self-identifies only via commit subject.

## Tech Stack

- Bash (`push_gate_check.sh`), pytest + `subprocess` against a real bare-origin/work-tree git
  fixture (`tests/test_push_gate_check.py`, mirrors `test_oos_excise.py`'s fixture shape)
- Markdown phase-command files (no runtime, validated by plain-text assertion tests)

## File Structure

| File | Change |
|---|---|
| `scripts/push_gate_check.sh` | Add pass 2 (commit-subject, per-commit association) |
| `tests/test_push_gate_check.py` | Add 2 regression tests pinning #382 + the discriminator |
| `commands/dark-factory-refine.md` | Phase 5: mandate `**Issue:** #<num>` line + self-review check |
| `commands/dark-factory-plan.md` | Phase 2: mandate `**Issue:** #<num>` line |
| `tests/test_command_issue_number_mandate.py` | New: pin the mandate text in both command files |

## Task 0: Copy this ticket's spec and plan onto the implementation branch

**Files:** `docs/superpowers/specs/2026-09-04-push-gate-commit-subject-detection-design.md`,
`docs/superpowers/plans/2026-09-04-push-gate-commit-subject-detection.md`

Per the `[PATTERN]` memory lesson (issue #42) and the Task 0 both #381's and #384's plans
needed: the implement phase's `feat/issue-382-...` branch forks from `main`, so this ticket's
own spec and this plan file (both refine-branch-only, not on `main`) do **not** transfer
automatically. Without them, Gate 2 (conformance) falls back to `NO_SPEC=true` advisory-only
review. Copy both files onto the feat branch and commit them before starting Task 1.

### Steps

1. Copy the two files from the refine branch (name derivation mirrors
   `workflows/archon-dark-factory.yaml`'s `setup-refine-branch` step):

```bash
ISSUE=382
SLUG=$(jq -r '.title // "feature"' "$ARTIFACTS_DIR/issue.json" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | head -c 40)
REFINE_BRANCH="refine/issue-${ISSUE}-${SLUG}"
git fetch origin "$REFINE_BRANCH"
git checkout "origin/$REFINE_BRANCH" -- \
  docs/superpowers/specs/2026-09-04-push-gate-commit-subject-detection-design.md \
  docs/superpowers/plans/2026-09-04-push-gate-commit-subject-detection.md
```

   If the computed `REFINE_BRANCH` doesn't exist on origin (slug drift), fall back to:

```bash
git fetch origin
git checkout "origin/$(git branch -r | grep -oE 'origin/refine/issue-382-[a-z0-9-]+' | head -1 | sed 's#origin/##')" -- \
  docs/superpowers/specs/2026-09-04-push-gate-commit-subject-detection-design.md \
  docs/superpowers/plans/2026-09-04-push-gate-commit-subject-detection.md
```

2. Verify both files landed, then commit:

```bash
test -f docs/superpowers/specs/2026-09-04-push-gate-commit-subject-detection-design.md && \
test -f docs/superpowers/plans/2026-09-04-push-gate-commit-subject-detection.md && echo OK
git add docs/superpowers/specs/2026-09-04-push-gate-commit-subject-detection-design.md \
  docs/superpowers/plans/2026-09-04-push-gate-commit-subject-detection.md
git commit -m "docs(#382): copy spec/plan onto the implementation branch"
```

---

## Task 1: Add commit-subject detection pass to `push_gate_check.sh`

**Files:** `tests/test_push_gate_check.py`, `scripts/push_gate_check.sh`

### Step 1.1 — Write failing test: #382 reproducer (commit-subject-only detection)

Append to `tests/test_push_gate_check.py`, inside `class TestPushGateCheckScript:`, after
`test_uncommitted_artifact_file_not_detected`:

```python
    def test_commit_subject_only_reference_detected(self, git_repo):
        """Reproduces #382: a spec committed with a numberless filename and no issue
        number in its content, where the issue number appears only in the commit
        subject, must still be detected via the pass-2 commit-subject association."""
        spec_dir = git_repo / "docs" / "superpowers" / "specs"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "2026-08-31-handoff-example-design.md"
        spec_file.write_text("# Design\n\nNo issue reference in the body.\n")
        git("add", "docs/superpowers/specs/2026-08-31-handoff-example-design.md", cwd=str(git_repo))
        git("commit", "-m", "docs(spec): #212 handoff example design", cwd=str(git_repo))

        result = run_script("docs/superpowers/specs/", "212", git_repo)
        assert result.returncode == 0, result.stderr
        assert (
            result.stdout.strip()
            == "docs/superpowers/specs/2026-08-31-handoff-example-design.md"
        )

    def test_commit_subject_match_is_per_commit_not_global(self, git_repo):
        """Discriminator: a side commit that merely mentions the issue number in its
        subject, but does not itself touch any file under the artifact prefix, must
        NOT cause an unrelated numberless candidate file to be reported. A global
        'any commit on the branch mentions #<num>' fallback would mis-associate the
        two; per-commit association must keep this empty (the #212 failure class)."""
        spec_dir = git_repo / "docs" / "superpowers" / "specs"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "2026-08-31-unrelated-numberless-design.md"
        spec_file.write_text("# Design\n\nNo issue reference in the body.\n")
        git("add", "docs/superpowers/specs/2026-08-31-unrelated-numberless-design.md", cwd=str(git_repo))
        git("commit", "-m", "docs(spec): draft design notes", cwd=str(git_repo))

        (git_repo / "memory-note.txt").write_text("lessons from issue #212\n")
        git("add", "memory-note.txt", cwd=str(git_repo))
        git("commit", "-m", "memory: lessons from issue #212", cwd=str(git_repo))

        result = run_script("docs/superpowers/specs/", "212", git_repo)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""
```

### Step 1.2 — Verify both new tests fail

```bash
python -m pytest tests/test_push_gate_check.py -k "commit_subject" -v
```

Expected: `test_commit_subject_only_reference_detected` **FAILS** (stdout is empty, not the
spec path — the script has no pass 2 yet). `test_commit_subject_match_is_per_commit_not_global`
**PASSES already**, even before Step 1.3 — today's script only does pass 1, so it correctly
finds nothing for that scenario too. This is expected and correct: that test is a regression
pin against a *future* global-fallback implementation, not a new-behavior assertion, so it has
no red phase. Confirm it stays green (do not treat a passing test here as a problem to fix);
the only test that must go red-then-green across Steps 1.2/1.4 is
`test_commit_subject_only_reference_detected`.

### Step 1.3 — Implement pass 2 in `scripts/push_gate_check.sh`

Replace the file's `HAS_COMMITS` block (from `HAS_COMMITS=$(git rev-list ...)` through the
final `exit 0`) with:

```bash
HAS_COMMITS=$(git rev-list --count origin/main..HEAD 2>/dev/null || echo 0)
if [ "$HAS_COMMITS" -gt 0 ]; then
  # Capture the three-dot candidate list once so both passes walk the same ordered
  # set — this is the structural fail-closed invariant: nothing is ever printed that
  # isn't a member of this list.
  # NUL-delimited iteration (via `git diff -z` + `read -d ''`) so a committed path
  # containing whitespace is handled as a single filename, not split across args.
  _candidates=()
  while IFS= read -r -d '' _file; do
    _candidates+=("$_file")
  done < <(git diff -z --name-only origin/main...HEAD -- "$ARTIFACT_PREFIX" 2>/dev/null)

  # Pass 1 (unchanged): filename- or content-delimited issue number match.
  for _file in ${_candidates[@]+"${_candidates[@]}"}; do
    _base=$(basename -- "$_file")
    # ISSUE_NUM is validated numeric-only above, so it is safe to interpolate directly
    # into these regexes (no metacharacter/injection risk from a malformed value).
    if [[ "$_base" =~ (^|[^0-9])${ISSUE_NUM}([^0-9]|$) ]] \
      || grep -Eq "#${ISSUE_NUM}\\b" -- "$_file" 2>/dev/null; then
      printf '%s\n' "$_file"
      exit 0
    fi
  done

  # Pass 2 (#382): only reached when pass 1 found nothing. A commit-subject match
  # may associate a file only if that same commit touched it under the artifact
  # prefix — per-commit association, never a global "any commit on the branch
  # mentions #<num>" fallback, which would risk mis-associating an unrelated file
  # (e.g. a "memory: lessons from issue #N" side commit) — the #212 failure class
  # this check exists to prevent.
  declare -A _assoc=()
  while IFS= read -r _sha; do
    _subj=$(git show -s --format=%s "$_sha")
    if [[ "$_subj" =~ \#${ISSUE_NUM}([^0-9]|$) ]]; then
      while IFS= read -r -d '' _touched; do
        if git cat-file -e "HEAD:$_touched" 2>/dev/null; then
          _assoc["$_touched"]=1
          echo "push_gate_check: associated $_touched via commit subject $_sha" >&2
        fi
      done < <(git diff-tree --no-commit-id -r -z --name-only "$_sha" -- "$ARTIFACT_PREFIX" 2>/dev/null)
    fi
  done < <(git log --format=%H origin/main..HEAD 2>/dev/null)

  for _file in ${_candidates[@]+"${_candidates[@]}"}; do
    if [[ -n "${_assoc["$_file"]+x}" ]]; then
      printf '%s\n' "$_file"
      exit 0
    fi
  done
fi
exit 0
```

Also update the usage comment block at the top of the file (the `<issue-number>` bullet) to
document pass 2, by replacing:

```
#   <issue-number>     issue number to match via "#<issue-number>" in file content, or
#                       "<issue-number>" delimited by non-digits in the filename (e.g.
#                       "...issue-212-...md") — a correctly committed artifact that only
#                       names the issue in its filename must still be detected.
```

with:

```
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
```

Do not touch anything above `set -uo pipefail` or the numeric-guard `case` block — both are
out of scope per the spec's requirement 6.

### Step 1.4 — Verify all tests pass

```bash
bash -n scripts/push_gate_check.sh
python -m pytest tests/test_push_gate_check.py -v
```

Expected: `bash -n` prints nothing (exit 0); pytest shows **11 passed** (the original 9,
unmodified, plus the 2 added in Step 1.1) — the count matters because requirement 7(c) pins
"all nine existing tests... unmodified" and this step is what proves it.

### Step 1.5 — Commit

```bash
git add scripts/push_gate_check.sh tests/test_push_gate_check.py
git commit -m "fix(push-gate): detect artifacts identified only via commit subject (#382)"
```

## Task 2: Producer-side `**Issue:** #<num>` mandate in refine/plan commands

**Files:** `tests/test_command_issue_number_mandate.py` (new), `commands/dark-factory-refine.md`,
`commands/dark-factory-plan.md`

### Step 2.1 — Write failing test

Create `tests/test_command_issue_number_mandate.py`:

```python
from pathlib import Path

COMMAND_DIR = Path(__file__).resolve().parents[1] / "commands"


def test_refine_spec_writing_mandates_issue_number_line():
    text = (COMMAND_DIR / "dark-factory-refine.md").read_text(encoding="utf-8")
    assert "**Issue:** #<num>" in text, (
        "Phase 5 (Spec Writing) must mandate a '**Issue:** #<num>' line in the spec "
        "body so content-only detection (budget telemetry, PR-push archive step) "
        "keeps working for artifacts the push gate associates via commit subject (#382)"
    )
    assert "issue number line" in text.lower() or "issue-number line" in text.lower(), (
        "Phase 5's self-review step must explicitly check for the mandated line, "
        "the same way it already checks for placeholders/consistency/scope"
    )


def test_plan_writing_mandates_issue_number_line():
    text = (COMMAND_DIR / "dark-factory-plan.md").read_text(encoding="utf-8")
    assert "**Issue:** #<num>" in text, (
        "Phase 2 (Plan Writing) conventions must mandate a '**Issue:** #<num>' line "
        "in the plan body for the same reason as the refine command (#382)"
    )
```

### Step 2.2 — Verify the new test fails

```bash
python -m pytest tests/test_command_issue_number_mandate.py -v
```

Expected: both tests **FAIL** with `AssertionError` — neither command file contains
`**Issue:** #<num>` yet.

### Step 2.3 — Edit `commands/dark-factory-refine.md` Phase 5

In the `## Phase 5: SPEC WRITING` section, replace:

```
3. Write the spec to `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` following existing spec format:
   - Overview / problem statement
   - Requirements (from Q&A)
   - Architecture / approach
   - Alternatives considered
   - Open questions (non-blocking)
   - Assumptions (flagged)
4. Self-review: placeholder scan, consistency check, scope check, ambiguity check. Fix inline.
```

with:

```
3. Write the spec to `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` following existing spec format:
   - A `**Issue:** #<num>` line directly under the title — required (#382) so the
     content-only `grep -rl "#${ISSUE}"` call sites elsewhere in the DAG (budget
     telemetry, the PR-push archive step) can find this artifact even when the push
     gate itself associates it via commit subject instead
   - Overview / problem statement
   - Requirements (from Q&A)
   - Architecture / approach
   - Alternatives considered
   - Open questions (non-blocking)
   - Assumptions (flagged)
4. Self-review: placeholder scan, consistency check, scope check, ambiguity check,
   issue-number line present. Fix inline.
```

### Step 2.4 — Edit `commands/dark-factory-plan.md` Phase 2

In the `## Phase 2: PLAN WRITING` section, replace:

```
Write a full implementation plan following these conventions:
- Save to `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`
- Start with the standard plan header (Goal, Architecture, Tech Stack)
- Include a File Structure table
- Break into bite-sized tasks (each step is one 2-5 minute action)
- Every task has: Files list, TDD steps (write failing test → verify fail → implement → verify pass → commit)
- No placeholders — every step has actual code blocks and exact file paths
- Exact commands with expected output
```

with:

```
Write a full implementation plan following these conventions:
- Save to `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`
- A `**Issue:** #<num>` line directly under the title, before the standard plan
  header (Goal, Architecture, Tech Stack) — required (#382) so the content-only
  `grep -rl "#${ISSUE}"` call sites elsewhere in the DAG (budget telemetry, the
  PR-push archive step) can find this artifact even when the push gate itself
  associates it via commit subject instead
- Include a File Structure table
- Break into bite-sized tasks (each step is one 2-5 minute action)
- Every task has: Files list, TDD steps (write failing test → verify fail → implement → verify pass → commit)
- No placeholders — every step has actual code blocks and exact file paths
- Exact commands with expected output
- Self-review before publishing: confirm the issue-number line is present, alongside
  the existing no-placeholders check
```

### Step 2.5 — Verify the test passes

```bash
python -m pytest tests/test_command_issue_number_mandate.py -v
```

Expected: **2 passed**.

### Step 2.6 — Commit

```bash
git add commands/dark-factory-refine.md commands/dark-factory-plan.md tests/test_command_issue_number_mandate.py
git commit -m "docs(commands): mandate Issue: #<num> line in spec/plan output (#382)"
```

## Task 3: Full verification pass

**Files:** none (verification only)

### Step 3.1 — Run the full test suite

```bash
python -m pytest tests/ -v
```

Expected: all tests pass, including the new/modified ones from Tasks 1-2 and the untouched
`tests/test_push_gate_check.py` baseline (11 tests) and `tests/test_command_issue_number_mandate.py`
(2 tests).

### Step 3.2 — Run the smoke gate

```bash
bash smoke_gate.sh
```

Expected: exits 0 — this repo's CI runs exactly `python -m pytest tests/ -v` plus
`smoke_gate.sh` plus the workflow DAG checks (per `CLAUDE.md`), and none of this ticket's
changes touch `workflows/archon-dark-factory.yaml` or any DAG structure, so no DAG-check
regression is expected.

No commit in this task — it is a verification-only checkpoint confirming Tasks 1-2 didn't
regress anything else in the suite.
