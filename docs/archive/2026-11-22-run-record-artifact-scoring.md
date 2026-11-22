# Truthful outcome scoring for artifact-less `refine`/`plan` runs — Implementation Plan

**Issue:** #447

**Revised:** 2026-11-22 (operator amendment after plan gate review)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A completed `refine`/`plan` run that committed no spec/plan scores `failed`/0.0. If `needs-discussion` is already on the issue, it scores the new `clean_abort`/0.0 instead. Today both score `produced_ungated`/1.0. No other intent's scoring changes.

**Architecture:** `_compute_outcome` and `_compute_harness_economics` each get one new keyword-only parameter, `artifact_check`, which defaults to `None` (today's behaviour). `cmd_assemble` builds `artifact_check` for refine/plan runs only. It first checks whether `push-result.json` exists. It looks in the workflow run's artifacts directory first, which it resolves from the archon cost JSON's `run_id`, and falls back to `--artifacts-dir` (Design decision 9). If the file is missing and the run completed, a new helper `_check_needs_discussion` makes one live label lookup that has a timeout and fails closed. The DAG nodes already run the same `providers/cli.py tracker get --fields labels` call. `cmd_assemble` saves the input as the record's top-level `artifact_check` key, and `_backfill_run_economics` replays it from there so a backfill never changes the verdict. `POLICY_VERSION` goes from `"1.0"` to `"1.1"`.

**Tech Stack:** Python 3 stdlib (`subprocess`, `json`, `pathlib`), pytest with `monkeypatch`/`tmp_path`.

**Spec:** `docs/superpowers/specs/2026-11-22-run-record-artifact-scoring-design.md`

---

## Design decisions pinned by this plan

The spec left these to the plan phase ("Open Questions"):

1. **One name everywhere: `artifact_check`.** The spec's sketch calls the kwarg `refine_plan_halt`. This plan uses `artifact_check` for the kwarg, the evidence key, and the top-level record key, because the spec already names the evidence and record keys `artifact_check`. With one name, backfill can pass `record.get("artifact_check")` straight through.
2. **Dict shapes** (also the values of `evidence["artifact_check"]` and `run_record["artifact_check"]`):
   - artifact found: `{"artifact_present": True}`
   - no artifact: `{"artifact_present": False, "needs_discussion": <bool>, "label_lookup": "ok" | "error", "workflow_run_id": <str | None>}`.
     - `needs_discussion` is always `False` when `label_lookup == "error"` (fail closed).
     - `workflow_run_id` records which workflow-run directory was searched for `push-result.json` (decision 9). `None` means the directory could not be resolved and only the entrypoint's directory was checked.
3. **`evidence["artifact_check"]` is only added when the kwarg is not `None`.** Every non-refine/plan run, and every caller that omits the kwarg, gets the same `outcome` dict as `main`. R1 and Safety Invariant 4 require this. Task 1 pins it with an exact-dict test. The one field that changes for every intent is `harness_economics.policy_version` (`"1.0"` → `"1.1"`), which spec R2 requires. Read the "byte-identical" requirement as covering the outcome and scoring fields. The version stamp is a marker that applies to every record.
4. **Lookup helper signature: `_check_needs_discussion(issue) -> (needs_discussion, lookup_ok)`.** It runs `providers/cli.py` from the module's own directory (`pathlib.Path(__file__).resolve().parent / "providers" / "cli.py"`) using `sys.executable`. `entrypoint.sh` runs `$CLONE_DIR/dark-factory/scripts/factory_core/cli.py run-record assemble`, so this resolves to the same `$CLONE_DIR/dark-factory/scripts/factory_core/providers/cli.py` that the DAG's `$_PCLI` uses, and it does not depend on `--clone-dir`. Timeout: `LABEL_LOOKUP_TIMEOUT_SECONDS = 30`, matching the `timeout 30` the DAG nodes use for `git push`.
5. **These responses count as a lookup error** (`(False, False)`):
   - a non-zero exit;
   - a timeout or OSError;
   - stdout that is not JSON;
   - a JSON object with no `labels` key. `GitHubTracker.get_item` returns `{}` with exit 0 when `gh` fails (`providers/tracker/github.py:60-65`), so a missing `labels` key means "unknown", not "no labels".

   A real issue with no labels returns `{"labels": []}`, and that is `(False, True)`. Labels can be dicts with `name` (GitHub) or plain strings (`providers/tracker/jira.py:129`). The helper accepts both.
6. **The helper catches its own exceptions.** `cmd_assemble` calls it without wrapping it in `try`. This keeps the gating tests strict: a monkeypatched helper that raises makes the test fail loudly instead of being hidden as a lookup error.
7. **`cost_report.py` does not change.** `format_economics_line` already prints `**Outcome:** clean_abort (score 0.0)`, which is accurate (spec Open Question 2).
8. **The existence check is duplicated, not moved** (spec R3 allows "move or duplicate"). A new `artifact_push_path` is computed in `cmd_assemble` ahead of the `_compute_harness_economics` call, for refine/plan only. The later `push_result` fold, including its own `push_path = artifacts_dir / "push-result.json"` line, is left byte-for-byte unchanged. Pointing the fold at the resolved workflow-run file would also revive #473's `**Push:** FAILED` cost-report line, which the directory split has left inert. That is a separate behaviour change, so it is filed as a follow-up, not bundled here (plan-phase conformance review flagged it as out of scope).
9. **`push-result.json` is looked up in the workflow run's artifacts directory, not only in `--artifacts-dir`.** (The architect review found this. It corrects a premise in the spec without changing any surface the spec puts off limits.)

   Spec R3 says `refine-push`/`plan-push-and-advance` write `$ARTIFACTS_DIR/push-result.json` and that `cmd_assemble` reads `$ARTIFACTS_DIR`. These are two different directories at runtime:
   - `entrypoint.sh:114` sets its own `ARTIFACTS_DIR=.../artifacts/runs/${RUN_ID}`, with a fresh uuid4, and passes it as `--artifacts-dir` (`entrypoint.sh:1095`).
   - Archon overrides `ARTIFACTS_DIR` for every bash node with the workflow run's directory (`/opt/archon/packages/workflows/src/dag-executor.ts:1322-1324`). `getRunArtifactsPath` builds it as `.../artifacts/runs/<workflowRunId>` (`/opt/archon/packages/paths/src/archon-paths.ts:356-357`).
   - The repo already documents this split at `entrypoint.sh:807-808` and `scripts/factory_core/effective_config.py:28` (`FACTORY_RUN_CONFIG_JSON`).

   Without this decision, every completed refine/plan run would miss the file and score `failed`, including runs that delivered.

   Fix, inside `run_record.py` only: the archon cost JSON that `cmd_assemble` already reads (`archon workflow cost --last --json` → `run_id: run.id`, `/opt/archon/packages/cli/src/commands/workflow.ts`) carries that same `workflowRunId`. A new pure helper `_archon_workflow_run_id(path)` reads it with the existing `_find_run_obj` and validates it with the existing `_SAFE_RUN_ID_RE` path-traversal guard. `cmd_assemble` then checks `artifacts_dir.parent / <workflow_run_id> / "push-result.json"` first and falls back to `artifacts_dir / "push-result.json"`. The fallback covers every existing fixture and any same-directory setup.

   This needs no `entrypoint.sh` or workflow YAML change, and the spec's "call sites unaffected" claim still holds.

   Accepted limitation, in the same direction as spec R3's accepted "failed write": if the cost capture produced no parseable run id, only the entrypoint directory is checked. A delivered run can then score `failed`, and `artifact_check["workflow_run_id"] is None` makes that visible in telemetry.

   The resolution only runs for `refine`/`plan` and feeds only `artifact_check`. No intent's `push_result` or `memory_trace` fold changes. The `push_result` fold is inert in production because of the directory split (none of the 40 most recent run records carries the key). The `memory_trace` fold is not: 27 of those 40 records carry `memory_trace`. The inert fold and the gate-stage files are noted for a follow-up ticket (see Self-review).

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `scripts/factory_core/run_record.py` | Modify | `POLICY_VERSION` 1.1; `_compute_outcome`/`_compute_harness_economics` get the `artifact_check` kwarg; new `_check_needs_discussion` helper and its constants; new `_archon_workflow_run_id` helper; wiring in `cmd_assemble`; replay in `_backfill_run_economics`; docstring path fix (R6) |
| `tests/test_run_record.py` | Modify (append only) | New tests for every new branch, the lookup gating, backfill replay, and the R1 regression pins |
| `docs/configuration.md` | Modify (Task 6b) | Re-map the `run_record.py` line citations the edits shift (lines 501 and 555) |
| `docs/features.md` | Modify (Task 6b) | Re-map the `run_record.py` line citations in the "Run records and telemetry" row (line 139) |
| `docs/run-lifecycle.md` | Modify (Task 6b) | Re-map the `cmd_assemble` range (line 925) and add one sentence on the refine/plan artifact check |

No other files change: no workflow YAML, `entrypoint.sh`, `scheduler.sh`, `breaker.py`, `cost_report.py`, `config/config.yaml`, or command files. The untracked `dark-factory/scripts/` mirror in the clone is not edited.

Test command used throughout (CI sets `PYTHONPATH=scripts`):

```bash
PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v
```

---

## Task 1: Pure outcome policy — `artifact_check` kwarg, `clean_abort`, policy 1.1, docstring fix

**Files:**
- Modify: `scripts/factory_core/run_record.py` (`POLICY_VERSION` at line 29; `_compute_outcome` at lines 428-479)
- Test: `tests/test_run_record.py` (append a new section at the end of the file)

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_run_record.py`:

```python


# ── artifact_check: artifact-less refine/plan scoring (issue #447) ─────────

_NO_GATE_STAGES = [{"stage": "conflict_resolution", "verdict": "none"}]
_CHECK_PRESENT = {"artifact_present": True}
_CHECK_CLEAN_ABORT = {"artifact_present": False, "needs_discussion": True, "label_lookup": "ok"}
_CHECK_SILENT_DEATH = {"artifact_present": False, "needs_discussion": False, "label_lookup": "ok"}
_CHECK_LOOKUP_ERROR = {"artifact_present": False, "needs_discussion": False, "label_lookup": "error"}


def test_policy_version_is_1_1():
    assert rr.POLICY_VERSION == "1.1"


def test_outcome_artifact_present_stays_produced_ungated():
    out = rr._compute_outcome("completed", _NO_GATE_STAGES, artifact_check=_CHECK_PRESENT)
    assert out["state"] == "produced_ungated"
    assert out["score"] == 1.0
    assert out["evidence"]["ungated"] is True
    assert out["evidence"]["artifact_check"] == _CHECK_PRESENT


def test_outcome_no_artifact_with_needs_discussion_is_clean_abort():
    out = rr._compute_outcome("completed", [], artifact_check=_CHECK_CLEAN_ABORT)
    assert out["state"] == "clean_abort"
    assert out["score"] == 0.0
    assert out["evidence"]["ungated"] is False
    assert out["evidence"]["penalties"] == []
    assert out["evidence"]["artifact_check"] == _CHECK_CLEAN_ABORT


def test_outcome_no_artifact_without_label_is_failed():
    out = rr._compute_outcome("completed", [], artifact_check=_CHECK_SILENT_DEATH)
    assert out["state"] == "failed"
    assert out["score"] == 0.0
    assert out["evidence"]["ungated"] is False
    assert out["evidence"]["artifact_check"] == _CHECK_SILENT_DEATH


def test_outcome_no_artifact_lookup_error_fails_closed():
    out = rr._compute_outcome("completed", [], artifact_check=_CHECK_LOOKUP_ERROR)
    assert out["state"] == "failed"
    assert out["score"] == 0.0
    assert out["evidence"]["artifact_check"]["label_lookup"] == "error"


def test_outcome_without_artifact_check_is_byte_identical_legacy():
    # R1 regression pin: omitting the kwarg (every non-refine/plan run) yields exactly
    # the pre-#447 dict — no new evidence key, same state/score.
    legacy = {
        "state": "produced_ungated",
        "score": 1.0,
        "evidence": {"status": "completed", "gate_stages": [], "penalties": [], "ungated": True},
    }
    assert rr._compute_outcome("completed", _NO_GATE_STAGES) == legacy
    assert rr._compute_outcome("completed", _NO_GATE_STAGES, artifact_check=None) == legacy


def test_outcome_artifact_check_never_overrides_status_or_gate_precedence():
    assert rr._compute_outcome("failed", [], artifact_check=_CHECK_CLEAN_ABORT)["state"] == "failed"
    blocking = [{"stage": "validation", "verdict": "FAIL"}]
    assert rr._compute_outcome("completed", blocking, artifact_check=_CHECK_CLEAN_ABORT)["state"] == "blocked"
    passing = [{"stage": "validation", "verdict": "PASS"}]
    assert rr._compute_outcome("completed", passing, artifact_check=_CHECK_SILENT_DEATH)["state"] == "delivered_clean"
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v -k "policy_version_is_1_1 or artifact_present_stays or clean_abort or without_label_is_failed or lookup_error_fails_closed or byte_identical_legacy or never_overrides"`

Expected: `test_policy_version_is_1_1` FAILS with `AssertionError: assert '1.0' == '1.1'`. Every test that passes `artifact_check=` FAILS with `TypeError: _compute_outcome() got an unexpected keyword argument 'artifact_check'`. That includes the second assertion in `test_outcome_without_artifact_check_is_byte_identical_legacy`.

- [ ] **Step 3: Implement**

In `scripts/factory_core/run_record.py`, change line 29:

```python
POLICY_VERSION = "1.1"
```

Replace the `_compute_outcome` signature, docstring, and the `elif not gate_stages:` branch, the score branch, and the return statement. The body between them does not change. The full function after the edit:

```python
def _compute_outcome(status: str, stages: list, *, artifact_check: "dict | None" = None) -> dict:
    """Deterministic outcome.state / outcome.score policy (policy_version 1.1).

    failed/blocked/clean_abort always score 0.0 regardless of token spend — the
    mechanical enforcement of "don't reward raw token reduction over correctness." See
    docs/archive/2026-07-16-harness-economics-ledger-cpm-design.md "Outcome-score
    policy" (1.0) and docs/superpowers/specs/2026-11-22-run-record-artifact-scoring-design.md
    (1.1).

    artifact_check is passed by cmd_assemble for refine/plan runs only (#447). With zero
    gate stages, a run that committed no spec/plan is clean_abort when needs-discussion
    is on the issue and failed otherwise (including an unknown label state). None keeps
    the pre-1.1 unconditional produced_ungated scoring for every other intent.
    """
    stage_by_name = {s["stage"]: s for s in stages if s.get("stage") in GATE_STAGE_NAMES}
    gate_stages = [stage_by_name[name] for name in GATE_STAGE_NAMES if name in stage_by_name]
    cycles = stage_by_name.get("conformance", {}).get("cycles") or 0
    advisory = stage_by_name.get("review", {}).get("advisory") or 0

    if status != "completed":
        state = "failed"
    elif any(
        stage_by_name.get(name, {}).get("verdict") in _BLOCKING_VERDICTS[name]
        for name in GATE_STAGE_NAMES
    ):
        state = "blocked"
    elif not gate_stages:
        if artifact_check is None or artifact_check.get("artifact_present"):
            state = "produced_ungated"
        elif artifact_check.get("needs_discussion"):
            state = "clean_abort"
        else:
            state = "failed"
    else:
        state = "delivered_with_findings" if (cycles or advisory) else "delivered_clean"

    penalties = []
    if state in ("failed", "blocked", "clean_abort"):
        score = 0.0
    elif state == "produced_ungated":
        score = 1.0
    else:
        score = 1.0
        if cycles:
            delta = round(-CONFORMANCE_CYCLE_PENALTY * cycles, 4)
            score += delta
            penalties.append({"reason": "conformance_cycles", "count": cycles, "delta": delta})
        if advisory:
            delta = round(-REVIEW_ADVISORY_PENALTY * advisory, 4)
            score += delta
            penalties.append({"reason": "review_advisory", "count": advisory, "delta": delta})
        score = max(SCORE_FLOOR, min(1.0, round(score, 4)))

    evidence = {
        "status": status,
        "gate_stages": gate_stages,
        "penalties": penalties,
        "ungated": state == "produced_ungated",
    }
    if artifact_check is not None:
        evidence["artifact_check"] = artifact_check

    return {"state": state, "score": score, "evidence": evidence}
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v -k "outcome or policy_version"`

Expected: all selected tests PASS. That covers the 7 new tests and the existing `test_outcome_*` tests, which are unchanged.

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_core/run_record.py tests/test_run_record.py
git commit -m "feat(run-record): clean_abort/failed outcome for artifact-less refine/plan (policy 1.1) (#447)"
```

---

## Task 2: Thread `artifact_check` through `_compute_harness_economics`

**Files:**
- Modify: `scripts/factory_core/run_record.py` (`_compute_harness_economics`, lines 567-607)
- Test: `tests/test_run_record.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_run_record.py`:

```python


def _economics(tmp_path, artifact_check=None, **overrides):
    kwargs = dict(
        run_id="run-447", status="completed", stages=[],
        totals={"gen_ai.usage.input_tokens": 300, "gen_ai.usage.output_tokens": 200, "cost_usd": 0.1},
        started_at="2026-11-22T00:00:00Z", completed_at="2026-11-22T00:01:00Z",
        ledger_path=tmp_path / "no-ledger.jsonl",
    )
    kwargs.update(overrides)
    if artifact_check is None:
        return rr._compute_harness_economics(**kwargs)
    return rr._compute_harness_economics(artifact_check=artifact_check, **kwargs)


def test_harness_economics_clean_abort_keeps_retry_only_failure_spend(tmp_path):
    he = _economics(tmp_path, artifact_check=_CHECK_CLEAN_ABORT)
    assert he["policy_version"] == "1.1"
    assert he["outcome"]["state"] == "clean_abort"
    assert he["factory_cpm"] == 0.0
    assert he["failure_spend"] == {"tokens": 0, "basis": "retry_only"}


def test_harness_economics_artifactless_failed_is_whole_run_spend(tmp_path):
    he = _economics(tmp_path, artifact_check=_CHECK_SILENT_DEATH)
    assert he["outcome"]["state"] == "failed"
    assert he["factory_cpm"] == 0.0
    assert he["failure_spend"] == {"tokens": 500, "basis": "whole_run"}


def test_harness_economics_artifact_present_scores_like_legacy(tmp_path):
    he = _economics(tmp_path, artifact_check=_CHECK_PRESENT)
    assert he["outcome"]["state"] == "produced_ungated"
    assert he["factory_cpm"] == pytest.approx(2000.0)  # 1.0 * 1e6 / 500 tokens
    assert he["failure_spend"] == {"tokens": 0, "basis": "retry_only"}


def test_harness_economics_without_artifact_check_unchanged(tmp_path):
    he = _economics(tmp_path)
    assert he["outcome"]["state"] == "produced_ungated"
    assert "artifact_check" not in he["outcome"]["evidence"]
    assert he["factory_cpm"] == pytest.approx(2000.0)
    assert he["failure_spend"] == {"tokens": 0, "basis": "retry_only"}
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v -k "harness_economics_clean_abort or artifactless_failed_is_whole_run or artifact_present_scores_like_legacy or without_artifact_check_unchanged"`

Expected: the first three FAIL with `TypeError: _compute_harness_economics() got an unexpected keyword argument 'artifact_check'`. `test_harness_economics_without_artifact_check_unchanged` PASSES already, because it pins the existing behaviour.

- [ ] **Step 3: Implement**

In `scripts/factory_core/run_record.py`, change the `_compute_harness_economics` signature and its `_compute_outcome` call. The rest of the function does not change. In particular, the `failure_spend` check `if outcome["state"] in ("failed", "blocked"):` stays exactly as it is, so `clean_abort` falls through to `retry_only`:

```python
def _compute_harness_economics(
    *, run_id: str, status: str, stages: list, totals: dict,
    started_at: str, completed_at: str, ledger_path: pathlib.Path,
    artifact_check: "dict | None" = None,
) -> dict:
    tokens_in = totals.get("gen_ai.usage.input_tokens")
    tokens_out = totals.get("gen_ai.usage.output_tokens")
    tokens_per_task = None if tokens_in is None or tokens_out is None else tokens_in + tokens_out
    cost_per_task = totals.get("cost_usd")

    outcome = _compute_outcome(status, stages, artifact_check=artifact_check)
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v -k "harness_economics"`

Expected: all PASS, including the existing `test_assemble_*harness_economics*` tests.

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_core/run_record.py tests/test_run_record.py
git commit -m "feat(run-record): thread artifact_check through _compute_harness_economics (#447)"
```

---

## Task 3: Fail-closed `needs-discussion` lookup helper

**Files:**
- Modify: `scripts/factory_core/run_record.py` (add `import subprocess` to the stdlib import block at lines 10-19; add constants and the helper directly above `def cmd_assemble`)
- Test: `tests/test_run_record.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_run_record.py`:

```python


class _FakeCompleted:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def _fake_tracker_run(monkeypatch, *, stdout="", returncode=0, exc=None):
    calls = []

    def _run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if exc is not None:
            raise exc
        return _FakeCompleted(returncode=returncode, stdout=stdout)

    monkeypatch.setattr(rr.subprocess, "run", _run)
    return calls


def test_providers_cli_path_resolves_to_real_file():
    assert rr._PROVIDERS_CLI.is_file()
    assert rr._PROVIDERS_CLI.parts[-2:] == ("providers", "cli.py")


def test_check_needs_discussion_label_present(monkeypatch):
    labels = {"labels": [{"name": "plan-pending-review"}, {"name": "needs-discussion"}]}
    calls = _fake_tracker_run(monkeypatch, stdout=json.dumps(labels))
    assert rr._check_needs_discussion(447) == (True, True)
    cmd, kwargs = calls[0]
    assert cmd == [sys.executable, str(rr._PROVIDERS_CLI), "tracker", "get",
                   "--id", "447", "--fields", "labels"]
    assert kwargs["timeout"] == rr.LABEL_LOOKUP_TIMEOUT_SECONDS
    assert kwargs["capture_output"] is True


def test_check_needs_discussion_label_absent(monkeypatch):
    _fake_tracker_run(monkeypatch, stdout=json.dumps({"labels": [{"name": "ready-for-agent"}]}))
    assert rr._check_needs_discussion(447) == (False, True)


def test_check_needs_discussion_no_labels_is_a_successful_lookup(monkeypatch):
    _fake_tracker_run(monkeypatch, stdout=json.dumps({"labels": []}))
    assert rr._check_needs_discussion(447) == (False, True)


def test_check_needs_discussion_accepts_plain_string_labels(monkeypatch):
    # JiraTracker.get_item returns labels as bare strings, GitHub as {"name": ...}.
    _fake_tracker_run(monkeypatch, stdout=json.dumps({"labels": ["needs-discussion"]}))
    assert rr._check_needs_discussion(447) == (True, True)


@pytest.mark.parametrize("stdout,returncode", [
    ("{}", 0),                       # GitHubTracker.get_item's own gh-failure fallback
    ("not json {{{", 0),
    (json.dumps({"labels": None}), 0),
    (json.dumps({"labels": [{"name": "needs-discussion"}]}), 1),
])
def test_check_needs_discussion_fails_closed_on_bad_response(monkeypatch, stdout, returncode):
    _fake_tracker_run(monkeypatch, stdout=stdout, returncode=returncode)
    assert rr._check_needs_discussion(447) == (False, False)


def test_check_needs_discussion_fails_closed_on_timeout(monkeypatch):
    import subprocess
    _fake_tracker_run(monkeypatch, exc=subprocess.TimeoutExpired(cmd="tracker", timeout=30))
    assert rr._check_needs_discussion(447) == (False, False)


def test_check_needs_discussion_fails_closed_on_oserror(monkeypatch):
    _fake_tracker_run(monkeypatch, exc=OSError("no python"))
    assert rr._check_needs_discussion(447) == (False, False)
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v -k "providers_cli_path or check_needs_discussion"`

Expected: all FAIL with `AttributeError: module 'factory_core.run_record' has no attribute 'subprocess'`, or with `... has no attribute '_PROVIDERS_CLI'` / `'_check_needs_discussion'`.

- [ ] **Step 3: Implement**

In `scripts/factory_core/run_record.py`, add `import subprocess` to the stdlib import block in alphabetical order, between `import re` and `import sys`:

```python
import re
import subprocess
import sys
```

Add directly above `def cmd_assemble(args) -> None:`:

```python
NEEDS_DISCUSSION_LABEL = "needs-discussion"
LABEL_LOOKUP_TIMEOUT_SECONDS = 30
# Same providers CLI the refine-push/plan-push-and-advance DAG nodes shell out to
# ($CLONE_DIR/dark-factory/scripts/factory_core/providers/cli.py — this module's sibling).
_PROVIDERS_CLI = pathlib.Path(__file__).resolve().parent / "providers" / "cli.py"


def _check_needs_discussion(issue) -> tuple:
    """Live needs-discussion lookup for a completed, artifact-less refine/plan run (#447).

    Returns (needs_discussion, lookup_ok). Fails closed: a timeout, non-zero exit,
    unparseable output, or a response without a "labels" key (GitHubTracker.get_item
    returns {} when gh fails) is (False, False) — an unknown label state scores as a
    failure, never as a clean abort, matching the DAG nodes' own `|| true` direction.
    """
    try:
        result = subprocess.run(
            [sys.executable, str(_PROVIDERS_CLI), "tracker", "get",
             "--id", str(issue), "--fields", "labels"],
            capture_output=True, text=True, timeout=LABEL_LOOKUP_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            return False, False
        labels = json.loads(result.stdout)["labels"]
        names = {label.get("name") if isinstance(label, dict) else label for label in labels}
    except Exception:
        return False, False
    return NEEDS_DISCUSSION_LABEL in names, True
```

(`{"labels": None}` makes the set comprehension raise `TypeError`, which the `except` catches. That case therefore fails closed, as the parametrized test expects.)

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v -k "providers_cli_path or check_needs_discussion"`

Expected: 11 PASS (1 path test, 4 success-path tests, 4 parametrized bad-response cases, timeout, oserror).

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_core/run_record.py tests/test_run_record.py
git commit -m "feat(run-record): fail-closed needs-discussion label lookup helper (#447)"
```

---

## Task 4: Resolve the workflow run id from the archon cost JSON

**Files:**
- Modify: `scripts/factory_core/run_record.py` (add the helper directly below `_check_needs_discussion`, above `def cmd_assemble`)
- Test: `tests/test_run_record.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_run_record.py`:

```python


@pytest.mark.parametrize("payload,expected", [
    ({"run_id": "b5f8d7c0f8bee469b23f19643b208214", "nodes": []}, "b5f8d7c0f8bee469b23f19643b208214"),
    ({"runId": "wf-run", "nodes": []}, "wf-run"),
    ({"run_id": "../../etc", "nodes": []}, None),   # _SAFE_RUN_ID_RE path-traversal guard
    ({"run_id": 42, "nodes": []}, None),
    ({"nodes": []}, None),
])
def test_archon_workflow_run_id_from_cost_json(tmp_path, payload, expected):
    cost = tmp_path / "cost.json"
    cost.write_text(json.dumps(payload))
    assert rr._archon_workflow_run_id(cost) == expected


def test_archon_workflow_run_id_missing_or_unparseable(tmp_path):
    assert rr._archon_workflow_run_id(None) is None
    assert rr._archon_workflow_run_id(tmp_path / "absent.json") is None
    garbage = tmp_path / "garbage.json"
    garbage.write_text("No cost data available for run x (archon-dark-factory).")
    assert rr._archon_workflow_run_id(garbage) is None
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v -k "archon_workflow_run_id"`

Expected: all 6 FAIL with `AttributeError: module 'factory_core.run_record' has no attribute '_archon_workflow_run_id'`.

- [ ] **Step 3: Implement**

In `scripts/factory_core/run_record.py`, add directly below `_check_needs_discussion` (above `def cmd_assemble`):

```python
def _archon_workflow_run_id(archon_cost_path: "pathlib.Path | None") -> "str | None":
    """Workflow run id from `archon workflow cost --last --json` output, or None.

    Archon gives every DAG node its own $ARTIFACTS_DIR, runs/<workflow run id> (see
    FACTORY_RUN_CONFIG_JSON in entrypoint.sh), a sibling of the entrypoint's runs/<RUN_ID>
    that cmd_assemble receives as --artifacts-dir — so refine-push/plan-push-and-advance's
    push-result.json lives under this id, not under args.artifacts_dir (#447).
    """
    if archon_cost_path is None or not archon_cost_path.exists():
        return None
    try:
        run_obj = _find_run_obj(archon_cost_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    run_id = (run_obj or {}).get("run_id") or (run_obj or {}).get("runId")
    if not isinstance(run_id, str) or not _SAFE_RUN_ID_RE.match(run_id):
        return None
    return run_id
```

(`{"run_id": 42}`: `_find_run_obj` returns the object because `42` is truthy, and the `isinstance` check then rejects it.)

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v -k "archon_workflow_run_id"`

Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_core/run_record.py tests/test_run_record.py
git commit -m "feat(run-record): resolve archon workflow run id from cost JSON (#447)"
```

---

## Task 5: Wire the artifact check into `cmd_assemble`

**Files:**
- Modify: `scripts/factory_core/run_record.py` (`cmd_assemble`, lines 609-736)
- Test: `tests/test_run_record.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_run_record.py`:

```python


def _artifact_assemble_args(tmp_path, monkeypatch, *, intent, status="completed"):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")
    args = _AssembleArgs(tmp_path, tmp_path / "run-record.json")
    args.intent = intent
    args.status = status
    return args


def _split_artifact_dirs(tmp_path, monkeypatch, *, intent):
    """Production layout: entrypoint's runs/<RUN_ID> (--artifacts-dir) and archon's
    runs/<workflow run id> (every DAG node's $ARTIFACTS_DIR) are sibling directories."""
    entry_dir = tmp_path / "runs" / "entry-run"
    workflow_dir = tmp_path / "runs" / "wf-run"
    entry_dir.mkdir(parents=True)
    workflow_dir.mkdir(parents=True)
    cost = tmp_path / "cost.json"
    cost.write_text(json.dumps({"run_id": "wf-run", "nodes": []}))
    args = _artifact_assemble_args(entry_dir, monkeypatch, intent=intent)
    args.archon_cost_json = str(cost)
    return args, entry_dir, workflow_dir


def _stub_lookup(monkeypatch, result):
    calls = []

    def _fake(issue):
        calls.append(issue)
        return result

    monkeypatch.setattr(rr, "_check_needs_discussion", _fake)
    return calls


def _forbid_lookup(monkeypatch):
    def _boom(issue):
        raise AssertionError("_check_needs_discussion must not be called here")

    monkeypatch.setattr(rr, "_check_needs_discussion", _boom)


def test_assemble_refine_finds_push_result_in_workflow_run_dir(tmp_path, monkeypatch):
    _forbid_lookup(monkeypatch)
    args, entry_dir, workflow_dir = _split_artifact_dirs(tmp_path, monkeypatch, intent="refine")
    (workflow_dir / "push-result.json").write_text(json.dumps({"node": "refine-push", "pushed": True}))
    rr.cmd_assemble(args)

    rec = json.loads((entry_dir / "run-record.json").read_text())
    assert rec["artifact_check"] == {"artifact_present": True}
    # The #473 push_result fold is deliberately unchanged (Design decision 8): it still
    # reads only --artifacts-dir.
    assert "push_result" not in rec
    assert rec["harness_economics"]["outcome"]["state"] == "produced_ungated"
    assert rec["harness_economics"]["outcome"]["score"] == 1.0


def test_assemble_plan_split_dirs_no_push_result_is_failed(tmp_path, monkeypatch):
    calls = _stub_lookup(monkeypatch, (False, True))
    args, entry_dir, _ = _split_artifact_dirs(tmp_path, monkeypatch, intent="plan")
    rr.cmd_assemble(args)

    assert calls == [333]
    rec = json.loads((entry_dir / "run-record.json").read_text())
    assert rec["artifact_check"] == {
        "artifact_present": False, "needs_discussion": False,
        "label_lookup": "ok", "workflow_run_id": "wf-run",
    }
    assert rec["harness_economics"]["outcome"]["state"] == "failed"
    assert "push_result" not in rec


def test_assemble_refine_with_push_result_is_produced_ungated(tmp_path, monkeypatch):
    # Same-directory fallback (no cost JSON -> workflow run id unresolved).
    # pushed: false is still "artifact found" (R3) — no third branch.
    _forbid_lookup(monkeypatch)
    (tmp_path / "push-result.json").write_text(json.dumps({"node": "refine-push", "pushed": False}))
    args = _artifact_assemble_args(tmp_path, monkeypatch, intent="refine")
    rr.cmd_assemble(args)

    rec = json.loads((tmp_path / "run-record.json").read_text())
    assert rec["artifact_check"] == {"artifact_present": True}
    assert rec["push_result"] == {"node": "refine-push", "pushed": False}
    outcome = rec["harness_economics"]["outcome"]
    assert outcome["state"] == "produced_ungated"
    assert outcome["score"] == 1.0
    assert outcome["evidence"]["artifact_check"] == {"artifact_present": True}


def test_assemble_malformed_push_result_still_counts_as_artifact(tmp_path, monkeypatch):
    _forbid_lookup(monkeypatch)
    (tmp_path / "push-result.json").write_text("not valid json {{{")
    args = _artifact_assemble_args(tmp_path, monkeypatch, intent="plan")
    rr.cmd_assemble(args)

    rec = json.loads((tmp_path / "run-record.json").read_text())
    assert rec["artifact_check"] == {"artifact_present": True}
    assert rec["harness_economics"]["outcome"]["state"] == "produced_ungated"
    assert "push_result" not in rec


def test_assemble_plan_no_artifact_with_label_is_clean_abort(tmp_path, monkeypatch):
    calls = _stub_lookup(monkeypatch, (True, True))
    args = _artifact_assemble_args(tmp_path, monkeypatch, intent="plan")
    rr.cmd_assemble(args)

    assert calls == [333]
    rec = json.loads((tmp_path / "run-record.json").read_text())
    expected = {"artifact_present": False, "needs_discussion": True,
                "label_lookup": "ok", "workflow_run_id": None}
    assert rec["artifact_check"] == expected
    he = rec["harness_economics"]
    assert he["outcome"]["state"] == "clean_abort"
    assert he["outcome"]["score"] == 0.0
    assert he["outcome"]["evidence"]["artifact_check"] == expected
    assert he["failure_spend"]["basis"] == "retry_only"


def test_assemble_refine_no_artifact_no_label_is_failed(tmp_path, monkeypatch):
    calls = _stub_lookup(monkeypatch, (False, True))
    args = _artifact_assemble_args(tmp_path, monkeypatch, intent="refine")
    rr.cmd_assemble(args)

    assert calls == [333]
    rec = json.loads((tmp_path / "run-record.json").read_text())
    assert rec["artifact_check"] == {"artifact_present": False, "needs_discussion": False,
                                     "label_lookup": "ok", "workflow_run_id": None}
    he = rec["harness_economics"]
    assert he["outcome"]["state"] == "failed"
    assert he["outcome"]["score"] == 0.0
    assert he["failure_spend"]["basis"] == "whole_run"


def test_assemble_refine_no_artifact_lookup_error_is_failed(tmp_path, monkeypatch):
    _stub_lookup(monkeypatch, (False, False))
    args = _artifact_assemble_args(tmp_path, monkeypatch, intent="refine")
    rr.cmd_assemble(args)

    rec = json.loads((tmp_path / "run-record.json").read_text())
    assert rec["artifact_check"] == {"artifact_present": False, "needs_discussion": False,
                                     "label_lookup": "error", "workflow_run_id": None}
    assert rec["harness_economics"]["outcome"]["state"] == "failed"
    assert rec["harness_economics"]["outcome"]["evidence"]["artifact_check"]["label_lookup"] == "error"


def test_assemble_refine_lookup_timeout_end_to_end_fails_closed(tmp_path, monkeypatch):
    # Real _check_needs_discussion, raising subprocess: proves the helper's catch end to end.
    import subprocess
    _fake_tracker_run(monkeypatch, exc=subprocess.TimeoutExpired(cmd="tracker", timeout=30))
    args = _artifact_assemble_args(tmp_path, monkeypatch, intent="refine")
    rr.cmd_assemble(args)  # must not raise

    rec = json.loads((tmp_path / "run-record.json").read_text())
    assert rec["artifact_check"]["label_lookup"] == "error"
    assert rec["artifact_check"]["needs_discussion"] is False
    assert rec["harness_economics"]["outcome"]["state"] == "failed"


def test_assemble_refine_failure_trap_never_looks_up(tmp_path, monkeypatch):
    # entrypoint.sh's on_failure path: --status failed, no push-result.json.
    _forbid_lookup(monkeypatch)
    args = _artifact_assemble_args(tmp_path, monkeypatch, intent="refine", status="failed")
    rr.cmd_assemble(args)

    rec = json.loads((tmp_path / "run-record.json").read_text())
    assert "artifact_check" not in rec
    assert rec["harness_economics"]["outcome"]["state"] == "failed"
    assert "artifact_check" not in rec["harness_economics"]["outcome"]["evidence"]


@pytest.mark.parametrize("intent", ["close", "new", "deconflict", "fix-main"])
def test_assemble_non_refine_plan_intents_unchanged(tmp_path, monkeypatch, intent):
    # R1 regression pin at the cmd_assemble level: no lookup, no artifact_check key,
    # today's unconditional produced_ungated/1.0 for zero gate stages.
    _forbid_lookup(monkeypatch)
    args = _artifact_assemble_args(tmp_path, monkeypatch, intent=intent)
    rr.cmd_assemble(args)

    rec = json.loads((tmp_path / "run-record.json").read_text())
    assert "artifact_check" not in rec
    outcome = rec["harness_economics"]["outcome"]
    assert outcome == {
        "state": "produced_ungated",
        "score": 1.0,
        "evidence": {"status": "completed", "gate_stages": [], "penalties": [], "ungated": True},
    }


def test_assemble_non_refine_plan_ignores_workflow_run_dir(tmp_path, monkeypatch):
    # The workflow-dir resolution is refine/plan-only: a close run's record is untouched
    # even when a push-result.json sits in the workflow run's directory.
    _forbid_lookup(monkeypatch)
    args, entry_dir, workflow_dir = _split_artifact_dirs(tmp_path, monkeypatch, intent="close")
    (workflow_dir / "push-result.json").write_text(json.dumps({"pushed": True}))
    rr.cmd_assemble(args)

    rec = json.loads((entry_dir / "run-record.json").read_text())
    assert "artifact_check" not in rec
    assert "push_result" not in rec
    assert rec["harness_economics"]["outcome"]["state"] == "produced_ungated"
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v -k "test_assemble_refine or test_assemble_plan or malformed_push_result_still_counts or non_refine_plan"`

Expected:
- FAIL with `KeyError: 'artifact_check'`, because `cmd_assemble` does not write the key yet:
  - `test_assemble_refine_finds_push_result_in_workflow_run_dir`
  - `test_assemble_refine_with_push_result_is_produced_ungated`
  - `test_assemble_malformed_push_result_still_counts_as_artifact`
  - `test_assemble_refine_no_artifact_lookup_error_is_failed`
  - `test_assemble_refine_lookup_timeout_end_to_end_fails_closed`
- FAIL with `assert [] == [333]`, because the lookup is never called:
  - `test_assemble_plan_split_dirs_no_push_result_is_failed`
  - `test_assemble_plan_no_artifact_with_label_is_clean_abort`
  - `test_assemble_refine_no_artifact_no_label_is_failed`
- PASS already, because they pin unchanged behaviour:
  - `test_assemble_refine_failure_trap_never_looks_up`
  - the 4 `test_assemble_non_refine_plan_intents_unchanged` cases
  - `test_assemble_non_refine_plan_ignores_workflow_run_dir`

- [ ] **Step 3: Implement**

In `scripts/factory_core/run_record.py` `cmd_assemble`, insert the following directly after the `loops` `try/except` block and before `run_record = {`. `archon_path` is already bound earlier in the function, as `pathlib.Path(args.archon_cost_json) if args.archon_cost_json else None`:

```python
    # #447: a refine/plan run's whole deliverable is one committed spec/plan doc.
    # refine-push/plan-push-and-advance write push-result.json only when
    # push_gate_check.sh found it — into archon's per-workflow-run $ARTIFACTS_DIR, a
    # sibling of args.artifacts_dir (see _archon_workflow_run_id). On a completed run its
    # absence means no artifact; then the live needs-discussion label separates a clean
    # abort from a silent death. Never looked up on the failure-trap path (--status
    # failed scores failed anyway).
    artifact_check = None
    if args.intent in ("refine", "plan"):
        artifact_push_path = artifacts_dir / "push-result.json"
        workflow_run_id = _archon_workflow_run_id(archon_path)
        if workflow_run_id is not None:
            workflow_push_path = artifacts_dir.parent / workflow_run_id / "push-result.json"
            if workflow_push_path.exists():
                artifact_push_path = workflow_push_path
        if artifact_push_path.exists():
            artifact_check = {"artifact_present": True}
        elif args.status == "completed":
            needs_discussion, lookup_ok = _check_needs_discussion(args.issue)
            artifact_check = {
                "artifact_present": False,
                "needs_discussion": needs_discussion,
                "label_lookup": "ok" if lookup_ok else "error",
                "workflow_run_id": workflow_run_id,
            }
```

Replace the existing `run_record["harness_economics"] = _compute_harness_economics(` block with:

```python
    if artifact_check is not None:
        run_record["artifact_check"] = artifact_check  # replayed by backfill-economics
    run_record["harness_economics"] = _compute_harness_economics(
        run_id=args.run_id,
        status=run_record["status"],
        stages=stages,
        totals=run_record["totals"],
        started_at=run_record["started_at"],
        completed_at=run_record["completed_at"],
        ledger_path=pathlib.Path(args.ledger_path) if args.ledger_path else LEDGER_PATH,
        artifact_check=artifact_check,
    )
```

Leave the later push-result fold (`push_path = artifacts_dir / "push-result.json"` and its `if push_path.exists():` block) exactly as it is. It is not part of this ticket (Design decision 8).

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v -k "assemble"`

Expected: all PASS. That includes the existing `test_assemble_picks_up_push_result`, `test_assemble_no_push_result_key_when_absent`, `test_assemble_tolerates_malformed_push_result` and `test_assemble_attaches_harness_economics_ungated`. Those all use `_AssembleArgs.intent = "new"`, so they never reach the resolution or the lookup.

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_core/run_record.py tests/test_run_record.py
git commit -m "feat(run-record): score artifact-less refine/plan runs in cmd_assemble (#447)"
```

---

## Task 6: Backfill replays the recorded `artifact_check`

**Files:**
- Modify: `scripts/factory_core/run_record.py` (`_backfill_run_economics`, lines 808-831)
- Test: `tests/test_run_record.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_run_record.py`:

```python


def _stored_refine_record(tmp_path, run_id, **extra):
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    record = {
        "run_id": run_id, "intent": "refine", "status": "completed",
        "started_at": "2026-11-22T04:00:00Z", "completed_at": "2026-11-22T04:01:00Z",
        "stages": [],
        "totals": {"gen_ai.usage.input_tokens": 100, "gen_ai.usage.output_tokens": 50, "cost_usd": 0.02},
    }
    record.update(extra)
    path = run_dir / "run-record.json"
    path.write_text(json.dumps(record))
    return path


def test_backfill_keeps_failed_verdict_for_artifactless_refine(tmp_path, monkeypatch):
    _forbid_lookup(monkeypatch)  # backfill replays, never looks up
    path = _stored_refine_record(tmp_path, "run-sd", artifact_check=_CHECK_SILENT_DEATH)
    ok = rr._backfill_run_economics("run-sd", artifacts_root=tmp_path / "runs",
                                    ledger_path=tmp_path / "no-ledger.jsonl")
    assert ok is True
    updated = json.loads(path.read_text())
    assert updated["artifact_check"] == _CHECK_SILENT_DEATH
    he = updated["harness_economics"]
    assert he["policy_version"] == "1.1"
    assert he["outcome"]["state"] == "failed"
    assert he["outcome"]["score"] == 0.0


def test_backfill_keeps_clean_abort_verdict(tmp_path, monkeypatch):
    _forbid_lookup(monkeypatch)
    path = _stored_refine_record(tmp_path, "run-ca", artifact_check=_CHECK_CLEAN_ABORT)
    rr._backfill_run_economics("run-ca", artifacts_root=tmp_path / "runs",
                               ledger_path=tmp_path / "no-ledger.jsonl")
    he = json.loads(path.read_text())["harness_economics"]
    assert he["outcome"]["state"] == "clean_abort"
    assert he["failure_spend"]["basis"] == "retry_only"


def test_backfill_legacy_record_without_artifact_check_unchanged(tmp_path, monkeypatch):
    _forbid_lookup(monkeypatch)
    path = _stored_refine_record(tmp_path, "run-legacy")
    rr._backfill_run_economics("run-legacy", artifacts_root=tmp_path / "runs",
                               ledger_path=tmp_path / "no-ledger.jsonl")
    updated = json.loads(path.read_text())
    assert "artifact_check" not in updated
    outcome = updated["harness_economics"]["outcome"]
    assert outcome["state"] == "produced_ungated"
    assert outcome["score"] == 1.0
    assert "artifact_check" not in outcome["evidence"]
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v -k "test_backfill_keeps or test_backfill_legacy"`

Expected: `test_backfill_keeps_failed_verdict_for_artifactless_refine` FAILS with `AssertionError: assert 'produced_ungated' == 'failed'`, and `test_backfill_keeps_clean_abort_verdict` FAILS with `AssertionError: assert 'produced_ungated' == 'clean_abort'`. This is the verdict rewrite that the spec's R4 "Backfill keeps the verdict" forbids. `test_backfill_legacy_record_without_artifact_check_unchanged` PASSES already.

- [ ] **Step 3: Implement**

In `scripts/factory_core/run_record.py` `_backfill_run_economics`, add one keyword to the `_compute_harness_economics(...)` call:

```python
    record["harness_economics"] = _compute_harness_economics(
        run_id=run_id,
        status=record.get("status", "completed"),
        stages=record.get("stages", []),
        totals=record.get("totals", {}),
        started_at=record.get("started_at", ""),
        completed_at=record.get("completed_at", ""),
        ledger_path=ledger_path,
        # Replay cmd_assemble's recorded refine/plan input (#447) — never a new lookup.
        # Absent (pre-1.1 records, other intents) -> None -> legacy scoring.
        artifact_check=record.get("artifact_check"),
    )
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v -k "backfill"`

Expected: all PASS. That covers the 3 new tests and the existing `test_backfill_economics_full_recompute`, `test_backfill_economics_ledger_rotated_away_degrades` and `test_backfill_economics_missing_run_record_skipped`.

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_core/run_record.py tests/test_run_record.py
git commit -m "fix(run-record): backfill-economics replays recorded artifact_check verdict (#447)"
```

---

## Task 6b: Re-map the doc citations the edits shifted

Tasks 1-6 add lines to `scripts/factory_core/run_record.py`, so every `file:line` citation into it in the reference docs now points at the wrong line. `scripts/docs_refs_check.py` only checks that a cited line is inside the file, so it stays green on stale numbers. This task corrects them by hand.

**Files:**
- Modify: `docs/configuration.md` (lines 501, 555)
- Modify: `docs/features.md` (line 139)
- Modify: `docs/run-lifecycle.md` (line 925 and the sentence that follows it)

- [ ] **Step 1: Read the real line numbers**

Run:

```bash
grep -n '^SEQ_URL\|^LEDGER_PATH\|^def cmd_record\|^def emit_health_event\|^def cmd_assemble\|loops = adapter.get\|out_file.write_text\|add_parser("issue-economics"\|add_parser("backfill-economics"' scripts/factory_core/run_record.py
```

Expected, when Tasks 1-6 were applied exactly as written (the file is then 1009 lines):

```
28:SEQ_URL = os.environ.get("SEQ_URL", "http://seq:5341")
124:def cmd_record(args) -> None:
173:def emit_health_event(event: str, issue: int, run_id: str, detail: dict) -> None:
494:LEDGER_PATH = pathlib.Path(
672:def cmd_assemble(args) -> None:
717:        loops = adapter.get(args.clone_dir, "loops") or []
796:    out_file.write_text(json.dumps(run_record, indent=2), encoding="utf-8")
981:    ie = sub.add_parser("issue-economics", help="Read-only cross-run rollup for an issue")
986:    be = sub.add_parser("backfill-economics", help="Degrade-only historical harness_economics recompute")
```

Confirm each number with this `grep -n` before editing. If a number differs from the table below, cite where the line actually is. The grep output is the source of truth, not this plan.

- [ ] **Step 2: Apply the re-map**

| Doc and line | Old citation | New citation | What it points at |
|---|---|---|---|
| `docs/configuration.md:501` | `run_record.py:27,483` | `run_record.py:28,494` | `SEQ_URL`, `LEDGER_PATH` |
| `docs/configuration.md:555` | `run_record.py:654` | `run_record.py:717` | `loops = adapter.get(...)` |
| `docs/features.md:139` | `cmd_record` (`:123`) | `cmd_record` (`:124`) | `def cmd_record` |
| `docs/features.md:139` | `cmd_assemble` (`:609`) | `cmd_assemble` (`:672`) | `def cmd_assemble` |
| `docs/features.md:139` | `emit_health_event` (`:172`) | `emit_health_event` (`:173`) | `def emit_health_event` |
| `docs/features.md:139` | (`run_record.py:886`, `:891`) | (`run_record.py:981`, `:986`) | the two `add_parser` lines |
| `docs/run-lifecycle.md:925` | `scripts/factory_core/run_record.py:609-705` | `scripts/factory_core/run_record.py:672-797` | `def cmd_assemble` through the line after `out_file.write_text` |

Change only these numbers. The `entrypoint.sh` citations on the same lines do not move, because this plan does not edit `entrypoint.sh`.

In `docs/run-lifecycle.md`, in the same list item, replace:

```
   stage to `runs.jsonl`. Failure is ignored.
```

with:

```
   stage to `runs.jsonl`. For `refine`/`plan` it also records `artifact_check`
   (`push-result.json` presence, else a `needs-discussion` label lookup) and scores an
   artifact-less run `failed` or `clean_abort`. Failure is ignored.
```

- [ ] **Step 3: Run the reference check**

Run: `python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md`

Expected: `Docs reference check passed for 4 doc(s).` and exit 0.

- [ ] **Step 4: Commit**

```bash
git add docs/configuration.md docs/features.md docs/run-lifecycle.md
git commit -m "docs: re-map run_record.py citations shifted by #447"
```

---

## Task 7: Full verification and scope check

**Files:** none modified. This task only verifies.

- [ ] **Step 1: Run the full run-record test file**

Run: `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v`

Expected: every test PASSES, with 0 failed. Every pre-existing test passes without modification (Safety Invariant 5).

- [ ] **Step 2: Run the full Python suite**

Run: `PYTHONPATH=scripts python -m pytest tests/ -q`

Expected: no new failures compared with `main`. `tests/test_bench_suite.py` runs `run-record assemble` with `--intent implement`, so it never reaches the lookup.

- [ ] **Step 3: Run the CI shell tests that exercise `run-record assemble`**

```bash
bash tests/test_cost_report_harness_economics.sh
bash tests/test_run_record_hermetic.sh
bash tests/test_entrypoint_cost_report_regression.sh
```

Expected: `test_cost_report_harness_economics.sh` and `test_run_record_hermetic.sh` end with `OK` and exit 0. `test_entrypoint_cost_report_regression.sh` gives the same result as on `origin/main`: it depends on the environment (it failed 3 of 5 on an unmodified tree in a bare factory-image container), so if it fails, run it on a clean `origin/main` checkout and compare. Identical failures there are not caused by this change; record them in the PR body and continue. `test_entrypoint_cost_report_regression.sh` uses `INTENT=fix`, so it never reaches the lookup. `on_failure` passes `--status failed`, so it never reaches the lookup either (Safety Invariant 6).

- [ ] **Step 4: Confirm the diff touches only the five planned files**

Run: `git diff --stat origin/main HEAD -- scripts/ tests/ workflows/ entrypoint.sh scheduler.sh config/ docs/configuration.md docs/features.md docs/run-lifecycle.md docs/scheduler.md`

Expected: only `scripts/factory_core/run_record.py`, `tests/test_run_record.py`, `docs/configuration.md`, `docs/features.md` and `docs/run-lifecycle.md` are listed. Nothing under `workflows/`, nothing in `entrypoint.sh`, `scheduler.sh`, `config/`, `scripts/factory_core/breaker.py` or `scripts/factory_core/cost_report.py`.

- [ ] **Step 5: Confirm the breaker stays independent of outcome (Safety Invariant 1)**

Run: `grep -nE "produced_ungated|clean_abort|_compute_outcome|harness_economics|\"outcome\"|outcome\[|outcome\.state" scripts/factory_core/breaker.py scheduler.sh || echo "no references"`

Expected output: `no references`. Do not grep for the bare word `run_record`. `breaker.py` legitimately imports `run_record` for `append_stop_record` (`breaker.py:232`, `:241`), which writes audit rows and never reads `outcome`. Spec Safety Invariant 1's parenthetical, "no reference to … `run_record` anywhere in `breaker.py`", is inaccurate on that point, but the invariant itself still holds. Never edit `breaker.py` to make a grep pass.

No commit is needed for this task. Every change was committed in Tasks 1-6b.

---

## Self-review

- `**Issue:** #447` line is present directly under the title.
- No placeholders: every code step contains the exact code, and every run step has an exact command and its expected result.
- Spec coverage:
  - R1: the exact-dict legacy tests in Task 1, plus the parametrized `close`/`new`/`deconflict`/`fix-main` pin and the workflow-dir-ignored `close` pin in Task 5.
  - R2: Task 1 covers state and score; Task 2 covers `failure_spend` and `factory_cpm`; Task 1 covers the version bump.
  - R3: Task 4 resolves the workflow run id. Task 5 duplicates the existence check ahead of scoring and leaves the fold alone (Design decision 8). It covers the production split layout and the same-directory fallback, `pushed: false`, and malformed JSON (Design decision 9).
  - R4: Task 3 covers the fail-closed lookup. Task 5 covers the gating to completed refine/plan with no artifact and an end-to-end timeout. Task 1 keeps `_compute_outcome` a pure function. Task 5 adds the top-level `artifact_check` key and Task 6 replays it in backfill.
  - R5: Tasks 1-6.
  - R6: the Task 1 docstring.
  - Safety Invariants 1-8: Tasks 1-7. Invariant 7 (paused runs) holds by construction, because `cmd_assemble` is never called for paused runs and this plan does not touch `entrypoint.sh`.
- Doc citations: Task 6b re-maps every `run_record.py` line citation in `docs/configuration.md`, `docs/features.md` and `docs/run-lifecycle.md`.
- Follow-up, not in scope: because of the same directory split, `cmd_assemble`'s `push_result` fold (#473) never finds its file in production, so the `**Push:** FAILED` cost-report line never fires. The gate-stage files (`validation.md`, `conformance.md`, `review.md`) appear to be missed the same way: all 8 completed `fix`/`continue` records among the 40 most recent score `produced_ungated`. The `memory_trace` fold is not affected (27 of those 40 records carry it). Fixing these changes visible behaviour and belongs in its own ticket.
