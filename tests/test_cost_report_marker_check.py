import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def test_exits_0_when_marker_present():
    import cost_report_marker_check as m
    with patch.object(m, "get_tracker") as mock_gt:
        mock_gt.return_value.get_comments.return_value = [
            {"body": "some other comment"},
            {"body": "## Cost Report\n<!-- dark-factory-cost-report -->\n..."},
        ]
        assert m.check(42) == 0


def test_exits_1_when_marker_absent():
    import cost_report_marker_check as m
    with patch.object(m, "get_tracker") as mock_gt:
        mock_gt.return_value.get_comments.return_value = [{"body": "unrelated"}]
        assert m.check(42) == 1


def test_exits_1_when_no_comments():
    import cost_report_marker_check as m
    with patch.object(m, "get_tracker") as mock_gt:
        mock_gt.return_value.get_comments.return_value = []
        assert m.check(42) == 1


def test_clone_dir_file_alone_does_not_override_tracker():
    """Regression guard (#198 conformance R5/R12): CLONE_DIR is the agent-writable
    working clone. Merely writing a JSON file at the legacy fixture filename there
    must never be sufficient to fake marker evidence -- only the explicit,
    never-forwarded-by-run_verifier COST_REPORT_MARKER_CHECK_TEST_FIXTURE_PATH env
    var may substitute for the real tracker."""
    import cost_report_marker_check as m
    with patch.object(m, "get_tracker") as mock_gt:
        mock_gt.return_value.get_comments.return_value = [
            {"body": "<!-- dark-factory-cost-report -->"}
        ]
        import tempfile
        with tempfile.TemporaryDirectory() as clone_dir:
            fixture = Path(clone_dir) / ".cost_report_marker_check_test_fixture.json"
            fixture.write_text(json.dumps({"comments": [{"body": "should not be used"}]}))
            with patch.dict(os.environ, {"CLONE_DIR": clone_dir}):
                os.environ.pop("COST_REPORT_MARKER_CHECK_TEST_FIXTURE_PATH", None)
                assert m.check(42) == 0
                mock_gt.assert_called_once()


def test_explicit_fixture_env_overrides_real_tracker():
    """The explicit-env-var JSON fixture seam Task 17 depends on — proven here in
    isolation, in-process, before Task 17 relies on it surviving a subprocess. Unlike
    the legacy CLONE_DIR-filename seam, the fixture's location is unrelated to
    CLONE_DIR — it lives wherever the env var points."""
    import cost_report_marker_check as m
    with patch.object(m, "get_tracker") as mock_gt:
        mock_gt.return_value.get_comments.return_value = [{"body": "should not be used"}]
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "fixture.json"
            fixture.write_text(json.dumps({"comments": [{"body": "<!-- dark-factory-cost-report -->"}]}))
            with patch.dict(os.environ, {"COST_REPORT_MARKER_CHECK_TEST_FIXTURE_PATH": str(fixture)}):
                assert m.check(42) == 0
                mock_gt.assert_not_called()


def test_cli_reads_issue_num_and_fixture_env(tmp_path):
    """Real subprocess invocation — the actual production entry point, exercising
    ISSUE_NUM + the explicit fixture-env seam together, with no network call."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "cost_report_marker_check.py"
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"comments": [{"body": "<!-- dark-factory-cost-report -->"}]}))
    env = {
        "ISSUE_NUM": "99",
        "COST_REPORT_MARKER_CHECK_TEST_FIXTURE_PATH": str(fixture),
        "PATH": os.environ["PATH"],
    }
    result = subprocess.run(["python3", str(script)], env=env, capture_output=True, text=True)
    assert result.returncode == 0
