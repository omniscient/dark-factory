# Harness-Driven Capability Probes Against a Real Endpoint

**Issue:** #631

**Status:** amended after an independent review of the first refine draft (`778d571`). Every
mechanism below marked *prototyped* was run hermetically (`--network none`) with the pinned CLI
2.1.282 in the factory image against the in-process mock; see "Prototype evidence". Nothing in
this document was run against a real endpoint. A second independent review (of `a613171`) is
applied as well; its measurements are quoted where they changed a rule.

## Overview / Problem Statement

Part B (#624, merged) added a `driver: harness` fixture case to each of four
`ACTOR_REQUIRED_DIMENSIONS` dimensions (`protocol_compatibility`, `tool_call_fidelity`,
`context_length`, `timeout_cancellation`). Each spawns the pinned `claude -p` CLI in a scrubbed
subprocess environment and judges what an in-process Anthropic-Messages mock observed on the
wire — never the CLI's own reply. All four are hardcoded `target: mock`.

In `runner.run_cases()`, whenever `--base-url` names a real endpoint (`mock_mode=False`), every
harness case is skipped and contributes an explicit `not_tested` to its dimension's probe
values, even when Tier 2 is `"ok"` (`runner.py:487-493`). Three of the four dimensions also
carry a `target: endpoint` HTTP-driver probe from part A (#561); `timeout_cancellation` has no
other probe. `dimension_results()` stores the worst of a dimension's probe values
(`min(values, key=WORST_FIRST.index)`, `not_tested` ranking below `pass`), so that one
`not_tested` blocks all four dimensions from `pass` in every endpoint-mode run.

This ticket adds an endpoint-driven judgement for each of the four dimensions: the pinned CLI is
driven against the real endpoint through a local recording relay. The hermetic Tier 2 CI step
keeps its command line, and the four existing mock-target harness cases keep their fixtures,
check functions and judges.

## What an endpoint-mode `pass` proves, per case

The spawned CLI talks only to the relay. A verdict is therefore built from three kinds of
observation: what the CLI sent (CLI behaviour), what the endpoint answered as seen by the relay
(endpoint behaviour), and the CLI's exit status. Each case states which is which.

| Case | A `pass` proves | A `pass` does not prove | Depends on model choice |
|---|---|---|---|
| `protocol_compatibility/cli_alias_resolution_endpoint` | For each of `--model opus/sonnet/haiku/fable`: every request the CLI sent named the endpoint's served name and carried only the endpoint's credential (CLI behaviour); at least one request per alias was answered 200 and every 200 answer's streamed `message_start.message.model` equalled the served name (endpoint behaviour); the CLI exited 0. | Nested `Agent` dispatch against this endpoint (fixed `not_tested` observation; the mock mechanism record covers the CLI side). | No. |
| `tool_call_fidelity/cli_tool_round_trip_endpoint` | The model, offered only `Read`, emitted at least one `Read` call for the seeded file that the CLI could parse and execute; the CLI returned a `tool_result` carrying such a call's id; both planted lines reached the endpoint only inside `tool_result` blocks of those calls and no request's `model` changed. | That the model resists following the planted instruction in its *text* reply (assistant output is not judged); behaviour with the full production tool set. | **Yes.** If the model never calls `Read` in 2 fresh attempts the verdict is `fail:read_not_called`. |
| `context_length/cli_padded_prompt_endpoint` | A delimited block of `min(context_tokens // 2, 30000)` filler words passed through the CLI intact, was answered 200, and the relayed stream reached `message_stop`. | **The window.** The block is capped by the kernel's 131,072-byte limit on one argv string, and the judge never reads `usage`, so a silently truncating endpoint can pass this case. Window acceptance is part A's `context_length/sized_request` (which sums the cache token fields since #628). Over-limit behaviour is a fixed `not_tested` observation. | No. |
| `timeout_cancellation/cli_sigint_endpoint` | While a real streamed reply was in flight, `SIGINT` made the CLI hang up on the relay with no retry. The relay then closes its upstream connection; that is the relay's own code, proven by a relay test with a stand-in upstream, not something a run measures. | That the endpoint stops generating once its connection is dropped (not observable from outside it); CLI behaviour toward a server that is itself stalled (the mock mechanism record's `stall` script). | Partly: the model must still be streaming when `SIGINT` lands. If it finishes first in all 3 attempts the verdict is `not_tested`, never `pass`. |

All four verdicts are taken on the *proxied* network path: the CLI runs with proxy variables
pointing at the sink (see "No-other-host property"), and with them set the pinned CLI behaves
slightly differently from an unproxied dispatch (it skips its `HEAD /api/hello` preflight).

## Requirements

1. A new local **recording relay** (`relay.py`) sits between the spawned CLI and the target.
   The CLI's `ANTHROPIC_BASE_URL` always points at the relay. The relay forwards each request to
   exactly one configured upstream, streams the response back as it arrives, and records each
   request as `{method, path, headers, body, status, response_model, completed}` — the
   `mock_server._Observed` request shape plus two response facts.
2. Four new `kind: probe, target: endpoint, driver: harness` cases, one per dimension, each
   yielding `pass`/`fail`/`not_tested` against the configured endpoint when Tier 2 is `"ok"`.
3. Two sub-properties are not judged against a real endpoint and never count as `pass`: the
   over-limit half of `context_length` and the nested-`Agent` half of `protocol_compatibility`.
   Each is a `kind: observation, target: endpoint, driver: harness` case with a fixed
   `not_tested` and a reason token, recorded in `controls` (never `probe_values`), so the gap is
   explicit and the dimension can still reach `pass`.
4. Four new `kind: control, target: mock, driver: harness` cases, one per new probe, each
   running the same check function through the relay against the mock and each with a stated
   `expect_reason`. Controls run in mock mode **and** in endpoint mode, as HTTP controls do.
5. The four existing mock-target harness cases are unchanged (fixtures, check functions,
   judges). In mock mode they stay dimension probes. In endpoint mode they still run against the
   mock and are recorded as non-dimension `mechanism` records, so one endpoint-mode result shows
   both the endpoint-driven verdict and the mock-driven mechanism check (nested `Agent`,
   over-limit, stalled-server `SIGINT`).
6. The hermetic Tier 2 CI step's command line and hermeticity are unchanged (`--base-url mock`,
   `--network none`); `.github/workflows/ci.yml` is not edited. **Deliberate deviation from the
   issue's "stay exactly as they are":** only the command line and hermeticity stay. The
   mock-mode run executes the ten new cases against the mock, and its result has the new schema
   (Requirement 7). It must still exit 0 within the step's existing 10-minute timeout.
7. `certification_result` gains `harness_sources`, `harness_probes` (each executed harness
   probe's `value:reason`, so #562's evidence need not be copied from stdout) and a `source`
   key on every harness-driven `controls` record; `emit.SCHEMA_VERSION` becomes `"1.1"`.
8. Endpoint mode with Tier 2 off, or with no `claude` binary, or with a version mismatch behaves
   as today: every harness case is skipped, the four dimensions are `not_tested` and listed in
   `harness_pending`; exit 0 / 6 / 7 (7 writes no result) are unchanged.
9. Security of the spawn:
   - The environment is still built from an allow-list by `harness.scrubbed_env()`. The only
     credential is the endpoint's own (`--secret-env`'s value). All four tier aliases are the
     endpoint's served name. `HOME` and the working directory are temporary.
   - Endpoint-driven spawns restrict the tool surface: `--tools Read` for the tool case and
     `--tools ""` for the other three. No `--allowedTools` is passed.
   - Prompts and the planted file stay synthetic.
10. No request leaves for any host other than the endpoint. This is enforced and measured in
    two places (see "No-other-host property"): the relay dials only its upstream and never
    follows a redirect; the CLI's proxy variables point at a loopback sink that records and
    refuses everything it receives (`NO_PROXY=127.0.0.1` keeps the relay's loopback traffic away
    from it), and a hit on the sink fails the case.
11. Bounded: a refused, unreachable or stalled endpoint yields a classified `fail`; the bounds
    are the named constants in "Bounds".
12. `model_endpoint.PINNED_FIXTURE_SUITE_VERSION` is recomputed (`relay.py` is a new top-level
    module, covered by `emit.suite_digest()`'s `*.py` glob).
13. No registry entry, alias, routing or `deploy/**` change. No `gate_*`, breaker or budget
    change. `count_tokens` and forced `tool_choice` stay observations. No endpoint address,
    hostname or model tag in any committed file (`192.0.2.0/24` and placeholder names only).

## Architecture / Approach

### New module: `relay.py`

`scripts/factory_core/capability_suite/relay.py`, next to `mock_server.py`. It never fabricates
a model reply. *Prototyped.*

```python
@dataclass
class Relayed:
    requests: list = field(default_factory=list)      # one record per request received
    marker: str | None = None      # SIGINT case: text that identifies the watched request
    first_chunk: asyncio.Event = field(default_factory=asyncio.Event)
    client_gone: bool = False      # CLI hung up while the watched upstream stream was open
    upstream_done: bool = False    # the watched upstream stream reached EOF and was forwarded
    upstream_closed: bool = False  # the relay closed the upstream response after client_gone
    upstream_errors: list = field(default_factory=list)   # classify_failure classes, in order
    upstream_failed: asyncio.Event = field(default_factory=asyncio.Event)
    dialled: list = field(default_factory=list)       # "host:port" of every outbound request
    other_host: list = field(default_factory=list)    # first line of every sink connection
    sink_url: str = ""

RELAYED = web.AppKey("relayed", Relayed)

@contextlib.asynccontextmanager
async def started(upstream_base_url, *, timeouts, fault=None):
    """Yields (app, relay base URL). Relay and sink each bind an ephemeral 127.0.0.1 port."""

def watch(app, marker) -> Relayed:
    """Start watching one request: set `marker`, clear `first_chunk`, `client_gone`,
    `upstream_done` and `upstream_closed`. `requests` and the error/sink lists are kept."""

BAD_GATEWAY_BODY = {"type": "error", "error": {
    "type": "api_error", "message": "capability-suite relay: upstream request failed"}}
```

`relay.py` imports neither `runner` nor `harness` (no import cycle): the caller passes
`timeouts`, and `runner._harness_probe` passes `runner.DEFAULT_TIMEOUTS`.

- **Bind and routes.** `web.AppRunner(app, handler_cancellation=True)` on `127.0.0.1:0`.
  `POST /v1/messages` and `HEAD /api/hello` are forwarded. Any other method or path is recorded
  with `status: 404` and answered 404 without contacting the upstream.
- **Outbound.** One `aiohttp.ClientSession(trust_env=False, auto_decompress=False)` with a
  `TraceConfig` whose `on_request_start` appends `host:port` to `dialled`. The URL is always
  `upstream_base_url.rstrip("/") + request.path_qs`; every request uses
  `allow_redirects=False`. A 3xx is returned to the CLI unfollowed and without its `Location`
  header, so the CLI is never handed another host to try.
- **Request headers.** Copied unchanged — including `x-api-key`, which is never swapped —
  except `Host`, `Content-Length`, `Connection`, `Keep-Alive`, `Proxy-Connection`, `TE`,
  `Transfer-Encoding`, `Upgrade` and `Accept-Encoding`, which are dropped; `Accept-Encoding:
  identity` is set. When `fault` is given (a control whose upstream is the mock)
  `mock_server.FAULT_HEADER: <fault>` is added. The body is forwarded as the raw bytes read.
- **Response.** Status plus `Content-Type` and `Retry-After` are returned; the body
  is written chunk by chunk as it arrives (`content.iter_any()`), never buffered.
- **Recording.** The record is appended before forwarding, with lower-cased headers and the
  parsed JSON body (`None` if it does not parse). `status` is the upstream status.
  `response_model` is `message_start.message.model`, parsed from at most the first 65,536
  response bytes. `completed` is true when a forwarded `data:` line matches
  `"type"\s*:\s*"message_stop"` — the rule `model_adapter.check_sse_complete` applies; the
  pattern is repeated in `relay.py` and matched on a rolling tail, so a chunk boundary cannot
  hide it. The `event:` line is not what is matched. Response bodies are not kept.
- **Watched request (SIGINT case).** A request is *watched* when `marker` is set and occurs in
  a user text block of its body. Only a watched request sets `first_chunk` (after its first
  chunk is written), `upstream_done` (after upstream EOF is forwarded) and `client_gone` (when
  the handler is cancelled or a write raises `ConnectionResetError` **before** `upstream_done`).
  After `client_gone` the relay calls `close()` on the upstream response and stores its `closed`
  flag in `upstream_closed`. That flag reads back the relay's own call, so it is a record, not
  a judgement input; the proof that the upstream sees the hang-up is the relay test in which a
  stand-in upstream observes it.
- **Upstream failure.** `timeouts` supplies `connect_seconds`, `time_to_first_token_seconds`
  and `idle_seconds` (from `runner.DEFAULT_TIMEOUTS`: 10 s, 300 s, 120 s). A connect error,
  timeout or mid-stream error appends `model_adapter.classify_failure(...)`'s class to
  `upstream_errors` and sets `upstream_failed`. Before response headers are sent the CLI gets
  status 502 with `BAD_GATEWAY_BODY`; after, the connection to the CLI is closed.
- **Sink.** `started()` also starts a loopback TCP listener, the *proxy sink*. It reads the
  first line of each connection, appends it to `other_host`, answers `403` and closes: it
  refuses everything. Its URL is `sink_url`.
- **Secrets.** The relay never logs, prints or writes headers or bodies; records live only in
  memory for the duration of one case. Judges return fixed reason tokens only.

### `harness.py` changes

- `scrubbed_env(*, base_url, served_name, home, credential=mock_server.DUMMY_CREDENTIAL,
  proxy=None)`. With `proxy` set it adds exactly `HTTP_PROXY`, `HTTPS_PROXY` (both `proxy`) and
  `NO_PROXY=127.0.0.1`. `BASE_ENV_KEYS` is unchanged; `RELAY_ENV_KEYS = BASE_ENV_KEYS +
  ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")`. The two existing call sites (`preflight`,
  `start_cli`) keep today's output.
- `start_cli` / `run_cli` gain keyword-only `served_name`, `credential`, `proxy` (defaults: the
  mock's constants and `None`) and pass them to `scrubbed_env`. Today `start_cli` hardcodes
  `mock_server.SERVED_NAME` (`harness.py:98`); this is what makes all four aliases point at the
  endpoint's served name.
- `run_relayed(relay_app, relay_url, root, prompt, *flags, served_name, credential, seconds)`:
  `seconds` is required, with no default. It spawns with `proxy=relay_app[RELAYED].sink_url`,
  then waits for the first of: process exit, `upstream_failed`, `seconds`. On `upstream_failed`
  or expiry it kills the process. Returns `(exit status or None, output)`; `None` means the
  process was killed. `output` is never printed, logged or stored: no relay check reads it.
- `spawn_seconds(mock_app)`: `RUN_SECONDS` when `mock_app is not None`, else
  `ENDPOINT_RUN_SECONDS`. Every relay check passes it as `seconds`. The relay allows a real
  endpoint 300 s to its first byte, so a 120 s spawn bound would kill a slow but healthy run.
- `_naming_problem(requests, served_name=mock_server.SERVED_NAME,
  credential=mock_server.DUMMY_CREDENTIAL, credential_reason="credential_not_dummy")`: the
  comparisons use the two parameters; the endpoint judges pass the upstream's values and
  `credential_reason="credential_mismatch"`. The legacy judges call it unchanged.
- New constants: `MAX_ARG_WORDS = 30000`, `ENDPOINT_RUN_SECONDS = 900.0`,
  `FIRST_CHUNK_SECONDS = 310.0`, `ENDPOINT_CASE_SECONDS = 1800.0`, `READ_ATTEMPTS = 2`,
  `SIGINT_ATTEMPTS = 3`, `SIGINT_MARKER = "capability-suite sigint probe"`,
  `VARIANTS = ("unplanted_file", "no_sigint")`,
  `FIXED_OBSERVATIONS = {"cli_over_limit_not_tested": "over_limit_not_judged_on_endpoint",
  "cli_nested_agent_not_tested": "nested_agent_not_judged_on_endpoint"}`.

Every relay check has the signature

```python
async def check_x_endpoint(relay_app, relay_url, *, served_name, credential, context_tokens,
                           mock_app, variant) -> tuple[str, str] | None
```

`mock_app` is the in-process mock's app when the relay's upstream is the mock (a mock-mode run
or a control) and `None` when it is a real endpoint. When it is not `None` the check arms, in
process and before each spawn, the same `mock_server.Script` its legacy sibling arms; an
unarmed mock answers every tool-bearing CLI request with a fabricated call to `tools[0]` and
the CLI never terminates. Nothing is ever armed for a real endpoint. `mock_server.py` is not
edited. *Prototyped.*

After every `run_relayed` call a relay check applies two rules, in this order, before any judge:

1. `upstream_errors` non-empty → return `("fail", upstream_errors[0])`;
2. exit status `None` (killed at `seconds`) → return `("fail", "cli_timeout")`.

Either return is final for the case and nothing further is spawned. No judge ever runs over the
records of a killed spawn: the second review measured that such records read as
`opus_response_model_mismatch` (alias), `read_not_called` (tool, burning the second attempt)
and `not_answered_200` (context). The SIGINT case does not use `run_relayed` and keeps its own
attempt verdict.

**`check_alias_resolution_endpoint`** — for each tier in `TIERS`: arm `Script("text")` if
`mock_app`; `run_relayed(..., ASK_OK, "--model", tier, "--tools", "")`.
`judge_alias_endpoint(runs, served_name, credential)` is a new judge (`judge_alias` is
unchanged, including its nested-`Agent` tail). Per run, in order: no `POST /v1/messages` →
`{tier}_no_request`; `_naming_problem` → `{tier}_model_not_served_name` /
`{tier}_credential_mismatch`; no POST answered 200 → `{tier}_not_answered_200`; any POST
answered 200 whose `response_model != served_name` → `{tier}_response_model_mismatch`; exit
status not 0 → `{tier}_cli_failed`. Otherwise `pass:ok`. Only 200 answers are compared, so a
transient 429 or 5xx followed by the CLI's successful retry is not reported as a model mismatch.

**`check_tool_round_trip_endpoint`** — up to `READ_ATTEMPTS` fresh attempts (fresh `workdir()`
each). Seed `cwd/PLANTED_FILE` with the two planted lines (with `variant == "unplanted_file"`:
one line of unrelated text); arm `Script("read", read_path=seeded)` if `mock_app`;
`run_relayed(..., READ_PROMPT, "--tools", "Read")`. `judge_read_endpoint(requests, seeded, cwd,
served_name)`, in order:

1. no POST → `no_request`;
2. the first POST's tool names are not exactly `["Read"]` → `tools_not_read_only`;
3. the *call ids* are the `id`s of **all** assistant `tool_use` blocks named `Read` whose
   `input.file_path` names the seeded file, read from the CLI's own follow-up requests. A
   `file_path` names it when `os.path.normpath(os.path.join(cwd, file_path))` equals the seeded
   path, so a relative path counts. Empty set → `read_not_called` (the only reason that
   consumes an attempt; any other verdict is final);
4. no `tool_result` whose `tool_use_id` is in the set → `tool_use_id_mismatch`;
5. for each planted line, over every string in every POST body **with `role: assistant`
   messages removed**: not found inside a `tool_result` whose id is in the set →
   `planted_not_carried`; found anywhere else (`system`, `tools`, a user or system message,
   another call's `tool_result`) → `payload_promoted`;
6. any POST whose `model != served_name` → `model_switched`.

The ids are derived from the request because a real endpoint mints its own; the mock constant
`TOOL_USE_ID` is not used. It is a set because a model may read the file twice, in sequence or
in parallel, and the second `tool_result` must not read as promotion. Assistant turns are excluded from step 5 because a model that quotes
the file is producing output, not promoting it. *Prototyped against the mock (`pass:ok`, 2
requests).*

**`check_context_window_endpoint`** — returns `None` (not run, exit 4, as
`context_length/sized_request` does) when `context_tokens` is `None`. `words =
min(context_tokens // 2, MAX_ARG_WORDS)`; arm `Script("text")` if `mock_app`;
`run_relayed(..., padded_prompt(words), "--tools", "")`.
`judge_padded_endpoint` is `judge_padded` (block intact in a user text block, answered 200)
plus: that request's `completed` is false → `stream_incomplete`. No over-limit attempt. The
judge never reads `usage`; if a later change does, it must sum `input_tokens`,
`cache_read_input_tokens` and `cache_creation_input_tokens`, as `runner.check_context_window`
does since #628. `MAX_ARG_WORDS` exists because the prompt is one argv string and Linux caps
one argv string at 131,072 bytes: `padded_prompt(30000)` is 120,064 bytes and spawns;
`padded_prompt(32768)` is 131,136 bytes and raises `OSError: Argument list too long`.
*Prototyped.*

**`check_sigint_endpoint`** — up to `SIGINT_ATTEMPTS` fresh attempts; attempt `n` (1-based)
uses a fresh `workdir()` and the prompt `f"{SIGINT_MARKER} {n}: write {40 * 2 ** (n - 1)}
paragraphs about rivers."` with `--tools ""`. Per attempt:

1. `relay.watch(app, f"{SIGINT_MARKER} {n}:")`; arm `Script("stall")` if `mock_app`; spawn
   with `start_cli` (same environment as `run_relayed`);
2. wait for `first_chunk`, `upstream_failed` or `FIRST_CHUNK_SECONDS` (310 s: ten seconds past
   the relay's own first-byte bound, so a silent endpoint ends as the relay's classified
   `timeout_time_to_first_token`, not as `no_stream`);
3. on `first_chunk`, unless `variant == "no_sigint"`, send `SIGINT`;
4. wait until `client_gone or upstream_done`, at most `DISCONNECT_SECONDS`;
5. **snapshot** the three flags `client_gone`, `upstream_done`, `upstream_closed`; then reap
   the process (`finish(proc, DISCONNECT_SECONDS)`, which kills it if needed); **then** count
   the watched requests. Reaping also hangs up, so the flags are never read after it (*the
   first prototype did, and its no-`SIGINT` control reported `pass`*). The count is taken after
   it because a retry is a request the CLI sends after its hang-up: counted before the process
   is dead, `fail:retried` could not be reached. A CLI still running after its hang-up is
   given `DISCONNECT_SECONDS` before it is killed, so a retry it would have sent later than that
   is not seen.

Attempt verdict, in order: an upstream error → `fail:<class>`; no first chunk →
`fail:no_stream`; the watched request's status is not 200 → `fail:not_answered_200` (an error
answer also produces a first chunk and an EOF, and would otherwise end as `not_tested`);
watched requests ≠ 1 → `fail:retried`; `upstream_done` → attempt consumed; not `client_gone` →
`fail:connection_not_closed`; else `pass:ok`. There is no `upstream_not_closed` reason:
`upstream_closed` is the relay reading back its own `close()` and cannot be false after
`client_gone`. A `fail` or `pass` is final. Three consumed attempts →
`not_tested:stream_finished_before_sigint`. A stream that ends normally is therefore
never a `pass`: the CLI closing its connection after `upstream_done` sets no flag.
*Prototyped: stalled stream `pass`; normally finishing stream `not_tested`.*

**Fixed observations** — the two names in `FIXED_OBSERVATIONS` have no check function and open
no relay; `_harness_probe` returns `("not_tested", FIXED_OBSERVATIONS[name])`.

### `runner.py` changes

`HARNESS_CHECKS` keeps its four names. New `HARNESS_RELAY_CHECKS` maps
`cli_alias_resolution_endpoint`, `cli_tool_round_trip_endpoint`, `cli_context_window_endpoint`,
`cli_sigint_endpoint` to the four functions above. `_CASE_KEYS` gains `variant`.

**`_validate()`** — the rule "a harness case is a mock-target probe" is replaced by:

- a harness `check` is in `HARNESS_CHECKS`, `HARNESS_RELAY_CHECKS` or
  `harness.FIXED_OBSERVATIONS`;
- a `HARNESS_CHECKS` name requires `kind: probe, target: mock`;
- a `HARNESS_RELAY_CHECKS` name requires `kind: probe, target: endpoint` or `kind: control`
  (the existing rule forces a control to `target: mock`);
- a `FIXED_OBSERVATIONS` name requires `kind: observation` (existing rule: `target: endpoint`);
- `variant` is allowed only on `driver: harness, kind: control` and must be in
  `harness.VARIANTS`.

**`run_cases()`** harness branch:

```python
if case["driver"] == "harness":
    legacy = case["check"] in HARNESS_CHECKS
    if tier2_mode != "ok":                      # unchanged bookkeeping, every harness kind
        if case["kind"] == "probe":
            summary.probe_values.setdefault(case["dimension"], []).append("not_tested")
        summary.harness_pending.append(case["dimension"])
        summary.lines.append(f"{name} [{case['kind']}] {_SKIPPED[tier2_mode]}")
        continue
    outcome, source = await _harness_probe(case, name, mock_app=mock_app, mock=mock,
                                           endpoint=endpoint, mock_mode=mock_mode,
                                           context_tokens=context_tokens)
    mechanism = legacy and not mock_mode        # a mock-driven case in an endpoint-mode run
```

- `outcome is None`: `not_run` and exit 4 as today. `harness_pending` and a `not_tested` in
  `probe_values` are added only when the case is not `mechanism`: a mock mechanism case that
  did not run says nothing about the endpoint's dimension.
- `mechanism`: appended to `summary.controls` as `{"dimension", "case", "kind": "mechanism",
  "expected": "pass:ok", "observed", "ok": observed == expected, "source": "mock"}`. It adds
  nothing to `probe_values` or `summary.probes`. A mismatch is exit 3 through the existing
  `any(not c["ok"] ...)` rule: the suite's own mock mechanism check failed.
- otherwise a probe goes to `probe_values`/`probes` as today and is also appended to
  `summary.harness_probes` as `{"dimension", "case", "observed": f"{value}:{reason}",
  "source"}`; a probe whose value is `not_tested` also appends its dimension to
  `harness_pending`. A control or observation goes to `controls` as today with
  `"source": source` added.
- every probe or mechanism outcome adds `source` to `summary.harness_probe_sources[dimension]`
  (a set) when its value is `pass` or `fail`.

**`_harness_probe()`** returns `(outcome, source)`:

```python
async def _harness_probe(case, name, *, mock_app, mock, endpoint, mock_mode, context_tokens):
    check = case["check"]
    use_mock = case["target"] == "mock" or mock_mode
    source = "mock" if use_mock else "endpoint"
    try:
        if check in harness.FIXED_OBSERVATIONS:
            return ("not_tested", harness.FIXED_OBSERVATIONS[check]), source
        if check in HARNESS_CHECKS:
            return await HARNESS_CHECKS[check](mock_app, mock.base_url), "mock"   # unchanged
        upstream = mock if use_mock else endpoint
        window = mock_server.CONTEXT_TOKENS if use_mock else context_tokens
        async with relay.started(upstream.base_url, timeouts=DEFAULT_TIMEOUTS,
                                 fault=case.get("fault")) as (app, url):
            try:
                outcome = await asyncio.wait_for(
                    HARNESS_RELAY_CHECKS[check](
                        app, url, served_name=upstream.served_name,
                        credential=upstream.credential, context_tokens=window,
                        mock_app=mock_app if use_mock else None, variant=case.get("variant")),
                    harness.ENDPOINT_CASE_SECONDS)
            except TimeoutError:
                outcome = ("fail", "timeout_total")
            if app[relay.RELAYED].other_host:
                outcome = ("fail", "other_host_contacted")
            return outcome, source
    except Exception as exc:  # a suite fault, never a finding
        detail = f": {exc}" if source == "mock" else ""   # endpoint: the type only
        print(f"capability suite: harness case {name} did not run: {type(exc).__name__}{detail}",
              file=sys.stderr)
        return None, source
    finally:
        mock_server.arm(mock_app)
```

For a case whose upstream is the mock the message is printed as today. For a case whose
upstream is the endpoint only the exception's type is printed: an aiohttp error text can
contain the endpoint address. Each check reaps its processes in `finally`, so a cancelled
check leaves no CLI behind. A control uses the mock's declared window, as HTTP controls do.

**`exit_code()`** is unchanged in code; its docstring and `docs/configuration.md` state the
extended meaning of 3: a control mismatch **or a mock mechanism record that is not `pass:ok`**.

**`main()`** passes `summary.harness_probes` and `harness_sources` to `emit.build_result()`: for each
`ACTOR_REQUIRED_DIMENSIONS` key, `"both"` if the set is `{"mock", "endpoint"}`, the single
member if it has one, else `None`.

**Resulting run matrix (Tier 2 `"ok"`)**

| Case group | Mock mode | Endpoint mode |
|---|---|---|
| 4 legacy mock probes | dimension probes, source `mock` | `mechanism` records, source `mock` |
| 4 new endpoint probes | dimension probes via relay → mock, source `mock` | dimension probes via relay → endpoint, source `endpoint` |
| 4 new controls | via relay → mock | via relay → mock |
| 2 fixed observations | `not_tested`, source `mock` | `not_tested`, source `endpoint` |
| `harness_sources` (four dimensions) | `"mock"` | `"both"` when both ran to a verdict |

In endpoint mode a dimension's `results` value comes only from endpoint-target probes (HTTP and
harness); `"both"` says a mock-driven mechanism record for that dimension is also present in
`controls`.

### `emit.py` changes

- `SCHEMA_VERSION = "1.1"`. The change is additive; no code reads a `certification_result`
  (`load_evidence()` rejects it by its top-level key) and nothing gates on the version.
- `build_result()` gains required `harness_sources`, validated like `results`: keys exactly
  `ACTOR_REQUIRED_DIMENSIONS`, values in `("mock", "endpoint", "both", None)`, else `ValueError`.
- `build_result()` gains required `harness_probes`: a list of `{dimension, case, observed,
  source}` for every executed `driver: harness` probe, `observed` matching
  `^(pass|fail|not_tested):[a-z0-9_]+$` and `source` in `("mock", "endpoint")`, else
  `ValueError`. Reasons are the judges' fixed tokens, so nothing free-form reaches the file.
  HTTP and function probes get no record (unchanged).
- `controls` records are copied as today; harness-driven ones carry `source`; the new record
  kind is `mechanism`.

### No-other-host property

Two mechanisms, each with a test, and one stated limit.

1. **The relay** dials only `upstream_base_url` and never follows a redirect. *Prototyped: an
   upstream answering 307 to a second loopback listener; the relay handed the 307 back, the
   second listener received 0 requests, `dialled` held only the upstream.* (The prototype still
   passed `Location` through; the spec drops it.)
2. **The CLI.** Its allow-listed environment already disables non-essential traffic. For every
   relay-driven spawn it also gets `HTTP_PROXY`/`HTTPS_PROXY` = the sink and
   `NO_PROXY=127.0.0.1`, so relay traffic goes direct and anything else is delivered to the
   sink, which refuses everything it receives and records it; any sink hit turns the case into `fail:other_host_contacted`.
   *Prototyped with an `LD_PRELOAD` shim logging every `connect()`, `sendto()` and
   `getaddrinfo()` of the CLI process:*
   - shipped scrubbed environment: the only connections were to the mock; no name lookup;
   - with `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` and `DISABLE_AUTOUPDATER` removed and no
     proxy: lookups of `api.anthropic.com` (the traffic the allow-list suppresses);
   - same, plus the three proxy variables (upper-case only): no lookup, no direct connection;
     the sink received `CONNECT api.anthropic.com:443` five times;
   - scrubbed environment plus the proxy variables: sink received nothing, run exited 0;
   - `NO_PROXY` omitted: the request to `127.0.0.1` itself went to the sink and the run failed,
     so `NO_PROXY=127.0.0.1` is required.

**What remains unproven.** The sink sees only traffic that honours the proxy variables. HTTP
requests do, whether the host is a name or an IP literal. The gap is a non-HTTP protocol or raw
socket, and a child process that ignores those variables; either would bypass the sink. None was observed in the shim measurement, but
that measurement was made with the mock's scripted replies, not a real model's, and it is not
part of the committed tests (it needs a C compiler and `LD_PRELOAD`). The `--tools` restriction
(no `Bash`, `WebFetch` or `Agent` offered) is what bounds a real model's ability to cause such
traffic. One observed side effect: with proxy variables set the pinned CLI does not send its
`HEAD /api/hello` preflight; no judge depends on it, but it shows that the proxied path on
which endpoint verdicts are taken is not identical to an unproxied dispatch.

### Tool surface with a real model

*Prototyped:* without `--tools` the pinned CLI offers 20 tools (including `Agent`, `Bash`,
`WebFetch`, `Write`); `--tools Read` offers exactly `Read`; `--tools ""` offers none.
`--allowedTools Read` lets `Read` open a file outside the working directory; without
`--allowedTools`, `Read` works inside the working directory and a path outside it is refused
with a permission error (same with `--tools Read`). Hence: `--tools Read` and **no**
`--allowedTools` for the tool case, `--tools ""` for the other three. A real model can then
read only files under the temporary working directory, which holds only the seeded file. The
legacy mock cases keep their flags (`--allowedTools Read`): their replies are scripted.

Consequence, stated once: the three `--tools ""` cases send a smaller request (about 7.5 k
characters of fixed overhead instead of about 62.7 k) than a production dispatch.

### Bounds

| Constant | Value | Bounds |
|---|---|---|
| relay connect / first byte / idle | 10 s / 300 s / 120 s (`runner.DEFAULT_TIMEOUTS`) | one upstream request |
| `RUN_SECONDS` (existing) | 120 s | one `run_relayed` spawn when the upstream is the mock; legacy cases |
| `ENDPOINT_RUN_SECONDS` | 900 s | one `run_relayed` spawn when the upstream is a real endpoint (alias, tool, context) → `fail:cli_timeout` |
| `FIRST_CHUNK_SECONDS` | 310 s | SIGINT case, wait for the stream to start (after the relay's 300 s) |
| `DISCONNECT_SECONDS` (existing) | 10 s | SIGINT case, wait for the hang-up; reap |
| `ENDPOINT_CASE_SECONDS` | 1800 s | one relay case in total → `fail:timeout_total` |

Harness cases have no per-case `timeouts:` key (that key is read only by the HTTP driver's
`_post`), so these constants are the whole mechanism. A refused upstream is classified on the
first request: *prototyped, the check stopped 0.5 s after spawn with `ClientConnectorError`
recorded;* without the relay's short-circuit the pinned CLI retried a refused connection for
185 s. A stalled upstream is classified by the relay's first-byte or idle timeout. There is no
per-run timeout today and none is added; the worst case is four relay cases at
`ENDPOINT_CASE_SECONDS` each. The alias case has four spawns, so a very slow endpoint can reach
`timeout_total` before a fourth `cli_timeout`; both are classified fails.

### New fixture cases

| File (new, each `<dir>/case.yaml`) | kind | target | check | extra keys |
|---|---|---|---|---|
| `protocol_compatibility/cli_alias_resolution_endpoint` | probe | endpoint | `cli_alias_resolution_endpoint` | |
| `protocol_compatibility/cli_alias_resolution_endpoint_control` | control | mock | `cli_alias_resolution_endpoint` | `fault: wrong_model`, `expect: fail`, `expect_reason: opus_response_model_mismatch` |
| `protocol_compatibility/cli_nested_agent_endpoint_scope` | observation | endpoint | `cli_nested_agent_not_tested` | |
| `tool_call_fidelity/cli_tool_round_trip_endpoint` | probe | endpoint | `cli_tool_round_trip_endpoint` | |
| `tool_call_fidelity/cli_tool_round_trip_endpoint_control` | control | mock | `cli_tool_round_trip_endpoint` | `variant: unplanted_file`, `expect: fail`, `expect_reason: planted_not_carried` |
| `context_length/cli_padded_prompt_endpoint` | probe | endpoint | `cli_context_window_endpoint` | |
| `context_length/cli_padded_prompt_endpoint_control` | control | mock | `cli_context_window_endpoint` | `fault: over_limit`, `expect: fail`, `expect_reason: not_answered_200` |
| `context_length/cli_over_limit_endpoint_scope` | observation | endpoint | `cli_over_limit_not_tested` | |
| `timeout_cancellation/cli_sigint_endpoint` | probe | endpoint | `cli_sigint_endpoint` | |
| `timeout_cancellation/cli_sigint_endpoint_control` | control | mock | `cli_sigint_endpoint` | `variant: no_sigint`, `expect: fail`, `expect_reason: connection_not_closed` |

Two controls make the mock misbehave through an existing mock fault the relay injects
(`wrong_model`, `over_limit`). The other two use a `variant` because no mock-side fault can
produce the failure: what is carried in a `tool_result` is the seeded file's content, and
whether the CLI hangs up is the CLI's doing. All four `expect_reason` values were observed in
the prototype. The alias control is why the alias judge checks `response_model`: `wrong_model`
changes only the response, and a judge reading requests alone returned `pass:ok` under it.

### Tests (`tests/test_capability_suite.py`)

New:

- relay: loopback bind; verbatim body and `x-api-key`; dropped and added headers; chunked
  streaming order; `response_model` and `completed`; unknown path → recorded 404, upstream not
  contacted; 3xx not followed (second listener gets 0 requests) and handed back without
  `Location`; `completed` set by a `data:` line with spaced `"type" : "message_stop"` and not by
  an `event:` line alone; `watch()` resets exactly the watched-request state; `dialled` is only the
  upstream; refused upstream → 502 and `endpoint_unreachable`-class record; watched-request
  flags for (a) client hang-up mid-stream — the stand-in upstream also observes the hang-up,
  (b) a stream that ends normally (`upstream_done`, not `client_gone`); sink records and refuses.
- endpoint-mode environment (acceptance criterion 5): a stub `claude` dumping
  `/proc/self/environ`, spawned through `run_relayed` with a sentinel credential and served
  name: key set is exactly `RELAY_ENV_KEYS`; `ANTHROPIC_API_KEY` is the sentinel; all four tier
  aliases are the served name; `HOME` is temporary; `CLAUDE_CODE_OAUTH_TOKEN`, `GH_TOKEN` and
  the caller's `HOME` absent; argv carries the expected `--tools` value.
- no-other-host for the CLI: a stub `claude` that requests `http://192.0.2.1/` honouring
  `HTTP_PROXY` → the case outcome is `fail:other_host_contacted`.
- judges as pure functions: `judge_alias_endpoint` (every reason, and a 429 followed by a 200
  retry that passes), `judge_read_endpoint` (every reason above, an assistant echo that is not
  `payload_promoted`, a non-mock tool id that passes, a relative `file_path` that passes, the
  file read twice — sequentially and in parallel — that passes), `judge_padded_endpoint`, the
  SIGINT attempt verdict from each snapshot shape, including "flags set only after reaping" →
  `connection_not_closed`, a non-200 watched request → `not_answered_200`, and a second watched
  request arriving after the hang-up → `retried`.
- killed spawn: with a stub `claude` that outlives `seconds`, the alias, tool and context
  checks each return `fail:cli_timeout`, spawn nothing further (tool: no second attempt) and
  call no judge; `spawn_seconds(None)` is `ENDPOINT_RUN_SECONDS` and `spawn_seconds(app)` is
  `RUN_SECONDS`.
- `MAX_ARG_WORDS`: `len(padded_prompt(MAX_ARG_WORDS).encode()) < 131072`.
- `_validate()`: each new rule; `variant` outside `VARIANTS`; a relay check as a mock-target
  probe is rejected.
- `run_cases()` with faked checks: mock mode vs endpoint mode routing and `source`; controls
  run in endpoint mode; `mechanism` records and exit 3 on a mechanism mismatch; a mechanism
  case that does not run is exit 4 without `harness_pending`; the exception message is printed
  for a mock-upstream case and withheld for an endpoint-upstream one; a `not_tested`
  endpoint probe lists its dimension in `harness_pending`; `other_host` and `timeout_total`
  overrides.
- acceptance criterion 2: endpoint mode with Tier 2 off → four dimensions `not_tested`, same
  `harness_pending`, exit 0; no binary → exit 6; version mismatch → exit 7, no file.
- result: `schema_version == "1.1"`; `harness_sources` and `harness_probes` validation;
  `harness_probes` carries each harness probe's `value:reason`; `"mock"` in a mock run and
  `"both"` in an endpoint run against a stand-in endpoint with faked checks.
- `test_endpoint_mode_writes_no_url_or_credential` repeated with Tier 2 on and faked relay
  checks: the sentinel credential and the host:port appear in neither the result nor
  stdout/stderr.

Existing tests whose pinned sets or counts change: `test_each_formerly_pending_dimension_ships_one_harness_case`,
`test_every_mapped_control_and_observation_exists`,
`test_tier2_with_passing_harness_checks_passes_all_six_dimensions` (`len(calls) == 4`),
`test_a_malformed_harness_case_is_rejected`, `test_every_harness_check_is_a_coroutine_function`,
`test_tier2_runs_every_harness_case_and_clears_its_script` (it compares `len(calls)` with the
number of harness cases, and the two fixed observations call nothing), the `_result()` helper
and its four users (`build_result()` gains two required arguments), and the `_fake_harness`
helper: it must also fake `HARNESS_RELAY_CHECKS`, and for a control it must return that
control's own `expect`/`expect_reason`, or every faked run exits 3.
`test_scrubbed_env_is_exactly_the_allow_list` and
`test_the_spawned_cli_gets_only_the_scrubbed_environment` stay as they are, as do
`test_every_control_matches_its_expect` (15) and `test_observations_are_recorded_with_no_expect`:
they run with Tier 2 off, where every harness case is skipped. The four new controls execute
with the real CLI only in the CI Tier 2 step, where a mismatch is exit 3.

### Docs

- `docs/configuration.md` (the capability-suite paragraph, about lines 564-581): endpoint-mode
  harness probes exist; what each `pass` proves and does not (the table above, condensed); the
  two fixed `not_tested` observations; `mechanism` records; `harness_sources` and schema 1.1;
  the tool case and the SIGINT case depend on model behaviour; the context case is not a window
  test; the proxy sink and its stated limit; verdicts are taken on the proxied path; exit 3
  also means a mock mechanism record that is not `pass:ok`; `harness_probes`.
- `docs/features.md` (the capability-suite row, line 174): the "Known limits" cell.
- Module docstrings that state the mock-only limit: `harness.py` (lines 1-8), `runner.py`
  (`run_cases`, lines 478-482, the `HARNESS_CHECKS` comment, and the `exit_code` docstring).

### Files touched

| File | Change |
|---|---|
| `scripts/factory_core/capability_suite/relay.py` | Create |
| `scripts/factory_core/capability_suite/harness.py` | Modify — `scrubbed_env`, `start_cli`/`run_cli`, `run_relayed`, constants, four checks, three judges, the SIGINT attempt verdict |
| `scripts/factory_core/capability_suite/runner.py` | Modify — `_CASE_KEYS`, `_validate()`, `HARNESS_RELAY_CHECKS`, `run_cases()`, `_harness_probe()`, `Summary.harness_probe_sources`, `main()` |
| `scripts/factory_core/capability_suite/emit.py` | Modify — `SCHEMA_VERSION`, `harness_sources`, `harness_probes` |
| `scripts/factory_core/capability_suite/fixtures/…` | Create — the ten `case.yaml` files above |
| `scripts/factory_core/providers/model_endpoint.py` | Modify — `PINNED_FIXTURE_SUITE_VERSION` value only |
| `tests/test_capability_suite.py` | Modify — as listed |
| `docs/configuration.md`, `docs/features.md` | Modify |

Not touched: `mock_server.py`, `.github/workflows/ci.yml`, `Dockerfile`, `scheduler.sh`,
`entrypoint.sh`, `deploy/**`, `.github/workflows/publish.yml`, `config/`, any `gate_*`, the
breaker, budgets, `model_proxy.py`, `model_adapter.py`, `role_isolation.py`,
`_NO_LOCAL_DISPATCH`, any registry, alias or routing surface.

## Prototype evidence

Run on 2026-11-26 in the factory image (suite modules byte-identical to `main` at `1628a6e`),
`--network none`, CLI 2.1.282, the in-process mock as upstream.

| What | Observed |
|---|---|
| Baseline hermetic Tier 2 run | exit 0, 6 s |
| CLI against an unarmed mock | 5,452 requests in 60 s, killed |
| Alias probe through the relay, mock armed in process | `pass:ok`, relay dialled only the mock |
| Alias control, `wrong_model` injected by the relay | `fail:opus_response_model_mismatch` |
| Legacy `judge_alias` under `wrong_model` | `pass:ok` (why the response model is recorded) |
| Tool probe through the relay, `--tools Read`, id from the request | `pass:ok` |
| Tool control, `unplanted_file` | `fail:planted_not_carried` |
| Context probe, 4,096 words | `pass:ok`, status 200 |
| Context control, `over_limit` injected by the relay | `fail:not_answered_200`, status 400 |
| `padded_prompt(30000)` (120,064 bytes) | spawns, block arrives |
| `padded_prompt(32768)` (131,136 bytes) | `OSError: Argument list too long` |
| SIGINT, stalled stream | `pass:ok`; `client_gone`, `upstream_closed`, mock saw the hang-up |
| SIGINT, stream finishing normally | `not_tested:stream_finished_before_sigint`; `client_gone` false |
| SIGINT control, `no_sigint` | `fail:connection_not_closed` (flags snapshotted before reaping) |
| Upstream answers 307 to a second listener | 307 handed back, second listener 0 requests |
| Refused upstream | recorded and stopped in 0.5 s |
| Proxy sink over all relay runs | no connection |

Second review's measurements (its own scripts): a spawn killed at its bound, judged anyway,
read as `opus_response_model_mismatch`, `read_not_called` and `not_answered_200` — hence the
`cli_timeout` rule.

Not prototyped: any real endpoint; a real model's tool choices; the `cli_timeout` rule itself;
the id-set and relative-path rules of the tool judge; the `data:`-line `completed` match;
dropping `Location`; `harness_probes`; `Retry-After` pass-through; the
catch-all 404 route; the 502 body's effect on the CLI beyond the short-circuit (the check kills
the process as soon as the failure is recorded).

## Alternatives Considered

1. **Judge from the CLI's stdout and exit code alone, no relay.** Rejected as the verdict: it
   cannot tell "the endpoint answered correctly" from "the CLI mis-assembled the request and
   the endpoint answered anyway", and it cannot observe a hang-up. Exit status is one input to
   the alias judge only.
2. **Swap the credential at the relay** (CLI holds the dummy, relay substitutes the secret).
   Rejected: a new secret-handling surface, and the credential check could no longer see what
   the CLI itself sent.
3. **Leave `timeout_cancellation` permanently `not_tested` on a real endpoint.** Rejected: it
   has no other endpoint probe, so the dimension could never certify; the relay's first-chunk
   trigger removes most of the race and the residue is an explicit `not_tested`.
4. **Skip the legacy mock cases in endpoint mode.** This was the first draft. Rejected: the
   fixed observations would point at a mock check absent from the same result, and #624's design
   was that every suite run exercises the harness mechanism against the mock.
5. **One case per dimension reporting both halves.** Rejected: one case yields one outcome; the
   unjudged half would need a side channel or lose its "never `pass`" guarantee.
6. **Sabotaged-input controls for all four judges** (first draft). Rejected where a mock fault
   exists (`wrong_model`, `over_limit`); kept as a `variant` only where none can.
7. **Scope `Read` with `--allowedTools "Read(<path>)"`.** Not needed: omitting `--allowedTools`
   already confines `Read` to the working directory.
8. **Feed the padded prompt on stdin to exceed the argv limit.** Rejected for this ticket: every
   spawn's stdin is `/dev/null` by design (a test asserts it), and window acceptance is already
   part A's `sized_request`.

## Refinement notes

Generated by the refine pipeline's own question-and-answer pass and by the review that
followed. **None of this is an owner decision**; the owner's decisions are the issue body.

- The relay records in the mock's observation shape so the judges reason the same way over
  real traffic.
- The two unjudged halves are observations because a probe that can only say `not_tested`
  would block its dimension from `pass` for ever under the worst-of rule.
- The result marks each harness verdict's source in a machine-readable field because #562's
  operator copies from this output by hand.
- The SIGINT case retries only "the stream finished first"; any `fail` is final.

## Assumptions

- Read on `main` at `1628a6e`: `runner.py`, `harness.py`, `mock_server.py`, `emit.py`,
  `model_endpoint.py`, `model_adapter.classify_failure`, `tests/test_capability_suite.py`, and
  the #561/#624 specs under `docs/archive/`. Re-verify line numbers if `main` has moved.
- #628 is merged on `main` (`34f7e45`, PR #632); this ticket's re-pin is computed after it.
- "#562 should depend on this ticket" is an issue-tracker edit, not part of the implementation.
- The pinned CLI sends `x-api-key` and no `authorization` header, and always streams (observed
  again in the prototype).
- A real endpoint under test echoes its served name in `message_start.message.model`. Part A's
  HTTP probes already require this: a different model is refused by
  `model_adapter.check_response_model`, and an absent one is `model_absent` in
  `runner._checked` (`runner.py:210-211`). An endpoint that does not echo it would already fail
  `protocol_compatibility` and `streaming`.
- The filler word tokenises to roughly one token on the endpoint under test. The context case
  uses at most half the declared window, so a 2x error still fits.
