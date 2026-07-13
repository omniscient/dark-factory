"""Runtime verification (issue #49) that the conformance rubric's clone-live/baked-fallback
resolution — built by #44 in scripts/context_budget.py's _resolve_skill_prompt — actually
falls back to the baked /opt/refinement-skills/conformance-reviewer-prompt.md copy when the
clone-live .claude/skills/conformance/RUBRIC.md file is absent, rather than failing, returning
None, or silently degrading to a no-op. This exercises the behavior the three existing static
tests (test_conformance_command_rubric_fallback.py, test_plan_command_conformance_rubric_fallback.py)
only assert as path-string ordering, never at runtime. No production code changes — this
verifies #44's existing resolution logic.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import context_budget as cb


def test_falls_back_to_baked_conformance_prompt_when_clone_live_rubric_absent(tmp_path, monkeypatch):
    baked_dir = tmp_path / "baked"
    baked_dir.mkdir()
    (baked_dir / "conformance-reviewer-prompt.md").write_text("BAKED conformance rubric content")
    monkeypatch.setattr(cb, "_SKILL_PROMPT_DIR", str(baked_dir))

    clone_dir = tmp_path / "clone"  # no .claude/skills/conformance/RUBRIC.md present
    clone_dir.mkdir()

    result = cb._resolve_skill_prompt(
        str(clone_dir), "conformance-reviewer-prompt.md", "conformance/RUBRIC.md"
    )

    assert result == "BAKED conformance rubric content", (
        "clone-live RUBRIC.md absent must fall back to the baked copy, not fail or return "
        "empty/None"
    )


def test_prefers_clone_live_conformance_rubric_when_present(tmp_path, monkeypatch):
    baked_dir = tmp_path / "baked"
    baked_dir.mkdir()
    (baked_dir / "conformance-reviewer-prompt.md").write_text("BAKED conformance rubric content")
    monkeypatch.setattr(cb, "_SKILL_PROMPT_DIR", str(baked_dir))

    clone_dir = tmp_path / "clone"
    skill_dir = clone_dir / ".claude" / "skills" / "conformance"
    skill_dir.mkdir(parents=True)
    (skill_dir / "RUBRIC.md").write_text("CLONE-LIVE conformance rubric content")

    result = cb._resolve_skill_prompt(
        str(clone_dir), "conformance-reviewer-prompt.md", "conformance/RUBRIC.md"
    )

    assert result == "CLONE-LIVE conformance rubric content"
