# Factory/target boundary contract doc (A7) — record the shipped contract

**Issue:** #201 · **Epic:** #194 (Factory/Target boundary v1)
**Depends on:** #195 (A1 `loops:` schema), #196 (A2 side-effect levels), #197 (A3
verifier contract), #198 (A4 stop-condition schema), #199 (A5 handoff manifest) — all
closed. #200 (A6 bypass prevention) is also closed and is in scope per the issue body's
"bypass rules (A6)" bullet even though not listed in a `Depends on:` line.
**Status:** spec-pending-review
**Operator spec gate:** 2026-09-09 — approved with amendments AM-1—AM-10 from an independent read-only review that re-verified every claim against `origin/main`. Two blocking omissions (committed forward references, and overlap with `docs/adapter-authoring-guide.md`) plus four accuracy corrections are folded in below.

---

## Overview / Problem Statement

Epic #194 shipped six mechanisms (A1–A6) across five prior tickets, each landing its own
design spec under `docs/superpowers/specs/` or `docs/archive/`. No single document ties
them together as "the boundary contract" — a reader has to reconstruct it from five specs
plus the `adapter.py`/`side_effect.py`/`verifier.py`/`handoff.py`/`gate_blast_radius.py`
source. Issue #193 (the epic's own refinement) deliberately deferred writing that
document until the mechanisms existed, to avoid documenting a design that might shift
during implementation. They're all shipped now; #201 (A7, this ticket) is the final
child of #194 and closes it by writing `docs/factory-target-boundary.md`.

The issue also relays a set of "non-negotiables" from a comment signed "Operator — Dark
Factory" — a signature that is not one of the two signatures CLAUDE.md names as trusted
PM input (`Hermes Agent` / `Hermes Agent / Product Manager`). One of the five claims in
that list — "live trading permanently excluded" — has no supporting code or config
anywhere in this repo (dark-factory); it reads as MarketHawk-domain framing. The
acceptance criterion "each claim references the implementing module/test" is binding
regardless of the comment's signature, so this spec resolves that discrepancy explicitly
(see Requirements and Architecture below) rather than transcribing the comment verbatim.

## Requirements (from Q&A)

- The doc's every factual claim must cite a concrete implementing module/test (file, and
  ideally file:line at time of writing) — no claim may rest solely on the issue or its
  comments.
- **Live-trading non-negotiable resolution** (Q&A): don't state "live trading
  permanently excluded" as a factory-wide, code-backed non-negotiable — nothing in this
  repo enforces it. Instead:
  - State the actually-enforced, citable non-negotiable: **side-effect level 6
    ("external production side effect") is rejected in v1 and requires human approval to
    ever enable** — `scripts/factory_core/side_effect.py`'s `LEVELS`/`_PROFILES` (no
    profile exists for 6) and `scripts/factory_core/adapter.py`'s `AdapterError` at load
    time citing "#196/D1", reinforced by `scripts/factory_core/handoff.py`.
  - Present live-trading exclusion as **one worked example of a level-6 external
    production side effect, excluded target-side** (MarketHawk's own
    `epic_autopilot.sensitive_keywords`/`hard_exclude_paths` in `config/config.yaml` and
    `scripts/factory_core/adapter_defaults.py`), explicitly labelled as target-layer
    keyword/path defense-in-depth, not a factory-wide hard block — and note dark-factory's
    own analogous exclusions (`deploy/instances/`, `publish.yml`, the skill/settings/MCP
    self-modification surfaces) as the parallel example for the self-target instance.
    State precisely what `hard_exclude_paths` does, because the obvious reading is wrong: its
    only consumer is `scripts/factory_core/epic_autopilot.py` (`_hard_exclude_paths` /
    `_load_exclude_paths` → `hard_excluded()`), which filters epic-autopilot candidate
    *tickets*. It never inspects a diff, never aborts a run, and `config/config.yaml` ships
    `epic_autopilot.enabled: false`. Do **not** repeat `README.md:167`'s phrasing ("Path
    prefixes the factory will never touch; matched diff paths abort the run") — that is not
    what the code does, and the doc must not inherit the error. What actually holds
    `deploy/instances/` and `publish.yml` on the self target is CLAUDE.md's Hard limits plus
    `.factory/adapter.yaml`'s `migration_seed_auth_patterns` entries `^deploy/` and
    `^\.github/workflows/` reaching `gate_blast_radius.py`.
- Doc lives at `docs/factory-target-boundary.md` (issue's explicit path) — a durable,
  living reference doc at the `docs/` root, not under `docs/superpowers/` (in-flight
  artifact) or `docs/archive/` (completed-workflow artifact); same tier as `README.md`,
  `CLAUDE.md`, `ARCHITECTURE.md`.
- README's existing `## Adapter contract` section (`README.md:155`) gets a pointer to the
  new doc; the `loops` row of the `adapter.yaml keys` table
  (the `loops` row, cited by its key rather than by line number — it is at `README.md:175` on
  `origin/main`, already one line off this spec's own citation; it currently points at
  `docs/archive/2026-08-28-adapter-schema-v2-loop-metadata-a1-5-design.md`)
  is the natural anchor since it already flags loop-schema-adjacent contract detail.
- `docs/domain.md` conventions: glossary-term discipline against `CONTEXT.md` is N/A —
  this repo has no `CONTEXT.md`, and `docs/domain.md` itself says to proceed silently
  when that file is absent. ADR-conflict flagging: checked `docs/adr/0008` (autonomous
  development trust model) and `docs/adr/0011` (GELF logging) — neither conflicts with
  the boundary contract; no flag needed.
- **`docs/adapter-authoring-guide.md` overlap and authority order (mandatory).** That doc
  already ships `## Side-effect levels` (the full level→profile table, the `effective_level`
  fail-closed rule, `FACTORY_OWNED_MIN_LEVEL`) and `## Handoff manifest (A5)` (schema, intake
  path, reason codes, trust boundary). The new doc must NOT restate either: its A2 and A5
  sections give the one-paragraph contract statement plus a link, and the doc declares the
  authority order explicitly, mirroring the guide's own header convention ("if they disagree,
  the design doc is authoritative"). Duplicating those tables would create the second,
  drifting source of truth this ticket exists to prevent.
- **A trust-model section is a committed obligation, not an option (mandatory).**
  `docs/adapter-authoring-guide.md:310` already reads "see `docs/factory-target-boundary.md`
  (#201) for the full trust-model writeup", and A2's design spec
  (`docs/archive/2026-09-04-side-effect-levels-permission-profiles-a2-design.md:72`) says "Say
  this plainly in `docs/factory-target-boundary.md` (#201)". Two live code comments wait on it
  as well (`scripts/shims/git:65`, `scripts/shims/gh:55`, both "F13: the adapter guide's
  section until #201 creates docs/factory-target-boundary.md"). The doc must therefore carry a
  trust-model section stating: the shim is a `PATH` shim, so a process invoking `/usr/bin/git`
  by absolute path bypasses it; v1 is a policy boundary against mistaken or prompt-injected
  behaviour, **not** a security boundary against a deliberately hostile agent; the boundary
  against a hostile agent is the credential, deferred as #196/D3. Without this section the
  merge leaves four in-repo references pointing at a writeup that does not exist — the exact
  failure this ticket exists to prevent. Retargeting the shims' deny message is out of scope
  for #201; file it as a follow-up.
- Known gaps must be named honestly, each citing its open issue number and current
  status (all three open as of this spec): #374 (a `HUMAN_REQUIRED` block from
  `gate_blast_radius.py` has no approval memory — re-running validate after a
  `needs-discussion` clear re-blocks identically), #407 (nothing deterministic consumes
  `blast.md`; `scripts/verdict_gate_check.sh` guards only `conformance.md`/`review.md`),
  #412 (fail-closed findings from `gate_blast_radius.py::_adapter_snapshot` carry no
  diagnostic beyond "adapter.yaml unparseable at `<ref>`", and the base ref is validated
  against HEAD's current schema, not the schema that was live at the base commit). Plus the
  following, verified against the shipped A6 code and not optional — a gaps list that omits
  them overstates the boundary:
  - `workflows/**` and `commands/**` are visibility-only, never blocking
    (`adapter_defaults._VISIBILITY_ONLY`; A6 OD1) — a PR editing the DAG or a phase command
    reaches Gates 2/3 but never `HUMAN_REQUIRED`.
  - `.factory/adapter.yaml` itself is visibility-only for the same reason (OD2); its
    escalation risk is caught only by the semantic diff, and only when that file is in the
    changed set.
  - `safety.hard_exclude_paths` is deliberately outside the floor (OD3): a PR can shrink it,
    caught only by the `safety:`-block diff under the same condition.
  - #411 (open) — the conformance agent refuses self-target runs whose subject is the
    shadowing paths, i.e. the floor's own `dark-factory/scripts/` entry.
  - `tests/test_scheduler.sh` is not in `.github/workflows/ci.yml`'s named bash-test list —
    include this only if the doc claims CI coverage for a boundary mechanism.
  - #414 is a scheduler dispatch-guard bug with no boundary relevance and is deliberately
    excluded.

## Architecture / Approach

`docs/factory-target-boundary.md` is a **plan/implement-phase deliverable, not this
spec's deliverable** — this refine phase is scoped to the spec only (SCOPE BOUNDARY).
The spec below is the outline the plan phase should turn into a task list and the
implement phase should write against.

### Proposed doc structure

1. **Overview** — one paragraph: dark-factory dispatches containerized agents against
   itself and against target repos (MarketHawk today); this doc is the durable contract
   for what the factory infrastructure owns vs. what a target repo owns/customizes, as
   actually shipped by epic #194.
2. **Non-negotiables** — the corrected five-item list:
   - Maker never validates maker (`scripts/factory_core/verifier.py::assert_verifier_independent()`,
     called from `adapter.py` on every loop at load time; docstring cites the
     "clean-room-grader principle", #189).
   - Declared, machine-evaluable stop conditions — `verification.stop_condition` is a
     required field (`adapter.py`'s `required_fields` for the `verification` sub-block) but is
     validated only as a non-empty string; **nothing parses its content**. The caps actually
     evaluated are `scheduling.max_iterations`, `scheduling.deadline_seconds` and
     `budget_caps.max_tokens`, read by `breaker.py::_evaluate_loop_caps()`. The doc must not
     call these "externally checkable": `evaluate_stop_condition`'s own docstring says
     "Cap-class-only stop evaluator (state-file I/O only — no subprocess, no network; the
     external-predicate class lives on #197's verifier.py seam, never here)".
   - Side-effect levels 4–5 are factory-owned — enforced at three sites, all keyed on
     `side_effect.FACTORY_OWNED_MIN_LEVEL = 4`: `adapter.py` requires `budget_caps` +
     `human_checkpoint` at level ≥4; `verifier.py::resolve_and_run` returns
     `STATUS: BLOCKED` / `REQUIRED_PROFILE: factory-owned` for any level ≥4; and
     `handoff.py::cross_check` rejects such a manifest with `producing_loop_factory_owned`.
     A target may *declare* a level-4/5 loop; nothing will run it.
   - Level 6 is human-approved and out of v1 (`side_effect.py` has no level-6 profile;
     `adapter.py` raises `AdapterError` on `sel == 6`, citing "#196/D1" and "out of scope
     for v1" — not "permanently", matching the issue's own framing more precisely than
     the operator comment's wording).
   - Live-trading exclusion — reframed per the Requirements section above: a worked
     target-side example of the level-6 rule, not a separate factory-wide mechanism.
3. **The `loops:` schema (A1)** — `scripts/factory_core/adapter.py`'s `_validate_loop()`
   and sub-block validators; hand-rolled `isinstance`/`AdapterError` validation (no
   `jsonschema`, dependency-free by design per `.archon/memory/architecture.md`'s AVOID
   entry); `schema_version` is inert. Cite `docs/archive/2026-07-07-adapter-schema-v2-loops-design.md`
   and `docs/archive/2026-08-28-adapter-schema-v2-loop-metadata-a1-5-design.md` (A1.5,
   five-move restructuring) as the shipped design record. Note: this repo's own
   `.factory/adapter.yaml` currently declares no `loops:` entries — no self-target
   example exists yet; say so rather than implying one does.
4. **Side-effect levels and enforced profiles (A2)** — `scripts/factory_core/side_effect.py`'s
   `LEVELS`/`Profile`/`profile_for()`; cite `docs/archive/2026-09-04-side-effect-levels-permission-profiles-a2-design.md`.
5. **Verifier contract (A3)** — `scripts/factory_core/verifier.py`, the maker≠checker
   declaration-time check, target-verifier registration (`verification.verifier`
   resolution, fail-closed on absolute/escaping paths), the shared verdict schema
   (`STATUS`/`GATE_TYPE`/`FINDINGS_COUNT`/`SEVERITY`); cite
   `docs/superpowers/specs/2026-08-28-verifier-abstraction-a3-design.md`.
6. **Stop-condition schema (A4)** — `verification.stop_condition` field +
   `breaker.py::evaluate_stop_condition()`; cite
   `docs/archive/2026-08-29-loop-declarative-stop-conditions-a4-design.md`.
7. **Handoff manifest (A5)** — `scripts/factory_core/handoff.py`'s `cross_check()` (R3:
   `producing_loop` must resolve to a declared `loops[].name`); cite
   `docs/superpowers/specs/2026-08-30-artifact-handoff-manifest-a5-design.md`.
8. **Bypass prevention (A6)** — name it by mechanism, not by grepping "bypass" (the
   string doesn't appear in the enforcement code): `FACTORY_OWNED_CRITICAL_DIFF_FLOOR` /
   `FACTORY_OWNED_MIGRATION_SEED_FLOOR` (`adapter_defaults.py`), merged on every
   `adapter.load()` return path via `adapter.py::_apply_boundary_floor()` (both the
   no-file and merged branches — the doc should quote the "Requirement 9" docstring
   comment as the citable source of the "every return path" claim); the semantic adapter
   diff computed exclusively via `git show <ref>:<path>` for both base and HEAD, never
   the working tree (`gate_blast_radius.py`), fixed in PR #410 after a working-tree read
   let a committed escalation pass when the on-disk copy was reverted; `enabled: false`
   suppresses only the hotspot/size triggers, never the floor or the semantic diff
   (`gate_blast_radius.py`, explicit comment to that effect). Cite
   `docs/superpowers/specs/2026-09-08-boundary-bypass-prevention-a6-design.md`, including
   its **OD1** decision that `workflows/**` and `commands/**` stay visibility-only (not
   blocking) while `.factory/hooks/**`, `.claude/**`, `.archon/commands/**`,
   `.archon/workflows/**`, and `dark-factory/scripts/**` are in the blocking floor, and that
   `.factory/adapter.yaml` itself is visibility-only (OD2). Cite the "every return path" claim
   by its two call sites (`adapter.py`'s no-file branch and its merged branch) plus
   `gate_blast_radius.py`, which re-unions the floor when `adapter.load()` raises — not by
   quoting the docstring that makes the claim, which would be a docstring vouching for itself.
   Two further shipped A6 mechanisms the section must name: `gate_blast_radius.py::load_config()`
   reads `blast_radius.*` from the image-baked config layered with `git show <base-ref>:<path>`,
   never the working tree, so a PR cannot flip its own kill switch; and
   `commands/dark-factory-validate.md` prefers the baked
   `/opt/dark-factory/scripts/gate_blast_radius.py` over the clone copy, so a target that tracks
   its own `dark-factory/scripts/` is not the copy that decides its own gate.
9. **Trust model (mandatory — see Requirements).** The shim is a `PATH` shim; an absolute
   `/usr/bin/git` bypasses it. v1 is a policy boundary against mistaken or prompt-injected
   behaviour, not a security boundary against a deliberately hostile agent; that boundary is
   the credential, deferred as #196/D3. Four in-repo references already point here for this.
10. **What is declared vs. what runs (mandatory).** There is no loop dispatcher.
    `verifier.py::resolve_and_run`'s docstring calls itself "the primitive a *future*
    dispatcher, the CLI below, or a test calls per declared loop";
    `breaker.py::format_trip_reason` records that "No live caller constructs the three
    loop-scoped variants today"; the only production caller of `evaluate_stop_condition` is
    `factory_core/cli.py`. A1/A3/A4/A5 are a validated declaration surface plus tested
    primitives — `README.md`'s `loops` row ("parse/validate/surface only, no runtime
    enforcement yet") is accurate and the doc must agree with it. A6 and A2's shims are what
    runs today; all factory phases are configured at `side_effect: 5` (`config/config.yaml`),
    so the level profiles constrain nothing in current operation. Without this section, A1 and
    A3–A5 read as running machinery.
11. **Known gaps** — as listed in Requirements above, stated as open items, not resolved
    history.
12. **README pointer** — one line in `README.md`'s `## Adapter contract` section (near
    the `loops` table row) linking to `docs/factory-target-boundary.md`.

### Self-review the implement phase must run

**Cite symbols, not line numbers.** Every citation is `path::symbol` — a function, class or
module constant (`adapter.py::_apply_boundary_floor`,
`adapter_defaults.FACTORY_OWNED_MIGRATION_SEED_FLOOR`,
`gate_blast_radius.py::_boundary_escalation_findings`) — never `path:line`. Line numbers drift
within days: this spec's own `README.md:174` citation was already off by one before the spec
gate ran, and the doc would have been stale before it merged.

**Add a drift guard.** The implement phase adds one pytest that reads
`docs/factory-target-boundary.md`, extracts every `path::symbol` citation, and asserts the
symbol still appears in that file — following the existing precedent of
`tests/test_verifier_contract_doc_referenced.py` (doc/command reference pinning) and
`tests/test_adapter.py::test_config_yaml_hard_exclude_paths_matches_defaults` (config/code list
pinning). Without it, "matches shipped behavior" has no enforcement after the merge commit, and
this doc becomes the stale artifact it was written to replace.

Before committing, re-verify every citation against the checkout at implement time, not this
spec's snapshot.

## Alternatives considered

1. **Place the doc under `docs/archive/`.** Rejected — `docs/archive/` is for completed
   *workflow* artifacts (specs/plans), not living reference documentation; this doc will
   be read and re-cited by future specs the way `README.md`/`CLAUDE.md` are, and needs a
   durable, non-archived path (consistent with the `codebase-patterns.md` memory entry
   distinguishing living policy docs from archived workflow artifacts).
2. **Transcribe the operator comment's non-negotiables list verbatim, including
   "live trading permanently excluded."** Rejected per the Q&A resolution above — the
   acceptance criterion requires a citable implementing module, the comment's signature
   isn't one of CLAUDE.md's two trusted PM signatures, and asserting an unenforced
   factory-wide rule would itself be a doc-accuracy bug.
3. **Organize the doc strictly by A1–A6 with no unifying Non-negotiables/Overview
   section.** Rejected — the issue's own framing ("the contract, levels, and
   non-negotiables") and its acceptance criteria treat the non-negotiables as a distinct,
   load-bearing section, not an incidental subsection of each mechanism.

## Open questions (non-blocking)

- ~~Whether `README.md`'s `loops` table row should drop "no runtime enforcement yet".~~
  **Resolved at the spec gate: it is accurate, keep it.** No loop dispatcher exists (see doc
  outline section 10); `verifier.resolve_and_run` and the loop-scoped breaker trip reasons have
  no live callers. No follow-up ticket needed. Retained here for the record.

## Assumptions (flagged)

- The plan/implement phases have the same repo checkout available (`scripts/factory_core/*.py`,
  `.factory/adapter.yaml`, `docs/superpowers/specs/`, `docs/archive/`) that this refine
  phase read from; no drift is assumed beyond normal line-number movement, which the
  self-review step above accounts for.
- `#200` (A6) is treated as in-scope for this doc despite not appearing in the issue's
  `Depends on:` list, because the issue body's Scope section explicitly names "bypass
  rules (A6)" and the referenced comment confirms #200 is closed.
