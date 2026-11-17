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
    result = GitHubTracker().set_status("42", "in_review")
    edit = next(c for c in calls if "item-edit" in c)
    assert edit == [
        "gh", "project", "item-edit",
        "--project-id", identity.PROJECT_ID,
        "--id", "ITEM42",
        "--field-id", identity.STATUS_FIELD,
        "--single-select-option-id", identity.STATUS["in_review"],
    ]
    assert result is True


def test_set_status_opaque_id_never_reaches_int(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout='{"items": []}'))[1])
    result = GitHubTracker().set_status("PROJ-123", "blocked")  # must not raise ValueError from int()
    assert not any("item-edit" in c for c in calls)
    assert result is False


def test_set_status_item_edit_failure_returns_false(monkeypatch):
    def fake(cmd, **kw):
        if "item-list" in cmd:
            return _ok(stdout=json.dumps(
                {"items": [{"id": "ITEM42", "content": {"number": 42, "type": "Issue"}}]}
            ))
        return subprocess.CompletedProcess([], 1, stdout="", stderr="denied")
    monkeypatch.setattr(subprocess, "run", fake)
    assert GitHubTracker().set_status("42", "blocked") is False


def test_add_label_matches_breaker_trip_to_blocked(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    result = GitHubTracker().add_label("42", "needs-discussion")
    assert calls[0] == [
        "gh", "issue", "edit", "42", "--repo", identity.SLUG,
        "--add-label", "needs-discussion",
    ]
    assert result is True


def test_remove_label_matches_scheduler_advance_path(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    result = GitHubTracker().remove_label("42", "spec-pending-review")
    assert calls[0] == [
        "gh", "issue", "edit", "42", "--repo", identity.SLUG,
        "--remove-label", "spec-pending-review",
    ]
    assert result is True


def test_add_label_returns_false_and_prints_stderr_on_gh_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="rate limited"),
    )
    result = GitHubTracker().add_label("42", "needs-discussion")
    assert result is False
    err = capsys.readouterr().err
    assert "42" in err
    assert "needs-discussion" in err
    assert "rate limited" in err


def test_remove_label_returns_false_and_prints_stderr_on_gh_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="rate limited"),
    )
    result = GitHubTracker().remove_label("42", "spec-pending-review")
    assert result is False
    err = capsys.readouterr().err
    assert "42" in err
    assert "spec-pending-review" in err
    assert "rate limited" in err


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


def test_required_env_returns_gh_token():
    assert GitHubTracker.required_env() == ["GH_TOKEN"]


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


# --- #507: append-only comment / comment lookup / board read / label list -----------------

_LEGACY_BOARD_QUERY = (
    # Byte-for-byte what scheduler.sh's pre-#507 fetch_board_items passed to
    # `gh api graphql -f query=...` (captured from the main branch with a gh stub, project
    # id normalised to the default).
    '\n      query {\n        node(id: "PVT_kwHOAAFds84BWh4w") {\n          ... on ProjectV2 {\n'
    '            items(first: 100%s) {\n              pageInfo { hasNextPage endCursor }\n'
    '              nodes {\n                fieldValueByName(name: "Status") {\n'
    '                  ... on ProjectV2ItemFieldSingleSelectValue { name }\n                }\n'
    '                content {\n                  ... on Issue {\n                    number\n'
    '                    title\n                    labels(first: 10) { nodes { name } }\n'
    '                  }\n                }\n              }\n            }\n          }\n'
    '        }\n      }\n    '
)

# Output of the pre-#507 `jq '{items: [...]}'` for the page in _BOARD_PAGE below.
_LEGACY_BOARD_OUTPUT = '''{
  "items": [
    {
      "content": {
        "number": 5,
        "title": "Tïtle é \\"q\\"",
        "type": "Issue"
      },
      "labels": [
        "a"
      ],
      "status": "Ready"
    },
    {
      "content": {
        "number": 6,
        "title": null,
        "type": "Issue"
      },
      "labels": [],
      "status": "Done"
    }
  ]
}'''

_BOARD_PAGE = {"data": {"node": {"items": {
    "pageInfo": {"hasNextPage": False, "endCursor": None},
    "nodes": [
        {"fieldValueByName": {"name": "Ready"},
         "content": {"number": 5, "title": "Tïtle é \"q\"", "labels": {"nodes": [{"name": "a"}]}}},
        {"fieldValueByName": None, "content": {}},
        {"fieldValueByName": {"name": "Done"},
         "content": {"number": 6, "title": None, "labels": {"nodes": []}}},
    ],
}}}}


def test_add_comment_argv_matches_scheduler_and_entrypoint_posts(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    body = "## Dark Factory — CI Failing\n\nmulti-line `body` with ${NOT_EXPANDED}\n---\nfooter"
    assert GitHubTracker().add_comment("42", body) is True
    assert calls == [["gh", "issue", "comment", "42", "--repo", identity.SLUG, "--body", body]]


def test_add_comment_failure_returns_false_and_forwards_stderr(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, stdout="", stderr="gh: HTTP 403\n"))
    assert GitHubTracker().add_comment("42", "b") is False
    assert capsys.readouterr().err == "gh: HTTP 403\n"


def test_find_comment_argv_matches_entrypoint_cost_report_lookup(monkeypatch):
    calls = []

    def fake(cmd, **kw):
        calls.append(cmd)
        return _ok(stdout="777\n" if cmd[-1] != ".body" else "prior body\nline2\n")
    monkeypatch.setattr(subprocess, "run", fake)
    marker = "<!-- dark-factory-cost-report -->"
    found = GitHubTracker().find_comment("42", marker)
    assert calls == [
        ["gh", "api", f"repos/{identity.SLUG}/issues/42/comments", "--jq",
         '[.[] | select(.body | contains("%s"))] | last | .id // empty' % marker],
        ["gh", "api", f"repos/{identity.SLUG}/issues/comments/777", "--jq", ".body"],
    ]
    assert found == {"id": "777", "body": "prior body\nline2\n"}


def test_find_comment_none_when_absent_or_lookup_fails_and_skips_body_fetch(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout=""))[1])
    assert GitHubTracker().find_comment("42", "m") is None
    assert len(calls) == 1
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout="777", returncode=1))
    assert GitHubTracker().find_comment("42", "m") is None


def test_find_comment_body_fetch_failure_yields_empty_body_like_old_or_true(monkeypatch):
    def fake(cmd, **kw):
        return _ok(stdout="777") if cmd[-1] != ".body" else _ok(stdout="partial", returncode=1)
    monkeypatch.setattr(subprocess, "run", fake)
    assert GitHubTracker().find_comment("42", "m") == {"id": "777", "body": ""}


def test_update_comment_argv_matches_entrypoint_patch(monkeypatch):
    seen = {}

    def fake(cmd, **kw):
        seen["cmd"] = list(cmd)
        seen["file_body"] = Path(cmd[cmd.index("-F") + 1].removeprefix("body=@")).read_text(encoding="utf-8")
        return _ok()
    monkeypatch.setattr(subprocess, "run", fake)
    assert GitHubTracker().update_comment("42", "777", "new — body\n") is True
    cmd = seen["cmd"]
    assert cmd[:5] == ["gh", "api", f"repos/{identity.SLUG}/issues/comments/777", "--method", "PATCH"]
    assert cmd[5] == "-F" and cmd[6].startswith("body=@") and len(cmd) == 7
    assert seen["file_body"] == "new — body\n"
    assert not Path(cmd[6].removeprefix("body=@")).exists()  # temp file cleaned up


def test_update_comment_failure_returns_false(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(returncode=1))
    assert GitHubTracker().update_comment("42", "777", "b") is False


def test_list_board_items_query_matches_pre_507_scheduler_byte_for_byte(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout=json.dumps(_BOARD_PAGE)))[1])
    GitHubTracker().list_board_items()
    assert calls == [["gh", "api", "graphql", "-f", "query=" + _LEGACY_BOARD_QUERY % ""]]


def test_list_board_items_pages_with_cursor_one_call_per_page(monkeypatch):
    calls = []
    page1 = {"data": {"node": {"items": {"pageInfo": {"hasNextPage": True, "endCursor": "CUR1"}, "nodes": [
        {"fieldValueByName": {"name": "Backlog"}, "content": {"number": 1, "title": "a", "labels": {"nodes": []}}}]}}}}
    page2 = {"data": {"node": {"items": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [
        {"fieldValueByName": {"name": "Backlog"}, "content": {"number": 2, "title": "b", "labels": {"nodes": []}}}]}}}}

    def fake(cmd, **kw):
        calls.append(cmd)
        return _ok(stdout=json.dumps(page2 if 'after: "CUR1"' in cmd[-1] else page1))
    monkeypatch.setattr(subprocess, "run", fake)
    out = GitHubTracker().list_board_items()
    assert [i["content"]["number"] for i in out["items"]] == [1, 2]
    assert [c[-1] for c in calls] == [
        "query=" + _LEGACY_BOARD_QUERY % "", "query=" + _LEGACY_BOARD_QUERY % ', after: "CUR1"']


def test_board_items_cli_output_is_byte_identical_to_pre_507_jq_output(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout=json.dumps(_BOARD_PAGE)))
    monkeypatch.setattr(cli_mod, "get_tracker", lambda: GitHubTracker())
    monkeypatch.setattr(sys, "argv", ["cli.py", "tracker", "board-items"])
    cli_mod.main()
    assert capsys.readouterr().out == _LEGACY_BOARD_OUTPUT + "\n"


def test_list_board_items_graphql_error_body_means_empty_board_like_old_jq_default(monkeypatch):
    body = json.dumps({"data": None, "errors": [{"message": "rate limited"}]})
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout=body, returncode=1))
    assert GitHubTracker().list_board_items() == {"items": []}


def test_list_board_items_unparsable_body_raises(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout="", returncode=1))
    with pytest.raises(RuntimeError):
        GitHubTracker().list_board_items()


def test_list_board_items_next_page_without_cursor_raises_like_old_return_1(monkeypatch):
    body = json.dumps({"data": {"node": {"items": {
        "pageInfo": {"hasNextPage": True, "endCursor": None}, "nodes": []}}}})
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout=body))
    with pytest.raises(RuntimeError, match="no cursor"):
        GitHubTracker().list_board_items()


def test_list_items_argv_matches_pre_507_off_board_lookup(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout='[{"number":101},{"number":102}]\n'))[1])
    assert GitHubTracker().list_items("ready-for-agent", limit=200) == ["101", "102"]
    assert calls == [[
        "gh", "issue", "list", "--repo", identity.SLUG, "--state", "open",
        "--label", "ready-for-agent", "--limit", "200", "--json", "number",
    ]]


def test_list_items_cli_prints_exactly_what_gh_printed(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    gh_stdout = '[{"number":101},{"number":102}]'
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout=gh_stdout + "\n"))
    monkeypatch.setattr(cli_mod, "get_tracker", lambda: GitHubTracker())
    monkeypatch.setattr(sys, "argv", ["cli.py", "tracker", "list-items", "--label", "ready-for-agent", "--limit", "200"])
    cli_mod.main()
    assert capsys.readouterr().out == gh_stdout + "\n"


def test_list_items_empty_result_is_empty_array(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout="[]\n"))
    monkeypatch.setattr(cli_mod, "get_tracker", lambda: GitHubTracker())
    monkeypatch.setattr(sys, "argv", ["cli.py", "tracker", "list-items", "--label", "x"])
    cli_mod.main()
    assert capsys.readouterr().out == "[]\n"


def test_list_items_gh_failure_or_garbage_raises_and_cli_exits_1(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    monkeypatch.setattr(cli_mod, "get_tracker", lambda: GitHubTracker())
    monkeypatch.setattr(sys, "argv", ["cli.py", "tracker", "list-items", "--label", "x"])
    for result in (_ok(stdout="", returncode=1), _ok(stdout="not json"), _ok(stdout='{"a":1}')):
        monkeypatch.setattr(subprocess, "run", lambda cmd, r=result, **kw: r)
        with pytest.raises(SystemExit) as exc:
            cli_mod.main()
        assert exc.value.code == 1
    assert capsys.readouterr().out == ""
