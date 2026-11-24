# Model Endpoint Registry, Secret References, and Provider-Aware Preflight

**Issue:** #557

**Revised:** 2026-11-23 (operator amendment after spec gate)

## Overview / Problem Statement

#557 is the **Configuration-contract owner** in the accepted **Hybrid model execution:
cloud + local** initiative (`docs/hybrid-execution-contract.md`, from #556, revised by #580 —
both merged/closed). The contract's §4.1 responsibility map assigns this ticket exactly one
row: *"Endpoint registry, secrets, preflight"* — "a registry entry is valid only with: id,
hosting location, destination class(es), model identity, timeout bounds, secret reference,
schema_version. Preflight hard-fails on any missing field (mirrors today's `providers/model.py`
posture for `databricks`/`openai`)." The issue body's acceptance criteria add: default-cloud
compatibility stays green; missing/unknown/stale config always fails visibly, never silently
reroutes; no cloud credential reaches a local endpoint; a redacted effective-configuration view
with schema/contract tests for same-host/LAN/remote; documented env placement; a reversible
default-cloud path. A trailing issue-body bullet (verified against #580's contract-revision
items 7 and 9, which name #557 as the owning ticket for exactly these two mechanisms, not an
injected instruction) additionally assigns this ticket the payload-class field + dispatch-time
predicate (§4.3) and the operator-controlled, write-protected capability-evidence storage
surface (§4.5).

Today, model selection is a single axis: `FACTORY_MODEL_PROVIDER` (default `anthropic`),
checked by `scripts/factory_core/providers/model.py`'s `_MODEL_PROVIDERS` dict of zero-arg
checks (`anthropic`/`bedrock`/`vertex` validate real env vars via `_missing()`;
`databricks`/`openai` are static `_not_yet_implemented()` stubs). There is no registry, no
per-endpoint timeout/capability concept, and no payload-confidentiality axis at all.

**Non-goals (hard scope boundary, repeated three times in the contract and restated here):**
this ticket makes no runtime-dispatch, gate, breaker, budget, session-window, or
`deploy/instances/**`-write change. It does not build the runtime adapter that actually talks
to a local endpoint (#558), does not build checker/worker isolation (#559), does not certify
any hardware (#561/#562), and does not perform a live network-reachability check (deferred to
#558's runtime adapter — see Alternatives). It ships a **read-only** configuration loader,
validator, and preflight, plus the pure payload-class predicate — nothing in this ticket writes
to the human-only instance-config surface.

## Requirements (from Q&A)

Full brainstorming dialogue is reproduced in the Phase 6 issue comment. Load-bearing
resolutions:

1. **Storage surface.** The registry and capability-evidence records live under
   `deploy/instances/<instance>/model-registry/` — a subdirectory of the existing
   `deploy/instances/<instance>/` human-only surface (already a hard-exclude path in
   CLAUDE.md's hard limits and `.factory/adapter.yaml`'s `safety.hard_exclude_paths`), isolated
   in its own subdirectory so mounting it never also exposes the rest of the instance directory.
2. **Registry entries are non-secret and fully committed**, since `secret_reference` holds only
   an env-var *name*; capability-evidence files are operator-authored only, one per
   `endpoint_id`, as siblings in `capability-evidence/`.
3. **Preflight must never trust the run container's own git clone for this data.** A dispatched
   container can edit its own clone's copy of anything under `deploy/instances/`; that path's
   protection is convention/review-level only (confirmed live: `scripts/gate_blast_radius.py`'s
   one hard PR-time block checks `side_effect_level`, not `hard_exclude_paths` membership). The
   registry must instead be delivered through a channel the clone cannot touch.
4. **Today's default path is untouched byte-for-byte.** The registry is read only when a new
   selection variable, `FACTORY_MODEL_ENDPOINT`, is set and non-empty. Unset (today's default),
   no registry file is read, looked for, or mounted — this *is* the reversible default-cloud
   path the acceptance criteria ask for.
5. **No cloud credential can reach a local endpoint**, enforced structurally: the loader rejects
   any `hosting_location: local` entry whose `secret_reference` names a recognized cloud
   credential variable.
6. **A redacted effective-configuration view** is a new CLI subcommand that never prints a
   secret's value, only its reference name and presence.
7. **No "declared but inert" stub ships in production config** — mirrors the `gitlab.py`
   precedent (a real, tested, but unselected implementation) via test fixtures only, since no
   certified local hardware exists yet (#562 is still unscoped/unbuilt).
8. **The staged copy is never trusted without a scheduler-supplied digest.** Run containers
   mount the shared state volume read-write (`run-compose.yml`, `scheduler_state` at
   `/var/lib/dark-factory`), so any run can rewrite the staged registry or capability evidence.
   After staging, the scheduler computes a SHA-256 over the staged tree and forwards it to that
   one dispatch as `FACTORY_MODEL_REGISTRY_SHA256`, only through its own `docker compose run -e`.
   Preflight, and every later reader, recomputes the digest and hard-fails on a mismatch. The
   digest is never read from a file a run container can write. Two more cases fail closed:
   `FACTORY_MODEL_ENDPOINT` set with no digest, and a digest present with no
   `FACTORY_MODEL_ENDPOINT` (§D).
9. **A local selection holds no cloud model credential.** When the selected entry is
   `hosting_location: local`, preflight fails if any cloud model credential is set in the
   environment, and it does not run the `FACTORY_MODEL_PROVIDER` credential check (contract
   §4.3 credential-scope rule, Invariant 16). Which env file a local-routed container receives
   is #558's and #587's to define; this spec does not designate `.archon/.env` for it (§C).
10. **Decisions 1 and 2 are enforced by preflight.** Every entry declares
    `protocol: anthropic-messages`; any other value is rejected. For a selected `local` entry,
    preflight fails unless `ANTHROPIC_BASE_URL` equals the entry's `base_url` and all four
    `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU,FABLE}_MODEL` equal `model_identity.served_name`
    (contract §4.11 decisions 1 and 2).
11. **No existing phase may select a local entry.** Until #587 ships the first helper dispatch,
    preflight fails whenever the selected entry is `local`, because every dispatch that exists
    today runs a gated phase (contract §4.11 decision 3). Operator decision, 2026-11-23.
12. **`deploy/` surface.** This spec edits `deploy/docker-compose.yml` (one read-only mount on
    `backlog-scheduler`) and adds `deploy/model-registry.empty/`. `deploy/**` is a
    human-authorised surface: these two paths, and no others under `deploy/`, are in scope only
    under the owner's explicit authorisation recorded on issue #557. `deploy/instances/**` and
    `.github/workflows/publish.yml` are never touched.

## Architecture / Approach

### A. File layout (human-only instance-config surface, §4.5)

```
deploy/instances/<instance>/model-registry/
  model-endpoints.yaml              # git-tracked, non-secret, operator-authored
  capability-evidence/
    <endpoint_id>.yaml              # git-tracked, operator-authored only (#562's output)
```

This repo ships no real file here — `deploy/instances/dark-factory/model-registry/` is created
by an operator only when they certify a local endpoint. Until then, `FACTORY_MODEL_ENDPOINT` is
unset everywhere and this ticket's new code paths never execute.

### B. Registry schema (`model-endpoints.yaml`, configuration contract `schema_version`)

```yaml
schema_version: "1.0"
endpoints:                          # a list, not a map — avoids silent YAML duplicate-key
                                     # overwrite; the loader rejects duplicate ids
  - id: local-qwen-coder            # ^[a-z0-9][a-z0-9-]*$ — the endpoint_id capability-evidence
                                     # files and future ledger rows cite
    hosting_location: local         # local | cloud
    protocol: anthropic-messages    # required; the only accepted value (§4.11 decision 1)
    accepts_payload_classes: [local_only]   # non-empty subset of {cloud_ok, local_only};
                                     # cloud entries may never list local_only; local entries
                                     # must list local_only (§4.3)
    model_identity:
      served_name: qwen2.5-coder-32b
      digest_or_revision: sha256:...        # required, immutable — no "latest"-style alias
    base_url: http://host.docker.internal:8080   # required for local; absent for cloud
                                     # (cloud already resolves its address via today's env vars)
    secret_reference: LOCAL_GATEWAY_TOKEN   # an env-var NAME only, never a value
    timeouts:                       # required for local; forbidden for cloud (see below)
      connect_seconds: 10
      time_to_first_token_seconds: 120
      idle_seconds: 60
      total_seconds: 1800
```

Validation rules (all enforced by the new loader, §C):
- Major-version mismatch on `schema_version` is a hard reject (Invariant 12). At the supported
  minor version, unknown keys are rejected (catches typos like `timeout`/`model`); unknown keys
  are tolerated only on a file whose minor version is newer than the loader supports.
- `accepts_payload_classes` cross-checked against `hosting_location` as stated above.
- `protocol` is mandatory on every entry and must be exactly `anthropic-messages`. Any other
  value, including an OpenAI-shape protocol, is rejected as an unsupported protocol; nothing is
  translated or defaulted.
- `timeouts`: all four fields mandatory, each a positive number, with
  `connect_seconds`/`time_to_first_token_seconds`/`idle_seconds` each ≤ `total_seconds`, **for
  `hosting_location: local` only**. A `hosting_location: cloud` entry that includes a `timeouts`
  block fails preflight explicitly ("timeouts are not enforced for cloud entries — §4.6") rather
  than silently ignoring it — cloud timeouts stay untouched, owned elsewhere.
- Cloud entries are optional, not required. Today's zero-entry `FACTORY_MODEL_PROVIDER=anthropic`
  path never reads this file. A cloud entry is only worth writing when something needs to name a
  cloud endpoint explicitly (e.g. the distinct `endpoint_id` that §4.2's checker-independence
  proof relies on); its `secret_reference` would name an existing var like `ANTHROPIC_API_KEY`.

### C. New module and wiring

- New file `scripts/factory_core/providers/model_endpoint.py`, parallel to `model.py`.
  `model.py`'s `_MODEL_PROVIDERS` dict is **not modified** — `anthropic`/`bedrock`/`vertex`/
  `databricks`/`openai` behave exactly as today.
- `providers/__init__.py::preflight()` calls into `model_endpoint.preflight()` only when
  `FACTORY_MODEL_ENDPOINT` (a registry `id`) is set and non-empty. For a selected `cloud` entry
  it runs **in addition to** the existing `model.preflight(model_name)` call. For a selected
  `local` entry the existing cloud-credential check is skipped, because that container must
  hold no cloud model credential (Requirement 9). #557 does not reroute anything — it only
  validates configuration. If `FACTORY_MODEL_REGISTRY_SHA256` is set while
  `FACTORY_MODEL_ENDPOINT` is unset or empty, `preflight()` fails (Requirement 8).
- `model_endpoint.py` exposes: a loader (`load_registry(path) -> list[dict]`, raising on schema
  violations per §B); a capability-evidence reader (`load_evidence(path, endpoint_id) ->
  dict | None`) that computes the §4.5 derived state (`tested | declared | stale | unknown`)
  from `expires_at`/`invalidated_by` fingerprint comparison, never stored, never operator-set;
  `preflight(registry_path, evidence_dir, endpoint_id) -> list[str]`, which fails closed (empty
  list = OK) on: a missing or mismatched staged-tree digest (Requirement 8), unknown
  `endpoint_id`, any schema violation, the selected entry's `secret_reference` env var unset,
  and, for a `local` entry: a cloud model credential present in the environment (Requirement
  9), `ANTHROPIC_BASE_URL` or a tier alias that does not match the entry (Requirement 10),
  evidence that is anything other than `tested` with every role-required dimension `pass`, and
  the run being an existing phase (Requirement 11) — mirroring `databricks`/`openai`'s existing
  all-or-nothing posture, but per-field instead of a static stub message.
- Capability evidence is required for `local` entries only; a `cloud` entry needs none. #557
  dials no endpoint, so it compares only what it can read without one: `expires_at`, the
  evidence's model digest against the registry entry's `digest_or_revision`, `harness_version`
  against the pinned Claude CLI version, `fixture_suite_version` against the pinned fixture
  suite, and a hash of the registry entry (`registry_config`). Any fingerprint it cannot
  resolve yields `unknown`, which fails; an unobserved fingerprint is never treated as
  matching. Until #561 pins a fixture suite there is no valid `fixture_suite_version`, so every
  local selection fails closed on evidence as well.
- `secret_reference` resolution is split in two, matching why `anthropic`/`bedrock`/`vertex`
  already separate schema shape from env-var presence:
  - **Parse time:** the loader only checks the field is present and matches
    `^[A-Z][A-Z0-9_]*$` — a pasted token value fails as invalid config, not as a name. The
    loader never reads the environment.
  - **Preflight time:** only for the endpoint actually selected, `_missing([secret_reference])`
    (the exact helper `anthropic`/`bedrock`/`vertex` already use) checks the named var is set.
    The error names the variable, never its value. This split exists because the scheduler host
    and the run container load different env files (`deploy/instance.env` vs `.archon/.env`); a
    parse-time existence check would wrongly fail on whichever side doesn't hold that secret.
  - **Cross-credential guard:** at parse time, a `hosting_location: local` entry whose
    `secret_reference` matches a recognized cloud-credential name (`ANTHROPIC_API_KEY`,
    `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_AUTH_TOKEN`, `AWS_ACCESS_KEY_ID`,
    `AWS_SECRET_ACCESS_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`) is rejected outright. This
    name check alone is not the guarantee: Claude Code sends whatever cloud token is in its
    environment to `ANTHROPIC_BASE_URL`.
  - **Environment guard (Requirement 9):** at preflight time, for a selected `local` entry, any
    of those same variables being set in the container's environment is a failure, with the one
    exception of the variable the entry's own `secret_reference` names. The error lists the
    variable names, never values. Together the two checks are the structural guarantee behind
    "no cloud credential forwarded to a local endpoint."
- `scripts/provider_env.sh`'s `PROVIDER_PASSTHROUGH_VARS` gains `FACTORY_MODEL_ENDPOINT` under
  its "Provider selection" group (names only, per the file's existing convention); the
  *resolved secret values* a `secret_reference` points at are never added there. A cloud
  entry's secret is one `.archon/.env` already carries (e.g. `ANTHROPIC_API_KEY`). A local
  entry's secret must not be delivered through `.archon/.env`: that file is the one env file
  every run container receives, so it would put local and cloud credentials in the same
  container. The env file a local-routed container receives is #558's and #587's to define.
  `FACTORY_MODEL_REGISTRY_SHA256` is **not** added to `PROVIDER_PASSTHROUGH_VARS` either: that
  list forwards values from the scheduler's `instance.env`, and the digest is computed per
  dispatch (§D). `tests/test_provider_env_passthrough.py` is extended to pin the addition of
  `FACTORY_MODEL_ENDPOINT` and the absence of the digest variable.

### D. Delivery mechanism — why a direct bind mount doesn't work, and what does

Two live infrastructure facts rule out the obvious "just add a `volumes:` bind mount in
`run-compose.yml`" design:

1. `docker compose run` (the dispatch call) executes **inside the backlog-scheduler container**,
   but bind-mount *source* paths in `volumes:` are resolved by the **daemon** the socket proxy
   forwards to — not by the scheduler container's own filesystem. `.archon/.env`'s existing
   mechanism avoids this because `env_file:` is resolved **client-side** (scheduler.sh copies
   `/workspace/project/.archon/.env` to `/opt/dark-factory/.archon/.env` — scheduler.sh:53-58 —
   and the compose CLI reads that file itself, translating it to literal `K=V` pairs in the
   container-creation request). A `volumes:` entry naming `/opt/dark-factory/...` would instead
   ask the daemon to mount *its own host's* path by that name, which has nothing to do with the
   scheduler container's copy.
2. The scheduler's only read access to a host filesystem is `${PROJECT_DIR}:/workspace/project:ro`
   (`deploy/docker-compose.yml`) — for the MarketHawk instance, that mount is the **MarketHawk**
   checkout, not this repo, so it never contains `deploy/instances/**` at all. This instance's
   own `model-registry/` must arrive through instance-level config, not the per-target clone.

The design that avoids both problems reuses machinery that already exists:

- **`deploy/` authorisation (Requirement 12).** This spec edits `deploy/docker-compose.yml`
  (one read-only mount on `backlog-scheduler`) and adds `deploy/model-registry.empty/`.
  `deploy/**` is a human-authorised surface: these two paths, and no others under `deploy/`,
  are in scope only under the owner's explicit authorisation recorded on issue #557.
  `deploy/instances/**` and `.github/workflows/publish.yml` are never touched. The scheduler
  container therefore gains one read-only mount of an empty directory even when the feature is
  off; dispatch argv and env stay byte-identical (Requirement 4).
- `deploy/docker-compose.yml` (outside `hard_exclude_paths`, but inside the adapter's
  `critical_diff_paths` — so PR review is already flagged to scrutinize this file) gains one new
  **optional, read-only** bind mount on `backlog-scheduler`:
  `${FACTORY_MODEL_REGISTRY_DIR:-./model-registry.empty}:/etc/dark-factory/model-registry:ro`.
  The default points at a new tracked, empty `deploy/model-registry.empty/` (a `.gitkeep`
  placeholder) so an operator who never sets `FACTORY_MODEL_REGISTRY_DIR` gets an empty,
  harmless mount. An operator who wants the feature sets `FACTORY_MODEL_REGISTRY_DIR` in their
  own `instance.env` to their host's `deploy/instances/<instance>/model-registry/` path. (Note:
  mounting only this one instance's own `model-registry/` subdirectory — never the broader
  `deploy/instances/` tree — is deliberate: it never exposes another instance's secrets or
  registry.)
- **Handoff to the run container reuses the existing shared named volume** already in lockstep
  between the scheduler (`dark_factory_state`) and every dispatched run container
  (`scheduler_state`), both mounted at `/var/lib/dark-factory` — the same channel the main-red
  sentinel already relies on (`run-compose.yml`'s own comment documents this lockstep
  requirement). Before each dispatch, **only when `FACTORY_MODEL_ENDPOINT` is set**, `scheduler.sh`
  calls a new `providers/cli.py model-endpoints stage` subcommand. The staging logic is a Python
  function, not shell, so pytest covers it. It copies `/etc/dark-factory/model-registry/` into
  `/var/lib/dark-factory/model-registry/` via a temp-directory-plus-atomic-`mv` swap (so a
  mid-copy failure never leaves a half-written registry visible to the next dispatch) and prints
  the SHA-256 of the staged tree (sorted relative paths and file bytes). This re-stages on
  *every* dispatch. `run-compose.yml` needs no new `volumes:` entry — the mount it already has
  is reused.
- **Digest handoff (Requirement 8).** Re-staging alone is not protection: the volume is
  read-write in every run container, so a running container could rewrite the staged copy
  between another dispatch's staging and its preflight. The scheduler therefore exports the
  digest it just computed as `FACTORY_MODEL_REGISTRY_SHA256` for that one `docker compose run`
  and forwards it with `-e FACTORY_MODEL_REGISTRY_SHA256`. That is the only channel: the digest
  is never written to, or read from, the shared volume, the clone, or any other file a run
  container can write, and it is not in `PROVIDER_PASSTHROUGH_VARS`. The run container's
  preflight recomputes the digest over what it reads and hard-fails on a mismatch, on a missing
  digest when `FACTORY_MODEL_ENDPOINT` is set, and on a digest present when
  `FACTORY_MODEL_ENDPOINT` is unset or empty. Any later reader of the staged copy (#558's
  adapter included) must repeat the check before using it. If staging fails, the scheduler does
  not dispatch.
- The run container's preflight reads `${FACTORY_MODEL_REGISTRY_PATH:-/var/lib/dark-factory/model-registry}`.
  The override exists for tests only and is **not** added to `PROVIDER_PASSTHROUGH_VARS`.
  A missing/unreadable staged directory, when `FACTORY_MODEL_ENDPOINT` is set, is a preflight
  hard-fail — never a silent fallback to cloud.

### E. "Declared but inert" entries — test fixtures only

Mirroring `providers/codehost/gitlab.py`'s precedent (a real, unit-tested implementation that is
deliberately never registered as selectable): #557 ships one example local entry, one example
cloud entry, and matching capability-evidence fixtures under `tests/fixtures/model_registry/` —
exercising same-host (`localhost`/`host.docker.internal`), LAN (an RFC1918 `base_url`), and
remote (a public hostname) `base_url` shapes, all validating against the identical schema. No
such entry ships under any real `deploy/instances/*/model-registry/` path, and this ticket's
code cannot create one there (hard-exclude convention, §A). Nothing becomes selectable until an
operator hand-writes the real files and sets `FACTORY_MODEL_ENDPOINT`.

### F. Payload-class field and dispatch-time predicate (§4.3)

`model_endpoint.py` additionally exposes two pure functions implementing §4.3's predicate, as
the issue's trailing bullet and the contract's "#557's to implement (not #558)" line both
require:

- `effective_payload_class(declared: str | None, hosting_location: str) -> str` — a
  `local`-destination dispatch is always `local_only`; a `cloud`-destination dispatch is the
  declared class if set, else `cloud_ok`.
- `dispatch_allowed(effective_class: str, accepts_payload_classes: list[str]) -> bool`, plus a
  structured decision object (`{payload_class, endpoint_id, secret_reference, allow, reason}`)
  matching the shape §4.7 says belongs in the run-record ledger extension.

**Scope boundary:** #557 ships the field, the predicate, and the decision object's *shape* only.
It does not call either function from a real dispatch path and does not touch `run_record.py` —
no dispatch exists yet to call them from (§4.11 decision 3: no helper dispatch ships until #587,
which depends on #585/#586). Wiring these functions into an actual dispatch and writing their
output into `run_record.py`'s additive-key idiom (`harness_economics`/`memory_trace`'s pattern)
is #558's and #559's to do, per §4.7's contract-ownership table (#558 owns the Request/Result
contract). This keeps #557 from adding a dead call site to a file it does not otherwise need to
change.

### G. Redacted effective-configuration view

`providers/cli.py` gains a `model-endpoints show` JSON subcommand: loads and validates the
staged registry, and for each entry prints every field verbatim **except** `secret_reference`,
which prints as `{"name": "<the name>", "present": true|false}` — the presence check reads
`os.environ` but the command never prints a value. A test sets a sentinel value for a fixture's
referenced var and asserts it never appears in the command's output. Invalid registries exit
non-zero with the same per-field messages preflight would produce. The same fixtures back the
same-host/LAN/remote schema/contract tests (§E) — one schema, three different `base_url` shapes;
classifying a URL as same-host/LAN/remote is informational only in the `show` output and never
changes validation.

### H. Tests and docs

- `tests/test_provider_model_endpoint.py` (new) — mirrors `tests/test_provider_model.py`'s
  pass/fail-per-field structure, plus the cross-credential guard, the four failure-mode
  acceptance criteria (unknown id, missing credential, stale/missing evidence, schema version
  mismatch), and the staging/atomic-swap behavior of the `model-endpoints stage` function with a
  tmp-path fixture standing in for `/var/lib/dark-factory`. It also covers: a tampered staged
  file failing the digest check; an endpoint with no digest; a digest with no endpoint; an
  unsupported `protocol`; a local selection with a cloud credential set; a local selection with
  a mismatched `ANTHROPIC_BASE_URL` or tier alias; a local selection failing as an existing
  phase; evidence required for local only; and an unresolvable fingerprint yielding `unknown`.
- `tests/fixtures/model_registry/` — the local/cloud/same-host/LAN/remote fixtures from §E.
- `tests/test_provider_env_passthrough.py` — extended for `FACTORY_MODEL_ENDPOINT`.
- `tests/test_provider_cli.py` — extended for `model-endpoints show` and `model-endpoints stage`.
- `tests/test_run_compose.sh` — extended to assert the new read-only mount in
  `deploy/docker-compose.yml`. This file is already listed in `.github/workflows/ci.yml`.
- **What runs where.** The pytest files above run in CI under `python -m pytest tests/`. A shell
  test runs in CI only if it is listed by name in `.github/workflows/ci.yml`.
  `tests/test_scheduler.sh` is not listed there, so the `scheduler.sh` call site is not covered
  by it in CI; this is why the staging logic lives in Python and `scheduler.sh` only calls it.
  None of the shell tests can run on the Windows host.
- `docs/configuration.md` — new section documenting exactly which variable lives where:
  `FACTORY_MODEL_ENDPOINT` + `FACTORY_MODEL_REGISTRY_DIR` in the scheduler's `instance.env`;
  `secret_reference`-named secrets in the run container's `.archon/.env`;
  `FACTORY_MODEL_REGISTRY_PATH` as a test-only override. This satisfies the issue's "document
  environment placement (scheduler vs run container)" criterion directly.
- Default-cloud compatibility: no existing test asserting today's `anthropic`/`bedrock`/`vertex`/
  `github`/`github` argv/env behavior changes, since `model.py` and `providers/__init__.py`'s
  existing code paths are additive-only (a new `if FACTORY_MODEL_ENDPOINT:` branch, nothing
  removed or reordered).

## Deviations from the issue's acceptance criteria

Two items in the issue text are not delivered by #557. Both follow the contract's ownership
split, and the operator accepted both on 2026-11-23.

- **Live reachability ("unreachable service … fail visibly", and "network
  reachability/readiness" in the issue's scope text).** Owner: #558. #557 validates static
  configuration only and checks that `base_url` is a well-formed URL. #558's runtime adapter is
  the first component that dials an endpoint; an unreachable endpoint fails closed there under
  the contract's §4.6 interim failure classes.
- **The predicate's "recorded decision" (the appended acceptance criterion).** Owner: #558.
  #557 ships the predicate and the decision object's shape (§F). Writing that decision into the
  run record is part of the Request/Result contract, which the contract's §4.7 assigns to #558.

Both are to be added to #558's acceptance criteria by the operator.

## Alternatives Considered

1. **Bind-mount `deploy/instances/<instance>/` (or `model-registry/`) directly into
   `run-compose.yml`.** Rejected — bind-mount sources resolve against the daemon's host
   filesystem, not the scheduler container's own copy (§D); would also require the scheduler to
   somehow see a path it has no access to for non-self instances.
2. **Store the registry in `.factory/adapter.yaml`.** Rejected — that file is clone-live,
   committed, and read fresh from the branch every run (the opposite trust posture from a
   write-protected, operator-only surface); a branch could freely edit it.
3. **Ship a selectable local stub entry now** (so #558 has something to exercise early).
   Rejected — no certified local hardware exists (#562 unbuilt); a stub that is actually
   selectable would let a dispatch route to nothing real. Test fixtures (§E) give #558/#559 the
   same shape to develop against without a false production signal.
4. **Perform a live reachability check in #557's preflight** (the issue's "unreachable service"
   criterion, read literally). Rejected/deferred — #557 is static-configuration validation only;
   #558's runtime adapter is the first component that actually dials an endpoint. #557's
   preflight validates that `base_url` is a syntactically well-formed URL, nothing more; the
   "unreachable service fails visibly" criterion is satisfied by #558's connection attempt
   failing closed into one of #238's failure classes (§4.6), not by a probe here.
5. **Validate `secret_reference`'s env-var existence at parse time.** Rejected — the scheduler
   host and the run container read different env files; a parse-time check would spuriously
   fail depending on which side parses it first.

## Open Questions (non-blocking)

- **Exact reachability semantics** are #558's to define once a real local endpoint exists to
  dial (§4.6's interim failure-class enumeration already covers "endpoint unreachable").
- **A per-subpath read-only mount** (Compose long-syntax `subpath` + `read_only`) would add
  defence in depth on top of the digest check (Requirement 8). Compose and volume-driver support
  for it is unverified in this checkout, and nothing in this spec depends on it.

## Assumptions (flagged)

- `docs/hybrid-execution-contract.md` (current `main`, post-#580 revision) was read in full at
  refinement time (2026-11-23) and is assumed stable; re-check citations if it is revised again
  before this spec is implemented.
- `scheduler.sh` (lines 49-58), `deploy/docker-compose.yml`, and `run-compose.yml` were read live
  at refinement time and are assumed representative of current `main`; these are fast-moving
  infra files — re-verify line citations at plan/implement time.
- No real `deploy/instances/*/model-registry/` directory exists on any instance today; this spec
  assumes the first one will be hand-authored by an operator only after #557 ships and only in
  support of #564's eventual canary, not as part of this ticket.
