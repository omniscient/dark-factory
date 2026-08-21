# Add an always-on state governance scorecard for Dark Factory persistent state

**Issue:** omniscient/dark-factory#190
**Status:** refined 2026-08-21. Six "Hermes Agent / Product Manager" comments each proposed folding
in an additional research paper's worth of state classes and checks; two (harness economics #234,
memory intervention #241) are explicitly assigned to separate owning epics. This spec scopes the
ticket to what its size:M label and the CLAUDE.md "gate changes get their own reviewed ticket" hard
limit actually allow, per the brainstorming Q&A below, and defers the rest as named follow-ups.

---

## Paper summary (AC1): arXiv 2606.30306, "Always-On Agents"

The paper argues that persistent-state agent systems (like Dark Factory) should be evaluated as
**governed-state systems**, not just memory-retrieval systems. It defines six diagnostic axes for
every durable state item — **Authority** (who/what may let this state influence an action),
**Scope** (which repo/issue/PR/role/time-window it may act within), **Mutability** (can it be
updated/superseded/expired/quarantined), **Provenance** (source/timestamp/run/transformation that
produced it), **Recoverability** (can downstream effects be found and undone), and **Actionability**
(is it passive evidence, advisory memory, policy, permission, executable skill, or an external
commitment) — plus a lifecycle (`observe → write → validate → organize → retrieve → act`,
`update → forget → audit → rollback`). Its central warning: systems over-invest in write/retrieve and
under-invest in governance operations (validation, audit, deletion propagation, rollback), so a
system can retrieve facts correctly while still failing governance obligations. Its AOEP-v0 idea
scores a state *trajectory* through events, not a final answer — checking authority monotonicity,
scope non-expansion, deletion propagation, provenance preservation, and rollback traceability.

**Dark Factory implication:** the factory already has several independent, unevenly-governed durable
state mechanisms (memory files, run records, GitHub board/label state, config flags, skill/hook
permissions). No mechanism today scores any of them against these six axes, and — as this refinement
pass found — at least one (`.archon/memory/index.jsonl`) silently fails several of them right now.

---

## Requirements

Distilled from the issue's acceptance criteria and the brainstorming Q&A:

1. Inventory ≥8 Dark Factory state classes, each scored against the six axes (Requirements §Inventory
   below covers 10 fully-scored classes plus 2 reserved-but-unscored classes owned by other epics).
2. Define a new, independent AOEP-style event/snapshot schema for `state-lineage.jsonl` — existing
   `runs.jsonl` and `.archon/memory/index.jsonl` do not carry authority/mutability/recoverability/
   actionability fields, so neither can be reused as-is, but three fields (`provenance.run_id`,
   `scope.issue`/`scope.repo`, `provenance.commit`) are **mandatory join keys** sourced from artifacts
   the factory already produces, so a later correlation with `runs.jsonl` stays possible without any
   `entrypoint.sh`/workflow wiring in this ticket.
3. Implement a **hard-capped** subset: `scripts/state_governance_audit.py` (repo-root, tracked —
   *not* the issue's literal `dark-factory/scripts/...`, which is an untracked runtime-staged mirror
   path; see Architecture §Script location) computing exactly 5 deterministic checks (authority
   monotonicity, scope non-expansion, deletion propagation, provenance preservation, rollback
   traceability) over a **synthetic, committed fixture corpus** — no live-run event capture, no
   `entrypoint.sh`/`workflows/archon-dark-factory.yaml` wiring, no non-zero/blocking exit code.
4. Produce a deterministic, committed sample `state-governance-scorecard.json`/`.md` generated from
   the fixture corpus (AC5's "synthetic replay"), with a test asserting regeneration is byte-stable.
5. A retrospective section evaluating 3 real historical factory failures (#212, #292, #305) against
   the six axes (AC6) — spec prose, not a replay harness.
6. A concrete recommendation (AC7): ship advisory-only, name the numeric bar for promotion to
   conformance-input, and rule out scheduler-gate with an architectural reason (§Recommendation).
7. No changes to `config/config.yaml`, `entrypoint.sh`, `workflows/archon-dark-factory.yaml`, or any
   gate/permission surface. Gate integration is Phase 5 of the issue's own rollout and is explicitly
   **out of scope**, per CLAUDE.md's "gate changes get their own reviewed ticket" and the #300 memory
   precedent — this holds regardless of the Hermes-Agent comments' trusted signature, since none of
   them are asking to expand a security-sensitive surface directly, but the *aggregate* ask (5 phases
   of a brand-new gate-adjacent subsystem in one size:M ticket) would.
8. Epic-owned state classes (`harness_economics` → #234, `memory_intervention` → #241) get an
   inventory row and a reserved `state_class` enum identifier each, with no sub-schema or checks
   designed here — that work belongs to their owning epics.
9. Non-epic comment-sourced additions (agent/role-definition state, the five "loop move" classes, the
   three skill-validity dimensions) are folded into existing inventory rows rather than becoming new
   rows, per the repo's own prior scoping precedent (`.archon/memory/architecture.md`, issue #41: "scope
   the lens to the surfaces where it carries semantic meaning... rather than running it across every
   ...row too — the latter produces mostly-null cells and quadruples the document for no signal").

---

## Architecture / Approach

### Inventory: 10 state classes × 6 axes, plus 2 reserved

| State class | Authority | Scope | Mutability | Provenance | Recoverability | Actionability |
|---|---|---|---|---|---|---|
| **memory** (`.archon/memory/*.md` + `index.jsonl`) | Any phase agent via `memory_write.py`; no per-writer allowlist | `path-prefix` + `scope` tag in the markdown comment, but not enforced at write time | Markdown: 6-month expiry, REINFORCE, dedup (`memory_maintain.py`). `index.jsonl`: **append-only, never mutated** — expiry/supersession never propagates to it | Markdown: issue/source/agent/date in an HTML comment. `index.jsonl`: writer omits `id`/`source_file`/`path_prefixes`, so its own reader (`memory_retrieve.scan_index`) `continue`s past every row it writes — **provenance is recorded but not retrievable** | None — no rollback ledger; `INVALID:` tagging is a manual edit, not a tracked operation | Advisory (feeds future agent context; does not itself gate) |
| **issue / project-board / scheduler-queue state** | `scheduler.sh` + human maintainers, via the shared `omniscient` GitHub identity | Per-issue | Labels/board column mutate freely, no versioning | GitHub's own audit log only — Dark Factory artifacts don't separately record who/why moved a card | GitHub history only; no Dark Factory–side undo | **Policy/permission** — `ready-for-agent`/`needs-discussion`/`direct-to-pr` labels directly gate dispatch |
| **branch / PR state** | Factory git identity (`factory@<repo>`) + human reviewers | Per-issue branch (`refine/issue-N`, `feat/issue-N`) | Force-push disallowed by policy; branches can be recreated | Git commit authorship + `Co-Authored-By` trailer (used by `fetch_scorecard.py::is_factory_commit`) | Strong — full git history, revert/reset available | External commitment |
| **run artifacts / failure telemetry** (`runs.jsonl` via `run_record.py`) | `entrypoint.sh` / the running container itself | Per `run_id` × issue × stage | Append-only; no update/delete | Best-governed class in the repo today: `run_id`, `policy_version`, `gen_ai.*` fields all present | Durable (`scheduler_state` volume); on-disk retention/pruning policy not found in this pass — flagged as an open question | Evidence, but validation/conformance/review verdicts are also policy-actionable (`verdict_gate_check.sh` reads them to block a DAG node) |
| **skills / hooks / MCP / tool-permission state** | Humans only, via reviewed PR | Repo-wide (not per-issue) | Git-versioned, no expiry | Git blame | Git revert | **Highest** — directly controls what tools/actions any agent may take. Listed in `epic_autopilot`'s `hard_exclude_paths` (fail-closed even under self-improvement); CLAUDE.md bars comment-channel authorization of this surface outright |
| **agent / role definitions** (`commands/*.md`, `refinement-skills/*.md`) | Humans via reviewed PR (refine-phase agents may *propose* command prose, but activation is always a human merge) | Per-phase (1:1 with a workflow DAG node) | Git-versioned | Git blame + PR review | Git revert | Skill/permission-tier — a specialization of the row above. No "role registry" or "eval baseline" file exists in this repo today (unlike the Agency Agents framework referenced in the comments) — an inventory gap, not a defect |
| **token / rollout policy flags** (`config/config.yaml` `enforce.*`, `*_autopilot.enabled`, kill-switches) | Human PR merge to `config.yaml`; **some flags have a second, less-visible authority channel** — env-var overrides (e.g. `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS`), and `main_red_autofix.enabled` requires *both* `config.yaml` and an uncommitted `.archon/.env` (the config file says so in its own comment) | Repo-wide | Toggled via reviewed config edits | Git history for `config.yaml`; **`.archon/.env` changes are never committed** — a provenance gap for any flag gated behind it | Git revert for `config.yaml` only | Policy — directly flips gate enforcement |
| **security-lane / code-review findings** | `code-review` phase agent (`code_review_payload.py`) | Per PR/diff | Regenerated each review, not versioned | Diff context assembled per run, but no cross-run finding identity | PR comment history only | Mixed: advisory below `block_threshold`, blocking (`high`+) above it, `fail_open: true` on reviewer error |
| **external commitments** (comments/labels/PR merges/status moves) | `entrypoint.sh`'s marker-based upsert (`tracker comment --marker ...`) — already idempotent, a genuinely well-governed pattern | Per issue/PR | Update-in-place via marker, not append-only duplication | **All comments post from the shared `omniscient` account** — no author-identity distinction between scheduler-authored and Hermes-Agent-authored content; CLAUDE.md itself flags this as a known gap | Comments are not auto-reverted | External commitment. CLAUDE.md's "Hermes Agent" signature convention is itself an authority rule layered on top of this class (elevated trust for refinement *product input* only, explicitly not for gate/permission surfaces) |
| **mechanism_lineage** (proposed by the Bilevel Autoresearch comment; no owning epic) | `activation_status` transitions (`proposed→sandboxed→shadow→active`) are declared in the issue's schema sketch but nothing in the repo enforces them yet | Per-mechanism, cross-cutting | `activation_status` mutable; `supersedes`/`rollback_to` fields are declared but unread by any code today | `source_trace` field declared, unpopulated | `rollback_to` field is aspirational — no runtime rollback path exists | Meta — describes governance state *about* other mechanisms; currently greenfield (schema only, see below) |
| `harness_economics` — **reserved, owned by #234** | — | — | — | — | — | — |
| `memory_intervention` — **reserved, owned by #241** | — | — | — | — | — | — |

The five "loop move" classes (discovery/handoff/verification/persistence/scheduling) from the Loop
Engineering comment map onto existing rows rather than becoming new ones: discovery → issue/board
state, handoff → run artifacts + branch state, verification → run artifacts (verdicts), persistence →
memory + external commitments, scheduling → scheduler-queue state. The three skill-validity
dimensions (structural/routing/behavioral) from the agent-skills comment become additional per-check
detail on the "skills/hooks/MCP" and "agent/role definitions" rows, not new rows.

### Event/snapshot schema

`state-lineage.jsonl` uses the issue's proposed nested envelope verbatim (`event_id`,
`idempotency_key`, `operation`, `state_type`, `authority{actor,permission_epoch,approval_record}`,
`scope{repo,issue,pr,agent_role}`, `provenance{source,trust_tier,run_id,commit}`,
`mutability{status,supersedes,conflicts_with}`, `recoverability{transaction_id,rollback_handle,
external_effects}`, `actionability`), with `state_type` restricted to the reserved enum:

```
memory | issue | project_status | branch | pr | skill | permission | artifact |
external_commitment | mechanism_lineage | harness_economics | memory_intervention
```

Three fields are **mandatory**, not free-form, because they are the join keys a later correlation
with `runs.jsonl` needs:

- `provenance.run_id` — the entrypoint's `RUN_ID` (`entrypoint.sh:95`, a uuid4 hex), also derivable
  as `basename "$ARTIFACTS_DIR"` (the workflow already does exactly this for bash nodes,
  `workflows/archon-dark-factory.yaml:354-356`). This join is free — it requires no new wiring.
- `scope.issue` / `scope.repo` — `issue.json`'s `.resolved_number` and `$FACTORY_REPO_SLUG`.
- `provenance.commit` — the clone's `HEAD`.

A normative **sources adapter table** documents how each existing mechanism *would* map onto this
schema (a memory write *is* a `write` operation on `state_type: memory`; a `runs.jsonl` stage record
*is* an `act` operation with `actionability: evidence`; a label change *is* a `state_type:
project_status` event) — but this ticket does **not** build any adapter. The implementation reads only
the synthetic fixture corpus below. Wiring a real adapter (touching `memory_write.py`, `run_record.py`,
or `entrypoint.sh`) is a named follow-up (§Follow-ups).

### Deterministic scorecard: 5 checks

`scripts/state_governance_audit.py` (repo-root, tracked — matches CLAUDE.md's repo map, `scripts/` +
`scripts/factory_core/`; the issue's literal `dark-factory/scripts/state_governance_audit.py` path is
where the factory's own tooling gets runtime-staged into a *target* repo's clone, not where a
Dark-Factory-authored script should live when Dark Factory is the target) reads a directory of
`state-lineage.jsonl` fixture files and emits `state-governance-scorecard.json`/`.md`. Invoked
directly (`python3 scripts/state_governance_audit.py --fixtures evals/state-governance/fixtures/
--out-dir $ARTIFACTS_DIR`), matching the existing standalone-script convention (`fetch_scorecard.py`,
`gate_blast_radius.py`, `memory_write.py` — none of these route through `factory_core/cli.py`).

Five deterministic checks, each a pure function over an ordered event sequence grouped by a stable
entity identifier:

1. **Authority monotonicity** — an event's `authority.permission_epoch` must not exceed the epoch of
   the event its `approval_record` claims to derive from. Flags forged/inflated authority claims.
2. **Scope non-expansion** — across a sequence of events sharing an entity, `scope` must not widen
   (e.g. issue-scoped → repo-wide) without an explicit new authorizing event of equal or higher
   `permission_epoch`. Historical anchor: #292 (§Retrospective).
3. **Deletion propagation** — every `tombstone`/`delete`/`quarantine` event must be reflected in any
   later `retrieve`/`act` event that reads the same entity; a read that ignores an active tombstone
   fails. Historical anchor: `.archon/memory/index.jsonl` never receiving `memory_maintain.py`'s
   expiry/supersession (found live in this repo during context assembly, see below).
4. **Provenance preservation** — every event with `actionability != evidence` must carry a non-null
   `provenance.source` and (`run_id` or `commit`); if `actionability` is `policy`/`permission`/`skill`/
   `external_commitment`, `provenance.trust_tier` must be `trusted` or `reviewed`. Historical anchor:
   #212 (§Retrospective).
5. **Rollback traceability** — every event with `actionability` in `{policy, permission, skill,
   external_commitment}` must carry a non-null `recoverability.rollback_handle` or `transaction_id`.
   Historical anchor: #305 (§Retrospective).

Exit code is always 0 — the caller reads the `STATUS`/score fields from the output, matching the
existing `gate_blast_radius.py` convention ("Exit 0 always — the caller reads STATUS from the
output"). This ticket wires no caller.

### Fixture corpus and sample artifacts (AC5)

Mirrors the existing `evals/behavioral-state/` + `tests/test_behavioral_state_fixtures.py` pattern
(built for epic #241's groundwork) rather than the provider-contract style `tests/fixtures/jira/`:

- `evals/state-governance/fixtures/<check>-{pass,fail}.jsonl` — one file per check per outcome (10
  files), each a small synthetic `state-lineage.jsonl` event sequence with a committed `expected`
  verdict sidecar or manifest entry.
- `evals/state-governance/fixtures/realistic-run-01.jsonl` — one combined sequence exercising all 5
  checks together (11th file). Hard cap: no additional fixtures without a spec update.
- `evals/state-governance/sample/state-governance-scorecard.{json,md}` — the committed, deterministic
  output of running the audit script against the combined fixture. A test
  (`tests/test_state_governance_audit.py`) asserts regeneration matches byte-for-byte, which requires
  `--now`/`--run-id` flags on the script (no wall-clock read, no fresh uuid at render time) —
  precedent: `eval_memory_quality.py --timestamp`.
- A corpus-schema test (modeled on `tests/test_behavioral_state_fixtures.py`) asserts: required event
  fields present, at least one pass and one fail fixture per check, and the 11-file cap.

Three of the ten pass/fail fixtures should be grounded in real defects found in this codebase during
context assembly (fabricated event data, real underlying gap — not fabricated findings):

- **provenance-preservation-fail**: modeled on `.archon/memory/index.jsonl`'s rows, which omit `id`/
  `source_file`/`path_prefixes`; `memory_retrieve.scan_index` (`scripts/memory_retrieve.py:335-353`)
  silently `continue`s past every row missing them, and `.archon/memory/records/` doesn't exist, so
  **zero index rows are ever retrievable by id today** — provenance is written but not preserved in a
  usable form.
- **deletion-propagation-fail**: modeled on `memory_maintain.py`'s expire/dedup/promote operations,
  which rewrite the markdown files but never touch `index.jsonl` — a tombstoned entry's `index.jsonl`
  row stays `"status": "active"` forever.
- **scope-non-expansion-fail** (supporting, not primary): modeled on `memory_write._write_index`
  hardcoding `"project": "markethawk"` (`scripts/memory_write.py:95`) regardless of the actual target
  repo — every Dark-Factory-instance memory write is mis-scoped at the ledger level.

These three defects are **not** fixed by this ticket (out of scope — this ticket specs and builds the
*detector*, not the fix); they're named explicitly as a follow-up (§Follow-ups) and cross-referenced
here so the scorecard's synthetic fixtures read as grounded rather than hypothetical.

### Retrospective: 3 historical failures (AC6)

| Issue | What happened | Axis / check | Remediation (already shipped) |
|---|---|---|---|
| **#212** | A DAG push-gate node labeled an issue `spec-pending-review`/`plan-pending-review` based on the ephemeral, never-committed `$ARTIFACTS_DIR/refinement-status.md` STATUS marker, which can exist even when the actual commit failed — an authority-bearing decision keyed to non-durable state (`.archon/memory/architecture.md`, issue #212 entry) | **Provenance preservation** + authority monotonicity — the gate's authority derived from state with no durable provenance | `push_gate_check.sh` — now checks the committed artifact + `git rev-list` directly, not the ephemeral marker |
| **#292** | `stage_orphan_sweep` (`scheduler.sh:1323`) ran *before* the session-window pause sentinel gate (`scheduler.sh:1325`), so a paused run's board status still mutated to `blocked` and posted a comment directly contradicting the just-posted "paused" comment — a scope/ordering escape (`.archon/memory/codebase-patterns.md`, issue #292 entry) | **Scope non-expansion** — a mutation escaped the boundary meant to contain it | Documented fix (gate `stage_orphan_sweep` behind the pause sentinel) — verify current status against `main` before citing as fully remediated in any downstream use of this table |
| **#305** | `session_window.py`'s fallback reset-time parser rolled a past wall-clock time forward a day instead of returning the literal past timestamp, manufacturing a ~22h false pause (`.archon/memory/architecture.md`, issue #305 entry) | **Rollback traceability / mutability** — a lossy, non-invertible transform destroyed the original parsed value, so nothing downstream could trace the pause back to its actual input | Parser corrected to return the literal (possibly past) value; downstream layers (buffer, scheduler's `now >= resume_epoch` gate) turn a past value into an effectively-immediate resume |

Note: `evals/behavioral-state/baseline.md` (epic #241's corpus) already scores #212 twice
(`policy-violated-before-side-effect-02`, `phase-handoff-loses-state-01`) on different axes than this
table — that's expected, not a duplication; the two efforts score the same real incident along
orthogonal lenses. `evals/factory-failures.jsonl` (172 rows) was considered and rejected as an AC6
source: its rows carry no governance-axis fields and its issue numbers belong to the MarketHawk
instance, not this repo.

### Recommendation (AC7): advisory only

Ship **advisory**. Endorse **conformance-input** as the sole future promotion target. **Explicitly
rule out scheduler-gate**, for an architectural reason, not just caution: `scheduler.sh` is the
pre-dispatch poll loop, but `state-lineage.jsonl` events are produced *during* a run by
`entrypoint.sh`. A scheduler gate could therefore only ever read the *previous* run's scorecard —
stale derived state gating a live dispatch decision, which is exactly the mutability/staleness failure
mode #305 embodies. A scheduler-gated scorecard would violate its own axis.

Promotion criteria (advisory → conformance-input), reusing the shape of the existing, already-applied
Observe → Enforce procedure (`docs/dark-factory-token-optimization.md`, "Observe → Enforce Procedure"
section) with scorecard-specific metrics substituted:

- `false_positive_rate == 0%` across ≥10 bench issues (mirrors `section_at_risk_rate == 0%`).
- `check_failure_rate ≤ 10%` at the candidate ruleset (mirrors `over_budget_rate ≤ 10%`).
- Per-check granularity — a future `state_governance.mode.<check>: advisory|conformance_input` knob
  (mirroring `enforce.<scenario>`), so one noisy check can revert without disabling the whole
  scorecard.
- Monitor the next 5 runs after any flip; Tier-1 (master kill) / Tier-2 (per-check revert) rollback.

`epic_autopilot.confidence_floor: 0.7` was considered and rejected as a model for this bar — it's a
per-decision threshold on a model's self-reported confidence to auto-*advance* a ticket, not a
promotion bar from observe to enforce; wrong shape for this use.

**This bar is not reachable by this ticket's deliverable.** The synthetic-fixture-only implementation
(§Requirements 3) has no mechanism to accumulate 10 bench issues of real data — that's the honest
state of things, not an open question. It's recorded here as a named, scoped dependency on the
live-capture follow-up (§Follow-ups), not deferred to "Open questions."

No changes to `config/config.yaml` ship in this ticket — AC7 is delivered as normative spec text
naming the exact future knob shape, for the (separately reviewed) Phase-5 ticket to implement.

---

## Alternatives considered

1. **Reuse `runs.jsonl` or `.archon/memory/index.jsonl` directly as the event log**, adding the
   missing governance fields to one of them. Rejected: neither carries authority/mutability/
   recoverability/actionability today, and retrofitting six axes onto a schema built for a different
   purpose (run verdicts; memory-entry summaries) would deform a working mechanism for a
   non-blocking, advisory tool. A new envelope with mandatory join keys gets correlation for free
   without touching either existing file.
2. **Build the live-capture adapters now** (wire `memory_write.py`/`run_record.py`/`entrypoint.sh` to
   emit real `state-lineage.jsonl` events). Rejected for this ticket: pushes well past size:M, and
   touching `entrypoint.sh`/the workflow DAG is exactly the kind of change CLAUDE.md's "gate changes
   get their own reviewed ticket" and the repo's #300 precedent say should not ride along with a
   scorecard-definition ticket. Named as a follow-up instead.
3. **Full sub-schema design for `harness_economics` (#234) and `memory_intervention` (#241) now**,
   since both were proposed in this issue's own comment thread. Rejected: both already have (or are
   building) their own owning epics; duplicating design effort here risks drifting from what those
   epics actually ship. A reserved enum identifier + inventory row is the cheapest correct handoff.
4. **Fix the `index.jsonl` provenance/deletion-propagation defects found during context assembly as
   part of this ticket**, since the fix is small. Rejected: this ticket's job is to *build the
   detector*; conflating "found a bug while building an audit tool" with "fixed the bug" blurs scope
   and risks an untested memory-system change riding on a governance-tooling PR. Filed as a named
   follow-up instead (§Follow-ups).

---

## Open questions (non-blocking)

- What retention/pruning policy (if any) governs `runs.jsonl` growth over time? Not found in this
  pass — relevant to the "run artifacts" inventory row's Recoverability cell but not blocking for the
  capped v1 scorecard, which doesn't read `runs.jsonl` at all.
- Should `mechanism_lineage`'s `activation_status` lifecycle eventually be enforced by real code
  (e.g. a registry file), or does it stay a documentation-only convention? No current owner or epic
  claims it; flagged for whoever picks up the Bilevel Autoresearch thread next.
- Is #292's `stage_orphan_sweep` ordering fix (cited in `.archon/memory/codebase-patterns.md`) fully
  landed on current `main`, or still pending? This spec cites the *documented* fix; the retrospective
  table should be re-verified against `main` before being reused as evidence elsewhere.

---

## Assumptions (flagged)

- `scripts/state_governance_audit.py` is invoked as a standalone script (not a `factory_core/cli.py`
  subcommand), matching the existing convention for `fetch_scorecard.py`, `gate_blast_radius.py`, and
  `memory_write.py`. No product-owner ruling was sought on this specific point since the existing
  convention is unanimous across every comparable script in the repo.
- The Observe → Enforce procedure's numeric shape (`≤10%` rate, `≥10` bench issues, monitor-5-runs)
  is assumed to generalize from token-budget metrics to governance-check metrics; the metrics measure
  different things (budget overage vs. false-positive check failures) even though the procedural
  shape transfers cleanly.
- "Historical factory failures" (AC6) is read as *real incidents already recorded in this repo's own
  operational memory* (`.archon/memory/*.md`), not currently-live code defects discovered while
  writing this spec — the latter are used as fixture-grounding evidence instead, to avoid AC6
  double-counting the same findings two different ways.
