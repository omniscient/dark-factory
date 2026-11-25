"""#607: batched dependency-state reads — one query for the states of all
referenced issues instead of one read per dependency.

Contract (Tracker.get_states): mapping id -> "OPEN"/"CLOSED", or None when the
item does not exist or its state is unknown. Callers treat None as unmet
(fail closed). The base default is the degradable per-id get_item read loop —
a conforming implementation for non-GitHub providers; GitHub overrides it with
one aliased GraphQL query per chunk.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from factory_core import identity
from factory_core.providers.tracker.base import Tracker
from factory_core.providers.tracker.github import GitHubTracker


def _ok(stdout="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def _states_body(mapping):
    """GraphQL response for a chunk: aliases i0.. in the repository object."""
    return json.dumps({"data": {"rateLimit": {"cost": 1, "remaining": 1, "resetAt": "r"},
                                "repository": mapping}})


# --- Contract: degradable default (non-GitHub providers) -----------------------

def test_base_get_states_defaults_to_per_id_get_item_reads():
    # The defined contract for non-GitHub trackers: a degradable per-id read loop
    # is conforming (Jira keeps its existing cost profile; its REST budget is
    # separate from GitHub's GraphQL one).
    class _Bare(Tracker):
        def list_work_items(self, statuses, labels=None): return []
        def get_item(self, id, fields=None):
            return {"state": "CLOSED" if id in ("200",) else "OPEN"} if fields == ("state",) else {}
        def get_comments(self, id): return []
        def get_children(self, epic_id): return []
        def set_status(self, id, canonical): pass
        def add_label(self, id, name): pass
        def remove_label(self, id, name): pass
        def upsert_comment(self, id, marker, body): pass
        def add_comment(self, id, body): return True
        def find_comment(self, id, marker): return None
        def update_comment(self, id, comment_id, body): return True
        def list_board_items(self): return {"items": []}
        def list_items(self, label, limit=200): return []
        def create_item(self, title, body, labels=None): return "1"
        def resolve_item(self, id): pass

    bare = _Bare()
    assert bare.get_states(["200", "201"]) == {"200": "CLOSED", "201": "OPEN"}


def test_base_get_states_maps_get_item_failure_to_none_unmet():
    class _Failing(Tracker):
        def list_work_items(self, statuses, labels=None): return []
        def get_item(self, id, fields=None): raise RuntimeError("transport")
        def get_comments(self, id): return []
        def get_children(self, epic_id): return []
        def set_status(self, id, canonical): pass
        def add_label(self, id, name): pass
        def remove_label(self, id, name): pass
        def upsert_comment(self, id, marker, body): pass
        def add_comment(self, id, body): return True
        def find_comment(self, id, marker): return None
        def update_comment(self, id, comment_id, body): return True
        def list_board_items(self): return {"items": []}
        def list_items(self, label, limit=200): return []
        def create_item(self, title, body, labels=None): return "1"
        def resolve_item(self, id): pass

    assert _Failing().get_states(["200"]) == {"200": None}  # fail closed: unmet


def test_base_get_states_empty_list_is_empty_mapping():
    class _Bare(Tracker):
        def list_work_items(self, statuses, labels=None): return []
        def get_item(self, id, fields=None): return {}
        def get_comments(self, id): return []
        def get_children(self, epic_id): return []
        def set_status(self, id, canonical): pass
        def add_label(self, id, name): pass
        def remove_label(self, id, name): pass
        def upsert_comment(self, id, marker, body): pass
        def add_comment(self, id, body): return True
        def find_comment(self, id, marker): return None
        def update_comment(self, id, comment_id, body): return True
        def list_board_items(self): return {"items": []}
        def list_items(self, label, limit=200): return []
        def create_item(self, title, body, labels=None): return "1"
        def resolve_item(self, id): pass

    assert _Bare().get_states([]) == {}


# --- GitHub: one aliased query, validated, fail closed --------------------------

def test_github_get_states_single_query_for_all_ids(monkeypatch):
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        return _ok(stdout=_states_body({"i0": {"state": "CLOSED"}, "i1": {"state": "OPEN"}}))
    monkeypatch.setattr(subprocess, "run", fake)
    out = GitHubTracker().get_states(["200", "201"])
    assert out == {"200": "CLOSED", "201": "OPEN"}
    assert len(calls) == 1  # one query, not one read per dependency
    query = calls[0][-1]
    assert "issue(number:200)" in query and "issue(number:201)" in query
    assert "repository(owner:%s" % identity.OWNER in query or \
           ('owner:"%s"' % identity.OWNER) in query


def test_github_get_states_validates_ids_before_interpolation(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd),
        _ok(stdout=_states_body({"i0": {"state": "CLOSED"}})))[1])
    out = GitHubTracker().get_states(["200", "1; DROP", "7 } } }", ""])
    # invalid ids are never interpolated; they read as unmet (None), not "met"
    assert out == {"200": "CLOSED", "1; DROP": None, "7 } } }": None, "": None}
    query = calls[0][-1]
    assert "DROP" not in query and "issue(number:7" not in query and "issue(number:)" not in query
    assert "issue(number:200)" in query


def test_github_get_states_null_issue_means_unmet(monkeypatch):
    # A nonexistent issue returns null for its alias — a missing issue is unmet.
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(
        stdout=_states_body({"i0": None, "i1": {"state": "CLOSED"}})))
    out = GitHubTracker().get_states(["999999", "200"])
    assert out == {"999999": None, "200": "CLOSED"}


def test_github_get_states_chunks_beyond_one_query(monkeypatch):
    import re as _re
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        aliases = _re.findall(r"(i\d+): issue\(number:\d+\)", cmd[-1])
        return _ok(stdout=_states_body({a: {"state": "OPEN"} for a in aliases}))
    monkeypatch.setattr(subprocess, "run", fake)
    ids = [str(1000 + i) for i in range(60)]  # > chunk size → multiple queries
    out = GitHubTracker().get_states(ids)
    assert len(out) == 60
    assert all(v == "OPEN" for v in out.values())
    assert len(calls) >= 2  # 60 ids never fit one query


def test_github_get_states_gh_failure_raises(monkeypatch):
    # Fail closed at the boundary: a refused/failed call raises so the CLI exits
    # non-zero and the scheduler treats every dependency as unmet.
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout="", returncode=1))
    with pytest.raises(RuntimeError):
        GitHubTracker().get_states(["200"])


def test_github_get_states_empty_list_makes_no_call(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    assert GitHubTracker().get_states([]) == {}
    assert calls == []


def test_github_get_states_logs_cost_per_chunk(monkeypatch, tmp_path):
    from factory_core import cost_log
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("DARK_FACTORY_COST_SOURCE", "scheduler")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout=_states_body(
        {"i0": {"state": "CLOSED"}, "i1": {"state": "OPEN"}})))
    GitHubTracker().get_states(["200", "201"])
    lines = (tmp_path / cost_log.LOG_NAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["context"] == "issue_states"


# --- CLI: tracker states --------------------------------------------------------

def test_tracker_states_cli_prints_json_mapping(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    monkeypatch.setattr(cli_mod, "get_tracker", lambda: GitHubTracker())
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout=_states_body(
        {"i0": {"state": "CLOSED"}})))
    monkeypatch.setattr(sys, "argv", ["cli.py", "tracker", "states", "--ids", "200"])
    cli_mod.main()
    assert json.loads(capsys.readouterr().out) == {"200": "CLOSED"}


def test_tracker_states_cli_exit_1_on_failure(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod

    class _Boom(GitHubTracker):
        def get_states(self, ids):
            raise RuntimeError("refused")
    monkeypatch.setattr(cli_mod, "get_tracker", lambda: _Boom())
    monkeypatch.setattr(sys, "argv", ["cli.py", "tracker", "states", "--ids", "200"])
    with pytest.raises(SystemExit) as e:
        cli_mod.main()
    assert e.value.code == 1
    assert "ERROR" in capsys.readouterr().err
