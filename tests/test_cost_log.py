"""#607: GraphQL cost log — one JSONL line per measured call under the scheduler's
state dir, source-tagged, size-rotated, written only when the path variable is
explicitly set, and failing soft (measurement must never break the call)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core import cost_log


def test_log_call_writes_one_line_with_all_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("DARK_FACTORY_COST_SOURCE", "scheduler")
    cost_log.log_call("board_items", {"cost": 12, "remaining": 4890, "resetAt": "2026-11-25T19:00:00Z"})
    lines = (tmp_path / cost_log.LOG_NAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    line = json.loads(lines[0])
    assert line["source"] == "scheduler"
    assert line["context"] == "board_items"
    assert line["cost"] == 12
    assert line["remaining"] == 4890
    assert line["reset_at"] == "2026-11-25T19:00:00Z"
    assert line["ts"].endswith("Z")


def test_log_call_source_defaults_to_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("DARK_FACTORY_COST_SOURCE", raising=False)
    cost_log.log_call("issue_states", {"cost": 1, "remaining": 1, "resetAt": "r"})
    line = json.loads((tmp_path / cost_log.LOG_NAME).read_text(encoding="utf-8").splitlines()[0])
    assert line["source"] == "unknown"


def test_log_call_appends_per_call(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(tmp_path))
    cost_log.log_call("board_items", {"cost": 1, "remaining": 9, "resetAt": "r"})
    cost_log.log_call("board_items", {"cost": 2, "remaining": 8, "resetAt": "r"})
    lines = (tmp_path / cost_log.LOG_NAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(l)["cost"] for l in lines] == [1, 2]


def test_log_call_no_write_when_state_dir_unset(tmp_path, monkeypatch):
    # CI's empty /var/lib/dark-factory assertion depends on this: the path variable
    # unset means no write, even to a path that exists.
    monkeypatch.delenv("SCHEDULER_STATE_DIR", raising=False)
    cost_log.log_call("board_items", {"cost": 1, "remaining": 9, "resetAt": "r"})
    assert not list(tmp_path.iterdir())


def test_log_call_no_write_for_missing_rate_limit(tmp_path, monkeypatch):
    # A GraphQL error body (data: null) carries no rateLimit — nothing to record.
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(tmp_path))
    cost_log.log_call("board_items", None)
    assert not (tmp_path / cost_log.LOG_NAME).exists()


def test_log_call_rotates_when_over_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(tmp_path))
    log = tmp_path / cost_log.LOG_NAME
    old = "".join(json.dumps({"ts": "old", "source": "scheduler", "context": "board_items",
                              "cost": 1, "remaining": 1, "reset_at": "r"}) + "\n"
                  for _ in range(1))
    log.write_text(old * (cost_log.ROTATE_BYTES // len(old) + 1), encoding="utf-8")
    cost_log.log_call("board_items", {"cost": 3, "remaining": 7, "resetAt": "r"})
    assert log.exists()
    rotated = tmp_path / (cost_log.LOG_NAME + ".1")
    assert rotated.exists()
    # the fresh file holds only the new line; the old generation is preserved, not lost
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1
    assert len(rotated.read_text(encoding="utf-8").splitlines()) > 1


def test_log_call_fails_soft_on_unwritable_dir(tmp_path, monkeypatch):
    # State dir points at a path that cannot hold a file (a FILE is its parent):
    # measurement must not raise.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file", encoding="utf-8")
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(blocker / "dir"))
    cost_log.log_call("board_items", {"cost": 1, "remaining": 9, "resetAt": "r"})  # must not raise
    assert not blocker.is_dir()
