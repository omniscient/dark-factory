from pathlib import Path

COMMAND = Path(__file__).resolve().parents[1] / "commands" / "dark-factory-implement.md"


def _continue_section() -> str:
    text = COMMAND.read_text(encoding="utf-8")
    start = text.index('### If intent is "continue"')
    end = text.index('### If intent is "new"')
    return text[start:end]


def test_continue_intent_checks_for_comment_digest():
    section = _continue_section()
    assert '[ -s "$ARTIFACTS_DIR/comment-digest.md" ]' in section, (
        "continue-intent Phase 1 must check for the comment-digest artifact by "
        "presence/non-emptiness, not a script exit code"
    )
    assert "FEEDBACK_SOURCE" in section


def test_continue_intent_prefers_digest_over_raw_arrays():
    section = _continue_section()
    assert "comment-digest.md" in section
    assert "do not separately re-read the raw arrays it was built from" in section


def test_continue_intent_keeps_raw_array_fallback():
    section = _continue_section()
    assert "Read the latest issue comments (bottom of the `comments` array)" in section
    assert "Read `pr_reviews` if present" in section
    assert "Read `pr_inline_comments` if present" in section


def test_continue_intent_digest_check_precedes_raw_array_fallback():
    section = _continue_section()
    digest_pos = section.index('[ -s "$ARTIFACTS_DIR/comment-digest.md" ]')
    fallback_pos = section.index(
        "Read the latest issue comments (bottom of the `comments` array)"
    )
    assert digest_pos < fallback_pos, (
        "the digest presence-check must appear before the raw-array fallback instructions"
    )


def test_continue_intent_keeps_branch_review_and_focus_steps():
    section = _continue_section()
    assert "git log --oneline main..HEAD" in section
    assert "Focus exclusively on addressing the feedback" in section
