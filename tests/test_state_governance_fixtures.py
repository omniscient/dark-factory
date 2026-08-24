"""Schema and corpus-invariant tests for the state-governance fixture corpus (#190).

Modeled on tests/test_behavioral_state_fixtures.py (#242). Guards the synthetic
state-lineage.jsonl corpus scripts/state_governance_audit.py's 5 checks are validated
against: every event must carry the full envelope from
docs/archive/2026-08-21-state-governance-scorecard-design.md, and the corpus
must stay within its 11-file hard cap (10 pass/fail + 1 combined).
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from state_governance_audit import ACTIONABILITIES, CHECK_NAMES, OPERATIONS, STATE_TYPES  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EVAL_DIR = _REPO_ROOT / "evals" / "state-governance"
_FIXTURES_DIR = _EVAL_DIR / "fixtures"
_SAMPLE_DIR = _EVAL_DIR / "sample"
_MANIFEST_FILE = _FIXTURES_DIR / "manifest.json"

REQUIRED_EVENT_KEYS = {
    "event_id", "idempotency_key", "operation", "state_type", "entity_id",
    "authority", "scope", "provenance", "mutability", "recoverability", "actionability",
}

COMBINED_FIXTURE = "realistic-run-01.jsonl"


def _fixture_paths():
    if not _FIXTURES_DIR.is_dir():
        return []
    return sorted(_FIXTURES_DIR.glob("*.jsonl"))


def _load_jsonl(path):
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


class TestFixtureCorpus:
    def test_fixtures_dir_exists(self):
        assert _FIXTURES_DIR.is_dir(), f"{_FIXTURES_DIR} does not exist"

    def test_corpus_has_exactly_11_files(self):
        paths = _fixture_paths()
        assert len(paths) == 11, (
            f"Expected the hard-capped 11 fixture files (10 pass/fail + 1 combined), got {len(paths)}"
        )

    @pytest.mark.parametrize("path", _fixture_paths(), ids=lambda p: p.stem)
    def test_fixture_events_conform_to_envelope_schema(self, path):
        events = _load_jsonl(path)
        assert events, f"{path.name} has no events"
        for event in events:
            missing = REQUIRED_EVENT_KEYS - set(event.keys())
            assert not missing, f"{path.name}/{event.get('event_id')} missing keys: {missing}"
            assert event["state_type"] in STATE_TYPES, (
                f"{path.name}/{event['event_id']} has unknown state_type {event['state_type']!r}"
            )
            assert event["operation"] in OPERATIONS, (
                f"{path.name}/{event['event_id']} has unknown operation {event['operation']!r}"
            )
            assert event["actionability"] in ACTIONABILITIES, (
                f"{path.name}/{event['event_id']} has unknown actionability {event['actionability']!r}"
            )


class TestManifest:
    def test_manifest_file_exists(self):
        assert _MANIFEST_FILE.exists()

    def test_manifest_covers_every_fixture_file(self):
        manifest = json.loads(_MANIFEST_FILE.read_text(encoding="utf-8"))
        fixture_names = {p.name for p in _fixture_paths()}
        assert set(manifest.keys()) == fixture_names

    def test_every_check_has_a_pass_and_fail_fixture(self):
        manifest = json.loads(_MANIFEST_FILE.read_text(encoding="utf-8"))
        by_check = {}
        for fname, spec in manifest.items():
            if spec["check"] == "combined":
                continue
            by_check.setdefault(spec["check"], set()).add(spec["expected_verdict"])
        missing = set(CHECK_NAMES) - set(by_check.keys())
        assert not missing, f"No fixtures at all for checks: {missing}"
        for check in CHECK_NAMES:
            assert by_check[check] == {"PASS", "FAIL"}, (
                f"{check} must have both a PASS and a FAIL fixture, got {by_check[check]}"
            )

    def test_combined_fixture_is_declared(self):
        manifest = json.loads(_MANIFEST_FILE.read_text(encoding="utf-8"))
        assert COMBINED_FIXTURE in manifest
        assert manifest[COMBINED_FIXTURE]["check"] == "combined"


class TestSampleArtifacts:
    def test_sample_json_exists(self):
        assert (_SAMPLE_DIR / "state-governance-scorecard.json").exists()

    def test_sample_md_exists(self):
        assert (_SAMPLE_DIR / "state-governance-scorecard.md").exists()

    def test_sample_json_has_the_minimum_output_contract(self):
        data = json.loads((_SAMPLE_DIR / "state-governance-scorecard.json").read_text(encoding="utf-8"))
        assert data["STATUS"] in ("PASS", "WARN", "FAIL")
        assert isinstance(data["score"], int) and 0 <= data["score"] <= 100
        assert [c["name"] for c in data["checks"]] == CHECK_NAMES
        for c in data["checks"]:
            assert c["verdict"] in ("PASS", "WARN", "FAIL")
            assert isinstance(c["violations"], list)
