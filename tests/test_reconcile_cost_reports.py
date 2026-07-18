import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import reconcile_cost_reports as rcr


def test_recoverable_run_has_durable_record(tmp_path):
    state_dir = tmp_path / "state"
    (state_dir / "run-records").mkdir(parents=True)
    (state_dir / "run-records" / "run-1.json").write_text(json.dumps({
        "run_id": "run-1", "issue_number": 300, "status": "completed",
        "nodes": [{"node_id": "implement", "cost_usd": 0.5}],
        "totals": {"cost_usd": 0.5},
    }))
    jsonl = state_dir / "runs.jsonl"
    jsonl.write_text(json.dumps({"run_id": "run-1", "issue_number": 300, "stage": "implement"}) + "\n")

    report = rcr.build_reconciliation_report(state_dir=state_dir)

    assert report["recoverable"][0]["run_id"] == "run-1"
    assert report["recoverable"][0]["source"] == "durable_run_record"
    assert report["irrecoverable"] == []


def test_stub_only_run_is_irrecoverable_but_identified(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    jsonl = state_dir / "runs.jsonl"
    jsonl.write_text(json.dumps({
        "run_id": "run-2", "issue_number": 251, "stage": "failed", "timestamp": "2026-07-10T00:00:00Z",
    }) + "\n")

    report = rcr.build_reconciliation_report(state_dir=state_dir)

    assert report["recoverable"] == []
    assert report["irrecoverable"][0]["run_id"] == "run-2"
    assert report["irrecoverable"][0]["reason"] == "stub_only_no_durable_record"
    assert report["irrecoverable"][0]["issue_number"] == 251


def test_empty_state_dir_reports_plainly_not_as_failure(tmp_path):
    state_dir = tmp_path / "empty-state"
    state_dir.mkdir(parents=True)

    report = rcr.build_reconciliation_report(state_dir=state_dir)

    assert report["recoverable"] == []
    assert report["irrecoverable"] == []
    assert report["summary"]  # non-empty human-readable summary, not an exception


def test_irrecoverable_run_flags_ledger_rows_available(tmp_path):
    # Spec item 7 names request-ledger.jsonl/Seq as an additional scan source
    # alongside retained run-record.json files — a stub-only run whose run_id still
    # has rows in the request ledger is not fully recoverable (no node-level
    # cost/token breakdown), but the ledger DOES prove request volume/retry data
    # survived, which is worth flagging separately from "nothing at all survived."
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "runs.jsonl").write_text(json.dumps({
        "run_id": "run-3", "issue_number": 292, "stage": "failed",
    }) + "\n")
    ledger = tmp_path / "request-ledger.jsonl"
    ledger.write_text(json.dumps({"run_id": "run-3", "status": 200}) + "\n")

    report = rcr.build_reconciliation_report(state_dir=state_dir, ledger_path=ledger)

    assert report["irrecoverable"][0]["run_id"] == "run-3"
    assert report["irrecoverable"][0]["ledger_rows_available"] is True


def test_irrecoverable_run_ledger_rows_unavailable_when_no_ledger(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "runs.jsonl").write_text(json.dumps({
        "run_id": "run-4", "issue_number": 292, "stage": "failed",
    }) + "\n")

    report = rcr.build_reconciliation_report(state_dir=state_dir, ledger_path=tmp_path / "no-ledger.jsonl")

    assert report["irrecoverable"][0]["ledger_rows_available"] is False
