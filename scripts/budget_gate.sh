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
#   2 = usage error (missing <scenario> argument, ARTIFACTS_DIR unset)

SCENARIO="${1:-}"
if [ -z "$SCENARIO" ]; then
  echo "Usage: budget_gate.sh <scenario>" >&2
  exit 2
fi

if [ -z "${ARTIFACTS_DIR:-}" ]; then
  echo "budget_gate.sh: ARTIFACTS_DIR must be set" >&2
  exit 2
fi

_CLONE="${CLONE_DIR:-.}"
_CFG="${_CLONE}/.claude/skills/refinement/config.yaml"
_SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"

# Single parse — avoids mode/budget desync if config is partially malformed.
# Scenario is passed as a python argv (not string-interpolated) so the only
# shell-interpolated string is the config path, same as the old inline blocks.
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
