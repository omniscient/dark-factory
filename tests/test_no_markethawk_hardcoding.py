from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_architect_prompt_no_markethawk():
    text = (REPO_ROOT / "refinement-skills" / "architect-prompt.md").read_text(encoding="utf-8")
    assert "MarketHawk" not in text, "architect-prompt.md still hardcodes MarketHawk"


def test_product_owner_prompt_no_markethawk():
    text = (REPO_ROOT / "refinement-skills" / "product-owner-prompt.md").read_text(encoding="utf-8")
    assert "MarketHawk" not in text, "product-owner-prompt.md still hardcodes MarketHawk"
