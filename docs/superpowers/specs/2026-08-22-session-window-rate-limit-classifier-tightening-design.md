# Session-window/breaker rate-limit classifier: tighten `RATE_LIMIT_RE`, split the pause gate, log the match

**Status:** design
**Date:** 2026-08-22
**Issue:** #344
**Related:** #292 (predicted this false-positive class), #334 (orphan-sweep-before-sentinel-gate
ordering bug — separate ticket, not touched here), #341 (retry burned by this incident), #35/#305
(shipped/hardened the backoff mechanism this classifier feeds)

## Problem

On 2026-08-21 21:14–21:15Z, a #332 implement run's captured stdout contained no structured
`claude.rate_limit_event` marker and no human-readable `resets HH:MMam (TZ)` text — only a bare
substring match against `RATE_LIMIT_RE`. `entrypoint.sh` classified this as a Claude Max 5h
session-window exhaustion and paused **all** factory dispatch for 30 minutes
(`session_window_fallback_minutes`, `config/config.yaml`). The account's 5h window was actually
at 39% utilization at that moment, and the very next run reported `status=allowed` for the same
window. The pause was a false positive, and it was expensive: combined with #334 (orphan sweep
running before the sentinel gate, a separate bug), it moved #332 to Blocked, burned a retry
(#341), and caused the retry to re-run the entire implement plan from Task 1.

Root cause, `scripts/factory_core/session_window.py:12`:

```python
RATE_LIMIT_RE = re.compile(
    r"usage limit|rate limit|429|credit balance|session limit", re.IGNORECASE
)
```

No word boundaries, no error context required. `429` matches inside a commit SHA, a dollar
figure (`$0.429`), an issue/PR reference (`#429`), or a line number. `rate limit`/`session limit`
match this repo's own prose: `scheduler.sh` logs `rate_limit remaining=… sleeping=…s
until_reset`, `session_window_gate=active`, and its comments literally say "Guard against rate
limit exhaustion." Any agent that greps its own logs, reads an archived design doc, or quotes this
regex's own source line reproduces the exact strings the regex is meant to detect. #292 flagged
this as a known false-positive vector; #344 is the first confirmed live occurrence.

**A second, more severe surface shares the same regex.** `scripts/factory_core/error_signature.py`
imports `RATE_LIMIT_RE` (`from .session_window import RATE_LIMIT_RE as _RATE_LIMIT_RE`) to
classify failures as `environmental:rate_limit` for the circuit breaker (`scripts/scheduler.sh`'s
early-trip logic, #33). A false match there doesn't pause the factory, but it *suppresses* the
early-trip signal — `environmental:` signatures are the lenient bucket the breaker does not
count toward `trip_to_blocked`, so a genuinely repeating substantive failure misclassified as
`environmental:rate_limit` silently burns the full retry ceiling instead of tripping early. Fixing
one surface and not the other leaves a known-duplicate of this exact bug live in the codebase.

**A third, byte-for-byte duplicate lives in `entrypoint.sh`** as a hardcoded bash fallback,
reached only when the `SESSION_WINDOW_BACKOFF_ENABLED` kill-switch is off (default: on):

```bash
if grep -qiE "usage limit|rate limit|429|credit balance|session limit" "$TMP_OUT"; then
```

This is the exact string the primary path is being fixed to stop using, sitting one branch away
in the same file, reachable specifically when an operator disables the new backoff path (e.g.
because it's misbehaving) — the escape hatch would land the operator back on the bug being
escaped.

## Decision

### 1. Tighten `RATE_LIMIT_RE` with a context-required design; keep it the single shared source

Word boundaries alone are insufficient for the `429` alternative: `\b429\b` still matches inside
`$0.429` (Python's `\b` fires at the `.`/`4` transition) and `#429`. The fix instead requires
adjacent error-context for `429`, and requires an exhaustion/throttle verb for the phrase
alternatives — verified against this repo's actual log vocabulary and every currently-pinned test
string (see Verification below).

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
# gap, not `.*`, so it can't bridge across log lines or sentences). "exhaust\w*" is
# deliberately NOT used (only "exhausted") because scheduler.sh's own comment says
# "Guard against rate limit exhaustion".
_RE_RATE_LIMIT = (
    r"(?:rate[ _-]?limit(?:s|ed|ing)?\b[^.\n]{0,40}?"
    r"\b(?:exceeded|reached|hit|exhausted|throttl\w*|too\s+many\s+requests)\b"
    r"|\b(?:hit|exceeded|reached)\s+(?:the\s+|a\s+|your\s+|our\s+)?rate[ _-]?limit(?:s|ed)?\b"
    r"|\bbeing\s+rate[ _-]?limited\b)"
)

# "usage"/"session"/"weekly"/"5-hour limit" — same shape, plus a pre-verb branch since
# real product phrasing puts the verb before the noun ("hit your usage limit").
# "resets?"/"will reset" IS safe here (unlike the rate-limit branch above): no
# scheduler.sh log line pairs "session limit"/"usage limit" with "reset".
_RE_USAGE_SESSION = (
    r"(?:(?:usage|session|weekly|5[ _-]?hour)[ _-]?limits?\b[^.\n]{0,40}?"
    r"\b(?:reached|exceeded|exhausted|hit|resets?|will\s+reset)\b"
    r"|\b(?:hit|reached|exceeded|exhausted|used\s+up|out\s+of)\s+"
    r"(?:your\s+|the\s+|my\s+|its\s+)?(?:\w+\s+){0,2}?"
    r"(?:usage|session|weekly|5[ _-]?hour)[ _-]?limits?\b)"
)

# "credit balance" needs the same tightening even though the issue gives no false-positive
# example for it: the bare regex source string (this literal) is checked into the repo in
# multiple docs/comments, so an agent quoting it would self-trigger. Anchored on Anthropic's
# actual API message ("Your credit balance is too low to access the Anthropic API").
_RE_CREDIT = (
    r"(?:credit\s+balance\b[^.\n]{0,30}?\b(?:too\s+low|insufficient|exhausted|depleted|is\s+0)\b"
    r"|\b(?:insufficient|low|zero|no)\s+credit\s+balance\b)"
)

RATE_LIMIT_RE = re.compile(
    "|".join([_RE_429, _RE_RATE_LIMIT, _RE_USAGE_SESSION, _RE_CREDIT]),
    re.IGNORECASE,
)
```

All quantifiers are bounded (no `.*`, no unbounded nesting) — a 50KB adversarial input completes
in single-digit milliseconds; no ReDoS exposure.

### 2. Split the pause gate onto a strictly narrower predicate

A transient HTTP-level `429`/"rate limit exceeded" is correctly `environmental:rate_limit` for
the breaker (it's not a substantive code/test failure), but it is **not** a 5h session-window
exhaustion and must not buy a 30-minute factory-wide halt — conflating the two is the same
category of over-trigger this ticket exists to fix, one level up. Add a module-private regex that
is a strict subset of `RATE_LIMIT_RE`, containing only the exhaustion-specific alternatives
(`_RE_USAGE_SESSION`, `_RE_CREDIT` — deliberately excluding `_RE_429` and `_RE_RATE_LIMIT`, since
those describe a transient throttle, not a session/window/balance exhaustion):

```python
_SESSION_EXHAUSTION_RE = re.compile(
    "|".join([_RE_USAGE_SESSION, _RE_CREDIT]),
    re.IGNORECASE,
)

def is_session_window_failure(text: str) -> bool:
    return _STRUCTURED_MARKER in text or bool(_SESSION_EXHAUSTION_RE.search(text))
```

`error_signature.py` is unchanged beyond inheriting the tightened `RATE_LIMIT_RE` — it already
imports the shared name and needs no split, since "environmental, not worth an early breaker
trip" is the correct bucket for both a transient 429 and a real exhaustion.

**Known behavior change to flag explicitly:** `"rate limit exceeded"` alone will stop pausing the
factory (it still classifies `environmental:rate_limit` for the breaker via the shared regex, per
`tests/test_factory_core_error_signature.py::test_environmental_signatures_have_no_exit_code_suffix`).
This is intentional — it is exactly the "transient throttle ≠ session exhaustion" distinction this
decision exists to draw.

### 3. Add a `match_snippet` helper; log the matched text through the existing `run-record` channel

The issue's complaint — "the evidence died with the `--rm` container" — is precise: production
dispatch is `docker compose run -d --rm` (`scheduler.sh`), so the container's stdout stream itself
is discarded once it exits; `deploy/docker-compose.yml`'s gelf log driver is commented out, and
`deploy/**` is a human-only surface this ticket cannot touch. But `scripts/factory_core/run_record.py`
already appends every event to `runs.jsonl` on the persistent `scheduler_state` volume (survives
`--rm`) and already POSTs to Seq — and `_handle_session_window_pause` already calls
`run-record record --stage paused` for every pause. That is the existing channel to extend, not a
new artifact:

```python
# scripts/factory_core/session_window.py
def match_snippet(text: str, radius: int = 80) -> Optional[dict]:
    """Return the exhaustion match plus surrounding context, or None. Used to make a
    session-window pause diagnosable after the container that produced it is gone."""
    match = _SESSION_EXHAUSTION_RE.search(text) if _STRUCTURED_MARKER not in text else None
    if match is None:
        return None
    start, end = max(0, match.start() - radius), min(len(text), match.end() + radius)
    return {"matched": match.group(0), "offset": match.start(), "window": text[start:end]}
```

- `cli.py session-window-check` includes the snippet in its stdout as its own final line,
  base64-encoded (`snippet_b64=<...>`) — the existing bash parser (`grep -o 'matched=...'` /
  `grep -o 'resume_epoch=...'`) reads single space-separated tokens off one line; a raw snippet
  containing spaces/newlines/`=` would break that shape.
- `_handle_session_window_pause` decodes it and passes it to `run-record record` as
  `--detail matched_pattern=<matched literal> match_offset=<int> snippet_b64=<...>` (the CLI's
  existing free-form `--detail KEY=VAL` support, already used elsewhere in `run_record.py`) — no
  new artifact format, no new retention/rotation policy.
- One line to stderr alongside the existing `echo "session-window exhausted — dispatch paused
  until ..."`, for the attached local-debug invocation. Explicitly **not** the system of record —
  under production `-d --rm` dispatch, stderr is discarded same as stdout.
- The GitHub issue pause comment (`DF_SESSION_WINDOW_PAUSE_MARKER`) gets a **classification
  summary line** (matched literal + which alternative fired + byte offset — e.g.
  `` matched `session limit reached` (usage/session-limit branch) at offset 4021 ``), not the raw
  ±80-char window. There is no general text-redaction utility in this codebase (`model_proxy.py`'s
  `redact_headers()` is HTTP-header-specific only) — piping arbitrary transcript bytes into a
  public GitHub comment inside a safety-gate ticket would be a new, unreviewed exfiltration
  surface, and raw text containing backticks/`<!--`/newlines can corrupt the marker-comment
  upsert `post_or_update_comment` relies on. The full window remains available in `runs.jsonl`/Seq
  for anyone diagnosing the false positive.

### 4. Eliminate the `entrypoint.sh` duplicate instead of hand-syncing two regexes

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
"$TMP_OUT"`. Scope fence: **only** the match-detection line changes. The reset-time parsing
(`RESET_TIME`/`RESET_TZ` via `grep -ioP`), the `SLEEP_SECS` math, the 90000s failsafe cap, and the
kill-switch semantics are byte-identical before and after — this ticket does not touch the
sleep-forever fallback's *behavior*, only which pattern decides whether it engages. This closes
the "escape hatch reopens the bug" gap identified in the Problem section without expanding scope
into the fallback's actual sleep/retry logic (a distinct concern, untouched).

### 5. Regression tests

`tests/test_factory_core_session_window.py`:
- Existing `test_check_and_pause_writes_sentinel_and_returns_epoch` uses `"429 rate limit hit"` —
  under the new split this string no longer contains exhaustion-specific phrasing, so it must
  **stop** triggering a pause. Re-fixture it to `"429 too many requests, session limit reached"`
  (already pinned elsewhere in this file) so the test keeps proving the sentinel-write plumbing,
  and add the old string (`"429 rate limit hit"`) as a new **negative** case for
  `is_session_window_failure` — the clearest regression lock on the two-tier split.
- Add negatives: SHA-embedded `429` (e.g. `"fixed in abc4291f2e"`), a dollar figure (`"cost
  $0.429 total this run"`), an issue reference (`"see #429 for context"`), and this repo's own log
  prose (`"rate_limit remaining=4000 sleeping=30s until_reset"`, `"session_window_gate=active
  resume_at=…"`) — none may trigger `is_session_window_failure` or `compute_resume_epoch`.
- Add a positive for a transient-but-not-exhaustion string (`"HTTP 429 — rate limit exceeded"`)
  asserting `is_session_window_failure` is **False** (transient ≠ session exhaustion) while a
  separate `classify()` test (below) asserts the same string IS `environmental:rate_limit`.
- Add a `match_snippet` unit test: matched text, offset, and a bounded window around a known
  exhaustion string.

`tests/test_factory_core_error_signature.py`:
- Existing `test_rate_limit` (`"Error: you have hit your usage limit for this session"`) and
  `test_rate_limit_session_limit_string` (`"Claude session limit reached — resets 9:20pm (UTC)"`)
  and `test_environmental_signatures_have_no_exit_code_suffix` (`"rate limit exceeded"`) must
  continue to pass unmodified — all three are verified against the tightened regex above.
- Add the same SHA/dollar-figure/issue-number/scheduler-log negatives as above, asserting
  `classify()` returns `substantive:unknown:<code>` (or another non-`environmental:rate_limit`
  bucket per whatever else the text contains), not `environmental:rate_limit`.
- Add `"HTTP 429 — rate limit exceeded"` asserting `environmental:rate_limit` (the
  transient-429-still-classifies-for-the-breaker case from Decision 2).

`tests/test_entrypoint_session_window.sh` and a new small test exercising the `rate-limit-match`
CLI subcommand end-to-end (mirroring the existing `test_cli_session_window_check_matched`/
`_unmatched` pattern in `test_factory_core_session_window.py`) — this is what makes the
`entrypoint.sh` fallback path actually testable, since the file's own header comment notes the
main retry loop itself is un-executable by this harness and verified by code review instead; the
CLI subcommand extraction moves the *logic* under test coverage even though the shell call site
that invokes it is not.

## Alternatives considered

1. **Keep one regex for both `session_window.py` and `error_signature.py`, no narrower pause-gate
   predicate.** Rejected: a transient HTTP 429 correctly belongs in the breaker's lenient
   `environmental:` bucket but must not halt all factory dispatch for 30 minutes — that conflation
   is the same over-trigger class this ticket fixes, one layer up. The two-tier split costs one
   extra module-private regex and is a strict subset of work already being done.
2. **Lookaround denylist for `429`** (exclude `#429`, `.429`, `:429` via negative lookbehind only,
   no required context words). Rejected: it loses on the dominant false-positive shape in this
   repo's actual output — bare `429` preceded by whitespace (`issue 429`, `line 429`, `PR 429`,
   `took 429 seconds`) is indistinguishable from a real HTTP status under a pure lookaround
   denylist, and this repo's logs are literally full of numbered issues/PRs/lines. A
   context-required allowlist trades a vanishingly small false-negative risk (a rate-limit payload
   that prints bare `429` with zero adjacent context — not a shape `claude.rate_limit_event` or
   the SDK's actual HTTP surface produces) for a decisive false-positive kill.
3. **Fix only `session_window.py`, leave `error_signature.py` and `entrypoint.sh`'s duplicate
   untouched** (narrowest possible diff, matching the issue title's literal file reference).
   Rejected: `error_signature.py`'s copy is the exact same defect on a different, arguably more
   consequential surface (it silently suppresses early breaker trips instead of visibly pausing);
   leaving it live means the ticket knowingly ships half a fix for a defect it names precisely.
   `entrypoint.sh`'s duplicate is the escape-hatch path for the primary fix — shipping a tightened
   primary path while leaving the kill-switch fallback on the old pattern means disabling the new
   backoff (the operator's documented recovery action when it misbehaves) lands back on this
   exact bug.
4. **Hand-sync the `entrypoint.sh` bash regex to the new Python pattern instead of extracting a
   CLI subcommand.** Rejected: two independently-maintained copies of a safety-classifier regex
   is how this exact duplication happened in the first place; a `grep -qiE` cannot express the
   lookaround/context logic in Decision 1 anyway (POSIX/PCRE-via-`-P` bash regex support for
   variable-width negative lookbehind is unreliable across grep versions), so a literal string sync
   isn't even mechanically faithful. Extracting a thin classify-only CLI call is a few added lines
   and gives the fallback path actual test coverage it doesn't have today.
5. **Route the ±80-char snippet directly into the GitHub pause comment.** Rejected (Decision 3):
   no text-redaction utility exists in this codebase, and an arbitrary raw window can contain
   markdown/HTML that corrupts the marker-comment upsert. A classification summary (matched
   literal + branch + offset) gives a human enough to triage without introducing a new exposure
   surface in a safety-gate ticket.

## Known limitations

- The residual false-negative risk from the context-required `429` design (a genuine rate-limit
  payload that prints a bare `429` with no adjacent `http`/`status`/`code`/`error`/`api` token) is
  accepted per Alternative 2's reasoning; no such shape has been observed in this repo's own
  incident history or in Claude Code's structured/HTTP output. If one is ever observed, add it as
  a fifth alternative rather than loosening the existing ones.
- `credit balance` remains in `_SESSION_EXHAUSTION_RE` (per the existing shared behavior this
  ticket preserves), even though credit exhaustion is a billing state, not a time-windowed one —
  pausing for `fallback_minutes` and retrying on a condition no timer heals is pre-existing
  behavior, unchanged by this ticket. A follow-up ticket could special-case it (e.g. escalate to a
  human-notification path instead of a timed pause) but that is a distinct, larger decision.
- This ticket does not touch `scheduler.sh`'s `stage_orphan_sweep`-runs-before-the-sentinel-gate
  ordering bug (#334) referenced in the issue's knock-on-effects — that is explicitly a separate,
  already-identified ticket (see `.archon/memory/codebase-patterns.md`'s #292 entry) and is out of
  scope here.

## Accepted trade-offs

- The `429` alternative requires adjacent context (Decision 1/Alternative 2) rather than pure
  lookarounds — this is a deliberate precision-over-recall choice given the asymmetric blast
  radius (a false positive halts all dispatch for 30 minutes; a false negative on the shared
  regex merely downgrades a breaker classification from `environmental:rate_limit` to
  `substantive:unknown:<code>`, which is retryable and self-correcting).
- `"rate limit exceeded"` and other transient-throttle phrasing will no longer trigger the
  30-minute pause (Decision 2) — a deliberate, named behavior change, not an oversight.

## Assumptions

- `dark-factory/scripts/factory_core/session_window.py` and `error_signature.py` (the TARGET-PATH
  self-target scaffold copies `entrypoint.sh` resolves under `$CLONE_DIR/dark-factory/`) are kept
  in sync with the canonical `scripts/factory_core/` sources by the existing build/copy mechanism
  referenced in prior session-window tickets (#35, #305) — this ticket edits the canonical source
  only.
- No caller of `RATE_LIMIT_RE`, `is_session_window_failure`, or `classify()` depends on matching
  any string beyond what's explicitly pinned in the existing test suites; all current callers
  (`cli.py`, `entrypoint.sh`, `scheduler.sh`) consume the boolean/enum result opaquely.

## Open questions (non-blocking)

- Should the follow-up credit-balance ticket (Known limitations) also change how the breaker
  treats a `environmental:rate_limit` signature specifically caused by `credit balance` — e.g.
  escalating to a human-notification path after N consecutive occurrences, since no scheduler
  timer will ever clear it? Worth deciding when that ticket is scoped, not here.
- Is there a real Claude Code output shape that prints a bare `429` (no adjacent context word)
  during an actual rate-limit event? If a future incident surfaces one, it should become a named
  fifth alternative in `_RE_429` rather than a reason to loosen the existing context requirement.
