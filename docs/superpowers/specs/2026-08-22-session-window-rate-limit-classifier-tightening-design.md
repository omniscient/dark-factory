# Session-window/breaker rate-limit classifier: fix the structured-marker path, tighten `RATE_LIMIT_RE`, split the pause gate, log the match

**Status:** design (revision 2 — returned for revision by operator review, see Revision history)
**Date:** 2026-08-22
**Issue:** #344
**Related:** #292 (predicted the substring false-positive class), #334 (orphan-sweep-before-sentinel-gate
ordering bug — separate ticket, not touched here), #341 (retry burned by this incident), #35/#305
(shipped/hardened the backoff mechanism this classifier feeds)

## Revision history

**Revision 1** (16:20Z) diagnosed the incident as a pure `RATE_LIMIT_RE` substring problem and
proposed tightening it plus splitting the pause gate from the breaker's classification. Operator
review (16:30Z) confirmed that work should be kept but identified that it misdiagnoses the
incident's actual trigger and leaves it unfixed: **every healthy run's captured stdout already
contains a `claude.rate_limit_event` structured marker line**, and `is_session_window_failure`
treats the marker's mere presence as exhaustion — it never reads the payload's status. Revision 2
(this document) adds that fix as a co-equal, higher-priority decision and folds the operator's
required test/detail-plumbing corrections into Decisions 1, 3, and 4 below.

## Problem

On 2026-08-21 21:14–21:15Z, a #332 implement run was classified as a Claude Max 5h session-window
exhaustion and paused **all** factory dispatch for 30 minutes (`session_window_fallback_minutes`,
`config/config.yaml`). The account's 5h window was actually at 39% utilization at that moment, and
the very next run reported `rateLimitInfo.status=allowed` for the same window. The pause was a
false positive, and it was expensive: combined with #334 (orphan sweep running before the sentinel
gate, a separate bug), it moved #332 to Blocked, burned a retry (#341), and caused the retry to
re-run the entire implement plan from Task 1.

Two co-equal defects in `scripts/factory_core/session_window.py` produce the same symptom (a false
30-minute factory-wide pause), and either alone reproduces the incident:

### Cause A (primary — verified against real run output and current main): the structured-marker path never reads status

```python
_STRUCTURED_MARKER = "claude.rate_limit_event"

def is_session_window_failure(text: str) -> bool:
    return _STRUCTURED_MARKER in text or bool(_SUBSTRING_RE.search(text))
```

`entrypoint.sh` captures the Claude Code runner's full stdout (`tee "$TMP_OUT"`, `entrypoint.sh:780`).
That stream routinely contains a pino-style structured log line on **every** run, healthy or not,
e.g.:

```json
{"level":40,"time":1784739600123,"rateLimitInfo":{"status":"allowed","resetsAt":1784739600,"rateLimitType":"five_hour"},"msg":"claude.rate_limit_event"}
```

`is_session_window_failure` matches on `_STRUCTURED_MARKER in text` alone — it never inspects
`status`. Every run that emits this line, `allowed` or not, satisfies the predicate. Compounding
this, `parse_structured_reset_epoch` (`session_window.py:31`) only reads a top-level `event["resetsAt"]`;
the real payload nests it under `event["rateLimitInfo"]["resetsAt"]`, so even when a run *is*
legitimately exhausted, the structured path silently fails to parse a reset time and falls through
to the 30-minute `fallback_minutes` default instead of the actual reset — the exact symptom
observed in the #332 incident (a flat +30 min pause, not a computed resume time).

The #292 design (`docs/archive/2026-07-13-scheduler-session-window-backoff-design.md:218`) assumed
the shape `{"event":"claude.rate_limit_event","resetsAt":"<ISO-8601>"}` with no `status` field at
all, and every currently-pinned fixture in this repo
(`tests/test_factory_core_session_window.py:17,45,54`, `tests/test_entrypoint_session_window.sh:63,140,287`)
uses that assumed shape — none of them exercise the real, nested, status-bearing payload the
runner actually emits, so the suite has never caught this. This is why the #332 pause looked, from
the run-record log, like a plain `RATE_LIMIT_RE` false positive at first pass, but of the incident's
captured runs, one had zero `RATE_LIMIT_RE` substring hits and paused anyway — only explicable by
the unconditional structured-marker branch.

### Cause B: bare substring matching, no word boundaries or error context

`scripts/factory_core/session_window.py:12`:

```python
RATE_LIMIT_RE = re.compile(
    r"usage limit|rate limit|429|credit balance|session limit", re.IGNORECASE
)
```

No word boundaries, no error context required. `429` matches inside a commit SHA, a dollar figure
(`$0.429`), an issue/PR reference (`#429`), or a line number. `rate limit`/`session limit` match
this repo's own prose: `scheduler.sh` logs `rate_limit remaining=… sleeping=…s until_reset`
(note: underscore, so this exact string does not actually collide with the space-delimited
`rate limit` alternative today — see Decision 6 test corrections) and `session_window_gate=active`,
and a comment in this very module says "Guard against rate limit exhaustion" (space-delimited,
and *does* collide). Any agent that quotes this regex's own source line, or the comment above it,
reproduces a string the regex is meant to detect. #292 flagged this as a known false-positive
vector.

**A second, more severe surface shares the tightened regex (not the structured-marker bug — that
bug is local to `session_window.py`'s pause gate).** `scripts/factory_core/error_signature.py`
imports `RATE_LIMIT_RE` (`from .session_window import RATE_LIMIT_RE as _RATE_LIMIT_RE`) to classify
failures as `environmental:rate_limit` for the circuit breaker (`scheduler.sh`'s early-trip logic,
#33). A false match there doesn't pause the factory, but it *suppresses* the early-trip signal —
`environmental:` signatures are the lenient bucket the breaker does not count toward
`trip_to_blocked`, so a genuinely repeating substantive failure misclassified as
`environmental:rate_limit` silently burns the full retry ceiling instead of tripping early.

**A third, byte-for-byte duplicate of the Cause-B pattern lives in `entrypoint.sh`** as a hardcoded
bash fallback, reached only when the `SESSION_WINDOW_BACKOFF_ENABLED` kill-switch is off (default:
on):

```bash
if grep -qiE "usage limit|rate limit|429|credit balance|session limit" "$TMP_OUT"; then
```

This is the exact pattern the primary path is being fixed to stop using, sitting one branch away
in the same file, reachable specifically when an operator disables the new backoff path (e.g.
because it's misbehaving) — the escape hatch would land the operator back on Cause B.

## Decision

### 1. Tighten `RATE_LIMIT_RE` with a context-required design; keep it the single shared source for the breaker

Word boundaries alone are insufficient for the `429` alternative: `\b429\b` still matches inside
`$0.429` (Python's `\b` fires at the `.`/`4` transition) and `#429`. The fix instead requires
adjacent error-context for `429`, and requires an exhaustion/throttle verb for the phrase
alternatives — verified against this repo's actual log vocabulary and every currently-pinned test
string (see Decision 6).

```python
# scripts/factory_core/session_window.py

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

# "usage"/"session"/"weekly"/"5-hour limit" — same shape, plus a pre-verb branch since real
# product phrasing puts the verb before the noun ("hit your usage limit"). "resets?"/"will
# reset" IS safe here (unlike the rate-limit branch above): no log line in this repo pairs
# "session limit"/"usage limit" with "reset" for an unrelated reason.
_RE_USAGE_SESSION = (
    r"(?:(?:usage|session|weekly|5[ _-]?hour)[ _-]?limits?\b[^.\n]{0,40}?"
    r"\b(?:reached|exceeded|exhausted|hit|resets?|will\s+reset)\b"
    r"|\b(?:hit|reached|exceeded|exhausted|used\s+up|out\s+of)\s+"
    r"(?:your\s+|the\s+|my\s+|its\s+)?(?:\w+\s+){0,2}?"
    r"(?:usage|session|weekly|5[ _-]?hour)[ _-]?limits?\b"
    r"|\blimit\b[^.\n]{0,40}?\bresets\s+\d{1,2}(?::\d{2})?\s*[ap]m\s*\()"
)

# "credit balance" needs the same tightening even though the issue gives no false-positive
# example for it: the bare regex source string is checked into the repo, so an agent quoting
# it would self-trigger. Anchored on Anthropic's actual API message.
_RE_CREDIT = (
    r"(?:credit\s+balance\b[^.\n]{0,30}?\b(?:too\s+low|insufficient|exhausted|depleted|is\s+0)\b"
    r"|\b(?:insufficient|low|zero|no)\s+credit\s+balance\b)"
)

RATE_LIMIT_RE = re.compile(
    "|".join([_RE_429, _RE_RATE_LIMIT, _RE_USAGE_SESSION, _RE_CREDIT]),
    re.IGNORECASE,
)
```

Added in this revision: a fifth `_RE_USAGE_SESSION` alternative for the reset-line shape that
names neither "session" nor "usage" — `\blimit\b[^.\n]{0,40}?\bresets\s+\d{1,2}(?::\d{2})?\s*[ap]m\s*\(`
— so `"You've hit your limit · resets 1:40pm (UTC)"` pauses; it was a gap in the original
alternative set (which required the literal word "session"/"usage"/"weekly"/"5-hour" before
"limit").

All quantifiers are bounded (no `.*`, no unbounded nesting). Verified independently in this
revision (not just asserted): the `_RE_RATE_LIMIT` alternative alone against a 63 KB adversarial
input built from the densest ambiguous token in this repo's vocabulary (`"rate limit "` repeated,
no periods, so the bounded `{0,40}` gap is exercised maximally) completes in ~11 ms; 126 KB in
~23 ms; 252 KB in ~45 ms — linear in input size, no exponential blowup, no ReDoS exposure. (A
`--rm` container's captured stdout is bounded well under 1 MB in practice.)

### 2. Fix the structured-marker path to read `status` and the nested `resetsAt` (Cause A — new in this revision)

```python
# scripts/factory_core/session_window.py

def _structured_event(text: str) -> Optional[dict]:
    """Return the parsed claude.rate_limit_event payload from `text`, or None if absent
    or unparseable. Accepts both the real Claude Code CLI shape (pino-style: a "msg" field,
    rate-limit fields nested under "rateLimitInfo") and the #292-assumed shape (a top-level
    "event" field, resetsAt at the top level) — the latter is not known to be emitted by any
    real Claude Code output, but every currently-pinned fixture uses it, and nothing here
    depends on the shapes being mutually exclusive."""
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
            return event
    return None


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

`parse_structured_reset_epoch` becomes a thin wrapper: call `_structured_event`, read
`_structured_resets_at`, and apply the existing int/float-vs-ISO-8601 handling
(`session_window.py:48-54`) to whichever value it returns — unchanged beyond the lookup path.

`is_session_window_failure` treats a structured event's `status == "allowed"` as **not** a
failure, and any other status (including `"rejected"` or absent — the #292 shape never carried
`status`, so `None != "allowed"` correctly still counts as exhaustion) as a match:

```python
def is_session_window_failure(text: str) -> bool:
    event = _structured_event(text)
    if event is not None:
        return _structured_status(event) != "allowed"
    return bool(_SESSION_EXHAUSTION_RE.search(text))  # see Decision 3
```

This is the fix for the actual #332 trigger: a `status=allowed` marker line, present in every
healthy run, now correctly falls through to "not a failure" instead of unconditionally pausing.

**Caveat flagged, not resolved, by this spec:** the exact real payload field names above
(`rateLimitInfo`, `msg`, nested `resetsAt` as an epoch int) are per the operator's review of
captured run output during this refinement round; this repo has no committed fixture or archived
raw log exhibiting that shape today (see Assumptions). Implementation must re-verify the field
names against an actual captured `claude.rate_limit_event` line before relying on them, and adjust
`_structured_status`/`_structured_resets_at` if the real shape differs from what's specified here
— the *behavior* (don't pause on `status=="allowed"`, do read a nested reset time) is the
requirement; the exact key path is best-evidence-available, not verified firsthand in this phase.

### 3. Split the pause gate onto a strictly narrower predicate than the breaker's classification

A transient HTTP-level `429`/"rate limit exceeded" is correctly `environmental:rate_limit` for the
breaker (it's not a substantive code/test failure), but it is **not** a 5h session-window
exhaustion and must not buy a 30-minute factory-wide halt — conflating the two is the same category
of over-trigger this ticket exists to fix, one level up. Add a module-private regex that is a
strict subset of `RATE_LIMIT_RE`, containing only the exhaustion-specific alternatives
(`_RE_USAGE_SESSION`, `_RE_CREDIT` — deliberately excluding `_RE_429` and `_RE_RATE_LIMIT`, since
those describe a transient throttle, not a session/window/balance exhaustion):

```python
_SESSION_EXHAUSTION_RE = re.compile(
    "|".join([_RE_USAGE_SESSION, _RE_CREDIT]),
    re.IGNORECASE,
)
```

(`is_session_window_failure`'s substring branch already references `_SESSION_EXHAUSTION_RE` —
see Decision 2's final code block.) `error_signature.py` is unchanged beyond inheriting the
tightened `RATE_LIMIT_RE` — it already imports the shared name and needs no split, since
"environmental, not worth an early breaker trip" is the correct bucket for both a transient 429
and a real exhaustion. `error_signature.py` has no equivalent of Cause A (it never looks at the
structured marker at all today), so Decision 2 does not touch it.

**Known behavior change to flag explicitly:** `"rate limit exceeded"` alone will stop pausing the
factory (it still classifies `environmental:rate_limit` for the breaker via the shared regex, per
`tests/test_factory_core_error_signature.py::test_environmental_signatures_have_no_exit_code_suffix`).
This is intentional — it is exactly the "transient throttle ≠ session exhaustion" distinction this
decision exists to draw.

### 4. Extend `match_snippet` to cover the structured path; fix the run-record detail encoding

The issue's complaint — "the evidence died with the `--rm` container" — is precise: production
dispatch is `docker compose run -d --rm` (`scheduler.sh`), so the container's stdout stream itself
is discarded once it exits; `deploy/**` is a human-only surface this ticket cannot touch. But
`scripts/factory_core/run_record.py` already appends every event to `runs.jsonl` on the persistent
`scheduler_state` volume (survives `--rm`) and already POSTs to Seq — and `_handle_session_window_pause`
already calls `run-record record --stage paused` for every pause. That is the existing channel to
extend, not a new artifact. Revised to also produce a snippet when the structured path decided the
pause (revision 1 returned `None` unconditionally whenever the marker was present, which would
have silently dropped diagnostics for every real Cause-A pause):

```python
# scripts/factory_core/session_window.py
def match_snippet(text: str, radius: int = 80) -> Optional[dict]:
    """Return the match that caused a pause, plus context, or None if the text doesn't
    represent a pause-worthy failure. Used to make a session-window pause diagnosable
    after the container that produced it is gone."""
    event = _structured_event(text)
    if event is not None:
        if _structured_status(event) == "allowed":
            return None
        offset = text.find(_STRUCTURED_MARKER)
        return {"matched": _STRUCTURED_MARKER, "offset": offset, "window": event}
    match = _SESSION_EXHAUSTION_RE.search(text)
    if match is None:
        return None
    start, end = max(0, match.start() - radius), min(len(text), match.end() + radius)
    return {"matched": match.group(0), "offset": match.start(), "window": text[start:end]}
```

- `cli.py session-window-check` includes the snippet in its stdout as its own final line,
  base64-encoded (`snippet_b64=<...>`, `json.dumps(result["window"])` first if `window` is a
  dict) — the existing bash parser (`grep -o 'matched=...'` / `grep -o 'resume_epoch=...'`) reads
  single space-separated tokens off one line; a raw snippet containing spaces/newlines/`=` would
  break that shape.
- `_handle_session_window_pause` decodes it and passes it to `run-record record` as
  `--detail matched_pattern="$MATCHED" match_offset="$OFFSET" "snippet_b64=b64:$SNIPPET_B64"` —
  each as one quoted argv element. Two corrections to revision 1's version of this, found by
  re-reading `run_record.py:116-124`: (a) `cmd_record`'s detail parser does `kv.partition("=")` on
  each argv token and only knows about that one token — a `matched_pattern` value containing
  spaces (e.g. `` `session limit reached` ``) must arrive as a single shell-quoted argv element,
  not bare words; (b) the parser tries `int()`/`float()` coercion on any all-digit or numeric-
  looking value before falling back to string — a base64 string is occasionally all-digits by
  chance, so prefix it `b64:` to force the string branch deterministically rather than relying on
  `isdigit()`/`float()` failing.
- One line to stderr alongside the existing `echo "session-window exhausted — dispatch paused
  until ..."`, for the attached local-debug invocation. Explicitly **not** the system of record —
  under production `-d --rm` dispatch, stderr is discarded same as stdout.
- The GitHub issue pause comment (`DF_SESSION_WINDOW_PAUSE_MARKER`) gets a **classification
  summary line** (matched literal + which branch fired + byte offset — e.g.
  `` matched `session limit reached` (usage/session-limit branch) at offset 4021 `` for the
  substring path, or `` matched claude.rate_limit_event (status=rejected) `` for the structured
  path), not the raw window. There is no general text-redaction utility in this codebase
  (`model_proxy.py`'s `redact_headers()` is HTTP-header-specific only) — piping arbitrary
  transcript bytes into a public GitHub comment inside a safety-gate ticket would be a new,
  unreviewed exfiltration surface, and raw text containing backticks/`<!--`/newlines can corrupt
  the marker-comment upsert `post_or_update_comment` relies on. The full window remains available
  in `runs.jsonl`/Seq for anyone diagnosing the false positive.

### 5. Eliminate the `entrypoint.sh` duplicate instead of hand-syncing two regexes

Add a classify-only CLI subcommand that reuses the canonical Python regex and writes no sentinel:

```bash
# cli.py: new subcommand, e.g. "rate-limit-match --tmp-out <path>"
# prints "matched=true"/"matched=false"; never touches SCHEDULER_STATE_DIR
```

`entrypoint.sh`'s kill-switch-off fallback branch:

```bash
if python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" rate-limit-match \
    --tmp-out "$TMP_OUT" | grep -q '^matched=true$'; then
```

replacing the hardcoded `grep -qiE "usage limit|rate limit|429|credit balance|session limit"
"$TMP_OUT"`. Note this classify-only path uses `RATE_LIMIT_RE` (the breaker-shared, less-narrow
regex), matching the kill-switch-off fallback's pre-existing behavior of pausing on any
rate-limit-shaped text — it is not in scope to also give the legacy fallback the Decision 2/3
structured-marker and narrow-pause-gate treatment, since that path only runs at all when an
operator has explicitly disabled the new backoff mechanism and reverted to the old coarse
behavior on purpose. Scope fence: **only** the match-detection line changes. The reset-time
parsing (`RESET_TIME`/`RESET_TZ` via `grep -ioP`), the `SLEEP_SECS` math, the 90000s failsafe cap,
and the kill-switch semantics are byte-identical before and after.

### 6. Regression tests

`tests/test_factory_core_session_window.py`:
- **New, Decision 2 (Cause A) — the tests that would have caught the actual incident:**
  - A verbatim real-shape `status=allowed` line (`{"level":40,...,"rateLimitInfo":{"status":"allowed","resetsAt":1784739600,"rateLimitType":"five_hour"},"msg":"claude.rate_limit_event"}`)
    must make `is_session_window_failure` return `False` and `compute_resume_epoch` return `None` —
    this is the literal #332 trigger.
  - A real-shape `status=rejected` line with the same nested structure but a `resetsAt` in the
    near future must make `is_session_window_failure` return `True`, and `compute_resume_epoch`
    must equal `resetsAt + buffer_minutes*60` (not the fallback) — proving the nested-path parse,
    not just the boolean.
  - The existing `#292`-shape fixtures (`test_is_session_window_failure_detects_structured_signal`,
    `test_parse_structured_reset_epoch_parses_resetsAt`, `..._real_claude_rate_limit_event_payload`,
    `..._handles_epoch_int_resetsat`, `test_compute_resume_epoch_prefers_structured_over_fallback`,
    `..._clamps_structured_far_future_to_max_window`) keep passing unmodified — that shape has no
    `status` field, so `_structured_status` returns `None`, which is `!= "allowed"`, so it
    continues to classify as exhaustion. Rename
    `test_parse_structured_reset_epoch_real_claude_rate_limit_event_payload` to
    `..._assumed_292_payload_shape` — it was never actually the real payload; keep it as the
    second accepted shape, not delete it.
- **Decision 1 (Cause B) negatives/positives**, updated per operator review of which strings
  actually collide with the *current* (pre-fix) regex on `main` today (two of the original
  spec's proposed fixtures did not, and would have been no-op tests):
  - `test_check_and_pause_writes_sentinel_and_returns_epoch` currently uses `"429 rate limit hit"`
    — under the new split this no longer contains exhaustion-specific phrasing, so it must
    **stop** triggering a pause. Re-fixture it to `"429 too many requests, session limit reached"`
    (already pinned elsewhere in this file) so the test keeps proving the sentinel-write plumbing,
    and add the old string (`"429 rate limit hit"`) as a new **negative** case for
    `is_session_window_failure` — the clearest regression lock on the two-tier split.
  - Add negatives that actually match on `main` today and must stop matching: the in-module
    comment text `"# Guard against rate limit exhaustion"`, and a literal quote of the old regex
    source (`'r"usage limit|rate limit|429|credit balance|session limit"'`) — both collide with
    the un-tightened regex's `rate limit` alternative today.
  - Add negatives that never actually collided with the pre-fix regex (kept as regression locks
    against future loosening, but not framed as "fixed false positives" since they weren't false
    positives against `main`): SHA-embedded `429` (`"fixed in abc4291f2e"`), a dollar figure
    (`"cost $0.429 total this run"`), an issue reference (`"see #429 for context"`), and
    `"rate_limit remaining=4000 sleeping=30s until_reset"` / `"session_window_gate=active
    resume_at=…"` (both use an underscore, not a space, so they never matched the `rate limit`/
    `session limit` alternatives on `main` — included here purely as forward regression locks).
  - Add a positive for a transient-but-not-exhaustion string (`"HTTP 429 — rate limit exceeded"`)
    asserting `is_session_window_failure` is **False** (transient ≠ session exhaustion) while a
    separate `classify()` test (below) asserts the same string IS `environmental:rate_limit`.
  - Add a positive for the new reset-line-without-session/usage shape:
    `"You've hit your limit · resets 1:40pm (UTC)"`.
  - Add a `match_snippet` unit test for the substring path (matched text, offset, bounded window)
    and one for the structured path (`matched == "claude.rate_limit_event"`, `window` is the
    parsed dict, `None` when `status == "allowed"`).

`tests/test_factory_core_error_signature.py`:
- Existing `test_rate_limit`, `test_rate_limit_session_limit_string`, and
  `test_environmental_signatures_have_no_exit_code_suffix` must continue to pass unmodified — all
  three verified against the tightened regex above.
- Add the same SHA/dollar-figure/issue-number negatives as above, asserting `classify()` does
  **not** return `environmental:rate_limit`.
- Add `"HTTP 429 — rate limit exceeded"` asserting `environmental:rate_limit` (the
  transient-429-still-classifies-for-the-breaker case from Decision 3).

`tests/test_entrypoint_session_window.sh`:
- Case A (`--- A: matched (structured rate_limit_event line) ---`) currently uses the #292-assumed
  shape (`{"event":"claude.rate_limit_event","resetsAt":"..."}`). Update it to the real pino shape
  with a `rejected`-equivalent (non-`allowed`) status and a nested epoch `resetsAt`, so this
  end-to-end shell test exercises Decision 2 the way `entrypoint.sh` actually receives it in
  production, not the shape no real output has been observed to emit.
- Add a new case: a `status=allowed` line only (no other exhaustion signal) → `_handle_session_window_pause`
  returns 1 (falls through, does not pause) and writes no sentinel — the direct shell-level
  regression lock for the #332 incident.

New small test exercising the `rate-limit-match` CLI subcommand end-to-end (mirroring the existing
`test_cli_session_window_check_matched`/`_unmatched` pattern) — this is what makes the
`entrypoint.sh` fallback path actually testable, since the file's own header comment notes the
main retry loop itself is un-executable by this harness and verified by code review instead; the
CLI subcommand extraction moves the *logic* under test coverage even though the shell call site
that invokes it is not.

## Alternatives considered

1. **Ship only the Decision 1/3/4/5 (Cause B / regex-tightening) work from revision 1, treat the
   structured-marker bug as a separate follow-up ticket.** Rejected: Cause A is the *actual*
   trigger of the #332 incident per the operator's review of captured run output (one of the four
   incident runs had zero `RATE_LIMIT_RE` substring hits and paused anyway) — shipping revision 1
   alone would close the ticket without fixing the bug it was filed to fix, and the next healthy
   `status=allowed` run would reproduce it immediately.
2. **Keep one regex for both `session_window.py` and `error_signature.py`, no narrower pause-gate
   predicate.** Rejected: a transient HTTP 429 correctly belongs in the breaker's lenient
   `environmental:` bucket but must not halt all factory dispatch for 30 minutes — that conflation
   is the same over-trigger class this ticket fixes, one layer up.
3. **Lookaround denylist for `429`** (exclude `#429`, `.429`, `:429` via negative lookbehind only,
   no required context words). Rejected: it loses on the dominant false-positive shape in this
   repo's actual output — bare `429` preceded by whitespace (`issue 429`, `line 429`, `PR 429`,
   `took 429 seconds`) is indistinguishable from a real HTTP status under a pure lookaround
   denylist. A context-required allowlist trades a vanishingly small false-negative risk for a
   decisive false-positive kill.
4. **Fix only `session_window.py`, leave `error_signature.py` and `entrypoint.sh`'s duplicate
   untouched.** Rejected: `error_signature.py`'s copy is the exact same Cause-B defect on a
   different, arguably more consequential surface (it silently suppresses early breaker trips);
   `entrypoint.sh`'s duplicate is the escape-hatch path for the primary fix.
5. **Hand-sync the `entrypoint.sh` bash regex to the new Python pattern instead of extracting a
   CLI subcommand.** Rejected: two independently-maintained copies of a safety-classifier regex is
   how this exact duplication happened in the first place; a `grep -qiE` cannot express the
   lookaround/context logic in Decision 1 anyway.
6. **Route the ±80-char snippet (or the full structured event) directly into the GitHub pause
   comment.** Rejected (Decision 4): no text-redaction utility exists in this codebase, and an
   arbitrary raw window can contain markdown/HTML that corrupts the marker-comment upsert.
7. **Treat a missing/unknown `status` on a structured event as "not exhausted" (only pause on an
   explicit non-`allowed` value the code recognizes).** Rejected: this would silently stop pausing
   on the #292-assumed shape (which never carries `status` at all) and on any future status value
   this code doesn't yet know about — the asymmetric cost (a missed real exhaustion burns the
   retry ceiling on repeated real 5h-window failures; an extra pause costs 30 minutes) favors
   `!= "allowed"` (fail toward pausing) over an allowlist of known-bad values (fail toward not
   pausing).

## Known limitations

- The residual false-negative risk from the context-required `429` design (a genuine rate-limit
  payload that prints a bare `429` with no adjacent `http`/`status`/`code`/`error`/`api` token) is
  accepted per Alternative 3's reasoning; no such shape has been observed in this repo's own
  incident history or in Claude Code's structured/HTTP output.
- `credit balance` remains in `_SESSION_EXHAUSTION_RE`, even though credit exhaustion is a billing
  state, not a time-windowed one — pausing for `fallback_minutes` and retrying on a condition no
  timer heals is pre-existing behavior, unchanged by this ticket.
- This ticket does not touch `scheduler.sh`'s `stage_orphan_sweep`-runs-before-the-sentinel-gate
  ordering bug (#334) referenced in the issue's knock-on-effects — that is a separate,
  already-identified ticket and out of scope here.
- The `entrypoint.sh` kill-switch-off fallback (Decision 5) keeps using the breaker-shared
  `RATE_LIMIT_RE`, not the narrower Decision 3 pause-only predicate or the Decision 2
  structured-marker fix — it is an explicitly-opted-into legacy path, not the default, and giving
  it feature parity with the new backoff mechanism is a larger change than this ticket's scope
  (the ticket's stated purpose for touching it at all is closing the "escape hatch reopens Cause B"
  gap, not upgrading it to Cause-A awareness).

## Accepted trade-offs

- The `429` alternative requires adjacent context rather than pure lookarounds — a deliberate
  precision-over-recall choice given the asymmetric blast radius (a false positive halts all
  dispatch for 30 minutes; a false negative on the shared regex merely downgrades a breaker
  classification, which is retryable and self-correcting).
- `"rate limit exceeded"` and other transient-throttle phrasing will no longer trigger the
  30-minute pause (Decision 3) — a deliberate, named behavior change, not an oversight.
- An unrecognized/absent structured `status` value pauses (fail toward pausing, Alternative 7) —
  a deliberate precision-over-recall choice in the opposite direction from the 429 case, because
  here the failure mode of *not* pausing on a real exhaustion is worse than an extra 30-minute
  pause.

## Assumptions

- The real `claude.rate_limit_event` payload shape described in Decision 2 (`rateLimitInfo`
  object, `status`/`resetsAt` nested under it, `msg` instead of a top-level `event` key) is based
  on the operator's review of captured run output during this refinement round; no raw incident
  log or existing fixture in this repo currently exhibits that shape. Implementation must confirm
  field names against real captured output before merging Decision 2's parsing logic, and treat
  the exact JSON keys here as best-available-evidence, not verified firsthand.
- No caller of `RATE_LIMIT_RE`, `is_session_window_failure`, or `classify()` depends on matching
  any string beyond what's explicitly pinned in the existing test suites; all current callers
  (`cli.py`, `entrypoint.sh`, `scheduler.sh`) consume the boolean/enum result opaquely.
- There is no separate "TARGET-PATH scaffold copy" of `session_window.py`/`error_signature.py`
  that needs independent syncing: `$CLONE_DIR/dark-factory` (what `entrypoint.sh` resolves
  `cli.py` under, e.g. `entrypoint.sh:14,556`) *is* the clone this ticket edits, produced by
  `entrypoint.sh`'s own vendoring step (copying `/opt/dark-factory/scripts` in) — not a second
  independently-maintained tree. (Revision 1 incorrectly assumed a syncing mechanism existed here;
  corrected per operator review.)

## Open questions (non-blocking)

- Should the follow-up credit-balance ticket (Known limitations) also change how the breaker
  treats a `environmental:rate_limit` signature specifically caused by `credit balance` — e.g.
  escalating to a human-notification path after N consecutive occurrences, since no scheduler
  timer will ever clear it? Worth deciding when that ticket is scoped, not here.
- Is there a real Claude Code output shape that prints a bare `429` (no adjacent context word)
  during an actual rate-limit event? If a future incident surfaces one, it should become a named
  fifth alternative in `_RE_429` rather than a reason to loosen the existing context requirement.
- #341 will touch `_handle_session_window_pause` too — this ticket should be sequenced ahead of
  it. #334/#335 share no files with this ticket.
