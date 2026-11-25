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
