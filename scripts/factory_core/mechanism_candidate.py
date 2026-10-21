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


# ── Paths, diff extraction and containment ──────────────────────────────────────

def _check_path_text(p, name):
    if not p:
        _fail(f"{name} is empty")
    if p.startswith("/"):
        _fail(f"{name} {p!r} is absolute")
    if "\\" in p:
        _fail(f"{name} {p!r} contains a backslash")
    if '"' in p:
        _fail(f"{name} {p!r} is C-quoted or escaped")
    if any(ord(ch) < 32 for ch in p):
        _fail(f"{name} {p!r} contains a control character")
    if ".." in p.split("/"):
        _fail(f"{name} {p!r} contains a '..' segment")


def _normalise(p):
    """posixpath.normpath, keeping a trailing '/' that marks a directory prefix."""
    core = posixpath.normpath(p)
    return core + "/" if p.endswith("/") and core != "." else core


def _validate_allowlist(entries):
    _str_list(entries, "editable_path_allowlist")
    for e in entries:
        name = "editable_path_allowlist entry"
        if _GLOB_CHARS & set(e):
            _fail(f"{name} {e!r} contains a glob character; literal paths or '/'-terminated prefixes only")
        _check_path_text(e, name)
        if _normalise(e) != e or e in (".", "./"):
            _fail(f"{name} {e!r} is not a normalised repo-relative path")


def extract_diff_paths(diff):
    """Parse an exact unified diff into {normalised path: [changed +/- lines]}.

    Every file block must start with `diff --git a/X b/Y`; its `---`/`+++`/`rename`/`copy`
    header names must agree per side after normalisation (and across sides unless the
    block is a rename/copy). Hunk bodies are consumed by their `@@ -a,b +c,d @@` counts,
    exactly as `git apply` reads them, so header-looking lines inside a hunk are content
    and nothing may follow an exhausted hunk except another `@@`, a `diff --git`, or a
    `\\ No newline` marker — a smuggled `---`/`+++` triplet is rejected, never skipped.
    Only git's known extended header lines are accepted; binary patches, absolute,
    backslash, `..`, C-quoted, and whitespace-bearing names, and an empty diff are
    rejected. Both sides of a rename/copy are returned.
    """
    if not isinstance(diff, str) or not diff.strip():
        _fail("diff is empty")
    blocks = []
    cur = None
    old_left = new_left = 0  # lines the current hunk still owes, per side
    in_hunks = False         # past the current block's first @@
    trailing = False         # a blank line ended the diff; only blank lines may follow
    for line in diff.splitlines():
        if trailing:
            if line.strip():
                _fail("diff has content after the blank line that ends it")
            continue
        if old_left or new_left:
            tag = line[:1]
            if tag == "\\":
                continue
            if line == "" or tag == " ":
                old_left, new_left = old_left - 1, new_left - 1
            elif tag == "-":
                old_left -= 1
            elif tag == "+":
                new_left -= 1
            else:
                _fail(f"malformed hunk line {line!r}")
            if old_left < 0 or new_left < 0:
                _fail("hunk body does not match its @@ line counts")
            if tag in ("+", "-"):
                cur["changes"].append(line)
            continue
        if line.startswith("diff --git"):
            if '"' in line:
                _fail(f"diff header {line!r} uses a C-quoted path")
            m = _GIT_HEADER_RE.match(line)
            if not m:
                _fail(f"malformed diff --git header {line!r}")
            cur = {"src": [m.group(1)], "dst": [m.group(2)], "rename_or_copy": False, "changes": []}
            blocks.append(cur)
            in_hunks = False
            continue
        if cur is None:
            if line.strip():
                _fail("diff has content before its first 'diff --git' header")
            continue
        if line.startswith("@@"):
            m = _HUNK_RE.match(line)
            if not m:
                _fail(f"malformed hunk header {line!r}")
            old_left = int(m.group(1) or "1")
            new_left = int(m.group(2) or "1")
            in_hunks = True
            continue
        if not line.strip():
            trailing = True
            continue
        if in_hunks:
            if line.startswith("\\"):
                continue
            _fail(f"unexpected line {line!r} after a hunk (only @@, diff --git, or the end "
                  f"of the diff may follow a complete hunk)")
        if line.startswith("--- ") or line.startswith("+++ "):
            if '"' in line:
                _fail(f"diff header {line!r} uses a C-quoted path")
            is_old = line.startswith("--- ")
            m = (_OLD_HEADER_RE if is_old else _NEW_HEADER_RE).match(line)
            if not m:
                _fail(f"malformed diff file header {line!r}")
            if m.group(1):
                cur["src" if is_old else "dst"].append(m.group(1))
        elif _RENAME_COPY_RE.match(line):
            m = _RENAME_COPY_RE.match(line)
            if '"' in m.group(2):
                _fail(f"diff header {line!r} uses a C-quoted path")
            cur["src" if m.group(1) == "from" else "dst"].append(m.group(2))
            cur["rename_or_copy"] = True
        elif line.startswith("GIT binary patch") or line.startswith("Binary files "):
            _fail("binary patches are not accepted")
        elif not _EXTENDED_HEADER_RE.match(line):
            _fail(f"unrecognised diff header line {line!r}")
    if old_left or new_left:
        _fail("diff ends inside a hunk (fewer lines than its @@ counts)")
    paths = {}
    for b in blocks:
        for raw in b["src"] + b["dst"]:
            _check_path_text(raw, "diff path")
            if any(ch.isspace() for ch in raw):
                _fail(f"diff path {raw!r} contains whitespace")
        srcs = {posixpath.normpath(p) for p in b["src"]}
        dsts = {posixpath.normpath(p) for p in b["dst"]}
        if len(srcs) != 1 or len(dsts) != 1 or (not b["rename_or_copy"] and srcs != dsts):
            _fail(f"diff block header names disagree: {sorted(srcs | dsts)}")
        for p in srcs | dsts:
            if p == ".":
                _fail("diff path normalises to the repository root")
            paths.setdefault(p, []).extend(b["changes"])
    return paths


def _contained(path, allowlist):
    return any(path == e or (e.endswith("/") and path.startswith(e)) for e in allowlist)


# ── Risk classification ─────────────────────────────────────────────────────────

def load_safety(clone_dir):
    """safety.hard_exclude_paths / critical_diff_paths from adapter.load() — the merged,
    boundary-floored lists, never raw adapter_defaults.DEFAULTS (MarketHawk constants)."""
    try:
        safety = adapter.load(clone_dir)["safety"]
    except adapter.AdapterError as exc:
        _fail(f"cannot resolve safety lists from adapter.load({clone_dir!r}): {exc}")
    return _resolved_safety({
        "hard_exclude_paths": safety.get("hard_exclude_paths"),
        "critical_diff_paths": safety.get("critical_diff_paths"),
    })


def _resolved_safety(safety):
    if safety is None:
        return load_safety(os.environ.get("CLONE_DIR", "."))
    for key in ("hard_exclude_paths", "critical_diff_paths"):
        v = safety.get(key) if isinstance(safety, dict) else None
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            _fail(f"resolved safety.{key} must be a list of strings (fail closed)")
    try:
        for pattern in safety["critical_diff_paths"]:
            re.compile(pattern)
    except re.error as exc:
        _fail(f"resolved safety.critical_diff_paths has an invalid regex: {exc}")
    return safety


def classify_path(path, safety, *, tool_frontmatter=False):
    """Risk class of one repo-relative path (default-deny). `safety` must already be
    resolved (load_safety()/an explicit dict)."""
    p = _normalise(path)
    folded = p.casefold()
    if any(folded.startswith(pre.casefold()) for pre in H3_PREFIXES):
        return "H3"
    if any(p.startswith(pre) for pre in safety["hard_exclude_paths"]):
        return "H3"
    if any(re.search(pat, p) for pat in safety["critical_diff_paths"]):
        return "H3"
    if any(p.startswith(pre) for pre in H0_PREFIXES):
        return "H0"
    if p.startswith(H1_COMMANDS_DIR) and p.endswith(".md") \
            and "/" not in p[len(H1_COMMANDS_DIR):]:
        return "H3" if tool_frontmatter else "H1"
    if H2_SCRIPT_RE.match(p) or any(p.startswith(pre) for pre in H2_PREFIXES):
        return "H2"
    return "H3"


def classify_risk(touched_paths, safety=None, tool_frontmatter_paths=()):
    """Maximum risk class over `touched_paths`; an empty set is H3 (default-deny)."""
    safety = _resolved_safety(safety)
    frontmatter = {_normalise(p) for p in tool_frontmatter_paths}
    classes = [
        classify_path(p, safety, tool_frontmatter=_normalise(p) in frontmatter)
        for p in touched_paths
    ]
    if not classes:
        return "H3"
    return max(classes, key=_RISK_RANK.__getitem__)
