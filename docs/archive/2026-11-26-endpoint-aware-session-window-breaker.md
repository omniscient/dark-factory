# Endpoint-Aware Session-Window Detection and Breaker Signature — Implementation Plan

**Issue:** #586

**Spec:** `docs/superpowers/specs/2026-11-26-endpoint-aware-session-window-breaker-design.md`
(operator-approved at `43589c8`, amendments A1-A7 applied)

**Goal:** A rate-limit or exhaustion signal from a verified-local model endpoint never writes the
factory-wide pause sentinel and never falls into `entrypoint.sh`'s legacy sleep loop. It becomes a
counted `substantive:endpoint_unreachable:<rc>` failure in the existing single signature bucket,
so two in a row trip the breaker. The default/cloud path stays byte-identical, including argv.

**Architecture:** `entrypoint.sh` resolves `DF_ENDPOINT_HOSTING` (`local`|`cloud`) once, right
after the provider check and before the clone, through the **baked** providers CLI's new
`model-endpoints hosting` verb. That verb is a thin wrapper over the unmodified
`model_endpoint.load_verified_entry()`, so the `_NO_LOCAL_DISPATCH` block still applies and it
prints `hosting=cloud` in production until #587. Three call sites read
`${DF_ENDPOINT_HOSTING:-cloud}`:
1. `_handle_session_window_pause` passes `--hosting-location local` to `session-window-check`.
   `check_and_pause` then writes no sentinel and the CLI prints `matched=false`.
2. The main loop's `rate-limit-match` check gets a bash guard. No Python change there.
3. `_write_error_signature` passes `--hosting-location local` to `error-signature-write`.
   `classify()` then returns `substantive:endpoint_unreachable:<rc>` above the delivery-failure
   check.

On the cloud path, no new token is passed anywhere.

**Tech Stack:** bash (`entrypoint.sh`), Python 3.12 (`scripts/factory_core/`), pytest, the
repo's plain-bash `tests/test_*.sh` harnesses.

---

## Facts re-verified against `main` (spec Requirement 10 / issue constraint 10)

- **Base:** current `origin/main` at `cf38d4d`. That includes #559 (PR #617) and #560
  (PRs #619/#620), which merged after this refine branch forked at `06afd26`. #559 added 17
  lines to `entrypoint.sh` around line 157 (the `ROLE_FIELD` block in the `current-run.json`
  write). It also changed `model_adapter.py` and `tests/test_entrypoint_current_run.sh`, which now
  sources `entrypoint.sh` several times. None of the five production files this plan edits
  changed otherwise. All numbers below are for `cf38d4d`. If `main` moves again, re-derive them
  with `grep -n`. Every edit step anchors on exact text, not on line numbers.
- #557 and #558 are merged (`4c65b47 docs: archive spec/plan for issue #558`). The issue body's
  `Depends on: #558` is satisfied.
- `scripts/provider_env.sh:29` forwards `FACTORY_MODEL_ENDPOINT` to **every** dispatch.
  `FACTORY_MODEL_REGISTRY_SHA256` is added per dispatch by `dispatch()`
  (`provider_env.sh:55-66`). Only `_NO_LOCAL_DISPATCH`
  (`scripts/factory_core/providers/model_endpoint.py:649` in `_local_problems`, and `:722` in
  `scheduler_preflight`) plus the `entrypoint.sh:10` run preflight block local today.
- Citations checked against current `main`:

  | File | Line | What is there |
  |---|---|---|
  | `entrypoint.sh` | 10 | preflight |
  | `entrypoint.sh` | 19 | `CLONE_DIR=` |
  | `entrypoint.sh` | 295 | `_handle_session_window_pause()` |
  | `entrypoint.sh` | 302 | `session-window-check` call |
  | `entrypoint.sh` | 316 | `[ "$matched" = "true" ] \|\| return 1` |
  | `entrypoint.sh` | 435 | `_write_error_signature()` |
  | `entrypoint.sh` | 483 | `error-signature-write` call |
  | `entrypoint.sh` | 566 | `on_failure()` |
  | `entrypoint.sh` | 664, 682 | `on_failure`'s two `_write_error_signature` calls |
  | `entrypoint.sh` | 712 | `trap on_failure ERR` |
  | `entrypoint.sh` | 830 | `ENTRYPOINT_SOURCE_ONLY` return |
  | `entrypoint.sh` | 842 | `git clone` |
  | `entrypoint.sh` | 1103 | `while true; do` |
  | `entrypoint.sh` | 1119 | `rate-limit-match` call |
  | `entrypoint.sh` | 1151 | the only `sleep` in the file |
  | `entrypoint.sh` | 1155-1156 | the loop's `run_post_mortem` / `_write_error_signature` |
  | `scripts/factory_core/cli.py` | 151, 178, 194 | the three handlers |
  | `scripts/factory_core/cli.py` | 426, 433, 437 | the three subparsers |
  | `scripts/factory_core/providers/cli.py` | 241 | `_model_endpoints_show` |
  | `scripts/factory_core/providers/cli.py` | 433-444 | `model-endpoints` group |
  | `scripts/factory_core/providers/model_endpoint.py` | 686-710 | `load_verified_entry` |
  | `scripts/factory_core/providers/model_adapter.py` | 34, 44-48 | `ENDPOINT_UNREACHABLE`, `FAILURE_CLASSES` |
  | `tests/test_scheduler.sh` | 232-269 | section B4 (`rollback_paused_retry`) |

- **Observed while authoring this plan (not in the spec):** in production the ERR trap handles a
  failed `archon workflow run` first. Bash fires `ERR` on the failing `archon | tee` pipeline even
  under `set +e`. `on_failure()`'s cost-capture block then re-enables `set -e`, so the shell exits
  with archon's code as the trap returns. The main loop's own `rate-limit-match` branch is
  therefore reached only after a pause-path early return from the trap. The bash guard (Task 7)
  is still required by the spec, as defense in depth. It is exercised by a loop harness that
  disarms the trap (Task 7). The end-to-end harness (Task 8) proves the real process exits as a
  counted failure.
- `tests/test_entrypoint_fix_main.sh` fails on `main` today (`fix-main route (952) not before
  smoke gate (725)`). It is not in `.github/workflows/ci.yml` and is unrelated. Do not touch it.

## File Structure

| File | Change | Task |
|---|---|---|
| `scripts/factory_core/session_window.py` | `check_and_pause(..., hosting_location="cloud")`: no sentinel on `local` | 1 |
| `scripts/factory_core/error_signature.py` | `classify(..., hosting_location="cloud")`: local branch above delivery-failure; lazy `model_adapter` import | 2 |
| `scripts/factory_core/cli.py` | `--hosting-location {cloud,local}` (default `cloud`) on `session-window-check` and `error-signature-write`, forwarded by both handlers | 3 |
| `scripts/factory_core/providers/cli.py` | `import os`; new `model-endpoints hosting` verb (`_model_endpoints_hosting`) | 4 |
| `entrypoint.sh` | resolver block after line 10; flag in `_handle_session_window_pause` and `_write_error_signature`; bash guard on the main loop's `rate-limit-match` | 6, 7 |
| `tests/test_factory_core_session_window.py` | local/cloud `check_and_pause` cases; CLI `--hosting-location local` case | 1, 3 |
| `tests/test_factory_core_error_signature.py` | local/cloud `classify` cases, precedence, import isolation; CLI case | 2, 3 |
| `tests/test_provider_model_endpoint.py` | `model-endpoints hosting` verb tests (reuses `_stage`/`_record`/`_local_env`) | 4 |
| `tests/test_factory_core_breaker.py` | regression pins: two local failures trip; `reset_retry` pops no new key | 5 |
| `tests/test_entrypoint_error_signature.sh` | section F2: `_write_error_signature` local vs cloud vs unset | 6 |
| `tests/test_entrypoint_session_window.sh` | sections J, K/K2 (sourced), L/L2/L3 (main-loop harness), M-M4 (end-to-end), N/N2 (argv parity) | 7, 8, 9 |
| `docs/run-lifecycle.md`, `docs/configuration.md`, `docs/features.md` | re-derived `entrypoint.sh`/`error_signature.py` citations; resolver step row; verified-local rule in §6.F and §9; `model-endpoints hosting` verb; session-window row | 10 |

**Not changed:** `scheduler.sh`, `scripts/factory_core/breaker.py`, `scripts/provider_env.sh`,
`scripts/factory_core/providers/model_endpoint.py`, `scripts/factory_core/providers/model_adapter.py`,
`config/config.yaml`, `deploy/**`, `workflows/**`, `.github/workflows/*`, `.factory/adapter.yaml`,
tool allow/deny lists, and `rate-limit-match` / `_rate_limit_match`. No new `tests/test_*.sh`:
CI runs only the shell tests listed in `ci.yml`, which this ticket does not touch.

## Conventions for every task

- Run Python tests with `PYTHONPATH=scripts python -m pytest <file> -v` from the repo root, as CI
  does.
- Shell tests: `bash tests/<file>.sh`. The last line is `Results: N passed, M failed`, and the
  exit status is non-zero iff `M > 0`.
  Pass totals quoted in this plan were measured in a factory container. On a clean runner
  (no `/var/lib/dark-factory` ledger) the two entrypoint shell tests report 2 fewer passes
  each (15 and 103), and pytest reports 3765 passed, 2 skipped. Only `0 failed` matters.
- Insert new shell-test sections in `tests/test_entrypoint_session_window.sh` **immediately before**
  these two lines, which close the file's numbered sections:
  ```bash
  echo ""
  echo "--- I: isolation -- the real /var/lib/dark-factory ledger was never touched (#362) ---"
  ```
  Each later task inserts before the same anchor, so the final order is M, J, K, L, M2-M4, N.
- Commit after each task with the subject given there. End each message with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

---

## Task 1: `check_and_pause` writes no sentinel for a verified-local match

**Files:** `tests/test_factory_core_session_window.py`, `scripts/factory_core/session_window.py`

- [ ] **Step 1.1: Write the failing tests.** Append to the end of
  `tests/test_factory_core_session_window.py`. It already imports `check_and_pause` at the top, and
  `subprocess`/`_sys` at module level near line 386; Task 3 uses those.

```python


# --- #586: a verified-local match never writes the pause sentinel ------------------------

_REJECTED_EVENT_LINE = (
    '{"level":40,"rateLimitInfo":{"status":"rejected","resetsAt":1000600,'
    '"rateLimitType":"five_hour"},"msg":"claude.rate_limit_event"}'
)


def test_check_and_pause_local_exhaustion_text_returns_none_and_writes_no_sentinel(tmp_path):
    text = "429 too many requests, session limit reached"
    assert check_and_pause(text, tmp_path, 1_000_000, 5, 30, hosting_location="local") is None
    assert not (tmp_path / "session-window-paused").exists()


def test_check_and_pause_local_rejected_structured_event_writes_no_sentinel(tmp_path):
    assert check_and_pause(_REJECTED_EVENT_LINE, tmp_path, 1_000_000, 5, 30,
                           hosting_location="local") is None
    assert not (tmp_path / "session-window-paused").exists()


def test_check_and_pause_cloud_and_omitted_hosting_still_pause(tmp_path):
    text = "429 too many requests, session limit reached"
    for sub, kwargs in (("omitted", {}), ("cloud", {"hosting_location": "cloud"})):
        state_dir = tmp_path / sub
        epoch = check_and_pause(text, state_dir, 1_000_000, 5, 30, **kwargs)
        assert epoch == 1_000_000 + 1800
        assert (state_dir / "session-window-paused").read_text() == str(epoch)
    state_dir = tmp_path / "structured"
    epoch = check_and_pause(_REJECTED_EVENT_LINE, state_dir, 1_000_000, 5, 30,
                            hosting_location="cloud")
    assert epoch == 1_000_600 + 300
    assert (state_dir / "session-window-paused").read_text() == str(epoch)
```

- [ ] **Step 1.2: Verify they fail.**
  `PYTHONPATH=scripts python -m pytest tests/test_factory_core_session_window.py -v -k "local or omitted_hosting"`
  Expected: the 3 new tests FAIL with
  `TypeError: check_and_pause() got an unexpected keyword argument 'hosting_location'`.

- [ ] **Step 1.3: Implement.** In `scripts/factory_core/session_window.py`, replace the whole
  `check_and_pause` function (currently at line 248, the last function in the file) with:

```python
def check_and_pause(
    text: str,
    state_dir: Path,
    now_epoch: int,
    buffer_minutes: int,
    fallback_minutes: int,
    hosting_location: str = "cloud",
) -> Optional[int]:
    resume_epoch = compute_resume_epoch(text, now_epoch, buffer_minutes, fallback_minutes)
    # #586: a verified-local endpoint's exhaustion-shaped output must never pause the whole
    # factory. Returning None makes the CLI print the same matched=false wire shape as "no
    # match", so entrypoint.sh routes it to the counted-failure path unchanged.
    if resume_epoch is None or hosting_location == "local":
        return None
    write_pause_sentinel(resume_epoch, state_dir)
    return resume_epoch
```

  Do not touch `compute_resume_epoch`, `is_session_window_failure`, or any regex. Detection is
  unchanged.

- [ ] **Step 1.4: Verify pass.**
  `PYTHONPATH=scripts python -m pytest tests/test_factory_core_session_window.py -v`
  Expected: all tests pass, including every pre-existing test.

- [ ] **Step 1.5: Commit.**
  `git add scripts/factory_core/session_window.py tests/test_factory_core_session_window.py`
  `git commit -m "feat(#586): check_and_pause writes no pause sentinel for a verified-local match"`

## Task 2: `classify()` signs a verified-local rate-limit match as a counted substantive failure

**Files:** `tests/test_factory_core_error_signature.py`, `scripts/factory_core/error_signature.py`

- [ ] **Step 2.1: Write the failing tests.** Append to the end of
  `tests/test_factory_core_error_signature.py`. `_classify`, `subprocess`, `_sys`, `json`, `Path`
  and `CLI` are already defined in the file.

```python


# --- #586: a verified-local rate-limit/exhaustion match is a counted substantive failure ---

_REJECTED_EVENT_LINE = (
    '{"level":40,"rateLimitInfo":{"status":"rejected","resetsAt":1000600,'
    '"rateLimitType":"five_hour"},"msg":"claude.rate_limit_event"}'
)
_SCRIPTS_DIR = str(Path(__file__).resolve().parents[1] / "scripts")


def test_local_rate_limit_text_is_substantive_endpoint_unreachable():
    assert _classify(text="HTTP 429 — rate limit exceeded", exit_code=1,
                     hosting_location="local") == "substantive:endpoint_unreachable:1"
    assert _classify(text="Error: you have hit your usage limit for this session", exit_code=2,
                     hosting_location="local") == "substantive:endpoint_unreachable:2"


def test_cloud_and_omitted_hosting_rate_limit_text_unchanged():
    assert _classify(text="HTTP 429 — rate limit exceeded") == "environmental:rate_limit"
    assert _classify(text="HTTP 429 — rate limit exceeded",
                     hosting_location="cloud") == "environmental:rate_limit"


def test_local_rejected_structured_event_is_substantive_endpoint_unreachable():
    # The event line does not match _RATE_LIMIT_RE, which is why the local branch also tests
    # is_session_window_failure().
    assert _classify(text=_REJECTED_EVENT_LINE, exit_code=1,
                     hosting_location="local") == "substantive:endpoint_unreachable:1"


def test_cloud_and_omitted_hosting_rejected_structured_event_unchanged():
    assert _classify(text=_REJECTED_EVENT_LINE) == "substantive:unknown:1"
    assert _classify(text=_REJECTED_EVENT_LINE,
                     hosting_location="cloud") == "substantive:unknown:1"


def test_local_non_rate_limit_failure_keeps_its_existing_class():
    assert _classify(text="npm ERR! build failed", exit_code=2,
                     hosting_location="local") == "substantive:build_failure:2"
    assert _classify(text="", exit_code=3, hosting_location="local") == "substantive:unknown:3"


def test_local_rate_limit_outranks_delivery_failure():
    fast_and_empty = dict(elapsed=5, commits=0, dirty=False, artifact=False)
    assert _classify(text="HTTP 429 Too Many Requests", exit_code=1, hosting_location="local",
                     **fast_and_empty) == "substantive:endpoint_unreachable:1"
    assert _classify(text="", exit_code=1, hosting_location="local",
                     **fast_and_empty) == "environmental:delivery_failure"
    assert _classify(text="HTTP 429 Too Many Requests", exit_code=1,
                     **fast_and_empty) == "environmental:delivery_failure"


def test_cloud_path_never_imports_model_adapter():
    code = (
        "import sys\n"
        f"sys.path.insert(0, {_SCRIPTS_DIR!r})\n"
        "from factory_core.error_signature import classify\n"
        "kw = dict(elapsed_seconds=999, commits_since_start=1, worktree_dirty=False,\n"
        "          artifact_present=True)\n"
        "for text in ('HTTP 429 Too Many Requests', '', 'npm ERR! build failed'):\n"
        "    classify(text, 1, **kw)\n"
        "    classify(text, 1, hosting_location='cloud', **kw)\n"
        "loaded = [m for m in ('factory_core.providers.model_adapter',\n"
        "                      'factory_core.providers.model_endpoint') if m in sys.modules]\n"
        "assert not loaded, loaded\n"
    )
    result = subprocess.run([_sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
```

  (`substantive:unknown:1` for the cloud structured-event case is today's output on `main`,
  checked while authoring. `_RATE_LIMIT_RE` does not match that line.)

- [ ] **Step 2.2: Verify they fail.**
  `PYTHONPATH=scripts python -m pytest tests/test_factory_core_error_signature.py -v -k "local or hosting or model_adapter"`
  Expected: the 7 new tests FAIL. The in-process ones fail with
  `TypeError: classify() got an unexpected keyword argument 'hosting_location'`. The subprocess
  one fails with the same `TypeError` in its stderr.

- [ ] **Step 2.3: Implement.** In `scripts/factory_core/error_signature.py`:
  1. Change the import on line 10 from
     `from .session_window import RATE_LIMIT_RE as _RATE_LIMIT_RE` to:
     ```python
     from .session_window import RATE_LIMIT_RE as _RATE_LIMIT_RE, is_session_window_failure
     ```
  2. In `classify()` (line 80), add the parameter after `delivery_failure_max_seconds: int = 30,`.
     Then insert the local branch as the **first** statement of the body, above the existing
     delivery-failure `if`:
     ```python
         delivery_failure_max_seconds: int = 30,
         hosting_location: str = "cloud",
     ) -> str:
         # #586: on a verified-local run, rate-limit/exhaustion output (text, or a rejected
         # structured claude.rate_limit_event, which _RATE_LIMIT_RE does not match) is a counted
         # local endpoint failure, so two in a row trip the breaker. Above delivery_failure: a
         # local 429 within seconds must not take the full retry ceiling as environmental.
         if hosting_location == "local" and (
             _RATE_LIMIT_RE.search(text) or is_session_window_failure(text)
         ):
             # Imported here, never at module top: the default cloud path must not load
             # model_adapter/model_endpoint (model_adapter.py's own module docstring).
             from .providers.model_adapter import ENDPOINT_UNREACHABLE  # contract §4.6 interim class
             return f"substantive:{ENDPOINT_UNREACHABLE}:{exit_code}"
         if (
             elapsed_seconds < delivery_failure_max_seconds
     ```
     Every existing branch below stays as is.

- [ ] **Step 2.4: Verify pass.**
  `PYTHONPATH=scripts python -m pytest tests/test_factory_core_error_signature.py -v`
  Expected: all tests pass. This includes the pre-existing `test_rate_limit`,
  `test_classify_http_429_rate_limit_exceeded_is_environmental` and the verdict-missing
  precedence tests, which show cloud is unchanged.

- [ ] **Step 2.5: Commit.**
  `git add scripts/factory_core/error_signature.py tests/test_factory_core_error_signature.py`
  `git commit -m "feat(#586): classify signs a verified-local rate-limit match substantive:endpoint_unreachable"`

## Task 3: `--hosting-location` on `session-window-check` and `error-signature-write`

**Files:** `tests/test_factory_core_session_window.py`, `tests/test_factory_core_error_signature.py`,
`scripts/factory_core/cli.py`

- [ ] **Step 3.1: Write the failing CLI tests.** Append to `tests/test_factory_core_session_window.py`:

```python


def test_cli_session_window_check_local_prints_unmatched_and_writes_no_sentinel(tmp_path):
    tmp_out = tmp_path / "run.out"
    tmp_out.write_text("429 too many requests, session limit reached")
    state_dir = tmp_path / "state"
    result = subprocess.run(
        [_sys.executable,
         str(Path(__file__).resolve().parents[1] / "scripts" / "factory_core" / "cli.py"),
         "session-window-check",
         "--tmp-out", str(tmp_out),
         "--state-dir", str(state_dir),
         "--buffer-minutes", "5",
         "--fallback-minutes", "30",
         "--hosting-location", "local"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "matched=false resume_epoch=0\n"
    assert not (state_dir / "session-window-paused").exists()
```

  Append to `tests/test_factory_core_error_signature.py`:

```python


def test_cli_error_signature_write_local_hosting_end_to_end(tmp_path):
    text_file = tmp_path / "out.txt"
    text_file.write_text("HTTP 429 Too Many Requests")
    result = subprocess.run(
        [_sys.executable, CLI, "error-signature-write",
         "--issue", "586", "--phase", "plan", "--exit-code", "1",
         "--text-file", str(text_file),
         "--elapsed-seconds", "5", "--commits-since-start", "0",
         "--state-dir", str(tmp_path), "--hosting-location", "local"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "signature=substantive:endpoint_unreachable:1" in result.stdout
    sig_file = tmp_path / "error-signatures" / "586.plan.sig"
    assert json.loads(sig_file.read_text())["signature"] == "substantive:endpoint_unreachable:1"
```

- [ ] **Step 3.2: Verify they fail.**
  `PYTHONPATH=scripts python -m pytest tests/test_factory_core_session_window.py tests/test_factory_core_error_signature.py -v -k "cli and local"`
  Expected: both FAIL. argparse exits 2 with
  `error: unrecognized arguments: --hosting-location local`.

- [ ] **Step 3.3: Implement.** In `scripts/factory_core/cli.py`:
  1. `_session_window_check` (line 151): inside the `check_and_pause(` call, after
     `fallback_minutes=args.fallback_minutes,`, add:
     ```python
             hosting_location=args.hosting_location,
     ```
  2. `_error_signature_write` (line 194): inside the `classify(` call, after
     `delivery_failure_max_seconds=args.delivery_failure_max_seconds,`, add:
     ```python
             hosting_location=args.hosting_location,
     ```
  3. In `main()`, the `sw` subparser (line 426): between the `--fallback-minutes` line and
     `sw.set_defaults(func=_session_window_check)`, add:
     ```python
         # #586: entrypoint.sh passes this only when the baked resolver said local.
         sw.add_argument("--hosting-location", choices=["cloud", "local"], default="cloud")
     ```
  4. The `esw` subparser (line 437): between `esw.add_argument("--state-dir", default="/var/lib/dark-factory")`
     and `esw.set_defaults(func=_error_signature_write)`, add:
     ```python
         esw.add_argument("--hosting-location", choices=["cloud", "local"], default="cloud")
     ```
  5. Do **not** touch the `rlm` (`rate-limit-match`) subparser or `_rate_limit_match`.

- [ ] **Step 3.4: Verify pass.**
  `PYTHONPATH=scripts python -m pytest tests/test_factory_core_session_window.py tests/test_factory_core_error_signature.py -v`
  Expected: all pass, including the pre-existing `test_cli_session_window_check_*`,
  `test_cli_rate_limit_match_*` and `test_cli_error_signature_write_*` tests.

- [ ] **Step 3.5: Commit.**
  `git add scripts/factory_core/cli.py tests/test_factory_core_session_window.py tests/test_factory_core_error_signature.py`
  `git commit -m "feat(#586): optional --hosting-location on session-window-check and error-signature-write"`

## Task 4: `providers/cli.py model-endpoints hosting` (baked, fail-closed resolver)

**Files:** `tests/test_provider_model_endpoint.py`, `scripts/factory_core/providers/cli.py`

The tests go in `tests/test_provider_model_endpoint.py`, not a new file. They reuse its `LOCAL`,
`CLOUD`, `_stage`, `_record`, `_local_env`, `_write_registry` and `pinned_suite` helpers. The
file's autouse `_clean_env` already clears every `FACTORY_MODEL_*` variable.

- [ ] **Step 4.1: Write the failing tests.** Append to the end of `tests/test_provider_model_endpoint.py`:

```python


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
```

- [ ] **Step 4.2: Verify they fail.**
  `PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py -v -k hosting`
  Expected: the 10 new test cases FAIL with `SystemExit: 2`. argparse prints
  `invalid choice: 'hosting' (choose from show, stage)`.

- [ ] **Step 4.3: Implement.** In `scripts/factory_core/providers/cli.py`:
  1. Add `import os` to the top-level imports, between `import json` and `import re` (alphabetical,
     matching the block).
  2. Insert this function between `_model_endpoints_show` (ends ~line 256) and
     `_model_endpoints_stage`:
     ```python
     def _model_endpoints_hosting(args):
         # #586: entrypoint.sh resolves the run's hosting location once, before the clone, through
         # this baked copy. Built only on load_verified_entry(), which keeps _NO_LOCAL_DISPATCH, so
         # every real registry prints hosting=cloud until #587. Always exits 0: any problem or
         # exception prints hosting=cloud (the caller fails closed on content, never on rc).
         hosting = "cloud"
         endpoint_id = os.environ.get("FACTORY_MODEL_ENDPOINT", "")
         try:
             if endpoint_id:
                 from factory_core.providers import model_endpoint
                 problems, entry = model_endpoint.load_verified_entry(
                     model_endpoint.staged_registry_dir(), endpoint_id)
                 if problems:
                     print(f"WARNING: model-endpoints hosting: {'; '.join(problems)}", file=sys.stderr)
                 elif entry["hosting_location"] == "local":
                     hosting = "local"
         except Exception as e:  # noqa: BLE001 — fail closed to cloud on anything unexpected
             print(f"WARNING: model-endpoints hosting: {type(e).__name__} — resolving to cloud",
                   file=sys.stderr)
             hosting = "cloud"
         print(f"hosting={hosting}")
     ```
     No flags. The digest is read only from `FACTORY_MODEL_REGISTRY_SHA256`, inside
     `read_verified_tree`. The registry path comes from `staged_registry_dir()`
     (`FACTORY_MODEL_REGISTRY_PATH`, a test-only override, or the
     `/var/lib/dark-factory/model-registry` default). Add no `--registry-sha256` or similar flag
     (issue constraint 5).
  3. In `main()`, after `mstage.set_defaults(func=_model_endpoints_stage)` (line 444), add:
     ```python

         mhost = msub.add_parser("hosting")
         mhost.set_defaults(func=_model_endpoints_hosting)
     ```

- [ ] **Step 4.4: Verify pass.**
  `PYTHONPATH=scripts python -m pytest tests/test_provider_model_endpoint.py tests/test_provider_cli.py -v`
  Expected: all pass. `test_load_verified_entry_keeps_no_local_dispatch` must still pass. It
  proves `model_endpoint.py` is untouched.

- [ ] **Step 4.5: Commit.**
  `git add scripts/factory_core/providers/cli.py tests/test_provider_model_endpoint.py`
  `git commit -m "feat(#586): providers-cli model-endpoints hosting — fail-closed resolver over load_verified_entry"`

## Task 5: Breaker regression pins (one bucket, no new key)

**Files:** `tests/test_factory_core_breaker.py` (test-only; `breaker.py` is NOT changed)

These two tests **pass on `main` and must keep passing**. They pin owner decision 2 against a
regression; they are not failing-first tests. The first proves the new class trips the breaker
through the unchanged `<issue>.<phase>.sig` file and `<key>:sig` key. The second proves
`reset_retry` gained no `:local:sig` pop (spec Testing item 8).

- [ ] **Step 5.1: Write the tests.** Append to the end of `tests/test_factory_core_breaker.py`.
  `json`, `reset_retry` and `record_failure_signature` are already imported at module level, the
  last one at line ~196.

```python


# --- #586: a local endpoint failure trips through the one existing signature bucket -------

def test_two_consecutive_local_endpoint_failures_trip_the_breaker(tmp_path):
    from factory_core.error_signature import write_signature
    sf = tmp_path / "state.json"
    sig = "substantive:endpoint_unreachable:1"
    write_signature(586, "plan", sig, 1, tmp_path)
    assert (tmp_path / "error-signatures" / "586.plan.sig").exists()
    assert record_failure_signature(586, "plan", sf, tmp_path) == (False, sig)
    write_signature(586, "plan", sig, 1, tmp_path)
    assert record_failure_signature(586, "plan", sf, tmp_path) == (True, sig)
    assert json.loads(sf.read_text())["586:plan:sig"] == sig
    assert sorted(p.name for p in (tmp_path / "error-signatures").iterdir()) == []


def test_reset_retry_pops_exactly_the_existing_key_family(tmp_path):
    # Regression guard against the rejected endpoint-split bucket (#586 owner decision 2):
    # reset_retry gained no new key, so a stray "<key>:local:sig" is not one of its pops.
    sf = tmp_path / "state.json"
    key = "586:plan"
    owned = [key, f"{key}:sig", f"{key}:delivery", f"{key}:loop:nightly:iter",
             f"{key}:cverdict", f"{key}:cid", f"{key}:crawlog"]
    kept = {f"{key}:local:sig": "x", "586:refine:sig": "y", "587": 1}
    sf.write_text(json.dumps({**{k: 1 for k in owned}, **kept}))
    reset_retry(key, sf)
    assert json.loads(sf.read_text()) == kept
```

- [ ] **Step 5.2: Verify pass.**
  `PYTHONPATH=scripts python -m pytest tests/test_factory_core_breaker.py -v -k "local_endpoint or key_family"`
  Expected: 2 passed. Then run `git diff --quiet -- scripts/factory_core/breaker.py && echo unchanged`.
  Expected output: `unchanged`.

- [ ] **Step 5.3: Commit.**
  `git add tests/test_factory_core_breaker.py`
  `git commit -m "test(#586): pin one signature bucket — local endpoint failures trip, reset_retry adds no key"`

## Task 6: `entrypoint.sh` resolves hosting once (baked, pre-clone) and threads it into `_write_error_signature`

**Files:** `tests/test_entrypoint_error_signature.sh`, `entrypoint.sh`

This task's failing test (F2) pins the `_write_error_signature` flag. The resolver block lands
here too. Its own assertions are end to end, because the resolver runs before the
`ENTRYPOINT_SOURCE_ONLY` return, and section M in Task 7 is where they become checkable.
They check that hosting is resolved exactly once, via `$FACTORY_PROVIDERS_CLI`, right after the
provider check. M also needs Task 7's pause suppression to go green, so it is written and
verified there. That keeps every commit green.

- [ ] **Step 6.1: Write the failing shell test.** In `tests/test_entrypoint_error_signature.sh`,
  insert immediately before the lines
  `echo ""` / `echo "--- G: isolation -- the real /var/lib/dark-factory ledger was never touched (#362) ---"`:

```bash
echo ""
echo "--- F2: #586 hosting flag — a fast local 429 is a counted failure, cloud is unchanged ---"
# Fast, zero commits, clean tree, no artifact: the delivery_failure conjunction holds, so
# only the local branch placed above it can change the class (operator review, #586).
SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-es-statedir6-XXXXXX)
export SCHEDULER_STATE_DIR
ARTIFACTS_DIR=$(mktemp -d /tmp/ep-es-artifacts6-XXXXXX)
RUN_STARTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
STUB_COMMIT_COUNT=0
STUB_DIRTY=false
INTENT=plan
TMP_OUT=$(mktemp)
echo "HTTP 429 Too Many Requests" > "$TMP_OUT"
SIG_FILE="${SCHEDULER_STATE_DIR}/error-signatures/33.plan.sig"
DF_ENDPOINT_HOSTING=local
_write_error_signature "plan" 1 "$TMP_OUT"
assert_true "local → substantive:endpoint_unreachable:1" \
  "grep -q 'substantive:endpoint_unreachable:1' '$SIG_FILE'"
DF_ENDPOINT_HOSTING=cloud
_write_error_signature "plan" 1 "$TMP_OUT"
assert_true "cloud → environmental:delivery_failure (unchanged)" \
  "grep -q 'environmental:delivery_failure' '$SIG_FILE'"
unset DF_ENDPOINT_HOSTING
_write_error_signature "plan" 1 "$TMP_OUT"
assert_true "unset → treated as cloud" \
  "grep -q 'environmental:delivery_failure' '$SIG_FILE'"
rm -f "$TMP_OUT"
rm -rf "$SCHEDULER_STATE_DIR" "$ARTIFACTS_DIR"

```

- [ ] **Step 6.2: Verify it fails.**
  `bash tests/test_entrypoint_error_signature.sh` → `FAIL: local → substantive:endpoint_unreachable:1`,
  which signs `environmental:delivery_failure` instead, and `Results: 16 passed, 1 failed`.

- [ ] **Step 6.3: Implement the resolver and the `_write_error_signature` flag.** In `entrypoint.sh`:
  1. Immediately after line 10 (`python3 "$FACTORY_PROVIDERS_CLI" preflight`) and before
     `# --- Configuration ---`, insert:
     ```bash

     # --- Model-endpoint hosting location (#586) ---
     # Resolved once, here, through the baked providers CLI: the clone does not exist yet, and
     # branch-controlled code must never decide something that relaxes a safety mechanism (the
     # factory-wide pause sentinel and the breaker's rate-limit class). Fails closed: anything
     # but exactly "hosting=local" (an error, a crash, no output) is cloud. Assigned
     # unconditionally, so an inherited value is overwritten; not readonly, because
     # tests/test_entrypoint_current_run.sh sources this file twice in one shell.
     DF_ENDPOINT_HOSTING=$(python3 "$FACTORY_PROVIDERS_CLI" model-endpoints hosting 2>/dev/null \
       | grep -o '^hosting=local$' || echo "hosting=cloud")
     DF_ENDPOINT_HOSTING="${DF_ENDPOINT_HOSTING#hosting=}"
     ```
     Do not use the word "preflight" in this block. `tests/test_entrypoint_preflight.sh` takes the
     first `preflight` line of the file as the preflight call. No `readonly` (spec amendment).
  2. In `_write_error_signature` (line 435), immediately before its
     `# TARGET-PATH: cli.py resolves under dark-factory/ in the clone — target's own copy until`
     comment (line ~481), insert:
     ```bash
       # #586: a verified-local run signs rate-limit/exhaustion output as a counted
       # substantive:endpoint_unreachable failure; the flag is passed only on local (default argv
       # unchanged, as in _handle_session_window_pause).
       local hosting_flag=()
       [ "${DF_ENDPOINT_HOSTING:-cloud}" = "local" ] && hosting_flag=(--hosting-location local)
     ```
     Then, in the `error-signature-write` invocation, add one line between
     `--state-dir "${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}" \` and `2>/dev/null || true`:
     ```bash
         "${hosting_flag[@]}" \
     ```
     An empty array expands to zero words, so the cloud argv is byte-identical. Bash ≥ 4.4 is
     safe under `set -u`; the file already relies on that with `"${detail_args[@]}"`.
     `_write_error_signature`'s three callers (`on_failure` lines 664/682 and the main loop's
     1156) do not change.

- [ ] **Step 6.4: Verify.**
  `bash tests/test_entrypoint_error_signature.sh` → `Results: 17 passed, 0 failed`.
  `bash tests/test_entrypoint_session_window.sh` → `0 failed`; the pre-existing sections are
  unchanged.
  `bash tests/test_entrypoint_current_run.sh` and `bash tests/test_entrypoint_preflight.sh` both
  pass. The first sources the file twice; the second checks preflight placement.
  `bash -n entrypoint.sh` prints nothing.

- [ ] **Step 6.5: Commit.**
  `git add entrypoint.sh tests/test_entrypoint_error_signature.sh`
  `git commit -m "feat(#586): resolve endpoint hosting once via the baked CLI; local failures sign endpoint_unreachable"`

## Task 7: No pause and no sleep loop on a verified-local match (both call sites)

**Files:** `tests/test_entrypoint_session_window.sh`, `entrypoint.sh`

- [ ] **Step 7.1: Write the failing shell tests.** In `tests/test_entrypoint_session_window.sh`,
  insert two blocks before the section-I anchor, in this order.

  **Block 1: the end-to-end harness and section M (spec Testing items 5 and 6a).**
  The harness runs `entrypoint.sh` itself as a child process, not sourced, because the main loop
  sits after the `ENTRYPOINT_SOURCE_ONLY` return at line 830.
  - It redirects every hardcoded `/opt/dark-factory` path through `source`/`cp`/`python3` wrapper
    functions, so it runs on a bare CI runner without the image.
  - `$FACTORY_PROVIDERS_CLI` is a stub that answers `preflight` (exit 0) and the `tracker` verbs,
    so it reaches no network.
  - The stub answers `model-endpoints hosting` with `$E2E_HOSTING_OUT` when set, and otherwise
    with this checkout's real resolver.
  - `SEQ_URL` points at a refused port so `run_record.py`'s best-effort Seq post fails at once
    instead of waiting out its 5 s timeout.

```bash
# --- #586 end-to-end harness --------------------------------------------------------------
# Runs entrypoint.sh itself as a child process, not sourced: the main loop sits after the
# ENTRYPOINT_SOURCE_ONLY return. Every external command is stubbed and every hardcoded
# /opt/dark-factory path is redirected to this checkout, so it runs on a bare CI runner and
# reaches no network. $FACTORY_PROVIDERS_CLI is a stub answering preflight and tracker
# verbs; `model-endpoints hosting` prints $E2E_HOSTING_OUT when set, else runs this
# checkout's real resolver.
_e2e_run() {  # _e2e_run <dir> <archon-output-file>; sets E2E_RC
  local dir="$1"
  mkdir -p "$dir/home" "$dir/state" "$dir/run" "$dir/tmp"
  cat > "$dir/providers_cli.py" <<'PY'
import os, runpy, sys
with open(os.path.join(os.environ["E2E_DIR"], "providers.log"), "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
args = sys.argv[1:]
if args[:2] == ["model-endpoints", "hosting"]:
    if "E2E_HOSTING_OUT" in os.environ:
        print(os.environ["E2E_HOSTING_OUT"])
    else:
        sys.argv[0] = os.environ["E2E_REAL_PROVIDERS_CLI"]
        runpy.run_path(sys.argv[0], run_name="__main__")
elif args[:1] == ["tracker"]:
    print("{}")
PY
  (
    export E2E_DIR="$dir" E2E_REPO="$REPO_ROOT" E2E_ARCHON_OUT="$2"
    export E2E_REAL_PROVIDERS_CLI="$REPO_ROOT/scripts/factory_core/providers/cli.py"
    export IDENTITY_SH="$REPO_ROOT/scripts/identity.sh" FACTORY_PROVIDERS_CLI="$dir/providers_cli.py"
    export FACTORY_SCRIPTS_DIR="$REPO_ROOT/scripts" FACTORY_CONFIG_PATH="$REPO_ROOT/config/config.yaml"
    export FACTORY_CLONE_DIR="$dir/clone" HOME="$dir/home" TMPDIR="$dir/tmp"
    export SCHEDULER_STATE_DIR="$dir/state" CURRENT_RUN_DIR="$dir/run"
    # SEQ_URL: a refused local port, so run_record.py's best-effort Seq post fails at once
    # instead of waiting out its 5s timeout on every record.
    export GH_TOKEN=stub-token CLAUDE_CODE_OAUTH_TOKEN=stub-token SEQ_URL=http://127.0.0.1:9
    unset ARTIFACTS_DIR TMP_OUT DF_ENDPOINT_HOSTING FACTORY_BLAST_HOLD_FILE RUN_ID ISSUE_NUM INTENT
    unset SESSION_WINDOW_BACKOFF_ENABLED
    git() {
      if [ "${1:-}" = clone ]; then
        mkdir -p "$3/dark-factory" "$3/.archon/workflows" "$3/.archon/commands"
        ln -s "$E2E_REPO/scripts" "$3/dark-factory/scripts"
      fi
      return 0
    }
    gh() { echo stub; return 0; }
    docker() { return 0; }
    claude() { echo stub; return 0; }
    pre-commit() { return 0; }
    archon() {
      if [ "${1:-} ${2:-}" = "workflow run" ]; then cat "$E2E_ARCHON_OUT"; return 1; fi
      echo "{}"; return 0
    }
    sleep() { echo slept >> "$E2E_DIR/sleep.log"; exit 98; }
    _e2e_path() {
      case "$1" in
        /opt/dark-factory/scripts/factory_core/providers/cli.py) echo "$FACTORY_PROVIDERS_CLI" ;;
        /opt/dark-factory/*) echo "$E2E_REPO${1#/opt/dark-factory}" ;;
        *) echo "$1" ;;
      esac
    }
    source() { local f; f=$(_e2e_path "$1"); shift; builtin source "$f" "$@"; }
    cp() { local a=() x; for x in "$@"; do a+=("$(_e2e_path "$x")"); done; command cp "${a[@]}"; }
    python3() {
      local a=() x
      for x in "$@"; do a+=("$(_e2e_path "$x")"); done
      case " ${a[*]} " in
        *" session-window-check "*|*" rate-limit-match "*|*" error-signature-write "*)
          printf '[%s]' "${a[@]}" >> "$E2E_DIR/argv.log"; echo >> "$E2E_DIR/argv.log" ;;
      esac
      PYTHONPATH="$(_e2e_path "${PYTHONPATH:-}")" command python3 "${a[@]}"
    }
    export -f git gh docker claude pre-commit archon sleep _e2e_path source cp python3
    timeout 120 bash "$REPO_ROOT/entrypoint.sh" "Plan issue #586"
  ) >"$dir/stdout.log" 2>&1
  E2E_RC=$?
}
_e2e_sig() {
  python3 -c "import json; print(json.load(open('$1/state/error-signatures/586.plan.sig'))['signature'])" 2>/dev/null
}

echo ""
echo "--- M: #586 end to end, verified-local, exhaustion text — counted failure, no pause ---"
E2E_DIR_M=$(mktemp -d /tmp/ep-sw-e2e-m-XXXXXX)
printf "You've hit your session limit · resets 11:10pm (UTC)\n" > "$E2E_DIR_M/archon.out"
E2E_HOSTING_OUT=hosting=local _e2e_run "$E2E_DIR_M" "$E2E_DIR_M/archon.out"
assert_eq "exits non-zero with the workflow's exit code (not a timeout, not the sleep)" "1" "$E2E_RC"
assert_true "never reached the legacy sleep" "[ ! -f '$E2E_DIR_M/sleep.log' ]"
assert_true "no sentinel written" "[ ! -f '$E2E_DIR_M/state/session-window-paused' ]"
assert_eq "signature the scheduler consumes is the counted local class" \
  "substantive:endpoint_unreachable:1" "$(_e2e_sig "$E2E_DIR_M")"
assert_eq "hosting resolved exactly once, through \$FACTORY_PROVIDERS_CLI" "1" \
  "$(grep -c '^model-endpoints hosting$' "$E2E_DIR_M/providers.log")"
assert_true "hosting resolved right after the provider check, before anything else" \
  "[ \"\$(sed -n 2p '$E2E_DIR_M/providers.log')\" = 'model-endpoints hosting' ]"
assert_true "no pause comment posted" \
  "! grep -q 'df-session-window-pause' '$E2E_DIR_M/providers.log'"
rm -rf "$E2E_DIR_M"

```

  The `sed -n 2p` assertion pins the call order. Line 1 of `providers.log` is `preflight` and
  line 2 is `model-endpoints hosting`, both before the `gh auth setup-git`/`git clone` that
  follow `entrypoint.sh:830`. The resolution happens via `$FACTORY_PROVIDERS_CLI`, the baked
  absolute path, never `$CLONE_DIR/...`.

  **Block 2: sections J, K/K2 and L/L2/L3.** J and K run against the sourced functions, like the
  file's existing A-H. Section L uses a main-loop harness. That harness runs the **real loop text**,
  extracted from `entrypoint.sh` with `awk '/^while true; do$/,/^done$/'` as in
  `tests/test_entrypoint_blast_hold.sh` section D. It runs in a subshell with the ERR trap
  disarmed. This is the only way to execute the loop's own `rate-limit-match` branch, because in
  production `on_failure()` ends every non-pause failure first.

```bash
echo ""
echo "--- J: #586 verified-local hosting — no sentinel, no pause signature, returns 1 ---"
SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-sw-statedir-j-XXXXXX)
export SCHEDULER_STATE_DIR
ISSUE_NUM=586
INTENT=plan
RUN_ID=test-run-j1
SESSION_WINDOW_BACKOFF_ENABLED=true
TMP_OUT=$(mktemp /tmp/ep-sw-out-j-XXXXXX)
printf "You've hit your session limit · resets 11:10pm (UTC)\n" > "$TMP_OUT"
DF_ENDPOINT_HOSTING=local
_handle_session_window_pause "$TMP_OUT"
RC_J=$?
assert_eq "local exhaustion text → returns 1 (falls through to the failure path)" "1" "$RC_J"
assert_true "local: no sentinel written" "[ ! -f '${SCHEDULER_STATE_DIR}/session-window-paused' ]"
assert_true "local: no session-window pause signature written" \
  "[ ! -f '${SCHEDULER_STATE_DIR}/error-signatures/586.plan.sig' ]"
assert_true "local: no paused run-record row" \
  "! grep -q '\"stage\": \"paused\"' '${SCHEDULER_STATE_DIR}/runs.jsonl' 2>/dev/null"
DF_ENDPOINT_HOSTING=cloud
_handle_session_window_pause "$TMP_OUT"
RC_J2=$?
assert_eq "cloud: same text still pauses (returns 0)" "0" "$RC_J2"
assert_true "cloud: sentinel written" "[ -f '${SCHEDULER_STATE_DIR}/session-window-paused' ]"
rm -f "$TMP_OUT"
rm -rf "$SCHEDULER_STATE_DIR"

# K/K2 share one body: on_failure() (the ERR-trap call site) on a verified-local run.
_k_on_failure_local() {  # _k_on_failure_local <kill-switch> <label>
  SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-sw-statedir-k-XXXXXX)
  export SCHEDULER_STATE_DIR
  ARTIFACTS_DIR=$(mktemp -d /tmp/ep-sw-artifacts-k-XXXXXX)
  export ARTIFACTS_DIR
  ISSUE_NUM=586
  INTENT=fix
  RUN_ID=test-run-k-$2
  RUN_STARTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  SESSION_WINDOW_BACKOFF_ENABLED=$1
  DF_ENDPOINT_HOSTING=local
  POST_MORTEM_CALLS=0
  run_post_mortem() { POST_MORTEM_CALLS=$((POST_MORTEM_CALLS+1)); }
  BOARD_STATUS_CALLS=0
  set_board_status() { BOARD_STATUS_CALLS=$((BOARD_STATUS_CALLS+1)); return 0; }
  COMMENT_LOG_DIR=$(mktemp -d /tmp/ep-sw-comments-k-XXXXXX)
  post_or_update_comment() {
    local marker="$1" body="$2"
    local safe
    safe=$(echo "$marker" | tr -cd 'a-zA-Z0-9')
    echo "$body" > "${COMMENT_LOG_DIR}/${safe}.md"
  }
  post_cost_report() { :; }
  TMP_OUT=$(mktemp /tmp/ep-sw-out-k-XXXXXX)
  printf "You've hit your session limit · resets 11:10pm (UTC)\n" > "$TMP_OUT"

  false
  on_failure
  set +e  # see the comment on section D's on_failure() call for why this is required

  assert_eq "[$2] run_post_mortem IS called (counted failure, not a pause)" "1" "$POST_MORTEM_CALLS"
  assert_eq "[$2] set_board_status IS called" "1" "$BOARD_STATUS_CALLS"
  assert_true "[$2] df-factory-failure comment produced" \
    "[ -f '${COMMENT_LOG_DIR}/dffactoryfailure.md' ]"
  assert_true "[$2] no session-window pause comment" \
    "[ ! -f '${COMMENT_LOG_DIR}/dfsessionwindowpause.md' ]"
  assert_true "[$2] no sentinel written" "[ ! -f '${SCHEDULER_STATE_DIR}/session-window-paused' ]"
  assert_true "[$2] runs.jsonl records a failed stage" \
    "grep -q '\"stage\": \"failed\"' '${SCHEDULER_STATE_DIR}/runs.jsonl'"
  assert_eq "[$2] signature is the counted local endpoint class" \
    "substantive:endpoint_unreachable:1" \
    "$(python3 -c "import json; print(json.load(open('${SCHEDULER_STATE_DIR}/error-signatures/586.implement.sig'))['signature'])" 2>/dev/null)"
  rm -f "$TMP_OUT"
  rm -rf "$SCHEDULER_STATE_DIR" "$ARTIFACTS_DIR" "$COMMENT_LOG_DIR"
  DF_ENDPOINT_HOSTING=cloud
  SESSION_WINDOW_BACKOFF_ENABLED=true
}
echo ""
echo "--- K: #586 on_failure() on a verified-local run — counted failure, never the pause branch ---"
_k_on_failure_local true kill-switch-on
echo ""
echo "--- K2: #586 same with the kill-switch off ---"
_k_on_failure_local false kill-switch-off

# --- #586 main-loop harness ---------------------------------------------------------------
# Runs the real main loop -- its text extracted from entrypoint.sh, not re-typed -- in a
# subshell with the ERR trap disarmed. In production on_failure() ends every non-pause
# failure first (it re-enables errexit), so this is the only way to execute the loop's own
# rate-limit-match branch, which the #586 bash guard sits on.
_loop_run() {  # _loop_run <dir> <archon-output-file>; sets LOOP_RC
  local dir="$1" out="$2"
  mkdir -p "$dir"
  (
    trap - ERR
    archon() { cat "$out"; return 1; }
    sleep() { echo slept >> "$dir/sleep.log"; exit 98; }
    run_post_mortem() { echo post_mortem >> "$dir/calls.log"; }
    _handle_blast_hold() { return 1; }
    python3() {
      case " $* " in
        *" session-window-check "*|*" rate-limit-match "*|*" error-signature-write "*)
          printf '[%s]' "$@" >> "$dir/argv.log"; echo >> "$dir/argv.log" ;;
      esac
      command python3 "$@"
    }
    eval "$(awk '/^while true; do$/,/^done$/' "$REPO_ROOT/entrypoint.sh")"
    exit 97  # the loop broke out: never expected for a failed workflow
  ) >"$dir/stdout.log" 2>&1
  LOOP_RC=$?
}
_loop_case() {  # _loop_case <hosting> <kill-switch> <label>; leaves LOOP_DIR behind
  LOOP_DIR=$(mktemp -d /tmp/ep-sw-loop-XXXXXX)
  SCHEDULER_STATE_DIR="$LOOP_DIR/state"; mkdir -p "$SCHEDULER_STATE_DIR"
  export SCHEDULER_STATE_DIR
  ARTIFACTS_DIR="$LOOP_DIR/artifacts"; mkdir -p "$ARTIFACTS_DIR"
  export ARTIFACTS_DIR
  ISSUE_NUM=586
  INTENT=plan
  RUN_ID=test-run-loop-$3
  RUN_STARTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  DF_ENDPOINT_HOSTING=$1
  SESSION_WINDOW_BACKOFF_ENABLED=$2
  printf "You've hit your session limit · resets 11:10pm (UTC)\n" > "$LOOP_DIR/archon.out"
  _loop_run "$LOOP_DIR" "$LOOP_DIR/archon.out"
  DF_ENDPOINT_HOSTING=cloud
  SESSION_WINDOW_BACKOFF_ENABLED=true
}
_loop_sig() {
  python3 -c "import json; print(json.load(open('$LOOP_DIR/state/error-signatures/586.plan.sig'))['signature'])" 2>/dev/null
}

echo ""
echo "--- L: #586 main loop, verified-local, kill-switch off — exits counted, never sleeps ---"
_loop_case local false l1
assert_eq "exits with the workflow's own exit code" "1" "$LOOP_RC"
assert_true "never reached the legacy sleep" "[ ! -f '$LOOP_DIR/sleep.log' ]"
assert_true "rate-limit-match never invoked (bash guard short-circuits)" \
  "! grep -q 'rate-limit-match' '$LOOP_DIR/argv.log' 2>/dev/null"
assert_true "post-mortem ran" "grep -q post_mortem '$LOOP_DIR/calls.log'"
assert_eq "signature is the counted local endpoint class" "substantive:endpoint_unreachable:1" "$(_loop_sig)"
assert_true "no sentinel written" "[ ! -f '$LOOP_DIR/state/session-window-paused' ]"
rm -rf "$LOOP_DIR"

echo ""
echo "--- L2: #586 main loop, verified-local, kill-switch on — same counted exit ---"
_loop_case local true l2
assert_eq "exits with the workflow's own exit code" "1" "$LOOP_RC"
assert_true "never reached the legacy sleep" "[ ! -f '$LOOP_DIR/sleep.log' ]"
assert_true "session-window-check got --hosting-location local" \
  "grep -q '\[session-window-check\].*\[--hosting-location\]\[local\]' '$LOOP_DIR/argv.log'"
assert_true "rate-limit-match never invoked" "! grep -q 'rate-limit-match' '$LOOP_DIR/argv.log'"
assert_eq "signature is the counted local endpoint class" "substantive:endpoint_unreachable:1" "$(_loop_sig)"
assert_true "no sentinel written" "[ ! -f '$LOOP_DIR/state/session-window-paused' ]"
rm -rf "$LOOP_DIR"

echo ""
echo "--- L3: #586 parity — cloud with the kill-switch off keeps the legacy sleep loop ---"
_loop_case cloud false l3
assert_eq "reached the legacy sleep (stub exits 98)" "98" "$LOOP_RC"
assert_true "rate-limit-match invoked" "grep -q 'rate-limit-match' '$LOOP_DIR/argv.log'"
rm -rf "$LOOP_DIR"

```

- [ ] **Step 7.2: Verify they fail.** Run `bash tests/test_entrypoint_session_window.sh`.
  Expected FAILs:
  - M: exits `0` paused, writes the sentinel, signs `environmental:session_window_pause` and
    posts the pause comment. Its resolver assertions already pass, because Task 6 landed the
    resolver.
  - J: returns `0`, writes the sentinel and the pause signature.
  - K: the pause branch runs (`run_post_mortem` count `0`, no failure comment) and the signature
    is `environmental:session_window_pause`.
  - L: exit `98`, meaning the legacy sleep was reached, and `rate-limit-match` was invoked.
  - L2: exit `0`, paused.

  For the resolver assertions' failing-first proof against `main`, run
  `git checkout "$(git merge-base origin/main HEAD)" -- entrypoint.sh scripts/ && bash tests/test_entrypoint_session_window.sh 2>&1 | grep -A8 -- '--- M:'; git checkout HEAD -- entrypoint.sh scripts/`.
  (Tasks 1-6 are committed, so `git stash` would save nothing.)
  Expected: on `main`, `hosting resolved exactly once` fails with count `0`, and so does the
  `sed -n 2p` ordering check. Confirm the restore with `git status --short`, which must list
  only `tests/test_entrypoint_session_window.sh`.

  Expected PASSes on the current code:
  - K2: Task 6's flag already signs it.
  - L3: legacy behavior is unchanged.

- [ ] **Step 7.3: Implement.** In `entrypoint.sh`:
  1. In `_handle_session_window_pause` (line ~306 after Task 6), replace this block:
     ```bash
       local sw_result sw_rc
       # TARGET-PATH: cli.py resolves under dark-factory/ in the clone — target's own copy
       # until P3 cleanup, baked self-contained fallback copy afterwards (df#14)
       sw_result=$(python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" session-window-check \
         --tmp-out "$tmp_out" \
         --state-dir "${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}" \
         --buffer-minutes "${SESSION_WINDOW_BUFFER_MINUTES:-5}" \
         --fallback-minutes "${SESSION_WINDOW_FALLBACK_MINUTES:-30}" 2>&1)
     ```
     with:
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
     ```
     Nothing else in the function changes. A suppressed local match returns at the existing
     `[ "$matched" = "true" ] || return 1`. So neither caller changes: `on_failure` (line ~592)
     and the main loop (line ~1134) both already fall through on `1`.
  2. In the main loop (line ~1141 after the edits above), replace:
     ```bash
         if python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" rate-limit-match \
             --tmp-out "$TMP_OUT" | grep -q '^matched=true$'; then
     ```
     with:
     ```bash
         # #586: never on a verified-local run -- this sleeps and retries forever with no
         # signature or retry count. Bash-only, so rate-limit-match itself is unchanged, and it
         # also holds with the kill-switch off (_handle_session_window_pause returns early then).
         if [ "${DF_ENDPOINT_HOSTING:-cloud}" != "local" ] \
             && python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" rate-limit-match \
             --tmp-out "$TMP_OUT" | grep -q '^matched=true$'; then
     ```
     The `rate-limit-match` argv and the sleep body are unchanged. When `local`, the `&&` skips
     the Python call. Execution then reaches the existing
     `run_post_mortem` / `_write_error_signature` / `exit "$EXIT_CODE"` lines.

- [ ] **Step 7.4: Verify pass.** `bash tests/test_entrypoint_session_window.sh`. Expected: every
  section passes, including pre-existing A-H and I, plus M, J, K, K2, L, L2 and L3.
  Also run `bash tests/test_entrypoint_blast_hold.sh`. Its section D statically checks the loop
  order (session-window, then hold, then post-mortem, then signature). Expected: pass.

- [ ] **Step 7.5: Commit.**
  `git add entrypoint.sh tests/test_entrypoint_session_window.sh`
  `git commit -m "feat(#586): verified-local match never pauses the factory nor enters the legacy sleep loop"`

## Task 8: End-to-end coverage of the remaining hosting cases (structured event, cloud parity, fail-closed parsing)

**Files:** `tests/test_entrypoint_session_window.sh` (test-only; behavior already landed in Tasks 6-7)

- [ ] **Step 8.1: Write the tests.** Insert before the section-I anchor:

```bash
echo ""
echo "--- M2: #586 end to end, verified-local, rejected structured event — same counted failure ---"
E2E_DIR_M2=$(mktemp -d /tmp/ep-sw-e2e-m2-XXXXXX)
printf '{"level":40,"rateLimitInfo":{"status":"rejected","resetsAt":%s,"rateLimitType":"five_hour"},"msg":"claude.rate_limit_event"}\n' \
  "$(( $(date -u +%s) + 600 ))" > "$E2E_DIR_M2/archon.out"
E2E_HOSTING_OUT=hosting=local _e2e_run "$E2E_DIR_M2" "$E2E_DIR_M2/archon.out"
assert_eq "exits non-zero" "1" "$E2E_RC"
assert_true "no sentinel written" "[ ! -f '$E2E_DIR_M2/state/session-window-paused' ]"
assert_eq "signature is the counted local class" \
  "substantive:endpoint_unreachable:1" "$(_e2e_sig "$E2E_DIR_M2")"
rm -rf "$E2E_DIR_M2"

echo ""
echo "--- M3: #586 parity, end to end — cloud hosting, same exhaustion text still pauses ---"
E2E_DIR_M3=$(mktemp -d /tmp/ep-sw-e2e-m3-XXXXXX)
printf "You've hit your session limit · resets 11:10pm (UTC)\n" > "$E2E_DIR_M3/archon.out"
E2E_HOSTING_OUT=hosting=cloud _e2e_run "$E2E_DIR_M3" "$E2E_DIR_M3/archon.out"
assert_eq "exits 0 (paused)" "0" "$E2E_RC"
assert_true "sentinel written" "[ -f '$E2E_DIR_M3/state/session-window-paused' ]"
assert_eq "pause signature written" "environmental:session_window_pause" "$(_e2e_sig "$E2E_DIR_M3")"
rm -rf "$E2E_DIR_M3"

echo ""
echo "--- M4: #586 fail closed, end to end — resolver output other than exactly hosting=local is cloud ---"
for out in "hosting=local-ish" "HOSTING=local" ""; do
  E2E_DIR_M4=$(mktemp -d /tmp/ep-sw-e2e-m4-XXXXXX)
  printf "You've hit your session limit · resets 11:10pm (UTC)\n" > "$E2E_DIR_M4/archon.out"
  E2E_HOSTING_OUT="$out" _e2e_run "$E2E_DIR_M4" "$E2E_DIR_M4/archon.out"
  assert_true "resolver printed '${out}' → paused as cloud" \
    "[ '$E2E_RC' = 0 ] && [ -f '$E2E_DIR_M4/state/session-window-paused' ]"
  rm -rf "$E2E_DIR_M4"
done

```

- [ ] **Step 8.2: Verify the local case is failing-first on `main`.** M3 and M4 are parity pins
  and pass on `main` by construction. To prove M2 is a real failing-first test, run:
  `git checkout "$(git merge-base origin/main HEAD)" -- entrypoint.sh scripts/ && bash tests/test_entrypoint_session_window.sh 2>&1 | grep -A3 -- '--- M2:'; git checkout HEAD -- entrypoint.sh scripts/`
  Expected: M2 FAILs on the pre-#586 code. It exits `0`, writes the sentinel and signs
  `environmental:session_window_pause`. M3 and M4 PASS. Confirm the restore with
  `git status --short`, which must list only `tests/test_entrypoint_session_window.sh`.

- [ ] **Step 8.3: Verify pass.** `bash tests/test_entrypoint_session_window.sh` → `0 failed`.

- [ ] **Step 8.4: Commit.**
  `git add tests/test_entrypoint_session_window.sh`
  `git commit -m "test(#586): end-to-end structured-event, cloud-parity and fail-closed resolver cases"`

## Task 9: Argv parity against a literal golden recorded from `main`

**Files:** `tests/test_entrypoint_session_window.sh` (test-only)

The goldens below were recorded from `main` (pre-#586) with these exact harnesses while
authoring this plan, and match it byte for byte. Each `[token]` is one argv element. Temp
directories, the `mktemp` transcript path and the variable elapsed-seconds value are normalized;
every other token is literal. `rate-limit-match` runs only in the main loop, which production's
ERR trap pre-empts (see "Facts"), so its golden comes from the loop harness (N2).

- [ ] **Step 9.1: Write the tests.** Insert before the section-I anchor:

```bash
# --- #586 argv parity -----------------------------------------------------------------------
# Golden argv recorded from main before #586, one [token] per argument; temp paths and the
# elapsed seconds are normalized. No --hosting-location token may appear on the cloud path.
_argv_norm() {  # _argv_norm <dir> <argv.log>
  sed -e "s#$1/tmp/tmp\.[A-Za-z0-9]*#<TMP_OUT>#g" -e "s#$1#<DIR>#g" \
      -e 's#\[--elapsed-seconds\]\[[0-9]*\]#[--elapsed-seconds][<N>]#' "$2"
}
GOLDEN_E2E_FAIL='[<DIR>/clone/dark-factory/scripts/factory_core/cli.py][session-window-check][--tmp-out][<TMP_OUT>][--state-dir][<DIR>/state][--buffer-minutes][5][--fallback-minutes][30]
[<DIR>/clone/dark-factory/scripts/factory_core/cli.py][error-signature-write][--issue][586][--phase][plan][--exit-code][1][--text-file][<TMP_OUT>][--elapsed-seconds][<N>][--commits-since-start][0][--delivery-failure-max-seconds][30][--state-dir][<DIR>/state]'
for variant in unset set-resolves-cloud; do
  echo ""
  echo "--- N: #586 argv parity end to end (FACTORY_MODEL_ENDPOINT ${variant}) ---"
  E2E_DIR_N=$(mktemp -d /tmp/ep-sw-e2e-n-XXXXXX)
  echo "HTTP 429 Too Many Requests" > "$E2E_DIR_N/archon.out"
  if [ "$variant" = unset ]; then
    (unset FACTORY_MODEL_ENDPOINT FACTORY_MODEL_REGISTRY_SHA256; _e2e_run "$E2E_DIR_N" "$E2E_DIR_N/archon.out"; exit "$E2E_RC")
  else
    # A real fixture local entry with no digest: the real resolver fails closed to cloud.
    (export FACTORY_MODEL_ENDPOINT=local-same-host FACTORY_MODEL_REGISTRY_PATH="$REPO_ROOT/tests/fixtures/model_registry"
     unset FACTORY_MODEL_REGISTRY_SHA256; _e2e_run "$E2E_DIR_N" "$E2E_DIR_N/archon.out"; exit "$E2E_RC")
  fi
  assert_eq "failed run exits 1" "1" "$?"
  assert_eq "session-window-check + error-signature-write argv byte-identical to main" \
    "$GOLDEN_E2E_FAIL" "$(_argv_norm "$E2E_DIR_N" "$E2E_DIR_N/argv.log")"
  assert_true "no --hosting-location token anywhere" "! grep -q 'hosting-location' '$E2E_DIR_N/argv.log'"
  rm -rf "$E2E_DIR_N"
done

echo ""
echo "--- N2: #586 argv parity in the main loop (rate-limit-match runs only there) ---"
GOLDEN_LOOP_FAIL='[<DIR>/dark-factory/scripts/factory_core/cli.py][session-window-check][--tmp-out][<TMP_OUT>][--state-dir][<DIR>/state][--buffer-minutes][5][--fallback-minutes][30]
[<DIR>/dark-factory/scripts/factory_core/cli.py][rate-limit-match][--tmp-out][<TMP_OUT>]
[<DIR>/dark-factory/scripts/factory_core/cli.py][error-signature-write][--issue][586][--phase][plan][--exit-code][1][--text-file][<TMP_OUT>][--elapsed-seconds][<N>][--commits-since-start][0][--delivery-failure-max-seconds][30][--state-dir][<DIR>/state]'
LOOP_DIR=$(mktemp -d /tmp/ep-sw-loop-n2-XXXXXX)
SCHEDULER_STATE_DIR="$LOOP_DIR/state"; mkdir -p "$SCHEDULER_STATE_DIR"; export SCHEDULER_STATE_DIR
ARTIFACTS_DIR="$LOOP_DIR/artifacts"; mkdir -p "$ARTIFACTS_DIR"; export ARTIFACTS_DIR
ISSUE_NUM=586; INTENT=plan; RUN_ID=test-run-loop-n2
RUN_STARTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
DF_ENDPOINT_HOSTING=cloud; SESSION_WINDOW_BACKOFF_ENABLED=true
echo "HTTP 429 Too Many Requests" > "$LOOP_DIR/archon.out"
SAVED_TMPDIR="${TMPDIR-}"; export TMPDIR="$LOOP_DIR/tmp"; mkdir -p "$TMPDIR"
_loop_run "$LOOP_DIR" "$LOOP_DIR/archon.out"
if [ -n "$SAVED_TMPDIR" ]; then TMPDIR="$SAVED_TMPDIR"; else unset TMPDIR; fi
assert_eq "loop exits 1" "1" "$LOOP_RC"
assert_eq "all three call sites' argv byte-identical to main" \
  "$GOLDEN_LOOP_FAIL" \
  "$(_argv_norm "$LOOP_DIR" "$LOOP_DIR/argv.log" | sed "s#\[$(dirname "$REPO_ROOT")/#[<DIR>/#")"
rm -rf "$LOOP_DIR"

```

  Why `set-resolves-cloud` uses the real resolver: no `E2E_HOSTING_OUT`, so the stub execs this
  checkout's `providers/cli.py model-endpoints hosting`. It is given the repo's real fixture
  local entry (`tests/fixtures/model_registry`, id `local-same-host`) without a digest, and fails
  closed to `cloud` (Requirement 8). N2's extra `sed` maps the sourced tests' `CLONE_DIR`, which
  is the checkout's parent per the file's TARGET-PATH trick, onto `<DIR>`.

- [ ] **Step 9.2: Verify the goldens are `main`'s.**
  `git checkout "$(git merge-base origin/main HEAD)" -- entrypoint.sh scripts/ && bash tests/test_entrypoint_session_window.sh 2>&1 | grep -A4 -- '--- N'; git checkout HEAD -- entrypoint.sh scripts/`
  Expected: N (both variants) and N2 PASS on the pre-#586 code. That proves the goldens
  are `main`'s argv. Confirm the restore with `git status --short`, which must list only
  `tests/test_entrypoint_session_window.sh`.

- [ ] **Step 9.3: Verify pass on the branch.** `bash tests/test_entrypoint_session_window.sh` →
  `0 failed`. N and N2 pass, so the cloud argv is byte-identical (Requirement 7).

- [ ] **Step 9.4: Commit.**
  `git add tests/test_entrypoint_session_window.sh`
  `git commit -m "test(#586): argv parity — cloud path passes no new token to any CLI (literal golden from main)"`

## Task 10: Reference docs (`run-lifecycle.md`, `configuration.md`, `features.md`)

**Files:** `docs/run-lifecycle.md`, `docs/configuration.md`, `docs/features.md`

The change alters documented behavior, and the implement phase's DOCUMENT step plus Gate 2.5
audit these three docs (architect review, cycles 1-2). Two parts:

- **Step 10.2 (mechanical).** Re-derive **every** `entrypoint.sh` and `error_signature.py` line
  citation in the three docs from the changed files. This is
  `commands/dark-factory-implement.md` Phase 4 step 3: a touched doc that cites a changed
  file's lines must re-derive them. A `difflib` old-to-new line map does it, so a citation that
  pointed at a line on `main` points at the same line afterwards.
- **Step 10.3 (prose).** Correct the passages this ticket makes false:
  - `run-lifecycle.md` §2 gets a step row for the pre-clone resolver.
  - `run-lifecycle.md` §6.F gets the verified-local rule, and its kill-switch sleep is scoped to
    cloud.
  - `run-lifecycle.md` §9 "Workflow failure": items 1-2 are scoped to cloud, and item 4's check
    order gains the local class first.
  - `configuration.md` gets the `model-endpoints hosting` verb next to `show`/`stage`.
  - `features.md`'s "Session-window pausing" row gets the verified-local rule.

`docs/hybrid-execution-contract.md` §4.10/§4.12 still describe #586 as pending. Updating the
contract's rollout status is #587's change, because #587 is what turns local dispatch on. It is
deliberately not edited here.

- [ ] **Step 10.1: Confirm the inputs.** Tasks 6-7 must be committed and the three docs still
  identical to `main`:
  ```bash
  git diff --quiet "$(git merge-base origin/main HEAD)" -- docs/run-lifecycle.md docs/features.md docs/configuration.md && echo docs-pristine
  grep -n 'Model-endpoint hosting location (#586)\|^DF_ENDPOINT_HOSTING="\${DF_ENDPOINT_HOSTING#' entrypoint.sh
  ```
  Expected: `docs-pristine`, then lines `12` and `21`. Those are the bounds the new §2 row 2a
  cites. If they differ because `main` moved, change `12-21` in Step 10.3's script to match.

- [ ] **Step 10.2: Re-derive the citations (run exactly once).** From the repo root:
  ```bash
  python3 - <<'PYEOF'
# Re-derive every entrypoint.sh / error_signature.py line citation in the three touched docs
# from this branch's changed files (implement Phase 4 step 3). Old→new line map from difflib
# over `git show <merge-base>:<file>` vs the working tree; a citation into a changed region
# maps to the region's new bounds. Bare `:N` / `N-M` spans follow the last file cited in the
# same paragraph or table row; run-lifecycle.md §2's Lines column is entrypoint.sh.
import difflib
import re
import subprocess
from pathlib import Path

FILES = {"entrypoint.sh": "entrypoint.sh",
         "error_signature.py": "scripts/factory_core/error_signature.py"}
DOCS = ["docs/run-lifecycle.md", "docs/features.md", "docs/configuration.md"]
# Lines of run-lifecycle.md whose bare `:N` spans cite entrypoint.sh although another path, or a
# paragraph break, sits between them and the last entrypoint.sh citation (architect cycle 3).
ENTRYPOINT_HINTS = ["(`:1102-1137`).", "the scheduler's breaker owns that (`:642-663`)",
                    "saying so (`:664-688`)", "at `:840-845`. `on_failure` then calls",
                    "guard (`:649`, `:672`)", "the move to Blocked (`:669`) happen",
                    "`run_post_mortem` (`:233`) uses"]
BASE = subprocess.run(["git", "merge-base", "origin/main", "HEAD"], capture_output=True,
                      text=True, check=True).stdout.strip()


def line_maps(path):
    old = subprocess.run(["git", "show", f"{BASE}:{path}"], capture_output=True,
                         text=True, check=True).stdout.splitlines()
    new = Path(path).read_text().splitlines()
    start, end = {}, {}
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        for k in range(i1, i2):
            if tag == "equal":
                start[k + 1] = end[k + 1] = j1 + (k - i1) + 1
            else:  # replaced/deleted: the new region's bounds (a pure delete keeps j1)
                start[k + 1], end[k + 1] = j1 + 1, max(j2, j1 + 1)
    return start, end


# Run exactly once: the map is main → branch, so a second pass would shift every citation again.
if subprocess.run(["git", "diff", "--quiet", BASE, "--", *DOCS]).returncode != 0:
    raise SystemExit("docs already differ from the merge-base: re-derive only once, before the prose edits")
MAPS = {key: line_maps(path) for key, path in FILES.items()}
SPEC_RE = re.compile(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*")


def remap_spec(spec, key):
    start, end = MAPS[key]
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        na = start.get(int(a), int(a))
        out.append(f"{na}-{end.get(int(b), int(b))}" if b else str(na))
    return ",".join(out)


def file_key(path):
    for key in FILES:
        if path == key or path.endswith("/" + key):
            return key
    return None


def remap_line(line, current, force=False):
    def span(m):
        nonlocal current
        body = m.group(1)
        path, colon, spec = body.rpartition(":")
        if colon and path and " " not in path and SPEC_RE.fullmatch(spec):
            current = file_key(path) or path
            return f"`{path}:{remap_spec(spec, current)}`" if current in FILES else m.group(0)
        if body.startswith(":") and SPEC_RE.fullmatch(body[1:]):
            key = "entrypoint.sh" if force else current
            return f"`:{remap_spec(body[1:], key)}`" if key in FILES else m.group(0)
        if SPEC_RE.fullmatch(body):
            return f"`{remap_spec(body, current)}`" if current in FILES else m.group(0)
        if " " not in body and "/" in body or body in FILES:
            current = file_key(body) or body
        return m.group(0)
    return re.sub(r"`([^`\n]+)`", span, line), current


def remap_doc(path):
    lines = Path(path).read_text().split("\n")
    current, in_steps = None, False
    for n, line in enumerate(lines):
        if not line.strip() or line.startswith("|"):
            current = None  # a paragraph or table row starts fresh
        if path.endswith("run-lifecycle.md"):
            if line.startswith("| # | Step | Lines | Notes |"):
                in_steps = True
            elif in_steps and not line.startswith("|"):
                in_steps = False
            elif in_steps and not line.startswith("|---"):
                current = "entrypoint.sh"  # every cell of this table cites entrypoint.sh
                cells = line.split("|")
                if len(cells) > 4 and SPEC_RE.search(cells[3]):
                    parts = re.split(r"(`[^`]*`)", cells[3])
                    cells[3] = "".join(p if p.startswith("`") else
                                       SPEC_RE.sub(lambda m: remap_spec(m.group(0), "entrypoint.sh"), p)
                                       for p in parts)
                    line = "|".join(cells)
        force = path.endswith("run-lifecycle.md") and any(h in line for h in ENTRYPOINT_HINTS)
        lines[n], current = remap_line(line, current, force)
    Path(path).write_text("\n".join(lines))


for doc in DOCS:
    remap_doc(doc)
print("citations re-derived")
PYEOF
  ```
  Expected output: `citations re-derived`.
  - The map's base is `git merge-base origin/main HEAD`, so a `main` that moves after the
    branch forks cannot skew it.
  - The script refuses a second run (exit 1, `docs already differ from the merge-base`). The
    map runs base-to-branch, so a second run would shift every citation again.
  - `ENTRYPOINT_HINTS` covers the seven `run-lifecycle.md` lines whose bare `:N` spans cite
    `entrypoint.sh` across a paragraph break or another path (architect cycle 3).

  Spot-check the result:
  ```bash
  grep -c 'entrypoint.sh:306-383' docs/run-lifecycle.md
  grep -c 'error_signature.py:80-126' docs/run-lifecycle.md
  grep -n '(reason at `:24-28`)' docs/run-lifecycle.md
  grep -c '(`:1102-1137`)' docs/run-lifecycle.md
  grep -n "breaker owns that (\`:665-686\`)" docs/run-lifecycle.md
  ```
  Expected, in order:
  - `1`, `1`, then one match. On `main`, `_handle_session_window_pause` was 295-366,
    `classify()` was 80-114 and the clone-URL comment was `:13-17`.
  - `0`, then one match. The §9 item that cited `:642-663` on `main` has been re-derived via
    the hint list.

- [ ] **Step 10.3: Apply the prose edits.** Each `edit()` asserts its anchor occurs exactly once,
  so a drifted doc fails loudly instead of being edited in the wrong place:
  ```bash
  python3 - <<'PYEOF'
from pathlib import Path


def edit(path, old, new):
    p = Path(path)
    s = p.read_text()
    assert s.count(old) == 1, (path, old[:70], s.count(old))
    p.write_text(s.replace(old, new))


# run-lifecycle.md §2: a step row for the resolver, right after the provider check.
edit("docs/run-lifecycle.md",
     "A failure here ends the run before any ticket signal exists |\n",
     "A failure here ends the run before any ticket signal exists |\n"
     "| 2a | Resolve the model-endpoint hosting location (`providers/cli.py model-endpoints hosting`, "
     "the baked copy) | 12-21 | Sets `DF_ENDPOINT_HOSTING` to `local` only when the resolver prints "
     "exactly `hosting=local`; anything else is `cloud`. Built on `load_verified_entry`, so it stays "
     "`cloud` until #587 lifts the local-dispatch block (#586, "
     "[section 6.F](#f-rate-limit-and-session-window-handling-entrypoint)) |\n")

# run-lifecycle.md §6.F: the verified-local rule; the kill-switch sleep is cloud-only.
edit("docs/run-lifecycle.md",
     "`scheduler.session_window_buffer_minutes`, `scheduler.session_window_fallback_minutes`.\n",
     "`scheduler.session_window_buffer_minutes`, `scheduler.session_window_fallback_minutes`.\n"
     "\n"
     "On a verified-local run (`DF_ENDPOINT_HOSTING=local`, #586) a session-window or rate-limit\n"
     "match is never a pause: `session-window-check --hosting-location local` writes no sentinel,\n"
     "the legacy sleep below is skipped, and the run takes the normal failure path with the\n"
     "signature `substantive:endpoint_unreachable:<exit code>`, so two in a row trip the breaker\n"
     "(`scripts/factory_core/session_window.py`, `scripts/factory_core/error_signature.py`). On\n"
     "cloud, the only hosting before #587, nothing changes and no new argument is passed.\n")
edit("docs/run-lifecycle.md",
     "(`entrypoint.sh:306-383`, `1149-1153`)",
     "(`entrypoint.sh:306-383`, called at `:592` and `:1134-1137`)")
edit("docs/run-lifecycle.md",
     "re-run the whole workflow\n(`:1125-1164`).",
     "re-run the whole workflow\n(`entrypoint.sh:1145-1181`, the guarded `rate-limit-match` block).")
edit("docs/run-lifecycle.md",
     "With the backoff disabled, a rate-limit match makes the entrypoint sleep until the",
     "With the backoff disabled, a rate-limit match on a cloud run makes the entrypoint sleep until the")

# run-lifecycle.md §9 "Workflow failure": items 1, 2 and 4 in the new check order.
edit("docs/run-lifecycle.md",
     "1. Session-window match: exit 0 (gate F).\n2. Rate-limit match with backoff disabled: sleep and retry.\n",
     "1. Session-window match on a cloud run: exit 0 (gate F).\n"
     "2. Rate-limit match with backoff disabled, on a cloud run: sleep and retry. A verified-local\n"
     "   run (#586) takes neither branch and continues below.\n")
edit("docs/run-lifecycle.md",
     "dir for the scheduler. Classes, in check order: `environmental:delivery_failure` (run",
     "dir for the scheduler. Classes, in check order: on a verified-local run only (#586),\n"
     "   `substantive:endpoint_unreachable:<exit code>` for rate-limit or exhaustion output (text\n"
     "   or a rejected `claude.rate_limit_event`); then `environmental:delivery_failure` (run")

# configuration.md "Model endpoint registry": the new verb, next to show/stage.
edit("docs/configuration.md",
     "`model-endpoints show --from-source`.\n",
     "`model-endpoints show --from-source`.\n"
     "\n"
     "A run container also resolves its hosting location once, before the clone, with\n"
     "`model-endpoints hosting` on the baked CLI. It prints exactly `hosting=local` or\n"
     "`hosting=cloud` and always exits 0; any problem prints `hosting=cloud`. It reads the same\n"
     "staged copy and digest as `model-endpoints show`, so it stays `cloud` for every entry until\n"
     "#587 (#586; [run-lifecycle.md](run-lifecycle.md) section 6.F).\n")

# features.md "Session-window pausing" row: the verified-local rule.
edit("docs/features.md",
     "Test: `tests/test_entrypoint_session_window.sh` (CI). |",
     "A verified-local run (#586) never pauses: it fails as `substantive:endpoint_unreachable`. "
     "Test: `tests/test_entrypoint_session_window.sh` (CI). |")
print("docs edited")
PYEOF
  ```
  Expected output: `docs edited`. Two of these edits correct §6.F citations that drifted before
  this ticket, inside the sentences Task 10 rewrites. Both values come from `grep -n` on the
  branch file:
  - the pause call sites, now `:592` and `:1134-1137`;
  - the guarded `rate-limit-match` sleep block, now `entrypoint.sh:1145-1181`.

  The rest of the §2 table's drift that predates #559, and the same 17-line drift in the bare `:N` citations of §9 and the oddities list (for example `on_failure` and the `:665-686` spot-check above), is not corrected here. Those rows
  pointed at the wrong lines on `main` already and keep pointing at the same targets. A
  separate docs ticket owns that re-mapping.

- [ ] **Step 10.4: Verify.**
  `python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md`
  Expected: `Docs reference check passed for 4 doc(s).` Then
  `PYTHONPATH=scripts python -m pytest tests/ -v -k doc`. Expected: all pass. Then
  `git diff --stat -- docs/` should show about 128 insertions and 111 deletions across the
  three files (measured on `cf38d4d`).

- [ ] **Step 10.5: Commit.**
  `git add docs/run-lifecycle.md docs/configuration.md docs/features.md`
  `git commit -m "docs(#586): re-derive entrypoint/error_signature citations; verified-local runs never pause"`

## Task 11: Full verification and scope check

**Files:** none (verification only)

- [ ] **Step 11.1: Python suite (CI parity).** `PYTHONPATH=scripts python -m pytest tests/ -v`.
  Expected: all pass.
- [ ] **Step 11.2: CI shell tests.** Run every shell test CI lists:
  ```bash
  for t in $(grep -o 'bash tests/test_[a-z_0-9]*\.sh' .github/workflows/ci.yml | awk '{print $2}'); do
    bash "$t" >/tmp/ci-586.log 2>&1 && echo "PASS $t" || { echo "FAIL $t"; tail -5 /tmp/ci-586.log; }
  done
  ```
  Expected: every line is `PASS`. In particular, these still pass unchanged:
  - `tests/test_scheduler.sh`, whose section B4 (`rollback_paused_retry`, lines 232-269) is the
    #341 pin.
  - `tests/test_provider_env_flags.sh`.
  - `tests/test_entrypoint_current_run.sh`, which sources the file twice, so `readonly` must not
    have crept in.

  Also run, outside the CI list, `bash tests/test_session_window_gate.sh` (expected: `Results: 12 passed, 4 failed`, identical on untouched `main`; its four `Priority N:` ordering checks are stale, it is not in CI, do not touch it or `scheduler.sh`) and
  `PYTHONPATH=scripts python -m pytest tests/test_provider_env_passthrough.py -v` (expected:
  pass). `tests/test_entrypoint_fix_main.sh` fails identically on `main` and is not in CI. Leave
  it.
  Inside a factory run container, `tests/test_identity.sh` (`FAIL status ids`) and
  `tests/test_provider_env_flags.sh` (`-e JIRA_PROJECT_KEY -e JIRA_EPIC_LINK_FIELD`) also fail on
  untouched `main`. The container's own `FACTORY_STATUS_*`/`JIRA_*` environment leaks into them.
  Both pass on CI's clean runner and in `env -i HOME="$HOME" PATH="$PATH" bash tests/<file>.sh`.
  This is not a regression from this ticket.
- [ ] **Step 11.3: Workflow DAG checks.**
  `python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml && python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml && python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md`
  Expected: exit 0.
- [ ] **Step 11.4: Scope check.** Use the two-dot form; it is net-of-main and avoids
  false OOS hits:
  `git diff --name-only origin/main HEAD`
  Expected: exactly these files. The plan and spec themselves arrive via
  `transfer_refine_artifacts.sh`.
  ```
  docs/configuration.md
  docs/features.md
  docs/run-lifecycle.md
  docs/superpowers/plans/2026-11-26-endpoint-aware-session-window-breaker.md
  docs/superpowers/specs/2026-11-26-endpoint-aware-session-window-breaker-design.md
  entrypoint.sh
  scripts/factory_core/cli.py
  scripts/factory_core/error_signature.py
  scripts/factory_core/providers/cli.py
  scripts/factory_core/session_window.py
  tests/test_entrypoint_error_signature.sh
  tests/test_entrypoint_session_window.sh
  tests/test_factory_core_breaker.py
  tests/test_factory_core_error_signature.py
  tests/test_factory_core_session_window.py
  tests/test_provider_model_endpoint.py
  ```
  The `.archon/memory/architecture.md` refine-phase memory commit may also appear. Anything else
  is out of scope and must be reverted. In particular, any path under `scheduler.sh`,
  `scripts/factory_core/breaker.py`, `scripts/provider_env.sh`, `model_endpoint.py`,
  `model_adapter.py`, `config/`, `deploy/`, `workflows/` or `.github/` is out of scope.
- [ ] **Step 11.5: Hard-block grep.**
  `git diff origin/main HEAD -- scripts/factory_core/providers/model_endpoint.py | wc -l` → `0`.
  Then `grep -n "_NO_LOCAL_DISPATCH" scripts/factory_core/providers/model_endpoint.py` still shows
  four lines: the definition (`:88`), both appends (`:649`, `:722`) and the
  `load_verified_entry` docstring mention (`:691`).

## Self-review (plan author)

- `**Issue:** #586` line present under the title.
- No placeholders. Every code step has the full code, and every verify step has the exact
  command and expected outcome.
- All code in this plan was applied to a scratch copy of current `origin/main` (`cf38d4d`, which
  includes #559 and #560) and run while authoring. The test files were rebuilt directly from this
  plan's code blocks, in the plan's order.
  - The 22 new Python tests in Tasks 1-4 FAIL on `main` and PASS with the change. The two Task 5
    pins pass on both.
  - `tests/test_entrypoint_session_window.sh`: 30 of the new assertions FAIL on the pre-#586
    code. With the change, all 105 pass, in about 75 s.
  - `tests/test_entrypoint_error_signature.sh`: all 17 pass.
  - Under a clean `env -i` environment, every shell test in `ci.yml` passes. This includes the
    updated `tests/test_entrypoint_current_run.sh`, which sources the file several times.
  - The full pytest suite passes (3767), and so do the three DAG/docs checks.
  - Task 10's citation re-derivation and prose edits pass `docs_refs_check.py` and every
    `-k doc` test. The re-derivation's refusal of a second run was checked.
- Spec coverage:

  | Spec item | Covered by |
  |---|---|
  | Req. 1 | Task 4 and Step 11.5 |
  | Req. 2 | Tasks 2 and 5 |
  | Req. 3 | Tasks 1 and 7 (J) |
  | Req. 4 | Task 7: K/K2 for `on_failure`; L/L2 and end-to-end M for the main loop |
  | Req. 5 | Task 7 (L, L2) |
  | Req. 6 | Tasks 4 and 6, plus the M ordering assertion in Task 7 |
  | Req. 7 | Task 9 |
  | Req. 8 | Tasks 4 and 8 (M4) |
  | Req. 9 | Task 11 |
  | Testing items 1-9 | 1 → Task 1; 2 → Task 1; 3 → Task 2; 4 → Task 4; 5 → Tasks 7/8; 6a → Tasks 7/8; 6b → Task 7 K/K2; 7 → Task 9; 8 → Task 5; 9 → Task 11 |
