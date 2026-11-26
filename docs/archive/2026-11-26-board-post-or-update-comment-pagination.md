# Fix `post_or_update_comment`: paginated lookup, fail-closed retry, `--repo` on create — Implementation Plan

**Issue:** #621

**Goal:** `scripts/factory_core/board.py::post_or_update_comment` finds its marker
comment on every page of an issue's comments, never creates a duplicate when the
lookup fails (one retry, then skip and log), and passes `--repo` on the create call.

**Architecture:** Fix in place inside the one function. The lookup becomes
`gh api repos/{OWNER}/{REPO}/issues/{n}/comments?per_page=100 --paginate` with no
`--jq`; Python parses the merged JSON array and takes the newest match. A non-zero
exit, stdout that is not JSON, or JSON that is not a list counts as a failed lookup.
After one failed lookup the function retries once. If the retry also fails it logs to
stderr and returns without posting. The create call gains `"--repo", f"{OWNER}/{REPO}"`.
Signature (`-> None`) and all call sites are unchanged. No new helper or module.

**Tech Stack:** Python 3 (stdlib `json`, `subprocess`, `sys`, `tempfile`, already
imported in `board.py`), pytest with `monkeypatch` on `subprocess.run`.

**Spec:** `docs/superpowers/specs/2026-11-26-board-post-or-update-comment-pagination-design.md`
(approved at `7b09923`, including the operator amendments: retry once, `per_page=100`,
null-body/non-list tolerance, page-2 test asserts `--paginate` and no `--jq`, docs update).

---

## File Structure

| File | Change | Why |
|---|---|---|
| `scripts/factory_core/board.py` | Modify `post_or_update_comment` (`:119-140` today) | The fix (spec Req 1-5) |
| `tests/test_factory_core_board.py` | Rewrite the 2 existing `post_or_update_comment` tests, add 5 (8 cases) | Spec Req 6 |
| `tests/test_provider_tracker_parity.py` | Update 2 `upsert_comment` tests | They mock the lookup with empty stdout / assert the old lookup path. They fail once the fix lands (verified in a scratch worktree) |
| `tests/test_tracker_contract.py` | Add a `--paginate` lookup branch to `_GitHubFixtureController._run` | The fixture only answers the old `--jq` lookup. The `[github]` upsert contract test fails without it (verified) |
| `tests/test_factory_core_rescue.py` | Lookup branch of `_fake_gh` answers `--paginate` with `[]`; tighten one assert | With only the fix, the rescue comment is silently skipped and the test still passes, because its `"comment" in joined` assert matches the lookup URL |
| `docs/scheduler.md` | Rewrite known issue 21 (`:978-987` today) | Spec Req 7 |

The three extra test files are tests of `board.post_or_update_comment` reached
through `GitHubTracker.upsert_comment` and `rescue.rescue_blocked`. They are in scope
as "board.py and its tests" (issue Scope line). Without these edits the change breaks
them: 4 failures in a scratch run of the whole suite with only the board.py fix
applied, against 3872 passed / 0 failed at baseline. Each edit only changes how the
mock answers the new lookup argv. No production file other than `board.py` changes.

**Not touched (deliberately):** `providers/tracker/github.py::find_comment` uses the
same unpaginated `--jq` lookup. It is a different function in a different file, so it
is out of scope here. Name it in the PR description as a spillover candidate. Do not
edit it. `docs/scheduler.md`'s tracking-issue table and the "none is fixed here"
intro sentence stay as they are: resolved item 13 is already handled that way.

---

## Task 1: Fix `post_or_update_comment` (TDD in `tests/test_factory_core_board.py`)

**Files:**
- Modify: `tests/test_factory_core_board.py` (the block from `def test_post_or_update_comment_new_comment` through the end of `test_post_or_update_comment_updates_existing`, `:160-180` today)
- Modify: `scripts/factory_core/board.py` (`post_or_update_comment`, `:119` to end of file today)

- [ ] **Step 1: Write the failing tests.** In `tests/test_factory_core_board.py`, replace
  the two existing functions `test_post_or_update_comment_new_comment` and
  `test_post_or_update_comment_updates_existing` (whole bodies) with this block. Leave
  every other test in the file unchanged:

```python
MARKER = "<!-- marker -->"


def _lookup_argv(issue_num=42):
    return ["gh", "api",
            f"repos/{board.OWNER}/{board.REPO}/issues/{issue_num}/comments?per_page=100",
            "--paginate"]


def _comment_recorder(lookup_results):
    """subprocess.run fake: lookup calls (argv contains --paginate) pop the next
    CompletedProcess off lookup_results; every other call returns _ok(). Records argv,
    plus the --body-file contents of any create call (the temp file is unlinked after)."""
    calls, bodies = [], []
    results = list(lookup_results)

    def fake(cmd, **kw):
        calls.append(cmd)
        if "--paginate" in cmd:
            return results.pop(0)
        if cmd[:3] == ["gh", "issue", "comment"]:
            bodies.append(Path(cmd[cmd.index("--body-file") + 1]).read_text())
        return _ok()
    return fake, calls, bodies


def _json_cp(payload, returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=json.dumps(payload), stderr="")


def test_post_or_update_comment_new_comment(monkeypatch):
    fake, calls, bodies = _comment_recorder([_json_cp([{"id": 1, "body": "unrelated"}])])
    monkeypatch.setattr(subprocess, "run", fake)
    board.post_or_update_comment(42, MARKER, "body text")
    assert calls[0] == _lookup_argv()
    assert len(calls) == 2
    create = calls[1]
    assert create[:-1] == ["gh", "issue", "comment", "42",
                           "--repo", f"{board.OWNER}/{board.REPO}", "--body-file"]
    assert create[-1].endswith(".md")
    assert bodies == ["body text"]


def test_post_or_update_comment_updates_existing(monkeypatch):
    fake, calls, _ = _comment_recorder([_json_cp([{"id": 12345, "body": f"{MARKER}\nold"}])])
    monkeypatch.setattr(subprocess, "run", fake)
    board.post_or_update_comment(42, MARKER, "updated body")
    assert len(calls) == 2
    patch = calls[1]
    assert patch[:5] == ["gh", "api", f"repos/{board.OWNER}/{board.REPO}/issues/comments/12345",
                         "--method", "PATCH"]
    assert not any(c[:3] == ["gh", "issue", "comment"] for c in calls)


def test_post_or_update_comment_marker_on_second_page_is_patched_not_duplicated(monkeypatch):
    # gh --paginate merges every page into one array; the newest marker comment sits past
    # index 29 (beyond GitHub's default first page), an older one is on "page 1".
    comments = [{"id": i, "body": f"unrelated {i}"} for i in range(1, 41)]
    comments[3] = {"id": 9003, "body": f"{MARKER}\nolder"}
    comments[31] = {"id": 9031, "body": f"{MARKER}\nnewest"}
    fake, calls, _ = _comment_recorder([_json_cp(comments)])
    monkeypatch.setattr(subprocess, "run", fake)
    board.post_or_update_comment(42, MARKER, "updated body")
    lookup = calls[0]
    assert lookup == _lookup_argv()
    assert "--paginate" in lookup and "--jq" not in lookup
    assert len(calls) == 2
    assert calls[1][2] == f"repos/{board.OWNER}/{board.REPO}/issues/comments/9031"
    assert "PATCH" in calls[1]
    assert not any(c[:3] == ["gh", "issue", "comment"] for c in calls)


def test_post_or_update_comment_tolerates_null_body_and_non_dict_entries(monkeypatch):
    comments = [{"id": 1, "body": None}, "garbage", {"id": 2, "body": f"{MARKER}\nx"}]
    fake, calls, _ = _comment_recorder([_json_cp(comments)])
    monkeypatch.setattr(subprocess, "run", fake)
    board.post_or_update_comment(42, MARKER, "updated body")
    assert calls[1][2] == f"repos/{board.OWNER}/{board.REPO}/issues/comments/2"


def test_post_or_update_comment_lookup_fails_twice_posts_nothing(monkeypatch, capsys):
    fail = subprocess.CompletedProcess([], 1, stdout="", stderr="HTTP 502")
    fake, calls, _ = _comment_recorder([fail, fail])
    monkeypatch.setattr(subprocess, "run", fake)
    board.post_or_update_comment(42, MARKER, "body text")
    assert calls == [_lookup_argv(), _lookup_argv()]
    err = capsys.readouterr().err
    assert "#42" in err and "skipping" in err


def test_post_or_update_comment_lookup_retry_then_success_posts(monkeypatch):
    fail = subprocess.CompletedProcess([], 1, stdout="", stderr="HTTP 502")
    fake, calls, bodies = _comment_recorder([fail, _json_cp([])])
    monkeypatch.setattr(subprocess, "run", fake)
    board.post_or_update_comment(42, MARKER, "body text")
    assert calls[:2] == [_lookup_argv(), _lookup_argv()]
    assert len(calls) == 3
    assert calls[2][:6] == ["gh", "issue", "comment", "42", "--repo", f"{board.OWNER}/{board.REPO}"]
    assert bodies == ["body text"]


@pytest.mark.parametrize("stdout", ["not json", "", '{"message": "Not Found"}', "null"])
def test_post_or_update_comment_unparseable_lookup_posts_nothing(monkeypatch, capsys, stdout):
    bad = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
    fake, calls, _ = _comment_recorder([bad, bad])
    monkeypatch.setattr(subprocess, "run", fake)
    board.post_or_update_comment(42, MARKER, "body text")
    assert calls == [_lookup_argv(), _lookup_argv()]
    assert "skipping" in capsys.readouterr().err
```

  (`json`, `subprocess`, `Path` and `pytest` are already imported at the top of this
  file. `_ok` is the existing helper at `:16`.)

- [ ] **Step 2: Run and confirm the new tests fail.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_factory_core_board.py -v -k post_or_update_comment
```

  Expected: `10 failed`, made up of `new_comment`, `updates_existing`, `second_page`,
  `null_body`, `fails_twice`, `retry_then_success` and 4 × `unparseable`. All fail with an
  `AssertionError`. The current code issues the `--jq` lookup, which has no `--paginate`,
  so the fake answers it with `_ok()` and every `calls[0] == _lookup_argv()` /
  `calls == [...]` assertion misses.

- [ ] **Step 3: Implement.** In `scripts/factory_core/board.py`, replace the whole
  `post_or_update_comment` function (from `def post_or_update_comment` through the final
  `os.unlink(tmp)`, which is the end of the file) with:

```python
def post_or_update_comment(issue_num: int, marker: str, body: str) -> None:
    # --paginate with no --jq: gh merges the array pages into one list, and the newest
    # match is taken over that whole list (a --jq filter would run once per page).
    # A failed lookup is retried once, then the post is skipped -- never "create",
    # which is the duplicate this guards against (#621).
    comments = None
    for _attempt in (1, 2):  # one retry: most callers are one-shot and cannot retry themselves
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
```

  No new imports: `json`, `os`, `subprocess`, `sys`, `tempfile` are already imported
  at `board.py:1-5`. If the `gh` in the image turns out not to merge array pages for
  `--paginate` (the spec's flagged assumption, verified on `gh` 2.92 per
  `scripts/fetch_scorecard.py::_gh`), say so in the PR. Do not fall back to `--jq`.

- [ ] **Step 4: Run and confirm the board tests pass.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_factory_core_board.py -v
```

  Expected: every test in the file PASSES, including the 10 `post_or_update_comment`
  cases. `0 failed`.

- [ ] **Step 5: Do not commit yet.** With only this task applied, 3 tests in other files
  fail (Task 2 Step 1 lists them). To keep every commit green, Task 1 and Task 2 land as
  one commit in Task 2 Step 6.

---

## Task 2: Update the tests that reach `post_or_update_comment` through other modules

**Files:**
- Modify: `tests/test_provider_tracker_parity.py` (`test_upsert_comment_delegates_to_board_post_or_update_comment` and `test_upsert_comment_opaque_id_passthrough`, `:161-175` today)
- Modify: `tests/test_tracker_contract.py` (`_GitHubFixtureController._run`, the `--jq` comments-lookup branch at `:81` today)
- Modify: `tests/test_factory_core_rescue.py` (`_fake_gh` lookup branch `:66-68` today, and `test_rescue_green_mergeable_promotes`)

- [ ] **Step 1: Confirm they now fail.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_tracker_parity.py tests/test_tracker_contract.py tests/test_factory_core_rescue.py -q
```

  Expected: `3 failed`:
  `test_upsert_comment_delegates_to_board_post_or_update_comment`,
  `test_upsert_comment_opaque_id_passthrough`,
  `test_marker_comment_upsert_updates_in_place_not_duplicate[github]`.
  The rescue tests still pass here, which is the problem Step 4 fixes: the comment is
  skipped, but the old assert matches the lookup URL.

- [ ] **Step 2: Parity tests.** In `tests/test_provider_tracker_parity.py`, replace the two
  functions with:

```python
def test_upsert_comment_delegates_to_board_post_or_update_comment(monkeypatch):
    calls = []
    def fake(cmd, **kw):
        calls.append(cmd)
        return _ok(stdout="[]") if "--paginate" in cmd else _ok()
    monkeypatch.setattr(subprocess, "run", fake)
    GitHubTracker().upsert_comment("42", "<!-- marker -->", "body text")
    assert any(c[:3] == ["gh", "issue", "comment"] for c in calls)


def test_upsert_comment_opaque_id_passthrough(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubTracker().upsert_comment("PROJ-123", "<!-- marker -->", "body")
    lookup = calls[0]
    assert f"repos/{identity.OWNER}/{identity.REPO}/issues/PROJ-123/comments?per_page=100" in lookup
```

- [ ] **Step 3: Contract fixture.** In `tests/test_tracker_contract.py`, inside
  `_GitHubFixtureController._run`, insert this branch **immediately before** the existing
  `if cmd[:2] == ["gh", "api"] and "--jq" in cmd and str(cmd[2]).endswith("/comments"):`
  branch. Keep that branch: `GitHubTracker.find_comment` still emits the `--jq` form.

```python
        if cmd[:2] == ["gh", "api"] and "--paginate" in cmd and "/comments?" in str(cmd[2]):
            # board.post_or_update_comment: the paginated lookup returns the whole merged
            # array; the marker match and newest-pick happen in Python (#621)
            key = cmd[2].split("/issues/")[1].split("/")[0]
            return self._ok(stdout=json.dumps(self.comments.get(key, [])))
```

  (`json` is already imported at the top of this file. The existing `gh issue comment`
  branch reads `cmd[3]` as the issue key, which still holds because `--repo` follows the
  issue number in the new argv.)

- [ ] **Step 4: Rescue fake.** In `tests/test_factory_core_rescue.py`, inside `_fake_gh`'s
  `run`, replace

```python
        if "--jq" in cmd:           # comment-id lookup -> none exists
            return _cp("")
```

  with

```python
        if "--paginate" in cmd:     # comment lookup -> none exists
            return _cp("[]")
```

  Dropping the `--jq` branch changes nothing else: `_cp("")` and the fall-through
  `_cp()` return the same `CompletedProcess`, so any other `--jq` call routed through
  `_fake_gh` gets the same answer as before.

  Then, in `test_rescue_green_mergeable_promotes`, replace

```python
    assert any("comment" in " ".join(c) for c in calls)
```

  with

```python
    assert any(c[:3] == ["gh", "issue", "comment"] for c in calls)
```

- [ ] **Step 5: Run and confirm they pass.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_tracker_parity.py tests/test_tracker_contract.py tests/test_factory_core_rescue.py -q
```

  Expected: `0 failed`.

- [ ] **Step 6: Commit Tasks 1 and 2 together.**

```bash
git add scripts/factory_core/board.py tests/test_factory_core_board.py \
        tests/test_provider_tracker_parity.py tests/test_tracker_contract.py tests/test_factory_core_rescue.py
git commit -m "fix(#621): post_or_update_comment paginates its lookup, retries once then skips, passes --repo on create"
```

---

## Task 3: Update `docs/scheduler.md` known issue 21

**Files:**
- Modify: `docs/scheduler.md` (item 21 under "## Open questions / oddities", `:978-987` today)

- [ ] **Step 1: Get the current line of the create call.**

```bash
grep -n '"--repo", f"{OWNER}/{REPO}"' scripts/factory_core/board.py
grep -n 'board.post_or_update_comment(id, marker, body)' scripts/factory_core/providers/tracker/github.py
```

  Expected: `159:` for board.py (`158:` is the `["gh", "issue", "comment", ...` line),
  and `218:` for github.py. Use the numbers grep prints in place of `158-159` and `218`
  below if they differ.

- [ ] **Step 2: Replace item 21.** Replace the whole item, from the line starting
  `21. **\`post_or_update_comment\` creates a comment without \`--repo\`.**` through the
  line ending `was not determined.`, with:

```markdown
21. **Resolved by #621: `post_or_update_comment` creates its comment with `--repo`.**
    `scripts/factory_core/board.py` (`:158-159`) now calls
    `gh issue comment <n> --repo <owner>/<repo> --body-file …`. From the scheduler's
    `/workspace`, which is not a git checkout, `gh` cannot infer the repo, and a bare
    `gh issue …` call fails with "not a git repository" (`scheduler.sh:685-687`). Its
    lookup and PATCH calls already named the repo. This is the path used by the
    blocked-rescue comment when no earlier rescue comment exists, and by
    `tracker comment`, which `GitHubTracker.upsert_comment` routes to it
    (`providers/tracker/github.py:218`). The same change makes the marker lookup read
    every comment page (`gh api …/comments?per_page=100 --paginate`, matched in
    Python). It also stops a failed lookup from creating a duplicate: one failed lookup
    is retried, and a second failure skips the post with a stderr warning.
```

- [ ] **Step 3: Run the docs reference check.**

```bash
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```

  Expected: `Docs reference check passed for 4 doc(s).`

- [ ] **Step 4: Commit.**

```bash
git add docs/scheduler.md
git commit -m "docs(#621): scheduler known issue 21 resolved"
```

---

## Task 4: Full verification and PR notes

**Files:** none modified.

- [ ] **Step 1: Full Python suite.**

```bash
PYTHONPATH=scripts python -m pytest tests/ -q
```

  Expected: `0 failed`. Baseline on this branch's parent was 3872 passed. Expect 3880:
  8 new board test cases, from 5 new functions, one of them parametrized ×4.

- [ ] **Step 2: Shell tests that run entrypoint/comment paths.**

```bash
for t in test_entrypoint_session_window test_entrypoint_blast_hold test_entrypoint_error_signature \
         test_entrypoint_cost_report_regression test_entrypoint_cost_report_comment \
         test_verdict_gate_check test_side_effect_shims test_scheduler; do
  bash tests/$t.sh >/dev/null 2>&1 && echo "PASS $t" || echo "FAIL $t"
done
```

  Expected: all `PASS`. These were checked in a scratch worktree with the Task 1 change
  and are unaffected. They need a checkout directory named `dark-factory`, as the
  factory clone is. A worktree with another name fails them on unchanged code too.

- [ ] **Step 3: Scope check.**

```bash
git diff --stat origin/main HEAD -- . ':!docs/superpowers' ':!.archon/memory'
```

  Expected: exactly `scripts/factory_core/board.py`, `docs/scheduler.md`,
  `tests/test_factory_core_board.py`, `tests/test_provider_tracker_parity.py`,
  `tests/test_tracker_contract.py`, `tests/test_factory_core_rescue.py`. No
  `scheduler.sh`, gate, breaker, budget, `deploy/**` or `providers/tracker/github.py`
  change.

- [ ] **Step 4: PR description must state** (the issue asks for the choice to be named
  in the PR):
  - Lookup-failure handling: **retry the lookup once, then skip the post and log to
    stderr**. The reason: of the callers, only `rescue.py` and the #585 local-routing
    refusal re-invoke on a cadence. About 13 call sites are one-shot notices that mostly
    discard stderr. A failed lookup never falls through to create.
  - Failed lookup = non-zero `gh` exit, stdout that is not JSON, or JSON that is not a
    list. A comment with a `null` body is treated as non-matching.
  - `--repo` added on the create path (defect reported in the 2026-11-26 operator
    comment; verified against `main`).
  - Spillover candidate, not fixed here: `providers/tracker/github.py::find_comment` uses
    the same unpaginated `--jq` lookup. Name it so the conformance gate's spillover
    filing, or a maintainer, can open a ticket for it. Do not fix it in this PR.
