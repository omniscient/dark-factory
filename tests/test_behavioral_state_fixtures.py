"""Schema and corpus-invariant tests for the behavioral-state-decay fixture corpus (#242).

Guards the ground-truth corpus that epic #241 child 5's state-decay-event-precision
metric will later be scored against: every fixture must conform to the locked schema
in docs/superpowers/specs/2026-07-16-behavioral-state-decay-baseline-design.md and to
the prefix/suffix outcome-isolation discipline (a future replay of `prefix` must never
be able to see `suffix`).
"""

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EVAL_DIR = _REPO_ROOT / "evals" / "behavioral-state"
_FIXTURES_DIR = _EVAL_DIR / "fixtures"
_RUBRIC_FILE = _EVAL_DIR / "rubric.md"
_BASELINE_FILE = _EVAL_DIR / "baseline.md"

CATEGORIES = {
    "requirement-forgotten",
    "environment-fact-ignored",
    "failed-command-repeated",
    "diagnosis-lost",
    "subgoal-abandoned",
    "policy-violated-before-side-effect",
    "phase-handoff-loses-state",
}

REQUIRED_TOP_LEVEL_KEYS = {
    "id", "category", "version", "fidelity", "source_issue", "source_repo",
    "provenance", "pivot_event_index", "prefix", "suffix", "annotation",
}


def _fixture_paths():
    if not _FIXTURES_DIR.is_dir():
        return []
    return sorted(_FIXTURES_DIR.glob("*.json"))


class TestFixtureCorpus:
    def test_fixtures_dir_exists(self):
        assert _FIXTURES_DIR.is_dir(), f"{_FIXTURES_DIR} does not exist"

    def test_at_least_one_fixture_per_category(self):
        paths = _fixture_paths()
        seen = {json.loads(p.read_text(encoding="utf-8"))["category"] for p in paths}
        missing = CATEGORIES - seen
        assert not missing, f"No fixture for categories: {missing}"

    def test_corpus_size_in_target_range(self):
        paths = _fixture_paths()
        assert 10 <= len(paths) <= 14, (
            f"Expected 10-14 fixtures (7-category floor + contrasting cases), got {len(paths)}"
        )

    @pytest.mark.parametrize("path", _fixture_paths(), ids=lambda p: p.stem)
    def test_fixture_schema(self, path):
        data = json.loads(path.read_text(encoding="utf-8"))

        missing = REQUIRED_TOP_LEVEL_KEYS - set(data.keys())
        assert not missing, f"{path.name} missing keys: {missing}"
        assert data["category"] in CATEGORIES, (
            f"{path.name} has unknown category {data['category']!r}"
        )
        assert data["fidelity"] == "reconstructed", (
            f"{path.name} must set fidelity: reconstructed"
        )
        assert data["id"] == path.stem, f"{path.name} id must match filename stem"

        provenance = data["provenance"]
        assert isinstance(provenance, list) and len(provenance) >= 2, (
            f"{path.name} provenance must have >=2 ordered events"
        )
        for event in provenance:
            assert "event" in event and "timestamp" in event, (
                f"{path.name} provenance entries need 'event' and 'timestamp'"
            )

        pivot = data["pivot_event_index"]
        assert isinstance(pivot, int) and 0 <= pivot < len(provenance), (
            f"{path.name} pivot_event_index out of range"
        )

        prefix = data["prefix"]
        assert {"established_state", "established_at_event_index"} <= set(prefix.keys())
        assert prefix["established_at_event_index"] <= pivot, (
            f"{path.name} prefix must be established at or before the pivot"
        )
        assert not ({"outcome", "verifier_signal"} & set(prefix.keys())), (
            f"{path.name} prefix must not leak suffix/outcome fields"
        )

        suffix = data["suffix"]
        assert {"outcome", "verifier_signal"} <= set(suffix.keys())

        annotation = data["annotation"]
        assert annotation.get("confidence") in {"high", "medium"}, (
            f"{path.name} annotation.confidence must be 'high' or 'medium'"
        )
        assert annotation.get("notes"), f"{path.name} annotation.notes must be non-empty"


class TestRubric:
    def test_rubric_file_exists(self):
        assert _RUBRIC_FILE.exists()

    def test_rubric_has_a_section_per_category(self):
        text = _RUBRIC_FILE.read_text(encoding="utf-8")
        for category in CATEGORIES:
            assert category in text, f"rubric.md is missing a section for {category}"


class TestBaseline:
    def test_baseline_file_exists(self):
        assert _BASELINE_FILE.exists()

    def test_baseline_defers_precision_explicitly(self):
        text = _BASELINE_FILE.read_text(encoding="utf-8").lower()
        assert "state-decay event precision" in text
        assert "deferred" in text
