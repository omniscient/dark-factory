import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core.session_window import (
    is_session_window_failure,
    parse_structured_reset_epoch,
    parse_fallback_reset_epoch,
    compute_resume_epoch,
    write_pause_sentinel,
    check_and_pause,
)


def test_is_session_window_failure_detects_structured_signal():
    text = '{"event":"claude.rate_limit_event","resetsAt":"2026-07-13T23:10:00Z"}'
    assert is_session_window_failure(text) is True


def test_is_session_window_failure_detects_substring_fallback():
    assert is_session_window_failure("Error: you've hit your USAGE LIMIT") is True


def test_is_session_window_failure_false_when_no_signal():
    assert is_session_window_failure("unrelated stack trace") is False


def test_parse_structured_reset_epoch_parses_resetsAt():
    text = ('noise\n{"event":"claude.rate_limit_event",'
            '"resetsAt":"2026-07-13T23:10:00Z"}\nmore noise')
    expected = int(datetime(2026, 7, 13, 23, 10, tzinfo=timezone.utc).timestamp())
    assert parse_structured_reset_epoch(text) == expected


def test_parse_structured_reset_epoch_none_when_absent():
    assert parse_structured_reset_epoch("no structured line here") is None


def test_parse_structured_reset_epoch_none_when_malformed_json():
    assert parse_structured_reset_epoch('{"event":"claude.rate_limit_event", broken') is None


def test_parse_fallback_reset_epoch_parses_human_readable_reset():
    now = int(datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc).timestamp())
    text = "You've hit your session limit · resets 11:10pm (UTC)"
    expected = int(datetime(2026, 7, 13, 23, 10, tzinfo=timezone.utc).timestamp())
    assert parse_fallback_reset_epoch(text, now) == expected


def test_parse_fallback_reset_epoch_rolls_over_to_next_day():
    now = int(datetime(2026, 7, 13, 23, 30, tzinfo=timezone.utc).timestamp())
    text = "resets 11:10pm (UTC)"
    expected = int(datetime(2026, 7, 14, 23, 10, tzinfo=timezone.utc).timestamp())
    assert parse_fallback_reset_epoch(text, now) == expected


def test_parse_fallback_reset_epoch_none_when_unparseable():
    assert parse_fallback_reset_epoch("session limit hit, try later", 0) is None


def test_parse_fallback_reset_epoch_none_for_unknown_timezone():
    assert parse_fallback_reset_epoch("resets 11:10pm (Nowhere/Fake)", 0) is None


def test_compute_resume_epoch_prefers_structured_over_fallback():
    now = int(datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc).timestamp())
    text = ('{"event":"claude.rate_limit_event","resetsAt":"2026-07-13T23:10:00Z"}\n'
            'resets 11:00pm (UTC)')
    expected = int(datetime(2026, 7, 13, 23, 10, tzinfo=timezone.utc).timestamp()) + 300
    assert compute_resume_epoch(text, now, buffer_minutes=5, fallback_minutes=30) == expected


def test_compute_resume_epoch_uses_regex_fallback_when_no_structured():
    now = int(datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc).timestamp())
    text = "session limit reached · resets 11:10pm (UTC)"
    expected = int(datetime(2026, 7, 13, 23, 10, tzinfo=timezone.utc).timestamp()) + 300
    assert compute_resume_epoch(text, now, buffer_minutes=5, fallback_minutes=30) == expected


def test_compute_resume_epoch_uses_fallback_minutes_when_unparseable():
    now = 1_000_000
    text = "429 too many requests, session limit reached"
    assert compute_resume_epoch(text, now, 5, 30) == now + 30 * 60


def test_compute_resume_epoch_none_when_no_match():
    assert compute_resume_epoch("unrelated error", 0, 5, 30) is None


def test_write_pause_sentinel_atomic(tmp_path):
    write_pause_sentinel(123456, tmp_path)
    sentinel = tmp_path / "session-window-paused"
    assert sentinel.read_text() == "123456"
    assert not (tmp_path / "session-window-paused.tmp").exists()


def test_write_pause_sentinel_creates_state_dir(tmp_path):
    nested = tmp_path / "nested" / "state"
    write_pause_sentinel(1, nested)
    assert (nested / "session-window-paused").read_text() == "1"


def test_check_and_pause_writes_sentinel_and_returns_epoch(tmp_path):
    text = "429 rate limit hit"
    epoch = check_and_pause(text, tmp_path, now_epoch=1_000_000,
                             buffer_minutes=5, fallback_minutes=30)
    assert epoch == 1_000_000 + 1800
    assert (tmp_path / "session-window-paused").read_text() == str(epoch)


def test_check_and_pause_returns_none_and_writes_nothing_when_no_match(tmp_path):
    epoch = check_and_pause("unrelated", tmp_path, 1_000_000, 5, 30)
    assert epoch is None
    assert not (tmp_path / "session-window-paused").exists()
