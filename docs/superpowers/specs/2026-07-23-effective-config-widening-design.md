# Widen `effective_config` to Every Knob — One Config Module

**Issue:** omniscient/dark-factory#180
**Status:** re-refined from current `main` on 2026-07-23 (prior 2026-07-07 spec's branch was
deleted; this replaces it — see Overview)
**Related, explicitly NOT this ticket's scope:** omniscient/dark-factory#205 (OPEN — fix
`epic_autopilot.py`'s `hard_exclude_paths` adapter-shadow precedence bug; this ticket only collapses
its *mechanism*, not its behavior)

---

## Overview / Problem Statement

`config/config.yaml` (158 lines, ~15 knob families) has no single reader. Six independent
mechanisms parse it today, re-verified against current `main`:

1. **`scheduler.sh:read_config()`** (lines 42–98) — 33 `_set_cfg` call sites (confirmed: `grep -c
   "_set_cfg " scheduler.sh` → 33), each a `yq` subprocess against a 3-path candidate search
   (`/workspace/project/config/config.yaml` → `/opt/dark-factory/config/config.yaml` →
   `/opt/refinement-skills/config.yaml`). Runs once, in the main exec path, **before any git clone
   exists** — the scheduler is a long-running host daemon, not a per-run container.
2. **`entrypoint.sh:_entrypoint_cfg_apply()`** (lines 40–69) — a second, narrower `yq` reader (5
   knobs: `FACTORY_WIP_LIMIT`, `CONFLICT_RESOLUTION_AI_TIER`, and 3 `session_window_*` knobs)
   pointed at a *different* path: `${CLONE_DIR}/.claude/skills/refinement/config.yaml`. Runs
   **after** clone.
3. **`scripts/factory_core/effective_config.py`** — the one real implementation: `resolve()`
   deep-merges baked `config/config.yaml` ← clone `.claude/skills/refinement/config.yaml` ←
   adapter-tunable blocks (`TARGET_TUNABLE_BLOCKS = ("token_optimization",)`). `materialize()`
   writes the merged tree back into the clone, but **only when the clone has no committed copy** —
   dark-factory's own repo always commits `.claude/skills/refinement/config.yaml` (2768 bytes,
   confirmed present, comment-stripped but semantically identical to `config/config.yaml`), so on
   this repo `materialize()`'s merge-and-write branch is dead code; the "clone file wins
   byte-identically" no-op branch is the only one ever exercised here. Called once, from
   `entrypoint.sh:564`.
4. **`workflows/archon-dark-factory.yaml`** — 5 inline `python3 -c "import yaml; ..."` one-liners
   (the `enforce-budget-{refine,plan,implement,conformance,code-review}` nodes, confirmed at lines
   379, 426, 595, 993, 1211 on current `main` — line numbers moved from the issue body's stale
   373/420/529/927/1135 across commits since 2026-07-07), each independently re-parsing
   `${CLONE_DIR}/.claude/skills/refinement/config.yaml` for `token_optimization` fields that
   `effective_config` already materializes.
5. **`scripts/architecture_slice.py`** (lines 119–158) — a 6th independent reader: its own 3-path
   candidate list + `yaml.safe_load`, pulling `dispatch_ceiling.keywords`,
   `epic_autopilot.sensitive_keywords`, and `epic_autopilot.hard_exclude_paths`.
6. **`scripts/factory_core/epic_autopilot.py:_load_exclude_paths()`** (lines 367–389) — three
   stacked mechanisms for `hard_exclude_paths`: `adapter.get(clone_dir,
   "safety.hard_exclude_paths")` first, then a `yq` subprocess against a 4-path config.yaml
   candidate list, then a hardcoded `_DEFAULT_EXCLUDE` fallback.

**Consequence** (unchanged from the issue body, still true): adding one knob is a 3+ file edit
(`config.yaml` → a `_set_cfg`/`_epcfg` line → each consumer's own parse), and the merge/override
rules are reimplemented six slightly different ways.

**A live, related bug, confirmed still present and still out of scope:** `.factory/adapter.yaml`'s
`safety.hard_exclude_paths` (line 20, always set — lists `deploy/instances/`,
`.github/workflows/publish.yml`, etc.) makes `epic_autopilot.py`'s `adapter.get()` call always
return early, so `config.yaml`'s `epic_autopilot.hard_exclude_paths` (which lists factory-self
paths — `dark-factory/`, `.archon/`, `scheduler.sh`, `factory_core/`) is silently unreachable. This
is currently latent (`epic_autopilot.enabled: false`) and is tracked separately as **#205 (still
OPEN)** — this ticket does not touch the precedence, only the mechanism (see Requirements).

### Why this spec replaces the 2026-07-07 draft

The original refinement's branch was deleted; per the operator's re-refine instruction, every
figure above was re-derived from current `main` rather than trusted from the old issue body or
draft spec. Unlike sibling re-refinements (#181/#182), the headline premise here has **not** gone
stale — all six mechanisms, the 33-count, and the adapter-shadow bug are still live and unchanged
in substance (only line numbers shifted). The approach below matches the prior draft's conclusions
where re-verification confirmed them, and is grounded fresh throughout.

---

## Requirements

Derived from the issue's acceptance criteria plus this pass's Q&A (full dialogue in the PR/issue
comment, not reproduced here):

1. **`effective_config.resolve()` merges the full config tree** (baked ← clone ← adapter), not
   just `token_optimization` for provenance purposes. (It already returns the full merged dict
   today — `_deep_merge(merged, clone_cfg)` merges the whole clone file; the `TARGET_TUNABLE_BLOCKS`
   scoping only limits which blocks the *adapter* layer may override, which stays narrow by
   design — see Requirement 6.)
2. **`effective_config` gains two new materialized output shapes**, both derived from one
   `resolve()` call:
   - `run-config.json` — canonical, full merged tree (serves nested/list consumers: the 5
     `enforce-budget-*` workflow nodes, `epic_autopilot.py`'s `hard_exclude_paths` list,
     `architecture_slice.py`'s keyword/path reads).
   - `run-config.env` — a flat `dotted.path → ENV_VAR` whitelist projection covering the ~33
     scalars bash currently exports (`ABOVE_CEILING_KEYWORDS` is a single regex string, not a
     list — it belongs in the env whitelist; genuine lists like `hard_exclude_paths` stay
     JSON-only).
   Both come from the **same shared writer**, invoked at two call sites with different inputs:
   - `scheduler.sh` calls it pre-clone, baked-only (`--clone-dir` omitted/`None` — see
     Requirement 4b for why the default must change from today's `"."`).
   - `entrypoint.sh` calls it post-clone with the real `--clone-dir` (full baked ← clone ← adapter
     merge), replacing today's single `--materialize` YAML-into-clone call.
   Materialized artifacts land in `$ARTIFACTS_DIR` (entrypoint/workflow — the existing per-run
   hand-off directory already used for `context-budget.json`, `token-opt-caps.env`,
   `memory-context.md`) and `$SCHEDULER_STATE_DIR` (scheduler — mirrors the existing
   `scheduler-state.json`/`main-red-last-recheck` convention), not inside the git-tracked clone —
   this sidesteps git-exclude bookkeeping entirely for the *new* artifacts (unlike the legacy
   clone-committed YAML they replace).
3. **Delete dark-factory's own committed `.claude/skills/refinement/config.yaml`.** Verified
   path-preserving: ~12 existing readers resolve the clone-side path at runtime, not committed
   content, and `entrypoint.sh:564` already runs `--materialize` *before* `_entrypoint_cfg_apply()`
   at `:568`, so ordering already works. This turns `materialize()`'s merge-and-write branch from
   dead code into the one this repo actually exercises. Required guardrails, both in-scope for this
   ticket (mechanical consequences of the deletion, not separate policy changes — deleting the file
   changes only *where config is delivered from at runtime*, not any `gate_*`/breaker/budget
   *value*):
   - Update `README.md:57-72`, `README.md:174`, and `docs/cutover-markethawk.md:31,164` — they
     currently document dark-factory itself as being in the "transition period (clone file wins)"
     state; flip that language for dark-factory while **preserving the "clone file wins" branch
     unchanged in code** for external targets (MarketHawk) still mid-transition.
   - Test materialized *semantic* equivalence (every knob resolves to the same value), not byte
     equality — the committed copy is already comment-stripped and byte-different from
     `config/config.yaml`.
   - Preserve the existing fail-open contract (`materialize()`/`main()` already exit 0 on any
     error) so a missing file plus a materialize error never leaves a reader with no config.
4. **Collapse the redundant readers into consumers of the new artifacts:**
   a. `scheduler.sh`'s 33 `_set_cfg`/`yq` call sites (lines 46–96) collapse into one generic loop
      that parses `run-config.env` into a side table and replays `_set_cfg`'s exact semantics —
      **not** a naive `source` (that would clobber pre-set operator env-override vars and skip the
      drift-warning log). Preserve: env-wins-over-config, the `[config] VAR=... (env override;
      config has '...')` log line, and null-as-empty. `resolve_config_yaml`'s 3-path search moves
      into the Python module (baked-only mode) and the shell function is retired.
   b. `entrypoint.sh:_entrypoint_cfg_apply()` is retired; its 5 knobs are covered by the same
      `run-config.env` whitelist and the same generic env-wins loop pattern as scheduler.sh (shared
      shell helper, not two separate re-implementations).
   c. The 5 inline `python3 -c "import yaml; ..."` workflow-YAML readers are replaced by one shared
      pattern reading `$ARTIFACTS_DIR/run-config.json` (e.g. via `jq`, consistent with the
      artifact's canonical/nested role) — collapsing 5 near-identical one-liners into 1 reused
      form.
   d. `scripts/architecture_slice.py`'s own config-loading (`_load_config`, lines 142–158) is
      replaced by reading the materialized `run-config.json` (or calling `effective_config.resolve`
      directly when invoked standalone/pre-materialization) instead of its own 3-path
      `yaml.safe_load`.
5. **`epic_autopilot.py`: mechanism-collapse only, behavior unchanged.** Route the `yq`-against-
   `_CONFIG_PATHS` fallback branch (lines 378–388 — the branch that is *already* never reached
   today because `adapter.get()` always wins) through `effective_config.resolve()` instead of its
   own subprocess call. Add a characterization test pinning the current (shadowed) precedence order
   exactly as-is: `adapter.get("safety.hard_exclude_paths")` still wins, still shadows
   `config.yaml`'s `epic_autopilot.hard_exclude_paths`, still falls back to `_DEFAULT_EXCLUDE` only
   if adapter is absent/invalid. The actual precedence fix stays entirely in **#205**; confirmed
   still OPEN, still the right split (verified via `gh issue view 205`).
6. **`.factory/adapter.yaml`'s `safety.*` block stays outside `effective_config`'s widened scope** —
   a separate trust boundary (human-in-the-loop surface per CLAUDE.md's hard limits and trusted-
   comment-channel restrictions), not a `config.yaml` knob. The issue title's "every knob" scopes to
   the `config/config.yaml` tree read through the six mechanisms above, not to adapter-owned safety
   config. The only adapter interaction this ticket touches is unchanged: `TARGET_TUNABLE_BLOCKS`
   stays `("token_optimization",)`.
7. **No dedicated rollback kill-switch.** Fail-open guards against errors, not silent wrong-value
   drift; a legacy-mode flag would add an untested, bit-rotting second code path. Substitute: an
   equivalence golden test (old readers vs. new artifact, catches silent drift), fail-open parsing
   preserved in `scheduler.sh`, and this ticket already self-triggers the `dispatch_ceiling`
   human-pairing gate (size L + `refactor` keyword in title) for human review before merge.
8. **Test coverage:** extend `tests/test_effective_config.py` (currently 12 tests covering
   `resolve`/`materialize`/CLI/drift-warning) for the widened JSON/env output and the two call-site
   modes; add/extend `scheduler.sh` and `entrypoint.sh` config tests to cover the new consumption
   path; add the `epic_autopilot.py` characterization test (Requirement 5) and a
   `run-config.{json,env}` vs. old-six-readers equivalence golden test (Requirement 7).

---

## Architecture / Approach

**Chosen approach: widen `effective_config.py` in place; do not fork into `cli.py`.**
`effective_config.py` is already a clean single-purpose `python -m` module
(`resolve()`→`materialize()`→fail-open `main()`). `cli.py` is a different animal: a stateful
multi-subcommand multiplexer for per-run *operations* (`session-window-check`, `run-record`,
`deconflict`, ...) whose invocation contract assumes a clone already exists — exactly what
`scheduler.sh`'s pre-clone call site doesn't have. Materializing config is not a stateful run-op;
keep it in its own module.

Interface changes to `effective_config.py`:
- `--clone-dir` default changes from today's `os.environ.get("CLONE_DIR", ".")` to `None`. A
  `None` clone-dir is baked-only mode (scheduler.sh: no clone/adapter layer, no git-exclusion
  concerns since output no longer lands inside the clone at all — see Requirement 2). A concrete
  `--clone-dir` is full-merge mode (entrypoint.sh). The `"."` default was actively unsafe for a
  future baked-only caller — it would probe `./.claude/skills/refinement/config.yaml` and
  `./.factory/adapter.yaml` relative to the daemon's cwd.
- Add an env-file emitter (`--emit-env`, sibling to the existing YAML materialize path) alongside
  a JSON emitter, both driven by the same `resolve()` call — see Requirement 2 for the exact
  shapes and destinations.
- `scheduler.sh` and `entrypoint.sh` both shrink to: invoke the module → get `run-config.{json,env}`
  → run one shared generic env-wins loop (a small shell helper, not duplicated).

**Data flow (after):**
```
config/config.yaml (baked)
  ← .claude/skills/refinement/config.yaml (clone; deleted for dark-factory itself,
    kept for external mid-transition targets per Requirement 3)
  ← .factory/adapter.yaml token_optimization block (adapter; unchanged scope)
       │
       ▼
effective_config.resolve()  ──┬──►  run-config.json   (scheduler/entrypoint/workflow/
                               │                        epic_autopilot/architecture_slice
                               └──►  run-config.env      all read one of these two, never
                                                          config.yaml directly)
```

---

## Alternatives Considered

- **JSON-only artifact.** Rejected: bash consumers (scheduler.sh, entrypoint.sh) would still fan
  out one `jq`/subprocess call per scalar knob — the exact per-knob subprocess pattern this ticket
  exists to kill.
- **Flat-env-only artifact.** Rejected: cannot cleanly carry nested maps (`token_optimization.
  enforce.<phase>`) or lists (`hard_exclude_paths`) that the workflow YAML and `epic_autopilot.py`
  need structured.
- **Fold config materialization into `cli.py`'s multi-subcommand shape.** Rejected — see
  Architecture/Approach: different responsibility (stateless resolution vs. stateful per-run ops),
  and `cli.py`'s invocation contract assumes a clone that doesn't exist yet at scheduler.sh's call
  site.
- **Leave dark-factory's committed `.claude/skills/refinement/config.yaml` in place, only widen
  `resolve()`/`materialize()`.** Rejected: the "widen to every knob, one config module" charter is
  best validated by exercising the merge-and-write path live on the one repo the factory runs
  against continuously, rather than shipping a consolidation that stays dead-tested on self and
  only takes effect on hypothetical future post-cleanup targets.
- **Bundle the `epic_autopilot.py` `hard_exclude_paths` precedence fix into this ticket.**
  Rejected, twice now (original 2026-07-07 pass and this re-verification): it's a latent
  safety-adjacent behavior change (CLAUDE.md: "gate changes get their own reviewed ticket"), it's
  already filed and open as #205, and bundling it risks this refactor being reviewed structurally
  instead of the safety fix being reviewed on its own merits.
- **Absorb `.factory/adapter.yaml`'s `safety.*` block into `effective_config`.** Rejected: it's a
  deliberately separate, human-in-the-loop trust boundary; merging it into the general config-merge
  path would be a safety regression disguised as a mechanism cleanup.
- **Add a dedicated rollback/legacy-mode kill-switch.** Rejected: fail-open already covers the
  error case; a second untested code path for "wrong value, no error" doesn't help and rots.
  Equivalence golden tests + the ticket's own `dispatch_ceiling` gate cover this instead.

---

## Open Questions (non-blocking)

- Should `architecture_slice.py`'s migration (Requirement 4d) happen in this ticket or a same-epic
  follow-up? It's a genuine 6th mechanism matching the issue's problem statement, but isn't named
  in the issue's original acceptance-criteria checklist. Included here as in-scope because it's a
  same-shaped mechanical collapse with no behavior change, sized well within this ticket's already-
  large mechanical surface; flag for descoping if implementation finds it materially larger than
  expected.
- Exact `run-config.env`/`run-config.json` file names/paths above are a reasonable default
  following existing `$ARTIFACTS_DIR`/`$SCHEDULER_STATE_DIR` conventions, not dictated by the issue;
  the plan phase may adjust naming as long as the two-call-site/one-shared-writer shape holds.

## Assumptions (flagged)

- "One materialized run-config artifact... per run" is read as one artifact *shape* from one
  shared implementation, at two call sites with different input layers (baked-only vs. full
  merge) — not one physical file globally, since the scheduler daemon structurally predates any
  per-issue clone. (Carried forward from the 2026-07-07 draft; still the correct reading.)
- No new config knobs are introduced by this refactor; the whitelist in `run-config.env` is scoped
  to knobs already consumed today by the six existing mechanisms.
- `docs/superpowers/specs/` contains no prior spec for #180 on `main` (the 2026-07-07 draft's
  branch was deleted per the operator's re-refine instruction) — this file is authored fresh.
