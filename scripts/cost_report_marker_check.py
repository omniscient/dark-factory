#!/usr/bin/env python3
"""Example contract-satisfaction stop-condition predicate (#198 R6): checks whether
the durable <!-- dark-factory-cost-report --> marker comment has been posted for
this issue — the exact regression check for #300 ("a run reached Done because
completion was inferred from node exit status, with no cost-report comment ever
posted"). Bare-exit-code convention for #197's verifier.py (exit 0 = PASS, exit 1 =
BLOCKED): no STATUS: lines are printed, matching smoke-gate's own low-effort on-ramp
for a target's first verifier.

Env contract (set by #197's verifier.py run_verifier(), same four-var + LOOP_NAME
contract as scripts/hooks.sh::run_hook): CLONE_DIR, ARTIFACTS_DIR, ISSUE_NUM,
FACTORY_REPO_SLUG, LOOP_NAME. CLONE_DIR and ISSUE_NUM are read here.

Checks marker *presence*, not the path that produced it — "posted once at run end"
and "posted early, updated in place under the same marker" both PASS (#311's own
stated invariant for this fixture).
"""
import json
import os
import sys
from pathlib import Path

COST_MARKER = "<!-- dark-factory-cost-report -->"
_TEST_FIXTURE_NAME = ".cost_report_marker_check_test_fixture.json"


def get_tracker():
    sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts/ itself, so
                                                                # `factory_core` resolves
    from factory_core.providers import get_tracker as _get_tracker
    return _get_tracker()


def _load_comments(issue_num: int) -> list:
    # Test-only seam: a CLONE_DIR-relative fixture file, never present in production
    # (CLONE_DIR is always a real clone there). CLONE_DIR is one of the four core
    # env vars #197 commits run_verifier to always forward, unlike an ad hoc extra
    # var, so this seam has no dependency on unverified subprocess env-forwarding
    # behavior (see #198 plan Task 16/17's own design note).
    clone_dir = os.environ.get("CLONE_DIR", "")
    if clone_dir:
        fixture = Path(clone_dir) / _TEST_FIXTURE_NAME
        if fixture.is_file():
            return json.loads(fixture.read_text()).get("comments", [])
    return get_tracker().get_comments(str(issue_num))


def check(issue_num: int) -> int:
    for comment in _load_comments(issue_num):
        if COST_MARKER in comment.get("body", ""):
            return 0
    return 1


def main() -> None:
    issue_num = os.environ.get("ISSUE_NUM", "")
    if not issue_num.isdigit():
        sys.exit(1)  # fail closed — no issue context, never PASS
    sys.exit(check(int(issue_num)))


if __name__ == "__main__":
    main()
