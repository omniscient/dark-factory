from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / ".claude" / "skills" / "conformance"


def test_skill_md_exists_with_required_frontmatter():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: conformance" in text
    assert "allowed-tools: Read, Grep, Glob" in text
    assert "RUBRIC.md" in text


def test_rubric_matches_source_prompt_content():
    rubric = (SKILL_DIR / "RUBRIC.md").read_text(encoding="utf-8")
    source = (REPO_ROOT / "refinement-skills" / "conformance-reviewer-prompt.md").read_text(encoding="utf-8")
    assert rubric == source, "RUBRIC.md must be a faithful copy — conformance-reviewer-prompt.md needed no MarketHawk fix"


def test_rubric_preserves_output_contract():
    rubric = (SKILL_DIR / "RUBRIC.md").read_text(encoding="utf-8")
    assert "## Out-of-Scope Changes" in rubric
    assert "**Verdict:**" in rubric
    assert "$ARTIFACT_KIND" in rubric and "$SPEC_CONTENT" in rubric and "$ARTIFACT_CONTENT" in rubric
