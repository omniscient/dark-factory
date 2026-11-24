"""#557: model endpoint registry, capability evidence, staged-copy digest and endpoint preflight.

Mirrors tests/test_provider_model.py's pass/fail-per-field structure. Every test runs with
the cloud credentials, tier aliases and FACTORY_MODEL_* selection vars cleared — a run
container sets several of them, and they would leak into the local-selection checks.
"""
import copy
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from factory_core.providers import model_endpoint as me  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "model_registry"
SENTINEL = "sk-sentinel-value-never-printed"
DIGEST = "sha256:" + "ab" * 32

LOCAL = {
    "id": "local-test",
    "hosting_location": "local",
    "protocol": "anthropic-messages",
    "accepts_payload_classes": ["local_only"],
    "model_identity": {"served_name": "qwen2.5-coder-32b", "digest_or_revision": DIGEST},
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
    for name in (*me.CLOUD_CREDENTIALS, *me.TIER_ALIASES, "ANTHROPIC_BASE_URL",
                 "LOCAL_GATEWAY_TOKEN", "REMOTE_GATEWAY_TOKEN", "FACTORY_MODEL_ENDPOINT",
                 "FACTORY_MODEL_REGISTRY_SHA256", "FACTORY_MODEL_REGISTRY_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(me, "observed_harness_version", lambda: "2.1.282", raising=False)


def _entry(base, **overrides):
    entry = copy.deepcopy(base)
    for key, value in overrides.items():
        if value is None:
            entry.pop(key, None)
        else:
            entry[key] = value
    return entry


def _write_registry(root, endpoints, schema_version="1.0", **top):
    root.mkdir(parents=True, exist_ok=True)
    doc = {"schema_version": schema_version, "endpoints": endpoints, **top}
    (root / me.REGISTRY_FILE).write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return root


def _problems(tmp_path, endpoints, **kw):
    path = _write_registry(tmp_path / "reg", endpoints, **kw) / me.REGISTRY_FILE
    with pytest.raises(me.RegistryError) as exc:
        me.load_registry(path)
    return exc.value.problems


# --- Task 1: registry schema --------------------------------------------------------------

def test_fixture_registry_loads_same_host_lan_remote_and_cloud():
    endpoints = me.load_registry(FIXTURES / me.REGISTRY_FILE)
    assert [e["id"] for e in endpoints] == [
        "local-same-host", "local-lan", "local-remote", "cloud-anthropic"]


def test_valid_local_and_cloud_entries_load(tmp_path):
    path = _write_registry(tmp_path / "reg", [LOCAL, CLOUD]) / me.REGISTRY_FILE
    assert [e["id"] for e in me.load_registry(path)] == ["local-test", "cloud-test"]


def test_missing_registry_file_fails(tmp_path):
    with pytest.raises(me.RegistryError) as exc:
        me.load_registry(tmp_path / me.REGISTRY_FILE)
    assert "model endpoint registry not found" in exc.value.problems[0]


@pytest.mark.parametrize("field", ["id", "hosting_location", "protocol", "accepts_payload_classes",
                                   "model_identity", "secret_reference", "base_url", "timeouts"])
def test_local_entry_missing_any_required_field_fails(tmp_path, field):
    problems = _problems(tmp_path, [_entry(LOCAL, **{field: None})])
    assert any(field in p for p in problems), problems


@pytest.mark.parametrize("protocol", ["openai-chat", "", None])
def test_unsupported_protocol_fails(tmp_path, protocol):
    problems = _problems(tmp_path, [_entry(LOCAL, protocol=protocol)])
    assert any("unsupported protocol" in p for p in problems)


def test_schema_major_version_mismatch_is_a_hard_reject(tmp_path):
    problems = _problems(tmp_path, [LOCAL], schema_version="2.0")
    assert problems == [problems[0]] and "unsupported major version" in problems[0]


def test_unquoted_schema_version_fails(tmp_path):
    problems = _problems(tmp_path, [LOCAL], schema_version=1.0)
    assert "schema_version must be a quoted" in problems[0]


def test_unknown_key_rejected_at_supported_minor(tmp_path):
    problems = _problems(tmp_path, [_entry(LOCAL, timeout=5)])
    assert "endpoint 'local-test': unknown key 'timeout'" in problems


def test_unknown_key_tolerated_on_newer_minor(tmp_path):
    path = _write_registry(tmp_path / "reg", [_entry(LOCAL, future_field=1)],
                           schema_version="1.1") / me.REGISTRY_FILE
    assert me.load_registry(path)[0]["id"] == "local-test"


def test_duplicate_endpoint_ids_fail(tmp_path):
    problems = _problems(tmp_path, [LOCAL, LOCAL])
    assert "duplicate endpoint id 'local-test'" in problems


def test_duplicate_yaml_key_fails_instead_of_overwriting(tmp_path):
    (tmp_path / me.REGISTRY_FILE).write_text(
        'schema_version: "1.0"\nendpoints: []\nendpoints: []\n', encoding="utf-8")
    with pytest.raises(me.RegistryError) as exc:
        me.load_registry(tmp_path / me.REGISTRY_FILE)
    assert "duplicate key 'endpoints'" in exc.value.problems[0]


def test_cloud_entry_may_not_accept_local_only(tmp_path):
    problems = _problems(tmp_path, [_entry(CLOUD, accepts_payload_classes=["cloud_ok", "local_only"])])
    assert any("a cloud entry may never accept local_only" in p for p in problems)


def test_local_entry_must_accept_local_only(tmp_path):
    problems = _problems(tmp_path, [_entry(LOCAL, accepts_payload_classes=["cloud_ok"])])
    assert any("a local entry must accept local_only" in p for p in problems)


@pytest.mark.parametrize("classes", [[], ["secret"], ["local_only", "local_only"], "local_only"])
def test_invalid_accepts_payload_classes_fail(tmp_path, classes):
    problems = _problems(tmp_path, [_entry(LOCAL, accepts_payload_classes=classes)])
    assert any("accepts_payload_classes must be a non-empty list" in p for p in problems)


def test_cloud_entry_with_timeouts_fails_explicitly(tmp_path):
    problems = _problems(tmp_path, [_entry(CLOUD, timeouts=LOCAL["timeouts"])])
    assert "endpoint 'cloud-test': timeouts are not enforced for cloud entries — §4.6" in problems


def test_cloud_entry_with_base_url_fails(tmp_path):
    problems = _problems(tmp_path, [_entry(CLOUD, base_url="https://api.anthropic.com")])
    assert any("base_url is not allowed on a cloud entry" in p for p in problems)


@pytest.mark.parametrize("field", me.TIMEOUT_FIELDS)
@pytest.mark.parametrize("value", [0, -1, "10", True])
def test_local_timeouts_must_be_positive_numbers(tmp_path, field, value):
    timeouts = dict(LOCAL["timeouts"], **{field: value})
    problems = _problems(tmp_path, [_entry(LOCAL, timeouts=timeouts)])
    assert f"endpoint 'local-test': timeouts.{field} must be a positive number" in problems


def test_local_timeout_may_not_exceed_total(tmp_path):
    timeouts = dict(LOCAL["timeouts"], idle_seconds=4000)
    problems = _problems(tmp_path, [_entry(LOCAL, timeouts=timeouts)])
    assert problems == ["endpoint 'local-test': timeouts.idle_seconds must not exceed timeouts.total_seconds"]


@pytest.mark.parametrize("url,message", [
    ("not a url", "must be an http(s) URL with a host"),
    ("ftp://host:21", "must be an http(s) URL with a host"),
    ("http://host:99999", "is not a well-formed URL"),
    ("http://user:pw@host:8080", "must not embed credentials"),
    ("http://host:8080/?token=x", "must not carry a query string"),
])
def test_local_base_url_must_be_well_formed(tmp_path, url, message):
    problems = _problems(tmp_path, [_entry(LOCAL, base_url=url)])
    assert any(message in p for p in problems), problems
    assert not any(url in p for p in problems)


def test_secret_reference_value_is_rejected_and_never_echoed(tmp_path):
    problems = _problems(tmp_path, [_entry(LOCAL, secret_reference=SENTINEL)])
    assert any("secret_reference must be an env-var name" in p for p in problems)
    assert SENTINEL not in " ".join(problems)


@pytest.mark.parametrize("name", me.CLOUD_CREDENTIALS)
def test_local_entry_may_not_reference_a_cloud_credential(tmp_path, name):
    problems = _problems(tmp_path, [_entry(LOCAL, secret_reference=name)])
    assert any(f"may not name the cloud credential {name}" in p for p in problems)


@pytest.mark.parametrize("revision", ["latest", "Main", "qwen:latest", "", "two words"])
def test_mutable_model_revision_fails(tmp_path, revision):
    identity = dict(LOCAL["model_identity"], digest_or_revision=revision)
    problems = _problems(tmp_path, [_entry(LOCAL, model_identity=identity)])
    assert any("no 'latest'-style alias" in p for p in problems)


def test_loader_does_not_require_referenced_secrets_to_exist(tmp_path):
    # Parse time checks the name only; existence is preflight's, for the selected entry.
    assert "LOCAL_GATEWAY_TOKEN" not in os.environ and "ANTHROPIC_API_KEY" not in os.environ
    path = _write_registry(tmp_path / "reg", [LOCAL, CLOUD]) / me.REGISTRY_FILE
    assert len(me.load_registry(path)) == 2
