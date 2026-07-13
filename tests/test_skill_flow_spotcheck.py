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
