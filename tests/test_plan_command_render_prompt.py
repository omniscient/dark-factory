from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CMD = REPO_ROOT / "commands" / "dark-factory-plan.md"


def test_phase1_binds_spec_file_variable():
    text = CMD.read_text(encoding="utf-8")
    assert "SPEC_FILE" in text


def test_phase2_binds_plan_file_variable():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("## Phase 2: PLAN WRITING")
    phase2 = text[idx:text.find("## Phase 3: ARCHITECT REVIEW")]
    assert "PLAN_FILE" in phase2


def test_phase3_architect_uses_render_prompt_not_prose_substitution():
    text = CMD.read_text(encoding="utf-8")
    assert "with `$SPEC_CONTENT` and `$PLAN_CONTENT` replaced with the actual file contents" \
        not in text
    idx = text.find("## Phase 3: ARCHITECT REVIEW")
    phase3 = text[idx:text.find("## Phase 3.5")]
    assert "render-prompt" in phase3
    assert "--template /opt/refinement-skills/architect-prompt.md" in phase3
    assert '--set SPEC_CONTENT=@"$SPEC_FILE"' in phase3
    assert '--set PLAN_CONTENT=@"$PLAN_FILE"' in phase3
    assert "architect_prompt.md" in phase3
    assert "render-prompt failed" in phase3


def test_phase35_conformance_uses_render_prompt():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("## Phase 3.5: CONFORMANCE REVIEW")
    phase35 = text[idx:text.find("## Phase 4")]
    assert "--set ARTIFACT_KIND=PLAN" in phase35
    assert '--set SPEC_CONTENT=@"$SPEC_FILE"' in phase35
    assert '--set ARTIFACT_CONTENT=@"$PLAN_FILE"' in phase35
    assert "conformance_prompt.md" in phase35
    assert "render-prompt failed" in phase35
    assert "- `$ARTIFACT_KIND` replaced with `PLAN`" not in phase35


def test_phase35_shadow_and_reconcile_reuse_rendered_prompt_file():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("## Phase 3.5: CONFORMANCE REVIEW")
    phase35 = text[idx:text.find("## Phase 4")]
    assert "conformance_prompt.md` the Opus call just read this cycle" in phase35
    reconcile_idx = phase35.find("**Reconcile loop** (only if MATERIAL)")
    assert reconcile_idx != -1
    reconcile = phase35[reconcile_idx:]
    assert "render-prompt" in reconcile
    assert "conformance_prompt.md" in reconcile
