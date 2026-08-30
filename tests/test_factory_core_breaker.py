import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core import run_record
from factory_core.breaker import (
    get_retry_count, increment_retry, reset_retry, set_retry_count, trip_to_blocked,
)
from factory_core.breaker import StopVerdict, _loop_state_key
from factory_core.breaker import evaluate_stop_condition


@pytest.fixture(autouse=True)
def _hermetic_runs_jsonl(tmp_path, monkeypatch):
    """Never let a tripped StopVerdict's audit row (#198 R8) write to the real
    /var/lib/dark-factory/runs.jsonl (df#300 precedent — mirrors
    tests/test_run_record.py's own hermeticity fixture). _append_jsonl reads the
    JSONL_PATH module global directly, not a re-derived SCHEDULER_STATE_DIR, so it
    must be patched directly rather than via SCHEDULER_STATE_DIR."""
    monkeypatch.setattr(run_record, "JSONL_PATH", tmp_path / "runs.jsonl")


def test_get_retry_count_missing_file(tmp_path):
    assert get_retry_count("42:refine", tmp_path / "state.json") == 0


def test_stop_verdict_defaults():
    v = StopVerdict(stopped=False)
    assert v.reason is None
    assert v.detail == {}


def test_loop_state_key_shape():
    assert _loop_state_key("42:plan", "nightly-scan", "iter") == "42:plan:loop:nightly-scan:iter"
    assert _loop_state_key("42", "nightly-scan", "tokens") == "42:loop:nightly-scan:tokens"


def test_increment_creates_key(tmp_path):
    sf = tmp_path / "state.json"
    assert increment_retry("42:refine", sf) == 1
    assert get_retry_count("42:refine", sf) == 1


def test_increment_accumulates(tmp_path):
    sf = tmp_path / "state.json"
    increment_retry("42:refine", sf)
    increment_retry("42:refine", sf)
    assert get_retry_count("42:refine", sf) == 2


def test_increment_does_not_affect_other_keys(tmp_path):
    sf = tmp_path / "state.json"
    increment_retry("42:refine", sf)
    increment_retry("42:plan", sf)
    assert get_retry_count("42:refine", sf) == 1
    assert get_retry_count("42:plan", sf) == 1


def test_reset_removes_key(tmp_path):
    sf = tmp_path / "state.json"
    increment_retry("42:refine", sf)
    reset_retry("42:refine", sf)
    assert get_retry_count("42:refine", sf) == 0


def test_reset_noop_when_missing(tmp_path):
    sf = tmp_path / "state.json"
    reset_retry("42:refine", sf)  # should not raise


def test_implement_key_is_bare_issue_number(tmp_path):
    sf = tmp_path / "state.json"
    increment_retry("42", sf)
    assert get_retry_count("42", sf) == 1
    assert get_retry_count("42:implement", sf) == 0


def test_state_file_is_valid_json(tmp_path):
    sf = tmp_path / "state.json"
    increment_retry("42:refine", sf)
    data = json.loads(sf.read_text())
    assert data == {"42:refine": 1}


def test_atomic_write_survives_existing_file(tmp_path):
    sf = tmp_path / "state.json"
    sf.write_text('{"existing": 5}')
    increment_retry("42:refine", sf)
    data = json.loads(sf.read_text())
    assert data["existing"] == 5
    assert data["42:refine"] == 1


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


def test_trip_to_blocked_resets_retry(tmp_path, monkeypatch):
    sf = tmp_path / "state.json"
    increment_retry("42", sf)
    monkeypatch.setattr(subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess([], 0, stdout="", stderr=""))
    with patch("factory_core.board.set_board_status"):
        trip_to_blocked(42, "implement", "test reason", sf)
    assert get_retry_count("42", sf) == 0


def test_trip_to_blocked_phase_key_naming(tmp_path, monkeypatch):
    sf = tmp_path / "state.json"
    increment_retry("42:refine", sf)
    monkeypatch.setattr(subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess([], 0, stdout="", stderr=""))
    with patch("factory_core.board.set_board_status"):
        trip_to_blocked(42, "refine", "test reason", sf)
    assert get_retry_count("42:refine", sf) == 0


def test_trip_to_blocked_posts_comment(tmp_path, monkeypatch):
    sf = tmp_path / "state.json"
    calls = []
    monkeypatch.setattr(subprocess, "run",
        lambda cmd, **kw: (calls.append(cmd),
                           subprocess.CompletedProcess([], 0, stdout="", stderr=""))[1])
    with patch("factory_core.board.set_board_status"):
        trip_to_blocked(42, "plan", "retry limit reached", sf)
    assert any("comment" in " ".join(c) for c in calls)


def test_trip_to_blocked_moves_to_blocked(tmp_path, monkeypatch):
    from factory_core.board import STATUS_BLOCKED

    sf = tmp_path / "state.json"
    monkeypatch.setattr(subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess([], 0, stdout="", stderr=""))
    with patch("factory_core.board.set_board_status") as mock_sbs:
        trip_to_blocked(42, "implement", "test reason", sf)
    mock_sbs.assert_called_once_with(42, STATUS_BLOCKED)


def test_trip_to_blocked_adds_both_labels(tmp_path, monkeypatch):
    sf = tmp_path / "state.json"
    calls = []
    monkeypatch.setattr(subprocess, "run",
        lambda cmd, **kw: (calls.append(cmd),
                           subprocess.CompletedProcess([], 0, stdout="", stderr=""))[1])
    with patch("factory_core.board.set_board_status"):
        trip_to_blocked(42, "plan", "retry limit reached", sf)
    edit_cmds = [" ".join(c) for c in calls if "issue" in c and "edit" in c]
    assert any("needs-discussion" in c for c in edit_cmds)
    assert any("factory-regression" in c for c in edit_cmds)


from factory_core.breaker import record_failure_signature


def _drop(state_dir, issue, phase, signature, exit_code=1):
    sig_dir = state_dir / "error-signatures"
    sig_dir.mkdir(parents=True, exist_ok=True)
    (sig_dir / f"{issue}.{phase}.sig").write_text(
        json.dumps({"signature": signature, "phase": phase, "exit_code": exit_code}))


def test_record_failure_signature_no_drop_file_returns_false(tmp_path):
    sf = tmp_path / "state.json"
    stuck, sig = record_failure_signature(1, "implement", sf, tmp_path)
    assert stuck is False
    assert sig == ""


def test_record_failure_signature_first_substantive_not_stuck(tmp_path):
    sf = tmp_path / "state.json"
    _drop(tmp_path, 1, "implement", "substantive:test_failure:1")
    stuck, sig = record_failure_signature(1, "implement", sf, tmp_path)
    assert stuck is False
    assert sig == "substantive:test_failure:1"


def test_record_failure_signature_second_matching_substantive_is_stuck(tmp_path):
    sf = tmp_path / "state.json"
    _drop(tmp_path, 1, "implement", "substantive:test_failure:1")
    record_failure_signature(1, "implement", sf, tmp_path)
    _drop(tmp_path, 1, "implement", "substantive:test_failure:1")
    stuck, sig = record_failure_signature(1, "implement", sf, tmp_path)
    assert stuck is True
    assert sig == "substantive:test_failure:1"


def test_record_failure_signature_different_substantive_not_stuck(tmp_path):
    sf = tmp_path / "state.json"
    _drop(tmp_path, 1, "implement", "substantive:test_failure:1")
    record_failure_signature(1, "implement", sf, tmp_path)
    _drop(tmp_path, 1, "implement", "substantive:build_failure:1")
    stuck, sig = record_failure_signature(1, "implement", sf, tmp_path)
    assert stuck is False
    assert sig == "substantive:build_failure:1"


def test_record_failure_signature_environmental_never_stuck_even_when_repeated(tmp_path):
    sf = tmp_path / "state.json"
    _drop(tmp_path, 279, "implement", "environmental:delivery_failure")
    record_failure_signature(279, "implement", sf, tmp_path)
    _drop(tmp_path, 279, "implement", "environmental:delivery_failure")
    stuck, sig = record_failure_signature(279, "implement", sf, tmp_path)
    assert stuck is False
    assert sig == "environmental:delivery_failure"


def test_record_failure_signature_consumes_drop_file(tmp_path):
    sf = tmp_path / "state.json"
    _drop(tmp_path, 1, "plan", "substantive:unknown:1")
    record_failure_signature(1, "plan", sf, tmp_path)
    assert not (tmp_path / "error-signatures" / "1.plan.sig").exists()


def test_record_failure_signature_respects_phase_key_naming(tmp_path):
    # implement uses the bare issue number key; plan/refine/resolve use "<issue>:<phase>".
    sf = tmp_path / "state.json"
    _drop(tmp_path, 5, "implement", "substantive:test_failure:1")
    record_failure_signature(5, "implement", sf, tmp_path)
    data = json.loads(sf.read_text())
    assert "5:sig" in data
    assert "5:implement:sig" not in data

    _drop(tmp_path, 5, "plan", "substantive:test_failure:1")
    record_failure_signature(5, "plan", sf, tmp_path)
    data = json.loads(sf.read_text())
    assert "5:plan:sig" in data


def test_record_failure_signature_does_not_disturb_retry_count(tmp_path):
    sf = tmp_path / "state.json"
    increment_retry("5:plan", sf)
    _drop(tmp_path, 5, "plan", "substantive:test_failure:1")
    record_failure_signature(5, "plan", sf, tmp_path)
    assert get_retry_count("5:plan", sf) == 1


def test_reset_retry_clears_stored_signature(tmp_path):
    # Regression for the #33 review finding: reset_retry (e.g. via a successful run,
    # Continue-dispatch, or blocked-rescue) must also clear the stored "<key>:sig"
    # entry, otherwise the signature chain survives the reset and the *first*
    # post-reset failure with a matching class trips the breaker one attempt early.
    sf = tmp_path / "state.json"
    _drop(tmp_path, 9, "implement", "substantive:test_failure:1")
    record_failure_signature(9, "implement", sf, tmp_path)
    data = json.loads(sf.read_text())
    assert "9:sig" in data

    reset_retry("9", sf)

    data = json.loads(sf.read_text())
    assert "9:sig" not in data

    # A subsequent failure with the same class must NOT be immediately "stuck" —
    # it is the first attempt since the reset.
    _drop(tmp_path, 9, "implement", "substantive:test_failure:1")
    stuck, sig = record_failure_signature(9, "implement", sf, tmp_path)
    assert stuck is False
    assert sig == "substantive:test_failure:1"


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


def test_evaluate_stop_condition_parity_not_tripped_increments(tmp_path):
    sf = tmp_path / "state.json"
    v = evaluate_stop_condition(None, 42, "plan", ceiling=3, state_file=sf)
    assert v == StopVerdict(False)
    assert get_retry_count("42:plan", sf) == 1


def test_evaluate_stop_condition_parity_trips_at_ceiling(tmp_path):
    sf = tmp_path / "state.json"
    for _ in range(3):
        evaluate_stop_condition(None, 42, "plan", ceiling=3, state_file=sf)
    v = evaluate_stop_condition(None, 42, "plan", ceiling=3, state_file=sf)
    assert v.stopped is True
    assert v.reason == "max_retries"
    # tripped: counter is NOT incremented past the ceiling
    assert get_retry_count("42:plan", sf) == 3


def test_evaluate_stop_condition_peek_does_not_increment(tmp_path):
    sf = tmp_path / "state.json"
    v = evaluate_stop_condition(None, 42, "resolve", ceiling=3, state_file=sf, peek=True)
    assert v == StopVerdict(False)
    assert get_retry_count("42:resolve", sf) == 0


def test_evaluate_stop_condition_peek_still_trips_at_ceiling(tmp_path):
    sf = tmp_path / "state.json"
    from factory_core.breaker import set_retry_count
    set_retry_count("42:resolve", 3, sf)
    v = evaluate_stop_condition(None, 42, "resolve", ceiling=3, state_file=sf, peek=True)
    assert v.stopped is True
    assert v.reason == "max_retries"


def test_evaluate_stop_condition_parity_never_writes_loop_key(tmp_path):
    sf = tmp_path / "state.json"
    evaluate_stop_condition(None, 42, "plan", ceiling=3, state_file=sf)
    data = json.loads(sf.read_text())
    assert not any(":loop:" in k for k in data)


def _loop(name="nightly-scan", **scheduling_extra):
    entry = {
        "name": name,
        "purpose": "test loop",
        "side_effect_level": 2,
        "discovery": {"trigger": "cron:0 6 * * *", "inputs": []},
        "handoff": {"manifest": "h.py", "outputs": []},
        "verification": {"verifier": "v.py", "stop_condition": "s.py"},
        "persistence": {"artifacts": []},
        "scheduling": {"failure_behavior": "escalate_to_human", **scheduling_extra},
    }
    return entry


def test_max_iterations_trips_after_n_evaluations(tmp_path):
    sf = tmp_path / "state.json"
    entry = _loop(max_iterations=3)
    for _ in range(3):
        v = evaluate_stop_condition(entry, 7, "implement", ceiling=10, state_file=sf)
        assert v.stopped is False
    v = evaluate_stop_condition(entry, 7, "implement", ceiling=10, state_file=sf)
    assert v.stopped is True
    assert v.reason == "max_iterations"
    assert v.detail["iter"] == 3
    assert v.detail["max_iterations"] == 3


def test_max_iterations_tighten_only_factory_ceiling_wins(tmp_path):
    """side_effect_level 5, max_iterations=10, ceiling=3: the 4th evaluation trips
    on the factory ceiling, not the declared 10 (R2)."""
    sf = tmp_path / "state.json"
    entry = _loop(max_iterations=10)
    entry["side_effect_level"] = 5
    for _ in range(3):
        evaluate_stop_condition(entry, 7, "implement", ceiling=3, state_file=sf)
    v = evaluate_stop_condition(entry, 7, "implement", ceiling=3, state_file=sf)
    assert v.stopped is True
    assert v.reason == "max_retries"


def test_deadline_trips_at_exact_boundary(tmp_path):
    sf = tmp_path / "state.json"
    entry = _loop(deadline_seconds=60)
    v0 = evaluate_stop_condition(entry, 8, "implement", ceiling=10, state_file=sf, now=1000)
    assert v0.stopped is False
    v1 = evaluate_stop_condition(entry, 8, "implement", ceiling=10, state_file=sf, now=1059)
    assert v1.stopped is False
    v2 = evaluate_stop_condition(entry, 8, "implement", ceiling=10, state_file=sf, now=1060)
    assert v2.stopped is True
    assert v2.reason == "deadline"
    assert v2.detail["elapsed"] == 60


def test_deadline_start_anchored_once(tmp_path):
    sf = tmp_path / "state.json"
    entry = _loop(deadline_seconds=60)
    evaluate_stop_condition(entry, 8, "implement", ceiling=10, state_file=sf, now=1000)
    evaluate_stop_condition(entry, 8, "implement", ceiling=10, state_file=sf, now=1010)
    # _make_key(8, "implement") is the bare "8" (implement's special case, breaker.py's
    # existing convention) — the loop-scoped key is "8:loop:<name>:...", NOT
    # "8:implement:loop:...". Matches Task 2's own _loop_state_key test.
    assert get_retry_count("8:loop:nightly-scan:deadline_start", sf) == 1000


def test_max_tokens_absent_never_trips(tmp_path):
    sf = tmp_path / "state.json"
    entry = _loop()  # no max_tokens anywhere — budget_caps absent entirely
    v = evaluate_stop_condition(entry, 9, "implement", ceiling=10, state_file=sf)
    assert v.stopped is False


def test_max_tokens_trips_after_add_loop_tokens(tmp_path):
    from factory_core.breaker import add_loop_tokens
    sf = tmp_path / "state.json"
    entry = _loop()
    entry["budget_caps"] = {"max_tokens": 1000}
    add_loop_tokens(9, "implement", "nightly-scan", 1000, sf)
    v = evaluate_stop_condition(entry, 9, "implement", ceiling=10, state_file=sf)
    assert v.stopped is True
    assert v.reason == "max_tokens"
    assert v.detail == {"tokens": 1000, "max_tokens": 1000}


def test_populated_entry_no_caps_declared_behaves_as_parity(tmp_path):
    """Absence of every cap field means parity (R2): a populated entry with no
    max_iterations/deadline_seconds/budget_caps never trips on cap grounds."""
    sf = tmp_path / "state.json"
    entry = _loop()
    for _ in range(5):
        v = evaluate_stop_condition(entry, 10, "implement", ceiling=10, state_file=sf)
        assert v.stopped is False


def test_add_loop_tokens_from_run_record_totals_shape(tmp_path):
    """R7: add_loop_tokens is 'unit-tested against run_record totals fixtures' —
    pin the intended data source (input + output tokens summed), not a bare int."""
    from factory_core.breaker import add_loop_tokens
    sf = tmp_path / "state.json"
    totals = {"gen_ai.usage.input_tokens": 600, "gen_ai.usage.output_tokens": 400}
    n = totals["gen_ai.usage.input_tokens"] + totals["gen_ai.usage.output_tokens"]
    assert add_loop_tokens(9, "implement", "nightly-scan", n, sf) == 1000
    entry = _loop()
    entry["budget_caps"] = {"max_tokens": 1000}
    v = evaluate_stop_condition(entry, 9, "implement", ceiling=10, state_file=sf)
    assert v.stopped is True and v.reason == "max_tokens"


def test_cap_class_trip_independent_of_predicate_state(tmp_path):
    """R6's third fixture assertion, proven here directly against breaker.py — it
    needs nothing from #197's verifier.py, so it does not wait on Task 17: with
    max_iterations reached, evaluate_stop_condition trips with reason
    max_iterations regardless of any predicate/verification field's content."""
    sf = tmp_path / "state.json"
    entry = _loop(max_iterations=1)
    entry["verification"]["stop_condition"] = "scripts/cost_report_marker_check.py"
    v = evaluate_stop_condition(entry, 300, "implement", ceiling=10, state_file=sf)
    assert v.stopped is False
    v2 = evaluate_stop_condition(entry, 300, "implement", ceiling=10, state_file=sf)
    assert v2.stopped is True
    assert v2.reason == "max_iterations"


def test_reset_retry_clears_loop_state(tmp_path):
    from factory_core.breaker import _make_key, add_loop_tokens
    sf = tmp_path / "state.json"
    entry = _loop(max_iterations=5)
    entry["budget_caps"] = {"max_tokens": 5000}
    evaluate_stop_condition(entry, 11, "implement", ceiling=10, state_file=sf)
    add_loop_tokens(11, "implement", "nightly-scan", 100, sf)

    key = _make_key(11, "implement")
    assert get_retry_count(f"{key}:loop:nightly-scan:iter", sf) == 1
    assert get_retry_count(f"{key}:loop:nightly-scan:deadline_start", sf) != 0
    assert get_retry_count(f"{key}:loop:nightly-scan:tokens", sf) == 100

    reset_retry(key, sf)

    assert get_retry_count(f"{key}:loop:nightly-scan:iter", sf) == 0
    assert get_retry_count(f"{key}:loop:nightly-scan:deadline_start", sf) == 0
    assert get_retry_count(f"{key}:loop:nightly-scan:tokens", sf) == 0

    # next evaluation starts fresh — not tripped even though 5 prior "attempts" existed
    v = evaluate_stop_condition(entry, 11, "implement", ceiling=10, state_file=sf)
    assert v.stopped is False
