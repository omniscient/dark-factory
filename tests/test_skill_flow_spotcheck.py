import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import skill_flow_spotcheck as sfsc  # noqa: E402


def test_render_conformance_prompt_substitutes_real_rubric_placeholders():
    rubric = "Kind: $ARTIFACT_KIND\nSpec:\n$SPEC_CONTENT\nArtifact:\n$ARTIFACT_CONTENT"
    prompt = sfsc.render_conformance_prompt(
        rubric, artifact_kind="IMPLEMENTATION", spec_content="the spec", artifact_content="the diff"
    )
    assert prompt == "Kind: IMPLEMENTATION\nSpec:\nthe spec\nArtifact:\nthe diff"


def test_render_code_review_prompt_substitutes_real_rubric_placeholders():
    rubric = "Issue:\n$ISSUE_CONTEXT\nDiff:\n$DIFF_CONTENT"
    prompt = sfsc.render_code_review_prompt(rubric, issue_context="issue body", diff_content="the diff")
    assert prompt == "Issue:\nissue body\nDiff:\nthe diff"


def test_extract_conformance_verdict_parses_material():
    text = "## Spec Conformance — Implementation\n\n**Verdict:** ⛔ Material divergence\n**Spec:** foo\n"
    assert sfsc.extract_conformance_verdict(text) == "MATERIAL_BLOCKED"


def test_extract_conformance_verdict_parses_conforms():
    text = "## Spec Conformance — Implementation\n\n**Verdict:** ✅ Conforms\n**Spec:** foo\n"
    assert sfsc.extract_conformance_verdict(text) == "CONFORMS_OR_MINOR"


def test_extract_conformance_verdict_missing_verdict_line_is_unparseable():
    # A response that doesn't follow the RUBRIC's required output format must not be silently
    # counted as a pass — it's a distinct outcome from a real CONFORMS verdict.
    assert sfsc.extract_conformance_verdict("no verdict line here") == "UNPARSEABLE"


def test_extract_code_review_verdict_blocked_on_high_severity():
    text = "| 1 | high | security | x.py:1 | sql injection |\n| 2 | low | naming | y.py:2 | rename |"
    assert sfsc.extract_code_review_verdict(text) == "BLOCKED"


def test_extract_code_review_verdict_pass_when_no_high_or_critical():
    text = "| 1 | low | naming | y.py:2 | rename |\n| 2 | medium | edge-case | z.py:3 | guard missing |"
    assert sfsc.extract_code_review_verdict(text) == "PASS"


def test_extract_code_review_verdict_pass_on_no_findings():
    assert sfsc.extract_code_review_verdict("No findings.") == "PASS"


import json
import subprocess


def test_run_one_arm_parses_usage_from_output_json(monkeypatch):
    fake_response = json.dumps({
        "type": "result", "is_error": False, "result": "**Verdict:** ✅ Conforms\n",
        "duration_ms": 4200, "total_cost_usd": 0.0123,
        "usage": {"input_tokens": 5000, "output_tokens": 300},
    })

    def fake_run(cmd, input=None, capture_output=True, text=True, timeout=None):
        assert cmd[:2] == ["claude", "-p"]
        assert "--output-format" in cmd and "json" in cmd
        assert input is not None  # prompt goes via stdin, never interpolated into cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=fake_response, stderr="")

    monkeypatch.setattr(sfsc.subprocess, "run", fake_run)

    result = sfsc.run_one_arm(prompt="the prompt", gate="conformance", model="claude-opus-4-8")

    assert result["verdict"] == "CONFORMS_OR_MINOR"
    assert result["input_tokens"] == 5000
    assert result["output_tokens"] == 300
    assert result["duration_ms"] == 4200
    assert result["cost_usd"] == 0.0123
    assert result["error"] is None


def test_run_one_arm_records_error_without_raising(monkeypatch):
    def fake_run(cmd, input=None, capture_output=True, text=True, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({
            "type": "result", "is_error": True, "result": "Not logged in", "duration_ms": 100,
            "total_cost_usd": 0, "usage": {"input_tokens": 0, "output_tokens": 0},
        }), stderr="")

    monkeypatch.setattr(sfsc.subprocess, "run", fake_run)
    result = sfsc.run_one_arm(prompt="p", gate="conformance", model="claude-opus-4-8")
    assert result["error"] == "Not logged in"
    assert result["verdict"] == "UNPARSEABLE"


def test_run_one_arm_handles_non_json_stdout_without_raising(monkeypatch):
    def fake_run(cmd, input=None, capture_output=True, text=True, timeout=None):
        return subprocess.CompletedProcess(cmd, 1, stdout="not json", stderr="boom")

    monkeypatch.setattr(sfsc.subprocess, "run", fake_run)
    result = sfsc.run_one_arm(prompt="p", gate="conformance", model="claude-opus-4-8")
    assert result["error"] is not None
    assert result["verdict"] == "UNPARSEABLE"


def test_budget_tracker_stops_after_cap():
    tracker = sfsc.BudgetTracker(cap_usd=1.00)
    assert tracker.check(0.40) is True   # 0.40 <= 1.00, ok
    assert tracker.check(0.55) is True   # 0.95 <= 1.00, ok
    assert tracker.check(0.10) is False  # 1.05 > 1.00, over cap


def test_build_spotcheck_arg_parser_defaults():
    parser = sfsc.build_arg_parser()
    args = parser.parse_args([])
    assert args.manifest == "evals/skill_flow_spotchecks.json"
    assert args.budget_usd == 5.00
    assert args.dry_run is False


def test_dry_run_prints_plan_without_calling_subprocess(monkeypatch, capsys):
    called = {"n": 0}

    def fake_run(*a, **k):
        called["n"] += 1
        raise AssertionError("must not be called in --dry-run")

    monkeypatch.setattr(sfsc.subprocess, "run", fake_run)
    manifest = {"pairs": [{"issue": 46, "pr": 229, "merge_sha": "abc123", "title": "t"}]}
    sfsc.run_spotcheck(manifest, dry_run=True, budget_usd=5.00, model="claude-opus-4-8", repo_root=".")
    out = capsys.readouterr().out
    assert called["n"] == 0
    assert "#46" in out
