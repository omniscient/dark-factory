import pathlib

REPO_ROOT = pathlib.Path(__file__).parent.parent
CMD = REPO_ROOT / "commands" / "dark-factory-conformance.md"


def test_phase_1_resolves_shadow_model_pin():
    text = CMD.read_text(encoding="utf-8")
    assert "CONFORMANCE_SHADOW_MODEL" in text
    assert "SHADOW_MODEL_PIN" in text


def test_step_3_1_spawns_shadow_after_opus():
    text = CMD.read_text(encoding="utf-8")
    opus_pos = text.find('`description`: "Conformance review: code vs spec"')
    shadow_pos = text.find('`description`: "Conformance shadow (fable): code vs spec"')
    assert opus_pos != -1 and shadow_pos != -1
    assert opus_pos < shadow_pos


def test_reconcile_loop_mirrors_shadow_spawn():
    text = CMD.read_text(encoding="utf-8")
    reconcile_idx = text.find("## Phase 3.5: RECONCILE LOOP")
    assert reconcile_idx != -1
    assert "SHADOW_DIALOGUE" in text[reconcile_idx:]


def test_phase_3_6_oos_scan_reads_conformance_dialogue_only():
    text = CMD.read_text(encoding="utf-8")
    scope_idx = text.find("## Phase 3.6: SCOPE REMEDIATION")
    blocked_idx = text.find("## Phase 3.5: RECONCILE LOOP")
    section = text[scope_idx:blocked_idx] if scope_idx < blocked_idx else text[scope_idx:]
    assert "SHADOW_DIALOGUE" not in section, "Phase 3.6 OOS scan must never read shadow output"


def test_phase_4_pass_emits_shadow_fields_and_marker_comment():
    text = CMD.read_text(encoding="utf-8")
    phase4_idx = text.find("## Phase 4: PASS")
    phase5_idx = text.find("## Phase 5: BLOCKED")
    section = text[phase4_idx:phase5_idx]
    assert "SHADOW_MODEL" in section
    assert "df-shadow-review" in section


def test_phase_5_blocked_folds_shadow_block():
    text = CMD.read_text(encoding="utf-8")
    phase5_idx = text.find("## Phase 5: BLOCKED")
    section = text[phase5_idx:]
    assert "SHADOW_MODEL" in section


def test_inline_opus_pin_count_unchanged():
    text = CMD.read_text(encoding="utf-8")
    assert text.count("claude-opus-4-8") >= 1
