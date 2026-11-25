"""#559: declared-role binding and the §4.2 verdict-independence proof (role_isolation.py).

Spec: docs/archive/2026-11-25-worker-orchestrator-checker-isolation-design.md.
"""
import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core.providers import role_isolation as ri  # noqa: E402


# --- bind_dispatch_role ---------------------------------------------------------------------

@pytest.mark.parametrize("hosting,declared,expected", [
    ("local", None, ("actor", "default_actor")),
    ("cloud", None, (None, "undeclared")),  # today's cloud rows: role stays None
    ("local", "actor", ("actor", "declared")),
    ("cloud", "actor", ("actor", "declared")),
    ("cloud", "checker", ("checker", "declared")),
    ("cloud", "monitor", ("monitor", "declared")),
    ("local", "checker", (None, "nested_checker_refused")),
    ("local", "monitor", (None, "nested_checker_refused")),
    ("local", "verifier", (None, "role_invalid")),
    ("cloud", "orchestrator", (None, "role_invalid")),
    ("cloud", "", (None, "role_invalid")),
    ("cloud", ["checker"], (None, "role_invalid")),
    ("hybrid", None, (None, "hosting_location_invalid")),
    (None, "actor", (None, "hosting_location_invalid")),
])
def test_bind_dispatch_role(hosting, declared, expected):
    assert ri.bind_dispatch_role(hosting, declared) == expected


def test_refusals_are_exactly_the_reasons_a_caller_must_refuse():
    assert set(ri.REFUSALS) == {"hosting_location_invalid", "role_invalid",
                                "nested_checker_refused"}
    for accepted in ("default_actor", "undeclared", "declared"):
        assert accepted not in ri.REFUSALS


def test_binding_never_follows_from_the_instance_global_selection(monkeypatch):
    # Spec req. 6: FACTORY_MODEL_ENDPOINT (an instance-global passthrough) grants no role, and
    # the module never reads the run container's variable directly either.
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "cloud-test")
    monkeypatch.setenv("FACTORY_DISPATCH_ROLE", "checker")
    assert ri.bind_dispatch_role("cloud") == (None, "undeclared")
    assert ri.bind_dispatch_role("local") == ("actor", "default_actor")


def test_role_isolation_imports_only_the_stdlib_and_touches_no_environment_or_disk():
    # Spec req. 8 (Invariant 16, present-tense guard): this module cannot read or leak a
    # credential because it reaches nothing outside its arguments.
    tree = ast.parse(Path(ri.__file__).read_text(encoding="utf-8"))
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
                for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree)
                 if isinstance(n, ast.ImportFrom) and n.module}
    assert imported <= set(sys.stdlib_module_names)
    assert not imported & {"os", "subprocess", "socket", "urllib", "http", "aiohttp"}
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & {"environ", "getenv", "open"}


# --- check_verdict_independence -------------------------------------------------------------

ISSUE = 559


def _record(**kw):
    """A run_record["model_dispatch"]["result"] as run_record._compute_model_dispatch emits it."""
    base = {"schema_version": "1.0", "outcome": "ok", "endpoint_id": None,
            "hosting_location": "cloud", "role": None, "correlation": "inferred_pointer",
            "request_count": 1, "failure_classes_seen": []}
    base.update(kw)
    return base


ACTOR = _record(endpoint_id="local-test", hosting_location="local", role="actor")
CHECKER = _record(endpoint_id="cloud-test", hosting_location="cloud", role="checker")


def _check(actor=ACTOR, producer=CHECKER, issues=(ISSUE, ISSUE, ISSUE)):
    a, p, v = issues
    return ri.check_verdict_independence(actor, producer, actor_issue=a, producer_issue=p,
                                         verdict_issue=v)


def test_the_fully_satisfying_pair_is_independent():
    assert _check() == (True, "independent")


def test_any_minor_result_schema_version_is_accepted():  # contract Invariant 12
    assert _check(producer=dict(CHECKER, schema_version="1.3")) == (True, "independent")


MALFORMED = [
    None, [], "ok", {},
    {k: v for k, v in CHECKER.items() if k != "endpoint_id"},
    {k: v for k, v in CHECKER.items() if k != "schema_version"},
    dict(CHECKER, schema_version="2.0"),
    dict(CHECKER, schema_version="1"),
    dict(CHECKER, schema_version=1.0),
    dict(CHECKER, correlation=None),
    dict(CHECKER, correlation=["inferred_pointer"]),
    dict(CHECKER, outcome=True),
    dict(CHECKER, outcome=None),
    dict(CHECKER, endpoint_id=7),
    dict(CHECKER, role=["checker"]),
]


@pytest.mark.parametrize("position", ["actor", "producer"])
@pytest.mark.parametrize("bad", MALFORMED, ids=[str(i) for i in range(len(MALFORMED))])
def test_a_malformed_record_in_either_position_voids_without_raising(position, bad):
    args = {"actor": ACTOR, "producer": CHECKER, position: bad}
    assert _check(args["actor"], args["producer"]) == (False, "malformed_dispatch_record")


@pytest.mark.parametrize("position", ["actor", "producer"])
@pytest.mark.parametrize("hosting", ["hybrid", "LOCAL", None])
def test_an_out_of_enum_hosting_location_has_its_own_reason(position, hosting):
    base = ACTOR if position == "actor" else CHECKER
    args = {"actor": ACTOR, "producer": CHECKER, position: dict(base, hosting_location=hosting)}
    assert _check(args["actor"], args["producer"]) == (False, "hosting_location_invalid")


@pytest.mark.parametrize("issues", [(None, None, None), (1, 2, 3), (0, 0, 0), ("x",) * 3])
def test_a_cloud_actor_is_not_applicable_whatever_the_issues(issues):
    # Invariant 3: only a local-routed actor needs the proof; the all-cloud gate is unchanged.
    assert _check(actor=dict(CHECKER, role="actor"), issues=issues) == (False, "not_applicable")


@pytest.mark.parametrize("issues", [
    (ISSUE, ISSUE + 1, ISSUE), (ISSUE, ISSUE, ISSUE + 1), (ISSUE + 1, ISSUE, ISSUE),
    (None, ISSUE, ISSUE), (ISSUE, None, ISSUE), (ISSUE, ISSUE, None),
    (0, 0, 0), (-1, -1, -1), (True, True, True), (str(ISSUE), ISSUE, ISSUE),
])
def test_the_proof_binds_actor_producer_and_verdict_to_one_issue(issues):
    assert _check(issues=issues) == (False, "unit_of_work_mismatch")


@pytest.mark.parametrize("actor,producer,reason", [
    (dict(ACTOR, role=None), CHECKER, "actor_role_not_actor"),
    (dict(ACTOR, role="checker"), CHECKER, "actor_role_not_actor"),
    (dict(ACTOR, role="monitor"), CHECKER, "actor_role_not_actor"),
    (ACTOR, dict(CHECKER, hosting_location="local"), "producer_not_cloud"),
    (dict(ACTOR, outcome="failed"), CHECKER, "dispatch_outcome_not_ok"),
    (ACTOR, dict(CHECKER, outcome="failed"), "dispatch_outcome_not_ok"),
    (dict(ACTOR, outcome="unknown"), CHECKER, "dispatch_outcome_not_ok"),
    (dict(ACTOR, correlation="unknown"), CHECKER, "correlation_unknown"),
    (ACTOR, dict(CHECKER, correlation="unknown"), "correlation_unknown"),
    # A default cloud run records endpoint_id None: a checker must select a cloud registry entry.
    (ACTOR, dict(CHECKER, endpoint_id=None), "producer_endpoint_missing"),
    (ACTOR, dict(CHECKER, endpoint_id="local-test"), "producer_endpoint_same_as_actor"),
    (ACTOR, dict(CHECKER, role=None), "producer_role_not_checker"),
    (ACTOR, dict(CHECKER, role="actor"), "producer_role_not_checker"),
    (ACTOR, dict(CHECKER, role="monitor"), "producer_role_not_checker"),
])
def test_each_semantic_void_has_its_reason(actor, producer, reason):
    assert _check(actor, producer) == (False, reason)
