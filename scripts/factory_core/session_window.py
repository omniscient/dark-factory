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

_SUBSTRING_RE = re.compile(
    r"usage limit|rate limit|429|credit balance|session limit", re.IGNORECASE
)
_STRUCTURED_MARKER = "claude.rate_limit_event"
_HUMAN_RESET_RE = re.compile(
    r"resets\s+([0-9]{1,2}:[0-9]{2}[ap]m)\s*\(([^)]+)\)", re.IGNORECASE
)


def is_session_window_failure(text: str) -> bool:
    return _STRUCTURED_MARKER in text or bool(_SUBSTRING_RE.search(text))


def parse_structured_reset_epoch(text: str) -> Optional[int]:
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
        resets_at = event.get("resetsAt")
        if not resets_at:
            continue
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
    if candidate.timestamp() < now_epoch:
        candidate += timedelta(days=1)
    return int(candidate.timestamp())


def compute_resume_epoch(
    text: str, now_epoch: int, buffer_minutes: int, fallback_minutes: int
) -> Optional[int]:
    if not is_session_window_failure(text):
        return None
    structured = parse_structured_reset_epoch(text)
    if structured is not None:
        return structured + buffer_minutes * 60
    fallback = parse_fallback_reset_epoch(text, now_epoch)
    if fallback is not None:
        return fallback + buffer_minutes * 60
    return now_epoch + fallback_minutes * 60


def write_pause_sentinel(resume_epoch: int, state_dir: Path) -> None:
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "session-window-paused"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(str(resume_epoch))
    tmp.rename(path)


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
