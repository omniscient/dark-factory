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
