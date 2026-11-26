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

from factory_core.capability_suite import emit, mock_server, runner  # noqa: E402
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
