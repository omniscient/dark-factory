# Spike: Evaluate Anthropic's Defending-Code Reference Harness for a Dark Factory Security Lane

**Issue:** omniscient/dark-factory#189
**Status:** spike spec — desk-research evaluation with a **committed recommendation** (see Requirements §1
and Q1/A1). Unlike the prior Mem0 spike (#50), this evaluation requires no package install, live
benchmark, or code execution, so the verdict is rendered in this refine pass rather than deferred to
implement.
**Reference repo (verified live, 2026-08-21):** [anthropics/defending-code-reference-harness](https://github.com/anthropics/defending-code-reference-harness)
— public, 7.3k★, actively pushed (last push 2026-08-06). All harness claims below were confirmed by
fetching `README.md`, `docs/security.md`, `docs/agent-sandbox.md`, and `docs/pipeline.md` directly via
`gh api` during Phase 3, not taken solely from the issue body's paraphrase.
**Precedent this spike's process follows:** #50 (Mem0 memory-backend spike,
`docs/archive/2026-07-17-mem0-memory-v2-spike-design.md`) — same "spike spec with a decision rule"
shape, but that spike's verdict required a live benchmark and was deferred to implement; this one's
verdict does not and is rendered here (Q1/A1).

---

## Overview / Problem Statement

Issue #189 asks whether Anthropic's `defending-code-reference-harness` — an open-source reference
pipeline for autonomous vulnerability discovery and remediation — should be adopted by Dark Factory,
either directly as a security lane or as a source of design patterns for the factory's existing
verification loops (conformance, code-review, blast-radius, budget/breaker).

The harness, verified live:

- Ships Claude Code skills (`/quickstart`, `/threat-model`, `/vuln-scan`, `/triage`, `/patch`,
  `/customize`) that are **read/write-only** and safe to run unsandboxed with a human approving each
  tool call.
- Ships an **autonomous pipeline** (`recon → find → verify(grade) → dedupe(judge) → report → patch`)
  that is explicitly "configured for finding C/C++ memory vulnerabilities using Docker and ASAN,"
  described in its own README as "a reference, not a product," requiring a `/customize` porting pass
  for any other language or vulnerability class.
- Requires every autonomous-pipeline agent to run inside a **gVisor** container (kernel-level syscall
  isolation, not just namespace/cgroup isolation) on a Docker network with **no route to the internet**
  except an allowlist proxy to `api.anthropic.com:443` (or Bedrock/Vertex equivalents). Setup needs
  `sudo`, a Linux host, and a one-time `scripts/setup_sandbox.sh` that installs the `runsc` runtime and
  registers it with Docker. Agent-spawning subcommands **refuse to start** outside this sandbox unless
  `--dangerously-no-sandbox` is explicitly passed.
- Wraps target-derived text (ASAN traces, build/test output) fed to the patch agent in
  `<untrusted_data>` blocks with a per-call random id, and documents "constraints must be enforced in
  code, not in prompts" as its core security thesis.

Dark Factory already has an overlapping but differently-shaped set of mechanisms: fresh-context
gate subagents for conformance (`commands/dark-factory-conformance.md`) and code-review
(`commands/dark-factory-code-review.md`), a blast-radius gate (`scripts/gate_blast_radius.py`), a
token-budget system (`config/config.yaml` → `token_optimization`), a de-conflict node
(`workflows/archon-dark-factory.yaml`, id `de-conflict`), and a `docker-socket-proxy`
(`tecnativa/docker-socket-proxy`, read-only-mounted `/var/run/docker.sock`) that restricts the Docker
**API surface** the scheduler container can call. Verified via repo-wide grep: **Dark Factory has no
gVisor, no egress-allowlist proxy for phase-agent containers, and no kernel-level sandbox tier today**
— `docker-socket-proxy` restricts *what Docker API calls the scheduler process may make*, which is a
different property from *what a spawned container's own processes can reach*.

Two issue comments from the "Hermes Agent" trusted comment channel (per `CLAUDE.md`, sanctioned
product-input from the shared `omniscient` account, never itself an authorization to touch
security-sensitive surfaces) fold in an IEEE-style paper's agent-loop failure taxonomy (Nodding Loop,
Amnesiac Loop, Tangled Loop, token blowout) and two patterns ("doubt-driven-development" adversarial
review; NO-GO-by-default for Critical/High findings) as an evaluation lens for how a security lane
should be graded.

---

## Requirements

Distilled from the issue's acceptance criteria and the Q&A below.

1. **This spec must contain the finished evaluation and a committed recommendation now** — one of
   `no-go` / `idea-only` / `skills-only integration` / `advisory security lane` /
   `full sandboxed security lane spike` — not a methodology deferred to a later phase. Unlike #50's
   Mem0 spike, none of #189's four evaluation phases require installing a package, running code, or
   producing measured benchmark numbers; the evidence base is two already-published document sets
   (the harness's own docs, this repo's own code) that refine can read directly (Q1/A1).
2. **Cost/token/safety figures must be explicitly labeled as estimates, derived by analogy to
   existing config/telemetry, never presented as measured.** (Q1/A1)
3. **Any part of the recommendation that would touch `gate_*`, `workflows/`, `config.yaml` budgets,
   sandbox/container runtime, or `.claude/settings*`/`.mcp.json`/`.claude/skills/` must be framed as
   the content of a *recommended, separately human-reviewed follow-up ticket* — never as a
   pre-authorized change, and never as a concrete diff against
   `commands/dark-factory-conformance.md`, `commands/dark-factory-code-review.md`,
   `scripts/gate_lib.sh`, `scripts/budget_gate.sh`, or `config/config.yaml`.** This spec may and must
   design lane mechanics *on paper* (node placement, evaluator isolation contract, evidence artifacts,
   token caps) since two of the five recommendation labels are lane designs and can't be chosen between
   without describing them — but the mechanics are proposal content, not authorization. (Q2/A2)
4. **Treat the two Hermes Agent comments' proposals (clean-room/acting graders, doubt-driven-development,
   NO-GO-by-default) as evaluation lens / idea-source input only** — they inform how this spec frames
   its security-model review and lane design, not a mandate to modify existing gates. (Q2/A2)
5. **Apply the IEEE failure taxonomy as observed-state analysis against Dark Factory's *existing*
   gates first** (conformance/code-review as separate fresh-context nodes, the de-conflict node,
   `emit_verdict()`/`$ARTIFACTS_DIR`/`.archon/memory` persistence) — this needs no authorization, is
   pure repo-reading, and per A2 is "the highest-value part of the evaluation." (Q2/A2)
6. **Preserve the issue's non-goals verbatim**: no running the autonomous pipeline against production;
   no mounting `.env`/cloud credentials/GitHub tokens/SSH keys/user home credential directories into
   agent containers; no privileged Docker or host networking for vulnerability-finding agents; no
   auto-merging generated patches bypassing existing conformance/code-review gates; no replacing
   scheduler/project-board semantics; no assuming the C/C++ ASAN model directly fits Python/TypeScript
   without adaptation.
7. **Do not create or label follow-up child issues in this refine pass** — candidate follow-ups are
   recommended in this spec (Architecture §6), not created, mirroring #50's Requirement 9.
8. **The final recommendation label must be exactly one of the five fixed options** — no hedged or
   compound verdict.

---

## Brainstorming Q&A

> **Q1:** #50's Mem0 spike deferred its verdict to implement because refine's scope boundary
> ("Do NOT implement code, write tests, or edit configuration") made a live pip-install-and-benchmark
> impossible during refine. None of #189's four evaluation phases require installing anything,
> running code, or producing measured numbers — they require reading the harness's published docs,
> reading Dark Factory's own gate code, producing a compatibility matrix and artifact-schema proposal,
> and rendering a judgment-call recommendation from 5 fixed labels. Should this spec (a) contain the
> finished evaluation and a committed recommendation now, or (b) still mirror the Mem0 split out of
> general caution even though its stated reason (inability to run code during refine) doesn't apply
> here?
>
> **A1:** (a). The Mem0 split's rationale was evidentiary, not procedural — #189 has no live run to
> substitute for; its evidence base is two already-published document sets plus this repo's existing
> code. A committed recommendation is the standard refine Phase 5 deliverable ("select the best
> approach"), not an exception, and there is no later research phase in the DAG to defer to (`implement`
> is a code phase) — deferring would either strand the ticket or spend a second run's budget re-reading
> the same material, the wrong trade for a `size:M` ticket. Guardrails: cost/token/safety figures must
> be labeled estimates, not invented benchmarks; anything the published docs don't settle goes in
> Assumptions/Open Questions; anything touching `gate_*`/budgets/sandbox-runtime must be framed as a
> recommended follow-up ticket, never pre-authorized.

> **Q2:** Given A1's guardrail that gate/budget/sandbox-runtime changes must be a recommended
> follow-up ticket, not a spec that itself modifies those surfaces — should this spec (a) treat the two
> Hermes Agent comments' proposals (clean-room/acting graders, doubt-driven-development, NO-GO-by-default)
> purely as evaluation lens / idea-source input, explicitly not authorization to draft concrete changes
> to the existing conformance/code-review gate implementations; or (b) does "security lane" being a
> net-new surface mean these comments are proposing something not yet covered by the `gate_*`
> restriction at all, freeing the spec to design concrete new-gate mechanics now?
>
> **A2:** (a), with a clarification: the "net-new surface" argument in (b) fails — CLAUDE.md's
> restriction names surfaces (`gate_*`, budgets, `workflows/`, `.claude/settings*`), and a security
> lane is born on those surfaces at creation (a gate script is `gate_*`-shaped, a DAG node lives in
> `workflows/`, exploit-attempt caps are budget config, "clean-room evaluators that act" is a
> tool-permission change under `.claude/settings*`/`.claude/skills/`, all in `hard_exclude_paths`).
> "Not wired to block anything yet" also doesn't hold — "NO-GO by default for Critical/High" *is* a
> blocking semantic; landing that default and deferring only the wiring is still pre-authorization.
> But pure "idea-source, no mechanics" is too timid: two of the five recommendation labels
> (`advisory security lane`, `full sandboxed security lane spike`) are lane *designs*, and choosing
> between them requires describing them. Resolution: design the lane on paper inside this spec
> (node placement, evaluator isolation contract, evidence artifacts, token caps), label every named
> surface it would touch as human-review-required, and frame the whole design as the content of a
> recommended follow-up ticket that itself needs a separately human-reviewed spec on a branch — never
> as authorization. Turning the failure taxonomy on Dark Factory's *existing* loops needs no
> authorization at all (it's reading the repo) and is the highest-value part of the evaluation.

> **Q3:** Given the harness's core autonomous pipeline is purpose-built and pinned to C/C++ + ASAN +
> Docker (requiring an unbounded-yield `/customize` port for any other stack) and mandates a gVisor
> sandbox tier that doesn't exist anywhere in Dark Factory's infra today (which relies on
> `docker-socket-proxy` for Docker-API-surface restriction, not kernel isolation) — versus the
> interactive-skills layer and the failure-taxonomy/clean-room-grader *patterns* being language- and
> sandbox-agnostic and already partially mirrored by Dark Factory's separate-fresh-context-agent gate
> design — which of the 5 fixed labels is best-supported, and why reject its two neighbors?
>
> **A3:** `advisory security lane`. The evidence splits the harness into a portable half (interactive
> skills' prompt structure, the recon→find→verify→dedupe evidence discipline, the failure-taxonomy
> lens — all read-mostly analysis producing findings) and an unportable half (the C/C++/ASAN-pinned
> autonomous pipeline and its mandatory gVisor tier). Dark Factory already hosts the portable half's
> shape: fresh-context gate nodes, `emit_verdict()`/`verdict_gate_check.sh`, a `blockers`/`advisory`
> split in `review_result.json`, `fail_open: true`, and a `revise-advisory` auto-fix node. Advisory
> (never blocking) is the right strength because a detector ported off its validated C/C++/ASAN ground
> truth has unknown precision on Python/bash/TypeScript, and because the harness's own safety story for
> the skills layer — "a human approves each tool use" — is a guarantee a headless factory container
> cannot make; the compensating control is that the lane's output can only advise, never patch or gate.
> **Rejecting `skills-only integration` (more conservative):** the skills are built for a human at a
> terminal approving each call; vendoring `/vuln-scan` into `refinement-skills/` with no scheduled or
> per-PR consumer means nobody ever invokes it — it would book the porting cost and realize none of the
> benefit. **Rejecting `full sandboxed security lane spike` (more ambitious):** gVisor is required only
> by the component pinned to C/C++/ASAN, so it would mean installing a `sudo`-level, Linux-only `runsc`
> runtime on every scheduler host — replacing the current API-surface model with a kernel-isolation
> model — to run a memory-safety detector against two codebases (Dark Factory, MarketHawk) that contain
> zero memory-unsafe code. That host-runtime/egress change is exactly the security-sensitive surface
> A1/A2 say can't be pre-authorized from a refine spec, for near-zero expected yield today. If the
> advisory lane later surfaces findings worth acting on autonomously, the sandbox tier becomes its own,
> better-evidenced ticket.

---

## Architecture / Approach

### 1. Verified harness architecture (Phase 1 — summary)

| Harness stage | What it does | Isolation |
|---|---|---|
| Build | Compiles target into an ASAN-instrumented Docker image (one-time per target) | Normal network access (setup phase) |
| Recon (optional) | An agent proposes a partition of the attack surface (e.g. "8 distinct parsers") for parallel Find agents | gVisor, `vp-internal` network, API-only egress |
| Find | N parallel agents craft malformed inputs and run the ASAN binary until a crash reproduces 3/3 | gVisor, isolated per-agent container |
| Verify (Grade) | A **separate** agent reproduces each crash in a fresh, find-agent-untouched container; only the PoC bytes cross over | gVisor, fresh container |
| Dedupe (Judge) | A short no-tools agent compares the verified crash against `reports/manifest.jsonl` (new / better example / duplicate); runs serially to avoid races | N/A (no target execution) |
| Report | A report agent writes a structured exploitability analysis (primitive class, reachability, escalation sketch, severity) from PoC + source only; a grader agent scores it for evidence-backing | gVisor for report agent; grader agent similarly isolated |
| Patch | A separate command: patch agent proposes a fix; a grader agent confirms build success, the original PoC no longer crashes, tests still pass, and a fresh Find agent can't route around the fix | gVisor; target-derived text wrapped in `<untrusted_data>` blocks |

**Compatibility matrix — direct reuse vs. pattern-only vs. reject:**

| Harness capability | Verdict | Why |
|---|---|---|
| `/threat-model` skill | Pattern-only | Author a DF-specific threat-model prompt as the lane's first stage; the published prompt is C/C++-flavored and not vendorable verbatim |
| `/vuln-scan` static skill | Pattern-only | Reuse the "static scan scoped by threat model" shape, retargeted to Python/TS-relevant vuln classes (injection, authz, secrets-in-code, SSRF, deserialization, path traversal) instead of memory-safety |
| `/triage` (confirm/dedupe/rank) | Pattern-only | Reuse the confirm → dedupe → severity-rank shape; DF already has an analogous shape in `dedupe_oos.py` for OOS findings |
| `/patch` skill | Reject (for now) | An advisory-only lane does not generate patches; revisit only if the lane graduates past advisory |
| Autonomous `recon→find→verify→dedupe→report` pipeline (execution-based, ASAN) | Reject direct adoption | Pinned to C/C++ + ASAN + Docker; neither Dark Factory nor MarketHawk contain memory-unsafe compiled code |
| gVisor sandbox + `vp-internal` egress allowlist | Reject (for now) | Infra swap (kernel-isolation tier, `sudo` host setup) disproportionate to near-zero memory-safety yield on Python/TS codebases; revisit only if a future *execution-based* security lane is separately proposed |
| Clean-room grader (grader sees only PoC/finding, not the finder's reasoning) | Pattern-only, high value | Directly strengthens Dark Factory's existing separate-fresh-context-agent gate pattern (§3 below) |
| Structured artifact schema discipline (findings/triage/report JSON, evidence-scored, not prose-trusted) | Pattern-only, adopt | Basis for Phase 3 schemas (§4 below) |
| `<untrusted_data>` wrapping of target-derived text | Pattern-only, adopt broadly | Extends CLAUDE.md's existing trusted/untrusted comment-channel discipline to security-finding evidence fed to any downstream agent |
| Usage marker on API requests (`anthropic-cyber-runbook` header) | N/A | Harness-internal telemetry convention; not applicable to Dark Factory's own request path |

### 2. Security model review (Phase 2)

| Property | Harness | Dark Factory today | Gap |
|---|---|---|---|
| Process isolation | gVisor (`runsc`) — kernel-level syscall/filesystem boundary | Plain Docker containers per phase run; `docker-socket-proxy` restricts which Docker **API calls** the scheduler can make, not what a spawned container's own processes can reach | No kernel-isolation tier exists; would need net-new host setup (`sudo`, Linux-only `runsc` install) |
| Network egress | `vp-internal` Docker network, no internet route, allowlist proxy to `api.anthropic.com:443` only | No egress-allowlist proxy for phase-agent containers today (verified: zero "gVisor"/"sandbox" hits repo-wide outside this issue) | A security lane that only reads diffs/source (no target execution) does not need this; flag as a gap only relevant if a future execution-based lane is proposed |
| Setup vs. attack phase separation | Build (networked) → freeze image → attack phase (API-only egress) | Analogous split already exists structurally: `implement` (networked, writes code) → `conformance`/`code-review` (read the frozen diff, no target execution) | Already substantially mirrored for a *static/read-only* lane; would need to be built fresh only for an execution-based lane |
| Credential handling | Never mounts `~/.aws`, `.env`; Bedrock/Vertex creds passed via env, not files; IMDS/instance-profile creds deliberately unsupported | `.factory/adapter.yaml` `hard_exclude_paths` already excludes `deploy/instances/`, `.claude/settings*`, `.mcp.json` from all phase-agent write access | Aligned in spirit; a security lane must not read or echo these paths into findings — add explicit exclusion when defining the lane's scan scope |
| Untrusted-data handling | `<untrusted_data>` blocks with per-call random id around target-derived text fed to the patch agent | CLAUDE.md already distinguishes trusted (Hermes Agent signed) vs. untrusted comment-channel input; `diff_rank.py`/`fmt_hunk_filter.py` already isolate diff content from prose | Pattern-compatible; a security-lane grader should receive finding evidence (file/line/repro) wrapped similarly, not narrative claims |
| Patch auto-application | Never auto-applies; "review every generated diff before upstreaming" | Conformance/code-review gates already require human-reviewed PR merge; `epic_autopilot` explicitly excludes `.claude/skills/`, `deploy/**` from any autonomous path | Already aligned — reinforces Requirement 6 (no auto-merging security patches) |

### 3. Applying the IEEE failure taxonomy to Dark Factory's *existing* loops (needs no authorization — Q2/A2)

| Failure mode | Observed state in Dark Factory today | Verdict |
|---|---|---|
| **Nodding Loop** (fixer grades its own patch) | `implement` writes the diff; `conformance` and `code-review` are separate DAG nodes (`commands/dark-factory-conformance.md`, `commands/dark-factory-code-review.md`), each spawning a fresh-context Opus subagent with its own prompt and no visibility into the implementer's reasoning — only the diff and spec/issue context. | **Partially mitigated.** The reviewer doesn't share the implementer's session, but it does read implementer-authored prose (`implementation.md`, commit messages) alongside the diff — closer to "sees the maker's claim" than the harness's grader (PoC bytes only). A security-lane grader adopting stricter evidence-only framing (finding + repro, not the scanning agent's narrative) would be a genuine level-up; this is exactly the kind of mechanic §4/§5 below propose, gated as a follow-up ticket per Requirement 3. |
| **Amnesiac Loop** (evidence not persisted) | `emit_verdict()` (`scripts/gate_lib.sh`) writes a durable `STATUS`/`FINDINGS_COUNT`/`SEVERITY` header to `$ARTIFACTS_DIR/*.md`; blocking findings are posted as durable GitHub issue/PR comments; recurring lessons are written to `.archon/memory/*.md` via `write_memory_entry()`. | **Reasonably mitigated already.** A security lane should follow the identical persistence pattern (ephemeral per-run JSON + durable advisory comment + memory entries only for *recurring* classes of finding) rather than inventing a new one. |
| **Tangled Loop** (concurrent agents mutate the same branch/artifact) | The `de-conflict` node (`workflows/archon-dark-factory.yaml`, id `de-conflict`) already scans for concurrent-branch conflicts before other gates run. | **Already covered for the write path.** A security lane that is strictly read-only/advisory (never commits) introduces no new tangled-loop surface — this is itself a reason to prefer `advisory` over any design that has the lane write patches or mutate the branch. |
| **Token blowout** (uncapped exploit attempts) | `token_optimization.budgets` in `config/config.yaml` already caps refine/plan/implement/conformance/code-review per scenario, enforced via `scripts/budget_gate.sh`. A security lane has no budget entry today. | **Gap — needs a new budget entry.** Since this touches `config/config.yaml`, it is explicitly follow-up-ticket work per Requirement 3, not something this spec authorizes. |

### 4. Proposed security lane design (paper only — recommended follow-up ticket content, per Q2/A2)

This section designs the `advisory security lane` recommendation's mechanics so the recommendation is
falsifiable and costable. **None of this is authorized for implementation by this spec** — it is the
starting content for a separately human-reviewed spec/PR, per Requirement 3, because it names
`gate_*`-shaped files, a `workflows/` node, and `config.yaml` budget/tool-permission surfaces.

- **DAG node placement:** a new advisory-only node (working name `security-scan`), positioned parallel
  to `code-review` (same `depends_on: [push-and-pr]` shape), feeding into `report`. It never sets
  `needs-discussion`, never moves the board to `Blocked`, and never blocks `status-in-review`/
  `review-gate` — mirroring `code_review.fail_open: true` and the existing `revise-advisory` node's
  "advisory findings never halt the pipeline" contract.
- **Evaluator isolation contract:** a fresh-context subagent (same "pinned to Opus, no orchestrator
  model inheritance" convention as conformance/code-review) that receives the pre-triaged,
  `diff_rank.py`-ranked diff (reusing existing infra, not duplicating it) — but, per §3's Nodding Loop
  finding, is explicitly **not** given `implementation.md` prose or commit messages, only the diff
  itself plus a DF-specific vuln-class rubric (adapted from `/vuln-scan`, Requirement 4). This is the
  concrete "doubt-driven-development" adoption the Hermes Agent comments proposed, scoped to this one
  new node rather than retrofitted onto the existing conformance/code-review graders.
- **Evidence artifact, not prose-trusting:** each finding must cite file/line and (where the finding
  type allows static confirmation) a grep/AST-match excerpt as evidence, mirroring the harness's
  "claims backed by evidence, not plausible prose" grader discipline. No PoC execution — this lane is
  static/read-only, consistent with rejecting the gVisor/execution surface (Q3/A3).
- **Token cap:** a new `token_optimization.budgets.security-scan` entry, sized by analogy to the
  existing `code-review` budget (§6, cost estimate) — config-file work, explicitly follow-up-ticket
  scope.
- **GitHub reporting behavior:** a single advisory PR comment (same shape as `code_review_payload.py`'s
  `advisory` array, rendered via the existing marker-comment upsert primitive
  `tracker comment --marker ...` per the `[AVOID]` memory entry on DAG-node comment idempotency) —
  never an inline blocking review, never a board-status change.

### 5. Proposed artifact schemas (Phase 3), adapted from the harness's shapes

All of these are **proposals for the follow-up ticket to implement**, not files this spec creates.

| Harness artifact | Dark Factory adaptation | Where it would live |
|---|---|---|
| `THREAT_MODEL.md` | Same shape (scope, trust boundaries, assets, prioritized attack surface), authored once per target repo (dark-factory, MarketHawk) and refreshed periodically like `docs/codeindex-hotspots.md`, not regenerated per-PR | Committed, living doc: `docs/security/THREAT_MODEL.md` |
| `VULN-FINDINGS.json` | Array of `{id, category, file, line, description, evidence_excerpt, confidence}`; `category` adapted from CWE-ish classes relevant to Python/TS (injection, authz, secrets-in-code, SSRF, deserialization, path traversal) instead of memory-safety primitives | Ephemeral, per-run: `$ARTIFACTS_DIR/security/VULN-FINDINGS.json` |
| `TRIAGE.json` | Same findings, deduped/ranked, with `{verdict: confirmed\|false_positive\|duplicate, grader_score}` — reuses the shape of DF's existing `dedupe_oos.py` action list (`create`/`comment`/`suppress`) as prior art for the dedupe step | Ephemeral: `$ARTIFACTS_DIR/security/TRIAGE.json` |
| `EXPLOITABILITY-REPORT.json` | Per confirmed finding: `{severity, reachability, escalation_sketch, evidence: [{file, line, excerpt}]}` — evidence-cited, not prose | Ephemeral: `$ARTIFACTS_DIR/security/EXPLOITABILITY-REPORT.json` |
| `PATCH-VERIFICATION.json` | **Not built now** — the advisory lane does not generate or verify patches (Requirement 6, Q3/A3). Kept as a placeholder shape only for a possible future non-advisory follow-up, should the lane ever graduate. | N/A |
| `SECURITY-LANE-RUN.json` | Run manifest: `{run_id, target_commit, findings_by_severity, token_spend, duration, rubric_version}` — propose extending `scripts/factory_core/run_record.py` (DF's existing per-run record pattern) rather than inventing a parallel format | Ephemeral, written by `run_record.py`'s existing mechanism |

Structured JSON stays ephemeral (`$ARTIFACTS_DIR`, never committed) except `THREAT_MODEL.md`, which is
the one artifact worth a durable, living, low-churn doc — consistent with how `docs/codeindex-hotspots.md`
and `ARCHITECTURE.md`-shaped docs are already treated in this repo. This avoids permanently committing
raw vulnerability-finding detail into public git history any longer than a PR review needs it.

### 6. Cost / token / safety assessment (labeled estimates — Requirement 2)

- **Token cost (estimate, by analogy — not measured):** a `security-scan` node reusing the same
  diff-rank + single-subagent-call shape as `code-review` would be expected to fall in a similar order
  of magnitude to `code_review`'s provisional budget (`22000` tokens, `config/config.yaml`). This is an
  analogy to an existing, structurally similar node — not a benchmark of the actual rubric prompt,
  which does not exist yet.
- **Safety:** confining the lane to advisory-only, static/read-only analysis (no target execution, no
  patch generation, no auto-merge) means it does not need gVisor, an egress-allowlist proxy, or any
  credential exposure beyond what `conformance`/`code-review` already have — this is the direct
  consequence of rejecting the execution-based half of the harness (Q3/A3), and is why the marginal
  safety surface added is small relative to `full sandboxed security lane spike`.
- **Infra cost:** no new host-level setup (no `sudo`, no `runsc` install, no new Docker network) —
  reuses the existing subagent/model-pin/diff-rank/`emit_verdict`/marker-comment machinery. This is a
  materially lower cost than `full sandboxed security lane spike`, which would require standing up a
  gVisor tier on every scheduler host.

---

## Alternatives Considered

1. **`no-go`.** Rejected — the harness's static-analysis and clean-room-grader patterns are genuinely
   applicable and cheap to adopt (§4-§6); a blanket no-go would discard real, low-cost value.
2. **`idea-only`** (patterns inform future work, nothing built or specified concretely). Rejected —
   two of the five labels are lane *designs*; picking between them requires the concrete mechanics in
   §4-§5, which idea-only would omit. The evidence supports going one step further than idea-only
   while stopping short of authorizing any implementation (Requirement 3 still applies regardless of
   label).
3. **`skills-only integration`** (vendor `/threat-model`/`/vuln-scan`/`/triage` skill prompts into
   `refinement-skills/` with no DAG trigger). Rejected per Q3/A3 — these skills are designed for a
   human approving each tool call; with no scheduled or per-PR consumer in a headless factory, nobody
   would ever invoke them, so the porting cost would be paid with none of the benefit realized.
4. **`full sandboxed security lane spike`** (stand up gVisor + egress allowlist, run the customized
   autonomous pipeline). Rejected per Q3/A3 — the gVisor/execution tier is required only by the
   component pinned to C/C++ + ASAN, which has near-zero yield against Dark Factory's and MarketHawk's
   actual (Python/TS, no compiled memory-unsafe code) surface; the host-runtime and egress changes this
   would require are exactly the security-sensitive-surface changes A1/A2 say cannot be pre-authorized
   from a refine spec.
5. **Building the `security-scan` DAG node, budget entry, and rubric prompt directly in this refine
   pass** (since refine could technically write prose describing exact diffs). Rejected per Q2/A2 and
   CLAUDE.md's "gate changes get their own reviewed ticket" — `gate_*`, `workflows/`, and
   `config.yaml` are named security-sensitive surfaces; the comment-channel input that inspired this
   evaluation cannot itself authorize touching them, regardless of how confident the resulting design
   (§4-§5) is.

---

## Open Questions (Non-blocking)

- Should the follow-up ticket's DF-specific vuln-class rubric (§4) be authored fresh, or adapted from
  an existing OWASP/CWE-mapped checklist already used elsewhere in the org? This spec did not find
  prior art for this in the Dark Factory or MarketHawk repos.
- Should `THREAT_MODEL.md` be per-target-repo (one for dark-factory, one for MarketHawk) or a single
  doc with per-repo sections? Precedent (`docs/codeindex-hotspots.md`) is per-repo; this spec assumes
  the follow-up ticket will do the same but does not mandate it.
- The harness's `docs/detection-response.md` track (`/dnr-hunt`, `/dnr-respond` — "an attacker is
  already in the logs") was not evaluated in depth here; it is a distinct use case (incident response,
  not pre-emptive scanning) or plausible relevant to Dark Factory's own run-telemetry/failure-signature
  tooling (`scripts/factory_core/error_signature.py`, `post_mortem.py`). Flagged as out of this spike's
  scope, worth a note in the follow-up ticket rather than a blocking question here.
- Exact token budget for `security-scan` (§6) is an analogy, not a measurement; the follow-up ticket
  should calibrate it the same way `conformance`/`code-review` budgets were calibrated (provisional
  value, "recalibrate per runbook Follow-up Path" per `config/config.yaml`'s existing comments).

---

## Assumptions

- This evaluation is based on the harness's `README.md`, `docs/security.md`, `docs/agent-sandbox.md`,
  and `docs/pipeline.md`, fetched live via `gh api` on 2026-08-21. It did not clone the repo or read
  the actual skill prompt source (`.claude/` directory), the `harness/` orchestration code, or
  `dnr_harness/`. Claims about *documented* behavior are verified; claims about undocumented
  implementation detail are not made.
- No `ARCHITECTURE.md` exists at the Dark Factory repo root (checked during Phase 3); this spec does
  not assume one will be created and does not reference it as a landing place for §3/§4's tables.
- This spec assumes the `docker-socket-proxy` setup (`deploy/docker-compose.yml`) restricts the
  **scheduler's** Docker API surface specifically, based on reading its compose service definition and
  the inline comment in `docker-compose.preview.yml` referencing "the docker.sock connection-hijack the
  socket-proxy blocks" — it does not claim this extends to phase-agent containers' own process/network
  isolation, which this spec found no evidence of anywhere in the repo.
- The `security-scan` node's projected token cost (§6) assumes a single-subagent-call shape similar to
  `code-review`; if the follow-up ticket's rubric design needs multiple passes (e.g. a separate
  triage/dedupe step per §4-§5), the actual cost could be materially higher than this estimate.

---

## Recommendation

**`advisory security lane`**

A static, read-only, fresh-context-evaluator lane — pattern-adopting the harness's clean-room-grader
and evidence-scored-artifact discipline, explicitly never blocking, never patching, never requiring
gVisor or an egress-allowlist change — is the best-supported outcome given the evidence in Q1-Q3 and
Architecture §1-§6. Concrete lane mechanics (§4-§5) are proposed as the starting content for a
separately human-reviewed follow-up ticket per Requirement 3; this spec does not itself authorize any
change to `gate_*`, `workflows/`, `config.yaml`, or `.claude/settings*`.
