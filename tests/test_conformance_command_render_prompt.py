from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CMD = REPO_ROOT / "commands" / "dark-factory-conformance.md"


def test_step31_uses_render_prompt_not_prose_substitution():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("### Step 3.1")
    step31 = text[idx:text.find("## Phase 3.6")]
    assert "- `$ARTIFACT_KIND` replaced with `IMPLEMENTATION`" not in step31
    assert "--set ARTIFACT_KIND=IMPLEMENTATION" in step31
    assert '--set SPEC_CONTENT=@"$SPEC_CONTENT_PATH"' in step31
    assert "conformance_artifact_content.md" in step31
    assert "conformance_prompt.md" in step31
    assert "render-prompt failed" in step31


def test_step31_handles_no_spec_fallback_path():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("### Step 3.1")
    step31 = text[idx:text.find("## Phase 3.6")]
    assert "SPEC_CONTENT_PATH" in step31
    assert "no_spec_issue_body.md" in step31


def test_step31_shadow_reuses_rendered_prompt_file():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("### Step 3.1")
    step31 = text[idx:text.find("## Phase 3.6")]
    assert "conformance_prompt.md` the Opus call just read in step 4" in step31


def test_reconcile_loop_re_renders_before_respawn():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("## Phase 3.5: RECONCILE LOOP")
    reconcile = text[idx:text.find("## Phase 4: PASS")]
    assert "render-prompt" in reconcile
    assert "conformance_prompt.md" in reconcile
