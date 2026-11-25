# Hybrid Runtime Adapter (Minimum Cloud/Local) — Implementation Plan

**Issue:** #558

**Goal:** Extend `model_proxy.py` so it can forward to one verified, local Anthropic-Messages
endpoint and refuse explicitly when it cannot. This covers credential scope, per-request
model-identity and unsupported-mode refusal, §4.6 timeout/cancellation/failure classification,
and the §4.7 Request/Result ledger and run-record contract. Cloud runs get `cost_usd: null`
handling and `model_dispatch` parity, and default cloud behavior does not change.

**Architecture:** There are three modules, split along contract lines (spec Requirement 6).
`providers/model_endpoint.py` gets one verified loader, `load_verified_entry()`. A new
`providers/model_adapter.py` holds every per-request check as plain functions over dicts and
headers, with no `aiohttp` import. `model_proxy.py` stays the HTTP/streaming/ledger layer; it
imports the adapter lazily and only when an endpoint is selected. `run_record.py` builds
`model_dispatch` from the request-ledger rows it already reads. Because `_NO_LOCAL_DISPATCH`
stays in force, a local entry can never pass the loader in production. Every local request-path
test therefore builds its destination from a fixture entry through
`model_adapter.destination_from_entry()`.

**Tech Stack:** Python 3 (3.11+ for `asyncio.timeout`; the image runs 3.14), aiohttp 3.14
(`TestServer`/`TestClient` hermetic harness), PyYAML, pytest.

**Spec:** `docs/superpowers/specs/2026-11-25-hybrid-runtime-adapter-design.md` (approved at
`4ffc13f`).

---

## Decisions this plan fixes (where the spec left room)

These come from verifying the spec against `main`. Each one is applied in the task that cites it.

1. **Failure-class names (§4.6 interim enumeration).** `endpoint_unreachable`,
   `timeout_connect`, `timeout_time_to_first_token`, `timeout_idle`, `timeout_total`,
   `cancellation`, `protocol_incompatibility`, `capability_evidence_invalid` and
   `destination_policy_denial`. `capability_evidence_invalid` is only ever a startup failure:
   evidence problems fail `load_verified_entry()` and never become a request-time status
   (spec Requirement 14). A mid-stream upstream drop is `endpoint_unreachable`, because the
   endpoint stopped being reachable partway through the response. No class outside §4.6's list
   is invented.
2. **Classifying aiohttp exceptions without importing aiohttp.** The classification was verified
   empirically on the installed aiohttp 3.14.5. `ConnectionTimeoutError` maps to `timeout_connect`.
   `SocketTimeoutError` (the `sock_read` bound) maps to `timeout_idle`. Any other `TimeoutError`
   comes from the `ClientTimeout.total` bound and maps to `timeout_total`. An expired
   `asyncio.timeout` for time-to-first-token maps to `timeout_time_to_first_token`.
   `ClientConnectorError`, `ClientPayloadError` and `ServerDisconnectedError` map to
   `endpoint_unreachable`. `model_adapter.classify_failure()` matches the two aiohttp timeout
   classes by MRO class name so the module stays aiohttp-free (spec Requirement 6). The tests
   use the real aiohttp classes.
3. **Time to first token and idle (spec Requirement 11, constraint 9; architect cycle 1).**
   On installed aiohttp 3.14.5, `ClientTimeout.sock_read` also runs while aiohttp waits for
   response headers and for the first body chunk (verified empirically). With the spec's literal
   `idle_seconds → sock_read` mapping, a normal entry where time-to-first-token exceeds idle
   (the registry fixture uses 120 vs 60) would always be killed at `idle_seconds`. Spec
   Requirement 11's own stated behavior ("`time_to_first_token_seconds` around the first chunk,
   falling back to the idle bound for every chunk after") could then never happen. The local path
   therefore builds `ClientTimeout(total=…, connect=…, sock_read=None)` and enforces both bounds
   with one rolling `asyncio.timeout`:
   - It is armed at `time_to_first_token_seconds` before the request.
   - It is disarmed while each received chunk is written downstream, so a slow client never
     reads as upstream idleness.
   - After each chunk it is re-armed at `loop.time() + idle_seconds`.
   - It is disarmed once the body ends.

   Expiry before any body byte is `timeout_time_to_first_token`; expiry after one is
   `timeout_idle`. This implements the spec's stated intent: `idle_seconds` bounds every
   inter-chunk wait, and time-to-first-token is the only bound not native to aiohttp. Spec
   Requirement 11 was amended in operator review to state this design, and Requirement 14 to
   list the `502` refusals of decision 4. On the
   cloud path the deadline is `asyncio.timeout(None)` and is never re-armed, so it never fires.
   The cloud path keeps `ClientTimeout(total=None, sock_connect=10)`.
4. **Malformed responses on the local path.** A non-streaming local response is buffered before
   `prepare()`. A 2xx body that is not a JSON object is then refused with a real JSON error:
   `502`, `protocol_incompatibility`. A streamed local response is forwarded chunk by chunk.
   Before `write_eof()` it must end on a complete SSE frame (`\n\n`) and must contain a
   `message_stop` event. Otherwise the transport is aborted (spec Requirement 12), so the client
   sees a truncated stream. A local 3xx is refused with `502`, `protocol_incompatibility`.
   `502 Bad Gateway` is used for upstream-caused refusals. Spec Requirement 14's
   `400`/`403`/`504` cover client-caused refusals and unreachable/timeout.
5. **"Not ready" (the reachability criterion).** A local upstream `502`/`503`/`504` is forwarded
   unchanged. It is the upstream's real error, not a synthetic one. Its ledger row records
   `failure_class: "endpoint_unreachable"`. Nothing is ever retried, against this endpoint or
   any other.
6. **Ledger `status` on failure rows.** A failure row records the failure's status: the
   refusal's status, or `504` for every other handled failure, before or after `prepare()`.
   This is exactly the value today's handler writes for every caught failure, so a default-path
   failure row reads as it does today. The one addition is a cancelled handler: it now writes a
   `504` row where it previously wrote none. That only happens when aiohttp cancels the handler,
   which TestServer's `handler_cancellation=True` does; production `web.run_app` defaults to
   `False`. After `prepare()`, the aborted transport is what the client actually sees.
7. **`failure_class` on the default cloud path.** With `FACTORY_MODEL_ENDPOINT` unset, the
   ledger's `endpoint_id`, `role`, `request_id` and `failure_class` stay `None`, as spec
   Requirement 7 states, and `model_adapter` is never imported. The 504 body still gains the
   `failure_class`/`endpoint_id` keys with value `null` (spec Requirement 14's envelope). Once
   an endpoint is selected (local, or a cloud registry entry), every failure is classified.
8. **Post-`prepare()` failures** (constraint 8; architect cycle 1). The handler aborts the
   transport and writes the ledger row under `asyncio.shield`, so a cancelled task still
   records it. It then re-raises `asyncio.CancelledError`, which is never swallowed. For any
   other failure after `prepare()` it returns the already-prepared response: aiohttp's
   `finish_response()` quietly absorbs the `write_eof()` `ConnectionError` on the aborted
   transport. Re-raising instead would make aiohttp's `handle_error()` log a traceback for every
   routine mid-stream client disconnect. Nothing pretends to send a body.
9. **Null cost (spec Requirement 8).** A node whose cost key is present but `None` becomes
   `cost_capture: "cost_unknown"`. Its tokens are kept and it is not listed in
   `missing_nodes`, so it emits no health event.
   - Like a missing node, it is excluded from `known_cost_usd`, which is the lower-bound sum
     over priced nodes. When no node is priced and at least one is `cost_unknown`,
     `known_cost_usd` is null rather than a fabricated `sum([]) == 0`.
   - `totals.cost_usd` is null whenever any node is `cost_unknown`.
   - Its tokens still count toward the token totals.
   - `_sum_usage()` also propagates a `None` cost instead of raising `TypeError`; it is still
     used on the degraded no-node-map path.
   For the local-run override, after `_capture_totals()` every node's `cost_usd` is set to
   `None` along with `totals.cost_usd`/`known_cost_usd`. Doing this after classification means
   neither the harness's figure nor `sum([]) == 0` can leak through. The ledger rows that decide
   the override are read before `_capture_totals()`, as the spec says.
10. **Correlation envelope `phase`** is `read_current_run()["stage"]`.
11. **`role` on local rows** (spec Requirement 13; conformance cycle 1). Every local-destination
    ledger row carries `role: "actor"` from the moment the row's columns are built, including
    400/403 refusal rows written before anything is signed. A run's
    `model_dispatch.result.role` therefore never collapses to `null` because one request was
    refused. `request_id` stays `null` on refused rows, since no outbound request existed.
12. **Inbound `X-Factory-Correlation` on the default cloud path** is forwarded unchanged and is
    redacted only in raw artifacts. Spec Requirement 13's "dropped and overwritten" applies
    where the adapter builds the envelope, which is the local request path (Architecture
    "Request path"). The default path keeps byte-identical forwarding.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `scripts/factory_core/providers/model_endpoint.py` | Modify | `+load_verified_entry()`; two comments name the proxy's startup as the second verified reader |
| `scripts/factory_core/providers/model_adapter.py` | Create | Startup destination, request refusals, header scoping/signing, correlation envelope, timeouts, response checks, failure taxonomy |
| `scripts/factory_core/model_proxy.py` | Modify | Interface docstring, `ADAPTER_ID`, new ledger columns, correlation-header redaction, `create_app(destination)`, `main()` startup, `handle_proxy()` local branch + shared failure path |
| `scripts/factory_core/run_record.py` | Modify | Null-cost preservation, `_compute_model_dispatch()`, single ledger read + local-run cost override in `cmd_assemble` |
| `docs/dark-factory-model-proxy-rollout.md` | Modify | "Local destination: supported and uncertified modes (#558)" section |
| `tests/test_provider_model_endpoint.py` | Modify | `load_verified_entry()` tests |
| `tests/test_provider_model_adapter.py` | Create | Unit tests for every `model_adapter` function |
| `tests/test_factory_model_proxy.py` | Modify | Ledger-column/parity, startup, local request-path and failure-mode hermetic tests, doc test |
| `tests/test_run_record.py` | Modify | Null-cost, `model_dispatch` and local-run override tests |

No other file changes. In particular there is no change to `scheduler.sh`, `provider_env.sh`,
`entrypoint.sh`, `run-compose.yml`, `providers/__init__.py`, `cost_report.py`, the workflow
YAML, `deploy/**`, `gate_*`, breaker, budgets or tool allow/deny lists (spec Scope).

Memory lessons baked in (`.archon/memory/architecture.md`, #558 entries):
- Request-time logic goes in `model_adapter.py`, never in `model_endpoint.py` or `model_proxy.py`
  (Tasks 2–5).
- Never call `providers.preflight(side="run")` from the proxy. Use only the verified-read
  primitives (Tasks 1, 7).

Run every command from the repo root. `tests/` files insert `scripts/` on `sys.path`
themselves, so a bare `python -m pytest` works (CI also sets `PYTHONPATH=scripts`).

---

### Task 1: `load_verified_entry()` in `model_endpoint.py`

**Files:**
- Modify: `scripts/factory_core/providers/model_endpoint.py`
- Test: `tests/test_provider_model_endpoint.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_provider_model_endpoint.py`
  (after `test_scheduler_preflight_without_a_registry_has_no_hosting`, before the
  `# --- Task 5` section). The tests reuse the file's existing `_stage`, `_local_env`, `_record`,
  `pinned_suite`, `_serve_first_read` and `_registry_bytes` helpers:

```python
# --- #558: load_verified_entry (the proxy's Configuration-contract loader) -----------------

def _forbidden(*args, **kwargs):
    raise AssertionError("load_verified_entry() must never call _select() or load_registry()")


def test_load_verified_entry_returns_a_verified_cloud_entry(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [CLOUD, LOCAL])
    assert me.load_verified_entry(reg, "cloud-test") == ([], CLOUD)


def test_load_verified_entry_fails_closed_on_a_digest_mismatch(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [CLOUD])
    _write_registry(reg, [CLOUD, _entry(CLOUD, id="cloud-extra")])
    problems, entry = me.load_verified_entry(reg, "cloud-test")
    assert entry is None
    assert len(problems) == 1 and "does not match FACTORY_MODEL_REGISTRY_SHA256" in problems[0]


def test_load_verified_entry_requires_the_digest(tmp_path):
    reg = _write_registry(tmp_path / "staged", [CLOUD])
    problems, entry = me.load_verified_entry(reg, "cloud-test")
    assert entry is None and "FACTORY_MODEL_REGISTRY_SHA256 is not set" in problems[0]


def test_load_verified_entry_unknown_id_chooses_no_other(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [CLOUD])
    problems, entry = me.load_verified_entry(reg, "cloud-nope")
    assert entry is None and "no other endpoint is chosen instead" in problems[0]


def test_load_verified_entry_without_registry_file(tmp_path, monkeypatch):
    reg = tmp_path / "staged"
    (reg / me.EVIDENCE_DIR).mkdir(parents=True)
    (reg / me.EVIDENCE_DIR / "cloud-test.yaml").write_text("x: 1\n", encoding="utf-8")
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", me.tree_sha256(reg))
    assert me.load_verified_entry(reg, "cloud-test") == (
        [f"model endpoint registry not found at {reg / me.REGISTRY_FILE}"], None)


def test_load_verified_entry_keeps_no_local_dispatch(tmp_path, monkeypatch, pinned_suite):
    # Not filtered: a local entry with perfect evidence and env still comes back as a problem.
    reg = _stage(tmp_path, monkeypatch, [LOCAL], evidence={"local-test": _record(LOCAL)})
    _local_env(monkeypatch)
    assert me.load_verified_entry(reg, "local-test") == (
        [me._NO_LOCAL_DISPATCH.format(eid="local-test")], None)


def test_load_verified_entry_reads_each_staged_file_once(tmp_path, monkeypatch, pinned_suite):
    reg = _stage(tmp_path, monkeypatch, [LOCAL], evidence={"local-test": _record(LOCAL)})
    _local_env(monkeypatch)
    monkeypatch.setattr(me, "_select", _forbidden)
    monkeypatch.setattr(me, "load_registry", _forbidden)
    evidence = f"{me.EVIDENCE_DIR}/local-test.yaml"
    reads = _serve_first_read(monkeypatch, reg, {
        me.REGISTRY_FILE: _registry_bytes([_entry(CLOUD, id="local-test")]),
        evidence: yaml.safe_dump({"capability_evidence": _record(
            LOCAL, results=dict(_record(LOCAL)["results"], streaming="fail"))}).encode("utf-8"),
    })
    assert me.load_verified_entry(reg, "local-test") == (
        [me._NO_LOCAL_DISPATCH.format(eid="local-test")], None)
    assert reads == {me.REGISTRY_FILE: 1, evidence: 1}
```

- [ ] **Step 2: Run the tests and confirm they fail.**

```bash
python -m pytest tests/test_provider_model_endpoint.py -k load_verified_entry -v
```
Expected: 7 failed, each with `AttributeError: module 'factory_core.providers.model_endpoint' has no attribute 'load_verified_entry'`.

- [ ] **Step 3: Implement.** In `scripts/factory_core/providers/model_endpoint.py`, add after
  `preflight()` (before `scheduler_preflight()`):

```python
def load_verified_entry(registry_path, endpoint_id) -> tuple[list[str], dict | None]:
    """The model proxy's Configuration-contract loader (#558): (problems, entry).

    entry is non-None only when problems is empty. Exactly one read_verified_tree() call;
    registry, entry and evidence are parsed only from the bytes it hashed (#593) — never
    _select(), load_registry() or a second read. _NO_LOCAL_DISPATCH is not filtered, so a
    local entry never comes back until #587 lifts it.
    """
    registry_path = Path(registry_path)
    problems, files = read_verified_tree(registry_path)
    if problems:
        return problems, None
    path = registry_path / REGISTRY_FILE
    if REGISTRY_FILE not in files:
        return [f"model endpoint registry not found at {path}"], None
    try:
        endpoints = _parse_registry(files[REGISTRY_FILE], path)
    except RegistryError as e:
        return e.problems, None
    entry, problems = _pick(endpoints, endpoint_id, path)
    if entry is None:
        return problems, None
    if entry["hosting_location"] == "local":
        problems = problems + _local_problems(entry, files, registry_path)
    return (problems, None) if problems else ([], entry)
```

  In the same file, update the two "#558 must keep it that way" comments so they name the
  second verified reader. In `staged_registry_dir()`, replace the comment's second and third
  lines

```python
    # Registry verification must never run from agent-controlled env (#593): today only the
    # pre-agent entrypoint.sh preflight reads this, and #558 must keep it that way.
```
  with
```python
    # Registry verification must never run from agent-controlled env (#593): only the
    # pre-agent entrypoint.sh preflight and the model proxy's startup (#558 — its own
    # container, whose env the agent never sets) read this.
```
  In `read_verified_tree()`, replace

```python
    # Registry verification must never run from agent-controlled env (#593): today only the
    # pre-agent entrypoint.sh preflight reads FACTORY_MODEL_REGISTRY_SHA256, and #558 must
    # keep it that way.
```
  with
```python
    # Registry verification must never run from agent-controlled env (#593): only the
    # pre-agent entrypoint.sh preflight and the model proxy's startup (#558 — its own
    # container, whose env the agent never sets) read FACTORY_MODEL_REGISTRY_SHA256.
```

  Leave `_local_problems()` and `_NO_LOCAL_DISPATCH` untouched.

- [ ] **Step 4: Run the tests and confirm they pass.**

```bash
python -m pytest tests/test_provider_model_endpoint.py -v
```
Expected: all passed, including the 7 new `load_verified_entry` tests.

- [ ] **Step 5: Commit.**

```bash
git add scripts/factory_core/providers/model_endpoint.py tests/test_provider_model_endpoint.py
git commit -m "feat(#558): load_verified_entry() — one verified read for the model proxy"
```

---

### Task 2: `model_adapter.py` — startup destination

**Files:**
- Create: `scripts/factory_core/providers/model_adapter.py`
- Create: `tests/test_provider_model_adapter.py`

- [ ] **Step 1: Write the failing tests.** Create `tests/test_provider_model_adapter.py`:

```python
"""#558: the local-endpoint runtime adapter (providers/model_adapter.py).

Destinations are built from fixture entries through destination_from_entry(): a local entry
never comes back from load_verified_entry() while _NO_LOCAL_DISPATCH stands.
"""
import ast
import asyncio
import copy
import json
import sys
from pathlib import Path

import aiohttp
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from factory_core.providers import model_adapter as ma  # noqa: E402
from factory_core.providers import model_endpoint as me  # noqa: E402

SENTINEL = "sk-sentinel-local-gateway-token"
SERVED = "qwen2.5-coder-32b"
LOCAL = {
    "id": "local-test",
    "hosting_location": "local",
    "protocol": "anthropic-messages",
    "accepts_payload_classes": ["local_only"],
    "model_identity": {"served_name": SERVED, "digest_or_revision": "sha256:" + "ab" * 32},
    "base_url": "http://host.docker.internal:8080",
    "secret_reference": "LOCAL_GATEWAY_TOKEN",
    "timeouts": {"connect_seconds": 10, "time_to_first_token_seconds": 120,
                 "idle_seconds": 60, "total_seconds": 1800},
}
CLOUD = {
    "id": "cloud-test",
    "hosting_location": "cloud",
    "protocol": "anthropic-messages",
    "accepts_payload_classes": ["cloud_ok"],
    "model_identity": {"served_name": "claude-opus-5-5", "digest_or_revision": "claude-opus-5-5"},
    "secret_reference": "ANTHROPIC_API_KEY",
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (*me.CLOUD_CREDENTIALS, "MODEL_PROXY_UPSTREAM", "LOCAL_GATEWAY_TOKEN",
                 "FACTORY_MODEL_REGISTRY_SHA256", "FACTORY_MODEL_REGISTRY_PATH"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setenv("LOCAL_GATEWAY_TOKEN", SENTINEL)


def _entry(**overrides):
    entry = copy.deepcopy(LOCAL)
    entry.update(overrides)
    return entry


def _problems(entry):
    with pytest.raises(ma.DestinationError) as exc:
        ma.destination_from_entry(entry)
    return exc.value.problems


# --- Task 2: startup destination ---------------------------------------------------------

def test_local_destination_forwards_only_to_the_entry_base_url(secret):
    dest = ma.destination_from_entry(LOCAL)
    assert dest["upstream"] == LOCAL["base_url"]
    assert dest["hosting_location"] == "local" and dest["endpoint_id"] == "local-test"
    assert dest["served_name"] == SERVED
    assert dest["secret_reference"] == "LOCAL_GATEWAY_TOKEN"
    assert dict(dest["timeouts"]) == LOCAL["timeouts"]
    assert dict(dest["payload_decision"]) == me.payload_decision(None, LOCAL)


def test_destination_is_read_only(secret):
    dest = ma.destination_from_entry(LOCAL)
    with pytest.raises(TypeError):
        dest["upstream"] = "https://api.anthropic.com"


def test_destination_never_holds_the_secret_value(secret):
    assert SENTINEL not in repr(dict(ma.destination_from_entry(LOCAL)))


def test_matching_model_proxy_upstream_is_accepted(secret, monkeypatch):
    monkeypatch.setenv("MODEL_PROXY_UPSTREAM", LOCAL["base_url"] + "/")
    assert ma.destination_from_entry(LOCAL)["upstream"] == LOCAL["base_url"]


def test_differing_model_proxy_upstream_fails_startup(secret, monkeypatch):
    monkeypatch.setenv("MODEL_PROXY_UPSTREAM", "https://api.anthropic.com")
    problems = _problems(LOCAL)
    assert any("MODEL_PROXY_UPSTREAM" in p for p in problems)
    assert not any("api.anthropic.com" in p for p in problems)


@pytest.mark.parametrize("name", me.CLOUD_CREDENTIALS)
def test_any_cloud_credential_fails_a_local_startup(secret, monkeypatch, name):
    monkeypatch.setenv(name, "cloud-sentinel")
    problems = _problems(LOCAL)
    assert any(name in p for p in problems)
    assert "cloud-sentinel" not in " ".join(problems)


def test_missing_secret_reference_fails_startup():
    assert any("LOCAL_GATEWAY_TOKEN" in p and "not set" in p for p in _problems(LOCAL))


def test_payload_decision_refusal_fails_startup(secret):
    problems = _problems(_entry(accepts_payload_classes=["cloud_ok"]))
    assert any("payload decision" in p and "local_only" in p for p in problems)


def test_cloud_destination_keeps_the_cloud_upstream(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cloud-sentinel")
    dest = ma.destination_from_entry(CLOUD)
    assert dest["upstream"] is None and dest["hosting_location"] == "cloud"
    assert dest["payload_decision"]["payload_class"] == "cloud_ok"


def test_resolve_local_destination_surfaces_loader_problems(tmp_path):
    (tmp_path / me.REGISTRY_FILE).write_text("schema_version: '1.0'\nendpoints: []\n")
    with pytest.raises(ma.DestinationError) as exc:
        ma.resolve_local_destination(tmp_path, "local-test")
    assert "FACTORY_MODEL_REGISTRY_SHA256 is not set" in exc.value.problems[0]


def test_resolve_local_destination_builds_from_the_verified_entry(tmp_path, monkeypatch):
    seen = []

    def loader(path, eid):
        seen.append((path, eid))
        return [], copy.deepcopy(CLOUD)

    monkeypatch.setattr(me, "load_verified_entry", loader)
    assert ma.resolve_local_destination(tmp_path, "cloud-test")["endpoint_id"] == "cloud-test"
    assert seen == [(tmp_path, "cloud-test")]


def test_adapter_module_does_not_import_aiohttp():
    tree = ast.parse(Path(ma.__file__).read_text(encoding="utf-8"))
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
                for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree)
                 if isinstance(n, ast.ImportFrom) and n.module}
    assert "aiohttp" not in imported
```

- [ ] **Step 2: Run the tests and confirm they fail.**

```bash
python -m pytest tests/test_provider_model_adapter.py -v
```
Expected: collection error `ImportError: cannot import name 'model_adapter' from 'factory_core.providers'`.

- [ ] **Step 3: Implement.** Create `scripts/factory_core/providers/model_adapter.py`:

```python
"""Local-endpoint runtime adapter: the Request/Result-contract checks (#558).

Contract: docs/hybrid-execution-contract.md §4.3, §4.4, §4.6, §4.7; design in
docs/archive/2026-11-25-hybrid-runtime-adapter-design.md.

Plain functions over dicts and headers — no aiohttp import — so each check unit-tests without
the TestServer harness. model_proxy.py imports this module only when an endpoint is selected
(FACTORY_MODEL_ENDPOINT), so the default cloud path never loads it or the registry code below.
"""
import asyncio
import json
import os
import re
import uuid
from types import MappingProxyType

from factory_core.providers import model_endpoint

CORRELATION_HEADER = "X-Factory-Correlation"
# §4.2: a local-routed container never hosts an independent checker — a contract constant.
LOCAL_ROLE = "actor"
REFUSAL_TYPE = "factory_model_adapter_refused"
# Dropped from every local-destined request: a client-supplied credential is self-report (#288).
INBOUND_AUTH_HEADERS = ("authorization", "x-api-key", "api-key")
# protocol is pinned to anthropic-messages, whose auth header this is.
LOCAL_AUTH_HEADER = "x-api-key"
SUPPORTED_METHOD = "POST"
SUPPORTED_PATH = "/v1/messages"
FORCED_TOOL_CHOICES = ("any", "tool")
DEFERRED_TOOL_FIELD = "defer_loading"
STRUCTURED_OUTPUT_FIELDS = ("output_config", "output_format")
# A local upstream answering one of these is not ready: forwarded unchanged, never retried.
NOT_READY_STATUSES = (502, 503, 504)

# Contract §4.6's interim failure classes; #238's names supersede these when it ships.
ENDPOINT_UNREACHABLE = "endpoint_unreachable"
TIMEOUT_CONNECT = "timeout_connect"
TIMEOUT_TIME_TO_FIRST_TOKEN = "timeout_time_to_first_token"
TIMEOUT_IDLE = "timeout_idle"
TIMEOUT_TOTAL = "timeout_total"
CANCELLATION = "cancellation"
PROTOCOL_INCOMPATIBILITY = "protocol_incompatibility"
# Startup-only: evidence problems fail load_verified_entry(), never a request (spec Req. 14).
CAPABILITY_EVIDENCE_INVALID = "capability_evidence_invalid"
DESTINATION_POLICY_DENIAL = "destination_policy_denial"
FAILURE_CLASSES = (
    ENDPOINT_UNREACHABLE, TIMEOUT_CONNECT, TIMEOUT_TIME_TO_FIRST_TOKEN, TIMEOUT_IDLE,
    TIMEOUT_TOTAL, CANCELLATION, PROTOCOL_INCOMPATIBILITY, CAPABILITY_EVIDENCE_INVALID,
    DESTINATION_POLICY_DENIAL,
)

_MESSAGE_STOP_RE = re.compile(rb'"type"\s*:\s*"message_stop"')


class DestinationError(Exception):
    """A startup problem: the proxy must not bind. .problems is the list of messages."""

    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = list(problems)


class AdapterRefusal(Exception):
    """An explicit refusal — a real error body and a ledger row, never a synthetic success."""

    def __init__(self, status, failure_class, message):
        super().__init__(message)
        self.status = status
        self.failure_class = failure_class
        self.message = message


def destination_from_entry(entry, environ=None):
    """Startup checks over one verified entry; the read-only destination, or DestinationError.

    The payload decision is computed here, once (declared is None in v1 — spec Req. 15).
    A local entry's upstream is its base_url and nothing else (Req. 3); its credential scope
    is enforced, not assumed (Req. 4). Never holds a secret value.
    """
    env = os.environ if environ is None else environ
    eid = entry["id"]
    problems = []
    decision = model_endpoint.payload_decision(None, entry)
    if not decision["allow"]:
        problems.append(f"endpoint '{eid}': payload decision does not allow dispatch — "
                        f"{decision['reason']}")
    upstream = None
    if entry["hosting_location"] == "local":
        upstream = entry["base_url"]
        configured = env.get("MODEL_PROXY_UPSTREAM")
        if configured and configured.rstrip("/") != upstream.rstrip("/"):
            # The configured value is not echoed: it is whatever a human set, not ours to log.
            problems.append(f"MODEL_PROXY_UPSTREAM is set and differs from endpoint '{eid}' "
                            "base_url — a local-routed proxy forwards only to the verified "
                            "entry's base_url")
        present = sorted(n for n in model_endpoint.CLOUD_CREDENTIALS if env.get(n))
        if present:
            problems.append(f"endpoint '{eid}' is local but cloud model credential(s) are set "
                            f"in the proxy: {', '.join(present)} — a local-routed proxy holds "
                            "none (contract §4.3)")
        ref = entry["secret_reference"]
        if not env.get(ref):
            problems.append(f"{ref} (secret_reference of endpoint '{eid}') is not set in the "
                            "proxy")
    if problems:
        raise DestinationError(problems)
    return MappingProxyType({
        "endpoint_id": eid,
        "hosting_location": entry["hosting_location"],
        "upstream": upstream,
        "served_name": entry["model_identity"]["served_name"],
        "secret_reference": entry["secret_reference"],
        "timeouts": MappingProxyType(dict(entry.get("timeouts") or {})),
        "payload_decision": MappingProxyType(decision),
    })


def resolve_local_destination(registry_path, endpoint_id):
    """The proxy's one startup resolution (spec Req. 2): verified loader, then the checks."""
    problems, entry = model_endpoint.load_verified_entry(registry_path, endpoint_id)
    if problems:
        raise DestinationError(problems)
    return destination_from_entry(entry)
```

- [ ] **Step 4: Run the tests and confirm they pass.**

```bash
python -m pytest tests/test_provider_model_adapter.py -v
```
Expected: all passed (one of them is the 6-case `CLOUD_CREDENTIALS` parametrization).

- [ ] **Step 5: Commit.**

```bash
git add scripts/factory_core/providers/model_adapter.py tests/test_provider_model_adapter.py
git commit -m "feat(#558): model_adapter startup destination — base_url only, credential scope enforced"
```

---

### Task 3: `model_adapter.py` — request refusals (unsupported endpoint/mode, model identity)

**Files:**
- Modify: `scripts/factory_core/providers/model_adapter.py`
- Test: `tests/test_provider_model_adapter.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_provider_model_adapter.py`:

```python
# --- Task 3: request refusals --------------------------------------------------------------

def _dest(monkeypatch):
    monkeypatch.setenv("LOCAL_GATEWAY_TOKEN", SENTINEL)
    return ma.destination_from_entry(LOCAL)


def _body(**fields):
    return json.dumps({"model": SERVED, "max_tokens": 16,
                       "messages": [{"role": "user", "content": "hi"}], **fields}).encode()


def _refusal(fn, *args):
    with pytest.raises(ma.AdapterRefusal) as exc:
        fn(*args)
    return exc.value


def test_plain_and_tool_result_turns_are_supported(monkeypatch):
    dest = _dest(monkeypatch)
    assert ma.check_request(dest, "POST", "/v1/messages", _body())["model"] == SERVED
    tool_turn = _body(
        tools=[{"name": "Bash", "input_schema": {"type": "object"}}],
        tool_choice={"type": "auto"},
        messages=[
            {"role": "user", "content": "list files"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_01", "name": "Bash", "input": {"cmd": "ls"}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_01", "content": "a.txt"}]},
        ],
    )
    assert ma.check_request(dest, "POST", "/v1/messages", tool_turn)["tool_choice"] == {"type": "auto"}


@pytest.mark.parametrize("extra,needle", [
    ({"tool_choice": {"type": "any"}}, "forced tool"),
    ({"tool_choice": {"type": "tool", "name": "Bash"}}, "forced tool"),
    ({"tools": [{"name": "Bash", "input_schema": {}, "defer_loading": True}]}, "defer_loading"),
    ({"output_config": {"format": {"type": "json_schema", "schema": {}}}}, "output_config"),
    ({"output_format": {"type": "json_schema", "schema": {}}}, "output_format"),
])
def test_unsupported_modes_are_refused_400(monkeypatch, extra, needle):
    refusal = _refusal(ma.check_request, _dest(monkeypatch), "POST", "/v1/messages", _body(**extra))
    assert (refusal.status, refusal.failure_class) == (400, ma.PROTOCOL_INCOMPATIBILITY)
    assert needle in refusal.message


@pytest.mark.parametrize("method,path", [
    ("GET", "/v1/messages"), ("POST", "/v1/messages/count_tokens"), ("POST", "/v1/complete"),
])
def test_unsupported_endpoints_are_refused_400(monkeypatch, method, path):
    refusal = _refusal(ma.check_request, _dest(monkeypatch), method, path, _body())
    assert (refusal.status, refusal.failure_class) == (400, ma.PROTOCOL_INCOMPATIBILITY)
    assert "only POST /v1/messages" in refusal.message


@pytest.mark.parametrize("body", [b"", b"not json", b"[1, 2]"])
def test_a_body_that_is_not_a_json_object_is_refused_400(monkeypatch, body):
    refusal = _refusal(ma.check_request, _dest(monkeypatch), "POST", "/v1/messages", body)
    assert (refusal.status, refusal.failure_class) == (400, ma.PROTOCOL_INCOMPATIBILITY)


@pytest.mark.parametrize("model", ["claude-opus-5-5", "qwen2.5-coder-7b", None])
def test_model_identity_mismatch_is_refused_403_never_remapped(monkeypatch, model):
    refusal = _refusal(ma.check_request, _dest(monkeypatch), "POST", "/v1/messages",
                       _body(model=model))
    assert (refusal.status, refusal.failure_class) == (403, ma.DESTINATION_POLICY_DENIAL)
    assert "never remapped" in refusal.message
```

- [ ] **Step 2: Run the tests and confirm they fail.**

```bash
python -m pytest tests/test_provider_model_adapter.py -k "supported or refused or identity" -v
```
Expected: failed with `AttributeError: module 'factory_core.providers.model_adapter' has no attribute 'check_request'`.

- [ ] **Step 3: Implement.** Append to `scripts/factory_core/providers/model_adapter.py`:

```python
def _incompatible(message):
    return AdapterRefusal(400, PROTOCOL_INCOMPATIBILITY, message)


def check_request(dest, method, path, body) -> dict:
    """Local-path refusals, before any outbound call (spec Req. 9, 10); the parsed body.

    Field-based and fixed: only POST /v1/messages (path compared without its query string);
    forced tool choice, deferred tools and structured output are refused, never forwarded.
    """
    if method != SUPPORTED_METHOD or path != SUPPORTED_PATH:
        raise _incompatible(f"{method} {path} is not supported on a local destination — "
                            f"only {SUPPORTED_METHOD} {SUPPORTED_PATH}")
    try:
        parsed = json.loads(body) if body else None
    except ValueError:  # JSONDecodeError and UnicodeDecodeError
        parsed = None
    if not isinstance(parsed, dict):
        raise _incompatible("request body is not a JSON object")
    choice = parsed.get("tool_choice")
    if isinstance(choice, dict) and choice.get("type") in FORCED_TOOL_CHOICES:
        raise _incompatible(f"forced tool choice ({choice['type']}) is not supported on a "
                            "local destination")
    tools = parsed.get("tools")
    if isinstance(tools, list) and any(isinstance(t, dict) and DEFERRED_TOOL_FIELD in t
                                       for t in tools):
        raise _incompatible(f"deferred tools ({DEFERRED_TOOL_FIELD}) are not supported on a "
                            "local destination")
    for field in STRUCTURED_OUTPUT_FIELDS:
        if field in parsed:
            raise _incompatible(f"structured output ({field}) is not supported on a local "
                                "destination")
    check_model_identity(dest, parsed.get("model"))
    return parsed


def check_model_identity(dest, model) -> None:
    """§4.11 decision 2: a local request names exactly the served model — refused, not remapped."""
    if model != dest["served_name"]:
        raise AdapterRefusal(403, DESTINATION_POLICY_DENIAL,
                             f"model {model!r} is not endpoint '{dest['endpoint_id']}' served "
                             f"model {dest['served_name']!r}; models are never remapped")
```

- [ ] **Step 4: Run the tests and confirm they pass.**

```bash
python -m pytest tests/test_provider_model_adapter.py -v
```
Expected: all passed.

- [ ] **Step 5: Commit.**

```bash
git add scripts/factory_core/providers/model_adapter.py tests/test_provider_model_adapter.py
git commit -m "feat(#558): refuse unsupported local endpoints/modes and model-identity mismatches"
```

---

### Task 4: `model_adapter.py` — header scoping/signing, correlation envelope, timeouts

**Files:**
- Modify: `scripts/factory_core/providers/model_adapter.py`
- Test: `tests/test_provider_model_adapter.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_provider_model_adapter.py`:

```python
# --- Task 4: headers, correlation, timeouts --------------------------------------------------

def test_strip_and_sign_drops_every_inbound_credential_and_correlation(monkeypatch):
    dest = _dest(monkeypatch)
    out = ma.strip_and_sign_headers({
        "Host": "proxy", "Authorization": "Bearer inbound", "X-Api-Key": "inbound-key",
        "api-key": "inbound-2", "X-Factory-Correlation": '{"role":"checker"}',
        "Content-Type": "application/json", "anthropic-version": "2023-06-01",
    }, dest)
    assert out == {"Content-Type": "application/json", "anthropic-version": "2023-06-01",
                   "x-api-key": SENTINEL}


def test_strip_and_sign_refuses_when_the_secret_is_gone(monkeypatch):
    dest = _dest(monkeypatch)
    monkeypatch.delenv("LOCAL_GATEWAY_TOKEN")
    refusal = _refusal(ma.strip_and_sign_headers, {"x-api-key": "inbound"}, dest)
    assert (refusal.status, refusal.failure_class) == (403, ma.DESTINATION_POLICY_DENIAL)
    assert "inbound" not in refusal.message


def test_build_correlation_envelope(monkeypatch):
    dest = _dest(monkeypatch)
    run = {"run_id": "run-9", "issue_number": 558, "intent": "plan", "stage": "plan"}
    header, envelope = ma.build_correlation(dest, run)
    assert json.loads(header) == envelope
    assert set(envelope) == {"run_id", "issue", "phase", "role", "endpoint_id", "request_id"}
    assert envelope["run_id"] == "run-9" and envelope["issue"] == 558
    assert envelope["phase"] == "plan" and envelope["role"] == "actor"
    assert envelope["endpoint_id"] == "local-test"
    assert ma.build_correlation(dest, run)[1]["request_id"] != envelope["request_id"]


def test_resolve_timeout_maps_the_entry_bounds(monkeypatch):
    assert ma.resolve_timeout(_dest(monkeypatch)) == {
        "connect": 10, "time_to_first_token": 120, "idle": 60, "total": 1800}
```

- [ ] **Step 2: Run the tests and confirm they fail.**

```bash
python -m pytest tests/test_provider_model_adapter.py -k "strip or correlation or timeout" -v
```
Expected: 4 failed with `AttributeError` (`strip_and_sign_headers` / `build_correlation` /
`resolve_timeout` missing).

- [ ] **Step 3: Implement.** Append to `scripts/factory_core/providers/model_adapter.py`:

```python
def strip_and_sign_headers(headers, dest, environ=None) -> dict:
    """Credential scope per request (spec Req. 4, 13): drop host, every inbound auth header and
    any inbound correlation header, then sign from the entry's secret_reference only."""
    env = os.environ if environ is None else environ
    dropped = {"host", CORRELATION_HEADER.lower(), *INBOUND_AUTH_HEADERS}
    out = {k: v for k, v in headers.items() if k.lower() not in dropped}
    secret = env.get(dest["secret_reference"])
    if not secret:
        raise AdapterRefusal(403, DESTINATION_POLICY_DENIAL,
                             f"{dest['secret_reference']} (secret_reference of endpoint "
                             f"'{dest['endpoint_id']}') is not set")
    out[LOCAL_AUTH_HEADER] = secret
    return out


def build_correlation(dest, run) -> tuple[str, dict]:
    """§4.4's envelope from read_current_run()'s pointer fields: (header value, envelope)."""
    envelope = {
        "run_id": run["run_id"],
        "issue": run["issue_number"],
        "phase": run["stage"],
        "role": LOCAL_ROLE,
        "endpoint_id": dest["endpoint_id"],
        "request_id": str(uuid.uuid4()),
    }
    return json.dumps(envelope, separators=(",", ":")), envelope


def resolve_timeout(dest) -> dict:
    """§4.6 bounds. connect/total are aiohttp.ClientTimeout fields; time_to_first_token and
    idle share one rolling deadline in model_proxy — aiohttp's sock_read also runs before the
    first byte, so it cannot carry idle without capping time_to_first_token at idle."""
    t = dest["timeouts"]
    return {
        "connect": t["connect_seconds"],
        "time_to_first_token": t["time_to_first_token_seconds"],
        "idle": t["idle_seconds"],
        "total": t["total_seconds"],
    }
```

- [ ] **Step 4: Run the tests and confirm they pass.**

```bash
python -m pytest tests/test_provider_model_adapter.py -v
```
Expected: all passed.

- [ ] **Step 5: Commit.**

```bash
git add scripts/factory_core/providers/model_adapter.py tests/test_provider_model_adapter.py
git commit -m "feat(#558): scope and sign local credentials; correlation envelope; timeout bounds"
```

---

### Task 5: `model_adapter.py` — response checks and failure classification

**Files:**
- Modify: `scripts/factory_core/providers/model_adapter.py`
- Test: `tests/test_provider_model_adapter.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_provider_model_adapter.py`:

```python
# --- Task 5: response checks, failure classification --------------------------------------

SSE_OK = (b'event: message_start\ndata: {"type":"message_start"}\n\n'
          b'event: message_stop\ndata: {"type":"message_stop"}\n\n')


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_a_redirect_is_protocol_incompatibility(status):
    refusal = _refusal(ma.check_response_status, status)
    assert (refusal.status, refusal.failure_class) == (502, ma.PROTOCOL_INCOMPATIBILITY)


@pytest.mark.parametrize("status", [200, 400, 429, 500])
def test_non_redirect_statuses_pass_the_status_check(status):
    ma.check_response_status(status)


def test_not_ready_statuses_are_endpoint_unreachable():
    assert [ma.status_failure_class(s) for s in (200, 400, 500, 502, 503, 504)] == [
        None, None, None, ma.ENDPOINT_UNREACHABLE, ma.ENDPOINT_UNREACHABLE, ma.ENDPOINT_UNREACHABLE]


def test_json_body_check():
    ma.check_json_body(200, b'{"type":"message"}')
    ma.check_json_body(400, b"upstream says no")  # a non-2xx body is forwarded as-is
    for bad in (b'{"type":"mess', b"[1]", b""):
        refusal = _refusal(ma.check_json_body, 200, bad)
        assert (refusal.status, refusal.failure_class) == (502, ma.PROTOCOL_INCOMPATIBILITY)


def test_sse_completeness_check():
    ma.check_sse_complete(200, SSE_OK)
    ma.check_sse_complete(200, SSE_OK.replace(b"\n", b"\r\n"))
    ma.check_sse_complete(500, b"partial")  # only a 2xx stream must be complete
    for bad in (SSE_OK[:-1], b'event: message_start\ndata: {"type":"message_start"}\n\n', b""):
        refusal = _refusal(ma.check_sse_complete, 200, bad)
        assert (refusal.status, refusal.failure_class) == (502, ma.PROTOCOL_INCOMPATIBILITY)


@pytest.mark.parametrize("exc,expected", [
    (aiohttp.ConnectionTimeoutError(), ma.TIMEOUT_CONNECT),
    (aiohttp.SocketTimeoutError(), ma.TIMEOUT_IDLE),
    (TimeoutError(), ma.TIMEOUT_TOTAL),
    (aiohttp.ClientPayloadError("Response payload is not completed"), ma.ENDPOINT_UNREACHABLE),
    (aiohttp.ServerDisconnectedError(), ma.ENDPOINT_UNREACHABLE),
    (ConnectionRefusedError(), ma.ENDPOINT_UNREACHABLE),
    (asyncio.CancelledError(), ma.CANCELLATION),
    (ma.AdapterRefusal(502, ma.PROTOCOL_INCOMPATIBILITY, "x"), ma.PROTOCOL_INCOMPATIBILITY),
])
def test_classify_failure(exc, expected):
    assert ma.classify_failure(exc) == expected


def test_classify_failure_flags():
    assert ma.classify_failure(TimeoutError(), ttft_expired=True) == ma.TIMEOUT_TIME_TO_FIRST_TOKEN
    assert ma.classify_failure(TimeoutError(), idle_expired=True) == ma.TIMEOUT_IDLE
    assert ma.classify_failure(ConnectionResetError(), downstream_gone=True) == ma.CANCELLATION


def test_every_class_is_in_the_interim_enumeration():
    assert len(set(ma.FAILURE_CLASSES)) == 9
```

- [ ] **Step 2: Run the tests and confirm they fail.**

```bash
python -m pytest tests/test_provider_model_adapter.py -k "redirect or status or json_body or sse or classify or enumeration" -v
```
Expected: failed with `AttributeError` for `check_response_status`, `status_failure_class`,
`check_json_body`, `check_sse_complete` and `classify_failure`.
`test_every_class_is_in_the_interim_enumeration` already passes.

- [ ] **Step 3: Implement.** Append to `scripts/factory_core/providers/model_adapter.py`:

```python
def check_response_status(status) -> None:
    """Redirects are never followed (allow_redirects=False); a 3xx is refused (spec Req. 3)."""
    if 300 <= status < 400:
        raise AdapterRefusal(502, PROTOCOL_INCOMPATIBILITY,
                             f"local endpoint answered {status} (a redirect); redirects are "
                             "never followed")


def status_failure_class(status):
    """A not-ready upstream status is endpoint_unreachable; any other status has no class."""
    return ENDPOINT_UNREACHABLE if status in NOT_READY_STATUSES else None


def check_json_body(status, data) -> None:
    """A 2xx non-streaming local body must be a JSON object (usage parsing requires it)."""
    if not 200 <= status < 300:
        return
    try:
        parsed = json.loads(data)
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        raise AdapterRefusal(502, PROTOCOL_INCOMPATIBILITY,
                             "local endpoint returned a non-streaming 2xx body that is not a "
                             "JSON object")


def check_sse_complete(status, data) -> None:
    """A 2xx local event stream must end on a complete frame and carry message_stop."""
    if not 200 <= status < 300:
        return
    if not data.endswith((b"\n\n", b"\r\n\r\n")) or not _MESSAGE_STOP_RE.search(data):
        raise AdapterRefusal(502, PROTOCOL_INCOMPATIBILITY,
                             "local endpoint's event stream ended without a complete "
                             "message_stop frame")


def classify_failure(exc, *, ttft_expired=False, idle_expired=False,
                     downstream_gone=False) -> str:
    """One §4.6 interim class per handled failure — never unclassified, never retried.

    ttft_expired/idle_expired: the proxy's rolling deadline fired before/after the first body
    byte. aiohttp's connect/sock_read timeouts are matched by class name so this module stays
    aiohttp-free; any other TimeoutError is aiohttp's ClientTimeout.total bound. Edge cases
    needing unusual bounds: aiohttp reports a total-bound expiry during connect as
    ConnectionTimeoutError (timeout_connect), and with time_to_first_token below connect a
    slow connect is timeout_time_to_first_token.
    """
    if isinstance(exc, AdapterRefusal):
        return exc.failure_class
    if isinstance(exc, asyncio.CancelledError) or downstream_gone:
        return CANCELLATION
    if ttft_expired:
        return TIMEOUT_TIME_TO_FIRST_TOKEN
    if idle_expired:
        return TIMEOUT_IDLE
    names = {cls.__name__ for cls in type(exc).__mro__}
    if "ConnectionTimeoutError" in names:
        return TIMEOUT_CONNECT
    if "SocketTimeoutError" in names:
        return TIMEOUT_IDLE
    if isinstance(exc, TimeoutError):
        return TIMEOUT_TOTAL
    return ENDPOINT_UNREACHABLE
```

- [ ] **Step 4: Run the tests and confirm they pass.**

```bash
python -m pytest tests/test_provider_model_adapter.py -v
```
Expected: all passed.

- [ ] **Step 5: Commit.**

```bash
git add scripts/factory_core/providers/model_adapter.py tests/test_provider_model_adapter.py
git commit -m "feat(#558): local response checks and §4.6 interim failure classification"
```

---

### Task 6: `model_proxy.py` — ledger columns, adapter identity, correlation-header redaction

**Files:**
- Modify: `scripts/factory_core/model_proxy.py`
- Test: `tests/test_factory_model_proxy.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_factory_model_proxy.py`:

```python
# --- #558: ledger columns and redaction --------------------------------------------------

def _bare_row(**kw):
    return mp.build_ledger_row(
        endpoint="/v1/messages", method="POST", model="m", status=200, duration_ms=1,
        input_tokens=1, output_tokens=1, cache_read_tokens=0, cache_creation_tokens=0,
        tool_count=0, tool_bytes=0, system_bytes=0, request_bytes=0, largest_tools=[],
        streamed=False, **kw,
    )


def test_ledger_row_defaults_are_the_cloud_path_values():
    row = _bare_row()
    assert row["hosting_location"] == "cloud"
    assert row["adapter_id"] == "model_proxy/anthropic-messages-passthrough" == mp.ADAPTER_ID
    for key in ("endpoint_id", "role", "request_id", "failure_class", "payload_decision"):
        assert row[key] is None
    assert row["correlation"] == "unknown"


def test_ledger_row_carries_the_local_columns():
    decision = {"payload_class": "local_only", "endpoint_id": "local-test",
                "secret_reference": "LOCAL_GATEWAY_TOKEN", "allow": True, "reason": "ok"}
    row = _bare_row(endpoint_id="local-test", hosting_location="local", role="actor",
                    request_id="r-1", failure_class="timeout_idle",
                    correlation="inferred_pointer", payload_decision=decision)
    assert (row["endpoint_id"], row["hosting_location"], row["role"], row["request_id"]) == (
        "local-test", "local", "actor", "r-1")
    assert row["failure_class"] == "timeout_idle" and row["correlation"] == "inferred_pointer"
    assert row["payload_decision"] == decision


def test_correlation_state_follows_the_pointer_file():
    assert mp._correlation_state({"run_id": "unknown"}) == "unknown"
    assert mp._correlation_state({"run_id": "run-9"}) == "inferred_pointer"


def test_redact_headers_redacts_the_correlation_header():
    redacted = mp.redact_headers({"X-Factory-Correlation": '{"run_id":"r"}', "X-Factory-Persona": "p"})
    assert redacted == {"X-Factory-Correlation": "[REDACTED]", "X-Factory-Persona": "p"}
```

- [ ] **Step 2: Run the tests and confirm they fail.**

```bash
python -m pytest tests/test_factory_model_proxy.py -k "ledger_row_defaults or local_columns or correlation_state or correlation_header" -v
```
Expected: 4 failed, 1 passed (`KeyError: 'hosting_location'`, `TypeError: build_ledger_row()
got an unexpected keyword argument 'endpoint_id'`, `AttributeError: … '_correlation_state'`,
and an assertion on the un-redacted correlation header; the `-k` also selects the existing
`test_build_ledger_row_defaults_when_correlation_missing`, which passes).

- [ ] **Step 3: Implement.** In `scripts/factory_core/model_proxy.py`:

  (a) Below `SEQ_URL = …` add:

```python
# Adapter identity on every ledger row (#558): which code path produced the row.
ADAPTER_ID = "model_proxy/anthropic-messages-passthrough"
```

  (b) Replace `_REDACT_EXACT = {"authorization", "x-api-key", "api-key"}` with

```python
# x-factory-correlation carries no secret, but §4.3 puts every correlation header in the
# redaction set; its fields are already ledger columns (#558).
_REDACT_EXACT = {"authorization", "x-api-key", "api-key", "x-factory-correlation"}
```

  (c) Replace `build_ledger_row`'s signature (the lines from `def build_ledger_row(` through
  `    stage=None,\n) -> dict:`) and append the new keys. The new signature is:

```python
def build_ledger_row(
    *, endpoint, method, model, status, duration_ms, input_tokens, output_tokens,
    cache_read_tokens, cache_creation_tokens, tool_count, tool_bytes, system_bytes,
    request_bytes, largest_tools, streamed, run_id=None, issue_number=None, intent=None,
    stage=None, endpoint_id=None, hosting_location="cloud", role=None, request_id=None,
    failure_class=None, correlation="unknown", adapter_id=ADAPTER_ID, payload_decision=None,
) -> dict:
```
  and directly after the existing `"retry_count": 0,` entry (still inside the returned dict)
  add:
```python
        # Request/Result contract columns (#558, contract §4.7). None/"cloud" on the default
        # path; the model column above is the backend model (a local row's served_name).
        "endpoint_id": endpoint_id,
        "hosting_location": hosting_location,
        "role": role,
        "request_id": request_id,
        "failure_class": failure_class,
        "correlation": correlation,
        "adapter_id": adapter_id,
        "payload_decision": payload_decision,
```

  (d) Below `read_current_run()` add:

```python
def _correlation_state(run: dict) -> str:
    """§4.4: the current-run.json pointer is today's only correlation source — never upgraded."""
    return "unknown" if run["run_id"] == "unknown" else "inferred_pointer"
```

- [ ] **Step 4: Run the tests and confirm they pass.**

```bash
python -m pytest tests/test_factory_model_proxy.py -v
```
Expected: all passed. The existing tests are unmodified and still pass.

- [ ] **Step 5: Commit.**

```bash
git add scripts/factory_core/model_proxy.py tests/test_factory_model_proxy.py
git commit -m "feat(#558): Request/Result ledger columns, adapter identity, correlation-header redaction"
```

---

### Task 7: `model_proxy.py` — `create_app(destination)` and `main()` startup

**Files:**
- Modify: `scripts/factory_core/model_proxy.py`
- Test: `tests/test_factory_model_proxy.py`

- [ ] **Step 1: Write the failing tests.** In the top-of-file imports of
  `tests/test_factory_model_proxy.py`, add `import copy` after `import asyncio` and `import yaml`
  after `import pytest`. Below the existing
  `from factory_core import model_proxy as mp` line, add:

```python
from factory_core.providers import model_adapter as ma  # noqa: E402
from factory_core.providers import model_endpoint as me  # noqa: E402

SENTINEL = "sk-sentinel-local-gateway-token"
SERVED = "qwen2.5-coder-32b"
LOCAL_ENTRY = {
    "id": "local-test", "hosting_location": "local", "protocol": "anthropic-messages",
    "accepts_payload_classes": ["local_only"],
    "model_identity": {"served_name": SERVED, "digest_or_revision": "sha256:" + "ab" * 32},
    "base_url": "http://127.0.0.1:9", "secret_reference": "LOCAL_GATEWAY_TOKEN",
    "timeouts": {"connect_seconds": 2, "time_to_first_token_seconds": 2, "idle_seconds": 2,
                 "total_seconds": 10},
}
CLOUD_ENTRY = {
    "id": "cloud-test", "hosting_location": "cloud", "protocol": "anthropic-messages",
    "accepts_payload_classes": ["cloud_ok"],
    "model_identity": {"served_name": "claude-opus-5-5", "digest_or_revision": "claude-opus-5-5"},
    "secret_reference": "ANTHROPIC_API_KEY",
}
```
  Then append:

```python
# --- #558: proxy startup -----------------------------------------------------------------

@pytest.fixture
def startup(monkeypatch):
    """main() with web.run_app() recorded instead of serving, and a clean selection env."""
    served = []
    monkeypatch.setattr(mp.web, "run_app", lambda app, **kw: served.append(app))
    monkeypatch.setattr(mp, "sweep_raw_artifacts", lambda: None)
    for name in (*me.CLOUD_CREDENTIALS, *me.TIER_ALIASES, "ANTHROPIC_BASE_URL",
                 "MODEL_PROXY_UPSTREAM", "LOCAL_GATEWAY_TOKEN", "FACTORY_MODEL_ENDPOINT",
                 "FACTORY_MODEL_REGISTRY_SHA256", "FACTORY_MODEL_REGISTRY_PATH"):
        monkeypatch.delenv(name, raising=False)
    return served


def _stage_registry(tmp_path, monkeypatch, endpoints):
    reg = tmp_path / "staged"
    reg.mkdir()
    (reg / me.REGISTRY_FILE).write_text(
        yaml.safe_dump({"schema_version": "1.0", "endpoints": endpoints}, sort_keys=False),
        encoding="utf-8")
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_PATH", str(reg))
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", me.tree_sha256(reg))
    return reg


def _main_exit(capsys):
    with pytest.raises(SystemExit) as exc:
        mp.main()
    assert exc.value.code == 1
    return capsys.readouterr().err


def test_create_app_defaults_to_no_destination():
    assert mp.create_app()["local_destination"] is None


def test_unset_endpoint_serves_the_cloud_default_without_loading_the_adapter(startup, monkeypatch):
    monkeypatch.delitem(sys.modules, "factory_core.providers.model_adapter", raising=False)
    mp.main()
    assert len(startup) == 1 and startup[0]["local_destination"] is None
    assert "factory_core.providers.model_adapter" not in sys.modules


def test_a_verified_cloud_entry_starts_with_its_destination(startup, tmp_path, monkeypatch):
    _stage_registry(tmp_path, monkeypatch, [CLOUD_ENTRY, LOCAL_ENTRY])
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "cloud-test")
    mp.main()
    dest = startup[0]["local_destination"]
    assert dest["endpoint_id"] == "cloud-test" and dest["hosting_location"] == "cloud"
    assert dest["upstream"] is None


def test_digest_mismatch_fails_startup_before_binding(startup, tmp_path, monkeypatch, capsys):
    reg = _stage_registry(tmp_path, monkeypatch, [CLOUD_ENTRY])
    (reg / me.REGISTRY_FILE).write_text(
        yaml.safe_dump({"schema_version": "1.0", "endpoints": [CLOUD_ENTRY, dict(CLOUD_ENTRY, id="cloud-extra")]}),
        encoding="utf-8")
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "cloud-test")
    assert "does not match FACTORY_MODEL_REGISTRY_SHA256" in _main_exit(capsys)
    assert startup == []


def test_unset_digest_fails_startup(startup, tmp_path, monkeypatch, capsys):
    _stage_registry(tmp_path, monkeypatch, [CLOUD_ENTRY])
    monkeypatch.delenv("FACTORY_MODEL_REGISTRY_SHA256")
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "cloud-test")
    assert "FACTORY_MODEL_REGISTRY_SHA256 is not set" in _main_exit(capsys)
    assert startup == []


def test_a_local_entry_still_fails_startup_on_no_local_dispatch(startup, tmp_path, monkeypatch, capsys):
    _stage_registry(tmp_path, monkeypatch, [LOCAL_ENTRY])
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "local-test")
    assert "no local-eligible dispatch exists yet" in _main_exit(capsys)
    assert startup == []


@pytest.mark.parametrize("setup,needle", [
    (lambda mpatch: mpatch.setenv("MODEL_PROXY_UPSTREAM", "https://api.anthropic.com"), "MODEL_PROXY_UPSTREAM"),
    (lambda mpatch: mpatch.setenv("ANTHROPIC_API_KEY", "cloud-sentinel"), "ANTHROPIC_API_KEY"),
    (lambda mpatch: mpatch.delenv("LOCAL_GATEWAY_TOKEN"), "LOCAL_GATEWAY_TOKEN"),
])
def test_fixture_fed_local_startup_failures_exit_before_binding(startup, monkeypatch, capsys, setup, needle):
    # A fixture entry stands in for the loader: a real local entry never passes it (#587).
    monkeypatch.setattr(me, "load_verified_entry", lambda path, eid: ([], copy.deepcopy(LOCAL_ENTRY)))
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "local-test")
    monkeypatch.setenv("LOCAL_GATEWAY_TOKEN", SENTINEL)
    setup(monkeypatch)
    err = _main_exit(capsys)
    assert needle in err and SENTINEL not in err and "cloud-sentinel" not in err
    assert startup == []


def test_payload_decision_allow_false_fails_startup(startup, monkeypatch, capsys):
    entry = dict(copy.deepcopy(LOCAL_ENTRY), accepts_payload_classes=["cloud_ok"])
    monkeypatch.setattr(me, "load_verified_entry", lambda path, eid: ([], entry))
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "local-test")
    monkeypatch.setenv("LOCAL_GATEWAY_TOKEN", SENTINEL)
    assert "payload decision does not allow dispatch" in _main_exit(capsys)
    assert startup == []
```

- [ ] **Step 2: Run the tests and confirm they fail.**

```bash
python -m pytest tests/test_factory_model_proxy.py -k "startup or create_app_defaults or cloud_default or cloud_entry_starts" -v
```
Expected: failed. `KeyError: 'local_destination'` for the default/`create_app` tests. The
others fail because `main()` never exits (`Failed: DID NOT RAISE <class 'SystemExit'>`) or
because the destination is missing.

- [ ] **Step 3: Implement.** In `scripts/factory_core/model_proxy.py`:

  (a) Extend the module docstring by appending a blank line and then this paragraph, before
  the closing `"""`:

```
Local destination (#558; docs/archive/2026-11-25-hybrid-runtime-adapter-design.md):
when FACTORY_MODEL_ENDPOINT is set, main() resolves the endpoint once at startup through
providers.model_adapter — never providers.preflight(), which needs GH_TOKEN — and exits
non-zero on any problem before binding. A local entry's upstream is its verified base_url;
no code path reaches the cloud default. Interfaces this process must hold for a local
selection: FACTORY_MODEL_ENDPOINT, FACTORY_MODEL_REGISTRY_SHA256, MODEL_PROXY_UPSTREAM
(optional; must equal the entry's base_url when set), ANTHROPIC_BASE_URL and the four
ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU,FABLE}_MODEL aliases (checked by the loader), and the
variable named by the entry's secret_reference. The proxy service receives none of these
today; delivering them is #587's.
```

  (b) Add `import sys` to the stdlib imports (between `import re` and `import time`).

  (c) Replace `create_app()`'s first lines

```python
def create_app() -> web.Application:
    app = web.Application()
    app["client_session"] = None
```
  with
```python
def create_app(destination=None) -> web.Application:
    app = web.Application()
    app["client_session"] = None
    # The endpoint resolved once at startup (#558); None on the cloud-default path.
    app["local_destination"] = destination
```

  (d) Replace `main()` with:

```python
def main() -> None:
    destination = None
    endpoint_id = os.environ.get("FACTORY_MODEL_ENDPOINT")
    if endpoint_id:
        # Lazy: the default cloud path never loads the adapter or registry code (#558).
        from factory_core.providers import model_adapter, model_endpoint
        try:
            destination = model_adapter.resolve_local_destination(
                model_endpoint.staged_registry_dir(), endpoint_id)
        except model_adapter.DestinationError as e:
            for problem in e.problems:
                print(f"factory-model-proxy: {problem}", file=sys.stderr)
            sys.exit(1)
    sweep_raw_artifacts()
    web.run_app(create_app(destination), port=PORT)
```

- [ ] **Step 4: Run the tests and confirm they pass.**

```bash
python -m pytest tests/test_factory_model_proxy.py -v
```
Expected: all passed.

- [ ] **Step 5: Commit.**

```bash
git add scripts/factory_core/model_proxy.py tests/test_factory_model_proxy.py
git commit -m "feat(#558): resolve and verify the selected endpoint once at proxy startup"
```

---

### Task 8: `model_proxy.handle_proxy()` — local branch and the shared failure path

**Files:**
- Modify: `scripts/factory_core/model_proxy.py`
- Test: `tests/test_factory_model_proxy.py`

- [ ] **Step 1: Write the failing local-forwarding and credential tests.** Append to
  `tests/test_factory_model_proxy.py`:

```python
# --- #558: local request path (hermetic; destination built from a fixture entry) ----------

SSE_OK = (b'event: message_start\ndata: {"type":"message_start","message":{"usage":'
          b'{"input_tokens":7,"output_tokens":1}}}\n\n'
          b'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":3}}\n\n'
          b'event: message_stop\ndata: {"type":"message_stop"}\n\n')
# Whatever the client sees once the proxy aborts a prepared response: never a clean EOF.
_TRUNCATED = (aiohttp.ClientPayloadError, aiohttp.ServerDisconnectedError, aiohttp.ClientOSError)


@pytest.fixture
def local_env(tmp_path, monkeypatch):
    for name in (*me.CLOUD_CREDENTIALS, "MODEL_PROXY_UPSTREAM"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LOCAL_GATEWAY_TOKEN", SENTINEL)
    monkeypatch.setattr(mp, "LEDGER_PATH", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(mp, "CURRENT_RUN_PATH", tmp_path / "current-run.json")
    monkeypatch.setattr(mp, "post_seq_ledger", lambda r: None)
    return tmp_path


def _url(server):
    return f"http://{server.host}:{server.port}"


def _local_destination(base_url, **timeouts):
    """Req. 2: a local entry never comes back from load_verified_entry() while
    _NO_LOCAL_DISPATCH stands, so tests build the destination from a fixture entry."""
    entry = copy.deepcopy(LOCAL_ENTRY)
    entry["base_url"] = base_url
    entry["timeouts"].update(timeouts)
    return ma.destination_from_entry(entry)


async def _local_client(base_url, **timeouts):
    client = TestClient(TestServer(mp.create_app(_local_destination(base_url, **timeouts))))
    await client.start_server()
    return client


def _rows(tmp_path):
    path = tmp_path / "ledger.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def _recording(handler, calls):
    async def wrapped(request):
        calls.append({"method": request.method, "path_qs": request.path_qs,
                      "headers": {k.lower(): v for k, v in request.headers.items()}})
        return await handler(request)
    return wrapped


async def _ok_json(request):
    return web.json_response({"type": "message", "usage": {"input_tokens": 10, "output_tokens": 5}})


async def _ok_sse(request):
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await resp.prepare(request)
    await resp.write(SSE_OK)
    await resp.write_eof()
    return resp


def _local_scenario(local_env, monkeypatch, handler, send, *, timeouts=None, rows=1, until=None):
    """Run send(client) against a local destination whose upstream is handler; return
    (send's result, upstream calls, cloud-upstream calls). The cloud UPSTREAM is a second fake
    server that must never be reached from a local-configured adapter (Req. 3). Waits for
    `until` (default: `rows` ledger rows) before the servers close."""
    calls, cloud_calls = [], []

    async def scenario():
        upstream = await _make_upstream(_recording(handler, calls))
        cloud = await _make_upstream(_recording(_ok_json, cloud_calls))
        monkeypatch.setattr(mp, "UPSTREAM", _url(cloud))
        client = await _local_client(_url(upstream), **(timeouts or {}))
        try:
            result = await send(client)
            await _wait_for(until or (lambda: len(_rows(local_env)) >= rows))
        finally:
            await client.close()
            await upstream.close()
            await cloud.close()
        return result

    result = _run(scenario())
    return result, calls, cloud_calls


async def _post_json(client, path="/v1/messages", **body):
    resp = await client.post(path, json={"model": SERVED, "messages": [], **body})
    return resp.status, await resp.json()


def test_local_request_reaches_only_the_entry_base_url_with_the_scoped_credential(local_env, monkeypatch):
    async def send(client):
        resp = await client.post("/v1/messages", json={"model": SERVED, "messages": []}, headers={
            "Authorization": "Bearer inbound", "x-api-key": "inbound-key", "api-key": "inbound-2",
            "X-Factory-Correlation": '{"role":"checker"}'})
        return resp.status, await resp.json()

    (status, body), calls, cloud_calls = _local_scenario(local_env, monkeypatch, _ok_json, send)
    assert status == 200 and body["usage"]["input_tokens"] == 10
    assert cloud_calls == [] and len(calls) == 1
    headers = calls[0]["headers"]
    assert headers["x-api-key"] == SENTINEL
    assert "authorization" not in headers and "api-key" not in headers
    envelope = json.loads(headers["x-factory-correlation"])
    assert envelope["role"] == "actor" and envelope["endpoint_id"] == "local-test"
    row = _rows(local_env)[0]
    assert (row["hosting_location"], row["endpoint_id"], row["role"]) == ("local", "local-test", "actor")
    assert row["request_id"] == envelope["request_id"]
    assert row["model"] == SERVED and row["adapter_id"] == mp.ADAPTER_ID
    assert row["failure_class"] is None and row["correlation"] == "unknown"
    assert row["payload_decision"] == {
        "payload_class": "local_only", "endpoint_id": "local-test",
        "secret_reference": "LOCAL_GATEWAY_TOKEN", "allow": True,
        "reason": "effective class accepted by endpoint"}
    assert SENTINEL not in (local_env / "ledger.jsonl").read_text()


def test_local_correlation_uses_the_pointer_file(local_env, monkeypatch):
    (local_env / "current-run.json").write_text(json.dumps(
        {"run_id": "run-9", "issue_number": 558, "intent": "plan", "stage": "plan"}))
    _, calls, _ = _local_scenario(local_env, monkeypatch, _ok_json, _post_json)
    envelope = json.loads(calls[0]["headers"]["x-factory-correlation"])
    assert (envelope["run_id"], envelope["issue"], envelope["phase"]) == ("run-9", 558, "plan")
    assert _rows(local_env)[0]["correlation"] == "inferred_pointer"


def test_local_streaming_is_forwarded_and_ledgered(local_env, monkeypatch):
    async def send(client):
        resp = await client.post("/v1/messages", json={"model": SERVED, "stream": True, "messages": []})
        return await resp.read()

    body, _, _ = _local_scenario(local_env, monkeypatch, _ok_sse, send)
    assert body == SSE_OK
    row = _rows(local_env)[0]
    assert row["streamed"] is True and row["failure_class"] is None
    assert row["gen_ai.usage.output_tokens"] == 3


def test_local_raw_artifact_redacts_forwarded_credential_and_correlation(local_env, monkeypatch):
    monkeypatch.setattr(mp, "RAW_ARTIFACT_CAPTURE", True)
    artifacts = local_env / "artifacts"
    monkeypatch.setattr(mp, "RAW_ARTIFACT_DIR", artifacts)
    _local_scenario(local_env, monkeypatch, _ok_json, _post_json,
                    until=lambda: artifacts.exists() and any(artifacts.rglob("*.json")))
    text = next(artifacts.rglob("*.json")).read_text()
    persisted = json.loads(text)
    forwarded = {k.lower(): v for k, v in persisted["forwarded_request_headers"].items()}
    assert forwarded["x-api-key"] == "[REDACTED]"
    assert forwarded["x-factory-correlation"] == "[REDACTED]"
    assert SENTINEL not in text and '"request_id"' not in text


def test_local_raw_artifacts_stay_opt_in(local_env, monkeypatch):
    monkeypatch.setattr(mp, "RAW_ARTIFACT_CAPTURE", False)
    monkeypatch.setattr(mp, "RAW_ARTIFACT_DIR", local_env / "artifacts")
    _local_scenario(local_env, monkeypatch, _ok_json, _post_json)
    assert not (local_env / "artifacts").exists()
```

- [ ] **Step 2: Write the failing refusal tests.** Append:

```python
def test_local_redirect_is_refused_and_never_followed(local_env, monkeypatch):
    async def redirect(request):
        if request.path == "/elsewhere":
            return web.json_response({"usage": {}})
        raise web.HTTPFound("/elsewhere")

    (status, body), calls, cloud_calls = _local_scenario(local_env, monkeypatch, redirect, _post_json)
    assert status == 502
    assert body["error"]["type"] == ma.REFUSAL_TYPE
    assert body["error"]["failure_class"] == ma.PROTOCOL_INCOMPATIBILITY
    assert [c["path_qs"] for c in calls] == ["/v1/messages"] and cloud_calls == []
    assert _rows(local_env)[0]["failure_class"] == ma.PROTOCOL_INCOMPATIBILITY


@pytest.mark.parametrize("model", ["claude-opus-5-5", "qwen2.5-coder-7b"])
def test_local_model_identity_mismatch_is_refused_403(local_env, monkeypatch, model):
    async def send(client):
        resp = await client.post("/v1/messages", json={"model": model, "messages": []})
        return resp.status, await resp.json()

    (status, body), calls, _ = _local_scenario(local_env, monkeypatch, _ok_json, send)
    assert status == 403 and calls == []
    assert body["error"]["failure_class"] == ma.DESTINATION_POLICY_DENIAL
    assert body["error"]["endpoint_id"] == "local-test"
    row = _rows(local_env)[0]
    assert row["failure_class"] == ma.DESTINATION_POLICY_DENIAL
    # Req. 13: role is "actor" on every local row, refused ones included; nothing was signed.
    assert row["role"] == "actor" and row["request_id"] is None


@pytest.mark.parametrize("extra", [
    {"tool_choice": {"type": "any"}},
    {"tool_choice": {"type": "tool", "name": "Bash"}},
    {"tools": [{"name": "Bash", "input_schema": {}, "defer_loading": True}]},
    {"output_config": {"format": {"type": "json_schema", "schema": {}}}},
    {"output_format": {"type": "json_schema", "schema": {}}},
])
def test_local_unsupported_modes_are_refused_before_any_outbound_call(local_env, monkeypatch, extra):
    async def send(client):
        return await _post_json(client, **extra)

    (status, body), calls, cloud_calls = _local_scenario(local_env, monkeypatch, _ok_json, send)
    assert status == 400 and calls == [] and cloud_calls == []
    assert body["error"]["type"] == ma.REFUSAL_TYPE
    assert body["error"]["failure_class"] == ma.PROTOCOL_INCOMPATIBILITY
    row = _rows(local_env)[0]
    assert row["status"] == 400 and row["role"] == "actor" and row["hosting_location"] == "local"


@pytest.mark.parametrize("method,path", [("GET", "/v1/messages"), ("POST", "/v1/messages/count_tokens")])
def test_local_unsupported_endpoints_are_refused(local_env, monkeypatch, method, path):
    async def send(client):
        resp = await client.request(method, path, json={"model": SERVED, "messages": []})
        return resp.status, await resp.json()

    (status, body), calls, _ = _local_scenario(local_env, monkeypatch, _ok_json, send)
    assert status == 400 and calls == []
    assert body["error"]["failure_class"] == ma.PROTOCOL_INCOMPATIBILITY


def test_local_query_string_on_messages_is_forwarded(local_env, monkeypatch):
    async def send(client):
        return await _post_json(client, path="/v1/messages?beta=true")

    (status, _), calls, _ = _local_scenario(local_env, monkeypatch, _ok_json, send)
    assert status == 200 and calls[0]["path_qs"] == "/v1/messages?beta=true"


def test_cloud_path_still_passes_every_endpoint_through(tmp_path, monkeypatch):
    monkeypatch.setattr(mp, "LEDGER_PATH", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(mp, "CURRENT_RUN_PATH", tmp_path / "current-run.json")
    monkeypatch.setattr(mp, "post_seq_ledger", lambda r: None)
    calls = []

    async def scenario():
        upstream = await _make_upstream(_recording(_ok_json, calls))
        monkeypatch.setattr(mp, "UPSTREAM", _url(upstream))
        client = TestClient(TestServer(mp.create_app()))
        await client.start_server()
        for method, path in (("GET", "/v1/messages"), ("POST", "/v1/messages/count_tokens")):
            resp = await client.request(method, path, json={"model": "claude-opus-5-5"},
                                        headers={"X-Factory-Correlation": "client-value"})
            assert resp.status == 200
        await _wait_for(lambda: len(_rows(tmp_path)) >= 2)
        await client.close()
        await upstream.close()

    _run(scenario())
    assert [(c["method"], c["path_qs"]) for c in calls] == [
        ("GET", "/v1/messages"), ("POST", "/v1/messages/count_tokens")]
    # Default-path parity: forwarded headers are untouched — nothing added, nothing stripped.
    assert calls[0]["headers"]["x-factory-correlation"] == "client-value"
    row = _rows(tmp_path)[0]
    assert row["hosting_location"] == "cloud" and row["adapter_id"] == mp.ADAPTER_ID
    assert (row["endpoint_id"], row["role"], row["request_id"], row["failure_class"],
            row["payload_decision"]) == (None, None, None, None, None)


def test_local_endpoint_unreachable_fails_closed_without_retrying_elsewhere(local_env, monkeypatch):
    cloud_calls = []

    async def scenario():
        cloud = await _make_upstream(_recording(_ok_json, cloud_calls))
        monkeypatch.setattr(mp, "UPSTREAM", _url(cloud))
        client = await _local_client("http://127.0.0.1:1")  # nothing listens here
        resp = await client.post("/v1/messages", json={"model": SERVED, "messages": []})
        result = resp.status, await resp.json()
        await _wait_for(lambda: len(_rows(local_env)) >= 1)
        await client.close()
        await cloud.close()
        return result

    status, body = _run(scenario())
    assert status == 504 and cloud_calls == []
    assert body["error"]["type"] == "factory_model_proxy_upstream_error"
    assert body["error"]["failure_class"] == ma.ENDPOINT_UNREACHABLE
    assert body["error"]["endpoint_id"] == "local-test"
    assert SENTINEL not in json.dumps(body)


def test_local_not_ready_upstream_is_forwarded_and_classified(local_env, monkeypatch):
    async def not_ready(request):
        return web.json_response({"error": {"type": "loading_model"}}, status=503)

    (status, body), _, cloud_calls = _local_scenario(local_env, monkeypatch, not_ready, _post_json)
    assert status == 503 and body["error"]["type"] == "loading_model" and cloud_calls == []
    assert _rows(local_env)[0]["failure_class"] == ma.ENDPOINT_UNREACHABLE
```

- [ ] **Step 3: Write the failing stream/timeout/cancellation tests.** Append:

```python
async def _expect_truncated(client, **body):
    with pytest.raises(_TRUNCATED):
        resp = await client.post("/v1/messages", json={"model": SERVED, "messages": [], **body})
        await resp.read()


def test_local_partial_stream_is_aborted_not_completed(local_env, monkeypatch):
    async def partial(request):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b'event: content_block_delta\ndata: {"type":"content_bl')
        await resp.write_eof()  # a clean upstream EOF after a partial frame
        return resp

    _local_scenario(local_env, monkeypatch, partial, lambda c: _expect_truncated(c, stream=True))
    assert _rows(local_env)[0]["failure_class"] == ma.PROTOCOL_INCOMPATIBILITY


def test_local_upstream_disconnect_mid_stream_is_aborted(local_env, monkeypatch):
    async def dropped(request):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b'event: message_start\ndata: {"type":"message_start"}\n\n')
        request.transport.abort()
        return resp

    _local_scenario(local_env, monkeypatch, dropped, lambda c: _expect_truncated(c, stream=True))
    assert _rows(local_env)[0]["failure_class"] == ma.ENDPOINT_UNREACHABLE


def test_local_malformed_json_is_refused_with_a_real_error(local_env, monkeypatch):
    async def malformed(request):
        return web.Response(body=b'{"type":"message","usage":{"input_tok',
                            content_type="application/json")

    (status, body), _, _ = _local_scenario(local_env, monkeypatch, malformed, _post_json)
    assert status == 502 and body["error"]["failure_class"] == ma.PROTOCOL_INCOMPATIBILITY


async def _silent(request):
    await asyncio.sleep(5)
    return web.json_response({})


def test_local_first_byte_after_idle_but_within_ttft_succeeds(local_env, monkeypatch):
    # Before the first byte only time_to_first_token applies — idle (0.2s) must not cap it.
    async def slow_first_byte(request):
        await asyncio.sleep(0.5)
        return web.json_response({"type": "message", "usage": {"input_tokens": 1, "output_tokens": 1}})

    (status, _), _, _ = _local_scenario(
        local_env, monkeypatch, slow_first_byte, _post_json,
        timeouts={"idle_seconds": 0.2, "time_to_first_token_seconds": 3})
    assert status == 200 and _rows(local_env)[0]["failure_class"] is None


def test_local_first_chunk_just_inside_ttft_succeeds(local_env, monkeypatch):
    # Spec Open Question: the boundary case — the first chunk arrives just under the bound.
    async def near_bound(request):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await asyncio.sleep(0.8)
        await resp.write(SSE_OK)
        await resp.write_eof()
        return resp

    async def send(client):
        resp = await client.post("/v1/messages", json={"model": SERVED, "stream": True, "messages": []})
        return await resp.read()

    body, _, _ = _local_scenario(local_env, monkeypatch, near_bound, send,
                                 timeouts={"idle_seconds": 0.2, "time_to_first_token_seconds": 1.5})
    assert body == SSE_OK and _rows(local_env)[0]["failure_class"] is None


def test_local_first_byte_past_ttft_is_its_own_class(local_env, monkeypatch):
    (status, body), _, _ = _local_scenario(
        local_env, monkeypatch, _silent, _post_json,
        timeouts={"idle_seconds": 3, "time_to_first_token_seconds": 0.2})
    assert status == 504 and body["error"]["failure_class"] == ma.TIMEOUT_TIME_TO_FIRST_TOKEN


def test_local_first_event_past_ttft_after_headers_aborts(local_env, monkeypatch):
    async def headers_then_silence(request):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await asyncio.sleep(5)
        return resp

    _local_scenario(local_env, monkeypatch, headers_then_silence,
                    lambda c: _expect_truncated(c, stream=True),
                    timeouts={"idle_seconds": 3, "time_to_first_token_seconds": 0.2})
    assert _rows(local_env)[0]["failure_class"] == ma.TIMEOUT_TIME_TO_FIRST_TOKEN


def test_local_stall_after_the_first_chunk_hits_the_idle_bound(local_env, monkeypatch):
    async def stall(request):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b'event: message_start\ndata: {"type":"message_start"}\n\n')
        await asyncio.sleep(5)
        return resp

    _local_scenario(local_env, monkeypatch, stall, lambda c: _expect_truncated(c, stream=True),
                    timeouts={"idle_seconds": 0.2, "time_to_first_token_seconds": 3})
    assert _rows(local_env)[0]["failure_class"] == ma.TIMEOUT_IDLE


def test_local_slow_but_complete_stream_finishes_within_total(local_env, monkeypatch):
    async def slow(request):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for frame in SSE_OK.split(b"\n\n")[:-1]:
            await asyncio.sleep(0.1)
            await resp.write(frame + b"\n\n")
        await resp.write_eof()
        return resp

    async def send(client):
        resp = await client.post("/v1/messages", json={"model": SERVED, "stream": True, "messages": []})
        return await resp.read()

    body, _, _ = _local_scenario(local_env, monkeypatch, slow, send,
                                 timeouts={"idle_seconds": 0.5, "time_to_first_token_seconds": 0.5,
                                           "total_seconds": 5})
    assert body == SSE_OK and _rows(local_env)[0]["failure_class"] is None


def _spy_on_handler(monkeypatch):
    outcomes = []
    real = mp.handle_proxy

    async def spy(request):
        try:
            result = await real(request)
        except asyncio.CancelledError:
            outcomes.append("cancelled")
            raise
        outcomes.append("returned")
        return result

    monkeypatch.setattr(mp, "handle_proxy", spy)  # create_app() routes to the module global
    return outcomes


def test_local_cancellation_writes_its_row_and_re_raises(local_env, monkeypatch):
    outcomes = _spy_on_handler(monkeypatch)

    async def first_then_wait(request):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b'event: message_start\ndata: {"type":"message_start"}\n\n')
        await asyncio.sleep(5)
        return resp

    async def send(client):
        resp = await client.post("/v1/messages", json={"model": SERVED, "stream": True, "messages": []})
        await resp.content.readany()
        resp.close()  # the client goes away mid-stream; TestServer cancels the handler task

    _local_scenario(local_env, monkeypatch, first_then_wait, send)
    assert _rows(local_env)[0]["failure_class"] == ma.CANCELLATION
    assert outcomes == ["cancelled"]


def test_cloud_mid_stream_failure_now_aborts_instead_of_completing(tmp_path, monkeypatch):
    # Shared failure path (Req. 12): the default path no longer returns a JSON body after
    # prepare(); its row keeps today's 504 and null failure_class (no endpoint selected).
    monkeypatch.setattr(mp, "LEDGER_PATH", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(mp, "CURRENT_RUN_PATH", tmp_path / "current-run.json")
    monkeypatch.setattr(mp, "post_seq_ledger", lambda r: None)

    async def dropped(request):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(b"event: message_start\n")
        request.transport.abort()
        return resp

    async def scenario():
        upstream = await _make_upstream(dropped)
        monkeypatch.setattr(mp, "UPSTREAM", _url(upstream))
        client = TestClient(TestServer(mp.create_app()))
        await client.start_server()
        with pytest.raises(_TRUNCATED):
            resp = await client.post("/v1/messages", json={"model": "m"})
            await resp.read()
        await _wait_for(lambda: len(_rows(tmp_path)) >= 1)
        await client.close()
        await upstream.close()

    _run(scenario())
    row = _rows(tmp_path)[0]
    assert row["status"] == 504 and row["failure_class"] is None
```

- [ ] **Step 4: Run the new tests and confirm they fail.**

```bash
python -m pytest tests/test_factory_model_proxy.py -k "local or cloud_path or cloud_mid_stream" -v
```
Expected: most of these fail, roughly 27 failed and 9 passed. Local requests go to the cloud
`UPSTREAM` (`cloud_calls` is non-empty and no `x-factory-correlation` header is set). Refusals
return `200`. The cloud mid-stream case does not raise `_TRUNCATED`. Some tests already pass,
because Tasks 6–7 make them hold or their request reaches a cloud fake that answers 200:
`test_cloud_path_still_passes_every_endpoint_through`, `test_local_raw_artifacts_stay_opt_in`,
`test_local_first_byte_after_idle_but_within_ttft_succeeds`, and six Task 6/7 tests the `-k`
also selects. Do not chase those.
Expect this pre-implementation run to take about 5 minutes: without local bounds, the timing
tests wait out their upstreams' 5s sleeps and their `_wait_for` deadlines.

- [ ] **Step 5: Implement the shared helpers.** In `scripts/factory_core/model_proxy.py`, add
  directly above `async def handle_proxy`:

```python
class _DownstreamGone(Exception):
    """The client side of a proxied response went away mid-write (classified cancellation)."""


# Every failure handle_proxy records as a ledger row; anything else is a bug and propagates.
_HANDLED_ERRORS = (aiohttp.ClientError, TimeoutError, OSError, _DownstreamGone,
                   asyncio.CancelledError)


def _error_body(error_type, message, failure_class, endpoint_id) -> dict:
    return {"error": {"type": error_type, "message": message,
                      "failure_class": failure_class, "endpoint_id": endpoint_id}}


def _decode_scan(scan: bytes, encoding: str) -> bytes:
    """The session runs auto_decompress=False (see _startup), so scans hold raw wire bytes —
    decompress a local copy for parsing; the client receives the untouched stream."""
    if encoding in ("gzip", "deflate"):
        try:
            return gzip.decompress(scan) if encoding == "gzip" else zlib.decompress(scan)
        except (OSError, zlib.error):
            pass  # ledger falls back to zero-usage rather than failing the request
    return scan


async def _write_downstream(response: web.StreamResponse, chunk: bytes) -> None:
    try:
        await response.write(chunk)
    except ConnectionError as exc:  # includes aiohttp.ClientConnectionResetError
        raise _DownstreamGone(str(exc)) from exc
```

- [ ] **Step 6: Replace `handle_proxy()`.** Replace the entire existing `handle_proxy` function
  (from `async def handle_proxy(request: web.Request) -> web.StreamResponse:` through its final
  `return response`) with:

```python
async def handle_proxy(request: web.Request) -> web.StreamResponse:
    start = time.monotonic()
    body_bytes = await request.read()
    measurements = _measure_request(body_bytes)
    model = ""
    try:
        model = (json.loads(body_bytes) if body_bytes else {}).get("model", "")
    except json.JSONDecodeError:
        pass

    correlation = read_current_run()
    dest = request.app["local_destination"]
    local = dest is not None and dest["hosting_location"] == "local"
    ma = None
    if dest is not None:
        # Lazy, as in main(): the default cloud path never loads the adapter (#558).
        from factory_core.providers import model_adapter as ma
    # Request/Result ledger columns (#558). role is the §4.2 contract constant on every local
    # row — refused rows included (spec Req. 13); request_id exists once a request is signed.
    columns = {
        "endpoint_id": dest["endpoint_id"] if dest else None,
        "hosting_location": dest["hosting_location"] if dest else "cloud",
        "role": ma.LOCAL_ROLE if local else None,
        "request_id": None,
        "correlation": _correlation_state(correlation),
        "payload_decision": dict(dest["payload_decision"]) if dest else None,
    }

    def _row(status, failure_class, *, usage=None, streamed=False):
        usage = usage or {}
        return build_ledger_row(
            endpoint=request.path, method=request.method, model=model, status=status,
            duration_ms=int((time.monotonic() - start) * 1000),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_read_tokens=0, cache_creation_tokens=0, streamed=streamed,
            request_bytes=len(body_bytes), **measurements,
            run_id=correlation["run_id"], issue_number=correlation["issue_number"],
            intent=correlation["intent"], stage=correlation["stage"],
            failure_class=failure_class, **columns,
        )

    if local:
        # Every refusal happens here, before any outbound call (spec Req. 9, 10, 4).
        try:
            ma.check_request(dest, request.method, request.path, body_bytes)
            fwd_headers = ma.strip_and_sign_headers(dict(request.headers), dest)
        except ma.AdapterRefusal as refusal:
            await _persist_ledger(_row(refusal.status, refusal.failure_class))
            return web.json_response(
                _error_body(ma.REFUSAL_TYPE, refusal.message, refusal.failure_class,
                            dest["endpoint_id"]),
                status=refusal.status)
        header_value, envelope = ma.build_correlation(dest, correlation)
        fwd_headers[ma.CORRELATION_HEADER] = header_value
        columns["role"], columns["request_id"] = envelope["role"], envelope["request_id"]
        bounds = ma.resolve_timeout(dest)
        # The verified entry's base_url — never UPSTREAM (spec Req. 3).
        upstream_url = f"{dest['upstream'].rstrip('/')}{request.path_qs}"
        # sock_read stays None: aiohttp runs it before the first byte too, which would cap
        # time_to_first_token at idle. Both ride the rolling deadline below (§4.6).
        timeout = aiohttp.ClientTimeout(total=bounds["total"], connect=bounds["connect"],
                                        sock_read=None)
        first_token_seconds, idle_seconds = bounds["time_to_first_token"], bounds["idle"]
        handled = _HANDLED_ERRORS + (ma.AdapterRefusal,)
    else:
        upstream_url = f"{UPSTREAM.rstrip('/')}{request.path_qs}"
        fwd_headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=10)
        first_token_seconds = idle_seconds = None
        handled = _HANDLED_ERRORS

    session = request.app["client_session"]
    loop = asyncio.get_running_loop()
    response = None
    usage_scan = bytearray()
    # One rolling upstream-wait deadline (§4.6): time_to_first_token until the first body
    # byte, then idle_seconds per wait between chunks; disarmed while a chunk is written
    # downstream. asyncio.timeout(None) is never re-armed on the cloud path, so never fires.
    deadline = asyncio.timeout(first_token_seconds)
    first_byte_seen = False

    def _got_chunk():
        nonlocal first_byte_seen
        first_byte_seen = True
        deadline.reschedule(None)

    def _await_next_chunk():
        if idle_seconds is not None:
            deadline.reschedule(loop.time() + idle_seconds)

    try:
        async with deadline:
            async with session.request(
                request.method, upstream_url, headers=fwd_headers, data=body_bytes,
                timeout=timeout, allow_redirects=not local,
            ) as upstream_resp:
                encoding = upstream_resp.headers.get("Content-Encoding", "").lower()
                is_streamed = "text/event-stream" in upstream_resp.headers.get("Content-Type", "")
                if local:
                    ma.check_response_status(upstream_resp.status)
                if local and not is_streamed:
                    # Buffered, so a malformed body is refused with a real error before prepare().
                    async for chunk in upstream_resp.content.iter_any():
                        _got_chunk()
                        usage_scan.extend(chunk)
                        _await_next_chunk()
                    deadline.reschedule(None)
                    ma.check_json_body(upstream_resp.status,
                                       _decode_scan(bytes(usage_scan), encoding))
                response = web.StreamResponse(
                    status=upstream_resp.status, headers=upstream_resp.headers
                )
                await response.prepare(request)
                if local and not is_streamed:
                    await _write_downstream(response, bytes(usage_scan))
                else:
                    async for chunk in upstream_resp.content.iter_any():
                        _got_chunk()
                        await _write_downstream(response, chunk)
                        usage_scan.extend(chunk)
                        _await_next_chunk()
                    deadline.reschedule(None)
                    if local:
                        ma.check_sse_complete(upstream_resp.status,
                                              _decode_scan(bytes(usage_scan), encoding))
                await response.write_eof()
    except handled as exc:
        prepared = response is not None and response.prepared
        if prepared and request.transport is not None:
            # No JSON error can follow a prepared response: abort without a clean EOF so the
            # client sees a truncated stream, never one that looks complete (spec Req. 12).
            request.transport.abort()
        failure_class = None
        if ma is not None:
            expired = deadline.expired()
            failure_class = ma.classify_failure(
                exc, ttft_expired=expired and not first_byte_seen,
                idle_expired=expired and first_byte_seen,
                downstream_gone=isinstance(exc, _DownstreamGone))
        refusal = ma is not None and isinstance(exc, ma.AdapterRefusal)
        status = exc.status if refusal else 504
        # Shielded: a cancelled handler still records its row (§4.6: never a silent hang).
        await asyncio.shield(_persist_ledger(_row(status, failure_class)))
        if isinstance(exc, asyncio.CancelledError):
            raise  # never swallowed
        if prepared:
            # The aborted response is all the client gets; aiohttp's finish_response() absorbs
            # the write_eof() ConnectionError quietly (re-raising would log a traceback).
            return response
        if refusal:
            return web.json_response(
                _error_body(ma.REFUSAL_TYPE, exc.message, failure_class, columns["endpoint_id"]),
                status=status)
        return web.json_response(
            _error_body("factory_model_proxy_upstream_error", f"upstream request failed: {exc}",
                        failure_class, columns["endpoint_id"]),
            status=504)

    scan_bytes = _decode_scan(bytes(usage_scan), encoding)
    if is_streamed:
        usage = _extract_usage_from_bytes(scan_bytes)
    else:
        try:
            usage = json.loads(scan_bytes).get("usage", {})
            usage = {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
            }
        except json.JSONDecodeError:
            usage = {"input_tokens": 0, "output_tokens": 0}

    failure_class = ma.status_failure_class(upstream_resp.status) if local else None
    await _persist_ledger(_row(upstream_resp.status, failure_class, usage=usage,
                               streamed=is_streamed))

    if RAW_ARTIFACT_CAPTURE:
        seq = int(time.time() * 1000)
        artifact = {
            "request_headers": redact_headers(dict(request.headers)),
            "request_path": request.path,
            "request_body": body_bytes.decode("utf-8", errors="replace"),
            "response_status": upstream_resp.status,
            "response_headers": redact_headers(dict(upstream_resp.headers)),
            # scan_bytes holds the full forwarded response (decompressed locally when
            # the upstream was gzip/deflate; the client got the raw wire bytes) —
            # requirement 6 asks for request/response artifacts, so persist both
            # sides, not request-only.
            "response_body": scan_bytes.decode("utf-8", errors="replace"),
        }
        if local:
            # What actually left for the local endpoint: signed and correlated, redacted (#558).
            artifact["forwarded_request_headers"] = redact_headers(fwd_headers)
        write_raw_artifact(correlation["run_id"], seq, artifact)
    return response
```

- [ ] **Step 7: Run the whole proxy test file and confirm it passes.**

```bash
python -m pytest tests/test_factory_model_proxy.py -v
```
Expected: all passed. The pre-existing tests, including the gzip and event-loop-blocking ones,
still pass unmodified. That is the default-cloud parity check.

- [ ] **Step 8: Run the proxy smoke and compose shell tests (CI parity).**

```bash
bash tests/test_model_proxy_smoke.sh && bash tests/test_model_proxy_compose.sh
```
Expected: both exit 0.

- [ ] **Step 9: Commit.**

```bash
git add scripts/factory_core/model_proxy.py tests/test_factory_model_proxy.py
git commit -m "feat(#558): local destination request path; abort-on-failure after prepare; cancellation recorded"
```

---

### Task 9: `run_record.py` — an unknown cost stays `null`

**Files:**
- Modify: `scripts/factory_core/run_record.py`
- Test: `tests/test_run_record.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_run_record.py`:

```python
# ---------------------------------------------------------------------------
# #558: an unknown (null) cost is never coerced to 0
# ---------------------------------------------------------------------------

def test_nodes_from_run_obj_keeps_an_explicit_null_cost():
    nodes = rr._nodes_from_run_obj({"nodes": [
        {"nodeId": "a", "costUsd": None, "inputTokens": 5},
        {"nodeId": "b", "inputTokens": 1},
        {"nodeId": "c", "costUsd": 0},
        {"nodeId": "d", "cost_usd": None},
        {"nodeId": "e", "costUsd": 0.25},
    ]})
    assert [n["cost_usd"] for n in nodes] == [None, 0, 0, None, 0.25]


def test_null_cost_node_is_cost_unknown_with_tokens_kept():
    nodes, missing = rr._classify_node_capture(
        [{"node_id": "implement", "cost_usd": None,
          "gen_ai.usage.input_tokens": 5, "gen_ai.usage.output_tokens": 1}],
        {"implement": "command"}, None)
    assert missing == []
    assert nodes[0]["cost_capture"] == "cost_unknown"
    assert nodes[0]["gen_ai.usage.input_tokens"] == 5


def test_sum_usage_nulls_the_cost_without_a_type_error():
    assert rr._sum_usage([{"cost_usd": 0.5, "gen_ai.usage.input_tokens": 1,
                           "gen_ai.usage.output_tokens": 1},
                          {"cost_usd": None, "gen_ai.usage.input_tokens": 2,
                           "gen_ai.usage.output_tokens": 3}]) == (3, 4, None)
    assert rr._sum_usage([{"cost_usd": 0.5}, {}]) == (0, 0, 0.5)


def _stub_node_maps(monkeypatch):
    monkeypatch.setattr(rr, "_load_workflow_node_maps", lambda clone_dir: ({"implement": "command", "review": "command"}, {}))
    monkeypatch.setattr(rr, "_parse_node_log", lambda log_file, tokens: None)
    monkeypatch.setattr(rr, "emit_health_event", lambda *a, **k: None)


def test_capture_totals_mixed_null_cost_keeps_the_known_lower_bound(monkeypatch):
    _stub_node_maps(monkeypatch)
    _, totals = rr._capture_totals(
        [{"node_id": "implement", "cost_usd": 0.5,
          "gen_ai.usage.input_tokens": 5, "gen_ai.usage.output_tokens": 1},
         {"node_id": "review", "cost_usd": None,
          "gen_ai.usage.input_tokens": 2, "gen_ai.usage.output_tokens": 2}],
        capture_ok=True, clone_dir=".", archon_log_file=None, issue=1, run_id="r")
    assert totals["cost_usd"] is None
    assert totals["known_cost_usd"] == pytest.approx(0.5)
    assert totals["gen_ai.usage.input_tokens"] == 7 and totals["known_output_tokens"] == 3


def test_capture_totals_null_cost_is_null_not_zero(monkeypatch):
    _stub_node_maps(monkeypatch)
    nodes, totals = rr._capture_totals(
        [{"node_id": "implement", "cost_usd": None,
          "gen_ai.usage.input_tokens": 5, "gen_ai.usage.output_tokens": 1}],
        capture_ok=True, clone_dir=".", archon_log_file=None, issue=1, run_id="r")
    assert totals["cost_usd"] is None and totals["known_cost_usd"] is None
    assert totals["gen_ai.usage.input_tokens"] == 5 and totals["known_input_tokens"] == 5
    assert totals["missing_nodes"] == []
```

- [ ] **Step 2: Run the tests and confirm they fail.**

```bash
python -m pytest tests/test_run_record.py -k "null_cost or explicit_null or sum_usage_nulls" -v
```
Expected: 5 failed. The nodes get `0` instead of `None`, `cost_capture` is `'completed'`,
`_sum_usage` raises `TypeError: unsupported operand type(s) for +: 'float' and 'NoneType'`,
and the two totals tests raise the same kind of `TypeError` (`'int' and 'NoneType'` for the
single-node case).

- [ ] **Step 3: Implement.** In `scripts/factory_core/run_record.py`:

  (a) Add above `_nodes_from_run_obj`:

```python
def _node_cost(n: dict):
    """A node's reported cost. A key present with a null value is an unknown cost (a
    local-routed node, #558, contract §4.10) and stays None; an absent key stays 0."""
    value = n.get("costUsd") or n.get("cost_usd")
    if value:
        return value
    if any(n.get(k) is not None for k in ("costUsd", "cost_usd")):
        return 0  # a reported zero stays a zero
    if "costUsd" in n or "cost_usd" in n:
        return None
    return 0
```
  and in `_nodes_from_run_obj` replace

```python
                "cost_usd": n.get("costUsd") or n.get("cost_usd") or 0,
```
  with
```python
                "cost_usd": _node_cost(n),
```

  (b) In `_classify_node_capture`, replace

```python
        else:
            node["cost_capture"] = "completed"
```
  with
```python
        elif node.get("cost_usd") is None:
            # Reported, but its cost is unknown (#558): tokens kept, and the null cost
            # nulls every cost total it joins instead of reading as 0.
            node["cost_capture"] = "cost_unknown"
        else:
            node["cost_capture"] = "completed"
```

  (c) Replace `_sum_usage` with:

```python
def _sum_usage(nodes: list) -> "tuple":
    # An unknown (null) cost is not zero (#558): any one makes the cost sum null.
    costs = [n.get("cost_usd", 0) for n in nodes]
    return (
        sum(n.get("gen_ai.usage.input_tokens", 0) for n in nodes),
        sum(n.get("gen_ai.usage.output_tokens", 0) for n in nodes),
        None if any(c is None for c in costs) else sum(costs),
    )
```

  (d) In `_capture_totals`, replace

```python
    known_in, known_out, known_cost = _sum_usage(
        [n for n in nodes if n.get("cost_capture") == "completed"]
    )
    complete = not missing
```
  with
```python
    counted = [n for n in nodes if n.get("cost_capture") in ("completed", "cost_unknown")]
    priced = [n for n in counted if n.get("cost_capture") == "completed"]
    known_in, known_out, _ = _sum_usage(counted)
    # A cost_unknown node (#558) is excluded from the known cost like a missing node; with no
    # priced node to bound it, the known cost is unknown (null) — never a fabricated 0.
    known_cost = _sum_usage(priced)[2] if priced or not counted else None
    cost_unknown = len(priced) != len(counted)
    complete = not missing
```
  and in the `totals.update({...})` that follows, replace

```python
        "cost_usd": known_cost if complete else None,
```
  with
```python
        "cost_usd": known_cost if complete and not cost_unknown else None,
```

- [ ] **Step 4: Run the whole run-record test file and confirm it passes.**

```bash
python -m pytest tests/test_run_record.py -v
```
Expected: all passed. The fully-cloud, fully-complete fixtures (e.g.
`test_assemble_incorporates_archon_cost`, `cost_per_task == 2.5`) are unchanged.

- [ ] **Step 5: Commit.**

```bash
git add scripts/factory_core/run_record.py tests/test_run_record.py
git commit -m "fix(#558): an unknown node cost stays null through run-record totals, never 0"
```

---

### Task 10: `run_record.py` — `model_dispatch` and the local-run cost override

**Files:**
- Modify: `scripts/factory_core/run_record.py`
- Test: `tests/test_run_record.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_run_record.py`:

```python
# ---------------------------------------------------------------------------
# #558: model_dispatch (Request/Result contracts) and the local-run cost override
# ---------------------------------------------------------------------------

LOCAL_DECISION = {"payload_class": "local_only", "endpoint_id": "local-test",
                  "secret_reference": "LOCAL_GATEWAY_TOKEN", "allow": True,
                  "reason": "effective class accepted by endpoint"}


def _dispatch_row(run_id="abc123", **kw):
    row = {**_ledger_row(run_id=run_id), "endpoint_id": None, "hosting_location": "cloud",
           "role": None, "request_id": None, "failure_class": None,
           "correlation": "inferred_pointer",
           "adapter_id": "model_proxy/anthropic-messages-passthrough", "payload_decision": None}
    row.update(kw)
    return row


def _local_row(**kw):
    return _dispatch_row(**{"endpoint_id": "local-test", "hosting_location": "local",
                            "role": "actor", "request_id": "r-1",
                            "payload_decision": dict(LOCAL_DECISION), **kw})


def test_model_dispatch_without_rows_is_unknown():
    md = rr._compute_model_dispatch([])
    assert md["request"] == {"schema_version": "1.0", "payload_class": None, "endpoint_id": None,
                             "secret_reference": None, "allow": None,
                             "reason": "no endpoint selected"}
    assert md["result"] == {"schema_version": "1.0", "outcome": "unknown", "endpoint_id": None,
                            "hosting_location": None, "role": None, "correlation": "unknown",
                            "request_count": 0, "failure_classes_seen": []}


def test_model_dispatch_cloud_parity():
    md = rr._compute_model_dispatch([_dispatch_row(), _dispatch_row()])
    assert md["request"]["reason"] == "no endpoint selected"
    assert md["result"] == {"schema_version": "1.0", "outcome": "ok", "endpoint_id": None,
                            "hosting_location": "cloud", "role": None,
                            "correlation": "inferred_pointer", "request_count": 2,
                            "failure_classes_seen": []}


def test_model_dispatch_copies_the_payload_decision_never_a_secret():
    md = rr._compute_model_dispatch([_local_row(), _local_row()])
    assert md["request"] == {"schema_version": "1.0", **LOCAL_DECISION}
    assert md["result"]["endpoint_id"] == "local-test" and md["result"]["role"] == "actor"
    assert md["result"]["hosting_location"] == "local" and md["result"]["outcome"] == "ok"


def test_model_dispatch_failures_and_unknown_correlation():
    md = rr._compute_model_dispatch([
        _local_row(failure_class="timeout_idle"), _local_row(failure_class="cancellation"),
        _local_row(failure_class="timeout_idle", correlation="unknown")])
    assert md["result"]["outcome"] == "failed"
    assert md["result"]["failure_classes_seen"] == ["cancellation", "timeout_idle"]
    assert md["result"]["correlation"] == "unknown"


def test_model_dispatch_two_endpoints_is_failed_with_null_endpoint():
    md = rr._compute_model_dispatch([_local_row(), _local_row(endpoint_id="local-other")])
    assert md["result"]["endpoint_id"] is None and md["result"]["outcome"] == "failed"


def _assemble_with_ledger(tmp_path, monkeypatch, rows, nodes):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setattr(rr, "LEDGER_PATH", ledger)
    cost_json = tmp_path / "cost.json"
    cost_json.write_text(json.dumps({"runId": "abc123", "nodes": nodes}))
    out = tmp_path / "run-record.json"
    args = _AssembleArgs(tmp_path, out)
    args.archon_cost_json = str(cost_json)
    rr.cmd_assemble(args)
    return json.loads(out.read_text())


def test_assemble_local_run_records_null_cost_never_zero(tmp_path, monkeypatch):
    rec = _assemble_with_ledger(tmp_path, monkeypatch, [_local_row()], [
        {"nodeId": "implement", "inputTokens": 100, "outputTokens": 50, "costUsd": 0.42,
         "durationMs": 1000, "modelUsage": {}},
        {"nodeId": "fetch-issue", "inputTokens": 0, "outputTokens": 0, "costUsd": 0,
         "durationMs": 10, "modelUsage": {}},
    ])
    assert [n["cost_usd"] for n in rec["nodes"]] == [None, None]
    assert rec["totals"]["cost_usd"] is None and rec["totals"]["known_cost_usd"] is None
    assert rec["harness_economics"]["cost_per_task"] is None
    assert rec["nodes"][0]["gen_ai.usage.input_tokens"] == 100
    assert rec["totals"]["gen_ai.usage.input_tokens"] == 100
    assert rec["model_dispatch"]["result"]["hosting_location"] == "local"
    assert rec["model_dispatch"]["request"] == {"schema_version": "1.0", **LOCAL_DECISION}
    assert "0.42" not in json.dumps(rec["totals"])


def test_assemble_cloud_run_keeps_its_cost_and_gains_model_dispatch(tmp_path, monkeypatch):
    rec = _assemble_with_ledger(tmp_path, monkeypatch, [_dispatch_row()], [
        {"nodeId": "implement", "inputTokens": 100, "outputTokens": 50, "costUsd": 0.01,
         "durationMs": 1000, "modelUsage": {}}])
    assert rec["totals"]["cost_usd"] == pytest.approx(0.01)
    assert rec["harness_economics"]["cost_per_task"] == pytest.approx(0.01)
    assert rec["harness_economics"]["ledger_rows_correlated"] == 1
    assert rec["model_dispatch"]["result"]["hosting_location"] == "cloud"
    assert rec["model_dispatch"]["result"]["role"] is None


def test_assemble_without_a_ledger_has_unknown_model_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")
    out = tmp_path / "run-record.json"
    rr.cmd_assemble(_AssembleArgs(tmp_path, out))
    rec = json.loads(out.read_text())
    assert rec["model_dispatch"]["result"]["outcome"] == "unknown"
    assert rec["harness_economics"]["ledger_available"] is False


def test_assemble_reads_the_ledger_once(tmp_path, monkeypatch):
    reads = []
    real = rr._read_ledger_rows
    monkeypatch.setattr(rr, "_read_ledger_rows", lambda run_id, path: reads.append(run_id) or real(run_id, path))
    _assemble_with_ledger(tmp_path, monkeypatch, [_dispatch_row()], [])
    assert reads == ["abc123"]
```

- [ ] **Step 2: Run the tests and confirm they fail.**

```bash
python -m pytest tests/test_run_record.py -k "model_dispatch or local_run or gains_model_dispatch or reads_the_ledger_once" -v
```
Expected: 8 failed, 1 passed. The tests raise `AttributeError: module 'factory_core.run_record'
has no attribute '_compute_model_dispatch'` or `KeyError: 'model_dispatch'`, and the local-run
node costs are `0.42`. `test_assemble_reads_the_ledger_once` already passes, because today's
`cmd_assemble` reads the ledger once inside `_compute_harness_economics()`. It pins that the
new early read does not add a second one.

- [ ] **Step 3: Implement `_compute_model_dispatch()`.** In `scripts/factory_core/run_record.py`,
  add after `_compute_harness_economics()`, directly before `NEEDS_DISCUSSION_LABEL = …`:

```python
# Request and Result contracts (contract §4.7), versioned independently.
MODEL_DISPATCH_REQUEST_VERSION = "1.0"
MODEL_DISPATCH_RESULT_VERSION = "1.0"
_PAYLOAD_DECISION_KEYS = ("payload_class", "endpoint_id", "secret_reference", "allow", "reason")


def _compute_model_dispatch(rows: list) -> dict:
    """run_record["model_dispatch"] (#558) from this run's request-ledger rows.

    request is the proxy's startup payload_decision() (its payload_decision column), copied
    from the first row — never recomputed here. result aggregates the rows: one endpoint_id or
    null (more than one is failed, never resolved to one), outcome ok/failed by failure class,
    unknown when the run has no rows.
    """
    decision = rows[0].get("payload_decision") if rows else None
    if isinstance(decision, dict):
        request = {"schema_version": MODEL_DISPATCH_REQUEST_VERSION, **decision}
    else:
        request = {"schema_version": MODEL_DISPATCH_REQUEST_VERSION,
                   **{k: None for k in _PAYLOAD_DECISION_KEYS}, "reason": "no endpoint selected"}
    if not rows:
        return {"request": request, "result": {
            "schema_version": MODEL_DISPATCH_RESULT_VERSION, "outcome": "unknown",
            "endpoint_id": None, "hosting_location": None, "role": None,
            "correlation": "unknown", "request_count": 0, "failure_classes_seen": []}}

    def _single(key):
        values = {r.get(key) for r in rows}
        return next(iter(values)) if len(values) == 1 else None

    endpoints_split = len({r.get("endpoint_id") for r in rows}) > 1
    failures = sorted({r["failure_class"] for r in rows if r.get("failure_class")})
    return {"request": request, "result": {
        "schema_version": MODEL_DISPATCH_RESULT_VERSION,
        "outcome": "failed" if failures or endpoints_split else "ok",
        "endpoint_id": _single("endpoint_id"),
        "hosting_location": _single("hosting_location"),
        "role": _single("role"),
        "correlation": ("inferred_pointer"
                        if all(r.get("correlation") == "inferred_pointer" for r in rows)
                        else "unknown"),
        "request_count": len(rows),
        "failure_classes_seen": failures,
    }}
```

- [ ] **Step 4: Let `_compute_harness_economics()` reuse rows that were already read.** Change
  its signature line

```python
    artifact_check: "dict | None" = None,
) -> dict:
```
  to
```python
    artifact_check: "dict | None" = None, ledger: "tuple | None" = None,
) -> dict:
```
  and replace

```python
    rows, ledger_available = _read_ledger_rows(run_id, ledger_path)
```
  with
```python
    # cmd_assemble passes the rows it already read (#558); backfill reads them here.
    rows, ledger_available = ledger if ledger is not None else _read_ledger_rows(run_id, ledger_path)
```

- [ ] **Step 5: Wire `cmd_assemble`.** In `cmd_assemble`, replace

```python
    # Capture failed -> totals null, not zero (df#300); otherwise classify every node's
    # cost capture (#453) — a measured zero (no AI nodes) still totals 0.
    nodes, totals = _capture_totals(
        nodes,
        capture_ok=bool(archon_cost_capture.get("ok")),
        clone_dir=getattr(args, "clone_dir", None) or ".",
        archon_log_file=getattr(args, "archon_log_file", None),
        issue=args.issue,
        run_id=args.run_id,
    )
```
  with
```python
    # The request ledger, read once (#558): it decides the local-run cost override below and
    # feeds harness_economics and model_dispatch.
    ledger_path = pathlib.Path(args.ledger_path) if args.ledger_path else LEDGER_PATH
    ledger_rows, ledger_available = _read_ledger_rows(args.run_id, ledger_path)
    local_run = any(r.get("hosting_location") == "local" for r in ledger_rows)

    # Capture failed -> totals null, not zero (df#300); otherwise classify every node's
    # cost capture (#453) — a measured zero (no AI nodes) still totals 0.
    nodes, totals = _capture_totals(
        nodes,
        capture_ok=bool(archon_cost_capture.get("ok")),
        clone_dir=getattr(args, "clone_dir", None) or ".",
        archon_log_file=getattr(args, "archon_log_file", None),
        issue=args.issue,
        run_id=args.run_id,
    )
    if local_run:
        # Contract §4.10: a local-routed run's cost is unknown — null, never the harness's
        # Claude-priced figure and never 0 (sum([]) included). Token counts are kept.
        nodes = [dict(n, cost_usd=None) for n in nodes]
        totals = dict(totals, cost_usd=None, known_cost_usd=None)
```
  Then replace the `harness_economics` assignment

```python
    run_record["harness_economics"] = _compute_harness_economics(
        run_id=args.run_id,
        status=run_record["status"],
        stages=stages,
        totals=run_record["totals"],
        started_at=run_record["started_at"],
        completed_at=run_record["completed_at"],
        ledger_path=pathlib.Path(args.ledger_path) if args.ledger_path else LEDGER_PATH,
        artifact_check=artifact_check,
    )
```
  with
```python
    run_record["harness_economics"] = _compute_harness_economics(
        run_id=args.run_id,
        status=run_record["status"],
        stages=stages,
        totals=run_record["totals"],
        started_at=run_record["started_at"],
        completed_at=run_record["completed_at"],
        ledger_path=ledger_path,
        artifact_check=artifact_check,
        ledger=(ledger_rows, ledger_available),
    )
    # Request/Result contracts (#558): additive, computed independently like harness_economics.
    run_record["model_dispatch"] = _compute_model_dispatch(ledger_rows)
```

- [ ] **Step 6: Run the run-record tests and confirm they pass.**

```bash
python -m pytest tests/test_run_record.py -v && bash tests/test_run_record_hermetic.sh
```
Expected: all passed; the hermetic script exits 0.

- [ ] **Step 7: Commit.**

```bash
git add scripts/factory_core/run_record.py tests/test_run_record.py
git commit -m "feat(#558): model_dispatch Request/Result record; local-run cost is null, never 0"
```

---

### Task 11: Rollout doc — supported and uncertified modes

**Files:**
- Modify: `docs/dark-factory-model-proxy-rollout.md`
- Test: `tests/test_factory_model_proxy.py`

- [ ] **Step 1: Write the failing doc test.** Append to `tests/test_factory_model_proxy.py`:

```python
def test_rollout_doc_lists_exactly_the_supported_and_refused_local_modes():
    doc = (Path(__file__).resolve().parents[1] / "docs" / "dark-factory-model-proxy-rollout.md"
           ).read_text(encoding="utf-8")
    section = doc.split("## Local destination: supported and uncertified modes (#558)", 1)[1]
    section = section.split("\n## ", 1)[0]
    for needle in (f"`{ma.SUPPORTED_METHOD} {ma.SUPPORTED_PATH}`", "count_tokens",
                   *(f"`{c}`" for c in ma.FORCED_TOOL_CHOICES), f"`{ma.DEFERRED_TOOL_FIELD}`",
                   *(f"`{f}`" for f in ma.STRUCTURED_OUTPUT_FIELDS), "served_name",
                   "uncertified", "/v1/chat/completions", *ma.FAILURE_CLASSES):
        assert needle in section, needle
```

- [ ] **Step 2: Run the test and confirm it fails.**

```bash
python -m pytest tests/test_factory_model_proxy.py -k rollout_doc -v
```
Expected: 1 failed with `IndexError: list index out of range` (no such section yet).

- [ ] **Step 3: Add the section.** In `docs/dark-factory-model-proxy-rollout.md`, insert
  directly before the `## Retention` heading:

```markdown
## Local destination: supported and uncertified modes (#558)

Spec: `docs/archive/2026-11-25-hybrid-runtime-adapter-design.md`. Nothing here is
reachable in production yet. #557's `_NO_LOCAL_DISPATCH` still rejects every local selection,
and the proxy service receives none of the variables a local selection needs. Delivering them
is #587's. The adapter is built and hermetically tested so #587 can enable it.

When `FACTORY_MODEL_ENDPOINT` is set, the proxy verifies the staged registry once at startup.
It recomputes the SHA-256 against `FACTORY_MODEL_REGISTRY_SHA256` and never re-reads the
registry afterwards. Any problem exits non-zero before the server binds. For a
`hosting_location: local` entry:

- **Destination.** Requests go only to the entry's verified `base_url`, never to the cloud
  default. A `MODEL_PROXY_UPSTREAM` that differs from `base_url` is a startup failure.
  Redirects are never followed: a 3xx is refused `502` `protocol_incompatibility`.
- **Credentials.** Inbound `authorization`, `x-api-key` and `api-key` headers are dropped. The
  outbound `x-api-key` comes only from the variable named by the entry's `secret_reference`.
  Any cloud model credential in the proxy's environment is a startup failure.
- **Accepted endpoint.** Only `POST /v1/messages` is accepted. A query string such as
  `?beta=true` is allowed. Any other path or method, including `POST /v1/messages/count_tokens`,
  is refused `400` `protocol_incompatibility` and never forwarded.
- **Supported.** Single-turn and multi-turn requests, tool-result continuation turns, and
  streaming (`text/event-stream`) and non-streaming responses.
- **Refused** with `400` `protocol_incompatibility`, before any outbound call:
  - forced tool choice: `tool_choice.type` of `any` or `tool`;
  - deferred tools: any `tools` entry carrying `defer_loading`;
  - structured output: `output_config` or `output_format` present.
- **Model identity.** A `model` other than the entry's `model_identity.served_name`, including
  any cloud model ID, is refused `403` `destination_policy_denial`. It is never remapped.
- **Responses.** A non-streaming 2xx body that is not a JSON object is refused `502`
  `protocol_incompatibility`. A streamed response that ends without a complete `message_stop`
  frame is aborted. Every failure after the response has started streaming aborts the
  connection without a clean end, so the client sees a truncated stream, never one that looks
  complete. A local `502`/`503`/`504` (not ready) is forwarded unchanged and recorded as
  `endpoint_unreachable`. Nothing is retried against this or any other endpoint. Only `gzip`
  and `deflate` response encodings are decoded for these checks: a local endpoint answering
  with another `Content-Encoding` (for example `br` or `zstd`) is treated as malformed.
- **Timeouts.** The entry's `connect_seconds` and `total_seconds` are aiohttp `ClientTimeout`
  bounds. `time_to_first_token_seconds` bounds the wait for the first body byte, and
  `idle_seconds` bounds every later wait between chunks. Both share one rolling deadline.
  aiohttp's `sock_read` is not used on this path: it also runs before the first byte, so it
  would cap time-to-first-token at the idle bound.
- **Failure classes** (contract §4.6 interim enumeration; #238's names supersede these):
  `endpoint_unreachable`, `timeout_connect`, `timeout_time_to_first_token`, `timeout_idle`,
  `timeout_total`, `cancellation`, `protocol_incompatibility`, `capability_evidence_invalid`
  (startup only) and `destination_policy_denial`. Refusals use the error envelope
  `{"error": {"type": "factory_model_adapter_refused", "message", "failure_class",
  "endpoint_id"}}`.
- **Telemetry.** Every ledger row carries `endpoint_id`, `hosting_location`, `role`,
  `request_id`, `failure_class`, `correlation`, `adapter_id` and `payload_decision`, and its
  `model` column is the backend model. On a refused request that column holds the model the
  client asked for, because nothing was forwarded. The outbound `X-Factory-Correlation` header is
  redacted from raw artifacts, which stay opt-in (`RAW_ARTIFACT_CAPTURE`) and bounded by
  retention. The run record gains `model_dispatch`, and a local-routed run records
  `cost_usd: null`, never 0.

Everything else about a local runtime is **uncertified** until #561/#562 ship capability
evidence. That includes Claude agent compatibility, subagents, prompt caching, extended
thinking, and any field not listed above. Compatibility with `/v1/chat/completions` is not
evidence of Claude agent compatibility, and this adapter does no protocol translation.
```

- [ ] **Step 4: Run the test and confirm it passes.**

```bash
python -m pytest tests/test_factory_model_proxy.py -k rollout_doc -v
```
Expected: 1 passed.

- [ ] **Step 5: Commit.**

```bash
git add docs/dark-factory-model-proxy-rollout.md tests/test_factory_model_proxy.py
git commit -m "docs(#558): local destination supported and uncertified modes"
```

---

### Task 12: Full verification and scope check

**Files:** none modified.

- [ ] **Step 1: Run the full Python suite.**

```bash
PYTHONPATH=scripts python -m pytest tests/ -q
```
Expected: all passed, 0 failed.

- [ ] **Step 2: Run the CI shell tests this change can affect, and CI's workflow checks.**

```bash
for t in tests/test_model_proxy_smoke.sh tests/test_model_proxy_compose.sh tests/test_run_record_hermetic.sh tests/test_cost_report_endpoint.sh tests/test_cost_report_harness_economics.sh; do bash "$t" >/dev/null 2>&1 && echo "PASS $t" || echo "FAIL $t"; done
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```
Expected: five `PASS` lines; the three checks exit 0. These are the exact commands
`.github/workflows/ci.yml` runs. Other `tests/test_*.sh` scripts need docker or a
CI-prepared `/var/lib/dark-factory`. If you run one and it fails, compare against `origin/main`
before acting.

- [ ] **Step 3: Confirm scope.** The diff touches only the files in the File Structure table:

```bash
git diff --name-only origin/main...HEAD -- . ':!docs/superpowers/specs' ':!docs/superpowers/plans' ':!.archon/memory'
```
Expected: exactly `docs/dark-factory-model-proxy-rollout.md`,
`scripts/factory_core/model_proxy.py`, `scripts/factory_core/providers/model_adapter.py`,
`scripts/factory_core/providers/model_endpoint.py`, `scripts/factory_core/run_record.py`,
`tests/test_factory_model_proxy.py`, `tests/test_provider_model_adapter.py`,
`tests/test_provider_model_endpoint.py` and `tests/test_run_record.py`. Nothing under
`deploy/**`, `gate_*`, breaker, budgets, `scheduler.sh`, `provider_env.sh`, `entrypoint.sh`,
`run-compose.yml` or `providers/__init__.py`.

- [ ] **Step 4: Confirm the AVOID memory lessons hold.**

```bash
grep -n "preflight(side" scripts/factory_core/model_proxy.py scripts/factory_core/providers/model_adapter.py; grep -n "^import aiohttp\|^from aiohttp" scripts/factory_core/providers/model_adapter.py
```
Expected: no output from either grep.

---

## Acceptance-criterion traceability

| Issue criterion / constraint | Where |
|---|---|
| Default-cloud parity; local-off unchanged | Tasks 6, 7 (`unset_endpoint…`), 8 (`cloud_path_still_passes…`, all pre-existing proxy tests unmodified), 9 Step 4 |
| Local path: hermetic HTTP/SSE incl. partial streams, malformed, disconnects, cancellation | Task 8 Step 3 |
| Unsupported forced-tool/deferred-tool/structured-output/endpoints surfaced explicitly | Tasks 3, 8 Step 2 |
| Credential custody; auth stripped/replaced; header + payload redaction tests | Tasks 2, 4, 8 Step 1 (`…scoped_credential`, `…raw_artifact_redacts…`) |
| Telemetry: endpoint, backend model, adapter identity; unknown/error states; raw artifacts opt-in and bounded | Tasks 6, 8 Step 1, 10 |
| Documented supported/uncertified modes | Task 11 |
| `cost_usd: null` for local-routed runs, even with a Claude-priced figure | Tasks 9, 10 |
| Live reachability fails closed and visibly; no cross-endpoint retry | Task 8 (`…unreachable_fails_closed…`, `…not_ready…`) |
| Ledger write of the payload decision; never a secret value | Tasks 2, 8 Step 1, 10 |
| Registry SHA-256 recomputed and matched before use | Tasks 1, 7 (`digest_mismatch_fails_startup…`) |
| Constraint 1 (one `read_verified_tree()`, no `_select`/`load_registry`) | Task 1 |
| Constraints 2–4 (startup, destination, credentials) | Tasks 2, 7, 8 |
| Constraints 8–9 (mid-stream abort, cancellation, timeouts) | Tasks 5, 8 |
| Constraint 11 (correlation header, `correlation` column, redaction) | Tasks 4, 6, 8 |

## Out of scope (spillover candidates, not implemented here)

- `cost_report.py` renders a null cost as `$0` (`format_cost(… or 0)`). The comment output for a
  local-routed run should say "unknown" instead. The spec does not list `cost_report.py`, so it
  is left unchanged; tracked as #608, a precondition of #587 (contract §4.10). #608 also covers
  the local-run override leaving `cost_capture: "completed"`/`"complete"` beside the null costs.
- Seq event properties (`post_seq_ledger`) do not gain the new ledger columns. The ledger file
  is the record of truth.
- Delivering the proxy's local-selection environment and reconciling `ANTHROPIC_BASE_URL` with
  proxy routing belong to #587.
