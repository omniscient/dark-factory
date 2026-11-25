# Worker/Orchestrator/Checker Endpoint Isolation

**Issue:** #559

**Status:** Second refinement pass. A first spec (commit 949791e, since removed from this
branch — the branch was reset to `main`, which now includes #558) was rejected by operator
review for reducing the ticket to three uncalled pure functions with a still-hardcoded role and
no exercised dispatch. This spec is a point-by-point rewrite against the issue body's 11-item
"Refinement constraints" section, confirmed live on `main` at `a859b6f`. It carries the
operator-review amendments to that rewrite (A1–A8); gate wiring is #611.

## Overview / Problem Statement

`docs/hybrid-execution-contract.md` (#556, accepted, confirmed by the owner in #580) already
settles the design for worker/orchestrator/checker isolation in §4.2 and §4.4: endpoint-role
binding happens at dispatch, a local-routed container may never also host an independent
checker for the same unit of work, nested Agent-tool subagents inherit their parent's endpoint
and must be refused when they claim a checker/verifier/monitor role under a local-routed
parent, and a gate verdict is accepted only if the producing dispatch's record shows
`hosting_location: cloud` and a different, non-null `endpoint_id` — proven by the dispatch
record, never an agent's own claim. #559 is that contract's Responsibility-map row (§4.1):
"enforcing that a checker dispatch is a separate container with a different endpoint... must
refuse (not silently downgrade) a nested-subagent checker claim."

#557 (endpoint registry) and #558 (runtime adapter) have since merged. #558 left exactly one
piece of §4.2/§4.4 unbuilt, by design — its own requirement 13 says role is `"actor"` whenever
the resolved entry is local "since §4.2 forbids a local-routed container from ever hosting an
independent checker... so this is a contract constant, not a guess," and `null` on the cloud
path "since... the proxy has no unforgeable signal to distinguish [actor from checker]
(inventing one would violate #288's self-report rule)." That is exactly the gap #559 closes: a
**harness-written, pointer-grade** (`inferred_pointer`) role signal — the run container can
write the file, so role never grants authority by itself — that #558 correctly declined to
invent on its own, because inventing one was never #558's job.

Today, `scripts/factory_core/providers/model_adapter.py:21` hardcodes `LOCAL_ROLE = "actor"`
as a module constant with no caller-supplied input, and `scripts/factory_core/model_proxy.py`
writes it into the ledger's `role` column unconditionally on the local path and `None`
unconditionally on the cloud path. No declared role exists yet to check *against* — a local
dispatch cannot yet be wrong about its role, because nothing can yet claim a different one.
`_NO_LOCAL_DISPATCH` (`model_endpoint.py`) still blocks every real local dispatch pending #587,
exactly as before; this spec changes nothing about that block.

This is a **library-and-wiring** change, not a gate change: it adds a declared-role carrier
from the run container's entrypoint through to the proxy, a small pure module
(`role_isolation.py`) that the proxy calls now and #611's gate wiring will call later, and a
hermetic test that exercises two real, concurrently-running proxy instances. It adds no new
gate, touches no command file, `VERIFIER-CONTRACT.md`, the workflow DAG, `scheduler.sh`,
`deploy/**`, or `run-compose.yml`, and it does not lift `_NO_LOCAL_DISPATCH`.

## Requirements (from Q&A and the issue's 11 refinement constraints)

1. **Declared role is a dispatch-level, harness-set signal** (constraint 1) — never the
   model's, never an inbound header. Carrier: the run container's `FACTORY_DISPATCH_ROLE`
   environment variable, which `entrypoint.sh` folds into the existing `current-run.json`
   pointer file as a `"role"` field only when the variable is non-empty; the model-proxy
   process (a separate long-lived compose service that never receives the run container's `-e`
   flags directly) reads it from there via `read_current_run()`, exactly as it already reads
   `run_id`/`issue_number`/`intent`/`stage`. No dispatch declares a non-default role in this
   ticket, so nothing sets the variable yet and `scheduler.sh` is not touched; the first ticket
   that needs a non-default role forwards it by name. With `FACTORY_MODEL_ENDPOINT` unset,
   behavior is byte-identical to today: the pointer file is unchanged and no declared role
   reaches a ledger row (`role` stays `None`). With an endpoint selected and the `role` field
   absent or unreadable, a local dispatch resolves to `actor` and a cloud dispatch records
   `role: None` — never `checker`; a present but invalid role is refused (Architecture C).
   Trust level: the pointer file sits on a volume the run container can write, so the role is
   pointer-grade (`inferred_pointer`), not proof of identity. It can only narrow what a
   dispatch may do (a local dispatch is `actor` or refused); it never grants authority by
   itself.
2. **No edit to `gate_*`, verdict-gate scripts, `VERIFIER-CONTRACT.md`, the workflow DAG,
   `deploy/**`, or `run-compose.yml`.** `_NO_LOCAL_DISPATCH` stays. Nothing from
   #585/#586/#587/#560/#561/#562/#563/#564.
3. **Acceptance criterion 1's hermetic test is real, not simulated**: two concurrently-running
   `model_proxy.create_app()` instances in one test process — a local-worker instance against a
   fake local upstream, and a cloud-checker instance *selected via a cloud registry entry* (so
   its ledger rows carry a non-null `endpoint_id`, per constraint 4) against a fake cloud
   upstream — each with its own `current-run.json` fixture declaring its role. The test reads
   each instance's ledger rows back through `run_record._read_ledger_rows` and
   `run_record._compute_model_dispatch` (the real production aggregation, not a hand-built
   fixture) to build the two `model_dispatch["result"]` records, then asserts
   `check_verdict_independence` on them.
4. **The independence proof is void** when either side's `correlation` is `"unknown"`; it
   requires a non-null **cloud** `endpoint_id` for the producer (a checker dispatch must
   therefore select a cloud registry entry, since a default cloud run records
   `endpoint_id: None`); it is scoped to a local-routed actor only (a cloud-routed actor's
   check returns `not_applicable`, not independence, per Invariant 3); it binds to role, not
   just hosting/endpoint (the producer's `role` must be `"checker"`); and it binds to the unit
   of work: the actor run, the producer run and the verdict must carry the same issue number,
   otherwise `unit_of_work_mismatch`, so an unrelated successful cloud run is never the proof.
   A void/`not_applicable` result is never treated as independent. A caller (#611) maps every
   void result to `STATUS: BLOCKED` plus `ready-for-human` — never `PASS`, `SKIPPED` or
   `ERROR` (in `verdict.py`, `ERROR` proceeds); `not_applicable` leaves today's all-cloud
   gate unchanged.
5. **Model identity** (constraint 5, contract §4.11 decision 2). #558's
   `check_model_identity` (`model_adapter.py:162`) already refuses a request naming anything
   other than `dest["served_name"]`, including a literal cloud model ID — this spec adds no new
   request-side check, only characterization tests pinning that behavior through the proxy
   (`tests/test_factory_model_proxy.py`), since #559 owns the isolation behavior, not a second
   implementation of #558's check. It adds one genuinely new **response**-side check,
   `check_response_model`, since nothing today compares what the local endpoint actually served
   against what was requested.
6. **Role isolation never depends on `FACTORY_MODEL_ENDPOINT` alone** (constraint 6).
   `role_isolation.py`'s functions take `hosting_location`/role values as explicit arguments and
   read no environment variable themselves; selecting an endpoint instance-wide grants no role
   by itself — the declared-role carrier (requirement 1) is the only role signal, and its
   absence resolves to `actor` on a local dispatch and to no role (`None`) on a cloud dispatch,
   never to `checker`.
7. **Nested checkers are disabled by construction** (constraint 7). The harness enforcement
   point is the proxy: it records every local-routed request as `actor` and refuses any other
   declared role, so no nested Agent-tool subagent of a local-routed parent can produce a
   checker row, and the independence proof (requirement 4) never accepts a local or
   same-endpoint producer. No model-reported role is an input to any function in this spec.
8. **Helper authority (constraint 8, Invariant 16)** — deferred by name to #587, with an
   acceptance-criterion line added to this spec's Open Questions for #587 to carry, plus a
   present-tense hermetic guard: `role_isolation.py` imports nothing beyond the stdlib (no
   `os`, `subprocess`, credential-shaped names), so it cannot itself read or leak a credential,
   mirroring `test_provider_model_adapter.py`'s existing
   `test_adapter_module_does_not_import_aiohttp`.
9. **"Malformed worker output" is the local worker's response** (constraint 9), not a
   redefinition: a non-JSON 2xx body, a truncated event stream, or a response naming the wrong
   `model` is recorded as `protocol_incompatibility`, which makes that run's
   `model_dispatch.result.outcome` `"failed"`, which the independence proof voids as
   `dispatch_outcome_not_ok` — a named proxy-level test (Test Plan). Malformed dispatch-record
   fixtures are additional hardening, not the criterion: a missing `endpoint_id` key, an
   absent or non-`"1.x"` `schema_version`, wrong-typed `correlation`/`outcome` values,
   non-dict input, and an out-of-enum `hosting_location` (distinguished from structural
   malformation by its own reason code).
10. **Facts corrected** (constraint 10): `verdict.py` has no `UNCERTAIN` status (its values are
    `PASS`/`SKIPPED`/`ERROR`/`BLOCKED` plus legacy `HUMAN_REQUIRED`/`FAIL`); this spec never
    writes a `verdict.py`-shaped status itself, since it wires no gate. #558's design doc
    (`docs/archive/2026-11-25-hybrid-runtime-adapter-design.md`) is cited by Requirement number
    below. `check_verdict_independence` returns a `(bool, str)` outcome/reason tuple and
    `bind_dispatch_role` a `(role, reason)` tuple, not `model_endpoint.py`'s
    `(problems, entry)` loader convention — the module has nothing to load.
11. **Hotspots** (constraint 11). Files touched, complete list:
    - `entrypoint.sh` (hotspot) — one change to the `current-run.json` printf: the only place
      the harness can carry the role to the proxy.
    - `scripts/factory_core/model_proxy.py` (hotspot) — the hardcoded-role read replaced by
      the role binding, one new response check call, and a per-app pointer path so two
      instances can run in one test process.
    - `scripts/factory_core/providers/model_adapter.py` — the new `check_response_model`
      function and a `build_correlation` signature change.
    - `scripts/factory_core/providers/role_isolation.py` (new).
    - Tests: `tests/test_provider_role_isolation.py` (new), `tests/test_factory_model_proxy.py`,
      `tests/test_provider_model_adapter.py`, `tests/test_entrypoint_current_run.sh`.
    - `docs/configuration.md` — one row documenting `FACTORY_DISPATCH_ROLE`.

    **`scheduler.sh`, `providers/__init__.py` and `run_record.py` need no change at all** —
    `_compute_model_dispatch` already reads `role` generically off ledger rows (`run_record.py`
    line ~905), so it picks up real role values the moment the ledger carries them, with zero
    new code.

## Architecture / Approach

### A. Declared-role carrier (requirement 1)

- `scheduler.sh` is not touched: no dispatch has a non-default role yet, so nothing sets
  `FACTORY_DISPATCH_ROLE` in this ticket. The first ticket that needs a non-default role
  forwards it by name only (`-e FACTORY_DISPATCH_ROLE`), the same way `dispatch()` forwards
  `FACTORY_MODEL_REGISTRY_SHA256` — not through `PROVIDER_PASSTHROUGH_VARS`, which is
  `provider_env.sh`'s instance-config passthrough list, not a per-dispatch value.
- `entrypoint.sh`'s existing `current-run.json` printf (around line 162) appends
  `,"role":"<value>"` only when `FACTORY_DISPATCH_ROLE` is non-empty, so today's pointer file
  is byte-identical:
  ```
  ROLE_FIELD=""
  if [ -n "${FACTORY_DISPATCH_ROLE:-}" ]; then
    case "$FACTORY_DISPATCH_ROLE" in
      *[!a-z_]*) ROLE_FIELD=',"role":"invalid"' ;;
      *) ROLE_FIELD=",\"role\":\"${FACTORY_DISPATCH_ROLE}\"" ;;
    esac
  fi
  printf '{"run_id":"%s","issue_number":%s,"intent":"%s","stage":"%s","started_at":"%s"%s}\n' \
    "$RUN_ID" "${ISSUE_NUM:-0}" "${INTENT:-unknown}" "$RUN_STAGE" "$RUN_STARTED_AT" "$ROLE_FIELD" \
    > "$CURRENT_RUN_DIR/current-run.json" 2>/dev/null || true
  ```
  A value that is not a bare lowercase token (`a`–`z` and `_`) is recorded as `invalid` and
  refused by the proxy (`role_invalid`) when an endpoint is selected, so it can never add or
  override another pointer field.
- `model_proxy.read_current_run()` (`model_proxy.py:79`) adds `"declared_role": data.get("role")`
  to its returned dict, next to its existing `run_id`/`issue_number`/`intent`/`stage` fields —
  `None` when the field is absent. A missing file or unreadable JSON returns
  `"declared_role": None` through the existing `except Exception` fallback — no new failure
  mode. The pointer path becomes a parameter (`read_current_run(path=None)`, defaulting to the
  module-level `CURRENT_RUN_PATH`), so each app can read its own pointer (section C).

### B. `scripts/factory_core/providers/role_isolation.py` (new module)

Stdlib-only (no `os`, `aiohttp`, or import of `model_endpoint`/`run_record`), mirroring
`model_adapter.py`'s own plain dict-in/dict-out style so every function unit-tests without a
server. `bind_dispatch_role` returns `(role, reason)` and `check_verdict_independence` returns
`(bool, str)` — an outcome and a reason code. Neither raises, and neither reads environment or
disk itself (requirement 6).

```python
VALID_HOSTING = ("local", "cloud")
VALID_ROLES = ("actor", "checker", "monitor")

def bind_dispatch_role(hosting_location, declared_role=None) -> tuple[str | None, str]:
    """(role, reason). A refusal is role None with reason hosting_location_invalid,
    role_invalid or nested_checker_refused; an undeclared cloud role is None, "undeclared"."""
    if hosting_location not in VALID_HOSTING:
        return None, "hosting_location_invalid"
    if declared_role is None:
        if hosting_location == "local":
            return "actor", "default_actor"
        return None, "undeclared"  # today's cloud rows: role stays None
    if declared_role not in VALID_ROLES:
        return None, "role_invalid"
    if hosting_location == "local" and declared_role != "actor":
        return None, "nested_checker_refused"  # §4.2: local never hosts an independent checker
    return declared_role, "declared"

def check_verdict_independence(actor_dispatch, producer_dispatch, *,
                               actor_issue, producer_issue, verdict_issue) -> tuple[bool, str]:
    """The §4.2 independence proof over two model_dispatch['result']-shaped dicts.

    The three issue numbers bind the proof to one unit of work: each run record's own
    top-level issue_number, and the issue the verdict is for.
    """
    for d in (actor_dispatch, producer_dispatch):
        ok, reason = _well_formed(d)
        if not ok:
            return False, reason
    if actor_dispatch["hosting_location"] != "local":
        return False, "not_applicable"  # Invariant 3: only a local-routed actor needs this proof
    issues = (actor_issue, producer_issue, verdict_issue)
    if any(type(i) is not int or i <= 0 for i in issues) or len(set(issues)) != 1:
        return False, "unit_of_work_mismatch"
    if producer_dispatch["hosting_location"] != "cloud":
        return False, "producer_not_cloud"
    if actor_dispatch["outcome"] != "ok" or producer_dispatch["outcome"] != "ok":
        return False, "dispatch_outcome_not_ok"
    if actor_dispatch["correlation"] != "inferred_pointer" or producer_dispatch["correlation"] != "inferred_pointer":
        return False, "correlation_unknown"
    if producer_dispatch["endpoint_id"] is None:
        return False, "producer_endpoint_missing"
    if producer_dispatch["endpoint_id"] == actor_dispatch["endpoint_id"]:
        return False, "producer_endpoint_same_as_actor"
    if producer_dispatch["role"] != "checker":
        return False, "producer_role_not_checker"
    return True, "independent"
```

A caller maps every `False` other than `not_applicable` to `STATUS: BLOCKED` plus
`ready-for-human`; never `PASS`, `SKIPPED` or `ERROR`. `not_applicable` means the proof does
not apply (a cloud-routed actor) and leaves the caller's existing behavior unchanged. The issue
arguments are each run record's own top-level `issue_number` (`run_record.py`'s `cmd_assemble`)
and the issue the verdict is for; `None`, a non-positive value (the pointer's `0` default) or
any inequality is `unit_of_work_mismatch`.

`_well_formed(d)` is the one private helper: rejects a non-dict, a dict missing any of
`{schema_version, outcome, endpoint_id, hosting_location, role, correlation}`, a
`schema_version` that is not a `"1.x"` string (accepting any minor — Invariant 12), and a
`hosting_location` outside `VALID_HOSTING` (reason `hosting_location_invalid`, kept distinct
from the generic `malformed_dispatch_record` so a caller can tell *what* broke). This exactly
matches `run_record.py`'s `MODEL_DISPATCH_RESULT_VERSION = "1.0"` and the six `result` keys
`_compute_model_dispatch` already emits (`run_record.py` lines 890–911) — duck-typed, no import.

### C. Wiring into `model_proxy.py` / `model_adapter.py` (requirement 11, the part the
rejected spec skipped)

- `create_app()` (`model_proxy.py`, around line 522) gains an optional pointer path —
  `create_app(destination=None, current_run_path=None)` — stored on the app and passed to
  `read_current_run()`; `None` keeps the module-level `CURRENT_RUN_PATH`, so `main()` and every
  existing caller are unchanged. Two apps in one process can then each read their own pointer.
- `handle_proxy()` (`model_proxy.py`, around line 335) binds the role only when an endpoint is
  selected. Immediately after computing `local = dest is not None and
  dest["hosting_location"] == "local"`, and before `ma.check_request`:
  ```python
  role = role_reason = None
  if dest is not None:
      # Lazy, like the adapter import: the default cloud path never loads this module.
      from factory_core.providers import role_isolation
      role, role_reason = role_isolation.bind_dispatch_role(
          dest["hosting_location"], correlation["declared_role"])
  ```
  With `FACTORY_MODEL_ENDPOINT` unset (`dest is None`) the declared role is never consulted and
  `role` is `None`, exactly as today — even if the pointer file carries a `role` field.
  When `dest` is not `None` and `role_reason` is `role_invalid`, `hosting_location_invalid` or
  `nested_checker_refused`, this is a refusal **before any outbound call** (the same posture as
  #558's Requirement 9/10 refusals), on a local and a cloud entry alike: record the ledger row
  with `failure_class="destination_policy_denial"` and `role: None`, and return a 403, never
  forwarding the request.
  Otherwise the row records the bound role: `actor` on a local entry (declared or defaulted);
  on a selected cloud entry, the declared role (e.g. `"checker"`, for a future cloud-routed
  checker dispatch) or `None` when none is declared. An undeclared cloud dispatch therefore
  keeps `role: None`, exactly as now. This is consistent with #558's Requirement 13 reasoning
  (the proxy has no signal it can trust to tell an actor from a checker): the recorded role is
  harness-written and pointer-grade (requirement 1), it is never treated as proof by itself,
  and the independence proof still rests on the dispatch record's `hosting_location`,
  `endpoint_id`, `correlation` and unit-of-work binding.
  `columns["role"] = role` replaces `ma.LOCAL_ROLE if local else None`.
- `ma.build_correlation(dest, run)` (`model_adapter.py:185`) changes its signature to
  `build_correlation(dest, run, role)`, using the caller-resolved `role` in the envelope instead
  of the `LOCAL_ROLE` module constant. `LOCAL_ROLE` is removed; nothing else referenced it.
- New function `model_adapter.check_response_model(dest, data, streamed)`: reads the
  non-streaming body's top-level `"model"` field, or a streamed body's `message_start` event's
  `message.model`; raises `AdapterRefusal(502, PROTOCOL_INCOMPATIBILITY, ...)` when the field is
  present and differs from `dest["served_name"]`. An absent field passes (the request-side
  `check_model_identity` already pinned identity before the call left). Called from
  `handle_proxy()` right next to the existing `check_json_body`/`check_sse_complete` calls
  (`model_proxy.py` lines ~433/449). Because a streamed response's chunks are already forwarded
  to the client by the time this check can run, a streamed mismatch cannot be a pre-forward
  refusal — it is recorded as a ledger `failure_class`, which makes that run's
  `model_dispatch.result.outcome == "failed"`, which `check_verdict_independence` then voids via
  the existing `dispatch_outcome_not_ok` path. The spec states this explicitly rather than
  implying a refusal that cannot happen post-stream.

### D. What does *not* change

- `providers/__init__.py`: no change. Its `preflight()` is unrelated to per-request role
  binding and the proxy already avoids it (`model_proxy.py`'s own module docstring, "never
  `providers.preflight()`, which needs `GH_TOKEN`").
- `run_record.py`: no change. `_compute_model_dispatch` (lines 874–911) already does
  `"role": _single("role")` generically over ledger rows; once the ledger carries real role
  values, the Result contract reflects them with zero new code.
- `scheduler.sh`: no change. Nothing sets or forwards `FACTORY_DISPATCH_ROLE` in this ticket.
- `scripts/provider_env.sh`: no change. `FACTORY_DISPATCH_ROLE` is a per-dispatch value, the
  same category as `FACTORY_MODEL_REGISTRY_SHA256`, not an instance-config passthrough var.
- `_NO_LOCAL_DISPATCH`, every `gate_*` script, `VERIFIER-CONTRACT.md`, the workflow DAG,
  `deploy/**`, `run-compose.yml`: untouched.

## Test Plan

New `tests/test_provider_role_isolation.py`:
- `bind_dispatch_role`: an undeclared role is `actor` for `local` and `None` (`undeclared`) for
  `cloud`; refuses a declared `checker`/`monitor` for `local`; accepts a declared `checker` for
  `cloud`; refuses an invalid hosting location or declared role, each with its own reason code.
- `check_verdict_independence`, parametrized over the malformed-record fixtures (missing
  `endpoint_id` key; absent/`"2.0"` `schema_version`; wrong-typed `correlation`/`outcome`;
  `None`/`[]`/`"ok"` as the whole record) applied to *either* argument position, asserting
  `(False, "malformed_dispatch_record")` and that nothing raises; plus the semantic cases —
  cloud actor → `not_applicable`; non-cloud producer → `producer_not_cloud`; either
  `outcome != "ok"` → `dispatch_outcome_not_ok`; either `correlation == "unknown"` →
  `correlation_unknown`; producer `endpoint_id is None` → `producer_endpoint_missing`; equal
  `endpoint_id`s → `producer_endpoint_same_as_actor`; producer `role != "checker"` →
  `producer_role_not_checker`; the one fully-satisfying case → `(True, "independent")`.
- Unit-of-work binding: with otherwise fully-satisfying records, a `producer_issue` or
  `verdict_issue` that differs from `actor_issue`, and any of the three being `None`, `0` or a
  non-int → `(False, "unit_of_work_mismatch")`; a cloud actor still → `not_applicable`
  whatever the issue arguments.
- Authority-confinement guard: an AST/import check that `role_isolation.py` imports only
  stdlib modules (no `os`, `subprocess`, `aiohttp`, `model_endpoint`, `run_record`), mirroring
  `tests/test_provider_model_adapter.py`'s `test_adapter_module_does_not_import_aiohttp`.

Extended `tests/test_factory_model_proxy.py`:
- **Acceptance criterion 1, hermetic**: build a local destination fixture (`_local_destination`
  helper, as existing tests do) and a *second*, cloud registry-entry destination fixture (a
  valid cloud `hosting_location` entry with its own `endpoint_id`) using
  `model_adapter.destination_from_entry`. Start two `TestServer(create_app(...))` instances
  concurrently — one against a fake local upstream `TestServer`, one against a fake cloud
  upstream `TestServer` — each with its own pointer fixture file passed as
  `create_app(destination, current_run_path=...)`: both name the same issue number; the
  worker's declares no role (defaults to `actor`), the checker's declares `"role": "checker"`.
  Issue one request through each client. Read back each run's ledger rows via
  `run_record._read_ledger_rows` (distinct `run_id`s) and assert per request: the worker row
  has `role: "actor"`, the local `endpoint_id` and a `request_id`; the checker row has
  `role: "checker"` and the cloud entry's `endpoint_id`. Build each side's
  `model_dispatch["result"]` via `run_record._compute_model_dispatch`. Assert
  `check_verdict_independence(worker_result, checker_result, actor_issue=N, producer_issue=N,
  verdict_issue=N) == (True, "independent")`, that the same call with a `producer_issue` from
  a run on a different issue gives `(False, "unit_of_work_mismatch")`, and
  separately assert: the worker's forwarded headers never contain the cloud entry's
  `secret_reference` value or vice versa; neither ledger row's `payload_decision`/any other
  field contains the other side's secret value; a raw-artifact capture (if enabled in the
  fixture) is redacted on both sides. (Scope note, stated in the spec rather than overclaimed:
  both proxy instances run in one Python process/one OS environment in this hermetic test, so
  this proves header/ledger/artifact non-leakage and the existing startup-time
  cloud-credential-absence check — not literal separate-OS-process isolation, which is a
  structural property of "one dispatched container, one endpoint config" per Q1 and is not
  something any in-process unit test can simulate.)
- A declared role of `"checker"` presented to a **local** destination is refused 403 before any
  upstream call, with `failure_class: "destination_policy_denial"`, and the refused row's
  `role` is `None` (not silently coerced to `actor`).
- A declared role that is not one of `actor`/`checker`/`monitor` is refused the same way on a
  local destination **and** on a selected cloud entry (403, no upstream call) — never
  defaulted.
- Default-path parity: with no endpoint selected (`create_app()` with no destination) and a
  pointer file declaring `"role": "checker"`, the request is forwarded untouched and the
  ledger row keeps `role: None`; `role_isolation` is not imported on that path.
- Endpoint selected, no declared role: a local row records `actor`, a selected-cloud row
  records `role: None` — role isolation never follows from the instance-wide selection alone
  (requirement 6).
- **Malformed worker output** (acceptance criterion 5): a local upstream answering a 2xx
  non-JSON body, and one answering a truncated event stream, each produce a
  `protocol_incompatibility` ledger row; that run's `model_dispatch.result.outcome` is
  `"failed"`, and `check_verdict_independence` with it as the actor returns
  `(False, "dispatch_outcome_not_ok")` — never independent.
- Characterization (pins #558, adds no new production assertions): a literal cloud model ID or
  alias name sent to a local destination gets 403 `destination_policy_denial` and the fake
  upstream is never contacted; a 3xx upstream response gets `protocol_incompatibility` and is not
  followed (both already proven at the unit level by
  `tests/test_provider_model_adapter.py::test_model_identity_mismatch_is_refused_403_never_remapped`
  and `check_response_status`'s existing coverage — these are proxy-level regression pins, not
  new behavior).
- New: `check_response_model` — a non-streaming local response whose body's `model` field
  differs from `served_name` is refused 502 `protocol_incompatibility` before the client
  receives it; a streamed response with a mismatching `message_start.message.model` is recorded
  with `failure_class` set (not refused — already forwarded), and a follow-up assertion shows
  that run's `model_dispatch.result.outcome == "failed"`.

Extended `tests/test_entrypoint_current_run.sh`:
- With `FACTORY_DISPATCH_ROLE` unset or empty the pointer file is byte-identical to today's
  (no `role` key); with `FACTORY_DISPATCH_ROLE=checker` it carries `"role":"checker"` and
  still parses as JSON; with a value that is not a bare lowercase token (one that tries to
  inject a second `issue_number`) it carries `"role":"invalid"` and `issue_number` is unchanged.

Updated `tests/test_provider_model_adapter.py`: the existing `build_correlation` tests pass the
role explicitly (signature change); no behavior change.

## Alternatives Considered

1. **Carry the role as a new `X-Factory-*` request header set by the run container's own
   process instead of a file.** Rejected: the proxy is a separate compose service that never
   receives the run container's environment or an injected header unless the client (Claude
   Code / Archon) sets one — which would make it self-report, exactly what #288 and this
   contract forbid. `current-run.json` is already the sanctioned, harness-written,
   proxy-readable channel.
2. **Let `model_proxy.py` read `FACTORY_DISPATCH_ROLE` directly from its own `os.environ`.**
   Rejected for the same reason the rejected first spec's "no caller" flaw mattered: the proxy
   process's environment is fixed at container start by the `factory-model-proxy` compose
   service definition, not by each run container's dispatch — it would require a `run-compose.yml`
   edit (out of scope, named hard limit) to make `FACTORY_DISPATCH_ROLE` reach the proxy process
   per-dispatch at all. The pointer-file hop is the only channel that already crosses that
   process boundary per-run.
3. **Keep cloud-path `role` hardcoded to `None` even after adding the carrier**, treating only
   the local path as role-aware. Rejected: it would make `check_verdict_independence`'s
   `producer_role_not_checker` check permanently unsatisfiable, since no future cloud checker
   dispatch could ever record `role: "checker"` — silently defeating the independence proof
   this ticket exists to build, for a case (#587's eventual checker dispatch) this contract
   explicitly anticipates.
4. **Add the independence/role functions directly to `model_adapter.py`** instead of a new
   module. Rejected to keep `model_adapter.py` scoped to the Request/Result-contract checks
   #558 already owns (per its own docstring) and to keep `role_isolation.py`'s
   authority-confinement property (stdlib-only, no `os`/`aiohttp`) independently verifiable
   without pulling in `model_adapter.py`'s aiohttp-adjacent surface.
5. **Write a hermetic test with hand-built fixture dicts for the independence proof, skipping
   the two-proxy-instance exercise.** Rejected per constraint 3 and the first spec's rejection
   reason — a hand-built fixture proves the pure function works but not that the real dispatch
   path produces the records it expects; the two-`TestServer` test exercises the actual ledger
   write and `_compute_model_dispatch` aggregation.

## Open Questions (non-blocking)

- Gate wiring is #611, *feat(gate): enforce the dispatch-record independence proof for
  local-routed work*. Acceptance criteria: the verdict gate calls
  `role_isolation.check_verdict_independence`; a void result → `STATUS: BLOCKED` plus
  `ready-for-human`; the all-cloud path is unchanged; it gets its own human-reviewed spec.
  Nothing in this spec edits a gate.
- For #587 to carry (constraint 8, Invariant 16): "the helper container #587 dispatches must
  receive no `GH_TOKEN`, Docker-socket, or write-scoped tracker/code-host credential,
  asserted by a test over its compose/env flags." This spec does not edit #587 (refine-phase
  scope is this file plus `.archon/memory/`); the operator is expected to add the line to #587.
- Which ticket first forwards `FACTORY_DISPATCH_ROLE` from `scheduler.sh` (for a cloud-routed
  checker dispatch declaring `checker`), or whether that belongs to some future split-gate
  mechanism — left to whichever ticket (gate-adjacent, out of scope here) eventually splits a
  gated phase's checker into its own dispatch.

## Assumptions (flagged)

- `docs/hybrid-execution-contract.md`, `model_endpoint.py`, `model_adapter.py`, `model_proxy.py`,
  `run_record.py`, `verdict.py`, `provider_env.sh`, and `docs/archive/2026-11-25-hybrid-runtime-adapter-design.md`
  were read live on `main` at `a859b6f` (2026-11-25); re-verify line citations before
  implementing if `main` has advanced.
- The issue body's 11-item "Refinement constraints" section is treated as authoritative product
  input for this spec (it only restates and sharpens existing CLAUDE.md hard limits and the
  accepted contract — it does not ask this spec to touch any security-sensitive surface), per
  this repo's trusted-comment-channel policy.
- `bind_dispatch_role`'s cloud-path behavior (the role is recorded when declared and stays
  `None` otherwise) is this spec's own synthesis, reconciling the Q&A answer ("cloud-path
  ledger stays byte-identical") with the requirement that a future cloud checker dispatch must
  be able to record `role: "checker"` — not a separately confirmed product answer. It changes
  no observed behavior today, since nothing sets `FACTORY_DISPATCH_ROLE` in this ticket.
