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
        if cmd[:3] == ["gh", "issue", "view"] and "--json" in cmd and "-q" not in cmd:
            key = cmd[3]
            if key not in self.items:
                return subprocess.CompletedProcess([], 1, stdout="", stderr="not found")
            it = self.items[key]
            full = {
                "title": it["title"], "body": "the body", "labels": sorted(it["labels"]),
                "comments": [], "state": "CLOSED" if it["status"] == "done" else "OPEN",
            }
            wanted = cmd[cmd.index("--json") + 1].split(",")
            return self._ok(stdout=json.dumps({k: full[k] for k in wanted}))
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
            # the real --jq is `[...] | last | .id`: the NEWEST matching comment
            matches = [c for c in self.comments.get(key, []) if marker in c["body"]]
            match = matches[-1] if matches else None
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
        if cmd[:3] == ["gh", "issue", "list"]:
            label = cmd[cmd.index("--label") + 1]
            rows = [{"number": int(k)} for k, it in self.items.items()
                    if label in it["labels"] and it["status"] != "done"]
            return self._ok(stdout=json.dumps(rows))
        if cmd[:2] == ["gh", "api"] and "--jq" in cmd and "/issues/comments/" in str(cmd[2]):
            comment_id = int(str(cmd[2]).rsplit("/", 1)[1])
            for lst in self.comments.values():
                for c in lst:
                    if c["id"] == comment_id:
                        return self._ok(stdout=c["body"])
            return self._ok()
        if cmd[:3] == ["gh", "issue", "comment"]:
            key = cmd[3]
            if "--body" in cmd:
                body_text = cmd[cmd.index("--body") + 1]
            else:
                body_text = Path(cmd[cmd.index("--body-file") + 1]).read_text()
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
                    if (self._status_name(it["status"]) in jql or "status IN" not in jql)
                    and all(label in it["labels"] for label in wanted_labels)
                ]
            if "statusCategory != Done" in jql:
                keys = [k for k in keys if self.items[k]["status"] != "done"]
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

    assert tracker.add_label(id1, "needs-discussion") is True
    assert "needs-discussion" in controller.items[id1]["labels"]

    assert tracker.remove_label(id1, "needs-discussion") is True
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


def test_add_comment_appends_never_edits(tracker_and_controller):
    tracker, controller = tracker_and_controller
    id1 = "1" if isinstance(tracker, GitHubTracker) else "PROJ-1"
    controller.seed_item(id1)

    assert tracker.add_comment(id1, "first") is True
    assert tracker.add_comment(id1, "first") is True  # identical body: still a new comment

    assert [c["body"] for c in controller.comments[id1]] == ["first", "first"]


def test_find_comment_returns_newest_marker_match_with_body(tracker_and_controller):
    tracker, controller = tracker_and_controller
    id1 = "1" if isinstance(tracker, GitHubTracker) else "PROJ-1"
    controller.seed_item(id1)
    marker = "<!-- factory:find-test -->"

    assert tracker.find_comment(id1, marker) is None
    tracker.add_comment(id1, "unrelated")
    tracker.add_comment(id1, f"{marker}\nold")
    tracker.add_comment(id1, f"{marker}\nnew")

    found = tracker.find_comment(id1, marker)
    assert found["body"].endswith("new")
    assert isinstance(found["id"], str)


def test_update_comment_replaces_body_of_found_comment(tracker_and_controller):
    tracker, controller = tracker_and_controller
    id1 = "1" if isinstance(tracker, GitHubTracker) else "PROJ-1"
    controller.seed_item(id1)
    marker = "<!-- factory:update-test -->"
    tracker.add_comment(id1, f"{marker}\nbefore")
    tracker.add_comment(id1, "other")

    found = tracker.find_comment(id1, marker)
    assert tracker.update_comment(id1, found["id"], f"{marker}\nafter") is True

    assert [c["body"] for c in controller.comments[id1]] == [f"{marker}\nafter", "other"]


def test_list_board_items_has_the_scheduler_snapshot_shape(tracker_and_controller):
    tracker, controller = tracker_and_controller
    gh = isinstance(tracker, GitHubTracker)
    id1, id2 = ("1", "2") if gh else ("PROJ-1", "PROJ-2")
    controller.seed_item(id1, title="a", labels=["ready-for-agent"], status="ready")
    controller.seed_item(id2, title="b", labels=[], status="in_review")

    snapshot = tracker.list_board_items()

    assert set(snapshot) == {"items"}
    by_title = {i["content"]["title"]: i for i in snapshot["items"]}
    assert set(by_title) == {"a", "b"}
    for item in snapshot["items"]:
        assert set(item) == {"content", "labels", "status"}
        assert item["content"]["type"] == "Issue"
        assert item["content"]["number"] is not None
    assert by_title["a"]["labels"] == ["ready-for-agent"]
    assert by_title["a"]["status"].casefold() == "ready"
    assert by_title["b"]["status"].casefold() == "in review"


def test_list_items_by_label_returns_open_ids_only(tracker_and_controller):
    tracker, controller = tracker_and_controller
    gh = isinstance(tracker, GitHubTracker)
    id1, id2, id3 = ("1", "2", "3") if gh else ("PROJ-1", "PROJ-2", "PROJ-3")
    controller.seed_item(id1, labels=["ready-for-agent"], status="backlog")
    controller.seed_item(id2, labels=[], status="backlog")
    controller.seed_item(id3, labels=["ready-for-agent"], status="done")

    assert tracker.list_items("ready-for-agent", limit=200) == [id1]


def test_set_status_moves_through_canonical_vocabulary(tracker_and_controller):
    tracker, controller = tracker_and_controller
    id1 = "1" if isinstance(tracker, GitHubTracker) else "PROJ-1"
    controller.seed_item(id1, status="ready")

    result = tracker.set_status(id1, "in_review")
    assert controller.items[id1]["status"] == "in_review"
    assert result is True


def test_set_status_unknown_item_is_safe_noop(tracker_and_controller):
    tracker, controller = tracker_and_controller
    unknown_id = "999" if isinstance(tracker, GitHubTracker) else "PROJ-999"

    result = tracker.set_status(unknown_id, "in_review")  # must not raise
    assert unknown_id not in controller.items
    assert result is False


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


def _cli_args(**kw):
    import argparse
    return argparse.Namespace(**kw)


def test_get_item_fields_filter_is_honoured_by_every_provider(tracker_and_controller):
    """`scheduler.sh` runs `tracker get --fields body` and `--fields state` (dependency
    gating); the result must contain exactly the requested keys on every provider."""
    tracker, controller = tracker_and_controller
    gh = isinstance(tracker, GitHubTracker)
    id1, id2 = ("1", "2") if gh else ("PROJ-1", "PROJ-2")
    controller.seed_item(id1, title="t", labels=["x"], status="ready")
    controller.seed_item(id2, status="done")

    assert set(tracker.get_item(id1, fields=("body",))) == {"body"}
    assert set(tracker.get_item(id1, fields=("title", "labels"))) == {"title", "labels"}
    assert tracker.get_item(id1, fields=("state",)) == {"state": "OPEN"}
    assert tracker.get_item(id2, fields=("state",)) == {"state": "CLOSED"}
    default = tracker.get_item(id1)
    assert {"title", "body", "labels"} <= set(default)


def test_every_tracker_verb_works_through_the_cli_argument_shape(
        tracker_and_controller, monkeypatch, tmp_path):
    """Drive each `providers/cli.py` tracker verb exactly as argparse would, so a provider
    that lacks a keyword the CLI passes fails here (#488)."""
    from factory_core.providers import cli
    tracker, controller = tracker_and_controller
    monkeypatch.setattr(cli, "get_tracker", lambda: tracker)
    gh = isinstance(tracker, GitHubTracker)
    id1 = "1" if gh else "PROJ-1"
    controller.seed_item(id1, status="ready")
    body_file = tmp_path / "body.md"
    body_file.write_text("hello")

    cli._tracker_list(_cli_args(statuses="ready,done", labels=""))
    cli._tracker_get(_cli_args(id=id1, fields=""))
    cli._tracker_get(_cli_args(id=id1, fields="body"))
    cli._tracker_get(_cli_args(id=id1, fields="state"))
    cli._tracker_get_comments(_cli_args(id=id1))
    cli._tracker_get_status_limits(_cli_args())
    cli._tracker_set_status(_cli_args(id=id1, status="in_review"))
    cli._tracker_label(_cli_args(id=id1, add=["a"], remove=["a"]))
    cli._tracker_comment(_cli_args(id=id1, marker="<!-- m -->", body_file=str(body_file)))
    cli._tracker_add_comment(_cli_args(id=id1, body="hi", body_file=None))
    cli._tracker_add_comment(_cli_args(id=id1, body=None, body_file=str(body_file)))
    cli._tracker_find_comment(_cli_args(id=id1, marker="<!-- m -->", body_out=str(tmp_path / "o.md")))
    cli._tracker_update_comment(_cli_args(
        id=id1, comment_id="1", body="hi", body_file=None))
    cli._tracker_board_items(_cli_args())
    cli._tracker_list_items(_cli_args(label="a", limit=200))
    cli._tracker_create(_cli_args(title="t", body_file=str(body_file), labels="a,b"))
    cli._tracker_children(_cli_args(epic=id1))
    cli._tracker_resolve(_cli_args(id=id1))
