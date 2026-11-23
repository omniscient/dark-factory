"""Static-assertion tests for commands/dark-factory-validate.md Phase 0 prose and the
checker it calls (scripts/blast_gate_check.sh, #551).

No bash-execution harness exists for command files in this repo (see
tests/test_ceiling_revisit_command.py) -- these assert on the literal fenced-block
text instead. The checker itself is executed by tests/test_blast_gate_check.sh.
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
COMMAND_FILE = _ROOT / "commands" / "dark-factory-validate.md"
CHECKER = _ROOT / "scripts" / "blast_gate_check.sh"


def _text():
    return COMMAND_FILE.read_text(encoding="utf-8")


def _phase0():
    text = _text()
    return text[text.index("## Phase 0:"):text.index("## Phase 1:")]


def test_no_working_tree_kill_switch_precheck():
    """Requirement 8 (F1): Phase 0 must not read blast_radius.enabled from the working
    tree and skip the gate invocation before it runs -- that would silently reopen the
    bypass this ticket closes."""
    text = _text()
    assert "BLAST_ENABLED" not in text
    assert "d.get('blast_radius', {}).get('enabled'" not in text


def test_phase0_calls_the_checker_not_the_gate():
    phase0 = _phase0()
    assert '--issue "$ISSUE_NUM" --mode run --out "$ARTIFACTS_DIR/blast.md"' in phase0
    assert "--changed-files-stdin" not in phase0, "Phase 0 must not invoke the gate inline any more"
    assert "GATE_SCRIPT" not in phase0
    assert "--hold" not in phase0, "the hold steps belong to the blast-gate node"


def test_phase0_prefers_the_baked_checker():
    phase0 = _phase0()
    assert "_BGC=/opt/dark-factory/scripts/blast_gate_check.sh" in phase0
    assert '[ -f "$_BGC" ] || _BGC="dark-factory/scripts/blast_gate_check.sh"' in phase0


def test_phase0_no_longer_comments_labels_or_moves_the_board():
    phase0 = _phase0()
    for forbidden in ("gh issue comment", "tracker label", "set-status", "Validate issue"):
        assert forbidden not in phase0, forbidden


def test_checker_passes_base_ref_clone_dir_and_trusted_merge_base():
    text = CHECKER.read_text(encoding="utf-8")
    assert '--base-ref "$base"' in text
    assert '--clone-dir "$wt_dir"' in text
    assert 'merge-base "$TRUSTED_REF" "$HEAD_SHA"' in text
    assert '"+refs/heads/main:${TRUSTED_REF}"' in text


def test_checker_prefers_baked_gate_with_fallback():
    text = CHECKER.read_text(encoding="utf-8")
    assert '/opt/dark-factory/scripts}/gate_blast_radius.py' in text
    assert '[ -f "$gate" ] || gate="${SCRIPT_DIR}/gate_blast_radius.py"' in text
