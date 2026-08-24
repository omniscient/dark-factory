import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core.session_window import (
    is_session_window_failure,
    parse_structured_reset_epoch,
    parse_fallback_reset_epoch,
    compute_resume_epoch,
    write_pause_sentinel,
    check_and_pause,
    RATE_LIMIT_RE,
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


def test_parse_structured_reset_epoch_assumed_292_payload_shape():
    # Pinned to the payload shape documented in
    # docs/archive/2026-07-13-scheduler-session-window-backoff-design.md — the
    # structured log line the Claude Code runner emits into the captured run output.
    text = ('some claude output\n'
            '{"event":"claude.rate_limit_event","resetsAt":"2026-07-13T23:10:00Z"}\n')
    expected = int(datetime(2026, 7, 13, 23, 10, tzinfo=timezone.utc).timestamp())
    assert parse_structured_reset_epoch(text) == expected


def test_parse_structured_reset_epoch_handles_epoch_int_resetsat():
    # A differently-shaped payload (epoch seconds instead of an ISO-8601 string) must
    # not silently no-op and fall back to the 30-min default (#35 review).
    epoch = int(datetime(2026, 7, 13, 23, 10, tzinfo=timezone.utc).timestamp())
    text = f'{{"event":"claude.rate_limit_event","resetsAt":{epoch}}}'
    assert parse_structured_reset_epoch(text) == epoch


def test_parse_structured_reset_epoch_none_when_malformed_json():
    assert parse_structured_reset_epoch('{"event":"claude.rate_limit_event", broken') is None


def test_parse_fallback_reset_epoch_parses_human_readable_reset():
    now = int(datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc).timestamp())
    text = "You've hit your session limit · resets 11:10pm (UTC)"
    expected = int(datetime(2026, 7, 13, 23, 10, tzinfo=timezone.utc).timestamp())
    assert parse_fallback_reset_epoch(text, now) == expected


def test_parse_fallback_reset_epoch_stays_today_when_time_already_passed():
    now = int(datetime(2026, 7, 13, 23, 30, tzinfo=timezone.utc).timestamp())
    text = "resets 11:10pm (UTC)"
    expected = int(datetime(2026, 7, 13, 23, 10, tzinfo=timezone.utc).timestamp())
    assert parse_fallback_reset_epoch(text, now) == expected


def test_parse_fallback_reset_epoch_matches_305_incident_repro():
    # Issue #305 repro: death at 22:49Z, reset text names 21:20Z (already passed today).
    # Must resolve to today, not roll to tomorrow (the ~22h false-pause bug).
    now = int(datetime(2026, 7, 18, 22, 49, tzinfo=timezone.utc).timestamp())
    text = "...resets 9:20pm (UTC)"
    result = parse_fallback_reset_epoch(text, now)
    assert result is not None
    assert result <= now


def test_parse_fallback_reset_epoch_rolls_forward_when_still_within_window():
    # Failure at 23:40Z, named reset "12:10am (UTC)" is just after midnight -- rolling
    # to tomorrow keeps the resume within the 5h session window, so it must roll
    # forward (unlike the #305 incident case, which stays today/past).
    now = int(datetime(2026, 7, 18, 23, 40, tzinfo=timezone.utc).timestamp())
    text = "resets 12:10am (UTC)"
    expected = int(datetime(2026, 7, 19, 0, 10, tzinfo=timezone.utc).timestamp())
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


def test_compute_resume_epoch_clamps_structured_far_future_to_max_window():
    # A malformed/far-out structured resetsAt (here +48h) is exactly as physically
    # impossible as the fallback rollover bug and must be bounded the same way.
    now = int(datetime(2026, 7, 18, 22, 49, tzinfo=timezone.utc).timestamp())
    far_future = datetime.fromtimestamp(now, tz=timezone.utc) + timedelta(hours=48)
    resets_at = far_future.isoformat().replace("+00:00", "Z")
    text = f'{{"event":"claude.rate_limit_event","resetsAt":"{resets_at}"}}'
    result = compute_resume_epoch(text, now, buffer_minutes=5, fallback_minutes=30)
    ceiling = now + 5 * 3600 + 5 * 60
    assert result <= ceiling


def test_compute_resume_epoch_clamps_305_incident_to_max_window():
    now = int(datetime(2026, 7, 18, 22, 49, tzinfo=timezone.utc).timestamp())
    text = "You've hit your session limit · resets 9:20pm (UTC)"
    result = compute_resume_epoch(text, now, buffer_minutes=5, fallback_minutes=30)
    assert result is not None
    assert result <= now + 5 * 3600 + 5 * 60


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
    text = "429 too many requests, session limit reached"
    epoch = check_and_pause(text, tmp_path, now_epoch=1_000_000,
                             buffer_minutes=5, fallback_minutes=30)
    assert epoch == 1_000_000 + 1800
    assert (tmp_path / "session-window-paused").read_text() == str(epoch)


def test_check_and_pause_returns_none_and_writes_nothing_when_no_match(tmp_path):
    epoch = check_and_pause("unrelated", tmp_path, 1_000_000, 5, 30)
    assert epoch is None
    assert not (tmp_path / "session-window-paused").exists()


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


import subprocess
import sys as _sys


def test_cli_session_window_check_matched(tmp_path):
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
    assert "matched=true" in result.stdout
    assert (state_dir / "session-window-paused").exists()


def test_cli_session_window_check_unmatched(tmp_path):
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
    assert "matched=false" in result.stdout
    assert not (state_dir / "session-window-paused").exists()
