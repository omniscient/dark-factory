"""Regression test for issue #42 — Dark Factory Claude Skills conventions and safety policy.

Pins the spec's five acceptance-criteria-bearing rules to exact substrings so a future edit
that silently weakens the policy (e.g. loosening the disable-model-invocation default, or
dropping a path from Review Expectations) fails CI instead of passing silently.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = REPO_ROOT / "docs" / "superpowers" / "specs" / "2026-07-10-dark-factory-claude-skills-design.md"
README = REPO_ROOT / "README.md"


def _spec_text():
    assert SPEC.exists(), f"{SPEC} does not exist — issue #42's policy doc must live here"
    return SPEC.read_text(encoding="utf-8")


def test_ac1_policy_doc_exists_at_appropriate_docs_path():
    # Acceptance criterion 1: "Add a design/policy doc under an appropriate docs path."
    assert SPEC.exists()
    assert SPEC.is_relative_to(REPO_ROOT / "docs")


def test_ac2_side_effecting_skills_require_disable_model_invocation_by_default():
    # Acceptance criterion 2: implement/merge/close/deploy-like skills must not be
    # auto-triggered unless explicitly justified.
    text = _spec_text()
    assert "structurally incapable" in text
    assert "is **mandatory by default**" in text
    assert "# justification:" in text


def test_ac3_bare_bash_wildcard_is_banned():
    # Acceptance criterion 3: ban broad `allowed-tools: Bash(*)` style permissions.
    text = _spec_text()
    assert "banned unconditionally" in text
    assert "Bash(gh:*)" in text
    assert "Bash(git:*)" in text


def test_ac4_dynamic_injection_requires_compact_artifact_scripts():
    # Acceptance criterion 4: require dynamic injection to call compact artifact scripts,
    # not raw cat/git diff/comment dumps.
    text = _spec_text()
    assert "Raw `cat ARCHITECTURE.md`, raw `git diff`, and" in text
    for script in (
        "architecture_slice.py",
        "memory_retrieve.py",
        "comment_digest.py",
        "diff_rank.py",
    ):
        assert script in text, f"{script} missing from injection policy"


def test_ac5_review_expectations_cover_required_paths():
    # Acceptance criterion 5: define review expectations for .claude/skills/**,
    # .claude/settings.json, hooks, and plugin config.
    text = _spec_text()
    assert "### 7. Review Expectations" in text
    assert ".claude/skills/**" in text
    assert ".claude/settings.json" in text
    assert ".factory/hooks/**" in text


def test_readme_links_the_policy_spec():
    text = README.read_text(encoding="utf-8")
    assert "docs/superpowers/specs/2026-07-10-dark-factory-claude-skills-design.md" in text, (
        "README.md's ## Further reading list must link the #42 Claude Skills policy spec"
    )
