from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPT = REPO_ROOT / "refinement-skills" / "orchestrator-prompt.md"


def test_orchestrator_prompt_is_thin_stub():
    text = PROMPT.read_text(encoding="utf-8")
    assert "MarketHawk" not in text, "stub must not hardcode a product identity"
    assert "dark-factory-refine" in text, "stub must point to the canonical command"
    assert "### Phase 6" not in text, "six-phase process narration must be removed"
    assert "$ISSUE_CONTEXT" not in text, "vestigial template placeholder must be removed"
    assert "$FEEDBACK" not in text, "vestigial template placeholder must be removed"
    assert len(text.strip()) > 0, "stub file must not be deleted (context_budget.py enumerates it)"


def test_skill_md_describes_orchestrator_as_stub():
    skill = (REPO_ROOT / "refinement-skills" / "SKILL.md").read_text(encoding="utf-8")
    assert "dark-factory-refine.md" in skill
    assert "stub" in skill.lower()
