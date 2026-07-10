from pathlib import Path

CMD = Path(__file__).resolve().parents[1] / "commands" / "dark-factory-plan.md"


def test_phase_3_5_reads_clone_live_rubric_before_baked_fallback():
    text = CMD.read_text(encoding="utf-8")
    assert ".claude/skills/conformance/RUBRIC.md" in text
    assert "/opt/refinement-skills/conformance-reviewer-prompt.md" in text
    clone_pos = text.find(".claude/skills/conformance/RUBRIC.md")
    baked_pos = text.find("/opt/refinement-skills/conformance-reviewer-prompt.md")
    assert clone_pos < baked_pos, "clone-live path must be named before the baked fallback"
