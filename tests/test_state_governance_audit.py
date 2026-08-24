"""Tests for scripts/state_governance_audit.py's 5 deterministic checks (#190).

No subprocess, no network — check-function tests use inline event dicts; the
regeneration test (Task 8) diffs freshly-computed output against the committed sample.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from state_governance_audit import check_authority_monotonicity  # noqa: E402


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
