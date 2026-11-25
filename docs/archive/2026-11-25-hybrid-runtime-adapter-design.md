# Hybrid Runtime Adapter: Minimum Cloud/Local Protocol Integration

**Issue:** #558

**Revised:** 2026-11-25 (re-refinement after operator rejection of the first spec at `99e8982`)

## Overview / Problem Statement

#557 (merged, PR #595) built the endpoint registry, the scheduler→run-container staged-digest
handoff, and `preflight()`. #593 (merged, PR #596) fixed two read/verify defects in that
registry code: the digest is now bound to the read-only source, and every content-affecting
read happens exactly once (`read_verified_tree()`), closing a swap-and-swap-back window. Neither
ticket dispatches a request anywhere — `preflight()` unconditionally rejects every
`hosting_location: local` selection today (`_NO_LOCAL_DISPATCH`, pending #587's first real
helper dispatch).

#558 is the next slice: the actual runtime-adapter code that `model_proxy.py`'s existing cloud
passthrough is missing — forwarding a request to a selected **local, Anthropic-Messages-
compatible** endpoint (the registry's `protocol` field accepts only `"anthropic-messages"`
today; no translation layer), enforcing credential scope, per-request model-identity refusal,
timeout/cancellation/failure classification, and the Request/Result ledger and run-record
contract (§4.7). Because `_NO_LOCAL_DISPATCH` stays in force (out of #558's scope — unchanged
boundaries, below), none of this code runs against a real local endpoint in production yet; it
is built and hermetically tested now (against a fake upstream, per the existing `TestServer`
pattern) so #587 has a proven adapter to enable later, and so default cloud behavior gains the
new ledger columns and `model_dispatch` run-record parity without any behavior change.

A first version of this spec (`99e8982`, now off-branch) was operator-rejected: it was written
against the pre-#593 registry API (`verify_staged_digest()`, which #593 deleted), proposed
calling `providers.preflight(side="run")` from the proxy container (which needs `GH_TOKEN`, a
credential the proxy container never has), left the forwarding destination and inbound-
credential stripping unspecified, dropped three acceptance criteria, and had no test plan. This
revision is written against current `main` (post-#593) and addresses all twelve operator
constraints appended to the issue body.

**Depends on:** #557 (merged, closed), #593 (merged, closed).

**Scope:** `scripts/factory_core/providers/model_endpoint.py` (one new loader function),
`scripts/factory_core/providers/model_adapter.py` (new module), `scripts/factory_core/
model_proxy.py`, `scripts/factory_core/run_record.py`, their tests, and one documentation
section in `docs/dark-factory-model-proxy-rollout.md` (supported and uncertified modes,
Requirement 10). No change to `scheduler.sh`, `provider_env.sh`, `entrypoint.sh`,
`run-compose.yml`, `providers/__init__.py`, the workflow YAML, `deploy/**`, `gate_*`, breaker,
or budgets.

## Requirements (from Q&A)

Full brainstorming dialogue (this pass's three questions, plus the prior pass's three — all
still load-bearing) is reproduced in the Phase 6 issue comment. Load-bearing resolutions,
mapped to the issue's twelve numbered constraints:

1. **Registry access (constraint 1).** Add `load_verified_entry(registry_path, endpoint_id) ->
   (problems, entry)` to `model_endpoint.py`, composed only from `read_verified_tree()`,
   `_parse_registry()`, `_pick()`, and `_local_problems()` — exactly one `read_verified_tree()`
   call, never `_select()` or `load_registry()` (both do an independent, unverified disk read).
   `entry` is non-`None` only when `problems == []`. It does not filter `_NO_LOCAL_DISPATCH` and
   does not touch `_local_problems()`.

2. **Proxy startup (constraint 2).** `model_proxy.main()` calls the loader once, synchronously,
   before `web.run_app()`, only when `FACTORY_MODEL_ENDPOINT` is set; any problem (from the
   loader or the credential hard-fail check below) logs and exits non-zero before the server
   ever binds. It never calls `providers.preflight(side="run")` (`providers/__init__.py:44`),
   which also runs the tracker/codehost `required_env()` check (`GH_TOKEN` etc.) the proxy
   container never has — the proxy calls only the registry-scoped primitives above.
   The module's documented interface list — every variable the proxy process itself must hold
   for a local selection to resolve — is: `FACTORY_MODEL_ENDPOINT`,
   `FACTORY_MODEL_REGISTRY_SHA256`, `MODEL_PROXY_UPSTREAM` (optional; Requirement 3),
   `ANTHROPIC_BASE_URL` and the four `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU,FABLE}_MODEL` aliases
   (`_local_problems()` compares each against the entry in the process that runs it), and the
   variable named by the entry's `secret_reference`. The proxy service receives none of these
   today; delivering them is #587's (Assumptions, below).

3. **Forwarding destination (constraint 3).** For a `hosting_location: local` entry, the
   upstream is the verified entry's `base_url` — never the module's `MODEL_PROXY_UPSTREAM`
   default. If `MODEL_PROXY_UPSTREAM` is set in the environment *and* differs from the entry's
   `base_url`, startup fails (a human set it for a different purpose; proceeding would silently
   ignore the operator's registry choice or, worse, send a local-destined payload to whatever
   `MODEL_PROXY_UPSTREAM` points at). Local-destined requests use `allow_redirects=False`; any
   3xx response is classified `protocol_incompatibility` (§4.6). A local-configured adapter has
   exactly one resolved upstream for the lifetime of the process — no code path reaches the
   cloud default once `FACTORY_MODEL_ENDPOINT` selects a local entry.

4. **Credentials (constraint 4, acceptance criterion).** Two layers, both required:
   - **Startup hard-fail** (mirrors `providers/model.py`'s existing fail-closed `preflight()`
     precedent): if the resolved entry's `hosting_location == "local"` and any name in
     `model_endpoint.CLOUD_CREDENTIALS` (`ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`,
     `ANTHROPIC_AUTH_TOKEN`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
     `GOOGLE_APPLICATION_CREDENTIALS`) is set in the proxy's own environment, startup fails.
     The container should never have been issued one, but this is enforced rather than assumed.
   - **Per-request stripping/injection**: drop inbound `authorization`, `x-api-key`, and
     `api-key` headers unconditionally on a local-destined request (a client-supplied value here
     is `self_report` and untrusted per #288), then set the outbound `x-api-key` header (the
     Anthropic-Messages convention; `protocol` is pinned to `"anthropic-messages"`, so this is
     the only auth header the local endpoint expects) from `os.environ[entry["secret_reference"]]`
     — never any other env var, never a value echoed back from the registry itself.
   - Header- and payload-redaction tests cover both layers (Test Plan, below).

5. **No dropped acceptance criteria (constraint 5).** Covered by Requirements 6–11 below, each
   with its own test: unsupported forced-tool/deferred-tool/structured-output modes and
   unsupported endpoints surfaced explicitly (Requirement 10); documented supported-mode list
   (Architecture, "Supported/unsupported modes", published in
   `docs/dark-factory-model-proxy-rollout.md`); adapter-identity telemetry and bounded raw
   artifacts (Requirement 7; the bound already exists — `RAW_ARTIFACT_RETENTION_DAYS` /
   `sweep_raw_artifacts()` — #558 only adds fields); reachability fail-closed with no
   cross-endpoint retry (Requirement 11, §4.6); the dispatch-time predicate decision persisted,
   never a secret (Requirement 15). The digest criterion — "Before using the staged model
   registry, recompute its SHA-256 and fail closed unless it matches
   `FACTORY_MODEL_REGISTRY_SHA256`" — is discharged by Requirements 1 and 2 and by the Test
   Plan's "Startup failures: digest mismatch" test: the proxy's only use of the staged registry
   is the single `load_verified_entry()` call at startup, whose one `read_verified_tree()` call
   recomputes the SHA-256 over the very bytes it then parses and returns problems on any
   mismatch. The entry is verified once at startup and held in memory for the process lifetime;
   the staged registry is never opened again, so there is no later use for a later check to
   guard, and no request-time re-read exists.

6. **Module placement (constraint 7's "undefined dispatch context" note, and general
   maintainability).** `load_verified_entry()` lives in `model_endpoint.py`, next to the
   private helpers it composes, leaving `_NO_LOCAL_DISPATCH`/`_local_problems()` untouched. All
   per-request, Request/Result-contract logic — credential scoping, the model-identity refusal
   check, timeout resolution from `entry["timeouts"]`, failure-class classification, the
   correlation header, and the `payload_decision()` call site — lives in a new module,
   `scripts/factory_core/providers/model_adapter.py`: plain functions over dicts and headers,
   no `aiohttp` import, unit-testable independently of the `TestServer` harness.
   `model_proxy.py` remains the HTTP/streaming/ledger layer and calls into `model_adapter`;
   it imports `model_adapter` (and transitively `model_endpoint`) lazily, inside `main()`,
   only when `FACTORY_MODEL_ENDPOINT` is set, so the default cloud path never loads registry
   code (byte-identical behavior when unset — the existing acceptance criterion).

7. **Request/Result contract — two layers (constraint 7, §4.7).** Ledger (per request,
   `request-ledger.jsonl` via `build_ledger_row()`): new columns `endpoint_id`,
   `hosting_location`, `role`, `request_id`, `failure_class`, `correlation` (Requirement 13),
   `adapter_id` and `payload_decision`, all added as defaulted keyword arguments so existing
   call sites stay valid. `adapter_id` is the constant
   `"model_proxy/anthropic-messages-passthrough"` on every row (adapter identity); the backend
   model is the existing `model` column, which on the local path always equals the entry's
   `served_name` (Requirement 9). `hosting_location` is `"cloud"` on the existing cloud path
   and `"local"` on a local destination, in the ledger and the run record alike.
   `payload_decision` is the dict from Requirement 15, or `None` when no endpoint is selected.
   `endpoint_id`, `role`, `request_id` and `failure_class` are `None` on the existing cloud
   path.
   Run record (per run, `run_record.py`'s `cmd_assemble`): new additive top-level
   `run_record["model_dispatch"]` key, added the same guarded way `harness_economics` and
   `memory_trace` are (compute independently, assign, never mutate an existing key), via a new
   `_compute_model_dispatch()` helper that works from this run's request-ledger rows — the same
   `LEDGER_PATH` rows (`MODEL_PROXY_LEDGER_PATH`, `run_record.py:716`) that
   `_compute_harness_economics` already reads through `_read_ledger_rows(run_id, ledger_path)`;
   there is no separate stage ledger — and is emitted for cloud runs too (parity — the keys are
   present with cloud-appropriate values, e.g. `hosting_location: "cloud"`, `role: null`, an
   empty `failure_classes_seen` when nothing failed). Shape:
   ```
   model_dispatch: {
     "request": {"schema_version": "1.0", "payload_class": ..., "endpoint_id": ...,
                  "secret_reference": ..., "allow": ..., "reason": ...},
     "result": {"schema_version": "1.0", "outcome": "ok"|"failed"|"unknown",
                "endpoint_id": ..., "hosting_location": ..., "role": ...,
                "correlation": "inferred_pointer"|"unknown",
                "request_count": <int>, "failure_classes_seen": [...]}
   }
   ```
   `request` and `result` each carry their own `schema_version` (§4.7 versions the Request and
   Result contracts independently); there is no shared top-level version. `request` is
   `payload_decision()`'s return dict with its five keys unchanged (`payload_class`,
   `endpoint_id`, `secret_reference` — the name only — `allow`, `reason`), plus
   `schema_version`; it is never recomputed in `run_record.py` (Requirement 15). Transport: the
   proxy computes the decision once at startup and writes it as the `payload_decision` column on
   every ledger row; `_compute_model_dispatch()` copies it from the run's first row. With no
   endpoint selected, or no rows for the run, the five `request` fields are `null` except
   `reason: "no endpoint selected"`. `result` is aggregated from the run's ledger rows:
   `endpoint_id` and `hosting_location` are the rows' single value (more than one distinct
   `endpoint_id` in a run is recorded as `endpoint_id: null`, `outcome: "failed"` — never
   resolved to one value); `outcome` is `"ok"` when `failure_classes_seen` is empty, `"failed"`
   otherwise, and `"unknown"` (with `correlation: "unknown"`, `request_count: 0`) when the run
   has no rows.

8. **`cost_usd: null`, never `0` (constraint 10, §4.10).** Two existing coercions in
   `run_record.py` must stop turning an intentionally-absent local cost into a fake zero:
   `_nodes_from_run_obj`'s `n.get("costUsd") or n.get("cost_usd") or 0` and `_sum_usage`'s bare
   `sum(n.get("cost_usd", 0) for n in nodes)`. Fix: distinguish "key absent" (a node that never
   reported cost at all — unaffected, stays `0`-summed as today) from "key present with value
   `None`" (a local-routed node — must stay `null` and be excluded from any `sum()`, the same way
   `_classify_node_capture` already excludes a `"missing"` node from the `known_*` sum before
   `_sum_usage` runs). Concretely: `_nodes_from_run_obj` checks `"cost_usd" in n or "costUsd" in n`
   before applying the `or 0` fallback, and only applies it when the raw value is itself falsy-but-
   present (e.g. `0`/`0.0`), not when the key holds `None`; `_classify_node_capture` gains a branch
   that treats an explicit local `cost_usd: None` the same as today's `"missing"` classification
   (excluded from `known_cost_usd`; `totals.cost_usd` is `null` whenever any node is so
   classified — matching #593's sibling ticket's existing partial-run precedent in
   `docs/archive/2026-11-22-run-record-node-cost-capture-design.md`). This closes the exact gap
   #558's own `cost_usd: null` requirement would otherwise be defeated by. No regression to a
   fully-cloud, fully-complete run: every node still has a concrete float, so neither branch
   changes its output.

   **Local-run override (the criterion's "even when the harness reports a Claude-priced
   figure").** Preserving a `None` is not enough, because the harness has no notion of a local
   endpoint and may report a Claude-priced number. `cmd_assemble` reads the run's request-ledger
   rows before `_capture_totals` runs; when any row shows `hosting_location: "local"`, it
   overwrites every node's `cost_usd` with `None`, whatever the harness reported.
   `totals.cost_usd`, `totals.known_cost_usd` and `harness_economics.cost_per_task` are then
   `null`, never `0` (an all-`None` node list must not fall through to `sum([]) == 0`). The
   per-stage `runs.jsonl` rows already carry `cost_usd: None` and stay that way. Token counts
   are still recorded.

9. **Per-request model-identity refusal (constraint 5 / prior Q&A, §4.11 decision 2).**
   `handle_proxy()` already parses the request body's `model` field for the ledger's `model`
   column (`model_proxy.py:266`). When the resolved entry is local, `model_adapter` compares
   that field against `entry["model_identity"]["served_name"]`; any mismatch — including a
   literal cloud model ID — is refused (403, `failure_class: "destination_policy_denial"`, one
   of §4.6's interim classes; see Requirement 14's error shape), never remapped. This check exists only
   on the local path; the literal cloud model IDs pinned in `config/config.yaml` are invisible
   to #557's alias-based `preflight()`, so this per-request check in #558 is the only place that
   catches a local-routed request naming one.

10. **Supported/unsupported modes (constraint 5).** No registry entry field describes
    forced-tool, deferred-tool, or structured-output support today. #558 documents (in
    Architecture, below) the exact, fixed list of modes the v1 adapter supports for a local
    destination — plain single-turn and multi-turn tool-result messages, streaming and
    non-streaming — and refuses every unsupported one explicitly (400, `failure_class:
    "protocol_incompatibility"`) rather than forwarding it and hoping. This is a hardcoded
    adapter capability statement, not a registry field — a future ticket may promote it to the
    registry once more than one certified local runtime exists.

11. **Timeouts (constraint 9, §4.6).** Local destinations use `entry["timeouts"]`:
    `connect_seconds` → `aiohttp.ClientTimeout(connect=...)`, `idle_seconds` → a rolling `asyncio.timeout` re-armed after each chunk (`sock_read` is not
    used: aiohttp also applies it before the first byte, which would cap time-to-first-token
    at idle),
    `total_seconds` → `total`. `time_to_first_token_seconds` has no native `aiohttp.ClientTimeout`
    field — it is enforced with the same rolling deadline, armed at
    `time_to_first_token_seconds` until the first body byte and at `idle_seconds` for every
    wait after. The cloud path is completely unchanged:
    `aiohttp.ClientTimeout(total=None, sock_connect=10)`, as today.

12. **Mid-stream failures and cancellation (constraint 8, §4.6).** After `response.prepare()`
    has run, no JSON error body can reach the client — the stream is already open on the wire.
    The handler's `except` clause is extended to also catch `asyncio.CancelledError` (uncaught
    today), and the post-`prepare()` failure path stops pretending a `json_response` return value
    does anything: it calls `request.transport.abort()` (abort the transport without a clean
    `write_eof()`, so the client observes a truncated/reset stream rather than a response that
    looks complete — `response.force_close()` is not sufficient: it only disables keep-alive,
    and aiohttp still finishes a returned response with a clean EOF), writes the ledger row with the real `status`/`failure_class`, and re-raises
    `CancelledError` (never swallows it — `asyncio` expects a cancelled task to actually cancel).
    A failure before `prepare()` is unaffected and keeps returning a real JSON error body.
    `protocol_incompatibility` is defined narrowly (Requirement 3's 3xx case, Requirement 10's
    unsupported path, method or field refusals, plus any response whose framing the adapter
    cannot parse where parsing is required — not a catch-all).

13. **Correlation and role (constraint 11, §4.4).** Outbound header name: `X-Factory-Correlation`,
    carrying compact JSON of `{run_id, issue, phase, role, endpoint_id, request_id}` built from
    `read_current_run()`'s existing `run_id`/`issue_number`/`intent`/`stage` fields (renamed to
    the envelope's `issue`/`phase`) plus `role`, `endpoint_id`, and a freshly generated
    `request_id` (uuid4). `role` is `"actor"` whenever the resolved entry is local — §4.2
    forbids a local-routed container from ever hosting an independent checker for the same unit
    of work, so this is a contract constant, not a guess; `role` is `null` on the cloud path,
    since a same-run cloud container may host either an actor or a checker and the proxy has no
    unforgeable signal to distinguish them (inventing one would violate #288's self-report
    rule). Any inbound `X-Factory-Correlation` header from the client is dropped and overwritten
    — it is self-report, untrusted. The header name is added to `model_proxy.py`'s
    `_REDACT_EXACT` set (not the `_REDACT_PATTERN` token/secret regex — it carries no secret,
    but §4.3 requires every new correlation header to join the redaction set regardless) so raw
    artifacts never persist it; the structured envelope fields are already recorded as ledger
    columns, so nothing is lost. The ledger's `correlation` column (distinct from `role`) is
    `"inferred_pointer"` when `read_current_run()` found a parseable `current-run.json`, else
    `"unknown"` when `run_id` is `"unknown"` — per §4.4, a `"unknown"` row can never be cited as
    dispatch-record proof of independence.

14. **4xx/5xx error shape (constraint 12).** Extends the existing `{"error": {"type": ...,
    "message": ...}}` envelope (today's only instance is the 504 `factory_model_proxy_upstream_error`
    body) with `failure_class` and `endpoint_id` nested under `error`. New `error.type`:
    `"factory_model_adapter_refused"` for every #558 refusal. Status codes: `400` for an
    unsupported-mode or unsupported-endpoint refusal (`protocol_incompatibility`,
    Requirement 10); `403` for a model-identity refusal (`destination_policy_denial`,
    Requirement 9); `502` for an upstream-caused refusal (`protocol_incompatibility`: a 3xx,
    a non-streaming 2xx body that is not a JSON object — buffered before `prepare()` — or a 2xx
    event stream ending without a complete `message_stop` frame, which aborts the transport);
    `504` is kept for the existing unreachable/timeout path, which also gains
    `failure_class`. Missing/expired/mismatched capability evidence, a registry digest mismatch
    and a payload decision with `allow: false` are startup failures (Requirements 2 and 15),
    never a request-time status: the registry is read and verified once at startup and never
    re-read per request. The
    `error.message` text is built only from already-redaction-safe values — it never includes a
    `secret_reference` value, a credential, or a raw header. As Requirement 12 notes, a
    post-`prepare()` failure cannot deliver any of this to the client; the ledger row is the
    record of truth for those cases.

15. **Payload-class decision, not eligibility (constraint 7's "no declared source" note, §4.3).**
    #557 owns the declared-`payload_class` operator-config surface and the dispatch-time
    predicate (`docs/archive/2026-11-22-hybrid-execution-contract-revision-design.md`'s operator
    follow-up for #557); as of this writing that surface is not yet wired into
    `scheduler.sh`/`provider_env.sh` (grep confirms neither references `payload_class` or
    `dispatch_allowed`/`effective_payload_class`), so `declared` is always `None` today. #558
    does not build that surface and adds no environment variable for it — the proxy calls
    `model_endpoint.payload_decision(None, entry)` once at startup, right after
    `load_verified_entry()` returns the entry (`declared` is the constant `None` in v1), treats
    `allow: false` as a startup failure, and writes the returned dict as the `payload_decision`
    column of every ledger row, from which `run_record.py` copies it into
    `model_dispatch.request` (Requirement 7). `effective_payload_class()` already
    handles `declared=None` correctly (`"local_only"` forced for local, `"cloud_ok"` default for
    cloud), so #558 needs no eligibility logic of its own; when a later ticket wires a real
    declared value in, only that one argument changes.

## Architecture / Approach

**Module layout** (Requirement 6):
- `providers/model_endpoint.py` (+1 function): `load_verified_entry()`.
- `providers/model_adapter.py` (new): `resolve_local_destination()` (startup: calls
  `load_verified_entry`, validates `MODEL_PROXY_UPSTREAM`, runs the credential hard-fail check,
  returns a frozen `LocalDestination` dict or raises on any problem), `strip_and_sign_headers()`,
  `check_model_identity()`, `resolve_timeout()`, `classify_failure()`, `build_correlation()`
  (header value + `role`/`correlation` fields), and a thin wrapper around
  `model_endpoint.payload_decision()`.
- `model_proxy.py` (modified): `main()` resolves the destination once (Requirement 2) and stores
  it on the app (`app["local_destination"]`, `None` on the cloud-default path); `handle_proxy()`
  branches once near the top on whether a destination is set, then calls into `model_adapter`
  for every local-only check; the streaming/ledger/error-handling code is shared between both
  paths exactly as today, with the local path adding the new ledger columns.
- `run_record.py` (modified): `_compute_model_dispatch()` (Requirement 7), the `cost_usd`
  null-preserving fix (Requirement 8) in `_nodes_from_run_obj` and `_classify_node_capture`, and
  the local-run cost override in `cmd_assemble` (Requirement 8).
- `docs/dark-factory-model-proxy-rollout.md` (modified): a "Local destination: supported and
  uncertified modes" section (Requirement 10).

**Startup sequence** (`model_proxy.main()`): if `FACTORY_MODEL_ENDPOINT` is unset, behavior is
byte-identical to today. If set: `model_adapter.resolve_local_destination(staged_registry_dir(),
endpoint_id)` runs `load_verified_entry()` (one `read_verified_tree()` call, so a tampered
registry or an endpoint that still trips `_NO_LOCAL_DISPATCH` fails here, before any socket
opens), validates `MODEL_PROXY_UPSTREAM` against the entry's `base_url` when hosting is local,
runs the `CLOUD_CREDENTIALS` hard-fail check, and computes `payload_decision(None, entry)` once
(`allow: false` is a problem). Any problem logs to stderr and the process
exits non-zero — `web.run_app()` is never reached, matching `provider_env.sh`'s existing
"a non-zero exit means do not dispatch" contract for everything else in this provider stack.

**Request path** (`handle_proxy()`, local destination set): parse `model` from the body (already
done); refuse on an unsupported path, method or mode (400, Requirement 10) or a model-identity
mismatch (403, Requirement 9) before making any outbound call; strip inbound auth headers and set the
outbound `x-api-key` from `secret_reference` (Requirement 4); build and attach
`X-Factory-Correlation` (Requirement 13); call the local `base_url` with `allow_redirects=False`
and the resolved timeout (Requirement 11); on success, stream exactly as today; on any §4.6
failure class, classify it (Requirement 12), write the ledger row with the new columns, and
return the extended error envelope (Requirement 14) or abort the transport if `prepare()` has
already run.

**Supported/unsupported modes (v1, Requirement 10):** supported — single-turn and multi-turn
requests, tool-result continuation turns, streaming (`text/event-stream`) and non-streaming
responses, as already exercised by the existing `TestServer`-based tests. Unsupported, refused
explicitly rather than silently passed through: forced-tool-choice requests, deferred/background
tool execution, and structured-output (JSON-schema-constrained) requests — none of these have a
registry-declared capability signal today, so the adapter refuses them unconditionally on the
local path pending a future capability-evidence field (§4.5, #561/#562).

Detection is fixed and field-based, on the local path only (the cloud path keeps today's generic
passthrough): the adapter accepts only `POST /v1/messages` (compared on `request.path`, so a
query string is allowed); any other path or method is refused 400 `protocol_incompatibility`.
Within `POST /v1/messages` a request is refused 400 `protocol_incompatibility` when
`tool_choice.type` is `any` or `tool` (forced tool), when any entry of `tools` carries
`defer_loading` (deferred tool), or when `output_config` or `output_format` is present
(structured output). Each refusal is a real error body and a ledger row, never a synthetic
success. The same list — supported modes, refused modes and fields, and the statement that
everything else about a local runtime stays uncertified until #561/#562 — is published in
`docs/dark-factory-model-proxy-rollout.md`.

**Run-record aggregation (Requirements 7 and 8):** `cmd_assemble` reads the request-ledger rows
for `args.run_id` once, via the existing `_read_ledger_rows()` helper and the same `LEDGER_PATH`
that `_compute_harness_economics()` already uses, before `_capture_totals` runs — the rows
decide the local-run cost override (Requirement 8). `_compute_model_dispatch()` is assigned
right after `harness_economics`, follows the identical compute-then-assign idiom, and works
from those same rows.

## Test Plan

Hermetic throughout; no code path here talks to a real local runtime (building on the
`TestServer`/`_make_upstream` pattern in `tests/test_factory_model_proxy.py`):

- **Default-cloud parity:** `FACTORY_MODEL_ENDPOINT` unset — every existing
  `test_factory_model_proxy.py` test still passes unmodified; new assertion that
  `model_dispatch.result.hosting_location == "cloud"` and `role is None` appear in a cloud run's
  assembled record, and that a cloud ledger row carries `hosting_location: "cloud"` and the
  constant `adapter_id`.
- **Startup failures:** digest mismatch (tampered staged file after `FACTORY_MODEL_REGISTRY_SHA256`
  is set) exits non-zero before `web.run_app()` — this is the test that discharges the
  digest re-check acceptance criterion (Requirement 5); an unset `FACTORY_MODEL_REGISTRY_SHA256`
  exits non-zero the same way; a payload decision with `allow: false` exits non-zero; `MODEL_PROXY_UPSTREAM` set to a value that
  differs from a local entry's `base_url` exits non-zero; a `CLOUD_CREDENTIALS` var present
  alongside a local entry exits non-zero; `_NO_LOCAL_DISPATCH` still present (no behavior change
  from #557) exits non-zero.
- **Header/payload redaction:** inbound `authorization`/`x-api-key`/`api-key` are absent from the
  forwarded request to the fake local upstream; the fake upstream sees exactly one `x-api-key`
  sourced from the `secret_reference` env var and nothing else; `X-Factory-Correlation` is present
  outbound, absent from the inbound-echoed set, and absent (redacted) from any persisted raw
  artifact.
- **Redirect refusal:** fake local upstream returns a 3xx — request is refused,
  `failure_class: "protocol_incompatibility"`, no follow-redirect occurs (`allow_redirects=False`
  verified via the fake server never receiving a second request).
- **Model-identity refusal:** request body names a model other than `served_name` (including a
  literal cloud model ID) — refused 403 with `failure_class: "destination_policy_denial"`, never
  forwarded, never remapped.
- **Unsupported mode refusal:** one case per refused field on the local path —
  `tool_choice.type` `any` and `tool`, a tool with `defer_loading`, `output_config`,
  `output_format` — each refused 400 `protocol_incompatibility` before any outbound call (the
  fake upstream receives nothing), with a ledger row.
- **Unsupported endpoint refusal:** on the local path, a `GET /v1/messages` and a
  `POST /v1/messages/count_tokens` are refused 400 `protocol_incompatibility` and never
  forwarded; `POST /v1/messages?beta=true` is forwarded; on the cloud path both are still
  passed through as today.
- **Adapter identity:** every ledger row, local and cloud, carries
  `adapter_id == "model_proxy/anthropic-messages-passthrough"`; a local row's `model` equals the
  entry's `served_name`.
- **Supported-modes document:** `docs/dark-factory-model-proxy-rollout.md` names every refused
  field and the single accepted endpoint (asserted by a test that reads the file).
- **Partial stream / malformed response / disconnect / cancellation:** fake upstream that sends a
  partial SSE frame then closes; a fake upstream returning truncated/invalid JSON on a
  non-streaming call; a fake upstream that accepts the connection then never responds (idle
  timeout fires); the handler task cancelled mid-stream (`asyncio.CancelledError` propagates,
  `request.transport.abort()` called, ledger row written with the real `failure_class`, no
  JSON body assumed delivered). For each post-`prepare()` failure the test client must raise a
  payload/connection error — it must not observe a cleanly terminated response.
- **Timeout classification:** fake upstream delaying first byte past `time_to_first_token_seconds`
  (classified as that specific class, not generic `idle`); fake upstream delaying a later chunk
  past `idle_seconds`; a slow-but-complete response finishing within `total_seconds`.
- **`cost_usd` regression (`tests/test_run_record.py`):** a node with `cost_usd: None` stays
  `null` through `_nodes_from_run_obj` and is excluded from `known_cost_usd`/`_sum_usage` without
  a `TypeError`; a fully-cloud, fully-complete run's assembled record is byte-identical to
  today's (no behavior change for the common case); `model_dispatch` present with
  `request.payload_class` populated via `payload_decision()`.
- **Local-run cost override (`tests/test_run_record.py`):** a run whose ledger rows show
  `hosting_location: "local"` and whose harness reports `costUsd: 0.42` on a node yields no
  numeric `cost_usd` anywhere in the assembled record — every node, `totals.cost_usd`,
  `totals.known_cost_usd` and `harness_economics.cost_per_task` are `null`, none is `0` — while
  token counts are kept.
- **Payload-decision persistence:** the proxy writes `payload_decision()`'s return value as the
  `payload_decision` column of each ledger row, and `model_dispatch.request` equals that dict
  plus `schema_version` (never recomputed in `run_record.py`); it carries the
  `secret_reference` name and no secret value. With no endpoint selected, `request` has
  `reason: "no endpoint selected"` and `null` for the other four fields. `request` and `result`
  each carry their own `schema_version`; `result` carries `endpoint_id` and `outcome`.

## Alternatives Considered

1. **Translation layer vs. pure passthrough.** Rejected — the registry enforces
   `protocol == "anthropic-messages"` for every certified entry (`model_endpoint.PROTOCOL`), and
   the issue explicitly warns against claiming `/v1/chat/completions` compatibility proves Claude
   agent compatibility. A pure passthrough with local-only policy checks is the only approach
   consistent with both.
2. **Single-module (`model_proxy.py` only) vs. the three-module split.** Rejected in the Q&A:
   `model_endpoint.py` is already 750+ lines; folding request-time logic into either existing
   file would create an unfocused grab-bag. The dedicated `model_adapter.py` also lets the
   Request/Result contract version independently of the Configuration contract, as §4.7 requires.
3. **Recomputing eligibility in #558 vs. only recording #557's decision.** Rejected — the issue
   states this ticket does not modify `_local_problems` or eligibility; `payload_decision()`'s
   own docstring says "(#558 records it)", not computes it.
4. **Building the declared-payload-class config surface now vs. accepting `None` today.**
   Rejected — that surface is explicitly #557's acceptance criterion
   (`docs/archive/2026-11-22-hybrid-execution-contract-revision-design.md`'s operator follow-up),
   not #558's; `effective_payload_class(None, ...)` already degrades correctly, so #558 needs no
   placeholder config mechanism of its own.

## Open Questions (non-blocking)

- Whether `time_to_first_token_seconds` enforcement should also re-arm after a mid-stream
  reconnect-less resume is not addressed — v1 treats the first chunk of the single outbound
  request as the only "first token," matching today's single-attempt (no retry) model.
  Recommend the implement phase add a focused unit test for the boundary case (chunk arrives at
  `time_to_first_token_seconds - 1ms`) but this does not change the design.
- `role` inference is a contract constant (`"actor"` for local, `null` for cloud) until #587/#559
  land and a real, unforgeable role signal exists; revisit then, not blocking #558.

## Assumptions (flagged)

- #593's fixes are confirmed present on `main` as of this refinement (2026-11-25); this branch
  was cut after `9c28aa5` (the #596 merge commit), so implementation calls `model_endpoint`'s
  verified-read functions as they exist post-#593, never the pre-fix shapes.
- `model_endpoint.py`, `model_proxy.py`, `run_record.py`, and `docs/hybrid-execution-contract.md`
  were read live from `main` at refinement time; re-verify line-number citations against current
  `main` before implementing if any of the four has since changed shape.
- The issue body's appended "Hermes Agent / Product Manager" requirements and the operator's
  twelve numbered constraints were cross-checked against the contract and against current code
  and found consistent with both; none expand any `gate_*`/budget/tool-allow-list/`deploy/**`
  surface, so they are treated as in-scope product input for this spec.
- No `scheduler.sh`/`provider_env.sh`/`entrypoint.sh`/`run-compose.yml` change is in scope. The
  proxy is a separate compose service (`run-compose.yml`'s `factory-model-proxy`) and today
  receives none of `FACTORY_MODEL_ENDPOINT`, `FACTORY_MODEL_REGISTRY_SHA256`,
  `ANTHROPIC_BASE_URL`, the four `ANTHROPIC_DEFAULT_*_MODEL` aliases or the `secret_reference`
  variable — #557's `provider_env.sh` machinery forwards the first two to the run container
  only. Delivering them to the proxy, and reconciling the run container's
  `ANTHROPIC_BASE_URL == base_url` rule (`_local_problems()`) with routing its traffic through
  the proxy, is #587's. #558 changes no compose file, so in production the proxy keeps taking
  today's cloud-default path until #587 lands.
