from pathlib import Path

COMMAND_FILES = sorted(Path("commands").glob("dark-factory-*.md"))
ALL_TRACKED_FILES = COMMAND_FILES + [Path("workflows/archon-dark-factory.yaml")]


def test_no_raw_project_item_edit_or_list_in_commands():
    for f in COMMAND_FILES:
        text = f.read_text(encoding="utf-8")
        assert "gh project item-list" not in text, f
        assert "gh project item-edit" not in text, f
