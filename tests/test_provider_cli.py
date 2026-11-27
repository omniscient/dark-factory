import json
import shutil
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
    monkeypatch.setattr(cli_mod, "preflight", lambda side="run", intent=None: [])
    monkeypatch.setattr(sys, "argv", ["cli.py", "preflight"])
    cli_mod.main()
    assert capsys.readouterr().out.strip() == "providers preflight: OK"


def test_preflight_failure_prints_every_problem_and_exits_1(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    monkeypatch.setattr(cli_mod, "preflight", lambda side="run", intent=None: [
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


def test_tracker_set_status_exits_0_on_success(monkeypatch):
    import factory_core.providers.cli as cli_mod

    class _FakeTracker:
        def set_status(self, id, canonical):
            return True
    monkeypatch.setattr(cli_mod, "get_tracker", lambda: _FakeTracker())
    monkeypatch.setattr(sys, "argv", ["cli.py", "tracker", "set-status", "--id", "42", "--status", "blocked"])
    cli_mod.main()  # must not raise / must not SystemExit


def test_tracker_set_status_prints_error_and_exits_1_on_failure(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod

    class _FakeTracker:
        def set_status(self, id, canonical):
            return False
    monkeypatch.setattr(cli_mod, "get_tracker", lambda: _FakeTracker())
    monkeypatch.setattr(sys, "argv", ["cli.py", "tracker", "set-status", "--id", "42", "--status", "blocked"])
    with pytest.raises(SystemExit) as exc:
        cli_mod.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "ERROR:" in err
    assert "42" in err
    assert "blocked" in err


def test_tracker_set_status_catches_runtime_error_and_exits_1(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod

    class _FakeTracker:
        def set_status(self, id, canonical):
            raise RuntimeError("jira: POST /issue/42/transitions failed (500): boom")
    monkeypatch.setattr(cli_mod, "get_tracker", lambda: _FakeTracker())
    monkeypatch.setattr(sys, "argv", ["cli.py", "tracker", "set-status", "--id", "42", "--status", "blocked"])
    with pytest.raises(SystemExit) as exc:
        cli_mod.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "ERROR: jira: POST /issue/42/transitions failed (500): boom" in err


def test_tracker_label_exits_0_on_success(monkeypatch):
    import factory_core.providers.cli as cli_mod

    class _FakeTracker:
        def add_label(self, id, name):
            return True
        def remove_label(self, id, name):
            return True
    monkeypatch.setattr(cli_mod, "get_tracker", lambda: _FakeTracker())
    monkeypatch.setattr(
        sys, "argv",
        ["cli.py", "tracker", "label", "--id", "42", "--add", "plan-pending-review"],
    )
    cli_mod.main()  # must not raise / must not SystemExit


def test_tracker_label_prints_error_and_exits_1_on_any_failure(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod

    calls = []

    class _FakeTracker:
        def add_label(self, id, name):
            calls.append(("add", name))
            return name != "spec-pending-review"
        def remove_label(self, id, name):
            calls.append(("remove", name))
            return True
    monkeypatch.setattr(cli_mod, "get_tracker", lambda: _FakeTracker())
    monkeypatch.setattr(
        sys, "argv",
        ["cli.py", "tracker", "label", "--id", "42",
         "--add", "spec-pending-review", "--remove", "needs-discussion"],
    )
    with pytest.raises(SystemExit) as exc:
        cli_mod.main()
    assert exc.value.code == 1
    # both operations were attempted despite the first failing
    assert calls == [("add", "spec-pending-review"), ("remove", "needs-discussion")]
    err = capsys.readouterr().err
    assert "ERROR:" in err
    assert "42" in err


def test_tracker_label_catches_runtime_error_and_exits_1(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod

    class _FakeTracker:
        def add_label(self, id, name):
            raise RuntimeError("jira: PUT /issue/42 failed (500): boom")
    monkeypatch.setattr(cli_mod, "get_tracker", lambda: _FakeTracker())
    monkeypatch.setattr(
        sys, "argv",
        ["cli.py", "tracker", "label", "--id", "42", "--add", "plan-pending-review"],
    )
    with pytest.raises(SystemExit) as exc:
        cli_mod.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "ERROR: jira: PUT /issue/42 failed (500): boom" in err


def test_codehost_close_keyword_prints_snippet(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod

    class _FakeCodeHost:
        def close_keyword(self, issue_id):
            return f"Closes #{issue_id}"
    monkeypatch.setattr(cli_mod, "get_codehost", lambda: _FakeCodeHost())
    monkeypatch.setattr(sys, "argv", ["cli.py", "codehost", "close-keyword", "--id", "42"])
    cli_mod.main()
    assert capsys.readouterr().out == "Closes #42\n"


def test_codehost_close_keyword_empty_prints_blank_line(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod

    class _FakeCodeHost:
        def close_keyword(self, issue_id):
            return ""
    monkeypatch.setattr(cli_mod, "get_codehost", lambda: _FakeCodeHost())
    monkeypatch.setattr(sys, "argv", ["cli.py", "codehost", "close-keyword", "--id", "42"])
    cli_mod.main()
    assert capsys.readouterr().out.strip() == ""


def test_codehost_merge_match_head_passed_through_and_omitted_when_absent(monkeypatch):
    """#551: --match-head reaches merge_change; without it the kwarg is not passed at all,
    so a provider that predates the parameter keeps working."""
    import factory_core.providers.cli as cli_mod

    calls = []

    class _FakeCodeHost:
        def merge_change(self, id, **kw):
            calls.append((id, kw))
            return True
    monkeypatch.setattr(cli_mod, "get_codehost", lambda: _FakeCodeHost())

    monkeypatch.setattr(sys, "argv", ["cli.py", "codehost", "merge", "--id", "7",
                                      "--match-head", "b" * 40])
    cli_mod.main()
    monkeypatch.setattr(sys, "argv", ["cli.py", "codehost", "merge", "--id", "7"])
    cli_mod.main()

    assert calls[0] == ("7", {"strategy": "merge", "delete_branch": True, "repo": None,
                              "match_head": "b" * 40})
    assert calls[1] == ("7", {"strategy": "merge", "delete_branch": True, "repo": None})


# --- #557: preflight --side, model-endpoints show/stage -------------------------------------

MODEL_REGISTRY_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "model_registry"


def test_preflight_side_defaults_to_run_and_passes_scheduler_through(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    seen = []
    monkeypatch.setattr(cli_mod, "preflight",
                        lambda side="run", intent=None: seen.append((side, intent)) or [])
    for argv in (["cli.py", "preflight"], ["cli.py", "preflight", "--side", "scheduler"],
                 ["cli.py", "preflight", "--intent", ""],
                 ["cli.py", "preflight", "--intent", "classify-preview-helper"]):
        monkeypatch.setattr(sys, "argv", argv)
        cli_mod.main()
    assert seen == [("run", None), ("scheduler", None), ("run", None),
                    ("run", "classify-preview-helper")]


def _staged_fixture(tmp_path, monkeypatch):
    from factory_core.providers import model_endpoint
    staged = tmp_path / "model-registry"
    shutil.copytree(MODEL_REGISTRY_FIXTURES, staged)
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_PATH", str(staged))
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", model_endpoint.tree_sha256(staged))
    return staged


def test_model_endpoints_show_redacts_secrets(tmp_path, monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    _staged_fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("LOCAL_GATEWAY_TOKEN", "sk-sentinel-show")
    monkeypatch.delenv("REMOTE_GATEWAY_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["cli.py", "model-endpoints", "show"])
    cli_mod.main()
    out = capsys.readouterr().out
    assert "sk-sentinel-show" not in out
    view = {e["id"]: e for e in json.loads(out)["endpoints"]}
    assert view["local-same-host"]["secret_reference"] == {"name": "LOCAL_GATEWAY_TOKEN", "present": True}
    assert view["local-remote"]["secret_reference"] == {"name": "REMOTE_GATEWAY_TOKEN", "present": False}
    assert view["local-lan"]["base_url_scope"] == "lan"


def test_model_endpoints_show_refuses_staged_copy_without_digest(tmp_path, monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    _staged_fixture(tmp_path, monkeypatch)
    monkeypatch.delenv("FACTORY_MODEL_REGISTRY_SHA256")
    monkeypatch.setattr(sys, "argv", ["cli.py", "model-endpoints", "show"])
    with pytest.raises(SystemExit) as exc:
        cli_mod.main()
    assert exc.value.code == 1
    assert "FACTORY_MODEL_REGISTRY_SHA256 is not set" in capsys.readouterr().err


def test_model_endpoints_show_from_source_needs_no_digest(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    from factory_core.providers import model_endpoint
    monkeypatch.setattr(model_endpoint, "SOURCE_REGISTRY_DIR", MODEL_REGISTRY_FIXTURES)
    monkeypatch.delenv("FACTORY_MODEL_REGISTRY_SHA256", raising=False)
    monkeypatch.setattr(sys, "argv", ["cli.py", "model-endpoints", "show", "--from-source"])
    cli_mod.main()
    assert len(json.loads(capsys.readouterr().out)["endpoints"]) == 5


def test_model_endpoints_show_invalid_registry_exits_1_with_field_errors(tmp_path, monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    staged = _staged_fixture(tmp_path, monkeypatch)
    registry = staged / "model-endpoints.yaml"
    registry.write_text(registry.read_text(encoding="utf-8").replace(
        "protocol: anthropic-messages", "protocol: openai-chat", 1), encoding="utf-8")
    from factory_core.providers import model_endpoint
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", model_endpoint.tree_sha256(staged))
    monkeypatch.setattr(sys, "argv", ["cli.py", "model-endpoints", "show"])
    with pytest.raises(SystemExit) as exc:
        cli_mod.main()
    assert exc.value.code == 1
    assert "ERROR: endpoint 'local-same-host': unsupported protocol 'openai-chat'" in capsys.readouterr().err


@pytest.mark.parametrize("from_source", [False, True])
def test_model_endpoints_show_never_prints_tolerated_unknown_keys(tmp_path, monkeypatch, capsys, from_source):
    import factory_core.providers.cli as cli_mod
    from factory_core.providers import model_endpoint
    staged = _staged_fixture(tmp_path, monkeypatch)
    registry = staged / "model-endpoints.yaml"
    registry.write_text(registry.read_text(encoding="utf-8")
                        .replace('schema_version: "1.0"', 'schema_version: "1.1"', 1)
                        .replace("    secret_reference: LOCAL_GATEWAY_TOKEN\n",
                                 "    secret_reference: LOCAL_GATEWAY_TOKEN\n"
                                 "    api_key: sk-sentinel-unknown-key\n", 1), encoding="utf-8")
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", model_endpoint.tree_sha256(staged))
    monkeypatch.setattr(model_endpoint, "SOURCE_REGISTRY_DIR", staged)
    argv = ["cli.py", "model-endpoints", "show"] + (["--from-source"] if from_source else [])
    monkeypatch.setattr(sys, "argv", argv)
    cli_mod.main()
    out = capsys.readouterr().out
    assert "sk-sentinel-unknown-key" not in out and "api_key" not in out
    view = {e["id"]: e for e in json.loads(out)["endpoints"]}
    assert view["local-same-host"]["unknown_keys_omitted"] == 1


def test_model_endpoints_stage_prints_staged_digest(tmp_path, monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    from factory_core.providers import model_endpoint
    dest = tmp_path / "state" / "model-registry"
    monkeypatch.setattr(sys, "argv", ["cli.py", "model-endpoints", "stage",
                                      "--source-dir", str(MODEL_REGISTRY_FIXTURES), "--dest-dir", str(dest)])
    cli_mod.main()
    assert capsys.readouterr().out.strip() == model_endpoint.tree_sha256(dest)


def test_model_endpoints_stage_failure_exits_1(tmp_path, monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    monkeypatch.setattr(sys, "argv", ["cli.py", "model-endpoints", "stage",
                                      "--source-dir", str(tmp_path / "empty"), "--dest-dir", str(tmp_path / "d")])
    with pytest.raises(SystemExit) as exc:
        cli_mod.main()
    assert exc.value.code == 1
    assert "model endpoint registry not found" in capsys.readouterr().err
    assert not (tmp_path / "d").exists()


def test_model_endpoints_show_uses_only_the_first_read_of_the_staged_registry(tmp_path, monkeypatch, capsys):
    # Swap-and-swap-back (#593): the staged registry reads as its real bytes once, then as a
    # registry with an injected endpoint on every later read. show must print the first read.
    # Hooks os.open() (what _read_tree uses, via os.fdopen), the builtin open() of both modules
    # and Path.read_bytes(): the last two do not go through os.open, so each needs its own
    # hook for a re-read to be observed whichever primitive performs it.
    import io
    import os
    import factory_core.providers.cli as cli_mod
    from factory_core.providers import model_endpoint
    staged = _staged_fixture(tmp_path, monkeypatch).resolve()
    registry = staged / "model-endpoints.yaml"
    tampered = registry.read_bytes().replace(b"- id: local-lan", b"- id: injected", 1)
    real_os_open, real_open, real_read_bytes = os.open, open, Path.read_bytes
    reads = []

    def _serve(path):
        """False if path is not the registry; else count the read and return the tampered
        bytes, or None for the real file (first read)."""
        try:
            if Path(path).resolve() != registry:
                return False
        except OSError:
            return False
        reads.append(path)
        return None if len(reads) == 1 else tampered

    def _os_open(path, flags, *args, **kwargs):
        data = _serve(path) if isinstance(path, (str, os.PathLike)) else False
        if not data:
            return real_os_open(path, flags, *args, **kwargs)
        # Serve the tampered bytes through a throwaway regular file (unlinked once open) so
        # the caller's fstat()-based regular-file check still passes.
        tmp = f"{path}.tamper"
        fd = real_os_open(tmp, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.write(fd, data)
        os.lseek(fd, 0, os.SEEK_SET)
        os.unlink(tmp)
        return fd

    def _open(file, mode="r", *args, **kwargs):
        data = _serve(file) if isinstance(file, (str, os.PathLike)) else False
        if not data:
            return real_open(file, mode, *args, **kwargs)
        return io.BytesIO(data) if "b" in mode else io.StringIO(data.decode("utf-8"))

    def _read_bytes(self):
        data = _serve(self)
        return data if data else real_read_bytes(self)

    monkeypatch.setattr(os, "open", _os_open)
    monkeypatch.setattr(model_endpoint, "open", _open, raising=False)
    monkeypatch.setattr(cli_mod, "open", _open, raising=False)
    monkeypatch.setattr(Path, "read_bytes", _read_bytes)
    monkeypatch.setattr(sys, "argv", ["cli.py", "model-endpoints", "show"])
    cli_mod.main()
    ids = [e["id"] for e in json.loads(capsys.readouterr().out)["endpoints"]]
    assert ids == ["local-same-host", "local-lan", "local-remote", "cloud-anthropic",
                   "lan-ollama-qwen3-coder"]
    assert len(reads) == 1


def test_model_endpoints_show_staged_copy_without_registry_file_exits_1(tmp_path, monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    from factory_core.providers import model_endpoint
    staged = _staged_fixture(tmp_path, monkeypatch)
    (staged / "model-endpoints.yaml").unlink()
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", model_endpoint.tree_sha256(staged))
    monkeypatch.setattr(sys, "argv", ["cli.py", "model-endpoints", "show"])
    with pytest.raises(SystemExit) as exc:
        cli_mod.main()
    assert exc.value.code == 1
    assert "model endpoint registry not found" in capsys.readouterr().err
