#!/usr/bin/env python3
"""State governance audit — deterministic scorecard over synthetic state-lineage fixtures (#190).

Reads a directory (or single file) of state-lineage.jsonl event envelopes and computes 5
deterministic governance checks: authority monotonicity, scope non-expansion, deletion
propagation, provenance preservation, and rollback traceability. Advisory only — this
script wires no caller and always exits 0; the caller reads STATUS/score from the output.

Usage:
    python3 scripts/state_governance_audit.py \\
        --fixtures evals/state-governance/fixtures/ \\
        --out-dir  "$ARTIFACTS_DIR" \\
        [--now 2026-08-22T00:00:00Z] \\
        [--run-id sample-run]

(The committed sample under evals/state-governance/sample/ is generated differently —
from the single combined fixture only, with fixed --now/--run-id — see Task 8. Pointing
--fixtures at the whole fixtures/ directory aggregates all 11 files, including the 10
check-isolated pass/fail cases, into one run; that is the real multi-entity-corpus
invocation this docstring documents, not the sample-regeneration invocation.)
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CHECK_NAMES = [
    "authority_monotonicity",
    "scope_non_expansion",
    "deletion_propagation",
    "provenance_preservation",
    "rollback_traceability",
]

# Reserved state_type enum — harness_economics (#234) and memory_intervention (#241) are
# reserved identifiers owned by other epics; no fixtures/checks exercise them here.
STATE_TYPES = {
    "memory", "issue", "project_status", "branch", "pr", "skill", "permission",
    "artifact", "external_commitment", "mechanism_lineage", "harness_economics",
    "memory_intervention",
}

OPERATIONS = {
    "write", "update", "delete", "tombstone", "share", "unshare", "validate",
    "quarantine", "deny", "rollback", "act",
}

ACTIONABILITIES = {
    "evidence", "advisory", "policy", "permission", "skill", "external_commitment",
}

_TOMBSTONE_OPS = {"delete", "tombstone", "quarantine"}
_ACTIVE_STATUSES = {"active"}
_HIGH_ACTIONABILITY = {"policy", "permission", "skill", "external_commitment"}


def load_events(path):
    """Read one or more state-lineage.jsonl files (dir glob or single file), preserving file order."""
    p = Path(path)
    if p.is_dir():
        paths = sorted(p.glob("*.jsonl"))
    else:
        paths = [p]
    events = []
    for fp in paths:
        for line in fp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


def _group_by_entity(events):
    groups = {}
    for e in events:
        groups.setdefault(e.get("entity_id"), []).append(e)
    return groups


def check_authority_monotonicity(events):
    """An event's authority.permission_epoch must not exceed the epoch of the event its
    approval_record claims to derive from (approval_record holds another event's event_id)."""
    by_id = {e.get("event_id"): e for e in events}
    violations = []
    for e in events:
        auth = e.get("authority") or {}
        epoch = auth.get("permission_epoch")
        approval_record = auth.get("approval_record")
        if epoch is None or not approval_record:
            continue
        ref = by_id.get(approval_record)
        if ref is None:
            continue
        ref_epoch = (ref.get("authority") or {}).get("permission_epoch")
        if ref_epoch is not None and epoch > ref_epoch:
            violations.append({
                "event_id": e.get("event_id"),
                "entity_id": e.get("entity_id"),
                "reason": (
                    f"permission_epoch {epoch} exceeds approval_record "
                    f"{approval_record}'s epoch {ref_epoch}"
                ),
            })
    verdict = "FAIL" if violations else "PASS"
    return verdict, violations


def _scope_width(scope):
    """0 = narrow (issue- or pr-scoped), 1 = wide (repo-wide, no issue/pr)."""
    scope = scope or {}
    if scope.get("issue") or scope.get("pr"):
        return 0
    return 1


def check_scope_non_expansion(events):
    """Across events sharing an entity_id (in file order), scope must not widen without a
    new authorizing event of equal or higher permission_epoch than the prior event."""
    violations = []
    for entity_id, group in _group_by_entity(events).items():
        if entity_id is None:
            continue
        prev = None
        for e in group:
            width = _scope_width(e.get("scope"))
            epoch = (e.get("authority") or {}).get("permission_epoch")
            if prev is not None:
                prev_width, prev_epoch = prev
                if width > prev_width and (
                    epoch is None or prev_epoch is None or epoch < prev_epoch
                ):
                    violations.append({
                        "event_id": e.get("event_id"),
                        "entity_id": entity_id,
                        "reason": (
                            f"scope widened (width {prev_width} -> {width}) without an "
                            f"authorizing event of equal or higher permission_epoch "
                            f"(prev={prev_epoch}, this={epoch})"
                        ),
                    })
            prev = (width, epoch)
    verdict = "FAIL" if violations else "PASS"
    return verdict, violations


def check_provenance_preservation(events):
    """Every event with actionability != evidence must carry a non-null provenance.source
    and (run_id or commit); policy/permission/skill/external_commitment additionally need
    trust_tier in {trusted, reviewed}."""
    violations = []
    for e in events:
        actionability = e.get("actionability")
        if actionability is None or actionability == "evidence":
            continue
        prov = e.get("provenance") or {}
        source = prov.get("source")
        has_join_key = bool(prov.get("run_id") or prov.get("commit"))
        if not source or not has_join_key:
            violations.append({
                "event_id": e.get("event_id"),
                "entity_id": e.get("entity_id"),
                "reason": "missing provenance.source or both run_id/commit join keys",
            })
            continue
        if actionability in _HIGH_ACTIONABILITY:
            trust_tier = prov.get("trust_tier")
            if trust_tier not in ("trusted", "reviewed"):
                violations.append({
                    "event_id": e.get("event_id"),
                    "entity_id": e.get("entity_id"),
                    "reason": (
                        f"actionability={actionability} requires provenance.trust_tier "
                        f"trusted|reviewed, got {trust_tier!r}"
                    ),
                })
    verdict = "FAIL" if violations else "PASS"
    return verdict, violations


def check_rollback_traceability(events):
    """Every event with actionability in {policy, permission, skill, external_commitment}
    must carry a non-null recoverability.rollback_handle or transaction_id."""
    violations = []
    for e in events:
        actionability = e.get("actionability")
        if actionability not in _HIGH_ACTIONABILITY:
            continue
        rec = e.get("recoverability") or {}
        if not rec.get("rollback_handle") and not rec.get("transaction_id"):
            violations.append({
                "event_id": e.get("event_id"),
                "entity_id": e.get("entity_id"),
                "reason": (
                    f"actionability={actionability} has no recoverability.rollback_handle "
                    f"or transaction_id"
                ),
            })
    verdict = "FAIL" if violations else "PASS"
    return verdict, violations


def check_deletion_propagation(events):
    """Every tombstone/delete/quarantine event must be reflected in any later 'act' event
    reading the same entity_id; an 'act' event that still reports mutability.status=active
    after a tombstone ignores it."""
    violations = []
    for entity_id, group in _group_by_entity(events).items():
        if entity_id is None:
            continue
        tombstoned = False
        for e in group:
            op = e.get("operation")
            if op in _TOMBSTONE_OPS:
                tombstoned = True
                continue
            if op == "act" and tombstoned:
                status = (e.get("mutability") or {}).get("status")
                if status in _ACTIVE_STATUSES:
                    violations.append({
                        "event_id": e.get("event_id"),
                        "entity_id": entity_id,
                        "reason": (
                            "act event reads entity as mutability.status=active after a "
                            "tombstone/delete/quarantine event"
                        ),
                    })
    verdict = "FAIL" if violations else "PASS"
    return verdict, violations
