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
