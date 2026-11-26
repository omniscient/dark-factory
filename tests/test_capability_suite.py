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

from factory_core.capability_suite import emit, mock_server  # noqa: E402
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
