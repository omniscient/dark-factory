import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core import model_proxy as mp


def test_redact_headers_strips_known_secrets():
    headers = {
        "Authorization": "Bearer sk-ant-xyz",
        "X-Api-Key": "abc123",
        "api-key": "def456",
        "Content-Type": "application/json",
    }
    redacted = mp.redact_headers(headers)
    assert redacted["Authorization"] == "[REDACTED]"
    assert redacted["X-Api-Key"] == "[REDACTED]"
    assert redacted["api-key"] == "[REDACTED]"
    assert redacted["Content-Type"] == "application/json"


def test_redact_headers_strips_factory_secret_pattern():
    headers = {
        "X-Factory-Deploy-Token": "tok-1",
        "X-Factory-Github-Secret": "sec-1",
        "X-Factory-Persona": "implement",
    }
    redacted = mp.redact_headers(headers)
    assert redacted["X-Factory-Deploy-Token"] == "[REDACTED]"
    assert redacted["X-Factory-Github-Secret"] == "[REDACTED]"
    assert redacted["X-Factory-Persona"] == "implement"


def test_build_ledger_row_shape():
    # intent="fix" is a multi-phase intent (implement -> conformance -> code-review),
    # so the caller passes stage="unknown" — this row shows the honest degraded case.
    row = mp.build_ledger_row(
        endpoint="/v1/messages",
        method="POST",
        model="claude-sonnet-4-6-20251101",
        status=200,
        duration_ms=1234,
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        tool_count=3,
        tool_bytes=900,
        system_bytes=400,
        request_bytes=2000,
        largest_tools=[{"name": "Bash", "bytes": 500}],
        streamed=True,
        run_id="abc123",
        issue_number=208,
        intent="fix",
        stage="unknown",
    )
    assert row["endpoint"] == "/v1/messages"
    assert row["model"] == "claude-sonnet-4-6-20251101"
    assert row["status"] == 200
    assert row["gen_ai.usage.input_tokens"] == 100
    assert row["gen_ai.usage.output_tokens"] == 50
    assert row["tool_count"] == 3
    assert row["run_id"] == "abc123"
    assert row["issue_number"] == 208
    assert row["stage"] == "unknown"
    assert row["persona"] == "unknown"
    assert "timestamp" in row


def test_build_ledger_row_carries_single_phase_stage():
    # intent="plan" is single-phase — the whole container run IS the plan phase,
    # so the caller passes the exact stage through and it must be preserved verbatim.
    row = mp.build_ledger_row(
        endpoint="/v1/messages", method="POST", model="m", status=200,
        duration_ms=1, input_tokens=1, output_tokens=1, cache_read_tokens=0,
        cache_creation_tokens=0, tool_count=0, tool_bytes=0, system_bytes=0,
        request_bytes=0, largest_tools=[], streamed=False,
        run_id="abc123", issue_number=208, intent="plan", stage="plan",
    )
    assert row["stage"] == "plan"


def test_build_ledger_row_defaults_when_correlation_missing():
    row = mp.build_ledger_row(
        endpoint="/v1/messages", method="POST", model="", status=502,
        duration_ms=10, input_tokens=0, output_tokens=0,
        cache_read_tokens=0, cache_creation_tokens=0, tool_count=0,
        tool_bytes=0, system_bytes=0, request_bytes=0, largest_tools=[],
        streamed=False, run_id=None, issue_number=None, intent=None, stage=None,
    )
    assert row["run_id"] == "unknown"
    assert row["issue_number"] == 0
    assert row["intent"] == "unknown"
    assert row["stage"] == "unknown"
