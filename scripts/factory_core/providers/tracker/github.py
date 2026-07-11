"""GitHubTracker — mechanical extraction of today's gh/gh-api/gh-api-graphql Tracker
calls (board.py, scheduler.sh, entrypoint.sh, epic_autopilot.py, smoke_gate.sh).
Must emit argv identical to what runs today — proven by golden-argv parity tests,
not asserted by inspection (spec requirement 3)."""
import json
import re
import subprocess

from factory_core import board, identity
from factory_core.providers.tracker.base import Tracker

_DEFAULT_GET_ITEM_FIELDS = ("title", "body", "labels", "comments")

_CANONICAL_STATUS_NAMES = {
    "ready": "Ready", "in_progress": "In progress", "in_review": "In review",
    "blocked": "Blocked", "done": "Done", "backlog": "Backlog", "refined": "Refined",
}
_STATUS_NAME_TO_CANONICAL = {v: k for k, v in _CANONICAL_STATUS_NAMES.items()}

_BOARD_ITEMS_QUERY_TEMPLATE = '''
      query {
        node(id: "%s") {
          ... on ProjectV2 {
            items(first: 100%s) {
              pageInfo { hasNextPage endCursor }
              nodes {
                fieldValueByName(name: "Status") {
                  ... on ProjectV2ItemFieldSingleSelectValue { name }
                }
                content {
                  ... on Issue {
                    number
                    title
                    labels(first: 10) { nodes { name } }
                  }
                }
              }
            }
          }
        }
      }
    '''


class GitHubTracker(Tracker):
    def get_item(self, id: str, fields: tuple | None = None) -> dict:
        fields = fields or _DEFAULT_GET_ITEM_FIELDS
        r = subprocess.run(
            ["gh", "issue", "view", id, "--repo", identity.SLUG,
             "--json", ",".join(fields)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            return {}
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            return {}

    def get_comments(self, id: str) -> list:
        r = subprocess.run(
            ["gh", "issue", "view", id, "--repo", identity.SLUG,
             "--json", "comments", "-q", ".comments"],
            capture_output=True, text=True,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return []
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            return []

    def get_children(self, epic_id: str) -> list:
        query = (
            'query { repository(owner:"%s", name:"%s") { issue(number:%s) { '
            'subIssues(first:50) { nodes { number state labels(first:20){nodes{name}} } } } } }'
            % (identity.OWNER, identity.REPO, epic_id)
        )
        r = subprocess.run(
            ["gh", "api", "graphql", "-f", "query=" + query],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            return []
        try:
            return json.loads(r.stdout)["data"]["repository"]["issue"]["subIssues"]["nodes"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return []

    # --- Stubs for not-yet-implemented Tracker ops ---
    # A stub override removes a method from Tracker.__abstractmethods__, so
    # GitHubTracker is instantiable starting now (needed for this task's own
    # tests, and every later task's tests through Task 11, to call
    # `GitHubTracker()` without TypeError: Can't instantiate abstract class).
    # Tasks 6-10 each replace one or more stubs below with a real
    # implementation — search for the matching `raise NotImplementedError  #
    # Task N` line when editing.
    def list_work_items(self, statuses: list, labels: list | None = None) -> list:
        wanted_names = {_CANONICAL_STATUS_NAMES.get(s, s) for s in statuses}
        cursor = ""
        results = []
        while True:
            after_arg = f', after: "{cursor}"' if cursor else ""
            query = _BOARD_ITEMS_QUERY_TEMPLATE % (identity.PROJECT_ID, after_arg)
            r = subprocess.run(
                ["gh", "api", "graphql", "-f", "query=" + query],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                break
            try:
                page = json.loads(r.stdout)["data"]["node"]["items"]
            except (json.JSONDecodeError, KeyError, TypeError):
                break
            for node in page.get("nodes", []):
                content = node.get("content") or {}
                number = content.get("number")
                if number is None:
                    continue
                status_name = (node.get("fieldValueByName") or {}).get("name")
                if status_name not in wanted_names:
                    continue
                item_labels = [n["name"] for n in (content.get("labels") or {}).get("nodes", [])]
                if labels and not set(labels).issubset(item_labels):
                    continue
                results.append({
                    "id": str(number),
                    "title": content.get("title"),
                    "labels": item_labels,
                    "status": _STATUS_NAME_TO_CANONICAL.get(status_name, status_name),
                })
            page_info = page.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor") or ""
            if not cursor:
                break
        return results

    def set_status(self, id: str, canonical: str) -> None:
        item_id = board._find_item_by_number(id)
        if not item_id:
            return
        board._item_edit_status(item_id, identity.STATUS[canonical])

    def add_label(self, id: str, name: str) -> None:
        subprocess.run(
            ["gh", "issue", "edit", id, "--repo", identity.SLUG, "--add-label", name],
            capture_output=True,
        )

    def remove_label(self, id: str, name: str) -> None:
        subprocess.run(
            ["gh", "issue", "edit", id, "--repo", identity.SLUG, "--remove-label", name],
            capture_output=True,
        )

    def upsert_comment(self, id: str, marker: str, body: str) -> None:
        board.post_or_update_comment(id, marker, body)

    def create_item(self, title: str, body: str, labels: list | None = None) -> str:
        cmd = ["gh", "issue", "create", "--repo", identity.SLUG]
        for label in (labels or []):
            cmd += ["--label", label]
        cmd += ["--title", title, "--body", body]
        r = subprocess.run(cmd, capture_output=True, text=True)
        m = re.search(r"(\d+)\s*$", (r.stdout or "").strip())
        return m.group(1) if m else ""

    def resolve_item(self, id: str, comment: str | None = None) -> None:
        cmd = ["gh", "issue", "close", id, "--repo", identity.SLUG]
        if comment:
            cmd += ["--comment", comment]
        subprocess.run(cmd, capture_output=True)

    def get_status_limits(self) -> dict:
        query = '''
    query {
      node(id: "%s") {
        ... on ProjectV2 {
          field(name: "Status") {
            ... on ProjectV2SingleSelectField {
              options { id name description }
            }
          }
        }
      }
    }
  ''' % identity.PROJECT_ID
        r = subprocess.run(
            ["gh", "api", "graphql", "-f", "query=" + query],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            return {name: 999 for name in identity.STATUS}
        try:
            options = json.loads(r.stdout)["data"]["node"]["field"]["options"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return {name: 999 for name in identity.STATUS}
        by_id = {o["id"]: (o.get("description") or "") for o in options}
        limits = {}
        for canonical, option_id in identity.STATUS.items():
            m = re.search(r"limit:\s*(\d+)", by_id.get(option_id, ""))
            limits[canonical] = int(m.group(1)) if m else 999
        return limits

    def get_rate_budget(self) -> dict:
        r = subprocess.run(
            ["gh", "api", "rate_limit", "--jq", ".resources.graphql"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            return {"remaining": None, "reset": None, "used": None, "limit": None}
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            return {"remaining": None, "reset": None, "used": None, "limit": None}
        return {
            "remaining": data.get("remaining"), "reset": data.get("reset"),
            "used": data.get("used"), "limit": data.get("limit"),
        }
