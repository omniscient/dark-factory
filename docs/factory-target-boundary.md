# Factory / Target Boundary Contract

## Overview

Dark Factory dispatches containerized Claude agents against itself (this repo) and against
target repos (MarketHawk today, via a separate instance). This document is the durable
contract for what the factory infrastructure owns versus what a target repo owns or
customizes, as actually shipped by epic #194 (mechanisms A1–A6).

**Authority order.** Where an A1–A6 section below cites a design spec under
`docs/superpowers/specs/` or `docs/archive/`, that spec is authoritative for design history;
this doc is authoritative for current shipped behavior — re-verify citations against the
checkout, not this doc's prose, when the two seem to disagree. Where a section links to
`docs/adapter-authoring-guide.md` instead of restating a table (A2, A5), the authoring guide
is authoritative for that table, mirroring its own "if they disagree, the design doc is
authoritative" convention — this doc does not keep a second copy.

This doc supersedes nothing else: `docs/adapter-authoring-guide.md` already documents
`## Side-effect levels` and `## Handoff manifest (A5)` in full, and this doc is the
trust-model writeup that `docs/adapter-authoring-guide.md` and two `scripts/shims/*` `F13`
comments already point at (see Trust model, below).

## Non-negotiables

- **Maker never validates maker.** `scripts/factory_core/verifier.py::assert_verifier_independent`
  is called from `scripts/factory_core/adapter.py` on every loop at load time; the
  "clean-room grader" principle traces to #189.
- **Declared stop conditions are validated as present, not as externally checkable.**
  `verification.stop_condition` is a required field (the `required_fields` argument to
  `scripts/factory_core/adapter.py::_validate_subblock`
  for the `verification` sub-block) but is validated only as a non-empty string — nothing
  parses its content. The caps actually evaluated are `scheduling.max_iterations`,
  `scheduling.deadline_seconds`, and `budget_caps.max_tokens`, read by
  `scripts/factory_core/breaker.py::_evaluate_loop_caps`. Per
  `scripts/factory_core/breaker.py::evaluate_stop_condition`'s own docstring, this is a
  "cap-class-only stop evaluator ... the external-predicate class lives on #197's
  verifier.py seam, never here."
- **Side-effect levels 4-5 are factory-owned**, enforced at four independent sites at the
  threshold `scripts/factory_core/side_effect.py::FACTORY_OWNED_MIN_LEVEL` (`= 4`). Two of
  the four read that constant — `scripts/factory_core/handoff.py::cross_check` and
  `scripts/gate_blast_radius.py::_boundary_escalation_findings`. The other two do not:
  `scripts/factory_core/verifier.py` keeps its own private `_FACTORY_OWNED_MIN_LEVEL = 4`,
  pinned to it by a test, and `scripts/factory_core/adapter.py` uses a bare literal `4` that
  no test pins (see Known gaps). Retuning the constant would therefore retune two sites,
  fail a test at the third, and silently leave the fourth behind. The four sites are:
  `scripts/factory_core/adapter.py` requires `budget_caps` and `human_checkpoint` on any
  loop declaring `side_effect_level >= 4`; `scripts/factory_core/verifier.py::resolve_and_run`
  returns `STATUS: BLOCKED` / `REQUIRED_PROFILE: factory-owned` for any such level;
  `scripts/factory_core/handoff.py::cross_check` rejects a handoff manifest from such a loop
  with reason `producing_loop_factory_owned`; and
  `scripts/gate_blast_radius.py::_boundary_escalation_findings` makes a newly declared
  level->=4 loop, or any change to an existing one, a boundary-escalation finding that routes
  the whole PR to `HUMAN_REQUIRED`. The A6 design record names this as "the PR-review-time
  counterpart to `scripts/factory_core/handoff.py::cross_check`'s runtime rejection, catching the escalation
  before merge instead of at first handoff attempt"
  (`docs/superpowers/specs/2026-09-08-boundary-bypass-prevention-a6-design.md`). So: a target may
  *declare* a level-4/5 loop and nothing will run it — and on this self-target instance the
  PR that declares one is stopped at validate, which is the only one of the four sites that
  fires on every PR today.
- **Level 6 is human-approved and out of v1** — `scripts/factory_core/side_effect.py`
  defines no profile for level 6, and `scripts/factory_core/adapter.py::AdapterError`
  is raised when a loop declares `side_effect_level == 6`; the raised message reads "out of
  scope for v1 (#194)" and the decision it implements, `#196/D1`, is cited in the source
  comment above it — v1-scoped, not a standing prohibition. (Grep the error text for
  `#196/D1` and you will not find it; that is why both are named here.)
- **Live-trading exclusion is a worked example of the level-6 rule, not a separate
  mechanism.** Nothing in this repo enforces a factory-wide "live trading" prohibition; the
  citable rule is the level-6 rejection above. The factory's own shipped
  `scripts/factory_core/adapter_defaults.py::DEFAULTS` default for
  `safety.sensitive_keywords` (mirrored, at time of writing, in this self-target instance's
  own `epic_autopilot.sensitive_keywords` in `config/config.yaml`, matching strings
  including `trading`, `ibkr`, `live order`, `notional`) is target-layer keyword
  defense-in-depth against *proposing* such a change via the epic-autopilot fast path — not
  a factory-level block. On this self-target instance it is currently inert, for two
  reasons rather than one: the epic-autopilot path is off (`config/config.yaml` ships
  `epic_autopilot.enabled: false`), and its second reader,
  `scripts/architecture_slice.py::_check_safety_fallback` (reached from
  `scripts/context_pack.py`, and *not* gated on that flag), has no blocking power at all —
  a fired keyword only widens the `ARCHITECTURE.md` context slice to the full document via
  `scripts/architecture_slice.py::_full_doc_result`, and this repo ships no `ARCHITECTURE.md`
  to slice. Note that this branch *is* reached here: `scripts/architecture_slice.py::infer_component`
  resolves from its own module tables (`_LABEL_COMPONENT_MAP` maps the label "dark factory"),
  not from the adapter's `components:` key, which is consulted only later when building the
  section map. The keywords are evaluated; nothing downstream stops a run (same caveat as
  `hard_exclude_paths` below, Known gaps' OD3). The parallel on this self-target instance is
  `deploy/instances/` and `.github/workflows/publish.yml`, held by two independent
  mechanisms: this repo's own `CLAUDE.md` Hard limits (a policy instruction to the agent),
  and, as an actual code-enforced backstop, `.factory/adapter.yaml`'s
  `safety.migration_seed_auth_patterns` entries `^deploy/` and `^\.github/workflows/`, which
  `scripts/gate_blast_radius.py::_migration_seed_auth_patterns` reads and which route a
  match to a `HUMAN_REQUIRED` finding rather than an automatic block. The Claude Skills
  self-modification surface is held the same blocking way, but by a separate, stronger
  floor a target's own `.factory/adapter.yaml` cannot override — the unconditional floor
  in Bypass prevention (A6) below:
  `scripts/factory_core/adapter_defaults.py::FACTORY_OWNED_MIGRATION_SEED_FLOOR` includes
  `^\.claude/` and `^\.factory/hooks/` outright (both are also in the sibling
  `FACTORY_OWNED_CRITICAL_DIFF_FLOOR` that ranks a diff Critical, but it is the
  migration-seed floor specifically that routes to `HUMAN_REQUIRED`).

## The `loops:` schema (A1)

Validated by `scripts/factory_core/adapter.py::_validate_loop` and its sub-block
validators — hand-rolled `isinstance`/`AdapterError` checks, not `jsonschema`
(dependency-free by design; the constraint is recorded as an AVOID entry in
`.archon/memory/architecture.md`). `schema_version` is inert metadata: a `schema_version: 1`
file containing `loops:` validates identically to a v2 file. This repo's own
`.factory/adapter.yaml` currently declares no `loops:` entries — there is no self-target
example yet.

Design record: `docs/archive/2026-07-07-adapter-schema-v2-loops-design.md` (original
schema), `docs/archive/2026-08-28-adapter-schema-v2-loop-metadata-a1-5-design.md` (A1.5,
the five-move restructuring: `discovery`/`handoff`/`verification`/`persistence`/`scheduling`).

## Side-effect levels and enforced profiles (A2)

Level semantics live in one module — `scripts/factory_core/side_effect.py::LEVELS`, the
`scripts/factory_core/side_effect.py::Profile` class, and
`scripts/factory_core/side_effect.py::profile_for`. `docs/adapter-authoring-guide.md`'s
`## Side-effect levels` section already carries the full level-to-profile table, the
`effective_level` fail-closed rule, and `scripts/factory_core/side_effect.py::FACTORY_OWNED_MIN_LEVEL`;
this doc links to it rather than restating it — see the Authority order note above.

Design record: `docs/archive/2026-09-04-side-effect-levels-permission-profiles-a2-design.md`.

## Verifier contract (A3)

`scripts/factory_core/verifier.py` implements the maker-not-checker declaration-time check
(`assert_verifier_independent`, see Non-negotiables), target-verifier registration
(`verification.verifier` resolved relative to the clone root by
`scripts/factory_core/verifier.py::resolve_verifier`, fail-closed on an absolute or
clone-escaping path), and the shared verdict schema
(`STATUS`/`GATE_TYPE`/`FINDINGS_COUNT`/`SEVERITY`). The full operational contract — env
vars, output modes, fail-closed defaults, reserved output names — lives in
`refinement-skills/VERIFIER-CONTRACT.md`, read by each reviewing phase command at phase
start from the baked `/opt/refinement-skills/` copy (`commands/dark-factory-refine.md`,
`commands/dark-factory-plan.md`,
`-conformance.md`, `-code-review.md`), with the inline model pin authoritative if the image
predates it; this
doc does not duplicate it.

Design record: `docs/superpowers/specs/2026-08-28-verifier-abstraction-a3-design.md`.

## Stop-condition schema (A4)

`verification.stop_condition` (see Non-negotiables for exactly what it does and does not
validate) plus `scripts/factory_core/breaker.py::evaluate_stop_condition`, the
cap-class-only evaluator. The only production caller today is
`scripts/factory_core/cli.py`.

Design record: `docs/archive/2026-08-29-loop-declarative-stop-conditions-a4-design.md`.

## Handoff manifest (A5)

`scripts/factory_core/handoff.py::cross_check` enforces R3 — a manifest's
`producing_loop` must resolve to a declared `loops[].name` — and rejects a factory-owned
producer (see Non-negotiables). `docs/adapter-authoring-guide.md`'s
`## Handoff manifest (A5)` section already carries the full schema, intake path, reason
codes, and trust boundary; this doc links to it rather than restating it — see the
Authority order note above.

Design record: `docs/superpowers/specs/2026-08-30-artifact-handoff-manifest-a5-design.md`.

## Bypass prevention (A6)

Named by mechanism — the string "bypass" does not appear in the enforcement code itself:

- **The floor is unconditional and unsuppressible by the PR under review.**
  `scripts/factory_core/adapter_defaults.py::FACTORY_OWNED_CRITICAL_DIFF_FLOOR` and
  `scripts/factory_core/adapter_defaults.py::FACTORY_OWNED_MIGRATION_SEED_FLOOR` are merged
  into `safety` on every `adapter.load()` return path via
  `scripts/factory_core/adapter.py::_apply_boundary_floor` (both the no-file and merged
  branches) — cited here by its two call sites, not by quoting its own docstring as proof.
  `scripts/gate_blast_radius.py::_migration_seed_auth_patterns` re-unions the
  migration-seed floor specifically when `adapter.load()` itself raises.
- **Both sides of the semantic adapter diff come from `git show`, never the working
  tree.** `scripts/gate_blast_radius.py::_boundary_escalation_findings` and
  `scripts/gate_blast_radius.py::_adapter_snapshot` read `git show <ref>:<path>` for both
  the base ref and HEAD. A
  working-tree read previously let a committed escalation pass when the on-disk copy was
  reverted; fixed in PR #410.
- **The `blast_radius.enabled` kill switch cannot flip itself off.**
  `scripts/gate_blast_radius.py::load_config` reads `blast_radius.*` from the merged base
  ref (`git show <base-ref>:<path>`) first, falling back to the image-baked config only
  for keys the base ref doesn't set — never from the branch under review — so a PR cannot
  disable its own gate. (On this self-target instance, `.claude/skills/refinement/config.yaml`
  is untracked and git-excluded, so the base-ref read misses and the image-baked block
  governs here specifically.) Even when `enabled: false`, it never suppresses the floor or
  the semantic adapter diff — it suppresses only the hotspot and size triggers.
- **The self-target instance cannot substitute its own gate script.**
  `commands/dark-factory-validate.md` prefers the image-baked
  `/opt/dark-factory/scripts/gate_blast_radius.py`, falling back to the clone's own
  `dark-factory/scripts/gate_blast_radius.py` only if the baked copy is absent — a target
  that tracks its own `dark-factory/scripts/` is not the copy that decides its own gate.

Design record: `docs/superpowers/specs/2026-09-08-boundary-bypass-prevention-a6-design.md`,
including its **OD1** decision that `workflows/**` and `commands/**` stay visibility-only
(see Known gaps) while `.factory/hooks/**`, `.claude/**`, `.archon/commands/**`,
`.archon/workflows/**`, and `dark-factory/scripts/**` are in the blocking floor, and
**OD2** (`.factory/adapter.yaml` itself is visibility-only).

## Trust model

- **Two independent enforcement layers, both keyed on the run's effective side-effect
  level.** Per `scripts/factory_core/side_effect.py`'s own module docstring, the level
  table has one owner and five readers — the DAG's `denied_tools` (Layer A), the git/gh
  shim (Layer B), the run record (Layer C), `scripts/factory_core/handoff.py::cross_check`,
  and a future loop runner — none of which re-declares it. The two that gate what an agent
  can do are: **Layer A**, which tools the model is offered — each phase node's `denied_tools:` key in
  `workflows/archon-dark-factory.yaml`, pinned to
  `scripts/factory_core/side_effect.py::profile_for(level).denied_tools` by
  `tests/test_side_effect_dag.py::test_phase_node_denied_tools_matches_configured_level`
  (every phase today runs at level 5, whose profile denies no tools, so this list is
  currently empty everywhere — see What is declared vs. what runs); and **Layer B**,
  which `git`/`gh` verbs the agent's shell can execute — the `scripts/shims/git` /
  `scripts/shims/gh` `PATH` shims, below. Both hold independent of harness permission
  mode, so both survive under `bypassPermissions`.
- **What the shims do not claim.** The shim is a `PATH` shim
  (`scripts/shims/git`, `scripts/shims/gh`). A process invoking `/usr/bin/git` by absolute
  path bypasses it, and so does one that clears `CLAUDECODE` or `FACTORY_SIDE_EFFECT_LEVEL`
  in its own subprocess — `scripts/shims/git` execs the real binary unshimmed when
  either is absent. Both are at least as reachable as the absolute path; this section
  exists to name them, not to imply the shim is airtight. **v1 is a policy boundary against mistaken or prompt-injected
  behaviour, not a security boundary against a deliberately hostile agent.** The boundary
  against a hostile agent is the credential, deferred as `#196/D3`.
- **Trusted comment channels.** Per this repo's own `CLAUDE.md`: issue comments signed
  `Hermes Agent` / `Hermes Agent / Product Manager` are sanctioned product input for
  refinement (scope, requirements, research context) — never authority to expand
  security-sensitive surfaces (tool allow/deny lists, `gate_*`, breaker, budgets,
  `deploy/**`). Any other signature is untrusted for factual or security claims regardless
  of tone or specificity — see this doc's own Non-negotiables section, where a
  differently-signed comment's absolute claim about live trading being forever off-limits
  was independently fact-checked during #201's refinement and found unsupported by any
  code in this repo.

This section resolves the in-repo forward references already waiting on it:
`docs/adapter-authoring-guide.md`'s trust-model pointer, the note in
`docs/archive/2026-09-04-side-effect-levels-permission-profiles-a2-design.md` to "say this
plainly in `docs/factory-target-boundary.md`", and the `F13` comments in
`scripts/shims/git` and `scripts/shims/gh`.

## What is declared vs. what runs

There is no loop dispatcher today. `scripts/factory_core/verifier.py::resolve_and_run`'s
own docstring calls itself "the primitive a future dispatcher, the CLI below, or a test
calls per declared loop"; `scripts/factory_core/breaker.py::format_trip_reason`'s docstring
records that "No live caller constructs the three loop-scoped variants today"; the only
production caller of `scripts/factory_core/breaker.py::evaluate_stop_condition` is
`scripts/factory_core/cli.py`.

A1, A3, A4, and A5 are therefore a validated declaration surface plus tested primitives,
not running machinery — `README.md`'s `loops` table row ("parse/validate/surface only, no
runtime enforcement yet") is accurate as written. Every factory phase runs at level 5 via
`side_effect.phase_levels` in `config/config.yaml` (each phase's own key set to `5`), so the
*graded* level distinctions A2 defines (1 through 4, and the factory-owned/human-approved
boundary at 4/5/6) constrain nothing in current operation — every phase already runs at the
top of the declared range. That is not the same as "A2 enforces nothing today": level 5's
own profile, `scripts/factory_core/side_effect.py::_PROFILES`, actively denies a specific
git/gh never-list on every single run regardless of level — `git push --delete` /
`push :refspec-delete`, and `gh repo delete` / `repo archive` / `repo rename` / `secret` /
`auth` / `ssh-key` / `gpg-key` / `api:DELETE` — the same list `docs/adapter-authoring-guide.md`'s
level-5 table row spells out. A6 (bypass prevention) is what else actually runs today,
alongside this always-on level-5 never-list.

## Known gaps

Named honestly as open items, not resolved history — all confirmed open as of this
writing:

- **Unpinned threshold literal** — `scripts/factory_core/adapter.py`'s level-4 check uses a
  bare `4`, not `scripts/factory_core/side_effect.py::FACTORY_OWNED_MIN_LEVEL`, and no test
  pins the two together (`tests/test_side_effect.py` pins only `scripts/factory_core/verifier.py`'s
  private copy). Retuning the constant would silently leave adapter validation at 4.
- **#374** — a `HUMAN_REQUIRED` block from `scripts/gate_blast_radius.py` has no approval
  memory; re-running validate after an operator clears `needs-discussion` re-blocks
  identically.
- **#407** — nothing deterministic consumes `blast.md`; `scripts/verdict_gate_check.sh`
  only guards `conformance.md`/`review.md`.
- **#412** — fail-closed findings from
  `scripts/gate_blast_radius.py::_adapter_snapshot` carry no diagnostic beyond
  "adapter.yaml unparseable at `<ref>`", and the base ref is validated against HEAD's
  current schema, not the schema live at the base commit.
- **#411** — the conformance agent refuses self-target runs whose subject is the floor's
  own shadowing-path entry (`dark-factory/scripts/**`).
- **OD1** — `workflows/**` and `commands/**` are visibility-only — never `HUMAN_REQUIRED`
  on boundary-floor grounds (the independent hotspot and size triggers in
  `scripts/gate_blast_radius.py` are unaffected, and today return nothing for these paths
  only because no hotspot entry lists them and `size_budget_blocks: false` — data, not
  structure), never blocking
  (`scripts/factory_core/adapter_defaults.py::_VISIBILITY_ONLY`). A PR editing the DAG or
  a phase command reaches Gates 2/3 and is never `HUMAN_REQUIRED` **on boundary-floor
  grounds** — flipping `size_budget_blocks` to `true` would make a large one blocking
  again, by a different trigger.
- **OD2** — `.factory/adapter.yaml` itself is visibility-only for the same reason; its
  escalation risk is caught only by the semantic adapter diff, and only when that file is
  in the changed set.
- **OD3** — `safety.hard_exclude_paths` is deliberately outside the floor; a PR can shrink
  it, caught only by the `safety:`-block diff under the same condition. Note also what
  `hard_exclude_paths` does *not* do: its only consumer is
  `scripts/factory_core/epic_autopilot.py::hard_excluded`, which filters epic-autopilot
  candidate *tickets* — it never inspects a diff or aborts a run, and
  `config/config.yaml` currently ships `epic_autopilot.enabled: false`. `README.md`'s
  `hard_exclude_paths` row phrasing ("matched diff paths abort the run") does not describe
  current code; this doc does not repeat that claim.
- **CI coverage** — `tests/test_scheduler.sh` exists but is not in
  `.github/workflows/ci.yml`'s bash-test list.
