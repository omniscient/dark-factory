"""GitHubTracker — mechanical extraction of today's gh/gh-api/gh-api-graphql Tracker
calls (board.py, scheduler.sh, entrypoint.sh, epic_autopilot.py, smoke_gate.sh).
Must emit argv identical to what runs today — proven by golden-argv parity tests,
not asserted by inspection (spec requirement 3)."""
import json
import os
import re
import subprocess
import sys
import tempfile

from factory_core import board, cost_log, identity
from factory_core.providers.tracker.base import Tracker

_DEFAULT_GET_ITEM_FIELDS = ("title", "body", "labels", "comments")

_CANONICAL_STATUS_NAMES = {
    "ready": "Ready", "in_progress": "In progress", "in_review": "In review",
    "blocked": "Blocked", "done": "Done", "backlog": "Backlog", "refined": "Refined",
}
_STATUS_NAME_TO_CANONICAL = {v: k for k, v in _CANONICAL_STATUS_NAMES.items()}

# #607: two deliberate extensions over the pre-#607 query (parity goldens updated):
#   - `id` per node: the scheduler's snapshot now carries each item's ProjectV2 id,
#     so `board-move` with a supplied item_id skips the item-list lookup entirely.
#   - root-level `rateLimit { cost remaining resetAt }`: each page call records its
#     own point cost (1 point, no extra round trip) for the cost log.
_BOARD_ITEMS_QUERY_TEMPLATE = '''
      query {
        rateLimit { cost remaining resetAt }
        node(id: "%s") {
          ... on ProjectV2 {
            items(first: 100%s) {
              pageInfo { hasNextPage endCursor }
              nodes {
                id
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
    @classmethod
    def required_env(cls) -> list[str]:
        return ["GH_TOKEN"]

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
        # Reject characters that could break out of the GraphQL query string
        # (quotes/braces/whitespace) while still allowing opaque non-GitHub
        # ids (e.g. "PROJ-123") to flow through unmodified.
        if not re.fullmatch(r"[A-Za-z0-9_-]+", str(epic_id)):
            return []
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

    # #607: batched dependency-state read — one aliased query per chunk instead of
    # one `tracker get` per dependency (the scheduler's per-item-loop hot path).
    # Numbers are validated \d+ before interpolation (an invalid id is never sent
    # and reads as unmet); a nonexistent issue returns null for its alias, i.e.
    # missing means unmet. gh failure raises so the CLI exits non-zero and the
    # scheduler treats every dependency as unmet — fail closed.
    _STATES_CHUNK_SIZE = 25

    def get_states(self, ids: list[str]) -> dict:
        out: dict = {str(i): None for i in ids}
        valid = [str(i) for i in ids if re.fullmatch(r"\d+", str(i))]
        for start in range(0, len(valid), self._STATES_CHUNK_SIZE):
            chunk = valid[start:start + self._STATES_CHUNK_SIZE]
            selections = " ".join(
                f"i{n}: issue(number:{num}) {{ state }}" for n, num in enumerate(chunk))
            query = (
                'query { rateLimit { cost remaining resetAt } '
                'repository(owner:"%s", name:"%s") { %s } }'
                % (identity.OWNER, identity.REPO, selections)
            )
            r = subprocess.run(
                ["gh", "api", "graphql", "-f", "query=" + query],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                raise RuntimeError(f"gh api graphql (issue states) failed: {r.stderr.strip()}")
            try:
                data = json.loads(r.stdout)
                repo = ((data.get("data") or {}).get("repository") or {})
            except json.JSONDecodeError as e:
                raise RuntimeError(
                    f"gh api graphql (issue states) returned non-JSON: {e}") from e
            for n, num in enumerate(chunk):
                node = repo.get(f"i{n}")
                out[num] = (node or {}).get("state") if isinstance(node, dict) else None
            cost_log.log_call("issue_states", (data.get("data") or {}).get("rateLimit"))
        return out

    def set_status(self, id: str, canonical: str) -> bool:
        item_id, lookup_ok = board._find_item_by_number_checked(id)
        if not lookup_ok:
            print(f"github: board item-list lookup failed for #{id}; leaving unchanged", file=sys.stderr)
            return False
        if not item_id:
            print(f"github: #{id} not found on the board; leaving unchanged", file=sys.stderr)
            return False
        return board._item_edit_status(item_id, identity.STATUS[canonical])

    def add_label(self, id: str, name: str) -> bool:
        r = subprocess.run(
            ["gh", "issue", "edit", id, "--repo", identity.SLUG, "--add-label", name],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"github: add-label {name!r} failed for #{id}: {r.stderr.strip()}", file=sys.stderr)
        return r.returncode == 0

    def remove_label(self, id: str, name: str) -> bool:
        r = subprocess.run(
            ["gh", "issue", "edit", id, "--repo", identity.SLUG, "--remove-label", name],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"github: remove-label {name!r} failed for #{id}: {r.stderr.strip()}", file=sys.stderr)
        return r.returncode == 0

    def upsert_comment(self, id: str, marker: str, body: str) -> None:
        board.post_or_update_comment(id, marker, body)

    def add_comment(self, id: str, body: str) -> bool:
        # Same argv as the scheduler/entrypoint `gh issue comment N --repo SLUG --body ...`
        # posts. gh's stdout (the comment URL) is not forwarded: no caller consumed it.
        r = subprocess.run(
            ["gh", "issue", "comment", id, "--repo", identity.SLUG, "--body", body],
            capture_output=True, text=True,
        )
        if r.stderr:
            sys.stderr.write(r.stderr)
        return r.returncode == 0

    def find_comment(self, id: str, marker: str) -> dict | None:
        # Same two gh api calls as the entrypoint cost-report upsert: list endpoint with the
        # marker --jq (newest match), then the single-comment endpoint for its body. Note
        # the single-comment endpoint omits the issue number (/issues/comments/{id}); the
        # /issues/{n}/comments/{id} form 404s.
        r = subprocess.run(
            ["gh", "api", f"repos/{identity.SLUG}/issues/{id}/comments",
             "--jq", f'[.[] | select(.body | contains("{marker}"))] | last | .id // empty'],
            capture_output=True, text=True,
        )
        comment_id = r.stdout.strip() if r.returncode == 0 else ""
        if not comment_id:
            return None
        b = subprocess.run(
            ["gh", "api", f"repos/{identity.SLUG}/issues/comments/{comment_id}",
             "--jq", ".body"],
            capture_output=True, text=True,
        )
        return {"id": comment_id, "body": b.stdout if b.returncode == 0 else ""}

    def update_comment(self, id: str, comment_id: str, body: str) -> bool:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(body)
            tmp = fh.name
        try:
            r = subprocess.run(
                ["gh", "api", f"repos/{identity.SLUG}/issues/comments/{comment_id}",
                 "--method", "PATCH", "-F", f"body=@{tmp}"],
                capture_output=True, text=True,
            )
        finally:
            os.unlink(tmp)
        if r.returncode != 0 and r.stderr:
            sys.stderr.write(r.stderr)
        return r.returncode == 0

    def list_board_items(self) -> dict:
        # Mirrors the scheduler's former bash fetch_board_items exactly: same query text
        # (identical template as list_work_items), one `gh api graphql` per 100 items, the
        # exit status of gh is NOT consulted (a GraphQL error body parses to "no nodes",
        # as the old `jq '// []'` did) and only an unparsable body is a hard failure.
        cursor = ""
        nodes = []
        while True:
            after_arg = f', after: "{cursor}"' if cursor else ""
            query = _BOARD_ITEMS_QUERY_TEMPLATE % (identity.PROJECT_ID, after_arg)
            r = subprocess.run(
                ["gh", "api", "graphql", "-f", "query=" + query],
                capture_output=True, text=True,
            )
            try:
                data = json.loads(r.stdout)
                items = ((data.get("data") or {}).get("node") or {}).get("items") or {}
                nodes.extend(items.get("nodes") or [])
                page_info = items.get("pageInfo") or {}
            except (json.JSONDecodeError, AttributeError, TypeError) as e:
                raise RuntimeError(f"github: unreadable project-items response: {e}") from e
            # #607: one cost line per page call (the query's own rateLimit payload).
            # No-op unless SCHEDULER_STATE_DIR is set; never raises.
            cost_log.log_call("board_items", (data.get("data") or {}).get("rateLimit"))
            has_next = page_info.get("hasNextPage") is True
            cursor = page_info.get("endCursor") or ""
            if has_next and not cursor:
                raise RuntimeError(
                    "GitHub ProjectV2 pageInfo indicated another page but returned no cursor")
            if not has_next:
                break
        out = []
        for node in nodes:
            content = node.get("content") or {}
            if content.get("number") is None:
                continue
            out.append({
                "content": {"number": content["number"], "title": content.get("title"),
                            "type": "Issue"},
                "labels": [n["name"] for n in (content.get("labels") or {}).get("nodes") or []],
                "status": (node.get("fieldValueByName") or {}).get("name"),
                # #607: carried for board-move's --item-id (None in older/stubbed
                # responses; a None id simply falls back to the lookup path).
                "item_id": node.get("id"),
            })
        return {"items": out}

    def list_items(self, label: str, limit: int = 200) -> list:
        # Same argv as the scheduler's former off-board `gh issue list` lookup.
        r = subprocess.run(
            ["gh", "issue", "list", "--repo", identity.SLUG, "--state", "open",
             "--label", label, "--limit", str(limit), "--json", "number"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"gh issue list failed: {r.stderr.strip()}")
        try:
            rows = json.loads(r.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"gh issue list returned non-JSON: {e}") from e
        if not isinstance(rows, list):
            raise RuntimeError("gh issue list did not return a JSON array")
        return [str(row["number"]) for row in rows]

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
            raise RuntimeError(f"gh api rate_limit failed: {r.stderr.strip()}")
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            return {"remaining": None, "reset": None, "used": None, "limit": None}
        return {
            "remaining": data.get("remaining"), "reset": data.get("reset"),
            "used": data.get("used"), "limit": data.get("limit"),
        }
