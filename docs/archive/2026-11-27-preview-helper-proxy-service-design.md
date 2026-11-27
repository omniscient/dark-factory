# A Dedicated Per-Dispatch `preview-helper-proxy` Service

**Issue:** #634

**Depends on:** #633 (open — spec already written on
`origin/refine/issue-633-feat-model---scope-the-local-dispatch-re`,
`docs/superpowers/specs/2026-11-27-scoped-local-dispatch-refusal-design.md`; this spec builds
on that ticket's §7 "Interface for #634 and #587" as a fixed contract, not something this
ticket redefines). `Depends on:` gates implementation dispatch, not refinement, per
`docs/triage-labels.md` convention — this spec can be written now.

**Status:** amended after an independent review of the refine draft (`b826e8e`). Rules marked
*verified* were exercised on a scratch copy of `scripts/` carrying #633's approved changes plus
this spec's, in the factory image with no network, and by `docker compose config` on a scratch
copy of `run-compose.yml`. No container was started and no real endpoint was contacted. A
second independent read (of `1c98e62`) is applied as well; where it corrected a claim of the
first review the text says so. File and line citations were re-checked against `main` at
`a8cba16`.

## Overview / Problem Statement

`scripts/factory_core/model_proxy.py` already knows how to resolve a `FACTORY_MODEL_ENDPOINT`
registry entry once at startup and forward a local-routed run's traffic only to that entry's
verified `base_url` (#558, #593). What it has never received is an actual local-eligible
dispatch to serve: `_NO_LOCAL_DISPATCH` (in `model_endpoint.py`) refuses every
`hosting_location: local` entry unconditionally today, at both the proxy's own startup
(`load_verified_entry()`) and the run container's preflight (`preflight()`).

#633 narrows that refusal to one named intent, `classify-preview-helper`, and threads an
`intent` parameter through `_local_problems()`, `preflight()` and `load_verified_entry()` to
carry it — but #633 explicitly leaves `model_adapter.resolve_local_destination()` untouched and
calls out that passing the intent to the proxy itself is this ticket's job. Three things still
block an actual proxied local dispatch, all already named in the issue:

1. `docker compose run -e` only reaches the `run` target, never a `depends_on` service — the
   existing `factory-model-proxy` (one per compose project, resolved once) receives none of
   this.
2. Routing a second, independent local destination through the shared `factory-model-proxy`
   would risk a later cloud run being served by a process still configured for a local
   endpoint.
3. Even with its own proxy, two run-side rules still assume an unproxied local run: `preflight()`
   requires the entry's `secret_reference` variable present in the run container, and requires
   `ANTHROPIC_BASE_URL` to equal the entry's `base_url` directly.

This ticket adds a new, dedicated `preview-helper-proxy` compose service that exists only for
the `classify-preview-helper` helper dispatch, holds that one entry's configuration and secret,
and is the only path from the helper container to the local endpoint; and it reconciles the two
run-side rules above for that one intent. It activates nothing by itself: nothing in `main`
dispatches `classify-preview-helper` (per #633, that intent is recognized but exits
non-zero — "not available" — until #587), and nothing starts `preview-helper-proxy` until
#587's dispatcher does. This is a security-labeled, gate-adjacent change, explicitly authorized
by the repo owner on 2026-11-26 (recorded on #587) as part of the approved Hybrid model
execution chain (#556–#564), scoped to exactly the service and the preflight/loader
reconciliation described below.

## Requirements

The numbered resolutions below came out of the refine pipeline's own question-and-answer pass
and the review that followed. **None of them is an owner decision.** The owner's decisions are
the issue body and the authorisation of 2026-11-26 recorded on #587; the two points this spec
could not settle inside that authorisation are recorded under "Owner decisions (recorded)".

1. **Intent token and plumbing are #633's, consumed as-is.** The fixed argument identifying
   the helper to the proxy's loader is the literal CLI pair `--intent classify-preview-helper`,
   written only in `preview-helper-proxy`'s own `entrypoint:` list in `run-compose.yml` — never
   interpolated, never an environment variable. See "Trust boundary and residual risk" for what
   that does and does not guarantee. `model_endpoint.LOCAL_ELIGIBLE_INTENTS`,
   `_local_problems(..., intent=None)`, `preflight(..., intent=None)` and
   `load_verified_entry(..., intent=None)` are #633's; this ticket does not redefine them.
2. **`model_adapter.py` gains the intent passthrough #633 left for it.**
   `resolve_local_destination(registry_path, endpoint_id, intent=None)` forwards `intent`
   straight to `model_endpoint.load_verified_entry(..., intent=intent)` and nothing else;
   `destination_from_entry()` is unchanged.
3. **`model_proxy.py` parses the fixed argument and never falls back to cloud.**
   `main(argv=None)` parses `argv` with `argparse` (`allow_abbrev=False`), one option
   `--intent` (default `None`). `argv=None` means **no arguments**, not `sys.argv`: the module's
   `if __name__ == "__main__":` block calls `main(sys.argv[1:])`. Three existing tests call
   `mp.main()` bare under pytest (`tests/test_factory_model_proxy.py:549,560,568`); a parser
   reading `sys.argv` there exits 2 (*verified*). When `--intent` is given:
   - `FACTORY_MODEL_ENDPOINT` must be set and non-empty, or `main()` exits non-zero before
     binding;
   - the resolved destination's `hosting_location` must be `local`, or `main()` exits non-zero
     before binding. Without this second rule a helper proxy whose endpoint variable names a
     **cloud** entry resolves cleanly and binds as a passthrough to the module's cloud default
     (`UPSTREAM`, `model_proxy.py:47`, used at `:423` whenever the destination has no
     `upstream`) — *verified* on the draft's rule alone.
   - the entry's `base_url` must not equal `HELPER_PROXY_BASE_URL` (compared without a
     trailing slash), or `main()` exits non-zero before binding: a proxy must never be
     configured to forward to its own address.
   "Given" means the option is present, whatever its value: `--intent ""` is given, takes
   the three rules above and is then refused by the loader because the empty string is not
   an eligible intent. A repeated `--intent` takes the last value (argparse's default). That
   is harmless here: the service definition is a literal list, and arguments appended by
   whoever invokes compose can only replace the value with one the loader refuses or with
   the same one.
   With no `--intent` argument behaviour is byte-identical to today: `factory-model-proxy` is
   unaffected and still refuses a local entry (*verified*: `_NO_LOCAL_DISPATCH`).
4. **Run-side reconciliation, for the helper intent only.** In `model_endpoint.py`:
   - a new constant `HELPER_PROXY_BASE_URL = "http://preview-helper-proxy:8787"`;
   - `_local_problems(..., intent=None, expected_base_url=None)`: the `ANTHROPIC_BASE_URL`
     comparison uses `expected_base_url` when given, else `entry["base_url"]` (unchanged for
     every existing caller). The comparison stays an exact string match; a trailing slash is a
     mismatch. When `expected_base_url` is given the problem text names the helper proxy
     address, not "the entry's base_url";
   - `preflight(registry_path, endpoint_id, intent=None)` passes
     `expected_base_url=HELPER_PROXY_BASE_URL` when `intent in LOCAL_ELIGIBLE_INTENTS`, and for
     that same condition inverts its secret check (the `elif not os.environ.get(ref)` branch of
     `preflight()`, not anything inside `_local_problems()`): the entry's `secret_reference`
     variable must be absent. "Absent" means unset or empty, the same truthiness every other
     presence check in the module uses;
   - `load_verified_entry(registry_path, endpoint_id, intent=None, expected_base_url=None)`
     forwards `expected_base_url` to `_local_problems()` and does nothing else with it. It has
     two callers with two different environments, so the caller says which one it is in:
     - the **proxy** (`model_adapter.resolve_local_destination()`) never passes it, so the
       proxy's own `ANTHROPIC_BASE_URL` must equal the entry's `base_url`, as today;
     - the **run container's** hosting resolution (`providers/cli.py`
       `_model_endpoints_hosting`, called from `entrypoint.sh` with `--intent` per #633) passes
       `HELPER_PROXY_BASE_URL` when its `--intent` is in `LOCAL_ELIGIBLE_INTENTS`, and passes
       no `expected_base_url` argument at all for any other intent, so the call it makes for
       every existing intent is exactly the one #633 specifies. Without this
       a correctly configured helper run container resolves `hosting=cloud` (*verified*), and
       #586's local handling (no factory-wide pause sentinel, `endpoint_unreachable` signature)
       would not apply to the one run that needs it.
   Results on the scratch copy (*verified*): helper intent passes with the constant and no
   secret; fails with the entry's `base_url`, with any other address and with the secret set;
   every other intent is refused exactly as before, whatever the environment.
5. **New `run-compose.yml` service `preview-helper-proxy`**, under its own profile of the same
   name. This is the literal definition (valid YAML, *verified* by rendering); the draft's
   mixed mapping/list form does not parse:
   ```yaml
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
   - **Environment, exactly these eleven keys.** Eight are the issue's list (endpoint id,
     registry digest, base URL, four aliases, the secret). Three are fixed literals the issue
     does not list and the service cannot work without: `PYTHONPATH` (the module is not
     importable otherwise, as on `factory-model-proxy`), `MODEL_PROXY_PORT` (pins the port the
     constant address assumes) and `CURRENT_RUN_DIR` (requirement 8). The draft also passed
     `SEQ_URL`, `RAW_ARTIFACT_CAPTURE` and `RAW_ARTIFACT_RETENTION_DAYS` from the scheduler's
     environment; they are removed (decision D3). The module's own defaults apply
     (`model_proxy.py:51-53`): no raw-artifact capture of helper traffic, and Seq events go
     to the default address only. An instance whose Seq is at a non-default `SEQ_URL` gets
     no helper-proxy events in Seq; the ledger is unaffected.
   - `FACTORY_HELPER_MODEL_SECRET:` with no value is compose's pass-through form: the container
     gets the scheduler's value, or no variable at all when the scheduler has none (renders as
     `null`).
   - No `env_file`, `depends_on`, `restart`, `container_name`, `command` or
     `MODEL_PROXY_UPSTREAM` key.
   - **Rendering, *verified*:** with the profile off, the rendered `dark-factory` and
     `factory-model-proxy` services, and the whole rendered file, are byte-identical to the
     file without this service. `config --services` lists the new service only under
     `--profile preview-helper-proxy`. `${VAR:-}` with the variable unset renders an empty
     string with no warning. `${VAR:?…}` on this service makes every ordinary render fail,
     profile off included, because interpolation covers the whole file; a bare `${VAR}` warns
     on every render. Hence `${VAR:-}` throughout.
   - **Reachability, stated as it is.** `dark-factory` has no `depends_on` on this service and
     no `aliases:` entry for it. But both services are on `dark-factory-net`, and a compose
     service's name resolves for every container on a shared network. While a helper proxy is
     up, any container on `dark-factory-net` — an ordinary gated run included — can reach
     `http://preview-helper-proxy:8787`. The proxy does not authenticate callers: it strips the
     inbound credential and signs with its own (`model_adapter.strip_and_sign_headers`). The
     draft's "no network path from `dark-factory` to it" was not true of its own YAML. This
     is the complete picture for this ticket (decision D1).
6. **Scheduler-side interface this ticket provides to #587.** All five are read by compose
   interpolation at the moment the dispatcher invokes compose for this profile.

   | Scheduler-side variable | Set by | Container-side key |
   |---|---|---|
   | `PREVIEW_HELPER_ENDPOINT` (#587's scheduler-only helper endpoint variable) | operator, scheduler environment | `FACTORY_MODEL_ENDPOINT` |
   | `FACTORY_MODEL_REGISTRY_SHA256` (the staged-tree digest `dispatch()` computes, `scheduler.sh:485-486`) | dispatcher, per dispatch | `FACTORY_MODEL_REGISTRY_SHA256` |
   | `PREVIEW_HELPER_ENDPOINT_BASE_URL` | dispatcher, per dispatch, from the registry entry | `ANTHROPIC_BASE_URL` |
   | `PREVIEW_HELPER_ENDPOINT_SERVED_NAME` | dispatcher, per dispatch, from the registry entry | the four `ANTHROPIC_DEFAULT_*_MODEL` |
   | `FACTORY_HELPER_MODEL_SECRET` | operator, scheduler environment | `FACTORY_HELPER_MODEL_SECRET` |

   The draft invented `FACTORY_HELPER_MODEL_ENDPOINT` for the first row; the issue says the id
   comes "from the scheduler-only helper endpoint variable #587 defines", and #587 names that
   variable `PREVIEW_HELPER_ENDPOINT`. The instance-wide `FACTORY_MODEL_ENDPOINT` is not a
   source: it means the instance's own selection and is forwarded to every ordinary dispatch.
   None of the five may be added to `PROVIDER_PASSTHROUGH_VARS`. The two derived values are
   not trusted by the proxy: its loader re-checks both against the digest-verified entry.
   **The digest is not there by default.** `dispatch()` stages the registry and sets
   `FACTORY_MODEL_REGISTRY_SHA256` only when the instance-wide `FACTORY_MODEL_ENDPOINT` is
   set (`scripts/provider_env.sh:64` returns early otherwise), and the variable is
   `local -x` inside `dispatch()` (`scheduler.sh:485`). #587 keeps the instance-wide
   variable unset, so its helper dispatcher must run `providers/cli.py model-endpoints stage`
   itself and export the digest it prints for both invocations: the compose call that
   starts this proxy and the one that starts the helper container.
7. **The secret has one fixed, reserved name, by choice.** Compose can pass through a
   variable whose name is chosen at dispatch time: in the list form of `environment:`, an item
   `- ${PREVIEW_HELPER_SECRET_NAME:-FACTORY_HELPER_MODEL_SECRET}` is interpolated and then
   passes through whatever variable that name names (*verified* by rendering; the first
   review's statement that compose cannot do this was true of the mapping form only). That
   form is **rejected deliberately**. A name chosen at dispatch time lets any variable in the
   scheduler's environment be delivered into the proxy: rendered with the name set to
   `GH_TOKEN`, the proxy receives the scheduler's tracker token (*verified*), and the registry
   validator only forbids `CLOUD_CREDENTIALS` names as a local `secret_reference`. One fixed,
   reserved name cannot be pointed at anything else. The helper lane therefore uses
   `FACTORY_HELPER_MODEL_SECRET`, in the mapping form shown in requirement 5, and a registry
   entry used for `classify-preview-helper` must declare
   `secret_reference: FACTORY_HELPER_MODEL_SECRET` (decision D2). An entry naming anything else
   fails closed with no new code: the named variable is absent from the proxy and
   `model_adapter.destination_from_entry()` refuses it (*verified*).
   What holds the value, plainly:
   - the scheduler's environment and the helper proxy container's environment;
   - an interpolated `docker compose config` for this profile prints it (*verified* with a
     dummy value); `config --no-interpolate`, and any render without this profile, do not;
   - `docker inspect` of the helper proxy container shows it to anything with Docker API
     access, which includes every `dark-factory` run container (`DOCKER_HOST`,
     `run-compose.yml`). Not executed here; this is how container environments work.
   No code in this ticket, and nothing #587 adds, may run or log an interpolated
   `docker compose config` for this profile.
8. **Correlation pointer: `CURRENT_RUN_DIR`, not `MODEL_PROXY_CURRENT_RUN_PATH`.** The service
   sets the static literal `CURRENT_RUN_DIR: /var/lib/dark-factory/preview-helper` (on the same
   `scheduler_state` volume the service already mounts read-write for ledger writes).
   `model_proxy.py`'s existing `CURRENT_RUN_PATH` module constant already falls back to
   `f"{os.environ.get('CURRENT_RUN_DIR', '/var/lib/dark-factory')}/current-run.json"`, so no
   code change is needed to honor this. This becomes the documented interface #587 must honor:
   the future `preview-helper` run container's one writable directory on an otherwise
   read-only state-volume mount is exactly `/var/lib/dark-factory/preview-helper/`, and that
   container must set the same `CURRENT_RUN_DIR` so `entrypoint.sh`'s existing
   `current-run.json` write (unchanged code, `entrypoint.sh:173-194`) lands where this proxy
   reads it. `MODEL_PROXY_LEDGER_PATH` is left unset, so the ledger stays at the shared
   `/var/lib/dark-factory/request-ledger.jsonl` and `run_record._compute_model_dispatch()`
   aggregates it unchanged. This pointer read stays best-effort/advisory exactly as today
   (`read_current_run()` never raises); reserving it a sub-directory does not make it a
   verified source.
   If the pointer is missing or unreadable the row's `correlation` is `unknown`
   (`model_proxy.py:106`), not `inferred_pointer`, and #587's independence proof fails closed
   on it. Creating the directory, and mounting only it writable in the helper container, are
   #587's.
9. **Lifetime: what this ticket delivers and what #587 must.** The issue asks that the proxy
   be started and removed with each helper dispatch and never reused.
   - *This ticket, testable alone:* the service definition has no `restart`, `container_name`
     or `depends_on` and is in no other service's `depends_on`, so nothing in the compose file
     starts it implicitly or keeps it alive; a helper-intent proxy refuses to start unless its
     whole environment matches one digest-verified local entry (requirements 3 and 4), so a
     proxy cannot come up half-configured. Nothing in `main` starts the service.
   - *#587's dispatcher, stated here as the contract:*
     1. Before a helper dispatch, look for any container of this compose project labelled
        `com.docker.compose.service=preview-helper-proxy`, in any state. If one exists: refuse
        the dispatch and report it. Never reuse it and never remove it silently — it may hold
        a different endpoint or secret, and the run-side check cannot tell a stale proxy from
        a fresh one because both answer on the same name.
     2. Start the proxy so that the service name resolves: `docker compose up -d
        preview-helper-proxy`, or `run` with `--use-aliases`. A plain `docker compose run`
        container does not get the service-name alias (compose's documented behaviour; not
        executed here), and the constant address would then not resolve.
     3. Remove the proxy when the helper container exits, is killed, times out or is stopped
        by the kill switch, before the dispatch is reported finished.
     4. Write a fresh `current-run.json` under `CURRENT_RUN_DIR` before the helper's first
        model call (requirement 8). The helper container's environment allow-list must
        therefore include `CURRENT_RUN_DIR=/var/lib/dark-factory/preview-helper`.
     5. Stage the registry and export its digest itself (requirement 6).
     6. Add the internal network its `preview-helper` container joins, and add that network
        to this service (decision D1).
   A proxy-side self-check for a stale instance was considered and rejected (Alternatives).
10. **Ledger and ledger aggregation unchanged.** `preview-helper-proxy` writes the same ledger
    row shape as `factory-model-proxy` today (`endpoint_id`, `hosting_location: local`, `role`,
    `correlation`, `payload_decision`, etc. — no new column). `role` resolves to `"actor"`
    automatically via the existing `role_isolation.bind_dispatch_role("local", declared_role=None)
    == ("actor", "default_actor")` path; no `FACTORY_DISPATCH_ROLE` handling changes.
    `run_record._compute_model_dispatch()` is untouched and aggregates these rows exactly as it
    does `factory-model-proxy`'s.
11. **Documentation.** Update, in `docs/configuration.md`'s "Model endpoint registry (#557)"
    section: the `secret_reference` row (line 539 at `a8cba16`, "the model proxy's own environment,
    delivered by #587" → delivered by `preview-helper-proxy`/#634 for the
    `classify-preview-helper` lane specifically, naming `FACTORY_HELPER_MODEL_SECRET` and the
    other four scheduler-side variables from requirement 6, and stating that an interpolated
    `docker compose config` for the helper profile prints the secret).
    Update `docs/dark-factory-model-proxy-rollout.md`'s "Local destination: supported and
    uncertified modes" section (the "the proxy service receives none of these today;
    delivering them is #587's" sentence) and `model_proxy.py`'s module docstring (the
    byte-identical duplicate sentence) to say the proxy now receives them for the
    `classify-preview-helper` lane via `preview-helper-proxy`, with dispatching that intent at
    all still gated on #587. Do not add a new runbook file — out of this issue's listed doc
    scope.

## Architecture / Approach

**Request flow once #587 lands (not active until then):** the (future) `preview-helper` run
container sends its Claude Code traffic to `ANTHROPIC_BASE_URL=http://preview-helper-proxy:8787`
(the constant from requirement 4). `preview-helper-proxy` — a single-purpose, per-dispatch
process holding the one local entry's `base_url`, served name and secret — forwards only to
that entry's verified `base_url`, exactly as `model_adapter.handle_proxy()` already does for
any other local destination, and appends the same ledger row shape to the shared
`request-ledger.jsonl`. `preflight()` (run-side) and `load_verified_entry()` (proxy-side) each
verify their own container's environment independently; `preflight()`'s view of a correctly
configured helper run container requires the proxy's constant address and no secret, while
`load_verified_entry()`'s view of a correctly configured proxy container requires the entry's
real `base_url` and the secret present — two different, non-conflicting checks over two
different containers' environments, both reachable with `intent in LOCAL_ELIGIBLE_INTENTS`.

**Why a disjoint service rather than widening `factory-model-proxy`:** `factory-model-proxy`
resolves its destination once per compose project and is designed to be the stable, always-on,
cloud-passthrough proxy every ordinary dispatch may share. Making it local-capable would mean
either resolving a destination per-request (a much larger change to a proxy whose startup
contract is "resolve once") or risking a later cloud dispatch being served by a process still
configured for a one-off local endpoint. A dedicated, ephemeral service — started and removed
per helper dispatch, per requirement 9 — keeps the blast radius of a local destination
to exactly the one dispatch that asked for it.

## Trust boundary and residual risk

What actually limits a local route, and what does not.

- **`--intent` is not a secret.** It is written in this service's `entrypoint:` and nowhere
  else in the repository, but any party that can invoke compose or Docker can append it to
  another container's command (`docker compose run factory-model-proxy --intent …` appends to
  that service's entrypoint) or start the module directly. The scheduler is the only component
  that invokes compose; `dark-factory` run containers hold `DOCKER_HOST` and the image, so a
  process inside one can do the same.
- **The environment checks are self-attested.** They compare the starting process's own
  environment with a registry tree whose digest also arrives in that environment. A process
  that chooses its own environment can satisfy all of them: the staged registry is on the
  state volume every run container mounts read-write, and its digest can be recomputed. The
  checks stop a misconfigured dispatch; they do not contain a hostile one.
- **What a `dark-factory` run container lacks** is the secret's value in its own environment
  and, for an ordinary dispatch, any helper variable at all (*verified*: with only the
  `factory` profile the rendered file contains none of them). It can still obtain the value
  through the Docker API while a helper proxy exists (requirement 7), and it can reach a
  running helper proxy by name (requirement 5).
- **Residual risk, accepted or not by the owner:** a compromised gated run that overlaps a
  helper dispatch can send requests through the helper proxy to the local endpoint, and its
  rows would carry the helper's run id from the helper's pointer file. This ticket does not
  remove that. #587's internal network narrows it for containers without Docker access;
  nothing in compose removes it for a container that holds `DOCKER_HOST`.
- **What a gated run container can do through Docker, concretely.** Its `DOCKER_HOST` is
  the scheduler's socket proxy, which allows `CONTAINERS`, `POST`, `NETWORKS`, `VOLUMES` and
  `IMAGES` and denies `EXEC` (`deploy/docker-compose.yml:45-56`). So a compromised gated run
  that overlaps a helper dispatch can: inspect the helper proxy and read its environment,
  the local secret included; stop or remove it; start a container that answers on the same
  network name; and attach itself to any network, a future internal one included. It
  cannot exec into the proxy. The helper's answers and the helper proxy's availability are
  therefore not protected from such a run. What bounds the effect is #587's rule that the
  helper's output is shadow, non-gating telemetry. This is an existing property of run
  containers; this ticket does not introduce it and does not change it.

## Owner decisions (recorded)

Decided by the operator on 2026-11-27 under the owner's overnight delegation, for the owner to
overrule. None is settled by the 2026-11-26 authorisation alone ("one new, separate proxy
service under its own profile; existing services untouched; nothing under `deploy/**`").

- **D1 — dedicated network: not in this ticket.** The authorisation covers one new service.
  The internal network belongs with #587's `preview-helper` service, the only container that
  would join it; #587 adds the network and adds it to this service. Until then the proxy is on
  `dark-factory-net` only and requirement 5's reachability statement is the complete picture.
  **Input for #587, not part of this ticket** — a form *verified* by rendering here (profile-off
  output stays byte-identical; `tests/test_build_network_compose.sh` still passes):
  ```yaml
  # top-level networks: gains
    preview-helper-net:
      driver: bridge
      internal: true
  # preview-helper-proxy networks: becomes
      - dark-factory-net      # egress to the local endpoint and Seq
      - preview-helper-net    # the only network preview-helper joins
  ```
  Gated run containers would still reach the proxy through `dark-factory-net`.
- **D2 — the reserved secret name `FACTORY_HELPER_MODEL_SECRET`: accepted** (requirement 7).
  It constrains the helper's registry entry, which is authored by hand under
  `deploy/instances/**`.
- **D3 — eleven environment keys: accepted.** The issue's eight plus `PYTHONPATH`,
  `MODEL_PROXY_PORT` and `CURRENT_RUN_DIR`. `SEQ_URL` and the two `RAW_ARTIFACT_*` variables
  stay dropped, with the consequence stated in requirement 5.

## Files touched

| File | Change |
|---|---|
| `run-compose.yml` | add the `preview-helper-proxy` service (requirement 5); nothing else |
| `scripts/factory_core/model_proxy.py` | `main(argv=None)`, `--intent`, the two helper-intent exits; module docstring |
| `scripts/factory_core/providers/model_adapter.py` | `resolve_local_destination(..., intent=None)` |
| `scripts/factory_core/providers/model_endpoint.py` | `HELPER_PROXY_BASE_URL`; `expected_base_url` on `_local_problems()` and `load_verified_entry()`; the two helper-intent rules in `preflight()` |
| `scripts/factory_core/providers/cli.py` | `_model_endpoints_hosting` passes the run-side expected address for an eligible `--intent` |
| `tests/test_run_compose.sh`, `tests/test_model_proxy_compose.sh` | extended; no new shell test file, so `.github/workflows/ci.yml` is not edited (`tests/test_ci_covers_shell_tests.py`) |
| `tests/test_factory_model_proxy.py`, `tests/test_provider_model_adapter.py`, `tests/test_provider_model_endpoint.py` (which already holds the `model-endpoints hosting` tests) | extended |
| `docs/configuration.md`, `docs/dark-factory-model-proxy-rollout.md` | requirement 11 |

Not touched: `scheduler.sh`, `entrypoint.sh`, `scripts/provider_env.sh`, the `dark-factory` and
`factory-model-proxy` service definitions, `deploy/**`, `.github/workflows/**`, `workflows/**`,
`config/`, any `gate_*` script, the breaker, budgets, any tool allow or deny list,
`run_record.py`, `role_isolation.py`. No endpoint address, hostname or model tag appears in any
committed file: fixtures use `192.0.2.0/24` and placeholder names.

Expected blast-radius verdict, checked with `gate_blast_radius.classify_file` against `main`
at `a8cba16` and the 5.0 hotspot floor: `scripts/factory_core/model_proxy.py` classifies as
`hotspot` (its score is exactly 5.0, at the floor), so the gate returns `HUMAN_REQUIRED` and a
signed approval is needed. No other file in the table triggers a category. `run-compose.yml`
is on the adapter's `critical_diff_paths`, which orders the review diff and does not block.
`sensitive_keywords` is read by epic-autopilot, not by the blast gate.

## Alternatives Considered

- **Pass the secret through under a name chosen at dispatch time**, using the list form
  `- ${PREVIEW_HELPER_SECRET_NAME:-…}`, so the entry's `secret_reference` could be any name.
  Possible in compose, and rejected: the name would be an input that decides which of the
  scheduler's variables enters the proxy (requirement 7).
- **Deliver the secret via an in-process `os.environ` rename inside `model_proxy.py`**, using
  two always-fixed variable names (one naming the real variable, one holding its value) instead
  of reserving one fixed `secret_reference` name. Rejected: it adds a secret-handling code path
  that `docker compose config` cannot verify, and the one-fixed-name convention (requirement 7)
  already satisfies every acceptance criterion with zero new code.
- **A proxy-side stale-instance self-check** (e.g. refusing startup if the ledger or pointer
  file looks "in progress"). Rejected (requirement 9): neither source can distinguish "a prior
  process is still running" from "a prior process exited uncleanly," and only the dispatcher,
  which can enumerate containers, can make that call correctly.
- **Reuse `FACTORY_MODEL_ENDPOINT` as the scheduler-side source for the helper's endpoint id**,
  avoiding one new variable name. Rejected: that name is already instance-wide, cloud-default
  semantics; reusing it would make the helper proxy resolve the wrong endpoint on any instance
  that also has a default `FACTORY_MODEL_ENDPOINT` configured.
- **Point the correlation pointer at `ARTIFACTS_DIR`** (the per-run artifact directory
  `entrypoint.sh` already creates, matching the issue Notes' "one writable artifact directory"
  phrasing literally). Rejected: `ARTIFACTS_DIR` lives on a different volume than
  `scheduler_state` (Archon's own workspace volume) and is parameterized by `RUN_ID`, which
  does not exist yet when `preview-helper-proxy`'s static environment is rendered —
  `CURRENT_RUN_DIR` reuses an interface the proxy already has, with a fixed, reservable path.
## Open Questions

None for this ticket. How #587's dispatcher implements the
stale-instance check and the removal (requirement 9) is #587's design.

## Assumptions (flagged)

- #633 lands with the exact names and signatures recorded in its own spec (§7): the intent
  token `classify-preview-helper`, `LOCAL_ELIGIBLE_INTENTS`, and the `intent=None` keyword on
  `_local_problems()`/`preflight()`/`load_verified_entry()`. If #633's implementation diverges
  from its spec before #634 is implemented, this spec's requirements 1–4 must be re-checked
  against whatever actually merged.
- `bind_dispatch_role("local", declared_role=None)` continues to resolve to `("actor",
  "default_actor")` unchanged (verified by reading `role_isolation.py` at the time of writing);
  this spec relies on that existing behavior rather than adding new role-handling code.
- `preflight()` and the hosting resolution receive the helper intent from `entrypoint.sh`'s
  `--intent` argument as #633 specifies. #587 decides whether its `preview-helper` container
  runs `entrypoint.sh` at all; whichever entry point it uses must call the same two checks
  with the same intent.

## Test Plan

Hermetic throughout: an in-process stub or the capability suite's mock as the endpoint, fixture
registries with `192.0.2.0/24` addresses, no real endpoint, no container started. Compose tests
only render (`docker compose config`) and skip, as the existing ones do, when Docker is absent.
Names are binding; the plan may add more. No new `tests/*.sh` file is created, so
`.github/workflows/ci.yml` is unchanged.

Two kinds of test. An unmarked test is written first and fails on `main` plus #633. A test
marked *guard* passes there already and exists to stop a regression; it cannot be written
failing-first and the plan must not pretend otherwise.

Where each compose assertion is made: keys that must be absent (`env_file`, `depends_on`,
`restart`, `container_name`, `command`, `MODEL_PROXY_UPSTREAM`) are asserted on the **parsed
source YAML**, because a render carries `command: null` for every service and merges an
`env_file` into `environment`. The environment key set is asserted on the **render**.

| Acceptance criterion | Test (file) |
|---|---|
| Helper profile shows exactly the listed variables; no `env_file`, `DOCKER_HOST`, tracker, code-host or cloud credential | `helper_proxy_env_is_exactly_the_allow_list` (`tests/test_run_compose.sh`): renders with the profile, the four non-secret source variables set to fixture values, `FACTORY_HELPER_MODEL_SECRET` set to a plain non-secret fixture string, and `DOCKER_HOST`, `GH_TOKEN`, `GITLAB_TOKEN`, `JIRA_TOKEN` and each `CLOUD_CREDENTIALS` name set in the caller's environment; asserts the rendered key set equals the eleven of requirement 5. The render is parsed, never echoed. `helper_proxy_source_has_no_forbidden_keys`: the parsed source YAML of the service has none of the six keys above. `helper_proxy_entrypoint_is_the_literal_intent`: the source `entrypoint` list equals the five literal tokens and contains no `$` |
| Profile off: existing services byte-identical | `new_service_does_not_change_other_renders` (*guard*, `tests/test_run_compose.sh`), a differential test: delete the `preview-helper-proxy` service from a temporary copy of `run-compose.yml`, render both files with the same fixed environment and env file for no profile, `factory`, and `factory` + `factory-model-proxy`, and assert identical output each time. `helper_proxy_absent_without_its_profile` (*guard*, `tests/test_model_proxy_compose.sh`): `config --services` under those profile sets does not list it |
| `dark-factory` has no `depends_on` or alias reaching it | `dark_factory_does_not_depend_on_helper_proxy` (*guard*, `tests/test_run_compose.sh`): in the parsed source, no service's `depends_on` names it and no service declares a network `aliases:` entry equal to its name. (This is what the criterion says. Same-network name resolution is requirement 5's stated limit.) |
| Helper proxy binds and forwards only to the entry's `base_url`; refuses otherwise | `tests/test_factory_model_proxy.py`: `test_helper_intent_starts_with_the_local_destination`; `test_helper_intent_forwards_only_to_the_entry_base_url` (stub upstream; a second listener receives nothing); `test_helper_intent_without_an_endpoint_exits_before_binding` (unset, empty); `test_helper_intent_with_a_cloud_entry_exits_before_binding`; `test_helper_intent_refuses_an_entry_pointing_at_the_proxy_address`; `test_empty_intent_value_counts_as_given`; `test_helper_intent_refuses_each_missing_or_wrong_variable`, parametrised one case per variable: digest missing, digest wrong, `ANTHROPIC_BASE_URL` missing, `ANTHROPIC_BASE_URL` = the proxy constant, each alias missing, an alias wrong, secret missing, `MODEL_PROXY_UPSTREAM` set to another address. *Guards:* `test_no_intent_argument_still_refuses_a_local_entry`; `test_bare_main_ignores_sys_argv`; `test_intent_is_not_read_from_the_environment` (plausible names set, no argument: refused) |
| Proxy refuses any cloud model credential | `test_helper_intent_refuses_each_cloud_credential`, parametrised over `CLOUD_CREDENTIALS` |
| Run-side preflight for the helper intent | `tests/test_provider_model_endpoint.py`: `test_helper_preflight_passes_with_the_proxy_address_and_no_secret`; `test_helper_preflight_refuses_other_base_urls`, parametrised over the entry's `base_url`, another address, the constant with a trailing slash, unset; `test_helper_preflight_refuses_a_present_secret`; `test_helper_hosting_resolves_local_only_with_the_proxy_address` (through the providers CLI's `model-endpoints hosting --intent`). *Guard:* `test_proxy_side_loader_still_requires_the_entry_base_url` |
| Every other intent unchanged | existing tests green; `test_non_helper_intents_ignore_the_helper_rules` (*guard*), parametrised over `None`, `""`, `fix`, `refine`, `classify-preview-checker`, in both a helper-shaped and today's environment: same problems as before this change; `test_hosting_cli_passes_no_expected_base_url_for_other_intents` |
| Ledger row and aggregation | `tests/test_factory_model_proxy.py`: `test_helper_request_writes_the_local_actor_row` (`endpoint_id`, `hosting_location: local`, `role: actor`, `correlation: inferred_pointer`, the pointer read from `CURRENT_RUN_DIR`) and `_compute_model_dispatch()` over it gives `outcome: ok`; `test_helper_failure_rows`, parametrised over upstream 5xx, timeout and wrong served model: the matching failure class and `outcome: failed`; `test_missing_pointer_gives_correlation_unknown` |
| The secret appears nowhere captured | `test_secret_value_is_in_no_row_artifact_or_log` (`tests/test_factory_model_proxy.py`): a sentinel secret, raw-artifact capture on, one success and one failure; the sentinel is in no ledger line, no artifact file, no captured stdout or stderr. `helper_secret_is_not_in_rendered_config` (`tests/test_run_compose.sh`): with a sentinel in the caller's environment, `config --no-interpolate` under the helper profile contains no sentinel, and (*guard*) neither does an interpolated `config` under `--profile factory`. No test captures an interpolated helper-profile render with the sentinel set |
| Adapter passthrough | `test_resolve_local_destination_forwards_the_intent` (`tests/test_provider_model_adapter.py`) |
| Docs | the existing docs reference checks stay green; the three edited passages agree with each other |

**On the issue's "golden comparison" wording.** The differential test replaces a committed
golden file. A golden generated from `main` goes stale with every later, legitimate change to
the existing services and then fails for a reason unrelated to this service; the differential
test keeps proving the one property that is this service's to guarantee, that its presence
changes no other render. Its limit: it cannot see an edit to the existing services made in the
same change, because both renders would carry it. That half of the criterion is met by review:
this ticket's diff to `run-compose.yml` must consist only of added lines inside the new
service block.

**Existing tests this change breaks, and the required update.** Found by the second reader
running the Python suite on #633 alone and on #633 plus this spec:

- `tests/test_factory_model_proxy.py::test_fixture_fed_local_startup_failures_exit_before_binding`
  (three parameters) and `::test_payload_decision_allow_false_fails_startup`: their
  two-parameter `load_verified_entry` stubs now receive `intent=`. The stubs must accept it.
- `tests/test_provider_model_adapter.py::test_resolve_local_destination_builds_from_the_verified_entry`:
  same stub, and its recorded call must include the intent.

Not broken: the three bare `mp.main()` callers (`:549`, `:560`, `:568`), because of
requirement 3's `argv` rule; #633's hosting stubs, because the hosting CLI adds
`expected_base_url` only for an eligible intent; and the four compose shell tests wired in CI
(`test_run_compose.sh`, `test_model_proxy_compose.sh`, `test_build_network_compose.sh`,
`test_provider_env_flags.sh`), which pass unchanged with the new service present (run on a
scratch copy of the repository).
