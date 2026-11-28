"""No draft plan may reach main (#409).

commands/dark-factory-plan.md checkpoints a draft plan under docs/superpowers/plan-drafts/
on its refine branch and moves it into docs/superpowers/plans/ when it publishes. Refine
branches are never merged by the factory, but an operator can open a PR from one; CI runs
this suite on every pull request to main, so a tracked draft fails the PR.
"""
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_no_file_is_tracked_under_plan_drafts():
    result = subprocess.run(
        ["git", "ls-files", "--", "docs/superpowers/plan-drafts/"],
        cwd=str(_REPO_ROOT), capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"not a git checkout: {result.stderr.strip()}")
    assert result.stdout.strip() == "", (
        "draft plans must not be committed outside a refine branch — move the draft into "
        f"docs/superpowers/plans/ or delete it:\n{result.stdout}"
    )
