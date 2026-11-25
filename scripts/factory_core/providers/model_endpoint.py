"""Model endpoint registry, capability evidence and endpoint preflight (#557).

Configuration-contract owner in docs/hybrid-execution-contract.md (§4.1, §4.3, §4.5, §4.11);
design in docs/archive/2026-11-23-endpoint-registry-secrets-preflight-design.md.

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
import stat
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
# Per-file read cap for a registry tree (#593): larger files fail closed.
MAX_FILE_BYTES = 1024 * 1024

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
        try:
            duplicate = key in seen
        except TypeError:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                "found unhashable key", key_node.start_mark) from None
        if duplicate:
            # The key is never echoed: a secret pasted as a mapping key must not reach a log.
            raise yaml.constructor.ConstructorError(
                None, None, "duplicate key", key_node.start_mark)
        seen.add(key)
    return loader.construct_mapping(node, deep=True)


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


# A YAMLError's own problem text can quote the document (an undefined alias's name), so only
# a fixed phrase per exception class is ever printed. Most specific class wins (__mro__ order).
_YAML_ERROR_PHRASES = {
    yaml.reader.ReaderError: "invalid characters",
    yaml.scanner.ScannerError: "malformed token",
    yaml.parser.ParserError: "syntax error",
    yaml.composer.ComposerError: "undefined or invalid alias/anchor",
    yaml.constructor.ConstructorError: "unsupported value or tag",
}
# _construct_unique_mapping's own problem texts: matched by equality, so they echo nothing.
_OWN_YAML_PROBLEMS = ("found unhashable key", "duplicate key")


def _yaml_error_phrase(e) -> str:
    problem = getattr(e, "problem", None)
    if problem in _OWN_YAML_PROBLEMS:
        return problem
    for cls in type(e).__mro__:
        if cls in _YAML_ERROR_PHRASES:
            return _YAML_ERROR_PHRASES[cls]
    return "parse error"


def _read_file(path) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        raise RegistryError([f"{path}: unreadable ({e.strerror})"]) from None


def _parse_yaml(data, where):
    """Parse YAML from bytes already read, so a caller can hash and parse the same read."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise RegistryError([f"{where}: not valid UTF-8"]) from None
    try:
        return yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 — SafeLoader subclass
    except yaml.YAMLError as e:
        # Never echo the offending line or the parser's own problem text: a pasted secret
        # would land in the log.
        mark = getattr(e, "problem_mark", None)
        line = f" at line {mark.line + 1}" if mark else ""
        raise RegistryError([f"{where}: invalid YAML{line}: {_yaml_error_phrase(e)}"]) from None
    except (ValueError, KeyError, AttributeError, TypeError) as e:
        # An explicit core tag (e.g. !!int) constructs its value via a raw exception —
        # not a YAMLError — whose message quotes the scalar. Same fixed phrase, no text:
        # a pasted secret must not reach the log as a traceback (#605).
        raise RegistryError([f"{where}: invalid YAML: unsupported value or tag"]) from None


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
    return _parse_registry(_read_file(path), path)


def _parse_registry(data, path) -> list[dict]:
    """load_registry() over bytes already read; path only labels the messages."""
    doc = _parse_yaml(data, path)
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


def _read_tree(root) -> tuple[str, dict[str, bytes]]:
    """One walk of a directory tree: its SHA-256 (sorted relative paths and file bytes) and
    the bytes that were hashed, keyed by relative POSIX path (#593).

    A caller that parses these bytes decides on exactly what the digest covers; re-opening a
    file afterwards would reopen the swap-and-swap-back window this closes.
    """
    root = Path(root)
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):  # never descends into a symlink
        entries += [Path(dirpath) / name for name in dirnames + filenames]
    digest = hashlib.sha256()
    files = {}
    for path in sorted(entries):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise RegistryError([f"{root}: {rel} is not a regular file or directory"])
        if path.is_file():
            # The staged tree is writable by run containers: reopen by descriptor (not by
            # path) and re-check the type on the open fd, so a path-to-FIFO swap after the
            # is_file() check above can't block this read (TOCTOU, #593).
            too_big = [f"{root}: {rel} is larger than {MAX_FILE_BYTES} bytes"]
            not_regular = [f"{root}: {rel} is not a regular file or directory"]
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                # Check the type before os.fdopen: it raises on a directory fd without
                # taking ownership of it, which would leak the descriptor.
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise RegistryError(not_regular)
                f = os.fdopen(fd, "rb")
            except BaseException:
                os.close(fd)
                raise
            with f:
                data = f.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise RegistryError(too_big)
            files[rel] = data
            digest.update(rel.encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(data).hexdigest().encode("ascii") + b"\n")
    return digest.hexdigest(), files


def tree_sha256(root) -> str:
    """SHA-256 over a directory tree: sorted relative paths and file bytes."""
    return _read_tree(root)[0]


def staged_registry_dir() -> Path:
    # FACTORY_MODEL_REGISTRY_PATH is a test-only override, never forwarded to run containers.
    # Registry verification must never run from agent-controlled env (#593): only the
    # pre-agent entrypoint.sh preflight and the model proxy's startup (#558 — its own
    # container, whose env the agent never sets) read this.
    return Path(os.environ.get("FACTORY_MODEL_REGISTRY_PATH") or _DEFAULT_STAGED_DIR)


def stage_registry(source=None, dest=None) -> str:
    """Copy the read-only source registry onto the shared volume; return the source's digest.

    The copy goes to a temp directory beside dest and is swapped in by rename, so a failed
    copy never leaves a half-written registry where a run container reads it. The returned
    digest is the read-only source's: a copy that differs from it (a run container shares
    the volume and can write the temp directory) fails staging instead of being blessed.
    """
    source = Path(source) if source else SOURCE_REGISTRY_DIR
    dest = Path(dest) if dest else staged_registry_dir()
    load_registry(source / REGISTRY_FILE)  # never stage an invalid registry
    source_digest = tree_sha256(source)  # rejects symlinks before anything is copied
    if dest.exists():
        try:
            if tree_sha256(dest) == source_digest:
                # Nothing changed: skip the swap so a concurrent dispatch's
                # read_verified_tree() never sees a missing path or a mid-walk
                # FileNotFoundError while this call replaces an identical tree.
                return source_digest
        except (RegistryError, OSError):
            pass  # dest is unreadable/invalid; fall through and re-stage over it
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{dest.name}.tmp-", dir=dest.parent))
    old = dest.with_name(f".{dest.name}.old-{uuid.uuid4().hex}")
    try:
        os.chmod(tmp, 0o755)  # mkdtemp is 0700; run containers must be able to read it
        shutil.copytree(source, tmp, dirs_exist_ok=True)
        staged_digest = tree_sha256(tmp)
        if staged_digest != source_digest:
            raise RegistryError([
                f"{tmp}: staged copy digest does not match the read-only source digest "
                f"({staged_digest} != {source_digest}) — the source or the staged copy changed "
                "mid-staging; refusing to publish a digest that does not bind the source"])
        if dest.exists():
            os.replace(dest, old)
        try:
            os.replace(tmp, dest)
        except OSError:
            if old.exists():
                os.replace(old, dest)
            raise
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    shutil.rmtree(old, ignore_errors=True)
    return source_digest


def read_verified_tree(registry_path) -> tuple[list[str], dict[str, bytes] | None]:
    """The one staged-tree reader (#593): ([], files) only if the bytes read match the digest
    the scheduler passed (Req. 8); (problems, None) otherwise.

    files maps each relative path to the bytes that were hashed. Parse those — never re-open a
    staged file, or a swap-and-swap-back between the two reads goes unseen.
    """
    # Registry verification must never run from agent-controlled env (#593): only the
    # pre-agent entrypoint.sh preflight and the model proxy's startup (#558 — its own
    # container, whose env the agent never sets) read FACTORY_MODEL_REGISTRY_SHA256.
    expected = os.environ.get("FACTORY_MODEL_REGISTRY_SHA256", "")
    if not expected:
        return ["FACTORY_MODEL_REGISTRY_SHA256 is not set — the staged model registry is trusted "
                "only with the digest the scheduler passes to this dispatch"], None
    if not _SHA256_HEX_RE.match(expected):
        return ["FACTORY_MODEL_REGISTRY_SHA256 is not a 64-hex SHA-256 digest"], None
    registry_path = Path(registry_path)
    if not registry_path.is_dir():
        return [f"staged model registry not found at {registry_path} — no fallback to cloud"], None
    try:
        actual, files = _read_tree(registry_path)
    except RegistryError as e:
        return e.problems, None
    except OSError:
        return [f"staged model registry at {registry_path} is unreadable"], None
    if not hmac.compare_digest(actual, expected):
        return [f"staged model registry at {registry_path} does not match "
                "FACTORY_MODEL_REGISTRY_SHA256 — it changed after the scheduler staged it"], None
    return [], files


def registry_config_sha256(entry) -> str:
    """The `registry_config` fingerprint: SHA-256 of the entry's canonical JSON."""
    canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def observed_harness_version():
    """The installed Claude CLI version (`claude --version`), or None if it cannot be read."""
    try:
        out = subprocess.run(["claude", "--version"], capture_output=True, text=True,
                             timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"\d+\.\d+\.\d+", out)
    return m.group(0) if m else None


def load_evidence(evidence_dir, endpoint_id):
    """The capability-evidence record for endpoint_id, or None if there is none."""
    path = Path(evidence_dir) / f"{endpoint_id}.yaml"
    if not path.is_file():
        return None
    return _parse_evidence(_read_file(path), path)


def _parse_evidence(data, path) -> dict:
    """load_evidence() over bytes already read; path only labels the messages."""
    doc = _parse_yaml(data, path)
    if (not isinstance(doc, dict) or set(doc) != {"capability_evidence"}
            or not isinstance(doc["capability_evidence"], dict)):
        raise RegistryError([f"{path}: must contain exactly one capability_evidence mapping"])
    return doc["capability_evidence"]


def _timestamp(value):
    if isinstance(value, datetime):
        ts = value
    elif isinstance(value, str):
        try:
            ts = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    return ts if ts.tzinfo else None


def _evidence_problems(record, entry):
    eid = entry["id"]
    where = f"capability evidence for '{eid}'"
    version, problems = _parse_version(record.get("schema_version"), where)
    if problems:
        return problems
    problems = _unknown_keys(record, _EVIDENCE_KEYS, where, version[1] <= SUPPORTED_MINOR)
    problems += [f"{where}: missing {k}" for k in sorted(_EVIDENCE_REQUIRED - set(record))]
    if problems:
        return problems
    if record["endpoint_id"] != eid:
        problems.append(f"{where}: endpoint_id does not match the registry entry")
    if not isinstance(record["certified_by"], str) or not record["certified_by"].strip():
        problems.append(f"{where}: certified_by must name the certifying operator")
    for field in ("certified_at", "expires_at"):
        if _timestamp(record[field]) is None:
            problems.append(f"{where}: {field} must be an ISO 8601 timestamp with a timezone")
    if not isinstance(record["model_identity"], dict):
        problems.append(f"{where}: model_identity must be a mapping")
    results = record["results"]
    if not isinstance(results, dict) or any(v not in RESULT_VALUES for v in results.values()):
        problems.append(f"{where}: results values must be one of {', '.join(RESULT_VALUES)}")
    roles = record["qualified_roles"]
    if not isinstance(roles, list) or not roles or any(r != "actor" for r in roles):
        problems.append(f"{where}: qualified_roles may only list actor (contract §4.4/§4.5)")
    names = record["invalidated_by"]
    if not isinstance(names, list) or any(n not in FINGERPRINTS for n in names):
        problems.append(f"{where}: invalidated_by may only list {', '.join(FINGERPRINTS)}")
    return problems


def evidence_state(record, entry, now=None):
    """Derived evidence state (contract §4.5) and the reasons behind it. Never stored.

    unknown: no record, or a fingerprint #557 cannot resolve without dialing the endpoint.
    declared: a local entry exists but its record is not valid. stale: expired, or a resolved
    fingerprint differs. tested: none of these.
    """
    eid = entry["id"]
    if record is None:
        return "unknown", [f"endpoint '{eid}': no capability-evidence record"]
    problems = _evidence_problems(record, entry)
    if problems:
        return "declared", problems
    stale = []
    if _timestamp(record["expires_at"]) <= (now or datetime.now(timezone.utc)):
        stale.append("expires_at has passed")
    observed = {
        "model_digest": entry["model_identity"]["digest_or_revision"],
        "harness_version": observed_harness_version(),
        "fixture_suite_version": PINNED_FIXTURE_SUITE_VERSION,
        "registry_config": registry_config_sha256(entry),
    }
    recorded = {
        "model_digest": record["model_identity"].get("digest_or_revision"),
        "harness_version": record["harness_version"],
        "fixture_suite_version": record["fixture_suite_version"],
        "registry_config": record.get("registry_config"),
    }
    # fixture_suite_version is always compared (§4.5), listed in invalidated_by or not.
    unresolved = []
    for name in dict.fromkeys([*record["invalidated_by"], "fixture_suite_version"]):
        if observed.get(name) is None or recorded.get(name) is None:
            unresolved.append(name)
        elif str(recorded[name]) != str(observed[name]):
            stale.append(f"{name} differs from what is observed")
    if unresolved:
        return "unknown", [f"endpoint '{eid}': cannot resolve fingerprint(s) "
                           f"{', '.join(unresolved)} — an unobserved fingerprint never matches"]
    if stale:
        return "stale", [f"endpoint '{eid}': capability evidence is stale ({'; '.join(stale)})"]
    return "tested", []


def role_problems(record, role="actor"):
    """A role is eligible only if every dimension it requires is `pass` (contract §4.5)."""
    if role not in record["qualified_roles"]:
        return [f"capability evidence for '{record['endpoint_id']}' does not qualify role {role}"]
    return [f"capability evidence for '{record['endpoint_id']}': {dim} is "
            f"{record['results'].get(dim, 'not_tested')}, not pass"
            for dim in ACTOR_REQUIRED_DIMENSIONS if record["results"].get(dim) != "pass"]


def _pick(endpoints, endpoint_id, path):
    for entry in endpoints:
        if entry["id"] == endpoint_id:
            return entry, []
    return None, [f"Unknown FACTORY_MODEL_ENDPOINT '{endpoint_id}' — no such id in {path}; "
                  "no other endpoint is chosen instead"]


def _select(registry_path, endpoint_id):
    path = Path(registry_path) / REGISTRY_FILE
    try:
        endpoints = load_registry(path)
    except RegistryError as e:
        return None, e.problems
    return _pick(endpoints, endpoint_id, path)


def _local_problems(entry, files, registry_path):
    eid = entry["id"]
    problems = []
    present = sorted(n for n in CLOUD_CREDENTIALS
                     if os.environ.get(n) and n != entry["secret_reference"])
    if present:
        problems.append(f"endpoint '{eid}' is local but cloud model credential(s) are set in this "
                        f"container: {', '.join(present)} — a local-routed container holds none "
                        "(contract §4.3)")
    if os.environ.get("ANTHROPIC_BASE_URL") != entry["base_url"]:
        problems.append(f"endpoint '{eid}': ANTHROPIC_BASE_URL must equal the entry's base_url "
                        "(contract §4.11 decision 1)")
    served = entry["model_identity"]["served_name"]
    for alias in TIER_ALIASES:
        if os.environ.get(alias) != served:
            problems.append(f"endpoint '{eid}': {alias} must equal model_identity.served_name "
                            f"'{served}' (contract §4.11 decision 2)")
    # The evidence record comes from the verified tree's bytes, never a separate read (#593).
    rel = f"{EVIDENCE_DIR}/{eid}.yaml"
    try:
        record = _parse_evidence(files[rel], registry_path / rel) if rel in files else None
    except RegistryError as e:
        problems += e.problems
    else:
        state, reasons = evidence_state(record, entry)
        if state != "tested":
            problems.append(f"endpoint '{eid}': capability evidence is '{state}', not 'tested' "
                            "(contract §4.5)")
            problems += reasons
        else:
            problems += role_problems(record, "actor")
    problems.append(_NO_LOCAL_DISPATCH.format(eid=eid))
    return problems


def preflight(registry_path, endpoint_id) -> tuple[list[str], str | None]:
    """Run-container check of the staged registry for one selected endpoint.

    Returns (problems, hosting); ([], hosting) == OK. hosting is the selected entry's
    hosting_location, or None if no entry was selected. Every decision is made on the bytes
    read_verified_tree() hashed, so no file is read twice (#593).
    """
    registry_path = Path(registry_path)
    problems, files = read_verified_tree(registry_path)
    if problems:
        return problems, None
    path = registry_path / REGISTRY_FILE
    if REGISTRY_FILE not in files:
        return [f"model endpoint registry not found at {path}"], None
    try:
        endpoints = _parse_registry(files[REGISTRY_FILE], path)
    except RegistryError as e:
        return e.problems, None
    entry, problems = _pick(endpoints, endpoint_id, path)
    if entry is None:
        return problems, None
    hosting = entry["hosting_location"]
    ref = entry["secret_reference"]
    if hosting == "cloud":
        problems += _missing([ref])  # a cloud entry's secret arrives via .archon/.env
    elif not os.environ.get(ref):
        # Not _missing(): a local secret must not be delivered through .archon/.env (spec §C).
        problems.append(f"{ref} (secret_reference of endpoint '{entry['id']}') is not set")
    if hosting == "local":
        problems += _local_problems(entry, files, registry_path)
    return problems, hosting


def load_verified_entry(registry_path, endpoint_id) -> tuple[list[str], dict | None]:
    """The model proxy's Configuration-contract loader (#558): (problems, entry).

    entry is non-None only when problems is empty. Exactly one read_verified_tree() call;
    registry, entry and evidence are parsed only from the bytes it hashed (#593) — never
    _select(), load_registry() or a second read. _NO_LOCAL_DISPATCH is not filtered, so a
    local entry never comes back until #587 lifts it.
    """
    registry_path = Path(registry_path)
    problems, files = read_verified_tree(registry_path)
    if problems:
        return problems, None
    path = registry_path / REGISTRY_FILE
    if REGISTRY_FILE not in files:
        return [f"model endpoint registry not found at {path}"], None
    try:
        endpoints = _parse_registry(files[REGISTRY_FILE], path)
    except RegistryError as e:
        return e.problems, None
    entry, problems = _pick(endpoints, endpoint_id, path)
    if entry is None:
        return problems, None
    if entry["hosting_location"] == "local":
        problems = problems + _local_problems(entry, files, registry_path)
    return (problems, None) if problems else ([], entry)


def scheduler_preflight(registry_path, endpoint_id) -> tuple[list[str], str | None]:
    """Scheduler boot check on its read-only source mount: schema, selected id, §4.11 decision 3.

    Returns (problems, hosting), like preflight(). The digest, secret presence and
    credential/alias guards are the run container's: it holds the environment they check.
    """
    entry, problems = _select(registry_path, endpoint_id)
    hosting = entry["hosting_location"] if entry else None
    if hosting == "local":
        problems.append(_NO_LOCAL_DISPATCH.format(eid=entry["id"]))
    return problems, hosting


def effective_payload_class(declared, hosting_location) -> str:
    """Contract §4.3: local is always local_only; cloud is the declared class, else cloud_ok."""
    if declared is not None and declared not in PAYLOAD_CLASSES:
        raise ValueError(f"declared payload_class {declared!r} is not one of cloud_ok, local_only")
    if hosting_location == "local":
        return "local_only"
    if hosting_location == "cloud":
        return declared or "cloud_ok"
    raise ValueError(f"hosting_location {hosting_location!r} is not one of local, cloud")


def dispatch_allowed(effective_class, accepts_payload_classes) -> bool:
    return effective_class in accepts_payload_classes


def payload_decision(declared, entry) -> dict:
    """The §4.3 dispatch-time decision, shaped for the run-record ledger (#558 records it)."""
    decision = {"payload_class": None, "endpoint_id": entry.get("id"),
                "secret_reference": entry.get("secret_reference"), "allow": False, "reason": ""}
    accepts = entry.get("accepts_payload_classes")
    if not isinstance(accepts, list) or not accepts or any(c not in PAYLOAD_CLASSES for c in accepts):
        decision["reason"] = "registry entry has missing or invalid accepts_payload_classes"
        return decision
    try:
        effective = effective_payload_class(declared, entry.get("hosting_location"))
    except ValueError as e:
        decision["reason"] = str(e)
        return decision
    decision["payload_class"] = effective
    decision["allow"] = dispatch_allowed(effective, accepts)
    decision["reason"] = ("effective class accepted by endpoint" if decision["allow"] else
                          f"effective class {effective} is not in the endpoint's "
                          "accepts_payload_classes")
    return decision


def base_url_scope(url) -> str:
    """same-host | lan | remote — informational only, never changes validation."""
    host = urlsplit(url).hostname or ""
    if host in ("localhost", "host.docker.internal"):
        return "same-host"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "lan" if host.endswith(".local") or "." not in host else "remote"
    if ip.is_loopback:
        return "same-host"
    if ip.is_private or ip.is_link_local:
        return "lan"
    return "remote"


def redacted_view(registry_path) -> dict:
    """Effective configuration with each secret_reference reduced to its name and presence.

    Path-based, for the scheduler's read-only source mount (--from-source); the staged copy
    goes through redacted_view_staged().
    """
    path = Path(registry_path) / REGISTRY_FILE
    return _redact(load_registry(path), path)


def redacted_view_staged(registry_path) -> tuple[list[str], dict | None]:
    """redacted_view() of the staged copy, parsed from the bytes read_verified_tree() hashed.

    Returns (problems, view); view is None whenever problems is non-empty.
    """
    registry_path = Path(registry_path)
    problems, files = read_verified_tree(registry_path)
    if problems:
        return problems, None
    path = registry_path / REGISTRY_FILE
    if REGISTRY_FILE not in files:
        return [f"model endpoint registry not found at {path}"], None
    try:
        endpoints = _parse_registry(files[REGISTRY_FILE], path)
    except RegistryError as e:
        return e.problems, None
    return [], _redact(endpoints, path)


def _known_keys(obj, allowed) -> tuple[dict, int]:
    """obj's allowed keys, in obj's order, and how many other keys were left out."""
    known = {k: v for k, v in obj.items() if k in allowed}
    return known, len(obj) - len(known)


def _redact(endpoints, path) -> dict:
    """The one place that decides what of a registry is safe to print."""
    views = []
    for entry in endpoints:
        # An allowlist at every level _unknown_keys tolerates: a newer-minor file's unknown
        # keys (a future api_key, say) are counted, never printed.
        view, omitted = _known_keys(entry, _ENTRY_KEYS)
        for name, allowed in (("model_identity", _MODEL_IDENTITY_KEYS), ("timeouts", TIMEOUT_FIELDS)):
            if isinstance(view.get(name), dict):
                view[name], nested = _known_keys(view[name], allowed)
                omitted += nested
        ref = view.pop("secret_reference")
        view["secret_reference"] = {"name": ref, "present": bool(os.environ.get(ref))}
        if "base_url" in entry:
            view["base_url_scope"] = base_url_scope(entry["base_url"])
        if omitted:
            view["unknown_keys_omitted"] = omitted
        views.append(view)
    return {"registry": str(path), "endpoints": views}
