import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core import board


def _items(items):
    return subprocess.CompletedProcess([], 0, stdout=json.dumps({"items": items}), stderr="")


def _ok():
    return subprocess.CompletedProcess([], 0, stdout="", stderr="")


def test_project_number_tracks_env_override(monkeypatch):
    import importlib
    monkeypatch.setenv("FACTORY_PROJECT_NUMBER", "7")
    from factory_core import identity as ident
    importlib.reload(ident)
    importlib.reload(board)
    assert board.PROJECT_NUMBER == 7


def test_find_board_item_found(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _items([
        {"id": "ITEM42", "content": {"number": 42, "type": "Issue"}},
    ]))
    assert board.find_board_item(42) == "ITEM42"


def test_find_board_item_wrong_number(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _items([
        {"id": "ITEM99", "content": {"number": 99, "type": "Issue"}},
    ]))
    assert board.find_board_item(42) == ""


def test_find_board_item_gh_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw:
        subprocess.CompletedProcess([], 1, stdout="", stderr="error"))
    assert board.find_board_item(42) == ""


def test_set_board_status_calls_item_edit(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        if "item-list" in cmd:
            return _items([{"id": "ITEM42", "content": {"number": 42, "type": "Issue"}}])
        return _ok()
    monkeypatch.setattr(subprocess, "run", fake)
    board.set_board_status(42, "opt_abc")
    assert any("item-edit" in " ".join(c) for c in calls)
    edit = next(c for c in calls if "item-edit" in " ".join(c))
    assert "opt_abc" in edit
    assert "ITEM42" in edit


def test_set_board_status_no_item_skips_edit(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _items([]))[1])
    board.set_board_status(42, "opt_abc")
    assert not any("item-edit" in " ".join(c) for c in calls)


def test_item_edit_status_returns_true_on_success(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok())
    assert board._item_edit_status("ITEM42", "opt_abc") is True


def test_item_edit_status_returns_false_and_prints_stderr_on_failure(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw:
        subprocess.CompletedProcess([], 1, stdout="", stderr="permission denied"))
    assert board._item_edit_status("ITEM42", "opt_abc") is False
    err = capsys.readouterr().err
    assert "ITEM42" in err
    assert "permission denied" in err


def test_find_item_by_number_checked_transport_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw:
        subprocess.CompletedProcess([], 1, stdout="", stderr="rate limited"))
    assert board._find_item_by_number_checked("42") == ("", False)


def test_find_item_by_number_checked_unparseable_json(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw:
        subprocess.CompletedProcess([], 0, stdout="not json", stderr=""))
    assert board._find_item_by_number_checked("42") == ("", False)


def test_find_item_by_number_checked_genuinely_absent(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _items([]))
    assert board._find_item_by_number_checked("42") == ("", True)


def test_find_item_by_number_checked_found(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _items([
        {"id": "ITEM42", "content": {"number": 42, "type": "Issue"}},
    ]))
    assert board._find_item_by_number_checked("42") == ("ITEM42", True)


def test_set_board_status_still_returns_none(monkeypatch):
    # Pins Requirement 3 / Design Decision 3: set_board_status's 4 direct callers
    # (breaker.py, rescue.py, deconflict.py, epic_autopilot.py) must see zero
    # behavior change even though the helpers it calls now report bool/tuple.
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _items([
        {"id": "ITEM42", "content": {"number": 42, "type": "Issue"}},
    ]) if "item-list" in cmd else _ok())
    assert board.set_board_status(42, "opt_abc") is None


def test_post_or_update_comment_new_comment(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", fake)
    board.post_or_update_comment(42, "<!-- marker -->", "body text")
    assert any("issue" in " ".join(c) and "comment" in " ".join(c) for c in calls)


def test_post_or_update_comment_updates_existing(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        if "--jq" in " ".join(cmd):
            return subprocess.CompletedProcess([], 0, stdout="12345\n", stderr="")
        return _ok()
    monkeypatch.setattr(subprocess, "run", fake)
    board.post_or_update_comment(42, "<!-- marker -->", "updated body")
    assert any("PATCH" in " ".join(c) for c in calls)
    assert any("12345" in " ".join(c) for c in calls)
