from pathlib import Path

COMMAND_FILES = sorted(Path("commands").glob("dark-factory-*.md"))
ALL_TRACKED_FILES = COMMAND_FILES + [Path("workflows/archon-dark-factory.yaml")]


def test_no_raw_project_item_edit_or_list_in_commands():
    for f in COMMAND_FILES:
        text = f.read_text(encoding="utf-8")
        assert "gh project item-list" not in text, f
        assert "gh project item-edit" not in text, f


def test_no_raw_footer_literal_in_commands_or_workflow():
    for f in [Path("workflows/archon-dark-factory.yaml")]:
        text = f.read_text(encoding="utf-8")
        assert "Posted by ${FACTORY_PRODUCT_NAME}" not in text, f
        assert "Updated by ${FACTORY_PRODUCT_NAME}" not in text, f
