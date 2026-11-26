"""In-process Anthropic-Messages mock for the capability suite (#561, #624).

Plain aiohttp.web (the tests/ TestServer helpers are not in the image), bound to an ephemeral
127.0.0.1 port. With no fault header it answers like a well-behaved endpoint serving
SERVED_NAME; a control names one scripted fault in FAULT_HEADER. It never forwards, never
dials out and never checks the credential it is sent. It records every request it answers in
app[OBSERVED]; a harness case (#624) arms a Script there before it spawns the CLI.
"""
import asyncio
import json
from dataclasses import dataclass, field

from aiohttp import web

SERVED_NAME = "capability-suite-mock"
DUMMY_CREDENTIAL = "capability-suite-dummy-credential"
FALLBACK_MODEL = "capability-suite-fallback"
# The context window the mock declares: the context_length probe's default size in mock mode.
CONTEXT_TOKENS = 8192
FAULT_HEADER = "X-Capability-Fault"
FAULTS = (
    "malformed_json", "wrong_model", "count_tokens_404", "ignore_tool_choice",
    "bad_input_json", "unoffered_tool", "cut_stream", "max_tokens_stop", "truncate_input",
    "over_limit", "stall_first_byte", "stall_mid_stream", "cache_hit",
)
# Longer than any control's timeout; a stalled handler is cancelled when the client hangs up.
STALL_SECONDS = 3.0
TRUNCATED_INPUT_TOKENS = 100
# Harness cases (#624): the id of every fabricated tool_use, and the reply scripts.
TOOL_USE_ID = "toolu_capability_suite_1"
SCRIPTS = ("text", "read", "agent", "stall")
# The `agent` script's nested dispatch: the prompt marks the nested request in the log.
NESTED_AGENT_PROMPT = "capability-suite nested agent: reply with the single word ok."
NESTED_AGENT_MODEL = "opus"  # the explicit model: the fabricated Agent call carries
STALL_INTERVAL_SECONDS = 0.1
_SCRIPT_TOOLS = {"read": "Read", "agent": "Agent"}


@dataclass
class Script:
    """A harness case's reply script, armed in-process before the CLI is spawned."""
    name: str
    read_path: str = ""  # the `read` script's seeded file
    fired: bool = False  # the script's one tool call has been sent


@dataclass
class _Observed:
    """What the mock saw: every request as {method, path, headers, body, status}, whether a
    client hung up mid-response, and the armed Script (None: part A's replies)."""
    requests: list = field(default_factory=list)
    disconnected: bool = False
    script: Script | None = None


# One mutable object per app, set before start: aiohttp deprecates changing a started app.
OBSERVED = web.AppKey("observed", _Observed)


def arm(app, script=None) -> _Observed:
    """Clear app's observation log and arm `script` (None restores part A's replies)."""
    if script is not None and script.name not in SCRIPTS:
        raise ValueError(f"script must be one of {', '.join(SCRIPTS)}")
    observed = app[OBSERVED]
    observed.requests.clear()
    observed.disconnected = False
    observed.script = script
    return observed


def _observe(request, body) -> dict:
    record = {"method": request.method, "path": request.path_qs,
              "headers": {k.lower(): v for k, v in request.headers.items()},
              "body": body, "status": None}
    request.app[OBSERVED].requests.append(record)
    return record


def _texts(messages):
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            yield content
            continue
        for block in content or []:
            if block.get("type") == "text":
                yield block.get("text", "")


def _word_count(messages) -> int:
    """The mock's token estimate: whitespace-separated words of every text block."""
    return sum(len(text.split()) for text in _texts(messages))


def _is_tool_result(message) -> bool:
    content = message.get("content")
    return isinstance(content, list) and any(b.get("type") == "tool_result" for b in content)


def _tool_use(tool, name=None, tool_input=None):
    schema = tool.get("input_schema") or {}
    if tool_input is None:
        tool_input = {key: "Paris" for key in schema.get("required", [])}
    return {"type": "tool_use", "id": TOOL_USE_ID, "name": name or tool["name"],
            "input": tool_input}


def _scripted(body, script):
    """A harness Script's reply: at most one call to the script's tool, picked by name from the
    request's own tools (never a substitute), then plain text. Stateful (script.fired): the
    pinned CLI's follow-up ends in a role: system message, not the tool_result."""
    tool = _SCRIPT_TOOLS.get(script.name)
    offered = {t.get("name"): t for t in body.get("tools") or []}
    if tool is not None and tool in offered and not script.fired:
        script.fired = True
        tool_input = ({"file_path": script.read_path} if tool == "Read" else
                      {"description": "capability-suite probe", "prompt": NESTED_AGENT_PROMPT,
                       "subagent_type": "general-purpose", "model": NESTED_AGENT_MODEL})
        return [_tool_use(offered[tool], tool_input=tool_input)], "tool_use"
    return [{"type": "text", "text": "ok"}], "end_turn"


def _reply(body, fault, script=None):
    """(content blocks, stop_reason) for one request; an armed Script replaces part A's logic."""
    if script is not None:
        return _scripted(body, script)
    tools = body.get("tools") or []
    if tools and not _is_tool_result(body["messages"][-1]):
        choice = body.get("tool_choice") or {}
        if choice.get("type") == "tool":
            if fault == "ignore_tool_choice":
                return [{"type": "text", "text": "ok"}], "end_turn"
            forced = next(t for t in tools if t["name"] == choice["name"])
            return [_tool_use(forced)], "tool_use"
        name = "not_offered_tool" if fault == "unoffered_tool" else None
        return [_tool_use(tools[0], name)], "tool_use"
    return [{"type": "text", "text": "ok"}], "end_turn"


def _error(status, error_type, message):
    return web.json_response({"type": "error", "error": {"type": error_type, "message": message}},
                             status=status)


async def _event(resp, event):
    await resp.write(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode())


async def _stream(request, message, fault):
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await resp.prepare(request)
    start = dict(message, content=[], stop_reason=None,
                 usage=dict(message["usage"], output_tokens=0))
    await _event(resp, {"type": "message_start", "message": start})
    if fault == "stall_mid_stream":
        await asyncio.sleep(STALL_SECONDS)
    if fault == "cut_stream":
        await resp.write(b'event: content_block_start\ndata: {"type": "content_bl')
        await resp.write_eof()
        return resp
    for index, block in enumerate(message["content"]):
        if block["type"] == "text":
            await _event(resp, {"type": "content_block_start", "index": index,
                                "content_block": {"type": "text", "text": ""}})
            delta = {"type": "text_delta", "text": block["text"]}
        else:
            await _event(resp, {"type": "content_block_start", "index": index,
                                "content_block": dict(block, input={})})
            partial = '{"city": "Par' if fault == "bad_input_json" else json.dumps(block["input"])
            delta = {"type": "input_json_delta", "partial_json": partial}
        await _event(resp, {"type": "content_block_delta", "index": index, "delta": delta})
        await _event(resp, {"type": "content_block_stop", "index": index})
    await _event(resp, {"type": "message_delta",
                        "delta": {"stop_reason": message["stop_reason"], "stop_sequence": None},
                        "usage": {"output_tokens": message["usage"]["output_tokens"]}})
    await _event(resp, {"type": "message_stop"})
    await resp.write_eof()
    return resp


async def _stall(request, message, observed):
    """The `stall` script: message_start, then an SSE comment every STALL_INTERVAL_SECONDS
    until the client hangs up. aiohttp reports the hang-up as a cancelled handler
    (handler_cancellation=True) or a reset write; either sets observed.disconnected."""
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await resp.prepare(request)
    start = dict(message, content=[], stop_reason=None,
                 usage=dict(message["usage"], output_tokens=0))
    try:
        await _event(resp, {"type": "message_start", "message": start})
        while True:
            await resp.write(b": capability-suite stall\n\n")
            await asyncio.sleep(STALL_INTERVAL_SECONDS)
    except (ConnectionResetError, asyncio.CancelledError):
        observed.disconnected = True
        raise


async def _messages(request):
    body = await request.json()
    record = _observe(request, body)
    resp = await _answer(request, body)
    record["status"] = resp.status
    return resp


async def _answer(request, body):
    fault = request.headers.get(FAULT_HEADER)
    if fault == "stall_first_byte":
        await asyncio.sleep(STALL_SECONDS)
    words = _word_count(body["messages"])
    if fault == "over_limit" or words > CONTEXT_TOKENS:
        return _error(400, "invalid_request_error", "prompt is too long")
    if fault == "malformed_json":
        return web.Response(body=b'{"type": "message", "content": [',
                            content_type="application/json")
    script = request.app[OBSERVED].script
    content, stop_reason = _reply(body, fault, script)
    message = {
        "id": "msg_capability_suite", "type": "message", "role": "assistant",
        "model": FALLBACK_MODEL if fault == "wrong_model" else SERVED_NAME,
        "content": content,
        "stop_reason": "max_tokens" if fault == "max_tokens_stop" else stop_reason,
        "stop_sequence": None,
        # cache_hit (#628): the endpoint accepted the whole prompt, but reports only a
        # small non-cached input_tokens with the rest under cache_read_input_tokens —
        # the shape a prefix-caching endpoint produces on every run after the first.
        "usage": {"input_tokens": TRUNCATED_INPUT_TOKENS if fault in ("truncate_input", "cache_hit") else words,
                   **({"cache_read_input_tokens": max(0, words - TRUNCATED_INPUT_TOKENS)}
                      if fault == "cache_hit" else {}),
                   "output_tokens": 1},
    }
    if script is not None and script.name == "stall":
        return await _stall(request, message, request.app[OBSERVED])
    if body.get("stream"):
        return await _stream(request, message, fault)
    return web.json_response(message)


async def _count_tokens(request):
    body = await request.json()
    record = _observe(request, body)
    if request.headers.get(FAULT_HEADER) == "count_tokens_404":
        resp = _error(404, "not_found_error", "Not Found")
    else:
        resp = web.json_response({"input_tokens": _word_count(body["messages"])})
    record["status"] = resp.status
    return resp


async def _hello(request):
    """HEAD /api/hello, the pinned CLI's first request (#624): 200, no body."""
    _observe(request, None)["status"] = 200
    return web.Response()


def create_app() -> web.Application:
    app = web.Application()
    app[OBSERVED] = _Observed()
    app.router.add_post("/v1/messages", _messages)
    app.router.add_post("/v1/messages/count_tokens", _count_tokens)
    app.router.add_head("/api/hello", _hello)
    return app


async def start() -> tuple[web.AppRunner, str]:
    """Serve the mock on 127.0.0.1:<ephemeral>; (runner, base URL). Call runner.cleanup()."""
    runner = web.AppRunner(create_app(), handler_cancellation=True, shutdown_timeout=1.0)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, f"http://127.0.0.1:{runner.addresses[0][1]}"
