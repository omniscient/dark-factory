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

# 3c2) Build-network lockstep (#455): build-net's no-op default must be this project's
# own dark-factory-net, i.e. "<the name: expression pinned by 3c>_dark-factory-net".
# If the project-name expression changes, this default must change with it, or every
# dispatch of an instance without FACTORY_BUILD_NETWORK points at a missing network.
grep -qF 'name: ${FACTORY_BUILD_NETWORK:-${FACTORY_INSTANCE:-dark-factory}_dark-factory-net}' "$RC" \
  || { echo "FAIL: run-compose.yml build-net default not tied to the FACTORY_INSTANCE project name"; exit 1; }

# 3d) WIP-guard seam: identity.sh's default FACTORY_RUN_PREFIX must match the
# names `docker compose run` actually produces (<project>-dark-factory-run-…).
IDS="$REPO_ROOT/scripts/identity.sh"
grep -q 'FACTORY_RUN_PREFIX:-${FACTORY_INSTANCE:-dark-factory}-dark-factory-run-' "$IDS"   || { echo "FAIL: identity.sh RUN_PREFIX default does not match compose run-container naming"; exit 1; }

# 3e) Model endpoint registry (#557): the scheduler alone gets one read-only mount of the
# instance's model-registry/ (default: the empty tracked placeholder). Run containers get
# the staged copy through the existing state volume, so run-compose.yml gains no mount.
grep -qF -- '- ${FACTORY_MODEL_REGISTRY_DIR:-./model-registry.empty}:/etc/dark-factory/model-registry:ro' "$DC" \
  || { echo "FAIL: deploy compose missing the read-only model registry mount"; exit 1; }
[ "$(grep -c ':/etc/dark-factory/model-registry' "$DC")" -eq 1 ] \
  || { echo "FAIL: deploy compose mounts the model registry more than once"; exit 1; }
[ -f "$REPO_ROOT/deploy/model-registry.empty/.gitkeep" ] \
  || { echo "FAIL: deploy/model-registry.empty/.gitkeep placeholder missing"; exit 1; }
[ "$(ls -A "$REPO_ROOT/deploy/model-registry.empty")" = ".gitkeep" ] \
  || { echo "FAIL: deploy/model-registry.empty/ must hold only .gitkeep"; exit 1; }
if grep -q 'model-registry' "$RC"; then
  echo "FAIL: run-compose.yml must not mount the model registry (staged via scheduler_state)"; exit 1
fi
# 3f) preview-helper-proxy (#634), asserted on the parsed SOURCE: a render carries
# `command: null` for every service and merges an env_file into environment.
python3 - "$RC" <<'PY' || { echo "FAIL: preview-helper-proxy source-definition checks"; exit 1; }
import sys, yaml
services = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))["services"]
name = "preview-helper-proxy"
svc = services.get(name)
assert svc is not None, f"{name} service missing"
# helper_proxy_source_has_no_forbidden_keys
bad = sorted({"env_file", "depends_on", "restart", "container_name", "command"} & set(svc))
assert not bad, f"{name} must not declare {bad}"
assert "MODEL_PROXY_UPSTREAM" not in (svc.get("environment") or {}), \
    f"{name} must not set MODEL_PROXY_UPSTREAM"
assert svc.get("profiles") == [name], f"{name} must be only under its own profile"
# helper_secret_has_one_fixed_name (spec requirement 7, decision D2): the mapping form only,
# so no variable name is chosen at dispatch time, and the secret is the one pass-through key.
env = svc.get("environment")
assert isinstance(env, dict), \
    f"{name} environment must be a mapping: the list form lets a variable name be interpolated"
passthrough = sorted(k for k, v in env.items() if v is None)
assert passthrough == ["FACTORY_HELPER_MODEL_SECRET"], \
    f"{name} must pass through exactly FACTORY_HELPER_MODEL_SECRET, not {passthrough}"
# decision D1: dark-factory-net only until #587 adds the helper's internal network
assert svc.get("networks") == ["dark-factory-net"], f"{name} must be on dark-factory-net only"
# helper_proxy_entrypoint_is_the_literal_intent
expected = ["python3", "-m", "factory_core.model_proxy", "--intent", "classify-preview-helper"]
assert svc.get("entrypoint") == expected, f"{name} entrypoint must be exactly {expected}"
assert not any("$" in token for token in svc["entrypoint"]), f"{name} entrypoint is interpolated"
# dark_factory_does_not_depend_on_helper_proxy
for other, spec in services.items():
    deps = spec.get("depends_on") or {}
    assert name not in deps, f"{other} depends_on {name}"
    nets = spec.get("networks") or {}
    if isinstance(nets, dict):
        for net in nets.values():
            assert name not in ((net or {}).get("aliases") or []), \
                f"{other} declares a network alias {name}"
PY
echo "PASS: preview-helper-proxy source definition (no forbidden keys, literal intent, no depends_on/alias)"
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
  # 4b) helper_proxy_env_is_exactly_the_allow_list (#634): rendered with its profile, the
  # five scheduler-side sources set to fixture values and every forbidden name set in the
  # caller's environment. The render is piped straight into the parser, never echoed.
  CLOUD_CREDS=$(PYTHONPATH="$REPO_ROOT/scripts" python3 -c \
    "from factory_core.providers import model_endpoint as me; print(' '.join(me.CLOUD_CREDENTIALS))")
  # shellcheck disable=SC2086
  # DOCKER_HOST keeps a working value: the CLI itself reads it.
  if ! env DOCKER_HOST="${DOCKER_HOST:-unix:///var/run/docker.sock}" \
      $(for n in GH_TOKEN GITLAB_TOKEN JIRA_TOKEN $CLOUD_CREDS; do printf '%s=forbidden-fixture ' "$n"; done) \
      PREVIEW_HELPER_ENDPOINT=helper-fixture \
      FACTORY_MODEL_REGISTRY_SHA256=0000000000000000000000000000000000000000000000000000000000000000 \
      PREVIEW_HELPER_ENDPOINT_BASE_URL=http://192.0.2.10:8080 \
      PREVIEW_HELPER_ENDPOINT_SERVED_NAME=served-fixture \
      FACTORY_HELPER_MODEL_SECRET=plain-fixture-not-a-secret \
      docker compose -f "$RC" --profile preview-helper-proxy config --format json 2>/dev/null \
    | python3 -c '
import json, sys
env = json.load(sys.stdin)["services"]["preview-helper-proxy"]["environment"]
served = "served-fixture"
expected = {
    "PYTHONPATH": "/opt/dark-factory/scripts", "MODEL_PROXY_PORT": "8787",
    "CURRENT_RUN_DIR": "/var/lib/dark-factory/preview-helper",
    "FACTORY_MODEL_ENDPOINT": "helper-fixture", "FACTORY_MODEL_REGISTRY_SHA256": "0" * 64,
    "ANTHROPIC_BASE_URL": "http://192.0.2.10:8080",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": served, "ANTHROPIC_DEFAULT_SONNET_MODEL": served,
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": served, "ANTHROPIC_DEFAULT_FABLE_MODEL": served,
    "FACTORY_HELPER_MODEL_SECRET": "plain-fixture-not-a-secret",
}
if set(env) != set(expected):
    sys.exit(f"rendered keys differ: extra {sorted(set(env) - set(expected))}, "
             f"missing {sorted(set(expected) - set(env))}")
wrong = sorted(k for k in expected if env[k] != expected[k])
if wrong:
    sys.exit(f"rendered values differ for {wrong}")
'; then
    echo "FAIL: preview-helper-proxy rendered environment is not exactly the eleven allowed keys"
    [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"
    exit 1
  fi
  echo "PASS: preview-helper-proxy renders exactly its eleven environment keys"

  # 4c) new_service_does_not_change_other_renders (#634, regression guard): a copy of the
  # file without the preview-helper-proxy block renders identically for every profile set
  # scheduler.sh dispatches with. Both copies sit in scratch dirs of the same layout.
  DIFF_DIR=$(mktemp -d)
  mkdir -p "$DIFF_DIR/with/.archon" "$DIFF_DIR/without/.archon"
  touch "$DIFF_DIR/with/.archon/.env" "$DIFF_DIR/without/.archon/.env"
  cp "$RC" "$DIFF_DIR/with/run-compose.yml"
  awk '/^  preview-helper-proxy:$/{skip=1} skip && /^[^ ]/{skip=0} !skip' "$RC" \
    > "$DIFF_DIR/without/run-compose.yml"
  if grep -q 'preview-helper-proxy:' "$DIFF_DIR/without/run-compose.yml"; then
    echo "FAIL: could not strip the preview-helper-proxy block for the differential render"
    rm -rf "$DIFF_DIR"; [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"; exit 1
  fi
  for profiles in "" "--profile factory" "--profile factory --profile factory-model-proxy"; do
    # shellcheck disable=SC2086
    if ! WITH=$(docker compose -f "$DIFF_DIR/with/run-compose.yml" $profiles config 2>&1) \
       || ! WITHOUT=$(docker compose -f "$DIFF_DIR/without/run-compose.yml" $profiles config 2>&1) \
       || [ "$WITH" != "$WITHOUT" ]; then
      echo "FAIL: preview-helper-proxy changes the render for profiles [${profiles:-none}]"
      rm -rf "$DIFF_DIR"; [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"; exit 1
    fi
  done
  rm -rf "$DIFF_DIR"
  echo "PASS: preview-helper-proxy leaves every other render byte-identical"

  # 4d) helper_secret_is_not_in_rendered_config (#634): with a sentinel in the caller's
  # environment, the uninterpolated helper render and (guard) an interpolated factory render
  # never carry it. No test captures an interpolated helper-profile render with it set.
  SENTINEL_VALUE="sk-sentinel-helper-secret-never-rendered"
  if FACTORY_HELPER_MODEL_SECRET="$SENTINEL_VALUE" \
       docker compose -f "$RC" --profile preview-helper-proxy config --no-interpolate 2>&1 \
       | grep -qF "$SENTINEL_VALUE" \
     || FACTORY_HELPER_MODEL_SECRET="$SENTINEL_VALUE" \
       docker compose -f "$RC" --profile factory config 2>&1 | grep -qF "$SENTINEL_VALUE"; then
    echo "FAIL: the helper secret value appears in a rendered config"
    [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"
    exit 1
  fi
  echo "PASS: the helper secret is in no uninterpolated or factory-profile render"
  [ "$SCRATCH_ENV" = "1" ] && rm -f "$REPO_ROOT/.archon/.env"
else
  echo "SKIP: docker not available — skipping compose config parse check"
fi

echo "PASS: test_run_compose.sh"
