#!/usr/bin/env python3
"""One-shot, read-only reconciliation report for historical cost-report data (df#300).

Scans the durable run-records store and the runs.jsonl stage-event stream to
identify which historical runs are recoverable (a full durable run-record.json
exists) vs. irrecoverable (only a stage-stub row survives, or nothing at all).
Per the spec's item 7, also cross-references request-ledger.jsonl: an
irrecoverable run whose run_id still has ledger rows had its request volume/retry
data survive even though node-level cost/token breakdown did not — this is
flagged, not treated as a second recoverability tier (it's still no
node/harness_economics data). Does not gate anything, does not auto-run in
scheduler.sh/entrypoint.sh — invoke manually:
`python3 scripts/reconcile_cost_reports.py [--state-dir DIR] [--ledger-path PATH]`.
"""
import argparse
import json
import pathlib
import sys


def _load_ledger_run_ids(ledger_path: pathlib.Path) -> set:
    run_ids = set()
    if not ledger_path.exists():
        return run_ids
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        run_id = row.get("run_id")
        if run_id:
            run_ids.add(run_id)
    return run_ids


def _load_run_records(run_records_dir: pathlib.Path) -> dict:
    records = {}
    if not run_records_dir.exists():
        return records
    for path in run_records_dir.glob("*.json"):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        run_id = rec.get("run_id") or path.stem
        records[run_id] = rec
    return records


def _load_jsonl_stubs(jsonl_path: pathlib.Path) -> dict:
    stubs = {}
    if not jsonl_path.exists():
        return stubs
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        run_id = row.get("run_id")
        if not run_id:
            continue
        stubs.setdefault(run_id, []).append(row)
    return stubs


def build_reconciliation_report(*, state_dir: pathlib.Path, ledger_path: "pathlib.Path | None" = None) -> dict:
    run_records = _load_run_records(state_dir / "run-records")
    stubs = _load_jsonl_stubs(state_dir / "runs.jsonl")
    ledger_run_ids = _load_ledger_run_ids(ledger_path) if ledger_path is not None else set()

    recoverable = []
    for run_id, rec in run_records.items():
        recoverable.append({
            "run_id": run_id,
            "issue_number": rec.get("issue_number"),
            "source": "durable_run_record",
            "cost_usd": (rec.get("totals") or {}).get("cost_usd"),
        })

    irrecoverable = []
    for run_id, rows in stubs.items():
        if run_id in run_records:
            continue
        irrecoverable.append({
            "run_id": run_id,
            "issue_number": rows[0].get("issue_number"),
            "reason": "stub_only_no_durable_record",
            "stage_rows": len(rows),
            "ledger_rows_available": run_id in ledger_run_ids,
        })

    total = len(recoverable) + len(irrecoverable)
    if total == 0:
        summary = "No run history found under this state directory — nothing to reconcile."
    else:
        ledger_partial = sum(1 for r in irrecoverable if r["ledger_rows_available"])
        summary = (
            f"{len(recoverable)}/{total} historical runs recoverable via durable "
            f"run-record.json; {len(irrecoverable)}/{total} irrecoverable (stage-stub "
            "rows only — issue number/timestamp survive, node-level cost/token data does "
            f"not), of which {ledger_partial} still have request-ledger rows (request "
            "volume/retry data survives; node/harness_economics breakdown does not)."
        )

    return {"recoverable": recoverable, "irrecoverable": irrecoverable, "summary": summary}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default=None,
                         help="Defaults to $SCHEDULER_STATE_DIR or /var/lib/dark-factory")
    parser.add_argument("--ledger-path", default=None,
                         help="Defaults to $MODEL_PROXY_LEDGER_PATH or /var/lib/dark-factory/request-ledger.jsonl")
    args = parser.parse_args()

    import os
    state_dir = pathlib.Path(args.state_dir or os.environ.get("SCHEDULER_STATE_DIR", "/var/lib/dark-factory"))
    ledger_path = pathlib.Path(
        args.ledger_path or os.environ.get("MODEL_PROXY_LEDGER_PATH", "/var/lib/dark-factory/request-ledger.jsonl")
    )
    report = build_reconciliation_report(state_dir=state_dir, ledger_path=ledger_path)
    json.dump(report, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
