#!/usr/bin/env bash
# Verifies the epic_autopilot config section + its scheduler wiring, which since #180 is the
# effective_config.ENV_WHITELIST row scheduler.sh read_config applies (no per-knob yq call).
# Run: bash tests/test_epic_autopilot_config.sh   (needs python3 + pyyaml; listed in CI)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cfg="$HERE/../config/config.yaml"
ec="$HERE/../scripts/factory_core/effective_config.py"

# _get <dotted.key> — print a config.yaml value the way the old yq read did (bools lowercase).
_get() {
  python3 -c 'import sys, yaml
v = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
for k in sys.argv[2].split("."):
    v = v[k]
print(str(v).lower() if isinstance(v, bool) else v)' "$cfg" "$1"
}

val=$(_get epic_autopilot.enabled)
[ "$val" = "false" ] || { echo "FAIL: epic_autopilot.enabled should default false, got '$val'"; exit 1; }

cap=$(_get epic_autopilot.daily_cap)
[ "$cap" = "5" ] || { echo "FAIL: epic_autopilot.daily_cap should be 5, got '$cap'"; exit 1; }

model=$(_get epic_autopilot.model)
[ "$model" = "claude-opus-5-5" ] || { echo "FAIL: epic_autopilot.model should be claude-opus-5-5, got '$model'"; exit 1; }

floor=$(_get epic_autopilot.confidence_floor)
[ "$floor" = "0.7" ] || { echo "FAIL: epic_autopilot.confidence_floor should be 0.7, got '$floor'"; exit 1; }

asi=$(_get epic_autopilot.allow_self_improvement)
[ "$asi" = "true" ] || { echo "FAIL: epic_autopilot.allow_self_improvement should default true, got '$asi'"; exit 1; }

# The scheduler wires the knobs through effective_config.ENV_WHITELIST
grep -qE '"EPIC_AUTOPILOT_ENABLED", "epic_autopilot\.enabled"' "$ec" \
  || { echo "FAIL: ENV_WHITELIST missing EPIC_AUTOPILOT_ENABLED wiring"; exit 1; }
grep -qE '"EPIC_AUTOPILOT_ALLOW_SELF_IMPROVEMENT", "epic_autopilot\.allow_self_improvement"' "$ec" \
  || { echo "FAIL: ENV_WHITELIST missing EPIC_AUTOPILOT_ALLOW_SELF_IMPROVEMENT wiring"; exit 1; }
grep -qE '"EPIC_AUTOPILOT_DAILY_CAP", "epic_autopilot\.daily_cap"' "$ec" \
  || { echo "FAIL: ENV_WHITELIST missing EPIC_AUTOPILOT_DAILY_CAP wiring"; exit 1; }

echo "PASS"
