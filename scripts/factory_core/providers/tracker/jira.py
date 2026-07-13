"""JiraTracker -- Jira Server/Data Center Tracker adapter (parent spec
docs/provider-abstraction-design.md Sec 5.4). All HTTP goes through `_request`,
the one seam the fixture tests monkeypatch (mirrors GitHubTracker's
subprocess.run monkeypatch idiom)."""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from factory_core.providers.tracker.base import Tracker

_CANONICAL_TO_ENV = {
    "ready": "FACTORY_STATUS_READY", "in_progress": "FACTORY_STATUS_IN_PROGRESS",
    "in_review": "FACTORY_STATUS_IN_REVIEW", "blocked": "FACTORY_STATUS_BLOCKED",
    "done": "FACTORY_STATUS_DONE", "backlog": "FACTORY_STATUS_BACKLOG",
    "refined": "FACTORY_STATUS_REFINED",
}
_CANONICAL_DEFAULT_NAME = {
    "ready": "Ready", "in_progress": "In progress", "in_review": "In review",
    "blocked": "Blocked", "done": "Done", "backlog": "Backlog", "refined": "Refined",
}

_SEARCH_PAGE_SIZE = 50


def _jql_escape(value: str) -> str:
    """Escape a value for safe interpolation inside a JQL double-quoted literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


class JiraTracker(Tracker):
    def __init__(self):
        self._base_url = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
        self._project_key = os.environ.get("JIRA_PROJECT_KEY", "")
        self._token = os.environ.get("JIRA_TOKEN", "")
        self._epic_link_field = os.environ.get("JIRA_EPIC_LINK_FIELD", "")
        self._issue_type = os.environ.get("JIRA_ISSUE_TYPE", "Task")
        self._timeout = float(os.environ.get("JIRA_REQUEST_TIMEOUT_SECONDS", "30"))
        self._canonical_to_name = {
            canonical: os.environ.get(env_name, _CANONICAL_DEFAULT_NAME[canonical])
            for canonical, env_name in _CANONICAL_TO_ENV.items()
        }
        self._name_to_canonical = {
            name.casefold(): canonical for canonical, name in self._canonical_to_name.items()
        }

    @classmethod
    def required_env(cls) -> list[str]:
        return ["JIRA_BASE_URL", "JIRA_PROJECT_KEY", "JIRA_TOKEN", "JIRA_EPIC_LINK_FIELD"]

    def _request(self, method: str, path: str, params: dict | None = None,
                 json_body: dict | None = None) -> dict:
        url = f"{self._base_url}/rest/api/2{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
        req = urllib.request.Request(url, method=method, data=data)
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"jira: {method} {path} failed ({e.code}): {detail}") from e
        if not body:
            return {}
        return json.loads(body)

    def _canonical_status(self, fields: dict) -> str:
        status_name = (fields.get("status") or {}).get("name", "")
        return self._name_to_canonical.get(status_name.casefold(), status_name)

    def _issue_to_item(self, issue: dict) -> dict:
        fields = issue.get("fields", {})
        return {
            "id": issue.get("key"),
            "title": fields.get("summary"),
            "labels": fields.get("labels", []),
            "status": self._canonical_status(fields),
        }

    def _search_all(self, jql: str) -> list:
        """Page through /search until all matching issues are collected -- Jira defaults
        maxResults to ~50 per page, so a single call silently drops overflow."""
        issues = []
        start_at = 0
        while True:
            data = self._request(
                "GET", "/search",
                params={"jql": jql, "startAt": start_at, "maxResults": _SEARCH_PAGE_SIZE},
            )
            page = data.get("issues", [])
            issues.extend(page)
            total = data.get("total", len(issues))
            start_at += len(page)
            if not page or start_at >= total:
                break
        return issues

    def list_work_items(self, statuses: list, labels: list | None = None) -> list:
        if not statuses:
            return []
        names = [self._canonical_to_name.get(s, s) for s in statuses]
        quoted = ",".join(f'"{_jql_escape(n)}"' for n in names)
        clauses = [f"project={self._project_key}", f"status IN({quoted})"]
        for label in (labels or []):
            clauses.append(f'labels = "{_jql_escape(label)}"')
        jql = " AND ".join(clauses)
        issues = self._search_all(jql)
        return [self._issue_to_item(issue) for issue in issues]

    def get_item(self, id: str) -> dict:
        issue = self._request("GET", f"/issue/{id}")
        fields = issue.get("fields", {})
        return {
            "title": fields.get("summary"),
            "body": fields.get("description") or "",
            "labels": fields.get("labels", []),
            "status": self._canonical_status(fields),
        }

    def get_comments(self, id: str) -> list:
        data = self._request("GET", f"/issue/{id}/comment")
        return data.get("comments", [])

    def _epic_link_field_number(self) -> str:
        m = re.search(r"(\d+)$", self._epic_link_field)
        return m.group(1) if m else self._epic_link_field

    def get_children(self, epic_id: str) -> list:
        jql = (
            f'cf[{self._epic_link_field_number()}] = "{_jql_escape(epic_id)}" '
            f"AND project={self._project_key}"
        )
        issues = self._search_all(jql)
        results = []
        for issue in issues:
            fields = issue.get("fields", {})
            results.append({
                "id": issue.get("key"),
                "status": self._canonical_status(fields),
                "labels": fields.get("labels", []),
            })
        return results

    def set_status(self, id: str, canonical: str) -> None:
        target_name = self._canonical_to_name.get(canonical, canonical)
        data = self._request("GET", f"/issue/{id}/transitions")
        match = next(
            (t for t in data.get("transitions", [])
             if (t.get("to") or {}).get("name", "").casefold() == target_name.casefold()),
            None,
        )
        if not match:
            print(
                f"jira: no transition to status {target_name!r} for {id}; leaving unchanged",
                file=sys.stderr,
            )
            return
        self._request("POST", f"/issue/{id}/transitions",
                       json_body={"transition": {"id": match["id"]}})

    def _current_labels(self, id: str) -> set:
        data = self._request("GET", f"/issue/{id}", params={"fields": "labels"})
        return set((data.get("fields") or {}).get("labels", []))

    def add_label(self, id: str, name: str) -> None:
        """Read-then-write the full labels array (Jira Server/DC v2 has no atomic
        add-one-label operation via `fields`). Last writer wins: a label change made by
        another actor between the GET and PUT here is silently overwritten."""
        labels = self._current_labels(id)
        labels.add(name)
        self._request("PUT", f"/issue/{id}", json_body={"fields": {"labels": sorted(labels)}})

    def remove_label(self, id: str, name: str) -> None:
        """Same read-then-write, last-writer-wins caveat as `add_label` above."""
        labels = self._current_labels(id)
        labels.discard(name)
        self._request("PUT", f"/issue/{id}", json_body={"fields": {"labels": sorted(labels)}})

    def upsert_comment(self, id: str, marker: str, body: str) -> None:
        data = self._request("GET", f"/issue/{id}/comment")
        existing = next(
            (c for c in data.get("comments", []) if marker in (c.get("body") or "")), None,
        )
        if existing:
            self._request("PUT", f"/issue/{id}/comment/{existing['id']}", json_body={"body": body})
        else:
            self._request("POST", f"/issue/{id}/comment", json_body={"body": body})

    def create_item(self, title: str, body: str, labels: list | None = None) -> str:
        fields = {
            "project": {"key": self._project_key},
            "summary": title,
            "description": body,
            "issuetype": {"name": self._issue_type},
        }
        if labels:
            fields["labels"] = list(labels)
        data = self._request("POST", "/issue", json_body={"fields": fields})
        return data.get("key", "")

    def resolve_item(self, id: str) -> None:
        self.set_status(id, "done")

    def get_status_limits(self) -> dict:
        """WIP limits come from adapter config (parent spec Sec 5.4), not Jira -- Jira has no
        per-status limit concept."""
        return {}

    def get_rate_budget(self) -> dict:
        """Jira Server/DC exposes no standard rate-budget endpoint (parent spec Sec 5.1: no-op)."""
        return {"remaining": None, "reset": None, "used": None, "limit": None}
