"""Hosting shadow comparator and repository-level checks (#563).

Spec: docs/superpowers/specs/2026-11-27-shadow-hosting-economics-comparator-design.md
(Tests 20-34). Rows are built in-test (no network); one end-to-end test drives the real
launcher with a rules_only-only arms file, which makes no request at all.
"""
import ipaddress
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
BENCH = REPO / "bench"
sys.path.insert(0, str(BENCH))

import compare_hosting_variants as chv  # noqa: E402
import hosting_shadow_schema as schema  # noqa: E402
import shadow_runner  # noqa: E402
from factory_core.providers import model_endpoint  # noqa: E402

EXAMPLES = BENCH / "fixtures" / "preview_classifier" / "examples"
EXAMPLE_FIXTURES = EXAMPLES / "fixtures.yaml"
EXAMPLE_LABELS = EXAMPLES / "oracle_labels.yaml"
EXAMPLE_PRE = BENCH / "preview_classifier_preregistration.example.yaml"
NEW_MODULES = ("hosting_shadow_schema.py", "shadow_runner.py", "_shadow_worker.py",
               "compare_hosting_variants.py")
LOCAL_IDENTITY = {"endpoint_id": "stub-endpoint",
                  "model_identity": {"served_name": "stub-model",
                                     "digest_or_revision": "stub-digest",
                                     "quantisation": "stub-quant"},
                  "runtime_stack": {"name": "stub-runtime", "version": "0.0.0",
                                    "chat_template": "stub-template",
                                    "context_settings": {"context_tokens": 8192}},
                  "hardware": "stub-hardware"}


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch):
    for name in (*schema.REPO_TOKENS, *model_endpoint.CLOUD_CREDENTIALS):
        monkeypatch.delenv(name, raising=False)


def fx(fid, provenance="synthetic", split="dev"):
    entry = {"fixture_id": fid, "provenance": provenance, "split": split,
             "name_only": ["backend/app/x.py"], "stat": "", "title": "t", "labels": [],
             "labels_source": "synthetic"}
    if provenance == "historical":
        entry.update(labels_source="dropped", source={"pr": 1, "merge_sha": "abc", "issue": 2})
    return entry


def label(oracle, fail_safe=False):
    return {"oracle_label": oracle, "expected_fail_safe": fail_safe, "oracle_rationale": "r",
            "labeled_by": "test", "labeled_at": "2026-11-27T00:00:00+00:00"}


class Run:
    """A results directory, fixtures file and labels file, built row by row."""

    def __init__(self, tmp_path, fixtures, labels, scope="same-host"):
        self.tmp = tmp_path
        self.fixtures = tmp_path / "fixtures.yaml"
        self.fixtures.write_text(yaml.safe_dump({"schema_version": "1.0",
                                                 "preview_enabled": True,
                                                 "fixtures": fixtures}))
        self.labels = tmp_path / "labels.yaml"
        self.labels.write_text(yaml.safe_dump({"schema_version": "1.0",
                                               "oracle_question": schema.ORACLE_QUESTION,
                                               "labels": labels}))
        self.digest = schema.sha256_file(self.fixtures)
        self.rows = []
        self.scope = scope
        self.results = tmp_path / "results"

    def row(self, fid, arm_kind="local", rep=0, **fields):
        arm_id = "rules" if arm_kind == "rules_only" else "local-a"
        row = dict.fromkeys(schema.ROW_KEYS)
        row.update({"schema_version": "1.0", "runner_version": schema.RUNNER_VERSION,
                    "fixture_id": fid, "arm_id": arm_id, "arm_kind": arm_kind,
                    "repetition": rep, "run_order_seed": 1,
                    "run_order_position": len(self.rows), "concurrency": 1,
                    "rubric_sha": "a" * 64, "fixture_set_digest": self.digest,
                    "prompt_substitutions_version": "1", "preview_enabled": True,
                    "unsupported_measurements": list(schema.UNSUPPORTED_MEASUREMENTS),
                    "energy_provenance": "unknown", "parse_status": "parsed",
                    "predicted_needs_preview": True,
                    "started_at": "2026-11-27T12:00:00+00:00",
                    "completed_at": "2026-11-27T12:00:01+00:00"})
        if arm_kind == "local":
            row.update({"request_params": dict(schema.REQUEST_PARAMS),
                        "hosting_location": "local", "role": "actor",
                        "identity_source": "declared", "served_name_verified": True,
                        "http_status": 200, "cold_or_warm": "unknown",
                        "gen_ai.usage.input_tokens": 100, "gen_ai.usage.output_tokens": 10,
                        "duration_ms": 1000, **json.loads(json.dumps(LOCAL_IDENTITY))})
        row.update(fields)
        self.rows.append(row)
        return row

    def both(self, fid, rep=0, rules=True, **local_fields):
        self.row(fid, "rules_only", rep, predicted_needs_preview=rules)
        return self.row(fid, "local", rep, **local_fields)

    def write(self, repetitions=1, manifest_extra=None):
        self.results.mkdir(exist_ok=True)
        manifest = {
            "schema_version": "1.0", "runner_version": schema.RUNNER_VERSION, "seed": 1,
            "repetitions": repetitions, "fixture_set_digest": self.digest,
            "rubric_sha": "a" * 64, "rubric_source_commit": "b" * 40,
            "prompt_substitutions_version": "1", "preview_enabled": True,
            "request_params": dict(schema.REQUEST_PARAMS), "concurrency": 1,
            "arms": [{"arm_id": "rules", "arm_kind": "rules_only"},
                     {"arm_id": "local-a", "arm_kind": "local", "identity_source": "declared",
                      "base_url_scope": self.scope, **LOCAL_IDENTITY}],
            "planned_units": [{"fixture_id": r["fixture_id"], "arm_id": r["arm_id"],
                               "repetition": r["repetition"],
                               "run_order_position": r["run_order_position"]}
                              for r in self.rows],
            **(manifest_extra or {})}
        (self.results / "manifest.json").write_text(json.dumps(manifest))
        (self.results / "rows.jsonl").write_text("".join(json.dumps(r) + "\n"
                                                         for r in self.rows))

    def report(self, out="report.json", pre=None, write=True):
        if write:
            self.write()
        out_path = self.tmp / out
        argv = ["--results-dir", str(self.results), "--fixtures", str(self.fixtures),
                "--labels", str(self.labels), "--out", str(out_path)]
        if pre is not None:
            argv += ["--preregistration", str(pre)]
        chv.main(argv)
        return json.loads(out_path.read_text()) if out_path.suffix == ".json" \
            else out_path.read_text()

    def prereg(self, **overrides):
        pre = {"schema_version": "1.0", "status": "registered", "registered_by": "test",
               "registered_at": "2026-11-27T00:00:00+00:00",
               "fixture_set_digest": self.digest,
               "oracle_label_digest": schema.sha256_file(self.labels),
               "rubric_sha": "a" * 64, "repetitions": 1, "seed": 1,
               "criteria": {"max_false_skips_held_out": 0, "max_false_skip_rate": 0.0,
                            "min_schema_validity_rate": 0.9, "min_coverage": 0.9,
                            "p95_duration_ms_slo": 5000,
                            "min_false_preview_rate_reduction_vs_rules_only": 0.0},
               "invalid_run_handling": schema.INVALID_RUN_HANDLING}
        pre.update(overrides)
        path = self.tmp / "pre.yaml"
        path.write_text(yaml.safe_dump({"preview_classifier_preregistration": pre}))
        return path


def simple_run(tmp_path, **kw):
    run = Run(tmp_path, [fx("f-1"), fx("f-2")], {"f-1": label(True), "f-2": label(False)}, **kw)
    run.both("f-1", rules=True, predicted_needs_preview=True)
    run.both("f-2", rules=True, predicted_needs_preview=False)
    return run


# 20
@pytest.mark.parametrize("key,value", [("fixture_set_digest", "c" * 64), ("rubric_sha", "d" * 64),
                                       ("prompt_substitutions_version", "2"),
                                       ("request_params", {**schema.REQUEST_PARAMS,
                                                           "max_tokens": 1})])
def test_equality_guard_raises(tmp_path, key, value):
    run = simple_run(tmp_path)
    run.rows[-1][key] = value
    with pytest.raises(chv.CompareError):
        run.report()


# 21
def test_missing_or_duplicate_row_raises(tmp_path):
    run = simple_run(tmp_path)
    run.write()
    rows = (run.results / "rows.jsonl").read_text().splitlines()
    (run.results / "rows.jsonl").write_text("\n".join(rows[:-1]) + "\n")
    with pytest.raises(chv.CompareError, match="planned units"):
        run.report(write=False)
    (run.results / "rows.jsonl").write_text("\n".join(rows + rows[-1:]) + "\n")
    with pytest.raises(chv.CompareError, match="duplicate"):
        run.report(write=False)


# 22
def test_outcome_classes_are_exclusive_and_ordered(tmp_path):
    fixtures = [fx(f"f-{i}") for i in range(1, 6)]
    labels = {"f-1": label(True), "f-2": label(True), "f-3": label(True, fail_safe=True),
              "f-4": label(True), "f-5": label(False)}
    run = Run(tmp_path, fixtures, labels)
    run.both("f-1", predicted_needs_preview=True)                      # accepted
    run.both("f-2", predicted_needs_preview=False)                     # substantive_failure
    run.both("f-3", predicted_needs_preview=True)                      # policy_fail_safe
    run.both("f-4", parse_status="schema_invalid", predicted_needs_preview=None)
    run.both("f-5", parse_status="no_response", predicted_needs_preview=None,
             http_status=None, failure_class="endpoint_unreachable")
    classes = {r["fixture_id"]: chv.outcome_class(r, labels[r["fixture_id"]])
               for r in run.rows if r["arm_kind"] == "local"}
    assert classes == {"f-1": "accepted", "f-2": "substantive_failure",
                       "f-3": "policy_fail_safe", "f-4": "schema_invalid",
                       "f-5": "infrastructure_invalid"}
    metrics = run.report()["cells"]["synthetic/dev"]["local-a"]
    assert metrics["class_counts"] == {c: 1 for c in chv.OUTCOME_CLASSES}
    assert sum(metrics["class_counts"].values()) == metrics["assigned"] == 5


# 23
def test_schema_invalid_is_never_a_false_skip_and_stays_in_denominators(tmp_path):
    run = Run(tmp_path, [fx("f-1"), fx("f-2")], {"f-1": label(True), "f-2": label(False)})
    run.both("f-1", parse_status="schema_invalid", predicted_needs_preview=None)
    run.both("f-2", parse_status="schema_invalid", predicted_needs_preview=None)
    m = run.report()["cells"]["synthetic/dev"]["local-a"]
    assert m["false_skips"] == 0 and m["false_skip_rate"] == 0.0
    assert m["false_preview_rate"] == 1.0  # no verdict -> effective true
    assert m["assigned"] == 2 and m["schema_validity_rate"] == 0.0 and m["coverage"] == 0.0


# 24
def test_null_metric_is_dropped_not_zeroed(tmp_path):
    run = Run(tmp_path, [fx("f-1"), fx("f-2")], {"f-1": label(True), "f-2": label(True)})
    run.both("f-1", duration_ms=None, **{"gen_ai.usage.input_tokens": None,
                                         "gen_ai.usage.output_tokens": None})
    run.both("f-2", duration_ms=400)
    report = run.report()
    m = report["cells"]["synthetic/dev"]["local-a"]
    assert m["duration_n"] == 1 and m["rows_without_duration"] == 1
    assert m["duration_ms_p50"] == 400 and m["duration_ms_p95"] == 400
    assert m["total_input_tokens"] == 100 and m["rows_with_null_usage"] == 1
    rules = report["cells"]["synthetic/dev"]["rules"]
    assert rules["total_input_tokens"] is None and rules["duration_ms_p95"] is None
    assert rules["accepted_per_hour"] is None


def test_zero_accepted_is_undefined_not_zero(tmp_path):
    run = Run(tmp_path, [fx("f-1")], {"f-1": label(False)})
    run.both("f-1", rules=False, predicted_needs_preview=True)
    report = run.report()
    m = report["cells"]["synthetic/dev"]["local-a"]
    assert m["class_counts"]["accepted"] == 0
    assert m["tokens_per_accepted"] is None and m["false_skip_rate"] is None
    assert m["failure_spend_tokens"] == 110  # the one failed row's 100 + 10
    text = run.report(out="report.md")
    assert "| tokens per accepted call (lower bound) | undefined | undefined |" in text


# 25
def test_total_input_tokens_includes_cache_fields(tmp_path):
    run = Run(tmp_path, [fx("f-1")], {"f-1": label(True)})
    run.both("f-1", **{"gen_ai.usage.input_tokens": 3,
                       "gen_ai.usage.cache_read_input_tokens": 90,
                       "gen_ai.usage.cache_creation_input_tokens": 7})
    m = run.report()["cells"]["synthetic/dev"]["local-a"]
    assert m["total_input_tokens"] == 100
    assert m["cache_read_ratio"] == 0.9
    assert m["tokens_per_accepted"] == 110
    assert m["failure_spend_tokens"] == 0  # no failed row: a known zero, not undefined


def test_absent_cache_fields_are_not_a_cache_hit(tmp_path):
    run = Run(tmp_path, [fx("f-1"), fx("f-2")], {"f-1": label(True), "f-2": label(True)})
    run.both("f-1", **{"gen_ai.usage.cache_read_input_tokens": 50,
                       "gen_ai.usage.cache_creation_input_tokens": 0})
    run.both("f-2")
    m = run.report()["cells"]["synthetic/dev"]["local-a"]
    assert m["rows_reporting_cache_fields"] == {"gen_ai.usage.cache_read_input_tokens": 1,
                                                "gen_ai.usage.cache_creation_input_tokens": 1}
    assert m["cache_read_ratio"] is None
    assert m["total_input_tokens"] == 250


# 26
def test_cost_is_null_and_rendered_unknown(tmp_path):
    run = simple_run(tmp_path)
    report = run.report()
    for per_arm in report["cells"].values():
        assert {m["cost_usd"] for m in per_arm.values()} == {None}
    text = run.report(out="report.md")
    assert "| cost_usd | unknown | unknown |" in text
    assert not re.search(r"\$\s*\d", text)


# 27
def test_held_out_requires_matching_preregistration(tmp_path):
    run = Run(tmp_path, [fx("f-1", split="held_out")], {"f-1": label(True)})
    run.both("f-1")
    with pytest.raises(chv.CompareError, match="--preregistration"):
        run.report()
    run.report(pre=run.prereg())
    for overrides in ({"fixture_set_digest": "c" * 64}, {"oracle_label_digest": "c" * 64},
                      {"rubric_sha": "c" * 64}, {"repetitions": 2}, {"seed": 2},
                      {"registered_at": "2026-11-27T13:00:00+00:00"}):
        with pytest.raises(chv.CompareError):
            run.report(pre=run.prereg(**overrides))
    with pytest.raises(schema.ShadowSchemaError, match="status"):
        run.report(pre=run.prereg(status="approved"))


def test_criteria_are_per_arm_and_cell(tmp_path):
    fixtures = [fx("h-1", split="held_out"), fx("d-1"), fx("d-2")]
    labels = {"h-1": label(True), "d-1": label(False), "d-2": label(True)}
    run = Run(tmp_path, fixtures, labels)
    run.both("h-1", predicted_needs_preview=False)                        # held-out false skip
    run.both("d-1", rules=True, predicted_needs_preview=False, duration_ms=None,
             worker_error=None)
    run.both("d-2", parse_status="no_response", predicted_needs_preview=None, http_status=None,
             worker_error="timeout", duration_ms=None, **{"gen_ai.usage.input_tokens": None,
                                                          "gen_ai.usage.output_tokens": None})
    report = run.report(pre=run.prereg())
    held = report["criteria"]["synthetic/held_out"]["local-a"]
    dev = report["criteria"]["synthetic/dev"]["local-a"]
    assert held["max_false_skips_held_out"] == "not met"
    assert held["max_false_skip_rate"] == "not met"
    assert held["min_false_preview_rate_reduction_vs_rules_only"] == "not evaluable"
    assert dev["max_false_skips_held_out"] == "not evaluable"
    assert dev["p95_duration_ms_slo"] == "not met"  # a timeout row fails the SLO
    assert dev["min_coverage"] == "not met"           # 1 parsed of 2 assigned
    assert dev["min_false_preview_rate_reduction_vs_rules_only"] == "met"
    assert dev["n_pairs"] == 2
    # a failed row exists but reported no usage: unknown, never 0
    assert report["cells"]["synthetic/dev"]["local-a"]["failure_spend_tokens"] is None
    assert set(report["criteria"]) == {"synthetic/held_out", "synthetic/dev"}
    criteria = yaml.safe_load(run.prereg().read_text())["preview_classifier_preregistration"]
    assert set(held) - {"n_pairs"} == set(criteria["criteria"])


# 28
def test_cells_are_never_pooled(tmp_path):
    fixtures = [fx("h-1", "historical", "dev"), fx("s-1"), fx("s-2", split="held_out")]
    labels = {"h-1": label(True), "s-1": label(True), "s-2": label(True)}
    run = Run(tmp_path, fixtures, labels)
    for fid in ("h-1", "s-1", "s-2"):
        run.both(fid)
    report = run.report(pre=run.prereg())
    assert set(report["cells"]) == {"historical/dev", "synthetic/dev", "synthetic/held_out"}
    assert set(report) == {"header", "example_banner", "rubric_source_commit",
                           "fixture_set_digest", "oracle_label_digest", "rubric_sha",
                           "preregistration_status", "arms", "cost_note", "cells",
                           "diagnostics_non_scoring", "criteria"}
    for per_arm in report["cells"].values():
        assert {m["assigned"] for m in per_arm.values()} == {1}
    text = run.report(out="report.md", pre=run.prereg())
    assert not re.search(r"(?i)\b(overall|pooled|composite|total across)\b", text)


# 29
def test_report_header_is_verbatim(tmp_path):
    run = simple_run(tmp_path, scope="remote")
    text = run.report(out="report.md", pre=run.prereg(status="example"))
    assert chv.HEADER == ("NON-AUTHORITATIVE instrument output. Not a promotion decision, "
                          "GO/NO-GO, production-readiness or savings evidence. v1 has no cloud "
                          "arm: this report contains no cloud/local cost or quality comparison.")
    assert chv.HEADER in text
    assert "EXAMPLE DATA — tooling test only" in text
    assert "declared local; address is remote" in text
    assert f"rules_only (reading of runner_version {schema.RUNNER_VERSION})" in text
    assert "(lower bound; 2 rows with null usage)" in text  # the rules_only column
    assert "(n=2; 0 rows without duration)" in text
    assert "b" * 40 in text
    (tmp_path / "lan").mkdir()
    lan = Run(tmp_path / "lan", [fx("f-1")], {"f-1": label(True)}, scope="lan")
    lan.both("f-1")
    lan_text = lan.report(out="report.md")
    assert "declared local; address is remote" not in lan_text
    assert "EXAMPLE DATA" not in lan_text


# 30
def test_report_out_path_inside_git_tree_raises(tmp_path):
    run = simple_run(tmp_path)
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    with pytest.raises(chv.CompareError, match="git work tree"):
        run.report(out="repo/report.md")
    with pytest.raises(chv.CompareError, match=r"\.md or \.json"):
        run.report(out="report.txt")


def test_end_to_end_rules_only_run_reports(tmp_path):
    arms = tmp_path / "arms.yaml"
    arms.write_text(yaml.safe_dump({"arms": [{"arm_id": "rules", "arm_kind": "rules_only"}]}))
    out_dir = tmp_path / "out"
    shadow_runner.main(["--fixtures", str(EXAMPLE_FIXTURES), "--arms", str(arms),
                        "--out-dir", str(out_dir), "--repetitions", "2", "--seed", "1"])
    report_path = tmp_path / "report.json"
    chv.main(["--results-dir", str(out_dir), "--fixtures", str(EXAMPLE_FIXTURES),
              "--labels", str(EXAMPLE_LABELS), "--out", str(report_path),
              "--preregistration", str(EXAMPLE_PRE)])
    report = json.loads(report_path.read_text())
    rules = report["cells"]["synthetic/dev"]["rules"]
    assert rules["assigned"] == 16
    assert rules["class_counts"]["accepted"] == 14
    assert rules["class_counts"]["policy_fail_safe"] == 2
    assert rules["false_skips"] == 0
    assert report["example_banner"] == chv.EXAMPLE_BANNER
    assert report["criteria"] == {}  # no local arm: nothing to check


# 31
def test_committed_examples_validate_and_digests_match():
    fixtures = schema.load_fixtures(EXAMPLE_FIXTURES)
    ids = {f["fixture_id"] for f in fixtures["fixtures"]}
    assert ids == {f"ex-0{i}" for i in range(1, 9)}
    schema.load_labels(EXAMPLE_LABELS, ids)
    schema.load_arms(BENCH / "hosting_arms.example.yaml")
    pre = schema.load_preregistration(EXAMPLE_PRE)
    assert pre["status"] == "example"
    assert pre["fixture_set_digest"] == schema.sha256_file(EXAMPLE_FIXTURES)
    assert pre["oracle_label_digest"] == schema.sha256_file(EXAMPLE_LABELS)
    rubric = schema.load_rubric()
    assert pre["rubric_sha"] == rubric["rubric_sha"]
    for f in fixtures["fixtures"]:
        schema.render_prompt(rubric["prompt"], f)


NEW_FILES = [*(BENCH / m for m in NEW_MODULES), BENCH / "hosting_arms.example.yaml", EXAMPLE_PRE,
             BENCH / "hosting_shadow.md",
             *sorted((BENCH / "fixtures" / "preview_classifier").rglob("*.*")),
             REPO / "tests" / "test_bench_shadow_runner.py",
             REPO / "tests" / "test_bench_hosting_compare.py"]


# 32
def test_committed_tree_has_only_placeholder_endpoint_identity():
    for path in NEW_FILES:
        for literal in re.findall(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])",
                                  path.read_text()):
            ip = ipaddress.ip_address(literal)
            assert ip == ipaddress.ip_address("127.0.0.1") or ip in ipaddress.ip_network(
                "192.0.2.0/24"), (path, literal)
    _, arm = schema.load_arms(BENCH / "hosting_arms.example.yaml")
    assert arm["endpoint_id"] == "example-local"
    assert arm["model_identity"] == {"served_name": "example-model",
                                     "digest_or_revision": "example-digest",
                                     "quantisation": "example-quant"}
    assert arm["hardware"] == "example-hardware"


# 33
def test_nothing_in_production_references_the_shadow_runner():
    names = [m.removesuffix(".py") for m in NEW_MODULES]
    targets = [REPO / "scheduler.sh", REPO / "entrypoint.sh"]
    for root in ("workflows", "commands", "scripts"):
        targets += [p for p in (REPO / root).rglob("*")
                    if p.is_file() and "__pycache__" not in p.parts]
    for path in targets:
        text = path.read_text(errors="ignore")
        for name in names:
            assert name not in text, (path, name)


# 34
def test_yaml_files_reject_unknown_keys(tmp_path):
    def write(name, text):
        path = tmp_path / name
        path.write_text(text)
        return path

    labels_text = EXAMPLE_LABELS.read_text()
    arms_text = (BENCH / "hosting_arms.example.yaml").read_text()
    ids = {f"ex-0{i}" for i in range(1, 9)}
    formats = {
        "fixtures": (EXAMPLE_FIXTURES.read_text(), schema.load_fixtures,
                     "preview_enabled: true", "preview_enabled: true\nextra: 1"),
        "labels": (labels_text, lambda p: schema.load_labels(p, ids),
                   "    labeled_by: example-not-an-oracle\n",
                   "    labeled_by: example-not-an-oracle\n    extra: 1\n"),
        "arms": (arms_text, schema.load_arms, "hardware: example-hardware",
                 "hardware: example-hardware\n    extra: 1"),
        "preregistration": (EXAMPLE_PRE.read_text(), schema.load_preregistration,
                            "  seed: 1", "  seed: 1\n  extra: 1"),
    }
    for kind, (text, load, old, new) in formats.items():
        load(write(f"{kind}-ok.yaml", text))
        with pytest.raises(schema.ShadowSchemaError, match="unknown key"):
            load(write(f"{kind}-extra.yaml", text.replace(old, new, 1)))
        with pytest.raises(schema.ShadowSchemaError):
            load(write(f"{kind}-tag.yaml", "!!python/object:os.system {}\n" + text))
    with pytest.raises(schema.ShadowSchemaError, match="no fixture"):
        schema.load_labels(write("unknown.yaml", labels_text.replace("  ex-08:", "  ex-99:")),
                           ids)
    with pytest.raises(schema.ShadowSchemaError, match="duplicate"):
        schema.load_labels(write("dup.yaml", labels_text + "  ex-01:\n    oracle_label: true\n"),
                           ids)
    secret_url = "http://user:hunter2@192.0.2.10:8080"
    bad = write("bad-url.yaml", arms_text.replace("http://192.0.2.10:8080", secret_url))
    with pytest.raises(schema.ShadowSchemaError) as e:
        schema.load_arms(bad)
    assert secret_url not in str(e.value) and "hunter2" not in str(e.value)
