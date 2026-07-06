#!/usr/bin/env bash
set -euo pipefail
# Tests for F1: run-compose.yml exists, parses, and contains all FACTORY_* identity vars.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RC="$REPO_ROOT/run-compose.yml"

# 1) File must exist in-repo
[ -f "$RC" ] || { echo "FAIL: run-compose.yml missing from repo root"; exit 1; }

# 2) Every FACTORY_* identity var from identity.sh must appear in the file
for var in FACTORY_OWNER FACTORY_REPO FACTORY_PROJECT_ID FACTORY_PROJECT_NUMBER \
           FACTORY_STATUS_FIELD FACTORY_STATUS_READY FACTORY_STATUS_IN_PROGRESS \
           FACTORY_STATUS_IN_REVIEW FACTORY_STATUS_BLOCKED FACTORY_STATUS_DONE \
           FACTORY_STATUS_BACKLOG FACTORY_STATUS_REFINED \
           FACTORY_PRODUCT_NAME FACTORY_CLONE_DIR FACTORY_RUN_PREFIX FACTORY_IMAGE; do
  grep -q "$var" "$RC" || { echo "FAIL: $var missing from run-compose.yml"; exit 1; }
done

# 3) Profiles block must declare 'factory'
grep -q "factory" "$RC" || { echo "FAIL: 'factory' profile not found in run-compose.yml"; exit 1; }

# 3b) Sentinel-seam lockstep: the state-volume name expression must be IDENTICAL
# in run-compose.yml and deploy/docker-compose.yml. Run containers write the
# main-red sentinel to /var/lib/dark-factory; the scheduler reads it from the
# same volume — a name drift silently breaks the red-main latch.
DC="$REPO_ROOT/deploy/docker-compose.yml"
RC_VOL=$(grep -oE 'name: \$\{FACTORY_INSTANCE:-dark-factory\}-scheduler-state' "$RC" | head -1)
DC_VOL=$(grep -oE 'name: \$\{FACTORY_INSTANCE:-dark-factory\}-scheduler-state' "$DC" | head -1)
[ -n "$RC_VOL" ] || { echo "FAIL: run-compose.yml missing instance-scoped scheduler-state volume name"; exit 1; }
[ "$RC_VOL" = "$DC_VOL" ] || { echo "FAIL: state-volume name expression drifted between run-compose.yml and deploy/docker-compose.yml"; exit 1; }

# 3c) Both compose files must scope the project name by FACTORY_INSTANCE
grep -q '^name: ${FACTORY_INSTANCE:-dark-factory}$' "$RC" || { echo "FAIL: run-compose.yml project name not FACTORY_INSTANCE-scoped"; exit 1; }
grep -q '^name: ${FACTORY_INSTANCE:-dark-factory}$' "$DC" || { echo "FAIL: deploy compose project name not FACTORY_INSTANCE-scoped"; exit 1; }

# 4) Parse check via docker compose config (skipped if docker not available)
if command -v docker &>/dev/null && docker compose version &>/dev/null 2>&1; then
  # Provision a scratch .archon/.env so the required env_file check passes
  mkdir -p "$REPO_ROOT/.archon"
  SCRATCH_ENV=0
  [ -f "$REPO_ROOT/.archon/.env" ] || { touch "$REPO_ROOT/.archon/.env"; SCRATCH_ENV=1; }
  if docker compose -f "$RC" config > /dev/null 2>&1; then
    echo "PASS: docker compose config parses run-compose.yml"
  else
    echo "FAIL: docker compose config failed for run-compose.yml"
    [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"
    exit 1
  fi
  [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"
else
  echo "SKIP: docker not available — skipping compose config parse check"
fi

echo "PASS: test_run_compose.sh"
