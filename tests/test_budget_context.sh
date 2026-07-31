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

# --- Case 1: intent=new maps to scenario=implement ---------------------------
# CLONE_DIR is pointed at the real repo root (not the throwaway artifacts dir) so
# CLAUDE.md is actually readable — otherwise every section resolves to "dropped"
# and the token-total/claude_md assertions below would pass vacuously even if
# --clone-dir were wired to the wrong value.
CASE1="${WORK}/case1"
_make_case "$CASE1" new
RC=0
ARTIFACTS_DIR="$CASE1" CLONE_DIR="$REPO_ROOT" bash "$SCRIPT" \
  > "${CASE1}/stdout.log" 2> "${CASE1}/stderr.log" || RC=$?
[ "$RC" = "0" ] || { echo "FAIL case1 exit code: $RC"; cat "${CASE1}/stderr.log"; exit 1; }
[ -f "${CASE1}/context-budget.json" ] || { echo "FAIL case1 expected context-budget.json to exist"; exit 1; }
SCEN=$(jq -r '.scenario' "${CASE1}/context-budget.json")
[ "$SCEN" = "implement" ] || { echo "FAIL case1 expected scenario=implement, got $SCEN"; exit 1; }
CLAUDE_MD_STATUS=$(jq -r '.sections.claude_md.status' "${CASE1}/context-budget.json")
[ "$CLAUDE_MD_STATUS" = "included" ] || { echo "FAIL case1 expected sections.claude_md.status=included, got $CLAUDE_MD_STATUS"; cat "${CASE1}/context-budget.json"; exit 1; }
EST_TOKENS=$(jq -r '.estimated_input_tokens' "${CASE1}/context-budget.json")
[ "$EST_TOKENS" -gt 0 ] || { echo "FAIL case1 expected estimated_input_tokens > 0, got $EST_TOKENS"; exit 1; }

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

# --- Case 5: WARNING visibility line when context-budget.json isn't written -
# A non-numeric resolved_number makes context_budget.py's --issue-num (type=int)
# fail argparse validation, so context-budget.json is never written.
CASE5="${WORK}/case5"
mkdir -p "$CASE5"
printf '{"resolved_number": "not-a-number", "intent": "new"}' > "${CASE5}/issue.json"
RC=0
ARTIFACTS_DIR="$CASE5" CLONE_DIR="$CASE5" bash "$SCRIPT" \
  > "${CASE5}/stdout.log" 2> "${CASE5}/stderr.log" || RC=$?
[ "$RC" = "0" ] || { echo "FAIL case5 expected exit 0 even when context_budget.py fails, got $RC"; cat "${CASE5}/stderr.log"; exit 1; }
[ ! -s "${CASE5}/context-budget.json" ] || { echo "FAIL case5 expected no/empty context-budget.json when python3 fails"; exit 1; }
grep -q "WARNING:" "${CASE5}/stderr.log" || { echo "FAIL case5 expected WARNING: line in stderr"; cat "${CASE5}/stderr.log"; exit 1; }

echo PASS
