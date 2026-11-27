# Scoped Local-Dispatch Refusal — Implementation Plan

**Issue:** #633

**Goal:** Keep `_NO_LOCAL_DISPATCH` exactly as it is on the scheduler side and for every existing
intent, and lift it on the run side only for `classify-preview-helper`. The intent comes from an
explicit `--intent` that `entrypoint.sh` derives once from its command. The same change closes
three gaps: the entrypoint's silent `fix` fallback, the local credential guard's blind spot for
alternate cloud-auth signals, and the empty-`invalidated_by` evidence loophole. It also adds a
baked-CLI fallback for `--hosting-location`. Nothing is activated: nothing dispatches either new
intent until #587.

**Architecture:** `entrypoint.sh` moves its argument parsing ahead of both pre-clone
provider-CLI calls and replaces the regex with an explicit `case`. Both calls get
`--intent "$INTENT"`. `providers/cli.py` threads the flag into `providers.preflight()` (run side
only) and `model_endpoint.load_verified_entry()`. `model_endpoint._local_problems()` appends
`_NO_LOCAL_DISPATCH` unless the intent is in `LOCAL_ELIGIBLE_INTENTS`. Every other local check
still runs. `scheduler_preflight()` and the model proxy's call site do not change.

**Tech Stack:** Python 3 (pytest), bash (`set -euo pipefail`), GitHub Actions CI.

**Spec:** `docs/superpowers/specs/2026-11-27-scoped-local-dispatch-refusal-design.md`
(approved at f6ad134).

**Gate-adjacent change.** This edits the check that refuses every local-routed run. It must not
touch any `gate_*` script, the breaker, budgets, `scheduler.sh`, `run-compose.yml`, `deploy/**`,
`.github/workflows/publish.yml`, `workflows/`, `bench/` or any tool allow/deny list. It touches
`.github/workflows/ci.yml` (two added lines only) and the hotspot
`scripts/factory_core/providers/__init__.py`, so the implementation is expected to stop at the
blast-radius hold for human review. That is the intended outcome, not a failure.

**How this plan was checked:** every code block below was applied to a scratch copy of
`origin/main` (1628a6e + this branch's spec commits) and run. Results: the full Python suite
passed (4008 tests). Every `tests/test_*.sh` that `ci.yml` wires passed, except
`test_provider_env_flags.sh` and `test_identity.sh`, which fail the same way on unmodified main
in that sandbox (host env vars). The new tests were also run against unmodified main: they
fail there as intended, except for the three marked "regression guard" below.

**Plan review (2026-11-27):** the plan was then executed literally, task by task, on
`origin/main` at a8cba16 (two merges later; every anchor below still matches). Each stated
red/green result was observed, except the two counts corrected in Steps 5.8 and 6.6. The full
Python suite went from 4124 passed on main to 4192 passed (4206 with the cases the review
added), and every `tests/test_*.sh` gave the
same result before and after, apart from the two new files. The review added the `NEAR_HELPER`
cases to Step 1.1, fixed the indentation of two snippets in Step 4.2 and one stale citation in
Step 8.4.

---

## File Structure

| File | Change | Task |
|---|---|---|
| `scripts/factory_core/providers/model_endpoint.py` | `LOCAL_ELIGIBLE_INTENTS`; `intent=None` on `_local_problems`/`preflight`/`load_verified_entry`; `LOCAL_CREDENTIAL_ENV_SIGNALS` guard; `INVALIDATED_BY_MINIMUM` | 1, 2, 3 |
| `scripts/factory_core/providers/__init__.py` | `preflight(side, intent=None)` → run-side `_endpoint_preflight` only | 4 |
| `scripts/factory_core/providers/cli.py` | optional `--intent` on `preflight` and `model-endpoints hosting` | 4 |
| `tests/test_provider_model_endpoint.py` | new #633 tests; `_clean_env` covers the new names; 5 existing tests updated (spec list) | 1–4 |
| `tests/test_provider_cli.py` | 3 existing stubs accept `intent=` (spec list) | 4 |
| `entrypoint.sh` | argument block moved and rewritten as an explicit `case`; usage text; `--intent` on both pre-clone calls; `_cli_with_hosting_fallback` and its two call sites | 5, 6 |
| `tests/test_entrypoint_intent_case.sh` | new | 5 |
| `tests/test_entrypoint_session_window.sh` | case M's two assertions accept `--intent plan` (spec list) | 5 |
| `tests/test_entrypoint_hosting_fallback.sh` | new | 6 |
| `.github/workflows/ci.yml` | exactly two added `- run:` steps | 7 |
| `docs/configuration.md`, `docs/local-inference-deployment-runbook.md`, `docs/run-lifecycle.md` | as spec §6 | 8 |

Run every command from the repo root. `PYTHONPATH=scripts` is what CI sets.

---

## Task 1: Intent gate in `model_endpoint.py`

**Files:** `scripts/factory_core/providers/model_endpoint.py`, `tests/test_provider_model_endpoint.py`

- [ ] **Step 1.1: Write the failing tests.** Append this block to the **end** of
  `tests/test_provider_model_endpoint.py`. It uses helpers defined earlier in the file
  (`_stage`, `_record`, `_local_env`, `pinned_suite`, `base_env`, `_select_staged`, `_hosting`),
  which are all module-level, so it must sit after them.

```python


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
```

- [ ] **Step 1.2: Verify they fail.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py -q -k "helper or every_intent or eligible or not_read_from"
```

  Expected: `test_local_eligible_intents_is_exactly_the_helper` fails with `AttributeError`.
  Every parametrisation of `test_local_entry_refused_for_every_intent_except_helper` (38) and
  `test_helper_intent_*` fails with `TypeError: ... unexpected keyword argument 'intent'`.
  `test_intent_not_read_from_environment_or_pointer` **passes** (regression guard, spec Test
  Plan). In total: `45 failed, 1 passed`.

  The `NEAR_HELPER` values pin the match as exact. Without them, a later change that
  normalised the intent before comparing it (strip, lower-case) would pass every test in this
  plan while `" classify-preview-helper"` lifted the refusal (checked by mutation in the plan
  review).

- [ ] **Step 1.3: Add the constant.** In `model_endpoint.py`, directly after the
  `_NO_LOCAL_DISPATCH = (...)` definition, add:

```python
# The only dispatch intents a local entry may serve on the run side (#633). Adding a name is a
# gate change with its own reviewed ticket; classify-preview-checker must never be added (a
# local endpoint never hosts a checker, contract §4.2). scheduler_preflight() ignores this.
LOCAL_ELIGIBLE_INTENTS = ("classify-preview-helper",)
```

- [ ] **Step 1.4: Gate the refusal on the intent.** In `_local_problems`, change the signature
  and the final append:

```python
def _local_problems(entry, files, registry_path, intent=None):
```

  and replace the last two lines of the function:

```python
            problems += role_problems(record, "actor")
    problems.append(_NO_LOCAL_DISPATCH.format(eid=eid))
    return problems
```

  with:

```python
            problems += role_problems(record, "actor")
    # Lifted only for a local-eligible intent, passed explicitly by the caller (#633). None,
    # "" or any other value keeps the refusal; every check above runs regardless.
    if intent not in LOCAL_ELIGIBLE_INTENTS:
        problems.append(_NO_LOCAL_DISPATCH.format(eid=eid))
    return problems
```

- [ ] **Step 1.5: Thread `intent` through `preflight()` and `load_verified_entry()`.**

```python
def preflight(registry_path, endpoint_id, intent=None) -> tuple[list[str], str | None]:
```

  and inside it, `problems += _local_problems(entry, files, registry_path)` becomes
  `problems += _local_problems(entry, files, registry_path, intent=intent)`.

```python
def load_verified_entry(registry_path, endpoint_id, intent=None) -> tuple[list[str], dict | None]:
```

  and inside it, `problems = problems + _local_problems(entry, files, registry_path)` becomes
  `problems = problems + _local_problems(entry, files, registry_path, intent=intent)`.

  Update the last sentence of `load_verified_entry`'s docstring from
  `_NO_LOCAL_DISPATCH is not filtered, so a local entry never comes back until #587 lifts it.`
  to:

```
    _NO_LOCAL_DISPATCH is not filtered: a local entry comes back only when intent is in
    LOCAL_ELIGIBLE_INTENTS (#633). The model proxy passes none, so it never gets one.
```

  Do **not** touch `scheduler_preflight()`.

- [ ] **Step 1.6: Verify they pass, and nothing else broke.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py tests/test_provider_cli.py tests/test_factory_model_proxy.py -q
```

  Expected: all pass. The existing `test_local_selection_always_fails_as_an_existing_phase`,
  `test_load_verified_entry_keeps_no_local_dispatch`, `test_scheduler_preflight_blocks_a_local_selection`
  and `test_hosting_keeps_the_no_local_dispatch_hard_block` still pass unchanged, because they
  pass no intent.

- [ ] **Step 1.7: Commit.**

```bash
git add scripts/factory_core/providers/model_endpoint.py tests/test_provider_model_endpoint.py
git commit -m "feat(#633): lift _NO_LOCAL_DISPATCH on the run side for classify-preview-helper only"
```

---

## Task 2: Local credential-env guard

**Files:** `scripts/factory_core/providers/model_endpoint.py`, `tests/test_provider_model_endpoint.py`

- [ ] **Step 2.1: Pin the names in the test module and clean them per test.** In
  `tests/test_provider_model_endpoint.py`, insert directly **above**
  `@pytest.fixture(autouse=True)` / `def _clean_env`:

```python
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
```

  and extend `_clean_env`'s first line so a developer or runner environment that happens to set
  one of these cannot fail an unrelated test:

```python
    for name in (*me.CLOUD_CREDENTIALS, *me.TIER_ALIASES, *GUARD_SIGNALS, *PLATFORM_IDENTITY_VARS,
                 *NON_DIVERTING_VARS, "ANTHROPIC_BASE_URL",
                 "LOCAL_GATEWAY_TOKEN", "REMOTE_GATEWAY_TOKEN", "FACTORY_MODEL_ENDPOINT",
                 "FACTORY_MODEL_REGISTRY_SHA256", "FACTORY_MODEL_REGISTRY_PATH"):
```

  (The `*me.` names are evaluated when the fixture runs, so this literal tuple never depends on
  the code under test.)

- [ ] **Step 2.2: Write the failing tests.** Append to the end of the file:

```python


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
```

- [ ] **Step 2.3: Verify they fail.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py -q -k "guard_signals or alternate_credential or platform_identity or non_diverting"
```

  Expected: the 13 `test_local_selection_fails_on_alternate_credential_signal` cases fail (no
  such problem is produced). The guard-tuple and non-diverting tests fail with
  `AttributeError: ... LOCAL_CREDENTIAL_ENV_SIGNALS`. The 7
  `test_platform_identity_variable_does_not_fail_local_selection` cases already pass, because
  Task 1 is in. They guard against a later over-wide list.

- [ ] **Step 2.4: Add the tuple.** In `model_endpoint.py`, directly after
  `LOCAL_ELIGIBLE_INTENTS`, add:

```python
# Alternate cloud-auth switches and credentials Claude Code honours without ANTHROPIC_BASE_URL
# (#594, #633). Mirrors, never imports, providers/model.py's _AWS_ALT_AUTH_SIGNALS /
# _GCP_ALT_AUTH_SIGNALS: credential-presence signals only, not the platform-identity ones
# (AWS_EXECUTION_ENV, K_SERVICE, GAE_APPLICATION, FUNCTION_TARGET, KUBERNETES_SERVICE_HOST).
# CLAUDE_CODE_USE_GATEWAY and AWS_BEARER_TOKEN_BEDROCK, each set alone, were measured with the
# pinned CLI 2.1.282 not to divert a request, so they are not listed.
LOCAL_CREDENTIAL_ENV_SIGNALS = (
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    "CLAUDE_CODE_USE_MANTLE", "CLAUDE_CODE_USE_ANTHROPIC_AWS",
    "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD",
    "AWS_PROFILE", "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_SESSION_TOKEN", "GOOGLE_CLOUD_PROJECT",
)
```

- [ ] **Step 2.5: Check it in `_local_problems`.** Directly after the existing
  `CLOUD_CREDENTIALS` block (the `if present: problems.append(... cloud model credential(s) ...)`),
  add:

```python
    signals = sorted(n for n in LOCAL_CREDENTIAL_ENV_SIGNALS if os.environ.get(n))
    if signals:
        problems.append(f"endpoint '{eid}' is local but an alternate cloud credential signal is set "
                        f"in this container: {', '.join(signals)} — a local-routed container "
                        "holds none (contract §4.3)")
```

  No warn-only mode, no `secret_reference` exemption. It runs for every intent.

- [ ] **Step 2.6: Verify.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py -q
```

  Expected: all pass.

- [ ] **Step 2.7: Commit.**

```bash
git add scripts/factory_core/providers/model_endpoint.py tests/test_provider_model_endpoint.py
git commit -m "feat(#633): fail a local selection closed on thirteen alternate cloud-auth signals" \
  -m "The check sits right after the CLOUD_CREDENTIALS check (the spec sketch places it after the evidence checks); only the order of the problem list differs, and no test depends on it."
```

---

## Task 3: Evidence `invalidated_by` minimum

**Files:** `scripts/factory_core/providers/model_endpoint.py`, `tests/test_provider_model_endpoint.py`

- [ ] **Step 3.1: Write the failing tests.** Append to the end of the file:

```python


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
```

- [ ] **Step 3.2: Verify they fail.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py -q -k "invalidated_by or below_minimum"
```

  Expected: the 5 `test_invalidated_by_below_minimum_is_declared` cases and
  `test_below_minimum_evidence_fails_the_helper_closed` fail (state is `tested`/`unknown`, not
  `declared`). `test_invalidated_by_exact_minimum_is_tested` and the 4
  `..._non_string_element_...` cases pass.

- [ ] **Step 3.3: Add the minimum.** In `model_endpoint.py`, directly after
  `LOCAL_CREDENTIAL_ENV_SIGNALS`, add:

```python
# invalidated_by must list at least these (#587 AC4, owner decision 2026-11-26);
# fixture_suite_version is compared whether listed or not.
INVALIDATED_BY_MINIMUM = ("model_digest", "harness_version", "registry_config")
```

  In `_evidence_problems`, replace the end of the function:

```python
    names = record["invalidated_by"]
    if not isinstance(names, list) or any(n not in FINGERPRINTS for n in names):
        problems.append(f"{where}: invalidated_by may only list {', '.join(FINGERPRINTS)}")
    return problems
```

  with:

```python
    names = record["invalidated_by"]
    if not isinstance(names, list) or any(n not in FINGERPRINTS for n in names):
        problems.append(f"{where}: invalidated_by may only list {', '.join(FINGERPRINTS)}")
    # List membership, not set(): reached only when every element is a known name, but a
    # set() of a malformed list would raise TypeError on an unhashable element.
    elif not all(m in names for m in INVALIDATED_BY_MINIMUM):
        problems.append(f"{where}: invalidated_by must include at least "
                        f"{', '.join(INVALIDATED_BY_MINIMUM)}")
    return problems
```

- [ ] **Step 3.4: Update the two existing tests the spec lists.** Both build a record that
  omits `harness_version` and `registry_config`, which now resolves `declared` before the
  fingerprint comparison. In `test_unresolvable_fingerprint_is_unknown`, replace
  `record = _record(LOCAL, invalidated_by=["model_digest", name])` with:

```python
    # #633: the three required names plus the one under test, so the record clears the
    # invalidated_by minimum and reaches the fingerprint comparison.
    record = _record(LOCAL, invalidated_by=["model_digest", "harness_version", "registry_config",
                                            name])
```

  In `test_unpinned_fixture_suite_is_unknown`, replace
  `state, reasons = me.evidence_state(_record(LOCAL, invalidated_by=["model_digest"]), LOCAL)` with:

```python
    record = _record(LOCAL, invalidated_by=["model_digest", "harness_version", "registry_config"])
    state, reasons = me.evidence_state(record, LOCAL)
```

  Both keep asserting `unknown`.

- [ ] **Step 3.5: Verify.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py tests/test_capability_suite.py tests/test_local_inference_deployment_runbook_doc.py -q
```

  Expected: all pass. `test_fixture_evidence_matches_fixture_entry_but_stays_unknown` is
  unaffected: `tests/fixtures/model_registry/capability-evidence/local-same-host.yaml` lists all
  eight fingerprints.

- [ ] **Step 3.6: Commit.**

```bash
git add scripts/factory_core/providers/model_endpoint.py tests/test_provider_model_endpoint.py
git commit -m "feat(#633): require model_digest, harness_version and registry_config in invalidated_by"
```

---

## Task 4: Thread `--intent` through the providers CLI

**Files:** `scripts/factory_core/providers/__init__.py`, `scripts/factory_core/providers/cli.py`,
`tests/test_provider_model_endpoint.py`, `tests/test_provider_cli.py`

- [ ] **Step 4.1: Write the failing CLI tests.** Append to the end of
  `tests/test_provider_model_endpoint.py`:

```python


def _providers_cli(monkeypatch, capsys, *argv):
    """Run `providers-cli <argv>`; returns (exit code, stdout, stderr)."""
    from factory_core.providers import cli
    monkeypatch.setattr(sys, "argv", ["providers-cli", *argv])
    code = 0
    try:
        cli.main()
    except SystemExit as e:
        code = e.code
    out = capsys.readouterr()
    return code, out.out, out.err


def test_hosting_cli_threads_intent(tmp_path, monkeypatch, capsys, pinned_suite):
    reg = _valid_local(tmp_path, monkeypatch)
    _select_staged(monkeypatch, reg, "local-test")
    assert _providers_cli(monkeypatch, capsys, "model-endpoints", "hosting", "--intent", HELPER) \
        == (0, "hosting=local\n", "")
    for extra in ([], ["--intent", ""], ["--intent", "fix"]):
        code, out, err = _providers_cli(monkeypatch, capsys, "model-endpoints", "hosting", *extra)
        assert (code, out) == (0, "hosting=cloud\n") and NO_LOCAL in err, extra


def test_preflight_cli_threads_intent(base_env, tmp_path, monkeypatch, capsys, pinned_suite):
    reg = _valid_local(tmp_path, monkeypatch)
    _select_staged(monkeypatch, reg, "local-test")
    code, out, err = _providers_cli(monkeypatch, capsys, "preflight", "--intent", HELPER)
    assert (code, out, err) == (0, "providers preflight: OK\n", "")
    code, out, err = _providers_cli(monkeypatch, capsys, "preflight")
    assert code == 1 and NO_LOCAL in err


def test_scheduler_side_ignores_intent(base_env, tmp_path, monkeypatch, capsys):
    from factory_core import providers
    monkeypatch.setattr(me, "SOURCE_REGISTRY_DIR", _write_registry(tmp_path / "src", [LOCAL]))
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "local-test")
    assert providers.preflight(side="scheduler", intent=HELPER) == [NO_LOCAL]
    code, out, err = _providers_cli(monkeypatch, capsys,
                                    "preflight", "--side", "scheduler", "--intent", HELPER)
    assert code == 1 and NO_LOCAL in err
```

- [ ] **Step 4.2: Update the existing stubs the spec lists.** The five two-line stub changes
  still pass on today's code. The rewritten
  `test_preflight_side_defaults_to_run_and_passes_scheduler_through` adds `--intent` argvs, so it
  fails on today's code (expected in Step 4.3) and passes after Step 4.5.

  The two fenced snippets below sit inside list items. Each is indented four columns beyond its
  fence, so with the list indentation removed it has exactly the file's indentation: the
  `lambda path, eid, intent=None` line starts at column 24, and the `monkeypatch.setattr(...)`,
  `for` and `assert` lines at column 4 (the test function's body).

  In `tests/test_provider_model_endpoint.py`:
  - `test_hosting_is_local_only_for_a_verified_local_entry`: the stub becomes
    `def verified_local(path, eid, intent=None):` with `calls.append((Path(path), eid, intent))`,
    and the assertion becomes `assert calls == [(tmp_path / "staged", "local-test", None)]`
    (`_hosting` passes no `--intent`).
  - `test_hosting_any_problem_from_the_verified_loader_is_cloud`: the lambda becomes
    ```python
                            lambda path, eid, intent=None: (["some problem"],
                                                            {"hosting_location": "local"}))
    ```
  - `test_hosting_exception_is_cloud_and_exits_zero`: `def boom(path, eid, intent=None):`.

  In `tests/test_provider_cli.py`:
  - `test_preflight_ok_prints_ok_and_exits_0`: `lambda side="run", intent=None: []`.
  - `test_preflight_failure_prints_every_problem_and_exits_1`:
    `lambda side="run", intent=None: [` (the list body unchanged).
  - `test_preflight_side_defaults_to_run_and_passes_scheduler_through`: replace the stub, loop
    and assertion with:
    ```python
        monkeypatch.setattr(cli_mod, "preflight",
                            lambda side="run", intent=None: seen.append((side, intent)) or [])
        for argv in (["cli.py", "preflight"], ["cli.py", "preflight", "--side", "scheduler"],
                     ["cli.py", "preflight", "--intent", ""],
                     ["cli.py", "preflight", "--intent", "classify-preview-helper"]):
            monkeypatch.setattr(sys, "argv", argv)
            cli_mod.main()
        assert seen == [("run", None), ("scheduler", None), ("run", None),
                        ("run", "classify-preview-helper")]
    ```

- [ ] **Step 4.3: Verify the new tests fail.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py tests/test_provider_cli.py -q -k "threads_intent or ignores_intent or preflight_side_defaults"
```

  Expected: all four fail (`test_preflight_side_defaults_to_run_and_passes_scheduler_through`
  included). argparse exits 2 on the unknown `--intent`, or `providers.preflight()` raises
  `TypeError` on `intent=`.

- [ ] **Step 4.4: `providers/__init__.py`.** Change `preflight` and `_endpoint_preflight`:

```python
def preflight(side: str = "run", intent: str | None = None) -> list[str]:
    """Return a list of human-readable problems; empty list == OK.

    side="scheduler" is scheduler.sh's boot check; the default is the run container's
    (entrypoint.sh). The two differ only when FACTORY_MODEL_ENDPOINT is set (#557).
    intent is the run's dispatch intent, from entrypoint.sh's --intent only (#633); it
    reaches the run-side endpoint check and is ignored on the scheduler side.
    """
```

  `problems += _endpoint_preflight(side, endpoint_id, model_name)` becomes
  `problems += _endpoint_preflight(side, endpoint_id, model_name, intent)`, and:

```python
def _endpoint_preflight(side: str, endpoint_id: str, model_name: str,
                        intent: str | None = None) -> list[str]:
```

  In its `else:` (run-side) branch only:

```python
        problems, hosting = model_endpoint.preflight(
            model_endpoint.staged_registry_dir(), endpoint_id, intent=intent)
```

  The `scheduler_preflight(...)` call stays exactly as it is.

- [ ] **Step 4.5: `providers/cli.py`.** In `_preflight`:

```python
    problems = preflight(side=args.side, intent=args.intent or None)
```

  In `_model_endpoints_hosting`, replace the comment block and the loader call:

```python
    # #586: entrypoint.sh resolves the run's hosting location once, before the clone, through
    # this baked copy. Built only on load_verified_entry(), which keeps _NO_LOCAL_DISPATCH
    # unless --intent names a local-eligible intent (#633), so without one every real registry
    # prints hosting=cloud. Always exits 0: any problem or exception prints hosting=cloud (the
    # caller fails closed on content, never on rc).
```

```python
            problems, entry = model_endpoint.load_verified_entry(
                model_endpoint.staged_registry_dir(), endpoint_id, intent=args.intent or None)
```

  In `main()`, after `pf.add_argument("--side", ...)`:

```python
    pf.add_argument("--intent", default="",
                    help="the run's dispatch intent, from entrypoint.sh (#633); run side only")
```

  and after `mhost = msub.add_parser("hosting")`:

```python
    mhost.add_argument("--intent", default="",
                       help="the run's dispatch intent, from entrypoint.sh (#633)")
```

  No other subcommand changes. `hosting-check` gets no `--intent`. The CLI reads the intent
  from nowhere but `args.intent`.

- [ ] **Step 4.6: Verify.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py tests/test_provider_cli.py tests/test_factory_model_proxy.py -q
```

  Expected: all pass. `test_default_path_never_loads_the_registry_module` stays green unchanged,
  so `model_endpoint` is still never imported with `FACTORY_MODEL_ENDPOINT` unset.

- [ ] **Step 4.7: Commit.**

```bash
git add scripts/factory_core/providers/__init__.py scripts/factory_core/providers/cli.py tests/test_provider_model_endpoint.py tests/test_provider_cli.py
git commit -m "feat(#633): thread an explicit --intent through providers preflight and model-endpoints hosting"
```

---

## Task 5: `entrypoint.sh` — explicit intent `case`, moved ahead of both provider calls

**Files:** `entrypoint.sh`, `tests/test_entrypoint_intent_case.sh` (new),
`tests/test_entrypoint_session_window.sh`

- [ ] **Step 5.1: Write the failing test.** Create `tests/test_entrypoint_intent_case.sh`. It
  sets `FACTORY_CLONE_DIR` to a scratch dir, so a run of the unmodified entrypoint, where an
  unrecognized command goes on as `fix`, can never `rm -rf` a real `/workspace/<repo>`. It
  exports `CURRENT_RUN_DIR` and `SCHEDULER_STATE_DIR` before any source, as
  `tests/test_run_record_hermetic.sh` requires. It sets `FACTORY_TRACKER` to an unknown name,
  so the source-time "In Progress" board move (`set_board_status`, which calls the baked
  providers CLI directly) fails before any network call, even inside the factory image where
  that CLI exists. Its child shells source `"$1/entrypoint.sh"`
  literally, so that guard's `source ... entrypoint.sh` line check finds them.

```bash
#!/usr/bin/env bash
# Verifies entrypoint.sh's explicit command → INTENT case (#633): every command string the
# scheduler issues keeps its intent; an unrecognized command, and the two #587 strings that
# are recognized but not yet available, exit non-zero before any clone, tracker or model call;
# nothing inherited from the environment changes the intent; and both pre-clone provider-CLI
# calls receive the derived intent as --intent.
# Run: bash tests/test_entrypoint_intent_case.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EP="$REPO_ROOT/entrypoint.sh"

WORK=$(mktemp -d /tmp/ep-intent-XXXXXX)
# #362: CURRENT_RUN_DIR is exported before entrypoint.sh is ever sourced
# (tests/test_run_record_hermetic.sh).
CURRENT_RUN_DIR="$WORK/run"
export CURRENT_RUN_DIR
SCHEDULER_STATE_DIR="$WORK/state"
export SCHEDULER_STATE_DIR
mkdir -p "$CURRENT_RUN_DIR" "$SCHEDULER_STATE_DIR" "$WORK/home"
export HOME="$WORK/home" FACTORY_CLONE_DIR="$WORK/clone"
export IDENTITY_SH="$REPO_ROOT/scripts/identity.sh"
export GH_TOKEN="stub-token" CLAUDE_CODE_OAUTH_TOKEN="stub-token"
export CALLS_LOG="$WORK/calls.log"
# Hermetic inside the factory image too: a sourced "Fix issue #N" moves the board through the
# baked providers CLI (set_board_status), which exists there. An unknown tracker makes that
# call fail before any network request.
export FACTORY_TRACKER="none-633"

# Every external a run could reach records its call. The providers CLI is a stub: it logs
# its argv and answers the hosting verb, so no registry, tracker or network is touched.
cat > "$WORK/providers_cli.py" <<'PY'
import os, sys
with open(os.environ["CALLS_LOG"], "a") as f:
    f.write("providers " + " ".join(sys.argv[1:]) + "\n")
if sys.argv[1:3] == ["model-endpoints", "hosting"]:
    print("hosting=cloud")
PY
export FACTORY_PROVIDERS_CLI="$WORK/providers_cli.py"
git() { echo "git $*" >> "$CALLS_LOG"; return 0; }
gh() { echo "gh $*" >> "$CALLS_LOG"; return 0; }
docker() { echo "docker $*" >> "$CALLS_LOG"; return 0; }
claude() { echo "claude $*" >> "$CALLS_LOG"; return 0; }
archon() { echo "archon $*" >> "$CALLS_LOG"; return 0; }
export -f git gh docker claude archon

PASSED=0; FAILED=0
assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else echo "  FAIL: $desc — expected='$expected' got='$actual'" >&2; FAILED=$((FAILED+1)); fi
}

# _source_intent <command> [NAME=VALUE ...]: sources entrypoint.sh with <command> as its only
# argument in a child shell (ENTRYPOINT_SOURCE_ONLY=1, so it returns before the clone) and
# prints the INTENT it derived. The NAME=VALUE pairs are placed in that child's environment.
# Sets SRC_RC; the sourced file's stderr is in $WORK/src.err.
_source_intent() {
  local cmd="$1"; shift
  : > "$CALLS_LOG"
  SRC_OUT=$(env "$@" ENTRYPOINT_SOURCE_ONLY=1 bash -c \
    'source "$1/entrypoint.sh" "$2" >/dev/null 2>"$3"; echo "$INTENT"' _ "$REPO_ROOT" "$cmd" "$WORK/src.err")
  SRC_RC=$?
}

# _run <command>: runs entrypoint.sh itself (not sourced) with <command>. Sets RUN_RC; stdout
# and stderr land in $WORK/run.out / $WORK/run.err.
_run() {
  : > "$CALLS_LOG"
  timeout 60 bash "$EP" "$@" >"$WORK/run.out" 2>"$WORK/run.err"
  RUN_RC=$?
}

# The derivation this change replaces (entrypoint.sh before #633), for the "same as before"
# comparison.
_old_intent() {
  local ARGUMENTS="$1" INTENT
  INTENT=$(echo "$ARGUMENTS" | grep -oiP '^\s*\K(fix|continue|close|refine|plan|deconflict|recheck)' | head -1 | tr '[:upper:]' '[:lower:]' || true)
  INTENT=${INTENT:-fix}
  case "$ARGUMENTS" in
    "Fix main"|"fix main") INTENT="fix-main" ;;
    "Audit deps"|"audit deps") INTENT="audit-deps" ;;
  esac
  echo "$INTENT"
}

echo "--- A: every scheduler-issued command string keeps its intent ---"
while IFS='|' read -r cmd expected; do
  _source_intent "$cmd"
  assert_eq "'$cmd' -> $expected" "$expected" "$SRC_OUT"
  assert_eq "'$cmd' -> same intent as before #633" "$(_old_intent "$cmd")" "$SRC_OUT"
  assert_eq "'$cmd': preflight receives --intent $expected" \
    "providers preflight --intent $expected" "$(sed -n 1p "$CALLS_LOG")"
  assert_eq "'$cmd': model-endpoints hosting receives --intent $expected" \
    "providers model-endpoints hosting --intent $expected" "$(sed -n 2p "$CALLS_LOG")"
done <<'EOF'
Fix issue #12|fix
Continue issue #12|continue
Continue|continue
Close issue #12|close
Refine issue #12|refine
Plan issue #12|plan
Deconflict issue #12|deconflict
Recheck main|recheck
Fix main|fix-main
Audit deps|audit-deps
fix main|fix-main
audit deps|audit-deps
fix issue #3|fix
PLAN ISSUE #3|plan
Fixup #3|fix
EOF

echo ""
echo "--- B: sourced with no argument (the test-harness case) stays fix ---"
: > "$CALLS_LOG"
# `set --` first: a bare `source` keeps the caller's positional parameters.
SRC_OUT=$(ENTRYPOINT_SOURCE_ONLY=1 bash -c \
  'root="$1"; set --; source "$root/entrypoint.sh" >/dev/null 2>&1; echo "$INTENT"' _ "$REPO_ROOT")
assert_eq "no argument, sourced -> fix" "fix" "$SRC_OUT"

echo ""
echo "--- C: unrecognized commands exit non-zero before any call ---"
while IFS= read -r cmd; do
  _run "$cmd"
  assert_eq "'$cmd' exits 1" "1" "$RUN_RC"
  assert_eq "'$cmd' makes no clone, tracker, provider or model call" "" "$(cat "$CALLS_LOG")"
  assert_eq "'$cmd' names itself on stderr" "1" "$(grep -c "^Unrecognized command: '$cmd'$" "$WORK/run.err")"
  assert_eq "'$cmd' prints the usage on stderr" "1" "$(grep -c '^Usage:' "$WORK/run.err")"
done <<'EOF'
Implement issue #3
Resolve issue #3
#3
 Fix issue #3
Audit Deps
Classify preview (helper) #
Classify preview (helper) #5 extra
Classify preview (helper) #5x
classify preview (helper) #5
CLASSIFY PREVIEW (HELPER) #5
Verify preview classification #
Verify preview classification #5 extra
verify preview classification #5
EOF
_source_intent "Implement issue #3"
assert_eq "sourced, unrecognized: exits 1 too" "1" "$SRC_RC"
assert_eq "sourced, unrecognized: no INTENT printed" "" "$SRC_OUT"

echo ""
echo "--- D: no argument, or an empty one, prints usage and exits 1 before any call ---"
_run
assert_eq "no argument exits 1" "1" "$RUN_RC"
assert_eq "no argument: usage on stdout" "1" "$(grep -c '^Usage:' "$WORK/run.out")"
assert_eq "no argument: no call" "" "$(cat "$CALLS_LOG")"
_run ""
assert_eq "empty argument exits 1" "1" "$RUN_RC"
assert_eq "empty argument: no call" "" "$(cat "$CALLS_LOG")"

echo ""
echo "--- E: the two #587 strings are recognized and not available ---"
for cmd in "Classify preview (helper) #5" "Verify preview classification #5"; do
  _run "$cmd"
  assert_eq "'$cmd' exits 1" "1" "$RUN_RC"
  assert_eq "'$cmd': 'not available' on stderr" "1" "$(grep -c 'not available' "$WORK/run.err")"
  assert_eq "'$cmd': no clone, tracker, provider or model call" "" "$(cat "$CALLS_LOG")"
done
_run "Classify preview (helper) #5"
assert_eq "the helper string names the helper intent" "1" \
  "$(grep -c "'classify-preview-helper'" "$WORK/run.err")"
_run "Verify preview classification #5"
assert_eq "the checker string names the checker intent" "1" \
  "$(grep -c "'classify-preview-checker'" "$WORK/run.err")"

echo ""
echo "--- F: nothing inherited from the environment changes the intent ---"
_source_intent "Plan issue #5" ARGUMENTS="Classify preview (helper) #5" \
  INTENT=classify-preview-helper INTENT_NOT_YET_AVAILABLE=0
assert_eq "inherited ARGUMENTS/INTENT are ignored" "plan" "$SRC_OUT"
assert_eq "inherited ARGUMENTS/INTENT: preflight still gets --intent plan" \
  "providers preflight --intent plan" "$(sed -n 1p "$CALLS_LOG")"
_source_intent "Plan issue #5" INTENT_NOT_YET_AVAILABLE=1
assert_eq "inherited INTENT_NOT_YET_AVAILABLE=1 is ignored" "plan" "$SRC_OUT"
_source_intent "Classify preview (helper) #5" INTENT_NOT_YET_AVAILABLE=0
assert_eq "inherited INTENT_NOT_YET_AVAILABLE=0 does not make the helper available" "1" "$SRC_RC"
assert_eq "inherited INTENT_NOT_YET_AVAILABLE=0: no call" "" "$(cat "$CALLS_LOG")"

echo ""
echo "--- G: an inherited nocasematch does not widen any match ---"
assert_eq "sanity: BASHOPTS=nocasematch reaches a child bash" "on" \
  "$(env BASHOPTS=nocasematch bash -c 'shopt -q nocasematch && echo on')"
_source_intent "FIX MAIN" BASHOPTS=nocasematch
assert_eq "nocasematch: 'FIX MAIN' is fix, not fix-main" "fix" "$SRC_OUT"
_source_intent "AUDIT DEPS" BASHOPTS=nocasematch
assert_eq "nocasematch: 'AUDIT DEPS' is unrecognized" "1" "$SRC_RC"
_source_intent "CLASSIFY PREVIEW (HELPER) #5" BASHOPTS=nocasematch
assert_eq "nocasematch: 'CLASSIFY PREVIEW (HELPER) #5' is unrecognized" "1" "$SRC_RC"
assert_eq "nocasematch: ... and names itself as unrecognized" "1" \
  "$(grep -c '^Unrecognized command:' "$WORK/src.err")"

rm -rf "$WORK"
echo ""
echo "Results: $PASSED passed, $FAILED failed"
[ "$FAILED" -eq 0 ]
```

  Notes on the test: `BASHOPTS` is read-only in the running shell, so it is only ever set
  through `env`. `FIX MAIN please` is not in list C, because it starts with `fix` and is `fix`
  both today and after the change.

- [ ] **Step 5.2: Verify it fails.**

```bash
bash tests/test_entrypoint_intent_case.sh 2>&1 | tail -3
```

  Expected: `Results: N passed, M failed` with M > 0, exit 1. Section A fails on every
  `--intent` assertion (today's calls carry no flag). C, E and F fail: today those strings run
  on as `fix` and make calls.

- [ ] **Step 5.3: Remove the old argument block.** In `entrypoint.sh`, delete the whole region
  from `# --- Parse arguments ---` through the `esac` that closes the
  `"Fix main"|"fix main"` / `"Audit deps"|"audit deps"` case (today lines 98–119: the
  `ARGUMENTS=` assignment, the six-line usage `if`, the `ISSUE_NUM=` grep, the regex `INTENT=`,
  `INTENT=${INTENT:-fix}` and the literal `case`). Leave the blank line before
  `# --- Canonical run identity and artifact directory ---`.

- [ ] **Step 5.4: Insert the new block.** Directly after `source "$IDENTITY_SH"` (line 6) and its
  blank line, before `# --- Validate required environment (provider-aware; parent spec §4) ---`,
  insert:

```bash
# --- Parse arguments: issue number and intent (no AI needed) ---
# Runs before the two provider-CLI calls below, which both receive the intent (#633). It is
# derived once, here, from the dispatch command alone: ARGUMENTS, INTENT and
# INTENT_NOT_YET_AVAILABLE are assigned before use, so an inherited value is never read.
_entrypoint_usage() {
  echo "Usage: docker compose --profile factory run --rm dark-factory \"<command>\""
  echo "Commands (# followed by the issue number):"
  echo "  \"Fix issue #3\"   \"Continue issue #3\"   \"Continue\"   \"Close issue #3\""
  echo "  \"Refine issue #3\"   \"Plan issue #3\"   \"Deconflict issue #3\""
  echo "  \"Recheck main\"   \"Fix main\"   \"Audit deps\""
  echo "Recognized but not available until #587:"
  echo "  \"Classify preview (helper) #3\"   \"Verify preview classification #3\""
}
ARGUMENTS="${*}"
if [ -z "$ARGUMENTS" ] && [ "${ENTRYPOINT_SOURCE_ONLY:-0}" != "1" ]; then
  _entrypoint_usage
  exit 1
fi
INTENT=""
INTENT_NOT_YET_AVAILABLE=0
# An inherited BASHOPTS or BASH_ENV can start this shell with nocasematch on, which would make
# every literal and regex below case-insensitive.
shopt -u nocasematch
case "$ARGUMENTS" in
  # Exact two-casing literals first: the generic word match below must not capture them.
  "Fix main"|"fix main") INTENT="fix-main" ;;
  "Audit deps"|"audit deps") INTENT="audit-deps" ;;
  *)
    # The two #587 command strings match as a whole string only — the fixed text, "#", one or
    # more digits, nothing after — case-sensitively, with the digit class written out.
    if [[ "$ARGUMENTS" =~ ^Classify\ preview\ \(helper\)\ \#[0123456789]+$ ]]; then
      INTENT="classify-preview-helper"; INTENT_NOT_YET_AVAILABLE=1
    elif [[ "$ARGUMENTS" =~ ^Verify\ preview\ classification\ \#[0123456789]+$ ]]; then
      INTENT="classify-preview-checker"; INTENT_NOT_YET_AVAILABLE=1
    else
      # Today's matching for every other command: a case-insensitive leading word, no
      # word boundary (so "Fixup #3" is still fix), no leading whitespace.
      shopt -s nocasematch
      case "$ARGUMENTS" in
        fix*)        INTENT="fix" ;;
        continue*)   INTENT="continue" ;;
        close*)      INTENT="close" ;;
        refine*)     INTENT="refine" ;;
        plan*)       INTENT="plan" ;;
        deconflict*) INTENT="deconflict" ;;
        recheck*)    INTENT="recheck" ;;
      esac
      shopt -u nocasematch
    fi
    ;;
esac
if [ -z "$INTENT" ]; then
  if [ -z "$ARGUMENTS" ] && [ "${ENTRYPOINT_SOURCE_ONLY:-0}" = "1" ]; then
    # Tests source this file with no arguments: keep today's value for that one case ("fix" is
    # never local-eligible). `exit` here would end the sourcing test's own shell.
    INTENT="fix"
  else
    # No fallback to fix (#633): an unrecognized command runs nothing.
    echo "Unrecognized command: '$ARGUMENTS'" >&2
    _entrypoint_usage >&2
    exit 1
  fi
fi
if [ "$INTENT_NOT_YET_AVAILABLE" = "1" ]; then
  echo "Intent '$INTENT' is recognized but not available until #587 lands; nothing was run." >&2
  exit 1
fi
# "Recheck main" carries no "#N" — the || true keeps the no-match grep (exit 1)
# from killing the script under set -euo pipefail.
ISSUE_NUM=$(echo "$ARGUMENTS" | grep -oP '#\K\d+' | head -1 || true)

```

  This keeps every property spec §1 calls normative. `ARGUMENTS` is assigned from `"${*}"`
  right before the block, and all three variables are assigned before use. `nocasematch` is
  switched off before the first match. The two literals keep their two casings. The #587
  strings match as whole strings only, with `[0123456789]`. The no-argument usage exit is
  unchanged: stdout, exit 1, skipped under `ENTRYPOINT_SOURCE_ONLY=1`. A non-empty
  unrecognized command writes its message and the usage to stderr and exits 1. There is no
  `fix` fallback.

- [ ] **Step 5.5: Pass `--intent` to both pre-clone calls.**

```bash
python3 "$FACTORY_PROVIDERS_CLI" preflight --intent "$INTENT"
```

```bash
DF_ENDPOINT_HOSTING=$(python3 "$FACTORY_PROVIDERS_CLI" model-endpoints hosting --intent "$INTENT" 2>/dev/null \
  | grep -o '^hosting=local$' || echo "hosting=cloud")
```

  (Only `--intent "$INTENT"` is added to each line. Both still run the baked
  `$FACTORY_PROVIDERS_CLI` before the clone.)

- [ ] **Step 5.6: Verify the new test passes.**

```bash
bash -n entrypoint.sh && bash tests/test_entrypoint_intent_case.sh 2>&1 | tail -1
```

  Expected: `Results: 138 passed, 0 failed`.

- [ ] **Step 5.7: Update case M of `tests/test_entrypoint_session_window.sh` (spec list).**
  Replace its two hosting assertions:

```bash
assert_eq "hosting resolved exactly once, through \$FACTORY_PROVIDERS_CLI" "1" \
  "$(grep -c '^model-endpoints hosting$' "$E2E_DIR_M/providers.log")"
assert_true "hosting resolved right after the provider check, before anything else" \
  "[ \"\$(sed -n 2p '$E2E_DIR_M/providers.log')\" = 'model-endpoints hosting' ]"
```

  with:

```bash
assert_eq "hosting resolved exactly once, through \$FACTORY_PROVIDERS_CLI, with the intent (#633)" "1" \
  "$(grep -c '^model-endpoints hosting --intent plan$' "$E2E_DIR_M/providers.log")"
assert_true "hosting resolved right after the provider check, before anything else" \
  "[ \"\$(sed -n 2p '$E2E_DIR_M/providers.log')\" = 'model-endpoints hosting --intent plan' ]"
```

- [ ] **Step 5.8: Verify every entrypoint shell test, plus the static guards.**

```bash
for t in tests/test_entrypoint_*.sh tests/test_run_record_hermetic.sh tests/test_cost_report_endpoint.sh tests/test_cost_report_harness_economics.sh; do
  [ "$t" = tests/test_entrypoint_fix_main.sh ] && continue   # CI-exempt; fails before and after
  bash "$t" >/tmp/out.log 2>&1 && echo "ok   $t" || { echo "FAIL $t"; tail -5 /tmp/out.log; }
done
PYTHONPATH=scripts python -m pytest tests/test_entrypoint_undefined_functions.py tests/test_side_effect.py tests/test_run_record.py -q
```

  Expected: every line `ok`, pytest all pass. `test_entrypoint_session_window.sh` reports
  `103 passed, 0 failed` (the same 103 assertions as before; two were edited, none added). `test_entrypoint_preflight.sh` still finds the preflight call before
  `git clone`. `test_entrypoint_current_run.sh` (which sources with `"Plan issue #208"` etc.)
  is unchanged. `test_run_record_hermetic.sh` passes for the new file.

- [ ] **Step 5.9: Commit.**

```bash
git add entrypoint.sh tests/test_entrypoint_intent_case.sh tests/test_entrypoint_session_window.sh
git commit -m "feat(#633): derive INTENT with an explicit case before both provider-CLI calls; no fix fallback"
```

---

## Task 6: `entrypoint.sh` — baked-CLI fallback for `--hosting-location`

**Files:** `entrypoint.sh`, `tests/test_entrypoint_hosting_fallback.sh` (new)

- [ ] **Step 6.1: Write the failing test.** Create `tests/test_entrypoint_hosting_fallback.sh`.
  The baked `/opt/dark-factory/scripts/factory_core/cli.py` does not exist on a CI runner, so a
  `python3` shell function routes exactly that path to this checkout's real `cli.py` and records
  it. No env override is added to `entrypoint.sh`.

```bash
#!/usr/bin/env bash
# Verifies _cli_with_hosting_fallback (#633): on a verified-local run, a clone cli.py that
# rejects --hosting-location (exit 2) is retried through the baked
# /opt/dark-factory/scripts/factory_core/cli.py with the identical argv, flag included — never
# without it. Any other exit status is returned as is. A cloud run never uses the helper, and
# its argv is unchanged.
# Run: bash tests/test_entrypoint_hosting_fallback.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export IDENTITY_SH="$REPO_ROOT/scripts/identity.sh"
export FACTORY_PROVIDERS_CLI="$REPO_ROOT/scripts/factory_core/providers/cli.py"
export GH_TOKEN="stub-token" CLAUDE_CODE_OAUTH_TOKEN="stub-token"

git() {
  case "$1" in
    status) [ "${STUB_DIRTY:-false}" = "true" ] && echo " M some/file.py" ;;
  esac
  return 0
}
gh() { echo "stub"; return 0; }
docker() { return 0; }
claude() { echo "stub"; return 0; }
export -f git gh docker claude

WORK=$(mktemp -d /tmp/ep-hf-XXXXXX)
# #362: both before the source line (tests/test_run_record_hermetic.sh).
CURRENT_RUN_DIR="$WORK/run"
export CURRENT_RUN_DIR
SCHEDULER_STATE_DIR="$WORK/state"
export SCHEDULER_STATE_DIR
export HOME="$WORK/home"
mkdir -p "$CURRENT_RUN_DIR" "$SCHEDULER_STATE_DIR" "$HOME"

ENTRYPOINT_SOURCE_ONLY=1 source "$REPO_ROOT/entrypoint.sh"
trap - ERR
set +e; set +u; set +o pipefail

# The clone's cli.py is a stub that records its argv. HF_CLONE_MODE: reject (exit 2 on
# --hosting-location, argparse's usage text on stderr), exit2 (exit 2 on any argv), fail
# (exit 1), accept (exit 0).
CLONE_DIR="$WORK/clone"
mkdir -p "$CLONE_DIR/dark-factory/scripts/factory_core"
export HF_ARGV_LOG="$WORK/argv.log"
cat > "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" <<'PY'
import os, sys
with open(os.environ["HF_ARGV_LOG"], "a") as f:
    f.write("clone " + " ".join(sys.argv[1:]) + "\n")
mode = os.environ.get("HF_CLONE_MODE", "reject")
if mode == "reject" and "--hosting-location" in sys.argv:
    print("usage: cli.py [-h] ...", file=sys.stderr)
    print("cli.py: error: unrecognized arguments: --hosting-location local", file=sys.stderr)
    sys.exit(2)
if mode == "exit2":
    sys.exit(2)
if mode == "fail":
    print("clone failure", file=sys.stderr)
    sys.exit(1)
print("clone ran")
PY
# The baked copy does not exist on a CI runner: route that one path to this checkout's real
# cli.py and record its argv.
python3() {
  if [ "${1:-}" = /opt/dark-factory/scripts/factory_core/cli.py ]; then
    shift
    echo "baked $*" >> "$HF_ARGV_LOG"
    command python3 "$REPO_ROOT/scripts/factory_core/cli.py" "$@"
    return
  fi
  command python3 "$@"
}

PASSED=0; FAILED=0
assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else echo "  FAIL: $desc — expected='$expected' got='$actual'" >&2; FAILED=$((FAILED+1)); fi
}
reset() { : > "$HF_ARGV_LOG"; rm -rf "$SCHEDULER_STATE_DIR"; mkdir -p "$SCHEDULER_STATE_DIR"; }

ISSUE_NUM=633
RUN_STARTED_AT=""   # elapsed and commits both 0: a deterministic argv
TEXT="$WORK/out.txt"
echo "HTTP 429 Too Many Requests" > "$TEXT"

echo "--- A: local, clone rejects the flag: error-signature-write falls back, flag intact ---"
reset; export HF_CLONE_MODE=reject; DF_ENDPOINT_HOSTING=local
_write_error_signature plan 1 "$TEXT"
SIG="$SCHEDULER_STATE_DIR/error-signatures/633.plan.sig"
assert_eq "signature written through the baked CLI" "substantive:endpoint_unreachable:1" \
  "$(command python3 -c "import json; print(json.load(open('$SIG'))['signature'])" 2>/dev/null)"
assert_eq "two attempts: the clone's, then the baked copy's" "clone baked" \
  "$(cut -d' ' -f1 "$HF_ARGV_LOG" | tr '\n' ' ' | sed 's/ $//')"
assert_eq "every attempt carries --hosting-location local" "2" \
  "$(grep -c -- '--hosting-location local$' "$HF_ARGV_LOG")"
assert_eq "both attempts have the identical argv" "1" \
  "$(cut -d' ' -f2- "$HF_ARGV_LOG" | sort -u | wc -l | tr -d ' ')"

echo ""
echo "--- B: local, clone rejects the flag: session-window-check falls back, no pause ---"
reset; DF_ENDPOINT_HOSTING=local SESSION_WINDOW_BACKOFF_ENABLED=true
TMP_OUT="$WORK/sw.out"
printf "You've hit your session limit · resets 11:10pm (UTC)\n" > "$TMP_OUT"
_handle_session_window_pause "$TMP_OUT" 2>"$WORK/sw.err"
assert_eq "returns 1: falls through to the failure path" "1" "$?"
assert_eq "no session-window-check failure warning" "0" \
  "$(grep -c 'session-window-check failed' "$WORK/sw.err")"
assert_eq "no sentinel written" "no" \
  "$([ -f "$SCHEDULER_STATE_DIR/session-window-paused" ] && echo yes || echo no)"
assert_eq "clone, then baked, both with the flag" "2" \
  "$(grep -c -- '--hosting-location local$' "$HF_ARGV_LOG")"
reset
OUT=$(_cli_with_hosting_fallback session-window-check --tmp-out "$TMP_OUT" \
  --state-dir "$SCHEDULER_STATE_DIR" --buffer-minutes 5 --fallback-minutes 30 \
  --hosting-location local)
assert_eq "helper returns the baked copy's status" "0" "$?"
assert_eq "helper prints the baked copy's output" "1" "$(grep -c '^matched=false' <<<"$OUT")"
assert_eq "the rejected attempt's usage text is dropped" "0" "$(grep -c 'unrecognized arguments' <<<"$OUT")"

echo ""
echo "--- C: local, clone fails for another reason: no fallback ---"
reset; export HF_CLONE_MODE=fail
OUT=$(_cli_with_hosting_fallback error-signature-write --hosting-location local)
assert_eq "exit 1 is returned as is" "1" "$?"
assert_eq "the clone's own output is printed" "clone failure" "$OUT"
assert_eq "only the clone's copy ran" "clone" "$(cut -d' ' -f1 "$HF_ARGV_LOG" | tr '\n' ' ' | sed 's/ $//')"

echo ""
echo "--- D: local, the clone has no cli.py at all: falls back (python3 exits 2) ---"
reset; mv "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" "$WORK/cli.py.away"
_write_error_signature plan 1 "$TEXT"
assert_eq "signature written through the baked CLI" "substantive:endpoint_unreachable:1" \
  "$(command python3 -c "import json; print(json.load(open('$SIG'))['signature'])" 2>/dev/null)"
assert_eq "baked copy ran with the flag" "1" "$(grep -c -- '^baked .*--hosting-location local$' "$HF_ARGV_LOG")"
mv "$WORK/cli.py.away" "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py"

echo ""
echo "--- E: cloud: the helper is not used and the argv is unchanged ---"
reset; export HF_CLONE_MODE=accept; DF_ENDPOINT_HOSTING=cloud
STUB_DIRTY=true _write_error_signature plan 1 "$TEXT"
assert_eq "error-signature-write: one call, the clone's, today's argv" \
  "clone error-signature-write --issue 633 --phase plan --exit-code 1 --text-file $TEXT --elapsed-seconds 0 --commits-since-start 0 --worktree-dirty --delivery-failure-max-seconds 30 --state-dir $SCHEDULER_STATE_DIR" \
  "$(cat "$HF_ARGV_LOG")"
reset
_handle_session_window_pause "$TMP_OUT" 2>/dev/null
assert_eq "session-window-check: one call, the clone's, today's argv" \
  "clone session-window-check --tmp-out $TMP_OUT --state-dir $SCHEDULER_STATE_DIR --buffer-minutes 5 --fallback-minutes 30" \
  "$(cat "$HF_ARGV_LOG")"
reset; export HF_CLONE_MODE=exit2
_write_error_signature plan 1 "$TEXT"
assert_eq "cloud never falls back, even if the clone's copy exits 2" "clone" \
  "$(cut -d' ' -f1 "$HF_ARGV_LOG" | tr '\n' ' ' | sed 's/ $//')"

rm -rf "$WORK"
echo ""
echo "Results: $PASSED passed, $FAILED failed"
[ "$FAILED" -eq 0 ]
```

- [ ] **Step 6.2: Verify it fails.**

```bash
bash tests/test_entrypoint_hosting_fallback.sh 2>&1 | tail -1
```

  Expected: `Results: 7 passed, 12 failed`. No fallback happens, and `_cli_with_hosting_fallback`
  is undefined, so its direct calls return 127.

- [ ] **Step 6.3: Add the helper.** In `entrypoint.sh`, directly above the comment block that
  starts `# Detects a session-window exhaustion in the captured run output via the Python`
  (the header of `_handle_session_window_pause`), insert:

```bash
# Verified-local runs only (#633). The clone's cli.py may predate --hosting-location and reject
# it with exit code 2 (an argparse unknown flag or subcommand, or a missing script — a real
# failure of either subcommand exits 1 or 3). Then the baked copy runs with the identical argv:
# never a retry without the flag. Only the output of the attempt whose status is returned is
# printed, stderr merged into stdout.
_cli_with_hosting_fallback() {
  local out rc=0
  # TARGET-PATH: the clone's copy first, as at every other cli.py call site.
  out=$(python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" "$@" 2>&1) || rc=$?
  if [ "$rc" -eq 2 ]; then
    rc=0
    out=$(python3 /opt/dark-factory/scripts/factory_core/cli.py "$@" 2>&1) || rc=$?
  fi
  printf '%s\n' "$out"
  return "$rc"
}

```

  `|| rc=$?`, never a bare `rc=$?` on the next line, because the file runs under `set -e` with an
  ERR trap.

- [ ] **Step 6.4: Use it in `_handle_session_window_pause`.** Replace the block from
  `local sw_result sw_rc` through `sw_rc=$?`:

```bash
  local sw_result sw_rc
  # #586: only a verified-local run passes the flag; the default argv is unchanged, so a
  # target's older cli.py copy never sees an argument it would reject. On local the CLI
  # writes no sentinel and prints matched=false, so this returns 1 below.
  local hosting_flag=()
  [ "${DF_ENDPOINT_HOSTING:-cloud}" = "local" ] && hosting_flag=(--hosting-location local)
  # TARGET-PATH: cli.py resolves under dark-factory/ in the clone — target's own copy
  # until P3 cleanup, baked self-contained fallback copy afterwards (df#14)
  sw_result=$(python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" session-window-check \
    --tmp-out "$tmp_out" \
    --state-dir "${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}" \
    --buffer-minutes "${SESSION_WINDOW_BUFFER_MINUTES:-5}" \
    --fallback-minutes "${SESSION_WINDOW_FALLBACK_MINUTES:-30}" \
    "${hosting_flag[@]}" 2>&1)
  sw_rc=$?
```

  with:

```bash
  local sw_result sw_rc=0
  local sw_args=(session-window-check
    --tmp-out "$tmp_out"
    --state-dir "${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}"
    --buffer-minutes "${SESSION_WINDOW_BUFFER_MINUTES:-5}"
    --fallback-minutes "${SESSION_WINDOW_FALLBACK_MINUTES:-30}")
  # #586: only a verified-local run passes the flag; the default argv is unchanged, so a
  # target's older cli.py copy never sees an argument it would reject. On local the CLI
  # writes no sentinel and prints matched=false, so this returns 1 below. #633: on local, a
  # clone cli.py that rejects the flag falls back to the baked copy, flag intact.
  if [ "${DF_ENDPOINT_HOSTING:-cloud}" = "local" ]; then
    sw_result=$(_cli_with_hosting_fallback "${sw_args[@]}" --hosting-location local) || sw_rc=$?
  else
    # TARGET-PATH: cli.py resolves under dark-factory/ in the clone — target's own copy
    # until P3 cleanup, baked self-contained fallback copy afterwards (df#14)
    sw_result=$(python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" "${sw_args[@]}" 2>&1) \
      || sw_rc=$?
  fi
```

  The cloud argv and its `2>&1` capture are byte-identical to today's. The `matched=` parsing
  below is unchanged.

- [ ] **Step 6.5: Use it in `_write_error_signature`.** Replace the block from
  `# #586: a verified-local run signs rate-limit/exhaustion output as a counted` through the
  function's closing `}`:

```bash
  # #586: a verified-local run signs rate-limit/exhaustion output as a counted
  # substantive:endpoint_unreachable failure; the flag is passed only on local (default argv
  # unchanged, as in _handle_session_window_pause).
  local hosting_flag=()
  [ "${DF_ENDPOINT_HOSTING:-cloud}" = "local" ] && hosting_flag=(--hosting-location local)
  # TARGET-PATH: cli.py resolves under dark-factory/ in the clone — target's own copy until
  # P3 cleanup, baked self-contained fallback copy afterwards (df#14)
  python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" error-signature-write \
    --issue "$ISSUE_NUM" \
    --phase "$phase" \
    --exit-code "$exit_code" \
    --text-file "${text_file:-}" \
    --elapsed-seconds "$elapsed_seconds" \
    --commits-since-start "$commits_since_start" \
    $dirty_flag $artifact_flag \
    --delivery-failure-max-seconds "${DELIVERY_FAILURE_MAX_SECONDS:-30}" \
    --state-dir "${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}" \
    "${hosting_flag[@]}" \
    2>/dev/null || true
}
```

  with:

```bash
  # $dirty_flag / $artifact_flag unquoted on purpose: an empty one adds no argument, as before.
  local es_args=(error-signature-write
    --issue "$ISSUE_NUM"
    --phase "$phase"
    --exit-code "$exit_code"
    --text-file "${text_file:-}"
    --elapsed-seconds "$elapsed_seconds"
    --commits-since-start "$commits_since_start"
    $dirty_flag $artifact_flag
    --delivery-failure-max-seconds "${DELIVERY_FAILURE_MAX_SECONDS:-30}"
    --state-dir "${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}")
  # #586: a verified-local run signs rate-limit/exhaustion output as a counted
  # substantive:endpoint_unreachable failure; the flag is passed only on local (default argv
  # unchanged, as in _handle_session_window_pause). #633: on local, a clone cli.py that
  # rejects the flag falls back to the baked copy, flag intact; its output is discarded.
  if [ "${DF_ENDPOINT_HOSTING:-cloud}" = "local" ]; then
    _cli_with_hosting_fallback "${es_args[@]}" --hosting-location local >/dev/null 2>&1 || true
  else
    # TARGET-PATH: cli.py resolves under dark-factory/ in the clone — target's own copy until
    # P3 cleanup, baked self-contained fallback copy afterwards (df#14)
    python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" "${es_args[@]}" \
      2>/dev/null || true
  fi
}
```

  On the local path the helper merges stderr into its stdout, so that output is discarded
  (spec §5). The cloud path keeps today's `2>/dev/null || true`.

- [ ] **Step 6.6: Verify.**

```bash
bash -n entrypoint.sh && bash tests/test_entrypoint_hosting_fallback.sh 2>&1 | tail -1
bash tests/test_entrypoint_session_window.sh 2>&1 | tail -1
bash tests/test_entrypoint_error_signature.sh 2>&1 | tail -1
bash tests/test_run_record_hermetic.sh 2>&1 | tail -1
PYTHONPATH=scripts python -m pytest tests/test_entrypoint_undefined_functions.py tests/test_factory_core_error_signature.py tests/test_factory_core_session_window.py -q
```

  Expected: `Results: 19 passed, 0 failed`; `103 passed, 0 failed`; the error-signature test
  passes; `OK`; pytest all pass.

- [ ] **Step 6.7: Commit.**

```bash
git add entrypoint.sh tests/test_entrypoint_hosting_fallback.sh
git commit -m "feat(#633): fall back to the baked cli.py when the clone's rejects --hosting-location"
```

---

## Task 7: Wire the two new shell tests into CI

**Files:** `.github/workflows/ci.yml`

- [ ] **Step 7.1: Verify the guard fails.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_ci_covers_shell_tests.py -q
```

  Expected: `test_every_shell_test_is_wired_or_exempt` fails and names
  `test_entrypoint_intent_case.sh` and `test_entrypoint_hosting_fallback.sh`.

- [ ] **Step 7.2: Add exactly two steps.** Directly after
  `      - run: bash tests/test_entrypoint_conflict_escalate.sh` and before the
  `- name: Assert none of the entrypoint tests touched /var/lib/dark-factory` step, so that
  assertion also covers the two new tests, insert:

```yaml
      - run: bash tests/test_entrypoint_intent_case.sh
      - run: bash tests/test_entrypoint_hosting_fallback.sh
```

  Nothing else in the file changes.

- [ ] **Step 7.3: Verify.**

```bash
PYTHONPATH=scripts python -m pytest tests/test_ci_covers_shell_tests.py -q
git diff --stat .github/workflows/ci.yml
```

  Expected: pass; `1 file changed, 2 insertions(+)`.

- [ ] **Step 7.4: Commit.**

```bash
git add .github/workflows/ci.yml
git commit -m "ci(#633): run the intent-case and hosting-fallback entrypoint tests"
```

---

## Task 8: Documentation (spec §6)

**Files:** `docs/configuration.md`, `docs/local-inference-deployment-runbook.md`, `docs/run-lifecycle.md`

No docs test pins the replaced sentences (`tests/test_local_inference_deployment_runbook_doc.py`
checks only the YAML example; `tests/test_docs_conformance_reference_docs.py` checks unrelated
tokens). `scripts/docs_refs_check.py` checks that every cited `path:N` resolves.

- [ ] **Step 8.1: `docs/configuration.md`, "Model endpoint registry (#557)".** Replace the
  sentence ending the staged-copy paragraph:

```
A local selection also fails if any
cloud model credential is set in the container, if `ANTHROPIC_BASE_URL` or any of the four
`ANTHROPIC_DEFAULT_*_MODEL` aliases does not match the entry, if its capability evidence is
not `tested`, and in every case until #587 ships a local-eligible dispatch.
```

  with:

```
A local selection also fails if any
cloud model credential or alternate cloud-auth signal (below) is set in the container, if
`ANTHROPIC_BASE_URL` or any of the four `ANTHROPIC_DEFAULT_*_MODEL` aliases does not match the
entry, or if its capability evidence is not `tested`. It also fails for every dispatch intent
except `classify-preview-helper` (`LOCAL_ELIGIBLE_INTENTS`, #633). `entrypoint.sh` derives the
intent from its command and passes it to the baked CLI's `preflight` and
`model-endpoints hosting` as `--intent`; no environment variable, `current-run.json` field or
registry content can supply it. The scheduler side (`preflight --side scheduler`,
`model-endpoints hosting-check`) still refuses every local selection, and nothing dispatches
the helper intent until #587.
```

  Replace the whole `Known gap, a precondition of #587 (#594): ...` paragraph (through
  `` `fixture_suite_version` is compared.``) with:

```
Alternate cloud-auth guard (#594, closed by #633): besides the static credentials in
`CLOUD_CREDENTIALS`, a local selection fails, with no warn-only mode, when any name in
`LOCAL_CREDENTIAL_ENV_SIGNALS` (`scripts/factory_core/providers/model_endpoint.py`) is set:
`CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`, `CLAUDE_CODE_USE_FOUNDRY`,
`CLAUDE_CODE_USE_MANTLE`, `CLAUDE_CODE_USE_ANTHROPIC_AWS`,
`CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD`, `AWS_PROFILE`, `AWS_ROLE_ARN`,
`AWS_WEB_IDENTITY_TOKEN_FILE`, `AWS_CONTAINER_CREDENTIALS_RELATIVE_URI`,
`AWS_CONTAINER_CREDENTIALS_FULL_URI`, `AWS_SESSION_TOKEN`, `GOOGLE_CLOUD_PROJECT`. Each is a way
Claude Code reaches a cloud model without `ANTHROPIC_BASE_URL`. The tuple mirrors, and never
imports, `_AWS_ALT_AUTH_SIGNALS` / `_GCP_ALT_AUTH_SIGNALS` in
`scripts/factory_core/providers/model.py`. It leaves out the platform-identity variables
(`AWS_EXECUTION_ENV`, `K_SERVICE`, `GAE_APPLICATION`, `FUNCTION_TARGET`,
`KUBERNETES_SERVICE_HOST`), which name where a container runs, not a credential. It also leaves
out `CLAUDE_CODE_USE_GATEWAY` and `AWS_BEARER_TOKEN_BEDROCK`, which the pinned CLI 2.1.282 was
measured not to divert when either is set alone. A capability-evidence record's
`invalidated_by` must list at least `model_digest`, `harness_version` and `registry_config`. A
record below that minimum, an empty list included, resolves `declared`. List only fingerprints
`evidence_state()` can resolve (those three and `fixture_suite_version`); any other name
resolves `unknown`. `fixture_suite_version` is compared whether listed or not.
```

  In the `model-endpoints hosting` paragraph, replace:

```
It reads the same
staged copy and digest as `model-endpoints show`, so it stays `cloud` for every entry until
#587 (#586; [run-lifecycle.md](run-lifecycle.md) section 6.F).
```

  with:

```
It reads the same
staged copy and digest as `model-endpoints show` and passes the run's `--intent` to
`load_verified_entry()`. So it prints `hosting=local` only for a verified local entry and the
`classify-preview-helper` intent, which nothing dispatches until #587 (#586, #633;
[run-lifecycle.md](run-lifecycle.md) section 6.F).
```

- [ ] **Step 8.2: `docs/local-inference-deployment-runbook.md`.** Replace:

```
It routes nothing either. Every local registry entry fails preflight today: `_NO_LOCAL_DISPATCH`
in `scripts/factory_core/providers/model_endpoint.py` rejects any `hosting_location: local`
selection until #587 ships.
```

  with:

```
It routes nothing either. `_NO_LOCAL_DISPATCH` in
`scripts/factory_core/providers/model_endpoint.py` rejects every `hosting_location: local`
selection on the scheduler side. On the run side it rejects every dispatch intent except
`classify-preview-helper` (#633), and nothing dispatches that intent until #587 ships. So every
local registry entry fails preflight today.
```

  In `## Caveats`, replace the two bullets:

```
- Selecting a local endpoint is refused everywhere until #587 (`_NO_LOCAL_DISPATCH`).
- The run container's cloud-credential guard does not yet cover Bedrock/Vertex switches or AWS/GCP
  alternate credentials (#594). That is a precondition of #587, not part of this runbook.
```

  with:

```
- Selecting a local endpoint is refused by the scheduler, and by the run container for every
  intent except `classify-preview-helper` (#633), which nothing dispatches until #587
  (`_NO_LOCAL_DISPATCH`).
- The run container's cloud-credential guard also covers the Bedrock, Vertex, Foundry and Mantle
  switches and AWS/GCP alternate credentials (#594, closed by #633; the full list is in
  [configuration.md](configuration.md) "Model endpoint registry"). A capability-evidence record
  must list `model_digest`, `harness_version` and `registry_config` in `invalidated_by`, and only
  names `evidence_state()` can resolve (those three and `fixture_suite_version`). Otherwise it is
  not `tested`.
```

- [ ] **Step 8.3: `docs/run-lifecycle.md` section 1 ("Intents").** Get the new line numbers:

```bash
grep -n '^ARGUMENTS=\|^INTENT=""\|^if \[ "\$INTENT_NOT_YET_AVAILABLE" = "1" \]\|^ISSUE_NUM=' entrypoint.sh
```

  Reference values: `ARGUMENTS=` 21 (the usage exit is 21-25), `INTENT=""` 26, the
  not-available `if` 71 (its `fi` is 74), `ISSUE_NUM=` 77. Use what the grep prints if it differs. Then:
  - `with no argument the entrypoint prints usage and exits 1 (`` `entrypoint.sh:99-108` ``).`
    → `` (`entrypoint.sh:21-25`) ``.
  - Replace item 1 (`1. **Entrypoint intent.** ... become `` `audit-deps` ``.`) with:

```
1. **Entrypoint intent.** An explicit `case` (`entrypoint.sh:26-74`) runs right after the
   identity is sourced and before any provider-CLI call (#633). The exact strings `Fix main` /
   `fix main` become `fix-main`, and `Audit deps` / `audit deps` become `audit-deps`.
   `Classify preview (helper) #N` and `Verify preview classification #N`, matched as whole
   strings and case-sensitively, become `classify-preview-helper` and
   `classify-preview-checker`; both exit 1 with "not available" until #587. Any other command
   is matched case-insensitively by its leading word against
   `fix|continue|close|refine|plan|deconflict|recheck`. No match exits 1 with the usage text on
   stderr, before any clone, tracker or model call; there is no fallback to `fix`. The first
   `#<digits>` is the issue number (`:77`). The intent goes to the provider preflight and the
   hosting resolution as `--intent` ([section 2](#2-before-the-workflow-entrypointsh)).
```

  - Append two rows to the end of the command table (after the `Audit deps` row):

```
| `Classify preview (helper) #N` (not issued yet; #587) | `classify-preview-helper` | never reached | Exits 1, "not available", until #587 |
| `Verify preview classification #N` (not issued yet; #587) | `classify-preview-checker` | never reached | Exits 1, "not available", until #587 |
```

- [ ] **Step 8.4: `docs/run-lifecycle.md` section 2 table.** Argument parsing now comes before the
  provider preflight. Replace rows `1` through `4` with:

```
| 1 | Source instance identity (`scripts/identity.sh`) | 5-6 | Provides repo slug, clone dir, board constants |
| 2 | Parse arguments into `INTENT` and `ISSUE_NUM` | 8-77 | Explicit `case` ([section 1](#1-intents)). No argument prints usage; an unrecognized command or a not-yet-available intent exits 1. All of these happen before any provider, tracker, clone or model call (#633) |
| 3 | Provider preflight (`providers/cli.py preflight --intent "$INTENT"`) | 79-81 | Validates required environment. When `FACTORY_MODEL_ENDPOINT` is set it also checks the staged model endpoint registry against `FACTORY_MODEL_REGISTRY_SHA256` and the selected entry (#557; [configuration.md](configuration.md) "Model endpoint registry"). A local entry passes only for `classify-preview-helper` (#633). A failure here ends the run before any ticket signal exists |
| 3a | Resolve the model-endpoint hosting location (`providers/cli.py model-endpoints hosting --intent "$INTENT"`, the baked copy) | 83-92 | Sets `DF_ENDPOINT_HOSTING` to `local` only when the resolver prints exactly `hosting=local`; anything else is `cloud`. Built on `load_verified_entry`, so it is `local` only for a verified local entry and `classify-preview-helper`, which nothing dispatches until #587 (#586, #633, [section 6.F](#f-rate-limit-and-session-window-handling-entrypoint)) |
| 4 | Git identity (`<Product> Factory`, `factory@<repo>`) | 105-107 | |
```

  Re-cite the `Lines` cell of every later row (5–26) from its anchor:

```bash
grep -n '^# --- Canonical run identity\|^mkdir -p "\$ARTIFACTS_DIR"\|^# --- Ledger-writability\|^_check_ledger_writable || true\|^# --- Model-proxy correlation\|current-run.json" 2>/dev/null\|^# --- Concurrency guard\|^# --- Move to "In Progress"\|^trap on_failure ERR\|^gh auth setup-git\|^# --- Clone the repo\|^cd "\$CLONE_DIR"$\|^_adapter_invalid_refuse$\|^# --- Self-contained fallbacks\|^# --- Effective config\|^export FACTORY_BLAST_HOLD_FILE\|^apply_run_config_env\|^_compute_side_effect_level\|^# --- Copy preview template\|^_exclude_in_clone "dark-factory/seed/"\|^# --- Install target project deps\|^# --- Write factory-scoped Claude\|^_exclude_in_clone ".claude/settings.local.json"\|^pre-commit install\|^if \[ "\$INTENT" = "fix-main" \]\|^# --- Dependency-health loop\|^if \[ "\$INTENT" = "audit-deps" \]\|^# --- Smoke gate\|^if \[ "\$INTENT" = "recheck" \]\|^if \[ "\$INTENT" = "deconflict" \]\|^# --- Run via Archon workflow\|^done$\|^_adapter_check_applies()' entrypoint.sh
```

  Reference values from the checked implementation (use the grep output if it differs):

| Row | Lines |
|---|---|
| 5 | 169-179 |
| 6 | 181-203 |
| 7 | 205-242 |
| 8 | 244-259 |
| 9 | 272-276 |
| 10 | 811 |
| 11 | 934 |
| 12 | 936-942 |
| 13 | 945 |
| 14 | 947-973 |
| 15 | 975-988 |
| 16 | 992 (`apply_run_config_env`, `scripts/run_config_env.sh`) |
| 17 | 996 (`_compute_side_effect_level`, 142-167) |
| 18 | 998-1008 |
| 19 | 1010-1027 |
| 20 | 1029-1046 |
| 21 | 1049 |
| 22 | 1051-1059 |
| 22a | 1061-1075 |
| 23 | 1077-1084 |
| 24 | 1086-1093 |
| 25 | 1096-1195 |
| 26 | 1197-1265 |

  In the paragraph under the table, `` gate (`entrypoint.sh:759-764`) `` becomes
  `` (`entrypoint.sh:831-836`) `` (`_adapter_check_applies`). Change the `Lines` cells only,
  with one exception: in row 11's Notes, `` (reason at `:24-28`) `` becomes
  `` (reason at `:95-99`) ``. That is the "Plain URL on purpose" comment, which moves with
  everything else; after this change lines 24-28 are the usage function. No other row text
  changes. Citations in sections 3 and later are out of scope (spec §6 names sections 1 and
  2).

- [ ] **Step 8.5: Verify.**

```bash
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
PYTHONPATH=scripts python -m pytest tests/test_docs_conformance_reference_docs.py tests/test_local_inference_deployment_runbook_doc.py tests/test_docs_refs_check.py tests/test_implement_doc_impact.py -q
grep -n 'refused everywhere\|No match means `fix`\|Known gap, a precondition of #587' docs/configuration.md docs/local-inference-deployment-runbook.md docs/run-lifecycle.md
```

  Expected: `Docs reference check passed for 4 doc(s).`; pytest all pass; the final grep prints
  nothing.

- [ ] **Step 8.6: Commit.**

```bash
git add docs/configuration.md docs/local-inference-deployment-runbook.md docs/run-lifecycle.md
git commit -m "docs(#633): local refusal scoped to the helper intent; guard and evidence minimum closed"
```

---

## Task 9: Full verification

- [ ] **Step 9.1: Whole Python suite.**

```bash
PYTHONPATH=scripts python -m pytest tests/ -q
```

  Expected: all pass (about 4008 tests at the time of planning; 4206 on `origin/main` at
  a8cba16 with this plan applied).

- [ ] **Step 9.2: Every CI-wired shell test, and the two DAG checks.**

```bash
for t in $(grep -oE 'bash tests/test_[A-Za-z0-9_]+\.sh' .github/workflows/ci.yml | awk '{print $2}'); do
  bash "$t" >/tmp/out.log 2>&1 && echo "ok   $t" || { echo "FAIL $t"; tail -3 /tmp/out.log; }
done
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```

  Expected: every line `ok`. If `test_identity.sh` or `test_provider_env_flags.sh` fail, run
  them on a clean `origin/main` checkout too. In the planning sandbox both failed the same way
  on unmodified main, because the host environment set board and Jira variables. Record that in
  the implementation notes rather than "fixing" either test. Neither file is touched by this
  plan.

- [ ] **Step 9.3: Scope check.**

```bash
git diff --stat origin/main HEAD -- . ':!docs/superpowers' ':!.archon/memory'
```

  Expected: exactly the 13 files in the File Structure table. No `gate_*`, `scheduler.sh`,
  `run-compose.yml`, `deploy/`, `workflows/`, `bench/` or `publish.yml`.

---

## Self-review

- **Issue line:** `**Issue:** #633` is directly under the title.
- **Spec coverage:** Requirement 1 (scheduler unchanged) is checked by
  `test_scheduler_side_ignores_intent` and the existing tests. Requirement 2 is Task 1.
  Requirement 3 (intent only via `--intent`) is Tasks 4–5 and
  `test_intent_not_read_from_environment_or_pointer`. Requirement 4 is Task 5. Requirement 5
  (thirteen names) is Task 2. Requirement 6 is Task 3. Requirement 7 is Task 6. Docs are Task 8,
  `ci.yml` is Task 7.
- **Every test the spec names exists:** `test_scheduler_side_ignores_intent`,
  `test_local_entry_refused_for_every_intent_except_helper`,
  `test_helper_intent_omits_only_no_local_dispatch`,
  `test_helper_intent_still_fails_each_local_check`,
  `test_intent_not_read_from_environment_or_pointer`,
  `test_local_eligible_intents_is_exactly_the_helper`, `test_hosting_cli_threads_intent`,
  `test_preflight_cli_threads_intent`, `tests/test_entrypoint_intent_case.sh`,
  `test_local_selection_fails_on_alternate_credential_signal`,
  `test_platform_identity_variable_does_not_fail_local_selection`,
  `test_measured_non_diverting_names_are_not_guard_signals`,
  `test_invalidated_by_below_minimum_is_declared`, `test_invalidated_by_exact_minimum_is_tested`,
  `test_invalidated_by_non_string_element_is_declared_not_an_exception`,
  `tests/test_entrypoint_hosting_fallback.sh`.
- **Every existing test the spec lists is updated:** the 2 evidence tests (Task 3.4), the 3
  hosting stubs and the 3 CLI stubs (Task 4.2), and case M (Task 5.7).
- **No placeholders:** every step has its code or command and its expected result.
- **Not done here, by design (spec):** no `bench/` repair (follow-up). The
  `ENDPOINT_UNREACHABLE` leaf move is declined. #587 must still give both new intents an
  entrypoint-only path that never reaches the workflow's `parse-intent` node (spec §7).
