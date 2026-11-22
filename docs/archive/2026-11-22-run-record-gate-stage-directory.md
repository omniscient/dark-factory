# Run record: read gate-stage artifacts from Archon's per-workflow-run directory — Implementation Plan

**Issue:** #573

Revised: 2026-11-22 (operator amendment after plan gate)

**Spec:** `docs/superpowers/specs/2026-11-22-run-record-gate-stage-directory-design.md`
(including the operator amendments A1-A7 in commit `8cad929`)

**Goal:** `run-record assemble` (`cmd_assemble`) reads `validation.md`, `conformance.md` and
`review.md` from the directory Archon's DAG nodes actually write them to:
`runs/<workflow run id>`, a sibling of the entrypoint's `--artifacts-dir`. Completed
`fix`/`continue` runs are then scored `delivered_clean`, `delivered_with_findings` or
`blocked` from real gate verdicts instead of always `produced_ungated`/1.0. Two more
changes come with it. The `push_result` fold reads the same resolved
`push-result.json` path that #447's `artifact_check` already uses. A scoring-neutral
`gate_artifact_lookup` field, plus a stderr warning, makes the remaining failure modes
visible.

**Architecture:** Everything changes inside `scripts/factory_core/run_record.py::cmd_assemble`
and its #447 helpers. `_archon_workflow_run_id()` is hoisted above the `artifact_names`
loop and runs once per call, giving `workflow_dir = artifacts_dir.parent / workflow_run_id`.
For the three `GATE_STAGE_NAMES` only, the loop keeps today's own-directory path as the
default and switches to the sibling copy when that copy exists and passes the freshness
guard. The guard is `_push_result_is_fresh`, renamed `_artifact_is_fresh` with the same
semantics. `conflict_resolution.md` keeps its own-directory lookup. The #447
`push-result.json` block reuses `workflow_dir` instead of recomputing the id. Its resolved
`artifact_push_path` is hoisted, so the fold reads it too. Intents other than
refine/plan still read `--artifacts-dir/push-result.json`. `_compute_outcome`,
`_parse_artifact_stage`, `POLICY_VERSION`, `entrypoint.sh` and the workflow YAML are not
touched.

**Tech Stack:** Python 3 (stdlib `pathlib`/`datetime`/`json`), pytest
(`tests/test_run_record.py`), `scripts/docs_refs_check.py` for doc citations.

**Verification note:** this plan's code and tests were dry-run before publishing, on a
scratch copy of `origin/main` at `4584159`. Every "expected output" below is the observed
result: per-task fail/pass counts and the final `3143 passed` full suite. `run_record.py`,
`tests/test_run_record.py`, `entrypoint.sh` and the cited sections of
`docs/run-lifecycle.md` are identical on this refine branch and on `origin/main`. Only
unrelated `scheduler.sh` citations in the docs differ. Counts assume a non-root user, as
in the factory container. As root, each "N passed" reads "N-2 passed, 2 skipped"; the
failed counts are unchanged.

---

## File Structure

| File | Change | Requirement |
|---|---|---|
| `scripts/factory_core/run_record.py` | Rename `_push_result_is_fresh` → `_artifact_is_fresh` and update the `_archon_workflow_run_id` docstring (Task 1). Hoist the workflow run id and add the sibling lookup for the three gate stages (Task 2). Add the `gate_artifact_lookup` field and the stderr warning (Task 3). Fold `push_result` from the resolved path (Task 4). | R1, R2, R2b, R3, R4 |
| `tests/test_run_record.py` | Generalize `_write_workflow_push_result` → `_write_workflow_artifact` and fix the comment that names the old helper (Task 1). Add new gate-stage, lookup and fold tests (Tasks 2-4). Flip one #447 assertion (Task 4). | R3, R5 |
| `docs/run-lifecycle.md` | §7 intro and step 2 prose; §8 rows for `push-result.json`, `validation.md`, `conformance.md` and `review.md` (Task 5) | R6 |
| `docs/configuration.md` | Re-map the `run_record.py:735` citation to the line Task 5 Step 1 re-derives (`:777` if nothing else landed first) (Task 5) | R6 |
| `docs/features.md` | Re-map `cmd_assemble` (`:690`) and the `issue-economics`/`backfill-economics` citations (`:1001`, `:1006`) to the lines Task 5 Step 1 re-derives (`:692`, `:1047`, `:1052` if nothing else landed first) (Task 5) | R6 (see note in Task 5) |

No other file is touched. `entrypoint.sh`, `workflows/archon-dark-factory.yaml`,
`post_mortem.py`, `_compute_outcome` and `_parse_artifact_stage` stay as they are, and so
does `POLICY_VERSION` (`"1.1"`; `test_policy_version_is_1_1` keeps passing). No `.sh` test
and no `ci.yml` edit is needed. All new tests are pytest, run by CI's
`python -m pytest tests/ -v`, and also run on the Windows host.

### Memory lessons applied

- `codebase-patterns.md` [AVOID]: "verify claimed artifacts actually exist". Every #447
  helper this plan reuses was re-checked in the live file before writing:
  `_archon_workflow_run_id`, `_push_result_is_fresh`, `_split_artifact_dirs`,
  `_forbid_lookup`, `_stub_lookup`, `_write_workflow_push_result` and
  `_ASSEMBLE_STARTED_EPOCH`. Nothing was taken from the spec's citations on trust.
- `codebase-patterns.md` [PATTERN] (#250): run the out-of-scope self-check in Task 6 with the
  two-dot `git diff origin/main HEAD`, not the three-dot form.
- `architecture.md` #182 lesson (cited by the spec): never trust a cited line number. Task 5
  re-derives every doc citation with `grep -n` after the code tasks land, not from this
  plan's numbers alone.

### Things that must NOT change (regression pins — never edit these tests)

- `test_assemble_non_refine_plan_intents_unchanged` (parametrized `close`/`new`/`deconflict`/`fix-main`)
- `test_assemble_non_refine_plan_ignores_workflow_run_dir`
- `test_assemble_builds_run_record`, `test_assemble_missing_artifacts_skipped`, and every
  other same-directory `validation.md`/`conformance.md`/`review.md` test
- `test_policy_version_is_1_1`

The only existing test whose **assertion** changes is
`test_assemble_refine_finds_push_result_in_workflow_run_dir` (Task 4, deliberate R2b flip).
Two other existing tests get only a mechanical change of helper call. They are
`test_assemble_refine_stale_workflow_push_result_is_failed` and
`test_assemble_refine_fresh_workflow_push_result_is_produced_ungated`, and they move to the
generalized helper in Task 1. Their assertions are untouched.

---

## Task 1: Rename the freshness guard and generalize the test helper (R3)

**Files:**
- Modify: `scripts/factory_core/run_record.py` (`_push_result_is_fresh` at `:652-667`, the
  `_archon_workflow_run_id` docstring at `:670-677`, and the call site at `:752`)
- Modify: `tests/test_run_record.py` (comment at `:1732-1733`, helper at `:1737-1741`, and
  its two callers at `:1750` and `:1771`)

- [ ] **Step 1: Generalize the test helper and fix the stale comment.**
  In `tests/test_run_record.py`, replace these lines:

  ```python
  # _AssembleArgs.started_at, as an epoch — the freshness check compares the sibling
  # push-result.json's mtime against it (_push_result_is_fresh, review fixup of #447).
  ```

  with:

  ```python
  # _AssembleArgs.started_at, as an epoch — the freshness check compares a sibling
  # artifact's mtime against it (_artifact_is_fresh; review fixup of #447, generalized by #573).
  ```

  Then replace the whole helper

  ```python
  def _write_workflow_push_result(workflow_dir, *, mtime):
      push = workflow_dir / "push-result.json"
      push.write_text(json.dumps({"node": "refine-push", "pushed": True}))
      os.utime(push, (mtime, mtime))
      return push
  ```

  with:

  ```python
  def _write_workflow_artifact(directory, filename, content, *, mtime=None):
      path = directory / filename
      path.write_text(content)
      if mtime is not None:
          os.utime(path, (mtime, mtime))
      return path
  ```

  Update its two callers. In `test_assemble_refine_stale_workflow_push_result_is_failed`,
  replace `    _write_workflow_push_result(workflow_dir, mtime=_ASSEMBLE_STARTED_EPOCH - 1)`
  with:

  ```python
      _write_workflow_artifact(
          workflow_dir, "push-result.json", json.dumps({"node": "refine-push", "pushed": True}),
          mtime=_ASSEMBLE_STARTED_EPOCH - 1,
      )
  ```

  In `test_assemble_refine_fresh_workflow_push_result_is_produced_ungated`, replace
  `    _write_workflow_push_result(workflow_dir, mtime=_ASSEMBLE_STARTED_EPOCH + offset)`
  with:

  ```python
      _write_workflow_artifact(
          workflow_dir, "push-result.json", json.dumps({"node": "refine-push", "pushed": True}),
          mtime=_ASSEMBLE_STARTED_EPOCH + offset,
      )
  ```

  Confirm no reference to the old helper is left:
  `grep -n "_write_workflow_push_result" tests/test_run_record.py`. Expected: no output.

- [ ] **Step 2: Write the failing tests.**
  Insert these directly after the new `_write_workflow_artifact` helper, before
  `def test_assemble_refine_stale_workflow_push_result_is_failed`:

  ```python
  @pytest.mark.parametrize("offset,expected", [(-1, False), (0, True), (1, True)])
  def test_artifact_is_fresh_compares_mtime_to_started_at(tmp_path, offset, expected):
      path = _write_workflow_artifact(
          tmp_path, "review.md", "STATUS: PASS\n", mtime=_ASSEMBLE_STARTED_EPOCH + offset
      )
      assert rr._artifact_is_fresh(path, "2026-06-12T04:00:00Z") is expected


  def test_artifact_is_fresh_fails_closed(tmp_path):
      path = _write_workflow_artifact(tmp_path, "review.md", "STATUS: PASS\n")
      assert rr._artifact_is_fresh(path, "not-a-timestamp") is False
      assert rr._artifact_is_fresh(path, "") is False
      assert rr._artifact_is_fresh(tmp_path / "absent.md", "2026-06-12T04:00:00Z") is False
      # #573 R3: renamed, not aliased — the helper is no longer push-result-specific.
      assert not hasattr(rr, "_push_result_is_fresh")
  ```

- [ ] **Step 3: Verify they fail.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q -k "artifact_is_fresh"
  ```

  Expected: `4 failed, 142 deselected`, each with
  `AttributeError: module 'factory_core.run_record' has no attribute '_artifact_is_fresh'`.

- [ ] **Step 4: Rename the helper.**
  In `scripts/factory_core/run_record.py`, replace the whole `_push_result_is_fresh`
  function with:

  ```python
  def _artifact_is_fresh(path: pathlib.Path, started_at: str) -> bool:
      """True if path's mtime is not older than this run's --started-at (#447 advisory, #573).

      `archon workflow cost --last` can hand back an earlier workflow run's id when the
      current run was never recorded there (see _archon_workflow_run_id) — without this
      check, that earlier run's push-result.json or gate-stage .md file in the sibling
      directory would be mistaken for this run's artifact. Unparseable started_at or an
      unreadable mtime fails closed (not fresh): an unverifiable timestamp must not be
      treated as evidence (a stale review.md could score this run blocked, a stale
      push-result.json a spurious produced_ungated/1.0).
      """
      try:
          started = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
          mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
      except (ValueError, TypeError, OSError):
          return False
      return mtime >= started
  ```

  The logic is byte-identical apart from the parameter name; that is R3's "no behavior
  change". In `_archon_workflow_run_id`'s docstring, replace

  ```
      that cmd_assemble receives as --artifacts-dir — so refine-push/plan-push-and-advance's
      push-result.json lives under this id, not under args.artifacts_dir (#447).
  ```

  with

  ```
      that cmd_assemble receives as --artifacts-dir — so refine-push/plan-push-and-advance's
      push-result.json (#447) and the validate/conformance/code-review nodes' gate-stage
      .md files (#573) live under this id, not under args.artifacts_dir.
  ```

  In `cmd_assemble`, change the one call `_push_result_is_fresh(` to `_artifact_is_fresh(`.
  Do not alias the old name: the new test asserts `_push_result_is_fresh` is gone.
  `grep -rn --include='*.py' "_push_result_is_fresh" scripts/ tests/` must print exactly
  one line: the `assert not hasattr(rr, "_push_result_is_fresh")` in
  `tests/test_run_record.py` added in Step 2.

- [ ] **Step 5: Verify they pass, plus the whole file.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q
  ```

  Expected: `146 passed`.

- [ ] **Step 6: Commit.**

  ```bash
  git add scripts/factory_core/run_record.py tests/test_run_record.py
  git commit -m "refactor(#573): rename _push_result_is_fresh to _artifact_is_fresh; generalize test helper"
  ```

---

## Task 2: Read the three gate stages from the workflow-run directory (R1, R2)

**Files:**
- Modify: `scripts/factory_core/run_record.py` (`cmd_assemble`: the top of the function
  through the `artifact_names` loop, the `archon_path =` line now right after the loop,
  and the #447 `if args.intent in ("refine", "plan"):` block)
- Modify: `tests/test_run_record.py` (new block inserted before `def _stored_refine_record(`)

- [ ] **Step 1: Write the tests.**
  Insert this block immediately after
  `test_assemble_non_refine_plan_ignores_workflow_run_dir`, before
  `def _stored_refine_record(`:

  ```python
  # ── gate-stage artifacts in archon's workflow-run directory (issue #573) ───

  _FRESH_EPOCH = _ASSEMBLE_STARTED_EPOCH + 1
  _STALE_EPOCH = _ASSEMBLE_STARTED_EPOCH - 1
  _CLEAN_GATES = {
      "validation.md": "STATUS: PASS\n",
      "conformance.md": "STATUS: PASS\nCYCLES: 0\n",
      "review.md": "STATUS: PASS\nBLOCKERS: 0\nADVISORY: 0\n",
  }
  _BLOCKED_REVIEW = "STATUS: BLOCKED\nBLOCKERS: 1\nADVISORY: 0\n"


  def _write_gates(directory, files, *, mtime=_FRESH_EPOCH):
      for filename, content in files.items():
          _write_workflow_artifact(directory, filename, content, mtime=mtime)


  def _assembled(args, entry_dir):
      rr.cmd_assemble(args)
      return json.loads((entry_dir / "run-record.json").read_text())


  @pytest.mark.parametrize("intent", ["fix", "continue"])
  def test_assemble_reads_gate_stages_from_workflow_run_dir(tmp_path, monkeypatch, intent):
      # Direct #573 regression: in production the three gate files exist ONLY in archon's
      # workflow-run directory. Before the fix, stages stayed [] and every completed
      # implement run scored produced_ungated/1.0.
      _forbid_lookup(monkeypatch)
      args, entry_dir, workflow_dir = _split_artifact_dirs(tmp_path, monkeypatch, intent=intent)
      _write_gates(workflow_dir, _CLEAN_GATES)
      rec = _assembled(args, entry_dir)

      assert [s["stage"] for s in rec["stages"]] == ["validation", "conformance", "review"]
      assert rec["artifacts"]["review"] == _CLEAN_GATES["review.md"]
      outcome = rec["harness_economics"]["outcome"]
      assert outcome["state"] == "delivered_clean"
      assert outcome["score"] == 1.0
      assert outcome["evidence"]["ungated"] is False
      assert "artifact_check" not in rec


  def test_assemble_workflow_run_dir_gate_findings_are_penalized(tmp_path, monkeypatch):
      _forbid_lookup(monkeypatch)
      args, entry_dir, workflow_dir = _split_artifact_dirs(tmp_path, monkeypatch, intent="fix")
      _write_gates(workflow_dir, {
          "validation.md": "STATUS: PASS\n",
          "conformance.md": "STATUS: PASS\nCYCLES: 2\n",
          "review.md": "STATUS: PASS\nBLOCKERS: 0\nADVISORY: 1\n",
      })
      outcome = _assembled(args, entry_dir)["harness_economics"]["outcome"]
      assert outcome["state"] == "delivered_with_findings"
      assert outcome["score"] == 0.75


  @pytest.mark.parametrize("filename,content", [
      ("review.md", _BLOCKED_REVIEW),
      ("conformance.md", "STATUS: BLOCKED\n"),
      ("validation.md", "STATUS: FAIL\n"),
  ], ids=["review", "conformance", "validation"])
  def test_assemble_blocking_verdict_in_workflow_run_dir_is_blocked(
      tmp_path, monkeypatch, filename, content
  ):
      _forbid_lookup(monkeypatch)
      args, entry_dir, workflow_dir = _split_artifact_dirs(tmp_path, monkeypatch, intent="fix")
      _write_gates(workflow_dir, {**_CLEAN_GATES, filename: content})
      outcome = _assembled(args, entry_dir)["harness_economics"]["outcome"]
      assert outcome["state"] == "blocked"
      assert outcome["score"] == 0.0


  def test_assemble_review_blocker_count_without_blocked_verdict_is_not_blocked(
      tmp_path, monkeypatch
  ):
      # The verdict blocks, not the count (_BLOCKING_VERDICTS).
      _forbid_lookup(monkeypatch)
      args, entry_dir, workflow_dir = _split_artifact_dirs(tmp_path, monkeypatch, intent="fix")
      _write_gates(workflow_dir, {**_CLEAN_GATES, "review.md": "STATUS: PASS\nBLOCKERS: 1\n"})
      outcome = _assembled(args, entry_dir)["harness_economics"]["outcome"]
      assert outcome["state"] == "delivered_clean"
      assert outcome["score"] == 1.0


  def test_assemble_stale_workflow_run_dir_gate_stages_are_ignored(tmp_path, monkeypatch):
      # `archon workflow cost --last` handed back an EARLIER workflow run's id: its
      # BLOCKED review.md predates this run's --started-at and must not score it blocked.
      _forbid_lookup(monkeypatch)
      args, entry_dir, workflow_dir = _split_artifact_dirs(tmp_path, monkeypatch, intent="fix")
      _write_gates(workflow_dir, {**_CLEAN_GATES, "review.md": _BLOCKED_REVIEW}, mtime=_STALE_EPOCH)
      rec = _assembled(args, entry_dir)

      assert rec["stages"] == []
      assert rec["artifacts"] == {}
      outcome = rec["harness_economics"]["outcome"]
      assert outcome["state"] == "produced_ungated"
      assert outcome["score"] == 1.0


  @pytest.mark.parametrize("started_at", ["", "not-a-timestamp", "2026-06-12 04:00:00"])
  def test_assemble_unparseable_started_at_ignores_workflow_run_dir_gate_stages(
      tmp_path, monkeypatch, started_at
  ):
      _forbid_lookup(monkeypatch)
      args, entry_dir, workflow_dir = _split_artifact_dirs(tmp_path, monkeypatch, intent="fix")
      _write_gates(workflow_dir, {**_CLEAN_GATES, "review.md": _BLOCKED_REVIEW})
      args.started_at = started_at
      rec = _assembled(args, entry_dir)

      assert rec["stages"] == []
      assert rec["harness_economics"]["outcome"]["state"] == "produced_ungated"


  def test_assemble_fresh_workflow_run_dir_copy_wins_over_own_dir(tmp_path, monkeypatch):
      _forbid_lookup(monkeypatch)
      args, entry_dir, workflow_dir = _split_artifact_dirs(tmp_path, monkeypatch, intent="fix")
      (entry_dir / "review.md").write_text(_BLOCKED_REVIEW)
      _write_gates(workflow_dir, {"review.md": "STATUS: PASS\nBLOCKERS: 0\nADVISORY: 0\n"})
      rec = _assembled(args, entry_dir)

      assert rec["stages"] == [{"stage": "review", "verdict": "PASS", "blockers": 0, "advisory": 0}]
      assert rec["artifacts"]["review"] == "STATUS: PASS\nBLOCKERS: 0\nADVISORY: 0\n"
      assert rec["harness_economics"]["outcome"]["state"] == "delivered_clean"


  def test_assemble_own_dir_gate_stage_used_when_workflow_run_dir_lacks_it(tmp_path, monkeypatch):
      _forbid_lookup(monkeypatch)
      args, entry_dir, _ = _split_artifact_dirs(tmp_path, monkeypatch, intent="fix")
      (entry_dir / "validation.md").write_text("STATUS: FAIL\n")
      rec = _assembled(args, entry_dir)

      assert rec["stages"] == [{"stage": "validation", "verdict": "FAIL"}]
      assert rec["harness_economics"]["outcome"]["state"] == "blocked"


  def test_assemble_failure_trap_reads_workflow_run_dir_gate_stages(tmp_path, monkeypatch):
      # entrypoint.sh's on_failure call site (--status failed): stages are now populated,
      # the state stays failed/0.0.
      _forbid_lookup(monkeypatch)
      args, entry_dir, workflow_dir = _split_artifact_dirs(tmp_path, monkeypatch, intent="fix")
      args.status = "failed"
      _write_gates(workflow_dir, {**_CLEAN_GATES, "review.md": _BLOCKED_REVIEW})
      rec = _assembled(args, entry_dir)

      assert [s["stage"] for s in rec["stages"]] == ["validation", "conformance", "review"]
      outcome = rec["harness_economics"]["outcome"]
      assert outcome["state"] == "failed"
      assert outcome["score"] == 0.0


  # Shape of commands/dark-factory-validate.md's free-form report: no STATUS: line.
  _FREEFORM_VALIDATION = (
      "# Validation Results\n\n"
      "- Unit tests: PASS\n"
      "- Endpoint tests: FAIL (2 failures)\n\n"
      "Final status: FAIL\n"
  )


  def test_assemble_freeform_validation_md_pins_substring_heuristic(tmp_path, monkeypatch):
      # Known parser gap (#573 spec, Assumptions): with no STATUS: line, the substring
      # heuristic in _parse_artifact_stage sees "PASS" first and reads this FAIL report as
      # PASS. Pinned as-is so the follow-up that fixes the heuristic changes it deliberately.
      _forbid_lookup(monkeypatch)
      args, entry_dir, workflow_dir = _split_artifact_dirs(tmp_path, monkeypatch, intent="fix")
      _write_gates(workflow_dir, {"validation.md": _FREEFORM_VALIDATION})
      rec = _assembled(args, entry_dir)

      assert rec["stages"] == [{"stage": "validation", "verdict": "PASS"}]
      assert rec["harness_economics"]["outcome"]["state"] == "delivered_clean"


  def test_assemble_conflict_resolution_in_workflow_run_dir_not_read(tmp_path, monkeypatch):
      # Known gap, left for a follow-up ticket: conflict_resolution.md keeps its own-dir
      # lookup. Pinned so widening it is a deliberate change.
      _forbid_lookup(monkeypatch)
      args, entry_dir, workflow_dir = _split_artifact_dirs(tmp_path, monkeypatch, intent="continue")
      _write_gates(workflow_dir, {"conflict_resolution.md": "CONFLICT_VERDICT=RESOLVED\n"})
      rec = _assembled(args, entry_dir)

      assert "conflict_resolution" not in rec["artifacts"]
      assert rec["stages"] == []


  def test_assemble_conflict_resolution_in_own_dir_still_read(tmp_path, monkeypatch):
      _forbid_lookup(monkeypatch)
      args, entry_dir, _ = _split_artifact_dirs(tmp_path, monkeypatch, intent="continue")
      (entry_dir / "conflict_resolution.md").write_text("CONFLICT_VERDICT=RESOLVED\n")
      rec = _assembled(args, entry_dir)

      assert rec["artifacts"]["conflict_resolution"] == "CONFLICT_VERDICT=RESOLVED\n"
      assert rec["stages"] == [{"stage": "conflict_resolution", "verdict": "RESOLVED"}]
  ```

  These rely on `_split_artifact_dirs`, which writes a cost JSON with `"run_id": "wf-run"`
  and creates `runs/entry-run` and `runs/wf-run`. They also use `_forbid_lookup`, which
  every new non-refine/plan test must call: the `needs-discussion` lookup stays
  refine/plan-only, per #447 R4. The intents are `fix` and `continue`, because
  `entrypoint.sh:103-104` defaults to `fix` and matches `continue`; the workflow's own `new`
  never reaches `--intent`.

- [ ] **Step 2: Verify the regression tests fail.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q
  ```

  Expected: `10 failed, 153 passed`. The 10 failures are the two
  `test_assemble_reads_gate_stages_from_workflow_run_dir` cases,
  `..._gate_findings_are_penalized`, the three `..._is_blocked` cases,
  `..._without_blocked_verdict_is_not_blocked`, `..._copy_wins_over_own_dir`,
  `..._failure_trap_reads_workflow_run_dir_gate_stages` and
  `..._freeform_validation_md_pins_substring_heuristic`. Seven new tests already pass,
  because they pin behavior that must stay the same: the stale and unparseable-`started_at`
  cases, the own-dir fallback, and both `conflict_resolution.md` cases. That is
  intended.

- [ ] **Step 3: Implement the hoist and the sibling lookup.**
  In `cmd_assemble`, replace the function's opening through the end of the
  `artifact_names` loop:

  ```python
  def cmd_assemble(args) -> None:
      artifacts_dir = pathlib.Path(args.artifacts_dir)
      out_file = pathlib.Path(args.out_file)

      stages = []
      artifacts: dict = {}
      artifact_names = ["validation", "conformance", "review", "conflict_resolution"]

      for name in artifact_names:
          md_path = artifacts_dir / f"{name}.md"
          if md_path.exists():
              content = md_path.read_text(encoding="utf-8")
              artifacts[name] = content
              stage = _parse_artifact_stage(name, content)
              if stage:
                  stages.append(stage)

      archon_path = pathlib.Path(args.archon_cost_json) if args.archon_cost_json else None
  ```

  with:

  ```python
  def cmd_assemble(args) -> None:
      artifacts_dir = pathlib.Path(args.artifacts_dir)
      out_file = pathlib.Path(args.out_file)

      # Resolved once, shared by the gate-stage loop and the push-result.json block (#573).
      archon_path = pathlib.Path(args.archon_cost_json) if args.archon_cost_json else None
      workflow_run_id = _archon_workflow_run_id(archon_path)
      workflow_dir = artifacts_dir.parent / workflow_run_id if workflow_run_id is not None else None

      stages = []
      artifacts: dict = {}
      artifact_names = ["validation", "conformance", "review", "conflict_resolution"]

      # #573: the validate, conformance and code-review DAG nodes write validation.md,
      # conformance.md and review.md into archon's per-workflow-run $ARTIFACTS_DIR, a
      # sibling of args.artifacts_dir (see _archon_workflow_run_id), never into the
      # entrypoint's own directory. Same resolution as #447's push-result.json below: the
      # own-dir path is the default and a fresh sibling copy wins. A stale sibling (an
      # earlier workflow run's leftover) is never read: its review.md could score this run
      # blocked. conflict_resolution.md is not a gate stage and keeps its own-dir lookup.
      for name in artifact_names:
          md_path = artifacts_dir / f"{name}.md"
          if name in GATE_STAGE_NAMES and workflow_dir is not None:
              sibling = workflow_dir / f"{name}.md"
              if sibling.exists() and _artifact_is_fresh(sibling, args.started_at):
                  md_path = sibling
          if md_path.exists():
              content = md_path.read_text(encoding="utf-8")
              artifacts[name] = content
              stage = _parse_artifact_stage(name, content)
              if stage:
                  stages.append(stage)

  ```

  The old `archon_path = ...` line after the loop is removed. It now sits at the top, so
  the next line after the loop is `    stderr_text = ""`, and `archon_path` is still in
  scope for `_parse_archon_cost_with_capture` below. The loop has no intent gate (R2:
  every intent).

  Then, in the #447 block, replace:

  ```python
      artifact_check = None
      if args.intent in ("refine", "plan"):
          artifact_push_path = artifacts_dir / "push-result.json"
          workflow_run_id = _archon_workflow_run_id(archon_path)
          if workflow_run_id is not None:
              workflow_push_path = artifacts_dir.parent / workflow_run_id / "push-result.json"
              if workflow_push_path.exists() and _artifact_is_fresh(
  ```

  with:

  ```python
      artifact_check = None
      artifact_push_path = artifacts_dir / "push-result.json"
      if args.intent in ("refine", "plan"):
          if workflow_dir is not None:
              workflow_push_path = workflow_dir / "push-result.json"
              if workflow_push_path.exists() and _artifact_is_fresh(
  ```

  This removes the duplicate `_archon_workflow_run_id()` call (R1). Hoisting
  `artifact_push_path` above the `if` does not change behavior here. It only gives Task 4's
  fold a defined name for every intent. `artifact_check["workflow_run_id"]` still reads the
  same `workflow_run_id` variable, now the hoisted one. Check that exactly one call is left:
  `grep -c "_archon_workflow_run_id(archon_path)" scripts/factory_core/run_record.py`
  → `1`.

- [ ] **Step 4: Verify they pass.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q
  ```

  Expected: `163 passed`.

- [ ] **Step 5: Commit.**

  ```bash
  git add scripts/factory_core/run_record.py tests/test_run_record.py
  git commit -m "fix(#573): read gate-stage artifacts from archon's per-workflow-run directory"
  ```

---

## Task 3: Scoring-neutral `gate_artifact_lookup` field and silent-path warning (R4)

**Files:**
- Modify: `scripts/factory_core/run_record.py` (`cmd_assemble`: the gate-stage loop, the
  new warning right after it, and the `run_record` field next to `artifact_check`)
- Modify: `tests/test_run_record.py` (block appended after Task 2's, still before
  `def _stored_refine_record(`)

- [ ] **Step 1: Write the failing tests.**
  Insert directly after Task 2's last test
  (`test_assemble_conflict_resolution_in_own_dir_still_read`):

  ```python
  _NO_GATE_WARNING = "WARNING: run-record: no gate-stage artifacts found"


  def test_assemble_gate_artifact_lookup_records_workflow_run_dir_hits(
      tmp_path, monkeypatch, capsys
  ):
      _forbid_lookup(monkeypatch)
      args, entry_dir, workflow_dir = _split_artifact_dirs(tmp_path, monkeypatch, intent="fix")
      _write_gates(workflow_dir, _CLEAN_GATES)
      rec = _assembled(args, entry_dir)

      assert rec["gate_artifact_lookup"] == {
          "workflow_run_id": "wf-run",
          "workflow_dir_exists": True,
          "found": ["validation", "conformance", "review"],
          "stale": [],
          "own_dir": [],
      }
      # Scoring-neutral: never part of the outcome evidence.
      assert "gate_artifact_lookup" not in rec["harness_economics"]["outcome"]["evidence"]
      assert _NO_GATE_WARNING not in capsys.readouterr().err


  def test_assemble_gate_artifact_lookup_sibling_wins_is_not_own_dir(tmp_path, monkeypatch):
      _forbid_lookup(monkeypatch)
      args, entry_dir, workflow_dir = _split_artifact_dirs(tmp_path, monkeypatch, intent="fix")
      (entry_dir / "review.md").write_text(_BLOCKED_REVIEW)
      (entry_dir / "validation.md").write_text("STATUS: PASS\n")
      _write_gates(workflow_dir, {"review.md": "STATUS: PASS\n"})
      lookup = _assembled(args, entry_dir)["gate_artifact_lookup"]

      assert lookup["found"] == ["review"]
      assert lookup["own_dir"] == ["validation"]


  def test_assemble_stale_gate_stages_are_listed_and_warned(tmp_path, monkeypatch, capsys):
      _forbid_lookup(monkeypatch)
      args, entry_dir, workflow_dir = _split_artifact_dirs(tmp_path, monkeypatch, intent="fix")
      _write_gates(workflow_dir, _CLEAN_GATES, mtime=_STALE_EPOCH)
      rec = _assembled(args, entry_dir)

      assert rec["gate_artifact_lookup"] == {
          "workflow_run_id": "wf-run",
          "workflow_dir_exists": True,
          "found": [],
          "stale": ["validation", "conformance", "review"],
          "own_dir": [],
      }
      err = capsys.readouterr().err
      assert f"{_NO_GATE_WARNING} (workflow_run_id=wf-run, workflow_dir_exists=True)" in err


  def test_assemble_absent_workflow_run_dir_is_recorded_and_warned(tmp_path, monkeypatch, capsys):
      # The id resolves but runs/<id>/ does not exist: the silent path R4 exists to surface.
      _forbid_lookup(monkeypatch)
      args, entry_dir, workflow_dir = _split_artifact_dirs(tmp_path, monkeypatch, intent="continue")
      workflow_dir.rmdir()
      rec = _assembled(args, entry_dir)

      assert rec["gate_artifact_lookup"] == {
          "workflow_run_id": "wf-run",
          "workflow_dir_exists": False,
          "found": [],
          "stale": [],
          "own_dir": [],
      }
      assert rec["harness_economics"]["outcome"]["state"] == "produced_ungated"
      err = capsys.readouterr().err
      assert f"{_NO_GATE_WARNING} (workflow_run_id=wf-run, workflow_dir_exists=False)" in err


  def test_assemble_no_cost_json_is_recorded_and_warned(tmp_path, monkeypatch, capsys):
      _forbid_lookup(monkeypatch)
      args = _artifact_assemble_args(tmp_path, monkeypatch, intent="fix")
      rr.cmd_assemble(args)

      rec = json.loads((tmp_path / "run-record.json").read_text())
      assert rec["gate_artifact_lookup"] == {
          "workflow_run_id": None,
          "workflow_dir_exists": False,
          "found": [],
          "stale": [],
          "own_dir": [],
      }
      err = capsys.readouterr().err
      assert f"{_NO_GATE_WARNING} (workflow_run_id=None, workflow_dir_exists=False)" in err


  def test_assemble_gate_artifact_lookup_records_own_dir_reads(tmp_path, monkeypatch, capsys):
      _forbid_lookup(monkeypatch)
      for filename, content in _CLEAN_GATES.items():
          (tmp_path / filename).write_text(content)
      args = _artifact_assemble_args(tmp_path, monkeypatch, intent="fix")
      rr.cmd_assemble(args)

      rec = json.loads((tmp_path / "run-record.json").read_text())
      assert rec["gate_artifact_lookup"]["own_dir"] == ["validation", "conformance", "review"]
      assert rec["gate_artifact_lookup"]["found"] == []
      assert _NO_GATE_WARNING not in capsys.readouterr().err


  @pytest.mark.parametrize("intent,status", [
      ("close", "completed"),
      ("new", "completed"),
      ("deconflict", "completed"),
      ("fix", "failed"),
      ("continue", "failed"),
  ])
  def test_assemble_no_gate_warning_only_for_completed_fix_continue(
      tmp_path, monkeypatch, capsys, intent, status
  ):
      _forbid_lookup(monkeypatch)
      args = _artifact_assemble_args(tmp_path, monkeypatch, intent=intent, status=status)
      rr.cmd_assemble(args)

      rec = json.loads((tmp_path / "run-record.json").read_text())
      assert "gate_artifact_lookup" in rec
      assert _NO_GATE_WARNING not in capsys.readouterr().err
  ```

- [ ] **Step 2: Verify they fail.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q
  ```

  Expected: `11 failed, 163 passed`. Every new test fails with `KeyError: 'gate_artifact_lookup'`
  or a missing-key assertion.

- [ ] **Step 3: Implement the field, the bookkeeping and the warning.**
  In `cmd_assemble`, replace Task 2's loop

  ```python
      for name in artifact_names:
          md_path = artifacts_dir / f"{name}.md"
          if name in GATE_STAGE_NAMES and workflow_dir is not None:
              sibling = workflow_dir / f"{name}.md"
              if sibling.exists() and _artifact_is_fresh(sibling, args.started_at):
                  md_path = sibling
          if md_path.exists():
              content = md_path.read_text(encoding="utf-8")
              artifacts[name] = content
              stage = _parse_artifact_stage(name, content)
              if stage:
                  stages.append(stage)

  ```

  with the following. The #573 comment block above the loop stays as it is.

  ```python
      gate_artifact_lookup = {
          "workflow_run_id": workflow_run_id,
          "workflow_dir_exists": workflow_dir is not None and workflow_dir.is_dir(),
          "found": [],
          "stale": [],
          "own_dir": [],
      }
      for name in artifact_names:
          md_path = artifacts_dir / f"{name}.md"
          source = "own_dir"
          if name in GATE_STAGE_NAMES and workflow_dir is not None:
              sibling = workflow_dir / f"{name}.md"
              if sibling.exists():
                  if _artifact_is_fresh(sibling, args.started_at):
                      md_path, source = sibling, "found"
                  else:
                      gate_artifact_lookup["stale"].append(name)
          if md_path.exists():
              if name in GATE_STAGE_NAMES:
                  gate_artifact_lookup[source].append(name)
              content = md_path.read_text(encoding="utf-8")
              artifacts[name] = content
              stage = _parse_artifact_stage(name, content)
              if stage:
                  stages.append(stage)

      if (
          args.status == "completed"
          and args.intent in ("fix", "continue")
          and not any(s["stage"] in GATE_STAGE_NAMES for s in stages)
      ):
          print(
              "WARNING: run-record: no gate-stage artifacts found "
              f"(workflow_run_id={workflow_run_id}, "
              f"workflow_dir_exists={gate_artifact_lookup['workflow_dir_exists']})",
              file=sys.stderr,
          )

  ```

  The lists cover only the three `GATE_STAGE_NAMES`; `conflict_resolution` is never
  recorded (R4). A name can appear in both `stale` and `own_dir`: a stale sibling plus an
  own-dir copy that was read. Then, right after
  `        run_record["artifact_check"] = artifact_check  # replayed by backfill-economics`,
  add these lines at the function's indentation, not inside the `if`:

  ```python
      # Scoring-neutral lookup evidence (#573): never passed to _compute_outcome. It is what
      # separates post-fix records from the historical blind-scored ones (no policy bump).
      run_record["gate_artifact_lookup"] = gate_artifact_lookup
  ```

  Do not pass it to `_compute_harness_economics`/`_compute_outcome`. It must not show up
  in `outcome.evidence`, and a test pins that. `_backfill_run_economics` stays as it is:
  it recomputes from the stored `stages`, which now carry the corrected verdicts.

- [ ] **Step 4: Verify they pass.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q
  ```

  Expected: `174 passed`.

- [ ] **Step 5: Commit.**

  ```bash
  git add scripts/factory_core/run_record.py tests/test_run_record.py
  git commit -m "feat(#573): record scoring-neutral gate_artifact_lookup and warn on an ungated implement run"
  ```

---

## Task 4: Fold `push_result` from the resolved path (R2b)

**Files:**
- Modify: `scripts/factory_core/run_record.py` (the #473 `push-result.json` fold near the
  end of `cmd_assemble`)
- Modify: `tests/test_run_record.py`
  (`test_assemble_refine_finds_push_result_in_workflow_run_dir` at `:1702-1714`, plus a
  block appended after Task 3's)

- [ ] **Step 1: Flip the #447 assertion and add the fold tests.**
  In `test_assemble_refine_finds_push_result_in_workflow_run_dir`, replace

  ```python
      # The #473 push_result fold is deliberately unchanged (Design decision 8): it still
      # reads only --artifacts-dir.
      assert "push_result" not in rec
  ```

  with

  ```python
      # #573 R2b: the push_result fold reads the same resolved path as artifact_check.
      assert rec["push_result"] == {"node": "refine-push", "pushed": True}
  ```

  Then insert directly after Task 3's last test
  (`test_assemble_no_gate_warning_only_for_completed_fix_continue`):

  ```python
  def test_assemble_plan_folds_fresh_failed_push_result_from_workflow_run_dir(
      tmp_path, monkeypatch
  ):
      # #573 R2b: a fresh sibling {"pushed": false} is folded, so cost_report.py's
      # **Push:** FAILED line can fire for a real run.
      _forbid_lookup(monkeypatch)
      args, entry_dir, workflow_dir = _split_artifact_dirs(tmp_path, monkeypatch, intent="plan")
      _write_workflow_artifact(
          workflow_dir, "push-result.json", json.dumps({"pushed": False, "error": "x"}),
          mtime=_FRESH_EPOCH,
      )
      rec = _assembled(args, entry_dir)

      assert rec["artifact_check"] == {"artifact_present": True}
      assert rec["push_result"] == {"pushed": False, "error": "x"}


  def test_assemble_refine_stale_workflow_push_result_is_not_folded(tmp_path, monkeypatch):
      _stub_lookup(monkeypatch, (False, True))
      args, entry_dir, workflow_dir = _split_artifact_dirs(tmp_path, monkeypatch, intent="refine")
      _write_workflow_artifact(
          workflow_dir, "push-result.json", json.dumps({"node": "refine-push", "pushed": True}),
          mtime=_STALE_EPOCH,
      )
      rec = _assembled(args, entry_dir)

      assert rec["artifact_check"]["artifact_present"] is False
      assert "push_result" not in rec


  def test_assemble_refine_malformed_workflow_push_result_is_not_folded(tmp_path, monkeypatch):
      _forbid_lookup(monkeypatch)
      args, entry_dir, workflow_dir = _split_artifact_dirs(tmp_path, monkeypatch, intent="refine")
      _write_workflow_artifact(workflow_dir, "push-result.json", "not valid json {{{", mtime=_FRESH_EPOCH)
      rec = _assembled(args, entry_dir)

      assert rec["artifact_check"] == {"artifact_present": True}
      assert "push_result" not in rec
  ```

  `test_assemble_non_refine_plan_ignores_workflow_run_dir` (a `close` run with a sibling
  `push-result.json`, asserting `"push_result" not in rec`) is **not** edited. It pins
  that intents other than refine/plan keep the own-dir fold.

- [ ] **Step 2: Verify they fail.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q
  ```

  Expected: `2 failed, 175 passed`. The failures are
  `test_assemble_refine_finds_push_result_in_workflow_run_dir` and
  `test_assemble_plan_folds_fresh_failed_push_result_from_workflow_run_dir`, both with a
  `KeyError: 'push_result'`. The stale and malformed cases pass already; they are pins.

- [ ] **Step 3: Implement the fold change.**
  Replace

  ```python
      # fold as memory-trace.json — these are plain DAG bash steps, not verdict gates.
      push_path = artifacts_dir / "push-result.json"
      if push_path.exists():
          try:
              run_record["push_result"] = json.loads(push_path.read_text(encoding="utf-8"))
  ```

  with

  ```python
      # fold as memory-trace.json — these are plain DAG bash steps, not verdict gates.
      # Folded from artifact_push_path: for refine/plan, the fresh sibling copy in archon's
      # workflow-run directory when the artifact_check block above found one (#573); every
      # other intent reads --artifacts-dir only (no implement-path node writes the file).
      if artifact_push_path.exists():
          try:
              run_record["push_result"] = json.loads(artifact_push_path.read_text(encoding="utf-8"))
  ```

  The `except Exception: pass` stays, so malformed JSON still yields no `push_result` key.
  `artifact_push_path` was defined for every intent in Task 2 Step 3. The intent gate on
  `push-result.json` (`if args.intent in ("refine", "plan")`) is not widened, because no
  implement-path node writes the file.

- [ ] **Step 4: Verify they pass.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q
  ```

  Expected: `177 passed`.

- [ ] **Step 5: Commit.**

  ```bash
  git add scripts/factory_core/run_record.py tests/test_run_record.py
  git commit -m "fix(#573): fold push_result from the resolved workflow-run push-result.json (R2b)"
  ```

---

## Task 5: Correct the docs and re-map the shifted citations (R6)

**Files:**
- Modify: `docs/run-lifecycle.md` (§7 at `:923-936`, §8 table rows at `:977`, `:985`,
  `:988` and `:994` on `origin/main`)
- Modify: `docs/configuration.md` (`:555`)
- Modify: `docs/features.md` (`:139`)

Note on `docs/features.md`: R6 asks to "re-map the `run_record.py` line citations that this
change shifts" and, as amended after the plan gate, names `docs/features.md:139` next to
the other two docs. That line carries three
`run_record.py` citations that Tasks 1-4 shift: `cmd_assemble` `:690`, and the
`issue-economics`/`backfill-economics` subparsers at `:1001` and `:1006`.
`docs_refs_check.py` only checks ranges and would not flag them. Leaving them stale would
break the doc-accuracy rule R6 itself states, so they are re-mapped here. This is a
citation-only change. `features.md:139`'s other citations (`cmd_record` `:124`,
`emit_health_event` `:173`) and `configuration.md:501`'s `run_record.py:28,494` sit
above the edited region and do not move.

- [ ] **Step 1: Re-derive the post-change line numbers (do not trust the values below blindly).**

  ```bash
  grep -n '^def cmd_assemble\|^def _build_issue_economics\|adapter.get(args.clone_dir, "loops")\|"issue-economics", help\|"backfill-economics", help' scripts/factory_core/run_record.py
  grep -n 'run-record assemble' entrypoint.sh
  ```

  Expected, after Tasks 1-4 exactly as written:

  ```
  692:def cmd_assemble(args) -> None:
  777:        loops = adapter.get(args.clone_dir, "loops") or []
  897:def _build_issue_economics(issue_number: int, *, ledger_path: pathlib.Path, artifacts_root: pathlib.Path) -> dict:
  1047:    ie = sub.add_parser("issue-economics", help="Read-only cross-run rollup for an issue")
  1052:    be = sub.add_parser("backfill-economics", help="Degrade-only historical harness_economics recompute")
  559:    python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record assemble \
  1090:python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record assemble \
  ```

  `cmd_assemble` therefore spans `692-894`: its last line is the durable-write `print`,
  three lines above `_build_issue_economics`. On the failure path, the cost capture and
  assemble call span `entrypoint.sh:552-571`: from `local FAIL_COST_JSON ...` to the
  assemble call's closing `|| true`.

  Every line number in Steps 2-4 below is the expected value on `origin/main` `4584159`,
  not a constant. Substitute Step 1's output wherever it differs, for example because
  #453 landed first and also edits `cmd_assemble` and `entrypoint.sh`'s assemble calls
  (operator note). In that case the "replace X with Y" source strings in Steps 2 and 4
  will also differ: locate each citation by its surrounding prose, not by its old number.

- [ ] **Step 2: `docs/run-lifecycle.md` §7.**
  Replace the intro line

  ```
  Only when `archon workflow run` exits 0 (`entrypoint.sh:1080-1107`):
  ```

  with

  ```
  When `archon workflow run` exits 0 (`entrypoint.sh:1080-1107`), the steps below run in
  order. Steps 1 and 2 also run from the `on_failure` trap (`entrypoint.sh:552-571`), with
  `--status failed`, which scores the run `failed`:
  ```

  Replace step 2's body (the seven lines from `(`scripts/factory_core/run_record.py:690-848`)`
  through `artifact-less run `failed` or `clean_abort`. Failure is ignored.`) so that step 2
  reads in full:

  ```
  2. `factory_core/cli.py run-record assemble` builds `run-record.json`
     (`scripts/factory_core/run_record.py:692-894`): it parses `validation.md`,
     `conformance.md`, `review.md` and `conflict_resolution.md` into stage verdicts. The
     validate, conformance and code-review nodes write the first three into Archon's
     per-workflow-run directory, `runs/<workflow run id>` (the id is the cost JSON's
     `run_id`), a sibling of the entrypoint's `ARTIFACTS_DIR`. Assemble reads each from
     there when the copy is not older than the run's start, else from the entrypoint's
     own directory (#573); `conflict_resolution.md` is read from the entrypoint's directory
     only. The scoring-neutral `gate_artifact_lookup` field records the workflow run id,
     whether its directory exists, and which of the three were read from it (`found`),
     present but too old (`stale`) or read from the entrypoint's directory (`own_dir`). A
     completed `fix`/`continue` run with no gate stage prints
     `WARNING: run-record: no gate-stage artifacts found` to stderr. Assemble also adds
     per-node token and cost figures from the Archon cost JSON, the adapter's `loops`,
     `memory-trace.json` and `push-result.json` when present, and appends one line per
     stage to `runs.jsonl`. For `refine`/`plan` it also records `artifact_check`
     (`push-result.json` presence, resolved the same two-directory way, else a
     `needs-discussion` label lookup), folds `push_result` from that resolved path, and
     scores an artifact-less run `failed` or `clean_abort`. Failure is ignored.
  ```

- [ ] **Step 3: `docs/run-lifecycle.md` §8 table.** For the spec's open question, this plan
  annotates the rows inline rather than adding a footnote. Make four row edits:

  | Row (`File` column) | Replace `Read by` cell | With |
  |---|---|---|
  | `push-result.json` | `` `run-record assemble` `` | `` `run-record assemble` (from the workflow-run directory, see [section 7](#7-after-the-workflow)) `` |
  | `validation.md` | `` `run-record assemble` `` | `` `run-record assemble` (from the workflow-run directory, see [section 7](#7-after-the-workflow)) `` |
  | `conformance.md` | `` `conformance-gate`, `report`, `run-record assemble`, post-mortem `` | `` `conformance-gate`, `report`, `run-record assemble` (from the workflow-run directory, see [section 7](#7-after-the-workflow)), post-mortem `` |
  | `review.md` | `` `review-gate`, `report`, `run-record assemble`, post-mortem `` | `` `review-gate`, `report`, `run-record assemble` (from the workflow-run directory, see [section 7](#7-after-the-workflow)), post-mortem `` |

  The anchor `#7-after-the-workflow` already exists in the doc's contents list (`:42`).
  Leave the `conflict_resolution.md` row unchanged; its own-directory reading is
  accurate.

- [ ] **Step 4: Re-map the other citations.**
  - `docs/configuration.md:555`: `` `handoff.py:437`, `run_record.py:735` `` → `` `handoff.py:437`, `run_record.py:777` ``
  - `docs/features.md:139`: `` `cmd_assemble` (`:690`) `` → `` `cmd_assemble` (`:692`) ``, and
    `` (`run_record.py:1001`, `:1006`) `` → `` (`run_record.py:1047`, `:1052`) ``

  Then confirm that no stale `run_record.py` citation is left in the reference docs:

  ```bash
  grep -n "run_record.py:690\|run_record.py:735\|run_record.py:1001\|(\`:690\`)\|run_record.py:690-848" docs/run-lifecycle.md docs/configuration.md docs/features.md
  ```

  Expected: no output.

  Then confirm each new citation lands on the intended line, using the numbers you wrote:
  `sed -n '<cmd_assemble line>p;<loops line>p;<issue-economics line>p;<backfill-economics line>p' scripts/factory_core/run_record.py`
  must print the `def cmd_assemble`, `adapter.get(args.clone_dir, "loops")`,
  `"issue-economics"` and `"backfill-economics"` lines, in that order.

- [ ] **Step 5: Run the CI doc reference check.**

  ```bash
  python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
  ```

  Expected: `Docs reference check passed for 4 doc(s).`

- [ ] **Step 6: Commit.**

  ```bash
  git add docs/run-lifecycle.md docs/configuration.md docs/features.md
  git commit -m "docs(#573): document the two-directory gate-stage lookup; re-map shifted run_record.py citations"
  ```

---

## Task 6: Full verification and scope self-check

**Files:** none modified.

- [ ] **Step 1: Run the full pytest suite, as CI does.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/ -q
  ```

  Expected: no failure that does not also fail on unmodified `origin/main` in the same
  environment (3143 tests collected; the scratch run in a git checkout as non-root gave
  `3143 passed`). Run `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q` on
  its own: expected `177 passed`.

  Then run the CI shell tests that call or guard `run-record assemble`. This is CI parity:
  `ci.yml` lists them, and `test_entrypoint_cost_report_regression.sh` runs a real
  completed `fix` assemble that will now print the new stderr `WARNING`. That test does
  not assert on assemble's stderr.

  ```bash
  bash tests/test_entrypoint_cost_report_regression.sh
  bash tests/test_run_record_hermetic.sh
  bash tests/test_cost_report_harness_economics.sh
  ```

  Expected: each exits 0 and reports no `FAIL:` lines. This was verified on the scratch
  copy. `test_entrypoint_cost_report_regression.sh` sets
  `CLONE_DIR="$(dirname "$REPO_ROOT")"`, so it only passes from a checkout whose directory
  is named `dark-factory`, as the factory clone's is. From a differently named copy it
  reports 3 `FAIL:` lines on unmodified `main` too.

- [ ] **Step 2: Confirm the regression pins are byte-identical to `origin/main`.**

  ```bash
  python - <<'PY'
  import ast, subprocess
  old = subprocess.run(["git", "show", "origin/main:tests/test_run_record.py"],
                       capture_output=True, text=True, check=True).stdout
  new = open("tests/test_run_record.py", encoding="utf-8").read()
  def funcs(src):
      lines = src.splitlines()
      return {n.name: lines[(n.decorator_list[0].lineno if n.decorator_list else n.lineno) - 1:n.end_lineno]
              for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)}
  o, n = funcs(old), funcs(new)
  for t in ["test_assemble_non_refine_plan_intents_unchanged",
            "test_assemble_non_refine_plan_ignores_workflow_run_dir",
            "test_assemble_builds_run_record",
            "test_assemble_missing_artifacts_skipped",
            "test_policy_version_is_1_1"]:
      print(("unchanged: " if o[t] == n[t] else "CHANGED: ") + t)
  PY
  grep -n 'POLICY_VERSION = ' scripts/factory_core/run_record.py
  ```

  Expected: five `unchanged:` lines and `30:POLICY_VERSION = "1.1"`.

- [ ] **Step 3: Scope self-check (two-dot diff, per the #250 memory lesson).**

  ```bash
  git diff --name-only origin/main HEAD
  ```

  Expected: exactly the following files. The spec and plan are carried over from the
  refine branch by `transfer_refine_artifacts.sh`:

  ```
  docs/configuration.md
  docs/features.md
  docs/run-lifecycle.md
  docs/superpowers/plans/2026-11-22-run-record-gate-stage-directory.md
  docs/superpowers/specs/2026-11-22-run-record-gate-stage-directory-design.md
  scripts/factory_core/run_record.py
  tests/test_run_record.py
  ```

  `entrypoint.sh`, `workflows/`, `scripts/factory_core/post_mortem.py`, `config/` and
  `deploy/` must not appear.

- [ ] **Step 4: Owner-facing note for the PR body** (no file change). State three things:
  - Completed implement-run scores will now drop below 1.0, because conformance-cycle and
    review-advisory penalties start applying, and `POLICY_VERSION` stays `1.1`.
  - `gate_artifact_lookup` is what separates post-fix records from the historical
    blind-scored ones.
  - Two gaps are left for follow-up tickets: `conflict_resolution.md` on `continue` runs,
    and the free-form `validation.md` heuristic.

  If #453 has merged first, merge `origin/main` before pushing. Both tickets edit
  `cmd_assemble` (operator note), so re-run Task 5 Step 1 and Task 6 Steps 1-2 after the
  merge.
