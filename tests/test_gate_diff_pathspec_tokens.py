from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFORMANCE_CMD = REPO_ROOT / "commands" / "dark-factory-conformance.md"
CODE_REVIEW_CMD = REPO_ROOT / "commands" / "dark-factory-code-review.md"

EXPECTED_TOKEN_SEQUENCE = "':!docs/*.md' ':!evals/*.md' ':!bench/*.md'"


def test_conformance_command_has_new_token_sequence_exactly_twice():
    text = CONFORMANCE_CMD.read_text(encoding="utf-8")
    assert text.count(EXPECTED_TOKEN_SEQUENCE) == 2, (
        "commands/dark-factory-conformance.md must use the "
        f"{EXPECTED_TOKEN_SEQUENCE!r} pathspec sequence at both diff call sites "
        "(Step 3.0.1 RAW_DIFF and the Phase 3.5 reconcile-loop diff refresh)"
    )


def test_code_review_command_has_new_token_sequence_exactly_once():
    text = CODE_REVIEW_CMD.read_text(encoding="utf-8")
    assert text.count(EXPECTED_TOKEN_SEQUENCE) == 1, (
        "commands/dark-factory-code-review.md must use the "
        f"{EXPECTED_TOKEN_SEQUENCE!r} pathspec sequence at its Phase 2 diff call site"
    )


def test_neither_command_still_uses_blanket_md_exclusion():
    for path in (CONFORMANCE_CMD, CODE_REVIEW_CMD):
        text = path.read_text(encoding="utf-8")
        assert "':!*.md'" not in text, (
            f"{path.name} must not reintroduce the blanket ':!*.md' exclusion — "
            "it hides commands/*.md, refinement-skills/*.md and other "
            "executable-policy content from the reviewer (issue #399)"
        )
