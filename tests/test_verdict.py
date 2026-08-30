import json
import sys, pathlib

# .factory/hooks/{validate,smoke-gate} run `python -m pytest tests/ -q` with no
# PYTHONPATH=scripts — self-insert so this file collects there too, not only in CI.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import pytest
from factory_core import run_record, verdict


def test_parse_basic_status_only():
    result = verdict.parse_verdict("STATUS: PASS\n")
    assert result == {"status": "PASS"}


def test_parse_full_four_line_shape():
    content = "STATUS: BLOCKED\nGATE_TYPE: conformance\nFINDINGS_COUNT: 2\nSEVERITY: critical\n"
    assert verdict.parse_verdict(content) == {
        "status": "BLOCKED", "gate_type": "conformance",
        "findings_count": 2, "severity": "critical",
    }


def test_parse_missing_status_returns_none():
    assert verdict.parse_verdict("no status line here\n") is None


def test_parse_empty_content_returns_none():
    assert verdict.parse_verdict("") is None
    assert verdict.parse_verdict("   \n") is None


def test_parse_never_raises_on_unknown_status():
    # HUMAN_REQUIRED (blast) and FAIL (validation) are documented legacy tokens —
    # returned verbatim, never rejected or normalized (Requirement 1).
    assert verdict.parse_verdict("STATUS: HUMAN_REQUIRED\n") == {"status": "HUMAN_REQUIRED"}
    assert verdict.parse_verdict("STATUS: something-a-target-loop-invented\n") == {
        "status": "something-a-target-loop-invented"
    }


def test_parse_malformed_findings_count_is_skipped_not_raised():
    result = verdict.parse_verdict("STATUS: PASS\nFINDINGS_COUNT: not-a-number\n")
    assert result == {"status": "PASS"}  # findings_count silently absent, no raise


def test_format_verdict_matches_gate_lib_emit_verdict_shape():
    text = verdict.format_verdict("code-review", "PASS", 0, "none")
    assert text == "STATUS: PASS\nGATE_TYPE: code-review\nFINDINGS_COUNT: 0\nSEVERITY: none\n"


def test_format_verdict_clamps_unknown_severity_to_none():
    text = verdict.format_verdict("loop:x", "BLOCKED", 1, "bogus")
    assert text == "STATUS: BLOCKED\nGATE_TYPE: loop:x\nFINDINGS_COUNT: 1\nSEVERITY: none\n"


def test_format_verdict_clamps_negative_findings_count_to_zero():
    text = verdict.format_verdict("loop:x", "PASS", -3, "none")
    assert text == "STATUS: PASS\nGATE_TYPE: loop:x\nFINDINGS_COUNT: 0\nSEVERITY: none\n"


def test_format_then_parse_roundtrips_an_invented_gate_type():
    text = verdict.format_verdict("loop:nightly-scan-triage", "BLOCKED", 1, "high")
    assert verdict.parse_verdict(text) == {
        "status": "BLOCKED", "gate_type": "loop:nightly-scan-triage",
        "findings_count": 1, "severity": "high",
    }


def test_documented_constants():
    assert verdict.GATING_PASS_STATUSES == {"PASS", "SKIPPED", "ERROR"}
    assert verdict.GATING_BLOCK_STATUSES == {"BLOCKED"}
    assert verdict.LEGACY_STATUSES == {"HUMAN_REQUIRED", "FAIL"}
    assert verdict.SEVERITY_LEVELS == ("none", "low", "medium", "high", "critical")


_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures" / "verdicts"

# blast.md is never routed through run_record._parse_artifact_stage (it is not in
# run_record.artifact_names) — its fixtures assert against verdict.parse_verdict
# directly, proving only the shared *schema* stays byte-compatible for that writer.
_SCHEMA_ONLY_PREFIXES = ("blast__",)


def _stage_name(fixture_path: pathlib.Path) -> str:
    return fixture_path.name.split("__", 1)[0]


def test_golden_corpus_byte_compat():
    md_files = sorted(_FIXTURES_DIR.glob("*.md"))
    assert len(md_files) == 17, "golden corpus fixture count changed unexpectedly"
    for md_path in md_files:
        expected_path = md_path.with_suffix("").with_suffix(".expected.json")
        content = md_path.read_text(encoding="utf-8")
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        if md_path.name.startswith(_SCHEMA_ONLY_PREFIXES):
            actual = verdict.parse_verdict(content)
        else:
            actual = run_record._parse_artifact_stage(_stage_name(md_path), content)
        assert actual == expected, f"{md_path.name}: expected {expected}, got {actual}"
