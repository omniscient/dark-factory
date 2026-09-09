# Implementation Plan: `docs/factory-target-boundary.md` — the shipped factory/target boundary contract (A7)

**Issue:** #201

## Goal

Write `docs/factory-target-boundary.md`, the durable reference doc closing epic #194: the
factory/target boundary "as shipped" — non-negotiables, the A1–A6 mechanisms, a trust-model
section, a "what's declared vs. what runs" section, and a known-gaps section — plus a README
pointer. Every factual claim cites a concrete `path::symbol`, re-verified against this checkout
at plan-writing time (all citations below were confirmed present via direct grep against
`origin/main` HEAD during planning). A pytest drift guard extracts every `path::symbol` citation
from the doc and asserts the symbol still exists, so "matches shipped behavior" has real
enforcement after merge, not just at authoring time.

Per the spec's Architecture section, this is a plan/implement-phase deliverable — the refine
phase was scoped to the spec only.

## Architecture

One new doc at `docs/factory-target-boundary.md` (durable reference tier, alongside `README.md`
and `CLAUDE.md` — not `docs/superpowers/` or `docs/archive/`), built section-by-section across
Tasks 1–6, each task adding both doc content and the drift-guard test assertions that pin it.
`docs/adapter-authoring-guide.md` already ships the A2 side-effect-level table and the A5
handoff-manifest schema in full; this doc links to those sections instead of restating them,
and declares an explicit authority order (mirroring the authoring guide's own "if they disagree,
the design doc is authoritative" convention). The doc also resolves the four in-repo forward
references already waiting on it (`docs/adapter-authoring-guide.md`'s trust-model pointer, A2's
design spec, and the `F13` comments in `scripts/shims/git` and `scripts/shims/gh`) via a
dedicated Trust model section.

One line is added to `README.md`'s existing `## Adapter contract` section, near the `loops`
table row, linking to the new doc.

**Live-trading resolution (per the approved spec's Q&A):** the doc does not state "live trading
permanently excluded" as a factory-wide non-negotiable — no code in this repo enforces it. It
appears once, under the level-6 non-negotiable, as a worked example of a target-side exclusion.

## Tech Stack

Markdown (the doc itself, `README.md`), `pytest` content-assertion tests (this repo's existing
convention for doc/code citation pinning — see `tests/test_verifier_contract_doc_referenced.py`,
`tests/test_adapter.py::test_config_yaml_hard_exclude_paths_matches_defaults`).

## File Structure

| Path | Change |
|---|---|
| `docs/factory-target-boundary.md` | New — the boundary contract doc (11 sections) |
| `tests/test_factory_target_boundary_doc.py` | New — drift guard: doc exists, required section headers present, every `` `path::symbol` `` citation resolves to a real file+symbol, known-gap issue numbers present |
| `README.md` | One-line pointer to the new doc, added to `## Adapter contract`, near the `loops` row |

---

## Task 1: Bootstrap the doc + drift-guard test; Overview and Non-negotiables sections

**Files:** `docs/factory-target-boundary.md` (new), `tests/test_factory_target_boundary_doc.py` (new)

### TDD Steps

1. Write the drift-guard test with its helpers and the first two section checks:

```python
"""Drift guard for docs/factory-target-boundary.md (#201): every `path::symbol` citation
must resolve to a real file and a real symbol name in that file, and every required
section header must be present. Extend this file's REQUIRED_SECTIONS/assertions as the
doc grows; never let a citation go unpinned."""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = REPO_ROOT / "docs" / "factory-target-boundary.md"

CITATION_RE = re.compile(r"`([\w./-]+\.(?:py|sh))::([A-Za-z_][A-Za-z0-9_]*)`")

REQUIRED_SECTIONS = [
    "## Overview",
    "## Non-negotiables",
]


def _doc_text() -> str:
    assert DOC_PATH.is_file(), f"{DOC_PATH} does not exist"
    return DOC_PATH.read_text(encoding="utf-8")


def _normalized(content: str) -> str:
    """Collapse whitespace runs (including markdown line wraps) to a single space,
    so multi-word phrase assertions don't break when prose re-wraps across lines."""
    return re.sub(r"\s+", " ", content)


def _section(content: str, header: str) -> str:
    """Slice out one `## `-headed section's body, up to (not including) the next
    `## ` header or end of file — so a claim can be pinned to the section that is
    supposed to make it, not just found anywhere in the doc."""
    start = content.index(header)
    rest_start = start + len(header)
    next_idx = content.find("\n## ", rest_start)
    return content[start:] if next_idx == -1 else content[start:next_idx]


def test_doc_exists_and_has_required_sections():
    # Anchored to a full line (re.MULTILINE ^...$), not a bare substring check: the
    # Overview prose references some of these headers inline in backticks (e.g. when
    # explaining what docs/adapter-authoring-guide.md already covers), and a substring
    # check would pass on that mention even if the doc's own real section were deleted.
    content = _doc_text()
    for header in REQUIRED_SECTIONS:
        pattern = r"^" + re.escape(header) + r"$"
        assert re.search(pattern, content, re.MULTILINE), f"missing section: {header}"


def test_doc_citations_resolve_to_real_symbols():
    content = _doc_text()
    citations = CITATION_RE.findall(content)
    assert citations, "expected at least one path::symbol citation"
    for path_str, symbol in citations:
        target = REPO_ROOT / path_str
        assert target.is_file(), f"citation path does not exist: {path_str}"
        text = target.read_text(encoding="utf-8")
        assert symbol in text, f"symbol {symbol!r} not found in {path_str}"


def test_non_negotiables_cite_the_three_factory_owned_enforcement_sites():
    content = _doc_text()
    for symbol in ("adapter.py", "resolve_and_run", "producing_loop_factory_owned"):
        assert symbol in content


def test_live_trading_is_not_stated_as_a_factory_wide_non_negotiable():
    content = _doc_text()
    assert "permanently excluded" not in content


def test_non_negotiables_cites_the_deploy_publish_exclusion_mechanism():
    content = _normalized(_doc_text())
    assert "migration_seed_auth_patterns" in content
    assert "^deploy/" in content
```

2. Verify fail:
   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_target_boundary_doc.py -x -v
   ```
   Expected: `test_doc_exists_and_has_required_sections` fails with `AssertionError` (doc does
   not exist yet); the other four tests error/fail for the same reason (`DOC_PATH.is_file()`
   assertion in `_doc_text()`).

3. Implement — create `docs/factory-target-boundary.md`:

```markdown
# Factory / Target Boundary Contract

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

## Overview

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
- **Side-effect levels 4-5 are factory-owned**, enforced at three independent sites keyed on
  `scripts/factory_core/side_effect.py::FACTORY_OWNED_MIN_LEVEL` (`= 4`):
  `scripts/factory_core/adapter.py` requires `budget_caps` and `human_checkpoint` on any
  loop declaring `side_effect_level >= 4`; `scripts/factory_core/verifier.py::resolve_and_run`
  returns `STATUS: BLOCKED` / `REQUIRED_PROFILE: factory-owned` for any such level; and
  `scripts/factory_core/handoff.py::cross_check` rejects a handoff manifest from such a loop
  with reason `producing_loop_factory_owned`. A target may *declare* a level-4/5 loop;
  nothing will run it.
- **Level 6 is human-approved and out of v1** — `scripts/factory_core/side_effect.py`
  defines no profile for level 6, and `scripts/factory_core/adapter.py::AdapterError`
  is raised when a loop declares `side_effect_level == 6`, citing `#196/D1` and
  "out of scope for v1" — v1-scoped, not a standing prohibition.
- **Live-trading exclusion is a worked example of the level-6 rule, not a separate
  mechanism.** Nothing in this repo enforces a factory-wide "live trading" prohibition; the
  citable rule is the level-6 rejection above. The factory's own shipped
  `scripts/factory_core/adapter_defaults.py::DEFAULTS` default for
  `safety.sensitive_keywords` (mirrored, at time of writing, in this self-target instance's
  own `epic_autopilot.sensitive_keywords` in `config/config.yaml`, matching strings
  including `trading`, `ibkr`, `live order`, `notional`) is target-layer keyword
  defense-in-depth against *proposing* such a change via the epic-autopilot fast path — not
  a factory-level block, and currently gating nothing at all on this self-target instance,
  since `config/config.yaml` ships `epic_autopilot.enabled: false` (same caveat as
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
```

4. Verify pass:
   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_target_boundary_doc.py -x -v
   ```
   Expected: all five tests pass.

5. Commit:
   ```bash
   git add docs/factory-target-boundary.md tests/test_factory_target_boundary_doc.py
   git commit -m "docs(#201): bootstrap factory-target-boundary.md — overview, non-negotiables"
   ```

**Note for the implementing agent:** `_migration_seed_auth_patterns` is a private
(underscore-prefixed) helper in `scripts/gate_blast_radius.py`. Citing it here is
intentional and consistent with the rest of this doc's citation style (e.g.
`_boundary_escalation_findings`, `_apply_boundary_floor` elsewhere in this plan) — the
drift guard cares only that the named symbol still exists in the file, not its visibility.

---

## Task 2: A1 `loops:` schema and A2 side-effect levels sections

**Files:** `docs/factory-target-boundary.md`, `tests/test_factory_target_boundary_doc.py`

### TDD Steps

1. Extend `REQUIRED_SECTIONS` in `tests/test_factory_target_boundary_doc.py` and add a
   dedicated assertion:

```python
REQUIRED_SECTIONS = [
    "## Overview",
    "## Non-negotiables",
    "## The `loops:` schema (A1)",
    "## Side-effect levels and enforced profiles (A2)",
]


def test_a2_section_links_to_authoring_guide_instead_of_restating_table():
    # Scoped to the A2 section body itself, not "anywhere in the doc" — the Overview
    # section already mentions docs/adapter-authoring-guide.md, so a doc-wide substring
    # check would pass before this task's own content exists (no red phase).
    content = _doc_text()
    section = _section(content, "## Side-effect levels and enforced profiles (A2)")
    assert "docs/adapter-authoring-guide.md" in section
    assert "_PROFILES" not in section, "A2 section must link out, not restate the level table"
```

2. Verify fail:
   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_target_boundary_doc.py -x -v
   ```
   Expected: `test_doc_exists_and_has_required_sections` and
   `test_a2_section_links_to_authoring_guide_instead_of_restating_table` fail — the latter
   with a `ValueError` from `_section()`'s `content.index(header)` (the A2 header does not
   exist in the doc yet); the five pre-existing tests still pass.

3. Implement — append to `docs/factory-target-boundary.md`:

```markdown
## The `loops:` schema (A1)

Validated by `scripts/factory_core/adapter.py::_validate_loop` and its sub-block
validators — hand-rolled `isinstance`/`AdapterError` checks, not `jsonschema`
(dependency-free by design). `schema_version` is inert metadata: a `schema_version: 1`
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
```

4. Verify pass:
   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_target_boundary_doc.py -x -v
   ```
   Expected: all six tests pass.

5. Commit:
   ```bash
   git add docs/factory-target-boundary.md tests/test_factory_target_boundary_doc.py
   git commit -m "docs(#201): add A1 loops-schema and A2 side-effect-levels sections"
   ```

---

## Task 3: A3 verifier contract and A4 stop-condition schema sections

**Files:** `docs/factory-target-boundary.md`, `tests/test_factory_target_boundary_doc.py`

### TDD Steps

1. Extend `REQUIRED_SECTIONS`:

```python
REQUIRED_SECTIONS = [
    "## Overview",
    "## Non-negotiables",
    "## The `loops:` schema (A1)",
    "## Side-effect levels and enforced profiles (A2)",
    "## Verifier contract (A3)",
    "## Stop-condition schema (A4)",
]


def test_a3_section_names_verdict_schema_tokens():
    content = _doc_text()
    section = _section(content, "## Verifier contract (A3)")
    for token in ("STATUS", "GATE_TYPE", "FINDINGS_COUNT", "SEVERITY"):
        assert token in section, f"A3 section missing verdict-schema token: {token}"
```

2. Verify fail:
   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_target_boundary_doc.py -x -v
   ```
   Expected: `test_doc_exists_and_has_required_sections` fails (two new headers missing) and
   `test_a3_section_names_verdict_schema_tokens` fails with a `ValueError` from `_section()`
   (the A3 header does not exist yet); the six pre-existing tests still pass.

3. Implement — append to `docs/factory-target-boundary.md`:

```markdown
## Verifier contract (A3)

`scripts/factory_core/verifier.py` implements the maker-not-checker declaration-time check
(`assert_verifier_independent`, see Non-negotiables), target-verifier registration
(`verification.verifier` resolved relative to the clone root by
`scripts/factory_core/verifier.py::resolve_verifier`, fail-closed on an absolute or
clone-escaping path), and the shared verdict schema
(`STATUS`/`GATE_TYPE`/`FINDINGS_COUNT`/`SEVERITY`). The full operational contract — env
vars, output modes, fail-closed defaults, reserved output names — lives in
`refinement-skills/VERIFIER-CONTRACT.md`, read live by every checker-subagent spawn; this
doc does not duplicate it.

Design record: `docs/superpowers/specs/2026-08-28-verifier-abstraction-a3-design.md`.

## Stop-condition schema (A4)

`verification.stop_condition` (see Non-negotiables for exactly what it does and does not
validate) plus `scripts/factory_core/breaker.py::evaluate_stop_condition`, the
cap-class-only evaluator. The only production caller today is
`scripts/factory_core/cli.py`.

Design record: `docs/archive/2026-08-29-loop-declarative-stop-conditions-a4-design.md`.
```

4. Verify pass:
   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_target_boundary_doc.py -x -v
   ```
   Expected: all seven tests pass.

5. Commit:
   ```bash
   git add docs/factory-target-boundary.md tests/test_factory_target_boundary_doc.py
   git commit -m "docs(#201): add A3 verifier-contract and A4 stop-condition-schema sections"
   ```

---

## Task 4: A5 handoff manifest and A6 bypass prevention sections

**Files:** `docs/factory-target-boundary.md`, `tests/test_factory_target_boundary_doc.py`

### TDD Steps

1. Extend `REQUIRED_SECTIONS` and add an A6-specific assertion:

```python
REQUIRED_SECTIONS = [
    "## Overview",
    "## Non-negotiables",
    "## The `loops:` schema (A1)",
    "## Side-effect levels and enforced profiles (A2)",
    "## Verifier contract (A3)",
    "## Stop-condition schema (A4)",
    "## Handoff manifest (A5)",
    "## Bypass prevention (A6)",
]


def test_a6_names_the_floor_the_semantic_diff_and_the_kill_switch():
    # Uses _normalized() (collapses line-wrap whitespace) because these are prose
    # phrases spanning a hard-wrapped markdown paragraph, not single-line tokens.
    content = _normalized(_doc_text())
    for phrase in (
        "FACTORY_OWNED_CRITICAL_DIFF_FLOOR",
        "_boundary_escalation_findings",
        "never suppresses the floor or the semantic adapter diff",
    ):
        assert phrase in content, f"missing A6 phrase: {phrase}"
```

2. Verify fail:
   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_target_boundary_doc.py -x -v
   ```
   Expected: `test_doc_exists_and_has_required_sections` and
   `test_a6_names_the_floor_the_semantic_diff_and_the_kill_switch` fail.

3. Implement — append to `docs/factory-target-boundary.md`:

```markdown
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
```

4. Verify pass:
   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_target_boundary_doc.py -x -v
   ```
   Expected: all eight tests pass.

5. Commit:
   ```bash
   git add docs/factory-target-boundary.md tests/test_factory_target_boundary_doc.py
   git commit -m "docs(#201): add A5 handoff-manifest and A6 bypass-prevention sections"
   ```

**Note for the implementing agent:** `_migration_seed_auth_patterns` is cited here as the
re-union symbol per architect review — it is the function `gate_blast_radius.py` calls to
rebuild the migration-seed floor list; the doc cites it rather than the bare filename so
the drift guard actually pins this claim.

---

## Task 5: Trust model and "what is declared vs. what runs" sections

**Files:** `docs/factory-target-boundary.md`, `tests/test_factory_target_boundary_doc.py`

### TDD Steps

1. Extend `REQUIRED_SECTIONS` and add trust-model / declared-vs-runs assertions:

```python
REQUIRED_SECTIONS = [
    "## Overview",
    "## Non-negotiables",
    "## The `loops:` schema (A1)",
    "## Side-effect levels and enforced profiles (A2)",
    "## Verifier contract (A3)",
    "## Stop-condition schema (A4)",
    "## Handoff manifest (A5)",
    "## Bypass prevention (A6)",
    "## Trust model",
    "## What is declared vs. what runs",
]


def test_trust_model_states_the_path_shim_limit_plainly():
    content = _normalized(_doc_text())
    assert "`PATH` shim" in content
    assert "not a security boundary against a deliberately hostile agent" in content
    assert "#196/D3" in content


def test_declared_vs_runs_names_phase_levels_config_key():
    content = _doc_text()
    assert "side_effect.phase_levels" in content
    section = _section(content, "## What is declared vs. what runs")
    assert "scripts/factory_core/side_effect.py::_PROFILES" in section, (
        "must name what level 5 actively enforces, not just say levels constrain nothing"
    )
```

2. Verify fail:
   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_target_boundary_doc.py -x -v
   ```
   Expected: `test_doc_exists_and_has_required_sections`,
   `test_trust_model_states_the_path_shim_limit_plainly`, and
   `test_declared_vs_runs_names_phase_levels_config_key` fail.

3. Implement — append to `docs/factory-target-boundary.md`:

```markdown
## Trust model

- **Two independent enforcement layers, both keyed on the run's effective side-effect
  level.** Per `scripts/factory_core/side_effect.py`'s own module docstring, level
  semantics are read by three layers that never re-declare each other's table: **Layer
  A**, which tools the model is offered — each phase node's `denied_tools:` key in
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
  path bypasses it. **v1 is a policy boundary against mistaken or prompt-injected
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
```

4. Verify pass:
   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_target_boundary_doc.py -x -v
   ```
   Expected: all ten tests pass.

5. Commit:
   ```bash
   git add docs/factory-target-boundary.md tests/test_factory_target_boundary_doc.py
   git commit -m "docs(#201): add Trust model and What is declared vs. what runs sections"
   ```

---

## Task 6: Known gaps section

**Files:** `docs/factory-target-boundary.md`, `tests/test_factory_target_boundary_doc.py`

### TDD Steps

1. Extend `REQUIRED_SECTIONS` and add a known-gaps assertion:

```python
REQUIRED_SECTIONS = [
    "## Overview",
    "## Non-negotiables",
    "## The `loops:` schema (A1)",
    "## Side-effect levels and enforced profiles (A2)",
    "## Verifier contract (A3)",
    "## Stop-condition schema (A4)",
    "## Handoff manifest (A5)",
    "## Bypass prevention (A6)",
    "## Trust model",
    "## What is declared vs. what runs",
    "## Known gaps",
]


def test_known_gaps_names_open_issues_and_ods():
    content = _doc_text()
    for token in ("#374", "#407", "#412", "#411", "OD1", "OD2", "OD3"):
        assert token in content, f"missing known-gap reference: {token}"
```

2. Verify fail:
   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_target_boundary_doc.py -x -v
   ```
   Expected: `test_doc_exists_and_has_required_sections` and
   `test_known_gaps_names_open_issues_and_ods` fail.

3. Implement — append to `docs/factory-target-boundary.md`:

```markdown
## Known gaps

Named honestly as open items, not resolved history — all confirmed open as of this
writing:

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
- **OD1** — `workflows/**` and `commands/**` are visibility-only, never blocking
  (`scripts/factory_core/adapter_defaults.py::_VISIBILITY_ONLY`). A PR editing the DAG or
  a phase command reaches Gates 2/3 but never `HUMAN_REQUIRED`.
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
```

4. Verify pass:
   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_target_boundary_doc.py -x -v
   ```
   Expected: all eleven tests pass.

5. Commit:
   ```bash
   git add docs/factory-target-boundary.md tests/test_factory_target_boundary_doc.py
   git commit -m "docs(#201): add Known gaps section (#374, #407, #412, #411, OD1-OD3)"
   ```

---

## Task 7: README pointer

**Files:** `README.md`, `tests/test_factory_target_boundary_doc.py`

### TDD Steps

1. Add a README-linkage test:

```python
def test_readme_links_to_boundary_doc_near_loops_row():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/factory-target-boundary.md" in readme
    loops_idx = readme.index("| `loops` |")
    link_idx = readme.index("docs/factory-target-boundary.md")
    assert abs(readme.count("\n", 0, loops_idx) - readme.count("\n", 0, link_idx)) <= 3, (
        "pointer should be within a few lines of the `loops` table row"
    )
```

2. Verify fail:
   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_target_boundary_doc.py::test_readme_links_to_boundary_doc_near_loops_row -x -v
   ```
   Expected: fails — `README.md` does not yet mention `docs/factory-target-boundary.md`.

3. Implement — in `README.md`, the `### adapter.yaml keys` table ends with the `loops` row
   (`...See docs/archive/2026-08-28-adapter-schema-v2-loop-metadata-a1-5-design.md (#301). |`)
   followed by one blank line and then `All keys are optional and deep-merged over the
   built-in defaults.`. **Keep that existing blank line** (removing it would make GFM parse
   the new paragraph as a continuation row of the table) and insert a new paragraph plus a
   second blank line after it, so the structure becomes: table row → blank line (existing)
   → pointer paragraph (new) → blank line (new) → `All keys are optional...`:

```markdown
See [`docs/factory-target-boundary.md`](docs/factory-target-boundary.md) for the full
factory/target boundary contract — non-negotiables, side-effect levels, the `loops:`
schema, the trust model, and known gaps.
```

4. Verify pass:
   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_target_boundary_doc.py -x -v
   ```
   Expected: all twelve tests pass.

5. Commit:
   ```bash
   git add README.md tests/test_factory_target_boundary_doc.py
   git commit -m "docs(#201): README pointer to factory-target-boundary.md near the loops row"
   ```

---

## Task 8: Final self-review — re-verify citations against the checkout, full suite

**Files:** none (verification only, unless drift is found — then whichever file needs a fixup)

### Steps

1. Re-run the drift guard alone:
   ```bash
   cd /workspace/dark-factory && python -m pytest tests/test_factory_target_boundary_doc.py -v
   ```
   Expected: all twelve tests pass.

2. Re-verify every `path::symbol` citation manually against the checkout at implement time
   (per the spec's mandatory self-review step — the drift guard only proves the symbol
   string exists in the file, not that a human reading it would agree it is the correct
   file). For each citation printed in the doc, confirm with `grep -n <symbol> <path>` that
   it still names a real function/class/constant, not a stale match inside a comment or
   string that happens to contain the same characters:
   ```bash
   cd /workspace/dark-factory
   grep -oE '`[[:alnum:]./_-]+\.(py|sh)::[[:alnum:]_]+`' docs/factory-target-boundary.md \
     | tr -d '`' | sort -u \
     | while read -r citation; do
         path="${citation%%::*}"
         symbol="${citation##*::}"
         echo "=== $path :: $symbol ==="
         grep -n "$symbol" "$path" | head -3
       done
   ```
   (Note: `IFS='::'` on a `read` splits on the *character set* `{:}`, not the literal
   two-character delimiter — it silently mis-parses every citation and always "succeeds"
   with an empty symbol. Use `${citation%%::*}` / `${citation##*::}` parameter expansion,
   as above, which splits on the literal `::` correctly.)
   If any citation no longer resolves to a real definition (renamed/removed since this plan
   was written), fix the doc's prose and citation, re-run the drift guard, and commit the
   fixup on its own (`git commit -m "docs(#201): fix drifted citation <path>::<symbol>"`).

3. Run the full test suite this repo's CI runs, to confirm no regression:
   ```bash
   cd /workspace/dark-factory && python -m pytest tests/ -v
   ```
   Expected: full pass (no new failures introduced by the doc/README/test additions).

4. Confirm no placeholder language slipped in:
   ```bash
   grep -niE "\bTBD\b|\bTODO\b|implement later|add appropriate error handling" docs/factory-target-boundary.md
   ```
   Expected: no matches (exit code 1).

5. File the deferred follow-up the spec requires. The spec's Requirements section states:
   "Retargeting the shims' deny message is out of scope for #201; file it as a follow-up."
   `scripts/shims/git` and `scripts/shims/gh` each carry an `F13` comment reading
   "the adapter guide's section until #201 creates docs/factory-target-boundary.md" — this
   doc now exists, so the shims' own deny-message text (whatever they print when a
   disallowed verb is blocked) should point at it. File the follow-up so this obligation is
   tracked rather than silently dropped:
   ```bash
   cd /workspace/dark-factory
   FOOTER=$(python3 scripts/factory_core/cli.py marker refinement)
   gh issue create --repo "$FACTORY_REPO_SLUG" \
     --title "F13 follow-up: retarget scripts/shims/git and scripts/shims/gh deny messages to docs/factory-target-boundary.md" \
     --body "docs/factory-target-boundary.md (#201) now exists and carries the full trust-model
   writeup. \`scripts/shims/git:65\` and \`scripts/shims/gh:55\` each carry an \`F13\` comment
   that named this doc as its trigger; their deny-message text (shown to the agent when a
   disallowed verb is blocked) should now point at \`docs/factory-target-boundary.md\`'s Trust
   model section instead of (or in addition to) \`docs/adapter-authoring-guide.md\`. Out of
   scope for #201 per that ticket's spec — filed here per its explicit instruction to do so.

   $FOOTER" \
     --label documentation
   ```
   Expected: a new issue is created; note its number in this plan's implementation commit
   message or the PR description for traceability. If `gh issue create` fails (e.g. no
   network in this environment), note the failure in the PR description instead of silently
   skipping the obligation — do not let this step be a silent no-op.

6. No further commit needed if steps 1-4 all pass clean (nothing changed). If step 2 found
   drift, the fixup from that step is already committed.

**Scope note (post-conformance-review):** an earlier version of this task also filed a
second follow-up issue for `README.md`'s inaccurate `hard_exclude_paths` row phrasing.
Both the gating and shadow conformance reviewers independently flagged that as
out-of-scope — the spec requires only that this doc not *inherit* the README error (which
the Known gaps / OD3 content already satisfies) and mandates exactly one follow-up filing
(step 5, the shim deny-message retarget). Removed to keep this task's actions matched to
what the spec actually asks for; the README inaccuracy remains correctly documented,
un-repeated, in Known gaps.
