"""#557: model endpoint registry, capability evidence, staged-copy digest and endpoint preflight.

Mirrors tests/test_provider_model.py's pass/fail-per-field structure. Every test runs with
the cloud credentials, tier aliases and FACTORY_MODEL_* selection vars cleared — a run
container sets several of them, and they would leak into the local-selection checks.
"""
import copy
import io
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


# --- Task 2: staged-tree digest and staging -----------------------------------------------

def test_tree_sha256_is_stable_and_content_sensitive(tmp_path):
    reg = _write_registry(tmp_path / "reg", [LOCAL])
    first = me.tree_sha256(reg)
    assert first == me.tree_sha256(reg) and len(first) == 64
    (reg / "capability-evidence").mkdir()
    (reg / "capability-evidence" / "local-test.yaml").write_text("x: 1\n", encoding="utf-8")
    assert me.tree_sha256(reg) != first


def test_tree_sha256_rejects_symlinks(tmp_path):
    reg = _write_registry(tmp_path / "reg", [LOCAL])
    (reg / "link.yaml").symlink_to(reg / me.REGISTRY_FILE)
    with pytest.raises(me.RegistryError, match="not a regular file"):
        me.tree_sha256(reg)


def test_tree_sha256_fails_closed_on_an_oversized_file(tmp_path):
    # The staged tree is writable by run containers and is read into memory: cap each file.
    reg = _write_registry(tmp_path / "reg", [LOCAL])
    (reg / "big.yaml").write_bytes(b"#" * (me.MAX_FILE_BYTES + 1))
    with pytest.raises(me.RegistryError) as exc:
        me.tree_sha256(reg)
    assert exc.value.problems == [f"{reg}: big.yaml is larger than {me.MAX_FILE_BYTES} bytes"]


def test_stage_copies_tree_and_returns_its_digest(tmp_path):
    src = _write_registry(tmp_path / "src", [LOCAL, CLOUD])
    dest = tmp_path / "state" / "model-registry"
    digest = me.stage_registry(src, dest)
    assert digest == me.tree_sha256(src) == me.tree_sha256(dest)
    assert (dest / me.REGISTRY_FILE).read_bytes() == (src / me.REGISTRY_FILE).read_bytes()
    assert sorted(p.name for p in dest.parent.iterdir()) == ["model-registry"]


def test_stage_replaces_previous_copy(tmp_path):
    dest = tmp_path / "state" / "model-registry"
    dest.mkdir(parents=True)
    (dest / "planted.yaml").write_text("tampered: true\n", encoding="utf-8")
    me.stage_registry(_write_registry(tmp_path / "src", [CLOUD]), dest)
    assert not (dest / "planted.yaml").exists()
    assert sorted(p.name for p in dest.parent.iterdir()) == ["model-registry"]


def test_stage_refuses_invalid_registry_and_keeps_previous_copy(tmp_path):
    dest = tmp_path / "state" / "model-registry"
    good = me.stage_registry(_write_registry(tmp_path / "good", [CLOUD]), dest)
    with pytest.raises(me.RegistryError):
        me.stage_registry(_write_registry(tmp_path / "bad", [_entry(CLOUD, protocol="openai")]), dest)
    assert me.tree_sha256(dest) == good


def test_stage_copy_failure_leaves_no_partial_tree(tmp_path, monkeypatch):
    dest = tmp_path / "state" / "model-registry"
    good = me.stage_registry(_write_registry(tmp_path / "good", [CLOUD]), dest)

    def _half_copy(src, dst, **kw):
        (Path(dst) / "half.yaml").write_text("partial\n", encoding="utf-8")
        raise OSError("disk full")
    monkeypatch.setattr(me.shutil, "copytree", _half_copy)
    with pytest.raises(OSError):
        me.stage_registry(_write_registry(tmp_path / "next", [LOCAL]), dest)
    assert me.tree_sha256(dest) == good
    assert sorted(p.name for p in dest.parent.iterdir()) == ["model-registry"]


def test_stage_fails_when_the_staged_copy_changes_mid_staging(tmp_path, monkeypatch):
    # #593 defect 1: a run container shares the volume and can write the temp copy between
    # copytree and the digest. Staging must fail rather than bless the tampered tree.
    dest = tmp_path / "state" / "model-registry"
    good = me.stage_registry(_write_registry(tmp_path / "good", [LOCAL]), dest)
    src = _write_registry(tmp_path / "src", [CLOUD])
    real_copytree = me.shutil.copytree

    def _copy_then_inject(source, target, **kw):
        result = real_copytree(source, target, **kw)
        _write_registry(Path(target), [CLOUD, _entry(CLOUD, id="cloud-injected")])
        return result
    monkeypatch.setattr(me.shutil, "copytree", _copy_then_inject)
    with pytest.raises(me.RegistryError, match="does not match the read-only source digest"):
        me.stage_registry(src, dest)
    assert me.tree_sha256(dest) == good
    assert sorted(p.name for p in dest.parent.iterdir()) == ["model-registry"]


def test_stage_skips_the_swap_when_unchanged(tmp_path):
    # Pins bd6be81's fast path: an unchanged source never replaces dest, so a concurrently
    # starting run never sees it missing.
    src = _write_registry(tmp_path / "src", [CLOUD])
    dest = tmp_path / "state" / "model-registry"
    first = me.stage_registry(src, dest)
    before = os.stat(dest).st_ino
    assert me.stage_registry(src, dest) == first == me.tree_sha256(src)
    assert os.stat(dest).st_ino == before
    assert not list(dest.parent.glob(f".{dest.name}.tmp-*"))


def test_staged_registry_dir_default_and_test_override(monkeypatch, tmp_path):
    assert me.staged_registry_dir() == Path("/var/lib/dark-factory/model-registry")
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_PATH", str(tmp_path))
    assert me.staged_registry_dir() == tmp_path


def test_read_verified_tree_requires_the_digest(tmp_path):
    reg = _write_registry(tmp_path / "reg", [CLOUD])
    problems, files = me.read_verified_tree(reg)
    assert "FACTORY_MODEL_REGISTRY_SHA256 is not set" in problems[0] and files is None


def test_read_verified_tree_rejects_malformed_digest(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", "not-hex")
    reg = _write_registry(tmp_path / "reg", [CLOUD])
    assert me.read_verified_tree(reg) == (
        ["FACTORY_MODEL_REGISTRY_SHA256 is not a 64-hex SHA-256 digest"], None)


def test_read_verified_tree_missing_dir_never_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", "0" * 64)
    problems, files = me.read_verified_tree(tmp_path / "absent")
    assert "no fallback to cloud" in problems[0] and files is None


def test_read_verified_tree_detects_tampering(tmp_path, monkeypatch):
    reg = _write_registry(tmp_path / "reg", [CLOUD])
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", me.tree_sha256(reg))
    assert me.read_verified_tree(reg) == ([], {me.REGISTRY_FILE: (reg / me.REGISTRY_FILE).read_bytes()})
    _write_registry(reg, [_entry(CLOUD, accepts_payload_classes=["cloud_ok"], id="cloud-evil")])
    problems, files = me.read_verified_tree(reg)
    assert "does not match FACTORY_MODEL_REGISTRY_SHA256" in problems[0] and files is None


def test_read_verified_tree_fails_closed_on_an_oversized_file(tmp_path, monkeypatch):
    reg = _write_registry(tmp_path / "reg", [CLOUD])
    (reg / "big.yaml").write_bytes(b"#" * (me.MAX_FILE_BYTES + 1))
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", "0" * 64)
    problems, files = me.read_verified_tree(reg)
    assert problems == [f"{reg}: big.yaml is larger than {me.MAX_FILE_BYTES} bytes"] and files is None


# --- Task 3: capability evidence ------------------------------------------------------------

def _record(entry, **overrides):
    record = {
        "schema_version": "1.0",
        "endpoint_id": entry["id"],
        "model_identity": dict(entry["model_identity"], quantisation="q4_k_m"),
        "runtime_stack": {"name": "rt", "version": "1.0.0", "chat_template": "t",
                          "context_settings": {}},
        "harness_version": "2.1.282",
        "fixture_suite_version": "suite-1",
        "results": {dim: "pass" for dim in me.ACTOR_REQUIRED_DIMENSIONS},
        "qualified_roles": ["actor"],
        "certified_by": "operator",
        "certified_at": "2026-11-01T00:00:00+00:00",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "invalidated_by": ["model_digest", "harness_version", "fixture_suite_version",
                           "registry_config"],
        "registry_config": me.registry_config_sha256(entry),
    }
    record.update(overrides)
    return record


@pytest.fixture
def pinned_suite(monkeypatch):
    monkeypatch.setattr(me, "PINNED_FIXTURE_SUITE_VERSION", "suite-1")


def test_no_record_is_unknown():
    assert me.evidence_state(None, LOCAL)[0] == "unknown"


def test_fixture_evidence_matches_fixture_entry_but_stays_unknown():
    entry = me.load_registry(FIXTURES / me.REGISTRY_FILE)[0]
    record = me.load_evidence(FIXTURES / me.EVIDENCE_DIR, "local-same-host")
    assert record["registry_config"] == me.registry_config_sha256(entry)
    state, reasons = me.evidence_state(record, entry)
    assert state == "unknown"
    assert "quantisation" in reasons[0] and "fixture_suite_version" in reasons[0]


def test_missing_evidence_file_loads_as_none(tmp_path):
    assert me.load_evidence(tmp_path, "local-test") is None


def test_evidence_without_wrapper_is_rejected(tmp_path):
    (tmp_path / "local-test.yaml").write_text("endpoint_id: local-test\n", encoding="utf-8")
    with pytest.raises(me.RegistryError, match="exactly one capability_evidence mapping"):
        me.load_evidence(tmp_path, "local-test")


def test_record_missing_certified_by_is_declared(pinned_suite):
    record = _record(LOCAL)
    del record["certified_by"]
    state, reasons = me.evidence_state(record, LOCAL)
    assert state == "declared" and any("missing certified_by" in r for r in reasons)


@pytest.mark.parametrize("overrides", [
    {"endpoint_id": "other"}, {"qualified_roles": ["checker"]}, {"results": {"streaming": "ok"}},
    {"invalidated_by": ["weather"]}, {"expires_at": "2099-01-01"}, {"schema_version": "2.0"},
    {"surprise": 1},
])
def test_invalid_record_is_declared(pinned_suite, overrides):
    assert me.evidence_state(_record(LOCAL, **overrides), LOCAL)[0] == "declared"


def test_matching_record_is_tested(pinned_suite):
    assert me.evidence_state(_record(LOCAL), LOCAL) == ("tested", [])


def test_yaml_datetime_timestamps_are_accepted(pinned_suite):
    record = _record(LOCAL, expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc))
    assert me.evidence_state(record, LOCAL)[0] == "tested"


def test_expired_record_is_stale(pinned_suite):
    state, reasons = me.evidence_state(_record(LOCAL, expires_at="2020-01-01T00:00:00+00:00"), LOCAL)
    assert state == "stale" and "expires_at has passed" in reasons[0]


@pytest.mark.parametrize("change", ["model_digest", "registry_config", "harness_version"])
def test_fingerprint_mismatch_is_stale(pinned_suite, monkeypatch, change):
    record = _record(LOCAL)
    if change == "model_digest":
        record["model_identity"]["digest_or_revision"] = "sha256:" + "cd" * 32
    elif change == "registry_config":
        record["registry_config"] = "0" * 64
    else:
        monkeypatch.setattr(me, "observed_harness_version", lambda: "2.1.300")
    state, reasons = me.evidence_state(record, LOCAL)
    assert state == "stale" and f"{change} differs" in reasons[0]


@pytest.mark.parametrize("name", ["quantisation", "runtime_version", "chat_template", "context_settings"])
def test_unresolvable_fingerprint_is_unknown(pinned_suite, name):
    record = _record(LOCAL, invalidated_by=["model_digest", name])
    state, reasons = me.evidence_state(record, LOCAL)
    assert state == "unknown" and name in reasons[0]


def test_unpinned_fixture_suite_is_unknown():
    state, reasons = me.evidence_state(_record(LOCAL, invalidated_by=["model_digest"]), LOCAL)
    assert state == "unknown" and "fixture_suite_version" in reasons[0]


def test_unreadable_harness_version_is_unknown(pinned_suite, monkeypatch):
    monkeypatch.setattr(me, "observed_harness_version", lambda: None)
    assert me.evidence_state(_record(LOCAL), LOCAL)[0] == "unknown"


def test_role_requires_every_dimension_to_pass():
    record = _record(LOCAL, results=dict(_record(LOCAL)["results"], streaming="not_tested"))
    assert me.role_problems(record, "actor") == [
        "capability evidence for 'local-test': streaming is not_tested, not pass"]
    assert me.role_problems(_record(LOCAL), "actor") == []


def test_observed_harness_version_is_none_without_cli(monkeypatch):
    monkeypatch.undo()
    def _no_cli(*a, **k):
        raise FileNotFoundError("claude")
    monkeypatch.setattr(me.subprocess, "run", _no_cli)
    assert me.observed_harness_version() is None


# --- Task 4: endpoint preflight -------------------------------------------------------------

def _stage(tmp_path, monkeypatch, endpoints, evidence=None):
    """Write a registry (+ evidence) as the staged copy and pass its digest like the scheduler."""
    reg = _write_registry(tmp_path / "staged", endpoints)
    for eid, record in (evidence or {}).items():
        (reg / me.EVIDENCE_DIR).mkdir(exist_ok=True)
        (reg / me.EVIDENCE_DIR / f"{eid}.yaml").write_text(
            yaml.safe_dump({"capability_evidence": record}), encoding="utf-8")
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", me.tree_sha256(reg))
    return reg


def _local_env(monkeypatch, entry=LOCAL):
    monkeypatch.setenv("LOCAL_GATEWAY_TOKEN", SENTINEL)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", entry["base_url"])
    for alias in me.TIER_ALIASES:
        monkeypatch.setenv(alias, entry["model_identity"]["served_name"])


def _run_preflight(reg, endpoint_id, hosting):
    """me.preflight()'s problems, after asserting the hosting it returned with them."""
    problems, got = me.preflight(reg, endpoint_id)
    assert got == hosting
    return problems


def test_cloud_selection_passes_with_secret_and_digest(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [CLOUD])
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    assert _run_preflight(reg, "cloud-test", "cloud") == []


def test_cloud_selection_needs_no_evidence_but_names_missing_secret(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [CLOUD])
    assert _run_preflight(reg, "cloud-test", "cloud") == ["ANTHROPIC_API_KEY is not set. Add it to .archon/.env"]


def test_unknown_endpoint_fails_without_choosing_another(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [CLOUD])
    problems = _run_preflight(reg, "cloud-nope", None)
    assert len(problems) == 1 and "Unknown FACTORY_MODEL_ENDPOINT 'cloud-nope'" in problems[0]
    assert "no other endpoint is chosen instead" in problems[0]


def test_preflight_without_digest_fails_before_reading_registry(tmp_path, monkeypatch):
    reg = _write_registry(tmp_path / "staged", [CLOUD])
    assert "FACTORY_MODEL_REGISTRY_SHA256 is not set" in _run_preflight(reg, "cloud-test", None)[0]


def test_preflight_fails_on_tampered_staged_copy(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [CLOUD])
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    _write_registry(reg, [CLOUD, _entry(CLOUD, id="cloud-extra")])
    problems = _run_preflight(reg, "cloud-test", None)
    assert len(problems) == 1 and "does not match FACTORY_MODEL_REGISTRY_SHA256" in problems[0]


def test_preflight_without_registry_file_reports_it_not_found(tmp_path, monkeypatch):
    reg = tmp_path / "staged"
    (reg / me.EVIDENCE_DIR).mkdir(parents=True)
    (reg / me.EVIDENCE_DIR / "cloud-test.yaml").write_text("x: 1\n", encoding="utf-8")
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", me.tree_sha256(reg))
    assert _run_preflight(reg, "cloud-test", None) == [
        f"model endpoint registry not found at {reg / me.REGISTRY_FILE}"]


def test_preflight_reports_schema_violations(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [_entry(CLOUD, protocol="openai-chat")])
    assert any("unsupported protocol" in p for p in _run_preflight(reg, "cloud-test", None))


def test_local_selection_missing_secret_names_it_without_archon_env_hint(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [LOCAL])
    problems = _run_preflight(reg, "local-test", "local")
    assert "LOCAL_GATEWAY_TOKEN (secret_reference of endpoint 'local-test') is not set" in problems
    assert not any(".archon/.env" in p for p in problems)


@pytest.mark.parametrize("name", me.CLOUD_CREDENTIALS)
def test_local_selection_fails_when_a_cloud_credential_is_present(tmp_path, monkeypatch, name):
    reg = _stage(tmp_path, monkeypatch, [LOCAL])
    _local_env(monkeypatch)
    monkeypatch.setenv(name, SENTINEL + "-cloud")
    problems = _run_preflight(reg, "local-test", "local")
    assert any(f"cloud model credential(s) are set in this container: {name}" in p for p in problems)
    assert SENTINEL not in " ".join(problems)


def test_local_selection_requires_matching_base_url(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [LOCAL])
    _local_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    assert any("ANTHROPIC_BASE_URL must equal the entry's base_url" in p
               for p in _run_preflight(reg, "local-test", "local"))


@pytest.mark.parametrize("alias", me.TIER_ALIASES)
def test_local_selection_requires_every_tier_alias_to_be_the_served_model(tmp_path, monkeypatch, alias):
    reg = _stage(tmp_path, monkeypatch, [LOCAL])
    _local_env(monkeypatch)
    monkeypatch.setenv(alias, "claude-opus-5-5")
    assert any(f"{alias} must equal model_identity.served_name" in p
               for p in _run_preflight(reg, "local-test", "local"))


def test_local_selection_requires_evidence(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [LOCAL])
    _local_env(monkeypatch)
    problems = _run_preflight(reg, "local-test", "local")
    assert "endpoint 'local-test': capability evidence is 'unknown', not 'tested' (contract §4.5)" in problems


def test_local_selection_always_fails_as_an_existing_phase(tmp_path, monkeypatch, pinned_suite):
    reg = _stage(tmp_path, monkeypatch, [LOCAL], evidence={"local-test": _record(LOCAL)})
    _local_env(monkeypatch)
    assert _run_preflight(reg, "local-test", "local") == [
        "endpoint 'local-test' is local, but no local-eligible dispatch exists yet — every "
        "current dispatch runs a gated phase (contract §4.11 decision 3; the first helper "
        "dispatch is #587)"]


def test_local_selection_with_failed_dimension_reports_it(tmp_path, monkeypatch, pinned_suite):
    record = _record(LOCAL, results=dict(_record(LOCAL)["results"], tool_call_fidelity="fail"))
    reg = _stage(tmp_path, monkeypatch, [LOCAL], evidence={"local-test": record})
    _local_env(monkeypatch)
    assert "capability evidence for 'local-test': tool_call_fidelity is fail, not pass" in \
        _run_preflight(reg, "local-test", "local")


def test_preflight_output_never_contains_secret_values(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [LOCAL, CLOUD])
    _local_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    for eid, hosting in (("local-test", "local"), ("cloud-test", "cloud")):
        assert SENTINEL not in " ".join(_run_preflight(reg, eid, hosting))


def _serve_first_read(monkeypatch, root, tampered):
    """Swap-and-swap-back (#593): each file under root reads as its real bytes on its first
    read and as tampered[rel] on every later read. Returns {rel: read count}.

    Hooks both read primitives this module has used (open() and Path.read_bytes), so a
    re-read is observed whichever primitive performs it.
    """
    root = Path(root).resolve()
    real_open, real_read_bytes = open, Path.read_bytes
    reads = {}

    def _serve(path):
        try:
            rel = Path(path).resolve().relative_to(root).as_posix()
        except ValueError:
            return None
        reads[rel] = reads.get(rel, 0) + 1
        if reads[rel] > 1 and rel in tampered:
            return tampered[rel]
        return real_read_bytes(Path(path))

    def _open(file, mode="r", *args, **kwargs):
        data = _serve(file) if isinstance(file, (str, os.PathLike)) else None
        if data is None:
            return real_open(file, mode, *args, **kwargs)
        return io.BytesIO(data) if "b" in mode else io.StringIO(data.decode("utf-8"))

    def _read_bytes(self):
        data = _serve(self)
        return real_read_bytes(self) if data is None else data

    monkeypatch.setattr(me, "open", _open, raising=False)
    monkeypatch.setattr(Path, "read_bytes", _read_bytes)
    return reads


def _registry_bytes(endpoints):
    return yaml.safe_dump({"schema_version": "1.0", "endpoints": endpoints},
                          sort_keys=False).encode("utf-8")


def test_preflight_uses_only_the_first_read_of_each_staged_file(tmp_path, monkeypatch, pinned_suite):
    reg = _stage(tmp_path, monkeypatch, [LOCAL], evidence={"local-test": _record(LOCAL)})
    _local_env(monkeypatch)
    evidence = f"{me.EVIDENCE_DIR}/local-test.yaml"
    reads = _serve_first_read(monkeypatch, reg, {
        me.REGISTRY_FILE: _registry_bytes([_entry(CLOUD, id="local-test")]),
        evidence: yaml.safe_dump({"capability_evidence": _record(
            LOCAL, results=dict(_record(LOCAL)["results"], streaming="fail"))}).encode("utf-8"),
    })
    assert me.preflight(reg, "local-test") == ([me._NO_LOCAL_DISPATCH.format(eid="local-test")], "local")
    assert reads == {me.REGISTRY_FILE: 1, evidence: 1}


def test_swap_and_swap_back_cannot_change_the_endpoint_decision(base_env, tmp_path, monkeypatch):
    # Through the public, unchanged providers.preflight(): on main the later reads see the
    # swapped-in local entry and preflight fails on them. After #593 there are no later reads.
    from factory_core import providers
    reg = _stage(tmp_path, monkeypatch, [CLOUD])
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_PATH", str(reg))
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "cloud-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    reads = _serve_first_read(monkeypatch, reg, {
        me.REGISTRY_FILE: _registry_bytes([_entry(LOCAL, id="cloud-test")])})
    assert providers.preflight() == []
    assert reads == {me.REGISTRY_FILE: 1}


def test_scheduler_preflight_checks_schema_and_id_only(tmp_path):
    src = _write_registry(tmp_path / "src", [CLOUD, LOCAL])
    assert me.scheduler_preflight(src, "cloud-test") == ([], "cloud")
    problems, hosting = me.scheduler_preflight(src, "nope")
    assert "Unknown FACTORY_MODEL_ENDPOINT 'nope'" in problems[0] and hosting is None


def test_scheduler_preflight_blocks_a_local_selection(tmp_path):
    src = _write_registry(tmp_path / "src", [LOCAL])
    assert me.scheduler_preflight(src, "local-test") == (
        [me._NO_LOCAL_DISPATCH.format(eid="local-test")], "local")


def test_scheduler_preflight_without_a_registry_has_no_hosting(tmp_path):
    problems, hosting = me.scheduler_preflight(tmp_path / "absent", "local-test")
    assert "model endpoint registry not found" in problems[0] and hosting is None


# --- Task 5: payload class predicate and redacted view --------------------------------------

@pytest.mark.parametrize("declared,hosting,expected", [
    (None, "local", "local_only"), ("cloud_ok", "local", "local_only"),
    ("local_only", "local", "local_only"), (None, "cloud", "cloud_ok"),
    ("cloud_ok", "cloud", "cloud_ok"), ("local_only", "cloud", "local_only"),
])
def test_effective_payload_class(declared, hosting, expected):
    assert me.effective_payload_class(declared, hosting) == expected


@pytest.mark.parametrize("declared,hosting", [("public", "cloud"), ("public", "local"), (None, "edge")])
def test_effective_payload_class_rejects_unknown_values(declared, hosting):
    with pytest.raises(ValueError):
        me.effective_payload_class(declared, hosting)


def test_dispatch_allowed():
    assert me.dispatch_allowed("cloud_ok", ["cloud_ok"])
    assert not me.dispatch_allowed("local_only", ["cloud_ok"])


def test_payload_decision_allows_unmarked_cloud_dispatch():
    assert me.payload_decision(None, CLOUD) == {
        "payload_class": "cloud_ok", "endpoint_id": "cloud-test",
        "secret_reference": "ANTHROPIC_API_KEY", "allow": True,
        "reason": "effective class accepted by endpoint"}


def test_payload_decision_blocks_local_only_payload_on_cloud():
    decision = me.payload_decision("local_only", CLOUD)
    assert decision["allow"] is False and decision["payload_class"] == "local_only"


@pytest.mark.parametrize("declared,entry", [
    ("public", CLOUD),
    (None, _entry(CLOUD, hosting_location=None)),
    (None, _entry(CLOUD, accepts_payload_classes=None)),
    (None, _entry(CLOUD, accepts_payload_classes=["secret"])),
])
def test_payload_decision_blocks_invalid_inputs(declared, entry):
    decision = me.payload_decision(declared, entry)
    assert decision["allow"] is False and decision["reason"]


@pytest.mark.parametrize("url,scope", [
    ("http://localhost:8080", "same-host"), ("http://127.0.0.1:8080", "same-host"),
    ("http://host.docker.internal:8080", "same-host"), ("http://192.168.1.50:8080", "lan"),
    ("http://10.0.0.5", "lan"), ("http://gpu-box.local:8080", "lan"), ("http://ollama:11434", "lan"),
    ("https://gpu.example.com", "remote"), ("http://8.8.8.8", "remote"),
])
def test_base_url_scope(url, scope):
    assert me.base_url_scope(url) == scope


def test_redacted_view_reduces_secret_reference_to_name_and_presence(monkeypatch):
    monkeypatch.setenv("LOCAL_GATEWAY_TOKEN", SENTINEL)
    view = me.redacted_view(FIXTURES)
    by_id = {e["id"]: e for e in view["endpoints"]}
    assert by_id["local-same-host"]["secret_reference"] == {"name": "LOCAL_GATEWAY_TOKEN", "present": True}
    assert by_id["local-remote"]["secret_reference"] == {"name": "REMOTE_GATEWAY_TOKEN", "present": False}
    assert [by_id[i].get("base_url_scope") for i in ("local-same-host", "local-lan", "local-remote", "cloud-anthropic")] == [
        "same-host", "lan", "remote", None]
    assert SENTINEL not in repr(view)


def test_redacted_view_staged_matches_the_source_view(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [LOCAL, CLOUD])
    monkeypatch.setenv("LOCAL_GATEWAY_TOKEN", SENTINEL)
    assert me.redacted_view_staged(reg) == ([], me.redacted_view(reg))


def test_redacted_view_staged_refuses_an_unverified_copy(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [CLOUD])
    _write_registry(reg, [CLOUD, _entry(CLOUD, id="cloud-extra")])
    problems, view = me.redacted_view_staged(reg)
    assert "does not match FACTORY_MODEL_REGISTRY_SHA256" in problems[0] and view is None


# --- Task 6: providers.preflight() wiring ---------------------------------------------------

@pytest.fixture
def base_env(monkeypatch):
    """Tracker/code-host/cloud credentials present, so only endpoint problems remain."""
    monkeypatch.setenv("GH_TOKEN", "x")
    for name in ("FACTORY_TRACKER", "FACTORY_CODEHOST", "FACTORY_MODEL_PROVIDER"):
        monkeypatch.delenv(name, raising=False)


def test_default_path_never_loads_the_registry_module(base_env, monkeypatch):
    from factory_core import providers
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "x")
    monkeypatch.delitem(sys.modules, "factory_core.providers.model_endpoint")
    assert providers.preflight() == [] and providers.preflight(side="scheduler") == []
    assert "factory_core.providers.model_endpoint" not in sys.modules


def test_default_path_reports_the_same_cloud_problems_as_before(base_env):
    from factory_core import providers
    from factory_core.providers import model
    assert providers.preflight() == model.preflight("anthropic")


def test_digest_without_endpoint_selection_fails(base_env, monkeypatch):
    from factory_core import providers
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "x")
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", "0" * 64)
    assert providers.preflight() == [
        "FACTORY_MODEL_REGISTRY_SHA256 is set but FACTORY_MODEL_ENDPOINT is not — a registry "
        "digest without an endpoint selection is refused"]


def test_run_side_cloud_selection_adds_endpoint_checks_to_cloud_check(base_env, tmp_path, monkeypatch):
    from factory_core import providers
    reg = _stage(tmp_path, monkeypatch, [CLOUD])
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_PATH", str(reg))
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "cloud-test")
    assert providers.preflight() == [
        "ANTHROPIC_API_KEY is not set. Add it to .archon/.env",
        "Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .archon/.env"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    assert providers.preflight() == []


def test_run_side_endpoint_without_digest_fails(base_env, tmp_path, monkeypatch):
    from factory_core import providers
    reg = _write_registry(tmp_path / "staged", [CLOUD])
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_PATH", str(reg))
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "cloud-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    assert any("FACTORY_MODEL_REGISTRY_SHA256 is not set" in p for p in providers.preflight())


def test_run_side_local_selection_skips_cloud_credential_check(base_env, tmp_path, monkeypatch):
    from factory_core import providers
    reg = _stage(tmp_path, monkeypatch, [LOCAL])
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_PATH", str(reg))
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "local-test")
    _local_env(monkeypatch)
    problems = providers.preflight()
    assert not any("CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY" in p for p in problems)
    assert problems[-1] == me._NO_LOCAL_DISPATCH.format(eid="local-test")


def test_undetermined_hosting_never_skips_the_cloud_credential_check(base_env, tmp_path, monkeypatch):
    # The endpoint check reports no problem but no hosting either: the missing cloud
    # credential must still be reported.
    from factory_core import providers
    monkeypatch.setattr(me, "SOURCE_REGISTRY_DIR", _write_registry(tmp_path / "src", [CLOUD]))
    real_scheduler_preflight = me.scheduler_preflight
    monkeypatch.setattr(me, "scheduler_preflight", lambda registry_dir, endpoint_id: ([], None))
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "cloud-test")
    assert providers.preflight(side="scheduler") == [
        "Set CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY in .archon/.env"]
    # An unknown id still fails on the endpoint check alone, with no credential noise.
    monkeypatch.setattr(me, "scheduler_preflight", real_scheduler_preflight)
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "nope")
    problems = providers.preflight(side="scheduler")
    assert len(problems) == 1 and "Unknown FACTORY_MODEL_ENDPOINT 'nope'" in problems[0]


def test_scheduler_side_reads_the_source_mount_without_a_digest(base_env, tmp_path, monkeypatch):
    from factory_core import providers
    monkeypatch.setattr(me, "SOURCE_REGISTRY_DIR", _write_registry(tmp_path / "src", [CLOUD, LOCAL]))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "x")
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "cloud-test")
    assert providers.preflight(side="scheduler") == []
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "local-test")
    assert providers.preflight(side="scheduler") == [me._NO_LOCAL_DISPATCH.format(eid="local-test")]
