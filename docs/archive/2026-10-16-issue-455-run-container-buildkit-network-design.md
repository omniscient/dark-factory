# Run containers can't reach target BuildKit daemon — optional build network + BuildKit passthrough

**Issue:** #455

---

## Overview / Problem Statement

`workflows/archon-dark-factory.yaml`'s `preview-up` node builds preview images through a
*remote* BuildKit daemon:

```bash
BUILDKIT_ADDR="tcp://${BUILDKIT_HOST:-buildkit}:${BUILDKIT_PORT:-1234}"
docker buildx create --name preview-remote --driver remote "$BUILDKIT_ADDR"
```

For MarketHawk that daemon is `markethawk-buildkit`, running on `markethawk_factory-network`
in MarketHawk's own `docker-compose.yml` — a network dark-factory's run containers never
join. Since the self-hosting extraction, a dispatched run container is attached only to
`run-compose.yml`'s `dark-factory-net` (runtime name
`${FACTORY_INSTANCE:-dark-factory}_dark-factory-net`), and nothing sets `BUILDKIT_HOST` /
`BUILDKIT_PORT`. `buildkit` is therefore unresolvable from inside a run container, and every
preview-classified MarketHawk ticket will fail at `docker buildx build` with a connection
timeout. This hasn't surfaced yet only because recent tickets were classified "no preview."

The fix must let a target declare its own build daemon's network and address without
hardcoding MarketHawk specifics into the factory image, and must be functionally identical to today (no new network, no new failure mode) for
instances that have no build daemon and set nothing: the self instance
(`deploy/instances/dark-factory/`) and jobfinder (an external instance, not under
`deploy/instances/`).

## Requirements

1. A dispatched run container can join an additional, target-declared external Docker
   network carrying the target's BuildKit daemon, controlled by a new scheduler-side
   variable (`FACTORY_BUILD_NETWORK`).
2. `BUILDKIT_HOST` / `BUILDKIT_PORT` reach the run container's environment from the
   scheduler's `instance.env`, so `preview-up`'s existing `${BUILDKIT_HOST:-buildkit}:
   ${BUILDKIT_PORT:-1234}` expression resolves to the target's real daemon when set. They
   reach the scheduler process through its existing `env_file:` entry (no
   `deploy/docker-compose.yml` change; see Architecture).
3. **Functionally identical when unset.** An instance that sets none of the three new
   variables (self, jobfinder today) dispatches as it does now: no new network to
   pre-create, no new failure mode. The only rendered differences in
   `docker compose -f run-compose.yml --profile factory config` are:
   - a second service network key `build-net`, declared `external: true`, whose name is
     the same network as `dark-factory-net` (`${FACTORY_INSTANCE:-dark-factory}_dark-factory-net`);
   - `BUILDKIT_HOST: buildkit` and `BUILDKIT_PORT: "1234"` in the service environment,
     which equal today's in-workflow fallbacks.
4. No socket-proxy permission change. Container create with a networking config is
   `POST` + `CONTAINERS`; the external-network lookup and any network connect are
   `NETWORKS` + `POST`. All are already granted at `deploy/docker-compose.yml:45-53`;
   `BUILD`/`EXEC` stay 0.
5. `deploy/instance.env.example` documents the three new variables, the
   `FACTORY_INSTANCE` normalization constraint (Architecture), and the network pre-check
   (Operational consequences).
6. `docs/onboarding-new-target.md` gains a short step covering the build-daemon /
   preview-network variables, scoped so it reads as optional (most targets have no
   build daemon and skip it).
7. A regression test proves the unset shape (exactly the differences in Requirement 3)
   and the set (MarketHawk) shape via `docker compose config`, plus a lockstep assertion
   that the self-referential default stays tied to the project name.
8. Files touched: `run-compose.yml`, `deploy/instance.env.example`,
   `docs/onboarding-new-target.md`, `README.md`, new `tests/test_build_network_compose.sh`,
   one assertion in `tests/test_run_compose.sh`, one `run:` line in
   `.github/workflows/ci.yml`. Nothing else: no `deploy/docker-compose.yml`,
   `scheduler.sh` or `Dockerfile` change.
9. Out of scope (per the operator's 2026-09-28 refinement note, reaffirmed here):
   - Rewiring `preview-up` / `close-preview` to delegate to the target's own
     `.factory/hooks/preview-up` instead of inlining MarketHawk's buildx/compose logic
     — tracked separately as #15.
   - Any change to socket-proxy permissions, gates, breaker, or budgets.
   - Editing `deploy/instances/**` (human-only; MarketHawk's own `instance.env` gets the
     new variables as the MarketHawk orchestrator's follow-up after this merges).
   - A scheduler startup warning when `FACTORY_BUILD_NETWORK` names a missing network:
     that needs a `scheduler.sh` change and is a separate owner decision.

## Architecture / Approach

### Compose changes (`run-compose.yml` only)

`run-compose.yml`'s `dark-factory` service gains a second network and two more
pass-through environment entries, following the file's existing pattern (every
`FACTORY_*` var is listed explicitly, most with a MarketHawk-parity default;
`FACTORY_RUN_PREFIX` defaults to empty):

```yaml
services:
  dark-factory:
    environment:
      # ...existing FACTORY_* entries...
      BUILDKIT_HOST: ${BUILDKIT_HOST:-buildkit}
      BUILDKIT_PORT: ${BUILDKIT_PORT:-1234}
    networks:
      - dark-factory-net
      - build-net

networks:
  dark-factory-net:
    driver: bridge
  build-net:
    external: true
    name: ${FACTORY_BUILD_NETWORK:-${FACTORY_INSTANCE:-dark-factory}_dark-factory-net}
```

`BUILDKIT_HOST`/`BUILDKIT_PORT` defaults reproduce today's hardcoded values
(`buildkit`, `1234`), so `preview-up`'s own `${BUILDKIT_HOST:-buildkit}` fallback is now
redundant-but-harmless belt-and-suspenders, not load-bearing, for any instance that
leaves the new vars unset.

**The no-op default for the new network is self-referential**, not a separate
placeholder: when `FACTORY_BUILD_NETWORK` is unset (or empty: compose's `:-` treats both
alike), `build-net` resolves to the same runtime name as the already-declared
`dark-factory-net`. This was chosen over a dedicated empty placeholder network because
`external: true` networks are never auto-created by compose; a placeholder would
require new scheduler-side provisioning, which is itself a new failure mode for every
instance, including self. Verified with Compose v5.1.3 `config`: unset, both keys render
`name: dark-factory_dark-factory-net` with no duplicate-key error; set to
`markethawk_factory-network`, `build-net` renders that name.

The default is correct only under three conditions, all true in production today:

1. **The network already exists before any dispatch.** `deploy/docker-compose.yml`
   uses the same project name (`name: ${FACTORY_INSTANCE:-dark-factory}`, line 1) and
   the same network key (`dark-factory-net`, lines 122-124), so the scheduler stack
   creates `<instance>_dark-factory-net` when it comes up, and the external lookup at
   dispatch always finds it.
2. **`FACTORY_INSTANCE` is already a normalized compose project name**
   (`[a-z0-9][a-z0-9_-]*`). Compose lower-cases and strips the project name, but not the
   interpolated `name:` string: with `FACTORY_INSTANCE=MarketHawk`, `dark-factory-net`
   renders `markethawk_dark-factory-net` while `build-net` renders
   `MarketHawk_dark-factory-net`, a network that does not exist, and every dispatch would
   fail. Today's values (`dark-factory-self`; MarketHawk omits it and gets `dark-factory`)
   are safe. `deploy/instance.env.example` states the constraint and a test pins it.
3. **No project-name override.** `-p` or `COMPOSE_PROJECT_NAME` renames
   `dark-factory-net` but not `build-net`'s default. `dispatch()` (`scheduler.sh:377`)
   passes neither.

### Scheduler environment: no `deploy/docker-compose.yml` change

No `deploy/docker-compose.yml` change: `env_file: instance.env`
(`deploy/docker-compose.yml:70-76`) already exports these variables into the scheduler
process environment that `dispatch()` (`scheduler.sh:377`) interpolates
`run-compose.yml` from. An `environment:` passthrough would override that, and would
blank the values whenever compose is started without a matching `--env-file`
(`environment:` wins over `env_file:`, and `${BUILDKIT_HOST:-}` interpolates to `""`;
reproduced with `docker compose config`). The operator note asked for passthrough "the
way `FACTORY_INSTANCE` is passed (`:83`)"; this spec departs from that on purpose.
`FACTORY_INSTANCE` is listed there only because `instance.env` may omit it
(`:80-82`). Dropping the change narrows scope and keeps a `critical_diff_paths` file
(`^deploy/`) out of the diff.

### Task 0 (the plan's first task): verify duplicate network attachment at container create

`docker compose config` proves the YAML renders. It does not prove that Docker accepts
one network attached under two compose keys at container create. Before any other
task, the implementer runs:

```bash
docker compose -f run-compose.yml --profile factory run --rm --no-deps \
  --entrypoint /bin/true dark-factory
```

with `FACTORY_BUILD_NETWORK` unset, no `-p` and no `COMPOSE_PROJECT_NAME`, in a project
whose `${FACTORY_INSTANCE}_dark-factory-net` already exists (as it does in production).
`--entrypoint /bin/true` is mandatory: without it the image's `entrypoint.sh` would treat
the argument as a workflow request and start a real, billed factory run. Remove any
resources the check creates. Record the command and its result in the PR description.

- **Pass:** continue with the design above.
- **Create fails because the same network is attached under two keys:** the
  implementer STOPS and reports the evidence on the issue. It does not switch designs
  headless.

The single-file design is the only design in scope. If Task 0 fails, the ticket returns
to the operator with the evidence and a follow-up spec decides. No `scheduler.sh` or
`Dockerfile` touches.

### Test

New `tests/test_build_network_compose.sh`, shaped like the existing
`tests/test_model_proxy_compose.sh` (grep checks; skip the docker-dependent assertions
cleanly if docker is unavailable; provision the same scratch `.archon/.env`):

- `FACTORY_BUILD_NETWORK`/`BUILDKIT_HOST`/`BUILDKIT_PORT` unset: `docker compose -f
  run-compose.yml --profile factory config` shows exactly the differences in
  Requirement 3 and nothing else in the networks: the service lists exactly
  `build-net` and `dark-factory-net`; both top-level entries render the same name
  (`dark-factory_dark-factory-net`), `build-net` with `external: true`; and
  `BUILDKIT_HOST`/`BUILDKIT_PORT` render `buildkit`/`"1234"`.
- Variables set (MarketHawk-shaped values): `build-net` renders
  `markethawk_factory-network`, and the overridden `BUILDKIT_HOST`/`BUILDKIT_PORT` values
  appear.

`tests/test_run_compose.sh` gains one lockstep assertion next to check 3c: the literal
`name: ${FACTORY_BUILD_NETWORK:-${FACTORY_INSTANCE:-dark-factory}_dark-factory-net}`
must be present in `run-compose.yml`, so the default stays tied to the
`name: ${FACTORY_INSTANCE:-dark-factory}` project line that 3c already pins.

The new test is added as its own `- run: bash tests/test_build_network_compose.sh` line
in `.github/workflows/ci.yml`'s `tests` job (ubuntu-latest, next to the other compose
tests), not the `docker-build` job, which only builds the image and asserts nothing
about compose config. The bash tests cannot run on the Windows host; CI is where they
run.

### Docs

- `deploy/instance.env.example`: a new optional block documenting
  `FACTORY_BUILD_NETWORK`, `BUILDKIT_HOST` and `BUILDKIT_PORT`, explicit that they are
  empty/no-op unless the target has its own build daemon. It also carries:
  - the constraint sentence on `FACTORY_INSTANCE`: "must already be a normalized compose
    project name (`[a-z0-9][a-z0-9_-]*`); otherwise the build-network default points at a
    network that does not exist and every dispatch fails";
  - the pre-check line: "the network must exist before the scheduler dispatches; check
    with `docker network inspect $FACTORY_BUILD_NETWORK`".
- `docs/onboarding-new-target.md`: a short new step ("build daemon / preview network")
  near the existing env-file step (step 4), explicit that targets with no build daemon
  skip it, with the same `docker network inspect` pre-check.
- `README.md`: the hooks/preview section's `preview-up` row gets a one-line mention
  that the two BuildKit variables exist for targets that use the built-in remote-daemon
  preview path (without re-describing the whole preview mechanism, which is
  MarketHawk-specific and tracked by #15).

## Operational consequences

- **When `FACTORY_BUILD_NETWORK` is set, the external network becomes a hard dependency
  for all dispatches of that instance**, not only preview tickets. If it is missing (for
  example, the target's stack was taken down with `docker compose down`), every
  `docker compose run` fails: refine, plan, implement and Fix main alike. Each failure is
  logged as `dispatch_error` (`scheduler.sh:377-381`); repeated failures trip the breaker
  ("repeated dispatch failure") and move tickets to Blocked. That is loud but drastic.
  Operators check first with `docker network inspect $FACTORY_BUILD_NETWORK`
  (documented in `deploy/instance.env.example` and onboarding).
- **When the network exists but BuildKit is down, or the variables are left unset on
  MarketHawk,** `preview-up` still degrades to `preview_fail` "buildx build … failed",
  which posts an issue comment with the build-log tail but does not name the network or
  address. Improving that message belongs to #15.
- **No scheduler startup warning is added here.** A warning for a missing
  `FACTORY_BUILD_NETWORK` needs a `scheduler.sh` change and is a separate owner decision.

## Alternatives Considered

1. **Dedicated empty placeholder network (option (b) from the issue's suggested fix),
   provisioned ahead of time.** Rejected: an `external: true` network is never
   auto-created by compose, so this needs new scheduler-side provisioning logic (e.g.
   `scheduler.sh` running `docker network create <name> || true` at startup) — a new
   moving part and a new failure mode (a missing placeholder blocks *every* dispatch,
   including self) in exchange for no clear benefit over the self-referential default.
2. **Rewire `preview-up`/`close-preview` to delegate to the target's own
   `.factory/hooks/preview-up`.** This is the "real" fix for the self target's
   misleading preview-up behavior (today the self target runs MarketHawk's inlined
   buildx/compose code verbatim) and would make the network/BuildKit wiring entirely
   target-owned. Rejected for this ticket: it's a materially larger, differently-risked
   change (rewriting a load-bearing DAG node's delegation model) already tracked as #15;
   bundling it here would violate scope discipline for what is otherwise a narrow
   compose/env fix.
3. **An override compose file added with `-f` only when `FACTORY_BUILD_NETWORK` is set.**
   Out of scope: it needs a `scheduler.sh` conditional and a `Dockerfile` `COPY` (the
   image copies only `run-compose.yml`, `Dockerfile:144`). If Task 0 fails, a follow-up
   spec decides whether to take this route.
4. **`environment:` passthrough in `deploy/docker-compose.yml`** (the operator note's
   suggested shape). Rejected: redundant with `env_file:`, and it blanks the values when
   compose is started without a matching `--env-file` (see Architecture).

## Open Questions (non-blocking)

- Whether Docker's container-create path accepts one network attached under two compose
  keys is not verified by `config`. Task 0 resolves it before any other work; a failure
  stops the ticket and returns it to the operator.
- A scheduler startup warning for a missing `FACTORY_BUILD_NETWORK` is a separate owner
  decision (needs `scheduler.sh`).

## Follow-up for the MarketHawk side

- Set `FACTORY_BUILD_NETWORK` and `BUILDKIT_HOST=markethawk-buildkit` in
  `deploy/instances/markethawk/instance.env`. That is the MarketHawk orchestrator's
  follow-up after this merges, per the operator note; it is not part of this ticket.
- Verification (the issue's acceptance, restated): from a MarketHawk run container,
  `getent hosts "$BUILDKIT_HOST"` resolves and
  `docker buildx create --driver remote tcp://$BUILDKIT_HOST:$BUILDKIT_PORT` succeeds.
- Consider a dedicated build-only network for `markethawk-buildkit` and use it as
  `FACTORY_BUILD_NETWORK`. Joining `markethawk_factory-network` gives every MarketHawk run
  container network reach to all of MarketHawk's services on that network, not just
  BuildKit.

## Assumptions (flagged)

- `markethawk-buildkit`'s actual reachable address and port (`markethawk-buildkit:1234`)
  are assumed unchanged from the issue's evidence; this spec does not re-verify
  MarketHawk's own compose file (out of this repo).
- The socket proxy's existing `CONTAINERS/IMAGES/POST/NETWORKS/VOLUMES` grants are
  sufficient for the extra network attachment, per a read of
  `deploy/docker-compose.yml:45-56`. Task 0 exercises this live against the real proxy.
