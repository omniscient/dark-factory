# Scheduler: session-window-aware backoff

**Status:** design
**Date:** 2026-07-13
**Issue:** #35
**Epic:** #133 (Dark Factory platform)
**Build constraint:** `entrypoint.sh`, `scheduler.sh`, and `scripts/factory_core/breaker.py`
are **baked** into the image (`Dockerfile` `COPY`). This change needs
`docker compose build` + a redeploy of the running scheduler/run image before it takes
effect — editing the clone alone does not change runtime behavior.

## Problem

The factory's `claude -p` calls all share one Claude Max **5-hour OAuth session window**.
When it's exhausted, every in-flight dispatch fails. Today the scheduler has no concept of
this: each ticket's circuit breaker (`scripts/factory_core/breaker.py`) is a pure per-ticket
counter with zero knowledge of *why* a run failed, so a run that dies to session-window
exhaustion burns exactly the same 3-attempt retry budget as a real bug, tripping the ticket to
**Blocked + `needs-discussion`** — a false regression signal, and one that (per the
`factory_wip_limit: 1` dispatch model) can chew through several unrelated tickets' retry
budgets in sequence during a single outage window, since each doomed run exits fast and frees
the sole WIP slot for the scheduler to feed it the next ticket.

Operator calibration (issue comments, 2026-07-10/2026-07-13) shows the window depletes fast
under normal load (refine ≈10–14%, plan ≈10–12%, an implement chain ≈15–20% of a window) and
reliably exhausts 2–3h into an active period — this is a routine operating condition, not an
edge case.

## Existing mechanism and its gap

`entrypoint.sh:705-752` already has an in-container loop: on a non-zero exit it greps the raw
agent output for `usage limit|rate limit|429|credit balance|session limit`, tries to
regex-parse a human-readable reset time (`"resets 11:10pm (America/Toronto)"`), and — if
matched — `sleep`s and retries the **same** `archon workflow run` call forever, never exiting,
never touching the breaker.

That only protects the **one ticket whose container is currently running** when detection
succeeds. It does nothing for the *next* ticket the scheduler dispatches once a slot frees —
which is exactly what happens when detection misses (wrong/changed wording, or a genuinely
different failure the substring coincidentally matches or fails to match). There is also no
signal visible to the scheduler at all today: the sleep is silent and internal to one
container.

This design keeps the existing parse logic but changes what happens on a match: instead of
sleeping inside the container, the run **gives up immediately, publishes a shared pause
signal the scheduler can see, and exits clean**. This turns an invisible, single-container
workaround into a scheduler-wide, observable pause — with no in-container `sleep` holding the
sole WIP slot hostage for up to 5 hours.

## Decision

### 1. Detection (`entrypoint.sh`, inside the existing retry block)

Keep the existing substring/regex detection as the fallback, but prefer a structured signal
when present:

1. **Preferred:** if the captured output contains a `claude.rate_limit_event` structured log
   line (JSON, emitted by the Claude Code runner), parse its `resetsAt` field directly. This
   is the "scope-free" signal identified in the issue — no new OAuth scope needed, it's
   already in the run's own stdout/log stream.
2. **Fallback:** the existing substring match (`usage limit|rate limit|429|credit
   balance|session limit`) plus the existing human-readable `"resets HH:MMam/pm (TZ)"`
   regex parse (`entrypoint.sh:715-724`), unchanged.

Either path yields the same two outputs: **matched** (bool) and **resume epoch** (parsed
timestamp, or empty if unparseable).

Proactive polling of `api.anthropic.com/api/oauth/usage` (the operator's "hold above 85%
utilization" idea) is **explicitly out of scope**: the factory's own
`CLAUDE_CODE_OAUTH_TOKEN` lacks the required `user:profile` scope, and granting it is a
credential/deploy-surface change — human-only per CLAUDE.md. This ticket only covers reactive,
already-available signals.

### 2. On match: publish the pause, don't sleep in-container

Replace the matched branch's `sleep "$SLEEP_SECS"; continue` with:

```
RESUME_EPOCH = parsed resetsAt + 5min buffer   (mirrors check_rate_limit()'s existing +5s buffer convention)
             = now + 30min                     (fallback, when unparseable)

write RESUME_EPOCH to ${SCHEDULER_STATE_DIR}/session-window-paused   (atomic tmp+rename, like breaker.py's _atomic_write)
log: "session-window exhausted — dispatch paused until <ISO8601 UTC of RESUME_EPOCH>"
python3 cli.py run-record record --run-id "$RUN_ID" --issue "$ISSUE_NUM" --intent "$INTENT" \
  --stage paused --verdict paused        # audit trail entry distinct from failed/succeeded
exit 0
```

`exit 0` is deliberate: it must **not** flow through `on_failure` (`trap on_failure ERR`) —
this is not a ticket failure, so no failure comment, no breaker involvement, and the ticket's
board status is left exactly as it was. It also must not fall through to the
success-path cost-report/run-record assembly at the bottom of the script (lines 754-772),
since nothing meaningfully "completed" — hence the early `exit 0` right after the pause
record, skipping both paths.

The 30-minute unparseable fallback (rather than defaulting to the full 5h) matches the
operator's own manual practice of resuming promptly: since the reset lands at an arbitrary
point in a rolling 5h cycle, "unknown" means the true remaining wait is anywhere in `(0, 5h]`;
a short fallback with cheap re-probing (a probe against an exhausted window fails fast — it
doesn't burn a meaningful part of a fresh window) converges on the real reset quickly, whereas
a fixed 5h pause would idle the whole factory in the common case where the window resets much
sooner.

### 3. Scheduler-side dispatch gate (`scheduler.sh`)

Mirrors the existing `main-is-red` sentinel pattern exactly (same file family, same
directory, same per-tick check):

- Near the top of the poll loop, alongside the existing `MAIN_IS_RED` read
  (`scheduler.sh:954-955`):

  ```sh
  SESSION_WINDOW_PAUSED=false
  if [ -f "${SCHEDULER_STATE_DIR}/session-window-paused" ]; then
    RESUME_EPOCH=$(cat "${SCHEDULER_STATE_DIR}/session-window-paused" 2>/dev/null || echo 0)
    if [ "$(date +%s)" -lt "${RESUME_EPOCH:-0}" ]; then
      SESSION_WINDOW_PAUSED=true
    else
      rm -f "${SCHEDULER_STATE_DIR}/session-window-paused"   # self-clearing, no dispatch needed to verify
    fi
  fi
  ```

- Gate every dispatch priority block the same way `MAIN_IS_RED` already gates them (refine
  ~1180-1186, plan ~1142-1148, implement/Blocked-retry ~1086-1120, resolve ~977-991, plus the
  autopilot check at ~1201): skip dispatch entirely while `SESSION_WINDOW_PAUSED=true`. Log
  once per cycle (e.g. `session_window_gate=active resume_at=<ISO8601>`), no GitHub comment.

- **No new logic at the breaker call sites.** Because dispatch is what triggers a breaker
  check (`get_retry_count` → `increment_retry`/`trip_to_blocked`), and this gate prevents
  dispatch entirely while paused, no ticket's counter can advance for the duration of the
  pause — the existing 4 call sites need no cause-awareness of their own. A ticket that lands
  in the `Blocked` bucket via the existing orphaned-in-progress sweep (~lines 930-945) while
  paused is inert: its retry/breaker check simply doesn't run until the gate clears, then it's
  retried exactly like any other Blocked ticket, with no extra retry consumed by the
  session-window event itself.

  There is one accepted, bounded race: a ticket's run can hit the signature and get dispatched
  to `session-window-paused` in the *same* poll cycle the scheduler already read the (until-now
  absent) sentinel at the top of that cycle — that one ticket can pick up a single stray
  `increment_retry` before the next cycle observes the sentinel and freezes dispatch. A lone
  increment cannot trip a ticket that wasn't already at its retry ceiling, so this is not
  special-cased.

- **No self-check/recheck dispatch is needed** (unlike `main-is-red`'s throttled "Recheck
  main" run): the resume time is already known from the sentinel's embedded epoch, so clearing
  it is a pure time comparison at the top of each poll tick (default 60s cadence), not an
  active verification dispatch.

### 4. Scope: this instance only

The sentinel lives on `${FACTORY_INSTANCE}-scheduler-state` (`run-compose.yml:59-61`), a
volume already scoped per deployed instance. If the underlying Claude Max account is in fact
shared across the dark-factory self-target instance and the separately deployed MarketHawk
instance, a pause written by one instance does not stop the other from dispatching into the
same exhausted window — that cross-instance coordination is explicitly **out of scope** here
(it would require touching the human-only `deploy/` surface) and is called out below as a
known limitation.

## Config (`config/config.yaml`, new keys under the existing `scheduler:` block)

```yaml
scheduler:
  # ... existing keys unchanged ...
  session_window_backoff_enabled: true   # env: SESSION_WINDOW_BACKOFF_ENABLED overrides
  session_window_buffer_minutes: 5       # added to a parsed resetsAt; env: SESSION_WINDOW_BUFFER_MINUTES overrides
  session_window_fallback_minutes: 30    # pause length when the reset time can't be parsed; env: SESSION_WINDOW_FALLBACK_MINUTES overrides
```

Ships **enabled by default** (unlike `epic_autopilot`/`main_red_autofix`, which ship OFF
because they autonomously edit code or merge to main): this feature only pauses dispatch and
skips a counter increment, the same risk class as the already-on-by-default
`main_red_recheck_enabled`.

## Known limitations (call out in implementation)

- **Cross-instance coordination is out of scope.** A shared underlying Claude Max account
  across dark-factory and MarketHawk instances (if applicable) is not coordinated by this
  design — each instance's scheduler only sees its own sentinel.
- **Proactive utilization-based holding is out of scope**, blocked on the factory's OAuth
  token lacking the `user:profile` scope needed for `api.anthropic.com/api/oauth/usage`.
  Tracked as a future follow-up once that credential change is made (human-only).
- The unparseable-reset 30-minute fallback is a guess; if the true reset is further out, the
  scheduler will re-detect and re-pause on the next doomed dispatch, extending the pause in
  30-minute increments rather than jumping straight to the correct time.

## Validation

- **Python unit tests** (`tests/test_factory_core_breaker.py`-style, mocked): resume-epoch
  parsing (structured `resetsAt` preferred over substring/regex fallback); buffer/fallback
  minute math; no breaker call is reachable while the gate is active (assert dispatch-skip,
  not a breaker-side bypass flag).
- **Scheduler bash test** (`SCHEDULER_SOURCE_ONLY`, mirroring `test_scheduler_main_red_fixer.sh`):
  all four dispatch priority blocks (refine/plan/implement/resolve) are skipped while
  `session-window-paused`'s embedded epoch is in the future; dispatch resumes and the sentinel
  is removed once the epoch passes; no breaker call site log/state changes while paused.
- **Entrypoint test** (mirroring `test_entrypoint_preflight.sh`/`test_entrypoint_fix_main.sh`):
  a simulated matched run writes the sentinel with the correct resume epoch and exits 0
  without invoking `on_failure` or the success-path run-record assembly; an unmatched failure
  still flows through the normal `on_failure`/breaker path unchanged.
- **Manual (staging):** force a session-limit-shaped failure message through a real dispatch,
  confirm the sentinel appears with a sane resume timestamp, confirm the scheduler log shows
  the gate active and skips all dispatch priorities, confirm no breaker increment and no
  `needs-discussion` label, confirm dispatch resumes automatically once the epoch passes.

## Accepted trade-offs

- A single stray `increment_retry` can occur in the same-poll-cycle race described above;
  bounded and non-trippable on its own, not special-cased.
- The 30-minute unparseable fallback under-shoots a genuinely long remaining wait; the
  self-correcting re-pause on the next failed probe is judged cheaper than guessing the full
  5h and idling the factory unnecessarily in the common case.
- No cross-instance or proactive-utilization coverage (see Known limitations) — both are
  credential/deploy-surface changes outside this ticket's reach.

## Assumptions

- The Claude Code runner already emits a `claude.rate_limit_event`-shaped structured log line
  with a `resetsAt` field into the same output stream `entrypoint.sh` already captures
  (`TMP_OUT`) — this design consumes it as text-in-stdout, same capture mechanism as today's
  substring heuristic, not a new IPC channel.
- `factory_wip_limit` stays at `1` (or low) for the dark-factory self-target instance; the
  motivating pathology (retry budget burned across a rotating set of tickets) is specific to a
  low-concurrency dispatch model.

## Open questions (non-blocking)

- Should the `session-window-paused` sentinel content also record *which* ticket/phase
  triggered it, for post-incident debugging? Not required for correctness (the gate only needs
  the timestamp), but cheap to add if useful.
