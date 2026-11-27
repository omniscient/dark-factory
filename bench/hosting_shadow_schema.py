"""Shared definitions for the hosting shadow comparator (#563).

Spec: docs/superpowers/specs/2026-11-27-shadow-hosting-economics-comparator-design.md.

No network call and no subprocess: constants, file/row validation, digests, prompt
rendering, verdict parsing, the rules-only reading of the rubric, the outside-git-tree
check and the run order. shadow_runner.py and compare_hosting_variants.py import this
module; _shadow_worker.py does not.
"""
import fnmatch
import hashlib
import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import yaml

_SCRIPTS = str(Path(__file__).resolve().parents[1] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from factory_core.providers import model_endpoint  # noqa: E402

SCHEMA_VERSION = "1.0"
# The "harness" identity of this replay (no Claude CLI is involved). Bump it whenever
# rules_only_verdict's reading of the rubric, the row shape or the request changes.
RUNNER_VERSION = "1"
PROMPT_SUBSTITUTIONS_VERSION = "1"
ARM_KINDS = ("rules_only", "local")
PROVENANCES = ("historical", "synthetic")
SPLITS = ("dev", "held_out")
PREREGISTRATION_STATUSES = ("example", "registered")
ORACLE_QUESTION = ("Would this changeset need live endpoint validation to catch a "
                   "regression?")
INVALID_RUN_HANDLING = ("all rows retained; schema_invalid and infrastructure_invalid count "
                        "in every denominator; no row is dropped or rerun")
REQUEST_PARAMS = {"max_tokens": 512, "temperature": 0, "connect_seconds": 10,
                  "total_seconds": 300}
UNSUPPORTED_MEASUREMENTS = ("queue_ms", "ttft_ms", "prefill_ms", "decode_ms",
                            "peak_memory_bytes", "oom")
USAGE_KEYS = ("gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
              "gen_ai.usage.cache_read_input_tokens",
              "gen_ai.usage.cache_creation_input_tokens")
ROW_KEYS = (
    "schema_version", "runner_version", "fixture_id", "arm_id", "arm_kind", "repetition",
    "run_order_seed", "run_order_position", "cold_or_warm", "concurrency", "rubric_sha",
    "fixture_set_digest", "prompt_substitutions_version", "preview_enabled",
    "request_params", "endpoint_id", "hosting_location", "role", "model_identity",
    "runtime_stack", "hardware", "identity_source", "served_name_verified", "http_status",
    "parse_status", "predicted_needs_preview", "failure_class", "worker_error",
    "response_text", *USAGE_KEYS, "duration_ms", "unsupported_measurements", "cost_usd",
    "energy_joules", "energy_provenance", "started_at", "completed_at",
)

RUBRIC_PATH = Path(__file__).resolve().parent / "fixtures" / "preview_classifier" / "rubric.pinned.md"
RUBRIC_DELIMITER = "---8<--- prompt below ---"
CHANGESET_TARGET = "$preview-changeset.output"
TITLE_TARGET = "$fetch-issue.output.title"
LABELS_TARGET = "$fetch-issue.output.labels"
# Deliberate substitution 1 (kill-switch): the replay has no file to read and no tools.
KILL_SWITCH_LINE = "Read the preview.enabled flag from .claude/skills/refinement/config.yaml."
KILL_SWITCH_STATED = "preview.enabled is true (stated here; there is no file to read)."
# Deliberate substitution 2 (output contract): the local path refuses output_format.
OUTPUT_CONTRACT_SUFFIX = (
    'Respond with only a JSON object and nothing else: {"needs_preview": <true|false>, '
    '"category": "<code|docs|config|tests|ci|mixed>", "reason": "<one sentence>"}')

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")  # model_endpoint._SECRET_NAME_RE's rule
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REPO_TOKENS = ("GH_TOKEN", "GITHUB_TOKEN")
# Names the worker environment already uses: an arm's secret must not shadow one.
_WORKER_RESERVED = ("PATH", "HOME", "SHADOW_BASE_URL")

# rules_only_verdict's reading of the classify-preview rubric (runner_version 1).
FORCE_TRUE_PREFIXES = ("backend/app/", "alembic/versions/", "frontend/src/",
                       "dark-factory/seed/")
FORCE_TRUE_NAMES = ("requirements.txt", "package.json", "package-lock.json", "Dockerfile*",
                    "docker-compose*", ".env*")
SKIP_PREFIXES = ("docs/", "Docs/", ".archon/", ".claude/", "backend/tests/",
                 "dark-factory/tests/", ".github/")
SKIP_WORKFLOW_NAMES = ("*.yaml", "*.yml")
SKIP_NAMES = ("*.md", "*.mdx", "LICENSE", "*.test.ts", "*.test.tsx", "*.spec.ts",
              "*.spec.tsx", "conftest.py", "*_test.py", ".gitignore",
              ".pre-commit-config.yaml", ".eslintrc*", ".prettierrc*", "ruff.toml",
              ".ruff.toml", ".flake8", "mypy.ini", ".mypy.ini", ".editorconfig")


class ShadowSchemaError(Exception):
    """A fixtures/labels/arms/preregistration/rows problem: names the file, the key path and
    the rule — never a value, so a base_url or a secret is never echoed."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """safe_load that refuses a duplicate mapping key instead of keeping the last one."""


def _unique_mapping(loader, node, deep=False):
    keys = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in keys:
            raise yaml.constructor.ConstructorError(None, None, "duplicate key",
                                                    key_node.start_mark)
        keys.add(key)
    return loader.construct_mapping(node, deep=deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
                                 _unique_mapping)


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load_yaml(path):
    try:
        return yaml.load(Path(path).read_bytes(), Loader=_UniqueKeyLoader)  # noqa: S506 (safe loader)
    except yaml.YAMLError as e:
        problem = getattr(e, "problem", None) or "not valid YAML"
        raise ShadowSchemaError(f"{path}: {problem} (yaml.safe_load rules, unique keys)") from None
    except OSError:
        raise ShadowSchemaError(f"{path}: cannot be read") from None


def _mapping(obj, where, required, optional=()):
    if not isinstance(obj, dict):
        raise ShadowSchemaError(f"{where}: must be a mapping")
    unknown = sorted(str(k) for k in obj if k not in required and k not in optional)
    if unknown:
        raise ShadowSchemaError(f"{where}: unknown key(s) {', '.join(unknown)}")
    missing = [k for k in required if k not in obj]
    if missing:
        raise ShadowSchemaError(f"{where}: missing required key(s) {', '.join(missing)}")
    return obj


def _str(value, where, *, empty_ok=False):
    if not isinstance(value, str) or (not empty_ok and not value):
        raise ShadowSchemaError(f"{where}: must be a {'' if empty_ok else 'non-empty '}string")
    return value


def _int(value, where, *, minimum=None):
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ShadowSchemaError(f"{where}: must be an integer"
                                + (f" >= {minimum}" if minimum is not None else ""))
    return value


def _bool(value, where):
    if type(value) is not bool:
        raise ShadowSchemaError(f"{where}: must be true or false")
    return value


def _rate(value, where):
    if type(value) not in (int, float) or not 0 <= value <= 1:
        raise ShadowSchemaError(f"{where}: must be a number between 0 and 1")
    return value


def _str_list(value, where):
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ShadowSchemaError(f"{where}: must be a list of non-empty strings")
    return value


def _choice(value, where, allowed):
    if value not in allowed:
        raise ShadowSchemaError(f"{where}: must be one of {', '.join(allowed)}")
    return value


def parse_timestamp(value, where) -> datetime:
    """An ISO8601 string with a timezone (quote it in YAML so safe_load keeps it a string)."""
    _str(value, where)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ShadowSchemaError(f"{where}: must be an ISO8601 timestamp") from None
    if parsed.tzinfo is None:
        raise ShadowSchemaError(f"{where}: must carry a timezone")
    return parsed


def _sha(value, where):
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        raise ShadowSchemaError(f"{where}: must be a lowercase sha256 hex digest")
    return value


def load_fixtures(path) -> dict:
    """The inputs file: what a model may see, and nothing else."""
    doc = _mapping(_load_yaml(path), f"{path}", ("schema_version", "preview_enabled",
                                                 "fixtures"))
    _choice(doc["schema_version"], f"{path}: schema_version", (SCHEMA_VERSION,))
    if doc["preview_enabled"] is not True:
        raise ShadowSchemaError(f"{path}: preview_enabled must be true in v1")
    if not isinstance(doc["fixtures"], list) or not doc["fixtures"]:
        raise ShadowSchemaError(f"{path}: fixtures must be a non-empty list")
    seen = set()
    for i, fx in enumerate(doc["fixtures"]):
        where = f"{path}: fixtures[{i}]"
        _mapping(fx, where, ("fixture_id", "provenance", "split", "name_only", "stat", "title",
                             "labels", "labels_source"), ("source",))
        fid = _str(fx["fixture_id"], f"{where}.fixture_id")
        if fid in seen:
            raise ShadowSchemaError(f"{where}.fixture_id: duplicate fixture_id")
        seen.add(fid)
        _choice(fx["provenance"], f"{where}.provenance", PROVENANCES)
        _choice(fx["split"], f"{where}.split", SPLITS)
        if not isinstance(fx["name_only"], list) or not all(
                isinstance(p, str) and p for p in fx["name_only"]):
            raise ShadowSchemaError(f"{where}.name_only: must be a list of non-empty paths")
        _str(fx["stat"], f"{where}.stat", empty_ok=True)
        _str(fx["title"], f"{where}.title", empty_ok=True)
        if not isinstance(fx["labels"], list) or not all(
                isinstance(v, str) and v for v in fx["labels"]):
            raise ShadowSchemaError(f"{where}.labels: must be a list of non-empty strings")
        if fx["provenance"] == "historical":
            _choice(fx["labels_source"], f"{where}.labels_source",
                    ("as_of_pr_created", "dropped"))
            if fx["labels_source"] == "dropped" and fx["labels"]:
                raise ShadowSchemaError(f"{where}.labels: must be empty when labels_source is "
                                        "dropped")
            if "source" not in fx:
                raise ShadowSchemaError(f"{where}: a historical fixture requires source")
            src = _mapping(fx["source"], f"{where}.source", ("pr", "merge_sha", "issue"))
            _int(src["pr"], f"{where}.source.pr", minimum=1)
            _str(src["merge_sha"], f"{where}.source.merge_sha")
            _int(src["issue"], f"{where}.source.issue", minimum=1)
        else:
            _choice(fx["labels_source"], f"{where}.labels_source", ("synthetic",))
            if "source" in fx:
                raise ShadowSchemaError(f"{where}: a synthetic fixture carries no source")
    return doc


def load_labels(path, fixture_ids) -> dict:
    """The oracle. Only the comparator opens this file."""
    doc = _mapping(_load_yaml(path), f"{path}", ("schema_version", "oracle_question", "labels"))
    _choice(doc["schema_version"], f"{path}: schema_version", (SCHEMA_VERSION,))
    if doc["oracle_question"] != ORACLE_QUESTION:
        raise ShadowSchemaError(f"{path}: oracle_question must be the pinned question in "
                                "bench/fixtures/preview_classifier/README.md")
    labels = doc["labels"]
    if not isinstance(labels, dict):
        raise ShadowSchemaError(f"{path}: labels must be a mapping of fixture_id to label")
    for fid, label in labels.items():
        where = f"{path}: labels.{fid}"
        if fid not in fixture_ids:
            raise ShadowSchemaError(f"{where}: no fixture with this fixture_id")
        _mapping(label, where, ("oracle_label", "expected_fail_safe", "oracle_rationale",
                                "labeled_by", "labeled_at"))
        _bool(label["oracle_label"], f"{where}.oracle_label")
        _bool(label["expected_fail_safe"], f"{where}.expected_fail_safe")
        if label["expected_fail_safe"] and not label["oracle_label"]:
            raise ShadowSchemaError(f"{where}: expected_fail_safe: true implies "
                                    "oracle_label: true")
        _str(label["oracle_rationale"], f"{where}.oracle_rationale")
        _str(label["labeled_by"], f"{where}.labeled_by")
        parse_timestamp(label["labeled_at"], f"{where}.labeled_at")
    missing = sorted(set(fixture_ids) - set(labels))
    if missing:
        raise ShadowSchemaError(f"{path}: labels: no label for fixture(s) {', '.join(missing)}")
    return doc


def validate_base_url(url, where) -> None:
    """model_endpoint._validate_base_url's rules (private there, so re-implemented): http(s)
    with a host, no embedded credentials, no query or fragment. Never echoes the value."""
    if not isinstance(url, str) or not url:
        raise ShadowSchemaError(f"{where}: base_url is required for a local arm")
    try:
        parts = urlsplit(url)
        parts.port  # raises ValueError on a malformed port
    except ValueError:
        raise ShadowSchemaError(f"{where}: base_url is not a well-formed URL") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ShadowSchemaError(f"{where}: base_url must be an http(s) URL with a host")
    if parts.username is not None or parts.password is not None:
        raise ShadowSchemaError(f"{where}: base_url must not embed credentials — use "
                                "secret_env")
    if parts.query or parts.fragment:
        raise ShadowSchemaError(f"{where}: base_url must not carry a query string or fragment")


def load_arms(path) -> list:
    doc = _mapping(_load_yaml(path), f"{path}", ("arms",))
    arms = doc["arms"]
    if not isinstance(arms, list) or not arms:
        raise ShadowSchemaError(f"{path}: arms must be a non-empty list")
    seen = set()
    for i, arm in enumerate(arms):
        where = f"{path}: arms[{i}]"
        if not isinstance(arm, dict):
            raise ShadowSchemaError(f"{where}: must be a mapping")
        _choice(arm.get("arm_kind"), f"{where}.arm_kind", ARM_KINDS)
        if arm["arm_kind"] == "rules_only":
            _mapping(arm, where, ("arm_id", "arm_kind"))
        else:
            _mapping(arm, where, ("arm_id", "arm_kind", "endpoint_id", "base_url", "secret_env",
                                  "model_identity", "runtime_stack", "hardware"))
            if not isinstance(arm["endpoint_id"], str) or not _ID_RE.match(arm["endpoint_id"]):
                raise ShadowSchemaError(f"{where}.endpoint_id: must match {_ID_RE.pattern}")
            validate_base_url(arm["base_url"], f"{where}.base_url")
            secret = arm["secret_env"]
            if not isinstance(secret, str) or not _SECRET_NAME_RE.match(secret):
                raise ShadowSchemaError(f"{where}.secret_env: must name an environment variable "
                                        f"({_SECRET_NAME_RE.pattern})")
            if secret in model_endpoint.CLOUD_CREDENTIALS:
                raise ShadowSchemaError(f"{where}.secret_env: {secret} is a cloud model "
                                        "credential; a local arm never carries one")
            if secret in REPO_TOKENS or secret in _WORKER_RESERVED:
                raise ShadowSchemaError(f"{where}.secret_env: {secret} is reserved")
            mi = _mapping(arm["model_identity"], f"{where}.model_identity",
                          ("served_name", "digest_or_revision", "quantisation"))
            for key in mi:
                _str(mi[key], f"{where}.model_identity.{key}")
            rs = _mapping(arm["runtime_stack"], f"{where}.runtime_stack",
                          ("name", "version", "chat_template", "context_settings"))
            for key in ("name", "version", "chat_template"):
                _str(rs[key], f"{where}.runtime_stack.{key}")
            cs = _mapping(rs["context_settings"], f"{where}.runtime_stack.context_settings",
                          ("context_tokens",))
            _int(cs["context_tokens"], f"{where}.runtime_stack.context_settings.context_tokens",
                 minimum=1)
            _str(arm["hardware"], f"{where}.hardware")
        if not isinstance(arm["arm_id"], str) or not _ID_RE.match(arm["arm_id"]):
            raise ShadowSchemaError(f"{where}.arm_id: must match {_ID_RE.pattern}")
        if arm["arm_id"] in seen:
            raise ShadowSchemaError(f"{where}.arm_id: duplicate arm_id")
        seen.add(arm["arm_id"])
    if sum(1 for a in arms if a["arm_kind"] == "rules_only") != 1:
        raise ShadowSchemaError(f"{path}: arms must contain exactly one rules_only arm")
    return arms


def load_preregistration(path) -> dict:
    doc = _mapping(_load_yaml(path), f"{path}", ("preview_classifier_preregistration",))
    where = f"{path}: preview_classifier_preregistration"
    pre = _mapping(doc["preview_classifier_preregistration"], where, (
        "schema_version", "status", "registered_by", "registered_at", "fixture_set_digest",
        "oracle_label_digest", "rubric_sha", "repetitions", "seed", "criteria",
        "invalid_run_handling"))
    _choice(pre["schema_version"], f"{where}.schema_version", (SCHEMA_VERSION,))
    _choice(pre["status"], f"{where}.status", PREREGISTRATION_STATUSES)
    _str(pre["registered_by"], f"{where}.registered_by")
    parse_timestamp(pre["registered_at"], f"{where}.registered_at")
    for key in ("fixture_set_digest", "oracle_label_digest", "rubric_sha"):
        _sha(pre[key], f"{where}.{key}")
    _int(pre["repetitions"], f"{where}.repetitions", minimum=1)
    _int(pre["seed"], f"{where}.seed")
    crit = _mapping(pre["criteria"], f"{where}.criteria", (
        "max_false_skips_held_out", "max_false_skip_rate", "min_schema_validity_rate",
        "min_coverage", "p95_duration_ms_slo", "min_false_preview_rate_reduction_vs_rules_only"))
    _int(crit["max_false_skips_held_out"], f"{where}.criteria.max_false_skips_held_out",
         minimum=0)
    for key in ("max_false_skip_rate", "min_schema_validity_rate", "min_coverage",
                "min_false_preview_rate_reduction_vs_rules_only"):
        _rate(crit[key], f"{where}.criteria.{key}")
    _int(crit["p95_duration_ms_slo"], f"{where}.criteria.p95_duration_ms_slo", minimum=1)
    if pre["invalid_run_handling"] != INVALID_RUN_HANDLING:
        raise ShadowSchemaError(f"{where}.invalid_run_handling: must be the pinned v1 rule")
    return pre


def load_rubric(path=RUBRIC_PATH) -> dict:
    """rubric.pinned.md: a header, the delimiter line, then the classify-preview prompt exactly
    as yaml.safe_load returns it. rubric_sha is the sha256 of the whole file's bytes."""
    data = Path(path).read_bytes()
    head, sep, prompt = data.decode("utf-8").partition(RUBRIC_DELIMITER + "\n")
    if not sep:
        raise ShadowSchemaError(f"{path}: missing the delimiter line {RUBRIC_DELIMITER!r}")
    commit = re.search(r"^source_commit: ([0-9a-f]{40})$", head, re.MULTILINE)
    if not commit:
        raise ShadowSchemaError(f"{path}: header has no source_commit: <40-hex sha> line")
    return {"prompt": prompt, "rubric_sha": hashlib.sha256(data).hexdigest(),
            "source_commit": commit.group(1)}


def _changeset_block(fixture) -> str:
    lines = ["=== Changed files (main...HEAD) ===", *fixture["name_only"], "=== Stat ===",
             fixture["stat"].rstrip("\n")]
    return "\n".join(lines)


def render_prompt(rubric_text, fixture) -> str:
    """The four targets, each required exactly once in the unrendered text, replaced in one
    re.sub pass so substituted fixture text is never re-scanned; then the output contract."""
    replacements = {
        CHANGESET_TARGET: _changeset_block(fixture),
        TITLE_TARGET: fixture["title"],
        LABELS_TARGET: json.dumps(fixture["labels"]),  # deliberate substitution 3: names only
        KILL_SWITCH_LINE: KILL_SWITCH_STATED,
    }
    for target in replacements:
        count = rubric_text.count(target)
        if count != 1:
            raise ShadowSchemaError(f"rubric: {target!r} occurs {count} times; exactly once is "
                                    "required")
    pattern = re.compile("|".join(re.escape(t) for t in sorted(replacements, key=len,
                                                                 reverse=True)))
    rendered = pattern.sub(lambda m: replacements[m.group(0)], rubric_text)
    if not rendered.endswith("\n"):
        rendered += "\n"
    return rendered + "\n" + OUTPUT_CONTRACT_SUFFIX


def _reject_duplicates(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


def _reject_constant(name):
    raise ValueError(f"non-finite constant {name}")


def parse_verdict(text):
    """True/False when `text` is exactly a JSON object whose needs_preview is a boolean; None
    (schema_invalid) otherwise. No fence stripping, no salvage, no partial credit."""
    if not isinstance(text, str):
        return None
    try:
        obj = json.loads(text.strip(), object_pairs_hook=_reject_duplicates,
                         parse_constant=_reject_constant)
    except ValueError:
        return None
    if not isinstance(obj, dict) or type(obj.get("needs_preview")) is not bool:
        return None
    return obj["needs_preview"]


def _final(path) -> str:
    return path.rsplit("/", 1)[-1]


def _any_name(path, patterns) -> bool:
    return any(fnmatch.fnmatchcase(_final(path), p) for p in patterns)


def _force_true(path) -> bool:
    return path.startswith(FORCE_TRUE_PREFIXES) or _any_name(path, FORCE_TRUE_NAMES)


def _skip_only(path) -> bool:
    if path.startswith(SKIP_PREFIXES) or _any_name(path, SKIP_NAMES):
        return True
    return path.startswith("workflows/") and _any_name(path, SKIP_WORKFLOW_NAMES)


def rules_only_verdict(name_only) -> bool:
    """One deterministic reading of the rubric (runner_version 1), not "the rubric": case-
    sensitive, no normalisation; `dir/**` is a root prefix test; other patterns match the final
    segment at any depth. Empty → true; force-true first; false only if every path is
    skip-only; otherwise true. Title and labels are ignored."""
    if not name_only:
        return True
    if any(_force_true(p) for p in name_only):
        return True
    return not all(_skip_only(p) for p in name_only)


def inside_git_tree(path) -> bool:
    """True when the resolved path or any ancestor directory holds a .git entry."""
    resolved = Path(path).resolve()
    return any((d / ".git").exists() for d in (resolved, *resolved.parents))


def run_order(fixture_ids, arm_ids, repetitions, seed) -> list:
    """Per repetition r: shuffle the sorted fixture ids with random.Random(f"{seed}:{r}"); the
    fixture at shuffled index i visits the sorted arms rotated left by (i + r) % len(arms)."""
    arms = sorted(arm_ids)
    units = []
    for r in range(repetitions):
        fixtures = sorted(fixture_ids)
        random.Random(f"{seed}:{r}").shuffle(fixtures)
        for i, fid in enumerate(fixtures):
            k = (i + r) % len(arms)
            for arm_id in arms[k:] + arms[:k]:
                units.append({"fixture_id": fid, "arm_id": arm_id, "repetition": r,
                              "run_order_position": len(units)})
    return units
