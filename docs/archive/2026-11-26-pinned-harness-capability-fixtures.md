# Pinned-Harness Capability Fixtures — Part A Implementation Plan

**Issue:** #561

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship part A of #561: the `scripts/factory_core/capability_suite/` package (an in-process Anthropic-Messages mock, an HTTP-driven runner, a `certification_result` emitter and a 26-case fixture tree), with `model_endpoint.PINNED_FIXTURE_SUITE_VERSION` set to the suite's SHA-256 digest and Tier 1 tests in `pytest tests/`.

**Architecture:** One shipped package under `scripts/` (which the image copies; `tests/` it does not). `python3 -m factory_core.capability_suite run --base-url <mock|http(s)://...> --out <path>` always starts the mock on `127.0.0.1:<ephemeral>`. Controls always run against the mock. Probes and observations run against `--base-url`, or against the mock when `--base-url mock`. Response checks call `model_adapter`'s merged functions (`check_json_body`, `check_sse_complete`, `check_response_model`, `classify_failure`). The `role_isolation` probes call `bind_dispatch_role()`/`check_verdict_independence()`. Dimension values and vocabularies come from `model_endpoint` (`ACTOR_REQUIRED_DIMENSIONS`, `RESULT_VALUES`, `base_url_scope`, `tree_sha256`, `observed_harness_version`). The four dimensions whose harness probes are deferred to part B (#624) are listed in `runner.HARNESS_PENDING` and store `not_tested`.

**Tech Stack:** Python 3.12+ (CI runs 3.12, the image 3.14), `aiohttp` (client and `aiohttp.web` server), `pyyaml`, `pytest`. No new dependency: CI installs `pytest pyyaml aiohttp`, and the image already has `pyyaml aiohttp`. No `pytest-asyncio`: tests drive coroutines with `asyncio.run`, the same approach `tests/test_factory_model_proxy.py` takes with `run_until_complete`.

**Spec:** `docs/superpowers/specs/2026-11-26-pinned-harness-capability-fixtures-design.md` (approved at 9794c58, operator review 2026-11-26).

---

## Ground truth (verified on this branch, 2026-11-26)

- Every interface the spec names exists on `main` with the signature the code below calls:
  `model_endpoint.{ACTOR_REQUIRED_DIMENSIONS, RESULT_VALUES, PINNED_FIXTURE_SUITE_VERSION (= None), tree_sha256, base_url_scope, observed_harness_version, load_evidence, role_problems, RegistryError}`,
  `model_adapter.{check_json_body, check_sse_complete, check_response_model, classify_failure, AdapterRefusal}`,
  `role_isolation.{bind_dispatch_role, check_verdict_independence}`. `model_proxy.py` lives at
  `scripts/factory_core/model_proxy.py`, not under `providers/`. This plan does not touch it.
- `model_adapter.check_response_model(dest, data, streamed)` reads only `dest["served_name"]` and
  `dest["endpoint_id"]`, so the runner passes the plain mapping
  `{"endpoint_id": "capability-suite", "served_name": <served name>}` and resolves no destination.
- `model_endpoint.load_evidence()` accepts a file only if its single top-level key is
  `capability_evidence`. A JSON `certification_result` file parses as YAML and is rejected with
  `must contain exactly one capability_evidence mapping`.
- The whole plan was executed in a scratch copy of this branch before it was written. Each task's
  tests fail before its implementation step and pass after it. The full `pytest tests/` run with
  the pin set: **3839 passed**. With the pin set, exactly the two predicted
  `tests/test_provider_model_endpoint.py` tests fail until Task 5 changes them. A mock run takes
  about 1 s.

## Plan-level decisions (where the spec leaves a detail open)

Each decision is stated once here and is not repeated in the tasks.

1. **`driver: function` for the `role_isolation` probes.** The spec lists "`driver: http | harness`"
   for request-producing cases. Its scope also lists "the `role_isolation` pure-function probes"
   as a separate item, and those cases produce no request. Labelling them `http` would be false,
   so they declare `driver: function` (with `kind: probe`, `target: mock`). The loader accepts
   `http`, `harness` and `function`. A `harness` case is still never executed (exit 4), as the
   spec requires.
2. **Controls compare a value *and* a reason.** Every control carries `expect` (one of
   `RESULT_VALUES`) and `expect_reason`, a short classification code such as `malformed_json`,
   `model_mismatch`, `stream_incomplete`, `timeout_idle` or `endpoint_unreachable`. The
   `controls` entries record `expected`/`observed` as `"<value>:<reason>"`. Comparing the
   value alone would let two different faults that both yield `fail` hide a misclassification.
   Matching on the reason is what makes "change a control's fault so the classification differs →
   exit 3" testable. The §4.6 classes come from `model_adapter.classify_failure`. The other
   reasons name the `model_adapter` check that refused the response.
3. **Fault selection is a request header, sent only by a control.** The runner adds
   `X-Capability-Fault: <fault>` only for a case that has `fault:`, and only a control may have
   one. Probes and observations send no extra header, so a real endpoint never sees one.
4. **A malformed fixture tree exits 3 and writes no result.** A malformed tree is a suite bug, and
   exit 3 is the spec's "suite bug" code. No result is written because no case ran. This case is
   not in the spec's table.
5. **The result file is JSON** (`json.dumps(..., indent=2)`). The spec gives the field list, not a
   serialization. JSON is valid YAML, so `load_evidence()` rejects it on its key, which is the
   check the spec asks a test to make.
6. **The `context_length` probe** sends `max(1, N*3//4)` filler words, where N is
   `--context-tokens`. It passes only when the response is complete (not `stop_reason:
   max_tokens`) and `usage.input_tokens >= words // 2`. The 3/4 leaves headroom for tokenizer
   differences. The 1/2 threshold catches silent truncation and tolerates tokenizer skew. The mock
   declares an 8192-token window (`mock_server.CONTEXT_TOKENS`) and estimates tokens by word count.
7. **Default request timeouts** for probes and observations are connect 10 s,
   time-to-first-token 300 s, idle 120 s and total 900 s, generous for local hardware. A case may
   override them with `timeouts:`. The two stall controls use 0.3 s against the mock's 3 s
   stall, so the outcome does not depend on timing.
8. **"No retry" is observed, not assumed.** The `connection_refused` control counts request
   starts with an `aiohttp.TraceConfig` and reports `fail:retried` if there was more than one.

## Memory and hazards baked into the steps

- **Stale bytecode can hide a pin change.** Python reuses a `.pyc` whose source has the same
  mtime second and the same size. A 64-hex pin replacing another 64-hex pin keeps the size, so
  computing the digest, which imports `model_endpoint`, and then editing the pin within one
  second makes later imports return the old value. This happened in the dry run. Task 5 runs
  every pin-related command with `PYTHONDONTWRITEBYTECODE=1` and clears `__pycache__` before
  verifying.
- **[PATTERN] two-dot diff** (codebase-patterns memory, #250). The final scope check uses the
  spec's `git diff --name-only origin/main...HEAD`. If that lists a file outside the expected set,
  confirm with the two-dot `git diff origin/main HEAD -- <file>` before treating it as a real
  out-of-scope change.
- **[PATTERN] refine artifacts transfer** (#387/#42). `setup-branch` copies this plan and the spec
  onto the `feat/issue-561-*` branch. Do not copy them by hand. The workflow's archive step
  moves both to `docs/archive/` (as it did for #560), so shipped files cite that path.
- **Pin recomputation rule.** Any edit to `capability_suite/fixtures/**` or to any top-level
  `capability_suite/*.py`, including the fixes a reviewer asks for later, changes the digest.
  After such an edit, re-run Task 5 Step 3 and commit the new pin in the same commit.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `scripts/factory_core/capability_suite/__init__.py` | Create (Task 1) | Package docstring only |
| `scripts/factory_core/capability_suite/mock_server.py` | Create (Task 1) | In-process `aiohttp.web` Anthropic-Messages mock with 12 scripted faults, bound to `127.0.0.1:<ephemeral>` |
| `scripts/factory_core/capability_suite/emit.py` | Create (Task 2) | `certification_result` builder and writer, plus `suite_digest()` |
| `scripts/factory_core/capability_suite/runner.py` | Create (Task 3), extend (Task 4) | Fixture loader, HTTP driver, checks, `run_cases`, `dimension_results` (Task 3); exit codes and CLI `main` (Task 4) |
| `scripts/factory_core/capability_suite/fixtures/<dimension>/<case>/case.yaml` | Create (Task 3) | 26 data-only cases: 10 probes, 14 controls, 2 observations |
| `scripts/factory_core/capability_suite/__main__.py` | Create (Task 4) | `python3 -m factory_core.capability_suite` → `runner.main()` |
| `scripts/factory_core/providers/model_endpoint.py` | Modify (Task 5) | The `PINNED_FIXTURE_SUITE_VERSION` value and the comment above it, nothing else |
| `tests/test_capability_suite.py` | Create (Task 1), extend (Tasks 2–5) | Tier 1 tests |
| `tests/test_provider_model_endpoint.py` | Modify (Task 5) | The two tests the spec names |

Not touched: `scheduler.sh`, `entrypoint.sh`, `Dockerfile`, `.github/workflows/**` (including
`ci.yml`), `deploy/**`, `config/`, any `gate_*`, the breaker, `model_proxy.py`,
`model_adapter.py`, `role_isolation.py`, and `_NO_LOCAL_DISPATCH`.

### Fixture tree (Task 3)

| Dimension | Probes (feed the result) | Controls (`expect:expect_reason`) | Observations |
|---|---|---|---|
| `protocol_compatibility` | `non_streaming_round_trip` (endpoint) | `malformed_json_body` → `fail:malformed_json`; `response_model_mismatch` → `fail:model_mismatch`; `count_tokens_404` → `unsupported:not_found` | `count_tokens_observed` |
| `tool_call_fidelity` | `tool_use_round_trip` (endpoint) | `forced_tool_choice_ignored` → `unsupported:ignored`; `unparseable_input_json_delta` → `fail:tool_input_unparseable`; `unoffered_tool_named` → `fail:tool_not_offered` | `forced_tool_choice_observed` |
| `streaming` | `sse_message_stop` (endpoint) | `stream_cut_no_message_stop` → `fail:stream_incomplete`; `message_start_model_mismatch` → `fail:model_mismatch` | — |
| `context_length` | `sized_request` (endpoint) | `max_tokens_truncated` → `fail:truncated`; `silent_input_truncation` → `fail:input_truncated`; `over_limit_error` → `fail:error_response` | — |
| `timeout_cancellation` | — (all harness, part B) | `connection_refused` → `fail:endpoint_unreachable`; `stall_before_first_byte` → `fail:timeout_time_to_first_token`; `idle_stall_mid_stream` → `fail:timeout_idle` | — |
| `role_isolation` | 6 `driver: function` probes: checker refused, monitor refused, absent role binds actor, local producer not independent, self-asserted checker content fails, other issue `unit_of_work_mismatch` | — | — |

### Tests and where they run

| Tier | What | Where | Gating |
|---|---|---|---|
| 1 (this plan) | Mock behaviour; every `http` case, control and observation against the in-process mock; the `role_isolation` probes; `--base-url mock` through `runner.main()` and through `python3 -m`; endpoint mode against a second loopback mock; exit codes 0, 2, 3, 4, 5; `--out` refusal; result rejected as evidence; no raw URL or credential value in the result or the output; dimension-directory check; `HARNESS_PENDING` subset check; digest equals pin | `pytest tests/` with `PYTHONPATH=scripts` (existing `tests` CI job) | Blocking (existing job, no `ci.yml` edit) |
| 2 (part B, #624) | `claude -p` against the mock for every `harness` case | #624's CI step | Not wired here |

---

## Task 1: In-process mock server

**Files:**
- Create: `scripts/factory_core/capability_suite/__init__.py`
- Create: `scripts/factory_core/capability_suite/mock_server.py`
- Create: `tests/test_capability_suite.py`

- [ ] **Step 1: Write the failing mock tests.** Create `tests/test_capability_suite.py` with exactly this content. The header
  imports every module later tasks use, so later tasks only change the single
  `from factory_core.capability_suite import ...` line.

```python
"""Tier 1 tests for the pinned capability suite (#561): every part A case against the in-process
mock, the role_isolation probes, the digest pin and the certification_result contract.

Hermetic: every connection is to 127.0.0.1, no secret is read (the endpoint-mode credential is
a sentinel set by monkeypatch), nothing touches GitHub.
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from factory_core.capability_suite import mock_server  # noqa: E402
from factory_core.providers import model_endpoint as me  # noqa: E402


@pytest.fixture(autouse=True)
def _no_claude(monkeypatch):
    # harness_version is informational in part A; never spawn `claude --version` in a test.
    monkeypatch.setattr(me, "observed_harness_version", lambda: None)


# --- mock server ----------------------------------------------------------------------------

ASK = {"model": mock_server.SERVED_NAME, "max_tokens": 8,
       "messages": [{"role": "user", "content": "say ok"}]}
WEATHER = {"name": "get_weather", "input_schema": {"type": "object", "required": ["city"]}}
TIME = {"name": "get_time", "input_schema": {"type": "object", "required": ["city"]}}


def _mock(path, body, fault=None):
    """(status, body bytes) of one request to a freshly started mock."""
    async def call():
        mock_runner, url = await mock_server.start()
        try:
            headers = {mock_server.FAULT_HEADER: fault} if fault else {}
            async with aiohttp.ClientSession() as session:
                async with session.post(url + path, json=body, headers=headers) as resp:
                    return resp.status, await resp.read()
        finally:
            await mock_runner.cleanup()
    return asyncio.run(call())


def test_mock_listens_on_loopback_only():
    async def url():
        mock_runner, base = await mock_server.start()
        await mock_runner.cleanup()
        return base
    assert asyncio.run(url()).startswith("http://127.0.0.1:")


def test_mock_answers_as_the_served_name():
    status, data = _mock("/v1/messages", ASK)
    body = json.loads(data)
    assert status == 200 and body["model"] == mock_server.SERVED_NAME
    assert body["content"] == [{"type": "text", "text": "ok"}]
    assert body["stop_reason"] == "end_turn" and body["usage"]["input_tokens"] == 2


def test_mock_stream_ends_in_a_complete_message_stop_frame():
    status, data = _mock("/v1/messages", dict(ASK, stream=True))
    assert status == 200 and data.endswith(b'data: {"type": "message_stop"}\n\n')
    assert b'"model": "capability-suite-mock"' in data


def test_mock_uses_the_first_offered_tool_or_the_forced_one():
    body = json.loads(_mock("/v1/messages", dict(ASK, tools=[WEATHER, TIME]))[1])
    assert body["content"][0]["name"] == "get_weather" and body["stop_reason"] == "tool_use"
    assert body["content"][0]["input"] == {"city": "Paris"}
    forced = dict(ASK, tools=[WEATHER, TIME], tool_choice={"type": "tool", "name": "get_time"})
    assert json.loads(_mock("/v1/messages", forced)[1])["content"][0]["name"] == "get_time"


def test_mock_answers_a_tool_result_turn_with_text():
    messages = ASK["messages"] + [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "get_weather",
                                           "input": {"city": "Paris"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                      "content": "sunny"}]},
    ]
    body = json.loads(_mock("/v1/messages", dict(ASK, tools=[WEATHER], messages=messages))[1])
    assert body["content"] == [{"type": "text", "text": "ok"}]


def test_mock_refuses_input_over_its_declared_window():
    prompt = "word " * (mock_server.CONTEXT_TOKENS + 1)
    status, data = _mock("/v1/messages", dict(ASK, messages=[{"role": "user", "content": prompt}]))
    assert status == 400 and json.loads(data)["type"] == "error"


def test_mock_counts_tokens():
    status, data = _mock("/v1/messages/count_tokens", ASK)
    assert status == 200 and json.loads(data) == {"input_tokens": 2}


@pytest.mark.parametrize("fault, path, body, check", [
    ("malformed_json", "/v1/messages", ASK, lambda s, d: s == 200 and d.endswith(b"[")),
    ("wrong_model", "/v1/messages", ASK,
     lambda s, d: json.loads(d)["model"] == mock_server.FALLBACK_MODEL),
    ("count_tokens_404", "/v1/messages/count_tokens", ASK, lambda s, d: s == 404),
    ("ignore_tool_choice", "/v1/messages",
     dict(ASK, tools=[WEATHER, TIME], tool_choice={"type": "tool", "name": "get_time"}),
     lambda s, d: json.loads(d)["content"] == [{"type": "text", "text": "ok"}]),
    ("bad_input_json", "/v1/messages", dict(ASK, tools=[WEATHER], stream=True),
     lambda s, d: b'"partial_json": "{\\"city\\": \\"Par"' in d),
    ("unoffered_tool", "/v1/messages", dict(ASK, tools=[WEATHER]),
     lambda s, d: json.loads(d)["content"][0]["name"] == "not_offered_tool"),
    ("cut_stream", "/v1/messages", dict(ASK, stream=True),
     lambda s, d: b"message_stop" not in d and not d.endswith(b"\n\n")),
    ("max_tokens_stop", "/v1/messages", ASK,
     lambda s, d: json.loads(d)["stop_reason"] == "max_tokens"),
    ("truncate_input", "/v1/messages", ASK,
     lambda s, d: json.loads(d)["usage"]["input_tokens"] == mock_server.TRUNCATED_INPUT_TOKENS),
    ("over_limit", "/v1/messages", ASK, lambda s, d: s == 400),
])
def test_mock_scripted_faults(fault, path, body, check):
    assert fault in mock_server.FAULTS
    assert check(*_mock(path, body, fault))
```

- [ ] **Step 2: Run the tests and confirm they fail.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_capability_suite.py -q
```

  Expected: `1 error` during collection, with
  `ModuleNotFoundError: No module named 'factory_core.capability_suite'`.

- [ ] **Step 3: Create the package marker** `scripts/factory_core/capability_suite/__init__.py`:

```python
"""Pinned capability and role-isolation certification suite (#561).

python3 -m factory_core.capability_suite run --base-url <mock|http(s)://...> --out <path>.
See runner.py; the fixtures under fixtures/ are data only.
"""
```

- [ ] **Step 4: Create** `scripts/factory_core/capability_suite/mock_server.py`:

```python
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
```

- [ ] **Step 5: Run the tests and confirm they pass.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_capability_suite.py -q
```

  Expected: `17 passed`.

- [ ] **Step 6: Commit.**

```bash
git add scripts/factory_core/capability_suite/__init__.py scripts/factory_core/capability_suite/mock_server.py tests/test_capability_suite.py
git commit -m "test(model): in-process Anthropic-Messages mock for the capability suite (#561)"
```

## Task 2: `certification_result` emitter and suite digest

**Files:**
- Create: `scripts/factory_core/capability_suite/emit.py`
- Modify: `tests/test_capability_suite.py`

- [ ] **Step 1: Import `emit` in the test file.** In `tests/test_capability_suite.py`, replace the line

```python
from factory_core.capability_suite import mock_server  # noqa: E402
```

  with

```python
from factory_core.capability_suite import emit, mock_server  # noqa: E402
```

- [ ] **Step 2: Append the emitter tests** to the end of `tests/test_capability_suite.py`:

```python
# --- certification_result -------------------------------------------------------------------

def _result(**kw):
    args = dict(target_kind="endpoint", base_url="http://192.0.2.10:8080",
                results={d: "pass" for d in me.ACTOR_REQUIRED_DIMENSIONS}, harness_pending=(),
                controls=[], now=datetime(2026, 11, 26, tzinfo=timezone.utc))
    args.update(kw)
    return emit.build_result(**args)


def test_result_records_the_scope_never_the_url():
    body = _result()["certification_result"]
    assert body["base_url_scope"] == "lan" and "192.0.2.10" not in json.dumps(body)
    assert body["fixture_suite_version"] == me.PINNED_FIXTURE_SUITE_VERSION
    assert body["generated_at"] == "2026-11-26T00:00:00+00:00"


def test_result_carries_no_evidence_record_field():
    result = _result()
    assert set(result) == {"certification_result"}
    assert not {"endpoint_id", "certified_by", "certified_at", "expires_at",
                "invalidated_by", "qualified_roles"} & set(result["certification_result"])


@pytest.mark.parametrize("kw", [
    {"target_kind": "rig"},
    {"results": {"streaming": "pass"}},
    {"results": {d: "unknown" for d in me.ACTOR_REQUIRED_DIMENSIONS}},
    {"results": {d: "stale" for d in me.ACTOR_REQUIRED_DIMENSIONS}},
])
def test_result_rejects_values_outside_the_vocabulary(kw):
    with pytest.raises(ValueError):
        _result(**kw)


def test_written_result_is_rejected_as_evidence(tmp_path):
    evidence_dir = tmp_path / "capability-evidence"
    emit.write_result(evidence_dir / "local-a.yaml", _result())
    with pytest.raises(me.RegistryError, match="exactly one capability_evidence mapping"):
        me.load_evidence(evidence_dir, "local-a")
```

- [ ] **Step 3: Run the tests and confirm they fail.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_capability_suite.py -q
```

  Expected: `1 error` during collection, with
  `ImportError: cannot import name 'emit' from 'factory_core.capability_suite'`.

- [ ] **Step 4: Create** `scripts/factory_core/capability_suite/emit.py`. `suite_digest()` is the spec's digest algorithm, copied
  without change. Task 5 pins its value.

```python
"""certification_result emitter and the suite-version digest (#561).

A certification_result is what one suite run found. It is never the per-endpoint
capability-evidence record (contract §4.5): its top-level key differs, so
model_endpoint.load_evidence() rejects it, and it carries none of the record's own fields
(endpoint_id, certified_*, expires_at, invalidated_by) nor the raw base URL.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from factory_core.providers import model_endpoint

RESULT_KEY = "certification_result"
SCHEMA_VERSION = "1.0"
TARGET_KINDS = ("mock", "endpoint")
PACKAGE_DIR = Path(__file__).resolve().parent


def suite_digest(package_dir=PACKAGE_DIR) -> str:
    """SHA-256 over fixtures/ and every top-level module: PINNED_FIXTURE_SUITE_VERSION.

    The glob is not recursive, so __pycache__/ is never read; fixtures/ holds no Python.
    """
    package_dir = Path(package_dir)
    digest = hashlib.sha256()
    digest.update(model_endpoint.tree_sha256(package_dir / "fixtures").encode())
    for path in sorted(package_dir.glob("*.py")):  # every module: a new one is never unhashed
        digest.update(path.name.encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode() + b"\n")
    return digest.hexdigest()


def build_result(*, target_kind, base_url, results, harness_pending, controls, now=None) -> dict:
    """The certification_result mapping. Only base_url's scope is recorded, never the URL."""
    if target_kind not in TARGET_KINDS:
        raise ValueError(f"target_kind must be one of {', '.join(TARGET_KINDS)}")
    if (tuple(results) != model_endpoint.ACTOR_REQUIRED_DIMENSIONS
            or any(v not in model_endpoint.RESULT_VALUES for v in results.values())):
        raise ValueError("results must map each ACTOR_REQUIRED_DIMENSIONS key to a RESULT_VALUES "
                         "value")
    return {RESULT_KEY: {
        "schema_version": SCHEMA_VERSION,
        "fixture_suite_version": model_endpoint.PINNED_FIXTURE_SUITE_VERSION,
        "harness_version": model_endpoint.observed_harness_version(),
        "target_kind": target_kind,
        "base_url_scope": model_endpoint.base_url_scope(base_url),
        "results": dict(results),
        "harness_pending": list(harness_pending),
        "controls": [dict(c) for c in controls],
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
    }}


def write_result(path, result) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
```

- [ ] **Step 5: Run the tests and confirm they pass.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_capability_suite.py -q
```

  Expected: `24 passed`.

- [ ] **Step 6: Commit.**

```bash
git add scripts/factory_core/capability_suite/emit.py tests/test_capability_suite.py
git commit -m "test(model): certification_result emitter and suite digest (#561)"
```

## Task 3: Fixture tree, loader, HTTP driver and `run_cases`

**Files:**
- Create: `scripts/factory_core/capability_suite/runner.py` (everything up to and including `dimension_results`; Task 4 appends the CLI)
- Create: 26 × `scripts/factory_core/capability_suite/fixtures/<dimension>/<case>/case.yaml`
- Modify: `tests/test_capability_suite.py`

- [ ] **Step 1: Import `runner` in the test file.** In `tests/test_capability_suite.py`, replace the line

```python
from factory_core.capability_suite import emit, mock_server  # noqa: E402
```

  with

```python
from factory_core.capability_suite import emit, mock_server, runner  # noqa: E402
```

- [ ] **Step 2: Append the fixture-tree and `run_cases` tests** to the end of `tests/test_capability_suite.py`:

```python
# --- fixture tree and run_cases -------------------------------------------------------------

def _summary(cases=None, context_tokens=mock_server.CONTEXT_TOKENS):
    """runner.run_cases over the shipped tree (or `cases`), the mock standing in for the
    endpoint."""
    async def go():
        mock_runner, url = await mock_server.start()
        try:
            mock = runner.Target(url, mock_server.SERVED_NAME, mock_server.DUMMY_CREDENTIAL)
            return await runner.run_cases(runner.load_cases() if cases is None else cases,
                                          endpoint=mock, mock=mock,
                                          context_tokens=context_tokens)
        finally:
            await mock_runner.cleanup()
    return asyncio.run(go())


def _cases_by_name():
    return {f"{c['dimension']}/{c['case']}": c for c in runner.load_cases()}


def _copy_fixtures(tmp_path):
    dest = tmp_path / "fixtures"
    shutil.copytree(runner.FIXTURES_DIR, dest)
    return dest


def test_dimension_directories_are_exactly_the_actor_required_dimensions():
    dirs = {p.name for p in runner.FIXTURES_DIR.iterdir() if p.is_dir()}
    assert dirs == set(me.ACTOR_REQUIRED_DIMENSIONS)


def test_fixtures_hold_only_case_yaml_files():
    files = [p for p in runner.FIXTURES_DIR.rglob("*") if p.is_file()]
    assert files and all(p.name == "case.yaml" for p in files)
    assert len(files) == len(runner.load_cases()) == 26


def test_harness_pending_is_a_subset_of_actor_required_dimensions():
    assert set(runner.HARNESS_PENDING) <= set(me.ACTOR_REQUIRED_DIMENSIONS)


def test_worst_first_orders_exactly_the_result_values():
    assert sorted(runner.WORST_FIRST) == sorted(me.RESULT_VALUES)


def test_part_a_ships_no_harness_case():
    assert {c["driver"] for c in runner.load_cases()} == {"http", "function"}


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
        "timeout_cancellation/connection_refused",
        "timeout_cancellation/stall_before_first_byte",
        "timeout_cancellation/idle_stall_mid_stream",
    }
    assert observations == {"protocol_compatibility/count_tokens_observed",
                            "tool_call_fidelity/forced_tool_choice_observed"}
    probes = {n for n, c in cases.items() if c["kind"] == "probe"}
    assert {n for n in probes if not n.startswith("role_isolation/")} == {
        "protocol_compatibility/non_streaming_round_trip", "tool_call_fidelity/tool_use_round_trip",
        "streaming/sse_message_stop", "context_length/sized_request"}


def test_role_isolation_cases_are_function_probes():
    cases = [c for c in runner.load_cases() if c["dimension"] == "role_isolation"]
    assert len(cases) == 6
    assert all(c["driver"] == "function" and c["kind"] == "probe" for c in cases)


def test_committed_fixtures_name_no_rig_address():
    for path in runner.FIXTURES_DIR.rglob("case.yaml"):
        text = path.read_text()
        assert "http://" not in text and "https://" not in text, path


@pytest.mark.parametrize("edit, message", [
    (lambda c: c.update(dimension="streaming"), "must match its directories"),
    (lambda c: c.update(surprise=1), "unknown key"),
    (lambda c: c.update(check="nope"), "check must be one of"),
    (lambda c: c.update(target="endpoint"), "always targets the mock"),
    (lambda c: c.pop("expect_reason"), "needs expect"),
    (lambda c: c.update(fault="nope"), "fault is for controls"),
])
def test_malformed_case_is_rejected(tmp_path, edit, message):
    fixtures = _copy_fixtures(tmp_path)
    path = fixtures / "protocol_compatibility" / "malformed_json_body" / "case.yaml"
    case = yaml.safe_load(path.read_text())
    edit(case)
    path.write_text(yaml.safe_dump(case))
    with pytest.raises(runner.CaseError, match=message):
        runner.load_cases(fixtures)


def test_a_missing_dimension_directory_is_rejected(tmp_path):
    fixtures = _copy_fixtures(tmp_path)
    shutil.rmtree(fixtures / "streaming")
    with pytest.raises(runner.CaseError, match="dimension directories"):
        runner.load_cases(fixtures)


def test_every_control_matches_its_expect():
    controls = [c for c in _summary().controls if c["kind"] == "control"]
    assert len(controls) == 14
    assert all(c["ok"] and c["observed"] == c["expected"] for c in controls), controls


def test_every_probe_passes_against_the_mock():
    summary = _summary()
    assert summary.not_run == []
    assert len(summary.probes) == 10 and all(v == "pass" for _, v in summary.probes)
    assert runner.dimension_results(summary.probe_values) == {
        "protocol_compatibility": "not_tested", "tool_call_fidelity": "not_tested",
        "streaming": "pass", "context_length": "not_tested", "role_isolation": "pass",
        "timeout_cancellation": "not_tested",
    }


def test_observations_are_recorded_with_no_expect():
    observations = [c for c in _summary().controls if c["kind"] == "observation"]
    assert {o["case"] for o in observations} == {"count_tokens_observed",
                                                 "forced_tool_choice_observed"}
    assert all(o["expected"] is None and o["ok"] for o in observations)


@pytest.mark.parametrize("check, case", [("count_tokens", "count_tokens_observed"),
                                         ("forced_tool_choice", "forced_tool_choice_observed")])
def test_an_unsupported_observation_changes_no_dimension(monkeypatch, check, case):
    baseline = runner.dimension_results(_summary().probe_values)

    async def unsupported(*_):
        return "unsupported", "not_found"

    monkeypatch.setitem(runner.HTTP_CHECKS, check, unsupported)
    summary = _summary()
    assert runner.dimension_results(summary.probe_values) == baseline
    assert [c["observed"] for c in summary.controls if c["case"] == case] == [
        "unsupported:not_found"]


def test_a_harness_case_is_reported_not_run():
    case = {"case": "via_cli", "dimension": "streaming", "driver": "harness", "kind": "probe",
            "target": "mock"}
    summary = _summary([case])
    assert summary.not_run == ["streaming/via_cli"]
    assert runner.dimension_results(summary.probe_values)["streaming"] == "not_tested"


def test_the_context_probe_does_not_run_without_a_window():
    cases = [c for c in runner.load_cases() if c["case"] == "sized_request"]
    assert _summary(cases, context_tokens=None).not_run == ["context_length/sized_request"]


def test_a_pending_dimension_reports_a_shipped_probe_failure():
    values = {"context_length": ["fail"], "streaming": ["pass"], "role_isolation": ["pass"]}
    results = runner.dimension_results(values)
    assert results["context_length"] == "fail" and results["streaming"] == "pass"
    assert results["timeout_cancellation"] == "not_tested"
```

- [ ] **Step 3: Run the tests and confirm they fail.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_capability_suite.py -q
```

  Expected: `1 error` during collection, with
  `ImportError: cannot import name 'runner' from 'factory_core.capability_suite'`.

- [ ] **Step 4: Create** `scripts/factory_core/capability_suite/runner.py` with exactly this content. The file ends at
  `dimension_results`. `argparse`, `os`, `re` and `sys` are imported now and used by the CLI
  that Task 4 appends.

```python
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

from factory_core.capability_suite import emit, mock_server
from factory_core.providers import model_adapter, model_endpoint, role_isolation

FIXTURES_DIR = emit.PACKAGE_DIR / "fixtures"
# Dimensions whose required harness (`claude -p`) probes are not shipped yet; part B (#624)
# removes a dimension in the change that adds its harness cases.
HARNESS_PENDING = ("protocol_compatibility", "tool_call_fidelity", "context_length",
                   "timeout_cancellation")
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
    lines: list = field(default_factory=list)


# --- fixture tree ---------------------------------------------------------------------------

def _validate(case, where):
    unknown = set(case) - _CASE_KEYS
    if unknown:
        raise CaseError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
    for key, allowed in (("driver", DRIVERS), ("kind", KINDS), ("target", TARGETS)):
        if case.get(key) not in allowed:
            raise CaseError(f"{where}: {key} must be one of {', '.join(allowed)}")
    if case["driver"] == "harness":
        return  # never executed in part A: reported as not run (exit code 4)
    checks = FUNCTION_CHECKS if case["driver"] == "function" else HTTP_CHECKS
    if case.get("check") not in checks:
        raise CaseError(f"{where}: check must be one of {', '.join(sorted(checks))}")
    if case["driver"] == "function" and (case["kind"] != "probe" or case["target"] != "mock"
                                         or not isinstance(case.get("args"), dict)
                                         or not isinstance(case.get("want"), list)):
        raise CaseError(f"{where}: a function case is a mock-target probe with args and want")
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


def _function_probe(case):
    got = list(FUNCTION_CHECKS[case["check"]](**case["args"]))
    return ("pass", "ok") if got == case["want"] else ("fail", f"unexpected_{got[1]}")


# --- run ------------------------------------------------------------------------------------

async def run_cases(cases, *, endpoint, mock, context_tokens) -> Summary:
    """Run every case in order. A control always runs against the mock and its declared
    window; a probe or observation targeting the endpoint uses `endpoint`."""
    summary = Summary()
    async with aiohttp.ClientSession() as session:
        for case in cases:
            name = f"{case['dimension']}/{case['case']}"
            if case["driver"] == "harness":
                outcome = None
            elif case["driver"] == "function":
                outcome = _function_probe(case)
            elif case["kind"] == "control" or case["target"] == "mock":
                outcome = await HTTP_CHECKS[case["check"]](session, mock, case,
                                                           mock_server.CONTEXT_TOKENS)
            else:
                outcome = await HTTP_CHECKS[case["check"]](session, endpoint, case,
                                                           context_tokens)
            if outcome is None:
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
```

- [ ] **Step 5: Create the `protocol_compatibility` cases.** One file per case, at the path shown:

`scripts/factory_core/capability_suite/fixtures/protocol_compatibility/count_tokens_404/case.yaml`:

```yaml
case: count_tokens_404
dimension: protocol_compatibility
description: A 404 from POST /v1/messages/count_tokens is classified as unsupported.
driver: http
kind: control
target: mock
check: count_tokens
fault: count_tokens_404
expect: unsupported
expect_reason: not_found
```

`scripts/factory_core/capability_suite/fixtures/protocol_compatibility/count_tokens_observed/case.yaml`:

```yaml
case: count_tokens_observed
dimension: protocol_compatibility
description: Records what POST /v1/messages/count_tokens returns. Observation only, feeds no dimension.
driver: http
kind: observation
target: endpoint
check: count_tokens
```

`scripts/factory_core/capability_suite/fixtures/protocol_compatibility/malformed_json_body/case.yaml`:

```yaml
case: malformed_json_body
dimension: protocol_compatibility
description: A 2xx non-streaming body that is not a JSON object is classified as fail.
driver: http
kind: control
target: mock
check: round_trip
fault: malformed_json
expect: fail
expect_reason: malformed_json
```

`scripts/factory_core/capability_suite/fixtures/protocol_compatibility/non_streaming_round_trip/case.yaml`:

```yaml
case: non_streaming_round_trip
dimension: protocol_compatibility
description: A non-streaming message is answered as a JSON message whose model is the served name.
driver: http
kind: probe
target: endpoint
check: round_trip
```

`scripts/factory_core/capability_suite/fixtures/protocol_compatibility/response_model_mismatch/case.yaml`:

```yaml
case: response_model_mismatch
dimension: protocol_compatibility
description: A response naming a model other than the served name is classified as fail.
driver: http
kind: control
target: mock
check: round_trip
fault: wrong_model
expect: fail
expect_reason: model_mismatch
```

- [ ] **Step 6: Create the `tool_call_fidelity` cases.** One file per case, at the path shown:

`scripts/factory_core/capability_suite/fixtures/tool_call_fidelity/forced_tool_choice_ignored/case.yaml`:

```yaml
case: forced_tool_choice_ignored
dimension: tool_call_fidelity
description: A forced tool_choice answered without the forced tool is classified as unsupported.
driver: http
kind: control
target: mock
check: forced_tool_choice
fault: ignore_tool_choice
expect: unsupported
expect_reason: ignored
```

`scripts/factory_core/capability_suite/fixtures/tool_call_fidelity/forced_tool_choice_observed/case.yaml`:

```yaml
case: forced_tool_choice_observed
dimension: tool_call_fidelity
description: Records whether a forced tool_choice is honoured. Observation only, feeds no dimension.
driver: http
kind: observation
target: endpoint
check: forced_tool_choice
```

`scripts/factory_core/capability_suite/fixtures/tool_call_fidelity/tool_use_round_trip/case.yaml`:

```yaml
case: tool_use_round_trip
dimension: tool_call_fidelity
description: A streamed tool_use names an offered tool, its input validates against the input_schema, and the tool_result turn is accepted.
driver: http
kind: probe
target: endpoint
check: tool_round_trip
```

`scripts/factory_core/capability_suite/fixtures/tool_call_fidelity/unoffered_tool_named/case.yaml`:

```yaml
case: unoffered_tool_named
dimension: tool_call_fidelity
description: A tool_use naming a tool that was not offered is classified as fail.
driver: http
kind: control
target: mock
check: tool_round_trip
fault: unoffered_tool
expect: fail
expect_reason: tool_not_offered
```

`scripts/factory_core/capability_suite/fixtures/tool_call_fidelity/unparseable_input_json_delta/case.yaml`:

```yaml
case: unparseable_input_json_delta
dimension: tool_call_fidelity
description: A tool_use whose input_json_delta fragments do not parse is classified as fail.
driver: http
kind: control
target: mock
check: tool_round_trip
fault: bad_input_json
expect: fail
expect_reason: tool_input_unparseable
```

- [ ] **Step 7: Create the `streaming` cases.** One file per case, at the path shown:

`scripts/factory_core/capability_suite/fixtures/streaming/message_start_model_mismatch/case.yaml`:

```yaml
case: message_start_model_mismatch
dimension: streaming
description: A message_start naming a model other than the served name is classified as fail.
driver: http
kind: control
target: mock
check: sse
fault: wrong_model
expect: fail
expect_reason: model_mismatch
```

`scripts/factory_core/capability_suite/fixtures/streaming/sse_message_stop/case.yaml`:

```yaml
case: sse_message_stop
dimension: streaming
description: An SSE response ends in a complete message_stop frame, answered as the served name.
driver: http
kind: probe
target: endpoint
check: sse
```

`scripts/factory_core/capability_suite/fixtures/streaming/stream_cut_no_message_stop/case.yaml`:

```yaml
case: stream_cut_no_message_stop
dimension: streaming
description: A stream cut mid-content with no message_stop is classified as fail.
driver: http
kind: control
target: mock
check: sse
fault: cut_stream
expect: fail
expect_reason: stream_incomplete
```

- [ ] **Step 8: Create the `context_length` cases.** One file per case, at the path shown:

`scripts/factory_core/capability_suite/fixtures/context_length/max_tokens_truncated/case.yaml`:

```yaml
case: max_tokens_truncated
dimension: context_length
description: A response with stop_reason max_tokens is classified as truncated, never complete.
driver: http
kind: control
target: mock
check: context_window
fault: max_tokens_stop
expect: fail
expect_reason: truncated
```

`scripts/factory_core/capability_suite/fixtures/context_length/over_limit_error/case.yaml`:

```yaml
case: over_limit_error
dimension: context_length
description: An over-limit 400 with an error body is a clean classified error, never a pass.
driver: http
kind: control
target: mock
check: context_window
fault: over_limit
expect: fail
expect_reason: error_response
```

`scripts/factory_core/capability_suite/fixtures/context_length/silent_input_truncation/case.yaml`:

```yaml
case: silent_input_truncation
dimension: context_length
description: A usage.input_tokens far below what was sent (silent input truncation) is classified as fail.
driver: http
kind: control
target: mock
check: context_window
fault: truncate_input
expect: fail
expect_reason: input_truncated
```

`scripts/factory_core/capability_suite/fixtures/context_length/sized_request/case.yaml`:

```yaml
case: sized_request
dimension: context_length
description: A request sized to --context-tokens is answered completely, and usage.input_tokens shows the input arrived (never count_tokens).
driver: http
kind: probe
target: endpoint
check: context_window
```

- [ ] **Step 9: Create the `timeout_cancellation` cases.** One file per case, at the path shown:

`scripts/factory_core/capability_suite/fixtures/timeout_cancellation/connection_refused/case.yaml`:

```yaml
case: connection_refused
dimension: timeout_cancellation
description: A refused connection (local outage) is classified endpoint_unreachable after exactly one attempt.
driver: http
kind: control
target: mock
check: unreachable
expect: fail
expect_reason: endpoint_unreachable
```

`scripts/factory_core/capability_suite/fixtures/timeout_cancellation/idle_stall_mid_stream/case.yaml`:

```yaml
case: idle_stall_mid_stream
dimension: timeout_cancellation
description: A stream that goes idle after message_start is classified timeout_idle.
driver: http
kind: control
target: mock
check: sse
fault: stall_mid_stream
timeouts: {idle_seconds: 0.3}
expect: fail
expect_reason: timeout_idle
```

`scripts/factory_core/capability_suite/fixtures/timeout_cancellation/stall_before_first_byte/case.yaml`:

```yaml
case: stall_before_first_byte
dimension: timeout_cancellation
description: An endpoint that stalls before the first byte is classified timeout_time_to_first_token.
driver: http
kind: control
target: mock
check: sse
fault: stall_first_byte
timeouts: {time_to_first_token_seconds: 0.3}
expect: fail
expect_reason: timeout_time_to_first_token
```

- [ ] **Step 10: Create the `role_isolation` cases.** One file per case, at the path shown:

`scripts/factory_core/capability_suite/fixtures/role_isolation/local_absent_role_binds_actor/case.yaml`:

```yaml
case: local_absent_role_binds_actor
dimension: role_isolation
description: A local dispatch with no declared role binds actor.
driver: function
kind: probe
target: mock
check: bind_dispatch_role
args: {hosting_location: local}
want: [actor, default_actor]
```

`scripts/factory_core/capability_suite/fixtures/role_isolation/local_declared_checker_refused/case.yaml`:

```yaml
case: local_declared_checker_refused
dimension: role_isolation
description: A local dispatch that declares checker is refused, never bound.
driver: function
kind: probe
target: mock
check: bind_dispatch_role
args: {hosting_location: local, declared_role: checker}
want: [null, nested_checker_refused]
```

`scripts/factory_core/capability_suite/fixtures/role_isolation/local_declared_monitor_refused/case.yaml`:

```yaml
case: local_declared_monitor_refused
dimension: role_isolation
description: A local dispatch that declares monitor is refused, never bound.
driver: function
kind: probe
target: mock
check: bind_dispatch_role
args: {hosting_location: local, declared_role: monitor}
want: [null, nested_checker_refused]
```

`scripts/factory_core/capability_suite/fixtures/role_isolation/local_producer_not_independent/case.yaml`:

```yaml
case: local_producer_not_independent
dimension: role_isolation
description: A verdict produced by a local dispatch never satisfies independence.
driver: function
kind: probe
target: mock
check: check_verdict_independence
args:
  actor_dispatch: {schema_version: "1.0", outcome: ok, endpoint_id: local-a, hosting_location: local, role: actor, correlation: inferred_pointer}
  producer_dispatch: {schema_version: "1.0", outcome: ok, endpoint_id: local-b, hosting_location: local, role: checker, correlation: inferred_pointer}
  actor_issue: 1
  producer_issue: 1
  verdict_issue: 1
want: [false, producer_not_cloud]
```

`scripts/factory_core/capability_suite/fixtures/role_isolation/other_issue_mismatch/case.yaml`:

```yaml
case: other_issue_mismatch
dimension: role_isolation
description: A producer record for another issue is unit_of_work_mismatch.
driver: function
kind: probe
target: mock
check: check_verdict_independence
args:
  actor_dispatch: {schema_version: "1.0", outcome: ok, endpoint_id: local-a, hosting_location: local, role: actor, correlation: inferred_pointer}
  producer_dispatch: {schema_version: "1.0", outcome: ok, endpoint_id: cloud-a, hosting_location: cloud, role: checker, correlation: inferred_pointer}
  actor_issue: 1
  producer_issue: 2
  verdict_issue: 1
want: [false, unit_of_work_mismatch]
```

`scripts/factory_core/capability_suite/fixtures/role_isolation/self_asserted_checker_content/case.yaml`:

```yaml
case: self_asserted_checker_content
dimension: role_isolation
description: A producer record whose content self-asserts "role checker" but whose role field is not checker fails independence.
driver: function
kind: probe
target: mock
check: check_verdict_independence
args:
  actor_dispatch: {schema_version: "1.0", outcome: ok, endpoint_id: local-a, hosting_location: local, role: actor, correlation: inferred_pointer}
  producer_dispatch: {schema_version: "1.0", outcome: ok, endpoint_id: cloud-a, hosting_location: cloud, role: actor, correlation: inferred_pointer, content: "role: checker"}
  actor_issue: 1
  producer_issue: 1
  verdict_issue: 1
want: [false, producer_role_not_checker]
```

- [ ] **Step 11: Confirm the tree has 26 cases and no Python file.**

```bash
find scripts/factory_core/capability_suite/fixtures -type f | wc -l
find scripts/factory_core/capability_suite/fixtures -name "*.py" | wc -l
```

  Expected: `26`, then `0`.

- [ ] **Step 12: Run the tests and confirm they pass.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_capability_suite.py -q
```

  Expected: `47 passed` (about 5 s; the two stall controls each wait 0.3 s).

- [ ] **Step 13: Commit.**

```bash
git add scripts/factory_core/capability_suite/runner.py scripts/factory_core/capability_suite/fixtures tests/test_capability_suite.py
git commit -m "test(model): capability fixture tree and HTTP runner for part A (#561)"
```

## Task 4: CLI, exit codes and module entry point

**Files:**
- Modify: `scripts/factory_core/capability_suite/runner.py` (append)
- Create: `scripts/factory_core/capability_suite/__main__.py`
- Modify: `tests/test_capability_suite.py`

- [ ] **Step 1: Append the CLI tests** to the end of `tests/test_capability_suite.py`. They need no import change: Task 1's
  header already imports `os`, `subprocess`, `threading` and `contextmanager`.

```python
# --- CLI: python3 -m factory_core.capability_suite run ---------------------------------------

SENTINEL = "sk-capability-suite-test-sentinel"


def _run(tmp_path, *argv, fixtures_dir=None):
    out = tmp_path / "out" / "result.json"
    code = runner.main(["run", *argv, "--out", str(out)],
                       fixtures_dir=fixtures_dir or runner.FIXTURES_DIR)
    result = json.loads(out.read_text()) if out.exists() else None
    return code, result, out


def _usage_error(*argv):
    with pytest.raises(SystemExit) as e:
        runner.main(["run", *argv])
    return e.value.code


@contextmanager
def _endpoint_mock():
    """A second mock on its own loop and thread, standing in for a real endpoint."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    mock_runner, url = asyncio.run_coroutine_threadsafe(mock_server.start(), loop).result(10)
    try:
        yield url
    finally:
        asyncio.run_coroutine_threadsafe(mock_runner.cleanup(), loop).result(10)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(10)


def test_mock_run_exits_0_with_streaming_and_role_isolation_pass(tmp_path):
    code, result, _ = _run(tmp_path, "--base-url", "mock")
    assert code == 0
    body = result["certification_result"]
    assert list(body["results"]) == list(me.ACTOR_REQUIRED_DIMENSIONS)
    assert body["results"] == {
        "protocol_compatibility": "not_tested", "tool_call_fidelity": "not_tested",
        "streaming": "pass", "context_length": "not_tested", "role_isolation": "pass",
        "timeout_cancellation": "not_tested",
    }
    assert body["harness_pending"] == list(runner.HARNESS_PENDING)
    assert body["target_kind"] == "mock" and body["base_url_scope"] == "same-host"
    assert len(body["controls"]) == 16 and all(c["ok"] for c in body["controls"])


def test_module_entry_point_runs(tmp_path):
    out = tmp_path / "result.json"
    # An empty PATH: the child can never spawn `claude --version` (harness_version is null).
    env = dict(os.environ, PYTHONPATH=str(Path(runner.__file__).resolve().parents[2]),
               PATH=str(tmp_path / "no-bin"))
    proc = subprocess.run([sys.executable, "-m", "factory_core.capability_suite", "run",
                           "--base-url", "mock", "--out", str(out)],
                          capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(out.read_text())
    assert set(result) == {"certification_result"}
    assert result["certification_result"]["harness_version"] is None


def test_a_changed_control_fault_exits_3(tmp_path):
    fixtures = _copy_fixtures(tmp_path)
    path = fixtures / "protocol_compatibility" / "malformed_json_body" / "case.yaml"
    path.write_text(path.read_text().replace("fault: malformed_json", "fault: wrong_model"))
    code, result, _ = _run(tmp_path, "--base-url", "mock", fixtures_dir=fixtures)
    assert code == 3
    bad = [c for c in result["certification_result"]["controls"] if not c["ok"]]
    assert bad == [{"dimension": "protocol_compatibility", "case": "malformed_json_body",
                    "kind": "control", "expected": "fail:malformed_json",
                    "observed": "fail:model_mismatch", "ok": False}]


def test_a_malformed_tree_exits_3_and_writes_nothing(tmp_path):
    fixtures = _copy_fixtures(tmp_path)
    shutil.rmtree(fixtures / "streaming")
    code, result, _ = _run(tmp_path, "--base-url", "mock", fixtures_dir=fixtures)
    assert code == 3 and result is None


def test_a_harness_case_in_the_tree_exits_4(tmp_path):
    fixtures = _copy_fixtures(tmp_path)
    case_dir = fixtures / "streaming" / "via_cli"
    case_dir.mkdir()
    (case_dir / "case.yaml").write_text(
        "case: via_cli\ndimension: streaming\ndriver: harness\nkind: probe\ntarget: mock\n")
    code, result, _ = _run(tmp_path, "--base-url", "mock", fixtures_dir=fixtures)
    assert code == 4 and result["certification_result"]["results"]["streaming"] == "not_tested"


def test_a_failing_probe_in_mock_mode_exits_5(tmp_path):
    # A window larger than the mock declares: the mock answers the sized request with a 400.
    code, result, _ = _run(tmp_path, "--base-url", "mock", "--context-tokens", "20000")
    assert code == 5
    assert result["certification_result"]["results"]["context_length"] == "fail"


def test_endpoint_mode_writes_no_url_or_credential(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPSUITE_TEST_KEY", SENTINEL)
    with _endpoint_mock() as url:
        code, result, out = _run(tmp_path, "--base-url", url, "--served-name",
                                 mock_server.SERVED_NAME, "--secret-env", "CAPSUITE_TEST_KEY",
                                 "--context-tokens", "4096")
    captured = capsys.readouterr()
    host_port = url.split("//", 1)[1]
    assert code == 0
    body = result["certification_result"]
    assert body["target_kind"] == "endpoint" and body["base_url_scope"] == "same-host"
    assert body["results"]["streaming"] == "pass"
    for blob in (out.read_text(), captured.out, captured.err):
        assert SENTINEL not in blob and host_port not in blob


def test_endpoint_mode_without_context_tokens_exits_4(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPSUITE_TEST_KEY", SENTINEL)
    with _endpoint_mock() as url:
        code, result, _ = _run(tmp_path, "--base-url", url, "--served-name",
                               mock_server.SERVED_NAME, "--secret-env", "CAPSUITE_TEST_KEY")
    assert code == 4
    assert result["certification_result"]["results"]["context_length"] == "not_tested"


def test_endpoint_mode_findings_are_not_errors(tmp_path, monkeypatch):
    # The endpoint answers as a different model: every endpoint probe fails, the exit stays 0.
    monkeypatch.setenv("CAPSUITE_TEST_KEY", SENTINEL)
    with _endpoint_mock() as url:
        code, result, _ = _run(tmp_path, "--base-url", url, "--served-name", "some-other-model",
                               "--secret-env", "CAPSUITE_TEST_KEY", "--context-tokens", "4096")
    assert code == 0 and result["certification_result"]["results"]["streaming"] == "fail"


@pytest.mark.parametrize("argv", [
    ["--base-url", "ftp://192.0.2.10"],
    ["--base-url", "http://192.0.2.10:8080", "--secret-env", "CAPSUITE_TEST_KEY"],
    ["--base-url", "http://192.0.2.10:8080", "--served-name", "m"],
    ["--base-url", "http://192.0.2.10:8080", "--served-name", "m", "--secret-env", "lower"],
    ["--base-url", "http://192.0.2.10:8080", "--served-name", "m",
     "--secret-env", "CAPSUITE_UNSET_KEY"],
    ["--base-url", "mock", "--context-tokens", "0"],
])
def test_usage_errors_exit_2_and_write_nothing(tmp_path, monkeypatch, argv):
    monkeypatch.delenv("CAPSUITE_UNSET_KEY", raising=False)
    out = tmp_path / "result.json"
    assert _usage_error(*argv, "--out", str(out)) == 2
    assert not out.exists()


def test_a_malformed_base_url_is_a_usage_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPSUITE_TEST_KEY", SENTINEL)
    for url in ("http://[bad", "http://:8080", "http://192.0.2.10:notaport"):
        assert _usage_error("--base-url", url, "--served-name", "m", "--secret-env",
                            "CAPSUITE_TEST_KEY", "--out", str(tmp_path / "r.json")) == 2
    assert "http(s):// URL with a host" in capsys.readouterr().err


def test_out_under_model_registry_is_refused(tmp_path):
    out = tmp_path / "model-registry" / "capability-evidence" / "local-a.yaml"
    assert _usage_error("--base-url", "mock", "--out", str(out)) == 2
    assert not out.parent.exists()


def test_an_unset_secret_env_names_the_variable_only(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CAPSUITE_TEST_KEY", "")
    _usage_error("--base-url", "http://192.0.2.10:8080", "--served-name", "m",
                 "--secret-env", "CAPSUITE_TEST_KEY", "--out", str(tmp_path / "r.json"))
    assert "CAPSUITE_TEST_KEY is not set" in capsys.readouterr().err


def test_emitted_result_is_rejected_as_evidence(tmp_path):
    code, _, out = _run(tmp_path, "--base-url", "mock")
    assert code == 0
    evidence_dir = tmp_path / "capability-evidence"
    evidence_dir.mkdir()
    shutil.copy(out, evidence_dir / "local-a.yaml")
    with pytest.raises(me.RegistryError, match="exactly one capability_evidence mapping"):
        me.load_evidence(evidence_dir, "local-a")


def test_part_a_result_qualifies_no_route(tmp_path):
    _, result, _ = _run(tmp_path, "--base-url", "mock")
    record = {"endpoint_id": "local-a", "qualified_roles": ["actor"],
              "results": result["certification_result"]["results"]}
    assert me.role_problems(record, "actor") == [
        f"capability evidence for 'local-a': {d} is not_tested, not pass"
        for d in runner.HARNESS_PENDING]
```

- [ ] **Step 2: Run the tests and confirm they fail.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_capability_suite.py -q
```

  Expected: `20 failed, 47 passed`. The failures are
  `AttributeError: module 'factory_core.capability_suite.runner' has no attribute 'main'`, and
  `test_module_entry_point_runs` fails with `No module named factory_core.capability_suite.__main__`.

- [ ] **Step 3: Append the CLI** to the end of `scripts/factory_core/capability_suite/runner.py`, after `dimension_results`, with
  two blank lines before `def exit_code`:

```python
def exit_code(summary, mock_mode) -> int:
    if any(not c["ok"] for c in summary.controls):
        return 3
    if summary.not_run:
        return 4
    if mock_mode and any(value != "pass" for _, value in summary.probes):
        return 5
    return 0


async def _run(cases, *, base_url, served_name, credential, context_tokens):
    mock_runner, mock_url = await mock_server.start()
    try:
        mock = Target(mock_url, mock_server.SERVED_NAME, mock_server.DUMMY_CREDENTIAL)
        endpoint = (mock if base_url == "mock"
                    else Target(base_url.rstrip("/"), served_name, credential))
        summary = await run_cases(cases, endpoint=endpoint, mock=mock,
                                  context_tokens=context_tokens)
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
    try:
        cases = load_cases(fixtures_dir)
    except CaseError as e:
        print(f"capability suite: fixture tree is invalid: {e}", file=sys.stderr)
        return 3
    summary, base_url = asyncio.run(_run(
        cases, base_url=args.base_url, served_name=args.served_name or mock_server.SERVED_NAME,
        credential=credential, context_tokens=context_tokens))
    result = emit.build_result(
        target_kind="mock" if mock_mode else "endpoint", base_url=base_url,
        results=dimension_results(summary.probe_values), harness_pending=HARNESS_PENDING,
        controls=summary.controls)
    emit.write_result(args.out, result)
    for line in summary.lines:
        print(line)
    code = exit_code(summary, mock_mode)
    print(f"capability suite: wrote {args.out} (exit {code})")
    return code
```

- [ ] **Step 4: Create** `scripts/factory_core/capability_suite/__main__.py`:

```python
from factory_core.capability_suite.runner import main

raise SystemExit(main())
```

- [ ] **Step 5: Run the tests and confirm they pass.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_capability_suite.py -q
```

  Expected: `67 passed`.

- [ ] **Step 6: Smoke-run the literal acceptance command.**

```bash
PYTHONPATH=scripts python3 -m factory_core.capability_suite run --base-url mock --out /tmp/capsuite/result.json; echo "exit=$?"
python3 -c "import json; print(json.load(open('/tmp/capsuite/result.json'))['certification_result']['results'])"
```

  Expected: 26 case lines, each ending `pass:ok` or matching its control. Then
  `capability suite: wrote /tmp/capsuite/result.json (exit 0)` and `exit=0`. Then
  `{'protocol_compatibility': 'not_tested', 'tool_call_fidelity': 'not_tested', 'streaming': 'pass', 'context_length': 'not_tested', 'role_isolation': 'pass', 'timeout_cancellation': 'not_tested'}`.
  `fixture_suite_version` is still `null` here. Task 5 sets it.

- [ ] **Step 7: Commit.**

```bash
git add scripts/factory_core/capability_suite/runner.py scripts/factory_core/capability_suite/__main__.py tests/test_capability_suite.py
git commit -m "test(model): capability suite CLI, exit codes and module entry point (#561)"
```

## Task 5: Pin `PINNED_FIXTURE_SUITE_VERSION` to the suite digest

**Files:**
- Modify: `scripts/factory_core/providers/model_endpoint.py` (the constant's value and the comment above it only)
- Modify: `tests/test_provider_model_endpoint.py` (the two tests the spec names)
- Modify: `tests/test_capability_suite.py`

- [ ] **Step 1: Append the digest tests** to the end of `tests/test_capability_suite.py`:

```python
# --- suite-version digest -------------------------------------------------------------------

needs_nofollow = pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"),
                                    reason="tree_sha256 opens files with os.O_NOFOLLOW")


def _package_copy(tmp_path):
    package = tmp_path / "capability_suite"
    shutil.copytree(emit.PACKAGE_DIR, package, ignore=shutil.ignore_patterns("__pycache__"))
    return package


@needs_nofollow
def test_pinned_fixture_suite_version_matches_the_recomputed_digest():
    assert me.PINNED_FIXTURE_SUITE_VERSION == emit.suite_digest(), (
        "capability_suite/fixtures/ or a capability_suite module changed: set "
        "model_endpoint.PINNED_FIXTURE_SUITE_VERSION to emit.suite_digest() in the same change")


@needs_nofollow
@pytest.mark.parametrize("edit", ["fixture", "module", "new_module"])
def test_any_suite_edit_changes_the_digest(tmp_path, edit):
    package = _package_copy(tmp_path)
    before = emit.suite_digest(package)
    if edit == "fixture":
        path = package / "fixtures" / "streaming" / "sse_message_stop" / "case.yaml"
        path.write_text(path.read_text() + "# edited\n")
    elif edit == "module":
        (package / "runner.py").write_text((package / "runner.py").read_text() + "# edited\n")
    else:
        (package / "extra.py").write_text("")
    assert emit.suite_digest(package) != before


@needs_nofollow
def test_pycache_never_changes_the_digest(tmp_path):
    package = _package_copy(tmp_path)
    before = emit.suite_digest(package)
    (package / "__pycache__").mkdir()
    (package / "__pycache__" / "runner.cpython-312.pyc").write_bytes(b"\0bytecode")
    assert emit.suite_digest(package) == before
```

- [ ] **Step 2: Run the tests and confirm the pin test fails.**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts python -m pytest tests/test_capability_suite.py -q
```

  Expected: `1 failed, 71 passed`. The failure is
  `test_pinned_fixture_suite_version_matches_the_recomputed_digest` (`None == '<64 hex>'`).

- [ ] **Step 3: Compute the digest.** Package files must not change after this step. If one does,
  repeat this step.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts python3 -c "from factory_core.capability_suite import emit; print(emit.suite_digest())"
```

  Expected: one 64-character lowercase hex string, called `<DIGEST>` below. Do not copy a value
  from this plan. The digest depends on the exact bytes committed in Tasks 1–4.

- [ ] **Step 4: Set the pin.** In `scripts/factory_core/providers/model_endpoint.py`, replace

```python
# No fixture suite is pinned until #561 ships one, so every local selection's evidence
# resolves to `unknown` until then.
PINNED_FIXTURE_SUITE_VERSION = None
```

  with the following, using the literal digest from Step 3 in place of `<DIGEST>`:

```python
# The pinned capability fixture suite (#561): capability_suite.emit.suite_digest(), a SHA-256
# over capability_suite/fixtures/ and the package's top-level modules. Never hand-bumped:
# tests/test_capability_suite.py fails when it differs from the recomputed digest.
PINNED_FIXTURE_SUITE_VERSION = "<DIGEST>"
```

  This file changes nowhere else. `model_endpoint.py` is not hashed, so setting the pin does not
  change the digest.

- [ ] **Step 5: Run both test files and confirm exactly the two predicted failures.**

```bash
find scripts -name __pycache__ -prune -exec rm -rf {} +
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts python -m pytest tests/test_capability_suite.py tests/test_provider_model_endpoint.py -q
```

  Expected: `2 failed`, namely
  `test_fixture_evidence_matches_fixture_entry_but_stays_unknown` (its reasons no longer name
  `fixture_suite_version`) and `test_unpinned_fixture_suite_is_unknown` (it resolves `stale`, not
  `unknown`). Everything else passes.

- [ ] **Step 6: Fix `test_fixture_evidence_matches_fixture_entry_but_stays_unknown`.** In
  `tests/test_provider_model_endpoint.py`, replace

```python
    assert "quantisation" in reasons[0] and "fixture_suite_version" in reasons[0]
```

  with

```python
    assert "quantisation" in reasons[0]
```

  The fixture record still resolves `unknown` through `quantisation`.

- [ ] **Step 7: Fix `test_unpinned_fixture_suite_is_unknown`** so that it still tests the unpinned
  case. Replace

```python
def test_unpinned_fixture_suite_is_unknown():
    state, reasons = me.evidence_state(_record(LOCAL, invalidated_by=["model_digest"]), LOCAL)
```

  with

```python
def test_unpinned_fixture_suite_is_unknown(monkeypatch):
    monkeypatch.setattr(me, "PINNED_FIXTURE_SUITE_VERSION", None)
    state, reasons = me.evidence_state(_record(LOCAL, invalidated_by=["model_digest"]), LOCAL)
```

- [ ] **Step 8: Run both test files and confirm they pass.**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts python -m pytest tests/test_capability_suite.py tests/test_provider_model_endpoint.py -q
```

  Expected: `0 failed`, i.e. the 72 capability-suite tests plus every
  `test_provider_model_endpoint.py` test pass.

- [ ] **Step 9: Commit.**

```bash
git add scripts/factory_core/providers/model_endpoint.py tests/test_provider_model_endpoint.py tests/test_capability_suite.py
git commit -m "test(model): pin PINNED_FIXTURE_SUITE_VERSION to the capability suite digest (#561)"
```

## Task 6: Full verification and scope check

**Files:** none created. This task only verifies; any fix goes back to the task that owns the
file. If the fix touches the package, re-pin per Task 5 Step 3.

- [ ] **Step 1: Run the full Python suite the way CI does.**

```bash
find scripts tests -name __pycache__ -prune -exec rm -rf {} +
PYTHONPATH=scripts python -m pytest tests/ -q
```

  Expected: `0 failed` (the dry run gave `3839 passed`; the exact count moves with `main`).

- [ ] **Step 2: Re-confirm the pin against a clean tree.**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts python3 -c "
from factory_core.capability_suite import emit
from factory_core.providers import model_endpoint as me
assert me.PINNED_FIXTURE_SUITE_VERSION == emit.suite_digest(), 'pin drifted: re-run Task 5 Step 3'
print('pin ok', me.PINNED_FIXTURE_SUITE_VERSION)"
```

  Expected: `pin ok <DIGEST>`.

- [ ] **Step 3: Run the acceptance command once more, now with the pin set.**

```bash
PYTHONPATH=scripts python3 -m factory_core.capability_suite run --base-url mock --out /tmp/capsuite/result.json >/dev/null; echo "exit=$?"
python3 -c "import json; r=json.load(open('/tmp/capsuite/result.json'))['certification_result']; print(r['fixture_suite_version'], r['target_kind'], r['base_url_scope'], sorted(set(r['results'].values())))"
```

  Expected: `exit=0`, then `<DIGEST> mock same-host ['not_tested', 'pass']`.

- [ ] **Step 4: Scope check (spec acceptance criterion).**

```bash
git fetch origin main
git diff --name-only origin/main...HEAD | grep -v '^scripts/factory_core/capability_suite/' | sort
```

  Expected, exactly, plus any `.archon/memory/` file that refine already committed on this branch:

```text
docs/superpowers/plans/2026-11-26-pinned-harness-capability-fixtures.md
docs/superpowers/specs/2026-11-26-pinned-harness-capability-fixtures-design.md
scripts/factory_core/providers/model_endpoint.py
tests/test_capability_suite.py
tests/test_provider_model_endpoint.py
```

  If any other path appears, confirm it with the two-dot form
  `git diff origin/main HEAD -- <path>` before treating it as out of scope (memory #250). Then
  confirm that the hard-limit surfaces are untouched:

```bash
git diff --name-only origin/main HEAD -- scheduler.sh entrypoint.sh Dockerfile .github deploy config scripts/factory_core/model_proxy.py scripts/factory_core/providers/model_adapter.py scripts/factory_core/providers/role_isolation.py
git diff origin/main HEAD -- scripts/factory_core/providers/model_endpoint.py | grep '^[-+][^-+]' 
```

  Expected: the first command prints nothing. The second prints only the three removed lines
  (two comment lines and `= None`) and the four added lines (three comment lines and the pin).

- [ ] **Step 5: Confirm the suite is hermetic.**

```bash
grep -rnE "https?://" scripts/factory_core/capability_suite/fixtures || echo "no URL in fixtures"
grep -nE "[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+" tests/test_capability_suite.py scripts/factory_core/capability_suite/*.py $(find scripts/factory_core/capability_suite/fixtures -name case.yaml) | grep -vE "127\.0\.0\.1|192\.0\.2\." || echo "only loopback and TEST-NET-1"
```

  Expected: `no URL in fixtures`, then `only loopback and TEST-NET-1`. No test connects to
  `192.0.2.10`: every argv that names it fails argument validation before any socket opens.

- [ ] **Step 6: Confirm the working tree is clean.** Each task committed its own files. Pushing
  and opening the PR are the workflow's later nodes, not this plan's.

```bash
git status --short
```

  Expected: no output.

## Self-review

- **Spec coverage.** Req 1: shipped package under `scripts/` (Tasks 1–4). Req 2: vocabulary
  imported from `model_endpoint`; `WORST_FIRST` is an ordering that a test checks against
  `RESULT_VALUES`. Req 3: six dimension directories, `HARNESS_PENDING` adds `not_tested`. Req 4:
  `suite_digest()` plus the pin and its tests (Task 5). Req 5: `run --base-url --out` with
  `driver`/`target` on every case. Req 6: `certification_result` key, no evidence fields,
  `model-registry` refusal. Req 7: values validated in `build_result`. Req 8: `model_adapter` and
  `role_isolation` functions only, no `LOCAL_ROLE`. Req 9: no registry load, no destination
  resolved. Req 10: tiers table above. Req 11: observations feed no dimension; each has its
  paired control; `message_stop` probe and control; only `192.0.2.0/24` and loopback appear.
  Req 12: scope check (Task 6 Step 4). Req 13: loopback only, sentinel credential, no GitHub.
  Req 14: `.archon/memory/` untouched.
- **Acceptance criteria → tests.** Mock run exit 0 with six keys:
  `test_mock_run_exits_0_with_streaming_and_role_isolation_pass`. Controls match, and a changed
  fault exits 3: `test_every_control_matches_its_expect`, `test_a_changed_control_fault_exits_3`.
  Observations change no dimension: `test_an_unsupported_observation_changes_no_dimension`.
  Digest: `test_pinned_fixture_suite_version_matches_the_recomputed_digest`,
  `test_any_suite_edit_changes_the_digest`. Vocabulary, URL, credential and `--out`:
  `test_result_rejects_values_outside_the_vocabulary`,
  `test_endpoint_mode_writes_no_url_or_credential`, `test_out_under_model_registry_is_refused`.
  No route qualifies: `test_part_a_result_qualifies_no_route`.
- **Issue-number line** `**Issue:** #561` is present under the title.
- **No placeholders.** Every file's full content is given. The only substituted value is
  `<DIGEST>`, which Task 5 Step 3 computes. It cannot be written in advance because it is a
  hash of the files committed in Tasks 1–4.
