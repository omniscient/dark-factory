#!/usr/bin/env bash
set -euo pipefail
# #489: provider_env_flags emits `-e NAME` only for forwarded vars that are set and non-empty,
# and never emits values.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_ROOT/scripts/provider_env.sh"

unset FACTORY_TRACKER JIRA_TOKEN JIRA_BASE_URL GITLAB_TOKEN
out=$(provider_env_flags | tr '\n' ' ')
[ -z "${out// /}" ] || { echo "FAIL: nothing set should emit no flags, got: $out"; exit 1; }

export FACTORY_TRACKER=jira JIRA_TOKEN=secret JIRA_BASE_URL=
out=$(provider_env_flags | tr '\n' ' ')
[ "$out" = "-e FACTORY_TRACKER -e JIRA_TOKEN " ] || { echo "FAIL: unexpected flags: '$out'"; exit 1; }

echo "PASS: test_provider_env_flags.sh"
