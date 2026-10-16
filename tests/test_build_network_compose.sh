#!/usr/bin/env bash
set -euo pipefail
# Tests for #455: run-compose.yml's optional target build network (build-net) and the
# BUILDKIT_HOST/BUILDKIT_PORT passthrough. Unset = no-op: build-net names the instance's
# own dark-factory-net and BuildKit falls back to buildkit:1234 (preview-up's in-workflow
# defaults). Set = the run joins the target's external build-daemon network.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RC="$REPO_ROOT/run-compose.yml"

grep -q '^  build-net:$' "$RC" || { echo "FAIL: build-net network missing from run-compose.yml"; exit 1; }
grep -qF 'name: ${FACTORY_BUILD_NETWORK:-${FACTORY_INSTANCE:-dark-factory}_dark-factory-net}' "$RC" \
  || { echo "FAIL: build-net name/default expression missing from run-compose.yml"; exit 1; }
grep -qF 'BUILDKIT_HOST: ${BUILDKIT_HOST:-buildkit}' "$RC" || { echo "FAIL: BUILDKIT_HOST passthrough missing"; exit 1; }
grep -qF 'BUILDKIT_PORT: ${BUILDKIT_PORT:-1234}' "$RC" || { echo "FAIL: BUILDKIT_PORT passthrough missing"; exit 1; }

# FACTORY_INSTANCE must be a normalized compose project name ([a-z0-9][a-z0-9_-]*):
# compose normalizes the project name but not build-net's interpolated name, so e.g.
# MarketHawk would point build-net at a network that does not exist (spec: "a test
# pins it"). Reads deploy/instances/** only, never writes it. Silent on success.
for f in "$REPO_ROOT"/deploy/instances/*/instance.env; do
  [ -f "$f" ] || continue
  v=$(sed -n 's/^FACTORY_INSTANCE=//p' "$f")
  [ -z "$v" ] || [[ "$v" =~ ^[a-z0-9][a-z0-9_-]*$ ]] \
    || { echo "FAIL: $f FACTORY_INSTANCE=$v is not a normalized compose project name"; exit 1; }
done

if ! command -v docker &>/dev/null || ! docker compose version &>/dev/null 2>&1; then
  echo "SKIP: docker not available — skipping compose config render checks"
  echo "PASS: test_build_network_compose.sh (partial — grep checks only)"
  exit 0
fi

# Provision a scratch .archon/.env so the required env_file check passes
mkdir -p "$REPO_ROOT/.archon"
SCRATCH_ENV=0
[ -f "$REPO_ROOT/.archon/.env" ] || { touch "$REPO_ROOT/.archon/.env"; SCRATCH_ENV=1; }
WORK=$(mktemp -d)
cleanup() {
  [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"
  rm -rf "$WORK"
}
trap cleanup EXIT

# render <out.json> [VAR=value ...] — the variables under test start unset; no -p override.
render() {
  local out="$1"; shift
  env -u FACTORY_BUILD_NETWORK -u BUILDKIT_HOST -u BUILDKIT_PORT \
      -u FACTORY_INSTANCE -u COMPOSE_PROJECT_NAME "$@" \
    docker compose -f "$RC" --profile factory config --format json > "$out"
}

# assert_shape <label> <json> <build-net name> <dark-factory-net name> <BUILDKIT_HOST> <BUILDKIT_PORT>
assert_shape() {
  local label="$1"; shift
  python3 - "$@" <<'PY' || { echo "FAIL: $label"; exit 1; }
import json, sys
path, build_name, df_name, host, port = sys.argv[1:]
cfg = json.load(open(path))
svc = cfg["services"]["dark-factory"]
nets = cfg.get("networks", {})
errors = []
if sorted(svc.get("networks", {})) != ["build-net", "dark-factory-net"]:
    errors.append(f"service networks = {sorted(svc.get('networks', {}))}, want [build-net, dark-factory-net]")
if sorted(nets) != ["build-net", "dark-factory-net"]:
    errors.append(f"top-level networks = {sorted(nets)}, want [build-net, dark-factory-net]")
bn, dn = nets.get("build-net", {}), nets.get("dark-factory-net", {})
if bn.get("name") != build_name:
    errors.append(f"build-net name = {bn.get('name')!r}, want {build_name!r}")
if bn.get("external") is not True:
    errors.append("build-net must be external: true (compose must never create it)")
if dn.get("name") != df_name:
    errors.append(f"dark-factory-net name = {dn.get('name')!r}, want {df_name!r}")
if dn.get("external"):
    errors.append("dark-factory-net must stay compose-managed, not external")
env = svc.get("environment", {})
if env.get("BUILDKIT_HOST") != host:
    errors.append(f"BUILDKIT_HOST = {env.get('BUILDKIT_HOST')!r}, want {host!r}")
if str(env.get("BUILDKIT_PORT")) != port:
    errors.append(f"BUILDKIT_PORT = {env.get('BUILDKIT_PORT')!r}, want {port!r}")
for e in errors:
    print("  " + e)
sys.exit(1 if errors else 0)
PY
  echo "PASS: $label"
}

# 1) All unset (self/jobfinder today): build-net is the instance's own network; BuildKit
#    defaults equal preview-up's in-workflow fallbacks.
render "$WORK/unset.json"
assert_shape "unset: build-net = own dark-factory-net, buildkit:1234" \
  "$WORK/unset.json" dark-factory_dark-factory-net dark-factory_dark-factory-net buildkit 1234

# 2) Lockstep with the project name: a named instance moves both networks together.
render "$WORK/instance.json" FACTORY_INSTANCE=dark-factory-self
assert_shape "FACTORY_INSTANCE=dark-factory-self: both networks follow the instance" \
  "$WORK/instance.json" dark-factory-self_dark-factory-net dark-factory-self_dark-factory-net buildkit 1234

# 3) Empty string behaves as unset (compose's :- treats both alike).
render "$WORK/empty.json" FACTORY_BUILD_NETWORK=
assert_shape "FACTORY_BUILD_NETWORK empty: falls back to own network" \
  "$WORK/empty.json" dark-factory_dark-factory-net dark-factory_dark-factory-net buildkit 1234

# 4) MarketHawk-shaped: the run joins the target's build-daemon network. A non-default
#    port proves BUILDKIT_PORT is passed through, not just defaulted.
render "$WORK/set.json" FACTORY_BUILD_NETWORK=markethawk_factory-network \
  BUILDKIT_HOST=markethawk-buildkit BUILDKIT_PORT=2345
assert_shape "set: build-net = markethawk_factory-network, markethawk-buildkit:2345" \
  "$WORK/set.json" markethawk_factory-network dark-factory_dark-factory-net markethawk-buildkit 2345

echo "PASS: test_build_network_compose.sh"
