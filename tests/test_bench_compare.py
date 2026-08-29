import copy
import json
import sys
from pathlib import Path

import pytest

_BENCH_DIR = Path(__file__).resolve().parents[1] / "bench"
sys.path.insert(0, str(_BENCH_DIR))
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "bench"

import compare_variants as cv  # noqa: E402


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


def _build_paired_results(tmp_path: Path) -> Path:
    """Three issues x two arms, derived from the two hand-written fixtures by varying
    issue_number/run_id/cost so paired-median has a real distribution to compute over."""
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    off_base = _load_fixture("budget-enforce-off-sample-run-record.json")
    on_base = _load_fixture("budget-enforce-on-sample-run-record.json")
    issues = [224, 332, 289]
    cost_deltas_off = [0.40, 0.10, 0.25]
    cost_deltas_on = [0.55, 0.15, 0.30]
    for idx, (issue, coff, con) in enumerate(zip(issues, cost_deltas_off, cost_deltas_on), start=1):
        off = copy.deepcopy(off_base)
        off["issue_number"] = issue
        off["run_id"] = f"budget-enforce-off-2026082{idx}T000000-issue{issue}-r1"
        off["harness_economics"]["cost_per_task"] = coff
        (results_dir / f"{off['run_id']}-run-record.json").write_text(json.dumps(off))

        on = copy.deepcopy(on_base)
        on["issue_number"] = issue
        on["run_id"] = f"budget-enforce-on-2026082{idx}T000100-issue{issue}-r1"
        on["harness_economics"]["cost_per_task"] = con
        (results_dir / f"{on['run_id']}-run-record.json").write_text(json.dumps(on))

        agg = {
            "tasks": [{
                "issue": issue, "size": "S", "n": 1, "k": 1, "passes": 1, "pass_k": 1.0,
                "runs": [
                    {"run": 1, "passed": True, "run_id": off["run_id"],
                     "variant_id": "budget-enforce-off",
                     "cost_cents": int(coff * 100), "cost_unavailable": False},
                ],
            }],
        }
        (results_dir / f"2026082{idx}T0000-off-run.json").write_text(json.dumps(agg))
        agg2 = copy.deepcopy(agg)
        agg2["tasks"][0]["runs"][0] = {
            "run": 1, "passed": True, "run_id": on["run_id"], "variant_id": "budget-enforce-on",
            "cost_cents": int(con * 100), "cost_unavailable": False,
        }
        (results_dir / f"2026082{idx}T0001-on-run.json").write_text(json.dumps(agg2))
    return results_dir


def _variants_yaml(tmp_path: Path, *, dimension_b: str = "economics") -> Path:
    path = tmp_path / "variants.yaml"
    path.write_text(json.dumps({  # YAML is a JSON superset — valid input for yaml.safe_load
        "variants": [
            {"variant_id": "budget-enforce-on", "dimension": "economics",
             "fixture_set": "bench/suite.json", "env": {}},
            {"variant_id": "budget-enforce-off", "dimension": dimension_b,
             "fixture_set": "bench/suite.json",
             "env": {"TOKEN_OPTIMIZATION_ENFORCE_BUDGETS": "false"}},
        ]
    }))
    return path


def test_paired_median_cost_delta(tmp_path):
    results_dir = _build_paired_results(tmp_path)
    variants = cv.load_variants(_variants_yaml(tmp_path))
    joined = cv.join_variant_results(variants, results_dir)
    delta = cv.paired_median_delta(joined, "cost_per_task")
    # off - on for each paired issue: (0.40-0.55), (0.10-0.15), (0.25-0.30) -> median -0.05
    assert delta == pytest.approx(-0.05)


def test_multiple_runs_per_issue_not_overwritten(tmp_path):
    """bench/run_suite.sh --n 3 (variants.example.yaml's own worked-example usage) produces
    multiple runs per issue per arm. Joining on issue alone would let run 2 silently overwrite
    run 1's entry — join on (issue, run) so both survive as distinct paired data points."""
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    off_base = _load_fixture("budget-enforce-off-sample-run-record.json")
    on_base = _load_fixture("budget-enforce-on-sample-run-record.json")
    off_runs, on_runs = [], []
    for run_idx, (coff, con) in enumerate([(0.40, 0.55), (0.42, 0.50)], start=1):
        off = copy.deepcopy(off_base)
        off["run_id"] = f"budget-enforce-off-ts{run_idx}-issue224-r{run_idx}"
        off["harness_economics"]["cost_per_task"] = coff
        (results_dir / f"{off['run_id']}-run-record.json").write_text(json.dumps(off))
        off_runs.append({"run": run_idx, "passed": True, "run_id": off["run_id"],
                          "variant_id": "budget-enforce-off", "cost_cents": 1, "cost_unavailable": False})

        on = copy.deepcopy(on_base)
        on["run_id"] = f"budget-enforce-on-ts{run_idx}-issue224-r{run_idx}"
        on["harness_economics"]["cost_per_task"] = con
        (results_dir / f"{on['run_id']}-run-record.json").write_text(json.dumps(on))
        on_runs.append({"run": run_idx, "passed": True, "run_id": on["run_id"],
                         "variant_id": "budget-enforce-on", "cost_cents": 1, "cost_unavailable": False})

    (results_dir / "off-run.json").write_text(json.dumps({"tasks": [
        {"issue": 224, "size": "S", "n": 2, "k": 2, "passes": 2, "pass_k": 1.0, "runs": off_runs}
    ]}))
    (results_dir / "on-run.json").write_text(json.dumps({"tasks": [
        {"issue": 224, "size": "S", "n": 2, "k": 2, "passes": 2, "pass_k": 1.0, "runs": on_runs}
    ]}))

    variants = cv.load_variants(_variants_yaml(tmp_path))
    joined = cv.join_variant_results(variants, results_dir)
    assert len(joined["budget-enforce-off"]) == 2, (
        "both runs for issue 224 must survive the join, not just the last one written"
    )
    pairs = cv.paired_values(joined, "cost_per_task")
    assert len(pairs) == 2


def test_cost_unavailable_excluded_not_zeroed(tmp_path):
    results_dir = _build_paired_results(tmp_path)
    # Corrupt one arm's cost to unavailable
    record = next(results_dir.glob("budget-enforce-off-*issue224*-run-record.json"))
    data = json.loads(record.read_text())
    data["harness_economics"]["cost_per_task"] = None
    record.write_text(json.dumps(data))

    variants = cv.load_variants(_variants_yaml(tmp_path))
    joined = cv.join_variant_results(variants, results_dir)
    pairs = cv.paired_values(joined, "cost_per_task")
    # 3 paired issues total, 1 excluded for missing data -> 2 remain
    assert len(pairs) == 2
    delta = cv.paired_median_delta(joined, "cost_per_task")
    assert delta is not None  # still computable from the remaining 2 pairs, never treated as 0


def test_reserved_dimension_raises_named_error(tmp_path):
    results_dir = _build_paired_results(tmp_path)
    variants = cv.load_variants(_variants_yaml(tmp_path, dimension_b="memory_intervention"))
    with pytest.raises(NotImplementedError, match="#241"):
        cv.join_variant_results(variants, results_dir)


def test_contract_trajectory_dimension_names_311(tmp_path):
    results_dir = _build_paired_results(tmp_path)
    variants = cv.load_variants(_variants_yaml(tmp_path, dimension_b="contract_trajectory"))
    with pytest.raises(NotImplementedError, match="#311"):
        cv.join_variant_results(variants, results_dir)


def test_mismatched_non_economics_config_overlay_refuses(tmp_path):
    """A config_overlay top-level key outside token_optimization (e.g. a gate surface) must
    refuse the compare outright, regardless of what the other arm declares."""
    path = tmp_path / "variants.yaml"
    path.write_text(json.dumps({
        "variants": [
            {"variant_id": "a", "dimension": "economics", "fixture_set": "bench/suite.json",
             "config_overlay": {"token_optimization": {"enforce_budgets": True}}},
            {"variant_id": "b", "dimension": "economics", "fixture_set": "bench/suite.json",
             "config_overlay": {"gate_conformance": {"enabled": False}}},
        ]
    }))
    variants = cv.load_variants(path)
    with pytest.raises(ValueError, match="non-economics config"):
        cv.assert_only_economics_keys_differ(variants)


def test_cross_arm_architecture_sub_key_mismatch_refuses(tmp_path):
    """Requirement #4 / Gate criteria: architecture/memory/comments/diff must be held at
    committed defaults in BOTH arms. This only surfaces from a cross-arm comparison — a
    per-arm-only check (the bug the architect review caught) would miss it entirely."""
    path = tmp_path / "variants.yaml"
    path.write_text(json.dumps({
        "variants": [
            {"variant_id": "a", "dimension": "economics", "fixture_set": "bench/suite.json",
             "config_overlay": {"token_optimization": {
                 "enforce_budgets": True, "architecture": {"max_tokens": 3000}}}},
            {"variant_id": "b", "dimension": "economics", "fixture_set": "bench/suite.json",
             "config_overlay": {"token_optimization": {
                 "enforce_budgets": False, "architecture": {"max_tokens": 6000}}}},
        ]
    }))
    variants = cv.load_variants(path)
    with pytest.raises(ValueError, match="architecture"):
        cv.assert_only_economics_keys_differ(variants)


def test_cross_arm_asymmetric_architecture_key_refuses(tmp_path):
    """One arm overrides token_optimization.architecture; the other omits it (implicitly the
    committed default) — this must still refuse, since an intersection-only check would miss
    it (the key isn't in *both* overlays' key sets, only one)."""
    path = tmp_path / "variants.yaml"
    path.write_text(json.dumps({
        "variants": [
            {"variant_id": "a", "dimension": "economics", "fixture_set": "bench/suite.json",
             "config_overlay": {"token_optimization": {
                 "enforce_budgets": True, "architecture": {"max_tokens": 3000}}}},
            {"variant_id": "b", "dimension": "economics", "fixture_set": "bench/suite.json",
             "config_overlay": {"token_optimization": {"enforce_budgets": False}}},
        ]
    }))
    variants = cv.load_variants(path)
    with pytest.raises(ValueError, match="architecture"):
        cv.assert_only_economics_keys_differ(variants)


def test_cross_arm_matching_architecture_sub_key_passes(tmp_path):
    """Same architecture sub-key value in both arms (committed default, held constant) must
    not be flagged — only enforce_budgets (the intended lever) is allowed to differ."""
    path = tmp_path / "variants.yaml"
    path.write_text(json.dumps({
        "variants": [
            {"variant_id": "a", "dimension": "economics", "fixture_set": "bench/suite.json",
             "config_overlay": {"token_optimization": {
                 "enforce_budgets": True, "architecture": {"max_tokens": 3000}}}},
            {"variant_id": "b", "dimension": "economics", "fixture_set": "bench/suite.json",
             "config_overlay": {"token_optimization": {
                 "enforce_budgets": False, "architecture": {"max_tokens": 3000}}}},
        ]
    }))
    variants = cv.load_variants(path)
    cv.assert_only_economics_keys_differ(variants)  # must not raise


def test_fixture_set_mismatch_refuses(tmp_path):
    path = tmp_path / "variants.yaml"
    path.write_text(json.dumps({
        "variants": [
            {"variant_id": "a", "dimension": "economics", "fixture_set": "bench/suite.json"},
            {"variant_id": "b", "dimension": "economics", "fixture_set": "evals/behavioral-state/fixtures"},
        ]
    }))
    variants = cv.load_variants(path)
    with pytest.raises(ValueError, match="fixture_set"):
        cv.assert_only_economics_keys_differ(variants)


def test_non_economics_env_var_mismatch_refuses(tmp_path):
    path = tmp_path / "variants.yaml"
    path.write_text(json.dumps({
        "variants": [
            {"variant_id": "a", "dimension": "economics", "fixture_set": "bench/suite.json",
             "env": {"TOKEN_OPTIMIZATION_ENFORCE_BUDGETS": "true"}},
            {"variant_id": "b", "dimension": "economics", "fixture_set": "bench/suite.json",
             "env": {"TOKEN_OPTIMIZATION_ENFORCE_BUDGETS": "false", "SOME_UNRELATED_VAR": "x"}},
        ]
    }))
    variants = cv.load_variants(path)
    with pytest.raises(ValueError, match="non-economics"):
        cv.assert_only_economics_keys_differ(variants)


def test_join_raises_when_variant_has_no_matching_runs(tmp_path):
    """A variant_id with zero matching aggregate rows (e.g. --variant-id omitted or wrong
    --results-dir) must raise, not silently produce an empty joined population — an empty
    report reads as 'ran and found nothing', which is a materially different, misleading
    signal from 'never ran'."""
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    variants = cv.load_variants(_variants_yaml(tmp_path))
    with pytest.raises(ValueError, match="no matching run"):
        cv.join_variant_results(variants, results_dir)


def test_rollback_tier_zero_for_env_kill_switch():
    variant = {"variant_id": "budget-enforce-off", "env": {"TOKEN_OPTIMIZATION_ENFORCE_BUDGETS": "false"}}
    assert cv.determine_rollback_tier(variant) == "0"


def test_rollback_tier_none_for_image_only_variant():
    variant = {"variant_id": "image-swap", "image": "ghcr.io/omniscient/dark-factory:candidate"}
    assert cv.determine_rollback_tier(variant) == "none"


def test_render_report_includes_all_table_columns(tmp_path):
    results_dir = _build_paired_results(tmp_path)
    variants = cv.load_variants(_variants_yaml(tmp_path))
    joined = cv.join_variant_results(variants, results_dir)
    report = cv.build_report(variants, joined)
    md = cv.render_markdown(report)
    for col in ("variant_id", "outcome delta", "economics delta", "gate verdict",
                "promotion stage", "rollback_tier"):
        assert col in md


def test_build_report_carries_stub_mode_score_and_cpm_not_gate_bearing(tmp_path):
    """Gate-criteria section: outcome.score/factory_cpm are reported ALONGSIDE pass^k but
    flagged stub-mode/not-gate-bearing — must be present in the report and rendered, but must
    never influence gate_verdict (which stays pass^k-only even when both are supplied)."""
    results_dir = _build_paired_results(tmp_path)
    variants = cv.load_variants(_variants_yaml(tmp_path))
    joined = cv.join_variant_results(variants, results_dir)
    report = cv.build_report(variants, joined)
    assert report["outcome_score_after_median"] == pytest.approx(1.0)  # fixtures are produced_ungated
    assert report["factory_cpm_after_median"] == pytest.approx(23.8)  # off-fixture's factory_cpm
    md = cv.render_markdown(report)
    assert "outcome.score" in md
    assert "factory_cpm" in md
    assert "not gate-bearing" in md


def test_variants_example_yaml_loads_and_validates():
    example = _BENCH_DIR / "variants.example.yaml"
    variants = cv.load_variants(example)
    # variants[0] is the baseline ("before"), variants[1] the candidate whose row the report
    # renders — the spec's worked example evaluates enforcement ON as the candidate.
    assert [v["variant_id"] for v in variants] == ["budget-enforce-off", "budget-enforce-on"]
    cv.assert_only_economics_keys_differ(variants)  # must not raise
    assert cv.determine_rollback_tier(variants[0]) == "0"  # env-override arm: tier 0 rollback
