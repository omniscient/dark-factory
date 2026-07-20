# Plan: Session-Window Reset Parse — Fix Day-Rollover, Clamp to Physical Window Bound

**Issue:** #305 (regression in #35)
**Spec:** `docs/superpowers/specs/2026-07-19-session-window-reset-parse-clamp-design.md`
**Status:** plan

## Goal

Fix the ~22h false-pause bug in `scripts/factory_core/session_window.py`: (1) a bare
wall-clock reset time (`"resets 9:20pm (UTC)"`) that has already passed today must resolve
to today, not roll to tomorrow, and (2) `compute_resume_epoch`'s output must be clamped to
`now + MAX_SESSION_WINDOW_HOURS(5) + buffer_minutes` across all three of its resolution
paths (structured/fallback/default), since a Claude Max session window can never truly
reset more than 5h out. Two isolated pure-function edits plus regression tests — no
call-site changes in `cli.py`, `entrypoint.sh`, or `scheduler.sh` (all three consume
`compute_resume_epoch`/`check_and_pause`'s return value opaquely).

## Architecture

`scripts/factory_core/session_window.py` currently has two independent bugs:

1. `parse_fallback_reset_epoch` (lines 55-72) builds today's candidate `datetime` for the
   parsed wall-clock time, and unconditionally rolls to tomorrow if that candidate is
   already in the past relative to `now_epoch`. At 22:49 with a parsed time of 21:20, this
   produces a ~22h-out resume instead of recognizing the reset already happened.
2. `compute_resume_epoch` (lines 75-86) trusts whatever any of its three paths (structured
   `resetsAt`, regex fallback, `now + fallback_minutes` default) returns, with no upper
   bound — so a malformed/misparsed value on *any* path can produce a physically
   impossible resume.

Both fixes are self-contained: (1) removes the day-rollover branch, returning the literal
(possibly past) candidate timestamp; (2) adds a module constant
`MAX_SESSION_WINDOW_HOURS = 5` and a single `ceiling = now_epoch +
MAX_SESSION_WINDOW_HOURS*3600 + buffer_minutes*60` clamp wrapping all three return paths.
Downstream, `scheduler.sh`'s existing self-clearing gate (`now >= resume_epoch → clear
sentinel, resume dispatch`) already treats a past/near-immediate resume epoch as elapsed —
no code change needed there (spec Assumptions).

Spec item 3 (verify structured-`resetsAt` precedence over fallback) requires **no code
change** — `compute_resume_epoch` already checks `structured` before `fallback`. Task 3
below verifies this in place rather than modifying anything.

## Tech Stack

Python (stdlib `datetime`/`zoneinfo`, no new dependencies), pytest
(`tests/test_factory_core_session_window.py`, existing `SCHEDULER_SOURCE_ONLY`-style pure
unit tests with no fixtures beyond stdlib `datetime`).

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/session_window.py` | `parse_fallback_reset_epoch`: remove day-rollover branch. `compute_resume_epoch`: add `MAX_SESSION_WINDOW_HOURS` constant + clamp on all 3 return paths. Drop now-unused `timedelta` import. |
| `tests/test_factory_core_session_window.py` | Rename+flip 1 existing test, add 3 new regression tests |

Both edits are confined to `scripts/factory_core/session_window.py` (the canonical
source). Per the spec's Assumptions, `dark-factory/scripts/factory_core/session_window.py`
(the TARGET-PATH self-target scaffold copy `entrypoint.sh` bakes from) is kept in sync by
the existing build/copy mechanism and is not edited directly by this plan — confirmed
identical to the canonical file as of plan authoring (`diff` returns no output).

---

## Task 1: Fix the day-rollover bug in `parse_fallback_reset_epoch`

**Files:** `tests/test_factory_core_session_window.py`, `scripts/factory_core/session_window.py`

### Step 1.1 — write the failing tests

In `tests/test_factory_core_session_window.py`, replace the existing rollover test (lines
69-73) — it currently pins the bug as correct behavior:

```python
def test_parse_fallback_reset_epoch_rolls_over_to_next_day():
    now = int(datetime(2026, 7, 13, 23, 30, tzinfo=timezone.utc).timestamp())
    text = "resets 11:10pm (UTC)"
    expected = int(datetime(2026, 7, 14, 23, 10, tzinfo=timezone.utc).timestamp())
    assert parse_fallback_reset_epoch(text, now) == expected
```

with the corrected-behavior version (same fixture values, assertion flipped from
tomorrow's timestamp to today's):

```python
def test_parse_fallback_reset_epoch_stays_today_when_time_already_passed():
    now = int(datetime(2026, 7, 13, 23, 30, tzinfo=timezone.utc).timestamp())
    text = "resets 11:10pm (UTC)"
    expected = int(datetime(2026, 7, 13, 23, 10, tzinfo=timezone.utc).timestamp())
    assert parse_fallback_reset_epoch(text, now) == expected


def test_parse_fallback_reset_epoch_matches_305_incident_repro():
    # Issue #305 repro: death at 22:49Z, reset text names 21:20Z (already passed today).
    # Must resolve to today, not roll to tomorrow (the ~22h false-pause bug).
    now = int(datetime(2026, 7, 18, 22, 49, tzinfo=timezone.utc).timestamp())
    text = "...resets 9:20pm (UTC)"
    result = parse_fallback_reset_epoch(text, now)
    assert result is not None
    assert result <= now
```

### Step 1.2 — verify it fails

```bash
python -m pytest tests/test_factory_core_session_window.py -v -k "stays_today_when_time_already_passed or matches_305_incident_repro"
```

Expected output:

```
FAILED tests/test_factory_core_session_window.py::test_parse_fallback_reset_epoch_stays_today_when_time_already_passed - AssertionError: assert 1784070600 == 1783984200
FAILED tests/test_factory_core_session_window.py::test_parse_fallback_reset_epoch_matches_305_incident_repro - assert 1784496000 <= 1784414940
======================== 2 failed in 0.1Xs ========================
```

(Both fail against the current implementation: the first because it still rolls to
tomorrow instead of staying on the 13th; the second because 21:20-rolled-to-tomorrow
[`1784496000`] is greater than `now` [`1784414940`], reproducing the exact #305 defect.)

### Step 1.3 — implement

In `scripts/factory_core/session_window.py`, remove the day-rollover branch (lines 69-72):

```python
    now_dt = datetime.fromtimestamp(now_epoch, tz)
    candidate = datetime.combine(now_dt.date(), parsed_time, tzinfo=tz)
    if candidate.timestamp() < now_epoch:
        candidate += timedelta(days=1)
    return int(candidate.timestamp())
```

becomes:

```python
    now_dt = datetime.fromtimestamp(now_epoch, tz)
    candidate = datetime.combine(now_dt.date(), parsed_time, tzinfo=tz)
    return int(candidate.timestamp())
```

`timedelta` is now unused in this module (its only other use was this branch) — drop it
from the import (line 7):

```python
from datetime import datetime, timedelta
```

becomes:

```python
from datetime import datetime
```

### Step 1.4 — verify it passes

```bash
python -m pytest tests/test_factory_core_session_window.py -v -k "stays_today_when_time_already_passed or matches_305_incident_repro"
```

Expected output:

```
tests/test_factory_core_session_window.py::test_parse_fallback_reset_epoch_stays_today_when_time_already_passed PASSED
tests/test_factory_core_session_window.py::test_parse_fallback_reset_epoch_matches_305_incident_repro PASSED
======================== 2 passed in 0.1Xs ========================
```

Also run the full module's test file to confirm no regression to the other parse tests:

```bash
python -m pytest tests/test_factory_core_session_window.py -v
```

Expected: all tests pass (23 at this point — 22 pre-existing, renamed in place, plus the
1 new repro test; Task 2 adds 2 more, for 25 total).

### Step 1.5 — commit

```bash
git add scripts/factory_core/session_window.py tests/test_factory_core_session_window.py
git commit -m "fix(session-window): stop rolling a past-today reset time to tomorrow (#305)"
```

---

## Task 2: Clamp `compute_resume_epoch` to the physical 5h session-window bound

**Files:** `tests/test_factory_core_session_window.py`, `scripts/factory_core/session_window.py`

### Step 2.1 — write the failing tests

In `tests/test_factory_core_session_window.py`, add `timedelta` to the top-level import
(line 2):

```python
from datetime import datetime, timezone
```

becomes:

```python
from datetime import datetime, timedelta, timezone
```

Add two new tests immediately after `test_compute_resume_epoch_none_when_no_match`
(after line 106):

```python
def test_compute_resume_epoch_clamps_structured_far_future_to_max_window():
    # A malformed/far-out structured resetsAt (here +48h) is exactly as physically
    # impossible as the fallback rollover bug and must be bounded the same way.
    now = int(datetime(2026, 7, 18, 22, 49, tzinfo=timezone.utc).timestamp())
    far_future = datetime.fromtimestamp(now, tz=timezone.utc) + timedelta(hours=48)
    resets_at = far_future.isoformat().replace("+00:00", "Z")
    text = f'{{"event":"claude.rate_limit_event","resetsAt":"{resets_at}"}}'
    result = compute_resume_epoch(text, now, buffer_minutes=5, fallback_minutes=30)
    ceiling = now + 5 * 3600 + 5 * 60
    assert result <= ceiling


def test_compute_resume_epoch_clamps_305_incident_to_max_window():
    now = int(datetime(2026, 7, 18, 22, 49, tzinfo=timezone.utc).timestamp())
    text = "You've hit your session limit · resets 9:20pm (UTC)"
    result = compute_resume_epoch(text, now, buffer_minutes=5, fallback_minutes=30)
    assert result is not None
    assert result <= now + 5 * 3600 + 5 * 60
```

### Step 2.2 — verify it fails

```bash
python -m pytest tests/test_factory_core_session_window.py -v -k "clamps_structured_far_future or clamps_305_incident"
```

Expected output (run this *after* Task 1 is committed):

```
tests/test_factory_core_session_window.py::test_compute_resume_epoch_clamps_structured_far_future_to_max_window FAILED
tests/test_factory_core_session_window.py::test_compute_resume_epoch_clamps_305_incident_to_max_window PASSED
======================== 1 failed, 1 passed in 0.1Xs ========================
```

```
FAILED tests/test_factory_core_session_window.py::test_compute_resume_epoch_clamps_structured_far_future_to_max_window - assert 1784588040 <= 1784433240
```

Only the far-future structured test is clamp-isolating red here: the far-future `resetsAt`
path has no upper bound yet, so `structured + buffer_minutes*60` (`1784588040`) exceeds the
ceiling (`1784433240`). `test_compute_resume_epoch_clamps_305_incident_to_max_window`
already **passes** at this point — Task 1's rollover fix alone makes the fallback path
return today-21:20 (already `<= now`), so its `+buffer_minutes*60` result is trivially
under the ceiling even with no clamp. That test is not a red-first proof of the clamp; it's
an end-to-end regression guard confirming the fallback path *stays* bounded once the clamp
is added in Step 2.3 (both tests must be green together after implementation).

### Step 2.3 — implement

In `scripts/factory_core/session_window.py`, add the module constant next to the existing
regex constants (after `_HUMAN_RESET_RE`, i.e. after the current lines 19-21):

```python
_HUMAN_RESET_RE = re.compile(
    r"resets\s+([0-9]{1,2}:[0-9]{2}[ap]m)\s*\(([^)]+)\)", re.IGNORECASE
)
```

becomes:

```python
_HUMAN_RESET_RE = re.compile(
    r"resets\s+([0-9]{1,2}:[0-9]{2}[ap]m)\s*\(([^)]+)\)", re.IGNORECASE
)
# Physical invariant: a Claude Max session window is fixed at 5h, so no true resume can
# ever be more than 5h out. Hardcoded, not a config.yaml key -- see #305.
MAX_SESSION_WINDOW_HOURS = 5
```

Then wrap all three return paths of `compute_resume_epoch` (current lines 78-86):

```python
    if not is_session_window_failure(text):
        return None
    structured = parse_structured_reset_epoch(text)
    if structured is not None:
        return structured + buffer_minutes * 60
    fallback = parse_fallback_reset_epoch(text, now_epoch)
    if fallback is not None:
        return fallback + buffer_minutes * 60
    return now_epoch + fallback_minutes * 60
```

becomes:

```python
    if not is_session_window_failure(text):
        return None
    ceiling = now_epoch + MAX_SESSION_WINDOW_HOURS * 3600 + buffer_minutes * 60
    structured = parse_structured_reset_epoch(text)
    if structured is not None:
        return min(structured + buffer_minutes * 60, ceiling)
    fallback = parse_fallback_reset_epoch(text, now_epoch)
    if fallback is not None:
        return min(fallback + buffer_minutes * 60, ceiling)
    return min(now_epoch + fallback_minutes * 60, ceiling)
```

### Step 2.4 — verify it passes

```bash
python -m pytest tests/test_factory_core_session_window.py -v
```

Expected output: all 25 tests pass (22 present before this plan's edits, +1 net from
Task 1's rename-in-place plus new repro test, +2 from this task's clamp tests).

```
======================== 25 passed in 0.6Xs ========================
```

Then run the full repo suite to confirm no cross-module regression (`compute_resume_epoch`
has no other callers outside this file's own tests and `cli.py`, which is exercised by
`test_cli_session_window_check_matched`/`_unmatched` in the same file, but re-run the whole
suite per CLAUDE.md convention):

```bash
python -m pytest tests/ -v
```

Expected output: all tests pass, no `FAILED` lines (`1413 passed` as of plan authoring on
`main`; exact count may drift with unrelated commits, but zero failures is the bar).

### Step 2.5 — commit

```bash
git add scripts/factory_core/session_window.py tests/test_factory_core_session_window.py
git commit -m "fix(session-window): clamp compute_resume_epoch to the 5h session-window bound (#305)"
```

---

## Task 3: Verify structured-vs-fallback precedence (no code change)

**Files:** none (verification only)

Spec item 3 concludes `compute_resume_epoch` already prefers `parse_structured_reset_epoch`
over `parse_fallback_reset_epoch` correctly, and needs no fix. Confirm this holds after
Task 1/2's edits by re-reading the post-Task-2 function body and re-running the existing
precedence test:

```bash
python -m pytest tests/test_factory_core_session_window.py -v -k test_compute_resume_epoch_prefers_structured_over_fallback
```

Expected output:

```
tests/test_factory_core_session_window.py::test_compute_resume_epoch_prefers_structured_over_fallback PASSED
======================== 1 passed in 0.1Xs ========================
```

```bash
sed -n '/^def compute_resume_epoch/,/^def write_pause_sentinel/p' scripts/factory_core/session_window.py
```

(Grepped by symbol name, not a fixed line range — Task 2's inserted constant and Task 1's
shortened rollover branch shift `compute_resume_epoch`'s starting line from 75 to 76, so a
hardcoded range would silently drift.)

Expected: the `structured = parse_structured_reset_epoch(text)` check and its `if
structured is not None: return ...` both appear textually before the
`parse_fallback_reset_epoch` call — confirming precedence order is unchanged by Task 1/2's
edits. No commit — this task produces no diff.

---

## Validation summary (maps to spec's Decision items)

- **Item 1** (clamp `compute_resume_epoch`'s output to the physical window bound, all
  three paths): Task 2, Step 2.3.
- **Item 2** (fix the day-rollover — past-today means today, not tomorrow): Task 1, Step
  1.3.
- **Item 3** (structured-vs-fallback precedence: no code change, document the finding):
  Task 3 — verified in place, no diff. The follow-up `source=` diagnostic tag the spec
  identifies as a related but distinct gap is **not filed by this plan** (refine/plan
  phases don't file issues); it is recommended for a separate follow-up ticket at
  implementation-review or issue-triage time.
- **Item 4** (regression tests): Task 1 Step 1.1 (rename+flip existing test, add #305
  repro test), Task 2 Step 2.1 (clamp tests for both the structured and fallback paths).

## Known limitations (carried from spec, no code action)

- The exact reason `parse_structured_reset_epoch` didn't yield a value for the 2026-07-17/18
  incident's 3 `claude.rate_limit_event` lines remains unconfirmed (spec Known
  limitations) — the raw run log wasn't available during refinement. Task 2's clamp bounds
  the damage regardless of cause; full root-cause closure is out of scope for this plan.
- The 30-minute unparseable-fallback default (`session_window_fallback_minutes`) is
  unchanged and already well inside the new 5h+buffer ceiling — no interaction with the
  clamp.
- A follow-up ticket ("session-window: emit resume-epoch source (structured/fallback/default)
  for post-incident debuggability") is recommended by the spec but not filed by this plan.
