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

    def loader(path, eid, intent=None):
        seen.append((path, eid, intent))
        return [], copy.deepcopy(CLOUD)

    monkeypatch.setattr(me, "load_verified_entry", loader)
    assert ma.resolve_local_destination(tmp_path, "cloud-test")["endpoint_id"] == "cloud-test"
    assert seen == [(tmp_path, "cloud-test", None)]


def test_resolve_local_destination_forwards_the_intent(tmp_path, monkeypatch):
    # #634: the proxy's --intent reaches the loader unchanged, and nothing else does.
    seen = []

    def loader(path, eid, **kwargs):
        seen.append(kwargs)
        return [], copy.deepcopy(CLOUD)

    monkeypatch.setattr(me, "load_verified_entry", loader)
    ma.resolve_local_destination(tmp_path, "cloud-test", intent="classify-preview-helper")
    ma.resolve_local_destination(tmp_path, "cloud-test")
    assert seen == [{"intent": "classify-preview-helper"}, {"intent": None}]


def test_adapter_module_does_not_import_aiohttp():
    tree = ast.parse(Path(ma.__file__).read_text(encoding="utf-8"))
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
                for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree)
                 if isinstance(n, ast.ImportFrom) and n.module}
    assert "aiohttp" not in imported


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
    header, envelope = ma.build_correlation(dest, run, "actor")
    assert json.loads(header) == envelope
    assert set(envelope) == {"run_id", "issue", "phase", "role", "endpoint_id", "request_id"}
    assert envelope["run_id"] == "run-9" and envelope["issue"] == 558
    assert envelope["phase"] == "plan" and envelope["role"] == "actor"
    assert envelope["endpoint_id"] == "local-test"
    assert ma.build_correlation(dest, run, "actor")[1]["request_id"] != envelope["request_id"]
    # #559: the role is bound per dispatch by the caller, not a module constant.
    assert not hasattr(ma, "LOCAL_ROLE")


def test_resolve_timeout_maps_the_entry_bounds(monkeypatch):
    assert ma.resolve_timeout(_dest(monkeypatch)) == {
        "connect": 10, "time_to_first_token": 120, "idle": 60, "total": 1800}


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
