# Spike: Evaluate Mem0 as Dark Factory Memory-v2 Idea Source and Optional Backend

**Issue:** omniscient/dark-factory#50
**Status:** spike spec — defines evaluation methodology for a later implement phase to execute and
fill in; this refine pass does not install packages, run code, or produce benchmark results itself
(see Architecture §1).
**Parent epic:** omniscient/dark-factory#140
**Related:** omniscient/dark-factory#36, #40, #37, #163, #241 (new proactive-memory epic — explicitly
must NOT depend on Mem0; see Requirements §7)
**Precedent this spike mirrors:** omniscient/dark-factory#644 / #661 — prior spike evaluating
`agentmemory` as a memory backend, verdict "do not adopt" (see Architecture §2)

---

## Overview / Problem Statement

Dark Factory's `.archon/memory` system (`scripts/memory_write.py`, `memory_retrieve.py`,
`memory_maintain.py`, `memory_import.py`, `eval_memory_quality.py`, plus
`.archon/memory/index.jsonl` + `records/`) is a git-reviewable, markdown-first memory store that
agents read and write across refine/plan/implement/validate/review phases. Issue #50 asks for a
spike evaluating [mem0ai/mem0](https://github.com/mem0ai/mem0) both as (A) a possible optional
backend behind a Dark Factory memory adapter, and (B) an idea source for improving the current
system without adopting Mem0 directly.

Two things sharpen this spike beyond a generic "should we adopt library X" evaluation:

1. **This is not the factory's first memory-backend spike.** #644/#661 already ran a structurally
   identical evaluation against a different OSS candidate (`agentmemory`) and concluded "do not
   adopt," for reasons — no prebuilt distributable artifact, unimplemented role/path filtering,
   retrieval latency far worse than the current approach at the factory's actual (~28-entry) scale,
   non-durable state — that are largely candidate-agnostic operational requirements, not
   `agentmemory`-specific quirks. This spike must produce a directly comparable verdict against the
   same bar (Architecture §2).
2. **A 2026-07-11 product-manager comment folded in requirements from a Meta AI paper** ("Remember
   When It Matters: Proactive Memory Agent for Long-Horizon Agents," arXiv 2607.08716v1) about
   *behavioral state decay* — memory that is stored but stops influencing the acting agent. That
   comment narrows Mem0's role to a possible retrieval/index backend only, explicitly keeps
   intervention-timing and reminder-generation logic factory-owned, and — critically — states the
   new proactive-memory epic **#241 must not depend on Mem0** (Requirements §7).

This spec defines what the spike evaluates, how, and against what decision rule. It does not
contain filled-in benchmark results: per this repo's refine-phase scope boundary (`commands/
dark-factory-refine.md`, "Do NOT implement code, write tests, or edit configuration"), live
installation and execution happens in the implement phase that follows this spec/plan, exactly as
#644/#661 did (spec+methodology authored at refine time, `eval_agentmemory.sh` + filled results
authored and run at implement time).

---

## Requirements

Distilled from the issue body, the folded-in PM comment, and the Q&A below.

1. **Live-evaluation methodology, not a live evaluation.** This spec must define a runnable
   evaluation harness contract (mirroring `scripts/eval_agentmemory.sh`'s shape) — pinned Mem0
   version, install/config steps, representative corpus, query patterns, and a benchmark table with
   placeholder rows — for a later implement phase to execute and fill in. It must NOT claim
   benchmark numbers that were not actually measured.
2. **Reuse the agentmemory spike's four operational-footprint criteria as named, directly-comparable
   benchmark rows**, re-derived against Mem0's actual shape (a Python *library* + vector-store
   dependency, not a separate engine+worker service):
   - Install/deploy footprint (agentmemory: no prebuilt artifact, 3-process source build →
     Mem0: `pip install mem0ai` is trivial, but score the vector-store backend it pulls in)
   - Role/path filter support (agentmemory: silently unimplemented → does Mem0's metadata-filter
     API support the factory's `agent_id`/`path_prefix`/`issue`/`source`/`kind`/`expires` query
     shape used by `memory_retrieve.py`'s `PHASE_SOURCE_MAP` and path-tag filtering?)
   - Retrieval latency vs. the current approach, at the factory's actual corpus scale (~28-34
     entries today; agentmemory's BM25 was 37× slower than grep at this scale)
   - State durability across container restarts (agentmemory: in-memory, full re-import required →
     verify per Mem0 backend choice, don't assume)
3. **Add Mem0-specific risk rows the agentmemory criteria don't cover**, because Mem0's default
   posture differs from agentmemory's: does it run fully local with zero network egress and no
   cloud API key, and is telemetry off by default / auditable? This maps directly to the issue's
   explicit non-goal "do not adopt Mem0 Cloud by default."
4. **Provenance IDs, scoped correctly.** The issue's suggested `--explain` flag on the prototype
   `memory_adapter.py search` command is the right surface for "provenance IDs so reminders can
   cite retrieved records" — but only if it emits a **stable, durable record ID** (the same kind of
   ID `memory_retrieve.py`'s `scan_index()` already dereferences via `records/{id}.json`), not an
   ephemeral rank position. For the Mem0 arm, verify Mem0 can return an equivalently stable ID
   across restarts/re-index; if not, that is itself a footprint-table failure (cross-references
   Requirement 2's durability row). Do not build a separate citation-rendering mechanism in this
   spike — that is reminder-generation, which belongs to epic #241.
5. **"Benchmark Mem0 retrieval-only against selective intervention" is scoped narrowly to a
   retrieval-quality comparison, not an intervention-policy prototype.** Score both arms with
   `eval_memory_quality.py`'s existing methodology against `evals/factory-failures.jsonl`
   (same recall metric, same `PASS_THRESHOLD = 0.5`):
   - **Mem0 retrieval-only arm:** Mem0's `search`/`smart-search` returning top-k every query — this
     is the "expose the full bank automatically every turn" shape the PM comment warns against.
   - **"Selective intervention" arm:** the factory's already-shipped selective retrieval —
     `memory_retrieve.py`'s `PHASE_SOURCE_MAP` role/source scoping, path-tag filtering, label boost,
     and the top-k + token-budget cap in `format_index_output`.
   The spec must name the resulting limitation explicitly: this measures *retrieval selectivity*,
   not the paper's *intervention-timing* quality (targeted reminder vs. explicit silence) — that
   stays a genuine gap deferred to #241, not something this spike should be mistaken for covering.
6. **Do not re-propose already-shipped Part B ideas.** Four of the issue's nine "Mem0-inspired
   memory-v2 ideas" are already implemented in production; two more are partially implemented. The
   spec must document current implementation status with file/function pointers (Architecture §4)
   and scope concrete follow-up-ticket recommendations only to the genuine remaining gaps.
7. **Do not couple epic #241 to Mem0.** Per the PM comment, #241 (the new proactive-memory epic)
   must proceed against the current memory contract (`memory_retrieve.py`/`memory_write.py`) with a
   backend-adapter boundary preserved, regardless of this spike's verdict. This spec's own adapter
   layer proposal (Architecture §3) exists specifically so #241 never needs to touch Mem0 to ship.
8. **Preserve the non-goals verbatim from the issue**: do not replace GitHub as workflow truth; do
   not replace `.archon/memory/*.md` as the human-readable export; do not adopt Mem0 Cloud by
   default; do not add hidden LLM calls without token/cost telemetry; do not ingest secrets/
   credentials/connection strings/raw logs without redaction; do not make memory retrieval
   authoritative over CLAUDE.md, ARCHITECTURE.md, Archon workflow YAML, or conformance/review gates.
9. **Do not create or label follow-up child issues in this refine pass.** The issue's acceptance
   criteria explicitly state follow-up child issues are "recommended or created only after the
   spike recommendation is clear" — i.e., after the implement phase produces a verdict, not now.
   This spec recommends candidate follow-ups (Architecture §5) without creating them.

---

## Brainstorming Q&A

> **Q1:** This refine phase can only write files under `docs/superpowers/specs/` and
> `.archon/memory/` — it cannot install packages, run code, or touch docker-compose. The issue's
> evaluation checklist asks to actually install a pinned Mem0 version and produce filled-in
> benchmark results, exactly like the prior agentmemory spike (#644/#661) did. Should this spec (a)
> define the live-evaluation methodology for a later implement phase to execute and fill in, or (b)
> attempt a recommendation now from desk research alone?
>
> **A1:** (a). The refine command's scope boundary makes live evaluation impossible in this phase
> (any installed package or docker-compose edit would be reverted by the OOS excision gate before
> publishing), and this exactly mirrors how #644/#661 worked: spec + methodology authored at refine
> time, executable eval script (`eval_agentmemory.sh`) + filled results authored and run at
> implement time. This spec should define: the pinned Mem0 version to install, the eval-script
> contract, a spike-only docker-compose profile, representative corpus/query patterns, a benchmark
> table with placeholders for implement-phase results, and a decision-rule/verdict framework. Desk
> research on Mem0's public docs is appropriate only for shaping the methodology and hypotheses —
> not as a substitute for the live run.

> **Q2:** Phase 3 context assembly found that 4 of the issue's 9 "Part B" ideas are already shipped
> in production (`memory_retrieve.py` hybrid scoring, `PHASE_SOURCE_MAP` role-scoping,
> `eval_memory_quality.py` eval suite, markdown-as-source-of-truth), and 2 more are partially
> shipped (`index.jsonl`/`records/` + `memory_maintain.py`'s expire/promote/dedup lifecycle covers
> part of "append-only events" and "active memory view"). Should the spec mark the shipped ideas as
> "already implemented — no new work" with file/function pointers, scoping follow-up
> recommendations only to genuine gaps — or produce recommendations for all 9 regardless of current
> state, treating the issue's list as authoritative scope?
>
> **A2:** Mark ideas 3/4/6/9 as already-implemented with file/function pointers so a future reader
> doesn't re-propose them — recommending follow-up tickets for already-shipped work would itself
> become a scope-discipline violation (CLAUDE.md: "touch only what the plan lists"). Scope concrete
> follow-up-ticket recommendations only to the genuinely unbuilt/partial ideas: full event-sourcing
> purity (completing 1/2), entity extraction (5), procedural handoff memory (7), and retrieval
> explanations surfaced into the agent prompt itself, not just a separate telemetry file (8). The
> issue's Part B list is an idea backlog to re-derive scope from against the live codebase, not a
> scope contract to execute mechanically.

> **Q3:** Should the live-evaluation methodology re-run the same four operational-footprint criteria
> that sank the prior agentmemory spike (prebuilt-artifact availability, role/path filter support,
> retrieval latency vs. the current approach at ~28-entry scale, state durability across restarts)
> as named, directly-comparable benchmark rows — or use a criteria set designed independently, given
> Mem0's different risk shape (library + vector-store dependency + cloud-API-key-by-default, vs.
> agentmemory's separate engine+worker service)?
>
> **A3:** Both — re-run the four as named rows (re-derived against Mem0's actual library+vector-store
> shape, not copy-pasted with agentmemory's service assumptions), AND add Mem0-specific rows for
> risk the four don't cover: runs fully local with zero network egress / no API key, and telemetry
> off by default / auditable. Comparability to the failed agentmemory precedent is the whole point
> of a spike verdict; dropping the four rows would strand the reader with two incomparable
> evaluations of the same underlying decision. But the four must be re-derived, not assumed — e.g.
> "prebuilt-artifact availability" reframes as "install footprint," scoring the vector-store backend
> Mem0 pulls in, not `pip install mem0ai` itself.

> **Q4:** (a) Does the issue's suggested `--explain` flag on `memory_adapter.py search` already
> satisfy the PM comment's "provenance IDs so reminders can cite retrieved records" requirement, or
> does provenance imply something more durable than a per-invocation score breakdown? (b) Should
> "benchmark Mem0 retrieval-only against selective intervention" be scoped narrowly as a
> retrieval-quality comparison reusing `eval_memory_quality.py`'s methodology, or does it require
> prototyping the paper's Phase 2 "targeted reminder OR explicit silence" intervention-policy layer
> now, rather than deferring that to epic #241?
>
> **A4:** (a) `--explain` is the right surface, but the requirement is satisfied only if it emits
> the **existing durable record ID** the factory's own store already provides
> (`memory_retrieve.py`'s `scan_index()` already keys candidates on `entry.get("id")` and
> dereferences `records/{id}.json` — this ID persists across invocations already, independent of
> Mem0). The spec should require `--explain` output = stable record ID + score components, and
> require the Mem0 arm to verify it can return an equivalently stable ID across restarts (cross-
> references the Q3 durability row — if Mem0's IDs aren't stable, that's a footprint failure, not a
> provenance failure to work around). No separate citation-rendering mechanism gets built here —
> consuming an ID to render a reminder is reminder-generation, which is factory-owned and belongs to
> #241. (b) Scope narrowly: reuse `eval_memory_quality.py`'s recall methodology against
> `evals/factory-failures.jsonl` for both arms (Mem0 raw top-k vs. the factory's existing
> `PHASE_SOURCE_MAP`-scoped + capped retrieval). Do NOT prototype the Phase 2 intervention-policy
> layer in this spike — that would give epic #241 a Mem0-shaped starting point, directly violating
> the PM comment's "#241 must not depend on Mem0." The spec must name the resulting limitation
> explicitly: this benchmark measures retrieval selectivity, not intervention-timing quality: the
> latter is a genuine gap deferred to #241's own evaluation, not something this spike covers.

---

## Architecture / Approach

### 1. Refine-phase deliverable vs. implement-phase deliverable (per Q1)

This spec is the refine-phase deliverable. It defines, but does not execute, the following
implement-phase deliverables (mirroring #644/#661's split exactly):

| Deliverable | Phase | Mirrors |
|---|---|---|
| This spec (methodology, benchmark table shape, decision rule) | refine (this doc) | `2026-06-27-agentmemory-memory-backend-spike.md` |
| `scripts/eval_mem0.sh` or `scripts/eval_mem0.py` — runnable harness | implement | `scripts/eval_agentmemory.sh` |
| Pinned `mem0ai` install + local backend config (disposable, non-default) | implement | `iii-config.agentmemory.yaml` + `agentmemory-spike` compose profile |
| Filled-in benchmark table + verdict, appended to this spec or a follow-up results doc | implement | #661's "8 PASS, 0 FAIL on 2026-06-27" results block |
| `.archon/memory` AVOID/PATTERN entry recording the verdict | implement (per memory write-bar) | intended but apparently lost after #644/#661 — implement must actually commit it this time (see Assumptions) |

The implement phase must not skip straight to writing production integration code — per the issue's
explicit instruction, "do not commit a production integration unless the spike explicitly
recommends it," the implement phase's job is to fill in this spec's benchmark table and produce a
verdict, not to build `memory_adapter.py` as shipped infrastructure regardless of outcome.

### 2. Benchmark table shape (to be filled in by implement)

All rows are scored against Mem0 configured for **local, non-cloud operation** (per Requirement 8 /
issue non-goals — a cloud-API-key-requiring configuration is out of scope to benchmark as the
primary candidate).

| # | Criterion | Precedent (agentmemory, #644) | Mem0 result (implement fills in) | Pass bar |
|---|---|---|---|---|
| 1 | Install/deploy footprint | No prebuilt image/npm package; 3-process source build | `pip install mem0ai==<pinned>`; score the local vector-store backend it requires (Chroma/Qdrant/FAISS) | Single-process, no source build, no extra long-running service beyond what's already in `docker-compose.yml` |
| 2 | Role/path filter support | Silently unimplemented (`role=` param ignored) | Does Mem0's metadata-filter API support `agent_id`/`path_prefix`/`issue`/`source`/`kind`/`expires_at` filtering equivalent to `PHASE_SOURCE_MAP` + path-tag filtering? | Filters must actually constrain results, not silently no-op |
| 3 | Retrieval latency at factory scale (~28-34 entries) | BM25 37× slower than grep | Measure Mem0 search/smart-search latency against the same corpus size | Must not regress materially vs. current `memory_retrieve.py` (grep/index-scan, sub-100ms at this scale per #644's report) |
| 4 | State durability across restarts | In-memory; full re-import required | Verify per chosen local backend (embedded persistent store vs. in-memory) | Must persist without a full re-import step on every container start |
| 5 | Zero network egress / no cloud API key required | N/A (agentmemory is self-hosted by default) | Confirm Mem0 can run with telemetry/cloud calls fully disabled; document exact config flags | Must be verifiably off, not "off unless you forget a flag" |
| 6 | Stable, dereferenceable record ID (provenance) | N/A | Confirm IDs survive restart/re-index for `--explain` citation use | ID for a given memory must be stable across a restart |
| 7 | Metadata filter coverage: `infer=False` raw writes vs. LLM-inferred extraction | N/A | Compare cost/safety/quality of both write modes | Raw (`infer=False`) writes must be available as a no-hidden-LLM-call option (Requirement 8) |
| 8 | Retrieval quality: Mem0 top-k-every-turn vs. factory's scoped+capped retrieval | N/A | Run `eval_memory_quality.py`'s recall methodology against `evals/factory-failures.jsonl` for both arms | Report recall delta; no fixed pass bar — informs the verdict, doesn't gate it alone |

### 3. Adapter-boundary architecture (idea source for Part A, applies regardless of verdict)

Per the issue's own recommended layering and Requirement 7 (#241 must not depend on Mem0), the
spike's design target is:

```
GitHub (workflow truth)
  ↓
.archon/memory/*.md          (human-readable durable export — unchanged, stays authoritative)
  ↓
memory event log             (genuine gap — see Architecture §4, idea 1)
  ↓
memory_adapter.py            (stable internal API — the ONLY thing #241 and other consumers import)
  ↓
retrieval backend            (pluggable: current flat-file/index.jsonl impl by default;
                               Mem0 only ever sits here, behind the adapter, opt-in)
```

`memory_adapter.py` is the prototype the issue suggests (`add`/`search` subcommands with
`--kind/--agent/--issue/--path/--memory` and `--paths/--query/--top-k/--explain`). Its job in this
spike is narrow: prove the adapter boundary is viable by sketching the interface, not to ship it as
the new default retrieval path for any existing phase command. Existing call sites
(`load_memory_context.sh`, `memory_retrieve.py` invocations in `commands/*.md`) are unaffected by
this spike regardless of verdict.

### 4. Part B idea status (per Q2 — do not re-propose shipped work)

| # | Idea | Status | Evidence |
|---|---|---|---|
| 1 | Append-only memory events | **Partial** | `index.jsonl` is append-only and `records/<id>.json` are immutable once written, but the authoritative `.archon/memory/*.md` files are still mutated in place by `memory_write.py` (reinforcement, expiry cleanup) and `memory_maintain.py` (expire/promote/dedup) rather than being a pure derived view of an event log. **Genuine gap**: making markdown a projection of the event log, not a second mutable copy. |
| 2 | Active memory view | **Partial** | `memory_maintain.py`'s `op_expire`/`op_promote`/`op_dedup` already compute an effective "active" set (PROVISIONAL→PATTERN promotion at 2+ distinct `issue:` confirmations, `SequenceMatcher`-based dedup at ratio ≥0.90, expiry-based removal). Depends on completing idea 1 to become a true derived view. |
| 3 | Markdown export | **Done — no new work.** `.archon/memory/*.md` already *is* the authoritative human-readable format, not merely an export target. |
| 4 | Hybrid retrieval scoring | **Done — no new work.** `scripts/memory_retrieve.py`'s `scan_index()` + `format_index_output()` already rank by path-prefix specificity (`path_specificity()`), issue-label boost (`compute_label_boost()`), and recency (`created_at` tiebreak), under a `TOP_K_DEFAULT=8` / `TOKEN_BUDGET_DEFAULT=1500`-token cap. |
| 5 | Entity extraction | **Not built — genuine gap.** No extraction of issue/PR numbers, file paths, services, routes, DB tables, or error/test names beyond the existing single `path:` prefix tag and `issue:#N` metadata field. |
| 6 | Role-scoped retrieval | **Done — no new work.** `PHASE_SOURCE_MAP` in `memory_retrieve.py` already scopes refine/plan/implement/validate/review to different allowed `source` tags, and `select_area_files()`/`AREA_PREFIX_MAP` scope which memory files load per changed-file area. |
| 7 | Procedural handoff memory | **Not built — genuine gap.** No continuation-record store for `Continue issue #N` (branch, PR, feedback, files changed, tests run, blockers, next action). |
| 8 | Retrieval explanations | **Partial — genuine gap.** `memory_retrieve.py --emit-trace-to` already writes `memory-trace.json` telemetry (entries selected/dropped, per-file counts, specificity), but this is a separate file for operators, not a human-readable "why was this memory chosen" annotation injected alongside each memory entry in the agent's own prompt. |
| 9 | Memory eval suite | **Done — no new work.** `scripts/eval_memory_quality.py` already scores retrieval quality against `evals/factory-failures.jsonl` historical regressions with a `PASS_THRESHOLD = 0.5`. |

### 5. Candidate follow-up tickets (recommended only, not created — per Requirement 9)

To be finalized once the implement phase's benchmark table (Architecture §2) produces a verdict:

- Event-sourcing purity for ideas 1/2 (independent of the Mem0 verdict — this is a factory-owned
  improvement either way).
- Entity extraction/linking (idea 5) — independent of the Mem0 verdict.
- Procedural handoff memory for `Continue issue #N` (idea 7) — feeds epic #241 but must not depend
  on Mem0 (Requirement 7).
- Retrieval explanations surfaced into the agent prompt itself, not just `memory-trace.json`
  telemetry (idea 8).
- `memory_adapter.py` as a real internal API boundary (Architecture §3) — recommended regardless of
  the Mem0 verdict, since it's what lets #241 and any future backend swap stay decoupled.
- Conditionally, only if the benchmark table clears the bar in Architecture §2: a Mem0-backed
  optional retrieval backend behind the adapter, opt-in and never the default.

---

## Alternatives Considered

1. **Reach a recommendation now from desk research alone, skipping live execution.** Rejected per
   Q1 — the issue's evaluation checklist requires measured results (install footprint, latency,
   durability), and asserting these from documentation alone risks a verdict that doesn't hold up
   under actual factory conditions, exactly the mistake avoided by #644/#661's live-run discipline.
2. **Design a fresh, Mem0-specific criteria set instead of reusing agentmemory's four operational
   rows.** Rejected per Q3 — this would make the two spikes' verdicts incomparable, undermining the
   spike's ability to show a maintainer "does this clear the bar the last candidate failed."
   Reusing the four rows (re-derived, not copy-pasted) plus adding Mem0-specific rows for its
   distinct risk shape (cloud-default, telemetry) captures both concerns.
3. **Produce follow-up recommendations for all 9 Part B ideas regardless of current implementation
   status.** Rejected per Q2 — four ideas are already shipped and two are partially shipped;
   recommending "new" work for them would itself be a scope-discipline violation and would waste a
   future refine/plan/implement cycle rediscovering existing code.
4. **Prototype the paper's Phase 2 intervention-policy (targeted reminder / explicit silence) as
   part of this spike, to jump-start epic #241.** Rejected per Q4 and Requirement 7 — the PM comment
   explicitly forbids #241 depending on Mem0; prototyping the policy layer inside a Mem0-evaluation
   spike would hand #241 a Mem0-shaped starting point regardless of intent.
5. **Build a durable citation-rendering mechanism as part of this spike's `--explain` work.**
   Rejected per Q4 — the factory's existing `records/{id}.json` IDs already provide a durable
   provenance target; consuming an ID to render a reminder is reminder-generation, factory-owned
   work that belongs to #241, not this backend-evaluation spike.

---

## Open Questions (Non-blocking)

- Which local vector-store backend (Chroma, Qdrant embedded, FAISS) should the implement phase pin
  for the Mem0 evaluation? The methodology (Architecture §2, row 1) requires *a* local backend be
  scored, but does not mandate which — implement should pick the one with the lowest install
  footprint and document the choice.
- Should the filled-in benchmark results live as an appendix to this same spec file, or as a
  separate `docs/superpowers/specs/<date>-mem0-spike-results.md`? #644/#661 filled results directly
  into the original spec file; this spike should default to the same pattern unless implement finds
  a reason not to.
- Exact pinned `mem0ai` version is left to the implement phase to select (latest stable at
  implement time) and record — pinning a specific version now in a refine-phase doc risks staleness
  by the time implement actually runs.

---

## Assumptions

- The prior agentmemory spike's commit message (`8769e9f`) claimed a spec file and a
  `.archon/memory` AVOID entry as deliverables, but neither is present in the current repo — only
  the eval script and compose config survived. This spec assumes that was an unintentional loss
  (not a deliberate later removal) and does not attempt to reconstruct the missing artifacts; it
  flags this so the implement phase treats "actually commit the memory entry and keep the spec
  file" as a hard requirement this time, not optional.
- This spec assumes "local, non-cloud Mem0" is achievable with `infer=False` raw writes and a local
  vector store per Mem0's public documentation, based on the issue's own framing ("local/non-cloud
  backend setup" as an explicit evaluation item) — the implement phase's live run is what actually
  confirms or refutes this.
- No ARCHITECTURE.md exists at the repo root today (checked during Phase 3); this spec does not
  assume one will be created as part of this spike, and does not reference it as a landing place for
  the adapter-boundary diagram in Architecture §3.

---

## Live Evaluation Results (implement phase, 2026-07-17)

Harness: `bash scripts/eval_mem0.sh` (issue #50). Full output: see commit for this section — the
table below is a transcription of the harness's own `Row N` / `VERDICT` lines, not independently
re-derived. Pinned version: `mem0ai==2.0.12` (resolved live from PyPI at run time; full dependency
set recorded in `scripts/requirements-mem0-spike.txt`, 83 packages). Two real API-shape mismatches
between the harness's first draft and the pinned `mem0ai==2.0.12` release were found and fixed
before this run (Qdrant `embedding_model_dims` defaulting to 1536 instead of the HuggingFace
embedder's actual 384 dims; `Memory.search()` rejecting a top-level `user_id=` kwarg in favor of
`filters={"user_id": ...}`) — both are spike-script plumbing bugs, not findings about Mem0 itself,
and are captured in commit `78ba251`.

| # | Criterion | Precedent (agentmemory, #644) | Mem0 result | Pass bar | Result |
|---|---|---|---|---|---|
| 1 | Install/deploy footprint | No prebuilt image/npm package; 3-process source build | `pip install mem0ai==2.0.12 qdrant-client sentence-transformers` in 107s, single venv, no source build, no extra long-running service | Single-process, no source build, no extra long-running service | PASS |
| 2 | Role/path filter support | Silently unimplemented | `search(..., filters={"user_id":..., "issue": "#N"})` actually constrains results (fewer, all-matching rows vs. unfiltered) | Filters must actually constrain results | PASS |
| 3 | Retrieval latency (~28-34 entries) | BM25 37× slower than grep | 29ms for an 8-result search against the 45-entry imported corpus | Must not regress materially vs. sub-100ms baseline | PASS |
| 4 | State durability across restarts | In-memory; full re-import required | Record ID resolvable via a fresh process re-opening the same on-disk Qdrant `STORE_PATH` — no re-import needed | Must persist without full re-import | PASS |
| 5 | Zero network egress / no cloud API key | N/A | `MEM0_TELEMETRY` is referenced (load-bearing) in the installed `mem0ai` source, confirming the opt-out isn't a no-op; the one-time HuggingFace model download for `sentence-transformers/all-MiniLM-L6-v2` is an install-time exception, not a per-query call | Must be verifiably off | PASS |
| 6 | Stable, dereferenceable record ID | N/A | Same imported-record ID (`4011fe06-5b65-4255-99c7-4e4f5ec45386` for the first entry) resolves via `m.get()` after the simulated-restart process reopened the store | ID stable across restart | PASS |
| 7 | `infer=False` raw writes vs. LLM-inferred extraction | N/A | `infer=False` verified working — all 45 corpus entries imported via this path with no LLM call; `infer=True` not executed live (would require a real LLM API key, violating the no-Mem0-Cloud-by-default / no-hidden-LLM-call non-goals) | Raw writes must be available with no hidden LLM call | PASS (by construction) |
| 8 | Retrieval quality: Mem0 top-k vs. factory scoped+capped | N/A | Baseline (`memory_retrieve.py`, `PHASE_SOURCE_MAP`-scoped + path-tag filtered): 100.0% recall (5/5 scorable hits). Mem0 arm (`mem0_retrieve_adapter.py`, raw top-8 semantic search over a free-text `"<phase> lessons for <path>"` query): 0.0% recall (0/5). `recall_delta = -1.0000` | Report delta; informs verdict, doesn't gate alone | informational |

**Verdict: `idea-only`**

The harness's decision rule: `FAIL_COUNT` (rows 1-6) `= 0`, so the branch taken is
`recall_delta >= -0.10 → "optional backend"` vs. otherwise `"idea-only"`. Row 8's measured
`recall_delta` is `-1.0000`, far below the `-0.10` threshold, so the rule resolves to `idea-only`:
Mem0 clears every operational bar the agentmemory spike failed on (install footprint, filters,
latency, durability, zero-egress, stable IDs), but its raw top-k semantic retrieval — evaluated
exactly as "expose the full bank automatically every turn" per the PM's Proactive-Memory-Agent
comment — finds none of the 5 scorable historical regressions the factory's own scoped+capped
`memory_retrieve.py` finds all of. This is not a knock against Mem0's engineering; it is the
harness's simple phase+path free-text query (`build_query()` in `mem0_retrieve_adapter.py`) being a
poor substitute for the factory's precise `PHASE_SOURCE_MAP` + `path:` tag exact-match filtering at
this corpus's scale (45 entries) — the same "selective intervention beats general retrieval" result
the folded-in Meta AI paper's ablation predicted (spec Overview, item 2). Per Requirement 5, this
measures retrieval selectivity only, not the paper's intervention-timing quality.

### Candidate follow-up tickets (recommended only, not created — per spec Requirement 9)

- Event-sourcing purity for ideas 1/2 (independent of the Mem0 verdict — this is a factory-owned
  improvement either way).
- Entity extraction/linking (idea 5) — independent of the Mem0 verdict.
- Procedural handoff memory for `Continue issue #N` (idea 7) — feeds epic #241 but must not depend
  on Mem0 (Requirement 7).
- Retrieval explanations surfaced into the agent prompt itself, not just `memory-trace.json`
  telemetry (idea 8).
- `memory_adapter.py` as a real internal API boundary (Architecture §3) — recommended regardless of
  the Mem0 verdict, since it's what lets #241 and any future backend swap stay decoupled.
