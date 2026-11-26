# Opt-in Local Inference Deployment Profile and Operator Runbook — Implementation Plan

**Issue:** #560

**Spec:** `docs/superpowers/specs/2026-11-25-local-inference-deployment-profile-design.md`
(approved at 9ffc394 with the operator's 2026-11-25 amendments; where the spec's Q&A and its
later sections differ, the later sections win, and so does this plan).

**Goal:** Give an operator a documented, opt-in way to register a candidate local inference
endpoint (the LAN Ollama case first) against the existing #557 registry and #558 adapter, and
check its connectivity and resource fit. Ship a no-purchase test path. Change no production file
and activate no routing: `_NO_LOCAL_DISPATCH` stays.

**Architecture:** Documentation and tests only. There is one new operator runbook,
`docs/local-inference-deployment-runbook.md`, and a one-line cross-reference in
`docs/configuration.md`. One registry fixture entry, `lan-ollama-qwen3-coder`, is appended; it
is the runbook's worked example. Five new tests sit on the existing harnesses: two in
`tests/test_provider_model_endpoint.py` (the profile validates but routes nothing; the
unselected profile leaves the cloud default path unchanged) and three in
`tests/test_factory_model_proxy.py` (this profile's timeouts at 1/20 scale against the in-process
fake upstream). No path under `deploy/**`, `run-compose.yml`, `.github/workflows/*`, `scripts/**`,
`scheduler.sh`, `entrypoint.sh` or `config/**` changes.

**Tech Stack:** Markdown; Python 3 + pytest; PyYAML; aiohttp's `TestServer`/`TestClient` (already
used by `tests/test_factory_model_proxy.py`).

---

## Two things this plan does beyond the spec's literal file list (read first)

1. **`tests/test_provider_cli.py` must be updated too.** The spec's "Files touched" table lists
   only `tests/test_provider_model_endpoint.py` for the fixture's id-list update. On a scratch
   copy of `main`, appending the fixture entry fails **three** tests, not one:
   - `tests/test_provider_model_endpoint.py::test_fixture_registry_loads_same_host_lan_remote_and_cloud` (the spec's)
   - `tests/test_provider_cli.py::test_model_endpoints_show_from_source_needs_no_digest` (asserts `== 4` endpoints, line 484)
   - `tests/test_provider_cli.py::test_model_endpoints_show_uses_only_the_first_read_of_the_staged_registry` (asserts the exact id list, line 602)

   The spec's deliverable 3 asks for exactly this change ("update the id list ... which would
   otherwise fail"); the two CLI assertions are the same kind of change, so Task 1 applies it to
   them too. The full suite was otherwise green on the scratch copy: only those 3 failed.
   With every task applied it is the baseline count plus 5, 0 failed.
2. **The cloud-only default test is stronger than the existing one it is modelled on.** Like
   `test_default_path_never_loads_the_registry_module`, it deletes the module from `sys.modules`.
   It also deletes the package attribute (`monkeypatch.delattr(providers, name)`). That matters:
   `from factory_core.providers import model_endpoint` does not re-import while the package
   attribute exists, so a `sys.modules`-only check passes even if the default path imports the
   module. This was verified on the scratch copy with a deliberate import injected into
   `providers.preflight()`: the existing test still **passed**, and the new test **failed**. The
   existing test is not modified; that is out of scope. It is noted here as a candidate spillover.

## File Structure

| Path | Change | Task |
|---|---|---|
| `tests/fixtures/model_registry/model-endpoints.yaml` | append one entry, `lan-ollama-qwen3-coder`; the four existing entries are not edited | 1 |
| `tests/test_provider_model_endpoint.py` | `import shutil`; id list updated; `LAN_OLLAMA` constant and `test_lan_ollama_profile_validates_but_activates_no_routing`; `test_unselected_lan_profile_leaves_the_cloud_default_path_unchanged` | 1, 2 |
| `tests/test_provider_cli.py` | two fixture-count/id-list assertions updated (see note 1) | 1 |
| `tests/test_factory_model_proxy.py` | `_lan_profile_timeouts()` helper and three `test_lan_profile_*` tests | 3 |
| `docs/local-inference-deployment-runbook.md` | new | 4 |
| `docs/configuration.md` | one cross-reference line in "Model endpoint registry (#557)" | 5 |

No capability-evidence fixture is added. `docs/hybrid-execution-contract.md` is not edited, and
neither is `docs/dark-factory-model-proxy-rollout.md`. Nothing in `.factory/adapter.yaml`'s
`hard_exclude_paths` is touched, and no owner authorisation is needed.

## Conventions for every task

- Run from the repository root. Tests run as CI runs them: `PYTHONPATH=scripts python -m pytest …`.
- Commit only the files the task lists, with `git add <paths>` and never `git add -A`. Each
  commit message ends with the attribution line
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Tasks 2 and 3 add *characterization* tests, which pin behaviour #558 already ships at this
  profile's values. They pass on first run. Their "verify the test can fail" step is a temporary,
  uncommitted mutation that is reverted with `git checkout --`, and `git status --short` must show
  the mutated file clean afterwards.

---

## Task 1: The LAN Ollama fixture entry, pinned by tests first

**Files:**
- Modify: `tests/test_provider_model_endpoint.py`
- Modify: `tests/test_provider_cli.py`
- Modify: `tests/fixtures/model_registry/model-endpoints.yaml`

- [ ] **Step 1: Update the fixture id list in `tests/test_provider_model_endpoint.py`.**
  Replace

  ```python
  def test_fixture_registry_loads_same_host_lan_remote_and_cloud():
      endpoints = me.load_registry(FIXTURES / me.REGISTRY_FILE)
      assert [e["id"] for e in endpoints] == [
          "local-same-host", "local-lan", "local-remote", "cloud-anthropic"]
  ```

  with

  ```python
  def test_fixture_registry_loads_same_host_lan_remote_and_cloud():
      endpoints = me.load_registry(FIXTURES / me.REGISTRY_FILE)
      assert [e["id"] for e in endpoints] == [
          "local-same-host", "local-lan", "local-remote", "cloud-anthropic", "lan-ollama-qwen3-coder"]
  ```

- [ ] **Step 2: Update the two CLI assertions in `tests/test_provider_cli.py`.**
  In `test_model_endpoints_show_from_source_needs_no_digest`, replace

  ```python
      assert len(json.loads(capsys.readouterr().out)["endpoints"]) == 4
  ```

  with

  ```python
      assert len(json.loads(capsys.readouterr().out)["endpoints"]) == 5
  ```

  In `test_model_endpoints_show_uses_only_the_first_read_of_the_staged_registry`, replace

  ```python
      assert ids == ["local-same-host", "local-lan", "local-remote", "cloud-anthropic"]
  ```

  with

  ```python
      assert ids == ["local-same-host", "local-lan", "local-remote", "cloud-anthropic",
                     "lan-ollama-qwen3-coder"]
  ```

- [ ] **Step 3: Add the profile test to `tests/test_provider_model_endpoint.py`.**
  In the import block, add `import shutil` between `import os` and `import sys`:

  ```python
  import copy
  import io
  import os
  import shutil
  import sys
  ```

  Then insert the following directly after
  `test_scheduler_preflight_without_a_registry_has_no_hosting`, which ends with
  `assert "model endpoint registry not found" in problems[0] and hosting is None`, and before the
  `# --- #558: load_verified_entry …` divider. Keep two blank lines on each side:

  ```python
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
      assert f"endpoint '{LAN_OLLAMA}': capability evidence is 'unknown', not 'tested' (contract §4.5)" in problems
      assert problems[-1] == me._NO_LOCAL_DISPATCH.format(eid=LAN_OLLAMA)
  ```

  Notes for the implementer. `_local_env` sets `ANTHROPIC_BASE_URL` and the four tier aliases to
  the entry's own values, so the only remaining run-side problems are the `unknown` evidence and
  `_NO_LOCAL_DISPATCH`, which `_local_problems` always appends last. `192.0.2.10` is in Python's
  `ipaddress` private ranges, so `base_url_scope` returns `lan`. The autouse `_clean_env` fixture
  does not clear `LOCAL_OLLAMA_DUMMY_TOKEN`; the test sets it through `monkeypatch`, which restores
  it.

- [ ] **Step 4: Run the tests and confirm they fail.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py tests/test_provider_cli.py -q -p no:cacheprovider
  ```

  Expected: `4 failed`. The three id/count assertions fail on the missing fifth entry, and
  `test_lan_ollama_profile_validates_but_activates_no_routing` fails with
  `KeyError: 'lan-ollama-qwen3-coder'`. Everything else passes.

- [ ] **Step 5: Append the entry to `tests/fixtures/model_registry/model-endpoints.yaml`.**
  Add it at the end of the file, after the `cloud-anthropic` entry
  (`    secret_reference: ANTHROPIC_API_KEY`). Do not edit the four existing entries or the header
  comment:

  ```yaml
    # #560: the worked example in docs/local-inference-deployment-runbook.md — keep the two
    # identical. Documentation-range address (RFC 5737) and a placeholder digest.
    - id: lan-ollama-qwen3-coder
      hosting_location: local
      protocol: anthropic-messages
      accepts_payload_classes: [local_only]
      model_identity:
        served_name: qwen3-coder-128k
        digest_or_revision: sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
      base_url: http://192.0.2.10:11434
      secret_reference: LOCAL_OLLAMA_DUMMY_TOKEN
      timeouts:
        connect_seconds: 10
        time_to_first_token_seconds: 30
        idle_seconds: 60
        total_seconds: 1800
  ```

- [ ] **Step 6: Run the tests and confirm they pass.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py tests/test_provider_cli.py -q -p no:cacheprovider
  ```

  Expected: all pass, `0 failed`. Then confirm that the existing consumers of the fixture are
  unaffected:

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py tests/test_provider_model_adapter.py -q -p no:cacheprovider
  ```

  Expected: all pass.

- [ ] **Step 7: Commit.**

  ```bash
  git add tests/fixtures/model_registry/model-endpoints.yaml tests/test_provider_model_endpoint.py tests/test_provider_cli.py
  git commit -m "test(#560): LAN Ollama registry fixture entry validates but routes nothing

  Appends lan-ollama-qwen3-coder (documentation-range address, placeholder
  digest, 10/30/60/1800 timeouts) and pins that both preflight sides still
  fail it on _NO_LOCAL_DISPATCH. Updates the three assertions that pin the
  fixture's ids/count, including two in test_provider_cli.py the spec's
  file list did not name.

  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  ```

---

## Task 2: The unselected profile leaves the cloud default path unchanged

**Files:**
- Modify: `tests/test_provider_model_endpoint.py`

- [ ] **Step 1: Append the test at the end of `tests/test_provider_model_endpoint.py`.**
  It goes after `test_scheduler_side_reads_the_source_mount_without_a_digest`, the last test in
  the `# --- Task 6: providers.preflight() wiring` section, which uses the `base_env` fixture
  defined there:

  ```python


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
  ```

  The `_clean_env` autouse fixture already clears `FACTORY_MODEL_ENDPOINT` and
  `FACTORY_MODEL_REGISTRY_SHA256`, so the test exercises the default path with the new profile
  present on both the staged path and the source mount.

- [ ] **Step 2: Run it.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py -q -p no:cacheprovider -k "unselected_lan_profile or default_path"
  ```

  Expected: `3 passed`: the new test, `test_default_path_never_loads_the_registry_module` and
  `test_default_path_reports_the_same_cloud_problems_as_before`. This is a characterization test:
  #560 changes no production file, so cloud-only dispatch is identical by construction, and the
  test pins it.

- [ ] **Step 3: Prove the test can fail (temporary mutation, not committed).**

  ```bash
  sed -i 's/^    problems = \[\]$/    problems = []\n    from factory_core.providers import model_endpoint, model_adapter  # MUTATION/' scripts/factory_core/providers/__init__.py
  grep -n "MUTATION" scripts/factory_core/providers/__init__.py
  PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py -q -p no:cacheprovider -k "unselected_lan_profile"
  git checkout -- scripts/factory_core/providers/__init__.py
  git status --short scripts/
  ```

  Expected: `grep` prints one line, in `preflight()`. pytest reports `1 failed` with an
  `AssertionError` on the `sys.modules` check. `git status --short scripts/` prints nothing.

- [ ] **Step 4: Commit.**

  ```bash
  git add tests/test_provider_model_endpoint.py
  git commit -m "test(#560): an unselected local profile leaves the cloud default path unchanged

  With the LAN profile staged and on the source mount but
  FACTORY_MODEL_ENDPOINT unset, both preflight sides return [] and neither
  model_endpoint nor model_adapter is imported. Also drops the package
  attribute, so a from-import on the default path cannot hide behind it.

  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  ```

---

## Task 3: This profile's timeouts at scaled bounds against the fake upstream

**Files:**
- Modify: `tests/test_factory_model_proxy.py`

Scale: the registered 10 / 30 / 60 / 1800 seconds divided by 20 gives `connect_seconds: 0.5`,
`time_to_first_token_seconds: 1.5`, `idle_seconds: 3` and `total_seconds: 90`. The four ratios
hold, and no test waits anywhere near 30 seconds. Measured on the scratch copy, the slowest of the
three takes about 1.5s. The helper reads the values from the fixture entry, so the fixture, the
runbook and these tests cannot drift apart unnoticed.

- [ ] **Step 1: Insert the helper and three tests.**
  They go directly before `def _spy_on_handler(monkeypatch):`, which follows
  `test_local_slow_but_complete_stream_finishes_within_total`. They reuse the existing
  `local_env`, `_local_scenario`, `_post_json`, `_silent` and `_rows`, and the module's existing
  imports (`asyncio`, `Path`, `web`, `ma`, `me`):

  ```python
  # --- #560: the LAN Ollama profile's timeouts at scaled bounds -----------------------------

  def _lan_profile_timeouts(scale=20):
      """The fixture lan-ollama-qwen3-coder entry's timeouts divided by scale: the four ratios
      hold, and no test waits anywhere near the registered 30-second time-to-first-token bound."""
      fixture = Path(__file__).resolve().parent / "fixtures" / "model_registry" / me.REGISTRY_FILE
      entry = next(e for e in me.load_registry(fixture) if e["id"] == "lan-ollama-qwen3-coder")
      assert entry["timeouts"] == {"connect_seconds": 10, "time_to_first_token_seconds": 30,
                                   "idle_seconds": 60, "total_seconds": 1800}
      return {k: v / scale for k, v in entry["timeouts"].items()}


  def test_lan_profile_cold_start_inside_ttft_succeeds(local_env, monkeypatch):
      # 0.9s scaled is 18s: the top of the unmeasured post-reboot estimate, inside the 30s bound.
      async def cold_start(request):
          await asyncio.sleep(0.9)
          return web.json_response({"type": "message", "usage": {"input_tokens": 1, "output_tokens": 1}})

      (status, _), calls, cloud_calls = _local_scenario(
          local_env, monkeypatch, cold_start, _post_json, timeouts=_lan_profile_timeouts())
      assert status == 200 and len(calls) == 1 and cloud_calls == []
      assert _rows(local_env)[0]["failure_class"] is None


  def test_lan_profile_cold_start_past_ttft_is_its_own_class(local_env, monkeypatch):
      (status, body), calls, cloud_calls = _local_scenario(
          local_env, monkeypatch, _silent, _post_json, timeouts=_lan_profile_timeouts())
      assert status == 504 and body["error"]["failure_class"] == ma.TIMEOUT_TIME_TO_FIRST_TOKEN
      assert len(calls) == 1 and cloud_calls == []


  def test_lan_profile_queue_full_503_is_not_retried_or_sent_to_cloud(local_env, monkeypatch):
      # A stand-in for the runtime refusing a request because its own queue is full.
      async def queue_full(request):
          return web.json_response({"type": "error", "error": {"type": "overloaded_error",
                                                                "message": "server busy"}}, status=503)

      (status, body), calls, cloud_calls = _local_scenario(
          local_env, monkeypatch, queue_full, _post_json, timeouts=_lan_profile_timeouts())
      assert status == 503 and body["error"]["type"] == "overloaded_error"
      assert len(calls) == 1 and cloud_calls == []
      assert _rows(local_env)[0]["failure_class"] == ma.ENDPOINT_UNREACHABLE


  ```

  `_local_scenario` builds the destination from the module's `LOCAL_ENTRY`, with its served name
  `qwen2.5-coder-32b`, and overwrites all four timeouts with the scaled profile. The served name
  does not matter here; only the timeout profile is under test. `len(calls) == 1` is the
  no-retry assertion, and `cloud_calls == []` the no-cloud-fallback one. `_silent` sleeps 5s and
  never answers inside the 1.5s bound.

- [ ] **Step 2: Run them.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py -q -p no:cacheprovider -k "lan_profile" --durations=3
  ```

  Expected: `3 passed`, each in under about 2s. These are characterization tests that pin #558's
  shipped behaviour at this profile's values.

- [ ] **Step 3: Commit.**

  ```bash
  git add tests/test_factory_model_proxy.py
  git commit -m "test(#560): pin the LAN profile's timeout bounds at 1/20 scale

  Cold start inside time_to_first_token succeeds; past it is
  timeout_time_to_first_token; a queue-full 503 is endpoint_unreachable
  with one upstream call and no cloud call. Bounds come from the fixture
  entry (10/30/60/1800 divided by 20).

  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  ```

- [ ] **Step 4: Prove the bound is live (temporary mutation, not committed).**

  ```bash
  sed -i 's/^def _lan_profile_timeouts(scale=20):$/def _lan_profile_timeouts(scale=60):/' tests/test_factory_model_proxy.py
  PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py -q -p no:cacheprovider -k "lan_profile"
  git checkout -- tests/test_factory_model_proxy.py
  git status --short tests/
  ```

  Expected: `1 failed, 2 passed`. `test_lan_profile_cold_start_inside_ttft_succeeds` fails because
  the bound is now 0.5s, below the 0.9s cold start, and it gets a `504`. `git status --short
  tests/` prints nothing.

---

## Task 4: The operator runbook

**Files:**
- Create: `docs/local-inference-deployment-runbook.md`

The section order follows the spec's deliverable 1: what this is, registering an entry, secret
location, pinning identity, distinguishing modes, connectivity, persistent model storage, service
isolation, registry write protection, timeout bounds, resource budget, readiness, failure modes,
enable/disable/rollback, the no-purchase test path, and caveats. The runbook links to the
contract, the configuration doc and the rollout doc instead of duplicating them. Every address is
in `192.0.2.0/24`. The YAML example is byte-for-byte the fixture entry's data. It contains no
memory-sizing numbers and no capability-evidence example. Every test it cites exists after
Tasks 1–3, so this task comes after them (memory lesson: never cite an artifact without checking
that it exists; Step 2 checks every cited test name).

- [ ] **Step 1: Create `docs/local-inference-deployment-runbook.md` with exactly this content.**
  The outer four-backtick fence is not part of the file:

````markdown
# Local Inference Deployment Profile — Operator Runbook

Issue: #560. Spec: `docs/archive/2026-11-25-local-inference-deployment-profile-design.md`.

This runbook links to three documents and does not repeat them. Policy and invariants are in
`docs/hybrid-execution-contract.md`. The registry schema, env vars and CLI are in
`docs/configuration.md`, "Model endpoint registry (#557)". What the adapter does with a local
destination (protocol, refusals, failure classes) is in
`docs/dark-factory-model-proxy-rollout.md`, "Local destination: supported and uncertified modes
(#558)".

## What this is

An opt-in profile for registering a candidate local inference endpoint: a model served on your
own hardware, on the Docker host or on your LAN. You use it to check the endpoint's connectivity
and resource fit before certification (#562) and the first local-eligible dispatch (#587) exist.

It changes no default behaviour. With `FACTORY_MODEL_ENDPOINT` unset (the default), nothing in
this runbook is read, staged or checked, and dispatch argv and env are unchanged.

It routes nothing either. Every local registry entry fails preflight today: `_NO_LOCAL_DISPATCH`
in `scripts/factory_core/providers/model_endpoint.py` rejects any `hosting_location: local`
selection until #587 ships. The proxy service receives none of the endpoint variables; delivering
them is also #587's. Live activation needs #562's certification and #587's first local-eligible
dispatch. It is not part of this runbook.

"Operator" means the person who controls the instance's `deploy/instances/<instance>/` directory
and the scheduler's env files. Every step below is the operator's, on their own host. The factory
never performs any of them.

The worked example is an Ollama server on a LAN machine that serves the Anthropic Messages API at
`/v1/messages` over plain http with no API key. Every address in this runbook is a
documentation-range placeholder (RFC 5737, `192.0.2.0/24`). Substitute your own.

## Registering an entry

Add the entry to your instance's registry file,
`deploy/instances/<instance>/model-registry/model-endpoints.yaml`. You can keep the directory
anywhere on the Docker host instead: `FACTORY_MODEL_REGISTRY_DIR` names it (see "Enable / disable
/ rollback"). Do not commit the file. The real address and digest stay in your own instance
config.

```yaml
schema_version: "1.0"
endpoints:
  - id: lan-ollama-qwen3-coder
    hosting_location: local
    protocol: anthropic-messages
    accepts_payload_classes: [local_only]
    model_identity:
      served_name: qwen3-coder-128k
      digest_or_revision: sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef   # placeholder — see "Pinning identity"
    base_url: http://192.0.2.10:11434   # documentation-range placeholder (RFC 5737); the real address is never committed
    secret_reference: LOCAL_OLLAMA_DUMMY_TOKEN   # name only; Ollama needs no real key
    timeouts:
      connect_seconds: 10
      time_to_first_token_seconds: 30   # see "Timeout bounds"
      idle_seconds: 60
      total_seconds: 1800
```

The digest and the address are placeholders in a form the schema accepts. This example is the
same entry as `lan-ollama-qwen3-coder` in `tests/fixtures/model_registry/model-endpoints.yaml`,
which the tests validate (see "No-purchase test path"). Replace both values with your own.

## Secret location

The schema requires a `secret_reference` on every entry. It is an env-var *name* matching
`^[A-Z][A-Z0-9_]*$`, never a value. A local entry may never name a cloud credential
(`CLOUD_CREDENTIALS` in `model_endpoint.py`).

- **An endpoint with no API key still needs the name.** Set the named variable
  (`LOCAL_OLLAMA_DUMMY_TOKEN` in the example) to any non-empty dummy value. A dummy value is not
  authentication and protects nothing.
- **Where it goes today:** the scheduler's `instance.env` (`deploy/instance.env`, or the file
  `FACTORY_INSTANCE_ENV` names; keep it uncommitted, like the credentials already in it), or the
  `-e` of a one-off validation container. Its only effect
  there is the `present: true` flag that `model-endpoints show --from-source` prints. It is not in
  `PROVIDER_PASSTHROUGH_VARS`, so it is not forwarded to run containers.
- **Where it must not go:** `.archon/.env` (a local entry's secret is never delivered that way;
  see `docs/configuration.md`, "Model endpoint registry"), any tracked file, or the registry file
  itself.
- **Not possible yet:** the proxy sends this value as a local request's only `x-api-key`, but the
  proxy service receives none of the endpoint variables today. Delivering them is #587's. There
  is no step for it here.

## Pinning identity

The registry pins the served name and the digest. Record everything else beside the entry, in
your own instance notes.

- **Served name.** `served_name` is the exact name the endpoint serves (`ollama list`). Every
  request carries it, and the adapter refuses any other `model`. The schema does not check that a
  served name is immutable; only `digest_or_revision` rejects `latest`-style aliases. An untagged
  Ollama name resolves to its implicit `latest` tag. Keeping the name stable is your obligation:
  never re-create the served model under the same name with different parameters. Doing so
  invalidates any later capability evidence (#562).
- **Digest.** `digest_or_revision` is the full `sha256:` digest of the served model. Read it from
  the endpoint's model listing, `GET <base_url>/api/tags`: take the `digest` of the matching entry
  and write it as `sha256:<64 hex characters>`. `ollama list` prints only a short form, and
  `ollama show <name> --modelfile` prints no digest. Re-pulling, re-quantising or re-creating the
  model changes the digest.
- **Runtime, template and quantisation.** The registry schema has no field for these, so record
  them in your notes: the runtime version (`ollama --version`), and the chat template,
  quantisation, parameter size and context length (`ollama show <name>`; the template text with
  `ollama show <name> --template`). The capability-evidence record that carries these fields
  (`runtime_stack`, `quantisation`) is #562's. This runbook never creates one.
- **Context length is part of the identity.** Choose it before you record the digest. Changing it
  re-creates the served model.
- **Provenance and licence.** Record the upstream licence and where the weights came from (the
  runtime's library page or the upstream model card) in the same notes. Check that the licence
  permits your use before you register the entry.

## Distinguishing modes

This profile is for real local weights, served by a runtime on hardware you control. A
cloud-backed stand-in is a different thing: a hosted model presented through a local-looking
address, such as a hosted OpenAI-compatible endpoint or a runtime's cloud-model passthrough. It
does not satisfy the intent of `hosting_location: local`, and you must not register it as local.
The registry's `hosting_location` is the only source of truth for where a request goes. Every
example in this runbook is the genuine local case.

## Connectivity: same host or LAN

Both cases are expressed only as `base_url`. No workflow logic changes either way.

- **Which containers must reach the endpoint.** The run container and the `factory-model-proxy`
  container. Both attach to the instance's `<instance>_dark-factory-net` bridge network
  (`run-compose.yml`), so one check from that network covers both (see "Readiness").
- **LAN.** `base_url` is the model machine's LAN address, as in the example
  (`http://192.0.2.10:11434`). Containers on a bridge network reach the LAN through the Docker
  host's own route. No new Docker network is needed.
- **Same host.** A container's `localhost` is the container itself, never the Docker host.
  `http://host.docker.internal:<port>` resolves only where the Docker runtime provides that name
  (Docker Desktop). On Linux Docker Engine it does not resolve without an `extra_hosts` entry on
  the service, which would be a `run-compose.yml` change; do not make one. Use the host's address
  on the instance bridge network instead (the gateway `docker network inspect
  <instance>_dark-factory-net` shows), or the host's LAN address.
- **Listen address.** The model service must listen on an interface the containers can reach. A
  service bound only to the host's loopback interface is not reachable from a container.
- **`base_url_scope`.** `model-endpoints show` prints it for each local entry. It is informational
  only; a bridge or LAN address prints as `lan`.

## Persistent model storage

- Model weights live in the runtime's own model store, at a location you choose. For Ollama, the
  `OLLAMA_MODELS` setting names it.
- The store must survive a service restart, a host reboot and, if the runtime itself runs in a
  container, the re-creation of that container. Use a named volume or bind mount owned by the
  model service, never one of the factory's volumes.
- The store's size and the free space left on its filesystem are measured values in the resource
  worksheet below. Leave headroom for one more pull of the same model.
- After restoring or re-pulling the store, read the digest again (see "Pinning identity") and
  compare it with the registry entry before you rely on the entry.
- A full disk is covered in the failure-mode table (disk pressure).

## Service isolation and network boundary

- **No factory access.** The model service runs with no mount of any target repository or factory
  volume, no GitHub credential, no cloud model credential and no Docker socket. If it runs in a
  container, that container is not part of a factory compose project and shares none of the
  factory's volumes.
- **Bind scope.** The service listens only on the interface the factory containers use to reach
  it. Never bind it to every interface of a machine that also has a routable or public address.
  For Ollama, `OLLAMA_HOST` sets the listen address.
- **Allowed clients.** A host firewall rule, or a reverse proxy in front of the service, admits
  only the Docker host that runs the factory. For example: allow TCP 11434 on 192.0.2.10 from the
  factory's Docker host (192.0.2.20 in this example) and drop it from every other source.
- **No authentication.** The model API is unauthenticated. The dummy value sent as `x-api-key`
  is not authentication. The network boundary is the only access control, so never expose the
  service beyond it.
- **Cleartext transport.** The transport is plain http, so prompts, repository content and model
  output cross the network unencrypted. `docs/hybrid-execution-contract.md` sets no transport
  requirement. This runbook accepts plain http as a property of a LAN-only endpoint on a network
  you control. An endpoint reached over any network you do not control needs TLS in front of it
  (`base_url` accepts `https`).

## Registry write protection

Contract §4.5 requires that no scheduler-dispatched container can write a capability-evidence
record. #557 supplied the mechanism; your part is below.

- The registry directory lives on the Docker host, under your control. It is mounted into the
  scheduler read-only (`deploy/docker-compose.yml`:
  `${FACTORY_MODEL_REGISTRY_DIR:-./model-registry.empty}` at
  `/etc/dark-factory/model-registry:ro`), and never into a run container.
- Run containers see only the staged copy on the shared state volume. They trust it only against
  the SHA-256 of the read-only source, which the scheduler passes per dispatch (#593). A run
  container that edits the staged copy fails its own preflight; it cannot change the source.
- Keep the host directory writable only by your operator account. Never point
  `FACTORY_MODEL_REGISTRY_DIR` at a directory inside a run's workspace or on the shared state
  volume.
- This runbook creates no `capability-evidence/<endpoint_id>.yaml`. Only an operator acting on
  #562's certification output writes one. Until then the entry's evidence state is `unknown`, which
  is expected.

## Timeout bounds

Starting values for the probed rig. They are recommendations to re-measure on your own hardware,
not certified numbers.

| Field | Value | Why |
|---|---|---|
| `connect_seconds` | 10 | LAN-local; generous |
| `time_to_first_token_seconds` | 30 | covers the measured 5.4s cold and 0.02s warm first token, plus the unmeasured 10-20s estimate after a full reboot, with headroom |
| `idle_seconds` | 60 | the existing fixture default |
| `total_seconds` | 1800 | the existing fixture default; no long multi-turn data exists yet |

The probed rig evicts an idle model after 5 minutes (Ollama's `OLLAMA_KEEP_ALIVE`). That window
decides how often a request pays the cold start. It is not one of the request timeout fields.

Two caveats:

- `time_to_first_token_seconds` bounds the wait for the first response *body byte*. A
  non-streaming request whose whole generation takes longer than the bound therefore times out as
  `timeout_time_to_first_token`, even on a warm model.
- A cold start can take longer than measured when another model on the machine has to be evicted
  first.

## Resource budget

Measure this on your own hardware. A table of numbers taken from a model card is not a budget.

What the probe measured (#562 pre-certification note, 2026-11-25; not certification): about 160
output tokens/s with the rig idle and a single request; a 28.6k-token prompt ingested in 4.2s; time
to first token 5.4s cold (model files in the OS page cache) and 0.02s warm; idle eviction after 5
minutes; 131072 tokens of configured context.

Not measured: memory footprint and behaviour under concurrency. Certification (#562) owns certified
figures.

The budget has four components:

1. **Weights:** the resident size of the model once loaded.
2. **KV/context cache:** at the *registered* context length, not the model's maximum.
3. **Concurrency:** one KV/context allocation per request served in parallel.
4. **Runtime and OS headroom:** the runtime process, the OS and anything else on the machine.

Procedure:

1. Load the model at the exact context length you are registering, with one request.
2. Read the resident size and the GPU/CPU split with `ollama ps`. Record them.
3. Repeat at the concurrency you actually plan (Ollama's `OLLAMA_NUM_PARALLEL`). Record the
   resident size again, and the first-token time and throughput while all requests run.
4. Record the model store's size and its filesystem's free space (see "Persistent model storage").

Worksheet (fill in from your own measurements):

| Item | Measured value | How measured |
|---|---|---|
| Runtime version | | `ollama --version` |
| Registered context length | | `ollama show <name>` |
| Resident size, 1 request | | `ollama ps` |
| GPU/CPU split, 1 request | | `ollama ps` |
| Planned concurrency | 1 | |
| Resident size at planned concurrency | | `ollama ps` |
| Cold time to first token | | readiness step 3 |
| Warm time to first token | | readiness step 3 |
| Model store size | | the store's filesystem |
| Free space on the store's filesystem | | the store's filesystem |
| Headroom left (memory) | | resident size against total memory |

For planning only, the KV cache per parallel request is at most about
`2 × layers × kv_heads × head_dim × context_tokens × bytes_per_element`. That is a rough upper
bound, not a substitute for measuring.

**Concurrency defaults to 1** until you measure otherwise. Only single-request behaviour has been
observed, and the contract's v1 local role is the actor alone (`LOCAL_ROLE = "actor"`), with no
parallel local checker.

## Readiness

Three steps, using code already on `main`. None of them selects the endpoint on a running
scheduler.

1. **The entry parses and the secret name is set.** With the registry directory mounted (see
   "Enable / disable / rollback"), run in the scheduler container:

   ```
   docker exec <instance>-scheduler \
     python3 /opt/dark-factory/scripts/factory_core/providers/cli.py model-endpoints show --from-source
   ```

   It prints the redacted effective configuration: the entry, its `base_url_scope`, and the
   `secret_reference` as its name and `present: true|false`, never its value.

2. **The preflight boundary.** In a one-off container, never the running scheduler (the same form
   as the offline check in `docs/configuration.md`):

   ```
   docker run --rm --env-file <instance.env> \
     -e FACTORY_MODEL_ENDPOINT=lan-ollama-qwen3-coder \
     -v <registry dir>:/etc/dark-factory/model-registry:ro \
     --entrypoint python3 <factory image> \
     /opt/dark-factory/scripts/factory_core/providers/cli.py preflight --side scheduler
   ```

   Expected: exit 1, with exactly one line about the model endpoint: the `_NO_LOCAL_DISPATCH`
   message, `endpoint 'lan-ollama-qwen3-coder' is local, but no local-eligible dispatch exists
   yet`. That is the correct outcome until #587. It shows that the registry is valid and the id
   resolves. It dials nothing, so it says nothing about reachability.

3. **Reachability from the factory's network.** From a throwaway container on
   `<instance>_dark-factory-net`, the network the run and proxy containers share. The factory
   image has `curl`; run it with no env file, so the container holds no credential:

   ```
   docker run --rm --network <instance>_dark-factory-net --entrypoint curl <factory image> \
     -sS -w '\nHTTP %{http_code}; first byte after %{time_starttransfer}s\n' \
     -H 'content-type: application/json' -H 'anthropic-version: 2023-06-01' \
     -d '{"model":"qwen3-coder-128k","max_tokens":16,"messages":[{"role":"user","content":"Reply with OK."}]}' \
     http://192.0.2.10:11434/v1/messages
   ```

   - Non-streaming, as above: expect `HTTP 200` and a response whose `model` is the registered
     `served_name`.
   - Streaming: add `-N` and `"stream":true` to the body. Expect the stream to end with an
     `event: message_stop` frame.
   - Cold: run both again after the endpoint has been idle for longer than its eviction window (5
     minutes on the probed rig). The `first byte after` time must sit inside
     `time_to_first_token_seconds`, with headroom.

   These are operator checks against real hardware. They are not certification (#562).

Not available before #587, so they are not steps here: the run-side `preflight`, and
`model-endpoints show` without `--from-source`. Both need the per-dispatch
`FACTORY_MODEL_REGISTRY_SHA256` and fail without it when run by hand.

## Failure modes and bounded queue/timeout behaviour

A local destination is `local-pinned` under the contract: a failed request is never retried and
never falls back to the cloud. The adapter does not queue. A request waiting in the runtime's own
queue is bounded only by `time_to_first_token_seconds` and `total_seconds`. For real admission
control, bound concurrency and queue depth in the runtime (Ollama's `OLLAMA_NUM_PARALLEL` and
`OLLAMA_MAX_QUEUE`) or the OS. The recommended concurrency is 1.

Each row cites the test that pins the behaviour: `proxy` is `tests/test_factory_model_proxy.py`,
`adapter` is `tests/test_provider_model_adapter.py`.

| Scenario | What the factory sees | Failure class | Pinned by |
|---|---|---|---|
| Endpoint unreachable (machine off, service stopped, wrong address) | `504`, nothing sent anywhere else | `endpoint_unreachable` | proxy `test_local_endpoint_unreachable_fails_closed_without_retrying_elsewhere` |
| Connect hangs (a firewall drops packets silently) | `504` after `connect_seconds` | `timeout_connect` | adapter `test_classify_failure` |
| Restart: runtime up but model still loading (`502`/`503`/`504`) | the status, forwarded unchanged | `endpoint_unreachable` | proxy `test_local_not_ready_upstream_is_forwarded_and_classified` |
| Restart or crash mid-stream | truncated stream, never a clean end | `endpoint_unreachable` | proxy `test_local_upstream_disconnect_mid_stream_is_aborted` |
| Cold start inside `time_to_first_token_seconds` | normal response | none | proxy `test_lan_profile_cold_start_inside_ttft_succeeds` |
| Cold start past `time_to_first_token_seconds` | `504` | `timeout_time_to_first_token` | proxy `test_lan_profile_cold_start_past_ttft_is_its_own_class`, `test_local_first_byte_past_ttft_is_its_own_class` |
| Overload: runtime refuses with `503` (queue full) | the `503`, forwarded; not retried, no cloud call | `endpoint_unreachable` | proxy `test_lan_profile_queue_full_503_is_not_retried_or_sent_to_cloud` |
| Overload: generation stalls between chunks | truncated stream | `timeout_idle` | proxy `test_local_stall_after_the_first_chunk_hits_the_idle_bound` |
| Overload: request runs past `total_seconds` | `504`, or a truncated stream once streaming has started | `timeout_total` | adapter `test_classify_failure` |
| OOM: runtime process killed mid-request | truncated stream or `504` | `endpoint_unreachable` | proxy `test_local_upstream_disconnect_mid_stream_is_aborted`, `test_local_endpoint_unreachable_fails_closed_without_retrying_elsewhere` |
| OOM or disk pressure reported as HTTP `500` (a load or allocation failure) | the `500`, forwarded unchanged | **none** (known gap) | adapter `test_not_ready_statuses_are_endpoint_unreachable` |

**Known gap: HTTP 500 has no failure class.** `status_failure_class` in `model_adapter.py`
classifies only `502`/`503`/`504`. A `500` from the runtime, which is how an out-of-memory or
full-disk load failure is likely to surface, is forwarded with no failure class. It is still never
retried and never sent to the cloud. This runbook does not fix it: the classification is #558's to
extend and #238's taxonomy to rename. It is a candidate spillover ticket.

## Enable / disable / rollback

No new mechanism: every command is one `docs/configuration.md` already documents.

**Enable** means making the registry available for validation, nothing more. Set
`FACTORY_MODEL_REGISTRY_DIR` to the host path of the instance's registry directory. It is compose
interpolation env, like `PROJECT_DIR`: put it in the `--env-file` file or the shell. Then recreate
the scheduler:

```
docker compose -f deploy/docker-compose.yml up -d
```

Use the same command you started this instance with. An instance started with
`--env-file deploy/instances/<instance>/instance.env` must be recreated with that same
`--env-file`; without it compose acts on the default `dark-factory` project, not your instance.

With `FACTORY_MODEL_ENDPOINT` unset, nothing reads the mount, and dispatch argv and env are
unchanged. Then run the three readiness steps.

> **Warning:** never set `FACTORY_MODEL_ENDPOINT` to a local id in a running instance's
> `instance.env` before #587 ships. `scheduler.sh` runs `preflight --side scheduler` at boot under
> `set -e`, and a local selection always fails it (`_NO_LOCAL_DISPATCH`). The scheduler service is
> `restart: unless-stopped`, so the scheduler restart-loops and every dispatch stops, cloud
> dispatch included.

**Disable / rollback.** Remove `FACTORY_MODEL_REGISTRY_DIR` from the interpolation env and recreate
the scheduler with the same command, including its `--env-file`. The mount falls back to `deploy/model-registry.empty/`.
Remove the dummy secret variable from `instance.env`. There is no staged copy to clean up, because
nothing was ever staged.

**Recovery, if a local id was selected by mistake.** Remove `FACTORY_MODEL_ENDPOINT` from
`instance.env` and recreate the scheduler with the same command, including its `--env-file`. Cloud-only behaviour is
restored.

Live activation is not part of this runbook. It needs #562's certification and #587's first
local-eligible dispatch.

## No-purchase test path

The whole profile can be checked without any hardware. The `lan-ollama-qwen3-coder` entry in
`tests/fixtures/model_registry/model-endpoints.yaml` is the example above. These tests cover it:

- `tests/test_provider_model_endpoint.py`:
  - `test_lan_ollama_profile_validates_but_activates_no_routing`: the entry validates, and both
    the scheduler and the run-side preflight still fail on `_NO_LOCAL_DISPATCH`.
  - `test_unselected_lan_profile_leaves_the_cloud_default_path_unchanged`: with the entry staged
    and `FACTORY_MODEL_ENDPOINT` unset, both preflight sides pass and neither the registry nor the
    adapter module is loaded.
- `tests/test_factory_model_proxy.py`: the `test_lan_profile_*` tests run this profile's timeouts,
  divided by 20, against an in-process fake upstream (no endpoint, no network).

```
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py tests/test_factory_model_proxy.py -v -k "lan_ollama or lan_profile"
```

## Caveats

- Nothing here is certification. Every number in this runbook is a starting point; #562 owns
  certified figures.
- Selecting a local endpoint is refused everywhere until #587 (`_NO_LOCAL_DISPATCH`).
- The run container's cloud-credential guard does not yet cover Bedrock/Vertex switches or AWS/GCP
  alternate credentials (#594). That is a precondition of #587, not part of this runbook.
- The probed rig ignores a forced `tool_choice` and has no `/v1/messages/count_tokens`. The adapter
  refuses both before any outbound call (see `docs/dark-factory-model-proxy-rollout.md`).
- HTTP `500` has no failure class (see "Failure modes").
- Transport is plain http and the API is unauthenticated: the network boundary above is the only
  protection.
````

- [ ] **Step 2: Check the runbook mechanically.** These checks cover the spec's "Verified by
  review" items that a script can check; the rest are reviewed in the PR. Save this script
  outside the repository, as `/tmp/check_runbook.py`, and do not commit it:

  ```python
  import ipaddress, re, sys
  from pathlib import Path
  import yaml
  sys.path.insert(0, "scripts")
  from factory_core.providers import model_endpoint as me

  doc = Path("docs/local-inference-deployment-runbook.md").read_text(encoding="utf-8")
  # 1. The worked example is the fixture entry, and both validate.
  block = doc.split("```yaml\n", 1)[1].split("```", 1)[0]
  example = yaml.safe_load(block)["endpoints"][0]
  fixture = {e["id"]: e for e in me.load_registry(Path("tests/fixtures/model_registry") / me.REGISTRY_FILE)}
  assert example == fixture["lan-ollama-qwen3-coder"], "runbook example differs from the fixture entry"
  # 2. Every IPv4 address is in the RFC 5737 documentation range.
  net = ipaddress.ip_network("192.0.2.0/24")
  addrs = re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", doc)
  bad = [a for a in addrs if ipaddress.ip_address(a) not in net]
  assert not bad, f"addresses outside 192.0.2.0/24: {bad}"
  # 3. Required statements and sections.
  for needle in ("## Secret location", "## Pinning identity", "## Distinguishing modes",
                 "## Connectivity: same host or LAN", "## Persistent model storage",
                 "## Service isolation and network boundary", "## Registry write protection",
                 "## Timeout bounds", "## Resource budget", "## Readiness",
                 "## Failure modes and bounded queue/timeout behaviour",
                 "## Enable / disable / rollback", "## No-purchase test path",
                 "instance.env", "`.archon/.env`", "any tracked file", "not authentication",
                 "`localhost` is the container itself", "host.docker.internal", "extra_hosts",
                 "`OLLAMA_MODELS`", "no Docker socket", "plain http", "TLS",
                 "api/tags", "ollama --version", "ollama show", "ollama ps", "ollama list",
                 "Provenance and licence", "Context length is part of the identity",
                 "not a substitute for measuring", "Concurrency defaults to 1", "Not measured",
                 "_NO_LOCAL_DISPATCH", "restart-loops", "model-registry.empty",
                 "--side scheduler", "message_stop", "HTTP 500 has no failure class",
                 "docs/hybrid-execution-contract.md", "docs/configuration.md",
                 "docs/dark-factory-model-proxy-rollout.md"):
      assert needle in doc, f"missing: {needle}"
  # 4. Every cited test exists.
  tests = Path("tests/test_factory_model_proxy.py").read_text() + Path("tests/test_provider_model_adapter.py").read_text() \
      + Path("tests/test_provider_model_endpoint.py").read_text()
  for name in set(re.findall(r"`(test_[a-z0-9_]+)`", doc)):
      assert f"def {name}(" in tests, f"cited test not found: {name}"
  # 5. No capability-evidence example.
  assert "capability_evidence:" not in doc
  print("runbook checks: OK")
  ```

  Run it:

  ```bash
  PYTHONPATH=scripts python /tmp/check_runbook.py
  ```

  Expected: `runbook checks: OK`. If it fails, fix the runbook, never the fixture; the fixture is
  pinned by Task 1's tests. Then run the runbook's own no-purchase command:

  ```bash
  PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py tests/test_factory_model_proxy.py -v -k "lan_ollama or lan_profile" -p no:cacheprovider
  ```

  Expected: `5 passed`. That is `test_lan_ollama_profile_validates_but_activates_no_routing`, the
  three `test_lan_profile_*` tests, and
  `test_unselected_lan_profile_leaves_the_cloud_default_path_unchanged`, which `-k` selects
  through `lan_profile`.

- [ ] **Step 3: Review the spec's "Verified by review" items by eye.** Check each against the
  file:
  - Secret location names the scheduler's `instance.env` as the only place the value lives, names
    the three places it must not go, and contains no secret value.
  - Pinning identity has all five items (served name, digest, runtime/template/quantisation,
    context length, provenance/licence), each with the command that produces it.
  - Connectivity states that container `localhost` is not the host and gives a same-host route
    that needs no compose change.
  - Persistent model storage names no host path, and the worksheet has rows for store size and
    free space.
  - Service isolation has all five statements, and nothing calls a placeholder key
    authentication.
  - Registry write protection has all four statements, and there is no capability-evidence
    example.

- [ ] **Step 4: Commit.**

  ```bash
  git add docs/local-inference-deployment-runbook.md
  git commit -m "docs(#560): operator runbook for an opt-in local inference endpoint

  Registering a LAN/same-host endpoint against the #557 registry, secret
  location, identity pinning, connectivity, storage, isolation, registry
  write protection, timeout bounds, a measure-it-yourself resource
  worksheet, readiness, a failure-mode table citing the pinning tests, and
  enable/disable/rollback. Routes nothing: _NO_LOCAL_DISPATCH stays until
  #587. Known gap documented, not fixed: HTTP 500 has no failure class.

  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  ```

---

## Task 5: Cross-reference from `docs/configuration.md`

**Files:**
- Modify: `docs/configuration.md`

- [ ] **Step 1: Add one line to the intro of "### Model endpoint registry (#557)".** Replace

  ```markdown
  Optional. With `FACTORY_MODEL_ENDPOINT` unset (the default) nothing below is read, staged or
  checked, and dispatch argv and env are unchanged. Design:
  `docs/archive/2026-11-23-endpoint-registry-secrets-preflight-design.md`.
  ```

  with

  ```markdown
  Optional. With `FACTORY_MODEL_ENDPOINT` unset (the default) nothing below is read, staged or
  checked, and dispatch argv and env are unchanged. Design:
  `docs/archive/2026-11-23-endpoint-registry-secrets-preflight-design.md`.
  Operator runbook for registering a candidate local endpoint: `docs/local-inference-deployment-runbook.md` (#560).
  ```

- [ ] **Step 2: Verify.**

  ```bash
  grep -c "docs/local-inference-deployment-runbook.md" docs/configuration.md
  git diff --stat -- docs/configuration.md
  ```

  Expected: `1`, then `1 file changed, 1 insertion(+)`.

- [ ] **Step 3: Commit.**

  ```bash
  git add docs/configuration.md
  git commit -m "docs(#560): link the local inference runbook from the registry section

  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
  ```

---

## Task 6: Final verification

**Files:** none modified.

- [ ] **Step 1: Run the full Python suite.**

  ```bash
  PYTHONPATH=scripts python -m pytest tests/ -q -p no:cacheprovider
  ```

  Expected: the baseline count plus 5, `0 failed`: the 5 new tests on top of whatever
  `origin/main` passes when the branch is cut.

- [ ] **Step 2: Run the CI shell tests that touch the registry or the provider env, and CI's
  doc-reference and workflow DAG checks.** None of
  their inputs changed; this confirms the default path end to end.

  ```bash
  env -i PATH="$PATH" HOME="$HOME" bash tests/test_provider_env_flags.sh
  bash tests/test_scheduler.sh 2>&1 | tail -1
  python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
  python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
  python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
  ```

  Expected: `PASS: test_provider_env_flags.sh` and `Results: <n> passed, 0 failed`, then the
  three CI checks pass: `Docs reference check passed for 4 doc(s).`, `DAG trigger_rule check
  passed for 1 workflow file(s).` and `when: expression lint passed for 1 workflow file(s).` The clean
  `env -i` matters: on a host or container where `JIRA_*` variables are already exported, the
  first assertion of `test_provider_env_flags.sh` fails before #560's change is involved. That
  was observed in the plan-phase container.

- [ ] **Step 3: Confirm the change set is exactly the plan's.**

  ```bash
  git diff --name-only origin/main...HEAD
  git diff --name-only origin/main...HEAD -- deploy run-compose.yml .github scripts scheduler.sh entrypoint.sh config .factory
  ```

  Expected: the first command lists only the six files in the File Structure table, plus the
  spec, plan and `.archon/memory/` files committed earlier on the refine branch if this branch
  carries them. The second command prints nothing.

- [ ] **Step 4: Confirm nothing local was activated.**

  ```bash
  grep -n "_NO_LOCAL_DISPATCH = (" scripts/factory_core/providers/model_endpoint.py
  git diff origin/main...HEAD -- scripts/factory_core/providers/model_endpoint.py | wc -l
  ```

  Expected: one match, and `0`.

## Out of scope (do not do)

- Lifting `_NO_LOCAL_DISPATCH`, or wiring endpoint variables into `factory-model-proxy` (#587).
- Classifying HTTP 500 in `model_adapter.py` (#558/#238). The runbook documents it as a known
  gap.
- Strengthening `test_default_path_never_loads_the_registry_module` (see note 2). It is a
  candidate spillover ticket.
- Any capability-evidence record or fixture (#561/#562), certification, economics, canary or
  end-gate work (#561–#564, #585–#587).
- Any edit under `deploy/**`, `run-compose.yml` or `.github/workflows/*`.
