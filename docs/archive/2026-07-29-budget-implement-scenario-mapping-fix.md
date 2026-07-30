# Implementation Plan: Fix `budget-implement`'s Invalid `"new"` Scenario Argument

**Issue:** omniscient/dark-factory#280
**Spec:** `docs/superpowers/specs/2026-07-28-budget-implement-scenario-mapping-design.md`
**Related:** #183 (the `enforce-budget-*` → `scripts/budget_gate.sh` consolidation this
plan's extraction mirrors)

---

## Goal

`workflows/archon-dark-factory.yaml`'s `budget-implement` node passes `INTENT` (`new` or
`continue`, read from `issue.json`) straight through as `context_budget.py`'s `--scenario`
argument. `context_budget.py`'s `--scenario` only accepts `_SECTION_REGISTRY`'s six keys
(`refine, plan, implement, continue, conformance, code-review`) — `"new"` is not one of
them, so every `intent=new` implement dispatch (every first Fix on a ticket) silently fails
to write `context-budget.json`, swallowed by the node's trailing `|| true`.

Extract the node's logic into a new `scripts/budget_context.sh` (mirroring the #183
`budget_gate.sh` extraction), with an explicit `new`→`implement` / `continue`→`continue`
mapping table, collapse the DAG node to a one-line call to that script, and add a
`WARNING:` visibility line when `context-budget.json` isn't written. This is a **pure bug
fix**: `context_budget.py`'s CLI surface, `_SECTION_REGISTRY`, and the sections assembled
for a first-pass run (`_SECTION_REGISTRY["implement"]` is already correct) do not change.

## Architecture

```
workflows/archon-dark-factory.yaml
  budget-implement
       │ bash "${CLONE_DIR:-.}/dark-factory/scripts/budget_context.sh"   (NOT || true)
       ▼
scripts/budget_context.sh
  1. guard: ARTIFACTS_DIR set and $ARTIFACTS_DIR/issue.json present, else exit 2
  2. read ISSUE/INTENT from issue.json via jq (unchanged)
  3. map INTENT → SCENARIO explicitly: new→implement, continue→continue;
     unrecognized INTENT → exit 1 (fail loud, NOT wrapped in || true)
  4. self-locate sibling context_budget.py via dirname "${BASH_SOURCE[0]}"
  5. invoke context_budget.py --scenario "$SCENARIO" ... || true   (fail-open, scoped
     to just this invocation — telemetry must never block an implement dispatch)
  6. if $ARTIFACTS_DIR/context-budget.json is missing/empty, print a WARNING: line to
     stderr (new — the visibility fix; does not change the exit code)
  7. exit 0
       │
       ▼
scripts/context_budget.py   (unchanged — --scenario CLI surface, _SECTION_REGISTRY
                              untouched, still called with `implement`/`continue`)
```

Two failure classes stay distinct, matching today's behavior exactly:
- An **unrecognized `INTENT`** is a real bug → the node's one-line call is *not* wrapped in
  `|| true`, so `budget_context.sh`'s `exit 1` fails the node (unchanged from today's
  un-wrapped `case` guard).
- A **`context_budget.py` failure** (e.g. a future unrelated argparse error) is telemetry
  noise → `|| true` stays scoped to just that invocation *inside* `budget_context.sh`, so it
  can never fail the node, and now also triggers the new `WARNING:` line.

## Tech Stack

- Bash only for `budget_context.sh` — the current inline logic is bash-native (`jq`,
  `case` mapping); no Python rewrite, matching `budget_gate.sh`'s precedent.
- Bash for `tests/test_budget_context.sh`, matching the existing `tests/test_*.sh`
  convention (`set -euo pipefail`, PASS/FAIL assertions, `echo PASS` on success).
- `pytest` for the new static guard added to `tests/test_budget_enforce_dag.py`.

## File Structure

| File | Change |
|---|---|
| `scripts/budget_context.sh` | **New** — INTENT→SCENARIO mapping, `context_budget.py` invocation, not-written warning |
| `tests/test_budget_context.sh` | **New** — new/continue/unrecognized-intent/usage-error coverage |
| `workflows/archon-dark-factory.yaml` | **Modified** — `budget-implement` node collapses to a one-liner; stale `pr_reviews` comment (`:531-533`) deleted |
| `tests/test_budget_enforce_dag.py` | **Modified** — add `test_all_reachable_scenarios_are_registry_keys`, which extracts scenario literals from the DAG/script (the check that would have caught #280) |
| `.github/workflows/ci.yml` | **Modified** — add `- run: bash tests/test_budget_context.sh` |

---

## Task 1: `scripts/budget_context.sh` + `tests/test_budget_context.sh`

**Files:** `scripts/budget_context.sh` (new), `tests/test_budget_context.sh` (new)

### TDD Steps

1. Write the failing test file `tests/test_budget_context.sh`. Each case builds its own
   fake **artifacts dir** (`$CASE/issue.json`) and points `ARTIFACTS_DIR`/`CLONE_DIR` at
   it — `CLONE_DIR` is set equal to the fixture dir so `context_budget.py`'s `CLAUDE.md`/
   `ARCHITECTURE.md` reads simply miss (reported `dropped`/`empty_or_missing`, matching
   `tests/test_context_budget.py`'s existing minimal-fixture pattern), not so the script
   needs any new flag:

```bash
#!/usr/bin/env bash
# Covers #280 acceptance criteria: new/continue intent mapping, unrecognized-intent
# fail-loud, and usage errors through scripts/budget_context.sh directly (not through
# the DAG). Modeled on tests/test_budget_gate.sh (#183).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="${REPO_ROOT}/scripts/budget_context.sh"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# $1 = fake artifacts dir, $2 = intent value written to issue.json
_make_case() {
  local dir="$1" intent="$2"
  mkdir -p "$dir"
  printf '{"resolved_number": 280, "intent": "%s"}' "$intent" > "${dir}/issue.json"
}

# --- Case 1: intent=new maps to scenario=implement --------------------------
CASE1="${WORK}/case1"
_make_case "$CASE1" new
RC=0
ARTIFACTS_DIR="$CASE1" CLONE_DIR="$CASE1" bash "$SCRIPT" \
  > "${CASE1}/stdout.log" 2> "${CASE1}/stderr.log" || RC=$?
[ "$RC" = "0" ] || { echo "FAIL case1 exit code: $RC"; cat "${CASE1}/stderr.log"; exit 1; }
[ -f "${CASE1}/context-budget.json" ] || { echo "FAIL case1 expected context-budget.json to exist"; exit 1; }
SCEN=$(jq -r '.scenario' "${CASE1}/context-budget.json")
[ "$SCEN" = "implement" ] || { echo "FAIL case1 expected scenario=implement, got $SCEN"; exit 1; }

# --- Case 2: intent=continue maps to scenario=continue ----------------------
CASE2="${WORK}/case2"
_make_case "$CASE2" continue
RC=0
ARTIFACTS_DIR="$CASE2" CLONE_DIR="$CASE2" bash "$SCRIPT" \
  > "${CASE2}/stdout.log" 2> "${CASE2}/stderr.log" || RC=$?
[ "$RC" = "0" ] || { echo "FAIL case2 exit code: $RC"; cat "${CASE2}/stderr.log"; exit 1; }
SCEN=$(jq -r '.scenario' "${CASE2}/context-budget.json")
[ "$SCEN" = "continue" ] || { echo "FAIL case2 expected scenario=continue, got $SCEN"; exit 1; }

# --- Case 3: unrecognized intent fails loud, is NOT swallowed ---------------
CASE3="${WORK}/case3"
_make_case "$CASE3" bogus
RC=0
ARTIFACTS_DIR="$CASE3" CLONE_DIR="$CASE3" bash "$SCRIPT" \
  > "${CASE3}/stdout.log" 2> "${CASE3}/stderr.log" || RC=$?
[ "$RC" = "1" ] || { echo "FAIL case3 expected exit 1, got $RC"; cat "${CASE3}/stderr.log"; exit 1; }
grep -q "unexpected INTENT" "${CASE3}/stderr.log" || { echo "FAIL case3 expected 'unexpected INTENT' in stderr"; cat "${CASE3}/stderr.log"; exit 1; }
[ ! -f "${CASE3}/context-budget.json" ] || { echo "FAIL case3 expected no context-budget.json to be written"; exit 1; }

# --- Case 4: usage errors ----------------------------------------------------
RC=0
env -u ARTIFACTS_DIR bash "$SCRIPT" 2>/dev/null || RC=$?
[ "$RC" = "2" ] || { echo "FAIL case4a expected exit 2 on missing ARTIFACTS_DIR, got $RC"; exit 1; }

CASE4B="${WORK}/case4b"
mkdir -p "$CASE4B"
RC=0
ARTIFACTS_DIR="$CASE4B" bash "$SCRIPT" 2>/dev/null || RC=$?
[ "$RC" = "2" ] || { echo "FAIL case4b expected exit 2 on missing issue.json, got $RC"; exit 1; }

echo PASS
```

2. Verify it fails (script doesn't exist yet):

```bash
chmod +x tests/test_budget_context.sh
bash tests/test_budget_context.sh
# bash: .../scripts/budget_context.sh: No such file or directory
```

3. Implement `scripts/budget_context.sh`:

```bash
#!/usr/bin/env bash
# Maps the implement-phase INTENT (new|continue) to context_budget.py's --scenario
# vocabulary and invokes it. Backs the budget-implement workflow node — a faithful
# extraction of what used to be duplicated inline logic (#280), mirroring the #183
# budget_gate.sh extraction for the enforce-budget-* nodes.
#
# Usage: budget_context.sh
# Env:   ARTIFACTS_DIR (required) — must contain issue.json; context-budget.json is
#                   written here
#        CLONE_DIR (optional, default ".") — clone root; CWD is the clone root in
#                   bash workflow nodes, so "." is the correct default there
#        RUN_ID (optional, defaults to basename of ARTIFACTS_DIR — matches the
#                   inline fallback this script replaces)
#
# Exit codes:
#   0 = ran to completion, including when context_budget.py itself failed (fail-open —
#       budget telemetry must never block an implement dispatch)
#   1 = unknown/missing INTENT in issue.json (fail loud — a real bug, not telemetry
#       noise; matches the un-wrapped `case` guard this script replaces)
#   2 = usage error (ARTIFACTS_DIR unset or issue.json missing)

if [ -z "${ARTIFACTS_DIR:-}" ]; then
  echo "budget_context.sh: ARTIFACTS_DIR must be set" >&2
  exit 2
fi

if [ ! -f "$ARTIFACTS_DIR/issue.json" ]; then
  echo "budget_context.sh: $ARTIFACTS_DIR/issue.json not found" >&2
  exit 2
fi

_CLONE="${CLONE_DIR:-.}"
_RUN="${RUN_ID:-$(basename "${ARTIFACTS_DIR:-/tmp/budget}")}"
_SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"

ISSUE=$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")
INTENT=$(jq -r '.intent' "$ARTIFACTS_DIR/issue.json")

case "$INTENT" in
  new)      SCENARIO=implement ;;
  continue) SCENARIO=continue ;;
  *) echo "budget_context.sh: unexpected INTENT='$INTENT'; expected new or continue" >&2
     exit 1 ;;
esac

# memory-context.md is written inside the command session by memory_retrieve.py (Phase 1
# load), so it is reported as dropped/empty_or_missing here — expected, unchanged.
python3 "${_SCRIPT_DIR}/context_budget.py" \
  --scenario "$SCENARIO" \
  --issue-num "$ISSUE" \
  --run-id "$_RUN" \
  --artifacts-dir "$ARTIFACTS_DIR" \
  --clone-dir "$_CLONE" \
  --issue-json "$ARTIFACTS_DIR/issue.json" \
  --memory-file "$ARTIFACTS_DIR/memory-context.md" \
  --comment-digest-file "$ARTIFACTS_DIR/comment-digest.md" \
  --out "$ARTIFACTS_DIR/context-budget.json" || true

if [ ! -s "$ARTIFACTS_DIR/context-budget.json" ]; then
  echo "WARNING: budget_context.sh: $ARTIFACTS_DIR/context-budget.json was not written (context_budget.py invocation failed)" >&2
fi

exit 0
```

4. Verify it passes:

```bash
bash tests/test_budget_context.sh
# PASS
```

5. Commit:

```bash
git add scripts/budget_context.sh tests/test_budget_context.sh
git commit -m "feat(budget-context): extract budget-implement's INTENT->SCENARIO mapping into scripts/budget_context.sh (#280)"
```

---

## Task 2: Collapse the `budget-implement` node + add the registry static guard

**Files:** `workflows/archon-dark-factory.yaml` (modified), `tests/test_budget_enforce_dag.py` (modified)

### TDD Steps

1. Confirm the current baseline is green before editing:

```bash
python -m pytest tests/test_budget_enforce_dag.py -v
# all tests pass
```

2. Edit `workflows/archon-dark-factory.yaml`: delete the stale 3-line comment
   immediately above `budget-implement` (the `_CLONE`/`case` guard it describes is being
   replaced, and its claim is inaccurate — there is no `pr_reviews` key anywhere in
   `_SECTION_REGISTRY`):

```yaml
  # Context budget telemetry — captures pre-prompt token estimate for implement/continue phase.
  # INTENT is read from issue.json (new|continue); context_budget.py uses the distinct registry
  # entries for each: "continue" includes pr_reviews, "new" does not (per _SECTION_REGISTRY).
```

   Replace the node's `bash:` body with a one-liner (matching `enforce-budget-implement`'s
   existing call convention immediately below it), keeping `depends_on`/`trigger_rule`/
   `when`/`timeout` — and the two OR-join explanatory comments between `depends_on` and
   `trigger_rule` (they document the invariant `scripts/check_workflow_dag.py`'s
   `REQUIRED_OR_JOIN_NODES` enforces, and are unrelated to the bash-body logic being
   extracted) — exactly as they are today. Per Requirement 2/the spec's rejected
   Alternative 4, the one-liner is deliberately **not** wrapped in `|| true` (that would
   silently swallow the unrecognized-`INTENT` fail-loud case, the exact bug class this
   ticket fixes):

```yaml
  - id: budget-implement
    bash: |
      bash "${CLONE_DIR:-.}/dark-factory/scripts/budget_context.sh"
    depends_on: [update-codeindex, fetch-issue, digest-comments]
    # OR-join: digest-comments runs only on continue; skipped on new.
    # none_failed_min_one_success ensures this node runs when digest-comments is skipped.
    trigger_rule: none_failed_min_one_success
    when: "$parse-intent.output.intent == 'new' || $parse-intent.output.intent == 'continue'"
    timeout: 30000
```

3. Run the DAG/when validators and the full test suite — no existing assertion inspects
   `budget-implement`'s (as opposed to `enforce-budget-implement`'s) bash body, so nothing
   should break:

```bash
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
python -m pytest tests/test_budget_enforce_dag.py -v
python -m pytest tests/test_workflow_or_join.py -v
# all still pass — test_workflow_or_join.py's budget-implement fixtures only assert
# trigger_rule/depends_on, unaffected by the bash body change
```

4. Add the new static guard to `tests/test_budget_enforce_dag.py` (append at the end of
   the file; add `import re` alongside the existing `import sys` at the top of the file —
   `re` is not currently imported there) — the check that would have caught #280 before
   merge. It must extract the scenario
   values actually reachable from the DAG/script rather than hardcoding them a second
   time (a hardcoded-on-both-sides version would pass against the pre-fix tree too, since
   it never reads the YAML or the script — it wouldn't have caught #280 at all):

```python
# ── T5-G1: every reachable --scenario value is a _SECTION_REGISTRY key ──────
# The check that would have caught #280: budget-implement used to pass INTENT
# ("new"/"continue") straight through as --scenario, and "new" is not a registry
# key. Extracts the literal --scenario values from the four budget-* nodes that
# pass one directly, plus the SCENARIO=<value> case arms from budget_context.sh
# (which budget-implement now delegates to) — not hardcoded, so a future node or
# script that starts emitting an invalid scenario is actually caught.

def test_all_reachable_scenarios_are_registry_keys():
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    from context_budget import _SECTION_REGISTRY
    registry_keys = set(_SECTION_REGISTRY.keys())

    nodes = _workflow_nodes()
    found = set()
    for node_id in ("budget-refine", "budget-plan", "budget-conformance", "budget-code-review"):
        bash = nodes.get(node_id, {}).get("bash", "")
        matches = re.findall(r'--scenario\s+"?([a-z-]+)"?', bash)
        assert matches, f"{node_id}: no --scenario literal found in bash body"
        found.update(matches)

    script_text = (_REPO_ROOT / "scripts" / "budget_context.sh").read_text(encoding="utf-8")
    mapped = re.findall(r'SCENARIO=([a-z-]+)\s*;;', script_text)
    assert mapped, "budget_context.sh: no SCENARIO=<value> case arms found"
    found.update(mapped)

    for scenario in found:
        assert scenario in registry_keys, \
            f"scenario '{scenario}' (from DAG/budget_context.sh) not in _SECTION_REGISTRY"
```

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
git commit -m "fix(workflow): collapse budget-implement to scripts/budget_context.sh, add scenario-registry static guard (#280)"
```

---

## Task 3: Wire `test_budget_context.sh` into CI

**Files:** `.github/workflows/ci.yml` (modified)

### TDD Steps

1. Confirm the gap: `test_budget_context.sh` (from Task 1) is not yet referenced anywhere
   in `.github/workflows/ci.yml`:

```bash
grep -n "test_budget_context" .github/workflows/ci.yml
# (no output)
```

2. Add it to the `tests` job's explicit per-file list, next to the existing
   `test_budget_gate.sh` line:

```yaml
      - run: bash tests/test_budget_gate.sh
      - run: bash tests/test_budget_context.sh
```

3. Verify the addition:

```bash
grep -n "test_budget_context" .github/workflows/ci.yml
# - run: bash tests/test_budget_context.sh
bash tests/test_budget_context.sh
# PASS
```

4. Commit:

```bash
git add .github/workflows/ci.yml
git commit -m "ci(budget-context): wire tests/test_budget_context.sh into ci.yml (#280)"
```

---

## Out of Scope (per spec Requirement 9)

- Any change to `context_budget.py`'s section-assembly logic or CLI surface, to
  `budget_gate.sh`/`budget_enforce.py`, or to the other four `budget-*` nodes (they pass
  literal scenario strings and cannot exhibit this bug).
- Flipping `token_optimization.enforce.implement` (currently `false` in
  `config/config.yaml`). This ticket fixes the telemetry/accounting pipeline so
  enforcement *can* work correctly once/if that flag is flipped; it does not flip it —
  that is a separate policy decision (spec Open Question).
