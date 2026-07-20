# Session-window reset parse: fix day-rollover bug, clamp to physical window bound

**Status:** design
**Date:** 2026-07-19
**Issue:** #305 (regression in #35)
**Related:** #35 (shipped the backoff being fixed), #292 (session-window failure-comment noise,
not touched here), #303 (adjacent `session_window.py` signature work, no overlap found)

## Problem

The factory sat `session-window-paused` for ~22.5 hours (2026-07-17 22:49Z →
2026-07-18 21:25Z; 1217 `session_window_gate=active` poll cycles) while both Claude Max token
windows were near-empty (5h: 4%, 7d: 11%) — it was not actually rate-limited for a day; a single
bad sentinel over-paused the whole factory.

Two runs died with the identical failure text `"...resets 9:20pm (UTC)"`:

- **19:00Z death** → sentinel `resume=2026-07-17T21:25:00Z` (same day). Correct.
- **~22:49Z death** → sentinel `resume=2026-07-18T21:25:00Z` (next day). **Bug.**

## Root cause

`scripts/factory_core/session_window.py::parse_fallback_reset_epoch` parses a bare
wall-clock time (`"9:20pm"`) and builds a candidate `datetime` for **today** at that time. If
`candidate.timestamp() < now_epoch` — i.e. the named time has already passed today — it
unconditionally adds one day:

```python
candidate = datetime.combine(now_dt.date(), parsed_time, tzinfo=tz)
if candidate.timestamp() < now_epoch:
    candidate += timedelta(days=1)
return int(candidate.timestamp())
```

At 22:49, a parsed time of 21:20 has already passed today, so this rolls to tomorrow —
producing a ~22h resume. A Claude Max session window is fixed at **5 hours**; no true reset can
ever be more than 5h out, so any resume epoch `> now + ~5h` is provably wrong regardless of how
it was derived. Nothing in `compute_resume_epoch` (the function's only caller, besides tests)
enforces that invariant — it trusts whatever any of its three paths (structured, fallback,
default-30min) returns.

**On the "was structured `resetsAt` precedence bypassed?" question** (issue also asks to verify
this): it was not. `compute_resume_epoch` already tries the structured path first in code —

```python
structured = parse_structured_reset_epoch(text)
if structured is not None:
    return structured + buffer_minutes * 60
fallback = parse_fallback_reset_epoch(text, now_epoch)
```

— so precedence ordering is already correct and needs no change. The far more likely
explanation for the incident (3 `claude.rate_limit_event` lines present, yet the fallback path
clearly drove the result) is a silent parse-miss inside `parse_structured_reset_epoch`: it
`continue`s past any line whose embedded `{...}` doesn't `json.loads` or whose `resetsAt` isn't
present, with no visibility into *why* a given line was skipped. The raw run log for the
2026-07-17/18 incident isn't available to this refine pass to confirm the exact malformed
shape, so this cannot be fully closed here — see Open Questions / follow-up below.

## Decision

### 1. Clamp `compute_resume_epoch`'s output to the physical window bound (all paths)

Add a module-level constant next to the existing regex constants in
`scripts/factory_core/session_window.py`:

```python
MAX_SESSION_WINDOW_HOURS = 5  # physical invariant: a Claude Max session window is fixed at 5h
```

Apply the clamp once, at `compute_resume_epoch`'s return boundary, wrapping **all three**
paths (structured, fallback, and the `now + fallback_minutes` default) — not just the fallback
branch that caused this specific incident. A malformed structured `resetsAt` (e.g. a
mis-parsed epoch, or a `resetsAt` many days out due to an upstream bug) is exactly as
physically impossible and deserves the same protection:

```python
ceiling = now_epoch + MAX_SESSION_WINDOW_HOURS * 3600 + buffer_minutes * 60
...
return min(candidate, ceiling)   # applied to whichever of the three paths produced `candidate`
```

This constant is a **hardcoded module constant, not a `config.yaml` key**. It encodes a
physical fact about the product (Claude Max's fixed 5h session window), not an operator
tuning knob like `session_window_buffer_minutes`/`session_window_fallback_minutes` — making it
configurable would let an operator (or a future misconfiguration) set it back to a value that
reopens this exact bug. The buffer added on top of the ceiling reuses the existing
`buffer_minutes` parameter already threaded through the function; no new constant or config key
for that either.

### 2. Fix the day-rollover: a past-today time means today, not tomorrow

Remove the `+= timedelta(days=1)` branch in `parse_fallback_reset_epoch`. Return today's
candidate timestamp as-is, even when it is already in the past relative to `now_epoch`:

```python
candidate = datetime.combine(now_dt.date(), parsed_time, tzinfo=tz)
return int(candidate.timestamp())
```

This keeps the parser single-purpose (it answers "what instant does this string name," not
"when should we resume") and pushes resume-policy decisions to their existing owners:
`compute_resume_epoch` adds `buffer_minutes`, the new clamp bounds the far end, and
`scheduler.sh`'s existing self-clearing gate (`now >= resume_epoch → clear sentinel, resume
dispatch`) already treats a past resume epoch as "already elapsed" with no code change needed
there. Net effect for the exact incident case (now=22:49, parsed=21:20): the function returns
today-21:20 (in the past), `compute_resume_epoch` adds the 5-minute buffer, the scheduler sees
the sentinel is already past and clears it on the next ~60s poll — i.e. an effectively
immediate resume instead of a ~22h stall, without guessing at "already reset" vs. "imminent."

### 3. Structured-vs-fallback precedence: no code change, document the finding

As established above, `compute_resume_epoch` already prefers structured `resetsAt` correctly.
No change is needed here. A `source=structured|fallback|default` diagnostic tag (so a *future*
occurrence of "structured lines were present but fallback drove the result" is immediately
debuggable) is a real gap, but is explicitly **out of scope for this ticket** — it touches the
function's return contract, `check_and_pause`, `cli.py`'s stdout, and `entrypoint.sh`'s
`matched=`/`resume_epoch=` grep-parsing, each needing its own test coverage. This is more
surface than a must-have regression fix should carry, and CLAUDE.md's "touch only what the
plan lists" scope discipline applies. **Follow-up ticket to file:** "session-window: emit
resume-epoch source (structured/fallback/default) for post-incident debuggability," referencing
#305 and this incident.

### 4. Regression tests

`tests/test_factory_core_session_window.py`, in scope for this ticket (a test-only edit is part
of the same behavior change per CLAUDE.md's "TDD for behavior changes" — the old test currently
pins the bug as correct, so fixing the bug without touching it would leave a green test
asserting the wrong thing):

- **Edit in place** `test_parse_fallback_reset_epoch_rolls_over_to_next_day` (lines 69-73).
  Rename to reflect the corrected behavior (e.g.
  `test_parse_fallback_reset_epoch_stays_today_when_time_already_passed`) and flip its
  assertion from tomorrow's timestamp to today's (now-in-the-past) timestamp. Keep the
  existing fixture values so the diff on this one test is the clearest possible review artifact
  for the behavioral flip. This is retained (not deleted) because it is the only parse-layer
  test exercising the exact past-today branch being fixed; the item below covers a different
  (near-the-minute, cross-midnight-adjacent) case and doesn't substitute for it.
- **Add** a new test using the issue's exact repro: `now=2026-07-18T22:49:00Z`,
  `text="...resets 9:20pm (UTC)"` (parsed time 21:20), assert
  `parse_fallback_reset_epoch(text, now) <= now` (today, not tomorrow).
- **Add** a `compute_resume_epoch` clamp test: feed a structured `resetsAt` far in the future
  (e.g. +48h) and assert the returned resume epoch is
  `<= now_epoch + MAX_SESSION_WINDOW_HOURS*3600 + buffer_minutes*60` — proving the clamp
  independently of the parse fix, since it must also protect a malformed structured path.
- **Add** a `compute_resume_epoch` test mirroring the exact incident end-to-end (fallback path,
  same 22:49/9:20pm repro) asserting `resume_epoch <= now_epoch + 5*3600 + buffer_minutes*60`.

No changes needed to `cli.py`, `entrypoint.sh`, or `scheduler.sh` — the fix is fully contained
in `session_window.py`'s pure functions, and all three callers already consume
`compute_resume_epoch`'s/`check_and_pause`'s return value opaquely.

## Alternatives considered

1. **Make `MAX_SESSION_WINDOW_HOURS` a `config.yaml` key** (mirroring
   `session_window_buffer_minutes`/`session_window_fallback_minutes`). Rejected: this bound is a
   physical fact about the product, not a policy choice; exposing it as a tunable knob would let
   a future config change silently reopen this exact bug, which is the opposite of what a
   safety clamp should allow.
2. **On a past-today parse, return `now_epoch` directly** (an explicit "resume now") instead of
   today's literal past timestamp. Rejected: this manufactures certainty the parser doesn't
   have (we can't distinguish "already reset 90 minutes ago" from "clock text is slightly
   stale") and duplicates resume-policy logic that already lives in `compute_resume_epoch`
   (buffer) and `scheduler.sh` (self-clearing gate). Returning the literal parsed instant and
   letting the existing downstream layers handle it keeps each function single-purpose and
   produces the same practical outcome (near-immediate resume) without extra logic.
3. **Delete the old day-rollover test instead of editing it in place.** Rejected: it's the only
   parse-layer test that exercises the exact past-today branch being fixed; deleting it loses
   that boundary coverage. Edited in place (renamed, assertion flipped) it becomes the clearest
   single-line proof of the fix.
4. **Bundle the `source=` diagnostic tag into this ticket** since it's directly motivated by the
   same incident. Rejected per Q&A: it's a distinct observability surface (touches 4 files, not
   1) with no correctness bearing on this fix once the clamp lands; scoped out to a follow-up
   ticket instead.

## Known limitations

- The exact reason `parse_structured_reset_epoch` didn't yield a value for this specific
  incident's 3 `claude.rate_limit_event` lines remains unconfirmed — the raw run log wasn't
  available to this refine pass. The clamp (item 1) bounds the damage regardless of cause; full
  root-cause closure is deferred to the follow-up observability ticket (item 3).
- The 30-minute unparseable-fallback default (`session_window_fallback_minutes`, unchanged by
  this ticket) is well inside the new 5h+buffer ceiling and is unaffected by the clamp.

## Accepted trade-offs

- `MAX_SESSION_WINDOW_HOURS` is a code constant, meaning changing it (e.g. if Claude Max's
  window length changes) requires a reviewed code diff rather than a config edit. This is
  intentional: it's the same protection this ticket exists to add — a safety invariant should
  not be a runtime-mutable surface.
- A past-today parsed timestamp can be arbitrarily far in the past within the same UTC day (up
  to ~24h if the death happens just before midnight and the reset time was just after the
  previous midnight) — but by construction this is always `<= now`, so the scheduler's
  self-clearing gate treats it as already-elapsed with no pause at all, which is strictly safer
  than both the old bug (rolls forward, over-pauses) and any fixed positive fallback.

## Assumptions

- No caller of `compute_resume_epoch`/`check_and_pause` (`cli.py`, `entrypoint.sh`,
  `scheduler.sh`) depends on the returned epoch ever being `> now_epoch` — inspection confirms
  all three treat it opaquely (write sentinel, compare to `date +%s` on each poll), so a
  same-day-or-past return value needs no caller-side changes.
- `dark-factory/scripts/factory_core/session_window.py` (the TARGET-PATH self-target scaffold
  copy referenced by `entrypoint.sh`'s baked path) is kept in sync with the canonical
  `scripts/factory_core/session_window.py` by the existing build/copy mechanism — this ticket
  edits the canonical source only, consistent with how #35 and #303 were implemented.

## Open questions (non-blocking)

- Should the follow-up `source=` diagnostic ticket also capture the raw unparsed
  `claude.rate_limit_event` line(s) into the run-record `paused` entry, so a future incident's
  root cause is confirmable without needing the full run log? Worth deciding when that ticket
  is scoped, not here.
