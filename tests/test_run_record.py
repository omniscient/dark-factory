import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core import run_record as rr


# ---------------------------------------------------------------------------
# record command
# ---------------------------------------------------------------------------

class _RecordArgs:
    run_id = "abc123"
    issue = 333
    intent = "new"
    stage = "conformance"
    verdict = "PASS"
    tokens_in = 1000
    tokens_out = 500
    cost_usd = 0.01
    duration_ms = 5000
    detail = ["cycles=2"]


def test_record_writes_jsonl(tmp_path, monkeypatch):
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(rr, "JSONL_PATH", jsonl)
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)

    rr.cmd_record(_RecordArgs())

    lines = jsonl.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["stage"] == "conformance"
    assert rec["verdict"] == "PASS"
    assert rec["gen_ai.usage.input_tokens"] == 1000
    assert rec["gen_ai.usage.output_tokens"] == 500
    assert rec["gen_ai.system"] == "dark-factory"
    assert rec["gen_ai.operation.name"] == "stage.conformance"
    assert rec["detail"]["cycles"] == 2


def test_record_appends_multiple(tmp_path, monkeypatch):
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(rr, "JSONL_PATH", jsonl)
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)

    for verdict in ("PASS", "FAIL"):
        args = type("A", (), {**vars(_RecordArgs), "verdict": verdict})()
        rr.cmd_record(args)

    lines = jsonl.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["verdict"] == "PASS"
    assert json.loads(lines[1])["verdict"] == "FAIL"


def test_record_detail_empty(tmp_path, monkeypatch):
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(rr, "JSONL_PATH", jsonl)
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)

    args = type("A", (), {**vars(_RecordArgs), "detail": None})()
    rr.cmd_record(args)

    rec = json.loads(jsonl.read_text().strip())
    assert "detail" not in rec


def test_record_detail_float(tmp_path, monkeypatch):
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(rr, "JSONL_PATH", jsonl)
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)

    args = type("A", (), {**vars(_RecordArgs), "detail": ["cost=1.23", "count=5"]})()
    rr.cmd_record(args)

    rec = json.loads(jsonl.read_text().strip())
    assert rec["detail"]["cost"] == pytest.approx(1.23)
    assert rec["detail"]["count"] == 5


def test_post_seq_is_nonfatal(tmp_path, monkeypatch):
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(rr, "JSONL_PATH", jsonl)
    monkeypatch.setattr(rr, "SEQ_URL", "http://unreachable-host-99999:5341")

    # Should not raise even when Seq is unreachable
    rr.cmd_record(_RecordArgs())
    assert jsonl.exists()


# ---------------------------------------------------------------------------
# _parse_archon_cost
# ---------------------------------------------------------------------------

def test_parse_archon_cost_basic(tmp_path):
    cost_json = tmp_path / "cost.json"
    cost_json.write_text(json.dumps({
        "runId": "xyz",
        "nodes": [
            {
                "nodeId": "implement",
                "inputTokens": 120000,
                "outputTokens": 52000,
                "costUsd": 0.34,
                "durationMs": 300000,
                "modelUsage": {"claude-sonnet-4-6-20251101": 1},
            }
        ],
        "totals": {"costUsd": 0.34, "inputTokens": 120000, "outputTokens": 52000},
    }))

    nodes = rr._parse_archon_cost(cost_json)
    assert len(nodes) == 1
    assert nodes[0]["node_id"] == "implement"
    assert nodes[0]["gen_ai.usage.input_tokens"] == 120000
    assert nodes[0]["gen_ai.usage.output_tokens"] == 52000
    assert nodes[0]["cost_usd"] == pytest.approx(0.34)
    assert nodes[0]["duration_ms"] == 300000
    assert nodes[0]["model"] == "sonnet-4-6"


def test_parse_archon_cost_missing_file(tmp_path):
    assert rr._parse_archon_cost(tmp_path / "nonexistent.json") == []


def test_parse_archon_cost_empty_file(tmp_path):
    f = tmp_path / "cost.json"
    f.write_text("")
    assert rr._parse_archon_cost(f) == []


# ---------------------------------------------------------------------------
# _parse_archon_cost_with_capture (df#300)
# ---------------------------------------------------------------------------

def test_parse_archon_cost_capture_ok_when_nodes_present(tmp_path):
    cost_json = tmp_path / "cost.json"
    cost_json.write_text(json.dumps({
        "runId": "xyz",
        "nodes": [{"nodeId": "implement", "inputTokens": 1, "outputTokens": 1,
                   "costUsd": 0.01, "durationMs": 100, "modelUsage": {}}],
    }))
    nodes, capture = rr._parse_archon_cost_with_capture(cost_json, exit_code=0, stderr_text="")
    assert len(nodes) == 1
    assert capture == {"ok": True, "exit_code": 0, "stderr_excerpt": ""}


def test_parse_archon_cost_capture_not_ok_on_nonzero_exit(tmp_path):
    cost_json = tmp_path / "cost.json"
    cost_json.write_text("")
    nodes, capture = rr._parse_archon_cost_with_capture(
        cost_json, exit_code=127, stderr_text="archon: command not found\n"
    )
    assert nodes == []
    assert capture["ok"] is False
    assert capture["exit_code"] == 127
    assert "command not found" in capture["stderr_excerpt"]


def test_parse_archon_cost_capture_ok_valid_json_zero_nodes(tmp_path):
    # Archon ran fine and genuinely reports zero nodes (e.g. an ungated refine/plan
    # run) — this must NOT be flagged as a capture failure.
    cost_json = tmp_path / "cost.json"
    cost_json.write_text(json.dumps({"runId": "xyz", "nodes": []}))
    nodes, capture = rr._parse_archon_cost_with_capture(cost_json, exit_code=0, stderr_text="")
    assert nodes == []
    assert capture == {"ok": True, "exit_code": 0, "stderr_excerpt": ""}


def test_parse_archon_cost_capture_not_ok_on_unparseable_output(tmp_path):
    cost_json = tmp_path / "cost.json"
    cost_json.write_text("not json at all {{{")
    nodes, capture = rr._parse_archon_cost_with_capture(cost_json, exit_code=0, stderr_text="")
    assert nodes == []
    assert capture["ok"] is False
    assert capture["exit_code"] == 0


# ---------------------------------------------------------------------------
# _parse_artifact_stage
# ---------------------------------------------------------------------------

def test_parse_artifact_validation_pass():
    stage = rr._parse_artifact_stage("validation", "STATUS: PASS\nSome detail\n")
    assert stage["stage"] == "validation"
    assert stage["verdict"] == "PASS"


def test_parse_artifact_validation_fail():
    stage = rr._parse_artifact_stage("validation", "STATUS: FAIL\nError details\n")
    assert stage["verdict"] == "FAIL"


def test_parse_artifact_conformance_with_cycles():
    content = "STATUS: PASS\nCYCLES: 2\nVERDICT: Approved\n"
    stage = rr._parse_artifact_stage("conformance", content)
    assert stage["verdict"] == "PASS"
    assert stage["cycles"] == 2


def test_parse_artifact_conformance_blocked():
    content = "⛔ Material divergence\n"
    stage = rr._parse_artifact_stage("conformance", content)
    assert stage["verdict"] == "BLOCKED"


def test_parse_artifact_review_with_blockers():
    content = "STATUS: PASS\nBLOCKERS: 0\nADVISORY: 3\n"
    stage = rr._parse_artifact_stage("review", content)
    assert stage["verdict"] == "PASS"
    assert stage["blockers"] == 0
    assert stage["advisory"] == 3


def test_parse_artifact_conflict_none():
    content = "CONFLICT_VERDICT=none\n"
    stage = rr._parse_artifact_stage("conflict_resolution", content)
    assert stage["verdict"] == "none"


def test_parse_artifact_conflict_resolved():
    content = "**Status:** RESOLVED\nBranch: feat/123\n"
    stage = rr._parse_artifact_stage("conflict_resolution", content)
    assert stage["verdict"] == "RESOLVED"


def test_parse_artifact_missing_returns_none():
    assert rr._parse_artifact_stage("validation", "") is None


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
    assert mech["cache_hit_ratio"] == pytest.approx(40 / 140)
    assert mech["tool_schema_overhead_bytes"] == 100
    assert mech["largest_tools"][0]["name"] == "Bash"


def test_compute_ledger_mechanics_empty_rows():
    mech = rr._compute_ledger_mechanics([])
    assert mech["cache_hit_ratio"] is None
    assert mech["tool_schema_overhead_bytes"] == 0
    assert mech["largest_tools"] == []


# ---------------------------------------------------------------------------
# assemble command
# ---------------------------------------------------------------------------

class _AssembleArgs:
    run_id = "abc123"
    issue = 333
    intent = "new"
    started_at = "2026-06-12T04:00:00Z"
    archon_cost_json = None
    status = "completed"
    ledger_path = None
    clone_dir = "."

    def __init__(self, artifacts_dir, out_file):
        self.artifacts_dir = str(artifacts_dir)
        self.out_file = str(out_file)


def test_assemble_builds_run_record(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

    (tmp_path / "validation.md").write_text("STATUS: PASS\n")
    (tmp_path / "conformance.md").write_text("STATUS: PASS\nCYCLES: 1\n")
    (tmp_path / "review.md").write_text("STATUS: PASS\nBLOCKERS: 0\nADVISORY: 2\n")

    out = tmp_path / "run-record.json"
    args = _AssembleArgs(tmp_path, out)
    rr.cmd_assemble(args)

    assert out.exists()
    rec = json.loads(out.read_text())
    assert rec["run_id"] == "abc123"
    assert rec["issue_number"] == 333
    assert len(rec["stages"]) == 3
    stages_by_name = {s["stage"]: s for s in rec["stages"]}
    assert stages_by_name["validation"]["verdict"] == "PASS"
    assert stages_by_name["conformance"]["cycles"] == 1
    assert stages_by_name["review"]["blockers"] == 0
    assert rec["artifacts"]["validation"] == "STATUS: PASS\n"
    assert rec["started_at"] == "2026-06-12T04:00:00Z"
    assert rec["completed_at"]


def test_assemble_missing_artifacts_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

    out = tmp_path / "run-record.json"
    args = _AssembleArgs(tmp_path, out)
    rr.cmd_assemble(args)

    rec = json.loads(out.read_text())
    assert rec["stages"] == []
    assert rec["artifacts"] == {}


def test_assemble_incorporates_archon_cost(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

    cost_json = tmp_path / "cost.json"
    cost_json.write_text(json.dumps({
        "runId": "abc123",
        "nodes": [{"nodeId": "implement", "inputTokens": 100, "outputTokens": 50,
                   "costUsd": 0.01, "durationMs": 60000, "modelUsage": {}}],
        "totals": {"costUsd": 0.01, "inputTokens": 100, "outputTokens": 50},
    }))

    out = tmp_path / "run-record.json"
    args = _AssembleArgs(tmp_path, out)
    args.archon_cost_json = str(cost_json)
    rr.cmd_assemble(args)

    rec = json.loads(out.read_text())
    assert len(rec["nodes"]) == 1
    assert rec["nodes"][0]["gen_ai.usage.input_tokens"] == 100
    assert rec["totals"]["gen_ai.usage.input_tokens"] == 100
    assert rec["totals"]["cost_usd"] == pytest.approx(0.01)


def test_assemble_records_archon_cost_capture_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

    cost_json = tmp_path / "cost.json"
    cost_json.write_text(json.dumps({
        "runId": "abc123",
        "nodes": [{"nodeId": "implement", "inputTokens": 1, "outputTokens": 1,
                   "costUsd": 0.01, "durationMs": 1, "modelUsage": {}}],
    }))

    out = tmp_path / "run-record.json"
    args = _AssembleArgs(tmp_path, out)
    args.archon_cost_json = str(cost_json)
    args.archon_cost_exit_code = 0
    args.archon_cost_stderr_file = None
    rr.cmd_assemble(args)

    rec = json.loads(out.read_text())
    assert rec["archon_cost_capture"] == {"ok": True, "exit_code": 0, "stderr_excerpt": ""}


def test_assemble_records_archon_cost_capture_failure_when_nodes_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

    stderr_file = tmp_path / "archon-cost.stderr"
    stderr_file.write_text("archon: unknown command 'workflow cost'\n")

    out = tmp_path / "run-record.json"
    args = _AssembleArgs(tmp_path, out)
    args.archon_cost_json = str(tmp_path / "does-not-exist.json")
    args.archon_cost_exit_code = 127
    args.archon_cost_stderr_file = str(stderr_file)
    rr.cmd_assemble(args)

    rec = json.loads(out.read_text())
    assert rec["archon_cost_capture"]["ok"] is False
    assert rec["archon_cost_capture"]["exit_code"] == 127
    assert "unknown command" in rec["archon_cost_capture"]["stderr_excerpt"]


def test_assemble_emits_jsonl_per_stage(tmp_path, monkeypatch):
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(rr, "JSONL_PATH", jsonl)
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

    (tmp_path / "validation.md").write_text("STATUS: PASS\n")
    (tmp_path / "conformance.md").write_text("STATUS: PASS\nCYCLES: 0\n")

    out = tmp_path / "run-record.json"
    args = _AssembleArgs(tmp_path, out)
    rr.cmd_assemble(args)

    lines = jsonl.read_text().strip().splitlines()
    assert len(lines) == 2
    stages = [json.loads(l)["stage"] for l in lines]
    assert "validation" in stages
    assert "conformance" in stages


# ---------------------------------------------------------------------------
# harness_economics (via cmd_assemble)
# ---------------------------------------------------------------------------

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
    assert he["ledger_mechanics"]["cache_hit_ratio"] == pytest.approx(2 / 19)


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


# ── memory-trace pickup tests (issue #647) ─────────────────────────────────

def test_assemble_picks_up_memory_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

    trace = {
        "schema_version": 1,
        "retrieval_mechanism": "flatfile-pathtag",
        "phase": "implement",
        "affected_files": [],
        "files_loaded": [],
        "fallback_used": False,
    }
    (tmp_path / "memory-trace.json").write_text(json.dumps(trace))

    out = tmp_path / "run-record.json"
    args = _AssembleArgs(tmp_path, out)
    rr.cmd_assemble(args)

    rec = json.loads(out.read_text())
    assert "memory_trace" in rec
    assert rec["memory_trace"]["schema_version"] == 1
    assert rec["memory_trace"]["retrieval_mechanism"] == "flatfile-pathtag"


def test_assemble_no_memory_trace_key_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

    out = tmp_path / "run-record.json"
    args = _AssembleArgs(tmp_path, out)
    rr.cmd_assemble(args)

    rec = json.loads(out.read_text())
    assert "memory_trace" not in rec


def test_assemble_tolerates_malformed_memory_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

    (tmp_path / "memory-trace.json").write_text("not valid json {{{")

    out = tmp_path / "run-record.json"
    args = _AssembleArgs(tmp_path, out)
    rr.cmd_assemble(args)  # must not raise

    rec = json.loads(out.read_text())
    assert "memory_trace" not in rec


def test_assemble_tolerates_unreadable_memory_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

    # Create a directory where the file is expected — causes read failure
    (tmp_path / "memory-trace.json").mkdir()

    out = tmp_path / "run-record.json"
    args = _AssembleArgs(tmp_path, out)
    rr.cmd_assemble(args)  # must not raise

    rec = json.loads(out.read_text())
    assert "memory_trace" not in rec


# ── loops: surfacing in run record (issue #195) ────────────────────────────

def test_assemble_surfaces_loops_from_adapter(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

    clone_dir = tmp_path / "clone"
    (clone_dir / ".factory").mkdir(parents=True)
    (clone_dir / ".factory" / "adapter.yaml").write_text(
        "loops:\n"
        "  - name: nightly-scan-triage\n"
        "    purpose: Triage overnight scanner false positives\n"
        "    trigger: 'cron:0 6 * * *'\n"
        "    inputs: []\n"
        "    outputs: []\n"
        "    artifacts: []\n"
        "    verifier: verifiers/triage_verifier.py\n"
        "    stop_condition: stop_conditions/triage_stop.py\n"
        "    failure_behavior: escalate_to_human\n"
        "    side_effect_level: 2\n"
        "    handoff: handoffs/triage_handoff.py\n"
    )

    artifacts_dir = tmp_path / "artifacts"; artifacts_dir.mkdir()
    out = tmp_path / "run-record.json"
    args = _AssembleArgs(artifacts_dir, out)
    args.clone_dir = str(clone_dir)
    rr.cmd_assemble(args)

    rec = json.loads(out.read_text())
    assert rec["loops"] == [{
        "name": "nightly-scan-triage",
        "purpose": "Triage overnight scanner false positives",
        "trigger": "cron:0 6 * * *",
        "inputs": [], "outputs": [], "artifacts": [],
        "verifier": "verifiers/triage_verifier.py",
        "stop_condition": "stop_conditions/triage_stop.py",
        "failure_behavior": "escalate_to_human",
        "side_effect_level": 2,
        "handoff": "handoffs/triage_handoff.py",
    }]


def test_assemble_no_adapter_file_loops_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

    clone_dir = tmp_path / "clone"; clone_dir.mkdir()
    artifacts_dir = tmp_path / "artifacts"; artifacts_dir.mkdir()
    out = tmp_path / "run-record.json"
    args = _AssembleArgs(artifacts_dir, out)
    args.clone_dir = str(clone_dir)
    rr.cmd_assemble(args)

    rec = json.loads(out.read_text())
    assert rec["loops"] == []


def test_assemble_malformed_adapter_loops_empty_fail_open(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

    clone_dir = tmp_path / "clone"
    (clone_dir / ".factory").mkdir(parents=True)
    (clone_dir / ".factory" / "adapter.yaml").write_text("{broken: [")

    artifacts_dir = tmp_path / "artifacts"; artifacts_dir.mkdir()
    out = tmp_path / "run-record.json"
    args = _AssembleArgs(artifacts_dir, out)
    args.clone_dir = str(clone_dir)
    rr.cmd_assemble(args)  # must not raise

    rec = json.loads(out.read_text())
    assert rec["loops"] == []


def test_assemble_default_clone_dir_when_unset(tmp_path, monkeypatch):
    """_AssembleArgs instances that don't set clone_dir explicitly still work
    (class attribute default '.') — existing tests in this file rely on this."""
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

    artifacts_dir = tmp_path / "artifacts"; artifacts_dir.mkdir()
    out = tmp_path / "run-record.json"
    args = _AssembleArgs(artifacts_dir, out)
    rr.cmd_assemble(args)  # must not raise even without explicit clone_dir

    rec = json.loads(out.read_text())
    assert "loops" in rec


# ---------------------------------------------------------------------------
# health-event (factory.cost_report.missing and similar recurrence signals)
# ---------------------------------------------------------------------------

class _HealthEventArgs:
    run_id = "abc123"
    issue = 300
    event = "factory.cost_report.missing"
    detail = ["nodes_count=0", "archon_cost_capture_ok=False"]


def test_health_event_posts_to_seq(monkeypatch):
    posted = {}
    monkeypatch.setattr(rr, "_post_seq_raw", lambda payload: posted.update(payload))

    rr.cmd_health_event(_HealthEventArgs())

    assert posted["Events"][0]["MessageTemplate"] == "{Event} issue=#{IssueNumber} run={RunId}"
    props = posted["Events"][0]["Properties"]
    assert props["Event"] == "factory.cost_report.missing"
    assert props["IssueNumber"] == 300
    assert props["RunId"] == "abc123"
    assert props["nodes_count"] == "0"
    assert props["archon_cost_capture_ok"] == "False"


def test_health_event_nonfatal_on_seq_failure(monkeypatch):
    def _raise(payload):
        raise Exception("unreachable")
    monkeypatch.setattr(rr, "_post_seq_raw", _raise)

    rr.cmd_health_event(_HealthEventArgs())  # must not raise
