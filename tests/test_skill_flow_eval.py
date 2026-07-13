import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import skill_flow_eval as sfe  # noqa: E402

SIX_SCENARIOS = {"refine", "plan_narrative", "plan_phase_3_5", "continue", "conformance", "code_review"}

# Real cost-report comment body (issue #48 investigation), fetched via:
#   gh api repos/omniscient/dark-factory/issues/73/comments
# Two cumulative Run sections: one 'fix'-intent, one 'continue'-intent — both containing an
# 'implement' node row, used as the literal fixture for mine_cost_report_population tests below.
_REAL_COST_REPORT_BODY = """<!-- dark-factory-cost-report -->
<!-- cumulative: cost=2.7529775500000003 in=173 out=36712 -->
## Dark Factory — Cost Report

**2 run(s) — Total: $2.7529775500000003 (173 in / 36.7K out)**

### Run: 2026-06-03 19:39 UTC (fix, completed)

| Step | Model | In tokens | Out tokens | Cost | Duration |
|------|-------|-----------|------------|------|----------|
| parse-intent |  | 28 | 332 | $0.0166 | 6.6s |
| fetch-issue |  | 0 | 0 | $0 | 953ms |
| setup-branch |  | 0 | 0 | $0 | 32ms |
| implement |  | 37 | 20.3K | $1.4114 | 6m 48s |
| preview-up |  | 0 | 0 | $0 | 23.4s |
| validate |  | 14 | 2.9K | $0.2735 | 6m 30s |
| push-and-pr |  | 0 | 0 | $0 | 3.4s |
| status-in-review |  | 0 | 0 | $0 | 2.5s |
| report |  | 0 | 0 | $0 | 1.4s |
| **Subtotal** | | **79** | **23.5K** | **$1.70157385** | |
### Run: 2026-06-03 19:51 UTC (continue, completed)

| Step | Model | In tokens | Out tokens | Cost | Duration |
|------|-------|-----------|------------|------|----------|
| parse-intent |  | 28 | 274 | $0.0163 | 5.1s |
| fetch-issue |  | 0 | 0 | $0 | 1.6s |
| setup-branch |  | 0 | 0 | $0 | 367ms |
| summarize-feedback |  | 28 | 943 | $0.0332 | 15.2s |
| acknowledge-continue |  | 0 | 0 | $0 | 901ms |
| implement |  | 29 | 9.6K | $0.7088 | 3m 4s |
| preview-up |  | 0 | 0 | $0 | 15.6s |
| validate |  | 9 | 2.4K | $0.2931 | 4m 6s |
| push-and-pr |  | 0 | 0 | $0 | 1s |
| status-in-review |  | 0 | 0 | $0 | 2.3s |
| report |  | 0 | 0 | $0 | 1.6s |
| **Subtotal** | | **94** | **13.2K** | **$1.0514037000000003** | |

---
*Updated by MarketHawk Dark Factory*
"""


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


def test_dimension_applicability_tier2_token_and_runtime_now_disclose_mined_coverage():
    # entrypoint.sh's post_cost_report() posts a durable cost-report comment that DOES let
    # token_count/runtime be mined (before/after boundary) for refine/plan_narrative/continue —
    # this must say so, with an honest partial-coverage caveat (#64), not claim full reliability.
    for scenario in ("refine", "plan_narrative", "continue"):
        for dim in ("token_count", "runtime"):
            val = sfe.DIMENSION_APPLICABILITY[scenario][dim]
            assert val == sfe._TIER2_TOKEN_MINED
            assert "mined" in val
            assert "#64" in val or "partial" in val


def test_dimension_applicability_discloses_tier2_tool_call_gap_rather_than_overclaiming():
    # tool-call/step count genuinely has no durable mined artifact — the cost-report comment's
    # per-node table only has tokens/cost/duration, no step-count column — this must say so
    # explicitly, not claim "measured from mined population" when nothing measures it.
    assert sfe.DIMENSION_APPLICABILITY["refine"]["tool_call_count"] == sfe._TIER2_TOKEN_GAP
    assert sfe.DIMENSION_APPLICABILITY["continue"]["tool_call_count"] == sfe._TIER2_TOKEN_GAP
    assert sfe.DIMENSION_APPLICABILITY["plan_narrative"]["tool_call_count"] == sfe._TIER2_TOKEN_GAP


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


def test_mine_conformance_population_classifies_and_buckets(monkeypatch):
    prs = [
        {"number": 1, "headRefName": "feat/issue-46-x", "mergedAt": "2026-07-10T18:00:00Z", "state": "MERGED", "commits": [{"authors": [{"email": "factory@dark-factory"}]}], "labels": []},
        {"number": 2, "headRefName": "feat/issue-47-y", "mergedAt": "2026-07-10T21:00:00Z", "state": "MERGED", "commits": [{"authors": [{"email": "factory@dark-factory"}]}], "labels": []},
    ]
    comments_by_issue = {
        46: [{"body": "## Spec Conformance — Blocked\n..."}],
        47: [{"body": "no findings"}],
    }

    monkeypatch.setattr(sfe.fsc, "FACTORY_EMAIL", "factory@dark-factory")
    monkeypatch.setattr(sfe, "_fetch_issue_comments", lambda repo, num: comments_by_issue[num])

    boundary = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
    result = sfe.mine_conformance_population("omniscient/dark-factory", prs, boundary)

    assert result["before"]["n"] == 0
    assert result["after"]["n"] == 2
    assert result["after"]["blocked"] == 1


def test_mine_code_review_population_classifies_and_buckets(monkeypatch):
    prs = [{"number": 1, "headRefName": "feat/issue-46-x", "mergedAt": "2026-07-10T18:00:00Z", "state": "MERGED", "commits": [{"authors": [{"email": "factory@dark-factory"}]}], "labels": []}]
    monkeypatch.setattr(sfe.fsc, "FACTORY_EMAIL", "factory@dark-factory")
    monkeypatch.setattr(sfe, "_fetch_issue_comments", lambda repo, num: [{"body": "## Code Review — Blocked\n..."}])

    boundary = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
    result = sfe.mine_code_review_population("omniscient/dark-factory", prs, boundary)
    assert result["after"] == {"n": 1, "blocked": 1}


def test_mine_label_incidence_counts_regression_and_needs_discussion(monkeypatch):
    prs = [
        {"number": 1, "mergedAt": "2026-07-10T05:00:00Z", "state": "MERGED",
         "commits": [{"authors": [{"email": "factory@dark-factory"}]}],
         "labels": [{"name": "factory-regression"}]},
        {"number": 2, "mergedAt": "2026-07-10T09:00:00Z", "state": "MERGED",
         "commits": [{"authors": [{"email": "factory@dark-factory"}]}],
         "labels": [{"name": "needs-discussion"}, {"name": "scope-spillover"}]},
        {"number": 3, "mergedAt": "2026-07-10T09:30:00Z", "state": "MERGED",
         "commits": [{"authors": [{"email": "factory@dark-factory"}]}],
         "labels": []},
    ]
    monkeypatch.setattr(sfe.fsc, "FACTORY_EMAIL", "factory@dark-factory")
    boundary = datetime(2026, 7, 10, 8, 0, 0, tzinfo=timezone.utc)

    result = sfe.mine_label_incidence(prs, boundary)

    assert result["before"] == {"n": 1, "factory_regression": 1, "scope_spillover": 0, "needs_discussion": 0}
    assert result["after"] == {"n": 2, "factory_regression": 0, "scope_spillover": 1, "needs_discussion": 1}


def _factory_pr(number, issue, merged_at):
    return {
        "number": number,
        "headRefName": f"feat/issue-{issue}-x",
        "mergedAt": merged_at,
        "state": "MERGED",
        "commits": [{"authors": [{"email": "factory@dark-factory"}]}],
        "labels": [],
    }


def test_mine_cost_report_population_parses_real_example(monkeypatch):
    prs = [_factory_pr(1, 73, "2026-07-10T18:00:00Z")]
    monkeypatch.setattr(sfe.fsc, "FACTORY_EMAIL", "factory@dark-factory")
    monkeypatch.setattr(sfe, "_fetch_issue_comments", lambda repo, num: [{"body": _REAL_COST_REPORT_BODY}])

    boundary = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
    result = sfe.mine_cost_report_population("omniscient/dark-factory", prs, boundary, "implement")

    assert result["before"] == {
        "n_total": 0, "n_with_data": 0,
        "avg_input_tokens": None, "avg_output_tokens": None,
        "avg_duration_ms": None, "avg_cost_usd": None,
    }
    after = result["after"]
    assert after["n_total"] == 1
    assert after["n_with_data"] == 1
    # Averaged across both Run sections' 'implement' rows: (37, 29) in / (20300, 9600) out /
    # (1.4114, 0.7088) cost / (408000, 184000) ms duration.
    assert after["avg_input_tokens"] == pytest.approx(33.0)
    assert after["avg_output_tokens"] == pytest.approx(14950.0)
    assert after["avg_cost_usd"] == pytest.approx(1.0601)
    assert after["avg_duration_ms"] == pytest.approx(296000.0)


def test_mine_cost_report_population_intent_filter_selects_continue_only(monkeypatch):
    prs = [_factory_pr(1, 73, "2026-07-10T18:00:00Z")]
    monkeypatch.setattr(sfe.fsc, "FACTORY_EMAIL", "factory@dark-factory")
    monkeypatch.setattr(sfe, "_fetch_issue_comments", lambda repo, num: [{"body": _REAL_COST_REPORT_BODY}])

    boundary = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
    result = sfe.mine_cost_report_population(
        "omniscient/dark-factory", prs, boundary, "implement", intent_filter="continue"
    )

    after = result["after"]
    assert after["n_with_data"] == 1
    assert after["avg_input_tokens"] == pytest.approx(29.0)
    assert after["avg_output_tokens"] == pytest.approx(9600.0)
    assert after["avg_cost_usd"] == pytest.approx(0.7088)
    assert after["avg_duration_ms"] == pytest.approx(184000.0)


def test_mine_cost_report_population_skips_pr_with_no_cost_comment(monkeypatch):
    prs = [_factory_pr(1, 73, "2026-07-10T18:00:00Z")]
    monkeypatch.setattr(sfe.fsc, "FACTORY_EMAIL", "factory@dark-factory")
    monkeypatch.setattr(sfe, "_fetch_issue_comments", lambda repo, num: [{"body": "no cost report here"}])

    boundary = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
    result = sfe.mine_cost_report_population("omniscient/dark-factory", prs, boundary, "implement")

    assert result["after"] == {
        "n_total": 1, "n_with_data": 0,
        "avg_input_tokens": None, "avg_output_tokens": None,
        "avg_duration_ms": None, "avg_cost_usd": None,
    }


def test_mine_cost_report_population_skips_pr_with_no_matching_node_row(monkeypatch):
    prs = [_factory_pr(1, 73, "2026-07-10T18:00:00Z")]
    monkeypatch.setattr(sfe.fsc, "FACTORY_EMAIL", "factory@dark-factory")
    monkeypatch.setattr(sfe, "_fetch_issue_comments", lambda repo, num: [{"body": _REAL_COST_REPORT_BODY}])

    boundary = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
    # 'conformance' never appears as a Step row in the fixture body — the PR should be skipped,
    # not crash, and be reflected in n_total vs n_with_data (coverage), not assumed 100%.
    result = sfe.mine_cost_report_population("omniscient/dark-factory", prs, boundary, "conformance")

    assert result["after"]["n_total"] == 1
    assert result["after"]["n_with_data"] == 0


def test_mine_cost_report_population_reports_partial_coverage_across_prs(monkeypatch):
    prs = [_factory_pr(1, 73, "2026-07-10T18:00:00Z"), _factory_pr(2, 74, "2026-07-10T19:00:00Z")]
    comments_by_issue = {
        73: [{"body": _REAL_COST_REPORT_BODY}],
        74: [{"body": "no cost report here"}],
    }
    monkeypatch.setattr(sfe.fsc, "FACTORY_EMAIL", "factory@dark-factory")
    monkeypatch.setattr(sfe, "_fetch_issue_comments", lambda repo, num: comments_by_issue[num])

    boundary = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
    result = sfe.mine_cost_report_population("omniscient/dark-factory", prs, boundary, "implement")

    after = result["after"]
    assert after["n_total"] == 2
    assert after["n_with_data"] == 1  # only issue #73 has a parseable cost-report comment


def test_mine_cross_repo_population_widens_conformance(monkeypatch):
    dfx_prs = [{"number": 1, "headRefName": "feat/issue-46-x", "mergedAt": "2026-07-10T18:00:00Z", "state": "MERGED", "commits": [{"authors": [{"email": "factory@dark-factory"}]}], "labels": []}]
    mh_prs = [{"number": 5, "headRefName": "feat/issue-9-y", "mergedAt": "2026-07-10T19:00:00Z", "state": "MERGED", "commits": [{"authors": [{"email": "factory@markethawk"}]}], "labels": []}]

    def fake_fetch_prs():
        return mh_prs if sfe.fsc.REPO == "omniscient/markethawk" else dfx_prs

    # Self-target arm: is_factory_pr reads fsc.FACTORY_EMAIL at call time, and
    # mine_cross_repo_verdict_population's self_target mining runs with whatever email is
    # ambient *before* the cross-repo reassignment — set it explicitly per the hermetic-test
    # convention tests/test_fetch_scorecard.py's own header documents (never depend on ambient env).
    # tests/conftest.py scrubs every FACTORY_* env var before any test imports, so fsc.REPO
    # otherwise defaults to fetch_scorecard's own "omniscient/markethawk" default (not
    # dark-factory) — set both explicitly so the "restored after" assertion below is meaningful.
    monkeypatch.setattr(sfe.fsc, "FACTORY_EMAIL", "factory@dark-factory")
    monkeypatch.setattr(sfe.fsc, "REPO", "omniscient/dark-factory")
    monkeypatch.setattr(sfe.fsc, "_OWNER_REPO", "omniscient/dark-factory")
    monkeypatch.setattr(sfe.fsc, "fetch_prs", fake_fetch_prs)
    monkeypatch.setattr(sfe, "_fetch_issue_comments", lambda repo, num: [{"body": "no findings"}])

    boundary = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
    result = sfe.mine_cross_repo_verdict_population(
        "omniscient/dark-factory", dfx_prs, "omniscient/markethawk", boundary
    )

    assert result["self_target"]["conformance"]["after"]["n"] == 1
    assert result["cross_repo"]["conformance"]["after"]["n"] == 1
    assert sfe.fsc.REPO == "omniscient/dark-factory"  # restored after the cross-repo fetch


def test_mine_cross_repo_population_degrades_gracefully_on_failure(monkeypatch):
    dfx_prs = []

    def raising_fetch_prs():
        if sfe.fsc.REPO == "omniscient/markethawk":
            raise RuntimeError("gh: no credentials for omniscient/markethawk")
        return dfx_prs

    monkeypatch.setattr(sfe.fsc, "REPO", "omniscient/dark-factory")
    monkeypatch.setattr(sfe.fsc, "_OWNER_REPO", "omniscient/dark-factory")
    monkeypatch.setattr(sfe.fsc, "fetch_prs", raising_fetch_prs)
    boundary = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)

    result = sfe.mine_cross_repo_verdict_population(
        "omniscient/dark-factory", dfx_prs, "omniscient/markethawk", boundary
    )

    assert result["cross_repo"] == "unavailable"
    assert sfe.fsc.REPO == "omniscient/dark-factory"  # restored even on failure


import json


def test_spotcheck_manifest_has_3_to_5_pairs_all_post_boundary():
    manifest = json.loads((Path(__file__).resolve().parents[1] / "evals" / "skill_flow_spotchecks.json").read_text())
    pairs = manifest["pairs"]
    assert 3 <= len(pairs) <= 5
    for pair in pairs:
        assert {"issue", "pr", "merge_sha", "title"} <= pair.keys()


def test_build_arg_parser_defaults():
    parser = sfe.build_arg_parser()
    args = parser.parse_args([])
    assert args.repo == "omniscient/dark-factory"
    assert args.cross_repo == "omniscient/markethawk"
    assert args.output_dir == "evals"


def test_build_arg_parser_overrides():
    parser = sfe.build_arg_parser()
    args = parser.parse_args(["--repo", "omniscient/other", "--output-dir", "/tmp/out"])
    assert args.repo == "omniscient/other"
    assert args.output_dir == "/tmp/out"


def test_run_reassigns_fetch_scorecard_repo_globals_before_fetching(monkeypatch):
    # Regression guard: run() must reassign fsc.REPO/_OWNER_REPO/FACTORY_EMAIL before
    # fetch_prs()/fetch_issues() (which read those globals, not a repo argument) — otherwise
    # --repo silently fetches from fetch_scorecard's own default instead.
    seen_repo_at_fetch = {}

    def fake_fetch_prs():
        seen_repo_at_fetch["repo"] = sfe.fsc.REPO
        seen_repo_at_fetch["owner_repo"] = sfe.fsc._OWNER_REPO
        return []

    monkeypatch.setattr(sfe.fsc, "fetch_prs", fake_fetch_prs)
    monkeypatch.setattr(sfe.fsc, "_git", lambda repo_root, *a: "2026-07-10T11:57:54-04:00\n")

    parser = sfe.build_arg_parser()
    args = parser.parse_args(["--repo", "omniscient/dark-factory", "--no-cross-repo"])
    sfe.run(args)

    assert seen_repo_at_fetch["repo"] == "omniscient/dark-factory"
    assert seen_repo_at_fetch["owner_repo"] == "omniscient/dark-factory"
    assert sfe.fsc.FACTORY_EMAIL == "factory@dark-factory"


def test_run_windows_prs_by_created_at(monkeypatch):
    in_window_prs = [{"number": 1, "createdAt": "2026-07-05T00:00:00Z", "headRefName": "feat/issue-46-x",
                       "mergedAt": "2026-07-05T00:00:00Z", "state": "MERGED",
                       "commits": [{"authors": [{"email": "factory@dark-factory"}]}], "labels": []}]
    out_of_window_prs = [{"number": 2, "createdAt": "2026-06-01T00:00:00Z", "headRefName": "feat/issue-47-y",
                           "mergedAt": "2026-06-01T00:00:00Z", "state": "MERGED",
                           "commits": [{"authors": [{"email": "factory@dark-factory"}]}], "labels": []}]

    monkeypatch.setattr(sfe.fsc, "FACTORY_EMAIL", "factory@dark-factory")
    monkeypatch.setattr(sfe.fsc, "fetch_prs", lambda: in_window_prs + out_of_window_prs)
    monkeypatch.setattr(sfe.fsc, "_git", lambda repo_root, *a: "2026-07-01T00:00:00+00:00\n")
    monkeypatch.setattr(sfe, "_fetch_issue_comments", lambda repo, num: [{"body": "no findings"}])

    parser = sfe.build_arg_parser()
    args = parser.parse_args(["--since", "2026-07-01", "--until", "2026-07-10", "--no-cross-repo"])
    result = sfe.run(args)

    assert result["conformance"]["before"]["n"] + result["conformance"]["after"]["n"] == 1


def test_run_includes_all_six_scenarios(monkeypatch):
    prs = [{"number": 1, "headRefName": "feat/issue-46-x", "createdAt": "2026-07-10T05:00:00Z",
            "mergedAt": "2026-07-10T05:00:00Z", "state": "MERGED",
            "commits": [{"authors": [{"email": "factory@dark-factory"}]}], "labels": []}]
    monkeypatch.setattr(sfe.fsc, "FACTORY_EMAIL", "factory@dark-factory")
    monkeypatch.setattr(sfe.fsc, "fetch_prs", lambda: prs)
    monkeypatch.setattr(sfe.fsc, "_git", lambda repo_root, *a: "2026-07-01T00:00:00+00:00\n")
    monkeypatch.setattr(sfe, "_fetch_issue_comments", lambda repo, num: [{"body": "no findings"}])

    parser = sfe.build_arg_parser()
    args = parser.parse_args(["--since", "2026-07-01", "--no-cross-repo"])
    result = sfe.run(args)

    for scenario in ("refine", "plan_narrative", "continue", "conformance", "code_review"):
        assert scenario in result
    # plan_phase_3_5 shares conformance's population per spec §6 — no separate top-level key.
    assert "plan_phase_3_5" not in result


def test_run_tier2_scenarios_include_both_label_and_quantitative_mining(monkeypatch):
    # spec Requirement #3: refine/plan_narrative/continue must carry a mined quantitative
    # (token/runtime) signal alongside the pre-existing qualitative label-incidence signal —
    # report[scenario] must not silently drop the label-incidence shape when this is added.
    prs = [{"number": 1, "headRefName": "feat/issue-46-x", "createdAt": "2026-07-10T05:00:00Z",
            "mergedAt": "2026-07-10T05:00:00Z", "state": "MERGED",
            "commits": [{"authors": [{"email": "factory@dark-factory"}]}], "labels": []}]
    monkeypatch.setattr(sfe.fsc, "FACTORY_EMAIL", "factory@dark-factory")
    monkeypatch.setattr(sfe.fsc, "fetch_prs", lambda: prs)
    monkeypatch.setattr(sfe.fsc, "_git", lambda repo_root, *a: "2026-07-01T00:00:00+00:00\n")
    monkeypatch.setattr(sfe, "_fetch_issue_comments", lambda repo, num: [{"body": "no findings"}])

    parser = sfe.build_arg_parser()
    args = parser.parse_args(["--since", "2026-07-01", "--no-cross-repo"])
    result = sfe.run(args)

    for scenario in ("refine", "plan_narrative", "continue"):
        assert set(result[scenario].keys()) == {"labels", "quantitative"}
        assert "before" in result[scenario]["labels"] and "after" in result[scenario]["labels"]
        assert "before" in result[scenario]["quantitative"] and "after" in result[scenario]["quantitative"]
        assert result[scenario]["quantitative"]["after"]["n_total"] == 1
        assert result[scenario]["quantitative"]["after"]["n_with_data"] == 0  # "no findings" has no cost marker


def test_run_writes_results_json(monkeypatch, tmp_path):
    monkeypatch.setattr(sfe.fsc, "fetch_prs", lambda: [])
    monkeypatch.setattr(sfe.fsc, "_git", lambda repo_root, *a: "2026-07-01T00:00:00+00:00\n")

    parser = sfe.build_arg_parser()
    args = parser.parse_args(["--output-dir", str(tmp_path), "--no-cross-repo"])
    sfe.run(args, write_json=True)

    written = list((tmp_path / "results").glob("skill-flow-population-*.json"))
    assert len(written) == 1
