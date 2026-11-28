# Dark Factory Model Proxy — Rollout, Rollback, and Caveats

Issue: #208. Spec: `docs/archive/2026-07-11-transparent-model-proxy-design.md`.

## What this is

`factory-model-proxy` is an opt-in reverse proxy between Claude Code (inside a dispatched
run container) and `api.anthropic.com`. When enabled, it forwards every request unchanged
and writes a compact per-request ledger row to
`/var/lib/dark-factory/request-ledger.jsonl` (and, optionally, raw request/response
artifacts). Disabled by default — zero behavior change from today.

## Enabling it

Two env files are involved, and each setting has to go in the right one. The scheduler reads
`deploy/instance.env`; dispatched run containers read `<PROJECT_DIR>/.archon/.env` (the file
`run-compose.yml`'s `dark-factory` service loads via `env_file:`) and never read
`instance.env`.

**1. Scheduler side — `deploy/instance.env`** (or the instance's own env file):

```
FACTORY_MODEL_PROXY_ENABLED=true
```

`scheduler.sh`'s `dispatch()` reads `FACTORY_MODEL_PROXY_ENABLED` from the scheduler's own
environment and adds `--profile factory-model-proxy` to its `docker compose run` invocation,
which brings the `factory-model-proxy` service (defined in `run-compose.yml`) online as an
optional dependency of the `dark-factory` run container. Setting this variable in
`.archon/.env` does nothing: the scheduler copies that file for run containers but never
sources it.

Optionally, in the same file:

```
RAW_ARTIFACT_CAPTURE=true
RAW_ARTIFACT_RETENTION_DAYS=7
```

The proxy service has no `env_file`; `run-compose.yml` interpolates these two from the
environment of the process that runs `docker compose`, which is the scheduler.

**2. Run side — `<PROJECT_DIR>/.archon/.env`:**

```
ANTHROPIC_BASE_URL=http://factory-model-proxy:8787
```

This points Claude Code inside the run container at the proxy.

**3. Recreate the scheduler** so it picks up `instance.env` and re-copies `.archon/.env` for
dispatched runs (it copies that file once, at startup):

```
docker compose --env-file deploy/instance.env -f deploy/docker-compose.yml up -d --force-recreate backlog-scheduler
```

Set both sides together. With `ANTHROPIC_BASE_URL` set but the scheduler flag missing, runs
are pointed at a proxy that is never started and their model calls fail.

## Why generic passthrough, not a `/v1/messages`-only allowlist

Claude Code's tool-search (deferred tool loading) mechanism issues Anthropic API calls
beyond a single hardcoded `/v1/messages` shape (e.g. token-count/tool-resolution calls on
the same base URL). A proxy restricted to one literal route risks silently breaking those
calls the moment Claude Code's harness adds or uses a different path — manifesting as tools
failing to resolve mid-session with no clear proxy-side error. `factory-model-proxy`
forwards the **full path space** under `ANTHROPIC_BASE_URL`, not an allowlisted route, to
avoid this failure mode.

## Rolling back

Remove `ANTHROPIC_BASE_URL` from `<PROJECT_DIR>/.archon/.env` and
`FACTORY_MODEL_PROXY_ENABLED` (plus any `RAW_ARTIFACT_*` lines) from `deploy/instance.env`,
then recreate the scheduler with the command above. Runs dispatched after that talk to
`api.anthropic.com` directly — no other state depends on the proxy being present; nothing
else needs to be reverted or cleaned up. Remove `ANTHROPIC_BASE_URL` first, or at the same
time: leaving it set after the proxy is disabled breaks runs.

## Known limitations

- **Correlation is best-effort and single-run-accurate.** `entrypoint.sh` writes
  `/var/lib/dark-factory/current-run.json` (`run_id`/`issue_number`/`intent`/`stage`, plus
  `role` when `FACTORY_DISPATCH_ROLE` is set) once
  per dispatch; the proxy re-reads it per-request. Under the default `FACTORY_WIP_LIMIT=1`
  this is exact. Under concurrent dispatches (`FACTORY_WIP_LIMIT>1`), a request may be
  attributed to whichever run most recently wrote the pointer file — Archon has no
  per-node `env:` key to carry a true per-request header, so this file-based approach is
  the least-infrastructure option that still satisfies the ledger's correlation
  requirement for the documented default deployment.
- **`stage` is exact for single-phase intents, `"unknown"` for multi-phase intents;
  `persona` is always `"unknown"`.** `refine`/`plan`/`deconflict`/`close`/`fix-main`/
  `recheck` runs are single-phase (the whole container run *is* that phase), so every
  request in those runs gets the correct `stage` for free. `fix`/`continue` runs traverse
  implement → conformance → code-review → merge inside one container run; placing a
  request within that traversal needs per-node start timestamps that neither
  `archon workflow get --json` nor `archon workflow runs --json` expose today (confirmed
  by live investigation against this repo's own `archon-dark-factory` workflow during
  planning) — `stage` stays `"unknown"` for those two intents. Sub-phase `persona`
  (distinguishing a subagent from its orchestrator within one node) is a strictly harder
  version of the same gap and is always `"unknown"`. Follow-up: file against parent epic
  #202 once Archon exposes per-node timing, or once the full reference gateway (spec #203
  §7.4, gated on #250) lands with real per-persona headers.
- **Streamed-response token counts are best-effort.** Non-streaming responses are fully
  parsed for `usage`; SSE responses are scanned for `"input_tokens"`/`"output_tokens"`
  substrings after the stream completes. The stream is forwarded to the client unblocked
  while the proxy keeps a copy of the bytes for that scan. The scan is robust to the current
  Anthropic streaming format but not schema-guaranteed.
- **The AC8 smoke test does not make a live Anthropic call.** It runs the real
  `factory_core.model_proxy` module (actual `create_app`/`handle_proxy` code) end-to-end over
  real HTTP, with only the upstream `api.anthropic.com` endpoint stubbed — a literal "real
  factory command with a live model call" smoke test would need committed live credentials in
  CI, which this repo does not do for any other test. This is a deliberate, documented
  substitution, not an unnoticed gap.
- **`retry_count` is always `0`.** The proxy is a single-pass forwarder and never retries
  itself; Claude-Code-level retries/fallbacks surface as separate, correlated ledger rows
  (same `run_id`/`issue_number`, adjacent timestamps) rather than a per-row counter. Aggregate
  by `(run_id, model, close timestamp window)` to reconstruct retry behavior.

## Local destination: supported and uncertified modes (#558)

Spec: `docs/archive/2026-11-25-hybrid-runtime-adapter-design.md`. Nothing here is
reachable in production yet. #557's `_NO_LOCAL_DISPATCH` rejects every local selection except
one made for the `classify-preview-helper` intent (#633), and only the #587 helper lane
(`scripts/preview_helper_dispatch.sh`, off by default) dispatches that intent. `factory-model-proxy` never receives the variables a local selection needs: the
helper lane gets its own proxy service, described next. The adapter is built and hermetically
tested for the #587 lane.

**The helper lane's proxy (#634).** `run-compose.yml`'s `preview-helper-proxy` service, under
its own `preview-helper-proxy` profile, runs this module with the literal argument
`--intent classify-preview-helper` in its `entrypoint:`. It is the only path from the helper
container to the local endpoint. Only the #587 lane (`scripts/preview_helper_dispatch.sh`, off by default)
starts it: it starts it for each helper dispatch, removes it when the helper exits, and refuses a dispatch
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
on `dark-factory-net` can reach it by name. It is also on `preview-helper-net` (#642), an
`internal: true` network whose only other member is the `preview-helper` container, which
has no other network and so no route out.

When `FACTORY_MODEL_ENDPOINT` is set, the proxy verifies the staged registry once at startup.
It recomputes the SHA-256 against `FACTORY_MODEL_REGISTRY_SHA256` and never re-reads the
registry afterwards. Any problem exits non-zero before the server binds. For a
`hosting_location: local` entry:

- **Destination.** Requests go only to the entry's verified `base_url`, never to the cloud
  default. A `MODEL_PROXY_UPSTREAM` that differs from `base_url` is a startup failure.
  Redirects are never followed: a 3xx is refused `502` `protocol_incompatibility`.
- **Credentials.** Inbound `authorization`, `x-api-key` and `api-key` headers are dropped. The
  outbound `x-api-key` comes only from the variable named by the entry's `secret_reference`.
  Any cloud model credential in the proxy's environment is a startup failure.
- **Accepted endpoint.** Only `POST /v1/messages` is accepted. A query string such as
  `?beta=true` is allowed. Any other path or method, including `POST /v1/messages/count_tokens`,
  is refused `400` `protocol_incompatibility` and never forwarded.
- **Supported.** Single-turn and multi-turn requests, tool-result continuation turns, and
  streaming (`text/event-stream`) and non-streaming responses.
- **Refused** with `400` `protocol_incompatibility`, before any outbound call:
  - forced tool choice: `tool_choice.type` of `any` or `tool`;
  - deferred tools: any `tools` entry carrying `defer_loading`;
  - structured output: `output_config` or `output_format` present.
- **Model identity.** A `model` other than the entry's `model_identity.served_name`, including
  any cloud model ID, is refused `403` `destination_policy_denial`. It is never remapped. The
  response is checked too (#559): a JSON body's `model`, or a stream's `message_start`
  `message.model`, that names a different model (a backend fallback or a swapped model) is
  refused `502` `protocol_incompatibility` — before the response starts for a non-streaming
  body, by aborting the stream for a streamed one. An absent `model` field passes.
- **Dispatch role (#559).** The role comes from the `role` field `entrypoint.sh` writes into
  `current-run.json` from `FACTORY_DISPATCH_ROLE`, never from the model or an inbound
  `X-Factory-Correlation` header. A local entry records `actor`, whether the role is declared
  or absent. A declared `checker` or `monitor` is refused `403` `destination_policy_denial`
  (`nested_checker_refused`) before any outbound call: a local dispatch never hosts an
  independent checker. This refusal is how nested checkers are disabled. Any role outside
  `actor`/`checker`/`monitor` is refused (`role_invalid`) on every selected entry, cloud
  included. A selected cloud entry records the declared role, or `null` when none is
  declared, so selecting an endpoint never makes a dispatch a checker by itself. With
  `FACTORY_MODEL_ENDPOINT` unset, the role is never read.
- **Responses.** A non-streaming 2xx body that is not a JSON object is refused `502`
  `protocol_incompatibility`. A streamed response that ends without a complete `message_stop`
  frame is aborted. Every failure after the response has started streaming aborts the
  connection without a clean end, so the client sees a truncated stream, never one that looks
  complete. A local `502`/`503`/`504` (not ready) is forwarded unchanged and recorded as
  `endpoint_unreachable`. Nothing is retried against this or any other endpoint. Only `gzip`
  and `deflate` response encodings are decoded for these checks: a local endpoint answering
  with another `Content-Encoding` (for example `br` or `zstd`) is treated as malformed.
- **Timeouts.** The entry's `connect_seconds` and `total_seconds` are aiohttp `ClientTimeout`
  bounds. `time_to_first_token_seconds` bounds the wait for the first body byte, and
  `idle_seconds` bounds every later wait between chunks. Both share one rolling deadline.
  aiohttp's `sock_read` is not used on this path: it also runs before the first byte, so it
  would cap time-to-first-token at the idle bound.
- **Failure classes** (contract §4.6; #238 adopted these names verbatim and declares their
  policy in `scripts/factory_core/failure_taxonomy.py`, which `model_adapter.py` re-exports):
  `endpoint_unreachable`, `timeout_connect`, `timeout_time_to_first_token`, `timeout_idle`,
  `timeout_total`, `cancellation`, `protocol_incompatibility`, `capability_evidence_invalid`
  (startup only) and `destination_policy_denial`. Refusals use the error envelope
  `{"error": {"type": "factory_model_adapter_refused", "message", "failure_class",
  "endpoint_id"}}`.
- **Telemetry.** Every ledger row carries `endpoint_id`, `hosting_location`, `role`,
  `request_id`, `failure_class`, `correlation`, `adapter_id` and `payload_decision`, and its
  `model` column is the backend model. On a refused request that column holds the model the
  client asked for, because nothing was forwarded. The outbound `X-Factory-Correlation` header is
  redacted from raw artifacts, which stay opt-in (`RAW_ARTIFACT_CAPTURE`) and bounded by
  retention. The run record gains `model_dispatch`, and a local-routed run records
  `cost_usd: null`, never 0.

Everything else about a local runtime is **uncertified** until #561/#562 ship capability
evidence. That includes Claude agent compatibility, subagents, prompt caching, extended
thinking, and any field not listed above. Compatibility with `/v1/chat/completions` is not
evidence of Claude agent compatibility, and this adapter does no protocol translation.

## Retention

- Ledger: size-rotated at 100 MB, 3 rotations kept, oldest deleted — bounded regardless of
  request volume.
- Raw artifacts (only written when `RAW_ARTIFACT_CAPTURE=true`): swept by mtime once, when
  the proxy process starts; there is no periodic sweep. Default retention 7 days
  (`RAW_ARTIFACT_RETENTION_DAYS`).
