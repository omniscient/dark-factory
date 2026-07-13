# Implementation Plan: Provider Abstraction — Tracker/CodeHost Contracts (Step 1)

**Issue:** omniscient/dark-factory#248
**Spec:** `docs/superpowers/specs/2026-07-11-provider-tracker-codehost-contracts-design.md`
**Depends on:** omniscient/dark-factory#203 (merged — `docs/provider-abstraction-design.md`, the parent
spec this ticket implements step 1 of)

---

## Goal

Introduce the `Tracker` and `CodeHost` provider seams with **zero behavior change**: new
`scripts/factory_core/providers/` package with ABCs (parent spec §5.1/§6.1), `GitHubTracker`/
`GitHubCodeHost` reference adapters that emit **argv-identical** `gh`/`gh api`/`gh api graphql`
calls to what runs today, golden-argv parity tests as the safety net for later steps, and thin
CLI entry points. No bash/YAML call site (`scheduler.sh`, `entrypoint.sh`, `smoke_gate.sh`, the
run DAG) is touched — that is spec step 2, a separate ticket. No `JiraTracker`/`GitLabCodeHost`.

## Architecture

```
scripts/factory_core/providers/
  __init__.py          # get_tracker(), get_codehost() — unconditionally return GitHub adapters
  tracker/
    __init__.py
    base.py             # Tracker ABC (11 ops); get_status_limits/get_rate_budget are
                         #   concrete (non-abstract) safe-default methods — "degradable"
                         #   per parent spec principle 4 — overridden by GitHubTracker.
    github.py           # GitHubTracker
  codehost/
    __init__.py
    base.py              # CodeHost ABC (11 abstract ops)
    github.py            # GitHubCodeHost
  cli.py                 # thin CLI: `python3 scripts/factory_core/providers/cli.py tracker|codehost <verb>`
```

`scripts/factory_core/board.py` keeps its exact three public signatures
(`find_board_item(issue_num: int)`, `set_board_status(issue_num: int, option_id: str)`,
`post_or_update_comment(issue_num: int, marker: str, body: str)`) and exact `gh` argv. Its
`gh project item-list` / `gh project item-edit` calls are split into two private helpers
(`_find_item_by_number`, `_item_edit_status`) that `GitHubTracker.set_status`/`get_item` also
call — this is the "shared low-level helper" option the spec's Open Questions section leaves to
this plan (§ Architecture, "Open Questions"). `board.py`'s own public functions become one-line
callers of those helpers, so `scheduler.sh`'s `set_board_status()` wrapper (line 469) and
`factory_core/cli.py`'s `board-move` subcommand need **no changes** and stay green under the
existing `test_factory_core_board.py` suite unmodified.

### Design decisions (implementation-plan-level choices the spec left open)

1. **`Tracker.get_item`** has no single existing call site with the exact field set implied by
   its purpose ("title/body/state/labels/status" — parent spec §5.1). Different bash callers
   fetch different `--json` subsets today (`title,body,labels,comments` in the run DAG's
   fetch-issue node; `state` alone in `scheduler.sh:690`; `body` alone in `scheduler.sh:677`).
   `GitHubTracker.get_item(id, fields=None)` takes an optional GitHub `--json` field list,
   defaulting to `("title", "body", "labels", "comments")` — the richest current shape (the run
   DAG's). This lets one method reproduce any of today's narrower calls without inventing a
   field combination nobody uses. "Status" (the ProjectV2 board field) is not part of
   `gh issue view` at all in any current call site — it stays out of `get_item`'s default and is
   available separately via `get_status_limits`/board lookups, exactly as today.
2. **`Tracker.set_status`/`get_item`-adjacent item lookup must not call `int()`** (spec
   requirement 1) but `board.find_board_item`'s existing comparison is `c.get("number") ==
   issue_num` against a JSON integer. `_find_item_by_number(number: str)` compares
   `str(c.get("number")) == number` (string comparison, not casting the *input*), so it accepts
   both `str(42)` from `board.find_board_item` and a raw opaque string like `"PROJ-123"` (which
   will simply never match any GitHub item — correct behavior for an ID a GitHub board could
   never contain) without ever coercing the caller's ID via `int()`.
3. **`CodeHost` methods with genuinely two live call shapes** (one from `workflows/archon-dark-
   factory.yaml`, relying on cwd/no `--repo`; one from a `factory_core` Python module, passing
   `--repo` explicitly) take an optional `repo: str | None = None` parameter — omitted, the argv
   drops `--repo` (matching the YAML); passed, it's included (matching the Python caller). Both
   real shapes get their own parity test rather than picking one and calling the other
   "close enough." Tracker methods ported from bash (`get_item`, `get_comments`, `get_children`,
   `add_label`, `remove_label`, `create_item`, `resolve_item`) have exactly **one** existing
   call-site shape each (all pass `--repo`/`owner+name` explicitly), so no optional param is
   needed there — `identity.SLUG`/`identity.OWNER`/`identity.REPO` are used unconditionally.
4. **CLI invocation form.** Parent spec §4.2 labels its `python -m factory_core.tracker …` /
   `… codehost …` surface "illustrative." This repo's actual convention (`scripts/factory_core/
   cli.py`, invoked as `python3 "$FACTORY_CORE_CLI" board-move …`, never via `-m`) uses a direct
   script path with subcommands, not package-relative `-m` invocation. `providers/cli.py`
   follows that existing, real convention: `python3 scripts/factory_core/providers/cli.py
   tracker <verb> …` / `… codehost <verb> …` — same dispatch mechanics as `factory_core/cli.py`
   (deferred imports, `argparse` subparsers, `set_defaults(func=...)`), one level deeper for the
   provider-group prefix. This satisfies requirement 6 ("thin CLI entry points... new, additional
   surface") without inventing three new top-level `__main__.py` package shims for a literal `-m`
   match to an explicitly "illustrative" spec surface.
5. **`get_status_limits`/`get_rate_budget` are concrete (non-abstract) ABC methods**, not
   `@abstractmethod` — this is the literal mechanism for "degradable" (parent spec principle 4):
   a future adapter that doesn't override them gets a safe default for free. `GitHubTracker`
   overrides both with genuine working ports per spec Q4/A4 (§ requirement 5).

## Tech Stack

Python 3 (`abc.ABC`, `subprocess`, `re`, `json`), `pytest` + `monkeypatch.setattr(subprocess,
"run", ...)` (the existing `tests/test_factory_core_board.py`/`test_factory_core_rescue.py`
convention). No new dependencies.

---

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/providers/__init__.py` | New — `get_tracker()`, `get_codehost()` |
| `scripts/factory_core/providers/tracker/__init__.py` | New — empty |
| `scripts/factory_core/providers/tracker/base.py` | New — `Tracker` ABC |
| `scripts/factory_core/providers/tracker/github.py` | New — `GitHubTracker` |
| `scripts/factory_core/providers/codehost/__init__.py` | New — empty |
| `scripts/factory_core/providers/codehost/base.py` | New — `CodeHost` ABC |
| `scripts/factory_core/providers/codehost/github.py` | New — `GitHubCodeHost` |
| `scripts/factory_core/providers/cli.py` | New — thin CLI dispatch |
| `scripts/factory_core/board.py` | Modified — extract `_find_item_by_number`/`_item_edit_status` private helpers, zero public-API/behavior change |
| `tests/test_provider_tracker_base.py` | New — ABC shape test |
| `tests/test_provider_codehost_base.py` | New — ABC shape test |
| `tests/test_provider_registry.py` | New — `get_tracker()`/`get_codehost()` test |
| `tests/test_provider_tracker_parity.py` | New — golden-argv parity + opaque-ID tests, `Tracker` |
| `tests/test_provider_codehost_parity.py` | New — golden-argv parity + opaque-ID tests, `CodeHost` |
| `tests/test_provider_cli.py` | New — CLI dispatch test |
| `tests/test_factory_core_board.py` | Unmodified — must stay green (delegation-didn't-break-anything check) |

---

## Memory Context Applied

Two accumulated-memory lessons from `.archon/memory/codebase-patterns.md` are baked into the
tasks below:

1. **Spec/plan branch transfer (issue #42):** the later `implement`-phase agent on
   `feat/issue-248-*` must itself copy this plan and `docs/superpowers/specs/2026-07-11-
   provider-tracker-codehost-contracts-design.md` onto its branch and commit them — not a step
   in this plan (which only produces the plan document on the `refine/` branch), flagged here so
   that agent isn't surprised.
2. **OOS scope check (issue #250):** if a later conformance/OOS pass needs to check whether a
   file this plan lists is genuinely new vs. already on `main`, use `git diff origin/main HEAD --
   <file>` (two-dot), not `git diff origin/main...HEAD -- <file>` (three-dot, which can produce
   false-positive OOS hits for files `main` independently already carries).

No `[AVOID]` memory entries were retrieved for this file set, so no approach is excluded.

---

## Task 1: `Tracker` ABC

**Files:** `scripts/factory_core/providers/__init__.py` (new, empty for now),
`scripts/factory_core/providers/tracker/__init__.py` (new, empty),
`scripts/factory_core/providers/tracker/base.py` (new),
`tests/test_provider_tracker_base.py` (new)

1. Write the failing test:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest


def test_tracker_is_abstract_with_required_ops():
    from factory_core.providers.tracker.base import Tracker

    required = {
        "list_work_items", "get_item", "get_comments", "get_children",
        "set_status", "add_label", "remove_label", "upsert_comment",
        "create_item", "resolve_item",
    }
    assert required.issubset(Tracker.__abstractmethods__)
    with pytest.raises(TypeError):
        Tracker()


def test_tracker_degradable_ops_have_safe_defaults():
    from factory_core.providers.tracker.base import Tracker

    class _Bare(Tracker):
        def list_work_items(self, statuses, labels=None): return []
        def get_item(self, id): return {}
        def get_comments(self, id): return []
        def get_children(self, epic_id): return []
        def set_status(self, id, canonical): pass
        def add_label(self, id, name): pass
        def remove_label(self, id, name): pass
        def upsert_comment(self, id, marker, body): pass
        def create_item(self, title, body, labels=None): return "1"
        def resolve_item(self, id): pass

    bare = _Bare()
    assert bare.get_status_limits() == {}
    assert bare.get_rate_budget() == {"remaining": None, "reset": None, "used": None, "limit": None}
```

2. Verify it fails:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_tracker_base.py -v
```

Expected output: `ModuleNotFoundError: No module named 'factory_core.providers'` in both tests.

3. Implement:

```python
# scripts/factory_core/providers/__init__.py
```

(empty for now — populated in Task 3)

```python
# scripts/factory_core/providers/tracker/__init__.py
```

(empty)

```python
# scripts/factory_core/providers/tracker/base.py
"""Tracker provider contract (parent spec docs/provider-abstraction-design.md §5.1).

IDs are opaque strings everywhere — no int() coercion anywhere in a conforming
implementation. `get_status_limits`/`get_rate_budget` are "degradable" (principle 4):
concrete, non-abstract methods with safe defaults so a minimal adapter needs no
override to have a working low floor.
"""
from abc import ABC, abstractmethod


class Tracker(ABC):
    @abstractmethod
    def list_work_items(self, statuses: list, labels: list | None = None) -> list:
        """Poll-loop discovery: work items whose canonical status is in `statuses`,
        optionally further filtered to items carrying every label in `labels`."""

    @abstractmethod
    def get_item(self, id: str) -> dict:
        """Title/body/labels (+ comments, adapter-dependent) for a single item."""

    @abstractmethod
    def get_comments(self, id: str) -> list:
        """The comment thread for an item."""

    @abstractmethod
    def get_children(self, epic_id: str) -> list:
        """Epic -> children (sub-issues / epic-link issues)."""

    @abstractmethod
    def set_status(self, id: str, canonical: str) -> None:
        """Move an item to one of the seven canonical statuses."""

    @abstractmethod
    def add_label(self, id: str, name: str) -> None:
        ...

    @abstractmethod
    def remove_label(self, id: str, name: str) -> None:
        ...

    @abstractmethod
    def upsert_comment(self, id: str, marker: str, body: str) -> None:
        """Idempotent marker-comment: create if absent, else update in place."""

    @abstractmethod
    def create_item(self, title: str, body: str, labels: list | None = None) -> str:
        """Create a new item (e.g. a regression ticket); returns its opaque id."""

    @abstractmethod
    def resolve_item(self, id: str) -> None:
        """Explicit close-on-merge / Done transition."""

    def get_status_limits(self) -> dict:
        """WIP limits per canonical status. Degradable: safe default is 'no limits known'."""
        return {}

    def get_rate_budget(self) -> dict:
        """Throttle signal for the poll loop. Degradable: safe default is 'unknown'."""
        return {"remaining": None, "reset": None, "used": None, "limit": None}
```

4. Verify it passes:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_tracker_base.py -v
```

Expected output: `2 passed`.

5. Commit:

```bash
git add scripts/factory_core/providers/__init__.py scripts/factory_core/providers/tracker/__init__.py \
        scripts/factory_core/providers/tracker/base.py tests/test_provider_tracker_base.py
git commit -m "feat(providers): add Tracker ABC with degradable get_status_limits/get_rate_budget"
```

---

## Task 2: `CodeHost` ABC

**Files:** `scripts/factory_core/providers/codehost/__init__.py` (new, empty),
`scripts/factory_core/providers/codehost/base.py` (new),
`tests/test_provider_codehost_base.py` (new)

1. Write the failing test:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest


def test_codehost_is_abstract_with_required_ops():
    from factory_core.providers.codehost.base import CodeHost

    required = {
        "remote_url", "find_change_for", "open_change", "update_change_body",
        "mark_ready", "merge_change", "get_change_checks", "get_change_mergeable",
        "get_change_reviews", "get_change_inline_comments", "close_keyword",
    }
    assert required.issubset(CodeHost.__abstractmethods__)
    with pytest.raises(TypeError):
        CodeHost()
```

2. Verify it fails:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_codehost_base.py -v
```

Expected output: `ModuleNotFoundError: No module named 'factory_core.providers.codehost'`.

3. Implement:

```python
# scripts/factory_core/providers/codehost/__init__.py
```

(empty)

```python
# scripts/factory_core/providers/codehost/base.py
"""CodeHost provider contract (parent spec docs/provider-abstraction-design.md §6.1).

Plain git (clone/branch/commit/push/fetch/diff) is host-agnostic and stays inline,
outside this contract (principle 3) — the only git-adjacent method here is
remote_url(). All ids are opaque strings.
"""
from abc import ABC, abstractmethod


class CodeHost(ABC):
    @abstractmethod
    def remote_url(self) -> str:
        """Auth-embedded clone/push URL for this host."""

    @abstractmethod
    def find_change_for(self, branch: str) -> str | None:
        """The open PR/MR id for a branch (or branch prefix), or None."""

    @abstractmethod
    def open_change(self, source: str, target: str, title: str, body: str,
                     draft: bool = False) -> str:
        """Create a PR/MR; returns its opaque id."""

    @abstractmethod
    def update_change_body(self, id: str, body: str) -> None:
        ...

    @abstractmethod
    def mark_ready(self, id: str) -> None:
        """Draft -> ready for review."""

    @abstractmethod
    def merge_change(self, id: str, strategy: str = "merge",
                      delete_branch: bool = True) -> bool:
        ...

    @abstractmethod
    def get_change_checks(self, id: str) -> list:
        """CI check results: [{name, bucket, ...}]."""

    @abstractmethod
    def get_change_mergeable(self, id: str) -> str:
        """Conflict-gate enum, e.g. MERGEABLE / CONFLICTING / UNKNOWN."""

    @abstractmethod
    def get_change_reviews(self, id: str) -> str:
        """Latest review-state signal, e.g. APPROVED / CHANGES_REQUESTED / ''."""

    @abstractmethod
    def get_change_inline_comments(self, id: str) -> list:
        """Inline review feedback: [{path, line, body, created_at}]."""

    @abstractmethod
    def close_keyword(self, issue_id: str) -> str:
        """Body snippet that auto-closes issue_id on merge — only meaningful when
        tracker == code host (parent spec §6.4); '' otherwise."""
```

4. Verify it passes:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_codehost_base.py -v
```

Expected output: `1 passed`.

5. Commit:

```bash
git add scripts/factory_core/providers/codehost/__init__.py scripts/factory_core/providers/codehost/base.py \
        tests/test_provider_codehost_base.py
git commit -m "feat(providers): add CodeHost ABC"
```

---

## Task 3: `get_tracker()` / `get_codehost()` registry

**Files:** `scripts/factory_core/providers/__init__.py` (modified — was empty from Task 1),
`tests/test_provider_registry.py` (new)

1. Write the failing test:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def test_get_tracker_returns_github_tracker():
    from factory_core.providers import get_tracker
    from factory_core.providers.tracker.github import GitHubTracker
    assert isinstance(get_tracker(), GitHubTracker)


def test_get_codehost_returns_github_codehost():
    from factory_core.providers import get_codehost
    from factory_core.providers.codehost.github import GitHubCodeHost
    assert isinstance(get_codehost(), GitHubCodeHost)
```

2. Verify it fails:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_registry.py -v
```

Expected output: `ImportError: cannot import name 'get_tracker'` — `GitHubTracker`/
`GitHubCodeHost` don't exist yet either (both land in Tasks 5–13; this test won't
fully pass until Task 13, which is fine — Tasks 4–13 build toward it. Re-run after
Task 13 to confirm green).

3. Implement:

```python
# scripts/factory_core/providers/__init__.py
"""Provider registry (parent spec docs/provider-abstraction-design.md §4).

No selection logic in this ticket — always returns the GitHub reference adapters.
FACTORY_TRACKER / FACTORY_CODEHOST env-based selection is parent spec step 3.
"""


def get_tracker():
    from factory_core.providers.tracker.github import GitHubTracker
    return GitHubTracker()


def get_codehost():
    from factory_core.providers.codehost.github import GitHubCodeHost
    return GitHubCodeHost()
```

4. Do not run the test suite for a full pass yet — `GitHubTracker`/`GitHubCodeHost` are
   implemented incrementally in Tasks 5–13. Confirm only that the import error moved:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_registry.py -v
```

Expected output: `ModuleNotFoundError: No module named 'factory_core.providers.tracker.github'`
(the next task's job — this confirms `providers/__init__.py` itself is now correct).

5. Commit:

```bash
git add scripts/factory_core/providers/__init__.py tests/test_provider_registry.py
git commit -m "feat(providers): add get_tracker()/get_codehost() registry (GitHub-only, step 1)"
```

---

## Task 4: Extract `board.py` shared helpers (zero behavior change)

**Files:** `scripts/factory_core/board.py` (modified)

This is a pure refactor — no new test is written because no new behavior is introduced;
`tests/test_factory_core_board.py` (unmodified) is the safety net.

1. Confirm the safety net is green before touching anything:

```bash
PYTHONPATH=scripts python -m pytest tests/test_factory_core_board.py -v
```

Expected output: `7 passed`.

2. Refactor `scripts/factory_core/board.py`, extracting the two private helpers
   `GitHubTracker.set_status` will reuse, with **no change** to any argv or to
   `find_board_item`'s / `set_board_status`'s public signatures:

```python
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


def _find_item_by_number(number: str) -> str:
    """Project-item lookup by issue number, compared as strings so an opaque
    Tracker id (e.g. "PROJ-123") never needs int() coercion to reach this call."""
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
            if str(c.get("number")) == number and c.get("type") == "Issue":
                return item["id"]
    except (json.JSONDecodeError, KeyError):
        pass
    return ""


def _item_edit_status(item_id: str, option_id: str) -> None:
    subprocess.run(
        ["gh", "project", "item-edit",
         "--project-id", PROJECT_ID,
         "--id", item_id,
         "--field-id", STATUS_FIELD,
         "--single-select-option-id", option_id],
        capture_output=True,
    )


def find_board_item(issue_num: int) -> str:
    return _find_item_by_number(str(issue_num))


def set_board_status(issue_num: int, option_id: str) -> None:
    item_id = _find_item_by_number(str(issue_num))
    if not item_id:
        return
    _item_edit_status(item_id, option_id)


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
```

3. Verify the existing suite is still fully green (this IS the test-pass step for this task):

```bash
PYTHONPATH=scripts python -m pytest tests/test_factory_core_board.py -v
PYTHONPATH=scripts python -m pytest tests/test_factory_core_breaker.py tests/test_factory_core_rescue.py -v
```

Expected output: all passed — `test_factory_core_breaker.py`/`test_factory_core_rescue.py` are
included because both import `board.set_board_status`, which now has a different internal call
graph.

4. Commit:

```bash
git add scripts/factory_core/board.py
git commit -m "refactor(board): extract _find_item_by_number/_item_edit_status for provider reuse"
```

---

## Task 5: `GitHubTracker.get_item` / `get_comments` / `get_children`

**Files:** `scripts/factory_core/providers/tracker/github.py` (new),
`tests/test_provider_tracker_parity.py` (new)

1. Write the failing tests:

```python
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from factory_core import identity
from factory_core.providers.tracker.github import GitHubTracker


def _ok(stdout="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def test_get_item_default_fields_matches_run_dag_fetch_issue_node(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        return _ok(stdout=json.dumps({"title": "t", "body": "b", "labels": [], "comments": []}))
    monkeypatch.setattr(subprocess, "run", fake)
    GitHubTracker().get_item("42")
    assert calls[0] == [
        "gh", "issue", "view", "42", "--repo", identity.SLUG,
        "--json", "title,body,labels,comments",
    ]


def test_get_comments_matches_scheduler_get_new_comments(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        return _ok(stdout=json.dumps([]))
    monkeypatch.setattr(subprocess, "run", fake)
    GitHubTracker().get_comments("42")
    assert calls[0] == [
        "gh", "issue", "view", "42", "--repo", identity.SLUG,
        "--json", "comments", "-q", ".comments",
    ]


def test_get_children_matches_epic_autopilot_sub_issue_numbers(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        return _ok(stdout=json.dumps({"data": {"repository": {"issue": {"subIssues": {"nodes": []}}}}}))
    monkeypatch.setattr(subprocess, "run", fake)
    GitHubTracker().get_children("1")
    expected_query = (
        'query { repository(owner:"%s", name:"%s") { issue(number:1) { '
        'subIssues(first:50) { nodes { number state labels(first:20){nodes{name}} } } } } }'
        % (identity.OWNER, identity.REPO)
    )
    assert calls[0] == ["gh", "api", "graphql", "-f", "query=" + expected_query]
```

2. Verify they fail:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_tracker_parity.py -v
```

Expected output: `ModuleNotFoundError: No module named 'factory_core.providers.tracker.github'`.

3. Implement (this is the first slice of the file — later tasks append to it):

```python
# scripts/factory_core/providers/tracker/github.py
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
        raise NotImplementedError  # Task 6

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
```

Note `%s` (not `%d`) for `epic_id` in the `get_children` query interpolation — `epic_id` is an
opaque string passed through unchanged, matching requirement 1 (no `int()` coercion anywhere).

4. Verify they pass:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_tracker_parity.py -v
```

Expected output: `3 passed`.

5. Commit:

```bash
git add scripts/factory_core/providers/tracker/github.py tests/test_provider_tracker_parity.py
git commit -m "feat(providers): GitHubTracker.get_item/get_comments/get_children"
```

---

## Task 6: `GitHubTracker.set_status`

**Files:** `scripts/factory_core/providers/tracker/github.py` (modified),
`tests/test_provider_tracker_parity.py` (modified)

1. Add the failing test:

```python
def test_set_status_resolves_canonical_and_calls_item_edit(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        if "item-list" in cmd:
            return _ok(stdout=json.dumps(
                {"items": [{"id": "ITEM42", "content": {"number": 42, "type": "Issue"}}]}
            ))
        return _ok()
    monkeypatch.setattr(subprocess, "run", fake)
    GitHubTracker().set_status("42", "in_review")
    edit = next(c for c in calls if "item-edit" in c)
    assert edit == [
        "gh", "project", "item-edit",
        "--project-id", identity.PROJECT_ID,
        "--id", "ITEM42",
        "--field-id", identity.STATUS_FIELD,
        "--single-select-option-id", identity.STATUS["in_review"],
    ]


def test_set_status_opaque_id_never_reaches_int(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout='{"items": []}'))[1])
    GitHubTracker().set_status("PROJ-123", "blocked")  # must not raise ValueError from int()
    assert not any("item-edit" in c for c in calls)
```

2. Verify it fails:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_tracker_parity.py -k set_status -v
```

Expected output: `AttributeError: 'GitHubTracker' object has no attribute 'set_status'`.

3. Implement — replace the `set_status` stub (`raise NotImplementedError  # Task 6`, added in
   Task 5) in `scripts/factory_core/providers/tracker/github.py` with:

```python
    def set_status(self, id: str, canonical: str) -> None:
        item_id = board._find_item_by_number(id)
        if not item_id:
            return
        board._item_edit_status(item_id, identity.STATUS[canonical])
```

4. Verify it passes:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_tracker_parity.py -k set_status -v
```

Expected output: `2 passed`.

5. Commit:

```bash
git add scripts/factory_core/providers/tracker/github.py tests/test_provider_tracker_parity.py
git commit -m "feat(providers): GitHubTracker.set_status via shared board.py item-edit helper"
```

---

## Task 7: `GitHubTracker.add_label` / `remove_label`

**Files:** `scripts/factory_core/providers/tracker/github.py` (modified),
`tests/test_provider_tracker_parity.py` (modified)

1. Add the failing tests:

```python
def test_add_label_matches_breaker_trip_to_blocked(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubTracker().add_label("42", "needs-discussion")
    assert calls[0] == [
        "gh", "issue", "edit", "42", "--repo", identity.SLUG,
        "--add-label", "needs-discussion",
    ]


def test_remove_label_matches_scheduler_advance_path(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubTracker().remove_label("42", "spec-pending-review")
    assert calls[0] == [
        "gh", "issue", "edit", "42", "--repo", identity.SLUG,
        "--remove-label", "spec-pending-review",
    ]
```

2. Verify they fail:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_tracker_parity.py -k "add_label or remove_label" -v
```

Expected output: `AttributeError: 'GitHubTracker' object has no attribute 'add_label'`.

3. Implement — replace the `add_label`/`remove_label` stubs (added in Task 5) with:

```python
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
```

4. Verify:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_tracker_parity.py -k "add_label or remove_label" -v
```

Expected output: `2 passed`.

5. Commit:

```bash
git add scripts/factory_core/providers/tracker/github.py tests/test_provider_tracker_parity.py
git commit -m "feat(providers): GitHubTracker.add_label/remove_label"
```

---

## Task 8: `GitHubTracker.upsert_comment`

**Files:** `scripts/factory_core/providers/tracker/github.py` (modified),
`tests/test_provider_tracker_parity.py` (modified)

1. Add the failing test:

```python
def test_upsert_comment_delegates_to_board_post_or_update_comment(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        return _ok()
    monkeypatch.setattr(subprocess, "run", fake)
    GitHubTracker().upsert_comment("42", "<!-- marker -->", "body text")
    assert any("issue" in c and "comment" in c for c in calls)


def test_upsert_comment_opaque_id_passthrough(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubTracker().upsert_comment("PROJ-123", "<!-- marker -->", "body")
    lookup = calls[0]
    assert f"repos/{identity.OWNER}/{identity.REPO}/issues/PROJ-123/comments" in lookup
```

2. Verify it fails:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_tracker_parity.py -k upsert_comment -v
```

Expected output: `AttributeError: 'GitHubTracker' object has no attribute 'upsert_comment'`.

3. Implement — replace the `upsert_comment` stub (added in Task 5) with:

```python
    def upsert_comment(self, id: str, marker: str, body: str) -> None:
        board.post_or_update_comment(id, marker, body)
```

`board.post_or_update_comment` never calls `int()` on `issue_num` — it only uses it in f-strings
and `str()` — so passing the opaque string `id` straight through is already correct with no
wrapper logic needed (see Design decision 2 above; this is the same reasoning, but this
particular function never had the `==` int-comparison problem `_find_item_by_number` had).

4. Verify:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_tracker_parity.py -k upsert_comment -v
```

Expected output: `2 passed`.

5. Commit:

```bash
git add scripts/factory_core/providers/tracker/github.py tests/test_provider_tracker_parity.py
git commit -m "feat(providers): GitHubTracker.upsert_comment delegates to board.post_or_update_comment"
```

---

## Task 9: `GitHubTracker.create_item` / `resolve_item`

**Files:** `scripts/factory_core/providers/tracker/github.py` (modified),
`tests/test_provider_tracker_parity.py` (modified)

1. Add the failing tests:

```python
def test_create_item_matches_smoke_gate_regression_ticket(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        return _ok(stdout=f"https://github.com/{identity.SLUG}/issues/77\n")
    monkeypatch.setattr(subprocess, "run", fake)
    new_id = GitHubTracker().create_item(
        title="main is red: tsc/python import failure",
        body="failure body",
        labels=["regression"],
    )
    assert calls[0] == [
        "gh", "issue", "create", "--repo", identity.SLUG,
        "--label", "regression",
        "--title", "main is red: tsc/python import failure",
        "--body", "failure body",
    ]
    assert new_id == "77"


def test_resolve_item_matches_smoke_gate_close(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubTracker().resolve_item("77", comment="main smoke gate passed — closing regression ticket.")
    assert calls[0] == [
        "gh", "issue", "close", "77", "--repo", identity.SLUG,
        "--comment", "main smoke gate passed — closing regression ticket.",
    ]
```

2. Verify they fail:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_tracker_parity.py -k "create_item or resolve_item" -v
```

Expected output: `AttributeError: 'GitHubTracker' object has no attribute 'create_item'`.

3. Implement — replace the `create_item`/`resolve_item` stubs (added in Task 5) with:

```python
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
```

4. Verify:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_tracker_parity.py -k "create_item or resolve_item" -v
```

Expected output: `2 passed`.

5. Commit:

```bash
git add scripts/factory_core/providers/tracker/github.py tests/test_provider_tracker_parity.py
git commit -m "feat(providers): GitHubTracker.create_item/resolve_item"
```

---

## Task 10: `GitHubTracker.list_work_items`

**Files:** `scripts/factory_core/providers/tracker/github.py` (modified),
`tests/test_provider_tracker_parity.py` (modified)

1. Add the failing test. `scheduler.sh`'s `fetch_board_items` GraphQL query fetches **all**
   items unfiltered (status filtering happens client-side via `get_items_by_status`) — the
   single-page argv is param-independent, so the parity test targets exactly that query, then
   asserts the Python-side status/label filtering behaves like `get_items_by_status`:

```python
def test_list_work_items_single_page_query_matches_scheduler_fetch_board_items(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        return _ok(stdout=json.dumps({
            "data": {"node": {"items": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [
                    {"fieldValueByName": {"name": "Ready"},
                     "content": {"number": 42, "title": "t", "labels": {"nodes": [{"name": "ready-for-agent"}]}}},
                    {"fieldValueByName": {"name": "Done"},
                     "content": {"number": 43, "title": "t2", "labels": {"nodes": []}}},
                ],
            }}}
        }))
    monkeypatch.setattr(subprocess, "run", fake)
    items = GitHubTracker().list_work_items(["ready"])
    expected_query = (
        '\n      query {\n        node(id: "' + identity.PROJECT_ID + '") {\n'
        '          ... on ProjectV2 {\n            items(first: 100) {\n'
        '              pageInfo { hasNextPage endCursor }\n              nodes {\n'
        '                fieldValueByName(name: "Status") {\n'
        '                  ... on ProjectV2ItemFieldSingleSelectValue { name }\n                }\n'
        '                content {\n                  ... on Issue {\n                    number\n'
        '                    title\n                    labels(first: 10) { nodes { name } }\n'
        '                  }\n                }\n              }\n            }\n          }\n        }\n      }\n    '
    )
    assert calls[0] == ["gh", "api", "graphql", "-f", "query=" + expected_query]
    assert [i["id"] for i in items] == ["42"]
    assert items[0]["status"] == "ready"


def test_list_work_items_filters_by_label(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout=json.dumps({
        "data": {"node": {"items": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [
                {"fieldValueByName": {"name": "Ready"},
                 "content": {"number": 1, "title": "a", "labels": {"nodes": [{"name": "ready-for-agent"}]}}},
                {"fieldValueByName": {"name": "Ready"},
                 "content": {"number": 2, "title": "b", "labels": {"nodes": []}}},
            ],
        }}}
    })))
    items = GitHubTracker().list_work_items(["ready"], labels=["ready-for-agent"])
    assert [i["id"] for i in items] == ["1"]
```

2. Verify they fail:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_tracker_parity.py -k list_work_items -v
```

Expected output: `AttributeError: 'GitHubTracker' object has no attribute 'list_work_items'`.

3. Implement — replace the `list_work_items` stub (added in Task 5) with the real method. The
   query template is transcribed verbatim from `scheduler.sh:523-548` (single-page case, no
   `after` cursor):

```python
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
```

```python
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
```

4. Verify:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_tracker_parity.py -k list_work_items -v
```

Expected output: `2 passed`. If the first test's `expected_query` string literal doesn't match
byte-for-byte (whitespace is easy to get wrong when transcribing a bash heredoc into a Python
triple-quoted string), copy the exact query text `_BOARD_ITEMS_QUERY_TEMPLATE % (identity.
PROJECT_ID, "")` produces at a REPL and paste that back into the test rather than
hand-retyping it a second time.

5. Commit:

```bash
git add scripts/factory_core/providers/tracker/github.py tests/test_provider_tracker_parity.py
git commit -m "feat(providers): GitHubTracker.list_work_items (paginated ProjectV2 query, client-side filter)"
```

---

## Task 11: `GitHubTracker.get_status_limits` / `get_rate_budget`

**Files:** `scripts/factory_core/providers/tracker/github.py` (modified),
`tests/test_provider_tracker_parity.py` (modified)

1. Add the failing tests — port `scheduler.sh`'s `fetch_wip_limits`/`get_column_limit` (including
   the `999` fallback) and the `check_rate_limit`/telemetry `gh api rate_limit` reads (spec Q4/A4):

```python
def test_get_status_limits_query_matches_scheduler_fetch_wip_limits(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        options = [
            {"id": identity.STATUS["in_progress"], "name": "In progress", "description": "limit: 3"},
            {"id": identity.STATUS["in_review"], "name": "In review", "description": "no limit token here"},
        ]
        return _ok(stdout=json.dumps({"data": {"node": {"field": {"options": options}}}}))
    monkeypatch.setattr(subprocess, "run", fake)
    limits = GitHubTracker().get_status_limits()
    # Transcribed verbatim (incl. whitespace) from scheduler.sh:591-608's fetch_wip_limits
    # heredoc, with $FACTORY_PROJECT_ID substituted — byte/argv equality, not substring checks.
    expected_query = (
        '\n    query {\n      node(id: "' + identity.PROJECT_ID + '") {\n'
        '        ... on ProjectV2 {\n          field(name: "Status") {\n'
        '            ... on ProjectV2SingleSelectField {\n'
        '              options { id name description }\n            }\n          }\n'
        '        }\n      }\n    }\n  '
    )
    assert calls[0] == ["gh", "api", "graphql", "-f", "query=" + expected_query]
    assert limits["in_progress"] == 3
    assert limits["in_review"] == 999  # scheduler.sh's documented fallback


def test_get_rate_budget_matches_scheduler_check_rate_limit(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        return _ok(stdout=json.dumps({"remaining": 150, "reset": 1999999999, "used": 4850, "limit": 5000}))
    monkeypatch.setattr(subprocess, "run", fake)
    budget = GitHubTracker().get_rate_budget()
    assert calls[0] == ["gh", "api", "rate_limit", "--jq", ".resources.graphql"]
    assert budget == {"remaining": 150, "reset": 1999999999, "used": 4850, "limit": 5000}


def test_get_rate_budget_degrades_safely_on_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout="", returncode=1))
    budget = GitHubTracker().get_rate_budget()
    assert budget == {"remaining": None, "reset": None, "used": None, "limit": None}
```

2. Verify they fail:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_tracker_parity.py -k "status_limits or rate_budget" -v
```

Expected output: `test_get_status_limits_query_matches_scheduler_fetch_wip_limits` fails with
`IndexError: list index out of range` on `calls[0]` — `GitHubTracker` still only has the ABC's
inherited concrete safe-default `get_status_limits()` (returns `{}` without calling `gh` at
all), so the fake `subprocess.run` is never invoked. `test_get_rate_budget_matches_scheduler_
check_rate_limit` fails the same way. `test_get_rate_budget_degrades_safely_on_failure` **may
already pass accidentally** via the inherited ABC default (both return the same all-`None`
dict on failure) — that's fine, it still verifies real behavior once overridden in step 3.

3. Implement — append:

```python
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
```

4. Verify:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_tracker_parity.py -k "status_limits or rate_budget" -v
```

Expected output: `3 passed`.

5. Commit:

```bash
git add scripts/factory_core/providers/tracker/github.py tests/test_provider_tracker_parity.py
git commit -m "feat(providers): GitHubTracker.get_status_limits/get_rate_budget (real ports, not stubs)"
```

---

## Task 12: `GitHubCodeHost` — `remote_url`, `find_change_for`, `open_change`, `update_change_body`, `mark_ready`

**Files:** `scripts/factory_core/providers/codehost/github.py` (new),
`tests/test_provider_codehost_parity.py` (new)

1. Write the failing tests:

```python
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from factory_core import identity
from factory_core.providers.codehost.github import GitHubCodeHost


def _ok(stdout="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def test_remote_url_matches_entrypoint_construction(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_faketoken")
    url = GitHubCodeHost().remote_url()
    assert url == f"https://ghp_faketoken@github.com/{identity.SLUG}.git"


def test_find_change_for_matches_scheduler_get_pr_for_issue(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="42\n"))[1])
    pr = GitHubCodeHost().find_change_for("feat/issue-42-", repo=identity.SLUG)
    assert calls[0] == [
        "gh", "pr", "list", "--repo", identity.SLUG,
        "--search", "head:feat/issue-42-",
        "--json", "number", "--jq", ".[0].number // empty",
    ]
    assert pr == "42"


def test_find_change_for_matches_run_dag_no_repo(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout=""))[1])
    pr = GitHubCodeHost().find_change_for("feat/issue-42-")
    assert calls[0] == [
        "gh", "pr", "list",
        "--search", "head:feat/issue-42-",
        "--json", "number", "--jq", ".[0].number // empty",
    ]
    assert pr is None


def test_find_change_for_exact_head_matches_push_resolve(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="9\n"))[1])
    GitHubCodeHost().find_change_for("feat/issue-42-slug", exact=True, repo=identity.SLUG)
    assert calls[0] == [
        "gh", "pr", "list", "--repo", identity.SLUG,
        "--head", "feat/issue-42-slug",
        "--json", "number", "--jq", ".[0].number // empty",
    ]


def test_open_change_matches_run_dag_push_and_pr(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="https://github.com/o/r/pull/9\n"))[1])
    new_id = GitHubCodeHost().open_change(None, None, "Fix the thing (#42)", "Closes #42\n\n## Summary", draft=True)
    assert calls[0] == ["gh", "pr", "create", "--title", "Fix the thing (#42)", "--body", "Closes #42\n\n## Summary", "--draft"]
    assert new_id == "9"


def test_open_change_matches_main_red_fixer_open_pr(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="https://github.com/o/r/pull/11\n"))[1])
    GitHubCodeHost().open_change(
        "fix/main-red-recovery-99", "main", "fix: main-red recovery (#99)", "body",
        draft=False, repo=identity.SLUG,
    )
    assert calls[0] == [
        "gh", "pr", "create", "--repo", identity.SLUG, "--base", "main",
        "--head", "fix/main-red-recovery-99",
        "--title", "fix: main-red recovery (#99)", "--body", "body",
    ]


def test_update_change_body_matches_run_dag_backfill(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubCodeHost().update_change_body("7", "Closes #42\n\nold body")
    assert calls[0] == ["gh", "pr", "edit", "7", "--body", "Closes #42\n\nold body"]


def test_mark_ready_matches_run_dag_close_preview(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubCodeHost().mark_ready("7")
    assert calls[0] == ["gh", "pr", "ready", "7"]


def test_mark_ready_matches_rescue_py(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubCodeHost().mark_ready("7", repo=identity.SLUG)
    assert calls[0] == ["gh", "pr", "ready", "7", "--repo", identity.SLUG]
```

2. Verify they fail:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_codehost_parity.py -v
```

Expected output: `ModuleNotFoundError: No module named 'factory_core.providers.codehost.github'`.

3. Implement:

```python
# scripts/factory_core/providers/codehost/github.py
"""GitHubCodeHost — mechanical extraction of today's gh pr / gh api ...pulls... calls.
Most current CodeHost-shaped operations live as inline strings in
workflows/archon-dark-factory.yaml and entrypoint.sh, not any factory_core Python
module — that YAML/bash text is the golden baseline these argv constants are
transcribed from (spec Architecture section, "CodeHost: no existing Python home")."""
import os
import re
import subprocess

from factory_core import identity
from factory_core.providers.codehost.base import CodeHost


class GitHubCodeHost(CodeHost):
    def remote_url(self) -> str:
        token = os.environ.get("GH_TOKEN", "")
        return f"https://{token}@github.com/{identity.SLUG}.git"

    def find_change_for(self, branch: str, exact: bool = False,
                         repo: str | None = None, fields: str = "number") -> str | None:
        cmd = ["gh", "pr", "list"]
        if repo:
            cmd += ["--repo", repo]
        if exact:
            cmd += ["--head", branch]
        else:
            cmd += ["--search", f"head:{branch}"]
        cmd += ["--json", fields, "--jq", ".[0].number // empty"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        out = r.stdout.strip() if r.returncode == 0 else ""
        return out or None

    def open_change(self, source: str | None, target: str | None, title: str, body: str,
                     draft: bool = False, repo: str | None = None) -> str:
        cmd = ["gh", "pr", "create"]
        if repo:
            cmd += ["--repo", repo]
        if target:
            cmd += ["--base", target]
        if source:
            cmd += ["--head", source]
        cmd += ["--title", title, "--body", body]
        if draft:
            cmd.append("--draft")
        r = subprocess.run(cmd, capture_output=True, text=True)
        m = re.search(r"/pull/(\d+)", r.stdout or "")
        return m.group(1) if m else (r.stdout or "").strip()

    def update_change_body(self, id: str, body: str) -> None:
        subprocess.run(["gh", "pr", "edit", id, "--body", body], capture_output=True)

    def mark_ready(self, id: str, repo: str | None = None) -> None:
        cmd = ["gh", "pr", "ready", id]
        if repo:
            cmd += ["--repo", repo]
        subprocess.run(cmd, capture_output=True)

    # --- Stubs for not-yet-implemented CodeHost ops ---
    # Same rationale as GitHubTracker's Task 5 stubs: a stub override removes
    # a method from CodeHost.__abstractmethods__, so GitHubCodeHost is
    # instantiable starting now (needed for this task's own tests, which call
    # `GitHubCodeHost()` directly). Task 13 replaces every stub below with a
    # real implementation.
    def merge_change(self, id: str, strategy: str = "merge", delete_branch: bool = True,
                      repo: str | None = None) -> bool:
        raise NotImplementedError  # Task 13

    def get_change_checks(self, id: str, fields: str = "name,bucket,link",
                           repo: str | None = None) -> list:
        raise NotImplementedError  # Task 13

    def get_change_mergeable(self, id: str, repo: str | None = None) -> str:
        raise NotImplementedError  # Task 13

    def get_change_reviews(self, id: str, repo: str | None = None) -> str:
        raise NotImplementedError  # Task 13

    def get_change_inline_comments(self, id: str, repo: str | None = None) -> list:
        raise NotImplementedError  # Task 13

    def close_keyword(self, issue_id: str) -> str:
        raise NotImplementedError  # Task 13
```

4. Verify:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_codehost_parity.py -v
```

Expected output: `9 passed`.

5. Commit:

```bash
git add scripts/factory_core/providers/codehost/github.py tests/test_provider_codehost_parity.py
git commit -m "feat(providers): GitHubCodeHost remote_url/find_change_for/open_change/update_change_body/mark_ready"
```

---

## Task 13: `GitHubCodeHost` — `merge_change`, `get_change_checks`, `get_change_mergeable`, `get_change_reviews`, `get_change_inline_comments`, `close_keyword`

**Files:** `scripts/factory_core/providers/codehost/github.py` (modified),
`tests/test_provider_codehost_parity.py` (modified)

1. Add the failing tests:

```python
def test_merge_change_matches_run_dag_close_preview(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(returncode=0))[1])
    ok = GitHubCodeHost().merge_change("9")
    assert calls[0] == ["gh", "pr", "merge", "9", "--merge", "--delete-branch"]
    assert ok is True


def test_merge_change_matches_main_red_fixer(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(returncode=0))[1])
    GitHubCodeHost().merge_change("9", repo=identity.SLUG)
    assert calls[0] == ["gh", "pr", "merge", "9", "--repo", identity.SLUG, "--merge", "--delete-branch"]


def test_get_change_checks_matches_scheduler_failing_checks_for_pr(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="[]"))[1])
    GitHubCodeHost().get_change_checks("9", fields="name,bucket,link", repo=identity.SLUG)
    assert calls[0] == ["gh", "pr", "checks", "9", "--repo", identity.SLUG, "--json", "name,bucket,link"]


def test_get_change_checks_matches_rescue_py(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="[]"))[1])
    GitHubCodeHost().get_change_checks("9", fields="bucket", repo=identity.SLUG)
    assert calls[0] == ["gh", "pr", "checks", "9", "--repo", identity.SLUG, "--json", "bucket"]


def test_get_change_mergeable_matches_scheduler_check_pr_mergeable(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="MERGEABLE\n"))[1])
    state = GitHubCodeHost().get_change_mergeable("9", repo=identity.SLUG)
    assert calls[0] == [
        "gh", "pr", "view", "9", "--repo", identity.SLUG,
        "--json", "mergeable", "--jq", ".mergeable",
    ]
    assert state == "MERGEABLE"


def test_get_change_mergeable_defaults_unknown_on_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout="", returncode=1))
    assert GitHubCodeHost().get_change_mergeable("9") == "UNKNOWN"


def test_get_change_reviews_matches_scheduler_end_gate_check(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="APPROVED\n"))[1])
    state = GitHubCodeHost().get_change_reviews("9", repo=identity.SLUG)
    assert calls[0] == [
        "gh", "pr", "view", "9", "--repo", identity.SLUG, "--json", "reviews", "--jq",
        '[.reviews[] | select(.state == "APPROVED" or .state == "CHANGES_REQUESTED")] | last | .state // ""',
    ]
    assert state == "APPROVED"


def test_get_change_inline_comments_matches_run_dag_fetch_issue(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="[]"))[1])
    GitHubCodeHost().get_change_inline_comments("9", repo=identity.SLUG)
    assert calls[0] == [
        "gh", "api", f"repos/{identity.SLUG}/pulls/9/comments",
        "--jq", "[.[] | {path: .path, line: .line, body: .body, created_at: .created_at}]",
    ]


def test_close_keyword_matches_run_dag_and_main_red_fixer():
    kw = GitHubCodeHost().close_keyword("42")
    assert kw == "Closes #42"


def test_close_keyword_opaque_id_passthrough():
    assert GitHubCodeHost().close_keyword("PROJ-123") == "Closes #PROJ-123"
```

2. Verify they fail:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_codehost_parity.py -k "merge_change or get_change or close_keyword" -v
```

Expected output: `AttributeError: 'GitHubCodeHost' object has no attribute 'merge_change'`.

3. Implement — replace the six stubs added in Task 12 (`merge_change`, `get_change_checks`,
   `get_change_mergeable`, `get_change_reviews`, `get_change_inline_comments`, `close_keyword`)
   in `scripts/factory_core/providers/codehost/github.py` with:

```python
    def merge_change(self, id: str, strategy: str = "merge", delete_branch: bool = True,
                      repo: str | None = None) -> bool:
        cmd = ["gh", "pr", "merge", id]
        if repo:
            cmd += ["--repo", repo]
        cmd.append(f"--{strategy}")
        if delete_branch:
            cmd.append("--delete-branch")
        r = subprocess.run(cmd, capture_output=True)
        return r.returncode == 0

    def get_change_checks(self, id: str, fields: str = "name,bucket,link",
                           repo: str | None = None) -> list:
        cmd = ["gh", "pr", "checks", id]
        if repo:
            cmd += ["--repo", repo]
        cmd += ["--json", fields]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            return []
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def get_change_mergeable(self, id: str, repo: str | None = None) -> str:
        cmd = ["gh", "pr", "view", id]
        if repo:
            cmd += ["--repo", repo]
        cmd += ["--json", "mergeable", "--jq", ".mergeable"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        result = r.stdout.strip() if r.returncode == 0 else ""
        return result or "UNKNOWN"

    def get_change_reviews(self, id: str, repo: str | None = None) -> str:
        cmd = ["gh", "pr", "view", id]
        if repo:
            cmd += ["--repo", repo]
        cmd += ["--json", "reviews", "--jq",
                '[.reviews[] | select(.state == "APPROVED" or .state == "CHANGES_REQUESTED")] | last | .state // ""']
        r = subprocess.run(cmd, capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""

    def get_change_inline_comments(self, id: str, repo: str | None = None) -> list:
        slug = repo or identity.SLUG
        r = subprocess.run(
            ["gh", "api", f"repos/{slug}/pulls/{id}/comments",
             "--jq", "[.[] | {path: .path, line: .line, body: .body, created_at: .created_at}]"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            return []
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            return []

    def close_keyword(self, issue_id: str) -> str:
        return f"Closes #{issue_id}"
```

Add `import json` to the top of the file alongside the existing `import os, re, subprocess`.

4. Verify:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_codehost_parity.py -v
```

Expected output: `19 passed` (9 from Task 12 + 10 here).

5. Commit:

```bash
git add scripts/factory_core/providers/codehost/github.py tests/test_provider_codehost_parity.py
git commit -m "feat(providers): GitHubCodeHost merge_change/checks/mergeable/reviews/inline-comments/close_keyword"
```

---

## Task 14: Opaque-ID round-trip contract tests

**Files:** `tests/test_provider_tracker_parity.py` (modified),
`tests/test_provider_codehost_parity.py` (modified)

Per spec Q3/A3: prove `"42"` and `"PROJ-123"` reach the constructed argv unchanged, with no
`int()` coercion anywhere in the path, across every `Tracker`/`CodeHost` method that takes an id.

1. Add to `tests/test_provider_tracker_parity.py`:

```python
import pytest

_TRACKER_ID_CASES = ["42", "PROJ-123"]


@pytest.mark.parametrize("opaque_id", _TRACKER_ID_CASES)
def test_tracker_methods_pass_opaque_id_through_unchanged(monkeypatch, opaque_id):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="{}"))[1])
    GitHubTracker().get_item(opaque_id)
    assert opaque_id in calls[-1]

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="[]"))[1])
    GitHubTracker().get_comments(opaque_id)
    assert opaque_id in calls[-1]

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="{}"))[1])
    GitHubTracker().get_children(opaque_id)
    assert opaque_id in calls[-1][4]  # embedded in the GraphQL query string, not a separate argv element

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubTracker().add_label(opaque_id, "some-label")
    assert opaque_id in calls[-1]

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubTracker().remove_label(opaque_id, "some-label")
    assert opaque_id in calls[-1]

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubTracker().resolve_item(opaque_id)
    assert opaque_id in calls[-1]
```

2. Add to `tests/test_provider_codehost_parity.py`:

```python
import pytest

_CODEHOST_ID_CASES = ["42", "PROJ-123"]


@pytest.mark.parametrize("opaque_id", _CODEHOST_ID_CASES)
def test_codehost_methods_pass_opaque_id_through_unchanged(monkeypatch, opaque_id):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubCodeHost().update_change_body(opaque_id, "body")
    assert opaque_id in calls[-1]

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubCodeHost().mark_ready(opaque_id)
    assert opaque_id in calls[-1]

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubCodeHost().merge_change(opaque_id)
    assert opaque_id in calls[-1]

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="[]"))[1])
    GitHubCodeHost().get_change_checks(opaque_id)
    assert opaque_id in calls[-1]

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout=""))[1])
    GitHubCodeHost().get_change_mergeable(opaque_id)
    assert opaque_id in calls[-1]

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout=""))[1])
    GitHubCodeHost().get_change_reviews(opaque_id)
    assert opaque_id in calls[-1]

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="[]"))[1])
    GitHubCodeHost().get_change_inline_comments(opaque_id)
    assert opaque_id in calls[-1][1]  # embedded in the REST path string

    assert GitHubCodeHost().close_keyword(opaque_id) == f"Closes #{opaque_id}"
```

3. Verify they pass (no implementation change expected — this task proves a property of Tasks
   5–13's code, it shouldn't need new production code):

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_tracker_parity.py tests/test_provider_codehost_parity.py -v
```

Expected output: all passed, including 2 new parametrized cases per file (4 total). If any
fails with `ValueError: invalid literal for int()`, that pinpoints a stray `int()`/numeric
comparison this plan's design missed — fix the specific method in its Task 5–13 section above,
not here, to keep each method's implementation and its parity test co-located.

4. Commit:

```bash
git add tests/test_provider_tracker_parity.py tests/test_provider_codehost_parity.py
git commit -m "test(providers): opaque-ID round-trip contract tests (42, PROJ-123) across Tracker/CodeHost"
```

---

## Task 15: `providers/cli.py` thin CLI entry points

**Files:** `scripts/factory_core/providers/cli.py` (new), `tests/test_provider_cli.py` (new)

1. Write the failing test:

```python
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

CLI = str(Path(__file__).resolve().parents[1] / "scripts" / "factory_core" / "providers" / "cli.py")


def test_tracker_get_prints_json(monkeypatch):
    import factory_core.providers.cli as cli_mod

    class _FakeTracker:
        def get_item(self, id, fields=None):
            return {"id": id, "title": "t"}
    monkeypatch.setattr(cli_mod, "get_tracker", lambda: _FakeTracker())
    monkeypatch.setattr(sys, "argv", ["cli.py", "tracker", "get", "--id", "42"])
    cli_mod.main()


def test_codehost_find_change_prints_id(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod

    class _FakeCodeHost:
        def find_change_for(self, branch, exact=False, repo=None, fields="number"):
            return "9"
    monkeypatch.setattr(cli_mod, "get_codehost", lambda: _FakeCodeHost())
    monkeypatch.setattr(sys, "argv", ["cli.py", "codehost", "find-change", "--branch", "feat/issue-42-"])
    cli_mod.main()
    assert capsys.readouterr().out.strip() == "9"
```

2. Verify it fails:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_cli.py -v
```

Expected output: `ModuleNotFoundError: No module named 'factory_core.providers.cli'`.

3. Implement:

```python
# scripts/factory_core/providers/cli.py
#!/usr/bin/env python3
"""Thin CLI entry points for the Tracker/CodeHost providers (parent spec §4.2,
illustrative surface). New, additive surface — nothing existing calls into it yet
(bash/YAML call sites are rewired in a later, separate ticket).

Invocation mirrors this repo's existing scripts/factory_core/cli.py convention
(direct script path + subcommands), not `-m factory_core.tracker` — see this
ticket's plan, "Design decisions" #4.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from factory_core.providers import get_codehost, get_tracker  # noqa: E402


def _print(value):
    print(json.dumps(value) if not isinstance(value, str) else value)


def _tracker_list(args):
    labels = args.labels.split(",") if args.labels else None
    _print(get_tracker().list_work_items(args.statuses.split(","), labels))


def _tracker_get(args):
    _print(get_tracker().get_item(args.id))


def _tracker_set_status(args):
    get_tracker().set_status(args.id, args.status)


def _tracker_label(args):
    tracker = get_tracker()
    for name in (args.add or []):
        tracker.add_label(args.id, name)
    for name in (args.remove or []):
        tracker.remove_label(args.id, name)


def _tracker_comment(args):
    body = Path(args.body_file).read_text(encoding="utf-8")
    get_tracker().upsert_comment(args.id, args.marker, body)


def _tracker_create(args):
    body = Path(args.body_file).read_text(encoding="utf-8")
    labels = args.labels.split(",") if args.labels else None
    _print(get_tracker().create_item(args.title, body, labels))


def _tracker_resolve(args):
    get_tracker().resolve_item(args.id)


def _tracker_children(args):
    _print(get_tracker().get_children(args.epic))


def _codehost_remote_url(args):
    _print(get_codehost().remote_url())


def _codehost_find_change(args):
    _print(get_codehost().find_change_for(args.branch) or "")


def _codehost_open_change(args):
    body = Path(args.body_file).read_text(encoding="utf-8")
    _print(get_codehost().open_change(args.source, args.target, args.title, body, draft=args.draft))


def _codehost_mark_ready(args):
    get_codehost().mark_ready(args.id)


def _codehost_merge(args):
    _print(get_codehost().merge_change(args.id, strategy=args.strategy, delete_branch=args.delete_branch))


def _codehost_checks(args):
    _print(get_codehost().get_change_checks(args.id))


def _codehost_mergeable(args):
    _print(get_codehost().get_change_mergeable(args.id))


def _codehost_reviews(args):
    _print(get_codehost().get_change_reviews(args.id))


def main():
    parser = argparse.ArgumentParser(prog="providers-cli")
    top = parser.add_subparsers(dest="provider", required=True)

    tracker = top.add_parser("tracker")
    tsub = tracker.add_subparsers(dest="verb", required=True)

    tl = tsub.add_parser("list")
    tl.add_argument("--statuses", required=True)
    tl.add_argument("--labels", default="")
    tl.set_defaults(func=_tracker_list)

    tg = tsub.add_parser("get")
    tg.add_argument("--id", required=True)
    tg.set_defaults(func=_tracker_get)

    tss = tsub.add_parser("set-status")
    tss.add_argument("--id", required=True)
    tss.add_argument("--status", required=True)
    tss.set_defaults(func=_tracker_set_status)

    tlabel = tsub.add_parser("label")
    tlabel.add_argument("--id", required=True)
    tlabel.add_argument("--add", action="append")
    tlabel.add_argument("--remove", action="append")
    tlabel.set_defaults(func=_tracker_label)

    tc = tsub.add_parser("comment")
    tc.add_argument("--id", required=True)
    tc.add_argument("--marker", required=True)
    tc.add_argument("--body-file", required=True)
    tc.set_defaults(func=_tracker_comment)

    tcr = tsub.add_parser("create")
    tcr.add_argument("--title", required=True)
    tcr.add_argument("--body-file", required=True)
    tcr.add_argument("--labels", default="")
    tcr.set_defaults(func=_tracker_create)

    tr = tsub.add_parser("resolve")
    tr.add_argument("--id", required=True)
    tr.set_defaults(func=_tracker_resolve)

    tch = tsub.add_parser("children")
    tch.add_argument("--epic", required=True)
    tch.set_defaults(func=_tracker_children)

    codehost = top.add_parser("codehost")
    csub = codehost.add_subparsers(dest="verb", required=True)

    cru = csub.add_parser("remote-url")
    cru.set_defaults(func=_codehost_remote_url)

    cfc = csub.add_parser("find-change")
    cfc.add_argument("--branch", required=True)
    cfc.set_defaults(func=_codehost_find_change)

    coc = csub.add_parser("open-change")
    coc.add_argument("--source")
    coc.add_argument("--target")
    coc.add_argument("--title", required=True)
    coc.add_argument("--body-file", required=True)
    coc.add_argument("--draft", action="store_true")
    coc.set_defaults(func=_codehost_open_change)

    cmr = csub.add_parser("mark-ready")
    cmr.add_argument("--id", required=True)
    cmr.set_defaults(func=_codehost_mark_ready)

    cm = csub.add_parser("merge")
    cm.add_argument("--id", required=True)
    cm.add_argument("--strategy", default="merge")
    cm.add_argument("--delete-branch", action="store_true", default=True)
    cm.set_defaults(func=_codehost_merge)

    cc = csub.add_parser("checks")
    cc.add_argument("--id", required=True)
    cc.set_defaults(func=_codehost_checks)

    cme = csub.add_parser("mergeable")
    cme.add_argument("--id", required=True)
    cme.set_defaults(func=_codehost_mergeable)

    cr = csub.add_parser("reviews")
    cr.add_argument("--id", required=True)
    cr.set_defaults(func=_codehost_reviews)

    parsed = parser.parse_args()
    parsed.func(parsed)


if __name__ == "__main__":
    main()
```

4. Verify:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_cli.py -v
```

Expected output: `2 passed`.

5. Commit:

```bash
git add scripts/factory_core/providers/cli.py tests/test_provider_cli.py
git commit -m "feat(providers): add thin CLI entry points (python3 scripts/factory_core/providers/cli.py tracker|codehost)"
```

---

## Task 16: Full-suite verification

**Files:** none (verification only)

1. Run the complete pytest suite to confirm no regression anywhere, including `board.py`'s
   delegation-didn't-break-anything check (`test_factory_core_board.py` must stay green
   unmodified, per spec Q2/A2):

```bash
PYTHONPATH=scripts python -m pytest tests/ -v
```

Expected output: all tests pass, including every new `test_provider_*.py` file, with
`test_factory_core_board.py`'s 7 tests unchanged and green, and `test_factory_core_breaker.py`/
`test_factory_core_rescue.py` (both call into `board.set_board_status`, refactored in Task 4)
also green.

2. Confirm `get_tracker()`/`get_codehost()` (Task 3) now fully resolve now that `GitHubTracker`/
   `GitHubCodeHost` exist:

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_registry.py -v
```

Expected output: `2 passed`.

3. Run the two workflow-DAG gate scripts (unaffected — this ticket touches no YAML):

```bash
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```

Expected output: both exit 0.

4. No commit in this task — it is a verification checkpoint confirming Tasks 1–15 left the
   repo green before the plan is published.

---

## Out of Scope (explicitly, per spec)

- No changes to `scheduler.sh`, `entrypoint.sh`, `smoke_gate.sh`, or
  `workflows/archon-dark-factory.yaml` — routing those bash/YAML call sites through the new
  provider CLIs is spec step 2, a separate ticket.
- No `JiraTracker` (`providers/tracker/jira.py`) or `GitLabCodeHost`
  (`providers/codehost/gitlab.py`) — steps 4 and 6.
- No `FACTORY_TRACKER`/`FACTORY_CODEHOST` env-based provider selection — step 3;
  `get_tracker()`/`get_codehost()` unconditionally return the GitHub adapters in this ticket.
- No VCR-style cross-adapter contract-test suite (parent spec §10) — lands with Jira in step 4.
- No changes to `identity.py`'s `STATUS` dict, `.factory/adapter.yaml`, or `config/config.yaml`.
- No model-endpoint work (parent spec §7) — a third, unrelated axis.
