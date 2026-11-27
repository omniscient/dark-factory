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
                             "accept-encoding", "proxy-authorization", "proxy-authenticate"})
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
    if not isinstance(body, dict):
        body = None
    record = {"method": request.method, "path": request.path_qs,
              "headers": {k.lower(): v for k, v in request.headers.items()},
              "body": body, "status": None, "response_model": None, "completed": False}
    relayed.requests.append(record)
    if (request.method, request.path) not in FORWARDED:
        record["status"] = 404
        return web.Response(status=404)
    watched = relayed.marker is not None and _has_user_text(body, relayed.marker)
    connection_tokens = {t.strip().lower() for t in request.headers.get("connection", "").split(",")
                         if t.strip()}
    dropped = DROPPED_HEADERS | connection_tokens
    headers = {k: v for k, v in request.headers.items() if k.lower() not in dropped}
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
    except Exception:
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
