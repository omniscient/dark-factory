import importlib, os, sys
sys.path.insert(0, "scripts")

def _fresh(monkeypatch, **env):
    for k in ("FACTORY_OWNER", "FACTORY_REPO", "FACTORY_PROJECT_ID", "FACTORY_PRODUCT_NAME"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import factory_core.identity as identity
    return importlib.reload(identity)

def test_defaults_are_markethawk(monkeypatch):
    ident = _fresh(monkeypatch)
    assert ident.SLUG == "omniscient/markethawk"
    assert ident.PROJECT_ID == "PVT_kwHOAAFds84BWh4w"
    assert ident.PROJECT_NUMBER == "1"
    assert ident.STATUS["done"] == "98236657"
    assert ident.marker("factory") == "*Posted by MarketHawk Dark Factory*"
    assert ident.marker("scheduler") == "*Posted by MarketHawk Backlog Scheduler*"

def test_env_overrides(monkeypatch):
    ident = _fresh(monkeypatch, FACTORY_OWNER="acme", FACTORY_REPO="widgets",
                   FACTORY_PRODUCT_NAME="Acme")
    assert ident.SLUG == "acme/widgets"
    assert ident.marker("factory") == "*Posted by Acme Dark Factory*"

def test_board_consumes_identity(monkeypatch):
    _fresh(monkeypatch, FACTORY_REPO="widgets")
    import factory_core.board as board
    importlib.reload(board)
    assert board.REPO == "widgets"
