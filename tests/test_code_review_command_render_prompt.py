from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CMD = REPO_ROOT / "commands" / "dark-factory-code-review.md"


def test_phase3_uses_render_prompt_not_prose_substitution():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("## Phase 3: REVIEW")
    phase3 = text[idx:text.find("## Phase 4: BUILD PAYLOAD")]
    assert "with `$ISSUE_CONTEXT` replaced by the issue context from step 1" not in phase3
    assert "render-prompt" in phase3
    assert '--set ISSUE_CONTEXT=@"$ARTIFACTS_DIR/code_review_issue_context.md"' in phase3
    assert '--set DIFF_CONTENT=@"$ARTIFACTS_DIR/review_diff.txt"' in phase3
    assert "code_review_prompt.md" in phase3
    assert "render-prompt failed" in phase3
