#!/usr/bin/env bash
# Test: entrypoint.sh writes /var/lib/dark-factory/current-run.json after RUN_ID
# generation, for factory-model-proxy correlation (issue #208).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

export GH_TOKEN="stub-token"
export CLAUDE_CODE_OAUTH_TOKEN="stub-token"

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

# entrypoint.sh hardcodes /var/lib/dark-factory for this write (matches the
# shared-volume convention used by runs.jsonl / the main-red sentinel), so this
# test asserts against that path directly and cleans it up afterward — it does
# not create the dir if absent, and removes only the file it wrote.
PRE_EXISTED=0
[ -f /var/lib/dark-factory/current-run.json ] && PRE_EXISTED=1
[ "$PRE_EXISTED" = "1" ] && cp /var/lib/dark-factory/current-run.json /tmp/208-backup.json

ARTIFACTS_DIR=$(mktemp -d /tmp/208-artifacts-XXXXXX)
export ARTIFACTS_DIR

PASSED=0; FAILED=0
assert_true() {
  local desc="$1" condition="$2"
  if eval "$condition"; then echo "  PASS: $desc"; PASSED=$((PASSED + 1))
  else echo "  FAIL: $desc" >&2; FAILED=$((FAILED + 1)); fi
}
stage_of() {
  python3 -c "import json; print(json.load(open('/var/lib/dark-factory/current-run.json')).get('stage','missing'))" 2>/dev/null
}

echo "=== #208: entrypoint writes current-run.json ==="

# Case 1: single-phase intent ("plan") — stage must be the exact phase name.
ARTIFACTS_DIR=$(mktemp -d /tmp/208-artifacts-XXXXXX)
export ARTIFACTS_DIR
ENTRYPOINT_SOURCE_ONLY=1 ARGUMENTS="Plan issue #208" \
  source "$SCRIPT_DIR/../entrypoint.sh" "Plan issue #208"

trap - ERR
set +e; set +u; set +o pipefail

assert_true "current-run.json exists" "[ -f /var/lib/dark-factory/current-run.json ]"

ISSUE_FIELD=$(python3 -c "import json; print(json.load(open('/var/lib/dark-factory/current-run.json')).get('issue_number','missing'))" 2>/dev/null)
assert_true "issue_number is 208" "[ '$ISSUE_FIELD' = '208' ]"

RUN_ID_FIELD=$(python3 -c "import json; print(json.load(open('/var/lib/dark-factory/current-run.json')).get('run_id','missing'))" 2>/dev/null)
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

# Cleanup — restore prior state instead of leaving the scratch write behind
if [ "$PRE_EXISTED" = "1" ]; then
  cp /tmp/208-backup.json /var/lib/dark-factory/current-run.json
  rm -f /tmp/208-backup.json
else
  rm -f /var/lib/dark-factory/current-run.json
fi
rm -rf "$ARTIFACTS_DIR" "$SCRATCH_STATE"

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
