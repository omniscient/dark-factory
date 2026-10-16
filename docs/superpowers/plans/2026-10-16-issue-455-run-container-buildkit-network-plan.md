# Run containers reach the target BuildKit daemon — Implementation Plan

**Issue:** #455

> **For agentic workers:** Execute tasks in order. Task 0 is a hard gate: if it does not
> pass, STOP per its outcome table. Do not proceed to Task 1, and do not switch designs.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a dispatched run container join an optional, target-declared external Docker
network (`FACTORY_BUILD_NETWORK`) and receive `BUILDKIT_HOST`/`BUILDKIT_PORT`, so the
built-in `preview-up` node can reach a target's remote BuildKit daemon (MarketHawk's
`markethawk-buildkit`). Instances that set nothing (self, jobfinder) keep working as they do today.

**Architecture:** `run-compose.yml` is the only behavior change. The `dark-factory` service
gains a second network key `build-net`, declared `external: true`, with name
`${FACTORY_BUILD_NETWORK:-${FACTORY_INSTANCE:-dark-factory}_dark-factory-net}`. When unset,
that default is the instance's own `dark-factory-net`, which `deploy/docker-compose.yml`
already creates. The service also gets two passthrough env entries,
`BUILDKIT_HOST: ${BUILDKIT_HOST:-buildkit}` and `BUILDKIT_PORT: ${BUILDKIT_PORT:-1234}`.
The scheduler already exports `instance.env` into its process env through `env_file:`,
and `dispatch()` interpolates `run-compose.yml` from that env, so
**no `deploy/docker-compose.yml`, `scheduler.sh` or `Dockerfile` change** is needed.
Docs and a `docker compose config` regression test round it out.

**Tech Stack:** Docker Compose v2 YAML, bash tests (`set -euo pipefail`, grep, plus
`docker compose config --format json` parsed with `python3`), GitHub Actions CI.

**Spec:** `docs/superpowers/specs/2026-10-16-issue-455-run-container-buildkit-network-design.md`

---

## Plan-phase pre-flight evidence (2026-10-16, informational; Task 0 is still mandatory)

The plan agent ran the spec's live check from a self-instance run container. It used a
scratch copy of `run-compose.yml` with the Task 3 edit applied, through the real
`docker-socket-proxy-scheduler` (tecnativa v0.4.2: CONTAINERS/IMAGES/POST/NETWORKS/VOLUMES=1, INFO=0):

```
$ INST=$(docker inspect "$(hostname)" --format '{{index .Config.Labels "com.docker.compose.project"}}')   # dark-factory-self
$ env -u FACTORY_BUILD_NETWORK -u COMPOSE_PROJECT_NAME FACTORY_INSTANCE="$INST" \
    docker compose -f "$T/run-compose.yml" --profile factory run --rm --no-deps \
    --name df-issue-455-preflight --entrypoint /bin/true dark-factory; echo "exit=$?"
 Container df-issue-455-preflight Creating
 Container df-issue-455-preflight Created
exit=0
# detached variant (sleep 60), then docker inspect → networks:
running | dark-factory-self_dark-factory-net aliases=[df-issue-455-preflight-b];
```

Docker accepts one network under two compose keys and attaches the container to it once.
No proxy change is needed (spec Requirement 4 confirmed).

**Additional finding (feeds Tasks 5–6 docs):** with `FACTORY_BUILD_NETWORK` set to a
network that does **not** exist, `docker compose run` fails with
`Error response from daemon: <html><body><h1>403 Forbidden</h1> Request forbidden by administrative rules.`.
It does not say "network not found". When an external network is missing, Compose calls
`GET /info`, and the socket proxy denies that (`INFO=0`; `docker info` returns the same
403). An operator reading a `dispatch_error` therefore sees a proxy 403. The docs must say
this means "check that the build network exists". It is not a proxy-permission problem,
and nobody should "fix" it by widening proxy grants: that stays out of scope.

This evidence also suggests why the refine phase's live checks were "blocked by a 403". They
probably ran with a project name whose network was missing, so compose hit `/info`.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `tests/test_build_network_compose.sh` | **Create** (Task 1; one grep added in Step 5.5) | Regression test: unset, instance-scoped, empty-string and MarketHawk-shaped renders of `build-net` + `BUILDKIT_*` via `docker compose config --format json`; pins the `FACTORY_INSTANCE` normalization constraint (instance values + documented sentence) |
| `tests/test_run_compose.sh` | Modify (one assertion after check 3c) | Lockstep pin: `build-net` default literal stays tied to the `FACTORY_INSTANCE` project-name expression |
| `run-compose.yml` | Modify | `build-net` external network (service + top-level) and `BUILDKIT_HOST`/`BUILDKIT_PORT` passthrough |
| `.github/workflows/ci.yml` | Modify (one `run:` line in `tests` job) | Run the new test in CI |
| `deploy/instance.env.example` | Modify (new optional block + one pointer line at `FACTORY_INSTANCE`) | Document the three variables, the `FACTORY_INSTANCE` normalization constraint and the network pre-check |
| `docs/onboarding-new-target.md` | Modify (optional sub-step at end of step 4 + one clause in the `FACTORY_INSTANCE` bullet) | Optional build-daemon / preview-network step |
| `README.md` | Modify (`preview-up` row of the Hooks table) | One-line mention of the BuildKit variables |

Nothing else. In particular, no change to `deploy/docker-compose.yml`, `scheduler.sh`,
`Dockerfile`, `workflows/`, `deploy/instances/**` (human-only) or socket-proxy grants.
`run-compose.yml` and `deploy/instance.env.example` match `critical_diff_paths`
(`^run-compose\.yml$`, `^deploy/`). That is expected and gets higher review scrutiny.

---

### Task 0: Live check — duplicate network attachment at container create (HARD GATE)

**Files:** none in the repo (scratch dir under `mktemp -d`; transcript to `$ARTIFACTS_DIR`)

This is the spec's Task 0. `--entrypoint /bin/true` (or `/bin/sleep` in 0.5) is
**mandatory**. Without it, the image's `entrypoint.sh` treats the argument as a workflow
request and starts a real, billed factory run. Pass no `-p` and do not set
`COMPOSE_PROJECT_NAME`. Run containers do **not** receive `FACTORY_INSTANCE`, because
`run-compose.yml` does not pass it. Derive it from this container's own compose label, so
the check runs in a project whose `<instance>_dark-factory-net` already exists, as it does
in production. `--name df-issue-455-task0*` keeps the check's containers out of the
scheduler's WIP-guard prefix (`<instance>-dark-factory-run-`).

- [ ] **Step 0.1: Resolve the instance and confirm its network exists**

```bash
INST=$(docker inspect "$(hostname)" --format '{{index .Config.Labels "com.docker.compose.project"}}')
echo "INST=$INST"
docker network inspect "${INST}_dark-factory-net" --format '{{.Name}}'
```

Expected: `INST=dark-factory-self` and `dark-factory-self_dark-factory-net`. If either
command fails, the check is **blocked**. See the outcome table.

- [ ] **Step 0.2: Build a scratch copy of `run-compose.yml` with the Task 3 edit applied**

```bash
T=$(mktemp -d)
cp run-compose.yml "$T/run-compose.yml"
mkdir -p "$T/.archon" && touch "$T/.archon/.env"
python3 - "$T/run-compose.yml" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
edits = [
    ("      ANTHROPIC_DEFAULT_FABLE_MODEL: ${ANTHROPIC_DEFAULT_FABLE_MODEL:-claude-fable-5-1}\n",
     "      ANTHROPIC_DEFAULT_FABLE_MODEL: ${ANTHROPIC_DEFAULT_FABLE_MODEL:-claude-fable-5-1}\n"
     "      BUILDKIT_HOST: ${BUILDKIT_HOST:-buildkit}\n"
     "      BUILDKIT_PORT: ${BUILDKIT_PORT:-1234}\n"),
    ("    networks:\n      - dark-factory-net\n    profiles:\n      - factory\n",
     "    networks:\n      - dark-factory-net\n      - build-net\n    profiles:\n      - factory\n"),
    ("networks:\n  dark-factory-net:\n    driver: bridge\n",
     "networks:\n  dark-factory-net:\n    driver: bridge\n  build-net:\n    external: true\n"
     "    name: ${FACTORY_BUILD_NETWORK:-${FACTORY_INSTANCE:-dark-factory}_dark-factory-net}\n"),
]
for old, new in edits:
    assert s.count(old) == 1, f"anchor not unique/found: {old!r}"
    s = s.replace(old, new)
open(p, "w").write(s)
print("scratch edit applied")
PY
env -u FACTORY_BUILD_NETWORK -u COMPOSE_PROJECT_NAME FACTORY_INSTANCE="$INST" \
  docker compose -f "$T/run-compose.yml" --profile factory config --format json \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print({k: v["name"] for k, v in d["networks"].items()})'
```

Expected:
```
scratch edit applied
{'build-net': 'dark-factory-self_dark-factory-net', 'dark-factory-net': 'dark-factory-self_dark-factory-net'}
```

- [ ] **Step 0.3: Run the spec's live check (variable unset)**

```bash
env -u FACTORY_BUILD_NETWORK -u COMPOSE_PROJECT_NAME FACTORY_INSTANCE="$INST" \
  timeout 120 docker compose -f "$T/run-compose.yml" --profile factory run --rm --no-deps \
  --name df-issue-455-task0 --entrypoint /bin/true dark-factory; echo "exit=$?"
```

Expected (an orphan-containers warning line for the scheduler/proxy is harmless):
```
 Container df-issue-455-task0 Creating
 Container df-issue-455-task0 Created
exit=0
```

If this one command is refused with the proxy's HTML 403 (`Request forbidden by
administrative rules.`), retry it once in `dispatch()`'s exact detached form,
`run -d --rm` with the same other flags. Every real dispatch goes through the same proxy
in that form. Then poll `docker ps -a --filter name=df-issue-455-task0` until it is empty.

- [ ] **Step 0.4: Prove the container attaches to the network exactly once**

```bash
env -u FACTORY_BUILD_NETWORK -u COMPOSE_PROJECT_NAME FACTORY_INSTANCE="$INST" \
  timeout 120 docker compose -f "$T/run-compose.yml" --profile factory run -d --no-deps \
  --name df-issue-455-task0b --entrypoint /bin/sleep dark-factory 60
docker inspect df-issue-455-task0b \
  --format '{{.State.Status}} | {{range $k,$v := .NetworkSettings.Networks}}{{$k}}; {{end}}'
docker rm -f df-issue-455-task0b
```

Expected: `running | dark-factory-self_dark-factory-net; ` (exactly one network), then
`df-issue-455-task0b` from `rm -f`.

- [ ] **Step 0.5: Clean up and save the transcript**

```bash
docker ps -aq --filter name=df-issue-455-task0 | xargs -r docker rm -f
docker ps -a --filter name=df-issue-455-task0 --format '{{.Names}}'   # expected: no output
rm -rf "$T"
```

Save the commands and outputs of Steps 0.1–0.5 to `$ARTIFACTS_DIR/issue-455-task0.txt`.
Paste them into the PR description under a `### Task 0 live check` heading, which the spec
requires, and into the Task 3 commit message body so they survive.

**Outcome table**

| Result | Action |
|---|---|
| 0.3 `exit=0` **and** 0.4 shows exactly one network | **Pass**: continue to Task 1. |
| Create fails with a Docker daemon error about the duplicate attachment (e.g. `endpoint with name … already exists in network …`, which Docker also reports as HTTP 403, but as a JSON message, not the proxy's HTML page), or 0.4 shows a different network set | **Fail**: STOP. Post the transcript on the issue as a comment titled `#455 Task 0 — duplicate network attachment failed`. Add `needs-discussion` (`python3 dark-factory/scripts/factory_core/providers/cli.py tracker label --id 455 --add needs-discussion`). Make no repo change and do not switch to the override-file design. A follow-up spec decides. |
| Blocked: 0.1 fails, docker is unreachable, or 0.3 and its detached retry both return the proxy's HTML 403 | **Blocked**: STOP, the same as Fail. Post the transcript titled `#455 Task 0 — live check blocked` and add `needs-discussion`. Do not ship an unverified change: the dispatch path is shared by every instance, and a wrong network spec would turn every dispatch into a `dispatch_error`. |

Posting the stop comment (Fail or Blocked; set `TITLE` to the row's title):

```bash
{ echo "## $TITLE"; echo; echo '```'; cat "$ARTIFACTS_DIR/issue-455-task0.txt"; echo '```'; } > "$ARTIFACTS_DIR/issue-455-task0-comment.md"
gh issue comment 455 --repo "$FACTORY_REPO_SLUG" --body-file "$ARTIFACTS_DIR/issue-455-task0-comment.md"
python3 dark-factory/scripts/factory_core/providers/cli.py tracker label --id 455 --add needs-discussion
```

---

### Task 1: Failing regression test `tests/test_build_network_compose.sh`

**Files:** Create `tests/test_build_network_compose.sh`

- [ ] **Step 1.1: Write the test**

```bash
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
# (Step 5.5 adds the instance.env.example constraint grep here, once Task 5 has written it.)

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
```

- [ ] **Step 1.2: Run it and verify it fails**

```bash
bash tests/test_build_network_compose.sh; echo "exit=$?"
```

Expected (`run-compose.yml` is unchanged so far):
```
FAIL: build-net network missing from run-compose.yml
exit=1
```

- [ ] **Step 1.3: Make it executable on disk.** This matches e.g. `tests/test_model_proxy_compose.sh` (mode 100755). With `core.fileMode=true`, the Step 3.6 `git add` stages the bit. CI invokes it through `bash` either way.

```bash
chmod +x tests/test_build_network_compose.sh
```

(Commit happens in Task 3, together with the implementation that makes it pass.)

---

### Task 2: Failing lockstep assertion in `tests/test_run_compose.sh`

**Files:** Modify `tests/test_run_compose.sh` (insert directly after check 3c, before the `# 3d)` comment)

- [ ] **Step 2.1: Add the assertion**

Insert after the line
`grep -q '^name: ${FACTORY_INSTANCE:-dark-factory}$' "$DC" || { echo "FAIL: deploy compose project name not FACTORY_INSTANCE-scoped"; exit 1; }`:

```bash

# 3c2) Build-network lockstep (#455): build-net's no-op default must be this project's
# own dark-factory-net, i.e. "<the name: expression pinned by 3c>_dark-factory-net".
# If the project-name expression changes, this default must change with it, or every
# dispatch of an instance without FACTORY_BUILD_NETWORK points at a missing network.
grep -qF 'name: ${FACTORY_BUILD_NETWORK:-${FACTORY_INSTANCE:-dark-factory}_dark-factory-net}' "$RC" \
  || { echo "FAIL: run-compose.yml build-net default not tied to the FACTORY_INSTANCE project name"; exit 1; }
```

- [ ] **Step 2.2: Run it and verify it fails**

```bash
bash tests/test_run_compose.sh; echo "exit=$?"
```

Expected:
```
FAIL: run-compose.yml build-net default not tied to the FACTORY_INSTANCE project name
exit=1
```

---

### Task 3: Implement the network and passthrough in `run-compose.yml`

**Files:** Modify `run-compose.yml`

- [ ] **Step 3.1: Add the BuildKit passthrough** after the `ANTHROPIC_DEFAULT_FABLE_MODEL` line (line 53) of the `dark-factory` service's `environment:`

```yaml
      ANTHROPIC_DEFAULT_FABLE_MODEL: ${ANTHROPIC_DEFAULT_FABLE_MODEL:-claude-fable-5-1}
      # Target build daemon (#455): preview-up builds via tcp://$BUILDKIT_HOST:$BUILDKIT_PORT.
      # Defaults equal preview-up's own in-workflow fallbacks, so unset = unchanged.
      BUILDKIT_HOST: ${BUILDKIT_HOST:-buildkit}
      BUILDKIT_PORT: ${BUILDKIT_PORT:-1234}
```

- [ ] **Step 3.2: Attach `build-net` to the `dark-factory` service only** (the `factory-model-proxy` service's `networks:` stays unchanged)

```yaml
    networks:
      - dark-factory-net
      - build-net
    profiles:
      - factory
```

- [ ] **Step 3.3: Declare `build-net` in the top-level `networks:` block**

```yaml
networks:
  dark-factory-net:
    driver: bridge
  # Optional external network carrying the target's BuildKit daemon (#455), set by
  # FACTORY_BUILD_NETWORK in the scheduler's instance.env. Unset or empty, it names this
  # instance's own dark-factory-net (created by deploy/docker-compose.yml under the same
  # project name), so dispatch needs no extra network. That default holds only while
  # FACTORY_INSTANCE is a normalized compose project name and no -p/COMPOSE_PROJECT_NAME
  # override is used (tests/test_run_compose.sh 3c2). Once set, the network must exist
  # before EVERY dispatch of the instance: a missing one fails compose run with the
  # socket proxy's "403 ... administrative rules" (compose falls back to GET /info).
  build-net:
    external: true
    name: ${FACTORY_BUILD_NETWORK:-${FACTORY_INSTANCE:-dark-factory}_dark-factory-net}
```

- [ ] **Step 3.4: Verify both tests now pass**

```bash
bash tests/test_build_network_compose.sh && bash tests/test_run_compose.sh && bash tests/test_model_proxy_compose.sh
```

Expected (in a container with the docker CLI; without it, the `partial — grep checks only` PASS lines).
The `FACTORY_INSTANCE` constraint block is silent on success, so it adds no PASS line:
```
PASS: unset: build-net = own dark-factory-net, buildkit:1234
PASS: FACTORY_INSTANCE=dark-factory-self: both networks follow the instance
PASS: FACTORY_BUILD_NETWORK empty: falls back to own network
PASS: set: build-net = markethawk_factory-network, markethawk-buildkit:2345
PASS: test_build_network_compose.sh
PASS: docker compose config parses run-compose.yml
PASS: test_run_compose.sh
PASS: factory-model-proxy absent from default (--profile factory) config
PASS: factory-model-proxy present when its profile is explicitly enabled
PASS: test_model_proxy_compose.sh
```

- [ ] **Step 3.5: Confirm the unset render differs from `main` only as the spec allows (Requirement 3)**

```bash
mkdir -p .archon; SCR=0; [ -f .archon/.env ] || { touch .archon/.env; SCR=1; }
git show origin/main:run-compose.yml > /tmp/rc-main.yml
cp /tmp/rc-main.yml ./.rc-main.yml   # same dir so env_file .archon/.env resolves identically
env -u FACTORY_BUILD_NETWORK -u BUILDKIT_HOST -u BUILDKIT_PORT -u FACTORY_INSTANCE -u COMPOSE_PROJECT_NAME \
  docker compose -f ./.rc-main.yml --profile factory config > /tmp/rc-main.out
env -u FACTORY_BUILD_NETWORK -u BUILDKIT_HOST -u BUILDKIT_PORT -u FACTORY_INSTANCE -u COMPOSE_PROJECT_NAME \
  docker compose -f run-compose.yml --profile factory config > /tmp/rc-new.out
diff /tmp/rc-main.out /tmp/rc-new.out; rm -f ./.rc-main.yml; [ "$SCR" = 1 ] && rm -f .archon/.env
```

Expected: the diff contains only the `BUILDKIT_HOST: buildkit` / `BUILDKIT_PORT: "1234"`
environment lines, a `build-net: null` service network line, and the top-level
`build-net:` block (`name: dark-factory_dark-factory-net`, `external: true`). Any other
hunk is a defect. Fix it before committing. Check that `.rc-main.yml` is gone
(`git status --short` must not list it).

- [ ] **Step 3.6: Commit**

```bash
git add run-compose.yml tests/test_build_network_compose.sh tests/test_run_compose.sh
git commit -m "feat(run-compose): optional target build network + BUILDKIT passthrough (#455)

Run containers can join FACTORY_BUILD_NETWORK (external; default = the
instance's own dark-factory-net, a no-op) and receive BUILDKIT_HOST/PORT
(defaults buildkit/1234 = preview-up's in-workflow fallbacks).

Task 0 live check: <paste the Step 0.3/0.4 commands + output here>"
```

---

### Task 4: Run the new test in CI

**Files:** Modify `.github/workflows/ci.yml`

- [ ] **Step 4.1: Add one `run:` line to the `tests` job**, directly after `- run: bash tests/test_model_proxy_compose.sh` (line 20)

```yaml
      - run: bash tests/test_model_proxy_compose.sh
      - run: bash tests/test_build_network_compose.sh
```

(Only the `tests` job, on ubuntu-latest with the docker CLI and Compose v2, so the render
checks run there. The `docker-build` job is not touched.)

- [ ] **Step 4.2: Verify the CI file still parses and lists the test exactly once**

```bash
python3 -c "import yaml; d=yaml.safe_load(open('.github/workflows/ci.yml')); runs=[s.get('run','') for s in d['jobs']['tests']['steps']]; print(runs.count('bash tests/test_build_network_compose.sh'))"
```

Expected: `1`

- [ ] **Step 4.3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: run tests/test_build_network_compose.sh in the tests job (#455)"
```

---

### Task 5: Document the variables in `deploy/instance.env.example`

**Files:** Modify `deploy/instance.env.example`

- [ ] **Step 5.1: Add a pointer to the `FACTORY_INSTANCE` comment** (lines 33–35)

```
# Scopes container names and the scheduler state volume; one value per target so
# several instances can share a host. Convention: the repo name. Must be a normalized
# compose project name ([a-z0-9][a-z0-9_-]*); see the build-network block below.
FACTORY_INSTANCE=
```

- [ ] **Step 5.2: Append the optional block** at the end of the file, after the model-proxy section

```
# ---------------------------------------------------------------------------
# Target build daemon / preview network (optional — most targets skip this, #455)
# ---------------------------------------------------------------------------

# Only for targets whose preview builds run on their own remote BuildKit daemon
# (the built-in preview-up path builds via tcp://$BUILDKIT_HOST:$BUILDKIT_PORT).
# Leave all three unset otherwise. They are then a no-op: run containers join no
# extra network and BuildKit falls back to buildkit:1234, as before.
#
# FACTORY_BUILD_NETWORK: an existing external Docker network carrying the daemon; run
# containers join it alongside their own dark-factory-net. The network must exist
# before the scheduler dispatches; check with
#   docker network inspect $FACTORY_BUILD_NETWORK
# Once set, it is a hard dependency of EVERY dispatch of this instance, not only
# preview tickets. While it is missing, each dispatch fails with the socket proxy's
# "403 Forbidden ... Request forbidden by administrative rules" (compose's fallback
# lookup hits /info, which the proxy denies), a dispatch_error, and repeated failures
# trip the breaker and move tickets to Blocked. That 403 means "create/restore the
# network", not "widen the proxy". If the network exists but BuildKit is down (or the
# BUILDKIT_* values are wrong), only preview tickets fail, with a generic preview_fail
# "buildx build ... failed" comment that names neither the network nor the address.
#
# When FACTORY_BUILD_NETWORK is unset, the default is <FACTORY_INSTANCE>_dark-factory-net,
# so FACTORY_INSTANCE must already be a normalized compose project name
# ([a-z0-9][a-z0-9_-]*); otherwise the build-network default points at a network that
# does not exist and every dispatch fails.
#
# BUILDKIT_HOST / BUILDKIT_PORT: the daemon's name (container name or alias) on that
# network and its port. Example (MarketHawk):
# FACTORY_BUILD_NETWORK=markethawk_factory-network
# BUILDKIT_HOST=markethawk-buildkit
# BUILDKIT_PORT=1234
```

- [ ] **Step 5.3: Bootstrap pytest in the run container.** The factory image ships Python
  without pytest, and `entrypoint.sh` only installs a target's `backend/requirements.txt`,
  which this repo does not have. Without this step every `python -m pytest` below prints
  `No module named pytest`.

```bash
python -m pip install --quiet --user pytest pyyaml aiohttp
```

- [ ] **Step 5.4: Verify the example still contains no active (uncommented) new variable**

```bash
grep -nE '^(FACTORY_BUILD_NETWORK|BUILDKIT_HOST|BUILDKIT_PORT)=' deploy/instance.env.example; echo "matches=$?"
grep -c 'docker network inspect \$FACTORY_BUILD_NETWORK' deploy/instance.env.example
python -m pytest tests/test_run_skill_files.py -q
```

Expected: `matches=1` (grep found none, so the defaults stay a no-op for anyone who copies
the file), then `1`, then the pytest file passes. It pins that
`deploy/instance.env.example` is referenced, which is unchanged.

- [ ] **Step 5.5: Pin the documented constraint in the test.** In
  `tests/test_build_network_compose.sh`, replace the placeholder comment line
  `# (Step 5.5 adds the instance.env.example constraint grep here, once Task 5 has written it.)`
  with the lines below. It cannot go in at Task 1: the sentence only exists after Step 5.1/5.2,
  and Step 3.4 would fail. The final file has the whole constraint block before the docker skip.

```bash
grep -qF '[a-z0-9][a-z0-9_-]*' "$REPO_ROOT/deploy/instance.env.example" \
  || { echo "FAIL: FACTORY_INSTANCE normalization constraint undocumented in deploy/instance.env.example"; exit 1; }
```

Then run `bash tests/test_build_network_compose.sh`. Expected: the same five PASS lines as
Step 3.4 (the grep is silent on success).

- [ ] **Step 5.6: Commit**

```bash
git add deploy/instance.env.example tests/test_build_network_compose.sh
git commit -m "docs(instance.env.example): optional FACTORY_BUILD_NETWORK / BUILDKIT_* block (#455)"
```

---

### Task 6: Onboarding step in `docs/onboarding-new-target.md`

**Files:** Modify `docs/onboarding-new-target.md`

- [ ] **Step 6.1: Tighten the `FACTORY_INSTANCE` bullet in step 4**, currently "`FACTORY_INSTANCE=<repo>`. This keeps container names and the state volume separate from any other instance on the host."

```markdown
- `FACTORY_INSTANCE=<repo>`, in lowercase letters, digits, `_` and `-` only, starting
  with a letter or digit (a normalized compose project name). This keeps container
  names and the state volume separate from any other instance on the host.
```

- [ ] **Step 6.2: Add an optional sub-step at the end of step 4**, after the
  `TARGET/.archon/.env` code block and before `## 5. Start the scheduler`

````markdown
**Optional: target build daemon / preview network.** Skip this unless your target's
preview builds use its own remote BuildKit daemon. Most targets have none. If yours
does, add to `DF/deploy/instance.env`:

```bash
FACTORY_BUILD_NETWORK=<the Docker network your BuildKit daemon is on>
BUILDKIT_HOST=<the daemon's container name or alias on that network>
BUILDKIT_PORT=1234
```

Run containers then join that network as well as their own, and the built-in
`preview-up` builds through `tcp://$BUILDKIT_HOST:$BUILDKIT_PORT`. The network must
exist before the scheduler dispatches. Once the variable is set, every dispatch (not
only previews) fails while the network is missing, with a socket-proxy
`403 Forbidden … administrative rules` error. Check first:

```bash
docker network inspect "$FACTORY_BUILD_NETWORK" --format '{{.Name}}'   # must print the name
```
````

- [ ] **Step 6.3: Verify**

```bash
grep -n 'Optional: target build daemon' docs/onboarding-new-target.md
grep -n '^## 5. Start the scheduler' docs/onboarding-new-target.md
```

Expected: the first line number is lower than the second (the sub-step sits inside step 4).

- [ ] **Step 6.4: Commit**

```bash
git add docs/onboarding-new-target.md
git commit -m "docs(onboarding): optional build daemon / preview network step (#455)"
```

---

### Task 7: README hooks table mention

**Files:** Modify `README.md` (the `preview-up` row of the `### Hooks` table, line 235)

- [ ] **Step 7.1: Replace the row**

```markdown
| `preview-up` | Post-implement | No | Spins up a preview stack for the PR branch. Built-in default: no-op. Separately, the workflow's own `preview-up` node builds through a remote BuildKit daemon at `BUILDKIT_HOST`/`BUILDKIT_PORT` (default `buildkit:1234`; delegating it to this hook is #15); a target with its own daemon sets them plus `FACTORY_BUILD_NETWORK` in `instance.env` (see `deploy/instance.env.example`). |
```

- [ ] **Step 7.2: Verify the table still has 4 hook rows and the mention landed**

```bash
grep -c '^| `\(smoke-gate\|validate\|preview-up\|preview-down\)`' README.md
grep -c 'FACTORY_BUILD_NETWORK' README.md
```

Expected: `4` then `1`.

- [ ] **Step 7.3: Commit**

```bash
git add README.md
git commit -m "docs(readme): mention BuildKit/build-network variables on the preview-up hook row (#455)"
```

---

### Task 8: Full verification and scope check

**Files:** none (verification only)

- [ ] **Step 8.1: Full CI-parity run**

```bash
python -m pip install --quiet --user pytest pyyaml aiohttp   # no-op if Step 5.3 already ran in this container
PYTHONPATH=scripts python -m pytest tests/ -q
for t in test_identity test_hooks test_smoke_gate test_run_compose test_model_proxy_compose test_build_network_compose; do
  bash "tests/$t.sh" > /tmp/$t.log 2>&1 && echo "OK $t" || { echo "FAILED $t"; tail -20 /tmp/$t.log; }
done
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```

Expected: pytest shows no failures beyond the two pre-existing environment-dependent ones
when run as root without an origin (`test_memory_write::test_index_io_error_is_nonfatal`,
`test_oos_excise` issue_json_fallback). Compare against a baseline run on unmodified main,
not against 0. Then six `OK …` lines, no `FAILED`, and both DAG checks pass. Workflows are
untouched, so this is a regression guard.

- [ ] **Step 8.2: Scope check against the spec's Requirement 8 file list**

```bash
git diff --name-only origin/main...HEAD | sort   # three-dot: branch changes only, even if main moved
```

Expected, exactly these seven, plus the refine-branch spec and plan docs that
`transfer_refine_artifacts.sh` carries over:
```
.github/workflows/ci.yml
README.md
deploy/instance.env.example
docs/onboarding-new-target.md
run-compose.yml
tests/test_build_network_compose.sh
tests/test_run_compose.sh
```
If `deploy/docker-compose.yml`, `scheduler.sh`, `Dockerfile`, `workflows/` or anything
under `deploy/instances/` appears, revert it. It is out of scope.

- [ ] **Step 8.3: Leftover check** (Task 0 resources and scratch env files)

```bash
docker ps -a --filter name=df-issue-455 --format '{{.Names}}'   # expected: no output
git status --short                                               # expected: clean (no .archon/.env, no .rc-main.yml)
```

- [ ] **Step 8.4: PR description must include**
  - the `### Task 0 live check` transcript (Task 0 Step 0.5);
  - a "MarketHawk follow-up (not in this PR)" note. MarketHawk's orchestrator sets
    `FACTORY_BUILD_NETWORK` and `BUILDKIT_HOST=markethawk-buildkit` in
    `deploy/instances/markethawk/instance.env` (human-only path). It runs
    `docker network inspect markethawk_factory-network` first. It then verifies from a
    MarketHawk run container that `getent hosts "$BUILDKIT_HOST"` resolves and
    `docker buildx create --driver remote tcp://$BUILDKIT_HOST:$BUILDKIT_PORT` succeeds.
    It should consider a dedicated build-only network for `markethawk-buildkit` instead
    of `markethawk_factory-network`;
  - an explicit "out of scope" line: `preview-up` delegation to target hooks (#15),
    socket-proxy/gate/breaker/budget changes, a scheduler startup warning for a missing
    build network.

---

## Self-review

- `**Issue:** #455` line present under the title. ✅
- No placeholders, except the commit-message slot in Step 3.6, which is filled with Task 0's
  real output at execution time by design. ✅
- Every spec requirement is mapped to a task:
  - R1/R2 → Task 3
  - R3 → Task 1 cases 1–3 and Step 3.5
  - R4 → Task 0 and the pre-flight evidence
  - R5 → Task 5
  - R6 → Task 6
  - R7 → Tasks 1–2
  - R8 → File Structure and Step 8.2
  - R9 → Step 8.4 and the "Nothing else" line
  - Architecture condition 2 ("a test pins it": `FACTORY_INSTANCE` normalization) →
    Task 1 instance-value loop + Step 5.5 documented-constraint grep ✅
- Spec Task 0 is kept verbatim in its essentials: `--entrypoint /bin/true`, no `-p`,
  variable unset, existing network, cleanup, results recorded, and stop-and-report on
  failure. This plan adds an explicit **Blocked** row, which also stops, and the
  `--name`/`FACTORY_INSTANCE` derivation that a run container needs. ✅
- Memory context consulted. No AVOID entry applies to compose/network work.
  Spec/plan transfer to the `feat/` branch is automatic (#387), so no manual copy step is
  needed. ✅
