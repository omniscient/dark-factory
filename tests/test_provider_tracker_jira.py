import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jira"


def _set_jira_env(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "PROJ")
    monkeypatch.setenv("JIRA_TOKEN", "secret-pat")
    monkeypatch.setenv("JIRA_EPIC_LINK_FIELD", "customfield_10008")


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_request_sends_bearer_auth_and_parses_json(monkeypatch):
    from factory_core.providers.tracker.jira import JiraTracker

    _set_jira_env(monkeypatch)
    captured = {}

    def fake_urlopen(req, *a, **kw):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["auth"] = req.get_header("Authorization")
        return _FakeResponse(json.dumps({"ok": True}).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = JiraTracker()._request("GET", "/issue/PROJ-1")
    assert captured["url"] == "https://jira.example.com/rest/api/2/issue/PROJ-1"
    assert captured["method"] == "GET"
    assert captured["auth"] == "Bearer secret-pat"
    assert result == {"ok": True}


def test_request_encodes_params_and_json_body(monkeypatch):
    from factory_core.providers.tracker.jira import JiraTracker

    _set_jira_env(monkeypatch)
    captured = {}

    def fake_urlopen(req, *a, **kw):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = json.loads(req.data.decode("utf-8")) if req.data else None
        return _FakeResponse(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    JiraTracker()._request(
        "POST", "/search", params={"jql": "project=PROJ"}, json_body={"x": 1}
    )
    assert captured["url"] == "https://jira.example.com/rest/api/2/search?jql=project%3DPROJ"
    assert captured["method"] == "POST"
    assert captured["body"] == {"x": 1}


def test_request_raises_runtime_error_on_http_error(monkeypatch):
    import io

    from factory_core.providers.tracker.jira import JiraTracker

    _set_jira_env(monkeypatch)

    def fake_urlopen(req, *a, **kw):
        raise urllib.error.HTTPError(
            req.full_url, 404, "Not Found", hdrs=None,
            fp=io.BytesIO(b'{"errorMessages":["No issue"]}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="No issue"):
        JiraTracker()._request("GET", "/issue/PROJ-999")


def test_required_env_lists_the_four_jira_vars(monkeypatch):
    from factory_core.providers.tracker.jira import JiraTracker

    assert JiraTracker.required_env() == [
        "JIRA_BASE_URL", "JIRA_PROJECT_KEY", "JIRA_TOKEN", "JIRA_EPIC_LINK_FIELD",
    ]


def test_status_maps_use_env_override_and_default(monkeypatch):
    from factory_core.providers.tracker.jira import JiraTracker

    _set_jira_env(monkeypatch)
    monkeypatch.setenv("FACTORY_STATUS_IN_PROGRESS", "Doing")
    tracker = JiraTracker()
    assert tracker._canonical_to_name["in_progress"] == "Doing"
    assert tracker._canonical_to_name["ready"] == "Ready"  # unset -> default
    assert tracker._name_to_canonical["doing"] == "in_progress"  # case-insensitive


def test_list_work_items_builds_jql_with_project_status_and_labels(monkeypatch):
    from factory_core.providers.tracker.jira import JiraTracker

    _set_jira_env(monkeypatch)
    tracker = JiraTracker()
    calls = []

    def fake_request(self, method, path, params=None, json_body=None):
        calls.append((method, path, params))
        return json.loads((FIXTURES / "search_result.json").read_text())

    monkeypatch.setattr(JiraTracker, "_request", fake_request)
    items = tracker.list_work_items(["ready"], labels=["ready-for-agent"])

    assert calls[0][0] == "GET"
    assert calls[0][1] == "/search"
    jql = calls[0][2]["jql"]
    assert 'project=PROJ' in jql
    assert 'status IN("Ready")' in jql
    assert 'labels = "ready-for-agent"' in jql
    assert [i["id"] for i in items] == ["PROJ-1", "PROJ-2"]
    assert items[0]["status"] == "ready"
    assert items[1]["status"] == "done"


def test_get_item_maps_summary_labels_and_status(monkeypatch):
    from factory_core.providers.tracker.jira import JiraTracker

    _set_jira_env(monkeypatch)
    tracker = JiraTracker()
    calls = []

    def fake_request(self, method, path, params=None, json_body=None):
        calls.append((method, path))
        return json.loads((FIXTURES / "issue.json").read_text())

    monkeypatch.setattr(JiraTracker, "_request", fake_request)
    item = tracker.get_item("PROJ-1")

    assert calls[0] == ("GET", "/issue/PROJ-1")
    assert item["title"] == "First ticket"
    assert item["body"] == "Ticket body text."
    assert item["labels"] == ["ready-for-agent"]
    assert item["status"] == "ready"


def test_get_comments_reads_comment_subresource(monkeypatch):
    from factory_core.providers.tracker.jira import JiraTracker

    _set_jira_env(monkeypatch)
    tracker = JiraTracker()
    calls = []

    def fake_request(self, method, path, params=None, json_body=None):
        calls.append((method, path))
        return json.loads((FIXTURES / "comments.json").read_text())

    monkeypatch.setattr(JiraTracker, "_request", fake_request)
    comments = tracker.get_comments("PROJ-1")

    assert calls[0] == ("GET", "/issue/PROJ-1/comment")
    assert comments[0]["body"] == "<!-- factory:refinement -->\nOriginal comment body."


def test_get_children_builds_epic_link_cf_jql_and_returns_neutral_shape(monkeypatch):
    from factory_core.providers.tracker.jira import JiraTracker

    _set_jira_env(monkeypatch)
    tracker = JiraTracker()
    calls = []

    def fake_request(self, method, path, params=None, json_body=None):
        calls.append((method, path, params))
        return json.loads((FIXTURES / "epic_children.json").read_text())

    monkeypatch.setattr(JiraTracker, "_request", fake_request)
    children = tracker.get_children("PROJ-5")

    assert calls[0][1] == "/search"
    jql = calls[0][2]["jql"]
    assert 'cf[10008] = "PROJ-5"' in jql
    assert 'project=PROJ' in jql
    # Adapter-neutral shape (Requirement 7) -- not GitHub's raw GraphQL envelope.
    assert children == [
        {"id": "PROJ-10", "status": "ready", "labels": ["ready-for-agent"]},
        {"id": "PROJ-11", "status": "in_progress", "labels": []},
    ]


def test_get_children_empty_when_no_children(monkeypatch):
    from factory_core.providers.tracker.jira import JiraTracker

    _set_jira_env(monkeypatch)
    tracker = JiraTracker()
    monkeypatch.setattr(JiraTracker, "_request", lambda self, *a, **kw: {"issues": []})
    assert tracker.get_children("PROJ-999") == []


def test_set_status_finds_transition_and_posts_its_id(monkeypatch):
    from factory_core.providers.tracker.jira import JiraTracker

    _set_jira_env(monkeypatch)
    tracker = JiraTracker()
    calls = []

    def fake_request(self, method, path, params=None, json_body=None):
        calls.append((method, path, json_body))
        if method == "GET":
            return json.loads((FIXTURES / "transitions.json").read_text())
        return {}

    monkeypatch.setattr(JiraTracker, "_request", fake_request)
    tracker.set_status("PROJ-1", "in_review")

    get_call, post_call = calls
    assert get_call[:2] == ("GET", "/issue/PROJ-1/transitions")
    assert post_call[:2] == ("POST", "/issue/PROJ-1/transitions")
    assert post_call[2] == {"transition": {"id": "41"}}  # "Send to Review" -> In review


def test_set_status_missing_transition_edge_fails_soft(monkeypatch, capsys):
    from factory_core.providers.tracker.jira import JiraTracker

    _set_jira_env(monkeypatch)
    tracker = JiraTracker()
    calls = []

    def fake_request(self, method, path, params=None, json_body=None):
        calls.append(method)
        return {"transitions": []}  # no edges available

    monkeypatch.setattr(JiraTracker, "_request", fake_request)
    tracker.set_status("PROJ-1", "in_review")  # must not raise

    assert calls == ["GET"]  # no POST attempted
    err = capsys.readouterr().err
    assert "jira:" in err
    assert "PROJ-1" in err
    assert "in review" in err.lower() or "In review" in err


def test_add_label_reads_then_puts_merged_labels(monkeypatch):
    from factory_core.providers.tracker.jira import JiraTracker

    _set_jira_env(monkeypatch)
    tracker = JiraTracker()
    calls = []

    def fake_request(self, method, path, params=None, json_body=None):
        calls.append((method, path, params, json_body))
        if method == "GET":
            return {"fields": {"labels": ["existing-label"]}}
        return {}

    monkeypatch.setattr(JiraTracker, "_request", fake_request)
    tracker.add_label("PROJ-1", "needs-discussion")

    get_call, put_call = calls
    assert get_call[:3] == ("GET", "/issue/PROJ-1", {"fields": "labels"})
    assert put_call[0] == "PUT"
    assert put_call[1] == "/issue/PROJ-1"
    assert set(put_call[3]["fields"]["labels"]) == {"existing-label", "needs-discussion"}


def test_remove_label_reads_then_puts_without_it(monkeypatch):
    from factory_core.providers.tracker.jira import JiraTracker

    _set_jira_env(monkeypatch)
    tracker = JiraTracker()
    calls = []

    def fake_request(self, method, path, params=None, json_body=None):
        calls.append((method, json_body))
        if method == "GET":
            return {"fields": {"labels": ["spec-pending-review", "keep-me"]}}
        return {}

    monkeypatch.setattr(JiraTracker, "_request", fake_request)
    tracker.remove_label("PROJ-1", "spec-pending-review")

    _, put_call = calls
    assert put_call[1]["fields"]["labels"] == ["keep-me"]


def test_upsert_comment_creates_when_marker_absent(monkeypatch):
    from factory_core.providers.tracker.jira import JiraTracker

    _set_jira_env(monkeypatch)
    tracker = JiraTracker()
    calls = []

    def fake_request(self, method, path, params=None, json_body=None):
        calls.append((method, path, json_body))
        if method == "GET":
            return {"comments": []}
        return {}

    monkeypatch.setattr(JiraTracker, "_request", fake_request)
    tracker.upsert_comment("PROJ-1", "<!-- marker -->", "<!-- marker -->\nbody")

    get_call, post_call = calls
    assert get_call[:2] == ("GET", "/issue/PROJ-1/comment")
    assert post_call[:2] == ("POST", "/issue/PROJ-1/comment")
    assert post_call[2] == {"body": "<!-- marker -->\nbody"}


def test_upsert_comment_updates_in_place_when_marker_present(monkeypatch):
    from factory_core.providers.tracker.jira import JiraTracker

    _set_jira_env(monkeypatch)
    tracker = JiraTracker()
    calls = []

    def fake_request(self, method, path, params=None, json_body=None):
        calls.append((method, path, json_body))
        if method == "GET":
            return json.loads((FIXTURES / "comments.json").read_text())
        return {}

    monkeypatch.setattr(JiraTracker, "_request", fake_request)
    tracker.upsert_comment("PROJ-1", "<!-- factory:refinement -->", "<!-- factory:refinement -->\nupdated")

    get_call, put_call = calls
    assert put_call[:2] == ("PUT", "/issue/PROJ-1/comment/501")
    assert put_call[2] == {"body": "<!-- factory:refinement -->\nupdated"}


def test_create_item_posts_issue_and_returns_key(monkeypatch):
    from factory_core.providers.tracker.jira import JiraTracker

    _set_jira_env(monkeypatch)
    tracker = JiraTracker()
    calls = []

    def fake_request(self, method, path, params=None, json_body=None):
        calls.append((method, path, json_body))
        return {"key": "PROJ-77"}

    monkeypatch.setattr(JiraTracker, "_request", fake_request)
    new_id = tracker.create_item(
        title="main is red: tsc/python import failure", body="failure body", labels=["regression"],
    )

    method, path, body = calls[0]
    assert (method, path) == ("POST", "/issue")
    assert body["fields"]["project"] == {"key": "PROJ"}
    assert body["fields"]["summary"] == "main is red: tsc/python import failure"
    assert body["fields"]["description"] == "failure body"
    assert body["fields"]["labels"] == ["regression"]
    assert body["fields"]["issuetype"] == {"name": "Task"}
    assert new_id == "PROJ-77"


def test_resolve_item_transitions_to_done(monkeypatch):
    from factory_core.providers.tracker.jira import JiraTracker

    _set_jira_env(monkeypatch)
    tracker = JiraTracker()
    calls = []

    def fake_request(self, method, path, params=None, json_body=None):
        calls.append((method, path, json_body))
        if method == "GET":
            return json.loads((FIXTURES / "transitions.json").read_text())
        return {}

    monkeypatch.setattr(JiraTracker, "_request", fake_request)
    tracker.resolve_item("PROJ-1")

    post_call = calls[-1]
    assert post_call[:2] == ("POST", "/issue/PROJ-1/transitions")
    assert post_call[2] == {"transition": {"id": "61"}}  # "Resolve" -> Done


def test_get_status_limits_returns_empty_dict_wip_from_config_not_jira(monkeypatch):
    from factory_core.providers.tracker.jira import JiraTracker

    _set_jira_env(monkeypatch)
    assert JiraTracker().get_status_limits() == {}


def test_get_rate_budget_returns_noop_default(monkeypatch):
    from factory_core.providers.tracker.jira import JiraTracker

    _set_jira_env(monkeypatch)
    assert JiraTracker().get_rate_budget() == {
        "remaining": None, "reset": None, "used": None, "limit": None,
    }


def test_opaque_ids_never_coerced_to_int(monkeypatch):
    # Requirement 4: no int() coercion anywhere -- exercise with a non-numeric key.
    from factory_core.providers.tracker.jira import JiraTracker

    _set_jira_env(monkeypatch)
    tracker = JiraTracker()
    monkeypatch.setattr(JiraTracker, "_request", lambda self, *a, **kw: {"transitions": []})
    tracker.set_status("PROJ-123", "blocked")  # must not raise ValueError from int()
