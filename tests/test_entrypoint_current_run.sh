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

# --- #196: FACTORY_SIDE_EFFECT_LEVEL is computed from the BAKED config/scripts only (F1) ---
# The function under test takes FACTORY_CONFIG_PATH / FACTORY_SCRIPTS_DIR overrides (defaults
# /opt/dark-factory/config/config.yaml and /opt/dark-factory/scripts, which do not exist on a
# bare CI checkout) -- same pattern as IDENTITY_SH / FACTORY_PROVIDERS_CLI above.
TMP_SE=$(mktemp -d /tmp/196-clone-XXXXXX)
SE_SCRIPTS=$(mktemp -d /tmp/196-scripts-XXXXXX)
SE_LOG="$SE_SCRIPTS/compute.log"
mkdir -p "$SE_SCRIPTS/factory_core" "$SE_SCRIPTS/shims"
cp "$SCRIPT_DIR/../scripts/factory_core/side_effect.py" "$SE_SCRIPTS/factory_core/"
SE_CFG_WITH="$SE_SCRIPTS/config-with-block.yaml"
SE_CFG_WITHOUT="$SE_SCRIPTS/config-without-block.yaml"
cat > "$SE_CFG_WITH" <<'EOF'
side_effect:
  phase_levels:
    plan: 4
    implement: 5
    validate: 5
    conformance: 5
    code_review: 5
    revise_advisory: 5
EOF
printf 'scheduler:\n  factory_wip_limit: 1\n' > "$SE_CFG_WITHOUT"
# A clone-resident config (the MarketHawk transition layout) claiming a HIGHER level than
# the baked one for 'plan': must be ignored -- the clone is never consulted (spec Trust model).
mkdir -p "$TMP_SE/.claude/skills/refinement"
printf 'side_effect:\n  phase_levels:\n    plan: 5\n' > "$TMP_SE/.claude/skills/refinement/config.yaml"
export FACTORY_SCRIPTS_DIR="$SE_SCRIPTS"
export FACTORY_CONFIG_PATH="$SE_CFG_WITH"

ARTIFACTS_DIR=$(mktemp -d /tmp/208-artifacts-XXXXXX)
export ARTIFACTS_DIR

# Case A: baked config WITH the block, multi-phase intent 'fix' -> 5. entrypoint.sh's own
# $INTENT vocabulary uses "fix" (not the DAG's "new") for a first-time implement dispatch --
# exactly the mismatch architect review (cycle 3) caught: intent_phases() must be fed
# entrypoint.sh's actual $INTENT or every real `Fix issue #N` run silently resolves to
# level 1 and the shim denies every git/gh call the implement phase needs.
FACTORY_CLONE_DIR="$TMP_SE" ARGUMENTS="Fix issue #1" \
  ENTRYPOINT_SOURCE_ONLY=1 source "$SCRIPT_DIR/../entrypoint.sh" "Fix issue #1"
trap - ERR
set +e; set +u; set +o pipefail

# Called directly, NOT in a $(...) subshell: the exports must land in this shell.
_compute_side_effect_level >"$SE_LOG" 2>&1
assert_true "#196 multi-phase intent 'fix' -> level 5 from the baked config (not 1)" "[ '$FACTORY_SIDE_EFFECT_LEVEL' = '5' ]"
assert_true "#196 profile version comes from render (v1)" "[ '$FACTORY_SIDE_EFFECT_PROFILE_VERSION' = 'v1' ]"
assert_true "#196 log line names the phase set" "grep -q 'phases=implement validate conformance code-review revise-advisory' '$SE_LOG'"
assert_true "#196 baked shim dir is prepended to PATH" "case ':$PATH:' in *':$SE_SCRIPTS/shims:'*) true;; *) false;; esac"

# Case B: 'plan' -> baked says 4, the clone-resident file says 5 -> 4 (clone never consulted).
unset FACTORY_SIDE_EFFECT_LEVEL FACTORY_SIDE_EFFECT_PROFILE_VERSION
FACTORY_CLONE_DIR="$TMP_SE" ARGUMENTS="Plan issue #1" \
  ENTRYPOINT_SOURCE_ONLY=1 source "$SCRIPT_DIR/../entrypoint.sh" "Plan issue #1"
trap - ERR
set +e; set +u; set +o pipefail
_compute_side_effect_level >"$SE_LOG" 2>&1
assert_true "#196 clone-resident config.yaml is ignored (baked 4 wins over clone 5)" "[ '$FACTORY_SIDE_EFFECT_LEVEL' = '4' ]"

# Case C: baked config WITHOUT the block -> 1 with a warning (D4, fail closed).
unset FACTORY_SIDE_EFFECT_LEVEL FACTORY_SIDE_EFFECT_PROFILE_VERSION
export FACTORY_CONFIG_PATH="$SE_CFG_WITHOUT"
_compute_side_effect_level >"$SE_LOG" 2>&1
assert_true "#196 missing side_effect block -> level 1 (fail closed)" "[ '$FACTORY_SIDE_EFFECT_LEVEL' = '1' ]"
assert_true "#196 missing block logs a 'defaulting to 1' warning" "grep -q 'defaulting to 1' '$SE_LOG'"

unset FACTORY_CONFIG_PATH FACTORY_SCRIPTS_DIR
rm -rf "$TMP_SE" "$SE_SCRIPTS" "$ARTIFACTS_DIR"
set -uo pipefail

# Cleanup — nothing outside scratch dirs was ever touched
rm -rf "$ARTIFACTS_DIR" "$SCRATCH_STATE" "$CURRENT_RUN_DIR"

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
