"""#557: model endpoint registry, capability evidence, staged-copy digest and endpoint preflight.

Mirrors tests/test_provider_model.py's pass/fail-per-field structure. Every test runs with
the cloud credentials, tier aliases and FACTORY_MODEL_* selection vars cleared — a run
container sets several of them, and they would leak into the local-selection checks.
"""
import copy
import io
import os
import shutil
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


# #633: the local credential-env guard's thirteen names, pinned literally; the platform-identity
# and measured non-diverting names it must not list.
GUARD_SIGNALS = (
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_MANTLE", "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
    "AWS_PROFILE", "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_SESSION_TOKEN", "GOOGLE_CLOUD_PROJECT",
)
PLATFORM_IDENTITY_VARS = ("AWS_EXECUTION_ENV", "K_SERVICE", "GAE_APPLICATION", "FUNCTION_TARGET",
                          "KUBERNETES_SERVICE_HOST")
NON_DIVERTING_VARS = ("CLAUDE_CODE_USE_GATEWAY", "AWS_BEARER_TOKEN_BEDROCK")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (*me.CLOUD_CREDENTIALS, *me.TIER_ALIASES, *GUARD_SIGNALS, *PLATFORM_IDENTITY_VARS,
                 *NON_DIVERTING_VARS, "ANTHROPIC_BASE_URL",
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
        "local-same-host", "local-lan", "local-remote", "cloud-anthropic", "lan-ollama-qwen3-coder"]


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
    assert exc.value.problems == [f"{tmp_path / me.REGISTRY_FILE}: invalid YAML at line 3: duplicate key"]


@pytest.mark.parametrize("text, problem", [
    # An undefined alias: PyYAML's own message quotes the alias name.
    ('schema_version: "1.0"\nendpoints: [*sk-ant-SECRETVALUE]\n',
     "invalid YAML at line 2: undefined or invalid alias/anchor"),
    # A secret pasted as a mapping key, twice.
    ('schema_version: "1.0"\nsk-ant-SECRETVALUE: 1\nsk-ant-SECRETVALUE: 2\n',
     "invalid YAML at line 3: duplicate key"),
    # An unhashable (sequence) key: already a clean error on main, kept that way (R2).
    ('schema_version: "1.0"\n? [sk-ant-SECRETVALUE, b]\n: 1\n',
     "invalid YAML at line 2: found unhashable key"),
])
def test_yaml_errors_never_echo_document_text(tmp_path, text, problem):
    (tmp_path / me.REGISTRY_FILE).write_text(text, encoding="utf-8")
    with pytest.raises(me.RegistryError) as exc:
        me.load_registry(tmp_path / me.REGISTRY_FILE)
    assert exc.value.problems == [f"{tmp_path / me.REGISTRY_FILE}: {problem}"]
    assert "SECRETVALUE" not in " ".join(exc.value.problems)


@pytest.mark.parametrize("text, phrase", [
    ("a: [1\n", "syntax error"),
    ('a: "\\q"\n', "malformed token"),
    ("a: !!python/object:os.system x\n", "unsupported value or tag"),
    ("a: \x00\n", "invalid characters"),
])
def test_yaml_errors_print_a_fixed_phrase_per_exception_class(tmp_path, text, phrase):
    with pytest.raises(me.RegistryError) as exc:
        me._parse_yaml(text.encode("utf-8"), "f.yaml")
    assert exc.value.problems[0].startswith("f.yaml: invalid YAML")
    assert exc.value.problems[0].endswith(f": {phrase}")


@pytest.mark.parametrize("text, exc_type", [
    # An explicit core tag with a bad scalar: PyYAML constructs the value via a raw
    # exception (not a YAMLError) whose message quotes the scalar — a pasted secret
    # would otherwise reach the log as a traceback (#605).
    ("a: !!int sk-ant-SECRETVALUE\n", ValueError),
    ("a: !!float sk-ant-SECRETVALUE\n", ValueError),
    ("a: !!bool sk-ant-SECRETVALUE\n", KeyError),
    ("a: !!timestamp sk-ant-SECRETVALUE\n", AttributeError),
])
def test_explicit_tag_scalars_fail_closed_with_fixed_phrase(tmp_path, text, exc_type):
    (tmp_path / me.REGISTRY_FILE).write_text(text, encoding="utf-8")
    with pytest.raises(me.RegistryError) as exc:
        me.load_registry(tmp_path / me.REGISTRY_FILE)
    assert exc.value.problems == [
        f"{tmp_path / me.REGISTRY_FILE}: invalid YAML: unsupported value or tag"]
    assert "SECRETVALUE" not in " ".join(exc.value.problems)


def test_explicit_tag_sentinel_never_reaches_cli_output(tmp_path, monkeypatch, capsys):
    """#605: the sentinel must appear in neither the error message nor CLI stderr/stdout."""
    src = tmp_path / "src"
    src.mkdir()
    (src / me.REGISTRY_FILE).write_text("a: !!int sk-ant-SECRETVALUE\n", encoding="utf-8")
    from factory_core.providers import cli
    argv = ["model-endpoints", "stage", "--source-dir", str(src),
            "--dest-dir", str(tmp_path / "dest")]
    monkeypatch.setattr(sys, "argv", ["providers-cli", *argv])
    with pytest.raises(SystemExit):
        cli.main()
    out = capsys.readouterr()
    assert "SECRETVALUE" not in out.out
    assert "SECRETVALUE" not in out.err
    assert "Traceback" not in out.err


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


def test_tree_sha256_closes_the_fd_when_a_file_is_swapped_for_a_directory(tmp_path, monkeypatch):
    # A path swapped for a directory after the is_file() check: os.fdopen() would raise
    # IsADirectoryError without closing the descriptor (#594 R7).
    reg = _write_registry(tmp_path / "reg", [LOCAL])
    swapped = tmp_path / "swapped-in"
    swapped.mkdir()
    real_os_open, opened = os.open, []

    def _os_open(path, flags, *args, **kwargs):
        if Path(path).name == me.REGISTRY_FILE:
            opened.append(real_os_open(swapped, os.O_RDONLY))
            return opened[-1]
        return real_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", _os_open)
    with pytest.raises(me.RegistryError, match="not a regular file"):
        me.tree_sha256(reg)
    # Nothing opens a file between the raise and this check, so the fd number is not reused.
    with pytest.raises(OSError):
        os.fstat(opened[0])


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
    assert "quantisation" in reasons[0]


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
    # #633: the three required names plus the one under test, so the record clears the
    # invalidated_by minimum and reaches the fingerprint comparison.
    record = _record(LOCAL, invalidated_by=["model_digest", "harness_version", "registry_config",
                                            name])
    state, reasons = me.evidence_state(record, LOCAL)
    assert state == "unknown" and name in reasons[0]


def test_unpinned_fixture_suite_is_unknown(monkeypatch):
    monkeypatch.setattr(me, "PINNED_FIXTURE_SUITE_VERSION", None)
    record = _record(LOCAL, invalidated_by=["model_digest", "harness_version", "registry_config"])
    state, reasons = me.evidence_state(record, LOCAL)
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

    Hooks every read primitive this module has used or could reach for: os.open() (what
    _read_tree uses, via os.fdopen), the module's builtin open() and Path.read_bytes(). The
    last two do not go through os.open, so each needs its own hook for a re-read to be
    observed whichever primitive performs it.
    """
    root = Path(root).resolve()
    real_os_open, real_open, real_read_bytes = os.open, open, Path.read_bytes
    reads = {}

    def _serve(path):
        """None if path is outside root; else count the read and return (rel, tampered bytes
        or None for the real file)."""
        try:
            rel = Path(path).resolve().relative_to(root).as_posix()
        except (ValueError, OSError):
            return None
        reads[rel] = reads.get(rel, 0) + 1
        return rel, (tampered[rel] if reads[rel] > 1 and rel in tampered else None)

    def _os_open(path, flags, *args, **kwargs):
        served = _serve(path) if isinstance(path, (str, os.PathLike)) else None
        if served is None or served[1] is None:
            return real_os_open(path, flags, *args, **kwargs)
        # A throwaway regular file (unlinked once open), so the caller's fstat()-based
        # regular-file check still passes.
        tmp = f"{path}.tamper-{reads[served[0]]}"
        fd = real_os_open(tmp, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.write(fd, served[1])
        os.lseek(fd, 0, os.SEEK_SET)
        os.unlink(tmp)
        return fd

    def _open(file, mode="r", *args, **kwargs):
        served = _serve(file) if isinstance(file, (str, os.PathLike)) else None
        if served is None or served[1] is None:
            return real_open(file, mode, *args, **kwargs)
        return io.BytesIO(served[1]) if "b" in mode else io.StringIO(served[1].decode("utf-8"))

    def _read_bytes(self):
        served = _serve(self)
        return real_read_bytes(self) if served is None or served[1] is None else served[1]

    monkeypatch.setattr(os, "open", _os_open)
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


LAN_OLLAMA = "lan-ollama-qwen3-coder"


def test_lan_ollama_profile_validates_but_activates_no_routing(tmp_path, monkeypatch):
    # #560: the runbook's worked LAN entry is schema-valid, and selecting it still fails on
    # _NO_LOCAL_DISPATCH on both sides — the profile routes nothing until #587.
    entry = {e["id"]: e for e in me.load_registry(FIXTURES / me.REGISTRY_FILE)}[LAN_OLLAMA]
    assert entry["hosting_location"] == "local" and me.base_url_scope(entry["base_url"]) == "lan"
    assert me.scheduler_preflight(FIXTURES, LAN_OLLAMA) == (
        [me._NO_LOCAL_DISPATCH.format(eid=LAN_OLLAMA)], "local")
    staged = tmp_path / "staged"
    shutil.copytree(FIXTURES, staged)
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", me.tree_sha256(staged))
    monkeypatch.setenv("LOCAL_OLLAMA_DUMMY_TOKEN", "dummy-not-a-credential")
    _local_env(monkeypatch, entry)
    problems = _run_preflight(staged, LAN_OLLAMA, "local")
    assert any(f"endpoint '{LAN_OLLAMA}'" in p and "capability evidence is 'unknown'" in p
               for p in problems)
    assert problems[-1] == me._NO_LOCAL_DISPATCH.format(eid=LAN_OLLAMA)


# --- #558: load_verified_entry (the proxy's Configuration-contract loader) -----------------

def _forbidden(*args, **kwargs):
    raise AssertionError("load_verified_entry() must never call _select() or load_registry()")


def test_load_verified_entry_returns_a_verified_cloud_entry(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [CLOUD, LOCAL])
    assert me.load_verified_entry(reg, "cloud-test") == ([], CLOUD)


def test_load_verified_entry_fails_closed_on_a_digest_mismatch(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [CLOUD])
    _write_registry(reg, [CLOUD, _entry(CLOUD, id="cloud-extra")])
    problems, entry = me.load_verified_entry(reg, "cloud-test")
    assert entry is None
    assert len(problems) == 1 and "does not match FACTORY_MODEL_REGISTRY_SHA256" in problems[0]


def test_load_verified_entry_requires_the_digest(tmp_path):
    reg = _write_registry(tmp_path / "staged", [CLOUD])
    problems, entry = me.load_verified_entry(reg, "cloud-test")
    assert entry is None and "FACTORY_MODEL_REGISTRY_SHA256 is not set" in problems[0]


def test_load_verified_entry_unknown_id_chooses_no_other(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [CLOUD])
    problems, entry = me.load_verified_entry(reg, "cloud-nope")
    assert entry is None and "no other endpoint is chosen instead" in problems[0]


def test_load_verified_entry_without_registry_file(tmp_path, monkeypatch):
    reg = tmp_path / "staged"
    (reg / me.EVIDENCE_DIR).mkdir(parents=True)
    (reg / me.EVIDENCE_DIR / "cloud-test.yaml").write_text("x: 1\n", encoding="utf-8")
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", me.tree_sha256(reg))
    assert me.load_verified_entry(reg, "cloud-test") == (
        [f"model endpoint registry not found at {reg / me.REGISTRY_FILE}"], None)


def test_load_verified_entry_keeps_no_local_dispatch(tmp_path, monkeypatch, pinned_suite):
    # Not filtered: a local entry with perfect evidence and env still comes back as a problem.
    reg = _stage(tmp_path, monkeypatch, [LOCAL], evidence={"local-test": _record(LOCAL)})
    _local_env(monkeypatch)
    assert me.load_verified_entry(reg, "local-test") == (
        [me._NO_LOCAL_DISPATCH.format(eid="local-test")], None)


def test_load_verified_entry_reads_each_staged_file_once(tmp_path, monkeypatch, pinned_suite):
    reg = _stage(tmp_path, monkeypatch, [LOCAL], evidence={"local-test": _record(LOCAL)})
    _local_env(monkeypatch)
    monkeypatch.setattr(me, "_select", _forbidden)
    monkeypatch.setattr(me, "load_registry", _forbidden)
    evidence = f"{me.EVIDENCE_DIR}/local-test.yaml"
    reads = _serve_first_read(monkeypatch, reg, {
        me.REGISTRY_FILE: _registry_bytes([_entry(CLOUD, id="local-test")]),
        evidence: yaml.safe_dump({"capability_evidence": _record(
            LOCAL, results=dict(_record(LOCAL)["results"], streaming="fail"))}).encode("utf-8"),
    })
    assert me.load_verified_entry(reg, "local-test") == (
        [me._NO_LOCAL_DISPATCH.format(eid="local-test")], None)
    assert reads == {me.REGISTRY_FILE: 1, evidence: 1}


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


def _newer_minor_registry_with_unknown_keys(root):
    """A schema 1.1 registry (unknown keys tolerated) whose local entry carries a secret-like
    unknown key at the entry level, inside model_identity and inside timeouts."""
    entry = _entry(LOCAL, api_key=SENTINEL)
    entry["model_identity"]["api_key"] = SENTINEL
    entry["timeouts"]["api_key"] = SENTINEL
    return _write_registry(root, [entry, CLOUD], schema_version="1.1")


def test_redacted_view_omits_tolerated_unknown_keys_at_every_level(tmp_path):
    view = me.redacted_view(_newer_minor_registry_with_unknown_keys(tmp_path / "reg"))
    assert SENTINEL not in repr(view) and "api_key" not in repr(view)
    local, cloud = view["endpoints"]
    assert local["unknown_keys_omitted"] == 3
    assert local["model_identity"] == LOCAL["model_identity"]
    assert local["timeouts"] == LOCAL["timeouts"]
    assert "unknown_keys_omitted" not in cloud


def test_redacted_view_staged_omits_tolerated_unknown_keys(tmp_path, monkeypatch):
    reg = _newer_minor_registry_with_unknown_keys(tmp_path / "staged")
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", me.tree_sha256(reg))
    problems, view = me.redacted_view_staged(reg)
    assert problems == [] and view == me.redacted_view(reg)
    assert SENTINEL not in repr(view) and view["endpoints"][0]["unknown_keys_omitted"] == 3


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


def test_unselected_lan_profile_leaves_the_cloud_default_path_unchanged(base_env, tmp_path, monkeypatch):
    # #560: the new profile staged and mounted, FACTORY_MODEL_ENDPOINT unset — both preflight
    # sides pass and neither the registry nor the adapter module is loaded.
    from factory_core import providers
    staged = tmp_path / "staged"
    shutil.copytree(FIXTURES, staged)
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_PATH", str(staged))
    monkeypatch.setattr(me, "SOURCE_REGISTRY_DIR", FIXTURES)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "x")
    for name in ("model_endpoint", "model_adapter"):
        monkeypatch.delitem(sys.modules, f"factory_core.providers.{name}", raising=False)
        monkeypatch.delattr(providers, name, raising=False)
    assert providers.preflight() == [] and providers.preflight(side="scheduler") == []
    assert "factory_core.providers.model_endpoint" not in sys.modules
    assert "factory_core.providers.model_adapter" not in sys.modules


# --- #586: model-endpoints hosting (the run container's pre-clone hosting resolver) --------

def _hosting(monkeypatch, capsys):
    """Run `providers-cli model-endpoints hosting`; returns (stdout, stderr)."""
    from factory_core.providers import cli
    monkeypatch.setattr(sys, "argv", ["providers-cli", "model-endpoints", "hosting"])
    cli.main()  # returns normally: the verb never exits non-zero
    out = capsys.readouterr()
    return out.out, out.err


def _select_staged(monkeypatch, reg, endpoint_id):
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_PATH", str(reg))
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", endpoint_id)


def test_hosting_without_an_endpoint_is_cloud(monkeypatch, capsys):
    assert _hosting(monkeypatch, capsys) == ("hosting=cloud\n", "")


def test_hosting_verified_cloud_entry_is_cloud(tmp_path, monkeypatch, capsys):
    reg = _stage(tmp_path, monkeypatch, [CLOUD, LOCAL])
    _select_staged(monkeypatch, reg, "cloud-test")
    assert _hosting(monkeypatch, capsys) == ("hosting=cloud\n", "")


@pytest.mark.parametrize("breakage, problem", [
    ("digest_missing", "FACTORY_MODEL_REGISTRY_SHA256 is not set"),
    ("digest_mismatch", "does not match FACTORY_MODEL_REGISTRY_SHA256"),
    ("unknown_id", "Unknown FACTORY_MODEL_ENDPOINT 'local-nope'"),
    ("unreadable_registry", "model endpoint registry not found"),
])
def test_hosting_fails_closed_to_cloud(tmp_path, monkeypatch, capsys, pinned_suite,
                                       breakage, problem):
    reg = _stage(tmp_path, monkeypatch, [LOCAL], evidence={"local-test": _record(LOCAL)})
    _local_env(monkeypatch)
    _select_staged(monkeypatch, reg, "local-test")
    if breakage == "digest_missing":
        monkeypatch.delenv("FACTORY_MODEL_REGISTRY_SHA256")
    elif breakage == "digest_mismatch":
        _write_registry(reg, [LOCAL, CLOUD])
    elif breakage == "unknown_id":
        monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "local-nope")
    else:
        (reg / me.REGISTRY_FILE).unlink()
        monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", me.tree_sha256(reg))
    out, err = _hosting(monkeypatch, capsys)
    assert out == "hosting=cloud\n"
    assert problem in err


def test_hosting_keeps_the_no_local_dispatch_hard_block(tmp_path, monkeypatch, capsys,
                                                        pinned_suite):
    # Owner decision 1: a fully valid local entry still resolves to cloud until #587.
    reg = _stage(tmp_path, monkeypatch, [LOCAL], evidence={"local-test": _record(LOCAL)})
    _local_env(monkeypatch)
    _select_staged(monkeypatch, reg, "local-test")
    out, err = _hosting(monkeypatch, capsys)
    assert out == "hosting=cloud\n"
    assert me._NO_LOCAL_DISPATCH.format(eid="local-test") in err


def test_hosting_is_local_only_for_a_verified_local_entry(tmp_path, monkeypatch, capsys):
    # The local branch is reachable only by patching the verified loader (spec Req. 1;
    # patching _NO_LOCAL_DISPATCH cannot work, _local_problems appends it unconditionally).
    calls = []

    def verified_local(path, eid):
        calls.append((Path(path), eid))
        return [], {"hosting_location": "local"}

    monkeypatch.setattr(me, "load_verified_entry", verified_local)
    _select_staged(monkeypatch, tmp_path / "staged", "local-test")
    assert _hosting(monkeypatch, capsys) == ("hosting=local\n", "")
    assert calls == [(tmp_path / "staged", "local-test")]


def test_hosting_any_problem_from_the_verified_loader_is_cloud(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(me, "load_verified_entry",
                        lambda path, eid: (["some problem"], {"hosting_location": "local"}))
    _select_staged(monkeypatch, tmp_path / "staged", "local-test")
    out, err = _hosting(monkeypatch, capsys)
    assert out == "hosting=cloud\n" and "some problem" in err


def test_hosting_exception_is_cloud_and_exits_zero(tmp_path, monkeypatch, capsys):
    def boom(path, eid):
        raise OSError("disk on fire")

    monkeypatch.setattr(me, "load_verified_entry", boom)
    _select_staged(monkeypatch, tmp_path / "staged", "local-test")
    out, err = _hosting(monkeypatch, capsys)
    assert out == "hosting=cloud\n" and "OSError" in err


# --- #585: model-endpoints hosting-check (scheduler.sh's non-cloud endpoint guard) ---------

def _hosting_check(monkeypatch, capsys, endpoint_id):
    from factory_core.providers import cli
    monkeypatch.setattr(sys, "argv", ["providers-cli", "model-endpoints", "hosting-check",
                                      "--endpoint-id", endpoint_id])
    code = 0
    try:
        cli.main()
    except SystemExit as e:
        code = e.code
    return code, capsys.readouterr()


def test_hosting_check_cloud_entry_prints_exactly_hosting_cloud(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(me, "SOURCE_REGISTRY_DIR", _write_registry(tmp_path / "src", [CLOUD, LOCAL]))
    code, out = _hosting_check(monkeypatch, capsys, "cloud-test")
    assert code == 0
    assert out.out == "hosting=cloud\n"


def test_hosting_check_local_entry_exits_1_with_local_token(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(me, "SOURCE_REGISTRY_DIR", _write_registry(tmp_path / "src", [CLOUD, LOCAL]))
    code, out = _hosting_check(monkeypatch, capsys, "local-test")
    assert code == 1
    assert out.out.startswith("hosting=local ")
    assert "hosting=cloud" not in out.out


def test_hosting_check_unknown_id_exits_1_unknown(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(me, "SOURCE_REGISTRY_DIR", _write_registry(tmp_path / "src", [CLOUD]))
    code, out = _hosting_check(monkeypatch, capsys, "nope")
    assert code == 1
    assert out.out.startswith("hosting=unknown ")
    assert "Unknown FACTORY_MODEL_ENDPOINT 'nope'" in out.out
    assert "hosting=cloud" not in out.out


def test_hosting_check_missing_registry_exits_1_unknown(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(me, "SOURCE_REGISTRY_DIR", tmp_path / "absent")
    code, out = _hosting_check(monkeypatch, capsys, "cloud-test")
    assert code == 1
    assert out.out.startswith("hosting=unknown ")
    assert "not found" in out.out
    assert "hosting=cloud" not in out.out


def test_hosting_check_invalid_registry_exits_1_unknown(tmp_path, monkeypatch, capsys):
    src = tmp_path / "src"
    src.mkdir()
    (src / me.REGISTRY_FILE).write_text("schema_version: 1.0\nendpoints: []\n", encoding="utf-8")
    monkeypatch.setattr(me, "SOURCE_REGISTRY_DIR", src)
    code, out = _hosting_check(monkeypatch, capsys, "cloud-test")
    assert code == 1
    assert out.out.startswith("hosting=unknown ")
    assert "hosting=cloud" not in out.out


def test_hosting_check_unreadable_registry_exits_1_unknown(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(me, "SOURCE_REGISTRY_DIR", _write_registry(tmp_path / "src", [CLOUD]))

    def _unreadable(path):
        raise me.RegistryError([f"{path}: unreadable (Permission denied)"])

    monkeypatch.setattr(me, "_read_file", _unreadable)
    code, out = _hosting_check(monkeypatch, capsys, "cloud-test")
    assert code == 1
    assert out.out.startswith("hosting=unknown ")
    assert "unreadable" in out.out
    assert "hosting=cloud" not in out.out


def test_hosting_check_reads_the_source_mount_never_the_staged_copy(tmp_path, monkeypatch, capsys):
    # The staged copy lives on the run-writable state volume: a cloud entry there must never
    # answer for a local entry on the scheduler's read-only mount (#585 owner decision 2).
    monkeypatch.setattr(me, "SOURCE_REGISTRY_DIR",
                        _write_registry(tmp_path / "src", [_entry(LOCAL, id="sel")]))
    staged = _write_registry(tmp_path / "staged", [_entry(CLOUD, id="sel")])
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_PATH", str(staged))
    code, out = _hosting_check(monkeypatch, capsys, "sel")
    assert code == 1
    assert out.out.startswith("hosting=local ")


# --- #633: the local-dispatch refusal scoped to one helper intent --------------------------

HELPER = "classify-preview-helper"
EXISTING_INTENTS = ("fix", "continue", "close", "refine", "plan", "deconflict", "recheck",
                    "fix-main", "audit-deps")
NO_LOCAL = me._NO_LOCAL_DISPATCH.format(eid="local-test")


def _valid_local(tmp_path, monkeypatch, record=None):
    """A fully valid, `tested` local entry staged with its digest and a matching env."""
    reg = _stage(tmp_path, monkeypatch, [LOCAL],
                 evidence={"local-test": record if record is not None else _record(LOCAL)})
    _local_env(monkeypatch)
    return reg


def test_local_eligible_intents_is_exactly_the_helper():
    assert me.LOCAL_ELIGIBLE_INTENTS == (HELPER,)


# Exact match only: a padded, re-cased or non-string value is not the helper intent either.
NEAR_HELPER = (" classify-preview-helper", "classify-preview-helper ", "classify-preview-helper\n",
               "CLASSIFY-PREVIEW-HELPER", b"classify-preview-helper", ["classify-preview-helper"],
               True)


@pytest.mark.parametrize("loader", ["preflight", "load_verified_entry"])
@pytest.mark.parametrize("intent", [*EXISTING_INTENTS, "classify-preview-checker", None, "",
                                    *NEAR_HELPER])
def test_local_entry_refused_for_every_intent_except_helper(tmp_path, monkeypatch, pinned_suite,
                                                           loader, intent):
    reg = _valid_local(tmp_path, monkeypatch)
    expected = ([NO_LOCAL], "local") if loader == "preflight" else ([NO_LOCAL], None)
    assert getattr(me, loader)(reg, "local-test", intent=intent) == expected


def test_helper_intent_omits_only_no_local_dispatch(tmp_path, monkeypatch, pinned_suite):
    reg = _valid_local(tmp_path, monkeypatch)
    assert me.preflight(reg, "local-test", intent=HELPER) == ([], "local")
    problems, entry = me.load_verified_entry(reg, "local-test", intent=HELPER)
    assert problems == [] and entry["id"] == "local-test" and entry["hosting_location"] == "local"


@pytest.mark.parametrize("check, expected", [
    ("cloud_credential", "cloud model credential(s) are set in this container: ANTHROPIC_API_KEY"),
    ("base_url", "ANTHROPIC_BASE_URL must equal the entry's base_url"),
    ("tier_alias", "ANTHROPIC_DEFAULT_OPUS_MODEL must equal model_identity.served_name"),
    ("evidence_not_tested", "capability evidence is 'stale', not 'tested'"),
    ("role_not_qualified", "streaming is fail, not pass"),
])
def test_helper_intent_still_fails_each_local_check(tmp_path, monkeypatch, pinned_suite,
                                                    check, expected):
    record = _record(LOCAL)
    if check == "evidence_not_tested":
        record = _record(LOCAL, expires_at="2020-01-01T00:00:00+00:00")
    elif check == "role_not_qualified":
        record = _record(LOCAL, results=dict(record["results"], streaming="fail"))
    reg = _valid_local(tmp_path, monkeypatch, record)
    if check == "cloud_credential":
        monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL + "-cloud")
    elif check == "base_url":
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://192.0.2.99:8080")
    elif check == "tier_alias":
        monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-5-5")
    problems, hosting = me.preflight(reg, "local-test", intent=HELPER)
    assert hosting == "local" and any(expected in p for p in problems)
    assert NO_LOCAL not in problems
    problems, entry = me.load_verified_entry(reg, "local-test", intent=HELPER)
    assert entry is None and any(expected in p for p in problems)


def test_intent_not_read_from_environment_or_pointer(base_env, tmp_path, monkeypatch, capsys,
                                                     pinned_suite):
    # Regression guard: passes on main too, where the refusal is unconditional.
    from factory_core import providers
    reg = _valid_local(tmp_path, monkeypatch)
    _select_staged(monkeypatch, reg, "local-test")
    for name in ("INTENT", "FACTORY_DISPATCH_INTENT", "FACTORY_INTENT", "DISPATCH_INTENT"):
        monkeypatch.setenv(name, HELPER)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "current-run.json").write_text(
        f'{{"run_id":"r","issue_number":1,"intent":"{HELPER}","stage":"{HELPER}"}}\n',
        encoding="utf-8")
    monkeypatch.setenv("CURRENT_RUN_DIR", str(run_dir))
    assert me.preflight(reg, "local-test") == ([NO_LOCAL], "local")
    assert me.load_verified_entry(reg, "local-test") == ([NO_LOCAL], None)
    assert providers.preflight()[-1] == NO_LOCAL
    out, err = _hosting(monkeypatch, capsys)
    assert out == "hosting=cloud\n" and NO_LOCAL in err


def test_guard_signals_are_the_thirteen_names_owned_by_model_endpoint():
    import ast
    assert me.LOCAL_CREDENTIAL_ENV_SIGNALS == GUARD_SIGNALS
    tree = ast.parse(Path(me.__file__).read_text(encoding="utf-8"))
    # model_endpoint already imports _missing from providers/model.py; the signal tuples
    # themselves must never come from there.
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert not any("ALT_AUTH" in name for name in imported)
    assert not hasattr(me, "_AWS_ALT_AUTH_SIGNALS") and not hasattr(me, "_GCP_ALT_AUTH_SIGNALS")


@pytest.mark.parametrize("name", GUARD_SIGNALS)
def test_local_selection_fails_on_alternate_credential_signal(tmp_path, monkeypatch, pinned_suite,
                                                              name):
    reg = _valid_local(tmp_path, monkeypatch)
    monkeypatch.setenv(name, "1")
    expected = (f"endpoint 'local-test' is local but an alternate cloud credential signal is set "
                f"in this container: {name} — a local-routed container holds none "
                "(contract §4.3)")
    assert me.preflight(reg, "local-test", intent=HELPER) == ([expected], "local")
    assert me.load_verified_entry(reg, "local-test", intent=HELPER) == ([expected], None)


@pytest.mark.parametrize("name", [*PLATFORM_IDENTITY_VARS, *NON_DIVERTING_VARS])
def test_platform_identity_variable_does_not_fail_local_selection(tmp_path, monkeypatch,
                                                                  pinned_suite, name):
    reg = _valid_local(tmp_path, monkeypatch)
    monkeypatch.setenv(name, "1")
    assert me.preflight(reg, "local-test", intent=HELPER) == ([], "local")


def test_measured_non_diverting_names_are_not_guard_signals():
    assert not set(NON_DIVERTING_VARS) & set(me.LOCAL_CREDENTIAL_ENV_SIGNALS)
    assert not set(PLATFORM_IDENTITY_VARS) & set(me.LOCAL_CREDENTIAL_ENV_SIGNALS)


MINIMUM = ["model_digest", "harness_version", "registry_config"]


@pytest.mark.parametrize("names", [[], ["harness_version", "registry_config"],
                                   ["model_digest", "registry_config"],
                                   ["model_digest", "harness_version"],
                                   ["fixture_suite_version"]])
def test_invalidated_by_below_minimum_is_declared(pinned_suite, names):
    state, reasons = me.evidence_state(_record(LOCAL, invalidated_by=names), LOCAL)
    assert state == "declared"
    assert any("invalidated_by must include at least model_digest, harness_version, "
               "registry_config" in r for r in reasons)


def test_invalidated_by_exact_minimum_is_tested(pinned_suite, monkeypatch):
    # Regression guard (passes on main): the three alone are enough, and they are compared.
    assert me.evidence_state(_record(LOCAL, invalidated_by=list(MINIMUM)), LOCAL) == ("tested", [])
    monkeypatch.setattr(me, "observed_harness_version", lambda: "2.1.300")
    assert me.evidence_state(_record(LOCAL, invalidated_by=list(MINIMUM)), LOCAL)[0] == "stale"


@pytest.mark.parametrize("bad", [{"model_digest": 1}, ["model_digest"], 7, None])
def test_invalidated_by_non_string_element_is_declared_not_an_exception(pinned_suite, bad):
    # Regression guard (passes on main): a malformed element must never raise.
    record = _record(LOCAL, invalidated_by=[*MINIMUM, bad])
    assert me.evidence_state(record, LOCAL)[0] == "declared"


def test_below_minimum_evidence_fails_the_helper_closed(tmp_path, monkeypatch, pinned_suite):
    reg = _valid_local(tmp_path, monkeypatch, _record(LOCAL, invalidated_by=[]))
    problems, hosting = me.preflight(reg, "local-test", intent=HELPER)
    assert hosting == "local"
    assert "endpoint 'local-test': capability evidence is 'declared', not 'tested' (contract §4.5)" \
        in problems
