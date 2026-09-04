from pathlib import Path

COMMAND_DIR = Path(__file__).resolve().parents[1] / "commands"


def test_refine_spec_writing_mandates_issue_number_line():
    text = (COMMAND_DIR / "dark-factory-refine.md").read_text(encoding="utf-8")
    assert "**Issue:** #<num>" in text, (
        "Phase 5 (Spec Writing) must mandate a '**Issue:** #<num>' line in the spec "
        "body so content-only detection (budget telemetry, PR-push archive step) "
        "keeps working for artifacts the push gate associates via commit subject (#382)"
    )
    assert "issue number line" in text.lower() or "issue-number line" in text.lower(), (
        "Phase 5's self-review step must explicitly check for the mandated line, "
        "the same way it already checks for placeholders/consistency/scope"
    )


def test_plan_writing_mandates_issue_number_line():
    text = (COMMAND_DIR / "dark-factory-plan.md").read_text(encoding="utf-8")
    assert "**Issue:** #<num>" in text, (
        "Phase 2 (Plan Writing) conventions must mandate a '**Issue:** #<num>' line "
        "in the plan body for the same reason as the refine command (#382)"
    )
    assert "issue number line" in text.lower() or "issue-number line" in text.lower(), (
        "Phase 2's self-review step must explicitly check for the mandated line, "
        "the same way the refine command's self-review already does"
    )
