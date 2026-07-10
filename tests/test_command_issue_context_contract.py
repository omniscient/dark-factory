from pathlib import Path

COMMAND_DIR = Path(__file__).resolve().parents[1] / "commands"

ISSUE_AWARE_COMMANDS = [
    "dark-factory-refine.md",
    "dark-factory-plan.md",
    "dark-factory-implement.md",
    "dark-factory-validate.md",
    "dark-factory-conformance.md",
    "dark-factory-code-review.md",
]


def test_issue_aware_commands_name_issue_json_as_context_source():
    for name in ISSUE_AWARE_COMMANDS:
        text = (COMMAND_DIR / name).read_text(encoding="utf-8")
        assert "$ARTIFACTS_DIR/issue.json" in text, (
            f"{name} must use the persisted issue artifact as the context source"
        )


def test_issue_aware_commands_do_not_claim_hidden_conversation_context():
    forbidden = [
        "available in the conversation",
        "reads issue context from workflow",
        "reads issue/PR context from the workflow",
        "from workflow context",
    ]

    for name in ISSUE_AWARE_COMMANDS:
        text = (COMMAND_DIR / name).read_text(encoding="utf-8")
        for phrase in forbidden:
            assert phrase not in text, f"{name} still uses stale context wording: {phrase}"


def test_issue_aware_commands_frame_inline_delivery_as_sanctioned_entrypoint():
    for name in ISSUE_AWARE_COMMANDS:
        text = (COMMAND_DIR / name).read_text(encoding="utf-8")
        assert "sanctioned Archon command entrypoint" in text, (
            f"{name} must tell pasted-command runners this canonical file is authorized"
        )
