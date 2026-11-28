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
