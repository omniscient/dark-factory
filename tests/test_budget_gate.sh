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
  local rc=0
  env "$@" ARTIFACTS_DIR="$dir" CLONE_DIR="$dir" bash "$GATE" refine \
    > "${dir}/stdout.log" 2> "${dir}/stderr.log" || rc=$?
  echo "$rc"
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
RC=0
ARTIFACTS_DIR="${WORK}/case5" bash "$GATE" 2>/dev/null || RC=$?
[ "$RC" = "2" ] || { echo "FAIL case5 expected exit 2 on missing scenario arg, got $RC"; exit 1; }

RC=0
env -u ARTIFACTS_DIR bash "$GATE" refine 2>/dev/null || RC=$?
[ "$RC" = "2" ] || { echo "FAIL case5b expected exit 2 on missing ARTIFACTS_DIR, got $RC"; exit 1; }

echo PASS
