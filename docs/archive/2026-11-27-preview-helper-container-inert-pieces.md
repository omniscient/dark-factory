# The `preview-helper` Container — Service, Entry Script, Endpoint Verb and Helper Module (inert) — Implementation Plan

**Issue:** #642

**Goal:** Deliver, with hermetic tests, four inert pieces of the `classify-preview-helper`
lane and one build-time check: the `preview-helper` compose service and its internal network,
the container's entry script `scripts/preview_helper_entry.sh`, the endpoint-only preflight
verb `model-endpoints endpoint-preflight --intent`, the module
`scripts/factory_core/preview_helper.py`, and a `docker-build` CI step that checks the pinned
CLI's request shape through the real helper proxy. Nothing dispatches any of it: no
`scheduler.sh`, `scripts/provider_env.sh`, `workflows/**`, `config/**`, `entrypoint.sh` or
`Dockerfile` change. #587 adds the scheduler lane on top.

**Architecture:** The spec's listings are the implementation. They are verbatim prototype
files that were run hermetically on the #633/#634 end state. The module, the entry script and
the verb are taken from the spec by a mechanical extraction step and checked against a
digest, so nothing is hand-transcribed. Then the few edits this plan names are applied
(Deviations). The compose block is small and is written out literally below. Tests come
first in every task.

**Tech Stack:** Python 3.12 (pytest, PyYAML, aiohttp), bash, `jq`, GNU `timeout`, Docker
Compose (`docker compose config` only, never `up`/`run`), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-27-preview-helper-container-inert-pieces-design.md`
(the operator's reviewed, prototype-backed design, commit `259d379`).

**Depends on: #633 and #634.** This plan is written against their merged interfaces:
`LOCAL_ELIGIBLE_INTENTS`, `preflight(..., intent=)`, `LOCAL_CREDENTIAL_ENV_SIGNALS`, the
evidence minimum, `entrypoint.sh`'s "not available" exit for `Classify preview (helper) #N`
(#633); `HELPER_PROXY_BASE_URL`, the helper-intent run-side rules, `model_proxy.main(argv)`
with `--intent`, the `preview-helper-proxy` service, the `test_run_compose.sh` 3f block and the
`test_provider_env_passthrough.py` exemption (#634). Task 0 checks they exist. If any is
missing, stop: do not re-implement #633 or #634 here.

**Gate posture.** Security-labelled and inert. It touches no `gate_*` script, breaker key,
budget, WIP or ceiling logic, or tool allow/deny list. It does not touch `deploy/**`,
`.github/workflows/publish.yml`, `run_record.py`, `model_proxy.py`, `model_adapter.py`,
`model_endpoint.py` or `scripts/factory_core/capability_suite/**`.
`PINNED_FIXTURE_SUITE_VERSION` therefore does not move. Expected blast-radius verdict (run
in Task 7, already measured on this file list): `STATUS: HUMAN_REQUIRED`, one trigger,
`.github/workflows/ci.yml` (`migration-seed`). Stopping at that hold for signed human approval
is the intended outcome, not a failure.

**How this plan was checked.** The plan's own blocks were extracted from this file and
applied by script, never retyped, to a scratch worktree. Its base was this branch, plus
`origin/feat/issue-633-…` merged, plus #634's plan Task 1 code (`model_endpoint.py`/`cli.py`)
and Task 4 (compose block, 3f/4b–4d checks, env-passthrough rewrite), both applied from #634's
plan text. Results:
- Task 1: 17 failed first, then `tests/test_provider_cli.py` 53 passed.
- Task 2: on the verbatim listing, exactly the 5 D1 cases failed. After D1, 144 passed.
- Task 3: `test_preview_helper_entry.sh` all `PASS:` in about 35 s, the two hung-CLI cases
  included.
- Task 4: the stated first failure, then every `PASS:` line on Docker Compose v5.8.0. Five
  mutations each failed 3g: `internal: false`, an added `GH_TOKEN` key, a second network, a
  state-volume-root mount, `env_file`.
- Task 5: `test_ci_covers_shell_tests.py` and the `docker-build` CI-shape test pass, and the
  embedded Python compiles.
- Task 6: `docs_refs_check.py` passes.
- The whole Python suite: 4076 passed. `test_provider_model_endpoint.py` was deselected,
  because #634's Step 1.2 test edits were not applied there.

Not checked in the scratch copy:
- The Tier 2 run itself (Task 5). This sandbox's Docker socket proxy refuses daemon calls,
  so the `docker-build` CI job is its first run.
- The `docs/dark-factory-model-proxy-rollout.md` edit (Step 6.2) against #634's Task 5 doc
  text, because #634's Task 5 was not applied. Step 6.2 matches the sentence, not its
  wrapping, and Step 0.1 greps only the sentence's last clause for that reason.

## Deviations from the spec (recorded for the reviewer)

1. **D1 — `record_outcome` survives forged ledger rows (module edit, Task 2 Step 2.6).**
   Spec requirement 4 says no function in the module raises on hostile input. The unchanged
   shared code it calls does raise on hostile rows. `run_record._read_ledger_rows` calls
   `row.get` on every parsed line, so a ledger line that is a JSON array, string or number
   raises `AttributeError`. `run_record._compute_model_dispatch` builds sets of
   `failure_class` and `endpoint_id`, so an unhashable value in a row with the helper's run
   id raises `TypeError`. Any gated run container can append such a line, because the ledger
   is on the shared volume. The spec's listing would then crash `record-outcome` and write
   no row. The edit wraps exactly those two calls. On any exception the ledger counts as
   unavailable: no rows, `available=False`, so the outcome is `abstained` /
   `ledger_unavailable`. `run_record.py` is still not edited. New test:
   `test_forged_ledger_rows_never_raise`.
2. **D2 — the request-shape CI step names its script so the shell-test guard sees it (Task 5).**
   `tests/test_ci_covers_shell_tests.py` globs every `tests/*.sh`, not only `test_*.sh`. It
   counts a file as wired only when a live `ci.yml` step's `run:` text contains
   `bash tests/<file>.sh`. The spec measured "existing tests this change breaks" with the four
   product files only, so it did not see this. With the spec's step
   (`--entrypoint bash dark-factory:pr /tests/tier2_preview_helper_cli.sh`), the new
   `tests/tier2_preview_helper_cli.sh` would be "unaccounted" and fail
   `test_every_shell_test_is_wired_or_exempt`. So the step mounts `tests/` at `/work/tests`
   and runs `bash -c 'bash tests/tier2_preview_helper_cli.sh'` with working directory
   `/work`. The script actually runs, the guard recognises it, and no exemption is added. Every
   other part of the step (`--network none`, `--add-host`, `--tmpfs`, the image, the 5-minute
   bound) is the spec's.
3. **D3 — the entry script's header comment names this ticket's spec (Task 3 Step 3.4).** The
   listing's comment cites `docs/superpowers/specs/2026-11-27-first-helper-dispatch-preview-classifier-design.md`
   (#587's spec, not on `main`). It becomes this ticket's spec path. Comment only.
4. **D4 — the prompt cap is the spec's 100,000 bytes.** The refine run's issue comment (Q&A)
   proposed 64 KiB. The replacing spec pins `MAX_PROMPT_BYTES = 100_000` (one argv string; the
   kernel's limit is 131,072). The issue says "the stated byte cap", and the spec states this
   one. Tested at 100,000 (accepted) and 100,001 (refused).
5. **D5 — tests beyond the spec's Test Plan (all named tests are kept).**
   `test_validate_pending_accepts_exactly_the_six_fields`, `test_build_prompt_caps_the_rendered_prompt`,
   `test_build_prompt_missing_or_invalid_input`, `test_result_document_and_read_result_round_trip`,
   `test_read_result_rejects_malformed`, `test_write_inflight_round_trips`,
   `test_next_candidate_picks_the_oldest_valid_unstamped_file`,
   `test_next_candidate_exits_2_when_the_copy_cannot_be_written`,
   `test_forged_ledger_rows_never_raise` (D1), the forged `schema_version` in
   `test_ledger_free_text_is_not_copied` (D6), `test_endpoint_preflight_refuses_without_a_selected_endpoint`,
   the entry-script argv/prompt equality check, and a `--profile preview-helper-proxy`
   render-difference check in `test_run_compose.sh`. The spec's "path separators" case for
   `test_validate_pending` is covered by `test_pending_name_pattern_refuses_path_separators`.
   `validate_pending` reads only the file's name, and a name inside a directory can never hold
   `/`. `test_validate_pending` keeps every hostile name that can exist on disk, the backslash
   included.
6. **D6 — `constrained_dispatch` writes the two schema versions as constants (module edit,
   Task 2 Step 2.6; found by the architect review).** `run_record._compute_model_dispatch`
   builds the request as `{"schema_version": MODEL_DISPATCH_REQUEST_VERSION, **decision}`,
   where `decision` is the first ledger row's `payload_decision`. So a forged row's own
   `"schema_version"` key overrides the constant. The listing's `constrained_dispatch` copies
   `req.get("schema_version")` unchecked, so arbitrary JSON from the shared volume reached
   `runs.jsonl`, which breaks spec decision 10. The edit writes
   `run_record.MODEL_DISPATCH_REQUEST_VERSION` and `run_record.MODEL_DISPATCH_RESULT_VERSION`
   instead. The result's version is not ledger-derived today; it is pinned too, so neither
   version can drift. `test_ledger_free_text_is_not_copied` gains a forged `schema_version`.

## Review amendments (2026-11-27, from executing this plan end to end)

The plan was executed task by task on the executed end state of #634's plan (`main` at
`a8cba16` plus #633 and #634, review amendments included) with this branch's spec and plan
added, in the factory image with no network. Task 0 found every anchor exactly once. Every
stated result was observed: 17 failed then 53 passed (Task 1); both extraction digests
(`52df292e85823334`, `662b03eaa1f0ebf2`); exactly the six D1/D6 failures, then 144 passed
(Task 2); the stated first failures of Tasks 3 and 4 and then every `PASS:` line; the guard
and CI-shape tests of Task 5; the docs checks of Task 6. The request-shape check that this
plan could not run (Task 5) was run: `tests/tier2_preview_helper_cli.sh` passes in the
factory image with `--network none`, the pinned CLI 2.1.282, the suite's mock and this
tree's `scripts/` mounted as the baked copy — one request, `tools == []`, no
`output_config` or `output_format`, the served name, the registry secret in `x-api-key`.
The product files equal the prototype's except for comments, the verb's position in
`cli.py`, and deviations D1 and D6. Two amendments, both to tests:

1. **Step 4.1: the 3e text check ends the `preview-helper` block at the next service.** As
   written the block ran to the next top-level key, so a service added after
   `preview-helper` could carry the string `model-registry` outside a mount and pass. The
   spec says the string occurs nowhere outside that service's block.
2. **Step 3.1: the zero-call assertion covers every failure class.** The acceptance
   criterion asks that a successful run and each failure class record zero `git`, `gh` and
   `docker` calls. The test asserted it for success, `schema_invalid` and
   `preflight_refused` only; four lines add `model_call_failed`, `missing_input`,
   `input_too_large` and `local_timeout_total`.

---

## File Structure

| File | Change | Task |
|---|---|---|
| `scripts/factory_core/providers/cli.py` | `_model_endpoints_endpoint_preflight()` and its `endpoint-preflight` sub-parser | 1 |
| `tests/test_provider_cli.py` | the verb's tests (appended block) | 1 |
| `scripts/factory_core/preview_helper.py` | new: the spec's listing plus D1 and D6 | 2 |
| `tests/test_preview_helper.py` | new | 2 |
| `scripts/preview_helper_entry.sh` | new, mode 100755: the spec's listing plus D3 | 3 |
| `tests/test_preview_helper_entry.sh` | new | 3 |
| `.github/workflows/ci.yml` | `- run: bash tests/test_preview_helper_entry.sh` in `tests` (Task 3); one step in `docker-build` (Task 5) | 3, 5 |
| `run-compose.yml` | add `preview-helper` and `preview-helper-net`; add that network to `preview-helper-proxy` | 4 |
| `tests/test_run_compose.sh` | 3e narrowed; #634's 3f network list; new 3g source block; new 4e/4f render blocks | 4 |
| `tests/test_provider_env_passthrough.py` | the `preview-helper-proxy` exemption also covers `preview-helper` | 4 |
| `tests/tier2_preview_helper_cli.sh` | new (needs the built image, so not a `test_*.sh`) | 5 |
| `docs/configuration.md` | the service, its nine keys and two mounts, the verb; nothing dispatches it until #587 | 6 |
| `docs/dark-factory-model-proxy-rollout.md` | `preview-helper-proxy` is on two networks | 6 |

Thirteen files, the same thirteen as the spec's "Files touched" table. Run every command from
the repo root. CI sets `PYTHONPATH=scripts`.

---

## Task 0: Preconditions (#633 and #634 are merged)

**Files:** none (read-only checks)

- [ ] **Step 0.1: Confirm the #633/#634 interface on this branch.**

```bash
PYTHONPATH=scripts python3 -c "
import inspect
from factory_core.providers import model_endpoint as me
from factory_core import model_proxy as mp
assert me.LOCAL_ELIGIBLE_INTENTS == ('classify-preview-helper',)
assert me.HELPER_PROXY_BASE_URL == 'http://preview-helper-proxy:8787'
assert len(me.LOCAL_CREDENTIAL_ENV_SIGNALS) == 13
assert 'intent' in inspect.signature(me.preflight).parameters
assert 'expected_base_url' in inspect.signature(me.load_verified_entry).parameters
assert 'argv' in inspect.signature(mp.main).parameters
print('ok')"
command grep -cxF '  preview-helper-proxy:' run-compose.yml
command grep -cF 'mhost.add_argument("--intent"' scripts/factory_core/providers/cli.py
command grep -cF 'assert svc.get("networks") == ["dark-factory-net"], f"{name} must be on dark-factory-net only"' tests/test_run_compose.sh
command grep -cF "if grep -q 'model-registry' \"\$RC\"; then" tests/test_run_compose.sh
command grep -cF 'echo "PASS: the helper secret is in no uninterpolated or factory-profile render"' tests/test_run_compose.sh
command grep -cF 'if service == "preview-helper-proxy":' tests/test_provider_env_passthrough.py
command grep -cF "#587 adds the helper's internal network" docs/dark-factory-model-proxy-rollout.md
command grep -cF 'E: the two #587 strings are recognized and not available' tests/test_entrypoint_intent_case.sh
command grep -cF '**Back to default cloud:** remove `FACTORY_MODEL_ENDPOINT`' docs/configuration.md
```

  Expected: `ok`, then `1` nine times. Every anchor is a fixed string (`grep -F`), and
  `command` bypasses any shell function named `grep`. If the Python check fails, #633 or #634 has not
  landed. Stop, post that finding on the issue, and do not continue. If only an anchor count
  is `0`, the merged wording drifted. Apply the same change in meaning at the drifted text in
  the step that edits it, and record that in that task's commit message.

- [ ] **Step 0.2: Confirm the baseline is green.**

```bash
PYTHONPATH=scripts python3 -m pytest tests/test_provider_cli.py tests/test_provider_model_endpoint.py tests/test_provider_env_passthrough.py tests/test_ci_covers_shell_tests.py tests/test_factory_model_proxy.py -q
bash tests/test_run_compose.sh && bash tests/test_model_proxy_compose.sh && bash tests/test_entrypoint_intent_case.sh
```

  Expected: all pass. Without Docker the compose tests print `SKIP:` lines for the render
  checks, as today.

---

## Task 1: The endpoint-only preflight verb

**Files:** `tests/test_provider_cli.py`, `scripts/factory_core/providers/cli.py`

- [ ] **Step 1.1: Write the failing tests.** Append this block to the **end** of
  `tests/test_provider_cli.py`. The file already imports `json`, `shutil`, `sys`, `Path` and
  `pytest` and puts `scripts/` on `sys.path`. Add `import os` to its stdlib imports
  (alphabetically, after `import json`).

```python
# --- #642: model-endpoints endpoint-preflight ------------------------------------------------

HELPER_INTENT = "classify-preview-helper"
HELPER_PROXY_URL = "http://preview-helper-proxy:8787"
HELPER_SECRET_NAME = "FACTORY_HELPER_MODEL_SECRET"
HELPER_LOCAL_ENTRY = {
    "id": "local-helper",
    "hosting_location": "local",
    "protocol": "anthropic-messages",
    "accepts_payload_classes": ["local_only"],
    "model_identity": {"served_name": "helper-served-fixture",
                       "digest_or_revision": "sha256:" + "cd" * 32},
    "base_url": "http://192.0.2.10:8080",
    "secret_reference": HELPER_SECRET_NAME,
    "timeouts": {"connect_seconds": 10, "time_to_first_token_seconds": 60,
                 "idle_seconds": 60, "total_seconds": 300},
}
HELPER_CLOUD_ENTRY = {
    "id": "cloud-a",
    "hosting_location": "cloud",
    "protocol": "anthropic-messages",
    "accepts_payload_classes": ["cloud_ok"],
    "model_identity": {"served_name": "claude-opus-5-5", "digest_or_revision": "claude-opus-5-5"},
    "secret_reference": "ANTHROPIC_API_KEY",
}
# Everything a helper container must not hold, plus the selection variables each test sets.
_HELPER_SCRUBBED = ("ANTHROPIC_BASE_URL", HELPER_SECRET_NAME, "GH_TOKEN", "GITHUB_TOKEN",
                    "GITLAB_TOKEN", "GITLAB_BASE_URL", "JIRA_TOKEN", "JIRA_BASE_URL",
                    "FACTORY_TRACKER", "FACTORY_CODEHOST", "FACTORY_MODEL_PROVIDER",
                    "FACTORY_MODEL_ENDPOINT", "FACTORY_MODEL_REGISTRY_SHA256",
                    "FACTORY_MODEL_REGISTRY_PATH")


def _helper_evidence(me, entry):
    """A capability-evidence record that resolves `tested` for entry under a 2.1.282 CLI."""
    return {
        "schema_version": "1.0",
        "endpoint_id": entry["id"],
        "model_identity": dict(entry["model_identity"], quantisation="q4_k_m"),
        "runtime_stack": {"name": "rt", "version": "1.0.0", "chat_template": "t",
                          "context_settings": {}},
        "harness_version": "2.1.282",
        "fixture_suite_version": me.PINNED_FIXTURE_SUITE_VERSION,
        "results": {dim: "pass" for dim in me.ACTOR_REQUIRED_DIMENSIONS},
        "qualified_roles": ["actor"],
        "certified_by": "operator",
        "certified_at": "2026-11-01T00:00:00+00:00",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "invalidated_by": ["model_digest", "harness_version", "fixture_suite_version",
                           "registry_config"],
        "registry_config": me.registry_config_sha256(entry),
    }


@pytest.fixture
def helper_registry(tmp_path, monkeypatch):
    """A staged registry holding one `tested` local entry, and the helper container's
    environment: the proxy's address, the served name on every alias, no secret, and no
    tracker, code-host or cloud variable. Returns stage(endpoints=..., evidence=...)."""
    import yaml
    from factory_core.providers import model_endpoint as me
    for name in (*me.CLOUD_CREDENTIALS, *me.LOCAL_CREDENTIAL_ENV_SIGNALS, *me.TIER_ALIASES,
                 *_HELPER_SCRUBBED):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(me, "observed_harness_version", lambda *a, **k: "2.1.282")

    def stage(endpoints=(HELPER_LOCAL_ENTRY,), evidence="tested"):
        root = tmp_path / "staged"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir()
        (root / me.REGISTRY_FILE).write_text(
            yaml.safe_dump({"schema_version": "1.0", "endpoints": list(endpoints)},
                           sort_keys=False), encoding="utf-8")
        if evidence != "absent":
            record = _helper_evidence(me, HELPER_LOCAL_ENTRY)
            if evidence == "declared":
                del record["certified_by"]
            elif evidence == "stale":
                record["expires_at"] = "2020-01-01T00:00:00+00:00"
            (root / me.EVIDENCE_DIR).mkdir()
            (root / me.EVIDENCE_DIR / "local-helper.yaml").write_text(
                yaml.safe_dump({"capability_evidence": record}), encoding="utf-8")
        monkeypatch.setenv("FACTORY_MODEL_REGISTRY_PATH", str(root))
        monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", me.tree_sha256(root))
        return root

    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "local-helper")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", HELPER_PROXY_URL)
    for alias in me.TIER_ALIASES:
        monkeypatch.setenv(alias, HELPER_LOCAL_ENTRY["model_identity"]["served_name"])
    stage()
    return stage


def _cli(monkeypatch, capsys, *argv):
    """Run `cli.py <argv>`; returns (exit code, stdout, stderr)."""
    import factory_core.providers.cli as cli_mod
    monkeypatch.setattr(sys, "argv", ["cli.py", *argv])
    code = 0
    try:
        cli_mod.main()
    except SystemExit as e:
        code = e.code
    out = capsys.readouterr()
    return code, out.out, out.err


def _break_rule(monkeypatch, stage, rule):
    from factory_core.providers import model_endpoint as me
    if rule == "wrong_digest":
        monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", "0" * 64)
    elif rule == "entry_base_url":
        monkeypatch.setenv("ANTHROPIC_BASE_URL", HELPER_LOCAL_ENTRY["base_url"])
    elif rule == "local_secret_present":
        monkeypatch.setenv(HELPER_SECRET_NAME, "sk-sentinel-helper")
    elif rule == "cloud_credential":
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-sentinel-cloud")
    elif rule == "guard_signal":
        monkeypatch.setenv("AWS_PROFILE", "fixture")
    elif rule == "alias_differs":
        monkeypatch.setenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "other-model")
    elif rule == "evidence_unknown":
        monkeypatch.setattr(me, "observed_harness_version", lambda *a, **k: None)
    elif rule.startswith("evidence_"):
        stage(evidence=rule.split("_", 1)[1])
    elif rule == "unknown_endpoint":
        monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "not-registered")


ENDPOINT_RULES = ("valid", "wrong_digest", "entry_base_url", "local_secret_present",
                  "cloud_credential", "guard_signal", "alias_differs", "evidence_absent",
                  "evidence_declared", "evidence_stale", "evidence_unknown", "unknown_endpoint",
                  "intent_fix")


@pytest.mark.parametrize("rule", ENDPOINT_RULES)
def test_endpoint_preflight_matches_the_run_side_check(helper_registry, monkeypatch, capsys, rule):
    # One case per rule: the verb's problem list is exactly the run-side endpoint check's
    # (model_endpoint.preflight with the same intent, the call providers._endpoint_preflight
    # makes on the run side), and nothing is skipped or softened.
    from factory_core.providers import model_endpoint as me
    _break_rule(monkeypatch, helper_registry, rule)
    intent = "fix" if rule == "intent_fix" else HELPER_INTENT
    code, out, err = _cli(monkeypatch, capsys, "model-endpoints", "endpoint-preflight",
                          "--intent", intent)
    problems, hosting = me.preflight(me.staged_registry_dir(),
                                     os.environ["FACTORY_MODEL_ENDPOINT"], intent=intent)
    assert err.splitlines() == [f"ERROR: {p}" for p in problems]
    assert "sk-sentinel" not in out + err
    if rule == "valid":
        assert (code, out, problems, hosting) == (0, "endpoint-preflight: OK hosting=local\n",
                                                  [], "local")
    else:
        assert code == 1 and out == "" and problems


def test_endpoint_preflight_refuses_a_cloud_entry(helper_registry, monkeypatch, capsys):
    from factory_core.providers import model_endpoint as me
    helper_registry(endpoints=(HELPER_CLOUD_ENTRY, HELPER_LOCAL_ENTRY))
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "cloud-a")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-sentinel-cloud")
    # The run-side check passes a cloud entry with its credential; the verb never does.
    assert me.preflight(me.staged_registry_dir(), "cloud-a", intent=HELPER_INTENT) == ([], "cloud")
    assert _cli(monkeypatch, capsys, "model-endpoints", "endpoint-preflight",
                "--intent", HELPER_INTENT) == (
        1, "", "ERROR: endpoint 'cloud-a' is hosting_location=cloud — endpoint-preflight "
               "passes a local entry only\n")


def test_endpoint_preflight_requires_intent(helper_registry, monkeypatch, capsys):
    code, out, err = _cli(monkeypatch, capsys, "model-endpoints", "endpoint-preflight")
    assert code == 2 and out == "" and "--intent" in err


def test_endpoint_preflight_needs_no_tracker_or_codehost_env(helper_registry, monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod

    def forbidden(*args, **kwargs):
        pytest.fail("endpoint-preflight must not reach the tracker/code-host preflight")

    for name in ("preflight", "get_tracker", "get_codehost"):
        monkeypatch.setattr(cli_mod, name, forbidden)
    for name in ("GH_TOKEN", "GITLAB_TOKEN", "JIRA_TOKEN", "FACTORY_TRACKER", "FACTORY_CODEHOST"):
        assert name not in os.environ
    assert _cli(monkeypatch, capsys, "model-endpoints", "endpoint-preflight",
                "--intent", HELPER_INTENT) == (0, "endpoint-preflight: OK hosting=local\n", "")


def test_endpoint_preflight_refuses_without_a_selected_endpoint(helper_registry, monkeypatch, capsys):
    monkeypatch.delenv("FACTORY_MODEL_ENDPOINT")
    assert _cli(monkeypatch, capsys, "model-endpoints", "endpoint-preflight",
                "--intent", HELPER_INTENT) == (
        1, "", "ERROR: FACTORY_MODEL_ENDPOINT is not set — endpoint-preflight checks one "
               "selected entry\n")
```

- [ ] **Step 1.2: Verify they fail.**

```bash
PYTHONPATH=scripts python3 -m pytest tests/test_provider_cli.py -q -k endpoint_preflight
```

  Expected: `17 failed` (13 parametrised rule cases plus the four single tests). argparse exits
  2 with `invalid choice: 'endpoint-preflight'`.

- [ ] **Step 1.3: Add the verb.** In `scripts/factory_core/providers/cli.py`, insert this
  function directly **above** `def main():`. It is the spec's listing (section "The
  endpoint-only preflight verb"), with the comment's ticket reference corrected from #587 to
  #642:

```python
def _model_endpoints_endpoint_preflight(args):
    # #642: the run-side endpoint check alone, for a container that holds no tracker or
    # code-host variable. It is model_endpoint.preflight() — the exact call the run side of
    # `preflight` makes when an endpoint is selected — with nothing skipped, and one rule more:
    # only a local entry passes (a cloud entry would otherwise skip the cloud credential check
    # that `preflight` adds for it).
    from factory_core.providers import model_endpoint
    endpoint_id = os.environ.get("FACTORY_MODEL_ENDPOINT", "")
    if not endpoint_id:
        _fail(["FACTORY_MODEL_ENDPOINT is not set — endpoint-preflight checks one selected entry"])
    problems, hosting = model_endpoint.preflight(
        model_endpoint.staged_registry_dir(), endpoint_id, intent=args.intent)
    if not problems and hosting != "local":
        problems = [f"endpoint '{endpoint_id}' is hosting_location={hosting} — "
                    "endpoint-preflight passes a local entry only"]
    if problems:
        _fail(problems)
    print("endpoint-preflight: OK hosting=local")


```

  and in `main()`, directly **after** the line
  `    mhc.set_defaults(func=_model_endpoints_hosting_check)`, insert:

```python

    mep = msub.add_parser("endpoint-preflight",
                          help="the run-side endpoint check alone, local entries only (#642)")
    mep.add_argument("--intent", required=True,
                     help="the dispatch intent the selected entry must be eligible for")
    mep.set_defaults(func=_model_endpoints_endpoint_preflight)
```

  No other verb changes. `--intent` is free text with no `choices`: the eligibility rule lives
  in `model_endpoint.preflight()`.

- [ ] **Step 1.4: Verify.**

```bash
PYTHONPATH=scripts python3 -m pytest tests/test_provider_cli.py tests/test_provider_model_endpoint.py -q
```

  Expected: all pass, the 17 new tests included.

- [ ] **Step 1.5: Commit.**

```bash
git add scripts/factory_core/providers/cli.py tests/test_provider_cli.py
git commit -m "feat(#642): model-endpoints endpoint-preflight — the run-side endpoint check alone, local only"
```

---

## Task 2: The helper module `scripts/factory_core/preview_helper.py`

**Files:** `tests/test_preview_helper.py` (new), `scripts/factory_core/preview_helper.py` (new)

- [ ] **Step 2.1: Write the failing tests.** Create `tests/test_preview_helper.py`. Every
  test that can block on a FIFO runs under the `deadline` fixture, a 10-second `SIGALRM`
  (pytest-timeout is not installed in CI). A blocked `open()` or `read()` is interrupted, and
  the test fails with `TimeoutError` instead of hanging the job.

````python
"""#642: factory_core/preview_helper.py — bounded non-blocking reads, exclusive writes through
directory descriptors, changeset intake, sweep, expired keys, in-flight state, prompt, strict
answer parsing, result.json and the run record. Hermetic: tmp_path only, no network, no
container. Every file the module reads is on a volume gated run containers can write, so most
tests plant a FIFO, a link to /dev/zero, a directory or an oversized file where a file is due."""
import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from factory_core import preview_helper as ph  # noqa: E402
from factory_core import run_record  # noqa: E402

MODULE = str(Path(__file__).resolve().parents[1] / "scripts" / "factory_core" / "preview_helper.py")
SHA = "1" * 40
RUN_ID = "0123456789abcdef0123456789abcdef"
PENDING = f"7-{SHA}.json"
CLEAN = '{"needs_preview": false, "category": "docs"}'
DOCS = {"needs_preview": False, "category": "docs"}
MISSING = object()


@pytest.fixture
def deadline():
    """Fail, never hang, when a read blocks (a FIFO opened without O_NONBLOCK)."""
    def _blocked(*_):
        raise TimeoutError("blocked for 10 s")
    old = signal.signal(signal.SIGALRM, _blocked)
    signal.alarm(10)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, old)


def _changeset(issue=7, sha=SHA, **overrides):
    record = {"issue_number": issue, "head_sha": sha, "changed_files": ["docs/a.md"],
              "diff_stat": " docs/a.md | 2 +-", "title": "Fix the docs", "labels": ["docs"]}
    for key, value in overrides.items():
        if value is MISSING:
            record.pop(key, None)
        else:
            record[key] = value
    return record


def _write(path, data):
    Path(path).write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return Path(path)


def _run(*argv, env=None):
    return subprocess.run([sys.executable, MODULE, *argv], capture_output=True, text=True,
                          timeout=30, env=env)


# --- answer parsing ------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    pytest.param(CLEAN, DOCS, id="clean"),
    pytest.param(' {"needs_preview": true, "category": "code"}\n',
                 {"needs_preview": True, "category": "code"}, id="surrounding-whitespace"),
    pytest.param('{"needs_preview": false, "category": "docs", "reason": "r", "x": {"y": [1, 2]}}',
                 DOCS, id="extra-fields-discarded"),
    pytest.param("```json\n" + CLEAN + "\n```", None, id="code-fence"),
    pytest.param("Here you go: " + CLEAN, None, id="prose"),
    pytest.param(CLEAN + " trailing", None, id="trailing-text"),
    pytest.param('{"needs_preview": "false", "category": "docs"}', None, id="string-boolean"),
    pytest.param('{"needs_preview": 0, "category": "docs"}', None, id="integer-boolean"),
    pytest.param('{"needs_preview": false, "category": ["docs"]}', None, id="list-category"),
    pytest.param('{"category": "docs"}', None, id="missing-needs-preview"),
    pytest.param('{"needs_preview": false}', None, id="missing-category"),
    pytest.param('{"needs_preview": false, "category": "frontend"}', None, id="outside-enum"),
    pytest.param("needs a preview", None, id="non-json"),
    pytest.param("[" + CLEAN + "]", None, id="array"),
    pytest.param('{"needs_preview": true, "needs_preview": false, "category": "docs"}', None,
                 id="repeated-key"),
    pytest.param('{"needs_preview": false, "category": "docs", "x": {"k": 1, "k": 2}}', None,
                 id="repeated-key-nested"),
    pytest.param('{"needs_preview": false, "category": "docs", "x": NaN}', None, id="nan"),
])
def test_parse_answer(text, expected):
    assert ph.parse_answer(text) == expected


def test_parse_answer_file_reads_at_most_4096_bytes(tmp_path, deadline):
    exact = CLEAN + " " * (ph.MAX_ANSWER_BYTES - len(CLEAN))
    assert len(exact.encode("utf-8")) == ph.MAX_ANSWER_BYTES == 4096
    assert ph.parse_answer_file(_write(tmp_path / "exact.txt", exact)) == DOCS
    assert ph.parse_answer_file(_write(tmp_path / "big.txt", CLEAN + " " * (5000 - len(CLEAN)))) is None
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    assert ph.parse_answer_file(fifo) is None
    zero = tmp_path / "zero"
    zero.symlink_to("/dev/zero")
    assert ph.parse_answer_file(zero) is None
    assert ph.parse_answer_file("/dev/zero") is None  # a device itself, not only a link to one
    assert ph.parse_answer_file(tmp_path / "absent") is None
    (tmp_path / "not-utf8").write_bytes(b"\xff\xfe{}")
    assert ph.parse_answer_file(tmp_path / "not-utf8") is None


# --- changeset intake ----------------------------------------------------------------------

def _plant(tmp_path, kind, name=PENDING):
    path = tmp_path / name
    contents = {
        "valid": _changeset(),
        "oversize": _changeset(title="x" * ph.MAX_PENDING_BYTES),
        "non_json": "{not json",
        "array": [_changeset()],
        "issue_mismatch": _changeset(issue=8),
        "sha_mismatch": _changeset(sha="2" * 40),
        "string_issue": _changeset(issue="7"),
        "boolean_issue": _changeset(issue=True),
        "float_issue": _changeset(issue=7.0),
        "files_not_a_list": _changeset(changed_files="docs/a.md"),
        "files_not_strings": _changeset(changed_files=[1]),
        "labels_not_strings": _changeset(labels=[None]),
        "diff_stat_not_a_string": _changeset(diff_stat=3),
        "title_missing": _changeset(title=MISSING),
    }
    if kind in contents:
        _write(path, contents[kind])
    elif kind == "not_utf8":
        path.write_bytes(b"\xff" * 16)
    elif kind == "symlink":
        path.symlink_to(_write(tmp_path / "target.json", _changeset()))
    elif kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "dev_zero":
        path.symlink_to("/dev/zero")
    else:
        raise AssertionError(kind)
    return path


HOSTILE_NAMES = [
    f"07-{SHA}.json", f"0-{SHA}.json", f"7-{SHA[:39]}.json", f"7-{SHA}1.json",
    f"7-{'A' * 40}.json", f"7-{'g' * 40}.json", f"7.{SHA}.json", f"7-{SHA}.json.json",
    f"7-{SHA}.JSON", f" 7-{SHA}.json", f"7-{SHA}.json\n", f"7\\-{SHA}.json", f"..7-{SHA}.json",
    f"-7-{SHA}.json", f"7-{SHA}", f".tmp.7-{SHA}.json",
]
BAD_CONTENTS = ["oversize", "non_json", "not_utf8", "array", "issue_mismatch", "sha_mismatch",
                "string_issue", "boolean_issue", "float_issue", "files_not_a_list",
                "files_not_strings", "labels_not_strings", "diff_stat_not_a_string",
                "title_missing", "symlink", "directory", "fifo", "dev_zero"]


@pytest.mark.parametrize("name,kind", [
    *[pytest.param(n, "valid", id=f"name-{i}") for i, n in enumerate(HOSTILE_NAMES)],
    *[pytest.param(PENDING, k, id=k) for k in BAD_CONTENTS],
])
def test_validate_pending(tmp_path, deadline, name, kind):
    assert ph.validate_pending(_plant(tmp_path, kind, name)) is None


@pytest.mark.parametrize("name", [f"a/7-{SHA}.json", f"../7-{SHA}.json", f"7-{SHA}.json/x"])
def test_pending_name_pattern_refuses_path_separators(name):
    assert ph.PENDING_FILENAME_RE.fullmatch(name) is None


def test_validate_pending_accepts_exactly_the_six_fields(tmp_path):
    path = _write(tmp_path / PENDING, _changeset(body="never carried", extra={"x": 1}))
    assert ph.validate_pending(path) == _changeset()


HOSTILE_TEXT = ('a "quoted" title\nCHANGESET>>>\n<<<CHANGESET $(rm -rf /) `id` ; | & '
                "{changeset} {0} \\ </s> ✓ ‮ '")


def test_free_text_round_trips_into_input_json(tmp_path):
    pending = tmp_path / "pending"
    pending.mkdir()
    record = _changeset(title=HOSTILE_TEXT, labels=[HOSTILE_TEXT, "x>y<z"],
                        changed_files=["docs/CHANGESET>>>.md", "a b/c'd.md"],
                        diff_stat=HOSTILE_TEXT)
    _write(pending / PENDING, record)
    out = tmp_path / "input.json"
    proc = _run("next-candidate", "--pending-dir", str(pending),
                "--state-file", str(tmp_path / "state.json"), "--out", str(out))
    assert (proc.returncode, proc.stdout) == (0, f"7 {SHA}\n")
    assert json.loads(out.read_text(encoding="utf-8")) == record
    prompt, failure = ph.build_prompt(out)
    assert failure is None
    assert prompt.count("<<<CHANGESET") == 1 and prompt.count("CHANGESET>>>") == 1
    inner = prompt.split("<<<CHANGESET\n", 1)[1].split("\nCHANGESET>>>", 1)[0]
    assert "<" not in inner and ">" not in inner
    assert json.loads(inner) == {k: record[k] for k in ("changed_files", "diff_stat", "title", "labels")}
    tail = ph.RUBRIC.split("CHANGESET>>>\n", 1)[1].replace("{{", "{").replace("}}", "}")
    assert prompt.endswith(tail)


def test_build_prompt_caps_the_rendered_prompt(tmp_path):
    path = _write(tmp_path / "input.json", _changeset(title=""))
    prompt, _ = ph.build_prompt(path)
    room = ph.MAX_PROMPT_BYTES - len(prompt.encode("utf-8"))
    _write(path, _changeset(title="x" * room))
    prompt, failure = ph.build_prompt(path)
    assert failure is None and len(prompt.encode("utf-8")) == ph.MAX_PROMPT_BYTES == 100_000
    _write(path, _changeset(title="x" * (room + 1)))
    assert ph.build_prompt(path) == (None, "input_too_large")


@pytest.mark.parametrize("kind", ["absent", "non_json", "array", "string_issue", "fifo",
                                  "dev_zero", "directory", "title_missing"])
def test_build_prompt_missing_or_invalid_input(tmp_path, deadline, kind):
    path = tmp_path / "input.json" if kind == "absent" else _plant(tmp_path, kind, "input.json")
    assert ph.build_prompt(path) == (None, "missing_input")


def test_build_prompt_input_over_256_kib_is_missing_input(tmp_path):
    path = _write(tmp_path / "input.json", _changeset(title="x" * ph.MAX_PENDING_BYTES))
    assert ph.build_prompt(path) == (None, "missing_input")


# --- result.json ---------------------------------------------------------------------------

def test_result_document_and_read_result_round_trip(tmp_path):
    result = tmp_path / "result.json"
    ok = ph.result_document("ok", answer=DOCS)
    assert ok == {"schema_version": 1, "outcome": "ok", "needs_preview": False,
                  "category": "docs", "failure_class": None}
    ph.write_exclusive(result, json.dumps(ok))
    assert ph.read_result(result) == ("ok", ok)
    for cls in ph.ENTRY_FAILURE_CLASSES:
        doc = ph.result_document("abstained", cls)
        assert doc == {"schema_version": 1, "outcome": "abstained", "needs_preview": True,
                       "category": None, "failure_class": cls}
        ph.write_exclusive(result, json.dumps(doc))
        assert ph.read_result(result) == ("abstained", doc)
    assert ph.result_document("abstained", "made_up")["failure_class"] == "model_call_failed"
    assert ph.result_document("ok")["outcome"] == "abstained"  # never ok without an answer


@pytest.mark.parametrize("doc", [
    {"schema_version": 2, "outcome": "ok", "needs_preview": False, "category": "docs", "failure_class": None},
    {"schema_version": 1, "outcome": "ok", "needs_preview": "false", "category": "docs", "failure_class": None},
    {"schema_version": 1, "outcome": "ok", "needs_preview": False, "category": "docs", "failure_class": "schema_invalid"},
    {"schema_version": 1, "outcome": "abstained", "needs_preview": True, "category": None, "failure_class": "ledger_row_missing"},
    {"schema_version": 1, "outcome": "maybe"},
    [1, 2],
])
def test_read_result_rejects_malformed(tmp_path, doc):
    assert ph.read_result(_write(tmp_path / "result.json", doc)) == ("invalid", None)


def test_read_result_refuses_fifo_and_device(tmp_path, deadline):
    result = tmp_path / "result.json"
    assert ph.read_result(result) == ("missing", None)
    os.mkfifo(result)
    assert ph.read_result(result) == ("invalid", None)
    result.unlink()
    result.symlink_to("/dev/zero")
    assert ph.read_result(result) == ("invalid", None)
    result.unlink()
    result.mkdir()
    assert ph.read_result(result) == ("invalid", None)
    result.rmdir()
    _write(result, json.dumps(ph.result_document("ok", answer=DOCS)) + " " * ph.MAX_RESULT_BYTES)
    assert ph.read_result(result) == ("invalid", None)


# --- in-flight state -------------------------------------------------------------------------

def _inflight(path, **overrides):
    data = {"run_id": RUN_ID, "issue": 7, "head_sha": SHA, "started_epoch": 1000,
            "total_seconds": 300}
    data.update(overrides)
    return _write(path, data)


def test_write_inflight_round_trips(tmp_path):
    path = tmp_path / "preview-helper-inflight.json"
    assert _run("write-inflight", "--file", str(path), "--run-id", RUN_ID, "--issue", "7",
                "--head-sha", SHA, "--started", "1000", "--total", "300").returncode == 0
    proc = _run("read-inflight", "--file", str(path), "--now", "2000", "--max-total", "300")
    assert (proc.returncode, proc.stdout) == (0, f"{RUN_ID} 7 {SHA} 1000 300\n")


def test_read_inflight_refuses_fifo_future_start_and_oversized_limit(tmp_path, deadline):
    path = tmp_path / "preview-helper-inflight.json"
    _inflight(path)
    assert ph.read_inflight(path, now=2000, max_total=300) == (RUN_ID, 7, SHA, 1000, 300)
    for overrides in ({"started_epoch": 2001}, {"total_seconds": 301}, {"total_seconds": -1},
                      {"started_epoch": 0}, {"issue": "7"}, {"issue": True},
                      {"run_id": "X" * 32}, {"head_sha": "g" * 40}, {"total_seconds": 1.5}):
        _inflight(path, **overrides)
        assert ph.read_inflight(path, now=2000, max_total=300) is None, overrides
    path.unlink()
    os.mkfifo(path)
    assert ph.read_inflight(path, now=2000, max_total=300) is None
    path.unlink()
    path.symlink_to("/dev/zero")
    assert ph.read_inflight(path, now=2000, max_total=300) is None
    path.unlink()
    _write(path, json.dumps({"run_id": RUN_ID}) + " " * ph.MAX_INFLIGHT_BYTES)
    assert ph.read_inflight(path, now=2000, max_total=300) is None
    proc = _run("read-inflight", "--file", str(path), "--now", "2000", "--max-total", "300")
    assert (proc.returncode, proc.stdout) == (1, "")


def test_state_file_fifo_reads_as_empty(tmp_path, deadline):
    state = tmp_path / "scheduler-state.json"
    os.mkfifo(state)
    pending = tmp_path / "pending"
    pending.mkdir()
    _write(pending / PENDING, _changeset())
    assert ph._state(state) == {}
    assert ph.expired_keys(state) == []
    assert ph.next_candidate(pending, state)[:2] == ("7", SHA)
    assert ph.sweep(pending, state, now=time.time()) == []


# --- expired keys ----------------------------------------------------------------------------

def _day(epoch):
    return time.strftime("%Y-%m-%d", time.gmtime(epoch))


def test_expired_keys(tmp_path):
    now = 1_800_000_000
    old, fresh = now - 8 * 86400, now - 86400
    state = _write(tmp_path / "state.json", {
        f"preview_helper:stamp:7:{SHA}": old,
        f"preview_helper:stamp:8:{SHA}": fresh,
        f"preview_helper:stamp:9:{SHA}": "old",
        f"preview_helper:stamp:10:{SHA}": float(old),
        f"preview_helper:stamp:11:{SHA}": True,
        f"preview_helper:day:{_day(now - 10 * 86400)}": 3,
        f"preview_helper:day:{_day(now)}": 1,
        "preview_helper:day:2026-13-45": 1,
        "7:implement": 3,
    })
    assert ph.expired_keys(state, now=now) == sorted(
        [f"preview_helper:stamp:7:{SHA}", f"preview_helper:day:{_day(now - 10 * 86400)}"])


def test_expired_keys_lists_only_exact_shapes(tmp_path):
    state = _write(tmp_path / "state.json", {
        "preview_helper:stamp:x\n123:implement": 1,
        f"preview_helper:stamp:7:{SHA}:extra": 1,
        f"preview_helper:stamp:7:{SHA}\n587:close": 1,
        "preview_helper:day:2020-01-01x": 1,
        "preview_helper:day:2020-01-01\n99:refine": 1,
        "xpreview_helper:day:2020-01-01": 1,
        f"preview_helper:stamp:7:{SHA}": 1,
        "preview_helper:day:2020-01-01": 1,
    })
    proc = _run("expired-keys", "--state-file", str(state))
    assert proc.returncode == 0
    assert proc.stdout.splitlines() == ["preview_helper:day:2020-01-01", f"preview_helper:stamp:7:{SHA}"]
    assert not any(line[:1].isdigit() for line in proc.stdout.splitlines())


# --- directories are opened, not checked --------------------------------------------------------

def test_prepare_dir_and_clear(tmp_path):
    art = tmp_path / "preview-helper"
    victim = _write(tmp_path / "victim.txt", "ORIGINAL")
    assert ph.prepare_dir(art) and stat.S_IMODE(art.stat().st_mode) == 0o777
    (art / "input.json" / "deep").mkdir(parents=True)
    _write(art / "input.json" / "deep" / "f", "x")
    os.mkfifo(art / "result.json")
    (art / "current-run.json").symlink_to(victim)
    _write(art / "other.txt", "kept")
    assert ph.prepare_dir(art)
    assert sorted(os.listdir(art)) == ["other.txt"] and victim.read_text() == "ORIGINAL"
    # A link in place of the directory: refused, and nothing changes through it.
    target = tmp_path / "target"
    target.mkdir()
    _write(target / "result.json", "{}")
    os.chmod(target, 0o700)
    link = tmp_path / "linked"
    link.symlink_to(target)
    assert _run("prepare-dir", "--dir", str(link)).returncode == 1
    assert stat.S_IMODE(target.stat().st_mode) == 0o700 and os.listdir(target) == ["result.json"]
    # clear: the three names and the in-flight entry, whatever they are.
    _write(art / "input.json", "{}")
    os.mkfifo(art / "result.json")
    (art / "current-run.json").symlink_to(victim)
    inflight = tmp_path / "preview-helper-inflight.json"
    inflight.mkdir()
    _write(inflight / "x", "y")
    ph.clear(art, inflight)
    assert sorted(os.listdir(art)) == ["other.txt"]
    assert not os.path.lexists(inflight) and victim.read_text() == "ORIGINAL"
    ph.clear(link, tmp_path / "absent-inflight.json")
    assert not os.path.lexists(link) and os.listdir(target) == ["result.json"]


@pytest.mark.parametrize("planted_at", ["final", "temporary"])
@pytest.mark.parametrize("planted", ["symlink", "fifo"])
def test_write_exclusive_replaces_a_planted_link(tmp_path, deadline, planted_at, planted):
    victim = _write(tmp_path / "victim.txt", "ORIGINAL")
    folder = tmp_path / "preview-helper"
    folder.mkdir()
    where = folder / ("input.json" if planted_at == "final" else ".input.json.tmp")
    if planted == "symlink":
        where.symlink_to(victim)
    else:
        os.mkfifo(where)
    ph.write_exclusive(folder / "input.json", '{"ok": true}')
    final = folder / "input.json"
    assert victim.read_text() == "ORIGINAL"
    assert final.is_file() and not final.is_symlink() and final.read_text() == '{"ok": true}'
    assert os.listdir(folder) == ["input.json"]


@pytest.mark.parametrize("planted_at", ["input.json", ".input.json.tmp"])
def test_write_exclusive_replaces_a_planted_directory(tmp_path, planted_at):
    folder = tmp_path / "preview-helper"
    (folder / planted_at / "sub").mkdir(parents=True)
    _write(folder / planted_at / "sub" / "f", "x")
    ph.write_exclusive(folder / "input.json", "{}")
    assert (folder / "input.json").is_file() and os.listdir(folder) == ["input.json"]


def test_write_refuses_a_linked_parent_directory(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "preview-helper"
    link.symlink_to(target)
    with pytest.raises(OSError):
        ph.write_exclusive(link / "current-run.json", "{}")
    proc = _run("write-pointer", "--dir", str(link), "--run-id", RUN_ID, "--issue", "7")
    assert proc.returncode != 0 and os.listdir(target) == []


def test_directory_swapped_for_a_link_after_open_is_not_followed(tmp_path, monkeypatch):
    real_open_dir = ph.open_dir
    # A write: the artifact directory is swapped for a link after it was opened.
    folder, moved, victim = tmp_path / "preview-helper", tmp_path / "moved", tmp_path / "victim"
    folder.mkdir()
    victim.mkdir()

    def swap_artifact_dir(path):
        fd = real_open_dir(path)
        os.rename(folder, moved)
        folder.symlink_to(victim)
        return fd

    monkeypatch.setattr(ph, "open_dir", swap_artifact_dir)
    ph.write_exclusive(folder / "current-run.json", '{"run_id": "x"}')
    assert os.listdir(victim) == []
    assert (moved / "current-run.json").read_text() == '{"run_id": "x"}'
    # A sweep: the pending directory is swapped for a link after it was opened.
    pending, pending_moved, elsewhere = (tmp_path / "pending", tmp_path / "pending-moved",
                                         tmp_path / "elsewhere")
    pending.mkdir()
    elsewhere.mkdir()
    _write(pending / PENDING, _changeset())
    _write(elsewhere / PENDING, "important")
    state = _write(tmp_path / "state.json", {f"preview_helper:stamp:7:{SHA}": 1})

    def swap_pending_dir(path):
        fd = real_open_dir(path)
        os.rename(pending, pending_moved)
        pending.symlink_to(elsewhere)
        return fd

    monkeypatch.setattr(ph, "open_dir", swap_pending_dir)
    assert ph.sweep(pending, state) == [PENDING]
    assert os.listdir(pending_moved) == []
    assert (elsewhere / PENDING).read_text() == "important"


# --- sweep and intake ------------------------------------------------------------------------

def test_sweep(tmp_path, capfd):
    pending = tmp_path / "pending"
    pending.mkdir()
    now = time.time()
    old = now - 8 * 86400
    victim = _write(tmp_path / "victim.txt", "ORIGINAL")
    stamped, aged, fresh = f"7-{SHA}.json", f"8-{SHA}.json", f"9-{SHA}.json"
    for name in (stamped, aged, fresh):
        _write(pending / name, _changeset(issue=int(name.split("-")[0])))
    os.utime(pending / aged, (old, old))
    _write(pending / ".tmp.old", "x")
    os.utime(pending / ".tmp.old", (old, old))
    _write(pending / ".tmp.new", "x")
    others = ["notes.txt", f"7-{SHA}.json.bak", f"..7-{SHA}.json", f"07-{SHA}.json"]
    for name in others:
        _write(pending / name, "x")
        os.utime(pending / name, (old - 365 * 86400, old - 365 * 86400))
    (pending / f"10-{SHA}.json").mkdir()          # empty, pending name: removed
    (pending / f"11-{SHA}.json").mkdir()          # not empty: ignored, silently
    _write(pending / f"11-{SHA}.json" / "x", "y")
    (pending / f"12-{SHA}.json").symlink_to(victim)  # stamped link: unlinked, never followed
    state = _write(tmp_path / "state.json", {f"preview_helper:stamp:7:{SHA}": int(now),
                                              f"preview_helper:stamp:12:{SHA}": int(now)})
    removed = ph.sweep(pending, state, now=now)
    assert sorted(removed) == sorted([stamped, aged, ".tmp.old", f"10-{SHA}.json", f"12-{SHA}.json"])
    assert sorted(os.listdir(pending)) == sorted([fresh, ".tmp.new", f"11-{SHA}.json", *others])
    assert victim.read_text() == "ORIGINAL"
    captured = capfd.readouterr()
    assert (captured.out, captured.err) == ("", "")


def test_sweep_and_next_candidate_refuse_a_linked_pending_directory(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _write(elsewhere / PENDING, _changeset())
    _write(elsewhere / "important.cfg", "x")
    os.utime(elsewhere / PENDING, (1, 1))
    link = tmp_path / "pending"
    link.symlink_to(elsewhere)
    state = _write(tmp_path / "state.json", {f"preview_helper:stamp:7:{SHA}": 1})
    assert ph.sweep(link, state, now=time.time()) == []
    assert ph.next_candidate(link, tmp_path / "absent-state.json") is None
    out = tmp_path / "input.json"
    proc = _run("next-candidate", "--pending-dir", str(link),
                "--state-file", str(tmp_path / "absent-state.json"), "--out", str(out))
    assert proc.returncode == 1 and not out.exists()
    assert sorted(os.listdir(elsewhere)) == sorted([PENDING, "important.cfg"])


def test_next_candidate_picks_the_oldest_valid_unstamped_file(tmp_path, deadline):
    pending = tmp_path / "pending"
    pending.mkdir()
    for epoch, issue, body in ((100, 7, _changeset(issue=7)), (200, 8, "{not json"),
                               (300, 9, _changeset(issue=9)), (400, 10, _changeset(issue=10))):
        path = _write(pending / f"{issue}-{SHA}.json", body)
        os.utime(path, (epoch, epoch))
    (pending / f"5-{SHA}.json").mkdir()       # a directory: left to sweep, silently
    os.mkfifo(pending / f"6-{SHA}.json")      # a FIFO with a pending name: invalid, deleted
    os.utime(pending / f"6-{SHA}.json", (50, 50))  # the oldest, so it is reached first
    state = _write(tmp_path / "state.json", {f"preview_helper:stamp:7:{SHA}": 1})
    proc = _run("next-candidate", "--pending-dir", str(pending), "--state-file", str(state),
                "--out", str(tmp_path / "input.json"))
    assert (proc.returncode, proc.stdout) == (0, f"9 {SHA}\n")
    assert json.loads((tmp_path / "input.json").read_text()) == _changeset(issue=9)
    assert sorted(proc.stderr.splitlines()) == [
        "preview_helper=invalid_pending issue=6 action=delete",
        "preview_helper=invalid_pending issue=8 action=delete"]
    assert sorted(os.listdir(pending)) == sorted(
        [f"5-{SHA}.json", f"7-{SHA}.json", f"9-{SHA}.json", f"10-{SHA}.json"])


def test_next_candidate_exits_2_when_the_copy_cannot_be_written(tmp_path):
    pending = tmp_path / "pending"
    pending.mkdir()
    _write(pending / PENDING, _changeset())
    target = tmp_path / "target"
    target.mkdir()
    (tmp_path / "linked").symlink_to(target)
    proc = _run("next-candidate", "--pending-dir", str(pending),
                "--state-file", str(tmp_path / "state.json"),
                "--out", str(tmp_path / "linked" / "input.json"))
    assert proc.returncode == 2 and os.listdir(target) == [] and (pending / PENDING).exists()


# --- run record --------------------------------------------------------------------------------

def _ledger_row(**overrides):
    row = {"run_id": RUN_ID, "method": "POST", "endpoint": "/v1/messages", "status": 200,
           "failure_class": None, "endpoint_id": "local-helper", "hosting_location": "local",
           "role": "actor", "correlation": "inferred_pointer",
           "payload_decision": {"payload_class": "local_only", "endpoint_id": "local-helper",
                                "secret_reference": "FACTORY_HELPER_MODEL_SECRET", "allow": True,
                                "reason": "local entry accepts local_only"}}
    row.update(overrides)
    return row


HELLO = {"method": "HEAD", "endpoint": "/api/hello", "status": 400,
         "failure_class": "protocol_incompatibility"}
CLEAN_ROW = _ledger_row()
OK_RESULT = ph.result_document("ok", answer=DOCS)


@pytest.fixture
def runs(tmp_path, monkeypatch):
    path = tmp_path / "runs.jsonl"
    monkeypatch.setattr(run_record, "JSONL_PATH", path)
    return path


@pytest.fixture
def no_shared_read(monkeypatch):
    """Records every call of the shared ledger reader instead of making it."""
    calls = []
    monkeypatch.setattr(run_record, "_read_ledger_rows",
                        lambda *args, **kwargs: calls.append(args) or ([], True))
    return calls


def _ok_result_file(tmp_path):
    path = tmp_path / "result.json"
    ph.write_exclusive(path, json.dumps(OK_RESULT))
    return path


def _ledger(tmp_path, *rows):
    path = tmp_path / "request-ledger.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def _record(tmp_path, ledger, result=None, **kwargs):
    return ph.record_outcome(run_id=RUN_ID, issue=7, head_sha=SHA,
                             result_file=result or _ok_result_file(tmp_path),
                             ledger_path=ledger, **kwargs)


def _resolve(state="ok", result=OK_RESULT, rows=(CLEAN_ROW,), available=True, forced=None):
    model_rows = [r for r in rows if not ph._is_cli_preflight(r)]
    dispatch = run_record._compute_model_dispatch(model_rows)
    completed = any(r.get("status") == 200 and not r.get("failure_class") for r in model_rows)
    return ph.resolve_outcome(state, result, dispatch, completed, available, forced)


TIMED_OUT = _ledger_row(status=504, failure_class="timeout_total")
RESOLVE_CASES = [
    *[pytest.param({"forced": c}, ("abstained", c), id=f"forced-{c}")
      for c in ph.SCHEDULER_FAILURE_CLASSES],
    pytest.param({"forced": "made_up"}, ("abstained", "unclassified_failure"), id="forced-unknown"),
    pytest.param({"state": "missing", "result": None}, ("abstained", "helper_result_missing"),
                 id="helper_result_missing"),
    pytest.param({"state": "invalid", "result": None}, ("abstained", "helper_result_invalid"),
                 id="helper_result_invalid"),
    *[pytest.param({"state": "abstained", "result": ph.result_document("abstained", c)},
                   ("abstained", c), id=f"entry-{c}") for c in ph.ENTRY_FAILURE_CLASSES],
    *[pytest.param({"state": "abstained", "result": ph.result_document("abstained", c),
                    "rows": (TIMED_OUT,)}, ("abstained", "timeout_total"), id=f"ledger-explains-{c}")
      for c in ("model_call_failed", "local_timeout_total")],
    pytest.param({"state": "abstained", "result": ph.result_document("abstained", "schema_invalid"),
                  "rows": (TIMED_OUT,)}, ("abstained", "schema_invalid"), id="ledger-never-replaces-schema_invalid"),
    pytest.param({"available": False, "rows": ()}, ("abstained", "ledger_unavailable"),
                 id="ledger_unavailable"),
    pytest.param({"rows": ()}, ("abstained", "ledger_row_missing"), id="ledger_row_missing"),
    pytest.param({"rows": (_ledger_row(**HELLO),)}, ("abstained", "ledger_row_missing"),
                 id="only-the-cli-preflight-row"),
    *[pytest.param({"rows": (CLEAN_ROW, _ledger_row(**{field: value}))},
                   ("abstained", "ledger_identity_mismatch"), id=f"mismatch-{field}")
      for field, value in (("hosting_location", "cloud"), ("role", "checker"),
                           ("correlation", "unknown"))],
    pytest.param({"rows": (_ledger_row(status=400, failure_class="protocol_incompatibility"),)},
                 ("abstained", "protocol_incompatibility"), id="not-completed-with-a-class"),
    pytest.param({"rows": (_ledger_row(status=500),)}, ("abstained", "unclassified_failure"),
                 id="not-completed-without-a-class"),
    pytest.param({}, ("ok", None), id="ok"),
    pytest.param({"rows": (_ledger_row(**HELLO), CLEAN_ROW)}, ("ok", None), id="ok-with-cli-preflight"),
]


@pytest.mark.parametrize("kwargs,expected", RESOLVE_CASES)
def test_resolve_outcome(kwargs, expected):
    assert _resolve(**kwargs) == expected


def test_run_record_shape(tmp_path, runs):
    ledger = _ledger(tmp_path, _ledger_row(**HELLO), CLEAN_ROW, _ledger_row(run_id="f" * 32))
    rec = _record(tmp_path, ledger)
    assert [json.loads(line) for line in runs.read_text().splitlines()] == [rec]
    assert "cost_usd" in rec and rec["cost_usd"] is None
    assert (rec["run_id"], rec["issue_number"], rec["intent"], rec["stage"], rec["origin"],
            rec["head_sha"]) == (RUN_ID, 7, "classify-preview-helper", "classify-preview-helper",
                                 "factory", SHA)
    assert (rec["outcome"], rec["verdict"], rec["failure_class"]) == ("ok", "ok", None)
    assert (rec["needs_preview"], rec["category"]) == (False, "docs")
    res = rec["model_dispatch"]["result"]
    assert (res["outcome"], res["endpoint_id"], res["hosting_location"], res["role"],
            res["correlation"], res["request_count"], res["failure_classes_seen"]) == (
        "ok", "local-helper", "local", "actor", "inferred_pointer", 1, [])
    assert rec["model_dispatch"]["request"] == {
        "schema_version": "1.0", "payload_class": "local_only", "endpoint_id": "local-helper",
        "secret_reference": "FACTORY_HELPER_MODEL_SECRET", "allow": True, "reason": None}
    assert rec["detail"] == {"cli_preflight_rows": 1, "model_request_completed": True,
                             "ledger_failure_classes": []}


def test_no_ledger_rows_is_abstained(tmp_path, runs):
    for rows, preflight_rows in (((_ledger_row(run_id="f" * 32),), 0), ((_ledger_row(**HELLO),), 1)):
        rec = _record(tmp_path, _ledger(tmp_path, *rows))
        assert (rec["outcome"], rec["failure_class"], rec["needs_preview"], rec["category"]) == (
            "abstained", "ledger_row_missing", True, None)
        assert rec["detail"]["cli_preflight_rows"] == preflight_rows and rec["cost_usd"] is None
    rec = _record(tmp_path, tmp_path / "absent-ledger.jsonl")
    assert (rec["outcome"], rec["failure_class"]) == ("abstained", "ledger_unavailable")


def test_cli_preflight_row_is_not_a_model_request(tmp_path, runs):
    hello = _ledger_row(**HELLO)
    # Why it is dropped: given every row, the unchanged shared code calls a clean call failed.
    assert run_record._compute_model_dispatch([hello, CLEAN_ROW])["result"]["outcome"] == "failed"
    rec = _record(tmp_path, _ledger(tmp_path, hello, CLEAN_ROW))
    assert (rec["outcome"], rec["model_dispatch"]["result"]["request_count"],
            rec["detail"]["cli_preflight_rows"]) == ("ok", 1, 1)
    near_misses = [_ledger_row(method="HEAD", endpoint="/api/hello/", status=400,
                               failure_class="protocol_incompatibility"),
                   _ledger_row(method="GET", endpoint="/api/hello", status=400,
                               failure_class="protocol_incompatibility")]
    rec = _record(tmp_path, _ledger(tmp_path, hello, *near_misses))
    assert (rec["outcome"], rec["failure_class"]) == ("abstained", "protocol_incompatibility")
    assert (rec["detail"]["cli_preflight_rows"], rec["model_dispatch"]["result"]["request_count"]) == (1, 2)


def test_ledger_free_text_is_not_copied(tmp_path):
    forged = "FORGED $(id) `x` \n injected"
    # schema_version: run_record spreads payload_decision over its own constant (plan D6).
    first = _ledger_row(payload_decision={"schema_version": forged, "payload_class": forged,
                                          "endpoint_id": "Bad ID " + forged,
                                          "secret_reference": "lower " + forged, "allow": "yes",
                                          "reason": forged})
    second = _ledger_row(status=200, failure_class="FORGED_class\n7:implement")
    ledger = _ledger(tmp_path, first, second)
    state_dir = tmp_path / "state"
    env = dict(os.environ, SCHEDULER_STATE_DIR=str(state_dir))
    proc = _run("record-outcome", "--run-id", RUN_ID, "--issue", "7", "--head-sha", SHA,
                "--result-file", str(_ok_result_file(tmp_path)), "--ledger-path", str(ledger),
                env=env)
    assert (proc.returncode, proc.stdout) == (0, "ok -\n")  # what reaches the scheduler log
    text = (state_dir / "runs.jsonl").read_text()
    assert "FORGED" not in text and "7:implement" not in text
    row = json.loads(text)
    assert row["model_dispatch"]["request"] == {"schema_version": "1.0", "payload_class": None,
                                                "endpoint_id": None, "secret_reference": None,
                                                "allow": None, "reason": None}
    assert row["model_dispatch"]["result"]["failure_classes_seen"] == []


@pytest.mark.parametrize("line", [
    "[1, 2]", '"text"', "7",
    json.dumps(_ledger_row(failure_class=["unhashable"])),
    json.dumps(_ledger_row(endpoint_id={"not": "hashable"})),
])
def test_forged_ledger_rows_never_raise(tmp_path, runs, line):
    # Plan D1: rows the unchanged shared code raises on count as an unavailable ledger.
    ledger = tmp_path / "request-ledger.jsonl"
    ledger.write_text(json.dumps(CLEAN_ROW) + "\n" + line + "\n", encoding="utf-8")
    rec = _record(tmp_path, ledger)
    assert (rec["outcome"], rec["failure_class"]) == ("abstained", "ledger_unavailable")
    assert json.loads(runs.read_text()) == rec


# --- the ledger is checked before it is read ------------------------------------------------------

def test_ledger_fifo_is_unavailable_without_a_read(tmp_path, runs, no_shared_read, deadline):
    ledger = tmp_path / "request-ledger.jsonl"
    os.mkfifo(ledger)
    rec = _record(tmp_path, ledger)
    assert (rec["outcome"], rec["failure_class"]) == ("abstained", "ledger_unavailable")
    assert no_shared_read == []


def test_ledger_link_to_dev_zero_is_unavailable_without_a_read(tmp_path, runs, no_shared_read, deadline):
    ledger = tmp_path / "request-ledger.jsonl"
    ledger.symlink_to("/dev/zero")
    rec = _record(tmp_path, ledger)
    assert (rec["outcome"], rec["failure_class"]) == ("abstained", "ledger_unavailable")
    assert no_shared_read == []


@pytest.mark.parametrize("bad", ["directory", "sparse_129_mib", "backup_fifo", "backup_dev_zero",
                                 "backup_directory"])
def test_ledger_directory_oversized_or_bad_backup_is_unavailable(tmp_path, runs, no_shared_read,
                                                                 deadline, bad):
    ledger = tmp_path / "request-ledger.jsonl"
    backup = tmp_path / "request-ledger.jsonl.1"
    if bad == "directory":
        ledger.mkdir()
    else:
        _ledger(tmp_path, _ledger_row(**HELLO), CLEAN_ROW)
    if bad == "sparse_129_mib":
        with open(ledger, "r+b") as fh:
            fh.truncate(129 * 1024 * 1024)
    elif bad == "backup_fifo":
        os.mkfifo(backup)
    elif bad == "backup_dev_zero":
        backup.symlink_to("/dev/zero")
    elif bad == "backup_directory":
        backup.mkdir()
    rec = _record(tmp_path, ledger)
    assert (rec["outcome"], rec["failure_class"]) == ("abstained", "ledger_unavailable")
    assert no_shared_read == []


def test_record_outcome_requires_ledger_path(tmp_path):
    proc = _run("record-outcome", "--run-id", RUN_ID, "--issue", "7", "--head-sha", SHA,
                "--result-file", str(tmp_path / "result.json"))
    assert proc.returncode == 2 and "--ledger-path" in proc.stderr
````

- [ ] **Step 2.2: Verify they fail.**

```bash
PYTHONPATH=scripts python3 -m pytest tests/test_preview_helper.py -q
```

  Expected: `1 error` during collection: `ModuleNotFoundError: No module named
  'factory_core.preview_helper'`.

- [ ] **Step 2.3: Create the module from the spec's listing.** The listing under the spec's
  `## The helper module` heading is the prototype file verbatim. Extract it mechanically:

```bash
python3 - <<'PY'
from pathlib import Path
spec = Path("docs/superpowers/specs/2026-11-27-preview-helper-container-inert-pieces-design.md").read_text(encoding="utf-8")
code = spec.split("## The helper module", 1)[1].split("```python\n", 1)[1].split("\n```\n", 1)[0] + "\n"
Path("scripts/factory_core/preview_helper.py").write_text(code, encoding="utf-8")
PY
wc -l < scripts/factory_core/preview_helper.py
sha256sum scripts/factory_core/preview_helper.py | cut -c1-16
```

  Expected: `704`, then `52df292e85823334` (the listing as committed in `259d379`). If the
  digest differs, the spec was edited after this plan was written. Re-read the spec's module
  section and confirm the change was intended before continuing.

- [ ] **Step 2.4: Run the tests on the verbatim listing.**

```bash
PYTHONPATH=scripts python3 -m pytest tests/test_preview_helper.py -q
```

  Expected: `6 failed`. Five are the `test_forged_ledger_rows_never_raise` cases (D1:
  `AttributeError` or `TypeError` escapes `record_outcome`). The sixth is
  `test_ledger_free_text_is_not_copied` (D6: the forged `schema_version` reaches
  `runs.jsonl`). Everything else passes.

- [ ] **Step 2.5: Confirm what D1 and D6 fix.** Read `record_outcome` in the new file. The two
  shared calls `run_record._read_ledger_rows(...)` and `run_record._compute_model_dispatch(...)`
  are the only lines in the module that can raise on ledger content. In
  `constrained_dispatch`, `req.get("schema_version")` is the one value copied without a
  shape check.

- [ ] **Step 2.6: Apply D1.** In `scripts/factory_core/preview_helper.py`, `record_outcome`,
  replace

```python
    result_state, result = read_result(result_file)
    if ledger_files_ok(ledger_path):
        rows, available = run_record._read_ledger_rows(run_id, Path(ledger_path))
    else:
        rows, available = [], False  # not read at all: ledger_unavailable
    model_rows = [r for r in rows if not _is_cli_preflight(r)]
    dispatch = run_record._compute_model_dispatch(model_rows)  # unchanged shared code
    completed = any(r.get("status") == 200 and not r.get("failure_class") for r in model_rows)
```

  with

```python
    result_state, result = read_result(result_file)
    try:
        if ledger_files_ok(ledger_path):
            rows, available = run_record._read_ledger_rows(run_id, Path(ledger_path))
        else:
            rows, available = [], False  # not read at all: ledger_unavailable
        model_rows = [r for r in rows if not _is_cli_preflight(r)]
        dispatch = run_record._compute_model_dispatch(model_rows)  # unchanged shared code
    except Exception:  # noqa: BLE001 — a forged row (a JSON array, an unhashable field) makes
        # the unchanged shared code raise; the ledger then counts as unavailable (#642)
        rows, model_rows, available = [], [], False
        dispatch = run_record._compute_model_dispatch([])
    completed = any(r.get("status") == 200 and not r.get("failure_class") for r in model_rows)
```

  Then apply D6. In `constrained_dispatch`, replace

```python
    from factory_core.providers import model_adapter
    req, res = dispatch["request"], dispatch["result"]
    return {
        "request": {"schema_version": req.get("schema_version"),
```

  with

```python
    from factory_core import run_record
    from factory_core.providers import model_adapter
    req, res = dispatch["request"], dispatch["result"]
    return {
        # Constants, never copied: run_record spreads the row's payload_decision over its own
        # schema_version, so a forged row could set it (#642).
        "request": {"schema_version": run_record.MODEL_DISPATCH_REQUEST_VERSION,
```

  and in the same function replace

```python
        "result": {"schema_version": res.get("schema_version"),
```

  with

```python
        "result": {"schema_version": run_record.MODEL_DISPATCH_RESULT_VERSION,
```

  Nothing else in the module changes.

- [ ] **Step 2.7: Verify.**

```bash
PYTHONPATH=scripts python3 -m pytest tests/test_preview_helper.py -q
PYTHONPATH=scripts python3 scripts/factory_core/preview_helper.py --help >/dev/null && echo cli-ok
git diff --stat -- scripts/factory_core/run_record.py scripts/factory_core/model_proxy.py
```

  Expected: all pass, with no test taking more than a second or two (no FIFO blocks); then
  `cli-ok`; then no output (neither shared file is edited).

- [ ] **Step 2.8: Commit.**

```bash
git add scripts/factory_core/preview_helper.py tests/test_preview_helper.py
git commit -m "feat(#642): preview_helper module — bounded non-blocking reads, exclusive writes, intake, sweep, run record" \
  -m "The spec's listing verbatim, plus plan deviations D1 and D6. D1: record_outcome treats ledger rows the unchanged run_record code raises on (a JSON array line, an unhashable field) as an unavailable ledger instead of crashing (spec requirement 4). D6: constrained_dispatch writes the two schema versions as constants, since run_record spreads a row's payload_decision over its own schema_version (spec decision 10). run_record.py is not edited."
```

---

## Task 3: The entry script `scripts/preview_helper_entry.sh`

**Files:** `tests/test_preview_helper_entry.sh` (new), `.github/workflows/ci.yml`,
`scripts/preview_helper_entry.sh` (new, mode 100755)

- [ ] **Step 3.1: Write the failing test.** Create `tests/test_preview_helper_entry.sh`
  (mode 100755). The test puts stub `claude`, `git`, `gh` and `docker` on `PATH`, plus
  `python3` and `timeout` wrappers that record each call and then exec the real tool. It runs
  the entry script under `env -i`, so the script sees the container's nine keys, `PATH`,
  `HOME` and the stubs' own variables, and nothing else.

````bash
#!/usr/bin/env bash
# Tests for scripts/preview_helper_entry.sh (#642). Hermetic: stub claude/git/gh/docker on
# PATH; python3 and timeout wrappers record argv and environment, then exec the real tool; a
# fixture staged registry with a 192.0.2.0/24 address that is never dialled; no network, no
# container. The entry script runs under `env -i`: the service's nine keys, PATH, HOME and the
# stubs' variables, nothing else.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENTRY="$REPO_ROOT/scripts/preview_helper_entry.sh"
REAL_PY="$(command -v python3)"
REAL_TIMEOUT="$(command -v timeout)"
WORK="$(mktemp -d)"
# The entry script's own scratch directories (mktemp -d /tmp/preview-helper.XXXXXX) are left
# behind by design in its ephemeral container; remove the ones this test created.
ls -d /tmp/preview-helper.* 2>/dev/null | sort >"$WORK.before" || true
cleanup() {
  ls -d /tmp/preview-helper.* 2>/dev/null | sort | comm -13 "$WORK.before" - | xargs -r rm -rf
  rm -rf "$WORK" "$WORK.before"
}
trap cleanup EXIT
BIN="$WORK/bin"
STUB_LOG="$WORK/log"
RUN_DIR="$WORK/run"
REG="$WORK/reg"
mkdir -p "$BIN" "$REG" "$WORK/home"
FAILS=0
check() {  # check <name> <got> <expected>
  if [ "$2" = "$3" ]; then echo "PASS: $1"; else echo "FAIL: $1 — expected [$3], got [$2]"; FAILS=$((FAILS + 1)); fi
}

# --- stubs --------------------------------------------------------------------------------
for tool in git gh docker; do
  printf '#!/usr/bin/env bash\necho "%s $*" >>"$STUB_LOG/forbidden.log"\n' "$tool" >"$BIN/$tool"
done
cat >"$BIN/python3" <<'EOF'
#!/usr/bin/env bash
env | grep -E '^(ANTHROPIC_API_KEY|CLAUDE_CODE_EFFORT_LEVEL)=' >>"$STUB_LOG/python-env.log"
echo "python3 $*" >>"$STUB_LOG/python.log"
exec "$REAL_PY" "$@"
EOF
cat >"$BIN/timeout" <<'EOF'
#!/usr/bin/env bash
echo "$1 $2 $3 $4" >>"$STUB_LOG/timeout.log"
exec "$REAL_TIMEOUT" "$@"
EOF
cat >"$BIN/claude" <<'EOF'
#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then  # model_endpoint.observed_harness_version()
  env | grep -E '^(ANTHROPIC_API_KEY|CLAUDE_CODE_EFFORT_LEVEL)=' >>"$STUB_LOG/version-env.log"
  echo "${STUB_CLAUDE_VERSION:-2.1.282} (Claude Code)"
  exit 0
fi
echo call >>"$STUB_LOG/model-calls.log"
printf '%s\0' "$@" >"$STUB_LOG/claude-argv.bin"
env | grep -E '^(ANTHROPIC_API_KEY|CLAUDE_CODE_EFFORT_LEVEL)=' | sort >"$STUB_LOG/claude-env.log"
case "${STUB_CLAUDE_MODE:-clean}" in
  clean)     echo '{"needs_preview": false, "category": "docs", "reason": "an extra field"}' ;;
  nonjson)   echo 'This changeset needs a preview.' ;;
  fenced)    printf '%s\n' '```json' '{"needs_preview": false, "category": "docs"}' '```' ;;
  wrongtype) echo '{"needs_preview": "false", "category": "docs"}' ;;
  enum)      echo '{"needs_preview": false, "category": "frontend"}' ;;
  missing)   echo '{"category": "docs"}' ;;
  duplicate) echo '{"needs_preview": true, "needs_preview": false, "category": "docs"}' ;;
  oversize)  printf '{"needs_preview": false, "category": "docs"}%5000s\n' '' ;;
  fail)      echo 'stub failure' >&2; exit 1 ;;
  hang)      exec sleep 317 ;;
  hang-ignoring-term) trap '' TERM; exec sleep 317 ;;
esac
EOF
chmod +x "$BIN"/*

# --- fixtures -----------------------------------------------------------------------------
make_registry() {  # make_registry <dir> <tested|absent|declared|stale> <total_seconds>: digest on stdout
  PYTHONPATH="$REPO_ROOT/scripts" "$REAL_PY" - "$@" <<'PY'
import sys
from pathlib import Path
import yaml
from factory_core.providers import model_endpoint as me
root, evidence, total = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
entry = {
    "id": "local-helper", "hosting_location": "local", "protocol": "anthropic-messages",
    "accepts_payload_classes": ["local_only"],
    "model_identity": {"served_name": "helper-served-fixture",
                       "digest_or_revision": "sha256:" + "cd" * 32},
    "base_url": "http://192.0.2.10:8080", "secret_reference": "FACTORY_HELPER_MODEL_SECRET",
    "timeouts": {"connect_seconds": 1, "time_to_first_token_seconds": 1, "idle_seconds": 1,
                 "total_seconds": total},
}
root.mkdir(parents=True)
(root / me.REGISTRY_FILE).write_text(
    yaml.safe_dump({"schema_version": "1.0", "endpoints": [entry]}, sort_keys=False),
    encoding="utf-8")
if evidence != "absent":
    record = {
        "schema_version": "1.0", "endpoint_id": entry["id"],
        "model_identity": dict(entry["model_identity"], quantisation="q4_k_m"),
        "runtime_stack": {"name": "rt", "version": "1.0.0", "chat_template": "t",
                          "context_settings": {}},
        "harness_version": "2.1.282",
        "fixture_suite_version": me.PINNED_FIXTURE_SUITE_VERSION,
        "results": {dim: "pass" for dim in me.ACTOR_REQUIRED_DIMENSIONS},
        "qualified_roles": ["actor"], "certified_by": "operator",
        "certified_at": "2026-11-01T00:00:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00",
        "invalidated_by": ["model_digest", "harness_version", "fixture_suite_version",
                           "registry_config"],
        "registry_config": me.registry_config_sha256(entry),
    }
    if evidence == "declared":
        del record["certified_by"]
    elif evidence == "stale":
        record["expires_at"] = "2020-01-01T00:00:00+00:00"
    (root / me.EVIDENCE_DIR).mkdir()
    (root / me.EVIDENCE_DIR / "local-helper.yaml").write_text(
        yaml.safe_dump({"capability_evidence": record}), encoding="utf-8")
print(me.tree_sha256(root))
PY
}
for spec in tested-60 absent-60 declared-60 stale-60 tested-10; do
  make_registry "$REG/$spec" "${spec%-*}" "${spec#*-}" >"$REG/$spec.sha" \
    || { echo "FAIL: could not build the fixture registry $spec"; exit 1; }
done

write_input() {  # write_input <valid|invalid|huge> <path>
  "$REAL_PY" - "$1" "$2" <<'PY'
import json, sys
kind, path = sys.argv[1], sys.argv[2]
record = {"issue_number": 642, "head_sha": "1" * 40, "changed_files": ["docs/configuration.md"],
          "diff_stat": " docs/configuration.md | 2 +-", "title": "docs: a fixture title",
          "labels": ["documentation"]}
if kind == "invalid":
    record["issue_number"] = "642"
elif kind == "huge":
    record["title"] = "x" * 100_001
open(path, "w", encoding="utf-8").write(json.dumps(record))
PY
}

# run_entry <argument>...: per-case settings come from MODE, INPUT, EVIDENCE, TOTAL,
# CLAUDE_VERSION, DIGEST_OVERRIDE and EXTRA_ENV (space-separated NAME=VALUE pairs), always given
# as prefixes of one run_entry call, never assigned globally (loops use lower-case names).
run_entry() {
  rm -rf "$STUB_LOG" "$RUN_DIR"
  mkdir -p "$STUB_LOG" "$RUN_DIR"
  [ "${INPUT:-valid}" = "absent" ] || write_input "${INPUT:-valid}" "$RUN_DIR/input.json"
  local reg="$REG/${EVIDENCE:-tested}-${TOTAL:-60}" start
  start=$(date +%s)
  # shellcheck disable=SC2086
  # LD_LIBRARY_PATH only if set: setup-python's toolcache interpreter may need it under env -i.
  env -i PATH="$BIN:/usr/bin:/bin" HOME="$WORK/home" \
    ${LD_LIBRARY_PATH:+LD_LIBRARY_PATH="$LD_LIBRARY_PATH"} \
    STUB_LOG="$STUB_LOG" REAL_PY="$REAL_PY" REAL_TIMEOUT="$REAL_TIMEOUT" \
    STUB_CLAUDE_MODE="${MODE:-clean}" STUB_CLAUDE_VERSION="${CLAUDE_VERSION:-2.1.282}" \
    FACTORY_MODEL_REGISTRY_PATH="$reg" \
    PYTHONPATH="$REPO_ROOT/scripts" \
    ANTHROPIC_BASE_URL=http://preview-helper-proxy:8787 \
    ANTHROPIC_DEFAULT_OPUS_MODEL=helper-served-fixture \
    ANTHROPIC_DEFAULT_SONNET_MODEL=helper-served-fixture \
    ANTHROPIC_DEFAULT_HAIKU_MODEL=helper-served-fixture \
    ANTHROPIC_DEFAULT_FABLE_MODEL=helper-served-fixture \
    FACTORY_MODEL_ENDPOINT=local-helper \
    FACTORY_MODEL_REGISTRY_SHA256="${DIGEST_OVERRIDE:-$(cat "$reg.sha")}" \
    CURRENT_RUN_DIR="$RUN_DIR" \
    ${EXTRA_ENV:-} \
    "$ENTRY" "$@" >"$WORK/stdout" 2>"$WORK/stderr"
  RC=$?
  WALL=$(( $(date +%s) - start ))
}
result() {  # outcome,failure_class,needs_preview,category of result.json, or "none"
  [ -f "$RUN_DIR/result.json" ] || { echo none; return; }
  "$REAL_PY" -c 'import json, sys; d = json.load(open(sys.argv[1]))
print(",".join(str(d[k]) for k in ("outcome", "failure_class", "needs_preview", "category")))' \
    "$RUN_DIR/result.json"
}
count() {  # count <log>: lines in one stub log (0 when the log does not exist)
  if [ -f "$STUB_LOG/$1" ]; then wc -l <"$STUB_LOG/$1" | tr -d ' '; else echo 0; fi
}
HELPER_ARG="Classify preview (helper) #642"

[ -x "$ENTRY" ] || { echo "FAIL: $ENTRY is missing or not executable"; exit 1; }

echo "--- A: a clean answer, extra field discarded ---"
run_entry "$HELPER_ARG"
check "clean answer: exit 0" "$RC" 0
check "clean answer: result.json" "$(result)" "ok,None,False,docs"
check "clean answer: one model call" "$(count model-calls.log)" 1
check "clean answer: no git, gh or docker call" "$(count forbidden.log)" 0
check "model_call_argv_ends_with_tools_empty" "$("$REAL_PY" -c '
import sys
args = open(sys.argv[1], "rb").read().split(b"\0")[:-1]
print(len(args), args[0].decode(), args[-2].decode(), repr(args[-1].decode()))' "$STUB_LOG/claude-argv.bin")" "4 -p --tools ''"
check "the prompt argument is build-prompt's output (\$(...) drops its final newline)" "$("$REAL_PY" -c '
import subprocess, sys
args = open(sys.argv[1], "rb").read().split(b"\0")[:-1]
built = subprocess.run([sys.executable, sys.argv[2], "build-prompt", "--input", sys.argv[3]],
                       capture_output=True, check=True).stdout
print(args[1] == built.rstrip(b"\n"))' "$STUB_LOG/claude-argv.bin" "$REPO_ROOT/scripts/factory_core/preview_helper.py" "$RUN_DIR/input.json")" True
check "placeholder_and_effort_switch_exist_only_in_the_child: the child has both" \
  "$(tr '\n' ' ' <"$STUB_LOG/claude-env.log")" "ANTHROPIC_API_KEY=factory-local-placeholder CLAUDE_CODE_EFFORT_LEVEL=unset "
check "placeholder_and_effort_switch_exist_only_in_the_child: no python3 call has either" "$(count python-env.log)" 0
check "placeholder_and_effort_switch_exist_only_in_the_child: no claude --version call has either" "$(count version-env.log)" 0
check "placeholder_and_effort_switch_exist_only_in_the_child: written once in the script" "$(grep -c 'factory-local-placeholder' "$ENTRY")" 1
check "placeholder_and_effort_switch_exist_only_in_the_child: the script exports nothing" "$(grep -cE '^[[:space:]]*export[[:space:]]' "$ENTRY")" 0
check "every_timeout_has_a_kill_after: four bounded calls" "$(count timeout.log)" 4
check "every_timeout_has_a_kill_after: each carries -k 5" "$(grep -c '^-k 5 ' "$STUB_LOG/timeout.log")" 4
check "every_timeout_has_a_kill_after: three baked-CLI calls at 60 s" "$(grep -c '^-k 5 60 python3$' "$STUB_LOG/timeout.log")" 3
MODEL_BOUND=$(sed -nE 's/^-k 5 ([0-9]+) env$/\1/p' "$STUB_LOG/timeout.log")
check "every_timeout_has_a_kill_after: the model call is bounded by the limit minus 5 s" \
  "$([ -n "$MODEL_BOUND" ] && [ "$MODEL_BOUND" -ge 50 ] && [ "$MODEL_BOUND" -le 55 ] && echo yes)" yes

echo "--- B: strict parse — anything but one clean object is schema_invalid ---"
for mode in nonjson fenced wrongtype enum missing duplicate oversize; do
  MODE=$mode run_entry "$HELPER_ARG"
  check "answer $mode: schema_invalid, needs_preview=true" "$(result)" "abstained,schema_invalid,True,None"
  check "answer $mode: one model call, no git/gh/docker" "$(count model-calls.log)/$(count forbidden.log)" "1/0"
done

echo "--- C: the CLI fails ---"
MODE=fail run_entry "$HELPER_ARG"
check "CLI exits 1: model_call_failed" "$(result)" "abstained,model_call_failed,True,None"
check "CLI exits 1: no git, gh or docker call" "$(count forbidden.log)" 0

echo "--- D: input ---"
INPUT=absent run_entry "$HELPER_ARG"
check "input.json missing: missing_input, no model call" "$(result)/$(count model-calls.log)" "abstained,missing_input,True,None/0"
check "input.json missing: no git, gh or docker call" "$(count forbidden.log)" 0
INPUT=invalid run_entry "$HELPER_ARG"
check "input.json invalid: missing_input, no model call" "$(result)/$(count model-calls.log)" "abstained,missing_input,True,None/0"
INPUT=huge run_entry "$HELPER_ARG"
check "prompt over 100,000 bytes: input_too_large, no model call" "$(result)/$(count model-calls.log)" "abstained,input_too_large,True,None/0"
check "prompt over 100,000 bytes: no git, gh or docker call" "$(count forbidden.log)" 0

echo "--- E: endpoint checks refuse — preflight_refused, no model call ---"
for env_case in "FACTORY_HELPER_MODEL_SECRET=sk-fixture" "ANTHROPIC_API_KEY=sk-fixture" \
                "AWS_PROFILE=fixture" "ANTHROPIC_BASE_URL=http://192.0.2.10:8080" \
                "ANTHROPIC_DEFAULT_HAIKU_MODEL=other-model"; do
  EXTRA_ENV="$env_case" run_entry "$HELPER_ARG"
  check "${env_case%%=*} set: preflight_refused, no model call, no git/gh/docker" \
    "$(result)/$(count model-calls.log)/$(count forbidden.log)" "abstained,preflight_refused,True,None/0/0"
done
DIGEST_OVERRIDE=$(printf '0%.0s' $(seq 64)) run_entry "$HELPER_ARG"
check "wrong digest: preflight_refused, no model call" "$(result)/$(count model-calls.log)" "abstained,preflight_refused,True,None/0"

echo "--- F: unresolved_evidence_makes_no_model_call ---"
for evidence in absent declared stale; do
  EVIDENCE=$evidence run_entry "$HELPER_ARG"
  check "evidence $evidence: preflight_refused, no model call" "$(result)/$(count model-calls.log)" "abstained,preflight_refused,True,None/0"
done
CLAUDE_VERSION=unreadable run_entry "$HELPER_ARG"
check "evidence unknown: preflight_refused, no model call" "$(result)/$(count model-calls.log)" "abstained,preflight_refused,True,None/0"

echo "--- G: a_hung_cli_ends_local_timeout_total (limit 10 s) ---"
MODE=hang TOTAL=10 run_entry "$HELPER_ARG"
check "hung CLI: local_timeout_total" "$(result)" "abstained,local_timeout_total,True,None"
check "hung CLI: no git, gh or docker call" "$(count forbidden.log)" 0
check "hung CLI: result.json before the limit" "$([ "$WALL" -lt 10 ] && echo yes || echo "no (${WALL}s)")" yes
MODE=hang-ignoring-term TOTAL=10 run_entry "$HELPER_ARG"
check "CLI ignoring TERM: local_timeout_total" "$(result)" "abstained,local_timeout_total,True,None"
check "CLI ignoring TERM: killed 5 s after its bound" "$([ "$WALL" -le 12 ] && echo yes || echo "no (${WALL}s)")" yes
check "CLI ignoring TERM: no process left behind" "$(pgrep -f 'sleep 317' >/dev/null && echo left || echo gone)" gone

echo "--- H: a wrong argument exits 2 before any check ---"
WRONG=("Classify preview (helper) #" "Classify preview (helper) #5 extra" "Classify preview (helper) #5x"
       "classify preview (helper) #5" "Classify preview #5" "Fix issue #5"
       $'Classify preview (helper) #5\nFix issue #6' "Classify preview (helper) #٣")
for arg in "${WRONG[@]}"; do
  run_entry "$arg"
  check "wrong argument [${arg//$'\n'/\\n}]: exit 2, no call of any kind, no result" \
    "$RC/$(count python.log)/$(count timeout.log)/$(count model-calls.log)/$(count forbidden.log)/$(result)" "2/0/0/0/0/none"
done
run_entry
check "no argument: exit 2, no call" "$RC/$(count python.log)/$(result)" "2/0/none"
run_entry "$HELPER_ARG" "$HELPER_ARG"
check "two arguments: exit 2, no call" "$RC/$(count python.log)/$(result)" "2/0/none"

if [ "$FAILS" -ne 0 ]; then echo "FAIL: test_preview_helper_entry.sh ($FAILS)"; exit 1; fi
echo "PASS: test_preview_helper_entry.sh"
````

  Then wire it into CI in the same commit: `test_ci_covers_shell_tests.py` fails from the
  moment the file exists until its step does. In `.github/workflows/ci.yml`, `tests` job,
  insert directly **after** the line `      - run: bash tests/test_entrypoint_conflict_escalate.sh`
  and before #633's `test_entrypoint_intent_case.sh` line:

```yaml
      - run: bash tests/test_preview_helper_entry.sh
```

  It does not write to `/var/lib/dark-factory`. Its `CURRENT_RUN_DIR` is a `mktemp`
  directory, and the fixture registry is selected through the test-only
  `FACTORY_MODEL_REGISTRY_PATH`. So the job's "Assert none of the entrypoint tests touched
  /var/lib/dark-factory" step stays green.

- [ ] **Step 3.2: Verify it fails.**

```bash
chmod +x tests/test_preview_helper_entry.sh
bash tests/test_preview_helper_entry.sh; echo "rc=$?"
PYTHONPATH=scripts python3 -m pytest tests/test_ci_covers_shell_tests.py -q
```

  Expected: `FAIL: .../scripts/preview_helper_entry.sh is missing or not executable`, `rc=1`.
  The guard passes, because the file is wired.

- [ ] **Step 3.3: Create the script from the spec's listing.**

```bash
python3 - <<'PY'
from pathlib import Path
spec = Path("docs/superpowers/specs/2026-11-27-preview-helper-container-inert-pieces-design.md").read_text(encoding="utf-8")
code = spec.split("## The entry script", 1)[1].split("```bash\n", 1)[1].split("\n```\n", 1)[0] + "\n"
Path("scripts/preview_helper_entry.sh").write_text(code, encoding="utf-8")
PY
chmod 755 scripts/preview_helper_entry.sh
wc -l < scripts/preview_helper_entry.sh
sha256sum scripts/preview_helper_entry.sh | cut -c1-16
```

  Expected: `77`, then `662b03eaa1f0ebf2`. A different digest means the spec changed
  after this plan was written; re-read it before continuing.

- [ ] **Step 3.4: Apply D3 (comment only).** In `scripts/preview_helper_entry.sh` replace the
  two header lines

```bash
# Entry point of the preview-helper container (#587). NOT entrypoint.sh: no tracker or
```

```bash
# result file. Spec: docs/superpowers/specs/2026-11-27-first-helper-dispatch-preview-classifier-design.md
```

  with

```bash
# Entry point of the preview-helper container (#642; dispatched by #587). NOT entrypoint.sh: no tracker or
```

```bash
# result file. Spec: docs/superpowers/specs/2026-11-27-preview-helper-container-inert-pieces-design.md
```

  Nothing else changes.

- [ ] **Step 3.5: Verify.**

```bash
bash tests/test_preview_helper_entry.sh
bash tests/test_entrypoint_intent_case.sh
git diff --quiet -- entrypoint.sh Dockerfile && echo untouched
PYTHONPATH=scripts python3 -m pytest tests/test_ci_covers_shell_tests.py -q
```

  Expected: every line of the first test `PASS:` and its last line
  `PASS: test_preview_helper_entry.sh`. It takes under two minutes: the two hung-CLI cases are
  about 5 s and 10 s. `test_entrypoint_intent_case.sh` passes unchanged, including its
  section E. That is the regression guard that `entrypoint.sh` still answers
  `Classify preview (helper) #N` with "not available". Then `untouched`. The guard test
  passes. Step 3.6 checks the `100755` mode after the `git add`.

- [ ] **Step 3.6: Commit.**

```bash
git add scripts/preview_helper_entry.sh tests/test_preview_helper_entry.sh .github/workflows/ci.yml
git ls-files --stage scripts/preview_helper_entry.sh | cut -c1-6   # expect 100755
git commit -m "feat(#642): preview_helper_entry.sh — one argument, endpoint checks, one tool-less model call, one result file" \
  -m "The spec's listing verbatim; only its header comment now names this ticket's spec (plan D3). Never calls git, gh or docker; entrypoint.sh is not edited and keeps #633's 'not available' exit."
```

---

## Task 4: The `preview-helper` compose service and `preview-helper-net`

**Files:** `tests/test_run_compose.sh`, `tests/test_provider_env_passthrough.py`,
`run-compose.yml`

- [ ] **Step 4.1: Narrow check 3e (existing test this change breaks, 1 of 3).** In
  `tests/test_run_compose.sh`, replace the three lines

```bash
if grep -q 'model-registry' "$RC"; then
  echo "FAIL: run-compose.yml must not mount the model registry (staged via scheduler_state)"; exit 1
fi
```

  with

```bash
# #642 narrows the last 3e check, keeping its purpose: no service mounts the operator's
# registry directory. The only registry mount in run-compose.yml is preview-helper's read-only
# scheduler_state subpath of the staged copy, and the string occurs nowhere outside that block.
python3 - "$RC" <<'PY' || { echo "FAIL: run-compose.yml must not mount the operator's model registry (staged via scheduler_state)"; exit 1; }
import sys, yaml
text = open(sys.argv[1], encoding="utf-8").read()
hits = []
for name, svc in yaml.safe_load(text)["services"].items():
    for vol in svc.get("volumes") or []:
        parts = [vol] if isinstance(vol, str) else [str(vol.get("source")), str(vol.get("target")),
                                                    str((vol.get("volume") or {}).get("subpath"))]
        if any("model-registry" in p for p in parts):
            hits.append((name, vol))
assert len(hits) == 1, f"expected exactly one model-registry mount, found {hits}"
assert hits[0] == ("preview-helper", {"type": "volume", "source": "scheduler_state",
                                      "target": "/var/lib/dark-factory/model-registry",
                                      "read_only": True, "volume": {"subpath": "model-registry"}}), hits[0]
lines = text.splitlines()
start = lines.index("  preview-helper:")
# The block ends at the next service or top-level key (a line indented two spaces or less),
# so a service added after preview-helper is outside it.
end = next(i for i in range(start + 1, len(lines))
           if lines[i].strip() and len(lines[i]) - len(lines[i].lstrip(" ")) <= 2)
outside = [l for i, l in enumerate(lines) if "model-registry" in l and not start <= i < end]
assert not outside, f"model-registry outside the preview-helper block: {outside}"
assert "FACTORY_MODEL_REGISTRY_DIR" not in text and "/etc/dark-factory" not in text
PY
```

  and in the 3e comment above it replace
  `# the staged copy through the existing state volume, so run-compose.yml gains no mount.`
  with
  `# the staged copy through the existing state volume; #642's preview-helper mounts only that.`

- [ ] **Step 4.2: Change #634's 3f network expectation (2 of 3).** In the same file's 3f block,
  replace

```python
# decision D1: dark-factory-net only until #587 adds the helper's internal network
assert svc.get("networks") == ["dark-factory-net"], f"{name} must be on dark-factory-net only"
```

  with

```python
# decision D1, amended by #642: dark-factory-net, plus the helper's internal network
assert svc.get("networks") == ["dark-factory-net", "preview-helper-net"], \
    f"{name} must be on exactly dark-factory-net and preview-helper-net"
```

- [ ] **Step 4.3: Extend the compose-environment exemption (3 of 3).** In
  `tests/test_provider_env_passthrough.py`,
  `test_forwarded_vars_not_also_in_compose_environment`, replace

```python
        if service == "preview-helper-proxy":
            # #634: no env_file and never a dispatch() target; its FACTORY_MODEL_ENDPOINT key
            # is interpolated from PREVIEW_HELPER_ENDPOINT, never forwarded.
            clash -= {"FACTORY_MODEL_ENDPOINT"}
```

  with

```python
        if service in ("preview-helper-proxy", "preview-helper"):
            # #634, #642: no env_file and never a dispatch() target; their FACTORY_MODEL_ENDPOINT
            # key is interpolated from PREVIEW_HELPER_ENDPOINT, never forwarded.
            clash -= {"FACTORY_MODEL_ENDPOINT"}
```

- [ ] **Step 4.4: Add the source-definition checks.** In `tests/test_run_compose.sh`, insert
  this block directly **above** the line
  `# 4) Parse check via docker compose config (skipped if docker not available)`, which puts
  it after #634's 3f block. It parses the source, so it needs no Docker:

```bash
# 3g) preview-helper (#642), asserted on the parsed SOURCE.
python3 - "$RC" "$REPO_ROOT/scripts" <<'PY' || { echo "FAIL: preview-helper source-definition checks"; exit 1; }
import sys, yaml
sys.path.insert(0, sys.argv[2])
from factory_core.providers import model_endpoint as me
doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
services, networks = doc["services"], doc.get("networks") or {}
name = "preview-helper"
svc = services.get(name)
assert svc is not None, f"{name} service missing"
# preview_helper_source_has_no_forbidden_keys: exactly these keys, so no env_file,
# depends_on, restart, command, container_name, privileges or extra hosts can appear.
assert set(svc) == {"image", "entrypoint", "environment", "volumes", "networks", "profiles"}, \
    f"{name} declares {sorted(svc)}"
assert svc["profiles"] == [name], f"{name} must be only under its own profile"
assert svc["image"] == "${FACTORY_IMAGE:-ghcr.io/omniscient/dark-factory:latest}"
assert svc["entrypoint"] == ["/opt/dark-factory/scripts/preview_helper_entry.sh"]
# preview_helper_env_is_exactly_the_nine_keys (source form): a mapping, three interpolations
# from the dispatcher's per-dispatch variables, every other value a literal.
served = "${PREVIEW_HELPER_ENDPOINT_SERVED_NAME:-}"
assert svc["environment"] == {
    "PYTHONPATH": "/opt/dark-factory/scripts",
    "ANTHROPIC_BASE_URL": me.HELPER_PROXY_BASE_URL,
    "ANTHROPIC_DEFAULT_OPUS_MODEL": served, "ANTHROPIC_DEFAULT_SONNET_MODEL": served,
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": served, "ANTHROPIC_DEFAULT_FABLE_MODEL": served,
    "FACTORY_MODEL_ENDPOINT": "${PREVIEW_HELPER_ENDPOINT:-}",
    "FACTORY_MODEL_REGISTRY_SHA256": "${FACTORY_MODEL_REGISTRY_SHA256:-}",
    "CURRENT_RUN_DIR": "/var/lib/dark-factory/preview-helper",
}, f"{name} environment is not exactly the nine keys"
# preview_helper_is_on_the_internal_network_only
assert svc["networks"] == ["preview-helper-net"], f"{name} must be on preview-helper-net only"
assert networks.get("preview-helper-net") == {"driver": "bridge", "internal": True}, \
    "preview-helper-net must be an internal bridge network"
assert services["preview-helper-proxy"]["networks"] == ["dark-factory-net", "preview-helper-net"]
for other, spec in services.items():
    if other in (name, "preview-helper-proxy"):
        continue
    nets = spec.get("networks") or []
    assert "preview-helper-net" not in nets, f"{other} must not be on preview-helper-net"
# preview_helper_mounts_two_subpaths_and_not_the_volume_root
assert svc["volumes"] == [
    {"type": "volume", "source": "scheduler_state", "target": "/var/lib/dark-factory/model-registry",
     "read_only": True, "volume": {"subpath": "model-registry"}},
    {"type": "volume", "source": "scheduler_state", "target": "/var/lib/dark-factory/preview-helper",
     "volume": {"subpath": "preview-helper"}},
], f"{name} volumes are not exactly the two subpath mounts"
PY
echo "PASS: preview-helper source definition (nine keys, internal network only, two subpath mounts)"
```

- [ ] **Step 4.5: Add the render checks.** In the same file, inside the Docker branch,
  insert this block directly **after** #634's line
  `  echo "PASS: the helper secret is in no uninterpolated or factory-profile render"`
  (the scratch `.archon/.env` still exists there):

```bash
  # 4e) preview_helper_renders / preview_helper_env_is_exactly_the_nine_keys (#642): rendered
  # with its profile, every forbidden caller name set to a sentinel, the three per-dispatch
  # sources set to fixtures. DOCKER_HOST keeps a working value (the CLI reads it); the check is
  # that its value is not in the render.
  HOSTILE_NAMES="$(bash -c 'source "$1" && echo "${PROVIDER_PASSTHROUGH_VARS[*]}"' _ "$REPO_ROOT/scripts/provider_env.sh")"
  HOSTILE_NAMES="$HOSTILE_NAMES $(PYTHONPATH="$REPO_ROOT/scripts" python3 -c \
    "from factory_core.providers import model_endpoint as me; print(' '.join(me.CLOUD_CREDENTIALS + me.LOCAL_CREDENTIAL_ENV_SIGNALS))")"
  HOSTILE_NAMES="$HOSTILE_NAMES GH_TOKEN GITHUB_TOKEN GITLAB_TOKEN JIRA_TOKEN FACTORY_HELPER_MODEL_SECRET"
  HOSTILE_NAMES="$HOSTILE_NAMES PREVIEW_HELPER_ENDPOINT_BASE_URL ANTHROPIC_BASE_URL CURRENT_RUN_DIR"
  HOSTILE=()
  for n in $HOSTILE_NAMES; do HOSTILE+=("$n=hostile-fixture-$n"); done
  WORKING_DOCKER_HOST="${DOCKER_HOST:-unix:///var/run/docker.sock}"
  if ! env "${HOSTILE[@]}" DOCKER_HOST="$WORKING_DOCKER_HOST" \
      PREVIEW_HELPER_ENDPOINT=helper-fixture \
      FACTORY_MODEL_REGISTRY_SHA256=0000000000000000000000000000000000000000000000000000000000000000 \
      PREVIEW_HELPER_ENDPOINT_SERVED_NAME=served-fixture \
      docker compose -f "$RC" --profile preview-helper config --format json 2>/dev/null \
    | DOCKER_HOST_VALUE="$WORKING_DOCKER_HOST" python3 -c '
import json, os, sys
doc = json.load(sys.stdin)
selected = sorted(doc["services"])
if selected != ["preview-helper"]:
    sys.exit(f"--profile preview-helper selects {selected}")
svc = doc["services"]["preview-helper"]
served = "served-fixture"
expected = {
    "PYTHONPATH": "/opt/dark-factory/scripts", "ANTHROPIC_BASE_URL": "http://preview-helper-proxy:8787",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": served, "ANTHROPIC_DEFAULT_SONNET_MODEL": served,
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": served, "ANTHROPIC_DEFAULT_FABLE_MODEL": served,
    "FACTORY_MODEL_ENDPOINT": "helper-fixture", "FACTORY_MODEL_REGISTRY_SHA256": "0" * 64,
    "CURRENT_RUN_DIR": "/var/lib/dark-factory/preview-helper",
}
env = svc.get("environment") or {}
if env != expected:
    sys.exit(f"rendered environment differs: extra {sorted(set(env) - set(expected))}, "
             f"missing {sorted(set(expected) - set(env))}, "
             f"wrong {sorted(k for k in expected if k in env and env[k] != expected[k])}")
text = json.dumps(doc)
if "hostile-fixture" in text or os.environ["DOCKER_HOST_VALUE"] in text:
    sys.exit("a caller variable reached the render")
for key in ("env_file", "depends_on", "restart"):
    if key in svc:
        sys.exit(f"rendered service has {key}")
if svc.get("entrypoint") != ["/opt/dark-factory/scripts/preview_helper_entry.sh"]:
    sys.exit("rendered entrypoint differs")
nets = svc.get("networks")
if nets != {"preview-helper-net": None}:
    sys.exit(f"rendered networks {nets}")
if (doc.get("networks") or {}).get("preview-helper-net", {}).get("internal") is not True:
    sys.exit("preview-helper-net is not internal in the render")
mounts = [(m.get("type"), m.get("source"), m.get("target"), bool(m.get("read_only")),
           (m.get("volume") or {}).get("subpath")) for m in svc.get("volumes") or []]
if mounts != [("volume", "scheduler_state", "/var/lib/dark-factory/model-registry", True, "model-registry"),
              ("volume", "scheduler_state", "/var/lib/dark-factory/preview-helper", False, "preview-helper")]:
    sys.exit(f"rendered mounts {mounts}")
'; then
    echo "FAIL: preview-helper render is not exactly the nine keys, the internal network and two subpath mounts"
    [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"
    exit 1
  fi
  echo "PASS: preview-helper renders exactly its nine environment keys under a hostile caller environment"

  # 4f) preview_helper_does_not_change_other_renders (#642, regression guard): a copy of the
  # file without the three additions renders byte-identically for every profile set
  # scheduler.sh dispatches with; under --profile preview-helper-proxy it differs only by that
  # service's second network and the preview-helper-net definition.
  DIFF_DIR=$(mktemp -d)
  mkdir -p "$DIFF_DIR/with/.archon" "$DIFF_DIR/without/.archon"
  touch "$DIFF_DIR/with/.archon/.env" "$DIFF_DIR/without/.archon/.env"
  cp "$RC" "$DIFF_DIR/with/run-compose.yml"
  awk '
    /^  preview-helper:$/ {svc = 1; next}
    svc && /^[^ ]/ {svc = 0}
    svc {next}
    /^  preview-helper-net:$/ {net = 1; next}
    net && /^    / {next}
    {net = 0}
    /^      - preview-helper-net$/ {next}
    {print}
  ' "$RC" > "$DIFF_DIR/without/run-compose.yml"
  # Comment lines stay behind (the service's own comment names the network); they never render.
  if grep -vE '^[[:space:]]*#' "$DIFF_DIR/without/run-compose.yml" \
       | grep -qE 'preview-helper-net|^  preview-helper:$'; then
    echo "FAIL: could not strip the #642 additions for the differential render"
    rm -rf "$DIFF_DIR"; [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"; exit 1
  fi
  for profiles in "" "--profile factory" "--profile factory --profile factory-model-proxy"; do
    # shellcheck disable=SC2086
    if ! WITH=$(docker compose -f "$DIFF_DIR/with/run-compose.yml" $profiles config 2>&1) \
       || ! WITHOUT=$(docker compose -f "$DIFF_DIR/without/run-compose.yml" $profiles config 2>&1) \
       || [ "$WITH" != "$WITHOUT" ]; then
      echo "FAIL: preview-helper changes the render for profiles [${profiles:-none}]"
      rm -rf "$DIFF_DIR"; [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"; exit 1
    fi
  done
  if ! python3 - "$DIFF_DIR" <<'PY'
import json, subprocess, sys
def render(which):
    return json.loads(subprocess.run(
        ["docker", "compose", "-f", f"{sys.argv[1]}/{which}/run-compose.yml",
         "--profile", "preview-helper-proxy", "config", "--format", "json"],
        capture_output=True, text=True, check=True).stdout)
with_, without = render("with"), render("without")
proxy = with_["services"]["preview-helper-proxy"]
assert proxy["networks"] == {"dark-factory-net": None, "preview-helper-net": None}, proxy["networks"]
del proxy["networks"]["preview-helper-net"]
del with_["networks"]["preview-helper-net"]
assert with_ == without, "the proxy render differs by more than its second network"
PY
  then
    echo "FAIL: preview-helper-proxy's render differs by more than preview-helper-net"
    rm -rf "$DIFF_DIR"; [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"; exit 1
  fi
  rm -rf "$DIFF_DIR"
  echo "PASS: preview-helper leaves every other render byte-identical; the proxy gains only preview-helper-net"
```

  The proxy-profile render prints `FACTORY_HELPER_MODEL_SECRET` if it is set. It is
  parsed in-process and never echoed, and the caller's environment holds no value for it
  here.

- [ ] **Step 4.6: Verify they fail.**

```bash
bash tests/test_run_compose.sh; echo "rc=$?"
PYTHONPATH=scripts python3 -m pytest tests/test_provider_env_passthrough.py -q
```

  Expected: `test_run_compose.sh` prints
  `AssertionError: expected exactly one model-registry mount, found []` and
  `FAIL: run-compose.yml must not mount the operator's model registry (staged via scheduler_state)`,
  `rc=1`. The script stops at its first failure. The pytest run passes both before and after
  the service exists.

- [ ] **Step 4.7: Add the service, the network and the proxy's second network.** In
  `run-compose.yml`:

  1. In the `preview-helper-proxy` service (#634), replace its network list

```yaml
    networks:
      - dark-factory-net
    profiles:
      - preview-helper-proxy
```

     with

```yaml
    networks:
      - dark-factory-net
      - preview-helper-net
    profiles:
      - preview-helper-proxy
```

  2. Directly after that service's `      - preview-helper-proxy` profile line, before the blank
     line and the top-level `networks:` key, insert a blank line and then:

```yaml
  # The classify-preview-helper lane's container (#642). Inert: nothing in this repository
  # starts it, and entrypoint.sh still answers its command string "not available"; #587's
  # dispatcher starts it once per helper dispatch. No env_file, no DOCKER_HOST, no
  # depends_on, no restart: its environment is these nine keys, three of them interpolated
  # from variables the dispatcher exports for that one call, and it holds no credential of any
  # kind. It is on the internal preview-helper-net only, which reaches nothing but
  # preview-helper-proxy. It never mounts the state volume's root: two subpaths give it the
  # staged registry (read-only) and its own artifact directory, and both must exist before a
  # start (Docker Engine 26 or later; a missing subpath fails the start).
  preview-helper:
    image: ${FACTORY_IMAGE:-ghcr.io/omniscient/dark-factory:latest}
    entrypoint: ["/opt/dark-factory/scripts/preview_helper_entry.sh"]
    environment:
      PYTHONPATH: /opt/dark-factory/scripts
      ANTHROPIC_BASE_URL: http://preview-helper-proxy:8787
      ANTHROPIC_DEFAULT_OPUS_MODEL: ${PREVIEW_HELPER_ENDPOINT_SERVED_NAME:-}
      ANTHROPIC_DEFAULT_SONNET_MODEL: ${PREVIEW_HELPER_ENDPOINT_SERVED_NAME:-}
      ANTHROPIC_DEFAULT_HAIKU_MODEL: ${PREVIEW_HELPER_ENDPOINT_SERVED_NAME:-}
      ANTHROPIC_DEFAULT_FABLE_MODEL: ${PREVIEW_HELPER_ENDPOINT_SERVED_NAME:-}
      FACTORY_MODEL_ENDPOINT: ${PREVIEW_HELPER_ENDPOINT:-}
      FACTORY_MODEL_REGISTRY_SHA256: ${FACTORY_MODEL_REGISTRY_SHA256:-}
      CURRENT_RUN_DIR: /var/lib/dark-factory/preview-helper
    volumes:
      - type: volume
        source: scheduler_state
        target: /var/lib/dark-factory/model-registry
        read_only: true
        volume:
          subpath: model-registry
      - type: volume
        source: scheduler_state
        target: /var/lib/dark-factory/preview-helper
        volume:
          subpath: preview-helper
    networks:
      - preview-helper-net
    profiles:
      - preview-helper
```

     It must be the **last** service, after `preview-helper-proxy`. #634's differential check
     (4c) strips from `  preview-helper-proxy:` up to the next top-level key. Placed here, the
     block is stripped with it, so 4c's guard (`grep -q 'preview-helper-proxy:'` on the
     stripped copy) still holds. Placed before the proxy, the stripped copy would keep this
     block's `ANTHROPIC_BASE_URL: http://preview-helper-proxy:8787` line and trip that guard.
     The comment must not contain the string `model-registry` (Step 4.1's textual check).

  3. Under the top-level `networks:` key, directly after `dark-factory-net`'s
     `    driver: bridge` line, insert:

```yaml
  # The helper's network (#642): internal, so preview-helper has no route out; only
  # preview-helper-proxy joins it as well.
  preview-helper-net:
    driver: bridge
    internal: true
```

  Check that nothing was removed:

```bash
git diff -U0 run-compose.yml | grep -E '^-[^-]' | wc -l
```

  Expected: `0`.

- [ ] **Step 4.8: Verify.**

```bash
bash tests/test_run_compose.sh && bash tests/test_model_proxy_compose.sh && bash tests/test_build_network_compose.sh && bash tests/test_model_proxy_smoke.sh
PYTHONPATH=scripts python3 -m pytest tests/test_provider_env_passthrough.py -q
docker compose -f run-compose.yml --profile preview-helper config   # needs a scratch .archon/.env, as the tests make
```

  Expected (with Docker): every line `PASS:`. The new ones are
  `PASS: preview-helper source definition (nine keys, internal network only, two subpath mounts)`,
  `PASS: preview-helper renders exactly its nine environment keys under a hostile caller environment`
  and
  `PASS: preview-helper leaves every other render byte-identical; the proxy gains only preview-helper-net`.
  #634's own lines still pass, including `PASS: preview-helper-proxy leaves every other render byte-identical`.
  The pytest run passes. The manual render matches the spec's "Rendered" listing, with the
  same keys, networks, mounts and `internal: true`. Without Docker the render checks print
  `SKIP:` as today, and 3e/3f/3g still run. CI's `tests` job (Ubuntu runner, Docker Compose
  2.38.x) renders `volume.subpath`, which Compose has supported since 2.26.0. The spec records
  that this render was not run on 2.38.2 itself. If the runner rejects the key, 4e/4f fail
  loudly on the first CI run. The fallback is then to run those two blocks inside the built
  image in `docker-build`, never to weaken the mounts.

- [ ] **Step 4.9: Commit.**

```bash
git add run-compose.yml tests/test_run_compose.sh tests/test_provider_env_passthrough.py
git commit -m "feat(#642): the inert preview-helper compose service on an internal network with two subpath mounts" \
  -m "Nine environment keys, no credential, no env_file/DOCKER_HOST/depends_on/restart; preview-helper-net (internal) also joined by preview-helper-proxy; the state volume's root is not mounted. test_run_compose.sh 3e narrowed to 'no mount of the operator's registry directory', #634's 3f network list updated, and the compose-environment exemption extended to preview-helper (the spec's three existing tests this change breaks)."
```

---

## Task 5: The request-shape check in the image build

**Files:** `tests/tier2_preview_helper_cli.sh` (new, mode 100755), `.github/workflows/ci.yml`

- [ ] **Step 5.1: Write the check.** Create `tests/tier2_preview_helper_cli.sh`. It runs only
  inside the built image, with the baked `/opt/dark-factory/scripts` and the pinned `claude`.
  The capability suite's mock is driven in-process. The real `model_proxy --intent
  classify-preview-helper` and the real entry script run as bare child processes. Nothing
  under `capability_suite/` changes.

```bash
#!/usr/bin/env bash
# #642 request-shape check: the pinned CLI's request, through the real helper-proxy module, has
# the shape the local adapter accepts — tools == [], no output_config, no output_format, the
# served name, and the registry secret (never the placeholder) as the key. Runs in the
# docker-build CI job inside the built image with --network none (.github/workflows/ci.yml);
# it needs the baked /opt/dark-factory/scripts and the pinned claude, so it is not a test_*.sh.
# The capability suite's mock is the local endpoint (in-process), model_proxy --intent
# classify-preview-helper the proxy and preview_helper_entry.sh the helper (bare processes).
set -euo pipefail

getent hosts preview-helper-proxy | grep -q '^127\.0\.0\.1[[:space:]]' || {
  echo "FAIL: preview-helper-proxy does not resolve to 127.0.0.1 (docker run --add-host preview-helper-proxy:127.0.0.1)"; exit 1; }
[ -x /opt/dark-factory/scripts/preview_helper_entry.sh ] || {
  echo "FAIL: /opt/dark-factory/scripts/preview_helper_entry.sh is not baked executable"; exit 1; }
[ -w /var/lib/dark-factory ] || {
  echo "FAIL: /var/lib/dark-factory is not writable (docker run --tmpfs /var/lib/dark-factory:mode=1777)"; exit 1; }

export PYTHONPATH=/opt/dark-factory/scripts
exec timeout -k 5 240 python3 - <<'PY'
import asyncio
import json
import os
import socket
import sys
import time
from pathlib import Path

import yaml

from factory_core import preview_helper as ph
from factory_core.capability_suite import harness, mock_server
from factory_core.providers import model_endpoint as me

STATE = Path("/var/lib/dark-factory")
ENTRY = "/opt/dark-factory/scripts/preview_helper_entry.sh"
ENDPOINT_ID = "tier2-helper"
SECRET = "tier2-registry-secret-fixture"
PLACEHOLDER = "factory-local-placeholder"
RUN_ID = "0123456789abcdef0123456789abcdef"
PROXY_PORT = 8787


def fail(message):
    print(f"FAIL: {message}")
    sys.exit(1)


def stage_registry(base_url):
    """A staged registry with one tested local entry pointing at the mock; its digest."""
    root = STATE / "model-registry"
    entry = {
        "id": ENDPOINT_ID, "hosting_location": "local", "protocol": me.PROTOCOL,
        "accepts_payload_classes": ["local_only"],
        "model_identity": {"served_name": mock_server.SERVED_NAME,
                           "digest_or_revision": "sha256:" + "ef" * 32},
        "base_url": base_url, "secret_reference": "FACTORY_HELPER_MODEL_SECRET",
        "timeouts": {"connect_seconds": 10, "time_to_first_token_seconds": 60,
                     "idle_seconds": 60, "total_seconds": 120},
    }
    record = {
        "schema_version": "1.0", "endpoint_id": ENDPOINT_ID,
        "model_identity": dict(entry["model_identity"], quantisation="q4_k_m"),
        "runtime_stack": {"name": "capability-suite-mock", "version": "1.0.0",
                          "chat_template": "none", "context_settings": {}},
        "harness_version": harness.PINNED_HARNESS_VERSION,
        "fixture_suite_version": me.PINNED_FIXTURE_SUITE_VERSION,
        "results": {dim: "pass" for dim in me.ACTOR_REQUIRED_DIMENSIONS},
        "qualified_roles": ["actor"], "certified_by": "tier2-fixture",
        "certified_at": "2026-11-01T00:00:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00",
        "invalidated_by": ["model_digest", "harness_version", "fixture_suite_version",
                           "registry_config"],
        "registry_config": me.registry_config_sha256(entry),
    }
    (root / me.EVIDENCE_DIR).mkdir(parents=True)
    (root / me.REGISTRY_FILE).write_text(
        yaml.safe_dump({"schema_version": "1.0", "endpoints": [entry]}, sort_keys=False),
        encoding="utf-8")
    (root / me.EVIDENCE_DIR / f"{ENDPOINT_ID}.yaml").write_text(
        yaml.safe_dump({"capability_evidence": record}), encoding="utf-8")
    return me.tree_sha256(root)


async def wait_for_proxy(proc):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.returncode is not None:
            fail(f"the helper proxy exited {proc.returncode} before binding")
        try:
            socket.create_connection(("127.0.0.1", PROXY_PORT), timeout=0.5).close()
            return
        except OSError:
            await asyncio.sleep(0.2)
    fail("the helper proxy did not bind within 30 s")


async def main():
    if me.observed_harness_version() != harness.PINNED_HARNESS_VERSION:
        fail(f"claude is not the pinned {harness.PINNED_HARNESS_VERSION}")
    runner, base_url = await mock_server.start()
    try:
        observed = runner.app[mock_server.OBSERVED]
        digest = stage_registry(base_url)
        art = STATE / "preview-helper"
        art.mkdir()
        os.chmod(art, 0o777)
        (art / "input.json").write_text(json.dumps({
            "issue_number": 642, "head_sha": "1" * 40, "changed_files": ["docs/configuration.md"],
            "diff_stat": " docs/configuration.md | 2 +-", "title": "docs: tier2 fixture",
            "labels": ["documentation"]}), encoding="utf-8")
        ph.write_exclusive(art / "current-run.json", json.dumps({
            "run_id": RUN_ID, "issue_number": 642, "intent": ph.INTENT, "stage": ph.INTENT,
            "role": "actor"}))
        aliases = {alias: mock_server.SERVED_NAME for alias in me.TIER_ALIASES}
        base = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}
        proxy_env = {**base, "PYTHONPATH": "/opt/dark-factory/scripts",
                     "MODEL_PROXY_PORT": str(PROXY_PORT), "CURRENT_RUN_DIR": str(art),
                     "FACTORY_MODEL_ENDPOINT": ENDPOINT_ID, "FACTORY_MODEL_REGISTRY_SHA256": digest,
                     "ANTHROPIC_BASE_URL": base_url, **aliases,
                     "FACTORY_HELPER_MODEL_SECRET": SECRET, "SEQ_URL": "http://127.0.0.1:9"}
        # The helper gets exactly the compose service's nine keys, plus PATH and HOME.
        helper_env = {**base, "PYTHONPATH": "/opt/dark-factory/scripts",
                      "ANTHROPIC_BASE_URL": me.HELPER_PROXY_BASE_URL, **aliases,
                      "FACTORY_MODEL_ENDPOINT": ENDPOINT_ID,
                      "FACTORY_MODEL_REGISTRY_SHA256": digest, "CURRENT_RUN_DIR": str(art)}
        proxy = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "factory_core.model_proxy", "--intent", ph.INTENT, env=proxy_env)
        try:
            await wait_for_proxy(proxy)
            helper = await asyncio.create_subprocess_exec(
                ENTRY, "Classify preview (helper) #642", env=helper_env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(helper.communicate(), timeout=180)
        finally:
            proxy.terminate()
            await proxy.wait()
        print(out.decode(errors="replace"), err.decode(errors="replace"), sep="\n")
        if helper.returncode != 0:
            fail(f"the entry script exited {helper.returncode}")
        requests = list(observed.requests)
        # The adapter refuses a request carrying output_config/output_format (or any other
        # field a local destination does not support) with 400 before any outbound call, so
        # the mock never sees it; the ledger names the refusal (HEAD /api/hello is the pinned
        # CLI's own preflight, refused the same way and expected).
        ledger = STATE / "request-ledger.jsonl"
        rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()
                if line.strip()] if ledger.is_file() else []
        refused = [(r.get("method"), r.get("endpoint")) for r in rows
                   if r.get("run_id") == RUN_ID
                   and r.get("failure_class") == "protocol_incompatibility"
                   and (r.get("method"), r.get("endpoint")) != ("HEAD", "/api/hello")]
        if refused:
            fail(f"the adapter refused {refused} with protocol_incompatibility — the pinned CLI "
                 "sent a field a local destination does not accept (output_config/output_format?)")
        if not requests:
            fail("the endpoint saw no request")
        other = [(r["method"], r["path"]) for r in requests
                 if r["method"] != "POST" or r["path"].split("?", 1)[0] != "/v1/messages"]
        if other:
            fail(f"the endpoint saw requests other than POST /v1/messages: {other}")
        prompted = [r for r in requests if "<<<CHANGESET" in json.dumps(r["body"])]
        if not prompted:
            fail("no request carried the helper's prompt")
        for r in requests:
            body, headers = r["body"], r["headers"]
            for field in ("output_config", "output_format"):
                if field in body:
                    fail(f"a request carries {field}: {body[field]!r}")
            if body.get("model") != mock_server.SERVED_NAME:
                fail(f"a request names model {body.get('model')!r}, not the served name")
            if headers.get("x-api-key") != SECRET:
                fail("the endpoint did not receive the registry secret in x-api-key")
            if PLACEHOLDER in json.dumps(headers):
                fail("the placeholder credential reached the endpoint")
        for r in prompted:
            if r["body"].get("tools") != []:
                fail(f"the helper's request has tools={r['body'].get('tools')!r}, not []")
        print(f"PASS: {len(requests)} request(s): tools == [], no output_config or output_format, "
              f"model {mock_server.SERVED_NAME}, the registry secret in x-api-key")
        # The mock answers the text "ok", which is not the JSON object: a completed call that
        # the strict parse refuses, so result.json is abstained / schema_invalid.
        state, result = ph.read_result(art / "result.json")
        if (state, (result or {}).get("failure_class")) != ("abstained", "schema_invalid"):
            fail(f"result.json is {state} {result}, expected abstained schema_invalid")
        print("PASS: result.json written (abstained, schema_invalid: the mock's 'ok' is not the object)")
    finally:
        await runner.cleanup()


asyncio.run(main())
print("PASS: tier2_preview_helper_cli.sh")
PY
```

- [ ] **Step 5.2: Wire it into `docker-build` (D2).** In `.github/workflows/ci.yml`, append
  this step at the **end** of the `docker-build` job, directly after the upload step's
  `        with: { name: capability-tier2-result, path: /tmp/capability-tier2/result.json }`
  line. It goes after the Tier 2 step's artifact upload, not between the two:
  `tests/test_capability_suite.py::test_ci_runs_tier2_as_a_blocking_docker_build_step` requires
  the `actions/upload-artifact` step to follow the Tier 2 step directly (measured: inserting
  the new step between them fails that test with `KeyError: 'uses'`).

```yaml
      - name: Preview helper — pinned CLI request shape (hermetic)
        timeout-minutes: 5
        run: |
          docker run --rm --network none --add-host preview-helper-proxy:127.0.0.1 \
            --tmpfs /var/lib/dark-factory:mode=1777 -v "$PWD/tests:/work/tests:ro" -w /work \
            --entrypoint bash dark-factory:pr -c 'bash tests/tier2_preview_helper_cli.sh'
```

- [ ] **Step 5.3: Verify the guard and, where Docker is available, the check.**

```bash
chmod 755 tests/tier2_preview_helper_cli.sh
PYTHONPATH=scripts python3 -m pytest tests/test_ci_covers_shell_tests.py -q
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci.yml parses')"
bash -n tests/tier2_preview_helper_cli.sh && echo syntax-ok
if docker info >/dev/null 2>&1; then
  docker build -f Dockerfile -t dark-factory:pr . \
  && docker run --rm --network none --add-host preview-helper-proxy:127.0.0.1 \
       --tmpfs /var/lib/dark-factory:mode=1777 -v "$PWD/tests:/work/tests:ro" -w /work \
       --entrypoint bash dark-factory:pr -c 'bash tests/tier2_preview_helper_cli.sh'
else
  echo "SKIP: no Docker daemon here; the docker-build CI job runs this step"
fi
```

  Then run `PYTHONPATH=scripts python3 -m pytest tests/test_capability_suite.py -q -k test_ci_runs_tier2`.
  Expected: the guard passes, because it finds `bash tests/tier2_preview_helper_cli.sh` in a
  live `docker-build` step. The capability-suite CI-shape test passes too. Then `ci.yml parses` and `syntax-ok`. With Docker, the last lines
  are the two `PASS:` lines and `PASS: tier2_preview_helper_cli.sh`. In the factory's
  implement container (no Docker daemon) it prints `SKIP:`. Then the `docker-build` job is the
  first real run, and the PR must not merge before it is green. The spec's prototype used an
  equivalent stand-in endpoint, not the suite's mock (spec "Not verified"). If this step fails
  in CI on anything other than the request shape, fix the script, never the assertions. The
  "only POST /v1/messages reached the endpoint" assertion is about the adapter, not the CLI.
  The local adapter refuses every other path or method, `count_tokens` and `HEAD /api/hello`
  included, with `400 protocol_incompatibility` before any outbound call
  (`docs/dark-factory-model-proxy-rollout.md`, "Accepted endpoint"). Those requests appear in
  the ledger, never at the mock. Anything else arriving at the mock is a real finding about
  the proxy, not a script problem.

- [ ] **Step 5.4: Commit.**

```bash
git add tests/tier2_preview_helper_cli.sh .github/workflows/ci.yml
git ls-files --stage tests/tier2_preview_helper_cli.sh | cut -c1-6   # expect 100755
git commit -m "ci(#642): check the pinned CLI's request shape through the real helper proxy in the built image" \
  -m "tools == [], no output_config/output_format, the served name, the registry secret and never the placeholder. The step runs 'bash tests/tier2_preview_helper_cli.sh' from a /work mount so test_ci_covers_shell_tests.py counts the file as wired (plan D2). Nothing under capability_suite/ changes."
```

---

## Task 6: Documentation

**Files:** `docs/configuration.md`, `docs/dark-factory-model-proxy-rollout.md`

- [ ] **Step 6.1: `docs/configuration.md`.** In "Model endpoint registry (#557)", insert this
  paragraph directly **above** the paragraph that begins
  `**Back to default cloud:** remove `FACTORY_MODEL_ENDPOINT``, with one blank line on each
  side:

```
**The helper container (#642, inert).** `run-compose.yml`'s `preview-helper` service, under its
own `preview-helper` profile, is the container #587's dispatcher will start for each
`classify-preview-helper` dispatch. Nothing in this repository starts it, and nothing dispatches
it until #587: `entrypoint.sh` still answers that command string "not available". Its
`entrypoint:` is `scripts/preview_helper_entry.sh`, not `entrypoint.sh`: no tracker or code-host
preflight, no clone, no `git`, `gh` or `docker`. Its environment is exactly nine keys and holds
no credential of any kind: `PYTHONPATH`, `ANTHROPIC_BASE_URL` (`http://preview-helper-proxy:8787`),
the four `ANTHROPIC_DEFAULT_*_MODEL` aliases (interpolated from the scheduler-side
`PREVIEW_HELPER_ENDPOINT_SERVED_NAME`), `FACTORY_MODEL_ENDPOINT` (from `PREVIEW_HELPER_ENDPOINT`),
`FACTORY_MODEL_REGISTRY_SHA256` and `CURRENT_RUN_DIR=/var/lib/dark-factory/preview-helper`. It
is on the internal network `preview-helper-net` only, which `preview-helper-proxy` also joins. It
does not mount the state volume's root: two `volume.subpath` mounts give it the staged registry
(`model-registry/`, read-only) and its artifact directory (`preview-helper/`, read-write), and
both must exist before a start (Docker Engine 26 or later). The script runs the endpoint checks,
reads four fields of `input.json` (changed-file names, diff stat, title, labels), makes one
model call with no tools, and writes `result.json`, whose failure class is one of
`preflight_refused`, `missing_input`, `input_too_large`, `local_timeout_total`,
`model_call_failed` and `schema_invalid`. Its endpoint check is
`model-endpoints endpoint-preflight --intent <intent>` on the baked CLI: the run-side endpoint
check of `preflight` alone, with nothing skipped, no tracker or code-host variable needed, and
only a local entry passing. `scripts/factory_core/preview_helper.py` holds the deterministic
halves on both sides (prompt, answer, result, and the scheduler-side verbs #587 will call). The
`docker-build` CI job checks the pinned CLI's request shape through the real helper proxy with
`tests/tier2_preview_helper_cli.sh`.
```

- [ ] **Step 6.2: `docs/dark-factory-model-proxy-rollout.md`.** In #634's "The helper
  lane's proxy (#634)" paragraph, replace its last sentence

```
While the proxy runs, any container
on `dark-factory-net` can reach it by name; #587 adds the helper's internal network.
```

  (the line break may fall elsewhere: match the sentence, not the wrapping) with

```
While the proxy runs, any container
on `dark-factory-net` can reach it by name. It is also on `preview-helper-net` (#642), an
`internal: true` network whose only other member is the `preview-helper` container, which
has no other network and so no route out.
```

- [ ] **Step 6.3: Verify.**

```bash
python3 scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
python3 scripts/docs_refs_check.py docs/dark-factory-model-proxy-rollout.md
PYTHONPATH=scripts python3 -m pytest tests/test_docs_refs_check.py tests/test_factory_model_proxy.py -q -k "doc or rollout"
grep -c "#587 adds the helper's internal network" docs/dark-factory-model-proxy-rollout.md
```

  Expected: `Docs reference check passed for 4 doc(s).`, then
  `Docs reference check passed for 1 doc(s).` (every backticked path named, such as
  `scripts/preview_helper_entry.sh` and `tests/tier2_preview_helper_cli.sh`, exists by now).
  Then the pytest run passes, including
  `test_rollout_doc_lists_exactly_the_supported_and_refused_local_modes`. Then `0`.

- [ ] **Step 6.4: Commit.**

```bash
git add docs/configuration.md docs/dark-factory-model-proxy-rollout.md
git commit -m "docs(#642): document the inert preview-helper service, its nine keys and two mounts, and endpoint-preflight"
```

---

## Task 7: Full verification

**Files:** none

- [ ] **Step 7.1: Whole Python suite.**

```bash
PYTHONPATH=scripts python3 -m pytest tests/ -q
```

  Expected: all pass. The spec measured, on the #634 end state with the four product files
  applied, `4267 passed` plus the three breakages this plan fixes. After this plan, the count
  is that base plus every new test in Tasks 1 and 2, with no failure.

- [ ] **Step 7.2: The CI-wired shell tests this change touches, and the two DAG checks.**

```bash
for t in test_run_compose test_model_proxy_compose test_build_network_compose test_model_proxy_smoke test_provider_env_flags test_entrypoint_intent_case test_preview_helper_entry; do
  bash "tests/$t.sh" > /dev/null 2>&1 && echo "ok $t" || echo "FAIL $t"
done
python3 scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python3 scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```

  Expected: `ok` for each, then both DAG checks pass (no workflow is edited).
  `test_provider_env_flags.sh` reads the host environment. If it fails, re-run it on
  `origin/main` before blaming this change: #633's and #634's plans both record it failing in
  a sandbox on unmodified `main`.

- [ ] **Step 7.3: Blast radius.**

```bash
git fetch origin main
git diff --name-only origin/main HEAD \
  | python3 scripts/gate_blast_radius.py --changed-files-stdin \
      --lines-changed "$(git diff --shortstat origin/main HEAD | awk '{print $4 + $6}')" \
      --hotspots docs/codeindex-hotspots.md --config .claude/skills/refinement/config.yaml
```

  Expected: `STATUS: HUMAN_REQUIRED`, `TRIGGER: migration-seed`, triggered file
  `.github/workflows/ci.yml` only. It was measured on this plan's file list before
  implementation. No hotspot is edited. The implementation stops at that hold for signed human
  review.

- [ ] **Step 7.4: Scope check.**

```bash
git diff --name-only origin/main HEAD
git diff origin/main HEAD -- run-compose.yml | grep -E '^-[^-]' | wc -l
git diff --quiet origin/main HEAD -- scheduler.sh entrypoint.sh Dockerfile scripts/provider_env.sh \
  workflows config deploy .github/workflows/publish.yml scripts/factory_core/run_record.py \
  scripts/factory_core/model_proxy.py scripts/factory_core/providers/model_adapter.py \
  scripts/factory_core/providers/model_endpoint.py scripts/factory_core/capability_suite \
  && echo "nothing out of scope"
```

  Two-dot, not `origin/main...HEAD` (factory memory: the three-dot form reports files `main`
  changed on its own as false out-of-scope hits). Expected: exactly the thirteen files of the
  File Structure table, plus this plan and the spec under `docs/superpowers/` and the refine
  run's `.archon/memory/` entry. Then `0` removed lines in `run-compose.yml`, then
  `nothing out of scope`.

- [ ] **Step 7.5: Safety statements to carry into the PR body.** The PR body must say:
  - Merging activates nothing. No code path starts `preview-helper`. `entrypoint.sh` keeps
    #633's "not available" exit for `Classify preview (helper) #N`. No scheduler variable is
    set, and no `scheduler.sh`, workflow, `config/` or `Dockerfile` line changes.
  - The helper holds no credential. Its nine keys are pinned on the source and on a render
    under a hostile caller environment. Its only network is `internal: true`, and it never
    mounts the state volume's root.
  - The `CLAUDE_CODE_EFFORT_LEVEL=unset` suppression of `output_config` rests on a
    pinned-CLI behaviour (2.1.282). A CLI bump makes capability evidence `stale` (the script
    then refuses before any call), and the new `docker-build` step fails if a request ever
    carries `output_config` or `output_format`.
  - The blast-radius gate is expected to hold on `.github/workflows/ci.yml` for signed human
    approval.
  - Deviations D1–D6 of this plan, and the spec's "Not verified" list. In particular:
    `volume.subpath` on the runner's Compose; name resolution and isolation in a running
    stack, since no container is started from the compose file; and the first run of
    `tests/tier2_preview_helper_cli.sh` against the suite's mock.

---

## Interface delivered to #587

Unchanged from the spec, restated so the PR and #587 can cite one place:

- Service and profile `preview-helper`. Its three interpolated scheduler-side variables:
  `PREVIEW_HELPER_ENDPOINT` (becomes `FACTORY_MODEL_ENDPOINT`), `FACTORY_MODEL_REGISTRY_SHA256`
  and `PREVIEW_HELPER_ENDPOINT_SERVED_NAME` (becomes the four aliases). None is in
  `PROVIDER_PASSTHROUGH_VARS`.
- Two sub-directories of `scheduler_state` that must exist before a start: `model-registry/`
  (the staged registry, created by staging) and `preview-helper/`, writable by the image's
  uid 1000 (`preview_helper.py prepare-dir`).
- The entry script's contract. Input: `input.json` in `CURRENT_RUN_DIR`, the six fields
  `next-candidate` writes. Output: `result.json`, `schema_version` 1, `outcome` `ok` or
  `abstained`, `needs_preview`, `category`, `failure_class`. Its six failure classes:
  `preflight_refused`, `missing_input`, `input_too_large`, `local_timeout_total`,
  `model_call_failed`, `schema_invalid`. Exit status: 2 for a wrong argument, 0 whenever
  `result.json` was written, 3 only if it could not be. The helper never writes
  `current-run.json` and never chooses the run id.
- The module's verbs: `sweep`, `expired-keys`, `next-candidate`, `prepare-dir`, `clear`,
  `write-pointer`, `write-inflight`, `read-inflight`, `record-outcome` (with its required
  `--ledger-path`), with the exit codes in the spec's verb table.
- The CLI verb `model-endpoints endpoint-preflight --intent <intent>`.
- Temporary names. `sweep` removes only pending-pattern names and names starting `.tmp.`.
  `write_exclusive` uses `.<name>.tmp`, which `sweep` never touches. That is fine for the
  artifact directory and the in-flight file, where this ticket uses it. Whatever writes
  pending files (#587's changeset producer) must give its own temporary files a `.tmp.`
  prefix, or write them outside the pending directory, or they are never swept.

## Self-review

- `**Issue:** #642` sits directly under the title.
- No placeholders. The module and entry script come from the spec's verbatim listings by a
  mechanical extraction step pinned to a digest. Every edit after that is quoted old/new.
  Every test is written out, and every command has its expected output.
- Spec coverage. Requirement 1 (compose) → Task 4. 2 (entry script) → Task 3. 3 (verb) →
  Task 1. 4 (module) → Task 2. 5 (request-shape check) → Task 5. 6 (hermetic tests, 192.0.2.0/24
  only) → every task; the tier2 check uses a loopback mock inside `--network none`. 7 (no
  gate/breaker/budget/deploy change) → Task 7.4. Docs → Task 6.
- Every row of the spec's Test Plan has its named test. Compose rows: `test_run_compose.sh`
  3e/3g/4e/4f, each check labelled with the spec's name. `entrypoint.sh` guard:
  `test_entrypoint_intent_case.sh` section E, unchanged. Entry-script rows:
  `test_preview_helper_entry.sh` checks named `model_call_argv_ends_with_tools_empty`,
  `placeholder_and_effort_switch_exist_only_in_the_child`, `every_timeout_has_a_kill_after`,
  `unresolved_evidence_makes_no_model_call`, `a_hung_cli_ends_local_timeout_total`. Verb rows:
  `test_provider_cli.py`. Module rows: `test_preview_helper.py`, with the spec's names. Request
  shape: `tests/tier2_preview_helper_cli.sh`.
- The spec's existing tests this change breaks are all handled: check 3e (Step 4.1), #634's
  3f network list (Step 4.2), `test_forwarded_vars_not_also_in_compose_environment` (Step 4.3),
  and `test_ci_covers_shell_tests.py` (Steps 3.1 and 5.2, D2).
- Memory lessons applied: two-dot diffs for the scope check (Step 7.4); and the spec stays
  at `docs/superpowers/specs/`, cited by the entry script's comment (D3). Only this plan is a
  candidate for `docs/archive/`.
