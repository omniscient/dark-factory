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


def test_plan_command_oos_gate_allowlist_includes_spec_and_memory_prefixes():
    """Regression test for #293: the plan-phase OOS gate must not treat the
    refine phase's own spec/memory commits (already on the branch when plan
    runs) as out-of-scope, or it silently deletes them."""
    text = (COMMAND_DIR / "dark-factory-plan.md").read_text(encoding="utf-8")
    oos_lines = [line for line in text.splitlines() if "oos_excise.sh" in line]
    assert oos_lines, "dark-factory-plan.md must call oos_excise.sh"
    line = oos_lines[0]
    for prefix in ("docs/superpowers/plans/", "docs/superpowers/specs/", ".archon/memory/"):
        assert prefix in line, (
            f"dark-factory-plan.md's oos_excise.sh invocation is missing allowed "
            f"prefix {prefix!r} — line: {line!r}"
        )
    for stray in ("\"docs/\"", "\"scripts/\""):
        assert stray not in line, (
            f"dark-factory-plan.md's oos_excise.sh invocation over-widened to a bare "
            f"prefix {stray!r} — line: {line!r}"
        )
