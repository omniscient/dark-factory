"""Regression test for issue #49 — the rollout-status runbook for Dark Factory Claude Skills
modularization. The runbook is the spec itself (docs/superpowers/specs/2026-07-13-roll-out-...),
kept living and never archived, per the #42/PR #215 pattern in
`.archon/memory/codebase-patterns.md`. Pins its durable claims to exact substrings so a future
edit that silently drops the advisory-only stance, the Tier 2 ineligibility, or the rollback
steps fails CI instead of passing silently — and pins the README link that protects the doc
from CLAUDE.md's archive-excision rule ("never archive a doc that tests or README still
reference").
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = REPO_ROOT / "docs" / "superpowers" / "specs" / "2026-07-13-roll-out-dark-factory-claude-skills-design.md"
README = REPO_ROOT / "README.md"


def _spec_text():
    assert SPEC.exists(), f"{SPEC} does not exist — issue #49's rollout runbook must live here"
    return SPEC.read_text(encoding="utf-8")


def test_runbook_exists_at_durable_specs_path():
    assert SPEC.exists()
    assert SPEC.is_relative_to(REPO_ROOT / "docs" / "superpowers" / "specs")


def test_status_line_marks_doc_as_living_not_archived():
    text = _spec_text()
    assert "living reference — not archived on completion" in text


def test_rollout_status_table_pins_tier1_advisory_only_and_tier2_ineligible():
    text = _spec_text()
    assert "**Advisory-only.**" in text
    assert "**Advisory-only**, same terms." in text
    assert "**Not rollout-eligible.**" in text


def test_standing_policy_states_no_default_on_or_blocking():
    text = _spec_text()
    assert (
        "**Standing policy:** no scenario goes default-on or blocking on native Skill-tool "
        "invocation." in text
    )


def test_rollback_steps_and_forward_obligation_sections_present():
    text = _spec_text()
    assert "### 3. Rollback steps" in text
    assert "### 4. Forward obligation for #218" in text
    assert "Baked-copy staleness hazard" in text


def test_readme_links_the_rollout_runbook_spec():
    text = README.read_text(encoding="utf-8")
    assert (
        "docs/superpowers/specs/2026-07-13-roll-out-dark-factory-claude-skills-design.md" in text
    ), "README.md's ## Further reading list must link the #49 rollout-status runbook spec"
