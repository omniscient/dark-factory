# Widen `effective_config` to every knob — one config module (#180)

**Issue:** #180 · **Status:** spec-pending-review

## Overview

`config/config.yaml` (155 lines, ~15 top-level knob families) is read through five
independent mechanisms today, not four:

1. `scheduler.sh:29-96` `read_config()` — resolves a config path via a 3-way search
   (`/workspace/project/config/config.yaml` → `/opt/dark-factory/config/config.yaml` →
   `/opt/refinement-skills/config.yaml`), then calls a `_set_cfg VAR '.yq.expr'` helper
   ~28 times, one hand-maintained line per knob, each shelling out to `yq`.
2. `entrypoint.sh:38-64` `_entrypoint_cfg_apply()` — a second, narrower `yq` reader
   pointed at a *different* path (`${CLONE_DIR}/.claude/skills/refinement/config.yaml`),
   reading only 2 pre-clone bootstrap knobs (`FACTORY_WIP_LIMIT`,
   `CONFLICT_RESOLUTION_AI_TIER`).
3. `scripts/factory_core/effective_config.py` — the one real implementation
   (`resolve()`/`materialize()`, baked ← clone ← adapter deep-merge), but scoped to the
   `token_optimization` block only (`TARGET_TUNABLE_BLOCKS = ("token_optimization",)`),
   invoked once via CLI at `entrypoint.sh:532`.
4. `workflows/archon-dark-factory.yaml:373,420,529,927,1135` — 5 near-identical inline
   `python3 -c "import yaml; ..."` one-liners, each preceded by
   `_CFG="${_CLONE}/.claude/skills/refinement/config.yaml"`, re-parsing the exact file
   `effective_config.py` just materialized, to pull 3 values into a bash
   `read -r _EB _ES _BUD < <(...)` triple per budget-gate DAG node.
5. `scripts/factory_core/epic_autopilot.py:366-388` `_load_exclude_paths()` — a third
   reader for `hard_exclude_paths`, stacking `adapter.get()` → a **live** `yq` subprocess
   over a 4-path `_CONFIG_PATHS` search → a hardcoded Python fallback. (A sibling function,
   `_hard_exclude_paths()` at line 331, duplicates the adapter-only half of this and has
   zero callers — dead code.)

A sixth, previously-undocumented reader was found during refinement:
`scripts/architecture_slice.py:119-146` (`_load_config()`) carries its own independent
4-path `_CONFIG_PATHS` search and raw-dict config parsing for
`epic_autopilot.sensitive_keywords`/`hard_exclude_paths` and the
`token_optimization.architecture.*` knobs — a Python gate consumer that the issue's
"Python gates" language already covers in spirit even though it isn't named by file.

Consequence: adding one knob is a 3-file edit (`config.yaml` → a `_set_cfg` line →
a consumer's `os.environ.get`), and the merge/override rules are reimplemented five
different ways with five different bugs waiting to diverge. This ticket widens
`effective_config.resolve()`/`materialize()` into the single interface for all ~58 knobs
and retires the other four readers (plus the newly-found sixth) in favor of it.

## Requirements

Distilled from the issue's acceptance criteria and the brainstorming Q&A below.

1. **`resolve()` covers the full config tree**, not just `token_optimization`. The
   baked ← clone ← adapter deep-merge (`effective_config.py:56-73`) runs over the whole
   parsed `config/config.yaml` document, keeping the existing `TARGET_TUNABLE_BLOCKS`
   mechanism as the (now larger) set of top-level blocks an adapter is allowed to
   override — the specific block list is an implementation decision at build time, not
   fixed by this spec, but must include at minimum every block currently read by
   `_set_cfg`/`_epcfg`.

2. **`materialize()` emits two artifacts per call, from one `resolve()`:**
   - `run-config.json` — canonical full merged tree (source of truth for
     structured/nested/list-valued knobs: `token_optimization.budgets.*`,
     `.enforce.*`, `epic_autopilot.hard_exclude_paths`/`sensitive_keywords`,
     `code_review.severity_order`, `blast_radius.*`, etc.).
   - `run-config.env` — a flat, `source`-able projection restricted to an explicit
     dotted-path → `ENV_VAR` whitelist covering exactly the ~30 scalars
     `scheduler.sh`'s `_set_cfg` and `entrypoint.sh`'s `_epcfg` currently export
     (including nested scalars like `token_optimization.architecture.enabled →
     TOKEN_OPTIMIZATION_ARCHITECTURE_ENABLED`). This whitelist is owned by
     `effective_config.py` alone — no consumer re-declares it.

3. **One `--materialize` CLI verb, two call sites, disambiguated by `--clone-dir`
   presence:**
   - `scheduler.sh` calls it with **no** `--clone-dir` (baked-only: no clone-level
     config layer, no adapter layer) before `read_config()` runs, writing artifacts to
     `$SCHEDULER_STATE_DIR`. Scheduler.sh has no `CLONE_DIR` concept — it is a
     long-running host daemon that pre-dates any per-issue clone.
   - `entrypoint.sh` calls it with `--clone-dir "$CLONE_DIR"` (full baked ← clone ←
     adapter merge) at its existing call site (`entrypoint.sh:532`), writing artifacts
     under `$CLONE_DIR/.claude/skills/refinement/`.
   - `--clone-dir` presence gates three things together: whether the adapter layer is
     merged, whether the clone-config layer is merged, and whether the materialized
     artifacts are git-excluded (there is no `.git` under `$SCHEDULER_STATE_DIR`, so
     that path is skipped there).
   - `effective_config.py`'s `--clone-dir` default changes from `"."`/`CLONE_DIR` env
     fallback to `None`, so absence is programmatically detectable rather than
     defaulting to the current directory.

4. **Delete dark-factory's own committed `.claude/skills/refinement/config.yaml`**
   (`git rm`). Today this file's mere presence makes `materialize()` write nothing and
   return "left in place" (the "clone file wins byte-identically" transition-period
   branch, `effective_config.py:105-116`) — meaning on the one repo the factory runs
   continuously, the merge this ticket widens would otherwise never execute. Keep the
   "clone file wins" *branch* in `effective_config.py` as general per-target capability
   — external cutover targets (documented in `README.md:70-73` and
   `docs/cutover-markethawk.md:31,164`) still rely on committing their own
   `.claude/skills/refinement/config.yaml` during their transition period. This is
   safe: dependency audit during refinement confirmed the ~12 existing readers
   (`budget_enforce.py`, `diff_rank.py`, `gate_blast_radius.py`, `architecture_slice.py`,
   `memory_retrieve.py`, `epic_autopilot.py`, the 5 workflow `_CFG=` sites, etc.) all
   resolve the clone-side **path** at runtime, not committed content, and
   `entrypoint.sh:532-533` materializes before `:536`'s `_entrypoint_cfg_apply` and
   everything downstream — so deletion is path-preserving.

5. **`scheduler.sh read_config()` loses its per-knob `yq` fan-out.** The 33 hand-written
   `_set_cfg VAR '.yq.expr'` lines and the `resolve_config_yaml`/`CONFIG_YAML_PATHS`
   search are deleted. `read_config()` survives as a function: it parses
   `$SCHEDULER_STATE_DIR/run-config.env` line-by-line into a side table (it must **not**
   be `source`d directly for variable assignment — see Requirement 7), then applies one
   generic loop over the whitelist keys present in the file, reusing the existing
   `_set_cfg` env-wins/drift-warning primitive (`scheduler.sh:44-59`) verbatim in
   spirit: if the shell env already has the var set, keep it and log a drift warning on
   mismatch; otherwise export the materialized value.

6. **`entrypoint.sh _entrypoint_cfg_apply()`'s second `yq` reader is deleted.** The
   pre-clone bootstrap defaults for `FACTORY_WIP_LIMIT`/`CONFLICT_RESOLUTION_AI_TIER`
   (`entrypoint.sh:32-34`, used by the pre-clone concurrency guard at `:96-108`, which
   runs *before* any clone or materialize call exists) are unchanged. Post-clone,
   `_entrypoint_cfg_apply()` reads the same `run-config.env`/`run-config.json` artifact
   `effective_config.py --materialize` just wrote, instead of independently re-resolving
   `.claude/skills/refinement/config.yaml` via its own `yq` call.

7. **Env-wins/drift-warning override semantics are preserved exactly**, applied in bash
   at artifact-read time, not baked into the generated artifact. A naive
   `source run-config.env` would clobber any operator-set env override before the
   drift-check logic ever runs — this is why Requirement 5 requires side-table parsing,
   not direct sourcing. This preserves the Tier-0 kill-switch behavior documented in the
   README and exercised by `tests/test_scheduler_*.sh`.

8. **`workflows/archon-dark-factory.yaml`'s 5 inline `python3 -c` readers are replaced
   by one shared, reused read pattern** (not 5 independent one-liners) — e.g. a single
   `python3 -m factory_core.effective_config --get token_optimization` (or a
   purpose-built budget-gate helper subcommand returning the existing
   `enforce_budgets enforce.<scenario> budgets.<scenario>` triple) invoked identically
   at all 5 gate nodes (refine/plan/implement/conformance/code-review), reading
   `run-config.json` rather than re-parsing the clone config.yaml.

9. **`epic_autopilot.py`'s `hard_exclude_paths` resolution — mechanism-collapse only,
   no behavior change in this ticket:**
   - Delete the dead `_hard_exclude_paths()` (line 331).
   - `_load_exclude_paths()` (line 366, the live path called at `:559`) loses its `yq`
     subprocess and `_CONFIG_PATHS` scan; the factory-owned
     `epic_autopilot.hard_exclude_paths` knob is read through the new
     `effective_config`-backed artifact instead.
   - **The current override precedence is preserved as a provable no-op**: if
     `.factory/adapter.yaml` sets `safety.hard_exclude_paths` (target-owned), it still
     fully shadows the factory-owned `epic_autopilot.hard_exclude_paths` list, exactly
     as today. This is pinned by a characterization test (Requirement 12) asserting the
     resolved exclude list for dark-factory's own repo is byte-identical before and
     after this refactor.
   - **Known bug, explicitly out of scope for #180:** during refinement, this shadowing
     was found to mean dark-factory's own self-improvement exclusions (`dark-factory/`,
     `.archon/`, `scheduler.sh`, `factory_core/`, trading/auth paths) are currently
     never applied on this repo, because `.factory/adapter.yaml` already sets
     `safety.hard_exclude_paths` to an unrelated conformance list
     (`deploy/instances/`, `.github/workflows/publish.yml`). This is currently latent —
     `config/config.yaml`'s `epic_autopilot.enabled` ships `false` — but must be fixed
     (union both lists explicitly, never silent override) in a follow-up issue **filed
     before this spec is approved and labeled as a blocking precondition on ever setting
     `epic_autopilot.enabled: true`**. See Open Questions.

10. **`architecture_slice.py`'s independent `_CONFIG_PATHS`/`_load_config()` (lines
    119-146) is swept onto the new materialized artifact** for the config keys it reads
    (`epic_autopilot.sensitive_keywords`/`hard_exclude_paths`,
    `token_optimization.architecture.*`) — it is a "Python gate" consumer per the
    issue's own framing, discovered during refinement, not named in the original issue
    body.

11. **`.factory/adapter.yaml`'s `safety.*` block is explicitly NOT absorbed into
    `effective_config`'s merge.** It remains its own trust-boundary system with its own
    loader (`adapter.py`) — target-repo-controlled input must not be folded into the
    factory's own materialized `run-config.json`, which is why Requirement 3's
    `--clone-dir`-gated adapter merge only ever pulls in the pre-existing
    `TARGET_TUNABLE_BLOCKS` set (widened per Requirement 1), never `safety.*`.

12. **Test coverage:**
    - `tests/test_effective_config.py` extended for: full-tree merge (not just
      `token_optimization`), dual-artifact `materialize()` output, the new `--out-dir`
      flag, and `--clone-dir=None` → baked-only mode.
    - **Equivalence golden test**: for the committed `config/config.yaml`, the new
      `run-config.env` whitelist output must be byte-identical (key→value set) to the
      current `_set_cfg`/`yq` output for all ~30 whitelisted scalars — this is the test
      that catches silent-drift, which fail-open error handling does not.
    - **Characterization golden test** for `epic_autopilot.py`'s resolved
      `hard_exclude_paths` on dark-factory's own repo, pinning the current (shadowed)
      precedence per Requirement 9 — the follow-up issue flips this golden when it adds
      the union fix.
    - Existing `tests/test_scheduler.sh` (`SCHEDULER_SOURCE_ONLY=1` sourcing pattern)
      stays green with no changes required in principle: `read_config()` is only called
      after the source-only guard (`scheduler.sh:739,744`), so its internals are
      invisible to that test-sourcing path, which already pre-exports every
      config-driven var per the existing issue #338 convention.
    - `scheduler.sh`'s parse of `run-config.env` must itself be fail-open: a
      missing/malformed/partial artifact falls back to built-in bootstrap defaults and
      the daemon keeps dispatching — never a hard stop.

13. **No dedicated rollback kill-switch** (e.g. an `EFFECTIVE_CONFIG_LEGACY_MODE` env
    var reverting to the old `yq` path) is introduced. See Alternatives Considered for
    why, and Requirement 12 for the equivalence/characterization tests that substitute
    for it.

14. **Docs touch-up**: `README.md` (the `token_optimization` config table row and the
    "transition period... when the target commits one, it wins byte-identically" prose
    at `README.md:70-73`) and `docs/cutover-markethawk.md` (lines 31, 164) are updated
    to describe the widened scope and dark-factory's own repo no longer carrying the
    committed duplicate.

## Architecture / Approach

### `scripts/factory_core/effective_config.py`

- Widen `TARGET_TUNABLE_BLOCKS` (or equivalent full-tree merge behavior) so `resolve()`
  deep-merges the entire config document, not just `token_optimization`.
- Add a dotted-path → `ENV_VAR` whitelist table (ported from `scheduler.sh`'s 33
  `_set_cfg` lines and `entrypoint.sh`'s 2 `_epcfg` lines) as a module-level constant,
  the single owner of that mapping.
- `materialize(clone_dir, baked_path, out_dir)`:
  - Computes `merged, sources = resolve(clone_dir, baked_path)` (clone_dir may be
    `None` → baked-only).
  - Writes `out_dir/run-config.json` (full merged tree).
  - Writes `out_dir/run-config.env` (whitelist projection, `KEY=value` lines).
  - If `clone_dir` is not `None`: git-excludes both new artifacts the same way the
    existing clone `config.yaml` write is excluded today (`_git_exclude`), and retains
    the existing "committed clone `.claude/skills/refinement/config.yaml` wins,
    write nothing" branch for external targets that still commit one.
  - If `clone_dir` is `None`: no git-exclude step (no `.git` at
    `$SCHEDULER_STATE_DIR`).
- CLI (`main()`): `--materialize` gains a required `--out-dir`; `--clone-dir` default
  changes to `None`. `--print`/`--baked` behavior unchanged. Stays fail-open
  (`except Exception: ... sys.exit(0)`).

### `scheduler.sh`

- Before `read_config()` in the main exec path (still gated by the
  `SCHEDULER_SOURCE_ONLY` guard per the existing #338 pattern), invoke
  `python3 -m factory_core.effective_config --materialize --out-dir "$SCHEDULER_STATE_DIR" || true`.
- `read_config()`: delete `resolve_config_yaml`/`CONFIG_YAML_PATHS`/the 33 `_set_cfg`
  call-site lines. Parse `$SCHEDULER_STATE_DIR/run-config.env` (line-by-line, never
  `source`d) into a side table; loop over its keys applying the existing env-wins/
  drift-warning check generically. Fail-open: missing/malformed file → keep whatever
  bootstrap/env defaults are already in scope, log a warning, continue.

### `entrypoint.sh`

- Bootstrap defaults (`FACTORY_WIP_LIMIT`, `CONFLICT_RESOLUTION_AI_TIER`, lines 32-34)
  and the pre-clone concurrency guard (lines 96-108) are unchanged — they run before
  any clone exists.
- Line 532's materialize call gains `--out-dir "$CLONE_DIR/.claude/skills/refinement"`.
- `_entrypoint_cfg_apply()` (lines 38-64) is rewritten to read the materialized
  `run-config.env`/`run-config.json` instead of independently `yq`-reading
  `.claude/skills/refinement/config.yaml`; its own env-wins/drift-warning logging is
  preserved.

### `workflows/archon-dark-factory.yaml`

- The 5 gate nodes (`_CFG=...` + inline `python3 -c` at lines 373/420/529/927/1135)
  are replaced with one shared invocation pattern reading `run-config.json` (or a
  purpose-built `--get`/budget-gate CLI subcommand on `effective_config.py`), reused
  identically across all 5 nodes rather than copy-pasted per scenario name.

### `scripts/factory_core/epic_autopilot.py`

- Delete dead `_hard_exclude_paths()` (line 331).
- `_load_exclude_paths()` (line 366): delete the `yq subprocess`/`_CONFIG_PATHS` scan;
  read the factory-owned `epic_autopilot.hard_exclude_paths` via the materialized
  artifact. Precedence vs. `adapter.get("safety.hard_exclude_paths")` is unchanged
  (adapter still shadows factory list when present) — pinned by the characterization
  test in Requirement 12.

### `scripts/architecture_slice.py`

- `_load_config()` (lines 119-146) and its private `_CONFIG_PATHS` are replaced with a
  read of the same materialized artifact used elsewhere, for the
  `epic_autopilot.sensitive_keywords`/`hard_exclude_paths` and
  `token_optimization.architecture.*` values it currently reads independently.

### Deletions

- `git rm .claude/skills/refinement/config.yaml` (dark-factory's own committed copy).
- `resolve_config_yaml`/`CONFIG_YAML_PATHS` in `scheduler.sh`.
- The second `yq` reader in `entrypoint.sh`'s `_entrypoint_cfg_apply()`.
- The 5 divergent inline `python3 -c` blocks in the workflow YAML.
- `_hard_exclude_paths()` and the `yq`/`_CONFIG_PATHS` half of `_load_exclude_paths()`
  in `epic_autopilot.py`.
- `_CONFIG_PATHS`/`_load_config()`'s independent parsing logic in
  `architecture_slice.py` (replaced, not left dangling).

## Alternatives considered

1. **JSON-only materialized artifact, bash consumers call a `--get <path>` helper per
   value.** Rejected — this swaps 33 `yq` subprocess spawns for 33 Python interpreter
   spawns per scheduler read, strictly worse than today and against the issue's own
   intent to "kill the per-knob fan-out." Chosen approach: dual artifact (JSON +
   pre-projected flat env file) from one `resolve()` call.

2. **Flat env file only, no JSON artifact.** Rejected — several knobs consumed by
   non-bash callers are nested or list-valued (`token_optimization.budgets.*`,
   `epic_autopilot.hard_exclude_paths`, `code_review.severity_order`) and cannot be
   faithfully flattened. Chosen approach: JSON as canonical source of truth, flat env
   as a bash-only projection of an explicit whitelist.

3. **Two separate CLI subcommands** (`--materialize-scheduler` vs. `--materialize-run`)
   for the two call sites, reflecting their different lifecycles (long-running host
   daemon vs. per-container). Rejected — the module doesn't care who calls it or how
   long they live, only which config layers are present; forking the verb duplicates
   arg-wiring and diverges from the module's existing single-purpose `-m` CLI shape
   (`--materialize`/`--print` as one mutually-exclusive action group). Chosen approach:
   one `--materialize` verb; `--clone-dir` presence (default changed to `None`)
   disambiguates baked-only vs. merged and gates git-exclusion.

4. **Fully delete `read_config()`/`_set_cfg` and have `scheduler.sh` directly
   `source run-config.env`.** Rejected — a direct `source` for assignment would
   clobber any pre-exported operator override before the existing env-wins/
   drift-warning check ever runs, silently breaking the Tier-0 kill-switch semantics
   the README documents and `test_scheduler_*.sh` exercises. Chosen approach: parse
   the artifact into a side table, keep the env-wins/drift-warning loop in bash, just
   collapse it from 33 hardcoded call sites to one generic loop over the artifact's
   keys.

5. **Leave the clone-committed `.claude/skills/refinement/config.yaml` in place** and
   scope this ticket strictly to "add missing knobs to the merge + swap 4 readers,"
   without touching the duplicate file. Rejected — its mere presence makes
   `materialize()` skip the merge entirely on dark-factory's own repo (the "clone file
   wins byte-identically" branch), so all the widened merge logic this ticket adds
   would be dead code on the one target the factory runs continuously, exercised only
   in unit tests. Chosen approach: delete dark-factory's own copy; keep the branch as
   general per-target capability for external cutover targets still mid-transition.

6. **Bundle the `epic_autopilot.py` `hard_exclude_paths` union-vs-override behavior fix
   into #180**, since the shadowing bug was discovered while researching this exact
   code path. Rejected for THIS ticket — the bug is currently latent
   (`epic_autopilot.enabled: false` ships by default), and burying a safety-semantics
   behavior change inside an already wide-blast-radius mechanical refactor (touching
   the daemon dispatcher, every container's entrypoint, and 5 DAG nodes) makes it the
   one change in the PR a reviewer must reason about behaviorally rather than
   structurally — exactly the kind of change most likely to be waved through under
   "it's just the config refactor." Chosen approach: mechanism-collapse only in #180
   (kill the `yq` fallback, route the factory-owned knob through `effective_config`,
   delete dead code), pinned by a characterization test; file the union fix as a
   separate, explicitly-linked follow-up gated as a blocking precondition on ever
   enabling `epic_autopilot`.

7. **Add an `EFFECTIVE_CONFIG_LEGACY_MODE` kill-switch env var** to revert
   `scheduler.sh` to the old `yq`-based `read_config()` if the new artifact-based path
   misbehaves, given `scheduler.sh` is single-point-of-failure infrastructure for the
   whole factory. Rejected — fail-open error handling only guards against
   `effective_config` *erroring*, not against it *returning a wrong-but-valid value*
   (silent drift), so a legacy-mode flag would guard a different risk than it appears
   to, while adding a second, rarely-exercised code path that bit-rots. This ticket's
   revert path (`git revert` + redeploy) is as cheap as the precedent set by the
   docker-socket-proxy topology split (also revert-by-redeploy, no runtime flag) rather
   than #732's `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS` (which guards a stateful
   enforcement policy operators need to flip without a redeploy). Additionally, this
   ticket (size L + `refactor` keyword) already self-classifies into the
   `dispatch_ceiling` policy's Blocked/human-pairing gate
   (`config/config.yaml:63-70`), which supplies exactly the staged-rollout safety net a
   kill-switch would otherwise provide. Chosen approach: no kill-switch; substitute an
   equivalence golden test (catches silent drift), a fail-open parse in
   `scheduler.sh` (daemon never hard-stops on a bad artifact), and the existing
   dispatch-ceiling human-pairing gate.

## Brainstorming Q&A

> **Q:** The issue says the materialized artifact should be "JSON and/or a sourceable
> env file" — an open choice, not a decision. Should `materialize()` produce JSON only,
> a flat env file only, or both? Which knobs need a flat env-var projection vs. can stay
> JSON-only?
> **A:** Both — `run-config.json` (canonical full tree) + `run-config.env` (an explicit
> dotted-path→ENV_VAR whitelist for the ~30 scalars bash consumers currently export,
> lifted from the existing `_set_cfg`/`_epcfg` hardcoded mapping, not a naive flatten of
> the whole tree). JSON alone means bash still fans out N subprocess calls per read,
> strictly worse than today; flat-env-only can't carry nested/list knobs
> (`token_optimization.budgets.*`, `epic_autopilot.hard_exclude_paths`,
> `code_review.severity_order`) that non-bash consumers need. Also flagged: the
> scheduler resolves config BEFORE any clone/adapter exists (no `CLONE_DIR` concept in
> `scheduler.sh`), so it needs its own baked-only materialization call, separate from
> the per-run `entrypoint.sh` materialization that includes the adapter merge — two
> materialization call sites feeding the same generator, not one shared artifact. And
> the env-wins/drift-warning override semantics (Tier-0 kill-switch, README-documented)
> must stay applied in bash at `source`/read time against the live host env, not baked
> into the generated file.

> **Q:** The clone-committed `.claude/skills/refinement/config.yaml` (near-duplicate of
> `config/config.yaml`) currently short-circuits `materialize()` — "clone file wins
> byte-identically, merge skipped" if present. Should this ticket delete that committed
> duplicate to force the merge to actually run in the reference loop, or leave it in
> place and scope narrowly to "add missing knobs + swap 4 readers to the new artifact"?
> **A:** Delete dark-factory's own committed copy (`git rm`) so the merge actually
> executes on the one repo the factory runs continuously — leaving it in place means
> all the new merge logic this ticket adds is dead code on the reference target. Keep
> the "clone file wins" branch in `effective_config.py` as general per-target
> capability (external cutover targets still document/rely on it per
> `README.md:70-73` and `docs/cutover-markethawk.md`) — just stop dark-factory's own
> repo from triggering it. Confirmed via dependency check: ~12 existing readers all
> resolve the clone-side PATH at runtime (not committed content), and `materialize()`
> writes to that same path before any reader runs — so deletion is path-preserving and
> safe. `tests/test_effective_config.py`'s git-exclude-write assertion actually gets
> exercised now instead of skipped. README/docs prose needs a matching touch-up.

> **Q:** Two things read `hard_exclude_paths`-shaped config but are NOT the same knob:
> `config.yaml`'s factory-owned `epic_autopilot.hard_exclude_paths` (self-improvement
> gating list) vs. `.factory/adapter.yaml`'s target-owned `safety.hard_exclude_paths`
> (conformance/blast-radius scope-guard list). Is the issue's "yq subprocess" claim
> stale, and should `effective_config` become the single call path for BOTH lists
> (disambiguated by dotted path), or should `.factory/adapter.yaml`'s `safety.*` block
> stay a fully separate trust-boundary system that `effective_config` should not
> absorb?
> **A:** The `yq` subprocess claim is accurate — it lives in `_load_exclude_paths()`
> (line 366, the live caller at `:559`), not the dead `_hard_exclude_paths()` (line
> 331) the question initially pointed at. Keep `.factory/adapter.yaml`'s `safety.*`
> block a fully separate trust-boundary system, NOT merged into `effective_config`'s
> baked←clone←adapter tree — different owner (target-controlled vs. factory-controlled),
> different trust posture. BUT verification surfaced a real live bug:
> `_load_exclude_paths()` returns adapter's `safety.hard_exclude_paths` whenever it's a
> non-None list, and ONLY falls through to the factory-owned
> `epic_autopilot.hard_exclude_paths` (via `yq`) if adapter doesn't set one. Since
> dark-factory's own `.factory/adapter.yaml` DOES set `safety.hard_exclude_paths` (to
> an unrelated conformance list), the self-improvement gate silently loses its own
> exclusions (`dark-factory/`, `.archon/`, `scheduler.sh`, `factory_core/`,
> trading/auth paths) entirely on this repo today. Recommended: collapse the mechanism
> (kill `yq`, route the factory-owned knob through `effective_config`, delete dead
> code) in #180; defer the union-vs-override behavior fix to a follow-up (see next
> Q&A and Open Questions) since the bug is currently latent behind
> `epic_autopilot.enabled: false`.

> **Q (Part A):** Given two materialization call sites (scheduler baked-only,
> entrypoint per-run merged) both need `run-config.json` + `run-config.env` from ONE
> `resolve()` implementation, what should the CLI/module interface look like — one
> `--materialize` flag disambiguated by `--clone-dir` presence, or two distinct
> flags/subcommands reflecting the different caller lifecycles? **Part B:** AC3 allows
> either fully deleting `scheduler.sh`'s `_set_cfg`/`read_config` fan-out or keeping
> `_set_cfg` as a thin wrapper backed by the new artifact instead of `yq` — which
> approach minimizes disruption to the existing `test_scheduler.sh` sourcing contract
> (issue #338 pattern) while still killing the per-knob `yq` fan-out?
> **A (Part A):** One `--materialize` verb; `--clone-dir` default changes from
> `"."`/env-fallback to `None` so its absence is genuinely detectable. Absent ⇒
> baked-only (scheduler, artifacts in `$SCHEDULER_STATE_DIR`, no git-exclude). Present
> ⇒ full merge (entrypoint, artifacts under `$CLONE_DIR/.claude/skills/refinement/`,
> git-excluded). This matches `effective_config.py`'s existing shape as a
> single-purpose `-m` module with a mutually-exclusive action group
> (`--materialize`/`--print`), rather than forking it into `factory_core/cli.py`'s
> `STATE_FILE`-op multiplexer style (`board-move`, `breaker-*`, etc.) which is a
> different, unrelated convention.
> **A (Part B):** Neither pure option — the #338 test contract is actually orthogonal
> here, since `read_config()` is only called after the `SCHEDULER_SOURCE_ONLY` guard
> (`scheduler.sh:739,744`) and every #338-pattern test pre-exports vars before sourcing,
> so tests never execute `read_config()`'s internals either way. The real constraint is
> env-wins: a naive `source run-config.env` would clobber pre-exported overrides before
> the drift-check ever runs. So: delete the 33 `_set_cfg`/`yq` call-site lines and the
> `yq` dependency, but keep `read_config()` as a function that parses the artifact into
> a side table (never directly sourced) and applies the existing env-wins/drift-warning
> primitive generically over the whitelist keys present in the file. The whitelist has
> one owner (`effective_config.py`) — `scheduler.sh` must not re-declare it as a bash
> array, or the fan-out has just moved, not been eliminated.

> **Q:** This ticket touches `scheduler.sh` (the daemon dispatching every other run),
> `entrypoint.sh` (every dispatched container), 5 workflow DAG nodes, and (per the
> `hard_exclude_paths` finding) a candidate behavior fix in `epic_autopilot.py` — wide
> blast radius for one PR, and it will self-classify into the `dispatch_ceiling`
> Blocked/human-pairing gate anyway (size L + `refactor` keyword match). Should the
> `hard_exclude_paths` union-vs-override fix be bundled into #180's scope, or split
> into a separate follow-up? Should a dedicated rollback kill-switch
> (`EFFECTIVE_CONFIG_LEGACY_MODE`) be required given `scheduler.sh` is
> single-point-of-failure infrastructure?
> **A:** Split the union fix into a follow-up — verified `epic_autopilot.enabled: false`
> ships by default (`config/config.yaml:73`), so the shadowing bug currently has no
> live safety consequence; bundling a behavior/safety-semantics change into an
> already-wide mechanical-refactor PR risks it being reviewed structurally rather than
> behaviorally. #180 does the mechanism-collapse only and pins current (shadowed)
> behavior with a characterization test; the follow-up issue must be filed before this
> spec is approved and labeled as a blocking precondition on ever setting
> `epic_autopilot.enabled: true`. No dedicated kill-switch — verified
> `_set_cfg`'s existing fail-open convention (`scheduler.sh:48`,
> `yq ... 2>/dev/null || true`) only guards against errors, not silent wrong-value
> drift, so a legacy-mode flag would guard a different risk than it appears to while
> adding a second, untested, bit-rotting code path. This ticket's revert is as cheap as
> the docker-socket-proxy topology split precedent (revert-by-redeploy, no runtime
> flag), unlike #732's `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS` (which guards a stateful
> policy operators need to flip without a redeploy). Substitute: an equivalence golden
> test (`run-config.env` output byte-identical to today's `_set_cfg`/`yq` output for all
> whitelisted scalars — catches silent drift, which fail-open cannot), a fail-open
> parse in `scheduler.sh` (bad/missing artifact → daemon falls back to bootstrap
> defaults and keeps dispatching, never hard-stops), and the existing dispatch-ceiling
> human-pairing gate this ticket already triggers by policy.

## Open questions (non-blocking)

- The exact top-level block list added to `TARGET_TUNABLE_BLOCKS` (Requirement 1) is
  left to the implementer — it must be a superset sufficient to cover every knob
  currently exported by `_set_cfg`/`_epcfg`, but the spec does not mandate every single
  `config.yaml` block become adapter-overridable (e.g. it's unclear any target should
  be able to override `main_red_autofix.*` via adapter) — implementer's judgment,
  informed by which blocks the issue's "~58 knobs" figure implies are in scope.
- Whether `workflows/archon-dark-factory.yaml`'s shared read (Requirement 8) is a new
  `effective_config.py` subcommand (e.g. `--budget-gate <scenario>`) or a generic
  `--get <dotted.path>` is left to the implementer; either satisfies "one shared
  pattern, not 5 divergent one-liners."
- **The `epic_autopilot.py` `hard_exclude_paths` union-vs-override behavior fix
  (Requirement 9's deferred item) must be filed as a follow-up GitHub issue before this
  spec moves to Refined**, labeled as blocking `epic_autopilot.enabled: true`. Filing
  that issue is a Phase 6 publish-time action, not part of this ticket's own
  acceptance criteria.
- Whether the `yq` binary can be fully dropped from the Dockerfile once `scheduler.sh`
  and `entrypoint.sh` no longer call it directly is left open — `epic_autopilot.py`'s
  removal (Requirement 9) also drops its last live call site, so this may be a free
  cleanup, but no other repo dependency on `yq` was audited as part of this spec.

## Assumptions (flagged)

- **[ASSUMPTION]** `docs/superpowers/specs/` does not exist in the current working
  tree (prior specs live only in git history on their own refine branches); this spec
  is written as if that directory is freshly created, consistent with how the three
  most recent prior specs (`d53e026`, `b97be7c`, `6ff98ad`) were each introduced on
  their own branch.
- **[ASSUMPTION]** "One materialized run-config artifact... per run" in the issue's
  Solution section is read as "one artifact **shape** (JSON + env projection),
  materialized from one shared `resolve()`/`materialize()` implementation, at
  potentially multiple call sites with different input layers" rather than "exactly one
  physical file, one call, globally" — the scheduler daemon structurally cannot share a
  single per-run artifact with per-issue dispatched containers, since it runs before
  any clone exists.
- **[ASSUMPTION]** The dotted-path→ENV_VAR whitelist (Requirement 2) is scoped to
  scalars currently consumed by `scheduler.sh`/`entrypoint.sh` only; no new knobs are
  added to the whitelist as part of this ticket unless already present in
  `config.yaml` today (this is a mechanism refactor, not a knob-inventory change).
