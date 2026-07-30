# Skip the Retry-Budget Counter for Runner-Side Delivery Failures

**Issue:** omniscient/dark-factory#279
**Status:** new spec, refined 2026-07-28 against current `main`.
**Related:** #212 (push/label artifact-gating — already shipped, cited below as prior art, not
in scope here), #33 (the `environmental:*`/`substantive:*` signature classification this ticket
extends), #214 (command-channel trust — unrelated but adjacent stranding-family ticket).

---

## Overview / Problem Statement

Agent nodes dispatched by the external Archon runner (baked at `/opt/archon`, not this repo)
intermittently receive a context-only message with no command text — the agent's whole transcript
is some variant of "I don't see a task, what would you like me to help with?" Captured evidence in
the issue (implement and conformance nodes, three 2026-07-13 refines) shows this burns a scheduler
retry and, pre-#212, stranded the ticket with a gate label and an empty branch. The issue's own
source-level investigation (comments) traces the fault to the runner's SDK→subprocess prompt
delivery layer, not this repo's `dag-executor.ts`-equivalent composition step — it is **not
fixable in this repo**. The issue proposes three angles; this spec scopes to exactly the one that
is both implementable here and not yet done:

1. **Artifact-gate push/label nodes (#212).** Already shipped and verified on current `main`:
   `refine-push` and `plan-push-and-advance` (`workflows/archon-dark-factory.yaml`) both call
   `scripts/push_gate_check.sh`, which checks for a committed, issue-matching file on the branch
   (`git diff -z --name-only origin/main...HEAD`) before pushing/labeling — an instruction-less run
   can no longer strand a ticket with a gate label and nothing to review. **Out of scope here.**
2. **A cheap tripwire in each command's preamble: classify a fast, artifact-less, commit-less,
   clean-worktree failure as a delivery failure, not a ticket failure, and don't burn a breaker
   retry on it.** The *classification* half is already shipped: `entrypoint.sh` computes
   `elapsed_seconds`/`commits_since_start`/`worktree_dirty`/`artifact_present` for every failed run
   and calls `cli.py error-signature-write`, which invokes
   `scripts/factory_core/error_signature.py`'s `classify()` — returning
   `"environmental:delivery_failure"` exactly when `elapsed_seconds < 30 and commits_since_start ==
   0 and not worktree_dirty and not artifact_present` (default threshold, `DELIVERY_FAILURE_MAX_SECONDS`).
   This is covered by an explicit `#279 regression` test in `tests/test_entrypoint_error_signature.sh`.
   `scheduler.sh`'s `check_failure_signature()` already reads this classification back via
   `breaker.py`'s `record_failure_signature()`, which — by design (#33) — never reports the early
   "stuck" trip for an `environmental:*` signature (`tests/test_scheduler.sh`: "environmental repeat
   never trips (mirrors #279)"). **The "no breaker increment" half is NOT done**: all four of
   `scheduler.sh`'s retry-counter call sites (`stage_blocked_retry`/implement,
   `stage_plan`/plan, `stage_refine`/refine, `stage_conflict_resolve`/resolve) call
   `increment_retry()` **unconditionally** — the classified signature `check_failure_signature`
   already returns in `$SIG_RESULT` is read only to build the `trip_to_blocked` reason message when
   `stuck=true`; it is otherwise discarded. **This is this spec's scope.**
3. **Report the bug upstream to Archon with transcripts.** Not a code deliverable in this repo — the
   issue's own comment thread already contains a detailed source-level trace (file/line citations
   into the external runner) that constitutes most of that report. **Out of scope here**; see Open
   Questions.

This is intentional, in-scope modification of the breaker/retry mechanism, requested directly by
the issue that owns it — not an incidental weakening of a safety gate as a side effect of unrelated
work (the case CLAUDE.md's hard limit is aimed at). The exemption below is narrowly scoped to one
already-tested classification, is capped so a chronically-cursed ticket's worst-case dispatch count
matches today's behavior exactly (see Requirement 4 and the Q&A), and no other failure
classification's handling changes.

---

## Requirements

1. When `check_failure_signature` returns signature exactly `environmental:delivery_failure` (and
   `stuck=false` — it always is for `environmental:*`, per #33), the phase's normal retry counter
   (`increment_retry`) must **not** be incremented for that attempt; the phase dispatches again as
   if the attempt hadn't counted.
2. Apply uniformly to all four `scheduler.sh` retry sites: `stage_blocked_retry` (implement, keyed
   `"$ISSUE"`, ceiling `$MAX_RETRIES`), `stage_plan` (plan, keyed `"${ISSUE}:plan"`, ceiling
   `$REFINE_MAX_RETRIES`), `stage_refine` (refine, keyed `"${ISSUE}:refine"`, ceiling
   `$REFINE_MAX_RETRIES`), `stage_conflict_resolve` (resolve, keyed `"${ISSUE}:resolve"`, ceiling
   `$MAX_RETRIES`). The underlying runner bug is phase-agnostic (same external delivery path for
   every agent node) and detection is already uniform across all four — do not scope narrower.
   `resolve`'s wiring point differs from the other three: its ceiling check (today, line ~803) and
   its `increment_retry` call (today, line ~815) are not adjacent — the increment only fires inside
   the `CONFLICTING)` case branch, after a `get_pr_for_issue`/`check_pr_mergeable` round-trip, not
   immediately after the ceiling check like the other three phases. The new logic must preserve that
   two-step shape (decide-to-trip at the existing checkpoint; count/skip at the existing increment
   point), not collapse it.
3. Bound the exemption. Track consecutive `environmental:delivery_failure` occurrences in a new
   per-key counter, `<key>:delivery` (e.g. `42:refine:delivery`), parallel to the existing `<key>:sig`
   entry `record_failure_signature` already stores in `scheduler-state.json`. Cap it at the **same**
   threshold already governing that call site (`MAX_RETRIES` for implement/resolve,
   `REFINE_MAX_RETRIES` for plan/refine) — introduce no new config knob or env var.
4. On cap exceeded (the `<key>:delivery` counter would reach the ceiling): back-fill — set the
   phase's normal counted retry total to that value (so it is `>= ceiling`) and trip to Blocked
   through the existing `RETRIES -ge <ceiling>` mechanism, with a reason string naming
   `environmental:delivery_failure`, the consecutive count, and `#279` (not the generic "retry limit
   of N reached" message), so a human reading the trip comment immediately understands this is a
   suspected runner-delivery bug, not N repeated substantive failures. Back-filling (rather than
   counting only the capping attempt) keeps the worst-case dispatch count for a chronically-cursed
   ticket×phase identical to today's behavior — a ticket that's cursed on every attempt still trips
   after exactly `ceiling` dispatches, not `ceiling + ceiling`.
5. `breaker.py`'s `reset_retry(key)` must also pop `f"{key}:delivery"`, alongside its existing
   `key` and `f"{key}:sig"` pops, so a ticket that clears Blocked (human removes
   `needs-discussion`, or a later success/advance calls `reset_retry`) does not inherit a banked
   delivery-skip count from a prior, unrelated episode.
6. Visibility:
   - **(a) Log every skipped (uncounted) attempt** via a scheduler log line in the existing
     `[timestamp] ... issue=#N phase=... action=...` shape (matching `stage_conflict_resolve`'s
     `conflict_gate` line), e.g. `action=delivery_failure_skip count=<n>/<ceiling>` — the local
     evidence stream an operator (or a future upstream Archon bug report) can grep for frequency.
   - **(b) For refine and plan**, which already post a "Starting…" comment on every dispatch
     (`scheduler.sh:1037`, `:989`), append one line to that existing comment when the *previous*
     attempt was a skipped delivery failure, noting the classification and that it wasn't counted
     against the retry budget, with a `#279` pointer. **Do not add a new comment** — this reuses the
     comment already being posted.
   - **(c) For implement and resolve** (no per-dispatch comment exists today), rely on the scheduler
     log (6a) plus the cap-exceeded trip comment (Requirement 4, which already names the fault) — do
     not introduce a new per-attempt comment for these two phases either.
7. No change to: #212's artifact-gating; the early "stuck" trip for two consecutive `substantive:*`
   signatures; or any other `environmental:*` class (`preview_infra`, `rate_limit`) — those have
   their own handling and are explicitly out of scope (see Open Questions).

---

## Brainstorming Q&A

> **Q:** Angle 1 (#212) is already shipped, and angle 3 (report upstream) isn't implementable in
> this repo. That leaves the wiring gap — scheduler.sh discards the classified signature it already
> has in `$SIG_RESULT` instead of using it to skip `increment_retry` — as the only concretely
> implementable scope. Should the fix apply uniformly to all four of scheduler.sh's retry call sites
> (implement, plan, refine, resolve), or scope narrower (e.g. only refine/plan, where #212's
> mitigation also lives, or only refine/plan/implement, matching the issue's captured evidence)?
>
> **A:** Apply it uniformly to all four. The bug is phase-agnostic by mechanism — same external
> runner prompt-delivery path for every agent node — and the issue body itself lists evidence across
> implement, conformance, and refine; captured evidence reflects where it was *observed*, not where
> it can occur, so scoping to the observed phases would encode sampling bias as policy. The detection
> layer (`entrypoint.sh` + `error_signature.py`) is already uniform across all four scheduler phases,
> so a partial rollout would mean writing a phase allowlist that exists only to be deleted later.
> `resolve` matters as much as the others — a deconflict node dying with no command text produces the
> identical signature and burns one of only `MAX_RETRIES` attempts against a PR with real conflicts —
> but its wiring point differs: `increment_retry "${ISSUE}:resolve"` sits inside the `CONFLICTING)`
> branch after the PR/mergeable lookups, not immediately after the ceiling check like the other
> three; the spec should call that out explicitly. Two constraints recommended as the price of the
> uniform rollout: (a) bound the skip with a separate, capped per-ticket×phase counter (since
> `environmental:*` signatures are deliberately exempt from the early "stuck" trip per #33, and the
> issue itself reports this failure is "sticky per ticket×phase across hours" — the sustained case,
> not a rare tail); (b) log every skipped increment so operators can see delivery-failure frequency,
> feeding the issue's angle 3 (an eventual upstream Archon report).

> **Q:** Given the recommended design (a capped counter `<issue>:<phase>:delivery`, reset via the
> existing reset-adapter, falling through to the normal counted path past the cap): (a) should the
> fall-through at cap-exceeded count only that one capping attempt as "1 real retry," or back-fill
> all previously-skipped attempts into the counted total at that point? (b) should a skipped
> (uncounted) delivery-failure attempt also post a GitHub issue comment, given operators primarily
> watch tickets via the issue thread rather than raw scheduler logs — or is that too noisy for what's
> meant to be an invisible, slot-free retry?
>
> **A:** (a) **Back-fill.** Back-filling makes the worst case for a chronically-cursed ticket exactly
> equal to today's behavior (~`ceiling` dispatches then Blocked); not back-filling would let it run
> up to `2 × ceiling` dispatches before anyone is told, and per the issue's own stickiness evidence
> (hours-long curses, poll cycles minutes apart) nearly all of those extra dispatches would also be
> delivery failures — the carve-out is meant to make the factory more *tolerant* of a runner bug, not
> to double the volume of stranding debris a cursed ticket produces before a human sees it. The
> observed recoveries (#208 succeeded on attempt 3, #275's retry was pending) both fall *within* a
> cap sized to the phase's existing ceiling, so extra attempts past that cap have no demonstrated
> rescue value. Tie the cap to the phase's existing `MAX_RETRIES`/`REFINE_MAX_RETRIES` (not a new
   hardcoded number or config knob) so back-filling is guaranteed to cross the existing threshold by
> construction — the trip then happens through each site's existing `RETRIES -ge <ceiling>` check,
> with no new `trip_to_blocked` call site. The reason string must name the delivery classification,
> the consecutive count, and #279, not the generic "retry limit reached" message, or the trip comment
> gives a human the wrong diagnosis (looks like N *substantive* failures). Also flagged as a bug to
> pre-empt: `reset_retry` currently pops `key` and `key:sig` but would not pop the new `key:delivery`
> — without that, a human clearing `needs-discussion` on a tripped ticket would resume it with the
> delivery counter still banked at the cap, re-tripping on its very first subsequent delivery
> failure. Fix: add the `key:delivery` pop alongside the existing `key:sig` pop in `reset_retry`,
> which automatically covers every existing `reset_retry` call site.
> (b) **No new comment.** For refine/plan, a new per-skip comment would be pure duplication — both
> phases already post a "Starting…" comment on *every* dispatch, so a human watching the thread
> already sees one comment per attempt; instead, append a line to the comment that's already being
> posted, noting the previous attempt's classification. For implement/resolve, every established
> comment site in this file marks a state transition (CI-failing→Blocked, orphaned-run recovered,
> above-ceiling→Blocked, breaker trip), not a per-retry event — a skipped retry is deliberately
> neither, that's the point of "doesn't consume a slot." The common case is a single occurrence (5-6
> in ~20 runs, usually resolving next attempt); commenting on skip #1 would optimize the thread for
> the rare sticky case at the cost of noise on the common one. Thread-level visibility for the sticky
> case is instead carried by the cap-exceeded trip comment, which under (a) already names the
> delivery fault explicitly. Log-only for implement/resolve; append-to-existing-comment for
> refine/plan.

---

## Architecture / Approach

**`scripts/factory_core/breaker.py` additions:**

1. `set_retry_count(key, value, state_file=_DEFAULT_STATE)` — a thin wrapper reusing the existing
   `_write_key` helper (same pattern as `increment_retry`, but writes a caller-supplied value
   instead of `get_retry_count(...) + 1`). Used only for the Requirement 4 back-fill.
2. `reset_retry`: add `data.pop(f"{key}:delivery", None)` alongside the existing `data.pop(f"{key}:sig", None)` (breaker.py, current `reset_retry` body) — covers all five existing `reset_retry`
   call sites in `scheduler.sh` automatically, no call-site changes needed.

**`scheduler.sh` additions — a new shared decision function**, mirroring this file's existing
extracted-helper style (`check_failure_signature`, `trip_to_blocked`):

```
# Usage: DECISION=$(retry_or_skip_delivery_failure <issue_num> <phase> <sig_value> <retry_key> <ceiling>)
# Echoes one of:
#   "count"     - sig_value is not environmental:delivery_failure; caller proceeds with its
#                 existing get_retry_count/ceiling-check/increment_retry sequence unchanged.
#   "skip"      - delivery failure, under cap; delivery-skip counter incremented, logged, caller
#                 dispatches WITHOUT touching the normal retry counter or ceiling check.
#   "trip:<reason>" - delivery failure, cap reached; normal retry counter has been back-filled via
#                 set_retry_count; caller calls trip_to_blocked with the given reason and continues.
retry_or_skip_delivery_failure() {
  local issue_num="$1" phase="$2" sig_value="$3" retry_key="$4" ceiling="$5"
  if [ "$sig_value" != "environmental:delivery_failure" ]; then echo "count"; return; fi
  local dkey="${retry_key}:delivery"
  local dcount
  dcount=$(increment_retry "$dkey")   # reuse the existing generic key/counter adapter as-is
  if [ "$dcount" -lt "$ceiling" ]; then
    echo "[$(date -u +%FT%TZ)] delivery_gate issue=#${issue_num} phase=${phase} action=delivery_failure_skip count=${dcount}/${ceiling}"
    echo "skip"
  else
    STATE_FILE="$STATE_FILE" python3 "$FACTORY_CORE_CLI" breaker-set-retry --key "$retry_key" --value "$dcount"
    echo "trip:same failure signature 'environmental:delivery_failure' recorded ${dcount} consecutive times (suspected runner prompt-delivery bug — see #279), retry budget exhausted"
  fi
}
```

(`breaker-set-retry` is a new thin `cli.py` subcommand over `breaker.set_retry_count`, mirroring the
existing `breaker-check-signature`/`breaker-trip` subcommands' shape.)

Each of the four call sites replaces its `get_retry_count` → ceiling-check → `increment_retry`
sequence with a call into this helper, parsing `$SIG_RESULT`'s `sig=` field (already extracted
today for the `stuck=true` branch) and branching on the three outcomes. Example (refine; plan and
implement follow the same shape):

```bash
SIG_RESULT=$(check_failure_signature "$ISSUE" "refine")
if echo "$SIG_RESULT" | grep -q "stuck=true"; then ...trip_to_blocked...; continue; fi
SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)

DECISION=$(retry_or_skip_delivery_failure "$ISSUE" "refine" "$SIG_VALUE" "${ISSUE}:refine" "$REFINE_MAX_RETRIES")
case "$DECISION" in
  skip)
    PREV_DELIVERY_SKIP=1   # threads into the "Starting..." comment body (Requirement 6b)
    ;;
  trip:*)
    trip_to_blocked "$ISSUE" "refine" "${DECISION#trip:}"
    continue
    ;;
  count)
    RETRIES=$(get_retry_count "${ISSUE}:refine")
    if [ "$RETRIES" -ge "$REFINE_MAX_RETRIES" ]; then
      trip_to_blocked "$ISSUE" "refine" "retry limit of ${REFINE_MAX_RETRIES} reached"
      continue
    fi
    increment_retry "${ISSUE}:refine"
    ;;
esac
# ...existing "Starting..." comment body gains one appended line when $PREV_DELIVERY_SKIP is set...
```

For `stage_conflict_resolve` (resolve), per Requirement 2, the decision call happens at today's
ceiling-checkpoint (~line 803) to decide trip-vs-not (the `trip:`/`count` outcomes are handled
there), but the `skip` outcome's actual dispatch-without-counting only takes effect at today's
existing `increment_retry "${ISSUE}:resolve"` call site (~line 815, inside the `CONFLICTING)`
branch) — i.e. the helper's counter-increment/log side effect for the `skip` path should fire once,
at the point where a real dispatch will occur, not at the earlier checkpoint (which today only
*reads* `get_retry_count`, never increments). The plan/implement phase should treat this as the one
call site needing the two-step split; the other three collapse checkpoint and increment into one
call as shown above.

**Test changes** (mirroring the existing `#279 regression` coverage style):

- `tests/test_scheduler.sh`: extend with cases covering (a) a single `environmental:delivery_failure`
  skip does not increment the normal retry key, for at least one phase representative of the
  "adjacent" shape (e.g. refine) and the `resolve` two-step shape; (b) `ceiling` consecutive skips
  back-fill the normal counter and trip to Blocked with a reason mentioning
  `environmental:delivery_failure` and `#279`; (c) `reset_retry` clears the new `:delivery` key
  (regression for the pre-empted bug in Q2/A2).
- A small `factory_core` unit test (new or extended `test_breaker.py`-equivalent if one exists, else
  inline in `test_scheduler.sh`'s Python-adjacent fixtures) for `set_retry_count` and the
  `reset_retry` `:delivery` pop.

---

## Alternatives Considered

1. **Increment the same normal counter for delivery failures too, but exempt those attempts from
   ever counting toward a trip** (no separate counter). Rejected: `trip_to_blocked`'s comment reads
   `get_retry_count(key)` directly and reports it as "attempted N time(s)" — mixing delivery and
   substantive failures into one counter that sometimes counts and sometimes doesn't makes that
   number meaningless, and a permanently-uncapped exemption reopens the exact
   sticky-per-ticket×phase infinite-retry risk the Q&A flagged.
2. **Uncapped skip** (never fall through to the counted path for delivery failures). Rejected per
   Q&A: given the issue's own evidence that this failure is sustained rather than a rare one-off, an
   uncapped exemption could let one ticket retry indefinitely, silently monopolizing the WIP slot
   with zero human visibility until someone thinks to check scheduler logs — parking sooner (angle
   3's own "get this in front of a human" goal) is the desired outcome, not a cost.
3. **Scope narrower**: only refine/plan (where #212 also lives), or only refine/plan/implement
   (matching the issue's captured evidence). Rejected per Q&A: the runner bug is phase-agnostic, the
   detection layer is already uniform across all four scheduler-dispatched phases, and `resolve`
   (real PR conflicts, only `MAX_RETRIES` attempts) is equally exposed with no existing mitigation.
4. **Post a new GitHub comment on every skipped attempt.** Rejected per Q&A: pure duplication for
   refine/plan (already comment on every dispatch); for implement/resolve, every established comment
   site marks a status transition, not a per-retry event, and the common case (single occurrence,
   resolves next attempt) doesn't warrant per-attempt noise. Thread-level visibility for the sticky
   case is instead carried by the (already fault-naming) cap-exceeded trip comment.

---

## Open Questions (Non-blocking)

- Whether the same delivery-failure exemption should eventually extend to the other
  `environmental:*` classes (`preview_infra`, `rate_limit`) is explicitly out of scope here — those
  already have separate handling (`rate_limit` in particular is largely absorbed by the
  session-window pause mechanism before it reaches this retry loop) and would need their own review
  against CLAUDE.md's "gate changes get their own reviewed ticket" limit.
- Whether/when the issue's angle 3 (filing the empty-prompt bug upstream against the Archon runner,
  attaching the transcripts already captured in the issue's comments) happens is an operator/process
  action, not a code deliverable of this ticket. This spec's new scheduler log line (Requirement 6a)
  is offered as the ongoing local telemetry stream that would support such a report, should the
  operator choose to file it.

---

## Assumptions

- "The four scheduler.sh retry call sites" refers to `stage_blocked_retry` (implement,
  `scheduler.sh:913-953`), `stage_plan` (plan, `:956-998`), `stage_refine` (refine, `:1001-1046`), and
  `stage_conflict_resolve` (resolve, `:786-824`) — line numbers as of 2026-07-28's `main`; they will
  drift, and the implementing agent should re-locate by function name, not by citing these numbers
  verbatim, per an existing memory lesson (issue #182: "always re-verify line-number citations ...
  against current main").
- "No new config knob" (Requirement 3) follows the existing `REFINE_MAX_RETRIES` precedent — env-only,
  not added to `config.yaml`, per `scheduler.sh`'s own header comment ("`REFINE_MAX_RETRIES` is not
  in config.yaml by design"). The delivery-skip cap reuses whichever ceiling (`MAX_RETRIES` or
  `REFINE_MAX_RETRIES`) already governs that call site rather than introducing a new
  `DELIVERY_FAILURE_SKIP_CAP`-style variable.
- `breaker.py`'s new `set_retry_count()` is additive — it does not change `get_retry_count`'s or
  `increment_retry`'s existing signature or behavior for any other caller.
- The new `breaker-set-retry` CLI subcommand follows the exact argument/dispatch pattern already
  used by `breaker-trip` and `breaker-check-signature` in `cli.py` (not introduced from scratch).
