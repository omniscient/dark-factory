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
