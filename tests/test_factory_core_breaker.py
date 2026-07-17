import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core.breaker import (
    get_retry_count, increment_retry, reset_retry, trip_to_blocked,
)


def test_get_retry_count_missing_file(tmp_path):
    assert get_retry_count("42:refine", tmp_path / "state.json") == 0


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
