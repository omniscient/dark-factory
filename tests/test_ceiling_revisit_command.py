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


def test_phase_4_has_duplicate_policy_guard():
    text = _text()
    assert "gh issue list" in text and "--state all" in text, (
        "Phase 4 must query the tracker for existing always-above-ceiling issues before "
        "filing, not file unconditionally (#361)"
    )
    assert "stateReason" in text and "NOT_PLANNED" in text, (
        "Phase 4 must branch on stateReason/NOT_PLANNED to distinguish a policy-declined "
        "issue (skip) from a completed cadence issue (file) — a purely textual comment "
        "without the actual gh/jq branch does not satisfy this (#361)"
    )


def test_guard_anchor_matches_filed_title_substring():
    text = _text()
    # The jq filter's search substring and the filed --title must share the same anchor
    # so the guard can never drift out of sync with what Phase 4 itself files (#361). Extract
    # both literals by regex (rather than asserting each in isolation) so this test would fail
    # if a future edit changed one anchor without the other.
    phase4_text = text[text.index("## Phase 4"):]
    filter_match = re.search(r'test\("([^"]+)"', phase4_text)
    title_match = re.search(r'--title "([^"]+)"', phase4_text)
    assert filter_match and title_match, "guard filter or filed title not found"
    assert filter_match.group(1) in title_match.group(1), (
        f"guard anchor {filter_match.group(1)!r} must be a substring of the filed title "
        f"{title_match.group(1)!r} — they must never drift apart"
    )
