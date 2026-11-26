# Worker/Orchestrator/Checker Endpoint Isolation — Implementation Plan

**Issue:** #559

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development
> or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** Bind each dispatch's single endpoint to a harness-declared role, record that role per
request, refuse role/endpoint combinations the contract forbids, check the model the local
endpoint actually answered as, and ship the §4.2 verdict-independence proof as a pure function
proven against two real, concurrently running proxy instances.

**Architecture:** `entrypoint.sh` adds an optional `"role"` field to the existing
`current-run.json` pointer, and only when `FACTORY_DISPATCH_ROLE` is non-empty. The proxy's
`read_current_run()` returns it as `declared_role`. When an endpoint is selected,
`handle_proxy()` passes it through a new stdlib-only `providers/role_isolation.py`
(`bind_dispatch_role`). That call either refuses the request with 403 before any outbound call
or gives the role to record. The removed `LOCAL_ROLE` constant no longer decides it. The same
module provides `check_verdict_independence`, the proof that #611 will wire into a gate later.
`model_adapter.check_response_model` compares the response's `model` field against the entry's
`served_name`. With `FACTORY_MODEL_ENDPOINT` unset, nothing new runs: `role_isolation` is never
imported and rows keep `role: None`.

**Tech Stack:** Python 3 (stdlib + aiohttp, which the proxy already uses), pytest with
`aiohttp.test_utils.TestServer`/`TestClient` (already the proxy-test harness), bash for the
entrypoint pointer test.

**Spec:** `docs/superpowers/specs/2026-11-25-worker-orchestrator-checker-isolation-design.md`
(approved at fdd1e27, amendments A1–A8 applied).

## Hotspot notice (spec requirement 11 — human signature hold)

This change touches two hotspots. Each touch is kept minimal:

- `entrypoint.sh` gets one conditional field in the existing `current-run.json` printf
  (lines 160–162 on `main`). It is the only harness-written channel that reaches the proxy
  once per run.
- `scripts/factory_core/model_proxy.py` gets: a path parameter on `read_current_run()`, one
  optional `create_app()` argument, the role binding and refusal that replace
  `ma.LOCAL_ROLE`, and two `check_response_model` calls placed next to the existing
  `check_json_body`/`check_sse_complete` calls.

These files are **not** touched: `scheduler.sh`, `scripts/provider_env.sh`,
`scripts/factory_core/providers/__init__.py`, `scripts/factory_core/run_record.py`, every
`gate_*`/verdict-gate script, `scripts/factory_core/verdict.py`,
`refinement-skills/VERIFIER-CONTRACT.md`, `workflows/archon-dark-factory.yaml`, `deploy/**`,
`run-compose.yml`, and `_NO_LOCAL_DISPATCH` in `model_endpoint.py`.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `scripts/factory_core/providers/role_isolation.py` | **Create** | `bind_dispatch_role`, `check_verdict_independence`, `_well_formed`. Stdlib-only: no env, disk or network access |
| `scripts/factory_core/providers/model_adapter.py` | Modify | Remove `LOCAL_ROLE`. `build_correlation(dest, run, role)`. New `check_response_model` |
| `scripts/factory_core/model_proxy.py` (hotspot) | Modify | `read_current_run(path=None)` returns `declared_role`. `create_app(..., current_run_path=None)`. Role binding and refusal in `handle_proxy`. Response-model calls |
| `entrypoint.sh` (hotspot) | Modify | Conditional `"role"` field in `current-run.json` |
| `tests/test_provider_role_isolation.py` | **Create** | Unit coverage: binding, independence proof, malformed records, authority-confinement guard |
| `tests/test_provider_model_adapter.py` | Modify | `build_correlation` gains a role argument. `check_response_model` unit tests |
| `tests/test_factory_model_proxy.py` | Modify | Role carrier and refusals through the proxy, default-path parity, named model-identity test, AC1 two-proxy test, AC5 fixture cases |
| `tests/test_entrypoint_current_run.sh` | Modify | Pointer stays byte-identical without the variable and carries `role` with it |
| `docs/configuration.md` | Modify | One row for `FACTORY_DISPATCH_ROLE` in the model-endpoint variable table |

Run every Python test with `PYTHONPATH=scripts`, the same way CI does. The test files also
insert `scripts/` into `sys.path` themselves.

---

## Task 1: `role_isolation.bind_dispatch_role`

**Files:**
- Create: `scripts/factory_core/providers/role_isolation.py`
- Create: `tests/test_provider_role_isolation.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_provider_role_isolation.py`:

```python
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
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_provider_role_isolation.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'factory_core.providers.role_isolation'`.

- [ ] **Step 3: Implement**

Create `scripts/factory_core/providers/role_isolation.py`:

```python
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
```

- [ ] **Step 4: Run them and confirm they pass**

Run: `PYTHONPATH=scripts python -m pytest tests/test_provider_role_isolation.py -v`
Expected: all tests pass (14 binding cases + 3).

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_core/providers/role_isolation.py tests/test_provider_role_isolation.py
git commit -m "feat(#559): role_isolation.bind_dispatch_role — harness-declared dispatch role"
```

---

## Task 2: `role_isolation.check_verdict_independence`

**Files:**
- Modify: `scripts/factory_core/providers/role_isolation.py`
- Modify: `tests/test_provider_role_isolation.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_provider_role_isolation.py`:

```python
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
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_provider_role_isolation.py -v -k "independ or malformed or hosting or issue or semantic or minor"`
Expected: FAIL with `AttributeError: module 'factory_core.providers.role_isolation' has no attribute 'check_verdict_independence'`.

- [ ] **Step 3: Implement**

Append to `scripts/factory_core/providers/role_isolation.py`:

```python
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
```

- [ ] **Step 4: Run them and confirm they pass**

Run: `PYTHONPATH=scripts python -m pytest tests/test_provider_role_isolation.py -v`
Expected: all pass. Task 1's authority-confinement guard still passes because the module
still has no imports.

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_core/providers/role_isolation.py tests/test_provider_role_isolation.py
git commit -m "feat(#559): check_verdict_independence — the §4.2 proof bound to one unit of work"
```

---

## Task 3: `model_adapter.check_response_model` (response-side model identity)

**Files:**
- Modify: `scripts/factory_core/providers/model_adapter.py`
- Modify: `tests/test_provider_model_adapter.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_provider_model_adapter.py`:

```python
# --- #559: response-side model identity (§4.11 decision 2) -------------------------------

def _sse_start(model_json):
    return (b'event: message_start\ndata: {"type":"message_start","message":' + model_json
            + b'}\n\nevent: message_stop\ndata: {"type":"message_stop"}\n\n')


@pytest.mark.parametrize("data,streamed", [
    (json.dumps({"type": "message", "model": SERVED}).encode(), False),
    (b'{"type":"message","usage":{}}', False),  # absent: request side already pinned identity
    (b"not json at all", False),
    (b"[1, 2]", False),
    (_sse_start(json.dumps({"model": SERVED}).encode()), True),
    (_sse_start(b'{"usage":{"input_tokens":1}}'), True),
    (b"event: ping\ndata: {not json}\n\n", True),
])
def test_response_model_matching_or_absent_passes(monkeypatch, data, streamed):
    ma.check_response_model(_dest(monkeypatch), data, streamed)


@pytest.mark.parametrize("data,streamed", [
    (b'{"type":"message","model":"claude-opus-5-5"}', False),  # a backend fallback to cloud
    (b'{"type":"message","model":"qwen2.5-coder-7b"}', False),  # a smaller local model
    (_sse_start(b'{"model":"claude-opus-5-5"}'), True),
])
def test_response_model_mismatch_is_refused_never_accepted(monkeypatch, data, streamed):
    refusal = _refusal(ma.check_response_model, _dest(monkeypatch), data, streamed)
    assert (refusal.status, refusal.failure_class) == (502, ma.PROTOCOL_INCOMPATIBILITY)
    assert SERVED in refusal.message
```

Then update the existing `test_build_correlation_envelope`. The role becomes an argument that
the caller resolves:

```python
def test_build_correlation_envelope(monkeypatch):
    dest = _dest(monkeypatch)
    run = {"run_id": "run-9", "issue_number": 558, "intent": "plan", "stage": "plan"}
    header, envelope = ma.build_correlation(dest, run, "actor")
    assert json.loads(header) == envelope
    assert set(envelope) == {"run_id", "issue", "phase", "role", "endpoint_id", "request_id"}
    assert envelope["run_id"] == "run-9" and envelope["issue"] == 558
    assert envelope["phase"] == "plan" and envelope["role"] == "actor"
    assert envelope["endpoint_id"] == "local-test"
    assert ma.build_correlation(dest, run, "actor")[1]["request_id"] != envelope["request_id"]
    # #559: the role is bound per dispatch by the caller, not a module constant.
    assert not hasattr(ma, "LOCAL_ROLE")
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_provider_model_adapter.py -v -k "response_model or build_correlation"`
Expected: FAIL. The `check_response_model` tests fail with `AttributeError: ... has no
attribute 'check_response_model'`. `test_build_correlation_envelope` fails with
`TypeError: build_correlation() takes 2 positional arguments but 3 were given`.

- [ ] **Step 3: Implement in `model_adapter.py`**

(a) Delete these two lines (currently lines 20–21):

```python
# §4.2: a local-routed container never hosts an independent checker — a contract constant.
LOCAL_ROLE = "actor"
```

(b) Replace `build_correlation` (currently lines 185–195) with:

```python
def build_correlation(dest, run, role) -> tuple[str, dict]:
    """§4.4's envelope from read_current_run()'s pointer fields and the dispatch role the
    caller bound (role_isolation.bind_dispatch_role, #559): (header value, envelope)."""
    envelope = {
        "run_id": run["run_id"],
        "issue": run["issue_number"],
        "phase": run["stage"],
        "role": role,
        "endpoint_id": dest["endpoint_id"],
        "request_id": str(uuid.uuid4()),
    }
    return json.dumps(envelope, separators=(",", ":")), envelope
```

(c) Insert after `check_sse_complete` (before `classify_failure`):

```python
def _response_model(data, streamed):
    """The model a local response says it is: a JSON body's top-level "model", or a stream's
    message_start event's message.model. None when absent or unparseable."""
    if not streamed:
        try:
            parsed = json.loads(data)
        except ValueError:
            return None
        return parsed.get("model") if isinstance(parsed, dict) else None
    for line in data.splitlines():
        if not line.startswith(b"data:"):
            continue
        try:
            event = json.loads(line[5:])
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "message_start":
            message = event.get("message")
            return message.get("model") if isinstance(message, dict) else None
    return None


def check_response_model(dest, data, streamed) -> None:
    """§4.11 decision 2, response side (#559): the model actually invoked is the served model.

    An absent field passes (check_model_identity pinned the request before it left); a
    different one — a backend fallback or a swapped model — is refused, never accepted.
    """
    model = _response_model(data, streamed)
    if model is not None and model != dest["served_name"]:
        raise AdapterRefusal(502, PROTOCOL_INCOMPATIBILITY,
                             f"local endpoint '{dest['endpoint_id']}' answered as model "
                             f"{model!r}, not its served model {dest['served_name']!r}; a "
                             "backend fallback is never accepted")
```

(d) Do **not** touch `model_proxy.py` in this task. It still references `ma.LOCAL_ROLE` and
the 2-argument `build_correlation`, and Task 4 fixes both. Running the proxy tests between
this commit and Task 4 would therefore fail, so this task runs only the adapter tests.

- [ ] **Step 4: Run them and confirm they pass**

Run: `PYTHONPATH=scripts python -m pytest tests/test_provider_model_adapter.py -v`
Expected: all pass, including the existing `test_adapter_module_does_not_import_aiohttp`.

- [ ] **Step 5: Commit together with Task 4**

Do not commit yet. Task 4 Step 6 commits the adapter and the proxy together, so no commit on
the branch has a broken `model_proxy.py`.

---

## Task 4: Declared-role carrier and binding in `model_proxy.py` (hotspot)

**Files:**
- Modify: `scripts/factory_core/model_proxy.py`
- Modify: `tests/test_factory_model_proxy.py`

- [ ] **Step 1: Write the failing tests**

(a) At the top of `tests/test_factory_model_proxy.py`, extend the imports after the existing
`model_endpoint` import:

```python
from factory_core import run_record as rr  # noqa: E402
from factory_core.providers import role_isolation as ri  # noqa: E402
```

(b) Append a new section at the **end** of the file. Every helper it uses (`local_env`,
`_local_scenario`, `_post_json`, `_ok_json`, `_rows`, `_url`, `_make_upstream`, `_recording`,
`_wait_for`, `_run`) is already defined above it:

```python
# --- #559: harness-declared dispatch role -------------------------------------------------

ISSUE = 559


def _pointer(path, run_id, *, issue=ISSUE, role=None):
    """A current-run.json as entrypoint.sh writes it; "role" only when the harness set one."""
    data = {"run_id": run_id, "issue_number": issue, "intent": "fix", "stage": "unknown",
            "started_at": "2026-11-25T00:00:00Z"}
    if role is not None:
        data["role"] = role
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _cloud_destination():
    """A selected cloud registry entry (non-null endpoint_id) — what a checker must select."""
    return ma.destination_from_entry(copy.deepcopy(CLOUD_ENTRY))


async def _post_cloud(client, headers=None):
    resp = await client.post("/v1/messages", json={"model": "claude-opus-5-5", "messages": []},
                             headers=headers or {})
    return resp.status, await resp.json()


def _cloud_scenario(local_env, monkeypatch, destination, send, *, current_run_path=None):
    """send(client) against a proxy with a cloud entry selected (or None: the default path);
    returns (send's result, cloud-upstream calls)."""
    calls = []

    async def scenario():
        upstream = await _make_upstream(_recording(_ok_json, calls))
        monkeypatch.setattr(mp, "UPSTREAM", _url(upstream))
        client = TestClient(TestServer(mp.create_app(destination,
                                                     current_run_path=current_run_path)))
        await client.start_server()
        try:
            result = await send(client)
            await _wait_for(lambda: len(_rows(local_env)) >= 1)
        finally:
            await client.close()
            await upstream.close()
        return result

    return _run(scenario()), calls


def test_read_current_run_carries_the_declared_role_from_a_given_path(tmp_path):
    assert mp.read_current_run(_pointer(tmp_path / "a.json", "run-a", role="checker")) == {
        "run_id": "run-a", "issue_number": ISSUE, "intent": "fix", "stage": "unknown",
        "declared_role": "checker"}
    assert mp.read_current_run(_pointer(tmp_path / "b.json", "run-b"))["declared_role"] is None
    assert mp.read_current_run(tmp_path / "missing.json") == {
        "run_id": "unknown", "issue_number": 0, "intent": "unknown", "stage": "unknown",
        "declared_role": None}


def test_read_current_run_defaults_to_the_module_pointer(tmp_path, monkeypatch):
    monkeypatch.setattr(mp, "CURRENT_RUN_PATH", _pointer(tmp_path / "c.json", "run-c"))
    assert mp.read_current_run()["run_id"] == "run-c"


def test_create_app_takes_a_per_app_pointer_path(tmp_path):
    assert mp.create_app()["current_run_path"] is None
    assert mp.create_app(current_run_path=tmp_path / "p.json")["current_run_path"] == (
        tmp_path / "p.json")


@pytest.mark.parametrize("declared,reason", [
    ("checker", "nested_checker_refused"),
    ("monitor", "nested_checker_refused"),
    ("verifier", "role_invalid"),
    ("invalid", "role_invalid"),  # what entrypoint.sh writes for a non-token value
])
def test_local_destination_refuses_a_non_actor_role_before_any_outbound_call(
        local_env, monkeypatch, declared, reason):
    _pointer(local_env / "current-run.json", "run-w", role=declared)
    (status, body), calls, cloud_calls = _local_scenario(local_env, monkeypatch, _ok_json,
                                                         _post_json)
    assert status == 403 and calls == [] and cloud_calls == []
    assert body["error"]["type"] == ma.REFUSAL_TYPE
    assert body["error"]["failure_class"] == ma.DESTINATION_POLICY_DENIAL
    assert body["error"]["endpoint_id"] == "local-test" and reason in body["error"]["message"]
    row = _rows(local_env)[0]
    assert row["failure_class"] == ma.DESTINATION_POLICY_DENIAL and row["status"] == 403
    # Never silently coerced to actor; nothing was signed.
    assert row["role"] is None and row["request_id"] is None


@pytest.mark.parametrize("declared", [None, "actor"])
def test_local_destination_records_actor_declared_or_defaulted(local_env, monkeypatch, declared):
    _pointer(local_env / "current-run.json", "run-w", role=declared)
    (status, _), calls, _ = _local_scenario(local_env, monkeypatch, _ok_json, _post_json)
    assert status == 200 and len(calls) == 1
    envelope = json.loads(calls[0]["headers"]["x-factory-correlation"])
    row = _rows(local_env)[0]
    assert row["role"] == envelope["role"] == "actor"
    assert row["request_id"] == envelope["request_id"]


@pytest.mark.parametrize("declared", ["verifier", "", "orchestrator", "invalid"])
def test_selected_cloud_entry_refuses_an_invalid_role_too(local_env, monkeypatch, declared):
    ptr = _pointer(local_env / "checker-run.json", "run-c", role=declared)
    (status, body), calls = _cloud_scenario(local_env, monkeypatch, _cloud_destination(),
                                            _post_cloud, current_run_path=ptr)
    assert status == 403 and calls == []
    assert body["error"]["failure_class"] == ma.DESTINATION_POLICY_DENIAL
    assert body["error"]["endpoint_id"] == "cloud-test"
    row = _rows(local_env)[0]
    assert row["role"] is None and row["failure_class"] == ma.DESTINATION_POLICY_DENIAL


def test_selected_cloud_entry_records_a_declared_checker(local_env, monkeypatch):
    ptr = _pointer(local_env / "checker-run.json", "run-c", role="checker")
    (status, _), calls = _cloud_scenario(local_env, monkeypatch, _cloud_destination(),
                                         _post_cloud, current_run_path=ptr)
    assert status == 200 and len(calls) == 1
    row = _rows(local_env)[0]
    assert (row["role"], row["endpoint_id"], row["hosting_location"]) == (
        "checker", "cloud-test", "cloud")


def test_selection_alone_grants_no_role_and_an_inbound_claim_is_ignored(local_env, monkeypatch):
    # Spec req. 6/7: an endpoint selected instance-wide, no declared role, and a model-side
    # X-Factory-Correlation claiming checker -> the cloud row records no role at all.
    ptr = _pointer(local_env / "checker-run.json", "run-c")
    (status, _), calls = _cloud_scenario(
        local_env, monkeypatch, _cloud_destination(),
        lambda c: _post_cloud(c, headers={"X-Factory-Correlation": '{"role":"checker"}'}),
        current_run_path=ptr)
    assert status == 200 and len(calls) == 1
    assert _rows(local_env)[0]["role"] is None


def test_default_path_never_consults_the_declared_role(local_env, monkeypatch):
    # FACTORY_MODEL_ENDPOINT unset: byte-identical to today, even with a role in the pointer.
    # Drop both the sys.modules entry and the package attribute: with only the former, a
    # lazy `from factory_core.providers import role_isolation` would reuse the attribute
    # without re-registering, and the final assertion could never fail.
    import factory_core.providers as providers_pkg
    monkeypatch.delitem(sys.modules, "factory_core.providers.role_isolation", raising=False)
    monkeypatch.delattr(providers_pkg, "role_isolation", raising=False)
    _pointer(local_env / "current-run.json", "run-d", role="checker")
    (status, _), calls = _cloud_scenario(
        local_env, monkeypatch, None,
        lambda c: _post_cloud(c, headers={"X-Factory-Correlation": "client-value"}))
    assert status == 200 and len(calls) == 1
    assert calls[0]["headers"]["x-factory-correlation"] == "client-value"
    row = _rows(local_env)[0]
    assert (row["role"], row["endpoint_id"], row["hosting_location"], row["request_id"]) == (
        None, None, "cloud", None)
    assert "factory_core.providers.role_isolation" not in sys.modules
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py -v -k "declared or pointer or role or selection"`
Expected: FAIL. The cause is either `TypeError: read_current_run() takes 0 positional
arguments but 1 was given` or `create_app() got an unexpected keyword argument
'current_run_path'`. Local-path tests fail with `AttributeError: ... has no attribute
'LOCAL_ROLE'` because Task 3 removed it.

- [ ] **Step 3: Implement `read_current_run(path=None)`**

Replace `read_current_run` (currently `model_proxy.py:79–96`) with:

```python
def read_current_run(path=None) -> dict:
    """Best-effort correlation read — never raises, defaults to unknown.

    `stage` is written by entrypoint.sh from INTENT (see Task 7): exact for
    single-phase intents (refine/plan/deconflict/close/fix-main/recheck), "unknown"
    for multi-phase intents (fix/continue) where no investigated Archon data source
    exposes per-node timing — see the plan's Architecture "Stage attribution" note.
    `declared_role` is the dispatching harness's FACTORY_DISPATCH_ROLE, present only when
    set (#559) — pointer-grade, consulted only when an endpoint is selected. `path` defaults
    to CURRENT_RUN_PATH; create_app() passes a per-app one.
    """
    try:
        data = json.loads((CURRENT_RUN_PATH if path is None else path).read_text(encoding="utf-8"))
        return {
            "run_id": data.get("run_id") or "unknown",
            "issue_number": int(data.get("issue_number") or 0),
            "intent": data.get("intent") or "unknown",
            "stage": data.get("stage") or "unknown",
            "declared_role": data.get("role"),
        }
    except Exception:
        return {"run_id": "unknown", "issue_number": 0, "intent": "unknown", "stage": "unknown",
                "declared_role": None}
```

- [ ] **Step 4: Implement `create_app(destination=None, current_run_path=None)`**

In `create_app` (currently line 522), change the signature and store the path beside
`local_destination`:

```python
def create_app(destination=None, current_run_path=None) -> web.Application:
    app = web.Application()
    app["client_session"] = None
    # The endpoint resolved once at startup (#558); None on the cloud-default path.
    app["local_destination"] = destination
    # This app's correlation pointer (#559); None reads the module-level CURRENT_RUN_PATH.
    app["current_run_path"] = current_run_path
    app.router.add_route("*", "/{tail:.*}", handle_proxy)
```

The rest of `create_app` and `main()` stay as they are. `main()` keeps calling
`create_app(destination)`.

- [ ] **Step 5: Implement the role binding and refusal in `handle_proxy`**

(a) Replace the block from `correlation = read_current_run()` through the end of the
`columns = {...}` dict (currently lines 335–351) with:

```python
    correlation = read_current_run(request.app["current_run_path"])
    dest = request.app["local_destination"]
    local = dest is not None and dest["hosting_location"] == "local"
    ma = None
    role = role_reason = None
    role_refused = False
    if dest is not None:
        # Lazy, as in main(): the default cloud path never loads the adapter (#558) or the
        # role binding (#559), so a pointer's declared role is never consulted there.
        from factory_core.providers import model_adapter as ma
        from factory_core.providers import role_isolation
        role, role_reason = role_isolation.bind_dispatch_role(
            dest["hosting_location"], correlation["declared_role"])
        role_refused = role_reason in role_isolation.REFUSALS
    # Request/Result ledger columns (#558). role is the bound dispatch role (#559): actor on
    # every local row, adapter-refused rows included; the declared role (or None) on a selected
    # cloud entry; None on a role refusal and on the default path. request_id exists once a
    # request is signed.
    columns = {
        "endpoint_id": dest["endpoint_id"] if dest else None,
        "hosting_location": dest["hosting_location"] if dest else "cloud",
        "role": role,
        "request_id": None,
        "correlation": _correlation_state(correlation),
        "payload_decision": dict(dest["payload_decision"]) if dest else None,
    }
```

(b) Immediately after the nested `_row` function and before `if local:` (currently line 367),
insert:

```python
    if role_refused:
        # #559: a declared role this destination may not carry — refused before any outbound
        # call, on a local and a selected cloud entry alike; never coerced to another role.
        message = (f"dispatch role refused on {dest['hosting_location']} endpoint "
                   f"'{dest['endpoint_id']}': {role_reason}")
        await _persist_ledger(_row(403, ma.DESTINATION_POLICY_DENIAL))
        return web.json_response(
            _error_body(ma.REFUSAL_TYPE, message, ma.DESTINATION_POLICY_DENIAL,
                        dest["endpoint_id"]),
            status=403)
```

(c) In the `if local:` block, change the `build_correlation` call (currently line 378) to pass
the bound role:

```python
        header_value, envelope = ma.build_correlation(dest, correlation, role)
```

Leave the next line (`columns["role"], columns["request_id"] = envelope["role"],
envelope["request_id"]`) unchanged.

- [ ] **Step 6: Run the proxy and adapter suites, then commit Tasks 3+4**

Run: `PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py tests/test_provider_model_adapter.py tests/test_provider_role_isolation.py -v`
Expected: all pass. Two existing tests must stay green without edits:
`test_local_model_identity_mismatch_is_refused_403` (a refused local row still records
`role == "actor"`) and
`test_local_request_reaches_only_the_entry_base_url_with_the_scoped_credential` (an inbound
`X-Factory-Correlation` claiming `checker` is dropped and the envelope says `actor`).

Then: `grep -rn "LOCAL_ROLE" scripts/ entrypoint.sh`
Expected: no output. (`tests/` is deliberately excluded: Task 3's
`assert not hasattr(ma, "LOCAL_ROLE")` guard names the constant and must stay.)

```bash
git add scripts/factory_core/providers/model_adapter.py scripts/factory_core/model_proxy.py \
        tests/test_provider_model_adapter.py tests/test_factory_model_proxy.py
git commit -m "feat(#559): bind the harness-declared dispatch role in the proxy; check_response_model

Replaces the hardcoded LOCAL_ROLE with role_isolation.bind_dispatch_role on the
selected-endpoint path only (default path never consults it). Non-actor roles on a
local entry and invalid roles on any selected entry are refused 403 before any
outbound call. Hotspot: model_proxy.py (human signature hold)."
```

---

## Task 5: Wire `check_response_model` and the named model-identity test

**Files:**
- Modify: `scripts/factory_core/model_proxy.py`
- Modify: `tests/test_factory_model_proxy.py`

- [ ] **Step 1: Write the failing tests**

Append to the #559 section of `tests/test_factory_model_proxy.py`:

```python
async def _redirect_elsewhere(request):
    if request.path == "/elsewhere":
        return web.json_response({"usage": {}})
    raise web.HTTPFound("/elsewhere")


def _answering_as(model):
    async def handler(request):
        return web.json_response({"type": "message", "model": model,
                                  "usage": {"input_tokens": 1, "output_tokens": 1}})
    return handler


@pytest.mark.parametrize("model,handler,status,failure_class,upstream_calls", [
    # Request side — pins #558's check_model_identity (model_adapter.py) through the proxy.
    pytest.param("claude-opus-5-5", _ok_json, 403, ma.DESTINATION_POLICY_DENIAL, 0,
                 id="literal-cloud-model-id"),
    pytest.param("opus", _ok_json, 403, ma.DESTINATION_POLICY_DENIAL, 0, id="alias"),
    pytest.param("qwen2.5-coder-7b", _ok_json, 403, ma.DESTINATION_POLICY_DENIAL, 0,
                 id="alias-mismatch-other-local-model"),
    # Transport — pins #558's check_response_status: a redirect is refused, never followed.
    pytest.param(SERVED, _redirect_elsewhere, 502, ma.PROTOCOL_INCOMPATIBILITY, 1,
                 id="redirect"),
    # Response side — new in #559: what the endpoint actually served is checked.
    pytest.param(SERVED, _answering_as("claude-opus-5-5"), 502, ma.PROTOCOL_INCOMPATIBILITY, 1,
                 id="upstream-model-mismatch-backend-fallback"),
    pytest.param(SERVED, _answering_as("qwen2.5-coder-7b"), 502, ma.PROTOCOL_INCOMPATIBILITY,
                 1, id="upstream-model-mismatch-swapped-model"),
])
def test_model_identity_policy_model_is_the_model_actually_invoked(
        local_env, monkeypatch, model, handler, status, failure_class, upstream_calls):
    async def send(client):
        resp = await client.post("/v1/messages", json={"model": model, "messages": []})
        return resp.status, await resp.json()

    (got, body), calls, cloud_calls = _local_scenario(local_env, monkeypatch, handler, send)
    assert got == status and cloud_calls == [] and len(calls) == upstream_calls
    assert body["error"]["type"] == ma.REFUSAL_TYPE
    assert body["error"]["failure_class"] == failure_class
    row = _rows(local_env)[0]
    assert row["failure_class"] == failure_class and row["model"] == model


def test_upstream_answering_as_the_served_model_passes(local_env, monkeypatch):
    (status, body), calls, _ = _local_scenario(local_env, monkeypatch, _answering_as(SERVED),
                                               _post_json)
    assert status == 200 and body["model"] == SERVED and len(calls) == 1
    assert _rows(local_env)[0]["failure_class"] is None


SSE_WRONG_MODEL = SSE_OK.replace(b'"message":{"usage"',
                                 b'"message":{"model":"claude-opus-5-5","usage"')


async def _sse_wrong_model(request):
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await resp.prepare(request)
    await resp.write(SSE_WRONG_MODEL)
    await resp.write_eof()
    return resp


def test_streamed_model_mismatch_is_aborted_and_fails_the_run(local_env, monkeypatch):
    # Chunks are already forwarded when the check can run: this is not a pre-forward refusal.
    # The proxy withholds the clean EOF (like check_sse_complete) and records the class, which
    # makes the run's model_dispatch outcome "failed".
    assert SSE_WRONG_MODEL != SSE_OK
    _pointer(local_env / "current-run.json", "run-s")
    _local_scenario(local_env, monkeypatch, _sse_wrong_model,
                    lambda c: _expect_truncated(c, stream=True))
    rows, _ = rr._read_ledger_rows("run-s", local_env / "ledger.jsonl")
    assert rows[0]["failure_class"] == ma.PROTOCOL_INCOMPATIBILITY
    assert rr._compute_model_dispatch(rows)["result"]["outcome"] == "failed"
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py -v -k "model_identity_policy or served_model_passes or streamed_model_mismatch"`
Expected: the two `upstream-model-mismatch-*` cases fail with `assert 200 == 502`.
`test_streamed_model_mismatch_is_aborted_and_fails_the_run` fails with `DID NOT RAISE`. The
request-side and redirect cases already pass: they are characterization pins of #558.

- [ ] **Step 3: Implement the two call sites in `handle_proxy`**

(a) Non-streaming. Replace the existing two-line `ma.check_json_body(...)` call (currently
lines 433–434) with:

```python
                    scan = _decode_scan(bytes(usage_scan), encoding)
                    ma.check_json_body(upstream_resp.status, scan)
                    # #559: refused before prepare(), so the client gets a real error body.
                    ma.check_response_model(dest, scan, streamed=False)
```

(b) Streaming. Replace the existing `if local: ma.check_sse_complete(...)` block after the
stream loop (currently lines 448–450) with:

```python
                    if local:
                        scan = _decode_scan(bytes(usage_scan), encoding)
                        ma.check_sse_complete(upstream_resp.status, scan)
                        # #559: already forwarded — a mismatch aborts before EOF and is ledgered.
                        ma.check_response_model(dest, scan, streamed=True)
```

Both calls raise `ma.AdapterRefusal`. That exception is already in `handled` on the local
path, so the existing `except handled` branch does the rest.

The check deliberately runs on every status, unlike `check_json_body`. This keeps the spec's
`(dest, data, streamed)` signature. A non-2xx local body naming a *different* model fails
closed as a 502 `protocol_incompatibility` rather than being forwarded. Not-ready bodies
(`{"error": ...}`) carry no top-level `model`, so they pass unchanged. It returns the 502 JSON refusal
when the response is not yet prepared. When it is prepared, it aborts the transport and
records the `protocol_incompatibility` row. No new error handling is needed.

- [ ] **Step 4: Run the proxy suite and confirm it passes**

Run: `PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py -v`
Expected: all pass. The existing local tests' upstream bodies (`_ok_json`, `SSE_OK`) carry no
`model` field, so `check_response_model` passes them.

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_core/model_proxy.py tests/test_factory_model_proxy.py
git commit -m "feat(#559): refuse a local response that names a model other than served_name

Named test pins #558's literal-id/alias/redirect refusals through the proxy and
adds the upstream-model-mismatch (backend fallback) case. Hotspot: model_proxy.py."
```

---

## Task 6: Acceptance criterion 1 — two real proxy instances, concurrently

**Files:**
- Modify: `tests/test_factory_model_proxy.py`

There are no production changes in this task. Tasks 1–5 already provide every piece. The test
is expected to pass on the first run. Step 2 deliberately breaks the code to prove the test
can fail (mutation check).

- [ ] **Step 1: Write the test**

Append to the #559 section:

```python
CLOUD_SENTINEL = "sk-ant-sentinel-cloud-checker-key"


def test_ac1_local_worker_and_cloud_checker_dispatches_are_isolated_and_attributed(
        local_env, monkeypatch):
    """#559 acceptance criterion 1. Two real create_app() instances run concurrently in one
    process: a local worker against a fake local upstream, and a cloud checker that selected a
    cloud registry entry against a fake cloud upstream. Each has its own current-run.json.
    Both dispatch records are built from ledger rows by run_record's production aggregation.

    Scope note (spec): one process and one OS environment, so this proves header, ledger and
    artifact non-leakage plus the worker proxy's startup refusal to hold a cloud credential.
    Separate-process isolation is structural (one dispatched container, one endpoint config)
    and no in-process test can simulate it.
    """
    monkeypatch.setattr(mp, "RAW_ARTIFACT_CAPTURE", True)
    artifacts = local_env / "artifacts"
    monkeypatch.setattr(mp, "RAW_ARTIFACT_DIR", artifacts)
    worker_ptr = _pointer(local_env / "worker-run.json", "run-worker")  # no role: actor
    checker_ptr = _pointer(local_env / "checker-run.json", "run-checker", role="checker")
    local_calls, cloud_calls = [], []

    async def scenario():
        local_up = await _make_upstream(_recording(_ok_json, local_calls))
        cloud_up = await _make_upstream(_recording(_ok_json, cloud_calls))
        monkeypatch.setattr(mp, "UPSTREAM", _url(cloud_up))
        worker = TestClient(TestServer(mp.create_app(_local_destination(_url(local_up)),
                                                     current_run_path=worker_ptr)))
        checker = TestClient(TestServer(mp.create_app(_cloud_destination(),
                                                      current_run_path=checker_ptr)))
        await worker.start_server()
        await checker.start_server()
        try:
            results = await asyncio.gather(
                _post_json(worker),
                _post_cloud(checker, headers={"x-api-key": CLOUD_SENTINEL}))
            await _wait_for(lambda: len(_rows(local_env)) >= 2 and artifacts.exists()
                            and len(list(artifacts.rglob("*.json"))) >= 2)
        finally:
            for c in (worker, checker):
                await c.close()
            for s in (local_up, cloud_up):
                await s.close()
        return results

    (w_status, _), (c_status, _) = _run(scenario())
    assert w_status == 200 and c_status == 200
    assert len(local_calls) == 1 and len(cloud_calls) == 1

    ledger = local_env / "ledger.jsonl"
    worker_rows, _ = rr._read_ledger_rows("run-worker", ledger)
    checker_rows, _ = rr._read_ledger_rows("run-checker", ledger)
    assert len(worker_rows) == 1 and len(checker_rows) == 1
    w, c = worker_rows[0], checker_rows[0]

    # Exact role / request / endpoint attribution, per request.
    envelope = json.loads(local_calls[0]["headers"]["x-factory-correlation"])
    assert (w["role"], w["endpoint_id"], w["hosting_location"], w["issue_number"]) == (
        "actor", "local-test", "local", ISSUE)
    assert w["request_id"] and w["request_id"] == envelope["request_id"]
    assert envelope["role"] == "actor" and envelope["run_id"] == "run-worker"
    assert (c["role"], c["endpoint_id"], c["hosting_location"], c["issue_number"]) == (
        "checker", "cloud-test", "cloud", ISSUE)
    assert c["request_id"] is None  # the cloud path signs nothing, so it mints none (#558)
    assert w["correlation"] == c["correlation"] == "inferred_pointer"

    # The independence proof over the real, ledger-derived dispatch records.
    worker_result = rr._compute_model_dispatch(worker_rows)["result"]
    checker_result = rr._compute_model_dispatch(checker_rows)["result"]
    assert ri.check_verdict_independence(
        worker_result, checker_result, actor_issue=w["issue_number"],
        producer_issue=c["issue_number"], verdict_issue=ISSUE) == (True, "independent")
    # An unrelated (other-issue) successful cloud run is never the proof.
    assert ri.check_verdict_independence(
        worker_result, checker_result, actor_issue=ISSUE, producer_issue=ISSUE + 1,
        verdict_issue=ISSUE) == (False, "unit_of_work_mismatch")

    # No cross-role credential leakage: forwarded headers, ledger rows, raw captures.
    assert local_calls[0]["headers"]["x-api-key"] == SENTINEL
    assert cloud_calls[0]["headers"]["x-api-key"] == CLOUD_SENTINEL
    assert CLOUD_SENTINEL not in json.dumps(local_calls[0]["headers"])
    assert SENTINEL not in json.dumps(cloud_calls[0]["headers"])
    ledger_text = ledger.read_text()
    assert SENTINEL not in ledger_text and CLOUD_SENTINEL not in ledger_text
    captured = [p.read_text() for p in artifacts.rglob("*.json")]
    assert {p.parent.name for p in artifacts.rglob("*.json")} == {"run-worker", "run-checker"}
    for text in captured:
        assert SENTINEL not in text and CLOUD_SENTINEL not in text

    # Process environment: the worker-side proxy holds no cloud credential, and refuses to
    # start if handed the checker's (startup check, contract §4.3) without echoing it.
    assert CLOUD_SENTINEL not in os.environ.values()
    with pytest.raises(ma.DestinationError) as exc:
        ma.destination_from_entry(  # LOCAL_ENTRY's base_url is never dialled: startup only
            copy.deepcopy(LOCAL_ENTRY),
            environ={"LOCAL_GATEWAY_TOKEN": SENTINEL, "ANTHROPIC_API_KEY": CLOUD_SENTINEL})
    assert "ANTHROPIC_API_KEY" in str(exc.value) and CLOUD_SENTINEL not in str(exc.value)
```

- [ ] **Step 2: Run it, then do a mutation check**

Run: `PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py -v -k ac1`
Expected: PASS.

Mutation check (do **not** commit this): edit by hand, not with a global sed. The literal
`"role": role,` also appears in `build_ledger_row`. In the `columns = {...}` dict inside
`handle_proxy` **only**, temporarily change `"role": role,` to `"role": None,`, then re-run
the same command with bytecode caching off, so a same-size edit can't reuse a stale `.pyc`:
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py -v -k ac1 -p no:cacheprovider`.
Expected: FAIL at `assert (c["role"], ...) == ("checker", ...)`. Revert the change
(`git checkout scripts/factory_core/model_proxy.py`) and re-run. Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_factory_model_proxy.py
git commit -m "test(#559): AC1 — concurrent local-worker and cloud-checker proxies, isolated and attributed"
```

---

## Task 7: Acceptance criterion 5 — fixture cases never convert failure to PASS

**Files:**
- Modify: `tests/test_factory_model_proxy.py`

These tests use real proxy runs. They need no production changes, because Tasks 1–5 already
produce the failure classes that void the proof. Step 2 is a mutation check.

- [ ] **Step 1: Write the tests**

Append to the #559 section:

```python
async def _malformed_worker_json(request):
    return web.Response(body=b'{"type":"message","usage":{"input_tok',
                        content_type="application/json")


async def _malformed_worker_stream(request):
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await resp.prepare(request)
    await resp.write(b'event: content_block_delta\ndata: {"type":"content_bl')
    await resp.write_eof()  # a clean upstream EOF after a partial frame
    return resp


def _worker_dispatch(local_env, monkeypatch, handler, *, stream=False, base_url=None):
    """One real local-worker request (run-worker, issue 559); its model_dispatch result."""
    _pointer(local_env / "current-run.json", "run-worker")

    async def scenario():
        upstream = await _make_upstream(handler)
        client = await _local_client(base_url or _url(upstream))
        try:
            try:
                resp = await client.post("/v1/messages",
                                         json={"model": SERVED, "stream": stream, "messages": []})
                await resp.read()
            except _TRUNCATED:
                pass  # an aborted stream is the client's view of a refused one
            await _wait_for(lambda: len(_rows(local_env)) >= 1)
        finally:
            await client.close()
            await upstream.close()

    _run(scenario())
    rows, _ = rr._read_ledger_rows("run-worker", local_env / "ledger.jsonl")
    return rr._compute_model_dispatch(rows)["result"]


def _checker_dispatch(local_env, monkeypatch, destination, *, role="checker", pointer=True):
    """One real checker request; (its row's issue_number, its model_dispatch result)."""
    path = local_env / "checker-run.json"
    if pointer:
        _pointer(path, "run-checker", role=role)
    before = len(_rows(local_env))

    async def scenario():
        upstream = await _make_upstream(_ok_json)
        monkeypatch.setattr(mp, "UPSTREAM", _url(upstream))
        client = TestClient(TestServer(mp.create_app(destination, current_run_path=path)))
        await client.start_server()
        try:
            await _post_cloud(client)
            await _wait_for(lambda: len(_rows(local_env)) > before)
        finally:
            await client.close()
            await upstream.close()

    _run(scenario())
    rows, _ = rr._read_ledger_rows("run-checker" if pointer else "unknown",
                                   local_env / "ledger.jsonl")
    return rows[0]["issue_number"], rr._compute_model_dispatch(rows)["result"]


@pytest.mark.parametrize("worker_kwargs,failure_class", [
    pytest.param({"handler": _ok_json, "base_url": "http://127.0.0.1:1"},
                 ma.ENDPOINT_UNREACHABLE, id="local-endpoint-failure"),
    pytest.param({"handler": _malformed_worker_json}, ma.PROTOCOL_INCOMPATIBILITY,
                 id="malformed-worker-output-json"),
    pytest.param({"handler": _malformed_worker_stream, "stream": True},
                 ma.PROTOCOL_INCOMPATIBILITY, id="malformed-worker-output-stream"),
    pytest.param({"handler": _answering_as("claude-opus-5-5")}, ma.PROTOCOL_INCOMPATIBILITY,
                 id="worker-answered-as-another-model"),
])
def test_ac5_failed_or_malformed_worker_output_never_proves_independence(
        local_env, monkeypatch, worker_kwargs, failure_class):
    worker = _worker_dispatch(local_env, monkeypatch, **worker_kwargs)
    assert worker["outcome"] == "failed" and failure_class in worker["failure_classes_seen"]
    issue, checker = _checker_dispatch(local_env, monkeypatch, _cloud_destination())
    assert checker["outcome"] == "ok" and checker["role"] == "checker"
    assert ri.check_verdict_independence(
        worker, checker, actor_issue=ISSUE, producer_issue=issue, verdict_issue=ISSUE) == (
        False, "dispatch_outcome_not_ok")


@pytest.mark.parametrize("destination,kwargs,reason", [
    # A default cloud run (no endpoint selected) records endpoint_id None and role None.
    pytest.param(None, {}, "producer_endpoint_missing", id="missing-checker-endpoint"),
    # No pointer: correlation "unknown", issue 0 — never the proof (Invariant 3, §4.4).
    pytest.param("cloud", {"pointer": False}, "unit_of_work_mismatch", id="no-pointer"),
    # Endpoint selected but no declared role: selection alone never makes a checker.
    pytest.param("cloud", {"role": None}, "producer_role_not_checker", id="undeclared-role"),
])
def test_ac5_a_checker_dispatch_without_a_provable_identity_is_void(
        local_env, monkeypatch, destination, kwargs, reason):
    worker = _worker_dispatch(local_env, monkeypatch, _ok_json)
    assert worker["outcome"] == "ok" and worker["role"] == "actor"
    dest = _cloud_destination() if destination == "cloud" else None
    issue, checker = _checker_dispatch(local_env, monkeypatch, dest, **kwargs)
    if kwargs.get("pointer") is False:
        assert checker["correlation"] == "unknown"
    assert ri.check_verdict_independence(
        worker, checker, actor_issue=ISSUE, producer_issue=issue, verdict_issue=ISSUE) == (
        False, reason)
```

Unauthorized role overrides, the remaining AC5 case, are covered by Task 4's
`test_local_destination_refuses_a_non_actor_role_before_any_outbound_call` and
`test_selected_cloud_entry_refuses_an_invalid_role_too`.

- [ ] **Step 2: Run them, then do a mutation check**

Run: `PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py -v -k ac5`
Expected: 7 passed.

Mutation check (do **not** commit this): in `role_isolation.check_verdict_independence`,
temporarily delete the `dispatch_outcome_not_ok` branch and re-run with
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py -v -k ac5`.
Expected: the four `test_ac5_failed_or_malformed...` cases FAIL. Revert
(`git checkout scripts/factory_core/providers/role_isolation.py`) and re-run. Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_factory_model_proxy.py
git commit -m "test(#559): AC5 — local failure, malformed worker output, missing checker endpoint are void"
```

---

## Task 8: `entrypoint.sh` carries `FACTORY_DISPATCH_ROLE` into the pointer (hotspot)

**Files:**
- Modify: `entrypoint.sh`
- Modify: `tests/test_entrypoint_current_run.sh`
- Modify: `docs/configuration.md`

- [ ] **Step 1: Write the failing shell assertions**

In `tests/test_entrypoint_current_run.sh`:

(a) Just after the `stage_of() { ... }` helper, add:

```bash
keys_of() {
  python3 -c "import json; print(','.join(sorted(json.load(open('$CURRENT_RUN_DIR/current-run.json')))))" 2>/dev/null
}
role_of() {
  python3 -c "import json; print(json.load(open('$CURRENT_RUN_DIR/current-run.json')).get('role','missing'))" 2>/dev/null
}
BASE_KEYS="intent,issue_number,run_id,stage,started_at"
unset FACTORY_DISPATCH_ROLE
```

(b) Immediately after Case 2's `assert_true "multi-phase intent 'fix' -> stage='unknown'" ...`
line, and before the `# --- #196:` section, insert:

```bash
# --- #559: FACTORY_DISPATCH_ROLE folds into current-run.json only when non-empty ---
KEYS_FIELD=$(keys_of)
assert_true "#559 FACTORY_DISPATCH_ROLE unset -> pointer key set unchanged (no role)" "[ '$KEYS_FIELD' = '$BASE_KEYS' ]"

set -uo pipefail
ARTIFACTS_DIR=$(mktemp -d /tmp/208-artifacts-XXXXXX)
export ARTIFACTS_DIR
FACTORY_DISPATCH_ROLE="" ENTRYPOINT_SOURCE_ONLY=1 ARGUMENTS="Plan issue #559" \
  source "$SCRIPT_DIR/../entrypoint.sh" "Plan issue #559"
trap - ERR
set +e; set +u; set +o pipefail
KEYS_FIELD=$(keys_of)
assert_true "#559 FACTORY_DISPATCH_ROLE empty -> pointer key set unchanged (no role)" "[ '$KEYS_FIELD' = '$BASE_KEYS' ]"
rm -rf "$ARTIFACTS_DIR"

set -uo pipefail
ARTIFACTS_DIR=$(mktemp -d /tmp/208-artifacts-XXXXXX)
export ARTIFACTS_DIR
FACTORY_DISPATCH_ROLE="checker" ENTRYPOINT_SOURCE_ONLY=1 ARGUMENTS="Plan issue #559" \
  source "$SCRIPT_DIR/../entrypoint.sh" "Plan issue #559"
trap - ERR
set +e; set +u; set +o pipefail
ROLE_FIELD_VALUE=$(role_of)
KEYS_FIELD=$(keys_of)
ISSUE_FIELD=$(python3 -c "import json; print(json.load(open('$CURRENT_RUN_DIR/current-run.json')).get('issue_number','missing'))" 2>/dev/null)
assert_true "#559 FACTORY_DISPATCH_ROLE=checker -> role='checker'" "[ '$ROLE_FIELD_VALUE' = 'checker' ]"
assert_true "#559 role pointer still parses with every base key" "[ '$KEYS_FIELD' = 'intent,issue_number,role,run_id,stage,started_at' ]"
assert_true "#559 role pointer keeps issue_number" "[ '$ISSUE_FIELD' = '559' ]"
rm -rf "$ARTIFACTS_DIR"

# A value that is not a bare lowercase token is recorded as "invalid": it can never add or
# override another pointer field (here it tries to replace issue_number).
set -uo pipefail
ARTIFACTS_DIR=$(mktemp -d /tmp/208-artifacts-XXXXXX)
export ARTIFACTS_DIR
FACTORY_DISPATCH_ROLE='actor","issue_number":1,"x":"' ENTRYPOINT_SOURCE_ONLY=1 ARGUMENTS="Plan issue #559" \
  source "$SCRIPT_DIR/../entrypoint.sh" "Plan issue #559"
trap - ERR
set +e; set +u; set +o pipefail
ROLE_FIELD_VALUE=$(role_of)
KEYS_FIELD=$(keys_of)
ISSUE_FIELD=$(python3 -c "import json; print(json.load(open('$CURRENT_RUN_DIR/current-run.json')).get('issue_number','missing'))" 2>/dev/null)
assert_true "#559 non-token FACTORY_DISPATCH_ROLE -> role='invalid'" "[ '$ROLE_FIELD_VALUE' = 'invalid' ]"
assert_true "#559 non-token role adds no pointer field" "[ '$KEYS_FIELD' = 'intent,issue_number,role,run_id,stage,started_at' ]"
assert_true "#559 non-token role cannot override issue_number" "[ '$ISSUE_FIELD' = '559' ]"
unset FACTORY_DISPATCH_ROLE
rm -rf "$ARTIFACTS_DIR"
```

The `FACTORY_DISPATCH_ROLE=... source ...` prefix assignment does not persist after `source`
in bash, and the explicit `unset` makes sure the later #196 cases are unaffected either way.

- [ ] **Step 2: Run it and confirm it fails**

Run: `bash tests/test_entrypoint_current_run.sh`
Expected: four `FAIL` lines. `#559 FACTORY_DISPATCH_ROLE=checker -> role='checker'` (it prints
`missing`) and the key-set assertion that follows it fail, and so do
`#559 non-token FACTORY_DISPATCH_ROLE -> role='invalid'` and its key-set assertion. The
`unset`/`empty` cases and both `issue_number` assertions pass already. The script exits
non-zero.

- [ ] **Step 3: Implement in `entrypoint.sh`**

Replace the printf block (currently lines 160–162):

```bash
printf '{"run_id":"%s","issue_number":%s,"intent":"%s","stage":"%s","started_at":"%s"}\n' \
  "$RUN_ID" "${ISSUE_NUM:-0}" "${INTENT:-unknown}" "$RUN_STAGE" "$RUN_STARTED_AT" \
  > "$CURRENT_RUN_DIR/current-run.json" 2>/dev/null || true
```

with:

```bash
# #559: the dispatching harness's declared role, folded in only when set, so a pointer
# without one is byte-identical. Pointer-grade (this container can write the file): the
# proxy uses it only to narrow what a selected endpoint may do, never to grant authority.
ROLE_FIELD=""
if [ -n "${FACTORY_DISPATCH_ROLE:-}" ]; then
  # Only a bare lowercase token is folded in; anything else becomes "invalid", which the
  # proxy refuses (role_invalid) — it can never add or override another pointer field.
  case "$FACTORY_DISPATCH_ROLE" in
    *[!a-z_]*) ROLE_FIELD=',"role":"invalid"' ;;
    *) ROLE_FIELD=",\"role\":\"${FACTORY_DISPATCH_ROLE}\"" ;;
  esac
fi
printf '{"run_id":"%s","issue_number":%s,"intent":"%s","stage":"%s","started_at":"%s"%s}\n' \
  "$RUN_ID" "${ISSUE_NUM:-0}" "${INTENT:-unknown}" "$RUN_STAGE" "$RUN_STARTED_AT" "$ROLE_FIELD" \
  > "$CURRENT_RUN_DIR/current-run.json" 2>/dev/null || true
```

(This is the spec's guard. Only a bare lowercase token (`a`–`z` and `_`) is written verbatim.
Any other value, including one carrying quotes, commas or a newline, is written as the literal
`invalid`. The proxy refuses that as `role_invalid` when an endpoint is selected, so a hostile
value can never add or override another pointer field such as `issue_number`.)

- [ ] **Step 4: Run it and confirm it passes**

Run: `bash tests/test_entrypoint_current_run.sh`
Expected: final line `Results: N passed, 0 failed` (exit 0). The new #559 assertions show as
`PASS`.

- [ ] **Step 5: Document the variable**

In `docs/configuration.md`, find the model-endpoint variable table (columns `Variable`,
`Set where`, `Read by`, `Notes`; it lists `FACTORY_MODEL_ENDPOINT`). Insert this row directly
after the `FACTORY_MODEL_REGISTRY_SHA256` row:

```
| `FACTORY_DISPATCH_ROLE` | nowhere yet; the first dispatch that needs a non-default role forwards it with `docker compose run -e` | `entrypoint.sh`, which folds it into the `current-run.json` pointer as `role`; the model proxy, only when `FACTORY_MODEL_ENDPOINT` is set (#559) | per-dispatch; not in `PROVIDER_PASSTHROUGH_VARS`. Unset or empty leaves the pointer unchanged. A value that is not a bare lowercase token is recorded as `invalid`, which the proxy refuses (`role_invalid`) |
```

Run: `python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md`
Expected: `Docs reference check passed for 4 doc(s).`

- [ ] **Step 6: Commit**

```bash
git add entrypoint.sh tests/test_entrypoint_current_run.sh docs/configuration.md
git commit -m "feat(#559): entrypoint folds FACTORY_DISPATCH_ROLE into current-run.json when set

Hotspot: entrypoint.sh (human signature hold) — one conditional field; the pointer
is byte-identical when the variable is unset or empty. Nothing sets it yet."
```

---

## Task 9: Full verification and scope check

**Files:** none modified.

- [ ] **Step 1: Run the full Python suite**

Run: `PYTHONPATH=scripts python -m pytest tests/ -v`
Expected: all pass, with no new failures compared with `main`.

- [ ] **Step 2: Run the CI shell test touched by this change**

Run: `bash tests/test_entrypoint_current_run.sh`
Expected: `Results: N passed, 0 failed`.

- [ ] **Step 3: Scope check — only the planned files changed**

Run: `git diff --name-only origin/main...HEAD`
Expected: exactly these files (plus the spec/plan docs already on the branch):

```
docs/configuration.md
docs/superpowers/plans/2026-11-25-worker-orchestrator-checker-isolation.md
docs/superpowers/specs/2026-11-25-worker-orchestrator-checker-isolation-design.md
entrypoint.sh
scripts/factory_core/model_proxy.py
scripts/factory_core/providers/model_adapter.py
scripts/factory_core/providers/role_isolation.py
tests/test_entrypoint_current_run.sh
tests/test_factory_model_proxy.py
tests/test_provider_model_adapter.py
tests/test_provider_role_isolation.py
```

Then: `git diff origin/main...HEAD -- scheduler.sh scripts/provider_env.sh scripts/factory_core/providers/__init__.py scripts/factory_core/run_record.py scripts/factory_core/verdict.py scripts/factory_core/providers/model_endpoint.py refinement-skills/ workflows/ deploy/ run-compose.yml`
Expected: no output. `_NO_LOCAL_DISPATCH` and every gate surface are untouched.

- [ ] **Step 4: No leftover constant**

Run: `grep -rn "LOCAL_ROLE" scripts/ entrypoint.sh`
Expected: no output. Then `grep -rn "LOCAL_ROLE" tests/`
Expected: exactly one line, the `assert not hasattr(ma, "LOCAL_ROLE")` guard in
`tests/test_provider_model_adapter.py`. Keep that line.

---

## Follow-ups (not in this ticket — recorded for the operator)

- **#611**, *feat(gate): enforce the dispatch-record independence proof for local-routed
  work*, already filed. Its acceptance criteria:
  - the verdict gate calls `role_isolation.check_verdict_independence`;
  - every void result → `STATUS: BLOCKED` plus `ready-for-human`, never PASS, SKIPPED or
    ERROR;
  - `not_applicable` leaves the all-cloud path unchanged;
  - it gets its own human-reviewed spec.
- **#587** (helper authority, contract Invariant 16): the operator should add this acceptance
  line: "the helper container #587 dispatches must receive no `GH_TOKEN`, Docker-socket, or
  write-scoped tracker/code-host credential, asserted by a test over its compose/env flags."
  This plan ships only the present-tense guard: Task 1's import/AST test, which proves
  `role_isolation.py` cannot read the environment.
- The first ticket that needs a non-default role forwards `FACTORY_DISPATCH_ROLE` from
  `scheduler.sh`'s `dispatch()` by name (`-e FACTORY_DISPATCH_ROLE`), the same way it forwards
  `FACTORY_MODEL_REGISTRY_SHA256`. Until then nothing sets it, so the pointer and every ledger
  row are unchanged.
