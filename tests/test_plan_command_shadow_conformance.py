import pathlib

REPO_ROOT = pathlib.Path(__file__).parent.parent
CMD = REPO_ROOT / "commands" / "dark-factory-plan.md"


def test_phase_3_5_resolves_shadow_model_pin():
    text = CMD.read_text(encoding="utf-8")
    assert "CONFORMANCE_SHADOW_MODEL" in text
    assert "SHADOW_MODEL_PIN" in text


def test_phase_3_5_first_pass_spawns_shadow_after_opus():
    text = CMD.read_text(encoding="utf-8")
    opus_pos = text.find('`description`: "Conformance review: plan vs spec (cycle N)"')
    shadow_pos = text.find('`description`: "Conformance shadow (fable): plan vs spec (cycle N)"')
    assert opus_pos != -1 and shadow_pos != -1
    assert opus_pos < shadow_pos, "shadow spawn must be documented after the gating Opus spawn"


def test_reconcile_loop_mirrors_shadow_spawn():
    text = CMD.read_text(encoding="utf-8")
    assert "SHADOW_DIALOGUE" in text
    # reconcile loop step 8 area must reference re-spawning the shadow subagent, not just the
    # first pass, per Requirement 2 ("mirrors every Opus spawn, including reconcile re-spawns")
    reconcile_idx = text.find("**Reconcile loop** (only if MATERIAL)")
    assert reconcile_idx != -1
    assert "SHADOW_DIALOGUE" in text[reconcile_idx:]


def test_shadow_dialogue_never_feeds_conformance_dialogue():
    text = CMD.read_text(encoding="utf-8")
    # CONFORMANCE_DIALOGUE assignments/appends must never read from SHADOW_DIALOGUE
    assert "CONFORMANCE_DIALOGUE=\"$SHADOW_DIALOGUE\"" not in text
    assert "CONFORMANCE_DIALOGUE=\"${SHADOW_DIALOGUE" not in text


def test_publish_comment_includes_shadow_subsection():
    text = CMD.read_text(encoding="utf-8")
    assert "### Shadow (Fable) Review" in text
    assert "SHADOW_STATUS" in text


def test_inline_opus_pin_count_unchanged():
    text = CMD.read_text(encoding="utf-8")
    assert text.count("claude-opus-4-8") >= 2  # unchanged from test_verifier_contract_doc_referenced.py::test_every_command_file_keeps_inline_model_pin
