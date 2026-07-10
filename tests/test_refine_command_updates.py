from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REFINE_CMD = REPO_ROOT / "commands" / "dark-factory-refine.md"
REFINE_CMD_MIRROR = REPO_ROOT / ".archon" / "commands" / "dark-factory-refine.md"


def test_refine_has_context_pack_presence_check():
    text = REFINE_CMD.read_text(encoding="utf-8")
    assert "context-pack.md" in text
    assert "## claude_md" in text
    assert "## architecture_md" in text


def test_refine_command_mirrors_are_identical():
    assert REFINE_CMD.read_text(encoding="utf-8") == REFINE_CMD_MIRROR.read_text(encoding="utf-8")


def test_refine_has_focus_questions_migrated():
    text = REFINE_CMD.read_text(encoding="utf-8")
    assert "Focus questions on" in text
    assert "integration points with existing code" in text.lower()


def test_refine_phase4_lead_in_is_not_circular():
    text = REFINE_CMD.read_text(encoding="utf-8")
    assert "Follow the process in `orchestrator-prompt.md`" not in text, (
        "orchestrator-prompt.md is now a stub that points back at this command; "
        "the Phase 4 lead-in must not point to it as if it holds the process"
    )
