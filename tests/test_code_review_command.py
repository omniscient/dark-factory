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


def test_command_resolves_spec_file_via_push_gate_check():
    text = CMD.read_text(encoding="utf-8")
    assert 'push_gate_check.sh "docs/superpowers/specs/" "$ISSUE_NUM"' in text
    # resolved in Phase 1, before Phase 2 consumes it
    assert text.find("push_gate_check.sh") < text.find("## Phase 2")


def test_command_threads_spec_file_into_diff_rank():
    text = CMD.read_text(encoding="utf-8")
    assert '${SPEC_FILE:+--spec-file "$SPEC_FILE"}' in text
    diff_rank_pos = text.find("dark-factory/scripts/diff_rank.py")
    spec_flag_pos = text.find('${SPEC_FILE:+--spec-file "$SPEC_FILE"}')
    assert diff_rank_pos != -1 and spec_flag_pos != -1
    assert 0 < spec_flag_pos - diff_rank_pos < 400, \
        "the --spec-file flag must be part of the Phase 2 diff_rank.py invocation"
