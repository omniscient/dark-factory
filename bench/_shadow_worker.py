"""Hosting shadow worker (#563): one model call, one JSON object on stdout, no file written.

Spec: docs/superpowers/specs/2026-11-27-shadow-hosting-economics-comparator-design.md §5.
bench/shadow_runner.py spawns it as `python -I _shadow_worker.py <job.json>` with an
environment of exactly PATH, HOME, SHADOW_BASE_URL and the arm's one secret. It makes exactly
one non-streaming POST to $SHADOW_BASE_URL/v1/messages, never follows a redirect, never
retries, and classifies a failure with model_adapter's §4.6 helpers. It imports aiohttp, the
standard library and factory_core.providers.model_adapter — nothing from bench/.
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core.providers import model_adapter  # noqa: E402

ANTHROPIC_VERSION = "2023-06-01"
RESPONSE_TEXT_LIMIT = 2000
USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens")
OUTPUT_KEYS = ("http_status", "failure_class", "served_name_verified", "response_text",
               *(f"gen_ai.usage.{f}" for f in USAGE_FIELDS), "duration_ms", "started_at",
               "completed_at")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parsed(data):
    try:
        parsed = json.loads(data)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _text(message) -> str:
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    return "".join(b["text"] for b in content
                   if isinstance(b, dict) and b.get("type") == "text"
                   and isinstance(b.get("text"), str))


def _usage(message) -> dict:
    usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
    return {f"gen_ai.usage.{f}": usage[f] if type(usage.get(f)) is int else None
            for f in USAGE_FIELDS}


async def _post(url, secret, body, params):
    timeout = aiohttp.ClientTimeout(total=params["total_seconds"],
                                    connect=params["connect_seconds"])
    headers = {"x-api-key": secret, "anthropic-version": ANTHROPIC_VERSION}
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=body, headers=headers,
                                allow_redirects=False) as resp:
            return resp.status, await resp.read()


def run(job, base_url, secret) -> dict:
    params = job["request_params"]
    body = {"model": job["served_name"], "max_tokens": params["max_tokens"],
            "temperature": params["temperature"],
            "messages": [{"role": "user", "content": job["prompt"]}]}
    dest = {"endpoint_id": job["endpoint_id"], "served_name": job["served_name"]}
    out = dict.fromkeys(OUTPUT_KEYS)
    out["started_at"] = _now()
    started = time.monotonic()
    try:
        status, data = asyncio.run(_post(base_url + "/v1/messages", secret, body, params))
        out["duration_ms"] = round((time.monotonic() - started) * 1000)
        out["http_status"] = status
        model_adapter.check_response_status(status)
        if 200 <= status < 300:
            model_adapter.check_json_body(status, data)
            out["served_name_verified"] = False
            out["served_name_verified"] = model_adapter.check_response_model(dest, data,
                                                                             streamed=False)
            message = _parsed(data)
            text = _text(message)
            if secret:
                text = text.replace(secret, "[redacted]")
            out["response_text"] = text[:RESPONSE_TEXT_LIMIT]
            out.update(_usage(message))
        else:
            out["failure_class"] = model_adapter.status_failure_class(status)
    except (model_adapter.AdapterRefusal, aiohttp.ClientError, TimeoutError, OSError) as exc:
        if out["duration_ms"] is None:
            out["duration_ms"] = round((time.monotonic() - started) * 1000)
        out["failure_class"] = model_adapter.classify_failure(exc)
    out["completed_at"] = _now()
    return out


def main(argv) -> int:
    job = json.loads(Path(argv[1]).read_text())
    result = run(job, os.environ["SHADOW_BASE_URL"], os.environ[job["secret_env"]])
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
