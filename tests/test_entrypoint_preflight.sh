#!/usr/bin/env bash
# Verifies entrypoint.sh's provider-aware preflight (parent spec §4) replaced
# the old inline GH_TOKEN / CLAUDE_CODE_OAUTH_TOKEN checks, and that it still
# runs before the repo clone.
# Run: bash tests/test_entrypoint_preflight.sh
set -euo pipefail
ep="$(cd "$(dirname "$0")" && pwd)/../entrypoint.sh"

grep -q 'FACTORY_PROVIDERS_CLI" preflight' "$ep" \
  || { echo "FAIL: entrypoint does not call providers preflight"; exit 1; }

if grep -qE '^\s*if \[ -z "\$\{GH_TOKEN:-\}" \]; then' "$ep"; then
  echo "FAIL: inline GH_TOKEN check was not removed"; exit 1
fi
if grep -q 'CLAUDE_CODE_OAUTH_TOKEN:-}" ] && [ -z "${ANTHROPIC_API_KEY' "$ep"; then
  echo "FAIL: inline CLAUDE_CODE_OAUTH_TOKEN/ANTHROPIC_API_KEY check was not removed"; exit 1
fi

preflight_ln=$(grep -n 'preflight' "$ep" | head -1 | cut -d: -f1)
clone_ln=$(grep -n '^git clone "\$REPO_URL"' "$ep" | head -1 | cut -d: -f1)
[ -n "$preflight_ln" ] && [ -n "$clone_ln" ] && [ "$preflight_ln" -lt "$clone_ln" ] \
  || { echo "FAIL: preflight ($preflight_ln) not before git clone ($clone_ln)"; exit 1; }

# --- #395: _check_ledger_writable, exercised live. It sits above the
# ENTRYPOINT_SOURCE_ONLY early return (entrypoint.sh:~644), so it already ran once
# against the REAL SCHEDULER_STATE_DIR during the source below -- before this test's
# scratch dir exists. Re-invoke it explicitly afterwards against a scratch
# SCHEDULER_STATE_DIR (the function stays in scope post-source) rather than relying on
# that source-time run.
_REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export IDENTITY_SH="${IDENTITY_SH:-$_REPO_DIR/scripts/identity.sh}"
export FACTORY_PROVIDERS_CLI="${FACTORY_PROVIDERS_CLI:-$_REPO_DIR/scripts/factory_core/providers/cli.py}"
export GH_TOKEN="stub-token"
export CLAUDE_CODE_OAUTH_TOKEN="stub-token"

git() { return 0; }
export -f git
gh() { echo "stub-title"; return 0; }
export -f gh
docker() { return 0; }
export -f docker
claude() { echo "stub"; return 0; }
export -f claude

CURRENT_RUN_DIR=$(mktemp -d /tmp/ep-preflight-rundir-XXXXXX)
export CURRENT_RUN_DIR

ENTRYPOINT_SOURCE_ONLY=1 source "$_REPO_DIR/entrypoint.sh"
trap - ERR
set +e; set +u; set +o pipefail

if [ "$(type -t _check_ledger_writable)" != "function" ]; then
  echo "FAIL: _check_ledger_writable not defined after sourcing entrypoint.sh"; exit 1
fi
echo "PASS: _check_ledger_writable is defined after sourcing entrypoint.sh"

SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-preflight-statedir-XXXXXX)
export SCHEDULER_STATE_DIR
LEDGER="$SCHEDULER_STATE_DIR/runs.jsonl"

# No ledger at all -> never a warning.
LEDGER_WRITE_WARNING=""
_check_ledger_writable
[ -z "$LEDGER_WRITE_WARNING" ] \
  || { echo "FAIL: LEDGER_WRITE_WARNING set when the ledger doesn't exist"; exit 1; }
echo "PASS: LEDGER_WRITE_WARNING stays empty when the ledger doesn't exist"

: > "$LEDGER"
if [ "$(id -u)" -eq 0 ]; then
  echo "SKIP: running as root -- chmod 0444 has no effect, can't exercise the unwritable case"
else
  chmod 0444 "$LEDGER"
  LEDGER_WRITE_WARNING=""
  # Capture stderr via redirection to a file, NOT `$(... 2>&1)` -- a command
  # substitution runs the function in a subshell, so the global it sets
  # (LEDGER_WRITE_WARNING) would never escape back to this shell.
  ERRF=$(mktemp)
  _check_ledger_writable 2>"$ERRF"
  WARN_OUT=$(cat "$ERRF")
  rm -f "$ERRF"
  [ -n "$LEDGER_WRITE_WARNING" ] \
    || { echo "FAIL: LEDGER_WRITE_WARNING not set for an unwritable ledger"; exit 1; }
  echo "$WARN_OUT" | grep -q '^WARNING: ledger not writable' \
    || { echo "FAIL: expected a WARNING line on stderr, got: $WARN_OUT"; exit 1; }
  echo "PASS: LEDGER_WRITE_WARNING set and WARNING printed for an unwritable ledger"
fi

# Writable ledger -> never a warning.
chmod 0644 "$LEDGER" 2>/dev/null || true
LEDGER_WRITE_WARNING=""
_check_ledger_writable
[ -z "$LEDGER_WRITE_WARNING" ] \
  || { echo "FAIL: LEDGER_WRITE_WARNING set for a writable ledger"; exit 1; }
echo "PASS: LEDGER_WRITE_WARNING stays empty when the ledger is writable"

# --- #395 Requirement 5: the warning threads into both existing failure-comment
# bodies (static text check -- avoids the hermetic-guard/network complexity of
# actually invoking on_failure(), see tests/test_run_record_hermetic.sh).
refine_body=$(awk '/post_or_update_comment "\$REFINE_FAILURE_MARKER"/,/^\$\{FOOTER\}"$/' "$ep")
echo "$refine_body" | grep -q 'LEDGER_NOTE' \
  || { echo "FAIL: REFINE_FAILURE_MARKER body does not reference LEDGER_NOTE"; exit 1; }
# Position check, not just presence: LEDGER_NOTE must land after the retry command,
# not spliced into the middle of the fenced retry block (Requirement 5).
retry_ln=$(echo "$refine_body" | grep -n 'docker compose --profile factory run --rm dark-factory' | head -1 | cut -d: -f1)
ledger_ln=$(echo "$refine_body" | grep -n 'LEDGER_NOTE' | head -1 | cut -d: -f1)
[ -n "$retry_ln" ] && [ -n "$ledger_ln" ] && [ "$ledger_ln" -gt "$retry_ln" ] \
  || { echo "FAIL: LEDGER_NOTE in REFINE_FAILURE_MARKER body is not after the retry command"; exit 1; }
echo "PASS: REFINE_FAILURE_MARKER body includes LEDGER_NOTE after the retry block"

factory_body=$(awk '/post_or_update_comment "\$FACTORY_FAILURE_MARKER"/,/^\$\{FOOTER\}"$/' "$ep")
echo "$factory_body" | grep -q 'LEDGER_NOTE' \
  || { echo "FAIL: FACTORY_FAILURE_MARKER body does not reference LEDGER_NOTE"; exit 1; }
retry_ln=$(echo "$factory_body" | grep -n 'docker compose --profile factory run --rm dark-factory' | head -1 | cut -d: -f1)
ledger_ln=$(echo "$factory_body" | grep -n 'LEDGER_NOTE' | head -1 | cut -d: -f1)
[ -n "$retry_ln" ] && [ -n "$ledger_ln" ] && [ "$ledger_ln" -gt "$retry_ln" ] \
  || { echo "FAIL: LEDGER_NOTE in FACTORY_FAILURE_MARKER body is not after the retry command"; exit 1; }
echo "PASS: FACTORY_FAILURE_MARKER body includes LEDGER_NOTE after the retry block"

echo "PASS"
