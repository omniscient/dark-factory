#!/usr/bin/env bash
# Entry point of the preview-helper container (#642; dispatched by #587). NOT entrypoint.sh: no tracker or
# code-host preflight, no clone, no git, gh or docker, no file from a target repository.
# One argument, two endpoint checks on the baked CLI, one model call with no tools, one
# result file. Spec: docs/superpowers/specs/2026-11-27-preview-helper-container-inert-pieces-design.md
set -uo pipefail
shopt -u nocasematch

INTENT="classify-preview-helper"
PREFLIGHT_SECONDS=60          # bound on each baked-CLI call below
EXIT_MARGIN_SECONDS=5         # the model call ends this long before the limit, so result.json
                              # is written before the scheduler's enforcer acts at the limit
SCRIPTS_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
PROVIDERS_CLI="$SCRIPTS_DIR/factory_core/providers/cli.py"
HELPER_PY="$SCRIPTS_DIR/factory_core/preview_helper.py"

# 1. Exactly one argument, the whole command string #633 defines. Before any check.
if [ "$#" -ne 1 ] || ! [[ "$1" =~ ^Classify\ preview\ \(helper\)\ \#[0123456789]+$ ]]; then
  echo "preview-helper: expected exactly one argument: 'Classify preview (helper) #<digits>'" >&2
  exit 2
fi

DIR="${CURRENT_RUN_DIR:?CURRENT_RUN_DIR is not set}"
RESULT="$DIR/result.json"
START=$SECONDS
WORK=$(mktemp -d /tmp/preview-helper.XXXXXX) || exit 3
cd "$WORK" || exit 3          # an empty directory: nothing for the CLI to pick up

finish() {  # finish <ok|abstained> [failure_class] [answer-file]
  python3 "$HELPER_PY" write-result --file "$RESULT" --outcome "$1" \
    ${2:+--failure-class "$2"} ${3:+--answer-file "$3"} || exit 3
  echo "preview-helper: outcome=$1 failure_class=${2:--}"
  exit 0
}

# 2. The two endpoint checks #634 requires, each bounded. A refusal is preflight_refused.
if ! timeout -k 5 "$PREFLIGHT_SECONDS" python3 "$PROVIDERS_CLI" model-endpoints endpoint-preflight \
      --intent "$INTENT" >"$WORK/preflight.out" 2>"$WORK/preflight.err"; then
  sed 's/^/preview-helper: /' "$WORK/preflight.err" >&2
  finish abstained preflight_refused
fi
HOSTING=$(timeout -k 5 "$PREFLIGHT_SECONDS" python3 "$PROVIDERS_CLI" model-endpoints hosting \
  --intent "$INTENT" 2>/dev/null) || HOSTING=""
[ "$HOSTING" = "hosting=local" ] || finish abstained preflight_refused

# The wall-clock limit, from the digest-verified staged registry (the same bytes the checks
# above accepted). Anything but one positive integer is a refusal.
TOTAL=$(timeout -k 5 "$PREFLIGHT_SECONDS" python3 "$PROVIDERS_CLI" model-endpoints show 2>/dev/null \
  | jq -r --arg id "${FACTORY_MODEL_ENDPOINT:-}" \
      '.endpoints[] | select(.id == $id) | .timeouts.total_seconds | floor' 2>/dev/null) || TOTAL=""
[[ "$TOTAL" =~ ^[1-9][0-9]*$ ]] || finish abstained preflight_refused

# 3. Input: the scheduler's validated copy, nothing else. The prompt is baked text plus four
# of its fields.
if ! python3 "$HELPER_PY" build-prompt --input "$DIR/input.json" >"$WORK/prompt.txt" 2>/dev/null; then
  CLASS=$(head -c 40 "$WORK/prompt.txt")
  [ "$CLASS" = "input_too_large" ] || CLASS=missing_input
  finish abstained "$CLASS"
fi

# 4. One model call, no tools, inside what is left of the limit. The placeholder credential
# and the effort switch exist only in the environment of this one child process.
REMAIN=$(( TOTAL - (SECONDS - START) - EXIT_MARGIN_SECONDS ))
[ "$REMAIN" -gt 0 ] || finish abstained local_timeout_total
RC=0
timeout -k 5 "$REMAIN" env ANTHROPIC_API_KEY=factory-local-placeholder CLAUDE_CODE_EFFORT_LEVEL=unset \
  claude -p "$(cat "$WORK/prompt.txt")" --tools "" \
  >"$WORK/answer.txt" 2>"$WORK/claude.err" </dev/null || RC=$?
[ "$RC" -ne 124 ] && [ "$RC" -ne 137 ] || finish abstained local_timeout_total
[ "$RC" -eq 0 ] || finish abstained model_call_failed

# 5. Strict parse: one JSON object with needs_preview (boolean) and category (enum).
python3 "$HELPER_PY" parse-answer --file "$WORK/answer.txt" >"$WORK/answer.json" 2>/dev/null \
  || finish abstained schema_invalid

# 6. One result file.
finish ok "" "$WORK/answer.json"
