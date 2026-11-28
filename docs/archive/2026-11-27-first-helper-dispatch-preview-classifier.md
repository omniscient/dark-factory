# First Helper Dispatch — Scheduler Lane for the `preview-helper` Container: Implementation Plan

**Issue:** #587

> **For agentic workers:** execute the tasks in order, one step at a time, and do not skip a
> "verify" step. Every listing below was run, as written, in a scratch clone of `main` at
> `d459e67` (#642 merged) before this plan was published; the expected outputs are the
> measured ones. Steps use `- [ ]` checkboxes.

**Goal:** Dispatch #642's inert `preview-helper` container from the scheduler as a **shadow,
non-gating** classifier of a changeset the implement run already pushed. The changes are: one
sourced scheduler file (stage, separate dispatch function, finalise/reap step), one gated
changeset flag in `dispatch()`, one marked block in `push-and-pr`, a documentation-only config
key, docs, and three new test files wired into CI. Nothing reads the helper's output. Merging
activates nothing.

**Architecture:** As in the approved spec
(`docs/superpowers/specs/2026-11-27-first-helper-dispatch-preview-classifier-design.md`,
approved at `adb592b`, copied verbatim onto this branch). `scheduler.sh` sources
`scripts/preview_helper_dispatch.sh`. `preview_helper_finalize` is the first call of every poll
(before `check_rate_limit`, the board fetch and the WIP capacity guard). `preview_helper_check`
is the last stage, behind the same guard as `deps_audit_check`. `dispatch_preview_helper` never
calls `provider_env_flags` and passes no `-e`: the three per-dispatch values reach compose by
function-local `local -x`. Every file read and write on the shared state volume goes through
#642's merged verbs in `scripts/factory_core/preview_helper.py` (`sweep`, `expired-keys`,
`next-candidate`, `prepare-dir`, `clear`, `write-pointer`, `write-inflight`, `read-inflight`,
`record-outcome --ledger-path`), used as merged. The scheduler is the only writer of the
helper's `runs.jsonl` row.

**Tech Stack:** bash (scheduler, sourced under `set -euo pipefail`), Python 3.12 (#642's
module, unchanged), jq, Archon workflow YAML, pytest, and hermetic bash tests (stubbed `docker`
and providers CLI; no container, endpoint or token).

**Out of scope (unchanged by this plan):** `run-compose.yml`, `scripts/preview_helper_entry.sh`,
`scripts/factory_core/preview_helper.py`, `scripts/factory_core/providers/cli.py`, `entrypoint.sh`,
`Dockerfile`, `run_record.py`, `model_proxy.py`, `scripts/provider_env.sh`
(`PROVIDER_PASSTHROUGH_VARS` is not edited), every `gate_*`, `breaker.py`, budgets, WIP or ceiling
logic, `is_issue_running`, `deploy/**`, `.github/workflows/publish.yml`. Also out of scope: the
checker, its proxy, `independent_acceptance` and the checker cap (#641), any registry entry, and
energy telemetry (#637). The operator note of 2026-11-26 (baked-CLI fallback for
`--hosting-location`) landed with #633 (`tests/test_entrypoint_hosting_fallback.sh`) and is not
repeated here.

---

## Named plan corrections to the spec's listing

The spec's scheduler listing is binding, except for the corrections below. Each one was found by
running the spec's listing verbatim through this plan's test file. That run gives
`Results: 68 passed, 5 failed` (before the two cases added after architect review). This plan's listing gives `81 passed, 0 failed`. Every property
the spec states is kept; these corrections only restore properties the listing failed to deliver.

| # | Spec listing | This plan | Why (measured) |
|---|---|---|---|
| C1 | First `_ph_write_inflight … "$now" 0` | `… "$now" 1` | Merged #642 `read-inflight` refuses `total_seconds` ≤ 0 (operator note of 2026-11-27). With `0`, a failure before the entry is resolved (`registry_stage_failed`, `endpoint_not_resolved`) is read as an *unreadable* in-flight file: no run record. That breaks acceptance criterion 4 (both cases fail). `1` is safe because nothing is started before the second write replaces it with the entry's own limit. |
| C2 | Bare pipelines and command substitutions in `preview_helper_finalize`, `preview_helper_proxy_down` and `preview_helper_check` | Each ends in `\|\| …` | `scheduler.sh` runs under `set -euo pipefail` and calls both functions bare. In the spec's listing, `docker ps \| grep -E … \| while …` returns grep's 1 when no helper runs. So a FIFO planted at `preview-helper-inflight.json` **ends the scheduler** before the entry is cleared, and it ends again on every restart (a crash loop). `limit=$(_ph_entry \| jq …)` ends it the same way when the registry cannot be read while an entry exists. Both are reproduced by the new `errexit:` cases. |
| C3 | `[ "$(get_retry_count …)" -ge "$max" ]` | `_ph_count` (a number, or `-1` = unreadable → `skip reason=state_unreadable`; a stamp that is not a plain `0` counts as stamped) | It fails closed when the state reader prints nothing. Before, `[ "" -ge 20 ]` was false and the lane dispatched. |
| C4 | `breaker-set-retry` for the stamp, unchecked | Write, then read the stamp back; if it is absent, `skip reason=stamp_write_failed`, clear, no dispatch | `breaker-set-retry` exits 0 even when it cannot write (`breaker._write_key` swallows `OSError`; measured with a read-only state dir). Unchecked, an unwritable state file would re-dispatch the same SHA every poll. |
| C5 | `… "$now" 0 \n    \|\| {` (a stray literal `\n`) | A line continuation | Cosmetic: the spec's line passes an extra argument `n`. |

Plan-level choices, for the reviewer:

- **Criterion 2's "byte-identical to main"** is measured against `dispatch()` with the six-line
  `#587` block removed, cut from `scheduler.sh`'s own text. CI's shallow checkout has no `main`
  ref. A precondition check asserts that exactly six lines were removed, so the comparison cannot
  pass vacuously.
- **Criterion 1's "never calls `provider_env_flags`"** is checked on `declare -f` bodies. The
  file's header comment names the function on purpose.
- **The static "no shell write onto the volume" check** allows `docker rm -f` and
  `compose … rm -sf preview-helper-proxy`, which the spec's own listing uses.
- **`docs/scheduler.md` is also updated** (poll-loop diagram, stage table, command table,
  state-volume table). The spec's files-touched table names only `configuration.md` and
  `run-lifecycle.md`, but `scheduler.md` is the document that describes the poll loop, so
  leaving it out would make it wrong. The change is docs only.
- **Extra cases** beyond the spec's Test Plan names: the `errexit:` section,
  `stamp_write_failed`, `state_unreadable` (a state file that is valid JSON but not an object
  makes `breaker-get` fail), and the six-line precondition. Every spec-named test exists under its
  exact spec name; where one name covers two assertions, both labels carry the full name with a
  bracketed suffix.

## Review amendments (2026-11-27, from executing this plan end to end)

The plan was executed task by task on a fresh export of `main` at `d459e67`, in the factory
image with no network. Every stated result was observed (23/53, the four named failures, 76/0,
3/5 then 8/0, the docs failures then passes); the Python suite went from 4432 to 4434 passed and
all 37 `ci.yml` shell steps gave the same result as on `main`. The spec's listing verbatim, before these amendments,
gave 70 passed, 6 failed on this test file. 37 mutants of the lane, the `scheduler.sh` call
sites and the `push-and-pr` block were run against the three test files; the amendments below
close the ones that survived. No product line changes.

1. **Fixture address.** The registry stub used `http://10.0.0.5:8000`; committed fixtures use
   the documentation range only. It is now `http://192.0.2.10:8000` (two places).
2. **`a_cloud_entry_is_never_dispatched`.** Removing `hosting_location == "local"` from
   `_ph_entry` passed every test: the stub's cloud entry had no `base_url`, so it failed for
   another reason. The new case selects a complete cloud entry.
3. **`one_helper_at_a_time`.** Removing `! _ph_inflight_present || return 0` from the stage
   passed every test, because the stale-proxy check always declined first. The new case has an
   in-flight entry and no proxy listed.
4. **Three more `errexit:` cases.** Removing the guard on `ids=$(preview_helper_proxy_ids)` or on
   the failure-counter bump in the unreadable-entry path passed every test. The `docker` stub
   gains a `ps_fails` flag, and the cases cover a failing `docker ps` and an unwritable counter
   on that path.

Corrections C1, C2 and C4 are each caught by a test when reverted. C3 is caught only when both
of its counter reads are reverted together (`every_declined_dispatch_is_logged
[state_unreadable]`); reverting one alone is masked by the other, and with both reverted the
stage still declines, at the stamp check. C3 is defence in depth and loosens nothing.

Known, pre-existing and unchanged by this plan: a FIFO or a device planted at
`scheduler-state.json` blocks or kills the scheduler on `main` too (its own state reads), and
no `docker` call in the scheduler, the lane's included, has a timeout.

---

## File Structure

| File | Change | Task |
|---|---|---|
| `tests/test_preview_helper_scheduler.sh` | new: hermetic tests for criteria 1–14, the real `scheduler.sh` sourced | 1 |
| `.github/workflows/ci.yml` | two `- run: bash tests/…` steps in the `tests` job | 1, 3 |
| `scripts/preview_helper_dispatch.sh` | new, sourced by `scheduler.sh`: stage, dispatch function, finalise step | 2 |
| `scheduler.sh` | one `source` line, the flag `case` in `dispatch()`, two call sites in the main loop | 2 |
| `tests/test_preview_changeset_write.sh` | new: the marked `push-and-pr` block, run for real (criterion 15) | 3 |
| `tests/test_preview_helper_lane.py` | new: `preview-up` and `classify-preview` regression guards (criterion 16) | 3 |
| `workflows/archon-dark-factory.yaml` | the marked block in `push-and-pr`, nothing else | 3 |
| `config/config.yaml` | a documentation-only `preview_helper_dispatch:` block (one leaf key) | 4 |
| `docs/configuration.md` | leaf count 110 → 111 (the Layer 1 intro and the "Counting" total, which also gains a `preview_helper_dispatch` 1 entry), the key's Layer 1 table, four variables, "The helper lane (#587)" activation paragraph | 4 |
| `docs/run-lifecycle.md` | the helper command row and how its run is finalised | 4 |
| `docs/scheduler.md` | poll-loop diagram and its line range, stage P7, command row, three state-volume rows | 4 |
| `.archon/memory/architecture.md` | append the two #587 entries approved with the spec | 5 |

The spec itself is already on this branch (copied verbatim from `adb592b`), so
`transfer_refine_artifacts.sh` picks up spec and plan from the same refine branch.

Blast radius (`scripts/gate_blast_radius.py` on the finished change in the scratch clone):
`STATUS: HUMAN_REQUIRED`, one trigger, `.github/workflows/ci.yml (category: migration-seed)`.
The same list without `ci.yml` gives `STATUS: PASS`. This is intended (spec, "Blast radius"):
keep both new shell tests as their own `ci.yml` steps, and do not fold them into an existing
wired test.

**Existing tests this change breaks:** two, both fixed inside this plan.
`tests/test_ci_covers_shell_tests.py::test_every_shell_test_is_wired_or_exempt` fails while a
new `tests/*.sh` exists without its `ci.yml` step, so Tasks 1 and 3 add the step in the same
step that creates the file.
`tests/test_docs_conformance_reference_docs.py::test_configuration_states_the_current_leaf_key_count`,
and `scripts/docs_refs_check.py`, fail between Task 4's config edit and its docs edit. Measured
on the finished scratch clone: `python -m pytest tests/` gives `4434 passed`, and every
`ci.yml` shell step returns the same result as on unchanged `main`.

**Sandbox note:** in the factory sandbox, six `ci.yml` shell tests already fail on unchanged
`main` (`test_identity.sh`, `test_provider_env_flags.sh`, `test_entrypoint_session_window.sh`,
`test_entrypoint_blast_hold.sh`, `test_entrypoint_error_signature.sh`,
`test_entrypoint_cost_report_regression.sh`). The causes are leaked `JIRA_*`/identity variables
and no `/var/lib/dark-factory`. Compare against `main` before attributing a failure to this
change.

---

## Task 1: The scheduler-lane tests (failing first)

**Files:** `tests/test_preview_helper_scheduler.sh` (new), `.github/workflows/ci.yml`

- [ ] **Step 1.1: Create `tests/test_preview_helper_scheduler.sh` with exactly this content**

The test names are the spec's Test Plan names (criteria 1–14, 6a, 12a, 12b). `docker` is a bash
function backed by files under `$FAKE`. The providers CLI is answered from `$FAKE` (stage
digest, `show.json`). The module and the breaker CLI run for real against a temp state dir.

```bash
#!/usr/bin/env bash
# Hermetic tests for the preview-helper shadow lane (#587): scripts/preview_helper_dispatch.sh,
# sourced through the real scheduler.sh (SCHEDULER_SOURCE_ONLY=1). No container is started and
# no endpoint is called: `docker` and the providers CLI are stubs driven by files under $FAKE;
# the module (scripts/factory_core/preview_helper.py) and the breaker CLI run for real.
# Run: bash tests/test_preview_helper_scheduler.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
SCHED="$REPO/scheduler.sh"
LANE="$REPO/scripts/preview_helper_dispatch.sh"
export FACTORY_CORE_CLI="$REPO/scripts/factory_core/cli.py"
export FACTORY_PROVIDERS_CLI="$REPO/scripts/factory_core/providers/cli.py"  # never run: stubbed

WORK=$(mktemp -d /tmp/ph-sched-XXXXXX)
trap 'rm -rf "$WORK"' EXIT
FAKE="$WORK/fake"            # docker / providers-CLI stub state
CALLS="$WORK/calls.log"      # every docker and python3 call, one per line
DOCKER_ENV_DIR="$WORK/docker-env"   # compose argv + environment, one file per compose call
mkdir -p "$FAKE" "$DOCKER_ENV_DIR"
: > "$CALLS"
export FAKE CALLS DOCKER_ENV_DIR
FAKE_DIGEST=$(printf 'a%.0s' {1..64})
export FAKE_DIGEST
SHA_A=$(printf '1%.0s' {1..40}); SHA_B=$(printf '2%.0s' {1..40}); SHA_C=$(printf '3%.0s' {1..40})

# ---- Stubs ----
# docker: running container names in $FAKE/running; proxies in $FAKE/proxies as
# "<id> <project> <service> <running:true|false>". Flags in $FAKE: proxy_up_fails, proxy_exits,
# run_fails. Every compose call also leaves its argv and its sorted environment in
# $DOCKER_ENV_DIR/<n>.argv / <n>.env.
docker() {
  echo "docker $*" >> "$CALLS"
  touch "$FAKE/running" "$FAKE/proxies"
  case "$1" in
    compose)
      local n; n=$(ls "$DOCKER_ENV_DIR" | grep -c '\.argv$' || true)
      printf '%s\n' "$@" > "$DOCKER_ENV_DIR/$n.argv"
      env | LC_ALL=C sort > "$DOCKER_ENV_DIR/$n.env"
      local project="${FACTORY_INSTANCE:-dark-factory}"
      case " $* " in
        *" up -d preview-helper-proxy "*)
          [ -f "$FAKE/proxy_up_fails" ] && return 1
          if [ -f "$FAKE/proxy_exits" ]; then
            echo "pxnew $project preview-helper-proxy false" >> "$FAKE/proxies"
          else
            echo "pxnew $project preview-helper-proxy true" >> "$FAKE/proxies"
          fi
          return 0 ;;
        *" rm -sf preview-helper-proxy "*)
          grep -v " $project preview-helper-proxy " "$FAKE/proxies" > "$FAKE/p.tmp" || true
          mv "$FAKE/p.tmp" "$FAKE/proxies"; return 0 ;;
        *" --profile preview-helper run "*)
          [ -f "$FAKE/run_fails" ] && return 1
          local name="" prev=""
          for a in "$@"; do [ "$prev" = "--name" ] && name="$a"; prev="$a"; done
          echo "$name" >> "$FAKE/running"; return 0 ;;
        *) return 0 ;;   # an ordinary dispatch() run
      esac ;;
    ps)
      [ -f "$FAKE/ps_fails" ] && return 1
      if [ "${2:-}" = "-aq" ]; then
        local project service
        project=$(printf '%s\n' "$@" | sed -n 's/^label=com.docker.compose.project=//p')
        service=$(printf '%s\n' "$@" | sed -n 's/^label=com.docker.compose.service=//p')
        awk -v p="$project" -v s="$service" '$2==p && $3==s {print $1}' "$FAKE/proxies"
      else
        cat "$FAKE/running"
      fi
      return 0 ;;
    inspect)
      awk -v id="${@: -1}" '$1==id {print $4}' "$FAKE/proxies"; return 0 ;;
    stop) return 0 ;;
    rm)
      shift; [ "${1:-}" = "-f" ] && shift
      local x
      for x in "$@"; do
        grep -vxF -- "$x" "$FAKE/running" > "$FAKE/r.tmp" || true; mv "$FAKE/r.tmp" "$FAKE/running"
        awk -v id="$x" '$1!=id' "$FAKE/proxies" > "$FAKE/p.tmp"; mv "$FAKE/p.tmp" "$FAKE/proxies"
      done
      return 0 ;;
  esac
  return 0
}
# python3: providers-CLI calls are answered from $FAKE (stage digest, show.json) and never run;
# tracker / codehost calls are only logged (the tests assert there are none). Everything else
# (the module, the breaker CLI, uuid) is the real interpreter.
_REAL_PY3="$(command -v python3)"
export _REAL_PY3
python3() {
  echo "python3 $*" >> "$CALLS"
  case "$*" in
    *providers/cli.py*"model-endpoints stage"*)
      [ -f "$FAKE/stage_fails" ] && return 1
      echo "$FAKE_DIGEST"; return 0 ;;
    *providers/cli.py*"model-endpoints show"*)
      [ -f "$FAKE/show.json" ] || return 1
      cat "$FAKE/show.json"; return 0 ;;
    *providers/cli.py*) return 0 ;;
    *) "$_REAL_PY3" "$@" ;;
  esac
}
export -f docker python3

# ---- Source the real scheduler (helpers only) ----
SCHEDULER_STATE_DIR="$WORK/state"
mkdir -p "$SCHEDULER_STATE_DIR"
export SCHEDULER_STATE_DIR
unset FACTORY_RUN_PREFIX
export FACTORY_INSTANCE=dark-factory
export FACTORY_WIP_LIMIT=1 POLL_INTERVAL=60 MAX_RETRIES=3 RATE_LIMIT_FLOOR=200
SCHEDULER_SOURCE_ONLY=1 source "$SCHED"
# scheduler.sh turns on `set -euo pipefail`; the cases below assert on failures, so errexit is
# off for the runner. The "errexit" section at the end runs both call sites under it.
set +e
PREVIEW_HELPER_PROXY_SETTLE_SECONDS=1
PH_DIR="$SCHEDULER_STATE_DIR/preview-helper"
PH_INFLIGHT="$SCHEDULER_STATE_DIR/preview-helper-inflight.json"
PENDING="$SCHEDULER_STATE_DIR/preview-pending"
RUNS="$SCHEDULER_STATE_DIR/runs.jsonl"
LEDGER="$SCHEDULER_STATE_DIR/request-ledger.jsonl"
HELPER_NAME="${FACTORY_RUN_PREFIX}ph-"

# ---- Runner ----
PASSED=0; FAILED=0
pass() { echo "  PASS: $1"; PASSED=$((PASSED+1)); }
fail() { echo "  FAIL: $1" >&2; FAILED=$((FAILED+1)); }
check() { local desc="$1"; shift; if "$@"; then pass "$desc"; else fail "$desc"; fi; }

PASSTHROUGH_DUMMY() { local v; for v in "${PROVIDER_PASSTHROUGH_VARS[@]}"; do export "$v=dummy-$v"; done; }
lane_on() { export PREVIEW_HELPER_DISPATCH_ENABLED=true PREVIEW_HELPER_ENDPOINT=local-a; }
lane_off() { unset PREVIEW_HELPER_DISPATCH_ENABLED PREVIEW_HELPER_ENDPOINT; }

# A fresh state volume, fake docker and lane switched on, for each case.
reset_case() {
  rm -rf "$SCHEDULER_STATE_DIR" "$FAKE" "$DOCKER_ENV_DIR"
  mkdir -p "$SCHEDULER_STATE_DIR" "$FAKE" "$DOCKER_ENV_DIR" "$PENDING"
  echo '{}' > "$STATE_FILE"
  : > "$CALLS"; : > "$FAKE/running"; : > "$FAKE/proxies"
  cat > "$FAKE/show.json" <<'JSON'
{"registry": "/etc/dark-factory/model-registry/registry.yaml", "endpoints": [
 {"id": "cloud-a", "hosting_location": "cloud", "timeouts": {"total_seconds": 600}},
 {"id": "local-a", "hosting_location": "local", "base_url": "http://192.0.2.10:8000",
  "model_identity": {"served_name": "local-model-a"}, "timeouts": {"total_seconds": 120},
  "secret_reference": {"name": "FACTORY_HELPER_MODEL_SECRET", "present": true}}]}
JSON
  unset PREVIEW_HELPER_DAILY_MAX
  lane_on
  DISPATCHED=""
}
make_pending() {  # issue sha
  jq -n --argjson i "$1" --arg s "$2" \
    '{issue_number: $i, head_sha: $s, changed_files: ["docs/a.md"], diff_stat: " docs/a.md | 1 +",
      title: "t", labels: ["x"]}' > "$PENDING/$1-$2.json"
}
run_id() { jq -r .run_id "$PH_INFLIGHT"; }
helper_exits() { : > "$FAKE/running"; }
write_result_ok() {
  printf '%s' '{"schema_version":1,"outcome":"ok","needs_preview":false,"category":"docs","failure_class":null}' > "$PH_DIR/result.json"
}
write_result_abstained() {  # class
  printf '{"schema_version":1,"outcome":"abstained","needs_preview":true,"category":null,"failure_class":"%s"}' "$1" > "$PH_DIR/result.json"
}
write_ledger_ok() {  # run_id
  jq -cn --arg r "$1" '{run_id: $r, method: "POST", endpoint: "/v1/messages", status: 200,
    failure_class: null, endpoint_id: "local-a", hosting_location: "local", role: "actor",
    correlation: "inferred_pointer",
    payload_decision: {payload_class: "local_only", endpoint_id: "local-a",
                       secret_reference: "FACTORY_HELPER_MODEL_SECRET", allow: true, reason: null}}' >> "$LEDGER"
}
failures() { get_retry_count "$PREVIEW_HELPER_FAILURE_KEY"; }
last_row() { tail -1 "$RUNS" | jq -r "$1"; }
rows() { [ -f "$RUNS" ] && grep -c . "$RUNS" || echo 0; }
# A dispatch that ends ok: pending file, stage, helper writes ok + ledger row, exits, finalise.
dispatch_ok() {  # issue sha
  make_pending "$1" "$2"; preview_helper_check >/dev/null
  local rid; rid=$(run_id); write_result_ok; write_ledger_ok "$rid"; helper_exits
  preview_helper_finalize >/dev/null
}
# A dispatch that ends abstained (compose run refused): dispatch_failed.
dispatch_abstain() {  # issue sha
  make_pending "$1" "$2"; touch "$FAKE/run_fails"; preview_helper_check >/dev/null; rm -f "$FAKE/run_fails"
}
no_tracker_or_codehost_call() { ! grep -qE 'providers/cli\.py (tracker|codehost)' "$CALLS"; }
only_lane_keys_in_state() { [ -z "$(jq -r 'keys[] | select(startswith("preview_helper:") | not)' "$STATE_FILE")" ]; }
docker_calls_are_the_lanes() {
  ! grep '^docker ' "$CALLS" | grep -vE '^docker (ps|inspect -f|stop -t 5|rm -f|compose -f /opt/dark-factory/docker-compose.yml --profile preview-helper(-proxy)? )' | grep -q .
}

# =====================================================================
echo "--- 1: helper dispatch carries no -e and never calls provider_env_flags ---"
reset_case; PASSTHROUGH_DUMMY
make_pending 587 "$SHA_A"
preview_helper_check >/dev/null
check "helper_dispatch_argv_has_no_dash_e_and_never_calls_provider_env_flags [argv: no compose call of the lane carries -e]" \
  bash -c '! grep -qxF -- "-e" "$0"/*.argv' "$DOCKER_ENV_DIR"
lane_bodies() { declare -f dispatch_preview_helper preview_helper_check preview_helper_finalize \
  preview_helper_proxy_up preview_helper_proxy_down _ph_entry _ph_py _ph_compose; }
check "helper_dispatch_argv_has_no_dash_e_and_never_calls_provider_env_flags [bodies: no provider_env_flags call, comments excluded]" \
  bash -c '! grep -q provider_env_flags <<<"$0"' "$(lane_bodies)"
RUN_ENV=$(grep -lx 'preview-helper' "$DOCKER_ENV_DIR"/*.argv | head -1); RUN_ENV="${RUN_ENV%.argv}.env"
check "the helper start sees the staged digest and the entry's served name and base URL" \
  bash -c 'grep -qx "FACTORY_MODEL_REGISTRY_SHA256=$1" "$0" && grep -qx "PREVIEW_HELPER_ENDPOINT_SERVED_NAME=local-model-a" "$0" && grep -qx "PREVIEW_HELPER_ENDPOINT_BASE_URL=http://192.0.2.10:8000" "$0"' "$RUN_ENV" "$FAKE_DIGEST"
check "the digest is not left exported in the scheduler's shell" test -z "${FACTORY_MODEL_REGISTRY_SHA256:-}"
for v in "${PROVIDER_PASSTHROUGH_VARS[@]}"; do unset "$v"; done

# =====================================================================
echo "--- 2/3: existing dispatches are byte-identical with the lane off; lane on adds one flag ---"
# The baseline is dispatch() with the #587 case block removed, taken from scheduler.sh's own
# text, so the comparison is against the function as it was before this change.
DISPATCH_TEXT=$(sed -n '/^dispatch() {/,/^}/p' "$SCHED")
BASE_TEXT=$(printf '%s\n' "$DISPATCH_TEXT" | sed '/# #587: the one changeset flag/,/^  esac$/d')
check "  (precondition) the baseline is dispatch() minus exactly the six-line #587 block" \
  test "$(( $(wc -l <<<"$DISPATCH_TEXT") - $(wc -l <<<"$BASE_TEXT") ))" -eq 6
eval "$(printf '%s\n' "$BASE_TEXT" | sed '1s/^dispatch()/baseline_dispatch()/')"
COMMANDS=("Fix issue #5" "Continue issue #5" "Close issue #5" "Refine issue #5" "Plan issue #5"
          "Deconflict issue #5" "Recheck main" "Fix main" "Audit deps")
record_dispatch() {  # fn command -> argv and env of its one compose call
  rm -rf "$DOCKER_ENV_DIR"; mkdir -p "$DOCKER_ENV_DIR"
  "$1" "$2" >/dev/null 2>&1
  cat "$DOCKER_ENV_DIR/0.argv"; echo "--env--"; cat "$DOCKER_ENV_DIR/0.env"
}
variant() {  # name: unset | switch_only | endpoint_only | switch_false | enabled
  lane_off
  case "$1" in
    switch_only) export PREVIEW_HELPER_DISPATCH_ENABLED=true ;;
    endpoint_only) export PREVIEW_HELPER_ENDPOINT=local-a ;;
    switch_false) export PREVIEW_HELPER_DISPATCH_ENABLED=false PREVIEW_HELPER_ENDPOINT=local-a ;;
    enabled) lane_on ;;
  esac
}
reset_case; PASSTHROUGH_DUMMY
for v in unset switch_only endpoint_only switch_false; do
  variant "$v"; same=0
  for c in "${COMMANDS[@]}"; do
    [ "$(record_dispatch dispatch "$c")" = "$(record_dispatch baseline_dispatch "$c")" ] || same=1
  done
  check "existing_dispatches_are_byte_identical_with_the_lane_off [$v] (argv and compose-process env, 9 commands)" test "$same" -eq 0
done
variant unset; : > "$CALLS"; preview_helper_check; preview_helper_finalize
check "lane_off_issues_nothing (no docker, no python call)" test ! -s "$CALLS"
strip_flag() {  # drop the one "-e" "PREVIEW_HELPER_CHANGESET_FLAG=true" pair
  awk '{l[NR]=$0} END {for (i = 1; i <= NR; i++) {
         if (l[i] == "-e" && l[i+1] == "PREVIEW_HELPER_CHANGESET_FLAG=true") { i++; continue }
         print l[i] }}'
}
variant enabled; only=0
for c in "${COMMANDS[@]}"; do
  new=$(record_dispatch dispatch "$c"); base=$(record_dispatch baseline_dispatch "$c")
  case "$c" in
    "Fix issue #5"|"Continue issue #5")
      [ "$(printf '%s\n' "$new" | grep -cxF 'PREVIEW_HELPER_CHANGESET_FLAG=true')" = "1" ] || only=1
      [ "$(printf '%s\n' "$new" | strip_flag)" = "$base" ] || only=1 ;;
    *) [ "$new" = "$base" ] || only=1 ;;
  esac
done
check "lane_on_adds_only_the_changeset_flag_to_fix_and_continue" test "$only" -eq 0
for v in "${PROVIDER_PASSTHROUGH_VARS[@]}"; do unset "$v"; done
unset FACTORY_MODEL_ENDPOINT; lane_on; export FACTORY_HELPER_MODEL_SECRET=s PREVIEW_HELPER_ENDPOINT_BASE_URL=x
: > "$CALLS"
check "cloud_endpoint_confirmed_ignores_the_helper_variables (guard)" cloud_endpoint_confirmed
check "  ... and makes no call" test ! -s "$CALLS"
unset FACTORY_HELPER_MODEL_SECRET PREVIEW_HELPER_ENDPOINT_BASE_URL

# =====================================================================
echo "--- 4: every scheduler-decided failure class ends abstained with a record ---"
fail_case() {  # class setup-fn
  reset_case; make_pending 41 "$SHA_A"; "$2"
  preview_helper_check >/dev/null
  case "$1" in
    wall_clock_exceeded)
      _ph_py write-inflight --file "$PH_INFLIGHT" --run-id "$(run_id)" --issue 41 --head-sha "$SHA_A" \
        --started $(( $(date +%s) - 200 )) --total 120
      preview_helper_finalize >/dev/null ;;
    kill_switch) lane_off; preview_helper_finalize >/dev/null ;;
  esac
  local ok=0
  [ "$(rows)" = "1" ] || ok=1
  [ "$(last_row .outcome)" = "abstained" ] && [ "$(last_row .failure_class)" = "$1" ] || ok=1
  [ "$(last_row .needs_preview)" = "true" ] && [ "$(last_row .cost_usd)" = "null" ] || ok=1
  [ "$(failures)" = "1" ] || ok=1
  [ ! -e "$PH_INFLIGHT" ] && [ -z "$(cat "$FAKE/running")" ] && [ -z "$(cat "$FAKE/proxies")" ] || ok=1
  no_tracker_or_codehost_call || ok=1
  only_lane_keys_in_state || ok=1
  docker_calls_are_the_lanes || ok=1
  check "every_scheduler_failure_class_ends_abstained_with_a_record [$1]" test "$ok" -eq 0
}
setup_stage_fails() { touch "$FAKE/stage_fails"; }
setup_no_entry() { rm -f "$FAKE/show.json"; }
setup_proxy_fails() { touch "$FAKE/proxy_exits"; }
setup_run_fails() { touch "$FAKE/run_fails"; }
setup_none() { :; }
fail_case registry_stage_failed setup_stage_fails
fail_case endpoint_not_resolved setup_no_entry
fail_case proxy_start_failed setup_proxy_fails
fail_case dispatch_failed setup_run_fails
fail_case wall_clock_exceeded setup_none
fail_case kill_switch setup_none

# =====================================================================
echo "--- 5: failure stop ---"
reset_case
dispatch_abstain 51 "$SHA_A"; dispatch_abstain 52 "$SHA_A"
make_pending 53 "$SHA_A"
out=$(preview_helper_check)
check "two_abstentions_stop_the_stage_until_reset (logged, nothing dispatched)" \
  bash -c '[[ "$0" == *"skip reason=failure_stop"* ]] && [ ! -e "$1" ]' "$out" "$PH_INFLIGHT"
STATE_FILE="$STATE_FILE" "$_REAL_PY3" "$FACTORY_CORE_CLI" breaker-reset --key preview_helper:failures
preview_helper_check >/dev/null
check "  ... and dispatches again after breaker-reset" test -e "$PH_INFLIGHT"
reset_case
dispatch_abstain 54 "$SHA_A"
dispatch_ok 55 "$SHA_A"
check "an_ok_resets_the_count" test "$(failures)" = "0"
check "  ... the ok row is ok / null / needs_preview false" \
  test "$(last_row '[.outcome, .failure_class, .needs_preview] | @tsv')" = "$(printf 'ok\t\tfalse')"
reset_case
make_pending 56 "$SHA_A"; preview_helper_check >/dev/null
write_result_abstained input_too_large; helper_exits; preview_helper_finalize >/dev/null
check "input_too_large_counts_toward_the_stop" \
  test "$(failures):$(last_row .failure_class)" = "1:input_too_large"

# =====================================================================
echo "--- 6: one dispatch per issue and head SHA ---"
reset_case
dispatch_ok 61 "$SHA_A"; make_pending 61 "$SHA_A"; DISPATCHED=""
preview_helper_check >/dev/null
check "a_stamped_sha_is_never_dispatched_again [after ok]" bash -c '[ -z "$0" ] && [ ! -e "$1" ]' "$DISPATCHED" "$PH_INFLIGHT"
reset_case
dispatch_abstain 62 "$SHA_A"; make_pending 62 "$SHA_A"; DISPATCHED=""
preview_helper_check >/dev/null
check "a_stamped_sha_is_never_dispatched_again [after abstained]" test -z "$DISPATCHED"
reset_case
STATE_FILE="$STATE_FILE" "$_REAL_PY3" "$FACTORY_CORE_CLI" breaker-set-retry --key "preview_helper:stamp:63:$SHA_A" --value 7
make_pending 63 "$SHA_A"; preview_helper_check >/dev/null
check "a_stamped_sha_is_never_dispatched_again [hand-written stamp value]" test -z "$DISPATCHED"
reset_case
"$_REAL_PY3" - "$STATE_FILE" "$SHA_A" <<'PY'
import json, sys
path, sha = sys.argv[1], sys.argv[2]
state = {f"pad:{i}": "x" * 1000 for i in range(9300)}   # > 8 MiB: the module reads it as empty
state[f"preview_helper:stamp:64:{sha}"] = 1
json.dump(state, open(path, "w"))
PY
make_pending 64 "$SHA_A"
out=$(preview_helper_check)
check "a_stamp_is_honoured_with_an_oversized_state_file" \
  bash -c '[[ "$0" == *"skip reason=already_stamped issue=#64"* ]] && [ -z "$1" ] && [ ! -e "$2/input.json" ] && [ "$3" = 0 ]' \
  "$out" "$DISPATCHED" "$PH_DIR" "$(rows)"

# =====================================================================
echo "--- 7: daily ceiling, expiry, and only the lane's own keys are reset ---"
DAY="preview_helper:day:$(date -u +%F)"
ceiling_case() {  # env-value expected-max
  reset_case
  if [ "$1" = "-" ]; then unset PREVIEW_HELPER_DAILY_MAX; else export PREVIEW_HELPER_DAILY_MAX="$1"; fi
  STATE_FILE="$STATE_FILE" "$_REAL_PY3" "$FACTORY_CORE_CLI" breaker-set-retry --key "$DAY" --value "$2"
  make_pending 71 "$SHA_A"
  local out; out=$(preview_helper_check)
  [[ "$out" == *"skip reason=daily_max count=$2"* ]] && [ ! -e "$PH_INFLIGHT" ]
}
check "the_daily_ceiling_stops_dispatch [default 20]" ceiling_case - 20
check "the_daily_ceiling_stops_dispatch [env 3]" ceiling_case 3 3
check "the_daily_ceiling_stops_dispatch [non-numeric env = 20]" ceiling_case abc 20
check "the_daily_ceiling_stops_dispatch [0 dispatches nothing]" ceiling_case 0 0
reset_case; export PREVIEW_HELPER_DAILY_MAX=5
dispatch_abstain 72 "$SHA_A"
check "  ... the count rises on a failed attempt too" test "$(get_retry_count "$DAY")" = "1"
reset_case
OLD=$(( $(date +%s) - 8 * 86400 ))
"$_REAL_PY3" - "$STATE_FILE" "$OLD" "$SHA_A" "$SHA_B" <<'PY'
import json, sys, time
path, old, a, b = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
json.dump({f"preview_helper:stamp:1:{a}": old, f"preview_helper:stamp:2:{b}": int(time.time()),
           "preview_helper:day:2020-01-01": 4, "587": 2}, open(path, "w"))
PY
preview_helper_check >/dev/null
check "expired_stamps_and_day_counts_are_deleted" \
  test "$(jq -c 'keys' "$STATE_FILE")" = "[\"587\",\"preview_helper:stamp:2:$SHA_B\"]"
reset_case
"$_REAL_PY3" - "$STATE_FILE" <<'PY'
import json, sys
json.dump({"123:implement": 2, "587:close": 1, "99:refine": 3,
           "preview_helper:stamp:x\n123:implement": 1, "preview_helper:stamp:\n587:close": 1,
           "preview_helper:day:2020-01-01\n99:refine": 1}, open(sys.argv[1], "w"))
PY
preview_helper_check >/dev/null
check "expiry_never_resets_a_key_that_is_not_the_lanes [forged keys]" \
  test "$(jq -c '[."123:implement", ."587:close", ."99:refine"]' "$STATE_FILE")" = "[2,1,3]"
reset_case
"$_REAL_PY3" - "$STATE_FILE" <<'PY'
import json, sys
json.dump({"123:implement": 2, "preview_helper:failures": 1,
           "preview_helper:day:2020-01-01:implement": 1}, open(sys.argv[1], "w"))
PY
_ph_py_real=$(declare -f _ph_py)
_ph_py() { if [ "$1" = expired-keys ]; then printf '%s\n' 123:implement preview_helper:failures preview_helper:day:2020-01-01:implement; else SCHEDULER_STATE_DIR="$SCHEDULER_STATE_DIR" python3 "${SCHEDULER_SCRIPTS_DIR}/factory_core/preview_helper.py" "$@"; fi; }
preview_helper_check >/dev/null
eval "$_ph_py_real"
check "expiry_never_resets_a_key_that_is_not_the_lanes [hostile verb output]" \
  test "$(jq -c '[."123:implement", ."preview_helper:failures", ."preview_helper:day:2020-01-01:implement"]' "$STATE_FILE")" = "[2,1,1]"

# =====================================================================
echo "--- 8: pause, capacity and an earlier dispatch ---"
loop_tail() {  # the main loop's call site for the stage, verbatim
  if [ "$SESSION_WINDOW_PAUSED" = "false" ] && [ -z "$DISPATCHED" ]; then
    preview_helper_check
  fi
}
reset_case; make_pending 81 "$SHA_A"
SESSION_WINDOW_PAUSED=true; loop_tail >/dev/null; p=$?
paused_ok=$([ ! -e "$PH_INFLIGHT" ] && echo 0 || echo 1)
SESSION_WINDOW_PAUSED=false; DISPATCHED="Fix issue #9"; loop_tail >/dev/null
prior_ok=$([ ! -e "$PH_INFLIGHT" ] && echo 0 || echo 1)
DISPATCHED=""
echo "dark-factory-dark-factory-run-abc" > "$FAKE/running"
cap_ok=$(factory_at_capacity "$(count_factory_running)" && echo 0 || echo 1)
check "stage_respects_pause_capacity_and_prior_dispatch" test "$paused_ok$prior_ok$cap_ok" = "000"
check "  ... the call site in scheduler.sh is exactly this guard, after stage_epic_autopilot" \
  bash -c 'awk "/^while true; do/{f=1} f" "$0" | grep -A3 "^  stage_epic_autopilot$" >/dev/null && awk "/^while true; do/{f=1} f" "$0" | grep -B1 "^    preview_helper_check$" | grep -qF "if [ \"\$SESSION_WINDOW_PAUSED\" = \"false\" ] && [ -z \"\$DISPATCHED\" ]; then"' "$SCHED"

# =====================================================================
echo "--- 9: WIP counting ---"
reset_case
printf '%s\n' "${HELPER_NAME}587" "dark-factory-preview-helper-proxy-1" > "$FAKE/running"
check "helper_name_counts_toward_wip_and_the_proxy_does_not" test "$(count_factory_running)" = "1"
make_pending 91 "$SHA_A"; : > "$FAKE/running"; preview_helper_check >/dev/null
check "  ... the started helper is named <prefix>ph-<issue>" grep -qxF "${HELPER_NAME}91" "$FAKE/running"

# =====================================================================
echo "--- 10: the reaper ---"
reset_case; make_pending 101 "$SHA_A"; preview_helper_check >/dev/null
check "  (precondition) the helper holds the only slot" factory_at_capacity "$(count_factory_running)"
_ph_py write-inflight --file "$PH_INFLIGHT" --run-id "$(run_id)" --issue 101 --head-sha "$SHA_A" \
  --started $(( $(date +%s) - 121 )) --total 120
preview_helper_finalize >/dev/null
check "reaper_runs_at_capacity_and_records_wall_clock_exceeded" \
  bash -c '[ "$0" = "abstained wall_clock_exceeded" ] && [ -z "$(cat "$1/running")" ] && [ -z "$(cat "$1/proxies")" ]' \
  "$(last_row '.outcome + " " + .failure_class')" "$FAKE"
slot_free() { ! factory_at_capacity "$(count_factory_running)"; }
check "  ... and the slot is free again" slot_free
reset_case; make_pending 102 "$SHA_A"; preview_helper_check >/dev/null
preview_helper_finalize >/dev/null
check "  ... a helper inside its limit is left running" bash -c '[ -e "$0" ] && grep -q ph-102 "$1/running"' "$PH_INFLIGHT" "$FAKE"
BODY=$(awk '/^while true; do/{f=1} f' "$SCHED")
pos() { printf '%s\n' "$BODY" | grep -nF -- "$1" | head -1 | cut -d: -f1; }
check "finalise_is_the_first_call_in_the_poll" \
  bash -c '[ "$0" -lt "$1" ] && [ "$1" -lt "$2" ] && [ "$2" -lt "$3" ] && [ "$0" -lt "$4" ]' \
  "$(pos '  preview_helper_finalize')" "$(pos '  check_rate_limit')" "$(pos 'BOARD_ITEMS=$(fetch_board_items')" \
  "$(pos 'if factory_at_capacity')" "$(pos 'BOT_RE=$(python3')"

# =====================================================================
echo "--- 11: stale proxy ---"
reset_case; make_pending 111 "$SHA_A"
echo "pxstale dark-factory preview-helper-proxy false" > "$FAKE/proxies"
out=$(preview_helper_check)
check "a_stale_proxy_of_this_project_blocks_and_another_projects_does_not [this project: logged, no dispatch, no count, no stamp]" \
  bash -c '[[ "$0" == *"skip reason=stale_proxy"* ]] && [ ! -e "$1" ] && [ "$2" = 0 ] && [ "$3" = 0 ] && grep -q pxstale "$4/proxies"' \
  "$out" "$PH_INFLIGHT" "$(failures)" "$(get_retry_count "preview_helper:stamp:111:$SHA_A")" "$FAKE"
echo "pxother markethawk preview-helper-proxy true" > "$FAKE/proxies"
preview_helper_check >/dev/null
check "a_stale_proxy_of_this_project_blocks_and_another_projects_does_not [another project: dispatched]" test -e "$PH_INFLIGHT"

# =====================================================================
echo "--- 12: the in-flight file is not trusted ---"
unreadable_case() {  # label setup-fn
  reset_case
  echo "${HELPER_NAME}7" > "$FAKE/running"; echo "${HELPER_NAME}abc" >> "$FAKE/running"
  "$2"
  local t0; t0=$(date +%s)
  preview_helper_finalize >/dev/null
  local ok=0
  [ $(( $(date +%s) - t0 )) -le 10 ] || ok=1
  ! [ -e "$PH_INFLIGHT" ] && ! [ -L "$PH_INFLIGHT" ] || ok=1
  [ "$(failures)" = "1" ] || ok=1
  [ "$(rows)" = "0" ] || ok=1
  ! grep -qxF "${HELPER_NAME}7" "$FAKE/running" || ok=1
  grep -qxF "${HELPER_NAME}abc" "$FAKE/running" || ok=1    # not a helper name: left alone
  [ ! -e /tmp/ph-pwned ] || ok=1
  check "an_unreadable_inflight_entry_is_cleaned_up [$1]" test "$ok" -eq 0
}
u_fifo() { mkfifo "$PH_INFLIGHT"; }
u_dir() { mkdir -p "$PH_INFLIGHT/x"; }
u_meta() {
  printf '%s' '{"run_id":"$(touch /tmp/ph-pwned)","issue":"7;touch /tmp/ph-pwned","head_sha":"`touch /tmp/ph-pwned`","started_epoch":"1","total_seconds":"../../x"}' > "$PH_INFLIGHT"
}
u_future() {
  _ph_py write-inflight --file "$PH_INFLIGHT" --run-id "$(printf 'a%.0s' {1..32})" --issue 7 \
    --head-sha "$SHA_A" --started $(( $(date +%s) + 3600 )) --total 120
}
u_limit() {
  _ph_py write-inflight --file "$PH_INFLIGHT" --run-id "$(printf 'a%.0s' {1..32})" --issue 7 \
    --head-sha "$SHA_A" --started "$(date +%s)" --total 99999999
}
unreadable_case fifo u_fifo
unreadable_case directory u_dir
unreadable_case shell_metacharacters u_meta
unreadable_case start_in_the_future u_future
unreadable_case limit_above_the_entry u_limit

echo "--- 12a: lane off ---"
reset_case; lane_off; : > "$CALLS"
cp "$STATE_FILE" "$WORK/state.before"
preview_helper_finalize; preview_helper_finalize; preview_helper_finalize
check "lane_off_and_no_inflight_entry_makes_no_call" \
  bash -c '[ ! -s "$0" ] && cmp -s "$1" "$2" && [ ! -e "$3" ]' "$CALLS" "$STATE_FILE" "$WORK/state.before" "$RUNS"
reset_case
STATE_FILE="$STATE_FILE" "$_REAL_PY3" "$FACTORY_CORE_CLI" breaker-set-retry --key preview_helper:failures --value 1
RID=$(printf 'b%.0s' {1..32})
mkdir -p "$PH_DIR"
_ph_py write-inflight --file "$PH_INFLIGHT" --run-id "$RID" --issue 55 --head-sha "$SHA_A" --started "$(date +%s)" --total 120
_ph_py write-pointer --dir "$PH_DIR" --run-id "$RID" --issue 55
write_result_ok; write_ledger_ok "$RID"
lane_off; preview_helper_finalize >/dev/null
first="$(last_row '.outcome + " " + .failure_class'):$(failures):$([ -e "$PH_INFLIGHT" ] && echo present || echo gone)"
: > "$CALLS"; preview_helper_finalize
check "lane_off_finalises_an_entry_once_as_kill_switch" \
  bash -c '[ "$0" = "abstained kill_switch:2:gone" ] && [ ! -s "$1" ] && [ "$2" = 1 ]' "$first" "$CALLS" "$(rows)"

echo "--- 12b: planted directories and declined dispatches ---"
reset_case; make_pending 121 "$SHA_A"
mkdir -p "$PH_DIR/input.json/deep" "$PH_DIR/current-run.json/deep"
preview_helper_check >/dev/null
check "a_directory_at_an_artifact_name_does_not_stop_the_lane" \
  bash -c '[ -e "$0" ] && [ -f "$1/input.json" ] && [ -f "$1/current-run.json" ]' "$PH_INFLIGHT" "$PH_DIR"
declined() {  # reason setup-fn
  reset_case; make_pending 122 "$SHA_A"; "$2"
  local out; out=$(preview_helper_check 2>&1)
  [[ "$out" == *"skip reason=$1"* ]]
}
d_dir() { rm -rf "$PH_DIR"; ln -s "$WORK" "$PH_DIR"; }
d_proxy() { echo "pxs dark-factory preview-helper-proxy true" > "$FAKE/proxies"; }
d_daily() { export PREVIEW_HELPER_DAILY_MAX=0; }
d_stop() { STATE_FILE="$STATE_FILE" "$_REAL_PY3" "$FACTORY_CORE_CLI" breaker-set-retry --key preview_helper:failures --value 2; }
# The module's own refusals, made to happen: its verb is replaced for one call.
with_verb_failing() {  # verb exit-code
  _PH_FAIL_VERB="$1"; _PH_FAIL_RC="$2"
  _ph_py() { if [ "$1" = "$_PH_FAIL_VERB" ]; then return "$_PH_FAIL_RC"; fi
             SCHEDULER_STATE_DIR="$SCHEDULER_STATE_DIR" python3 "${SCHEDULER_SCRIPTS_DIR}/factory_core/preview_helper.py" "$@"; }
}
d_input() { with_verb_failing next-candidate 2; }
d_inflight_w() { with_verb_failing write-inflight 1; }
d_stamp() { mkdir -p "$PH_DIR"; chmod 0555 "$SCHEDULER_STATE_DIR"; }
check "every_declined_dispatch_is_logged [artifact_dir_not_a_directory]" declined artifact_dir_not_a_directory d_dir
check "every_declined_dispatch_is_logged [stale_proxy]" declined stale_proxy d_proxy
check "every_declined_dispatch_is_logged [daily_max]" declined daily_max d_daily
check "every_declined_dispatch_is_logged [failure_stop]" declined failure_stop d_stop
d_state() { echo '[]' > "$STATE_FILE"; }   # valid JSON, not an object: the state reader fails
check "every_declined_dispatch_is_logged [state_unreadable]" declined state_unreadable d_state
check "every_declined_dispatch_is_logged [input_write_failed]" declined input_write_failed d_input
eval "$_ph_py_real"
check "every_declined_dispatch_is_logged [inflight_write_failed]" declined inflight_write_failed d_inflight_w
eval "$_ph_py_real"
if [ "$(id -u)" != 0 ]; then   # root ignores the mode bits
  check "every_declined_dispatch_is_logged [stamp_write_failed]" declined stamp_write_failed d_stamp
  chmod 0755 "$SCHEDULER_STATE_DIR"
fi
reset_case; out=$(preview_helper_check 2>&1)
check "  ... only 'no candidate' is silent" test -z "$out"

# =====================================================================
echo "--- 13: planted links are never followed ---"
reset_case
VICTIMS="$WORK/victims"; mkdir -p "$VICTIMS"
for n in a b c d e f; do echo ORIGINAL > "$VICTIMS/$n"; done
mkdir -p "$PH_DIR"
ln -s "$VICTIMS/a" "$SCHEDULER_STATE_DIR/.preview-helper-inflight.json.tmp"
ln -s "$VICTIMS/b" "$PH_DIR/.current-run.json.tmp"
ln -s "$VICTIMS/c" "$PH_DIR/.input.json.tmp"
ln -s "$VICTIMS/d" "$PH_DIR/input.json"
ln -s "$VICTIMS/e" "$PH_DIR/current-run.json"
ln -s "$VICTIMS/f" "$PH_DIR/result.json"
make_pending 131 "$SHA_A"
preview_helper_check >/dev/null
helper_exits; preview_helper_finalize >/dev/null
check "planted_links_are_never_followed [temporary and final names]" \
  bash -c 'for n in a b c d e f; do [ "$(cat "$0/$n")" = ORIGINAL ] || exit 1; done' "$VICTIMS"
reset_case
mkdir -p "$WORK/away"; echo KEEP > "$WORK/away/result.json"; rm -rf "$PH_DIR"; ln -s "$WORK/away" "$PH_DIR"
make_pending 132 "$SHA_A"; preview_helper_check >/dev/null
check "planted_links_are_never_followed [artifact dir: no dispatch, no stamp]" \
  bash -c '[ ! -e "$0" ] && [ "$1" = 0 ]' "$PH_INFLIGHT" "$(get_retry_count "preview_helper:stamp:132:$SHA_A")"
_ph_py write-inflight --file "$PH_INFLIGHT" --run-id "$(printf 'c%.0s' {1..32})" --issue 132 --head-sha "$SHA_A" --started "$(date +%s)" --total 120
preview_helper_finalize >/dev/null
check "planted_links_are_never_followed [finalise removes the link only]" \
  bash -c '[ ! -L "$0" ] && [ "$(cat "$1/result.json")" = KEEP ]' "$PH_DIR" "$WORK/away"
reset_case
mkdir -p "$WORK/elsewhere"; OLDF="$WORK/elsewhere/9-$SHA_A.json"; echo '{}' > "$OLDF"; touch -d '10 days ago' "$OLDF"
rm -rf "$PENDING"; ln -s "$WORK/elsewhere" "$PENDING"
preview_helper_check >/dev/null
check "planted_links_are_never_followed [pending dir: nothing deleted behind it]" test -e "$OLDF"
check "planted_links_are_never_followed [static: no shell write, delete or chmod onto the volume]" \
  bash -c '! sed -e "s/[[:space:]]#.*$//" "$0" | grep -v "^[[:space:]]*#" | sed -e "s#2>/dev/null##g; s#>/dev/null##g; s#2>&1##g" \
      | grep -nE "(^|[^-])>|(^|[;&|[:space:]])(rm|chmod|mkdir|mv|ln|touch|cp)[[:space:]]" | grep -vE "docker rm -f|preview-helper-proxy rm -sf"' "$LANE"

# =====================================================================
echo "--- 14: the run-record write is bounded ---"
reset_case; make_pending 141 "$SHA_A"; preview_helper_check >/dev/null
rm -f "$LEDGER"; mkfifo "$LEDGER"; write_result_ok; helper_exits
t0=$(date +%s); preview_helper_finalize >/dev/null; dt=$(( $(date +%s) - t0 ))
check "a_bad_ledger_ends_ledger_unavailable_at_once (FIFO, ${dt}s)" \
  bash -c '[ "$0" = "abstained ledger_unavailable" ] && [ "$1" -le 5 ]' "$(last_row '.outcome + " " + .failure_class')" "$dt"
reset_case; make_pending 142 "$SHA_A"; preview_helper_check >/dev/null; helper_exits
SLOW="$WORK/slow-scripts"; mkdir -p "$SLOW/factory_core"
cat > "$SLOW/factory_core/preview_helper.py" <<PY
import runpy, sys, time
if sys.argv[1:2] == ["record-outcome"]:
    time.sleep(30)
sys.argv[0] = "$REPO/scripts/factory_core/preview_helper.py"
runpy.run_path(sys.argv[0], run_name="__main__")
PY
SAVED_DIR="$SCHEDULER_SCRIPTS_DIR"; SCHEDULER_SCRIPTS_DIR="$SLOW"; PREVIEW_HELPER_RECORD_SECONDS=4
t0=$(date +%s); out=$(preview_helper_finalize); dt=$(( $(date +%s) - t0 ))
SCHEDULER_SCRIPTS_DIR="$SAVED_DIR"; PREVIEW_HELPER_RECORD_SECONDS=60
check "a_blocking_record_step_ends_record_write_failed (${dt}s)" \
  bash -c '[[ "$0" == *"outcome=abstained failure_class=record_write_failed"* ]] && [ "$1" -le 10 ] && [ "$2" = 1 ] && [ ! -e "$3" ] && [ "$4" = 0 ]' \
  "$out" "$dt" "$(failures)" "$PH_INFLIGHT" "$(rows)"

# =====================================================================
echo "--- errexit: the two bare call sites never end the scheduler ---"
errexit_case() {  # label setup
  reset_case; "$2"
  bash -c 'set -euo pipefail; set -E
    SCHEDULER_SOURCE_ONLY=1 source "$0" >/dev/null 2>&1
    PREVIEW_HELPER_PROXY_SETTLE_SECONDS=1
    DISPATCHED=""
    preview_helper_finalize >/dev/null 2>&1
    preview_helper_check >/dev/null 2>&1
    preview_helper_finalize >/dev/null 2>&1
    echo survived' "$SCHED" | grep -qx survived
}
e_fifo_no_helper() { mkfifo "$PH_INFLIGHT"; }
e_no_registry() { rm -f "$FAKE/show.json"; make_pending 151 "$SHA_A"; lane_off; mkdir -p "$PH_DIR"
  _ph_py write-inflight --file "$PH_INFLIGHT" --run-id "$(printf 'd%.0s' {1..32})" --issue 151 --head-sha "$SHA_A" --started "$(date +%s)" --total 60; }
e_state_garbage() { echo 'not json' > "$STATE_FILE"; make_pending 152 "$SHA_A"; }
e_state_list() { echo '[]' > "$STATE_FILE"; make_pending 154 "$SHA_A"; }
e_docker_down() { make_pending 153 "$SHA_A"; touch "$FAKE/run_fails" "$FAKE/proxy_up_fails"; }
check "errexit: planted FIFO and no helper running" errexit_case fifo e_fifo_no_helper
check "errexit: registry unreadable, lane off, real in-flight entry" errexit_case registry e_no_registry
check "errexit: scheduler-state.json is not JSON" errexit_case state e_state_garbage
check "errexit: proxy and helper both refuse to start" errexit_case docker e_docker_down
check "errexit: scheduler-state.json is a JSON list (the state reader fails)" errexit_case state_list e_state_list
# Review additions: each of these ends the scheduler when its `|| …` guard is removed.
e_fifo_docker_ps_fails() { mkfifo "$PH_INFLIGHT"; touch "$FAKE/ps_fails"; }
e_fifo_state_list() { echo '[]' > "$STATE_FILE"; mkfifo "$PH_INFLIGHT"; }
e_ps_fails_with_pending() { make_pending 155 "$SHA_A"; touch "$FAKE/ps_fails"; }
check "errexit: unreadable in-flight entry while docker ps fails" errexit_case fifo_ps e_fifo_docker_ps_fails
check "errexit: unreadable in-flight entry while the failure counter cannot be written" errexit_case fifo_state e_fifo_state_list
check "errexit: docker ps fails with a pending changeset" errexit_case ps_pending e_ps_fails_with_pending

echo "--- review additions: a cloud entry, and one helper at a time ---"
# A complete CLOUD entry under the selected id: only the scheduler's own hosting_location
# filter stands between it and a dispatch here (the proxy and the helper refuse it too).
reset_case; make_pending 161 "$SHA_A"; export PREVIEW_HELPER_ENDPOINT=cloud-b
cat > "$FAKE/show.json" <<'JSON'
{"endpoints": [{"id": "cloud-b", "hosting_location": "cloud", "base_url": "https://192.0.2.20",
  "model_identity": {"served_name": "cloud-model-b"}, "timeouts": {"total_seconds": 120}}]}
JSON
preview_helper_check >/dev/null
check "a_cloud_entry_is_never_dispatched (endpoint_not_resolved; no proxy, no helper started)" \
  bash -c '[ "$0" = "abstained endpoint_not_resolved" ] && ! grep -qE "preview-helper-proxy up -d|--profile preview-helper run" "$1"' \
  "$(last_row '"\(.outcome) \(.failure_class)"')" "$CALLS"
# An in-flight helper and no proxy container listed: the in-flight entry alone must decline.
reset_case; make_pending 162 "$SHA_A"; mkdir -p "$PH_DIR"
_ph_py write-inflight --file "$PH_INFLIGHT" --run-id "$(printf 'e%.0s' {1..32})" --issue 163 \
  --head-sha "$SHA_B" --started "$(date +%s)" --total 60
echo "${HELPER_NAME}163" > "$FAKE/running"; : > "$CALLS"
preview_helper_check >/dev/null
calls=$(grep -c . "$CALLS" || true)   # counted before the state read below adds its own call
check "one_helper_at_a_time (an in-flight entry declines the stage: no call, no stamp)" \
  bash -c '[ "$0" = 0 ] && [ "$1" = 0 ]' "$calls" "$(get_retry_count "preview_helper:stamp:162:$SHA_A")"

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
```

- [ ] **Step 1.2: Wire it into CI in the same change**

In `.github/workflows/ci.yml`, job `tests`, insert one line directly after
`      - run: bash tests/test_scheduler.sh`:

```yaml
      - run: bash tests/test_preview_helper_scheduler.sh
```

Do **not** add the file to `CI_EXEMPT_SHELL_TESTS` in `tests/test_ci_covers_shell_tests.py`.

- [ ] **Step 1.3: Verify it fails**

```bash
chmod +x tests/test_preview_helper_scheduler.sh
bash tests/test_preview_helper_scheduler.sh; echo "exit=$?"
```

Expected: many `FAIL:` lines, a summary of about `Results: 24 passed, 57 failed`, and
`exit=1`. The lane functions do not exist yet, and the few passes are vacuous checks of
functions that are not defined.

```bash
PYTHONPATH=scripts python -m pytest tests/test_ci_covers_shell_tests.py -q
```

Expected: `passed` (the new file is wired).

No commit yet: Task 2 makes this test pass, and both land in one commit.

---

## Task 2: The sourced lane file and the `scheduler.sh` changes

**Files:** `scripts/preview_helper_dispatch.sh` (new), `scheduler.sh`

- [ ] **Step 2.1: Create `scripts/preview_helper_dispatch.sh` with exactly this content**

This is the spec's listing with corrections C1–C5 applied (see "Named plan corrections"). It
contains no `rm`, `chmod`, `mkdir`, `mv` or shell redirect onto a state-volume path: every write
and delete goes through the module. It contains no `-e` and no `provider_env_flags` call.

```bash
#!/usr/bin/env bash
# Preview-helper shadow lane (#587). Sourced by scheduler.sh.
# Do NOT add set -euo pipefail: this file is sourced and must not alter caller shell options.
#
# One stage (preview_helper_check), one dispatch function (dispatch_preview_helper) that is
# NOT dispatch() and never calls provider_env_flags, and one finalise/reap step
# (preview_helper_finalize) that runs first in every poll, before the WIP capacity guard. Nothing here
# calls the tracker or the code host, and nothing reads or writes an issue's own breaker key.
#
# scheduler.sh runs under `set -euo pipefail` and calls preview_helper_finalize and
# preview_helper_check bare, so no command here may fail unguarded: every pipeline, command
# substitution and counter call ends in `|| ...` (an unguarded failure would end the scheduler).
#
# Uses from scheduler.sh: SCHEDULER_STATE_DIR, STATE_FILE, SCHEDULER_SCRIPTS_DIR,
# FACTORY_PROVIDERS_CLI, FACTORY_CORE_CLI, FACTORY_RUN_PREFIX, get_retry_count/increment_retry/
# reset_retry, DISPATCHED.
# Scheduler-only variables (never in PROVIDER_PASSTHROUGH_VARS): PREVIEW_HELPER_DISPATCH_ENABLED,
# PREVIEW_HELPER_ENDPOINT, FACTORY_HELPER_MODEL_SECRET.

PREVIEW_HELPER_FAILURE_KEY="preview_helper:failures"
PREVIEW_HELPER_FAILURE_STOP=2
PREVIEW_HELPER_PROXY_SETTLE_SECONDS=3
PREVIEW_HELPER_RECORD_SECONDS=60        # bound on the run-record write
PREVIEW_HELPER_LIMIT_CEILING=3600       # in-flight limit accepted when the entry cannot be read
PREVIEW_HELPER_DAILY_MAX_DEFAULT=20     # helper dispatches per UTC day (env PREVIEW_HELPER_DAILY_MAX)
PREVIEW_HELPER_COMPOSE_FILE=/opt/dark-factory/docker-compose.yml

preview_helper_enabled() {
  [ "${PREVIEW_HELPER_DISPATCH_ENABLED:-false}" = "true" ] && [ -n "${PREVIEW_HELPER_ENDPOINT:-}" ]
}

_ph_dir() { echo "${SCHEDULER_STATE_DIR}/preview-helper"; }
_ph_inflight() { echo "${SCHEDULER_STATE_DIR}/preview-helper-inflight.json"; }
_ph_log() { echo "[$(date -u +%FT%TZ)] preview_helper=$*"; }
_ph_py() {
  SCHEDULER_STATE_DIR="$SCHEDULER_STATE_DIR" \
    python3 "${SCHEDULER_SCRIPTS_DIR}/factory_core/preview_helper.py" "$@"
}
_ph_compose() { docker compose -f "$PREVIEW_HELPER_COMPOSE_FILE" "$@"; }
_ph_inflight_present() { [ -e "$(_ph_inflight)" ] || [ -L "$(_ph_inflight)" ]; }
# Remove the three artifact names and the in-flight entry, whatever is planted there, through
# the module (directory descriptors; a link in place of the directory is unlinked, not followed).
_ph_clear() { _ph_py clear --dir "$(_ph_dir)" --inflight "$(_ph_inflight)" 2>/dev/null || true; }
_ph_running() { docker ps --format '{{.Names}}' 2>/dev/null | grep -qxF -- "$1"; }
# A lane counter read through the scheduler's own state reader: its value, or -1 when the
# reader fails or prints anything but a number (the callers then decline: fail closed).
_ph_count() {
  local n
  n=$(get_retry_count "$1" 2>/dev/null) || n=""
  if [[ "$n" =~ ^[0-9]+$ ]]; then echo "$n"; else echo "-1"; fi
}

# Every preview-helper-proxy container of THIS compose project, in any state (#634 9.1).
# `docker ps` is host-wide and two instances can share a host: both labels are required.
preview_helper_proxy_ids() {
  docker ps -aq \
    --filter "label=com.docker.compose.project=${FACTORY_INSTANCE:-dark-factory}" \
    --filter "label=com.docker.compose.service=preview-helper-proxy" 2>/dev/null
}

preview_helper_proxy_down() {
  _ph_compose --profile preview-helper-proxy rm -sf preview-helper-proxy >/dev/null 2>&1 || true
  local ids
  ids=$(preview_helper_proxy_ids) || ids=""
  # shellcheck disable=SC2086
  [ -z "$ids" ] || docker rm -f $ids >/dev/null 2>&1 || true
}

# Starts the proxy with `up -d`, so the service name resolves on preview-helper-net. The caller
# has exported the five variables #634 interpolates. A proxy that refuses its configuration
# exits before it binds (its registry checks run first), so "ready" is: the container exists
# and is still running after the settle time. Its log is not used: the banner is block-buffered.
preview_helper_proxy_up() {
  _ph_compose --profile preview-helper-proxy up -d preview-helper-proxy >/dev/null 2>&1 || return 1
  local id i
  for (( i = 0; i < PREVIEW_HELPER_PROXY_SETTLE_SECONDS; i++ )); do
    sleep 1
    id=$(preview_helper_proxy_ids | head -1) || id=""
    [ -n "$id" ] || return 1
    [ "$(docker inspect -f '{{.State.Running}}' "$id" 2>/dev/null)" = "true" ] || return 1
  done
  return 0
}

_ph_write_inflight() {  # run_id issue sha started total
  _ph_py write-inflight --file "$(_ph_inflight)" --run-id "$1" --issue "$2" --head-sha "$3" \
    --started "$4" --total "$5" 2>/dev/null
}

# The selected local entry on the scheduler's read-only registry mount, as one JSON object,
# or nothing: exactly one entry with the id and hosting_location local.
_ph_entry() {
  python3 "$FACTORY_PROVIDERS_CLI" model-endpoints show --from-source 2>/dev/null \
    | jq -c --arg id "${PREVIEW_HELPER_ENDPOINT:-}" \
        '[.endpoints[] | select(.id == $id and .hosting_location == "local")]
         | if length == 1 then .[0] else empty end' 2>/dev/null
}

# Runs first in every poll (before the rate-limit wait, the board fetch and the WIP capacity
# guard), whether or not the lane is still enabled.
# No in-flight file: returns at once. Helper still running and inside its limit: returns.
# Otherwise (exited, over the limit, lane switched off, or a forced class from the
# dispatcher): stop and remove the helper, remove the proxy, write the run record, bump or
# reset the failure counter, clear the directory. `--rm` leaves nothing behind, so "exited"
# is "no running container with this name".
preview_helper_finalize() {
  local forced="${1:-}" inflight dir
  inflight=$(_ph_inflight)
  _ph_inflight_present || return 0
  dir=$(_ph_dir)
  local run_id issue sha started total name out fields limit
  # The limit a stored value may claim: the selected entry's own, else a fixed ceiling.
  limit=$(_ph_entry | jq -r '.timeouts.total_seconds // empty | floor' 2>/dev/null) || limit=""
  [[ "$limit" =~ ^[1-9][0-9]*$ ]] || limit="$PREVIEW_HELPER_LIMIT_CEILING"
  if ! fields=$(_ph_py read-inflight --file "$inflight" --now "$(date +%s)" --max-total "$limit" 2>/dev/null); then
    # Unreadable state (not a small regular file, a field out of shape, a start in the
    # future, a limit above the entry's): nothing can be attributed. Stop any helper of this
    # instance, remove the proxy, clear everything, count one failure. No run record: there
    # is no trustworthy run id.
    _ph_log "inflight_unreadable action=cleanup"
    { docker ps --format '{{.Names}}' 2>/dev/null | grep -E -- "^${FACTORY_RUN_PREFIX}ph-[0-9]+$" \
      | while IFS= read -r name; do docker rm -f "$name" >/dev/null 2>&1 || true; done; } || true
    preview_helper_proxy_down
    _ph_clear
    increment_retry "$PREVIEW_HELPER_FAILURE_KEY" >/dev/null 2>&1 || true
    return 0
  fi
  read -r run_id issue sha started total <<<"$fields"
  name="${FACTORY_RUN_PREFIX}ph-${issue}"
  # Lane off: whatever is there ends kill_switch. No result file or ledger row is taken as
  # `ok` while the lane is off, so an entry forged then cannot reset the failure counter.
  if [ -z "$forced" ] && ! preview_helper_enabled; then
    forced=kill_switch
  fi
  if [ -z "$forced" ] && _ph_running "$name"; then
    if [ $(( $(date +%s) - started )) -gt "$total" ]; then
      forced=wall_clock_exceeded
    else
      return 0
    fi
  fi
  if _ph_running "$name"; then
    docker stop -t 5 "$name" >/dev/null 2>&1 || true
    docker rm -f "$name" >/dev/null 2>&1 || true
  fi
  preview_helper_proxy_down
  out=$(SCHEDULER_STATE_DIR="$SCHEDULER_STATE_DIR" timeout -k 5 "$PREVIEW_HELPER_RECORD_SECONDS" \
          python3 "${SCHEDULER_SCRIPTS_DIR}/factory_core/preview_helper.py" record-outcome --run-id "$run_id" --issue "$issue" --head-sha "$sha" \
          --result-file "$dir/result.json" \
          --ledger-path "${SCHEDULER_STATE_DIR}/request-ledger.jsonl" \
          ${forced:+--forced-class "$forced"} 2>/dev/null) || out="abstained record_write_failed"   # also a timeout
  case "$out" in
    "ok "*) reset_retry "$PREVIEW_HELPER_FAILURE_KEY" >/dev/null 2>&1 || true ;;
    *)      increment_retry "$PREVIEW_HELPER_FAILURE_KEY" >/dev/null 2>&1 || true ;;
  esac
  _ph_log "finished issue=#${issue} head_sha=${sha} run_id=${run_id} outcome=${out%% *} failure_class=${out##* }"
  _ph_clear
  return 0
}

# The separate dispatch function. $1 issue (digits), $2 head SHA; input.json is already in
# the helper's directory (preview_helper_check wrote the validated copy). Returns 0 only when
# the helper container was started. Every failure goes through preview_helper_finalize with
# its class, so it ends `abstained` with a run record and nothing is left running.
dispatch_preview_helper() {
  local issue="$1" sha="$2" dir run_id entry total now
  dir=$(_ph_dir)
  run_id=$(python3 -c 'import uuid; print(uuid.uuid4().hex)')
  now=$(date +%s)
  # Limit 1 until the entry is resolved: read-inflight refuses 0, and nothing is started
  # before the second write below replaces it with the entry's own limit.
  _ph_write_inflight "$run_id" "$issue" "$sha" "$now" 1 \
    || { _ph_log "skip reason=inflight_write_failed"; return 1; }

  # The pointer the proxy correlates on: written by the scheduler, never by the helper.
  _ph_py write-pointer --dir "$dir" --run-id "$run_id" --issue "$issue" 2>/dev/null \
    || { preview_helper_finalize dispatch_failed; return 1; }

  # Exported to the two compose invocations below and to nothing else (function-local).
  local -x FACTORY_MODEL_REGISTRY_SHA256 PREVIEW_HELPER_ENDPOINT_BASE_URL PREVIEW_HELPER_ENDPOINT_SERVED_NAME
  FACTORY_MODEL_REGISTRY_SHA256=$(python3 "$FACTORY_PROVIDERS_CLI" model-endpoints stage 2>/dev/null) \
    && [ -n "$FACTORY_MODEL_REGISTRY_SHA256" ] \
    || { preview_helper_finalize registry_stage_failed; return 1; }

  # From the scheduler's read-only registry mount: exactly one local entry with this id.
  entry=$(_ph_entry) || entry=""
  PREVIEW_HELPER_ENDPOINT_BASE_URL=$(jq -r '.base_url // empty' <<<"$entry" 2>/dev/null)
  PREVIEW_HELPER_ENDPOINT_SERVED_NAME=$(jq -r '.model_identity.served_name // empty' <<<"$entry" 2>/dev/null)
  total=$(jq -r '.timeouts.total_seconds // empty | floor' <<<"$entry" 2>/dev/null)
  if [ -z "$PREVIEW_HELPER_ENDPOINT_BASE_URL" ] || [ -z "$PREVIEW_HELPER_ENDPOINT_SERVED_NAME" ] \
      || ! [[ "$total" =~ ^[1-9][0-9]*$ ]]; then
    preview_helper_finalize endpoint_not_resolved
    return 1
  fi
  _ph_write_inflight "$run_id" "$issue" "$sha" "$now" "$total" \
    || { preview_helper_finalize dispatch_failed; return 1; }

  if ! preview_helper_proxy_up; then
    preview_helper_finalize proxy_start_failed
    return 1
  fi
  if ! _ph_compose --profile preview-helper run --name "${FACTORY_RUN_PREFIX}ph-${issue}" \
        -d --rm preview-helper "Classify preview (helper) #${issue}" >/dev/null 2>&1; then
    preview_helper_finalize dispatch_failed
    return 1
  fi
  # The limit runs from the container's start: the scheduler's enforcer and the one inside
  # the container count the same seconds.
  _ph_write_inflight "$run_id" "$issue" "$sha" "$(date +%s)" "$total" || true
  _ph_log "dispatched issue=#${issue} head_sha=${sha} run_id=${run_id} limit=${total}s"
  return 0
}

# The stage. Called once per poll after every existing stage, behind the same call-site
# guard deps_audit_check has (no session-window pause, nothing dispatched this poll); the
# capacity guard above it has already ended the poll when no slot is free.
preview_helper_check() {
  preview_helper_enabled || return 0
  ! _ph_inflight_present || return 0
  local pending="${SCHEDULER_STATE_DIR}/preview-pending" dir cand issue sha
  dir=$(_ph_dir)
  local day_key max key count
  day_key="preview_helper:day:$(date -u +%F)"
  max="${PREVIEW_HELPER_DAILY_MAX:-$PREVIEW_HELPER_DAILY_MAX_DEFAULT}"
  [[ "$max" =~ ^[0-9]+$ ]] || max="$PREVIEW_HELPER_DAILY_MAX_DEFAULT"
  _ph_py sweep --pending-dir "$pending" --state-file "$STATE_FILE" >/dev/null 2>&1 || true
  # Stamps and day counts older than the retention leave scheduler-state.json.
  # Each line is checked again here: nothing but this lane's own two key shapes is ever reset.
  { _ph_py expired-keys --state-file "$STATE_FILE" 2>/dev/null \
    | while IFS= read -r key; do
        [[ "$key" =~ ^preview_helper:(stamp:[1-9][0-9]*:[0-9a-f]{40}|day:[0-9]{4}-[0-9]{2}-[0-9]{2})$ ]] || continue
        reset_retry "$key" >/dev/null 2>&1 || true
      done; } || true
  count=$(_ph_count "$day_key")
  if [ "$count" -lt 0 ]; then
    _ph_log "skip reason=state_unreadable"
    return 0
  fi
  if [ "$count" -ge "$max" ]; then
    _ph_log "skip reason=daily_max count=${max}"
    return 0
  fi
  count=$(_ph_count "$PREVIEW_HELPER_FAILURE_KEY")
  if [ "$count" -lt 0 ]; then
    _ph_log "skip reason=state_unreadable"
    return 0
  fi
  if [ "$count" -ge "$PREVIEW_HELPER_FAILURE_STOP" ]; then
    _ph_log "skip reason=failure_stop key=${PREVIEW_HELPER_FAILURE_KEY}"
    return 0
  fi
  if [ -n "$(preview_helper_proxy_ids || true)" ]; then
    _ph_log "skip reason=stale_proxy"   # retried next poll; not a failure
    return 0
  fi
  # The helper's one writable directory must exist before its container is created (a
  # missing subpath fails the start) and be writable by the image's user.
  # prepare-dir also empties the directory of the three artifact names, whatever they are.
  if ! _ph_py prepare-dir --dir "$dir" 2>/dev/null; then
    _ph_log "skip reason=artifact_dir_not_a_directory"
    return 0
  fi
  local rc=0
  cand=$(_ph_py next-candidate --pending-dir "$pending" --state-file "$STATE_FILE" \
           --out "$dir/input.json") || rc=$?
  if [ "$rc" -ne 0 ]; then
    [ "$rc" -eq 1 ] || _ph_log "skip reason=input_write_failed"   # 1 = no candidate
    return 0
  fi
  issue="${cand%% *}"; sha="${cand##* }"
  [[ "$issue" =~ ^[1-9][0-9]*$ && "$sha" =~ ^[0-9a-f]{40}$ ]] || return 0
  # Asked again through the scheduler's own state reader: the module reads at most 8 MiB of
  # the state file and treats a larger one as empty, which would offer a stamped SHA again.
  # Anything but a plain 0 (a value, or an unreadable state) counts as stamped.
  if [ "$(_ph_count "preview_helper:stamp:${issue}:${sha}")" != "0" ]; then
    _ph_log "skip reason=already_stamped issue=#${issue}"
    _ph_clear
    return 0
  fi
  # Stamped before the dispatch: any value means this head SHA is never dispatched again.
  # The value is the epoch, so sweep can age it out with the pending file's own retention.
  STATE_FILE="$STATE_FILE" python3 "$FACTORY_CORE_CLI" breaker-set-retry \
    --key "preview_helper:stamp:${issue}:${sha}" --value "$(date +%s)" >/dev/null 2>&1 || true
  # breaker-set-retry exits 0 even when it could not write the state file: read the stamp
  # back, and dispatch only when it is there.
  if [ "$(_ph_count "preview_helper:stamp:${issue}:${sha}")" -le 0 ]; then
    _ph_log "skip reason=stamp_write_failed issue=#${issue}"
    _ph_clear
    return 0
  fi
  increment_retry "$day_key" >/dev/null 2>&1 || true
  if dispatch_preview_helper "$issue" "$sha"; then
    DISPATCHED="Classify preview (helper) #${issue}"
  fi
  return 0
}
```

- [ ] **Step 2.2: Source it from `scheduler.sh`**

In `scheduler.sh`, directly after the line
`source "$(dirname "${BASH_SOURCE[0]:-$0}")/scripts/provider_env.sh"` (near the top, before
`SCHEDULER_SCRIPTS_DIR=`), insert:

```bash
source "$(dirname "${BASH_SOURCE[0]:-$0}")/scripts/preview_helper_dispatch.sh"
```

Then check syntax:

```bash
bash -n scripts/preview_helper_dispatch.sh && bash -n scheduler.sh && echo SYNTAX_OK
```

Expected: `SYNTAX_OK`.

- [ ] **Step 2.3: Verify only the call-site and flag checks still fail**

```bash
bash tests/test_preview_helper_scheduler.sh 2>&1 | grep -E 'FAIL|Results'
```

Expected, exactly these four failures:

```
  FAIL:   (precondition) the baseline is dispatch() minus exactly the six-line #587 block
  FAIL: lane_on_adds_only_the_changeset_flag_to_fix_and_continue
  FAIL:   ... the call site in scheduler.sh is exactly this guard, after stage_epic_autopilot
  FAIL: finalise_is_the_first_call_in_the_poll
Results: 77 passed, 4 failed
```

- [ ] **Step 2.4: Add the changeset flag in `dispatch()` and the two main-loop call sites**

Apply this patch to `scheduler.sh` (save it to `/tmp/587-scheduler.patch`, then run
`git apply /tmp/587-scheduler.patch`). The anchors, in case line numbers have moved: the
`case` goes directly after `mapfile -t env_flags < <(provider_env_flags)` in `dispatch()`;
`preview_helper_finalize` goes directly after `DISPATCHED=""` at the top of `while true; do`;
the stage call goes directly after `stage_epic_autopilot` in the loop, before
`# --- Log cycle summary ---`. `dispatch()` gains nothing else.

````diff
--- a/scheduler.sh
+++ b/scheduler.sh
@@ -483,6 +483,12 @@
   # Forward provider selection/env set in the scheduler's env (#489); see scripts/provider_env.sh.
   local -a env_flags=()
   mapfile -t env_flags < <(provider_env_flags)
+  # #587: the one changeset flag, on an implement dispatch only and only while the preview-helper
+  # lane is enabled (switch on AND endpoint set). Otherwise this adds nothing.
+  case "$command" in
+    "Fix issue #"*|"Continue issue #"*)
+      if preview_helper_enabled; then env_flags+=(-e PREVIEW_HELPER_CHANGESET_FLAG=true); fi ;;
+  esac
   local -x FACTORY_MODEL_REGISTRY_SHA256  # #557: set only by model_registry_env_flags, for this run
   model_registry_env_flags env_flags \
     || {
@@ -1670,6 +1676,11 @@
 
 while true; do
   DISPATCHED=""
+
+  # --- Preview-helper finalise/reap (#587): first in the poll. A helper counts toward WIP, so
+  # this must run before the capacity guard; it needs no board data, so it also runs before
+  # the rate-limit wait and the board fetch. Dispatches nothing, calls no tracker. ---
+  preview_helper_finalize
   BOT_RE=$(python3 "$FACTORY_CORE_CLI" markers-regex)
 
   # Guard against rate limit exhaustion (REST call, doesn't cost GraphQL points)
@@ -1767,6 +1778,12 @@
   # --- Priority 6: Epic Autopilot (see stage_epic_autopilot) ---
   stage_epic_autopilot
 
+  # --- Lowest priority: the preview-helper shadow dispatch (#587). Same slot/pause
+  # discipline as deps_audit_check, and never a second dispatch this cycle. ---
+  if [ "$SESSION_WINDOW_PAUSED" = "false" ] && [ -z "$DISPATCHED" ]; then
+    preview_helper_check
+  fi
+
   # --- Log cycle summary ---
   BUDGET=$(python3 "$FACTORY_PROVIDERS_CLI" tracker get-rate-budget 2>/dev/null \
     | jq -r '"\(.used)/\(.limit)"' 2>/dev/null) || BUDGET="?"
````

- [ ] **Step 2.5: Verify all scheduler-lane tests pass**

```bash
bash tests/test_preview_helper_scheduler.sh 2>&1 | grep -E 'FAIL|Results'
```

Expected: `Results: 81 passed, 0 failed` and no `FAIL:` line. It takes about 2 minutes,
mostly Python start-up and one 1-second proxy settle per dispatch.

- [ ] **Step 2.6: Verify nothing existing broke**

```bash
bash tests/test_scheduler.sh 2>&1 | tail -3
bash tests/test_scheduler_lib.sh 2>&1 | tail -2
PYTHONPATH=scripts python -m pytest tests/test_provider_env_passthrough.py tests/test_ci_covers_shell_tests.py tests/test_scheduler_deps_audit.py tests/test_scheduler_comment_verdict.py -q
```

Expected: both shell suites end with 0 failures (as on `main`), and pytest reports `passed`
with no failures. `PROVIDER_PASSTHROUGH_VARS` is untouched, so `test_provider_env_passthrough.py`
is unaffected.

- [ ] **Step 2.7: Commit**

```bash
git add scripts/preview_helper_dispatch.sh scheduler.sh tests/test_preview_helper_scheduler.sh .github/workflows/ci.yml
git commit -m "feat(#587): the preview-helper shadow lane in the scheduler — one stage, a separate dispatch function, a finalise step first in the poll"
```

---

## Task 3: The changeset block in `push-and-pr`

**Files:** `tests/test_preview_changeset_write.sh` (new), `tests/test_preview_helper_lane.py`
(new), `.github/workflows/ci.yml`, `workflows/archon-dark-factory.yaml`

- [ ] **Step 3.1: Create `tests/test_preview_changeset_write.sh` with exactly this content**

It YAML-decodes `push-and-pr`, cuts the text between the two markers, redirects the fixed
`/var/lib/dark-factory/preview-pending` path to a temp dir, and runs the block under
`set -euo pipefail` in a scratch git repository. It feeds the written file to the real
`next-candidate` intake.

```bash
#!/usr/bin/env bash
# The push-and-pr changeset block (#587): the text between its two markers in
# workflows/archon-dark-factory.yaml, run for real in a scratch git repository with its
# pending directory pointed at a temp dir. No push, no network.
# Run: bash tests/test_preview_changeset_write.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
WF="$REPO/workflows/archon-dark-factory.yaml"
WORK=$(mktemp -d /tmp/ph-changeset-XXXXXX)
trap 'chmod -R u+w "$WORK" 2>/dev/null; rm -rf "$WORK"' EXIT

PASSED=0; FAILED=0
check() { local desc="$1"; shift; if "$@"; then echo "  PASS: $desc"; PASSED=$((PASSED+1)); else echo "  FAIL: $desc" >&2; FAILED=$((FAILED+1)); fi; }

# The node's bash, YAML-decoded, then the marked block with its fixed path redirected.
NODE="$WORK/node.sh"
python3 - "$WF" > "$NODE" <<'PY'
import sys, yaml
nodes = {n["id"]: n for n in yaml.safe_load(open(sys.argv[1]))["nodes"]}
sys.stdout.write(nodes["push-and-pr"]["bash"])
PY
BLOCK="$WORK/block.sh"
sed -n '/^# >>> preview-helper changeset (#587)/,/^# <<< preview-helper changeset (#587)/p' "$NODE" \
  | sed "s#PH_DIR=/var/lib/dark-factory/preview-pending#PH_DIR=\"\$PH_TEST_PENDING\"#" > "$BLOCK"
check "the block is present between its markers and names the fixed pending path" \
  bash -c 'grep -q "^# <<< preview-helper changeset (#587)" "$0" && grep -qF "PH_DIR=/var/lib/dark-factory/preview-pending" "$1"' "$BLOCK" "$NODE"

# A scratch repository: main plus one feature commit with an awkward file name.
GIT="$WORK/repo"; mkdir -p "$GIT"
git -C "$GIT" init -q -b main
git -C "$GIT" -c user.email=t@t -c user.name=t commit -q --allow-empty -m base
git -C "$GIT" checkout -q -b feat/x
mkdir -p "$GIT/docs"; echo a > "$GIT/docs/b.md"; echo b > "$GIT/we ird \$(x).md"
git -C "$GIT" add -A
git -C "$GIT" -c user.email=t@t -c user.name=t commit -q -m feat
HEAD_SHA=$(git -C "$GIT" rev-parse HEAD)
ART="$WORK/artifacts"; mkdir -p "$ART"
jq -n '{resolved_number: 42, title: "A title with \"quotes\" and $(x)",
        labels: [{name: "enhancement"}, {name: "size: L"}]}' > "$ART/issue.json"

run_block() {  # flag issue pending-dir -> exit code of the whole node fragment
  ( cd "$GIT" && set -euo pipefail
    ISSUE="$2" ARTIFACTS_DIR="$ART" PH_TEST_PENDING="$3" PREVIEW_HELPER_CHANGESET_FLAG="$1"
    export ISSUE ARTIFACTS_DIR PH_TEST_PENDING PREVIEW_HELPER_CHANGESET_FLAG
    source "$BLOCK"
    echo node-continues )
}

echo "--- 15: the changeset file ---"
P1="$WORK/p1"
out=$(run_block "" 42 "$P1"); rc=$?
check "no_file_without_the_flag (guard)" bash -c '[ "$0" = 0 ] && [ "$1" = node-continues ] && [ ! -e "$2" ]' "$rc" "$out" "$P1"
out=$(run_block false 42 "$P1"); rc=$?
check "  ... nor with the flag set to anything but true" test ! -e "$P1"

P2="$WORK/p2"
out=$(run_block true 42 "$P2"); rc=$?
F="$P2/42-$HEAD_SHA.json"
check "one_valid_file_with_the_flag (one file, final name only)" \
  bash -c '[ "$0" = 0 ] && [ "$(ls -A "$1")" = "$(basename "$2")" ]' "$rc" "$P2" "$F"
check "  ... issue_number is a JSON integer and the six fields are exact" \
  test "$(jq -c '[(.issue_number | type), (keys)]' "$F")" = '["number",["changed_files","diff_stat","head_sha","issue_number","labels","title"]]'
check "  ... file names, title and labels round-trip" \
  test "$(jq -c '[.changed_files, .title, .labels]' "$F")" = '[["docs/b.md","we ird $(x).md"],"A title with \"quotes\" and $(x)",["enhancement","size: L"]]'
STATE="$WORK/state.json"; echo '{}' > "$STATE"; OUTJ="$WORK/input.json"
cand=$(python3 "$REPO/scripts/factory_core/preview_helper.py" next-candidate --pending-dir "$P2" \
  --state-file "$STATE" --out "$OUTJ"); rc=$?
check "  ... accepted by the intake (next-candidate)" test "$rc:$cand" = "0:42 $HEAD_SHA"

P3="$WORK/p3"; mkdir -p "$P3"; chmod 0555 "$P3"
out=$(run_block true 42 "$P3"); rc=$?
ok3=$([ "$rc" = 0 ] && [ "$out" = node-continues ] && echo 0 || echo 1)
P4="$WORK/p4"
out=$(run_block true "abc" "$P4"); rc=$?
ok4=$([ "$rc" = 0 ] && [ "$out" = node-continues ] && [ -z "$(ls -A "$P4" 2>/dev/null)" ] && echo 0 || echo 1)
mv "$ART/issue.json" "$ART/issue.json.away"
P5="$WORK/p5"
out=$(run_block true 42 "$P5"); rc=$?
ok5=$([ "$rc" = 0 ] && [ "$out" = node-continues ] && [ -z "$(ls -A "$P5" 2>/dev/null)" ] && echo 0 || echo 1)
mv "$ART/issue.json.away" "$ART/issue.json"
check "the_block_never_fails_the_node (unwritable dir, non-numeric ISSUE, missing issue.json; set -euo pipefail)" \
  test "$ok3$ok4$ok5" = "000"
if [ "$(id -u)" = 0 ]; then echo "  (as root the unwritable-dir case writes; the node still continues)"; fi

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
```

- [ ] **Step 3.2: Wire it into CI**

In `.github/workflows/ci.yml`, directly after the line added in Step 1.2, insert:

```yaml
      - run: bash tests/test_preview_changeset_write.sh
```

- [ ] **Step 3.3: Create `tests/test_preview_helper_lane.py` with exactly this content**

```python
"""Regression guards for the preview-helper shadow lane (#587) in the workflow DAG.

preview-up must keep reading only the implement run's in-process classify-preview node, so
neither node (nor any phase command) may mention the lane, and the only workflow text the lane
adds is the marked changeset block in push-and-pr (spec deviation 4: a text-reference check,
not a byte comparison of the rendered nodes)."""
import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _REPO_ROOT / "workflows" / "archon-dark-factory.yaml"
_LANE = re.compile(r"preview[-_]helper|preview-pending|PREVIEW_HELPER", re.IGNORECASE)
_BEGIN = "# >>> preview-helper changeset (#587)"
_END = "# <<< preview-helper changeset (#587)"


def _nodes():
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return {n["id"]: n for n in data.get("nodes", []) if isinstance(n, dict) and "id" in n}


def _text(node):
    return "\n".join(str(node.get(k, "")) for k in ("bash", "prompt", "command", "when"))


def test_preview_up_and_classify_preview_do_not_reference_the_lane():
    nodes = _nodes()
    for node_id in ("preview-up", "classify-preview", "preview-changeset"):
        assert not _LANE.search(_text(nodes[node_id])), f"{node_id} mentions the lane"
    for command in sorted((_REPO_ROOT / "commands").glob("*.md")):
        assert not _LANE.search(command.read_text(encoding="utf-8")), f"{command.name} mentions the lane"


def test_the_only_workflow_text_added_is_between_the_markers():
    nodes = _nodes()
    for node_id, node in nodes.items():
        text = _text(node)
        if node_id != "push-and-pr":
            assert _BEGIN not in text and not _LANE.search(text), f"{node_id} mentions the lane"
            continue
        assert text.count(_BEGIN) == 1 and text.count(_END) == 1
        before, rest = text.split(_BEGIN, 1)
        block, after = rest.split(_END, 1)
        assert not _LANE.search(before) and not _LANE.search(after), "lane text outside the markers"
        assert 'if [ "${PREVIEW_HELPER_CHANGESET_FLAG:-}" = "true" ]; then' in block
        # After the confirmed push, before the PR is looked up or opened.
        assert "git ls-remote --exit-code --heads origin" in before
        assert "codehost find-change" in after and "codehost open-change" in after
```

- [ ] **Step 3.4: Verify they fail (the guards pass)**

```bash
chmod +x tests/test_preview_changeset_write.sh
bash tests/test_preview_changeset_write.sh 2>&1 | grep -E 'PASS|FAIL|Results'
PYTHONPATH=scripts python -m pytest tests/test_preview_helper_lane.py -q 2>&1 | tail -2
```

Expected:

```
  FAIL: the block is present between its markers and names the fixed pending path
  PASS: no_file_without_the_flag (guard)
  PASS:   ... nor with the flag set to anything but true
  FAIL: one_valid_file_with_the_flag (one file, final name only)
  FAIL:   ... issue_number is a JSON integer and the six fields are exact
  FAIL:   ... file names, title and labels round-trip
  FAIL:   ... accepted by the intake (next-candidate)
  PASS: the_block_never_fails_the_node (unwritable dir, non-numeric ISSUE, missing issue.json; set -euo pipefail)
Results: 3 passed, 5 failed
FAILED tests/test_preview_helper_lane.py::test_the_only_workflow_text_added_is_between_the_markers
1 failed, 1 passed
```

`no_file_without_the_flag` and `test_preview_up_and_classify_preview_do_not_reference_the_lane`
are the regression guards; they pass before the change.

- [ ] **Step 3.5: Add the marked block to `push-and-pr`**

Apply this patch (save it to `/tmp/587-workflow.patch`, then run
`git apply /tmp/587-workflow.patch`). Anchor: directly after the `git ls-remote --exit-code
--heads origin "$BRANCH"` confirmation `if … fi` in `push-and-pr`, before
`# --- Blast radius report (codeindex advisory) ---`. No other node changes. Keep the
indentation: six spaces inside the YAML block scalar.

````diff
diff --git a/workflows/archon-dark-factory.yaml b/workflows/archon-dark-factory.yaml
index 098dec2..6b6d7de 100644
--- a/workflows/archon-dark-factory.yaml
+++ b/workflows/archon-dark-factory.yaml
@@ -1407,6 +1407,36 @@ nodes:
         exit 1
       fi
 
+      # >>> preview-helper changeset (#587) — shadow only. Written only when the scheduler
+      # passed the flag on this dispatch; in a subshell that can never fail this node.
+      if [ "${PREVIEW_HELPER_CHANGESET_FLAG:-}" = "true" ]; then
+        (
+          set +e
+          PH_DIR=/var/lib/dark-factory/preview-pending
+          PH_SHA=$(git rev-parse HEAD 2>/dev/null)
+          printf '%s' "$PH_SHA" | grep -qE '^[0-9a-f]{40}$' || exit 0
+          printf '%s' "$ISSUE" | grep -qE '^[1-9][0-9]*$' || exit 0
+          mkdir -p "$PH_DIR" 2>/dev/null || exit 0
+          PH_TMP=$(mktemp "$PH_DIR/.tmp.XXXXXX" 2>/dev/null) || exit 0
+          git diff main...HEAD --name-only > "$PH_TMP.files" 2>/dev/null
+          git diff main...HEAD --stat > "$PH_TMP.stat" 2>/dev/null
+          if jq -n --argjson issue "$ISSUE" --arg sha "$PH_SHA" \
+               --rawfile files "$PH_TMP.files" --rawfile stat "$PH_TMP.stat" \
+               --slurpfile ij "$ARTIFACTS_DIR/issue.json" \
+               '{issue_number: $issue, head_sha: $sha,
+                 changed_files: ($files | split("\n") | map(select(length > 0))),
+                 diff_stat: $stat,
+                 title: (($ij[0].title // "") | tostring),
+                 labels: [($ij[0].labels // [])[]
+                          | if type == "object" then ((.name // "") | tostring) else tostring end]}' \
+               > "$PH_TMP" 2>/dev/null; then
+            mv -f "$PH_TMP" "$PH_DIR/${ISSUE}-${PH_SHA}.json" 2>/dev/null
+          fi
+          rm -f "$PH_TMP" "$PH_TMP.files" "$PH_TMP.stat" 2>/dev/null
+        ) || true
+      fi
+      # <<< preview-helper changeset (#587)
+
       # --- Blast radius report (codeindex advisory) ---
       BLAST_SECTION=""
       if command -v codeindex &>/dev/null; then
````

- [ ] **Step 3.6: Verify they pass and the DAG checks still pass**

```bash
bash tests/test_preview_changeset_write.sh 2>&1 | tail -1
PYTHONPATH=scripts python -m pytest tests/test_preview_helper_lane.py tests/test_blast_gate_dag.py tests/test_bench_suite.py tests/test_ci_covers_shell_tests.py -q 2>&1 | tail -1
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```

Expected: `Results: 8 passed, 0 failed`; pytest reports only `passed`; then
`DAG trigger_rule check passed for 1 workflow file(s).` and
`when: expression lint passed for 1 workflow file(s).`

- [ ] **Step 3.7: Commit**

```bash
git add tests/test_preview_changeset_write.sh tests/test_preview_helper_lane.py .github/workflows/ci.yml workflows/archon-dark-factory.yaml
git commit -m "feat(#587): push-and-pr writes the pushed changeset for the helper lane, only when the scheduler flags the dispatch"
```

---

## Task 4: The documentation-only config key and the docs

**Files:** `config/config.yaml`, `docs/configuration.md`, `docs/run-lifecycle.md`,
`docs/scheduler.md`

- [ ] **Step 4.1: Add the config block**

Apply (to `/tmp/587-config.patch`, then `git apply`). Anchor: directly after the
`deps_audit:` block's last line (`auto_merge: false …`), before `side_effect:`.

````diff
diff --git a/config/config.yaml b/config/config.yaml
index 3114b3b..bcecc10 100644
--- a/config/config.yaml
+++ b/config/config.yaml
@@ -141,6 +141,16 @@ deps_audit:
   ci_wait_minutes: 20         # bounded wait for the deps PR's CI (container default; env DEPS_AUDIT_CI_WAIT_MINUTES)
   auto_merge: false           # merge the deps PR on green only when true (container default; env DEPS_AUDIT_AUTO_MERGE); direct-to-pr on the tracking issue also allows it
 
+preview_helper_dispatch:
+  # Preview-helper shadow lane (#587): the scheduler starts #642's token-free, tool-free
+  # preview-helper container on a local endpoint to re-classify a pushed changeset. Shadow
+  # only: nothing reads its result. Ships OFF, and this key is documentation only (no code
+  # reads it). The scheduler dispatches only when PREVIEW_HELPER_DISPATCH_ENABLED=true AND
+  # PREVIEW_HELPER_ENDPOINT is set in ITS OWN environment (deploy/instance.env, human-only);
+  # PREVIEW_HELPER_DAILY_MAX (default 20) caps dispatches per UTC day. Activation is #564's
+  # canary only: docs/configuration.md, "Model endpoint registry".
+  enabled: false
+
 side_effect:
   # Side-effect level each factory phase runs at (#196). 1..5; 6 is never valid here
   # (adapter.py/handoff.py reject 6 outright — see side_effect.py). All phases start at
````

- [ ] **Step 4.2: Verify the docs checks now fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_docs_conformance_reference_docs.py -q 2>&1 | grep -E '^FAILED|passed|failed'
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md 2>&1 | tail -1
```

Expected:

```
FAILED tests/test_docs_conformance_reference_docs.py::test_configuration_states_the_current_leaf_key_count
1 failed, 3 passed
  docs/configuration.md: config key `preview_helper_dispatch.enabled` in config/config.yaml is not documented in the Layer 1 tables
```

- [ ] **Step 4.3: Update `docs/configuration.md`**

Apply (to `/tmp/587-configuration.patch`, then `git apply`). It changes the leaf count from 110
to 111 (the Layer 1 intro and the "Counting" line, which also gains `preview_helper_dispatch` 1).
It adds a `### preview_helper_dispatch` table after `### deps_audit`, four rows in "Feature flags
and switches", and a "The helper lane (#587)" paragraph under "Model endpoint registry" that
states the trust boundary and the activation steps. It rewords two sentences that said nothing
dispatches the helper until #587. The new text cites function names, not line numbers.

````diff
diff --git a/docs/configuration.md b/docs/configuration.md
index 5b1dd40..f489791 100644
--- a/docs/configuration.md
+++ b/docs/configuration.md
@@ -78,7 +78,7 @@ Two consequences worth knowing:
 
 ## Layer 1: `config/config.yaml`
 
-The file is baked at `/opt/dark-factory/config/config.yaml` (`Dockerfile:152`). 110 leaf keys,
+The file is baked at `/opt/dark-factory/config/config.yaml` (`Dockerfile:152`). 111 leaf keys,
 documented below section by section. The scheduler's env projection is `apply_run_config_env`
 (`scripts/run_config_env.sh`); the list of exported keys is `ENV_WHITELIST`
 (`scripts/factory_core/effective_config.py:43-80`).
@@ -244,6 +244,18 @@ loop has two halves with separate environments, and both must be set to turn it
 | `deps_audit.ci_wait_minutes` | `20` | bounded wait for the deps PR's CI | **unread**; `deps_fixer.py` `main_once` (env, code default `20`) | `DEPS_AUDIT_CI_WAIT_MINUTES` (run only) | no |
 | `deps_audit.auto_merge` | `false` | merge the deps PR on green CI without approval | **unread**; `deps_fixer.py` `main_once` (env, code default `false`) | `DEPS_AUDIT_AUTO_MERGE` (run only) | `safety.deps_allowed_paths` bounds what the fix may touch |
 
+### `preview_helper_dispatch`
+
+Preview-helper shadow lane (#587). The key is **documentation only**: no code path reads
+`config/config.yaml` for it, and none of the lane's variables is in `ENV_WHITELIST`
+(`effective_config.py`). The lane lives entirely in the scheduler (`scripts/preview_helper_dispatch.sh`),
+so only the scheduler's environment matters; see "The helper lane (#587)" under
+[Model endpoint registry](#model-endpoint-registry-557) for activation.
+
+| Key | Default | Controls | Reader | Env override (scope) | Adapter |
+|---|---|---|---|---|---|
+| `preview_helper_dispatch.enabled` | `false` | kill-switch for the lane | **unread**. `preview_helper_check` in `scripts/preview_helper_dispatch.sh` dispatches only when `PREVIEW_HELPER_DISPATCH_ENABLED=true` **and** `PREVIEW_HELPER_ENDPOINT` is set in the scheduler's own environment; switching it off stops a running helper on the next poll (`kill_switch`) | `PREVIEW_HELPER_DISPATCH_ENABLED` (scheduler, `deploy/instance.env`; never forwarded) | no |
+
 ### `side_effect.phase_levels`
 
 Each phase starts at `5`. Reader: `scripts/factory_core/side_effect.py:209-231` (`phase_level`), called
@@ -331,9 +343,9 @@ Scenarios are `refine`, `plan`, `implement`, `conformance`, `docs-conformance`,
 
 Counting: `scheduler` 10, `refine` 3, `plan` 1, `direct_to_pr` 3, `conformance` 7, `code_review` 5,
 `docs_conformance` 2, `preview` 2, `conflict_resolution` 2, `blast_radius` 4, `dispatch_ceiling` 3,
-`epic_autopilot` 11, `main_red_autofix` 5, `deps_audit` 6, `side_effect.phase_levels` 10, `token_optimization` 32
+`epic_autopilot` 11, `main_red_autofix` 5, `deps_audit` 6, `preview_helper_dispatch` 1, `side_effect.phase_levels` 10, `token_optimization` 32
 (`enforce_budgets`, `default_budget_tokens`, 6 budgets, 6 enforce, `issue_context` 1,
-`architecture` 4, `memory` 5, `comments` 4, `diff` 3, `escalation` 1), `materializer` 4: 110 leaf keys.
+`architecture` 4, `memory` 5, `comments` 4, `diff` 3, `escalation` 1), `materializer` 4: 111 leaf keys.
 
 ### `materializer`
 
@@ -487,6 +499,10 @@ containers (`run-compose.yml:31-47`).
 | `FACTORY_MANIFEST_LABEL` | `manifest-intake` | `scripts/factory_core/handoff.py:57` | process that imports `handoff` |
 | `SIDE_EFFECT_LEVEL_<PHASE>` | unset | `side_effect.py:215` | `.archon/.env` |
 | `CONFORMANCE_SHADOW_MODEL` | unset | `commands/dark-factory-conformance.md:77` | `.archon/.env` |
+| `PREVIEW_HELPER_DISPATCH_ENABLED` | `false` | `preview_helper_enabled` in `scripts/preview_helper_dispatch.sh`: the lane runs only when this is `true` **and** `PREVIEW_HELPER_ENDPOINT` is set (#587) | `instance.env` (scheduler only; never forwarded) |
+| `PREVIEW_HELPER_ENDPOINT` | unset (a local registry `id`) | `scripts/preview_helper_dispatch.sh` (`_ph_entry`), interpolated by `run-compose.yml` into the `preview-helper` and `preview-helper-proxy` services as their `FACTORY_MODEL_ENDPOINT` (#634, #642). Not in `PROVIDER_PASSTHROUGH_VARS`; the instance-wide `FACTORY_MODEL_ENDPOINT` is not used | `instance.env` (scheduler only) |
+| `PREVIEW_HELPER_DAILY_MAX` | `20` | `preview_helper_check`: helper dispatches per UTC day, counted per attempt in `preview_helper:day:<date>`; a non-numeric value means 20, `0` dispatches nothing | `instance.env` (scheduler only) |
+| `PREVIEW_HELPER_CHANGESET_FLAG` | unset | set to `true` by `dispatch()` on `Fix issue #N` and `Continue issue #N` only while the lane is enabled; read by the marked block in `push-and-pr` (`workflows/archon-dark-factory.yaml`), which then writes `/var/lib/dark-factory/preview-pending/<issue>-<head_sha>.json` | never set by hand |
 | `SHADOW_BASE_URL` | set by the launcher only | `bench/_shadow_worker.py` (the one local arm's endpoint, from the arms file's `base_url`) | never set by hand; the worker's environment is exactly `PATH`, `HOME`, `SHADOW_BASE_URL` and the arm's `secret_env` (#563) |
 | (arm `secret_env`, e.g. `SHADOW_LOCAL_API_KEY`) | none | `bench/shadow_runner.py` (copied into the worker under the same name), `bench/_shadow_worker.py` (sent as `x-api-key`) | the operator's shell; a keyless server takes a dummy value (`bench/hosting_shadow.md`) |
 | `GH_TOKEN`, `GITHUB_TOKEN`, `model_endpoint.CLOUD_CREDENTIALS` names | must be unset | `bench/shadow_runner.py` presence check: the launcher refuses to start if any is set (no override) | the operator's shell (#563) |
@@ -637,13 +653,13 @@ A run container also resolves its hosting location once, before the clone, with
 `hosting=cloud` and always exits 0; any problem prints `hosting=cloud`. It reads the same
 staged copy and digest as `model-endpoints show` and passes the run's `--intent` to
 `load_verified_entry()`. So it prints `hosting=local` only for a verified local entry and the
-`classify-preview-helper` intent, which nothing dispatches until #587 (#586, #633;
+`classify-preview-helper` intent, which only the #587 lane's helper container runs (#586, #633;
 [run-lifecycle.md](run-lifecycle.md) section 6.F).
 
 **The helper container (#642, inert).** `run-compose.yml`'s `preview-helper` service, under its
-own `preview-helper` profile, is the container #587's dispatcher will start for each
-`classify-preview-helper` dispatch. Nothing in this repository starts it, and nothing dispatches
-it until #587: `entrypoint.sh` still answers that command string "not available". Its
+own `preview-helper` profile, is the container the #587 lane starts for each
+`classify-preview-helper` dispatch (below); nothing else starts it, and `entrypoint.sh` still
+answers that command string "not available". Its
 `entrypoint:` is `scripts/preview_helper_entry.sh`, not `entrypoint.sh`: no tracker or code-host
 preflight, no clone, no `git`, `gh` or `docker`. Its environment is exactly nine keys and holds
 no credential of any kind: `PYTHONPATH`, `ANTHROPIC_BASE_URL` (`http://preview-helper-proxy:8787`),
@@ -665,6 +681,33 @@ halves on both sides (prompt, answer, result, and the scheduler-side verbs #587
 `docker-build` CI job checks the pinned CLI's request shape through the real helper proxy with
 `tests/tier2_preview_helper_cli.sh`.
 
+**The helper lane (#587).** `scripts/preview_helper_dispatch.sh`, sourced by `scheduler.sh`,
+dispatches that container as a **shadow, non-gating** classifier: nothing reads its result,
+and `preview-up` still reads only the implement run's own `classify-preview` node. While the
+lane is enabled, `dispatch()` adds `-e PREVIEW_HELPER_CHANGESET_FLAG=true` to `Fix issue #N`
+and `Continue issue #N`, and `push-and-pr` writes the pushed changeset to
+`preview-pending/<issue>-<head_sha>.json`. Each poll, `preview_helper_finalize` runs first
+(before the rate-limit wait, the board fetch and the capacity guard) and `preview_helper_check`
+runs last (no session-window pause, nothing else dispatched). The stage stages the registry
+itself, starts `preview-helper-proxy` with `up -d` (refusing if a proxy of this compose project
+already exists, in any state), writes `current-run.json`, and starts the helper as
+`<FACTORY_RUN_PREFIX>ph-<issue>` with no `-e` flag, so it counts toward `FACTORY_WIP_LIMIT`.
+One helper at a time, one dispatch per issue and head SHA (`preview_helper:stamp:<issue>:<sha>`,
+deleted after 7 days), at most `PREVIEW_HELPER_DAILY_MAX` a day. A helper running longer than
+the entry's `timeouts.total_seconds` is stopped. The scheduler is the only writer of the
+helper's `runs.jsonl` row (`intent: classify-preview-helper`, `cost_usd: null`); every failure
+ends `abstained`, never touches an issue's breaker or retry keys, and counts on
+`preview_helper:failures`: two in a row stop the lane until
+`breaker-reset --key preview_helper:failures`. Every gated run container mounts the same state
+volume read-write and can forge any of these files; the lane only guarantees that the
+scheduler never blocks on, reads unboundedly, or writes or deletes through what is planted
+there. **Activation is #564's canary only.** The operator must (1) run Docker Engine 26 or
+later, (2) place on the human-only instance surface a local registry entry with
+`secret_reference: FACTORY_HELPER_MODEL_SECRET` and its `tested` capability-evidence record from
+#562 (`invalidated_by` at least `model_digest`, `harness_version`, `registry_config`), and (3) set
+`PREVIEW_HELPER_DISPATCH_ENABLED=true`, `PREVIEW_HELPER_ENDPOINT=<that id>` and
+`FACTORY_HELPER_MODEL_SECRET` in the scheduler's environment. To stop: unset the switch.
+
 **Back to default cloud:** remove `FACTORY_MODEL_ENDPOINT` from `instance.env` (and
 `FACTORY_MODEL_REGISTRY_DIR` from the interpolation env), then recreate the scheduler with
 `docker compose -f deploy/docker-compose.yml up -d`. No staged copy is read while
````

- [ ] **Step 4.4: Update `docs/run-lifecycle.md` and `docs/scheduler.md`**

Apply both patches (`/tmp/587-run-lifecycle.patch`, `/tmp/587-scheduler-doc.patch`, then
`git apply` each):

````diff
diff --git a/docs/run-lifecycle.md b/docs/run-lifecycle.md
index afa413c..73d282b 100644
--- a/docs/run-lifecycle.md
+++ b/docs/run-lifecycle.md
@@ -62,7 +62,9 @@ The command is interpreted twice, by two different mechanisms:
    `fix main` become `fix-main`, and `Audit deps` / `audit deps` become `audit-deps`.
    `Classify preview (helper) #N` and `Verify preview classification #N`, matched as whole
    strings and case-sensitively, become `classify-preview-helper` and
-   `classify-preview-checker`; both exit 1 with "not available" until #587. Any other command
+   `classify-preview-checker`; both still exit 1 with "not available" here: the #587 lane
+   sends the helper string to the separate `preview-helper` service, which never runs
+   `entrypoint.sh`, and nothing sends the checker string (#641). Any other command
    is matched case-insensitively by its leading word against
    `fix|continue|close|refine|plan|deconflict|recheck`. No match exits 1 with the usage text on
    stderr, before any clone, tracker or model call; there is no fallback to `fix`. The first
@@ -85,11 +87,19 @@ The command is interpreted twice, by two different mechanisms:
 | `Recheck main` (`scheduler.sh:159`) | `recheck` | never reached | Entrypoint only: smoke gate, then exit |
 | `Fix main` (`scheduler.sh:185`) | `fix-main` | never reached | Entrypoint only: main-red auto-fix |
 | `Audit deps` (`deps_audit_check` in `scheduler.sh`) | `audit-deps` | never reached | Entrypoint only: dependency-health loop |
-| `Classify preview (helper) #N` (not issued yet; #587) | `classify-preview-helper` | never reached | Exits 1, "not available", until #587 |
-| `Verify preview classification #N` (not issued yet; #587) | `classify-preview-checker` | never reached | Exits 1, "not available", until #587 |
+| `Classify preview (helper) #N` (`dispatch_preview_helper` in `scripts/preview_helper_dispatch.sh`, #587) | not reached: the command goes to the `preview-helper` service | never reached | `scripts/preview_helper_entry.sh` in the `preview-helper` service (#642): one tool-less model call on the local endpoint, one `result.json`. Shadow only |
+| `Verify preview classification #N` (not issued; #641) | `classify-preview-checker` | never reached | Exits 1, "not available" |
 
 When and why the scheduler picks each command is covered in [scheduler.md](scheduler.md).
 
+The helper's run has no entrypoint, workflow, cost report or comment. The scheduler finalises
+it: `preview_helper_finalize`, the first call of every poll (before the rate-limit wait, the
+board fetch and the capacity guard), notices that the container named
+`<FACTORY_RUN_PREFIX>ph-<N>` has gone or has run past its limit, removes it and its proxy, and
+writes its one `runs.jsonl` row (`intent: classify-preview-helper`) through
+`scripts/factory_core/preview_helper.py record-outcome`. Nothing reads that row
+([configuration.md](configuration.md#model-endpoint-registry-557), "The helper lane").
+
 The workflow's `resolve` intent has no command that reaches it today; see
 [oddity 1](#open-questions-and-oddities).
 
````

````diff
diff --git a/docs/scheduler.md b/docs/scheduler.md
index 641cc38..4057c51 100644
--- a/docs/scheduler.md
+++ b/docs/scheduler.md
@@ -98,7 +98,7 @@ not reach run containers; see [Dispatch](#dispatch).
 
 ```mermaid
 flowchart TD
-    A[poll start: DISPATCHED = empty, compute BOT_RE] --> B[check_rate_limit]
+    A[poll start: DISPATCHED = empty, preview_helper_finalize, compute BOT_RE] --> B[check_rate_limit]
     B --> C{fetch_board_items ok?}
     C -- no --> Z1[log error=gh_api_failed, sleep, next poll]
     C -- yes --> D[split items by status, count, count_refine_running]
@@ -118,10 +118,11 @@ flowchart TD
     O --> P[stage_plan: Refined]
     P --> Q[stage_refine: Backlog]
     Q --> R[stage_epic_autopilot: only if nothing dispatched]
-    R --> S[summary log line, sleep POLL_INTERVAL]
+    R --> R2[preview_helper_check: only if not paused and nothing dispatched]
+    R2 --> S[summary log line, sleep POLL_INTERVAL]
 ```
 
-The loop body is `scheduler.sh:1670-1790`.
+The loop body is `scheduler.sh:1677-1808`.
 
 ### What is fetched each poll
 
@@ -220,6 +221,7 @@ so one poll normally starts one run.
 | P4 | `stage_plan` (`:1392-1453`) | Refined | `paused_only` | Not running, `REFINE_RUNNING < REFINE_WIP_LIMIT`, breaker checks pass. Items with `plan-pending-review` **and** `direct-to-pr` go to `plan_advance_check` instead | Comments "Starting plan generation" and dispatches `Plan issue #N` | Refine skip | `REFINE_WIP_LIMIT` (stage stops); retry key `N:plan`, ceiling `REFINE_MAX_RETRIES` |
 | P5 | `stage_refine` (`:1456-1520`) | Backlog | `paused_only` | Carries `ready-for-agent` or `direct-to-pr`, not running, `REFINE_RUNNING < REFINE_WIP_LIMIT`, breaker checks pass. Items with `spec-pending-review` go to `spec_advance_check` instead | Comments "Starting brainstorming" and dispatches `Refine issue #N` | Refine skip | `REFINE_WIP_LIMIT` (stage stops); retry key `N:refine`, ceiling `REFINE_MAX_RETRIES` |
 | P6 | `stage_epic_autopilot` (`:1527-1533`) | (its own queries) | Own condition: nothing dispatched this poll, main green, not paused, `EPIC_AUTOPILOT_ENABLED=true` | See [Epic autopilot](#epic-autopilot) | Runs `cli.py epic-autopilot --once` in the scheduler process. May add `direct-to-pr` to one child or start one epic. Starts no container itself | Its own eligibility rules | Daily cap |
+| P7 | `preview_helper_check` (`scripts/preview_helper_dispatch.sh`) | (pending changesets on the state volume) | Not paused, nothing dispatched this poll; `PREVIEW_HELPER_DISPATCH_ENABLED=true` and `PREVIEW_HELPER_ENDPOINT` set; no helper in flight; no `preview-helper-proxy` of this project in any state | An unstamped valid `preview-pending/<N>-<sha>.json` | Starts the helper proxy and `Classify preview (helper) #N` in the `preview-helper` service. Shadow only; never touches the board, labels or an issue's breaker keys. `preview_helper_finalize` reaps it at the top of a later poll | — | One at a time; `preview_helper:stamp:<N>:<sha>`; `PREVIEW_HELPER_DAILY_MAX` (20); stops after two consecutive abstentions (`preview_helper:failures`) |
 
 Items are processed in the order the board query returns them; the scheduler applies no
 priority ordering of its own within a status (`get_items_by_status`, `scheduler.sh:742-747`).
@@ -309,6 +311,7 @@ these:
 | `Recheck main` | `main_red_recheck_check` |
 | `Fix main` | `main_red_fixer_check` |
 | `Audit deps` | `deps_audit_check` |
+| `Classify preview (helper) #N` | `preview_helper_check` → `dispatch_preview_helper` (`scripts/preview_helper_dispatch.sh`), to the `preview-helper` service, not `dark-factory` |
 
 `entrypoint.sh:99-108` turns the first word into the run's intent. What each intent
 does is in [run-lifecycle.md](run-lifecycle.md).
@@ -435,6 +438,9 @@ the named volume `<FACTORY_INSTANCE>-scheduler-state` mounted into both the sche
 | `main-red-fixer-state.json` | `main_red_fixer.py` in the `Fix main` run | Same | Attempts per regression ticket |
 | `deps-audit-last-run` | Scheduler (`deps_audit_check`) | Scheduler `deps_audit_due` | `Audit deps` throttle (file mtime) |
 | `deps-fixer-state.json` | `deps_fixer.py` in the `Audit deps` run | Same | Attempts per advisory-set hash |
+| `preview-pending/<N>-<sha>.json` | Implement run (`push-and-pr`, only with `PREVIEW_HELPER_CHANGESET_FLAG=true`) | Scheduler (`preview_helper.py next-candidate`, `sweep`) | Changeset the helper lane classifies; deleted when stamped or after 7 days |
+| `preview-helper-inflight.json` | Scheduler (`preview_helper.py write-inflight`) | Scheduler `preview_helper_finalize` | The one helper in flight: run id, issue, head SHA, start, limit. Never trusted |
+| `preview-helper/` (`input.json`, `current-run.json`, `result.json`) | Scheduler (first two), helper container (`result.json`) | Helper, `preview-helper-proxy` (pointer), scheduler | The helper's one writable directory; emptied after every dispatch |
 | `autopilot-state.json` | `epic_autopilot.py` in the scheduler process | Same | Daily advance count, cached verdicts |
 | `runs.jsonl` | Run containers (`entrypoint.sh`); scheduler appends a `stop_condition` row when `evaluate_stop` reports a stop (`breaker.py` `_append_stop_audit_row`) | Reporting | Run ledger |
 | `graphql-costs.jsonl` (rotates to `.1`) | Scheduler and run containers via `factory_core/cost_log.py` — **only when `SCHEDULER_STATE_DIR` is set in the child's environment** (unset in CI's pytest job, which asserts this dir stays empty) | Operator (manual tailing; no factory code reads it) | One JSONL line per measured GraphQL call (`board_items`, `issue_states`): `ts`, `source` (`scheduler` or a run id, via `DARK_FACTORY_COST_SOURCE`), `context`, `cost`, `remaining`, `reset_at`. Size rotation at ~1 MiB, one retained generation. Fail-soft by design (#607) |
````

- [ ] **Step 4.5: Verify the docs checks pass**

```bash
PYTHONPATH=scripts python -m pytest tests/test_docs_conformance_reference_docs.py tests/test_docs_refs_check.py -q 2>&1 | tail -1
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```

Expected: pytest reports only `passed`, then `Docs reference check passed for 4 doc(s).`

- [ ] **Step 4.6: Commit**

```bash
git add config/config.yaml docs/configuration.md docs/run-lifecycle.md docs/scheduler.md
git commit -m "docs(#587): the preview-helper lane — documentation-only config key, its four variables, activation, poll order"
```

---

## Task 5: The two memory entries approved with the spec

**Files:** `.archon/memory/architecture.md`

- [ ] **Step 5.1: Append these two lines, verbatim, to the end of `.archon/memory/architecture.md`**

They are the #587 entries on the approved refine branch at `adb592b`. The first records the
rejected "early-node changeset" design. The second is the entry the spec's decision 4 corrected
to the mount design. No other memory line changes.

```markdown
- [AVOID] Rejected approach: deriving a shadow/secondary artifact (e.g. a changeset snapshot for a later-dispatched helper/checker) from an early-DAG node's output (like preview-changeset, which runs before conformance/docs-conformance/push-and-pr). Later nodes in the same run can add commits (auto-fixes, OOS excision, memory commits), so an early node's head SHA is routinely stale by the time anything reads it after the run. Chosen approach: recompute and write such artifacts at the actual push step (push-and-pr, after git push and the ls-remote push-confirmation), keyed by the real, final head SHA. <!-- issue:#587 date:2026-11-27 expires:2027-05-27 source:refine agent:refine scope:architecture path:workflows/archon-dark-factory.yaml -->
- [AVOID] A local helper-dispatch run container (e.g. #587's preview-helper) must not mount the state volume's root: by design it mounts only two sub-directories (the staged model-registry read-only, its own artifact directory read-write), so it has no read path to the model-proxy's request-ledger, scheduler-state.json or runs.jsonl and cannot observe transport-level failures (endpoint_unreachable, timeout classes, wrong served model) that happen inside the proxy — it can only know what it determined before/around its own model call (preflight refusal, missing input, schema-invalid output, its own timeout). Only the scheduler, after the container exits, holds both the helper's self-reported result and the ledger rows for that run id (via run_record._compute_model_dispatch()), so failure-class resolution for a local helper's run record must be a scheduler-side reconciliation step (the entry-script class, except that its model_call_failed/local_timeout_total give way to the ledger's failure class when the proxy recorded one; a helper "ok" counts only with a completed local model-request row in the ledger), never something the entry script tries to fully classify itself. Separately: is_issue_running() (scheduler.sh) already matches any running container's command string against '#<issue>' with no service-name filter, so a running helper/checker dispatch for issue N already correctly blocks a new gated dispatch for issue N with zero code change — do not assume this function needs a narrow per-service exclusion before verifying it against the live grep. <!-- issue:#587 date:2026-11-27 expires:2027-05-27 source:refine agent:refine scope:architecture path:.archon/commands/ -->
```

- [ ] **Step 5.2: Verify and commit**

```bash
test "$(grep -c 'issue:#587' .archon/memory/architecture.md)" = 2 && echo MEMORY_OK
git add .archon/memory/architecture.md
git commit -m "memory(#587): architectural decisions from the scheduler-lane spec"
```

Expected: `MEMORY_OK`.

---

## Task 6: Full verification

- [ ] **Step 6.1: The whole Python suite**

```bash
PYTHONPATH=scripts python -m pytest tests/ -q 2>&1 | tail -1
```

Expected: only `passed` (measured on the scratch clone: `4434 passed`).

- [ ] **Step 6.2: Every `ci.yml` shell step, compared with `main`**

```bash
for t in $(grep -oE 'bash tests/test_[A-Za-z0-9_]+\.sh' .github/workflows/ci.yml | awk '{print $2}'); do
  timeout 600 bash "$t" >/dev/null 2>&1; echo "$? $t"; done
```

Expected: `0` for `tests/test_preview_helper_scheduler.sh`, `tests/test_preview_changeset_write.sh`,
`tests/test_scheduler.sh` and `tests/test_scheduler_lib.sh`. Every other line gives the same
result as the same loop on unchanged `main` (see "Sandbox note").

- [ ] **Step 6.3: DAG and docs gates**

```bash
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
```

Expected: all three print their "passed" line.

- [ ] **Step 6.4: Scope check**

```bash
git diff --name-only main...HEAD | sort
```

Expected: exactly the twelve files of the File Structure table, plus this plan and the spec
under `docs/superpowers/`. No path under `deploy/`, no `run-compose.yml`, no
`scripts/factory_core/`, no `entrypoint.sh`, no `scripts/provider_env.sh`, no `gate_*`.

- [ ] **Step 6.5: Blast radius (expected hold)**

```bash
git diff --name-only main...HEAD | python scripts/gate_blast_radius.py --changed-files-stdin \
  --lines-changed "$(git diff --shortstat main...HEAD | awk '{print $4+$6}')" \
  --hotspots docs/codeindex-hotspots.md --config .claude/skills/refinement/config.yaml --clone-dir . | head -9
```

Expected: `STATUS: HUMAN_REQUIRED` with one trigger,
`.github/workflows/ci.yml (category: migration-seed)`. This hold is intended (spec, "Blast
radius"): the operator requires human approval for the first ticket that lets the scheduler
start a local model's container, whatever the gate says.

---

## Acceptance-criterion → test map

| # | Criterion (issue body) | Test (file → case) |
|---|---|---|
| 1 | no `-e` on the helper dispatch; never calls `provider_env_flags` | `test_preview_helper_scheduler.sh` → `helper_dispatch_argv_has_no_dash_e_and_never_calls_provider_env_flags [argv / bodies]` |
| 2 | lane off: nothing issued; existing dispatches byte-identical; `cloud_endpoint_confirmed()` unchanged | `existing_dispatches_are_byte_identical_with_the_lane_off [unset/switch_only/endpoint_only/switch_false]`, `lane_off_issues_nothing`, `cloud_endpoint_confirmed_ignores_the_helper_variables` (*guard*) |
| 3 | lane on: only the flag on Fix/Continue | `lane_on_adds_only_the_changeset_flag_to_fix_and_continue` |
| 4 | one test per scheduler failure class | `every_scheduler_failure_class_ends_abstained_with_a_record [registry_stage_failed, endpoint_not_resolved, proxy_start_failed, dispatch_failed, wall_clock_exceeded, kill_switch]` |
| 5 | two abstentions stop; ok resets; `input_too_large` counts | `two_abstentions_stop_the_stage_until_reset`, `an_ok_resets_the_count`, `input_too_large_counts_toward_the_stop` |
| 6 | no second dispatch per issue+SHA, each stamp value | `a_stamped_sha_is_never_dispatched_again [after ok / after abstained / hand-written]`, `a_stamp_is_honoured_with_an_oversized_state_file` |
| 7 | daily ceiling default/override; retention | `the_daily_ceiling_stops_dispatch [default 20 / env 3 / non-numeric / 0]`, `expired_stamps_and_day_counts_are_deleted`, `expiry_never_resets_a_key_that_is_not_the_lanes` |
| 8 | not during pause, at capacity, or after another dispatch | `stage_respects_pause_capacity_and_prior_dispatch` (+ the static call-site check) |
| 9 | helper name prefix counted; proxy not | `helper_name_counts_toward_wip_and_the_proxy_does_not` |
| 10 | over-limit reaped at capacity; finalise first in the poll | `reaper_runs_at_capacity_and_records_wall_clock_exceeded`, `finalise_is_the_first_call_in_the_poll` |
| 11 | stale proxy of this project blocks; other project's does not | `a_stale_proxy_of_this_project_blocks_and_another_projects_does_not [this project / another project]` |
| 12 | untrusted in-flight file | `an_unreadable_inflight_entry_is_cleaned_up [fifo / directory / shell_metacharacters / start_in_the_future / limit_above_the_entry]`; 12a `lane_off_*`; 12b `a_directory_at_an_artifact_name_does_not_stop_the_lane`, `every_declined_dispatch_is_logged` |
| 13 | planted links never followed | `planted_links_are_never_followed [temporary and final names / artifact dir / finalise / pending dir / static]` |
| 14 | FIFO ledger: record step bounded | `a_bad_ledger_ends_ledger_unavailable_at_once`, `a_blocking_record_step_ends_record_write_failed` |
| 15 | `push-and-pr`: no file without flag; one valid file with it; never fails the node | `test_preview_changeset_write.sh` → `no_file_without_the_flag` (*guard*), `one_valid_file_with_the_flag` (+ intake), `the_block_never_fails_the_node` |
| 16 | `preview-up`/`classify-preview` don't reference the lane; only marked text added | `test_preview_helper_lane.py` → `test_preview_up_and_classify_preview_do_not_reference_the_lane` (*guard*), `test_the_only_workflow_text_added_is_between_the_markers` |
| — | (plan C2, C3) both bare call sites survive `set -euo pipefail`; an unreadable state declines | `errexit: …` (five cases), `every_declined_dispatch_is_logged [state_unreadable]` |
| 17 | documentation | `tests/test_docs_conformance_reference_docs.py` (111 leaf keys), `scripts/docs_refs_check.py` |

## Not verified (carried from the spec)

Anything against a real endpoint or model. The real `docker compose up/run/rm`, `docker
stop/ps`, service-name resolution on `preview-helper-net` and the subpath mounts (`docker` is a
stub). The 3 s settle time against a real container. That `PREVIEW_HELPER_CHANGESET_FLAG` set
with `run -e` reaches the `push-and-pr` shell through Archon (`BENCH_MODE` reaches nodes the
same way). The main loop itself: call order is checked statically. These belong to #564's
canary.
