"""Dispatch-role binding and the verdict-independence proof (#559).

Contract: docs/hybrid-execution-contract.md §4.2, §4.4; design in
docs/archive/2026-11-25-worker-orchestrator-checker-isolation-design.md.

Pure functions over plain values and model_dispatch["result"]-shaped dicts. Nothing here
reads the process environment, the disk or the network, so role isolation never follows from
an instance-wide endpoint selection (spec req. 6), and the module holds no credential it could
leak (spec req. 8). Neither function raises; each returns an outcome and a reason code.

The declared role is harness-written but pointer-grade: the run container can write the
current-run.json pointer it arrives in. It can therefore only narrow what a dispatch may do
(a local dispatch is "actor" or refused) and never grants authority by itself.
"""
VALID_HOSTING = ("local", "cloud")
VALID_ROLES = ("actor", "checker", "monitor")
# bind_dispatch_role reasons that are refusals; the caller refuses before any outbound call.
REFUSALS = ("hosting_location_invalid", "role_invalid", "nested_checker_refused")


def bind_dispatch_role(hosting_location, declared_role=None) -> tuple[str | None, str]:
    """(role, reason). A refusal is role None with a reason in REFUSALS; an undeclared cloud
    role is (None, "undeclared") — never "checker"."""
    if hosting_location not in VALID_HOSTING:
        return None, "hosting_location_invalid"
    if declared_role is None:
        if hosting_location == "local":
            return "actor", "default_actor"
        return None, "undeclared"  # today's cloud rows: role stays None
    if declared_role not in VALID_ROLES:
        return None, "role_invalid"
    if hosting_location == "local" and declared_role != "actor":
        return None, "nested_checker_refused"  # §4.2: local never hosts an independent checker
    return declared_role, "declared"


# The six run_record.py Result-contract keys this proof reads (MODEL_DISPATCH_RESULT_VERSION
# "1.0", emitted by _compute_model_dispatch); duck-typed, so run_record is never imported.
RESULT_KEYS = ("schema_version", "outcome", "endpoint_id", "hosting_location", "role",
               "correlation")


def _well_formed(d) -> tuple[bool, str]:
    """Structure of one model_dispatch["result"] dict: (ok, reason). Never raises."""
    if not isinstance(d, dict) or any(k not in d for k in RESULT_KEYS):
        return False, "malformed_dispatch_record"
    version = d["schema_version"]
    if not isinstance(version, str) or not version.startswith("1."):  # any minor: Invariant 12
        return False, "malformed_dispatch_record"
    if not isinstance(d["outcome"], str) or not isinstance(d["correlation"], str):
        return False, "malformed_dispatch_record"
    if any(d[k] is not None and not isinstance(d[k], str) for k in ("endpoint_id", "role")):
        return False, "malformed_dispatch_record"
    if d["hosting_location"] not in VALID_HOSTING:
        return False, "hosting_location_invalid"
    return True, "well_formed"


def check_verdict_independence(actor_dispatch, producer_dispatch, *,
                               actor_issue, producer_issue, verdict_issue) -> tuple[bool, str]:
    """The §4.2 independence proof over two model_dispatch["result"]-shaped dicts.

    The three issue numbers bind the proof to one unit of work: each run record's own
    top-level issue_number, and the issue the verdict is for. A caller (#611) maps every
    False other than "not_applicable" to STATUS: BLOCKED plus ready-for-human — never PASS,
    SKIPPED or ERROR (verdict.py's ERROR proceeds); "not_applicable" (a cloud-routed actor)
    leaves today's all-cloud gate unchanged.
    """
    for d in (actor_dispatch, producer_dispatch):
        ok, reason = _well_formed(d)
        if not ok:
            return False, reason
    if actor_dispatch["hosting_location"] != "local":
        return False, "not_applicable"  # Invariant 3: only a local-routed actor needs this proof
    issues = (actor_issue, producer_issue, verdict_issue)
    if any(type(i) is not int or i <= 0 for i in issues) or len(set(issues)) != 1:
        return False, "unit_of_work_mismatch"
    if producer_dispatch["hosting_location"] != "cloud":
        return False, "producer_not_cloud"
    if actor_dispatch["outcome"] != "ok" or producer_dispatch["outcome"] != "ok":
        return False, "dispatch_outcome_not_ok"
    if (actor_dispatch["correlation"] != "inferred_pointer"
            or producer_dispatch["correlation"] != "inferred_pointer"):
        return False, "correlation_unknown"  # Invariant 3 / §4.4: never the proof
    if producer_dispatch["endpoint_id"] is None:
        return False, "producer_endpoint_missing"
    if producer_dispatch["endpoint_id"] == actor_dispatch["endpoint_id"]:
        return False, "producer_endpoint_same_as_actor"
    if producer_dispatch["role"] != "checker":
        return False, "producer_role_not_checker"
    return True, "independent"
