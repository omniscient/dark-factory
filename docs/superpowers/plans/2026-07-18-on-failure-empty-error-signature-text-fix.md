# Plan: `on_failure` writes error signatures with empty text, defeating #33's environmental carve-out

**Issue:** omniscient/dark-factory#303
**Spec:** [docs/superpowers/specs/2026-07-17-on-failure-empty-error-signature-text-design.md](../specs/2026-07-17-on-failure-empty-error-signature-text-design.md)

## Goal

`entrypoint.sh`'s `on_failure()` (the `ERR` trap) calls `_write_error_signature` at two call
sites (lines 609, 625) and `run_post_mortem` at one call site (line 627) with a literal `""`
instead of the live captured-output variable `$TMP_OUT`. This makes every text-based failure
classifier (`rate_limit`, `preview_infra`, `oos_files`, `build_failure`, `test_failure`) dead at
those call sites — any non-fast, non-clean failure collapses to `substantive:unknown`, which can
wrongly trip #33's early circuit breaker for genuinely environmental failures (e.g. session-window
deaths). Fix: reuse the existing script-global `$TMP_OUT` variable, guarded with `${TMP_OUT:-}` so
`set -u` never traps on an early/setup-phase crash where `TMP_OUT` isn't assigned yet.

## Architecture

No new state, no new files, no new functions. Three single-argument substitutions
(`""` → `"${TMP_OUT:-}"`) inside the existing `on_failure()` function in `entrypoint.sh`. `TMP_OUT`
is already a script-global (non-`local`) variable, created fresh each retry-loop iteration
(`entrypoint.sh:897`) and populated with the real `tee`'d `archon workflow run` output
(`entrypoint.sh:898`). Because bash `trap` handlers run in the same shell as the code that was
executing when the trap fired, `on_failure()` already has visibility into whatever value `TMP_OUT`
last held — it just never reads it today.

## Tech Stack

Bash (`entrypoint.sh`, `tests/test_entrypoint_error_signature.sh`), Python (`scripts/factory_core/error_signature.py` — unchanged, `tests/test_factory_core_error_signature.py` — extended).

## File Structure

| File | Change |
|---|---|
| `entrypoint.sh` | Fix 3 call sites inside `on_failure()`: lines 609, 625 (`_write_error_signature`), 627 (`run_post_mortem`) |
| `tests/test_entrypoint_error_signature.sh` | Add Section F: shell-level regression driving the literal session-limit string through `on_failure()`'s actual wiring |
| `tests/test_factory_core_error_signature.py` | Add `test_rate_limit_session_limit_string`: literal string through `classify()` (issue's explicit ask) |

---

## Task 1: Add failing shell-level regression test for `on_failure()`'s wiring

This proves the bug at the layer where it actually lives — `classify()` and the CLI already work
correctly today (confirmed in Task 3), so only a test that drives `on_failure()` itself can catch
a regression back to `""`.

**Files:** `tests/test_entrypoint_error_signature.sh`

### Step 1.1 — Write the failing test

Insert a new Section F immediately before the final cleanup block (currently lines 113-114:
`rm -f "$TMP_OUT"` / `rm -rf "$SCHEDULER_STATE_DIR" "$ARTIFACTS_DIR"`).

```bash
echo ""
echo "--- F: on_failure() threads live \$TMP_OUT to the text classifiers (#303 regression) ---"
rm -rf "$SCHEDULER_STATE_DIR"; SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-es-statedir5-XXXXXX)
echo "placeholder" > "${ARTIFACTS_DIR}/implementation.md"
INTENT=fix
TMP_OUT=$(mktemp)
echo "Claude session limit reached — resets 9:20pm (UTC)" > "$TMP_OUT"
( exit 1 )
on_failure
SIG_FILE="${SCHEDULER_STATE_DIR}/error-signatures/33.implement.sig"
assert_true "on_failure() classifies live TMP_OUT text as environmental:rate_limit" \
  "grep -q 'environmental:rate_limit' '$SIG_FILE'"
rm -f "${ARTIFACTS_DIR}/implementation.md"
```

Notes on the test design:
- `implementation.md` is dropped into `$ARTIFACTS_DIR` to force `artifact_present=true`, which
  breaks the `delivery_failure` conjunction (`classify()` checks that branch *first*, before any
  text classifier — without this, the test would pass for the wrong reason even on buggy code,
  since a fast/clean/no-commit failure classifies as `environmental:delivery_failure` regardless
  of text).
- `( exit 1 )` sets `$?` to 1 in the parent shell immediately before `on_failure` is called
  directly (not via the disabled `trap`); `on_failure()`'s first line, `local EXIT_CODE=$?`,
  captures it.
- Calling `on_failure` directly (the existing test file already disables the trap at line 37 and
  calls other sourced functions directly, e.g. `_write_error_signature`) exercises the real
  function body, including the two `_write_error_signature` call sites and the `run_post_mortem`
  call site this ticket fixes.
- `INTENT=fix` routes to the `else` branch (line 624-629 today), which is the implement/continue
  branch containing both the second `_write_error_signature` call and the `run_post_mortem` call.

### Step 1.2 — Verify it fails on current code

```bash
bash tests/test_entrypoint_error_signature.sh
```

Expected output includes:
```
--- F: on_failure() threads live $TMP_OUT to the text classifiers (#303 regression) ---
signature=substantive:unknown:1
Dark factory failed (exit 1). Moving issue #33 back to Ready...
Posting cost report to issue #33...
  FAIL: on_failure() classifies live TMP_OUT text as environmental:rate_limit
```
and a final line `Results: 10 passed, 1 failed` (exit code 1).

### Step 1.3 — Commit the failing test

```bash
git add tests/test_entrypoint_error_signature.sh
git commit -m "test(entrypoint): add failing regression for on_failure() empty-text bug (#303)"
```

---

## Task 2: Fix the three call sites in `entrypoint.sh`

**Files:** `entrypoint.sh`

### Step 2.1 — Fix the refine/plan/deconflict branch (line 609)

```diff
-      _write_error_signature "$(_failure_phase_for_intent)" "$EXIT_CODE" ""
+      _write_error_signature "$(_failure_phase_for_intent)" "$EXIT_CODE" "${TMP_OUT:-}"
       echo "Refinement pipeline failed (exit $EXIT_CODE) for issue #$ISSUE_NUM"
```

### Step 2.2 — Fix the implement/continue branch (lines 625, 627)

```diff
-      _write_error_signature "$(_failure_phase_for_intent)" "$EXIT_CODE" ""
+      _write_error_signature "$(_failure_phase_for_intent)" "$EXIT_CODE" "${TMP_OUT:-}"
       echo "Dark factory failed (exit $EXIT_CODE). Moving issue #$ISSUE_NUM back to Ready..."
-      run_post_mortem "$EXIT_CODE" "" || true
+      run_post_mortem "$EXIT_CODE" "${TMP_OUT:-}" || true
```

Apply both diffs directly to `entrypoint.sh` with an editor (no other lines in `on_failure()`
change). `${TMP_OUT:-}` is mandatory, not a bare `$TMP_OUT` reference: `on_failure` legitimately
fires for early/setup-phase crashes before `TMP_OUT` is ever assigned (`entrypoint.sh:897`), and
`entrypoint.sh` runs under `set -euo pipefail` (line 2) — a bare unset-variable reference would
trip `set -u` inside the trap handler itself.

### Step 2.3 — Verify the new shell test passes

```bash
bash tests/test_entrypoint_error_signature.sh
```

Expected output includes:
```
--- F: on_failure() threads live $TMP_OUT to the text classifiers (#303 regression) ---
signature=environmental:rate_limit
Dark factory failed (exit 1). Moving issue #33 back to Ready...
Posting cost report to issue #33...
  PASS: on_failure() classifies live TMP_OUT text as environmental:rate_limit
```
and a final line `Results: 11 passed, 0 failed` (exit code 0).

### Step 2.4 — Verify no regression in the rest of the shell suite

Already covered by the same invocation above (Sections A-E must still all `PASS`) — confirm the
full `Results: 11 passed, 0 failed` line, not just Section F.

### Step 2.5 — Commit the fix

```bash
git add entrypoint.sh
git commit -m "fix(circuit-break): thread live TMP_OUT into on_failure's error-signature/post-mortem calls (#303)"
```

---

## Task 3: Extend the Python fixture test with the literal observed string

The issue explicitly asks for a regression test feeding the literal observed string through
`error-signature-write`. `classify()` and the CLI were never broken (only `entrypoint.sh`'s wiring
was), so this test is expected to pass immediately — it guards against a future regression in the
regex or the CLI plumbing, distinct from Task 1's shell-level guard on the wiring itself.

**Files:** `tests/test_factory_core_error_signature.py`

### Step 3.1 — Write the test

Add immediately after the existing `test_rate_limit` function:

```python
def test_rate_limit_session_limit_string():
    sig = _classify(text="Claude session limit reached — resets 9:20pm (UTC)", exit_code=1)
    assert sig == "environmental:rate_limit"
```

### Step 3.2 — Verify it fails without the fixture present, then passes

The test targets already-correct code, so there is no red step against `entrypoint.sh` — instead
confirm the assertion is meaningful by first checking it would fail against a wrong expectation,
then run for real:

```bash
python3 -m pytest tests/test_factory_core_error_signature.py -q
```

Expected output: `15 passed` (14 existing + 1 new), exit code 0.

### Step 3.3 — Commit

```bash
git add tests/test_factory_core_error_signature.py
git commit -m "test(error-signature): add literal session-limit string fixture (#303)"
```

---

## Task 4: Final verification

**Files:** none (verification only)

### Step 4.1 — Run the full test suite

```bash
python3 -m pytest tests/ -v
bash tests/test_entrypoint_error_signature.sh
bash smoke_gate.sh
```

Expected: all pytest tests pass, the shell suite reports `Results: 11 passed, 0 failed`, and
`smoke_gate.sh` exits 0.

### Step 4.2 — Confirm scope

```bash
git diff origin/main HEAD --stat
```

Expected: only `entrypoint.sh`, `tests/test_entrypoint_error_signature.sh`,
`tests/test_factory_core_error_signature.py`, plus this plan file and the already-committed spec
under `docs/superpowers/specs/` and memory file under `.archon/memory/`. No changes to
`gate_*`, breaker, budgets, or `deploy/**`.
