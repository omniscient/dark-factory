"""Tier 1 tests for the pinned capability suite (#561): every part A case against the in-process
mock, the role_isolation probes, the digest pin and the certification_result contract.

Hermetic: every connection is to 127.0.0.1, no secret is read (the endpoint-mode credential is
a sentinel set by monkeypatch), nothing touches GitHub.
"""
import asyncio
import inspect
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import pytest
import yaml
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from factory_core.capability_suite import emit, harness, mock_server, relay, runner  # noqa: E402
from factory_core.providers import model_endpoint as me  # noqa: E402


@pytest.fixture(autouse=True)
def _no_claude(monkeypatch):
    # Never spawn `claude --version` in a test (emit's harness_version, Tier 2 preflight).
    # preflight() now calls this with env=harness.scrubbed_env(...); accept any arguments.
    monkeypatch.setattr(me, "observed_harness_version", lambda *a, **k: None)


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


def _on_mock(script, *calls):
    """([(status, body bytes)] of `calls` — (method, path, json body or None) — to one fresh
    mock armed with `script`, and that mock's _Observed)."""
    async def go():
        mock_runner, url = await mock_server.start()
        try:
            observed = mock_server.arm(mock_runner.app, script)
            replies = []
            async with aiohttp.ClientSession() as session:
                for method, path, body in calls:
                    async with session.request(method, url + path, json=body) as resp:
                        replies.append((resp.status, await resp.read()))
            return replies, observed
        finally:
            await mock_runner.cleanup()
    return asyncio.run(go())


def test_mock_answers_the_cli_hello_probe():
    replies, observed = _on_mock(None, ("HEAD", "/api/hello", None))
    assert replies == [(200, b"")]
    assert [(r["method"], r["path"], r["status"]) for r in observed.requests] == [
        ("HEAD", "/api/hello", 200)]


def test_mock_accepts_the_cli_beta_query_string():
    replies, observed = _on_mock(None, ("POST", "/v1/messages?beta=true", ASK))
    status, data = replies[0]
    assert status == 200 and json.loads(data)["model"] == mock_server.SERVED_NAME
    assert observed.requests[0]["path"] == "/v1/messages?beta=true"


def test_mock_records_every_request_with_lower_cased_headers():
    replies, observed = _on_mock(None, ("POST", "/v1/messages", ASK),
                                 ("POST", "/v1/messages/count_tokens", ASK))
    assert [s for s, _ in replies] == [200, 200]
    first, second = observed.requests
    assert first["method"] == "POST" and first["body"] == ASK and first["status"] == 200
    assert "content-type" in first["headers"] and "Content-Type" not in first["headers"]
    assert second["path"] == "/v1/messages/count_tokens" and second["status"] == 200


def test_arm_clears_the_log_and_refuses_an_unknown_script():
    async def go():
        mock_runner, url = await mock_server.start()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url + "/v1/messages", json=ASK) as resp:
                    await resp.read()
            observed = mock_runner.app[mock_server.OBSERVED]
            assert len(observed.requests) == 1
            assert mock_server.arm(mock_runner.app) is observed and observed.requests == []
            with pytest.raises(ValueError, match="script must be one of"):
                mock_server.arm(mock_runner.app, mock_server.Script("nope"))
        finally:
            await mock_runner.cleanup()
    asyncio.run(go())


# The pinned CLI's tools as the scripts see them (measured: about 20, Agent first, Read present).
CLI_TOOLS = [{"name": "Agent", "input_schema": {"type": "object", "required": ["prompt"]}},
             {"name": "Read", "input_schema": {"type": "object", "required": ["file_path"]}}]
OK_TEXT = [{"type": "text", "text": "ok"}]


def test_text_script_never_calls_a_tool():
    replies, _ = _on_mock(mock_server.Script("text"),
                          ("POST", "/v1/messages", dict(ASK, tools=CLI_TOOLS)))
    assert json.loads(replies[0][1])["content"] == OK_TEXT


def test_read_script_fabricates_one_read_call_then_text():
    ask = dict(ASK, tools=CLI_TOOLS)
    replies, observed = _on_mock(mock_server.Script("read", read_path="/tmp/seeded.txt"),
                                 ("POST", "/v1/messages", ask), ("POST", "/v1/messages", ask))
    first, second = (json.loads(data) for _, data in replies)
    assert first["content"] == [{"type": "tool_use", "id": mock_server.TOOL_USE_ID,
                                 "name": "Read", "input": {"file_path": "/tmp/seeded.txt"}}]
    assert first["stop_reason"] == "tool_use" and second["content"] == OK_TEXT
    assert observed.script.fired


def test_read_script_never_substitutes_another_tool():
    replies, observed = _on_mock(mock_server.Script("read", read_path="/tmp/seeded.txt"),
                                 ("POST", "/v1/messages", dict(ASK, tools=CLI_TOOLS[:1])))
    assert json.loads(replies[0][1])["content"] == OK_TEXT and not observed.script.fired


def test_agent_script_fabricates_one_agent_call_with_an_explicit_model():
    ask = dict(ASK, tools=CLI_TOOLS)
    replies, _ = _on_mock(mock_server.Script("agent"),
                          ("POST", "/v1/messages", ask), ("POST", "/v1/messages", ask))
    first, second = (json.loads(data) for _, data in replies)
    (use,) = first["content"]
    assert use["name"] == "Agent" and use["id"] == mock_server.TOOL_USE_ID
    assert use["input"] == {"description": "capability-suite probe",
                            "prompt": mock_server.NESTED_AGENT_PROMPT,
                            "subagent_type": "general-purpose",
                            "model": mock_server.NESTED_AGENT_MODEL}
    assert second["content"] == OK_TEXT


def test_stall_script_sees_the_client_hang_up():
    async def go():
        mock_runner, url = await mock_server.start()
        try:
            observed = mock_server.arm(mock_runner.app, mock_server.Script("stall"))
            async with aiohttp.ClientSession() as session:
                async with session.post(url + "/v1/messages",
                                        json=dict(ASK, stream=True)) as resp:
                    assert (await resp.content.readline()).startswith(b"event: message_start")
                    await asyncio.sleep(0.3)  # still streaming keep-alive comments
                    assert not observed.disconnected
            for _ in range(50):  # the hang-up reaches the handler asynchronously
                if observed.disconnected:
                    break
                await asyncio.sleep(0.1)
            return observed.disconnected
        finally:
            await mock_runner.cleanup()
    assert asyncio.run(go())


# --- certification_result -------------------------------------------------------------------

def _result(**kw):
    args = dict(target_kind="endpoint", base_url="http://192.0.2.10:8080",
                results={d: "pass" for d in me.ACTOR_REQUIRED_DIMENSIONS}, harness_pending=(),
                controls=[], fixture_suite_version="f" * 64,
                now=datetime(2026, 11, 26, tzinfo=timezone.utc))
    args.update(kw)
    return emit.build_result(**args)


def test_result_records_the_scope_never_the_url():
    body = _result()["certification_result"]
    assert body["base_url_scope"] == "lan" and "192.0.2.10" not in json.dumps(body)
    assert body["fixture_suite_version"] == "f" * 64
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


# --- fixture tree and run_cases -------------------------------------------------------------

def _summary(cases=None, context_tokens=mock_server.CONTEXT_TOKENS, tier2_mode="skip"):
    """runner.run_cases over the shipped tree (or `cases`), the mock standing in for the
    endpoint."""
    async def go():
        mock_runner, url = await mock_server.start()
        try:
            mock = runner.Target(url, mock_server.SERVED_NAME, mock_server.DUMMY_CREDENTIAL)
            return await runner.run_cases(runner.load_cases() if cases is None else cases,
                                          endpoint=mock, mock=mock,
                                          context_tokens=context_tokens,
                                          mock_app=mock_runner.app, tier2_mode=tier2_mode)
        finally:
            await mock_runner.cleanup()
    return asyncio.run(go())


def _cases_by_name():
    return {f"{c['dimension']}/{c['case']}": c for c in runner.load_cases()}


def _copy_fixtures(tmp_path):
    dest = tmp_path / "fixtures"
    shutil.copytree(runner.FIXTURES_DIR, dest)
    return dest


def _with_harness_case(tmp_path, case=None):
    """A fixture-tree copy with one extra harness case, streaming/via_cli (no shipped harness
    case lives under streaming, so it never collides with one)."""
    fixtures = _copy_fixtures(tmp_path)
    case_dir = fixtures / "streaming" / "via_cli"
    case_dir.mkdir()
    (case_dir / "case.yaml").write_text(yaml.safe_dump(case or HARNESS_CASE))
    return fixtures


def test_dimension_directories_are_exactly_the_actor_required_dimensions():
    dirs = {p.name for p in runner.FIXTURES_DIR.iterdir() if p.is_dir()}
    assert dirs == set(me.ACTOR_REQUIRED_DIMENSIONS)


def test_fixtures_hold_only_case_yaml_files():
    files = [p for p in runner.FIXTURES_DIR.rglob("*") if p.is_file()]
    assert files and all(p.name == "case.yaml" for p in files)
    assert len(files) == len(runner.load_cases()) == 31


def test_harness_pending_is_empty():
    assert runner.HARNESS_PENDING == ()


def test_worst_first_orders_exactly_the_result_values():
    assert sorted(runner.WORST_FIRST) == sorted(me.RESULT_VALUES)


def test_each_formerly_pending_dimension_ships_one_harness_case():
    cases = [c for c in runner.load_cases() if c["driver"] == "harness"]
    assert {f"{c['dimension']}/{c['case']}": c["check"] for c in cases} == {
        "protocol_compatibility/cli_alias_and_nested_agent_resolution": "cli_alias_resolution",
        "tool_call_fidelity/cli_tool_round_trip_and_injection_resistance": "cli_tool_round_trip",
        "context_length/cli_padded_prompt_and_over_limit": "cli_context_window",
        "timeout_cancellation/cli_sigint_closes_connection": "cli_sigint",
    }
    assert all(c["kind"] == "probe" and c["target"] == "mock" for c in cases)
    assert sorted(c["dimension"] for c in cases) == sorted(PART_A_PENDING)
    assert set(runner.HARNESS_CHECKS) == {c["check"] for c in cases}


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
    }
    assert observations == {"protocol_compatibility/count_tokens_observed",
                            "tool_call_fidelity/forced_tool_choice_observed"}
    probes = {n for n, c in cases.items() if c["kind"] == "probe"}
    assert {n for n in probes if not n.startswith("role_isolation/")} == {
        "protocol_compatibility/non_streaming_round_trip", "tool_call_fidelity/tool_use_round_trip",
        "streaming/sse_message_stop", "context_length/sized_request",
        "protocol_compatibility/cli_alias_and_nested_agent_resolution",
        "tool_call_fidelity/cli_tool_round_trip_and_injection_resistance",
        "context_length/cli_padded_prompt_and_over_limit",
        "timeout_cancellation/cli_sigint_closes_connection"}


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
    assert len(controls) == 15
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


HARNESS_CASE = {"case": "via_cli", "dimension": "streaming", "driver": "harness",
                "kind": "probe", "target": "mock", "check": "cli_sigint"}
# What a Tier 2-off run reports pending: part A's HARNESS_PENDING, now from skipped cases.
PART_A_PENDING = ["protocol_compatibility", "tool_call_fidelity", "context_length",
                  "timeout_cancellation"]


@pytest.mark.parametrize("mode", ["skip", "missing"])
def test_a_skipped_harness_case_is_not_tested_and_never_a_failure(mode):
    summary = _summary([HARNESS_CASE], tier2_mode=mode)
    assert summary.not_run == [] and summary.probes == []
    assert summary.probe_values == {"streaming": ["not_tested"]}
    assert summary.harness_pending == ["streaming"]
    assert runner.exit_code(summary, True, mode) == (6 if mode == "missing" else 0)


@pytest.mark.parametrize("edit, message", [
    (lambda c: c.update(check="nope"), "check must be one of"),
    (lambda c: c.update(target="endpoint"), "a harness case is a mock-target probe"),
    (lambda c: c.update(kind="observation"), "a harness case is a mock-target probe"),
])
def test_a_malformed_harness_case_is_rejected(tmp_path, edit, message):
    case = dict(HARNESS_CASE)
    edit(case)
    fixtures = _with_harness_case(tmp_path, case)
    with pytest.raises(runner.CaseError, match=message):
        runner.load_cases(fixtures)


def test_exit_code_priority():
    S = runner.Summary
    assert runner.exit_code(S(controls=[{"ok": False}], not_run=["x"]), True, "missing") == 3
    assert runner.exit_code(S(not_run=["x"], probes=[("x", "fail")]), True, "missing") == 6
    assert runner.exit_code(S(not_run=["x"], probes=[("x", "fail")]), True, "ok") == 4
    assert runner.exit_code(S(probes=[("x", "fail")]), True, "ok") == 5
    assert runner.exit_code(S(probes=[("x", "pass")]), True, "ok") == 0


def test_cache_hit_usage_is_not_input_truncation():
    """A prefix-cache hit reports a small input_tokens with the rest under
    cache_read_input_tokens: the control passes (#628), while the same small
    input_tokens with no cache field (truncate_input) still fails."""
    by_case = {c["case"]: c for c in _summary().controls}
    assert by_case["cache_hit"]["observed"] == "pass:ok" and by_case["cache_hit"]["ok"]
    assert by_case["silent_input_truncation"]["observed"] == "fail:input_truncated"


def test_the_context_probe_does_not_run_without_a_window():
    cases = [c for c in runner.load_cases() if c["case"] == "sized_request"]
    assert _summary(cases, context_tokens=None).not_run == ["context_length/sized_request"]


def test_a_pending_dimension_reports_a_shipped_probe_failure():
    values = {"context_length": ["fail"], "streaming": ["pass"], "role_isolation": ["pass"]}
    results = runner.dimension_results(values)
    assert results["context_length"] == "fail" and results["streaming"] == "pass"
    assert results["timeout_cancellation"] == "not_tested"


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
    assert body["harness_pending"] == PART_A_PENDING
    assert body["fixture_suite_version"] == me.PINNED_FIXTURE_SUITE_VERSION
    assert body["target_kind"] == "mock" and body["base_url_scope"] == "same-host"
    assert len(body["controls"]) == 17 and all(c["ok"] for c in body["controls"])


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


def test_a_modified_tree_records_its_own_digest_not_the_pin(tmp_path, capsys):
    fixtures = _copy_fixtures(tmp_path)
    path = fixtures / "streaming" / "sse_message_stop" / "case.yaml"
    path.write_text(path.read_text() + "# edited\n")
    code, result, _ = _run(tmp_path, "--base-url", "mock", fixtures_dir=fixtures)
    version = result["certification_result"]["fixture_suite_version"]
    assert code == 0 and version == emit.suite_digest(fixtures_dir=fixtures)
    assert version != me.PINNED_FIXTURE_SUITE_VERSION
    assert "differs from model_endpoint.PINNED_FIXTURE_SUITE_VERSION" in capsys.readouterr().err


def test_a_malformed_tree_exits_3_and_writes_nothing(tmp_path):
    fixtures = _copy_fixtures(tmp_path)
    shutil.rmtree(fixtures / "streaming")
    code, result, _ = _run(tmp_path, "--base-url", "mock", fixtures_dir=fixtures)
    assert code == 3 and result is None


def _fake_harness(monkeypatch, outcome):
    """Every HARNESS_CHECKS entry -> a fake that arms a script (which the runner must clear
    again) and returns `outcome`, or raises it if it is an exception. Returns the base URLs."""
    calls = []

    async def fake(app, base_url):
        calls.append(base_url)
        mock_server.arm(app, mock_server.Script("text"))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    for name in runner.HARNESS_CHECKS:
        monkeypatch.setitem(runner.HARNESS_CHECKS, name, fake)
    return calls


def _tier2(monkeypatch, version=None):
    monkeypatch.setenv(harness.TIER2_ENV, "1")
    monkeypatch.setattr(me, "observed_harness_version",
                        lambda **k: harness.PINNED_HARNESS_VERSION if version is None else version)


def test_tier2_runs_every_harness_case_and_clears_its_script(tmp_path, monkeypatch):
    _tier2(monkeypatch)
    calls = _fake_harness(monkeypatch, ("pass", "ok"))
    fixtures = _with_harness_case(tmp_path)
    code, result, _ = _run(tmp_path, "--base-url", "mock", fixtures_dir=fixtures)
    harness_cases = [c for c in runner.load_cases(fixtures) if c["driver"] == "harness"]
    # Exit 0 also proves the script was cleared: a left-armed `text` script would turn part A's
    # tool_call_fidelity controls and probe (which run after streaming/via_cli) red.
    assert code == 0 and len(calls) == len(harness_cases)
    assert all(url.startswith("http://127.0.0.1:") for url in calls)
    body = result["certification_result"]
    assert body["results"]["streaming"] == "pass" and "streaming" not in body["harness_pending"]


@pytest.mark.parametrize("fault", [FileNotFoundError("claude"), KeyError("messages")])
def test_a_harness_probe_that_cannot_run_exits_4(tmp_path, monkeypatch, fault):
    _tier2(monkeypatch)
    _fake_harness(monkeypatch, fault)
    code, result, _ = _run(tmp_path, "--base-url", "mock",
                           fixtures_dir=_with_harness_case(tmp_path))
    body = result["certification_result"]
    assert code == 4 and body["results"]["streaming"] == "not_tested"
    assert "streaming" in body["harness_pending"]


def test_a_failing_harness_probe_in_mock_mode_exits_5(tmp_path, monkeypatch):
    _tier2(monkeypatch)
    _fake_harness(monkeypatch, ("fail", "connection_not_closed"))
    code, result, _ = _run(tmp_path, "--base-url", "mock",
                           fixtures_dir=_with_harness_case(tmp_path))
    assert code == 5 and result["certification_result"]["results"]["streaming"] == "fail"


def test_tier2_without_a_claude_binary_exits_6_and_still_writes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(harness.TIER2_ENV, "1")  # observed_harness_version is None (autouse)
    calls = _fake_harness(monkeypatch, ("pass", "ok"))
    code, result, _ = _run(tmp_path, "--base-url", "mock",
                           fixtures_dir=_with_harness_case(tmp_path))
    body = result["certification_result"]
    assert code == 6 and calls == []
    assert body["results"]["streaming"] == "not_tested" and "streaming" in body["harness_pending"]
    assert "no claude binary" in capsys.readouterr().err


def test_tier2_version_mismatch_exits_7_and_writes_nothing(tmp_path, monkeypatch, capsys):
    _tier2(monkeypatch, version="0.0.1")
    calls = _fake_harness(monkeypatch, ("pass", "ok"))
    code, result, out = _run(tmp_path, "--base-url", "mock")
    err = capsys.readouterr().err
    assert code == 7 and result is None and not out.parent.exists() and calls == []
    assert "0.0.1" in err and harness.PINNED_HARNESS_VERSION in err


def test_tier2_with_passing_harness_checks_passes_all_six_dimensions(tmp_path, monkeypatch):
    _tier2(monkeypatch)
    calls = _fake_harness(monkeypatch, ("pass", "ok"))
    code, result, _ = _run(tmp_path, "--base-url", "mock")
    body = result["certification_result"]
    assert code == 0 and len(calls) == 4
    assert body["results"] == {d: "pass" for d in me.ACTOR_REQUIRED_DIMENSIONS}
    assert body["harness_pending"] == []


def test_tier2_without_a_claude_binary_reports_the_four_dimensions(tmp_path, monkeypatch):
    monkeypatch.setenv(harness.TIER2_ENV, "1")
    code, result, _ = _run(tmp_path, "--base-url", "mock")
    body = result["certification_result"]
    assert code == 6 and body["harness_pending"] == PART_A_PENDING
    assert all(body["results"][d] == "not_tested" for d in PART_A_PENDING)


def test_a_usage_error_stays_exit_2_whatever_cli_is_installed(tmp_path, monkeypatch):
    _tier2(monkeypatch, version="0.0.1")
    assert _usage_error("--base-url", "mock", "--context-tokens", "0",
                        "--out", str(tmp_path / "r.json")) == 2


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
        for d in PART_A_PENDING]


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


# --- harness driver (#624) ------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_pinned_harness_version_is_the_dockerfile_pin():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()
    assert re.findall(r"@anthropic-ai/claude-code@(\S+)", dockerfile) == [
        harness.PINNED_HARNESS_VERSION]


def test_scrubbed_env_is_exactly_the_allow_list(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", SENTINEL)
    monkeypatch.setenv("GH_TOKEN", SENTINEL)
    monkeypatch.setenv("HOME", str(tmp_path / "caller-home"))
    env = harness.scrubbed_env(base_url="http://127.0.0.1:9",
                               served_name=mock_server.SERVED_NAME, home=tmp_path / "h")
    assert set(env) == set(harness.BASE_ENV_KEYS)
    assert env["HOME"] == str(tmp_path / "h")
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9"
    assert env["ANTHROPIC_API_KEY"] == mock_server.DUMMY_CREDENTIAL
    assert all(env[alias] == mock_server.SERVED_NAME for alias in me.TIER_ALIASES)
    assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert env["DISABLE_AUTOUPDATER"] == "1"
    assert SENTINEL not in json.dumps(env) and str(tmp_path / "caller-home") not in env.values()


def _stub_claude(bin_dir, dump):
    """A `claude` on PATH that dumps the environment it was exec'd with (/proc/self/environ:
    the initial block, untouched by Python's own locale coercion), its cwd, argv and stdin."""
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "raw = open('/proc/self/environ', 'rb').read().split(b'\\0')\n"
        "env = dict(item.decode().split('=', 1) for item in raw if item)\n"
        f"with open({str(dump)!r}, 'w') as f:\n"
        "    json.dump({'env': env, 'cwd': os.getcwd(), 'argv': sys.argv[1:],\n"
        "               'stdin': sys.stdin.read()}, f)\n")
    stub.chmod(0o755)


@pytest.mark.skipif(not Path("/proc/self/environ").exists(),
                    reason="reads the child's initial environment from /proc")
def test_the_spawned_cli_gets_only_the_scrubbed_environment(tmp_path, monkeypatch):
    dump = tmp_path / "dump.json"
    _stub_claude(tmp_path / "bin", dump)
    caller_home = tmp_path / "caller-home"
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", SENTINEL)
    monkeypatch.setenv("GH_TOKEN", SENTINEL)
    monkeypatch.setenv("HOME", str(caller_home))

    async def go():
        with harness.workdir() as root:
            status, _ = await harness.run_cli("http://127.0.0.1:9", root, "a prompt",
                                              "--model", "opus")
            return status, root
    status, root = asyncio.run(go())
    seen = json.loads(dump.read_text())
    assert status == 0
    assert set(seen["env"]) == set(harness.BASE_ENV_KEYS)
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in seen["env"] and "GH_TOKEN" not in seen["env"]
    assert SENTINEL not in json.dumps(seen)
    assert seen["env"]["HOME"] == str(root / "home") != str(caller_home)
    assert Path(seen["cwd"]).resolve() == (root / "cwd").resolve()
    assert Path(seen["cwd"]).resolve() != Path.cwd().resolve()
    assert seen["stdin"] == ""
    assert seen["argv"] == ["-p", "a prompt", "--model", "opus"]
    assert not root.exists()  # the temporary HOME and cwd are removed afterwards


@pytest.mark.parametrize("value, enabled", [("1", True), ("", False), ("0", False),
                                            ("true", False), (None, False)])
def test_tier2_is_enabled_only_by_exactly_1(value, enabled):
    environ = {} if value is None else {harness.TIER2_ENV: value}
    assert harness.tier2_enabled(environ) is enabled


def test_preflight_with_tier2_off_never_reads_the_cli_version(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("claude --version read with Tier 2 off")
    monkeypatch.setattr(me, "observed_harness_version", boom)
    assert harness.preflight(False) == ("skip", None)


@pytest.mark.parametrize("version, want", [
    (None, ("missing", None)),
    (harness.PINNED_HARNESS_VERSION, ("ok", None)),
    ("2.1.281", ("mismatch", "2.1.281")),
])
def test_preflight_with_tier2_on(monkeypatch, version, want):
    monkeypatch.setattr(me, "observed_harness_version", lambda **k: version)
    assert harness.preflight(True) == want


def test_preflight_reads_the_version_through_a_scrubbed_env(tmp_path, monkeypatch):
    """preflight() must not let observed_harness_version() inherit this process's own
    environment (CLAUDE_CODE_OAUTH_TOKEN, GH_TOKEN, HOME): it passes harness.scrubbed_env()
    with a temporary HOME, never the default None (which means "inherit os.environ")."""
    captured = {}

    def fake(env=None):
        captured["env"] = env
        return harness.PINNED_HARNESS_VERSION
    monkeypatch.setattr(me, "observed_harness_version", fake)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", SENTINEL)
    monkeypatch.setenv("GH_TOKEN", SENTINEL)
    monkeypatch.setenv("HOME", str(tmp_path / "caller-home"))
    assert harness.preflight(True) == ("ok", None)
    env = captured["env"]
    assert env is not None
    assert set(env) == set(harness.BASE_ENV_KEYS)
    assert SENTINEL not in json.dumps(env)
    assert env["HOME"] != str(tmp_path / "caller-home")


def _hello():
    return {"method": "HEAD", "path": "/api/hello", "status": 200, "body": None, "headers": {}}


def _rec(body, status=200):
    return {"method": "POST", "path": "/v1/messages?beta=true", "status": status, "body": body,
            "headers": {"x-api-key": mock_server.DUMMY_CREDENTIAL}}


def _cli_body(*texts, model=mock_server.SERVED_NAME, tools=("Agent", "Read"), after=()):
    """A request shaped like the pinned CLI's (measured 2026-11-26): user text blocks, a
    role: system message inside messages, a top-level system list, Agent first in tools."""
    return {"model": model, "stream": True, "max_tokens": 32000,
            "system": [{"type": "text", "text": "You are a CLI."}],
            "tools": [{"name": n, "input_schema": {"type": "object"}} for n in tools],
            "messages": [{"role": "user", "content": [{"type": "text", "text": t} for t in texts]},
                         {"role": "system", "content": [{"type": "text", "text": "# Env"}]},
                         *after]}


def _alias_runs():
    runs = [(tier, 0, [_hello(), _rec(_cli_body(harness.ASK_OK))]) for tier in harness.TIERS]
    runs.append(("agent", 0, [_hello(), _rec(_cli_body(harness.ASK_OK)),
                              _rec(_cli_body(mock_server.NESTED_AGENT_PROMPT)),
                              _rec(_cli_body(harness.ASK_OK))]))
    return runs


def test_judge_alias_passes_the_measured_shape():
    assert harness.judge_alias(_alias_runs()) == ("pass", "ok")


@pytest.mark.parametrize("edit, reason", [
    (lambda r: r[1][2][1]["body"].update(model="sonnet"), "sonnet_model_not_served_name"),
    (lambda r: r[0][2][1]["headers"].update({"x-api-key": "sk-other"}),
     "opus_credential_not_dummy"),
    (lambda r: r[2][2][0]["headers"].update(authorization="Bearer x"),
     "haiku_credential_not_dummy"),
    (lambda r: r[3][2].pop(), "fable_no_request"),
    (lambda r: r.__setitem__(0, ("opus", 1, r[0][2])), "opus_cli_failed"),
    (lambda r: r[4][2].pop(2), "no_nested_request"),
    (lambda r: [r[4][2][i]["body"].update(tools=[{"name": "Read"}]) for i in (1, 2, 3)],
     "agent_not_offered"),
])
def test_judge_alias_fails(edit, reason):
    runs = _alias_runs()
    edit(runs)
    assert harness.judge_alias(runs) == ("fail", reason)


def _read_requests():
    use = {"type": "tool_use", "id": mock_server.TOOL_USE_ID, "name": "Read",
           "input": {"file_path": f"/tmp/capability-suite-x/cwd/{harness.PLANTED_FILE}"}}
    result = {"tool_use_id": mock_server.TOOL_USE_ID, "type": "tool_result",
              "content": f"1\t{harness.PLANTED_RESULT}\n2\t{harness.PLANTED_SWITCH}\n3\t"}
    follow = _cli_body(harness.READ_PROMPT, after=(
        {"role": "assistant", "content": [use]}, {"role": "user", "content": [result]},
        {"role": "system", "content": [{"type": "text", "text": "<total_tokens>"}]}))
    return [_hello(), _rec(_cli_body(harness.READ_PROMPT)), _rec(follow)]


def test_judge_read_passes_the_measured_shape():
    assert harness.judge_read(_read_requests()) == ("pass", "ok")


def _tool_result_block(requests):
    return requests[2]["body"]["messages"][3]["content"][0]


@pytest.mark.parametrize("edit, reason", [
    (lambda r: r.clear(), "no_request"),
    (lambda r: r[1]["body"].update(tools=[{"name": "Agent"}]), "read_not_offered"),
    (lambda r: r.pop(), "no_tool_result"),
    (lambda r: _tool_result_block(r).update(tool_use_id="toolu_other"), "tool_use_id_mismatch"),
    (lambda r: _tool_result_block(r).update(content="1\tnothing planted\n"),
     "planted_not_carried"),
    (lambda r: r[2]["body"]["system"].append({"type": "text", "text": harness.PLANTED_SWITCH}),
     "payload_promoted"),
    (lambda r: r[2]["body"]["messages"][0]["content"].append(
        {"type": "text", "text": harness.PLANTED_RESULT}), "payload_promoted"),
    (lambda r: r[2]["body"].update(model=mock_server.FALLBACK_MODEL), "model_switched"),
])
def test_judge_read_fails(edit, reason):
    requests = _read_requests()
    edit(requests)
    assert harness.judge_read(requests) == ("fail", reason)


def test_planted_strings_are_single_lines_outside_the_prompt_and_path():
    for planted in (harness.PLANTED_RESULT, harness.PLANTED_SWITCH):
        assert "\n" not in planted and planted not in harness.READ_PROMPT
        assert harness.PLANTED_FILE not in planted


def test_judge_padded():
    words = 50
    good = harness.padded_prompt(words)
    assert harness.judge_padded([_hello(), _rec(_cli_body(good))], words) == ("pass", "ok")
    damaged = good.replace(" pad pad ", " pad ", 1)
    assert harness.judge_padded([_rec(_cli_body(damaged))], words) == ("fail", "padding_damaged")
    assert harness.judge_padded([_rec(_cli_body(good), status=400)], words) == (
        "fail", "not_answered_200")
    assert harness.judge_padded([_hello()], words) == ("fail", "no_request")


def test_judge_over_limit_needs_both_signals():
    refused = [_hello(), _rec(_cli_body(harness.padded_prompt(10)), status=400)]
    answered = [_hello(), _rec(_cli_body(harness.padded_prompt(10)), status=200)]
    too_long = "Prompt is too long · this conversation is a single exchange"
    assert harness.judge_over_limit(refused, 1, too_long) == ("pass", "ok")
    assert harness.judge_over_limit(refused, 0, too_long) == ("fail", "over_limit_not_surfaced")
    assert harness.judge_over_limit(refused, 1, "Execution error") == (
        "fail", "over_limit_not_surfaced")
    assert harness.judge_over_limit(refused, None, "") == ("fail", "over_limit_not_surfaced")
    assert harness.judge_over_limit(answered, 1, too_long) == ("fail", "over_limit_not_refused")


def test_judge_sigint():
    one = [_hello(), _rec(_cli_body(harness.ASK_OK))]
    assert harness.judge_sigint(True, True, one) == ("pass", "ok")
    assert harness.judge_sigint(False, False, [_hello()]) == ("fail", "no_request")
    assert harness.judge_sigint(True, False, one) == ("fail", "connection_not_closed")
    assert harness.judge_sigint(True, True, one + [_rec(_cli_body(harness.ASK_OK))]) == (
        "fail", "retried")


def test_the_padded_prompts_straddle_the_mock_window():
    under = harness.padded_prompt(mock_server.CONTEXT_TOKENS // 2)
    over = harness.padded_prompt(mock_server.CONTEXT_TOKENS + 1)
    assert _mock("/v1/messages", dict(ASK, messages=[{"role": "user", "content": under}]))[0] == 200
    assert _mock("/v1/messages", dict(ASK, messages=[{"role": "user", "content": over}]))[0] == 400
    # The pinned CLI adds ~1.2k words of its own (measured 2026-11-26); half the window leaves room.
    assert mock_server.CONTEXT_TOKENS // 2 + 1500 < mock_server.CONTEXT_TOKENS


def test_every_harness_check_is_a_coroutine_function():
    for check in (harness.check_alias_resolution, harness.check_tool_round_trip,
                  harness.check_context_window, harness.check_sigint):
        assert inspect.iscoroutinefunction(check)  # asyncio's is deprecated in 3.14


def test_ci_runs_tier2_as_a_blocking_docker_build_step():
    ci = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())
    steps = ci["jobs"]["docker-build"]["steps"]
    runs = [step.get("run", "") for step in steps]
    build = next(i for i, run in enumerate(runs) if run.startswith("docker build"))
    tier2 = next(i for i, run in enumerate(runs) if "factory_core.capability_suite run" in run)
    step = steps[tier2]
    assert tier2 > build and "continue-on-error" not in step
    assert step["timeout-minutes"] == 10
    for needle in ("chmod 777", "--network none", "FACTORY_CAPABILITY_TIER2=1",
                   "PYTHONPATH=/opt/dark-factory/scripts", "--entrypoint python3 dark-factory:pr",
                   "--base-url mock"):
        assert needle in step["run"], needle
    upload = steps[tier2 + 1]
    assert upload["uses"].startswith("actions/upload-artifact@") and upload["if"] == "always()"
    assert "continue-on-error" not in upload


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
