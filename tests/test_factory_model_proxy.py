import asyncio
import json
import os
import sys
import time
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core import model_proxy as mp


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
