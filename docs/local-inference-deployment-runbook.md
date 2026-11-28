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
and resource fit before certification (#562) exists and before the first local-eligible dispatch
(#587's helper lane, off by default) is switched on.

It changes no default behaviour. With `FACTORY_MODEL_ENDPOINT` unset (the default), nothing in
this runbook is read, staged or checked, and dispatch argv and env are unchanged.

It routes nothing either. `_NO_LOCAL_DISPATCH` in
`scripts/factory_core/providers/model_endpoint.py` rejects every `hosting_location: local`
selection on the scheduler side. On the run side it rejects every dispatch intent except
`classify-preview-helper` (#633), and only the #587 helper lane dispatches that intent. That lane
selects its endpoint with `PREVIEW_HELPER_ENDPOINT`, never `FACTORY_MODEL_ENDPOINT`, so every
local `FACTORY_MODEL_ENDPOINT` selection fails preflight. `factory-model-proxy` receives none of the endpoint
variables. The helper lane's own `preview-helper-proxy` service interpolates them from the
scheduler's environment (#634), and only the #587 lane starts it. Live activation of that lane is
#564's canary (`docs/configuration.md`, "The helper lane (#587)"). It is not part of this runbook.

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
  value reaches only the `preview-helper-proxy` service, as `FACTORY_HELPER_MODEL_SECRET` in the
  scheduler's own environment (#634), and only the #587 lane (off by default) starts that service. The helper's
  entry must name `secret_reference: FACTORY_HELPER_MODEL_SECRET`. There is no step for it here.

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
observed, and the contract's v1 local role is the actor alone (`bind_dispatch_role()` in
`scripts/factory_core/providers/role_isolation.py` refuses any other role for a local dispatch),
with no parallel local checker.

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
   yet`. That is the correct outcome: the #587 lane selects its endpoint with
   `PREVIEW_HELPER_ENDPOINT`, not `FACTORY_MODEL_ENDPOINT`. It shows that the registry is valid and the id
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

Not available by hand (the #587 lane stages the registry for each of its own dispatches), so they are not steps here: the run-side `preflight`, and
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
extend (#238's taxonomy kept the adapter's class names and adds none for it). It is a candidate
spillover ticket.

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
> `instance.env` (the #587 lane uses `PREVIEW_HELPER_ENDPOINT` instead). `scheduler.sh` runs `preflight --side scheduler` at boot under
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

Live activation is not part of this runbook. It needs #562's certification and #564's canary of
the #587 helper lane.

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
- Selecting a local endpoint is refused by the scheduler, and by the run container for every
  intent except `classify-preview-helper` (#633), which only the #587 helper lane dispatches (off
  by default; `_NO_LOCAL_DISPATCH`).
- The run container's cloud-credential guard also covers the Bedrock, Vertex, Foundry and Mantle
  switches and AWS/GCP alternate credentials (#594, closed by #633; the full list is in
  [configuration.md](configuration.md) "Model endpoint registry"). A capability-evidence record
  must list `model_digest`, `harness_version` and `registry_config` in `invalidated_by`, and only
  names `evidence_state()` can resolve (those three and `fixture_suite_version`). Otherwise it is
  not `tested`.
- The probed rig ignores a forced `tool_choice` and has no `/v1/messages/count_tokens`. The adapter
  refuses both before any outbound call (see `docs/dark-factory-model-proxy-rollout.md`).
- HTTP `500` has no failure class (see "Failure modes").
- Transport is plain http and the API is unauthenticated: the network boundary above is the only
  protection.
