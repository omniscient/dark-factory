# Plan: Scheduler session-window-aware backoff

**Issue:** #35
**Spec:** `docs/superpowers/specs/2026-07-13-scheduler-session-window-backoff-design.md`
**Status:** plan

## Goal

When a dispatched run's `claude -p` call fails because the shared Claude Max 5-hour OAuth
session window is exhausted, the container must stop burning that ticket's retry budget
and instead: (1) give up immediately, (2) publish a shared, self-clearing pause sentinel
the scheduler can see, and (3) exit clean (`exit 0`, bypassing `on_failure`/breaker/board
changes). The scheduler must then skip all dispatch (implement, resolve, plan, refine,
autopilot) until the sentinel's embedded resume epoch passes, at which point it
self-clears with no active recheck dispatch needed.

## Architecture

```
entrypoint.sh (in-container, per dispatched run)
  while true; do
    archon workflow run ... → EXIT_CODE
    if EXIT_CODE != 0:
      _handle_session_window_pause "$TMP_OUT"     # NEW — calls into Python for parsing
        → python3 cli.py session-window-check      # NEW subcommand
            → factory_core/session_window.py        # NEW module (pure, pytest-tested)
                - detect structured `claude.rate_limit_event` JSON line (preferred)
                - else fall back to substring/regex reset-time parse (existing pattern)
                - compute resume_epoch = parsed_reset + buffer_minutes,
                  or now + fallback_minutes if unparseable
                - atomically write ${SCHEDULER_STATE_DIR}/session-window-paused
        → prints "matched=true resume_epoch=<N>" or "matched=false resume_epoch=0"
      if matched: run-record (stage=paused) + exit 0   # bypasses on_failure entirely
      else: kill-switch-off fallback → OLD sleep loop, or fall through to on_failure

scheduler.sh (poll loop, once per cycle)
  read ${SCHEDULER_STATE_DIR}/session-window-paused → SESSION_WINDOW_PAUSED=true/false
  (self-clears once now >= embedded resume epoch — mirrors MAIN_IS_RED sentinel pattern)
  gate Priority 1.5 (resolve), 2 (implement/ready), 3 (implement/blocked-retry),
       4 (plan), 5 (refine), 6 (autopilot) on SESSION_WINDOW_PAUSED
```

## Tech Stack

- Bash (`entrypoint.sh`, `scheduler.sh`) — orchestration, gating, sentinel I/O
- Python 3 (`scripts/factory_core/session_window.py`) — pure, pytest-testable parsing/math
- `config/config.yaml` + `.claude/skills/refinement/config.yaml` — policy knobs (kept in
  sync; `effective_config.py` layers the clone copy over the baked copy)

**Build constraint (carried from spec):** `entrypoint.sh`, `scheduler.sh`, and
`scripts/factory_core/` are baked into the image (`Dockerfile` `COPY` at lines 112-115,
127). This plan's changes require `docker compose build` + a redeploy of the running
scheduler/run image before they take effect in a live instance — editing the clone alone
does not change runtime behavior. This is a deploy-time note only; no `deploy/` files are
touched by this plan.

## File Structure

| File | Change |
|---|---|
| `config/config.yaml` | Add 3 keys under `scheduler:` |
| `.claude/skills/refinement/config.yaml` | Add the same 3 keys (kept in sync with baked copy) |
| `scripts/factory_core/session_window.py` | **New** — pure detection/parsing/sentinel-write module |
| `tests/test_factory_core_session_window.py` | **New** — pytest unit tests |
| `scripts/factory_core/cli.py` | Add `session-window-check` subcommand |
| `entrypoint.sh` | Bootstrap defaults, `_entrypoint_cfg_apply` entries, new `_handle_session_window_pause()`, rewire the retry `while` loop's matched branch |
| `tests/test_entrypoint_session_window.sh` | **New** — sources entrypoint.sh, exercises `_handle_session_window_pause()` |
| `scheduler.sh` | Sentinel read/self-clear block, gate 5 dispatch blocks + autopilot check |
| `tests/test_session_window_gate.sh` | **New** — static gate-wiring checks + sentinel self-clear behavior |

---

## Task 1: Config keys

**Files:** `config/config.yaml`, `.claude/skills/refinement/config.yaml`

No TDD cycle for pure config (no executable behavior yet) — verified by Task 4/5's tests
reading these keys via `yq`/`_epcfg`.

1. In `config/config.yaml`, add after the `blocked_rescue_enabled` line (currently line 9,
   inside the `scheduler:` block):

```yaml
  session_window_backoff_enabled: true   # env: SESSION_WINDOW_BACKOFF_ENABLED overrides — false = old sleep-forever fallback
  session_window_buffer_minutes: 5       # added to a parsed resetsAt; env: SESSION_WINDOW_BUFFER_MINUTES overrides
  session_window_fallback_minutes: 30    # pause length when the reset time can't be parsed; env: SESSION_WINDOW_FALLBACK_MINUTES overrides
```

2. In `.claude/skills/refinement/config.yaml`, add the same 3 keys (no comments, matching
   that file's existing style) after `blocked_rescue_enabled: true`:

```yaml
  session_window_backoff_enabled: true
  session_window_buffer_minutes: 5
  session_window_fallback_minutes: 30
```

3. Verify both files still parse:

```bash
python3 -c "import yaml; yaml.safe_load(open('config/config.yaml'))" && echo OK
python3 -c "import yaml; yaml.safe_load(open('.claude/skills/refinement/config.yaml'))" && echo OK
```
Expected output: `OK` twice.

4. Commit:

```bash
git add config/config.yaml .claude/skills/refinement/config.yaml
git commit -m "config: add session_window_backoff keys (#35)"
```

---

## Task 2: `session_window.py` — pure detection/parsing module

**Files:** `scripts/factory_core/session_window.py`, `tests/test_factory_core_session_window.py`

### Step 2.1 — write the failing test file

Create `tests/test_factory_core_session_window.py`:

```python
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core.session_window import (
    is_session_window_failure,
    parse_structured_reset_epoch,
    parse_fallback_reset_epoch,
    compute_resume_epoch,
    write_pause_sentinel,
    check_and_pause,
)


def test_is_session_window_failure_detects_structured_signal():
    text = '{"event":"claude.rate_limit_event","resetsAt":"2026-07-13T23:10:00Z"}'
    assert is_session_window_failure(text) is True


def test_is_session_window_failure_detects_substring_fallback():
    assert is_session_window_failure("Error: you've hit your USAGE LIMIT") is True


def test_is_session_window_failure_false_when_no_signal():
    assert is_session_window_failure("unrelated stack trace") is False


def test_parse_structured_reset_epoch_parses_resetsAt():
    text = ('noise\n{"event":"claude.rate_limit_event",'
            '"resetsAt":"2026-07-13T23:10:00Z"}\nmore noise')
    expected = int(datetime(2026, 7, 13, 23, 10, tzinfo=timezone.utc).timestamp())
    assert parse_structured_reset_epoch(text) == expected


def test_parse_structured_reset_epoch_none_when_absent():
    assert parse_structured_reset_epoch("no structured line here") is None


def test_parse_structured_reset_epoch_none_when_malformed_json():
    assert parse_structured_reset_epoch('{"event":"claude.rate_limit_event", broken') is None


def test_parse_fallback_reset_epoch_parses_human_readable_reset():
    now = int(datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc).timestamp())
    text = "You've hit your session limit · resets 11:10pm (UTC)"
    expected = int(datetime(2026, 7, 13, 23, 10, tzinfo=timezone.utc).timestamp())
    assert parse_fallback_reset_epoch(text, now) == expected


def test_parse_fallback_reset_epoch_rolls_over_to_next_day():
    now = int(datetime(2026, 7, 13, 23, 30, tzinfo=timezone.utc).timestamp())
    text = "resets 11:10pm (UTC)"
    expected = int(datetime(2026, 7, 14, 23, 10, tzinfo=timezone.utc).timestamp())
    assert parse_fallback_reset_epoch(text, now) == expected


def test_parse_fallback_reset_epoch_none_when_unparseable():
    assert parse_fallback_reset_epoch("session limit hit, try later", 0) is None


def test_parse_fallback_reset_epoch_none_for_unknown_timezone():
    assert parse_fallback_reset_epoch("resets 11:10pm (Nowhere/Fake)", 0) is None


def test_compute_resume_epoch_prefers_structured_over_fallback():
    now = int(datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc).timestamp())
    text = ('{"event":"claude.rate_limit_event","resetsAt":"2026-07-13T23:10:00Z"}\n'
            'resets 11:00pm (UTC)')
    expected = int(datetime(2026, 7, 13, 23, 10, tzinfo=timezone.utc).timestamp()) + 300
    assert compute_resume_epoch(text, now, buffer_minutes=5, fallback_minutes=30) == expected


def test_compute_resume_epoch_uses_regex_fallback_when_no_structured():
    now = int(datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc).timestamp())
    text = "session limit reached · resets 11:10pm (UTC)"
    expected = int(datetime(2026, 7, 13, 23, 10, tzinfo=timezone.utc).timestamp()) + 300
    assert compute_resume_epoch(text, now, buffer_minutes=5, fallback_minutes=30) == expected


def test_compute_resume_epoch_uses_fallback_minutes_when_unparseable():
    now = 1_000_000
    text = "429 too many requests, session limit reached"
    assert compute_resume_epoch(text, now, 5, 30) == now + 30 * 60


def test_compute_resume_epoch_none_when_no_match():
    assert compute_resume_epoch("unrelated error", 0, 5, 30) is None


def test_write_pause_sentinel_atomic(tmp_path):
    write_pause_sentinel(123456, tmp_path)
    sentinel = tmp_path / "session-window-paused"
    assert sentinel.read_text() == "123456"
    assert not (tmp_path / "session-window-paused.tmp").exists()


def test_write_pause_sentinel_creates_state_dir(tmp_path):
    nested = tmp_path / "nested" / "state"
    write_pause_sentinel(1, nested)
    assert (nested / "session-window-paused").read_text() == "1"


def test_check_and_pause_writes_sentinel_and_returns_epoch(tmp_path):
    text = "429 rate limit hit"
    epoch = check_and_pause(text, tmp_path, now_epoch=1_000_000,
                             buffer_minutes=5, fallback_minutes=30)
    assert epoch == 1_000_000 + 1800
    assert (tmp_path / "session-window-paused").read_text() == str(epoch)


def test_check_and_pause_returns_none_and_writes_nothing_when_no_match(tmp_path):
    epoch = check_and_pause("unrelated", tmp_path, 1_000_000, 5, 30)
    assert epoch is None
    assert not (tmp_path / "session-window-paused").exists()
```

### Step 2.2 — verify it fails

```bash
python -m pytest tests/test_factory_core_session_window.py -v
```
Expected output: `ModuleNotFoundError: No module named 'factory_core.session_window'` (or
`ImportError`) — collection error, 0 passed.

### Step 2.3 — implement

Create `scripts/factory_core/session_window.py`:

```python
"""Session-window-aware backoff (#35): detect a Claude Max 5h session-window exhaustion
in a run's captured stdout, compute a resume epoch, and write the shared pause sentinel
scheduler.sh gates dispatch on.
"""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_SUBSTRING_RE = re.compile(
    r"usage limit|rate limit|429|credit balance|session limit", re.IGNORECASE
)
_STRUCTURED_MARKER = "claude.rate_limit_event"
_HUMAN_RESET_RE = re.compile(
    r"resets\s+([0-9]{1,2}:[0-9]{2}[ap]m)\s*\(([^)]+)\)", re.IGNORECASE
)


def is_session_window_failure(text: str) -> bool:
    return _STRUCTURED_MARKER in text or bool(_SUBSTRING_RE.search(text))


def parse_structured_reset_epoch(text: str) -> Optional[int]:
    for line in text.splitlines():
        if _STRUCTURED_MARKER not in line:
            continue
        match = re.search(r"\{.*\}", line)
        if not match:
            continue
        try:
            event = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        resets_at = event.get("resetsAt")
        if not resets_at:
            continue
        try:
            dt = datetime.fromisoformat(str(resets_at).replace("Z", "+00:00"))
        except ValueError:
            continue
        return int(dt.timestamp())
    return None


def parse_fallback_reset_epoch(text: str, now_epoch: int) -> Optional[int]:
    match = _HUMAN_RESET_RE.search(text)
    if not match:
        return None
    time_str, tz_name = match.group(1), match.group(2)
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    try:
        parsed_time = datetime.strptime(time_str.upper(), "%I:%M%p").time()
    except ValueError:
        return None
    now_dt = datetime.fromtimestamp(now_epoch, tz)
    candidate = datetime.combine(now_dt.date(), parsed_time, tzinfo=tz)
    if candidate.timestamp() < now_epoch:
        candidate += timedelta(days=1)
    return int(candidate.timestamp())


def compute_resume_epoch(
    text: str, now_epoch: int, buffer_minutes: int, fallback_minutes: int
) -> Optional[int]:
    if not is_session_window_failure(text):
        return None
    structured = parse_structured_reset_epoch(text)
    if structured is not None:
        return structured + buffer_minutes * 60
    fallback = parse_fallback_reset_epoch(text, now_epoch)
    if fallback is not None:
        return fallback + buffer_minutes * 60
    return now_epoch + fallback_minutes * 60


def write_pause_sentinel(resume_epoch: int, state_dir: Path) -> None:
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "session-window-paused"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(str(resume_epoch))
    tmp.rename(path)


def check_and_pause(
    text: str,
    state_dir: Path,
    now_epoch: int,
    buffer_minutes: int,
    fallback_minutes: int,
) -> Optional[int]:
    resume_epoch = compute_resume_epoch(text, now_epoch, buffer_minutes, fallback_minutes)
    if resume_epoch is not None:
        write_pause_sentinel(resume_epoch, state_dir)
    return resume_epoch
```

### Step 2.4 — verify it passes

```bash
python -m pytest tests/test_factory_core_session_window.py -v
```
Expected output: `18 passed`.

### Step 2.5 — commit

```bash
git add scripts/factory_core/session_window.py tests/test_factory_core_session_window.py
git commit -m "feat(factory-core): add session-window detection/parsing module (#35)"
```

---

## Task 3: Wire `session-window-check` into `cli.py`

**Files:** `scripts/factory_core/cli.py`, `tests/test_factory_core_session_window.py`

### Step 3.1 — write the failing test (append to the same test file)

Append to `tests/test_factory_core_session_window.py`:

```python
import subprocess
import sys as _sys


def test_cli_session_window_check_matched(tmp_path):
    tmp_out = tmp_path / "run.out"
    tmp_out.write_text("429 too many requests, session limit reached")
    state_dir = tmp_path / "state"
    result = subprocess.run(
        [_sys.executable,
         str(Path(__file__).resolve().parents[1] / "scripts" / "factory_core" / "cli.py"),
         "session-window-check",
         "--tmp-out", str(tmp_out),
         "--state-dir", str(state_dir),
         "--buffer-minutes", "5",
         "--fallback-minutes", "30"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "matched=true" in result.stdout
    assert (state_dir / "session-window-paused").exists()


def test_cli_session_window_check_unmatched(tmp_path):
    tmp_out = tmp_path / "run.out"
    tmp_out.write_text("unrelated stack trace")
    state_dir = tmp_path / "state"
    result = subprocess.run(
        [_sys.executable,
         str(Path(__file__).resolve().parents[1] / "scripts" / "factory_core" / "cli.py"),
         "session-window-check",
         "--tmp-out", str(tmp_out),
         "--state-dir", str(state_dir),
         "--buffer-minutes", "5",
         "--fallback-minutes", "30"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "matched=false" in result.stdout
    assert not (state_dir / "session-window-paused").exists()
```

### Step 3.2 — verify it fails

```bash
python -m pytest tests/test_factory_core_session_window.py -k cli_session_window -v
```
Expected output: `argument cmd: invalid choice: 'session-window-check'` — 2 failed.

### Step 3.3 — implement

In `scripts/factory_core/cli.py`, add a handler function after `_rescue_blocked` (before `def main():`):

```python
def _session_window_check(args):
    import time
    from factory_core.session_window import check_and_pause
    tmp_out_path = Path(args.tmp_out)
    text = tmp_out_path.read_text(errors="replace") if tmp_out_path.exists() else ""
    resume_epoch = check_and_pause(
        text,
        Path(args.state_dir),
        now_epoch=int(time.time()),
        buffer_minutes=args.buffer_minutes,
        fallback_minutes=args.fallback_minutes,
    )
    if resume_epoch is not None:
        print(f"matched=true resume_epoch={resume_epoch}")
    else:
        print("matched=false resume_epoch=0")
```

In `main()`, add the subparser after `rb.set_defaults(func=_rescue_blocked)` and before
`parsed = parser.parse_args()`:

```python
    sw = sub.add_parser("session-window-check")
    sw.add_argument("--tmp-out", required=True)
    sw.add_argument("--state-dir", default="/var/lib/dark-factory")
    sw.add_argument("--buffer-minutes", type=int, default=5)
    sw.add_argument("--fallback-minutes", type=int, default=30)
    sw.set_defaults(func=_session_window_check)
```

### Step 3.4 — verify it passes

```bash
python -m pytest tests/test_factory_core_session_window.py -v
```
Expected output: `20 passed`.

### Step 3.5 — commit

```bash
git add scripts/factory_core/cli.py tests/test_factory_core_session_window.py
git commit -m "feat(factory-core): wire session-window-check CLI subcommand (#35)"
```

---

## Task 4: `entrypoint.sh` — detect, pause, exit clean

**Files:** `entrypoint.sh`, `tests/test_entrypoint_session_window.sh`

### Step 4.1 — write the failing test

Create `tests/test_entrypoint_session_window.sh`:

```bash
#!/usr/bin/env bash
# Verifies _handle_session_window_pause() (#35): a matched failure writes the sentinel
# with the correct resume epoch and returns 0 — the caller (the while-loop rewire in
# Step 4.3.4) uses that 0 to exit clean before ever reaching on_failure/run_post_mortem
# or the success-path record assembly, but that call ordering itself lives in the
# un-executable main retry loop and is verified by code review of Step 4.3.4, not by
# this test. An unmatched failure (or the kill-switch off) returns 1, signalling the
# caller to fall through to the normal failure/sleep path unchanged.
# Run: bash tests/test_entrypoint_session_window.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

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

ENTRYPOINT_SOURCE_ONLY=1 source "$SCRIPT_DIR/../entrypoint.sh"

trap - ERR
set +e; set +u; set +o pipefail

# _handle_session_window_pause resolves cli.py at "$CLONE_DIR/dark-factory/scripts/..."
# (the TARGET-PATH convention — see entrypoint.sh's existing on_failure/post_cost_report
# calls). REPO_ROOT's own basename is "dark-factory", so its parent's "dark-factory"
# child IS REPO_ROOT — this holds both in this sandbox (.../dark-factory) and under
# GitHub Actions' checkout layout (.../dark-factory/dark-factory), so the real
# branch cli.py (with this task's session-window-check subcommand) resolves correctly
# without any bootstrap/copy step.
CLONE_DIR="$(dirname "$REPO_ROOT")"
ISSUE_NUM=35
INTENT=fix
RUN_ID=test-run-1

PASSED=0; FAILED=0
assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else
    echo "  FAIL: $desc — expected='$expected' got='$actual'" >&2; FAILED=$((FAILED+1))
  fi
}
assert_true() {
  local desc="$1"; shift
  if eval "$1"; then assert_eq "$desc" "0" "0"; else assert_eq "$desc" "0" "1"; fi
}

echo "--- A: matched (structured rate_limit_event line) ---"
SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-sw-statedir-XXXXXX)
NOW=$(date -u +%s)
RESET_ISO=$(date -u -d "@$((NOW+600))" +%Y-%m-%dT%H:%M:%SZ)
TMP_OUT=$(mktemp /tmp/ep-sw-out-XXXXXX)
printf 'some claude output\n{"event":"claude.rate_limit_event","resetsAt":"%s"}\n' \
  "$RESET_ISO" > "$TMP_OUT"

SESSION_WINDOW_BACKOFF_ENABLED=true
SESSION_WINDOW_BUFFER_MINUTES=5
SESSION_WINDOW_FALLBACK_MINUTES=30
_handle_session_window_pause "$TMP_OUT"
RC=$?
assert_eq "matched → returns 0" "0" "$RC"
assert_true "sentinel written" "[ -f '${SCHEDULER_STATE_DIR}/session-window-paused' ]"
SENTINEL_EPOCH=$(cat "${SCHEDULER_STATE_DIR}/session-window-paused" 2>/dev/null || echo 0)
EXPECTED_EPOCH=$((NOW + 600 + 300))
DIFF=$((SENTINEL_EPOCH - EXPECTED_EPOCH)); DIFF=${DIFF#-}
assert_true "resume epoch within 2s of resetsAt+buffer" "[ '$DIFF' -le 2 ]"

echo ""
echo "--- B: unmatched (unrelated failure) — falls through to normal failure path ---"
rm -f "${SCHEDULER_STATE_DIR}/session-window-paused"
TMP_OUT2=$(mktemp /tmp/ep-sw-out2-XXXXXX)
echo "some unrelated stack trace" > "$TMP_OUT2"
_handle_session_window_pause "$TMP_OUT2"
RC2=$?
assert_eq "unmatched → returns 1" "1" "$RC2"
assert_true "no sentinel written for unmatched failure" \
  "[ ! -f '${SCHEDULER_STATE_DIR}/session-window-paused' ]"

echo ""
echo "--- C: kill-switch off — falls through even on a matched signal ---"
SESSION_WINDOW_BACKOFF_ENABLED=false
_handle_session_window_pause "$TMP_OUT"
RC3=$?
assert_eq "kill-switch off → returns 1" "1" "$RC3"
assert_true "no sentinel written when kill-switch off" \
  "[ ! -f '${SCHEDULER_STATE_DIR}/session-window-paused' ]"
SESSION_WINDOW_BACKOFF_ENABLED=true

rm -f "$TMP_OUT" "$TMP_OUT2"
rm -rf "$SCHEDULER_STATE_DIR"

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
```

### Step 4.2 — verify it fails

```bash
bash tests/test_entrypoint_session_window.sh
```
Expected output: `_handle_session_window_pause: command not found` — script exits non-zero.

### Step 4.3 — implement

**4.3.1** — bootstrap defaults. In `entrypoint.sh`, after line 28
(`CONFLICT_RESOLUTION_AI_TIER="${CONFLICT_RESOLUTION_AI_TIER:-true}"`):

```bash
SCHEDULER_STATE_DIR="${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}"
SESSION_WINDOW_BACKOFF_ENABLED="${SESSION_WINDOW_BACKOFF_ENABLED:-true}"
SESSION_WINDOW_BUFFER_MINUTES="${SESSION_WINDOW_BUFFER_MINUTES:-5}"
SESSION_WINDOW_FALLBACK_MINUTES="${SESSION_WINDOW_FALLBACK_MINUTES:-30}"
```

**4.3.2** — config resolution. In `_entrypoint_cfg_apply()`, after
`_epcfg CONFLICT_RESOLUTION_AI_TIER '.conflict_resolution.ai_tier'` (line 56):

```bash
  _epcfg SESSION_WINDOW_BACKOFF_ENABLED   '.scheduler.session_window_backoff_enabled'
  _epcfg SESSION_WINDOW_BUFFER_MINUTES    '.scheduler.session_window_buffer_minutes'
  _epcfg SESSION_WINDOW_FALLBACK_MINUTES  '.scheduler.session_window_fallback_minutes'
```

**4.3.3** — new function. After `run_post_mortem()`'s closing `}` (line 236), insert:

```bash

# Detects a session-window exhaustion in the captured run output via the Python
# session_window module (structured claude.rate_limit_event preferred, substring/regex
# fallback otherwise). On match: writes the shared pause sentinel, records a distinct
# "paused" run-record entry, and returns 0 so the caller exits clean WITHOUT flowing
# through on_failure or the success-path record assembly. Returns 1 (kill-switch off,
# or no match at all) so the caller falls through to the existing failure/sleep paths.
_handle_session_window_pause() {
  local tmp_out="$1"
  [ "${SESSION_WINDOW_BACKOFF_ENABLED:-true}" = "true" ] || return 1

  local sw_result
  # TARGET-PATH: cli.py resolves under dark-factory/ in the clone — target's own copy
  # until P3 cleanup, baked self-contained fallback copy afterwards (df#14)
  sw_result=$(python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" session-window-check \
    --tmp-out "$tmp_out" \
    --state-dir "${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}" \
    --buffer-minutes "${SESSION_WINDOW_BUFFER_MINUTES:-5}" \
    --fallback-minutes "${SESSION_WINDOW_FALLBACK_MINUTES:-30}" 2>/dev/null) || return 1

  local matched resume_epoch
  matched=$(echo "$sw_result" | grep -o 'matched=[a-z]*' | cut -d= -f2)
  resume_epoch=$(echo "$sw_result" | grep -o 'resume_epoch=[0-9]*' | cut -d= -f2)
  [ "$matched" = "true" ] || return 1

  local resume_iso
  resume_iso=$(date -u -d "@${resume_epoch}" +%FT%TZ 2>/dev/null || echo "unknown")
  echo "session-window exhausted — dispatch paused until ${resume_iso}"
  python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record record \
    --run-id "${RUN_ID:-unknown}" \
    --issue "${ISSUE_NUM:-0}" \
    --intent "${INTENT:-unknown}" \
    --stage paused \
    --verdict paused || true
  return 0
}
```

**4.3.4** — rewire the retry loop. Replace the matched branch of the existing `while
true` loop (the block starting `if grep -qiE "usage limit|rate limit|429|credit
balance|session limit" "$TMP_OUT"; then` through its `continue`):

```bash
  if [ "$EXIT_CODE" -ne 0 ]; then
    if _handle_session_window_pause "$TMP_OUT"; then
      rm -f "$TMP_OUT"
      exit 0
    fi
    if grep -qiE "usage limit|rate limit|429|credit balance|session limit" "$TMP_OUT"; then
      # Kill-switch fallback (SESSION_WINDOW_BACKOFF_ENABLED=false): old sleep-forever
      # behavior, unchanged.
      # Attempt to parse specific reset time from: "You've hit your session limit · resets 11:10pm (America/Toronto)"
      RESET_TIME=$(grep -ioP "resets\s+\K([0-9]{1,2}:[0-9]{2}[a-z]{2})" "$TMP_OUT" | head -1)
      RESET_TZ=$(grep -ioP "resets\s+[0-9]{1,2}:[0-9]{2}[a-z]{2}\s*\(\K([^)]+)" "$TMP_OUT" | head -1)

      SLEEP_SECS=300 # default to 5 mins if parsing fails
      if [ -n "$RESET_TIME" ]; then
        if [ -n "$RESET_TZ" ]; then
          TARGET_EPOCH=$(TZ="$RESET_TZ" date -d "$RESET_TIME" +%s 2>/dev/null || echo "")
        else
          TARGET_EPOCH=$(date -d "$RESET_TIME" +%s 2>/dev/null || echo "")
        fi

        if [ -n "$TARGET_EPOCH" ]; then
          NOW_EPOCH=$(date +%s)
          if [ "$TARGET_EPOCH" -lt "$NOW_EPOCH" ]; then
            TARGET_EPOCH=$((TARGET_EPOCH + 86400))
          fi
          SLEEP_SECS=$((TARGET_EPOCH - NOW_EPOCH + 60)) # Add 60s buffer to ensure it actually resets

          # Failsafe for absurd values (e.g., more than 24 hours or negative)
          if [ "$SLEEP_SECS" -lt 0 ] || [ "$SLEEP_SECS" -gt 90000 ]; then
            SLEEP_SECS=300
          fi
        fi
      fi

      echo "Claude Max subscription limit reached. Sleeping for ${SLEEP_SECS}s before retrying..."
      rm -f "$TMP_OUT"
      sleep "$SLEEP_SECS"
      echo "Waking up and retrying..."
      continue
    fi
    run_post_mortem "$EXIT_CODE" "$TMP_OUT" || true
    rm -f "$TMP_OUT"
    exit "$EXIT_CODE"
  fi
```

(Everything above and below this block — the `set +e`/`set -e` pair, the success-path
`break`, and lines 754-772's cost-report/run-record assembly — is unchanged.)

### Step 4.4 — verify it passes

```bash
bash tests/test_entrypoint_session_window.sh
```
Expected output: `Results: 7 passed, 0 failed`.

Also re-run the existing entrypoint tests to confirm no regression:
```bash
bash tests/test_entrypoint_fix_main.sh
bash tests/test_entrypoint_preflight.sh
bash tests/test_431_telemetry_isolation.sh
```
Expected output: `PASS` / `Results: N passed, 0 failed` for each, unchanged from before.

### Step 4.5 — commit

```bash
git add entrypoint.sh tests/test_entrypoint_session_window.sh
git commit -m "feat(entrypoint): pause dispatch on session-window exhaustion instead of sleeping in-container (#35)"
```

---

## Task 5: `scheduler.sh` — dispatch gate

**Files:** `scheduler.sh`, `tests/test_session_window_gate.sh`

### Step 5.1 — write the failing test

Create `tests/test_session_window_gate.sh`:

```bash
#!/usr/bin/env bash
# Verifies the session-window-paused sentinel (#35) gates all five dispatch priority
# blocks (resolve, implement/ready, implement/blocked-retry, plan, refine) and the
# autopilot check, and self-clears once its embedded epoch passes. Mirrors
# test_scheduler_main_red_fixer.sh / test_dispatch_ceiling.sh style: static wiring
# checks (the main loop can't be executed under test) plus a real-behavior check of
# the sentinel read/self-clear snippet in isolation.
# Run: bash tests/test_session_window_gate.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCHED="$SCRIPT_DIR/../scheduler.sh"

PASSED=0; FAILED=0
assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else
    echo "  FAIL: $desc — expected='$expected' got='$actual'" >&2; FAILED=$((FAILED+1))
  fi
}
assert_true() {
  local desc="$1"; shift
  if eval "$1"; then assert_eq "$desc" "0" "0"; else assert_eq "$desc" "0" "1"; fi
}

echo "--- A: static gate wiring ---"
grep -q 'SCHEDULER_STATE_DIR}/session-window-paused' "$SCHED" \
  || { echo "FAIL: no session-window-paused sentinel read"; exit 1; }
echo "  PASS: sentinel read present"; PASSED=$((PASSED+1))

for hdr in "Priority 1.5:" "Priority 2:" "Priority 3:" "Priority 4:" "Priority 5:"; do
  block="$(awk -v h="$hdr" 'index($0,h){f=1} f{print} f&&/^  fi$/{exit}' "$SCHED")"
  if echo "$block" | grep -q 'SESSION_WINDOW_PAUSED'; then
    echo "  PASS: '$hdr' block gated by SESSION_WINDOW_PAUSED"; PASSED=$((PASSED+1))
  else
    echo "  FAIL: '$hdr' block missing SESSION_WINDOW_PAUSED gate" >&2; FAILED=$((FAILED+1))
  fi
done

block6="$(awk '/Priority 6: Epic Autopilot/{f=1} f{print} f&&/^  fi$/{exit}' "$SCHED")"
if echo "$block6" | grep -q 'SESSION_WINDOW_PAUSED.*false'; then
  echo "  PASS: autopilot guarded by session-window-green"; PASSED=$((PASSED+1))
else
  echo "  FAIL: autopilot not guarded by session-window-green" >&2; FAILED=$((FAILED+1))
fi

# Structural proxy for "no breaker call site reachable while paused": the gate check
# must appear before any get_retry_count/increment_retry call within each block. Only
# blocks that actually call the breaker qualify — Priority 2 (Ready/implement) dispatches
# "Fix" directly with no get_retry_count/increment_retry call, so it is excluded here
# (its SESSION_WINDOW_PAUSED gate is still covered by the wiring loop above); Priority 1.5
# (resolve) does call the breaker (get_retry_count/increment_retry on the ":resolve" key)
# and is included instead. These 4 are exactly the spec's "4 call sites."
for hdr in "Priority 1.5:" "Priority 3:" "Priority 4:" "Priority 5:"; do
  block="$(awk -v h="$hdr" 'index($0,h){f=1} f{print} f&&/^  fi$/{exit}' "$SCHED")"
  gate_ln=$(echo "$block" | grep -n 'SESSION_WINDOW_PAUSED' | head -1 | cut -d: -f1)
  retry_ln=$(echo "$block" | grep -n 'get_retry_count\|increment_retry' | head -1 | cut -d: -f1)
  if [ -n "$gate_ln" ] && [ -n "$retry_ln" ] && [ "$gate_ln" -lt "$retry_ln" ]; then
    echo "  PASS: '$hdr' gate precedes breaker call sites"; PASSED=$((PASSED+1))
  else
    echo "  FAIL: '$hdr' gate ($gate_ln) does not precede breaker calls ($retry_ln)" >&2
    FAILED=$((FAILED+1))
  fi
done

echo ""
echo "--- B: sentinel self-clear behavior ---"
# Mirrors the snippet inserted after the MAIN_IS_RED read in scheduler.sh — exercised
# directly since it lives inside the un-executable main poll loop.
# NOTE: this mirror uses RESUME_EPOCH as a local var name; the real inserted code in
# Step 5.3.1 uses SW_RESUME_EPOCH (namespaced to avoid colliding with other scheduler.sh
# vars). This tests a faithful copy of the logic, not the shipped line — the same
# tradeoff test_scheduler_main_red_fixer.sh accepts for other un-executable main-loop
# code. If Step 5.3.1's snippet changes, update this mirror to match.
sw_gate_check() {
  SESSION_WINDOW_PAUSED=false
  if [ -f "${SCHEDULER_STATE_DIR}/session-window-paused" ]; then
    RESUME_EPOCH=$(cat "${SCHEDULER_STATE_DIR}/session-window-paused" 2>/dev/null || echo 0)
    if [ "$(date +%s)" -lt "${RESUME_EPOCH:-0}" ]; then
      SESSION_WINDOW_PAUSED=true
    else
      rm -f "${SCHEDULER_STATE_DIR}/session-window-paused"
    fi
  fi
}

SCHEDULER_STATE_DIR=$(mktemp -d /tmp/sched-sw-statedir-XXXXXX)

sw_gate_check
assert_eq "no sentinel → not paused" "false" "$SESSION_WINDOW_PAUSED"

FUTURE=$(( $(date +%s) + 3600 ))
echo "$FUTURE" > "${SCHEDULER_STATE_DIR}/session-window-paused"
sw_gate_check
assert_eq "future epoch → paused" "true" "$SESSION_WINDOW_PAUSED"
assert_true "sentinel kept while future" "[ -f '${SCHEDULER_STATE_DIR}/session-window-paused' ]"

PAST=$(( $(date +%s) - 10 ))
echo "$PAST" > "${SCHEDULER_STATE_DIR}/session-window-paused"
sw_gate_check
assert_eq "past epoch → resumed" "false" "$SESSION_WINDOW_PAUSED"
assert_true "sentinel removed once expired" "[ ! -f '${SCHEDULER_STATE_DIR}/session-window-paused' ]"

rm -rf "$SCHEDULER_STATE_DIR"

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
```

### Step 5.2 — verify it fails

```bash
bash tests/test_session_window_gate.sh
```
Expected output: `FAIL: no session-window-paused sentinel read` (script exits 1 immediately).

### Step 5.3 — implement

**5.3.1** — sentinel read/self-clear + gate variable. After the existing `MAIN_IS_RED`
block (lines 951-960, ending `fi`), insert:

```bash

  # --- Read session-window-paused sentinel (written by entrypoint.sh on a detected
  # Claude Max session-window exhaustion, #35) — self-clearing, no recheck dispatch
  # needed since the resume time is already known from the embedded epoch. ---
  SESSION_WINDOW_PAUSED=false
  if [ -f "${SCHEDULER_STATE_DIR}/session-window-paused" ]; then
    SW_RESUME_EPOCH=$(cat "${SCHEDULER_STATE_DIR}/session-window-paused" 2>/dev/null || echo 0)
    if [ "$(date +%s)" -lt "${SW_RESUME_EPOCH:-0}" ]; then
      SESSION_WINDOW_PAUSED=true
      SW_RESUME_ISO=$(date -u -d "@${SW_RESUME_EPOCH}" +%FT%TZ 2>/dev/null || echo "unknown")
      echo "[$(date -u +%FT%TZ)] session_window_gate=active resume_at=${SW_RESUME_ISO}"
    else
      rm -f "${SCHEDULER_STATE_DIR}/session-window-paused"
    fi
  fi
```

**5.3.2** — Priority 1.5 (resolve), currently:
```bash
  if [ "$MAIN_IS_RED" = "true" ]; then
    echo "[$(date -u +%FT%TZ)] main_red_gate=skip_deconflict"
  elif [ "${CONFLICT_RESOLUTION_ENABLED:-true}" = "true" ]; then
```
becomes:
```bash
  if [ "$MAIN_IS_RED" = "true" ] || [ "$SESSION_WINDOW_PAUSED" = "true" ]; then
    echo "[$(date -u +%FT%TZ)] main_red=${MAIN_IS_RED} session_window_paused=${SESSION_WINDOW_PAUSED} action=skip_deconflict"
  elif [ "${CONFLICT_RESOLUTION_ENABLED:-true}" = "true" ]; then
```

**5.3.3** — Priority 2 (Ready/implement), currently:
```bash
  if [ "$MAIN_IS_RED" = "true" ]; then
    echo "[$(date -u +%FT%TZ)] main_red_gate=skip_implement"
  else
```
becomes:
```bash
  if [ "$MAIN_IS_RED" = "true" ] || [ "$SESSION_WINDOW_PAUSED" = "true" ]; then
    echo "[$(date -u +%FT%TZ)] main_red=${MAIN_IS_RED} session_window_paused=${SESSION_WINDOW_PAUSED} action=skip_implement"
  else
```

**5.3.4** — Priority 3 (Blocked/implement-retry), currently:
```bash
  if [ "$MAIN_IS_RED" = "true" ]; then
    echo "[$(date -u +%FT%TZ)] main_red_gate=skip_blocked_retry"
  else
```
becomes:
```bash
  if [ "$MAIN_IS_RED" = "true" ] || [ "$SESSION_WINDOW_PAUSED" = "true" ]; then
    echo "[$(date -u +%FT%TZ)] main_red=${MAIN_IS_RED} session_window_paused=${SESSION_WINDOW_PAUSED} action=skip_blocked_retry"
  else
```

**5.3.5** — Priority 4 (Refined/plan). This block currently has no `MAIN_IS_RED` gate at
all (plan generation doesn't touch main). Wrap the existing `while IFS= read -r item; do
... done < <(echo "$REFINED" | jq -c '.[]')` loop:
```bash
  if [ "$SESSION_WINDOW_PAUSED" = "true" ]; then
    echo "[$(date -u +%FT%TZ)] session_window_paused=true action=skip_plan"
  else
    while IFS= read -r item; do
      [ -n "$DISPATCHED" ] && break
      ISSUE=$(get_issue_number "$item")

      # Direct-to-PR plan auto-advance: handle before refine_skip_label blocks plan-pending-review
      if echo "$item" | jq -r '.labels[]?' 2>/dev/null | grep -qi "plan-pending-review" \
         && has_direct_to_pr_label "$item"; then
        if ! is_issue_running "$ISSUE" && [ "$REFINE_RUNNING" -lt "$REFINE_WIP_LIMIT" ]; then
          plan_advance_check "$ISSUE" "$item"
        fi
        continue
      fi

      if has_refine_skip_label "$item"; then continue; fi
      if is_issue_running "$ISSUE"; then continue; fi
      if [ "$REFINE_RUNNING" -ge "$REFINE_WIP_LIMIT" ]; then break; fi

      RETRIES=$(get_retry_count "${ISSUE}:plan")
      if [ "$RETRIES" -ge "$REFINE_MAX_RETRIES" ]; then
        trip_to_blocked "$ISSUE" "plan" "retry limit of ${REFINE_MAX_RETRIES} reached"
        continue
      fi

      increment_retry "${ISSUE}:plan"
      gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body "📋 **Refinement Pipeline** — Starting plan generation and architect validation.

---
*Posted by ${FACTORY_PRODUCT_NAME} Backlog Scheduler*" 2>/dev/null || true
      if dispatch "Plan issue #${ISSUE}"; then
        DISPATCHED="Plan issue #${ISSUE}"
        REFINE_RUNNING=$((REFINE_RUNNING + 1))
      fi
    done < <(echo "$REFINED" | jq -c '.[]')
  fi
```
(the loop body itself is semantically unchanged, re-indented one level — only the
enclosing `if SESSION_WINDOW_PAUSED ... else ... fi` wrapper is new).

**5.3.6** — Priority 5 (Backlog/refine). Same pattern — wrap the existing loop:
```bash
  if [ "$SESSION_WINDOW_PAUSED" = "true" ]; then
    echo "[$(date -u +%FT%TZ)] session_window_paused=true action=skip_refine"
  else
    while IFS= read -r item; do
      [ -n "$DISPATCHED" ] && break
      ISSUE=$(get_issue_number "$item")

      # Handle spec-pending-review items first (before skip-label check would filter them)
      ITEM_LABELS=$(echo "$item" | jq -r '.labels[]?' 2>/dev/null)
      if echo "$ITEM_LABELS" | grep -qi "spec-pending-review"; then
        if ! is_issue_running "$ISSUE" && [ "$REFINE_RUNNING" -lt "$REFINE_WIP_LIMIT" ]; then
          spec_advance_check "$ISSUE" "$item"
        fi
        continue
      fi

      if has_refine_skip_label "$item"; then continue; fi
      # Opt-in gate: only auto-refine Backlog items labelled ready-for-agent.
      # Unlabelled items are left for triage — humans add the label when the issue is ready.
      if ! has_opt_in_refine_label "$item" && ! has_direct_to_pr_label "$item"; then continue; fi
      if is_issue_running "$ISSUE"; then continue; fi
      if [ "$REFINE_RUNNING" -ge "$REFINE_WIP_LIMIT" ]; then break; fi

      RETRIES=$(get_retry_count "${ISSUE}:refine")
      if [ "$RETRIES" -ge "$REFINE_MAX_RETRIES" ]; then
        trip_to_blocked "$ISSUE" "refine" "retry limit of ${REFINE_MAX_RETRIES} reached"
        continue
      fi

      increment_retry "${ISSUE}:refine"
      gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body "🧠 **Refinement Pipeline** — Starting brainstorming and spec generation.

---
*Posted by ${FACTORY_PRODUCT_NAME} Backlog Scheduler*" 2>/dev/null || true
      if dispatch "Refine issue #${ISSUE}"; then
        DISPATCHED="Refine issue #${ISSUE}"
        REFINE_RUNNING=$((REFINE_RUNNING + 1))
      fi
    done < <(echo "$BACKLOG" | jq -c '.[]')
  fi
```

**5.3.7** — Priority 6 (autopilot), currently:
```bash
  if [ -z "$DISPATCHED" ] && [ "$MAIN_IS_RED" = "false" ] && [ "${EPIC_AUTOPILOT_ENABLED:-false}" = "true" ]; then
```
becomes:
```bash
  if [ -z "$DISPATCHED" ] && [ "$MAIN_IS_RED" = "false" ] && [ "$SESSION_WINDOW_PAUSED" = "false" ] && [ "${EPIC_AUTOPILOT_ENABLED:-false}" = "true" ]; then
```

### Step 5.4 — verify it passes

```bash
bash tests/test_session_window_gate.sh
```
Expected output: `Results: 16 passed, 0 failed`.

Also re-run existing scheduler tests to confirm no regression:
```bash
bash tests/test_scheduler.sh
bash tests/test_scheduler_main_red_fixer.sh
bash tests/test_scheduler_autopilot_guard.sh
bash tests/test_dispatch_ceiling.sh
bash tests/test_scheduler_ceiling.sh
```
Expected output: `PASS` / `Results: N passed, 0 failed` for each, unchanged from before.

### Step 5.5 — commit

```bash
git add scheduler.sh tests/test_session_window_gate.sh
git commit -m "feat(scheduler): gate dispatch on session-window-paused sentinel (#35)"
```

---

## Validation summary (maps to spec's Validation section)

- **Python unit tests** (Task 2/3): resume-epoch parsing (structured preferred over
  substring/regex fallback), buffer/fallback minute math, sentinel atomic write, CLI
  wiring — `python -m pytest tests/test_factory_core_session_window.py -v`.
- **Scheduler bash test** (Task 5): all five dispatch priority blocks skipped while
  paused, gate precedes breaker call sites, sentinel self-clears once the embedded epoch
  passes — `bash tests/test_session_window_gate.sh`.
- **Entrypoint test** (Task 4): a simulated matched run writes the sentinel with the
  correct resume epoch and returns 0 (caller exits clean, bypassing `on_failure`); an
  unmatched failure or kill-switch-off returns 1 (falls through unchanged) —
  `bash tests/test_entrypoint_session_window.sh`.
- **Manual (staging)** — out of scope for this automated plan; call out in the PR
  description per spec: force a session-limit-shaped failure through a real dispatch
  post-deploy, confirm the sentinel appears with a sane resume timestamp, confirm the
  scheduler log shows the gate active and skips all five dispatch priorities, confirm no
  breaker increment and no `needs-discussion` label, confirm dispatch resumes
  automatically once the epoch passes. Requires the `docker compose build` + redeploy
  called out under Build constraint above — this plan's commits alone do not change a
  running instance's behavior.

## Known limitations (carried from spec, no code action — call out in the PR description)

- Cross-instance coordination (dark-factory + MarketHawk sharing one Claude Max account)
  is out of scope — each instance's scheduler only sees its own sentinel.
- Proactive utilization-based holding is out of scope, blocked on the factory's OAuth
  token lacking the `user:profile` scope.
- The unparseable-reset 30-minute fallback under-shoots a genuinely long remaining wait;
  self-corrects via re-pause on the next failed probe.
