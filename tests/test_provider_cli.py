import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

CLI = str(Path(__file__).resolve().parents[1] / "scripts" / "factory_core" / "providers" / "cli.py")


def test_tracker_get_prints_json(monkeypatch):
    import factory_core.providers.cli as cli_mod

    class _FakeTracker:
        def get_item(self, id, fields=None):
            return {"id": id, "title": "t"}
    monkeypatch.setattr(cli_mod, "get_tracker", lambda: _FakeTracker())
    monkeypatch.setattr(sys, "argv", ["cli.py", "tracker", "get", "--id", "42"])
    cli_mod.main()


def test_codehost_find_change_prints_id(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod

    class _FakeCodeHost:
        def find_change_for(self, branch, exact=False, repo=None, fields="number"):
            return "9"
    monkeypatch.setattr(cli_mod, "get_codehost", lambda: _FakeCodeHost())
    monkeypatch.setattr(sys, "argv", ["cli.py", "codehost", "find-change", "--branch", "feat/issue-42-"])
    cli_mod.main()
    assert capsys.readouterr().out.strip() == "9"


def test_codehost_find_change_passes_repo_and_exact(monkeypatch):
    import factory_core.providers.cli as cli_mod

    seen = {}

    class _FakeCodeHost:
        def find_change_for(self, branch, exact=False, repo=None, fields="number"):
            seen.update(branch=branch, exact=exact, repo=repo)
            return None
    monkeypatch.setattr(cli_mod, "get_codehost", lambda: _FakeCodeHost())
    monkeypatch.setattr(sys, "argv", [
        "cli.py", "codehost", "find-change", "--branch", "feat/issue-42-slug",
        "--repo", "o/r", "--exact",
    ])
    cli_mod.main()
    assert seen == {"branch": "feat/issue-42-slug", "exact": True, "repo": "o/r"}


def test_codehost_find_change_details_prints_json(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod

    class _FakeCodeHost:
        def find_change_details(self, branch, exact=False, repo=None):
            return {"number": 9, "isDraft": False, "mergeable": "MERGEABLE"}
    monkeypatch.setattr(cli_mod, "get_codehost", lambda: _FakeCodeHost())
    monkeypatch.setattr(sys, "argv", [
        "cli.py", "codehost", "find-change-details", "--branch", "feat/issue-9-",
    ])
    cli_mod.main()
    assert json.loads(capsys.readouterr().out) == {
        "number": 9, "isDraft": False, "mergeable": "MERGEABLE",
    }


def test_codehost_find_change_details_prints_empty_string_on_none(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod

    class _FakeCodeHost:
        def find_change_details(self, branch, exact=False, repo=None):
            return None
    monkeypatch.setattr(cli_mod, "get_codehost", lambda: _FakeCodeHost())
    monkeypatch.setattr(sys, "argv", [
        "cli.py", "codehost", "find-change-details", "--branch", "feat/issue-9-",
    ])
    cli_mod.main()
    assert capsys.readouterr().out.strip() == ""


def test_codehost_find_change_details_passes_repo_and_exact(monkeypatch):
    import factory_core.providers.cli as cli_mod

    seen = {}

    class _FakeCodeHost:
        def find_change_details(self, branch, exact=False, repo=None):
            seen.update(branch=branch, exact=exact, repo=repo)
            return None
    monkeypatch.setattr(cli_mod, "get_codehost", lambda: _FakeCodeHost())
    monkeypatch.setattr(sys, "argv", [
        "cli.py", "codehost", "find-change-details", "--branch", "feat/issue-9-slug",
        "--repo", "o/r", "--exact",
    ])
    cli_mod.main()
    assert seen == {"branch": "feat/issue-9-slug", "exact": True, "repo": "o/r"}


def test_tracker_get_default_fields(monkeypatch):
    import factory_core.providers.cli as cli_mod

    seen = {}

    class _FakeTracker:
        def get_item(self, id, fields=None):
            seen.update(id=id, fields=fields)
            return {"id": id}
    monkeypatch.setattr(cli_mod, "get_tracker", lambda: _FakeTracker())
    monkeypatch.setattr(sys, "argv", ["cli.py", "tracker", "get", "--id", "42"])
    cli_mod.main()
    assert seen == {"id": "42", "fields": None}


def test_tracker_get_custom_fields(monkeypatch):
    import factory_core.providers.cli as cli_mod

    seen = {}

    class _FakeTracker:
        def get_item(self, id, fields=None):
            seen.update(id=id, fields=fields)
            return {"id": id}
    monkeypatch.setattr(cli_mod, "get_tracker", lambda: _FakeTracker())
    monkeypatch.setattr(sys, "argv", ["cli.py", "tracker", "get", "--id", "42", "--fields", "state"])
    cli_mod.main()
    assert seen == {"id": "42", "fields": ("state",)}


def test_tracker_get_comments_prints_json(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod

    class _FakeTracker:
        def get_comments(self, id):
            return [{"body": "hi"}]
    monkeypatch.setattr(cli_mod, "get_tracker", lambda: _FakeTracker())
    monkeypatch.setattr(sys, "argv", ["cli.py", "tracker", "get-comments", "--id", "42"])
    cli_mod.main()
    assert json.loads(capsys.readouterr().out) == [{"body": "hi"}]


def test_tracker_get_status_limits_prints_json(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod

    class _FakeTracker:
        def get_status_limits(self):
            return {"in_progress": 3, "in_review": 999}
    monkeypatch.setattr(cli_mod, "get_tracker", lambda: _FakeTracker())
    monkeypatch.setattr(sys, "argv", ["cli.py", "tracker", "get-status-limits"])
    cli_mod.main()
    assert json.loads(capsys.readouterr().out) == {"in_progress": 3, "in_review": 999}


def test_tracker_get_rate_budget_prints_json(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod

    class _FakeTracker:
        def get_rate_budget(self):
            return {"remaining": 150, "reset": 1999999999, "used": 4850, "limit": 5000}
    monkeypatch.setattr(cli_mod, "get_tracker", lambda: _FakeTracker())
    monkeypatch.setattr(sys, "argv", ["cli.py", "tracker", "get-rate-budget"])
    cli_mod.main()
    assert json.loads(capsys.readouterr().out) == {
        "remaining": 150, "reset": 1999999999, "used": 4850, "limit": 5000,
    }


def test_codehost_inline_comments_prints_json(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod

    seen = {}

    class _FakeCodeHost:
        def get_change_inline_comments(self, id, repo=None):
            seen.update(id=id, repo=repo)
            return [{"path": "a.py", "line": 1, "body": "x", "created_at": "t"}]
    monkeypatch.setattr(cli_mod, "get_codehost", lambda: _FakeCodeHost())
    monkeypatch.setattr(sys, "argv", ["cli.py", "codehost", "inline-comments", "--id", "9", "--repo", "o/r"])
    cli_mod.main()
    assert seen == {"id": "9", "repo": "o/r"}
    assert json.loads(capsys.readouterr().out) == [{"path": "a.py", "line": 1, "body": "x", "created_at": "t"}]


def test_codehost_update_body(monkeypatch, tmp_path):
    import factory_core.providers.cli as cli_mod

    seen = {}

    class _FakeCodeHost:
        def update_change_body(self, id, body):
            seen.update(id=id, body=body)
    body_file = tmp_path / "b.md"
    body_file.write_text("Closes #42\n\nold body")
    monkeypatch.setattr(cli_mod, "get_codehost", lambda: _FakeCodeHost())
    monkeypatch.setattr(sys, "argv", [
        "cli.py", "codehost", "update-body", "--id", "7", "--body-file", str(body_file),
    ])
    cli_mod.main()
    assert seen == {"id": "7", "body": "Closes #42\n\nold body"}


def test_codehost_verbs_pass_repo_through(monkeypatch):
    import factory_core.providers.cli as cli_mod

    seen = {}

    class _FakeCodeHost:
        def mark_ready(self, id, repo=None):
            seen["mark_ready"] = (id, repo)

        def merge_change(self, id, strategy="merge", delete_branch=True, repo=None):
            seen["merge_change"] = (id, strategy, delete_branch, repo)
            return True

        def get_change_checks(self, id, fields="name,bucket,link", repo=None):
            seen["get_change_checks"] = (id, fields, repo)
            return []

        def get_change_mergeable(self, id, repo=None):
            seen["get_change_mergeable"] = (id, repo)
            return "MERGEABLE"

        def get_change_reviews(self, id, repo=None):
            seen["get_change_reviews"] = (id, repo)
            return "APPROVED"
    monkeypatch.setattr(cli_mod, "get_codehost", lambda: _FakeCodeHost())

    monkeypatch.setattr(sys, "argv", ["cli.py", "codehost", "mark-ready", "--id", "7", "--repo", "o/r"])
    cli_mod.main()
    monkeypatch.setattr(sys, "argv", ["cli.py", "codehost", "merge", "--id", "7", "--repo", "o/r"])
    cli_mod.main()
    monkeypatch.setattr(sys, "argv", ["cli.py", "codehost", "checks", "--id", "7", "--repo", "o/r", "--fields", "bucket"])
    cli_mod.main()
    monkeypatch.setattr(sys, "argv", ["cli.py", "codehost", "mergeable", "--id", "7", "--repo", "o/r"])
    cli_mod.main()
    monkeypatch.setattr(sys, "argv", ["cli.py", "codehost", "reviews", "--id", "7", "--repo", "o/r"])
    cli_mod.main()

    assert seen == {
        "mark_ready": ("7", "o/r"),
        "merge_change": ("7", "merge", True, "o/r"),
        "get_change_checks": ("7", "bucket", "o/r"),
        "get_change_mergeable": ("7", "o/r"),
        "get_change_reviews": ("7", "o/r"),
    }


def test_preflight_ok_prints_ok_and_exits_0(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    monkeypatch.setattr(cli_mod, "preflight", lambda: [])
    monkeypatch.setattr(sys, "argv", ["cli.py", "preflight"])
    cli_mod.main()
    assert capsys.readouterr().out.strip() == "providers preflight: OK"


def test_preflight_failure_prints_every_problem_and_exits_1(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    monkeypatch.setattr(cli_mod, "preflight", lambda: [
        "GH_TOKEN is not set. Add it to .archon/.env",
        "Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .archon/.env",
    ])
    monkeypatch.setattr(sys, "argv", ["cli.py", "preflight"])
    with pytest.raises(SystemExit) as exc:
        cli_mod.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "ERROR: GH_TOKEN is not set. Add it to .archon/.env" in err
    assert "ERROR: Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .archon/.env" in err
