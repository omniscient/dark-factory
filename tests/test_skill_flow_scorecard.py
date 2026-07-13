import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import skill_flow_scorecard as sfs  # noqa: E402


def test_render_report_includes_scenario_table_and_tiers():
    rows = [
        {"scenario": "conformance", "tier": 1, "mechanism": "toggle A/B", "rollout": "default-on", "confounds": ""},
        {"scenario": "refine", "tier": 2, "mechanism": "before/after #43", "rollout": "advisory-readiness", "confounds": "different issues/complexity"},
    ]
    md = sfs.render_report(rows, generated_at="2026-07-12T00:00:00+00:00")
    assert "# Skill-Modularization Scorecard" in md
    assert "| conformance | 1 |" in md
    assert "| refine | 2 |" in md
    assert "default-on" in md
    assert "advisory-readiness" in md
    assert "different issues/complexity" in md


def test_render_report_footer_credits_script():
    md = sfs.render_report([], generated_at="2026-07-12T00:00:00+00:00")
    assert "evals/skill_flow_eval.py" in md


def test_render_report_out_of_scope_note_present():
    # The operator's 2026-07-12 scope-decision comment on #48 excludes whole-harness economics
    # (that belongs to #240/Epic #234) — the report should say so rather than silently drift scope.
    md = sfs.render_report([], generated_at="2026-07-12T00:00:00+00:00")
    assert "#240" in md
