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


def test_set_board_status_with_item_id_makes_zero_item_list_calls(monkeypatch):
    # #607: the scheduler's board snapshot now carries each item's ProjectV2 id;
    # board-move with a supplied item_id goes straight to the item-edit and makes
    # NO item-list lookup call (five per-item-loop call sites were paying one
    # unbounded item-list call per move).
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    board.set_board_status(42, "opt_abc", item_id="ITEM_FROM_SNAPSHOT")
    assert not any("item-list" in " ".join(c) for c in calls), "supplied item_id must not trigger a lookup"
    assert len(calls) == 1
    edit = calls[0]
    assert "item-edit" in " ".join(edit)
    assert "ITEM_FROM_SNAPSHOT" in edit
    assert "opt_abc" in edit


def test_set_board_status_with_item_id_still_reports_edit_failure(monkeypatch, capsys):
    # #607: skipping the lookup must not weaken the edit's failure signalling —
    # a refused item-edit still prints stderr and returns False (board.set_board_status
    # itself keeps returning None for its 4 direct callers).
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw:
        subprocess.CompletedProcess([], 1, stdout="", stderr="rate limited"))
    assert board._item_edit_status("ITEM42", "opt_abc") is False
    assert "rate limited" in capsys.readouterr().err


def test_set_board_status_empty_item_id_falls_back_to_lookup(monkeypatch):
    # #607: an empty/absent item_id (e.g. an item with no id in an old snapshot)
    # falls back to the historical lookup path — never a no-op.
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        if "item-list" in " ".join(cmd):
            return _items([{"id": "ITEM42", "content": {"number": 42, "type": "Issue"}}])
        return _ok()
    monkeypatch.setattr(subprocess, "run", fake)
    board.set_board_status(42, "opt_abc", item_id="")
    assert any("item-list" in " ".join(c) for c in calls)
    assert any("item-edit" in " ".join(c) for c in calls)


def test_set_board_status_still_returns_none(monkeypatch):
    # Pins Requirement 3 / Design Decision 3: set_board_status's 4 direct callers
    # (breaker.py, rescue.py, deconflict.py, epic_autopilot.py) must see zero
    # behavior change even though the helpers it calls now report bool/tuple.
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _items([
        {"id": "ITEM42", "content": {"number": 42, "type": "Issue"}},
    ]) if "item-list" in cmd else _ok())
    assert board.set_board_status(42, "opt_abc") is None


MARKER = "<!-- marker -->"


def _lookup_argv(issue_num=42):
    return ["gh", "api",
            f"repos/{board.OWNER}/{board.REPO}/issues/{issue_num}/comments?per_page=100",
            "--paginate"]


def _comment_recorder(lookup_results):
    """subprocess.run fake: lookup calls (argv contains --paginate) pop the next
    CompletedProcess off lookup_results; every other call returns _ok(). Records argv,
    plus the --body-file contents of any create call (the temp file is unlinked after)."""
    calls, bodies = [], []
    results = list(lookup_results)

    def fake(cmd, **kw):
        calls.append(cmd)
        if "--paginate" in cmd:
            return results.pop(0)
        if cmd[:3] == ["gh", "issue", "comment"]:
            bodies.append(Path(cmd[cmd.index("--body-file") + 1]).read_text())
        return _ok()
    return fake, calls, bodies


def _json_cp(payload, returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=json.dumps(payload), stderr="")


def test_post_or_update_comment_new_comment(monkeypatch):
    fake, calls, bodies = _comment_recorder([_json_cp([{"id": 1, "body": "unrelated"}])])
    monkeypatch.setattr(subprocess, "run", fake)
    board.post_or_update_comment(42, MARKER, "body text")
    assert calls[0] == _lookup_argv()
    assert len(calls) == 2
    create = calls[1]
    assert create[:-1] == ["gh", "issue", "comment", "42",
                           "--repo", f"{board.OWNER}/{board.REPO}", "--body-file"]
    assert create[-1].endswith(".md")
    assert bodies == ["body text"]


def test_post_or_update_comment_updates_existing(monkeypatch):
    fake, calls, _ = _comment_recorder([_json_cp([{"id": 12345, "body": f"{MARKER}\nold"}])])
    monkeypatch.setattr(subprocess, "run", fake)
    board.post_or_update_comment(42, MARKER, "updated body")
    assert len(calls) == 2
    patch = calls[1]
    assert patch[:5] == ["gh", "api", f"repos/{board.OWNER}/{board.REPO}/issues/comments/12345",
                         "--method", "PATCH"]
    assert not any(c[:3] == ["gh", "issue", "comment"] for c in calls)


def test_post_or_update_comment_marker_on_second_page_is_patched_not_duplicated(monkeypatch):
    # gh --paginate merges every page into one array; the newest marker comment sits past
    # index 29 (beyond GitHub's default first page), an older one is on "page 1".
    comments = [{"id": i, "body": f"unrelated {i}"} for i in range(1, 41)]
    comments[3] = {"id": 9003, "body": f"{MARKER}\nolder"}
    comments[31] = {"id": 9031, "body": f"{MARKER}\nnewest"}
    fake, calls, _ = _comment_recorder([_json_cp(comments)])
    monkeypatch.setattr(subprocess, "run", fake)
    board.post_or_update_comment(42, MARKER, "updated body")
    lookup = calls[0]
    assert lookup == _lookup_argv()
    assert "--paginate" in lookup and "--jq" not in lookup
    assert len(calls) == 2
    assert calls[1][2] == f"repos/{board.OWNER}/{board.REPO}/issues/comments/9031"
    assert "PATCH" in calls[1]
    assert not any(c[:3] == ["gh", "issue", "comment"] for c in calls)


def test_post_or_update_comment_tolerates_null_body_and_non_dict_entries(monkeypatch):
    comments = [{"id": 1, "body": None}, "garbage", {"id": 2, "body": f"{MARKER}\nx"}]
    fake, calls, _ = _comment_recorder([_json_cp(comments)])
    monkeypatch.setattr(subprocess, "run", fake)
    board.post_or_update_comment(42, MARKER, "updated body")
    assert calls[1][2] == f"repos/{board.OWNER}/{board.REPO}/issues/comments/2"


def test_post_or_update_comment_lookup_fails_twice_posts_nothing(monkeypatch, capsys):
    fail = subprocess.CompletedProcess([], 1, stdout="", stderr="HTTP 502")
    fake, calls, _ = _comment_recorder([fail, fail])
    monkeypatch.setattr(subprocess, "run", fake)
    board.post_or_update_comment(42, MARKER, "body text")
    assert calls == [_lookup_argv(), _lookup_argv()]
    err = capsys.readouterr().err
    assert "#42" in err and "skipping" in err


def test_post_or_update_comment_lookup_retry_then_success_posts(monkeypatch):
    fail = subprocess.CompletedProcess([], 1, stdout="", stderr="HTTP 502")
    fake, calls, bodies = _comment_recorder([fail, _json_cp([])])
    monkeypatch.setattr(subprocess, "run", fake)
    board.post_or_update_comment(42, MARKER, "body text")
    assert calls[:2] == [_lookup_argv(), _lookup_argv()]
    assert len(calls) == 3
    assert calls[2][:6] == ["gh", "issue", "comment", "42", "--repo", f"{board.OWNER}/{board.REPO}"]
    assert bodies == ["body text"]


@pytest.mark.parametrize("stdout", ["not json", "", '{"message": "Not Found"}', "null"])
def test_post_or_update_comment_unparseable_lookup_posts_nothing(monkeypatch, capsys, stdout):
    bad = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
    fake, calls, _ = _comment_recorder([bad, bad])
    monkeypatch.setattr(subprocess, "run", fake)
    board.post_or_update_comment(42, MARKER, "body text")
    assert calls == [_lookup_argv(), _lookup_argv()]
    assert "skipping" in capsys.readouterr().err



def test_add_to_board_success(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        if "item-add" in cmd:
            return subprocess.CompletedProcess([], 0, stdout=json.dumps({"id": "ITEM42"}), stderr="")
        return _ok()
    monkeypatch.setattr(subprocess, "run", fake)
    assert board.add_to_board(42, "https://github.com/o/r/issues/42") is True
    add_call = next(c for c in calls if "item-add" in c)
    assert "https://github.com/o/r/issues/42" in add_call
    edit_call = next(c for c in calls if "item-edit" in c)
    assert "ITEM42" in edit_call
    assert board.STATUS_BACKLOG in edit_call


def test_add_to_board_item_add_fails_falls_back_to_lookup(monkeypatch, capsys):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        if "item-add" in cmd:
            return subprocess.CompletedProcess([], 1, stdout="", stderr="already exists")
        if "item-list" in cmd:
            return _items([{"id": "ITEM42", "content": {"number": 42, "type": "Issue"}}])
        return _ok()
    monkeypatch.setattr(subprocess, "run", fake)
    assert board.add_to_board(42, "https://github.com/o/r/issues/42") is True
    assert any("item-edit" in c for c in calls)
    err = capsys.readouterr().err
    assert "42" in err


def test_add_to_board_item_add_and_fallback_fail(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        if "item-add" in cmd:
            return subprocess.CompletedProcess([], 1, stdout="", stderr="boom")
        if "item-list" in cmd:
            return subprocess.CompletedProcess([], 1, stdout="", stderr="rate limited")
        return _ok()
    monkeypatch.setattr(subprocess, "run", fake)
    assert board.add_to_board(42, "https://github.com/o/r/issues/42") is False
    assert not any("item-edit" in c for c in calls)
