import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from factory_core import identity
from factory_core.providers.tracker.github import GitHubTracker


def _ok(stdout="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def test_get_item_default_fields_matches_run_dag_fetch_issue_node(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        return _ok(stdout=json.dumps({"title": "t", "body": "b", "labels": [], "comments": []}))
    monkeypatch.setattr(subprocess, "run", fake)
    GitHubTracker().get_item("42")
    assert calls[0] == [
        "gh", "issue", "view", "42", "--repo", identity.SLUG,
        "--json", "title,body,labels,comments",
    ]


def test_get_comments_matches_scheduler_get_new_comments(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        return _ok(stdout=json.dumps([]))
    monkeypatch.setattr(subprocess, "run", fake)
    GitHubTracker().get_comments("42")
    assert calls[0] == [
        "gh", "issue", "view", "42", "--repo", identity.SLUG,
        "--json", "comments", "-q", ".comments",
    ]


def test_get_children_matches_epic_autopilot_sub_issue_numbers(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        return _ok(stdout=json.dumps({"data": {"repository": {"issue": {"subIssues": {"nodes": []}}}}}))
    monkeypatch.setattr(subprocess, "run", fake)
    GitHubTracker().get_children("1")
    expected_query = (
        'query { repository(owner:"%s", name:"%s") { issue(number:1) { '
        'subIssues(first:50) { nodes { number state labels(first:20){nodes{name}} } } } } }'
        % (identity.OWNER, identity.REPO)
    )
    assert calls[0] == ["gh", "api", "graphql", "-f", "query=" + expected_query]


def test_set_status_resolves_canonical_and_calls_item_edit(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        if "item-list" in cmd:
            return _ok(stdout=json.dumps(
                {"items": [{"id": "ITEM42", "content": {"number": 42, "type": "Issue"}}]}
            ))
        return _ok()
    monkeypatch.setattr(subprocess, "run", fake)
    GitHubTracker().set_status("42", "in_review")
    edit = next(c for c in calls if "item-edit" in c)
    assert edit == [
        "gh", "project", "item-edit",
        "--project-id", identity.PROJECT_ID,
        "--id", "ITEM42",
        "--field-id", identity.STATUS_FIELD,
        "--single-select-option-id", identity.STATUS["in_review"],
    ]


def test_set_status_opaque_id_never_reaches_int(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout='{"items": []}'))[1])
    GitHubTracker().set_status("PROJ-123", "blocked")  # must not raise ValueError from int()
    assert not any("item-edit" in c for c in calls)


def test_add_label_matches_breaker_trip_to_blocked(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubTracker().add_label("42", "needs-discussion")
    assert calls[0] == [
        "gh", "issue", "edit", "42", "--repo", identity.SLUG,
        "--add-label", "needs-discussion",
    ]


def test_remove_label_matches_scheduler_advance_path(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubTracker().remove_label("42", "spec-pending-review")
    assert calls[0] == [
        "gh", "issue", "edit", "42", "--repo", identity.SLUG,
        "--remove-label", "spec-pending-review",
    ]
