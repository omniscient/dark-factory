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


def _resolve_approval(event, seen_by_id, required_epoch):
    """Resolve `event`'s authority.approval_record under the shared authorization rules
    (Gate-3 round 3 on #190). Returns (authorized: bool, reason: str or None).

    An approval_record authorizes only if ALL hold:
    - it is present (non-empty);
    - it is not the event's own event_id (self-approval never authorizes);
    - it resolves to an event ALREADY SEEN before this one in file order (a later or
      dangling reference never retroactively authorizes);
    - the approval event is linked to the governed entity: its entity_id equals the
      event's entity_id, OR its subject_entity_id field equals the event's entity_id;
    - its authority.actor is "human" (human approvals may grant any epoch), OR its
      authority.permission_epoch is >= required_epoch.
    """
    auth = event.get("authority") or {}
    approval_record = auth.get("approval_record")
    if not approval_record:
        return False, "no approval_record"
    if approval_record == event.get("event_id"):
        return False, (
            f"approval_record {approval_record} is the event itself "
            f"(self-approval never authorizes)"
        )
    ref = seen_by_id.get(approval_record)
    if ref is None:
        return False, (
            f"approval_record {approval_record} does not resolve to any "
            f"previously-seen event (later or dangling references never authorize)"
        )
    entity_id = event.get("entity_id")
    if ref.get("entity_id") != entity_id and ref.get("subject_entity_id") != entity_id:
        return False, (
            f"approval_record {approval_record} is not linked to entity {entity_id} "
            f"(neither its entity_id nor its subject_entity_id matches)"
        )
    ref_auth = ref.get("authority") or {}
    if ref_auth.get("actor") == "human":
        return True, None  # human approvals may grant any epoch
    ref_epoch = ref_auth.get("permission_epoch")
    if ref_epoch is not None and required_epoch is not None and ref_epoch >= required_epoch:
        return True, None  # authorized by an event of equal-or-higher epoch
    return False, (
        f"approval_record {approval_record} has epoch {ref_epoch} < {required_epoch} "
        f"and its actor is not human"
    )


def check_authority_monotonicity(events):
    """An event may not raise authority.permission_epoch above the prior event of the same
    entity_id (file order) unless the increase is authorized per _resolve_approval: the
    approval_record must be a previously-seen, non-self event linked to the escalated
    entity (same entity_id, or subject_entity_id pointing at it) whose permission_epoch
    is >= the new epoch or whose authority.actor is "human". A missing, dangling, later,
    self-referencing, or entity-unlinked approval_record on an epoch increase is a
    violation. An entity's first appearance is baseline; non-increasing epochs need no
    approval. Flags forged/inflated authority claims (Gate-3 rounds 1-3 on #190)."""
    violations = []
    prev_epoch_by_entity = {}
    seen_by_id = {}
    for e in events:
        entity_id = e.get("entity_id")
        auth = e.get("authority") or {}
        epoch = auth.get("permission_epoch")
        if entity_id is None or epoch is None:
            seen_by_id[e.get("event_id")] = e
            continue
        prev_epoch = prev_epoch_by_entity.get(entity_id)
        prev_epoch_by_entity[entity_id] = epoch
        if prev_epoch is not None and epoch > prev_epoch:
            authorized, why = _resolve_approval(e, seen_by_id, epoch)
            if not authorized:
                violations.append({
                    "event_id": e.get("event_id"),
                    "entity_id": entity_id,
                    "reason": f"permission_epoch increased {prev_epoch} -> {epoch}: {why}",
                })
        seen_by_id[e.get("event_id")] = e
    verdict = "FAIL" if violations else "PASS"
    return verdict, violations


def _scope_width(scope):
    """0 = narrow (issue- or pr-scoped), 1 = wide (repo-wide, no issue/pr)."""
    scope = scope or {}
    if scope.get("issue") or scope.get("pr"):
        return 0
    return 1


def check_scope_non_expansion(events):
    """Across events sharing an entity_id (in file order), scope must not widen
    (per _scope_width) unless the widening event carries an approval_record that
    resolves per _resolve_approval: a previously-seen, non-self event linked to the
    widened entity (same entity_id, or subject_entity_id pointing at it) whose
    permission_epoch is >= the widening event's epoch or whose authority.actor is
    "human". A widening event's self-declared epoch never authorizes on its own
    (Gate-3 round 3 on #190)."""
    violations = []
    prev_width_by_entity = {}
    seen_by_id = {}
    for e in events:
        entity_id = e.get("entity_id")
        if entity_id is None:
            seen_by_id[e.get("event_id")] = e
            continue
        width = _scope_width(e.get("scope"))
        prev_width = prev_width_by_entity.get(entity_id)
        prev_width_by_entity[entity_id] = width
        if prev_width is not None and width > prev_width:
            epoch = (e.get("authority") or {}).get("permission_epoch")
            authorized, why = _resolve_approval(e, seen_by_id, epoch)
            if not authorized:
                violations.append({
                    "event_id": e.get("event_id"),
                    "entity_id": entity_id,
                    "reason": (
                        f"scope widened (width {prev_width} -> {width}) "
                        f"without valid authorization: {why}"
                    ),
                })
        seen_by_id[e.get("event_id")] = e
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


CHECK_FUNCS = {
    "authority_monotonicity": check_authority_monotonicity,
    "scope_non_expansion": check_scope_non_expansion,
    "deletion_propagation": check_deletion_propagation,
    "provenance_preservation": check_provenance_preservation,
    "rollback_traceability": check_rollback_traceability,
}


def compute_scorecard(events, now, run_id):
    checks = []
    fail_count = 0
    for name in CHECK_NAMES:
        verdict, violations = CHECK_FUNCS[name](events)
        if verdict == "FAIL":
            fail_count += 1
        checks.append({"name": name, "verdict": verdict, "violations": violations})
    status = "FAIL" if fail_count else "PASS"
    score = round(100 * (len(CHECK_NAMES) - fail_count) / len(CHECK_NAMES))
    return {
        "STATUS": status,
        "generated_at": now,
        "run_id": run_id,
        "event_count": len(events),
        "checks": checks,
        "score": score,
    }


def write_json(scorecard, out_path):
    out_path.write_text(json.dumps(scorecard, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def write_markdown(scorecard, out_path):
    lines = [
        "# Dark Factory State Governance Scorecard",
        "",
        f"Generated: {scorecard['generated_at']}",
        f"Run: {scorecard['run_id']}",
        "",
        f"**STATUS:** {scorecard['STATUS']} — **Score:** {scorecard['score']}/100 "
        f"— **Events evaluated:** {scorecard['event_count']}",
        "",
        "## Checks",
        "",
        "| Check | Verdict | Violations |",
        "|---|---|---|",
    ]
    for c in scorecard["checks"]:
        lines.append(f"| {c['name']} | {c['verdict']} | {len(c['violations'])} |")
    lines.append("")
    for c in scorecard["checks"]:
        if not c["violations"]:
            continue
        lines.append(f"### {c['name']} violations")
        lines.append("")
        for v in c["violations"]:
            lines.append(f"- `{v.get('entity_id')}` / `{v.get('event_id')}`: {v.get('reason')}")
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fixtures", required=True, help="Directory of *.jsonl fixtures, or a single .jsonl file")
    p.add_argument("--out-dir", required=True, help="Directory to write state-governance-scorecard.{json,md}")
    p.add_argument("--now", default="", help="Fixed generated_at timestamp (default: current UTC time)")
    p.add_argument("--run-id", default="", help="Fixed run_id for the scorecard (default: 'adhoc')")
    return p.parse_args()


def main():
    args = parse_args()
    now = args.now
    if not now:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id = args.run_id or "adhoc"

    events = load_events(args.fixtures)
    scorecard = compute_scorecard(events, now, run_id)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(scorecard, out_dir / "state-governance-scorecard.json")
    write_markdown(scorecard, out_dir / "state-governance-scorecard.md")

    print(f"STATUS: {scorecard['STATUS']}", file=sys.stderr)
    print(f"score: {scorecard['score']}/100 over {len(CHECK_NAMES)} checks", file=sys.stderr)


if __name__ == "__main__":
    main()
