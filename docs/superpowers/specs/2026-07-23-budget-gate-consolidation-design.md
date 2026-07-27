# Consolidate the 5x enforce-budget Nodes into scripts/budget_gate.sh

**Issue:** omniscient/dark-factory#183
**Status:** re-refined 2026-07-23 against current `main`. A prior 2026-07-07 spec for this ticket
existed on a now-deleted branch; per operator instruction this pass starts fresh and does not
assume any of that spec's claims still hold. One claim from it is explicitly corrected below.
**Parent context:** architecture-audit-v4 (candidate 4 of 6, 2026-07-06 architecture deepening
review). Related but explicitly NOT this ticket's scope: the "widen `effective_config`" follow-on
(wiring `scripts/factory_core/effective_config.py` into the config read this ticket extracts).

---

## Overview / Problem Statement

`workflows/archon-dark-factory.yaml` currently defines five `enforce-budget-*` nodes — one per
scenario (refine, plan, implement, conformance, code-review) — each an inline bash block that:
reads `token_optimization` config via an inline `python3 -c "import yaml..."` one-liner, derives an
`enforce`/`observe` mode, applies the `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS` kill-switch override, and
invokes `scripts/budget_enforce.py`. Re-derived directly from current `main` (2026-07-23):

- The five blocks are still byte-identical except the scenario string, confirming the issue's core
  claim: `enforce-budget-refine` (`workflows/archon-dark-factory.yaml:373-390`),
  `enforce-budget-plan` (`:420-437`), `enforce-budget-implement` (`:589-606`),
  `enforce-budget-conformance` (`:987-1004`), `enforce-budget-code-review` (`:1207-1224`). (These
  line numbers have shifted from the issue body's original citations — the file has moved since
  2026-07-06 — but the duplication itself is unchanged.)
- The header comment at `:370-372` ("the Archon runner does not support calling external scripts
  from YAML bash nodes without entrypoint changes") is false on current `main`: these same blocks
  already invoke `python3 .../scripts/budget_enforce.py`, and `refine-push` (`:456`) and
  `plan-push-and-advance` (`:503`) already invoke `bash .../scripts/push_gate_check.sh` from bash
  nodes. The seam was declined, not blocked, and remains available today.
- Every `enforce-budget-*` node's body is wrapped in `(...) || true`, so today a non-zero exit from
  this block never blocks the DAG — token-budget enforcement is purely advisory/fail-open. This is
  consistent with `scripts/budget_enforce.py` itself, which only exits non-zero (`1`) on unreadable
  or malformed `--context-budget-json` input; an over-budget result is reported via stdout/stderr,
  not a failing exit code.

**Correction to the discarded 2026-07-07 spec:** that spec cited `tests/test_budget_line_trim.sh`
as an example of an existing-but-unwired shell test. That file does not exist anywhere in the repo
(verified via `find . -iname '*budget_line_trim*'`) — the claim was stale or fabricated even at the
time. The underlying point is still independently true, just not via that example: current `main`'s
`.github/workflows/ci.yml` invokes exactly 11 named `tests/test_*.sh` files individually (no glob),
while `tests/` holds 35 `.sh` files total — so roughly 24 are genuinely unwired today (real
examples: `tests/test_scheduler.sh`, `tests/test_dispatch_ceiling.sh`, `tests/test_load_memory.sh`).
CLAUDE.md's own Conventions section frames CI as "`python -m pytest tests/ -v` ... plus
`smoke_gate.sh` and the workflow DAG checks," so most `.sh` files are out-of-band by design, not
uniformly an oversight — but a *new*, safety-relevant test should not be added to that unwired pile
(see Requirements).

This is a pure refactor: locality (gate semantics change once, apply 5×), independent testability,
and ~150 fewer YAML lines — no behavior change to enforcement itself.

---

## Requirements

Distilled from the issue's acceptance criteria and the Q&A below:

1. `scripts/budget_gate.sh <scenario>` holds the full config read, enforce/observe mode decision,
   `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS` kill-switch override, and `budget_enforce.py` invocation —
   a faithful extraction of the current inline logic, not a rewrite of its semantics.
2. Documented exit codes exist purely so the script is independently unit-testable; the DAG-blocking
   behavior does not change. Each `enforce-budget-*` node keeps wrapping its call in `(...) || true`.
3. All 5 `enforce-budget-*` nodes become one-line invocations: no inline `python3 -c` config read
   remains in any of them. Nodes call the script via the same `${CLONE_DIR:-.}/dark-factory/scripts/`
   path convention already used by `budget_enforce.py` and `push_gate_check.sh` in this same file.
4. The stale "runner does not support calling external scripts" comment (`:370-372`) is deleted.
5. Kill-switch semantics — `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS=false|0|no` forces observe mode and
   can never force enforce mode ON — are preserved byte-for-byte and covered by a test.
6. `budget_gate.sh` self-locates its sibling `scripts/budget_enforce.py` via
   `dirname "${BASH_SOURCE[0]}"` (the `load_memory_context.sh` convention), rather than depending on
   `$CLONE_DIR` (not exported to bash nodes) or caller CWD. The scenario config file
   (`.claude/skills/refinement/config.yaml`) is NOT a sibling of `scripts/` — it stays resolved
   CWD/clone-root-relative, matching every other node in this file (CWD is the clone root in bash
   nodes; `${CLONE_DIR:-.}` is the existing documented fallback).
7. A new `tests/test_budget_gate.sh` covers: green (within budget, enforce mode), over-budget
   (enforce mode), observe mode, and the kill-switch override — and is wired into
   `.github/workflows/ci.yml`'s explicit per-file list (new safety-relevant coverage should not join
   the pile of pre-existing unwired `.sh` tests).
8. `tests/test_budget_enforce_dag.py` is triaged, not left red: assertions that check node
   *structure* (existence, `depends_on`, `when`, `timeout`, absence of `trigger_rule`, `|| true`
   presence, DAG validator, `token-opt-caps.env` sourcing order) are kept as-is since they still hold
   against one-line nodes. Assertions that grep the inline bash *body* for logic now living in
   `budget_gate.sh` (`${CLONE_DIR:-.}` presence, the `_EENV` case statement, `_MODE="observe"`) are
   removed from this file; their intent is covered by `tests/test_budget_gate.sh` instead.
9. Out of scope: wiring `scripts/factory_core/effective_config.py` into the config read (a separate,
   already-identified follow-on); any change to DAG-blocking/gate semantics (a separate,
   human-reviewed ticket per CLAUDE.md's hard limits).

---

## Brainstorming Q&A

> **Q:** The issue's acceptance criteria ask for "documented exit codes" and a shell test covering
> "green/over-budget/observe/kill-switch paths," but every `enforce-budget-*` node already wraps its
> body in `(...) || true`, so the DAG never blocks on this today. Given CLAUDE.md's hard limit
> against weakening/restructuring budget safety logic as a side effect of an unrelated change, and
> given this issue is framed as a pure refactor, should `budget_gate.sh` use meaningful exit codes
> purely for independent testability while the YAML node keeps `|| true` (preserving today's
> behavior exactly), or does the issue want DAG-blocking behavior to change?
>
> **A:** Keep today's never-block-the-DAG behavior exactly. `budget_gate.sh` uses meaningful,
> documented exit codes purely so the extracted script is independently unit-testable; the YAML
> adapter node keeps wrapping the invocation in `(...) || true`. This ticket is scoped as a pure
> refactor ("byte-identical except the scenario key"), and the operator's re-scoping comment
> reinforces "keep scope to what the (corrected) problem statement actually requires." Making budget
> enforcement newly able to block the DAG would be a behavior change, not a refactor, and would
> violate CLAUDE.md's "never weaken safety gates... as a side effect" hard limit — exactly the case
> the operator flagged ("if the refactor's cleanest form touches a safety gate, call it out for its
> own reviewed ticket"). The two goals aren't in tension: exit codes (e.g. 0 = ran to completion in
> any mode, distinct codes for internal failure vs. computed-over-budget) make the four required test
> paths assertable at the script boundary, while `|| true` at the adapter level guarantees the DAG's
> observed behavior is byte-for-byte unchanged. Also flagged: the stale "runner can't call external
> scripts" comment (`:370-372`) should be deleted as part of the collapse — it's already contradicted
> by the very blocks it sits above.

> **Q:** `tests/test_budget_enforce_dag.py`'s content-assertions (grepping the inline bash body for
> `CLONE_DIR`, the kill-switch `case` statement, etc.) will break once the 5 nodes collapse to
> one-liners, since that logic moves into `budget_gate.sh`. (1) Is rewriting those obsolete
> assertions in scope, with equivalent coverage added to the new shell test? (2) Should
> `budget_gate.sh` self-locate `budget_enforce.py` via `BASH_SOURCE` rather than `$CLONE_DIR`/CWD?
> (3) Should the new shell test be wired into `ci.yml`, or is leaving it unwired (like ~24 other
> existing `.sh` tests) acceptable?
>
> **A:** (1) Yes to both — leaving `test_budget_enforce_dag.py` red is not an option since
> `python -m pytest tests/ -v` is the CI contract per CLAUDE.md; the DAG test keeps only structural
> assertions (node exists, `depends_on`, `when`, `timeout==30000`, `trigger_rule` absence, `|| true`
> presence, DAG validator, the downstream `token-opt-caps.env`/`RANK_IN` ordering checks, all of
> which are unaffected by the body becoming a one-liner); the kill-switch/mode-decision logic moves
> to `tests/test_budget_gate.sh`. This is squarely what AC "kill-switch semantics... preserved and
> tested" requires, not gold-plating. (2) Yes — `budget_enforce.py` is a direct sibling of the new
> script, so `dirname "${BASH_SOURCE[0]}"` finds it reliably regardless of CWD or the un-exported
> `$CLONE_DIR`, matching `load_memory_context.sh`'s existing convention. Caveat: the scenario config
> file lives at a different anchor (`.claude/skills/refinement/config.yaml`, no `dark-factory/`
> prefix, not a sibling of `scripts/`) — self-location solves finding the sibling script only; the
> config path stays resolved from CWD/clone-root as today. (3) Yes — wire it into `ci.yml`'s explicit
> per-file list. The AC's intent ("kill-switch semantics... preserved and tested") is defeated if the
> test protecting that invariant never runs in CI; `ci.yml` is not a hard-exclude path, the addition
> is a one-line, low-risk change matching the 11 existing named invocations, and shipping new
> safety-relevant coverage unwired (unlike the ~24 pre-existing gap-coverage `.sh` files) would be a
> regression in intent even if the letter of the AC is silent on it. Also flagged: do not wire
> `effective_config.py` into this refactor — no ticket authorizes it and doing so would exceed the
> size: M scope; `budget_gate.sh` should be a faithful extraction of current logic only.

---

## Architecture / Approach

**New file: `scripts/budget_gate.sh`**

```
Usage: budget_gate.sh <scenario>
Env:   ARTIFACTS_DIR (required — same contract as the current inline blocks)
       CLONE_DIR (optional, defaults to ".", matching every other node in this workflow)
       TOKEN_OPTIMIZATION_ENFORCE_BUDGETS (optional kill-switch override)
Exit codes (for independent testability only — callers should wrap in `|| true`
to preserve today's fail-open DAG semantics; this script does not change that):
  0 = ran to completion (covers observe mode, enforce-mode green, and
      enforce-mode over-budget — budget_enforce.py itself only fails on
      malformed input, per its existing contract)
  1 = budget_enforce.py hard failure (unreadable/malformed context-budget.json)
  2 = usage error (missing/unrecognized <scenario> argument, ARTIFACTS_DIR unset)
```

Body: lift the existing inline logic verbatim —

1. Resolve `_CLONE="${CLONE_DIR:-.}"` and `_CFG="${_CLONE}/.claude/skills/refinement/config.yaml"`
   (unchanged from today; CWD is the clone root in bash nodes).
2. Single-parse the `token_optimization` block via the existing `python3 -c "import yaml..."`
   one-liner (parameterized on `$1` instead of a hardcoded scenario key) to get
   `enforce_budgets`, `enforce.<scenario>`, and `budgets.<scenario>` (falling back to
   `default_budget_tokens`).
3. Derive `_MODE` (`enforce` iff both booleans true, else `observe`); apply the
   `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS` override (`false|0|no` forces `observe`, matching today's
   `case "${_EENV,,}" in false|0|no) _MODE="observe" ;; esac` verbatim).
4. Locate `budget_enforce.py` via `dirname "${BASH_SOURCE[0]}"` (sibling of `budget_gate.sh`) and
   invoke it with the same flags as today, writing `$ARTIFACTS_DIR/token-opt-caps.env` (truncate,
   not append — same comment carried over).
5. Propagate `budget_enforce.py`'s own exit code (1 on hard failure); exit 2 on usage errors caught
   before that point; exit 0 otherwise.

**Workflow YAML changes** (`workflows/archon-dark-factory.yaml`):

- Delete the stale comment block at `:370-372`.
- Replace each `enforce-budget-*` node's `bash:` body with a one-liner following the existing
  cross-script call convention in this same file (`push_gate_check.sh`, `budget_enforce.py`):

  ```yaml
  - id: enforce-budget-refine
    bash: |
      bash "${CLONE_DIR:-.}/dark-factory/scripts/budget_gate.sh" refine || true  # TARGET-PATH
    depends_on: [budget-refine]
    when: "$parse-intent.output.intent == 'refine'"
    timeout: 30000
  ```

  (and equivalently for plan/implement/conformance/code-review). `depends_on`, `when`, and `timeout`
  are unchanged per node — only the `bash:` body collapses.

**Test changes:**

- `tests/test_budget_enforce_dag.py`: remove the four content-assertions listed in Requirement 8;
  keep every structural assertion as-is (they assert against `depends_on`/`when`/`timeout`/node
  presence, which the DAG YAML still has).
- New `tests/test_budget_gate.sh`: exercises `budget_gate.sh` directly (not through the DAG) with a
  fixture `context-budget.json`, covering: (a) green — enforce mode, under budget; (b) over-budget —
  enforce mode, verifies `budget_enforce.py`'s reported over-budget output surfaces in
  `token-opt-caps.env` without the script itself failing; (c) observe mode — `enforce.<scenario>:
  false` in config; (d) kill-switch — `enforce.<scenario>: true` in config but
  `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS=false` in env, asserting mode still resolves to observe.
- `.github/workflows/ci.yml`: add `- run: bash tests/test_budget_gate.sh` to the `tests` job,
  alongside the 11 existing named `.sh` invocations.

---

## Alternatives Considered

1. **Python script instead of bash.** `budget_enforce.py` is already Python, so a `budget_gate.py`
   that also does the config read and mode decision was considered. Rejected: the current inline
   logic is bash-native (YAML `when:`/env-var handling, the `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS`
   shell-env override), and converting it to Python would be a larger rewrite than a "byte-identical
   except scenario key" refactor calls for — it risks subtly changing the kill-switch's shell-string
   comparison semantics (`${_EENV,,}` case-insensitive matching) for no stated benefit.
2. **Wire `effective_config.py` into `budget_gate.sh` now**, since the discarded 2026-07-07 spec
   flagged it as forward-compatible. Rejected per Q&A: no ticket authorizes this wiring, and doing so
   during this ticket would mix a config-materialization behavior change into what both product-owner
   answers and the operator comment insist stays a pure, size:M extraction.
3. **Make budget enforcement actually block the DAG**, satisfying "documented exit codes" more
   literally. Rejected per Q&A and CLAUDE.md's hard limit — this is the exact "weaken/restructure a
   safety gate as a side effect" case that requires its own reviewed ticket.

---

## Open Questions (Non-blocking)

- Whether/when the "widen `effective_config`" follow-on lands and whether it should then read
  `budget_gate.sh`'s config path from the materialized run-config instead of re-parsing
  `.claude/skills/refinement/config.yaml` directly — deferred to that ticket, not this one.

---

## Assumptions

- "Documented exit codes" means documented in a header comment inside `budget_gate.sh` itself — no
  external caller other than the discarding `|| true` YAML wrapper and the new test consumes the
  code today.
- `budget_gate.sh` does not validate `<scenario>` against a fixed enum; an unrecognized scenario
  falls through to the config's `.get(scenario, ...)` defaults, matching current inline behavior.
- The `${CLONE_DIR:-.}/dark-factory/scripts/` invocation path (rather than a bare `scripts/` path) is
  correct because it matches every other cross-script call already present in this workflow file
  (`budget_enforce.py`, `push_gate_check.sh`) — this is an existing, working runtime convention for
  self-target containers, not something introduced by this ticket.
