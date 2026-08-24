"""Tests for scripts/state_governance_audit.py's 5 deterministic checks (#190).

No subprocess, no network — check-function tests use inline event dicts; the
regeneration test (Task 8) diffs freshly-computed output against the committed sample.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from state_governance_audit import check_authority_monotonicity  # noqa: E402
from state_governance_audit import check_scope_non_expansion  # noqa: E402
from state_governance_audit import check_deletion_propagation  # noqa: E402
from state_governance_audit import check_provenance_preservation  # noqa: E402
from state_governance_audit import check_rollback_traceability  # noqa: E402
from state_governance_audit import (  # noqa: E402
    CHECK_FUNCS,
    CHECK_NAMES,
    compute_scorecard,
    load_events,
)
from state_governance_audit import write_json, write_markdown  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURES_DIR = _REPO_ROOT / "evals" / "state-governance" / "fixtures"
_MANIFEST = _FIXTURES_DIR / "manifest.json"
_SAMPLE_DIR = _REPO_ROOT / "evals" / "state-governance" / "sample"


def _event(**overrides):
    """Build a minimal, schema-complete event envelope with sane defaults."""
    base = {
        "event_id": "evt-1",
        "idempotency_key": "issue-190:evt-1",
        "operation": "write",
        "state_type": "memory",
        "entity_id": "memory:x",
        "authority": {"actor": "refine", "permission_epoch": 1, "approval_record": None},
        "scope": {"repo": "omniscient/dark-factory", "issue": 190, "pr": None, "agent_role": "refine"},
        "provenance": {"source": "memory_write.py", "trust_tier": "reviewed", "run_id": "run-1", "commit": None},
        "mutability": {"status": "active", "supersedes": [], "conflicts_with": []},
        "recoverability": {"transaction_id": None, "rollback_handle": None, "external_effects": []},
        "actionability": "advisory",
    }
    base.update(overrides)
    return base


class TestAuthorityMonotonicity:
    def test_equal_epoch_passes(self):
        e1 = _event(event_id="a1", authority={"actor": "x", "permission_epoch": 2, "approval_record": None})
        e2 = _event(event_id="a2", authority={"actor": "x", "permission_epoch": 2, "approval_record": "a1"})
        verdict, violations = check_authority_monotonicity([e1, e2])
        assert verdict == "PASS"
        assert violations == []

    def test_inflated_epoch_fails(self):
        e1 = _event(event_id="a1", authority={"actor": "x", "permission_epoch": 2, "approval_record": None})
        e2 = _event(event_id="a2", authority={"actor": "x", "permission_epoch": 5, "approval_record": "a1"})
        verdict, violations = check_authority_monotonicity([e1, e2])
        assert verdict == "FAIL"
        assert len(violations) == 1
        assert violations[0]["event_id"] == "a2"

    def test_unresolved_approval_record_is_not_a_violation(self):
        e1 = _event(event_id="a1", authority={"actor": "x", "permission_epoch": 5, "approval_record": "no-such-event"})
        verdict, violations = check_authority_monotonicity([e1])
        assert verdict == "PASS"


class TestScopeNonExpansion:
    def test_widen_with_equal_epoch_passes(self):
        e1 = _event(event_id="s1", entity_id="memory:y",
                     authority={"actor": "x", "permission_epoch": 3, "approval_record": None},
                     scope={"repo": "omniscient/dark-factory", "issue": 190, "pr": None, "agent_role": "refine"})
        e2 = _event(event_id="s2", entity_id="memory:y",
                     authority={"actor": "x", "permission_epoch": 3, "approval_record": "s1"},
                     scope={"repo": "omniscient/dark-factory", "issue": None, "pr": None, "agent_role": "refine"})
        verdict, violations = check_scope_non_expansion([e1, e2])
        assert verdict == "PASS"

    def test_widen_with_lower_epoch_fails(self):
        e1 = _event(event_id="s1", entity_id="memory:y",
                     authority={"actor": "x", "permission_epoch": 2, "approval_record": None},
                     scope={"repo": "omniscient/dark-factory", "issue": 190, "pr": None, "agent_role": "refine"})
        e2 = _event(event_id="s2", entity_id="memory:y",
                     authority={"actor": "x", "permission_epoch": 1, "approval_record": None},
                     scope={"repo": "omniscient/markethawk", "issue": None, "pr": None, "agent_role": "refine"})
        verdict, violations = check_scope_non_expansion([e1, e2])
        assert verdict == "FAIL"
        assert violations[0]["event_id"] == "s2"

    def test_narrowing_never_violates(self):
        e1 = _event(event_id="s1", entity_id="memory:y",
                     authority={"actor": "x", "permission_epoch": 1, "approval_record": None},
                     scope={"repo": "omniscient/dark-factory", "issue": None, "pr": None, "agent_role": "refine"})
        e2 = _event(event_id="s2", entity_id="memory:y",
                     authority={"actor": "x", "permission_epoch": 1, "approval_record": None},
                     scope={"repo": "omniscient/dark-factory", "issue": 190, "pr": None, "agent_role": "refine"})
        verdict, violations = check_scope_non_expansion([e1, e2])
        assert verdict == "PASS"


class TestDeletionPropagation:
    def test_act_after_tombstone_reflecting_status_passes(self):
        e1 = _event(event_id="d1", entity_id="memory:z", operation="tombstone",
                     mutability={"status": "tombstoned", "supersedes": [], "conflicts_with": []})
        e2 = _event(event_id="d2", entity_id="memory:z", operation="act",
                     mutability={"status": "tombstoned", "supersedes": [], "conflicts_with": []})
        verdict, violations = check_deletion_propagation([e1, e2])
        assert verdict == "PASS"

    def test_act_after_tombstone_ignoring_status_fails(self):
        e1 = _event(event_id="d1", entity_id="memory:z", operation="tombstone",
                     mutability={"status": "tombstoned", "supersedes": [], "conflicts_with": []})
        e2 = _event(event_id="d2", entity_id="memory:z", operation="act",
                     mutability={"status": "active", "supersedes": [], "conflicts_with": []})
        verdict, violations = check_deletion_propagation([e1, e2])
        assert verdict == "FAIL"
        assert violations[0]["event_id"] == "d2"

    def test_act_before_any_tombstone_is_fine(self):
        e1 = _event(event_id="d1", entity_id="memory:z", operation="act",
                     mutability={"status": "active", "supersedes": [], "conflicts_with": []})
        verdict, violations = check_deletion_propagation([e1])
        assert verdict == "PASS"


class TestProvenancePreservation:
    def test_evidence_actionability_is_exempt(self):
        e1 = _event(event_id="p1", actionability="evidence",
                     provenance={"source": None, "trust_tier": None, "run_id": None, "commit": None})
        verdict, violations = check_provenance_preservation([e1])
        assert verdict == "PASS"

    def test_advisory_missing_source_fails(self):
        e1 = _event(event_id="p1", actionability="advisory",
                     provenance={"source": None, "trust_tier": None, "run_id": None, "commit": None})
        verdict, violations = check_provenance_preservation([e1])
        assert verdict == "FAIL"

    def test_policy_missing_trust_tier_fails(self):
        e1 = _event(event_id="p1", actionability="policy",
                     provenance={"source": "git", "trust_tier": "untrusted", "run_id": None, "commit": "abc"})
        verdict, violations = check_provenance_preservation([e1])
        assert verdict == "FAIL"

    def test_policy_with_reviewed_trust_tier_passes(self):
        e1 = _event(event_id="p1", actionability="policy",
                     provenance={"source": "git", "trust_tier": "reviewed", "run_id": None, "commit": "abc"})
        verdict, violations = check_provenance_preservation([e1])
        assert verdict == "PASS"


class TestRollbackTraceability:
    def test_advisory_actionability_is_exempt(self):
        e1 = _event(event_id="r1", actionability="advisory",
                     recoverability={"transaction_id": None, "rollback_handle": None, "external_effects": []})
        verdict, violations = check_rollback_traceability([e1])
        assert verdict == "PASS"

    def test_external_commitment_without_handle_fails(self):
        e1 = _event(event_id="r1", actionability="external_commitment",
                     recoverability={"transaction_id": None, "rollback_handle": None, "external_effects": []})
        verdict, violations = check_rollback_traceability([e1])
        assert verdict == "FAIL"

    def test_external_commitment_with_transaction_id_passes(self):
        e1 = _event(event_id="r1", actionability="external_commitment",
                     recoverability={"transaction_id": "txn-1", "rollback_handle": None, "external_effects": []})
        verdict, violations = check_rollback_traceability([e1])
        assert verdict == "PASS"


class TestComputeScorecard:
    def test_all_pass_scores_100(self):
        e1 = _event(event_id="e1", actionability="evidence")
        scorecard = compute_scorecard([e1], now="2026-01-01T00:00:00Z", run_id="t")
        assert scorecard["STATUS"] == "PASS"
        assert scorecard["score"] == 100
        assert [c["name"] for c in scorecard["checks"]] == CHECK_NAMES

    def test_one_failing_check_drops_score_and_status(self):
        e1 = _event(event_id="e1", actionability="external_commitment",
                     recoverability={"transaction_id": None, "rollback_handle": None, "external_effects": []})
        scorecard = compute_scorecard([e1], now="2026-01-01T00:00:00Z", run_id="t")
        assert scorecard["STATUS"] == "FAIL"
        assert scorecard["score"] == 80


class TestManifestFixtures:
    def test_every_manifest_entry_matches_its_check_function(self):
        manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
        for fname, spec in manifest.items():
            if spec["check"] == "combined":
                continue
            events = load_events(_FIXTURES_DIR / fname)
            verdict, _ = CHECK_FUNCS[spec["check"]](events)
            assert verdict == spec["expected_verdict"], (
                f"{fname}: expected {spec['expected_verdict']}, got {verdict}"
            )


class TestSampleRegeneration:
    def test_sample_matches_fresh_regeneration(self):
        events = load_events(_FIXTURES_DIR / "realistic-run-01.jsonl")
        scorecard = compute_scorecard(events, now="2026-08-22T00:00:00Z", run_id="state-governance-sample-v1")

        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            write_json(scorecard, tmp_path / "state-governance-scorecard.json")
            write_markdown(scorecard, tmp_path / "state-governance-scorecard.md")

            fresh_json = (tmp_path / "state-governance-scorecard.json").read_text(encoding="utf-8")
            fresh_md = (tmp_path / "state-governance-scorecard.md").read_text(encoding="utf-8")

        committed_json = (_SAMPLE_DIR / "state-governance-scorecard.json").read_text(encoding="utf-8")
        committed_md = (_SAMPLE_DIR / "state-governance-scorecard.md").read_text(encoding="utf-8")

        assert fresh_json == committed_json
        assert fresh_md == committed_md
