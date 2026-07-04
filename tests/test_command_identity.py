from pathlib import Path
FILES = list(Path("commands").glob("dark-factory-*.md")) + [
    Path("commands/ceiling-revisit.md"), Path("workflows/archon-dark-factory.yaml")]

def test_no_hardcoded_slug():
    for f in FILES:
        assert "omniscient/markethawk" not in f.read_text(encoding="utf-8"), f
def test_no_hardcoded_project_id():
    for f in FILES:
        t = f.read_text(encoding="utf-8")
        assert "PVT_kwHOAAFds84BWh4w" not in t, f
        assert "PVTSSF_lAHOAAFds84BWh4wzhR1VaA" not in t, f
def test_no_literal_by_markethawk():
    for f in FILES:
        assert "by MarketHawk" not in f.read_text(encoding="utf-8"), f
