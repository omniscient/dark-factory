import asyncio
import copy
import json
import os
import sys
import time
from pathlib import Path

import aiohttp
import pytest
import yaml
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core import model_proxy as mp
from factory_core.providers import model_adapter as ma  # noqa: E402
from factory_core.providers import model_endpoint as me  # noqa: E402
from factory_core import run_record as rr  # noqa: E402
from factory_core.providers import role_isolation as ri  # noqa: E402

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


def test_redact_headers_strips_known_secrets():
    headers = {
        "Authorization": "Bearer sk-ant-xyz",
        "X-Api-Key": "abc123",
        "api-key": "def456",
        "Content-Type": "application/json",
    }
    redacted = mp.redact_headers(headers)
    assert redacted["Authorization"] == "[REDACTED]"
    assert redacted["X-Api-Key"] == "[REDACTED]"
    assert redacted["api-key"] == "[REDACTED]"
    assert redacted["Content-Type"] == "application/json"


def test_redact_headers_strips_factory_secret_pattern():
    headers = {
        "X-Factory-Deploy-Token": "tok-1",
        "X-Factory-Github-Secret": "sec-1",
        "X-Factory-Persona": "implement",
    }
    redacted = mp.redact_headers(headers)
    assert redacted["X-Factory-Deploy-Token"] == "[REDACTED]"
    assert redacted["X-Factory-Github-Secret"] == "[REDACTED]"
    assert redacted["X-Factory-Persona"] == "implement"


def test_build_ledger_row_shape():
    # intent="fix" is a multi-phase intent (implement -> conformance -> code-review),
    # so the caller passes stage="unknown" — this row shows the honest degraded case.
    row = mp.build_ledger_row(
        endpoint="/v1/messages",
        method="POST",
        model="claude-sonnet-4-6-20251101",
        status=200,
        duration_ms=1234,
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        tool_count=3,
        tool_bytes=900,
        system_bytes=400,
        request_bytes=2000,
        largest_tools=[{"name": "Bash", "bytes": 500}],
        streamed=True,
        run_id="abc123",
        issue_number=208,
        intent="fix",
        stage="unknown",
    )
    assert row["endpoint"] == "/v1/messages"
    assert row["model"] == "claude-sonnet-4-6-20251101"
    assert row["status"] == 200
    assert row["gen_ai.usage.input_tokens"] == 100
    assert row["gen_ai.usage.output_tokens"] == 50
    assert row["tool_count"] == 3
    assert row["run_id"] == "abc123"
    assert row["issue_number"] == 208
    assert row["stage"] == "unknown"
    assert row["persona"] == "unknown"
    assert "timestamp" in row


def test_build_ledger_row_carries_single_phase_stage():
    # intent="plan" is single-phase — the whole container run IS the plan phase,
    # so the caller passes the exact stage through and it must be preserved verbatim.
    row = mp.build_ledger_row(
        endpoint="/v1/messages", method="POST", model="m", status=200,
        duration_ms=1, input_tokens=1, output_tokens=1, cache_read_tokens=0,
        cache_creation_tokens=0, tool_count=0, tool_bytes=0, system_bytes=0,
        request_bytes=0, largest_tools=[], streamed=False,
        run_id="abc123", issue_number=208, intent="plan", stage="plan",
    )
    assert row["stage"] == "plan"


def test_build_ledger_row_defaults_when_correlation_missing():
    row = mp.build_ledger_row(
        endpoint="/v1/messages", method="POST", model="", status=502,
        duration_ms=10, input_tokens=0, output_tokens=0,
        cache_read_tokens=0, cache_creation_tokens=0, tool_count=0,
        tool_bytes=0, system_bytes=0, request_bytes=0, largest_tools=[],
        streamed=False, run_id=None, issue_number=None, intent=None, stage=None,
    )
    assert row["run_id"] == "unknown"
    assert row["issue_number"] == 0
    assert row["intent"] == "unknown"
    assert row["stage"] == "unknown"


def test_append_ledger_writes_line(tmp_path, monkeypatch):
    path = tmp_path / "request-ledger.jsonl"
    monkeypatch.setattr(mp, "LEDGER_PATH", path)
    row = {"a": 1}
    mp.append_ledger(row)
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {"a": 1}


def test_append_ledger_rotates_at_max_bytes(tmp_path, monkeypatch):
    path = tmp_path / "request-ledger.jsonl"
    monkeypatch.setattr(mp, "LEDGER_PATH", path)
    monkeypatch.setattr(mp, "MAX_LEDGER_BYTES", 50)
    monkeypatch.setattr(mp, "BACKUP_COUNT", 2)

    for i in range(10):
        mp.append_ledger({"i": i, "pad": "x" * 20})

    assert path.exists()
    assert (tmp_path / "request-ledger.jsonl.1").exists()
    # rotation caps backups — no unbounded growth of rotation files
    assert not (tmp_path / "request-ledger.jsonl.3").exists()


def test_append_ledger_rotation_keeps_backup_count(tmp_path, monkeypatch):
    path = tmp_path / "request-ledger.jsonl"
    monkeypatch.setattr(mp, "LEDGER_PATH", path)
    monkeypatch.setattr(mp, "MAX_LEDGER_BYTES", 10)
    monkeypatch.setattr(mp, "BACKUP_COUNT", 1)

    for i in range(20):
        mp.append_ledger({"i": i})

    assert path.exists()
    assert (tmp_path / "request-ledger.jsonl.1").exists()
    assert not (tmp_path / "request-ledger.jsonl.2").exists()


def test_post_seq_ledger_is_nonfatal(monkeypatch):
    monkeypatch.setattr(mp, "SEQ_URL", "http://unreachable-host-99999:5341")
    row = mp.build_ledger_row(
        endpoint="/v1/messages", method="POST", model="m", status=200,
        duration_ms=1, input_tokens=1, output_tokens=1, cache_read_tokens=0,
        cache_creation_tokens=0, tool_count=0, tool_bytes=0, system_bytes=0,
        request_bytes=0, largest_tools=[], streamed=False,
    )
    mp.post_seq_ledger(row)  # must not raise


def test_write_raw_artifact_disabled_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(mp, "RAW_ARTIFACT_CAPTURE", False)
    monkeypatch.setattr(mp, "RAW_ARTIFACT_DIR", tmp_path)
    mp.write_raw_artifact("run1", 1, {"foo": "bar"})
    assert list(tmp_path.iterdir()) == []


def test_write_raw_artifact_enabled_writes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(mp, "RAW_ARTIFACT_CAPTURE", True)
    monkeypatch.setattr(mp, "RAW_ARTIFACT_DIR", tmp_path)
    mp.write_raw_artifact("run1", 1, {"foo": "bar"})
    out = tmp_path / "run1" / "1.json"
    assert out.exists()
    assert json.loads(out.read_text()) == {"foo": "bar"}


def test_sweep_raw_artifacts_removes_stale_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(mp, "RAW_ARTIFACT_DIR", tmp_path)
    monkeypatch.setattr(mp, "RAW_ARTIFACT_RETENTION_DAYS", 7)

    stale_dir = tmp_path / "old-run"
    stale_dir.mkdir()
    stale_file = stale_dir / "1.json"
    stale_file.write_text("{}")
    old_time = time.time() - (8 * 86400)
    os.utime(stale_file, (old_time, old_time))
    os.utime(stale_dir, (old_time, old_time))

    fresh_dir = tmp_path / "new-run"
    fresh_dir.mkdir()
    (fresh_dir / "1.json").write_text("{}")

    mp.sweep_raw_artifacts()

    assert not stale_dir.exists()
    assert fresh_dir.exists()


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _make_upstream(handler):
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    server = TestServer(app)
    await server.start_server()
    return server


async def _wait_for(predicate, timeout=2.0, interval=0.01):
    """Poll `predicate()` until truthy — ledger/artifact persistence now runs in
    a thread executor after the response is flushed, so callers can't assume
    it's done the instant client.post() returns; a fixed sleep is flaky under
    load, so poll with a generous timeout instead."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


def test_create_app_defers_client_session_to_on_startup():
    # aiohttp.ClientSession() binds to whatever event loop is current at
    # construction time. create_app() is called synchronously in main() before
    # web.run_app() creates and runs its own loop — building the session eagerly
    # here binds it to the wrong loop, breaking every proxied request. The
    # session must only be constructed once app.on_startup fires, inside the
    # loop that will actually serve requests.
    app = mp.create_app()
    assert app.get("client_session") is None

    async def scenario():
        server = TestServer(app)
        await server.start_server()
        assert isinstance(app["client_session"], aiohttp.ClientSession)
        await server.close()

    _run(scenario())


def test_proxy_forwards_non_streaming_response(tmp_path, monkeypatch):
    monkeypatch.setattr(mp, "LEDGER_PATH", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(mp, "CURRENT_RUN_PATH", tmp_path / "current-run.json")
    monkeypatch.setattr(mp, "post_seq_ledger", lambda r: None)

    async def upstream_handler(request):
        body = await request.json()
        assert body["model"] == "claude-sonnet-4-6-20251101"
        return web.json_response(
            {"usage": {"input_tokens": 10, "output_tokens": 5}}, status=200
        )

    async def scenario():
        upstream = await _make_upstream(upstream_handler)
        monkeypatch.setattr(mp, "UPSTREAM", f"http://{upstream.host}:{upstream.port}")
        app = mp.create_app()
        client = TestClient(TestServer(app))
        await client.start_server()
        resp = await client.post(
            "/v1/messages",
            json={"model": "claude-sonnet-4-6-20251101", "tools": [], "messages": []},
            headers={"x-api-key": "secret-key"},
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["usage"]["input_tokens"] == 10
        # Ledger persistence runs in a thread executor after the response is
        # flushed, so client.post() can return before that background write
        # finishes — wait for it rather than assuming it's already on disk.
        ledger_path = tmp_path / "ledger.jsonl"
        await _wait_for(lambda: ledger_path.exists() and ledger_path.stat().st_size > 0)
        await client.close()
        await upstream.close()

    _run(scenario())

    lines = (tmp_path / "ledger.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["status"] == 200
    assert row["model"] == "claude-sonnet-4-6-20251101"
    assert row["gen_ai.usage.input_tokens"] == 10
    assert row["gen_ai.usage.output_tokens"] == 5


def test_proxy_redacts_headers_before_persisting(tmp_path, monkeypatch):
    monkeypatch.setattr(mp, "LEDGER_PATH", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(mp, "CURRENT_RUN_PATH", tmp_path / "current-run.json")
    monkeypatch.setattr(mp, "post_seq_ledger", lambda r: None)
    monkeypatch.setattr(mp, "RAW_ARTIFACT_CAPTURE", True)
    monkeypatch.setattr(mp, "RAW_ARTIFACT_DIR", tmp_path / "artifacts")

    captured_upstream_headers = {}

    async def upstream_handler(request):
        captured_upstream_headers.update(request.headers)
        return web.json_response({"usage": {}}, status=200)

    async def scenario():
        upstream = await _make_upstream(upstream_handler)
        monkeypatch.setattr(mp, "UPSTREAM", f"http://{upstream.host}:{upstream.port}")
        app = mp.create_app()
        client = TestClient(TestServer(app))
        await client.start_server()
        await client.post(
            "/v1/messages", json={"model": "m"}, headers={"x-api-key": "super-secret"}
        )
        # Ledger persistence and the raw-artifact write happen after the response
        # is flushed to the client (persistence is offloaded to a thread executor,
        # so client.post() can return before that background work finishes) — wait
        # for the artifact rather than assuming it's already on disk.
        artifacts_dir = tmp_path / "artifacts"
        await _wait_for(lambda: any(artifacts_dir.rglob("*.json")) if artifacts_dir.exists() else False)
        await client.close()
        await upstream.close()

    _run(scenario())

    # The real secret still reached upstream (redaction is persistence-only)
    assert captured_upstream_headers.get("x-api-key") == "super-secret"

    artifact_files = list((tmp_path / "artifacts").rglob("*.json"))
    assert len(artifact_files) == 1
    persisted = json.loads(artifact_files[0].read_text())
    assert persisted["request_headers"]["x-api-key"] == "[REDACTED]"
    # Requirement 6 covers request AND response artifacts — assert both sides land.
    assert '"usage"' in persisted["response_body"]


def test_ledger_persistence_does_not_block_event_loop(tmp_path, monkeypatch):
    # append_ledger (flock + write) and post_seq_ledger (blocking urlopen) were
    # previously called directly in the async handler, stalling the whole event
    # loop — and therefore every other concurrent streaming request — for as
    # long as those calls took. They must be offloaded to a thread executor.
    monkeypatch.setattr(mp, "LEDGER_PATH", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(mp, "CURRENT_RUN_PATH", tmp_path / "current-run.json")
    monkeypatch.setattr(mp, "post_seq_ledger", lambda row: time.sleep(0.3))

    async def upstream_handler(request):
        return web.json_response({"usage": {}}, status=200)

    async def scenario():
        upstream = await _make_upstream(upstream_handler)
        monkeypatch.setattr(mp, "UPSTREAM", f"http://{upstream.host}:{upstream.port}")
        app = mp.create_app()
        client = TestClient(TestServer(app))
        await client.start_server()

        ticks = []

        async def ticker():
            for _ in range(10):
                ticks.append(time.monotonic())
                await asyncio.sleep(0.03)

        ticker_task = asyncio.ensure_future(ticker())
        resp = await client.post("/v1/messages", json={"model": "m"})
        assert resp.status == 200
        await ticker_task

        await client.close()
        await upstream.close()
        return ticks

    ticks = _run(scenario())
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert max(gaps) < 0.15, f"event loop stalled for {max(gaps):.3f}s — blocking call not offloaded"


def test_proxy_fails_closed_on_upstream_unreachable(tmp_path, monkeypatch):
    monkeypatch.setattr(mp, "LEDGER_PATH", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(mp, "CURRENT_RUN_PATH", tmp_path / "current-run.json")
    monkeypatch.setattr(mp, "post_seq_ledger", lambda r: None)
    monkeypatch.setattr(mp, "UPSTREAM", "http://127.0.0.1:1")  # nothing listens here

    async def scenario():
        app = mp.create_app()
        client = TestClient(TestServer(app))
        await client.start_server()
        resp = await client.post("/v1/messages", json={"model": "m"})
        assert resp.status in (502, 504)
        body = await resp.json()
        assert body["error"]["type"] == "factory_model_proxy_upstream_error"
        ledger_path = tmp_path / "ledger.jsonl"
        await _wait_for(lambda: ledger_path.exists() and ledger_path.stat().st_size > 0)
        await client.close()

    _run(scenario())

    lines = (tmp_path / "ledger.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["status"] in (502, 504)


def test_proxy_passes_gzip_response_through_untouched_and_still_ledgers_usage(
    tmp_path, monkeypatch
):
    """Regression for the auto_decompress finding on PR #270: a gzip upstream
    response must reach the client as the raw compressed bytes with its
    Content-Encoding header intact (no plaintext-under-gzip-header mismatch),
    while the ledger still extracts usage tokens from a locally decompressed
    scan copy."""
    import gzip as _gzip

    monkeypatch.setattr(mp, "LEDGER_PATH", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(mp, "CURRENT_RUN_PATH", tmp_path / "current-run.json")
    monkeypatch.setattr(mp, "post_seq_ledger", lambda r: None)

    payload = json.dumps(
        {"usage": {"input_tokens": 42, "output_tokens": 7}}
    ).encode()
    compressed = _gzip.compress(payload)

    async def upstream_handler(request):
        return web.Response(
            body=compressed,
            status=200,
            headers={
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
            },
        )

    async def scenario():
        upstream = await _make_upstream(upstream_handler)
        monkeypatch.setattr(mp, "UPSTREAM", f"http://{upstream.host}:{upstream.port}")
        app = mp.create_app()
        client = TestClient(TestServer(app))
        await client.start_server()
        # auto_decompress=False on the test client: we want to assert on the raw
        # wire bytes the proxy actually sent, not on aiohttp's client-side inflate.
        raw_session = aiohttp.ClientSession(auto_decompress=False)
        try:
            url = client.make_url("/v1/messages")
            async with raw_session.post(
                url, json={"model": "claude-sonnet-4-6-20251101", "messages": []}
            ) as resp:
                assert resp.status == 200
                assert resp.headers.get("Content-Encoding") == "gzip"
                body = await resp.read()
                assert body == compressed, "proxy must forward raw wire bytes"
                assert json.loads(_gzip.decompress(body))["usage"]["input_tokens"] == 42
        finally:
            await raw_session.close()
        ledger_path = tmp_path / "ledger.jsonl"
        await _wait_for(lambda: ledger_path.exists() and ledger_path.stat().st_size > 0)
        await client.close()
        await upstream.close()

    _run(scenario())

    row = json.loads((tmp_path / "ledger.jsonl").read_text().strip().splitlines()[0])
    assert row["status"] == 200
    assert row["gen_ai.usage.input_tokens"] == 42
    assert row["gen_ai.usage.output_tokens"] == 7


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
