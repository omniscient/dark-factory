# Implementation Plan: Skip the Retry-Budget Counter for Runner-Side Delivery Failures

**Issue:** omniscient/dark-factory#279
**Spec:** `docs/superpowers/specs/2026-07-28-delivery-failure-retry-exemption-design.md`

---

## Goal

Wire the already-shipped `environmental:delivery_failure` classification (#33,
`error_signature.py`) into `scheduler.sh`'s four retry-counter call sites
(`stage_blocked_retry`/implement, `stage_plan`/plan, `stage_refine`/refine,
`stage_conflict_resolve`/resolve) so a runner-side empty-prompt delivery failure no
longer burns a counted breaker retry. The exemption is bounded by a new, capped shadow
counter (`<key>:delivery`) tied to each site's existing ceiling (`MAX_RETRIES` /
`REFINE_MAX_RETRIES`) — no new config knob. On cap-exceeded, the real counter is
back-filled and the ticket trips to Blocked exactly as today, so a chronically-cursed
ticket's worst-case dispatch volume is unchanged. `reset_retry` clears the new counter
alongside its existing pops. Function-name line citations below (e.g. `scheduler.sh:786`)
are current as of `main` at plan time and will drift — re-locate by function name
(`grep -n '^stage_conflict_resolve\|^check_failure_signature'` etc.), per the spec's own
assumption and the #182 memory lesson it cites.

## Architecture

```
scripts/factory_core/breaker.py
  + set_retry_count(key, value, state_file)        # back-fill adapter (Task 1)
  ~ reset_retry(key, state_file)                    # now also pops f"{key}:delivery"

scripts/factory_core/cli.py
  + breaker-set-retry --key --value                 # thin CLI wrapper (Task 1)

scheduler.sh
  + retry_or_skip_delivery_failure(issue, phase, sig_value, retry_key, ceiling)
        echoes "count" | "skip" | "trip:<reason>"    # shared helper (Task 2)
        reuses increment_retry() for the "<key>:delivery" shadow counter;
        calls `breaker-set-retry` to back-fill on cap-exceeded.

  stage_refine()          → calls the helper; "skip" appends a note to the existing
  stage_plan()               per-dispatch "Starting…" comment (Requirement 6b)  [Tasks 3, 4]
  stage_blocked_retry()   → calls the helper; log-only, no comment (Requirement 6c) [Task 5]
  stage_conflict_resolve()→ does NOT call the shared helper — see Design Note below;
                            reimplements the same 3-way decision split across the
                            existing checkpoint (~line 802) and increment point
                            (~line 815) to preserve the two-step shape [Task 6]
```

**Design Note — why `stage_conflict_resolve` doesn't call the shared helper.** The spec's
Requirement 2 mandates that the delivery-shadow-counter *increment* fire "at the point
where a real dispatch will occur" (the `CONFLICTING)` branch, ~line 815), not at the
earlier ceiling checkpoint (~line 802, which today only *reads* `get_retry_count`, never
increments — a cycle can reach that checkpoint and then bail with no dispatch at all, e.g.
`PR_NUM` empty or `MERGEABLE=UNKNOWN`). `retry_or_skip_delivery_failure()` always
increments the shadow counter as an inseparable side effect of returning its decision, so
calling it at the checkpoint would increment on cycles that never dispatch — violating
Requirement 2 and silently burning shadow-cap budget on no-op poll cycles. Task 6 instead
peeks the shadow counter (read-only) at the checkpoint to decide trip-vs-proceed, and only
increments it at the existing increment point, once a dispatch is actually about to
happen — same cap, same back-fill, same log format, same `<key>:delivery` naming
convention as the shared helper, just split across the two call points the spec's own
Requirement 2 describes. This is flagged explicitly for architect/conformance review.

**Accepted asymmetry (flagged for the conformance reviewer, not a defect).** The shared
helper increments *before* comparing (`dcount=$(increment_retry ...); if dcount <
ceiling`), so refine/plan/implement trip on the attempt that would be the `ceiling`-th
skip (i.e. after `ceiling - 1` actual dispatches). Task 6's peek-then-increment split for
resolve dispatches `ceiling` times before the following checkpoint peeks a shadow count
already at the ceiling and trips. Both match the spec's own Architecture-section
pseudocode arithmetic (which has the same increment-before-compare shape) — this plan
does not introduce the asymmetry, it inherits it, and preserves it faithfully rather than
"fixing" it into a spec deviation.

**Bash mechanics note — hardening `retry_or_skip_delivery_failure` against its own
internal failures.** `scheduler.sh` runs `set -euo pipefail` (and `set -E`). Bash
suppresses `-e` for the *entire dynamic extent* of a function call when that function is
invoked as the left operand of `||` — not just the top-level call, every command inside
it. Every call site here invokes the helper as
`DECISION=$(retry_or_skip_delivery_failure ... || echo "count")`, so if `increment_retry`
inside the helper failed, `-e` would NOT abort the helper at that point; execution would
continue with `dcount` empty, and the later `[ "$dcount" -lt "$ceiling" ]` would itself
error unpredictably rather than cleanly selecting a branch — worst case, a garbled
`trip:...recorded  consecutive times...` message (blank count) reaching
`trip_to_blocked`, which the outer `|| echo "count"` would never catch (the helper's own
`echo` still exits 0). The helper closes this directly: `dcount=$(increment_retry "$dkey"
|| echo "")` followed by `case "$dcount" in ''|*[!0-9]*) echo "count"; return ;; esac` —
any failure or non-numeric result falls back to `"count"` *before* the numeric comparison
is ever attempted. The call-site `|| echo "count"` and every `case "$DECISION")"`'s
`count|*)` arm remain as defense in depth against the function being entirely unreachable
(e.g. a typo), not as the primary safeguard. Net effect of any internal anomaly: fall back
to today's existing, already-reviewed ceiling-check/increment path — never an ungated
bypass, and never a garbled operator-facing trip reason. `stage_conflict_resolve`
(Task 6) doesn't call the helper, but applies the same `|| echo 0` capture pattern to its
own peek (`DPEEK=$(get_retry_count "${ISSUE}:resolve:delivery" || echo 0)`) and to its own
skip-branch increment (`DCOUNT=$(increment_retry "${ISSUE}:resolve:delivery" || echo 0)`,
matching the `|| true` already on its sibling branch's `increment_retry "${ISSUE}:resolve"
|| true`).

Separately: the helper's own diagnostic log line goes to `>&2` — not for `set -e` safety,
but so it never gets concatenated into `$DECISION` and breaks the `case` match (every call
site captures the helper's *entire* stdout).

## Tech Stack

- Python (`scripts/factory_core/breaker.py`, `cli.py`) — matches every existing
  `breaker-*` adapter's shape exactly (thin `argparse` subcommand → one function call).
- Bash (`scheduler.sh`) — new helper mirrors the file's existing extracted-helper style
  (`check_failure_signature`, `trip_to_blocked`); call-site edits are `case`-statement
  branches replacing today's linear `get_retry_count` → ceiling-check → `increment_retry`.
- `pytest` for `tests/test_factory_core_breaker.py` (unchanged framework).
- Bash `tests/test_scheduler.sh`'s existing stub/assert harness (`assert_eq`, `$STUB_LOG`,
  `_drop_sig`) — new sections follow the file's established letter-section convention.
  `D` through `R` are already used by other suites; new sections `S`–`W` are appended at
  the **end of the file**, directly after section `R`'s final assertion and before the
  `# ========== Cleanup` block — **not** inserted earlier in the file. Section `C`
  (`dispatch()` exit-code capture) and `C2` rely on `dispatch` being scheduler.sh's real,
  unstubbed function at the point they run; inserting new sections anywhere before `C`
  would either shadow that or read stale content depending on stub ordering. Each new
  section (`S`–`W`) is self-contained: it (re)defines every stub it depends on
  (`dispatch`, `gh`, `get_pr_for_issue`, `check_pr_mergeable`) at its own start rather than
  relying on whatever an earlier section last left those functions bound to.

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/breaker.py` | **Modified** — add `set_retry_count()`; `reset_retry()` pops `f"{key}:delivery"` |
| `scripts/factory_core/cli.py` | **Modified** — add `breaker-set-retry --key --value` subcommand |
| `tests/test_factory_core_breaker.py` | **Modified** — cover `set_retry_count` and the `reset_retry` `:delivery` pop |
| `scheduler.sh` | **Modified** — new `retry_or_skip_delivery_failure()` helper; four call sites (`stage_refine`, `stage_plan`, `stage_blocked_retry`, `stage_conflict_resolve`) rewired |
| `tests/test_scheduler.sh` | **Modified** — new sections `S` (helper), `T` (refine), `U` (plan), `V` (implement), `W` (resolve), appended at end of file |
| `docs/superpowers/specs/2026-07-28-delivery-failure-retry-exemption-design.md` | **Copied onto the feat branch** (Task 0) — not modified |

---

## Task 0: Bring the spec and plan onto the `feat/issue-279-*` branch

**Files:** `docs/superpowers/specs/2026-07-28-delivery-failure-retry-exemption-design.md`
(copied), `docs/superpowers/plans/2026-07-29-delivery-failure-retry-exemption.md` (copied)

Per an existing memory lesson (issue #42, `codebase-patterns.md`): a spec/plan approved on
a sibling `refine/issue-279-...` branch does **not** transfer automatically onto the
`feat/issue-279-...` branch the implement phase runs on — they must be copied and
committed explicitly, or PR #215's regression repeats (tests/README referencing a spec
path that was never committed to the feat branch).

1. Confirm both files are missing on the current (feat) branch and present on the refine
   branch:

```bash
git show refine/issue-279-bug-runner---agent-nodes-intermittently-:docs/superpowers/specs/2026-07-28-delivery-failure-retry-exemption-design.md > /tmp/279-spec.md
git show refine/issue-279-bug-runner---agent-nodes-intermittently-:docs/superpowers/plans/2026-07-29-delivery-failure-retry-exemption.md > /tmp/279-plan.md
ls docs/superpowers/specs/2026-07-28-delivery-failure-retry-exemption-design.md 2>&1
# ls: cannot access ...: No such file or directory   (expected — confirms the copy is needed)
```

2. Copy them into place and commit:

```bash
mkdir -p docs/superpowers/specs docs/superpowers/plans
cp /tmp/279-spec.md docs/superpowers/specs/2026-07-28-delivery-failure-retry-exemption-design.md
cp /tmp/279-plan.md docs/superpowers/plans/2026-07-29-delivery-failure-retry-exemption.md
git add docs/superpowers/specs/2026-07-28-delivery-failure-retry-exemption-design.md docs/superpowers/plans/2026-07-29-delivery-failure-retry-exemption.md
git commit -m "docs(279): bring spec and plan onto the feat branch (#279)"
```

(If the implement workflow's branch-setup step already copies these automatically by the
time this runs, this task is a no-op confirmation — verify with the `ls` check in step 1
before assuming step 2 is needed.)

---

## Task 1: `breaker.py` — `set_retry_count()` + `reset_retry` delivery-key pop, and the `cli.py` adapter

**Files:** `scripts/factory_core/breaker.py`, `scripts/factory_core/cli.py`,
`tests/test_factory_core_breaker.py`

### TDD Steps

1. Edit `tests/test_factory_core_breaker.py`'s import line (currently line 8-10):

```python
from factory_core.breaker import (
    get_retry_count, increment_retry, reset_retry, trip_to_blocked,
)
```

   to:

```python
from factory_core.breaker import (
    get_retry_count, increment_retry, reset_retry, set_retry_count, trip_to_blocked,
)
```

   Then append these tests after `test_atomic_write_survives_existing_file` (currently
   ending at line 70):

```python
def test_set_retry_count_writes_exact_value(tmp_path):
    sf = tmp_path / "state.json"
    set_retry_count("42:refine:delivery", 7, sf)
    assert get_retry_count("42:refine:delivery", sf) == 7


def test_set_retry_count_overwrites_existing_value(tmp_path):
    sf = tmp_path / "state.json"
    increment_retry("42:refine:delivery", sf)
    increment_retry("42:refine:delivery", sf)
    set_retry_count("42:refine:delivery", 3, sf)
    assert get_retry_count("42:refine:delivery", sf) == 3


def test_set_retry_count_does_not_disturb_other_keys(tmp_path):
    sf = tmp_path / "state.json"
    increment_retry("42:refine", sf)
    set_retry_count("42:refine:delivery", 3, sf)
    assert get_retry_count("42:refine", sf) == 1
```

   And append this test directly after `test_reset_retry_clears_stored_signature`
   (the file's current final test):

```python
def test_reset_retry_clears_delivery_shadow_counter(tmp_path):
    # Regression for #279 Requirement 5: a ticket resumed from Blocked (human removes
    # needs-discussion) must not inherit a banked delivery-skip count from a prior,
    # unrelated episode — otherwise it re-trips on its very first subsequent delivery
    # failure instead of getting a fresh cap.
    sf = tmp_path / "state.json"
    increment_retry("9:refine:delivery", sf)
    increment_retry("9:refine:delivery", sf)
    assert get_retry_count("9:refine:delivery", sf) == 2

    reset_retry("9:refine", sf)

    assert get_retry_count("9:refine:delivery", sf) == 0
```

2. Verify it fails (import error — `set_retry_count` doesn't exist yet):

```bash
python -m pytest tests/test_factory_core_breaker.py -v
# ImportError: cannot import name 'set_retry_count' from 'factory_core.breaker'
```

3. Implement in `scripts/factory_core/breaker.py`. Insert `set_retry_count` directly
   after `increment_retry` (currently lines 24-27):

```python
def increment_retry(key: str, state_file: Path = _DEFAULT_STATE) -> int:
    new = get_retry_count(key, state_file) + 1
    _write_key(key, new, state_file)
    return new


def set_retry_count(key: str, value: int, state_file: Path = _DEFAULT_STATE) -> None:
    """Write an explicit retry count, bypassing the +1 that increment_retry applies.
    Used only to back-fill the normal counter when a capped `<key>:delivery` shadow
    counter (#279) reaches its ceiling, so trip_to_blocked's "attempted N time(s)"
    report reflects the true dispatch count."""
    _write_key(key, value, state_file)
```

   Then edit `reset_retry` (currently lines 30-42) to also pop the delivery key:

```python
def reset_retry(key: str, state_file: Path = _DEFAULT_STATE) -> None:
    if not state_file.exists():
        return
    try:
        data = json.loads(state_file.read_text())
        data.pop(key, None)
        # Clear the stored failure signature alongside the retry counter so the
        # "two consecutive attempts" invariant in record_failure_signature() doesn't
        # survive a reset (success, Continue-dispatch, blocked-rescue, spec/plan
        # advance) — otherwise the first post-reset failure with a matching class
        # would trip the breaker one attempt early (#33 review).
        data.pop(f"{key}:sig", None)
        # Same reasoning for the capped delivery-failure shadow counter (#279): a
        # ticket resumed from Blocked must not inherit a banked count from a prior,
        # unrelated episode.
        data.pop(f"{key}:delivery", None)
        _atomic_write(state_file, data)
    except (json.JSONDecodeError, OSError):
        pass
```

4. Verify it passes:

```bash
python -m pytest tests/test_factory_core_breaker.py -v
# all tests pass
```

5. Add the `breaker-set-retry` CLI adapter. This is a thin one-line wrapper with no
   independent logic (matching `breaker-get`/`breaker-incr`/`breaker-reset`, none of
   which have a standalone Python-level test either — they're exercised end-to-end via
   `tests/test_scheduler.sh`'s bash harness, which Task 2 extends to cover this new
   subcommand through the shared helper's "trip" path). In `scripts/factory_core/cli.py`,
   insert `_breaker_set_retry` directly after `_breaker_trip` (currently lines 58-67):

```python
def _breaker_trip(args):
    from factory_core.breaker import trip_to_blocked
    state_file = Path(os.environ.get("STATE_FILE",
                                     "/var/lib/dark-factory/scheduler-state.json"))
    trip_to_blocked(
        issue_num=args.issue,
        phase=args.phase,
        reason=args.reason,
        state_file=state_file,
    )


def _breaker_set_retry(args):
    from factory_core.breaker import set_retry_count
    state_file = Path(os.environ.get("STATE_FILE",
                                     "/var/lib/dark-factory/scheduler-state.json"))
    set_retry_count(args.key, args.value, state_file)
```

   And register the subparser directly after the `bt = sub.add_parser("breaker-trip")`
   block (currently lines 235-239):

```python
    bt = sub.add_parser("breaker-trip")
    bt.add_argument("--issue", type=int, required=True)
    bt.add_argument("--phase", required=True)
    bt.add_argument("--reason", required=True)
    bt.set_defaults(func=_breaker_trip)

    bsr = sub.add_parser("breaker-set-retry")
    bsr.add_argument("--key", required=True)
    bsr.add_argument("--value", type=int, required=True)
    bsr.set_defaults(func=_breaker_set_retry)
```

6. Smoke-test the new subcommand directly (fast feedback before Task 2 wires it in):

```bash
TMP_STATE=$(mktemp /tmp/breaker-set-retry-smoke-XXXXXX.json)
echo '{}' > "$TMP_STATE"
STATE_FILE="$TMP_STATE" python3 scripts/factory_core/cli.py breaker-set-retry --key "1:resolve" --value 5
STATE_FILE="$TMP_STATE" python3 scripts/factory_core/cli.py breaker-get --key "1:resolve"
# 5
rm -f "$TMP_STATE"
```

7. Commit:

```bash
git add scripts/factory_core/breaker.py scripts/factory_core/cli.py tests/test_factory_core_breaker.py
git commit -m "feat(breaker): add set_retry_count and delivery-shadow-counter reset pop (#279)"
```

---

## Task 2: `scheduler.sh` — shared `retry_or_skip_delivery_failure()` helper

**Files:** `scheduler.sh`, `tests/test_scheduler.sh`

### TDD Steps

1. Capture the pre-change baseline (needed because the suite is not 100% green today —
   see the "Out of Scope" section's second bullet at the end of this plan):

```bash
bash tests/test_scheduler.sh 2>&1 | tail -3
# Results: 124 passed, 2 failed
# (the 2 failures — "G2: advance: set_board_status REFINED" and "I2: advance:
# set_board_status READY" — are pre-existing and unrelated to #279; every TDD step
# below is scored against "no NEW failures beyond this baseline", not "0 failed")
```

2. Append a new section to `tests/test_scheduler.sh`, at the **end of the file**,
   directly after section `R`'s final assertion (currently `R8b`, ending around line
   1176) and before the `# ========== Cleanup` block:

```bash
# ==========================================
# S: retry_or_skip_delivery_failure (#279 skip-retry-counter exemption)
# ==========================================
echo ""
echo "--- S: retry_or_skip_delivery_failure ---"
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"

assert_eq "S1: non-delivery signature returns count" "count" \
  "$(retry_or_skip_delivery_failure 60 refine "substantive:test_failure:1" "60:refine" 3)"
assert_eq "S1b: non-delivery signature creates no shadow counter" "0" \
  "$(get_retry_count "60:refine:delivery")"

assert_eq "S2: empty signature returns count" "count" \
  "$(retry_or_skip_delivery_failure 60 refine "" "60:refine" 3)"

echo '{}' > "$STATE_FILE"
D1=$(retry_or_skip_delivery_failure 61 refine "environmental:delivery_failure" "61:refine" 3)
assert_eq "S3: 1st delivery failure under cap returns skip" "skip" "$D1"
assert_eq "S3b: shadow counter incremented to 1" "1" "$(get_retry_count "61:refine:delivery")"
assert_eq "S3c: normal counter untouched" "0" "$(get_retry_count "61:refine")"

D2=$(retry_or_skip_delivery_failure 61 refine "environmental:delivery_failure" "61:refine" 3)
assert_eq "S4: 2nd delivery failure under cap returns skip" "skip" "$D2"
assert_eq "S4b: shadow counter incremented to 2" "2" "$(get_retry_count "61:refine:delivery")"

D3=$(retry_or_skip_delivery_failure 61 refine "environmental:delivery_failure" "61:refine" 3)
assert_eq "S5: 3rd delivery failure at cap returns a trip: decision" \
  "1" "$(echo "$D3" | grep -c '^trip:')"
assert_eq "S5b: trip reason names the consecutive count and #279" \
  "1" "$(echo "$D3" | grep -c "3 consecutive times.*#279")"
assert_eq "S5c: back-fill delegates to breaker-set-retry with the shadow count" \
  "1" "$(grep -c 'breaker-set-retry --key 61:refine --value 3' "$STUB_LOG" || echo 0)"
assert_eq "S5d: normal counter back-filled to 3" "3" "$(get_retry_count "61:refine")"

# S6: the diagnostic log line must go to stderr, not pollute the captured decision
echo '{}' > "$STATE_FILE"
D_CLEAN=$(retry_or_skip_delivery_failure 62 refine "environmental:delivery_failure" "62:refine" 5 2>/dev/null)
assert_eq "S6: decision value is exactly 'skip', not polluted by the log line" "skip" "$D_CLEAN"

# S7: reset_retry clears the shadow counter (#279 Requirement 5 / breaker.py Task 1)
echo '{}' > "$STATE_FILE"
retry_or_skip_delivery_failure 63 refine "environmental:delivery_failure" "63:refine" 3 > /dev/null
retry_or_skip_delivery_failure 63 refine "environmental:delivery_failure" "63:refine" 3 > /dev/null
assert_eq "S7: shadow counter at 2 before reset" "2" "$(get_retry_count "63:refine:delivery")"
reset_retry "63:refine"
assert_eq "S7b: shadow counter cleared by reset_retry" "0" "$(get_retry_count "63:refine:delivery")"

> "$STUB_LOG"
```

3. Verify it fails (function doesn't exist yet):

```bash
bash tests/test_scheduler.sh 2>&1 | grep -A2 "S1:"
# ...: retry_or_skip_delivery_failure: command not found
```

4. Implement in `scheduler.sh`: insert directly after `check_failure_signature`'s closing
   `}` (currently line 397) and before the `check_pr_mergeable` comment block (currently
   line 399):

```bash
# --- Skip the counted retry for a runner-side delivery failure (#279) ---
# Bounded by a capped shadow counter ("<retry_key>:delivery") so a chronically-cursed
# ticket's worst-case dispatch volume still matches today's behavior exactly once the
# cap is reached (back-fill + trip). Reuses increment_retry's existing key/counter
# adapter for the shadow counter — no new bash/python plumbing beyond breaker-set-retry.
# Usage: DECISION=$(retry_or_skip_delivery_failure <issue_num> <phase> <sig_value> <retry_key> <ceiling> || echo "count")
# Callers MUST append `|| echo "count"` at the capture site — see the plan's "Bash
# mechanics note" for why (safe fail-toward-counted behavior under set -euo pipefail).
# Echoes exactly one of (the diagnostic log line goes to stderr so it never pollutes
# the captured decision):
#   "count"          - sig_value is not environmental:delivery_failure; caller proceeds
#                       with its existing get_retry_count/ceiling-check/increment_retry
#                       sequence unchanged.
#   "skip"           - delivery failure, under cap; delivery-skip counter incremented;
#                       caller dispatches WITHOUT touching the normal retry counter.
#   "trip:<reason>"  - delivery failure, cap reached; normal retry counter has been
#                       back-filled via breaker-set-retry; caller calls trip_to_blocked
#                       with the given reason and continues.
retry_or_skip_delivery_failure() {
  local issue_num="$1" phase="$2" sig_value="$3" retry_key="$4" ceiling="$5"
  if [ "$sig_value" != "environmental:delivery_failure" ]; then echo "count"; return; fi
  local dkey="${retry_key}:delivery"
  local dcount
  dcount=$(increment_retry "$dkey" || echo "")
  case "$dcount" in
    ''|*[!0-9]*) echo "count"; return ;;
  esac
  if [ "$dcount" -lt "$ceiling" ]; then
    echo "[$(date -u +%FT%TZ)] delivery_gate issue=#${issue_num} phase=${phase} action=delivery_failure_skip count=${dcount}/${ceiling}" >&2
    echo "skip"
  else
    STATE_FILE="$STATE_FILE" python3 "$FACTORY_CORE_CLI" breaker-set-retry --key "$retry_key" --value "$dcount"
    echo "trip:same failure signature 'environmental:delivery_failure' recorded ${dcount} consecutive times (suspected runner prompt-delivery bug — see #279), retry budget exhausted"
  fi
}
```

5. Verify it passes and confirm no new failures beyond the Task 2 step 1 baseline:

```bash
bash tests/test_scheduler.sh 2>&1 | grep -E "^  (PASS|FAIL): S"
# all "S*" lines PASS
bash tests/test_scheduler.sh 2>&1 | tail -3
# Results: 139 passed, 2 failed   (124 baseline + 15 new "S*" assertions; same 2
# pre-existing failures as the Task 2 step 1 baseline — recount the "S*" assertions if
# this section's content changes, don't treat a mismatch here as a real regression
# without checking which specific assertion changed)
```

6. Commit:

```bash
git add scheduler.sh tests/test_scheduler.sh
git commit -m "feat(scheduler): add retry_or_skip_delivery_failure shared helper (#279)"
```

---

## Task 3: Wire the helper into `stage_refine`

**Files:** `scheduler.sh`, `tests/test_scheduler.sh`

### TDD Steps

1. Append to `tests/test_scheduler.sh`, directly after section S (which Task 2 just added
   at the end of the file):

```bash
# ==========================================
# T: stage_refine — delivery-failure retry exemption wiring (#279)
# ==========================================
echo ""
echo "--- T: stage_refine — delivery-failure exemption ---"
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
gh() { echo "gh $*" >> "$STUB_LOG"; return 0; }
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
export -f gh dispatch

# Reproduces stage_refine's per-item body (matches this file's existing K-section
# convention of exercising the loop body directly rather than fixturing REFINE_RUNNING/
# REFINE_WIP_LIMIT/BACKLOG end-to-end). This section defines every stub it needs itself
# (gh, dispatch above) rather than relying on whatever an earlier section left bound.
_run_refine_body() {
  local issue="$1"
  SIG_RESULT=$(check_failure_signature "$issue" "refine")
  SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
  if echo "$SIG_RESULT" | grep -q "stuck=true"; then
    trip_to_blocked "$issue" "refine" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
    return
  fi

  PREV_DELIVERY_SKIP=""
  DECISION=$(retry_or_skip_delivery_failure "$issue" "refine" "$SIG_VALUE" "${issue}:refine" "$REFINE_MAX_RETRIES" || echo "count")
  case "$DECISION" in
    skip) PREV_DELIVERY_SKIP=1 ;;
    trip:*) trip_to_blocked "$issue" "refine" "${DECISION#trip:}"; return ;;
    count|*)
      RETRIES=$(get_retry_count "${issue}:refine")
      if [ "$RETRIES" -ge "$REFINE_MAX_RETRIES" ]; then
        trip_to_blocked "$issue" "refine" "retry limit of ${REFINE_MAX_RETRIES} reached"
        return
      fi
      increment_retry "${issue}:refine"
      ;;
  esac

  DELIVERY_NOTE=""
  if [ -n "$PREV_DELIVERY_SKIP" ]; then
    DELIVERY_NOTE=" was not counted against the retry budget (runner-side delivery failure, #279)."
  fi
  gh issue comment "$issue" --repo test/repo --body "Starting refine.${DELIVERY_NOTE}" > /dev/null
  dispatch "Refine issue #${issue}" > /dev/null
}

# T1: a substantive (non-delivery) failure increments the normal counter, no note
_drop_sig 80 refine "substantive:test_failure:1"
_run_refine_body 80
assert_eq "T1: normal counter incremented" "1" "$(get_retry_count "80:refine")"
assert_eq "T1b: no shadow counter created" "0" "$(get_retry_count "80:refine:delivery")"
assert_eq "T1c: dispatched" "1" "$(grep -c 'dispatch Refine issue #80' "$STUB_LOG" || echo 0)"
assert_eq "T1d: comment has no delivery-skip note" "0" "$(grep -c 'was not counted against the retry budget' "$STUB_LOG" || true)"

> "$STUB_LOG"

# T2: a delivery failure under cap dispatches without touching the normal counter,
# and the comment carries the delivery-skip note
echo '{}' > "$STATE_FILE"
_drop_sig 81 refine "environmental:delivery_failure"
_run_refine_body 81
assert_eq "T2: normal counter NOT incremented" "0" "$(get_retry_count "81:refine")"
assert_eq "T2b: shadow counter incremented to 1" "1" "$(get_retry_count "81:refine:delivery")"
assert_eq "T2c: dispatched" "1" "$(grep -c 'dispatch Refine issue #81' "$STUB_LOG" || echo 0)"
assert_eq "T2d: comment carries the delivery-skip note" "1" "$(grep -c 'was not counted against the retry budget' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"

# T3: REFINE_MAX_RETRIES consecutive delivery failures trip, back-filling the normal
# counter (asserted via the breaker-set-retry delegation — trip_to_blocked's own
# reset_retry zeroes the counter again immediately after, matching section B/K9's
# already-passing "counter reset after trip" assertions), and do NOT dispatch on the
# tripping attempt
echo '{}' > "$STATE_FILE"
for i in $(seq 1 "$REFINE_MAX_RETRIES"); do
  _drop_sig 82 refine "environmental:delivery_failure"
  _run_refine_body 82
done
assert_eq "T3: back-fill delegates to breaker-set-retry with the shadow count" \
  "1" "$(grep -c "breaker-set-retry --key 82:refine --value ${REFINE_MAX_RETRIES}" "$STUB_LOG" || echo 0)"
assert_eq "T3b: only REFINE_MAX_RETRIES-1 dispatches occurred (cap attempt trips, no dispatch)" \
  "$((REFINE_MAX_RETRIES - 1))" "$(grep -c 'dispatch Refine issue #82' "$STUB_LOG" || echo 0)"
assert_eq "T3c: breaker-trip delegated with the delivery-failure reason" \
  "1" "$(grep -c 'breaker-trip --issue 82 --phase refine' "$STUB_LOG" || echo 0)"
assert_eq "T3d: normal counter reset to 0 after trip (trip_to_blocked's existing reset_retry)" \
  "0" "$(get_retry_count "82:refine")"

> "$STUB_LOG"
```

2. Verify it fails against `stage_refine`'s current shape. `_run_refine_body` calls
   `retry_or_skip_delivery_failure` (already implemented by Task 2), so T1-T3 pass
   against the *reproduced* body immediately — that part is not the red signal here. The
   real gap this step confirms is that `scheduler.sh`'s actual `stage_refine` function
   does not yet match this shape (no `PREV_DELIVERY_SKIP`/`DECISION` branching, no
   delivery-skip comment note) — step 3 below closes that gap:

```bash
bash tests/test_scheduler.sh 2>&1 | grep -E "^  (PASS|FAIL): T"
# T1-T3 PASS (self-contained against the reproduced body); grep for PREV_DELIVERY_SKIP
# in scheduler.sh confirms the real function doesn't have this logic yet:
grep -c "PREV_DELIVERY_SKIP" scheduler.sh
# 0
```

3. Edit `stage_refine` in `scheduler.sh` (currently lines 1001-1046). Replace:

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

    increment_retry "${ISSUE}:refine"
    FOOTER=$(python3 "$FACTORY_CORE_CLI" marker scheduler)
    gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body "🧠 **Refinement Pipeline** — Starting brainstorming and spec generation.

---
${FOOTER}" 2>/dev/null || true
    if dispatch "Refine issue #${ISSUE}"; then
      DISPATCHED="Refine issue #${ISSUE}"
      REFINE_RUNNING=$((REFINE_RUNNING + 1))
    fi
```

   with:

```bash
    SIG_RESULT=$(check_failure_signature "$ISSUE" "refine")
    SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
    if echo "$SIG_RESULT" | grep -q "stuck=true"; then
      trip_to_blocked "$ISSUE" "refine" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
      continue
    fi

    PREV_DELIVERY_SKIP=""
    DECISION=$(retry_or_skip_delivery_failure "$ISSUE" "refine" "$SIG_VALUE" "${ISSUE}:refine" "$REFINE_MAX_RETRIES" || echo "count")
    case "$DECISION" in
      skip)
        PREV_DELIVERY_SKIP=1
        ;;
      trip:*)
        trip_to_blocked "$ISSUE" "refine" "${DECISION#trip:}"
        continue
        ;;
      count|*)
        RETRIES=$(get_retry_count "${ISSUE}:refine")
        if [ "$RETRIES" -ge "$REFINE_MAX_RETRIES" ]; then
          trip_to_blocked "$ISSUE" "refine" "retry limit of ${REFINE_MAX_RETRIES} reached"
          continue
        fi
        increment_retry "${ISSUE}:refine"
        ;;
    esac

    FOOTER=$(python3 "$FACTORY_CORE_CLI" marker scheduler)
    DELIVERY_NOTE=""
    if [ -n "$PREV_DELIVERY_SKIP" ]; then
      DELIVERY_NOTE="

> ℹ️ The previous attempt hit a runner-side delivery failure (empty prompt, [#279](https://github.com/${FACTORY_REPO_SLUG}/issues/279)) and was not counted against the retry budget."
    fi
    gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body "🧠 **Refinement Pipeline** — Starting brainstorming and spec generation.${DELIVERY_NOTE}

---
${FOOTER}" 2>/dev/null || true
    if dispatch "Refine issue #${ISSUE}"; then
      DISPATCHED="Refine issue #${ISSUE}"
      REFINE_RUNNING=$((REFINE_RUNNING + 1))
    fi
```

4. Verify no new failures beyond baseline:

```bash
bash tests/test_scheduler.sh 2>&1 | tail -3
# Results: N passed, 2 failed   (same 2 pre-existing failures; N greater than Task 2's count)
```

5. Commit:

```bash
git add scheduler.sh tests/test_scheduler.sh
git commit -m "feat(scheduler): wire delivery-failure retry exemption into stage_refine (#279)"
```

---

## Task 4: Wire the helper into `stage_plan`

**Files:** `scheduler.sh`, `tests/test_scheduler.sh`

Same shape as Task 3, phase `"plan"`, ceiling `$REFINE_MAX_RETRIES`, key
`"${ISSUE}:plan"`, comment text `"📋 **Refinement Pipeline** — Starting plan generation
and architect validation."`, dispatch command `"Plan issue #${ISSUE}"`.

### TDD Steps

1. Append to `tests/test_scheduler.sh`, directly after section T:

```bash
# ==========================================
# U: stage_plan — delivery-failure retry exemption wiring (#279)
# ==========================================
echo ""
echo "--- U: stage_plan — delivery-failure exemption ---"
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
gh() { echo "gh $*" >> "$STUB_LOG"; return 0; }
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
export -f gh dispatch

_run_plan_body() {
  local issue="$1"
  SIG_RESULT=$(check_failure_signature "$issue" "plan")
  SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
  if echo "$SIG_RESULT" | grep -q "stuck=true"; then
    trip_to_blocked "$issue" "plan" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
    return
  fi

  PREV_DELIVERY_SKIP=""
  DECISION=$(retry_or_skip_delivery_failure "$issue" "plan" "$SIG_VALUE" "${issue}:plan" "$REFINE_MAX_RETRIES" || echo "count")
  case "$DECISION" in
    skip) PREV_DELIVERY_SKIP=1 ;;
    trip:*) trip_to_blocked "$issue" "plan" "${DECISION#trip:}"; return ;;
    count|*)
      RETRIES=$(get_retry_count "${issue}:plan")
      if [ "$RETRIES" -ge "$REFINE_MAX_RETRIES" ]; then
        trip_to_blocked "$issue" "plan" "retry limit of ${REFINE_MAX_RETRIES} reached"
        return
      fi
      increment_retry "${issue}:plan"
      ;;
  esac

  DELIVERY_NOTE=""
  if [ -n "$PREV_DELIVERY_SKIP" ]; then
    DELIVERY_NOTE=" was not counted against the retry budget (runner-side delivery failure, #279)."
  fi
  gh issue comment "$issue" --repo test/repo --body "Starting plan.${DELIVERY_NOTE}" > /dev/null
  dispatch "Plan issue #${issue}" > /dev/null
}

_drop_sig 90 plan "substantive:test_failure:1"
_run_plan_body 90
assert_eq "U1: normal counter incremented" "1" "$(get_retry_count "90:plan")"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

_drop_sig 91 plan "environmental:delivery_failure"
_run_plan_body 91
assert_eq "U2: normal counter NOT incremented" "0" "$(get_retry_count "91:plan")"
assert_eq "U2b: shadow counter incremented to 1" "1" "$(get_retry_count "91:plan:delivery")"
assert_eq "U2c: comment carries the delivery-skip note" "1" "$(grep -c 'was not counted against the retry budget' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

for i in $(seq 1 "$REFINE_MAX_RETRIES"); do
  _drop_sig 92 plan "environmental:delivery_failure"
  _run_plan_body 92
done
assert_eq "U3: back-fill delegates to breaker-set-retry with the shadow count" \
  "1" "$(grep -c "breaker-set-retry --key 92:plan --value ${REFINE_MAX_RETRIES}" "$STUB_LOG" || echo 0)"
assert_eq "U3b: breaker-trip delegated" \
  "1" "$(grep -c 'breaker-trip --issue 92 --phase plan' "$STUB_LOG" || echo 0)"
assert_eq "U3c: normal counter reset to 0 after trip" "0" "$(get_retry_count "92:plan")"

> "$STUB_LOG"
```

2. Verify it fails against `stage_plan`'s current shape, same reasoning as Task 3 step 2:

```bash
bash tests/test_scheduler.sh 2>&1 | grep -E "^  (PASS|FAIL): U"
grep -c "PREV_DELIVERY_SKIP" scheduler.sh
# 3 (Task 3's stage_refine edit alone has 3 matching lines: the PREV_DELIVERY_SKIP=""
# init, the PREV_DELIVERY_SKIP=1 assignment, and the "if [ -n "$PREV_DELIVERY_SKIP" ]"
# check — stage_plan doesn't have this variable yet at this point)
```

3. Edit `stage_plan` in `scheduler.sh` (currently lines 956-998). Replace:

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

    increment_retry "${ISSUE}:plan"
    FOOTER=$(python3 "$FACTORY_CORE_CLI" marker scheduler)
    gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body "📋 **Refinement Pipeline** — Starting plan generation and architect validation.

---
${FOOTER}" 2>/dev/null || true
    if dispatch "Plan issue #${ISSUE}"; then
      DISPATCHED="Plan issue #${ISSUE}"
      REFINE_RUNNING=$((REFINE_RUNNING + 1))
    fi
```

   with:

```bash
    SIG_RESULT=$(check_failure_signature "$ISSUE" "plan")
    SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
    if echo "$SIG_RESULT" | grep -q "stuck=true"; then
      trip_to_blocked "$ISSUE" "plan" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
      continue
    fi

    PREV_DELIVERY_SKIP=""
    DECISION=$(retry_or_skip_delivery_failure "$ISSUE" "plan" "$SIG_VALUE" "${ISSUE}:plan" "$REFINE_MAX_RETRIES" || echo "count")
    case "$DECISION" in
      skip)
        PREV_DELIVERY_SKIP=1
        ;;
      trip:*)
        trip_to_blocked "$ISSUE" "plan" "${DECISION#trip:}"
        continue
        ;;
      count|*)
        RETRIES=$(get_retry_count "${ISSUE}:plan")
        if [ "$RETRIES" -ge "$REFINE_MAX_RETRIES" ]; then
          trip_to_blocked "$ISSUE" "plan" "retry limit of ${REFINE_MAX_RETRIES} reached"
          continue
        fi
        increment_retry "${ISSUE}:plan"
        ;;
    esac

    FOOTER=$(python3 "$FACTORY_CORE_CLI" marker scheduler)
    DELIVERY_NOTE=""
    if [ -n "$PREV_DELIVERY_SKIP" ]; then
      DELIVERY_NOTE="

> ℹ️ The previous attempt hit a runner-side delivery failure (empty prompt, [#279](https://github.com/${FACTORY_REPO_SLUG}/issues/279)) and was not counted against the retry budget."
    fi
    gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body "📋 **Refinement Pipeline** — Starting plan generation and architect validation.${DELIVERY_NOTE}

---
${FOOTER}" 2>/dev/null || true
    if dispatch "Plan issue #${ISSUE}"; then
      DISPATCHED="Plan issue #${ISSUE}"
      REFINE_RUNNING=$((REFINE_RUNNING + 1))
    fi
```

4. Verify no new failures beyond baseline:

```bash
bash tests/test_scheduler.sh 2>&1 | tail -3
# Results: N passed, 2 failed   (same 2 pre-existing failures)
```

5. Commit:

```bash
git add scheduler.sh tests/test_scheduler.sh
git commit -m "feat(scheduler): wire delivery-failure retry exemption into stage_plan (#279)"
```

---

## Task 5: Wire the helper into `stage_blocked_retry` (implement)

**Files:** `scheduler.sh`, `tests/test_scheduler.sh`

No per-dispatch comment for this site (Requirement 6c) — log-only visibility, already
provided by the shared helper's `>&2` line.

### TDD Steps

1. Append to `tests/test_scheduler.sh`, directly after section U:

```bash
# ==========================================
# V: stage_blocked_retry (implement) — delivery-failure retry exemption (#279)
# ==========================================
echo ""
echo "--- V: stage_blocked_retry — delivery-failure exemption ---"
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
get_pr_for_issue() { echo ""; }
export -f dispatch get_pr_for_issue

_run_blocked_retry_body() {
  local issue="$1"
  SIG_RESULT=$(check_failure_signature "$issue" "implement")
  SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
  if echo "$SIG_RESULT" | grep -q "stuck=true"; then
    trip_to_blocked "$issue" "implement" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
    return
  fi

  DECISION=$(retry_or_skip_delivery_failure "$issue" "implement" "$SIG_VALUE" "$issue" "$MAX_RETRIES" || echo "count")
  case "$DECISION" in
    skip) ;;
    trip:*) trip_to_blocked "$issue" "implement" "${DECISION#trip:}"; return ;;
    count|*)
      RETRIES=$(get_retry_count "$issue")
      if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
        trip_to_blocked "$issue" "implement" "retry limit of ${MAX_RETRIES} reached"
        return
      fi
      increment_retry "$issue"
      ;;
  esac

  if [ -n "$(get_pr_for_issue "$issue")" ]; then
    dispatch "Continue issue #${issue}" > /dev/null
  else
    dispatch "Fix issue #${issue}" > /dev/null
  fi
}

_drop_sig 100 implement "substantive:test_failure:1"
_run_blocked_retry_body 100
assert_eq "V1: normal counter incremented" "1" "$(get_retry_count "100")"
assert_eq "V1b: dispatched Fix (no PR)" "1" "$(grep -c 'dispatch Fix issue #100' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

_drop_sig 101 implement "environmental:delivery_failure"
_run_blocked_retry_body 101
assert_eq "V2: normal counter NOT incremented" "0" "$(get_retry_count "101")"
assert_eq "V2b: shadow counter incremented to 1" "1" "$(get_retry_count "101:delivery")"
assert_eq "V2c: dispatched" "1" "$(grep -c 'dispatch Fix issue #101' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

for i in $(seq 1 "$MAX_RETRIES"); do
  _drop_sig 102 implement "environmental:delivery_failure"
  _run_blocked_retry_body 102
done
assert_eq "V3: back-fill delegates to breaker-set-retry with the shadow count" \
  "1" "$(grep -c "breaker-set-retry --key 102 --value ${MAX_RETRIES}" "$STUB_LOG" || echo 0)"
assert_eq "V3b: breaker-trip delegated" \
  "1" "$(grep -c 'breaker-trip --issue 102 --phase implement' "$STUB_LOG" || echo 0)"
assert_eq "V3c: normal counter reset to 0 after trip" "0" "$(get_retry_count "102")"

> "$STUB_LOG"
```

2. Verify it fails against `stage_blocked_retry`'s current shape, same reasoning as
   Tasks 3-4 step 2 (`_run_blocked_retry_body` is self-contained and its V1-V3
   assertions already pass against the reproduced body; the gap is that the real
   function doesn't match it yet):

```bash
bash tests/test_scheduler.sh 2>&1 | grep -E "^  (PASS|FAIL): V"
grep -c "retry_or_skip_delivery_failure" scheduler.sh
# 4 (Task 2's helper block itself contributes 2 matches — the "Usage: DECISION=..."
# comment and the "retry_or_skip_delivery_failure() {" definition — plus one real call
# each from Tasks 3 and 4's stage_refine/stage_plan edits)
```

3. Edit `stage_blocked_retry` in `scheduler.sh` (currently lines 913-953). Replace:

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

    increment_retry "$ISSUE"
```

   with:

```bash
    SIG_RESULT=$(check_failure_signature "$ISSUE" "implement")
    SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
    if echo "$SIG_RESULT" | grep -q "stuck=true"; then
      trip_to_blocked "$ISSUE" "implement" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
      continue
    fi

    DECISION=$(retry_or_skip_delivery_failure "$ISSUE" "implement" "$SIG_VALUE" "$ISSUE" "$MAX_RETRIES" || echo "count")
    case "$DECISION" in
      skip)
        ;;
      trip:*)
        trip_to_blocked "$ISSUE" "implement" "${DECISION#trip:}"
        continue
        ;;
      count|*)
        RETRIES=$(get_retry_count "$ISSUE")
        if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
          trip_to_blocked "$ISSUE" "implement" "retry limit of ${MAX_RETRIES} reached"
          continue
        fi
        increment_retry "$ISSUE"
        ;;
    esac
```

   (the rest of the function — the branch-aware `get_pr_for_issue` / `dispatch`
   Continue-vs-Fix logic, currently lines 940-951 — is unchanged).

4. Verify no new failures beyond baseline:

```bash
bash tests/test_scheduler.sh 2>&1 | tail -3
# Results: N passed, 2 failed   (same 2 pre-existing failures)
```

5. Commit:

```bash
git add scheduler.sh tests/test_scheduler.sh
git commit -m "feat(scheduler): wire delivery-failure retry exemption into stage_blocked_retry (#279)"
```

---

## Task 6: Wire the two-step split into `stage_conflict_resolve` (resolve)

**Files:** `scheduler.sh`, `tests/test_scheduler.sh`

Per the Design Note above, this site does not call `retry_or_skip_delivery_failure()` —
it reimplements the same 3-way decision (count / skip-under-cap / trip-at-cap) split
across the existing ceiling checkpoint (peek, no mutation) and the existing
`increment_retry "${ISSUE}:resolve"` call site (the real mutation, only on an actual
dispatch).

### TDD Steps

1. Append to `tests/test_scheduler.sh`, directly after section V:

```bash
# ==========================================
# W: stage_conflict_resolve (resolve) — delivery-failure retry exemption (#279)
# ==========================================
echo ""
echo "--- W: stage_conflict_resolve — delivery-failure exemption ---"
echo '{}' > "$STATE_FILE"; > "$STUB_LOG"
dispatch() { echo "dispatch $*" >> "$STUB_LOG"; return 0; }
get_pr_for_issue() { echo "500"; }
check_pr_mergeable() { echo "CONFLICTING"; }
export -f dispatch get_pr_for_issue check_pr_mergeable

_run_resolve_body() {
  local issue="$1"
  SIG_RESULT=$(check_failure_signature "$issue" "resolve")
  SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
  if echo "$SIG_RESULT" | grep -q "stuck=true"; then
    trip_to_blocked "$issue" "resolve" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
    return
  fi

  RESOLVE_DELIVERY_SKIP=""
  if [ "$SIG_VALUE" = "environmental:delivery_failure" ]; then
    DPEEK=$(get_retry_count "${issue}:resolve:delivery" || echo 0)
    if [ "$DPEEK" -ge "$MAX_RETRIES" ]; then
      STATE_FILE="$STATE_FILE" python3 "$FACTORY_CORE_CLI" breaker-set-retry --key "${issue}:resolve" --value "$DPEEK"
      trip_to_blocked "$issue" "resolve" "same failure signature 'environmental:delivery_failure' recorded ${DPEEK} consecutive times (suspected runner prompt-delivery bug — see #279), retry budget exhausted"
      return
    fi
    RESOLVE_DELIVERY_SKIP=1
  else
    RETRIES=$(get_retry_count "${issue}:resolve")
    if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
      trip_to_blocked "$issue" "resolve" "retry limit of ${MAX_RETRIES} reached for conflict resolution"
      return
    fi
  fi

  PR_NUM=$(get_pr_for_issue "$issue")
  [ -z "$PR_NUM" ] && return

  MERGEABLE=$(check_pr_mergeable "$PR_NUM")
  case "$MERGEABLE" in
    CONFLICTING)
      if [ -n "$RESOLVE_DELIVERY_SKIP" ]; then
        increment_retry "${issue}:resolve:delivery" > /dev/null || true
      else
        increment_retry "${issue}:resolve" || true
      fi
      dispatch "Deconflict issue #${issue}" > /dev/null
      ;;
  esac
}

# W1: substantive failure — unchanged behavior (normal counter increments on dispatch)
_drop_sig 110 resolve "substantive:test_failure:1"
_run_resolve_body 110
assert_eq "W1: normal counter incremented" "1" "$(get_retry_count "110:resolve")"
assert_eq "W1b: dispatched" "1" "$(grep -c 'dispatch Deconflict issue #110' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

# W2: delivery failure under cap — dispatches, shadow counter (not normal) increments
_drop_sig 111 resolve "environmental:delivery_failure"
_run_resolve_body 111
assert_eq "W2: normal counter NOT incremented" "0" "$(get_retry_count "111:resolve")"
assert_eq "W2b: shadow counter incremented to 1" "1" "$(get_retry_count "111:resolve:delivery")"
assert_eq "W2c: dispatched" "1" "$(grep -c 'dispatch Deconflict issue #111' "$STUB_LOG" || echo 0)"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

# W3: MAX_RETRIES consecutive delivery failures dispatch (each incrementing the shadow
# counter), then the NEXT checkpoint peeks a shadow count already at the ceiling and
# trips without dispatching again — see the plan's "Accepted asymmetry" note for why
# this is MAX_RETRIES dispatches (not MAX_RETRIES-1, unlike refine/plan/implement).
for i in $(seq 1 "$MAX_RETRIES"); do
  _drop_sig 112 resolve "environmental:delivery_failure"
  _run_resolve_body 112
done
assert_eq "W3: shadow counter at MAX_RETRIES after MAX_RETRIES dispatches" \
  "$MAX_RETRIES" "$(get_retry_count "112:resolve:delivery")"
assert_eq "W3b: MAX_RETRIES dispatches occurred" \
  "$MAX_RETRIES" "$(grep -c 'dispatch Deconflict issue #112' "$STUB_LOG" || echo 0)"

_drop_sig 112 resolve "environmental:delivery_failure"
_run_resolve_body 112
assert_eq "W3c: the next checkpoint trips instead of dispatching again" \
  "$MAX_RETRIES" "$(grep -c 'dispatch Deconflict issue #112' "$STUB_LOG" || echo 0)"
assert_eq "W3d: back-fill delegates to breaker-set-retry with the shadow count" \
  "1" "$(grep -c "breaker-set-retry --key 112:resolve --value ${MAX_RETRIES}" "$STUB_LOG" || echo 0)"
assert_eq "W3e: breaker-trip delegated with the delivery-failure reason" \
  "1" "$(grep -c 'breaker-trip --issue 112 --phase resolve' "$STUB_LOG" || echo 0)"
assert_eq "W3f: normal counter reset to 0 after trip" "0" "$(get_retry_count "112:resolve")"

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

# W4: no dispatch this cycle (MERGEABLE=UNKNOWN) → neither counter mutates
check_pr_mergeable() { echo "UNKNOWN"; }
export -f check_pr_mergeable
_drop_sig 113 resolve "environmental:delivery_failure"
_run_resolve_body 113
assert_eq "W4: shadow counter untouched when no dispatch occurs" "0" "$(get_retry_count "113:resolve:delivery")"
assert_eq "W4b: no dispatch" "0" "$(grep -c 'dispatch' "$STUB_LOG" || true)"
check_pr_mergeable() { echo "CONFLICTING"; }
export -f check_pr_mergeable

> "$STUB_LOG"; echo '{}' > "$STATE_FILE"

# W5: reset_retry clears the resolve shadow counter
_drop_sig 114 resolve "environmental:delivery_failure"
_run_resolve_body 114
assert_eq "W5: shadow counter at 1 before reset" "1" "$(get_retry_count "114:resolve:delivery")"
reset_retry "114:resolve"
assert_eq "W5b: shadow counter cleared" "0" "$(get_retry_count "114:resolve:delivery")"

> "$STUB_LOG"
```

2. Verify it fails against `stage_conflict_resolve`'s current shape, same reasoning as
   Tasks 3-5 step 2:

```bash
bash tests/test_scheduler.sh 2>&1 | grep -E "^  (PASS|FAIL): W"
grep -c "RESOLVE_DELIVERY_SKIP" scheduler.sh
# 0
```

3. Edit `stage_conflict_resolve` in `scheduler.sh` (currently lines 786-825). Replace the
   whole function body from the `SIG_RESULT=` line through the `esac` (currently lines
   795-823) with:

```bash
    SIG_RESULT=$(check_failure_signature "$ISSUE" "resolve")
    SIG_VALUE=$(echo "$SIG_RESULT" | grep -o 'sig=.*' | cut -d= -f2-)
    if echo "$SIG_RESULT" | grep -q "stuck=true"; then
      trip_to_blocked "$ISSUE" "resolve" "same failure signature '${SIG_VALUE}' recorded on two consecutive attempts — halting retries"
      continue
    fi

    # #279: the delivery-failure exemption's accounting (the "<key>:delivery" shadow
    # counter) must increment at the actual dispatch point below, not here — this
    # checkpoint only PEEKS the shadow counter to decide trip-vs-proceed, preserving
    # the existing two-step shape (decide-to-trip here; count/skip at the CONFLICTING
    # branch). See the "#279 skip-retry-counter design" spec's Requirement 2 for why
    # this site doesn't call the shared retry_or_skip_delivery_failure helper the other
    # three call sites use.
    RESOLVE_DELIVERY_SKIP=""
    if [ "$SIG_VALUE" = "environmental:delivery_failure" ]; then
      DPEEK=$(get_retry_count "${ISSUE}:resolve:delivery" || echo 0)
      if [ "$DPEEK" -ge "$MAX_RETRIES" ]; then
        STATE_FILE="$STATE_FILE" python3 "$FACTORY_CORE_CLI" breaker-set-retry --key "${ISSUE}:resolve" --value "$DPEEK"
        trip_to_blocked "$ISSUE" "resolve" "same failure signature 'environmental:delivery_failure' recorded ${DPEEK} consecutive times (suspected runner prompt-delivery bug — see #279), retry budget exhausted"
        continue
      fi
      RESOLVE_DELIVERY_SKIP=1
    else
      RETRIES=$(get_retry_count "${ISSUE}:resolve")
      if [ "$RETRIES" -ge "$MAX_RETRIES" ]; then
        trip_to_blocked "$ISSUE" "resolve" "retry limit of ${MAX_RETRIES} reached for conflict resolution"
        continue
      fi
    fi

    PR_NUM=$(get_pr_for_issue "$ISSUE")
    [ -z "$PR_NUM" ] && continue

    MERGEABLE=$(check_pr_mergeable "$PR_NUM")
    case "$MERGEABLE" in
      CONFLICTING)
        echo "[$(date -u +%FT%TZ)] conflict_gate issue=#${ISSUE} pr=#${PR_NUM} mergeable=CONFLICTING action=dispatch_deconflict"
        if [ -n "$RESOLVE_DELIVERY_SKIP" ]; then
          DCOUNT=$(increment_retry "${ISSUE}:resolve:delivery" || echo 0)
          echo "[$(date -u +%FT%TZ)] delivery_gate issue=#${ISSUE} phase=resolve action=delivery_failure_skip count=${DCOUNT}/${MAX_RETRIES}" >&2
        else
          increment_retry "${ISSUE}:resolve" || true
        fi
        if dispatch "Deconflict issue #${ISSUE}"; then
          DISPATCHED="Deconflict issue #${ISSUE}"
        fi
        ;;
      UNKNOWN)
        echo "[$(date -u +%FT%TZ)] conflict_gate issue=#${ISSUE} pr=#${PR_NUM} mergeable=UNKNOWN action=skip"
        ;;
    esac
```

   (the `delivery_gate` log line is redirected to `>&2`, matching the shared helper's own
   stream — Requirement 6a visibility should land on one consistent stream an operator can
   grep, not split across stdout/stderr depending on which call site fired.)

4. Verify no new failures beyond baseline:

```bash
bash tests/test_scheduler.sh 2>&1 | tail -3
# Results: N passed, 2 failed   (same 2 pre-existing failures)
```

5. Run the complete verification sweep (full regression, all tasks combined):

```bash
python -m pytest tests/ -v
bash tests/test_scheduler.sh 2>&1 | tail -3
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```

   `pytest` must be 100% green. `test_scheduler.sh` must show exactly the same 2
   pre-existing failures as the Task 2 step 1 baseline (`G2`, `I2`) and zero others —
   confirm by name, not just by count:

```bash
bash tests/test_scheduler.sh 2>&1 | grep "^  FAIL"
# FAIL: G2: advance: set_board_status REFINED — ...
# FAIL: I2: advance: set_board_status READY — ...
```

6. Commit:

```bash
git add scheduler.sh tests/test_scheduler.sh
git commit -m "feat(scheduler): wire delivery-failure retry exemption into stage_conflict_resolve (#279)"
```

---

## Out of Scope

- **Wiring `tests/test_scheduler.sh` into `.github/workflows/ci.yml`.** This file is not
  currently run by CI at all (confirmed: absent from `ci.yml`'s `tests` job, unlike
  `test_budget_gate.sh`, `test_identity.sh`, etc.) — a pre-existing gap independent of
  #279, not introduced or worsened by this change. Fixing it would risk surfacing
  unrelated pre-existing failures across the file's ~20 other sections and is its own
  ticket. TDD verification for every task above runs `bash tests/test_scheduler.sh`
  directly.
- **Fixing the 2 pre-existing `test_scheduler.sh` failures** (`G2: advance: set_board_status
  REFINED`, `I2: advance: set_board_status READY`). Confirmed pre-existing on `main` before
  any change in this plan — unrelated to #279's retry/breaker logic (they're in the "Spec
  auto-advance"/"Plan auto-advance" sections). Fixing them is a separate ticket; this
  plan's tasks are scored against "no new failures beyond this baseline."
- **Angle 3 (report the empty-prompt bug upstream to Archon).** Not a code deliverable in
  this repo — the issue's own comment thread already carries the source-level
  investigation; this spec's new `delivery_gate`/`action=delivery_failure_skip` log line
  (Task 2) is the ongoing local telemetry that would support such a report.
- **Extending the exemption to other `environmental:*` classes** (`preview_infra`,
  `rate_limit`) — explicitly out of scope per the spec's Open Questions; those already
  have separate handling and would need their own reviewed ticket per CLAUDE.md's gate
  discipline.
- **`#212`'s artifact-gating, the early "stuck" trip for consecutive `substantive:*`
  signatures.** Unchanged — this plan only touches the four `increment_retry` call sites'
  discard-vs-use of the signature `check_failure_signature` already returns.
- **Evaluating the normal (substantive) ceiling inside the `skip` arm.** In all four
  sites, a ticket already at or near its normal ceiling that then draws a delivery failure
  gets a fresh, separately-capped shadow-counter budget rather than being blocked
  immediately — bounded (at most `ceiling` additional dispatches via the shadow cap), but
  not evaluated against the normal counter first. The spec is silent on this interaction;
  flagged for the conformance reviewer rather than resolved unilaterally here, since
  tightening it would be an unrequested interpretation of Requirement 4's back-fill
  mechanism.
