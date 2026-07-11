#!/usr/bin/env python3
"""Thin CLI entry points for the Tracker/CodeHost providers (parent spec §4.2,
illustrative surface). New, additive surface — nothing existing calls into it yet
(bash/YAML call sites are rewired in a later, separate ticket).

Invocation mirrors this repo's existing scripts/factory_core/cli.py convention
(direct script path + subcommands), not `-m factory_core.tracker` — see this
ticket's plan, "Design decisions" #4.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from factory_core.providers import get_codehost, get_tracker  # noqa: E402


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
    get_tracker().set_status(args.id, args.status)


def _tracker_label(args):
    tracker = get_tracker()
    for name in (args.add or []):
        tracker.add_label(args.id, name)
    for name in (args.remove or []):
        tracker.remove_label(args.id, name)


def _tracker_comment(args):
    body = Path(args.body_file).read_text(encoding="utf-8")
    get_tracker().upsert_comment(args.id, args.marker, body)


def _tracker_create(args):
    body = Path(args.body_file).read_text(encoding="utf-8")
    labels = args.labels.split(",") if args.labels else None
    _print(get_tracker().create_item(args.title, body, labels))


def _tracker_resolve(args):
    get_tracker().resolve_item(args.id)


def _tracker_children(args):
    _print(get_tracker().get_children(args.epic))


def _codehost_remote_url(args):
    url = get_codehost().remote_url()
    _print(re.sub(r"://[^@/]+@", "://***@", url))


def _codehost_find_change(args):
    _print(get_codehost().find_change_for(args.branch, exact=args.exact, repo=args.repo) or "")


def _codehost_open_change(args):
    body = Path(args.body_file).read_text(encoding="utf-8")
    _print(get_codehost().open_change(
        args.source, args.target, args.title, body, draft=args.draft, repo=args.repo))


def _codehost_mark_ready(args):
    get_codehost().mark_ready(args.id, repo=args.repo)


def _codehost_merge(args):
    _print(get_codehost().merge_change(
        args.id, strategy=args.strategy, delete_branch=args.delete_branch, repo=args.repo))


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
    get_codehost().update_change_body(args.id, body)


def _codehost_close_keyword(args):
    _print(get_codehost().close_keyword(args.id))


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

    codehost = top.add_parser("codehost")
    csub = codehost.add_subparsers(dest="verb", required=True)

    cru = csub.add_parser("remote-url")
    cru.set_defaults(func=_codehost_remote_url)

    cfc = csub.add_parser("find-change")
    cfc.add_argument("--branch", required=True)
    cfc.add_argument("--repo")
    cfc.add_argument("--exact", action="store_true")
    cfc.set_defaults(func=_codehost_find_change)

    coc = csub.add_parser("open-change")
    coc.add_argument("--source")
    coc.add_argument("--target")
    coc.add_argument("--title", required=True)
    coc.add_argument("--body-file", required=True)
    coc.add_argument("--draft", action="store_true")
    coc.add_argument("--repo")
    coc.set_defaults(func=_codehost_open_change)

    cmr = csub.add_parser("mark-ready")
    cmr.add_argument("--id", required=True)
    cmr.add_argument("--repo")
    cmr.set_defaults(func=_codehost_mark_ready)

    cm = csub.add_parser("merge")
    cm.add_argument("--id", required=True)
    cm.add_argument("--strategy", default="merge")
    cm.add_argument("--delete-branch", action=argparse.BooleanOptionalAction, default=True)
    cm.add_argument("--repo")
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

    cck = csub.add_parser("close-keyword")
    cck.add_argument("--id", required=True)
    cck.set_defaults(func=_codehost_close_keyword)

    parsed = parser.parse_args()
    parsed.func(parsed)


if __name__ == "__main__":
    main()
