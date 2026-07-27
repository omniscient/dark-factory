# Implementation Plan: Collapse the 5x enforce-budget nodes into `scripts/budget_gate.sh`

**Issue:** omniscient/dark-factory#183
**Spec:** `docs/superpowers/specs/2026-07-23-budget-gate-consolidation-design.md`

---

## Goal

Extract the byte-identical logic shared by the five `enforce-budget-*` workflow nodes
(refine/plan/implement/conformance/code-review) — config read, enforce/observe mode
decision, `TOKEN_OPTIMIZATION_ENFORCE_BUDGETS` kill-switch, `budget_enforce.py`
invocation — into one `scripts/budget_gate.sh <scenario>`, so each node collapses to a
one-line `bash .../budget_gate.sh <scenario> || true` adapter. This is a **faithful
extraction, not a behavior change**: every node keeps wrapping the call in `(...) || true`
(fail-open, unchanged), the kill-switch's exact case-insensitive semantics are preserved
byte-for-byte, and `budget_gate.sh`'s documented exit codes exist purely so the extracted
logic is independently unit-testable outside the DAG.

## Architecture

```
workflows/archon-dark-factory.yaml
  enforce-budget-refine        enforce-budget-plan        ... (5 nodes)
       │                            │
       │ bash "${CLONE_DIR:-.}/dark-factory/scripts/budget_gate.sh" <scenario> || true
       ▼                            ▼
scripts/budget_gate.sh <scenario>
  1. resolve _CLONE="${CLONE_DIR:-.}", _CFG=<clone-root-relative config.yaml>
  2. single python3 -c "import yaml..." parse of token_optimization (parameterized
     on <scenario>, not hardcoded) → enforce_budgets, enforce.<scenario>, budgets.<scenario>
  3. derive _MODE (enforce iff both true, else observe); apply
     TOKEN_OPTIMIZATION_ENFORCE_BUDGETS kill-switch override (false|0|no → observe,
     case-insensitive, never forces enforce ON)
  4. self-locate sibling budget_enforce.py via dirname "${BASH_SOURCE[0]}"
  5. invoke budget_enforce.py, write $ARTIFACTS_DIR/token-opt-caps.env (truncate)
  6. propagate exit code: 0 = ran to completion, 1 = budget_enforce.py hard failure,
     2 = usage error
       │
       ▼
scripts/budget_enforce.py   (unchanged — existing sibling script)
```

The IO/path-resolution boundary matches `scripts/load_memory_context.sh`'s existing
convention exactly: the sibling script (`budget_enforce.py`) is found via `BASH_SOURCE`,
while the scenario config (`.claude/skills/refinement/config.yaml`, not a sibling of
`scripts/`) stays resolved `${CLONE_DIR:-.}`-relative, matching every other node in this
workflow file.

## Tech Stack

- Bash only for `budget_gate.sh` — the current inline logic is bash-native (YAML
  `when:`/env-var handling, the `${_EENV,,}` case-insensitive kill-switch match); no
  Python rewrite (per spec Alternative #1, rejected as out of scope for a "byte-identical
  except scenario key" refactor).
- `python3 -c "import yaml..."` one-liner for the config read — unchanged from today,
  parameterized via a `sys.argv` scenario argument instead of a hardcoded key.
- Bash for the new `tests/test_budget_gate.sh`, matching the existing `tests/test_*.sh`
  convention (`set -euo pipefail`, PASS/FAIL assertions, `echo PASS` on success).
- `pytest` for the (modified) `tests/test_budget_enforce_dag.py` — unchanged framework.

## File Structure

| File | Change |
|---|---|
| `scripts/budget_gate.sh` | **New** — config read, mode decision, kill-switch, `budget_enforce.py` invocation |
| `tests/test_budget_gate.sh` | **New** — green/over-budget/observe/kill-switch coverage |
| `workflows/archon-dark-factory.yaml` | **Modified** — 5 `enforce-budget-*` nodes collapse to one-liners; stale comment (`:370-372`) deleted |
| `tests/test_budget_enforce_dag.py` | **Modified** — remove `test_enforce_budget_node_env_kill_switch` (body-content assertions now covered by `test_budget_gate.sh`); every other assertion (structural) unchanged |
| `.github/workflows/ci.yml` | **Modified** — add `- run: bash tests/test_budget_gate.sh` |

---

## Task 1: `scripts/budget_gate.sh` + `tests/test_budget_gate.sh`

**Files:** `scripts/budget_gate.sh` (new), `tests/test_budget_gate.sh` (new)

### TDD Steps

1. Write the failing test file `tests/test_budget_gate.sh`. Each case builds its own
   fake **clone root** directory (`$CASE/.claude/skills/refinement/config.yaml` +
   `$CASE/context-budget.json`) and points `CLONE_DIR`/`ARTIFACTS_DIR` at it — this
   exercises `budget_gate.sh`'s real, unmodified config-path resolution
   (`${CLONE_DIR:-.}/.claude/skills/refinement/config.yaml`) rather than adding any new
   flag to the script:

```bash
#!/usr/bin/env bash
# Covers #183 acceptance criteria: green / over-budget / observe / kill-switch paths
# through scripts/budget_gate.sh directly (not through the DAG).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GATE="${REPO_ROOT}/scripts/budget_gate.sh"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# context-budget.json fixture shared by all cases: claude_md=500, issue_context=300
# (floored to 2000 by budget_enforce.py's reserve_tokens), two optimizable sections
# present (memory_context, comments) so enforce-mode output is non-empty.
_CONTEXT_BUDGET_JSON='{
  "scenario": "refine",
  "sections": {
    "claude_md": {"status": "loaded", "tokens": 500},
    "issue_context": {"status": "loaded", "tokens": 300},
    "architecture_md": {"status": "dropped"},
    "memory_context": {"status": "loaded", "tokens": 400},
    "comments": {"status": "loaded", "tokens": 300},
    "diff": {"status": "dropped"}
  }
}'

# $1 = fake clone-root dir, $2 = enforce_budgets, $3 = enforce.refine, $4 = budgets.refine
_make_case() {
  local dir="$1"
  mkdir -p "${dir}/.claude/skills/refinement"
  printf '%s' "$_CONTEXT_BUDGET_JSON" > "${dir}/context-budget.json"
  cat > "${dir}/.claude/skills/refinement/config.yaml" <<CFG
token_optimization:
  enforce_budgets: ${2}
  default_budget_tokens: 30000
  budgets:
    refine: ${4}
  enforce:
    refine: ${3}
CFG
}

# $1 = fake clone-root dir, $2.. = extra env assignments (e.g. kill-switch override)
_run_gate() {
  local dir="$1"; shift
  env "$@" ARTIFACTS_DIR="$dir" CLONE_DIR="$dir" bash "$GATE" refine \
    > "${dir}/stdout.log" 2> "${dir}/stderr.log"
  echo $?
}

# --- Case 1: green — enforce mode, comfortably under budget ---------------
CASE1="${WORK}/case1"
_make_case "$CASE1" true true 30000
RC=$(_run_gate "$CASE1" TOKEN_OPTIMIZATION_ENFORCE_BUDGETS=)
[ "$RC" = "0" ] || { echo "FAIL case1 exit code: $RC"; cat "${CASE1}/stderr.log"; exit 1; }
grep -q "over_budget=False" "${CASE1}/stderr.log" || { echo "FAIL case1 expected over_budget=False"; cat "${CASE1}/stderr.log"; exit 1; }
[ -s "${CASE1}/token-opt-caps.env" ] || { echo "FAIL case1 expected non-empty token-opt-caps.env (enforce mode)"; exit 1; }

# --- Case 2: over-budget — enforce mode, budget too small to cover reserved
CASE2="${WORK}/case2"
_make_case "$CASE2" true true 1000
RC=$(_run_gate "$CASE2" TOKEN_OPTIMIZATION_ENFORCE_BUDGETS=)
[ "$RC" = "0" ] || { echo "FAIL case2 exit code: $RC (budget_enforce.py must not hard-fail on over-budget)"; cat "${CASE2}/stderr.log"; exit 1; }
grep -q "over_budget=True" "${CASE2}/stderr.log" || { echo "FAIL case2 expected over_budget=True"; cat "${CASE2}/stderr.log"; exit 1; }

# --- Case 3: observe mode — enforce.refine: false in config ----------------
CASE3="${WORK}/case3"
_make_case "$CASE3" true false 30000
RC=$(_run_gate "$CASE3" TOKEN_OPTIMIZATION_ENFORCE_BUDGETS=)
[ "$RC" = "0" ] || { echo "FAIL case3 exit code: $RC"; cat "${CASE3}/stderr.log"; exit 1; }
[ ! -s "${CASE3}/token-opt-caps.env" ] || { echo "FAIL case3 expected empty token-opt-caps.env (observe mode emits no KEY=VALUE lines)"; exit 1; }

# --- Case 4: kill-switch — config says enforce, env override forces observe
CASE4="${WORK}/case4"
_make_case "$CASE4" true true 30000
RC=$(_run_gate "$CASE4" TOKEN_OPTIMIZATION_ENFORCE_BUDGETS=false)
[ "$RC" = "0" ] || { echo "FAIL case4 exit code: $RC"; cat "${CASE4}/stderr.log"; exit 1; }
[ ! -s "${CASE4}/token-opt-caps.env" ] || { echo "FAIL case4 expected empty token-opt-caps.env (kill-switch forces observe despite enforce:true)"; exit 1; }

# --- Case 5: usage errors -----------------------------------------------
set +e
ARTIFACTS_DIR="${WORK}/case5" bash "$GATE" 2>/dev/null
RC=$?
set -e
[ "$RC" = "2" ] || { echo "FAIL case5 expected exit 2 on missing scenario arg, got $RC"; exit 1; }

set +e
env -u ARTIFACTS_DIR bash "$GATE" refine 2>/dev/null
RC=$?
set -e
[ "$RC" = "2" ] || { echo "FAIL case5b expected exit 2 on missing ARTIFACTS_DIR, got $RC"; exit 1; }

echo PASS
```

2. Verify it fails (script doesn't exist yet):

```bash
chmod +x tests/test_budget_gate.sh
bash tests/test_budget_gate.sh
# bash: .../scripts/budget_gate.sh: No such file or directory
```

3. Implement `scripts/budget_gate.sh`:

```bash
#!/usr/bin/env bash
# Budget gate — reads token_optimization config, derives enforce/observe mode,
# applies the TOKEN_OPTIMIZATION_ENFORCE_BUDGETS kill-switch, and invokes
# budget_enforce.py. One script backs all 5 enforce-budget-* workflow nodes
# (refine/plan/implement/conformance/code-review) — a faithful extraction of
# logic that used to be duplicated inline per scenario (#183).
#
# Usage: budget_gate.sh <scenario>
# Env:   ARTIFACTS_DIR (required) — same contract as context_budget.py/budget_enforce.py
#        CLONE_DIR (optional, default ".") — clone root; CWD is the clone root in
#                   bash workflow nodes, so "." is the correct default there
#        TOKEN_OPTIMIZATION_ENFORCE_BUDGETS (optional) — kill-switch override;
#                   false|0|no forces observe mode, can never force enforce ON
#
# Exit codes (for standalone testability only — callers wrap this call in `|| true`
# to preserve the DAG's fail-open semantics; this script does not change that):
#   0 = ran to completion (observe mode, enforce-mode green, or enforce-mode
#       over-budget — budget_enforce.py itself only fails on malformed input)
#   1 = budget_enforce.py hard failure (unreadable/malformed context-budget.json)
#   2 = usage error (missing/unrecognized <scenario> argument, ARTIFACTS_DIR unset)

SCENARIO="${1:-}"
if [ -z "$SCENARIO" ]; then
  echo "Usage: budget_gate.sh <scenario>" >&2
  exit 2
fi

_CLONE="${CLONE_DIR:-.}"
_CFG="${_CLONE}/.claude/skills/refinement/config.yaml"

if [ -z "${ARTIFACTS_DIR:-}" ]; then
  echo "budget_gate.sh: ARTIFACTS_DIR must be set" >&2
  exit 2
fi

_SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"

# Single parse — avoids mode/budget desync if config is partially malformed.
# Scenario is passed as a python argv (not string-interpolated) to keep the
# YAML/config string-interpolation surface identical to today's inline blocks.
read -r _EB _ES _BUD < <(python3 -c "
import sys, yaml
to = yaml.safe_load(open('${_CFG}')).get('token_optimization', {})
s = sys.argv[1]
print(str(to.get('enforce_budgets', False)).lower(),
      str(to.get('enforce', {}).get(s, False)).lower(),
      to.get('budgets', {}).get(s, to.get('default_budget_tokens', 30000)))
" "$SCENARIO" 2>/dev/null || echo "false false 30000")

if [ "$_EB" = "true" ] && [ "$_ES" = "true" ]; then _MODE="enforce"; else _MODE="observe"; fi
_EENV="${TOKEN_OPTIMIZATION_ENFORCE_BUDGETS:-}"
case "${_EENV,,}" in false|0|no) _MODE="observe" ;; esac

python3 "${_SCRIPT_DIR}/budget_enforce.py" \
  --context-budget-json "$ARTIFACTS_DIR/context-budget.json" \
  --budget-tokens "${_BUD:-30000}" --mode "$_MODE" --config "$_CFG" \
  > "$ARTIFACTS_DIR/token-opt-caps.env"  # truncate (not append) — clears stale caps from prior enforce runs
exit $?
```

4. Verify it passes:

```bash
bash tests/test_budget_gate.sh
# PASS
```

5. Commit:

```bash
git add scripts/budget_gate.sh tests/test_budget_gate.sh
git commit -m "feat(budget-gate): extract enforce-budget config/mode/kill-switch logic into scripts/budget_gate.sh (#183)"
```

---

## Task 2: Collapse the 5 workflow nodes + triage `test_budget_enforce_dag.py`

**Files:** `workflows/archon-dark-factory.yaml` (modified), `tests/test_budget_enforce_dag.py` (modified)

### TDD Steps

1. Confirm the current baseline is green before editing:

```bash
python -m pytest tests/test_budget_enforce_dag.py -v
# all tests pass
```

2. Edit `workflows/archon-dark-factory.yaml`: delete the stale comment block
   immediately above `enforce-budget-refine` —

```yaml
  # The 5 enforce-budget-* nodes below are intentionally inline bash blocks; the Archon
  # runner does not support calling external scripts from YAML bash nodes without entrypoint
  # changes. Each block differs only by the scenario key ('refine', 'plan', etc.).
```

   — and replace each of the 5 `enforce-budget-*` nodes' `bash:` body with a one-liner,
   keeping `depends_on`/`when`/`timeout` exactly as they are today (only the `bash:` body
   changes). For `enforce-budget-refine`:

```yaml
  - id: enforce-budget-refine
    bash: |
      bash "${CLONE_DIR:-.}/dark-factory/scripts/budget_gate.sh" refine || true  # TARGET-PATH
    depends_on: [budget-refine]
    when: "$parse-intent.output.intent == 'refine'"
    timeout: 30000
```

   Apply the same collapse to `enforce-budget-plan` (scenario `plan`),
   `enforce-budget-implement` (scenario `implement`), `enforce-budget-conformance`
   (scenario `conformance`), and `enforce-budget-code-review` (scenario `code-review`) —
   each keeping its own existing `depends_on`/`when`/`timeout` values unchanged.

3. Run the DAG/when validators and the full test suite to see exactly what breaks:

```bash
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
python -m pytest tests/test_budget_enforce_dag.py -v
# Expect exactly one failure: test_enforce_budget_node_env_kill_switch (parametrized x5)
# — it asserts on TOKEN_OPTIMIZATION_ENFORCE_BUDGETS / `case "${_EENV` / `_MODE="observe"`
# literal substrings that no longer appear in the one-line bash body (that logic now
# lives in scripts/budget_gate.sh, covered by tests/test_budget_gate.sh). Every other
# test (node exists, depends_on, no trigger_rule, || true, CLONE_DIR fallback presence
# in the invocation, timeout, when gates, command depends_on enforce-not-budget,
# implement's trigger_rule, DAG validator, memory-context/code-review/conformance
# sourcing) must still be green — the one-liner still contains "${CLONE_DIR:-.}" and
# "|| true", and depends_on/when/timeout are untouched.
```

4. Remove exactly the assertions that grep body-content logic now owned by
   `budget_gate.sh` — delete the `test_enforce_budget_node_env_kill_switch` function
   (the `# ── T4-E1: enforce-budget nodes read env kill-switch ─────` block at the
   bottom of `tests/test_budget_enforce_dag.py`). Do not touch any other test in the
   file — every other assertion is structural (node shape, not body content) and
   continues to hold against the one-line nodes.

5. Verify green:

```bash
python -m pytest tests/test_budget_enforce_dag.py -v
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
python -m pytest tests/ -v
```

6. Commit:

```bash
git add workflows/archon-dark-factory.yaml tests/test_budget_enforce_dag.py
git commit -m "refactor(workflow): collapse 5x enforce-budget-* nodes to budget_gate.sh one-liners (#183)"
```

---

## Task 3: Wire `test_budget_gate.sh` into CI

**Files:** `.github/workflows/ci.yml` (modified)

### TDD Steps

1. Confirm the gap: `test_budget_gate.sh` (from Task 1) is not yet referenced anywhere
   in `.github/workflows/ci.yml`:

```bash
grep -n "test_budget_gate" .github/workflows/ci.yml
# (no output)
```

2. Add it to the `tests` job's explicit per-file list, alongside the 11 existing named
   `.sh` invocations:

```yaml
      - run: bash tests/test_entrypoint_cost_report_regression.sh
      - run: bash tests/test_budget_gate.sh
```

3. Verify the addition:

```bash
grep -n "test_budget_gate" .github/workflows/ci.yml
# - run: bash tests/test_budget_gate.sh
bash tests/test_budget_gate.sh
# PASS
```

4. Commit:

```bash
git add .github/workflows/ci.yml
git commit -m "ci(budget-gate): wire tests/test_budget_gate.sh into ci.yml (#183)"
```

---

## Out of Scope (per spec Requirement 9 / operator sequencing note)

- Wiring `scripts/factory_core/effective_config.py` into `budget_gate.sh`'s config read
  — a separate follow-on ticket (#180, "widen effective_config"), which the operator's
  approval comment says lands **after** this ticket and then adapts `budget_gate.sh` to
  read the materialized run-config instead of re-parsing YAML directly.
- Any change to DAG-blocking/gate semantics — every node keeps `|| true`; this refactor
  does not make budget enforcement newly able to block the DAG (CLAUDE.md hard limit:
  never weaken/restructure a safety gate as a side effect of an unrelated change).
