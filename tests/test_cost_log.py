"""#607: GraphQL cost log — one JSONL line per measured call under the scheduler's
state dir, source-tagged, size-rotated, written only when the path variable is
explicitly set, and failing soft (measurement must never break the call)."""
import fcntl
import gzip
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core import cost_log  # noqa: E402
from factory_core import rotation  # noqa: E402

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
RATE = {"cost": 1, "remaining": 9, "resetAt": "r"}


def _store_lines(directory):
    """Every line of the cost log: the live file, then each generation (plain or .gz)."""
    log = directory / cost_log.LOG_NAME
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    for _, forms in sorted(rotation.generations(log).items()):
        path = forms.get("plain") or forms["gz"]
        data = path.read_bytes()
        if path.name.endswith(".gz"):
            data = gzip.decompress(data)
        lines.extend(data.decode("utf-8").splitlines())
    return lines


def _fill_over(log, threshold):
    line = json.dumps({"ts": "old", "source": "scheduler", "context": "old", "cost": 1,
                       "remaining": 1, "reset_at": "r"}) + "\n"
    log.write_text(line * (threshold // len(line) + 1), encoding="utf-8")


@pytest.fixture
def deadline():
    """Fail, never hang, when an open blocks (a FIFO opened without O_NONBLOCK)."""
    def _blocked(*_):
        raise TimeoutError("blocked for 10 s")
    old = signal.signal(signal.SIGALRM, _blocked)
    signal.alarm(10)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, old)


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
    _fill_over(log, cost_log.ROTATE_BYTES)
    before = log.read_bytes()
    cost_log.log_call("board_items", {"cost": 3, "remaining": 7, "resetAt": "r"})
    # The fresh file holds only the new line. The old generation is kept, numbered and
    # compressed (#652), not overwritten.
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1
    assert not (tmp_path / (cost_log.LOG_NAME + ".1")).exists()
    assert gzip.decompress((tmp_path / (cost_log.LOG_NAME + ".1.gz")).read_bytes()) == before


def test_log_call_fails_soft_on_unwritable_dir(tmp_path, monkeypatch):
    # State dir points at a path that cannot hold a file (a FILE is its parent):
    # measurement must not raise.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file", encoding="utf-8")
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(blocker / "dir"))
    cost_log.log_call("board_items", {"cost": 1, "remaining": 9, "resetAt": "r"})  # must not raise
    assert not blocker.is_dir()


_COST_WORKER = (
    "import sys\n"
    "from factory_core import cost_log\n"
    "cost_log.ROTATE_BYTES = 2000\n"
    "writer, count = sys.argv[1], int(sys.argv[2])\n"
    "for i in range(count):\n"
    "    cost_log.log_call(f'{writer}:{i}', {'cost': i, 'remaining': 0, 'resetAt': 'r'})\n"
)


def test_rotation_keeps_every_generation(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cost_log, "ROTATE_BYTES", 300)
    seen = {}
    calls = 0
    while len(seen) < 5 or calls < 40:
        cost_log.log_call(f"call-{calls}", RATE)
        calls += 1
        for gz in tmp_path.glob(cost_log.LOG_NAME + ".*.gz"):
            data = gz.read_bytes()
            assert seen.setdefault(gz.name, data) == data  # never rewritten by a later rotation
        assert calls < 500
    assert {f"{cost_log.LOG_NAME}.{n}.gz" for n in range(1, 6)} <= set(seen)
    assert all(gzip.decompress(data) for data in seen.values())
    contexts = [json.loads(line)["context"] for line in _store_lines(tmp_path)]
    assert sorted(contexts) == sorted(f"call-{i}" for i in range(calls))


def test_concurrent_appends_through_rotations_lose_nothing(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(_SCRIPTS_DIR), SCHEDULER_STATE_DIR=str(tmp_path))
    writers = [subprocess.Popen([sys.executable, "-c", _COST_WORKER, str(w), "400"], env=env,
                                stderr=subprocess.DEVNULL) for w in range(8)]
    assert [p.wait(timeout=180) for p in writers] == [0] * 8
    contexts = [json.loads(line)["context"] for line in _store_lines(tmp_path)]
    assert len(contexts) == 3200 and len(set(contexts)) == 3200
    assert not any("plain" in forms for forms in rotation.generations(tmp_path / cost_log.LOG_NAME).values())
    assert rotation.temporaries(tmp_path / cost_log.LOG_NAME) == []


def test_append_is_not_blocked_by_a_slow_compression(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cost_log, "ROTATE_BYTES", 300)
    log = tmp_path / cost_log.LOG_NAME
    _fill_over(log, 300)
    compressing, release = threading.Event(), threading.Event()
    original = rotation._write_gz

    def slow_write(src, dst):
        compressing.set()
        release.wait(10)
        original(src, dst)

    monkeypatch.setattr(rotation, "_write_gz", slow_write)
    first = threading.Thread(target=cost_log.log_call, args=("first", RATE))
    first.start()
    try:
        assert compressing.wait(5)
        started = time.monotonic()
        cost_log.log_call("second", RATE)
        elapsed = time.monotonic() - started
    finally:
        release.set()
        first.join(10)
    assert elapsed < 0.9  # under the 1 s bounded wait: the lock was free during compression
    assert [json.loads(l)["context"] for l in log.read_text().splitlines()] == ["first", "second"]
    assert (tmp_path / (cost_log.LOG_NAME + ".1.gz")).exists()


def test_log_call_returns_within_two_seconds_when_the_lock_is_held(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cost_log, "ROTATE_BYTES", 300)
    log = tmp_path / cost_log.LOG_NAME
    _fill_over(log, 300)
    with open(tmp_path / (cost_log.LOG_NAME + ".lock"), "a") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        started = time.monotonic()
        cost_log.log_call("held", RATE)
        elapsed = time.monotonic() - started
    assert elapsed < 2
    assert json.loads(log.read_text().splitlines()[-1])["context"] == "held"  # the row is kept
    assert rotation.generations(log) == {}  # and no rotation happened


def test_log_call_with_a_fifo_as_lock_file_appends_and_returns(tmp_path, monkeypatch, deadline):
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cost_log, "ROTATE_BYTES", 300)
    log = tmp_path / cost_log.LOG_NAME
    _fill_over(log, 300)
    os.mkfifo(tmp_path / (cost_log.LOG_NAME + ".lock"))
    started = time.monotonic()
    cost_log.log_call("fifo", RATE)
    assert time.monotonic() - started < 2
    assert json.loads(log.read_text().splitlines()[-1])["context"] == "fifo"
    assert rotation.generations(log) == {}


def test_old_numbered_generations_are_kept_compressed_and_read(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cost_log, "ROTATE_BYTES", 300)
    log = tmp_path / cost_log.LOG_NAME
    legacy = tmp_path / (cost_log.LOG_NAME + ".1")  # the single generation kept before #652
    legacy.write_text(json.dumps({"context": "legacy"}) + "\n", encoding="utf-8")
    _fill_over(log, 300)
    cost_log.log_call("new", RATE)
    assert {forms.get("gz").name for forms in rotation.generations(log).values()} == {
        cost_log.LOG_NAME + ".1.gz", cost_log.LOG_NAME + ".2.gz"}
    contexts = [json.loads(line)["context"] for line in _store_lines(tmp_path)]
    assert "legacy" in contexts and "new" in contexts


@pytest.mark.parametrize("planted", ["fifo", "link", "directory"])
def test_log_call_never_waits_on_or_writes_through_a_planted_live_file(tmp_path, monkeypatch, deadline, planted):
    # log_call runs in the scheduler's poll. A FIFO at the live name blocked a plain open()
    # for good; a link there was written through.
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(tmp_path))
    log = tmp_path / cost_log.LOG_NAME
    victim = tmp_path / "victim.txt"
    victim.write_text("ORIGINAL")
    if planted == "fifo":
        os.mkfifo(log)
    elif planted == "link":
        log.symlink_to(victim)
    else:
        log.mkdir()
    started = time.monotonic()
    cost_log.log_call("planted", RATE)  # returns, raises nothing
    assert time.monotonic() - started < 2
    assert victim.read_text() == "ORIGINAL"
    log.rmdir() if planted == "directory" else log.unlink()
    cost_log.log_call("after", RATE)  # the next call, on a clean name, writes again
    assert json.loads(log.read_text().splitlines()[-1])["context"] == "after"
