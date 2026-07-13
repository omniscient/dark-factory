import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import skill_flow_eval as sfe  # noqa: E402

SIX_SCENARIOS = {"refine", "plan_narrative", "plan_phase_3_5", "continue", "conformance", "code_review"}


def test_scenario_map_covers_all_six_named_scenarios():
    keys = {row["scenario"] for row in sfe.SCENARIO_MAP}
    assert SIX_SCENARIOS <= keys


def test_scenario_map_tier_assignment_matches_spec():
    by_scenario = {row["scenario"]: row for row in sfe.SCENARIO_MAP}
    assert by_scenario["conformance"]["tier"] == 1
    assert by_scenario["code_review"]["tier"] == 1
    assert by_scenario["plan_phase_3_5"]["tier"] == 1
    assert by_scenario["refine"]["tier"] == 2
    assert by_scenario["plan_narrative"]["tier"] == 2
    assert by_scenario["continue"]["tier"] == 2


def test_scenario_map_boundary_issue_and_pr_number_are_distinct():
    # boundary_issue is the omniscient/dark-factory issue number (#43/#44/#45); boundary_pr_number
    # is the actual merge-PR number (#220/#225/#231) — a field holding the issue number where a
    # caller expects a PR number is a real defect class (found and fixed once already on this
    # ticket's superseded draft), so this is asserted explicitly.
    by_scenario = {row["scenario"]: row for row in sfe.SCENARIO_MAP}
    assert by_scenario["conformance"]["boundary_issue"] == 44
    assert by_scenario["conformance"]["boundary_pr_number"] == 225
    assert by_scenario["refine"]["boundary_issue"] == 43
    assert by_scenario["refine"]["boundary_pr_number"] == 220
    assert by_scenario["continue"]["boundary_issue"] == 45
    assert by_scenario["continue"]["boundary_pr_number"] == 231


def test_implement_new_intent_excluded_not_evaluated():
    keys = {row["scenario"] for row in sfe.SCENARIO_MAP}
    assert "implement_new" not in keys


def test_dimension_applicability_marks_triggering_na_for_deterministic_scenarios():
    assert sfe.DIMENSION_APPLICABILITY["conformance"]["skill_over_under_triggering"] == "N/A — deterministic resolution"
    assert sfe.DIMENSION_APPLICABILITY["code_review"]["skill_over_under_triggering"] == "N/A — deterministic resolution"
    assert sfe.DIMENSION_APPLICABILITY["refine"]["skill_over_under_triggering"] == "N/A — no model-mediated skill routing"


def test_dimension_applicability_discloses_tier2_token_gap_rather_than_overclaiming():
    # Tier 2 token/tool-call/runtime cannot actually be mined (no durable per-run cost artifact) —
    # this must say so explicitly, not claim "measured from mined population" when nothing measures it.
    assert sfe.DIMENSION_APPLICABILITY["refine"]["token_count"] == sfe._TIER2_TOKEN_GAP
    assert sfe.DIMENSION_APPLICABILITY["continue"]["token_count"] == sfe._TIER2_TOKEN_GAP


def test_dimension_applicability_tier1_token_dims_disclose_spot_check_only_not_mined():
    # Regression guard: mined PR history cannot supply token/tool-call/runtime for Tier 1 either —
    # only the live spot-check pairs measure it directly. Do not claim "+ mined population" here.
    for dim in ("token_count", "tool_call_count", "runtime"):
        val = sfe.DIMENSION_APPLICABILITY["conformance"][dim]
        assert "spot-check" in val or "toggle-pair" in val
        assert "mined" not in val


def test_dimension_applicability_spec_plan_quality_is_scenario_specific_not_uniform():
    # continue produces no spec/plan (it's implement's continue-intent path) — N/A, not "measured".
    assert sfe.DIMENSION_APPLICABILITY["continue"]["spec_plan_quality"].startswith("N/A")
    assert sfe.DIMENSION_APPLICABILITY["refine"]["spec_plan_quality"].startswith("measured")
    assert sfe.DIMENSION_APPLICABILITY["plan_narrative"]["spec_plan_quality"].startswith("measured")


def test_dimension_applicability_implementation_correctness_scenario_specific():
    assert sfe.DIMENSION_APPLICABILITY["continue"]["implementation_correctness"].startswith("measured")
    assert sfe.DIMENSION_APPLICABILITY["refine"]["implementation_correctness"].startswith("N/A")
    assert sfe.DIMENSION_APPLICABILITY["plan_narrative"]["implementation_correctness"].startswith("N/A")


def test_classify_conformance_verdict_blocked():
    comments = [
        {"body": "some other comment"},
        {"body": "## Spec Conformance — Blocked\n\nmaterial divergence..."},
    ]
    assert sfe.classify_conformance_verdict(comments) == "MATERIAL_BLOCKED"


def test_classify_conformance_verdict_plan_variant_blocked():
    comments = [{"body": "## Spec Conformance — Blocked (Plan)\n\n..."}]
    assert sfe.classify_conformance_verdict(comments) == "MATERIAL_BLOCKED"


def test_classify_conformance_verdict_pass_when_silent():
    comments = [{"body": "🧠 Refinement Pipeline — Starting"}]
    assert sfe.classify_conformance_verdict(comments) == "CONFORMS_OR_MINOR"


def test_classify_code_review_verdict_blocked():
    comments = [{"body": "## Code Review — Blocked\n\nThe AI code reviewer found 2 blocking issue(s)"}]
    assert sfe.classify_code_review_verdict(comments) == "BLOCKED"


def test_classify_code_review_verdict_pass_when_silent():
    comments = [{"body": "unrelated"}]
    assert sfe.classify_code_review_verdict(comments) == "PASS"


def test_classify_verdict_ignores_body_none():
    # gh's REST comment objects always have a body string, but be defensive against a None value
    # rather than crashing the mining pass on one malformed comment.
    comments = [{"body": None}, {"body": "## Spec Conformance — Blocked\n..."}]
    assert sfe.classify_conformance_verdict(comments) == "MATERIAL_BLOCKED"


from datetime import datetime, timezone


def test_bucket_prs_by_boundary_splits_before_after():
    boundary = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
    prs = [
        {"number": 1, "mergedAt": "2026-07-10T10:00:00Z"},
        {"number": 2, "mergedAt": "2026-07-10T14:00:00Z"},
        {"number": 3, "mergedAt": None},
    ]
    buckets = sfe.bucket_prs_by_boundary(prs, boundary)
    assert [p["number"] for p in buckets["before"]] == [1]
    assert [p["number"] for p in buckets["after"]] == [2]
    assert [p["number"] for p in buckets["unmerged"]] == [3]


def test_merge_boundary_date_reads_commit_date(monkeypatch, tmp_path):
    def fake_git(repo_root, *args):
        assert args[:2] == ("log", "-1")
        return "2026-07-10T11:57:54-04:00\n"

    monkeypatch.setattr(sfe.fsc, "_git", fake_git)
    dt = sfe.merge_boundary_date(str(tmp_path), "f72738f8beb3e079335bc4daf9b1da85a198b2ef")
    assert dt == datetime.fromisoformat("2026-07-10T11:57:54-04:00")
