from pathlib import Path

CMD = Path(__file__).resolve().parents[1] / "commands" / "dark-factory-code-review.md"


def test_command_wires_the_contract():
    text = CMD.read_text(encoding="utf-8")
    # reads config + kill-switch
    assert "code_review" in text and "enabled" in text
    # calls the helper
    assert "code_review_payload.py" in text
    # reads the clone-live rubric first, falls back to the baked /opt path
    assert ".claude/skills/code-review/RUBRIC.md" in text
    assert "/opt/refinement-skills/code-review-reviewer-prompt.md" in text
    clone_pos = text.find(".claude/skills/code-review/RUBRIC.md")
    baked_pos = text.find("/opt/refinement-skills/code-review-reviewer-prompt.md")
    assert clone_pos < baked_pos, "clone-live path must be named before the baked fallback"
    # mirrors conformance's pre-triage diff exclusions
    assert "':!*.lock'" in text and "':!.archon/memory/**'" in text
    # blocking path routes the board-move through the shared tracker seam (#181 R1)
    assert "tracker set-status" in text and "--status blocked" in text
    # posts the review via the Pulls Reviews API
    assert "/pulls/" in text and "/reviews" in text
    # writes the artifact the report node reads
    assert "review.md" in text
