"""implement Phase 4 DOCUMENT (#535): derives doc impact from the diff's content, not only
the #55 path table, and always hands Gate 2.5 a doc-impact.md with an OVERALL: line."""
from pathlib import Path

_CMD = Path(__file__).resolve().parents[1] / "commands" / "dark-factory-implement.md"


def _phase4():
    text = _CMD.read_text(encoding="utf-8")
    start = text.index("## Phase 4: DOCUMENT")
    return text[start:text.index("## Phase 5: MEMORY UPDATE")]


def test_no_silent_skip_when_no_mapping_row_matches():
    p4 = _phase4()
    assert "skip this phase entirely" not in p4
    assert "If a path matches no pattern, skip it." not in p4
    assert "is **not** evidence of no impact" in p4


def test_mapping_table_from_55_is_preserved():
    p4 = _phase4()
    for row in ("| `backend/app/models/*.py` | `ARCHITECTURE.md` | Database Models table |",
                "| `.env.example` | `ENV_VARIABLES.md` | Relevant section |",
                "| `frontend/src/pages/*.tsx` | `ARCHITECTURE.md` | Pages table |"):
        assert row in p4
    assert "`docs/database-schema.md` is auto-generated — never edit it." in p4
    assert 'git commit -m "docs: update architecture map for <feature-slug>"' in p4


def test_behavior_scan_covers_policy_markdown_and_reference_docs():
    p4 = _phase4()
    assert "read the diff content, not just the paths" in p4
    assert "`commands/**`" in p4
    for doc in ("docs/run-lifecycle.md", "docs/scheduler.md", "docs/features.md",
                "docs/configuration.md", "README.md"):
        assert doc in p4, doc


def test_doc_impact_artifact_contract():
    p4 = _phase4()
    assert "$ARTIFACTS_DIR/doc-impact.md` — always" in p4
    assert "OVERALL: UPDATED" in p4 and "OVERALL: NO_IMPACT: <reason" in p4
    assert "-> NO_IMPACT:" in p4 and "-> UPDATED" in p4
    assert "do not\n   commit it" in p4


def test_continue_reads_gate_2_5_feedback():
    p4 = _phase4()
    assert "df-docs-conformance-blocked" in p4 and "df-docs-conformance-observed" in p4


def test_report_points_at_doc_impact():
    text = _CMD.read_text(encoding="utf-8")
    report = text[text.index("## Phase 6: REPORT"):]
    assert "`OVERALL:` line from `$ARTIFACTS_DIR/doc-impact.md`" in report
    assert "5 bullets" in report and "5 standard bullets" in report
