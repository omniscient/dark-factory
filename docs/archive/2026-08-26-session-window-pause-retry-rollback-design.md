# Roll Back the Retry-Budget Counter for a Confirmed Session-Window Pause

**Issue:** omniscient/dark-factory#341
**Status:** new spec, refined 2026-08-26 against current `main` (post-#344 merge, `dc2e4ab`).
**Depends on:** #344 (merged) — tightened `session_window.py`'s classifier to require a structured
exhaustion event (`status=rejected`, or the legacy no-status `claude.rate_limit_event` shape) or explicit usage/session/credit exhaustion phrasing (`_SESSION_EXHAUSTION_RE`) before calling a run "paused," closing the
false-positive-pause hole that made this ticket's original evidence (the #19/#207 incident) ambiguous.
**Related:** #279 (`docs/archive/2026-07-28-delivery-failure-retry-exemption-design.md`) — the directly
analogous "an attempt that never produced a verdict must not consume a retry" change this spec extends
the same drop-file/decision-point mechanism from; #292 (the pause path itself); #334 (orphan sweep vs.
sentinel ordering); #335 (board-move failures).

---

## Overview / Problem Statement

`scheduler.sh` pre-increments a per-issue-and-phase retry counter (`increment_retry`) **at dispatch
time**, before the container has run at all — an optimistic count of "an attempt is in flight."
`entrypoint.sh`'s `_handle_session_window_pause()` (now correctly gated by #344's fixed classifier)
detects a genuine Claude 5h session-window exhaustion mid-run, writes a *global* pause sentinel that
freezes all scheduler dispatch until the parsed resume epoch, records a `run-record --stage paused
--verdict paused` entry, and exits clean — but it leaves **no per-issue trace** the scheduler can read
back. On the next poll that considers the paused issue, `get_retry_count` still returns the
pre-increment value from the interrupted attempt, indistinguishable from a real failure. This is
exactly what happened to #19 (2026-08-21): two genuine plan failures (counter → 2), a third dispatch
that paused mid-run (counter → 3, never reaching a verdict), and on resume the scheduler saw
`RETRIES(3) >= REFINE_MAX_RETRIES(3)` and tripped the ticket to Blocked without ever giving attempt 3
a real chance to run. #207's refine counter (2, one strike from tripping) was corrupted the same way.

`increment_retry` is called unconditionally at four `scheduler.sh` call sites — `stage_blocked_retry`
(implement), `stage_plan` (plan), `stage_refine` (refine), `stage_conflict_resolve` (resolve) — all of
which share the same "counter, incremented before the outcome is known" architecture. This spec adds a
correction step: when the scheduler observes (via the same drop-file channel #279 already built) that
the previous dispatch for a given issue+phase key was a **classifier-confirmed genuine pause**, it
rolls back that dispatch's increment before evaluating the new one.

This is intentional, in-scope modification of the breaker/retry mechanism, requested directly by the
issue that owns it — not an incidental weakening of a safety gate as a side effect of unrelated work
(the case CLAUDE.md's hard limit is aimed at). The correction is unconditional and clamped (a paused
attempt can only ever restore the counter to its pre-dispatch value, never below), keeps the breaker
fail-closed in every other respect, and is bounded in the worst case by wall-clock time via the
existing global session-window sentinel (see Requirement 3 and the Q&A) rather than by a new capped
counter.

---

## Requirements

1. When `check_failure_signature` returns signature exactly `environmental:session_window_pause` (a
   new value, written only by the pause path — never by `error_signature.classify()`, which only runs
   for genuine failures), the phase's normal retry counter must be **decremented by 1, clamped at 0**,
   before that call site's existing `get_retry_count` → ceiling-check → `increment_retry` sequence
   runs for the new dispatch. Net effect when the new dispatch proceeds: the counter ends up exactly
   where it would be had the paused attempt never been counted. Unlike #279's `environmental:
   delivery_failure` handling, this correction is **unconditional and not deferred** to any
   dispatch-branch — it corrects history (a past increment for an attempt that never reached a
   verdict), not future counting, so it must apply immediately at the existing ceiling-check
   checkpoint, before that checkpoint reads the counter, at all four sites — including
   `stage_conflict_resolve`, whose ceiling-check and `increment_retry` call are not adjacent (see
   Requirement 2). This is simpler than #279's two-step split there: the decrement fires at the single
   existing checkpoint, not split across two sites.
2. Apply uniformly to all four `scheduler.sh` retry sites, same keys/ceilings #279 already documented:
   `stage_blocked_retry` (implement, keyed `"$ISSUE"`, ceiling `$MAX_RETRIES`), `stage_plan` (plan,
   keyed `"${ISSUE}:plan"`, ceiling `$REFINE_MAX_RETRIES`), `stage_refine` (refine, keyed
   `"${ISSUE}:refine"`, ceiling `$REFINE_MAX_RETRIES`), `stage_conflict_resolve` (resolve, keyed
   `"${ISSUE}:resolve"`, ceiling `$MAX_RETRIES`). `_handle_session_window_pause()` is intent-agnostic —
   it never inspects `$INTENT` beyond recording it — so a genuine window exhaustion can occur mid-run
   for any of the four phases; scoping to only the two phases with concrete #19/#207 evidence would
   encode sampling bias as policy and leave a known-identical bug live on the phase with the longest
   runs (implement).
3. **No cap / no shadow counter**, unlike #279's `<key>:delivery`. A `delivery_failure` needed a cap
   because it is per-issue, invisible to other issues, and can recur every ~60s poll interval with no
   natural throttle. A session-window pause has the opposite topology: `_handle_session_window_pause`
   writes the *global* `session-window-paused` sentinel, and every dispatch-guarded stage
   (`stage_conflict_resolve`, `stage_ready_implement`, `stage_blocked_retry`, `stage_plan`,
   `stage_refine`, plus epic-autopilot and the main-red recheck/fixer) is frozen until the parsed
   resume epoch — there is no per-ticket amplification channel to bound. `compute_resume_epoch` is
   already clamped to `now + 5h + buffer`, so the worst case is bounded by wall-clock time, shared
   factory-wide, regardless of how many times a specific issue happens to be the one paused. The
   `max(0, n-1)` clamp (Requirement 1) is the only bound this correction needs: it can restore a
   counter to its pre-dispatch value, never mint budget above it.
4. `record_failure_signature`'s existing behavior — it always overwrites the stored `<key>:sig`
   regardless of class, and never reports `stuck=true` for a non-`substantive:` value — is relied on
   unchanged. A pause landing between two identical substantive failures resets the
   consecutive-signature breaker's memory (identical to today's behavior for `environmental:
   delivery_failure`); this errs toward delaying a trip rather than causing a false one. Documented
   here, not fixed — no machinery to preserve prior signature history is in scope.
5. `entrypoint.sh`'s `_handle_session_window_pause()` additionally writes an
   `error-signatures/{issue}.{phase}.sig` drop file (identical JSON shape to
   `error_signature.write_signature()`'s existing output: `{"signature": "environmental:
   session_window_pause", "phase": ..., "exit_code": 0}`) for the issue/phase currently in flight,
   using the same `_failure_phase_for_intent()` mapping `_write_error_signature()` already uses. Guard
   identically to `_write_error_signature()`: `[ -z "${ISSUE_NUM:-}" ] && return` (skip for
   issue-less runs like "Recheck main"/"Fix main"). This is a **direct** write of an
   already-confirmed classification — it does not go through `error_signature.classify()`, which
   only classifies *failure* text; the pause classification already happened inside
   `session_window.py` before `_handle_session_window_pause` ever returns 0.
6. Consumption / idempotency: rely on `record_failure_signature`'s existing `drop_file.unlink()` — the
   scheduler's `check_failure_signature` call already consumes the drop file exactly once per read, so
   the decrement fires exactly once per observed pause, never once per poll. This must be an explicit
   regression test (Architecture / Testing), not an inherited accident — an unconsumed drop file would
   pin the counter at (or below) its pre-pause value on every subsequent poll.
7. Operator visibility:
   - **(a) Log every decrement** via the existing `[timestamp] ... issue=#N phase=... action=...`
     shape, e.g. `session_window_gate issue=#N phase=X action=retry_decrement count=<n-1>/<ceiling>` —
     grep-able local evidence, and the mechanism for spotting a rapid re-pause cycle (see Open
     Questions) even though this ticket does not act on that case.
   - **(b) For refine and plan**, which already post a "Starting…" comment on every dispatch, append
     one line to that existing comment when the *previous* attempt for this issue+phase was a
     confirmed pause — parallel to `delivery_skip_note()`/`PREV_DELIVERY_SKIP` (#279), a new
     `session_window_pause_note()` gated on a new `PREV_SESSION_WINDOW_PAUSE` variable. Do not add a
     new comment.
   - **(c) For implement and resolve** (no per-dispatch comment exists today), rely on the scheduler
     log (7a) only — matches #279's same asymmetry rationale (every established comment site in this
     file marks a state transition, not a per-retry/per-pause event).
8. No new config knob, no new `breaker.py` persisted key, and — unlike #279 — **no change to
   `reset_retry`**: because there is no `<key>:pause`-style shadow counter (Requirement 3), there is
   nothing new for `reset_retry` to pop. This is a simplification relative to #279's `:delivery` pop,
   worth calling out explicitly so a reviewer doesn't go looking for a parallel addition that isn't
   needed here.
9. No change to: `environmental:delivery_failure` handling, the early "stuck" trip for two consecutive
   `substantive:*` signatures, `environmental:preview_infra`/`environmental:rate_limit`, or the global
   pause sentinel / `session-window-paused` mechanics themselves — those are unmodified and out of
   scope (see Open Questions).

---

## Brainstorming Q&A

> **Q:** `scheduler.sh` tracks retry counters for four distinct dispatch call sites (refine, plan,
> implement, resolve) sharing the same increment-at-dispatch-time architecture. The issue's concrete
> evidence only covers plan (#19) and refine (#207). Should the rollback apply uniformly to all four
> phases, or scope to only the two with concrete evidence?
>
> **A:** All four, uniformly, in this ticket. The pause path is intent-agnostic by construction —
> `_handle_session_window_pause` never inspects `$INTENT` beyond recording it, so a window exhaustion
> mid-implement produces exactly the same state as mid-plan; fixing only refine/plan would leave a
> known-identical bug live on the phase with the longest runs (implement). #279 set direct precedent
> for landing this exact shape (an attempt that never produced a verdict must not consume a retry)
> across all four call sites in one reviewed ticket, avoiding two separate passes over breaker code
> that CLAUDE.md's "gate changes get their own reviewed ticket" rule makes expensive to repeat. Caveat:
> the resolve/deconflict site (`stage_conflict_resolve`) has a bespoke two-step peek-then-increment
> shape unlike the other three's shared helper — #279 deferred its shadow-counter increment to the
> `CONFLICTING` dispatch branch specifically because it was counting a *future* dispatch. A pause
> rollback corrects *history*, so (per the next answer) it does not inherit that deferral.

> **Q:** Given that the prior dispatch's increment already happened but nothing marks it for
> rollback, and #279's precedent only ever diverts *future* counts (never rolls back a *past*
> increment) — should this ticket implement (a) a true rollback: write a new
> `environmental:session_window_pause` signature via the same drop-file channel #279 built, and have
> the scheduler's decision function decrement the counter on seeing it; or (b) defer `increment_retry`
> entirely, so counting only happens after a later poll confirms the previous dispatch reached a real
> verdict (a larger, all-four-sites control-flow change)?
>
> **A:** (a) — the signature-drop rollback; not (b) in this ticket. (a) is literally the issue's
> proposed fix and its named acceptance test ("dispatch → pause → resume must leave the retry count
> unchanged"). (b) would flip the breaker's fail direction: dispatch-time pre-increment is
> fail-**closed** (any run whose outcome the scheduler can't observe — OOM, host restart, a `docker
> ps` miss — still counts against the ceiling); post-increment-on-confirmed-verdict is fail-**open**
> (an unobservable verdict never counts, silently disabling the ceiling for a chronically-cursed
> ticket). CLAUDE.md's hard limits forbid weakening breaker semantics as a side effect of a bugfix
> ticket, which is exactly what (b) would do while wearing this ticket's label. (a) reuses a
> already-proven, correctly-shaped channel: `error-signatures/{issue}.{phase}.sig` is per-issue-and-
> phase, while the pause sentinel is global — with `factory_wip_limit: 1` and `refine.wip_limit: 2`
> allowing multiple runs in flight, a single window exhaustion can pause several issues at once, and
> only per-issue drop files (not the global sentinel) can say which ones need a rollback. Naming the
> new value with the `environmental:` prefix inherits `record_failure_signature`'s existing "never
> reports `stuck=true`" guarantee for free — no new logic needed to keep two consecutive pauses from
> tripping the early-stuck breaker. The resolve-site caveat from the first answer actually
> *simplifies* under (a): #279 deferred its shadow increment to the `CONFLICTING` branch because it
> was counting a future dispatch; a pause rollback corrects history, so it must be unconditional and
> un-deferred — it sits at resolve's existing ceiling-check checkpoint, same as the other three, with
> no split required.

> **Q:** #279's `environmental:delivery_failure` diversion is capped by a shadow counter because a
> delivery failure can recur every poll interval with no natural throttle. Does the new
> `environmental:session_window_pause` decrement need an analogous cap, or is it already self-throttled
> because every genuine pause triggers the *global* sentinel that blocks all dispatch (every issue,
> every phase) until a resume epoch bounded by Claude's real 5h window plus buffer (or the 30-minute
> fallback) — making the worst case bounded by wall-clock time regardless of how many times one issue
> happens to be the one paused?
>
> **A:** No cap. The self-throttling reasoning is correct and holds against the code:
> `write_pause_sentinel()` writes the global `session-window-paused` file, and `scheduler.sh`'s stage
> guard table gates every dispatch stage on it (plus epic-autopilot and the main-red recheck/fixer) —
> there is no per-ticket amplification channel a cap would need to bound, unlike delivery_failure's
> per-issue, unthrottled recurrence. Two properties are the real (and sufficient) cap instead, and
> must be preserved as explicit tests rather than inherited accidents: (1) `record_failure_signature`
> consumes (`unlink`s) the drop file, so the decrement fires exactly once per observed pause, never
> once per poll; (2) the `max(0, n-1)` clamp means the decrement can only ever restore the counter to
> its pre-dispatch value, never mint budget above `MAX_RETRIES`/`REFINE_MAX_RETRIES`. Together these
> are why this is not a fail-open weakening under CLAUDE.md's hard limits. With no shadow counter,
> there is also no new persisted key for `reset_retry` to pop (unlike #279's `:delivery` — contrast
> `breaker.py`'s `reset_retry`, which pops `key`, `key:sig`, and `key:delivery` today; this ticket adds
> nothing there). Instead of a cap, require a distinct log line at the decrement site
> (`session_window_gate issue=#N phase=X action=retry_decrement count=<n>/<ceiling>`) so chronic
> recurrence stays visible to an operator — `compute_resume_epoch` has no lower floor, so a rapid
> re-pause cycle is possible in principle; observability, not a counter, is the right mitigation, and
> fixing that floor is explicitly out of scope for this ticket (see Open Questions).

---

## Architecture / Approach

**`scripts/factory_core/error_signature.py` addition** — a module-level constant documenting the new
signature value's provenance (written directly by the pause path, never returned by `classify()`):

```python
# Written directly by entrypoint.sh's _handle_session_window_pause() via a dedicated CLI
# subcommand, bypassing classify() entirely — the pause classification already happened in
# session_window.py before that function returns 0. Included here (not just as a bash literal)
# so scheduler.sh's hardcoded comparison and this module's writer share one canonical source
# a reader can find by grepping the module, mirroring how "environmental:delivery_failure" is
# both classify()'s return value and retry_or_skip_delivery_failure()'s bash literal.
SESSION_WINDOW_PAUSE_SIGNATURE = "environmental:session_window_pause"
```

**`scripts/factory_core/cli.py` addition** — a thin subcommand mirroring `error-signature-write`'s
shape but skipping `classify()` (the classification is already known):

```python
def _session_window_pause_signature_write(args):
    from factory_core.error_signature import SESSION_WINDOW_PAUSE_SIGNATURE, write_signature
    write_signature(args.issue, args.phase, SESSION_WINDOW_PAUSE_SIGNATURE, 0, Path(args.state_dir))
```

```python
swp = sub.add_parser("session-window-pause-signature-write")
swp.add_argument("--issue", type=int, required=True)
swp.add_argument("--phase", required=True)
swp.add_argument("--state-dir", default="/var/lib/dark-factory")
swp.set_defaults(func=_session_window_pause_signature_write)
```

**`entrypoint.sh`** — `_handle_session_window_pause()` gains one guarded call, placed after `matched`
is confirmed true and before (or alongside) the existing `run-record record --stage paused` call:

```bash
if [ -n "${ISSUE_NUM:-}" ]; then
  python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" session-window-pause-signature-write \
    --issue "$ISSUE_NUM" \
    --phase "$(_failure_phase_for_intent)" \
    --state-dir "${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}" || true
fi
```

**`scheduler.sh` addition — a new shared function**, mirroring the file's existing extracted-helper
style (`check_failure_signature`, `retry_or_skip_delivery_failure`):

```bash
# --- Session-window pause rollback (corrects history; unconditional, never deferred) ---
# Usage: rollback_paused_retry <issue_num> <phase> <sig_value> <retry_key> <ceiling>
# When sig_value is "environmental:session_window_pause" (written only by
# entrypoint.sh's _handle_session_window_pause, gated by #344's structured-evidence
# classifier), the prior dispatch for retry_key never reached a verdict — its optimistic
# increment_retry is decremented (clamped at 0) so the caller's immediately-following
# get_retry_count/ceiling-check/increment_retry sequence treats this attempt as if the
# paused one had never counted. No-op for every other sig_value (including "").
rollback_paused_retry() {
  local issue_num="$1" phase="$2" sig_value="$3" retry_key="$4" ceiling="$5"
  [ "$sig_value" = "environmental:session_window_pause" ] || return 0
  local cur new
  cur=$(get_retry_count "$retry_key")
  new=$(( cur > 0 ? cur - 1 : 0 ))
  STATE_FILE="$STATE_FILE" python3 "$FACTORY_CORE_CLI" breaker-set-retry --key "$retry_key" --value "$new"
  echo "[$(date -u +%FT%TZ)] session_window_gate issue=#${issue_num} phase=${phase} action=retry_decrement count=${new}/${ceiling}" >&2
}
```

Each of the four call sites inserts one call immediately after its existing `check_failure_signature` /
`SIG_VALUE` extraction and before its existing ceiling-check logic (which is otherwise unchanged).
Example (refine; plan and implement follow the same shape):

```bash
SIG_RESULT=$(check_failure_signature "$ISSUE" "refine")
SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
if echo "$SIG_RESULT" | grep -q "stuck=true"; then ...trip_to_blocked...; continue; fi

PREV_SESSION_WINDOW_PAUSE=""
[ "$SIG_VALUE" = "environmental:session_window_pause" ] && PREV_SESSION_WINDOW_PAUSE=1
rollback_paused_retry "$ISSUE" "refine" "$SIG_VALUE" "${ISSUE}:refine" "$REFINE_MAX_RETRIES"

PREV_DELIVERY_SKIP=""
DECISION=$(retry_or_skip_delivery_failure "$ISSUE" "refine" "$SIG_VALUE" "${ISSUE}:refine" "$REFINE_MAX_RETRIES" || echo "count")
case "$DECISION" in
  skip) PREV_DELIVERY_SKIP=1 ;;
  trip:*) trip_to_blocked "$ISSUE" "refine" "${DECISION#trip:}"; continue ;;
  count|*)
    RETRIES=$(get_retry_count "${ISSUE}:refine")
    if [ "$RETRIES" -ge "$REFINE_MAX_RETRIES" ]; then
      trip_to_blocked "$ISSUE" "refine" "retry limit of ${REFINE_MAX_RETRIES} reached"
      continue
    fi
    increment_retry "${ISSUE}:refine"
    ;;
esac
# ...existing "Starting..." comment body gains the delivery note AND (new) the session-window
# pause note when PREV_SESSION_WINDOW_PAUSE is set...
```

Note `rollback_paused_retry` runs *before* `retry_or_skip_delivery_failure` and is a no-op for any
`sig_value` that isn't the pause signature, so the two helpers compose without interaction: a signature
is exactly one value per poll (the drop file is consumed on read), so a given poll's `SIG_VALUE` can
match at most one of "pause" or "delivery_failure" — never both.

`stage_conflict_resolve` inserts the same unconditional call at its existing ceiling-check checkpoint
(no split needed, per the Q&A) — it does **not** need the two-step treatment `retry_or_skip_delivery_
failure` requires there, since the rollback isn't gating a *future* dispatch decision the way the
delivery-failure shadow counter is.

**Shared comment-note helper** (parallel to `delivery_skip_note()`):

```bash
# --- Shared "previous attempt hit a confirmed session-window pause" issue-comment note ---
# Callers must set PREV_SESSION_WINDOW_PAUSE (non-empty to include the note) before calling.
session_window_pause_note() {
  if [ -n "$PREV_SESSION_WINDOW_PAUSE" ]; then
    cat <<EOF


> ⏸️ The previous attempt was paused for a Claude session-window exhaustion and was not
> counted against the retry budget.
EOF
  fi
}
```

Wired into `stage_plan`'s and `stage_refine`'s existing "Starting…" comment bodies alongside
`DELIVERY_NOTE`, e.g. `SESSION_WINDOW_NOTE=$(session_window_pause_note)` appended the same way
`DELIVERY_NOTE` is today. `stage_blocked_retry` (implement) and `stage_conflict_resolve` (resolve) get
no new comment — the scheduler log line (Requirement 7a) is their only visibility, matching #279's
existing implement/resolve asymmetry.

**Test changes** (mirroring the existing `#279 regression` coverage style):

- `tests/test_scheduler.sh`: extend with cases covering (a) a single confirmed pause does not leave the
  normal retry key net-incremented across dispatch→pause→resume, for at least one adjacent-shape phase
  (e.g. refine) and the resolve shape; (b) the decrement clamps at 0 (a pause observed when the counter
  is already 0, e.g. after a `reset_retry`, does not go negative); (c) the drop file is consumed exactly
  once — a second poll without a new pause does not decrement again; (d) `environmental:
  session_window_pause` never yields `stuck=true` even on repeat.
- `tests/test_entrypoint_session_window.sh`: extend to assert `_handle_session_window_pause` calls
  `session-window-pause-signature-write` with the correct `--issue`/`--phase` (via
  `_failure_phase_for_intent`) when `ISSUE_NUM` is set, and does not call it when unset.
- `tests/test_factory_core_error_signature.py`: unit test for the new
  `session-window-pause-signature-write` CLI path writing the exact JSON shape
  `record_failure_signature` expects, independent of `classify()`.

---

## Reviewer notes (2026-08-27)

- CI does not run `tests/test_scheduler.sh`; this ticket's dispatch/pause/resume tests are
  local-only (same pre-existing gap as the #279 tests they mirror). The plan's final
  verification must run `bash tests/test_scheduler.sh` explicitly and state the gap; wiring the
  scheduler suite into CI is a separate ticket — do not add the ci.yml line here.
- When copying the `_write_error_signature` guard, use the existing `return 0` form (not bare
  `return`) — it matters under `set -e` at end of function.


## Alternatives Considered

1. **Defer `increment_retry` to post-verdict confirmation** (Q&A option (b)). Rejected: flips the
   breaker's fail direction from fail-closed to fail-open — an unobservable run outcome (host restart,
   OOM, a `docker ps` miss) would never count against the ceiling, silently disabling it for a
   chronically-cursed ticket. CLAUDE.md's hard limits forbid weakening breaker semantics as a side
   effect of a bugfix ticket; this ticket's scope is the pause case specifically, not a broader
   accounting-timing redesign.
2. **Cap the rollback with a shadow counter**, mirroring #279's `<key>:delivery`. Rejected: a
   delivery failure is per-issue and unthrottled (can recur every poll interval); a session-window
   pause is global and self-throttled by the existing `session-window-paused` sentinel, which already
   freezes all dispatch until a wall-clock-bounded resume epoch. A cap here would add persisted state
   and a `reset_retry` change with no corresponding risk to bound.
3. **Increment the normal counter for paused attempts too, but exempt them from ever counting toward a
   trip** (no decrement, just a "sticky exemption" flag). Rejected: `trip_to_blocked`'s comment reads
   `get_retry_count(key)` directly and reports it as "attempted N time(s)" — a counter that's
   sometimes real and sometimes not makes that number meaningless, and (as with #279's rejected
   equivalent) doesn't correct the actual bug: the counter would still read one-too-high for every
   poll between the pause and its next real dispatch.
4. **Post a new GitHub comment for every rollback**, rather than appending to the existing per-dispatch
   comment. Rejected: for refine/plan, both already post a "Starting…" comment on every dispatch, so a
   new comment would duplicate; for implement/resolve, no established comment site exists for a
   per-retry event (only state transitions), matching #279's identical rejection of a per-skip comment.

---

## Open Questions (Non-blocking)

- `compute_resume_epoch` has no lower floor — a `resetsAt` at or slightly before `now` yields a resume
  only `SESSION_WINDOW_BUFFER_MINUTES` (default 5) out, so a rapid re-pause cycle is possible in
  principle. This spec's new `session_window_gate ... action=retry_decrement` log line is the
  telemetry an operator would use to notice that pattern; actually bounding it (e.g. a minimum resume
  gap) is explicitly out of scope here and would need its own reviewed ticket against the pause-sentinel
  mechanics themselves.
- Whether `environmental:preview_infra` or `environmental:rate_limit` (the `error_signature.classify()`
  buckets, distinct from this ticket's direct-write pause signature) should ever get similar rollback
  treatment is out of scope — those represent runs that *did* fail after actually attempting work,
  unlike a pause, and would need their own review against CLAUDE.md's "gate changes get their own
  reviewed ticket" limit.

---

## Assumptions

- "The four scheduler.sh retry call sites" refers to `stage_blocked_retry` (implement),
  `stage_plan` (plan), `stage_refine` (refine), and `stage_conflict_resolve` (resolve) — located by
  function name in current `main`, not by the line numbers cited in this document's Architecture
  section, which will drift; re-locate by function name per the existing memory lesson from issue #182
  ("always re-verify line-number citations ... against current main").
- `breaker.py`'s `set_retry_count()` (added by #279, unchanged here) and the `breaker-set-retry` CLI
  subcommand (also #279) are reused as-is for the decrement — no new `breaker.py` function is needed;
  the clamp-at-0 arithmetic lives in the bash helper (`rollback_paused_retry`), matching how
  `retry_or_skip_delivery_failure`'s existing back-fill arithmetic also lives in bash rather than
  python.
- `_failure_phase_for_intent()` (entrypoint.sh) is the correct, already-existing phase-mapping
  function for the new signature write — same mapping `_write_error_signature()` already uses, so the
  written phase always matches what `scheduler.sh`'s `check_failure_signature "$ISSUE" "<phase>"` call
  will look up.
- No config.yaml or env-var addition is needed; `REFINE_MAX_RETRIES`/`MAX_RETRIES` remain the only
  ceilings involved, consistent with `scheduler.sh`'s existing "`REFINE_MAX_RETRIES` is not in
  config.yaml by design" convention.
