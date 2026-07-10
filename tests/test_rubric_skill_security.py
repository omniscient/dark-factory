"""Presence assertions for the #46 skill-security RUBRIC guidance.

Prose read by subagents isn't unit-testable for behavior, but these guard
against silent deletion of the required instruction strings.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CODE_REVIEW_RUBRIC = REPO_ROOT / ".claude" / "skills" / "code-review" / "RUBRIC.md"
CONFORMANCE_RUBRIC = REPO_ROOT / ".claude" / "skills" / "conformance" / "RUBRIC.md"


def test_code_review_rubric_has_skill_security_guidance():
    text = CODE_REVIEW_RUBRIC.read_text(encoding="utf-8")
    assert "skill-security" in text
    assert "allowed-tools" in text and "disallowed-tools" in text
    assert "Bash(*)" in text
    assert "context: fork" in text
    assert "# justification:" in text
    assert "shell=True" in text


def test_conformance_rubric_has_skill_security_carve_out():
    text = CONFORMANCE_RUBRIC.read_text(encoding="utf-8")
    assert ".claude/skills/**" in text
    assert ".factory/hooks/**" in text
    assert "[OOS]" in text
