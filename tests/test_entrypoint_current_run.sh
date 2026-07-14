#!/usr/bin/env bash
# Test: entrypoint.sh writes $CURRENT_RUN_DIR/current-run.json after RUN_ID
# generation, for factory-model-proxy correlation (issue #208).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

export GH_TOKEN="stub-token"
export CLAUDE_CODE_OAUTH_TOKEN="stub-token"

# entrypoint.sh hardcodes /opt/dark-factory/scripts/* for these two, which only
# exist in the built container image. Point both at this checkout's own copies
# so the test runs on a bare CI checkout with no /opt/dark-factory present.
export IDENTITY_SH="$SCRIPT_DIR/../scripts/identity.sh"
export FACTORY_PROVIDERS_CLI="$SCRIPT_DIR/../scripts/factory_core/providers/cli.py"

git() { return 0; }
export -f git
gh() { echo "stub-title"; return 0; }
export -f gh
docker() { return 0; }
export -f docker

# Redirect the shared state dir to a scratch location — this test must never
# touch the real /var/lib/dark-factory.
SCRATCH_STATE=$(mktemp -d /tmp/208-state-XXXXXX)
mkdir -p "$SCRATCH_STATE"

# entrypoint.sh honors CURRENT_RUN_DIR (default /var/lib/dark-factory, which is
# unwritable on CI runners) for the current-run.json write — point it at a
# scratch dir so this test never touches the real shared path.
CURRENT_RUN_DIR=$(mktemp -d /tmp/208-run-XXXXXX)
export CURRENT_RUN_DIR

ARTIFACTS_DIR=$(mktemp -d /tmp/208-artifacts-XXXXXX)
export ARTIFACTS_DIR

PASSED=0; FAILED=0
assert_true() {
  local desc="$1" condition="$2"
  if eval "$condition"; then echo "  PASS: $desc"; PASSED=$((PASSED + 1))
  else echo "  FAIL: $desc" >&2; FAILED=$((FAILED + 1)); fi
}
stage_of() {
  python3 -c "import json; print(json.load(open('$CURRENT_RUN_DIR/current-run.json')).get('stage','missing'))" 2>/dev/null
}

echo "=== #208: entrypoint writes current-run.json ==="

# Case 1: single-phase intent ("plan") — stage must be the exact phase name.
ARTIFACTS_DIR=$(mktemp -d /tmp/208-artifacts-XXXXXX)
export ARTIFACTS_DIR
ENTRYPOINT_SOURCE_ONLY=1 ARGUMENTS="Plan issue #208" \
  source "$SCRIPT_DIR/../entrypoint.sh" "Plan issue #208"

trap - ERR
set +e; set +u; set +o pipefail

assert_true "current-run.json exists" "[ -f '$CURRENT_RUN_DIR/current-run.json' ]"

ISSUE_FIELD=$(python3 -c "import json; print(json.load(open('$CURRENT_RUN_DIR/current-run.json')).get('issue_number','missing'))" 2>/dev/null)
assert_true "issue_number is 208" "[ '$ISSUE_FIELD' = '208' ]"

RUN_ID_FIELD=$(python3 -c "import json; print(json.load(open('$CURRENT_RUN_DIR/current-run.json')).get('run_id','missing'))" 2>/dev/null)
assert_true "run_id is non-empty" "[ -n '$RUN_ID_FIELD' ] && [ '$RUN_ID_FIELD' != 'missing' ]"

STAGE_FIELD=$(stage_of)
assert_true "single-phase intent 'plan' -> stage='plan'" "[ '$STAGE_FIELD' = 'plan' ]"

rm -rf "$ARTIFACTS_DIR"

# Case 2: multi-phase intent ("fix") — stage must honestly degrade to 'unknown'.
set -uo pipefail
ARTIFACTS_DIR=$(mktemp -d /tmp/208-artifacts-XXXXXX)
export ARTIFACTS_DIR
ENTRYPOINT_SOURCE_ONLY=1 ARGUMENTS="Fix issue #208" \
  source "$SCRIPT_DIR/../entrypoint.sh" "Fix issue #208"

trap - ERR
set +e; set +u; set +o pipefail

STAGE_FIELD=$(stage_of)
assert_true "multi-phase intent 'fix' -> stage='unknown'" "[ '$STAGE_FIELD' = 'unknown' ]"

# Cleanup — nothing outside scratch dirs was ever touched
rm -rf "$ARTIFACTS_DIR" "$SCRATCH_STATE" "$CURRENT_RUN_DIR"

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
