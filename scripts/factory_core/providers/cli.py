#!/usr/bin/env python3
"""Thin CLI entry points for the Tracker/CodeHost providers (parent spec §4.2,
illustrative surface). scheduler.sh, entrypoint.sh, the smoke gate and the run DAG call
it for the tracker/code-host verbs; the few remaining direct `gh` call sites are tracked in #507.

Invocation mirrors this repo's existing scripts/factory_core/cli.py convention
(direct script path + subcommands), not `-m factory_core.tracker` — see this
ticket's plan, "Design decisions" #4.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from factory_core.providers import get_codehost, get_tracker, preflight  # noqa: E402


def _print(value):
    print(json.dumps(value) if not isinstance(value, str) else value)


def _tracker_list(args):
    labels = args.labels.split(",") if args.labels else None
    _print(get_tracker().list_work_items(args.statuses.split(","), labels))


def _tracker_get(args):
    fields = tuple(args.fields.split(",")) if args.fields else None
    _print(get_tracker().get_item(args.id, fields=fields))


def _tracker_get_comments(args):
    _print(get_tracker().get_comments(args.id))


def _tracker_get_status_limits(args):
    _print(get_tracker().get_status_limits())


def _tracker_get_rate_budget(args):
    _print(get_tracker().get_rate_budget())


def _tracker_set_status(args):
    try:
        ok = get_tracker().set_status(args.id, args.status)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    if not ok:
        print(f"ERROR: board move to {args.status!r} failed for issue {args.id}", file=sys.stderr)
        sys.exit(1)


def _tracker_label(args):
    tracker = get_tracker()
    ok = True
    for name in (args.add or []):
        try:
            if not tracker.add_label(args.id, name):
                ok = False
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            ok = False
    for name in (args.remove or []):
        try:
            if not tracker.remove_label(args.id, name):
                ok = False
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            ok = False
    if not ok:
        print(f"ERROR: one or more label operations failed for issue {args.id}", file=sys.stderr)
        sys.exit(1)


def _tracker_comment(args):
    body = Path(args.body_file).read_text(encoding="utf-8")
    get_tracker().upsert_comment(args.id, args.marker, body)


def _body_from_args(args):
    """Body text from --body-file (read verbatim) or --body (exactly one is required)."""
    if (args.body is None) == (args.body_file is None):
        print("ERROR: exactly one of --body / --body-file is required", file=sys.stderr)
        sys.exit(2)
    if args.body_file is not None:
        return Path(args.body_file).read_text(encoding="utf-8")
    return args.body


def _tracker_add_comment(args):
    # Append-only post. Exit 1 on failure so callers keep their `|| true` / `|| echo WARNING`
    # tolerance exactly as with the former inline `gh issue comment`.
    try:
        ok = get_tracker().add_comment(args.id, _body_from_args(args))
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    if not ok:
        sys.exit(1)


def _tracker_find_comment(args):
    # Prints the comment id (nothing when there is none / the lookup failed; exit 0 either
    # way, matching the entrypoint's former `|| true`). --body-out receives the body.
    try:
        found = get_tracker().find_comment(args.id, args.marker)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        found = None
    if not found:
        return
    if args.body_out:
        Path(args.body_out).write_text(found["body"], encoding="utf-8")
    print(found["id"])


def _tracker_update_comment(args):
    try:
        ok = get_tracker().update_comment(args.id, args.comment_id, _body_from_args(args))
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    if not ok:
        sys.exit(1)


def _tracker_board_items(args):
    # indent=2 / ensure_ascii=False matches what the scheduler's former `jq '{items: ...}'`
    # printed; the scheduler only pipes it into further jq filters.
    try:
        snapshot = get_tracker().list_board_items()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(snapshot, indent=2, ensure_ascii=False))


def _tracker_list_items(args):
    # Compact `[{"number":N},...]` = what `gh issue list --json number` printed.
    try:
        ids = get_tracker().list_items(args.label, limit=args.limit)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    rows = [{"number": int(i) if str(i).isdigit() else i} for i in ids]
    print(json.dumps(rows, separators=(",", ":"), ensure_ascii=False))


def _tracker_create(args):
    body = Path(args.body_file).read_text(encoding="utf-8")
    labels = args.labels.split(",") if args.labels else None
    _print(get_tracker().create_item(args.title, body, labels))


def _tracker_resolve(args):
    get_tracker().resolve_item(args.id)


def _tracker_children(args):
    _print(get_tracker().get_children(args.epic))


def _tracker_states(args):
    # #607: batched dependency-state read — one query for all referenced issues.
    # Exit 1 on failure: a refused read must read as unmet, never as met.
    ids = [i for i in args.ids.split(",") if i.strip()]
    try:
        _print(get_tracker().get_states(ids))
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def _codehost_remote_url(args):
    url = get_codehost().remote_url()
    _print(re.sub(r"://[^@/]+@", "://***@", url))


def _codehost_find_change(args):
    _print(get_codehost().find_change_for(args.branch, exact=args.exact, repo=args.repo) or "")


def _codehost_find_change_details(args):
    _print(get_codehost().find_change_details(args.branch, exact=args.exact, repo=args.repo) or "")


def _codehost_open_change(args):
    body = Path(args.body_file).read_text(encoding="utf-8")
    _print(get_codehost().open_change(
        args.source, args.target, args.title, body, draft=args.draft, repo=args.repo))


def _codehost_close_keyword(args):
    # Prints the snippet verbatim ("" when tracker != host). _print() emits a trailing
    # newline, which shell command substitution strips.
    _print(get_codehost().close_keyword(args.id))


def _codehost_mark_ready(args):
    get_codehost().mark_ready(args.id, repo=args.repo)


def _codehost_merge(args):
    # match_head only when given, so providers/fakes predating #551 keep working.
    extra = {"match_head": args.match_head} if args.match_head else {}
    _print(get_codehost().merge_change(
        args.id, strategy=args.strategy, delete_branch=args.delete_branch, repo=args.repo,
        **extra))


def _codehost_checks(args):
    _print(get_codehost().get_change_checks(args.id, fields=args.fields, repo=args.repo))


def _codehost_mergeable(args):
    _print(get_codehost().get_change_mergeable(args.id, repo=args.repo))


def _codehost_reviews(args):
    _print(get_codehost().get_change_reviews(args.id, repo=args.repo))


def _codehost_inline_comments(args):
    _print(get_codehost().get_change_inline_comments(args.id, repo=args.repo))


def _codehost_update_body(args):
    body = Path(args.body_file).read_text(encoding="utf-8")
    _print(get_codehost().update_change_body(args.id, body))


def _preflight(args):
    problems = preflight(side=args.side, intent=args.intent or None)
    if problems:
        for p in problems:
            print(f"ERROR: {p}", file=sys.stderr)
        sys.exit(1)
    print("providers preflight: OK")


def _fail(problems):
    for p in problems:
        print(f"ERROR: {p}", file=sys.stderr)
    sys.exit(1)


def _model_endpoints_show(args):
    # Redacted effective-configuration view (#557): secret_reference prints as its name and
    # presence only. The staged copy is trusted only with this dispatch's digest and is parsed
    # from the bytes that digest covered (#593); --from-source reads the scheduler's read-only
    # mount instead.
    from factory_core.providers import model_endpoint
    if args.from_source:
        try:
            problems, view = [], model_endpoint.redacted_view(model_endpoint.SOURCE_REGISTRY_DIR)
        except model_endpoint.RegistryError as e:
            problems, view = e.problems, None
    else:
        problems, view = model_endpoint.redacted_view_staged(model_endpoint.staged_registry_dir())
    if problems:
        _fail(problems)
    print(json.dumps(view, indent=2, default=str))


def _model_endpoints_hosting(args):
    # #586: entrypoint.sh resolves the run's hosting location once, before the clone, through
    # this baked copy. Built only on load_verified_entry(), which keeps _NO_LOCAL_DISPATCH
    # unless --intent names a local-eligible intent (#633), so without one every real registry
    # prints hosting=cloud. Always exits 0: any problem or exception prints hosting=cloud (the
    # caller fails closed on content, never on rc).
    hosting = "cloud"
    endpoint_id = os.environ.get("FACTORY_MODEL_ENDPOINT", "")
    try:
        if endpoint_id:
            from factory_core.providers import model_endpoint
            problems, entry = model_endpoint.load_verified_entry(
                model_endpoint.staged_registry_dir(), endpoint_id, intent=args.intent or None)
            if problems:
                print(f"WARNING: model-endpoints hosting: {'; '.join(problems)}", file=sys.stderr)
            elif entry["hosting_location"] == "local":
                hosting = "local"
    except Exception as e:  # noqa: BLE001 — fail closed to cloud on anything unexpected
        print(f"WARNING: model-endpoints hosting: {type(e).__name__} — resolving to cloud",
              file=sys.stderr)
        hosting = "cloud"
    print(f"hosting={hosting}")


def _model_endpoints_stage(args):
    # scheduler.sh dispatch() calls this before each dispatch when FACTORY_MODEL_ENDPOINT is
    # set; stdout is the staged tree's digest, forwarded as FACTORY_MODEL_REGISTRY_SHA256.
    from factory_core.providers import model_endpoint
    try:
        digest = model_endpoint.stage_registry(args.source_dir, args.dest_dir)
    except model_endpoint.RegistryError as e:
        _fail(e.problems)
    except OSError as e:
        _fail([f"model registry staging failed: {e}"])
    print(digest)


def _model_endpoints_hosting_check(args):
    # #585: scheduler.sh's non-cloud endpoint guard. Resolves against the scheduler's
    # read-only mount — never the staged (run-writable) copy. Only a definite cloud entry
    # prints `hosting=cloud` and exits 0; the reason= text is for an operator running this
    # by hand, and scheduler.sh discards it.
    from factory_core.providers import model_endpoint
    problems, hosting = model_endpoint.scheduler_preflight(
        model_endpoint.SOURCE_REGISTRY_DIR, args.endpoint_id
    )
    if hosting == "cloud":
        print("hosting=cloud")
        return
    if hosting == "local":
        print(f"hosting=local reason=endpoint '{args.endpoint_id}' hosting_location=local")
    else:
        reason = "; ".join(problems) if problems else f"endpoint '{args.endpoint_id}' not found in registry"
        print(f"hosting=unknown reason={reason}")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(prog="providers-cli")
    top = parser.add_subparsers(dest="provider", required=True)

    tracker = top.add_parser("tracker")
    tsub = tracker.add_subparsers(dest="verb", required=True)

    tl = tsub.add_parser("list")
    tl.add_argument("--statuses", required=True)
    tl.add_argument("--labels", default="")
    tl.set_defaults(func=_tracker_list)

    tg = tsub.add_parser("get")
    tg.add_argument("--id", required=True)
    tg.add_argument("--fields", default="")
    tg.set_defaults(func=_tracker_get)

    tgc = tsub.add_parser("get-comments")
    tgc.add_argument("--id", required=True)
    tgc.set_defaults(func=_tracker_get_comments)

    tgsl = tsub.add_parser("get-status-limits")
    tgsl.set_defaults(func=_tracker_get_status_limits)

    tgrb = tsub.add_parser("get-rate-budget")
    tgrb.set_defaults(func=_tracker_get_rate_budget)

    tss = tsub.add_parser("set-status")
    tss.add_argument("--id", required=True)
    tss.add_argument("--status", required=True)
    tss.set_defaults(func=_tracker_set_status)

    tlabel = tsub.add_parser("label")
    tlabel.add_argument("--id", required=True)
    tlabel.add_argument("--add", action="append")
    tlabel.add_argument("--remove", action="append")
    tlabel.set_defaults(func=_tracker_label)

    tc = tsub.add_parser("comment")
    tc.add_argument("--id", required=True)
    tc.add_argument("--marker", required=True)
    tc.add_argument("--body-file", required=True)
    tc.set_defaults(func=_tracker_comment)

    tac = tsub.add_parser("add-comment")
    tac.add_argument("--id", required=True)
    tac.add_argument("--body")
    tac.add_argument("--body-file")
    tac.set_defaults(func=_tracker_add_comment)

    tfc = tsub.add_parser("find-comment")
    tfc.add_argument("--id", required=True)
    tfc.add_argument("--marker", required=True)
    tfc.add_argument("--body-out")
    tfc.set_defaults(func=_tracker_find_comment)

    tuc = tsub.add_parser("update-comment")
    tuc.add_argument("--id", required=True)
    tuc.add_argument("--comment-id", required=True)
    tuc.add_argument("--body")
    tuc.add_argument("--body-file")
    tuc.set_defaults(func=_tracker_update_comment)

    tbi = tsub.add_parser("board-items")
    tbi.set_defaults(func=_tracker_board_items)

    tli = tsub.add_parser("list-items")
    tli.add_argument("--label", required=True)
    tli.add_argument("--limit", type=int, default=200)
    tli.set_defaults(func=_tracker_list_items)

    tcr = tsub.add_parser("create")
    tcr.add_argument("--title", required=True)
    tcr.add_argument("--body-file", required=True)
    tcr.add_argument("--labels", default="")
    tcr.set_defaults(func=_tracker_create)

    tr = tsub.add_parser("resolve")
    tr.add_argument("--id", required=True)
    tr.set_defaults(func=_tracker_resolve)

    tch = tsub.add_parser("children")
    tch.add_argument("--epic", required=True)
    tch.set_defaults(func=_tracker_children)

    tst = tsub.add_parser("states")
    tst.add_argument("--ids", required=True,
                     help="#607: comma-joined issue numbers, one query for all of them")
    tst.set_defaults(func=_tracker_states)

    codehost = top.add_parser("codehost")
    csub = codehost.add_subparsers(dest="verb", required=True)

    cru = csub.add_parser("remote-url")
    cru.set_defaults(func=_codehost_remote_url)

    cfc = csub.add_parser("find-change")
    cfc.add_argument("--branch", required=True)
    cfc.add_argument("--repo")
    cfc.add_argument("--exact", action="store_true")
    cfc.set_defaults(func=_codehost_find_change)

    cfcd = csub.add_parser("find-change-details")
    cfcd.add_argument("--branch", required=True)
    cfcd.add_argument("--repo")
    cfcd.add_argument("--exact", action="store_true")
    cfcd.set_defaults(func=_codehost_find_change_details)

    coc = csub.add_parser("open-change")
    coc.add_argument("--source")
    coc.add_argument("--target")
    coc.add_argument("--title", required=True)
    coc.add_argument("--body-file", required=True)
    coc.add_argument("--draft", action="store_true")
    coc.add_argument("--repo")
    coc.set_defaults(func=_codehost_open_change)

    cck = csub.add_parser("close-keyword")
    cck.add_argument("--id", required=True)
    cck.set_defaults(func=_codehost_close_keyword)

    cmr = csub.add_parser("mark-ready")
    cmr.add_argument("--id", required=True)
    cmr.add_argument("--repo")
    cmr.set_defaults(func=_codehost_mark_ready)

    cm = csub.add_parser("merge")
    cm.add_argument("--id", required=True)
    cm.add_argument("--strategy", default="merge")
    cm.add_argument("--delete-branch", action=argparse.BooleanOptionalAction, default=True)
    cm.add_argument("--repo")
    cm.add_argument("--match-head", default=None,
                    help="Refuse the merge unless the change head is exactly this sha (#551)")
    cm.set_defaults(func=_codehost_merge)

    cc = csub.add_parser("checks")
    cc.add_argument("--id", required=True)
    cc.add_argument("--fields", default="name,bucket,link")
    cc.add_argument("--repo")
    cc.set_defaults(func=_codehost_checks)

    cme = csub.add_parser("mergeable")
    cme.add_argument("--id", required=True)
    cme.add_argument("--repo")
    cme.set_defaults(func=_codehost_mergeable)

    cr = csub.add_parser("reviews")
    cr.add_argument("--id", required=True)
    cr.add_argument("--repo")
    cr.set_defaults(func=_codehost_reviews)

    cic = csub.add_parser("inline-comments")
    cic.add_argument("--id", required=True)
    cic.add_argument("--repo")
    cic.set_defaults(func=_codehost_inline_comments)

    cub = csub.add_parser("update-body")
    cub.add_argument("--id", required=True)
    cub.add_argument("--body-file", required=True)
    cub.set_defaults(func=_codehost_update_body)

    pf = top.add_parser("preflight")
    pf.add_argument("--side", choices=("run", "scheduler"), default="run",
                    help="scheduler: scheduler.sh's boot check; run (default): a run container's")
    pf.add_argument("--intent", default="",
                    help="the run's dispatch intent, from entrypoint.sh (#633); run side only")
    pf.set_defaults(func=_preflight)

    model_endpoints = top.add_parser("model-endpoints")
    msub = model_endpoints.add_subparsers(dest="verb", required=True)

    mshow = msub.add_parser("show")
    mshow.add_argument("--from-source", action="store_true",
                       help="read the scheduler's read-only registry mount, not the staged copy")
    mshow.set_defaults(func=_model_endpoints_show)

    mstage = msub.add_parser("stage")
    mstage.add_argument("--source-dir", default=None)
    mstage.add_argument("--dest-dir", default=None)
    mstage.set_defaults(func=_model_endpoints_stage)

    mhost = msub.add_parser("hosting")
    mhost.add_argument("--intent", default="",
                       help="the run's dispatch intent, from entrypoint.sh (#633)")
    mhost.set_defaults(func=_model_endpoints_hosting)

    mhc = msub.add_parser("hosting-check",
                          help="scheduler-side hosting of an endpoint id (#585); exit 0 only for cloud")
    mhc.add_argument("--endpoint-id", required=True)
    mhc.set_defaults(func=_model_endpoints_hosting_check)

    parsed = parser.parse_args()
    parsed.func(parsed)


if __name__ == "__main__":
    main()
