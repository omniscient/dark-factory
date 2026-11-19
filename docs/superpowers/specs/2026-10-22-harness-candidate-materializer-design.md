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
runs inside) shows `bwrap` **cannot create any Linux namespace today, of any kind**:

```
$ bwrap --unshare-user --unshare-net --unshare-pid --die-with-parent \
    --ro-bind / / --tmpfs /tmp --proc /proc --dev /dev -- /bin/echo ok
bwrap: No permissions to create a new namespace, likely because the kernel does not
allow non-privileged user namespaces.
```

This is Docker's default seccomp profile blocking `clone`/`unshare`, not the Debian
`unprivileged_userns_clone` sysctl the first draft of this spec cited (that sysctl doesn't exist on
this kernel/build at all — a red herring, not the actual cause). Making `bwrap` functional inside
the **production** phase container still requires a `run-compose.yml`/Dockerfile container-runtime
change — a factory-container privilege expansion and a security-sensitive-surface change the
candidate contract's own H3 classification already protects (`mechanism_candidate.py`'s
`H3_PREFIXES` and `.factory/adapter.yaml`'s `safety.critical_diff_paths` both name
`run-compose.yml`/`Dockerfile`/`deploy/` explicitly) — requiring its own separate, human-reviewed
ticket.

**Round 2 (this revision):** an operator design review (issue comment, 2026-10-22) found that the
first draft's "ships inert-but-correct, never really exercised" framing hid two blocking gaps —
the boundary was never exercised anywhere, including CI, and the one live probe used in that draft
(`--ro-bind / /`) would have exposed host credentials had it ever run for real. The ticket owner's
decisions (issue comment, 2026-11-19) resolve both:

- **Decision 1 = (a):** add a CI-only job that builds the real image and runs
  `tests/test_materializer.py` inside a container started with
  `--security-opt seccomp=unconfined --security-opt apparmor=unconfined` and
  `MATERIALIZER_REQUIRE_LIVE=1` (turning the live-test skip into a hard failure), on every PR. This
  exercises the real `bwrap` namespace boundary on every change **without** touching
  `run-compose.yml` or any production phase-container privilege — it is a new CI job only. The
  production phase container keeps failing closed exactly as before.
- **Decision 2 = agree with proposal:** split the API surface into a **materialize** half (H0–H2,
  fully unit-testable without real namespaces) and a **run** half (H0–H1 only, needs the real
  `bwrap` boundary; refuses H2 with a `BLOCKED` verdict pending #467's quarantine design). See
  "Implementation sequencing" below for how this spec structures that split without this refine
  phase creating new tracker issues (out of its authorized scope).

This spec therefore now ships two things: the materializer itself (inert-but-correct in the
production container, exactly as the first draft specified), **and** a CI job that gives the
boundary a real, PR-gated execution path for the first time. See "Open questions" for the one
empirical assumption this new CI job rests on that this spec cannot verify by itself.

## Requirements (from Q&A)

1. **Isolation primitive: `bwrap` namespaces** (user, mount, pid, net, ipc), launched from inside
   the existing phase container. Nested Docker is rejected — it would require giving phase
   containers Docker-socket access they don't have today: the socket (`/var/run/docker.sock`) is
   bind-mounted read-only only into `docker-socket-proxy-scheduler`
   (`deploy/docker-compose.yml`), whose ACL already denies `EXEC`/`BUILD`/`AUTH`/`SECRETS`; the run
   container only ever reaches Docker indirectly via the `DOCKER_HOST`/`BUILDKIT_HOST` env vars
   (`run-compose.yml`), never a socket bind mount. Widening that is a separate human-reviewed
   privilege-expansion change. gVisor is rejected as disproportionate (already rejected on
   identical reasoning by the #189 spike for a far cheaper advisory-lane use case).
2. **Network: `--unshare-net`, loopback only, no route out, by construction** — not an
   allowlist/proxy. A candidate whose `network_envelope` declares `egress_allowed: true` or a
   non-empty `allowed_hosts` must be **refused fail-closed with a clear reason**, never silently
   downgraded to no-network. Real egress-allowlist/scoped-proxy support (plausibly reusing
   `scripts/factory_core/model_proxy.py`'s existing transparent Anthropic-passthrough-plus-ledger
   shape, and the already-provisioned-but-unused `socat` apt package) is deferred — open question,
   below.
3. **Credential isolation by construction**: `bwrap --clearenv` plus an explicit, minimal env
   allowlist (`PATH`, a fresh empty `HOME`, `TMPDIR`) — never `GH_TOKEN`, `ANTHROPIC_*`,
   `CLAUDE_CODE_OAUTH_TOKEN`, `DOCKER_HOST`, `BUILDKIT_HOST`, `BUILDKIT_PORT`, `SEQ_URL`, or
   `SSH_AUTH_SOCK` (round-2 addition — the first draft's credential list omitted all six). No bind
   mount of `.archon/.env`, `~/.config/gh`, `~/.claude`, `~/.gitconfig` (round-2 addition), the
   original clone, or the `scheduler_state`/`dark_factory_state` volumes. The workspace is always a
   **fresh copy** built from the digested baseline, never a bind mount of the live clone.
4. **Filesystem — explicit allowlist, never `--ro-bind / /`** (round-2 fix: the first draft's own
   verification probe used `--ro-bind / /`, which would have exposed `~/.claude`, `~/.config/gh`,
   `~/.gitconfig`, `$ARTIFACTS_DIR`, `/var/lib/dark-factory`, and the live `/workspace` clone had
   any real run ever executed it). The sandbox root is composed from an explicit bind list only:
   - `--ro-bind <snapshot> <snapshot>` — the baseline workspace built in "Workspace construction".
   - `--ro-bind /usr /usr`, `--ro-bind /bin /bin`, `--ro-bind /lib /lib`, and `--ro-bind /lib64
     /lib64` if present — the minimum needed to exec `python3`/`/bin/sh` inside the sandbox.
   - `--tmpfs /tmp`, `--proc /proc`, `--dev /dev` (a minimal synthetic `/dev`, never a bind of the
     host's).
   - `--dir <fresh-empty-dir-inside-workspace>` bound as `$HOME`.
   - `--bind <path> <path>` for each `filesystem_envelope.write_paths` entry (nothing else is
     writable).
   - Never bound, by construction (absent from the translator's possible output, not filtered by a
     denylist): `/`, `/home`, `/root`, `/etc` beyond what's pulled in under `/usr`, the real `$HOME`,
     `$ARTIFACTS_DIR`, `/var/lib/dark-factory`, the live `/workspace` clone, and any credential path.
   - `filesystem_envelope.read_paths` is **not** enforced by this ticket (see "Deferred scope",
     below) — the baseline snapshot bind above is already read-only in its entirety, so there is no
     read-path-narrowing behavior yet to build; a future ticket that needs to grant read access to
     *additional* host paths beyond the baseline can wire this field in without reworking the bind
     translator.
5. **Fail-closed preflight, never a silent fallback**: before attempting any real sandbox, probe
   whether `bwrap` can create its required namespaces. If it cannot, emit a `BLOCKED` verdict
   (round-2 fix: the current schema's canonical gating-block token — `scripts/factory_core/
   verdict.py`'s `GATING_BLOCK_STATUSES = {"BLOCKED"}`; `FAIL` is `LEGACY_STATUSES`, documented but
   not the token to emit from new code, and `ERROR` must never be used here since it is a
   pass-through/proceed status), citing the probe's stderr as the reason, and stop. **No weaker
   fallback tier** (e.g. `--clearenv`+rlimits+restricted `$PATH` alone, with no namespaces) is
   acceptable, even gated behind the same preflight check — that would leave credentials readable
   on disk and egress open, producing false assurance, and directly contradicts the issue's own
   framing that "the current PATH shims are defense in depth, not the boundary."
6. **Resource limits — per-process, not a sandbox-wide cap** (round-2 clarification: `RLIMIT_AS`
   and the other rlimits below bound one process each; a fork bomb that gets past `RLIMIT_NPROC`
   can still multiply effective memory use across many processes each under the per-process
   `RLIMIT_AS` ceiling, so `RLIMIT_NPROC` must itself be bounded to a small, config-set ceiling, not
   left unlimited). Memory and CPU-time via `prlimit`/`RLIMIT_AS`/`RLIMIT_CPU`; wall-clock duration
   via `bwrap --die-with-parent` plus a parent-side timer; fork-bomb protection via a bounded
   `RLIMIT_NPROC`; output/expansion-bomb protection via `RLIMIT_FSIZE` plus a total-bytes cap
   enforced during workspace construction (checked against *expanded* size, not input size).
   `resource_envelope.cpu_limit` is validated by #464 only as `isinstance(v, str) or None` — it is a
   **free-form string, not a number** (`_opt_str`, `mechanism_candidate.py`). `materializer.py`
   must parse it defensively against a small supported-format allowlist (e.g. a bare integer
   CPU-seconds-per-wall-second approximation) and refuse the candidate fail-closed (`BLOCKED`) if
   the string doesn't match a supported format — never silently ignore it or let a parse error
   propagate as an uncaught exception. Tool-call/token/monetary ceilings are moot while network
   egress is denied by construction (requirement 2) — explicitly handed to #466/#467, not built
   here. A cgroup tier remains a follow-up (open question).
   These ceilings live in a **new, separate** `materializer:` top-level block in `config/
   config.yaml` (sibling to `token_optimization:`, not nested inside it) — distinct from
   `budget_enforce.py`'s own declare-in-YAML/enforce-in-module shape, which this mirrors but which
   actually reads `.claude/skills/refinement/config.yaml` (round-2 fix: the first draft's citation
   implied both live in the same file; they are two separate YAML files that share a pattern, not
   one file).
7. **Generic execution primitive, not just workspace materialization.** The acceptance criteria's
   attack fixtures only mean something if *something* actually attempts each forbidden action
   inside a materialized workspace and is observed failing. #465 therefore ships both:
   - a library function, `run(candidate, argv, *, baseline, out_dir) -> Result` (exit code, rlimit
     usage, wall time, a verdict), which builds the bwrap-wrapped workspace per the candidate's
     envelopes and execs `argv` inside it;
   - a thin `python -m factory_core.materializer` CLI (`materialize`, `run -- <argv>`
     subcommands), mirroring `mechanism_candidate.py`'s own `argparse`-CLI-over-importable-API
     shape.
   #465 does not decide *what* candidate-logic command to run (#466/#467's job) — only that the
   boundary around *any* command is real.
8. **Baseline digest generation is real, not caller-trusted, and the commit it pins is
   caller-supplied.** The #464 candidate record carries only `baseline.repo_digest` (a value to
   check against), not a git ref — `materialize()`/`run()` therefore take an explicit
   `Baseline(commit, repo_root)` parameter from the caller (round-2 fix: the first draft's
   `compute_repo_digest(commit, repo_root)` had nothing in the data model to supply `commit` from).
   `repo_digest` is computed from that pinned commit (`git ls-tree -r <commit>` plus blob contents
   via `git cat-file`/`git archive` — never a working-tree walk, because `entrypoint.sh`
   git-excludes run-time staged files into the clone per #472, so a tree walk would be
   non-deterministic run-to-run), reusing `mechanism_candidate.canonical_digest()` **by import, not
   copy**. The manifest maps `path -> {sha256, mode}`. A path whose mode is a symlink (`120000`) is
   recorded by its link target, and the record is refused if any symlink target resolves outside
   the snapshot. A gitlink (mode `160000` — a submodule) is refused outright, as is any blob whose
   content starts with the Git LFS pointer header — refuse, never attempt to flatten/fetch. (This
   repo has no `.gitmodules` and no `git lfs` binary today, reconfirmed this pass — these fixtures
   are defensive/forward-looking, e.g. for a future MarketHawk baseline, not exercised against this
   repo's own content.) The materializer recomputes `repo_digest` and refuses fail-closed if it
   does not match the candidate record's declared value. Digests this ticket cannot derive from
   real content (`model_runtime_digest`, `tool_digest`, `verifier_digest`, `policy_digest`,
   `library_digest`) are verified only for shape (#464 already does this) and trusted as
   caller-supplied, not invented.
9. **Re-validate the candidate as defense-in-depth.** Before materializing, call
   `mechanism_candidate.validate_candidate()` (imported, not re-implemented) even though a prior
   gate presumably already validated the record — closes a TOCTOU gap. This call is also what makes
   H3 unreachable for this ticket's purposes: `_validate_standalone()` already fails any record
   whose derived risk class is H3 (`mechanism_candidate.py`: `if derived == "H3": _fail(...)`), so
   an H3-touching record can never reach `materialize()`/`run()` as a valid record in the first
   place (round-2 addition: see `attack-h3-surface` fixture below, which exists to pin this down as
   an asserted fact rather than an implicit assumption).
   - **H2 policy (round-2 addition, Decision 2):** `materialize()` accepts H0–H2 candidates (full
     workspace build, digesting, bundle production — all independent of real execution). `run()`
     accepts **only H0–H1**; given an H2 candidate it refuses with a `BLOCKED` verdict and a
     `"H2 execution quarantine policy not yet defined (#467)"` reason, rather than executing it.
     This seam — `materialize()` wider than `run()` — is also the implementation-sequencing seam
     described below.
10. **Artifact bundle is ephemeral, never committed, and self-describing.** Baseline/candidate
    digests, the exact diff, a `provenance` block, and the verdict are written as JSON via an
    `--out-dir` flag, defaulting to `$ARTIFACTS_DIR/materializer/<candidate_id>/` —
    `candidate_id` is sanitized with the same `^[A-Za-z0-9_-]+$` pattern
    `run_record.py::_SAFE_RUN_ID_RE` already uses to block path traversal. The disposable workspace
    itself is a fresh `mkdtemp()` directory **outside** both the clone and `$ARTIFACTS_DIR`, removed
    on completion (success or failure) — cleanup must leave no candidate state in the active harness
    or host workspace (this is #465's **own** acceptance criterion, not #463's — round-2 citation
    fix). Only small, synthetic **test fixtures** are committed to the repo, under a new
    `evals/materializer/fixtures/` directory mirroring `evals/mechanism-candidates/fixtures/`.
    `provenance` (round-2 addition — the first draft's bundle example promised this key but never
    defined it) records at minimum: the materializer's own module version/commit, the resolved
    `bwrap`/kernel capability state at run time (preflight result), and a hash of the exact
    invocation (`argv` plus the resolved bind/rlimit arguments). The bundle itself carries a
    `bundle_digest` — `canonical_digest()` over the bundle object computed with `bundle_digest`
    itself absent, then the field is added — so a reviewer or #466/#467 can detect post-write
    tampering without re-deriving the whole bundle from scratch.
11. **No new adapter.yaml consumer, gate, or scheduler/DAG surface.** `adapter.py`'s
    `mechanism_candidates` top-level key stays hard-rejected (#465 adds no consumer either, same
    reasoning #464 used). No DAG/workflow node, no scheduler dispatch path, no board/label mutation
    is introduced.
12. **Attack fixtures must not touch real secrets or the public internet.** The credential fixture
    plants a synthetic canary secret under **both** a temporary `HOME` and a temporary
    `$ARTIFACTS_DIR`-style directory (round-2 fix: the first draft asserted only one location) and
    asserts the sandboxed process cannot read either. The network fixture targets a TEST-NET-1
    address (`192.0.2.1`, RFC 5737), never a real hostname. The Docker-socket fixture (round-2 fix:
    the first draft's framing — "no `/var/run/docker.sock` inside the sandbox" — was vacuous,
    because the socket was never bind-mounted into the run container to begin with; the real
    threat is the proxy) asserts `DOCKER_HOST`/`BUILDKIT_HOST`/`BUILDKIT_PORT` are absent from the
    sandboxed process's environment (via the `--clearenv` allowlist) **and** that a TCP connection
    attempt to wherever `docker-socket-proxy-scheduler` is reachable fails under `--unshare-net`.
13. **Deferred scope (round-2 addition).** The issue body's scope list is broader than what #465
    builds; the following are explicitly deferred so a later conformance pass judges against this
    spec, not the raw issue text:
    - Dependency allowlists, language-aware static checks, and generated-file inventory — these
      operate on the *diff a candidate proposes*, which #466 (the proposer) authors; #465 only
      executes against an already-validated record and has no natural hook for them.
    - Full SBOM/provenance beyond this ticket's `provenance` block (item 10) — left to #467, which
      has the promotion-time context (real dependency closure resolution, etc.) to make it
      meaningful.
    - `filesystem_envelope.read_paths` enforcement (item 4) — no behavior to attach it to yet; wired
      in whenever a future ticket needs to grant read access beyond the baseline snapshot.

## Architecture / Approach

### File layout

```text
scripts/factory_core/materializer.py        # baseline digesting, workspace build, bwrap-arg
                                             # translation, run(), preflight probe, CLI
tests/test_materializer.py                  # unit tests (no real namespaces needed) +
                                             # one live bwrap integration test (skips on
                                             # preflight failure; see CI job below)
evals/materializer/fixtures/
  baseline-manifest.json                    # synthetic small baseline for digest tests
  candidate-*.json                          # reused/extended from mechanism-candidates shape
  attack-credential-canary.json             # expects: canary unreadable under $HOME or $ARTIFACTS_DIR
  attack-network-testnet.json               # expects: connect to 192.0.2.1 fails
  attack-docker-socket.json                 # expects: DOCKER_HOST/BUILDKIT_* absent; proxy TCP fails
  attack-path-escape.json                   # expects: write outside overlay fails
  attack-symlink-escape.json                # expects: symlink resolving outside baseline refused
  attack-submodule.json                     # expects: gitlink entry refused
  attack-lfs-pointer.json                   # expects: LFS pointer blob refused
  attack-archive-bomb.json                  # expects: expansion-bytes cap trips
  attack-resource-exhaustion.json           # expects: RLIMIT_NPROC/RLIMIT_FSIZE/RLIMIT_CPU trip
  attack-h3-surface.json                    # expects: validate_candidate() rejects before materialize()
  manifest.json                             # expected verdict per fixture
.github/workflows/ci.yml                    # +1 new job: build image, run live materializer tests
                                             # with seccomp/apparmor unconfined (see below)
```

`materializer.py` exposes an importable API (`compute_repo_digest(commit, repo_root)`,
`build_workspace(candidate, baseline)`, `preflight_probe()`, `run(candidate, argv, *, baseline,
out_dir)`, `materialize(candidate, *, baseline, out_dir)`) plus a small `argparse` CLI with
`materialize` and `run -- <argv>` subcommands. It imports `mechanism_candidate`
(`validate_candidate`, `canonical_digest`, `classify_risk`) and `verdict` (`format_verdict`) —
never re-implements either.

### Baseline digesting

`compute_repo_digest(commit, repo_root)` runs `git ls-tree -r <commit>` against the clone, reads
each blob's content via `git cat-file`, and builds a `{path: {sha256, mode}}` manifest (`.git/` is
never walked — it isn't part of the tree listing). Refusal rules (in order): a gitlink entry
(mode `160000`) refuses the whole record; a blob whose first line is the Git LFS pointer header
refuses; a symlink (mode `120000`) whose target, resolved against the snapshot root, falls outside
it refuses. The manifest is hashed via `mechanism_candidate.canonical_digest()` to produce
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
  empty dir inside the workspace, `TMPDIR`) — never `GH_TOKEN`, `ANTHROPIC_*`,
  `CLAUDE_CODE_OAUTH_TOKEN`, `DOCKER_HOST`, `BUILDKIT_HOST`, `BUILDKIT_PORT`, `SEQ_URL`,
  `SSH_AUTH_SOCK`, or any `.archon/.env`/`~/.gitconfig` value.
- An **explicit allowlist** of binds (see Requirement 4) — never `--ro-bind / /`: the baseline
  snapshot, the minimal `/usr`/`/bin`/`/lib`/`/lib64` set needed to exec `python3`/`/bin/sh`,
  `--tmpfs /tmp`, `--proc /proc`, a minimal synthetic `--dev /dev`, a fresh empty `$HOME`, and a
  `--bind` per `filesystem_envelope.write_paths` entry.
- No `--bind` of `/var/run/docker.sock`, `~/.config/gh`, `~/.claude`, `~/.gitconfig`, `.archon/`, or
  any credential path, ever — enforced by construction (these are simply never in the bind-mount
  list the translator can produce), not by a denylist check.
- `prlimit`-wrapped exec for `RLIMIT_AS` (memory), `RLIMIT_CPU`, a bounded `RLIMIT_NPROC`
  (fork-bomb), `RLIMIT_FSIZE` (single-file size); a parent-side wall-clock timer enforces
  `max_duration_seconds` by killing the `bwrap` process group.

### Preflight probe and fail-closed refusal

`preflight_probe()` attempts the minimal `bwrap --unshare-user --unshare-pid --unshare-net
--die-with-parent -- true` invocation in a throwaway temp dir before any real materialization. On
failure (the documented, currently-universal case in the production phase container),
`materialize()` and `run()` both short-circuit to `format_verdict(gate_type="materializer",
status="BLOCKED", findings_count=1, severity="high")` with the probe's stderr recorded as the
reason, and perform no further action. This path is unit-tested directly (mock/force the probe to
fail) and is what every attack-fixture test exercises as the actual verdict in the default `pytest`
run: the *generated bwrap argv and rlimit values* are what those tests assert against, not a live
sandboxed execution — except the live integration test below, and the new CI job.

### Live integration test and CI job (round-2 addition)

One `tests/test_materializer.py` test actually invokes `bwrap` for real and asserts a trivial
sandboxed command succeeds when the preflight probe passes, skipping cleanly
(`pytest.mark.skipif`) otherwise — unchanged from the first draft. New this round: `.github/
workflows/ci.yml` gains a job that (1) builds the image from the repo-root `Dockerfile` (the same
Dockerfile `publish.yml` uses to build `ghcr.io/omniscient/dark-factory`, which already
apt-installs `bubblewrap`) — today `ci.yml` never builds this image on a PR, only `publish.yml`
does, on push to `main`, so this is a new CI capability scoped to this one job, not a change to
`ci.yml`'s existing fast pytest/`test_*.sh` jobs or to `publish.yml` itself; (2) runs the image with
`--security-opt seccomp=unconfined --security-opt apparmor=unconfined` and
`MATERIALIZER_REQUIRE_LIVE=1`, which the live test reads to turn its own skip into a hard failure;
(3) executes `python -m pytest tests/test_materializer.py -v` inside that container. This runs on
every PR and exercises the real namespace boundary without touching `run-compose.yml` or any
production phase-container privilege. See "Open questions" for the one assumption this rests on
that is not yet empirically verified.

### Execution primitive

```text
run(candidate, argv, *, baseline, out_dir) -> Result(
    exit_code, wall_seconds, max_rss_bytes, verdict,
)
```

Builds the workspace, execs `argv` inside the bwrap sandbox, tears down the workspace afterward
regardless of outcome, and writes the artifact bundle (below) to `out_dir`. The CLI's `run --
<argv>` subcommand is a thin wrapper. `run()` refuses an H2 candidate outright (`BLOCKED`,
Requirement 9); `materialize()` accepts H0–H2. #466/#467 call this same function/subcommand to run
real proposer/prober agent commands; #465 does not know or care what `argv` contains.

### Artifact bundle

```json
{
  "candidate_id": "...",
  "candidate_digest": "sha256:...",
  "baseline": { "commit": "...", "repo_digest": "sha256:...", "...": "..." },
  "diff": "<the exact unified diff, copied from the candidate record>",
  "materialized_at": "2026-10-22T00:00:00Z",
  "provenance": {
    "materializer_version": "...",
    "bwrap_available": false,
    "invocation_digest": "sha256:..."
  },
  "verdict": { "status": "BLOCKED", "gate_type": "materializer", "findings_count": 1,
               "severity": "high", "reason": "bwrap: No permissions to create a new namespace..." },
  "bundle_digest": "sha256:..."
}
```

Written once per `materialize()`/`run()` call to `$ARTIFACTS_DIR/materializer/<candidate_id>/
bundle.json` (sanitized `candidate_id`), never committed. `bundle_digest` is computed over the
object with that key absent, then added.

### Config

A new `materializer:` top-level block in `config/config.yaml` (sibling to `token_optimization:`,
not nested inside it): default `memory_limit_mb`, `max_duration_seconds`, `max_workspace_bytes`,
and `max_nproc` ceilings, overridable per-candidate only downward (a candidate cannot declare a
*larger* limit than the config ceiling — enforced in `materializer.py`).

### Implementation sequencing (round-2 addition, Decision 2)

The spec is structured so implementation can proceed as either two sequential PRs under this one
issue, or two separately-filed tracker issues, without rework — but **this refine phase does not
file new tracker issues itself** (not among its authorized outputs; see Scope Boundary). The seam:

- **Phase A — materialize.** Requirements 2–5 (envelope translation shape and refusal rules, not
  requiring real namespaces), 6 (rlimit/config shape), 8 (baseline digesting), 9 (re-validation,
  H2/H3 policy), 10 (bundle+provenance), 13 (deferred-scope documentation). Seven of the twelve
  attack fixtures are non-execution (argv/bind-list assertions) and need no real `bwrap`. Fully
  buildable and testable in CI today, with no dependency on Decision 1's new job landing.
  Acceptance criteria: materializer produces correct bind/rlimit argv and a valid, self-describing
  bundle for any H0–H2 candidate; refuses H3/malformed/digest-mismatched candidates fail-closed.
- **Phase B — run.** Requirement 7's `run()`/CLI primitive, the preflight probe, the live
  integration test, the remaining six execution-attack fixtures, and the new CI job. Depends on
  Phase A's bundle/digest/envelope-translation code and on the CI job existing (Decision 1).
  Acceptance criteria: the CI job's live test passes against a real `bwrap` sandbox; the production
  phase container still fails closed via the same preflight probe.

Whoever runs the plan phase for this ticket should decide, as a scheduling matter (not a design
matter — this spec takes no position), whether Phase A and Phase B ship as one PR, two PRs under
#465, or two filed issues depending on #465.

## Alternatives considered

- **Nested Docker containers.** Rejected — requires giving phase containers Docker-socket access
  they don't have today (the socket is bind-mounted, read-only, only into
  `docker-socket-proxy-scheduler`, itself capped to `EXEC=0`/`BUILD=0`/`AUTH=0`/`SECRETS=0`); a
  privilege expansion needing its own human-reviewed ticket, not something this spec can authorize.
- **gVisor (`runsc`) kernel-isolation tier.** Rejected — already rejected by the #189 spike as
  disproportionate host-level infra for a far cheaper read-only advisory-lane use case; no stronger
  case exists here for an L-size ticket.
- **Bundling the `run-compose.yml`/Dockerfile container-runtime change into this ticket so the
  materializer is functional in production on merge.** Rejected — widens the factory container's
  own privileges, a change the candidate contract's own H3 classification already protects
  (`mechanism_candidate.py`'s `H3_PREFIXES` and `.factory/adapter.yaml`'s
  `safety.critical_diff_paths`, not CLAUDE.md's own separately-scoped `hard_exclude_paths` list —
  round-2 citation fix); the verified total non-function makes this a bigger change, not a smaller
  one. Decision 1's CI-only job (above) achieves real boundary verification without needing this
  change at all, which removes most of the original pressure to bundle it.
- **A weaker non-namespace fallback (`--clearenv` + rlimits + restricted `$PATH`, no bwrap
  namespaces) used whenever bwrap is unavailable.** Rejected unconditionally, even gated behind the
  same preflight check — credentials stay readable on disk and egress stays open, which is false
  assurance, worse than an honest refusal, and directly regresses the issue's own stated framing
  that "the current PATH shims are defense in depth, not the boundary."
- **Stopping at workspace-materialization-only, with no `run()`/CLI execution primitive**, leaving
  attack-fixture verification and all execution to #466/#467. Rejected — the acceptance criteria's
  attack fixtures are unfalsifiable without something that actually attempts the forbidden action;
  #465 must own the boundary it claims to build.
- **Computing `repo_digest` from a live working-tree walk.** Rejected — `entrypoint.sh` stages
  run-time files into the clone (git-excluded, #472), so a tree walk is non-deterministic run to
  run; a pinned-commit `git ls-tree`/`git archive` read is deterministic and reproducible.
- **Binding the whole host filesystem read-only (`--ro-bind / /`) for simplicity.** Rejected in this
  round — it was the first draft's own verification probe, and would expose `~/.claude`,
  `~/.config/gh`, `~/.gitconfig`, `$ARTIFACTS_DIR`, and the live clone to any process that actually
  ran inside it. An explicit allowlist (Requirement 4) costs a short, enumerable bind list in
  exchange for a credential-isolation property that actually holds.

## Open questions (non-blocking)

- **Whether CI's elevated seccomp/apparmor settings actually permit `bwrap` to create namespaces on
  a GitHub Actions-hosted runner's Docker daemon is not yet empirically verified** — only the
  production phase container's total non-function was verified live. Decision 1 explicitly calls
  for "one real CI run before implementation" of the rest of the live-execution surface; the
  implement phase's first task should be a minimal spike confirming the CI job's live test actually
  passes before building rlimit/execution logic on the assumption that it will. If it also fails
  closed, that is new information for the ticket owner, not something to silently work around with
  a weaker fallback (see Alternatives).
- **Enabling infra change for the production container.** A follow-up ticket that makes `bwrap`
  namespace creation possible in the *production* phase container (as opposed to the CI-only job
  this spec adds) remains open and deferred, for whenever #466/#467 need the live boundary to run
  real candidate logic outside of CI. It should weigh a narrow grant (a custom seccomp/AppArmor
  profile permitting only the needed `unshare`/`clone3`/`mount` syscalls) against a broad one — that
  choice, and its own security review, belongs to that ticket.
- **Real network egress allowlist / scoped model proxy.** Deferred until #466/#467 actually need a
  materialized sandbox to reach the model API — likely reusing `model_proxy.py`'s existing
  transparent-passthrough-plus-ledger shape and the unused `socat` apt package for a narrow
  port-forward, rather than a bespoke proxy.
- **cgroup-based CPU/memory enforcement.** rlimits are coarser than cgroups; a cgroup v2 tier is a
  follow-up if rlimits prove too imprecise in practice.
- **Cooldown/TTL on repeated materialization attempts** for the same candidate — not addressed;
  mirrors #464's identical open question about re-proposal after quarantine.
- **Whether Phase A/Phase B (above) ship as separate tracker issues** — a scheduling decision for
  whoever plans this ticket, not resolved here.

## Assumptions (flagged)

- **The materializer does not emit `mechanism_lineage` events or mutate a candidate's
  `lifecycle_state`.** It only re-validates (#464's `validate_candidate`) and materializes; lineage
  advancement is left to whichever ticket actually performs the probe (#467).
- **This repo has no submodules and no `git-lfs` binary today** (reconfirmed this pass) — the
  submodule/LFS refusal rules and fixtures are defensive/forward-looking, not exercised against
  this repo's real content in CI.
- **In the production phase container, every live `materialize()`/`run()` call fails closed** to a
  `BLOCKED` "isolation unavailable" verdict — this remains the ticket's intended, accepted
  delivered state for production use. What's new this round is that the boundary is no longer
  *entirely* unexercised: the CI job (Decision 1) is expected to exercise it live on every PR, once
  built — but that expectation itself rests on the unverified assumption flagged in "Open
  questions," not on something this spec has confirmed.
