# Endpoint-Aware Session-Window Detection and Breaker Signature

**Issue:** #586

---

## Overview / Problem Statement

`scripts/factory_core/session_window.py` pauses the *entire* factory (every issue, every
target) whenever a run's captured stdout looks like a Claude session/usage exhaustion, or
carries a rejected structured `claude.rate_limit_event`. The same captured text also feeds
`scripts/factory_core/error_signature.py`'s `classify()`, which buckets a bare 429/rate-limit
match into `environmental:rate_limit` for the breaker. Neither path can tell which *endpoint*
produced the text. Once a local, self-hosted inference server becomes a real dispatch target
(#587, which itself depends on this ticket per the contract's §4.11 decision 3), a local
server's own rate-limit-shaped output, or a local HTTP 429, must never (a) pause unrelated
cloud work, or (b) consume the "two consecutive substantive failures" breaker budget as if it
were a transient, retry-worthy environmental blip — it is a real, local-only failure.

This is the ticket's own, human-reviewed, security-sensitive change (CLAUDE.md requires one
for any `gate_*`/breaker touch); `spec-pending-review` on this document is that review.

### Re-refinement history

A first spec (committed at `6049bf0`) was rejected by operator review for three concrete
defects: it left a local match able to fall into `entrypoint.sh`'s legacy
`rate-limit-match` sleep-forever loop at the main-loop call site; it still let
`check_and_pause` write the shared pause sentinel on a local match; and it split the
breaker's signature into an endpoint-suffixed file, which the operator rejected outright (a
container-chosen filename must never influence a safety mechanism). The issue body was then
amended with ten binding owner decisions (2026-11-25); this document implements them. A
second refinement attempt was interrupted by a session-window pause of the factory itself
(ironic, given the subject) before it produced a spec — nothing from that attempt is reused
here (no spec existed on the branch, so this is a full re-refinement against current `main`
and today's binding owner decisions).

**Correction to the issue's own `#341` citation chain:** `_rate_limit_match`
(`scripts/factory_core/cli.py:178-191`) was already rewritten by #344 (merged after the
issue body and the rejected spec were written) to use the same strict
`is_session_window_failure` detector `session-window-check` uses, instead of the broad
`RATE_LIMIT_RE`. That closed the "any bare 429 sleeps entrypoint.sh forever" bug **for
cloud**. It does not close the gap this ticket is about: `rate-limit-match` has no
hosting-location awareness, so it independently re-evaluates the *same* text a verified-local
match already produced, and would still report `matched=true` — re-opening exactly the
infinite-retry failure mode decision 4 forbids, just narrowed to the local-only case. This
spec closes that remaining gap explicitly (Requirement 5 / Architecture §3).

## Requirements

Numbered to match the issue's "Refinement constraints" where they map 1:1; three are this
spec's own (re-)verified additions.

1. **No change to the `_NO_LOCAL_DISPATCH` hard block.** The new hosting resolver is built
   directly on `model_endpoint.load_verified_entry()`, unmodified. No new reader/resolver may
   accept a local entry that path refuses. Until #587 removes the block
   (`scripts/factory_core/providers/model_endpoint.py:649`), the resolver always prints
   `hosting=cloud` in production; the local branch is exercised only by tests that patch
   `load_verified_entry` (patching the `_NO_LOCAL_DISPATCH` constant itself cannot work:
   `_local_problems` appends it unconditionally, so `problems` stays non-empty).
   `scripts/provider_env.sh` forwards `FACTORY_MODEL_ENDPOINT` to every dispatch; only
   `_NO_LOCAL_DISPATCH` (and the `entrypoint.sh:10` preflight) blocks local today.
2. **One signature file, one bucket, as today.** No change to
   `error_signature.write_signature`'s path (`<state_dir>/error-signatures/<issue>.<phase>.sig`)
   or to `breaker.py`'s `<key>:sig` state key. A local-sourced rate-limit/exhaustion match gets
   its own `substantive:*` class instead of `environmental:rate_limit`; everything downstream
   (two-in-a-row trip detection, `reset_retry`) is otherwise untouched.
3. **No pause sentinel on a verified-local match.** `session_window.check_and_pause` must not
   call `write_pause_sentinel` when the match is attributable to a verified-local endpoint.
4. **No infinite free retries, at both `entrypoint.sh` call sites.** A verified-local match
   must reach `run_post_mortem` / `_write_error_signature` / a counted `exit`, at the main loop
   (`entrypoint.sh:1086-1145`) *and* via the `on_failure` ERR trap (`entrypoint.sh:549-694`) —
   never the legacy `rate-limit-match` sleep loop (`entrypoint.sh:1102-1137`, the only call site
   that has one).
5. **`rate-limit-match` must independently stop matching for a verified-local source.** Not
   explicit in the issue (written before #344 landed), but required by Requirement 4: since
   `rate-limit-match` now runs the identical strict detector `session-window-check` runs, a
   verified-local match that `session-window-check` correctly suppressed would otherwise still
   be caught by `rate-limit-match`'s own, separate evaluation of the same text.
6. **Hosting resolved once, early, via the baked CLI, before the clone.** Via
   `/opt/dark-factory/scripts/factory_core/providers/cli.py` — never the clone's or branch's
   own copy — for this one relaxing decision. No new flag carries the registry digest; the
   resolver reads `FACTORY_MODEL_REGISTRY_SHA256`/`FACTORY_MODEL_ENDPOINT` from the environment
   exactly as `load_verified_entry`/`read_verified_tree` already do internally.
7. **Byte-identical default argv.** When hosting resolves to `cloud` (today's only live case,
   and the case for every target that hasn't opted into a local endpoint), no new flag is
   passed to any CLI invocation anywhere in `entrypoint.sh`. An argv-parity test pins this.
8. **Fail conservative.** A missing/unset endpoint id, a missing or mismatched digest, an
   unknown id, or an unreadable/invalid registry all resolve to `cloud` — the resolver never
   raises out of `entrypoint.sh`'s startup sequence; any problem is swallowed and printed as
   `hosting=cloud`.
9. **No change to:** the plain retry counter (`breaker.get_retry_count`/`increment_retry`),
   the #341 pause-doesn't-consume-a-retry rule, breaker trip thresholds, `token_optimization.*`
   budgets, `scheduler.sh`'s pause gate, `config/config.yaml`, `deploy/**`, workflow DAG files,
   or tool allow/deny lists. Nothing from #585, #587, #559, #611, #615 or #616 is touched.
10. **Tests, failing first** — enumerated in full in [Testing](#testing) below.

## Architecture / Approach

### 1. One new resolver: `providers/cli.py model-endpoints hosting`

A third verb alongside the existing `model-endpoints show|stage` group
(`scripts/factory_core/providers/cli.py:433-444`), handled by a new `_model_endpoints_hosting`
next to `_model_endpoints_show` (line 241):

```python
def _model_endpoints_hosting(args):
    from factory_core.providers import model_endpoint
    endpoint_id = os.environ.get("FACTORY_MODEL_ENDPOINT", "")
    hosting = "cloud"
    if endpoint_id:
        problems, entry = model_endpoint.load_verified_entry(
            model_endpoint.staged_registry_dir(), endpoint_id)
        if problems:
            print(f"WARNING: model-endpoint-hosting: {'; '.join(problems)}", file=sys.stderr)
        elif entry["hosting_location"] == "local":
            hosting = "local"
    print(f"hosting={hosting}")
```

No new flags: the endpoint id, the registry path (`model_endpoint.staged_registry_dir()`,
which itself reads `FACTORY_MODEL_REGISTRY_PATH` or defaults to
`/var/lib/dark-factory/model-registry`) and the digest
(`read_verified_tree`'s own `FACTORY_MODEL_REGISTRY_SHA256` read, inside `load_verified_entry`)
all come from the environment exactly as the existing `model-endpoints show --staged` path
already does. Exit code is always 0 — `entrypoint.sh` fail-closes on *content*
(anything other than exactly `hosting=local`), not on exit code, so a crash inside this
function is indistinguishable from "resolved to cloud" on the bash side too (belt-and-suspenders
with the `|| echo hosting=cloud` fallback below).

Because `load_verified_entry` keeps `_NO_LOCAL_DISPATCH` (Requirement 1), `problems` is
non-empty for every real local registry entry today, so this prints `hosting=cloud`
unconditionally in production until #587. Unit tests exercise the `local` branch by patching
`model_endpoint.load_verified_entry` directly — never by adding a second code path that
bypasses the real one.

### 2. `entrypoint.sh`: resolve once, pre-clone, via the baked path

Immediately after the existing preflight call (`entrypoint.sh:10`), before `CLONE_DIR` is
even assigned (`entrypoint.sh:19`) or the clone happens (`entrypoint.sh:825`):

```bash
# --- Resolve model-endpoint hosting location once, via the baked (never the clone's) CLI,
# before any branch-controlled code can run. Fails closed to cloud (#586). ---
DF_ENDPOINT_HOSTING=$(python3 "$FACTORY_PROVIDERS_CLI" model-endpoints hosting 2>/dev/null \
  | grep -o '^hosting=local$' || echo "hosting=cloud")
DF_ENDPOINT_HOSTING="${DF_ENDPOINT_HOSTING#hosting=}"
```

`$FACTORY_PROVIDERS_CLI` is already the baked, absolute path
(`entrypoint.sh:9`, `/opt/dark-factory/scripts/factory_core/providers/cli.py` by default) —
no new indirection needed. The `grep -o '^hosting=local$'` makes any output other than exactly
that literal string (empty, `hosting=cloud`, a stray warning line that leaked past
`2>/dev/null`, a crash) fail closed to `cloud` through the `||` fallback. `DF_ENDPOINT_HOSTING`
is then a plain `local`/`cloud` string, available to every later section of the script
including the post-clone functions below (bash globals persist across `cd`). It is not marked
read-only: `tests/test_entrypoint_current_run.sh` sources `entrypoint.sh` twice in one shell, and
every file sourced after this point is baked. The assignment is unconditional, so a value
inherited from the environment is always overwritten. The three guards below read it as
`${DF_ENDPOINT_HOSTING:-cloud}`, so an unset value means cloud.

### 3. Three downstream call sites, each gated on the one cached variable

All three existing helper functions/call sites gain a conditional, and nothing else — no new
return codes, no restructuring of `on_failure` or the main loop's control flow.

`scripts/factory_core/cli.py`'s argparse layer (`main()`, lines ~426-455) needs exactly two new
optional arguments, both `default="cloud"`, both `choices=["cloud", "local"]`: `--hosting-location`
on the `session-window-check` subparser (`sw`, line 426) and on the `error-signature-write`
subparser (`esw`, line 437), with `_session_window_check`/`_error_signature_write`
(lines 151/194) forwarding `args.hosting_location` into `check_and_pause`/`classify`. The
`rate-limit-match` subparser (`rlm`, line 433) gets **no** new argument and `_rate_limit_match`
(line 178) is untouched — the gap at that call site is closed entirely in bash (§3b below), not
in Python.

**a. `_handle_session_window_pause` (`entrypoint.sh:278-349`)** — thread
`--hosting-location` into the existing `session-window-check` call (line 285) only when local:

```bash
local hosting_flag=()
[ "${DF_ENDPOINT_HOSTING:-cloud}" = "local" ] && hosting_flag=(--hosting-location local)
sw_result=$(python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" session-window-check \
  --tmp-out "$tmp_out" \
  --state-dir "${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}" \
  --buffer-minutes "${SESSION_WINDOW_BUFFER_MINUTES:-5}" \
  --fallback-minutes "${SESSION_WINDOW_FALLBACK_MINUTES:-30}" \
  "${hosting_flag[@]}" 2>&1)
```

On the Python side, `session_window.check_and_pause` gains an optional
`hosting_location="cloud"` parameter (detection unchanged — `compute_resume_epoch` is not
touched):

```python
def check_and_pause(text, state_dir, now_epoch, buffer_minutes, fallback_minutes,
                     hosting_location="cloud"):
    resume_epoch = compute_resume_epoch(text, now_epoch, buffer_minutes, fallback_minutes)
    if resume_epoch is None or hosting_location == "local":
        return None
    write_pause_sentinel(resume_epoch, state_dir)
    return resume_epoch
```

A verified-local match therefore returns `None` — the *identical* wire shape
(`matched=false resume_epoch=0`) `_session_window_check` already prints for "no match at all"
(`scripts/factory_core/cli.py:174-175`). `_handle_session_window_pause` already returns 1 (not
paused) whenever `matched` isn't `true` (`entrypoint.sh:299`) — no bash-side change needed
beyond threading the flag. This single collapse (suppressed-local ≡ no-match, on the wire) is
why neither call site of `_handle_session_window_pause` needs new branching: both already treat
a false return as "fall through," which is exactly Requirement 4's destination.

**b. Main loop's `rate-limit-match` check (`entrypoint.sh:1102`)** — a pure bash guard, no
Python change to `rate-limit-match` itself (kept byte-identical in code and argv, for the
reasons in [Alternatives](#alternatives-considered)):

```bash
if [ "${DF_ENDPOINT_HOSTING:-cloud}" != "local" ] && python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" rate-limit-match \
    --tmp-out "$TMP_OUT" | grep -q '^matched=true$'; then
```

When `DF_ENDPOINT_HOSTING=local`, the `&&` short-circuits before the Python call ever runs, and
execution falls straight through to the existing `run_post_mortem` / `_write_error_signature` /
`exit "$EXIT_CODE"` block (`entrypoint.sh:1138-1141`) — unchanged code, now reachable for this
case. This also closes the case `SESSION_WINDOW_BACKOFF_ENABLED=false`: that kill-switch makes
`_handle_session_window_pause` return 1 immediately, before it ever calls
`session-window-check` (`entrypoint.sh:280`), so a Python-only fix inside that function would
never run for a local match while the kill-switch is off — the bash guard here runs
unconditionally, independent of the kill-switch, and is the only point that fully closes
Requirement 5.

**c. `_write_error_signature` (`entrypoint.sh:418-477`)** — thread the same flag into the
existing `error-signature-write` call (line 466), again inside the one function body so none
of its three call sites (`on_failure`'s two branches, lines 647/665, and the main loop's line
1139) need to change:

```bash
local hosting_flag=()
[ "${DF_ENDPOINT_HOSTING:-cloud}" = "local" ] && hosting_flag=(--hosting-location local)
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
```

On the Python side, `error_signature.classify` gains the same optional
`hosting_location="cloud"` parameter. Cloud detection is unchanged. For a verified-local run
only, one new first branch tests `_RATE_LIMIT_RE.search(text)` plus
`is_session_window_failure(text)` (imported from `.session_window`, next to the existing
`RATE_LIMIT_RE` import): a rejected structured `claude.rate_limit_event` line pauses on cloud
but does not match `_RATE_LIMIT_RE`, so without the second test it would never get the new
class. Every existing branch of `classify()` (delivery_failure, rate_limit, verdict_missing,
preview_infra, oos_files, build_failure, test_failure, the `unknown` fallback) is untouched:

```python
def classify(text, exit_code, *, elapsed_seconds, commits_since_start, worktree_dirty,
             artifact_present, delivery_failure_max_seconds=30, hosting_location="cloud"):
    if hosting_location == "local" and (
            _RATE_LIMIT_RE.search(text) or is_session_window_failure(text)):
        # Imported here, never at module top: the default cloud path must not load
        # model_adapter/model_endpoint (model_adapter.py's own module docstring).
        from .providers.model_adapter import ENDPOINT_UNREACHABLE  # contract §4.6 interim class
        return f"substantive:{ENDPOINT_UNREACHABLE}:{exit_code}"
    if (elapsed_seconds < delivery_failure_max_seconds and commits_since_start == 0
            and not worktree_dirty and not artifact_present):
        return "environmental:delivery_failure"
    if _RATE_LIMIT_RE.search(text):
        return "environmental:rate_limit"
    ...  # unchanged below
```

The new branch is placed *above* the delivery-failure check (operator review, 2026-11-25): a
local endpoint that answers 429 within seconds would otherwise be signed
`environmental:delivery_failure` and take the full retry ceiling instead of tripping the
breaker on the second attempt, which is not owner decision 2. It fires only when the text
matches, so an early crash with an empty transcript is still `environmental:delivery_failure`.
It is guarded on `hosting_location == "local"`, so it is a strict no-op on the default/cloud
path: every existing branch below it still runs unchanged for every cloud call, and
`model_adapter` is imported only inside the local branch, never on the cloud path.

### Why `substantive:endpoint_unreachable`, not a new coined name

`scripts/factory_core/providers/model_adapter.py:35-50` already implements contract §4.6's
interim failure-class enumeration as `FAILURE_CLASSES` — built for #558's Result-contract
classification of a local endpoint's *request-level* failures (`status_failure_class`,
`model_adapter.py:219-221`, maps a 502/503/504 "not ready" upstream to `ENDPOINT_UNREACHABLE`;
the same constant is also the generic catch-all for any unrecognized connection exception,
`model_adapter.py:274`). Reusing `ENDPOINT_UNREACHABLE` here — a *different* trigger (text/event
shape, not an HTTP status) landing in the *same* bucket — keeps exactly one §4.6 enumeration in
the codebase instead of two that #238 (the ticket that owns naming this taxonomy for real)
would have to reconcile later. The issue's own example (`substantive:endpoint_unreachable`)
already named this constant; this spec imports it rather than retyping the string literal.
`:<exit_code>` is appended for consistency with every other exit-code-bearing `substantive:*`
class `classify()` already returns (`oos_files`, `build_failure`, `test_failure`, `unknown`) —
`verdict_missing` is the sole exception, and is unrelated to this change.

## Files Touched

Changed:

- `entrypoint.sh` — the resolver block after the preflight call; one conditional each in
  `_handle_session_window_pause`, the main loop's `rate-limit-match` check and
  `_write_error_signature`.
- `scripts/factory_core/providers/cli.py` — the `model-endpoints hosting` verb.
- `scripts/factory_core/cli.py` — `--hosting-location` on the `session-window-check` and
  `error-signature-write` subparsers, forwarded by their two handlers.
- `scripts/factory_core/session_window.py` — `check_and_pause`'s `hosting_location` parameter.
- `scripts/factory_core/error_signature.py` — `classify`'s `hosting_location` parameter and
  the one local branch.
- Tests: `tests/test_factory_core_session_window.py`,
  `tests/test_factory_core_error_signature.py`, `tests/test_factory_core_breaker.py`,
  `tests/test_provider_model_endpoint.py` (or the new
  `tests/test_provider_cli_model_endpoints_hosting.py`), `tests/test_entrypoint_session_window.sh`
  and `tests/test_entrypoint_error_signature.sh`. Extend these existing shell tests rather than
  adding a sibling: a new `tests/test_*.sh` runs in CI only if it is listed in
  `.github/workflows/ci.yml`, which this ticket does not touch.

Not changed: `scheduler.sh` (pause gate, pause lift, signature read, #341 rollback),
`scripts/factory_core/breaker.py` (thresholds and the two-consecutive-identical-`substantive:`
rule), `scripts/provider_env.sh`, `scripts/factory_core/providers/model_endpoint.py` (the
`_NO_LOCAL_DISPATCH` hard block), `scripts/factory_core/providers/model_adapter.py`,
`config/config.yaml`, `deploy/**`, `workflows/**`, `.github/workflows/*`, tool allow/deny
lists and `.factory/adapter.yaml`.

## Testing

Per Requirement 10 / the issue's own test list, re-expressed against the concrete design above
(write these failing first, against current `main`, before any production code changes):

1. `tests/test_factory_core_session_window.py`: `check_and_pause(local_exhaustion_text, ...,
   hosting_location="local")` → `None`; no `session-window-paused` file written to the
   temp state dir. Parity case: the identical call with `hosting_location="cloud"` (and with
   the parameter omitted) is byte-identical to today — sentinel written, `resume_epoch`
   returned.
2. `tests/test_factory_core_session_window.py`: same for a local structured
   `claude.rate_limit_event` (`status: "rejected"`) payload — `None`, no sentinel.
3. `tests/test_factory_core_error_signature.py`: `classify(local_rate_limit_text, ...,
   hosting_location="local")` → `"substantive:endpoint_unreachable:<exit_code>"`. Parity case:
   `hosting_location="cloud"`/omitted on the same text → unchanged `"environmental:rate_limit"`.
   A non-rate-limit local failure (e.g. a build failure) with `hosting_location="local"` still
   returns its existing `substantive:build_failure:*` class — the new branch must not shadow
   unrelated classifications. A rejected structured `claude.rate_limit_event` line with
   `hosting_location="local"` → `"substantive:endpoint_unreachable:<exit_code>"`; the same text
   with `cloud`/omitted → unchanged from today. Precedence: fast, zero commits, clean worktree,
   no artifact and local 429 text → the endpoint class; the same inputs with empty text → still
   `"environmental:delivery_failure"`. Import isolation, checked in a fresh subprocess: after
   importing `error_signature` and calling `classify` on the cloud path,
   `factory_core.providers.model_adapter` is not in `sys.modules`.
4. New test (`tests/test_provider_model_endpoint.py` or a new
   `tests/test_provider_cli_model_endpoints_hosting.py`) for `_model_endpoints_hosting`:
   `FACTORY_MODEL_ENDPOINT` unset → `hosting=cloud`; a verified cloud entry → `hosting=cloud`;
   missing/mismatched `FACTORY_MODEL_REGISTRY_SHA256`, unknown id, or an unreadable registry →
   `hosting=cloud`; an unpatched local entry with valid evidence → `hosting=cloud` (the hard
   block holds); `load_verified_entry` patched to return `([], {"hosting_location": "local"})`
   → `hosting=local` (proving the resolver logic itself is correct, even though it is
   unreachable in production — Requirement 1).
5. `tests/test_entrypoint_session_window.sh`: end-to-end with the baked CLI call stubbed to
   print `hosting=local` and a `TMP_OUT` containing session-exhaustion-shaped text — assert no
   `session-window-paused` sentinel, a non-zero exit, and an error-signature file containing
   `substantive:endpoint_unreachable`. A second scenario stubs `hosting=cloud` on the same text
   and asserts today's pause behavior is unchanged (regression/parity). The
   `FACTORY_PROVIDERS_CLI` stub must answer both `preflight` (exit 0) and
   `model-endpoints hosting`: `entrypoint.sh:10` runs preflight through the same path. One
   subshell per scenario.
6. New entrypoint-level test (extend `tests/test_entrypoint_session_window.sh` or add a sibling)
   covering **both** call sites per Requirement 4: (a) the main-loop path — the mocked `archon
   workflow run` exits non-zero with local-rate-limit-shaped output in `TMP_OUT`, assert the
   script reaches `exit` promptly (bounded run time — it must never reach the
   `sleep "$SLEEP_SECS"` branch) rather than sleeping; (b) the `on_failure` ERR-trap path — a
   crash before the main loop runs, with `TMP_OUT` seeded the same way, asserts the normal
   failure comment/board-update path runs, not the pause branch. The main loop sits after the
   `ENTRYPOINT_SOURCE_ONLY` return (`entrypoint.sh:813`), so (a) runs `entrypoint.sh` end to end
   with stubbed `archon`/`git`/`gh` and the providers-CLI stub above, not by sourcing it.
7. Argv-parity test (`tests/test_entrypoint_session_window.sh` or
   `tests/test_entrypoint_error_signature.sh`): with `FACTORY_MODEL_ENDPOINT` unset, and again
   with it set but resolving to cloud, capture the exact argv passed to `session-window-check`,
   `rate-limit-match` and `error-signature-write` and compare against a literal golden recorded
   from current `main` — must be byte-identical (no `--hosting-location` token present at all).
8. `tests/test_factory_core_breaker.py`: `reset_retry` still pops exactly `<key>:sig`,
   `<key>:delivery`, `<key>:loop:*`, `<key>:cverdict`, `<key>:cid`, `<key>:crawlog` — no new key
   — a regression guard against the rejected spec's `.local.sig`-suffix design reappearing.
9. `tests/test_session_window_gate.sh` and `tests/test_provider_env_flags.sh`/
   `test_provider_env_passthrough.py`: unchanged, run as-is to confirm Requirement 9 (no
   `scheduler.sh`/`provider_env.sh` behavior drift). The #341 rollback is pinned by
   `tests/test_scheduler.sh` section B4 (`rollback_paused_retry`, lines 232-269), also unchanged
   and run as-is.

## Alternatives Considered

- **Split the breaker signature by endpoint (`.local.sig`), as the rejected first spec did.**
  Rejected by the operator and superseded by owner decision 2: a container-chosen filename
  must never influence a safety mechanism, and a second bucket nobody reads (`reset_retry`
  wouldn't know to pop it without a matching code change at every site that already pops
  `:sig`) would silently lose early-stuck detection for local runs. The single-class-value
  approach (Architecture §3c) gets the same outcome — two consecutive local failures still
  trip the breaker — through the existing, unchanged bucket.
- **Give `rate-limit-match` its own `--hosting-location` flag**, mirroring the other two CLI
  calls, instead of a bash-level guard. Rejected: `rate-limit-match` runs against
  `$CLONE_DIR`'s copy of `cli.py` (the target's own, potentially-older-than-the-baked-image
  copy, per the `# TARGET-PATH` convention), and the *existing* rollout ordering already makes
  this moot in practice — no target can resolve `hosting=local` before #587 (which itself
  depends on this ticket) ships for that specific target's clone — but a pure bash guard needs
  zero coordination with what that clone's `cli.py` happens to accept, costs one `if`, and
  keeps `rate-limit-match` provably byte-identical (code *and* argv) regardless. It is also the
  only construction that correctly disables the legacy loop when
  `SESSION_WINDOW_BACKOFF_ENABLED=false` (Architecture §3b).
- **Coin a new failure-class name** (e.g. `substantive:local_rate_limit`) instead of reusing
  `ENDPOINT_UNREACHABLE`. Rejected: §4.6 is explicit that its interim enumeration is superseded,
  not extended, by #238; adding a second ad-hoc name today only grows what #238 has to
  reconcile, and `model_adapter.py`'s existing catch-all semantics for `ENDPOINT_UNREACHABLE`
  already cover "this local destination isn't producing a usable response" in spirit.

## Open Questions (non-blocking)

- When #238 ships its own failure-class taxonomy, `classify()`'s new branch (and
  `model_adapter.py`'s own uses of `ENDPOINT_UNREACHABLE`) will need a follow-up rename pass —
  tracked by #238 itself, not this ticket.
- Whether a non-self target (e.g. MarketHawk) will vendor its own up-to-date
  `dark-factory/scripts/factory_core/cli.py` by the time it first opts into a local endpoint is
  a rollout concern for whichever ticket enables that target's #587, not this one — today,
  `_NO_LOCAL_DISPATCH` and this ticket's Requirement 7 (no flag on the default path) together
  guarantee no target ever receives `--hosting-location local` before its own clone can handle
  it.

## Assumptions

- `model_endpoint.load_verified_entry`'s `(registry_path, endpoint_id) -> (problems, entry)`
  shape (`scripts/factory_core/providers/model_endpoint.py:686-710`) is stable as of this
  writing (re-verified against current `main`, not trusted from either prior spec attempt).
- `model_adapter.FAILURE_CLASSES`/`ENDPOINT_UNREACHABLE`
  (`scripts/factory_core/providers/model_adapter.py:35-50`) is stable and intended as the
  shared §4.6 vocabulary pending #238 — re-verified directly against current `main`.
- No caller of `error_signature.write_signature`/`breaker.record_failure_signature` exists
  outside the `cli.py`/`entrypoint.sh` call sites identified above (re-verified via `grep`
  against current `main`); a new, concurrently-landing caller should default
  `hosting_location` to `"cloud"`, matching every signature in this spec.
