import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest


def _cli(monkeypatch, **env):
    for k in ("FACTORY_OWNER", "FACTORY_REPO", "FACTORY_PROJECT_ID", "FACTORY_PRODUCT_NAME"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import factory_core.identity as identity
    importlib.reload(identity)
    import factory_core.cli as cli_mod
    importlib.reload(cli_mod)
    return cli_mod


def test_marker_prints_footer(monkeypatch, capsys):
    cli_mod = _cli(monkeypatch, FACTORY_PRODUCT_NAME="Acme")
    monkeypatch.setattr(sys, "argv", ["cli.py", "marker", "refinement"])
    cli_mod.main()
    assert capsys.readouterr().out.strip() == "*Posted by Acme Refinement Pipeline*"


def test_marker_rejects_unknown_kind(monkeypatch):
    cli_mod = _cli(monkeypatch, FACTORY_PRODUCT_NAME="Acme")
    monkeypatch.setattr(sys, "argv", ["cli.py", "marker", "not_a_kind"])
    with pytest.raises(SystemExit):
        cli_mod.main()


def test_markers_regex_prints_escaped_alternation(monkeypatch, capsys):
    cli_mod = _cli(monkeypatch, FACTORY_PRODUCT_NAME="Acme")
    monkeypatch.setattr(sys, "argv", ["cli.py", "markers-regex"])
    cli_mod.main()
    out = capsys.readouterr().out.strip()
    assert "Posted\\ by\\ Acme\\ Refinement\\ Pipeline" in out or \
        "Posted by Acme Refinement Pipeline" in out
    assert "|" in out


def test_breaker_evaluate_stop_prints_stopped_and_reason(monkeypatch, tmp_path, capsys):
    cli_mod = _cli(monkeypatch, FACTORY_PRODUCT_NAME="Acme")
    state_file = tmp_path / "state.json"
    state_file.write_text("{}")
    monkeypatch.setenv("STATE_FILE", str(state_file))
    monkeypatch.setattr(sys, "argv", [
        "cli.py", "breaker-evaluate-stop", "--issue", "1", "--phase", "plan", "--ceiling", "3",
    ])
    cli_mod.main()
    assert capsys.readouterr().out.strip() == "stopped=false reason=none"


def test_breaker_evaluate_stop_trips_at_ceiling(monkeypatch, tmp_path, capsys):
    # A trip writes a runs.jsonl audit row (R8) via run_record.append_stop_record,
    # whose JSONL_PATH is bound at import time — same df#300 hermeticity requirement
    # as test_factory_core_breaker.py's autouse fixture (Task 3, step 0), applied
    # here by hand since this file has no such autouse fixture of its own.
    from factory_core import run_record
    monkeypatch.setattr(run_record, "JSONL_PATH", tmp_path / "runs.jsonl")
    cli_mod = _cli(monkeypatch, FACTORY_PRODUCT_NAME="Acme")
    state_file = tmp_path / "state.json"
    state_file.write_text('{"1:plan": 3}')
    monkeypatch.setenv("STATE_FILE", str(state_file))
    monkeypatch.setattr(sys, "argv", [
        "cli.py", "breaker-evaluate-stop", "--issue", "1", "--phase", "plan", "--ceiling", "3",
    ])
    cli_mod.main()
    assert capsys.readouterr().out.strip() == "stopped=true reason=max_retries"


def test_breaker_evaluate_stop_peek_does_not_increment(monkeypatch, tmp_path, capsys):
    cli_mod = _cli(monkeypatch, FACTORY_PRODUCT_NAME="Acme")
    state_file = tmp_path / "state.json"
    state_file.write_text("{}")
    monkeypatch.setenv("STATE_FILE", str(state_file))
    monkeypatch.setattr(sys, "argv", [
        "cli.py", "breaker-evaluate-stop", "--issue", "1", "--phase", "resolve",
        "--ceiling", "3", "--peek",
    ])
    cli_mod.main()
    assert capsys.readouterr().out.strip() == "stopped=false reason=none"
    import json
    assert json.loads(state_file.read_text()) == {}
