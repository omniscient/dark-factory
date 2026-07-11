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
        raise NotImplementedError  # Task 10

    def set_status(self, id: str, canonical: str) -> None:
        item_id = board._find_item_by_number(id)
        if not item_id:
            return
        board._item_edit_status(item_id, identity.STATUS[canonical])

    def add_label(self, id: str, name: str) -> None:
        raise NotImplementedError  # Task 7

    def remove_label(self, id: str, name: str) -> None:
        raise NotImplementedError  # Task 7

    def upsert_comment(self, id: str, marker: str, body: str) -> None:
        raise NotImplementedError  # Task 8

    def create_item(self, title: str, body: str, labels: list | None = None) -> str:
        raise NotImplementedError  # Task 9

    def resolve_item(self, id: str) -> None:
        raise NotImplementedError  # Task 9
