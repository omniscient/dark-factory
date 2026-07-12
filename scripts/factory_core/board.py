import json
import os
import subprocess
import tempfile

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


def find_board_item(issue_num: int) -> str:
    r = subprocess.run(
        ["gh", "project", "item-list", str(PROJECT_NUMBER),
         "--owner", OWNER, "--format", "json", "--limit", "200"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return ""
    try:
        for item in json.loads(r.stdout).get("items", []):
            c = item.get("content", {})
            if c.get("number") == issue_num and c.get("type") == "Issue":
                return item["id"]
    except (json.JSONDecodeError, KeyError):
        pass
    return ""


def set_board_status(issue_num: int, option_id: str) -> None:
    item_id = find_board_item(issue_num)
    if not item_id:
        return
    subprocess.run(
        ["gh", "project", "item-edit",
         "--project-id", PROJECT_ID,
         "--id", item_id,
         "--field-id", STATUS_FIELD,
         "--single-select-option-id", option_id],
        capture_output=True,
    )


def post_or_update_comment(issue_num: int, marker: str, body: str) -> None:
    r = subprocess.run(
        ["gh", "api", f"repos/{OWNER}/{REPO}/issues/{issue_num}/comments",
         "--jq", f'[.[] | select(.body | contains("{marker}"))] | last | .id // empty'],
        capture_output=True, text=True,
    )
    comment_id = r.stdout.strip() if r.returncode == 0 else ""
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
                ["gh", "issue", "comment", str(issue_num), "--body-file", tmp],
                capture_output=True,
            )
    finally:
        os.unlink(tmp)
