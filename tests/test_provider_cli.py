import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

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
