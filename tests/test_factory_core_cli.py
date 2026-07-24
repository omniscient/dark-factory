import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest


def _cli(monkeypatch, **env):
    for k in ("FACTORY_OWNER", "FACTORY_REPO", "FACTORY_PROJECT_ID", "FACTORY_PRODUCT_NAME"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import factory_core.identity as identity
    importlib.reload(identity)
    import factory_core.cli as cli_mod
    importlib.reload(cli_mod)
    return cli_mod


def test_marker_prints_footer(monkeypatch, capsys):
    cli_mod = _cli(monkeypatch, FACTORY_PRODUCT_NAME="Acme")
    monkeypatch.setattr(sys, "argv", ["cli.py", "marker", "refinement"])
    cli_mod.main()
    assert capsys.readouterr().out.strip() == "*Posted by Acme Refinement Pipeline*"


def test_marker_rejects_unknown_kind(monkeypatch):
    cli_mod = _cli(monkeypatch, FACTORY_PRODUCT_NAME="Acme")
    monkeypatch.setattr(sys, "argv", ["cli.py", "marker", "not_a_kind"])
    with pytest.raises(SystemExit):
        cli_mod.main()


def test_markers_regex_prints_escaped_alternation(monkeypatch, capsys):
    cli_mod = _cli(monkeypatch, FACTORY_PRODUCT_NAME="Acme")
    monkeypatch.setattr(sys, "argv", ["cli.py", "markers-regex"])
    cli_mod.main()
    out = capsys.readouterr().out.strip()
    assert "Posted\\ by\\ Acme\\ Refinement\\ Pipeline" in out or \
        "Posted by Acme Refinement Pipeline" in out
    assert "|" in out
