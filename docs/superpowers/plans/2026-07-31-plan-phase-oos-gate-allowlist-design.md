# Implementation Plan: Fix Plan-Phase OOS Gate Deleting the Refine-Phase Spec

**Issue:** omniscient/dark-factory#293
**Spec:** `docs/superpowers/specs/2026-07-28-plan-phase-oos-gate-allowlist-design.md`

---

## Goal

`commands/dark-factory-plan.md`'s Phase 4 step 4 OOS gate call only allows
`docs/superpowers/plans/`, so on every plan run it excises (deletes) the
`docs/superpowers/specs/*.md` and `.archon/memory/*` files the refine phase
legitimately committed earlier on the same branch — 32 historical excise commits, all
from `plan` runs. Two changes, both scoped exactly per spec Requirement 5 (no diffing
semantics change, no touching conformance/code-review):

1. Widen the plan-phase allowlist to `"docs/superpowers/plans/ docs/superpowers/specs/ .archon/memory/"`,
   mirroring `dark-factory-refine.md`'s own already-correct call.
2. Make `scripts/oos_excise.sh` fall back to `$ARTIFACTS_DIR/issue.json`'s
   `resolved_number` when the caller's `$ISSUE_NUM` env var isn't set, fixing the
   `(#)` empty-issue-number bug in the excision commit message at both call sites.

## Architecture

```
commands/dark-factory-plan.md  Phase 4 step 4
  OOS_FILES=$(bash ".../oos_excise.sh" "docs/superpowers/plans/ docs/superpowers/specs/ .archon/memory/" plan)
                                                    │
                                                    ▼
scripts/oos_excise.sh <allowed-prefixes> <commit-noun>
  1. ALLOWED_PREFIXES, COMMIT_NOUN from $1/$2 (unchanged)
  2. ARTIFACTS_DIR required, now resolved BEFORE ISSUE_NUM (reordered)
  3. ISSUE_NUM="${ISSUE_NUM:-$(jq -r '.resolved_number // empty' "$ARTIFACTS_DIR/issue.json" ...)}"
     — caller's env var wins if set (bash ${:-} short-circuits, so the jq subshell
     never runs when ISSUE_NUM is already non-empty); falls back to the artifact
     every phase command already requires and treats as ground truth
  4. diff/excise/commit logic — UNCHANGED (origin/main...HEAD three-dot diff from #266
     stays exactly as-is per spec Requirement 5 / Alternatives Considered)
```

No change to `dark-factory-refine.md`'s call (already correct), `dark-factory-conformance.md`,
or `dark-factory-code-review.md` (neither calls `oos_excise.sh` — confirmed in spec).

## Tech Stack

- Bash for the `oos_excise.sh` edit — matches the script's existing language.
- Markdown edit for `commands/dark-factory-plan.md` — one-line allowlist string change.
- `pytest` for `tests/test_oos_excise.py` (existing suite, `subprocess`-driven,
  fixture-based git repos) and `tests/test_command_issue_context_contract.py`
  (existing suite, plain text-content assertions against `commands/*.md`) —
  both already run via `python -m pytest tests/ -v` in `.github/workflows/ci.yml:12`,
  so no CI wiring task is needed.

## File Structure

| File | Change |
|---|---|
| `commands/dark-factory-plan.md` | **Modified** — widen the `oos_excise.sh` allowlist argument on the Phase 4 step 4 line |
| `scripts/oos_excise.sh` | **Modified** — reorder `ARTIFACTS_DIR`/`ISSUE_NUM` assignment; add `issue.json` fallback for `ISSUE_NUM` |
| `tests/test_command_issue_context_contract.py` | **Modified** — new test asserting the plan command's `oos_excise.sh` line carries all three prefixes |
| `tests/test_oos_excise.py` | **Modified** — new test asserting the `issue.json` fallback populates the commit message when `ISSUE_NUM` is unset |

---

## Task 1: Widen the plan-phase OOS gate allowlist

**Files:** `commands/dark-factory-plan.md` (modified), `tests/test_command_issue_context_contract.py` (modified)

### TDD Steps

1. Write the failing test. Add this test function to
   `tests/test_command_issue_context_contract.py`, following the file's existing
   pattern (`COMMAND_DIR`-relative read, plain substring assertions against the raw
   command text):

```python
def test_plan_command_oos_gate_allowlist_includes_spec_and_memory_prefixes():
    """Regression test for #293: the plan-phase OOS gate must not treat the
    refine phase's own spec/memory commits (already on the branch when plan
    runs) as out-of-scope, or it silently deletes them."""
    text = (COMMAND_DIR / "dark-factory-plan.md").read_text(encoding="utf-8")
    oos_lines = [line for line in text.splitlines() if "oos_excise.sh" in line]
    assert oos_lines, "dark-factory-plan.md must call oos_excise.sh"
    line = oos_lines[0]
    for prefix in ("docs/superpowers/plans/", "docs/superpowers/specs/", ".archon/memory/"):
        assert prefix in line, (
            f"dark-factory-plan.md's oos_excise.sh invocation is missing allowed "
            f"prefix {prefix!r} — line: {line!r}"
        )
    for stray in ("\"docs/\"", "\"scripts/\""):
        assert stray not in line, (
            f"dark-factory-plan.md's oos_excise.sh invocation over-widened to a bare "
            f"prefix {stray!r} — line: {line!r}"
        )
```

2. Verify it fails (current allowlist is `"docs/superpowers/plans/"` only):

```bash
python -m pytest tests/test_command_issue_context_contract.py -v -k oos_gate_allowlist
# FAILED ... AssertionError: dark-factory-plan.md's oos_excise.sh invocation is
# missing allowed prefix 'docs/superpowers/specs/' ...
```

3. Edit `commands/dark-factory-plan.md` Phase 4 step 4 — widen the allowlist argument
   to match `dark-factory-refine.md`'s own call pattern, keeping the `# TARGET-PATH`
   marker and `plan` commit-noun unchanged, and reword the step's lead-in sentence to
   record that the two added prefixes are tolerated inherited artifacts, not new
   authorized plan output (the file's `SCOPE BOUNDARY` section, unchanged, still lists
   only `docs/superpowers/plans/` as this command's own output). Replace:

   > 4. Run the OOS gate — detect and revert any files committed outside the plan allowlist:

   with:

   > 4. Run the OOS gate — detect and revert any files committed outside the plan
   >    allowlist. `docs/superpowers/specs/` and `.archon/memory/` are included because
   >    the refine phase legitimately commits to those prefixes earlier on this same
   >    branch (#293); they remain outside this command's own `SCOPE BOUNDARY`, which is
   >    still only `docs/superpowers/plans/`:

   and update the code block itself:

```bash
OOS_FILES=$(bash "${REPO_ROOT}/dark-factory/scripts/oos_excise.sh" "docs/superpowers/plans/ docs/superpowers/specs/ .archon/memory/" plan)  # TARGET-PATH
```

4. Verify it passes:

```bash
python -m pytest tests/test_command_issue_context_contract.py -v -k oos_gate_allowlist
# PASSED
python -m pytest tests/test_command_issue_context_contract.py -v
# all tests pass
```

5. Commit:

```bash
git add commands/dark-factory-plan.md tests/test_command_issue_context_contract.py
git commit -m "fix(plan): widen OOS gate allowlist to tolerate refine-phase spec/memory commits (#293)"
```

---

## Task 2: Make `oos_excise.sh` self-sufficient for the issue number

**Files:** `scripts/oos_excise.sh` (modified), `tests/test_oos_excise.py` (modified)

### TDD Steps

1. Write the failing test. Add this test method to the `TestOosExciseScript` class in
   `tests/test_oos_excise.py`, next to `test_commit_message_contains_noun_and_issue`.
   Unlike `base_env()` (which always sets `ISSUE_NUM="670"`), this test deliberately
   omits `ISSUE_NUM` from the environment and instead writes a minimal `issue.json`
   into the artifacts dir, proving the fallback path:

```python
    def test_issue_num_falls_back_to_issue_json_when_env_unset(self, git_repo, tmp_path):
        """Regression test for #293: when the caller doesn't set $ISSUE_NUM (the
        common case, since entrypoint.sh's ISSUE_NUM isn't exported into the later
        Bash tool subprocess that runs this script), the excision commit message
        must still embed the real issue number instead of interpolating '(#)'."""
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        (artifacts / "issue.json").write_text('{"resolved_number": 293}\n')

        oos_file = git_repo / "backend" / "oops.py"
        oos_file.parent.mkdir(exist_ok=True)
        oos_file.write_text("oops\n")
        git("add", str(oos_file), cwd=str(git_repo))
        git("commit", "-m", "oos", cwd=str(git_repo))

        env = os.environ.copy()
        env["ARTIFACTS_DIR"] = str(artifacts)
        env.pop("ISSUE_NUM", None)

        result = run_script("docs/", "plan", env, git_repo)
        assert result.returncode == 0, result.stderr
        log = git("log", "--oneline", "-1", cwd=str(git_repo)).stdout.strip()
        # Match the parenthesized form, not a bare "293" substring — --oneline
        # prepends an abbreviated SHA that could coincidentally contain "293".
        assert "(#293)" in log, f"Issue number not in message (issue.json fallback failed): {log}"
```

2. Verify it fails (script has no `issue.json` fallback yet — commit message
   interpolates the unset `ISSUE_NUM` as empty):

```bash
python -m pytest tests/test_oos_excise.py -v -k issue_num_falls_back
# FAILED ... AssertionError: Issue number not in message (issue.json fallback failed): ... (#)
# (git log --oneline shows the commit message ends in the empty "(#)" form)
```

3. Edit `scripts/oos_excise.sh`: reorder `ARTIFACTS_DIR` before `ISSUE_NUM` (the
   fallback needs `$ARTIFACTS_DIR` already resolved) and add the `issue.json`
   fallback. Replace:

```bash
ALLOWED_PREFIXES="${1:?Usage: oos_excise.sh <allowed-prefixes> <commit-noun>}"
COMMIT_NOUN="${2:?Usage: oos_excise.sh <allowed-prefixes> <commit-noun>}"
ISSUE_NUM="${ISSUE_NUM:-}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:?ARTIFACTS_DIR must be set}"
```

   with:

```bash
ALLOWED_PREFIXES="${1:?Usage: oos_excise.sh <allowed-prefixes> <commit-noun>}"
COMMIT_NOUN="${2:?Usage: oos_excise.sh <allowed-prefixes> <commit-noun>}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:?ARTIFACTS_DIR must be set}"
# Falls back to the issue.json artifact every phase command already requires and
# treats as ground truth, since callers frequently fail to export ISSUE_NUM into
# the shell invocation that runs this script (#293).
ISSUE_NUM="${ISSUE_NUM:-$(jq -r '.resolved_number // empty' "$ARTIFACTS_DIR/issue.json" 2>/dev/null || true)}"
```

   Also update the script's header comment (currently `#   ISSUE_NUM     (optional)
   issue number embedded in commit message`) to document the fallback:

```bash
#   ISSUE_NUM     (optional) issue number embedded in commit message; falls back to
#                 $ARTIFACTS_DIR/issue.json's resolved_number when unset
```

4. Verify it passes:

```bash
python -m pytest tests/test_oos_excise.py -v -k issue_num_falls_back
# PASSED
python -m pytest tests/test_oos_excise.py -v
# all tests pass (existing test_commit_message_contains_noun_and_issue still passes:
# it sets ISSUE_NUM="670" via base_env(), which the ${ISSUE_NUM:-...} form preserves
# unchanged since the fallback only evaluates when ISSUE_NUM is unset/empty)
bash -n scripts/oos_excise.sh
# (no output = syntax OK)
```

5. Commit:

```bash
git add scripts/oos_excise.sh tests/test_oos_excise.py
git commit -m "fix(oos-excise): fall back to issue.json for ISSUE_NUM so excision commits stop saying (#) (#293)"
```

---

## Task 3: Full regression pass

**Files:** none (verification only)

### TDD Steps

1. Run the full suite plus the DAG/when validators to confirm nothing else broke:

```bash
python -m pytest tests/ -v
bash tests/test_budget_gate.sh
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
# all green
```

2. No commit for this task — it is a verification checkpoint, not a code change.

---

## Out of Scope (per spec Requirement 5)

- Changing `oos_excise.sh`'s `origin/main...HEAD` three-dot diffing semantics — that is
  `#266`'s deliberate fix for a different false-positive class and stays as-is.
- A per-phase-base-commit or cumulative-allowlist redesign — deferred to the open
  generic-fix ticket `#272`, which has no existing plumbing to build on.
- Any change to `commands/dark-factory-conformance.md` or `commands/dark-factory-code-review.md`
  — confirmed unaffected: neither calls `oos_excise.sh` (conformance uses a separate
  LLM-review scope gate with its own doc-file exemption; code-review has no OOS logic).
- `entrypoint.sh`'s `ISSUE_NUM` parsing/export — rejected in the spec's Alternatives
  Considered as widening the shell-environment surface for a value already available,
  more authoritatively, via `issue.json`.
