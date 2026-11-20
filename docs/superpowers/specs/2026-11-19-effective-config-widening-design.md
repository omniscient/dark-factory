# Widen `effective_config` to Every Knob — One Config Module (#180)

**Revised:** 2026-11-19 (operator amendment after spec-gate review: no file deletion, clone-side materialized file retained, scheduler stays first-match and fail-closed, hard_exclude_paths fallback never reads a clone, unmigrated readers scoped out)

**Issue:** #180
**Status:** re-refined from current `main` (commit `f457c2c`, 2026-11-17) on 2026-11-19.
**Supersedes:** `docs/superpowers/specs/2026-07-23-effective-config-widening-design.md`
(deleted by this commit — same ticket, same branch; superseding in place avoids two
specs both matching `#180` in `docs/superpowers/specs/`).
**Related, explicitly NOT this ticket's scope:** #205 (OPEN — fix `epic_autopilot.py`'s
`hard_exclude_paths` adapter-shadow precedence bug; this ticket only collapses its
*mechanism*, not its behavior).

---

## Why this spec replaces the 2026-07-23 draft

The refine branch (`refine/issue-180-refactor-config---widen-effective-config`) forked
from `main` on 2026-07-22 (`git merge-base HEAD main` → `cf5858d`) and was never synced
again across three more refinement passes (2026-07-23, 2026-08-08, now 2026-11-19).
`main` advanced **463 commits** in that window. The 2026-07-23 spec re-derived its
figures from `main` at the time, but "the time" is now four months stale — re-verifying
every claim against current `main` this pass (not the branch's checkout) found the
headline premise **still live and substantively unchanged**, but one concrete mechanism
changed shape, one safety-gate citation in the old spec was factually wrong, and one
operator review point needed a concrete answer. All three are folded in below as this
pass's requirements, replacing (not appending to) the superseded spec.

## Overview / Problem Statement

`config/config.yaml` (now 184 lines — grew from the old spec's 158 via `conformance.
shadow_model` and a new `side_effect.phase_levels` block, neither relevant to this
ticket's scope) still has no single reader. Re-verified against `main`@`f457c2c`:

1. **`scheduler.sh::read_config()`** — unchanged: 33 `_set_cfg` call sites (`grep -c
   "_set_cfg " scheduler.sh` → 33), each its own `yq` subprocess against a 3-path
   candidate search (`resolve_config_yaml()`, `scheduler.sh:27-40`: the first existing
   file wins whole-file, with no merge, and the first candidate is the bind-mounted
   target checkout's `config/config.yaml` — so this is not a baked-only read; see
   Requirement 2). Runs pre-clone, in the scheduler's main
   exec path — the scheduler is a long-running host daemon, not a per-run container.
2. **`entrypoint.sh::_entrypoint_cfg_apply()`** — unchanged: a second, narrower `yq`
   reader (5 knobs: `FACTORY_WIP_LIMIT`, `CONFLICT_RESOLUTION_AI_TIER`, and 3
   `session_window_*` knobs) pointed at `${CLONE_DIR}/.claude/skills/refinement/
   config.yaml`. Runs post-clone, before `archon workflow run`.
3. **`scripts/factory_core/effective_config.py`** — byte-identical to the 2026-07-23
   draft's description (confirmed via `git diff` against `main`: zero changes to this
   file in the 463-commit window). `resolve()` already deep-merges the full baked←clone
   tree; `TARGET_TUNABLE_BLOCKS` stays `("token_optimization",)` — adapter-layer scope
   only, by design (see existing `.archon/memory/architecture.md` entry on #180; do not
   widen this). `materialize()`'s merge-and-write branch is the live path on
   both targets today: dark-factory has never committed a clone-side copy and
   MarketHawk no longer does (see Requirement 3).
4. **Changed since 2026-07-23: #183 landed** (PR #322, merged 2026-07-27, well before
   this pass). The old spec's "5 inline `python3 -c "import yaml; ..."` workflow-YAML
   readers" no longer exist as 5 copies — `workflows/archon-dark-factory.yaml`'s 5
   `enforce-budget-*` nodes are now one-line calls to `scripts/budget_gate.sh
   <scenario>` (lines 402, 442, 744, 1143, 1379 on current `main`). **`budget_gate.sh`
   itself still does its own inline `python3 -c "import yaml; ..."` read of
   `.claude/skills/refinement/config.yaml`** (confirmed in its source) — it does not use
   `effective_config`. Net effect of #183 for this ticket: the migration target
   shrinks from "5 duplicate inline readers" to "1 shared script with 1 reader" — less
   work, same requirement (get it off its own YAML parse and onto the materialized
   artifact).
5. **`scripts/architecture_slice.py::_load_config()`** — unchanged: its own 3-path
   candidate list + `yaml.safe_load`, pulling `dispatch_ceiling.keywords` and
   `epic_autopilot.*` keys.
6. **`scripts/factory_core/epic_autopilot.py::_load_exclude_paths()`**
   (`epic_autopilot.py:409`) — unchanged in substance: a 3-mechanism stack
   (`adapter.get()` → `yq` subprocess against the 4-path `_CONFIG_PATHS` list at
   `epic_autopilot.py:320-323` → hardcoded default). It is the **live** exclusion
   loader: `_effective_exclude_paths()` (`epic_autopilot.py:350-361`) unions two calls
   to it (the `CLONE_DIR`/cwd lookup and the target-adapter lookup, "never narrower
   than either"), and that union is the `exclude_paths` handed to `hard_excluded()`
   (`epic_autopilot.py:627` → `:255`). The `yq` branch is reached only when the adapter
   lookup raises or returns a non-list. Its candidate order is project
   `config/config.yaml` → baked → clone-side copy → legacy, first file yielding a
   non-empty list wins; the baked file always exists in the image, so a clone-side
   copy can never win today. The sibling `_hard_exclude_paths()`
   (`epic_autopilot.py:326`, adapter → hardcoded `_DEFAULT_EXCLUDE`) has no production
   caller (only `tests/test_adapter.py`) and is not in scope.

**A live, related bug, confirmed still present, still out of scope, still correctly
split into #205 (confirmed OPEN via `gh issue view 205`):** `.factory/adapter.yaml`'s
`safety.hard_exclude_paths` (always set) makes `_load_exclude_paths()`'s
`adapter.get()` call always win, so `config.yaml`'s `epic_autopilot.hard_exclude_paths`
is unreachable except when the adapter lookup raises. Still latent
(`epic_autopilot.enabled: false`).
`docs/factory-target-boundary.md` (shipped since the 2026-07-23 pass) now documents
precisely what `hard_exclude_paths` does and does not do (OD3: it filters epic-autopilot
*candidate tickets*; it never inspects a diff or aborts a run) — useful background, not
additional scope.

**A new, explicitly-out-of-scope trust boundary found this pass:** `entrypoint.sh::
_compute_side_effect_level()` (added by #196, after 2026-07-23) deliberately reads
**only** the baked `/opt/dark-factory/config/config.yaml` (never the clone, never
`effective_config`'s merged output) to compute a run's side-effect level, by design —
its own comment states the target clone must never be able to raise its own level. This
is a 7th thing that reads `config.yaml`, but — like `.factory/adapter.yaml`'s `safety.*`
block (Requirement 6, carried from the prior spec) — it is a deliberate fail-closed
trust boundary, not a knob-delivery gap, and widening `effective_config` must not
become a path to merge clone content into it.

**Readers the earlier passes did not inventory (found by the spec-gate review).** Both
live targets get a materialized, git-excluded `<clone>/.claude/skills/refinement/
config.yaml` on every run, and these read it directly: `scripts/budget_enforce.py`
(`--config`, passed by `budget_gate.sh`), `scripts/diff_rank.py` (`--config`),
`scripts/memory_retrieve.py` (three first-match readers), `scripts/
load_memory_context.sh`, `commands/dark-factory-refine.md`, `commands/
dark-factory-plan.md`, `commands/dark-factory-conformance.md`, `commands/
dark-factory-code-review.md`, `commands/dark-factory-validate.md`, and the
`preview.enabled` read at `workflows/archon-dark-factory.yaml:846`. Two more read
config through deliberate trust boundaries: `scripts/gate_blast_radius.py::
load_config` (baked `blast_radius` block ← `git show <base-ref>:<path>`, never the
working tree) and `scripts/factory_core/side_effect.py` (the `--config` behind
`_compute_side_effect_level()`). None of these is migrated by this ticket — see
Requirement 2, Requirement 6 and "Owner-reversible choice".

---

## Requirements

1. **`effective_config.resolve()` merges the full config tree** (baked ← clone ←
   adapter) for provenance purposes, not just `token_optimization`. (Unchanged
   observation from the prior pass: it already returns the full merged dict today —
   `_deep_merge(merged, clone_cfg)` merges the whole clone file; `TARGET_TUNABLE_BLOCKS`
   only limits which blocks the *adapter* layer may override, which stays narrow by
   design — see Requirement 6.) `resolve()`'s merge is not changed by this ticket;
   what the new artifacts contain in each mode is fixed by Requirement 2, including
   the committed-copy-alone rule.
2. **`effective_config` gains two new materialized output shapes**, both written by
   one shared writer:
   - `run-config.json` — canonical, full tree (serves nested/list consumers:
     `budget_gate.sh`, `architecture_slice.py`'s keyword/path reads).
   - `run-config.env` — a flat `dotted.path → ENV_VAR` whitelist projection covering the
     33 scalars `scheduler.sh::read_config()` exports and the 5 knobs
     `_entrypoint_cfg_apply` reads (`ABOVE_CEILING_KEYWORDS` is a single regex string,
     not a list — it belongs in the env whitelist; genuine lists like
     `hard_exclude_paths` stay JSON-only).
   The writer is invoked at two call sites with different inputs:
   - **Scheduler mode** (`scheduler.sh`, pre-clone, `--clone-dir` omitted/`None`).
     Scheduler mode resolves exactly one file: the first existing of
     `/workspace/project/config/config.yaml`, `/opt/dark-factory/config/config.yaml`,
     `/opt/refinement-skills/config.yaml` (today's `CONFIG_YAML_PATHS`,
     `scheduler.sh:27-31`), whole-file, no merge, no adapter overlay, never
     `<clone>/.claude/skills/refinement/config.yaml`. This is **not** "baked-only": the
     first candidate is the target checkout bind-mounted read-only at
     `/workspace/project` (`deploy/docker-compose.yml:91`), and `docs/configuration.md:64`
     states that on the scheduler side the target's file replaces the baked one
     entirely. Today the self instance resolves its host checkout's file and MarketHawk
     (no `config/config.yaml`) resolves the baked file; both must stay so. Test: with
     both of the first two present and differing on `dispatch_ceiling.keywords` and
     `epic_autopilot.enabled`, the first wins entirely, and keys absent from it resolve
     empty, not to the baked value.
   - **Run mode** (`entrypoint.sh`, post-clone, real `--clone-dir`), extending — not
     replacing — today's `--materialize` call at `entrypoint.sh:834-835`. For a clone
     with no committed copy (both live instances) `run-config.json` is the `resolve()`
     output (baked ← adapter `token_optimization`), semantically equal to the clone-side
     file written in the same run. If a clone commits its own copy, `run-config.json`
     is that copy alone (today's "clone file wins": `materialize()` leaves the file in
     place and every run-side reader reads only it), not a baked or adapter merge.
   **The existing clone-side materialization (`<clone>/.claude/skills/refinement/
   config.yaml`, git-excluded, same bytes as today) is retained unchanged;
   `run-config.{json,env}` are additional outputs of the same `resolve()` call.**
   Readers not migrated by this ticket keep reading the clone-side file:
   `budget_enforce.py`, `diff_rank.py`, `memory_retrieve.py`, `load_memory_context.sh`,
   `commands/dark-factory-{refine,plan,conformance,code-review,validate}.md`, the
   `preview.enabled` read at `workflows/archon-dark-factory.yaml:846`, and (after PR
   #542) `cli.py docs-conformance-config`. `gate_blast_radius.py::load_config` and
   `side_effect.py` are trust-boundary readers and are out of scope like
   `_compute_side_effect_level` (Requirement 6).
   The new artifacts land in `$ARTIFACTS_DIR` (entrypoint/workflow — the existing
   per-run hand-off directory already used for `context-budget.json`, `token-opt-
   caps.env`) and `$SCHEDULER_STATE_DIR` (scheduler — mirrors the existing
   `scheduler-state.json` convention), not inside the git-tracked clone.
   `run-config.json` carries the whole tree, so it also contains
   `side_effect.phase_levels` and `blast_radius`; no reader may consume those two
   blocks from it (Requirement 6).
3. **No file deletion.** dark-factory has never committed `.claude/skills/refinement/
   config.yaml` (`git log --all -- <path>` is empty) and MarketHawk no longer does.
   Both live targets already take `materialize()`'s merge-and-write branch. The "clone
   config present — left in place" branch stays byte-for-byte unchanged for any future
   target that commits a copy. README.md:78/:230 need no "transition" reframing.
   (The 2026-07-07, 2026-07-23 and first 2026-11-19 drafts all required deleting a
   committed duplicate; that premise was false and is withdrawn.) `materialize()` and
   `main()` keep their fail-open contract on the run side (exit 0 on any error).
4. **Collapse the redundant readers into consumers of the new artifacts:**
   a. `scheduler.sh`'s 33 `_set_cfg`/`yq` call sites collapse into one generic loop
      that parses `run-config.env` into a side table and replays `_set_cfg`'s exact
      semantics — **not** a naive `source` (would clobber operator env-override vars
      and skip the drift-warning log). Preserve: env-wins-over-config (a variable set
      to the empty string still wins), the `[config] VAR=... (env override; config has
      '...')` log line, and null-as-empty. `resolve_config_yaml`'s 3-path first-match
      search moves into the Python module's scheduler mode with the same order and the
      same whole-file semantics (Requirement 2) and the shell function is retired.
      `scheduler.sh` stays fail-closed: if no candidate file exists, or the module
      produces no `run-config.env` or an empty one, `read_config` prints `FATAL: cannot
      read config.yaml` and exits 1 (today's `scheduler.sh:47`).
      `tests/test_config_deletion.sh` is ported to assert this.
   b. `entrypoint.sh::_entrypoint_cfg_apply()` is retired; its 5 knobs are covered by
      the same `run-config.env` whitelist and a shared shell helper (not two separate
      re-implementations). The entrypoint helper preserves `_epcfg`
      (`entrypoint.sh:56-66`): a variable already set, including by the bootstrap
      defaults at `entrypoint.sh:33-38` (so, in practice, always), is never
      overwritten; a null config value is skipped; the `[entrypoint-config]` log
      prefix is kept. The run side stays fail-open as today (missing artifact → warning,
      bootstrap defaults kept).
   c. **`scripts/budget_gate.sh`'s own inline `python3 -c "import yaml; ..."` read is
      replaced** by a read of `$ARTIFACTS_DIR/run-config.json` (e.g. via `jq`) — #183
      already collapsed the five inline readers to this one script. `budget_gate.sh`
      keeps `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS` kill-only semantics and falls back to
      `false false 30000` (observe) when `run-config.json` is missing or unparseable.
      It keeps passing the clone-side file to `budget_enforce.py --config`
      (`budget_enforce.py` is not migrated); Requirement 8's equality test is what
      guarantees the two sources of this one gate cannot diverge.
   d. `scripts/architecture_slice.py::_load_config()` is replaced by reading the
      materialized `run-config.json`; when invoked standalone/pre-materialization it
      falls back to the run-mode resolver of Requirement 2 (clone copy alone if one is
      committed, else baked ← adapter `token_optimization`), not its own 3-path
      `yaml.safe_load`.
5. **`epic_autopilot.py`: mechanism-collapse only, behavior unchanged.** Replace the
   `yq` subprocess in `_load_exclude_paths()`'s fallback branch. Route the fallback
   through a first-match resolver with exactly today's `_CONFIG_PATHS` candidate list
   and order (`epic_autopilot.py:320-323` — the scheduler-mode order of Requirement 2
   plus the clone-side entry in its current third position): the first file yielding a
   non-empty list wins, no merge. It must never be called with a clone dir, and never
   through the run-mode `resolve(clone_dir)` merge — `_deep_merge` replaces lists, so
   that would let a target's clone-side file replace the factory list, which it cannot
   do today. Characterization tests: (a) adapter present → adapter list; (b)
   `adapter.get` raising → baked list even when a clone-side file declares a different
   list; (c) no config → `_DEFAULT_EXCLUDE`. `_hard_exclude_paths()` is untouched (it
   has no production caller). The actual precedence fix stays entirely in #205.
6. **Trust-boundary readers stay outside `effective_config`'s widened scope:**
   `.factory/adapter.yaml`'s `safety.*` block; `entrypoint.sh::
   _compute_side_effect_level()`'s baked-only read and `scripts/factory_core/
   side_effect.py` behind it; and `scripts/gate_blast_radius.py::load_config` (baked ←
   base-ref, never the working tree). All are deliberate trust boundaries (human-
   in-the-loop surface / anti-privilege-escalation surface per CLAUDE.md's hard limits),
   not `config.yaml` knob-delivery gaps, and none may be changed to read
   `run-config.json` or `run-config.env`. `run-config.json` still carries
   `side_effect.phase_levels` and `blast_radius` because it is the whole tree; those
   copies are inert and no migrated reader may consume them. The only adapter
   interaction this ticket touches is unchanged: `TARGET_TUNABLE_BLOCKS` stays
   `("token_optimization",)`.
7. **No dedicated rollback kill-switch.** Fail-open guards against errors, not silent
   wrong-value drift; a legacy-mode flag would add an untested, bit-rotting second code
   path. Substitute: a golden-fixture equivalence test (Requirement 9, catches silent
   drift), fail-closed startup preserved in `scheduler.sh` (Requirement 4a), and
   fail-open preserved on the run side.
   **Correction from the 2026-07-23 draft (operator feedback, 2026-07-23T02:00:59Z):**
   that draft additionally justified this by claiming the ticket "self-triggers the
   `dispatch_ceiling` human-pairing gate (size L + `refactor` keyword)." This is
   **factually wrong** and must not be repeated: per `is_above_ceiling()` at
   `scripts/scheduler_lib.sh:44`
   (confirmed unchanged on `main`, and now stated correctly in `config/config.yaml`'s
   own `dispatch_ceiling` comment), only **XL** tickets always park, and **M** tickets
   park only on a keyword title match — **L tickets (this ticket is `size: L`) are
   never parked**. The actual human checkpoints for this change are the plan-pending-
   review gate, the conformance/code-review gates, and the human PR-merge gate — not
   dispatch ceiling. The plan phase must state this correctly.
8. **Cross-target guarantee:** for a clone with no committed copy (both live
   instances), `run-config.json` must be semantically equal to the YAML `materialize()`
   writes into the clone in the same run. Test with an adapter that sets
   `token_optimization.enforce.implement: true` (MarketHawk's shape) and with no adapter
   block (self). No manual MarketHawk-side check is added to this ticket. Also:
   - Extend `tests/test_effective_config.py::test_clone_config_wins_over_baked_and_is_
     never_dirtied` so the clone fixture overrides a non-`token_optimization` knob
     (e.g. `dispatch_ceiling` or `epic_autopilot`), and assert the committed clone
     file's bytes are unchanged and nothing new is git-excluded — proves a future
     target that commits its own copy keeps the unchanged "left in place" branch.
   - With a committed clone file present, assert `run-config.json` equals that file
     alone (Requirement 2): no baked fill-in for keys the copy omits, no adapter
     overlay.
   - The scheduler-mode first-match test of Requirement 2.
   - Keep `test_materialize_writes_merged_config_and_git_exclude_once` passing
     unmodified (the clone-side write is retained).
9. **Test coverage:** extend `tests/test_effective_config.py` (currently 12 tests) for
   the JSON/env output, the two call-site modes, and Requirement 8's tests; add the
   `epic_autopilot.py` characterization tests (Requirement 5). Golden test: a
   checked-in expected-values fixture (33 scheduler variables plus the 5 entrypoint
   variables, generated from current main by the old readers before they are deleted),
   asserted against `run-config.env` in pytest. Update `tests/test_budget_gate.sh`
   (CI; its fixture writes a clone-side YAML today). Update `tests/test_scheduler.sh`,
   `test_dispatch_ceiling.sh`, `test_config_deletion.sh` and
   `test_epic_autopilot_config.sh`. Either add each to `.github/workflows/ci.yml` or
   state in the plan that they do not run in CI and cannot run on the Windows host; any
   new `.sh` test must be listed in `ci.yml`. `tests/test_config_deletion.sh` is the
   named test for safety invariant 3 and must be listed in `ci.yml`.
10. **Docs.** Update `docs/configuration.md`, `docs/scheduler.md`,
    `docs/run-lifecycle.md` and `docs/features.md` wherever they cite `_set_cfg`,
    `read_config`, `_entrypoint_cfg_apply`, `resolve_config_yaml` or their line ranges;
    `python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md
    docs/features.md docs/configuration.md` must pass. No Layer 1 key rows change (this
    ticket adds no config key). Re-derive line numbers after PR #542 merges (6
    `enforce-budget-*` nodes, `docs_conformance.*`).

---

## Safety invariants (hard requirements)

A changed default, precedence or source for any of these keys is a gate change and is
not allowed as a side effect of this refactor (CLAUDE.md hard limits). Each invariant
has a named test; the plan may not drop or weaken one.

1. **No `gate_*`, breaker, budget, ceiling, autopilot or tool-list key changes its
   effective value or its source file for either live instance** (self target, and
   MarketHawk). Tests: `tests/test_effective_config.py::
   test_run_config_env_matches_golden_fixture` (the Requirement 9 fixture) and
   `::test_run_config_json_equals_clone_side_materialization` (Requirement 8, both
   adapter shapes); `tests/test_budget_gate.sh` (kill-switch stays kill-only; missing
   or unparseable `run-config.json` → observe).
2. **A target clone cannot override any key it cannot override today.** Named keys:
   `epic_autopilot.hard_exclude_paths`, `dispatch_ceiling.*`,
   `side_effect.phase_levels`, `blast_radius.*`. Tests: `tests/test_epic_autopilot.py::
   test_exclude_paths_fallback_ignores_clone_side_config` (Requirement 5 case b);
   `tests/test_effective_config.py::test_scheduler_mode_first_match_never_reads_clone`
   (Requirement 2); `tests/test_effective_config.py::
   test_no_reader_takes_side_effect_or_blast_radius_from_run_config` (a source scan:
   `gate_blast_radius.py`, `side_effect.py` and `_compute_side_effect_level` contain no
   `run-config` reference); `tests/test_blast_radius.py` and
   `tests/test_entrypoint_current_run.sh` pass unmodified.
3. **The scheduler exits 1 when no config resolves** (no candidate file, or a missing
   or empty `run-config.env`). Test: `tests/test_config_deletion.sh`, ported and listed
   in `.github/workflows/ci.yml`.

**Acceptance matrix.** "Source today" is what each live instance resolves on
`main`@`f457c2c`; the last column is what the implementation must show.

| Key(s) | Reader today | Self | MarketHawk | Required after this ticket | Test |
|---|---|---|---|---|---|
| `dispatch_ceiling.{enabled,label,keywords}`, `epic_autopilot.*` scalars, `main_red_autofix.*`, `scheduler.{max_retries,rate_limit_floor,factory_wip_limit}`, `refine.wip_limit` | `scheduler.sh::read_config()` first-match | host checkout `config/config.yaml` | baked | same file, same values, env still wins | golden fixture; scheduler-mode first-match |
| `epic_autopilot.hard_exclude_paths` | `_load_exclude_paths()`: adapter → first-match config | adapter | adapter | same order; clone-side copy never beats baked | Requirement 5 (a)(b)(c) |
| `token_optimization.{enforce_budgets,enforce.*,budgets.*,default_budget_tokens}` | `budget_gate.sh` + `budget_enforce.py` on the clone-side materialized file | = baked | = baked ← adapter (`enforce.implement: true`) | identical values from `run-config.json`; observe on missing artifact; kill-switch kill-only | Requirement 8 equality; `test_budget_gate.sh` |
| `blast_radius.*` | `gate_blast_radius.py::load_config` (baked ← base-ref) | unchanged | unchanged | reader untouched | source scan; `test_blast_radius.py` |
| `side_effect.phase_levels` | `_compute_side_effect_level()` (baked only) | unchanged | unchanged | reader untouched | source scan; `test_entrypoint_current_run.sh` |
| `code_review.{block_threshold,fail_open}`, `conformance.*`, `docs_conformance.*` (after PR #542), `preview.enabled`, `direct_to_pr.*_grace_minutes` (run side) | phase commands on the clone-side materialized file | materialized | materialized | file still written, same bytes | `test_materialize_writes_merged_config_and_git_exclude_once` |
| Tool allow/deny (`denied_tools`) | static in `workflows/archon-dark-factory.yaml` | — | — | untouched | DAG checks |
| adapter `safety.*` | `factory_core/adapter.py` | — | — | untouched | existing `tests/test_adapter.py` |

Nothing under `deploy/instances/**`, `.github/workflows/publish.yml` or
`.claude/skills/**` is touched by this ticket.

## Owner-reversible choice

The operator, acting on the spec-gate review while the owner was away, chose to
**scope the unmigrated readers out** of this ticket: `scripts/budget_enforce.py`,
`scripts/diff_rank.py`, `scripts/memory_retrieve.py`, `scripts/load_memory_context.sh`,
`commands/dark-factory-refine.md`, `commands/dark-factory-plan.md`,
`commands/dark-factory-conformance.md`, `commands/dark-factory-code-review.md`,
`commands/dark-factory-validate.md`, the `preview.enabled` read at
`workflows/archon-dark-factory.yaml:846`, and (after PR #542) `cli.py
docs-conformance-config`. They keep reading the clone-side materialized file, which is
why that file is retained. Migrating them onto `run-config.json` is a follow-up ticket,
not this one. The owner may reverse this; doing so makes the ticket larger than `size:
L` and needs a re-refine. Consequence for the issue's acceptance criteria: "one
resolved config for all readers" is met for the scheduler, the entrypoint knobs,
`budget_gate.sh`, `architecture_slice.py` and the `epic_autopilot.py` fallback only.

## Sequencing

The plan must be written against `main` after PR #542 (docs-conformance gate) merges,
if it has merged by then. PR #542 adds a sixth `enforce-budget-*` node (six
`budget_gate.sh` calls; the five line numbers in the Overview shift),
`docs_conformance.{enabled,mode}` and `token_optimization.{budgets,enforce}.
docs-conformance` keys, and a `cli.py docs-conformance-config --config
.claude/skills/refinement/config.yaml` reader used by the command and by the
`docs-conformance-gate` node. That reader stays on the clone-side file (scoped out
above). No new key joins the `run-config.env` whitelist.

---

## Architecture / Approach

**Chosen approach: widen `effective_config.py` in place; do not fork into `cli.py`.**
Unchanged from the prior pass's conclusion — `effective_config.py` is a clean
single-purpose `python -m` module (`resolve()`→`materialize()`→fail-open `main()`);
`cli.py` is a stateful multi-subcommand multiplexer for per-run *operations* whose
invocation contract assumes a clone already exists, which `scheduler.sh`'s pre-clone
call site doesn't have.

Interface changes to `effective_config.py`:
- `--clone-dir` default changes from today's `os.environ.get("CLONE_DIR", ".")` to
  `None`. `None` is scheduler mode (first existing of the three candidate files,
  whole-file, no clone, no adapter — Requirement 2; not "baked-only"). A concrete
  `--clone-dir` is run mode (entrypoint.sh). Today's `"."` default was unsafe for a
  scheduler-mode caller — it would probe `./.claude/skills/refinement/config.yaml` and
  `./.factory/adapter.yaml` relative to the daemon's cwd.
- Add an env-file emitter (`--emit-env`) alongside a JSON emitter, both driven by the
  same shared writer (run mode: one `resolve()` call; scheduler mode: the first-match
  file).
- `scheduler.sh` and `entrypoint.sh` both shrink to: invoke the module → get
  `run-config.{json,env}` → run one shared generic env-wins loop. The scheduler
  exits 1 if that produces nothing (Requirement 4a); the entrypoint keeps its
  bootstrap defaults (Requirement 4b).
- `budget_gate.sh` and `architecture_slice.py` switch their own inline/standalone YAML
  reads to reading `$ARTIFACTS_DIR/run-config.json`. Without that file,
  `budget_gate.sh` falls back to observe (Requirement 4c) and `architecture_slice.py`
  (e.g. its standalone CLI mode) falls back to the run-mode resolver (Requirement
  4d).

**Data flow (after):**
```
Run mode (entrypoint.sh, per run):
  config/config.yaml (baked)
    ← .factory/adapter.yaml token_optimization block (adapter; unchanged scope)
    (a clone that commits .claude/skills/refinement/config.yaml: that file alone)
         │
         ▼
  effective_config.resolve() ──┬──► <clone>/.claude/skills/refinement/config.yaml
                               │      (unchanged; still read by the scoped-out readers)
                               ├──► $ARTIFACTS_DIR/run-config.json  (budget_gate.sh,
                               │                                      architecture_slice.py)
                               └──► $ARTIFACTS_DIR/run-config.env   (entrypoint knobs)

Scheduler mode (scheduler.sh, at startup):
  first existing of /workspace/project/config/config.yaml,
  /opt/dark-factory/config/config.yaml, /opt/refinement-skills/config.yaml
  (whole-file, no merge) ──► $SCHEDULER_STATE_DIR/run-config.env  (read_config loop)
```
The five migrated readers read one of the new artifacts; the readers listed in
Requirement 2 keep the clone-side file. `epic_autopilot.py`'s fallback uses the
first-match resolver directly (Requirement 5).
Untouched by this flow: `.factory/adapter.yaml`'s `safety.*` block,
`entrypoint.sh::_compute_side_effect_level()`'s baked-only read, and
`gate_blast_radius.py::load_config` (Requirement 6).

---

## Alternatives Considered

- **JSON-only artifact.** Rejected: bash consumers would still fan out one
  `jq`/subprocess call per scalar knob — the pattern this ticket exists to kill.
- **Flat-env-only artifact.** Rejected: cannot cleanly carry nested maps or lists
  (`hard_exclude_paths`) that `epic_autopilot.py`/`architecture_slice.py` need
  structured.
- **Fold config materialization into `cli.py`'s multi-subcommand shape.** Rejected —
  different responsibility (stateless resolution vs. stateful per-run ops), and
  `cli.py`'s invocation contract assumes a clone that doesn't exist yet at
  `scheduler.sh`'s call site.
- **Stop writing the clone-side materialized file and migrate every reader onto
  `run-config.json` in this ticket.** Rejected for this ticket (operator choice, see
  "Owner-reversible choice"): about ten more readers, several of them gate inputs
  whose fallbacks are silent (`|| echo 30`, hardcoded defaults, an agent told to read
  a missing file). Keeping the file makes this refactor additive on the run side.
- **Bundle the `epic_autopilot.py` `hard_exclude_paths` precedence fix (#205) into this
  ticket.** Rejected, now three times (2026-07-07, 2026-07-23, this pass): latent
  safety-adjacent behavior change, already filed and open as #205, bundling risks this
  refactor being reviewed structurally instead of the safety fix on its own merits.
- **Absorb `.factory/adapter.yaml`'s `safety.*` block, or `_compute_side_effect_level()`'s
  baked-only read, into `effective_config`.** Rejected: both are deliberately separate,
  fail-closed trust boundaries; merging either into the general config-merge path would
  be a safety regression disguised as a mechanism cleanup.
- **Add a dedicated rollback/legacy-mode kill-switch.** Rejected: fail-open on the
  run side and fail-closed scheduler startup already cover the error case; a second untested code path for "wrong value, no error"
  doesn't help and rots. Equivalence/characterization golden tests cover this instead.
- **Add a manual MarketHawk-side verification step to this ticket, gating its merge.**
  Rejected: MarketHawk no longer commits a clone-side copy, so it takes the same
  `materialize()` branch as the self target, and nothing is deleted from either repo.
  Requirement 8's equality test runs with MarketHawk's adapter shape
  (`token_optimization.enforce.implement: true`), and the acceptance matrix states the
  per-instance source for every safety key.

---

## Open Questions (non-blocking)

- Should `architecture_slice.py`'s migration (Requirement 4d) happen in this ticket or
  a same-epic follow-up? It's a same-shaped mechanical collapse with no behavior
  change, sized well within this ticket's already-large mechanical surface; flag for
  descoping if implementation finds it materially larger than expected.
- Exact `run-config.env`/`run-config.json` file names/paths are a reasonable default
  following existing `$ARTIFACTS_DIR`/`$SCHEDULER_STATE_DIR` conventions, not dictated
  by the issue; the plan phase may adjust naming as long as the two-call-site/one-
  shared-writer shape holds.

## Assumptions (flagged)

- "One materialized run-config artifact... per run" is read as one artifact *shape*
  from one shared implementation, at two call sites with different input layers — not
  one physical file globally, since the scheduler daemon structurally predates any
  per-issue clone. (Carried forward unchanged from the original 2026-07-07 draft.)
- No new config knobs are introduced by this refactor; the whitelist in `run-config.env`
  is scoped to knobs already consumed today by the six existing mechanisms.
- All figures in this document were re-derived directly against `main`@`f457c2c`
  (2026-11-17) via `git show main:<path>` / `git diff HEAD main -- <path>`, not trusted
  from the stale on-branch checkout (which sat at `main`@`cf5858d`, 2026-07-22, 463
  commits behind) or from the superseded 2026-07-23 spec. The plan phase should
  re-confirm line numbers against whatever `main` commit it actually branches from,
  since further drift is possible between refine and plan.
