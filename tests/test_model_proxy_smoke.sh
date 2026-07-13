#!/usr/bin/env bash
# Smoke test (#208 AC8): factory-model-proxy starts, proxies a request, and writes
# a ledger row — without touching a real Anthropic endpoint or the real
# /var/lib/dark-factory state dir.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH=$(mktemp -d /tmp/208-smoke-XXXXXX)
trap 'rm -rf "$SCRATCH"' EXIT

PYTHONPATH="$REPO_ROOT/scripts" python3 - "$SCRATCH" <<'PYEOF'
import asyncio
import json
import sys
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from factory_core import model_proxy as mp

scratch = Path(sys.argv[1])
mp.LEDGER_PATH = scratch / "request-ledger.jsonl"
mp.CURRENT_RUN_PATH = scratch / "current-run.json"
mp.RAW_ARTIFACT_DIR = scratch / "artifacts"
mp.post_seq_ledger = lambda row: None  # no live Seq in this smoke test

(scratch / "current-run.json").write_text(
    json.dumps({"run_id": "smoke-run", "issue_number": 208, "intent": "plan", "stage": "plan"})
)


async def upstream_handler(request):
    await request.json()
    return web.json_response({"usage": {"input_tokens": 7, "output_tokens": 3}}, status=200)


async def main():
    upstream_app = web.Application()
    upstream_app.router.add_route("*", "/{tail:.*}", upstream_handler)
    upstream = TestServer(upstream_app)
    await upstream.start_server()
    mp.UPSTREAM = f"http://{upstream.host}:{upstream.port}"

    app = mp.create_app()
    client = TestClient(TestServer(app))
    await client.start_server()

    resp = await client.post(
        "/v1/messages",
        json={"model": "claude-sonnet-4-6-20251101", "tools": [], "messages": []},
        headers={"x-api-key": "smoke-test-key"},
    )
    assert resp.status == 200, f"expected 200, got {resp.status}"
    body = await resp.json()
    assert body["usage"]["input_tokens"] == 7

    await client.close()
    await upstream.close()


asyncio.run(main())

ledger_lines = mp.LEDGER_PATH.read_text().strip().splitlines()
assert len(ledger_lines) == 1, f"expected 1 ledger row, got {len(ledger_lines)}"
row = json.loads(ledger_lines[0])
assert row["run_id"] == "smoke-run", row
assert row["issue_number"] == 208, row
assert row["stage"] == "plan", row
assert row["status"] == 200, row
print("SMOKE_OK")
PYEOF

echo "PASS: test_model_proxy_smoke.sh"
