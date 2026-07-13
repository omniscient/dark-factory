# Implementation Plan: Jira Server/DC Tracker Adapter, Fixtures, and Shared Contract Suite

**Issue:** omniscient/dark-factory#251
**Spec:** `docs/superpowers/specs/2026-07-13-jira-server-dc-tracker-design.md`
**Parent epic:** omniscient/dark-factory#202
**Depends on (implementation dispatch only):** omniscient/dark-factory#250 (OPEN, `needs-discussion`,
unmerged) — per `CLAUDE.md` conventions this gates dispatch of the `Fix issue #251` workflow, not
this plan.

---

## Goal

Implement `JiraTracker` (`scripts/factory_core/providers/tracker/jira.py`), a `Tracker`-conformant
adapter for Jira Server/Data Center's REST API v2 + JQL search, and author the shared,
implementation-agnostic behavioral contract suite (`tests/test_tracker_contract.py`) that the
provider-abstraction design doc calls for but which does not exist yet — run against both
`JiraTracker` and `GitHubTracker`. Both land together per the issue's acceptance criteria.

**Standalone by design:** this ticket does **not** touch `providers/__init__.py` (the `_TRACKERS`
registry — #250's scope, unmerged), `scripts/factory_core/providers/tracker/base.py` (the frozen
`Tracker` ABC), `scripts/factory_core/providers/tracker/github.py`, or `epic_autopilot.py`. See
spec Q3/A3 and Q4/A4.

## Architecture

`scripts/factory_core/providers/tracker/jira.py` mirrors `github.py`'s structure: one class,
`JiraTracker(Tracker)`, with all I/O funneled through a single private seam, `_request()`, the one
method the tests monkeypatch (the direct analog of `test_provider_tracker_parity.py`'s
`monkeypatch.setattr(subprocess, "run", fake)` idiom, per spec Q1/A1).

**Instantiability from Task 1 onward.** `Tracker` (`base.py`) declares all ten non-degradable
methods `@abstractmethod`. Because every task's tests instantiate `JiraTracker()` directly
(there is no separate "wire it up" task at the end), Task 1 defines a stub body
(`raise NotImplementedError`) for all ten abstract methods alongside `__init__`/`_request` —
this makes the class concrete (instantiable) starting at Task 1, not just once every method has
its real implementation at Task 10. Each of Tasks 4–10 then *replaces* its stub with the real
implementation (TDD step "verify it fails" expects `NotImplementedError` from the stub, not
`AttributeError`, since the method already exists).

**Plan-phase decisions** (spec's [Open Questions](../specs/2026-07-13-jira-server-dc-tracker-design.md) deferred these three):

1. **Auth scheme: `Authorization: Bearer $JIRA_TOKEN`.** The spec's `required_env()` list
   (`JIRA_BASE_URL`, `JIRA_PROJECT_KEY`, `JIRA_TOKEN`, `JIRA_EPIC_LINK_FIELD`) has no username
   field, which rules out Basic auth (needs a username + token pair). Jira Server/DC 8.14+
   supports Personal Access Tokens via Bearer auth — one token, matching the required-env shape
   exactly.
2. **Comments via the dedicated `/issue/{key}/comment` sub-resource**, not `?expand=comment` — a
   separate resource keeps `get_comments()` a single, simply-parsed call (`data["comments"]`)
   instead of unpacking a nested `expand` envelope, and `upsert_comment()` needs the same
   resource for its GET-scan-then-PUT/POST idiom anyway.
3. **Epic-Link JQL custom-field syntax: `cf[<number>]`.** `JIRA_EPIC_LINK_FIELD` arrives as
   `customfield_10008` (spec Assumptions); a small helper regexes out the trailing digits
   (`10008`) to build `cf[10008] = "<epic key>"`, Jira JQL's documented custom-field-by-number
   syntax.
4. **`create_item`'s Jira `issuetype` field: a new optional env var, `JIRA_ISSUE_TYPE`, default
   `"Task"`.** Jira's `POST /issue` requires an `issuetype`; this is not one of the four
   spec-mandated `required_env()` entries (it has a safe default so a minimal config still
   works), so it is a plain `os.environ.get(...)` read, not added to `required_env()`.

**Canonical status mapping** (`_CANONICAL_TO_JIRA_STATUS`, built at `__init__` time — not a module
constant, unlike `GitHubTracker`'s `_CANONICAL_STATUS_NAMES` — because Jira status *names* are
operator-configured per project, not fixed literals):

```python
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
```

`JiraTracker.__init__` reads each `FACTORY_STATUS_*` env var (falling back to
`_CANONICAL_DEFAULT_NAME`) into `self._canonical_to_name`, and builds the case-insensitive reverse
map `self._name_to_canonical` for parsing Jira responses back to canonical statuses.

Reading `FACTORY_STATUS_*` at construction time (not module import time, unlike `identity.py`'s
`STATUS` dict) matters for testability: `tests/conftest.py` strips all `FACTORY_*` env at *module
import* to keep the suite hermetic across instances — a construction-time read means
`monkeypatch.setenv(...)` before `JiraTracker()` still works inside a test.

## Tech Stack

Python stdlib only — `urllib.request`/`urllib.error`/`urllib.parse`, `json`, `re`, `os`, `sys`.
No new pip dependency (`requests`/`httpx` rejected; no `vcrpy`/`responses` — hand-rolled JSON
fixtures via monkeypatch instead, per spec Q1/A1). `pytest` for tests (already the repo's only
test dependency).

---

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/providers/tracker/jira.py` | New — `JiraTracker` adapter |
| `tests/test_provider_tracker_jira.py` | New — Jira-specific unit tests: request shape, JQL correctness, transition-id selection, fail-soft behavior |
| `tests/fixtures/jira/search_result.json` | New — synthetic JQL search page (2 issues) |
| `tests/fixtures/jira/issue.json` | New — synthetic single-issue detail |
| `tests/fixtures/jira/transitions.json` | New — synthetic transitions list covering all 7 canonical statuses |
| `tests/fixtures/jira/comments.json` | New — synthetic comment thread (1 marker comment) |
| `tests/fixtures/jira/epic_children.json` | New — synthetic Epic-Link JQL result (2 children) |
| `tests/test_jira_fixtures.py` | New — validates the fixture files themselves (valid JSON, expected keys, no live secrets/URLs) |
| `tests/test_tracker_contract.py` | New — shared behavioral contract suite, parametrized over `GitHubTracker` and `JiraTracker` |

No existing file is modified by this ticket.

---

## Memory Context Applied

Four accumulated-memory lessons (all from this ticket's own refine pass, `architecture.md`) are
baked into the task steps below, not left as a separate advisory section:

1. **`[AVOID]` stdlib-only HTTP, hand-rolled fixtures:** Task 1's `_request()` uses
   `urllib.request` exclusively; every Jira-specific test (Tasks 1, 4–11) monkeypatches
   `JiraTracker._request` (or, for Task 1's own low-level test, `urllib.request.urlopen`) with
   canned JSON — no `requests`/`vcrpy`/`responses` anywhere in this plan.
2. **`[AVOID]` narrow per-implementation reading under-delivers:** Tasks 12–13 author the missing
   *shared* `tests/test_tracker_contract.py` suite (parametrized over both `GitHubTracker` and
   `JiraTracker`), not a Jira-only fixture file.
3. **`[AVOID]` don't assume #250 landed:** Task 2's `required_env()` is a plain classmethod on
   `JiraTracker` with no `@abstractmethod`/base dependency; no task in this plan touches
   `providers/__init__.py` or `base.py`.
4. **`[AVOID]` `get_children()` adapter-neutral shape:** Task 6 returns
   `[{"id": str, "status": <canonical>, "labels": [str, ...]}, ...]` — never GitHub's raw GraphQL
   envelope.

**For the implement-phase agent (not a step in this plan, which only produces the plan document):**
per the `codebase-patterns.md` `[PATTERN]` memory (issue #42), this plan and its spec must be
copied from this `refine/issue-251-...` branch onto the `feat/issue-251-...` branch and committed
there — they do not transfer automatically.

---

## Task 1: `JiraTracker._request` — the single HTTP seam

**Files:** `scripts/factory_core/providers/tracker/jira.py` (new), `tests/test_provider_tracker_jira.py` (new)

1. Write the failing test:

```python
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest


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
```

2. Verify it fails (module doesn't exist yet):

```bash
python -m pytest tests/test_provider_tracker_jira.py -v
```

Expected output: `ModuleNotFoundError: No module named 'factory_core.providers.tracker.jira'` in
every test.

3. Implement `scripts/factory_core/providers/tracker/jira.py`:

```python
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


class JiraTracker(Tracker):
    def __init__(self):
        self._base_url = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
        self._project_key = os.environ.get("JIRA_PROJECT_KEY", "")
        self._token = os.environ.get("JIRA_TOKEN", "")
        self._epic_link_field = os.environ.get("JIRA_EPIC_LINK_FIELD", "")
        self._issue_type = os.environ.get("JIRA_ISSUE_TYPE", "Task")
        self._canonical_to_name = {
            canonical: os.environ.get(env_name, _CANONICAL_DEFAULT_NAME[canonical])
            for canonical, env_name in _CANONICAL_TO_ENV.items()
        }
        self._name_to_canonical = {
            name.casefold(): canonical for canonical, name in self._canonical_to_name.items()
        }

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
            with urllib.request.urlopen(req) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"jira: {method} {path} failed ({e.code}): {detail}") from e
        if not body:
            return {}
        return json.loads(body)

    # --- Stubs for the Tracker ABC's ten abstract methods -----------------
    # `Tracker` marks all ten `@abstractmethod`, so JiraTracker is not
    # instantiable until every one has a body. Tasks 4-10 replace each stub
    # below with its real implementation, one method per task; leaving them
    # unimplemented here (rather than omitting them) is what makes
    # `JiraTracker()` constructible starting at this task, since every later
    # task's tests instantiate it directly.
    def list_work_items(self, statuses: list, labels: list | None = None) -> list:
        raise NotImplementedError

    def get_item(self, id: str) -> dict:
        raise NotImplementedError

    def get_comments(self, id: str) -> list:
        raise NotImplementedError

    def get_children(self, epic_id: str) -> list:
        raise NotImplementedError

    def set_status(self, id: str, canonical: str) -> None:
        raise NotImplementedError

    def add_label(self, id: str, name: str) -> None:
        raise NotImplementedError

    def remove_label(self, id: str, name: str) -> None:
        raise NotImplementedError

    def upsert_comment(self, id: str, marker: str, body: str) -> None:
        raise NotImplementedError

    def create_item(self, title: str, body: str, labels: list | None = None) -> str:
        raise NotImplementedError

    def resolve_item(self, id: str) -> None:
        raise NotImplementedError
```

4. Verify it passes:

```bash
python -m pytest tests/test_provider_tracker_jira.py -v
```

Expected output: `3 passed`. (`JiraTracker()` is now instantiable — all ten `Tracker`
abstract methods have a stub body, even though nine of them still raise `NotImplementedError`
until their own task replaces the stub.)

5. Commit:

```bash
git add scripts/factory_core/providers/tracker/jira.py tests/test_provider_tracker_jira.py
git commit -m "feat(tracker): add JiraTracker _request HTTP seam with Bearer auth"
```

---

## Task 2: `required_env()` and canonical status maps

**Files:** `scripts/factory_core/providers/tracker/jira.py`, `tests/test_provider_tracker_jira.py`

1. Add the failing test (append to `tests/test_provider_tracker_jira.py`):

```python
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
```

2. Verify it fails:

```bash
python -m pytest tests/test_provider_tracker_jira.py -v -k "required_env or status_maps"
```

Expected output: `AttributeError: type object 'JiraTracker' has no attribute 'required_env'`.

3. Add the classmethod to `JiraTracker` (below `__init__`):

```python
    @classmethod
    def required_env(cls) -> list[str]:
        return ["JIRA_BASE_URL", "JIRA_PROJECT_KEY", "JIRA_TOKEN", "JIRA_EPIC_LINK_FIELD"]
```

(The status-map assertions already pass against Task 1's `__init__` — no further implementation
needed for the second test.)

4. Verify both pass:

```bash
python -m pytest tests/test_provider_tracker_jira.py -v -k "required_env or status_maps"
```

Expected output: `2 passed`.

5. Commit:

```bash
git add scripts/factory_core/providers/tracker/jira.py tests/test_provider_tracker_jira.py
git commit -m "feat(tracker): add JiraTracker.required_env() classmethod"
```

---

## Task 3: Synthetic Jira fixture files

**Files:** `tests/fixtures/jira/search_result.json`, `tests/fixtures/jira/issue.json`,
`tests/fixtures/jira/transitions.json`, `tests/fixtures/jira/comments.json`,
`tests/fixtures/jira/epic_children.json` (all new), `tests/test_jira_fixtures.py` (new)

1. Write the failing test:

```python
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jira"


def test_all_fixture_files_are_valid_json_with_expected_top_level_keys():
    expected = {
        "search_result.json": "issues",
        "issue.json": "fields",
        "transitions.json": "transitions",
        "comments.json": "comments",
        "epic_children.json": "issues",
    }
    for filename, key in expected.items():
        data = json.loads((FIXTURES / filename).read_text())
        assert key in data, f"{filename} missing top-level '{key}'"


def test_no_live_jira_base_url_or_secret_in_fixtures():
    for path in FIXTURES.glob("*.json"):
        text = path.read_text()
        assert "atlassian.net" not in text
        assert "Bearer " not in text
```

2. Verify it fails (directory doesn't exist yet):

```bash
python -m pytest tests/test_jira_fixtures.py -v
```

Expected output: `FileNotFoundError` for every fixture read.

3. Create `tests/fixtures/jira/search_result.json`:

```json
{
  "issues": [
    {
      "key": "PROJ-1",
      "fields": {
        "summary": "First ticket",
        "description": "Ticket body text.",
        "labels": ["ready-for-agent"],
        "status": {"name": "Ready"}
      }
    },
    {
      "key": "PROJ-2",
      "fields": {
        "summary": "Second ticket",
        "description": "",
        "labels": [],
        "status": {"name": "Done"}
      }
    }
  ]
}
```

4. Create `tests/fixtures/jira/issue.json`:

```json
{
  "key": "PROJ-1",
  "fields": {
    "summary": "First ticket",
    "description": "Ticket body text.",
    "labels": ["ready-for-agent"],
    "status": {"name": "Ready"}
  }
}
```

5. Create `tests/fixtures/jira/transitions.json`:

```json
{
  "transitions": [
    {"id": "11", "name": "Move to Backlog", "to": {"id": "1", "name": "Backlog"}},
    {"id": "21", "name": "Move to Ready", "to": {"id": "2", "name": "Ready"}},
    {"id": "31", "name": "Start Progress", "to": {"id": "3", "name": "In progress"}},
    {"id": "41", "name": "Send to Review", "to": {"id": "4", "name": "In review"}},
    {"id": "51", "name": "Block", "to": {"id": "5", "name": "Blocked"}},
    {"id": "61", "name": "Resolve", "to": {"id": "6", "name": "Done"}},
    {"id": "71", "name": "Refine", "to": {"id": "7", "name": "Refined"}}
  ]
}
```

6. Create `tests/fixtures/jira/comments.json`:

```json
{
  "comments": [
    {"id": 501, "body": "<!-- factory:refinement -->\nOriginal comment body."}
  ]
}
```

7. Create `tests/fixtures/jira/epic_children.json`:

```json
{
  "issues": [
    {
      "key": "PROJ-10",
      "fields": {
        "summary": "Child A",
        "description": "",
        "labels": ["ready-for-agent"],
        "status": {"name": "Ready"}
      }
    },
    {
      "key": "PROJ-11",
      "fields": {
        "summary": "Child B",
        "description": "",
        "labels": [],
        "status": {"name": "In progress"}
      }
    }
  ]
}
```

8. Verify it passes:

```bash
python -m pytest tests/test_jira_fixtures.py -v
```

Expected output: `2 passed`.

9. Commit:

```bash
git add tests/fixtures/jira/ tests/test_jira_fixtures.py
git commit -m "test(tracker): add synthetic Jira fixture files for contract/unit tests"
```

---

## Task 4: `list_work_items`

**Files:** `scripts/factory_core/providers/tracker/jira.py`, `tests/test_provider_tracker_jira.py`

1. Add the failing tests:

```python
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
```

Add `FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jira"` near the top of
`tests/test_provider_tracker_jira.py` (alongside the existing `sys.path.insert`/imports), and
`import json` if not already present.

2. Verify it fails:

```bash
python -m pytest tests/test_provider_tracker_jira.py -v -k list_work_items
```

Expected output: `NotImplementedError` (raised by Task 1's stub body for `list_work_items`).

3. Replace the `list_work_items` stub on `JiraTracker` with its real implementation:

```python
    def list_work_items(self, statuses: list, labels: list | None = None) -> list:
        names = [self._canonical_to_name.get(s, s) for s in statuses]
        quoted = ",".join(f'"{n}"' for n in names)
        clauses = [f"project={self._project_key}", f"status IN({quoted})"]
        for label in (labels or []):
            clauses.append(f'labels = "{label}"')
        jql = " AND ".join(clauses)
        data = self._request("GET", "/search", params={"jql": jql})
        return [self._issue_to_item(issue) for issue in data.get("issues", [])]

    def _issue_to_item(self, issue: dict) -> dict:
        fields = issue.get("fields", {})
        status_name = (fields.get("status") or {}).get("name", "")
        return {
            "id": issue.get("key"),
            "title": fields.get("summary"),
            "labels": fields.get("labels", []),
            "status": self._name_to_canonical.get(status_name.casefold(), status_name),
        }
```

4. Verify it passes:

```bash
python -m pytest tests/test_provider_tracker_jira.py -v -k list_work_items
```

Expected output: `1 passed`.

5. Commit:

```bash
git add scripts/factory_core/providers/tracker/jira.py tests/test_provider_tracker_jira.py
git commit -m "feat(tracker): add JiraTracker.list_work_items JQL discovery"
```

---

## Task 5: `get_item` and `get_comments`

**Files:** `scripts/factory_core/providers/tracker/jira.py`, `tests/test_provider_tracker_jira.py`

1. Add the failing tests:

```python
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
```

2. Verify it fails:

```bash
python -m pytest tests/test_provider_tracker_jira.py -v -k "get_item or get_comments"
```

Expected output: `NotImplementedError` (raised by Task 1's stub bodies for `get_item`/`get_comments`).

3. Replace both stubs with their real implementations:

```python
    def get_item(self, id: str) -> dict:
        issue = self._request("GET", f"/issue/{id}")
        fields = issue.get("fields", {})
        status_name = (fields.get("status") or {}).get("name", "")
        return {
            "title": fields.get("summary"),
            "body": fields.get("description") or "",
            "labels": fields.get("labels", []),
            "status": self._name_to_canonical.get(status_name.casefold(), status_name),
        }

    def get_comments(self, id: str) -> list:
        data = self._request("GET", f"/issue/{id}/comment")
        return data.get("comments", [])
```

4. Verify it passes:

```bash
python -m pytest tests/test_provider_tracker_jira.py -v -k "get_item or get_comments"
```

Expected output: `2 passed`.

5. Commit:

```bash
git add scripts/factory_core/providers/tracker/jira.py tests/test_provider_tracker_jira.py
git commit -m "feat(tracker): add JiraTracker.get_item and get_comments"
```

---

## Task 6: `get_children` — adapter-neutral Epic-Link discovery

**Files:** `scripts/factory_core/providers/tracker/jira.py`, `tests/test_provider_tracker_jira.py`

1. Add the failing test:

```python
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
```

2. Verify it fails:

```bash
python -m pytest tests/test_provider_tracker_jira.py -v -k get_children
```

Expected output: `NotImplementedError` (raised by Task 1's stub body for `get_children`).

3. Replace the stub:

```python
    def _epic_link_field_number(self) -> str:
        m = re.search(r"(\d+)$", self._epic_link_field)
        return m.group(1) if m else self._epic_link_field

    def get_children(self, epic_id: str) -> list:
        jql = f'cf[{self._epic_link_field_number()}] = "{epic_id}" AND project={self._project_key}'
        data = self._request("GET", "/search", params={"jql": jql})
        results = []
        for issue in data.get("issues", []):
            fields = issue.get("fields", {})
            status_name = (fields.get("status") or {}).get("name", "")
            results.append({
                "id": issue.get("key"),
                "status": self._name_to_canonical.get(status_name.casefold(), status_name),
                "labels": fields.get("labels", []),
            })
        return results
```

4. Verify it passes:

```bash
python -m pytest tests/test_provider_tracker_jira.py -v -k get_children
```

Expected output: `2 passed`.

5. Commit:

```bash
git add scripts/factory_core/providers/tracker/jira.py tests/test_provider_tracker_jira.py
git commit -m "feat(tracker): add JiraTracker.get_children Epic-Link JQL discovery"
```

---

## Task 7: `set_status` — transition-ID lookup with fail-soft

**Files:** `scripts/factory_core/providers/tracker/jira.py`, `tests/test_provider_tracker_jira.py`

1. Add the failing tests:

```python
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
```

2. Verify it fails:

```bash
python -m pytest tests/test_provider_tracker_jira.py -v -k set_status
```

Expected output: `NotImplementedError` (raised by Task 1's stub body for `set_status`).

3. Replace the stub:

```python
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
```

4. Verify it passes:

```bash
python -m pytest tests/test_provider_tracker_jira.py -v -k set_status
```

Expected output: `2 passed`.

5. Commit:

```bash
git add scripts/factory_core/providers/tracker/jira.py tests/test_provider_tracker_jira.py
git commit -m "feat(tracker): add JiraTracker.set_status with fail-soft transition lookup"
```

---

## Task 8: `add_label` / `remove_label` — read-modify-write

**Files:** `scripts/factory_core/providers/tracker/jira.py`, `tests/test_provider_tracker_jira.py`

1. Add the failing tests:

```python
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
```

2. Verify it fails:

```bash
python -m pytest tests/test_provider_tracker_jira.py -v -k "add_label or remove_label"
```

Expected output: `NotImplementedError` (raised by Task 1's stub bodies for `add_label`/`remove_label`).

3. Replace both stubs:

```python
    def _current_labels(self, id: str) -> set:
        data = self._request("GET", f"/issue/{id}", params={"fields": "labels"})
        return set((data.get("fields") or {}).get("labels", []))

    def add_label(self, id: str, name: str) -> None:
        labels = self._current_labels(id)
        labels.add(name)
        self._request("PUT", f"/issue/{id}", json_body={"fields": {"labels": sorted(labels)}})

    def remove_label(self, id: str, name: str) -> None:
        labels = self._current_labels(id)
        labels.discard(name)
        self._request("PUT", f"/issue/{id}", json_body={"fields": {"labels": sorted(labels)}})
```

4. Verify it passes:

```bash
python -m pytest tests/test_provider_tracker_jira.py -v -k "add_label or remove_label"
```

Expected output: `2 passed`.

5. Commit:

```bash
git add scripts/factory_core/providers/tracker/jira.py tests/test_provider_tracker_jira.py
git commit -m "feat(tracker): add JiraTracker.add_label/remove_label read-modify-write"
```

---

## Task 9: `upsert_comment` — idempotent marker scan

**Files:** `scripts/factory_core/providers/tracker/jira.py`, `tests/test_provider_tracker_jira.py`

1. Add the failing tests:

```python
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
```

2. Verify it fails:

```bash
python -m pytest tests/test_provider_tracker_jira.py -v -k upsert_comment
```

Expected output: `NotImplementedError` (raised by Task 1's stub body for `upsert_comment`).

3. Replace the stub:

```python
    def upsert_comment(self, id: str, marker: str, body: str) -> None:
        data = self._request("GET", f"/issue/{id}/comment")
        existing = next(
            (c for c in data.get("comments", []) if marker in (c.get("body") or "")), None,
        )
        if existing:
            self._request("PUT", f"/issue/{id}/comment/{existing['id']}", json_body={"body": body})
        else:
            self._request("POST", f"/issue/{id}/comment", json_body={"body": body})
```

4. Verify it passes:

```bash
python -m pytest tests/test_provider_tracker_jira.py -v -k upsert_comment
```

Expected output: `2 passed`.

5. Commit:

```bash
git add scripts/factory_core/providers/tracker/jira.py tests/test_provider_tracker_jira.py
git commit -m "feat(tracker): add JiraTracker.upsert_comment idempotent marker scan"
```

---

## Task 10: `create_item` / `resolve_item`

**Files:** `scripts/factory_core/providers/tracker/jira.py`, `tests/test_provider_tracker_jira.py`

1. Add the failing tests:

```python
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
```

2. Verify it fails:

```bash
python -m pytest tests/test_provider_tracker_jira.py -v -k "create_item or resolve_item"
```

Expected output: `NotImplementedError` (raised by Task 1's stub bodies for `create_item`/`resolve_item`).

3. Replace both stubs:

```python
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
```

4. Verify it passes:

```bash
python -m pytest tests/test_provider_tracker_jira.py -v -k "create_item or resolve_item"
```

Expected output: `2 passed`.

5. Commit:

```bash
git add scripts/factory_core/providers/tracker/jira.py tests/test_provider_tracker_jira.py
git commit -m "feat(tracker): add JiraTracker.create_item/resolve_item"
```

---

## Task 11: Degradable ops — `get_status_limits` / `get_rate_budget`

**Files:** `scripts/factory_core/providers/tracker/jira.py`, `tests/test_provider_tracker_jira.py`

1. Add the tests (already green, by design — see step 2):

```python
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
```

2. Run them — there is no red step here, by design:

```bash
python -m pytest tests/test_provider_tracker_jira.py -v -k "status_limits or rate_budget or opaque_ids"
```

Expected output: `3 passed`. All three already pass unmodified — `get_status_limits`/
`get_rate_budget` degrade correctly via the `Tracker` ABC's inherited defaults (Task 1's
`JiraTracker` doesn't override them), and `set_status` never calls `int()` on an id. This task
is documentation, not a bug fix: it adds explicit overrides so the safe-default behavior is
visible on `JiraTracker` itself (with the Jira-specific rationale in each docstring) rather than
implicit via inheritance — step 3 below must not change any assertion's outcome.

3. Add explicit overrides on `JiraTracker` (same values as the inherited defaults, but explicit
   per spec Requirement 1 — WIP limits are adapter-config-driven, not Jira-derived, and Jira
   Server/DC has no standard rate-budget endpoint, so both are documented no-ops rather than
   silently relying on the base class):

```python
    def get_status_limits(self) -> dict:
        """WIP limits come from adapter config (parent spec Sec 5.4), not Jira -- Jira has no
        per-status limit concept."""
        return {}

    def get_rate_budget(self) -> dict:
        """Jira Server/DC exposes no standard rate-budget endpoint (parent spec Sec 5.1: no-op)."""
        return {"remaining": None, "reset": None, "used": None, "limit": None}
```

4. Verify all pass:

```bash
python -m pytest tests/test_provider_tracker_jira.py -v
```

Expected output: all tests in the file `passed` (accumulated from Tasks 1–11).

5. Commit:

```bash
git add scripts/factory_core/providers/tracker/jira.py tests/test_provider_tracker_jira.py
git commit -m "feat(tracker): document JiraTracker degradable-op defaults explicitly"
```

---

## Task 12: Shared contract suite — fixture controllers and behavioral assertions

**Files:** `tests/test_tracker_contract.py` (new)

This task is one unit because the fixture controllers and the assertions that exercise them
are only independently meaningful together — a controller with no behavioral test is untested
scaffolding, and a behavioral test has nothing to run against without its controller. There is
no smaller failing increment for a brand-new file that defines its own fixtures: the file simply
does not exist until step 1, so "write failing test" and "define the controllers it needs" happen
in the same edit.

1. Write the full file in one pass — fixture controllers, the parametrized `tracker_and_controller`
   fixture, and every behavioral assertion (Requirement 10 / spec Q2/A2: canonical status
   vocabulary, `list_work_items` status+label filtering, marker-comment idempotent upsert, label
   add/remove, safe no-op on an unreachable transition/target, `get_children` list/empty):

```python
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
```

Note: `monkeypatch.setattr(JiraTracker, "_request", self._request)` would be a bug here — a
**bound method** (`self._request`, already bound to the controller) is not itself a descriptor,
so assigning it as a class attribute does *not* get re-bound to the `JiraTracker` instance on
attribute access; `tracker._request(...)` would call the controller's bound method verbatim,
silently swallowing `tracker` as if it were `_request`'s first declared parameter and leaving the
real first argument unfilled (`TypeError: missing 1 required positional argument`). The
`__init__` above avoids this by monkeypatching a **plain closure function**, `_request`
(a real descriptor, like any `def`), which correctly receives the `JiraTracker` instance as
`tracker_instance` on every call and forwards the rest to `controller._dispatch(...)` — the
same pattern every per-task `fake_request(self, method, path, ...)` in Tasks 1–11 already uses
correctly (a plain `def`, not a bound method).

2. Verify it fails (the module doesn't exist yet):

```bash
python -m pytest tests/test_tracker_contract.py -v
```

Expected output: `ModuleNotFoundError: No module named 'tests.test_tracker_contract'`-style
collection error — there is no prior version of this file to compare against.

3. Verify it passes once the file above is created:

```bash
python -m pytest tests/test_tracker_contract.py -v
```

Expected output: `12 passed` (6 test functions x 2 params: `github`, `jira`).

4. Commit:

```bash
git add tests/test_tracker_contract.py
git commit -m "test(tracker): add shared behavioral contract suite for GitHub and Jira trackers"
```

---

## Task 13: Full-suite verification

**Files:** none (verification only)

1. Run the complete suite:

```bash
python -m pytest tests/ -v
```

Expected output: all tests pass, including every pre-existing `test_provider_tracker_parity.py`
and `test_provider_tracker_base.py` test **unchanged** (GitHub default-path parity remains green —
acceptance criterion 6), plus all new Jira/contract tests from Tasks 1–12.

2. Confirm no out-of-scope files changed:

```bash
git diff origin/main HEAD --stat
```

Expected output: only files listed in the [File Structure](#file-structure) table above, plus this
plan document and its spec (already committed on this branch by the refine phase).

3. Confirm no live Jira secrets/URLs anywhere in the new files:

```bash
git grep -n "atlassian.net" -- scripts/factory_core/providers/tracker/jira.py tests/fixtures/jira/ tests/test_provider_tracker_jira.py tests/test_tracker_contract.py
```

Expected output: no matches (exit code 1).

4. No commit for this task — it is verification-only. If any check fails, return to the relevant
   earlier task and fix it there (with its own commit) rather than amending.

---

## Acceptance Criteria Traceability

| Acceptance criterion | Satisfied by |
|---|---|
| `JiraTracker` passes the same contract suite as `GitHubTracker` using recorded fixtures | Tasks 1–12 |
| Missing transition edges log actionable failures and leave status unchanged | Task 7, Task 12 |
| Marker-comment re-runs update rather than spam | Task 9, Task 12 |
| Epic-Link child discovery returns independently runnable work items | Task 6, Task 12 |
| No live Jira secrets or URLs are committed | Task 3 (synthetic fixtures), Task 13 step 3 |
| GitHub default-path parity remains green | Task 13 step 1 |

## Out of Scope (unchanged from spec)

- `providers/__init__.py` (`_TRACKERS` registry, `FACTORY_TRACKER` selection) — #250's scope.
- `scripts/factory_core/providers/tracker/base.py` (the `Tracker` ABC itself).
- `scripts/factory_core/providers/tracker/github.py` and its existing parity tests.
- `epic_autopilot.py` (disabled by default; `get_children()` shape divergence is a documented,
  separately-tracked follow-up per spec's [Known Limitation](../specs/2026-07-13-jira-server-dc-tracker-design.md#known-limitation-get_children-shape-divergence)).
- Live Jira environment certification (explicit non-goal; deferred to the dependent validation
  ticket).
