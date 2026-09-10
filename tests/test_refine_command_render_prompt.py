from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CMD = REPO_ROOT / "commands" / "dark-factory-refine.md"


def test_phase4_uses_render_prompt_not_prose_substitution():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("## Phase 4: BRAINSTORMING LOOP")
    phase4 = text[idx:]
    assert "with the $ISSUE_CONTEXT, $QA_HISTORY, and $QUESTION placeholders replaced" \
        not in phase4
    assert "render-prompt" in phase4
    assert "--template /opt/refinement-skills/product-owner-prompt.md" in phase4
    assert '--set ISSUE_CONTEXT=@"$ARTIFACTS_DIR/refine_issue_context.md"' in phase4
    assert '--set QA_HISTORY=@"$ARTIFACTS_DIR/refine_qa_history.md"' in phase4
    assert '--set QUESTION=@"$ARTIFACTS_DIR/refine_question.md"' in phase4
    assert "refine_product_owner_prompt.md" in phase4
    assert "render-prompt failed" in phase4


def test_phase4_notes_per_iteration_rerender():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("## Phase 4: BRAINSTORMING LOOP")
    phase4 = text[idx:]
    assert "repeat step 2 in full" in phase4
