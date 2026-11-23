"""#373: static content checks on commands/dark-factory-conformance.md — foreground
reviewer dispatch (Requirement 8), the Phase 3.5 no-response retry (Requirement 7) and the
Phase 6 artifact guard (Requirement 9). These prove the instructions are present and
correctly placed; they prove nothing about whether a live agent obeys them."""
import importlib.util
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CMD = REPO_ROOT / "commands" / "dark-factory-conformance.md"


def _text():
    return CMD.read_text(encoding="utf-8")


def _between(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i + len(start))]


def _flat(text):
    # Collapse whitespace so a phrase still matches after the prose is re-wrapped.
    return " ".join(text.split())


def _step_3_1():
    return _between(_text(), "### Step 3.1", "## Phase 3.6:")


def _phase_3_5():
    return _between(_text(), "## Phase 3.5: RECONCILE LOOP", "## Phase 4: PASS")


# --- Requirement 8: one assertion per dispatch site, each scoped to that step's text ---

def test_step_3_1_opus_dispatch_is_foreground():
    site = _flat(_between(_step_3_1(), "4. Spawn a conformance reviewer subagent",
                          "5. Append the subagent's output"))
    assert "run_in_background: false" in site
    assert "poll inside your turn" in site
    assert "never enter Phase 4 or Phase 5" in site


def test_step_3_1_shadow_dispatch_is_foreground():
    site = _flat(_between(_step_3_1(), "5a. If `$SHADOW_MODEL_PIN`",
                          "6. Parse the **`## Out-of-Scope Changes`**"))
    assert "run_in_background: false" in site


def test_reconcile_opus_respawn_is_foreground():
    site = _flat(_between(_phase_3_5(), "6. Re-spawn the conformance reviewer subagent",
                          "7. Prepend"))
    assert "run_in_background: false" in site
    assert "poll inside your turn" in site
    assert "never enter Phase 4 or Phase 5" in site


def test_reconcile_shadow_respawn_is_foreground():
    site = _flat(_between(_phase_3_5(), "7a. If `$SHADOW_MODEL_PIN`", "8. Parse verdict again"))
    assert "run_in_background: false" in site


def test_headless_contract_block_unchanged():
    # HEADLESS_CONTRACT_BLOCK is the canonical pre-change text (#431's single source).
    spec = importlib.util.spec_from_file_location(
        "_headless_contract", REPO_ROOT / "tests" / "test_command_headless_contract.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert _text().count(mod.HEADLESS_CONTRACT_BLOCK) == 1


# --- Requirement 7: no-response cycle skips the code fix, re-spawns, shares the budget ---

def test_reconcile_step_4_no_response_cycle_precedes_code_fix():
    section = _flat(_phase_3_5())
    step4 = section.find("4. Decide whether this cycle has a finding to fix")
    cond = section.find("**No-response cycle (#373):**")
    fix = section.find("Fix the code to align with the spec")
    assert -1 not in (step4, cond, fix)
    assert step4 < cond < fix
    branch = section[cond:fix]
    assert "go straight to step 6" in branch
    assert "MAX_CYCLES" in branch


# --- Requirement 9: Phase 6 is last and never synthesizes a verdict ---

def _phase_6():
    text = _text()
    return text[text.index("## Phase 6: ARTIFACT GUARD"):]


def test_phase_6_follows_phase_5_and_is_last():
    text = _text()
    p5 = text.find("## Phase 5: BLOCKED")
    p6 = text.find("## Phase 6: ARTIFACT GUARD")
    assert p5 != -1 and p6 > p5
    assert not re.search(r"^## ", text[p6 + len("## Phase 6"):], re.M), \
        "Phase 6 must be the file's last top-level section"


def test_phase_6_never_synthesizes_a_verdict():
    section = _flat(_phase_6())
    assert "emit_verdict" not in section
    for literal in ("STATUS: BLOCKED", "STATUS: PASS", "STATUS: ERROR"):
        assert literal not in section, literal
    assert "leave the file absent" in section
    assert "grep -q '^STATUS:'" in section
    assert "exit 1" in section
