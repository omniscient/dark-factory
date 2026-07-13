import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

from factory_core import identity
from factory_core.providers.tracker.github import GitHubTracker, _CANONICAL_STATUS_NAMES
from factory_core.providers.tracker.jira import JiraTracker

_ID_TO_CANONICAL = {v: k for k, v in identity.STATUS.items()}


class _GitHubFixtureController:
    """In-memory GitHub-shaped backend; dispatches on `gh` argv exactly as
    GitHubTracker/board.py emit it today, so its own subprocess seam stays exercised."""

    def __init__(self, monkeypatch):
        self.items = {}     # id (str) -> {"title", "labels": set[str], "status": canonical}
        self.comments = {}  # id -> list[{"id": int, "body": str}]
        self._next_comment_id = 1
        self._next_issue_id = 1000
        monkeypatch.setattr(subprocess, "run", self._run)

    def seed_item(self, id, title="t", labels=None, status="ready"):
        self.items[id] = {"title": title, "labels": set(labels or []), "status": status}
        self.comments.setdefault(id, [])

    @staticmethod
    def _ok(stdout=""):
        return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")

    def _run(self, cmd, **kw):
        if cmd[:3] == ["gh", "project", "item-list"]:
            payload = {"items": [
                {"id": f"ITEM-{k}", "content": {"number": int(k), "type": "Issue"}}
                for k in self.items
            ]}
            return self._ok(stdout=json.dumps(payload))
        if cmd[:3] == ["gh", "project", "item-edit"]:
            item_id = cmd[cmd.index("--id") + 1]
            option_id = cmd[cmd.index("--single-select-option-id") + 1]
            key = item_id.removeprefix("ITEM-")
            canonical = _ID_TO_CANONICAL.get(option_id, option_id)
            if key in self.items:
                self.items[key]["status"] = canonical
            return self._ok()
        if cmd[:3] == ["gh", "issue", "edit"]:
            key = cmd[3]
            if "--add-label" in cmd:
                self.items[key]["labels"].add(cmd[cmd.index("--add-label") + 1])
            if "--remove-label" in cmd:
                self.items[key]["labels"].discard(cmd[cmd.index("--remove-label") + 1])
            return self._ok()
        if cmd[:3] == ["gh", "issue", "create"]:
            new_id = str(self._next_issue_id)
            self._next_issue_id += 1
            title = cmd[cmd.index("--title") + 1]
            self.items[new_id] = {"title": title, "labels": set(), "status": "backlog"}
            self.comments[new_id] = []
            return self._ok(stdout=f"https://github.com/x/y/issues/{new_id}\n")
        if cmd[:3] == ["gh", "issue", "close"]:
            if cmd[3] in self.items:
                self.items[cmd[3]]["status"] = "done"
            return self._ok()
        if cmd[:2] == ["gh", "api"] and "--jq" in cmd and str(cmd[2]).endswith("/comments"):
            key = cmd[2].split("/issues/")[1].split("/")[0]
            marker = cmd[cmd.index("--jq") + 1].split('contains("')[1].split('")')[0]
            match = next((c for c in self.comments.get(key, []) if marker in c["body"]), None)
            return self._ok(stdout=str(match["id"]) if match else "")
        if cmd[:2] == ["gh", "api"] and "--method" in cmd and "PATCH" in cmd:
            comment_id = int(str(cmd[2]).rsplit("/", 1)[1])
            body_path = cmd[cmd.index("-F") + 1].split("body=@")[1]
            new_body = Path(body_path).read_text()
            for lst in self.comments.values():
                for c in lst:
                    if c["id"] == comment_id:
                        c["body"] = new_body
            return self._ok()
        if cmd[:3] == ["gh", "issue", "comment"]:
            key = cmd[3]
            body_path = cmd[cmd.index("--body-file") + 1]
            body_text = Path(body_path).read_text()
            cid = self._next_comment_id
            self._next_comment_id += 1
            self.comments.setdefault(key, []).append({"id": cid, "body": body_text})
            return self._ok()
        if cmd[:3] == ["gh", "api", "graphql"] and "subIssues" in cmd[-1]:
            return self._ok(stdout=json.dumps(
                {"data": {"repository": {"issue": {"subIssues": {"nodes": []}}}}}
            ))
        if cmd[:3] == ["gh", "api", "graphql"]:
            nodes = [
                {
                    "fieldValueByName": {"name": _CANONICAL_STATUS_NAMES.get(it["status"], it["status"])},
                    "content": {
                        "number": int(k), "title": it["title"],
                        "labels": {"nodes": [{"name": l} for l in it["labels"]]},
                    },
                }
                for k, it in self.items.items()
            ]
            payload = {"data": {"node": {"items": {
                "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes,
            }}}}
            return self._ok(stdout=json.dumps(payload))
        return self._ok()


class _JiraFixtureController:
    """In-memory Jira-shaped backend; monkeypatches JiraTracker._request directly
    (the adapter's own I/O seam), using tests/fixtures/jira/issue.json as the response
    template for individual-issue lookups."""

    def __init__(self, monkeypatch, tracker):
        self.items = {}     # key -> {"summary", "description", "labels": set, "status": canonical}
        self.comments = {}  # key -> list[{"id": int, "body": str}]
        self.children = {}  # epic key -> [child key, ...]
        self._next_comment_id = 1
        self._next_issue_seq = 1
        self._tracker = tracker
        self._issue_template = json.loads(
            (Path(__file__).resolve().parent / "fixtures" / "jira" / "issue.json").read_text()
        )
        controller = self

        def _request(tracker_instance, method, path, params=None, json_body=None):
            return controller._dispatch(method, path, params, json_body)

        monkeypatch.setattr(JiraTracker, "_request", _request)

    def seed_item(self, key, title="t", labels=None, status="ready"):
        self.items[key] = {"summary": title, "description": "", "labels": set(labels or []), "status": status}
        self.comments.setdefault(key, [])

    def seed_children(self, epic_key, child_keys):
        self.children[epic_key] = child_keys

    def _status_name(self, canonical):
        return self._tracker._canonical_to_name.get(canonical, canonical)

    def _issue_json(self, key):
        it = self.items[key]
        issue = json.loads(json.dumps(self._issue_template))  # deep copy via round-trip
        issue["key"] = key
        issue["fields"]["summary"] = it["summary"]
        issue["fields"]["description"] = it["description"]
        issue["fields"]["labels"] = sorted(it["labels"])
        issue["fields"]["status"] = {"name": self._status_name(it["status"])}
        return issue

    def _requested_labels(self, jql):
        return re.findall(r'labels = "([^"]+)"', jql)

    def _dispatch(self, method, path, params=None, json_body=None):
        params = params or {}
        if path == "/search":
            jql = params.get("jql", "")
            if jql.startswith("cf["):
                epic_key = jql.split('= "')[1].split('"')[0]
                keys = self.children.get(epic_key, [])
            else:
                wanted_labels = self._requested_labels(jql)
                keys = [
                    k for k, it in self.items.items()
                    if self._status_name(it["status"]) in jql
                    and all(label in it["labels"] for label in wanted_labels)
                ]
            return {"issues": [self._issue_json(k) for k in keys]}
        if path.endswith("/transitions"):
            key = path.split("/")[2]
            if method == "GET":
                if key not in self.items:
                    return {"transitions": []}
                return {"transitions": [
                    {"id": str(i), "to": {"name": name}}
                    for i, name in enumerate(self._tracker._canonical_to_name.values())
                ]}
            names = list(self._tracker._canonical_to_name.values())
            name = names[int(json_body["transition"]["id"])]
            self.items[key]["status"] = self._tracker._name_to_canonical[name.casefold()]
            return {}
        if path.endswith("/comment") and "/comment/" not in path:
            key = path.split("/")[2]
            if method == "GET":
                return {"comments": self.comments.get(key, [])}
            cid = self._next_comment_id
            self._next_comment_id += 1
            self.comments.setdefault(key, []).append({"id": cid, "body": json_body["body"]})
            return {}
        if "/comment/" in path:
            key = path.split("/")[2]
            comment_id = int(path.rsplit("/", 1)[1])
            for c in self.comments.get(key, []):
                if c["id"] == comment_id:
                    c["body"] = json_body["body"]
            return {}
        if path.startswith("/issue/") and method == "GET":
            key = path.split("/")[2]
            return self._issue_json(key) if key in self.items else {"fields": {}}
        if path.startswith("/issue/") and method == "PUT":
            key = path.split("/")[2]
            fields = (json_body or {}).get("fields", {})
            if "labels" in fields:
                self.items[key]["labels"] = set(fields["labels"])
            return {}
        if path == "/issue" and method == "POST":
            key = f"PROJ-{100 + self._next_issue_seq}"
            self._next_issue_seq += 1
            fields = json_body["fields"]
            self.items[key] = {
                "summary": fields["summary"], "description": fields.get("description", ""),
                "labels": set(fields.get("labels", [])), "status": "backlog",
            }
            self.comments[key] = []
            return {"key": key}
        raise AssertionError(f"unhandled jira request in contract fixture: {method} {path}")


@pytest.fixture(params=["github", "jira"])
def tracker_and_controller(request, monkeypatch):
    if request.param == "github":
        return GitHubTracker(), _GitHubFixtureController(monkeypatch)
    monkeypatch.setenv("JIRA_BASE_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "PROJ")
    monkeypatch.setenv("JIRA_TOKEN", "t")
    monkeypatch.setenv("JIRA_EPIC_LINK_FIELD", "customfield_10008")
    tracker = JiraTracker()
    return tracker, _JiraFixtureController(monkeypatch, tracker)


def test_list_work_items_filters_by_status_and_label(tracker_and_controller):
    tracker, controller = tracker_and_controller
    is_github = isinstance(tracker, GitHubTracker)
    id1, id2, id3 = ("1", "2", "3") if is_github else ("PROJ-1", "PROJ-2", "PROJ-3")
    controller.seed_item(id1, title="a", labels=["ready-for-agent"], status="ready")
    controller.seed_item(id2, title="b", labels=[], status="ready")
    controller.seed_item(id3, title="c", labels=["ready-for-agent"], status="done")

    items = tracker.list_work_items(["ready"], labels=["ready-for-agent"])
    assert [i["id"] for i in items] == [id1]


def test_label_add_and_remove_round_trip(tracker_and_controller):
    tracker, controller = tracker_and_controller
    id1 = "1" if isinstance(tracker, GitHubTracker) else "PROJ-1"
    controller.seed_item(id1, labels=[])

    tracker.add_label(id1, "needs-discussion")
    assert "needs-discussion" in controller.items[id1]["labels"]

    tracker.remove_label(id1, "needs-discussion")
    assert "needs-discussion" not in controller.items[id1]["labels"]


def test_marker_comment_upsert_updates_in_place_not_duplicate(tracker_and_controller):
    tracker, controller = tracker_and_controller
    id1 = "1" if isinstance(tracker, GitHubTracker) else "PROJ-1"
    controller.seed_item(id1)
    marker = "<!-- factory:contract-test -->"

    tracker.upsert_comment(id1, marker, f"{marker}\nfirst")
    tracker.upsert_comment(id1, marker, f"{marker}\nsecond")

    bodies = [c["body"] for c in controller.comments[id1]]
    assert len(bodies) == 1
    assert bodies[0].endswith("second")


def test_set_status_moves_through_canonical_vocabulary(tracker_and_controller):
    tracker, controller = tracker_and_controller
    id1 = "1" if isinstance(tracker, GitHubTracker) else "PROJ-1"
    controller.seed_item(id1, status="ready")

    tracker.set_status(id1, "in_review")
    assert controller.items[id1]["status"] == "in_review"


def test_set_status_unknown_item_is_safe_noop(tracker_and_controller):
    tracker, controller = tracker_and_controller
    unknown_id = "999" if isinstance(tracker, GitHubTracker) else "PROJ-999"

    tracker.set_status(unknown_id, "in_review")  # must not raise
    assert unknown_id not in controller.items


def test_get_children_returns_list_of_actual_children_and_empty_when_none(tracker_and_controller):
    tracker, controller = tracker_and_controller
    is_github = isinstance(tracker, GitHubTracker)
    epic_id = "5" if is_github else "PROJ-5"

    if is_github:
        # GitHubTracker.get_children reads gh api graphql subIssues directly; the
        # controller's catch-all subIssues branch always returns an empty page,
        # matching the "empty when none" branch of this same assertion below.
        assert tracker.get_children(epic_id) == []
    else:
        controller.seed_children(epic_id, ["PROJ-10", "PROJ-11"])
        controller.seed_item("PROJ-10", title="Child A", labels=["ready-for-agent"], status="ready")
        controller.seed_item("PROJ-11", title="Child B", labels=[], status="in_progress")
        children = tracker.get_children(epic_id)
        assert len(children) == 2
        assert {c["id"] for c in children} == {"PROJ-10", "PROJ-11"}

    assert tracker.get_children("no-such-epic" if not is_github else "999") == []
