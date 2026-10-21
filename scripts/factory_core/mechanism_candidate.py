"""Harness-improvement candidate contract and mechanism lineage validator (#464, epic #463).

Data and lifecycle only: validates `mechanism_candidates` records and their append-only
`mechanism_lineage` audit trail (state_governance_audit.py's #190 event envelope, reused
verbatim). Nothing here executes, probes, promotes, or activates a candidate, and no
caller is wired. Candidate payloads are untrusted data, never instructions — every
check fails closed. Spec:
docs/superpowers/specs/2026-10-21-harness-candidate-contract-mechanism-lineage-design.md

Usage:
    PYTHONPATH=scripts python3 -m factory_core.mechanism_candidate \\
        --fixtures evals/mechanism-candidates/fixtures/ \\
        [--clone-dir .] [--out-dir "$ARTIFACTS_DIR"]

Unlike state_governance_audit.py, --out-dir is optional and the process exits non-zero
when any candidate record or lineage file fails: this is a fail-closed gate input, not
a scorecard writer. A lineage file is validated against the candidate records in the
same --fixtures directory, so point --fixtures at a directory, not a lone .jsonl file.
"""
import argparse
import hashlib
import json
import math
import os
import posixpath
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import state_governance_audit as _sga  # noqa: E402  (#190 envelope + checks, reused as-is)

from . import adapter  # noqa: E402


class MechanismCandidateError(Exception):
    pass


SCHEMA_VERSION = 1

# One shared shape check for every digest-valued field and digest-list element.
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

RISK_CLASSES = ("H0", "H1", "H2", "H3")
_RISK_RANK = {c: i for i, c in enumerate(RISK_CLASSES)}

STATES = ("draft", "quarantined", "probed", "composed", "shadow", "advisory", "revoked")

# Allowed lifecycle edges. revoked is terminal; quarantined is one-way (-> revoked only).
TRANSITIONS = {
    "draft": {"probed", "quarantined", "revoked"},
    "probed": {"composed", "quarantined", "revoked"},
    "composed": {"shadow", "quarantined", "revoked"},
    "shadow": {"advisory", "quarantined", "revoked"},
    "advisory": {"quarantined", "revoked"},
    "quarantined": {"revoked"},
    "revoked": set(),
}
_ADVANCING_STATES = {"probed", "composed", "shadow", "advisory"}
_HUMAN_APPROVAL_STATES = {"shadow", "advisory"}

# Lifecycle -> #190 operation mapping (spec "mechanism_lineage event mapping"). Expiry
# firing is also a tombstone.
_OPERATION_TARGETS = {
    "write": ("draft",),
    "validate": ("probed",),
    "act": ("composed", "shadow", "advisory"),
    "quarantine": ("quarantined",),
    "tombstone": ("revoked",),
}
LINEAGE_OPERATIONS = frozenset(_OPERATION_TARGETS)
MUTABILITY_STATUSES = frozenset({"active", "tombstoned", "quarantined", "superseded"})
# state_governance_audit.py's #190 envelope, verbatim (+ optional subject_entity_id).
_ENVELOPE_REQUIRED = (
    "event_id", "idempotency_key", "operation", "state_type", "entity_id", "authority",
    "scope", "provenance", "mutability", "recoverability", "actionability",
)

_BASELINE_FIELDS = (
    "harness_digest", "repo_digest", "task_digest", "model_runtime_digest",
    "tool_digest", "verifier_digest", "policy_digest", "library_digest",
)

_RECORD_FIELDS = (
    "schema_version", "candidate_id", "candidate_digest", "parent_digest", "baseline",
    "source", "observed_failure", "invariant_scope", "domain_scope", "risk_class",
    "editable_path_allowlist", "diff", "dependency_closure",
    "expected_behavioral_hypothesis", "resource_envelope", "permission_envelope",
    "network_envelope", "filesystem_envelope", "side_effect_envelope",
    "probe_evidence_refs", "contradictions", "confidence", "reviewer", "approver",
    "lifecycle_state", "supersession", "expiry", "rollback_handle",
)

# The identity-bearing fields candidate_digest is computed over. Lifecycle/governance
# bookkeeping (state, evidence, review, supersession, expiry, rollback, risk_class,
# schema_version, ids/digests) is excluded so the digest survives lifecycle advancement.
DIGEST_FIELDS = (
    "baseline", "source", "observed_failure", "invariant_scope", "domain_scope",
    "editable_path_allowlist", "diff", "dependency_closure",
    "expected_behavioral_hypothesis", "resource_envelope", "permission_envelope",
    "network_envelope", "filesystem_envelope", "side_effect_envelope",
)

# Default-deny path classifier (spec "Risk classification"). A path is H3 unless it
# matches an H0/H1/H2 allowlist below, and H3 regardless if it matches H3_PREFIXES
# (case-folded) or the resolved adapter.load() safety lists.
H0_PREFIXES = ("docs/superpowers/",)
H1_COMMANDS_DIR = "commands/"
H2_SCRIPT_RE = re.compile(r"^scripts/[^/]+\.(?:py|sh)$")
H2_PREFIXES = ("workflows/",)
H3_PREFIXES = (
    "tests/", "evals/", "config/", "scripts/factory_core/", "scripts/gate_",
    "scripts/budget_", "scripts/verdict_gate_check.sh", "scripts/push_gate_check.sh",
    "scripts/state_governance_audit.py", "scheduler.sh", "entrypoint.sh", "Dockerfile",
    "run-compose.yml", "deploy/", ".github/", ".factory/", ".claude/", ".archon/",
    "refinement-skills/",
)
# A changed allowed-tools:/disallowed-tools: line in a commands/*.md file forces H3.
_TOOL_FRONTMATTER_RE = re.compile(r"^[+-]\s*(?:allowed-tools|disallowed-tools)\s*:")

_GLOB_CHARS = frozenset("*?[")
_GIT_HEADER_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)$")
_OLD_HEADER_RE = re.compile(r"^--- (?:a/(\S+)|/dev/null)$")
_NEW_HEADER_RE = re.compile(r"^\+\+\+ (?:b/(\S+)|/dev/null)$")
_RENAME_COPY_RE = re.compile(r"^(?:rename|copy) (from|to) (.+)$")
_HUNK_RE = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
# git's extended header lines (besides rename/copy, handled above); anything else is rejected.
_EXTENDED_HEADER_RE = re.compile(
    r"^(?:old mode|new mode|deleted file mode|new file mode|similarity index|"
    r"dissimilarity index|index) ")


# ── Generic field helpers ───────────────────────────────────────────────────────

def _fail(msg):
    raise MechanismCandidateError(msg)


def _check_keys(block, name, required, optional=()):
    if not isinstance(block, dict):
        _fail(f"{name} must be a mapping")
    allowed = set(required) | set(optional)
    for key in block:
        if key not in allowed:
            _fail(f"{name}: unknown field '{key}'")
    for key in required:
        if key not in block:
            _fail(f"{name}: missing required field '{key}'")


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _req_str(v, name):
    if not isinstance(v, str) or not v.strip():
        _fail(f"{name} must be a non-empty string")


def _opt_str(v, name):
    if v is not None and not isinstance(v, str):
        _fail(f"{name} must be a string or null")


def _str_list(v, name):
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        _fail(f"{name} must be a list of strings")


def _pos_int(v, name, *, nullable=False):
    if v is None and nullable:
        return
    if not _is_int(v) or v < 1:
        _fail(f"{name} must be an int >= 1" + (" or null" if nullable else ""))


def _digest(v, name, *, nullable=False):
    if v is None and nullable:
        return
    if not isinstance(v, str) or not DIGEST_RE.fullmatch(v):
        _fail(f"{name} must match sha256:<64 lowercase hex>, got {v!r}")


def _digest_list(v, name):
    if not isinstance(v, list):
        _fail(f"{name} must be a list of digests")
    for i, d in enumerate(v):
        _digest(d, f"{name}[{i}]")


# ── Digests ─────────────────────────────────────────────────────────────────────

def canonical_digest(obj):
    """sha256 over the canonical JSON encoding: sorted keys, no whitespace, UTF-8."""
    data = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(data.encode("utf-8")).hexdigest()


def candidate_digest_of(record):
    """The candidate_digest a record must carry: canonical_digest over DIGEST_FIELDS."""
    return canonical_digest({k: record[k] for k in DIGEST_FIELDS})
