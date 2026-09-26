"""Every phase command file carries the same Headless Execution Contract block (#431).

Phase agents run headless: an ended turn ends the process. A target repo's own
CLAUDE.md may not carry those rules, and several commands never read CLAUDE.md at
all, so each commands/*.md carries them inline. HEADLESS_CONTRACT_BLOCK below is the
single source of truth for the block's text; a missing, duplicated or drifted copy
fails CI.
"""
import shutil
from pathlib import Path

COMMAND_DIR = Path(__file__).resolve().parents[1] / "commands"

EXPECTED_COMMANDS = {
    "ceiling-revisit.md",
    "dark-factory-code-review.md",
    "dark-factory-conformance.md",
    "dark-factory-implement.md",
    "dark-factory-plan.md",
    "dark-factory-refine.md",
    "dark-factory-revise-advisory.md",
    "dark-factory-validate.md",
}

BEGIN = "<!-- headless-contract:begin -->"
END = "<!-- headless-contract:end -->"

HEADLESS_CONTRACT_BLOCK = """\
<!-- headless-contract:begin -->
## Headless Execution Contract

You are running with no human attached; an ended turn ends the process.

- **Never end your turn on a question or an offer.** There is no one to answer. Decide
  per this command's instructions, act, and record any reservations in the issue comment or commit
  message instead.
- **Persist this phase's artifact before your final turn ends.** Commit (and push, where
  this command says to) any repo file this phase owns; write or post any comment, label or
  `$ARTIFACTS_DIR` artifact it owns. Work left unpersisted when the turn ends is destroyed.
- **Turn end = process end.** Scheduled wakeups do not fire (do not use `ScheduleWakeup`),
  task-notifications never arrive, and pending subagent work is destroyed — and an ended
  turn is reported as success whether or not this phase's artifact exists.
- **To wait on a background subagent, poll inside your turn** (keep issuing tool calls)
  or do the work inline — never end the turn to "wait."
<!-- headless-contract:end -->"""


def _contract_errors(text):
    """Return why `text` fails the contract; an empty list means it carries the exact block once."""
    begins, ends = text.count(BEGIN), text.count(END)
    if begins != 1 or ends != 1:
        return [f"expected exactly one marker pair, found {begins} begin / {ends} end"]
    start = text.index(BEGIN)
    stop = text.index(END) + len(END)
    if stop <= start:
        return ["end marker precedes begin marker"]
    if text[start:stop] != HEADLESS_CONTRACT_BLOCK:
        return ["marked block differs from HEADLESS_CONTRACT_BLOCK"]
    return []


def _failures(command_dir):
    """Map each commands/*.md file under `command_dir` that fails the contract to its errors."""
    failures = {}
    for path in sorted(command_dir.glob("*.md")):
        errors = _contract_errors(path.read_text(encoding="utf-8"))
        if errors:
            failures[path.name] = errors
    return failures


def test_glob_finds_every_command_file():
    found = {path.name for path in COMMAND_DIR.glob("*.md")}
    missing = EXPECTED_COMMANDS - found
    assert not missing, f"{COMMAND_DIR} glob is missing command files: {sorted(missing)}"


def test_every_command_file_carries_the_exact_headless_contract():
    failures = _failures(COMMAND_DIR)
    assert not failures, (
        "every commands/*.md must carry HEADLESS_CONTRACT_BLOCK exactly once, byte-for-byte "
        f"(#431): {failures}"
    )


def test_block_states_generic_rules_and_excludes_claude_md_specifics():
    for rule in (
        "Never end your turn on a question or an offer",
        "Persist this phase's artifact before your final turn ends",
        "Turn end = process end",
        "ScheduleWakeup",
        "poll inside your turn",
    ):
        assert rule in HEADLESS_CONTRACT_BLOCK, rule
    # CLAUDE.md-specific rules stay out of the generic block.
    assert "Hermes" not in HEADLESS_CONTRACT_BLOCK
    assert "sanctioned" not in HEADLESS_CONTRACT_BLOCK


def test_checker_accepts_the_exact_block():
    assert _contract_errors(f"# Title\n\n{HEADLESS_CONTRACT_BLOCK}\n\n## Next\n") == []


def test_checker_rejects_a_missing_block():
    assert _contract_errors("# Title\n\n## Phase 1: LOAD\n")


def test_checker_rejects_a_single_character_drift():
    drifted = HEADLESS_CONTRACT_BLOCK.replace("process end.", "process end!", 1)
    assert drifted != HEADLESS_CONTRACT_BLOCK
    assert _contract_errors(f"# Title\n\n{drifted}\n")


def test_checker_rejects_a_duplicated_block():
    assert _contract_errors(f"{HEADLESS_CONTRACT_BLOCK}\n\n{HEADLESS_CONTRACT_BLOCK}\n")


def test_real_command_dir_fails_when_a_block_is_deleted(tmp_path):
    copy = tmp_path / "commands"
    shutil.copytree(COMMAND_DIR, copy)
    target = copy / "dark-factory-conformance.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace(HEADLESS_CONTRACT_BLOCK, "", 1),
        encoding="utf-8",
    )
    assert set(_failures(copy)) == {"dark-factory-conformance.md"}


def test_real_command_dir_fails_when_a_ninth_file_has_no_block(tmp_path):
    copy = tmp_path / "commands"
    shutil.copytree(COMMAND_DIR, copy)
    (copy / "new-phase.md").write_text("# New Phase\n\n## Phase 1: LOAD\n", encoding="utf-8")
    assert set(_failures(copy)) == {"new-phase.md"}
