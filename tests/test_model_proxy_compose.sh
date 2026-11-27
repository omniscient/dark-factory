#!/usr/bin/env bash
set -euo pipefail
# Tests for #208: factory-model-proxy service is opt-in via a distinct compose
# profile and does not change default (flag-off) behavior.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RC="$REPO_ROOT/run-compose.yml"

grep -q "factory-model-proxy:" "$RC" || { echo "FAIL: factory-model-proxy service missing from run-compose.yml"; exit 1; }
grep -q "profiles:" "$RC" || { echo "FAIL: no profiles block found"; exit 1; }

if ! command -v docker &>/dev/null || ! docker compose version &>/dev/null 2>&1; then
  echo "SKIP: docker not available — skipping compose config parse checks"
  echo "PASS: test_model_proxy_compose.sh (partial — grep checks only)"
  exit 0
fi

mkdir -p "$REPO_ROOT/.archon"
SCRATCH_ENV=0
[ -f "$REPO_ROOT/.archon/.env" ] || { touch "$REPO_ROOT/.archon/.env"; SCRATCH_ENV=1; }

# 1) Flag off (default): factory-model-proxy must NOT actually start for the
#    profile set scheduler.sh dispatches with. `config --services` lists only
#    the services that would actually run — unlike `config`'s full YAML dump,
#    it isn't tripped up by the dark-factory service's own (inert, required:
#    false) `depends_on: factory-model-proxy` reference.
OFF_SERVICES=$(docker compose -f "$RC" --profile factory config --services 2>&1)
if echo "$OFF_SERVICES" | grep -qx "factory-model-proxy"; then
  echo "FAIL: factory-model-proxy present in config with --profile factory alone (must be opt-in)"
  [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"
  exit 1
fi
echo "PASS: factory-model-proxy absent from default (--profile factory) config"

# 2) Flag on: adding --profile factory-model-proxy must include the service.
ON_SERVICES=$(docker compose -f "$RC" --profile factory --profile factory-model-proxy config --services 2>&1)
if ! echo "$ON_SERVICES" | grep -qx "factory-model-proxy"; then
  echo "FAIL: factory-model-proxy absent even with --profile factory-model-proxy set"
  [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"
  exit 1
fi
echo "PASS: factory-model-proxy present when its profile is explicitly enabled"
# 3) helper_proxy_absent_without_its_profile (#634, regression guard): preview-helper-proxy
#    is never selected by the profile sets scheduler.sh dispatches with — dark-factory does
#    not pull it in — and appears only under its own profile.
for profiles in "" "--profile factory" "--profile factory --profile factory-model-proxy"; do
  # shellcheck disable=SC2086
  SERVICES=$(docker compose -f "$RC" $profiles config --services 2>&1)
  if echo "$SERVICES" | grep -qx "preview-helper-proxy"; then
    echo "FAIL: preview-helper-proxy selected by profiles [${profiles:-none}] (must be opt-in)"
    [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"
    exit 1
  fi
done
HELPER_SERVICES=$(docker compose -f "$RC" --profile preview-helper-proxy config --services 2>&1)
if [ "$HELPER_SERVICES" != "preview-helper-proxy" ]; then
  echo "FAIL: --profile preview-helper-proxy must select exactly preview-helper-proxy"
  [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"
  exit 1
fi
echo "PASS: preview-helper-proxy present only under its own profile"

[ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"
echo "PASS: test_model_proxy_compose.sh"
