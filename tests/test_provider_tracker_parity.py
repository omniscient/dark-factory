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
