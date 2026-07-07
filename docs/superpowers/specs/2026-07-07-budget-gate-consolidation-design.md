# Collapse the 5x enforce-budget nodes into `budget_gate.sh <scenario>` (#183)

**Issue:** #183 · **Status:** spec-pending-review

## Overview

`workflows/archon-dark-factory.yaml` repeats the `budget-*` → `enforce-budget-*` node
pair five times (10 nodes total) — once per scenario (`refine`, `plan`, `implement`,
`conformance`, `code-review`). The five `enforce-budget-*` bodies are byte-identical
except for the scenario string substituted in two spots (`to.get('enforce',{}).get(
'<scenario>',...)` and `to.get('budgets',{}).get('<scenario>',...)`), confirmed by
direct comparison of all five blocks (lines 367-384, 414-431, 523-540, 921-938,
1129-1146). Each block re-implements, inline, in YAML:

- a single-line `python3 -c "import yaml; ..."` read of `token_optimization.{
  enforce_budgets, enforce.<scenario>, budgets.<scenario>, default_budget_tokens}`
  from `.claude/skills/refinement/config.yaml`, falling back to `"false false 30000"`
  on any parse error,
- the `enforce_budgets && enforce.<scenario>` → `_MODE=enforce`, else `_MODE=observe`
  decision,
- the `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS` kill-switch override (`false`/`0`/`no` forces
  `_MODE=observe`; nothing else can force it on),
- the `budget_enforce.py` invocation, redirecting stdout to
  `$ARTIFACTS_DIR/token-opt-caps.env` (truncating stale caps),
- a blanket `(...) || true` so the whole block can never block the DAG.

The comment at workflow lines 364-366 claims "the Archon runner does not support
calling external scripts from YAML bash nodes" — this is false today: the very same
blocks already invoke `dark-factory/scripts/budget_enforce.py`. The seam was declined,
not blocked, and this ticket closes it: a single `budget_gate.sh <scenario>` owns the
config read, mode decision, override handling, and exit-code semantics, and each
`enforce-budget-*` node collapses to a one-line adapter.

## Requirements

Distilled from the issue's acceptance criteria and the brainstorming Q&A below.

1. **This is a refactor, not a behavior change.** `budget_gate.sh` must reproduce the
   current fail-open DAG semantics exactly: the workflow adapter node keeps
   `bash dark-factory/scripts/budget_gate.sh <scenario> || true`, so no config error,
   parse failure, or `budget_enforce.py` crash can newly block the pipeline (Q&A #1).
   Do not change enforcement from advisory to blocking as part of this ticket.

2. **`budget_gate.sh` uses meaningful, documented exit codes for its own
   testability** — not to change DAG semantics (Q&A #1):
   - `0` — the gate ran to completion and wrote `token-opt-caps.env` (covers both
     `observe` and `enforce` mode, and both `over_budget=true` and `over_budget=false`
     outcomes — over-budget is a valid computed result, not an error, matching
     `budget_enforce.py`'s own behavior of never treating it as fatal).
   - `1` — a hard failure in `budget_enforce.py` itself (today: a missing or
     unparseable `--context-budget-json`, `budget_enforce.py`'s existing `sys.exit(1)`
     path). A malformed or missing `config.yaml` is **not** in this category — that
     already falls back silently to `enforce_budgets=false enforce=false budget=30000`
     (observe mode), matching current inline behavior, and must keep doing so.

3. **Kill-switch semantics are preserved verbatim and covered by the new test**
   (issue AC #3): `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS` is kill-only — `false`/`0`/`no`
   (case-insensitive) forces `_MODE=observe` regardless of config; unset or any other
   value defers to the config-derived mode; it can never force enforcement ON.

4. **`budget_gate.sh` self-locates its sibling script and resolves the config path
   without relying on `$CLONE_DIR`/CWD assumptions** (Q&A #2), following the existing
   convention in `dark-factory/scripts/load_memory_context.sh` (`python3
   "$(dirname "${BASH_SOURCE[0]}")/memory_retrieve.py"`):
   - `budget_enforce.py` is a sibling in the same directory: locate it via
     `dirname "${BASH_SOURCE[0]}"`.
   - `config.yaml` is clone-root-relative (`.claude/skills/refinement/config.yaml`),
     two directories above the script (`dark-factory/scripts/budget_gate.sh` →
     `../../` → clone root). Resolve this relative to the script's own directory, not
     `$CLONE_DIR` (documented in the workflow as "NOT exported to bash nodes") or a
     bare CWD assumption.
   - This path is also the effective-config materialization target
     (`scripts/factory_core/effective_config.py`'s `_CLONE_REL`): during the current
     transition period the clone's `config.yaml` wins byte-identically and nothing is
     rewritten; post-cleanup, `effective_config.materialize()` writes the merged
     config to this exact same path. `budget_gate.sh` reading this path is therefore
     already forward-compatible with the "config-module ticket" the issue flags as a
     possible follow-on — no additional integration work is needed now or later.

5. **File location**: `budget_gate.sh` is added at `scripts/budget_gate.sh` in this
   repo (baked into the image at `/opt/dark-factory/scripts/budget_gate.sh` per the
   `Dockerfile`'s `COPY scripts/ /opt/dark-factory/scripts/`). No `entrypoint.sh`
   change is needed — its existing directory-level materialization
   (`cp -r /opt/dark-factory/scripts "$CLONE_DIR/dark-factory/scripts"`, only when
   `$CLONE_DIR/dark-factory/scripts` doesn't already exist) picks up the new file
   automatically, landing it at `$CLONE_DIR/dark-factory/scripts/budget_gate.sh` —
   the exact path the workflow nodes invoke, matching the existing
   `dark-factory/scripts/budget_enforce.py` reference in the same blocks.

6. **All 5 `enforce-budget-*` nodes become one-line invocations** (issue AC #2):
   ```yaml
   - id: enforce-budget-refine
     bash: |
       bash dark-factory/scripts/budget_gate.sh refine || true
     depends_on: [budget-refine]
     when: "$parse-intent.output.intent == 'refine'"
     timeout: 30000
   ```
   No inline `python3 -c` config reads remain in any of the five. The stale
   "runner does not support calling external scripts" comment (lines 364-366) is
   deleted as part of this change.

7. **Existing test coverage is triaged, not left to bit-rot** (Q&A #2). In
   `tests/test_budget_enforce_dag.py`:
   - **Unchanged (still valid against one-line adapter nodes):**
     `test_enforce_budget_node_exists`, `..._depends_on_budget`,
     `..._no_trigger_rule`, `..._timeout`, all `..._when` tests,
     `test_command_node_depends_on_enforce_not_budget`,
     `test_implement_retains_trigger_rule`, `test_dag_validator_passes`, and all
     `test_config_*` tests (config shape is untouched by this ticket).
   - **Rewritten** (content moved into `budget_gate.sh`, so string-matching the YAML
     bash no longer applies):
     - `test_enforce_budget_node_nonfatal` (T3-D4) — assert the one-line bash is
       exactly `bash dark-factory/scripts/budget_gate.sh <scenario> || true` (still
       contains `|| true`).
     - `test_enforce_budget_node_clone_dir_fallback` (T3-D5) — delete; `CLONE_DIR` is
       no longer referenced by these nodes (Requirement 4 removes that dependency).
     - The three `test_enforce_budget_node_env_kill_switch` assertions (T4-E1,
       `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS` / `case "${_EENV` / `_MODE="observe"`) —
       delete; replaced by new `budget_gate.sh`-level tests (Requirement 8).
   - New tests are added asserting the one-line node shape for all 5 scenarios (not
     just `nonfatal` — also that the invoked scenario argument matches the node's own
     scenario, e.g. `enforce-budget-plan` invokes `budget_gate.sh plan`).

8. **A new shell test, `tests/test_budget_gate.sh`**, drives `budget_gate.sh` directly
   with fixture `context-budget.json` and `config.yaml` files (issue AC #4), covering:
   - **green** — enforce mode, under budget: exit `0`, `token-opt-caps.env` contains
     the derived `TOKEN_OPTIMIZATION_*_MAX_TOKENS` lines, `over_budget=false` written
     back to `context-budget.json`.
   - **over-budget** — enforce mode, reserved tokens ≥ budget: exit `0` (not an
     error), `over_budget=true` written back, caps still emitted (this is a valid,
     non-fatal outcome per Requirement 2).
   - **observe** — `enforce.<scenario>: false` or `enforce_budgets: false` in config:
     exit `0`, `token-opt-caps.env` is empty/truncated (no caps printed — matches
     current `budget_enforce.py` observe-mode behavior of only writing telemetry
     back, never stdout).
   - **kill-switch** — config says `enforce: true` for the scenario, but
     `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS=false` in the environment: exit `0`, mode
     forced to observe (empty caps), proving the override wins.
   This follows the existing `tests/test_budget_line_trim.sh` precedent (a standalone
   bash script under `tests/`, not a pytest subprocess wrapper).

9. **The new shell test must actually run in CI.** `tests/test_budget_line_trim.sh`
   is *not* currently wired into `.github/workflows/ci.yml`'s explicit
   `run: bash tests/test_*.sh` list (only `test_identity.sh`, `test_hooks.sh`,
   `test_smoke_gate.sh`, and `test_run_compose.sh` are) — `pytest tests/ -v` does not
   pick up `.sh` files. Without an explicit `- run: bash tests/test_budget_gate.sh`
   line added to `ci.yml`, the new test would exist but silently never execute,
   leaving issue AC #4 unmet in practice. This ticket adds that line; it does not
   retroactively wire in `test_budget_line_trim.sh` (out of scope — file a follow-up
   if desired).

## Architecture / Approach

### `scripts/budget_gate.sh` (new)

```
Usage: budget_gate.sh <scenario>
Env:   ARTIFACTS_DIR (required — same contract as the existing enforce-budget-* nodes)
       TOKEN_OPTIMIZATION_ENFORCE_BUDGETS (optional kill-only override)
Exit:  0 = gate ran to completion (observe or enforce, over-budget or not)
       1 = budget_enforce.py hard failure (missing/unparseable context-budget.json)
```

- `_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"` — resolves both
  sibling and config paths without `$CLONE_DIR`.
- `_CFG="${_SCRIPT_DIR}/../../.claude/skills/refinement/config.yaml"` (clone-root-
  relative, matching `_CLONE_REL` in `effective_config.py`).
- Single-parse config read exactly as today (now a real multi-line Python block in a
  `.sh` file — no longer constrained to a single YAML-safe line):
  `enforce_budgets`, `enforce.<scenario>`, `budgets.<scenario>` (falling back to
  `default_budget_tokens`), defaulting to `false false 30000` on any read error.
- Mode decision: `enforce_budgets && enforce.<scenario>` → `enforce`, else `observe`;
  then the `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS` kill-only override, identical to
  today's `case "${_EENV,,}" in false|0|no) _MODE="observe" ;; esac`.
- Invokes `"${_SCRIPT_DIR}/budget_enforce.py"` with `--context-budget-json
  "$ARTIFACTS_DIR/context-budget.json" --budget-tokens "$_BUD" --mode "$_MODE"
  --config "$_CFG"`, redirecting stdout to `$ARTIFACTS_DIR/token-opt-caps.env`
  (truncating, per the existing comment about clearing stale caps).
- Propagates `budget_enforce.py`'s own exit code (`1` on its documented
  `--context-budget-json` read failure; `0` otherwise) as `budget_gate.sh`'s exit
  code.

### `workflows/archon-dark-factory.yaml`

- Each of the 5 `enforce-budget-*` nodes (refine, plan, implement, conformance,
  code-review) becomes:
  ```yaml
  bash: |
    bash dark-factory/scripts/budget_gate.sh <scenario> || true
  ```
  `depends_on`, `when`, and `timeout: 30000` are unchanged from today for every node.
- The stale lines 364-366 comment ("the Archon runner does not support calling
  external scripts...") is deleted.
- Net effect: ~150 lines removed from the workflow file (issue's stated benefit),
  concentrated entirely in the five `enforce-budget-*` bodies — the sibling
  `budget-*` (telemetry) nodes are untouched; they are a separate concern
  (`context_budget.py` invocation) not covered by this ticket.

### `tests/test_budget_enforce_dag.py`

Per Requirement 7: delete T3-D5 and the three T4-E1 content assertions, rewrite
T3-D4 to check the exact one-line form, add a per-scenario check that the invoked
`budget_gate.sh` argument matches the node's scenario. All other tests unchanged.

### `tests/test_budget_gate.sh` (new)

Per Requirement 8: a standalone bash script (mirroring `test_budget_line_trim.sh`'s
shape — `mktemp -d`, hand-authored fixture JSON/YAML, `trap ... EXIT` cleanup)
invoking `dark-factory/scripts/budget_gate.sh` (or `scripts/budget_gate.sh` directly,
implementer's choice, since they're the same file pre-materialization) with
`ARTIFACTS_DIR` pointed at the temp dir, asserting exit codes and the four scenarios
from Requirement 8 against the resulting `token-opt-caps.env` and
`context-budget.json` write-back.

### `.github/workflows/ci.yml`

Add `- run: bash tests/test_budget_gate.sh` alongside the existing
`test_identity.sh`/`test_hooks.sh`/`test_smoke_gate.sh`/`test_run_compose.sh` lines
(Requirement 9).

## Alternatives considered

1. **Let a hard `budget_gate.sh` failure block the DAG node** (drop the `|| true`
   wrapper now that the logic is testable and presumably more trustworthy). Rejected
   — the issue is scoped as a pure refactor ("byte-identical except the scenario
   key," "Workflow YAML shrinks ~150 lines"), and the entire token-budget subsystem
   is deliberately fail-open end to end (`budget_enforce.py`'s own docstring: "Fail-
   open: errors are logged to stderr and do not affect the load-bearing path";
   memory: advisory tools in the factory "must exit 0 or it will abort an autonomous
   factory run"). Changing DAG-blocking behavior is a separate, separately-risk-
   assessed ticket (Q&A #1).

2. **Keep threading `$CLONE_DIR`/CWD-relative paths into `budget_gate.sh`** (matching
   the inline blocks' `_CLONE="${CLONE_DIR:-.}"` pattern) rather than self-locating
   via `BASH_SOURCE`. Rejected — `$CLONE_DIR` is documented in the workflow itself as
   "NOT exported to bash nodes," which is exactly the fragility that motivated the
   existing `${CLONE_DIR:-.}` fallback comment. `load_memory_context.sh` in the same
   directory already establishes the `dirname "${BASH_SOURCE[0]}"` self-locating
   pattern; reusing it removes an entire class of "which directory am I actually in"
   bugs for both the new script and any future sibling scripts (Q&A #2).

3. **Wait for the "config-module ticket" (widening `effective_config`) to land
   first**, per the issue's own hedge ("If the config-module ticket lands first,
   `budget_gate.sh` reads the materialized run-config instead of inline YAML
   parsing"). Rejected as a blocking dependency — `effective_config.py` already
   exists and already targets `.claude/skills/refinement/config.yaml` as its
   materialization path (today, in the transition period, it deliberately leaves the
   clone's file in place unchanged). Reading that same path directly, as this spec
   requires (Requirement 4), is already forward-compatible with the widening ticket
   landing later — no rework needed, so there is no reason to sequence behind it.

4. **Preserve the old `tests/test_budget_enforce_dag.py` content-assertions in some
   form** (e.g. asserting the same substrings now exist inside `budget_gate.sh`
   instead of the YAML). Rejected — the issue's AC #2 explicitly requires "no inline
   `python3 -c` config reads remain," so assertions written against those substrings
   are inherently about to become false; re-pointing them at the new file would just
   re-encode implementation details already covered by the new `test_budget_gate.sh`
   behavioral suite (Q&A #2).

## Open questions (non-blocking)

- Whether `test_budget_gate.sh` invokes `budget_gate.sh` via the repo-root
  `scripts/budget_gate.sh` path or a materialized `dark-factory/scripts/`
  copy is left to the implementer — both resolve to byte-identical file content
  before/after the `Dockerfile`'s `COPY scripts/ /opt/dark-factory/scripts/` step,
  so it doesn't affect what's under test.
- Whether to also retroactively wire `tests/test_budget_line_trim.sh` into
  `ci.yml` (Requirement 9 notes it is currently unwired) is left as a follow-up;
  not required by this issue's acceptance criteria.

## Assumptions (flagged)

- **[ASSUMPTION]** `docs/superpowers/specs/` does not exist in the working tree at
  the start of this refinement (no specs have merged to `main` yet, though several
  exist on other open `refine/*` branches) — this spec is written as a new file,
  matching the precedent set by `2026-07-07-cost-report-post-mortem-extraction-
  design.md` on a sibling branch.
- **[ASSUMPTION]** "Documented exit codes" (issue AC #1) means `budget_gate.sh`'s
  own exit codes are documented in a header comment in the script (as specified in
  Requirement 2 / the Architecture section above), not a change to any external
  contract — no caller other than the workflow YAML (which discards the code via
  `|| true`) and the new test (which asserts on it) consumes this exit code today.
- **[ASSUMPTION]** `budget_gate.sh` does not validate the `<scenario>` argument
  against a known list (`refine|plan|implement|conformance|code-review`) — an unknown
  scenario falls through to `default_budget_tokens: 30000` / `enforce: false`
  (observe), exactly as the current inline `.get(scenario, ...)` calls already do.
  Adding strict validation would be new behavior beyond what any inline block does
  today.
