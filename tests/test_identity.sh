#!/usr/bin/env bash
set -euo pipefail
# 1) defaults match today's literals
unset FACTORY_OWNER FACTORY_REPO FACTORY_PROJECT_ID FACTORY_PRODUCT_NAME || true
source scripts/identity.sh
[ "$FACTORY_OWNER" = "omniscient" ] || { echo "FAIL owner default"; exit 1; }
[ "$FACTORY_REPO" = "markethawk" ] || { echo "FAIL repo default"; exit 1; }
[ "$FACTORY_REPO_SLUG" = "omniscient/markethawk" ] || { echo "FAIL slug"; exit 1; }
[ "$FACTORY_STATUS_DONE" = "98236657" ] || { echo "FAIL status ids"; exit 1; }
[ "$FACTORY_PRODUCT_NAME" = "MarketHawk" ] || { echo "FAIL product name"; exit 1; }
# 2) env wins
FACTORY_OWNER=acme FACTORY_REPO=widgets bash -c '
  source scripts/identity.sh
  [ "$FACTORY_REPO_SLUG" = "acme/widgets" ] || exit 1'
# 3) no hardcoded slug remains in the three shell entrypoints outside identity defaults
! grep -n "omniscient/markethawk" scheduler.sh entrypoint.sh smoke_gate.sh || { echo "FAIL residual slug"; exit 1; }
# 4) no orphaned pre-identity variable names remain (undefined under set -u)
! grep -nE '\$\{?(STATUS_(READY|IN_PROGRESS|IN_REVIEW|BLOCKED|DONE|BACKLOG|REFINED)|PROJECT_ID|STATUS_FIELD)\b' scheduler.sh entrypoint.sh smoke_gate.sh || { echo "FAIL orphaned pre-identity variable"; exit 1; }
echo PASS
