# Opt-in Local Inference Deployment Profile and Operator Runbook

**Issue:** #560

## Overview / Problem Statement

#560 is the "Local deployment profile/runbook" slice of the accepted **Hybrid model
execution: cloud + local** contract (`docs/hybrid-execution-contract.md`, §4.1), which
assigns it exactly this scope: *"connectivity/resource controls, operator
documentation... owns the actual timeout/resource numbers Q3(c) defers; no production
default changes."*

Two upstream tickets are already merged on `main` and define the mechanism this ticket
documents, rather than extends:

- **#557** (endpoint registry, secrets, preflight) — `model-endpoints.yaml` schema,
  loaded/validated by `scripts/factory_core/providers/model_endpoint.py`, lives on the
  human-only `deploy/instances/<instance>/model-registry/` surface.
- **#558** (minimum runtime adapter) — `scripts/factory_core/providers/model_adapter.py`
  enforces credential stripping, protocol restrictions, and timeout/cancellation
  classification for any local destination, already hermetically tested in
  `tests/test_factory_model_proxy.py` and `tests/test_provider_model_adapter.py`.

Critically, **every local registry entry fails preflight today, unconditionally**:
`model_endpoint._NO_LOCAL_DISPATCH` ("no local-eligible dispatch exists yet... the first
helper dispatch is #587") fires for any `hosting_location: local` selection, because
every current DAG phase runs its checker in the same container (contract §4.11 decision
3). Lifting that is explicitly **#587's** job. This ticket does not touch
`model_adapter.py`, does not lift `_NO_LOCAL_DISPATCH`, and activates no routing — it
documents how an operator makes a *candidate* local endpoint reachable and registered,
with resource/connectivity guidance and a no-purchase validation path, entirely in
documentation and tests.

### The concrete target: an operator's LAN Ollama rig

The issue names a specific, already-probed candidate (operator pre-certification note,
#562 comment, 2026-11-25 — explicitly *not* certification, read-only checks made
directly against the endpoint, not through the factory proxy or harness):

- Ollama 0.40.18 on a LAN rig, Anthropic Messages API at `/v1/messages`; candidate served
  name `qwen3-coder-128k` (qwen3-coder 30B, 131072 context); no API key, plain HTTP, LAN
  only.
- Streaming and non-streaming both work; stream ends with a complete `message_stop`
  frame; response `model` echoes the requested id.
- `tool_choice: auto` — 8/8 correct tool calls. **Forced `tool_choice` is silently
  ignored** (200, plain text) — exactly the case `model_adapter.py` already refuses
  before any outbound call. `/v1/messages/count_tokens` → 404.
- Throughput (rig idle, single request): ~160 output tok/s; a 28.6k-token prompt
  ingested in 4.2s.
- Time to first token: 5.4s cold (model files in OS page cache), 0.02s warm; an
  **unmeasured** estimate of 10-20s after a full rig reboot. Idle eviction after 5
  minutes. No concurrency or memory-pressure data exists.
- The served name must stay stable — re-creating the Ollama model with different
  parameters invalidates any future capability-evidence record (#562).

This spec treats every number above as **measured-but-not-certified**: useful for a
documented, conservative default, never a stand-in for #562's certification.

## Requirements

Distilled from the issue's acceptance criteria and the brainstorming Q&A (full dialogue
in the Phase 6 issue comment):

1. Opt-in deployment/config validation and readiness tests pass; cloud-only deployment
   is unchanged (acceptance criterion 1, Q1).
2. Operator can configure same-host vs. LAN connectivity without editing workflow logic
   or embedding secrets in tracked files (acceptance criterion 2).
3. Resource budget covers weights, KV/context allocation, concurrency and runtime
   headroom, as a measurement methodology an operator runs on their own hardware — not a
   paper table (acceptance criterion 3, Q2).
4. Pin runtime/model/template/quant identities; document provenance/license checks;
   distinguish optional cloud-backed modes from real local weights (acceptance
   criterion 4).
5. Document OOM, restart, unreachable-endpoint, disk-pressure and overload outcomes and
   bounded queue/timeout behavior, reusing existing #558 test coverage and failure
   classes, adding only the few tests that are missing (acceptance criterion 5, Q3).
6. Provide enable/disable/rollback commands and a no-purchase test path; live activation
   stays gated behind the separate #562 operator certification (acceptance criterion 6).
7. Zero committed changes under `deploy/**` or to `run-compose.yml`; the deliverable is
   documentation + tests only (Q1).
8. Nothing from #561-#564 or #585-#587 (capability fixtures, certification run,
   economics comparison, canary, no-auto-merge end-gate, local-aware session-window
   detection, first helper dispatch) — those stay separate, already-filed tickets.

### Brainstorming Q&A

> **Q1 (deploy/** scope):** Given that `deploy/instances/**` is hard-excluded for a
> factory agent and any other `deploy/**` or `run-compose.yml` change requires its own
> owner-authorization section (the issue's own constraint #1 says "prefer a design that
> needs none"), should this ticket's "opt-in deployment/profile" deliverable be entirely
> documentation + tests with zero committed changes under `deploy/**`? Or is there a
> specific, narrow `deploy/**`/`run-compose.yml` change this ticket should propose
> (listed for explicit owner sign-off) rather than avoid?
>
> **A:** Documentation plus tests only — no committed `deploy/**`/`run-compose.yml`
> change, and none should be proposed for sign-off either. Any compose change today
> would be dead or out-of-scope: `run-compose.yml` hardcodes
> `MODEL_PROXY_UPSTREAM: https://api.anthropic.com`, and `_NO_LOCAL_DISPATCH` fails
> preflight for every local entry — wiring registry vars into the proxy is #587's
> explicit job, and doing it here either wires up something unused or starts #587's
> work. Reachability guidance belongs in the runbook as inline illustrative examples
> (placeholder LAN address, `host.docker.internal` for same-host) with an explicit note
> that these are applied by the operator on the human-only `deploy/instances/<instance>/`
> surface, never by the factory. Follow the existing flat-file doc convention (a new
> `docs/*.md` runbook shaped like `docs/dark-factory-model-proxy-rollout.md`), and put the
> recommended timeout numbers there. Tests: a new fixture under
> `tests/fixtures/model_registry/` for this LAN entry, a test that it validates but still
> fails preflight on `_NO_LOCAL_DISPATCH`, and a cloud-only-byte-identical test.

> **Q2 (resource-budget sizing numbers):** The acceptance criteria require measured
> sizing, not paper tables, but the only data is one informal, non-certifying probe with
> no concurrency or memory data. Should the runbook include a concrete worked
> memory-sizing example for this rig, or only a methodology?
>
> **A:** Methodology plus a worksheet, no worked memory-sizing example even with a
> disclaimer — the acceptance criterion itself rules a worked example out ("actual
> sizing requires measured hardware, not paper tables"), and nothing about memory was
> actually measured. Cite only what was measured (throughput, TTFT, idle eviction,
> configured context), attributed to the non-certifying probe. Structure the methodology
> as a measurement *procedure* (load the model at the registered context length, read
> resident size and GPU/CPU split via `ollama ps`, repeat under the planned concurrency)
> with a blank table for the operator to fill in; a general KV-cache formula may appear
> only as a rough planning upper bound, explicitly not a substitute for measuring.
> Default recommended concurrency is 1 (only single-request behavior has been observed,
> and this matches `LOCAL_ROLE = "actor"` — no parallel local checker). Context length is
> part of model identity: changing it re-creates the served model and invalidates any
> future capability evidence, so the operator picks it before fixing
> `digest_or_revision`.

> **Q3 (failure-outcome doc vs. test coverage):** Given `model_adapter.py`'s
> `FAILURE_CLASSES` already shipped in #558 and this ticket doesn't modify that file or
> lift `_NO_LOCAL_DISPATCH`, should the OOM/restart/unreachable/disk-pressure/overload
> acceptance criterion be satisfied by (a) a documentation table mapping each scenario to
> its existing failure class, or (b) new test coverage against a mock local server?
>
> **A:** Mostly (a), plus a small slice of (b) for genuine gaps — reusing the existing
> fake-upstream harness, never touching `model_adapter.py`. Most scenarios are already
> pinned: `tests/test_factory_model_proxy.py`'s `_local_scenario`/`_local_destination`
> harness covers endpoint-unreachable
> (`test_local_endpoint_unreachable_fails_closed_without_retrying_elsewhere`), a
> not-ready 503 (`test_local_not_ready_upstream_is_forwarded_and_classified`), mid-stream
> disconnect (`test_local_upstream_disconnect_mid_stream_is_aborted`), and the
> TTFT/idle bounds (from ~line 959). `tests/test_provider_model_adapter.py`'s
> `test_not_ready_statuses_are_endpoint_unreachable` pins that HTTP 500 maps to **no**
> failure class (`status_failure_class` only classifies 502/503/504) — a real, existing
> gap to document, not fix (it's #558's classification to extend and #238's eventual
> rename target). The runbook gets a scenario table citing the pinning test per row; a
> handful of new tests, added to `tests/test_factory_model_proxy.py` using
> `_local_scenario` with this ticket's own LAN timeout values (not the generic
> 10/120/60/1800 fixture defaults), fill the only real gaps: a cold start answering
> inside vs. past the TTFT bound, and a queue-full 503 with no retry/no cloud call.

### Operator review amendments (2026-11-25)

The Q&A above is the refinement record and is left as written. Where it differs from the
sections below, the sections below win. The differences:

- **A1, enable procedure.** A local id is never selected on a running instance before #587
  (see "Enable / disable / rollback"): the scheduler's boot preflight would fail and the
  scheduler would restart-loop.
- **A1, same-host.** `host.docker.internal` is not assumed to resolve (see "Connectivity").
- **A1, cloud-only test.** The default path is already pinned by three existing tests; this
  ticket adds one pytest on top of them (deliverable 5).
- **A3, timeout tests.** The new proxy tests use this ticket's timeout profile at scaled
  bounds, never a real 30-second wait (deliverable 4).
- Every address in the runbook, the spec and the fixture is a documentation-range
  placeholder (RFC 5737, `192.0.2.0/24`).

## Architecture / Approach

### Deliverables (all under already-permitted paths; zero `deploy/**` changes)

1. **`docs/local-inference-deployment-runbook.md`** (new, flat top-level file, following
   the `docs/dark-factory-model-proxy-rollout.md` shape: what this is → registering an
   entry → secret location → pinning identity → connectivity → persistent model storage →
   service isolation → registry write protection → timeout bounds → resource budget →
   readiness check → failure-mode table → enable/disable/rollback → caveats). Cross-links rather than duplicates:
   `docs/hybrid-execution-contract.md` (policy/invariants),
   `docs/configuration.md`'s "Model endpoint registry (#557)" section (schema, env vars,
   CLI), and `docs/dark-factory-model-proxy-rollout.md`'s "Local destination: supported
   and uncertified modes (#558)" section (protocol/failure-class behavior at the adapter
   level — not re-documented here).

   Contents:

   - **What this is.** An opt-in profile for registering a candidate local inference
     endpoint so an operator can validate connectivity and resource fit *before* #562's
     certification and #587's first helper dispatch exist. It changes no default
     behavior: with no registry entry selected (`FACTORY_MODEL_ENDPOINT` unset, today's
     default), nothing in this runbook is read, staged, or checked.
   - **Registering an entry.** A worked `model-endpoints.yaml` entry for the LAN Ollama
     case, to be placed by the operator under the human-only
     `deploy/instances/<instance>/model-registry/model-endpoints.yaml` (never committed
     by the factory):

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
           time_to_first_token_seconds: 30   # see "Timeout bounds" below
           idle_seconds: 60
           total_seconds: 1800
     ```

     The digest and the address are placeholders, shown in a form the schema accepts so
     the runbook example and the test fixture (deliverable 2) are the same text. The
     operator substitutes their own values in their own instance config; neither the real
     address nor the real digest is committed to the repository.
   - **Secret location.** The schema requires a `secret_reference` on every entry: an
     env-var *name* matching `^[A-Z][A-Z0-9_]*$`, never a value, and for a local entry
     never one of `CLOUD_CREDENTIALS`. The runbook must say all of the following:
     - An endpoint with no API key still needs the name. The operator sets the named
       variable (`LOCAL_OLLAMA_DUMMY_TOKEN` in the example) to any non-empty dummy value.
       A dummy value is not authentication and protects nothing.
     - Where it goes today: the scheduler's own `instance.env` (or the one-off validation
       container's `-e`), where its only effect is the `present: true` flag printed by
       `model-endpoints show --from-source`. It is not in `PROVIDER_PASSTHROUGH_VARS`, so
       it is not forwarded to run containers.
     - Where it must not go: `.archon/.env` (a local secret is never delivered that way,
       `docs/configuration.md` "Model endpoint registry"), any tracked file, or the
       registry file itself.
     - What is not possible yet: the proxy sends this value as the local request's only
       `x-api-key`, but the proxy service receives none of the endpoint variables today.
       Delivering them is #587's, and the runbook says so instead of describing a step.
     Verified by review: the runbook names `instance.env` as the only location, names the
     three places the value must not go, and contains no secret value.
   - **Pinning identity.** The runbook must say:
     - `served_name` is the exact model name the endpoint serves (`ollama list`) and the
       name every request carries; the adapter refuses any other `model`. The schema does
       not check that a served name is immutable — only `digest_or_revision` rejects
       `latest`-style aliases — and an untagged Ollama name resolves to its implicit
       `latest` tag. Keeping the name stable is therefore an operator obligation: never
       re-create the served model under the same name with different parameters. Doing so
       invalidates any later capability evidence (#562).
     - `digest_or_revision` is the full `sha256:` digest of the served model, read from
       the endpoint's model listing (`GET <base_url>/api/tags`, the `digest` of the
       matching entry). `ollama list` prints only its short form, and
       `ollama show <name> --modelfile` prints no digest. Re-pulling, re-quantizing or
       re-creating the model changes it.
     - Runtime, template and quantisation are pinned by recording them beside the entry in
       the operator's own instance notes, because the registry schema has no field for
       them: the runtime version (`ollama --version`), and the chat template,
       quantisation, parameter size and context length (`ollama show <name>`). The
       capability-evidence record that carries these fields (`runtime_stack`,
       `quantisation`) is #562's; this runbook never creates one.
     - Context length is part of the identity: it is chosen before the digest is recorded,
       because changing it re-creates the served model.
     - Provenance and licence: record the upstream licence and the distribution source
       (the runtime's library page or the upstream model card) in the same notes.
     Verified by review: each of the five items above is present, with the command that
     produces the value.
   - **Distinguishing modes.** This profile is *real local weights* served by Ollama on
     operator hardware — not a cloud-backed "local-shaped" proxy to a hosted model. A
     cloud-backed stand-in (e.g. a hosted OpenAI-compatible endpoint presented as
     "local") does not satisfy `hosting_location: local`'s intent and must not be
     registered as one; the registry's `hosting_location` field is the only source of
     truth, and this runbook's examples are the genuine-local case.
   - **Connectivity (same-host vs. LAN).** No workflow-logic change either way: both are
     expressed purely as `base_url`. The runbook must say:
     - Which containers must reach the endpoint: the run container and the
       `factory-model-proxy` container. Both attach to the instance's
       `<instance>_dark-factory-net` bridge network (`run-compose.yml`), so one check from
       that network covers both (see "Readiness / validation").
     - LAN: `base_url` is the rig's LAN address (the `local-lan` fixture shape, shown here
       as `http://192.0.2.10:11434`). Containers on a bridge network reach the LAN through
       the Docker host's own route; no new Docker network is introduced.
     - Same-host: a container's `localhost` is the container, never the Docker host.
       `http://host.docker.internal:<port>` (the `local-same-host` fixture shape) resolves
       only where the Docker runtime provides that name (Docker Desktop). On a Linux
       Docker Engine it does not resolve without an `extra_hosts` entry on the service,
       which would be a `run-compose.yml` change: this ticket makes none and the runbook
       must not suggest one. There the operator uses the host's address on the instance
       bridge network (the gateway shown by `docker network inspect
       <instance>_dark-factory-net`) or the host's LAN address as `base_url`.
     - The model service must listen on an interface the containers can reach. A service
       bound to the host's loopback only is not reachable from a container.
     - `base_url_scope` in `model-endpoints show` is informational only; a bridge or LAN
       address prints as `lan`.
     Verified by review: the runbook states that container `localhost` is not the host,
     gives a same-host route that needs no compose change, and contains no address outside
     `192.0.2.0/24`.
   - **Persistent model storage.** The runbook must say:
     - Model weights live in the runtime's own model store, at a location the operator
       chooses (for Ollama, the directory named by `OLLAMA_MODELS`). The runbook names the
       setting, never a path.
     - The store must be on storage that survives a service restart, a host reboot and,
       when the runtime itself runs in a container, the re-creation of that container (a
       named volume or bind mount owned by the model service, never one of the factory's
       volumes).
     - The store's size and the free space left on its filesystem are measured values in
       the resource worksheet, with headroom for one further pull of the same model.
     - After restoring or re-pulling the store, the operator re-reads the digest and
       compares it with the registry entry before relying on the entry again.
     - A full disk is covered in the failure-mode table (disk pressure).
     Verified by review: the section exists, names no host path, and the worksheet has
     rows for store size and free space.
   - **Service isolation and network boundary.** The runbook must say:
     - The model service runs with no mount of any target repository or factory volume, no
       GitHub credential, no cloud model credential and no Docker socket. If it runs in a
       container, that container is not on a factory compose project and shares none of
       the factory's volumes.
     - Bind scope is explicit: the service listens only on the interface the factory
       containers use to reach it, never on every interface of a host that also has a
       routable or public address.
     - Allowed client networks are explicit: a host firewall rule (or a reverse proxy in
       front of the service) admits only the Docker host running the factory. The runbook
       gives the rule as a description with placeholder addresses, not as a command tied
       to one firewall product.
     - The model API is unauthenticated. The dummy `secret_reference` value sent as
       `x-api-key` is not authentication, so the network boundary is the only access
       control, and the service must not be exposed beyond it.
     - Transport is plain http: prompts, repository content and model output cross the
       network unencrypted. The contract sets no transport requirement
       (`docs/hybrid-execution-contract.md` is silent on it), so this is stated as an
       accepted property of a LAN-only endpoint, and the runbook says an endpoint reached
       over any network the operator does not control needs TLS in front of it
       (`base_url` accepts `https`).
     Verified by review: each of the five statements above is present, and the runbook
     does not describe a placeholder key as authentication anywhere.
   - **Registry write protection.** Contract §4.5 makes "no scheduler-dispatched container
     may be able to write [a capability-evidence record]" a mechanism for #557 and #560 to
     specify. #557 supplied the mechanism; the runbook states the operator's part:
     - The registry directory lives on the Docker host under the operator's control and is
       mounted into the scheduler read-only (`deploy/docker-compose.yml`,
       `${FACTORY_MODEL_REGISTRY_DIR:-./model-registry.empty}` at
       `/etc/dark-factory/model-registry:ro`). It is never mounted into a run container.
     - Run containers see only the staged copy on the shared state volume, which is
       trusted only against the SHA-256 of the read-only source that the scheduler passes
       per dispatch (#593). A run container that edits the staged copy fails its own
       preflight; it cannot change the source.
     - The operator keeps the host directory writable only by the operator account, and
       never points `FACTORY_MODEL_REGISTRY_DIR` at a directory inside a run's workspace
       or on the shared state volume.
     - No `capability-evidence/<endpoint_id>.yaml` is created by this runbook. Only an
       operator acting on #562's certification output writes one; until then the entry's
       evidence state is `unknown`, which is expected.
     Verified by review: the four statements above are present, and the runbook contains
     no capability-evidence example for the new entry.
   - **Timeout bounds.** Recommended starting values for this probe's rig, stated as
     recommendations an operator should re-measure, not certified numbers:
     `connect_seconds: 10` (LAN-local, generous), `time_to_first_token_seconds: 30`
     (covers the measured 5.4s cold/0.02s warm plus the unmeasured 10-20s
     post-reboot estimate, with headroom), `idle_seconds: 60`, `total_seconds: 1800`
     (matching the existing fixture defaults, since no long-multi-turn data exists yet).
     Note the 5-minute idle-eviction window separately — it governs *warm-up*
     cadence, not the request timeout fields. Two caveats the runbook must state:
     `time_to_first_token_seconds` bounds the wait for the first response *body byte*, so
     a non-streaming request whose whole generation takes longer than the bound times out
     as `timeout_time_to_first_token` even on a warm model; and a cold start can take
     longer than measured when another model on the rig has to be evicted first.
   - **Resource budget — methodology, not a paper table.** Per Q2: list the budget
     components an operator must measure on their own hardware — model weights
     (resident size at load), KV/context cache at the *registered* context length,
     a concurrency multiplier (default to 1 — see below), and runtime/OS headroom.
     Procedure: load the model at the exact context length being registered; read
     resident memory and the GPU/CPU split via `ollama ps`; repeat under the concurrency
     actually planned. A blank worksheet table for the operator to fill in. A general
     KV-cache-size formula may appear only as a rough upper bound for planning purposes,
     explicitly labeled "not a substitute for measuring." Only measured facts from the
     probe are cited as data (throughput ~160 tok/s idle, TTFT 5.4s cold/0.02s warm,
     5-minute idle eviction, 131072 configured context) — explicitly marked "not
     measured: memory footprint, concurrency behavior; certification (#562) owns
     certified figures." **Concurrency defaults to 1** until the operator measures
     otherwise, consistent with `LOCAL_ROLE = "actor"` (no parallel local checker).
   - **Readiness / validation.** Three steps, all with code already on `main`. None of
     them selects the endpoint on a running scheduler.
     1. *Entry parses, secret name is set.* In the scheduler container, with the registry
        directory mounted (see "Enable / disable / rollback"):
        `python3 /opt/dark-factory/scripts/factory_core/providers/cli.py model-endpoints show --from-source`.
        It prints the redacted effective config: the entry, `base_url_scope`, and the
        `secret_reference` as its name and `present: true|false`, never its value.
     2. *Preflight boundary.* In a one-off container, never the running scheduler, in the
        same form as the offline check `docs/configuration.md` already documents:

        ```
        docker run --rm --env-file <instance.env> \
          -e FACTORY_MODEL_ENDPOINT=lan-ollama-qwen3-coder \
          -v <registry dir>:/etc/dark-factory/model-registry:ro \
          --entrypoint python3 <factory image> \
          /opt/dark-factory/scripts/factory_core/providers/cli.py preflight --side scheduler
        ```

        Expected: exit 1 and, for the model endpoint, exactly one line — the
        `_NO_LOCAL_DISPATCH` message. That is the correct outcome until #587. It shows the
        registry is valid and the id resolves; it dials nothing, so it says nothing about
        reachability.
     3. *Reachability, from where the factory's containers sit.* From a throwaway container
        attached to `<instance>_dark-factory-net` (any image that provides `curl`), which
        is the network the run and proxy containers share:
        - a non-streaming `POST <base_url>/v1/messages` with `model` set to the registered
          `served_name`: expect HTTP 200 and a response whose `model` echoes that name;
        - the same request with `"stream": true`: expect the stream to end with a
          `message_stop` event;
        - both again after the endpoint has been idle for longer than its eviction window
          (5 minutes on the probed rig), timing the first byte: the cold time to first
          token must sit inside `time_to_first_token_seconds` with headroom.
        These are operator steps against real hardware and are not certification (#562).
     Not available before #587, and the runbook must not list them as steps: the run-side
     `preflight` and `model-endpoints show` without `--from-source`. Both need the
     per-dispatch `FACTORY_MODEL_REGISTRY_SHA256` and fail on its absence when run by hand.
     Hermetic counterpart: deliverables 2 to 5 exercise the same schema, preflight boundary
     and timeout behaviour with no endpoint and no network.
   - **Failure modes and bounded queue/timeout behavior.** The scenario table from A3
     (reproduced here, each row citing its pinning test), plus: never retried, never
     falls back to cloud (this is `local-pinned` behavior per the contract, not something
     this ticket adds); the endpoint's own queue is bounded only by the
     `time_to_first_token_seconds`/`total_seconds` timeouts above, so an operator who
     wants real admission control must bound concurrency at the Ollama/OS level, since
     the adapter does not queue (concurrency recommendation: 1). The one known,
     documented-not-fixed gap: an HTTP 500 (e.g. an OOM or disk-pressure failure inside
     Ollama) is forwarded with **no** failure class (`status_failure_class` only
     classifies 502/503/504) — flagged as a caveat and a spillover-ticket candidate for
     #558/#238, not fixed here.
   - **Enable / disable / rollback.** No new mechanism; every command is one
     `docs/configuration.md` already documents.
     - **Enable** means making the registry available for validation, nothing more: set
       `FACTORY_MODEL_REGISTRY_DIR` (compose interpolation env, like `PROJECT_DIR`) to the
       host path of the instance's registry directory and recreate the scheduler with
       `docker compose -f deploy/docker-compose.yml up -d`. The runbook must say to use the
       command the instance was started with: an instance started with
       `--env-file deploy/instances/<instance>/instance.env` is recreated with that same
       `--env-file`, because without it compose acts on the default `dark-factory`
       project, not that instance. With `FACTORY_MODEL_ENDPOINT`
       unset nothing reads the mount, and dispatch argv and env are unchanged. Then run
       the three readiness steps above.
     - **Warning the runbook must carry, in these terms:** never set
       `FACTORY_MODEL_ENDPOINT` to a local id in a running instance's `instance.env`
       before #587 ships. `scheduler.sh` runs `preflight --side scheduler` at boot under
       `set -e`, a local selection always fails it (`_NO_LOCAL_DISPATCH`), and the
       scheduler service is `restart: unless-stopped`: the scheduler restart-loops and
       every dispatch stops, cloud dispatch included.
     - **Disable / rollback:** remove `FACTORY_MODEL_REGISTRY_DIR` from the interpolation
       env and recreate the scheduler with the same command, including its `--env-file`; the mount falls back
       to `deploy/model-registry.empty/`. Remove the dummy secret variable from
       `instance.env`. No staged copy exists to clean up, because nothing was ever staged.
     - **Recovery, if a local id was selected by mistake:** remove `FACTORY_MODEL_ENDPOINT`
       from `instance.env` and recreate the scheduler the same way; cloud-only behaviour is restored.
     - Live activation is not part of this runbook: it needs #562's certification and
       #587's first local-eligible dispatch, and the runbook says so.
   - **No-purchase test path.** The fixture + tests below validate the whole profile
     (schema, preflight boundary, timeout behavior) with no real hardware, using the
     existing fake-upstream harness.

2. **`tests/fixtures/model_registry/model-endpoints.yaml`** — add one new entry,
   `lan-ollama-qwen3-coder`, identical to the runbook's worked example:
   `base_url: http://192.0.2.10:11434`, the placeholder digest
   `sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef`,
   `secret_reference: LOCAL_OLLAMA_DUMMY_TOKEN` and timeouts 10 / 30 / 60 / 1800. It is
   appended after the existing `local-same-host`/`local-lan`/`local-remote`/
   `cloud-anthropic` entries, which are not edited. No capability-evidence fixture is
   added for it.

3. **`tests/test_provider_model_endpoint.py`** — two changes:
   - Update the id list asserted by
     `test_fixture_registry_loads_same_host_lan_remote_and_cloud`, which compares the
     fixture's ids to an exact list and would otherwise fail, to include the new id last.
   - One new test, modeled on `test_local_selection_always_fails_as_an_existing_phase`,
     asserting that the `lan-ollama-qwen3-coder` fixture entry (a) loads and validates
     through `load_registry()`, (b) makes `scheduler_preflight()` return exactly the
     `_NO_LOCAL_DISPATCH` message and hosting `local`, and (c) makes the run-side
     `preflight()` on a staged, digest-matched copy include that message — pinning "this
     profile activates no routing."

4. **`tests/test_factory_model_proxy.py`** — three new tests using `_local_scenario` with
   this ticket's timeout profile at scaled bounds: the registered 10 / 30 / 60 / 1800
   divided by 20, i.e. `connect_seconds: 0.5`, `time_to_first_token_seconds: 1.5`,
   `idle_seconds: 3`, `total_seconds: 90`. No test waits anywhere near a real 30 seconds;
   the plan may pick another factor as long as the four ratios hold and each test finishes
   in a few seconds. Per A3: a cold-start first byte arriving inside the time-to-first-token
   bound succeeds; one arriving past it is classified `timeout_time_to_first_token`; a
   queue-full `503` is classified `endpoint_unreachable` with no retry and no call to the
   cloud upstream. All three run against the existing in-process fake upstream: no real
   endpoint, no network.

5. **Cloud-only default test** — this ticket changes no production file, so cloud-only
   dispatch argv, env and compose are identical by construction. The default path is
   already pinned by `test_default_path_never_loads_the_registry_module`
   (`tests/test_provider_model_endpoint.py`),
   `test_unset_endpoint_serves_the_cloud_default_without_loading_the_adapter`
   (`tests/test_factory_model_proxy.py`) and the no-endpoint case of
   `tests/test_provider_env_flags.sh` (`model_registry_env_flags` adds nothing). Add one
   new pytest in `tests/test_provider_model_endpoint.py`, no shell test and so no CI
   workflow change: with the fixture registry (new entry included) copied to the staged
   path and `FACTORY_MODEL_ENDPOINT` unset, `providers.preflight()` and
   `providers.preflight(side="scheduler")` both return `[]`, and neither
   `factory_core.providers.model_endpoint` nor `factory_core.providers.model_adapter` is
   in `sys.modules` afterwards. It proves the new profile's presence changes nothing
   while unselected.

6. **Cross-reference** — add one line to `docs/configuration.md`'s existing "Model
   endpoint registry (#557)" section pointing at the new runbook.
   `docs/hybrid-execution-contract.md`'s §4.1 table row for #560 is already accurate as
   written and needs no edit (the contract is design history plus the living
   responsibility map; it does not need to cite the runbook's filename).

### Files touched (complete list)

| Path | Change |
|---|---|
| `docs/local-inference-deployment-runbook.md` | new |
| `docs/configuration.md` | one cross-reference line in "Model endpoint registry (#557)" |
| `tests/fixtures/model_registry/model-endpoints.yaml` | one appended entry |
| `tests/test_provider_model_endpoint.py` | one id list updated, two tests added |
| `tests/test_factory_model_proxy.py` | three tests added |

Nothing else. In particular no path under `deploy/**` (including `deploy/instances/**`),
`run-compose.yml`, `.github/workflows/*`, `scheduler.sh`, `entrypoint.sh`,
`config/config.yaml`, `scripts/provider_env.sh`,
`scripts/factory_core/providers/__init__.py` or `scripts/factory_core/model_proxy.py`
changes, and no path in `.factory/adapter.yaml`'s `hard_exclude_paths`. No owner
authorisation is requested, because no owner-gated path is touched.

### What this ticket explicitly does not do

- Does not lift `_NO_LOCAL_DISPATCH` or make any dispatch route locally (#587's job).
- Does not wire registry/endpoint env vars into `factory-model-proxy` in
  `run-compose.yml` (#587's job; that service's `MODEL_PROXY_UPSTREAM` stays hardcoded to
  `https://api.anthropic.com`).
- Does not touch `model_adapter.py`'s failure classification, including the documented
  HTTP-500 gap (#558/#238's to extend).
- Does not write anything under `deploy/instances/**` or `.github/workflows/publish.yml`,
  and asks for no authorisation to. Placing a registry file in an instance directory is
  the operator's own action on their own host, described by the runbook, never a commit.
- Does not select a local endpoint on a running instance, and tells the operator not to.
- Does not run or substitute for #562's formal capability certification.
- Does not widen the local cloud-credential guard (#594 R4's documented gap, a
  precondition of #587, not #560).

## Alternatives Considered

1. **Extend `docs/dark-factory-model-proxy-rollout.md` in place** instead of a new file.
   Rejected: that doc is #558's adapter-level rollout note (protocol/failure-class
   behavior), already cross-linked from here; folding in #560's deployment/resource
   content would conflate two tickets' ownership in one file and make future diffs to
   either harder to scope. A new, cross-linking file matches the existing
   `docs/dark-factory-memory-v2.md` / `docs/dark-factory-memory-contract.md` precedent of
   pairing a runbook with a stable design doc.
2. **Ship a worked memory-sizing table using model-card arithmetic** (weights + KV
   formula at 131072 context) so the resource-budget acceptance criterion has concrete
   numbers today. Rejected per Q2 — the acceptance criterion explicitly forbids "paper
   tables," and no memory data was actually measured by the #562 probe; a labeled
   example would still not be a measured number.
3. **Build a mock-local-server integration test suite from scratch** for every named
   failure scenario (OOM, restart, disk pressure, overload). Rejected per Q3 — it would
   duplicate #558's already-hermetic `tests/test_factory_model_proxy.py` coverage;
   instead, document via a table citing existing pinning tests and add only the few
   tests exercising this ticket's timeout profile at scaled bounds.
4. **Propose a narrow `run-compose.yml` change** to forward registry env vars to
   `factory-model-proxy` so this ticket's profile is "closer to real." Rejected per Q1 —
   it would either be dead code (since `_NO_LOCAL_DISPATCH` blocks every dispatch
   anyway) or would start #587's work under a different ticket number, and the issue's
   own constraint #1 asks for a design that needs no `deploy/**`/`run-compose.yml`
   change at all.

## Open Questions (non-blocking)

- Exact production timeout/resource numbers remain provisional until #562's formal
  certification runs on real hardware under load; this runbook's values are a
  documented starting point, not a certified default.
- Whether the HTTP-500-has-no-failure-class gap (identified in Q3/A3) should become its
  own spillover ticket against #558/#238, or wait for #238's taxonomy work generally —
  left to the operator to file if desired.
- Whether an operator ever wants true admission control (a request queue) rather than
  concurrency-limited-by-convention — out of scope; the contract names this as
  mechanism #238/#239 may own later, not #560.

## Assumptions (flagged)

- This checkout's state (`scripts/factory_core/providers/model_endpoint.py`,
  `model_adapter.py`, `run-compose.yml`, `docs/configuration.md`,
  `docs/dark-factory-model-proxy-rollout.md`, `docs/hybrid-execution-contract.md`,
  `tests/test_provider_model_endpoint.py`, `tests/test_factory_model_proxy.py`,
  `tests/test_provider_model_adapter.py`) was read live on 2026-11-25 on `main`
  (post-#593/#594) and is assumed representative; all file:line citations above were
  re-verified against the current checkout, not carried over from an older draft.
- #557's and #558's shipped behavior (schema, `_NO_LOCAL_DISPATCH`, `FAILURE_CLASSES`,
  the proxy's hardcoded cloud upstream) is treated as fixed and out of scope to change,
  per the issue's own constraint #2 and the contract's §4.1 ownership split.
- The #562 pre-certification probe's measured facts are treated as directionally useful
  but explicitly non-certifying, per its own stated caveat ("not through the factory
  proxy or the Claude Code harness, so this is not certification").
- "Operator" throughout means the human who controls the `deploy/instances/<instance>/`
  surface and the scheduler's env files — never a factory agent, which cannot write to
  that surface regardless of what this runbook documents.
