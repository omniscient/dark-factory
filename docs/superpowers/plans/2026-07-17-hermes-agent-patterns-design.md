# Apply Hermes Agent Patterns — Early Circuit-Break on Repeated Failure Signature + Implement-Prompt Hardening — Implementation Plan

**Issue:** omniscient/dark-factory#33
**Spec:** [docs/superpowers/specs/2026-07-17-hermes-agent-patterns-design.md](../specs/2026-07-17-hermes-agent-patterns-design.md)

## Goal

Give the scheduler a repeated-**substantive**-failure early circuit break (independent of, and
earlier than, the existing count-based `MAX_RETRIES`/`REFINE_MAX_RETRIES`), carrying the operator's
environmental carve-out (rate-limit / delivery-failure / preview-infra repeats must NOT trip early),
plus 3 non-overlapping prompt-hardening blocks in `commands/dark-factory-implement.md`. Requirements
1, 2, and 6 (17-recipe disposition table, persistent-daemon-not-needed evaluation, spec committed to
`docs/superpowers/specs/`) are already satisfied by the spec document itself — no code task below
addresses them; the spec's §1 and §4 are the deliverable.

## Requirements coverage

| Spec requirement | Where satisfied |
|---|---|
| 1. 17-recipe disposition | Spec §4 (documentation only — no plan task) |
| 2. Persistent-daemon evaluation | Spec §1 (documentation only — no plan task) |
| 3. Exactly 3 non-overlapping prompt-hardening blocks | Task 7 |
| 4. Self-interruption with environmental carve-out | Tasks 1–6 |
| 5. Minimum credit-assignment state (one field, no new file) | Task 4 (`breaker.py` extension of existing `scheduler-state.json`) |
| 6. Spec committed regardless of disposition | Already true (spec file exists on this branch) |

## Architecture

```text
container (entrypoint.sh)                      scheduler (scheduler.sh)
────────────────────────────                    ─────────────────────────
on real task failure (main archon-workflow       before each of the 4 existing
loop, exit-code path) OR an early setup           retry-count checks:
crash caught by the ERR trap:                     check_failure_signature(issue, phase)
  _write_error_signature(phase, exit_code, text)    → factory_core/cli.py breaker-check-signature
    → factory_core/cli.py error-signature-write        → breaker.record_failure_signature()
      → factory_core.error_signature.classify()           reads + deletes the drop file,
      → factory_core.error_signature.write_signature()    updates scheduler-state.json's
        writes ${SCHEDULER_STATE_DIR}/                    "<key>:sig", returns
        error-signatures/<issue>.<phase>.sig               stuck=True only when both the
                                                             stored and new signature carry
                                                             "substantive:" and match
                                                       on stuck=True: trip_to_blocked()
                                                       immediately (bypasses MAX_RETRIES)
```

**Deviation from the spec's literal hook location, and why (read before implementing Task 3):**
The spec's Architecture §2 says to call the classify+write helper "from inside `on_failure()`
(line 488)... in both the refine/plan/deconflict branch (line 518) and the implement branch (line
537)." Tracing `entrypoint.sh`'s actual control flow during planning found that `on_failure()` is
registered as `trap on_failure ERR` (line 562), and **bash's ERR trap does not fire for an explicit
`exit N`** (verified: `bash -c 'trap "echo fired" ERR; exit 7'` does not print `fired` — this is
standard, documented bash behavior, not a version quirk). The main archon-workflow loop (lines
807–860) wraps the actual task run in `set +e` / `set -e` specifically so it can branch on
`$EXIT_CODE` itself (session-window pause vs. rate-limit sleep-retry vs. real failure), and on a
real failure it calls `run_post_mortem` then `exit "$EXIT_CODE"` directly (line 856) — **never
re-entering `set -e`'s failure path**, so `on_failure()` never runs for this call site. The same is
true for every intent that reaches this loop (`refine`, `plan`, `fix`, `continue`) since they all
share it. `on_failure()` (the ERR trap) is reachable only for genuine early/setup-phase crashes
(clone, dependency install, config apply, or a few other ungated commands between `trap` registration
and the main loop) — which happen to be a good structural match for the `delivery_failure`
environmental class the spec's own taxonomy already accounts for (fast, zero-commit, no-artifact).
This is also internally consistent with the spec's own reasoning that `rate_limit` is "residual /
defense-in-depth only — genuine rate-limit/session-window text is already intercepted... before
`on_failure()` runs at all": that interception (`_handle_session_window_pause` and the plain-grep
sleep-retry) exists *only* inside the main loop, downstream of where the spec's classify() call was
meant to sit — so the spec's own text already implies a hook point downstream of the rate-limit
checks, which structurally can only be the main loop, not the ERR trap.

Net effect: Task 3 adds the classify+write call in **two** places — the main loop's real-failure
branch (the functionally load-bearing one, with the actual captured transcript) **and** both
branches of `on_failure()` (matching the spec's literal instruction, providing coverage for early
crashes, and — via the pre-existing, unrelated, out-of-scope `_conflict_escalate` being undefined,
which turns every deconflict escalation into an ERR-trap-triggering "command not found" — the only
path deconflict failures currently take at all). This satisfies Requirement 4 as written; it does
not change the requirement, the taxonomy, the state shape, or any other part of the spec — only
where in `entrypoint.sh` the same helper call is wired in, which is an implementation-level decision.
`_conflict_escalate` being undefined is a pre-existing, unrelated defect — out of scope for this
ticket; do not fix it (Scope Discipline). If encountered during implementation, record it in
`$ARTIFACTS_DIR/out-of-scope.md` and leave it unfixed.

## Tech Stack

Bash (`entrypoint.sh`, `scheduler.sh`), Python 3 (`scripts/factory_core/`), pytest for Python unit
tests, the repo's existing hand-rolled bash test harness (`tests/test_*.sh`) for shell-level tests.

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/error_signature.py` | **New.** `classify()`, `write_signature()` |
| `scripts/factory_core/cli.py` | Add `error-signature-write`, `breaker-check-signature` subcommands |
| `scripts/factory_core/breaker.py` | Add `record_failure_signature()` + small state-read/write helpers |
| `entrypoint.sh` | Add `DELIVERY_FAILURE_MAX_SECONDS` env-only knob, `_write_error_signature()` / `_failure_phase_for_intent()` helpers, wire into `on_failure()` and the main loop's failure branch |
| `scheduler.sh` | Add `check_failure_signature()` adapter, call it before each of the 4 existing retry-count checks |
| `commands/dark-factory-implement.md` | Add 3 prompt-hardening blocks (Pre-commit self-review, If-you-cannot-pass diagnosis, Report discipline) |
| `tests/test_factory_core_error_signature.py` | **New.** Unit tests for `classify()`/`write_signature()` |
| `tests/test_factory_core_breaker.py` | Extend — tests for `record_failure_signature()` |
| `tests/test_entrypoint_error_signature.sh` | **New.** Bash-level integration test for the entrypoint helpers |
| `tests/test_scheduler.sh` | Extend — early-trip tests at each of the 4 call sites |

---

## Task 1: `error_signature.py` — `classify()` and `write_signature()`

**Files:** `scripts/factory_core/error_signature.py` (new), `tests/test_factory_core_error_signature.py` (new)

### Step 1.1 — write the failing tests

Create `tests/test_factory_core_error_signature.py`:

```python
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core.error_signature import classify, write_signature


def _classify(text="", exit_code=1, elapsed=999, commits=1, dirty=False, artifact=True, **kw):
    return classify(
        text, exit_code,
        elapsed_seconds=elapsed,
        commits_since_start=commits,
        worktree_dirty=dirty,
        artifact_present=artifact,
        **kw,
    )


def test_delivery_failure_conjunction():
    sig = _classify(text="anything", elapsed=5, commits=0, dirty=False, artifact=False)
    assert sig == "environmental:delivery_failure"


def test_delivery_failure_requires_all_four_conjuncts():
    # elapsed under threshold but a commit landed — not delivery_failure
    assert _classify(text="", elapsed=5, commits=1, dirty=False, artifact=False) != \
        "environmental:delivery_failure"
    # elapsed under threshold, zero commits, clean tree, but an artifact exists
    assert _classify(text="", elapsed=5, commits=0, dirty=False, artifact=True) != \
        "environmental:delivery_failure"
    # elapsed at/over threshold
    assert _classify(text="", elapsed=30, commits=0, dirty=False, artifact=False) != \
        "environmental:delivery_failure"


def test_delivery_failure_max_seconds_is_tunable():
    sig = _classify(text="", elapsed=45, commits=0, dirty=False, artifact=False,
                     delivery_failure_max_seconds=60)
    assert sig == "environmental:delivery_failure"


def test_preview_infra_checked_before_build_failure():
    # Contains both a toolchain string and build-ish language; must classify preview_infra.
    sig = _classify(text="failed to solve: process /bin/sh -c npm ERR! build failed")
    assert sig == "environmental:preview_infra"


def test_rate_limit():
    sig = _classify(text="Error: you have hit your usage limit for this session", exit_code=1)
    assert sig == "environmental:rate_limit"


def test_oos_files():
    sig = _classify(text="OOS gate: excising out-of-scope files: foo.py", exit_code=1)
    assert sig == "substantive:oos_files:1"


def test_build_failure():
    sig = _classify(text="npm ERR! code ELIFECYCLE\nnpm ERR! Exit status 1", exit_code=1)
    assert sig == "substantive:build_failure:1"


def test_test_failure():
    sig = _classify(text="FAILED tests/test_foo.py::test_bar - AssertionError", exit_code=1)
    assert sig == "substantive:test_failure:1"


def test_unknown_fallback():
    sig = _classify(text="some completely unrelated stack trace", exit_code=3)
    assert sig == "substantive:unknown:3"


def test_environmental_signatures_have_no_exit_code_suffix():
    assert ":" not in _classify(text="", elapsed=5, commits=0, dirty=False,
                                 artifact=False).split("environmental:")[1]
    assert _classify(text="rate limit exceeded", elapsed=120) == "environmental:rate_limit"


def test_write_signature_atomic(tmp_path):
    write_signature(42, "implement", "substantive:test_failure:1", 1, tmp_path)
    sig_file = tmp_path / "error-signatures" / "42.implement.sig"
    assert sig_file.exists()
    data = json.loads(sig_file.read_text())
    assert data == {"signature": "substantive:test_failure:1", "phase": "implement", "exit_code": 1}


def test_write_signature_creates_parent_dirs(tmp_path):
    target = tmp_path / "nested" / "state"
    write_signature(7, "plan", "environmental:rate_limit", 1, target)
    assert (target / "error-signatures" / "7.plan.sig").exists()
```

### Step 1.2 — verify the tests fail

```bash
cd /workspace/dark-factory && python -m pytest tests/test_factory_core_error_signature.py -v
```
Expected: `ModuleNotFoundError: No module named 'factory_core.error_signature'`.

### Step 1.3 — implement

Create `scripts/factory_core/error_signature.py`:

```python
"""Repeated-failure-signature classification (#33): categorize a failed run's captured
output into a stable class-prefixed enum the scheduler compares across consecutive
attempts to decide whether to trip the circuit breaker early. Mirrors session_window.py's
_SUBSTRING_RE keyword-match style and its write_pause_sentinel drop-file pattern.
"""
import json
import re
from pathlib import Path

from .session_window import _SUBSTRING_RE as _RATE_LIMIT_RE

_PREVIEW_INFRA_RE = re.compile(
    r"buildkit|failed to solve|docker[- ]compose|pull access denied|manifest unknown|"
    r"no space left on device|port is already allocated|network .* not found|"
    r"preview stack|failed to build preview",
    re.IGNORECASE,
)
_OOS_FILES_RE = re.compile(r"OOS gate|out-of-scope", re.IGNORECASE)
_BUILD_FAILURE_RE = re.compile(
    r"npm err!|build failed|compilation error|modulenotfounderror|importerror|syntaxerror",
    re.IGNORECASE,
)
_TEST_FAILURE_RE = re.compile(
    r"assertionerror|failed \(errors=|failed \(failures=|pytest.*failed|tsc.*error ts\d+",
    re.IGNORECASE,
)


def classify(
    text: str,
    exit_code: int,
    *,
    elapsed_seconds: int,
    commits_since_start: int,
    worktree_dirty: bool,
    artifact_present: bool,
    delivery_failure_max_seconds: int = 30,
) -> str:
    if (
        elapsed_seconds < delivery_failure_max_seconds
        and commits_since_start == 0
        and not worktree_dirty
        and not artifact_present
    ):
        return "environmental:delivery_failure"
    if _PREVIEW_INFRA_RE.search(text):
        return "environmental:preview_infra"
    if _RATE_LIMIT_RE.search(text):
        return "environmental:rate_limit"
    if _OOS_FILES_RE.search(text):
        return f"substantive:oos_files:{exit_code}"
    if _BUILD_FAILURE_RE.search(text):
        return f"substantive:build_failure:{exit_code}"
    if _TEST_FAILURE_RE.search(text):
        return f"substantive:test_failure:{exit_code}"
    return f"substantive:unknown:{exit_code}"


def write_signature(issue_num: int, phase: str, signature: str, exit_code: int, state_dir) -> None:
    state_dir = Path(state_dir)
    sig_dir = state_dir / "error-signatures"
    sig_dir.mkdir(parents=True, exist_ok=True)
    path = sig_dir / f"{issue_num}.{phase}.sig"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"signature": signature, "phase": phase, "exit_code": exit_code}))
    tmp.rename(path)
```

### Step 1.4 — verify the tests pass

```bash
cd /workspace/dark-factory && python -m pytest tests/test_factory_core_error_signature.py -v
```
Expected: all tests pass.

### Step 1.5 — commit

```bash
git add scripts/factory_core/error_signature.py tests/test_factory_core_error_signature.py
git commit -m "feat: add error_signature classify/write for repeated-failure early circuit break (#33)"
```

---

## Task 2: `cli.py` — `error-signature-write` subcommand

**Files:** `scripts/factory_core/cli.py`

### Step 2.1 — write the failing test

Add to `tests/test_factory_core_error_signature.py` (append):

```python
import subprocess
import sys as _sys

CLI = str(Path(__file__).resolve().parents[1] / "scripts" / "factory_core" / "cli.py")


def test_cli_error_signature_write_end_to_end(tmp_path):
    text_file = tmp_path / "out.txt"
    text_file.write_text("FAILED tests/test_foo.py::test_bar - AssertionError")
    result = subprocess.run(
        [_sys.executable, CLI, "error-signature-write",
         "--issue", "9", "--phase", "implement", "--exit-code", "1",
         "--text-file", str(text_file),
         "--elapsed-seconds", "120", "--commits-since-start", "0",
         "--state-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "signature=substantive:test_failure:1" in result.stdout
    sig_file = tmp_path / "error-signatures" / "9.implement.sig"
    assert json.loads(sig_file.read_text())["signature"] == "substantive:test_failure:1"


def test_cli_error_signature_write_missing_text_file_is_empty_text(tmp_path):
    result = subprocess.run(
        [_sys.executable, CLI, "error-signature-write",
         "--issue", "9", "--phase", "plan", "--exit-code", "1",
         "--text-file", str(tmp_path / "nonexistent.txt"),
         "--elapsed-seconds", "5", "--commits-since-start", "0",
         "--state-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "signature=environmental:delivery_failure" in result.stdout
```

### Step 2.2 — verify the tests fail

```bash
cd /workspace/dark-factory && python -m pytest tests/test_factory_core_error_signature.py -v -k cli_error_signature
```
Expected: `error: argument {...}: invalid choice: 'error-signature-write'`.

### Step 2.3 — implement

In `scripts/factory_core/cli.py`, add a handler function after `_session_window_check` (after line 107):

```python
def _error_signature_write(args):
    from factory_core.error_signature import classify, write_signature
    text = ""
    if args.text_file:
        text_path = Path(args.text_file)
        text = text_path.read_text(errors="replace") if text_path.exists() else ""
    signature = classify(
        text,
        args.exit_code,
        elapsed_seconds=args.elapsed_seconds,
        commits_since_start=args.commits_since_start,
        worktree_dirty=args.worktree_dirty,
        artifact_present=args.artifact_present,
        delivery_failure_max_seconds=args.delivery_failure_max_seconds,
    )
    write_signature(args.issue, args.phase, signature, args.exit_code, Path(args.state_dir))
    print(f"signature={signature}")


def _breaker_check_signature(args):
    from factory_core.breaker import record_failure_signature
    state_file = Path(os.environ.get("STATE_FILE",
                                     "/var/lib/dark-factory/scheduler-state.json"))
    state_dir = Path(os.environ.get("SCHEDULER_STATE_DIR", "/var/lib/dark-factory"))
    stuck, sig = record_failure_signature(args.issue, args.phase, state_file, state_dir)
    print(f"stuck={'true' if stuck else 'false'} sig={sig}")
```

Register both subcommands in `main()`, after the `sw` (`session-window-check`) block (after line 163):

```python
    esw = sub.add_parser("error-signature-write")
    esw.add_argument("--issue", type=int, required=True)
    esw.add_argument("--phase", required=True)
    esw.add_argument("--exit-code", type=int, required=True)
    esw.add_argument("--text-file", default="")
    esw.add_argument("--elapsed-seconds", type=int, required=True)
    esw.add_argument("--commits-since-start", type=int, required=True)
    esw.add_argument("--worktree-dirty", action="store_true")
    esw.add_argument("--artifact-present", action="store_true")
    esw.add_argument("--delivery-failure-max-seconds", type=int, default=30,
                      dest="delivery_failure_max_seconds")
    esw.add_argument("--state-dir", default="/var/lib/dark-factory")
    esw.set_defaults(func=_error_signature_write)

    bcs = sub.add_parser("breaker-check-signature")
    bcs.add_argument("--issue", type=int, required=True)
    bcs.add_argument("--phase", required=True)
    bcs.set_defaults(func=_breaker_check_signature)
```

### Step 2.4 — verify the tests pass

```bash
cd /workspace/dark-factory && python -m pytest tests/test_factory_core_error_signature.py -v
```
Expected: all tests pass (Task 1 + Task 2 tests). `breaker-check-signature` is exercised in Task 4/6; it will fail until `record_failure_signature` exists (Task 4) — do not commit Task 2 until Task 4 lands, or gate the `_breaker_check_signature` import lazily (already done above via the inline `from factory_core.breaker import` inside the function body, so `cli.py` itself still imports cleanly).

### Step 2.5 — commit

```bash
git add scripts/factory_core/cli.py tests/test_factory_core_error_signature.py
git commit -m "feat: add error-signature-write and breaker-check-signature CLI subcommands (#33)"
```

---

## Task 3: `entrypoint.sh` — compute inputs, call the CLI, wire into both real hook points

**Files:** `entrypoint.sh`, `tests/test_entrypoint_error_signature.sh` (new)

**On `DELIVERY_FAILURE_MAX_SECONDS` being env-only (no config.yaml wiring), found during planning:**
`entrypoint.sh`'s `_entrypoint_cfg_apply()` reads `.claude/skills/refinement/config.yaml` (or the
baked `/opt/refinement-skills/config.yaml`), **not** `config/config.yaml` — confirmed by tracing
`scripts/factory_core/effective_config.py::materialize()`: because this repo already has a
committed clone-relative `.claude/skills/refinement/config.yaml`, the function's "transition
period" branch runs (`clone_cfg_path` exists → "write NOTHING — the committed clone file wins
byte-identically"), so anything added to `config/config.yaml` alone would never reach the
container's `_entrypoint_cfg_apply()` for this repo. And `.claude/skills/refinement/config.yaml`
itself is listed in `.factory/adapter.yaml`'s `hard_exclude_paths` — a human-in-the-loop-only
surface no automated phase may edit. Rather than either write an inert config value or touch an
excluded path, this task follows the codebase's own existing precedent for exactly this situation:
`scheduler.sh` line 17 already documents `REFINE_MAX_RETRIES` as "env-only: ... is not in
config.yaml by design." `DELIVERY_FAILURE_MAX_SECONDS` gets the same treatment — a bootstrap
default plus a documented env override, no config.yaml entry, no `_epcfg` line in
`_entrypoint_cfg_apply()`.

### Step 3.2 — write the failing bash integration test

Create `tests/test_entrypoint_error_signature.sh` (mirrors `tests/test_entrypoint_session_window.sh`):

```bash
#!/usr/bin/env bash
# Verifies _write_error_signature() / _failure_phase_for_intent() (#33): the entrypoint
# helper computes elapsed/commits/dirty/artifact signals and hands them to
# factory_core/cli.py error-signature-write, which drops a classified signature file the
# scheduler later reads back via breaker.record_failure_signature().
# Run: bash tests/test_entrypoint_error_signature.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export GH_TOKEN="stub-token"
export CLAUDE_CODE_OAUTH_TOKEN="stub-token"

git() {
  case "$1" in
    log) echo "$STUB_COMMITS" | sed -n '1,'"${STUB_COMMIT_COUNT:-0}"'p' ;;
    status) [ "${STUB_DIRTY:-false}" = "true" ] && echo " M some/file.py" ;;
    *) return 0 ;;
  esac
}
export -f git
gh() { echo "stub-title"; return 0; }
export -f gh
docker() { return 0; }
export -f docker
claude() { echo "stub"; return 0; }
export -f claude

ENTRYPOINT_SOURCE_ONLY=1 source "$SCRIPT_DIR/../entrypoint.sh"

trap - ERR
set +e; set +u; set +o pipefail

CLONE_DIR="$(dirname "$REPO_ROOT")"
ISSUE_NUM=33
RUN_ID=test-run-1

PASSED=0; FAILED=0
assert_true() {
  local desc="$1"; shift
  if eval "$1"; then echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else echo "  FAIL: $desc" >&2; FAILED=$((FAILED+1)); fi
}
assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else echo "  FAIL: $desc — expected='$expected' got='$actual'" >&2; FAILED=$((FAILED+1)); fi
}

echo "--- A: phase mapping ---"
INTENT=refine; assert_eq "refine -> refine" "refine" "$(_failure_phase_for_intent)"
INTENT=plan; assert_eq "plan -> plan" "plan" "$(_failure_phase_for_intent)"
INTENT=deconflict; assert_eq "deconflict -> resolve" "resolve" "$(_failure_phase_for_intent)"
INTENT=fix; assert_eq "fix -> implement" "implement" "$(_failure_phase_for_intent)"
INTENT=continue; assert_eq "continue -> implement" "implement" "$(_failure_phase_for_intent)"

echo ""
echo "--- B: delivery_failure classification (no commits, no artifact, fast) ---"
SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-es-statedir-XXXXXX)
ARTIFACTS_DIR=$(mktemp -d /tmp/ep-es-artifacts-XXXXXX)
RUN_STARTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
STUB_COMMIT_COUNT=0
STUB_DIRTY=false
INTENT=fix
_write_error_signature "implement" 1 ""
SIG_FILE="${SCHEDULER_STATE_DIR}/error-signatures/33.implement.sig"
assert_true "signature file written" "[ -f '$SIG_FILE' ]"
assert_true "classified environmental:delivery_failure" \
  "grep -q 'environmental:delivery_failure' '$SIG_FILE'"

echo ""
echo "--- C: substantive classification when a transcript + real work is present ---"
rm -rf "$SCHEDULER_STATE_DIR"; SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-es-statedir2-XXXXXX)
echo "placeholder" > "${ARTIFACTS_DIR}/implementation.md"
TMP_OUT=$(mktemp)
echo "FAILED tests/test_foo.py::test_bar - AssertionError" > "$TMP_OUT"
_write_error_signature "implement" 1 "$TMP_OUT"
SIG_FILE="${SCHEDULER_STATE_DIR}/error-signatures/33.implement.sig"
assert_true "classified substantive:test_failure" \
  "grep -q 'substantive:test_failure:1' '$SIG_FILE'"
rm -f "${ARTIFACTS_DIR}/implementation.md"

echo ""
echo "--- D: pre-existing workflow context files must NOT count as artifact_present (#279 regression) ---"
# issue.json / context-budget.json / token-opt-caps.env are written into ARTIFACTS_DIR by
# the workflow runner BEFORE the phase command ever executes — present on every run,
# including one where the agent did zero work. If these counted toward artifact_present,
# delivery_failure would never classify, which is exactly the false-positive early-trip
# the operator's carve-out (#279) was added to prevent.
rm -rf "$SCHEDULER_STATE_DIR"; SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-es-statedir3-XXXXXX)
echo '{"resolved_number":33}' > "${ARTIFACTS_DIR}/issue.json"
echo '{}' > "${ARTIFACTS_DIR}/context-budget.json"
_write_error_signature "implement" 1 ""
SIG_FILE="${SCHEDULER_STATE_DIR}/error-signatures/33.implement.sig"
assert_true "context-only artifacts still classify as delivery_failure" \
  "grep -q 'environmental:delivery_failure' '$SIG_FILE'"

echo ""
echo "--- E: run_post_mortem's factory-failures.jsonl must NOT count as artifact_present either ---"
rm -rf "$SCHEDULER_STATE_DIR"; SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-es-statedir4-XXXXXX)
echo '[{"issue":33}]' > "${ARTIFACTS_DIR}/factory-failures.jsonl"
_write_error_signature "implement" 1 ""
SIG_FILE="${SCHEDULER_STATE_DIR}/error-signatures/33.implement.sig"
assert_true "factory-failures.jsonl alone still classifies as delivery_failure" \
  "grep -q 'environmental:delivery_failure' '$SIG_FILE'"

rm -f "$TMP_OUT"
rm -rf "$SCHEDULER_STATE_DIR" "$ARTIFACTS_DIR"

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
```

### Step 3.3 — verify the test fails

```bash
cd /workspace/dark-factory && bash tests/test_entrypoint_error_signature.sh
```
Expected: `_failure_phase_for_intent: command not found` (or similar) — the helpers don't exist yet.

### Step 3.4 — implement: bootstrap default (env-only, no `_entrypoint_cfg_apply()` wiring — see the
callout above Step 3.1) and the two helpers

In `entrypoint.sh`, add a bootstrap default after line 33 (`SESSION_WINDOW_FALLBACK_MINUTES=...`):

```bash
# env-only by design, mirroring REFINE_MAX_RETRIES (scheduler.sh:17) — not threaded through
# _entrypoint_cfg_apply()/config.yaml; see Task 3's callout above Step 3.1 for why.
DELIVERY_FAILURE_MAX_SECONDS="${DELIVERY_FAILURE_MAX_SECONDS:-30}"
```

Do **not** add a corresponding `_epcfg` line in `_entrypoint_cfg_apply()`.

Add the two new helpers immediately after `_handle_session_window_pause()` ends (after line 307,
before `post_cost_report()`):

```bash
# Maps the container's INTENT to the phase string the scheduler's retry keys and
# trip_to_blocked() already use (_make_key in factory_core/breaker.py): "resolve" for
# deconflict (not "deconflict" — matches the existing scheduler.sh call sites), "refine"
# and "plan" pass through unchanged, everything else (fix/continue/recheck/fix-main) maps
# to "implement" (the bare-issue-number key).
_failure_phase_for_intent() {
  case "${INTENT:-fix}" in
    refine) echo "refine" ;;
    plan) echo "plan" ;;
    deconflict) echo "resolve" ;;
    *) echo "implement" ;;
  esac
}

# Classifies the current failure and drops the signature file the scheduler reads back on
# its next poll (mirrors _handle_session_window_pause's sentinel-file pattern). Called from
# two places: (1) the main archon-workflow loop's real-failure branch, with the captured
# transcript — the functionally load-bearing call, since that is where a real task failure
# (test_failure, build_failure, oos_files) is actually observed; (2) both branches of
# on_failure() (the ERR trap), with no transcript, covering early/setup-phase crashes before
# the main loop ever runs — these classify as environmental:delivery_failure by construction
# (fast, zero commits, no artifact), which is the correct, conservative outcome.
_write_error_signature() {
  local phase="$1" exit_code="$2" text_file="${3:-}"
  [ -z "${ISSUE_NUM:-}" ] && return 0
  local elapsed_seconds=0
  if [ -n "${RUN_STARTED_AT:-}" ]; then
    local started_epoch now_epoch
    started_epoch=$(date -u -d "$RUN_STARTED_AT" +%s 2>/dev/null || echo 0)
    now_epoch=$(date -u +%s)
    elapsed_seconds=$((now_epoch - started_epoch))
  fi
  local commits_since_start=0
  if [ -n "${RUN_STARTED_AT:-}" ]; then
    commits_since_start=$(git log --oneline --since="$RUN_STARTED_AT" HEAD 2>/dev/null | wc -l | tr -d ' ')
  fi
  local dirty_flag="" artifact_flag=""
  [ -n "$(git status --porcelain 2>/dev/null)" ] && dirty_flag="--worktree-dirty"
  # Allowlist of genuine phase-deliverable filenames, not a denylist of
  # run-record.json alone: $ARTIFACTS_DIR always already contains workflow-written
  # context artifacts (issue.json, context-budget.json, token-opt-caps.env — present
  # before the phase command even starts) and, for implement-intent failures,
  # run_post_mortem() unconditionally writes factory-failures.jsonl (line 250) before
  # this helper runs. A denylist of only run-record.json would treat all of those as
  # "real work happened," making artifact_present effectively always true and the
  # delivery_failure conjunction unreachable — defeating the #279 carve-out this
  # spec exists to add. The list below reuses run_post_mortem's own known-deliverable
  # set (line 201: implementation.md conformance.md review.md plan.md) plus the
  # refine/plan/deconflict/Task-7 deliverables.
  if [ -n "${ARTIFACTS_DIR:-}" ]; then
    for f in implementation.md conformance.md review.md plan.md \
             refinement-status.md conflict_resolution.md \
             failure-diagnosis.md out-of-scope.md; do
      if [ -f "${ARTIFACTS_DIR}/${f}" ]; then
        artifact_flag="--artifact-present"
        break
      fi
    done
  fi
  # TARGET-PATH: cli.py resolves under dark-factory/ in the clone — target's own copy until
  # P3 cleanup, baked self-contained fallback copy afterwards (df#14)
  python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" error-signature-write \
    --issue "$ISSUE_NUM" \
    --phase "$phase" \
    --exit-code "$exit_code" \
    --text-file "${text_file:-}" \
    --elapsed-seconds "$elapsed_seconds" \
    --commits-since-start "$commits_since_start" \
    $dirty_flag $artifact_flag \
    --delivery-failure-max-seconds "${DELIVERY_FAILURE_MAX_SECONDS:-30}" \
    --state-dir "${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}" \
    2>/dev/null || true
}
```

### Step 3.5 — wire into `on_failure()`

In `on_failure()`, in the refine/plan/deconflict branch, add the call right before the existing
`echo "Refinement pipeline failed..."` line (currently line 523):

```bash
    if [ "$INTENT" = "refine" ] || [ "$INTENT" = "plan" ] || [ "$INTENT" = "deconflict" ]; then
      _write_error_signature "$(_failure_phase_for_intent)" "$EXIT_CODE" ""
      echo "Refinement pipeline failed (exit $EXIT_CODE) for issue #$ISSUE_NUM"
```

In the `else` (implement) branch, add the call right before the existing
`echo "Dark factory failed..."` line (currently line 538):

```bash
    else
      _write_error_signature "$(_failure_phase_for_intent)" "$EXIT_CODE" ""
      echo "Dark factory failed (exit $EXIT_CODE). Moving issue #$ISSUE_NUM back to Ready..."
```

### Step 3.6 — wire into the main loop's real-failure branch

In the main `while true` loop, add the call right after `run_post_mortem` and before `rm -f
"$TMP_OUT"` (currently lines 854–855):

```bash
    run_post_mortem "$EXIT_CODE" "$TMP_OUT" || true
    _write_error_signature "$(_failure_phase_for_intent)" "$EXIT_CODE" "$TMP_OUT"
    rm -f "$TMP_OUT"
    exit "$EXIT_CODE"
```

### Step 3.7 — verify the test passes

```bash
cd /workspace/dark-factory && bash tests/test_entrypoint_error_signature.sh
```
Expected: `Results: N passed, 0 failed`.

### Step 3.8 — regression-check the existing session-window test

```bash
cd /workspace/dark-factory && bash tests/test_entrypoint_session_window.sh
```
Expected: unchanged, all passing (confirms the new helpers don't disturb `_handle_session_window_pause`).

### Step 3.9 — commit

```bash
git add entrypoint.sh tests/test_entrypoint_error_signature.sh
git commit -m "feat: wire error-signature classification into entrypoint failure paths (#33)"
```

---

## Task 4: `breaker.py` — `record_failure_signature()`

**Files:** `scripts/factory_core/breaker.py`, `tests/test_factory_core_breaker.py`

### Step 4.1 — write the failing tests

Append to `tests/test_factory_core_breaker.py`:

```python
from factory_core.breaker import record_failure_signature


def _drop(state_dir, issue, phase, signature, exit_code=1):
    sig_dir = state_dir / "error-signatures"
    sig_dir.mkdir(parents=True, exist_ok=True)
    (sig_dir / f"{issue}.{phase}.sig").write_text(
        json.dumps({"signature": signature, "phase": phase, "exit_code": exit_code}))


def test_record_failure_signature_no_drop_file_returns_false(tmp_path):
    sf = tmp_path / "state.json"
    stuck, sig = record_failure_signature(1, "implement", sf, tmp_path)
    assert stuck is False
    assert sig == ""


def test_record_failure_signature_first_substantive_not_stuck(tmp_path):
    sf = tmp_path / "state.json"
    _drop(tmp_path, 1, "implement", "substantive:test_failure:1")
    stuck, sig = record_failure_signature(1, "implement", sf, tmp_path)
    assert stuck is False
    assert sig == "substantive:test_failure:1"


def test_record_failure_signature_second_matching_substantive_is_stuck(tmp_path):
    sf = tmp_path / "state.json"
    _drop(tmp_path, 1, "implement", "substantive:test_failure:1")
    record_failure_signature(1, "implement", sf, tmp_path)
    _drop(tmp_path, 1, "implement", "substantive:test_failure:1")
    stuck, sig = record_failure_signature(1, "implement", sf, tmp_path)
    assert stuck is True
    assert sig == "substantive:test_failure:1"


def test_record_failure_signature_different_substantive_not_stuck(tmp_path):
    sf = tmp_path / "state.json"
    _drop(tmp_path, 1, "implement", "substantive:test_failure:1")
    record_failure_signature(1, "implement", sf, tmp_path)
    _drop(tmp_path, 1, "implement", "substantive:build_failure:1")
    stuck, sig = record_failure_signature(1, "implement", sf, tmp_path)
    assert stuck is False
    assert sig == "substantive:build_failure:1"


def test_record_failure_signature_environmental_never_stuck_even_when_repeated(tmp_path):
    sf = tmp_path / "state.json"
    _drop(tmp_path, 279, "implement", "environmental:delivery_failure")
    record_failure_signature(279, "implement", sf, tmp_path)
    _drop(tmp_path, 279, "implement", "environmental:delivery_failure")
    stuck, sig = record_failure_signature(279, "implement", sf, tmp_path)
    assert stuck is False
    assert sig == "environmental:delivery_failure"


def test_record_failure_signature_consumes_drop_file(tmp_path):
    sf = tmp_path / "state.json"
    _drop(tmp_path, 1, "plan", "substantive:unknown:1")
    record_failure_signature(1, "plan", sf, tmp_path)
    assert not (tmp_path / "error-signatures" / "1.plan.sig").exists()


def test_record_failure_signature_respects_phase_key_naming(tmp_path):
    # implement uses the bare issue number key; plan/refine/resolve use "<issue>:<phase>".
    sf = tmp_path / "state.json"
    _drop(tmp_path, 5, "implement", "substantive:test_failure:1")
    record_failure_signature(5, "implement", sf, tmp_path)
    data = json.loads(sf.read_text())
    assert "5:sig" in data
    assert "5:implement:sig" not in data

    _drop(tmp_path, 5, "plan", "substantive:test_failure:1")
    record_failure_signature(5, "plan", sf, tmp_path)
    data = json.loads(sf.read_text())
    assert "5:plan:sig" in data


def test_record_failure_signature_does_not_disturb_retry_count(tmp_path):
    sf = tmp_path / "state.json"
    increment_retry("5:plan", sf)
    _drop(tmp_path, 5, "plan", "substantive:test_failure:1")
    record_failure_signature(5, "plan", sf, tmp_path)
    assert get_retry_count("5:plan", sf) == 1
```

### Step 4.2 — verify the tests fail

```bash
cd /workspace/dark-factory && python -m pytest tests/test_factory_core_breaker.py -v -k signature
```
Expected: `ImportError: cannot import name 'record_failure_signature'`.

### Step 4.3 — implement

In `scripts/factory_core/breaker.py`, add after `_make_key` (after line 57):

```python
def _read_state(state_file: Path) -> dict:
    if not state_file.exists():
        return {}
    try:
        return json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_signature_key(key: str, value: str, state_file: Path) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    data = _read_state(state_file)
    data[f"{key}:sig"] = value
    _atomic_write(state_file, data)


def record_failure_signature(
    issue_num: int,
    phase: str,
    state_file: Path = _DEFAULT_STATE,
    state_dir: Path = None,
) -> tuple:
    """Reads and consumes the drop file the container wrote via error-signature-write,
    always updates the stored signature for this issue+phase (regardless of class, so a
    later substantive repeat still compares against the right prior value), and returns
    (stuck, signature). stuck is True only when both the newly-read and previously-stored
    signature carry the "substantive:" prefix and match exactly.

    Naming note for conformance review: the spec's Requirement 5 / Brainstorming Q&A refers
    to this stored value as "last_error_signature" (one new field on scheduler-state.json,
    not a new file). This implementation stores it as a "<issue_key>:sig" entry in the same
    flat dict scheduler-state.json already is (e.g. "42:sig", "42:plan:sig") rather than a
    literal field named last_error_signature — semantically identical (one new per-key entry
    on the existing single-writer state surface; see _make_key's existing "<issue>[:phase]"
    convention, which every other key in this file already follows), just named to match the
    file's existing flat-key-per-issue+phase shape instead of introducing a differently-shaped
    nested field. Not a deviation from Requirement 5.
    """
    if state_dir is None:
        state_dir = Path(os.environ.get("SCHEDULER_STATE_DIR", "/var/lib/dark-factory"))
    drop_file = Path(state_dir) / "error-signatures" / f"{issue_num}.{phase}.sig"
    if not drop_file.exists():
        return False, ""
    try:
        new_sig = json.loads(drop_file.read_text()).get("signature", "")
    except (json.JSONDecodeError, OSError):
        new_sig = ""
    try:
        drop_file.unlink()
    except OSError:
        pass
    if not new_sig:
        return False, ""

    key = _make_key(issue_num, phase)
    prev_sig = str(_read_state(state_file).get(f"{key}:sig", ""))
    _write_signature_key(key, new_sig, state_file)

    stuck = new_sig.startswith("substantive:") and prev_sig == new_sig
    return stuck, new_sig
```

### Step 4.4 — verify the tests pass

```bash
cd /workspace/dark-factory && python -m pytest tests/test_factory_core_breaker.py -v
```
Expected: all tests pass (existing + new).

### Step 4.5 — re-run Task 2's CLI test now that `record_failure_signature` exists

```bash
cd /workspace/dark-factory && python -m pytest tests/test_factory_core_error_signature.py -v
```
Expected: all pass (the `breaker-check-signature` subcommand's import now resolves).

### Step 4.6 — commit

```bash
git add scripts/factory_core/breaker.py tests/test_factory_core_breaker.py
git commit -m "feat: add breaker.record_failure_signature for early circuit-break (#33)"
```

---

## Task 5: `scheduler.sh` — wire the early trip into all 4 retry call sites

**Files:** `scheduler.sh`, `tests/test_scheduler.sh`

### Step 5.1 — write the failing tests

Add a new section to `tests/test_scheduler.sh`, immediately after section B (`trip_to_blocked`,
after line 131, before section C's `# ==========` header):

```bash
# ==========================================
# B2: check_failure_signature — early trip on 2nd consecutive substantive match
# ==========================================
echo ""
echo "--- B2: check_failure_signature early trip ---"
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"

_drop_sig() {
  local issue="$1" phase="$2" sig="$3"
  mkdir -p "${SCHEDULER_STATE_DIR}/error-signatures"
  printf '{"signature":"%s","phase":"%s","exit_code":1}' "$sig" "$phase" \
    > "${SCHEDULER_STATE_DIR}/error-signatures/${issue}.${phase}.sig"
}

_drop_sig 50 implement "substantive:test_failure:1"
RESULT1=$(check_failure_signature "50" "implement")
assert_eq "1st substantive match: not stuck" "1" "$(echo "$RESULT1" | grep -c 'stuck=false')"

_drop_sig 50 implement "substantive:test_failure:1"
RESULT2=$(check_failure_signature "50" "implement")
assert_eq "2nd consecutive substantive match: stuck" "1" "$(echo "$RESULT2" | grep -c 'stuck=true')"
assert_eq "stuck result carries the signature" "1" \
  "$(echo "$RESULT2" | grep -c 'sig=substantive:test_failure:1')"

echo '{}' > "$STATE_FILE"
_drop_sig 51 implement "environmental:delivery_failure"
check_failure_signature "51" "implement" > /dev/null
_drop_sig 51 implement "environmental:delivery_failure"
RESULT3=$(check_failure_signature "51" "implement")
assert_eq "environmental repeat never trips (mirrors #279)" "1" \
  "$(echo "$RESULT3" | grep -c 'stuck=false')"

echo '{}' > "$STATE_FILE"; > "$STUB_LOG"

# K10: refine/plan/resolve call sites early-trip via trip_to_blocked, bypassing MAX_RETRIES
_drop_sig 52 resolve "substantive:build_failure:1"
check_failure_signature "52" "resolve" > /dev/null
_drop_sig 52 resolve "substantive:build_failure:1"

SIG_RESULT=$(check_failure_signature "52" "resolve")
if echo "$SIG_RESULT" | grep -q "stuck=true"; then
  SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
  trip_to_blocked "52" "resolve" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
fi
assert_eq "K10: early trip delegates to breaker-trip (resolve)" \
  "1" "$(grep -c 'breaker-trip --issue 52 --phase resolve' "$STUB_LOG" || echo 0)"
assert_eq "K10: reason string embeds the signature" \
  "1" "$(grep -c \"same failure signature 'substantive:build_failure:1'\" "$STUB_LOG" || echo 0)"

> "$STUB_LOG"
```

### Step 5.2 — verify the test fails

```bash
cd /workspace/dark-factory && bash tests/test_scheduler.sh 2>&1 | grep -A2 "B2:"
```
Expected: `check_failure_signature: command not found`.

### Step 5.3 — implement: `check_failure_signature()` adapter

In `scheduler.sh`, add right after `trip_to_blocked()` (after line 481):

```bash
# --- Early circuit-break: repeated substantive failure signature (thin adapter) ---
# Reads and consumes the drop file the container wrote via error-signature-write, updates
# the stored last_error_signature in scheduler-state.json, and reports whether this is the
# 2nd consecutive SUBSTANTIVE match — logic lives in factory_core/breaker.py's
# record_failure_signature(), which never reports "stuck" for an environmental: signature.
# Usage: SIG_RESULT=$(check_failure_signature <issue_num> <phase>) — echoes "stuck=true|false sig=<sig-or-empty>"
check_failure_signature() {
  local issue_num="$1" phase="$2"
  STATE_FILE="$STATE_FILE" SCHEDULER_STATE_DIR="$SCHEDULER_STATE_DIR" python3 "$FACTORY_CORE_CLI" \
    breaker-check-signature --issue "$issue_num" --phase "$phase"
}
```

### Step 5.4 — wire into the 4 call sites

**Site 1 — conflict resolution (currently lines 1009–1013):**

```bash
      SIG_RESULT=$(check_failure_signature "$ISSUE" "resolve")
      if echo "$SIG_RESULT" | grep -q "stuck=true"; then
        SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
        trip_to_blocked "$ISSUE" "resolve" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
        continue
      fi

      RETRIES=$(get_retry_count "${ISSUE}:resolve")
      if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
        trip_to_blocked "$ISSUE" "resolve" "retry limit of ${MAX_RETRIES} reached for conflict resolution"
        continue
      fi
```

**Site 2 — blocked/implement retry (currently lines 1134–1138):**

```bash
      SIG_RESULT=$(check_failure_signature "$ISSUE" "implement")
      if echo "$SIG_RESULT" | grep -q "stuck=true"; then
        SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
        trip_to_blocked "$ISSUE" "implement" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
        continue
      fi

      RETRIES=$(get_retry_count "$ISSUE")
      if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
        trip_to_blocked "$ISSUE" "implement" "retry limit of ${MAX_RETRIES} reached"
        continue
      fi
```

**Site 3 — refined/plan retry (currently lines 1177–1181):**

```bash
      SIG_RESULT=$(check_failure_signature "$ISSUE" "plan")
      if echo "$SIG_RESULT" | grep -q "stuck=true"; then
        SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
        trip_to_blocked "$ISSUE" "plan" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
        continue
      fi

      RETRIES=$(get_retry_count "${ISSUE}:plan")
      if [ "$RETRIES" -ge "$REFINE_MAX_RETRIES" ]; then
        trip_to_blocked "$ISSUE" "plan" "retry limit of ${REFINE_MAX_RETRIES} reached"
        continue
      fi
```

**Site 4 — backlog/refine retry (currently lines 1219–1223):**

```bash
      SIG_RESULT=$(check_failure_signature "$ISSUE" "refine")
      if echo "$SIG_RESULT" | grep -q "stuck=true"; then
        SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
        trip_to_blocked "$ISSUE" "refine" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
        continue
      fi

      RETRIES=$(get_retry_count "${ISSUE}:refine")
      if [ "$RETRIES" -ge "$REFINE_MAX_RETRIES" ]; then
        trip_to_blocked "$ISSUE" "refine" "retry limit of ${REFINE_MAX_RETRIES} reached"
        continue
      fi
```

### Step 5.5 — verify the tests pass

```bash
cd /workspace/dark-factory && bash tests/test_scheduler.sh
```
Expected: `Results: N passed, 0 failed` (all existing sections plus B2/K10).

### Step 5.6 — regression-check the other scheduler test files

```bash
cd /workspace/dark-factory && for f in tests/test_scheduler_ceiling.sh tests/test_scheduler_autopilot_guard.sh tests/test_scheduler_main_red_fixer.sh tests/test_scheduler_pagination.sh tests/test_session_window_gate.sh; do bash "$f" || echo "FAILED: $f"; done
```
Expected: all pass — the new call sites only add checks before existing logic, they don't change
existing return values or side effects for the "not stuck" path.

### Step 5.7 — commit

```bash
git add scheduler.sh tests/test_scheduler.sh
git commit -m "feat: early-trip scheduler retry on repeated substantive failure signature (#33)"
```

---

## Task 6: End-to-end sanity check across the container/scheduler boundary

**Files:** none (verification only)

### Step 6.1 — full test suite

```bash
cd /workspace/dark-factory && python -m pytest tests/ -v
```
Expected: all pass, including Tasks 1–5's new/modified tests.

### Step 6.2 — full bash test suite

```bash
cd /workspace/dark-factory && for f in tests/test_*.sh; do echo "=== $f ==="; bash "$f" || echo "FAILED: $f"; done
```
Expected: all pass.

### Step 6.3 — manual signature round-trip (no scheduler/container needed)

```bash
cd /workspace/dark-factory
TMPDIR=$(mktemp -d)
STATE_FILE="$TMPDIR/state.json" python3 scripts/factory_core/cli.py error-signature-write \
  --issue 999 --phase implement --exit-code 1 --text-file /dev/null \
  --elapsed-seconds 5 --commits-since-start 0 --state-dir "$TMPDIR"
STATE_FILE="$TMPDIR/state.json" SCHEDULER_STATE_DIR="$TMPDIR" python3 scripts/factory_core/cli.py \
  breaker-check-signature --issue 999 --phase implement
```
Expected final line: `stuck=false sig=environmental:delivery_failure` (first observation, and
environmental never trips even on a repeat — confirmed by tests, this is a smoke check only).

No commit for this task (verification only).

---

## Task 7: 3 prompt-hardening blocks in `commands/dark-factory-implement.md`

**Files:** `commands/dark-factory-implement.md`

This task has no automated test — it is prompt text for a Claude agent, not executable code.
Verification is: (a) the file still parses as valid Archon command markdown (frontmatter intact),
(b) `bash smoke_gate.sh` still passes (it does not lint prompt content but confirms nothing else
broke), (c) a manual diff review confirming exactly 3 new, non-overlapping `###` blocks were added
and no existing text was removed.

### Step 7.1 — Pre-commit self-review (insert immediately before `### PHASE_3_CHECKPOINT`, i.e.
after the existing "If the change requires a new SQLAlchemy model" block, currently ending at
line 145, before line 147's `### PHASE_3_CHECKPOINT`):

```markdown
### Pre-commit self-review

Before the checkpoint below, scan your own diff for shipped-by-accident debt:

```bash
git diff main...HEAD -- . ':(exclude)*.md' > /tmp/self-review.diff
grep -nE '^\+.*\b(TODO|FIXME|XXX)\b' /tmp/self-review.diff || echo "no TODO/FIXME/XXX markers"
grep -nE '^\+.*\b(print\(|console\.log\(|breakpoint\(\)|pdb\.set_trace\(\))' /tmp/self-review.diff || echo "no shipped debug prints"
```

For each hit **introduced by this run** (a `+` line, not context): fix it before committing. For a
pre-existing hit you did not introduce, record it in `$ARTIFACTS_DIR/out-of-scope.md` per Scope
Discipline above — do not fix it inline.

Also check, for every non-doc file you changed: does at least one touched test file cover it? If a
changed source path has zero corresponding test changes and isn't pure plumbing (config wiring,
`__init__.py` exports), add a test before committing or note the gap in `implementation.md`.

Functions grown past ~60 lines by this run's changes are a signal to reconsider decomposition — not
a hard rule; note the trade-off in `implementation.md` if you keep one long.
```

### Step 7.2 — If you cannot pass (insert immediately after the block from Step 7.1, still before
`### PHASE_3_CHECKPOINT`):

```markdown
### If you cannot pass (blocked exit)

If Phase 3 cannot reach a green state (tests still failing, a gate you can't satisfy, an
environment you can't unblock) and you are about to end the run without shipping: before ending the
turn, write a one-paragraph first-guess diagnosis to `$ARTIFACTS_DIR/failure-diagnosis.md`:

```markdown
## Failure diagnosis — issue #$ISSUE_NUM

**Most likely cause:** <one sentence>
**Failing command:** `<the exact command>`
**Last ~15 lines of output:**
```
<paste>
```
**Smallest next step:** <one sentence — what the next `continue` run should try first>
```

Then post the same content as an issue comment (`gh issue comment $ISSUE_NUM --body-file
$ARTIFACTS_DIR/failure-diagnosis.md`) before the turn ends, so a future `continue` run picks it up
through the existing comment-digest pipeline (referenced above in Phase 1). This is a best-effort
diagnosis, not a guarantee — state your confidence plainly rather than asserting a root cause you
have not confirmed.
```

### Step 7.3 — Report discipline (append to the very end of Phase 6, after the existing bullet list
that currently ends the file at line 386):

```markdown

### Report discipline

Keep a green-path report to the 4 bullets above (files, tests, migrations, decisions) — no restated
issue text, no process narration ("first I explored...", "then I decided..."), and no questions per
`CLAUDE.md`'s "never end your turn on a question" rule; this run is headless.

If anything went sideways, surface it prominently at the **top** of `implementation.md`, before the
4 standard bullets: entries in `$ARTIFACTS_DIR/out-of-scope.md`, unresolved reservations about the
approach taken, and the contents of `$ARTIFACTS_DIR/failure-diagnosis.md` if one was written.
```

### Step 7.4 — verify

```bash
cd /workspace/dark-factory
head -5 commands/dark-factory-implement.md   # frontmatter intact
grep -c '^### ' commands/dark-factory-implement.md   # 3 higher than before
bash smoke_gate.sh
```

### Step 7.5 — commit

```bash
git add commands/dark-factory-implement.md
git commit -m "feat: add pre-commit self-review, failure-diagnosis, and report-discipline blocks to implement prompt (#33)"
```

---

## Task 8: Final verification and requirements sign-off

**Files:** none

### Step 8.1 — run the full suite one more time after all commits

```bash
cd /workspace/dark-factory
python -m pytest tests/ -v
for f in tests/test_*.sh; do bash "$f" || echo "FAILED: $f"; done
bash smoke_gate.sh
```

### Step 8.2 — confirm every requirement has a landed change

- Requirement 1 (17-recipe disposition): spec §4 — no code, already on branch.
- Requirement 2 (persistent-daemon evaluation documented): spec §1 — no code, already on branch.
- Requirement 3 (3 prompt-hardening blocks): Task 7 — `git diff main...HEAD -- commands/dark-factory-implement.md`.
- Requirement 4 (self-interruption + environmental carve-out): Tasks 1–6 — confirm with
  `git diff main...HEAD --stat` that `error_signature.py`, `cli.py`, `breaker.py`, `entrypoint.sh`,
  `scheduler.sh` all appear.
- Requirement 5 (minimum state, no new file): confirm `scheduler-state.json`'s shape is
  unchanged except for new `"<key>:sig"` entries (Task 4) — no new top-level state file was added.
- Requirement 6 (spec committed): already true.

No commit for this task (verification only).
