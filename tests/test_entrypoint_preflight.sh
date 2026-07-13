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

echo "PASS"
