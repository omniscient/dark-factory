# Implementation Plan: CI shell-test coverage guard

**Issue:** #426

**Spec:** `docs/superpowers/specs/2026-11-19-ci-shell-test-coverage-guard-design.md`
**Related:** #395 (Task 5 wired `test_entrypoint_preflight.sh` as a one-off), #418, #201 (Known
gaps), #531 (umbrella — this ticket owns only its "tests not in CI" item)

---

## Goal

Add `tests/test_ci_covers_shell_tests.py`. It fails when a `tests/*.sh` file is neither
referenced in a live `run:` step of `.github/workflows/ci.yml` nor listed in an in-file
`CI_EXEMPT_SHELL_TESTS` dict with a reason. It also fails when an exemption is stale (the file
has since been wired in or deleted). Seed the dict with every currently unwired shell test. Each
reason comes from one real, credential-stripped run of that test. Then update
`docs/factory-target-boundary.md`'s now-stale "CI coverage" Known-gaps bullet to point at the
dict.

## Architecture

- **One new test file**, pure Python. The existing `python -m pytest tests/ -v` step at
  `ci.yml:13` picks it up automatically, so **`ci.yml` is not edited** (spec Requirement 6).
- **House style** follows `tests/test_push_gate_dag.py`: bare module-level `def test_*()`, no
  fixtures, `_REPO_ROOT = Path(__file__).resolve().parents[1]`, `yaml.safe_load` of a repo
  file, and imports ordered `re`, `pathlib`, blank line, `yaml`. The allowlist is a
  module-level dict in the test file, not a side YAML/JSON file. This follows spec Requirement
  2 and the `[AVOID]` memory entry recorded by refine #426 in `.archon/memory/architecture.md`.
- **Three helpers:**
  - `_referenced_in_ci(ci_text)` (YAML-aware): walks `jobs.*.steps`, skips any step whose
    `if:` is literally `false`, and regex-matches `tests/[a-z_0-9]+\.sh` inside each step's
    `run:` string. YAML comments never count.
  - `_violations(sh_files, referenced, exempt) -> (unaccounted, stale)`: pure set logic,
    unit-tested with synthetic inputs so the guard is *proven* able to fail.
  - `_live()`: reads the real repo and asserts both sets are non-empty, so a wrong path or a
    broken parse can't pass vacuously.
- **Seeding:** the list is re-derived at implementation time from the issue's `comm -23`
  reproduce command against the branch HEAD. Each file is run once under the spec's
  credential-stripped command in a scratch worktree (Task 2).
- **Doc follow-through:** rewrite one bullet in `docs/factory-target-boundary.md`'s
  `## Known gaps` section. The rewrite cites
  `tests/test_ci_covers_shell_tests.py::CI_EXEMPT_SHELL_TESTS`, so the existing drift guard
  `tests/test_factory_target_boundary_doc.py::test_doc_citations_resolve_to_real_symbols`
  pins the citation for free.

**Not touched:** `.github/workflows/ci.yml`, any shell test (fixing or wiring the failing ones
is follow-up work, spec Req. 5 and 7), `gate_*`, breaker, budgets, `deploy/**`, and
`.github/workflows/publish.yml`.

## Tech Stack

Python 3.12 (CI) / pytest / PyYAML (`pip install pytest pyyaml aiohttp`, `ci.yml:12`); bash and
coreutils `timeout` for the one-off seeding runs only.

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `tests/test_ci_covers_shell_tests.py` | Create | Guard test: helpers, `CI_EXEMPT_SHELL_TESTS`, 3 live-repo tests, 4 synthetic tests |
| `docs/factory-target-boundary.md` | Modify (one bullet, ~line 309) | Known-gaps "CI coverage" bullet now points at the allowlist |

Those are the only two files changed (spec: "the only file other than the new test that the
implementation touches"). The implement phase also records the doc edit in its run's
`doc-impact.md` artifact, if implement Phase 4 writes one on the current image. That artifact
lives outside the repo tree.

## Plan-time observations (for the implementer's cross-check, not a substitute for Task 2)

The plan phase ran the reproduce command against `f457c2c` / branch HEAD `8de351f`. It listed
**23** unwired files, matching the spec's Overview. Each file was run once in a scratch worktree
named `…/dark-factory`, with credentials stripped. **16 pass, 7 fail:**

| File | Plan-time result (first failure) |
|---|---|
| `test_159_regression.sh` | `FAIL: set_board_status called (→ Blocked) — expected='1' got='0'` |
| `test_codeindex_config.sh` | `FAIL: implement depends on update-codeindex — pattern not found in …/workflows/archon-dark-factory.yaml` |
| `test_dispatch_ceiling.sh` | `FAIL: L → always above — expected='0' got='1'` |
| `test_entrypoint_fix_main.sh` | `FAIL: fix-main route (896) not before smoke gate (679)` |
| `test_restore_drill.sh` | `FAIL: R2: no-backup exits 2 (expected 0); spec requires exit 0 on fresh-deploy skip` |
| `test_scheduler.sh` | `tests/test_scheduler.sh: line 411: STATUS_REFINED: unbound variable` |
| `test_session_window_gate.sh` | `FAIL: 'Priority 1.5:' gate (351) does not precede breaker calls (33)` |

Everything else passed: `test_431_telemetry_isolation.sh`, `test_close_preview_teardown.sh`,
`test_config_deletion.sh`, `test_conformance_memory_write.sh`,
`test_epic_autopilot_config.sh`, `test_failing_checks_for_pr.sh`,
`test_has_new_comment_after_report.sh`, `test_load_memory.sh`, `test_memory_integration.sh`,
`test_memory_write_gate.sh`, `test_off_board_detection.sh`, `test_preview_differentiator.sh`,
`test_scheduler_autopilot_guard.sh`, `test_scheduler_ceiling.sh`,
`test_scheduler_main_red_fixer.sh` and `test_scheduler_pagination.sh`.

**Lesson baked into Task 2:** `test_431_telemetry_isolation.sh` derives
`CLONE_DIR="$(dirname "$REPO_ROOT")"` (its line 53) and then calls
`$CLONE_DIR/dark-factory/scripts/...`. In a worktree whose directory is *not* named
`dark-factory`, it fails with
`python3: can't open file '…/dark-factory/scripts/factory_core/cli.py'`. That is a false
failure. The scratch worktree path must therefore end in `/dark-factory`.

The plan phase also tried running all 23 in parallel. Two tests can collide on shared `/tmp`
state that way, so Task 2 runs them **serially**.

---

## Task 1: Helpers and synthetic tests (prove the guard can fail)

**Files:** `tests/test_ci_covers_shell_tests.py` (create)

- [ ] **Step 1.1 — Pre-flight: pytest available.** Run:
  ```bash
  python3 -m pytest --version || pip install --user pytest pyyaml
  ```
  Expected: `pytest 8.x.x` (the factory image may not ship pytest; the `pip` fallback installs
  it). If `pip` is unavailable, every later `python3 -m pytest tests/test_ci_covers_shell_tests.py -v`
  step can be replaced with:
  ```bash
  PYTHONPATH=scripts python3 -c "import sys; sys.path.insert(0, 'tests'); import test_ci_covers_shell_tests as t; [getattr(t, n)() or print('PASS', n) for n in sorted(dir(t)) if n.startswith('test_')]"
  ```
  This runs every `test_*` function in that module and prints `PASS <name>`, or stops at the
  first `AssertionError`/`NameError`.

- [ ] **Step 1.2 — Write the failing synthetic tests.** Create
  `tests/test_ci_covers_shell_tests.py` with this exact content (helpers deliberately absent):
  ```python
  """Guard (#426): every tests/*.sh must run in CI or be explicitly exempted.

  .github/workflows/ci.yml wires shell tests one `- run: bash tests/X.sh` step at a time, so
  an unwired file is indistinguishable from a passing one. This file fails when a shell test
  is neither referenced in a live ci.yml run: step nor listed in CI_EXEMPT_SHELL_TESTS, and
  when an exemption goes stale (the file was wired in or deleted)."""
  import re
  from pathlib import Path

  import yaml

  _REPO_ROOT = Path(__file__).resolve().parents[1]
  _CI_YML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"


  # --- synthetic checks: prove the guard can fail ---

  def test_new_unwired_file_is_unaccounted():
      assert _violations({"a.sh", "new.sh"}, {"a.sh"}, {}) == ({"new.sh"}, set())


  def test_exempt_file_that_is_wired_is_stale():
      assert _violations({"a.sh"}, {"a.sh"}, {"a.sh": "r"}) == (set(), {"a.sh"})


  def test_exempt_file_that_is_missing_is_stale():
      assert _violations({"a.sh"}, {"a.sh"}, {"gone.sh": "r"}) == (set(), {"gone.sh"})


  def test_commented_out_or_disabled_step_is_not_referenced():
      ci = (
          "jobs:\n  tests:\n    steps:\n"
          "      - run: bash tests/test_live.sh\n"
          "      # - run: bash tests/test_commented.sh\n"
          "      - run: bash tests/test_off.sh\n        if: false\n"
      )
      assert _referenced_in_ci(ci) == {"test_live.sh"}
  ```

- [ ] **Step 1.3 — Verify they fail.** Run:
  ```bash
  python3 -m pytest tests/test_ci_covers_shell_tests.py -v
  ```
  Expected: `4 failed`, each with `NameError: name '_violations' is not defined` (or
  `'_referenced_in_ci'`).

- [ ] **Step 1.4 — Implement the helpers.** Insert this block directly after the `_CI_YML = …`
  line (before the `# --- synthetic checks` comment):
  ```python
  _SH_REF_PATTERN = re.compile(r"tests/[a-z_0-9]+\.sh")


  def _referenced_in_ci(ci_text: str) -> set:
      """Shell tests named in the run: string of a live step. YAML comments never count."""
      refs = set()
      for job in ((yaml.safe_load(ci_text) or {}).get("jobs") or {}).values():
          for step in job.get("steps") or []:
              if str(step.get("if", "")).strip().lower() == "false":
                  continue
              for m in _SH_REF_PATTERN.findall(str(step.get("run") or "")):
                  refs.add(m.split("/", 1)[1])
      return refs


  def _violations(sh_files: set, referenced: set, exempt) -> tuple:
      """Pure: (unaccounted, stale). No filesystem access, so it is testable synthetically."""
      unaccounted = sh_files - referenced - set(exempt)
      stale = {name for name in exempt if name in referenced or name not in sh_files}
      return unaccounted, stale


  def _live() -> tuple:
      sh_files = {p.name for p in (_REPO_ROOT / "tests").glob("*.sh")}
      referenced = _referenced_in_ci(_CI_YML.read_text(encoding="utf-8"))
      # A wrong path or a parse that finds nothing must not pass vacuously.
      assert sh_files, "no tests/*.sh found — wrong repo root?"
      assert referenced, "no shell test found in any ci.yml run: step — parse broken?"
      return sh_files, referenced
  ```
  Note: `if: false` in YAML loads as the bool `False`, and `str(False).lower() == "false"`, so
  both the bool and the quoted string `"false"` are skipped. `if: always()` and other
  expressions are kept.

- [ ] **Step 1.5 — Verify they pass.** Run:
  ```bash
  python3 -m pytest tests/test_ci_covers_shell_tests.py -v
  ```
  Expected: `4 passed`.

- [ ] **Step 1.6 — Commit.**
  ```bash
  git add tests/test_ci_covers_shell_tests.py
  git commit -m "test(ci): add shell-test coverage helpers with synthetic checks (#426)"
  ```

## Task 2: Live-repo checks and the seeded allowlist

**Files:** `tests/test_ci_covers_shell_tests.py` (modify)

- [ ] **Step 2.1 — Add the live tests with an empty allowlist.** Insert this block after
  `_live()` and before `# --- synthetic checks`:
  ```python
  # tests/*.sh not yet wired into .github/workflows/ci.yml, each with a reason.
  # Seeded 2026-11-19 (#426) from:
  #   comm -23 \
  #     <(git ls-tree HEAD tests/ --name-only | grep '\.sh$' | sed 's|tests/||' | sort) \
  #     <(git show HEAD:.github/workflows/ci.yml | grep -oE 'tests/[a-z_0-9]+\.sh' | sed 's|tests/||' | sort -u)
  # Each reason is the observed result of one standalone run with credentials stripped:
  #   env -u GH_TOKEN -u GITHUB_TOKEN GH_CONFIG_DIR="$(mktemp -d)" \
  #     SCHEDULER_STATE_DIR="$(mktemp -d)" timeout 120 bash tests/X.sh
  # either "passes standalone — wire in follow-up" or "fails: <first failure>".
  # Wiring a test into ci.yml means deleting its entry here (test_no_stale_exemptions).
  CI_EXEMPT_SHELL_TESTS = {
  }


  # --- live-repo checks ---

  def test_every_shell_test_is_wired_or_exempt():
      unaccounted, _ = _violations(*_live(), CI_EXEMPT_SHELL_TESTS)
      assert not unaccounted, (
          f"tests/*.sh not in ci.yml and not in CI_EXEMPT_SHELL_TESTS: {sorted(unaccounted)} — "
          "add a `- run: bash tests/<file>` step to .github/workflows/ci.yml, or exempt it "
          "here with a reason"
      )


  def test_no_stale_exemptions():
      _, stale = _violations(*_live(), CI_EXEMPT_SHELL_TESTS)
      assert not stale, (
          f"CI_EXEMPT_SHELL_TESTS entries now stale (wired into ci.yml or deleted): {sorted(stale)}"
      )


  def test_exemptions_have_reasons():
      empty = [name for name, reason in CI_EXEMPT_SHELL_TESTS.items() if not reason.strip()]
      assert not empty, f"CI_EXEMPT_SHELL_TESTS entries missing a reason: {empty}"
  ```

- [ ] **Step 2.2 — Verify the live check fails on today's repo.** Run:
  ```bash
  python3 -m pytest tests/test_ci_covers_shell_tests.py -v
  ```
  Expected: `1 failed, 6 passed`. The failure is `test_every_shell_test_is_wired_or_exempt`,
  and its message lists the unwired files (23 at plan time). This is the issue's acceptance
  item 1 shown failing on real data.

- [ ] **Step 2.3 — Re-derive the seed list from branch HEAD** (spec Req. 5; do not copy it from
  this plan or the spec):
  ```bash
  comm -23 \
    <(git ls-tree HEAD tests/ --name-only | grep '\.sh$' | sed 's|tests/||' | sort) \
    <(git show HEAD:.github/workflows/ci.yml | grep -oE 'tests/[a-z_0-9]+\.sh' | sed 's|tests/||' | sort -u) \
    > /tmp/unwired-426.txt
  wc -l < /tmp/unwired-426.txt; cat /tmp/unwired-426.txt
  ```
  Expected: 23 at plan time. If the count or names differ, use the fresh list; `main` may have
  added, deleted or wired tests since. Cross-check that the list is exactly the set Step 2.2's
  failure message printed.

- [ ] **Step 2.4 — Run each seeded file once, serially, credential-stripped, in a scratch
  worktree named `dark-factory`.** Use a scratch worktree so the tests' side effects never land
  in the implementation checkout. The directory must be named `dark-factory` because
  `test_431_telemetry_isolation.sh` resolves `$(dirname "$REPO_ROOT")/dark-factory/scripts`.
  Never run these with the live token: `test_dispatch_ceiling.sh` reaches real `gh`. The
  command is the spec's credential-stripped command, plus the two enterprise-token unsets
  and `GIT_TERMINAL_PROMPT=0`. Those additions only strip more ambient credentials.
  ```bash
  rm -rf /tmp/seed426 && mkdir -p /tmp/seed426 /tmp/seed426-out
  git worktree add -q --detach /tmp/seed426/dark-factory HEAD
  ( cd /tmp/seed426/dark-factory
    while read -r f; do
      env -u GH_TOKEN -u GITHUB_TOKEN -u GH_ENTERPRISE_TOKEN -u GITHUB_ENTERPRISE_TOKEN \
        GIT_TERMINAL_PROMPT=0 GH_CONFIG_DIR="$(mktemp -d)" SCHEDULER_STATE_DIR="$(mktemp -d)" \
        timeout 120 bash "tests/$f" > "/tmp/seed426-out/$f.log" 2>&1
      rc=$?
      if [ "$rc" -eq 0 ]; then
        echo "$f :: passes standalone — wire in follow-up"
      elif [ "$rc" -eq 124 ]; then
        echo "$f :: fails: timeout after 120s"
      else
        first=$(grep -m1 -E 'FAIL|unbound variable|No such file|command not found|Error' "/tmp/seed426-out/$f.log" \
                | sed -e 's/^[[:space:]]*FAIL:[[:space:]]*//' -e 's/^FAIL:[[:space:]]*//' -e 's/^tests\/[a-z_0-9]*\.sh: //')
        [ -n "$first" ] || first="exit $rc: $(tail -1 "/tmp/seed426-out/$f.log")"
        echo "$f :: fails: $first"
      fi
    done < /tmp/unwired-426.txt ) | tee /tmp/seed426-reasons.txt
  git worktree remove --force /tmp/seed426/dark-factory && git worktree prune
  git status --short
  ```
  Expected: one `<file> :: <reason>` line per seeded file. The plan-time results (table above)
  were 16 `passes standalone — wire in follow-up` and 7 `fails: …`. Differences are fine; use
  the observed output. `git status --short` must show only `tests/test_ci_covers_shell_tests.py`
  (modified, from Step 2.1). If anything else appears, the run leaked into the checkout:
  `git checkout -- <path>` / `git clean -f <path>` it before continuing. The `grep -m1` is
  only a first pass: for **every** `fails:` line, open that file's
  `/tmp/seed426-out/<file>.log`. Confirm the extracted text is the first failing assertion or
  error, not a summary line such as `N failed` or an earlier warning, and correct it if it
  isn't (spec Req. 5). The reason should be one line; trim any absolute
  scratch path to the repo-relative part (e.g. `…/workflows/archon-dark-factory.yaml` →
  `workflows/archon-dark-factory.yaml`).

- [ ] **Step 2.5 — Fill `CI_EXEMPT_SHELL_TESTS`.** Write one `"<file>": "<reason>",` entry per
  line of `/tmp/seed426-reasons.txt`, sorted by filename, using double-quoted strings. If a
  reason itself contains `"`, switch that one entry to single quotes. With the plan-time
  results, the dict reads exactly:
  ```python
  CI_EXEMPT_SHELL_TESTS = {
      "test_159_regression.sh": "fails: set_board_status called (→ Blocked) — expected='1' got='0'",
      "test_431_telemetry_isolation.sh": "passes standalone — wire in follow-up",
      "test_close_preview_teardown.sh": "passes standalone — wire in follow-up",
      "test_codeindex_config.sh": "fails: implement depends on update-codeindex — pattern not found in workflows/archon-dark-factory.yaml",
      "test_config_deletion.sh": "passes standalone — wire in follow-up",
      "test_conformance_memory_write.sh": "passes standalone — wire in follow-up",
      "test_dispatch_ceiling.sh": "fails: L → always above — expected='0' got='1'",
      "test_entrypoint_fix_main.sh": "fails: fix-main route (896) not before smoke gate (679)",
      "test_epic_autopilot_config.sh": "passes standalone — wire in follow-up",
      "test_failing_checks_for_pr.sh": "passes standalone — wire in follow-up",
      "test_has_new_comment_after_report.sh": "passes standalone — wire in follow-up",
      "test_load_memory.sh": "passes standalone — wire in follow-up",
      "test_memory_integration.sh": "passes standalone — wire in follow-up",
      "test_memory_write_gate.sh": "passes standalone — wire in follow-up",
      "test_off_board_detection.sh": "passes standalone — wire in follow-up",
      "test_preview_differentiator.sh": "passes standalone — wire in follow-up",
      "test_restore_drill.sh": "fails: R2: no-backup exits 2 (expected 0); spec requires exit 0 on fresh-deploy skip",
      "test_scheduler.sh": "fails: line 411: STATUS_REFINED: unbound variable",
      "test_scheduler_autopilot_guard.sh": "passes standalone — wire in follow-up",
      "test_scheduler_ceiling.sh": "passes standalone — wire in follow-up",
      "test_scheduler_main_red_fixer.sh": "passes standalone — wire in follow-up",
      "test_scheduler_pagination.sh": "passes standalone — wire in follow-up",
      "test_session_window_gate.sh": "fails: 'Priority 1.5:' gate (351) does not precede breaker calls (33)",
  }
  ```
  Overwrite any entry whose Step 2.4 output differs. Add or remove keys so the set matches
  `/tmp/unwired-426.txt` exactly. No blanket "not yet triaged" reasons (spec Req. 5).

- [ ] **Step 2.6 — Verify all pass.** Run:
  ```bash
  python3 -m pytest tests/test_ci_covers_shell_tests.py -v
  ```
  Expected: `7 passed`.

- [ ] **Step 2.7 — Verify the guard bites in both directions on live data (no file edits).**
  Run:
  ```bash
  PYTHONPATH=scripts python3 -c "
  import sys; sys.path.insert(0, 'tests'); import test_ci_covers_shell_tests as t
  sh, ref = t._live()
  ex = dict(t.CI_EXEMPT_SHELL_TESTS); ex.pop(sorted(ex)[0])
  print('unaccounted:', sorted(t._violations(sh, ref, ex)[0]))
  ex = dict(t.CI_EXEMPT_SHELL_TESTS); ex['test_identity.sh'] = 'r'; ex['test_gone.sh'] = 'r'
  print('stale:', sorted(t._violations(sh, ref, ex)[1]))
  print('referenced:', len(ref), 'sh files:', len(sh))
  "
  ```
  Expected, with plan-time data:
  ```
  unaccounted: ['test_159_regression.sh']
  stale: ['test_gone.sh', 'test_identity.sh']
  referenced: 27 sh files: 50
  ```
  At implementation time, `referenced` + `len(CI_EXEMPT_SHELL_TESTS)` must equal `sh files`.

- [ ] **Step 2.8 — Commit.** Record the run conditions in the message:
  ```bash
  git add tests/test_ci_covers_shell_tests.py
  git commit -m "test(ci): guard every tests/*.sh is wired into ci.yml or exempted (#426)

  Seeds CI_EXEMPT_SHELL_TESTS with the N currently-unwired shell tests (derived from the
  issue's comm -23 reproduce command at HEAD). Each reason is from one serial run in a
  scratch worktree with GH_TOKEN/GITHUB_TOKEN unset and throwaway GH_CONFIG_DIR /
  SCHEDULER_STATE_DIR. Fixing or wiring the failing ones is follow-up work (option 3)."
  ```
  Replace `N` with the actual count before committing.

## Task 3: Known-gaps doc follow-through

**Files:** `docs/factory-target-boundary.md` (modify one bullet in `## Known gaps`)

- [ ] **Step 3.1 — Locate the bullet.** Run:
  ```bash
  grep -n -A1 'CI coverage' docs/factory-target-boundary.md
  ```
  Expected (line number may drift):
  ```
  309:- **CI coverage** — `tests/test_scheduler.sh` exists but is not in
  310-  `.github/workflows/ci.yml`'s bash-test list.
  ```

- [ ] **Step 3.2 — Replace the two lines.** Replace these lines:
  ```markdown
  - **CI coverage** — `tests/test_scheduler.sh` exists but is not in
    `.github/workflows/ci.yml`'s bash-test list.
  ```
  with:
  ```markdown
  - **CI coverage** — `.github/workflows/ci.yml` wires shell tests one `- run:` step at a
    time, so a `tests/*.sh` file runs nowhere until someone adds its step. Every shell test
    not yet wired in is listed, with its observed standalone result, in
    `tests/test_ci_covers_shell_tests.py::CI_EXEMPT_SHELL_TESTS` (#426); that guard test fails
    on any new unwired file and on any stale exemption. Wiring the listed tests in is open
    follow-up work.
  ```
  The bullet deliberately does not name individual files. The dict is the source of
  truth, and a named file would go stale silently once wired, because
  `test_every_doc_path_reference_exists` checks only that the path exists.

- [ ] **Step 3.3 — Verify the doc drift guard resolves the new citation.** Run:
  ```bash
  PYTHONPATH=scripts python3 -m pytest tests/test_factory_target_boundary_doc.py -v
  ```
  Expected: all pass, including `test_doc_citations_resolve_to_real_symbols`. The new
  `path::symbol` citation matches `CITATION_RE` and resolves to `CI_EXEMPT_SHELL_TESTS` in the
  new file.

- [ ] **Step 3.4 — Commit.**
  ```bash
  git add docs/factory-target-boundary.md
  git commit -m "docs(boundary): point CI-coverage known gap at CI_EXEMPT_SHELL_TESTS (#426)"
  ```
  Record this edit in the run's `doc-impact.md` if implement Phase 4 produces one. It doesn't
  if PR #542's docs-conformance gate isn't on `main` yet. Either way the doc edit is still
  made.

## Task 4: Full verification and scope check

**Files:** none (verification only)

- [ ] **Step 4.1 — Full pytest suite** (CI parity):
  ```bash
  PYTHONPATH=scripts python3 -m pytest tests/ -q
  ```
  Expected: no new failures relative to `origin/main`. The 7 new tests pass. If a failure
  shows up outside the two touched files, confirm it also fails on `origin/main` before
  treating it as pre-existing.

- [ ] **Step 4.2 — Scope check.** Run:
  ```bash
  git diff --name-only origin/main HEAD -- . ':!docs/superpowers/' ':!.archon/memory/'
  ```
  Expected, exactly:
  ```
  docs/factory-target-boundary.md
  tests/test_ci_covers_shell_tests.py
  ```
  In particular `.github/workflows/ci.yml` must **not** appear (spec Req. 6). Neither may
  any `tests/*.sh`, `gate_*`, `deploy/**` or `publish.yml` path. The two-dot form is used on
  purpose, per the `codebase-patterns.md` memory: three-dot reports false OOS hits.

- [ ] **Step 4.3 — Clean up scratch state.**
  ```bash
  rm -rf /tmp/seed426 /tmp/seed426-out /tmp/seed426-reasons.txt /tmp/unwired-426.txt
  git worktree prune && git status --short
  ```
  Expected: empty `git status` output (everything committed).

---

## Self-review

- `**Issue:** #426` line present under the title.
- No placeholders: every code block is the literal content to write. The only
  implementation-time values are the exemption reasons and the count `N` in the Task 2 commit
  message. The spec requires both to come from a real run (Req. 5), and plan-time values are
  given for cross-checking.
- Spec coverage:

  | Req. | Covered by |
  |---|---|
  | 1 | Task 2 `test_every_shell_test_is_wired_or_exempt` |
  | 2 | In-file dict |
  | 3 | `_referenced_in_ci` and `test_commented_out_or_disabled_step_is_not_referenced` |
  | 4 | `test_no_stale_exemptions` and two synthetic stale tests |
  | 5 | Steps 2.3–2.5 (re-derived list, per-file run, credential-stripped) |
  | 6 | No `ci.yml` edit, checked in Step 4.2 |
  | 7 | No wiring or fixing of shell tests |

  The doc follow-through is Task 3.
