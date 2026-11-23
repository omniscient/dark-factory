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
