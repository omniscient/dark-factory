"""Static-assertion tests for commands/dark-factory-validate.md Phase 0 prose.

No bash-execution harness exists for command files in this repo (see
tests/test_ceiling_revisit_command.py) -- these assert on the literal fenced-block
text instead.
"""
from pathlib import Path

COMMAND_FILE = Path(__file__).resolve().parents[1] / "commands" / "dark-factory-validate.md"


def _text():
    return COMMAND_FILE.read_text(encoding="utf-8")


def test_no_working_tree_kill_switch_precheck():
    """Requirement 8 (F1): Phase 0 must not read blast_radius.enabled from the working
    tree and skip the gate invocation before it runs -- that would silently reopen the
    bypass this ticket closes."""
    text = _text()
    assert "BLAST_ENABLED" not in text
    assert "d.get('blast_radius', {}).get('enabled'" not in text


def test_gate_invoked_with_base_ref_and_clone_dir():
    text = _text()
    assert "--base-ref \"$BASE_SHA\"" in text
    assert "--clone-dir \"$REPO_ROOT\"" in text
    assert 'BASE_SHA=$(git merge-base main HEAD' in text


def test_gate_prefers_baked_script_with_fallback():
    text = _text()
    assert '/opt/dark-factory/scripts/gate_blast_radius.py' in text
    assert 'dark-factory/scripts/gate_blast_radius.py' in text
    assert '[ -f "$GATE_SCRIPT" ]' in text
