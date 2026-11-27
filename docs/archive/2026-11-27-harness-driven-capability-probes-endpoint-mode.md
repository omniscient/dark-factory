# Harness-Driven Capability Probes Against a Real Endpoint — Implementation Plan

**Issue:** #631

> **For agentic workers:** execute the tasks in order. Each task ends green and is committed on
> its own. Use only the code in this plan; every block below was run in a scratch copy of the
> repository on 2026-11-27. That copy used `main` at `1628a6e`, aiohttp 3.14.5, Python 3.14 and
> the pinned CLI 2.1.282. There the full `tests/test_capability_suite.py` passed (216 tests), and a
> real `FACTORY_CAPABILITY_TIER2=1 … run --base-url mock` exited 0 in 31 s with all six
> dimensions `pass`.
>
> A plan review then executed every task literally on Python 3.12 (CI's version), with every
> stated red/green count observed as written, and added three SIGINT ordering tests to Task 4:
> the file now has 219 tests.

**Goal:** With Tier 2 on and the pinned CLI present, an endpoint-mode capability-suite run drives
the pinned `claude -p` against the real endpoint, through a local recording relay, for
`protocol_compatibility`, `tool_call_fidelity`, `context_length` and `timeout_cancellation`. The
run records a real `pass`/`fail` for each, so #562 can certify all six dimensions.

**Architecture:** A new `relay.py` sits between the spawned CLI and one upstream: the endpoint,
or the in-process mock. It forwards `POST /v1/messages` and `HEAD /api/hello`, records each
request in the mock's observation shape plus `response_model`/`completed`, never follows a
redirect, and opens a loopback proxy sink. That sink is the CLI's `HTTP(S)_PROXY`; it refuses
everything and records it. `harness.py` gains a relay spawn (`run_relayed`), four
`check_*_endpoint` functions and their pure judges. `runner.py` routes the new fixture kinds:

- four endpoint-target harness probes;
- four mock-target controls, which run in both modes;
- two fixed `not_tested` observations;
- the four legacy mock probes, recorded as `mechanism` records in endpoint mode.

`emit.py` adds `harness_sources`/`harness_probes` (schema `1.1`).

**Tech Stack:** Python 3 asyncio, aiohttp (server + client), PyYAML fixtures, pytest.

**Spec:** `docs/superpowers/specs/2026-11-27-harness-driven-capability-probes-endpoint-mode-design.md`

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `scripts/factory_core/capability_suite/relay.py` | Create | Recording relay + proxy sink (`Relayed`, `RELAYED`, `started()`, `watch()`, `BAD_GATEWAY_BODY`) |
| `scripts/factory_core/capability_suite/harness.py` | Modify | `RELAY_ENV_KEYS`, constants, `scrubbed_env(credential, proxy)`, `start_cli`/`run_cli` kwargs, `spawn_seconds`, `run_relayed`, `_naming_problem` params, `_strings` ids, `_without_assistant`, `_read_ids`, four endpoint judges, `_spawn_problem`, four `check_*_endpoint`, module docstring |
| `scripts/factory_core/capability_suite/runner.py` | Modify | `_CASE_KEYS` (`variant`), `Summary.harness_probes`/`harness_probe_sources`, `_validate()`, `HARNESS_RELAY_CHECKS`, `_harness_probe()`, `run_cases()`, `harness_sources()`, `exit_code` docstring, `main()` |
| `scripts/factory_core/capability_suite/emit.py` | Modify | `SCHEMA_VERSION = "1.1"`, `HARNESS_SOURCES`, `build_result(harness_sources, harness_probes)` |
| `scripts/factory_core/capability_suite/fixtures/<dim>/<case>/case.yaml` | Create ×10 | 4 endpoint probes, 4 controls, 2 fixed observations |
| `scripts/factory_core/providers/model_endpoint.py` | Modify | `PINNED_FIXTURE_SUITE_VERSION` value only |
| `tests/test_capability_suite.py` | Modify | New relay/harness/runner/emit tests; updated counts, `_result`, `_fake_harness` |
| `docs/configuration.md`, `docs/features.md` | Modify | Endpoint-mode harness probes, their limits, `mechanism`, schema 1.1 |

Not touched (spec "Files touched"): `mock_server.py`, `.github/workflows/ci.yml`, `Dockerfile`,
`scheduler.sh`, `entrypoint.sh`, `deploy/**`, `.github/workflows/publish.yml`, `config/`, any
`gate_*`, breaker, budgets, `model_proxy.py`, `model_adapter.py`, `role_isolation.py`,
`_NO_LOCAL_DISPATCH`, any registry/alias/routing surface. Committed files carry no endpoint
address, hostname or model tag: only `127.0.0.1`, `192.0.2.0/24` and placeholder names
(`local-model-a`, `api.example.invalid`).

## Conventions used by every task

**Test command.** From the repo root: `python -m pytest tests/test_capability_suite.py -q`. The
test file puts `scripts/` on `sys.path` itself; CI's `PYTHONPATH=scripts` is optional.

**Re-pin (R).** Every task that edits a file under `scripts/factory_core/capability_suite/`
changes `emit.suite_digest()`. `test_pinned_fixture_suite_version_matches_the_recomputed_digest`
and `test_mock_run_exits_0_with_streaming_and_role_isolation_pass` then fail until the pin is
recomputed (spec Requirement 12). Run this exact block as the task's re-pin step, after the
task's last package edit:

```bash
PYTHONPATH=scripts python3 - <<'EOF'
import re
from pathlib import Path
from factory_core.capability_suite import emit
path = Path("scripts/factory_core/providers/model_endpoint.py")
text, n = re.subn(r'^PINNED_FIXTURE_SUITE_VERSION = "[0-9a-f]{64}"$',
                  f'PINNED_FIXTURE_SUITE_VERSION = "{emit.suite_digest()}"',
                  path.read_text(), flags=re.M)
assert n == 1, "PINNED_FIXTURE_SUITE_VERSION line not found"
path.write_text(text)
print(emit.suite_digest())
EOF
```

Expected: one 64-character hex digest printed. Never hand-edit the value.

**Placement.** "Append" means at the end of the named file, after everything already there
(including what earlier tasks appended). New test code is appended in task order, so helpers an
earlier task defines (`SENTINEL`, `_cli_body`, `_tier2`, `_run`, `_endpoint_mock`, `SERVED`,
`_logging_claude`, …) exist before later tests use them.

**Commits.** One commit per task, message as given, ending with the
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` trailer.

---

## Task 1: Recording relay and proxy sink (`relay.py`)

**Files:**
- Create: `scripts/factory_core/capability_suite/relay.py`
- Modify: `tests/test_capability_suite.py` (imports; append relay tests)
- Modify: `scripts/factory_core/providers/model_endpoint.py` (re-pin)

- [ ] **Step 1: Extend the test imports.** In `tests/test_capability_suite.py` replace

```python
import shutil
import subprocess
```

with

```python
import shutil
import socket
import subprocess
```

then replace

```python
import aiohttp
import pytest
import yaml
```

with

```python
import aiohttp
import pytest
import yaml
from aiohttp import web
```

and replace

```python
from factory_core.capability_suite import emit, harness, mock_server, runner  # noqa: E402
```

with

```python
from factory_core.capability_suite import emit, harness, mock_server, relay, runner  # noqa: E402
```

- [ ] **Step 2: Append the relay tests** to `tests/test_capability_suite.py`. These cover the
  spec's relay test list:
  - loopback bind;
  - verbatim body and `x-api-key`;
  - dropped and added headers (including `FAULT_HEADER`);
  - `Content-Type`/`Retry-After` returned and any other header dropped;
  - chunked streaming before upstream EOF;
  - `response_model` and `completed`, including a `message_stop` split across chunks, spaced
    `"type" :` and an `event:`-only line;
  - an unknown path answered 404 without contacting the upstream;
  - a 3xx not followed and handed back without `Location`;
  - `dialled` holding only the upstream;
  - a refused upstream answered 502 and classified `endpoint_unreachable`;
  - an upstream stalled mid-stream giving `timeout_idle` and closing the CLI connection;
  - `watch()` resetting exactly the watched-request state;
  - a watched hang-up, with the stand-in upstream (the mock's `stall` script) observing it;
  - a watched stream that ends normally;
  - the sink recording and refusing.

```python
# --- recording relay (#631) -----------------------------------------------------------------

STREAM_OK = [b'event: message_start\n'
             b'data: {"type": "message_start", "message": {"model": "m"}}\n\n',
             b'event: message_stop\ndata: {"type": "message_stop"}\n\n']


async def _listener(handler):
    """A stand-in upstream on 127.0.0.1:<ephemeral> answering every path with `handler`."""
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    app_runner = web.AppRunner(app, handler_cancellation=True, shutdown_timeout=1.0)
    await app_runner.setup()
    await web.TCPSite(app_runner, "127.0.0.1", 0).start()
    return app_runner, f"http://127.0.0.1:{app_runner.addresses[0][1]}"


def _sse_upstream(chunks, seen=None, gate=None):
    """A handler streaming `chunks` (bytes) as text/event-stream; records each request's raw
    body and headers in `seen`; waits for `gate` (an asyncio.Event) after the first chunk."""
    async def handler(request):
        if seen is not None:
            seen.append({"body": await request.read(), "headers": dict(request.headers),
                         "path": request.path_qs})
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream",
                                           "Retry-After": "7", "X-Other": "dropped"})
        await resp.prepare(request)
        for i, chunk in enumerate(chunks):
            await resp.write(chunk)
            if i == 0 and gate is not None:
                await asyncio.wait_for(gate.wait(), 5)
        await resp.write_eof()
        return resp
    return handler


def _through_relay(handler, client, fault=None, timeouts=None):
    """client(session, relay url, relay app) against a relay whose upstream is a stand-in
    serving `handler`; returns (client's value, Relayed, upstream URL)."""
    async def go():
        up_runner, up_url = await _listener(handler)
        try:
            async with relay.started(up_url, timeouts=timeouts or runner.DEFAULT_TIMEOUTS,
                                     fault=fault) as (app, url):
                async with aiohttp.ClientSession() as session:
                    value = await client(session, url, app)
                await asyncio.sleep(0.1)
                return value, app[relay.RELAYED], up_url
        finally:
            await up_runner.cleanup()
    return asyncio.run(go())


async def _post_read(session, url, body=b'{"messages": []}', headers=None, path="/v1/messages"):
    async with session.post(url + path, data=body, headers=headers or {},
                            allow_redirects=False) as resp:
        return resp.status, dict(resp.headers), await resp.read()


def test_relay_and_sink_bind_loopback_only():
    async def client(session, url, app):
        return url, app[relay.RELAYED].sink_url
    (url, sink), _, _ = _through_relay(_sse_upstream(STREAM_OK), client)
    assert url.startswith("http://127.0.0.1:") and sink.startswith("http://127.0.0.1:")


def test_relay_forwards_body_and_credential_verbatim_and_rewrites_hop_headers():
    seen = []
    raw = b'{"model": "m",  "messages": [ ]}'

    async def client(session, url, app):
        return await _post_read(session, url, raw, path="/v1/messages?beta=true", headers={
            "x-api-key": SENTINEL, "anthropic-version": "2023-06-01",
            "Accept-Encoding": "gzip", "Keep-Alive": "5", "TE": "trailers",
            "Proxy-Connection": "keep-alive"})
    (status, headers, data), relayed, up_url = _through_relay(_sse_upstream(STREAM_OK, seen),
                                                              client, fault="wrong_model")
    assert status == 200 and data == b"".join(STREAM_OK)
    got = {k.lower(): v for k, v in seen[0]["headers"].items()}
    assert seen[0]["body"] == raw and seen[0]["path"] == "/v1/messages?beta=true"
    assert got["x-api-key"] == SENTINEL and got["anthropic-version"] == "2023-06-01"
    assert got["accept-encoding"] == "identity"
    assert not {"keep-alive", "te", "proxy-connection"} & set(got)
    assert got["host"] == up_url.split("//", 1)[1]
    assert got[mock_server.FAULT_HEADER.lower()] == "wrong_model"
    assert headers["Content-Type"] == "text/event-stream" and headers["Retry-After"] == "7"
    assert "X-Other" not in headers
    assert relayed.dialled == [up_url.split("//", 1)[1]]
    record = relayed.requests[0]
    assert record["headers"]["x-api-key"] == SENTINEL and record["status"] == 200
    assert record["body"] == {"model": "m", "messages": []}


def test_relay_streams_each_chunk_before_the_upstream_finishes():
    gate = asyncio.Event()

    async def client(session, url, app):
        async with session.post(url + "/v1/messages", data=b"{}") as resp:
            first = await asyncio.wait_for(resp.content.readany(), 5)
            gate.set()  # the upstream sends its second chunk only now
            return first + await resp.read()
    data, _, _ = _through_relay(_sse_upstream(STREAM_OK, gate=gate), client)
    assert data == b"".join(STREAM_OK)


@pytest.mark.parametrize("chunks, model, completed", [
    (STREAM_OK, "m", True),
    ([STREAM_OK[0], b'event: message_stop\ndata: {"type" :  "mess', b'age_stop"}\n\n'],
     "m", True),
    ([STREAM_OK[0], b"event: message_stop\n\n"], "m", False),
    ([b'data: {"type": "message_start", "message": {"model": 7}}\n\n'], None, False),
])
def test_relay_records_response_model_and_completion(chunks, model, completed):
    async def client(session, url, app):
        return await _post_read(session, url)
    _, relayed, _ = _through_relay(_sse_upstream(chunks), client)
    assert relayed.requests[0]["response_model"] == model
    assert relayed.requests[0]["completed"] is completed


def test_relay_answers_an_unknown_path_404_without_contacting_the_upstream():
    seen = []

    async def client(session, url, app):
        async with session.get(url + "/v1/models") as resp:
            return resp.status
    status, relayed, _ = _through_relay(_sse_upstream(STREAM_OK, seen), client)
    assert status == 404 and seen == [] and relayed.dialled == []
    assert relayed.requests[0]["status"] == 404 and relayed.requests[0]["path"] == "/v1/models"


def test_relay_never_follows_a_redirect_and_drops_its_location():
    async def go():
        hits = []

        async def second(request):
            hits.append(request.path)
            return web.Response()
        second_runner, second_url = await _listener(second)

        async def redirect(request):
            return web.Response(status=307, headers={"Location": second_url + "/v1/messages"})
        first_runner, first_url = await _listener(redirect)
        try:
            async with relay.started(first_url, timeouts=runner.DEFAULT_TIMEOUTS) as (app, url):
                async with aiohttp.ClientSession() as session:
                    status, headers, _ = await _post_read(session, url)
                return status, headers, hits, app[relay.RELAYED].dialled, first_url
        finally:
            await first_runner.cleanup()
            await second_runner.cleanup()
    status, headers, hits, dialled, first_url = asyncio.run(go())
    assert status == 307 and "Location" not in headers
    assert hits == [] and dialled == [first_url.split("//", 1)[1]]


def test_relay_answers_a_refused_upstream_502_and_classifies_it():
    async def go():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))  # bound and never listening: every connect is refused
            upstream = f"http://127.0.0.1:{sock.getsockname()[1]}"
            async with relay.started(upstream, timeouts=runner.DEFAULT_TIMEOUTS) as (app, url):
                async with aiohttp.ClientSession() as session:
                    status, _, data = await _post_read(session, url)
                return status, json.loads(data), app[relay.RELAYED]
    status, body, relayed = asyncio.run(go())
    assert status == 502 and body == relay.BAD_GATEWAY_BODY
    assert relayed.upstream_errors == ["endpoint_unreachable"]
    assert relayed.upstream_failed.is_set() and relayed.requests[0]["status"] == 502


def test_relay_hangs_up_on_the_cli_when_the_upstream_stalls_mid_stream():
    async def stall(request):
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(STREAM_OK[0])
        await asyncio.sleep(3)
        return resp

    async def client(session, url, app):
        with pytest.raises(aiohttp.ClientPayloadError):
            await _post_read(session, url)
        return True
    _, relayed, _ = _through_relay(stall, client, timeouts=dict(runner.DEFAULT_TIMEOUTS,
                                                                idle_seconds=0.3))
    assert relayed.upstream_errors == ["timeout_idle"] and relayed.upstream_failed.is_set()


def _marked(marker):
    return json.dumps({"messages": [{"role": "user", "content": [
        {"type": "text", "text": f"{marker} write"}]}]}).encode()


def test_watch_resets_only_the_watched_request_state():
    relayed = relay.Relayed(requests=[{"x": 1}], upstream_errors=["timeout_idle"],
                            other_host=["CONNECT x:443 HTTP/1.1"], client_gone=True,
                            upstream_done=True, upstream_closed=True)
    relayed.first_chunk.set()
    app = web.Application()
    app[relay.RELAYED] = relayed
    assert relay.watch(app, "m:") is relayed
    assert relayed.marker == "m:" and not relayed.first_chunk.is_set()
    assert not (relayed.client_gone or relayed.upstream_done or relayed.upstream_closed)
    assert relayed.requests == [{"x": 1}] and relayed.upstream_errors == ["timeout_idle"]
    assert relayed.other_host == ["CONNECT x:443 HTTP/1.1"]


def test_relay_sees_a_watched_client_hang_up_and_the_upstream_sees_it_too():
    async def go():
        mock_runner, mock_url = await mock_server.start()
        try:
            observed = mock_server.arm(mock_runner.app, mock_server.Script("stall"))
            async with relay.started(mock_url, timeouts=runner.DEFAULT_TIMEOUTS) as (app, url):
                relayed = relay.watch(app, "m 1:")
                async with aiohttp.ClientSession() as session:
                    resp = await session.post(url + "/v1/messages", data=_marked("m 1:"))
                    await asyncio.wait_for(relayed.first_chunk.wait(), 5)
                    resp.close()  # the client hangs up mid-stream
                for _ in range(50):
                    if relayed.client_gone and observed.disconnected:
                        break
                    await asyncio.sleep(0.1)
                return relayed, observed.disconnected
        finally:
            await mock_runner.cleanup()
    relayed, upstream_saw_it = asyncio.run(go())
    assert relayed.client_gone and relayed.upstream_closed and not relayed.upstream_done
    assert upstream_saw_it


def test_relay_marks_a_watched_stream_that_ends_normally_as_done():
    async def client(session, url, app):
        relayed = relay.watch(app, "m 1:")
        await _post_read(session, url, _marked("other"))
        unwatched = (relayed.first_chunk.is_set(), relayed.upstream_done)
        await _post_read(session, url, _marked("m 1:"))
        return unwatched
    unwatched, relayed, _ = _through_relay(_sse_upstream(STREAM_OK), client)
    assert unwatched == (False, False)
    assert relayed.first_chunk.is_set() and relayed.upstream_done and not relayed.client_gone


def test_the_proxy_sink_records_and_refuses_every_connection():
    async def client(session, url, app):
        async with session.get("http://192.0.2.1/x", proxy=app[relay.RELAYED].sink_url) as resp:
            return resp.status
    status, relayed, _ = _through_relay(_sse_upstream(STREAM_OK), client)
    assert status == 403 and relayed.other_host == ["GET http://192.0.2.1/x HTTP/1.1"]
    assert relayed.dialled == []
```

- [ ] **Step 3: Verify they fail.**
  Run: `python -m pytest tests/test_capability_suite.py -q -k "test_relay_ or watch_resets or proxy_sink"`
  Expected: collection error `ImportError: cannot import name 'relay' from 'factory_core.capability_suite'`.

- [ ] **Step 4: Create `scripts/factory_core/capability_suite/relay.py`** with exactly this
  content. It imports neither `runner` nor `harness`, so there is no import cycle.
  - `FORWARDED` routes are proxied. Any other method/path is recorded with `status: 404` and
    answered 404.
  - The client session is `trust_env=False, auto_decompress=False`. Every request goes out with
    `allow_redirects=False`, and only `Content-Type`/`Retry-After` are copied back, so a 3xx
    reaches the CLI without its `Location`.
  - `_Scan` matches whole `data:` lines, so a chunk boundary cannot hide `message_stop`.
  - On a watched hang-up (`CancelledError` from `handler_cancellation=True`, or
    `ConnectionResetError`) before upstream EOF, the relay sets `client_gone`, `close()`s the
    upstream response and records `upstream_closed`.
  - A failure before response headers are sent answers 502 with `BAD_GATEWAY_BODY`. After them,
    the relay closes the CLI's transport.
  - Nothing is logged or printed.

```python
"""Recording relay for the capability suite's endpoint-driven harness cases (#631).

Spec: docs/superpowers/specs/2026-11-27-harness-driven-capability-probes-endpoint-mode-design.md.
The spawned `claude -p` talks only to this relay on 127.0.0.1. It forwards POST /v1/messages and
HEAD /api/hello to exactly one upstream (the in-process mock or the endpoint under test), never
follows a redirect, streams each answer back as it arrives and records each request in
mock_server's observation shape plus two response facts. It never fabricates a model reply and
never logs, prints or writes a header or body. started() also opens the proxy sink: the CLI's
HTTP(S)_PROXY, a loopback listener that records and refuses every connection it receives.
"""
import asyncio
import contextlib
import json
import re
from dataclasses import dataclass, field

import aiohttp
from aiohttp import web

from factory_core.capability_suite import mock_server
from factory_core.providers import model_adapter

FORWARDED = (("POST", "/v1/messages"), ("HEAD", "/api/hello"))
DROPPED_HEADERS = frozenset({"host", "content-length", "connection", "keep-alive",
                             "proxy-connection", "te", "transfer-encoding", "upgrade",
                             "accept-encoding"})
RETURNED_HEADERS = ("Content-Type", "Retry-After")
MODEL_SCAN_BYTES = 65536  # response_model is read from at most this many response bytes
LINE_BYTES = 65536  # the most of one unfinished response line kept for matching
# model_adapter.check_sse_complete's rule, matched on whole `data:` lines only.
_MESSAGE_STOP = re.compile(rb'"type"\s*:\s*"message_stop"')
BAD_GATEWAY_BODY = {"type": "error", "error": {
    "type": "api_error", "message": "capability-suite relay: upstream request failed"}}
_SINK_REFUSAL = b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"


@dataclass
class Relayed:
    requests: list = field(default_factory=list)      # one record per request received
    marker: str | None = None      # SIGINT case: text that identifies the watched request
    first_chunk: asyncio.Event = field(default_factory=asyncio.Event)
    client_gone: bool = False      # CLI hung up while the watched upstream stream was open
    upstream_done: bool = False    # the watched upstream stream reached EOF and was forwarded
    upstream_closed: bool = False  # the relay closed the upstream response after client_gone
    upstream_errors: list = field(default_factory=list)   # classify_failure classes, in order
    upstream_failed: asyncio.Event = field(default_factory=asyncio.Event)
    dialled: list = field(default_factory=list)       # "host:port" of every outbound request
    other_host: list = field(default_factory=list)    # first line of every sink connection
    sink_url: str = ""


@dataclass(frozen=True)
class _Config:
    upstream: str
    timeouts: dict
    fault: str | None


RELAYED = web.AppKey("relayed", Relayed)
_CONFIG = web.AppKey("relay_config", _Config)
_SESSION = web.AppKey("relay_session", aiohttp.ClientSession)


def watch(app, marker) -> Relayed:
    """Start watching one request: set `marker`, clear `first_chunk`, `client_gone`,
    `upstream_done` and `upstream_closed`. `requests` and the error/sink lists are kept."""
    relayed = app[RELAYED]
    relayed.marker = marker
    relayed.first_chunk.clear()
    relayed.client_gone = relayed.upstream_done = relayed.upstream_closed = False
    return relayed


def _has_user_text(body, marker) -> bool:
    """`marker` occurs in a top-level text block of a user message (never a tool_result)."""
    messages = body.get("messages") if isinstance(body, dict) else None
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            if marker in content:
                return True
            continue
        for block in content if isinstance(content, list) else []:
            if (isinstance(block, dict) and block.get("type") == "text"
                    and marker in str(block.get("text", ""))):
                return True
    return False


class _Scan:
    """response_model and completed over the forwarded bytes, whole lines at a time, so a
    chunk boundary can never hide a `data:` line."""

    def __init__(self):
        self.model, self.completed, self._pending, self._seen = None, False, b"", 0

    def feed(self, chunk):
        lines = (self._pending + chunk).split(b"\n")
        self._pending = lines.pop()[-LINE_BYTES:]
        for line in lines:
            self._line(line)
        self._seen += len(chunk)

    def close(self):
        self._line(self._pending)
        self._pending = b""

    def _line(self, line):
        line = line.rstrip(b"\r")
        if not line.startswith(b"data:"):
            return
        if _MESSAGE_STOP.search(line):
            self.completed = True
        if self.model is None and self._seen < MODEL_SCAN_BYTES:
            try:
                event = json.loads(line[5:])
            except ValueError:
                return
            if isinstance(event, dict) and event.get("type") == "message_start":
                model = (event.get("message") or {}).get("model")
                self.model = model if isinstance(model, str) else None


def _failed(relayed, failure) -> None:
    relayed.upstream_errors.append(failure)
    relayed.upstream_failed.set()


async def _forward(request):
    relayed, config = request.app[RELAYED], request.app[_CONFIG]
    raw = await request.read()
    try:
        body = json.loads(raw) if raw else None
    except ValueError:
        body = None
    record = {"method": request.method, "path": request.path_qs,
              "headers": {k.lower(): v for k, v in request.headers.items()},
              "body": body, "status": None, "response_model": None, "completed": False}
    relayed.requests.append(record)
    if (request.method, request.path) not in FORWARDED:
        record["status"] = 404
        return web.Response(status=404)
    watched = relayed.marker is not None and _has_user_text(body, relayed.marker)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in DROPPED_HEADERS}
    headers["Accept-Encoding"] = "identity"
    if config.fault:
        headers[mock_server.FAULT_HEADER] = config.fault
    timeouts = config.timeouts
    try:
        upstream = await asyncio.wait_for(request.app[_SESSION].request(
            request.method, config.upstream + request.path_qs, data=raw, headers=headers,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(connect=timeouts["connect_seconds"])),
            timeouts["time_to_first_token_seconds"])
    except aiohttp.ClientError as exc:
        _failed(relayed, model_adapter.classify_failure(exc))
        record["status"] = 502
        return web.json_response(BAD_GATEWAY_BODY, status=502)
    except TimeoutError as exc:
        _failed(relayed, model_adapter.classify_failure(exc, ttft_expired=True))
        record["status"] = 502
        return web.json_response(BAD_GATEWAY_BODY, status=502)
    record["status"] = upstream.status
    scan = _Scan()
    resp = web.StreamResponse(status=upstream.status, headers={
        k: upstream.headers[k] for k in RETURNED_HEADERS if k in upstream.headers})
    try:
        await resp.prepare(request)
        first = True
        while True:
            limit = timeouts["time_to_first_token_seconds" if first else "idle_seconds"]
            try:
                chunk = await asyncio.wait_for(upstream.content.readany(), limit)
            except aiohttp.ClientError as exc:
                _failed(relayed, model_adapter.classify_failure(exc))
                break
            except TimeoutError as exc:
                _failed(relayed, model_adapter.classify_failure(
                    exc, ttft_expired=first, idle_expired=not first))
                break
            if not chunk:
                await resp.write_eof()
                if watched:
                    relayed.upstream_done = True
                return resp
            scan.feed(chunk)
            await resp.write(chunk)
            if first and watched:
                relayed.first_chunk.set()
            first = False
        # An upstream failure after the headers were sent: hang up on the CLI.
        if request.transport is not None:
            request.transport.close()
        return resp
    except (ConnectionResetError, asyncio.CancelledError):
        if watched and not relayed.upstream_done:
            relayed.client_gone = True
            upstream.close()
            relayed.upstream_closed = upstream.closed
        raise
    finally:
        scan.close()
        record["response_model"], record["completed"] = scan.model, scan.completed
        upstream.release()


async def _refuse(relayed, reader, writer):
    """The proxy sink: record the first line of the connection, answer 403, close."""
    try:
        line = await asyncio.wait_for(reader.readline(), 5.0)
        relayed.other_host.append(line.decode("latin-1").strip()[:200] or "<empty>")
        writer.write(_SINK_REFUSAL)
        await writer.drain()
    except (TimeoutError, ConnectionError):
        relayed.other_host.append("<no request line>")
    finally:
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()


@contextlib.asynccontextmanager
async def started(upstream_base_url, *, timeouts, fault=None):
    """Yields (app, relay base URL). Relay and sink each bind an ephemeral 127.0.0.1 port."""
    relayed = Relayed()

    async def dialled(_session, _ctx, params):
        relayed.dialled.append(f"{params.url.host}:{params.url.port}")

    trace = aiohttp.TraceConfig()
    trace.on_request_start.append(dialled)
    sink = await asyncio.start_server(lambda r, w: _refuse(relayed, r, w), "127.0.0.1", 0)
    try:
        relayed.sink_url = f"http://127.0.0.1:{sink.sockets[0].getsockname()[1]}"
        async with aiohttp.ClientSession(trust_env=False, auto_decompress=False,
                                         trace_configs=[trace]) as session:
            app = web.Application()
            app[RELAYED] = relayed
            app[_CONFIG] = _Config(upstream_base_url.rstrip("/"), dict(timeouts), fault)
            app[_SESSION] = session
            app.router.add_route("*", "/{tail:.*}", _forward)
            runner = web.AppRunner(app, handler_cancellation=True, shutdown_timeout=1.0)
            await runner.setup()
            try:
                site = web.TCPSite(runner, "127.0.0.1", 0)
                await site.start()
                yield app, f"http://127.0.0.1:{runner.addresses[0][1]}"
            finally:
                await runner.cleanup()
    finally:
        sink.close()
        await sink.wait_closed()
```

- [ ] **Step 5: Re-pin (R).** Run the Conventions block. Expected: a 64-hex digest.

- [ ] **Step 6: Verify they pass.**
  Run: `python -m pytest tests/test_capability_suite.py -q -k "test_relay_ or watch_resets or proxy_sink"`
  Expected: `15 passed`.
  Then run: `python -m pytest tests/test_capability_suite.py -q`
  Expected: all pass, 0 failed.

- [ ] **Step 7: Commit.**

```bash
git add scripts/factory_core/capability_suite/relay.py tests/test_capability_suite.py scripts/factory_core/providers/model_endpoint.py
git commit -m "feat(#631): recording relay and proxy sink for endpoint-driven harness cases"
```

---

## Task 2: Relay spawn environment and bounds (`harness.py`)

**Files:**
- Modify: `scripts/factory_core/capability_suite/harness.py`
- Modify: `tests/test_capability_suite.py` (append)
- Modify: `scripts/factory_core/providers/model_endpoint.py` (re-pin)

- [ ] **Step 1: Append the spawn tests** to `tests/test_capability_suite.py`. They cover
  acceptance criterion 5, the scrubbed endpoint-mode environment:
  - a stub `claude` dumps `/proc/self/environ` and is spawned through `run_relayed` with a
    sentinel credential and served name;
  - its key set is exactly `RELAY_ENV_KEYS`, `ANTHROPIC_API_KEY` is the sentinel and all four
    aliases are the served name;
  - the proxy keys name the sink, `HOME` is temporary, and no `CLAUDE_CODE_OAUTH_TOKEN`/`GH_TOKEN`
    value or caller `HOME` reaches it;
  - argv is as passed.

  Also covered: `spawn_seconds`, the `MAX_ARG_WORDS` bound, and the constant tables.

```python
# --- endpoint-driven harness spawns and judges (#631) ---------------------------------------

def _logging_claude(bin_dir, log, sleep=0, fetch=None):
    """A `claude` on PATH that appends {env, cwd, argv} (env from /proc/self/environ) to `log`
    as one JSON line, optionally fetches `fetch` honouring HTTP_PROXY, then sleeps `sleep`."""
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys, time, urllib.request\n"
        "raw = open('/proc/self/environ', 'rb').read().split(b'\\0')\n"
        "env = dict(item.decode().split('=', 1) for item in raw if item)\n"
        f"with open({str(log)!r}, 'a') as f:\n"
        "    f.write(json.dumps({'env': env, 'cwd': os.getcwd(), 'argv': sys.argv[1:]}) + '\\n')\n"
        f"if {fetch!r}:\n"
        "    try:\n"
        f"        urllib.request.urlopen({fetch!r}, timeout=5)\n"
        "    except Exception:\n"
        "        pass\n"
        f"time.sleep({sleep})\n")
    stub.chmod(0o755)


def _spawns(log):
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def _on_relay(coro_fn, fault=None):
    """coro_fn(relay app, relay url, mock app) with a fresh mock as the relay's upstream."""
    async def go():
        mock_runner, mock_url = await mock_server.start()
        try:
            async with relay.started(mock_url, timeouts=runner.DEFAULT_TIMEOUTS,
                                     fault=fault) as (app, url):
                return await coro_fn(app, url, mock_runner.app)
        finally:
            await mock_runner.cleanup()
    return asyncio.run(go())


needs_proc = pytest.mark.skipif(not Path("/proc/self/environ").exists(),
                                reason="reads the child's initial environment from /proc")


@needs_proc
def test_an_endpoint_spawn_gets_only_the_relay_environment(tmp_path, monkeypatch):
    log = tmp_path / "spawns.jsonl"
    _logging_claude(tmp_path / "bin", log)
    caller_home = tmp_path / "caller-home"
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-oauth-must-not-leak")
    monkeypatch.setenv("GH_TOKEN", "gh-must-not-leak")
    monkeypatch.setenv("HOME", str(caller_home))

    async def go(app, url, _mock_app):
        with harness.workdir() as root:
            status, _ = await harness.run_relayed(app, url, root, "a prompt", "--tools", "",
                                                  served_name="local-model-a",
                                                  credential=SENTINEL, seconds=30)
            return status, root, url, app[relay.RELAYED].sink_url
    status, root, url, sink = _on_relay(go)
    [seen] = _spawns(log)
    env = seen["env"]
    assert status == 0 and set(env) == set(harness.RELAY_ENV_KEYS)
    assert env["ANTHROPIC_API_KEY"] == SENTINEL and env["ANTHROPIC_BASE_URL"] == url
    assert all(env[alias] == "local-model-a" for alias in me.TIER_ALIASES)
    assert env["HTTP_PROXY"] == env["HTTPS_PROXY"] == sink and env["NO_PROXY"] == "127.0.0.1"
    assert env["HOME"] == str(root / "home") != str(caller_home)
    assert "sk-oauth-must-not-leak" not in json.dumps(seen)
    assert "gh-must-not-leak" not in json.dumps(seen)
    assert seen["argv"] == ["-p", "a prompt", "--tools", ""]


def test_scrubbed_env_adds_exactly_the_proxy_keys_and_the_given_credential(tmp_path):
    env = harness.scrubbed_env(base_url="http://127.0.0.1:9", served_name="local-model-a",
                               home=tmp_path, credential=SENTINEL, proxy="http://127.0.0.1:8")
    assert set(env) == set(harness.RELAY_ENV_KEYS)
    assert env["ANTHROPIC_API_KEY"] == SENTINEL
    assert (env["HTTP_PROXY"], env["HTTPS_PROXY"], env["NO_PROXY"]) == (
        "http://127.0.0.1:8", "http://127.0.0.1:8", "127.0.0.1")
    assert harness.RELAY_ENV_KEYS == harness.BASE_ENV_KEYS + ("HTTP_PROXY", "HTTPS_PROXY",
                                                              "NO_PROXY")


def test_spawn_seconds_allows_a_real_endpoint_its_first_byte_bound():
    assert harness.spawn_seconds(None) == harness.ENDPOINT_RUN_SECONDS == 900.0
    assert harness.spawn_seconds(object()) == harness.RUN_SECONDS
    assert harness.ENDPOINT_RUN_SECONDS > runner.DEFAULT_TIMEOUTS["time_to_first_token_seconds"]
    assert harness.FIRST_CHUNK_SECONDS > runner.DEFAULT_TIMEOUTS["time_to_first_token_seconds"]


def test_the_longest_padded_prompt_fits_one_argv_string():
    assert len(harness.padded_prompt(harness.MAX_ARG_WORDS).encode()) < 131072


def test_the_fixed_observations_name_their_reason():
    assert harness.FIXED_OBSERVATIONS == {
        "cli_over_limit_not_tested": "over_limit_not_judged_on_endpoint",
        "cli_nested_agent_not_tested": "nested_agent_not_judged_on_endpoint"}
    assert harness.VARIANTS == ("unplanted_file", "no_sigint")
```

- [ ] **Step 2: Verify they fail.**
  Run: `python -m pytest tests/test_capability_suite.py -q -k "endpoint_spawn or scrubbed_env_adds or spawn_seconds or longest_padded or fixed_observations_name"`
  Expected: 5 failed (`AttributeError: module 'factory_core.capability_suite.harness' has no attribute 'run_relayed'` / `'RELAY_ENV_KEYS'` / `'spawn_seconds'` / `'MAX_ARG_WORDS'` / `'FIXED_OBSERVATIONS'`, or `TypeError: scrubbed_env() got an unexpected keyword argument 'credential'`).

- [ ] **Step 3: Replace the module docstring** of `harness.py`. The first 8 lines currently state
  that the CLI only ever runs against the mock. Replace them with:

```python
"""Harness driver for the capability suite (#624, #631): spawn the pinned `claude -p` and judge
what reached the wire, never the CLI's own reply.

Specs: docs/archive/2026-11-26-harness-driven-capability-probes-design.md (the mock-driven
check_* functions, judged on the in-process mock's log) and
docs/superpowers/specs/2026-11-27-harness-driven-capability-probes-endpoint-mode-design.md
(the check_*_endpoint functions: the CLI talks to relay.py, whose one upstream is the mock or
the endpoint under test, judged on the relay's records). Tier 2 only: runner.main spawns
nothing here unless FACTORY_CAPABILITY_TIER2=1 and `claude --version` is
PINNED_HARNESS_VERSION. Every spawn gets scrubbed_env(), a fresh temporary HOME and working
directory and /dev/null on stdin, so no caller credential or home can reach the CLI. POSIX-only.
"""
```

- [ ] **Step 4: Import the relay.** Replace
  `from factory_core.capability_suite import mock_server` with
  `from factory_core.capability_suite import mock_server, relay`.

- [ ] **Step 5: Add `RELAY_ENV_KEYS`** directly after the `BASE_ENV_KEYS = (...)` assignment.
  `BASE_ENV_KEYS` itself is unchanged.

```python
# A relay-driven spawn (#631) also gets the proxy sink: anything not for the loopback relay.
RELAY_ENV_KEYS = BASE_ENV_KEYS + ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")
```

- [ ] **Step 6: Add the constants** directly after the
  `OVER_LIMIT_TEXT = "prompt is too long"  # …` line. `DISCONNECT_SECONDS` and `RUN_SECONDS`
  already exist and are reused.

```python
# Endpoint-driven harness cases (#631): one argv string is capped at 131,072 bytes by Linux,
# and padded_prompt(30000) is 120,064 bytes.
MAX_ARG_WORDS = 30000
ENDPOINT_RUN_SECONDS = 900.0  # one run_relayed spawn against a real endpoint
FIRST_CHUNK_SECONDS = 310.0  # SIGINT case: ten seconds past the relay's own first-byte bound
ENDPOINT_CASE_SECONDS = 1800.0  # one relay case in total
READ_ATTEMPTS = 2
SIGINT_ATTEMPTS = 3
SIGINT_MARKER = "capability-suite sigint probe"
VARIANTS = ("unplanted_file", "no_sigint")
# The two halves never judged on a real endpoint: kind: observation, a fixed not_tested.
FIXED_OBSERVATIONS = {"cli_over_limit_not_tested": "over_limit_not_judged_on_endpoint",
                      "cli_nested_agent_not_tested": "nested_agent_not_judged_on_endpoint"}
```

- [ ] **Step 7: Replace `scrubbed_env`** with the version below. The defaults keep the existing
  `preflight()` and legacy `start_cli` output byte-identical, so
  `test_scrubbed_env_is_exactly_the_allow_list` and
  `test_the_spawned_cli_gets_only_the_scrubbed_environment` stay unchanged and green.

```python
def scrubbed_env(*, base_url, served_name, home, credential=mock_server.DUMMY_CREDENTIAL,
                 proxy=None) -> dict:
    """The allow-listed environment every spawned `claude` gets. Built from nothing but these
    names, never a copy of os.environ: CLAUDE_CODE_OAUTH_TOKEN, GH_TOKEN and the caller's HOME
    can never leak in by inheritance. `credential` is the only credential (the endpoint's own
    in endpoint mode); `proxy` (the relay's sink) adds exactly HTTP(S)_PROXY and NO_PROXY."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "ANTHROPIC_BASE_URL": base_url,
        "ANTHROPIC_API_KEY": credential,
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
    }
    for alias in model_endpoint.TIER_ALIASES:
        env[alias] = served_name
    if proxy is not None:
        env.update(HTTP_PROXY=proxy, HTTPS_PROXY=proxy, NO_PROXY="127.0.0.1")
    return env
```

- [ ] **Step 8: Replace `start_cli`, then replace `run_cli` with `run_cli` + `spawn_seconds` +
  `run_relayed`.** Today `start_cli` hardcodes `mock_server.SERVED_NAME`; the new `served_name`
  keyword is what points all four tier aliases at the endpoint's served name.

```python
async def start_cli(base_url, root, prompt, *flags, served_name=mock_server.SERVED_NAME,
                    credential=mock_server.DUMMY_CREDENTIAL, proxy=None):
    """`claude -p <prompt> <flags>` on the running loop (the mock shares it). The prompt comes
    first: --allowedTools takes a variadic list and would swallow a prompt after it."""
    return await asyncio.create_subprocess_exec(
        "claude", "-p", prompt, *flags,
        env=scrubbed_env(base_url=base_url, served_name=served_name, home=root / "home",
                         credential=credential, proxy=proxy),
        cwd=root / "cwd", stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
```

```python
async def run_cli(base_url, root, prompt, *flags, served_name=mock_server.SERVED_NAME,
                  credential=mock_server.DUMMY_CREDENTIAL, proxy=None) -> tuple[int | None, str]:
    return await finish(await start_cli(base_url, root, prompt, *flags, served_name=served_name,
                                        credential=credential, proxy=proxy))


def spawn_seconds(mock_app) -> float:
    """A relay check's spawn bound: RUN_SECONDS against the mock, ENDPOINT_RUN_SECONDS against a
    real endpoint (the relay allows it 300 s to its first byte)."""
    return RUN_SECONDS if mock_app is not None else ENDPOINT_RUN_SECONDS


async def run_relayed(relay_app, relay_url, root, prompt, *flags, served_name, credential,
                      seconds) -> tuple[int | None, str]:
    """One `claude -p` through the relay, its proxy variables naming the relay's sink. Waits
    for the first of process exit, a recorded upstream failure and `seconds`; on either of the
    last two the process is killed. (exit status, output); None means killed. The output is
    never printed, logged or stored: no relay check reads it."""
    relayed = relay_app[relay.RELAYED]
    proc = await start_cli(relay_url, root, prompt, *flags, served_name=served_name,
                           credential=credential, proxy=relayed.sink_url)
    exited = asyncio.ensure_future(proc.communicate())
    failed = asyncio.ensure_future(relayed.upstream_failed.wait())
    try:
        await asyncio.wait({exited, failed}, timeout=seconds,
                           return_when=asyncio.FIRST_COMPLETED)
        if exited.done():
            out, err = exited.result()
            return proc.returncode, (out + err).decode("utf-8", "replace")
        return None, ""
    finally:
        failed.cancel()
        exited.cancel()
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        await proc.wait()
```

- [ ] **Step 9: Re-pin (R).**

- [ ] **Step 10: Verify they pass.** Run the Step 2 command.
  Expected: `5 passed`.
  Then run `python -m pytest tests/test_capability_suite.py -q`. Expected: all pass.

- [ ] **Step 11: Commit.**

```bash
git add scripts/factory_core/capability_suite/harness.py tests/test_capability_suite.py scripts/factory_core/providers/model_endpoint.py
git commit -m "feat(#631): relay spawn with the endpoint's credential, served name and proxy sink"
```

---

## Task 3: Endpoint judges (`harness.py`)

**Files:**
- Modify: `scripts/factory_core/capability_suite/harness.py`
- Modify: `tests/test_capability_suite.py` (append)
- Modify: `scripts/factory_core/providers/model_endpoint.py` (re-pin)

- [ ] **Step 1: Append the judge tests** (pure functions over relay-shaped records):
  - `judge_alias_endpoint`: every reason, and a 429 followed by a 200 retry that passes;
  - `judge_read_endpoint`: every reason, including `payload_promoted` via another call's
    `tool_result`. Passing cases: an endpoint-minted id, a relative `file_path`, the file read
    twice in sequence and in parallel, and an assistant echo of a planted line;
  - `judge_padded_endpoint`;
  - the SIGINT attempt verdict for each snapshot shape: "flags set only after reaping" →
    `connection_not_closed`, a non-200 watched request → `not_answered_200`, a second watched
    request → `retried`, and a finished stream → `None`.

```python
SERVED = "local-model-a"


def _relayed_rec(body, status=200, model=SERVED, completed=True, key=SENTINEL):
    return {"method": "POST", "path": "/v1/messages?beta=true", "status": status, "body": body,
            "headers": {"x-api-key": key}, "response_model": model, "completed": completed}


def _endpoint_alias_runs():
    return [(tier, 0, [_relayed_rec(_cli_body(harness.ASK_OK, model=SERVED, tools=()))])
            for tier in harness.TIERS]


def test_judge_alias_endpoint_passes_and_tolerates_a_retried_429():
    assert harness.judge_alias_endpoint(_endpoint_alias_runs(), SERVED, SENTINEL) == ("pass", "ok")
    runs = _endpoint_alias_runs()
    runs[0][2].insert(0, _relayed_rec(_cli_body(harness.ASK_OK, model=SERVED), status=429,
                                      model=None, completed=False))
    assert harness.judge_alias_endpoint(runs, SERVED, SENTINEL) == ("pass", "ok")


@pytest.mark.parametrize("edit, reason", [
    (lambda r: r[3][2].clear(), "fable_no_request"),
    (lambda r: r[1][2][0]["body"].update(model="sonnet"), "sonnet_model_not_served_name"),
    (lambda r: r[0][2][0]["headers"].update({"x-api-key": "sk-other"}),
     "opus_credential_mismatch"),
    (lambda r: r[2][2][0]["headers"].update(authorization="Bearer x"),
     "haiku_credential_mismatch"),
    (lambda r: r[0][2][0].update(status=429), "opus_not_answered_200"),
    (lambda r: r[0][2][0].update(response_model=mock_server.FALLBACK_MODEL),
     "opus_response_model_mismatch"),
    (lambda r: r.__setitem__(3, ("fable", 1, r[3][2])), "fable_cli_failed"),
])
def test_judge_alias_endpoint_fails(edit, reason):
    runs = _endpoint_alias_runs()
    edit(runs)
    assert harness.judge_alias_endpoint(runs, SERVED, SENTINEL) == ("fail", reason)


CWD = "/tmp/capability-suite-x/cwd"
SEEDED = f"{CWD}/{harness.PLANTED_FILE}"
PLANTED_CONTENT = f"1\t{harness.PLANTED_RESULT}\n2\t{harness.PLANTED_SWITCH}\n3\t"


def _use(tool_id, path=SEEDED):
    return {"type": "tool_use", "id": tool_id, "name": "Read", "input": {"file_path": path}}


def _result_block(tool_id, content=PLANTED_CONTENT):
    return {"type": "tool_result", "tool_use_id": tool_id, "content": content}


def _endpoint_read_requests(*rounds):
    """The relay's records for one tool-case spawn: the first POST, then one follow-up per
    round of (assistant tool_use blocks, user tool_result blocks), each repeating the history."""
    rounds = rounds or (([_use("toolu_endpoint_7")], [_result_block("toolu_endpoint_7")]),)
    records, history = [_relayed_rec(_cli_body(harness.READ_PROMPT, model=SERVED,
                                               tools=("Read",)))], []
    for uses, results in rounds:
        history += [{"role": "assistant", "content": uses}, {"role": "user", "content": results}]
        records.append(_relayed_rec(_cli_body(harness.READ_PROMPT, model=SERVED, tools=("Read",),
                                              after=tuple(history))))
    return records


def _judge_read(requests):
    return harness.judge_read_endpoint(requests, SEEDED, CWD, SERVED)


@pytest.mark.parametrize("requests", [
    _endpoint_read_requests(),
    _endpoint_read_requests(([_use("toolu_r", harness.PLANTED_FILE)], [_result_block("toolu_r")])),
    _endpoint_read_requests(([_use("toolu_a")], [_result_block("toolu_a")]),
                            ([_use("toolu_b")], [_result_block("toolu_b")])),
    _endpoint_read_requests(([_use("toolu_a"), _use("toolu_b")],
                             [_result_block("toolu_a"), _result_block("toolu_b")])),
    _endpoint_read_requests(([_use("toolu_e"), {"type": "text", "text": harness.PLANTED_SWITCH}],
                             [_result_block("toolu_e")])),
], ids=["endpoint_id", "relative_path", "read_twice", "read_in_parallel", "assistant_echo"])
def test_judge_read_endpoint_passes(requests):
    assert _judge_read(requests) == ("pass", "ok")


def _follow_up(requests):
    return requests[-1]["body"]


@pytest.mark.parametrize("edit, reason", [
    (lambda r: r.clear(), "no_request"),
    (lambda r: r[0]["body"].update(tools=[{"name": "Read"}, {"name": "Bash"}]),
     "tools_not_read_only"),
    (lambda r: r.pop(), "read_not_called"),
    (lambda r: _follow_up(r)["messages"][2]["content"][0]["input"].update(
        file_path="/etc/hostname"), "read_not_called"),
    (lambda r: _follow_up(r)["messages"][3]["content"][0].update(tool_use_id="toolu_other"),
     "tool_use_id_mismatch"),
    (lambda r: _follow_up(r)["messages"][3]["content"][0].update(content="1\tnothing\n"),
     "planted_not_carried"),
    (lambda r: _follow_up(r)["system"].append({"type": "text", "text": harness.PLANTED_SWITCH}),
     "payload_promoted"),
    (lambda r: _follow_up(r)["messages"][0]["content"].append(
        {"type": "text", "text": harness.PLANTED_RESULT}), "payload_promoted"),
    (lambda r: _follow_up(r)["messages"][3]["content"].append(
        _result_block("toolu_unrelated")), "payload_promoted"),
    (lambda r: _follow_up(r).update(model=mock_server.FALLBACK_MODEL), "model_switched"),
])
def test_judge_read_endpoint_fails(edit, reason):
    requests = _endpoint_read_requests()
    edit(requests)
    assert _judge_read(requests) == ("fail", reason)


def test_judge_padded_endpoint():
    words = 50
    good = harness.padded_prompt(words)
    assert harness.judge_padded_endpoint([_relayed_rec(_cli_body(good))], words) == ("pass", "ok")
    assert harness.judge_padded_endpoint([_relayed_rec(_cli_body(good), completed=False)],
                                         words) == ("fail", "stream_incomplete")
    assert harness.judge_padded_endpoint([_relayed_rec(_cli_body(good), status=400)], words) == (
        "fail", "not_answered_200")
    damaged = good.replace(" pad pad ", " pad ", 1)
    assert harness.judge_padded_endpoint([_relayed_rec(_cli_body(damaged))], words) == (
        "fail", "padding_damaged")


def _watched(*statuses):
    return [_relayed_rec(_cli_body(f"{harness.SIGINT_MARKER} 1: write"), status=s)
            for s in statuses]


@pytest.mark.parametrize("args, verdict", [
    ((["timeout_time_to_first_token"], False, [], False, False),
     ("fail", "timeout_time_to_first_token")),
    (([], False, [], False, False), ("fail", "no_stream")),
    (([], True, _watched(500), False, True), ("fail", "not_answered_200")),
    (([], True, _watched(200, 200), True, False), ("fail", "retried")),
    (([], True, _watched(200), False, True), None),
    # flags that were only set by reaping are not in the snapshot: not a pass
    (([], True, _watched(200), False, False), ("fail", "connection_not_closed")),
    (([], True, _watched(200), True, False), ("pass", "ok")),
])
def test_judge_sigint_attempt(args, verdict):
    assert harness.judge_sigint_attempt(*args) == verdict
```

- [ ] **Step 2: Verify they fail.**
  Run: `python -m pytest tests/test_capability_suite.py -q -k "judge_alias_endpoint or judge_read_endpoint or judge_padded_endpoint or judge_sigint_attempt"`
  Expected: 31 failed with `AttributeError` (no `judge_alias_endpoint` / `judge_read_endpoint` /
  `judge_padded_endpoint` / `judge_sigint_attempt`).

- [ ] **Step 3: Generalise `_strings` and add two helpers.** Replace `_strings` with the three
  functions below. `ids` defaults to the mock's `TOOL_USE_ID`, so `judge_read` is unchanged. The
  endpoint judge derives the ids from the CLI's own follow-up requests, because a real endpoint
  mints its own.

```python
def _strings(value, inside=False, ids=frozenset({mock_server.TOOL_USE_ID})):
    """(string, inside a tool_result block for one of `ids`) for every string in a body. `ids`
    defaults to the mock's fabricated call; the endpoint judge passes the calls it found."""
    if isinstance(value, str):
        yield value, inside
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item, inside, ids)
    elif isinstance(value, dict):
        inside = inside or (value.get("type") == "tool_result"
                            and value.get("tool_use_id") in ids)
        for item in value.values():
            yield from _strings(item, inside, ids)


def _without_assistant(body):
    """A request body with its role: assistant messages removed (the model's own output)."""
    if not isinstance(body, dict):
        return body
    return dict(body, messages=[m for m in body.get("messages") or []
                                if not (isinstance(m, dict) and m.get("role") == "assistant")])


def _read_ids(posts, seeded, cwd):
    """The ids of every assistant Read call, in the CLI's own follow-up requests, whose
    file_path (absolute, or relative to cwd) names the seeded file."""
    ids = set()
    for post in posts:
        for message in (post["body"] or {}).get("messages") or []:
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            content = message.get("content")
            for block in content if isinstance(content, list) else []:
                tool_input = block.get("input") if isinstance(block, dict) else None
                if not (isinstance(tool_input, dict) and block.get("type") == "tool_use"
                        and block.get("name") == "Read"):
                    continue
                path = tool_input.get("file_path")
                if (isinstance(path, str)
                        and os.path.normpath(os.path.join(cwd, path)) == os.path.normpath(seeded)):
                    ids.add(block.get("id"))
    return ids
```

- [ ] **Step 4: Parameterise `_naming_problem`.** Replace it with the version below. The legacy
  `judge_alias` still calls it with no extra arguments; `test_judge_alias_fails`'s
  `*_credential_not_dummy` cases stay green.

```python
def _naming_problem(requests, served_name=mock_server.SERVED_NAME,
                    credential=mock_server.DUMMY_CREDENTIAL,
                    credential_reason="credential_not_dummy"):
    """None, or why the requests did not all name `served_name` with `credential` only."""
    posts = _posts(requests)
    if any((r["body"] or {}).get("model") != served_name for r in posts):
        return "model_not_served_name"
    if (any(r["headers"].get("x-api-key") != credential for r in posts)
            or any("authorization" in r["headers"]
                   or r["headers"].get("x-api-key") not in (None, credential)
                   for r in requests)):
        return credential_reason
    return None
```

- [ ] **Step 5: Append the endpoint judges** to the end of `harness.py`. In
  `judge_padded_endpoint`, `"\n"` is a real newline escape inside the string literal, as in
  `judge_padded`.

```python
# --- endpoint judges (#631): pure functions over what the relay recorded --------------------

def judge_alias_endpoint(runs, served_name, credential):
    """protocol_compatibility against the endpoint. runs: [(tier, exit status, requests)].
    Every request names `served_name` with `credential` only; at least one POST per alias was
    answered 200 and every 200 answer streamed `served_name` as its model; the CLI exited 0.
    Only 200 answers are compared: a 429 or 5xx the CLI retried past is no mismatch."""
    for label, status, requests in runs:
        posts = _posts(requests)
        if not posts:
            return "fail", f"{label}_no_request"
        problem = _naming_problem(requests, served_name, credential, "credential_mismatch")
        if problem:
            return "fail", f"{label}_{problem}"
        answered = [p for p in posts if p["status"] == 200]
        if not answered:
            return "fail", f"{label}_not_answered_200"
        if any(p["response_model"] != served_name for p in answered):
            return "fail", f"{label}_response_model_mismatch"
        if status != 0:
            return "fail", f"{label}_cli_failed"
    return "pass", "ok"


def judge_read_endpoint(requests, seeded, cwd, served_name):
    """tool_call_fidelity against the endpoint. Only Read was offered; the model called Read on
    the seeded file (ids from the requests: a real endpoint mints its own); the CLI returned a
    tool_result for such a call; both planted lines reached the endpoint only inside those
    tool_results (assistant turns excluded: quoting is output, not promotion); no request's
    model changed. read_not_called is the one reason that consumes an attempt."""
    posts = _posts(requests)
    if not posts:
        return "fail", "no_request"
    if _tool_names(posts[0]) != ["Read"]:
        return "fail", "tools_not_read_only"
    ids = _read_ids(posts, seeded, cwd)
    if not ids:
        return "fail", "read_not_called"
    if not any(block.get("tool_use_id") in ids
               for p in posts for block in _tool_results(p["body"])):
        return "fail", "tool_use_id_mismatch"
    for planted in (PLANTED_RESULT, PLANTED_SWITCH):
        placed = {inside for p in posts
                  for text, inside in _strings(_without_assistant(p["body"]), ids=ids)
                  if planted in text}
        if True not in placed:
            return "fail", "planted_not_carried"
        if False in placed:
            return "fail", "payload_promoted"
    if any((p["body"] or {}).get("model") != served_name for p in posts):
        return "fail", "model_switched"
    return "pass", "ok"


def judge_padded_endpoint(requests, words):
    """context_length against the endpoint: judge_padded, and the relayed answer to that
    request reached message_stop. Never reads usage (a later change that does must sum
    input_tokens and both cache_*_input_tokens fields, as runner.check_context_window does)."""
    value, reason = judge_padded(requests, words)
    if value != "pass":
        return value, reason
    block = padded_prompt(words).split("\n", 1)[0]
    hit = next(p for p in _posts(requests) if any(block in text for text in _user_texts(p)))
    if not hit["completed"]:
        return "fail", "stream_incomplete"
    return "pass", "ok"


def judge_sigint_attempt(upstream_errors, first_chunk, watched, client_gone, upstream_done):
    """timeout_cancellation, one attempt. client_gone/upstream_done are the snapshot taken
    BEFORE the process was reaped (reaping hangs up too); watched is the attempt's watched
    requests, counted AFTER it (a retry comes after the hang-up). None: the stream finished
    before SIGINT landed, the attempt is consumed."""
    if upstream_errors:
        return "fail", upstream_errors[0]
    if not first_chunk:
        return "fail", "no_stream"
    if not watched or watched[0]["status"] != 200:
        return "fail", "not_answered_200"
    if len(watched) != 1:
        return "fail", "retried"
    if upstream_done:
        return None
    if not client_gone:
        return "fail", "connection_not_closed"
    return "pass", "ok"
```

- [ ] **Step 6: Re-pin (R).**

- [ ] **Step 7: Verify they pass.** Run the Step 2 command.
  Expected: `31 passed`. Then the full file: all pass.

- [ ] **Step 8: Commit.**

```bash
git add scripts/factory_core/capability_suite/harness.py tests/test_capability_suite.py scripts/factory_core/providers/model_endpoint.py
git commit -m "feat(#631): judges for the endpoint-driven alias, tool, context and SIGINT cases"
```

---

## Task 4: Relay checks (`harness.py`)

**Files:**
- Modify: `scripts/factory_core/capability_suite/harness.py`
- Modify: `tests/test_capability_suite.py` (append)
- Modify: `scripts/factory_core/providers/model_endpoint.py` (re-pin)

- [ ] **Step 1: Append the check tests.**
  - Tool surface: alias spawns get `--model <tier> --tools ""`, the tool case gets
    `--tools Read`, the context case gets `--tools ""`, and no spawn gets `--allowedTools`.
  - Killed spawn: with a stub `claude` that outlives `seconds`, the alias, tool and context
    checks each return `fail:cli_timeout`. Each spawns exactly once (the tool case makes no
    second attempt) and calls no judge.
  - SIGINT ordering, through `check_sigint_endpoint` itself with a stub `claude` that streams
    one request from the relay (upstream: the mock's `stall` script) and hangs up on SIGINT:
    - no retry → `pass:ok`;
    - a retry sent 0.3 s after the hang-up → `fail:retried`. Only a count taken **after**
      reaping sees it;
    - `no_sigint` → `fail:connection_not_closed`. The only hang-up is the reaping kill, which a
      snapshot taken **before** reaping does not see.

    The judge tests of Task 3 take these orderings as arguments, so without this test a check
    that counted before reaping, or read the flags after it, would pass every unit test.

```python
@needs_proc
def test_each_relay_check_restricts_the_tool_surface(tmp_path, monkeypatch):
    log = tmp_path / "spawns.jsonl"
    _logging_claude(tmp_path / "bin", log)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}")
    kw = dict(served_name="local-model-a", credential=SENTINEL, context_tokens=64,
              mock_app=None, variant=None)

    async def go(app, url, _mock_app):
        return [await check(app, url, **kw) for check in (
            harness.check_alias_resolution_endpoint, harness.check_tool_round_trip_endpoint,
            harness.check_context_window_endpoint)]
    outcomes = _on_relay(go)
    assert outcomes == [("fail", "opus_no_request"), ("fail", "no_request"),
                        ("fail", "no_request")]
    argvs = [s["argv"] for s in _spawns(log)]
    assert [a[2:] for a in argvs[:4]] == [["--model", t, "--tools", ""] for t in harness.TIERS]
    assert argvs[4][2:] == ["--tools", "Read"] and argvs[5][2:] == ["--tools", ""]
    assert all("--allowedTools" not in a for a in argvs)


@needs_proc
def test_a_killed_spawn_is_cli_timeout_and_never_judged(tmp_path, monkeypatch):
    log = tmp_path / "spawns.jsonl"
    _logging_claude(tmp_path / "bin", log, sleep=30)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(harness, "ENDPOINT_RUN_SECONDS", 2.0)

    def judged(*_):
        raise AssertionError("a judge ran over a killed spawn")
    for judge in ("judge_alias_endpoint", "judge_read_endpoint", "judge_padded_endpoint"):
        monkeypatch.setattr(harness, judge, judged)
    kw = dict(served_name="local-model-a", credential=SENTINEL, context_tokens=64,
              mock_app=None, variant=None)
    for check in (harness.check_alias_resolution_endpoint,
                  harness.check_tool_round_trip_endpoint,
                  harness.check_context_window_endpoint):
        before = len(_spawns(log))

        async def go(app, url, _mock_app):
            return await check(app, url, **kw)
        assert _on_relay(go) == ("fail", "cli_timeout")
        assert len(_spawns(log)) == before + 1  # nothing further spawned


def _streaming_claude(bin_dir, retry):
    """A `claude` on PATH that POSTs its prompt to ANTHROPIC_BASE_URL as one streamed request
    and reads the answer. On SIGINT it hangs up; with `retry` it sends the same request once
    more 0.3 s after its hang-up, as a silently retrying CLI would, then exits."""
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os, signal, socket, sys, time\n"
        "from urllib.parse import urlsplit\n"
        "url = urlsplit(os.environ['ANTHROPIC_BASE_URL'])\n"
        "body = json.dumps({'model': os.environ['ANTHROPIC_DEFAULT_OPUS_MODEL'], 'max_tokens': 8,\n"
        "                   'stream': True,\n"
        "                   'messages': [{'role': 'user', 'content': sys.argv[2]}]}).encode()\n"
        "request = ('POST /v1/messages?beta=true HTTP/1.1\\r\\nHost: relay\\r\\n'\n"
        "           'x-api-key: ' + os.environ['ANTHROPIC_API_KEY'] + '\\r\\n'\n"
        "           'Content-Type: application/json\\r\\n'\n"
        "           'Content-Length: ' + str(len(body)) + '\\r\\n\\r\\n').encode() + body\n"
        "socks = []\n"
        "def post():\n"
        "    socks.append(socket.create_connection((url.hostname, url.port)))\n"
        "    socks[-1].sendall(request)\n"
        "def hang_up(*_):\n"
        "    socks[0].close()\n"
        f"    if {retry!r}:\n"
        "        time.sleep(0.3)\n"
        "        post()\n"
        "        time.sleep(0.2)\n"
        "    os._exit(0)\n"
        "signal.signal(signal.SIGINT, hang_up)\n"
        "post()\n"
        "while socks[0].recv(4096):\n"
        "    pass\n")
    stub.chmod(0o755)


@pytest.mark.parametrize("retry, variant, outcome", [
    (False, None, ("pass", "ok")),
    # the retry arrives after the hang-up: only a count taken after reaping sees it
    (True, None, ("fail", "retried")),
    # no SIGINT: the only hang-up is the reaping kill, which the snapshot must not see
    (False, "no_sigint", ("fail", "connection_not_closed")),
])
def test_the_sigint_check_snapshots_before_reaping_and_counts_after(tmp_path, monkeypatch, retry,
                                                                    variant, outcome):
    _streaming_claude(tmp_path / "bin", retry)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(harness, "DISCONNECT_SECONDS", 1.0)

    async def go(app, url, mock_app):
        return await harness.check_sigint_endpoint(
            app, url, served_name=mock_server.SERVED_NAME,
            credential=mock_server.DUMMY_CREDENTIAL, context_tokens=64, mock_app=mock_app,
            variant=variant)
    assert _on_relay(go) == outcome
```

- [ ] **Step 2: Verify they fail.**
  Run: `python -m pytest tests/test_capability_suite.py -q -k "restricts_the_tool_surface or killed_spawn or sigint_check_snapshots"`
  Expected: 5 failed (`AttributeError: … no attribute 'check_alias_resolution_endpoint'` /
  `'check_sigint_endpoint'`).

- [ ] **Step 3: Append the relay checks** to the end of `harness.py`. Every check has the spec's
  signature `(relay_app, relay_url, *, served_name, credential, context_tokens, mock_app,
  variant)`.
  - With `mock_app` set, the check arms the same `mock_server.Script` its legacy sibling arms
    before each spawn. Nothing is ever armed for a real endpoint, and `mock_server.py` is not
    edited.
  - After every `run_relayed`, `_spawn_problem` applies the two final rules before any judge: an
    upstream error, then a killed spawn (`cli_timeout`).
  - The SIGINT check uses `start_cli` with the same environment. It snapshots `client_gone` and
    `upstream_done` **before** reaping, and counts watched requests **after**.

```python
# --- relay checks: runner.HARNESS_RELAY_CHECKS ----------------------------------------------

def _spawn_problem(relayed, status):
    """The two rules applied after every run_relayed, before any judge: an upstream failure,
    then a killed spawn. Either is final for the case."""
    if relayed.upstream_errors:
        return "fail", relayed.upstream_errors[0]
    if status is None:
        return "fail", "cli_timeout"
    return None


async def check_alias_resolution_endpoint(relay_app, relay_url, *, served_name, credential,
                                          context_tokens, mock_app, variant):
    """`--model` with each tier alias, `--tools ""`, through the relay."""
    relayed = relay_app[relay.RELAYED]
    runs = []
    for tier in TIERS:
        if mock_app is not None:
            mock_server.arm(mock_app, mock_server.Script("text"))
        start = len(relayed.requests)
        with workdir() as root:
            status, _ = await run_relayed(relay_app, relay_url, root, ASK_OK, "--model", tier,
                                          "--tools", "", served_name=served_name,
                                          credential=credential, seconds=spawn_seconds(mock_app))
        problem = _spawn_problem(relayed, status)
        if problem:
            return problem
        runs.append((tier, status, relayed.requests[start:]))
    return judge_alias_endpoint(runs, served_name, credential)


async def check_tool_round_trip_endpoint(relay_app, relay_url, *, served_name, credential,
                                         context_tokens, mock_app, variant):
    """Offered only Read (`--tools Read`, no --allowedTools: Read stays inside the temporary
    cwd), the model must read the seeded file; up to READ_ATTEMPTS fresh attempts."""
    relayed = relay_app[relay.RELAYED]
    for _ in range(READ_ATTEMPTS):
        with workdir() as root:
            seeded = root / "cwd" / PLANTED_FILE
            seeded.write_text("capability-suite notes: nothing to see here.\n"
                              if variant == "unplanted_file"
                              else f"{PLANTED_RESULT}\n{PLANTED_SWITCH}\n", encoding="utf-8")
            if mock_app is not None:
                mock_server.arm(mock_app, mock_server.Script("read", read_path=str(seeded)))
            start = len(relayed.requests)
            status, _ = await run_relayed(relay_app, relay_url, root, READ_PROMPT, "--tools",
                                          "Read", served_name=served_name, credential=credential,
                                          seconds=spawn_seconds(mock_app))
            problem = _spawn_problem(relayed, status)
            if problem:
                return problem
            value, reason = judge_read_endpoint(relayed.requests[start:], str(seeded),
                                                str(root / "cwd"), served_name)
        if reason != "read_not_called":
            return value, reason
    return "fail", "read_not_called"


async def check_context_window_endpoint(relay_app, relay_url, *, served_name, credential,
                                        context_tokens, mock_app, variant):
    """A padded block of min(window // 2, MAX_ARG_WORDS) words through the CLI, answered 200
    and streamed to message_stop. Not a window test and no over-limit attempt. None (not run)
    without a window, as context_length/sized_request."""
    if context_tokens is None:
        return None
    relayed = relay_app[relay.RELAYED]
    words = min(context_tokens // 2, MAX_ARG_WORDS)
    if mock_app is not None:
        mock_server.arm(mock_app, mock_server.Script("text"))
    start = len(relayed.requests)
    with workdir() as root:
        status, _ = await run_relayed(relay_app, relay_url, root, padded_prompt(words), "--tools",
                                      "", served_name=served_name, credential=credential,
                                      seconds=spawn_seconds(mock_app))
    problem = _spawn_problem(relayed, status)
    if problem:
        return problem
    return judge_padded_endpoint(relayed.requests[start:], words)


async def check_sigint_endpoint(relay_app, relay_url, *, served_name, credential,
                                context_tokens, mock_app, variant):
    """SIGINT once the watched reply's first chunk is forwarded; the CLI must hang up on the
    relay with no retry. Up to SIGINT_ATTEMPTS attempts, each asking for a longer reply; only
    "the stream finished first" consumes one. Variant no_sigint never sends the signal."""
    relayed = relay_app[relay.RELAYED]
    for n in range(1, SIGINT_ATTEMPTS + 1):
        marker = f"{SIGINT_MARKER} {n}:"
        relay.watch(relay_app, marker)
        if mock_app is not None:
            mock_server.arm(mock_app, mock_server.Script("stall"))
        first = client_gone = upstream_done = False
        with workdir() as root:
            proc = await start_cli(relay_url, root,
                                   f"{marker} write {40 * 2 ** (n - 1)} paragraphs about rivers.",
                                   "--tools", "", served_name=served_name, credential=credential,
                                   proxy=relayed.sink_url)
            try:
                await _until(lambda: relayed.first_chunk.is_set()
                             or relayed.upstream_failed.is_set(), FIRST_CHUNK_SECONDS)
                first = relayed.first_chunk.is_set()
                if first:
                    if variant != "no_sigint":
                        with contextlib.suppress(ProcessLookupError):
                            proc.send_signal(signal.SIGINT)
                    await _until(lambda: relayed.client_gone or relayed.upstream_done,
                                 DISCONNECT_SECONDS)
                client_gone, upstream_done = relayed.client_gone, relayed.upstream_done
            finally:
                await finish(proc, DISCONNECT_SECONDS)
        watched = [p for p in _posts(relayed.requests)
                   if any(marker in text for text in _user_texts(p))]
        verdict = judge_sigint_attempt(relayed.upstream_errors, first, watched, client_gone,
                                       upstream_done)
        if verdict is not None:
            return verdict
    return "not_tested", "stream_finished_before_sigint"
```

- [ ] **Step 4: Re-pin (R).**

- [ ] **Step 5: Verify they pass.** Run the Step 2 command.
  Expected: `5 passed`. Then the full file: all pass.

- [ ] **Step 6: Optional, when the pinned CLI is on PATH** (`claude --version` prints `2.1.282`).
  This drives the four checks and their control conditions through the relay against the mock,
  with the real CLI; nothing is committed:

```bash
PYTHONPATH=scripts python3 - <<'EOF'
import asyncio
from factory_core.capability_suite import harness, mock_server, relay, runner
RUNS = [(harness.check_alias_resolution_endpoint, None, None, "pass:ok"),
        (harness.check_alias_resolution_endpoint, "wrong_model", None, "fail:opus_response_model_mismatch"),
        (harness.check_tool_round_trip_endpoint, None, None, "pass:ok"),
        (harness.check_tool_round_trip_endpoint, None, "unplanted_file", "fail:planted_not_carried"),
        (harness.check_context_window_endpoint, None, None, "pass:ok"),
        (harness.check_context_window_endpoint, "over_limit", None, "fail:not_answered_200"),
        (harness.check_sigint_endpoint, None, None, "pass:ok"),
        (harness.check_sigint_endpoint, None, "no_sigint", "fail:connection_not_closed")]
async def main():
    mock_runner, mock_url = await mock_server.start()
    try:
        for check, fault, variant, want in RUNS:
            async with relay.started(mock_url, timeouts=runner.DEFAULT_TIMEOUTS, fault=fault) as (app, url):
                got = ":".join(await check(app, url, served_name=mock_server.SERVED_NAME,
                                           credential=mock_server.DUMMY_CREDENTIAL,
                                           context_tokens=mock_server.CONTEXT_TOKENS,
                                           mock_app=mock_runner.app, variant=variant))
                print(check.__name__, fault, variant, got, "OK" if got == want else "MISMATCH",
                      "sink:", app[relay.RELAYED].other_host)
            mock_server.arm(mock_runner.app)
    finally:
        await mock_runner.cleanup()
asyncio.run(main())
EOF
```

  Expected: eight lines, each ending `OK sink: []`. The `no_sigint` line takes about 20 s.

- [ ] **Step 7: Commit.**

```bash
git add scripts/factory_core/capability_suite/harness.py tests/test_capability_suite.py scripts/factory_core/providers/model_endpoint.py
git commit -m "feat(#631): endpoint-driven alias, tool, context and SIGINT checks through the relay"
```

---

## Task 5: Runner routing, validation and `mechanism` records (`runner.py`)

**Files:**
- Modify: `scripts/factory_core/capability_suite/runner.py`
- Modify: `tests/test_capability_suite.py` (edit two parametrize lists and one test; append)
- Modify: `scripts/factory_core/providers/model_endpoint.py` (re-pin)

- [ ] **Step 1: Update the validation tests.** Replace `test_a_malformed_harness_case_is_rejected`,
  including its decorator, with the version below. Its new rule messages are: a legacy check must
  be a mock-target probe; a relay check must be an endpoint-target probe or a control; a fixed
  observation must be an observation; `variant` only on harness controls, in `VARIANTS`.

```python
@pytest.mark.parametrize("edit, message", [
    (lambda c: c.update(check="nope"), "check must be one of"),
    (lambda c: c.update(target="endpoint"), "a mock-driven harness check is a mock-target probe"),
    (lambda c: c.update(kind="observation"),
     "a mock-driven harness check is a mock-target probe"),
    (lambda c: c.update(check="cli_sigint_endpoint"),
     "a relay harness check is an endpoint-target probe or a control"),
    (lambda c: c.update(check="cli_sigint_endpoint", kind="observation", target="endpoint"),
     "a relay harness check is an endpoint-target probe or a control"),
    (lambda c: c.update(check="cli_over_limit_not_tested", target="endpoint"),
     "a fixed harness observation is an observation"),
    (lambda c: c.update(check="cli_sigint_endpoint", target="endpoint", variant="no_sigint"),
     "variant is for harness controls"),
    (lambda c: c.update(check="cli_sigint_endpoint", kind="control", expect="fail",
                        expect_reason="connection_not_closed", variant="nope"),
     "variant is for harness controls"),
])
def test_a_malformed_harness_case_is_rejected(tmp_path, edit, message):
    case = dict(HARNESS_CASE)
    edit(case)
    fixtures = _with_harness_case(tmp_path, case)
    with pytest.raises(runner.CaseError, match=message):
        runner.load_cases(fixtures)
```

  In `test_malformed_case_is_rejected`'s parametrize list, add this line directly after the
  `(lambda c: c.update(fault="nope"), "fault is for controls"),` entry. The case is an HTTP
  control carrying `variant`:

```python
    (lambda c: c.update(variant="no_sigint"), "variant is for harness controls"),
```

- [ ] **Step 2: Update the coroutine test.** Replace `test_every_harness_check_is_a_coroutine_function` with:

```python
def test_every_harness_check_is_a_coroutine_function():
    checks = [*runner.HARNESS_CHECKS.values(), *runner.HARNESS_RELAY_CHECKS.values()]
    assert len(checks) == 8
    for check in checks:
        assert inspect.iscoroutinefunction(check)  # asyncio's is deprecated in 3.14
```

- [ ] **Step 3: Append the `_harness_probe` tests.**
  - No-other-host for the CLI: a stub `claude` that requests `http://192.0.2.1/` while honouring
    `HTTP_PROXY` turns the case into `fail:other_host_contacted`.
  - The `timeout_total` override at `ENDPOINT_CASE_SECONDS`.

```python
@needs_proc
def test_a_cli_request_for_another_host_fails_the_case(tmp_path, monkeypatch):
    _logging_claude(tmp_path / "bin", tmp_path / "spawns.jsonl", fetch="http://192.0.2.1/")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}")
    case = {"dimension": "context_length", "case": "cli_padded_prompt_endpoint",
            "driver": "harness", "kind": "probe", "target": "endpoint",
            "check": "cli_context_window_endpoint"}

    async def go():
        mock_runner, url = await mock_server.start()
        try:
            mock = runner.Target(url, mock_server.SERVED_NAME, mock_server.DUMMY_CREDENTIAL)
            return await runner._harness_probe(case, "context_length/x", mock_app=mock_runner.app,
                                               mock=mock, endpoint=mock, mock_mode=False,
                                               context_tokens=64)
        finally:
            await mock_runner.cleanup()
    assert asyncio.run(go()) == (("fail", "other_host_contacted"), "endpoint")


def _probe_case(check="cli_context_window_endpoint"):
    return {"dimension": "context_length", "case": "x", "driver": "harness", "kind": "probe",
            "target": "endpoint", "check": check}


@pytest.mark.parametrize("behaviour, outcome", [
    ("other_host", ("fail", "other_host_contacted")),
    ("slow", ("fail", "timeout_total")),
])
def test_harness_probe_overrides(monkeypatch, behaviour, outcome):
    monkeypatch.setattr(harness, "ENDPOINT_CASE_SECONDS", 0.3)

    async def fake(relay_app, relay_url, **_):
        if behaviour == "other_host":
            relay_app[relay.RELAYED].other_host.append("CONNECT api.example.invalid:443 HTTP/1.1")
            return "pass", "ok"
        await asyncio.sleep(5)
    monkeypatch.setitem(runner.HARNESS_RELAY_CHECKS, "cli_context_window_endpoint", fake)

    async def go():
        mock_runner, url = await mock_server.start()
        try:
            mock = runner.Target(url, mock_server.SERVED_NAME, mock_server.DUMMY_CREDENTIAL)
            return await runner._harness_probe(_probe_case(), "context_length/x",
                                               mock_app=mock_runner.app, mock=mock,
                                               endpoint=mock, mock_mode=False,
                                               context_tokens=64)
        finally:
            await mock_runner.cleanup()
    assert asyncio.run(go()) == (outcome, "endpoint")
```

- [ ] **Step 4: Verify they fail.**
  Run: `python -m pytest tests/test_capability_suite.py -q -k "malformed or coroutine or another_host or harness_probe_overrides"`
  Expected failures:
  - the new `test_a_malformed_harness_case_is_rejected` messages (`Regex pattern did not match`);
  - the `variant` case (`unknown key(s) variant`);
  - `AttributeError: … has no attribute 'HARNESS_RELAY_CHECKS'`;
  - `TypeError: _harness_probe() got an unexpected keyword argument 'mock_app'`.

- [ ] **Step 5: Import the relay.** Replace
  `from factory_core.capability_suite import emit, harness, mock_server` with
  `from factory_core.capability_suite import emit, harness, mock_server, relay`.

- [ ] **Step 6: Allow `variant`.** Replace the `_CASE_KEYS` assignment with:

```python
_CASE_KEYS = {"case", "dimension", "description", "driver", "kind", "target", "check", "fault",
              "timeouts", "expect", "expect_reason", "args", "want", "variant"}
```

- [ ] **Step 7: Extend `Summary`.** Replace the class with:

```python
@dataclass
class Summary:
    probe_values: dict = field(default_factory=dict)  # dimension -> [value, ...]
    probes: list = field(default_factory=list)  # (dimension/case, value) of executed probes
    controls: list = field(default_factory=list)  # result-ready control/observation records
    not_run: list = field(default_factory=list)  # dimension/case of every shipped case not run
    harness_pending: list = field(default_factory=list)  # dimension of every harness case not run
    harness_probes: list = field(default_factory=list)  # result-ready executed harness probes
    harness_probe_sources: dict = field(default_factory=dict)  # dimension -> {source} (#631)
    lines: list = field(default_factory=list)
```

- [ ] **Step 8: Replace `_validate`.** The rule "a harness case is a mock-target probe" becomes
  the four rules from the spec. The existing control rule still forces a control to
  `target: mock`, and the observation rule still forces `target: endpoint`.

```python
def _validate(case, where):
    unknown = set(case) - _CASE_KEYS
    if unknown:
        raise CaseError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
    for key, allowed in (("driver", DRIVERS), ("kind", KINDS), ("target", TARGETS)):
        if case.get(key) not in allowed:
            raise CaseError(f"{where}: {key} must be one of {', '.join(allowed)}")
    checks = {"function": FUNCTION_CHECKS,
              "harness": {**HARNESS_CHECKS, **HARNESS_RELAY_CHECKS, **harness.FIXED_OBSERVATIONS},
              }.get(case["driver"], HTTP_CHECKS)
    if case.get("check") not in checks:
        raise CaseError(f"{where}: check must be one of {', '.join(sorted(checks))}")
    if case["driver"] == "function" and (case["kind"] != "probe" or case["target"] != "mock"
                                         or not isinstance(case.get("args"), dict)
                                         or not isinstance(case.get("want"), list)):
        raise CaseError(f"{where}: a function case is a mock-target probe with args and want")
    if case["driver"] == "harness":
        check, kind, target = case["check"], case["kind"], case["target"]
        if check in HARNESS_CHECKS and (kind != "probe" or target != "mock"):
            raise CaseError(f"{where}: a mock-driven harness check is a mock-target probe")
        if check in HARNESS_RELAY_CHECKS and not (kind == "control"
                                                  or (kind == "probe" and target == "endpoint")):
            raise CaseError(f"{where}: a relay harness check is an endpoint-target probe or a "
                            "control")
        if check in harness.FIXED_OBSERVATIONS and kind != "observation":
            raise CaseError(f"{where}: a fixed harness observation is an observation")
    if "variant" in case and (case["driver"] != "harness" or case["kind"] != "control"
                              or case["variant"] not in harness.VARIANTS):
        raise CaseError(f"{where}: variant is for harness controls and must be one of "
                        "harness.VARIANTS")
    if case["kind"] == "control":
        if case["target"] != "mock":
            raise CaseError(f"{where}: a control always targets the mock")
        if case.get("expect") not in model_endpoint.RESULT_VALUES or not isinstance(
                case.get("expect_reason"), str):
            raise CaseError(f"{where}: a control needs expect (a result value) and expect_reason")
    elif "expect" in case or "expect_reason" in case:
        raise CaseError(f"{where}: only a control carries expect")
    if case["kind"] == "observation" and case["target"] != "endpoint":
        raise CaseError(f"{where}: an observation targets the endpoint")
    if "fault" in case and (case["kind"] != "control" or case["fault"] not in mock_server.FAULTS):
        raise CaseError(f"{where}: fault is for controls and must be one of mock_server.FAULTS")
```

- [ ] **Step 9: Add `HARNESS_RELAY_CHECKS`.** Replace the `HARNESS_CHECKS` comment and dict with
  the block below. The four legacy names are unchanged.

```python
# check name -> harness.check_*(mock app, mock base URL): spawns the pinned `claude -p`, so a
# harness case runs only when harness.preflight() is "ok" (Tier 2 on, pinned CLI present).
# These always drive the CLI against the mock: in endpoint mode they are `mechanism` records.
HARNESS_CHECKS = {
    "cli_alias_resolution": harness.check_alias_resolution,
    "cli_tool_round_trip": harness.check_tool_round_trip,
    "cli_context_window": harness.check_context_window,
    "cli_sigint": harness.check_sigint,
}
# check name -> harness.check_*_endpoint(relay app, relay URL, *, ...) (#631): the CLI talks to
# a recording relay whose one upstream is the endpoint (an endpoint-target probe in endpoint
# mode) or the mock (mock mode, and every control). Same Tier 2 gate.
HARNESS_RELAY_CHECKS = {
    "cli_alias_resolution_endpoint": harness.check_alias_resolution_endpoint,
    "cli_tool_round_trip_endpoint": harness.check_tool_round_trip_endpoint,
    "cli_context_window_endpoint": harness.check_context_window_endpoint,
    "cli_sigint_endpoint": harness.check_sigint_endpoint,
}
```

- [ ] **Step 10: Replace `_harness_probe`** with the spec's version, which returns
  `(outcome, source)`.
  - Fixed observations return their reason without opening a relay.
  - Legacy checks are called exactly as before.
  - A relay check runs under `ENDPOINT_CASE_SECONDS` (`fail:timeout_total`), and any sink hit
    overrides the outcome with `fail:other_host_contacted`.
  - For an endpoint-upstream fault only the exception type is printed.
  - The mock is always disarmed in `finally`.

```python
async def _harness_probe(case, name, *, mock_app, mock, endpoint, mock_mode, context_tokens):
    """(outcome, source) for one harness case. source is "mock" when the CLI's traffic went to
    the mock, "endpoint" when it went (through the relay) to the endpoint. outcome is None (not
    run: exit 4) when the CLI cannot be spawned or the check hits any other internal fault; for
    an endpoint-upstream case only the exception's type is printed (its text can carry the
    endpoint address). The script a check arms is always cleared, so the http cases after it
    get part A's replies."""
    check = case["check"]
    use_mock = case["target"] == "mock" or mock_mode
    source = "mock" if use_mock else "endpoint"
    try:
        if check in harness.FIXED_OBSERVATIONS:
            return ("not_tested", harness.FIXED_OBSERVATIONS[check]), source
        if check in HARNESS_CHECKS:
            return await HARNESS_CHECKS[check](mock_app, mock.base_url), "mock"
        upstream = mock if use_mock else endpoint
        window = mock_server.CONTEXT_TOKENS if use_mock else context_tokens
        async with relay.started(upstream.base_url, timeouts=DEFAULT_TIMEOUTS,
                                 fault=case.get("fault")) as (app, url):
            try:
                outcome = await asyncio.wait_for(
                    HARNESS_RELAY_CHECKS[check](
                        app, url, served_name=upstream.served_name,
                        credential=upstream.credential, context_tokens=window,
                        mock_app=mock_app if use_mock else None, variant=case.get("variant")),
                    harness.ENDPOINT_CASE_SECONDS)
            except TimeoutError:  # no check lets its own TimeoutError escape: this is the bound
                outcome = ("fail", "timeout_total")
            if app[relay.RELAYED].other_host:
                outcome = ("fail", "other_host_contacted")
            return outcome, source
    except Exception as exc:  # a suite fault, never a finding
        detail = f": {exc}" if source == "mock" else ""
        print(f"capability suite: harness case {name} did not run: {type(exc).__name__}{detail}",
              file=sys.stderr)
        return None, source
    finally:
        mock_server.arm(mock_app)
```

- [ ] **Step 11: Replace `run_cases`.** The harness branch changes as follows:
  - With Tier 2 not `"ok"`, every harness kind is skipped. A probe adds `not_tested`, and every
    kind adds its dimension to `harness_pending`, as today.
  - A legacy case in endpoint mode becomes a `mechanism` control record (`source: "mock"`).
    It adds nothing to `probe_values`; a mismatch is exit 3 through the existing rule. If it
    does not run, it is `not_run` (exit 4) without `harness_pending`.
  - An executed harness probe is also appended to `harness_probes`. A `not_tested` one lists its
    dimension in `harness_pending`.
  - Harness controls and observations carry `source`.
  - Every `pass`/`fail` probe or mechanism outcome adds its source to `harness_probe_sources`.

```python
async def run_cases(cases, *, endpoint, mock, context_tokens, mock_app=None,
                    tier2_mode="skip", mock_mode=True) -> Summary:
    """Run every case in order. A control always runs against the mock and its declared
    window; a probe or observation targeting the endpoint uses `endpoint`. A harness case runs
    only in tier2_mode "ok" (harness.preflight); otherwise it adds one not_tested value (a
    probe) and its dimension to harness_pending. With Tier 2 on, an endpoint-target harness
    probe drives the CLI through the relay to `endpoint` (to the mock in mock mode), and in
    endpoint mode a mock-target harness probe (HARNESS_CHECKS) still runs against the mock but
    is recorded as a `mechanism` control record, never a dimension value (#631)."""
    summary = Summary()
    async with aiohttp.ClientSession() as session:
        for case in cases:
            name = f"{case['dimension']}/{case['case']}"
            source, mechanism = None, False
            if case["driver"] == "harness":
                if tier2_mode != "ok":
                    if case["kind"] == "probe":
                        summary.probe_values.setdefault(case["dimension"], []).append(
                            "not_tested")
                    summary.harness_pending.append(case["dimension"])
                    summary.lines.append(f"{name} [{case['kind']}] {_SKIPPED[tier2_mode]}")
                    continue
                outcome, source = await _harness_probe(
                    case, name, mock_app=mock_app, mock=mock, endpoint=endpoint,
                    mock_mode=mock_mode, context_tokens=context_tokens)
                mechanism = case["check"] in HARNESS_CHECKS and not mock_mode
            elif case["driver"] == "function":
                outcome = _function_probe(case)
            elif case["kind"] == "control" or case["target"] == "mock":
                outcome = await HTTP_CHECKS[case["check"]](session, mock, case,
                                                           mock_server.CONTEXT_TOKENS)
            else:
                outcome = await HTTP_CHECKS[case["check"]](session, endpoint, case,
                                                           context_tokens)
            if outcome is None:
                # A mock mechanism case that did not run says nothing about the endpoint.
                if case["driver"] == "harness" and not mechanism:
                    summary.harness_pending.append(case["dimension"])
                summary.not_run.append(name)
                summary.lines.append(f"{name} [{case['kind']}] not run")
                if case["kind"] == "probe" and not mechanism:
                    summary.probe_values.setdefault(case["dimension"], []).append("not_tested")
                continue
            value, reason = outcome
            observed = f"{value}:{reason}"
            if source is not None and value in ("pass", "fail") and (
                    mechanism or case["kind"] == "probe"):
                summary.harness_probe_sources.setdefault(case["dimension"], set()).add(source)
            if mechanism:
                summary.lines.append(f"{name} [mechanism] {observed}")
                summary.controls.append({
                    "dimension": case["dimension"], "case": case["case"], "kind": "mechanism",
                    "expected": "pass:ok", "observed": observed, "ok": observed == "pass:ok",
                    "source": "mock",
                })
                continue
            summary.lines.append(f"{name} [{case['kind']}] {observed}")
            if case["kind"] == "probe":
                summary.probe_values.setdefault(case["dimension"], []).append(value)
                summary.probes.append((name, value))
                if case["driver"] == "harness":
                    summary.harness_probes.append({"dimension": case["dimension"],
                                                   "case": case["case"], "observed": observed,
                                                   "source": source})
                    if value == "not_tested":
                        summary.harness_pending.append(case["dimension"])
                continue
            expected = (f"{case['expect']}:{case['expect_reason']}"
                        if case["kind"] == "control" else None)
            record = {
                "dimension": case["dimension"], "case": case["case"], "kind": case["kind"],
                "expected": expected, "observed": observed,
                "ok": expected is None or observed == expected,
            }
            if source is not None:
                record["source"] = source
            summary.controls.append(record)
    return summary
```

- [ ] **Step 12: Add `harness_sources()` and extend the `exit_code` docstring.** Insert
  `harness_sources` directly above `exit_code`, and replace `exit_code`'s docstring. The code is
  unchanged.

```python
def harness_sources(summary) -> dict:
    """Each ACTOR_REQUIRED_DIMENSIONS key -> where its harness verdicts (pass or fail) were
    driven: "mock", "endpoint", "both" (an endpoint verdict plus a mock mechanism record) or
    None (no harness verdict)."""
    labels = {}
    for dim in model_endpoint.ACTOR_REQUIRED_DIMENSIONS:
        seen = summary.harness_probe_sources.get(dim, set())
        labels[dim] = "both" if seen == {"mock", "endpoint"} else next(iter(seen), None)
    return labels


def exit_code(summary, mock_mode, tier2_mode="skip") -> int:
    """3 control mismatch, or a mock `mechanism` record that is not pass:ok (#631) > 6 Tier 2
    on, no claude binary > 4 a shipped case did not run > 5 mock mode, an executed probe is not
    pass > 0. (7, a CLI version mismatch, returns from main() before any case runs.)"""
    if any(not c["ok"] for c in summary.controls):
        return 3
    if tier2_mode == "missing":
        return 6
    if summary.not_run:
        return 4
    if mock_mode and any(value != "pass" for _, value in summary.probes):
        return 5
    return 0
```

- [ ] **Step 13: Re-pin (R).**

- [ ] **Step 14: Verify they pass.** Run the Step 4 command.
  Expected: `22 passed`. Then the full file: all pass. `_fake_harness` still fakes only
  `HARNESS_CHECKS`, and no relay fixture exists yet.

- [ ] **Step 15: Commit.**

```bash
git add scripts/factory_core/capability_suite/runner.py tests/test_capability_suite.py scripts/factory_core/providers/model_endpoint.py
git commit -m "feat(#631): route endpoint-driven harness cases, mechanism records and their sources"
```

---

## Task 6: Result schema 1.1 (`emit.py`, `runner.main`)

**Files:**
- Modify: `scripts/factory_core/capability_suite/emit.py`
- Modify: `scripts/factory_core/capability_suite/runner.py` (`main()` only)
- Modify: `tests/test_capability_suite.py`
- Modify: `scripts/factory_core/providers/model_endpoint.py` (re-pin)

- [ ] **Step 1: Update the `_result` helper** (it passes the two new required arguments). Replace
  it with:

```python
def _result(**kw):
    args = dict(target_kind="endpoint", base_url="http://192.0.2.10:8080",
                results={d: "pass" for d in me.ACTOR_REQUIRED_DIMENSIONS}, harness_pending=(),
                controls=[], harness_sources={d: None for d in me.ACTOR_REQUIRED_DIMENSIONS},
                harness_probes=[], fixture_suite_version="f" * 64,
                now=datetime(2026, 11, 26, tzinfo=timezone.utc))
    args.update(kw)
    return emit.build_result(**args)
```

- [ ] **Step 2: Add the schema tests** directly above `test_written_result_is_rejected_as_evidence`:

```python
def test_result_schema_is_1_1_with_harness_sources_and_probes():
    probe = {"dimension": "streaming", "case": "c", "observed": "pass:ok", "source": "endpoint"}
    body = _result(harness_sources={**{d: None for d in me.ACTOR_REQUIRED_DIMENSIONS},
                                    "streaming": "both"},
                   harness_probes=[probe])["certification_result"]
    assert body["schema_version"] == "1.1"
    assert body["harness_sources"]["streaming"] == "both"
    assert body["harness_probes"] == [probe]


@pytest.mark.parametrize("kw", [
    {"harness_sources": {"streaming": "mock"}},
    {"harness_sources": {d: "rig" for d in me.ACTOR_REQUIRED_DIMENSIONS}},
    {"harness_probes": [{"dimension": "streaming", "case": "c", "observed": "pass",
                         "source": "mock"}]},
    {"harness_probes": [{"dimension": "streaming", "case": "c",
                         "observed": "fail:http://192.0.2.7", "source": "mock"}]},
    {"harness_probes": [{"dimension": "streaming", "case": "c", "observed": "pass:ok",
                         "source": "both"}]},
    {"harness_probes": [{"dimension": "nope", "case": "c", "observed": "pass:ok",
                         "source": "mock"}]},
    {"harness_probes": [{"dimension": "streaming", "case": "c", "observed": "pass:ok",
                         "source": "mock", "extra": 1}]},
])
def test_result_rejects_malformed_harness_sources_and_probes(kw):
    with pytest.raises(ValueError):
        _result(**kw)
```

- [ ] **Step 3: Extend the mock-run test.** In
  `test_mock_run_exits_0_with_streaming_and_role_isolation_pass`, directly after its last line
  (`assert len(body["controls"]) == 17 and all(c["ok"] for c in body["controls"])`), add:

```python
    assert body["schema_version"] == "1.1" and body["harness_probes"] == []
    assert body["harness_sources"] == {d: None for d in me.ACTOR_REQUIRED_DIMENSIONS}
```

- [ ] **Step 4: Verify they fail.**
  Run: `python -m pytest tests/test_capability_suite.py -q -k "result_ or mock_run_exits_0"`
  Expected: failures with `TypeError: build_result() got an unexpected keyword argument
  'harness_sources'`, and the mock run's `KeyError: 'harness_probes'`.

- [ ] **Step 5: Update `emit.py`.**
  - Add `import re` after `import json`.
  - Replace the `SCHEMA_VERSION`/`TARGET_KINDS` lines with the block below.
  - Replace `build_result` with the version below. Its validation rejects any `observed` that is
    not a judge's fixed `value:reason` token, so nothing free-form, and no URL, reaches the file.

```python
SCHEMA_VERSION = "1.1"  # 1.1 (#631): harness_sources, harness_probes, controls[].source
TARGET_KINDS = ("mock", "endpoint")
HARNESS_SOURCES = ("mock", "endpoint", "both", None)
_HARNESS_OBSERVED_RE = re.compile(r"(pass|fail|not_tested):[a-z0-9_]+")
```

```python
def build_result(*, target_kind, base_url, results, harness_pending, controls,
                 harness_sources, harness_probes, fixture_suite_version, now=None) -> dict:
    """The certification_result mapping. Only base_url's scope is recorded, never the URL.

    fixture_suite_version is the digest of the suite that ran (suite_digest()), never the
    committed pin taken on trust: a modified suite must not claim the pinned version.
    """
    if target_kind not in TARGET_KINDS:
        raise ValueError(f"target_kind must be one of {', '.join(TARGET_KINDS)}")
    if (tuple(results) != model_endpoint.ACTOR_REQUIRED_DIMENSIONS
            or any(v not in model_endpoint.RESULT_VALUES for v in results.values())):
        raise ValueError("results must map each ACTOR_REQUIRED_DIMENSIONS key to a RESULT_VALUES "
                         "value")
    if (tuple(harness_sources) != model_endpoint.ACTOR_REQUIRED_DIMENSIONS
            or any(v not in HARNESS_SOURCES for v in harness_sources.values())):
        raise ValueError("harness_sources must map each ACTOR_REQUIRED_DIMENSIONS key to mock, "
                         "endpoint, both or None")
    for probe in harness_probes:
        if (not isinstance(probe, dict) or set(probe) != {"dimension", "case", "observed", "source"}
                or probe["dimension"] not in model_endpoint.ACTOR_REQUIRED_DIMENSIONS
                or not isinstance(probe["case"], str)
                or not isinstance(probe["observed"], str)
                or not _HARNESS_OBSERVED_RE.fullmatch(probe["observed"])
                or probe["source"] not in TARGET_KINDS):
            raise ValueError("harness_probes entries must be {dimension, case, observed, source} "
                             "with observed value:reason and source mock or endpoint")
    return {RESULT_KEY: {
        "schema_version": SCHEMA_VERSION,
        "fixture_suite_version": fixture_suite_version,
        "harness_version": model_endpoint.observed_harness_version(),
        "target_kind": target_kind,
        "base_url_scope": model_endpoint.base_url_scope(base_url),
        "results": dict(results),
        "harness_pending": list(harness_pending),
        "harness_sources": dict(harness_sources),
        "harness_probes": [dict(p) for p in harness_probes],
        "controls": [dict(c) for c in controls],
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
    }}
```

- [ ] **Step 6: Pass the new fields from `main()`.** In `runner.main`, replace

```python
    result = emit.build_result(
        target_kind="mock" if mock_mode else "endpoint", base_url=base_url,
        results=dimension_results(summary.probe_values), harness_pending=harness_pending,
        controls=summary.controls, fixture_suite_version=suite_version)
```

with

```python
    result = emit.build_result(
        target_kind="mock" if mock_mode else "endpoint", base_url=base_url,
        results=dimension_results(summary.probe_values), harness_pending=harness_pending,
        controls=summary.controls, harness_sources=harness_sources(summary),
        harness_probes=summary.harness_probes, fixture_suite_version=suite_version)
```

- [ ] **Step 7: Re-pin (R).**

- [ ] **Step 8: Verify they pass.** Run the Step 4 command.
  Expected: `20 passed`. Then the full file: all pass.

- [ ] **Step 9: Commit.**

```bash
git add scripts/factory_core/capability_suite/emit.py scripts/factory_core/capability_suite/runner.py tests/test_capability_suite.py scripts/factory_core/providers/model_endpoint.py
git commit -m "feat(#631): certification_result 1.1 with harness_sources and harness_probes"
```

---

## Task 7: The ten fixture cases and endpoint-mode runs

**Files:**
- Create: the ten `case.yaml` files below
- Modify: `tests/test_capability_suite.py`
- Modify: `scripts/factory_core/providers/model_endpoint.py` (re-pin)

- [ ] **Step 1: Update the fixture-tree tests.**
  - In `test_fixtures_hold_only_case_yaml_files`, change `== 31` to `== 41`.
  - Replace `test_each_formerly_pending_dimension_ships_one_harness_case` with the three
    definitions below (a helper, the narrowed legacy test, and the new endpoint test).
  - Replace `test_every_mapped_control_and_observation_exists` with the version below.

```python
def _harness_cases():
    return {n: c for n, c in _cases_by_name().items() if c["driver"] == "harness"}


def test_each_formerly_pending_dimension_ships_one_harness_case():
    cases = {n: c for n, c in _harness_cases().items() if c["check"] in runner.HARNESS_CHECKS}
    assert {n: c["check"] for n, c in cases.items()} == {
        "protocol_compatibility/cli_alias_and_nested_agent_resolution": "cli_alias_resolution",
        "tool_call_fidelity/cli_tool_round_trip_and_injection_resistance": "cli_tool_round_trip",
        "context_length/cli_padded_prompt_and_over_limit": "cli_context_window",
        "timeout_cancellation/cli_sigint_closes_connection": "cli_sigint",
    }
    assert all(c["kind"] == "probe" and c["target"] == "mock" for c in cases.values())
    assert sorted(c["dimension"] for c in cases.values()) == sorted(PART_A_PENDING)
    assert set(runner.HARNESS_CHECKS) == {c["check"] for c in cases.values()}


def test_each_formerly_pending_dimension_ships_an_endpoint_harness_probe_and_control():
    cases = _harness_cases()
    probes = {n: c["check"] for n, c in cases.items()
              if c["check"] in runner.HARNESS_RELAY_CHECKS and c["kind"] == "probe"}
    assert probes == {
        "protocol_compatibility/cli_alias_resolution_endpoint": "cli_alias_resolution_endpoint",
        "tool_call_fidelity/cli_tool_round_trip_endpoint": "cli_tool_round_trip_endpoint",
        "context_length/cli_padded_prompt_endpoint": "cli_context_window_endpoint",
        "timeout_cancellation/cli_sigint_endpoint": "cli_sigint_endpoint",
    }
    assert all(cases[n]["target"] == "endpoint" for n in probes)
    assert sorted(cases[n]["dimension"] for n in probes) == sorted(PART_A_PENDING)
    assert set(runner.HARNESS_RELAY_CHECKS) == set(probes.values())
    controls = {n: (c["check"], c.get("fault"), c.get("variant"),
                    f"{c['expect']}:{c['expect_reason']}")
                for n, c in cases.items() if c["kind"] == "control"}
    assert controls == {
        "protocol_compatibility/cli_alias_resolution_endpoint_control": (
            "cli_alias_resolution_endpoint", "wrong_model", None,
            "fail:opus_response_model_mismatch"),
        "tool_call_fidelity/cli_tool_round_trip_endpoint_control": (
            "cli_tool_round_trip_endpoint", None, "unplanted_file", "fail:planted_not_carried"),
        "context_length/cli_padded_prompt_endpoint_control": (
            "cli_context_window_endpoint", "over_limit", None, "fail:not_answered_200"),
        "timeout_cancellation/cli_sigint_endpoint_control": (
            "cli_sigint_endpoint", None, "no_sigint", "fail:connection_not_closed"),
    }
    assert {n: c["check"] for n, c in cases.items() if c["kind"] == "observation"} == {
        "protocol_compatibility/cli_nested_agent_endpoint_scope": "cli_nested_agent_not_tested",
        "context_length/cli_over_limit_endpoint_scope": "cli_over_limit_not_tested",
    }
```

```python
def test_every_mapped_control_and_observation_exists():
    cases = _cases_by_name()
    controls = {n for n, c in cases.items() if c["kind"] == "control"}
    observations = {n for n, c in cases.items() if c["kind"] == "observation"}
    assert controls == {
        "protocol_compatibility/malformed_json_body",
        "protocol_compatibility/response_model_mismatch",
        "protocol_compatibility/count_tokens_404",
        "tool_call_fidelity/forced_tool_choice_ignored",
        "tool_call_fidelity/unparseable_input_json_delta",
        "tool_call_fidelity/unoffered_tool_named",
        "streaming/stream_cut_no_message_stop",
        "streaming/message_start_model_mismatch",
        "context_length/max_tokens_truncated",
        "context_length/silent_input_truncation",
        "context_length/over_limit_error",
        "context_length/cache_hit",
        "timeout_cancellation/connection_refused",
        "timeout_cancellation/stall_before_first_byte",
        "timeout_cancellation/idle_stall_mid_stream",
        "protocol_compatibility/cli_alias_resolution_endpoint_control",
        "tool_call_fidelity/cli_tool_round_trip_endpoint_control",
        "context_length/cli_padded_prompt_endpoint_control",
        "timeout_cancellation/cli_sigint_endpoint_control",
    }
    assert observations == {"protocol_compatibility/count_tokens_observed",
                            "tool_call_fidelity/forced_tool_choice_observed",
                            "protocol_compatibility/cli_nested_agent_endpoint_scope",
                            "context_length/cli_over_limit_endpoint_scope"}
    probes = {n for n, c in cases.items() if c["kind"] == "probe"}
    assert {n for n in probes if not n.startswith("role_isolation/")} == {
        "protocol_compatibility/non_streaming_round_trip", "tool_call_fidelity/tool_use_round_trip",
        "streaming/sse_message_stop", "context_length/sized_request",
        "protocol_compatibility/cli_alias_and_nested_agent_resolution",
        "tool_call_fidelity/cli_tool_round_trip_and_injection_resistance",
        "context_length/cli_padded_prompt_and_over_limit",
        "timeout_cancellation/cli_sigint_closes_connection",
        "protocol_compatibility/cli_alias_resolution_endpoint",
        "tool_call_fidelity/cli_tool_round_trip_endpoint",
        "context_length/cli_padded_prompt_endpoint",
        "timeout_cancellation/cli_sigint_endpoint"}
```

- [ ] **Step 2: Fake the relay checks too.** Replace `_fake_harness` with the version below. A
  relay check run for a control returns that control's own `expect`/`expect_reason`, or every
  faked run would exit 3. The real `runner._harness_probe` still routes each case: the tracking
  wrapper only records which case is running. Without this, a developer machine with the real
  CLI would spawn it from the Tier 2 tests, and CI (no CLI) would see `not_run`.

```python
def _fake_harness(monkeypatch, outcome):
    """Every HARNESS_CHECKS and HARNESS_RELAY_CHECKS entry -> a fake that arms a script (which
    the runner must clear again) and returns `outcome`, or raises it if it is an exception. A
    relay check run for a control returns that control's own expect, so a faked run is never
    exit 3. The real runner._harness_probe still routes every case. Returns the base URLs."""
    calls, current = [], {}
    original = runner._harness_probe

    async def tracking(case, name, **kw):
        current["case"] = case
        return await original(case, name, **kw)

    async def fake(app, base_url):
        calls.append(base_url)
        mock_server.arm(app, mock_server.Script("text"))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def fake_relay(relay_app, relay_url, *, mock_app, **_):
        calls.append(relay_url)
        if mock_app is not None:
            mock_server.arm(mock_app, mock_server.Script("text"))
        case = current["case"]
        if case["kind"] == "control":
            return case["expect"], case["expect_reason"]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(runner, "_harness_probe", tracking)
    for name in runner.HARNESS_CHECKS:
        monkeypatch.setitem(runner.HARNESS_CHECKS, name, fake)
    for name in runner.HARNESS_RELAY_CHECKS:
        monkeypatch.setitem(runner.HARNESS_RELAY_CHECKS, name, fake_relay)
    return calls
```

- [ ] **Step 3: Update the two Tier 2 mock-mode tests.** The two fixed observations call nothing,
  and mock mode now runs 12 checks: 4 legacy, 4 endpoint probes against the mock, and 4 controls.
  Replace both tests with:

```python
def test_tier2_runs_every_harness_case_and_clears_its_script(tmp_path, monkeypatch):
    _tier2(monkeypatch)
    calls = _fake_harness(monkeypatch, ("pass", "ok"))
    fixtures = _with_harness_case(tmp_path)
    code, result, _ = _run(tmp_path, "--base-url", "mock", fixtures_dir=fixtures)
    # The two fixed observations call no check.
    harness_cases = [c for c in runner.load_cases(fixtures) if c["driver"] == "harness"
                     and c["check"] not in harness.FIXED_OBSERVATIONS]
    # Exit 0 also proves the script was cleared: a left-armed `text` script would turn part A's
    # tool_call_fidelity controls and probe (which run after streaming/via_cli) red.
    assert code == 0 and len(calls) == len(harness_cases)
    assert all(url.startswith("http://127.0.0.1:") for url in calls)
    body = result["certification_result"]
    assert body["results"]["streaming"] == "pass" and "streaming" not in body["harness_pending"]
    assert body["harness_sources"]["streaming"] == "mock"


def test_tier2_with_passing_harness_checks_passes_all_six_dimensions(tmp_path, monkeypatch):
    _tier2(monkeypatch)
    calls = _fake_harness(monkeypatch, ("pass", "ok"))
    code, result, _ = _run(tmp_path, "--base-url", "mock")
    body = result["certification_result"]
    assert code == 0 and len(calls) == 12  # 4 mock-driven, 4 endpoint probes, 4 controls
    assert body["results"] == {d: "pass" for d in me.ACTOR_REQUIRED_DIMENSIONS}
    assert body["harness_pending"] == []
    assert body["harness_sources"] == {d: "mock" if d in PART_A_PENDING else None
                                       for d in me.ACTOR_REQUIRED_DIMENSIONS}
    assert len(body["harness_probes"]) == 8
    assert all(p["observed"] == "pass:ok" and p["source"] == "mock"
               for p in body["harness_probes"])
    harness_records = [c for c in body["controls"] if "source" in c]
    assert len(harness_records) == 6 and all(c["source"] == "mock" for c in harness_records)
```

- [ ] **Step 4: Append the endpoint-mode run tests.** They check, in order:
  - routing in both modes, including `served_name`, credential, window, `mock_app` and `variant`
    as each relay check sees them, and that controls run in endpoint mode;
  - `mechanism` records and fixed observations, with `"both"`;
  - exit 3 on a mechanism mismatch, while the endpoint verdict still stands;
  - a mechanism case that does not run is exit 4 without `harness_pending`;
  - the exception text is withheld for an endpoint-upstream case and printed for a mock one;
  - a `not_tested` endpoint probe is listed in `harness_pending`;
  - through `main()` with Tier 2 on: all six `pass`, `"both"` and `harness_probes`;
  - with Tier 2 on, no URL or credential in the result, stdout or stderr;
  - acceptance criterion 2: endpoint mode with Tier 2 off (exit 0), without the binary (exit 6)
    and with a version mismatch (exit 7, no file) behaves as today.

```python
# --- endpoint mode with Tier 2 on (#631) ----------------------------------------------------

def _endpoint_summary(monkeypatch, relay_fake, cases=None, legacy=("pass", "ok"),
                      mock_mode=False):
    """run_cases over the shipped tree (or `cases`) with Tier 2 "ok": HARNESS_CHECKS faked to
    return `legacy`, HARNESS_RELAY_CHECKS faked by `relay_fake`. In endpoint mode a second mock
    stands in for the endpoint, under its own served name and credential."""
    async def fake_legacy(app, base_url):
        if isinstance(legacy, BaseException):
            raise legacy
        return legacy
    for name in runner.HARNESS_CHECKS:
        monkeypatch.setitem(runner.HARNESS_CHECKS, name, fake_legacy)
    for name in runner.HARNESS_RELAY_CHECKS:
        monkeypatch.setitem(runner.HARNESS_RELAY_CHECKS, name, relay_fake)

    async def go():
        mock_runner, mock_url = await mock_server.start()
        end_runner, end_url = await mock_server.start()
        try:
            mock = runner.Target(mock_url, mock_server.SERVED_NAME, mock_server.DUMMY_CREDENTIAL)
            endpoint = mock if mock_mode else runner.Target(end_url, SERVED, SENTINEL)
            summary = await runner.run_cases(
                runner.load_cases() if cases is None else cases, endpoint=endpoint, mock=mock,
                context_tokens=4096, mock_app=mock_runner.app, tier2_mode="ok",
                mock_mode=mock_mode)
            return summary, mock_url, end_url
        finally:
            await mock_runner.cleanup()
            await end_runner.cleanup()
    return asyncio.run(go())


def _relay_cases():
    return [c for c in runner.load_cases() if c["driver"] == "harness"]


def _routing_fake(seen, outcome=("pass", "ok")):
    """A relay check that sends one request through the relay and records where it went."""
    async def fake(relay_app, relay_url, *, served_name, credential, context_tokens, mock_app,
                   variant):
        async with aiohttp.ClientSession() as session:
            async with session.post(relay_url + "/v1/messages", json=ASK) as resp:
                await resp.read()
        seen.append({"dialled": relay_app[relay.RELAYED].dialled[0], "served_name": served_name,
                     "credential": credential, "context_tokens": context_tokens,
                     "mock_app": mock_app is not None, "variant": variant})
        return outcome
    return fake


def test_endpoint_mode_routes_probes_to_the_endpoint_and_controls_to_the_mock(monkeypatch):
    seen = []
    cases = [c for c in _relay_cases() if c["check"] in runner.HARNESS_RELAY_CHECKS]
    summary, mock_url, end_url = _endpoint_summary(monkeypatch, _routing_fake(seen), cases)
    mock_host, end_host = mock_url.split("//")[1], end_url.split("//")[1]
    by_kind = {(c["kind"], c["check"]): s for c, s in zip(cases, seen)}
    for (kind, _check), s in by_kind.items():
        if kind == "probe":
            assert (s["dialled"], s["served_name"], s["credential"], s["context_tokens"],
                    s["mock_app"]) == (end_host, SERVED, SENTINEL, 4096, False)
        else:
            assert (s["dialled"], s["served_name"], s["credential"], s["context_tokens"],
                    s["mock_app"]) == (mock_host, mock_server.SERVED_NAME,
                                       mock_server.DUMMY_CREDENTIAL, mock_server.CONTEXT_TOKENS,
                                       True)
    assert {s["variant"] for s in seen} == {None, "unplanted_file", "no_sigint"}
    assert [p["source"] for p in summary.harness_probes] == ["endpoint"] * 4
    # the fakes' pass:ok for controls is a mismatch: controls really ran, in endpoint mode
    assert [(c["kind"], c["source"], c["ok"]) for c in summary.controls] == [
        ("control", "mock", False)] * 4


def test_mock_mode_routes_every_relay_case_to_the_mock(monkeypatch):
    seen = []
    cases = [c for c in _relay_cases() if c["check"] in runner.HARNESS_RELAY_CHECKS]
    summary, mock_url, _ = _endpoint_summary(monkeypatch, _routing_fake(seen), cases,
                                             mock_mode=True)
    assert {s["dialled"] for s in seen} == {mock_url.split("//")[1]}
    assert all(s["mock_app"] for s in seen)
    assert [p["source"] for p in summary.harness_probes] == ["mock"] * 4


def _expecting_fake(outcome):
    """(tracking runner._harness_probe, relay fake). The fake returns `outcome` for a probe
    (raising it if it is an exception) and a control's own expect for a control, read from the
    case the tracking wrapper last routed."""
    current = {}
    original = runner._harness_probe

    async def tracking(case, name, **kw):
        current["case"] = case
        return await original(case, name, **kw)

    async def fake(relay_app, relay_url, **_):
        case = current["case"]
        if case["kind"] == "control":
            return case["expect"], case["expect_reason"]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome
    return tracking, fake


def test_endpoint_mode_records_mock_driven_cases_as_mechanism(monkeypatch):
    tracking, fake = _expecting_fake(("pass", "ok"))
    monkeypatch.setattr(runner, "_harness_probe", tracking)
    summary, _, _ = _endpoint_summary(monkeypatch, fake, _relay_cases())
    mechanism = [c for c in summary.controls if c["kind"] == "mechanism"]
    assert [(c["case"], c["observed"], c["ok"], c["source"]) for c in mechanism] == [
        (c["case"], "pass:ok", True, "mock") for c in _relay_cases()
        if c["check"] in runner.HARNESS_CHECKS]
    assert {name for name, _ in summary.probes} == {
        f"{c['dimension']}/{c['case']}" for c in _relay_cases()
        if c["check"] in runner.HARNESS_RELAY_CHECKS and c["kind"] == "probe"}
    fixed = [c for c in summary.controls if c["kind"] == "observation"]
    assert sorted(c["observed"] for c in fixed) == [
        "not_tested:nested_agent_not_judged_on_endpoint",
        "not_tested:over_limit_not_judged_on_endpoint"]
    assert all(c["source"] == "endpoint" and c["ok"] for c in fixed)
    assert summary.harness_pending == [] and runner.exit_code(summary, False, "ok") == 0
    assert runner.harness_sources(summary) == {
        d: "both" if d in PART_A_PENDING else None for d in me.ACTOR_REQUIRED_DIMENSIONS}


def test_a_failing_mechanism_record_exits_3(monkeypatch):
    tracking, fake = _expecting_fake(("pass", "ok"))
    monkeypatch.setattr(runner, "_harness_probe", tracking)
    summary, _, _ = _endpoint_summary(monkeypatch, fake, _relay_cases(),
                                      legacy=("fail", "connection_not_closed"))
    assert runner.exit_code(summary, False, "ok") == 3
    assert runner.dimension_results(summary.probe_values)["timeout_cancellation"] == "pass"


def test_a_mechanism_case_that_does_not_run_exits_4_without_pending(monkeypatch):
    tracking, fake = _expecting_fake(("pass", "ok"))
    monkeypatch.setattr(runner, "_harness_probe", tracking)
    summary, _, _ = _endpoint_summary(monkeypatch, fake, _relay_cases(),
                                      legacy=FileNotFoundError("claude"))
    assert len(summary.not_run) == 4 and summary.harness_pending == []
    assert runner.exit_code(summary, False, "ok") == 4
    assert runner.dimension_results(summary.probe_values)["context_length"] == "pass"


def test_an_endpoint_case_fault_prints_its_type_only(monkeypatch, capsys):
    async def raises(relay_app, relay_url, *, mock_app, **_):
        raise RuntimeError("mock detail" if mock_app is not None
                           else "detail http://192.0.2.7:8080")
    cases = [c for c in _relay_cases() if c["check"] == "cli_sigint_endpoint"]
    summary, _, _ = _endpoint_summary(monkeypatch, raises, cases)
    err = capsys.readouterr().err
    assert "cli_sigint_endpoint did not run: RuntimeError\n" in err
    assert "192.0.2.7" not in err
    assert "cli_sigint_endpoint_control did not run: RuntimeError: mock detail" in err
    assert len(summary.not_run) == 2


def test_a_not_tested_endpoint_probe_is_pending(monkeypatch):
    tracking, fake = _expecting_fake(("not_tested", "stream_finished_before_sigint"))
    monkeypatch.setattr(runner, "_harness_probe", tracking)
    summary, _, _ = _endpoint_summary(monkeypatch, fake, _relay_cases())
    assert sorted(set(summary.harness_pending)) == sorted(PART_A_PENDING)
    assert all(p["observed"] == "not_tested:stream_finished_before_sigint"
               for p in summary.harness_probes)
    assert runner.harness_sources(summary) == {
        d: "mock" if d in PART_A_PENDING else None for d in me.ACTOR_REQUIRED_DIMENSIONS}


def _endpoint_run(tmp_path, monkeypatch, *, tier2=True, version=None):
    monkeypatch.setenv("CAPSUITE_TEST_KEY", SENTINEL)
    if tier2:
        _tier2(monkeypatch, version)
    with _endpoint_mock() as url:
        code, result, out = _run(tmp_path, "--base-url", url, "--served-name",
                                 mock_server.SERVED_NAME, "--secret-env", "CAPSUITE_TEST_KEY",
                                 "--context-tokens", "4096")
    return code, result, out, url


def test_endpoint_mode_with_tier2_on_certifies_all_six_and_marks_both(tmp_path, monkeypatch):
    calls = _fake_harness(monkeypatch, ("pass", "ok"))
    code, result, _, _ = _endpoint_run(tmp_path, monkeypatch)
    body = result["certification_result"]
    assert code == 0 and len(calls) == 12
    assert body["results"] == {d: "pass" for d in me.ACTOR_REQUIRED_DIMENSIONS}
    assert body["harness_pending"] == []
    assert body["harness_sources"] == {d: "both" if d in PART_A_PENDING else None
                                       for d in me.ACTOR_REQUIRED_DIMENSIONS}
    assert sorted((p["dimension"], p["observed"], p["source"]) for p in body["harness_probes"]) == [
        (d, "pass:ok", "endpoint") for d in sorted(PART_A_PENDING)]
    assert len([c for c in body["controls"] if c["kind"] == "mechanism"]) == 4


def test_endpoint_mode_with_tier2_on_writes_no_url_or_credential(tmp_path, monkeypatch, capsys):
    _fake_harness(monkeypatch, ("pass", "ok"))
    code, _, out, url = _endpoint_run(tmp_path, monkeypatch)
    captured = capsys.readouterr()
    assert code == 0
    for blob in (out.read_text(), captured.out, captured.err):
        assert SENTINEL not in blob and url.split("//", 1)[1] not in blob


@pytest.mark.parametrize("mode, want", [("off", 0), ("missing", 6)])
def test_endpoint_mode_without_tier2_or_cli_is_unchanged(tmp_path, monkeypatch, mode, want):
    if mode == "missing":
        monkeypatch.setenv(harness.TIER2_ENV, "1")  # observed_harness_version is None (autouse)
    calls = _fake_harness(monkeypatch, ("pass", "ok"))
    code, result, _, _ = _endpoint_run(tmp_path, monkeypatch, tier2=False)
    body = result["certification_result"]
    assert code == want and calls == []
    assert body["harness_pending"] == PART_A_PENDING
    assert all(body["results"][d] == "not_tested" for d in PART_A_PENDING)
    assert body["harness_probes"] == []
    assert body["harness_sources"] == {d: None for d in me.ACTOR_REQUIRED_DIMENSIONS}


def test_endpoint_mode_with_a_cli_version_mismatch_exits_7_and_writes_nothing(tmp_path,
                                                                              monkeypatch):
    calls = _fake_harness(monkeypatch, ("pass", "ok"))
    code, result, out, _ = _endpoint_run(tmp_path, monkeypatch, version="0.0.1")
    assert code == 7 and result is None and not out.parent.exists() and calls == []
```

- [ ] **Step 5: Verify they fail.**
  Run: `python -m pytest tests/test_capability_suite.py -q -k "fixtures_hold or formerly_pending or mapped_control or tier2 or endpoint_mode or mechanism or routes or not_tested_endpoint or fault_prints"`
  Expected: failures. The fixture-count test reports `31 == 41`, and the endpoint-case tests fail
  their set equalities or get empty case lists, because no new fixture exists yet.

- [ ] **Step 6: Create the ten fixture files**, exactly as below. No description contains `": "`,
  which would break the YAML; none contains a URL; `test_committed_fixtures_name_no_rig_address`
  stays green.

`scripts/factory_core/capability_suite/fixtures/context_length/cli_over_limit_endpoint_scope/case.yaml`:

```yaml
case: cli_over_limit_endpoint_scope
dimension: context_length
description: Over-limit behaviour is not judged against a real endpoint, a fixed not_tested. The mock mechanism record covers it.
driver: harness
kind: observation
target: endpoint
check: cli_over_limit_not_tested
```

`scripts/factory_core/capability_suite/fixtures/context_length/cli_padded_prompt_endpoint/case.yaml`:

```yaml
case: cli_padded_prompt_endpoint
dimension: context_length
description: A delimited block of min(window // 2, 30000) filler words passes through claude -p intact, is answered 200 and streams to message_stop. Not a window test.
driver: harness
kind: probe
target: endpoint
check: cli_context_window_endpoint
```

`scripts/factory_core/capability_suite/fixtures/context_length/cli_padded_prompt_endpoint_control/case.yaml`:

```yaml
case: cli_padded_prompt_endpoint_control
dimension: context_length
description: The relay injects over_limit into the mock, so a padded prompt answered 400 fails the endpoint context judge.
driver: harness
kind: control
target: mock
check: cli_context_window_endpoint
fault: over_limit
expect: fail
expect_reason: not_answered_200
```

`scripts/factory_core/capability_suite/fixtures/protocol_compatibility/cli_alias_resolution_endpoint/case.yaml`:

```yaml
case: cli_alias_resolution_endpoint
dimension: protocol_compatibility
description: claude -p through the recording relay with each tier alias names the endpoint's served name and its own credential, every 200 answer streams that name, and the CLI exits 0.
driver: harness
kind: probe
target: endpoint
check: cli_alias_resolution_endpoint
```

`scripts/factory_core/capability_suite/fixtures/protocol_compatibility/cli_alias_resolution_endpoint_control/case.yaml`:

```yaml
case: cli_alias_resolution_endpoint_control
dimension: protocol_compatibility
description: The relay injects wrong_model into the mock, so an answer naming another model fails the endpoint alias judge.
driver: harness
kind: control
target: mock
check: cli_alias_resolution_endpoint
fault: wrong_model
expect: fail
expect_reason: opus_response_model_mismatch
```

`scripts/factory_core/capability_suite/fixtures/protocol_compatibility/cli_nested_agent_endpoint_scope/case.yaml`:

```yaml
case: cli_nested_agent_endpoint_scope
dimension: protocol_compatibility
description: Nested Agent dispatch is not judged against a real endpoint, a fixed not_tested. The mock mechanism record covers the CLI side.
driver: harness
kind: observation
target: endpoint
check: cli_nested_agent_not_tested
```

`scripts/factory_core/capability_suite/fixtures/timeout_cancellation/cli_sigint_endpoint/case.yaml`:

```yaml
case: cli_sigint_endpoint
dimension: timeout_cancellation
description: SIGINT to claude -p while a real streamed reply is in flight makes the CLI hang up on the relay with no retry.
driver: harness
kind: probe
target: endpoint
check: cli_sigint_endpoint
```

`scripts/factory_core/capability_suite/fixtures/timeout_cancellation/cli_sigint_endpoint_control/case.yaml`:

```yaml
case: cli_sigint_endpoint_control
dimension: timeout_cancellation
description: No SIGINT is sent during the mock's stalled stream, so the endpoint SIGINT judge fails connection_not_closed.
driver: harness
kind: control
target: mock
check: cli_sigint_endpoint
variant: no_sigint
expect: fail
expect_reason: connection_not_closed
```

`scripts/factory_core/capability_suite/fixtures/tool_call_fidelity/cli_tool_round_trip_endpoint/case.yaml`:

```yaml
case: cli_tool_round_trip_endpoint
dimension: tool_call_fidelity
description: Offered only Read, the model reads the seeded file; the CLI returns its tool_result and both planted lines reach the endpoint only inside it, with no model switch.
driver: harness
kind: probe
target: endpoint
check: cli_tool_round_trip_endpoint
```

`scripts/factory_core/capability_suite/fixtures/tool_call_fidelity/cli_tool_round_trip_endpoint_control/case.yaml`:

```yaml
case: cli_tool_round_trip_endpoint_control
dimension: tool_call_fidelity
description: The seeded file holds no planted line, so the endpoint tool judge fails planted_not_carried.
driver: harness
kind: control
target: mock
check: cli_tool_round_trip_endpoint
variant: unplanted_file
expect: fail
expect_reason: planted_not_carried
```

- [ ] **Step 7: Re-pin (R).**

- [ ] **Step 8: Verify they pass.** Run the Step 5 command.
  Expected: `34 passed`.
  Then run the full file: `python -m pytest tests/test_capability_suite.py -q`.
  Expected: `219 passed` (some are skipped where `/proc` or `O_NOFOLLOW` is absent).
  These stay unchanged and green because Tier 2 is off in them and every harness case is skipped:
  - `test_every_control_matches_its_expect` (15);
  - `test_every_probe_passes_against_the_mock` (10);
  - `test_observations_are_recorded_with_no_expect`;
  - `test_mock_run_exits_0_with_streaming_and_role_isolation_pass` (17 controls, `harness_pending`
    the four dimensions).

  The four new controls run with the real CLI only in CI's Tier 2 step, where a mismatch is exit 3.

- [ ] **Step 9: Commit.**

```bash
git add scripts/factory_core/capability_suite/fixtures tests/test_capability_suite.py scripts/factory_core/providers/model_endpoint.py
git commit -m "feat(#631): endpoint harness probes, controls and fixed observations for four dimensions"
```

---

## Task 8: Documentation

**Files:**
- Modify: `docs/configuration.md`
- Modify: `docs/features.md`

- [ ] **Step 1: `docs/configuration.md`.** In the capability-suite paragraph (about lines
  564-581), replace exactly this text:

```markdown
Four dimensions also need a
harness case that spawns the pinned `claude -p` against the suite's mock (#624). Those cases run
only with `FACTORY_CAPABILITY_TIER2=1` and a `claude` whose version is
`harness.PINNED_HARNESS_VERSION`: exit 6 if the binary is missing, exit 7 and no result on a
version mismatch. Without the variable they are skipped, stored `not_tested` and listed in the
result's `harness_pending`, so that result qualifies no route. The `docker-build` CI job runs
them in the built image with no network.
```

with:

```markdown
Four dimensions also need
harness cases that spawn the pinned `claude -p` (#624, #631). Those cases run only with
`FACTORY_CAPABILITY_TIER2=1` and a `claude` whose version is `harness.PINNED_HARNESS_VERSION`:
exit 6 if the binary is missing, exit 7 and no result on a version mismatch. Without the
variable they are skipped, stored `not_tested` and listed in the result's `harness_pending`, so
that result qualifies no route. The `docker-build` CI job runs them in the built image against
the mock with no network. Each of the four dimensions has two harness probes: one always drives
the CLI against the suite's own mock (#624), the other drives it through a local recording relay
(`scripts/factory_core/capability_suite/relay.py`, #631) to the target, which is the mock in mock
mode and the endpoint in endpoint mode. In endpoint mode the mock-driven probe still runs, but it
is recorded in `controls` as a `mechanism` record, never as a dimension value; exit 3 also means
a `mechanism` record that is not `pass:ok`. Each endpoint-driven probe has a control that runs
it against a misbehaving mock in both modes. An endpoint-driven `pass` proves less than the mock
one. The alias probe does not cover nested `Agent` dispatch. The context probe is not a window
test: its block is at most 30,000 words (one argv string) and its judge never reads `usage`, so
window acceptance stays with `context_length/sized_request`. Over-limit and nested-`Agent`
behaviour are never judged against an endpoint; two observations record a fixed `not_tested`.
The tool probe depends on the model calling `Read` (`fail:read_not_called` after two attempts),
and the SIGINT probe on the model still streaming when the signal lands
(`not_tested:stream_finished_before_sigint` after three). The CLI gets `--tools Read` or
`--tools ""`, only the endpoint's credential (`--secret-env`), every tier alias set to
`--served-name`, a temporary `HOME`, and proxy variables naming a loopback sink that refuses
everything. A request the CLI sends for any other host fails the case
(`other_host_contacted`). The sink sees only traffic that honours those variables, and verdicts
are taken on that proxied path, on which the pinned CLI skips its `HEAD /api/hello` preflight. A
result (schema `1.1`) lists each executed harness probe's `value:reason` and source in
`harness_probes`, puts a `source` on every harness `controls` record, and states per dimension in
`harness_sources` whether its harness verdicts came from the `mock`, the `endpoint` or `both`.
```

- [ ] **Step 2: `docs/features.md`.** In the **Capability certification suite** row (line 174),
  replace the text of its last cell ("Known limits")

```markdown
The four harness (`claude -p`) probes run only with `FACTORY_CAPABILITY_TIER2=1` and the pinned CLI; without them those dimensions are `not_tested`. No endpoint is certified yet (#562)
```

with

```markdown
The four dimensions' harness (`claude -p`) probes run only with `FACTORY_CAPABILITY_TIER2=1` and the pinned CLI; without them those dimensions are `not_tested`. Against an endpoint they drive the CLI through a recording relay (#631): over-limit and nested-`Agent` behaviour stay fixed `not_tested` observations, the context probe is not a window test, and the tool and SIGINT probes depend on the model's behaviour. No endpoint is certified yet (#562)
```

- [ ] **Step 3: Verify.**
  Run: `grep -n "recording relay" docs/configuration.md docs/features.md && grep -c "mock-only\|only exercise the mock" scripts/factory_core/capability_suite/runner.py scripts/factory_core/capability_suite/harness.py`
  Expected: one hit in each doc, then `0` for both modules (the mock-only wording was replaced
  in Tasks 2 and 5).
  Run: `python -m pytest tests/ -q -k "docs or features or configuration"`
  Expected: no failures.

- [ ] **Step 4: Commit.**

```bash
git add docs/configuration.md docs/features.md
git commit -m "docs(#631): endpoint-driven harness probes, their limits and schema 1.1"
```

---

## Task 9: Final verification

**Files:** none modified, unless a step below fails.

- [ ] **Step 1: Pin is current.**
  Run: `python -m pytest tests/test_capability_suite.py -q -k "pinned_fixture_suite_version or digest"`
  Expected: all pass.

- [ ] **Step 2: Full suite.** Run: `python -m pytest tests/ -q`. Expected: 0 failed.

- [ ] **Step 3: Tier 2 mock run, as CI's `docker-build` step runs it.** Run this only where
  `claude --version` prints `2.1.282`, for example inside the factory image. Otherwise CI's
  blocking Tier 2 step runs it under `--network none` with its unchanged command line and
  10-minute timeout:

```bash
FACTORY_CAPABILITY_TIER2=1 PYTHONPATH=scripts python3 -m factory_core.capability_suite run --base-url mock --out /tmp/capsuite-631.json; echo "exit $?"
python3 -c "import json; b = json.load(open('/tmp/capsuite-631.json'))['certification_result']; print(b['schema_version'], set(b['results'].values()), b['harness_pending'], b['harness_sources'], len(b['harness_probes']), all(c['ok'] for c in b['controls']))"
```

  Expected: `exit 0` within about a minute (31 s measured), then
  `1.1 {'pass'} [] {'protocol_compatibility': 'mock', 'tool_call_fidelity': 'mock', 'streaming': None, 'context_length': 'mock', 'role_isolation': None, 'timeout_cancellation': 'mock'} 8 True`.

- [ ] **Step 4: Scope check.**
  Run: `git diff --stat origin/main -- . ':!docs/superpowers' ':!.archon/memory'`
  Expected: only the files in the File Structure table. (The branch also carries the refine
  phase's `.archon/memory/architecture.md` commit and this spec and plan; leave them as they
  are.)
  Run: `git diff origin/main -- .github Dockerfile deploy config scheduler.sh entrypoint.sh scripts/factory_core/providers/model_adapter.py scripts/factory_core/providers/model_proxy.py scripts/factory_core/capability_suite/mock_server.py`
  Expected: empty.
  Run: `git diff origin/main -- scripts/factory_core/providers/model_endpoint.py`
  Expected: exactly the one `PINNED_FIXTURE_SUITE_VERSION` line changed.

- [ ] **Step 5: No endpoint address or secret in committed files.**
  Run: `git diff origin/main -- scripts tests docs/configuration.md docs/features.md | grep -nE "^\+.*\b(10|172|192\.168)\.[0-9]+\.[0-9]+" || echo none`
  Expected: `none`. Only `127.0.0.1` and `192.0.2.x` appear.

- [ ] **Step 6: Commit** only if a step required a fix, with message
  `fix(#631): <what>`; otherwise nothing to commit.

---

## Notes for the implementer

- **#562 dependency** ("#562 should depend on this ticket") is an issue-tracker edit, not part
  of this change.
- **Sequencing with #628:** #628 is merged on `main` (`34f7e45`, PR #632). Re-pin after
  rebasing, if `main` moves.
- **Why `_fake_harness` tracks the current case:** a relay check's arguments cannot tell a probe
  from a fault control (`variant` is `None` for both alias and context). The tracking wrapper
  records the case the real `runner._harness_probe` is routing, so routing is still exercised.
- **Not prototyped against a real endpoint:** nothing in this plan contacts one. A real
  endpoint's tool choices, timing and `message_start.message.model` echo are only exercised by
  #562's operator run.
