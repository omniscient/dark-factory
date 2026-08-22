# Implementation Plan: Propagate set_board_status failure through the Tracker provider and cli.py

**Issue:** omniscient/dark-factory#335
**Spec:** `docs/superpowers/specs/2026-08-22-board-status-failure-propagation-design.md`
**Related:** #292 (added `entrypoint.sh`'s dormant `BOARD_MOVE_OK` guard), #330 (Gate 3 finding origin)

---

## Goal

Make a failed board move actually exit `providers/cli.py tracker set-status` non-zero, so
`entrypoint.sh`'s existing `BOARD_MOVE_OK` guard (added by #292) finally goes live, with **no**
`entrypoint.sh` change. Thread a `bool` success signal from `board.py`'s two `gh`-shelling
helpers, through `Tracker.set_status`'s widened ABC contract (both `GitHubTracker` and
`JiraTracker`), to `cli.py:_tracker_set_status`. `board.set_board_status()` (the separate
fire-and-forget path used by `breaker.py`/`rescue.py`/`deconflict.py`/`epic_autopilot.py`) keeps
its exact current `-> None`, never-raises contract — zero behavior change there.

## Architecture

No new modules, no new exception classes. Five existing files change:

- `scripts/factory_core/board.py` — `_item_edit_status` starts returning the real `gh` exit code
  (`bool`) and prints captured stderr on failure. A new private helper,
  `_find_item_by_number_checked`, distinguishes "transport failure" from "genuinely not on the
  board" internally; the existing public `_find_item_by_number`/`find_board_item`/
  `set_board_status` become thin wrappers over it with **unchanged** external behavior.
- `scripts/factory_core/providers/tracker/base.py` — `Tracker.set_status` return annotation
  widens `None` → `bool` (docstring updated to state the contract).
- `scripts/factory_core/providers/tracker/github.py` — `set_status` implements the widened
  contract using the two board.py signals above.
- `scripts/factory_core/providers/tracker/jira.py` — `set_status` returns `True` after a
  successful transition POST, `False` (unchanged stderr message) when no transition exists. Its
  existing `RuntimeError`-on-`HTTPError` transport-failure path is untouched.
- `scripts/factory_core/providers/cli.py` — `_tracker_set_status` checks the bool and exits 1 with
  an `ERROR: ...` stderr line on `False`; also catches `RuntimeError` (Jira transport failures) the
  same way.
- `workflows/archon-dark-factory.yaml` — two DAG-node call sites (lines 257, 1195) gain an
  advisory `|| echo "WARNING: ..."` guard so a board-move failure there doesn't fail an otherwise-
  successful merge/review-transition node.

This mirrors the `CodeHost` ABC's existing `-> bool` idiom
(`providers/codehost/base.py:30,39`, `providers/codehost/github.py`'s
`r = subprocess.run(...); return r.returncode == 0`).

## Tech Stack

Python 3 (stdlib only: `subprocess`, `json`), `pytest` for all tests, YAML (no new dependency) for
the two-line DAG edit.

---

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/board.py` | `_item_edit_status` returns `bool` + prints stderr; add `_find_item_by_number_checked`; `_find_item_by_number` becomes a wrapper |
| `scripts/factory_core/providers/tracker/base.py` | Widen `set_status` return annotation to `bool`, update docstring |
| `scripts/factory_core/providers/tracker/github.py` | `set_status` returns `bool` via the two board.py signals |
| `scripts/factory_core/providers/tracker/jira.py` | `set_status` returns `bool` |
| `scripts/factory_core/providers/cli.py` | `_tracker_set_status` exits 1 + prints `ERROR:` on `False` or caught `RuntimeError` |
| `workflows/archon-dark-factory.yaml` | Append advisory `\|\| echo "WARNING: ..."` at lines 257, 1195 |
| `tests/test_factory_core_board.py` | Add `_item_edit_status`/`_find_item_by_number_checked` coverage |
| `tests/test_provider_tracker_parity.py` | Extend/add `GitHubTracker.set_status` bool-return coverage |
| `tests/test_provider_tracker_jira.py` | Extend `JiraTracker.set_status` bool-return coverage |
| `tests/test_tracker_contract.py` | Extend contract test to assert bool returns for both trackers |
| `tests/test_provider_cli.py` | Add `_tracker_set_status` exit-code/stderr coverage |

---

## Tasks

### Task 1 — `board._item_edit_status` returns the real `gh` exit code

**Files:** `scripts/factory_core/board.py`, `tests/test_factory_core_board.py`

1. Write failing tests in `tests/test_factory_core_board.py` (append after
   `test_set_board_status_no_item_skips_edit`):

   ```python
   def test_item_edit_status_returns_true_on_success(monkeypatch):
       monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok())
       assert board._item_edit_status("ITEM42", "opt_abc") is True


   def test_item_edit_status_returns_false_and_prints_stderr_on_failure(monkeypatch, capsys):
       monkeypatch.setattr(subprocess, "run", lambda cmd, **kw:
           subprocess.CompletedProcess([], 1, stdout="", stderr="permission denied"))
       assert board._item_edit_status("ITEM42", "opt_abc") is False
       err = capsys.readouterr().err
       assert "ITEM42" in err
       assert "permission denied" in err
   ```

2. Verify fail:

   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_core_board.py -k item_edit_status -v
   ```

   Expected: both new tests fail (`_ok()` helper already exists in this file at line 16;
   `_item_edit_status` currently returns `None`, so `is True`/`is False` assertions fail; no stderr
   printed).

3. Add `import sys` to the top of `board.py` (alongside the existing `import json`, `import os`,
   `import subprocess`, `import tempfile`).

4. Implement in `scripts/factory_core/board.py`, replacing `_item_edit_status` (lines 42-50):

   ```python
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
   ```

5. Verify pass:

   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_core_board.py -v
   ```

   Expected: all tests in the file pass, including the two new ones and the pre-existing
   `test_set_board_status_calls_item_edit`/`test_set_board_status_no_item_skips_edit` (unaffected —
   `set_board_status` still ignores the return value).

6. Commit:

   ```bash
   git add scripts/factory_core/board.py tests/test_factory_core_board.py
   git commit -m "fix(board): _item_edit_status reports gh's real exit code and prints stderr on failure"
   ```

### Task 2 — `_find_item_by_number_checked`: distinguish transport failure from genuine absence

**Files:** `scripts/factory_core/board.py`, `tests/test_factory_core_board.py`

1. Write failing tests, appended to `tests/test_factory_core_board.py`:

   ```python
   def test_find_item_by_number_checked_transport_failure(monkeypatch):
       monkeypatch.setattr(subprocess, "run", lambda cmd, **kw:
           subprocess.CompletedProcess([], 1, stdout="", stderr="rate limited"))
       assert board._find_item_by_number_checked("42") == ("", False)


   def test_find_item_by_number_checked_unparseable_json(monkeypatch):
       monkeypatch.setattr(subprocess, "run", lambda cmd, **kw:
           subprocess.CompletedProcess([], 0, stdout="not json", stderr=""))
       assert board._find_item_by_number_checked("42") == ("", False)


   def test_find_item_by_number_checked_genuinely_absent(monkeypatch):
       monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _items([]))
       assert board._find_item_by_number_checked("42") == ("", True)


   def test_find_item_by_number_checked_found(monkeypatch):
       monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _items([
           {"id": "ITEM42", "content": {"number": 42, "type": "Issue"}},
       ]))
       assert board._find_item_by_number_checked("42") == ("ITEM42", True)


   def test_set_board_status_still_returns_none(monkeypatch):
       # Pins Requirement 3 / Design Decision 3: set_board_status's 4 direct callers
       # (breaker.py, rescue.py, deconflict.py, epic_autopilot.py) must see zero
       # behavior change even though the helpers it calls now report bool/tuple.
       monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _items([
           {"id": "ITEM42", "content": {"number": 42, "type": "Issue"}},
       ]) if "item-list" in cmd else _ok())
       assert board.set_board_status(42, "opt_abc") is None
   ```

2. Verify fail:

   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_core_board.py -k "find_item_by_number_checked or set_board_status_still_returns_none" -v
   ```

   Expected: the four `find_item_by_number_checked` cases fail with `AttributeError: module
   'factory_core.board' has no attribute '_find_item_by_number_checked'`.
   `test_set_board_status_still_returns_none` already passes even before this task's implementation
   (`set_board_status` already returns `None` today) — it is a pinning test, not a red/green case;
   confirm it passes now and stays passing after step 3's implementation.

3. Implement in `scripts/factory_core/board.py`, replacing `_find_item_by_number` (lines 22-39)
   with the checked helper plus a thin wrapper:

   ```python
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
           return "", False
       try:
           items = json.loads(r.stdout).get("items", [])
       except json.JSONDecodeError:
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
   ```

4. Verify pass:

   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_core_board.py -v
   ```

   Expected: all tests pass, including the pre-existing `test_find_board_item_found`,
   `test_find_board_item_wrong_number`, `test_find_board_item_gh_failure` (Requirement 2 — these
   stay green, unedited, both asserting `== ""`).

5. Commit:

   ```bash
   git add scripts/factory_core/board.py tests/test_factory_core_board.py
   git commit -m "fix(board): add _find_item_by_number_checked distinguishing transport failure from absence"
   ```

### Task 3 — Widen `Tracker.set_status` ABC contract to `bool`

**Files:** `scripts/factory_core/providers/tracker/base.py`

1. No test needed for the ABC itself (it's an abstract method signature/docstring change with no
   runtime behavior); implementations are tested in Tasks 4-5 and the contract test in Task 6.

2. Edit `scripts/factory_core/providers/tracker/base.py`, replacing lines 29-31:

   ```python
       @abstractmethod
       def set_status(self, id: str, canonical: str) -> bool:
           """Move an item to one of the seven canonical statuses. Returns True iff
           the item's status actually changed; False for "not found on the board /
           no valid transition" or "operation failed" -- implementations must not
           raise for either case."""
   ```

3. Sanity-check the module still imports:

   ```bash
   cd /workspace/dark-factory && python -c "import sys; sys.path.insert(0, 'scripts'); from factory_core.providers.tracker.base import Tracker; print('ok')"
   ```

   Expected output: `ok`

4. Commit:

   ```bash
   git add scripts/factory_core/providers/tracker/base.py
   git commit -m "fix(tracker): widen Tracker.set_status ABC contract from None to bool"
   ```

### Task 4 — `GitHubTracker.set_status` implements the widened `bool` contract

**Files:** `scripts/factory_core/providers/tracker/github.py`, `tests/test_provider_tracker_parity.py`

1. Update the existing test `test_set_status_resolves_canonical_and_calls_item_edit` in
   `tests/test_provider_tracker_parity.py` to assert the return value, and extend
   `test_set_status_opaque_id_never_reaches_int` similarly. Replace both tests (lines 72-97):

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
       result = GitHubTracker().set_status("42", "in_review")
       edit = next(c for c in calls if "item-edit" in c)
       assert edit == [
           "gh", "project", "item-edit",
           "--project-id", identity.PROJECT_ID,
           "--id", "ITEM42",
           "--field-id", identity.STATUS_FIELD,
           "--single-select-option-id", identity.STATUS["in_review"],
       ]
       assert result is True


   def test_set_status_opaque_id_never_reaches_int(monkeypatch):
       calls = []
       monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout='{"items": []}'))[1])
       result = GitHubTracker().set_status("PROJ-123", "blocked")  # must not raise ValueError from int()
       assert not any("item-edit" in c for c in calls)
       assert result is False


   def test_set_status_item_edit_failure_returns_false(monkeypatch):
       def fake(cmd, **kw):
           if "item-list" in cmd:
               return _ok(stdout=json.dumps(
                   {"items": [{"id": "ITEM42", "content": {"number": 42, "type": "Issue"}}]}
               ))
           return subprocess.CompletedProcess([], 1, stdout="", stderr="denied")
       monkeypatch.setattr(subprocess, "run", fake)
       assert GitHubTracker().set_status("42", "blocked") is False
   ```

2. Verify fail:

   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_provider_tracker_parity.py -k set_status -v
   ```

   Expected: `test_set_status_resolves_canonical_and_calls_item_edit` and
   `test_set_status_opaque_id_never_reaches_int` fail on the new `assert result is ...` lines
   (`set_status` currently returns `None`); `test_set_status_item_edit_failure_returns_false` fails
   with `AssertionError` (`None is False` is falsy but `is False` fails since `None is not False`).

3. Implement in `scripts/factory_core/providers/tracker/github.py`, replacing `set_status` (lines
   141-145):

   ```python
       def set_status(self, id: str, canonical: str) -> bool:
           item_id, lookup_ok = board._find_item_by_number_checked(id)
           if not lookup_ok or not item_id:
               return False
           return board._item_edit_status(item_id, identity.STATUS[canonical])
   ```

4. Verify pass:

   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_provider_tracker_parity.py -v
   ```

   Expected: all tests pass, including the three `set_status` tests above and the unrelated
   pre-existing tests in the file (unaffected).

5. Commit:

   ```bash
   git add scripts/factory_core/providers/tracker/github.py tests/test_provider_tracker_parity.py
   git commit -m "fix(tracker): GitHubTracker.set_status returns bool per widened ABC contract"
   ```

### Task 5 — `JiraTracker.set_status` implements the widened `bool` contract

**Files:** `scripts/factory_core/providers/tracker/jira.py`, `tests/test_provider_tracker_jira.py`

1. Update the two existing tests in `tests/test_provider_tracker_jira.py`. Replace
   `test_set_status_finds_transition_and_posts_its_id` (lines 214-233) and
   `test_set_status_missing_transition_edge_fails_soft` (lines 236-254):

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
       result = tracker.set_status("PROJ-1", "in_review")

       get_call, post_call = calls
       assert get_call[:2] == ("GET", "/issue/PROJ-1/transitions")
       assert post_call[:2] == ("POST", "/issue/PROJ-1/transitions")
       assert post_call[2] == {"transition": {"id": "41"}}  # "Send to Review" -> In review
       assert result is True


   def test_set_status_missing_transition_edge_fails_soft(monkeypatch, capsys):
       from factory_core.providers.tracker.jira import JiraTracker

       _set_jira_env(monkeypatch)
       tracker = JiraTracker()
       calls = []

       def fake_request(self, method, path, params=None, json_body=None):
           calls.append(method)
           return {"transitions": []}  # no edges available

       monkeypatch.setattr(JiraTracker, "_request", fake_request)
       result = tracker.set_status("PROJ-1", "in_review")  # must not raise

       assert calls == ["GET"]  # no POST attempted
       err = capsys.readouterr().err
       assert "jira:" in err
       assert "PROJ-1" in err
       assert "in review" in err.lower() or "In review" in err
       assert result is False
   ```

2. Verify fail:

   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_provider_tracker_jira.py -k set_status -v
   ```

   Expected: both fail on the new `assert result is ...` line (`set_status` currently returns
   `None`).

3. Implement in `scripts/factory_core/providers/tracker/jira.py`, replacing `set_status` (lines
   152-167):

   ```python
       def set_status(self, id: str, canonical: str) -> bool:
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
               return False
           self._request("POST", f"/issue/{id}/transitions",
                          json_body={"transition": {"id": match["id"]}})
           return True
   ```

4. Verify pass:

   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_provider_tracker_jira.py -v
   ```

   Expected: all tests pass, including `test_resolve_item_transitions_to_done` (`resolve_item`
   calls `self.set_status(id, "done")` and ignores the return — unaffected by the new `bool`).

5. Commit:

   ```bash
   git add scripts/factory_core/providers/tracker/jira.py tests/test_provider_tracker_jira.py
   git commit -m "fix(tracker): JiraTracker.set_status returns bool per widened ABC contract"
   ```

### Task 6 — Contract test: both trackers assert `bool` returns

**Files:** `tests/test_tracker_contract.py`

1. Update `test_set_status_moves_through_canonical_vocabulary` and
   `test_set_status_unknown_item_is_safe_noop` in `tests/test_tracker_contract.py` (lines 274-288):

   ```python
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
   ```

2. Verify pass (this is a parity-locking, test-only task appended after Tasks 4-5 already landed
   the `bool` behavior on both trackers — the genuine TDD red for this contract was Tasks 4 and 5's
   own red/green cycles, not this one):

   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_tracker_contract.py -k set_status -v
   ```

   Expected: both parametrized cases (github/jira) of each of the two updated tests pass.

3. No implementation change needed here (already delivered by Tasks 4-5) — this task only adds
   test assertions.

4. Verify pass:

   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_tracker_contract.py -v
   ```

   Expected: all tests pass — 6 test functions × 2 params (`github`, `jira`) = 12 cases total in
   the file.

5. Commit:

   ```bash
   git add tests/test_tracker_contract.py
   git commit -m "test(tracker): assert set_status bool contract holds for both GitHubTracker and JiraTracker"
   ```

### Task 7 — `cli.py:_tracker_set_status` exits non-zero on failure

**Files:** `scripts/factory_core/providers/cli.py`, `tests/test_provider_cli.py`

1. Write failing tests, appended at the end of `tests/test_provider_cli.py`, matching the
   `_preflight` test pattern already in the file:

   ```python
   def test_tracker_set_status_exits_0_on_success(monkeypatch):
       import factory_core.providers.cli as cli_mod

       class _FakeTracker:
           def set_status(self, id, canonical):
               return True
       monkeypatch.setattr(cli_mod, "get_tracker", lambda: _FakeTracker())
       monkeypatch.setattr(sys, "argv", ["cli.py", "tracker", "set-status", "--id", "42", "--status", "blocked"])
       cli_mod.main()  # must not raise / must not SystemExit


   def test_tracker_set_status_prints_error_and_exits_1_on_failure(monkeypatch, capsys):
       import factory_core.providers.cli as cli_mod

       class _FakeTracker:
           def set_status(self, id, canonical):
               return False
       monkeypatch.setattr(cli_mod, "get_tracker", lambda: _FakeTracker())
       monkeypatch.setattr(sys, "argv", ["cli.py", "tracker", "set-status", "--id", "42", "--status", "blocked"])
       with pytest.raises(SystemExit) as exc:
           cli_mod.main()
       assert exc.value.code == 1
       err = capsys.readouterr().err
       assert "ERROR:" in err
       assert "42" in err
       assert "blocked" in err


   def test_tracker_set_status_catches_runtime_error_and_exits_1(monkeypatch, capsys):
       import factory_core.providers.cli as cli_mod

       class _FakeTracker:
           def set_status(self, id, canonical):
               raise RuntimeError("jira: POST /issue/42/transitions failed (500): boom")
       monkeypatch.setattr(cli_mod, "get_tracker", lambda: _FakeTracker())
       monkeypatch.setattr(sys, "argv", ["cli.py", "tracker", "set-status", "--id", "42", "--status", "blocked"])
       with pytest.raises(SystemExit) as exc:
           cli_mod.main()
       assert exc.value.code == 1
       err = capsys.readouterr().err
       assert "ERROR: jira: POST /issue/42/transitions failed (500): boom" in err
   ```

2. Verify fail:

   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_provider_cli.py -k tracker_set_status -v
   ```

   Expected: `test_tracker_set_status_exits_0_on_success` passes trivially (current code already
   ignores the return and exits 0), but the other two fail — `_tracker_set_status` never calls
   `sys.exit(1)`, so `pytest.raises(SystemExit)` fails to raise.

3. Implement in `scripts/factory_core/providers/cli.py`, replacing `_tracker_set_status` (lines
   47-48):

   ```python
   def _tracker_set_status(args):
       try:
           ok = get_tracker().set_status(args.id, args.status)
       except RuntimeError as e:
           print(f"ERROR: {e}", file=sys.stderr)
           sys.exit(1)
       if not ok:
           print(f"ERROR: board move to {args.status!r} failed for issue {args.id}", file=sys.stderr)
           sys.exit(1)
   ```

4. Verify pass:

   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_provider_cli.py -v
   ```

   Expected: all tests pass, including the three new ones and every pre-existing test in the file.

5. Commit:

   ```bash
   git add scripts/factory_core/providers/cli.py tests/test_provider_cli.py
   git commit -m "fix(cli): tracker set-status exits 1 with ERROR on failure, activating BOARD_MOVE_OK"
   ```

### Task 8 — DAG-node advisory guard so a board-move failure never fails a successful merge/transition

**Files:** `workflows/archon-dark-factory.yaml`

1. No pytest coverage applies to this YAML/bash edit (this repo's DAG has no unit-test harness for
   individual bash blocks beyond the shell-sourcing tests already covering `entrypoint.sh`/
   `scheduler.sh`, neither of which this task touches). Verification is the DAG-parse check in
   step 3.

2. Edit `workflows/archon-dark-factory.yaml` at the two exact call sites:

   Line 257 — before:
   ```
         python3 "$_PCLI" tracker set-status --id "$ISSUE" --status done
   ```
   after:
   ```
         python3 "$_PCLI" tracker set-status --id "$ISSUE" --status done \
           || echo "WARNING: board move to done failed for #$ISSUE — check board state manually"
   ```

   Line 1195 (the second line of the existing two-line invocation) — before:
   ```
         python3 "${CLONE_DIR:-.}/dark-factory/scripts/factory_core/providers/cli.py" \
           tracker set-status --id "$ISSUE" --status in_review
   ```
   after:
   ```
         python3 "${CLONE_DIR:-.}/dark-factory/scripts/factory_core/providers/cli.py" \
           tracker set-status --id "$ISSUE" --status in_review \
           || echo "WARNING: board move to in_review failed for #$ISSUE — check board state manually"
   ```

3. Verify the DAG still passes CI's actual DAG-structure gate (`.github/workflows/ci.yml:36`):

   ```bash
   cd /workspace/dark-factory && python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
   ```

   Expected: exits 0 with no errors printed — YAML still parses and node structure is unchanged
   (only bash body text changed inside two existing nodes).

4. Commit:

   ```bash
   git add workflows/archon-dark-factory.yaml
   git commit -m "fix(dag): advisory-guard the two unguarded tracker set-status DAG call sites"
   ```

### Task 9 — Full suite regression pass

**Files:** none (verification only)

1. Run the complete test suite, matching CLAUDE.md's stated CI command:

   ```bash
   cd /workspace/dark-factory && python -m pytest tests/ -v
   ```

   Expected: all tests pass, zero failures/errors — in particular
   `tests/test_factory_core_board.py`, `tests/test_provider_tracker_parity.py`,
   `tests/test_provider_tracker_jira.py`, `tests/test_tracker_contract.py`,
   `tests/test_provider_cli.py` (this ticket's touched files) and every other test file (regression
   check — nothing outside this ticket's scope should have moved).

2. No commit for this task (verification only, no file changes expected). If any failure surfaces,
   fix it under the task that owns the affected file and re-run this step before proceeding.

---

## Design Decisions

1. **`bool`, not a new exception class** — matches the sibling `CodeHost` ABC's established idiom
   for subprocess-backed provider mutations (spec Q&A 2). Keeps `board.set_board_status()`'s four
   direct callers at true zero-diff (a bool return they simply don't consume, vs. a new
   `try/except SomeError: pass` they'd otherwise need).
2. **`_find_item_by_number_checked` returns `(id, lookup_ok)`, not a third `None`/sentinel state**
   — two orthogonal booleans (`lookup_ok`, `item_id truthy`) already cover the three real states
   (transport failure / genuinely absent / found) without inventing a tri-state return value.
3. **`board.set_board_status()` is untouched in shape** — it still calls the unchecked
   `_find_item_by_number` wrapper and discards `_item_edit_status`'s bool, so its 4 direct callers
   (`breaker.py`, `rescue.py`, `deconflict.py`, `epic_autopilot.py`) and the 5th caller via
   `scripts/factory_core/cli.py:_board_move`/`scheduler.sh` see zero behavior change (spec
   Requirement 3, Alternatives Considered #1).
4. **DAG guard is two lines, not a shared bash helper** — `entrypoint.sh`'s existing
   `set_board_status()` wrapper already provides that abstraction for its three call sites; the
   two DAG nodes are bare `python3 ... tracker set-status` invocations with no existing wrapper to
   extend, so a one-line `|| echo WARNING` per site (spec Requirement 8a) is the minimal, in-scope
   fix — introducing a new shared DAG-bash function for two call sites would be undue scope growth
   per CLAUDE.md's scope discipline.

## Out of Scope (per spec)

- `board.set_board_status()`'s four direct Python callers gain no new failure-handling logic.
- Every other `cli.py` verb sharing the same swallow-failure `subprocess.run(capture_output=True)`
  pattern (`_tracker_label`, `_tracker_comment`, `_tracker_resolve`, `_codehost_*`) — flagged as a
  possible separate follow-up, not touched here.
- `entrypoint.sh` itself — no change; its `BOARD_MOVE_OK` guard already branches on this process's
  exit code and needs nothing further.
