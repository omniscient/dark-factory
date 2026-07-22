#!/usr/bin/env python3
"""factory_core CLI — thin dispatch layer for shell adapters."""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _board_move(args):
    from factory_core.board import set_board_status
    set_board_status(args.issue, args.status)


def _deconflict(args):
    from factory_core.deconflict import resolve_merge_conflicts
    from factory_core import identity
    clone_dir = os.environ.get("CLONE_DIR", identity.CLONE_DIR)
    artifacts_dir = os.environ.get("ARTIFACTS_DIR", f"/tmp/artifacts/{args.issue}")
    if args.repo:
        owner, _, repo = args.repo.partition("/")
    else:
        owner = identity.OWNER
        repo = identity.REPO
    rc = resolve_merge_conflicts(
        issue_num=args.issue,
        clone_dir=clone_dir,
        owner=owner,
        repo=repo,
        artifacts_dir=artifacts_dir,
        ai_tier=not args.no_ai_tier,
    )
    sys.exit(rc)


def _breaker_get(args):
    from factory_core.breaker import get_retry_count
    state_file = Path(os.environ.get("STATE_FILE",
                                     "/var/lib/dark-factory/scheduler-state.json"))
    print(get_retry_count(args.key, state_file))


def _breaker_incr(args):
    from factory_core.breaker import increment_retry
    state_file = Path(os.environ.get("STATE_FILE",
                                     "/var/lib/dark-factory/scheduler-state.json"))
    print(increment_retry(args.key, state_file))


def _breaker_reset(args):
    from factory_core.breaker import reset_retry
    state_file = Path(os.environ.get("STATE_FILE",
                                     "/var/lib/dark-factory/scheduler-state.json"))
    reset_retry(args.key, state_file)


def _breaker_trip(args):
    from factory_core.breaker import trip_to_blocked
    state_file = Path(os.environ.get("STATE_FILE",
                                     "/var/lib/dark-factory/scheduler-state.json"))
    trip_to_blocked(
        issue_num=args.issue,
        phase=args.phase,
        reason=args.reason,
        state_file=state_file,
    )


def _run_record(args):
    sys.argv = ["run_record"] + args.run_record_args
    from factory_core import run_record
    run_record.main()


def _epic_autopilot(args):
    from factory_core.epic_autopilot import main_once
    main_once()


def _main_red_fix(args):
    from factory_core.main_red_fixer import main_once
    main_once()


def _rescue_blocked(args):
    from factory_core.rescue import rescue_blocked
    print(rescue_blocked(args.issue))


def _session_window_check(args):
    import time
    from factory_core.session_window import check_and_pause
    tmp_out_path = Path(args.tmp_out)
    text = tmp_out_path.read_text(errors="replace") if tmp_out_path.exists() else ""
    resume_epoch = check_and_pause(
        text,
        Path(args.state_dir),
        now_epoch=int(time.time()),
        buffer_minutes=args.buffer_minutes,
        fallback_minutes=args.fallback_minutes,
    )
    if resume_epoch is not None:
        print(f"matched=true resume_epoch={resume_epoch}")
    else:
        print("matched=false resume_epoch=0")


def _error_signature_write(args):
    from factory_core.error_signature import classify, write_signature
    text = ""
    if args.text_file:
        text_path = Path(args.text_file)
        text = text_path.read_text(errors="replace") if text_path.exists() else ""
    signature = classify(
        text,
        args.exit_code,
        elapsed_seconds=args.elapsed_seconds,
        commits_since_start=args.commits_since_start,
        worktree_dirty=args.worktree_dirty,
        artifact_present=args.artifact_present,
        delivery_failure_max_seconds=args.delivery_failure_max_seconds,
    )
    write_signature(args.issue, args.phase, signature, args.exit_code, Path(args.state_dir))
    print(f"signature={signature}")


def _cost_report_check(args):
    import json
    from factory_core import cost_report, run_record
    run_record_data = json.loads(Path(args.run_record_file).read_text())
    diagnostic = cost_report.check_renderable(run_record_data)
    if diagnostic is None:
        return
    msg = cost_report.format_missing_diagnostic(diagnostic, args.run_id, args.issue)
    print(msg, file=sys.stderr)
    if cost_report._jqstr(diagnostic["capture_ok"]) != "true":
        run_record.emit_health_event(
            "factory.cost_report.missing", args.issue, args.run_id,
            {
                "nodes_count": str(diagnostic["nodes_count"]),
                "archon_cost_capture_ok": cost_report._jqstr(diagnostic["capture_ok"]),
                "archon_cost_exit_code": cost_report._jqstr(diagnostic["capture_exit_code"]),
            },
        )
    sys.exit(3)


def _cost_report_render(args):
    import json
    from factory_core import cost_report
    run_record_data = json.loads(Path(args.run_record_file).read_text())
    prior_body = ""
    if args.prior_body_file:
        prior_path = Path(args.prior_body_file)
        prior_body = prior_path.read_text() if prior_path.exists() else ""
    budget = None
    if args.budget_file:
        budget_path = Path(args.budget_file)
        if budget_path.exists():
            budget = json.loads(budget_path.read_text())
    print(cost_report.render(run_record_data, prior_body, args.timestamp, args.intent,
                              product_name=args.product_name, budget=budget))


def _breaker_check_signature(args):
    from factory_core.breaker import record_failure_signature
    state_file = Path(os.environ.get("STATE_FILE",
                                     "/var/lib/dark-factory/scheduler-state.json"))
    state_dir = Path(os.environ.get("SCHEDULER_STATE_DIR", "/var/lib/dark-factory"))
    stuck, sig = record_failure_signature(args.issue, args.phase, state_file, state_dir)
    print(f"stuck={'true' if stuck else 'false'} sig={sig}")


def main():
    parser = argparse.ArgumentParser(prog="factory-core")
    sub = parser.add_subparsers(dest="cmd", required=True)

    bm = sub.add_parser("board-move")
    bm.add_argument("--issue", type=int, required=True)
    bm.add_argument("--status", required=True)
    bm.set_defaults(func=_board_move)

    dc = sub.add_parser("deconflict")
    dc.add_argument("--issue", type=int, required=True)
    dc.add_argument("--repo", default="")
    dc.add_argument("--no-ai-tier", action="store_true")
    dc.set_defaults(func=_deconflict)

    bg = sub.add_parser("breaker-get")
    bg.add_argument("--key", required=True)
    bg.set_defaults(func=_breaker_get)

    bi = sub.add_parser("breaker-incr")
    bi.add_argument("--key", required=True)
    bi.set_defaults(func=_breaker_incr)

    br = sub.add_parser("breaker-reset")
    br.add_argument("--key", required=True)
    br.set_defaults(func=_breaker_reset)

    bt = sub.add_parser("breaker-trip")
    bt.add_argument("--issue", type=int, required=True)
    bt.add_argument("--phase", required=True)
    bt.add_argument("--reason", required=True)
    bt.set_defaults(func=_breaker_trip)

    rr = sub.add_parser("run-record")
    rr.add_argument("run_record_args", nargs=argparse.REMAINDER)
    rr.set_defaults(func=_run_record)

    ea = sub.add_parser("epic-autopilot")
    ea.add_argument("--once", action="store_true")
    ea.set_defaults(func=_epic_autopilot)

    mr = sub.add_parser("main-red-fix")
    mr.add_argument("--once", action="store_true")
    mr.set_defaults(func=_main_red_fix)

    rb = sub.add_parser("rescue-blocked")
    rb.add_argument("--issue", type=int, required=True)
    rb.set_defaults(func=_rescue_blocked)

    sw = sub.add_parser("session-window-check")
    sw.add_argument("--tmp-out", required=True)
    sw.add_argument("--state-dir", default="/var/lib/dark-factory")
    sw.add_argument("--buffer-minutes", type=int, default=5)
    sw.add_argument("--fallback-minutes", type=int, default=30)
    sw.set_defaults(func=_session_window_check)

    esw = sub.add_parser("error-signature-write")
    esw.add_argument("--issue", type=int, required=True)
    esw.add_argument("--phase", required=True)
    esw.add_argument("--exit-code", type=int, required=True)
    esw.add_argument("--text-file", default="")
    esw.add_argument("--elapsed-seconds", type=int, required=True)
    esw.add_argument("--commits-since-start", type=int, required=True)
    esw.add_argument("--worktree-dirty", action="store_true")
    esw.add_argument("--artifact-present", action="store_true")
    esw.add_argument("--delivery-failure-max-seconds", type=int, default=30,
                      dest="delivery_failure_max_seconds")
    esw.add_argument("--state-dir", default="/var/lib/dark-factory")
    esw.set_defaults(func=_error_signature_write)

    bcs = sub.add_parser("breaker-check-signature")
    bcs.add_argument("--issue", type=int, required=True)
    bcs.add_argument("--phase", required=True)
    bcs.set_defaults(func=_breaker_check_signature)

    crc = sub.add_parser("cost-report-check")
    crc.add_argument("--run-record-file", required=True)
    crc.add_argument("--run-id", required=True)
    crc.add_argument("--issue", type=int, required=True)
    crc.set_defaults(func=_cost_report_check)

    crr = sub.add_parser("cost-report-render")
    crr.add_argument("--run-record-file", required=True)
    crr.add_argument("--prior-body-file", default="")
    crr.add_argument("--timestamp", required=True)
    crr.add_argument("--intent", required=True)
    crr.add_argument("--product-name", default="Dark Factory")
    crr.add_argument("--budget-file", default="")
    crr.set_defaults(func=_cost_report_render)

    parsed = parser.parse_args()
    parsed.func(parsed)


if __name__ == "__main__":
    main()
