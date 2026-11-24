"""Model endpoint registry, capability evidence and endpoint preflight (#557).

Configuration-contract owner in docs/hybrid-execution-contract.md (§4.1, §4.3, §4.5, §4.11);
design in docs/superpowers/specs/2026-11-23-endpoint-registry-secrets-preflight-design.md.

Read-only: nothing here writes the operator's registry or evidence; stage_registry() only
copies the scheduler's read-only mount onto the shared state volume. providers.preflight()
imports this module only when FACTORY_MODEL_ENDPOINT is set, so the default cloud path never
loads it.
"""
import hashlib
import hmac
import ipaddress
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from factory_core.providers.model import _missing

REGISTRY_FILE = "model-endpoints.yaml"
EVIDENCE_DIR = "capability-evidence"
# The scheduler's read-only bind mount of this instance's model-registry/ (deploy/docker-compose.yml).
SOURCE_REGISTRY_DIR = Path("/etc/dark-factory/model-registry")
# The staged copy on the shared state volume every run container mounts.
_DEFAULT_STAGED_DIR = "/var/lib/dark-factory/model-registry"

SUPPORTED_MAJOR, SUPPORTED_MINOR = 1, 0
PROTOCOL = "anthropic-messages"  # contract §4.11 decision 1
PAYLOAD_CLASSES = ("cloud_ok", "local_only")
HOSTING_LOCATIONS = ("local", "cloud")
# Recognized cloud model credentials: never a local entry's secret_reference, never present
# in a local-routed container (contract §4.3 credential scope).
CLOUD_CREDENTIALS = (
    "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
)
TIER_ALIASES = tuple(f"ANTHROPIC_DEFAULT_{t}_MODEL" for t in ("OPUS", "SONNET", "HAIKU", "FABLE"))
TIMEOUT_FIELDS = ("connect_seconds", "time_to_first_token_seconds", "idle_seconds", "total_seconds")

EVIDENCE_STATES = ("tested", "declared", "stale", "unknown")
RESULT_VALUES = ("pass", "fail", "unsupported", "not_tested")
FINGERPRINTS = (
    "model_digest", "quantisation", "runtime_version", "chat_template", "context_settings",
    "harness_version", "fixture_suite_version", "registry_config",
)
# §4.5's minimum capability dimensions; an `actor` qualification needs every one to be `pass`.
ACTOR_REQUIRED_DIMENSIONS = (
    "protocol_compatibility", "tool_call_fidelity", "streaming", "context_length",
    "role_isolation", "timeout_cancellation",
)
# No fixture suite is pinned until #561 ships one, so every local selection's evidence
# resolves to `unknown` until then.
PINNED_FIXTURE_SUITE_VERSION = None

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_MUTABLE_REVISIONS = {"latest", "main", "master", "head", "stable", "default"}

_TOP_KEYS = {"schema_version", "endpoints"}
_ENTRY_KEYS = {
    "id", "hosting_location", "protocol", "accepts_payload_classes", "model_identity",
    "base_url", "secret_reference", "timeouts",
}
_MODEL_IDENTITY_KEYS = {"served_name", "digest_or_revision"}
# §4.5's record fields, plus `registry_config`: the SHA-256 of the registry entry the record
# certified (registry_config_sha256), which the `registry_config` fingerprint compares.
_EVIDENCE_KEYS = {
    "schema_version", "endpoint_id", "model_identity", "runtime_stack", "harness_version",
    "fixture_suite_version", "results", "qualified_roles", "certified_by", "certified_at",
    "expires_at", "invalidated_by", "registry_config",
}
_EVIDENCE_REQUIRED = _EVIDENCE_KEYS - {"registry_config"}

_NO_LOCAL_DISPATCH = (
    "endpoint '{eid}' is local, but no local-eligible dispatch exists yet — every current "
    "dispatch runs a gated phase (contract §4.11 decision 3; the first helper dispatch is #587)"
)


class RegistryError(Exception):
    """Invalid registry, evidence or staged tree. .problems is the list of messages."""

    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = list(problems)


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of keeping the last one."""


def _construct_unique_mapping(loader, node):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark)
        seen.add(key)
    return loader.construct_mapping(node, deep=True)


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def _read_yaml(path):
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.load(f, Loader=_UniqueKeyLoader)  # noqa: S506 — SafeLoader subclass
    except yaml.YAMLError as e:
        # Never echo the offending line: a pasted secret would land in the log.
        mark = getattr(e, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark else ""
        problem = getattr(e, "problem", None) or "parse error"
        raise RegistryError([f"{path}: invalid YAML{where}: {problem}"]) from None
    except OSError as e:
        raise RegistryError([f"{path}: unreadable ({e.strerror})"]) from None


def _parse_version(value, where):
    m = _VERSION_RE.match(value) if isinstance(value, str) else None
    if not m:
        return None, [f'{where}: schema_version must be a quoted "MAJOR.MINOR" string']
    major, minor = int(m[1]), int(m[2])
    if major != SUPPORTED_MAJOR:
        return None, [f"{where}: schema_version {value} has an unsupported major version "
                      f"(this loader supports {SUPPORTED_MAJOR}.x) — hard reject"]
    return (major, minor), []


def _unknown_keys(obj, allowed, where, strict):
    if not strict:
        return []
    return [f"{where}: unknown key '{k}'" for k in sorted(set(obj) - allowed, key=str)]


def _validate_base_url(url, where):
    if not isinstance(url, str) or not url:
        return [f"{where}: base_url is required for a local entry"]
    try:
        parts = urlsplit(url)
        parts.port  # raises ValueError on a malformed port
    except ValueError:
        return [f"{where}: base_url is not a well-formed URL"]
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return [f"{where}: base_url must be an http(s) URL with a host"]
    if parts.username is not None or parts.password is not None:
        return [f"{where}: base_url must not embed credentials — use secret_reference"]
    if parts.query or parts.fragment:
        return [f"{where}: base_url must not carry a query string or fragment"]
    return []


def _validate_timeouts(timeouts, where, strict):
    if not isinstance(timeouts, dict):
        return [f"{where}: timeouts are required for a local entry ({', '.join(TIMEOUT_FIELDS)})"]
    problems = _unknown_keys(timeouts, set(TIMEOUT_FIELDS), f"{where}.timeouts", strict)
    values = {}
    for field in TIMEOUT_FIELDS:
        v = timeouts.get(field)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            problems.append(f"{where}: timeouts.{field} must be a positive number")
        else:
            values[field] = v
    total = values.get("total_seconds")
    if total is not None:
        for field in TIMEOUT_FIELDS[:3]:
            if field in values and values[field] > total:
                problems.append(f"{where}: timeouts.{field} must not exceed timeouts.total_seconds")
    return problems


def _validate_entry(entry, index, strict):
    where = f"endpoints[{index}]"
    if not isinstance(entry, dict):
        return [f"{where}: must be a mapping"]
    problems = []
    eid = entry.get("id")
    if isinstance(eid, str) and _ID_RE.match(eid):
        where = f"endpoint '{eid}'"
    else:
        problems.append(f"{where}: id must match {_ID_RE.pattern}")
    problems += _unknown_keys(entry, _ENTRY_KEYS, where, strict)

    hosting = entry.get("hosting_location")
    if hosting not in HOSTING_LOCATIONS:
        problems.append(f"{where}: hosting_location must be one of local, cloud")

    protocol = entry.get("protocol")
    if protocol != PROTOCOL:
        problems.append(f"{where}: unsupported protocol {protocol!r} — only '{PROTOCOL}' is "
                        "supported (contract §4.11 decision 1)")

    classes = entry.get("accepts_payload_classes")
    if (not isinstance(classes, list) or not classes
            or any(c not in PAYLOAD_CLASSES for c in classes) or len(set(classes)) != len(classes)):
        problems.append(f"{where}: accepts_payload_classes must be a non-empty list drawn from "
                        "cloud_ok, local_only")
    elif hosting == "cloud" and "local_only" in classes:
        problems.append(f"{where}: a cloud entry may never accept local_only (contract §4.3)")
    elif hosting == "local" and "local_only" not in classes:
        problems.append(f"{where}: a local entry must accept local_only (contract §4.3)")

    identity = entry.get("model_identity")
    if not isinstance(identity, dict):
        problems.append(f"{where}: model_identity is required (served_name, digest_or_revision)")
    else:
        problems += _unknown_keys(identity, _MODEL_IDENTITY_KEYS, f"{where}.model_identity", strict)
        served = identity.get("served_name")
        if not isinstance(served, str) or not served.strip():
            problems.append(f"{where}: model_identity.served_name is required")
        revision = identity.get("digest_or_revision")
        if (not isinstance(revision, str) or not revision or any(ch.isspace() for ch in revision)
                or revision.lower() in _MUTABLE_REVISIONS
                or revision.lower().endswith((":latest", "-latest"))):
            problems.append(f"{where}: model_identity.digest_or_revision must be an immutable "
                            "digest or revision — no 'latest'-style alias")

    ref = entry.get("secret_reference")
    if not isinstance(ref, str) or not _SECRET_NAME_RE.match(ref):
        # The value is never echoed: a pasted token must not reach a log.
        problems.append(f"{where}: secret_reference must be an env-var name matching "
                        f"{_SECRET_NAME_RE.pattern} — never the secret itself")
    elif hosting == "local" and ref in CLOUD_CREDENTIALS:
        problems.append(f"{where}: a local entry's secret_reference may not name the cloud "
                        f"credential {ref} — no cloud credential is forwarded to a local endpoint")

    if hosting == "local":
        problems += _validate_base_url(entry.get("base_url"), where)
        problems += _validate_timeouts(entry.get("timeouts"), where, strict)
    elif hosting == "cloud":
        if "base_url" in entry:
            problems.append(f"{where}: base_url is not allowed on a cloud entry (cloud resolves "
                            "its address from the existing provider env)")
        if "timeouts" in entry:
            problems.append(f"{where}: timeouts are not enforced for cloud entries — §4.6")
    return problems


def load_registry(path) -> list[dict]:
    """Parse and validate model-endpoints.yaml (spec §B). Never reads the environment."""
    path = Path(path)
    if not path.is_file():
        raise RegistryError([f"model endpoint registry not found at {path}"])
    doc = _read_yaml(path)
    if not isinstance(doc, dict):
        raise RegistryError([f"{path}: must be a mapping with schema_version and endpoints"])
    version, problems = _parse_version(doc.get("schema_version"), str(path))
    if problems:
        raise RegistryError(problems)
    # Unknown keys are rejected at the supported minor version and tolerated only on a file
    # whose minor version is newer than this loader's.
    strict = version[1] <= SUPPORTED_MINOR
    problems = _unknown_keys(doc, _TOP_KEYS, str(path), strict)
    endpoints = doc.get("endpoints")
    if not isinstance(endpoints, list):
        raise RegistryError(problems + [f"{path}: endpoints must be a list"])
    seen = set()
    for index, entry in enumerate(endpoints):
        problems += _validate_entry(entry, index, strict)
        eid = entry.get("id") if isinstance(entry, dict) else None
        if isinstance(eid, str):
            if eid in seen:
                problems.append(f"duplicate endpoint id '{eid}'")
            seen.add(eid)
    if problems:
        raise RegistryError(problems)
    return endpoints
