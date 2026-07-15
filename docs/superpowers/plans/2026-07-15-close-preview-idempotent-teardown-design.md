# Implementation Plan: Idempotent `close-preview` Teardown on Absent/Stale Preview Stack

**Issue:** omniscient/dark-factory#230
**Spec:** `docs/superpowers/specs/2026-07-15-close-preview-idempotent-teardown-design.md`

---

## Goal

Soften `close-preview`'s stale-container assertion (`workflows/archon-dark-factory.yaml`,
lines 210-217) from a hard `exit 1` to a `WARNING`-and-continue, mirroring `preview-up`'s
`preview_fail()` fail-soft philosophy. This is the only change: the "Tearing down..." /
`docker compose down -v` line above it and everything after it (PR discovery, the
`needs-discussion` guard, `mark-ready`, `merge`, `set-status done`) is untouched — those are
genuine close-intent hard-fails that must keep blocking on real failure.

## Architecture

Single-node bash edit inside the Archon DAG YAML, plus one new bash regression test that
locks in the fail-soft behavior. No new DAG nodes, no `when:` expression changes, no schema
changes — `scripts/check_workflow_dag.py` and `scripts/check_workflow_when.py` are unaffected
by construction (the node's `id`, `depends_on`, and `when:` are untouched; only bash inside the
`bash: |` block scalar changes).

Per `.archon/memory/dark-factory-ops.md` `[PROVISIONAL]` (issue #716): `bash: |` block scalars
require every content line indented at the block's level — the replacement block below
preserves the existing 6-space (8-space inside the `if`) indentation exactly, no zero-indent
continuation lines are introduced.

Test approach mirrors the existing `tests/test_preview_differentiator.sh` convention: a
self-contained bash harness with `assert_eq`/`assert_contains`/`assert_not_contains` helpers
exercising a maintained copy of the bash block under test (that sibling test does the same for
`preview-up`'s guard block). This repo's convention is copy-based regression tests for
DAG-node bash logic, not dynamic YAML extraction — followed here for consistency. Like its
sibling, this test is a manual/CI-parity regression check (`bash tests/test_*.sh`); it is not
added to `.github/workflows/ci.yml`'s explicit step list, matching `test_preview_differentiator.sh`'s
existing precedent (verified via `git log` — that file was never wired into `ci.yml` either).

## Tech Stack

Bash (Archon DAG node body, test harness). No Python, no new dependencies.

---

## File Structure

| File | Change |
|---|---|
| `tests/test_close_preview_teardown.sh` | New — regression test for the fail-soft teardown check |
| `workflows/archon-dark-factory.yaml` | Modified — `close-preview` node's stale-container assertion (lines 210-217) |

---

## Memory Context Applied

- `.archon/memory/dark-factory-ops.md` `[PROVISIONAL]` (issue #716, "YAML block scalars
  require ALL content lines indented at the block's level"): Task 2 step 1 preserves the
  existing 6/8-space indentation exactly and adds no zero-indent continuation lines — called
  out explicitly in the Architecture section above and re-verified in Task 2's steps.
- No `[AVOID]` entries apply to this change (checked `dark-factory-ops.md`,
  `codebase-patterns.md`, `architecture.md` — none target `workflows/archon-dark-factory.yaml`
  bash-node edits).

---

## Task 1: Add failing test for the fail-soft teardown check

**Files:** `tests/test_close_preview_teardown.sh` (new)

1. Write the test file with `run_teardown_check` implemented as a **copy of the current
   (buggy) block** from `workflows/archon-dark-factory.yaml` lines 210-217 — this makes the
   test fail against today's behavior, proving it actually detects the bug before the fix
   lands:

```bash
#!/usr/bin/env bash
# Regression test for close-preview's stale-container teardown check (issue #230).
# Mirrors tests/test_preview_differentiator.sh's convention: run_teardown_check is a
# maintained copy of workflows/archon-dark-factory.yaml's close-preview node (lines
# ~210-217) — keep both in sync on any future edit to that block.
# Run: bash tests/test_close_preview_teardown.sh
set -uo pipefail

PASSED=0; FAILED=0
assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else
    echo "  FAIL: $desc — expected='$expected' got='$actual'" >&2; FAILED=$((FAILED+1))
  fi
}

assert_contains() {
  local desc="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -qF "$needle"; then
    echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else
    echo "  FAIL: $desc — expected to find '$needle' in output" >&2; FAILED=$((FAILED+1))
  fi
}

assert_not_contains() {
  local desc="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -qF "$needle"; then
    echo "  FAIL: $desc — did not expect '$needle' in output" >&2; FAILED=$((FAILED+1))
  else
    echo "  PASS: $desc"; PASSED=$((PASSED+1))
  fi
}

# =====================================================================
# close-preview: stale-container teardown check
# =====================================================================
echo "--- close-preview: stale-container teardown check ---"

run_teardown_check() {
  local stale="$1"
  (
    STALE="$stale"
    if [ -n "$STALE" ]; then
      echo "ERROR: Stale preview containers remain after teardown:" >&2
      echo "$STALE" >&2
      exit 1
    fi
    echo "close-preview: teardown verified — no mh-preview-99 containers remain"
  )
}

# No stale containers → verified message, exit 0
OUT=$(run_teardown_check "" 2>&1); RC=$?
assert_eq "empty STALE → exit 0" "0" "$RC"
assert_contains "empty STALE → teardown verified message" "teardown verified" "$OUT"
assert_not_contains "empty STALE → no WARNING" "WARNING" "$OUT"

# Stale containers present → WARNING + continue (exit 0), not ERROR + exit 1
OUT=$(run_teardown_check "mh-preview-99-backend-1" 2>&1); RC=$?
assert_eq "non-empty STALE → exit 0 (fail-soft, not exit 1)" "0" "$RC"
assert_contains "non-empty STALE → WARNING message" "WARNING: Stale preview containers remain after teardown" "$OUT"
assert_contains "non-empty STALE → container name in output" "mh-preview-99-backend-1" "$OUT"
assert_not_contains "non-empty STALE → no ERROR" "ERROR" "$OUT"

# =====================================================================
# Summary
# =====================================================================
echo ""
echo "============================="
echo "Results: ${PASSED} passed, ${FAILED} failed"
echo "============================="
[ "$FAILED" -eq 0 ] && exit 0 || exit 1
```

2. Verify it fails (the embedded copy still has the old hard-fail behavior):

```bash
bash tests/test_close_preview_teardown.sh
```

Expected output:

```
--- close-preview: stale-container teardown check ---
  PASS: empty STALE → exit 0
  PASS: empty STALE → teardown verified message
  PASS: empty STALE → no WARNING
  FAIL: non-empty STALE → exit 0 (fail-soft, not exit 1) — expected='0' got='1'
  FAIL: non-empty STALE → WARNING message — expected to find 'WARNING: Stale preview containers remain after teardown' in output
  PASS: non-empty STALE → container name in output
  FAIL: non-empty STALE → no ERROR — did not expect 'ERROR' in output

=============================
Results: 4 passed, 3 failed
=============================
```

Exit code: `1`.

3. Implement — replace `run_teardown_check`'s body with the fixed, fail-soft logic (the same
   logic that Task 2 applies to `workflows/archon-dark-factory.yaml`):

```bash
run_teardown_check() {
  local stale="$1"
  (
    STALE="$stale"
    if [ -n "$STALE" ]; then
      echo "WARNING: Stale preview containers remain after teardown (continuing):" >&2
      echo "$STALE" >&2
    else
      echo "close-preview: teardown verified — no mh-preview-99 containers remain"
    fi
  )
}
```

4. Verify it passes:

```bash
bash tests/test_close_preview_teardown.sh
```

Expected output:

```
--- close-preview: stale-container teardown check ---
  PASS: empty STALE → exit 0
  PASS: empty STALE → teardown verified message
  PASS: empty STALE → no WARNING
  PASS: non-empty STALE → exit 0 (fail-soft, not exit 1)
  PASS: non-empty STALE → WARNING message
  PASS: non-empty STALE → container name in output
  PASS: non-empty STALE → no ERROR

=============================
Results: 7 passed, 0 failed
=============================
```

Exit code: `0`.

5. Commit:

```bash
chmod +x tests/test_close_preview_teardown.sh
git add tests/test_close_preview_teardown.sh
git commit -m "test(close-preview): add fail-soft teardown regression test (issue #230)"
```

---

## Task 2: Apply the fail-soft fix to `close-preview`

**Files:** `workflows/archon-dark-factory.yaml` (modified)

1. Replace the stale-container assertion in the `close-preview` node. Current block (lines
   210-217):

```yaml
      # Assert no containers survive teardown
      STALE=$(docker ps -a --filter "label=com.docker.compose.project=mh-preview-${ISSUE}" --format '{{.Names}}' 2>/dev/null || true)
      if [ -n "$STALE" ]; then
        echo "ERROR: Stale preview containers remain after teardown:" >&2
        echo "$STALE" >&2
        exit 1
      fi
      echo "close-preview: teardown verified — no mh-preview-${ISSUE} containers remain"
```

Replace with:

```yaml
      # Fail-soft teardown check — mirrors preview-up's preview_fail(): a broken/absent preview
      # must never block the close intent. Log-and-continue on stale containers instead of exit 1
      # (was #230: hard-failed close on the self-repo, which never has a live preview stack).
      STALE=$(docker ps -a --filter "label=com.docker.compose.project=mh-preview-${ISSUE}" --format '{{.Names}}' 2>/dev/null || true)
      if [ -n "$STALE" ]; then
        echo "WARNING: Stale preview containers remain after teardown (continuing):" >&2
        echo "$STALE" >&2
      else
        echo "close-preview: teardown verified — no mh-preview-${ISSUE} containers remain"
      fi
```

   Indentation must stay exactly as shown (6 spaces for top-level block lines, 8 spaces inside
   the `if`/`else`) — this is a YAML block scalar (`bash: |`), and the surrounding
   "Tearing down..." / `docker compose down -v` line immediately above and the "Find the PR"
   line immediately below must not move or change.

2. Verify the DAG/when-expression gates still pass (this node's `id`, `depends_on`, and
   `when:` are untouched, so both must be a no-op pass):

```bash
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```

Expected output: both commands exit `0` with no error output.

3. Re-run Task 1's regression test as a sanity check that the real YAML's replacement block has
   the same branch structure and message strings as the test's now-fixed copy (the test
   hardcodes `mh-preview-99` and a literal `$stale` in place of the real `${ISSUE}` variable and
   the live `docker ps` capture, so this is a logic/message check, not a literal byte-diff):

```bash
bash tests/test_close_preview_teardown.sh
```

Expected output: `Results: 7 passed, 0 failed`, exit `0`.

4. Diff-review the change is scoped to exactly the intended lines:

```bash
git diff workflows/archon-dark-factory.yaml
```

Expected output: a diff touching only the stale-container assertion block (comment line added,
`ERROR:` → `WARNING:`, `exit 1` removed, `if`/`else`/`fi` restructured) — no other line in
`workflows/archon-dark-factory.yaml` changes.

5. Commit:

```bash
git add workflows/archon-dark-factory.yaml
git commit -m "fix(close-preview): fail-soft the stale-container teardown check (issue #230)

Mirrors preview-up's preview_fail() philosophy: a leftover mh-preview-\${ISSUE}
container (including the deterministic self-repo case where none ever existed)
now logs a WARNING and continues instead of exit 1, so close-preview reaches
mark-ready/merge/set-status instead of halting the close workflow."
```

---

## Out of Scope (explicitly, per spec)

- No change to PR discovery (`codehost find-change`), the `needs-discussion` guard,
  `codehost mark-ready`, `codehost merge`, or `tracker set-status done` — their existing
  `ERROR:` + `exit 1` handling on real failure is correct and must keep blocking the close.
- No change to `push-and-pr`'s draft-PR creation logic — it already opens every PR as
  `--draft` unconditionally, and `close-preview`'s existing `mark-ready` call (unchanged,
  line ~240) already promotes the PR once the softened teardown can no longer short-circuit
  ahead of it.
- No broad "existence-check the whole node" rewrite (the #222 pattern applied literally) —
  rejected per the spec's Brainstorming Q&A #1 as a functional regression risk (would let a
  close silently "succeed" without merging).
- No `.github/workflows/ci.yml` change — the new test follows `test_preview_differentiator.sh`'s
  existing precedent of not being wired into the explicit CI step list.
