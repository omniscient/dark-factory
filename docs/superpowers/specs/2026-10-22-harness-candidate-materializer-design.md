# Harness candidate materializer — disposable bwrap sandboxes from an immutable baseline

**Issue:** #465 · **Epic:** #463 (Add a governed recursive harness-improvement capability)
**Depends on:** #464 (closed/merged — `scripts/factory_core/mechanism_candidate.py`)

---

## Overview / Problem statement

#464 shipped a **data-and-lifecycle-only** validator for `mechanism_candidates` records: schema,
content digests, a default-deny H0–H3 risk classifier, exact-diff containment, and a one-way
lifecycle state machine. It deliberately left several declared fields **descriptive-only, never
enforced** — `baseline.*_digest`, `resource_envelope`, `permission_envelope`, `network_envelope`,
`filesystem_envelope`, `side_effect_envelope` — and said so explicitly: real enforcement is "the
responsibility of whatever sandboxing mechanism a future probe-execution ticket builds."

#465 is that ticket. Given a `mechanism_candidate.py`-valid record, it must:

1. Materialize a **real, immutable baseline** (not #464's labelled-synthetic-string fixtures) and
   compute its `repo_digest` for real.
2. Build a **disposable, credential-free workspace** from that baseline with the candidate's exact
   diff applied, constrained to its `editable_path_allowlist`.
3. **Enforce** — not merely record — the declared resource/network/filesystem/permission/
   side-effect envelopes, so that running arbitrary candidate-supplied logic inside the workspace
   cannot read credentials, reach the network, escape the workspace, or exhaust the host.
4. Produce an **immutable artifact bundle** (baseline/candidate digests, the exact diff, provenance,
   a pass/fail verdict) that later tickets (#466's proposer, #467's probe runner) and reviewers can
   trust without re-deriving it.

It explicitly does **not** decide what candidate logic to run (#466), run promotion/hidden tests
(#467), activate a candidate, or write the improvement library — matching #464's "no loop
dispatcher" scope discipline.

**Load-bearing finding from this refinement pass:** the repo has `bubblewrap` (`bwrap`)
apt-installed but never invoked anywhere, and no gVisor/firejail/nsjail/cgroup precedent. Live
verification in the actual factory phase container (the same kind of container this materializer
runs inside) shows `bwrap` **cannot create any Linux namespace today, of any kind** — not only a
user namespace:

```
$ bwrap --unshare-user --unshare-net --unshare-pid --die-with-parent \
    --ro-bind / / --tmpfs /tmp --proc /proc --dev /dev -- /bin/echo ok
bwrap: No permissions to create a new namespace, likely because the kernel does not
allow non-privileged user namespaces.

$ cat /proc/sys/kernel/unprivileged_userns_clone
cat: No such file or directory          # sysctl doesn't exist on this kernel/build
$ id
uid=1000(factory) gid=1000(factory) groups=1000(factory)   # no CAP_SYS_ADMIN
```

Making `bwrap` functional requires a `run-compose.yml`/Dockerfile container-runtime change
(a `security_opt`/seccomp-profile relaxation or an added capability) — itself a factory-container
privilege expansion and a security-sensitive-surface change per `CLAUDE.md`, requiring its own
separate, human-reviewed ticket. This spec therefore ships the materializer **inert-but-correct**:
every piece is built and tested, but in today's container every real materialize-and-run attempt
fail-closes to an "isolation unavailable" verdict rather than silently running unsandboxed. This is
intended behavior, not a defect, and is a core acceptance criterion of this ticket (see
Requirements and Alternatives Considered).

## Requirements (from Q&A)

1. **Isolation primitive: `bwrap` namespaces** (user, mount, pid, net, ipc), launched from inside
   the existing phase container. Nested Docker is rejected — it would require giving phase
   containers Docker-socket access they don't have today (the socket is scheduler-only, via
   `docker-socket-proxy-scheduler` in `deploy/docker-compose.yml`, itself locked down to
   `EXEC=0`/`BUILD=0`), which is a separate human-reviewed privilege-expansion change. gVisor is
   rejected as disproportionate host-level infra (already rejected on identical reasoning by the
   #189 spike for a far cheaper advisory-lane use case).
2. **Network: `--unshare-net`, loopback only, no route out, by construction** — not an
   allowlist/proxy. A candidate whose `network_envelope` declares `egress_allowed: true` or a
   non-empty `allowed_hosts` must be **refused fail-closed with a clear reason**, never silently
   downgraded to no-network (a silent downgrade would make the materialized run not match what the
   candidate declared, corrupting matched-probe comparability). Real egress-allowlist/scoped-proxy
   support (plausibly reusing `scripts/factory_core/model_proxy.py`'s existing transparent
   Anthropic-passthrough-plus-ledger shape, and the already-provisioned-but-unused `socat` apt
   package) is deferred — open question, below.
3. **Credential isolation by construction**: `bwrap --clearenv` plus an explicit, minimal env
   allowlist; no bind mount of `.archon/.env`, `~/.config/gh`, `~/.claude`, the original clone, or
   the `scheduler_state`/`dark_factory_state` volumes. The workspace is always a **fresh copy**
   built from the digested baseline, never a bind mount of the live clone.
4. **Filesystem**: the baseline snapshot is bound read-only; only `filesystem_envelope.write_paths`
   are writable, and all writes outside the disposable workspace/artifact directory fail.
5. **Fail-closed preflight, never a silent fallback**: before attempting any real sandbox, probe
   whether `bwrap` can create its required namespaces. If it cannot, emit a `STATUS: FAIL`-shaped
   verdict (`refinement-skills/VERIFIER-CONTRACT.md`'s shared schema: `STATUS`/`GATE_TYPE`/
   `FINDINGS_COUNT`/`SEVERITY`, canonically `scripts/factory_core/verdict.py`), citing the probe's
   stderr as the reason, and stop. **No weaker fallback tier** (e.g. `--clearenv`+rlimits+restricted
   `$PATH` alone, with no namespaces) is acceptable, even gated behind the same preflight check —
   that would leave credentials readable on disk and egress open, producing false assurance, and
   directly contradicts the issue's own framing that "the current PATH shims are defense in depth,
   not the boundary."
6. **Resource limits**: memory and CPU-time via `prlimit`/`RLIMIT_AS`/`RLIMIT_CPU`; wall-clock
   duration via `bwrap --die-with-parent` plus a parent-side timer; fork-bomb protection via
   `RLIMIT_NPROC`; output/expansion-bomb protection via `RLIMIT_FSIZE` plus a total-bytes cap
   enforced during workspace construction (an "archive bomb" is a small input that *expands*
   enormously when unpacked — the cap must be checked against expanded size, not input size).
   `resource_envelope.cpu_limit` as a core-count cannot be truly enforced without cgroups (none
   exist in this repo); treat it as best-effort via `RLIMIT_CPU`, or refuse a candidate whose
   declared limit cannot be approximated this way. A cgroup tier is a follow-up (open question).
   These ceilings live in a **new, separate** `config/config.yaml` block and are enforced inside
   `materializer.py` itself — not inside `token_optimization`, which gates factory *phase* agents,
   not sandboxed candidate runs.
7. **Generic execution primitive, not just workspace materialization.** The acceptance criteria's
   attack fixtures (credential/network/Docker-socket/path-escape/symlink/submodule/archive-bomb/
   resource-exhaustion) only mean something if *something* actually attempts each forbidden action
   inside a materialized workspace and is observed failing. #465 therefore ships both:
   - a library function, `run(candidate, argv, *, baseline, out_dir) -> Result` (exit code, rlimit
     usage, wall time, a verdict), which builds the bwrap-wrapped workspace per the candidate's
     envelopes and execs `argv` inside it — called directly by this ticket's own attack-fixture
     tests;
   - a thin `python -m factory_core.materializer` CLI (`materialize`, `run -- <argv>`
     subcommands), mirroring `mechanism_candidate.py`'s own `argparse`-CLI-over-importable-API
     shape, giving #466/#467 a stable subprocess boundary to reuse for real proposer/prober agent
     commands.
   #465 does not decide *what* candidate-logic command to run (#466/#467's job) — only that the
   boundary around *any* command is real.
8. **Baseline digest generation is real, not caller-trusted.** `repo_digest` is computed from a
   **pinned git commit** (`git ls-tree -r <commit>` plus blob contents via `git cat-file`/
   `git archive` — never a working-tree walk, because `entrypoint.sh` git-excludes run-time staged
   files into the clone per #472, so a tree walk would be non-deterministic run-to-run and the
   digest would not reproduce), reusing `mechanism_candidate.canonical_digest()` **by import, not
   copy**. The manifest maps `path -> {sha256, mode}`. A path whose mode is a symlink (120000) is
   recorded by its link target, and the record is refused if any symlink target resolves outside
   the snapshot. A gitlink (mode 160000 — a submodule) is refused outright, as is any blob whose
   content starts with the Git LFS pointer header (`version https://git-lfs.github.com/spec/v1`)
   — refuse, never attempt to flatten/fetch. (This repo has no submodules and no `git-lfs` binary
   today — these fixtures are defensive/forward-looking, e.g. for a future MarketHawk baseline, not
   exercised against this repo's own content.) The materializer recomputes `repo_digest` and
   refuses fail-closed if it does not match the candidate record's declared value. Digests this
   ticket cannot derive from real content (`model_runtime_digest`, `tool_digest`, `verifier_digest`,
   `policy_digest`, `library_digest`) are verified only for shape (#464 already does this) and
   trusted as caller-supplied, not invented.
9. **Re-validate the candidate as defense-in-depth.** Before materializing, call
   `mechanism_candidate.validate_candidate()` (imported, not re-implemented) even though a prior
   gate presumably already validated the record — closes a TOCTOU gap (a record could be mutated,
   regenerated, or the materializer invoked directly without a prior gate run).
10. **Artifact bundle is ephemeral, never committed.** Baseline/candidate digests, the exact diff,
    provenance, and the verdict are written as JSON via an `--out-dir` flag, defaulting to
    `$ARTIFACTS_DIR/materializer/<candidate_id>/` — `candidate_id` is sanitized with the same
    `^[A-Za-z0-9_-]+$` pattern `run_record.py::_run_record_path` already uses to block path
    traversal. The disposable workspace itself is a fresh `mkdtemp()` directory **outside** both the
    clone and `$ARTIFACTS_DIR`, removed on completion (success or failure) — cleanup must leave no
    candidate state in the active harness or host workspace (epic #463 acceptance criterion).
    Only small, synthetic **test fixtures** are committed to the repo, under a new
    `evals/materializer/fixtures/` directory mirroring `evals/mechanism-candidates/fixtures/`
    (manifest-driven expected-verdict pattern, same as #464's `test_fixture_manifest_expectations`).
11. **No new adapter.yaml, gate, or scheduler surface.** `adapter.py`'s `mechanism_candidates`
    top-level key stays hard-rejected (#465 adds no consumer either, same reasoning #464 used — no
    behavior to wire yet). No DAG/workflow node, no scheduler dispatch path, no board/label
    mutation is introduced — matches #464's "no loop dispatcher" scope cut.
12. **Attack fixtures must not touch real secrets or the public internet.** The credential fixture
    plants a synthetic canary secret under a temporary `HOME` and asserts the sandboxed process
    cannot read it — never pointed at the real `.archon/.env`/`~/.config/gh`. The network fixture
    targets a TEST-NET-1 address (`192.0.2.1`, RFC 5737), never a real hostname.

## Architecture / Approach

### File layout

```text
scripts/factory_core/materializer.py        # baseline digesting, workspace build, bwrap-arg
                                             # translation, run(), preflight probe, CLI
tests/test_materializer.py                  # unit tests (no real namespaces needed) +
                                             # one live bwrap integration test (skips on
                                             # preflight failure)
evals/materializer/fixtures/
  baseline-manifest.json                    # synthetic small baseline for digest tests
  candidate-*.json                          # reused/extended from mechanism-candidates shape
  attack-credential-canary.json             # expects: canary unreadable inside sandbox
  attack-network-testnet.json               # expects: connect to 192.0.2.1 fails
  attack-docker-socket.json                 # expects: no /var/run/docker.sock inside sandbox
  attack-path-escape.json                   # expects: write outside overlay fails
  attack-symlink-escape.json                # expects: symlink resolving outside baseline refused
  attack-submodule.json                     # expects: gitlink entry refused
  attack-lfs-pointer.json                   # expects: LFS pointer blob refused
  attack-archive-bomb.json                  # expects: expansion-bytes cap trips
  attack-resource-exhaustion.json           # expects: RLIMIT_NPROC/RLIMIT_FSIZE/RLIMIT_CPU trip
  manifest.json                             # expected verdict per fixture
```

`materializer.py` exposes an importable API (`compute_repo_digest(commit, repo_root)`,
`build_workspace(candidate, baseline)`, `preflight_probe()`, `run(candidate, argv, *, baseline,
out_dir)`, `materialize(candidate, *, out_dir)`) plus a small `argparse` CLI with `materialize`
and `run -- <argv>` subcommands, mirroring `mechanism_candidate.py`'s CLI-over-API shape. It
imports `mechanism_candidate` (`validate_candidate`, `canonical_digest`, `classify_risk`) and
`verdict` (`format_verdict`) — never re-implements either.

### Baseline digesting

`compute_repo_digest(commit, repo_root)` runs `git ls-tree -r <commit>` against the clone,
reads each blob's content via `git cat-file`, and builds a `{path: {sha256, mode}}` manifest
(`.git/` is never walked — it isn't part of the tree listing). Refusal rules (in order): a gitlink
entry (mode `160000`) refuses the whole record; a blob whose first line is the Git LFS pointer
header refuses; a symlink (mode `120000`) whose target, resolved against the snapshot root, falls
outside it refuses. The manifest is hashed via `mechanism_candidate.canonical_digest()` to produce
`repo_digest`, compared against the candidate's declared `baseline.repo_digest` — a mismatch fails
closed before any workspace is built.

### Workspace construction

A read-only snapshot is materialized from the pinned-commit tree (`git archive <commit> | tar -x`
into a fresh `mkdtemp()` directory) — never the live working tree. The candidate's `diff` (already
containment-checked by #464 against `editable_path_allowlist`) is applied with `git apply` inside
that snapshot to produce the candidate workspace; `git apply --check` first, so a diff that
doesn't cleanly apply against this exact baseline fails closed rather than partially mutating the
workspace. `filesystem_envelope.write_paths` controls which paths bwrap mounts read-write inside
the sandbox; everything else is read-only.

### Isolation: bwrap invocation

`build_workspace()`/`run()` translate the candidate's envelopes into `bwrap` arguments:

- `--unshare-user --unshare-pid --unshare-ipc --unshare-net --die-with-parent`
- `--clearenv` + `--setenv` for an explicit minimal allowlist (`PATH`, `HOME` pointed at a fresh
  empty dir inside the workspace, `TMPDIR`) — never `GH_TOKEN`, `ANTHROPIC_*`, or any
  `.archon/.env` value.
- `--ro-bind <snapshot> <snapshot>` for the baseline, `--bind <path> <path>` for each
  `filesystem_envelope.write_paths` entry, `--tmpfs /tmp`, `--proc /proc`, `--dev /dev` (a
  minimal synthetic `/dev`, not a bind of the host's).
- No `--bind` of `/var/run/docker.sock`, `~/.config/gh`, `~/.claude`, `.archon/`, or any credential
  path, ever — enforced by construction (these are simply never in the bind-mount list the
  translator can produce), not by a denylist check.
- `prlimit`-wrapped exec for `RLIMIT_AS` (memory), `RLIMIT_CPU`, `RLIMIT_NPROC` (fork-bomb),
  `RLIMIT_FSIZE` (single-file size); a parent-side wall-clock timer enforces
  `max_duration_seconds` by killing the `bwrap` process group.

### Preflight probe and fail-closed refusal

`preflight_probe()` attempts the minimal `bwrap --unshare-user --unshare-pid --unshare-net
--die-with-parent -- true` invocation in a throwaway temp dir before any real materialization.
On failure (the documented, currently-universal case in this repo's container), `materialize()`
and `run()` both short-circuit to `format_verdict(gate_type="materializer", status="FAIL",
findings_count=1, severity="high")` with the probe's stderr recorded as the reason, and perform no
further action — no workspace is built, no partial artifact is written as if it succeeded. This
path is unit-tested directly (mock/force the probe to fail) and is also what every attack-fixture
test exercises as the actual verdict in CI today: the *generated bwrap argv and rlimit values*
are what the tests assert against (e.g. "the docker socket path never appears in any `--bind`
argument"; "the credential-path allowlist excludes `.archon/.env`"), not a live sandboxed
execution, except for the one integration test below.

### Live integration test

One `tests/test_materializer.py` test actually invokes `bwrap` for real and asserts a trivial
sandboxed command succeeds when the preflight probe passes, skipping cleanly
(`pytest.mark.skipif`) when it doesn't — so `python -m pytest tests/ -v` stays green in this
repo's current container while the test still exercises real isolation in any environment where
the future follow-up ticket has enabled namespace creation.

### Execution primitive

```text
run(candidate, argv, *, baseline, out_dir) -> Result(
    exit_code, wall_seconds, max_rss_bytes, verdict,
)
```

Builds the workspace, execs `argv` inside the bwrap sandbox, tears down the workspace afterward
regardless of outcome, and writes the artifact bundle (below) to `out_dir`. The CLI's `run --
<argv>` subcommand is a thin wrapper. #466/#467 call this same function/subcommand to run real
proposer/prober agent commands; #465 does not know or care what `argv` contains.

### Artifact bundle

```json
{
  "candidate_id": "...",
  "candidate_digest": "sha256:...",
  "baseline": { "commit": "...", "repo_digest": "sha256:...", "...": "..." },
  "diff": "<the exact unified diff, copied from the candidate record>",
  "materialized_at": "2026-10-22T00:00:00Z",
  "verdict": { "status": "FAIL", "gate_type": "materializer", "findings_count": 1,
               "severity": "high", "reason": "bwrap: No permissions to create a new namespace..." }
}
```

Written once per `materialize()`/`run()` call to `$ARTIFACTS_DIR/materializer/<candidate_id>/
bundle.json` (sanitized `candidate_id`), never committed.

### Config

A new `materializer:` top-level block in `config/config.yaml` (sibling to `token_optimization:`,
not nested inside it): default `memory_limit_mb`, `max_duration_seconds`, `max_workspace_bytes`
ceilings, overridable per-candidate only downward (a candidate cannot declare a *larger* limit than
the config ceiling — enforced in `materializer.py`, mirroring `budget_enforce.py`'s
declare-in-YAML/enforce-in-module shape).

## Alternatives considered

- **Nested Docker containers.** Rejected — requires giving phase containers Docker-socket access
  they don't have today (currently scheduler-only, and even there capped to `EXEC=0`/`BUILD=0`); a
  privilege expansion needing its own human-reviewed ticket, not something this spec can authorize.
- **gVisor (`runsc`) kernel-isolation tier.** Rejected — already rejected by the #189 spike as
  disproportionate host-level infra (`sudo`, Linux-only runtime install) for a far cheaper
  read-only advisory-lane use case; no stronger case exists here for an L-size ticket.
- **Bundling the `run-compose.yml`/Dockerfile container-runtime change into this ticket so the
  materializer is functional on merge.** Rejected — widens the factory container's own privileges,
  a security-sensitive-surface change per `CLAUDE.md`; the verified total non-function (not just a
  degraded mode) makes this a bigger change, not a smaller one, and still doesn't belong in an
  autonomously-dispatched ticket.
- **A weaker non-namespace fallback (`--clearenv` + rlimits + restricted `$PATH`, no bwrap
  namespaces) used whenever bwrap is unavailable.** Rejected unconditionally, even gated behind the
  same preflight check — credentials stay readable on disk and egress stays open, which is false
  assurance, worse than an honest refusal, and directly regresses the issue's own stated framing
  that "the current PATH shims are defense in depth, not the boundary."
- **Stopping at workspace-materialization-only, with no `run()`/CLI execution primitive**, leaving
  attack-fixture verification and all execution to #466/#467. Rejected — the acceptance criteria's
  attack fixtures (credential/network/docker-socket/etc. all "blocked") are unfalsifiable without
  something that actually attempts the forbidden action; #465 must own the boundary it claims to
  build, even though it stays agnostic to *what* real candidate logic eventually runs through it.
- **Computing `repo_digest` from a live working-tree walk.** Rejected — `entrypoint.sh` stages
  run-time files into the clone (git-excluded, #472), so a tree walk is non-deterministic run to
  run; a pinned-commit `git ls-tree`/`git archive` read is deterministic and reproducible.

## Open questions (non-blocking)

- **Enabling infra change.** The follow-up ticket that makes `bwrap` namespace creation actually
  possible in the factory container should weigh a narrow grant (a custom seccomp/AppArmor profile
  permitting only the needed `unshare`/`clone3`/`mount` syscalls) against a broad one
  (`security_opt: ["seccomp=unconfined"]`, `privileged: true`, or `CAP_SYS_ADMIN`) — that choice,
  and its own security review, belongs to that ticket, not this one. This spec's verified probe
  output (above) is the evidence it should start from.
- **Real network egress allowlist / scoped model proxy.** Deferred until #466/#467 actually need a
  materialized sandbox to reach the model API — likely reusing `model_proxy.py`'s existing
  transparent-passthrough-plus-ledger shape and the unused `socat` apt package for a narrow
  port-forward from the sandbox's otherwise-isolated network namespace, rather than a bespoke
  proxy.
- **cgroup-based CPU/memory enforcement.** rlimits are coarser than cgroups (e.g. `RLIMIT_CPU` is
  wall-clock-adjacent CPU-seconds, not a true core-count ceiling); a cgroup v2 tier is a follow-up
  if rlimits prove too imprecise in practice.
- **Cooldown/TTL on repeated materialization attempts** for the same candidate — not addressed;
  mirrors #464's identical open question about re-proposal after quarantine.

## Assumptions (flagged)

- **The materializer does not emit `mechanism_lineage` events or mutate a candidate's
  `lifecycle_state`.** It only re-validates (#464's `validate_candidate`) and materializes; lineage
  advancement (e.g. the `-> probed` transition) is left to whichever ticket actually performs the
  probe (#467), consistent with the epic's state-machine ownership and #464's "no loop dispatcher"
  cut. Neither the issue body nor the Q&A settles this explicitly — flagged for spec review.
- **This repo has no submodules and no `git-lfs` binary today** (verified: `.gitmodules` absent,
  `git lfs` unrecognized) — the submodule/LFS refusal rules and fixtures are defensive/
  forward-looking (e.g. for a future MarketHawk baseline), not exercised against this repo's real
  content in CI.
- **In the current container, every live `materialize()`/`run()` call fails closed to an
  "isolation unavailable" verdict** — this is the ticket's intended, accepted delivered state, not
  a partial implementation; the mechanism becomes live only once the separate follow-up ticket
  changes the container runtime's permitted namespace operations.
