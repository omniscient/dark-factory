# Fix budget-implement's Invalid "new" Scenario Argument

**Issue:** omniscient/dark-factory#280
**Status:** initial refinement, 2026-07-28.
**Related:** #279 (empty-prompt root cause — separate, not touched here), #223
(token-optimization doc/config reconciliation — not touched here), #183 (the
`enforce-budget-*` → `scripts/budget_gate.sh` consolidation this spec's approach mirrors).

---

## Overview / Problem Statement

`workflows/archon-dark-factory.yaml`'s `budget-implement` node (`:534-564`) reads `INTENT` from
`issue.json` (`new` or `continue`) and passes it straight through as `context_budget.py`'s
`--scenario` argument. `context_budget.py`'s `--scenario` uses
`choices=list(_SECTION_REGISTRY.keys())` = `refine, plan, implement, continue, conformance,
code-review` (`scripts/context_budget.py:26-33,371`) — `"new"` is not a member. When
`INTENT=new` (every first Fix dispatch on a ticket), argparse rejects the CLI invocation, the
`python3` call inside the node exits non-zero (swallowed by its trailing `|| true`), and
`$ARTIFACTS_DIR/context-budget.json` is never written. The downstream `enforce-budget-implement`
node (`scripts/budget_gate.sh implement` → `scripts/budget_enforce.py`) then fails to read that
file, logs `budget_enforce: error reading .../context-budget.json`, and proceeds with
`reserved=0`. Both nodes still report Completed, so this has been silently broken on every
`intent=new` implement dispatch; `intent=continue` only works because `continue` happens to also
be a registry key.

Root cause: `new`/`continue` (from `parse-intent.output.intent`, whose domain also includes
`plan`/`resolve`/`close`) is the *intent* vocabulary. `refine`/`plan`/`implement`/`continue`/
`conformance`/`code-review` (shared by `config/config.yaml`'s `token_optimization.budgets`/
`enforce` maps, `scripts/budget_gate.sh`, `scripts/context_pack.py`, and
`scripts/architecture_slice.py`) is the *scenario* vocabulary. These are different namespaces
that happen to overlap on the string `"continue"` — the `budget-implement` node conflated them.

This is a pure bug fix: no new capability, no behavior change to what context gets assembled once
the correct scenario is used (`_SECTION_REGISTRY["implement"]` is already the right section list
for a first-pass run — plain `comments`, no `spec`).

---

## Requirements

Distilled from the issue's acceptance criteria and the Q&A below:

1. `context_budget.py`'s `--scenario` CLI surface is untouched — it never accepts the literal
   string `"new"`. `_SECTION_REGISTRY` keeps its current six keys.
2. The `budget-implement` node maps `INTENT` to a `SCENARIO` value explicitly before invoking
   `context_budget.py`: `new` → `implement`, `continue` → `continue`. An unrecognized `INTENT`
   still fails loud (non-zero exit, not swallowed by `|| true`) — this fail-loud behavior is
   preserved from the current inline `case` guard, not weakened.
3. Following the #183 precedent (`enforce-budget-*` nodes → `scripts/budget_gate.sh`), the
   `budget-implement` node's logic is extracted into a new `scripts/budget_context.sh`, and the
   node itself collapses to a one-line invocation of that script. Inline behavioral logic in DAG
   YAML bash is the pattern this codebase has already moved away from for budget nodes; adding a
   second inline-bash instance of the same defect class one commit after #183 fixed it for the
   `enforce-budget-*` nodes would be inconsistent.
4. `context_budget.py`'s own failure (e.g. a future unrelated argparse error) must still be
   fail-open for the implement dispatch — `context-budget.json` not being written must never fail
   the node. This is preserved via `|| true` scoped to just the `python3` invocation inside
   `budget_context.sh`, not a blanket wrapper around the whole script.
5. Visibility fix: when `context-budget.json` is not written after the `context_budget.py` call,
   `budget_context.sh` emits an explicit `WARNING:` line to stderr, so a future silent failure of
   this kind shows up in node output instead of requiring someone to read raw stderr.
6. The stale comment above the current node (`:531-533`, claiming `"continue" includes
   pr_reviews, "new" does not`) is deleted — there is no `pr_reviews` section key anywhere in
   `_SECTION_REGISTRY`.
7. Regression coverage, per this codebase's established test philosophy (inline YAML bash gets
   only static assertions; anything with real logic is extracted to a file and exercised
   directly — see `tests/test_budget_gate.sh` vs. `tests/test_budget_enforce_dag.py`):
   - New `tests/test_budget_context.sh`, modeled on `tests/test_budget_gate.sh`, drives
     `scripts/budget_context.sh` directly with a fixture `issue.json` and asserts: `INTENT=new` →
     exit 0, `context-budget.json` exists with `.scenario == "implement"`; `INTENT=continue` →
     exit 0, `.scenario == "continue"`; an unrecognized `INTENT` → non-zero exit and a stderr
     message; missing `ARTIFACTS_DIR`/`issue.json` → usage-error exit. Wired into
     `.github/workflows/ci.yml` next to the existing `test_budget_gate.sh` line.
   - A new static guard in `tests/test_budget_enforce_dag.py`: import `_SECTION_REGISTRY` from
     `scripts/context_budget.py` and assert every scenario value the DAG can produce for a
     `--scenario` argument — the literal `--scenario <x>` strings in the other four
     `budget-*` nodes (`refine`, `plan`, `conformance`, `code-review`) plus the mapped values in
     `budget_context.sh`'s case statement (`implement`, `continue`) — is a registry key. This is
     the class of check that would have caught #280 before merge, and it generalizes to the other
     four nodes without requiring any change to them.
8. Existing `tests/test_budget_enforce_dag.py` assertions are unaffected: none of them inspect
   `budget-implement`'s (as opposed to `enforce-budget-implement`'s) bash body, so no existing
   assertion needs to change.
9. Out of scope: any change to `context_budget.py`'s section-assembly logic, to
   `budget_gate.sh`/`budget_enforce.py`, to the other four `budget-*` nodes, or to
   `token_optimization.enforce.implement` (currently `false` in `config/config.yaml` — this ticket
   fixes the telemetry/accounting pipeline so enforcement *can* work correctly once/if that flag
   is flipped; it does not flip it).

---

## Brainstorming Q&A

> **Q:** The issue offers two fix options: (a) map `INTENT=new` to `--scenario implement` at the
> DAG call site, or (b) add a `"new"` alias key to `_SECTION_REGISTRY` inside `context_budget.py`
> itself. Which should the spec choose, and should `context_budget.py`'s `--scenario` ever accept
> the literal string `"new"`?
>
> **A:** Choose (a). `new`/`continue` is the intent vocabulary; `refine`/`plan`/`implement`/
> `continue`/`conformance`/`code-review` is the scenario vocabulary shared by
> `config/config.yaml`, `budget_gate.sh`, `context_pack.py`, and `architecture_slice.py`. Adding
> `"new"` to `_SECTION_REGISTRY` would leak intent-speak into a namespace four other components
> key off, and immediately be inconsistent (no `budgets.new`/`enforce.new`, and
> `enforce-budget-implement` would still hardcode `budget_gate.sh implement`). The recorded
> `"scenario"` telemetry field should stay `"implement"` for first-pass runs so it's joinable
> against `budgets.implement`/`enforce.implement` — recording `"new"` would be a fresh instance of
> the #223 doc/config drift problem. Keep the existing `case "$INTENT"` guard but make it an
> explicit mapping table (`new) SCENARIO=implement ;; continue) SCENARIO=continue ;; *) exit 1
> ;;`) rather than a bare validator, and pass `--scenario "$SCENARIO"`. Also fix the stale
> `pr_reviews` comment, and add a post-invocation warning when `context-budget.json` isn't
> written, since `|| true` on the python call must stay (budget telemetry must never fail an
> implement dispatch) and that's exactly what made this bug invisible.

> **Q:** The bug and its fix both live entirely in the `budget-implement` node's inline bash, and
> no existing test infrastructure executes a specific DAG node's inline `bash:` block end-to-end
> (`test_context_budget.py` only calls `context_budget.py`'s Python function directly, bypassing
> the CLI/argparse layer entirely — invisible to this exact bug both before and after a call-site
> fix). Should the test (a) parse and execute the actual node's `bash:` string out of the YAML in
> a new general-purpose harness, (b) mirror just the mapping table as an inline shell one-liner
> test, or (c) something else?
>
> **A:** (c) — extract the node's logic into `scripts/budget_context.sh` and test that script
> directly, exactly as #183 (three commits prior on this branch's history) already did for the
> five `enforce-budget-*` nodes: extract to `scripts/budget_gate.sh`, collapse each node to a
> one-liner, add `tests/test_budget_gate.sh` driving the script with fixture dirs, wire it into
> `ci.yml`. This codebase's consistent philosophy is that inline YAML bash gets only static
> assertions (`test_budget_enforce_dag.py`, `test_push_gate_dag.py` just check `"|| true" in
> bash`); anything with real behavior is extracted to a file and executed
> (`tests/test_159_regression.sh` sources `scheduler.sh` rather than reimplementing it). Reject
> (a): a generic node-bash-execution harness has to reconstruct the executor's env (`RUN_ID`,
> `jq`, the `when:` gating) — high complexity that would tempt future work to leave more logic in
> YAML "because it's testable now." Reject (b): a mirrored mapping table only asserts the test
> agrees with itself and would pass even if the real YAML/script diverged. Preserve failure
> semantics exactly: today an unknown `INTENT` fails the node while a `context_budget.py` failure
> does not — so `|| true` must stay *inside* `budget_context.sh` on just the python invocation,
> and the node's one-line call to the script must NOT itself be wrapped in `|| true` (that would
> silently convert the unknown-intent guard into another no-op — the same bug class this ticket
> fixes). Add one cheap static guard to `test_budget_enforce_dag.py` (assert every `--scenario`
> literal reachable from the workflow, across all five `budget-*` nodes, is a valid
> `_SECTION_REGISTRY` key) — that's the check that would actually have caught #280 in review.
> Scope note: extract only `budget-implement`; the other four `budget-*` nodes pass literal
> scenario strings and cannot exhibit this bug, so folding them in would be out-of-scope churn.

---

## Architecture / Approach

**New file: `scripts/budget_context.sh`**

```
Usage: budget_context.sh
Env:   ARTIFACTS_DIR (required) — must contain issue.json; context-budget.json is written here
       CLONE_DIR (optional, default ".")
       RUN_ID (optional, defaults to basename of ARTIFACTS_DIR — matches today's inline fallback)
Exit codes:
  0 = ran to completion, including when context_budget.py itself failed (fail-open — budget
      telemetry must never block an implement dispatch)
  1 = unknown/missing INTENT in issue.json (fail loud — a real bug, not telemetry noise;
      matches today's un-wrapped `case` exit 1)
  2 = usage error (ARTIFACTS_DIR unset or issue.json missing)
```

Body — lift the current inline logic, with the mapping and visibility fixes:

1. Guard: `ARTIFACTS_DIR` set and `$ARTIFACTS_DIR/issue.json` present, else exit 2.
2. Read `ISSUE`/`INTENT` from `issue.json` via `jq` (unchanged).
3. Map `INTENT` → `SCENARIO` explicitly:
   ```bash
   case "$INTENT" in
     new)      SCENARIO=implement ;;
     continue) SCENARIO=continue ;;
     *) echo "budget_context.sh: unexpected INTENT='$INTENT'; expected new or continue" >&2
        exit 1 ;;
   esac
   ```
4. Locate `context_budget.py` via `dirname "${BASH_SOURCE[0]}"` (sibling of `budget_context.sh`,
   matching `budget_gate.sh`'s existing self-location convention) rather than
   `$_CLONE/dark-factory/scripts/...`.
5. Invoke `context_budget.py` with `--scenario "$SCENARIO"` and the same flags as today
   (`--issue-num`, `--run-id`, `--artifacts-dir`, `--clone-dir "${CLONE_DIR:-.}"`,
   `--issue-json`, `--memory-file`, `--comment-digest-file`, `--out`), keeping `|| true` scoped to
   just this invocation.
6. After the call, if `$ARTIFACTS_DIR/context-budget.json` is missing or empty, print a `WARNING:`
   line to stderr (new — this is the visibility fix; does not change the exit code).
7. Exit 0.

**Workflow YAML changes** (`workflows/archon-dark-factory.yaml`):

- Delete the stale comment block at `:531-533`.
- Replace the `budget-implement` node's `bash:` body with a one-liner, following the same
  cross-script call convention `enforce-budget-implement` already uses:

  ```yaml
  - id: budget-implement
    bash: |
      bash "${CLONE_DIR:-.}/dark-factory/scripts/budget_context.sh"
    depends_on: [update-codeindex, fetch-issue, digest-comments]
    trigger_rule: none_failed_min_one_success
    when: "$parse-intent.output.intent == 'new' || $parse-intent.output.intent == 'continue'"
    timeout: 30000
  ```

  `depends_on`, `trigger_rule`, `when`, and `timeout` are unchanged — only the `bash:` body
  collapses, and it is deliberately NOT wrapped in `|| true` (see Requirement 2 / Q&A).

**Test changes:**

- New `tests/test_budget_context.sh` (sibling of `tests/test_budget_gate.sh`): a `mktemp -d`
  fixture directory with an `issue.json` (`resolved_number`, `intent`), driving
  `scripts/budget_context.sh` directly via `ARTIFACTS_DIR=<fixture> CLONE_DIR=<fixture> bash
  scripts/budget_context.sh`. Cases: `intent=new` → exit 0, `context-budget.json` exists,
  `jq .scenario` == `"implement"`; `intent=continue` → exit 0, `jq .scenario` == `"continue"`;
  `intent=bogus` → non-zero exit, stderr contains the `unexpected INTENT` message; missing
  `ARTIFACTS_DIR`/`issue.json` → exit 2.
- `.github/workflows/ci.yml`: add `- run: bash tests/test_budget_context.sh` next to the existing
  `- run: bash tests/test_budget_gate.sh` line.
- `tests/test_budget_enforce_dag.py`: add a new static guard —
  ```python
  def test_all_reachable_scenarios_are_registry_keys():
      sys.path.insert(0, str(_REPO_ROOT / "scripts"))
      from context_budget import _SECTION_REGISTRY
      registry_keys = set(_SECTION_REGISTRY.keys())
      # Literal --scenario values in the four nodes that pass one directly.
      nodes = _workflow_nodes()
      for node_id, literal in [
          ("budget-refine", "refine"), ("budget-plan", "plan"),
          ("budget-conformance", "conformance"), ("budget-code-review", "code-review"),
      ]:
          assert literal in registry_keys, f"{node_id}'s scenario '{literal}' not in _SECTION_REGISTRY"
      # budget-implement now delegates to budget_context.sh; check its mapped values instead
      # of a literal in the node body.
      for mapped in ("implement", "continue"):
          assert mapped in registry_keys, f"budget_context.sh's mapped scenario '{mapped}' not in _SECTION_REGISTRY"
  ```
  No existing assertion in this file inspects `budget-implement`'s bash body, so nothing else in
  it needs to change.

---

## Alternatives Considered

1. **Add `"new"` as a `_SECTION_REGISTRY` alias key.** Rejected per Q&A — conflates the intent and
   scenario vocabularies, produces a telemetry label (`"new"`) that reconciles with nothing in
   `config.yaml`'s `budgets`/`enforce` maps, and diverges `context_budget.py`'s CLI surface from
   the same-shaped `context_pack.py`/`architecture_slice.py` scenario arguments.
2. **Keep the mapping inline in the YAML node** (just fix the `case` statement in place, don't
   extract to a script). Rejected: this codebase settled the inline-vs-extracted question one
   commit ago via #183 for the sibling `enforce-budget-*` nodes specifically so this class of
   logic could be independently tested; leaving `budget-implement` inline would reintroduce the
   exact untested-YAML-bash pattern #183 just eliminated, one node over.
3. **Test via a generic YAML-bash-execution harness.** Rejected per Q&A — no precedent in this
   codebase, meaningfully more complex than extraction, and would tempt future nodes to stay
   inline "because it's testable now" instead of following the established extraction pattern.
4. **Blanket-wrap the new `budget-implement` node call in `|| true`** for symmetry with
   `enforce-budget-implement`. Rejected: this would silently swallow the unknown-`INTENT` fail-loud
   case too, undoing the one part of the current behavior that already works correctly (an
   unrecognized intent is a real bug and should fail the node, unlike a `context_budget.py`
   telemetry hiccup).

---

## Open Questions (Non-blocking)

- Whether `token_optimization.enforce.implement` (currently `false`) should ever flip to `true`
  now that the underlying telemetry pipeline will work correctly for `intent=new` — a policy
  decision for a separate ticket, not this one.

---

## Assumptions

- `${CLONE_DIR:-.}/dark-factory/scripts/budget_context.sh` is the correct invocation path from the
  workflow node, matching the existing `budget_gate.sh`/`push_gate_check.sh`/`budget_enforce.py`
  convention already used elsewhere in this same file for self-target container clones.
- `RUN_ID` continues to default to `basename "${ARTIFACTS_DIR:-/tmp/budget}"` when unset, matching
  today's inline fallback exactly (no behavior change).
- No downstream consumer branches on the literal string `"new"` vs `"implement"` in
  `context-budget.json`'s `"scenario"` field — confirmed by inspection of `budget_enforce.py` and
  `scripts/factory_core/cost_report.py`, both of which only use it in display/log text.
