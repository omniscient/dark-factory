# Implementation Plan: Close the board write path and detect off-board `ready-for-agent` issues

**Issue:** #418

**Spec:** `docs/superpowers/specs/2026-09-10-board-add-off-board-detection-design.md`
**Related:** #395 (silent `runs.jsonl` append failure — same failure class), #403 (last issue
that reached the board before the write path broke)

---

## Goal

Close the two halves of #418: (1) give `scripts/factory_core/board.py` an `add_to_board()`
that wraps `gh project item-add`, wire it into all three `gh issue create` sites so a
factory-filed ticket always lands on the board, and (2) give `scheduler.sh`'s idle branch a
self-check that diffs open `ready-for-agent` issues against the board snapshot, replacing the
ambiguous `skip=nothing_to_do` with `skip=queue_unreachable off_board=N issues=#a,#b` (or
`skip=nothing_to_do off_board_check=failed` if the check itself can't run) whenever the write
path breaks again for any reason.

## Architecture

No new modules. Eight existing files change, plus one new test file:

- `scripts/factory_core/board.py` — new `add_to_board(issue_num, issue_url) -> bool`, alongside
  `find_board_item`/`set_board_status`. Wraps `gh project item-add`, falls back to
  `_find_item_by_number_checked` if the add response is unparseable, then calls
  `_item_edit_status(item_id, STATUS_BACKLOG)`.
- `scripts/factory_core/cli.py` — new `board-add` CLI verb mirroring the existing `board-move`
  verb: `--issue` (int, required), `--url` (required), exits 1 on failure.
- `commands/dark-factory-conformance.md` — the scope-spillover `gh issue create` site (Step C,
  `create` action) captures the URL (already does) and calls `board-add` right after.
- `commands/ceiling-revisit.md` — both `gh issue create` sites (Phase 4's XL-bucket issue, Phase
  5's unconditional next-weekly-revisit issue) capture the URL and call `board-add` right after.
  Only the tracked `commands/ceiling-revisit.md` is edited — `.archon/commands/ceiling-revisit.md`
  is an untracked runtime copy `entrypoint.sh` recreates from the baked image on every run and
  git-excludes, so editing it would have no lasting effect. Note: Phase 5's issue already
  carries `ready-for-agent` (`--label "ready-for-agent"` at the end of its `gh issue create`)
  — the spec's rationale for wiring both sites ("neither ticket carries ready-for-agent") is
  factually true only for Phase 4's ticket, not Phase 5's; both sites still get wired per spec
  Requirement 2, so this doesn't change any task, only the stated justification for one of them.
- `tests/test_ceiling_revisit_command.py` — `test_target_path_markers_preserved`'s expected
  `# TARGET-PATH` count moves from 2 to 4 (Tasks 4 and 5 each add one marker for their new
  `board-add` call).
- `scheduler.sh` — new `off_board_ready_issues()` next to `fetch_board_items`, plus the idle
  branch at the end of the main loop (currently a bare `echo ... skip=nothing_to_do`) replaced
  with the three-way branch: check failed / off-board issues found / genuinely idle. The
  capture **must** be the `if` condition itself (`if ! OFF_BOARD=$(off_board_ready_issues ...)`)
  — `scheduler.sh:2` is `set -euo pipefail` and `:3` adds `set -E`, so a bare
  `VAR=$(cmd); [ $? -ne 0 ]` would exit the whole scheduler process on a transient `gh` failure
  instead of reporting it, reproducing this ticket's own bug class inside its fix.
- `tests/test_factory_core_board.py` — add `add_to_board` coverage (success, item-add-failure
  with successful fallback, both failing).
- `tests/test_factory_core_cli.py` — add `board-add` verb coverage (success exit 0, failure exit
  1, args forwarded correctly).
- `tests/test_off_board_detection.sh` (new) — modeled on `tests/test_scheduler_pagination.sh`'s
  `SCHEDULER_SOURCE_ONLY=1` + exported `gh()` stub pattern: exercises
  `off_board_ready_issues()` directly (found / healthy / lookup-failed / full-page cases);
  **executes** the `if ! OFF_BOARD=$(...)` idiom under `set -euo pipefail` to prove it survives
  a `gh` failure rather than killing the shell, *and* separately greps the real `scheduler.sh`
  source for that same idiom (an executed copy alone can't catch a future regression back to
  the unsafe `VAR=$(cmd); [ $? -ne 0 ]` form in the actual file — only reading the file can);
  and renders the idle-branch log line through the exact same `grep -c .` / `head -10` /
  `sed 's/^/#/'` / `paste -sd, -` pipeline `scheduler.sh` uses, for both a single off-board
  issue and a 12-issue case that exercises the `+N more` cap (spec Requirement 8).

## Tech Stack

Python 3 (stdlib only: `subprocess`, `json` — already imported in `board.py`), `pytest` for the
Python tests, `bash`/`jq` for `scheduler.sh` and its test (matching the existing
`tests/test_scheduler_pagination.sh` harness). No new dependencies.

---

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/board.py` | New `add_to_board(issue_num, issue_url) -> bool` |
| `scripts/factory_core/cli.py` | New `board-add` verb (`_board_add`, subparser) |
| `commands/dark-factory-conformance.md` | Spillover `create` action calls `board-add` after `gh issue create` |
| `commands/ceiling-revisit.md` | Phase 4 (XL) and Phase 5 (next revisit) call `board-add` after `gh issue create` |
| `scheduler.sh` | New `off_board_ready_issues()`; idle-branch log line becomes 3-way |
| `tests/test_factory_core_board.py` | Add `add_to_board` success/fallback/failure cases |
| `tests/test_factory_core_cli.py` | Add `board-add` verb cases |
| `tests/test_ceiling_revisit_command.py` | Update `test_target_path_markers_preserved`'s expected count (2 → 4) |
| `tests/test_off_board_detection.sh` | New — `off_board_ready_issues()` + idiom-survival + log-line-format coverage |

---

## Tasks

### Task 1 — `board.add_to_board()` (write-path core)

**Files:** `scripts/factory_core/board.py`, `tests/test_factory_core_board.py`

1. Write failing tests, appended to `tests/test_factory_core_board.py` (after
   `test_post_or_update_comment_updates_existing`):

   ```python
   def test_add_to_board_success(monkeypatch):
       calls = []
       def fake(cmd, **kw):
           calls.append(cmd)
           if "item-add" in cmd:
               return subprocess.CompletedProcess([], 0, stdout=json.dumps({"id": "ITEM42"}), stderr="")
           return _ok()
       monkeypatch.setattr(subprocess, "run", fake)
       assert board.add_to_board(42, "https://github.com/o/r/issues/42") is True
       add_call = next(c for c in calls if "item-add" in c)
       assert "https://github.com/o/r/issues/42" in add_call
       edit_call = next(c for c in calls if "item-edit" in c)
       assert "ITEM42" in edit_call
       assert board.STATUS_BACKLOG in edit_call


   def test_add_to_board_item_add_fails_falls_back_to_lookup(monkeypatch, capsys):
       calls = []
       def fake(cmd, **kw):
           calls.append(cmd)
           if "item-add" in cmd:
               return subprocess.CompletedProcess([], 1, stdout="", stderr="already exists")
           if "item-list" in cmd:
               return _items([{"id": "ITEM42", "content": {"number": 42, "type": "Issue"}}])
           return _ok()
       monkeypatch.setattr(subprocess, "run", fake)
       assert board.add_to_board(42, "https://github.com/o/r/issues/42") is True
       assert any("item-edit" in c for c in calls)
       err = capsys.readouterr().err
       assert "42" in err


   def test_add_to_board_item_add_and_fallback_fail(monkeypatch):
       calls = []
       def fake(cmd, **kw):
           calls.append(cmd)
           if "item-add" in cmd:
               return subprocess.CompletedProcess([], 1, stdout="", stderr="boom")
           if "item-list" in cmd:
               return subprocess.CompletedProcess([], 1, stdout="", stderr="rate limited")
           return _ok()
       monkeypatch.setattr(subprocess, "run", fake)
       assert board.add_to_board(42, "https://github.com/o/r/issues/42") is False
       assert not any("item-edit" in c for c in calls)
   ```

2. Verify fail:

   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_core_board.py -k add_to_board -v
   ```

   Expected: all three fail with `AttributeError: module 'factory_core.board' has no attribute
   'add_to_board'`.

3. Implement in `scripts/factory_core/board.py`, appended after `post_or_update_comment` (the
   file already imports `json`, `subprocess`, `sys` at the top — no new imports needed):

   ```python
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
   ```

4. Verify pass:

   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_core_board.py -v
   ```

   Expected: all tests pass, including the three new ones and every pre-existing test in the
   file (unaffected).

5. Commit:

   ```bash
   git add scripts/factory_core/board.py tests/test_factory_core_board.py
   git commit -m "feat(board): add add_to_board() wrapping gh project item-add, with lookup fallback"
   ```

### Task 2 — `board-add` CLI verb

**Files:** `scripts/factory_core/cli.py`, `tests/test_factory_core_cli.py`

1. Write failing tests, appended to `tests/test_factory_core_cli.py`:

   ```python
   def test_board_add_exits_0_on_success(monkeypatch):
       cli_mod = _cli(monkeypatch, FACTORY_PRODUCT_NAME="Acme")
       import factory_core.board as board_mod
       monkeypatch.setattr(board_mod, "add_to_board", lambda issue, url: True)
       monkeypatch.setattr(sys, "argv", [
           "cli.py", "board-add", "--issue", "42", "--url", "https://github.com/o/r/issues/42",
       ])
       cli_mod.main()  # must not raise / must not SystemExit


   def test_board_add_exits_1_on_failure(monkeypatch):
       cli_mod = _cli(monkeypatch, FACTORY_PRODUCT_NAME="Acme")
       import factory_core.board as board_mod
       monkeypatch.setattr(board_mod, "add_to_board", lambda issue, url: False)
       monkeypatch.setattr(sys, "argv", [
           "cli.py", "board-add", "--issue", "42", "--url", "https://github.com/o/r/issues/42",
       ])
       with pytest.raises(SystemExit) as exc:
           cli_mod.main()
       assert exc.value.code == 1


   def test_board_add_passes_issue_and_url(monkeypatch):
       cli_mod = _cli(monkeypatch, FACTORY_PRODUCT_NAME="Acme")
       import factory_core.board as board_mod
       calls = []
       monkeypatch.setattr(board_mod, "add_to_board",
           lambda issue, url: calls.append((issue, url)) or True)
       monkeypatch.setattr(sys, "argv", [
           "cli.py", "board-add", "--issue", "42", "--url", "https://github.com/o/r/issues/42",
       ])
       cli_mod.main()
       assert calls == [(42, "https://github.com/o/r/issues/42")]
   ```

2. Verify fail:

   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_core_cli.py -k board_add -v
   ```

   Expected: all three fail — `argparse` rejects the unknown `board-add` subcommand
   (`SystemExit` with code 2, not the asserted code / not-raising behavior).

3. Implement in `scripts/factory_core/cli.py`. Add `_board_add` right after `_board_move`
   (after line 14):

   ```python
   def _board_add(args):
       from factory_core.board import add_to_board
       ok = add_to_board(args.issue, args.url)
       if not ok:
           sys.exit(1)
   ```

   Register the subparser right after `bm.set_defaults(func=_board_move)` (in `main()`):

   ```python
       ba = sub.add_parser("board-add")
       ba.add_argument("--issue", type=int, required=True)
       ba.add_argument("--url", required=True)
       ba.set_defaults(func=_board_add)
   ```

4. Verify pass:

   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_core_cli.py -v
   ```

   Expected: all tests pass, including the three new ones and every pre-existing test in the
   file (unaffected).

5. Commit:

   ```bash
   git add scripts/factory_core/cli.py tests/test_factory_core_cli.py
   git commit -m "feat(cli): add board-add verb wrapping board.add_to_board"
   ```

### Task 3 — Wire `board-add` into the conformance spillover site

**Files:** `commands/dark-factory-conformance.md`

1. No pytest coverage applies (a markdown prompt document, not executed code). Verification is
   a `bash -n` syntax check on the edited snippet (step 3 below) plus a presence grep (step 4).

2. Edit `commands/dark-factory-conformance.md`, in the `create)` case block (lines 398-405),
   inserting the `board-add` call between the existing `SPILLOVER_NUM=...` line and the
   `SPILLOVER_TICKETS=...` line. Before:

   ```bash
         SPILLOVER_URL=$(gh issue create \
           --repo "$FACTORY_REPO_SLUG" \
           --title "$SPILLOVER_TITLE" \
           --body "$SPILLOVER_BODY" \
           --label "needs-triage,${BACKLOG_LABEL}")
         SPILLOVER_NUM=$(basename "$SPILLOVER_URL")
         SPILLOVER_TICKETS="$SPILLOVER_TICKETS $SPILLOVER_NUM"
         echo "scope-enforcement: created new spillover #${SPILLOVER_NUM} (key: $KEY)"
         ;;
   ```

   After:

   ```bash
         SPILLOVER_URL=$(gh issue create \
           --repo "$FACTORY_REPO_SLUG" \
           --title "$SPILLOVER_TITLE" \
           --body "$SPILLOVER_BODY" \
           --label "needs-triage,${BACKLOG_LABEL}")
         SPILLOVER_NUM=$(basename "$SPILLOVER_URL")
         # TARGET-PATH
         python3 dark-factory/scripts/factory_core/cli.py board-add \
           --issue "$SPILLOVER_NUM" --url "$SPILLOVER_URL" \
           || echo "scope-enforcement: WARNING board-add failed for spillover #${SPILLOVER_NUM}" >&2
         SPILLOVER_TICKETS="$SPILLOVER_TICKETS $SPILLOVER_NUM"
         echo "scope-enforcement: created new spillover #${SPILLOVER_NUM} (key: $KEY)"
         ;;
   ```

   (The `# TARGET-PATH` marker is on its own line, not trailing a `\` continuation — a `\`
   followed by whitespace and a comment does not continue a shell line, so appending the marker
   to the `python3 ... \` line itself would break the command at its first argument.)

3. Syntax-check the new block in isolation (dummy values stand in for the runtime-supplied
   variables):

   ```bash
   cd /workspace/dark-factory && cat > /tmp/spillover-snippet-check.sh << 'EOF'
   SPILLOVER_NUM=42
   SPILLOVER_URL="https://github.com/o/r/issues/42"
   # TARGET-PATH
   python3 dark-factory/scripts/factory_core/cli.py board-add \
     --issue "$SPILLOVER_NUM" --url "$SPILLOVER_URL" \
     || echo "scope-enforcement: WARNING board-add failed for spillover #${SPILLOVER_NUM}" >&2
   EOF
   bash -n /tmp/spillover-snippet-check.sh && echo "SYNTAX OK"
   rm -f /tmp/spillover-snippet-check.sh
   ```

   Expected output: `SYNTAX OK`

4. Confirm the edit landed. Match on the exact invocation rather than the bare word
   `board-add` — that word also appears in the same block's `|| echo "... WARNING board-add
   failed ..."` fallback line, so a bare `grep -c "board-add"` would report 2, not 1:

   ```bash
   cd /workspace/dark-factory && grep -c "cli.py board-add" commands/dark-factory-conformance.md
   ```

   Expected: `1`

5. Commit:

   ```bash
   git add commands/dark-factory-conformance.md
   git commit -m "fix(conformance): call board-add after filing a scope-spillover ticket"
   ```

### Task 4 — Wire `board-add` into `ceiling-revisit.md` Phase 4 (XL-bucket issue)

**Files:** `commands/ceiling-revisit.md`

1. No pytest coverage applies to this task's own edit; verification is a `bash -n` check
   (step 3) plus a presence grep (step 4), same as Task 3. Do **not** run the full
   `tests/` suite at the end of this task — this task's edit alone leaves
   `tests/test_ceiling_revisit_command.py::test_target_path_markers_preserved`
   transiently red (it asserts `== 2`, and this edit brings the file's `# TARGET-PATH`
   count to 3); Task 5 adds the file's fourth marker and updates that assertion to `== 4`
   in the same commit. This is expected, not a regression — full-suite verification happens
   in Task 8, after Task 5 has landed.

2. Edit `commands/ceiling-revisit.md` Phase 4 (lines 157-181), wrapping the existing
   `gh issue create` in a capture and adding the `board-add` call. Before:

   ```bash
     if [ "$XL_ACTION" = "file" ]; then
       gh issue create \
         --repo "$REPO" \
         --title "Revisit XL=always-above-ceiling rule in is_above_ceiling() — scheduler_lib.sh" \
         --body "## Purpose
   ...
   *Filed automatically by weekly ceiling revisit*" \
         --label "enhancement" \
         --label "priority: should-have"
     elif [ "$XL_ACTION" = "skip-lookup-failed" ]; then
   ```

   After:

   ```bash
     if [ "$XL_ACTION" = "file" ]; then
       XL_URL=$(gh issue create \
         --repo "$REPO" \
         --title "Revisit XL=always-above-ceiling rule in is_above_ceiling() — scheduler_lib.sh" \
         --body "## Purpose
   ...
   *Filed automatically by weekly ceiling revisit*" \
         --label "enhancement" \
         --label "priority: should-have")
       XL_NUM=$(basename "$XL_URL")
       # TARGET-PATH
       python3 dark-factory/scripts/factory_core/cli.py board-add \
         --issue "$XL_NUM" --url "$XL_URL" \
         || echo "ceiling-revisit: WARNING board-add failed for XL-ceiling issue #${XL_NUM}" >&2
     elif [ "$XL_ACTION" = "skip-lookup-failed" ]; then
   ```

   (Only the `gh issue create ... --label "priority: should-have"` invocation itself gains the
   `$(...)` wrapper and trailing `)`; the `--body` heredoc-style content in between is unchanged.)

3. Syntax-check the new block in isolation:

   ```bash
   cd /workspace/dark-factory && cat > /tmp/xl-snippet-check.sh << 'EOF'
   XL_URL=$(echo "https://github.com/o/r/issues/99")
   XL_NUM=$(basename "$XL_URL")
   # TARGET-PATH
   python3 dark-factory/scripts/factory_core/cli.py board-add \
     --issue "$XL_NUM" --url "$XL_URL" \
     || echo "ceiling-revisit: WARNING board-add failed for XL-ceiling issue #${XL_NUM}" >&2
   EOF
   bash -n /tmp/xl-snippet-check.sh && echo "SYNTAX OK"
   rm -f /tmp/xl-snippet-check.sh
   ```

   Expected output: `SYNTAX OK`

4. Confirm the edit landed:

   ```bash
   cd /workspace/dark-factory && grep -n "XL-ceiling issue" commands/ceiling-revisit.md
   ```

   Expected: one match.

5. Commit:

   ```bash
   git add commands/ceiling-revisit.md
   git commit -m "fix(ceiling-revisit): call board-add after filing the XL-bucket revisit issue"
   ```

### Task 5 — Wire `board-add` into `ceiling-revisit.md` Phase 5 (next weekly revisit issue)

**Files:** `commands/ceiling-revisit.md`, `tests/test_ceiling_revisit_command.py`

1. This task's edit to `commands/ceiling-revisit.md` interacts with an existing pytest
   assertion: `tests/test_ceiling_revisit_command.py::test_target_path_markers_preserved`
   asserts `text.count("# TARGET-PATH") == 2` (the file's two pre-existing Phase-1 markers).
   Task 4 added a third `# TARGET-PATH` marker (the XL-bucket `board-add` call) and this task
   adds a fourth (the next-weekly-revisit `board-add` call), so the count is 4 once both land.
   Verify the assertion is currently red against the post-Task-4 file (count is already 3,
   the stale `== 2` assertion fails):

   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_ceiling_revisit_command.py -k target_path_markers -v
   ```

   Expected: **FAILS** — `assert 3 == 2`. This confirms the assertion is stale before this
   task's edits (step 2 adds the file's fourth marker; step 3 below updates the assertion to
   match).

2. Edit `commands/ceiling-revisit.md` Phase 5 (lines 199-237), wrapping the existing
   `gh issue create` and adding the `board-add` call. Before:

   ```bash
   NEXT_TITLE="Revisit dispatch ceiling — re-measure success-by-size/type"
   gh issue create \
     --repo "$REPO" \
     --title "$NEXT_TITLE" \
     --body "## Purpose
   ...
   *Filed automatically by ${FACTORY_PRODUCT_NAME} weekly ceiling revisit agent*" \
     --label "enhancement" \
     --label "priority: should-have" \
     --label "size: S" \
     --label "ready-for-agent"
   ```

   After:

   ```bash
   NEXT_TITLE="Revisit dispatch ceiling — re-measure success-by-size/type"
   NEXT_URL=$(gh issue create \
     --repo "$REPO" \
     --title "$NEXT_TITLE" \
     --body "## Purpose
   ...
   *Filed automatically by ${FACTORY_PRODUCT_NAME} weekly ceiling revisit agent*" \
     --label "enhancement" \
     --label "priority: should-have" \
     --label "size: S" \
     --label "ready-for-agent")
   NEXT_NUM=$(basename "$NEXT_URL")
   # TARGET-PATH
   python3 dark-factory/scripts/factory_core/cli.py board-add \
     --issue "$NEXT_NUM" --url "$NEXT_URL" \
     || echo "ceiling-revisit: WARNING board-add failed for next weekly revisit issue #${NEXT_NUM}" >&2
   ```

   (As in Task 4: only the `gh issue create ... --label "ready-for-agent"` invocation itself
   gains the `$(...)` wrapper and trailing `)`; the `--body` content in between is unchanged —
   the `...` above elides it for brevity, it is not a placeholder to fill in.)

3. Syntax-check the new block in isolation:

   ```bash
   cd /workspace/dark-factory && cat > /tmp/next-snippet-check.sh << 'EOF'
   NEXT_URL=$(echo "https://github.com/o/r/issues/100")
   NEXT_NUM=$(basename "$NEXT_URL")
   # TARGET-PATH
   python3 dark-factory/scripts/factory_core/cli.py board-add \
     --issue "$NEXT_NUM" --url "$NEXT_URL" \
     || echo "ceiling-revisit: WARNING board-add failed for next weekly revisit issue #${NEXT_NUM}" >&2
   EOF
   bash -n /tmp/next-snippet-check.sh && echo "SYNTAX OK"
   rm -f /tmp/next-snippet-check.sh
   ```

   Expected output: `SYNTAX OK`

4. Update the now-stale assertion in `tests/test_ceiling_revisit_command.py`. Replace
   `test_target_path_markers_preserved` (lines 34-39):

   ```python
   def test_target_path_markers_preserved():
       text = _text()
       assert text.count("# TARGET-PATH") == 4, (
           "Phase 1's original two '# TARGET-PATH' markers, plus the two added by #418's "
           "board-add calls (Phase 4's XL-bucket issue, Phase 5's next-weekly-revisit issue), "
           "must all be present"
       )
   ```

5. Verify pass:

   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_ceiling_revisit_command.py -v
   ```

   Expected: all tests in the file pass, including the updated `test_target_path_markers_preserved`
   (count is now 4) and every pre-existing test (unaffected — this task didn't touch the XL
   rule name, the `scheduler_lib.sh` citation, the duplicate/policy guard, or the guard-anchor
   substring relationship).

6. Confirm both `ceiling-revisit.md` sites call `board-add`. Match on the exact invocation
   rather than the bare word `board-add` — that word also appears in each site's own
   `|| echo "... WARNING board-add failed ..."` fallback line, so a bare `grep -c "board-add"`
   would report 4, not 2:

   ```bash
   cd /workspace/dark-factory && grep -c "cli.py board-add" commands/ceiling-revisit.md
   ```

   Expected: `2`

7. Commit:

   ```bash
   git add commands/ceiling-revisit.md tests/test_ceiling_revisit_command.py
   git commit -m "fix(ceiling-revisit): call board-add after filing the next weekly revisit issue"
   ```

### Task 6 — `scheduler.sh`: `off_board_ready_issues()` and the idle-branch self-check

**Files:** `scheduler.sh`

1. No pytest coverage applies to this bash file; Task 7 adds the executable test. This task's
   own verification is a bash syntax check (step 4).

2. Add `off_board_ready_issues()` in `scheduler.sh` immediately after `fetch_board_items` (its
   closing `}` is at line 623; insert after the blank line 624, before `get_items_by_status` at
   line 625, so the new comment block doesn't butt directly against `fetch_board_items`'s `}`):

   ```bash
   # Detects issues that carry ready-for-agent but never reached the board (this
   # ticket's root cause, or any future write-path gap). Prints a comma-joined,
   # '#'-prefixed, capped-at-10 issue list (empty string = none found); returns
   # non-zero if the gh issue list lookup itself failed, so the caller can tell
   # "queue is healthy" apart from "couldn't check."
   off_board_ready_issues() {
     local board_items="$1" ready_json
     # --limit is REQUIRED: `gh issue list` defaults to 30 results and truncates SILENTLY.
     # A detector for a silently-truncated queue that is itself silently truncated would be
     # this ticket's own bug class. 200 is well clear of any plausible ready-for-agent
     # population; the guard below turns a future overflow into a loud failure rather than
     # an under-report.
     ready_json=$(gh issue list --repo "$FACTORY_REPO_SLUG" --state open \
       --label ready-for-agent --limit 200 --json number 2>/dev/null) || return 1
     echo "$ready_json" | jq -e 'type == "array"' >/dev/null 2>&1 || return 1
     # A full page means the cap may have truncated the result: report "cannot check"
     # rather than an under-count.
     [ "$(echo "$ready_json" | jq 'length')" -ge 200 ] && return 1
     echo "$ready_json" | jq -r --argjson board "$board_items" \
       '([.[].number] - [$board.items[].content.number]) | sort | .[]'
   }
   ```

3. Replace the idle-branch log line (currently lines 1613-1617):

   Before:

   ```bash
     if [ -n "$DISPATCHED" ]; then
       echo "[$(date -u +%FT%TZ)] backlog=${BACKLOG_COUNT} refined=${REFINED_COUNT} in_progress=${IN_PROGRESS_COUNT}/${MAX_IN_PROGRESS} in_review=${IN_REVIEW_COUNT}/${MAX_IN_REVIEW} factory_running=${FACTORY_RUNNING}/${FACTORY_WIP_LIMIT} refine_running=${REFINE_RUNNING}/${REFINE_WIP_LIMIT} dispatched=\"${DISPATCHED}\" main_red=${MAIN_IS_RED} graphql=${BUDGET}"
     else
       echo "[$(date -u +%FT%TZ)] backlog=${BACKLOG_COUNT} refined=${REFINED_COUNT} in_progress=${IN_PROGRESS_COUNT}/${MAX_IN_PROGRESS} in_review=${IN_REVIEW_COUNT}/${MAX_IN_REVIEW} factory_running=${FACTORY_RUNNING}/${FACTORY_WIP_LIMIT} refine_running=${REFINE_RUNNING}/${REFINE_WIP_LIMIT} skip=nothing_to_do main_red=${MAIN_IS_RED} graphql=${BUDGET}"
     fi
   ```

   After:

   ```bash
     if [ -n "$DISPATCHED" ]; then
       echo "[$(date -u +%FT%TZ)] backlog=${BACKLOG_COUNT} refined=${REFINED_COUNT} in_progress=${IN_PROGRESS_COUNT}/${MAX_IN_PROGRESS} in_review=${IN_REVIEW_COUNT}/${MAX_IN_REVIEW} factory_running=${FACTORY_RUNNING}/${FACTORY_WIP_LIMIT} refine_running=${REFINE_RUNNING}/${REFINE_WIP_LIMIT} dispatched=\"${DISPATCHED}\" main_red=${MAIN_IS_RED} graphql=${BUDGET}"
     else
       # `if VAR=$(cmd); then` keeps a non-zero return non-fatal under `set -e`; a bare
       # `VAR=$(cmd)` followed by `[ $? -ne 0 ]` would exit the scheduler instead.
       if ! OFF_BOARD=$(off_board_ready_issues "$BOARD_ITEMS"); then
         echo "[$(date -u +%FT%TZ)] backlog=${BACKLOG_COUNT} refined=${REFINED_COUNT} in_progress=${IN_PROGRESS_COUNT}/${MAX_IN_PROGRESS} in_review=${IN_REVIEW_COUNT}/${MAX_IN_REVIEW} factory_running=${FACTORY_RUNNING}/${FACTORY_WIP_LIMIT} refine_running=${REFINE_RUNNING}/${REFINE_WIP_LIMIT} skip=nothing_to_do off_board_check=failed main_red=${MAIN_IS_RED} graphql=${BUDGET}"
       elif [ -n "$OFF_BOARD" ]; then
         OFF_BOARD_COUNT=$(echo "$OFF_BOARD" | grep -c .)
         OFF_BOARD_LIST=$(echo "$OFF_BOARD" | head -10 | sed 's/^/#/' | paste -sd, -)
         [ "$OFF_BOARD_COUNT" -gt 10 ] && OFF_BOARD_LIST="${OFF_BOARD_LIST},+$((OFF_BOARD_COUNT - 10))more"
         echo "[$(date -u +%FT%TZ)] backlog=${BACKLOG_COUNT} refined=${REFINED_COUNT} in_progress=${IN_PROGRESS_COUNT}/${MAX_IN_PROGRESS} in_review=${IN_REVIEW_COUNT}/${MAX_IN_REVIEW} factory_running=${FACTORY_RUNNING}/${FACTORY_WIP_LIMIT} refine_running=${REFINE_RUNNING}/${REFINE_WIP_LIMIT} skip=queue_unreachable off_board=${OFF_BOARD_COUNT} issues=${OFF_BOARD_LIST} main_red=${MAIN_IS_RED} graphql=${BUDGET}"
       else
         echo "[$(date -u +%FT%TZ)] backlog=${BACKLOG_COUNT} refined=${REFINED_COUNT} in_progress=${IN_PROGRESS_COUNT}/${MAX_IN_PROGRESS} in_review=${IN_REVIEW_COUNT}/${MAX_IN_REVIEW} factory_running=${FACTORY_RUNNING}/${FACTORY_WIP_LIMIT} refine_running=${REFINE_RUNNING}/${REFINE_WIP_LIMIT} skip=nothing_to_do main_red=${MAIN_IS_RED} graphql=${BUDGET}"
       fi
     fi
   ```

   Every field that existed before (`backlog=`, `refined=`, `in_progress=`, `in_review=`,
   `factory_running=`, `refine_running=`, `main_red=`, `graphql=`) stays byte-identical in all
   three sub-branches, so operator tooling that greps this line is unaffected.

4. Syntax-check the whole file:

   ```bash
   cd /workspace/dark-factory && bash -n scheduler.sh && echo "SYNTAX OK"
   ```

   Expected output: `SYNTAX OK`

5. Confirm the existing pagination test (which sources this same file) still passes, as a smoke
   check that the new function didn't break sourcing:

   ```bash
   cd /workspace/dark-factory && bash tests/test_scheduler_pagination.sh
   ```

   Expected: `PASS: fetch_board_items paginates ProjectV2 items`, possibly preceded by output
   from sourcing `scheduler.sh` itself (e.g. a `providers preflight` line and/or a
   `.archon/.env not found` warning) — that banner is pre-existing `scheduler.sh` behavior,
   unrelated to this task's edit, and is not a failure.

6. Commit:

   ```bash
   git add scheduler.sh
   git commit -m "fix(scheduler): detect off-board ready-for-agent issues instead of reporting nothing_to_do"
   ```

### Task 7 — New scheduler test: `tests/test_off_board_detection.sh`

**Files:** `tests/test_off_board_detection.sh` (new)

1. This is itself a test file (bash, no separate pytest red/green cycle) — the "red" step is
   running it before Task 6 lands, but since Task 6 is already committed by this point in TDD
   task ordering within this plan, write the file and verify it passes as the primary signal
   (mirroring `tests/test_scheduler_pagination.sh`, which has no failing counterpart either).
   Create `tests/test_off_board_detection.sh`:

   ```bash
   #!/usr/bin/env bash
   # Unit test for scheduler.sh's off-board ready-for-agent detection (issue #418).
   # Run: bash tests/test_off_board_detection.sh
   set -euo pipefail

   SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
   SCHED="$SCRIPT_DIR/../scheduler.sh"

   docker() { return 0; }
   export -f docker

   export GH_TOKEN="${GH_TOKEN:-stub-token}"
   export CLAUDE_CODE_OAUTH_TOKEN="${CLAUDE_CODE_OAUTH_TOKEN:-stub-token}"
   export SCHEDULER_SOURCE_ONLY=1
   export SCHEDULER_STATE_DIR="$(mktemp -d /tmp/sched-offboard-test-statedir-XXXXXX)"
   export FACTORY_CORE_CLI="$SCRIPT_DIR/../scripts/factory_core/cli.py"
   export FACTORY_REPO_SLUG="omniscient/dark-factory"

   cleanup() {
     rm -rf "$SCHEDULER_STATE_DIR"
   }
   trap cleanup EXIT

   BOARD_ITEMS='{"items":[{"content":{"number":101,"title":"on board","type":"Issue"},"labels":["ready-for-agent"],"status":"Backlog"}]}'

   # --- Case 1: an open ready-for-agent issue (#102) absent from the board ---
   gh() {
     if echo "$*" | grep -q 'issue list'; then
       echo '[{"number":101},{"number":102}]'
       return 0
     fi
     echo "unexpected gh call: $*" >&2
     return 1
   }
   export -f gh

   source "$SCHED"

   OFF_BOARD="$(off_board_ready_issues "$BOARD_ITEMS")"
   if [ "$OFF_BOARD" != "102" ]; then
     echo "FAIL: expected off-board issue 102, got: $OFF_BOARD" >&2
     exit 1
   fi
   echo "PASS: off_board_ready_issues finds issue #102 missing from the board"

   # --- Case 2: every open ready-for-agent issue is already on the board ---
   gh() {
     if echo "$*" | grep -q 'issue list'; then
       echo '[{"number":101}]'
       return 0
     fi
     return 1
   }
   export -f gh

   OFF_BOARD="$(off_board_ready_issues "$BOARD_ITEMS")"
   if [ -n "$OFF_BOARD" ]; then
     echo "FAIL: expected no off-board issues, got: $OFF_BOARD" >&2
     exit 1
   fi
   echo "PASS: off_board_ready_issues returns empty when the board is healthy"

   # --- Case 3: the gh issue list lookup itself fails ---
   gh() {
     if echo "$*" | grep -q 'issue list'; then
       return 1
     fi
     return 1
   }
   export -f gh

   if off_board_ready_issues "$BOARD_ITEMS" >/dev/null 2>/dev/null; then
     echo "FAIL: expected off_board_ready_issues to return non-zero on a gh failure" >&2
     exit 1
   fi
   echo "PASS: off_board_ready_issues returns non-zero when gh issue list fails"

   # --- Case 4: gh issue list returns a full (200) page -- the count can't be trusted ---
   gh() {
     if echo "$*" | grep -q 'issue list'; then
       python3 -c 'import json; print(json.dumps([{"number": n} for n in range(200)]))'
       return 0
     fi
     return 1
   }
   export -f gh

   if off_board_ready_issues "$BOARD_ITEMS" >/dev/null 2>/dev/null; then
     echo "FAIL: expected off_board_ready_issues to fail closed on a full (200) page" >&2
     exit 1
   fi
   echo "PASS: off_board_ready_issues fails closed when the ready-for-agent page is full"

   # --- Case 5 (operator gate, blocking correction): under set -euo pipefail + set -E
   # (both active in this sourced shell -- scheduler.sh:2-3), the main-loop idiom
   # `if ! OFF_BOARD=$(off_board_ready_issues ...); then` must NOT kill this shell when the
   # function returns non-zero. This executes the real idiom rather than grepping for it --
   # a source-grep alone would not have caught the set -e regression the operator gate found.
   gh() { return 1; }
   export -f gh

   REACHED_AFTER=false
   if ! OFF_BOARD=$(off_board_ready_issues "$BOARD_ITEMS"); then
     RESULT_LINE="skip=nothing_to_do off_board_check=failed"
   elif [ -n "$OFF_BOARD" ]; then
     RESULT_LINE="skip=queue_unreachable off_board=BUG_SHOULD_NOT_REACH"
   else
     RESULT_LINE="skip=nothing_to_do"
   fi
   REACHED_AFTER=true

   if [ "$REACHED_AFTER" != "true" ] || [ "$RESULT_LINE" != "skip=nothing_to_do off_board_check=failed" ]; then
     echo "FAIL: the if-condition-capture idiom did not survive a gh failure under set -e: $RESULT_LINE" >&2
     exit 1
   fi
   echo "PASS: 'if ! OFF_BOARD=\$(off_board_ready_issues ...)' survives a gh failure under set -euo pipefail"

   # --- Case 6 (companion to Case 5): Case 5 proves the idiom is safe in isolation, but a
   # copy of the idiom inside this test file cannot catch a future scheduler.sh regression
   # back to the unsafe `OFF_BOARD=$(...); [ $? -ne 0 ]` form -- only reading the real file
   # can. Guard the actual source.
   if ! grep -qF 'if ! OFF_BOARD=$(off_board_ready_issues "$BOARD_ITEMS"); then' "$SCHED"; then
     echo "FAIL: scheduler.sh's idle branch no longer uses the set -e-safe 'if ! OFF_BOARD=\$(...)' capture idiom" >&2
     exit 1
   fi
   echo "PASS: scheduler.sh's idle branch still uses the set -e-safe capture idiom"

   # --- Case 7: the single-off-board-issue rendering from the spec's own Testing section
   # ("skip=queue_unreachable off_board=1 issues=#N"), run through the identical
   # grep -c . / head -10 / sed 's/^/#/' / paste -sd, - pipeline scheduler.sh's idle branch
   # uses (Task 6) -- not a hand-built string.
   gh() {
     if echo "$*" | grep -q 'issue list'; then
       echo '[{"number":101},{"number":102}]'
       return 0
     fi
     return 1
   }
   export -f gh

   OFF_BOARD="$(off_board_ready_issues "$BOARD_ITEMS")"
   OFF_BOARD_COUNT=$(echo "$OFF_BOARD" | grep -c .)
   OFF_BOARD_LIST=$(echo "$OFF_BOARD" | head -10 | sed 's/^/#/' | paste -sd, -)
   [ "$OFF_BOARD_COUNT" -gt 10 ] && OFF_BOARD_LIST="${OFF_BOARD_LIST},+$((OFF_BOARD_COUNT - 10))more"
   LOG_LINE="skip=queue_unreachable off_board=${OFF_BOARD_COUNT} issues=${OFF_BOARD_LIST}"
   if [ "$LOG_LINE" != "skip=queue_unreachable off_board=1 issues=#102" ]; then
     echo "FAIL: expected 'skip=queue_unreachable off_board=1 issues=#102', got: $LOG_LINE" >&2
     exit 1
   fi
   echo "PASS: single off-board issue renders 'skip=queue_unreachable off_board=1 issues=#102'"

   # --- Case 8: spec Requirement 8 -- cap the issues= list at 10, then "+N more" -- exercised
   # against a real 12-issue off_board_ready_issues() result through the same pipeline.
   gh() {
     if echo "$*" | grep -q 'issue list'; then
       python3 -c 'import json; print(json.dumps([{"number": n} for n in range(201, 213)]))'
       return 0
     fi
     return 1
   }
   export -f gh

   OFF_BOARD="$(off_board_ready_issues "$BOARD_ITEMS")"
   OFF_BOARD_COUNT=$(echo "$OFF_BOARD" | grep -c .)
   OFF_BOARD_LIST=$(echo "$OFF_BOARD" | head -10 | sed 's/^/#/' | paste -sd, -)
   [ "$OFF_BOARD_COUNT" -gt 10 ] && OFF_BOARD_LIST="${OFF_BOARD_LIST},+$((OFF_BOARD_COUNT - 10))more"
   LOG_LINE="skip=queue_unreachable off_board=${OFF_BOARD_COUNT} issues=${OFF_BOARD_LIST}"
   EXPECTED="skip=queue_unreachable off_board=12 issues=#201,#202,#203,#204,#205,#206,#207,#208,#209,#210,+2more"
   if [ "$LOG_LINE" != "$EXPECTED" ]; then
     echo "FAIL: expected '$EXPECTED', got: $LOG_LINE" >&2
     exit 1
   fi
   echo "PASS: 12 off-board issues cap the issues= list at 10 with a '+2more' suffix"

   echo "PASS: all off-board detection cases"
   ```

2. Verify pass:

   ```bash
   cd /workspace/dark-factory && bash tests/test_off_board_detection.sh
   ```

   Expected output: the following nine `PASS:` lines, in order — possibly preceded by output
   from sourcing `scheduler.sh` itself (e.g. a `providers preflight` line and/or a
   `.archon/.env not found` warning on stderr); that banner is pre-existing `scheduler.sh`
   behavior, unrelated to this test, and is not a failure:

   ```
   PASS: off_board_ready_issues finds issue #102 missing from the board
   PASS: off_board_ready_issues returns empty when the board is healthy
   PASS: off_board_ready_issues returns non-zero when gh issue list fails
   PASS: off_board_ready_issues fails closed when the ready-for-agent page is full
   PASS: 'if ! OFF_BOARD=$(off_board_ready_issues ...)' survives a gh failure under set -euo pipefail
   PASS: scheduler.sh's idle branch still uses the set -e-safe capture idiom
   PASS: single off-board issue renders 'skip=queue_unreachable off_board=1 issues=#102'
   PASS: 12 off-board issues cap the issues= list at 10 with a '+2more' suffix
   PASS: all off-board detection cases
   ```

3. Commit:

   ```bash
   git add tests/test_off_board_detection.sh
   git commit -m "test(scheduler): cover off_board_ready_issues and the set -e survival idiom"
   ```

### Task 8 — Full suite regression pass

**Files:** none (verification only)

1. Run the complete pytest suite, matching CLAUDE.md's stated CI command:

   ```bash
   cd /workspace/dark-factory && python -m pytest tests/ -v
   ```

   Expected: all tests pass, zero failures/errors — in particular
   `tests/test_factory_core_board.py`, `tests/test_factory_core_cli.py`, and
   `tests/test_ceiling_revisit_command.py` (this ticket's touched Python test files) and every
   other test file (regression check — nothing outside this ticket's scope should have moved).

2. Run the two bash scheduler tests directly (neither is currently wired into
   `.github/workflows/ci.yml` — `tests/test_scheduler_pagination.sh` isn't either, so this
   matches existing project convention rather than introducing a new gap):

   ```bash
   cd /workspace/dark-factory && bash tests/test_scheduler_pagination.sh && bash tests/test_off_board_detection.sh
   ```

   Expected: both scripts print their `PASS:` lines and exit 0.

2a. Run `tests/test_smoke_gate.sh`, which CLAUDE.md names alongside pytest and the DAG checks
    as part of what CI runs (`.github/workflows/ci.yml:17`) — this plan doesn't touch
    `smoke_gate.sh` or any gate, but this confirms that's actually true:

    ```bash
    cd /workspace/dark-factory && bash tests/test_smoke_gate.sh
    ```

    Expected: exits 0 (no output change expected from this ticket's edits).

3. Run the workflow DAG checks (this plan doesn't touch `workflows/archon-dark-factory.yaml`,
   but this confirms that's actually true):

   ```bash
   cd /workspace/dark-factory && python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml && python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
   ```

   Expected: both exit 0 with no errors printed.

4. No commit for this task (verification only, no file changes expected). If any failure
   surfaces, fix it under the task that owns the affected file and re-run this step before
   proceeding.

---

## Design Decisions

1. **`gh project item-add`, not raw `addProjectV2ItemById` GraphQL.** Matches `board.py`'s
   existing convention (`item-list`/`item-edit` are both `gh project` subcommand wraps) and has
   direct precedent in `docs/archive/2026-08-28-dispatch-ceiling-weekly-revisit-plan.md:756-757`.
   Avoids an extra GraphQL round trip to resolve the issue's node id (spec Requirement 1).
2. **All three `gh issue create` sites get wired, not just the conformance one the Acceptance
   section names explicitly.** The Acceptance bar is a floor; the issue's own Fix section says
   "all three" (spec Requirement 2).
3. **The idle-branch capture is `if ! VAR=$(fn); then`, never a bare `VAR=$(fn)` followed by
   `[ $? -ne 0 ]`.** Under `scheduler.sh`'s `set -euo pipefail` + `set -E`, the bare form exits
   the whole poll loop on a transient `gh` failure — a regression into this exact ticket's bug
   class, caught at the operator spec gate and pinned by Task 7's Case 5 (executed, not grepped).
4. **`off_board_ready_issues` lives in `scheduler.sh`, not `scripts/scheduler_lib.sh`.**
   `scheduler_lib.sh`'s own header states its contract: "pure, side-effect-free item-blob
   predicates only... Do NOT add ... gh-mutating logic here." The new function shells out to
   `gh issue list` and operates on the whole board snapshot, not a single item blob (spec
   Requirement 9).
5. **`--limit 200` plus a full-page guard on the `gh issue list` lookup**, not an unbounded or
   default-30 call. `gh issue list` truncates silently past its `--limit`; a detector for a
   silently-truncated queue that is itself silently truncated would reproduce this ticket's bug
   class one level down (spec Requirement 7, operator spec-gate finding).
6. **Off-board check runs only on the idle branch**, not every poll cycle. A cycle that
   dispatched work has already proven the board reachable; this also matches the issue's own
   framing of "one extra `gh issue list` per idle poll" (spec Requirement 5).
7. **A `board-add` failure at any of the three creation sites is non-fatal** (stderr warning,
   phase continues) — matches each command file's existing graceful-degradation posture (e.g.
   `ceiling-revisit.md`'s `XL_ACTION="skip-lookup-failed"` path) and avoids aborting a phase over
   a board-hygiene failure (spec Requirement 4).

## Out of Scope (per spec)

- A periodic "spillover tickets with no board item" audit — scope-spillover tickets are never
  `ready-for-agent`-labeled, so Part 2's detector structurally cannot backstop a `board-add`
  failure at the conformance site. Flagged as a possible future ticket, not fixed here (spec
  Open Questions).
- Any change to `gate_*`, the breaker, budgets, or `deploy/**` (Acceptance).
- `.archon/commands/ceiling-revisit.md` — an untracked runtime copy `entrypoint.sh` regenerates
  from the baked image and git-excludes; editing it would have no lasting effect.
- Independently verifying `gh project item-add --format json`'s exact output shape against a
  live `gh` invocation — the spec flags this as unverified (no network access during
  refinement); Task 1's tests stub `subprocess.run` rather than calling `gh` for real, matching
  every other test in `tests/test_factory_core_board.py`.
