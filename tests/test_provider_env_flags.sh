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

# #557: model_registry_env_flags is a no-op unless FACTORY_MODEL_ENDPOINT is set; then it
# stages, exports the digest for the caller's scope only and appends the flag by name. A
# failed or empty stage returns 1 and appends nothing.
FACTORY_PROVIDERS_CLI=/fake/providers/cli.py
CALLS=$(mktemp)
STAGE_OUT="$(printf 'b%.0s' {1..64})"; STAGE_RC=0
python3() { echo "$*" >> "$CALLS"; printf '%s\n' "$STAGE_OUT"; return "$STAGE_RC"; }
run_case() {  # prints <rc>|<flags>|<digest as a child process sees it>
  local -x FACTORY_MODEL_REGISTRY_SHA256
  local -a flags=(-e FACTORY_TRACKER)
  local rc=0
  model_registry_env_flags flags || rc=$?
  printf '%s|%s|%s' "$rc" "${flags[*]}" "$(bash -c 'echo "${FACTORY_MODEL_REGISTRY_SHA256:-unset}"')"
}

unset FACTORY_MODEL_ENDPOINT FACTORY_MODEL_REGISTRY_SHA256
out=$(run_case)
[ "$out" = "0|-e FACTORY_TRACKER|unset" ] || { echo "FAIL: registry flags with no endpoint: '$out'"; exit 1; }
[ ! -s "$CALLS" ] || { echo "FAIL: staged with no endpoint selected"; exit 1; }

export FACTORY_MODEL_ENDPOINT=cloud-anthropic
out=$(run_case)
[ "$out" = "0|-e FACTORY_TRACKER -e FACTORY_MODEL_REGISTRY_SHA256|$STAGE_OUT" ] \
  || { echo "FAIL: registry flags with an endpoint: '$out'"; exit 1; }
[ "$(cat "$CALLS")" = "/fake/providers/cli.py model-endpoints stage" ] \
  || { echo "FAIL: unexpected staging call: '$(cat "$CALLS")'"; exit 1; }
[ "${FACTORY_MODEL_REGISTRY_SHA256:-unset}" = "unset" ] \
  || { echo "FAIL: digest leaked out of the caller's scope"; exit 1; }

STAGE_RC=1
out=$(run_case)
[ "${out%%|*}" = "1" ] && [ "$(echo "$out" | cut -d'|' -f2)" = "-e FACTORY_TRACKER" ] \
  || { echo "FAIL: failed staging must refuse and append nothing: '$out'"; exit 1; }
STAGE_RC=0 STAGE_OUT=""
out=$(run_case)
[ "${out%%|*}" = "1" ] || { echo "FAIL: empty digest must refuse the dispatch: '$out'"; exit 1; }
rm -f "$CALLS"
echo "PASS: test_provider_env_flags.sh"
