#!/usr/bin/env python3
"""Dark factory model traffic proxy — transparent Anthropic passthrough + request ledger.

Generic reverse proxy for ANTHROPIC_BASE_URL: forwards every request to the real
Anthropic API unchanged (streaming responses forwarded byte-for-byte), while writing
a compact per-request ledger row and optional raw artifacts. See
docs/superpowers/specs/2026-07-11-transparent-model-proxy-design.md.
"""
import asyncio
import fcntl
import json
import os
import pathlib
import re
import time
import urllib.error
import urllib.request
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

_REDACT_EXACT = {"authorization", "x-api-key", "api-key"}
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


def build_ledger_row(
    *, endpoint, method, model, status, duration_ms, input_tokens, output_tokens,
    cache_read_tokens, cache_creation_tokens, tool_count, tool_bytes, system_bytes,
    request_bytes, largest_tools, streamed, run_id=None, issue_number=None, intent=None,
    stage=None,
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
    is_streamed = "text/event-stream" in upstream_resp.headers.get("Content-Type", "")
    if is_streamed:
        usage = _extract_usage_from_bytes(bytes(usage_scan))
    else:
        try:
            usage = json.loads(bytes(usage_scan)).get("usage", {})
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
                # usage_scan already holds the full forwarded response bytes (it was
                # populated alongside response.write(chunk) above, at no extra upstream
                # cost) — requirement 6 asks for request/response artifacts, so persist
                # both sides, not request-only.
                "response_body": bytes(usage_scan).decode("utf-8", errors="replace"),
            },
        )
    return response


def create_app() -> web.Application:
    app = web.Application()
    app["client_session"] = None
    app.router.add_route("*", "/{tail:.*}", handle_proxy)

    async def _startup(app):
        # Must be built here, not in create_app() itself: create_app() runs
        # synchronously in main() before web.run_app() creates and starts its
        # own event loop, so a session built eagerly binds to the wrong loop.
        app["client_session"] = aiohttp.ClientSession()

    async def _cleanup(app):
        if app["client_session"] is not None:
            await app["client_session"].close()

    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
    return app


def main() -> None:
    sweep_raw_artifacts()
    web.run_app(create_app(), port=PORT)


if __name__ == "__main__":
    main()
