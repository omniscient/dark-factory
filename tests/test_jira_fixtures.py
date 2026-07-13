import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jira"


def test_all_fixture_files_are_valid_json_with_expected_top_level_keys():
    expected = {
        "search_result.json": "issues",
        "issue.json": "fields",
        "transitions.json": "transitions",
        "comments.json": "comments",
        "epic_children.json": "issues",
    }
    for filename, key in expected.items():
        data = json.loads((FIXTURES / filename).read_text())
        assert key in data, f"{filename} missing top-level '{key}'"


def test_no_live_jira_base_url_or_secret_in_fixtures():
    for path in FIXTURES.glob("*.json"):
        text = path.read_text()
        assert "atlassian.net" not in text
        assert "Bearer " not in text
