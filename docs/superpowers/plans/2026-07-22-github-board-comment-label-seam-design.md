# Implementation Plan: GitHub board/comment/label ops — close the remaining raw-`gh` call sites

**Issue:** omniscient/dark-factory#181
**Spec:** `docs/superpowers/specs/2026-07-22-github-board-comment-label-seam-design.md`
**Related:** #249 (CodeHost provider gaps closed here), #212 (DAG-bash-node comment posting), #275/#281
(unrelated read-path fix cited as incident precedent)

---

## Goal

Close every remaining raw-`gh`/hand-inlined-literal call site the spec identified across five
surfaces, so `factory_core` (and its `providers/` seam) is the single implementation of each
board/comment/label operation:

1. **R1** — 3 `commands/*.md` files still inline raw `gh project item-list`/`item-edit` blocks.
2. **R2** — `rescue.py:pr_for_issue()` still raw-shells `gh pr list` (provider gap: no method
   returns `{number, isDraft, mergeable}`).
3. **R3** — `GitHubCodeHost.get_change_checks()` discards data on `gh`'s nonzero exit — the exact
   case `scheduler.sh:failing_checks_for_pr`/`rescue.py:pr_check_buckets` exist to read, which is
   why both still bypass it.
4. **R4/R5** — 28 hand-inlined footer-literal strings and one hand-listed detection regex
   (`scheduler.sh:bot_re`) can drift silently from `identity.py`.
5. **R6** — 7 raw `gh issue edit --add-label needs-discussion` sites.

Every acceptance criterion is a **global-absence invariant** ("zero raw X outside Y") — this
lands as one PR, internally ordered by commit: provider-layer capability + tests first (R2, R3),
then the mechanical adapter conversions that depend on it (R1, R4, R5, R6), per the spec's
Architecture section and the operator's approval comment.

## Architecture

No new modules. Three existing surfaces gain capability:

- `providers/codehost/base.py` (ABC) gains a sibling method `find_change_details` (not a widened
  `find_change_for` — one-shape-per-method convention, `.archon/memory/architecture.md`).
- `providers/codehost/github.py`'s `get_change_checks` drops its data-discarding early return.
- `factory_core/cli.py` (the **non-`providers`** dispatch CLI, distinct from
  `providers/cli.py`) gains two new verbs, `marker <kind>` and `markers-regex`, backed by a new
  `identity.detection_patterns()` function.

Every bash/markdown call site that hand-inlines a literal footer, board-move block, or
`--add-label` call becomes a one-line delegation to one of these two CLIs — the same shape
`entrypoint.sh:set_board_status()`/`scheduler.sh:set_board_status()` already use.

## Tech Stack

Python 3 (stdlib only: `subprocess`, `json`, `re`, `argparse`), `pytest` for all Python tests,
bash (existing `tests/test_*.sh` source-and-stub harness, e.g. `test_has_new_comment_after_report.sh`)
for scheduler.sh behavioral regression tests, `jq` for the JSON glue already used throughout
`scheduler.sh`.

---

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/providers/codehost/base.py` | Add abstract `find_change_details` |
| `scripts/factory_core/providers/codehost/github.py` | Implement `find_change_details`; fix `get_change_checks` exit-code bug |
| `scripts/factory_core/providers/codehost/gitlab.py` | Stub `find_change_details` (`NotImplementedError`) |
| `scripts/factory_core/providers/cli.py` | Add `codehost find-change-details` verb |
| `scripts/factory_core/rescue.py` | Migrate `pr_for_issue`/`pr_check_buckets` to the provider seam |
| `scripts/factory_core/identity.py` | Add `detection_patterns()` |
| `scripts/factory_core/cli.py` | Add `marker <kind>` and `markers-regex` verbs |
| `scheduler.sh` | Migrate `failing_checks_for_pr`, `BOT_RE`/`has_new_comment_after_report`, 7 footer-literal posts |
| `entrypoint.sh` | Migrate 4 of 5 footer-literal posts (1 documented exclusion — see Design Decisions) |
| `workflows/archon-dark-factory.yaml` | Migrate 3 footer-literal sites |
| `commands/dark-factory-code-review.md` | Board-move block → `tracker set-status`; footer; label |
| `commands/dark-factory-validate.md` | Board-move block → `tracker set-status`; footer; label |
| `commands/dark-factory-conformance.md` | Board-move block → `tracker set-status`; footer; label |
| `commands/dark-factory-refine.md` | 2 label sites; 1 footer site |
| `commands/dark-factory-plan.md` | 2 label sites; 2 footer sites |
| `commands/dark-factory-revise-advisory.md` | 1 footer site |
| `tests/test_provider_codehost_base.py` | Add `find_change_details` to the required-abstract-methods set |
| `tests/test_provider_codehost_contract.py` | Add `find_change_details` to `HTTP_BACKED_ARGS` |
| `tests/test_provider_codehost_parity.py` | New `find_change_details` parity tests; new `get_change_checks` nonzero-exit regression tests |
| `tests/test_provider_cli.py` | New `codehost find-change-details` CLI test |
| `tests/test_factory_core_rescue.py` | New routing-through-codehost tests for both migrated functions |
| `tests/test_factory_core_identity.py` | New `detection_patterns()` tests |
| `tests/test_factory_core_cli.py` | **New file** — `marker`/`markers-regex` verb tests |
| `tests/test_has_new_comment_after_report.sh` | Update to compute `BOT_RE` via the stubbed `markers-regex` verb |
| `tests/test_failing_checks_for_pr.sh` | **New file** — end-to-end regression for the R3 fix through `scheduler.sh` |
| `tests/test_command_footer_migration.py` | **New file** — static done-criterion assertions for R4/R6 across `commands/*.md` + `workflows/archon-dark-factory.yaml` |

---

## Design Decisions (corrections to the spec's static examples — re-derived from current `main`)

Per the spec's own R5 note and the operator's approval-comment condition #3 ("re-grep at
implementation time ... rather than trusting this spec's static counts"), the following three
details were re-derived against the current working tree and diverge from the spec's literal
code samples. Each is a **narrowing of scope to what's actually safe/applicable**, not a
reduction in what R1–R6 accomplish:

1. **`commands/*.md` python3 invocations use the `# TARGET-PATH` convention, not `/opt/...`.**
   The spec's R1 code sample shows `python3 /opt/dark-factory/scripts/factory_core/providers/cli.py
   ...`, copying `entrypoint.sh`'s **pre-clone** pattern. But every existing `python3
   dark-factory/scripts/...` call already inside `dark-factory-code-review.md`,
   `dark-factory-validate.md`, and `dark-factory-conformance.md` (e.g. `diff_rank.py`,
   `gate_blast_radius.py`) uses the **post-clone**, repo-relative `dark-factory/scripts/...
   # TARGET-PATH` convention instead (these commands run after clone, inside the checkout). All
   new `python3 dark-factory/scripts/factory_core/...` call sites added to `commands/*.md` in this
   plan use that convention to match their file's existing siblings, not the `/opt` path.

2. **`entrypoint.sh`'s footer-literal migrations use the hardcoded `/opt/...` path, matching
   `set_board_status`/`post_or_update_comment`'s own established reasoning** (their comments:
   "on_failure ... is reachable via the ERR trap before git clone ever completes" — they always
   use the baked `/opt` copy, never `$CLONE_DIR`, so they work regardless of clone state). The 4
   footer sites migrated here (`run_post_mortem`, 2 `post_or_update_comment` failure bodies, 1
   `gh issue comment` success body) sit in the same failure/success paths, so they follow the same
   rule for the same reason.

3. **`entrypoint.sh:577`'s `"Updated by ${FACTORY_PRODUCT_NAME} Dark Factory*"` cost-report footer
   is intentionally NOT migrated.** `identity._MARKERS` has exactly 5 kinds
   (`factory/scheduler/refinement/autopilot/main_red`), all "**Posted by** ... " templates — there
   is no "Updated by" kind, and the spec's R4 explicitly scopes the new `marker <kind>` verb to
   exactly those 5 existing kinds (no new kind is added). `identity.detection_patterns()` (R5)
   already treats this exact string as its own hardcoded literal, independent of `_MARKERS` —
   confirming the codebase's existing design treats "Updated by ... Dark Factory" as a one-off,
   not part of the 5-kind footer system. Inventing a 6th `_MARKERS` kind to cover it would exceed
   R4's defined scope and duplicate what R5 already does correctly for detection purposes. This
   line remains a hand-inlined literal; combined with the 5 `scheduler.sh` search-marker sites
   excluded in Design Decision 4 below, exactly **22 of the 28** footer literals the spec counted
   are migrated by this plan — the other 6 (this 1 + those 5) are documented, intentional
   exclusions, not missed sites. Task 14's final-verification grep expects exactly these 6 to
   remain.

4. **Five `scheduler.sh` sites that use a marker string as a `jq test()` search argument are
   excluded from the R4 migration** — lines 316, 334, 351, 374, 715 (passed into
   `has_new_comment_after_report`/`elapsed_minutes_since_marker`/`get_new_comments` as
   `report_marker`/`marker_re`). These pass the **bare** text (`"Posted by ${FACTORY_PRODUCT_NAME}
   Refinement Pipeline"`, no asterisks) as a regex substring to match against an
   asterisk-wrapped footer already present in a fetched comment body. `identity.marker(kind)`
   returns the **asterisk-wrapped** display form (`"*Posted by ... Refinement Pipeline*"`); a
   leading `*` is not a valid regex atom, so substituting the fetched marker verbatim into these
   5 call sites would break `jq`'s `test()` (or silently change matching semantics) — a real
   regression, not a style nit. No verb in R4/R5's design produces the bare, un-asterisked form.
   Task 10 below migrates only the 7 true footer-**posting** sites in `scheduler.sh` (which do
   print the asterisk-wrapped form verbatim) plus the `bot_re` detection regex (R5, a genuinely
   different, already-correctly-escaped verb). These 5 sites keep their current hand-inlined bare
   literal — flagged here so conformance review sees this as a deliberate, reasoned exclusion, not
   a missed site.

---

## Task 1 — R2: Add `find_change_details` to the `CodeHost` ABC (base + GitHub impl + GitLab stub)

**Files:** `scripts/factory_core/providers/codehost/base.py`,
`scripts/factory_core/providers/codehost/github.py`, `scripts/factory_core/providers/codehost/gitlab.py`,
`tests/test_provider_codehost_base.py`, `tests/test_provider_codehost_parity.py`,
`tests/test_provider_codehost_contract.py`

**Single commit, all three implementations together.** Adding an `@abstractmethod` to `base.py`
makes every concrete subclass un-instantiable (`ABCMeta` recomputes `__abstractmethods__`) until
it implements the new method — landing the ABC change alone would leave `GitHubCodeHost()` and
`GitLabCodeHost()` both raising `TypeError`, breaking `test_provider_codehost_parity.py`,
`test_provider_codehost_contract.py`, `test_provider_cli.py`, and `test_factory_core_rescue.py`
(all of which instantiate one or both classes) for however long the ABC and the implementations
are split across commits. So the ABC method, the GitHub implementation, and the GitLab stub land
together, in this order of edits, verified once at the end:

1. Edit `tests/test_provider_codehost_base.py` — add `"find_change_details"` to the `required` set
   (line 12-16) and a `find_change_details` implementation to `_Bare` (line 25-36):
   ```python
   def test_codehost_is_abstract_with_required_ops():
       from factory_core.providers.codehost.base import CodeHost

       required = {
           "remote_url", "find_change_for", "find_change_details", "open_change",
           "update_change_body", "mark_ready", "merge_change", "get_change_checks",
           "get_change_mergeable", "get_change_reviews", "get_change_inline_comments",
           "close_keyword",
       }
       assert required.issubset(CodeHost.__abstractmethods__)
       with pytest.raises(TypeError):
           CodeHost()


   def test_codehost_degradable_ops_have_safe_defaults():
       from factory_core.providers.codehost.base import CodeHost

       class _Bare(CodeHost):
           def remote_url(self): return ""
           def find_change_for(self, branch): return None
           def find_change_details(self, branch): return None
           def open_change(self, source, target, title, body, draft=False): return "1"
           def update_change_body(self, id, body): return True
           def mark_ready(self, id): pass
           def merge_change(self, id, strategy="merge", delete_branch=True): return True
           def get_change_checks(self, id): return []
           def get_change_mergeable(self, id): return "UNKNOWN"
           def get_change_reviews(self, id): return ""
           def get_change_inline_comments(self, id): return []
           def close_keyword(self, issue_id): return ""

       assert _Bare.required_env() == []
   ```
2. Add parity tests to `tests/test_provider_codehost_parity.py`, immediately after
   `test_find_change_for_exact_head_matches_push_resolve` (after line 57):
   ```python
   def test_find_change_details_matches_rescue_pr_for_issue(monkeypatch):
       calls = []
       monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (
           calls.append(cmd),
           _ok(stdout='[{"number": 7, "isDraft": false, "mergeable": "MERGEABLE"}]'),
       )[1])
       details = GitHubCodeHost().find_change_details("feat/issue-7-", repo=identity.SLUG)
       assert calls[0] == [
           "gh", "pr", "list", "--repo", identity.SLUG,
           "--search", "head:feat/issue-7-",
           "--json", "number,isDraft,mergeable",
       ]
       assert details == {"number": 7, "isDraft": False, "mergeable": "MERGEABLE"}


   def test_find_change_details_exact_head(monkeypatch):
       calls = []
       monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="[]"))[1])
       details = GitHubCodeHost().find_change_details("feat/issue-7-slug", exact=True)
       assert calls[0] == [
           "gh", "pr", "list", "--head", "feat/issue-7-slug",
           "--json", "number,isDraft,mergeable",
       ]
       assert details is None


   def test_find_change_details_returns_none_on_failure(monkeypatch):
       monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout="", returncode=1))
       assert GitHubCodeHost().find_change_details("feat/issue-7-") is None


   def test_find_change_details_returns_none_on_invalid_json(monkeypatch):
       monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout="not json"))
       assert GitHubCodeHost().find_change_details("feat/issue-7-") is None
   ```
3. Add `"find_change_details": ("feat/issue-1-x",)` to `HTTP_BACKED_ARGS` in
   `tests/test_provider_codehost_contract.py` (line 29-39), keeping it alphabetically placed after
   `find_change_for`:
   ```python
   HTTP_BACKED_ARGS = {
       "find_change_for": ("feat/issue-1-x",),
       "find_change_details": ("feat/issue-1-x",),
       "open_change": (None, None, "title", "body"),
       "update_change_body": ("{id}", "body"),
       "mark_ready": ("{id}",),
       "merge_change": ("{id}",),
       "get_change_checks": ("{id}",),
       "get_change_mergeable": ("{id}",),
       "get_change_reviews": ("{id}",),
       "get_change_inline_comments": ("{id}",),
   }
   ```
4. Run `python -m pytest tests/test_provider_codehost_base.py tests/test_provider_codehost_parity.py tests/test_provider_codehost_contract.py -v -k find_change_details`
   — fails throughout: `test_provider_codehost_base.py`'s new assertion fails because
   `"find_change_details"` isn't in `CodeHost.__abstractmethods__` yet; the parity tests fail with
   `AttributeError: 'GitHubCodeHost' object has no attribute 'find_change_details'`; the contract
   test fails the same way for both `github` and `gitlab` parametrizations.
5. Implement in `scripts/factory_core/providers/codehost/base.py`, immediately after
   `find_change_for` (line 16-17):
   ```python
       @abstractmethod
       def find_change_details(self, branch: str, exact: bool = False,
                                repo: str | None = None) -> dict | None:
           """The open PR/MR {number, isDraft, mergeable} for a branch (or prefix), or None."""
   ```
6. Implement in `scripts/factory_core/providers/codehost/github.py`, immediately after
   `find_change_for` (after line 36):
   ```python
       def find_change_details(self, branch: str, exact: bool = False,
                                repo: str | None = None) -> dict | None:
           cmd = ["gh", "pr", "list"]
           if repo:
               cmd += ["--repo", repo]
           if exact:
               cmd += ["--head", branch]
           else:
               cmd += ["--search", f"head:{branch}"]
           cmd += ["--json", "number,isDraft,mergeable"]
           r = subprocess.run(cmd, capture_output=True, text=True)
           if r.returncode != 0:
               return None
           try:
               arr = json.loads(r.stdout)
           except json.JSONDecodeError:
               return None
           return arr[0] if isinstance(arr, list) and arr else None
   ```
7. Implement the stub in `scripts/factory_core/providers/codehost/gitlab.py`, immediately after
   `find_change_for` (after line 61), following that method's exact template (no
   `_validate_change_id` — it takes a branch, not a change id, same as `find_change_for`):
   ```python
       def find_change_details(self, branch: str, exact: bool = False,
                                repo: str | None = None) -> dict | None:
           raise NotImplementedError(
               "live GitLab MR list API — deferred; see "
               "docs/adapter-authoring-guide.md#worked-example-gitlab-codehost-seam-proof"
           )
   ```
8. Run `python -m pytest tests/test_provider_codehost_base.py tests/test_provider_codehost_parity.py tests/test_provider_codehost_contract.py -v`
   — all pass, including every pre-existing test in these three files (both concrete classes are
   fully instantiable again).
9. Commit: `feat(codehost): add find_change_details to the CodeHost ABC, GitHub impl, and GitLab stub (#181 R2)`

## Task 2 — R2: Add `codehost find-change-details` CLI verb

**Files:** `scripts/factory_core/providers/cli.py`, `tests/test_provider_cli.py`

1. Add tests to `tests/test_provider_cli.py`, after `test_codehost_find_change_passes_repo_and_exact`
   (after line 51):
   ```python
   def test_codehost_find_change_details_prints_json(monkeypatch, capsys):
       import factory_core.providers.cli as cli_mod

       class _FakeCodeHost:
           def find_change_details(self, branch, exact=False, repo=None):
               return {"number": 9, "isDraft": False, "mergeable": "MERGEABLE"}
       monkeypatch.setattr(cli_mod, "get_codehost", lambda: _FakeCodeHost())
       monkeypatch.setattr(sys, "argv", [
           "cli.py", "codehost", "find-change-details", "--branch", "feat/issue-9-",
       ])
       cli_mod.main()
       assert json.loads(capsys.readouterr().out) == {
           "number": 9, "isDraft": False, "mergeable": "MERGEABLE",
       }


   def test_codehost_find_change_details_prints_empty_string_on_none(monkeypatch, capsys):
       import factory_core.providers.cli as cli_mod

       class _FakeCodeHost:
           def find_change_details(self, branch, exact=False, repo=None):
               return None
       monkeypatch.setattr(cli_mod, "get_codehost", lambda: _FakeCodeHost())
       monkeypatch.setattr(sys, "argv", [
           "cli.py", "codehost", "find-change-details", "--branch", "feat/issue-9-",
       ])
       cli_mod.main()
       assert capsys.readouterr().out.strip() == ""


   def test_codehost_find_change_details_passes_repo_and_exact(monkeypatch):
       import factory_core.providers.cli as cli_mod

       seen = {}

       class _FakeCodeHost:
           def find_change_details(self, branch, exact=False, repo=None):
               seen.update(branch=branch, exact=exact, repo=repo)
               return None
       monkeypatch.setattr(cli_mod, "get_codehost", lambda: _FakeCodeHost())
       monkeypatch.setattr(sys, "argv", [
           "cli.py", "codehost", "find-change-details", "--branch", "feat/issue-9-slug",
           "--repo", "o/r", "--exact",
       ])
       cli_mod.main()
       assert seen == {"branch": "feat/issue-9-slug", "exact": True, "repo": "o/r"}
   ```
2. Run `python -m pytest tests/test_provider_cli.py -v -k find_change_details` — fails:
   `SystemExit: 2` (argparse: `invalid choice: 'find-change-details'`).
3. Implement in `scripts/factory_core/providers/cli.py`: handler immediately after
   `_codehost_find_change` (after line 84):
   ```python
   def _codehost_find_change_details(args):
       _print(get_codehost().find_change_details(args.branch, exact=args.exact, repo=args.repo) or "")
   ```
   Subparser immediately after `cfc`'s block (after line 200, before `coc = csub.add_parser("open-change")`):
   ```python
       cfcd = csub.add_parser("find-change-details")
       cfcd.add_argument("--branch", required=True)
       cfcd.add_argument("--repo")
       cfcd.add_argument("--exact", action="store_true")
       cfcd.set_defaults(func=_codehost_find_change_details)
   ```
4. Run `python -m pytest tests/test_provider_cli.py -v -k find_change_details` — passes.
5. Commit: `feat(providers-cli): add codehost find-change-details verb (#181 R2)`

## Task 3 — R2: Migrate `rescue.py:pr_for_issue()` to `find_change_details`

**Files:** `scripts/factory_core/rescue.py`, `tests/test_factory_core_rescue.py`

1. Add a routing-lock test to `tests/test_factory_core_rescue.py`, after the imports (after line 9):
   ```python
   def test_pr_for_issue_routes_through_codehost_find_change_details(monkeypatch):
       seen = {}

       class _FakeCodeHost:
           def find_change_details(self, branch, exact=False, repo=None):
               seen.update(branch=branch, repo=repo)
               return {"number": 7, "isDraft": False, "mergeable": "MERGEABLE"}
       monkeypatch.setattr(rescue, "get_codehost", lambda: _FakeCodeHost())
       result = rescue.pr_for_issue(7)
       assert seen == {"branch": "feat/issue-7-", "repo": rescue._repo()}
       assert result == {"number": 7, "isDraft": False, "mergeable": "MERGEABLE"}
   ```
2. Run `python -m pytest tests/test_factory_core_rescue.py -v -k routes_through_codehost_find_change_details`
   — fails: `AssertionError` (`seen` stays `{}` — `pr_for_issue` still calls raw `subprocess.run`,
   never touches the monkeypatched `get_codehost`).
3. Implement in `scripts/factory_core/rescue.py` — replace `pr_for_issue` (lines 35-59):
   ```python
   def pr_for_issue(issue_num: int) -> dict | None:
       """The open PR for an issue's feature branch (feat/issue-<N>-*), or None."""
       return get_codehost().find_change_details(f"feat/issue-{issue_num}-", repo=_repo())
   ```
   (Drops the #249 docstring paragraph — the gap it described is closed by Task 1.)
4. Run `python -m pytest tests/test_factory_core_rescue.py -v` — all pass (the existing
   `_fake_gh`-based tests still pass unchanged: `find_change_details` internally calls
   `subprocess.run(["gh","pr","list",...])`, the exact shape `_fake_gh`'s `"pr" in cmd and "list"
   in cmd` branch already routes).
5. Commit: `refactor(rescue): route pr_for_issue through CodeHost.find_change_details (#181 R2)`

## Task 4 — R3: Fix `get_change_checks`'s exit-code data loss

**Files:** `scripts/factory_core/providers/codehost/github.py`, `tests/test_provider_codehost_parity.py`

This is the **gate-critical** change (operator condition #1): must prove the green-exit path is
byte-for-byte unchanged AND the nonzero-exit path now returns real data.

1. Add regression tests to `tests/test_provider_codehost_parity.py`, after
   `test_get_change_checks_matches_rescue_py` (after line 129):
   ```python
   def test_get_change_checks_green_exit_path_unchanged(monkeypatch):
       """Byte-for-byte: a zero-exit response returns exactly what it always did."""
       calls = []
       monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (
           calls.append(cmd),
           _ok(stdout='[{"name": "ci", "bucket": "pass", "link": "u"}]', returncode=0),
       )[1])
       checks = GitHubCodeHost().get_change_checks("9", repo=identity.SLUG)
       assert calls[0] == ["gh", "pr", "checks", "9", "--repo", identity.SLUG, "--json", "name,bucket,link"]
       assert checks == [{"name": "ci", "bucket": "pass", "link": "u"}]


   def test_get_change_checks_returns_data_on_nonzero_exit_with_valid_json(monkeypatch):
       """The failing/pending path: gh exits nonzero but stdout is valid JSON — must not be discarded."""
       monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(
           stdout='[{"name": "ci", "bucket": "fail", "link": "u"}]', returncode=1,
       ))
       checks = GitHubCodeHost().get_change_checks("9", repo=identity.SLUG)
       assert checks == [{"name": "ci", "bucket": "fail", "link": "u"}]


   def test_get_change_checks_empty_list_on_invalid_json_regardless_of_exit_code(monkeypatch):
       """A genuine error (empty/invalid stdout) still yields [] on both exit codes."""
       for code in (0, 1):
           monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout="not json", returncode=code))
           assert GitHubCodeHost().get_change_checks("9") == []
       for code in (0, 1):
           monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout="", returncode=code))
           assert GitHubCodeHost().get_change_checks("9") == []
   ```
2. Run `python -m pytest tests/test_provider_codehost_parity.py -v -k get_change_checks` —
   `test_get_change_checks_returns_data_on_nonzero_exit_with_valid_json` fails: `assert [] ==
   [{'name': 'ci', ...}]` (current code's `if r.returncode != 0: return []` discards it). The
   other two already pass (proving they're the pre-existing behavior this fix must preserve).
3. Implement in `scripts/factory_core/providers/codehost/github.py` — remove the early return from
   `get_change_checks` (lines 75-88 → drop lines 82-83):
   ```python
       def get_change_checks(self, id: str, fields: str = "name,bucket,link",
                              repo: str | None = None) -> list:
           cmd = ["gh", "pr", "checks", id]
           if repo:
               cmd += ["--repo", repo]
           cmd += ["--json", fields]
           r = subprocess.run(cmd, capture_output=True, text=True)
           try:
               data = json.loads(r.stdout)
           except json.JSONDecodeError:
               return []
           return data if isinstance(data, list) else []
   ```
4. Run `python -m pytest tests/test_provider_codehost_parity.py tests/test_provider_codehost_contract.py -v`
   — all pass, including the two pre-existing tests untouched by this change (green-path parity
   proof).
5. Commit: `fix(codehost): get_change_checks no longer discards data on gh's nonzero exit (#181 R3)`

## Task 5 — R3: Migrate `scheduler.sh:failing_checks_for_pr` to `codehost checks`

**Files:** `scheduler.sh`, `tests/test_failing_checks_for_pr.sh` (new)

1. Create `tests/test_failing_checks_for_pr.sh`, modeled directly on
   `tests/test_has_new_comment_after_report.sh`'s source-and-stub harness:
   ```bash
   #!/usr/bin/env bash
   # Regression test for scheduler.sh:failing_checks_for_pr() after #181 R3: it now routes
   # through `providers/cli.py codehost checks` instead of raw `gh pr checks`. This proves the
   # R3 fix (get_change_checks no longer discards data on gh's nonzero exit) is actually reached
   # end-to-end — a red/pending PR (the exact case this function exists to read) must still
   # surface its failing checks after the migration.
   #
   # Run: bash tests/test_failing_checks_for_pr.sh
   set -uo pipefail

   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
   SCHEDULER="${SCRIPT_DIR}/../scheduler.sh"

   export GH_TOKEN="test-token"
   export CLAUDE_CODE_OAUTH_TOKEN="test-oauth"
   export SCHEDULER_SOURCE_ONLY=1

   # shellcheck source=/dev/null
   source "$SCHEDULER"
   set +e

   MOCK_CHECKS='[]'
   python3() {
     case "$*" in
       *"providers/cli.py"*"codehost checks"*) printf '%s' "$MOCK_CHECKS" ;;
       *) return 0 ;;
     esac
   }

   PASS=0
   FAIL=0
   assert_eq() {
     local name="$1" expected="$2" actual="$3"
     if [ "$expected" = "$actual" ]; then
       echo "  PASS: $name"; PASS=$((PASS + 1))
     else
       echo "  FAIL: $name (expected '$expected', got '$actual')"; FAIL=$((FAIL + 1))
     fi
   }

   # Scenario A — all checks passing (gh would exit 0): no failing checks.
   MOCK_CHECKS='[{"name":"ci","bucket":"pass","link":"u"}]'
   assert_eq "all-pass PR has no failing checks" "0" "$(failing_checks_for_pr 9 | jq 'length')"

   # Scenario B — a check is failing (gh would exit nonzero, but the fixed codehost-checks path
   # still returns real data): the fail bucket must surface, not be silently dropped.
   MOCK_CHECKS='[{"name":"ci","bucket":"fail","link":"u"},{"name":"lint","bucket":"pass","link":"u"}]'
   FAILED=$(failing_checks_for_pr 9)
   assert_eq "failing check count" "1" "$(echo "$FAILED" | jq 'length')"
   assert_eq "failing check name" "ci" "$(echo "$FAILED" | jq -r '.[0].name')"

   echo ""
   echo "Passed: $PASS  Failed: $FAIL"
   [ "$FAIL" -eq 0 ]
   ```
2. Run `bash tests/test_failing_checks_for_pr.sh` — Scenario B fails: `FAIL: failing check count
   (expected '1', got '0')` (the current `failing_checks_for_pr` calls raw `gh pr checks`
   directly, invisible to the `python3` stub above, and the bash-level `gh` isn't stubbed either
   so it errors out and `checks='[]'` via the `|| checks='[]'` fallback).
3. Implement in `scheduler.sh` — replace `failing_checks_for_pr` (lines 524-535):
   ```bash
   failing_checks_for_pr() {
     local pr_num="$1"
     local checks
     checks=$(python3 "$FACTORY_PROVIDERS_CLI" codehost checks --id "$pr_num" \
       --repo "$FACTORY_REPO_SLUG" --fields name,bucket,link 2>/dev/null) || true
     echo "$checks" | jq -e 'type == "array"' >/dev/null 2>&1 || checks='[]'
     echo "$checks" | jq -c '[.[] | select(.bucket == "fail")]'
   }
   ```
   (Drops the `# NOT migrated to codehost checks (#249): ...` comment block — the gap it
   described is closed by Task 4.)
4. Run `bash tests/test_failing_checks_for_pr.sh` — passes. Also run
   `bash tests/test_scheduler.sh` and `bash tests/test_dispatch_ceiling.sh` (both source
   `scheduler.sh` and could be sensitive to this function's signature) — still pass, since the
   function name/signature/return shape are unchanged.
5. Commit: `refactor(scheduler): route failing_checks_for_pr through codehost checks (#181 R3)`

## Task 6 — R3: Migrate `rescue.py:pr_check_buckets` to `get_change_checks`

**Files:** `scripts/factory_core/rescue.py`, `tests/test_factory_core_rescue.py`

1. Add a routing-lock test to `tests/test_factory_core_rescue.py`, after the Task 3 test:
   ```python
   def test_pr_check_buckets_routes_through_codehost_get_change_checks(monkeypatch):
       seen = {}

       class _FakeCodeHost:
           def get_change_checks(self, id, fields="name,bucket,link", repo=None):
               seen.update(id=id, fields=fields, repo=repo)
               return [{"bucket": "fail"}, {"bucket": "pass"}]
       monkeypatch.setattr(rescue, "get_codehost", lambda: _FakeCodeHost())
       result = rescue.pr_check_buckets(9)
       assert seen == {"id": "9", "fields": "bucket", "repo": rescue._repo()}
       assert result == ["fail", "pass"]
   ```
2. Run `python -m pytest tests/test_factory_core_rescue.py -v -k routes_through_codehost_get_change_checks`
   — fails: `seen` stays `{}`.
3. Implement in `scripts/factory_core/rescue.py` — replace `pr_check_buckets` (lines 62-83):
   ```python
   def pr_check_buckets(pr_num: int) -> list:
       """Bucket of every check on a PR ("pass" / "fail" / "pending" / "skipping" / …)."""
       checks = get_codehost().get_change_checks(str(pr_num), fields="bucket", repo=_repo())
       return [c.get("bucket") for c in checks]
   ```
   (Drops the #249 docstring paragraph — closed by Task 4.) Both raw-`gh` functions are now gone,
   so remove the now-unused `import json` and `import subprocess` from the top of the file
   (lines 18-19) — grep the file first (`grep -n "json\.\|subprocess\." scripts/factory_core/rescue.py`)
   to confirm no other function in the file still uses them before deleting the imports.
4. Run `python -m pytest tests/test_factory_core_rescue.py -v` — all pass (same reasoning as
   Task 3 step 4: `get_change_checks` internally calls the same `subprocess.run(["gh","pr","checks",...,"--json","bucket"])`
   shape the existing `_fake_gh` stub already routes via `"pr" in cmd and "checks" in cmd`).
5. Commit: `refactor(rescue): route pr_check_buckets through CodeHost.get_change_checks; drop unused imports (#181 R3)`

## Task 7 — R1: Convert the 3 `commands/*.md` board-move stragglers

**Files:** `commands/dark-factory-code-review.md`, `commands/dark-factory-validate.md`,
`commands/dark-factory-conformance.md`, `tests/test_command_footer_migration.py` (new — also
covers R4/R6 done-criteria added by later tasks)

1. Create `tests/test_command_footer_migration.py` with the R1 done-criterion first (later tasks
   extend this same file for R4/R6 — see Tasks 11-13):
   ```python
   from pathlib import Path

   COMMAND_FILES = sorted(Path("commands").glob("dark-factory-*.md"))
   ALL_TRACKED_FILES = COMMAND_FILES + [Path("workflows/archon-dark-factory.yaml")]


   def test_no_raw_project_item_edit_or_list_in_commands():
       for f in COMMAND_FILES:
           text = f.read_text(encoding="utf-8")
           assert "gh project item-list" not in text, f
           assert "gh project item-edit" not in text, f
   ```
2. Run `python -m pytest tests/test_command_footer_migration.py -v` — fails for
   `dark-factory-code-review.md`, `dark-factory-validate.md`, `dark-factory-conformance.md`.
3. Implement — in each of the 3 files, replace the `ITEM_ID=$(gh project item-list ...) ...
   gh project item-edit ...` block with the one-line delegation, following the file's own
   `# TARGET-PATH` convention (Design Decision 1):

   **`commands/dark-factory-code-review.md`** (replace lines 170-180):
   ```bash
   python3 dark-factory/scripts/factory_core/providers/cli.py \
     tracker set-status --id "$ISSUE_NUM" --status blocked  # TARGET-PATH
   ```

   **`commands/dark-factory-validate.md`** (replace lines 93-102):
   ```bash
     # Move to Blocked on the project board
     python3 dark-factory/scripts/factory_core/providers/cli.py \
       tracker set-status --id "$ISSUE_NUM" --status blocked  # TARGET-PATH
   ```

   **`commands/dark-factory-conformance.md`** (replace lines 519-529):
   ```bash
   python3 dark-factory/scripts/factory_core/providers/cli.py \
     tracker set-status --id "$ISSUE_NUM" --status blocked  # TARGET-PATH
   ```
4. Run `python -m pytest tests/test_command_footer_migration.py -v` — passes.
5. Commit: `refactor(commands): route board-move blocks through tracker set-status (#181 R1)`

## Task 8 — R4: Add `identity.detection_patterns()` and the `marker`/`markers-regex` CLI verbs

**Files:** `scripts/factory_core/identity.py`, `scripts/factory_core/cli.py`,
`tests/test_factory_core_identity.py`, `tests/test_factory_core_cli.py` (new)

1. Add tests to `tests/test_factory_core_identity.py`, after `test_env_overrides` (after line 25):
   ```python
   def test_detection_patterns_covers_all_footer_variants(monkeypatch):
       ident = _fresh(monkeypatch)
       patterns = ident.detection_patterns()
       assert "Posted by MarketHawk Refinement Pipeline" in patterns
       assert "Posted by MarketHawk Backlog Scheduler" in patterns
       assert "Posted by MarketHawk Dark Factory" in patterns
       assert "Posted by MarketHawk Epic Autopilot" in patterns
       assert "Updated by MarketHawk Dark Factory" in patterns
       assert "dark-factory-cost-report" in patterns

   def test_detection_patterns_excludes_main_red(monkeypatch):
       ident = _fresh(monkeypatch)
       assert not any("Main-Red" in p for p in ident.detection_patterns())
   ```
2. Create `tests/test_factory_core_cli.py`. Each test sets `FACTORY_PRODUCT_NAME` explicitly (not
   relying on the `MarketHawk` default holding under whatever environment pytest runs in — the
   same determinism concern `test_factory_core_identity.py`'s `_fresh(monkeypatch)` helper exists
   to address) and reimports `factory_core.cli` after setting it, since `identity.PRODUCT_NAME` is
   resolved once at import time:
   ```python
   import importlib
   import sys
   from pathlib import Path

   sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

   import pytest


   def _cli(monkeypatch, **env):
       for k in ("FACTORY_OWNER", "FACTORY_REPO", "FACTORY_PROJECT_ID", "FACTORY_PRODUCT_NAME"):
           monkeypatch.delenv(k, raising=False)
       for k, v in env.items():
           monkeypatch.setenv(k, v)
       import factory_core.identity as identity
       importlib.reload(identity)
       import factory_core.cli as cli_mod
       importlib.reload(cli_mod)
       return cli_mod


   def test_marker_prints_footer(monkeypatch, capsys):
       cli_mod = _cli(monkeypatch, FACTORY_PRODUCT_NAME="Acme")
       monkeypatch.setattr(sys, "argv", ["cli.py", "marker", "refinement"])
       cli_mod.main()
       assert capsys.readouterr().out.strip() == "*Posted by Acme Refinement Pipeline*"


   def test_marker_rejects_unknown_kind(monkeypatch):
       cli_mod = _cli(monkeypatch, FACTORY_PRODUCT_NAME="Acme")
       monkeypatch.setattr(sys, "argv", ["cli.py", "marker", "not_a_kind"])
       with pytest.raises(SystemExit):
           cli_mod.main()


   def test_markers_regex_prints_escaped_alternation(monkeypatch, capsys):
       cli_mod = _cli(monkeypatch, FACTORY_PRODUCT_NAME="Acme")
       monkeypatch.setattr(sys, "argv", ["cli.py", "markers-regex"])
       cli_mod.main()
       out = capsys.readouterr().out.strip()
       assert "Posted\\ by\\ Acme\\ Refinement\\ Pipeline" in out or \
           "Posted by Acme Refinement Pipeline" in out
       assert "|" in out
   ```
   (The `re.escape` assertion is written loosely — Python's `re.escape` only escapes
   regex-metacharacter runs, and its exact escaping of plain spaces has changed across Python
   versions; the test checks for either form so it doesn't pin an implementation detail unrelated
   to this ticket.)
3. Run `python -m pytest tests/test_factory_core_identity.py tests/test_factory_core_cli.py -v -k "detection_patterns or marker"`
   — `detection_patterns` tests fail: `AttributeError: module 'factory_core.identity' has no
   attribute 'detection_patterns'`. `test_factory_core_cli.py` tests fail: `SystemExit: 2`
   (unknown subcommand `marker`/`markers-regex`).
4. Implement in `scripts/factory_core/identity.py`, after `marker()` (after line 32):
   ```python
   def detection_patterns() -> list[str]:
       posted = [f"Posted by {PRODUCT_NAME} {suffix}" for suffix in
                 ("Refinement Pipeline", "Backlog Scheduler", "Dark Factory", "Epic Autopilot")]
       return posted + [f"Updated by {PRODUCT_NAME} Dark Factory", "dark-factory-cost-report"]
   ```
   Implement in `scripts/factory_core/cli.py`: two handlers after `_breaker_check_signature`
   (after line 134):
   ```python
   def _marker(args):
       from factory_core import identity
       print(identity.marker(args.kind))


   def _markers_regex(args):
       import re
       from factory_core import identity
       print("|".join(re.escape(p) for p in identity.detection_patterns()))
   ```
   Two subparsers in `main()`, after the `bcs` block (after line 210, before `parsed = parser.parse_args()`):
   ```python
       from factory_core import identity

       mk = sub.add_parser("marker")
       mk.add_argument("kind", choices=list(identity._MARKERS))
       mk.set_defaults(func=_marker)

       mkr = sub.add_parser("markers-regex")
       mkr.set_defaults(func=_markers_regex)
   ```
5. Run `python -m pytest tests/test_factory_core_identity.py tests/test_factory_core_cli.py -v` —
   all pass.
6. Commit: `feat(identity,cli): add detection_patterns() and marker/markers-regex verbs (#181 R4/R5)`

## Task 9 — R4: Migrate `entrypoint.sh`'s 4 footer-literal posts

**Files:** `entrypoint.sh`

Per Design Decision 2, all 4 sites use the hardcoded `/opt/dark-factory/scripts/factory_core/cli.py`
path (matching `set_board_status`/`post_or_update_comment`'s pre/post-clone-safety reasoning).
Line 577 (`"Updated by ..."`) is excluded per Design Decision 3.

1. Line 249 (`run_post_mortem`, kind `factory`) — replace the closing line of the `post_or_update_comment`
   call's heredoc body:
   ```bash
   FOOTER=$(python3 /opt/dark-factory/scripts/factory_core/cli.py marker factory)
   post_or_update_comment "$DF_POST_MORTEM_MARKER" \
     "${DF_POST_MORTEM_MARKER}
   ## Dark Factory — Post-Mortem

   ${post_mortem_text}

   **Exit code:** ${exit_code} | **Phase:** ${INTENT:-fix} | **Timestamp:** ${PROMOTED_AT}

   ---
   ${FOOTER}" || true
   ```
2. Line 652 (refine-failure body, kind `refinement`):
   ```bash
   FOOTER=$(python3 /opt/dark-factory/scripts/factory_core/cli.py marker refinement)
   post_or_update_comment "$REFINE_FAILURE_MARKER" \
     "${REFINE_FAILURE_MARKER}
   ## Refinement Pipeline — Failed

   The refinement pipeline encountered an error (exit code $EXIT_CODE) and could not complete.

   \`\`\`bash
   # Retry
   docker compose --profile factory run --rm dark-factory \"$ARGUMENTS\"
   \`\`\`

   ---
   ${FOOTER}"
   ```
3. Line 671 (factory-failure body, kind `factory`):
   ```bash
   FOOTER=$(python3 /opt/dark-factory/scripts/factory_core/cli.py marker factory)
   post_or_update_comment "$FACTORY_FAILURE_MARKER" \
     "${FACTORY_FAILURE_MARKER}
   ## Dark Factory Run — Failed

   The dark factory encountered an error (exit code $EXIT_CODE) and could not complete.
   Issue has been moved to **Blocked**.

   \`\`\`bash
   # Retry
   docker compose --profile factory run --rm dark-factory \"$ARGUMENTS\"
   \`\`\`

   ---
   ${FOOTER}"
   ```
4. Line 913 (deconflict-resolved success body, kind `factory`, plain `gh issue comment` — not an
   upsert site, stays on its current posting path per R4's scope note):
   ```bash
   FOOTER=$(python3 /opt/dark-factory/scripts/factory_core/cli.py marker factory)
   gh issue comment "$ISSUE_NUM" --repo "$FACTORY_REPO_SLUG" --body \
   "## Dark Factory — Merge Conflicts Resolved

   \`main\` has been merged into \`${FEATURE_BRANCH}\` and all conflicts were resolved automatically.

   The branch has been pushed and is ready for re-review.

   ---
   ${FOOTER}" 2>/dev/null || true
   ```
5. Verify: `grep -n 'Posted by \${FACTORY_PRODUCT_NAME}' entrypoint.sh` now returns zero matches;
   `grep -n 'Updated by \${FACTORY_PRODUCT_NAME}' entrypoint.sh` returns exactly the one documented
   exception at line 577. Run `bash tests/test_entrypoint_current_run.sh
   tests/test_entrypoint_error_signature.sh tests/test_entrypoint_fix_main.sh
   tests/test_entrypoint_preflight.sh tests/test_entrypoint_session_window.sh
   tests/test_entrypoint_cost_report_regression.sh` (the existing entrypoint bash suite) — all
   still pass (these functions' names/signatures/callers are unchanged; only the literal body text
   construction changed).
6. Commit: `refactor(entrypoint): fetch footer text via marker verb instead of hand-inlining (#181 R4)`

## Task 10 — R4/R5: Migrate `scheduler.sh`'s 7 footer-posting sites and the `bot_re` detection regex

**Files:** `scheduler.sh`, `tests/test_has_new_comment_after_report.sh`

Per Design Decision 4, this task does **not** touch lines 316, 334, 351, 374, 715 (search-marker
arguments — different mechanism, documented exclusion).

1. Add `BOT_RE` computed once per poll cycle, at the top of the `while true` loop body (line 848-849):
   ```bash
   while true; do
     DISPATCHED=""
     BOT_RE=$(python3 "$FACTORY_CORE_CLI" markers-regex)
   ```
2. Update `has_new_comment_after_report()` (lines 420-443) to use the global `$BOT_RE` instead of
   hand-listing it locally:
   ```bash
   has_new_comment_after_report() {
     local issue_num="$1"
     local report_marker="$2"
     local comments
     comments=$(python3 "$FACTORY_PROVIDERS_CLI" tracker get-comments --id "$issue_num" 2>/dev/null) \
       || { echo "no"; return; }

     # A comment counts as reviewer feedback only if it appears AFTER the last spec report
     # AND is not one of our own automated comments. The dark factory posts its cost report
     # after the spec on the success path (entrypoint.sh post_cost_report), and the scheduler
     # posts pipeline-status comments — none are feedback, so re-running the spec on them
     # loops the pipeline (issue #124: cost report -> spurious second spec). Match on
     # footer/marker, NOT author: every comment is authored by the same PAT account.
     # BOT_RE is computed once per poll cycle (see the `while true` loop top) via
     # `factory_core/cli.py markers-regex`, sourced from identity.detection_patterns() — not
     # hand-listed here, so it can't drift from identity.py (#181).
     local has_human
     has_human=$(echo "$comments" | jq --arg marker "$report_marker" --arg bot "$BOT_RE" '
       (to_entries | map(select(.value.body | test($marker))) | last | .key // -1) as $ridx
       | if $ridx == -1 then false
         else (to_entries | any(.key > $ridx and (.value.body | test($bot) | not)))
         end')

     if [ "$has_human" = "true" ]; then echo "yes"; else echo "no"; fi
   }
   ```
3. Migrate the 7 footer-posting sites (all kind `scheduler`). Each follows the same pattern —
   compute `FOOTER` once right before the `gh issue comment` call and interpolate it in place of
   the literal closing line:

   **Line 321-325** (refine re-run notice):
   ```bash
   FOOTER=$(python3 "$FACTORY_CORE_CLI" marker scheduler)
   gh issue comment "$issue_num" --repo "$FACTORY_REPO_SLUG" --body \
   "🔄 **Refinement Pipeline** — Re-running with new feedback.

   ---
   ${FOOTER}" 2>/dev/null || true
   ```

   **Line 356-360** (plan re-run notice):
   ```bash
   FOOTER=$(python3 "$FACTORY_CORE_CLI" marker scheduler)
   gh issue comment "$issue_num" --repo "$FACTORY_REPO_SLUG" --body \
   "🔄 **Refinement Pipeline** — Re-running plan with new feedback.

   ---
   ${FOOTER}" 2>/dev/null || true
   ```

   **Line 895-903** (CI-failing-moved-to-blocked notice):
   ```bash
   FOOTER=$(python3 "$FACTORY_CORE_CLI" marker scheduler)
   gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body "## Dark Factory — CI Failing, Moved to Blocked

   PR #${PR_NUM} has failing CI checks, so this ticket has been moved out of **In review** to **Blocked**. The factory will retry automatically, continue the existing PR branch, and attempt to fix the failures.

   **Failing checks:**
   ${FAIL_LIST}

   ---
   ${FOOTER}" 2>/dev/null || true
   ```

   **Line 960-965** (orphaned-run-recovered notice):
   ```bash
   FOOTER=$(python3 "$FACTORY_CORE_CLI" marker scheduler)
   gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body "## Dark Factory — Orphaned Run Recovered

   This issue was left in **In progress** with no running factory container — the run died without its error handler executing (e.g. a host restart or OOM/SIGKILL). The scheduler has moved it to **Blocked** so it will be retried automatically.

   ---
   ${FOOTER}" 2>/dev/null || true
   ```

   **Line 1109-1126** (above-dispatch-ceiling notice):
   ```bash
   FOOTER=$(python3 "$FACTORY_CORE_CLI" marker scheduler)
   gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body \
   "## Scheduler — Above Dispatch Ceiling

   This ticket has been classified as **above the autonomous dispatch ceiling** \
   (size: XL, or size: M with a perf/architectural/migration title keyword).

   Spec and plan are complete. **A human must pair on implementation.**

   To proceed:
   1. Remove the \`$ABOVE_CEILING_LABEL\` label.
   2. Dispatch manually:
      \`\`\`bash
      docker compose --profile factory run --rm dark-factory \"Fix issue #${ISSUE}\"
      \`\`\`
      Or implement directly in a local worktree.

   ---
   ${FOOTER}" 2>/dev/null || true
   ```

   **Line 1217-1220** (plan-generation-starting notice):
   ```bash
   FOOTER=$(python3 "$FACTORY_CORE_CLI" marker scheduler)
   gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body "📋 **Refinement Pipeline** — Starting plan generation and architect validation.

   ---
   ${FOOTER}" 2>/dev/null || true
   ```

   **Line 1266-1269** (spec-generation-starting notice):
   ```bash
   FOOTER=$(python3 "$FACTORY_CORE_CLI" marker scheduler)
   gh issue comment "$ISSUE" --repo "$FACTORY_REPO_SLUG" --body "🧠 **Refinement Pipeline** — Starting brainstorming and spec generation.

   ---
   ${FOOTER}" 2>/dev/null || true
   ```
4. Update `tests/test_has_new_comment_after_report.sh` — it sources `scheduler.sh` with
   `SCHEDULER_SOURCE_ONLY=1`, which returns before the `while true` loop, so `BOT_RE` is never set
   by the sourcing itself. Extend the `python3` stub (after line 43) to answer `markers-regex`, and
   set `BOT_RE` explicitly before the assertions (mirroring what the production loop does), reusing
   the file's own `SCHED_FOOTER`/`SPEC_FOOTER`/`COST_FOOTER`/`AUTOPILOT_FOOTER` constants:
   ```bash
   python3() {
     case "$*" in
       *"providers/cli.py"*"get-comments"*) printf '%s' "$MOCK_COMMENTS" ;;
       *"markers-regex"*)
         printf '%s' "Posted by ${FACTORY_PRODUCT_NAME:-MarketHawk} Refinement Pipeline|Posted by ${FACTORY_PRODUCT_NAME:-MarketHawk} Backlog Scheduler|Posted by ${FACTORY_PRODUCT_NAME:-MarketHawk} Dark Factory|Updated by ${FACTORY_PRODUCT_NAME:-MarketHawk} Dark Factory|dark-factory-cost-report|Posted by ${FACTORY_PRODUCT_NAME:-MarketHawk} Epic Autopilot"
         ;;
       *) return 0 ;;
     esac
   }
   BOT_RE=$(python3 "${FACTORY_CORE_CLI:-/opt/dark-factory/scripts/factory_core/cli.py}" markers-regex)
   ```
5. Run `bash tests/test_has_new_comment_after_report.sh` — all 6 scenarios still pass (identical
   detection semantics; the regex source moved, its content didn't). Run
   `grep -n 'Posted by \${FACTORY_PRODUCT_NAME}\|Updated by \${FACTORY_PRODUCT_NAME}' scheduler.sh`
   — returns exactly the 6 documented Design-Decision-4 exclusions (316, 334, 351, 374, 433's
   `bot_re` is now gone entirely, 715) — i.e. 5 lines, since `bot_re` (433) no longer contains the
   literal text at all after step 2.
6. Commit: `refactor(scheduler): fetch footer text via marker verb; source bot_re from markers-regex once per cycle (#181 R4/R5)`

## Task 11 — R4: Migrate `workflows/archon-dark-factory.yaml`'s 3 footer-literal sites

**Files:** `workflows/archon-dark-factory.yaml`, `tests/test_command_footer_migration.py`

1. Extend `tests/test_command_footer_migration.py` (created in Task 7) with the R4 done-criterion,
   scoped to the workflow yaml only for now — **not** `ALL_TRACKED_FILES` yet, since the
   `commands/*.md` sites aren't migrated until Task 13 and this task's commit must leave the suite
   green (Task 13 widens this same test to `ALL_TRACKED_FILES` once those sites are also fixed):
   ```python
   def test_no_raw_footer_literal_in_commands_or_workflow():
       for f in [Path("workflows/archon-dark-factory.yaml")]:
           text = f.read_text(encoding="utf-8")
           assert "Posted by ${FACTORY_PRODUCT_NAME}" not in text, f
           assert "Updated by ${FACTORY_PRODUCT_NAME}" not in text, f
   ```
2. Run `python -m pytest tests/test_command_footer_migration.py -v -k no_raw_footer_literal` —
   fails: `workflows/archon-dark-factory.yaml` still has 3 raw footer literals.
3. Implement in `workflows/archon-dark-factory.yaml`:

   **Line ~481** (`refine-push`'s `_FAIL_BODY`, already posted via `tracker comment --marker`
   upsert — per R4's scope note, keep the `--marker` argument unchanged, only stop hand-inlining
   the footer):
   ```yaml
          _FOOTER=$(python3 "$_PCLI_FACTORY_CORE" marker refinement 2>/dev/null || echo "")
          _FAIL_BODY="<!-- df-refine-failure -->
      ## Refinement Pipeline — Failed

      The refine agent ended without producing a committed spec (\`docs/superpowers/specs/\`) for this issue. No gate label was applied; this item remains eligible for automatic retry.

      \`\`\`bash
      # Retry manually if needed
      docker compose --profile factory run --rm dark-factory \"Refine issue #${ISSUE}\"
      \`\`\`

      ---
      ${_FOOTER}"
   ```
   Add `_PCLI_FACTORY_CORE="${CLONE_DIR:-.}/dark-factory/scripts/factory_core/cli.py"` alongside
   the existing `_PCLI="${CLONE_DIR:-.}/dark-factory/scripts/factory_core/providers/cli.py"`
   declaration in this step's bash block, following the step's existing local-variable convention.

   **Line ~528** (`plan-push-and-advance`'s `_FAIL_BODY`) — identical shape, same `_PCLI_FACTORY_CORE`
   declaration added to that step's bash block, footer kind `refinement`.

   **Line ~1426** (final status-report comment, plain `gh issue comment`, kind `factory`):
   ```yaml
      _FOOTER=$(python3 "${CLONE_DIR:-.}/dark-factory/scripts/factory_core/cli.py" marker factory 2>/dev/null || echo "")
      gh issue comment "$ISSUE" --body "## Dark Factory Run — ${ACTION}

      ${PR_LINE}
      ${EPIC_LINE}
      **Branch:** \`${BRANCH}\`

      ### Changes

      ${CHANGES:-_No implementation summary available._}

      ${BLAST_GATE_SECTION}

      ${CONFORMANCE_SECTION}
      ${CODE_REVIEW_SECTION}
      ${CONFLICT_SECTION}

      ### Preview Environment

      ${PREVIEW_SECTION}

      ### Commands
      \`\`\`bash
      # Iterate after review
      docker compose --profile factory run --rm dark-factory \"Continue issue #${ISSUE}\"

      # Tear down when done
      docker compose --profile factory run --rm dark-factory \"Close issue #${ISSUE}\"
      \`\`\`

      ---
      ${_FOOTER}"
   ```
4. Run `python -m pytest tests/test_command_footer_migration.py -v -k no_raw_footer_literal` and
   `bash tests/test_run_compose.sh` (parses/validates the workflow yaml) — both pass. Run
   `python -m pytest tests/ -v` — full suite is green (this task's test is scoped to only the file
   this task actually fixes, so the commit below leaves the suite green).
5. Commit: `refactor(workflow): fetch footer text via marker verb in archon-dark-factory.yaml (#181 R4)`

## Task 12 — R6: Migrate the 7 `gh issue edit --add-label needs-discussion` sites

**Files:** `commands/dark-factory-code-review.md`, `commands/dark-factory-validate.md`,
`commands/dark-factory-conformance.md`, `commands/dark-factory-refine.md`,
`commands/dark-factory-plan.md`, `tests/test_command_footer_migration.py`

1. Extend `tests/test_command_footer_migration.py` with the R6 done-criterion:
   ```python
   def test_no_raw_add_label_needs_discussion():
       for f in COMMAND_FILES:
           text = f.read_text(encoding="utf-8")
           assert "--add-label needs-discussion" not in text, f
   ```
2. Run `python -m pytest tests/test_command_footer_migration.py -v -k no_raw_add_label` — fails
   for all 5 files (6 files if counting revise-advisory.md, but that file has no label site — 7
   occurrences across 5 files per the spec's inventory, confirmed by direct grep).
3. Implement — each site converts `gh issue edit [$ISSUE_NUM|"$ISSUE_NUM"]
   [--repo "$FACTORY_REPO_SLUG"] --add-label needs-discussion` to the `tracker label` delegation,
   following each file's own quoting/`--repo` style so the diff stays minimal:

   **`commands/dark-factory-validate.md:92`**:
   ```bash
   python3 dark-factory/scripts/factory_core/providers/cli.py \
     tracker label --id "$ISSUE_NUM" --add needs-discussion  # TARGET-PATH
   ```

   **`commands/dark-factory-code-review.md:183`**:
   ```bash
   python3 dark-factory/scripts/factory_core/providers/cli.py \
     tracker label --id "$ISSUE_NUM" --add needs-discussion  # TARGET-PATH
   ```

   **`commands/dark-factory-conformance.md:533`**:
   ```bash
   python3 dark-factory/scripts/factory_core/providers/cli.py tracker label --id $ISSUE_NUM --add needs-discussion  # TARGET-PATH
   ```

   **`commands/dark-factory-refine.md:81`** (inline code-span, not a fenced block):
   ```markdown
   2. Add `needs-discussion` label: `python3 dark-factory/scripts/factory_core/providers/cli.py tracker label --id $ISSUE_NUM --add needs-discussion`
   ```

   **`commands/dark-factory-refine.md:105`**:
   ```markdown
      - Run: `python3 dark-factory/scripts/factory_core/providers/cli.py tracker label --id $ISSUE_NUM --add needs-discussion`
   ```

   **`commands/dark-factory-plan.md:104`**:
   ```markdown
      - Add `needs-discussion` label: `python3 dark-factory/scripts/factory_core/providers/cli.py tracker label --id $ISSUE_NUM --add needs-discussion`
   ```

   **`commands/dark-factory-plan.md:147`**:
   ```markdown
        - Add `needs-discussion` label: `python3 dark-factory/scripts/factory_core/providers/cli.py tracker label --id $ISSUE_NUM --add needs-discussion`
   ```
4. Run `python -m pytest tests/test_command_footer_migration.py -v -k no_raw_add_label` — passes.
5. Commit: `refactor(commands): route needs-discussion label-add through tracker label (#181 R6)`

## Task 13 — R4: Migrate the remaining 7 `commands/*.md` footer-literal sites

**Files:** `commands/dark-factory-code-review.md`, `commands/dark-factory-validate.md`,
`commands/dark-factory-conformance.md`, `commands/dark-factory-refine.md`,
`commands/dark-factory-plan.md` (×2), `commands/dark-factory-revise-advisory.md`

1. Widen the scope of `test_no_raw_footer_literal_in_commands_or_workflow` (added in Task 11 step 1,
   currently scoped to just the workflow yaml) to the full `ALL_TRACKED_FILES` list, now that this
   task is about to fix every remaining site it covers:
   ```python
   def test_no_raw_footer_literal_in_commands_or_workflow():
       for f in ALL_TRACKED_FILES:
           text = f.read_text(encoding="utf-8")
           assert "Posted by ${FACTORY_PRODUCT_NAME}" not in text, f
           assert "Updated by ${FACTORY_PRODUCT_NAME}" not in text, f
   ```
2. Run `python -m pytest tests/test_command_footer_migration.py -v -k no_raw_footer_literal` —
   fails: the 6 `commands/*.md` files below still each have a raw footer literal (the workflow
   yaml, already fixed in Task 11, no longer fails).
3. Implement, each replacing the literal `*Posted by ${FACTORY_PRODUCT_NAME} ...*` line with a
   fetched-footer instruction, matching each file's fenced-vs-inline style:

   **`commands/dark-factory-code-review.md:167`** (inside the fenced `gh issue comment` block —
   add the fetch line immediately before the block, kind `factory`):
   ```bash
   FOOTER=$(python3 dark-factory/scripts/factory_core/cli.py marker factory)  # TARGET-PATH
   gh issue comment "$ISSUE_NUM" --repo "$FACTORY_REPO_SLUG" --body "## Code Review — Blocked

   The AI code reviewer found ${BLOCKERS} blocking issue(s) (severity ≥ ${BLOCK_THRESHOLD}). See the inline comments on PR #${PR_NUM}.

   $(jq -r '.blockers[] | \"- **[\(.severity)] \(.category)** \(.path):\(.line) — \(.description)\"' \"$ARTIFACTS_DIR/review_result.json\")

   ### Next Steps
   Fix the issues and re-run: \`docker compose --profile factory run --rm dark-factory \\\"Continue issue #${ISSUE_NUM}\\\"\`, or add \`needs-discussion\` if a finding is a false positive.

   ---
   ${FOOTER}"
   ```

   **`commands/dark-factory-validate.md:89`** (kind `factory`):
   ```bash
   FOOTER=$(python3 dark-factory/scripts/factory_core/cli.py marker factory)  # TARGET-PATH
   gh issue comment "$ISSUE_NUM" --body "$(cat <<EOF
   ## Blast-Radius Gate — BLOCKED

   The blast-radius gate has flagged this change as requiring human review before it can auto-merge.

   **Trigger:** $BLAST_TRIGGER

   **Triggered files:**
   $BLAST_FILES

   Remove the \`needs-discussion\` label after reviewing and approving the risk, then re-run validate:
   \`\`\`
   docker compose --profile factory run --rm dark-factory "Validate issue #$ISSUE_NUM"
   \`\`\`
   ---
   $FOOTER
   EOF
   )"
   ```

   **`commands/dark-factory-conformance.md:515`** (kind `factory`):
   ```bash
   FOOTER=$(python3 dark-factory/scripts/factory_core/cli.py marker factory)  # TARGET-PATH
   gh issue comment $ISSUE_NUM --body "## Spec Conformance — Blocked

   The implementation has material divergences from the spec that could not be resolved in $MAX_CYCLES reconcile cycle(s).

   $CONFORMANCE_DIALOGUE

   ### Next Steps

   Review the deviations above and either:
   - Fix the implementation to match the spec, then re-run: \`docker compose --profile factory run --rm dark-factory \"Continue issue #$ISSUE_NUM\"\`
   - Update the spec to document the deviation as intentional, then re-run.
   - Add \`needs-discussion\` if the spec itself needs revisiting.

   ---
   $FOOTER"
   ```

   **`commands/dark-factory-refine.md:230`** (prose "Next Steps" template — the phase agent
   composes and posts this comment itself; instruct it to fetch the footer first, kind
   `refinement`):
   ```markdown
   6. Fetch the footer: `python3 dark-factory/scripts/factory_core/cli.py marker refinement` —
      use its output in place of the literal line below when composing the comment.

      ---
      <fetched footer text>
   ```

   **`commands/dark-factory-plan.md:145`** (inside the reconcile-loop-exhausted comment template,
   kind `refinement`):
   ```markdown
        - Post the conformance dialogue as an issue comment (fetch the footer first via
          `python3 dark-factory/scripts/factory_core/cli.py marker refinement`):
          ```
          ## Spec Conformance — Blocked (Plan)

          The plan has material divergences from the spec that could not be resolved in $MAX_CYCLES reconcile cycle(s).

          $CONFORMANCE_DIALOGUE

          ---
          <fetched footer text>
          ```
   ```

   **`commands/dark-factory-plan.md:216`** (prose "Next Steps" template, kind `refinement`,
   same pattern as `dark-factory-refine.md:230`):
   ```markdown
   6. Fetch the footer: `python3 dark-factory/scripts/factory_core/cli.py marker refinement` —
      use its output in place of the literal line below when composing the comment.

      ---
      <fetched footer text>
   ```

   **`commands/dark-factory-revise-advisory.md:138`** (kind `factory`):
   ```bash
   FOOTER=$(python3 dark-factory/scripts/factory_core/cli.py marker factory)  # TARGET-PATH
   SUMMARY=$(cat "$ARTIFACTS_DIR/revise_summary.txt" 2>/dev/null || echo "(no summary)")
   gh api "repos/${FACTORY_REPO_SLUG}/pulls/$PR_NUM/reviews" \
     --method POST \
     --field body="## Advisory Findings Addressed

   Automatically addressed **${ADVISORY_COUNT}** advisory finding(s):

   ${SUMMARY}

   ---
   ${FOOTER}" \
     --field event="COMMENT" || \
     echo "revise-advisory: WARNING — posting follow-up review comment failed (continuing)"
   ```
4. Run `python -m pytest tests/test_command_footer_migration.py -v` — all pass (R1, R4, R6
   done-criteria together).
5. Commit: `refactor(commands): fetch footer text via marker verb across remaining command docs (#181 R4)`

## Task 14 — R7: Final repo-wide verification

**Files:** none (verification only)

1. Run the full test suite: `python -m pytest tests/ -v` — all pass.
2. Run the bash test suite: for each `tests/test_*.sh`, `bash tests/<file>`, focusing on
   `test_scheduler.sh`, `test_scheduler_ceiling.sh`, `test_scheduler_pagination.sh`,
   `test_scheduler_main_red_fixer.sh`, `test_scheduler_autopilot_guard.sh`,
   `test_dispatch_ceiling.sh`, `test_has_new_comment_after_report.sh`,
   `test_failing_checks_for_pr.sh`, and the full `test_entrypoint_*.sh` set — all pass.
3. Run each spec-cited done-criterion grep directly and confirm the exact expected counts (not
   just the pytest wrappers, as a second, independent check):
   ```bash
   # R1 — zero outside board.py
   grep -rn "gh project item-list\|gh project item-edit" --include="*.sh" --include="*.md" --include="*.yaml" . \
     | grep -v "scripts/factory_core/board.py" | grep -v "^./docs/archive/" | grep -v "^./\.archon/"
   # expect: zero matches (the .archon/ copies are runtime-regenerated, not edited directly — see
   # the spec's Assumptions; docs/archive/ are historical records, not live call sites)

   # R4 — footer literals: expect exactly 1 (entrypoint.sh:577, Design Decision 3) +
   # 5 (scheduler.sh Class A search-marker sites, Design Decision 4)
   grep -rn 'Posted by \${FACTORY_PRODUCT_NAME}\|Updated by \${FACTORY_PRODUCT_NAME}' \
     entrypoint.sh scheduler.sh commands/*.md workflows/archon-dark-factory.yaml

   # R6 — zero raw label-add left
   grep -rn "add-label needs-discussion" commands/*.md entrypoint.sh scheduler.sh workflows/*.yaml
   ```
4. Confirm `scripts/factory_core/identity.py` still defines exactly 5 `_MARKERS` kinds (no scope
   creep from Task 8) and that `detection_patterns()` returns exactly 6 strings (4 "Posted by" +
   1 "Updated by" + 1 cost-report marker), matching Requirement R5.
5. No commit — this task is verification-only, confirming the preceding 13 tasks collectively
   satisfy every AC in the spec's Requirements section.

---

## Notes for the architect/conformance reviewers

- Design Decisions 3 and 4 are **intentional, documented scope narrowings** relative to the
  spec's literal examples, not gaps — each is justified by a concrete correctness risk (regex
  breakage for #4, no corresponding verb for #3) discovered while re-deriving the current code,
  per the operator's approval-comment condition #3 ("re-grep at implementation time ... rather
  than trusting this spec's static counts").
- Tasks are ordered so `git bisect` between any two commits always lands on a state where the
  existing test suite passes — provider-layer capability (Tasks 1-6) lands and is fully tested
  before any adapter starts depending on it (Tasks 7-13).
- R3 (Task 4) carries the CLAUDE.md "never weaken a safety gate" burden of proof: its 3 regression
  tests (green-path-unchanged, nonzero-exit-now-returns-data, genuine-error-still-empty) are the
  operator's condition #1 satisfied directly, not by inference.
