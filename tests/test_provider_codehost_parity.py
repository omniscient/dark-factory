import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

from factory_core import identity
from factory_core.providers.codehost.github import GitHubCodeHost


def _ok(stdout="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def test_remote_url_matches_entrypoint_construction(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_faketoken")
    url = GitHubCodeHost().remote_url()
    assert url == f"https://ghp_faketoken@github.com/{identity.SLUG}.git"


def test_find_change_for_matches_scheduler_get_pr_for_issue(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="42\n"))[1])
    pr = GitHubCodeHost().find_change_for("feat/issue-42-", repo=identity.SLUG)
    assert calls[0] == [
        "gh", "pr", "list", "--repo", identity.SLUG,
        "--search", "head:feat/issue-42-",
        "--json", "number", "--jq", ".[0].number // empty",
    ]
    assert pr == "42"


def test_find_change_for_matches_run_dag_no_repo(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout=""))[1])
    pr = GitHubCodeHost().find_change_for("feat/issue-42-")
    assert calls[0] == [
        "gh", "pr", "list",
        "--search", "head:feat/issue-42-",
        "--json", "number", "--jq", ".[0].number // empty",
    ]
    assert pr is None


def test_find_change_for_exact_head_matches_push_resolve(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="9\n"))[1])
    GitHubCodeHost().find_change_for("feat/issue-42-slug", exact=True, repo=identity.SLUG)
    assert calls[0] == [
        "gh", "pr", "list", "--repo", identity.SLUG,
        "--head", "feat/issue-42-slug",
        "--json", "number", "--jq", ".[0].number // empty",
    ]


def test_find_change_details_matches_rescue_pr_for_issue(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (
        calls.append(cmd),
        _ok(stdout='[{"number": 7, "isDraft": false, "mergeable": "MERGEABLE"}]'),
    )[1])
    details = GitHubCodeHost().find_change_details("feat/issue-7-", repo=identity.SLUG)
    assert calls[0] == [
        "gh", "pr", "list", "--repo", identity.SLUG,
        "--search", "head:feat/issue-7-",
        "--json", "number,isDraft,mergeable",
    ]
    assert details == {"number": 7, "isDraft": False, "mergeable": "MERGEABLE"}


def test_find_change_details_exact_head(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="[]"))[1])
    details = GitHubCodeHost().find_change_details("feat/issue-7-slug", exact=True)
    assert calls[0] == [
        "gh", "pr", "list", "--head", "feat/issue-7-slug",
        "--json", "number,isDraft,mergeable",
    ]
    assert details is None


def test_find_change_details_returns_none_on_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout="", returncode=1))
    assert GitHubCodeHost().find_change_details("feat/issue-7-") is None


def test_find_change_details_returns_none_on_invalid_json(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout="not json"))
    assert GitHubCodeHost().find_change_details("feat/issue-7-") is None


def test_open_change_matches_run_dag_push_and_pr(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="https://github.com/o/r/pull/9\n"))[1])
    new_id = GitHubCodeHost().open_change(None, None, "Fix the thing (#42)", "Closes #42\n\n## Summary", draft=True)
    assert calls[0] == ["gh", "pr", "create", "--title", "Fix the thing (#42)", "--body", "Closes #42\n\n## Summary", "--draft"]
    assert new_id == "9"


def test_open_change_matches_main_red_fixer_open_pr(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="https://github.com/o/r/pull/11\n"))[1])
    GitHubCodeHost().open_change(
        "fix/main-red-recovery-99", "main", "fix: main-red recovery (#99)", "body",
        draft=False, repo=identity.SLUG,
    )
    assert calls[0] == [
        "gh", "pr", "create", "--repo", identity.SLUG, "--base", "main",
        "--head", "fix/main-red-recovery-99",
        "--title", "fix: main-red recovery (#99)", "--body", "body",
    ]


def test_update_change_body_matches_run_dag_backfill(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubCodeHost().update_change_body("7", "Closes #42\n\nold body")
    assert calls[0] == ["gh", "pr", "edit", "7", "--body", "Closes #42\n\nold body"]


def test_mark_ready_matches_run_dag_close_preview(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubCodeHost().mark_ready("7")
    assert calls[0] == ["gh", "pr", "ready", "7"]


def test_mark_ready_matches_rescue_py(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubCodeHost().mark_ready("7", repo=identity.SLUG)
    assert calls[0] == ["gh", "pr", "ready", "7", "--repo", identity.SLUG]


def test_merge_change_matches_run_dag_close_preview(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(returncode=0))[1])
    ok = GitHubCodeHost().merge_change("9")
    assert calls[0] == ["gh", "pr", "merge", "9", "--merge", "--delete-branch"]
    assert ok is True


def test_merge_change_matches_main_red_fixer(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(returncode=0))[1])
    GitHubCodeHost().merge_change("9", repo=identity.SLUG)
    assert calls[0] == ["gh", "pr", "merge", "9", "--repo", identity.SLUG, "--merge", "--delete-branch"]


def test_get_change_checks_matches_scheduler_failing_checks_for_pr(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="[]"))[1])
    GitHubCodeHost().get_change_checks("9", fields="name,bucket,link", repo=identity.SLUG)
    assert calls[0] == ["gh", "pr", "checks", "9", "--repo", identity.SLUG, "--json", "name,bucket,link"]


def test_get_change_checks_matches_rescue_py(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="[]"))[1])
    GitHubCodeHost().get_change_checks("9", fields="bucket", repo=identity.SLUG)
    assert calls[0] == ["gh", "pr", "checks", "9", "--repo", identity.SLUG, "--json", "bucket"]


def test_get_change_checks_green_exit_path_unchanged(monkeypatch):
    """Byte-for-byte: a zero-exit response returns exactly what it always did."""
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (
        calls.append(cmd),
        _ok(stdout='[{"name": "ci", "bucket": "pass", "link": "u"}]', returncode=0),
    )[1])
    checks = GitHubCodeHost().get_change_checks("9", repo=identity.SLUG)
    assert calls[0] == ["gh", "pr", "checks", "9", "--repo", identity.SLUG, "--json", "name,bucket,link"]
    assert checks == [{"name": "ci", "bucket": "pass", "link": "u"}]


def test_get_change_checks_returns_data_on_nonzero_exit_with_valid_json(monkeypatch):
    """The failing/pending path: gh exits nonzero but stdout is valid JSON — must not be discarded."""
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(
        stdout='[{"name": "ci", "bucket": "fail", "link": "u"}]', returncode=1,
    ))
    checks = GitHubCodeHost().get_change_checks("9", repo=identity.SLUG)
    assert checks == [{"name": "ci", "bucket": "fail", "link": "u"}]


def test_get_change_checks_empty_list_on_invalid_json_regardless_of_exit_code(monkeypatch):
    """A genuine error (empty/invalid stdout) still yields [] on both exit codes."""
    for code in (0, 1):
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout="not json", returncode=code))
        assert GitHubCodeHost().get_change_checks("9") == []
    for code in (0, 1):
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout="", returncode=code))
        assert GitHubCodeHost().get_change_checks("9") == []


def test_get_change_mergeable_matches_scheduler_check_pr_mergeable(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="MERGEABLE\n"))[1])
    state = GitHubCodeHost().get_change_mergeable("9", repo=identity.SLUG)
    assert calls[0] == [
        "gh", "pr", "view", "9", "--repo", identity.SLUG,
        "--json", "mergeable", "--jq", ".mergeable",
    ]
    assert state == "MERGEABLE"


def test_get_change_mergeable_defaults_unknown_on_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _ok(stdout="", returncode=1))
    assert GitHubCodeHost().get_change_mergeable("9") == "UNKNOWN"


def test_get_change_reviews_matches_scheduler_end_gate_check(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="APPROVED\n"))[1])
    state = GitHubCodeHost().get_change_reviews("9", repo=identity.SLUG)
    assert calls[0] == [
        "gh", "pr", "view", "9", "--repo", identity.SLUG, "--json", "reviews", "--jq",
        '[.reviews[] | select(.state == "APPROVED" or .state == "CHANGES_REQUESTED")] | last | .state // ""',
    ]
    assert state == "APPROVED"


def test_get_change_inline_comments_matches_run_dag_fetch_issue(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="[]"))[1])
    GitHubCodeHost().get_change_inline_comments("9", repo=identity.SLUG)
    assert calls[0] == [
        "gh", "api", f"repos/{identity.SLUG}/pulls/9/comments",
        "--jq", "[.[] | {path: .path, line: .line, body: .body, created_at: .created_at}]",
    ]


def test_close_keyword_matches_run_dag_and_main_red_fixer():
    kw = GitHubCodeHost().close_keyword("42")
    assert kw == "Closes #42"


def test_close_keyword_opaque_id_passthrough():
    assert GitHubCodeHost().close_keyword("PROJ-123") == "Closes #PROJ-123"


def test_required_env_returns_gh_token():
    assert GitHubCodeHost.required_env() == ["GH_TOKEN"]


_CODEHOST_ID_CASES = ["42", "PROJ-123"]


@pytest.mark.parametrize("opaque_id", _CODEHOST_ID_CASES)
def test_codehost_methods_pass_opaque_id_through_unchanged(monkeypatch, opaque_id):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubCodeHost().update_change_body(opaque_id, "body")
    assert opaque_id in calls[-1]

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubCodeHost().mark_ready(opaque_id)
    assert opaque_id in calls[-1]

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    GitHubCodeHost().merge_change(opaque_id)
    assert opaque_id in calls[-1]

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="[]"))[1])
    GitHubCodeHost().get_change_checks(opaque_id)
    assert opaque_id in calls[-1]

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout=""))[1])
    GitHubCodeHost().get_change_mergeable(opaque_id)
    assert opaque_id in calls[-1]

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout=""))[1])
    GitHubCodeHost().get_change_reviews(opaque_id)
    assert opaque_id in calls[-1]

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok(stdout="[]"))[1])
    GitHubCodeHost().get_change_inline_comments(opaque_id)
    assert opaque_id in calls[-1][2]  # embedded in the REST path string

    assert GitHubCodeHost().close_keyword(opaque_id) == f"Closes #{opaque_id}"
