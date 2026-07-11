import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

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


def test_get_item_custom_single_field_matches_run_dag_state_check(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        return _ok(stdout=json.dumps({"state": "OPEN"}))
    monkeypatch.setattr(subprocess, "run", fake)
    GitHubTracker().get_item("42", fields=("state",))
    assert calls[0] == [
        "gh", "issue", "view", "42", "--repo", identity.SLUG,
        "--json", "state",
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


def test_upsert_comment_delegates_to_board_post_or_update_comment(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        return _ok()
    monkeypatch.setattr(subprocess, "run", fake)
    GitHubTracker().upsert_comment("42", "<!-- marker -->", "body text")
    assert any("issue" in c and "comment" in c for c in calls)


def test_upsert_comment_opaque_id_passthrough(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubTracker().upsert_comment("PROJ-123", "<!-- marker -->", "body")
    lookup = calls[0]
    assert f"repos/{identity.OWNER}/{identity.REPO}/issues/PROJ-123/comments" in lookup


def test_create_item_matches_smoke_gate_regression_ticket(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        return _ok(stdout=f"https://github.com/{identity.SLUG}/issues/77\n")
    monkeypatch.setattr(subprocess, "run", fake)
    new_id = GitHubTracker().create_item(
        title="main is red: tsc/python import failure",
        body="failure body",
        labels=["regression"],
    )
    assert calls[0] == [
        "gh", "issue", "create", "--repo", identity.SLUG,
        "--label", "regression",
        "--title", "main is red: tsc/python import failure",
        "--body", "failure body",
    ]
    assert new_id == "77"


def test_resolve_item_matches_smoke_gate_close(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubTracker().resolve_item("77", comment="main smoke gate passed — closing regression ticket.")
    assert calls[0] == [
        "gh", "issue", "close", "77", "--repo", identity.SLUG,
        "--comment", "main smoke gate passed — closing regression ticket.",
    ]


def test_list_work_items_single_page_query_matches_scheduler_fetch_board_items(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        return _ok(stdout=json.dumps({
            "data": {"node": {"items": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [
                    {"fieldValueByName": {"name": "Ready"},
                     "content": {"number": 42, "title": "t", "labels": {"nodes": [{"name": "ready-for-agent"}]}}},
                    {"fieldValueByName": {"name": "Done"},
                     "content": {"number": 43, "title": "t2", "labels": {"nodes": []}}},
                ],
            }}}
        }))
    monkeypatch.setattr(subprocess, "run", fake)
    items = GitHubTracker().list_work_items(["ready"])
    expected_query = (
        '\n      query {\n        node(id: "' + identity.PROJECT_ID + '") {\n'
        '          ... on ProjectV2 {\n            items(first: 100) {\n'
        '              pageInfo { hasNextPage endCursor }\n              nodes {\n'
        '                fieldValueByName(name: "Status") {\n'
        '                  ... on ProjectV2ItemFieldSingleSelectValue { name }\n                }\n'
        '                content {\n                  ... on Issue {\n                    number\n'
        '                    title\n                    labels(first: 10) { nodes { name } }\n'
        '                  }\n                }\n              }\n            }\n          }\n        }\n      }\n    '
    )
    assert calls[0] == ["gh", "api", "graphql", "-f", "query=" + expected_query]
    assert [i["id"] for i in items] == ["42"]
    assert items[0]["status"] == "ready"


def test_list_work_items_filters_by_label(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout=json.dumps({
        "data": {"node": {"items": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [
                {"fieldValueByName": {"name": "Ready"},
                 "content": {"number": 1, "title": "a", "labels": {"nodes": [{"name": "ready-for-agent"}]}}},
                {"fieldValueByName": {"name": "Ready"},
                 "content": {"number": 2, "title": "b", "labels": {"nodes": []}}},
            ],
        }}}
    })))
    items = GitHubTracker().list_work_items(["ready"], labels=["ready-for-agent"])
    assert [i["id"] for i in items] == ["1"]


def test_get_status_limits_query_matches_scheduler_fetch_wip_limits(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        options = [
            {"id": identity.STATUS["in_progress"], "name": "In progress", "description": "limit: 3"},
            {"id": identity.STATUS["in_review"], "name": "In review", "description": "no limit token here"},
        ]
        return _ok(stdout=json.dumps({"data": {"node": {"field": {"options": options}}}}))
    monkeypatch.setattr(subprocess, "run", fake)
    limits = GitHubTracker().get_status_limits()
    # Transcribed verbatim (incl. whitespace) from scheduler.sh:591-608's fetch_wip_limits
    # heredoc, with $FACTORY_PROJECT_ID substituted — byte/argv equality, not substring checks.
    expected_query = (
        '\n    query {\n      node(id: "' + identity.PROJECT_ID + '") {\n'
        '        ... on ProjectV2 {\n          field(name: "Status") {\n'
        '            ... on ProjectV2SingleSelectField {\n'
        '              options { id name description }\n            }\n          }\n'
        '        }\n      }\n    }\n  '
    )
    assert calls[0] == ["gh", "api", "graphql", "-f", "query=" + expected_query]
    assert limits["in_progress"] == 3
    assert limits["in_review"] == 999  # scheduler.sh's documented fallback


def test_get_rate_budget_matches_scheduler_check_rate_limit(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        return _ok(stdout=json.dumps({"remaining": 150, "reset": 1999999999, "used": 4850, "limit": 5000}))
    monkeypatch.setattr(subprocess, "run", fake)
    budget = GitHubTracker().get_rate_budget()
    assert calls[0] == ["gh", "api", "rate_limit", "--jq", ".resources.graphql"]
    assert budget == {"remaining": 150, "reset": 1999999999, "used": 4850, "limit": 5000}


def test_get_rate_budget_raises_on_gh_failure(monkeypatch):
    # A failed `gh api rate_limit` call must be observable (nonzero CLI exit) rather
    # than silently degrading to a null budget with exit 0 — see issue #249 follow-up:
    # callers' `|| return 0` / `|| BUDGET="?"` guards only fire on a real failure signal.
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout="", returncode=1))
    with pytest.raises(RuntimeError):
        GitHubTracker().get_rate_budget()


_TRACKER_ID_CASES = ["42", "PROJ-123"]


@pytest.mark.parametrize("opaque_id", _TRACKER_ID_CASES)
def test_tracker_methods_pass_opaque_id_through_unchanged(monkeypatch, opaque_id):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="{}"))[1])
    GitHubTracker().get_item(opaque_id)
    assert opaque_id in calls[-1]

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="[]"))[1])
    GitHubTracker().get_comments(opaque_id)
    assert opaque_id in calls[-1]

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="{}"))[1])
    GitHubTracker().get_children(opaque_id)
    assert opaque_id in calls[-1][4]  # embedded in the GraphQL query string, not a separate argv element

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubTracker().add_label(opaque_id, "some-label")
    assert opaque_id in calls[-1]

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubTracker().remove_label(opaque_id, "some-label")
    assert opaque_id in calls[-1]

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubTracker().resolve_item(opaque_id)
    assert opaque_id in calls[-1]
