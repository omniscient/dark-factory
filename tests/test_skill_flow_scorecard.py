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


def test_tier1_scenario_can_reach_default_on():
    rollout = sfs.recommend_rollout(tier=1, before_blocked_rate=0.30, after_blocked_rate=0.05)
    assert rollout == "default-on"


def test_tier2_scenario_capped_at_advisory_even_with_strong_signal():
    # Even a dramatic improvement cannot license default-on for a Tier 2 scenario — spec
    # Requirement #6: only Tier 1 (conformance, code-review, plan's Phase 3.5) can reach it.
    rollout = sfs.recommend_rollout(tier=2, before_blocked_rate=0.90, after_blocked_rate=0.01)
    assert rollout == "advisory-readiness"


def test_tier1_no_go_when_after_worse_than_before():
    rollout = sfs.recommend_rollout(tier=1, before_blocked_rate=0.05, after_blocked_rate=0.30)
    assert rollout == "no-go"


def test_tier1_advisory_only_when_flat():
    rollout = sfs.recommend_rollout(tier=1, before_blocked_rate=0.10, after_blocked_rate=0.09)
    assert rollout == "advisory-only"


def test_recommend_rollout_handles_zero_denominator_population():
    # An empty before/after bucket (n=0) must not raise ZeroDivisionError upstream — the caller
    # passes rate=0.0 for an empty bucket; recommend_rollout itself just consumes floats and must
    # not special-case NaN/inf.
    rollout = sfs.recommend_rollout(tier=1, before_blocked_rate=0.0, after_blocked_rate=0.0)
    assert rollout == "advisory-only"


def test_blocked_rate_from_population_handles_zero_n():
    assert sfs.blocked_rate({"n": 0, "blocked": 0}) == 0.0
    assert sfs.blocked_rate({"n": 4, "blocked": 1}) == 0.25
