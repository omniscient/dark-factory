# Implementation Plan: run-record — make a silent, unwritable `runs.jsonl` loud

**Operator plan gate:** 2026-09-10 — approved with one blocking amendment (`os.geteuid` is
Unix-only and would break the whole test module on Windows). Everything else was re-verified
against `origin/main`; see the disposition comment.

**Issue:** #395

**Spec:** `docs/superpowers/specs/2026-09-10-run-record-ledger-write-failure-design.md`

---

## Goal

A root-owned `runs.jsonl` silently swallowed every per-stage verdict (`paused`,
`failed`, `validation`, `conformance`, `review`, `manifest_intake`) for five days because
`cmd_record()` had no try/except around `_append_jsonl()` and every `entrypoint.sh` call
site already wraps the CLI in `|| true`. Fix, per the approved spec:

1. `cmd_record()`'s ledger-append failure becomes loud: stderr line, best-effort Seq
   post, `factory.run_record.ledger_write_failed` health event, then re-raise the
   `OSError` (never `sys.exit()` — `handoff.py` calls `cmd_record()` in-process).
2. `main()`'s `record` dispatch is the only place that turns that `OSError` into a
   process exit (`sys.exit(4)`), keeping the diagnostic traceback.
3. `entrypoint.sh` gets a start-of-run preflight (`_check_ledger_writable`) that warns
   loudly (stderr + `factory.run_record.ledger_not_writable` health event) the moment a
   run starts against an unwritable ledger, instead of five days later.
4. That warning threads into `on_failure()`'s two existing failure-comment bodies (no
   new comment — an unwritable ledger is host-level, not issue-level).
5. New test coverage for all of the above, wired into CI.

## Architecture

No new modules. Two existing files change:

- `scripts/factory_core/run_record.py` — `cmd_record()` gains a try/except; `main()`'s
  `record` dispatch gains a try/except that maps `OSError` to `sys.exit(4)`.
- `entrypoint.sh` — a new `_check_ledger_writable()` function runs once, pre-clone, right
  after `RUN_ID`/`ISSUE_NUM` are available; `on_failure()` reads back the warning it sets.

`_append_jsonl()` itself, and `breaker.py`'s use of it via `append_stop_record()`, are
**unchanged** — that call path has its own deliberate print-and-swallow posture for the
scheduler's live poll loop and is out of scope.

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/run_record.py` | `cmd_record()` loud-failure branch; `main()`'s `record` dispatch exit-code mapping |
| `tests/test_run_record.py` | New unit test (loud failure) + new CLI-level subprocess test (exit code 4) |
| `entrypoint.sh` | New `_check_ledger_writable()` preflight; `on_failure()` threads `LEDGER_NOTE` into both failure-comment bodies |
| `tests/test_entrypoint_preflight.sh` | New dynamic tests for the preflight function and the failure-comment thread-through |
| `.github/workflows/ci.yml` | Wire `tests/test_entrypoint_preflight.sh` into the `tests` job (currently never run) |

---

## Task 1 — `cmd_record()`: make the ledger-append failure loud

**Files:** `scripts/factory_core/run_record.py`, `tests/test_run_record.py`

### Step 1.1 — write the failing test

**Blocking amendment (operator plan gate): do not call `os.geteuid()` directly.** It does not
exist on Windows, and a `@pytest.mark.skipif(...)` condition is evaluated at **import** time — so
`os.geteuid() == 0` raises `AttributeError` during collection and takes down all ~40 tests in
`tests/test_run_record.py`, rather than skipping the one new test. That contradicts
`tests/conftest.py`, which exists precisely to keep these modules importable "during local
development on Windows", and `tests/test_run_record.py` already carries a
`sys.platform == "win32"` skipif, so the file is expected to be collected there. There is no
existing `geteuid` call anywhere in this repo — this would be the first, and it would be a
portability regression introduced by a ticket about silent failures, in a file whose collection
error is easy to mistake for unrelated breakage.

Define one helper near the top of `tests/test_run_record.py` (after the imports) and use it for
both new tests:

```python
def _is_root() -> bool:
    """os.geteuid() is Unix-only; on Windows report non-root so the chmod-based tests
    are governed by their own platform skipif instead of failing collection
    (operator plan gate, #395)."""
    return getattr(os, "geteuid", lambda: -1)() == 0
```

Then add, immediately after `test_post_seq_is_nonfatal` (the test at line ~197) and before the
`# --- _parse_archon_cost ---` section:

```python
@pytest.mark.skipif(_is_root(), reason="chmod 0o444 has no effect as root")
def test_record_ledger_write_failure_is_loud(tmp_path, monkeypatch, capsys):
    jsonl = tmp_path / "runs.jsonl"
    jsonl.write_text("")
    jsonl.chmod(0o444)
    monkeypatch.setattr(rr, "JSONL_PATH", jsonl)

    posted = []
    monkeypatch.setattr(rr, "_post_seq", lambda r: posted.append(r))
    health_events = []
    monkeypatch.setattr(
        rr, "emit_health_event",
        lambda event, issue, run_id, detail: health_events.append((event, issue, run_id, detail)),
    )

    with pytest.raises(OSError):
        rr.cmd_record(_RecordArgs())

    # Seq still gets the verdict -- the only remaining place it can land.
    assert len(posted) == 1
    assert posted[0]["stage"] == "conformance"

    assert len(health_events) == 1
    event, issue, run_id, detail = health_events[0]
    assert event == "factory.run_record.ledger_write_failed"
    assert issue == _RecordArgs.issue
    assert run_id == _RecordArgs.run_id
    assert detail["stage"] == "conformance"
    assert detail["path"] == str(jsonl)
    assert "error" in detail

    assert str(jsonl) in capsys.readouterr().err
```

### Step 1.2 — verify it fails

```bash
cd /workspace/dark-factory
PYTHONPATH=scripts python -m pytest tests/test_run_record.py::test_record_ledger_write_failure_is_loud -v
```

Expected: the `pytest.raises(OSError)` block itself already passes today — an unwritable
`JSONL_PATH` already raises `PermissionError` (an `OSError` subclass) out of the unguarded
`_append_jsonl(record)` call. The test fails on the assertions *after* it:
`_post_seq`/`emit_health_event` are never called (empty `posted`/`health_events` lists),
and there is no stderr line naming the path.

### Step 1.3 — implement

In `scripts/factory_core/run_record.py`, replace the tail of `cmd_record()`:

```python
    if details:
        record["detail"] = details

    _append_jsonl(record)
    _post_seq(record)
```

with:

```python
    if details:
        record["detail"] = details

    try:
        _append_jsonl(record)
    except OSError as exc:
        print(f"run-record: ledger append failed ({JSONL_PATH}): {exc}", file=sys.stderr)
        _post_seq(record)  # best-effort — the only remaining place this verdict can land
        emit_health_event(
            "factory.run_record.ledger_write_failed",
            record["issue_number"], record["run_id"],
            {"stage": record["stage"], "path": str(JSONL_PATH), "error": str(exc)[:500]},
        )
        raise
    _post_seq(record)
```

(Reads `record["issue_number"]`/`record["run_id"]`/`record["stage"]` rather than
`args.issue`/`args.run_id`/`args.stage` — `record` is the reliable source, since
`handoff.py`'s hand-built `argparse.Namespace` doesn't guarantee every field the way a
CLI-parsed `Namespace` does.)

Also update the now-stale comment on `_post_seq_raw` (called from both the success path,
where the local write already succeeded, and now this failure path, where it didn't):

```python
    except Exception:
        pass  # non-fatal: local file was already written
```

→

```python
    except Exception:
        pass  # non-fatal: Seq is always best-effort, whether or not the local ledger write succeeded
```

### Step 1.4 — verify it passes

```bash
PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v
```

Expected: all tests pass, including `test_record_ledger_write_failure_is_loud` and every
pre-existing test in the file (`test_record_writes_jsonl`, `test_post_seq_is_nonfatal`,
etc. — none of them exercise a failing `_append_jsonl`, so none change behavior).

### Step 1.5 — commit

```bash
git add scripts/factory_core/run_record.py tests/test_run_record.py
git commit -m "fix(#395): make cmd_record's ledger-append failure loud (stderr + Seq + health event)"
```

---

## Task 2 — `main()`'s `record` dispatch: translate the `OSError` into exit code 4

**Files:** `scripts/factory_core/run_record.py`, `tests/test_run_record.py`

### Step 2.1 — write the failing test

Add to `tests/test_run_record.py`, immediately after `test_cli_record_accepts_origin_flag`
(the last test in the file, ~line 1339):

```python
@pytest.mark.skipif(sys.platform == "win32", reason="fcntl import in the subprocess")
@pytest.mark.skipif(_is_root(), reason="chmod 0o444 has no effect as root")
def test_cli_record_exits_nonzero_on_unwritable_ledger(tmp_path):
    import subprocess
    jsonl = tmp_path / "runs.jsonl"
    jsonl.write_text("")
    jsonl.chmod(0o444)
    env = {
        **os.environ, "SCHEDULER_STATE_DIR": str(tmp_path),
        "SEQ_URL": "http://unreachable-host-99999:5341",
    }
    result = subprocess.run(
        [sys.executable, "-m", "factory_core.run_record", "record",
         "--run-id", "r1", "--issue", "1", "--intent", "intake", "--stage", "manifest_intake",
         "--verdict", "ACCEPTED"],
        cwd=str(Path(__file__).parent.parent / "scripts"),
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 4, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "runs.jsonl" in result.stderr
```

### Step 2.2 — verify it fails

```bash
PYTHONPATH=scripts python -m pytest tests/test_run_record.py::test_cli_record_exits_nonzero_on_unwritable_ledger -v
```

Expected: fails — today an unwritable ledger produces an *uncaught* traceback and Python's
default exit code `1`, not the documented `4`.

### Step 2.3 — implement

Add `import traceback` to the top of `scripts/factory_core/run_record.py` (alphabetically
between `import sys` and `import urllib.error`):

```python
import argparse
import fcntl
import json
import os
import pathlib
import re
import sys
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
```

In `main()`, replace:

```python
    parsed = parser.parse_args()
    if parsed.cmd == "record":
        cmd_record(parsed)
    elif parsed.cmd == "health-event":
```

with:

```python
    parsed = parser.parse_args()
    if parsed.cmd == "record":
        try:
            cmd_record(parsed)
        except OSError:
            traceback.print_exc()   # the CLI path is the interactive/debug one
            sys.exit(4)
    elif parsed.cmd == "health-event":
```

### Step 2.4 — verify it passes

```bash
PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v
```

Expected: all tests pass, including both new tests from Tasks 1 and 2.

### Step 2.5 — commit

```bash
git add scripts/factory_core/run_record.py tests/test_run_record.py
git commit -m "fix(#395): record subcommand exits 4 on an unwritable ledger, keeping the traceback"
```

---

## Task 3 — `entrypoint.sh`: ledger-writability preflight

**Files:** `entrypoint.sh`, `tests/test_entrypoint_preflight.sh`

### Step 3.1 — write the failing test

Replace the trailing `echo "PASS"` in `tests/test_entrypoint_preflight.sh` with:

```bash
# --- #395: _check_ledger_writable, exercised live. It sits above the
# ENTRYPOINT_SOURCE_ONLY early return (entrypoint.sh:~644), so it already ran once
# against the REAL SCHEDULER_STATE_DIR during the source below -- before this test's
# scratch dir exists. Re-invoke it explicitly afterwards against a scratch
# SCHEDULER_STATE_DIR (the function stays in scope post-source) rather than relying on
# that source-time run.
_REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export IDENTITY_SH="${IDENTITY_SH:-$_REPO_DIR/scripts/identity.sh}"
export FACTORY_PROVIDERS_CLI="${FACTORY_PROVIDERS_CLI:-$_REPO_DIR/scripts/factory_core/providers/cli.py}"
export GH_TOKEN="stub-token"
export CLAUDE_CODE_OAUTH_TOKEN="stub-token"

git() { return 0; }
export -f git
gh() { echo "stub-title"; return 0; }
export -f gh
docker() { return 0; }
export -f docker
claude() { echo "stub"; return 0; }
export -f claude

CURRENT_RUN_DIR=$(mktemp -d /tmp/ep-preflight-rundir-XXXXXX)
export CURRENT_RUN_DIR

ENTRYPOINT_SOURCE_ONLY=1 source "$ep"
trap - ERR
set +e; set +u; set +o pipefail

if [ "$(type -t _check_ledger_writable)" != "function" ]; then
  echo "FAIL: _check_ledger_writable not defined after sourcing entrypoint.sh"; exit 1
fi
echo "PASS: _check_ledger_writable is defined after sourcing entrypoint.sh"

SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-preflight-statedir-XXXXXX)
export SCHEDULER_STATE_DIR
LEDGER="$SCHEDULER_STATE_DIR/runs.jsonl"

# No ledger at all -> never a warning.
LEDGER_WRITE_WARNING=""
_check_ledger_writable
[ -z "$LEDGER_WRITE_WARNING" ] \
  || { echo "FAIL: LEDGER_WRITE_WARNING set when the ledger doesn't exist"; exit 1; }
echo "PASS: LEDGER_WRITE_WARNING stays empty when the ledger doesn't exist"

: > "$LEDGER"
if [ "$(id -u)" -eq 0 ]; then
  echo "SKIP: running as root -- chmod 0o444 has no effect, can't exercise the unwritable case"
else
  chmod 0o444 "$LEDGER"
  LEDGER_WRITE_WARNING=""
  # Capture stderr via redirection to a file, NOT `$(... 2>&1)` -- a command
  # substitution runs the function in a subshell, so the global it sets
  # (LEDGER_WRITE_WARNING) would never escape back to this shell.
  ERRF=$(mktemp)
  _check_ledger_writable 2>"$ERRF"
  WARN_OUT=$(cat "$ERRF")
  rm -f "$ERRF"
  [ -n "$LEDGER_WRITE_WARNING" ] \
    || { echo "FAIL: LEDGER_WRITE_WARNING not set for an unwritable ledger"; exit 1; }
  echo "$WARN_OUT" | grep -q '^WARNING: ledger not writable' \
    || { echo "FAIL: expected a WARNING line on stderr, got: $WARN_OUT"; exit 1; }
  echo "PASS: LEDGER_WRITE_WARNING set and WARNING printed for an unwritable ledger"
fi

# Writable ledger -> never a warning.
chmod 0o644 "$LEDGER" 2>/dev/null || true
LEDGER_WRITE_WARNING=""
_check_ledger_writable
[ -z "$LEDGER_WRITE_WARNING" ] \
  || { echo "FAIL: LEDGER_WRITE_WARNING set for a writable ledger"; exit 1; }
echo "PASS: LEDGER_WRITE_WARNING stays empty when the ledger is writable"

echo "PASS"
```

### Step 3.2 — verify it fails

```bash
bash tests/test_entrypoint_preflight.sh
```

Expected: `FAIL: _check_ledger_writable not defined after sourcing entrypoint.sh` (the
function doesn't exist yet), exit code 1.

### Step 3.3 — implement

In `entrypoint.sh`, insert the preflight right after `RUN_ID`/`ISSUE_NUM` are available
(`mkdir -p "$ARTIFACTS_DIR"`) and before the model-proxy correlation comment block:

```bash
export ARTIFACTS_DIR
mkdir -p "$ARTIFACTS_DIR"

# --- Ledger-writability preflight (#395): a root-owned runs.jsonl silently drops every
# per-stage verdict since run containers execute as `factory`. Detect it here, once per
# run, before anything durable is attempted -- see run_record.py's cmd_record for the
# loud-failure counterpart on the write side itself.
LEDGER_WRITE_WARNING=""
_check_ledger_writable() {
  local ledger="${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}/runs.jsonl"
  [ -e "$ledger" ] || return 0
  [ -w "$ledger" ] && return 0
  local owner
  owner=$(stat -c '%U:%G' "$ledger" 2>/dev/null || echo "unknown")
  LEDGER_WRITE_WARNING="WARNING: ledger not writable (owner=${owner}, path=${ledger})"
  echo "$LEDGER_WRITE_WARNING" >&2
  python3 /opt/dark-factory/scripts/factory_core/cli.py run-record health-event \
    --run-id "${RUN_ID:-unknown}" --issue "${ISSUE_NUM:-0}" \
    --event factory.run_record.ledger_not_writable \
    --detail "owner=${owner}" "path=${ledger}" 2>/dev/null || true
}
# Never let a chmod mistake kill the container: this must degrade to a loud warning,
# not a dead run. entrypoint.sh runs under `set -euo pipefail` and installs its ERR
# trap (on_failure) after this point, so an unguarded non-zero return here would abort
# the script before on_failure ever runs.
_check_ledger_writable || true

# --- Model-proxy correlation pointer (best-effort; consumed by factory-model-proxy
```

### Step 3.4 — verify it passes

```bash
bash tests/test_entrypoint_preflight.sh
```

Expected: every line prefixed `PASS:`, ending in a bare `PASS`, exit code 0.

### Step 3.5 — commit

```bash
git add entrypoint.sh tests/test_entrypoint_preflight.sh
git commit -m "fix(#395): entrypoint preflight warns loudly on an unwritable runs.jsonl"
```

---

## Task 4 — thread the warning into `on_failure()`'s failure comments

**Files:** `entrypoint.sh`, `tests/test_entrypoint_preflight.sh`

### Step 4.1 — write the failing test

Replace the trailing `echo "PASS"` in `tests/test_entrypoint_preflight.sh` (now at the
end of Task 3's additions) with:

```bash
# --- #395 Requirement 5: the warning threads into both existing failure-comment
# bodies (static text check -- avoids the hermetic-guard/network complexity of
# actually invoking on_failure(), see tests/test_run_record_hermetic.sh).
refine_body=$(awk '/post_or_update_comment "\$REFINE_FAILURE_MARKER"/,/^\$\{FOOTER\}"$/' "$ep")
echo "$refine_body" | grep -q 'LEDGER_NOTE' \
  || { echo "FAIL: REFINE_FAILURE_MARKER body does not reference LEDGER_NOTE"; exit 1; }
# Position check, not just presence: LEDGER_NOTE must land after the retry command,
# not spliced into the middle of the fenced retry block (Requirement 5).
retry_ln=$(echo "$refine_body" | grep -n 'docker compose --profile factory run --rm dark-factory' | head -1 | cut -d: -f1)
ledger_ln=$(echo "$refine_body" | grep -n 'LEDGER_NOTE' | head -1 | cut -d: -f1)
[ -n "$retry_ln" ] && [ -n "$ledger_ln" ] && [ "$ledger_ln" -gt "$retry_ln" ] \
  || { echo "FAIL: LEDGER_NOTE in REFINE_FAILURE_MARKER body is not after the retry command"; exit 1; }
echo "PASS: REFINE_FAILURE_MARKER body includes LEDGER_NOTE after the retry block"

factory_body=$(awk '/post_or_update_comment "\$FACTORY_FAILURE_MARKER"/,/^\$\{FOOTER\}"$/' "$ep")
echo "$factory_body" | grep -q 'LEDGER_NOTE' \
  || { echo "FAIL: FACTORY_FAILURE_MARKER body does not reference LEDGER_NOTE"; exit 1; }
retry_ln=$(echo "$factory_body" | grep -n 'docker compose --profile factory run --rm dark-factory' | head -1 | cut -d: -f1)
ledger_ln=$(echo "$factory_body" | grep -n 'LEDGER_NOTE' | head -1 | cut -d: -f1)
[ -n "$retry_ln" ] && [ -n "$ledger_ln" ] && [ "$ledger_ln" -gt "$retry_ln" ] \
  || { echo "FAIL: LEDGER_NOTE in FACTORY_FAILURE_MARKER body is not after the retry command"; exit 1; }
echo "PASS: FACTORY_FAILURE_MARKER body includes LEDGER_NOTE after the retry block"

echo "PASS"
```

### Step 4.2 — verify it fails

```bash
bash tests/test_entrypoint_preflight.sh
```

Expected: `FAIL: REFINE_FAILURE_MARKER body does not reference LEDGER_NOTE` (neither
body references it yet).

### Step 4.3 — implement

In `entrypoint.sh`'s `on_failure()`, insert the `LEDGER_NOTE` computation right before
the branch that builds the two failure comments:

```bash
    rm -f "$FAIL_COST_JSON" "$FAIL_COST_STDERR"
  fi
  # #395: thread the ledger-writability warning into whichever failure comment below
  # actually fires, mirroring _handle_session_window_pause's SESSION_WINDOW_MATCHED_PATTERN
  # -> SUMMARY_LINE pattern (helper sets a global, the comment builder reads it back).
  local LEDGER_NOTE=""
  [ -n "${LEDGER_WRITE_WARNING:-}" ] && LEDGER_NOTE="

> ⚠️ ${LEDGER_WRITE_WARNING}"
  if [ -n "${ISSUE_NUM:-}" ] && [ "$INTENT" != "close" ]; then
```

Then append `${LEDGER_NOTE}` to both marker bodies, immediately after their fenced retry
block and before the closing `---`/`${FOOTER}"`. First, the `REFINE_FAILURE_MARKER` body:

```bash
The refinement pipeline encountered an error (exit code $EXIT_CODE) and could not complete.

\`\`\`bash
# Retry
docker compose --profile factory run --rm dark-factory \"$ARGUMENTS\"
\`\`\`

---
${FOOTER}"
```

→

```bash
The refinement pipeline encountered an error (exit code $EXIT_CODE) and could not complete.

\`\`\`bash
# Retry
docker compose --profile factory run --rm dark-factory \"$ARGUMENTS\"
\`\`\`
${LEDGER_NOTE}

---
${FOOTER}"
```

Then the `FACTORY_FAILURE_MARKER` body:

```bash
The dark factory encountered an error (exit code $EXIT_CODE) and could not complete.
${BOARD_NOTE}

\`\`\`bash
# Retry
docker compose --profile factory run --rm dark-factory \"$ARGUMENTS\"
\`\`\`

---
${FOOTER}"
```

→

```bash
The dark factory encountered an error (exit code $EXIT_CODE) and could not complete.
${BOARD_NOTE}

\`\`\`bash
# Retry
docker compose --profile factory run --rm dark-factory \"$ARGUMENTS\"
\`\`\`
${LEDGER_NOTE}

---
${FOOTER}"
```

### Step 4.4 — verify it passes

```bash
bash tests/test_entrypoint_preflight.sh
```

Expected: every line prefixed `PASS:`, ending in a bare `PASS`, exit code 0.

### Step 4.5 — commit

```bash
git add entrypoint.sh tests/test_entrypoint_preflight.sh
git commit -m "fix(#395): surface the ledger-writability warning in on_failure's comments"
```

---

## Verified at the operator plan gate (2026-09-10)

- **`tests/test_entrypoint_preflight.sh` really is absent from CI.** `.github/workflows/ci.yml`
  lists every other `tests/test_*.sh` individually (`:15-38`) and omits this one, so Task 5's
  premise holds. The chosen insertion point — after `sudo install -d -m 777 /var/lib/dark-factory`
  (`:23`) and before the "assert nothing touched it" check (`:27`) — is correct: the new
  assertions use `mktemp` scratch dirs and never write to the shared path. Note
  `.github/workflows/ci.yml` is **not** a hard-excluded path; only
  `.github/workflows/publish.yml` and `deploy/instances/**` are.
- **`run_record.py` has an `if __name__ == "__main__": main()` guard (`:888-889`)**, so Task 2's
  `python -m factory_core.run_record` subprocess actually runs `main()` instead of importing
  silently and exiting 0.
- **`JSONL_PATH` is derived from `SCHEDULER_STATE_DIR` at import** (`run_record.py:24-25`), so
  passing `SCHEDULER_STATE_DIR` in the subprocess env does redirect the ledger as Task 2 assumes.
- **The `awk` range anchor works:** `${FOOTER}"` sits alone on its own line at the end of both
  failure-comment bodies (`entrypoint.sh:595`, `:618`), so each range terminates where Task 4
  expects.
- **Task 3's subshell avoidance is correct and load-bearing.** Capturing stderr with
  `ERRF=$(mktemp); _check_ledger_writable 2>"$ERRF"` rather than
  `$(_check_ledger_writable 2>&1)` is the difference between the test working and silently never
  observing `LEDGER_WRITE_WARNING` — a command substitution runs the function in a subshell, so
  the global it sets would never escape. Keep the comment that explains it.

## Task 5 — wire `tests/test_entrypoint_preflight.sh` into CI

**Files:** `.github/workflows/ci.yml`

This file currently exists but is **never invoked by CI** — `.github/workflows/ci.yml`'s
`tests` job lists every other `tests/test_*.sh` individually but omits it. Tasks 3-4 just
gave it real assertions (previously it was static-grep-only); without this step those
assertions never run anywhere, silently defeating Requirement 6.

### Step 5.1 — implement

In `.github/workflows/ci.yml`, in the `tests` job:

```yaml
      - run: bash tests/test_entrypoint_current_run.sh
      - run: sudo install -d -m 777 /var/lib/dark-factory
      - run: bash tests/test_entrypoint_session_window.sh
      - run: bash tests/test_entrypoint_error_signature.sh
      - name: Assert neither entrypoint test touched /var/lib/dark-factory
        run: test -z "$(ls -A /var/lib/dark-factory)"
```

→

```yaml
      - run: bash tests/test_entrypoint_current_run.sh
      - run: sudo install -d -m 777 /var/lib/dark-factory
      - run: bash tests/test_entrypoint_preflight.sh
      - run: bash tests/test_entrypoint_session_window.sh
      - run: bash tests/test_entrypoint_error_signature.sh
      - name: Assert none of the entrypoint tests touched /var/lib/dark-factory
        run: test -z "$(ls -A /var/lib/dark-factory)"
```

(Placed inside the same `/var/lib/dark-factory` scratch-dir bracket as the other two
entrypoint tests that source `entrypoint.sh`, for consistency — `_check_ledger_writable`'s
own source-time invocation only reads/stats the ledger, it never writes one, so the
"assert empty" check afterward still holds.)

### Step 5.2 — verify

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))" && echo "YAML OK"
grep -n 'test_entrypoint_preflight.sh' .github/workflows/ci.yml
```

Expected: `YAML OK`, and exactly one match showing the new step in place.

### Step 5.3 — commit

```bash
git add .github/workflows/ci.yml
git commit -m "test(#395): run tests/test_entrypoint_preflight.sh in CI"
```

---

## Task 6 — full regression check

**Files:** none (verification only)

### Step 6.1 — run the full Python suite

```bash
PYTHONPATH=scripts python -m pytest tests/ -v
```

Expected: all tests pass, including (unmodified, per the spec's Requirement 6 callout)
`tests/test_handoff.py::test_intake_records_internal_error_for_unwritable_artifacts_dir`
(the existing OSError-arm test around line 800-828), which must still pass exactly as
before — it exercises a *writable* ledger and asserts the `REJECTED`/`internal_error`
row, not ledger-write failure itself.

### Step 6.2 — run the affected bash tests directly

```bash
bash tests/test_run_record_hermetic.sh
bash tests/test_entrypoint_preflight.sh
bash tests/test_entrypoint_session_window.sh
bash tests/test_entrypoint_error_signature.sh
```

Expected: `PASS` (or per-assertion `PASS:` lines) from each, exit code 0.

### Step 6.3 — DAG / workflow checks (CLAUDE.md conventions)

```bash
python3 scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python3 scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```

Expected: no output / exit code 0 (this ticket touches no workflow YAML, so this is a
pure regression check).

No commit for this task — it is verification only, confirming Tasks 1-5 didn't regress
anything else in the suite.
