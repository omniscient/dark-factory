"""Static-assertion tests for commands/ceiling-revisit.md prose.

There is no bash-execution harness for command files in this repo (see
tests/test_command_issue_number_mandate.py for the established convention) —
these tests assert on the literal text of the fenced gh/jq commands instead.
"""
import re
from pathlib import Path

COMMAND_FILE = Path(__file__).resolve().parents[1] / "commands" / "ceiling-revisit.md"


def _text():
    return COMMAND_FILE.read_text(encoding="utf-8")


def test_filed_issue_title_is_corrected():
    text = _text()
    assert "Revisit XL=always-above-ceiling rule" in text, (
        "Phase 4 must file issues titled with the corrected XL rule name (#361)"
    )
    assert "Revisit L=always-above-ceiling rule" not in text, (
        "Phase 4 must not still file issues with the stale L rule name (#361)"
    )


def test_filed_issue_body_cites_scheduler_lib():
    text = _text()
    assert "scripts/scheduler_lib.sh" in text, (
        "Phase 4's filed issue body must cite scripts/scheduler_lib.sh, not scheduler.sh (#361)"
    )


def test_target_path_markers_preserved():
    text = _text()
    assert text.count("# TARGET-PATH") == 2, (
        "Phase 1's two '# TARGET-PATH' markers on the python3 dark-factory/scripts/... lines "
        "must survive this text/logic fix untouched (#361 is not a path fix)"
    )
