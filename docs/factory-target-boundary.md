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
  `verification.stop_condition` is a required field (`scripts/factory_core/adapter.py::required_fields`
  for the `verification` sub-block) but is validated only as a non-empty string — nothing
  parses its content. The caps actually evaluated are `scheduling.max_iterations`,
  `scheduling.deadline_seconds`, and `budget_caps.max_tokens`, read by
  `scripts/factory_core/breaker.py::_evaluate_loop_caps`. Per
  `scripts/factory_core/breaker.py::evaluate_stop_condition`'s own docstring, this is a
  "cap-class-only stop evaluator ... the external-predicate class lives on #197's
  verifier.py seam, never here."
- **Side-effect levels 4-5 are factory-owned**, enforced at three independent sites at the
  threshold `scripts/factory_core/side_effect.py::FACTORY_OWNED_MIN_LEVEL` (`= 4`). Only
  `scripts/factory_core/handoff.py::cross_check` reads that constant;
  `scripts/factory_core/verifier.py` keeps its own private `_FACTORY_OWNED_MIN_LEVEL = 4`,
  pinned to it by a test, and `scripts/factory_core/adapter.py` uses a bare literal `4` that
  no test pins — see Known gaps. The three sites are:
  `scripts/factory_core/adapter.py` requires `budget_caps` and `human_checkpoint` on any
  loop declaring `side_effect_level >= 4`; `scripts/factory_core/verifier.py::resolve_and_run`
  returns `STATUS: BLOCKED` / `REQUIRED_PROFILE: factory-owned` for any such level; and
  `scripts/factory_core/handoff.py::cross_check` rejects a handoff manifest from such a loop
  with reason `producing_loop_factory_owned`. A target may *declare* a level-4/5 loop;
  nothing will run it.
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
  `scripts/context_pack.py`, and *not* gated on that flag), never reaches its keyword branch
  here because `.factory/adapter.yaml` declares `components: {}`, so
  `scripts/architecture_slice.py::infer_component` resolves nothing (same caveat as
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
