"""Capability-suite runner (#561): run the pinned fixture tree against one base URL.

Spec: docs/archive/2026-11-26-pinned-harness-capability-fixtures-design.md. Contract:
docs/hybrid-execution-contract.md §4.5 — this writes a certification_result, an input to the
operator-authored evidence record (#562), never that record.

It talks to the URL it is given and nothing else: no registry entry is loaded and no local
destination is resolved, so _NO_LOCAL_DISPATCH is untouched. Response checks are
model_adapter's merged functions; role binding is role_isolation's. The role those functions
bind is pointer-grade, and nothing here treats it as an authorization.
"""
import argparse
import asyncio
import json
import os
import re
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
import yaml

from factory_core.capability_suite import emit, harness, mock_server
from factory_core.providers import model_adapter, model_endpoint, role_isolation

FIXTURES_DIR = emit.PACKAGE_DIR / "fixtures"
# Dimensions whose required harness (`claude -p`) probes are not shipped: none since #624. A
# harness case skipped in one run (Tier 2 off, no claude binary) is listed in that run's
# result harness_pending instead.
HARNESS_PENDING = ()
DRIVERS = ("http", "harness", "function")
KINDS = ("probe", "control", "observation")
TARGETS = ("mock", "endpoint")
# A dimension's stored value is the worst of its probes' values, in this order.
WORST_FIRST = ("fail", "unsupported", "not_tested", "pass")
DEFAULT_TIMEOUTS = {"connect_seconds": 10.0, "time_to_first_token_seconds": 300.0,
                    "idle_seconds": 120.0, "total_seconds": 900.0}
ANTHROPIC_VERSION = "2023-06-01"
NOT_FOUND_STATUSES = (404, 405, 501)
_CASE_KEYS = {"case", "dimension", "description", "driver", "kind", "target", "check", "fault",
              "timeouts", "expect", "expect_reason", "args", "want"}

WEATHER_TOOL = {
    "name": "get_weather",
    "description": "Current weather for a city.",
    "input_schema": {"type": "object", "properties": {"city": {"type": "string"}},
                     "required": ["city"], "additionalProperties": False},
}
TIME_TOOL = {
    "name": "get_time",
    "description": "Current local time for a city.",
    "input_schema": {"type": "object", "properties": {"city": {"type": "string"}},
                     "required": ["city"], "additionalProperties": False},
}
_ASK = [{"role": "user", "content": "Reply with the single word ok."}]
_ASK_WEATHER = [{"role": "user", "content": "What is the weather in Paris? Use a tool."}]


class CaseError(Exception):
    """A malformed fixture tree: a suite bug, never a finding about the target."""


@dataclass(frozen=True)
class Target:
    base_url: str
    served_name: str
    credential: str


@dataclass
class Summary:
    probe_values: dict = field(default_factory=dict)  # dimension -> [value, ...]
    probes: list = field(default_factory=list)  # (dimension/case, value) of executed probes
    controls: list = field(default_factory=list)  # result-ready control/observation records
    not_run: list = field(default_factory=list)  # dimension/case of every shipped case not run
    harness_pending: list = field(default_factory=list)  # dimension of every harness case not run
    lines: list = field(default_factory=list)


# --- fixture tree ---------------------------------------------------------------------------

def _validate(case, where):
    unknown = set(case) - _CASE_KEYS
    if unknown:
        raise CaseError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
    for key, allowed in (("driver", DRIVERS), ("kind", KINDS), ("target", TARGETS)):
        if case.get(key) not in allowed:
            raise CaseError(f"{where}: {key} must be one of {', '.join(allowed)}")
    checks = {"function": FUNCTION_CHECKS, "harness": HARNESS_CHECKS}.get(case["driver"],
                                                                          HTTP_CHECKS)
    if case.get("check") not in checks:
        raise CaseError(f"{where}: check must be one of {', '.join(sorted(checks))}")
    if case["driver"] == "function" and (case["kind"] != "probe" or case["target"] != "mock"
                                         or not isinstance(case.get("args"), dict)
                                         or not isinstance(case.get("want"), list)):
        raise CaseError(f"{where}: a function case is a mock-target probe with args and want")
    if case["driver"] == "harness" and (case["kind"] != "probe" or case["target"] != "mock"):
        raise CaseError(f"{where}: a harness case is a mock-target probe")
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


def load_cases(fixtures_dir=FIXTURES_DIR) -> list[dict]:
    """Every fixtures/<dimension>/<case>/case.yaml, sorted by path; CaseError if malformed."""
    fixtures_dir = Path(fixtures_dir)
    dimensions = sorted(p.name for p in fixtures_dir.iterdir() if p.is_dir())
    if dimensions != sorted(model_endpoint.ACTOR_REQUIRED_DIMENSIONS):
        raise CaseError(f"{fixtures_dir}: dimension directories must be exactly "
                        f"{', '.join(model_endpoint.ACTOR_REQUIRED_DIMENSIONS)}")
    cases = []
    for path in sorted(fixtures_dir.glob("*/*/case.yaml")):
        where = path.relative_to(fixtures_dir).as_posix()
        case = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(case, dict):
            raise CaseError(f"{where}: must be a mapping")
        if case.get("dimension") != path.parent.parent.name or case.get("case") != path.parent.name:
            raise CaseError(f"{where}: dimension and case must match its directories")
        _validate(case, where)
        cases.append(case)
    return cases


# --- HTTP driver ----------------------------------------------------------------------------

class _Failed(Exception):
    """One classified reason a request did not produce a usable response."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


async def _post(session, target, path, body, case) -> tuple[int, bytes]:
    """One attempt, never retried: (status, body bytes), or _Failed with a §4.6 class from
    model_adapter.classify_failure."""
    timeouts = {**DEFAULT_TIMEOUTS, **(case.get("timeouts") or {})}
    headers = {"x-api-key": target.credential, "anthropic-version": ANTHROPIC_VERSION}
    if case.get("fault"):
        headers[mock_server.FAULT_HEADER] = case["fault"]

    async def attempt():
        request = session.post(target.base_url + path, json=body, headers=headers,
                               allow_redirects=False,
                               timeout=aiohttp.ClientTimeout(connect=timeouts["connect_seconds"]))
        try:
            resp = await asyncio.wait_for(request, timeouts["time_to_first_token_seconds"])
        except aiohttp.ClientError as exc:
            raise _Failed(model_adapter.classify_failure(exc))
        except TimeoutError as exc:
            raise _Failed(model_adapter.classify_failure(exc, ttft_expired=True))
        chunks = []
        async with resp:
            while True:
                first = not chunks
                limit = timeouts["time_to_first_token_seconds" if first else "idle_seconds"]
                try:
                    chunk = await asyncio.wait_for(resp.content.readany(), limit)
                except aiohttp.ClientError as exc:
                    raise _Failed(model_adapter.classify_failure(exc))
                except TimeoutError as exc:
                    raise _Failed(model_adapter.classify_failure(
                        exc, ttft_expired=first, idle_expired=not first))
                if not chunk:
                    return resp.status, b"".join(chunks)
                chunks.append(chunk)

    try:
        return await asyncio.wait_for(attempt(), timeouts["total_seconds"])
    except TimeoutError as exc:
        raise _Failed(model_adapter.classify_failure(exc))


def _is_error_body(data) -> bool:
    try:
        parsed = json.loads(data)
    except ValueError:
        return False
    return isinstance(parsed, dict) and parsed.get("type") == "error"


def _checked(target, status, data, streamed) -> None:
    """model_adapter's response checks, each refusal mapped to one reason; _Failed if any fails."""
    if not 200 <= status < 300:
        raise _Failed("error_response" if _is_error_body(data) else "error_status")
    dest = {"endpoint_id": "capability-suite", "served_name": target.served_name}
    try:
        if streamed:
            model_adapter.check_sse_complete(status, data)
        else:
            model_adapter.check_json_body(status, data)
    except model_adapter.AdapterRefusal:
        raise _Failed("stream_incomplete" if streamed else "malformed_json")
    try:
        present = model_adapter.check_response_model(dest, data, streamed)
    except model_adapter.AdapterRefusal:
        raise _Failed("model_mismatch")
    if not present:
        raise _Failed("model_absent")


def _message(data) -> dict:
    """A non-streaming body that passed _checked, as a message with a content list."""
    message = json.loads(data)
    if not isinstance(message.get("content"), list):
        raise _Failed("malformed_message")
    return message


def _assemble(data) -> dict:
    """The message an SSE stream carried: message_start's message, its content blocks, and
    message_delta's stop_reason/usage. _Failed on an unparseable input_json_delta."""
    message, blocks, partial = {}, {}, {}
    try:
        for line in data.splitlines():
            if not line.startswith(b"data:"):
                continue
            try:
                event = json.loads(line[5:])
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "message_start":
                message = dict(event["message"])
            elif kind == "content_block_start":
                blocks[event["index"]] = dict(event["content_block"])
                partial[event["index"]] = ""
            elif kind == "content_block_delta":
                delta, block = event["delta"], blocks[event["index"]]
                if delta["type"] == "text_delta":
                    block["text"] = block.get("text", "") + delta["text"]
                elif delta["type"] == "input_json_delta":
                    partial[event["index"]] += delta["partial_json"]
            elif kind == "content_block_stop":
                block = blocks[event["index"]]
                if block.get("type") == "tool_use":
                    try:
                        block["input"] = json.loads(partial[event["index"]] or "{}")
                    except ValueError:
                        raise _Failed("tool_input_unparseable")
            elif kind == "message_delta":
                message.update(event.get("delta") or {})
                message["usage"] = {**(message.get("usage") or {}), **(event.get("usage") or {})}
    except (KeyError, TypeError, AttributeError):
        raise _Failed("malformed_stream")
    message["content"] = [blocks[i] for i in sorted(blocks)]
    return message


_JSON_TYPES = {"object": dict, "string": str, "integer": int, "number": (int, float),
               "boolean": bool, "array": list}


def _valid(value, schema) -> bool:
    """The subset of JSON Schema the suite's own tools use: type, properties, required,
    additionalProperties: false."""
    expected = schema.get("type")
    if expected in _JSON_TYPES and (not isinstance(value, _JSON_TYPES[expected])
                                    or (isinstance(value, bool) and expected != "boolean")):
        return False
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        if any(k not in value for k in schema.get("required") or []):
            return False
        if schema.get("additionalProperties") is False and any(k not in props for k in value):
            return False
        return all(_valid(v, props[k]) for k, v in value.items() if k in props)
    return True


def _request(target, messages, **extra) -> dict:
    return {"model": target.served_name, "max_tokens": 64, "messages": messages, **extra}


async def check_round_trip(session, target, case, context_tokens):
    """protocol_compatibility: a non-streaming message answered as the served model."""
    try:
        status, data = await _post(session, target, "/v1/messages", _request(target, _ASK), case)
        _checked(target, status, data, streamed=False)
        _message(data)
    except _Failed as f:
        return "fail", f.reason
    return "pass", "ok"


async def check_sse(session, target, case, context_tokens):
    """streaming: an SSE response that ends in a complete message_stop frame."""
    try:
        status, data = await _post(session, target, "/v1/messages",
                                   _request(target, _ASK, stream=True), case)
        _checked(target, status, data, streamed=True)
        _assemble(data)
    except _Failed as f:
        return "fail", f.reason
    return "pass", "ok"


async def check_unreachable(session, target, case, context_tokens):
    """A local outage: a refused connect is endpoint_unreachable after exactly one attempt."""
    attempts = []
    trace = aiohttp.TraceConfig()

    async def count(*_):
        attempts.append(1)

    trace.on_request_start.append(count)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))  # bound and never listening: every connect is refused
        refused = Target(f"http://127.0.0.1:{sock.getsockname()[1]}", target.served_name,
                         target.credential)
        async with aiohttp.ClientSession(trace_configs=[trace]) as own:
            value, reason = await check_round_trip(own, refused, case, context_tokens)
    return (value, reason) if len(attempts) == 1 else ("fail", "retried")


async def check_count_tokens(session, target, case, context_tokens):
    """An observation (and its control): what POST /v1/messages/count_tokens returns."""
    body = {"model": target.served_name, "messages": _ASK}
    try:
        status, data = await _post(session, target, "/v1/messages/count_tokens", body, case)
    except _Failed as f:
        return "fail", f.reason
    if status in NOT_FOUND_STATUSES:
        return "unsupported", "not_found"
    if not 200 <= status < 300:
        return "fail", "error_response" if _is_error_body(data) else "error_status"
    try:
        parsed = json.loads(data)
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict) or type(parsed.get("input_tokens")) is not int:
        return "fail", "malformed_json"
    return "pass", "ok"


async def check_tool_round_trip(session, target, case, context_tokens):
    """tool_call_fidelity: a streamed tool_use whose input validates against the offered
    input_schema, then a tool_result turn the endpoint accepts."""
    tools = [WEATHER_TOOL]
    try:
        status, data = await _post(session, target, "/v1/messages",
                                   _request(target, _ASK_WEATHER, tools=tools, stream=True), case)
        _checked(target, status, data, streamed=True)
        message = _assemble(data)
        uses = [b for b in message["content"] if b.get("type") == "tool_use"]
        if not uses:
            return "fail", "no_tool_use"
        offered = {t["name"]: t for t in tools}
        if any(u.get("name") not in offered for u in uses):
            return "fail", "tool_not_offered"
        if any(not _valid(u.get("input"), offered[u["name"]]["input_schema"]) for u in uses):
            return "fail", "tool_input_invalid"
        follow_up = _ASK_WEATHER + [
            {"role": "assistant", "content": message["content"]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": u["id"],
                                          "content": "18 C and sunny"} for u in uses]},
        ]
        status, data = await _post(session, target, "/v1/messages",
                                   _request(target, follow_up, tools=tools, stream=True), case)
        _checked(target, status, data, streamed=True)
        _assemble(data)
    except _Failed as f:
        return "fail", f.reason
    return "pass", "ok"


async def check_forced_tool_choice(session, target, case, context_tokens):
    """An observation (and its control): whether a forced tool_choice is honoured."""
    body = _request(target, _ASK_WEATHER, tools=[WEATHER_TOOL, TIME_TOOL],
                    tool_choice={"type": "tool", "name": TIME_TOOL["name"]})
    try:
        status, data = await _post(session, target, "/v1/messages", body, case)
        if 400 <= status < 500:
            return "unsupported", "refused"
        _checked(target, status, data, streamed=False)
        message = _message(data)
    except _Failed as f:
        return "fail", f.reason
    if any(b.get("type") == "tool_use" and b.get("name") == TIME_TOOL["name"]
           for b in message["content"]):
        return "pass", "ok"
    return "unsupported", "ignored"


async def check_context_window(session, target, case, context_tokens):
    """context_length: a request sized to the window, checked through usage.input_tokens
    (never count_tokens). None when no window is known (the case does not run)."""
    if context_tokens is None:
        return None
    words = max(1, context_tokens * 3 // 4)
    prompt = "word " * words + "\nReply with the single word ok."
    try:
        status, data = await _post(session, target, "/v1/messages",
                                   _request(target, [{"role": "user", "content": prompt}]), case)
        _checked(target, status, data, streamed=False)
        message = _message(data)
    except _Failed as f:
        return "fail", f.reason
    if message.get("stop_reason") == "max_tokens":
        return "fail", "truncated"
    sent = (message.get("usage") or {}).get("input_tokens")
    if type(sent) is not int or sent < words // 2:
        return "fail", "input_truncated"
    return "pass", "ok"


HTTP_CHECKS = {
    "round_trip": check_round_trip,
    "sse": check_sse,
    "unreachable": check_unreachable,
    "count_tokens": check_count_tokens,
    "tool_round_trip": check_tool_round_trip,
    "forced_tool_choice": check_forced_tool_choice,
    "context_window": check_context_window,
}
FUNCTION_CHECKS = {
    "bind_dispatch_role": role_isolation.bind_dispatch_role,
    "check_verdict_independence": role_isolation.check_verdict_independence,
}
# check name -> harness.check_*(mock app, mock base URL): spawns the pinned `claude -p`, so a
# harness case runs only when harness.preflight() is "ok" (Tier 2 on, pinned CLI present).
HARNESS_CHECKS = {
    "cli_alias_resolution": harness.check_alias_resolution,
    "cli_tool_round_trip": harness.check_tool_round_trip,
    "cli_context_window": harness.check_context_window,
    "cli_sigint": harness.check_sigint,
}
_SKIPPED = {"skip": f"skipped: {harness.TIER2_ENV} is not 1",
            "missing": "skipped: no claude binary"}


def _function_probe(case):
    got = list(FUNCTION_CHECKS[case["check"]](**case["args"]))
    return ("pass", "ok") if got == case["want"] else ("fail", f"unexpected_{got[1]}")


async def _harness_probe(case, mock_app, mock):
    """One harness case against the mock; None (not run: exit 4) when the CLI cannot be spawned
    or the check hits any other internal fault (spec Requirement 9). The script a check arms is
    always cleared, so the http cases after it get part A's replies."""
    try:
        return await HARNESS_CHECKS[case["check"]](mock_app, mock.base_url)
    except Exception as exc:  # a suite fault, never a finding about the CLI
        print(f"capability suite: harness case {case['dimension']}/{case['case']} did not run: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return None
    finally:
        mock_server.arm(mock_app)


# --- run ------------------------------------------------------------------------------------

async def run_cases(cases, *, endpoint, mock, context_tokens, mock_app=None,
                    tier2_mode="skip") -> Summary:
    """Run every case in order. A control always runs against the mock and its declared
    window; a probe or observation targeting the endpoint uses `endpoint`. A harness case runs
    only in tier2_mode "ok" (harness.preflight); otherwise it adds one not_tested value to its
    dimension and nothing to probes or not_run."""
    summary = Summary()
    async with aiohttp.ClientSession() as session:
        for case in cases:
            name = f"{case['dimension']}/{case['case']}"
            if case["driver"] == "harness" and tier2_mode != "ok":
                summary.probe_values.setdefault(case["dimension"], []).append("not_tested")
                summary.harness_pending.append(case["dimension"])
                summary.lines.append(f"{name} [{case['kind']}] {_SKIPPED[tier2_mode]}")
                continue
            if case["driver"] == "harness":
                outcome = await _harness_probe(case, mock_app, mock)
            elif case["driver"] == "function":
                outcome = _function_probe(case)
            elif case["kind"] == "control" or case["target"] == "mock":
                outcome = await HTTP_CHECKS[case["check"]](session, mock, case,
                                                           mock_server.CONTEXT_TOKENS)
            else:
                outcome = await HTTP_CHECKS[case["check"]](session, endpoint, case,
                                                           context_tokens)
            if outcome is None:
                if case["driver"] == "harness":
                    summary.harness_pending.append(case["dimension"])
                summary.not_run.append(name)
                summary.lines.append(f"{name} [{case['kind']}] not run")
                if case["kind"] == "probe":
                    summary.probe_values.setdefault(case["dimension"], []).append("not_tested")
                continue
            value, reason = outcome
            observed = f"{value}:{reason}"
            summary.lines.append(f"{name} [{case['kind']}] {observed}")
            if case["kind"] == "probe":
                summary.probe_values.setdefault(case["dimension"], []).append(value)
                summary.probes.append((name, value))
                continue
            expected = (f"{case['expect']}:{case['expect_reason']}"
                        if case["kind"] == "control" else None)
            summary.controls.append({
                "dimension": case["dimension"], "case": case["case"], "kind": case["kind"],
                "expected": expected, "observed": observed,
                "ok": expected is None or observed == expected,
            })
    return summary


def dimension_results(probe_values) -> dict:
    """Each ACTOR_REQUIRED_DIMENSIONS key's stored value: the worst of its probes, with one
    implicit not_tested for a HARNESS_PENDING dimension."""
    results = {}
    for dim in model_endpoint.ACTOR_REQUIRED_DIMENSIONS:
        values = list(probe_values.get(dim, []))
        if dim in HARNESS_PENDING:
            values.append("not_tested")
        results[dim] = min(values, key=WORST_FIRST.index) if values else "not_tested"
    return results


def exit_code(summary, mock_mode, tier2_mode="skip") -> int:
    """3 control mismatch > 6 Tier 2 on, no claude binary > 4 a shipped case did not run > 5
    mock mode, an executed probe is not pass > 0. (7, a CLI version mismatch, returns from
    main() before any case runs.)"""
    if any(not c["ok"] for c in summary.controls):
        return 3
    if tier2_mode == "missing":
        return 6
    if summary.not_run:
        return 4
    if mock_mode and any(value != "pass" for _, value in summary.probes):
        return 5
    return 0


async def _run(cases, *, base_url, served_name, credential, context_tokens, tier2_mode="skip"):
    mock_runner, mock_url = await mock_server.start()
    try:
        mock = Target(mock_url, mock_server.SERVED_NAME, mock_server.DUMMY_CREDENTIAL)
        endpoint = (mock if base_url == "mock"
                    else Target(base_url.rstrip("/"), served_name, credential))
        summary = await run_cases(cases, endpoint=endpoint, mock=mock,
                                  context_tokens=context_tokens, mock_app=mock_runner.app,
                                  tier2_mode=tier2_mode)
        return summary, endpoint.base_url
    finally:
        await mock_runner.cleanup()


_SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _is_http_url(value) -> bool:
    try:
        parts = urlsplit(value)
        parts.port  # raises ValueError on a malformed port
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m factory_core.capability_suite")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run the pinned fixture suite and write a "
                                     "certification_result")
    run.add_argument("--base-url", required=True,
                     help="'mock' for the in-process mock, or the endpoint's http(s):// URL")
    run.add_argument("--out", required=True, help="where to write the certification_result")
    run.add_argument("--served-name", help="the endpoint's served model name (endpoint mode)")
    run.add_argument("--secret-env", help="name of the env var holding the endpoint credential "
                                          "(endpoint mode)")
    run.add_argument("--context-tokens", type=int,
                     help="the window the context_length probe is sized to (endpoint mode: no "
                          "default, the probe does not run without it)")
    return parser


def main(argv=None, *, fixtures_dir=FIXTURES_DIR) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    mock_mode = args.base_url == "mock"
    if not mock_mode and not _is_http_url(args.base_url):
        parser.error("--base-url must be 'mock' or an http(s):// URL with a host")
    if "model-registry" in Path(args.out).resolve().parts:
        parser.error("--out must not be under a model-registry directory: a certification_result "
                     "is never a capability-evidence record")
    if args.context_tokens is not None and args.context_tokens <= 0:
        parser.error("--context-tokens must be a positive integer")
    credential = mock_server.DUMMY_CREDENTIAL
    if not mock_mode:
        if not args.served_name:
            parser.error("--served-name is required with an endpoint --base-url")
        if not args.secret_env or not _SECRET_NAME_RE.match(args.secret_env):
            parser.error("--secret-env must name an environment variable (e.g. LOCAL_MODEL_KEY)")
        credential = os.environ.get(args.secret_env, "")
        if not credential:
            parser.error(f"{args.secret_env} is not set")  # the name, never a value
    context_tokens = args.context_tokens
    if mock_mode and context_tokens is None:
        context_tokens = mock_server.CONTEXT_TOKENS
    # After argument validation (a usage error stays exit 2), before the tree, the mock or any
    # result file: a version mismatch writes nothing.
    tier2_mode, observed_version = harness.preflight(harness.tier2_enabled())
    if tier2_mode == "mismatch":
        print(f"capability suite: claude --version is {observed_version}, not "
              f"harness.PINNED_HARNESS_VERSION {harness.PINNED_HARNESS_VERSION}: no result "
              "written", file=sys.stderr)
        return 7
    if tier2_mode == "missing":
        print(f"capability suite: {harness.TIER2_ENV}=1 but no claude binary reports a "
              "version: every harness case is not_tested", file=sys.stderr)
    try:
        cases = load_cases(fixtures_dir)
        suite_version = emit.suite_digest(fixtures_dir=fixtures_dir)
    except (CaseError, model_endpoint.RegistryError) as e:
        print(f"capability suite: fixture tree is invalid: {e}", file=sys.stderr)
        return 3
    if suite_version != model_endpoint.PINNED_FIXTURE_SUITE_VERSION:
        print("capability suite: WARNING: this suite's digest differs from "
              "model_endpoint.PINNED_FIXTURE_SUITE_VERSION. The result records the digest that "
              "ran; evidence written from it resolves stale.", file=sys.stderr)
    summary, base_url = asyncio.run(_run(
        cases, base_url=args.base_url, served_name=args.served_name or mock_server.SERVED_NAME,
        credential=credential, context_tokens=context_tokens, tier2_mode=tier2_mode))
    harness_pending = [d for d in model_endpoint.ACTOR_REQUIRED_DIMENSIONS
                       if d in HARNESS_PENDING or d in summary.harness_pending]
    result = emit.build_result(
        target_kind="mock" if mock_mode else "endpoint", base_url=base_url,
        results=dimension_results(summary.probe_values), harness_pending=harness_pending,
        controls=summary.controls, fixture_suite_version=suite_version)
    emit.write_result(args.out, result)
    for line in summary.lines:
        print(line)
    code = exit_code(summary, mock_mode, tier2_mode)
    print(f"capability suite: wrote {args.out} (exit {code})")
    return code
