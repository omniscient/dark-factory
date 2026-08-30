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


def test_clone_dir_fixture_file_overrides_real_tracker():
    """The CLONE_DIR-relative JSON fixture seam Task 17 depends on — proven here in
    isolation, in-process, before Task 17 relies on it surviving a subprocess."""
    import cost_report_marker_check as m
    with patch.object(m, "get_tracker") as mock_gt:
        mock_gt.return_value.get_comments.return_value = [{"body": "should not be used"}]
        import tempfile
        with tempfile.TemporaryDirectory() as clone_dir:
            fixture = Path(clone_dir) / ".cost_report_marker_check_test_fixture.json"
            fixture.write_text(json.dumps({"comments": [{"body": "<!-- dark-factory-cost-report -->"}]}))
            with patch.dict(os.environ, {"CLONE_DIR": clone_dir}):
                assert m.check(42) == 0
                mock_gt.assert_not_called()


def test_cli_reads_issue_num_and_clone_dir_env(tmp_path):
    """Real subprocess invocation — the actual production entry point, exercising
    ISSUE_NUM + the CLONE_DIR fixture seam together, with no network call."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "cost_report_marker_check.py"
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    fixture = clone_dir / ".cost_report_marker_check_test_fixture.json"
    fixture.write_text(json.dumps({"comments": [{"body": "<!-- dark-factory-cost-report -->"}]}))
    env = {"ISSUE_NUM": "99", "CLONE_DIR": str(clone_dir), "PATH": os.environ["PATH"]}
    result = subprocess.run(["python3", str(script)], env=env, capture_output=True, text=True)
    assert result.returncode == 0
