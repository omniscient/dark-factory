#!/usr/bin/env python3
"""Dark factory model traffic proxy — transparent Anthropic passthrough + request ledger.

Generic reverse proxy for ANTHROPIC_BASE_URL: forwards every request to the real
Anthropic API unchanged (streaming responses forwarded byte-for-byte), while writing
a compact per-request ledger row and optional raw artifacts. See
docs/superpowers/specs/2026-07-11-transparent-model-proxy-design.md.
"""
import fcntl
import json
import os
import pathlib
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

LEDGER_PATH = pathlib.Path(
    os.environ.get("MODEL_PROXY_LEDGER_PATH", "/var/lib/dark-factory/request-ledger.jsonl")
)
CURRENT_RUN_PATH = pathlib.Path(
    os.environ.get("MODEL_PROXY_CURRENT_RUN_PATH", "/var/lib/dark-factory/current-run.json")
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
