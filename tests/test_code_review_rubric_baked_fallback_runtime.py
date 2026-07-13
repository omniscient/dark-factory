"""Runtime verification (issue #49) that the code-review rubric's clone-live/baked-fallback
resolution — built by #44 in scripts/context_budget.py's _resolve_skill_prompt — actually
falls back to the baked /opt/refinement-skills/code-review-reviewer-prompt.md copy when the
clone-live .claude/skills/code-review/RUBRIC.md file is absent, rather than failing, returning
None, or silently degrading to a no-op. This exercises the behavior tests/test_code_review_command.py
only asserts as path-string ordering, never at runtime. No production code changes — this
verifies #44's existing resolution logic.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import context_budget as cb


def test_falls_back_to_baked_code_review_prompt_when_clone_live_rubric_absent(tmp_path, monkeypatch):
    baked_dir = tmp_path / "baked"
    baked_dir.mkdir()
    (baked_dir / "code-review-reviewer-prompt.md").write_text("BAKED code-review rubric content")
    monkeypatch.setattr(cb, "_SKILL_PROMPT_DIR", str(baked_dir))

    clone_dir = tmp_path / "clone"  # no .claude/skills/code-review/RUBRIC.md present
    clone_dir.mkdir()

    result = cb._resolve_skill_prompt(
        str(clone_dir), "code-review-reviewer-prompt.md", "code-review/RUBRIC.md"
    )

    assert result == "BAKED code-review rubric content", (
        "clone-live RUBRIC.md absent must fall back to the baked copy, not fail or return "
        "empty/None"
    )


def test_prefers_clone_live_code_review_rubric_when_present(tmp_path, monkeypatch):
    baked_dir = tmp_path / "baked"
    baked_dir.mkdir()
    (baked_dir / "code-review-reviewer-prompt.md").write_text("BAKED code-review rubric content")
    monkeypatch.setattr(cb, "_SKILL_PROMPT_DIR", str(baked_dir))

    clone_dir = tmp_path / "clone"
    skill_dir = clone_dir / ".claude" / "skills" / "code-review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "RUBRIC.md").write_text("CLONE-LIVE code-review rubric content")

    result = cb._resolve_skill_prompt(
        str(clone_dir), "code-review-reviewer-prompt.md", "code-review/RUBRIC.md"
    )

    assert result == "CLONE-LIVE code-review rubric content"
