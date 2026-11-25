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
