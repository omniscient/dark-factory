# Implementation Plan — Per-Run Harness Economics Ledger and CPM Scorecard

**Issue:** omniscient/dark-factory#235
**Spec:** `docs/superpowers/specs/2026-07-16-harness-economics-ledger-cpm-design.md`
**Depends on / consumes:** #208 (merged — `scripts/factory_core/model_proxy.py`,
`request-ledger.jsonl`)

---

## Goal

Add a `harness_economics` block — deterministic outcome scoring, cost/tokens-per-task,
retry/failure spend, and Factory CPM — computed at the end of every run and attached to the
existing `run-record.json` (no new persisted file). Make `harness_economics` reachable on the
failure path (today `cmd_assemble` only runs on success). Add two new read-only/degrade-only
`run_record.py` subcommands (`issue-economics`, `backfill-economics`) that consume the same
computation without introducing a second source of truth.

## Architecture

All new logic lives in `scripts/factory_core/run_record.py`, reusing the module's existing
`fcntl.flock`-free-read style (it already reads `request-ledger.jsonl`'s sibling concepts via
plain `pathlib`/`json`, no new dependency). `harness_economics` computation is split into small
pure functions (`_compute_outcome`, `_wall_clock_seconds`, `_read_ledger_rows`,
`_compute_ledger_mechanics`, `_compute_retry_spend`, `_compute_harness_economics`) so
`cmd_assemble`, `cmd_backfill_economics`, and unit tests all call the same code — the module's
existing pattern of pure helpers (`_parse_archon_cost`, `_parse_artifact_stage`) wrapped by thin
`cmd_*` argparse handlers.

`run_record.py` does **not** import `model_proxy.py` (which pulls in `aiohttp` at module scope) —
the ledger's default path is re-derived from the same `MODEL_PROXY_LEDGER_PATH` env var as a
plain module-level constant, mirroring the existing `JSONL_PATH` pattern so tests can
`monkeypatch.setattr(rr, "LEDGER_PATH", ...)`.

`issue-economics` and `backfill-economics` are standalone CLI capabilities only — per the spec's
own Open Questions ("which future work actually calls it routinely is left to those tickets to
decide"), this ticket does not wire either into `entrypoint.sh` or any phase command. Their
`--artifacts-root`/`--ledger-path` are explicit CLI flags (no new env var), exercised directly
by unit tests against `tmp_path` fixtures.

`cli.py`'s existing `run-record` subparser (`scripts/factory_core/cli.py:142-144`) forwards
`args.run_record_args` via `argparse.REMAINDER` straight into `run_record.main()`'s own
subparser — the new `issue-economics`/`backfill-economics` subcommands and the `assemble`
`--status`/`--ledger-path` flags need **no `cli.py` change** at all.

## Tech Stack

Python 3.14 stdlib only (no new dependency). Bash for `entrypoint.sh` wiring. `pytest` for unit
tests, matching `tests/test_run_record.py`'s existing conventions exactly (module-level
`monkeypatch.setattr(rr, "X", ...)`, plain test-double `class _Args` helpers, no `capsys` — new
`cmd_*` wrappers are thin `print(json.dumps(...))` shells around pure functions that are tested
directly, matching `_parse_archon_cost`'s existing pure-function test style).

---

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/run_record.py` | Modified — named scoring constants, `_compute_outcome`, `_wall_clock_seconds`, `_read_ledger_rows`, `_compute_ledger_mechanics`, `_compute_retry_spend`, `_compute_harness_economics`, `cmd_assemble` (`--status`, `--ledger-path`, `harness_economics` key), `cmd_issue_economics` (new), `cmd_backfill_economics` (new), new subparsers |
| `tests/test_run_record.py` | Modified — new tests for all of the above |
| `entrypoint.sh` | Modified — `on_failure` gains a `run-record assemble --status failed` call; `post_cost_report()` renders one `harness_economics` line |
| `tests/test_cost_report_harness_economics.sh` | New — static/regression guard for the `post_cost_report()` line (mirrors `tests/test_cost_report_endpoint.sh`'s convention; not wired into `.github/workflows/ci.yml`, matching that file's own un-wired precedent) |

---

## Memory Context Applied

Checked `.archon/memory/codebase-patterns.md`, `dark-factory-ops.md`, `architecture.md` for
`[AVOID]`/`[FIX]` entries touching `scripts/factory_core/run_record.py`, `entrypoint.sh`, or
CLI/test conventions. Four `[PATTERN]` entries were found, none of which are anti-patterns to
avoid and none change this plan's task content:

- Spec/plan branch-copy pattern (#42) — applies to the *implement* phase copying
  `docs/superpowers/*` onto `feat/issue-N-*`, not to this plan's file set.
- Mermaid-diagram-ownership pattern (#174) — no diagrams are added by this ticket.
- Selective-memory-loading pattern (#149) — describes how `$MEMORY_CONTEXT` itself is built
  (already followed automatically by `load_memory_context.sh`), not something a task step here
  needs to implement.
- Two-dot vs three-dot `git diff` for OOS checks (#250) — a Publish-phase gate concern, not a
  task-implementation concern; no task below performs OOS scope verification.

No `[AVOID]` entries target this file set, so no task step needed a defensive rewrite.

---

## Task 1 — Named constants and the outcome-scoring core (`_compute_outcome`)

**Files:** `scripts/factory_core/run_record.py`, `tests/test_run_record.py`

1. Add failing tests to `tests/test_run_record.py` (append a new section after the existing
   `_parse_artifact_stage` tests, before `# assemble command`):

   ```python
   # ---------------------------------------------------------------------------
   # _compute_outcome
   # ---------------------------------------------------------------------------

   def test_outcome_failed_when_status_not_completed():
       out = rr._compute_outcome("failed", [])
       assert out["state"] == "failed"
       assert out["score"] == 0.0
       assert out["evidence"]["ungated"] is False

   def test_outcome_blocked_on_validation_fail():
       stages = [{"stage": "validation", "verdict": "FAIL"}]
       out = rr._compute_outcome("completed", stages)
       assert out["state"] == "blocked"
       assert out["score"] == 0.0

   def test_outcome_blocked_on_conformance_blocked():
       stages = [
           {"stage": "validation", "verdict": "PASS"},
           {"stage": "conformance", "verdict": "BLOCKED", "cycles": 3},
       ]
       out = rr._compute_outcome("completed", stages)
       assert out["state"] == "blocked"
       assert out["score"] == 0.0

   def test_outcome_blocked_on_review_blocked():
       stages = [{"stage": "review", "verdict": "BLOCKED", "blockers": 2}]
       out = rr._compute_outcome("completed", stages)
       assert out["state"] == "blocked"

   def test_outcome_produced_ungated_when_no_gate_stages():
       # e.g. a refine/plan run — conflict_resolution alone is not a gate stage.
       stages = [{"stage": "conflict_resolution", "verdict": "none"}]
       out = rr._compute_outcome("completed", stages)
       assert out["state"] == "produced_ungated"
       assert out["score"] == 1.0
       assert out["evidence"]["ungated"] is True

   def test_outcome_delivered_clean_zero_friction():
       stages = [
           {"stage": "validation", "verdict": "PASS"},
           {"stage": "conformance", "verdict": "PASS", "cycles": 0},
           {"stage": "review", "verdict": "PASS", "blockers": 0, "advisory": 0},
       ]
       out = rr._compute_outcome("completed", stages)
       assert out["state"] == "delivered_clean"
       assert out["score"] == 1.0
       assert out["evidence"]["penalties"] == []

   def test_outcome_delivered_with_findings_conformance_cycles():
       stages = [
           {"stage": "validation", "verdict": "PASS"},
           {"stage": "conformance", "verdict": "PASS", "cycles": 2},
           {"stage": "review", "verdict": "PASS", "blockers": 0, "advisory": 0},
       ]
       out = rr._compute_outcome("completed", stages)
       assert out["state"] == "delivered_with_findings"
       assert out["score"] == pytest.approx(0.80)  # 1.0 - 0.10*2
       assert out["evidence"]["penalties"] == [
           {"reason": "conformance_cycles", "count": 2, "delta": -0.20}
       ]

   def test_outcome_delivered_with_findings_review_advisory():
       stages = [
           {"stage": "review", "verdict": "PASS", "blockers": 0, "advisory": 3},
       ]
       out = rr._compute_outcome("completed", stages)
       assert out["state"] == "delivered_with_findings"
       assert out["score"] == pytest.approx(0.85)  # 1.0 - 0.05*3

   def test_outcome_score_floor_at_quarter():
       stages = [
           {"stage": "conformance", "verdict": "PASS", "cycles": 20},
           {"stage": "review", "verdict": "PASS", "advisory": 20},
       ]
       out = rr._compute_outcome("completed", stages)
       assert out["score"] == 0.25

   def test_outcome_evidence_includes_gate_stages_only():
       stages = [
           {"stage": "validation", "verdict": "PASS"},
           {"stage": "conflict_resolution", "verdict": "RESOLVED"},
       ]
       out = rr._compute_outcome("completed", stages)
       names = [s["stage"] for s in out["evidence"]["gate_stages"]]
       assert names == ["validation"]
   ```

2. Run to confirm failure (function does not exist yet):

   ```bash
   python -m pytest tests/test_run_record.py -k test_outcome -v
   ```

   Expected: every `test_outcome_*` fails with `AttributeError: module ... has no attribute
   '_compute_outcome'`.

3. Implement in `scripts/factory_core/run_record.py`. Add near the top, after the existing
   `SEQ_URL` constant (line 20):

   ```python
   POLICY_VERSION = "1.0"
   GATE_STAGE_NAMES = ("validation", "conformance", "review")
   _BLOCKING_VERDICTS = {
       "validation": {"FAIL"},
       "conformance": {"BLOCKED"},
       "review": {"BLOCKED"},
   }
   CONFORMANCE_CYCLE_PENALTY = 0.10
   REVIEW_ADVISORY_PENALTY = 0.05
   SCORE_FLOOR = 0.25
   ```

   Add the function itself after `_parse_artifact_stage` (after line 258, before
   `def cmd_assemble`):

   ```python
   def _compute_outcome(status: str, stages: list) -> dict:
       """Deterministic outcome.state / outcome.score policy (policy_version 1.0).

       failed/blocked always score 0.0 regardless of token spend — the mechanical
       enforcement of "don't reward raw token reduction over correctness." See
       docs/superpowers/specs/2026-07-16-harness-economics-ledger-cpm-design.md
       "Outcome-score policy".
       """
       stage_by_name = {s["stage"]: s for s in stages if s.get("stage") in GATE_STAGE_NAMES}
       gate_stages = [stage_by_name[name] for name in GATE_STAGE_NAMES if name in stage_by_name]

       if status != "completed":
           state = "failed"
       elif any(
           stage_by_name.get(name, {}).get("verdict") in _BLOCKING_VERDICTS[name]
           for name in GATE_STAGE_NAMES
       ):
           state = "blocked"
       elif not gate_stages:
           state = "produced_ungated"
       else:
           cycles = stage_by_name.get("conformance", {}).get("cycles") or 0
           advisory = stage_by_name.get("review", {}).get("advisory") or 0
           state = "delivered_with_findings" if (cycles or advisory) else "delivered_clean"

       penalties = []
       if state in ("failed", "blocked"):
           score = 0.0
       elif state == "produced_ungated":
           score = 1.0
       else:
           score = 1.0
           cycles = stage_by_name.get("conformance", {}).get("cycles") or 0
           if cycles:
               delta = round(-CONFORMANCE_CYCLE_PENALTY * cycles, 4)
               score += delta
               penalties.append({"reason": "conformance_cycles", "count": cycles, "delta": delta})
           advisory = stage_by_name.get("review", {}).get("advisory") or 0
           if advisory:
               delta = round(-REVIEW_ADVISORY_PENALTY * advisory, 4)
               score += delta
               penalties.append({"reason": "review_advisory", "count": advisory, "delta": delta})
           score = max(SCORE_FLOOR, min(1.0, round(score, 4)))

       return {
           "state": state,
           "score": score,
           "evidence": {
               "status": status,
               "gate_stages": gate_stages,
               "penalties": penalties,
               "ungated": state == "produced_ungated",
           },
       }
   ```

4. Run to confirm pass:

   ```bash
   python -m pytest tests/test_run_record.py -k test_outcome -v
   ```

   Expected: all `test_outcome_*` tests pass (10 passed).

5. Run the full existing suite to confirm no regressions:

   ```bash
   python -m pytest tests/test_run_record.py -v
   ```

   Expected: all prior tests (record/assemble/parse) still pass — `_compute_outcome` is
   additive, `cmd_assemble` is untouched in this task.

6. Commit:

   ```bash
   git add scripts/factory_core/run_record.py tests/test_run_record.py
   git commit -m "feat(economics): add deterministic outcome-score policy core"
   ```

---

## Task 2 — Wall-clock and ledger-row reading (`_wall_clock_seconds`, `_read_ledger_rows`, mechanics)

**Files:** `scripts/factory_core/run_record.py`, `tests/test_run_record.py`

1. Add failing tests, appended after the Task 1 tests:

   ```python
   # ---------------------------------------------------------------------------
   # _wall_clock_seconds
   # ---------------------------------------------------------------------------

   def test_wall_clock_seconds_basic():
       secs = rr._wall_clock_seconds("2026-06-12T04:00:00Z", "2026-06-12T04:05:30Z")
       assert secs == 330

   def test_wall_clock_seconds_malformed_returns_zero():
       assert rr._wall_clock_seconds("not-a-date", "2026-06-12T04:00:00Z") == 0
       assert rr._wall_clock_seconds("", "") == 0

   def test_wall_clock_seconds_never_negative():
       # started_at after completed_at (clock skew) must not go negative.
       secs = rr._wall_clock_seconds("2026-06-12T04:05:00Z", "2026-06-12T04:00:00Z")
       assert secs == 0

   # ---------------------------------------------------------------------------
   # _read_ledger_rows
   # ---------------------------------------------------------------------------

   def _ledger_row(run_id="run-1", status=200, in_tok=10, out_tok=5):
       return {
           "run_id": run_id, "status": status,
           "gen_ai.usage.input_tokens": in_tok, "gen_ai.usage.output_tokens": out_tok,
           "gen_ai.usage.cache_read_input_tokens": 0, "gen_ai.usage.cache_creation_input_tokens": 0,
           "tool_bytes": 100, "system_bytes": 50, "largest_tools": [{"name": "Bash", "bytes": 40}],
       }

   def test_read_ledger_rows_missing_file_not_available(tmp_path):
       rows, available = rr._read_ledger_rows("run-1", tmp_path / "missing.jsonl")
       assert rows == []
       assert available is False

   def test_read_ledger_rows_filters_by_run_id(tmp_path):
       path = tmp_path / "request-ledger.jsonl"
       path.write_text(
           json.dumps(_ledger_row("run-1")) + "\n" + json.dumps(_ledger_row("run-2")) + "\n"
       )
       rows, available = rr._read_ledger_rows("run-1", path)
       assert available is True
       assert len(rows) == 1
       assert rows[0]["run_id"] == "run-1"

   def test_read_ledger_rows_includes_rotation_backups(tmp_path):
       path = tmp_path / "request-ledger.jsonl"
       path.write_text(json.dumps(_ledger_row("run-1", in_tok=1)) + "\n")
       backup = tmp_path / "request-ledger.jsonl.1"
       backup.write_text(json.dumps(_ledger_row("run-1", in_tok=2)) + "\n")
       rows, available = rr._read_ledger_rows("run-1", path)
       assert available is True
       assert len(rows) == 2

   def test_read_ledger_rows_skips_malformed_lines(tmp_path):
       path = tmp_path / "request-ledger.jsonl"
       path.write_text("not json\n" + json.dumps(_ledger_row("run-1")) + "\n")
       rows, available = rr._read_ledger_rows("run-1", path)
       assert len(rows) == 1

   # ---------------------------------------------------------------------------
   # _compute_retry_spend / _compute_ledger_mechanics
   # ---------------------------------------------------------------------------

   def test_compute_retry_spend_counts_failed_rows_only():
       rows = [_ledger_row(status=200, in_tok=10, out_tok=5), _ledger_row(status=529, in_tok=7, out_tok=3)]
       spend = rr._compute_retry_spend(rows)
       assert spend == {"tokens": 10, "request_count": 1}

   def test_compute_retry_spend_zero_when_no_failures():
       rows = [_ledger_row(status=200)]
       assert rr._compute_retry_spend(rows) == {"tokens": 0, "request_count": 0}

   def test_compute_ledger_mechanics_cache_hit_ratio():
       rows = [
           {**_ledger_row(in_tok=100), "gen_ai.usage.cache_read_input_tokens": 40},
       ]
       mech = rr._compute_ledger_mechanics(rows)
       assert mech["cache_hit_ratio"] == pytest.approx(0.4)
       assert mech["tool_schema_overhead_bytes"] == 100
       assert mech["largest_tools"][0]["name"] == "Bash"

   def test_compute_ledger_mechanics_empty_rows():
       mech = rr._compute_ledger_mechanics([])
       assert mech["cache_hit_ratio"] is None
       assert mech["tool_schema_overhead_bytes"] == 0
       assert mech["largest_tools"] == []
   ```

2. Run to confirm failure:

   ```bash
   python -m pytest tests/test_run_record.py -k "wall_clock or read_ledger_rows or retry_spend or ledger_mechanics" -v
   ```

   Expected: `AttributeError` for each missing function.

3. Implement in `scripts/factory_core/run_record.py`, added after `_compute_outcome`:

   ```python
   LEDGER_PATH = pathlib.Path(
       os.environ.get("MODEL_PROXY_LEDGER_PATH", "/var/lib/dark-factory/request-ledger.jsonl")
   )


   def _wall_clock_seconds(started_at: str, completed_at: str) -> int:
       try:
           start = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ")
           end = datetime.strptime(completed_at, "%Y-%m-%dT%H:%M:%SZ")
           return max(0, int((end - start).total_seconds()))
       except (ValueError, TypeError):
           return 0


   def _iter_ledger_paths(ledger_path: pathlib.Path):
       yield ledger_path
       for i in (1, 2, 3):
           backup = ledger_path.with_suffix(ledger_path.suffix + f".{i}")
           if backup.exists():
               yield backup


   def _read_ledger_rows(run_id: str, ledger_path: pathlib.Path) -> tuple:
       """Scan the live ledger plus rotation backups for rows matching run_id.

       Returns (rows, ledger_available) — ledger_available distinguishes "the ledger
       exists and genuinely has zero rows for this run" from "no ledger data at all"
       (opt-in model-proxy was never enabled). See the spec's "Graceful degradation"
       section.
       """
       rows = []
       available = False
       for path in _iter_ledger_paths(ledger_path):
           if not path.exists():
               continue
           available = True
           try:
               for line in path.read_text(encoding="utf-8").splitlines():
                   line = line.strip()
                   if not line:
                       continue
                   try:
                       row = json.loads(line)
                   except json.JSONDecodeError:
                       continue
                   if row.get("run_id") == run_id:
                       rows.append(row)
           except OSError:
               continue
       return rows, available


   def _compute_retry_spend(rows: list) -> dict:
       """Ledger retry_count is always 0 (single-pass forwarder) — retries are
       reconstructed from rows with a failure status instead. See model_proxy.py's
       build_ledger_row docstring.
       """
       failed = [r for r in rows if isinstance(r.get("status"), int) and r["status"] >= 400]
       tokens = sum(
           r.get("gen_ai.usage.input_tokens", 0) + r.get("gen_ai.usage.output_tokens", 0)
           for r in failed
       )
       return {"tokens": tokens, "request_count": len(failed)}


   def _compute_ledger_mechanics(rows: list) -> dict:
       total_input = sum(r.get("gen_ai.usage.input_tokens", 0) for r in rows)
       total_cache_read = sum(r.get("gen_ai.usage.cache_read_input_tokens", 0) for r in rows)
       cache_hit_ratio = (total_cache_read / total_input) if total_input else None
       tool_schema_overhead_bytes = sum(r.get("tool_bytes", 0) for r in rows)
       system_bytes_vals = [r.get("system_bytes", 0) for r in rows if r.get("system_bytes")]
       largest_tools = []
       for r in rows:
           largest_tools.extend(r.get("largest_tools") or [])
       largest_tools = sorted(largest_tools, key=lambda t: t.get("bytes", 0), reverse=True)[:5]
       return {
           "cache_hit_ratio": cache_hit_ratio,
           "tool_schema_overhead_bytes": tool_schema_overhead_bytes,
           "system_prompt_bytes": max(system_bytes_vals) if system_bytes_vals else 0,
           "largest_tools": largest_tools,
       }
   ```

4. Run to confirm pass:

   ```bash
   python -m pytest tests/test_run_record.py -k "wall_clock or read_ledger_rows or retry_spend or ledger_mechanics" -v
   ```

   Expected: all pass (12 passed).

5. Full-suite regression check:

   ```bash
   python -m pytest tests/test_run_record.py -v
   ```

6. Commit:

   ```bash
   git add scripts/factory_core/run_record.py tests/test_run_record.py
   git commit -m "feat(economics): add ledger-row reading and retry/mechanics aggregation"
   ```

---

## Task 3 — `_compute_harness_economics` and wiring into `cmd_assemble`

**Files:** `scripts/factory_core/run_record.py`, `tests/test_run_record.py`

1. Add failing tests, appended after the existing `# assemble command` section's tests (after
   `test_assemble_emits_jsonl_per_stage`, before the memory-trace tests):

   ```python
   # ---------------------------------------------------------------------------
   # harness_economics (via cmd_assemble)
   # ---------------------------------------------------------------------------

   class _AssembleArgs:
       run_id = "abc123"
       issue = 333
       intent = "new"
       started_at = "2026-06-12T04:00:00Z"
       archon_cost_json = None
       status = "completed"
       ledger_path = None

       def __init__(self, artifacts_dir, out_file):
           self.artifacts_dir = str(artifacts_dir)
           self.out_file = str(out_file)


   def test_assemble_attaches_harness_economics_ungated(tmp_path, monkeypatch):
       monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
       monkeypatch.setattr(rr, "_post_seq", lambda r: None)
       monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

       out = tmp_path / "run-record.json"
       args = _AssembleArgs(tmp_path, out)  # no validation/conformance/review artifacts
       rr.cmd_assemble(args)

       rec = json.loads(out.read_text())
       he = rec["harness_economics"]
       assert he["policy_version"] == rr.POLICY_VERSION
       assert he["outcome"]["state"] == "produced_ungated"
       assert he["outcome"]["score"] == 1.0
       assert he["ledger_available"] is False
       assert he["ledger_rows_correlated"] == 0
       assert he["ledger_mechanics"] is None
       assert he["retry_spend"] == {"tokens": None, "request_count": None}
       assert he["failure_spend"] == {"tokens": 0, "basis": "retry_only"}

   def test_assemble_harness_economics_delivered_clean_with_cost(tmp_path, monkeypatch):
       monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
       monkeypatch.setattr(rr, "_post_seq", lambda r: None)
       monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

       (tmp_path / "validation.md").write_text("STATUS: PASS\n")
       (tmp_path / "conformance.md").write_text("STATUS: PASS\nCYCLES: 0\n")
       (tmp_path / "review.md").write_text("STATUS: PASS\nBLOCKERS: 0\nADVISORY: 0\n")

       cost_json = tmp_path / "cost.json"
       cost_json.write_text(json.dumps({
           "runId": "abc123",
           "nodes": [{"nodeId": "implement", "inputTokens": 800000, "outputTokens": 200000,
                      "costUsd": 2.5, "durationMs": 1000, "modelUsage": {}}],
           "totals": {"costUsd": 2.5, "inputTokens": 800000, "outputTokens": 200000},
       }))

       out = tmp_path / "run-record.json"
       args = _AssembleArgs(tmp_path, out)
       args.archon_cost_json = str(cost_json)
       rr.cmd_assemble(args)

       rec = json.loads(out.read_text())
       he = rec["harness_economics"]
       assert he["outcome"]["state"] == "delivered_clean"
       assert he["cost_per_task"] == pytest.approx(2.5)
       assert he["tokens_per_task"] == 1_000_000
       assert he["factory_cpm"] == pytest.approx(1.0)  # score 1.0 * 1e6 / 1_000_000 tokens

   def test_assemble_harness_economics_tokens_zero_cpm_null(tmp_path, monkeypatch):
       monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
       monkeypatch.setattr(rr, "_post_seq", lambda r: None)
       monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

       out = tmp_path / "run-record.json"
       args = _AssembleArgs(tmp_path, out)
       rr.cmd_assemble(args)

       rec = json.loads(out.read_text())
       assert rec["harness_economics"]["factory_cpm"] is None

   def test_assemble_harness_economics_status_failed(tmp_path, monkeypatch):
       monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
       monkeypatch.setattr(rr, "_post_seq", lambda r: None)
       monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

       out = tmp_path / "run-record.json"
       args = _AssembleArgs(tmp_path, out)
       args.status = "failed"
       rr.cmd_assemble(args)

       rec = json.loads(out.read_text())
       assert rec["status"] == "failed"
       assert rec["harness_economics"]["outcome"]["state"] == "failed"
       assert rec["harness_economics"]["outcome"]["score"] == 0.0

   def test_assemble_harness_economics_failure_spend_whole_run_when_blocked(tmp_path, monkeypatch):
       monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
       monkeypatch.setattr(rr, "_post_seq", lambda r: None)
       monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

       (tmp_path / "validation.md").write_text("STATUS: FAIL\n")

       cost_json = tmp_path / "cost.json"
       cost_json.write_text(json.dumps({
           "runId": "abc123",
           "nodes": [{"nodeId": "implement", "inputTokens": 100, "outputTokens": 50,
                      "costUsd": 0.01, "durationMs": 1000, "modelUsage": {}}],
           "totals": {"costUsd": 0.01, "inputTokens": 100, "outputTokens": 50},
       }))

       out = tmp_path / "run-record.json"
       args = _AssembleArgs(tmp_path, out)
       args.archon_cost_json = str(cost_json)
       rr.cmd_assemble(args)

       rec = json.loads(out.read_text())
       he = rec["harness_economics"]
       assert he["outcome"]["state"] == "blocked"
       assert he["failure_spend"] == {"tokens": 150, "basis": "whole_run"}

   def test_assemble_harness_economics_with_ledger_rows(tmp_path, monkeypatch):
       monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
       monkeypatch.setattr(rr, "_post_seq", lambda r: None)
       ledger = tmp_path / "request-ledger.jsonl"
       ledger.write_text(
           json.dumps({
               "run_id": "abc123", "status": 200,
               "gen_ai.usage.input_tokens": 10, "gen_ai.usage.output_tokens": 5,
               "gen_ai.usage.cache_read_input_tokens": 2, "tool_bytes": 30, "system_bytes": 10,
               "largest_tools": [],
           }) + "\n" +
           json.dumps({
               "run_id": "abc123", "status": 529,
               "gen_ai.usage.input_tokens": 7, "gen_ai.usage.output_tokens": 3,
               "gen_ai.usage.cache_read_input_tokens": 0, "tool_bytes": 20, "system_bytes": 10,
               "largest_tools": [],
           }) + "\n"
       )
       monkeypatch.setattr(rr, "LEDGER_PATH", ledger)

       out = tmp_path / "run-record.json"
       args = _AssembleArgs(tmp_path, out)
       rr.cmd_assemble(args)

       rec = json.loads(out.read_text())
       he = rec["harness_economics"]
       assert he["ledger_available"] is True
       assert he["ledger_rows_correlated"] == 2
       assert he["retry_spend"] == {"tokens": 10, "request_count": 1}
       assert he["ledger_mechanics"]["cache_hit_ratio"] == pytest.approx(2 / 17)
   ```

2. Run to confirm failure:

   ```bash
   python -m pytest tests/test_run_record.py -k harness_economics -v
   ```

   Expected: `KeyError: 'harness_economics'` for every new test (key not attached yet).

3. Implement `_compute_harness_economics` in `scripts/factory_core/run_record.py`, added after
   `_compute_ledger_mechanics`:

   ```python
   def _compute_harness_economics(
       *, run_id: str, status: str, stages: list, totals: dict,
       started_at: str, completed_at: str, ledger_path: pathlib.Path,
   ) -> dict:
       tokens_in = totals.get("gen_ai.usage.input_tokens", 0)
       tokens_out = totals.get("gen_ai.usage.output_tokens", 0)
       tokens_per_task = tokens_in + tokens_out
       cost_per_task = totals.get("cost_usd", 0.0)

       outcome = _compute_outcome(status, stages)
       factory_cpm = (
           outcome["score"] * 1_000_000 / tokens_per_task if tokens_per_task > 0 else None
       )

       rows, ledger_available = _read_ledger_rows(run_id, ledger_path)
       if ledger_available:
           retry_spend = _compute_retry_spend(rows)
           ledger_mechanics = _compute_ledger_mechanics(rows)
       else:
           retry_spend = {"tokens": None, "request_count": None}
           ledger_mechanics = None

       if outcome["state"] in ("failed", "blocked"):
           failure_spend = {"tokens": tokens_per_task, "basis": "whole_run"}
       else:
           failure_spend = {"tokens": retry_spend["tokens"] or 0, "basis": "retry_only"}

       return {
           "policy_version": POLICY_VERSION,
           "cost_per_task": cost_per_task,
           "tokens_per_task": tokens_per_task,
           "wall_clock_seconds": _wall_clock_seconds(started_at, completed_at),
           "outcome": outcome,
           "factory_cpm": factory_cpm,
           "retry_spend": retry_spend,
           "failure_spend": failure_spend,
           "ledger_available": ledger_available,
           "ledger_rows_correlated": len(rows),
           "ledger_mechanics": ledger_mechanics,
       }
   ```

4. Wire into `cmd_assemble` — replace the hardcoded `"status": "completed",` (line 291) and add
   the `harness_economics` key after `totals` is built:

   ```python
   run_record = {
       "run_id": args.run_id,
       "issue_number": args.issue,
       "intent": args.intent,
       "started_at": args.started_at or _timestamp(),
       "completed_at": _timestamp(),
       "status": args.status,
       "stages": stages,
       "nodes": nodes,
       "artifacts": artifacts,
       "totals": {
           "gen_ai.usage.input_tokens": totals_in,
           "gen_ai.usage.output_tokens": totals_out,
           "cost_usd": totals_cost,
       },
   }
   run_record["harness_economics"] = _compute_harness_economics(
       run_id=args.run_id,
       status=run_record["status"],
       stages=stages,
       totals=run_record["totals"],
       started_at=run_record["started_at"],
       completed_at=run_record["completed_at"],
       ledger_path=pathlib.Path(args.ledger_path) if args.ledger_path else LEDGER_PATH,
   )
   ```

5. Add the two new argparse flags to the `assemble` subparser (after `a.add_argument("--out-file",
   required=True)`, line 358):

   ```python
   a.add_argument("--status", default="completed")
   a.add_argument("--ledger-path", default=None)
   ```

6. `cmd_assemble` now reads `LEDGER_PATH` (via `_compute_harness_economics`) on every call.
   The pre-existing assemble/memory-trace tests never set `rr.LEDGER_PATH`, so without this
   step they'd fall through to the real module default
   (`/var/lib/dark-factory/request-ledger.jsonl`) — harmless in a clean sandbox (missing file
   → `ledger_available=False`), but a non-hermetic dependency on host state that must be
   closed off explicitly. Add `monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path /
   "no-ledger.jsonl")` to each of these eight existing test functions, right alongside their
   existing `monkeypatch.setattr(rr, "JSONL_PATH", ...)` line:
   - `test_assemble_builds_run_record`
   - `test_assemble_missing_artifacts_skipped`
   - `test_assemble_incorporates_archon_cost`
   - `test_assemble_emits_jsonl_per_stage`
   - `test_assemble_picks_up_memory_trace`
   - `test_assemble_no_memory_trace_key_when_absent`
   - `test_assemble_tolerates_malformed_memory_trace`
   - `test_assemble_tolerates_unreadable_memory_trace`

   For example, `test_assemble_builds_run_record` becomes:

   ```python
   def test_assemble_builds_run_record(tmp_path, monkeypatch):
       monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
       monkeypatch.setattr(rr, "_post_seq", lambda r: None)
       monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

       (tmp_path / "validation.md").write_text("STATUS: PASS\n")
       ...  # rest of the test body unchanged
   ```

   Apply the same one-line addition to the other seven functions listed above; none of their
   existing assertions change.

7. Run to confirm pass:

   ```bash
   python -m pytest tests/test_run_record.py -k harness_economics -v
   ```

   Expected: all 6 new tests pass.

8. Full-suite regression check (the pre-existing `_AssembleArgs` class in the `# assemble
   command` section was replaced by the version added in step 1 above — Python keeps only the
   last class definition of that name in the module, so no duplicate-class conflict; the earlier
   assemble tests (`test_assemble_builds_run_record` etc.) now use the new class with its added
   `status`/`ledger_path` defaults, and step 6's `LEDGER_PATH` monkeypatch, which do not change
   their existing assertions):

   ```bash
   python -m pytest tests/test_run_record.py -v
   ```

   Expected: all tests pass (0 failures).

9. Commit:

   ```bash
   git add scripts/factory_core/run_record.py tests/test_run_record.py
   git commit -m "feat(economics): compute and attach harness_economics in cmd_assemble"
   ```

---

## Task 4 — Wire `on_failure` and extend `post_cost_report()`

**Files:** `entrypoint.sh`, `tests/test_cost_report_harness_economics.sh` (new)

1. Write the failing test first — `tests/test_cost_report_harness_economics.sh` (static guard,
   mirroring `tests/test_cost_report_endpoint.sh`'s convention: behavioral testing of
   `post_cost_report()` is impractical since it shells out to `gh`/`jq`/`bc`):

   ```bash
   #!/usr/bin/env bash
   # Regression guard: on_failure must assemble a run-record (so outcome.state=="failed" is
   # reachable), and post_cost_report() must render harness_economics without requiring the
   # key to exist (older run-record.json files predate this field).
   #
   # Run: bash tests/test_cost_report_harness_economics.sh
   set -uo pipefail

   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
   ENTRYPOINT="${SCRIPT_DIR}/../entrypoint.sh"
   FAIL=0

   # Extract each function's body by ranging to the NEXT top-level function header, not a
   # fixed line-count window (`grep -A N` can miss content near the end of a long function)
   # and not `/^}/` (both functions build multi-line bash strings containing a literal
   # column-0 `}` as string content — e.g. post_cost_report's SAVINGS_BLOCK — which a naive
   # `/^}/` sentinel matches too early, truncating the body). Current function order in
   # entrypoint.sh (verified via `grep -n '^[a-zA-Z_][a-zA-Z0-9_]*() {$'`):
   # ... post_cost_report() -> on_failure() -> _resolve_merge_conflicts() ...
   ON_FAILURE_BODY=$(sed -n '/^on_failure() {$/,/^_resolve_merge_conflicts() {$/p' "$ENTRYPOINT")
   POST_COST_REPORT_BODY=$(sed -n '/^post_cost_report() {$/,/^on_failure() {$/p' "$ENTRYPOINT")

   # on_failure must call `run-record assemble --status failed` so a failed run gets a
   # run-record.json (previously only `run-record record` ran on the failure path).
   if echo "$ON_FAILURE_BODY" | grep -q -- '--status failed'; then
     echo "  PASS: on_failure calls run-record assemble --status failed"
   else
     echo "  FAIL: on_failure does not assemble a failed run-record"
     FAIL=1
   fi

   # post_cost_report must read harness_economics with a `//` (jq alternative-operator)
   # fallback, so a run-record.json without the key does not break rendering.
   if echo "$POST_COST_REPORT_BODY" | grep -q 'harness_economics'; then
     echo "  PASS: post_cost_report references harness_economics"
   else
     echo "  FAIL: post_cost_report does not render harness_economics"
     FAIL=1
   fi

   if echo "$POST_COST_REPORT_BODY" | grep 'harness_economics' | grep -q '//'; then
     echo "  PASS: harness_economics lookups use jq // fallback (absent-tolerant)"
   else
     echo "  FAIL: harness_economics lookups are not absent-tolerant"
     FAIL=1
   fi

   echo ""
   [ "$FAIL" -eq 0 ] && echo "OK" || echo "FAILED"
   [ "$FAIL" -eq 0 ]
   ```

2. Run to confirm failure:

   ```bash
   bash tests/test_cost_report_harness_economics.sh
   ```

   Expected: two `FAIL` lines (neither `on_failure` nor `post_cost_report` reference
   `harness_economics`/`--status failed` yet), exit code 1.

3. Implement in `entrypoint.sh`. First, `on_failure()` (after the existing `run-record record`
   call, entrypoint.sh:480-485) — add a second, independently best-effort assemble call so a
   failed run gets a `run-record.json` with `outcome.state == "failed"`:

   ```bash
   on_failure() {
     local EXIT_CODE=$?
     # Capture partial-failure record before any other action (non-fatal)
     # TARGET-PATH: cli.py resolves under dark-factory/ in the clone — target's own copy until
     # P3 cleanup, baked self-contained fallback copy afterwards (df#14)
     python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record record \
       --run-id "${RUN_ID:-unknown}" \
       --issue "${ISSUE_NUM:-0}" \
       --intent "${INTENT:-unknown}" \
       --stage "failed" \
       --verdict "failed" || true
     # Assemble a full run-record.json on the failure path too, so harness_economics'
     # outcome.state == "failed" (score 0.0) is actually reachable — previously only the
     # bare stage event above was written and cmd_assemble never ran on failure.
     if [ -n "${ARTIFACTS_DIR:-}" ]; then
       local FAIL_COST_JSON
       FAIL_COST_JSON=$(mktemp)
       archon workflow cost --last --json --quiet > "$FAIL_COST_JSON" 2>/dev/null || true
       python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record assemble \
         --run-id "${RUN_ID:-unknown}" \
         --issue "${ISSUE_NUM:-0}" \
         --intent "${INTENT:-unknown}" \
         --started-at "${RUN_STARTED_AT:-}" \
         --artifacts-dir "$ARTIFACTS_DIR" \
         --archon-cost-json "$FAIL_COST_JSON" \
         --status failed \
         --out-file "$ARTIFACTS_DIR/run-record.json" || true
       rm -f "$FAIL_COST_JSON"
     fi
     if [ -n "${ISSUE_NUM:-}" ] && [ "$INTENT" != "close" ]; then
   ```

   (The remainder of `on_failure()` — the `refine`/`plan`/`deconflict` branch, the
   `set_board_status`/comment branch, and the trailing `post_cost_report || true` — is
   unchanged; `post_cost_report` now finds a `run-record.json` on the failure path too, which
   is the entire point of this wiring.)

4. Extend `post_cost_report()` — add a `harness_economics` extraction alongside the existing
   `RUN_STATUS`/`TOTAL_COST` extraction (entrypoint.sh:317-321), and render it into the comment
   body. Add after the existing `TOTAL_OUT=$(...)` line:

   ```bash
   local HE_CPM HE_STATE HE_SCORE ECONOMICS_LINE=""
   HE_CPM=$(jq -r '.harness_economics.factory_cpm // empty' "$RUN_RECORD_FILE" 2>/dev/null || true)
   HE_STATE=$(jq -r '.harness_economics.outcome.state // empty' "$RUN_RECORD_FILE" 2>/dev/null || true)
   HE_SCORE=$(jq -r '.harness_economics.outcome.score // empty' "$RUN_RECORD_FILE" 2>/dev/null || true)
   if [ -n "$HE_STATE" ]; then
     local HE_CPM_FMT="n/a"
     [ -n "$HE_CPM" ] && HE_CPM_FMT=$(printf '%.0f' "$HE_CPM" 2>/dev/null || echo "$HE_CPM")
     ECONOMICS_LINE="**Factory CPM:** ${HE_CPM_FMT} | **Outcome:** ${HE_STATE} (score ${HE_SCORE:-n/a})"
   fi
   ```

   Then insert `${ECONOMICS_LINE}` into `BODY`, right after the Subtotal row
   (entrypoint.sh:452, before the trailing `---`):

   ```bash
     BODY="${COST_MARKER}
   <!-- cumulative: cost=${CUM_COST} in=${CUM_IN} out=${CUM_OUT} -->
   ## Dark Factory — Cost Report

   **${RUN_COUNT} run(s) — Total: \$${CUM_COST} ($(fmt_tokens "$CUM_IN") in / $(fmt_tokens "$CUM_OUT") out)**

   ${PRIOR_RUNS}
   ### Run: ${TIMESTAMP} (${INTENT:-fix}, ${RUN_STATUS})
   ${SAVINGS_BLOCK}
   | Step | Model | In tokens | Out tokens | Cost | Duration |
   |------|-------|-----------|------------|------|----------|
   ${RUN_ROWS}
   | **Subtotal** | | **$(fmt_tokens "$TOTAL_IN")** | **$(fmt_tokens "$TOTAL_OUT")** | **\$${TOTAL_COST}** | |
   ${ECONOMICS_LINE:+
   ${ECONOMICS_LINE}}

   ---
   *Updated by ${FACTORY_PRODUCT_NAME} Dark Factory*"
   ```

5. Run to confirm pass:

   ```bash
   bash tests/test_cost_report_harness_economics.sh
   ```

   Expected: three `PASS` lines, `OK`, exit code 0.

6. Regression-check the pre-existing static guard (same file, unrelated assertions, must still
   pass since this task did not touch the single-comment endpoint paths):

   ```bash
   bash tests/test_cost_report_endpoint.sh
   ```

   Expected: `OK`.

7. Sanity-check `entrypoint.sh` bash syntax:

   ```bash
   bash -n entrypoint.sh
   ```

   Expected: no output (valid syntax).

8. Commit:

   ```bash
   git add entrypoint.sh tests/test_cost_report_harness_economics.sh
   git commit -m "fix(economics): assemble run-record on failure path; render CPM in cost report"
   ```

   Note: `tests/test_cost_report_harness_economics.sh` is intentionally **not** added to
   `.github/workflows/ci.yml`'s explicit bash-test list — matching the existing, un-wired
   precedent already set by its sibling `tests/test_cost_report_endpoint.sh` (verified via
   `grep -n test_cost_report .github/workflows/ci.yml`, no match).

---

## Task 5 — `issue-economics` read-only cross-run/issue/phase rollup

**Files:** `scripts/factory_core/run_record.py`, `tests/test_run_record.py`

1. Add failing tests, appended after the Task 3 tests:

   ```python
   # ---------------------------------------------------------------------------
   # issue-economics
   # ---------------------------------------------------------------------------

   def test_build_issue_economics_groups_by_run_issue_phase(tmp_path):
       ledger = tmp_path / "request-ledger.jsonl"
       ledger.write_text(
           json.dumps({"run_id": "run-1", "issue_number": 235, "intent": "implement",
                       "stage": "implement", "status": 200,
                       "gen_ai.usage.input_tokens": 100, "gen_ai.usage.output_tokens": 50}) + "\n" +
           json.dumps({"run_id": "run-1", "issue_number": 235, "intent": "implement",
                       "stage": "implement", "status": 529,
                       "gen_ai.usage.input_tokens": 20, "gen_ai.usage.output_tokens": 5}) + "\n" +
           json.dumps({"run_id": "run-2", "issue_number": 235, "intent": "plan",
                       "stage": "plan", "status": 200,
                       "gen_ai.usage.input_tokens": 10, "gen_ai.usage.output_tokens": 5}) + "\n" +
           json.dumps({"run_id": "run-3", "issue_number": 999, "intent": "implement",
                       "stage": "implement", "status": 200,
                       "gen_ai.usage.input_tokens": 999, "gen_ai.usage.output_tokens": 999}) + "\n"
       )
       artifacts_root = tmp_path / "runs"
       (artifacts_root / "run-1").mkdir(parents=True)
       (artifacts_root / "run-1" / "run-record.json").write_text(json.dumps({
           "totals": {"cost_usd": 1.5},
           "harness_economics": {"outcome": {"state": "delivered_clean", "score": 1.0},
                                  "factory_cpm": 5714.0},
       }))
       # run-2 has no retained run-record.json — overlay must degrade gracefully.

       result = rr._build_issue_economics(235, ledger_path=ledger, artifacts_root=artifacts_root)

       assert set(result["runs"].keys()) == {"run-1", "run-2"}
       run1 = result["runs"]["run-1"]
       assert run1["intent"] == "implement"
       assert run1["stage"] == "implement"
       assert run1["request_count"] == 2
       assert run1["retry_spend"] == {"tokens": 25, "request_count": 1}
       assert run1["cost_usd"] == pytest.approx(1.5)
       assert run1["outcome_state"] == "delivered_clean"
       assert run1["factory_cpm"] == pytest.approx(5714.0)

       run2 = result["runs"]["run-2"]
       assert run2["cost_usd"] is None
       assert run2["outcome_state"] is None

   def test_build_issue_economics_no_rows_for_issue(tmp_path):
       ledger = tmp_path / "request-ledger.jsonl"
       ledger.write_text(json.dumps({"run_id": "run-1", "issue_number": 1, "intent": "x",
                                      "stage": "x", "status": 200,
                                      "gen_ai.usage.input_tokens": 1, "gen_ai.usage.output_tokens": 1}) + "\n")
       result = rr._build_issue_economics(999, ledger_path=ledger, artifacts_root=tmp_path / "runs")
       assert result["runs"] == {}
   ```

2. Run to confirm failure:

   ```bash
   python -m pytest tests/test_run_record.py -k issue_economics -v
   ```

   Expected: `AttributeError: ... has no attribute '_build_issue_economics'`.

3. Implement in `scripts/factory_core/run_record.py`, added after `cmd_assemble` (before
   `def main`):

   ```python
   def _build_issue_economics(issue_number: int, *, ledger_path: pathlib.Path, artifacts_root: pathlib.Path) -> dict:
       """Read-only rollup over request-ledger.jsonl, grouped by run/issue/phase.

       Overlays dollar/outcome figures from each run's own retained run-record.json —
       never recomputes them independently (see spec's "Cost source" section). Produces
       no new persisted file.
       """
       runs: dict = {}
       for path in _iter_ledger_paths(ledger_path):
           if not path.exists():
               continue
           try:
               for line in path.read_text(encoding="utf-8").splitlines():
                   line = line.strip()
                   if not line:
                       continue
                   try:
                       row = json.loads(line)
                   except json.JSONDecodeError:
                       continue
                   if int(row.get("issue_number") or 0) != issue_number:
                       continue
                   run_id = row.get("run_id", "unknown")
                   bucket = runs.setdefault(run_id, {
                       "intent": row.get("intent", "unknown"),
                       "stage": row.get("stage", "unknown"),
                       "rows": [],
                   })
                   bucket["rows"].append(row)
           except OSError:
               continue

       result_runs = {}
       for run_id, bucket in runs.items():
           rows = bucket["rows"]
           entry = {
               "intent": bucket["intent"],
               "stage": bucket["stage"],
               "request_count": len(rows),
               "retry_spend": _compute_retry_spend(rows),
               "ledger_mechanics": _compute_ledger_mechanics(rows),
               "cost_usd": None,
               "outcome_state": None,
               "factory_cpm": None,
           }
           record_path = artifacts_root / run_id / "run-record.json"
           if record_path.exists():
               try:
                   record = json.loads(record_path.read_text(encoding="utf-8"))
                   entry["cost_usd"] = record.get("totals", {}).get("cost_usd")
                   he = record.get("harness_economics") or {}
                   entry["outcome_state"] = he.get("outcome", {}).get("state")
                   entry["factory_cpm"] = he.get("factory_cpm")
               except (json.JSONDecodeError, OSError):
                   pass
           result_runs[run_id] = entry

       return {"issue_number": issue_number, "runs": result_runs}


   def cmd_issue_economics(args) -> None:
       result = _build_issue_economics(
           args.issue,
           ledger_path=pathlib.Path(args.ledger_path) if args.ledger_path else LEDGER_PATH,
           artifacts_root=pathlib.Path(args.artifacts_root),
       )
       print(json.dumps(result, indent=2))
   ```

4. Add the subparser in `main()`, after the `assemble` subparser block (after line 358, before
   `parsed = parser.parse_args()`):

   ```python
   ie = sub.add_parser("issue-economics", help="Read-only cross-run rollup for an issue")
   ie.add_argument("--issue", type=int, required=True)
   ie.add_argument("--artifacts-root", required=True)
   ie.add_argument("--ledger-path", default=None)
   ```

   And dispatch it in the `if/elif` chain at the bottom of `main()`:

   ```python
   elif parsed.cmd == "issue-economics":
       cmd_issue_economics(parsed)
   ```

5. Run to confirm pass:

   ```bash
   python -m pytest tests/test_run_record.py -k issue_economics -v
   ```

   Expected: both tests pass.

6. Manual CLI smoke check:

   ```bash
   mkdir -p /tmp/ie-smoke/runs/run-1
   echo '{"run_id":"run-1","issue_number":42,"intent":"implement","stage":"implement","status":200,"gen_ai.usage.input_tokens":5,"gen_ai.usage.output_tokens":5}' > /tmp/ie-smoke/ledger.jsonl
   python3 scripts/factory_core/run_record.py issue-economics --issue 42 \
     --artifacts-root /tmp/ie-smoke/runs --ledger-path /tmp/ie-smoke/ledger.jsonl
   rm -rf /tmp/ie-smoke
   ```

   Expected: pretty-printed JSON with `"runs": {"run-1": {...}}`.

7. Full-suite regression check:

   ```bash
   python -m pytest tests/test_run_record.py -v
   ```

8. Commit:

   ```bash
   git add scripts/factory_core/run_record.py tests/test_run_record.py
   git commit -m "feat(economics): add read-only issue-economics rollup query"
   ```

---

## Task 6 — `backfill-economics` degrade-only historical recompute

**Files:** `scripts/factory_core/run_record.py`, `tests/test_run_record.py`

1. Add failing tests, appended after the Task 5 tests:

   ```python
   # ---------------------------------------------------------------------------
   # backfill-economics
   # ---------------------------------------------------------------------------

   def test_backfill_economics_full_recompute(tmp_path):
       run_dir = tmp_path / "runs" / "run-1"
       run_dir.mkdir(parents=True)
       (run_dir / "run-record.json").write_text(json.dumps({
           "run_id": "run-1", "status": "completed",
           "started_at": "2026-06-12T04:00:00Z", "completed_at": "2026-06-12T04:01:00Z",
           "stages": [{"stage": "validation", "verdict": "PASS"}],
           "totals": {"gen_ai.usage.input_tokens": 100, "gen_ai.usage.output_tokens": 50, "cost_usd": 0.02},
       }))
       ledger = tmp_path / "request-ledger.jsonl"
       ledger.write_text(json.dumps({"run_id": "run-1", "status": 200,
                                      "gen_ai.usage.input_tokens": 100, "gen_ai.usage.output_tokens": 50}) + "\n")

       ok = rr._backfill_run_economics("run-1", artifacts_root=tmp_path / "runs", ledger_path=ledger)
       assert ok is True

       updated = json.loads((run_dir / "run-record.json").read_text())
       assert "harness_economics" in updated
       assert updated["harness_economics"]["ledger_available"] is True
       # "validation" is a gate stage (GATE_STAGE_NAMES, Task 1) with verdict PASS and no
       # friction signals present -> delivered_clean, not produced_ungated (which requires
       # zero gate stages).
       assert updated["harness_economics"]["outcome"]["state"] == "delivered_clean"

   def test_backfill_economics_ledger_rotated_away_degrades(tmp_path):
       run_dir = tmp_path / "runs" / "run-2"
       run_dir.mkdir(parents=True)
       (run_dir / "run-record.json").write_text(json.dumps({
           "run_id": "run-2", "status": "completed",
           "started_at": "2026-06-12T04:00:00Z", "completed_at": "2026-06-12T04:01:00Z",
           "stages": [], "totals": {"gen_ai.usage.input_tokens": 0, "gen_ai.usage.output_tokens": 0, "cost_usd": 0.0},
       }))
       missing_ledger = tmp_path / "no-such-ledger.jsonl"

       ok = rr._backfill_run_economics("run-2", artifacts_root=tmp_path / "runs", ledger_path=missing_ledger)
       assert ok is True

       updated = json.loads((run_dir / "run-record.json").read_text())
       assert updated["harness_economics"]["ledger_available"] is False

   def test_backfill_economics_missing_run_record_skipped(tmp_path):
       ok = rr._backfill_run_economics("no-such-run", artifacts_root=tmp_path / "runs", ledger_path=tmp_path / "ledger.jsonl")
       assert ok is False
   ```

2. Run to confirm failure:

   ```bash
   python -m pytest tests/test_run_record.py -k backfill_economics -v
   ```

   Expected: `AttributeError: ... has no attribute '_backfill_run_economics'`.

3. Implement in `scripts/factory_core/run_record.py`, added after `cmd_issue_economics`:

   ```python
   def _backfill_run_economics(run_id: str, *, artifacts_root: pathlib.Path, ledger_path: pathlib.Path) -> bool:
       """Best-effort, degrade-only recompute of harness_economics for a retained run.

       Returns False (skipped, not an error) when the run's run-record.json directory
       no longer exists — bounded by artifact retention, per the spec's backfill section.
       """
       record_path = artifacts_root / run_id / "run-record.json"
       if not record_path.exists():
           return False
       try:
           record = json.loads(record_path.read_text(encoding="utf-8"))
       except (json.JSONDecodeError, OSError):
           return False

       record["harness_economics"] = _compute_harness_economics(
           run_id=run_id,
           status=record.get("status", "completed"),
           stages=record.get("stages", []),
           totals=record.get("totals", {}),
           started_at=record.get("started_at", ""),
           completed_at=record.get("completed_at", ""),
           ledger_path=ledger_path,
       )
       record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
       return True


   def cmd_backfill_economics(args) -> None:
       ok = _backfill_run_economics(
           args.run_id,
           artifacts_root=pathlib.Path(args.artifacts_root),
           ledger_path=pathlib.Path(args.ledger_path) if args.ledger_path else LEDGER_PATH,
       )
       print(json.dumps({"run_id": args.run_id, "backfilled": ok}))
   ```

4. Add the subparser in `main()`, after the `issue-economics` subparser block:

   ```python
   be = sub.add_parser("backfill-economics", help="Degrade-only historical harness_economics recompute")
   be.add_argument("--run-id", required=True)
   be.add_argument("--artifacts-root", required=True)
   be.add_argument("--ledger-path", default=None)
   ```

   And dispatch it:

   ```python
   elif parsed.cmd == "backfill-economics":
       cmd_backfill_economics(parsed)
   ```

5. Run to confirm pass:

   ```bash
   python -m pytest tests/test_run_record.py -k backfill_economics -v
   ```

   Expected: all 3 pass.

6. Full-suite final regression check:

   ```bash
   python -m pytest tests/test_run_record.py -v
   python -m pytest tests/ -v
   ```

   Expected: 0 failures across the whole suite.

7. Commit:

   ```bash
   git add scripts/factory_core/run_record.py tests/test_run_record.py
   git commit -m "feat(economics): add degrade-only backfill-economics recompute"
   ```

---

## Post-implementation checklist (for the conformance/review gates, not a task to execute here)

- `harness_economics` is attached on both success and failure paths — Task 3 + Task 4.
- Deterministic outcome-score policy with named constants — Task 1.
- `cost_per_task`, `tokens_per_task`, `retry_spend`, `failure_spend`, `factory_cpm` — Task 3.
- Graceful degradation (`ledger_available`/`ledger_rows_correlated`/nullable `ledger_mechanics`)
  — Task 2 + Task 3.
- Read-only `issue-economics` rollup, no new persisted artifact — Task 5.
- Degrade-only `backfill-economics`, bounded by retention — Task 6.
- `memory_intervention` (#241) — deliberately not added anywhere in this plan (non-goal).
