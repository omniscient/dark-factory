#!/usr/bin/env bash
# #642 request-shape check: the pinned CLI's request, through the real helper-proxy module, has
# the shape the local adapter accepts — tools == [], no output_config, no output_format, the
# served name, and the registry secret (never the placeholder) as the key. Runs in the
# docker-build CI job inside the built image with --network none (.github/workflows/ci.yml);
# it needs the baked /opt/dark-factory/scripts and the pinned claude, so it is not a test_*.sh.
# The capability suite's mock is the local endpoint (in-process), model_proxy --intent
# classify-preview-helper the proxy and preview_helper_entry.sh the helper (bare processes).
set -euo pipefail

getent hosts preview-helper-proxy | grep -q '^127\.0\.0\.1[[:space:]]' || {
  echo "FAIL: preview-helper-proxy does not resolve to 127.0.0.1 (docker run --add-host preview-helper-proxy:127.0.0.1)"; exit 1; }
[ -x /opt/dark-factory/scripts/preview_helper_entry.sh ] || {
  echo "FAIL: /opt/dark-factory/scripts/preview_helper_entry.sh is not baked executable"; exit 1; }
[ -w /var/lib/dark-factory ] || {
  echo "FAIL: /var/lib/dark-factory is not writable (docker run --tmpfs /var/lib/dark-factory:mode=1777)"; exit 1; }

export PYTHONPATH=/opt/dark-factory/scripts
exec timeout -k 5 240 python3 - <<'PY'
import asyncio
import json
import os
import socket
import sys
import time
from pathlib import Path

import yaml

from factory_core import preview_helper as ph
from factory_core.capability_suite import harness, mock_server
from factory_core.providers import model_endpoint as me

STATE = Path("/var/lib/dark-factory")
ENTRY = "/opt/dark-factory/scripts/preview_helper_entry.sh"
ENDPOINT_ID = "tier2-helper"
SECRET = "tier2-registry-secret-fixture"
PLACEHOLDER = "factory-local-placeholder"
RUN_ID = "0123456789abcdef0123456789abcdef"
PROXY_PORT = 8787


def fail(message):
    print(f"FAIL: {message}")
    sys.exit(1)


def stage_registry(base_url):
    """A staged registry with one tested local entry pointing at the mock; its digest."""
    root = STATE / "model-registry"
    entry = {
        "id": ENDPOINT_ID, "hosting_location": "local", "protocol": me.PROTOCOL,
        "accepts_payload_classes": ["local_only"],
        "model_identity": {"served_name": mock_server.SERVED_NAME,
                           "digest_or_revision": "sha256:" + "ef" * 32},
        "base_url": base_url, "secret_reference": "FACTORY_HELPER_MODEL_SECRET",
        "timeouts": {"connect_seconds": 10, "time_to_first_token_seconds": 60,
                     "idle_seconds": 60, "total_seconds": 120},
    }
    record = {
        "schema_version": "1.0", "endpoint_id": ENDPOINT_ID,
        "model_identity": dict(entry["model_identity"], quantisation="q4_k_m"),
        "runtime_stack": {"name": "capability-suite-mock", "version": "1.0.0",
                          "chat_template": "none", "context_settings": {}},
        "harness_version": harness.PINNED_HARNESS_VERSION,
        "fixture_suite_version": me.PINNED_FIXTURE_SUITE_VERSION,
        "results": {dim: "pass" for dim in me.ACTOR_REQUIRED_DIMENSIONS},
        "qualified_roles": ["actor"], "certified_by": "tier2-fixture",
        "certified_at": "2026-11-01T00:00:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00",
        "invalidated_by": ["model_digest", "harness_version", "fixture_suite_version",
                           "registry_config"],
        "registry_config": me.registry_config_sha256(entry),
    }
    (root / me.EVIDENCE_DIR).mkdir(parents=True)
    (root / me.REGISTRY_FILE).write_text(
        yaml.safe_dump({"schema_version": "1.0", "endpoints": [entry]}, sort_keys=False),
        encoding="utf-8")
    (root / me.EVIDENCE_DIR / f"{ENDPOINT_ID}.yaml").write_text(
        yaml.safe_dump({"capability_evidence": record}), encoding="utf-8")
    return me.tree_sha256(root)


async def wait_for_proxy(proc):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.returncode is not None:
            fail(f"the helper proxy exited {proc.returncode} before binding")
        try:
            socket.create_connection(("127.0.0.1", PROXY_PORT), timeout=0.5).close()
            return
        except OSError:
            await asyncio.sleep(0.2)
    fail("the helper proxy did not bind within 30 s")


async def main():
    if me.observed_harness_version() != harness.PINNED_HARNESS_VERSION:
        fail(f"claude is not the pinned {harness.PINNED_HARNESS_VERSION}")
    runner, base_url = await mock_server.start()
    try:
        observed = runner.app[mock_server.OBSERVED]
        digest = stage_registry(base_url)
        art = STATE / "preview-helper"
        art.mkdir()
        os.chmod(art, 0o777)
        (art / "input.json").write_text(json.dumps({
            "issue_number": 642, "head_sha": "1" * 40, "changed_files": ["docs/configuration.md"],
            "diff_stat": " docs/configuration.md | 2 +-", "title": "docs: tier2 fixture",
            "labels": ["documentation"]}), encoding="utf-8")
        ph.write_exclusive(art / "current-run.json", json.dumps({
            "run_id": RUN_ID, "issue_number": 642, "intent": ph.INTENT, "stage": ph.INTENT,
            "role": "actor"}))
        aliases = {alias: mock_server.SERVED_NAME for alias in me.TIER_ALIASES}
        base = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}
        proxy_env = {**base, "PYTHONPATH": "/opt/dark-factory/scripts",
                     "MODEL_PROXY_PORT": str(PROXY_PORT), "CURRENT_RUN_DIR": str(art),
                     "FACTORY_MODEL_ENDPOINT": ENDPOINT_ID, "FACTORY_MODEL_REGISTRY_SHA256": digest,
                     "ANTHROPIC_BASE_URL": base_url, **aliases,
                     "FACTORY_HELPER_MODEL_SECRET": SECRET, "SEQ_URL": "http://127.0.0.1:9"}
        # The helper gets exactly the compose service's nine keys, plus PATH and HOME.
        helper_env = {**base, "PYTHONPATH": "/opt/dark-factory/scripts",
                      "ANTHROPIC_BASE_URL": me.HELPER_PROXY_BASE_URL, **aliases,
                      "FACTORY_MODEL_ENDPOINT": ENDPOINT_ID,
                      "FACTORY_MODEL_REGISTRY_SHA256": digest, "CURRENT_RUN_DIR": str(art)}
        proxy = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "factory_core.model_proxy", "--intent", ph.INTENT, env=proxy_env)
        try:
            await wait_for_proxy(proxy)
            helper = await asyncio.create_subprocess_exec(
                ENTRY, "Classify preview (helper) #642", env=helper_env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(helper.communicate(), timeout=180)
        finally:
            proxy.terminate()
            await proxy.wait()
        print(out.decode(errors="replace"), err.decode(errors="replace"), sep="\n")
        if helper.returncode != 0:
            fail(f"the entry script exited {helper.returncode}")
        requests = list(observed.requests)
        # The adapter refuses a request carrying output_config/output_format (or any other
        # field a local destination does not support) with 400 before any outbound call, so
        # the mock never sees it; the ledger names the refusal (HEAD /api/hello is the pinned
        # CLI's own preflight, refused the same way and expected).
        ledger = STATE / "request-ledger.jsonl"
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()
                if line.strip()] if ledger.is_file() else []
        refused = [(r.get("method"), r.get("endpoint")) for r in rows
                   if r.get("run_id") == RUN_ID
                   and r.get("failure_class") == "protocol_incompatibility"
                   and (r.get("method"), r.get("endpoint")) != ("HEAD", "/api/hello")]
        if refused:
            fail(f"the adapter refused {refused} with protocol_incompatibility — the pinned CLI "
                 "sent a field a local destination does not accept (output_config/output_format?)")
        if not requests:
            fail("the endpoint saw no request")
        other = [(r["method"], r["path"]) for r in requests
                 if r["method"] != "POST" or r["path"].split("?", 1)[0] != "/v1/messages"]
        if other:
            fail(f"the endpoint saw requests other than POST /v1/messages: {other}")
        prompted = [r for r in requests if "<<<CHANGESET" in json.dumps(r["body"])]
        if not prompted:
            fail("no request carried the helper's prompt")
        for r in requests:
            body, headers = r["body"], r["headers"]
            for field in ("output_config", "output_format"):
                if field in body:
                    fail(f"a request carries {field}: {body[field]!r}")
            if body.get("model") != mock_server.SERVED_NAME:
                fail(f"a request names model {body.get('model')!r}, not the served name")
            if headers.get("x-api-key") != SECRET:
                fail("the endpoint did not receive the registry secret in x-api-key")
            if PLACEHOLDER in json.dumps(headers):
                fail("the placeholder credential reached the endpoint")
        for r in prompted:
            if r["body"].get("tools") != []:
                fail(f"the helper's request has tools={r['body'].get('tools')!r}, not []")
        print(f"PASS: {len(requests)} request(s): tools == [], no output_config or output_format, "
              f"model {mock_server.SERVED_NAME}, the registry secret in x-api-key")
        # The mock answers the text "ok", which is not the JSON object: a completed call that
        # the strict parse refuses, so result.json is abstained / schema_invalid.
        state, result = ph.read_result(art / "result.json")
        if (state, (result or {}).get("failure_class")) != ("abstained", "schema_invalid"):
            fail(f"result.json is {state} {result}, expected abstained schema_invalid")
        print("PASS: result.json written (abstained, schema_invalid: the mock's 'ok' is not the object)")
    finally:
        await runner.cleanup()


asyncio.run(main())
print("PASS: tier2_preview_helper_cli.sh")
PY
