#!/usr/bin/env python3
"""Dark factory model traffic proxy — transparent Anthropic passthrough + request ledger.

Generic reverse proxy for ANTHROPIC_BASE_URL: forwards every request to the real
Anthropic API unchanged (streaming responses forwarded byte-for-byte), while writing
a compact per-request ledger row and optional raw artifacts. See
docs/archive/2026-07-11-transparent-model-proxy-design.md.

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
"""
import asyncio
import fcntl
import gzip
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timezone

import aiohttp
from aiohttp import web

LEDGER_PATH = pathlib.Path(
    os.environ.get("MODEL_PROXY_LEDGER_PATH", "/var/lib/dark-factory/request-ledger.jsonl")
)
CURRENT_RUN_PATH = pathlib.Path(
    os.environ.get("MODEL_PROXY_CURRENT_RUN_PATH")
    or f"{os.environ.get('CURRENT_RUN_DIR', '/var/lib/dark-factory')}/current-run.json"
)
RAW_ARTIFACT_DIR = pathlib.Path(
    os.environ.get("RAW_ARTIFACT_DIR", "/var/lib/dark-factory/request-artifacts")
)
UPSTREAM = os.environ.get("MODEL_PROXY_UPSTREAM", "https://api.anthropic.com")
PORT = int(os.environ.get("MODEL_PROXY_PORT", "8787"))
MAX_LEDGER_BYTES = int(os.environ.get("MODEL_PROXY_MAX_BYTES", str(100 * 1024 * 1024)))
BACKUP_COUNT = int(os.environ.get("MODEL_PROXY_BACKUP_COUNT", "3"))
RAW_ARTIFACT_CAPTURE = os.environ.get("RAW_ARTIFACT_CAPTURE", "false").lower() == "true"
RAW_ARTIFACT_RETENTION_DAYS = int(os.environ.get("RAW_ARTIFACT_RETENTION_DAYS", "7"))
SEQ_URL = os.environ.get("SEQ_URL", "http://seq:5341")

# Adapter identity on every ledger row (#558): which code path produced the row.
ADAPTER_ID = "model_proxy/anthropic-messages-passthrough"

# x-factory-correlation carries no secret, but §4.3 puts every correlation header in the
# redaction set; its fields are already ledger columns (#558).
_REDACT_EXACT = {"authorization", "x-api-key", "api-key", "x-factory-correlation"}
_REDACT_PATTERN = re.compile(r"^x-factory-.*-(token|secret)$", re.IGNORECASE)


def redact_headers(headers: dict) -> dict:
    """Return a copy of headers with credential-like values replaced."""
    redacted = {}
    for key, value in headers.items():
        if key.lower() in _REDACT_EXACT or _REDACT_PATTERN.match(key):
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = value
    return redacted


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_current_run() -> dict:
    """Best-effort correlation read — never raises, defaults to unknown.

    `stage` is written by entrypoint.sh from INTENT (see Task 7): exact for
    single-phase intents (refine/plan/deconflict/close/fix-main/recheck), "unknown"
    for multi-phase intents (fix/continue) where no investigated Archon data source
    exposes per-node timing — see the plan's Architecture "Stage attribution" note.
    """
    try:
        data = json.loads(CURRENT_RUN_PATH.read_text(encoding="utf-8"))
        return {
            "run_id": data.get("run_id") or "unknown",
            "issue_number": int(data.get("issue_number") or 0),
            "intent": data.get("intent") or "unknown",
            "stage": data.get("stage") or "unknown",
        }
    except Exception:
        return {"run_id": "unknown", "issue_number": 0, "intent": "unknown", "stage": "unknown"}


def _correlation_state(run: dict) -> str:
    """§4.4: the current-run.json pointer is today's only correlation source — never upgraded."""
    return "unknown" if run["run_id"] == "unknown" else "inferred_pointer"


def build_ledger_row(
    *, endpoint, method, model, status, duration_ms, input_tokens, output_tokens,
    cache_read_tokens, cache_creation_tokens, tool_count, tool_bytes, system_bytes,
    request_bytes, largest_tools, streamed, run_id=None, issue_number=None, intent=None,
    stage=None, endpoint_id=None, hosting_location="cloud", role=None, request_id=None,
    failure_class=None, correlation="unknown", adapter_id=ADAPTER_ID, payload_decision=None,
) -> dict:
    return {
        "timestamp": _timestamp(),
        "run_id": run_id or "unknown",
        "issue_number": int(issue_number or 0),
        "intent": intent or "unknown",
        "stage": stage or "unknown",
        "persona": "unknown",
        "endpoint": endpoint,
        "method": method,
        "model": model,
        "status": status,
        "duration_ms": duration_ms,
        "gen_ai.usage.input_tokens": input_tokens,
        "gen_ai.usage.output_tokens": output_tokens,
        "gen_ai.usage.cache_read_input_tokens": cache_read_tokens,
        "gen_ai.usage.cache_creation_input_tokens": cache_creation_tokens,
        "tool_count": tool_count,
        "tool_bytes": tool_bytes,
        "system_bytes": system_bytes,
        "request_bytes": request_bytes,
        "largest_tools": largest_tools,
        "streamed": streamed,
        # Always 0: the proxy is a single-pass forwarder (one inbound request ->
        # one outbound request, never retried at this layer — see requirement 2/3).
        # Claude-Code-level retries/fallbacks arrive as separate HTTP requests and
        # become their own correlated ledger rows; see the Architecture section's
        # "Retry/fallback metadata" note and the rollout doc.
        "retry_count": 0,
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
    }


def _rotate_if_needed(path: pathlib.Path) -> None:
    if not path.exists() or path.stat().st_size < MAX_LEDGER_BYTES:
        return
    for i in range(BACKUP_COUNT - 1, 0, -1):
        src = path.with_suffix(path.suffix + f".{i}")
        dst = path.with_suffix(path.suffix + f".{i + 1}")
        if src.exists():
            src.rename(dst)
    oldest = path.with_suffix(path.suffix + f".{BACKUP_COUNT}")
    if oldest.exists():
        oldest.unlink()
    path.rename(path.with_suffix(path.suffix + ".1"))


def append_ledger(row: dict) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    _rotate_if_needed(LEDGER_PATH)
    with open(LEDGER_PATH, "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.write(json.dumps(row) + "\n")
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def post_seq_ledger(row: dict) -> None:
    payload = {
        "Events": [
            {
                "Timestamp": row.get("timestamp", _timestamp()),
                "Level": "Information",
                "MessageTemplate": "factory.model_proxy.request endpoint={Endpoint} status={Status}",
                "Properties": {
                    "gen_ai.system": "dark-factory-model-proxy",
                    "gen_ai.operation.name": "model_proxy.request",
                    "gen_ai.usage.input_tokens": row.get("gen_ai.usage.input_tokens", 0),
                    "gen_ai.usage.output_tokens": row.get("gen_ai.usage.output_tokens", 0),
                    "Endpoint": row.get("endpoint", ""),
                    "Model": row.get("model", ""),
                    "Status": row.get("status", 0),
                    "DurationMs": row.get("duration_ms", 0),
                    "RunId": row.get("run_id", ""),
                    "IssueNumber": row.get("issue_number", 0),
                    "Intent": row.get("intent", ""),
                    "ToolCount": row.get("tool_count", 0),
                    "ToolBytes": row.get("tool_bytes", 0),
                    "SystemBytes": row.get("system_bytes", 0),
                    "RequestBytes": row.get("request_bytes", 0),
                },
            }
        ]
    }
    endpoint = f"{SEQ_URL.rstrip('/')}/api/events/raw"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(endpoint, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception:
        pass  # non-fatal: ledger file was already written


def write_raw_artifact(run_id: str, seq: int, payload: dict) -> None:
    if not RAW_ARTIFACT_CAPTURE:
        return
    run_dir = RAW_ARTIFACT_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / f"{seq}.json").write_text(json.dumps(payload), encoding="utf-8")


def sweep_raw_artifacts() -> None:
    if not RAW_ARTIFACT_DIR.exists():
        return
    cutoff = time.time() - (RAW_ARTIFACT_RETENTION_DAYS * 86400)
    for run_dir in RAW_ARTIFACT_DIR.iterdir():
        if not run_dir.is_dir():
            continue
        try:
            if run_dir.stat().st_mtime < cutoff:
                for f in run_dir.iterdir():
                    f.unlink()
                run_dir.rmdir()
        except OSError:
            continue  # best-effort — a sweep failure must never crash the proxy


_TOKEN_RE = re.compile(rb'"input_tokens"\s*:\s*(\d+)|"output_tokens"\s*:\s*(\d+)')


def _extract_usage_from_bytes(data: bytes) -> dict:
    """Best-effort scan for token usage in a (possibly streamed) SSE/JSON body.

    Non-streaming responses are fully parsed as JSON by the caller; this is the
    fallback used for SSE bodies, where we must not buffer/parse the full stream.
    Returns the LAST matches seen (Anthropic emits running usage updates).
    """
    usage = {"input_tokens": 0, "output_tokens": 0}
    for m in _TOKEN_RE.finditer(data):
        if m.group(1):
            usage["input_tokens"] = int(m.group(1))
        elif m.group(2):
            usage["output_tokens"] = int(m.group(2))
    return usage


def _measure_request(body_bytes: bytes) -> dict:
    tool_count = tool_bytes = system_bytes = 0
    largest_tools = []
    try:
        parsed = json.loads(body_bytes) if body_bytes else {}
        tools = parsed.get("tools") or []
        tool_count = len(tools)
        tool_sizes = [
            {"name": t.get("name", "?"), "bytes": len(json.dumps(t))} for t in tools
        ]
        tool_bytes = sum(t["bytes"] for t in tool_sizes)
        largest_tools = sorted(tool_sizes, key=lambda t: t["bytes"], reverse=True)[:5]
        system = parsed.get("system", "")
        system_bytes = len(system) if isinstance(system, str) else len(json.dumps(system))
    except (json.JSONDecodeError, AttributeError):
        pass
    return {
        "tool_count": tool_count,
        "tool_bytes": tool_bytes,
        "system_bytes": system_bytes,
        "largest_tools": largest_tools,
    }


async def _persist_ledger(row: dict) -> None:
    """Write the ledger row and post it to Seq off the event loop.

    append_ledger does blocking flock/write and post_seq_ledger does a blocking
    urlopen (timeout=5) — run both in the default thread executor so a slow
    disk or unreachable Seq never stalls concurrent streaming requests.
    """
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, append_ledger, row)
    await loop.run_in_executor(None, post_seq_ledger, row)


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
    upstream_url = f"{UPSTREAM.rstrip('/')}{request.path_qs}"
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}

    session = request.app["client_session"]
    try:
        async with session.request(
            request.method, upstream_url, headers=fwd_headers, data=body_bytes,
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=10),
        ) as upstream_resp:
            response = web.StreamResponse(
                status=upstream_resp.status, headers=upstream_resp.headers
            )
            await response.prepare(request)
            usage_scan = bytearray()
            async for chunk in upstream_resp.content.iter_any():
                await response.write(chunk)
                usage_scan.extend(chunk)
            await response.write_eof()
    except (aiohttp.ClientError, TimeoutError, OSError) as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        error_body = {
            "error": {
                "type": "factory_model_proxy_upstream_error",
                "message": f"upstream request failed: {exc}",
            }
        }
        row = build_ledger_row(
            endpoint=request.path, method=request.method, model=model, status=504,
            duration_ms=duration_ms, input_tokens=0, output_tokens=0,
            cache_read_tokens=0, cache_creation_tokens=0, streamed=False,
            request_bytes=len(body_bytes), **measurements,
            run_id=correlation["run_id"], issue_number=correlation["issue_number"],
            intent=correlation["intent"], stage=correlation["stage"],
        )
        await _persist_ledger(row)
        return web.json_response(error_body, status=504)

    duration_ms = int((time.monotonic() - start) * 1000)
    # The session runs auto_decompress=False (see _startup), so usage_scan holds raw
    # wire bytes — decompress a local copy for usage parsing and raw artifacts; the
    # client already received the untouched compressed stream.
    scan_bytes = bytes(usage_scan)
    encoding = upstream_resp.headers.get("Content-Encoding", "").lower()
    if encoding in ("gzip", "deflate"):
        try:
            scan_bytes = (
                gzip.decompress(scan_bytes)
                if encoding == "gzip"
                else zlib.decompress(scan_bytes)
            )
        except (OSError, zlib.error):
            pass  # ledger falls back to zero-usage rather than failing the request
    is_streamed = "text/event-stream" in upstream_resp.headers.get("Content-Type", "")
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

    row = build_ledger_row(
        endpoint=request.path, method=request.method, model=model,
        status=upstream_resp.status, duration_ms=duration_ms,
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cache_read_tokens=0, cache_creation_tokens=0, streamed=is_streamed,
        request_bytes=len(body_bytes), **measurements,
        run_id=correlation["run_id"], issue_number=correlation["issue_number"],
        intent=correlation["intent"], stage=correlation["stage"],
    )
    await _persist_ledger(row)

    if RAW_ARTIFACT_CAPTURE:
        seq = int(time.time() * 1000)
        write_raw_artifact(
            correlation["run_id"], seq,
            {
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
            },
        )
    return response


def create_app(destination=None) -> web.Application:
    app = web.Application()
    app["client_session"] = None
    # The endpoint resolved once at startup (#558); None on the cloud-default path.
    app["local_destination"] = destination
    app.router.add_route("*", "/{tail:.*}", handle_proxy)

    async def _startup(app):
        # Must be built here, not in create_app() itself: create_app() runs
        # synchronously in main() before web.run_app() creates and starts its
        # own event loop, so a session built eagerly binds to the wrong loop.
        # auto_decompress=False: upstream headers (Content-Encoding/Content-Length)
        # are forwarded verbatim, so the body must stay the raw wire bytes too —
        # otherwise compressed non-streaming responses reach the client as
        # plaintext under a gzip header with the compressed length.
        app["client_session"] = aiohttp.ClientSession(auto_decompress=False)

    async def _cleanup(app):
        if app["client_session"] is not None:
            await app["client_session"].close()

    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
    return app


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


if __name__ == "__main__":
    main()
