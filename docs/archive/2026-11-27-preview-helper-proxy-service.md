# Dedicated Per-Dispatch `preview-helper-proxy` Service — Implementation Plan

**Issue:** #634

**Goal:** Add a separate, opt-in `preview-helper-proxy` compose service. It holds the
`classify-preview-helper` lane's local endpoint configuration and secret, and is the only path
from a future helper run container to that endpoint. The intent reaches the proxy's loader only
through a fixed CLI pair, `--intent classify-preview-helper`, written literally in the service's
`entrypoint:`. The run-side preflight and hosting check for that one intent are changed to
expect the proxy's constant address and no secret. Nothing is activated: per #633 the intent
exits "not available", and nothing starts the service until #587's dispatcher does.

**Architecture:** `model_endpoint.py` gains `HELPER_PROXY_BASE_URL` and an `expected_base_url`
keyword on `_local_problems()`/`load_verified_entry()`. `preflight()` sets it and inverts the
secret check, both only for `intent in LOCAL_ELIGIBLE_INTENTS`. `providers/cli.py`'s
`model-endpoints hosting` passes the same constant for that intent only.
`model_adapter.resolve_local_destination()` forwards an `intent`. `model_proxy.main(argv=None)`
parses `--intent`. When the option is given, `main()` refuses to start, before binding, unless
it resolves exactly one verified local entry that is not its own address. `run-compose.yml`
gains one new service block and nothing else.

**Tech Stack:** Python 3 (pytest, aiohttp test utilities), bash, Docker Compose
(`docker compose config` only, never `up`/`run`), GitHub Actions CI.

**Spec:** `docs/superpowers/specs/2026-11-27-preview-helper-proxy-service-design.md`
(approved at `af9f82b`).

**Depends on: #633.** Every task builds on #633's merged interface:
`LOCAL_ELIGIBLE_INTENTS`, the `intent=None` keywords, the CLI's `--intent`,
`LOCAL_CREDENTIAL_ENV_SIGNALS`, the `invalidated_by` minimum, and #633's test helpers
(`HELPER`, `NO_LOCAL`, `_valid_local`, `_providers_cli`). Task 0 checks they exist. If any is
missing, stop: do not re-implement #633 here.

**Gate-adjacent, security-labelled change.** It edits the run-side local preflight and the
model proxy's startup. It must not touch any `gate_*` script, the breaker, budgets,
`scheduler.sh`, `entrypoint.sh`, `scripts/provider_env.sh`, `deploy/**`, `.github/workflows/**`,
`workflows/`, `config/`, `run_record.py`, `role_isolation.py`, or any tool allow/deny list.
In `run-compose.yml` the diff must be **added lines inside the new service block only**. The
`dark-factory` and `factory-model-proxy` definitions stay byte-identical. Expected
blast-radius verdict (spec, "Files touched"): `scripts/factory_core/model_proxy.py` is a
hotspot (score exactly 5.0, at the floor), so the gate returns `HUMAN_REQUIRED`. Stopping at
that hold for signed human approval is the intended outcome, not a failure.

**How this plan was checked.** Every code block below was applied and run on a scratch copy.
That copy was `origin/main` at `a8cba16` plus the Python code of #633's plan (Tasks 1–4,
`docs/superpowers/plans/2026-11-27-scoped-local-dispatch-refusal.md` on
`origin/refine/issue-633-…`, `ff9f4ee`) and #633's Step 8.1 first paragraph. #633 alone gave
4192 passing Python tests. With this plan applied, 4254 passed and none failed. Before Task 4
Step 4.1 existed, 4253 passed and 1 failed; that step fixes it (see Deviations). The CI-wired
compose tests `test_run_compose.sh`, `test_model_proxy_compose.sh`, `test_build_network_compose.sh` and `test_model_proxy_smoke.sh`
passed (Docker Compose v5.8.0). `test_provider_env_flags.sh` fails in that sandbox on unmodified
`main` too (host environment), as #633's plan also records. The "Expected" fail counts in each
task were measured on the same base. No container was started and no real endpoint was
contacted.

## Deviations from the spec (recorded for the reviewer)

1. **#633's own new tests need updating — 6 tests, 28 cases edited, 24 of them failing first
   (Task 1, Step 1.2).** The spec's
   list of tests this change breaks covers only the loader stubs. #633's plan adds tests that
   call `preflight(..., intent=HELPER)` or `model-endpoints hosting --intent
   classify-preview-helper` in an unproxied environment (entry `base_url`, secret present) and
   expect a pass. This ticket's acceptance criteria require exactly those to fail. The updates
   move each run-side call to the proxied run-container shape and leave every loader-side
   (`load_verified_entry`) assertion as #633 wrote it.
2. **`tests/test_provider_env_passthrough.py` is touched (Task 4, Step 4.1).** It is not in the
   spec's "Files touched" table. `test_forwarded_vars_not_also_in_compose_environment` greps
   the whole of `run-compose.yml` for any `PROVIDER_PASSTHROUGH_VARS` name used as an
   environment key. The new service's `FACTORY_MODEL_ENDPOINT:` key (interpolated from
   `PREVIEW_HELPER_ENDPOINT`, never forwarded, no `env_file`) trips it. The rewrite parses the
   YAML. It keeps the check for every service and every name, and exempts exactly that one key
   on that one service. A forwarded name added anywhere else, including elsewhere on
   `preview-helper-proxy`, still fails (checked by mutation).
3. **"A mock 5xx" is parametrised over 502, 503 and 504 (Task 3).** Those are
   `model_adapter.NOT_READY_STATUSES`, the only statuses that carry a failure class
   (`endpoint_unreachable`). A local 500 is forwarded with no class, and its dispatch record is
   `outcome: ok` today. That is an existing classification rule for every local destination.
   Changing it is out of this ticket's scope and is left as a reservation for #587, whose
   independence proof consumes `outcome`.
4. **`docs/configuration.md` gets one sentence beyond the `secret_reference` row (Task 5).**
   #633's rewritten paragraph says a local selection fails if `ANTHROPIC_BASE_URL` "does not
   match the entry". After this ticket that is false for the helper intent. One sentence after
   that paragraph states the helper rule, so the three edited passages agree (spec Test Plan,
   "Docs").
5. **Extra tests the spec allows ("the plan may add more").**
   `test_helper_proxy_base_url_is_the_service_address`,
   `test_helper_preflight_treats_an_empty_secret_as_absent`,
   `test_loader_expected_base_url_replaces_only_the_base_url_rule`,
   `test_module_entry_point_parses_its_own_arguments` and
   `test_current_run_dir_selects_the_helper_pointer` (a guard).

## Review amendments (2026-11-27, from executing this plan end to end)

The plan was executed task by task on the executed end state of #633's plan (`main` at
`a8cba16` plus #633, review amendments included), in the factory image with no network.
Every #633 anchor of Task 0 was found exactly once, and every stated count was observed:
35 failed (Step 1.3), 1 failed (2.3), 35 failed with the four guards passing (3.3), the two
compose failures (4.3), and green after each implementation step. The whole Python suite went
from 4206 passed on that base to 4268 passed (the +62 this plan states; the base has 14 more
tests than the 4192 quoted above because it includes #633's later tasks and review
amendments). All 56 `tests/test_*.sh` gave the same result before and after, apart from the
new `PASS:` lines. Two amendments:

1. **Step 4.2's source check also pins decisions D2 and D1.** Rewriting the service's
   `environment:` in the list form with `- ${PREVIEW_HELPER_SECRET_NAME:-FACTORY_HELPER_MODEL_SECRET}`
   — the form spec requirement 7 rejects because it can deliver any scheduler variable into the
   proxy — passed every test of this plan: with the name unset it renders the same eleven
   keys. The source check now requires the mapping form, exactly one pass-through key
   (`FACTORY_HELPER_MODEL_SECRET`), and `networks: [dark-factory-net]`. An unused top-level
   network still passes; that half stays with review of the `run-compose.yml` diff.
2. **Step 1.7 says exactly how the docstring sentence is replaced.** The sentence sits
   mid-line and its replacement is five indented lines.

Left as written, for the reviewer to know: `docs/local-inference-deployment-runbook.md`
(lines 25-26 and 86 at the #633 end state) still says the proxy service receives none of
the endpoint variables and that delivering them is #587's. The spec's requirement 11 and
"Files touched" do not list that file, so this plan does not edit it; Step 5.4's `grep`
covers only the three listed places.

---

## File Structure

| File | Change | Task |
|---|---|---|
| `scripts/factory_core/providers/model_endpoint.py` | `HELPER_PROXY_BASE_URL`; `expected_base_url` on `_local_problems()` and `load_verified_entry()`; `preflight()`'s two helper-intent rules | 1 |
| `scripts/factory_core/providers/cli.py` | `_model_endpoints_hosting` passes `expected_base_url` for an eligible `--intent` only | 1 |
| `tests/test_provider_model_endpoint.py` | new #634 block; six #633 tests moved to the proxied run-container shape | 1 |
| `scripts/factory_core/providers/model_adapter.py` | `resolve_local_destination(..., intent=None)` | 2 |
| `tests/test_provider_model_adapter.py` | one stub updated, one new test | 2 |
| `tests/test_factory_model_proxy.py` | two stubs (four cases) accept `intent=` (Task 2); new #634 block (Task 3) | 2, 3 |
| `scripts/factory_core/model_proxy.py` | `import argparse`; `_parse_args()`; `main(argv=None)` with the three helper-intent exits; `main(sys.argv[1:])`; module docstring | 3 |
| `tests/test_provider_env_passthrough.py` | the compose-environment guard parses YAML and exempts one key (Deviation 2) | 4 |
| `run-compose.yml` | the `preview-helper-proxy` service block; nothing else | 4 |
| `tests/test_run_compose.sh`, `tests/test_model_proxy_compose.sh` | extended (no new shell file, so `.github/workflows/ci.yml` is unchanged) | 4 |
| `docs/configuration.md`, `docs/dark-factory-model-proxy-rollout.md` | spec requirement 11 | 5 |

Run every command from the repo root. CI sets `PYTHONPATH=scripts`.

---

## Task 0: Preconditions (#633 is merged)

**Files:** none (read-only checks)

- [ ] **Step 0.1: Confirm #633's interface is on this branch.**

```bash
PYTHONPATH=scripts python3 -c "
from factory_core.providers import model_endpoint as me
import inspect
assert me.LOCAL_ELIGIBLE_INTENTS == ('classify-preview-helper',)
assert len(me.LOCAL_CREDENTIAL_ENV_SIGNALS) == 13
for f in (me._local_problems, me.preflight, me.load_verified_entry):
    assert 'intent' in inspect.signature(f).parameters, f.__name__
print('ok')"
grep -c 'mhost.add_argument("--intent"' scripts/factory_core/providers/cli.py
grep -nE '^HELPER = |^NO_LOCAL = |^def _valid_local|^def _providers_cli' tests/test_provider_model_endpoint.py
grep -c 'The model proxy passes none, so it never gets one.' scripts/factory_core/providers/model_endpoint.py
grep -c 'problems += _local_problems(entry, files, registry_path, intent=intent)' scripts/factory_core/providers/model_endpoint.py
grep -c 'model_endpoint.staged_registry_dir(), endpoint_id, intent=args.intent or None)' scripts/factory_core/providers/cli.py
grep -c 'the helper intent until #587\.' docs/configuration.md
```

  Expected: `ok`, then `1`, then four matching lines, then `1` four times (the anchors the
  later tasks edit; #633's plan wording). If a name is missing, #633 has not landed as its
  plan describes: stop, and report it on the issue rather than continuing. If only an anchor
  count is `0`, #633's merged wording drifted: make the same change in meaning at the drifted
  text, as Step 1.2 describes, and record it in that task's commit message.

- [ ] **Step 0.2: Confirm the baseline is green.**

```bash
PYTHONPATH=scripts python3 -m pytest tests/test_provider_model_endpoint.py tests/test_provider_cli.py tests/test_provider_model_adapter.py tests/test_factory_model_proxy.py tests/test_provider_env_passthrough.py -q
bash tests/test_run_compose.sh && bash tests/test_model_proxy_compose.sh
```

  Expected: all pass. The compose tests print `SKIP:` lines if Docker is absent.

---

## Task 1: Run-side reconciliation for the helper intent

**Files:** `scripts/factory_core/providers/model_endpoint.py`,
`scripts/factory_core/providers/cli.py`, `tests/test_provider_model_endpoint.py`

- [ ] **Step 1.1: Write the failing tests.** Append this block to the **end** of
  `tests/test_provider_model_endpoint.py`. It uses helpers defined earlier in the file:
  `_local_env`, `_select_staged`, `base_env`, `pinned_suite`, `LOCAL`, `SENTINEL`, and from
  #633 `HELPER`, `NO_LOCAL`, `_valid_local`, `_providers_cli`. All are module-level.

```python
# --- #634: run-side reconciliation for the helper intent (preview-helper-proxy) -------------

HELPER_PROXY = "http://preview-helper-proxy:8787"
BASE_URL_TO_PROXY = ("endpoint 'local-test': ANTHROPIC_BASE_URL must equal the helper proxy "
                     f"address {HELPER_PROXY} (#634)")
SECRET_PRESENT = ("LOCAL_GATEWAY_TOKEN (secret_reference of endpoint 'local-test') is set in "
                  "this container — a helper run reaches its endpoint only through "
                  "preview-helper-proxy, which holds it (#634)")


def _helper_run_env(monkeypatch, entry=LOCAL):
    """A correctly configured helper run container: the proxy's address, no secret."""
    _local_env(monkeypatch, entry)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", HELPER_PROXY)
    monkeypatch.delenv(entry["secret_reference"], raising=False)


def _valid_helper_run(tmp_path, monkeypatch):
    reg = _valid_local(tmp_path, monkeypatch)
    _helper_run_env(monkeypatch)
    return reg


def test_helper_proxy_base_url_is_the_service_address():
    assert me.HELPER_PROXY_BASE_URL == HELPER_PROXY


def test_helper_preflight_passes_with_the_proxy_address_and_no_secret(base_env, tmp_path,
                                                                     monkeypatch, capsys,
                                                                     pinned_suite):
    reg = _valid_helper_run(tmp_path, monkeypatch)
    assert me.preflight(reg, "local-test", intent=HELPER) == ([], "local")
    _select_staged(monkeypatch, reg, "local-test")
    assert _providers_cli(monkeypatch, capsys, "preflight", "--intent", HELPER) \
        == (0, "providers preflight: OK\n", "")


@pytest.mark.parametrize("base_url", ["entry", "http://192.0.2.50:8080", HELPER_PROXY + "/",
                                      None])
def test_helper_preflight_refuses_other_base_urls(tmp_path, monkeypatch, pinned_suite, base_url):
    reg = _valid_helper_run(tmp_path, monkeypatch)
    if base_url is None:
        monkeypatch.delenv("ANTHROPIC_BASE_URL")
    else:
        monkeypatch.setenv("ANTHROPIC_BASE_URL",
                           LOCAL["base_url"] if base_url == "entry" else base_url)
    assert me.preflight(reg, "local-test", intent=HELPER) == ([BASE_URL_TO_PROXY], "local")


def test_helper_preflight_refuses_a_present_secret(tmp_path, monkeypatch, pinned_suite):
    reg = _valid_helper_run(tmp_path, monkeypatch)
    monkeypatch.setenv("LOCAL_GATEWAY_TOKEN", SENTINEL)
    problems, hosting = me.preflight(reg, "local-test", intent=HELPER)
    assert (problems, hosting) == ([SECRET_PRESENT], "local")
    assert SENTINEL not in " ".join(problems)


def test_helper_preflight_treats_an_empty_secret_as_absent(tmp_path, monkeypatch, pinned_suite):
    reg = _valid_helper_run(tmp_path, monkeypatch)
    monkeypatch.setenv("LOCAL_GATEWAY_TOKEN", "")
    assert me.preflight(reg, "local-test", intent=HELPER) == ([], "local")


def test_proxy_side_loader_still_requires_the_entry_base_url(tmp_path, monkeypatch, pinned_suite):
    # Regression guard (passes before #634): the proxy's own loader call passes no
    # expected_base_url, so its container must hold the entry's real base_url.
    reg = _valid_local(tmp_path, monkeypatch)
    assert me.load_verified_entry(reg, "local-test", intent=HELPER)[0] == []
    monkeypatch.setenv("ANTHROPIC_BASE_URL", HELPER_PROXY)
    problems, entry = me.load_verified_entry(reg, "local-test", intent=HELPER)
    assert entry is None and problems == [
        "endpoint 'local-test': ANTHROPIC_BASE_URL must equal the entry's base_url "
        "(contract §4.11 decision 1)"]


def test_loader_expected_base_url_replaces_only_the_base_url_rule(tmp_path, monkeypatch,
                                                                  pinned_suite):
    reg = _valid_helper_run(tmp_path, monkeypatch)
    problems, entry = me.load_verified_entry(reg, "local-test", intent=HELPER,
                                             expected_base_url=HELPER_PROXY)
    assert problems == [] and entry["id"] == "local-test"
    # Without the helper intent the refusal stands, whatever address the caller expects.
    assert me.load_verified_entry(reg, "local-test", intent="fix",
                                  expected_base_url=HELPER_PROXY) == ([NO_LOCAL], None)


_HELPER_SHAPED_PROBLEMS = [
    "LOCAL_GATEWAY_TOKEN (secret_reference of endpoint 'local-test') is not set",
    "endpoint 'local-test': ANTHROPIC_BASE_URL must equal the entry's base_url "
    "(contract §4.11 decision 1)",
    NO_LOCAL,
]


@pytest.mark.parametrize("env_shape", ["helper", "today"])
@pytest.mark.parametrize("intent", [None, "", "fix", "refine", "classify-preview-checker"])
def test_non_helper_intents_ignore_the_helper_rules(tmp_path, monkeypatch, pinned_suite,
                                                    env_shape, intent):
    # Regression guard (passes before #634): the same problems as #633 alone produces.
    reg = _valid_local(tmp_path, monkeypatch)
    if env_shape == "helper":
        _helper_run_env(monkeypatch)
    expected = _HELPER_SHAPED_PROBLEMS if env_shape == "helper" else [NO_LOCAL]
    assert me.preflight(reg, "local-test", intent=intent) == (expected, "local")


def test_helper_hosting_resolves_local_only_with_the_proxy_address(tmp_path, monkeypatch, capsys,
                                                                   pinned_suite):
    reg = _valid_helper_run(tmp_path, monkeypatch)
    _select_staged(monkeypatch, reg, "local-test")
    argv = ("model-endpoints", "hosting", "--intent", HELPER)
    assert _providers_cli(monkeypatch, capsys, *argv) == (0, "hosting=local\n", "")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", LOCAL["base_url"])
    code, out, err = _providers_cli(monkeypatch, capsys, *argv)
    assert (code, out) == (0, "hosting=cloud\n") and BASE_URL_TO_PROXY in err


def test_hosting_cli_passes_no_expected_base_url_for_other_intents(tmp_path, monkeypatch, capsys):
    calls = []

    def loader(path, eid, **kwargs):
        calls.append(kwargs)
        return ["stub"], None

    monkeypatch.setattr(me, "load_verified_entry", loader)
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "local-test")
    for extra in ([], ["--intent", ""], ["--intent", "fix"], ["--intent", HELPER]):
        _providers_cli(monkeypatch, capsys, "model-endpoints", "hosting", *extra)
    assert calls == [{"intent": None}, {"intent": None}, {"intent": "fix"},
                     {"intent": HELPER, "expected_base_url": HELPER_PROXY}]
```

- [ ] **Step 1.2: Move #633's six run-side helper tests to the proxied shape (Deviation 1).**
  Each edit below is quoted as #633's plan wrote it. If the merged text differs cosmetically,
  apply the same change in meaning: every `preflight(..., intent=HELPER)` call and every
  `model-endpoints hosting --intent HELPER` / `preflight --intent HELPER` CLI call runs after
  `_helper_run_env(monkeypatch)`, or on a registry from `_valid_helper_run`. Every
  `load_verified_entry(..., intent=HELPER)` call keeps #633's proxy-shaped environment.

  1. `test_helper_intent_omits_only_no_local_dispatch`: replace its body

```python
    reg = _valid_local(tmp_path, monkeypatch)
    assert me.preflight(reg, "local-test", intent=HELPER) == ([], "local")
    problems, entry = me.load_verified_entry(reg, "local-test", intent=HELPER)
    assert problems == [] and entry["id"] == "local-test" and entry["hosting_location"] == "local"
```

  with

```python
    reg = _valid_local(tmp_path, monkeypatch)
    problems, entry = me.load_verified_entry(reg, "local-test", intent=HELPER)
    assert problems == [] and entry["id"] == "local-test" and entry["hosting_location"] == "local"
    _helper_run_env(monkeypatch)  # #634: the run container is checked in its proxied shape
    assert me.preflight(reg, "local-test", intent=HELPER) == ([], "local")
```

  2. `test_helper_intent_still_fails_each_local_check`: replace everything from
     `reg = _valid_local(tmp_path, monkeypatch, record)` to the end of the function

```python
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
```

  with

```python
    reg = _valid_local(tmp_path, monkeypatch, record)

    def break_the_check():
        if check == "cloud_credential":
            monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL + "-cloud")
        elif check == "base_url":
            monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://192.0.2.99:8080")
        elif check == "tier_alias":
            monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-5-5")

    break_the_check()
    problems, entry = me.load_verified_entry(reg, "local-test", intent=HELPER)
    assert entry is None and any(expected in p for p in problems)
    # #634: the run container is checked in its proxied shape, so its base_url rule names
    # the helper proxy address instead of the entry's.
    _helper_run_env(monkeypatch)
    break_the_check()
    if check == "base_url":
        expected = "ANTHROPIC_BASE_URL must equal the helper proxy address"
    problems, hosting = me.preflight(reg, "local-test", intent=HELPER)
    assert hosting == "local" and any(expected in p for p in problems)
    assert NO_LOCAL not in problems
```

  3. `test_local_selection_fails_on_alternate_credential_signal`: replace its last two lines

```python
    assert me.preflight(reg, "local-test", intent=HELPER) == ([expected], "local")
    assert me.load_verified_entry(reg, "local-test", intent=HELPER) == ([expected], None)
```

  with

```python
    assert me.load_verified_entry(reg, "local-test", intent=HELPER) == ([expected], None)
    _helper_run_env(monkeypatch)  # #634: the run container is checked in its proxied shape
    assert me.preflight(reg, "local-test", intent=HELPER) == ([expected], "local")
```

  4. `test_platform_identity_variable_does_not_fail_local_selection`: its first body line
     `reg = _valid_local(tmp_path, monkeypatch)` becomes

```python
    reg = _valid_helper_run(tmp_path, monkeypatch)  # #634: the proxied run-container shape
```

  5. `test_hosting_cli_threads_intent`: its first body line
     `reg = _valid_local(tmp_path, monkeypatch)` becomes the same line as in 4.
  6. `test_preflight_cli_threads_intent`: its first body line
     `reg = _valid_local(tmp_path, monkeypatch)` becomes the same line as in 4.

  (`_helper_run_env` and `_valid_helper_run` are defined in Step 1.1's block, later in the
  module. Python resolves them when the test runs, so the order is fine.)

- [ ] **Step 1.3: Verify they fail.**

```bash
PYTHONPATH=scripts python3 -m pytest tests/test_provider_model_endpoint.py -q
```

  Expected: `35 failed`. 11 are new: `test_helper_proxy_base_url_is_the_service_address`
  (`AttributeError`), `..._passes_with_the_proxy_address_and_no_secret`, the 4
  `..._refuses_other_base_urls`, `..._refuses_a_present_secret`,
  `..._treats_an_empty_secret_as_absent`, `test_loader_expected_base_url_...` (`TypeError` on
  `expected_base_url`), `test_helper_hosting_resolves_local_...` and
  `test_hosting_cli_passes_no_expected_base_url_...`. 24 are Step 1.2's updated #633 cases
  (1 + 1 + 13 + 7 + 1 + 1; item 2 edits all 5 cases of its test, but only `base_url` fails
  first). These pass on #633 alone and **pass now** (regression guards):
  `test_proxy_side_loader_still_requires_the_entry_base_url` and the 10
  `test_non_helper_intents_ignore_the_helper_rules` cases.

- [ ] **Step 1.4: Add the constant.** In `model_endpoint.py`, directly after the
  `LOCAL_ELIGIBLE_INTENTS = ("classify-preview-helper",)` line (#633), add:

```python
# The run-side address of a local-eligible dispatch (#634): run-compose.yml's
# preview-helper-proxy service on model_proxy's MODEL_PROXY_PORT. A helper run container
# reaches its local endpoint only through that proxy, which holds the entry's base_url and
# secret; the run container holds neither.
HELPER_PROXY_BASE_URL = "http://preview-helper-proxy:8787"
```

- [ ] **Step 1.5: `expected_base_url` on `_local_problems()`.** Change the signature

```python
def _local_problems(entry, files, registry_path, intent=None):
```

  to

```python
def _local_problems(entry, files, registry_path, intent=None, expected_base_url=None):
```

  and replace the `ANTHROPIC_BASE_URL` check

```python
    if os.environ.get("ANTHROPIC_BASE_URL") != entry["base_url"]:
        problems.append(f"endpoint '{eid}': ANTHROPIC_BASE_URL must equal the entry's base_url "
                        "(contract §4.11 decision 1)")
```

  with

```python
    # expected_base_url is the caller's statement of which container it checks (#634): None
    # (the proxy, and every non-helper caller) keeps the entry's base_url. Exact match.
    if expected_base_url is not None:
        if os.environ.get("ANTHROPIC_BASE_URL") != expected_base_url:
            problems.append(f"endpoint '{eid}': ANTHROPIC_BASE_URL must equal the helper proxy "
                            f"address {expected_base_url} (#634)")
    elif os.environ.get("ANTHROPIC_BASE_URL") != entry["base_url"]:
        problems.append(f"endpoint '{eid}': ANTHROPIC_BASE_URL must equal the entry's base_url "
                        "(contract §4.11 decision 1)")
```

  Nothing else in `_local_problems()` changes. The cloud-credential, alternate-signal, alias,
  evidence and `_NO_LOCAL_DISPATCH` checks run exactly as #633 left them.

- [ ] **Step 1.6: The two helper-intent rules in `preflight()`.** Replace

```python
    hosting = entry["hosting_location"]
    ref = entry["secret_reference"]
    if hosting == "cloud":
        problems += _missing([ref])  # a cloud entry's secret arrives via .archon/.env
    elif not os.environ.get(ref):
        # Not _missing(): a local secret must not be delivered through .archon/.env (spec §C).
        problems.append(f"{ref} (secret_reference of endpoint '{entry['id']}') is not set")
    if hosting == "local":
        problems += _local_problems(entry, files, registry_path, intent=intent)
```

  with

```python
    hosting = entry["hosting_location"]
    ref = entry["secret_reference"]
    # A local-eligible run goes through preview-helper-proxy (#634): the proxy's address, and
    # the secret held by the proxy alone. Every other intent keeps the unproxied rules.
    helper = intent in LOCAL_ELIGIBLE_INTENTS
    if hosting == "cloud":
        problems += _missing([ref])  # a cloud entry's secret arrives via .archon/.env
    elif helper:
        if os.environ.get(ref):
            problems.append(f"{ref} (secret_reference of endpoint '{entry['id']}') is set in this "
                            "container — a helper run reaches its endpoint only through "
                            "preview-helper-proxy, which holds it (#634)")
    elif not os.environ.get(ref):
        # Not _missing(): a local secret must not be delivered through .archon/.env (spec §C).
        problems.append(f"{ref} (secret_reference of endpoint '{entry['id']}') is not set")
    if hosting == "local":
        problems += _local_problems(entry, files, registry_path, intent=intent,
                                    expected_base_url=HELPER_PROXY_BASE_URL if helper else None)
```

  The message never contains the secret's value, only its name.

- [ ] **Step 1.7: `expected_base_url` on `load_verified_entry()`.** Change the signature

```python
def load_verified_entry(registry_path, endpoint_id, intent=None) -> tuple[list[str], dict | None]:
```

  to

```python
def load_verified_entry(registry_path, endpoint_id, intent=None,
                        expected_base_url=None) -> tuple[list[str], dict | None]:
```

  In its docstring, #633's last line reads
  `    LOCAL_ELIGIBLE_INTENTS (#633). The model proxy passes none, so it never gets one.`
  Cut that line after `(#633).` (delete ` The model proxy passes none, so it never gets one.`)
  and add these five lines directly below it, before the closing `"""`:

```
    The model proxy passes its own --intent (#634): factory-model-proxy has none, so it never
    gets one. expected_base_url is forwarded to _local_problems() and used for nothing else
    (#634): the proxy never passes it, so its own ANTHROPIC_BASE_URL must be the entry's
    base_url; the run container's hosting resolution passes HELPER_PROXY_BASE_URL for a
    local-eligible intent only.
```

  and in the body replace

```python
        problems = problems + _local_problems(entry, files, registry_path, intent=intent)
```

  with

```python
        problems = problems + _local_problems(entry, files, registry_path, intent=intent,
                                              expected_base_url=expected_base_url)
```

  Do **not** touch `scheduler_preflight()`.

- [ ] **Step 1.8: The hosting CLI.** In `scripts/factory_core/providers/cli.py`,
  `_model_endpoints_hosting`, replace #633's loader call

```python
            problems, entry = model_endpoint.load_verified_entry(
                model_endpoint.staged_registry_dir(), endpoint_id, intent=args.intent or None)
```

  with

```python
            # A local-eligible run's container points at preview-helper-proxy, not the
            # entry (#634). Any other intent passes no expected_base_url at all.
            extra = ({"expected_base_url": model_endpoint.HELPER_PROXY_BASE_URL}
                     if args.intent in model_endpoint.LOCAL_ELIGIBLE_INTENTS else {})
            problems, entry = model_endpoint.load_verified_entry(
                model_endpoint.staged_registry_dir(), endpoint_id, intent=args.intent or None,
                **extra)
```

  No other CLI verb changes. `preflight --intent` already reaches `model_endpoint.preflight()`
  through #633's `providers.preflight(side, intent)`, which needs no change.

- [ ] **Step 1.9: Verify.**

```bash
PYTHONPATH=scripts python3 -m pytest tests/test_provider_model_endpoint.py tests/test_provider_cli.py tests/test_factory_model_proxy.py -q
```

  Expected: all pass. That includes #633's
  `test_local_entry_refused_for_every_intent_except_helper`,
  `test_intent_not_read_from_environment_or_pointer`, `test_scheduler_side_ignores_intent` and
  the `hosting` stub tests, unchanged. They pass no helper intent, or stub the loader with
  `intent=None` only.

- [ ] **Step 1.10: Commit.**

```bash
git add scripts/factory_core/providers/model_endpoint.py scripts/factory_core/providers/cli.py tests/test_provider_model_endpoint.py
git commit -m "feat(#634): helper-intent run preflight expects preview-helper-proxy and no secret" \
  -m "Six of #633's run-side helper tests assumed an unproxied environment; they now check the run container in its proxied shape (plan Deviation 1)."
```

---

## Task 2: `model_adapter.resolve_local_destination()` forwards the intent

**Files:** `scripts/factory_core/providers/model_adapter.py`,
`tests/test_provider_model_adapter.py`, `tests/test_factory_model_proxy.py`

- [ ] **Step 2.1: Write the failing test and update the existing stub.** In
  `tests/test_provider_model_adapter.py`, in
  `test_resolve_local_destination_builds_from_the_verified_entry`, replace

```python
    def loader(path, eid):
        seen.append((path, eid))
        return [], copy.deepcopy(CLOUD)

    monkeypatch.setattr(me, "load_verified_entry", loader)
    assert ma.resolve_local_destination(tmp_path, "cloud-test")["endpoint_id"] == "cloud-test"
    assert seen == [(tmp_path, "cloud-test")]
```

  with

```python
    def loader(path, eid, intent=None):
        seen.append((path, eid, intent))
        return [], copy.deepcopy(CLOUD)

    monkeypatch.setattr(me, "load_verified_entry", loader)
    assert ma.resolve_local_destination(tmp_path, "cloud-test")["endpoint_id"] == "cloud-test"
    assert seen == [(tmp_path, "cloud-test", None)]
```

  and insert directly above `def test_adapter_module_does_not_import_aiohttp():`

```python
def test_resolve_local_destination_forwards_the_intent(tmp_path, monkeypatch):
    # #634: the proxy's --intent reaches the loader unchanged, and nothing else does.
    seen = []

    def loader(path, eid, **kwargs):
        seen.append(kwargs)
        return [], copy.deepcopy(CLOUD)

    monkeypatch.setattr(me, "load_verified_entry", loader)
    ma.resolve_local_destination(tmp_path, "cloud-test", intent="classify-preview-helper")
    ma.resolve_local_destination(tmp_path, "cloud-test")
    assert seen == [{"intent": "classify-preview-helper"}, {"intent": None}]


```

- [ ] **Step 2.2: Update the two proxy-startup stubs the spec lists (four test cases).** They
  now receive `intent=`. In `tests/test_factory_model_proxy.py`:
  - `test_fixture_fed_local_startup_failures_exit_before_binding`: replace
    `monkeypatch.setattr(me, "load_verified_entry", lambda path, eid: ([], copy.deepcopy(LOCAL_ENTRY)))`
    with

```python
    monkeypatch.setattr(me, "load_verified_entry",
                        lambda path, eid, intent=None: ([], copy.deepcopy(LOCAL_ENTRY)))
```

  - `test_payload_decision_allow_false_fails_startup`: replace
    `monkeypatch.setattr(me, "load_verified_entry", lambda path, eid: ([], entry))` with

```python
    monkeypatch.setattr(me, "load_verified_entry", lambda path, eid, intent=None: ([], entry))
```

  Both still pass on today's code: a defaulted parameter changes nothing until something
  passes it.

- [ ] **Step 2.3: Verify the new test fails.**

```bash
PYTHONPATH=scripts python3 -m pytest tests/test_provider_model_adapter.py tests/test_factory_model_proxy.py -q
```

  Expected: `1 failed` —
  `test_resolve_local_destination_forwards_the_intent` (`TypeError: ... unexpected keyword
  argument 'intent'`).

- [ ] **Step 2.4: Implement.** In `scripts/factory_core/providers/model_adapter.py` replace

```python
def resolve_local_destination(registry_path, endpoint_id):
    """The proxy's one startup resolution (spec Req. 2): verified loader, then the checks."""
    problems, entry = model_endpoint.load_verified_entry(registry_path, endpoint_id)
```

  with

```python
def resolve_local_destination(registry_path, endpoint_id, intent=None):
    """The proxy's one startup resolution (spec Req. 2): verified loader, then the checks.

    intent is the proxy's own --intent argument (#634), forwarded to the loader unchanged.
    """
    problems, entry = model_endpoint.load_verified_entry(registry_path, endpoint_id,
                                                         intent=intent)
```

  `destination_from_entry()` is unchanged. It never receives `expected_base_url`.

- [ ] **Step 2.5: Verify.**

```bash
PYTHONPATH=scripts python3 -m pytest tests/test_provider_model_adapter.py tests/test_factory_model_proxy.py -q
```

  Expected: all pass. Without Step 2.2, the three
  `test_fixture_fed_local_startup_failures_exit_before_binding` cases and
  `test_payload_decision_allow_false_fails_startup` would now fail with `TypeError`
  (verified).

- [ ] **Step 2.6: Commit.**

```bash
git add scripts/factory_core/providers/model_adapter.py tests/test_provider_model_adapter.py tests/test_factory_model_proxy.py
git commit -m "feat(#634): resolve_local_destination forwards the proxy's intent to the verified loader"
```

---

## Task 3: `model_proxy.main()` parses `--intent` and never falls back to cloud

**Files:** `scripts/factory_core/model_proxy.py`, `tests/test_factory_model_proxy.py`

- [ ] **Step 3.1: Add the imports.** In `tests/test_factory_model_proxy.py`, the stdlib import
  block becomes (add `shutil` and `subprocess`):

```python
import asyncio
import copy
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
```

- [ ] **Step 3.2: Write the failing tests.** Append this block to the **end** of
  `tests/test_factory_model_proxy.py`. It reuses module-level helpers: `startup`,
  `_stage_registry`, `_main_exit`, `local_env`, `_make_upstream`, `_recording`, `_url`,
  `_rows`, `_wait_for`, `_run`, `_ok_json`, `_post_json`, `_silent`, `_answering_as`,
  `_pointer`, `ISSUE`, `LOCAL_ENTRY`, `CLOUD_ENTRY`, `SENTINEL`, `SERVED`. The block needs
  `me.LOCAL_CREDENTIAL_ENV_SIGNALS` (#633) and `me.HELPER_PROXY_BASE_URL` (Task 1).

```python
# --- #634: the helper intent (preview-helper-proxy) --------------------------------------

HELPER = "classify-preview-helper"
HELPER_ARGV = ["--intent", HELPER]
HELPER_SECRET = "FACTORY_HELPER_MODEL_SECRET"
HELPER_BASE_URL = "http://192.0.2.10:8080"
HELPER_ENTRY = dict(copy.deepcopy(LOCAL_ENTRY), id="helper-local", base_url=HELPER_BASE_URL,
                    secret_reference=HELPER_SECRET)
HELPER_NO_LOCAL = me._NO_LOCAL_DISPATCH.format(eid="helper-local")
HELPER_RUN = "run-helper"


def _tested_evidence(entry):
    """A `tested`, actor-qualified capability evidence record for entry."""
    return {
        "schema_version": "1.0", "endpoint_id": entry["id"],
        "model_identity": dict(entry["model_identity"], quantisation="q4_k_m"),
        "runtime_stack": {"name": "rt", "version": "1.0.0", "chat_template": "t",
                          "context_settings": {}},
        "harness_version": "2.1.282", "fixture_suite_version": "suite-1",
        "results": {dim: "pass" for dim in me.ACTOR_REQUIRED_DIMENSIONS},
        "qualified_roles": ["actor"], "certified_by": "operator",
        "certified_at": "2026-11-01T00:00:00+00:00", "expires_at": "2099-01-01T00:00:00+00:00",
        "invalidated_by": ["model_digest", "harness_version", "fixture_suite_version",
                           "registry_config"],
        "registry_config": me.registry_config_sha256(entry),
    }


@pytest.fixture
def helper_proxy(startup, tmp_path, monkeypatch):
    """configure(**entry overrides) stages one `tested` local entry and sets exactly the
    environment run-compose.yml gives preview-helper-proxy; .served records each app main()
    would have bound (empty == exited before binding)."""
    monkeypatch.setattr(me, "PINNED_FIXTURE_SUITE_VERSION", "suite-1")
    monkeypatch.setattr(me, "observed_harness_version", lambda: "2.1.282")
    for name in (HELPER_SECRET, *me.LOCAL_CREDENTIAL_ENV_SIGNALS, "INTENT",
                 "FACTORY_DISPATCH_INTENT", "FACTORY_INTENT", "MODEL_PROXY_INTENT"):
        monkeypatch.delenv(name, raising=False)

    def configure(extra=(), **overrides):
        entry = dict(copy.deepcopy(HELPER_ENTRY), **overrides)
        shutil.rmtree(tmp_path / "staged", ignore_errors=True)
        reg = _stage_registry(tmp_path, monkeypatch, [entry, *extra])
        (reg / me.EVIDENCE_DIR).mkdir()
        (reg / me.EVIDENCE_DIR / f"{entry['id']}.yaml").write_text(
            yaml.safe_dump({"capability_evidence": _tested_evidence(entry)}), encoding="utf-8")
        monkeypatch.setenv("FACTORY_MODEL_REGISTRY_SHA256", me.tree_sha256(reg))
        monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", entry["id"])
        monkeypatch.setenv("ANTHROPIC_BASE_URL", entry["base_url"])
        for alias in me.TIER_ALIASES:
            monkeypatch.setenv(alias, SERVED)
        monkeypatch.setenv(HELPER_SECRET, SENTINEL)
        return entry

    configure.served = startup
    return configure


def _helper_exit(capsys, argv=HELPER_ARGV):
    with pytest.raises(SystemExit) as exc:
        mp.main(argv)
    assert exc.value.code == 1
    return capsys.readouterr().err


def test_helper_intent_starts_with_the_local_destination(helper_proxy):
    helper_proxy()
    mp.main(HELPER_ARGV)
    assert len(helper_proxy.served) == 1
    dest = helper_proxy.served[0]["local_destination"]
    assert (dest["endpoint_id"], dest["hosting_location"], dest["upstream"],
            dest["secret_reference"]) == ("helper-local", "local", HELPER_BASE_URL, HELPER_SECRET)


@pytest.mark.parametrize("value", [None, ""])
def test_helper_intent_without_an_endpoint_exits_before_binding(helper_proxy, monkeypatch, capsys,
                                                                value):
    helper_proxy()
    if value is None:
        monkeypatch.delenv("FACTORY_MODEL_ENDPOINT")
    else:
        monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", value)
    err = _helper_exit(capsys)
    assert "FACTORY_MODEL_ENDPOINT is not set" in err and helper_proxy.served == []


def test_helper_intent_with_a_cloud_entry_exits_before_binding(helper_proxy, monkeypatch, capsys):
    helper_proxy(extra=[CLOUD_ENTRY])
    monkeypatch.setenv("FACTORY_MODEL_ENDPOINT", "cloud-test")
    err = _helper_exit(capsys)
    assert "'cloud-test' is hosting_location=cloud" in err and helper_proxy.served == []


@pytest.mark.parametrize("base_url", ["http://preview-helper-proxy:8787",
                                      "http://preview-helper-proxy:8787/"])
def test_helper_intent_refuses_an_entry_pointing_at_the_proxy_address(helper_proxy, capsys,
                                                                      base_url):
    helper_proxy(base_url=base_url)
    err = _helper_exit(capsys)
    assert "forward to its own address" in err and helper_proxy.served == []


def test_empty_intent_value_counts_as_given(helper_proxy, monkeypatch, capsys):
    helper_proxy()
    assert HELPER_NO_LOCAL in _helper_exit(capsys, ["--intent", ""])
    monkeypatch.delenv("FACTORY_MODEL_ENDPOINT")
    assert "FACTORY_MODEL_ENDPOINT is not set" in _helper_exit(capsys, ["--intent", ""])
    assert helper_proxy.served == []


_ENTRY_BASE_URL = "ANTHROPIC_BASE_URL must equal the entry's base_url"
_SECRET_UNSET = f"{HELPER_SECRET} (secret_reference of endpoint 'helper-local') is not set"


@pytest.mark.parametrize("change,needle", [
    pytest.param(lambda m: m.delenv("FACTORY_MODEL_REGISTRY_SHA256"),
                 "FACTORY_MODEL_REGISTRY_SHA256 is not set", id="digest-missing"),
    pytest.param(lambda m: m.setenv("FACTORY_MODEL_REGISTRY_SHA256", "0" * 64),
                 "does not match FACTORY_MODEL_REGISTRY_SHA256", id="digest-wrong"),
    pytest.param(lambda m: m.delenv("ANTHROPIC_BASE_URL"), _ENTRY_BASE_URL,
                 id="base-url-missing"),
    pytest.param(lambda m: m.setenv("ANTHROPIC_BASE_URL", "http://preview-helper-proxy:8787"),
                 _ENTRY_BASE_URL, id="base-url-is-the-proxy-constant"),
    *[pytest.param(lambda m, a=alias: m.delenv(a),
                   f"{alias} must equal model_identity.served_name", id=f"{alias}-missing")
      for alias in me.TIER_ALIASES],
    pytest.param(lambda m: m.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-5"),
                 "ANTHROPIC_DEFAULT_SONNET_MODEL must equal model_identity.served_name",
                 id="alias-wrong"),
    pytest.param(lambda m: m.delenv(HELPER_SECRET), _SECRET_UNSET, id="secret-missing"),
    pytest.param(lambda m: m.setenv(HELPER_SECRET, ""), _SECRET_UNSET, id="secret-empty"),
    pytest.param(lambda m: m.setenv("MODEL_PROXY_UPSTREAM", "http://192.0.2.77:8080"),
                 "MODEL_PROXY_UPSTREAM is set and differs", id="upstream-elsewhere"),
])
def test_helper_intent_refuses_each_missing_or_wrong_variable(helper_proxy, monkeypatch, capsys,
                                                              change, needle):
    helper_proxy()
    change(monkeypatch)
    err = _helper_exit(capsys)
    assert needle in err and SENTINEL not in err and helper_proxy.served == []


@pytest.mark.parametrize("name", me.CLOUD_CREDENTIALS)
def test_helper_intent_refuses_each_cloud_credential(helper_proxy, monkeypatch, capsys, name):
    helper_proxy()
    monkeypatch.setenv(name, "cloud-sentinel")
    err = _helper_exit(capsys)
    assert f"cloud model credential(s) are set in this container: {name}" in err
    assert "cloud-sentinel" not in err and SENTINEL not in err and helper_proxy.served == []


def test_no_intent_argument_still_refuses_a_local_entry(helper_proxy, capsys):
    # Regression guard (passes before #634): factory-model-proxy's own startup is unchanged.
    helper_proxy()
    assert HELPER_NO_LOCAL in _main_exit(capsys)
    assert helper_proxy.served == []


def test_bare_main_ignores_sys_argv(helper_proxy, monkeypatch, capsys):
    # Regression guard: main() with no argv parses no arguments, never sys.argv (pytest's own
    # argv would otherwise reach the parser, and an argv-borne --intent would be honoured).
    helper_proxy()
    monkeypatch.setattr(sys, "argv", ["model_proxy", *HELPER_ARGV])
    with pytest.raises(SystemExit):
        mp.main()
    assert HELPER_NO_LOCAL in capsys.readouterr().err and helper_proxy.served == []


def test_intent_is_not_read_from_the_environment(helper_proxy, monkeypatch, capsys):
    # Regression guard: plausible variable names never stand in for the fixed argument.
    helper_proxy()
    for name in ("INTENT", "FACTORY_DISPATCH_INTENT", "FACTORY_INTENT", "MODEL_PROXY_INTENT"):
        monkeypatch.setenv(name, HELPER)
    assert HELPER_NO_LOCAL in _main_exit(capsys)
    assert helper_proxy.served == []


def test_module_entry_point_parses_its_own_arguments(tmp_path):
    # `python3 -m factory_core.model_proxy --intent …` is the service's literal entrypoint.
    # With no endpoint it must exit 1 before binding; an unknown option exits 2 (argparse).
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(Path(mp.__file__).parents[1])}
    run = lambda *argv: subprocess.run(  # noqa: E731
        [sys.executable, "-m", "factory_core.model_proxy", *argv], env=env, cwd=tmp_path,
        capture_output=True, text=True, timeout=30)
    helper = run(*HELPER_ARGV)
    assert helper.returncode == 1 and "FACTORY_MODEL_ENDPOINT is not set" in helper.stderr
    assert run("--intent-x", HELPER).returncode == 2


def test_current_run_dir_selects_the_helper_pointer(tmp_path):
    # Regression guard (passes before #634): the service's CURRENT_RUN_DIR literal is honoured.
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(Path(mp.__file__).parents[1]),
           "CURRENT_RUN_DIR": "/var/lib/dark-factory/preview-helper"}
    out = subprocess.run(
        [sys.executable, "-c", "from factory_core import model_proxy as m; print(m.CURRENT_RUN_PATH)"],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=30, check=True).stdout
    assert out == "/var/lib/dark-factory/preview-helper/current-run.json\n"


def _helper_scenario(helper_proxy, local_env, monkeypatch, handler, send, *, rows=1,
                     timeouts=None):
    """Stage the entry at a fake local upstream, start the app main(HELPER_ARGV) builds, run
    send(client); returns (send's result, upstream calls, cloud-default calls)."""
    calls, cloud_calls = [], []

    async def scenario():
        upstream = await _make_upstream(_recording(handler, calls))
        cloud = await _make_upstream(_recording(_ok_json, cloud_calls))
        monkeypatch.setattr(mp, "UPSTREAM", _url(cloud))
        helper_proxy(base_url=_url(upstream),
                     timeouts=dict(HELPER_ENTRY["timeouts"], **(timeouts or {})))
        mp.main(HELPER_ARGV)
        client = TestClient(TestServer(helper_proxy.served[-1]))
        await client.start_server()
        try:
            result = await send(client)
            await _wait_for(lambda: len(_rows(local_env)) >= rows)
        finally:
            await client.close()
            await upstream.close()
            await cloud.close()
        return result

    return (_run(scenario()), calls, cloud_calls)


def _helper_pointer(local_env, monkeypatch):
    # The file entrypoint.sh writes under CURRENT_RUN_DIR=/var/lib/dark-factory/preview-helper.
    (local_env / "preview-helper").mkdir(exist_ok=True)
    path = _pointer(local_env / "preview-helper" / "current-run.json", HELPER_RUN)
    monkeypatch.setattr(mp, "CURRENT_RUN_PATH", path)


def _helper_dispatch(local_env):
    rows, _ = rr._read_ledger_rows(HELPER_RUN, local_env / "ledger.jsonl")
    return rows, rr._compute_model_dispatch(rows)["result"]


def test_helper_intent_forwards_only_to_the_entry_base_url(helper_proxy, local_env, monkeypatch):
    (status, _), calls, cloud_calls = _helper_scenario(helper_proxy, local_env, monkeypatch,
                                                       _ok_json, _post_json)
    assert status == 200 and len(calls) == 1 and cloud_calls == []
    assert calls[0]["headers"]["x-api-key"] == SENTINEL


def test_helper_request_writes_the_local_actor_row(helper_proxy, local_env, monkeypatch):
    _helper_pointer(local_env, monkeypatch)
    _helper_scenario(helper_proxy, local_env, monkeypatch, _ok_json, _post_json)
    rows, result = _helper_dispatch(local_env)
    assert len(rows) == 1
    row = rows[0]
    assert (row["endpoint_id"], row["hosting_location"], row["role"], row["correlation"],
            row["issue_number"]) == ("helper-local", "local", "actor", "inferred_pointer", ISSUE)
    assert row["payload_decision"]["secret_reference"] == HELPER_SECRET
    assert row["payload_decision"]["allow"] is True and row["failure_class"] is None
    assert (result["outcome"], result["endpoint_id"], result["hosting_location"], result["role"],
            result["correlation"]) == ("ok", "helper-local", "local", "actor", "inferred_pointer")


def _not_ready(status):
    async def handler(request):
        return web.json_response({"error": {"type": "loading_model"}}, status=status)
    return handler


@pytest.mark.parametrize("handler,timeouts,failure_class", [
    *[pytest.param(_not_ready(s), None, ma.ENDPOINT_UNREACHABLE, id=f"upstream-{s}")
      for s in ma.NOT_READY_STATUSES],
    pytest.param(_silent, {"time_to_first_token_seconds": 0.2}, ma.TIMEOUT_TIME_TO_FIRST_TOKEN,
                 id="timeout"),
    pytest.param(_answering_as("claude-opus-5-5"), None, ma.PROTOCOL_INCOMPATIBILITY,
                 id="wrong-served-model"),
])
def test_helper_failure_rows(helper_proxy, local_env, monkeypatch, handler, timeouts,
                             failure_class):
    _helper_pointer(local_env, monkeypatch)
    _helper_scenario(helper_proxy, local_env, monkeypatch, handler, _post_json,
                     timeouts=timeouts)
    rows, result = _helper_dispatch(local_env)
    assert [r["failure_class"] for r in rows] == [failure_class]
    assert rows[0]["hosting_location"] == "local" and rows[0]["endpoint_id"] == "helper-local"
    assert result["outcome"] == "failed" and result["failure_classes_seen"] == [failure_class]


def test_missing_pointer_gives_correlation_unknown(helper_proxy, local_env, monkeypatch):
    monkeypatch.setattr(mp, "CURRENT_RUN_PATH", local_env / "preview-helper" / "absent.json")
    _helper_scenario(helper_proxy, local_env, monkeypatch, _ok_json, _post_json)
    rows, _ = rr._read_ledger_rows("unknown", local_env / "ledger.jsonl")
    assert [r["correlation"] for r in rows] == ["unknown"]


def test_secret_value_is_in_no_row_artifact_or_log(helper_proxy, local_env, monkeypatch, capfd):
    monkeypatch.setattr(mp, "RAW_ARTIFACT_CAPTURE", True)
    artifacts = local_env / "artifacts"
    monkeypatch.setattr(mp, "RAW_ARTIFACT_DIR", artifacts)
    _helper_pointer(local_env, monkeypatch)
    answers = iter([_ok_json, _answering_as("claude-opus-5-5")])

    async def ok_then_wrong_model(request):
        return await next(answers)(request)

    async def send(client):
        return [await _post_json(client), await _post_json(client)]

    (results, calls, _) = _helper_scenario(helper_proxy, local_env, monkeypatch,
                                           ok_then_wrong_model, send, rows=2)
    assert [status for status, _ in results] == [200, 502] and len(calls) == 2
    assert all(c["headers"]["x-api-key"] == SENTINEL for c in calls)  # sent only upstream
    captured = [(local_env / "ledger.jsonl").read_text(),
                *(p.read_text() for p in artifacts.rglob("*") if p.is_file())]
    assert len(captured) >= 2
    out, err = capfd.readouterr()
    for text in (*captured, out, err, json.dumps(results)):
        assert SENTINEL not in text
```

  The helper entry lists `fixture_suite_version` and the three `invalidated_by` names #633
  requires, so it resolves `tested`. The ledger, the pointer file and Seq are redirected by the
  existing `local_env` fixture. Every endpoint is an in-process aiohttp stub or a
  `192.0.2.0/24` address that is never dialled.

- [ ] **Step 3.3: Verify they fail.**

```bash
PYTHONPATH=scripts python3 -m pytest tests/test_factory_model_proxy.py -q
```

  Expected: `35 failed`, every one a new #634 test. Most fail with `TypeError: main() takes 0
  positional arguments but 1 was given`. `test_module_entry_point_parses_its_own_arguments`
  fails because the module ignores `--intent` and tries to bind. Three guards **pass**:
  `test_no_intent_argument_still_refuses_a_local_entry`, `test_bare_main_ignores_sys_argv`
  and `test_intent_is_not_read_from_the_environment` (spec Test Plan: guards cannot be written
  failing-first). `test_current_run_dir_selects_the_helper_pointer` is also a guard and passes.

- [ ] **Step 3.4: Module docstring and import.** In `scripts/factory_core/model_proxy.py`
  replace the docstring's last two lines

```
variable named by the entry's secret_reference. The proxy service receives none of these
today; delivering them is #587's.
```

  with

```
variable named by the entry's secret_reference. The proxy service receives them for the
classify-preview-helper lane only, through run-compose.yml's preview-helper-proxy service
(#634), whose literal entrypoint adds `--intent classify-preview-helper`; dispatching that
intent at all is still gated on #587.
```

  and add `import argparse` as the first stdlib import (directly above `import asyncio`).

- [ ] **Step 3.5: `main(argv=None)`.** Replace the whole of `main()` and the
  `if __name__ == "__main__":` block

```python
def main() -> None:
    destination = None
    endpoint_id = os.environ.get("FACTORY_MODEL_ENDPOINT")
    if endpoint_id:
        # Lazy: the default cloud path never loads the adapter or registry code (#558).
        from factory_core.providers import model_adapter, model_endpoint
        try:
            destination = model_adapter.resolve_local_destination(
                model_endpoint.staged_registry_dir(), endpoint_id)
        except model_adapter.DestinationError as e:
            for problem in e.problems:
                print(f"factory-model-proxy: {problem}", file=sys.stderr)
            sys.exit(1)
    sweep_raw_artifacts()
    web.run_app(create_app(destination), port=PORT)


if __name__ == "__main__":
    main()
```

  with

```python
def _parse_args(argv):
    parser = argparse.ArgumentParser(prog="factory_core.model_proxy", allow_abbrev=False)
    # Written only in run-compose.yml's preview-helper-proxy entrypoint (#634); never read from
    # the environment. Present at all (even "") means a helper proxy: no cloud fallback.
    parser.add_argument("--intent", default=None,
                        help="the dispatch intent this proxy serves (preview-helper-proxy only)")
    return parser.parse_args(argv)


def main(argv=None) -> None:
    # argv None means no arguments, never sys.argv: callers that pass nothing get today's
    # behaviour (#634). The module entry point below passes sys.argv[1:].
    args = _parse_args([] if argv is None else argv)
    destination = None
    endpoint_id = os.environ.get("FACTORY_MODEL_ENDPOINT")
    if args.intent is not None and not endpoint_id:
        print(f"factory-model-proxy: --intent {args.intent!r} is given but "
              "FACTORY_MODEL_ENDPOINT is not set — a helper proxy never falls back to the "
              "cloud default", file=sys.stderr)
        sys.exit(1)
    if endpoint_id:
        # Lazy: the default cloud path never loads the adapter or registry code (#558).
        from factory_core.providers import model_adapter, model_endpoint
        try:
            destination = model_adapter.resolve_local_destination(
                model_endpoint.staged_registry_dir(), endpoint_id, intent=args.intent)
        except model_adapter.DestinationError as e:
            for problem in e.problems:
                print(f"factory-model-proxy: {problem}", file=sys.stderr)
            sys.exit(1)
        if args.intent is not None:
            # A helper proxy serves one verified local entry or nothing (#634): a cloud entry
            # would otherwise bind as a passthrough to UPSTREAM.
            problem = None
            if destination["hosting_location"] != "local":
                problem = (f"--intent {args.intent!r} needs a local endpoint, but "
                           f"'{destination['endpoint_id']}' is "
                           f"hosting_location={destination['hosting_location']} — a helper "
                           "proxy never forwards to the cloud default")
            elif (destination["upstream"].rstrip("/")
                  == model_endpoint.HELPER_PROXY_BASE_URL.rstrip("/")):
                problem = (f"endpoint '{destination['endpoint_id']}' base_url is the helper "
                           "proxy's own address — a proxy must never forward to its own address")
            if problem:
                print(f"factory-model-proxy: {problem}", file=sys.stderr)
                sys.exit(1)
    sweep_raw_artifacts()
    web.run_app(create_app(destination), port=PORT)


if __name__ == "__main__":
    main(sys.argv[1:])
```

  With no `--intent`, the code path is today's line for line. The only difference is the
  `intent=None` passed to `resolve_local_destination()`, which forwards `None`.
  `factory-model-proxy`'s `entrypoint:` gives no argument, so it is unaffected.

- [ ] **Step 3.6: Verify.**

```bash
PYTHONPATH=scripts python3 -m pytest tests/test_factory_model_proxy.py tests/test_provider_model_adapter.py tests/test_provider_model_endpoint.py -q
```

  Expected: all pass. That includes the three existing bare `mp.main()` callers
  (`test_unset_endpoint_serves_the_cloud_default_without_loading_the_adapter`,
  `test_a_verified_cloud_entry_starts_with_its_destination` and the digest/startup tests),
  because `argv=None` parses no arguments, never pytest's `sys.argv`.

- [ ] **Step 3.7: Commit.**

```bash
git add scripts/factory_core/model_proxy.py tests/test_factory_model_proxy.py
git commit -m "feat(#634): model_proxy --intent serves one verified local entry or exits before binding" \
  -m "A local 500 still carries no failure class (NOT_READY_STATUSES is 502/503/504 only), so the 5xx criterion is tested on those three; reclassifying 500 is out of scope (plan Deviation 3)."
```

---

## Task 4: The `preview-helper-proxy` compose service

**Files:** `tests/test_provider_env_passthrough.py`, `run-compose.yml`,
`tests/test_run_compose.sh`, `tests/test_model_proxy_compose.sh`

- [ ] **Step 4.1: Make the compose-environment guard service-aware (Deviation 2).** In
  `tests/test_provider_env_passthrough.py`, add `import yaml` as its own third-party group
  after `from pathlib import Path`:

```python
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
```

  and replace

```python
def test_forwarded_vars_not_also_in_compose_environment():
    compose = (REPO / "run-compose.yml").read_text(encoding="utf-8")
    for name in _forwarded():
        assert not re.search(rf"^\s+{name}:", compose, re.M), \
            f"{name} in run-compose.yml environment: would override or blank env_file"
```

  with

```python
def test_forwarded_vars_not_also_in_compose_environment():
    services = yaml.safe_load((REPO / "run-compose.yml").read_text(encoding="utf-8"))["services"]
    forwarded = _forwarded()
    for service, spec in services.items():
        clash = set(spec.get("environment") or {}) & forwarded
        if service == "preview-helper-proxy":
            # #634: no env_file and never a dispatch() target; its FACTORY_MODEL_ENDPOINT key
            # is interpolated from PREVIEW_HELPER_ENDPOINT, never forwarded.
            clash -= {"FACTORY_MODEL_ENDPOINT"}
        assert not clash, \
            f"{sorted(clash)} in run-compose.yml {service} environment: would override or blank env_file"
```

  `re` stays imported: other tests in the file use it.

```bash
PYTHONPATH=scripts python3 -m pytest tests/test_provider_env_passthrough.py -q
```

  Expected: all pass, before and after the service exists (verified both ways). With the old
  regex form, adding the service fails this test with
  `FACTORY_MODEL_ENDPOINT in run-compose.yml environment` (verified).

- [ ] **Step 4.2: Write the failing compose assertions.** In `tests/test_run_compose.sh`,
  insert this block directly **above** the line
  `# 4) Parse check via docker compose config (skipped if docker not available)`. It needs no
  Docker: it parses the source YAML (PyYAML is installed in the CI job).

```bash
# 3f) preview-helper-proxy (#634), asserted on the parsed SOURCE: a render carries
# `command: null` for every service and merges an env_file into environment.
python3 - "$RC" <<'PY' || { echo "FAIL: preview-helper-proxy source-definition checks"; exit 1; }
import sys, yaml
services = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))["services"]
name = "preview-helper-proxy"
svc = services.get(name)
assert svc is not None, f"{name} service missing"
# helper_proxy_source_has_no_forbidden_keys
bad = sorted({"env_file", "depends_on", "restart", "container_name", "command"} & set(svc))
assert not bad, f"{name} must not declare {bad}"
assert "MODEL_PROXY_UPSTREAM" not in (svc.get("environment") or {}), \
    f"{name} must not set MODEL_PROXY_UPSTREAM"
assert svc.get("profiles") == [name], f"{name} must be only under its own profile"
# helper_secret_has_one_fixed_name (spec requirement 7, decision D2): the mapping form only,
# so no variable name is chosen at dispatch time, and the secret is the one pass-through key.
env = svc.get("environment")
assert isinstance(env, dict), \
    f"{name} environment must be a mapping: the list form lets a variable name be interpolated"
passthrough = sorted(k for k, v in env.items() if v is None)
assert passthrough == ["FACTORY_HELPER_MODEL_SECRET"], \
    f"{name} must pass through exactly FACTORY_HELPER_MODEL_SECRET, not {passthrough}"
# decision D1: dark-factory-net only until #587 adds the helper's internal network
assert svc.get("networks") == ["dark-factory-net"], f"{name} must be on dark-factory-net only"
# helper_proxy_entrypoint_is_the_literal_intent
expected = ["python3", "-m", "factory_core.model_proxy", "--intent", "classify-preview-helper"]
assert svc.get("entrypoint") == expected, f"{name} entrypoint must be exactly {expected}"
assert not any("$" in token for token in svc["entrypoint"]), f"{name} entrypoint is interpolated"
# dark_factory_does_not_depend_on_helper_proxy
for other, spec in services.items():
    deps = spec.get("depends_on") or {}
    assert name not in deps, f"{other} depends_on {name}"
    nets = spec.get("networks") or {}
    if isinstance(nets, dict):
        for net in nets.values():
            assert name not in ((net or {}).get("aliases") or []), \
                f"{other} declares a network alias {name}"
PY
echo "PASS: preview-helper-proxy source definition (no forbidden keys, literal intent, no depends_on/alias)"
```

  In the same file, inside the Docker branch, insert this block directly **above** its
  closing lines

```bash
  [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"
else
  echo "SKIP: docker not available — skipping compose config parse check"
```

  that is, after the existing `PASS: docker compose config parses run-compose.yml` check and
  while the scratch `.archon/.env` still exists:

```bash
  # 4b) helper_proxy_env_is_exactly_the_allow_list (#634): rendered with its profile, the
  # five scheduler-side sources set to fixture values and every forbidden name set in the
  # caller's environment. The render is piped straight into the parser, never echoed.
  CLOUD_CREDS=$(PYTHONPATH="$REPO_ROOT/scripts" python3 -c \
    "from factory_core.providers import model_endpoint as me; print(' '.join(me.CLOUD_CREDENTIALS))")
  # shellcheck disable=SC2086
  # DOCKER_HOST keeps a working value: the CLI itself reads it.
  if ! env DOCKER_HOST="${DOCKER_HOST:-unix:///var/run/docker.sock}" \
      $(for n in GH_TOKEN GITLAB_TOKEN JIRA_TOKEN $CLOUD_CREDS; do printf '%s=forbidden-fixture ' "$n"; done) \
      PREVIEW_HELPER_ENDPOINT=helper-fixture \
      FACTORY_MODEL_REGISTRY_SHA256=0000000000000000000000000000000000000000000000000000000000000000 \
      PREVIEW_HELPER_ENDPOINT_BASE_URL=http://192.0.2.10:8080 \
      PREVIEW_HELPER_ENDPOINT_SERVED_NAME=served-fixture \
      FACTORY_HELPER_MODEL_SECRET=plain-fixture-not-a-secret \
      docker compose -f "$RC" --profile preview-helper-proxy config --format json 2>/dev/null \
    | python3 -c '
import json, sys
env = json.load(sys.stdin)["services"]["preview-helper-proxy"]["environment"]
served = "served-fixture"
expected = {
    "PYTHONPATH": "/opt/dark-factory/scripts", "MODEL_PROXY_PORT": "8787",
    "CURRENT_RUN_DIR": "/var/lib/dark-factory/preview-helper",
    "FACTORY_MODEL_ENDPOINT": "helper-fixture", "FACTORY_MODEL_REGISTRY_SHA256": "0" * 64,
    "ANTHROPIC_BASE_URL": "http://192.0.2.10:8080",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": served, "ANTHROPIC_DEFAULT_SONNET_MODEL": served,
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": served, "ANTHROPIC_DEFAULT_FABLE_MODEL": served,
    "FACTORY_HELPER_MODEL_SECRET": "plain-fixture-not-a-secret",
}
if set(env) != set(expected):
    sys.exit(f"rendered keys differ: extra {sorted(set(env) - set(expected))}, "
             f"missing {sorted(set(expected) - set(env))}")
wrong = sorted(k for k in expected if env[k] != expected[k])
if wrong:
    sys.exit(f"rendered values differ for {wrong}")
'; then
    echo "FAIL: preview-helper-proxy rendered environment is not exactly the eleven allowed keys"
    [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"
    exit 1
  fi
  echo "PASS: preview-helper-proxy renders exactly its eleven environment keys"

  # 4c) new_service_does_not_change_other_renders (#634, regression guard): a copy of the
  # file without the preview-helper-proxy block renders identically for every profile set
  # scheduler.sh dispatches with. Both copies sit in scratch dirs of the same layout.
  DIFF_DIR=$(mktemp -d)
  mkdir -p "$DIFF_DIR/with/.archon" "$DIFF_DIR/without/.archon"
  touch "$DIFF_DIR/with/.archon/.env" "$DIFF_DIR/without/.archon/.env"
  cp "$RC" "$DIFF_DIR/with/run-compose.yml"
  awk '/^  preview-helper-proxy:$/{skip=1} skip && /^[^ ]/{skip=0} !skip' "$RC" \
    > "$DIFF_DIR/without/run-compose.yml"
  if grep -q 'preview-helper-proxy:' "$DIFF_DIR/without/run-compose.yml"; then
    echo "FAIL: could not strip the preview-helper-proxy block for the differential render"
    rm -rf "$DIFF_DIR"; [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"; exit 1
  fi
  for profiles in "" "--profile factory" "--profile factory --profile factory-model-proxy"; do
    # shellcheck disable=SC2086
    if ! WITH=$(docker compose -f "$DIFF_DIR/with/run-compose.yml" $profiles config 2>&1) \
       || ! WITHOUT=$(docker compose -f "$DIFF_DIR/without/run-compose.yml" $profiles config 2>&1) \
       || [ "$WITH" != "$WITHOUT" ]; then
      echo "FAIL: preview-helper-proxy changes the render for profiles [${profiles:-none}]"
      rm -rf "$DIFF_DIR"; [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"; exit 1
    fi
  done
  rm -rf "$DIFF_DIR"
  echo "PASS: preview-helper-proxy leaves every other render byte-identical"

  # 4d) helper_secret_is_not_in_rendered_config (#634): with a sentinel in the caller's
  # environment, the uninterpolated helper render and (guard) an interpolated factory render
  # never carry it. No test captures an interpolated helper-profile render with it set.
  SENTINEL_VALUE="sk-sentinel-helper-secret-never-rendered"
  if FACTORY_HELPER_MODEL_SECRET="$SENTINEL_VALUE" \
       docker compose -f "$RC" --profile preview-helper-proxy config --no-interpolate 2>&1 \
       | grep -qF "$SENTINEL_VALUE" \
     || FACTORY_HELPER_MODEL_SECRET="$SENTINEL_VALUE" \
       docker compose -f "$RC" --profile factory config 2>&1 | grep -qF "$SENTINEL_VALUE"; then
    echo "FAIL: the helper secret value appears in a rendered config"
    [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"
    exit 1
  fi
  echo "PASS: the helper secret is in no uninterpolated or factory-profile render"
```

  (4c's `awk` drops the service body but leaves its comment lines behind in the "without"
  copy. That is intended: comments never reach a render, and the `grep` guard catches a failed
  strip.)

  In `tests/test_model_proxy_compose.sh`, insert directly **after** the line
  `echo "PASS: factory-model-proxy present when its profile is explicitly enabled"`:

```bash
# 3) helper_proxy_absent_without_its_profile (#634, regression guard): preview-helper-proxy
#    is never selected by the profile sets scheduler.sh dispatches with — dark-factory does
#    not pull it in — and appears only under its own profile.
for profiles in "" "--profile factory" "--profile factory --profile factory-model-proxy"; do
  # shellcheck disable=SC2086
  SERVICES=$(docker compose -f "$RC" $profiles config --services 2>&1)
  if echo "$SERVICES" | grep -qx "preview-helper-proxy"; then
    echo "FAIL: preview-helper-proxy selected by profiles [${profiles:-none}] (must be opt-in)"
    [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"
    exit 1
  fi
done
HELPER_SERVICES=$(docker compose -f "$RC" --profile preview-helper-proxy config --services 2>&1)
if [ "$HELPER_SERVICES" != "preview-helper-proxy" ]; then
  echo "FAIL: --profile preview-helper-proxy must select exactly preview-helper-proxy"
  [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"
  exit 1
fi
echo "PASS: preview-helper-proxy present only under its own profile"
```

- [ ] **Step 4.3: Verify they fail.**

```bash
bash tests/test_run_compose.sh; echo "rc=$?"
bash tests/test_model_proxy_compose.sh; echo "rc=$?"
```

  Expected: `test_run_compose.sh` prints `AssertionError: preview-helper-proxy service missing`
  and `FAIL: preview-helper-proxy source-definition checks`, `rc=1`.
  `test_model_proxy_compose.sh` prints
  `FAIL: --profile preview-helper-proxy must select exactly preview-helper-proxy`, `rc=1`.

- [ ] **Step 4.4: Add the service.** In `run-compose.yml`, insert this block between the end
  of the `factory-model-proxy` service (its `      - factory-model-proxy` profile line, then a
  blank line) and the top-level `networks:` key. Add nothing else, anywhere:

```yaml
  # Per-dispatch proxy for the classify-preview-helper lane (#634); the only path from the
  # helper container to its local endpoint. Started and removed with each helper dispatch by
  # #587's dispatcher, never by this file: no depends_on in either direction, no restart, no
  # container_name. Environment comes from the scheduler's at dispatch time (no env_file):
  # PREVIEW_HELPER_* and FACTORY_HELPER_MODEL_SECRET are scheduler-only and must never be
  # added to PROVIDER_PASSTHROUGH_VARS. ${VAR:-} throughout, because interpolation covers the
  # whole file and :? would fail every ordinary dispatch. The --intent pair is a literal.
  # An interpolated `docker compose config` with this profile prints the secret — never run
  # or log one. See docs/dark-factory-model-proxy-rollout.md.
  preview-helper-proxy:
    image: ${FACTORY_IMAGE:-ghcr.io/omniscient/dark-factory:latest}
    entrypoint: ["python3", "-m", "factory_core.model_proxy", "--intent", "classify-preview-helper"]
    environment:
      PYTHONPATH: /opt/dark-factory/scripts
      MODEL_PROXY_PORT: "8787"
      CURRENT_RUN_DIR: /var/lib/dark-factory/preview-helper
      FACTORY_MODEL_ENDPOINT: ${PREVIEW_HELPER_ENDPOINT:-}
      FACTORY_MODEL_REGISTRY_SHA256: ${FACTORY_MODEL_REGISTRY_SHA256:-}
      ANTHROPIC_BASE_URL: ${PREVIEW_HELPER_ENDPOINT_BASE_URL:-}
      ANTHROPIC_DEFAULT_OPUS_MODEL: ${PREVIEW_HELPER_ENDPOINT_SERVED_NAME:-}
      ANTHROPIC_DEFAULT_SONNET_MODEL: ${PREVIEW_HELPER_ENDPOINT_SERVED_NAME:-}
      ANTHROPIC_DEFAULT_HAIKU_MODEL: ${PREVIEW_HELPER_ENDPOINT_SERVED_NAME:-}
      ANTHROPIC_DEFAULT_FABLE_MODEL: ${PREVIEW_HELPER_ENDPOINT_SERVED_NAME:-}
      FACTORY_HELPER_MODEL_SECRET:
    volumes:
      - scheduler_state:/var/lib/dark-factory
    networks:
      - dark-factory-net
    profiles:
      - preview-helper-proxy
```

  The definition is the spec's requirement 5 verbatim, plus the comment. Check that nothing
  outside the block moved:

```bash
git diff -U0 run-compose.yml | grep -E '^[-+][^-+]' | grep -c '^-'
```

  Expected: `0` (no removed lines).

- [ ] **Step 4.5: Verify.**

```bash
bash tests/test_run_compose.sh && bash tests/test_model_proxy_compose.sh && bash tests/test_build_network_compose.sh && bash tests/test_model_proxy_smoke.sh
PYTHONPATH=scripts python3 -m pytest tests/test_provider_env_passthrough.py -q
```

  Expected (with Docker): every line `PASS:`. The new ones are:
  `PASS: preview-helper-proxy source definition (no forbidden keys, literal intent, no depends_on/alias)`,
  `PASS: preview-helper-proxy renders exactly its eleven environment keys`,
  `PASS: preview-helper-proxy leaves every other render byte-identical`,
  `PASS: the helper secret is in no uninterpolated or factory-profile render` and
  `PASS: preview-helper-proxy present only under its own profile`. The pytest run passes.
  Without Docker the render checks print `SKIP:` as today, and the source-definition check
  still runs. Mutation-checked on the scratch copy: adding `env_file`, `restart`,
  `MODEL_PROXY_UPSTREAM`, `SEQ_URL` or `GH_TOKEN` to the service, interpolating the intent
  token, or adding a `depends_on: preview-helper-proxy` to `dark-factory` each fails one of
  these checks. So do (plan review) writing `environment:` in the list form with an
  interpolated pass-through name, and attaching the service to a second network.

- [ ] **Step 4.6: Commit.**

```bash
git add run-compose.yml tests/test_run_compose.sh tests/test_model_proxy_compose.sh tests/test_provider_env_passthrough.py
git commit -m "feat(#634): add the per-dispatch preview-helper-proxy compose service" \
  -m "Owner-authorised (2026-11-26, recorded on #587): one new service under its own profile; dark-factory and factory-model-proxy unchanged. test_forwarded_vars_not_also_in_compose_environment now parses the YAML and exempts only this service's FACTORY_MODEL_ENDPOINT key (plan Deviation 2)."
```

---

## Task 5: Documentation (spec requirement 11)

**Files:** `docs/configuration.md`, `docs/dark-factory-model-proxy-rollout.md`

The module docstring (the third copy of the "delivering them is #587's" sentence) was updated
in Task 3, Step 3.4. No new runbook file is added.

- [ ] **Step 5.1: `docs/configuration.md`, the `secret_reference` row.** In "Model endpoint
  registry (#557)", replace the table row

```
| the variable a `secret_reference` names | cloud entry: the run container's `.archon/.env` (for example `ANTHROPIC_API_KEY`); local entry: not `.archon/.env`; the model proxy's own environment, delivered by #587 | run-container preflight (presence only); the model proxy, which sends it as the local request's only `x-api-key` (#558) | the registry stores the name, never the value |
```

  with

```
| the variable a `secret_reference` names | cloud entry: the run container's `.archon/.env` (for example `ANTHROPIC_API_KEY`); local entry: not `.archon/.env`. For the `classify-preview-helper` lane, the only local lane: `FACTORY_HELPER_MODEL_SECRET` in the scheduler's own environment, which compose interpolation delivers to the `preview-helper-proxy` service alone (#634) | run-container preflight: present for a cloud entry, absent for the helper lane; the model proxy, which sends it as the local request's only `x-api-key` (#558) | the registry stores the name, never the value. The helper's entry must name `secret_reference: FACTORY_HELPER_MODEL_SECRET`; any other name fails closed. The other four scheduler-side variables `preview-helper-proxy` interpolates are `PREVIEW_HELPER_ENDPOINT`, `FACTORY_MODEL_REGISTRY_SHA256`, `PREVIEW_HELPER_ENDPOINT_BASE_URL` and `PREVIEW_HELPER_ENDPOINT_SERVED_NAME`; none of the five is in `PROVIDER_PASSTHROUGH_VARS`. An interpolated `docker compose config` with the `preview-helper-proxy` profile prints the secret: never run or log one |
```

- [ ] **Step 5.2: `docs/configuration.md`, the run-side helper rule (Deviation 4).** Directly
  after #633's sentence `` `model-endpoints hosting-check`) still refuses every local selection, and nothing dispatches
  the helper intent until #587.`` (the end of the paragraph #633's Step 8.1 rewrote), append
  to the same paragraph:

```
A helper run reaches its endpoint only through the
`preview-helper-proxy` service (#634), so for that intent the run container's
`ANTHROPIC_BASE_URL` must be exactly `http://preview-helper-proxy:8787`
(`HELPER_PROXY_BASE_URL`) instead of the entry's `base_url`, and the entry's secret must be
absent from it.
```

- [ ] **Step 5.3: `docs/dark-factory-model-proxy-rollout.md`.** In "Local destination:
  supported and uncertified modes (#558)", replace the opening paragraph

```
Spec: `docs/archive/2026-11-25-hybrid-runtime-adapter-design.md`. Nothing here is
reachable in production yet. #557's `_NO_LOCAL_DISPATCH` still rejects every local selection,
and the proxy service receives none of the variables a local selection needs. Delivering them
is #587's. The adapter is built and hermetically tested so #587 can enable it.
```

  with

```
Spec: `docs/archive/2026-11-25-hybrid-runtime-adapter-design.md`. Nothing here is
reachable in production yet. #557's `_NO_LOCAL_DISPATCH` rejects every local selection except
one made for the `classify-preview-helper` intent (#633), and nothing dispatches that intent
until #587. `factory-model-proxy` never receives the variables a local selection needs: the
helper lane gets its own proxy service, described next. The adapter is built and hermetically
tested so #587 can enable it.

**The helper lane's proxy (#634).** `run-compose.yml`'s `preview-helper-proxy` service, under
its own `preview-helper-proxy` profile, runs this module with the literal argument
`--intent classify-preview-helper` in its `entrypoint:`. It is the only path from the helper
container to the local endpoint. Nothing in this repository starts it: #587's dispatcher
starts it for each helper dispatch, removes it when the helper exits, and refuses a dispatch
that finds a stale one. Its environment is interpolated from the scheduler's at that moment,
with no `env_file`, from five scheduler-side variables: `PREVIEW_HELPER_ENDPOINT` (becomes
`FACTORY_MODEL_ENDPOINT`), `FACTORY_MODEL_REGISTRY_SHA256`, `PREVIEW_HELPER_ENDPOINT_BASE_URL`
(becomes `ANTHROPIC_BASE_URL`), `PREVIEW_HELPER_ENDPOINT_SERVED_NAME` (becomes the four
`ANTHROPIC_DEFAULT_*_MODEL` aliases) and `FACTORY_HELPER_MODEL_SECRET`, plus three literals:
`PYTHONPATH`, `MODEL_PROXY_PORT=8787` and `CURRENT_RUN_DIR=/var/lib/dark-factory/preview-helper`.
With `--intent` given, startup also exits non-zero before binding if `FACTORY_MODEL_ENDPOINT`
is empty, if the resolved entry is not `hosting_location: local`, or if its `base_url` is the
proxy's own address: a helper proxy never falls back to the cloud default. The helper's
registry entry must name `secret_reference: FACTORY_HELPER_MODEL_SECRET`; any other name fails
closed. The helper run container sets `ANTHROPIC_BASE_URL=http://preview-helper-proxy:8787` and
holds no secret, and its preflight requires both. Ledger rows go to the shared
`request-ledger.jsonl` in the usual shape, correlated from
`/var/lib/dark-factory/preview-helper/current-run.json`. An interpolated `docker compose config`
with this profile prints the secret: never run or log one. While the proxy runs, any container
on `dark-factory-net` can reach it by name; #587 adds the helper's internal network.
```

  Keep the rest of the section unchanged. `test_rollout_doc_lists_exactly_the_supported_and_refused_local_modes`
  reads this section and must stay green.

- [ ] **Step 5.4: Verify.**

```bash
python3 scripts/docs_refs_check.py docs/configuration.md docs/dark-factory-model-proxy-rollout.md
PYTHONPATH=scripts python3 -m pytest tests/test_docs_refs_check.py tests/test_docs_conformance_reference_docs.py tests/test_implement_doc_impact.py tests/test_factory_model_proxy.py -q -k "doc or rollout"
grep -rn "delivering them is #587\|delivered by #587" docs/configuration.md docs/dark-factory-model-proxy-rollout.md scripts/factory_core/model_proxy.py
```

  Expected: `Docs reference check passed for 2 doc(s).`; the pytest run passes; the grep prints
  nothing.

- [ ] **Step 5.5: Commit.**

```bash
git add docs/configuration.md docs/dark-factory-model-proxy-rollout.md
git commit -m "docs(#634): document preview-helper-proxy, its five scheduler-side variables and the helper run-side rule"
```

---

## Task 6: Full verification

**Files:** none

- [ ] **Step 6.1: Whole Python suite.**

```bash
PYTHONPATH=scripts python3 -m pytest tests/ -q
```

  Expected: all pass (4254 on the scratch copy described above).

- [ ] **Step 6.2: The CI-wired compose and proxy shell tests.**

```bash
for t in test_run_compose test_model_proxy_compose test_build_network_compose test_model_proxy_smoke test_provider_env_flags; do bash "tests/$t.sh" > /dev/null && echo "ok $t" || echo "FAIL $t"; done
```

  Expected: `ok` for each. `test_provider_env_flags.sh` is unaffected by this change. If it
  fails, re-run it on `origin/main`: it reads host environment variables, and the scratch
  sandbox failed it on unmodified `main` too.

- [ ] **Step 6.3: Scope check.**

```bash
git fetch origin main
git diff --name-only origin/main HEAD
git diff origin/main HEAD -- run-compose.yml | grep -E '^-[^-]' | wc -l
```

  Two-dot, not `origin/main...HEAD` (factory memory: the three-dot form reports files `main`
  changed independently as false out-of-scope hits).

  Expected: exactly the thirteen files of the File Structure table (eleven rows; two rows name
  two files each), plus the plan/spec under `docs/superpowers/`, and `0` removed lines in `run-compose.yml`. Nothing under `deploy/**`,
  `.github/workflows/**`, `scheduler.sh`, `entrypoint.sh` or `scripts/provider_env.sh`.

- [ ] **Step 6.4: Safety statements to carry into the PR body.** The PR body must say:
  - Merging activates nothing. #633 keeps `classify-preview-helper` "not available", nothing
    starts `preview-helper-proxy`, and no scheduler variable is set by this change.
  - The residual risks the spec records under "Trust boundary and residual risk" remain:
    a reachable name on `dark-factory-net` while the proxy runs, Docker-API visibility of
    its environment, and a self-attested environment check. So does decision D1 (no dedicated
    network until #587).
  - The blast-radius gate is expected to hold on `scripts/factory_core/model_proxy.py`
    (hotspot) for signed human approval.
  - Deviations 1–4 of this plan.

---

## Interface delivered to #587

Unchanged from the spec. It is restated so the PR and #587 can cite one place:

- Service and profile: `preview-helper-proxy` (both names). Constant run-side address:
  `http://preview-helper-proxy:8787` (`model_endpoint.HELPER_PROXY_BASE_URL`).
- Scheduler-side variables interpolated at dispatch time, none in `PROVIDER_PASSTHROUGH_VARS`:
  `PREVIEW_HELPER_ENDPOINT`, `FACTORY_MODEL_REGISTRY_SHA256` (the helper dispatcher must stage
  the registry and export the digest itself), `PREVIEW_HELPER_ENDPOINT_BASE_URL`,
  `PREVIEW_HELPER_ENDPOINT_SERVED_NAME`, `FACTORY_HELPER_MODEL_SECRET`.
- The helper's registry entry must declare `secret_reference: FACTORY_HELPER_MODEL_SECRET`.
- Correlation pointer: `/var/lib/dark-factory/preview-helper/current-run.json`. The helper
  container sets `CURRENT_RUN_DIR=/var/lib/dark-factory/preview-helper`, and #587 creates the
  directory and mounts only it writable.
- Lifetime: #587's dispatcher refuses to dispatch when any container with label
  `com.docker.compose.service=preview-helper-proxy` exists. It starts the proxy so that the
  service name resolves (`up -d`, or `run --use-aliases`), and removes it whatever way the
  helper ends.

## Self-review

- `**Issue:** #634` sits directly under the title.
- No placeholders: every step has its literal code, exact paths, commands and expected output.
- Spec coverage: requirements 1–3 → Tasks 2–3; 4 → Task 1; 5, 9 (this ticket's part) → Task 4;
  6–8 → Task 4 (service) and Task 5 (docs); 10 → Task 3's ledger tests (no code change);
  11 → Tasks 3 and 5. Every row of the spec's Test Plan table has its named test(s):
  Task 4 for the compose rows, Task 3 for the proxy, ledger, secret and cloud-credential rows,
  Task 1 for the run-side rows, Task 2 for the adapter row.
- Every test the spec marks *guard* is marked "passes before" in the relevant Verify step.
  All other new tests were run failing first.
