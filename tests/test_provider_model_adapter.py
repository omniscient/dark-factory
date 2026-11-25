"""#558: the local-endpoint runtime adapter (providers/model_adapter.py).

Destinations are built from fixture entries through destination_from_entry(): a local entry
never comes back from load_verified_entry() while _NO_LOCAL_DISPATCH stands.
"""
import ast
import asyncio
import copy
import json
import sys
from pathlib import Path

import aiohttp
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from factory_core.providers import model_adapter as ma  # noqa: E402
from factory_core.providers import model_endpoint as me  # noqa: E402

SENTINEL = "sk-sentinel-local-gateway-token"
SERVED = "qwen2.5-coder-32b"
LOCAL = {
    "id": "local-test",
    "hosting_location": "local",
    "protocol": "anthropic-messages",
    "accepts_payload_classes": ["local_only"],
    "model_identity": {"served_name": SERVED, "digest_or_revision": "sha256:" + "ab" * 32},
    "base_url": "http://host.docker.internal:8080",
    "secret_reference": "LOCAL_GATEWAY_TOKEN",
    "timeouts": {"connect_seconds": 10, "time_to_first_token_seconds": 120,
                 "idle_seconds": 60, "total_seconds": 1800},
}
CLOUD = {
    "id": "cloud-test",
    "hosting_location": "cloud",
    "protocol": "anthropic-messages",
    "accepts_payload_classes": ["cloud_ok"],
    "model_identity": {"served_name": "claude-opus-5-5", "digest_or_revision": "claude-opus-5-5"},
    "secret_reference": "ANTHROPIC_API_KEY",
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (*me.CLOUD_CREDENTIALS, "MODEL_PROXY_UPSTREAM", "LOCAL_GATEWAY_TOKEN",
                 "FACTORY_MODEL_REGISTRY_SHA256", "FACTORY_MODEL_REGISTRY_PATH"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setenv("LOCAL_GATEWAY_TOKEN", SENTINEL)


def _entry(**overrides):
    entry = copy.deepcopy(LOCAL)
    entry.update(overrides)
    return entry


def _problems(entry):
    with pytest.raises(ma.DestinationError) as exc:
        ma.destination_from_entry(entry)
    return exc.value.problems


# --- Task 2: startup destination ---------------------------------------------------------

def test_local_destination_forwards_only_to_the_entry_base_url(secret):
    dest = ma.destination_from_entry(LOCAL)
    assert dest["upstream"] == LOCAL["base_url"]
    assert dest["hosting_location"] == "local" and dest["endpoint_id"] == "local-test"
    assert dest["served_name"] == SERVED
    assert dest["secret_reference"] == "LOCAL_GATEWAY_TOKEN"
    assert dict(dest["timeouts"]) == LOCAL["timeouts"]
    assert dict(dest["payload_decision"]) == me.payload_decision(None, LOCAL)


def test_destination_is_read_only(secret):
    dest = ma.destination_from_entry(LOCAL)
    with pytest.raises(TypeError):
        dest["upstream"] = "https://api.anthropic.com"


def test_destination_never_holds_the_secret_value(secret):
    assert SENTINEL not in repr(dict(ma.destination_from_entry(LOCAL)))


def test_matching_model_proxy_upstream_is_accepted(secret, monkeypatch):
    monkeypatch.setenv("MODEL_PROXY_UPSTREAM", LOCAL["base_url"] + "/")
    assert ma.destination_from_entry(LOCAL)["upstream"] == LOCAL["base_url"]


def test_differing_model_proxy_upstream_fails_startup(secret, monkeypatch):
    monkeypatch.setenv("MODEL_PROXY_UPSTREAM", "https://api.anthropic.com")
    problems = _problems(LOCAL)
    assert any("MODEL_PROXY_UPSTREAM" in p for p in problems)
    assert not any("api.anthropic.com" in p for p in problems)


@pytest.mark.parametrize("name", me.CLOUD_CREDENTIALS)
def test_any_cloud_credential_fails_a_local_startup(secret, monkeypatch, name):
    monkeypatch.setenv(name, "cloud-sentinel")
    problems = _problems(LOCAL)
    assert any(name in p for p in problems)
    assert "cloud-sentinel" not in " ".join(problems)


def test_missing_secret_reference_fails_startup():
    assert any("LOCAL_GATEWAY_TOKEN" in p and "not set" in p for p in _problems(LOCAL))


def test_payload_decision_refusal_fails_startup(secret):
    problems = _problems(_entry(accepts_payload_classes=["cloud_ok"]))
    assert any("payload decision" in p and "local_only" in p for p in problems)


def test_cloud_destination_keeps_the_cloud_upstream(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cloud-sentinel")
    dest = ma.destination_from_entry(CLOUD)
    assert dest["upstream"] is None and dest["hosting_location"] == "cloud"
    assert dest["payload_decision"]["payload_class"] == "cloud_ok"


def test_resolve_local_destination_surfaces_loader_problems(tmp_path):
    (tmp_path / me.REGISTRY_FILE).write_text("schema_version: '1.0'\nendpoints: []\n")
    with pytest.raises(ma.DestinationError) as exc:
        ma.resolve_local_destination(tmp_path, "local-test")
    assert "FACTORY_MODEL_REGISTRY_SHA256 is not set" in exc.value.problems[0]


def test_resolve_local_destination_builds_from_the_verified_entry(tmp_path, monkeypatch):
    seen = []

    def loader(path, eid):
        seen.append((path, eid))
        return [], copy.deepcopy(CLOUD)

    monkeypatch.setattr(me, "load_verified_entry", loader)
    assert ma.resolve_local_destination(tmp_path, "cloud-test")["endpoint_id"] == "cloud-test"
    assert seen == [(tmp_path, "cloud-test")]


def test_adapter_module_does_not_import_aiohttp():
    tree = ast.parse(Path(ma.__file__).read_text(encoding="utf-8"))
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)
                for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree)
                 if isinstance(n, ast.ImportFrom) and n.module}
    assert "aiohttp" not in imported
