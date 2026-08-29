# Verifier abstraction (A3) — shared verdict contract and target-registered check-only verifiers

**Issue:** #197 · **Epic:** #194 (Factory/Target boundary v1)
**Depends on:** #195 (A1 `loops:` schema, shipped), #301 (A1.5 five-move loop-schema
restructuring, spec-approved 2026-08-28, not yet merged)
**Status:** spec-pending-review
**Operator note (2026-08-28):** refine/plan may proceed; **implement waits for #301 to
land** — this ticket depends on #301's `verification.verifier`/`handoff.manifest`/
`handoff.outputs`/`persistence.artifacts` field shape, which does not exist on `main`
yet. Spec gate reviewed by the operator.

---

## Overview / Problem Statement

Dark Factory today hand-wires four maker/checker instances: refine↔product-owner,
plan↔architect, conformance↔reviewer, code-review↔reviewer. Each pairs an
Opus-4.8-pinned checker subagent with a deterministic Python/bash adjudicator. Two of
the four (conformance, code-review) are true blocking gates: they write a `STATUS:`
verdict artifact via `scripts/gate_lib.sh::emit_verdict`, and a dedicated DAG node
(`conformance-gate`, `review-gate`) reads that artifact through
`scripts/verdict_gate_check.sh` to fail-closed-block the pipeline. The other two
(refine, plan) are open-ended brainstorming Q&A loops with a structurally different
escalation path (`UNCERTAIN:` → `needs-discussion` label → clean exit 0), not a
PASS/BLOCK verdict, and nothing downstream gates on them.

Epic #194 (Factory/Target boundary v1) needs a shared verifier abstraction so that a
target repo can declare its own check-only verifier against a `.factory/adapter.yaml`
`loops:` entry (the `verification.verifier` field #301 is adding) and have it plugged
into the *same* fail-closed verdict machinery the factory already trusts for its own
gates — without a target's verifier being able to "grade its own homework." This spec
answers three questions the issue leaves open: what exactly gets extracted and shared
across all four pairs (they are less alike than the issue's framing implies); what
"gates the loop's handoff" can concretely mean given no loop-dispatch engine exists yet;
and how "maker never validates maker" is structurally checkable against a schema that
has no explicit maker-identity field.

**Verified starting facts (re-checked against `main` `2e12b8c`, 2026-08-28):**

- `scripts/gate_lib.sh::emit_verdict` writes `STATUS / GATE_TYPE / FINDINGS_COUNT /
  SEVERITY` — used today by `commands/dark-factory-conformance.md` and
  `commands/dark-factory-code-review.md`. `scripts/gate_blast_radius.py` (validate's
  blast-radius check) emits the identical four-line shape independently, without a
  checker subagent at all — it's purely deterministic.
- `scripts/factory_core/run_record.py::_parse_artifact_stage(name, content)` parses
  `conformance`/`review`/`validation`/`conflict_resolution` with hand-rolled,
  per-`name` `if`/`elif` branches (lines 304–373). `cmd_assemble`'s
  `artifact_names = ["validation", "conformance", "review", "conflict_resolution"]`
  (line 563) and `GATE_STAGE_NAMES = ("validation", "conformance", "review")` (line 28,
  feeds `_compute_outcome`'s blocking-verdict/scoring policy) are both hardcoded to this
  fixed ticket-lifecycle set — `blast.md` is not among them despite emitting the same
  shape.
- `scripts/verdict_gate_check.sh` already implements the fail-closed contract AC3 asks
  for: `STATUS: BLOCKED`, or a missing/unparseable verdict file, exits 1 and blocks the
  DAG node it gates; `PASS`/`SKIPPED`/`ERROR` proceed.
- `scripts/hooks.sh::run_hook` is the shipped precedent for "target executable/hook >
  factory default, check-only, factory owns all resulting side effects" (the pattern
  the issue's "smoke-gate precedent" bullet refers to): it resolves
  `${CLONE_DIR}/.factory/hooks/<name>`, runs it with a fixed exported env contract
  (`CLONE_DIR`, `ARTIFACTS_DIR`, `ISSUE_NUM`, `FACTORY_REPO_SLUG`), and keeps all
  state/board/label side effects factory-side (`_smoke_on_red`/`_smoke_on_green`).
- **No loop-dispatch/execution engine exists anywhere in the repo.** `adapter.py`
  parses/validates `loops:`; `run_record.py` surfaces it verbatim for provenance
  (`adapter.get(clone_dir, "loops") or []`, line 602); nothing schedules, triggers, or
  runs a declared loop's maker or checker. Epic #194's own "Out of scope" section
  confirms this is expected at this stage ("v1 loops run inside factory-dispatched
  containers" — the dispatcher is future work), and its children list shows no ticket
  (#196, #198, #199, #200, #201) claims loop-dispatch ownership. #197 does not build one
  either (Q&A below).
- #301's approved (not-yet-merged) A1.5 shape reshapes each `loops:` entry into
  `discovery{trigger,inputs}` / `handoff{outputs,manifest}` /
  `verification{verifier,stop_condition}` / `persistence{artifacts}` /
  `scheduling{failure_behavior}`, plus optional `role_card`/`economics`/`skills`/
  `human_checkpoint`/`budget_caps`. Its own "Consumers" table assigns `#197 (A3) |
  verification.verifier | Opaque string reference resolved by A3; maker≠checker rule
  enforced there, not in the schema."
- The #311 spike's "Handoff to #197" section (`docs/archive/2026-08-28-contract-driven-
  execution-trajectory-conformance-spike-design.md`) verdicts **"#197: PROCEED as
  scoped"**, inheriting two acceptance criteria this spec adopts as Requirements: (i) a
  verifier consumes only observable events/artifacts, never agent self-report; (ii) a
  missing/failed required deliverable fails closed before handoff. It also places a
  *different*, ticket-lifecycle-scoped verifier (a future `contract.md` gate for #300-
  class completion checks) as a sibling of Gate 2 in the existing DAG — that gate is
  #197/#198's to build in a *later* pass once #240's replay substrate and #198's
  contract-satisfaction stop condition exist; it is not this spec's deliverable and is
  listed under Open Questions, not Requirements.

---

## Requirements

1. **One documented verdict schema.** `STATUS ∈ {PASS, BLOCKED, SKIPPED, ERROR}`,
   `GATE_TYPE` (free-form identifier — `conformance`, `code-review`, `blast`, or a
   target loop's own name), `FINDINGS_COUNT` (non-negative int), `SEVERITY ∈ {none,
   low, medium, high, critical}`. Canonicalized once, in code, as the single source of
   truth `scripts/gate_lib.sh::emit_verdict` and `run_record.py` already imply but never
   wrote down together.
2. **`run_record.py::_parse_artifact_stage` refactored, not reshaped.** It becomes a
   thin per-name wrapper over one generic parser; `artifact_names` and
   `GATE_STAGE_NAMES` are **unchanged** (still the fixed ticket-lifecycle four) — this
   ticket must not alter which stages feed `_compute_outcome`'s scoring policy, per the
   issue's own "without changing their behavior." Existing `tests/test_run_record.py`
   parse/outcome tests pass unmodified (byte-compatible artifacts, byte-compatible
   parse results).
3. **Checker-invocation contract documented once, referenced four times.** The
   Opus-4.8 pin, the read-only tool grant (`Glob`, `Grep`, `Read`), and the
   clone-live-first persona/rubric resolution pattern (already used by
   conformance/code-review; refine/plan keep reading their baked `/opt/refinement-
   skills/` prompts as-is — no behavior change) are written once in a new reference doc
   and linked from all four `commands/*.md` files, replacing each file's own copy of
   the same three sentences. This is documentation deduplication, not a new spawn
   mechanism — the `Agent` tool invocation itself stays in each command's own prose,
   since a markdown-interpreted phase command cannot delegate a tool call to shared
   code.
4. **A verifier resolver for target-registered verifiers**
   (`scripts/factory_core/verifier.py`), modeled on `hooks.sh::run_hook`'s
   target-over-default, check-only, factory-owns-side-effects precedent, generalized
   from a fixed `.factory/hooks/<name>` convention to an arbitrary
   adapter-declared path (`verification.verifier`, resolved relative to
   `CLONE_DIR`):
   - Executes the resolved path with the same four-variable env contract
     `run_hook` already uses, plus `LOOP_NAME` (the verifiers/hooks env contract
     documented once, per Requirement 3's doc).
   - Accepts two output modes: **structured** (the verifier's stdout already begins
     with `STATUS:` lines — parsed directly through the Requirement 1 schema) and
     **bare-exit-code** (no structured output — exit 0 synthesizes `STATUS: PASS`,
     non-zero synthesizes `STATUS: BLOCKED, FINDINGS_COUNT: 1, SEVERITY: high`),
     mirroring `smoke-gate`'s existing bare-exit-code convention as the low-effort
     on-ramp for a target's first verifier.
   - Writes the normalized verdict to a caller-supplied artifact path via the
     Requirement 1 schema (`emit_verdict`-equivalent, in Python).
   - Fails closed (`STATUS: BLOCKED`) if the declared path is missing, not executable,
     times out, or the process cannot be started — never silently skips (AC3). `ERROR`
     is reserved for "the verifier ran and reported it could not complete" (a
     structured-mode self-report, e.g. a tooling crash inside the verifier itself) and
     is **not** auto-pass-through for target verifiers — unlike `code_review.fail_open`,
     there is no config knob defaulting target-verifier `ERROR` to non-blocking, because
     AC3 requires "missing/failing cannot hand off" as the default, not an opt-in.
5. **Maker≠checker enforced structurally as a path-disjointness rule**, per epic #194's
   non-negotiable ("Maker never validates maker — every consequential loop gets an
   independent checker") and #301's schema (which exposes no maker-identity field
   separate from the loop entry's own declared outputs):
   ```
   owned = {handoff.manifest} ∪ set(handoff.outputs) ∪ set(persistence.artifacts)
   if normpath(verification.verifier) in {normpath(p) for p in owned}:
       reject — a loop cannot declare its own handoff producer or a file it writes
       as its own verifier.
   ```
   String/path comparison only (`os.path.normpath`, no filesystem access, no existence
   check) — consistent with #301's "opaque reference, not resolved" treatment of these
   fields. This is the declaration-time half of maker≠checker; the load-bearing half is
   execution isolation (Requirement 6). No cross-loop uniqueness constraint — two
   different loops sharing one verifier script is independence-preserving, not a
   violation.
   Implemented as `verifier.assert_verifier_independent(entry)`, called from
   `adapter.py::load()` immediately after each existing `_validate_loop(entry, i)` call
   — an additive one-line integration hook. It does not modify `_validate_loop` itself,
   which stays #301's owned surface. **This call site can only be added once #301 has
   merged** its nested shape into `adapter.py`; until then `verifier.py`'s independence
   check is developed and unit-tested against the schema #301's spec already documents
   (fixture dicts shaped per its R1 example), and the plan/implement phases must
   re-verify the field names against `main` on the day they start, per the
   `.archon/memory` precedent for re-checking a dependency's shape before building
   against it.
6. **The A2 permission-profile gap is recorded, not silently enforced.** #196 (A2,
   side-effect-level-to-permission-profile enforcement) has not shipped — there is no
   container/tool-permission mechanism in this repo to actually confine a verifier
   process to "level-1 read-only." #197 therefore: (a) declares the requirement in the
   verifier's run-record entry (a `required_profile: level-1` field on the recorded
   verdict, so a future #196 enforcement layer has something to check against and its
   absence is visible, not silently assumed); (b) fails closed (`STATUS: BLOCKED`) if
   asked to run a verifier whose loop entry does not resolve a level (never guesses);
   and (c) does **not** attempt to sandbox or restrict what the verifier process can
   actually do at the OS/container level — that enforcement is #196's explicit,
   already-chartered scope (`#196 (A2) consumes side_effect_level, ... human_checkpoint,
   budget_caps` per #301's Consumers table). Declaring-not-enforcing is the same
   posture #190's state-governance scorecard took for its own advisory checks before
   its own promotion ticket, and matches this ticket's own dependency edge on #196.
7. **AC1 restated accurately.** The issue's own phrase "all four existing **gates**"
   overstates today's reality — refine and plan are not gates (no `refine-gate`/
   `plan-gate` DAG node exists, and adding one is new gating behavior on a
   safety-adjacent surface, which CLAUDE.md reserves for its own reviewed ticket, not
   this refactor). AC1 is satisfied as: **all four maker/checker pairs invoke their
   checker through the shared invocation-contract doc (Requirement 3); the
   verdict-emitting producers (conformance, code-review, plus the already-deterministic
   validation/blast producers) emit and parse through the shared verdict schema
   (Requirements 1–2); existing gate tests (`test_run_record.py`,
   `test_verdict_gate_check.sh`, `test_verdict_gate_dag.py`) stay green with
   byte-identical verdict artifacts.**
8. **AC2/AC3 demonstrated via tests, not new pipeline wiring.** Since no loop-dispatch
   engine exists (and building one is explicitly out of this ticket's scope — see
   Q&A), "a target loop can declare a verifier... and its verdict is parsed, recorded,
   and gates the loop's handoff" is proven by: (a) unit tests on
   `verifier.resolve_and_run()` against real, executable fixture scripts (structured
   and bare-exit-code, passing and failing); (b) unit tests on
   `verdict.parse_verdict()` proving any `GATE_TYPE` — including one a target loop
   would invent — round-trips through the same generic parser the ticket-lifecycle
   gates use; (c) one integration test that pipes `verifier.py`'s written artifact
   through the **real** `verdict_gate_check.sh` (subprocess, not a mock) and asserts
   exit 0 on PASS, exit 1 on BLOCKED, and exit 1 on a missing artifact — the literal
   fail-closed mechanics AC3 describes, exercised end-to-end without a live scheduler.
   No new node is added to `workflows/archon-dark-factory.yaml`; wiring a target
   verifier into a live, scheduled loop is the loop-dispatcher's job (out of scope,
   Alternatives below).

---

## Brainstorming Q&A

> **Q:** AC2 says "a target loop can declare a verifier... and its verdict... gates the
> loop's handoff," but no loop-dispatch engine exists anywhere in the repo. Should
> #197's scope be (a) build only a standalone verifier-invocation primitive, proven
> against real executing seams but not a live dispatcher, (b) narrow AC2/AC3 to the
> ticket-lifecycle DAG only, or (c) also build a minimal loop dispatcher?
>
> **A:** (a), with one amendment: not purely synthetic — demonstrate AC2/AC3 through one
> real executing seam (`hooks.sh::run_hook`'s target-over-default precedent for
> resolution, `verdict_gate_check.sh`'s fail-closed pattern for gating), not fixtures
> alone. No epic #194 child (#196/#198/#199/#200/#201) claims loop-dispatch ownership —
> it's a genuine, unowned gap, but it's a scheduler/entrypoint surface and stays out of
> this refactor per CLAUDE.md's "gate changes get their own reviewed ticket"; file it as
> a spillover child of #194 if it needs to be tracked. Narrowing to (b) would under-
> deliver the issue's own third scope bullet and the #311 spike's "PROCEED as scoped"
> verdict for #197.

> **Q:** Given refine/plan are structurally different from conformance/code-review (no
> verdict artifact, no gate node, an `UNCERTAIN:`-based escalation instead of PASS/
> BLOCK), should "refactor the four existing pairs onto the abstraction" (a) extract
> only the common invocation mechanic across all four while verdict-emission stays
> conformance/code-review-only, (b) also make refine/plan emit a formal verdict artifact
> for future-proofing even though nothing consumes it yet, or (c) something else?
>
> **A:** (a). The verdict-emitting producers (`conformance.md`, `review.md`,
> `validation.md`, `blast.md`) and the Opus-pinned-checker-subagent pairs are two
> different sets that overlap but neither contains the other — fusing them into one
> "all four do everything" layer is the wrong shape. (b) is rejected: it changes
> behavior (forbidden by the issue body), a new artifact is "one `artifact_names` edit
> away" from silently entering `_compute_outcome`'s scoring, and it misrepresents
> `UNCERTAIN:` (a human-escalation exit 0) as a verdict enum value with no real
> semantics. AC1's own "all four existing gates" phrasing is corrected in the spec
> (Requirement 7) rather than stretched to make refine/plan gates. Maker≠checker holds
> for refine/plan by construction (fresh-context subagent, no self-grading) — assert it,
> don't restructure them to prove it.

> **Q:** #301's loop schema has no field naming a distinct "maker agent" separate from
> the loop entry itself — only `verification.verifier` as an opaque path, plus
> `handoff.manifest`/`handoff.outputs`/`persistence.artifacts` as other opaque paths on
> the same entry. What should the structurally-enforceable "verifier ≠ maker" check in
> #197 concretely compare?
>
> **A:** A path-disjointness rule: `verifier` must not equal `handoff.manifest`, and
> must not be a member of `handoff.outputs` or `persistence.artifacts` — the full set of
> paths the loop itself *is* or *produces*, since the loop entry is the only
> maker-identity the schema exposes. `verifier == handoff.manifest` alone (the narrowest
> reading) misses the actual bypass case — a loop declaring its own emitted output file
> as its verifier. No cross-loop uniqueness constraint: independence is per-loop
> disjointness, not scarcity of shared checker scripts. The check's code lives in
> #197's own `verifier.py` (not `#301`'s `_validate_loop`, whose field-shape ownership
> stays with #301 per its own Consumers table), called from `adapter.load()` as an
> additive hook. The declaration-time string check is explicitly the *cheap* half —
> the load-bearing half is that the verifier always runs as a separate check-only
> process whose verdict the factory (not the loop) parses and acts on, per #189's
> clean-room-grader principle (independence from execution isolation, not naming).

---

## Architecture / Approach

### New files

- **`scripts/factory_core/verdict.py`** — the canonical schema. `parse_verdict(content:
  str) -> dict | None` (generic STATUS/GATE_TYPE/FINDINGS_COUNT/SEVERITY line parser,
  falling back to today's loose heuristics — `"PASS" in content`, `"⛔" in content`,
  etc. — only when structured lines are absent, exactly mirroring each existing
  `elif name == ...` branch's own fallback so behavior is preserved); `format_verdict(
  gate_type, status, findings_count, severity) -> str` (Python-side sibling of
  `gate_lib.sh::emit_verdict`, for verifiers not written in bash); the enum
  constants from Requirement 1 as the module's documented single source of truth.
- **`scripts/factory_core/verifier.py`** — `resolve_verifier(clone_dir, verifier_path)`,
  `run_verifier(resolved_path, env) -> (exit_code, stdout)`, `normalize_verdict(exit_code,
  stdout, gate_type) -> str` (structured vs. bare-exit-code dispatch, Requirement 4),
  `assert_verifier_independent(loop_entry)` (Requirement 5), and a small CLI (`python3
  -m factory_core.verifier --clone-dir . --loop-name X --verifier-path Y run
  --out artifacts/verifier.md`) so a future dispatcher (or a human, or a test) has one
  documented entry point.
- **`refinement-skills/VERIFIER-CONTRACT.md`** — Requirement 3's shared doc: the
  checker-invocation contract (Opus-4.8 pin, read-only tool grant, clone-live-first
  resolution), the verdict schema (re-stated from `verdict.py`'s docstring for a
  non-Python-reading audience — command authors), and the target-verifier registration
  contract (env vars, structured vs. bare-exit-code modes, fail-closed defaults,
  the maker≠checker rule). Linked from `refinement-skills/SKILL.md` and each of the
  four `commands/*.md` files.

### Modified files

- **`scripts/factory_core/run_record.py`** — `_parse_artifact_stage` delegates to
  `verdict.parse_verdict`, keeping each `name`'s extra detail-field extraction (`cycles`
  for conformance, `blockers`/`advisory` for review) as a thin overlay on top of the
  generic parse. `artifact_names`/`GATE_STAGE_NAMES`/`_compute_outcome` unchanged
  (Requirement 2).
- **`scripts/factory_core/adapter.py`** — one additive call,
  `verifier.assert_verifier_independent(entry)`, added to the per-loop-entry loop in
  `load()` right after the existing `_validate_loop(entry, i)` call. Gated on #301
  having landed (Requirement 5); implement phase confirms the exact call-site line
  numbers against `main` before writing this edit.
- **`scripts/gate_lib.sh`** — top-of-file comment updated to point at `verdict.py` as
  the canonical schema reference. `emit_verdict` itself is unchanged (it already matches
  the documented schema).
- **`commands/dark-factory-refine.md`, `dark-factory-plan.md`,
  `dark-factory-conformance.md`, `dark-factory-code-review.md`** — each command's
  "always pin this subagent to Opus 4.8 ... needs Glob, Grep, and Read tools" sentence
  is replaced with a one-line reference to `refinement-skills/VERIFIER-CONTRACT.md`'s
  checker-invocation section. No change to which tools are granted or which model is
  pinned — pure de-duplication.

### Tests

- `tests/test_verdict.py` (new) — schema round-trip for every `GATE_TYPE` the four
  existing gates use plus one invented target-loop `GATE_TYPE`, structured and
  loose-fallback parsing, unknown/malformed `STATUS:` handling.
- `tests/test_verifier.py` (new) — `resolve_verifier`/`run_verifier` against real
  executable fixture scripts under `tests/fixtures/verifiers/` (structured-PASS,
  structured-BLOCKED, bare-exit-0, bare-exit-1, missing path, non-executable path,
  timeout); `assert_verifier_independent` positive/negative cases for all three
  disjointness members (`handoff.manifest`, one of `handoff.outputs`, one of
  `persistence.artifacts`); env-contract assertions (`CLONE_DIR`/`ARTIFACTS_DIR`/
  `ISSUE_NUM` or `LOOP_NAME`/`FACTORY_REPO_SLUG` all present in the child process).
- `tests/test_run_record.py` — existing parse/outcome tests unchanged and green
  (byte-compatibility); one additive test proving `_parse_artifact_stage` round-trips a
  `GATE_TYPE` it has never seen through the generic path.
- `tests/test_adapter.py` — additive `assert_verifier_independent` cases written against
  #301's approved shape, added once #301 merges (coordinated, not concurrent, per the
  `Depends on:` edge).
- New integration test (`tests/test_verifier_gate_integration.sh` or appended to
  `tests/test_verdict_gate_check.sh`) — pipes a `verifier.py`-written artifact through
  the real `verdict_gate_check.sh` subprocess: PASS → exit 0, BLOCKED → exit 1, missing
  artifact → exit 1 (Requirement 8c, the concrete AC3 proof).

### Consumers (informational — none of these are this ticket's deliverable)

| Ticket | What it will consume from #197 |
|---|---|
| #196 (A2) | The `required_profile` field on a recorded verifier verdict (Requirement 6), once it ships real permission-profile enforcement |
| #198 (A4) | The verdict schema and `verdict_gate_check.sh` pattern for its own external stop-condition check; #311's "contract" verdict (a *different*, future artifact) also builds on this schema |
| #199 (A5) | A durable, addressable verifier verdict + its run-record entry as one of the inputs to the handoff manifest |
| #189 (security lane, spiked) | Its clean-room grader is expected to "consume #197's verifier" per its own archived spike spec |
| Future loop-dispatch ticket (unowned, epic #194 gap) | `verifier.py`'s CLI as the check-only primitive it would call per dispatched loop |

---

## Acceptance criteria → disposition

| # | Source | Criterion | Disposition |
|---|---|---|---|
| 1 | Issue | All four existing gates run on the shared abstraction; tests green | Satisfied as restated (Requirement 7): invocation-contract sharing for all four, verdict-schema sharing for the verdict-emitting producers, byte-compatible artifacts |
| 2 | Issue | A target loop can declare a verifier and its verdict is parsed, recorded, gates handoff | Satisfied via `verifier.py` + `verdict.py` + the integration test (Requirement 8); no live dispatcher, per Q&A |
| 3 | Issue | Missing/failing verifier cannot hand off (fail closed) | Satisfied: `verifier.py` fails closed on missing/non-executable/timeout/undetermined-profile (Requirements 4, 6); proven against the real `verdict_gate_check.sh` (Requirement 8c) |
| i | #311 inherited | Verifier consumes only observable events/artifacts, never agent self-report | Satisfied by construction: `verifier.py` reads only the resolved script's stdout/exit code and the adapter-declared paths — never a maker's session/reasoning |
| ii | #311 inherited | Missing/failed required deliverable fails closed before handoff | Satisfied: same fail-closed default as AC3; `ERROR` is not auto-pass-through for target verifiers (Requirement 4) |

---

## Alternatives Considered

1. **Wire a live DAG node that dispatches a real declared loop's verifier as part of the
   ticket-lifecycle pipeline**, to make AC2/AC3 demonstrably "real" rather than
   test-proven. Rejected: this adds new behavior to `workflows/archon-dark-factory.yaml`
   that runs on every future ticket, contradicting the issue's own "without changing
   their behavior," and edges into building the loop-dispatch capability the Q&A above
   explicitly scoped out as a separate, unowned ticket.
2. **Make refine/plan emit a formal PASS/BLOCK verdict artifact** for schema uniformity.
   Rejected per Q&A: changes behavior, risks silently entering `_compute_outcome`'s
   scoring the moment someone extends `artifact_names`, and misrepresents
   `UNCERTAIN:`'s real semantics (human escalation, not a gate verdict).
3. **Add a `maker:` field to #301's loop schema** so maker≠checker could be a direct
   equality check instead of a path-disjointness rule. Rejected per Q&A: #301's spec is
   already approved and explicitly assigns "maker≠checker rule enforced [in #197], not
   in the schema" — reopening the schema is out of this ticket's authority and would
   require a new #301 revision cycle.
4. **Enforce the A2 level-1 permission profile now** (e.g. a bespoke, #197-local
   tool/container restriction for verifier processes) rather than deferring to #196.
   Rejected: #196 is epic #194's chartered owner of `side_effect_level` →
   enforced-permission-profile mapping; building a parallel, ticket-local enforcement
   mechanism duplicates that work and risks two divergent permission models before A2
   ships its own.
5. **Build the "Nodding Loop" detector** (a Hermes Agent comment proposal: flag loops
   that never reject/escalate across non-trivial workload). Deferred, not designed
   here: it is a cross-run analytics feature over historical verdicts, not part of the
   issue's own three acceptance criteria, and would need its own scope (which run
   history it mines, what "non-trivial workload" means quantitatively) better suited to
   a follow-up ticket once target verifiers actually accumulate a verdict history to
   analyze.

---

## Open Questions (non-blocking)

- Whether a future loop-dispatch ticket calls `verifier.py`'s CLI directly or wraps it
  further — left to that ticket, which does not exist yet (Q&A flags it as a spillover
  candidate under epic #194, not filed by this refine run).
- Whether the #311-described `contract.md` gate (a ticket-lifecycle-scoped completion-
  manifest verifier, distinct from target-loop verifiers) is built as a second consumer
  of `verdict.py`/`verifier.py` in this same ticket or a later #197/#198 follow-up — the
  #311 spike frames it as "#197/#198 to plan," not mandated inside this spec's three
  ACs; left for the plan phase to size once #240's replay substrate and #198's own
  scope are further along.
- Exact artifact path/naming convention for a *loop-scoped* (not ticket-scoped) verifier
  run — `run_record.py` today is entirely keyed by `ISSUE_NUM`/`RUN_ID`; a future
  loop-dispatcher will need its own run-record-equivalent for loop executions, which is
  out of this ticket's file list.

---

## Assumptions (flagged)

- **[ASSUMPTION]** #301 merges with the field shape its approved spec documents
  (`discovery`/`handoff`/`verification`/`persistence`/`scheduling`, no further
  amendments to the five block names or the `verifier`/`manifest`/`outputs`/`artifacts`
  field names). If #301's implementation deviates from its own spec before merging,
  the plan phase for #197 re-verifies against `main` before writing
  `assert_verifier_independent`'s field-access code, per the repo's own
  re-verify-before-building-on-a-dependency convention.
- **[ASSUMPTION]** "Check-only" for a target verifier remains a documented convention,
  not a sandboxed guarantee, at this stage — exactly like `smoke-gate`'s own docstring-
  only enforcement today. `verifier.py` does not attempt filesystem/process isolation;
  Requirement 6 records this gap explicitly rather than papering over it with an
  unenforced claim of containment.
- **[ASSUMPTION]** The `refinement-skills/VERIFIER-CONTRACT.md` doc is the right home
  for the shared checker-invocation text (rather than duplicating it into each of
  `.claude/skills/conformance/SKILL.md`, `.claude/skills/code-review/SKILL.md`, and
  `refinement-skills/SKILL.md`), since `refinement-skills/` already hosts the
  cross-cutting persona prompts (`product-owner-prompt.md`, `architect-prompt.md`) that
  predate the per-gate `.claude/skills/` split.
