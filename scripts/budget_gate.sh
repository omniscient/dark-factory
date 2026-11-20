#!/usr/bin/env bash
# Budget gate — reads token_optimization config, derives enforce/observe mode,
# applies the TOKEN_OPTIMIZATION_ENFORCE_BUDGETS kill-switch, and invokes
# budget_enforce.py. One script backs all 6 enforce-budget-* workflow nodes
# (refine/plan/implement/conformance/docs-conformance/code-review) — a faithful extraction of
# logic that used to be duplicated inline per scenario (#183). Mode and budget come from
# the run's materialized run-config.json (#180) — the same tree entrypoint.sh writes to the
# clone-side config.yaml that budget_enforce.py still reads via --config.
#
# Usage: budget_gate.sh <scenario>
# Env:   ARTIFACTS_DIR (required) — same contract as context_budget.py/budget_enforce.py
#        FACTORY_RUN_CONFIG_JSON (optional, default $ARTIFACTS_DIR/run-config.json) —
#                   exported by entrypoint.sh: archon gives bash nodes their own
#                   ARTIFACTS_DIR, so in a run this is the path that resolves. Missing or
#                   unparseable → observe mode
#        CLONE_DIR (optional, default ".") — clone root; CWD is the clone root in
#                   bash workflow nodes, so "." is the correct default there
#        TOKEN_OPTIMIZATION_ENFORCE_BUDGETS (optional) — kill-switch override;
#                   false|0|no forces observe mode, can never force enforce ON
#
# Exit codes (for standalone testability only — callers wrap this call in `|| true`
# to preserve the DAG's fail-open semantics; this script does not change that):
#   0 = ran to completion (observe mode, enforce-mode green, or enforce-mode
#       over-budget — budget_enforce.py itself only fails on malformed input)
#   1 = budget_enforce.py hard failure (unreadable/malformed context-budget.<scenario>.json)
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
_RUN_CFG="${FACTORY_RUN_CONFIG_JSON:-$ARTIFACTS_DIR/run-config.json}"
[ -f "$_RUN_CFG" ] || echo "budget_gate.sh: WARNING — ${_RUN_CFG} not found; observe mode" >&2

# Single parse — avoids mode/budget desync if config is partially malformed. Same
# defaults as the #183 python reader: enforce_budgets/enforce.<s> false, budget
# budgets.<s> → default_budget_tokens → 30000. Scenario is a jq --arg, never interpolated.
read -r _EB _ES _BUD < <(jq -r --arg s "$SCENARIO" '
  (.token_optimization // {}) as $to
  | [ ($to.enforce_budgets // false | tostring | ascii_downcase),
      (($to.enforce // {})[$s] // false | tostring | ascii_downcase),
      (($to.budgets // {})[$s] // $to.default_budget_tokens // 30000 | tostring) ]
  | join(" ")' "$_RUN_CFG" 2>/dev/null || echo "false false 30000")

if [ "$_EB" = "true" ] && [ "$_ES" = "true" ]; then _MODE="enforce"; else _MODE="observe"; fi
_EENV="${TOKEN_OPTIMIZATION_ENFORCE_BUDGETS:-}"
case "${_EENV,,}" in false|0|no) _MODE="observe" ;; esac

python3 "${_SCRIPT_DIR}/budget_enforce.py" \
  --context-budget-json "$ARTIFACTS_DIR/context-budget.${SCENARIO}.json" \
  --budget-tokens "${_BUD:-30000}" --mode "$_MODE" --config "$_CFG" \
  > "$ARTIFACTS_DIR/token-opt-caps.env"  # truncate (not append) — clears stale caps from prior enforce runs
