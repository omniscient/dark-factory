# Harness candidate materializer — disposable bwrap sandboxes from an immutable baseline

**Issue:** #465 · **Epic:** #463 (Add a governed recursive harness-improvement capability)
**Depends on:** #464 (closed/merged — `scripts/factory_core/mechanism_candidate.py`)
**Revised:** 2026-11-19 (operator amendment after second spec-gate review: Phase A/Phase B split per owner Decision 2, runnable preflight probe and CI step, write_paths containment, live attack fixtures, bind order, snapshot source)
**Revised:** 2026-11-21 (operator amendment after plan gate review: R9 clarification)
**Scope of #465:** Phase A (materialize) only. Phase B (run) is specified here as the design of
record and is implemented under the Phase B follow-up issue #544 (filed by the operator at spec approval;
`Depends on: #465`). See "Implementation sequencing".

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

Items 1, 2 and 4, and the *translation* of item 3's envelopes into a `bwrap` argument list, are
**Phase A** and are what #465 itself implements. Launching the sandbox and observing item 3's
enforcement is **Phase B**.

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

- **Decision 1 = (a):** run `tests/test_materializer.py` inside the freshly built image, in a
  container started with `--security-opt seccomp=unconfined --security-opt apparmor=unconfined` and
  `MATERIALIZER_REQUIRE_LIVE=1` (a failed preflight or a skipped live test becomes a hard failure),
  on every PR. This is one added step in `ci.yml`'s existing `docker-build` job, which already
  builds `dark-factory:pr` on every PR. It exercises the real `bwrap` namespace boundary
  **without** touching `run-compose.yml` or any production phase-container privilege. In the
  production phase container `run()` keeps failing closed exactly as before. **Phase B.**
- **Decision 2 = agree with proposal:** `materialize()` accepts H0–H2 and needs no namespaces;
  `run()` accepts H0–H1 only, needs the real `bwrap` boundary, and refuses H2 with a `BLOCKED`
  verdict pending #467's quarantine design. The ticket is split on that seam: **#465 is the
  materialize half (Phase A)**; the run half (Phase B) is implemented under the Phase B follow-up
  issue #544 (filed by the operator at spec approval; `Depends on: #465`). See "Implementation
  sequencing".

This document therefore specifies two things and #465 implements one of them: **Phase A**, the
materializer's non-executing half, fully testable in today's CI with no namespaces; and **Phase
B**, the execution half plus the CI step that gives the boundary a real, PR-gated execution path,
kept here as the design of record. Every requirement, fixture, test and file below is tagged with
its phase. See "Open questions" for the result of the CI spike Decision 1 called for.

## Requirements (from Q&A)

1. **[Phase A: argv translation · Phase B: launch]**
   **Isolation primitive: `bwrap` namespaces** (user, mount, pid, net, ipc, uts), launched from
   inside the existing phase container. Nested Docker is rejected — it would require giving phase
   containers Docker-socket access they don't have today: the socket (`/var/run/docker.sock`) is
   bind-mounted read-only only into `docker-socket-proxy-scheduler`
   (`deploy/docker-compose.yml`), whose ACL already denies `EXEC`/`BUILD`/`AUTH`/`SECRETS`; the run
   container only ever reaches Docker indirectly via the `DOCKER_HOST`/`BUILDKIT_HOST` env vars
   (`run-compose.yml`), never a socket bind mount. Widening that is a separate human-reviewed
   privilege-expansion change. gVisor is rejected as disproportionate (already rejected on
   identical reasoning by the #189 spike for a far cheaper advisory-lane use case).
2. **[Phase A: argv and refusal of declared egress · Phase B: live proof]**
   **Network: `--unshare-net`, loopback only, no route out, by construction** — not an
   allowlist/proxy. A candidate whose `network_envelope` declares `egress_allowed: true` or a
   non-empty `allowed_hosts` must be **refused fail-closed with a clear reason**, never silently
   downgraded to no-network. Real egress-allowlist/scoped-proxy support (plausibly reusing
   `scripts/factory_core/model_proxy.py`'s existing transparent Anthropic-passthrough-plus-ledger
   shape, and the already-provisioned-but-unused `socat` apt package) is deferred — open question,
   below.
3. **[Phase A: argv · Phase B: live proof]**
   **Credential isolation by construction**: `bwrap --clearenv` plus an explicit, minimal env
   allowlist (`PATH`, a fresh empty `HOME`, `TMPDIR`) — never `GH_TOKEN`, `ANTHROPIC_*`,
   `CLAUDE_CODE_OAUTH_TOKEN`, `DOCKER_HOST`, `BUILDKIT_HOST`, `BUILDKIT_PORT`, `SEQ_URL`, or
   `SSH_AUTH_SOCK` (round-2 addition — the first draft's credential list omitted all six). No bind
   mount of `.archon/.env`, `~/.config/gh`, `~/.claude`, `~/.gitconfig` (round-2 addition), the
   original clone, or the `scheduler_state`/`dark_factory_state` volumes. The workspace is always a
   **fresh copy** built from the digested baseline, never a bind mount of the live clone.
4. **[Phase A: translator, `write_paths` validation, argv-level fixtures · Phase B: live proof]**
   **Filesystem — explicit allowlist, never `--ro-bind / /`** (round-2 fix: the first draft's own
   verification probe used `--ro-bind / /`, which would have exposed `~/.claude`, `~/.config/gh`,
   `~/.gitconfig`, `$ARTIFACTS_DIR`, `/var/lib/dark-factory`, and the live `/workspace` clone had
   any real run ever executed it). The sandbox root is composed from an explicit bind list only,
   emitted **in this order** (order is load-bearing: a later `--tmpfs` hides anything bound earlier
   beneath it, and `mkdtemp()` places the workspace under `/tmp`):
   - The **base binds**: `--ro-bind /usr /usr`, `--ro-bind /bin /bin`, `--ro-bind /lib /lib`, and
     `--ro-bind /lib64 /lib64` if present — the minimum needed to exec `python3`/`/bin/sh` inside
     the sandbox.
   - `--tmpfs /tmp`, `--proc /proc`, `--dev /dev` (a minimal synthetic `/dev`, never a bind of the
     host's), and `--tmpfs /home/sandbox` as `$HOME` (a tmpfs, not a `--dir` inside the workspace:
     creating a directory inside a read-only bind fails with `Read-only file system`).
   - `--ro-bind <workspace> /workspace` — the candidate workspace built in "Workspace
     construction", always mounted at the fixed sandbox path `/workspace`, after every tmpfs, never
     at its host path.
   - `--bind <workspace>/<entry> /workspace/<entry>` for each **validated**
     `filesystem_envelope.write_paths` entry (nothing else is writable), then `--chdir /workspace`.
   - **`write_paths` containment (second-review fix).** #464 validates `write_paths` only as a list
     of strings (`_str_list`, `mechanism_candidate.py`), so an absolute path or a `..` segment is
     contract-valid; bound verbatim it would mount a host path read-write inside the sandbox. Each
     entry must therefore be a relative, normalised repo path (no leading `/`, no `..` segment, no
     backslash or control character — the same rules the contract applies to diff paths) and must
     lie under the candidate's `editable_path_allowlist`. The translator derives the bind source as
     `<workspace>/<entry>` only after `os.path.realpath` of that source is confirmed inside the
     workspace and no component of it is a symlink. Any violation is a `BLOCKED` refusal before any
     argv is produced. The source of a `--bind` is never a candidate-supplied string. Fixtures:
     `attack-write-path-absolute`, `attack-write-path-dotdot`, `attack-write-path-symlink`.
   - Never bound, by construction (absent from the translator's possible output, not filtered by a
     denylist): `/`, `/home`, `/root`, `/etc`, the real `$HOME`, `$ARTIFACTS_DIR`,
     `/var/lib/dark-factory`, the live `/workspace` clone, and any credential path.
   - `filesystem_envelope.read_paths` is **not** enforced by this ticket (see "Deferred scope",
     below) — the workspace bind above is already read-only in its entirety, so there is no
     read-path-narrowing behavior yet to build.
5. **[Phase B]**
   **Fail-closed preflight, never a silent fallback**: before `run()` attempts any real sandbox,
   probe whether `bwrap` can create its required namespaces (the exact probe is in "Preflight
   probe and fail-closed refusal"). If it cannot, emit a `BLOCKED` verdict
   (round-2 fix: the current schema's canonical gating-block token — `scripts/factory_core/
   verdict.py`'s `GATING_BLOCK_STATUSES = {"BLOCKED"}`; `FAIL` is `LEGACY_STATUSES`, documented but
   not the token to emit from new code, and `ERROR` must never be used here since it is a
   pass-through/proceed status), citing the probe's stderr as the reason, and stop. **No weaker
   fallback tier** (e.g. `--clearenv`+rlimits+restricted `$PATH` alone, with no namespaces) is
   acceptable, even gated behind the same preflight check — that would leave credentials readable
   on disk and egress open, producing false assurance, and directly contradicts the issue's own
   framing that "the current PATH shims are defense in depth, not the boundary."
   `materialize()` (Phase A) executes nothing from the workspace, needs no namespaces, and never
   calls the probe.
6. **[Phase A: config block, parsing, ceilings, rlimit values in the argv · Phase B: applying
   them to the running child, wall-clock timer]**
   **Resource limits — per-process, not a sandbox-wide cap** (round-2 clarification: `RLIMIT_AS`
   and the other rlimits below bound one process each; a fork bomb that gets past `RLIMIT_NPROC`
   can still multiply effective memory use across many processes each under the per-process
   `RLIMIT_AS` ceiling, so `RLIMIT_NPROC` must itself be bounded to a small, config-set ceiling, not
   left unlimited). Memory and CPU-time via `RLIMIT_AS`/`RLIMIT_CPU`, set by a `prlimit` wrapper
   placed **after** `--` in the `bwrap` argv so the limits apply to the exec'd child inside the
   sandbox, never to `bwrap` itself (`RLIMIT_NPROC` is accounted per uid, so a small ceiling
   applied to `bwrap` can count the factory user's existing processes and stop the sandbox from
   starting); wall-clock duration
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
   one file). Phase A adds this block together with one `docs/configuration.md` Layer 1 row per
   `materializer.*` leaf key: `scripts/docs_refs_check.py` (run by `ci.yml`'s `dag-check` job)
   fails when the Layer 1 table and the leaf keys of `config/config.yaml` differ in either
   direction.
7. **[Phase B; the importable `materialize()` and the CLI `materialize` subcommand are Phase A]**
   **Generic execution primitive, not just workspace materialization.** The acceptance criteria's
   attack fixtures only mean something if *something* actually attempts each forbidden action
   inside a materialized workspace and is observed failing. The design therefore includes both:
   - a library function, `run(candidate, argv, *, baseline, out_dir) -> Result` (exit code, rlimit
     usage, wall time, a verdict), which builds the bwrap-wrapped workspace per the candidate's
     envelopes and execs `argv` inside it (Phase B);
   - a thin `python -m factory_core.materializer` CLI, mirroring `mechanism_candidate.py`'s own
     `argparse`-CLI-over-importable-API shape: the `materialize` subcommand ships in Phase A, the
     `run -- <argv>` subcommand in Phase B.
   Neither phase decides *what* candidate-logic command to run (#466/#467's job) — only that the
   boundary around *any* command is real.
8. **[Phase A]**
   **Baseline digest generation is real, not caller-trusted, and the commit it pins is
   caller-supplied.** The #464 candidate record carries only `baseline.repo_digest` (a value to
   check against), not a git ref — `materialize()`/`run()` therefore take an explicit
   `Baseline(commit, repo_root)` parameter from the caller (round-2 fix: the first draft's
   `compute_repo_digest(commit, repo_root)` had nothing in the data model to supply `commit` from).
   `commit` must be a full 40-hex SHA, resolved once per call.
   `repo_digest` is computed from that pinned commit (`git ls-tree -r <commit>` plus blob contents
   via `git cat-file` — never `git archive`, which honours `export-ignore`/`export-subst` and can
   therefore differ from the listed tree, and never a working-tree walk, because `entrypoint.sh`
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
9. **[Phase A; the H2 refusal in `run()` is Phase B]**
   **Re-validate the candidate as defense-in-depth.** Before materializing, call
   `mechanism_candidate.validate_candidate()` (imported, not re-implemented), passing
   `safety=mechanism_candidate.load_safety(baseline.repo_root)` explicitly (the default resolves
   from `$CLONE_DIR` or the current directory, which is not necessarily the baseline) and the
   caller-supplied `known_digests`, even though a prior
   gate presumably already validated the record — closes a TOCTOU gap. This call is also what makes
   H3 unreachable for this ticket's purposes: `_validate_standalone()` already fails any record
   whose derived risk class is H3 (`mechanism_candidate.py`: `if derived == "H3": _fail(...)`), so
   an H3-touching record can never reach `materialize()`/`run()` as a valid record in the first
   place (round-2 addition: see `attack-h3-surface` fixture below, which exists to pin this down as
   an asserted fact rather than an implicit assumption).
   Validation alone is not the containment: after the diff is applied, the workspace is
   verified against the validated path set (every path whose content, type or exec bit changed
   must be a declared diff path inside `editable_path_allowlist`, and no symlink may be added,
   removed or retargeted), and a mismatch is a `BLOCKED` refusal.
   - **H2 policy (round-2 addition, Decision 2):** `materialize()` accepts H0–H2 candidates (full
     workspace build, digesting, bundle production — all independent of real execution). `run()`
     accepts **only H0–H1**; given an H2 candidate it refuses with a `BLOCKED` verdict and a
     `"H2 execution quarantine policy not yet defined (#467)"` reason, rather than executing it.
     This seam — `materialize()` wider than `run()` — is also the implementation-sequencing seam
     described below. The `materialize()` half of this rule is Phase A. The `run()` refusal and
     its `refuse-h2-run` fixture are Phase B; the refusal happens before the preflight, so its
     test needs no namespaces.
10. **[Phase A: the `materialize()` bundle · Phase B: the `run()` bundle]**
    **Artifact bundle is ephemeral, never committed, and self-describing.** Baseline/candidate
    digests, the exact diff, a `provenance` block, and the verdict are written as JSON via an
    `--out-dir` flag, defaulting to `$ARTIFACTS_DIR/materializer/<candidate_id>/` (when
    `$ARTIFACTS_DIR` is unset, as in CI, `out_dir`/`--out-dir` is required and its absence is an
    error, never a fallback to the current directory) —
    `candidate_id` is sanitized with the same `^[A-Za-z0-9_-]+$` pattern
    `run_record.py::_SAFE_RUN_ID_RE` already uses to block path traversal. The disposable workspace
    itself is a fresh `mkdtemp()` directory **outside** both the clone and `$ARTIFACTS_DIR`, removed
    on completion (success or failure) — cleanup must leave no candidate state in the active harness
    or host workspace (this is #465's **own** acceptance criterion, not #463's — round-2 citation
    fix). Only small, synthetic **test fixtures** are committed to the repo, under a new
    `evals/materializer/fixtures/` directory mirroring `evals/mechanism-candidates/fixtures/`.
    `provenance` (round-2 addition — the first draft's bundle example promised this key but never
    defined it) records at minimum: the materializer's own module version/commit, the preflight
    result (`bwrap_available`: `true`/`false` from `run()`; `null` from `materialize()`, which
    never probes), and a hash of the exact invocation (the resolved bind/rlimit arguments, plus
    `argv` for `run()`). The bundle itself carries a
    `bundle_digest` — `canonical_digest()` over the bundle object computed with `bundle_digest`
    itself absent, then the field is added — so a reviewer or #466/#467 can detect post-write
    tampering without re-deriving the whole bundle from scratch.
11. **[Phase A and Phase B]**
    **No new adapter.yaml consumer, gate, or scheduler/DAG surface.** `adapter.py`'s
    `mechanism_candidates` top-level key stays hard-rejected (neither phase adds a consumer, same
    reasoning #464 used). No DAG/workflow node, no scheduler dispatch path, no board/label mutation
    is introduced.
12. **[Phase A: fixture files and argv-level assertions · Phase B: live execution]**
    **Attack fixtures must not touch real secrets or the public internet.** The per-fixture phase
    assignment is the table under "File layout". What each execution fixture asserts:
    - **Credential:** plants a synthetic canary secret under **both** a temporary `HOME` and a
      temporary `$ARTIFACTS_DIR`-style directory (round-2 fix: the first draft asserted only one
      location) and asserts the sandboxed process cannot read either.
    - **Network:** targets a TEST-NET-1 address (`192.0.2.1`, RFC 5737) and the link-local
      cloud-metadata address (`169.254.169.254`), never a real hostname.
    - **Docker** (round-2 fix: the first draft's framing — "no `/var/run/docker.sock` inside the
      sandbox" — was vacuous, because the socket was never bind-mounted into the run container to
      begin with; the real threat is the proxy): asserts `DOCKER_HOST`/`BUILDKIT_HOST`/
      `BUILDKIT_PORT` are absent from the sandboxed process's environment **and** that a TCP
      listener the test itself starts on the outer loopback is unreachable from inside the
      sandbox. CI has no `docker-socket-proxy-scheduler`, so an assertion against the real proxy
      would pass vacuously there.
    - **Path escape:** a write anywhere outside the validated write binds fails.
    - **Git/GitHub mutation:** the workspace has no `.git`; `git push` and `gh` fail (no
      repository, no token, no network).
    - **Resource exhaustion:** `RLIMIT_NPROC`/`RLIMIT_FSIZE`/`RLIMIT_CPU` trip.

    Every live negative has a **positive control outside the sandbox** in the same test (the
    canary is readable, the listener is reachable, the write succeeds), so it cannot pass
    vacuously. Phase A asserts these six fixtures against the generated argv only; that is a check
    of the translator, **not** evidence that the boundary holds. Only Phase B's live tests are.
    The refusal fixtures need no sandbox at all: `refuse-egress-declared`, `refuse-digest-mismatch`
    and `refuse-cpu-limit-format` (Phase A) and `refuse-h2-run` (Phase B).
13. **[Phase A — documentation only]**
    **Deferred scope (round-2 addition).** The issue body's scope list is broader than what #465
    builds; the following are explicitly deferred so a later conformance pass judges against this
    spec, not the raw issue text:
    - Dependency allowlists, language-aware static checks, and generated-file inventory — these
      operate on the *diff a candidate proposes*, which #466 (the proposer) authors; the
      materializer only operates on an already-validated record and has no natural hook for them.
    - Full SBOM/provenance beyond this ticket's `provenance` block (item 10) — left to #467, which
      has the promotion-time context (real dependency closure resolution, etc.) to make it
      meaningful.
    - `filesystem_envelope.read_paths` enforcement (item 4) — no behavior to attach it to yet;
      handed to #467, the first ticket that can need read access beyond the baseline snapshot.

## Architecture / Approach

### File layout

```text
Phase A — #465
scripts/factory_core/materializer.py   # compute_repo_digest, build_workspace, write_paths
                                        # validation, build_bwrap_argv (pure), materialize(),
                                        # CLI `materialize`
tests/test_materializer.py             # unit tests, no namespaces, no skips; collected by
                                        # ci.yml's existing `tests` job (python -m pytest tests/ -v)
evals/materializer/fixtures/           # all 18 fixtures below, baseline-manifest.json,
                                        # candidate-*.json, manifest.json (expected verdict and
                                        # phase per fixture)
config/config.yaml                     # + `materializer:` block (see Config)
docs/configuration.md                  # + one Layer 1 row per materializer.* leaf key

Phase B — the Phase B follow-up issue
scripts/factory_core/materializer.py   # + preflight_probe(), run(), CLI `run -- <argv>`
tests/test_materializer.py             # + live tests (marker `live_sandbox`), refuse-h2-run
tests/conftest.py                      # + register the marker; with MATERIALIZER_REQUIRE_LIVE=1
                                        # a skipped `live_sandbox` test fails the session
.github/workflows/ci.yml               # + one step in the existing `docker-build` job
```

Neither phase touches `.github/workflows/publish.yml`, `run-compose.yml`, `Dockerfile`,
`deploy/**` or `.claude/skills/**`. `.github/workflows/ci.yml` is a blast-radius hotspot and an H3
surface (`mechanism_candidate.py`'s `H3_PREFIXES` names `.github/`); the single Phase B step is the
only change to it.

Fixtures (fourteen `attack-*`, four `refuse-*`; all files are committed in Phase A):

| Fixture | Expects | Phase A (no namespaces) | Phase B |
|---|---|---|---|
| `attack-credential-canary` | canary unreadable under `$HOME` and `$ARTIFACTS_DIR` | argv only: `--clearenv`, no credential bind | executed live |
| `attack-network-testnet` | connect to `192.0.2.1` and `169.254.169.254` fails | argv only: `--unshare-net` | executed live |
| `attack-docker-socket` | `DOCKER_HOST`/`BUILDKIT_*` absent; outer-loopback listener unreachable | argv only: env allowlist | executed live |
| `attack-path-escape` | write outside the write binds fails | argv only: the sole `--bind`s are validated write paths | executed live |
| `attack-git-mutation` | no `.git`; `git push`/`gh` fail | workspace has no `.git`; argv | executed live |
| `attack-resource-exhaustion` | `RLIMIT_NPROC`/`RLIMIT_FSIZE`/`RLIMIT_CPU` trip | argv only: rlimit values within config ceilings | executed live |
| `attack-write-path-absolute` | `BLOCKED`, no argv produced | decided here | live step: `run()` refuses before any sandbox starts; canary at the target untouched |
| `attack-write-path-dotdot` | `BLOCKED`, no argv produced | decided here | same |
| `attack-write-path-symlink` | `BLOCKED`, no argv produced | decided here | same |
| `attack-symlink-escape` | baseline symlink resolving outside the snapshot refused | decided here | — |
| `attack-submodule` | gitlink entry refused | decided here | — |
| `attack-lfs-pointer` | LFS pointer blob refused | decided here | — |
| `attack-archive-bomb` | expansion-bytes cap trips during workspace construction | decided here | — |
| `attack-h3-surface` | `validate_candidate()` rejects before `materialize()` | decided here | — |
| `refuse-egress-declared` | `BLOCKED` for `egress_allowed: true` or non-empty `allowed_hosts` | decided here | — |
| `refuse-digest-mismatch` | `BLOCKED` when the recomputed `repo_digest` differs | decided here | — |
| `refuse-cpu-limit-format` | `BLOCKED` for an unsupported `cpu_limit` string | decided here | — |
| `refuse-h2-run` | `run()` returns `BLOCKED` for an H2 candidate | — (needs `run()`) | unit test, no namespaces |

Phase A exercises 17 of the 18: eleven are decided entirely without execution (the five
baseline/contract refusals, the three `write_paths` refusals and three `refuse-*`), and six
execution fixtures are asserted at argv level only. Phase B adds the six live executions, the three
`write_paths` refusals driven through `run()`, and `refuse-h2-run`.

`materializer.py` exposes an importable API. Phase A: `compute_repo_digest(commit, repo_root)`,
`build_workspace(candidate, baseline)`, `build_bwrap_argv(candidate, workspace, config)` and
`materialize(candidate, *, baseline, out_dir)`, plus an `argparse` CLI with a `materialize`
subcommand. Phase B adds `preflight_probe()`, `run(candidate, argv, *, baseline, out_dir)` and the
CLI's `run -- <argv>` subcommand. It imports `mechanism_candidate` (`validate_candidate`,
`canonical_digest`, `classify_risk`, `load_safety`) and `verdict` (`format_verdict`) — never
re-implements either.

### Baseline digesting (Phase A)

`compute_repo_digest(commit, repo_root)` runs `git ls-tree -r <commit>` against the clone, reads
each blob's content via `git cat-file`, and builds a `{path: {sha256, mode}}` manifest (`.git/` is
never walked — it isn't part of the tree listing). Refusal rules (in order): a gitlink entry
(mode `160000`) refuses the whole record; a blob whose first line is the Git LFS pointer header
refuses; a symlink (mode `120000`) whose target, resolved against the snapshot root, falls outside
it refuses. The manifest is hashed via `mechanism_candidate.canonical_digest()` to produce
`repo_digest`, compared against the candidate's declared `baseline.repo_digest` — a mismatch fails
closed before any workspace is built.

### Workspace construction (Phase A)

A snapshot is materialized from the pinned commit into a fresh `mkdtemp()` directory by writing
out the same `git cat-file` blobs the manifest hashed, with the modes the manifest recorded —
never `git archive` (it honours `export-ignore`/`export-subst`, so its output can differ from the
digested tree) and never the live working tree. The snapshot therefore contains no `.git`. The
candidate's `diff` (already containment-checked by #464 against `editable_path_allowlist`) is
applied with `git apply` inside that snapshot to produce the candidate workspace; `git apply
--check` first, so a diff that doesn't cleanly apply against this exact baseline fails closed
rather than partially mutating the workspace. The `max_workspace_bytes` cap is checked against the
expanded size while writing. Host-side walks of the workspace (the size cap, teardown) never
follow symlinks. `filesystem_envelope.write_paths`, after the containment check in Requirement 4,
controls which paths `bwrap` mounts read-write inside the sandbox; everything else is read-only.

`materialize()` removes its workspace before returning. `run()` (Phase B) never reuses a
materialized workspace: it rebuilds one from the same record and baseline, so there is no window
between materialize and run in which a workspace can be altered.

### Isolation: bwrap invocation (Phase A builds the argv; Phase B launches it)

`build_bwrap_argv()` is a pure function: it translates the candidate's envelopes into the complete
`bwrap` argument list and executes nothing. Phase A's tests assert on its output. In order:

- `--unshare-user --unshare-pid --unshare-ipc --unshare-uts --unshare-cgroup-try --unshare-net
  --die-with-parent --new-session --cap-drop ALL`
- `--clearenv --setenv PATH /usr/bin:/bin --setenv HOME /home/sandbox --setenv TMPDIR /tmp` — an
  explicit minimal allowlist; never `GH_TOKEN`, `ANTHROPIC_*`, `CLAUDE_CODE_OAUTH_TOKEN`,
  `DOCKER_HOST`, `BUILDKIT_HOST`, `BUILDKIT_PORT`, `SEQ_URL`, `SSH_AUTH_SOCK`, or any
  `.archon/.env`/`~/.gitconfig` value.
- The base binds, then `--tmpfs /tmp --proc /proc --dev /dev --tmpfs /home/sandbox`, then
  `--ro-bind <workspace> /workspace`, then one `--bind <workspace>/<entry> /workspace/<entry>` per
  validated `write_paths` entry, then `--chdir /workspace` — exactly the order of Requirement 4,
  never `--ro-bind / /`.
- No `--bind` of `/var/run/docker.sock`, `~/.config/gh`, `~/.claude`, `~/.gitconfig`, `.archon/`, or
  any credential path, ever — enforced by construction (these are simply never in the bind-mount
  list the translator can produce), not by a denylist check.
- After `--`: a `prlimit` wrapper carrying `RLIMIT_AS` (memory), `RLIMIT_CPU`, a bounded
  `RLIMIT_NPROC` (fork-bomb) and `RLIMIT_FSIZE` (single-file size), then the caller's `argv`. The
  limits therefore apply to the child inside the sandbox, not to `bwrap`.

Phase B launches this list with `subprocess` (`close_fds=True`, no `pass_fds`, so no inherited
descriptor reaches the sandbox) and a parent-side wall-clock timer that enforces
`max_duration_seconds` by killing the `bwrap` process group.

### Preflight probe and fail-closed refusal (Phase B)

`preflight_probe()` runs, before `run()` builds anything:

```
bwrap --unshare-user --unshare-pid --unshare-ipc --unshare-net --die-with-parent \
  --ro-bind /usr /usr --ro-bind /bin /bin --ro-bind /lib /lib --ro-bind /lib64 /lib64 \
  --proc /proc --dev /dev -- /bin/true
```

The probe uses the same base binds as the real invocation (`--ro-bind /lib64 /lib64` only if
present). The first draft's probe had no binds at all; its new root was an empty tmpfs, so it
failed with `execvp true: No such file or directory` even where namespaces work, and could never
have passed anywhere. A unit test asserts the probe argv contains the base binds.

A non-zero exit (the documented, currently-universal case in the production phase container, where
Docker's default seccomp profile refuses the namespace) makes `run()` short-circuit to
`format_verdict(gate_type="materializer", status="BLOCKED", findings_count=1, severity="high")`
with the probe's stderr recorded as the reason, and perform no further action. This path is
unit-tested directly by forcing the probe to fail.

`materialize()` never calls the probe: it needs no namespaces and behaves identically in the
production container, in the plain CI `tests` job and in the Phase B CI step.

### Live tests and CI step (Phase B)

**Live tests.** Every execution fixture (`attack-credential-canary`, `attack-network-testnet`,
`attack-docker-socket`, `attack-path-escape`, `attack-git-mutation`,
`attack-resource-exhaustion`) has a live test in `tests/test_materializer.py` that runs the attack
argv through `run()` inside the real sandbox and asserts the observed failure, together with its
positive control outside the sandbox (Requirement 12). The three `write_paths` refusals are also
driven through `run()` under the same marker (in `run()` they come after the preflight, so
anywhere the preflight fails they would be `BLOCKED` for the wrong reason). One further live test
asserts a trivial command succeeds and that `/src` does not exist inside the sandbox. All live
tests carry one marker, `live_sandbox`, registered in `tests/conftest.py`.

**What runs where — stated honestly.** In `ci.yml`'s existing `tests` job the runner has no
`bubblewrap`, so every `live_sandbox` test skips there. Those skips are expected and are **not**
evidence of anything. The only place the boundary is exercised is the step below, where
`MATERIALIZER_REQUIRE_LIVE=1` makes a failed preflight or any skipped `live_sandbox` test fail the
session (a `tests/conftest.py` hook turns skips of that marker into failures, and the step runs
`pytest -rs` so any other skip is visible in the log).

**CI step.** `.github/workflows/ci.yml` already has a `docker-build` job that builds
`dark-factory:pr` from the repo-root `Dockerfile` on every PR (the image apt-installs
`bubblewrap`). Phase B appends to that job; it adds no new job and no second build of the
production image. The image contains neither pytest nor `tests/`/`evals/` (the `Dockerfile` copies
`scripts/`, `config/` and the runtime files only), so the step builds a throwaway test layer and
mounts the checkout read-only:

```
printf 'FROM dark-factory:pr\nUSER root\nRUN pip install --quiet pytest\nUSER factory\n' \
  | docker build -t dark-factory:pr-test -
docker run --rm --network none \
  --security-opt seccomp=unconfined --security-opt apparmor=unconfined \
  -e MATERIALIZER_REQUIRE_LIVE=1 -e PYTHONPATH=/src/scripts \
  --entrypoint bash -v "$PWD:/src:ro" -w /src dark-factory:pr-test \
  -c 'python3 -m pytest tests/test_materializer.py -v -rs -p no:cacheprovider'
```

Constraints on that step:

- The tests build their own throwaway git repositories under `$TMPDIR` and never run git against
  `/src`: the runner's uid differs from the image's `factory` user (uid 1000), so git would refuse
  the mounted checkout as dubious ownership.
- No secrets and no token in the container's environment, no Docker socket mount, never
  `--privileged`. `--network none` is deliberate: the sandbox needs no network and the loopback
  listener of the Docker fixture lives inside the container.
- `actions/checkout` leaves the job token in the checkout's `.git/config`, so it is readable in
  the *test container* through the `/src` mount. It is never readable in the *sandbox*: `/src` is
  not in the bind list, and a live test asserts that.
- The relaxed seccomp/apparmor options exist on this one `docker run` only. `run-compose.yml`,
  `deploy/**`, the production `Dockerfile` and `publish.yml` are not changed, and no production run
  container ever gets them.

See "Open questions" for the result of the spike that checked this on a real runner.

### Execution primitive (Phase B)

```text
run(candidate, argv, *, baseline, out_dir) -> Result(
    exit_code, wall_seconds, max_rss_bytes, verdict,
)
```

In this order: re-validates the record (Requirement 9); refuses an H2 candidate; runs the
preflight probe; builds a fresh workspace; execs `argv` inside the bwrap sandbox; tears down the
workspace regardless of outcome; and writes the artifact bundle (below) to `out_dir`. The first
three steps can each end the call with a `BLOCKED` bundle and nothing executed. The CLI's `run --
<argv>` subcommand is a thin wrapper. #466/#467 call this same function/subcommand to run real
proposer/prober agent commands; the materializer does not know or care what `argv` contains.

### Artifact bundle (Phase A: `materialize()` · Phase B: `run()`)

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

The example above is a Phase B `run()` bundle from the production container (preflight failed). A
Phase A `materialize()` bundle has the same shape with `"bwrap_available": null` (never probed);
its verdict is `PASS` when the workspace was built and digested, and `BLOCKED` with the refusal
reason otherwise.

### Config (Phase A)

A new `materializer:` top-level block in `config/config.yaml` (sibling to `token_optimization:`,
not nested inside it) with four leaf keys: `materializer.memory_limit_mb`,
`materializer.max_duration_seconds`, `materializer.max_workspace_bytes` and
`materializer.max_nproc`. They are ceilings, overridable per-candidate only downward (a candidate
cannot declare a *larger* limit than the config ceiling — enforced in `materializer.py`).

The same Phase A change adds one row per key to the `## Layer 1` tables of
`docs/configuration.md`. `scripts/docs_refs_check.py` compares those tables with the leaf keys of
`config/config.yaml` in both directions and fails CI on a difference; any backtick repo path added
to the four reference docs must exist.

### Implementation sequencing (Decision 2)

Per Decision 2 the ticket is split on the `materialize()`/`run()` seam. **#465 is Phase A only.**
Phase B is specified in this document as the design of record and is implemented under the Phase B
follow-up issue #544 (filed by the operator at spec approval; `Depends on: #465`), with parent epic
#463. The plan and the conformance gate for #465 cover Phase A only; Phase B work appearing in a
#465 change is out of scope.

- **Phase A — materialize (#465).** Requirements 1–4 and 6 at the translation level (argv shape,
  refusal rules, `write_paths` containment, config block, rlimit values), 8 (baseline digesting),
  9 (re-validation, H3 unreachable, `materialize()` accepts H0–H2), 10 (the `materialize()`
  bundle with provenance and `bundle_digest`), 11, 12 at argv level, 13. Seventeen of the eighteen
  fixtures are exercised without namespaces (see "File layout"). Acceptance criteria:
  - `materialize()` accepts only a schema-valid H0–H2 candidate against an exact baseline digest,
    and refuses H3, malformed, digest-mismatched, egress-declaring, bad-`cpu_limit` and
    bad-`write_paths` candidates with `BLOCKED`.
  - It writes a self-describing bundle (digests, exact diff, provenance, `bundle_digest`) and
    leaves no workspace behind, on success and on refusal.
  - `build_bwrap_argv()` output satisfies the argv-level assertion of each execution fixture and
    matches the order in "Isolation".
  - Every Phase A test runs in `ci.yml`'s existing `tests` job with no `bwrap` and no namespaces,
    and none of them skips.
  - `config/config.yaml` and `docs/configuration.md` change together; `docs_refs_check.py` passes.
  - Phase A adds no `preflight_probe()`, no `run()`, no `ci.yml` change, and makes no claim that
    the boundary holds.
- **Phase B — run (the Phase B follow-up issue).** Requirement 5 (preflight), 7 (`run()` and the
  CLI `run` subcommand), the `run()` half of 9 (H2 refusal), the `run()` bundle of 10, the live
  half of 12, and the CI step. Depends on Phase A's digest, workspace and translation code.
  Acceptance criteria:
  - The CI step's live tests pass against a real `bwrap` sandbox on every PR, and a failed
    preflight or a skipped live test fails that step.
  - `run()` refuses H2 with `BLOCKED`; in the production phase container `run()` still fails closed
    through the same preflight probe.
  - The `ci.yml` change is the single step described above; no production privilege changes.

Two of the issue's acceptance criteria — "Writes outside the disposable overlay/artifact directory
fail" and "Credential, network, Git/GitHub mutation, Docker socket, path-escape, symlink,
submodule, archive-bomb, and resource-exhaustion fixtures are blocked" — are only demonstrated by
execution. Phase A delivers their non-executing parts; the Phase B follow-up issue restates them
and owns the proof.

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
  one. Decision 1's CI-only step (above) achieves real boundary verification without needing this
  change at all, which removes most of the original pressure to bundle it.
- **A weaker non-namespace fallback (`--clearenv` + rlimits + restricted `$PATH`, no bwrap
  namespaces) used whenever bwrap is unavailable.** Rejected unconditionally, even gated behind the
  same preflight check — credentials stay readable on disk and egress stays open, which is false
  assurance, worse than an honest refusal, and directly regresses the issue's own stated framing
  that "the current PATH shims are defense in depth, not the boundary."
- **Stopping at workspace-materialization-only, with no `run()`/CLI execution primitive**, leaving
  attack-fixture verification and all execution to #466/#467. Rejected — the acceptance criteria's
  attack fixtures are unfalsifiable without something that actually attempts the forbidden action;
  this design must own the boundary it claims to build. (Under Decision 2 the primitive is
  implemented in the Phase B follow-up issue rather than under #465 itself.)
- **Computing `repo_digest` from a live working-tree walk.** Rejected — `entrypoint.sh` stages
  run-time files into the clone (git-excluded, #472), so a tree walk is non-deterministic run to
  run; a pinned-commit `git ls-tree`/`git cat-file` read is deterministic and reproducible.
- **Binding the whole host filesystem read-only (`--ro-bind / /`) for simplicity.** Rejected in this
  round — it was the first draft's own verification probe, and would expose `~/.claude`,
  `~/.config/gh`, `~/.gitconfig`, `$ARTIFACTS_DIR`, and the live clone to any process that actually
  ran inside it. An explicit allowlist (Requirement 4) costs a short, enumerable bind list in
  exchange for a credential-isolation property that actually holds.

## Open questions (non-blocking)

- **CI spike (Decision 1: "verify with one real CI run before implementation").** The operator ran
  the F4-style probe step (the `docker run` shape of "Live tests and CI step") on a throwaway PR
  on 2026-11-19 before approving this spec; result:
  **partly works** (draft PR #543, runs 44502970621 and 44503860571, `ubuntu-latest`,
  `kernel.apparmor_restrict_unprivileged_userns = 1`). With seccomp and apparmor unconfined,
  `bwrap` creates the user, pid, ipc and net namespaces, but the probe as specified exits 1 with
  `bwrap: Can't mount proc on /newroot/proc: Operation not permitted` (Docker's masked `/proc`
  paths block a fresh procfs mount). The same probe exits 0 when the `docker run` also carries
  `--security-opt systempaths=unconfined`, and it exits 0 without that option when the argv has
  no `--proc /proc`. With default confinement it fails at namespace creation, as expected.
  Consequence for Phase B only (Phase A needs no namespaces and is unaffected): the CI step as
  written in "Live tests and CI step" does not pass. The Phase B issue must choose between adding
  `systempaths=unconfined` to that one CI `docker run` (a third relaxation that Decision 1 did
  not name, so it needs the owner's confirmation) and a sandbox argv without a fresh `/proc`.
  If that step ever fails closed on a hosted runner, the Phase B item returns to
  `needs-discussion`. A runner-side change such as a sysctl is outside what Decision 1 named, and a
  weaker fallback is never the answer (see Alternatives).
- **Enabling infra change for the production container.** A follow-up ticket that makes `bwrap`
  namespace creation possible in the *production* phase container (as opposed to the CI-only step
  Phase B adds) remains open and deferred, for whenever #466/#467 need the live boundary to run
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

## Assumptions (flagged)

- **The materializer does not emit `mechanism_lineage` events or mutate a candidate's
  `lifecycle_state`.** It only re-validates (#464's `validate_candidate`) and materializes; lineage
  advancement is left to whichever ticket actually performs the probe (#467).
- **This repo has no submodules and no `git-lfs` binary today** (reconfirmed this pass) — the
  submodule/LFS refusal rules and fixtures are defensive/forward-looking, not exercised against
  this repo's real content in CI.
- **In the production phase container, `run()` (Phase B) fails closed** to a `BLOCKED` "isolation
  unavailable" verdict — this remains the intended, accepted state for production use.
  `materialize()` (Phase A) needs no namespaces and works there: it builds, digests, bundles and
  removes a workspace without executing anything from it. The boundary is exercised live only by
  Phase B's CI step. Until Phase B lands, nothing in this repository demonstrates that the boundary
  holds, and Phase A's argv-level tests must not be read as doing so.
