# Model Endpoint Registry, Secret References and Provider-Aware Preflight — Implementation Plan

**Issue:** #557

> **For agentic workers:** execute task by task, in order. Every step is one small action.
> TDD throughout: write the failing test, watch it fail with the stated output, implement,
> watch it pass, commit. Touch only the files in the File Structure table.

**Goal:** Ship the Configuration-contract slice of the hybrid cloud/local initiative: a
read-only endpoint registry with name-only secret references, a capability-evidence reader,
a scheduler-staged and digest-checked delivery of that config to run containers, a
provider-aware preflight that fails closed, the §4.3 payload-class predicate, and a redacted
effective-configuration view. With `FACTORY_MODEL_ENDPOINT` unset (the default), dispatch argv
and env and preflight behaviour stay as they are today.

**Spec:** `docs/superpowers/specs/2026-11-23-endpoint-registry-secrets-preflight-design.md`
(operator-amended 2026-11-23, Requirements 1–12). Contract:
`docs/hybrid-execution-contract.md` §4.1, §4.3, §4.5, §4.6, §4.7, §4.11.

**Architecture:** A new module, `scripts/factory_core/providers/model_endpoint.py`, sits
beside `model.py`; `model.py` itself is not modified. `providers/__init__.py::preflight()` imports it
lazily, and only when `FACTORY_MODEL_ENDPOINT` is set. The scheduler gets one read-only bind
mount of the instance's own `model-registry/` at `/etc/dark-factory/model-registry`. Before
each dispatch, `dispatch()` re-stages it onto the existing shared state volume
(`/var/lib/dark-factory/model-registry`) via `providers/cli.py model-endpoints stage`, and
passes the staged tree's SHA-256 to that one run as `FACTORY_MODEL_REGISTRY_SHA256` through
`docker compose run -e`. The run container's preflight (`entrypoint.sh`, unchanged) recomputes
the digest and fails closed on any mismatch.

**Tech Stack:** Python 3.12 (CI) / 3.14 (image), PyYAML, pytest; bash 5 (`local -n`,
`local -x`); Docker Compose bind mounts.

---

## Plan-level design decisions

These close gaps the spec leaves open. Each one is fail-closed and adds no new surface.

1. **Two preflight sides.** `scheduler.sh` and `entrypoint.sh` both call
   `providers/cli.py preflight` at boot. The scheduler has `FACTORY_MODEL_ENDPOINT` (from its
   `instance.env`) but never a digest, since the digest is computed per dispatch, so the run-side
   rule "endpoint set without digest fails" would crash-loop the scheduler. The plan therefore
   adds `preflight --side {run,scheduler}` (default `run`, the stricter side).
   `scheduler.sh` passes `--side scheduler`. The scheduler side validates the registry schema
   and the selected id on its read-only source mount, and applies the §4.11 decision 3 block
   (Requirement 11). The digest, secret presence and the credential/alias guards stay
   run-side only, because only the run container has the env they check (spec §C, "parse-time existence check would
   wrongly fail on whichever side doesn't hold that secret"). `entrypoint.sh` is not changed.
2. **Staging glue lives in `scripts/provider_env.sh`** as `model_registry_env_flags`.
   `dispatch()` gains three lines: a `local -x FACTORY_MODEL_REGISTRY_SHA256` declaration and one
   call that refuses the dispatch on failure. Effects: (a) a CI-listed shell test
   (`tests/test_provider_env_flags.sh`) covers the glue, which matters because `tests/test_scheduler.sh`
   is not in CI (spec §H); (b) `scheduler.sh` shifts by only 3 lines, so Task 11 can re-point
   the reference docs' `scheduler.sh:N` citations mechanically. The staging logic itself stays
   in Python (`stage_registry`), as the spec requires.
3. **Capability-evidence record shape.** The file holds one top-level `capability_evidence:`
   mapping, as in the contract §4.5 text block, with §4.5's fields plus one more,
   `registry_config`: the SHA-256 of the canonical JSON of the registry entry that was
   certified (`registry_config_sha256`). The spec's "a hash of the registry entry
   (`registry_config`)" needs a recorded value to compare against, and §4.5's schema has no
   field for it. If `registry_config` is absent while `invalidated_by` lists it, the
   fingerprint is unresolvable, so the state is `unknown`.
4. **Derived evidence state.** Computed by `evidence_state(record, entry)`, which works with
   `load_evidence(evidence_dir, endpoint_id)`; together they are the spec's "capability-evidence
   reader".
   - `unknown`: no record exists, or a fingerprint cannot be resolved.
   - `declared`: a local entry exists but its record is not valid, e.g. missing `certified_by`
     (§4.5: "no valid record exists yet").
   - `stale`: `expires_at` has passed, or a resolved fingerprint differs.
   - `tested`: none of the above.

   Only `model_digest`, `harness_version`, `fixture_suite_version` and `registry_config` can be
   resolved without dialing the endpoint. `quantisation`, `runtime_version`, `chat_template` and
   `context_settings` cannot, so listing any of them in `invalidated_by` yields `unknown`.
   `fixture_suite_version` is always compared, as §4.5 requires. `PINNED_FIXTURE_SUITE_VERSION = None`
   until #561, so every local selection is `unknown`. `harness_version` is observed with
   `claude --version`, which reads the installed CLI. The image installs exactly the pinned
version (`Dockerfile`: `npm install -g @anthropic-ai/claude-code@2.1.282`), so the installed
version is how the pin resolves at runtime. An unreadable version is `None`, which means
`unknown`.
   `ACTOR_REQUIRED_DIMENSIONS` uses snake_case names for §4.5's six minimum dimensions; #561
   owns the final names.
5. **`digest_or_revision` immutability** is enforced with a denylist: no whitespace, not one of
   `latest/main/master/head/stable/default`, and no `:latest`/`-latest` suffix. Cloud entries
   legitimately use a pinned model id (e.g. `claude-opus-5-5`), so a sha-only rule would wrongly
   reject them.
6. **YAML duplicate keys are rejected.** A `SafeLoader` subclass refuses duplicate mapping keys,
   closing the in-entry form of the silent-overwrite hazard that §B's list-not-map rule exists
   to avoid.
7. **No secret value in any message.** `base_url` may not embed userinfo, a query string or a
   fragment. YAML parse errors report only line and problem, never the source line. A bad
   `secret_reference` is never echoed.
8. **Redacted view, two sources.** `model-endpoints show` reads the staged copy and requires
   this dispatch's digest, as every reader of the staged copy must (Requirement 8).
   `model-endpoints show --from-source` reads the scheduler's read-only mount, which needs no
   digest. That is the operator's view on the scheduler container.
9. **The staging swap is two renames:** current copy to `.model-registry.old-<uuid>`, then temp
   dir to `model-registry`. That leaves a microsecond window in which the directory is absent.
   A preflight landing in that window fails closed with "staged model registry not found".
   The spec's guarantee ("a mid-copy failure never leaves a half-written registry visible")
   still holds, because the copy is only ever written into the temp dir.
10. **Expected CI/gate behaviour.**
    - `deploy/docker-compose.yml` matches the adapter's `^deploy/` `migration_seed_auth_patterns`,
      so the blast-radius gate will hold the PR as HUMAN_REQUIRED. That is the intended
      human-in-the-loop surface (owner authorisation recorded on #557, Requirement 12). Do not
      route around it.
    - `deploy/instances/**` and `.github/workflows/publish.yml` are never touched.
11. **Digest re-checked after reading.** On the run side, every read of the staged copy
    happens before a closing `verify_staged_digest`:
    - `model_endpoint.preflight()` checks the digest first and again after all its reads.
    - `providers._endpoint_preflight()` reads `hosting_location_of` *before* calling
      `preflight()`, so that read is also covered by the closing check.
    - `model-endpoints show` checks before and after `redacted_view`.

    A file swapped by another run container during the reads therefore fails closed (architect
    and conformance advisories). A swap-and-restore that finishes between two checks is not
    caught. #558, the next trusted reader, should parse from a single in-memory snapshot of
    the tree rather than re-reading files.
12. **Known pre-existing local test noise (not regressions; verified on `main` 3807b03):**
    - `bash tests/test_scheduler.sh` reports `G2`/`I2` failures (`set_board_status REFINED/READY`).
    - `bash tests/test_provider_env_flags.sh` fails its very first assertion whenever `JIRA_*`
      variables are exported in the shell, as they are in factory run containers.
    - Run shell tests as shown (`env -u ...`) and compare against those baselines.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `scripts/factory_core/providers/model_endpoint.py` | **new** | Registry loader/validator, staged-tree digest + staging, capability-evidence reader, run/scheduler endpoint preflight, §4.3 payload predicate, redacted view |
| `scripts/factory_core/providers/__init__.py` | modify | `preflight(side="run")`; lazy `_endpoint_preflight` branch when `FACTORY_MODEL_ENDPOINT` is set; digest-without-endpoint refusal |
| `scripts/factory_core/providers/cli.py` | modify | `preflight --side`; `model-endpoints show [--from-source]`; `model-endpoints stage [--source-dir --dest-dir]` |
| `scripts/provider_env.sh` | modify | `FACTORY_MODEL_ENDPOINT` in `PROVIDER_PASSTHROUGH_VARS`; new `model_registry_env_flags` |
| `scheduler.sh` | modify | boot `preflight --side scheduler`; `dispatch()` stages + forwards the digest |
| `deploy/docker-compose.yml` | modify (owner-authorised) | one read-only mount on `backlog-scheduler` |
| `deploy/model-registry.empty/.gitkeep` | **new** (owner-authorised) | empty default mount source |
| `tests/fixtures/model_registry/model-endpoints.yaml` | **new** | same-host / LAN / remote local entries + one cloud entry |
| `tests/fixtures/model_registry/capability-evidence/local-same-host.yaml` | **new** | §4.5-shaped evidence fixture |
| `tests/test_provider_model_endpoint.py` | **new** | module + `providers.preflight()` wiring tests |
| `tests/test_provider_cli.py` | modify | `--side`, `show`, `stage`; two existing `preflight` fakes accept `side` |
| `tests/test_provider_env_passthrough.py` | modify | passthrough pins; `dispatch()` structure pins; boot `--side scheduler` pin |
| `tests/test_provider_env_flags.sh` | modify | `model_registry_env_flags` behaviour (CI-listed) |
| `tests/test_scheduler.sh` | modify | section C3 (dispatch staging) + section P `--side scheduler` assertion (local only) |
| `tests/test_run_compose.sh` | modify | section 3e: the mount, the placeholder, no `run-compose.yml` mount |
| `docs/configuration.md` | modify | new "Model endpoint registry (#557)" subsection, env placement, revert path; rule 3 + selection table; shifted citations |
| `docs/scheduler.md`, `docs/run-lifecycle.md`, `docs/features.md` | modify (citations only) | re-point `scheduler.sh:N` (and one `deploy/docker-compose.yml:N`) citations shifted by this change |

Not touched: `scripts/factory_core/providers/model.py`, `entrypoint.sh`, `run-compose.yml`,
`scripts/factory_core/run_record.py`, `deploy/instances/**`, `.github/workflows/*`, any `gate_*`,
breaker, budget or session-window code.

Run every pytest command below from the repo root with `PYTHONPATH=scripts`, as CI does.

---

## Task 1: Registry schema loader + fixtures

**Files:**
- Create: `tests/fixtures/model_registry/model-endpoints.yaml`
- Create: `tests/fixtures/model_registry/capability-evidence/local-same-host.yaml`
- Create: `tests/test_provider_model_endpoint.py`
- Create: `scripts/factory_core/providers/model_endpoint.py`

- [ ] **Step 1: Create the registry fixture** `tests/fixtures/model_registry/model-endpoints.yaml`:

```yaml
# Test fixture only (#557): the same-host, LAN and remote base_url shapes plus one cloud
# entry, all under the one registry schema. Never copy into deploy/instances/**.
schema_version: "1.0"
endpoints:
  - id: local-same-host
    hosting_location: local
    protocol: anthropic-messages
    accepts_payload_classes: [local_only]
    model_identity:
      served_name: qwen2.5-coder-32b
      digest_or_revision: sha256:0b1f5c3c1f0e4f7a9d2b6e8a4c3d5f7e9a1b3c5d7e9f0a2b4c6d8e0f1a3b5c7d
    base_url: http://host.docker.internal:8080
    secret_reference: LOCAL_GATEWAY_TOKEN
    timeouts:
      connect_seconds: 10
      time_to_first_token_seconds: 120
      idle_seconds: 60
      total_seconds: 1800
  - id: local-lan
    hosting_location: local
    protocol: anthropic-messages
    accepts_payload_classes: [local_only]
    model_identity:
      served_name: qwen2.5-coder-32b
      digest_or_revision: sha256:0b1f5c3c1f0e4f7a9d2b6e8a4c3d5f7e9a1b3c5d7e9f0a2b4c6d8e0f1a3b5c7d
    base_url: http://192.168.1.50:8080
    secret_reference: LOCAL_GATEWAY_TOKEN
    timeouts:
      connect_seconds: 10
      time_to_first_token_seconds: 120
      idle_seconds: 60
      total_seconds: 1800
  - id: local-remote
    hosting_location: local
    protocol: anthropic-messages
    accepts_payload_classes: [local_only, cloud_ok]
    model_identity:
      served_name: qwen2.5-coder-32b
      digest_or_revision: sha256:0b1f5c3c1f0e4f7a9d2b6e8a4c3d5f7e9a1b3c5d7e9f0a2b4c6d8e0f1a3b5c7d
    base_url: https://gpu.example.com
    secret_reference: REMOTE_GATEWAY_TOKEN
    timeouts:
      connect_seconds: 10
      time_to_first_token_seconds: 120
      idle_seconds: 60
      total_seconds: 1800
  - id: cloud-anthropic
    hosting_location: cloud
    protocol: anthropic-messages
    accepts_payload_classes: [cloud_ok]
    model_identity:
      served_name: claude-opus-5-5
      digest_or_revision: claude-opus-5-5
    secret_reference: ANTHROPIC_API_KEY
```

- [ ] **Step 2: Create the evidence fixture** `tests/fixtures/model_registry/capability-evidence/local-same-host.yaml`.
  Its `registry_config` value is the `registry_config_sha256` of the `local-same-host` entry
  above. Task 3's `test_fixture_evidence_matches_fixture_entry_but_stays_unknown` pins it, so if
  you change that entry, recompute this value.

```yaml
# Test fixture only (#557): shaped like contract §4.5's record. Lists every invalidated_by
# fingerprint, so #557 (which dials no endpoint) derives `unknown` from it.
capability_evidence:
  schema_version: "1.0"
  endpoint_id: local-same-host
  model_identity:
    served_name: qwen2.5-coder-32b
    digest_or_revision: sha256:0b1f5c3c1f0e4f7a9d2b6e8a4c3d5f7e9a1b3c5d7e9f0a2b4c6d8e0f1a3b5c7d
    quantisation: q4_k_m
  runtime_stack:
    name: example-runtime
    version: "1.0.0"
    chat_template: example-template
    context_settings: {max_tokens: 32768}
  harness_version: "2.1.282"
  fixture_suite_version: "fixture-suite-example"
  results:
    protocol_compatibility: pass
    tool_call_fidelity: pass
    streaming: pass
    context_length: pass
    role_isolation: pass
    timeout_cancellation: pass
  qualified_roles: [actor]
  certified_by: example-operator
  certified_at: "2026-11-01T00:00:00+00:00"
  expires_at: "2099-01-01T00:00:00+00:00"
  invalidated_by: [model_digest, quantisation, runtime_version, chat_template, context_settings,
                   harness_version, fixture_suite_version, registry_config]
  registry_config: d4323ada731dca764311176191937e89fbdddd8c7e076b7896b38402607fdec3
```

- [ ] **Step 3: Write the failing tests.** Create `tests/test_provider_model_endpoint.py` with
  exactly this content. Later tasks append sections to it.

```python
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
```

- [ ] **Step 4: Run it and watch it fail.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py -q
```

Expected: collection error ending in
`ModuleNotFoundError: No module named 'factory_core.providers.model_endpoint'`.

- [ ] **Step 5: Implement.** Create `scripts/factory_core/providers/model_endpoint.py` with
  exactly this content. The module docstring, every import and every constant are included now;
  Tasks 2–5 append functions below `load_registry`.

```python
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
```

- [ ] **Step 6: Run it and watch it pass.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py -q
```

Expected: `63 passed`.

- [ ] **Step 7: Commit.**

```bash
git add scripts/factory_core/providers/model_endpoint.py tests/test_provider_model_endpoint.py tests/fixtures/model_registry
git commit -m "feat(model): endpoint registry schema loader and fixtures (#557)"
```

---

## Task 2: Staged-tree digest and atomic staging

**Files:**
- Modify: `tests/test_provider_model_endpoint.py` (append)
- Modify: `scripts/factory_core/providers/model_endpoint.py` (append)

- [ ] **Step 1: Write the failing tests.** Append this to `tests/test_provider_model_endpoint.py`,
  separated by two blank lines:

```python
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


def test_staged_registry_dir_default_and_test_override(monkeypatch, tmp_path):
    assert me.staged_registry_dir() == Path("/var/lib/dark-factory/model-registry")
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_PATH", str(tmp_path))
    assert me.staged_registry_dir() == tmp_path


def test_verify_staged_digest_requires_the_digest(tmp_path):
    reg = _write_registry(tmp_path / "reg", [CLOUD])
    assert "FACTORY_MODEL_REGISTRY_SHA256 is not set" in me.verify_staged_digest(reg)[0]


def test_verify_staged_digest_rejects_malformed_digest(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", "not-hex")
    reg = _write_registry(tmp_path / "reg", [CLOUD])
    assert me.verify_staged_digest(reg) == ["FACTORY_MODEL_REGISTRY_SHA256 is not a 64-hex SHA-256 digest"]


def test_verify_staged_digest_missing_dir_never_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", "0" * 64)
    assert "no fallback to cloud" in me.verify_staged_digest(tmp_path / "absent")[0]


def test_verify_staged_digest_detects_tampering(tmp_path, monkeypatch):
    reg = _write_registry(tmp_path / "reg", [CLOUD])
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", me.tree_sha256(reg))
    assert me.verify_staged_digest(reg) == []
    _write_registry(reg, [_entry(CLOUD, accepts_payload_classes=["cloud_ok"], id="cloud-evil")])
    assert "does not match FACTORY_MODEL_REGISTRY_SHA256" in me.verify_staged_digest(reg)[0]
```

- [ ] **Step 2: Run and watch it fail.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py -q
```

Expected: `11 failed, 63 passed`. The failures are `AttributeError: module
'factory_core.providers.model_endpoint' has no attribute 'tree_sha256'` (and
`stage_registry` / `staged_registry_dir` / `verify_staged_digest`).

- [ ] **Step 3: Implement.** Append to `scripts/factory_core/providers/model_endpoint.py`, two
  blank lines after `load_registry`:

```python
def tree_sha256(root) -> str:
    """SHA-256 over a directory tree: sorted relative paths and file bytes."""
    root = Path(root)
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):  # never descends into a symlink
        entries += [Path(dirpath) / name for name in dirnames + filenames]
    digest = hashlib.sha256()
    for path in sorted(entries):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise RegistryError([f"{root}: {rel} is not a regular file or directory"])
        if path.is_file():
            digest.update(rel.encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii") + b"\n")
    return digest.hexdigest()


def staged_registry_dir() -> Path:
    # FACTORY_MODEL_REGISTRY_PATH is a test-only override, never forwarded to run containers.
    return Path(os.environ.get("FACTORY_MODEL_REGISTRY_PATH") or _DEFAULT_STAGED_DIR)


def stage_registry(source=None, dest=None) -> str:
    """Copy the read-only source registry onto the shared volume; return the staged digest.

    The copy goes to a temp directory beside dest and is swapped in by rename, so a failed
    copy never leaves a half-written registry where a run container reads it.
    """
    source = Path(source) if source else SOURCE_REGISTRY_DIR
    dest = Path(dest) if dest else staged_registry_dir()
    load_registry(source / REGISTRY_FILE)  # never stage an invalid registry
    tree_sha256(source)  # rejects symlinks before anything is copied
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{dest.name}.tmp-", dir=dest.parent))
    old = dest.with_name(f".{dest.name}.old-{uuid.uuid4().hex}")
    try:
        os.chmod(tmp, 0o755)  # mkdtemp is 0700; run containers must be able to read it
        shutil.copytree(source, tmp, dirs_exist_ok=True)
        digest = tree_sha256(tmp)
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
    return digest


def verify_staged_digest(registry_path) -> list[str]:
    """The staged copy is trusted only if it matches the digest the scheduler passed (Req. 8)."""
    expected = os.environ.get("FACTORY_MODEL_REGISTRY_SHA256", "")
    if not expected:
        return ["FACTORY_MODEL_REGISTRY_SHA256 is not set — the staged model registry is trusted "
                "only with the digest the scheduler passes to this dispatch"]
    if not _SHA256_HEX_RE.match(expected):
        return ["FACTORY_MODEL_REGISTRY_SHA256 is not a 64-hex SHA-256 digest"]
    registry_path = Path(registry_path)
    if not registry_path.is_dir():
        return [f"staged model registry not found at {registry_path} — no fallback to cloud"]
    try:
        actual = tree_sha256(registry_path)
    except RegistryError as e:
        return e.problems
    except OSError:
        return [f"staged model registry at {registry_path} is unreadable"]
    if not hmac.compare_digest(actual, expected):
        return [f"staged model registry at {registry_path} does not match "
                "FACTORY_MODEL_REGISTRY_SHA256 — it changed after the scheduler staged it"]
    return []
```

- [ ] **Step 4: Run and watch it pass.** Same command. Expected: `74 passed`.

- [ ] **Step 5: Commit.**

```bash
git add scripts/factory_core/providers/model_endpoint.py tests/test_provider_model_endpoint.py
git commit -m "feat(model): digest-checked staging of the endpoint registry (#557)"
```

---

## Task 3: Capability-evidence reader and derived state

**Files:**
- Modify: `tests/test_provider_model_endpoint.py` (append)
- Modify: `scripts/factory_core/providers/model_endpoint.py` (append)

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_provider_model_endpoint.py`:

```python
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
```

- [ ] **Step 2: Run and watch it fail.** Same command. Expected: `26 failed, 74 passed`, every
  failure an `AttributeError` for `evidence_state`, `load_evidence`, `registry_config_sha256`,
  `role_problems` or `observed_harness_version`.

- [ ] **Step 3: Implement.** Append to `scripts/factory_core/providers/model_endpoint.py`:

```python
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
    doc = _read_yaml(path)
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
```

- [ ] **Step 4: Run and watch it pass.** Same command. Expected: `100 passed`.

- [ ] **Step 5: Commit.**

```bash
git add scripts/factory_core/providers/model_endpoint.py tests/test_provider_model_endpoint.py
git commit -m "feat(model): capability-evidence reader with derived state (#557)"
```

---

## Task 4: Endpoint preflight — run side and scheduler side

**Files:**
- Modify: `tests/test_provider_model_endpoint.py` (append)
- Modify: `scripts/factory_core/providers/model_endpoint.py` (append)

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_provider_model_endpoint.py`:

```python
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


def _run_preflight(reg, endpoint_id):
    return me.preflight(reg, reg / me.EVIDENCE_DIR, endpoint_id)


def test_cloud_selection_passes_with_secret_and_digest(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [CLOUD])
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    assert _run_preflight(reg, "cloud-test") == []


def test_cloud_selection_needs_no_evidence_but_names_missing_secret(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [CLOUD])
    assert _run_preflight(reg, "cloud-test") == ["ANTHROPIC_API_KEY is not set. Add it to .archon/.env"]


def test_unknown_endpoint_fails_without_choosing_another(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [CLOUD])
    problems = _run_preflight(reg, "cloud-nope")
    assert len(problems) == 1 and "Unknown FACTORY_MODEL_ENDPOINT 'cloud-nope'" in problems[0]
    assert "no other endpoint is chosen instead" in problems[0]


def test_preflight_without_digest_fails_before_reading_registry(tmp_path, monkeypatch):
    reg = _write_registry(tmp_path / "staged", [CLOUD])
    assert "FACTORY_MODEL_REGISTRY_SHA256 is not set" in _run_preflight(reg, "cloud-test")[0]


def test_preflight_fails_on_tampered_staged_copy(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [CLOUD])
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    _write_registry(reg, [CLOUD, _entry(CLOUD, id="cloud-extra")])
    problems = _run_preflight(reg, "cloud-test")
    assert len(problems) == 1 and "does not match FACTORY_MODEL_REGISTRY_SHA256" in problems[0]


def test_preflight_rechecks_digest_after_reading(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [CLOUD])
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    real_select = me._select

    def _select_then_tamper(path, endpoint_id):
        result = real_select(path, endpoint_id)
        (Path(path) / "planted.yaml").write_text("x: 1\n", encoding="utf-8")
        return result
    monkeypatch.setattr(me, "_select", _select_then_tamper)
    problems = _run_preflight(reg, "cloud-test")
    assert len(problems) == 1 and "does not match FACTORY_MODEL_REGISTRY_SHA256" in problems[0]


def test_preflight_reports_schema_violations(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [_entry(CLOUD, protocol="openai-chat")])
    assert any("unsupported protocol" in p for p in _run_preflight(reg, "cloud-test"))


def test_local_selection_missing_secret_names_it_without_archon_env_hint(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [LOCAL])
    problems = _run_preflight(reg, "local-test")
    assert "LOCAL_GATEWAY_TOKEN (secret_reference of endpoint 'local-test') is not set" in problems
    assert not any(".archon/.env" in p for p in problems)


@pytest.mark.parametrize("name", me.CLOUD_CREDENTIALS)
def test_local_selection_fails_when_a_cloud_credential_is_present(tmp_path, monkeypatch, name):
    reg = _stage(tmp_path, monkeypatch, [LOCAL])
    _local_env(monkeypatch)
    monkeypatch.setenv(name, SENTINEL + "-cloud")
    problems = _run_preflight(reg, "local-test")
    assert any(f"cloud model credential(s) are set in this container: {name}" in p for p in problems)
    assert SENTINEL not in " ".join(problems)


def test_local_selection_requires_matching_base_url(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [LOCAL])
    _local_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    assert any("ANTHROPIC_BASE_URL must equal the entry's base_url" in p
               for p in _run_preflight(reg, "local-test"))


@pytest.mark.parametrize("alias", me.TIER_ALIASES)
def test_local_selection_requires_every_tier_alias_to_be_the_served_model(tmp_path, monkeypatch, alias):
    reg = _stage(tmp_path, monkeypatch, [LOCAL])
    _local_env(monkeypatch)
    monkeypatch.setenv(alias, "claude-opus-5-5")
    assert any(f"{alias} must equal model_identity.served_name" in p
               for p in _run_preflight(reg, "local-test"))


def test_local_selection_requires_evidence(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [LOCAL])
    _local_env(monkeypatch)
    problems = _run_preflight(reg, "local-test")
    assert "endpoint 'local-test': capability evidence is 'unknown', not 'tested' (contract §4.5)" in problems


def test_local_selection_always_fails_as_an_existing_phase(tmp_path, monkeypatch, pinned_suite):
    reg = _stage(tmp_path, monkeypatch, [LOCAL], evidence={"local-test": _record(LOCAL)})
    _local_env(monkeypatch)
    assert _run_preflight(reg, "local-test") == [
        "endpoint 'local-test' is local, but no local-eligible dispatch exists yet — every "
        "current dispatch runs a gated phase (contract §4.11 decision 3; the first helper "
        "dispatch is #587)"]


def test_local_selection_with_failed_dimension_reports_it(tmp_path, monkeypatch, pinned_suite):
    record = _record(LOCAL, results=dict(_record(LOCAL)["results"], tool_call_fidelity="fail"))
    reg = _stage(tmp_path, monkeypatch, [LOCAL], evidence={"local-test": record})
    _local_env(monkeypatch)
    assert "capability evidence for 'local-test': tool_call_fidelity is fail, not pass" in \
        _run_preflight(reg, "local-test")


def test_preflight_output_never_contains_secret_values(tmp_path, monkeypatch):
    reg = _stage(tmp_path, monkeypatch, [LOCAL, CLOUD])
    _local_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    for eid in ("local-test", "cloud-test"):
        assert SENTINEL not in " ".join(_run_preflight(reg, eid))


def test_scheduler_preflight_checks_schema_and_id_only(tmp_path):
    src = _write_registry(tmp_path / "src", [CLOUD, LOCAL])
    assert me.scheduler_preflight(src, "cloud-test") == []
    assert "Unknown FACTORY_MODEL_ENDPOINT 'nope'" in me.scheduler_preflight(src, "nope")[0]


def test_scheduler_preflight_blocks_a_local_selection(tmp_path):
    src = _write_registry(tmp_path / "src", [LOCAL])
    assert me.scheduler_preflight(src, "local-test") == [me._NO_LOCAL_DISPATCH.format(eid="local-test")]


def test_hosting_location_of(tmp_path):
    src = _write_registry(tmp_path / "src", [CLOUD, LOCAL])
    assert me.hosting_location_of(src, "local-test") == "local"
    assert me.hosting_location_of(src, "cloud-test") == "cloud"
    assert me.hosting_location_of(src, "nope") is None
    assert me.hosting_location_of(tmp_path / "absent", "local-test") is None
```

- [ ] **Step 2: Run and watch it fail.** Same command. Expected: `26 failed, 100 passed`
  (`AttributeError` for `preflight` / `scheduler_preflight` / `hosting_location_of` /
  `_NO_LOCAL_DISPATCH` users).

- [ ] **Step 3: Implement.** Append to `scripts/factory_core/providers/model_endpoint.py`:

```python
def _select(registry_path, endpoint_id):
    path = Path(registry_path) / REGISTRY_FILE
    try:
        endpoints = load_registry(path)
    except RegistryError as e:
        return None, e.problems
    for entry in endpoints:
        if entry["id"] == endpoint_id:
            return entry, []
    return None, [f"Unknown FACTORY_MODEL_ENDPOINT '{endpoint_id}' — no such id in {path}; "
                  "no other endpoint is chosen instead"]


def hosting_location_of(registry_path, endpoint_id):
    entry, _ = _select(registry_path, endpoint_id)
    return entry["hosting_location"] if entry else None


def _local_problems(entry, evidence_dir):
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
    try:
        record = load_evidence(evidence_dir, eid)
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


def preflight(registry_path, evidence_dir, endpoint_id) -> list[str]:
    """Run-container check of the staged registry for one selected endpoint; [] == OK."""
    problems = verify_staged_digest(registry_path)
    if problems:
        return problems
    entry, problems = _select(registry_path, endpoint_id)
    if entry is None:
        return problems
    ref = entry["secret_reference"]
    if entry["hosting_location"] == "cloud":
        problems += _missing([ref])  # a cloud entry's secret arrives via .archon/.env
    elif not os.environ.get(ref):
        # Not _missing(): a local secret must not be delivered through .archon/.env (spec §C).
        problems.append(f"{ref} (secret_reference of endpoint '{entry['id']}') is not set")
    if entry["hosting_location"] == "local":
        problems += _local_problems(entry, evidence_dir)
    # Checked again after every read: a swap between the first check and the reads fails here.
    problems += verify_staged_digest(registry_path)
    return problems


def scheduler_preflight(registry_path, endpoint_id) -> list[str]:
    """Scheduler boot check on its read-only source mount: schema, selected id, §4.11 decision 3.

    The digest, secret presence and credential/alias guards are the run container's: it holds
    the environment they check.
    """
    entry, problems = _select(registry_path, endpoint_id)
    if entry is not None and entry["hosting_location"] == "local":
        problems.append(_NO_LOCAL_DISPATCH.format(eid=entry["id"]))
    return problems
```

- [ ] **Step 4: Run and watch it pass.** Same command. Expected: `126 passed`.

- [ ] **Step 5: Commit.**

```bash
git add scripts/factory_core/providers/model_endpoint.py tests/test_provider_model_endpoint.py
git commit -m "feat(model): fail-closed endpoint preflight for run and scheduler (#557)"
```

---

## Task 5: Payload-class predicate (§4.3) and redacted view helpers

**Files:**
- Modify: `tests/test_provider_model_endpoint.py` (append)
- Modify: `scripts/factory_core/providers/model_endpoint.py` (append)

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_provider_model_endpoint.py`:

```python
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
```

- [ ] **Step 2: Run and watch it fail.** Same command. Expected: `26 failed, 126 passed`.

- [ ] **Step 3: Implement.** Append to `scripts/factory_core/providers/model_endpoint.py`.
  Per spec §F, nothing calls these from a real dispatch path, and `run_record.py` is not
  touched; #558 wires them in.

```python
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
    """Effective configuration with each secret_reference reduced to its name and presence."""
    path = Path(registry_path) / REGISTRY_FILE
    endpoints = []
    for entry in load_registry(path):
        view = {k: v for k, v in entry.items() if k != "secret_reference"}
        ref = entry["secret_reference"]
        view["secret_reference"] = {"name": ref, "present": bool(os.environ.get(ref))}
        if "base_url" in entry:
            view["base_url_scope"] = base_url_scope(entry["base_url"])
        endpoints.append(view)
    return {"registry": str(path), "endpoints": endpoints}
```

- [ ] **Step 4: Run and watch it pass.** Same command. Expected: `152 passed`.

- [ ] **Step 5: Commit.**

```bash
git add scripts/factory_core/providers/model_endpoint.py tests/test_provider_model_endpoint.py
git commit -m "feat(model): payload-class predicate and redacted endpoint view (#557)"
```

---

## Task 6: Wire `providers.preflight(side)`

**Files:**
- Modify: `tests/test_provider_model_endpoint.py` (append)
- Modify: `scripts/factory_core/providers/__init__.py`

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_provider_model_endpoint.py`:

```python
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


def test_scheduler_side_reads_the_source_mount_without_a_digest(base_env, tmp_path, monkeypatch):
    from factory_core import providers
    monkeypatch.setattr(me, "SOURCE_REGISTRY_DIR", _write_registry(tmp_path / "src", [CLOUD, LOCAL]))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "x")
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "cloud-test")
    assert providers.preflight(side="scheduler") == []
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "local-test")
    assert providers.preflight(side="scheduler") == [me._NO_LOCAL_DISPATCH.format(eid="local-test")]
```

- [ ] **Step 2: Run and watch it fail.** Same command. Expected: `6 failed, 153 passed`
  (`TypeError: preflight() got an unexpected keyword argument 'side'`, and endpoint
  selections not being checked yet).

- [ ] **Step 3: Implement — signature and docstring.** In
  `scripts/factory_core/providers/__init__.py` replace:

```python
def preflight() -> list[str]:
    """Return a list of human-readable problems; empty list == OK."""
    problems = []
```

with:

```python
def preflight(side: str = "run") -> list[str]:
    """Return a list of human-readable problems; empty list == OK.

    side="scheduler" is scheduler.sh's boot check; the default is the run container's
    (entrypoint.sh). The two differ only when FACTORY_MODEL_ENDPOINT is set (#557).
    """
    problems = []
```

- [ ] **Step 4: Implement — the endpoint branch.** In the same file, replace the tail of
  `preflight()`:

```python
    model_name = os.environ.get("FACTORY_MODEL_PROVIDER", "anthropic")
    problems += model.preflight(model_name)

    return problems
```

with this. The unset path still ends in the same `problems += model.preflight(model_name)`;
its only new line refuses a stray digest.

```python
    model_name = os.environ.get("FACTORY_MODEL_PROVIDER", "anthropic")
    endpoint_id = os.environ.get("FACTORY_MODEL_ENDPOINT", "")
    if endpoint_id:
        problems += _endpoint_preflight(side, endpoint_id, model_name)
        return problems
    if os.environ.get("FACTORY_MODEL_REGISTRY_SHA256"):
        problems.append("FACTORY_MODEL_REGISTRY_SHA256 is set but FACTORY_MODEL_ENDPOINT is not "
                        "— a registry digest without an endpoint selection is refused")
    problems += model.preflight(model_name)

    return problems


def _endpoint_preflight(side: str, endpoint_id: str, model_name: str) -> list[str]:
    # Imported here so the default path (FACTORY_MODEL_ENDPOINT unset) never loads it.
    from factory_core.providers import model_endpoint

    if side == "scheduler":
        registry_dir = model_endpoint.SOURCE_REGISTRY_DIR
    else:
        registry_dir = model_endpoint.staged_registry_dir()
    # Read before preflight(), so its closing digest check also covers this read.
    hosting = model_endpoint.hosting_location_of(registry_dir, endpoint_id)
    if side == "scheduler":
        problems = model_endpoint.scheduler_preflight(registry_dir, endpoint_id)
    else:
        problems = model_endpoint.preflight(
            registry_dir, registry_dir / model_endpoint.EVIDENCE_DIR, endpoint_id)
    # A local selection holds no cloud model credential, so the cloud credential check is
    # skipped for it; a cloud selection runs it in addition to the endpoint checks.
    if hosting != "local":
        problems += model.preflight(model_name)
    return problems
```

- [ ] **Step 5: Run and watch it pass.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py tests/test_provider_registry.py tests/test_provider_model.py -q
```

Expected: all pass (`159 passed` in the new file, plus the existing registry/model tests;
none of those are edited).

- [ ] **Step 6: Commit.**

```bash
git add scripts/factory_core/providers/__init__.py tests/test_provider_model_endpoint.py
git commit -m "feat(model): providers preflight checks a selected endpoint (#557)"
```

---

## Task 7: CLI — `preflight --side`, `model-endpoints show|stage`

**Files:**
- Modify: `tests/test_provider_cli.py`
- Modify: `scripts/factory_core/providers/cli.py`

- [ ] **Step 1: Let the two existing fakes accept `side`.** In `tests/test_provider_cli.py`,
  in `test_preflight_ok_prints_ok_and_exits_0` replace
  `monkeypatch.setattr(cli_mod, "preflight", lambda: [])` with
  `monkeypatch.setattr(cli_mod, "preflight", lambda side="run": [])`. In
  `test_preflight_failure_prints_every_problem_and_exits_1` replace
  `monkeypatch.setattr(cli_mod, "preflight", lambda: [` with
  `monkeypatch.setattr(cli_mod, "preflight", lambda side="run": [`. Nothing else in those tests
  changes.

- [ ] **Step 2: Add the import.** In `tests/test_provider_cli.py`, add `import shutil` on the line
  after `import json`.

- [ ] **Step 3: Write the failing tests.** Append to `tests/test_provider_cli.py`:

```python
# --- #557: preflight --side, model-endpoints show/stage -------------------------------------

MODEL_REGISTRY_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "model_registry"


def test_preflight_side_defaults_to_run_and_passes_scheduler_through(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    seen = []
    monkeypatch.setattr(cli_mod, "preflight", lambda side="run": seen.append(side) or [])
    for argv in (["cli.py", "preflight"], ["cli.py", "preflight", "--side", "scheduler"]):
        monkeypatch.setattr(sys, "argv", argv)
        cli_mod.main()
    assert seen == ["run", "scheduler"]


def _staged_fixture(tmp_path, monkeypatch):
    from factory_core.providers import model_endpoint
    staged = tmp_path / "model-registry"
    shutil.copytree(MODEL_REGISTRY_FIXTURES, staged)
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_PATH", str(staged))
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", model_endpoint.tree_sha256(staged))
    return staged


def test_model_endpoints_show_redacts_secrets(tmp_path, monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    _staged_fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("LOCAL_GATEWAY_TOKEN", "sk-sentinel-show")
    monkeypatch.delenv("REMOTE_GATEWAY_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["cli.py", "model-endpoints", "show"])
    cli_mod.main()
    out = capsys.readouterr().out
    assert "sk-sentinel-show" not in out
    view = {e["id"]: e for e in json.loads(out)["endpoints"]}
    assert view["local-same-host"]["secret_reference"] == {"name": "LOCAL_GATEWAY_TOKEN", "present": True}
    assert view["local-remote"]["secret_reference"] == {"name": "REMOTE_GATEWAY_TOKEN", "present": False}
    assert view["local-lan"]["base_url_scope"] == "lan"


def test_model_endpoints_show_refuses_staged_copy_without_digest(tmp_path, monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    _staged_fixture(tmp_path, monkeypatch)
    monkeypatch.delenv("FACTORY_MODEL_REGISTRY_SHA256")
    monkeypatch.setattr(sys, "argv", ["cli.py", "model-endpoints", "show"])
    with pytest.raises(SystemExit) as exc:
        cli_mod.main()
    assert exc.value.code == 1
    assert "FACTORY_MODEL_REGISTRY_SHA256 is not set" in capsys.readouterr().err


def test_model_endpoints_show_from_source_needs_no_digest(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    from factory_core.providers import model_endpoint
    monkeypatch.setattr(model_endpoint, "SOURCE_REGISTRY_DIR", MODEL_REGISTRY_FIXTURES)
    monkeypatch.delenv("FACTORY_MODEL_REGISTRY_SHA256", raising=False)
    monkeypatch.setattr(sys, "argv", ["cli.py", "model-endpoints", "show", "--from-source"])
    cli_mod.main()
    assert len(json.loads(capsys.readouterr().out)["endpoints"]) == 4


def test_model_endpoints_show_invalid_registry_exits_1_with_field_errors(tmp_path, monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    staged = _staged_fixture(tmp_path, monkeypatch)
    registry = staged / "model-endpoints.yaml"
    registry.write_text(registry.read_text(encoding="utf-8").replace(
        "protocol: anthropic-messages", "protocol: openai-chat", 1), encoding="utf-8")
    from factory_core.providers import model_endpoint
    monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", model_endpoint.tree_sha256(staged))
    monkeypatch.setattr(sys, "argv", ["cli.py", "model-endpoints", "show"])
    with pytest.raises(SystemExit) as exc:
        cli_mod.main()
    assert exc.value.code == 1
    assert "ERROR: endpoint 'local-same-host': unsupported protocol 'openai-chat'" in capsys.readouterr().err


def test_model_endpoints_stage_prints_staged_digest(tmp_path, monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    from factory_core.providers import model_endpoint
    dest = tmp_path / "state" / "model-registry"
    monkeypatch.setattr(sys, "argv", ["cli.py", "model-endpoints", "stage",
                                      "--source-dir", str(MODEL_REGISTRY_FIXTURES), "--dest-dir", str(dest)])
    cli_mod.main()
    assert capsys.readouterr().out.strip() == model_endpoint.tree_sha256(dest)


def test_model_endpoints_stage_failure_exits_1(tmp_path, monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod
    monkeypatch.setattr(sys, "argv", ["cli.py", "model-endpoints", "stage",
                                      "--source-dir", str(tmp_path / "empty"), "--dest-dir", str(tmp_path / "d")])
    with pytest.raises(SystemExit) as exc:
        cli_mod.main()
    assert exc.value.code == 1
    assert "model endpoint registry not found" in capsys.readouterr().err
    assert not (tmp_path / "d").exists()
```

- [ ] **Step 4: Run and watch it fail.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_cli.py -q
```

Expected: `7 failed, 25 passed` (argparse `invalid choice: 'model-endpoints'` /
`unrecognized arguments: --side scheduler` → `SystemExit: 2`).

- [ ] **Step 5: Implement the handlers.** In `scripts/factory_core/providers/cli.py` replace the
  whole `_preflight` function:

```python
def _preflight(args):
    problems = preflight()
    if problems:
        for p in problems:
            print(f"ERROR: {p}", file=sys.stderr)
        sys.exit(1)
    print("providers preflight: OK")
```

with:

```python
def _preflight(args):
    problems = preflight(side=args.side)
    if problems:
        for p in problems:
            print(f"ERROR: {p}", file=sys.stderr)
        sys.exit(1)
    print("providers preflight: OK")


def _fail(problems):
    for p in problems:
        print(f"ERROR: {p}", file=sys.stderr)
    sys.exit(1)


def _model_endpoints_show(args):
    # Redacted effective-configuration view (#557): secret_reference prints as its name and
    # presence only. The staged copy is trusted only with this dispatch's digest; --from-source
    # reads the scheduler's read-only mount instead.
    from factory_core.providers import model_endpoint
    if args.from_source:
        registry_dir, problems = model_endpoint.SOURCE_REGISTRY_DIR, []
    else:
        registry_dir = model_endpoint.staged_registry_dir()
        problems = model_endpoint.verify_staged_digest(registry_dir)
    if problems:
        _fail(problems)
    try:
        view = model_endpoint.redacted_view(registry_dir)
    except model_endpoint.RegistryError as e:
        _fail(e.problems)
    if not args.from_source:
        problems = model_endpoint.verify_staged_digest(registry_dir)  # unchanged while read?
        if problems:
            _fail(problems)
    print(json.dumps(view, indent=2, default=str))


def _model_endpoints_stage(args):
    # scheduler.sh dispatch() calls this before each dispatch when FACTORY_MODEL_ENDPOINT is
    # set; stdout is the staged tree's digest, forwarded as FACTORY_MODEL_REGISTRY_SHA256.
    from factory_core.providers import model_endpoint
    try:
        digest = model_endpoint.stage_registry(args.source_dir, args.dest_dir)
    except model_endpoint.RegistryError as e:
        _fail(e.problems)
    except OSError as e:
        _fail([f"model registry staging failed: {e}"])
    print(digest)
```

- [ ] **Step 6: Implement the parsers.** In the same file, in `main()`, replace:

```python
    pf = top.add_parser("preflight")
    pf.set_defaults(func=_preflight)
```

with:

```python
    pf = top.add_parser("preflight")
    pf.add_argument("--side", choices=("run", "scheduler"), default="run",
                    help="scheduler: scheduler.sh's boot check; run (default): a run container's")
    pf.set_defaults(func=_preflight)

    model_endpoints = top.add_parser("model-endpoints")
    msub = model_endpoints.add_subparsers(dest="verb", required=True)

    mshow = msub.add_parser("show")
    mshow.add_argument("--from-source", action="store_true",
                       help="read the scheduler's read-only registry mount, not the staged copy")
    mshow.set_defaults(func=_model_endpoints_show)

    mstage = msub.add_parser("stage")
    mstage.add_argument("--source-dir", default=None)
    mstage.add_argument("--dest-dir", default=None)
    mstage.set_defaults(func=_model_endpoints_stage)
```

- [ ] **Step 7: Run and watch it pass.** Same command. Expected: `32 passed`.

- [ ] **Step 8: Commit.**

```bash
git add scripts/factory_core/providers/cli.py tests/test_provider_cli.py
git commit -m "feat(model): model-endpoints show/stage and preflight --side CLI (#557)"
```

---

## Task 8: `provider_env.sh` — forward the selection, stage-and-flag helper

**Files:**
- Modify: `tests/test_provider_env_passthrough.py`
- Modify: `tests/test_provider_env_flags.sh`
- Modify: `scripts/provider_env.sh`

- [ ] **Step 1: Write the failing pytest pin.** Append to `tests/test_provider_env_passthrough.py`:

```python
def test_model_endpoint_selection_forwarded_but_digest_and_path_are_not():
    # #557: the registry id is instance config; the digest is per dispatch (dispatch() adds it
    # with its own -e) and FACTORY_MODEL_REGISTRY_PATH is a test-only override.
    f = _forwarded()
    assert "FACTORY_MODEL_ENDPOINT" in f
    assert not {"FACTORY_MODEL_REGISTRY_SHA256", "FACTORY_MODEL_REGISTRY_PATH"} & f
```

- [ ] **Step 2: Write the failing shell test.** In `tests/test_provider_env_flags.sh`, insert
  this block immediately before the final `echo "PASS: test_provider_env_flags.sh"` line:

```bash
# #557: model_registry_env_flags is a no-op unless FACTORY_MODEL_ENDPOINT is set; then it
# stages, exports the digest for the caller's scope only and appends the flag by name. A
# failed or empty stage returns 1 and appends nothing.
FACTORY_PROVIDERS_CLI=/fake/providers/cli.py
CALLS=$(mktemp)
STAGE_OUT="$(printf 'b%.0s' {1..64})"; STAGE_RC=0
python3() { echo "$*" >> "$CALLS"; printf '%s\n' "$STAGE_OUT"; return "$STAGE_RC"; }
run_case() {  # prints <rc>|<flags>|<digest as a child process sees it>
  local -x FACTORY_MODEL_REGISTRY_SHA256
  local -a flags=(-e FACTORY_TRACKER)
  local rc=0
  model_registry_env_flags flags || rc=$?
  printf '%s|%s|%s' "$rc" "${flags[*]}" "$(bash -c 'echo "${FACTORY_MODEL_REGISTRY_SHA256:-unset}"')"
}

unset FACTORY_MODEL_ENDPOINT FACTORY_MODEL_REGISTRY_SHA256
out=$(run_case)
[ "$out" = "0|-e FACTORY_TRACKER|unset" ] || { echo "FAIL: registry flags with no endpoint: '$out'"; exit 1; }
[ ! -s "$CALLS" ] || { echo "FAIL: staged with no endpoint selected"; exit 1; }

export FACTORY_MODEL_ENDPOINT=cloud-anthropic
out=$(run_case)
[ "$out" = "0|-e FACTORY_TRACKER -e FACTORY_MODEL_REGISTRY_SHA256|$STAGE_OUT" ] \
  || { echo "FAIL: registry flags with an endpoint: '$out'"; exit 1; }
[ "$(cat "$CALLS")" = "/fake/providers/cli.py model-endpoints stage" ] \
  || { echo "FAIL: unexpected staging call: '$(cat "$CALLS")'"; exit 1; }
[ "${FACTORY_MODEL_REGISTRY_SHA256:-unset}" = "unset" ] \
  || { echo "FAIL: digest leaked out of the caller's scope"; exit 1; }

STAGE_RC=1
out=$(run_case)
[ "${out%%|*}" = "1" ] && [ "$(echo "$out" | cut -d'|' -f2)" = "-e FACTORY_TRACKER" ] \
  || { echo "FAIL: failed staging must refuse and append nothing: '$out'"; exit 1; }
STAGE_RC=0 STAGE_OUT=""
out=$(run_case)
[ "${out%%|*}" = "1" ] || { echo "FAIL: empty digest must refuse the dispatch: '$out'"; exit 1; }
rm -f "$CALLS"
```

- [ ] **Step 3: Run both and watch them fail.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_env_passthrough.py -q -k model_endpoint_selection
env -u JIRA_PROJECT_KEY -u JIRA_EPIC_LINK_FIELD -u JIRA_BASE_URL -u JIRA_TOKEN bash tests/test_provider_env_flags.sh
```

Expected: `1 failed` (`assert 'FACTORY_MODEL_ENDPOINT' in f`), then
`model_registry_env_flags: command not found` followed by
`FAIL: registry flags with no endpoint: '127|-e FACTORY_TRACKER|unset'`.

- [ ] **Step 4: Implement — passthrough list.** In `scripts/provider_env.sh` replace:

```bash
  # Provider selection
  FACTORY_TRACKER FACTORY_CODEHOST FACTORY_MODEL_PROVIDER
```

with:

```bash
  # Provider selection (FACTORY_MODEL_ENDPOINT: #557 registry id; the per-dispatch
  # FACTORY_MODEL_REGISTRY_SHA256 is added by dispatch() itself, never listed here)
  FACTORY_TRACKER FACTORY_CODEHOST FACTORY_MODEL_PROVIDER FACTORY_MODEL_ENDPOINT
```

- [ ] **Step 5: Implement — the helper.** Append to the end of `scripts/provider_env.sh`, after
  `provider_env_flags`, with one blank line before it:

```bash
# Model endpoint registry (#557). dispatch() calls this in its own shell, not a subshell,
# right after `local -x FACTORY_MODEL_REGISTRY_SHA256`, so the digest is exported to that one
# `docker compose run` and nothing else. FACTORY_MODEL_ENDPOINT unset: does nothing. Set:
# re-stages the scheduler's read-only registry mount onto the shared state volume
# (`providers/cli.py model-endpoints stage`), sets FACTORY_MODEL_REGISTRY_SHA256 to the
# staged tree's digest and appends `-e FACTORY_MODEL_REGISTRY_SHA256` (name only) to the
# array named by $1. Returns 1 — the caller must not dispatch — when staging fails or prints
# no digest. The digest is never written to a file a run container can write.
model_registry_env_flags() {
  local -n _registry_flags="$1"
  [ -n "${FACTORY_MODEL_ENDPOINT:-}" ] || return 0
  FACTORY_MODEL_REGISTRY_SHA256=$(python3 "$FACTORY_PROVIDERS_CLI" model-endpoints stage) \
    && [ -n "$FACTORY_MODEL_REGISTRY_SHA256" ] || return 1
  _registry_flags+=(-e FACTORY_MODEL_REGISTRY_SHA256)
}
```

- [ ] **Step 6: Run both and watch them pass.** Same two commands. Expected: `1 passed`, then
  `PASS: test_provider_env_flags.sh`.

- [ ] **Step 7: Commit.**

```bash
git add scripts/provider_env.sh tests/test_provider_env_passthrough.py tests/test_provider_env_flags.sh
git commit -m "feat(model): forward FACTORY_MODEL_ENDPOINT and add registry staging helper (#557)"
```

---

## Task 9: `scheduler.sh` — scheduler-side boot preflight, staging in `dispatch()`

**Files:**
- Modify: `tests/test_provider_env_passthrough.py`
- Modify: `tests/test_scheduler.sh`
- Modify: `scheduler.sh`

- [ ] **Step 1: Write the failing CI pins.** Append to `tests/test_provider_env_passthrough.py`:

```python
def test_dispatch_stages_model_registry_before_compose_run():
    # #557: the digest is a dispatch-local export, set only by model_registry_env_flags.
    sched = (REPO / "scheduler.sh").read_text(encoding="utf-8")
    dispatch = re.search(r"^dispatch\(\) \{.*?^\}", sched, re.S | re.M).group(0)
    assert "local -x FACTORY_MODEL_REGISTRY_SHA256" in dispatch
    call = dispatch.index("model_registry_env_flags env_flags")
    assert dispatch.index("provider_env_flags)") < call < dispatch.index("docker compose")
    assert "return 1" in dispatch[call:dispatch.index("docker compose")]


def test_scheduler_boot_preflight_runs_scheduler_side():
    sched = (REPO / "scheduler.sh").read_text(encoding="utf-8")
    assert re.search(r'^python3 "\$FACTORY_PROVIDERS_CLI" preflight --side scheduler$', sched, re.M)
```

- [ ] **Step 2: Write the local end-to-end checks.** In `tests/test_scheduler.sh`:
  (a) insert this block immediately before the `# D: Opt-in label gate (fails until Task 8)`
  section's leading `# ==========================================` line:

```bash
# ==========================================
# C3: dispatch() stages the model registry only when FACTORY_MODEL_ENDPOINT is set (#557)
# ==========================================
echo ""
echo "--- C3: dispatch() model registry staging ---"
> "$STUB_LOG"

# The stub prints the env a real child process would see, proving the digest is exported.
docker() {
  echo "docker $* sha_env=$(bash -c 'echo "${FACTORY_MODEL_REGISTRY_SHA256:-unset}"')" >> "$STUB_LOG"
  return 0
}
export -f docker

unset FACTORY_MODEL_ENDPOINT FACTORY_MODEL_REGISTRY_SHA256
dispatch "Fix issue #3"
assert_eq "no staging call when FACTORY_MODEL_ENDPOINT is unset" \
  "0" "$(grep -c 'model-endpoints stage' "$STUB_LOG" || true)"
assert_eq "no digest flag when FACTORY_MODEL_ENDPOINT is unset" \
  "0" "$(grep -c -- '-e FACTORY_MODEL_REGISTRY_SHA256' "$STUB_LOG" || true)"
assert_eq "no digest in the compose env when FACTORY_MODEL_ENDPOINT is unset" \
  "1" "$(grep -c 'sha_env=unset' "$STUB_LOG" || true)"

> "$STUB_LOG"
export FACTORY_MODEL_ENDPOINT=cloud-anthropic
PROVIDERS_CLI_OUTPUT="$(printf 'a%.0s' {1..64})"
dispatch "Fix issue #3"
assert_eq "staging runs before the dispatch" \
  "1" "$(grep -c 'providers/cli.py model-endpoints stage' "$STUB_LOG" || true)"
assert_eq "endpoint selection is forwarded by name" \
  "1" "$(grep -c -- 'run -e FACTORY_MODEL_ENDPOINT ' "$STUB_LOG" || true)"
assert_eq "digest is forwarded by name, after the provider flags" \
  "1" "$(grep -c -- ' -e FACTORY_MODEL_REGISTRY_SHA256 -d --rm dark-factory' "$STUB_LOG" || true)"
assert_eq "the staged digest is exported to docker compose" \
  "1" "$(grep -c "sha_env=${PROVIDERS_CLI_OUTPUT}\$" "$STUB_LOG" || true)"
assert_eq "the digest does not outlive the dispatch call" "unset" "${FACTORY_MODEL_REGISTRY_SHA256:-unset}"

> "$STUB_LOG"
PROVIDERS_CLI_OUTPUT=""
EXIT_CODE=0
dispatch "Fix issue #3" 2>/dev/null || EXIT_CODE=$?
assert_eq "an empty staging result refuses the dispatch" "1" "$EXIT_CODE"
assert_eq "no compose run after a staging failure" \
  "0" "$(grep -c '^docker compose' "$STUB_LOG" || true)"
unset FACTORY_MODEL_ENDPOINT
reset_python3_stub
docker() { echo "docker $*" >> "$STUB_LOG"; return 0; }
export -f docker
```

  (b) in section `P: Provider preflight gate`, directly after the existing
  `assert_eq "sourcing calls providers preflight before the poll loop" ...` statement (two
  lines), insert:

```bash
assert_eq "scheduler preflight runs the scheduler-side check (#557)" \
  "1" "$(echo "$SOURCE_TIME_LOG" | grep -c 'providers/cli.py preflight --side scheduler' || echo 0)"
```

- [ ] **Step 3: Run and watch them fail.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_env_passthrough.py -q
bash tests/test_scheduler.sh 2>&1 | grep -E "FAIL|Results"
```

Expected: `2 failed, 6 passed`. The scheduler suite shows new `FAIL`s for the C3 staging
assertions and the `--side scheduler` assertion, on top of the pre-existing `G2`/`I2`
failures.

- [ ] **Step 4: Implement — boot preflight.** In `scheduler.sh` replace the line
  `python3 "$FACTORY_PROVIDERS_CLI" preflight` (under
  `# --- Validate required environment (provider-aware; parent spec §4) ---`) with:

```bash
python3 "$FACTORY_PROVIDERS_CLI" preflight --side scheduler
```

- [ ] **Step 5: Implement — dispatch.** In `dispatch()` replace:

```bash
  mapfile -t env_flags < <(provider_env_flags)
  docker compose -f /opt/dark-factory/docker-compose.yml "${profile_flags[@]}" run \
```

with exactly these lines (three added; no other line of `scheduler.sh` changes):

```bash
  mapfile -t env_flags < <(provider_env_flags)
  local -x FACTORY_MODEL_REGISTRY_SHA256  # #557: set only by model_registry_env_flags, for this run
  model_registry_env_flags env_flags \
    || { echo "[$(date -u +%FT%TZ)] dispatch_error command=\"${command}\" reason=model_registry_stage_failed" >&2; return 1; }
  docker compose -f /opt/dark-factory/docker-compose.yml "${profile_flags[@]}" run \
```

- [ ] **Step 6: Run and watch them pass.**

```bash
bash -n scheduler.sh
PYTHONPATH=scripts python -m pytest tests/test_provider_env_passthrough.py -q
bash tests/test_scheduler.sh 2>&1 | grep -E "C3|FAIL|Results"
```

Expected: no syntax error; `8 passed`. The scheduler suite shows every C3 assertion `PASS`,
and `FAIL` lines only for the pre-existing `G2` and `I2` (`Results: 309 passed, 2 failed`).

- [ ] **Step 7: Commit.**

```bash
git add scheduler.sh tests/test_provider_env_passthrough.py tests/test_scheduler.sh
git commit -m "feat(model): scheduler stages the endpoint registry per dispatch (#557)"
```

---

## Task 10: Read-only registry mount on the scheduler (owner-authorised `deploy/` change)

**Files:**
- Modify: `tests/test_run_compose.sh`
- Modify: `deploy/docker-compose.yml`
- Create: `deploy/model-registry.empty/.gitkeep`

Owner authorisation (issue #557 comment, 2026-11-23; spec Requirement 12) covers exactly one
read-only mount on `backlog-scheduler` in `deploy/docker-compose.yml`, plus
`deploy/model-registry.empty/`. Nothing else under `deploy/` changes.

- [ ] **Step 1: Write the failing test.** In `tests/test_run_compose.sh`, insert this block
  immediately before the `# 4) Parse check via docker compose config` comment:

```bash
# 3e) Model endpoint registry (#557): the scheduler alone gets one read-only mount of the
# instance's model-registry/ (default: the empty tracked placeholder). Run containers get
# the staged copy through the existing state volume, so run-compose.yml gains no mount.
grep -qF -- '- ${FACTORY_MODEL_REGISTRY_DIR:-./model-registry.empty}:/etc/dark-factory/model-registry:ro' "$DC" \
  || { echo "FAIL: deploy compose missing the read-only model registry mount"; exit 1; }
[ "$(grep -c ':/etc/dark-factory/model-registry' "$DC")" -eq 1 ] \
  || { echo "FAIL: deploy compose mounts the model registry more than once"; exit 1; }
[ -f "$REPO_ROOT/deploy/model-registry.empty/.gitkeep" ] \
  || { echo "FAIL: deploy/model-registry.empty/.gitkeep placeholder missing"; exit 1; }
[ "$(ls -A "$REPO_ROOT/deploy/model-registry.empty")" = ".gitkeep" ] \
  || { echo "FAIL: deploy/model-registry.empty/ must hold only .gitkeep"; exit 1; }
if grep -q 'model-registry' "$RC"; then
  echo "FAIL: run-compose.yml must not mount the model registry (staged via scheduler_state)"; exit 1
fi
```

- [ ] **Step 2: Run and watch it fail.**

```bash
bash tests/test_run_compose.sh
```

Expected: `FAIL: deploy compose missing the read-only model registry mount` (exit 1).

- [ ] **Step 3: Implement — the mount.** In `deploy/docker-compose.yml`, under
  `backlog-scheduler:` → `volumes:`, replace:

```yaml
      - ${PROJECT_DIR:-..}:/workspace/project:ro
    networks:
      - dark-factory-net
    # Optional: structured logging to Seq/Gelf. Comment out if you don't run Seq.
```

with:

```yaml
      - ${PROJECT_DIR:-..}:/workspace/project:ro
      # Model endpoint registry (#557): this instance's own model-registry/ directory, read
      # only. FACTORY_MODEL_REGISTRY_DIR is interpolated at `up` time like PROJECT_DIR. Unset,
      # the empty tracked placeholder is mounted, and nothing reads the mount unless
      # FACTORY_MODEL_ENDPOINT is set. See docs/configuration.md "Model endpoint registry".
      - ${FACTORY_MODEL_REGISTRY_DIR:-./model-registry.empty}:/etc/dark-factory/model-registry:ro
    networks:
      - dark-factory-net
    # Optional: structured logging to Seq/Gelf. Comment out if you don't run Seq.
```

- [ ] **Step 4: Implement — the placeholder.**

```bash
mkdir -p deploy/model-registry.empty
: > deploy/model-registry.empty/.gitkeep
```

- [ ] **Step 5: Run and watch it pass.**

```bash
bash tests/test_run_compose.sh
```

Expected: `PASS: test_run_compose.sh` (plus `PASS: docker compose config parses
run-compose.yml` when docker is available).

- [ ] **Step 6: Confirm compose resolves the mount (when docker is available).**

```bash
printf '' > /tmp/empty-instance.env
(cd deploy && FACTORY_INSTANCE_ENV=/tmp/empty-instance.env docker compose -f docker-compose.yml config | grep -A2 'target: /etc/dark-factory/model-registry')
```

Expected: `target: /etc/dark-factory/model-registry` followed by `read_only: true`. The source
line just above it is `<repo>/deploy/model-registry.empty`.

- [ ] **Step 7: Commit.**

```bash
git add deploy/docker-compose.yml deploy/model-registry.empty/.gitkeep tests/test_run_compose.sh
git commit -m "feat(deploy): read-only model registry mount on the scheduler (#557)

Owner-authorised deploy/ change (issue #557, spec Requirement 12): one
read-only mount on backlog-scheduler plus the empty default source."
```

---

## Task 11: Documentation — env placement, revert path, citations

**Files:**
- Modify: `docs/configuration.md`
- Modify: `docs/scheduler.md`, `docs/run-lifecycle.md`, `docs/features.md` (citations only)

- [ ] **Step 1: Re-point the `scheduler.sh:N` citations shifted by Task 9.** `dispatch()` gained 3
  lines after line 398, so every cited line ≥ 399 moves by +3. Run this one-off script. It is
  not committed: save it under `/tmp`.

```bash
cat > /tmp/shift_scheduler_citations.py <<'EOF'
"""One-off for #557: re-point `scheduler.sh:N` citations after dispatch() grew 3 lines."""
import re
import sys
from pathlib import Path

FIRST_SHIFTED, DELTA = 399, 3  # original line 399 is now line 402
CITE = re.compile(r"`scheduler\.sh:([0-9][0-9,-]*)`")
NUM = re.compile(r"\d+")


def _shift(m):
    spec = NUM.sub(lambda n: str(int(n[0]) + DELTA) if int(n[0]) >= FIRST_SHIFTED else n[0], m[1])
    return f"`scheduler.sh:{spec}`"


for doc in sys.argv[1:]:
    path = Path(doc)
    text = path.read_text(encoding="utf-8")
    path.write_text(CITE.sub(_shift, text), encoding="utf-8")
EOF
python3 /tmp/shift_scheduler_citations.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```

- [ ] **Step 2: Verify each re-pointed citation still lands on the same text.**

```bash
python3 - <<'EOF'
import re
import subprocess


def show(path):
    return subprocess.run(["git", "show", f"origin/main:{path}"],
                          capture_output=True, text=True, check=True).stdout


old = show("scheduler.sh").splitlines()
new = open("scheduler.sh", encoding="utf-8").read().splitlines()
cite = re.compile(r"`scheduler\.sh:([0-9][0-9,-]*)`")
checked, changed = 0, set()
for doc in ("docs/run-lifecycle.md", "docs/scheduler.md", "docs/features.md", "docs/configuration.md"):
    before = cite.findall(show(doc))
    after = cite.findall(open(doc, encoding="utf-8").read())
    assert len(before) == len(after), doc
    for a, b in zip(before, after):
        for x, y in zip(map(int, re.findall(r"\d+", a)), map(int, re.findall(r"\d+", b))):
            checked += 1
            if x <= len(old) and old[x - 1] != new[y - 1]:
                changed.add(x)
print(f"checked {checked} cited lines; changed text only at line 45: {changed <= {45}}")
EOF
```

Expected: `checked 226 cited lines; changed text only at line 45: True`. Line 45 is the
`preflight` line Task 9 edited on purpose.

- [ ] **Step 3: Fix the other shifted citations in `docs/configuration.md`.**
  - `scripts/provider_env.sh:26-40` → `scripts/provider_env.sh:26-41` (the array gained one
    comment line in Task 8).
  - `providers/__init__.py:24,48` → `providers/__init__.py:24,52`
  - `providers/__init__.py:32,55` → `providers/__init__.py:32,59`
  - `providers/__init__.py:62` → `providers/__init__.py:66` (Task 6's docstring added 4 lines).

  And in `docs/features.md`, `deploy/docker-compose.yml:96`-`:98` →
  `deploy/docker-compose.yml:101`-`:103` (Task 10 added 5 lines above the GELF block).
  Confirm with:

```bash
sed -n '26p;41p' scripts/provider_env.sh
sed -n '52p;59p;66p' scripts/factory_core/providers/__init__.py
sed -n '101,103p' deploy/docker-compose.yml
```

Expected: `PROVIDER_PASSTHROUGH_VARS=(` and `)`; the `tracker_name =`, `codehost_name =`,
`model_name =` lines; the three commented GELF lines (`#   driver: gelf` …).

- [ ] **Step 4: Rule 3 of "Which file goes where".** In `docs/configuration.md` replace:

```markdown
   non-empty in the scheduler. A forwarded value beats `.archon/.env`; unset or empty leaves
   `.archon/.env` in control. The list: `FACTORY_TRACKER FACTORY_CODEHOST FACTORY_MODEL_PROVIDER`,
```

with:

```markdown
   non-empty in the scheduler. A forwarded value beats `.archon/.env`; unset or empty leaves
   `.archon/.env` in control. When `FACTORY_MODEL_ENDPOINT` is set, `dispatch()` also stages the
   model endpoint registry and adds `-e FACTORY_MODEL_REGISTRY_SHA256` (see "Model endpoint
   registry" below). The list: `FACTORY_TRACKER FACTORY_CODEHOST FACTORY_MODEL_PROVIDER FACTORY_MODEL_ENDPOINT`,
```

- [ ] **Step 5: Provider selection table.** Directly below the `FACTORY_MODEL_PROVIDER` row of
  the "Provider selection" table, add:

```markdown
| `FACTORY_MODEL_ENDPOINT` | unset (a registry `id`) | `scripts/factory_core/providers/__init__.py`, `scripts/factory_core/providers/model_endpoint.py`; see "Model endpoint registry" below | `instance.env` |
```

- [ ] **Step 6: New subsection.** Insert this immediately before
  `## Layer 3: the target adapter (`.factory/adapter.yaml`)`:

```markdown
### Model endpoint registry (#557)

Optional. With `FACTORY_MODEL_ENDPOINT` unset (the default) nothing below is read, staged or
checked, and dispatch argv and env are unchanged. Design:
`docs/superpowers/specs/2026-11-23-endpoint-registry-secrets-preflight-design.md`.

| Variable | Set where | Read by | Notes |
|---|---|---|---|
| `FACTORY_MODEL_ENDPOINT` | scheduler's `instance.env` | scheduler boot preflight; forwarded to runs by `dispatch()` | selects one registry `id`; an unknown id fails preflight, no other endpoint is chosen |
| `FACTORY_MODEL_REGISTRY_DIR` | compose interpolation at `up` time (the `--env-file` file or the shell), like `PROJECT_DIR` | `deploy/docker-compose.yml` | host path of this instance's own `model-registry/` directory; unset mounts the empty `deploy/model-registry.empty/` |
| `FACTORY_MODEL_REGISTRY_SHA256` | never set by hand | run-container preflight | computed by `dispatch()` per run and passed only with `docker compose run -e`; not in `PROVIDER_PASSTHROUGH_VARS` |
| the variable a `secret_reference` names | cloud entry: the run container's `.archon/.env` (for example `ANTHROPIC_API_KEY`); local entry: not `.archon/.env`, defined by #558/#587 | run-container preflight (presence only) | the registry stores the name, never the value |
| `FACTORY_MODEL_REGISTRY_PATH` | tests only | `model_endpoint.staged_registry_dir()` | overrides the staged path; never forwarded |

The registry lives on the human-only instance surface,
`deploy/instances/<instance>/model-registry/model-endpoints.yaml`, with one
`capability-evidence/<endpoint_id>.yaml` per local entry beside it. The scheduler mounts that
directory read-only at `/etc/dark-factory/model-registry`. Before each dispatch it copies it to
`/var/lib/dark-factory/model-registry` on the shared state volume and passes the copy's SHA-256
to that run. The run container's preflight fails if the digest is missing or does not match,
and also if a digest arrives with no endpoint selected. A local selection also fails if any
cloud model credential is set in the container, if `ANTHROPIC_BASE_URL` or any of the four
`ANTHROPIC_DEFAULT_*_MODEL` aliases does not match the entry, if its capability evidence is
not `tested`, and in every case until #587 ships a local-eligible dispatch.

Inspect the effective configuration without printing secret values:
`python3 /opt/dark-factory/scripts/factory_core/providers/cli.py model-endpoints show --from-source`
in the scheduler container (the read-only mount), or `model-endpoints show` in a run container
(the staged copy, checked against its digest). Each `secret_reference` prints as its name and
whether it is set. With an endpoint selected, the README's offline check
(`providers/cli.py preflight` with `--env-file deploy/instance.env`) fails on the missing
per-dispatch digest by design. Check offline with `preflight --side scheduler` and the
registry directory mounted at `/etc/dark-factory/model-registry`
(`-v <host model-registry dir>:/etc/dark-factory/model-registry:ro`), or with
`model-endpoints show --from-source`.

**Back to default cloud:** remove `FACTORY_MODEL_ENDPOINT` from `instance.env` (and
`FACTORY_MODEL_REGISTRY_DIR` from the interpolation env), then recreate the scheduler with
`docker compose -f deploy/docker-compose.yml up -d`. No staged copy is read while
`FACTORY_MODEL_ENDPOINT` is unset. The leftover `/var/lib/dark-factory/model-registry` is
inert and can be deleted.
```

- [ ] **Step 7: Run the reference-doc checks.**

```bash
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
PYTHONPATH=scripts python -m pytest tests/test_docs_refs_check.py tests/test_docs_conformance_reference_docs.py -q
```

Expected: `Docs reference check passed for 4 doc(s).`, then all passed.

- [ ] **Step 8: Commit.**

```bash
git add docs/configuration.md docs/scheduler.md docs/run-lifecycle.md docs/features.md
git commit -m "docs(model): endpoint registry env placement and default-cloud revert path (#557)"
```

---

## Task 12: Full verification

- [ ] **Step 1: Whole pytest suite (CI parity).**

```bash
PYTHONPATH=scripts python -m pytest tests/ -q
```

Expected: `3426 passed`. The baseline at `main` 3807b03 is 3257 tests; this ticket adds 159 in
`test_provider_model_endpoint.py`, 7 in `test_provider_cli.py` and 3 in
`test_provider_env_passthrough.py`. If `main` has moved, the count shifts by however many tests
it gained. Zero failures is the requirement.

- [ ] **Step 2: CI shell tests this ticket touches, plus the workflow DAG checks.**

```bash
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
bash tests/test_run_compose.sh
env -u JIRA_PROJECT_KEY -u JIRA_EPIC_LINK_FIELD -u JIRA_BASE_URL -u JIRA_TOKEN bash tests/test_provider_env_flags.sh
bash tests/test_entrypoint_preflight.sh
bash tests/test_config_deletion.sh
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```

Expected: each prints its `PASS`/passed line and exits 0.

- [ ] **Step 3: Default path is unchanged.** With `FACTORY_MODEL_ENDPOINT` unset:

```bash
git diff origin/main -- scripts/factory_core/providers/model.py entrypoint.sh run-compose.yml
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py -q -k default_path
```

Expected: empty diff, then `2 passed`: the registry module is never imported, and the same
cloud problems are reported as before. Default-path dispatch argv/env equality is pinned by
`tests/test_scheduler.sh` C3 ("no staging call / no digest flag / no digest in the compose
env when FACTORY_MODEL_ENDPOINT is unset") and `tests/test_provider_env_flags.sh`.

- [ ] **Step 4: Scope check.**

```bash
git diff --name-only origin/main HEAD | sort
```

(Two-dot on purpose: three-dot can flag files whose content already matches `main`; see
codebase-patterns memory.) Expected: exactly the files in the File Structure table, plus the
spec/plan/memory files the refine and plan phases already committed (`docs/superpowers/specs/…`,
`docs/superpowers/plans/…`, `.archon/memory/architecture.md`). No `deploy/instances/**`, no
`.github/**`.

## Acceptance-criteria trace

| Issue criterion | Where it is satisfied |
|---|---|
| Default cloud invocation and argv/env compatibility stay green | Task 6 (`test_default_path_*`), Task 9 C3 unset-path assertions, Task 12 Steps 1/3; `model.py`, `entrypoint.sh`, `run-compose.yml` untouched |
| Missing/unknown endpoint, unsupported protocol, missing credential, stale/incompatible evidence fail visibly, no silent reroute | Task 1 (protocol/schema), Task 3 (stale/unknown/declared), Task 4 (unknown id "no other endpoint is chosen instead", secret missing, digest), Task 6 wiring. *Unreachable service → #558* (spec "Deviations", accepted by the operator) |
| No cloud credential to a local endpoint; secrets absent from persisted config and logs | Task 1 (cross-credential guard, no userinfo in `base_url`, no echo), Task 4 (environment guard, sentinel tests), Task 7 (`show` sentinel test) |
| Redacted effective-configuration view + same-host/LAN/remote schema/contract tests | Task 1 fixture (one schema, three `base_url` shapes), Task 5 `base_url_scope` / `redacted_view`, Task 7 `model-endpoints show` |
| Environment placement (scheduler vs run) + reversible default-cloud path | Task 11 "Model endpoint registry (#557)" table and "Back to default cloud" |
| Payload-class field, dispatch-time predicate and decision (§4.3); operator-controlled evidence storage (§4.5), not writable by any dispatched container | Task 5 (predicate + decision shape; the ledger write is #558's per spec "Deviations"); Tasks 2/9/10 (read-only source mount + digest; staged copy never trusted without the per-dispatch digest) |
