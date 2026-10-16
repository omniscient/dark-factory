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
hardcoding MarketHawk specifics into the factory image, and must be a true no-op for
instances (self, jobfinder) that have no build daemon and set nothing.

## Requirements

1. A dispatched run container can join an additional, target-declared external Docker
   network carrying the target's BuildKit daemon, controlled by a new scheduler-side
   variable (`FACTORY_BUILD_NETWORK`).
2. `BUILDKIT_HOST` / `BUILDKIT_PORT` reach the run container's environment from the
   scheduler's `instance.env`, so `preview-up`'s existing `${BUILDKIT_HOST:-buildkit}:
   ${BUILDKIT_PORT:-1234}` expression resolves to the target's real daemon when set.
3. **Zero new ops burden when unset.** An instance that sets none of the three new
   variables (self, jobfinder today) must dispatch exactly as it does now — no new
   network to pre-create, no new failure mode, byte-identical behavior.
4. No socket-proxy permission change. Attaching a network at container-create time is
   `POST /containers/create` with a `NetworkingConfig` block, already covered by the
   scheduler proxy's existing `POST:1` + `NETWORKS:1`.
5. `deploy/instance.env.example` documents the three new variables.
6. `docs/onboarding-new-target.md` gains a short step covering the build-daemon /
   preview-network variables, scoped so it reads as optional (most targets have no
   build daemon and skip it).
7. A regression test proves both the unset (today's) shape and the set (MarketHawk)
   shape render correctly via `docker compose config`.
8. Out of scope (per the operator's 2026-09-28 refinement note, reaffirmed here):
   - Rewiring `preview-up` / `close-preview` to delegate to the target's own
     `.factory/hooks/preview-up` instead of inlining MarketHawk's buildx/compose logic
     — tracked separately as #15.
   - Any change to socket-proxy permissions, gates, breaker, or budgets.
   - Editing `deploy/instances/**` (human-only; MarketHawk's own `instance.env` gets the
     new variables as the MarketHawk orchestrator's follow-up after this merges).

## Architecture / Approach

### Compose changes

`run-compose.yml`'s `dark-factory` service gains a second network and two more
pass-through environment entries, following the file's existing pattern (every
`FACTORY_*` var is listed explicitly with a MarketHawk-parity default):

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
(`buildkit`, `1234`) byte-for-byte, so `preview-up`'s own `${BUILDKIT_HOST:-buildkit}`
fallback is now redundant-but-harmless belt-and-suspenders, not load-bearing, for any
instance that leaves the new vars unset.

**The no-op default for the new network is self-referential**, not a separate
placeholder: when `FACTORY_BUILD_NETWORK` is unset, `build-net` resolves to the exact
same runtime name as the already-declared `dark-factory-net`
(`${FACTORY_INSTANCE:-dark-factory}_dark-factory-net`). This was chosen over a
dedicated empty placeholder network because `external: true` networks are never
auto-created by compose — a placeholder would require new scheduler-side provisioning
(e.g. an idempotent `docker network create` added somewhere in `scheduler.sh`'s
startup), which is itself a new failure mode for every instance, including self.
`docker compose config` was verified directly against this shape (Compose v5.5.1):
both network keys resolve to the literal identical name string with no duplicate-key
error.

`deploy/docker-compose.yml`'s `backlog-scheduler` service passes the three new
variables from `instance.env` into its own `environment:` block the same way
`FACTORY_INSTANCE` already is (line 83) — `instance.env`'s `env_file:` entry alone
is not sufficient, because `dispatch()` (`scheduler.sh:377`, the `docker compose run`
call against the baked `run-compose.yml`) resolves `${VAR}` interpolation from the
scheduler container's own process environment, not by re-reading `instance.env` from
disk:

```yaml
environment:
  FACTORY_INSTANCE: ${FACTORY_INSTANCE:-dark-factory}
  FACTORY_BUILD_NETWORK: ${FACTORY_BUILD_NETWORK:-}
  BUILDKIT_HOST: ${BUILDKIT_HOST:-}
  BUILDKIT_PORT: ${BUILDKIT_PORT:-}
```

Leaving these three empty-string defaults here (rather than repeating MarketHawk's
values) keeps `deploy/docker-compose.yml` target-agnostic; the real defaults live in
`run-compose.yml` as shown above, and compose's `:-` semantics treat an explicitly
empty variable the same as unset, so the fallback chain still resolves correctly.

### Decide-in-advance contingency (headless constraint)

`docker compose config` proves the YAML renders; it cannot prove the self-referential
network attaches cleanly at container-create time (does Docker accept two
network-attachment keys that resolve to the same physical network, or does the second
attach fail with an "endpoint already exists" error?). Two independent live attempts
to check this directly (`docker compose run` against a fresh project, var unset) were
both blocked by a 403 from an administrative/socket-proxy-style restriction in the
sandbox each check ran in — `docker network create` itself works fine in the same
sandbox, so the restriction is specific to container creation, not networks.

Because the implement phase runs headless (no human to ask if this turns out broken),
the decision rule is fixed here, in advance:

1. Implementation must run a real
   `docker compose -f run-compose.yml --profile factory run --rm --no-deps dark-factory true`
   against a fresh compose project, `FACTORY_BUILD_NETWORK` unset, and record the
   result (pass/fail, or "blocked by proxy restriction — treat as fail") in the PR
   description.
2. **If it passes:** keep the self-referential default as specified above.
3. **If it fails (or can't run and is treated as a fail):** switch to a split-file
   fallback instead of a provisioned placeholder network — move the `build-net`
   network/attachment into a small override compose file, e.g.
   `run-compose.build-net.yml`, and have `dispatch()` add it via `-f` only when
   `FACTORY_BUILD_NETWORK` is non-empty (mirroring how `factory-model-proxy` is
   opted into via `--profile` today). When the variable is unset, `dispatch()` omits
   the `-f` flag entirely and nothing extra is attached — a different mechanism for
   the same "zero new ops burden when unset" requirement. This is a `scheduler.sh`
   change but not a gate/budget/proxy change, and stays within the operator note's
   scope either way.

Either branch satisfies Requirements 1–4; the test in both branches must assert the
unset case renders byte-identically to today.

### Test

New `tests/test_build_network_compose.sh`, shaped like the existing
`tests/test_model_proxy_compose.sh` (grep checks; skip the docker-dependent assertions
cleanly if docker is unavailable; provision the same scratch `.archon/.env`):

- `FACTORY_BUILD_NETWORK`/`BUILDKIT_HOST`/`BUILDKIT_PORT` unset: `docker compose -f
  run-compose.yml --profile factory config` resolves the service's two network
  attachments to the identical name (or, under the fallback branch, to only
  `dark-factory-net`), and `BUILDKIT_HOST`/`BUILDKIT_PORT` resolve to `buildkit`/`1234`.
- Variables set (MarketHawk-shaped values): the named external network and the
  overridden `BUILDKIT_HOST`/`BUILDKIT_PORT` values appear in `config` output.

Added as its own `- run: bash tests/test_build_network_compose.sh` line in
`.github/workflows/ci.yml`'s `tests` job, next to the other compose tests — not the
`docker-build` job, which only builds the image and asserts nothing about compose
config.

### Docs

- `deploy/instance.env.example`: new optional block documenting `FACTORY_BUILD_NETWORK`,
  `BUILDKIT_HOST`, `BUILDKIT_PORT`, explicit that these are empty/no-op unless the
  target has its own build daemon.
- `docs/onboarding-new-target.md`: a short new step ("build daemon / preview network")
  near the existing env-file step (step 4), explicit that it's skippable for targets
  with no build daemon.
- `README.md`: the hooks/preview section's `preview-up` row gets a one-line mention
  that the two BuildKit variables exist for targets that use the built-in remote-daemon
  preview path (without re-describing the whole preview mechanism, which is
  MarketHawk-specific and tracked by #15).

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
3. **Make the override-compose-file split (the contingency above) the primary design
   from the start, skipping the live-check step.** Rejected: it's strictly more
   machinery (a second compose file, a `scheduler.sh` conditional) for a problem the
   self-referential default may well not have. The decide-in-advance rule gets the
   simpler fix tried first without leaving a headless implementation agent stuck
   without a rule to follow if it doesn't work.

## Open Questions (non-blocking)

- Whether Docker's container-create API actually dedupes two network-attachment
  entries resolving to the same physical network, or rejects the second one, is
  unverified as of this spec (two independent live-check attempts were both blocked by
  a sandbox-level 403 on container creation). Resolved by the decide-in-advance rule in
  Architecture above, not by this spec.
- MarketHawk's own `deploy/instances/markethawk/instance.env` edit (setting
  `FACTORY_BUILD_NETWORK=markethawk_factory-network`, `BUILDKIT_HOST=markethawk-buildkit`)
  is the MarketHawk orchestrator's follow-up after this merges, per the operator note —
  not part of this ticket's implementation.

## Assumptions (flagged)

- `markethawk-buildkit`'s actual reachable address and port (`markethawk-buildkit:1234`)
  are assumed unchanged from the issue's evidence; this spec does not re-verify
  MarketHawk's own compose file (out of this repo).
- The socket proxy's existing `CONTAINERS/IMAGES/POST/NETWORKS/VOLUMES` grants are
  assumed sufficient for the extra `NetworkingConfig` attachment; this is asserted by
  the operator note and by reading `deploy/docker-compose.yml`'s proxy environment
  block, but is not independently re-verified against a live proxy in this spec (the
  implement phase's live-check step above exercises this incidentally).
