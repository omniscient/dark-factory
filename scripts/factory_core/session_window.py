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
_STRUCTURED_MARKER = "claude.rate_limit_event"


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


_HUMAN_RESET_RE = re.compile(
    r"resets\s+([0-9]{1,2}:[0-9]{2}[ap]m)\s*\(([^)]+)\)", re.IGNORECASE
)
# Physical invariant: a Claude Max session window is fixed at 5h, so no true resume can
# ever be more than 5h out. Hardcoded, not a config.yaml key -- see #305.
MAX_SESSION_WINDOW_HOURS = 5


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
    if (
        candidate.timestamp() < now_epoch
        and candidate.timestamp() + 86400 <= now_epoch + MAX_SESSION_WINDOW_HOURS * 3600
    ):
        candidate += timedelta(days=1)
    return int(candidate.timestamp())


def compute_resume_epoch(
    text: str, now_epoch: int, buffer_minutes: int, fallback_minutes: int
) -> Optional[int]:
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


def write_pause_sentinel(resume_epoch: int, state_dir: Path) -> None:
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "session-window-paused"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(str(resume_epoch))
    tmp.rename(path)


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
