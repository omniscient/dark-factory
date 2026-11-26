"""certification_result emitter and the suite-version digest (#561).

A certification_result is what one suite run found. It is never the per-endpoint
capability-evidence record (contract §4.5): its top-level key differs, so
model_endpoint.load_evidence() rejects it, and it carries none of the record's own fields
(endpoint_id, certified_*, expires_at, invalidated_by) nor the raw base URL.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from factory_core.providers import model_endpoint

RESULT_KEY = "certification_result"
SCHEMA_VERSION = "1.0"
TARGET_KINDS = ("mock", "endpoint")
PACKAGE_DIR = Path(__file__).resolve().parent


def suite_digest(package_dir=PACKAGE_DIR, fixtures_dir=None) -> str:
    """SHA-256 over fixtures/ and every top-level module: PINNED_FIXTURE_SUITE_VERSION.

    The glob is not recursive, so __pycache__/ is never read; fixtures/ holds no Python.
    fixtures_dir is the tree a run actually loaded when it is not package_dir's own.
    """
    package_dir = Path(package_dir)
    fixtures = package_dir / "fixtures" if fixtures_dir is None else Path(fixtures_dir)
    digest = hashlib.sha256()
    digest.update(model_endpoint.tree_sha256(fixtures).encode())
    for path in sorted(package_dir.glob("*.py")):  # every module: a new one is never unhashed
        digest.update(path.name.encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode() + b"\n")
    return digest.hexdigest()


def build_result(*, target_kind, base_url, results, harness_pending, controls,
                 fixture_suite_version, now=None) -> dict:
    """The certification_result mapping. Only base_url's scope is recorded, never the URL.

    fixture_suite_version is the digest of the suite that ran (suite_digest()), never the
    committed pin taken on trust: a modified suite must not claim the pinned version.
    """
    if target_kind not in TARGET_KINDS:
        raise ValueError(f"target_kind must be one of {', '.join(TARGET_KINDS)}")
    if (tuple(results) != model_endpoint.ACTOR_REQUIRED_DIMENSIONS
            or any(v not in model_endpoint.RESULT_VALUES for v in results.values())):
        raise ValueError("results must map each ACTOR_REQUIRED_DIMENSIONS key to a RESULT_VALUES "
                         "value")
    return {RESULT_KEY: {
        "schema_version": SCHEMA_VERSION,
        "fixture_suite_version": fixture_suite_version,
        "harness_version": model_endpoint.observed_harness_version(),
        "target_kind": target_kind,
        "base_url_scope": model_endpoint.base_url_scope(base_url),
        "results": dict(results),
        "harness_pending": list(harness_pending),
        "controls": [dict(c) for c in controls],
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
    }}


def write_result(path, result) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
