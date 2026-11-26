"""In-process Anthropic-Messages mock for the capability suite (#561).

Plain aiohttp.web (the tests/ TestServer helpers are not in the image), bound to an ephemeral
127.0.0.1 port. With no fault header it answers like a well-behaved endpoint serving
SERVED_NAME; a control names one scripted fault in FAULT_HEADER. It never forwards, never
dials out and never checks the credential it is sent.
"""
import asyncio
import json

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
    "over_limit", "stall_first_byte", "stall_mid_stream",
)
# Longer than any control's timeout; a stalled handler is cancelled when the client hangs up.
STALL_SECONDS = 3.0
TRUNCATED_INPUT_TOKENS = 100


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


def _tool_use(tool, name=None):
    schema = tool.get("input_schema") or {}
    return {"type": "tool_use", "id": "toolu_capability_suite_1", "name": name or tool["name"],
            "input": {key: "Paris" for key in schema.get("required", [])}}


def _reply(body, fault):
    """(content blocks, stop_reason) for one request."""
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


async def _messages(request):
    fault = request.headers.get(FAULT_HEADER)
    body = await request.json()
    if fault == "stall_first_byte":
        await asyncio.sleep(STALL_SECONDS)
    words = _word_count(body["messages"])
    if fault == "over_limit" or words > CONTEXT_TOKENS:
        return _error(400, "invalid_request_error", "prompt is too long")
    if fault == "malformed_json":
        return web.Response(body=b'{"type": "message", "content": [',
                            content_type="application/json")
    content, stop_reason = _reply(body, fault)
    message = {
        "id": "msg_capability_suite", "type": "message", "role": "assistant",
        "model": FALLBACK_MODEL if fault == "wrong_model" else SERVED_NAME,
        "content": content,
        "stop_reason": "max_tokens" if fault == "max_tokens_stop" else stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": TRUNCATED_INPUT_TOKENS if fault == "truncate_input" else words,
                  "output_tokens": 1},
    }
    if body.get("stream"):
        return await _stream(request, message, fault)
    return web.json_response(message)


async def _count_tokens(request):
    if request.headers.get(FAULT_HEADER) == "count_tokens_404":
        return _error(404, "not_found_error", "Not Found")
    body = await request.json()
    return web.json_response({"input_tokens": _word_count(body["messages"])})


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/v1/messages", _messages)
    app.router.add_post("/v1/messages/count_tokens", _count_tokens)
    return app


async def start() -> tuple[web.AppRunner, str]:
    """Serve the mock on 127.0.0.1:<ephemeral>; (runner, base URL). Call runner.cleanup()."""
    runner = web.AppRunner(create_app(), handler_cancellation=True, shutdown_timeout=1.0)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, f"http://127.0.0.1:{runner.addresses[0][1]}"
