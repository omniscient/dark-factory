# Implementation Plan: Fix the structured-marker status blindness, tighten `RATE_LIMIT_RE`, split the pause gate, log the match

**Issue:** omniscient/dark-factory#344
**Spec:** `docs/superpowers/specs/2026-08-22-session-window-rate-limit-classifier-tightening-design.md` (revision 2)

---

## Goal

Two co-equal defects in `scripts/factory_core/session_window.py` each independently
reproduce the #332 false 30-minute factory-wide pause:

1. **(Primary) The structured-marker path never reads `status`.** Every healthy run's
   captured stdout contains a `claude.rate_limit_event` pino log line with
   `rateLimitInfo.status == "allowed"`. `is_session_window_failure` currently pauses on
   the marker's mere *presence*. `parse_structured_reset_epoch` also reads a top-level
   `resetsAt` the real payload nests under `rateLimitInfo`.
2. **Bare substring matching, no word boundaries or error context.** `RATE_LIMIT_RE`
   matches inside SHA fragments, dollar figures, issue references, and this repo's own
   log/doc prose.

This plan: (a) fixes the structured-marker path to parse JSON and check `status`, (b)
tightens `RATE_LIMIT_RE` with context-required alternatives shared by the breaker
(`error_signature.py`), (c) splits a strictly narrower `_SESSION_EXHAUSTION_RE` used only
by the pause gate so a transient 429 no longer buys a 30-minute halt, (d) makes a pause
diagnosable after the `--rm` container exits by extending the existing `run-record`
channel with the matched snippet, and (e) eliminates `entrypoint.sh`'s hardcoded
duplicate grep via a new classify-only CLI subcommand.

## Architecture

```
entrypoint.sh (main retry loop, TMP_OUT = captured archon stdout)
  │
  ├─ _handle_session_window_pause "$TMP_OUT"          (primary path, kill-switch-gated)
  │    └─ cli.py session-window-check --tmp-out ...
  │         └─ session_window.check_and_pause()
  │              ├─ is_session_window_failure(text)        ── gate
  │              │    ├─ _structured_events(text) → any status != "allowed"?  (Cause A fix)
  │              │    └─ else _SESSION_EXHAUSTION_RE.search(text)             (Cause B, narrow)
  │              ├─ compute_resume_epoch(text, ...)         ── resume time
  │              │    └─ parse_structured_reset_epoch() prefers rateLimitInfo.resetsAt
  │              └─ write_pause_sentinel()
  │         └─ match_snippet(text) → {matched, offset, window, branch}   (new, Decision 4)
  │              → printed as snippet_b64=<...> (own stdout line)
  │    └─ decodes snippet_b64 → run-record record --stage paused --detail matched_pattern=... match_offset=... snippet_b64=b64:...
  │    └─ (on_failure() call site only) → pause GitHub comment gets a classification summary line
  │
  └─ legacy fallback grep (kill-switch off / no pause-gate match)
       └─ replaced by: cli.py rate-limit-match --tmp-out ...  (uses shared RATE_LIMIT_RE, Decision 5)
            └─ session_window.RATE_LIMIT_RE  (same tightened regex error_signature.py imports)

error_signature.py classify()
  └─ imports RATE_LIMIT_RE from session_window (unchanged import site; inherits the
     tightened regex automatically) → "environmental:rate_limit"
```

`RATE_LIMIT_RE` (breaker + legacy-fallback shared) and `_SESSION_EXHAUSTION_RE`
(pause-gate-only, strict subset) are both built from four regex fragments —
`_RE_429`, `_RE_RATE_LIMIT`, `_RE_USAGE_SESSION`, `_RE_CREDIT` — so a transient
"HTTP 429 — rate limit exceeded" matches `RATE_LIMIT_RE` (real for the breaker) but not
`_SESSION_EXHAUSTION_RE` (not a session/window/balance exhaustion, must not pause).

## Tech Stack

- Python (`scripts/factory_core/session_window.py`, `cli.py`) — `re`, `json`, stdlib only.
- Bash (`entrypoint.sh`) — existing `python3 .../cli.py <subcommand>` + stdout-parsing
  convention (`grep -o 'key=value'`), extended with one `base64`/`python3 -c` decode step
  for the new snippet, matching the file's existing python3-does-the-logic /
  bash-just-plumbs style.
- `pytest` for `tests/test_factory_core_session_window.py` and
  `tests/test_factory_core_error_signature.py` (existing convention, `python -m pytest
  tests/ -v`).
- Bash for `tests/test_entrypoint_session_window.sh` (existing convention: source
  `entrypoint.sh` with `ENTRYPOINT_SOURCE_ONLY=1`, stub `git`/`gh`/`docker`/`claude`, run
  real `python3 cli.py` subprocesses against the branch's own code).

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/session_window.py` | Modified — two-tier context-required regex (Decisions 1+3), `_structured_events`/`_structured_status`/`_structured_resets_at` + rewritten `is_session_window_failure`/`parse_structured_reset_epoch` (Decision 2), new `match_snippet()` (Decision 4) |
| `scripts/factory_core/cli.py` | Modified — `_session_window_check` emits a `snippet_b64=<...>` stdout line; new `rate-limit-match` subcommand (Decision 5) |
| `entrypoint.sh` | Modified — `_handle_session_window_pause` decodes the snippet and extends the `run-record record --detail` call; `on_failure()`'s pause comment gets a classification-summary line; legacy fallback grep replaced by the `rate-limit-match` subcommand |
| `tests/test_factory_core_session_window.py` | Modified — regex-tightening fixtures, Cause-A structured-status fixtures, `match_snippet` unit tests, `rate-limit-match` CLI test |
| `tests/test_factory_core_error_signature.py` | Modified — breaker-side regression fixtures (no code change to `error_signature.py` itself — it inherits the tightened regex via its existing import) |
| `tests/test_entrypoint_session_window.sh` | Modified — Case A updated to the real pino payload shape, new `status=allowed` regression case, classification-summary/run-record-detail assertions on Case D |

---

## Task 1: Tighten `RATE_LIMIT_RE`; split `_SESSION_EXHAUSTION_RE` for the pause gate

**Files:** `scripts/factory_core/session_window.py`, `tests/test_factory_core_session_window.py`

### TDD Steps

1. Edit the import block at the top of `tests/test_factory_core_session_window.py` to
   also import `RATE_LIMIT_RE`:

```python
from factory_core.session_window import (
    is_session_window_failure,
    parse_structured_reset_epoch,
    parse_fallback_reset_epoch,
    compute_resume_epoch,
    write_pause_sentinel,
    check_and_pause,
    RATE_LIMIT_RE,
)
```

2. Re-fixture the existing `test_check_and_pause_writes_sentinel_and_returns_epoch` —
   under the new split, `"429 rate limit hit"` no longer contains exhaustion-specific
   phrasing:

```python
def test_check_and_pause_writes_sentinel_and_returns_epoch(tmp_path):
    text = "429 too many requests, session limit reached"
    epoch = check_and_pause(text, tmp_path, now_epoch=1_000_000,
                             buffer_minutes=5, fallback_minutes=30)
    assert epoch == 1_000_000 + 1800
    assert (tmp_path / "session-window-paused").read_text() == str(epoch)
```

3. Append these new tests to the same file:

```python
def test_is_session_window_failure_false_for_bare_429_rate_limit_hit():
    # Regression lock for the two-tier split: transient-shaped text (matches
    # RATE_LIMIT_RE via _RE_429/_RE_RATE_LIMIT) must not buy a factory pause.
    assert is_session_window_failure("429 rate limit hit") is False


def test_is_session_window_failure_false_for_transient_429_rate_limit_exceeded():
    # Real for the breaker (see test_classify_http_429_rate_limit_exceeded_is_environmental
    # in test_factory_core_error_signature.py) but not a session-window exhaustion.
    assert is_session_window_failure("HTTP 429 — rate limit exceeded") is False


def test_is_session_window_failure_true_for_reset_line_without_session_or_usage_word():
    assert is_session_window_failure("You've hit your limit · resets 1:40pm (UTC)") is True


def test_is_session_window_failure_true_for_claude_ai_usage_limit_reached_epoch_suffix():
    # Claude Code CLI's non-interactive output shape. No dedicated "|<epoch>"-suffix
    # parser is specified by any Decision in the spec, so only the classification is
    # asserted here -- resume-epoch derivation falls back to fallback_minutes. The spec's
    # own phrasing is conditional on this ("where the epoch suffix is parsed by...").
    assert is_session_window_failure("Claude AI usage limit reached|1736899200") is True


def test_rate_limit_re_rejects_own_module_comment_style_text():
    # "exhaustion" (not "exhausted") -- the fixed shape no longer collides.
    assert RATE_LIMIT_RE.search("# Guard against rate limit exhaustion") is None


def test_rate_limit_re_rejects_quoted_old_regex_source():
    old_source = 'r"usage limit|rate limit|429|credit balance|session limit"'
    assert RATE_LIMIT_RE.search(old_source) is None


def test_rate_limit_re_rejects_sha_embedded_429():
    assert RATE_LIMIT_RE.search("fixed in abc4291f2e") is None


def test_rate_limit_re_rejects_dollar_figure():
    assert RATE_LIMIT_RE.search("cost $0.429 total this run") is None


def test_rate_limit_re_rejects_issue_reference():
    assert RATE_LIMIT_RE.search("see #429 for context") is None


def test_rate_limit_re_rejects_scheduler_rate_limit_log_prose():
    # Forward regression lock: scheduler.sh emits "rate_limit remaining=..." (underscore),
    # which never collided with the space-delimited pre-fix regex either.
    assert RATE_LIMIT_RE.search("rate_limit remaining=4000 sleeping=30s until_reset") is None


def test_rate_limit_re_rejects_session_window_gate_log_prose():
    assert RATE_LIMIT_RE.search("session_window_gate=active resume_at=1784739600") is None


def test_rate_limit_re_still_matches_http_429_rate_limit_exceeded():
    assert RATE_LIMIT_RE.search("HTTP 429 — rate limit exceeded") is not None
```

4. Verify the new/modified tests fail (implementation not yet changed):

```bash
python -m pytest tests/test_factory_core_session_window.py -v -k \
  "bare_429_rate_limit_hit or transient_429 or reset_line_without or claude_ai_usage or rate_limit_re_rejects or rate_limit_re_still or writes_sentinel_and_returns_epoch"
```

   Expected: several failures — the old bare regex still matches the comment/SHA/dollar/
   issue-reference negatives, and `is_session_window_failure`/`RATE_LIMIT_RE` do not yet
   exist in the new split shape.

5. Implement two separate edits in `scripts/factory_core/session_window.py` (the
   `_STRUCTURED_MARKER`/`_HUMAN_RESET_RE`/`MAX_SESSION_WINDOW_HOURS` lines that sit
   between them are untouched by this task):

   **a.** Replace this exact block (the `RATE_LIMIT_RE` compile and the now-dead
   `_SUBSTRING_RE` alias, immediately below the module imports):

```python
RATE_LIMIT_RE = re.compile(
    r"usage limit|rate limit|429|credit balance|session limit", re.IGNORECASE
)
# Backward-compatible alias for existing in-module references; new external
# consumers should import the public RATE_LIMIT_RE name above.
_SUBSTRING_RE = RATE_LIMIT_RE
```

   with:

```python
_HTTP_ERR_CTX = (
    r"(?:https?|http/\d(?:\.\d)?|status(?:\s+code)?|code|error|err"
    r"|response|responded|returned|got|api)"
)

# "429" requires HTTP/error context on one side; guards against a preceding digit+dot
# ($0.429), '#'/':' (#429, file.py:429), or being embedded in an alphanumeric run (a SHA).
_RE_429 = (
    r"(?:"
    r"\b" + _HTTP_ERR_CTX + r"\b[\s:=,/\"'|-]{0,4}(?<![\w.#$])429(?![\w.])"
    r"|"
    r"(?<![\w.#$:])429(?![\w.])[\s:,()-]{0,3}(?:too\s+many\s+requests|rate[\s_-]?limit)"
    r")"
)

# "rate limit" requires an exhaustion/throttle verb in the same clause (bounded [^.\n]{0,40}
# gap, not `.*`, so it can't bridge across log lines or sentences).
_RE_RATE_LIMIT = (
    r"(?:rate[ _-]?limit(?:s|ed|ing)?\b[^.\n]{0,40}?"
    r"\b(?:exceeded|reached|hit|exhausted|throttl\w*|too\s+many\s+requests)\b"
    r"|\b(?:hit|exceeded|reached)\s+(?:the\s+|a\s+|your\s+|our\s+)?rate[ _-]?limit(?:s|ed)?\b"
    r"|\bbeing\s+rate[ _-]?limited\b)"
)

# "usage"/"session"/"weekly"/"5-hour limit" -- a pre-verb branch (real phrasing puts the
# verb before the noun, "hit your usage limit"), plus a reset-line shape naming neither
# "session" nor "usage" ("You've hit your limit · resets 1:40pm (UTC)").
_RE_USAGE_SESSION = (
    r"(?:(?:usage|session|weekly|5[ _-]?hour)[ _-]?limits?\b[^.\n]{0,40}?"
    r"\b(?:reached|exceeded|exhausted|hit|resets?|will\s+reset)\b"
    r"|\b(?:hit|reached|exceeded|exhausted|used\s+up|out\s+of)\s+"
    r"(?:your\s+|the\s+|my\s+|its\s+)?(?:\w+\s+){0,2}?"
    r"(?:usage|session|weekly|5[ _-]?hour)[ _-]?limits?\b"
    r"|\blimit\b[^.\n]{0,40}?\bresets\s+\d{1,2}(?::\d{2})?\s*[ap]m\s*\()"
)

# "credit balance" needs the same tightening even with no false-positive example in the
# issue: the bare regex source string is checked into the repo, so an agent quoting it
# would self-trigger. Anchored on Anthropic's actual API message.
_RE_CREDIT = (
    r"(?:credit\s+balance\b[^.\n]{0,30}?\b(?:too\s+low|insufficient|exhausted|depleted|is\s+0)\b"
    r"|\b(?:insufficient|low|zero|no)\s+credit\s+balance\b)"
)

RATE_LIMIT_RE = re.compile(
    "|".join([_RE_429, _RE_RATE_LIMIT, _RE_USAGE_SESSION, _RE_CREDIT]),
    re.IGNORECASE,
)

# Strict subset of RATE_LIMIT_RE used ONLY by the pause gate: a transient
# 429/"rate limit exceeded" is real for the breaker's environmental:rate_limit bucket
# (error_signature.py, unchanged import of RATE_LIMIT_RE above) but is not a session/
# window/balance exhaustion and must not buy a 30-minute factory-wide halt.
_SESSION_EXHAUSTION_RE = re.compile(
    "|".join([_RE_USAGE_SESSION, _RE_CREDIT]),
    re.IGNORECASE,
)
```

   (Note: this task does *not* introduce `_RE_CREDIT_ONLY` — Task 3 adds it later,
   alongside its only consumer, `match_snippet`.) The `_STRUCTURED_MARKER`,
   `_HUMAN_RESET_RE`, and `MAX_SESSION_WINDOW_HOURS` lines that currently follow the
   replaced block are untouched by this edit.

   **b.** Separately, replace the existing `is_session_window_failure` function (its
   location is unchanged — still directly below `MAX_SESSION_WINDOW_HOURS`):

```python
def is_session_window_failure(text: str) -> bool:
    return _STRUCTURED_MARKER in text or bool(_SUBSTRING_RE.search(text))
```

   with:

```python
def is_session_window_failure(text: str) -> bool:
    return _STRUCTURED_MARKER in text or bool(_SESSION_EXHAUSTION_RE.search(text))
```

   Confirm the alias is fully gone with
   `grep -rn _SUBSTRING_RE scripts/factory_core/session_window.py` (expect no hits).
   `scripts/factory_core/error_signature.py`'s module docstring separately mentions the
   string `_SUBSTRING_RE` in prose ("Mirrors session_window.py's `_SUBSTRING_RE`
   keyword-match style") — that is an unrelated, pre-existing comment, not a code
   reference, and `error_signature.py` is not in this task's file list; leave it
   untouched.

6. Verify all tests in the file pass:

```bash
python -m pytest tests/test_factory_core_session_window.py -v
```

   Expected: all tests pass, `0 failed`.

7. Commit:

```bash
git add scripts/factory_core/session_window.py tests/test_factory_core_session_window.py
git commit -m "fix(session-window): tighten RATE_LIMIT_RE with context-required alternatives; split pause-gate regex from breaker classification (#344)"
```

---

## Task 2: Fix the structured-marker path to read `status` and the nested `resetsAt` (Cause A)

**Files:** `scripts/factory_core/session_window.py`, `tests/test_factory_core_session_window.py`, `tests/test_entrypoint_session_window.sh`

Per the spec's Decision 2 caveat and Assumptions: the exact real payload field names below
(`rateLimitInfo`, `msg`, nested `resetsAt` as an epoch int) are the operator's
best-available-evidence from reviewing captured run output, not a committed fixture or
archived raw log in this repo. Before starting, search this run's own artifacts/logs (and
`runs.jsonl`/Seq if reachable) for a real `claude.rate_limit_event` line; if one turns up
with different field names, adjust `_structured_status`/`_structured_resets_at` below
accordingly — the *behavior* (don't pause on `status=="allowed"`, do read a nested reset
time) is the requirement, the exact key path is not verified firsthand. If no real sample
is found (expected, per the spec), proceed with the shape below as-is.

### TDD Steps

1. Rename the existing test `test_parse_structured_reset_epoch_real_claude_rate_limit_event_payload`
   to `test_parse_structured_reset_epoch_assumed_292_payload_shape` (it exercises the
   `{"event":"claude.rate_limit_event","resetsAt":"..."}` shape assumed by the original
   #292 design, not the real CLI payload — keep the fixture body unchanged, rename only).

2. Append these new tests to `tests/test_factory_core_session_window.py`:

```python
def test_is_session_window_failure_false_for_status_allowed_real_shape():
    # The literal #332 trigger: every healthy run emits this marker.
    text = ('{"level":40,"time":1784739600123,"rateLimitInfo":{"status":"allowed",'
            '"resetsAt":1784739600,"rateLimitType":"five_hour"},"msg":"claude.rate_limit_event"}')
    assert is_session_window_failure(text) is False


def test_compute_resume_epoch_none_for_status_allowed_real_shape():
    text = ('{"level":40,"rateLimitInfo":{"status":"allowed","resetsAt":1784739600,'
            '"rateLimitType":"five_hour"},"msg":"claude.rate_limit_event"}')
    assert compute_resume_epoch(text, now_epoch=1784730000, buffer_minutes=5, fallback_minutes=30) is None


def test_is_session_window_failure_true_for_status_rejected_real_shape():
    text = ('{"level":40,"rateLimitInfo":{"status":"rejected","resetsAt":1784739600,'
            '"rateLimitType":"five_hour"},"msg":"claude.rate_limit_event"}')
    assert is_session_window_failure(text) is True


def test_compute_resume_epoch_uses_nested_resetsAt_for_status_rejected():
    text = ('{"level":40,"rateLimitInfo":{"status":"rejected","resetsAt":1784739600,'
            '"rateLimitType":"five_hour"},"msg":"claude.rate_limit_event"}')
    now = 1784730000
    result = compute_resume_epoch(text, now, buffer_minutes=5, fallback_minutes=30)
    assert result == 1784739600 + 5 * 60


def test_is_session_window_failure_true_when_allowed_precedes_rejected():
    # Direct lock on the any-event rule: an early healthy marker must not shadow a
    # later genuine rejection in the same stdout.
    text = (
        '{"rateLimitInfo":{"status":"allowed","resetsAt":1784730000},"msg":"claude.rate_limit_event"}\n'
        '{"rateLimitInfo":{"status":"rejected","resetsAt":1784739600},"msg":"claude.rate_limit_event"}'
    )
    assert is_session_window_failure(text) is True


def test_compute_resume_epoch_uses_rejected_not_shadowed_by_earlier_allowed():
    text = (
        '{"rateLimitInfo":{"status":"allowed","resetsAt":1784730000},"msg":"claude.rate_limit_event"}\n'
        '{"rateLimitInfo":{"status":"rejected","resetsAt":1784739600},"msg":"claude.rate_limit_event"}'
    )
    result = compute_resume_epoch(text, 1784730000, buffer_minutes=5, fallback_minutes=30)
    assert result == 1784739600 + 300


def test_is_session_window_failure_true_for_allowed_marker_plus_human_readable_text():
    # allowed events are neutral, not a veto -- the substring branch still runs over
    # the full text.
    text = (
        '{"rateLimitInfo":{"status":"allowed","resetsAt":1784730000},"msg":"claude.rate_limit_event"}\n'
        "You've hit your session limit · resets 11:10pm (UTC)"
    )
    assert is_session_window_failure(text) is True
```

3. Verify the new tests fail and the two now-stale existing tests
   (`test_is_session_window_failure_detects_structured_signal`,
   `test_parse_structured_reset_epoch_parses_resetsAt`) still pass unmodified — the
   #292-assumed shape has no `status` field so it must keep classifying as exhaustion:

```bash
python -m pytest tests/test_factory_core_session_window.py -v -k \
  "status_allowed or status_rejected or allowed_precedes_rejected or allowed_marker_plus_human"
```

   Expected: the new tests fail (current code pauses unconditionally on marker presence
   and never reads `resetsAt` from `rateLimitInfo`).

4. Implement: in `scripts/factory_core/session_window.py`, insert these three helpers
   immediately after the `_STRUCTURED_MARKER = "claude.rate_limit_event"` line, then
   replace `is_session_window_failure` (from Task 1) and `parse_structured_reset_epoch`
   with the versions below:

```python
def _structured_events(text: str) -> list:
    """Return every parsed claude.rate_limit_event payload in `text`, in order of
    appearance. Accepts both the real Claude Code CLI shape (pino-style: a "msg" field,
    fields nested under "rateLimitInfo") and the #292-assumed shape (top-level "event"
    and "resetsAt"). Returning ALL events, not the first that parses, is load-bearing: a
    healthy run's early status=allowed marker must not shadow a later genuine rejection.
    """
    events = []
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
        if event.get("event") == _STRUCTURED_MARKER or event.get("msg") == _STRUCTURED_MARKER:
            events.append(event)
    return events


def _structured_status(event: dict) -> Optional[str]:
    info = event.get("rateLimitInfo")
    if isinstance(info, dict) and "status" in info:
        return info["status"]
    return event.get("status")  # None for the #292 assumed shape, which never carried status


def _structured_resets_at(event: dict):
    info = event.get("rateLimitInfo")
    if isinstance(info, dict) and "resetsAt" in info:
        return info["resetsAt"]
    return event.get("resetsAt")
```

```python
def is_session_window_failure(text: str) -> bool:
    events = _structured_events(text)
    if any(_structured_status(e) != "allowed" for e in events):
        return True
    return bool(_SESSION_EXHAUSTION_RE.search(text))


def parse_structured_reset_epoch(text: str) -> Optional[int]:
    for event in _structured_events(text):
        if _structured_status(event) == "allowed":
            continue
        resets_at = _structured_resets_at(event)
        if not resets_at:
            continue
        # Handle an epoch (int/float, seconds since epoch) resetsAt in addition to the
        # documented ISO-8601 string, so a differently-shaped payload doesn't silently
        # no-op the structured path and fall back to the 30-min default (#35 review).
        if isinstance(resets_at, (int, float)) and not isinstance(resets_at, bool):
            return int(resets_at)
        try:
            dt = datetime.fromisoformat(str(resets_at).replace("Z", "+00:00"))
        except ValueError:
            continue
        return int(dt.timestamp())
    return None
```

5. Verify all tests in the file pass:

```bash
python -m pytest tests/test_factory_core_session_window.py -v
```

   Expected: all tests pass, `0 failed`.

6. Update `tests/test_entrypoint_session_window.sh` Section A to the real pino shape
   with a non-`allowed` status and a nested epoch `resetsAt`, and add a new Section A2
   that is the direct shell-level regression lock for the #332 incident. Replace the
   fixture-generation block under `echo "--- A: matched..."` (the `RESET_ISO=`/`printf`
   pair) with:

```bash
echo "--- A: matched (structured rate_limit_event line, real pino shape, status=rejected) ---"
SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-sw-statedir-XXXXXX)
NOW=$(date -u +%s)
RESET_EPOCH=$((NOW+600))
TMP_OUT=$(mktemp /tmp/ep-sw-out-XXXXXX)
printf 'some claude output\n{"level":40,"time":%s000,"rateLimitInfo":{"status":"rejected","resetsAt":%s,"rateLimitType":"five_hour"},"msg":"claude.rate_limit_event"}\n' \
  "$NOW" "$RESET_EPOCH" > "$TMP_OUT"
```

   (`EXPECTED_EPOCH=$((NOW + 600 + 300))` below is unchanged — `RESET_EPOCH` is still
   `NOW+600`.) Immediately after the existing Section A assertions and before
   `echo "--- B: unmatched..."`, insert:

```bash
echo ""
echo "--- A2: unmatched (structured rate_limit_event line, status=allowed) — direct #332 regression lock ---"
rm -f "${SCHEDULER_STATE_DIR}/session-window-paused"
TMP_OUT_ALLOWED=$(mktemp /tmp/ep-sw-out-allowed-XXXXXX)
printf 'some claude output\n{"level":40,"rateLimitInfo":{"status":"allowed","resetsAt":%s,"rateLimitType":"five_hour"},"msg":"claude.rate_limit_event"}\n' \
  "$((NOW+18000))" > "$TMP_OUT_ALLOWED"
_handle_session_window_pause "$TMP_OUT_ALLOWED"
RC_ALLOWED=$?
assert_eq "status=allowed only → returns 1 (falls through)" "1" "$RC_ALLOWED"
assert_true "no sentinel written for status=allowed" \
  "[ ! -f '${SCHEDULER_STATE_DIR}/session-window-paused' ]"
rm -f "$TMP_OUT_ALLOWED"
```

7. Run the shell test to confirm it passes end-to-end against the branch's own
   `cli.py`/`session_window.py` (no failures expected here since Task 2's Python change
   already landed in step 4):

```bash
bash tests/test_entrypoint_session_window.sh
```

   Expected: every `PASS:` line, `FAILED=0` in the summary.

8. Commit:

```bash
git add scripts/factory_core/session_window.py tests/test_factory_core_session_window.py tests/test_entrypoint_session_window.sh
git commit -m "fix(session-window): read rateLimitInfo.status before classifying a claude.rate_limit_event marker as exhaustion (#344, Cause A)"
```

---

## Task 3: `match_snippet()` — the matched pattern, offset, window, and branch

**Files:** `scripts/factory_core/session_window.py`, `tests/test_factory_core_session_window.py`

### TDD Steps

1. Extend the import block at the top of `tests/test_factory_core_session_window.py`
   (added in Task 1) to also pull in `match_snippet`:

```python
from factory_core.session_window import (
    is_session_window_failure,
    parse_structured_reset_epoch,
    parse_fallback_reset_epoch,
    compute_resume_epoch,
    write_pause_sentinel,
    check_and_pause,
    RATE_LIMIT_RE,
    match_snippet,
)
```

   Then add these tests:

```python
def test_match_snippet_none_when_no_pause_worthy_signal():
    assert match_snippet("unrelated stack trace") is None


def test_match_snippet_substring_path_returns_matched_offset_window_branch():
    text = "noise noise " + ("x" * 100) + " session limit reached " + ("y" * 100)
    result = match_snippet(text, radius=20)
    assert result["matched"] == "session limit reached"
    assert result["branch"] == "usage/session-limit"
    assert result["offset"] == text.index("session limit reached")
    assert "session limit reached" in result["window"]
    assert len(result["window"]) <= len("session limit reached") + 40


def test_match_snippet_credit_branch():
    result = match_snippet("insufficient credit balance, please top up")
    assert result["branch"] == "credit-balance"


def test_match_snippet_structured_path_returns_first_non_allowed_event():
    text = (
        '{"rateLimitInfo":{"status":"allowed","resetsAt":1},"msg":"claude.rate_limit_event"}\n'
        '{"rateLimitInfo":{"status":"rejected","resetsAt":2},"msg":"claude.rate_limit_event"}'
    )
    result = match_snippet(text)
    assert result == {
        "matched": "claude.rate_limit_event",
        "offset": text.find("claude.rate_limit_event"),
        "window": {"rateLimitInfo": {"status": "rejected", "resetsAt": 2}, "msg": "claude.rate_limit_event"},
        "branch": "status=rejected",
    }


def test_match_snippet_falls_through_to_substring_when_only_allowed_events():
    text = ('{"rateLimitInfo":{"status":"allowed","resetsAt":1},"msg":"claude.rate_limit_event"}\n'
            "session limit reached")
    result = match_snippet(text)
    assert result["matched"] == "session limit reached"
```

2. Verify these fail (`match_snippet` does not exist yet):

```bash
python -m pytest tests/test_factory_core_session_window.py -v -k match_snippet
```

   Expected: `ImportError`/collection failure.

3. Implement: append to `scripts/factory_core/session_window.py`, after
   `write_pause_sentinel` and before `check_and_pause`:

```python
_RE_CREDIT_ONLY = re.compile(_RE_CREDIT, re.IGNORECASE)


def match_snippet(text: str, radius: int = 80) -> Optional[dict]:
    """Return the match that caused a pause, plus context, or None if the text doesn't
    represent a pause-worthy failure. Used to make a session-window pause diagnosable
    after the container that produced it is gone."""
    for event in _structured_events(text):
        status = _structured_status(event)
        if status == "allowed":
            continue  # neutral -- keep looking, then fall through to the substring path
        offset = text.find(_STRUCTURED_MARKER)
        return {
            "matched": _STRUCTURED_MARKER,
            "offset": offset,
            "window": event,
            "branch": f"status={status}",
        }
    match = _SESSION_EXHAUSTION_RE.search(text)
    if match is None:
        return None
    start, end = max(0, match.start() - radius), min(len(text), match.end() + radius)
    branch = "credit-balance" if _RE_CREDIT_ONLY.search(match.group(0)) else "usage/session-limit"
    return {
        "matched": match.group(0),
        "offset": match.start(),
        "window": text[start:end],
        "branch": branch,
    }
```

4. Verify all tests in the file pass:

```bash
python -m pytest tests/test_factory_core_session_window.py -v
```

   Expected: all tests pass, `0 failed`.

5. Commit:

```bash
git add scripts/factory_core/session_window.py tests/test_factory_core_session_window.py
git commit -m "feat(session-window): add match_snippet() for post-hoc pause diagnosis (#344, Decision 4)"
```

---

## Task 4: `cli.py session-window-check` emits the snippet as `snippet_b64`

**Files:** `scripts/factory_core/cli.py`, `tests/test_factory_core_session_window.py`

### TDD Steps

1. Extend the mid-file import block that currently reads (immediately above
   `test_cli_session_window_check_matched`):

```python
import subprocess
import sys as _sys
```

   to:

```python
import base64
import subprocess
import sys as _sys
```

   Then add this test near the existing `test_cli_session_window_check_matched`:

```python
def test_cli_session_window_check_matched_includes_snippet_b64(tmp_path):
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
    lines = [l for l in result.stdout.splitlines() if l.startswith("snippet_b64=")]
    assert len(lines) == 1
    import json as _json
    decoded = _json.loads(base64.b64decode(lines[0].split("=", 1)[1]).decode())
    assert decoded["matched"] == "session limit reached"
    assert decoded["branch"] == "usage/session-limit"


def test_cli_session_window_check_unmatched_has_no_snippet_b64(tmp_path):
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
    assert "snippet_b64=" not in result.stdout
```

2. Verify these fail (no `snippet_b64` line is printed yet):

```bash
python -m pytest tests/test_factory_core_session_window.py -v -k snippet_b64
```

   Expected: `test_cli_session_window_check_matched_includes_snippet_b64` fails
   (`assert len(lines) == 1` — got 0); the unmatched test already passes trivially.

3. Implement: replace `_session_window_check` in `scripts/factory_core/cli.py`
   (currently lines 98–113) with:

```python
def _session_window_check(args):
    import base64
    import json as _json
    import time
    from factory_core.session_window import check_and_pause, match_snippet
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
        snippet = match_snippet(text)
        if snippet is not None:
            payload = dict(snippet)
            if isinstance(payload["window"], dict):
                payload["window"] = _json.dumps(payload["window"])
            encoded = base64.b64encode(_json.dumps(payload).encode()).decode()
            print(f"snippet_b64={encoded}")
    else:
        print("matched=false resume_epoch=0")
```

4. Verify all tests in the file pass:

```bash
python -m pytest tests/test_factory_core_session_window.py -v
```

   Expected: all tests pass, `0 failed`.

5. Commit:

```bash
git add scripts/factory_core/cli.py tests/test_factory_core_session_window.py
git commit -m "feat(cli): session-window-check emits the match snippet as a base64 stdout line (#344, Decision 4)"
```

---

## Task 5: `entrypoint.sh` — snippet through `run-record`, classification summary in the pause comment

**Files:** `entrypoint.sh`, `tests/test_entrypoint_session_window.sh`

### TDD Steps

1. Section D's own fixture is still the #292-assumed shape (no `status` field), which
   would make `match_snippet`'s `branch` read `status=None` instead of `status=rejected`.
   Update it to the real pino shape, matching Task 2 step 6's change to Section A.
   Replace Section D's fixture-generation block (currently
   `RESET_ISO_D=$(date -u -d "@$(( $(date -u +%s) + 600 ))" +%Y-%m-%dT%H:%M:%SZ)` down
   through the `printf ... > "$TMP_OUT"` line, right before `false` / `on_failure`) with:

```bash
RESET_EPOCH_D=$(( $(date -u +%s) + 600 ))
TMP_OUT=$(mktemp /tmp/ep-sw-out-d-XXXXXX)
printf 'some claude output\n{"level":40,"rateLimitInfo":{"status":"rejected","resetsAt":%s,"rateLimitType":"five_hour"},"msg":"claude.rate_limit_event"}\n' \
  "$RESET_EPOCH_D" > "$TMP_OUT"
```

2. Add assertions to that same Section D, immediately after the existing
   `assert_true "pause comment posted under the session-window marker" ...` block and
   before `rm -f "$TMP_OUT"` / `rm -rf "$SCHEDULER_STATE_DIR" ...` at the end of that
   section:

```bash
assert_true "pause comment includes a classification summary line" \
  "grep -q 'Classification: matched' '${COMMENT_LOG_DIR}/dfsessionwindowpause.md'"
assert_true "pause comment classification names the rejected status" \
  "grep -q 'status=rejected' '${COMMENT_LOG_DIR}/dfsessionwindowpause.md'"
assert_true "runs.jsonl paused record includes matched_pattern detail" \
  "grep -q 'claude.rate_limit_event' '${SCHEDULER_STATE_DIR}/runs.jsonl'"
```

   Section D only exercises the structured-marker branch of the new classification
   summary (`Classification: matched \`claude.rate_limit_event\` (status=...)`) — add a
   Section D2 covering the substring-path branch (`(... branch) at offset ...`) and the
   `SAFE_MATCHED_PATTERN` backtick-stripping, right after Section D's `rm -rf
   "$SCHEDULER_STATE_DIR" "$ARTIFACTS_DIR" "$COMMENT_LOG_DIR"` line and before
   `echo "--- E: on_failure() guard ..."`:

```bash
echo ""
echo "--- D2: on_failure() guard — substring-path classification summary (branch label, no backticks in matched text) ---"
SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-sw-statedir-d2-XXXXXX)
export SCHEDULER_STATE_DIR
ARTIFACTS_DIR=$(mktemp -d /tmp/ep-sw-artifacts-d2-XXXXXX)
export ARTIFACTS_DIR
ISSUE_NUM=292
INTENT=fix
RUN_ID=test-run-d2
RUN_STARTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
SESSION_WINDOW_BACKOFF_ENABLED=true

run_post_mortem() { :; }
set_board_status() { return 0; }
COMMENT_LOG_DIR=$(mktemp -d /tmp/ep-sw-comments-d2-XXXXXX)
post_or_update_comment() {
  local marker="$1" body="$2"
  local safe
  safe=$(echo "$marker" | tr -cd 'a-zA-Z0-9')
  echo "$body" > "${COMMENT_LOG_DIR}/${safe}.md"
}
post_cost_report() { :; }

TMP_OUT=$(mktemp /tmp/ep-sw-out-d2-XXXXXX)
printf "You've hit your session limit · resets 11:10pm (UTC)\n" > "$TMP_OUT"

false
on_failure
set +e  # see the comment on section D's on_failure() call for why this is required

assert_true "substring-path pause comment names the usage/session-limit branch with an offset" \
  "grep -q 'usage/session-limit branch) at offset' '${COMMENT_LOG_DIR}/dfsessionwindowpause.md'"
assert_true "substring-path matched text has no stray backtick in the comment" \
  "! grep -qE 'matched \`[^\`]*\`[^\`]*\`' '${COMMENT_LOG_DIR}/dfsessionwindowpause.md'"

rm -f "$TMP_OUT"
rm -rf "$SCHEDULER_STATE_DIR" "$ARTIFACTS_DIR" "$COMMENT_LOG_DIR"
```

3. Run the shell test to confirm the new Section D and D2 assertions fail (nothing
   populates `Classification:`/`matched_pattern` yet):

```bash
bash tests/test_entrypoint_session_window.sh
```

   Expected: Section D reports `FAIL` for the three new assertions; `FAILED` count > 0
   in the summary.

4. Implement: replace `_handle_session_window_pause` in `entrypoint.sh` (currently lines
   237–270) with:

```bash
_handle_session_window_pause() {
  local tmp_out="$1"
  [ "${SESSION_WINDOW_BACKOFF_ENABLED:-true}" = "true" ] || return 1

  local sw_result sw_rc
  # TARGET-PATH: cli.py resolves under dark-factory/ in the clone — target's own copy
  # until P3 cleanup, baked self-contained fallback copy afterwards (df#14)
  sw_result=$(python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" session-window-check \
    --tmp-out "$tmp_out" \
    --state-dir "${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}" \
    --buffer-minutes "${SESSION_WINDOW_BUFFER_MINUTES:-5}" \
    --fallback-minutes "${SESSION_WINDOW_FALLBACK_MINUTES:-30}" 2>&1)
  sw_rc=$?
  if [ "$sw_rc" -ne 0 ]; then
    echo "WARNING: session-window-check failed (exit ${sw_rc}) — path/import likely broken, falling through to legacy detection: ${sw_result}" >&2
    return 1
  fi

  local matched resume_epoch snippet_b64
  matched=$(echo "$sw_result" | grep -o 'matched=[a-z]*' | cut -d= -f2)
  resume_epoch=$(echo "$sw_result" | grep -o 'resume_epoch=[0-9]*' | cut -d= -f2)
  [ "$matched" = "true" ] || return 1
  snippet_b64=$(echo "$sw_result" | grep -o 'snippet_b64=[A-Za-z0-9+/=]*' | cut -d= -f2-)

  local resume_iso
  resume_iso=$(date -u -d "@${resume_epoch}" +%FT%TZ 2>/dev/null || echo "unknown")
  echo "session-window exhausted — dispatch paused until ${resume_iso}"

  # Not the system of record (stderr is discarded under production -d --rm dispatch) --
  # these three globals (no `local`) are the handoff to on_failure()'s comment builder:
  # on_failure() invokes THIS function once, directly in its own `if` condition
  # (entrypoint.sh:426); since that's a plain function call, not a subshell, the globals
  # set here are still visible in on_failure()'s body right after the call returns.
  SESSION_WINDOW_MATCHED_PATTERN=""
  SESSION_WINDOW_MATCH_OFFSET=""
  SESSION_WINDOW_MATCH_BRANCH=""
  local detail_args=()
  if [ -n "$snippet_b64" ]; then
    echo "session-window match snippet (base64): ${snippet_b64}" >&2
    # Plain string concatenation, not an f-string: the script runs inside bash single
    # quotes, so python's own double-quoted dict keys need no escaping -- backslash-
    # escaping them inside an f-string expression is a SyntaxError on every Python
    # version (the trailing `2>/dev/null` would otherwise swallow that error silently).
    eval "$(echo "$snippet_b64" | base64 -d 2>/dev/null | python3 -c '
import json, sys, shlex
d = json.load(sys.stdin)
print("SESSION_WINDOW_MATCHED_PATTERN=" + shlex.quote(str(d.get("matched", ""))))
print("SESSION_WINDOW_MATCH_OFFSET=" + shlex.quote(str(d.get("offset", ""))))
print("SESSION_WINDOW_MATCH_BRANCH=" + shlex.quote(str(d.get("branch", ""))))
' 2>/dev/null)"
    detail_args=(--detail "matched_pattern=${SESSION_WINDOW_MATCHED_PATTERN}" \
      "match_offset=${SESSION_WINDOW_MATCH_OFFSET}" "snippet_b64=b64:${snippet_b64}")
  fi

  python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record record \
    --run-id "${RUN_ID:-unknown}" \
    --issue "${ISSUE_NUM:-0}" \
    --intent "${INTENT:-unknown}" \
    --stage paused \
    --verdict paused \
    "${detail_args[@]}" || true
  return 0
}
```

5. Update `on_failure()`'s pause-comment block (currently lines 430–441) to append the
   classification summary line, reading the globals `_handle_session_window_pause` just
   set as a side effect of the call in this same `if`. `SAFE_MATCHED_PATTERN` strips
   backticks so a matched literal containing one can't corrupt the comment body (the
   same corruption risk Decision 4 calls out for the raw window, on a smaller scale):

```bash
    if [ -n "${ISSUE_NUM:-}" ] && [ "$INTENT" != "close" ]; then
      local RESUME_EPOCH RESUME_ISO SUMMARY_LINE SAFE_MATCHED_PATTERN
      RESUME_EPOCH=$(cat "${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}/session-window-paused" 2>/dev/null || echo "")
      RESUME_ISO=$(date -u -d "@${RESUME_EPOCH}" +%FT%TZ 2>/dev/null || echo "unknown")
      SUMMARY_LINE=""
      if [ -n "${SESSION_WINDOW_MATCHED_PATTERN:-}" ]; then
        SAFE_MATCHED_PATTERN=$(printf '%s' "${SESSION_WINDOW_MATCHED_PATTERN}" | tr -d '`')
        if [ "${SESSION_WINDOW_MATCHED_PATTERN}" = "claude.rate_limit_event" ]; then
          SUMMARY_LINE="Classification: matched \`${SAFE_MATCHED_PATTERN}\` (${SESSION_WINDOW_MATCH_BRANCH})"
        else
          SUMMARY_LINE="Classification: matched \`${SAFE_MATCHED_PATTERN}\` (${SESSION_WINDOW_MATCH_BRANCH} branch) at offset ${SESSION_WINDOW_MATCH_OFFSET}"
        fi
      fi
      post_or_update_comment "$DF_SESSION_WINDOW_PAUSE_MARKER" \
        "${DF_SESSION_WINDOW_PAUSE_MARKER}
⏸️ **Dark Factory — Paused** (session window)

Claude session window exhausted mid-run. Dispatch resumes automatically at \`${RESUME_ISO}\`
(scheduler-enforced). The scheduler reconciles this issue's board state on its next poll —
no action needed.
${SUMMARY_LINE}"
    fi
```

6. Run `bash -n entrypoint.sh` to catch any syntax error before running the test suite:

```bash
bash -n entrypoint.sh
```

   Expected: no output, exit code 0.

7. Verify the shell test passes:

```bash
bash tests/test_entrypoint_session_window.sh
```

   Expected: every `PASS:` line, `FAILED=0` in the summary.

8. Commit:

```bash
git add entrypoint.sh tests/test_entrypoint_session_window.sh
git commit -m "feat(entrypoint): thread the match snippet into run-record detail and the pause comment's classification summary (#344, Decision 4)"
```

---

## Task 6: Eliminate `entrypoint.sh`'s hardcoded duplicate grep via a `rate-limit-match` CLI subcommand

**Files:** `scripts/factory_core/cli.py`, `entrypoint.sh`, `tests/test_factory_core_session_window.py`

### TDD Steps

1. Add these tests to `tests/test_factory_core_session_window.py`, mirroring the
   existing `test_cli_session_window_check_matched`/`_unmatched` pattern:

```python
def test_cli_rate_limit_match_true(tmp_path):
    tmp_out = tmp_path / "run.out"
    tmp_out.write_text("429 too many requests, rate limit exceeded")
    result = subprocess.run(
        [_sys.executable,
         str(Path(__file__).resolve().parents[1] / "scripts" / "factory_core" / "cli.py"),
         "rate-limit-match", "--tmp-out", str(tmp_out)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "matched=true"


def test_cli_rate_limit_match_false(tmp_path):
    tmp_out = tmp_path / "run.out"
    tmp_out.write_text("unrelated stack trace")
    result = subprocess.run(
        [_sys.executable,
         str(Path(__file__).resolve().parents[1] / "scripts" / "factory_core" / "cli.py"),
         "rate-limit-match", "--tmp-out", str(tmp_out)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "matched=false"
```

2. Verify these fail (`rate-limit-match` subcommand does not exist yet):

```bash
python -m pytest tests/test_factory_core_session_window.py -v -k rate_limit_match
```

   Expected: both fail — argparse exits non-zero with "invalid choice: 'rate-limit-match'".

3. Implement in `scripts/factory_core/cli.py`: add a handler function near
   `_session_window_check`:

```python
def _rate_limit_match(args):
    from factory_core.session_window import RATE_LIMIT_RE
    tmp_out_path = Path(args.tmp_out)
    text = tmp_out_path.read_text(errors="replace") if tmp_out_path.exists() else ""
    matched = bool(RATE_LIMIT_RE.search(text))
    print(f"matched={'true' if matched else 'false'}")
```

   and register the subparser immediately after the existing `session-window-check`
   subparser block (the one ending in `sw.set_defaults(func=_session_window_check)`;
   note Task 4 did not change this block's location, only the body of the handler
   function above it):

```python
    rlm = sub.add_parser("rate-limit-match")
    rlm.add_argument("--tmp-out", required=True)
    rlm.set_defaults(func=_rate_limit_match)
```

4. Verify all tests in the file pass:

```bash
python -m pytest tests/test_factory_core_session_window.py -v
```

   Expected: all tests pass, `0 failed`.

5. Replace the legacy grep in `entrypoint.sh` (currently line 789):

```bash
    if grep -qiE "usage limit|rate limit|429|credit balance|session limit" "$TMP_OUT"; then
```

   with:

```bash
    if python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" rate-limit-match \
        --tmp-out "$TMP_OUT" | grep -q '^matched=true$'; then
```

   No other line in that branch changes — `RESET_TIME`/`RESET_TZ` parsing, `SLEEP_SECS`
   math, and the 90000s failsafe cap stay byte-identical (this ticket only
   de-duplicates the match-detection line; see the spec's Known limitations for why the
   legacy fallback keeps the breaker-shared `RATE_LIMIT_RE`, not the narrower pause-gate
   predicate). This branch is the un-executable main retry loop per this test file's own
   header comment — verify by code review and syntax check only, not a new shell test.

6. Verify the syntax and re-run the full shell test to confirm nothing else broke:

```bash
bash -n entrypoint.sh
bash tests/test_entrypoint_session_window.sh
```

   Expected: `bash -n` produces no output; the shell test reports every `PASS:` line,
   `FAILED=0`.

7. Commit:

```bash
git add scripts/factory_core/cli.py entrypoint.sh tests/test_factory_core_session_window.py
git commit -m "refactor(entrypoint): replace the hardcoded legacy rate-limit grep with the shared RATE_LIMIT_RE via a new CLI subcommand (#344, Decision 5)"
```

---

## Task 7: `error_signature.py` breaker-side regression tests

**Files:** `tests/test_factory_core_error_signature.py` (no change to `error_signature.py` itself — it imports `RATE_LIMIT_RE` from `session_window` and inherits Task 1's tightening automatically)

### TDD Steps

1. Add these tests to `tests/test_factory_core_error_signature.py`:

```python
def test_classify_rejects_sha_embedded_429_negative():
    assert _classify(text="fixed in abc4291f2e") != "environmental:rate_limit"


def test_classify_rejects_dollar_figure_negative():
    assert _classify(text="cost $0.429 total this run") != "environmental:rate_limit"


def test_classify_rejects_issue_reference_negative():
    assert _classify(text="see #429 for context") != "environmental:rate_limit"


def test_classify_http_429_rate_limit_exceeded_is_environmental():
    # Transient-429-still-classifies-for-the-breaker case (Decision 3): real for the
    # breaker even though the paired is_session_window_failure test in
    # test_factory_core_session_window.py asserts False for the same string.
    assert _classify(text="HTTP 429 — rate limit exceeded") == "environmental:rate_limit"
```

2. Verify these fail against the pre-Task-1 regex — **skip this red step if Task 1
   already landed** (these fixtures were already proven to reject/accept correctly by
   Task 1's `RATE_LIMIT_RE`-level tests; this task only adds the `classify()`-level
   assertions). If run before Task 1: `test_classify_rejects_sha_embedded_429_negative`,
   `..._dollar_figure_negative`, and `..._issue_reference_negative` fail (old bare regex
   matches `429`).

3. No implementation change needed. Run the full file plus the three pre-existing
   pinned tests to confirm nothing regressed:

```bash
python -m pytest tests/test_factory_core_error_signature.py -v
```

   Expected: all tests pass, including the unmodified `test_rate_limit`,
   `test_rate_limit_session_limit_string`, and
   `test_environmental_signatures_have_no_exit_code_suffix`.

4. Commit:

```bash
git add tests/test_factory_core_error_signature.py
git commit -m "test(error-signature): lock in the tightened RATE_LIMIT_RE's breaker-side negatives/positives (#344)"
```

---

## Final verification

After Task 7, run the full suite once to confirm the whole ticket is green together:

```bash
python -m pytest tests/ -v
bash tests/test_entrypoint_session_window.sh
bash -n entrypoint.sh
```

Expected: `python -m pytest tests/ -v` reports `0 failed`; the shell test reports every
`PASS:` line and `FAILED=0`; `bash -n` produces no output.

## Note for implementation (not a task — carries no action item)

Per the spec's Known limitations: `entrypoint.sh`'s own runtime calls resolve
`cli.py`/`session_window.py` under `$CLONE_DIR/dark-factory/scripts` — a one-time vendor
of the image-baked `/opt/dark-factory/scripts` copied at container bootstrap, not a live
mirror of this branch. This ticket's fixes take effect for tests/conformance/code-review
immediately (they import `scripts/factory_core/*` directly, unprefixed) but for live
production dispatch only after the next image rebuild/publish — `deploy/**`-gated,
human-only, out of scope here. Worth restating in the implement phase's PR description so
a reviewer doesn't assume "tests pass" means "the #332 false pause stops recurring
immediately on merge."

Per `.archon/memory/codebase-patterns.md`'s pinned `[PATTERN]` (issue #42): this spec
(`docs/superpowers/specs/2026-08-22-session-window-rate-limit-classifier-tightening-design.md`)
and this plan live on the `refine/issue-344-...` branch and do not transfer to the
`feat/issue-344-...` implementation branch automatically — the implement phase must copy
both onto its own branch and commit them itself before starting Task 1.
