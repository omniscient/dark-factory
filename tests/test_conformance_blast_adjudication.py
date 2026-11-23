"""#551: the conformance phase must not decide whether the blast-radius gate was
"genuinely resolved" -- enforcement is deterministic (blast-gate node, push-and-pr
re-check, close-time check). Static assertions on the command text."""
from pathlib import Path

_CMD = Path(__file__).resolve().parents[1] / "commands" / "dark-factory-conformance.md"


def _text():
    return _CMD.read_text(encoding="utf-8")


def test_phase1_states_conformance_does_not_adjudicate_blast_radius():
    text = _text()
    phase1 = text[text.index("## Phase 1: LOAD"):text.index("## Phase 2:")]
    assert "Conformance does not adjudicate the blast-radius gate." in phase1
    for name in ("`blast.md`", "`blast-gate`", "`push-and-pr` re-check", "close-time check"):
        assert name in phase1, name


def test_phase5_label_and_board_writes_unchanged():
    text = _text()
    phase5 = text[text.index("## Phase 5: BLOCKED"):]
    assert "tracker label --id $ISSUE_NUM --add needs-discussion" in phase5
    assert 'tracker set-status --id "$ISSUE_NUM" --status blocked' in phase5
