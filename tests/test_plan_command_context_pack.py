from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAN_CMD = REPO_ROOT / "commands" / "dark-factory-plan.md"
PLAN_CMD_MIRROR = REPO_ROOT / ".archon" / "commands" / "dark-factory-plan.md"


def test_plan_has_context_pack_presence_check():
    text = PLAN_CMD.read_text(encoding="utf-8")
    assert "context-pack.md" in text
    assert "## claude_md" in text
    assert "## spec" in text


def test_plan_command_mirrors_are_identical():
    assert PLAN_CMD.read_text(encoding="utf-8") == PLAN_CMD_MIRROR.read_text(encoding="utf-8")
