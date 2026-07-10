from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / ".claude" / "skills" / "code-review"


def test_skill_md_exists_with_required_frontmatter():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: code-review" in text
    assert "allowed-tools: Read, Grep, Glob" in text
    assert "RUBRIC.md" in text


def test_rubric_has_no_markethawk_mislabel():
    rubric = (SKILL_DIR / "RUBRIC.md").read_text(encoding="utf-8")
    assert "MarketHawk" not in rubric, "RUBRIC.md must not carry the MarketHawk mislabel"


def test_rubric_matches_source_except_delabel():
    rubric = (SKILL_DIR / "RUBRIC.md").read_text(encoding="utf-8")
    source = (REPO_ROOT / "refinement-skills" / "code-review-reviewer-prompt.md").read_text(encoding="utf-8")
    rubric_lines = rubric.splitlines()
    source_lines = source.splitlines()
    assert len(rubric_lines) == len(source_lines), "only the title/opening-sentence lines should change"
    assert rubric_lines[0] == "# Code Reviewer"
    assert source_lines[0] == "# Code Reviewer — MarketHawk"
    assert rubric_lines[2] == "You are a senior code reviewer for the dark factory pipeline. You review a code"
    # every other line is untouched
    for i in range(len(rubric_lines)):
        if i in (0, 2):
            continue
        assert rubric_lines[i] == source_lines[i], f"unexpected content drift at line {i + 1}"


def test_rubric_preserves_output_contract():
    rubric = (SKILL_DIR / "RUBRIC.md").read_text(encoding="utf-8")
    assert "### Findings" in rubric
    assert "[severity] category | path:line | description" in rubric
    for sev in ("critical", "high", "medium", "low"):
        assert sev in rubric
    assert "$ISSUE_CONTEXT" in rubric and "$DIFF_CONTENT" in rubric
