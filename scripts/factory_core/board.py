import json
import os
import subprocess
import sys
import tempfile
import time

from . import identity

OWNER = identity.OWNER
REPO = identity.REPO
PROJECT_NUMBER = identity.PROJECT_NUMBER
PROJECT_ID = identity.PROJECT_ID
STATUS_FIELD = identity.STATUS_FIELD
STATUS_READY = identity.STATUS["ready"]
STATUS_IN_PROGRESS = identity.STATUS["in_progress"]
STATUS_IN_REVIEW = identity.STATUS["in_review"]
STATUS_BLOCKED = identity.STATUS["blocked"]
STATUS_DONE = identity.STATUS["done"]
STATUS_BACKLOG = identity.STATUS["backlog"]
STATUS_REFINED = identity.STATUS["refined"]


def _find_item_by_number_checked(number: str) -> tuple[str, bool]:
    """Project-item lookup by issue number, compared as strings so an opaque
    Tracker id (e.g. "PROJ-123") never needs int() coercion to reach this call.
    Returns (item_id_or_"", lookup_ok) -- lookup_ok is False only when the gh
    call itself failed (non-zero rc or unparseable JSON), True (with item_id
    == "") when the call succeeded but no matching item was found."""
    r = subprocess.run(
        ["gh", "project", "item-list", str(PROJECT_NUMBER),
         "--owner", OWNER, "--format", "json", "--limit", "200"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"board: item-list failed: {r.stderr.strip()}", file=sys.stderr)
        return "", False
    try:
        items = json.loads(r.stdout).get("items", [])
    except json.JSONDecodeError:
        print(f"board: item-list returned unparseable JSON: {r.stdout.strip()[:200]!r}", file=sys.stderr)
        return "", False
    try:
        for item in items:
            c = item.get("content", {})
            if str(c.get("number")) == number and c.get("type") == "Issue":
                return item["id"], True
    except KeyError:
        return "", False
    return "", True


def _find_item_by_number(number: str) -> str:
    return _find_item_by_number_checked(number)[0]


def _item_edit_status(item_id: str, option_id: str) -> bool:
    r = subprocess.run(
        ["gh", "project", "item-edit",
         "--project-id", PROJECT_ID,
         "--id", item_id,
         "--field-id", STATUS_FIELD,
         "--single-select-option-id", option_id],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"board: item-edit failed for {item_id}: {r.stderr.strip()}", file=sys.stderr)
    return r.returncode == 0


def find_board_item(issue_num: int) -> str:
    return _find_item_by_number(str(issue_num))


def set_board_status(issue_num: int, option_id: str, item_id: str | None = None) -> None:
    """Move an issue's board card. This is an EDIT (a board-state mutation): the
    `gh project item-edit` call is the write; the item-list lookup below is only
    the read that resolves which item to edit.

    #607: when `item_id` is supplied (the scheduler's board snapshot carries each
    item's ProjectV2 id), the lookup read is skipped entirely and the edit goes
    straight through — no `gh project item-list` call. An empty/absent id falls
    back to the lookup so a stale snapshot never turns the edit into a no-op.
    Failure signalling is unchanged on both paths: a refused edit prints stderr
    and `_item_edit_status` returns False; this function still returns None so
    its 4 direct callers (breaker.py, rescue.py, deconflict.py, epic_autopilot.py)
    see zero behavior change."""
    if item_id:
        _item_edit_status(item_id, option_id)
        return
    item_id = _find_item_by_number(str(issue_num))
    if not item_id:
        return
    _item_edit_status(item_id, option_id)


def add_to_board(issue_num: int, issue_url: str) -> bool:
    r = subprocess.run(
        ["gh", "project", "item-add", str(PROJECT_NUMBER),
         "--owner", OWNER, "--url", issue_url, "--format", "json"],
        capture_output=True, text=True,
    )
    item_id = ""
    if r.returncode == 0:
        try:
            item_id = json.loads(r.stdout).get("id", "")
        except json.JSONDecodeError:
            item_id = ""
    if not item_id:
        print(f"board: item-add failed for #{issue_num}: {r.stderr.strip()}", file=sys.stderr)
        # item-add is idempotent (re-adding an already-present issue returns the
        # existing item rather than erroring), but don't trust a missing/unparseable
        # .id blindly -- fall back to a direct lookup before giving up.
        item_id, lookup_ok = _find_item_by_number_checked(str(issue_num))
        if not lookup_ok or not item_id:
            return False
    return _item_edit_status(item_id, STATUS_BACKLOG)


def post_or_update_comment(issue_num: int, marker: str, body: str) -> None:
    # --paginate with no --jq: gh merges the array pages into one list, and the newest
    # match is taken over that whole list (a --jq filter would run once per page).
    # A failed lookup is retried once, then the post is skipped -- never "create",
    # which is the duplicate this guards against (#621).
    comments = None
    for _attempt in (1, 2):  # one retry: most callers are one-shot and cannot retry themselves
        if _attempt == 2:
            time.sleep(1)  # give a transient error (HTTP 502, secondary rate limit) a chance to clear
        r = subprocess.run(
            ["gh", "api", f"repos/{OWNER}/{REPO}/issues/{issue_num}/comments?per_page=100",
             "--paginate"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            continue
        try:
            parsed = json.loads(r.stdout)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            comments = parsed
            break
    if comments is None:
        print(f"board: comment lookup failed twice for #{issue_num}; skipping post", file=sys.stderr)
        return
    matches = [c for c in comments if isinstance(c, dict) and marker in (c.get("body") or "")]
    comment_id = str(matches[-1]["id"]) if matches else ""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as fh:
        fh.write(body)
        tmp = fh.name
    try:
        if comment_id:
            subprocess.run(
                ["gh", "api",
                 f"repos/{OWNER}/{REPO}/issues/comments/{comment_id}",
                 "--method", "PATCH", "-F", f"body=@{tmp}"],
                capture_output=True,
            )
        else:
            subprocess.run(
                ["gh", "issue", "comment", str(issue_num),
                 "--repo", f"{OWNER}/{REPO}", "--body-file", tmp],
                capture_output=True,
            )
    finally:
        os.unlink(tmp)
