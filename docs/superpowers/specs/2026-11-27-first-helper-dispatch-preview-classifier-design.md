# First Helper Dispatch — The Scheduler Lane for the `preview-helper` Container

**Issue:** #587

**Status:** written by the operator's reviewer session after three factory refine passes were
rejected (2026-11-26, twice on 2026-11-27), read independently by a second reviewer twice
(amended after each read), and cut in two by operator decision of 2026-11-27. It replaces the specs at `8b9e4d8` and
`76a2716` in full. Every mechanism was prototyped and run hermetically (`--network none`, the
factory image, the pinned CLI 2.1.282) on the executed end state of #633 and #634
(`fdbba7c`). Nothing was run against a real endpoint or model, and no container was started
from the compose file: `docker` was a stub that started the real proxy module and the real
entry script as bare processes.

The issue body is the scope. Where this spec chooses differently from the body's wording it
says so under "Named deviations" and shows the property is kept.

## Overview

Contract §4.11 decision 3 allows only helper dispatches on a local endpoint in v1. #642
delivers the inert pieces: the `preview-helper` service, its entry script, the endpoint-only
preflight verb and the module `factory_core/preview_helper.py`. **This ticket is the part
that dispatches**: a sourced scheduler file with one stage, one dispatch function and one
finalise step; the changeset flag in `dispatch()`; the block in `push-and-pr` that writes the
changeset; the stamps, in-flight state, daily count and failure stop; the run-record call; the
activation documentation.

Shadow only. The scheduler turns the helper's result file into one run record. **Nothing
reads either.** The checker is #641. The implement run's in-process `classify-preview` node
stays the only input `preview-up` reads.

Merging activates nothing: the lane needs a switch, an endpoint variable, a secret and a
`tested` capability-evidence record, all placed by an operator (see "Activation").

## Decisions (recorded)

Operator decisions of 2026-11-27, under the owner's delegation, for the owner to overrule.
None is an owner decision.

1. `output_config` is suppressed client-side, in #642's entry script. The local adapter's
   refusal is not changed.
2. Compose interpolation carries the per-dispatch values, not `-e` flags (deviation 1).
3. The scheduler writes `current-run.json` and chooses the run id; the helper never does.
4. Two memory entries that recorded rejected designs as fact are deleted from
   `.archon/memory/architecture.md`; a third is corrected to the mount design; none is
   restored.
5. The checker is #641.
6. The helper does not mount the state volume's root (#642, deviation 7).
7. **A per-UTC-day ceiling on helper dispatches**, in its own non-issue key, default 20.
8. The work is cut in two tickets; this one adds only scheduler and workflow code.

Decisions of this spec's author, for review:

9. The changeset flag is added only while the lane is enabled: the switch is on **and**
   `PREVIEW_HELPER_ENDPOINT` is set. The body says "only when the helper switch is on"; its
   criterion says dispatches are byte-identical with either unset. Both hold.
10. Proxy readiness is "still running after 3 s", not an HTTP probe and not a log line.
11. One helper at a time; switching the lane off stops a running helper (`kill_switch`).
12. `preview_helper_finalize` is the first call in the poll, before the rate-limit wait and
    the board fetch (see "Why the finalise step is first").
13. The daily default is 20. The lane classifies one head SHA per implement push; at the
    default WIP limit of 1 an instance pushes far fewer than 20 a day, so a normal day is
    never cut. It holds a run that forges **only pending files** to 20 dispatches a day. It
    does not hold a run that also rewrites `scheduler-state.json`, which can clear the count
    as it can any counter there (see "Trust and forgery").
14. `PREVIEW_HELPER_LIMIT_CEILING=3600`: the largest limit an in-flight file may claim when
    the registry entry cannot be read (lane switched off, registry removed).
15. **With the lane off, an in-flight entry is finalised in full, and always as
    `kill_switch`** (see "Lane off").
16. Before dispatching, the stage asks the scheduler's own state reader whether the SHA is
    stamped, in addition to the module's check.

## Named deviations from the body's wording

Numbering is shared with #642's spec.

1. **"An explicit, fixed list of `-e` names" becomes compose interpolation.** #642's service
   names nine environment keys; `dispatch_preview_helper()` exports three variables
   function-locally (`local -x`) and passes no `-e`. Property kept: the container receives
   nothing from `provider_env_flags` and nothing the scheduler did not set for this dispatch.
   Measured: across the whole prototype run no compose invocation of the lane carried `-e`.
   The acceptance statement "the recorded argv … contains exactly the fixed `-e` list" is
   reworded in the Test Plan: the argv contains no `-e` at all.
2. The in-container enforcer bounds the whole script and ends 5 s early (#642). The
   scheduler's enforcer uses the same value, the entry's `timeouts.total_seconds`.
3. `issue_number` in a pending file must be a JSON integer equal to the file name's (#642's
   intake). The block below writes an integer.
4. **"`preview-up` byte-identical" is checked as a text-reference check**, not a byte
   comparison of rendered prompts: neither `preview-up`, `classify-preview` nor any file under
   `commands/` mentions the lane, and the only workflow text added sits between two markers in
   `push-and-pr`. A byte comparison of the two nodes' text before and after says the same and
   breaks on any unrelated edit of those nodes.
5. "Evidence not `tested`" is reported as `preflight_refused` (#642).
6. **The stale-proxy check is scoped to this compose project.** `docker ps` is host-wide and
   two instances share a host; a proxy of the other instance is not this lane's to refuse on.
   Both labels (project and service) are required.
7. "State volume read-only" becomes "state volume root not mounted" (#642). Consequence here:
   the scheduler must stage the registry and create `preview-helper/` before each start.

## Requirements

Numbered as the body's "Scope and boundaries".

1. **Shadow only.** No helper output is read by any gate, merge, label, board move, advance,
   or by an issue's breaker or retry counters. Completion is not success. Any output that is
   not a clean parse is recorded `needs_preview=true`.
2. The entry script is #642's. `entrypoint.sh` is not edited.
3. The service and network are #642's. This ticket does not edit `run-compose.yml`.
4. **One stage, one separate dispatch function, one finalise step** in a new sourced file
   `scripts/preview_helper_dispatch.sh`; `dispatch()` gains only the changeset flag.
5. **Wall-clock limit**, two enforcers, the entry's `timeouts.total_seconds`.
6. **The changeset file**, written only when flagged, atomically, never failing `push-and-pr`.
7. **Run record: one writer, the scheduler**, one row in `runs.jsonl` per dispatch, written
   under a process timeout.
8. **Failure policy**: every failure ends `abstained` with a run record; one non-issue
   counter; two consecutive abstentions stop the stage until an operator resets it; a daily
   ceiling bounds dispatches whatever their outcome.
9. **State-volume honesty**: see "Trust and forgery".
10. **Activation**: see that section.
11. **Every write**: see the table.
12. No `gate_*`, existing breaker key, budget, WIP or ceiling logic, tool allow/deny list of
    an existing node, `deploy/**` or `publish.yml` change. With the lane off, every existing
    dispatch is byte-identical.

## Interface taken from #642

This ticket calls, and does not change:

- **Compose.** `docker compose -f /opt/dark-factory/docker-compose.yml --profile preview-helper
  run --name <name> -d --rm preview-helper "Classify preview (helper) #<issue>"`. The service
  interpolates `PREVIEW_HELPER_ENDPOINT`, `FACTORY_MODEL_REGISTRY_SHA256` and
  `PREVIEW_HELPER_ENDPOINT_SERVED_NAME` from the caller's environment and mounts the state
  volume's `model-registry/` (read-only) and `preview-helper/` (read-write) sub-directories,
  which must both exist. #634's `preview-helper-proxy` service additionally needs
  `PREVIEW_HELPER_ENDPOINT_BASE_URL` and `FACTORY_HELPER_MODEL_SECRET`.
- **The entry script's contract.** It reads `preview-helper/input.json`, writes
  `preview-helper/result.json` and nothing else, ends within the entry's
  `timeouts.total_seconds` minus 5 s, and reports one of six classes.
- **Module verbs** (`python3 scripts/factory_core/preview_helper.py <verb>`):

| Verb | Output | Exit |
|---|---|---|
| `sweep --pending-dir --state-file` | names removed | 0 |
| `expired-keys --state-file` | keys to delete, one per line | 0 |
| `next-candidate --pending-dir --state-file --out` | `<issue> <sha>`; writes the validated record to `--out` | 0; 1 when there is none; 2 when the copy could not be written |
| `prepare-dir --dir` | creates the directory if absent, makes it mode 0777, removes the three artifact names | 0, or 1 when it is not a real directory |
| `clear --dir --inflight` | removes the three artifact names and the in-flight entry, whatever they are | 0 |
| `write-pointer --dir --run-id --issue` | writes `current-run.json` | 0 |
| `write-inflight --file --run-id --issue --head-sha --started --total` | writes the in-flight file | 0 |
| `read-inflight --file --now --max-total` | `<run_id> <issue> <sha> <started> <total>` | 0, or 1 when unreadable |
| `record-outcome --run-id --issue --head-sha --result-file --ledger-path [--forced-class]` | `<outcome> <class or ->`; appends one row to `runs.jsonl` | 0 |

- **Key names**: `preview_helper:stamp:<issue>:<sha>` (value: the epoch it was written at),
  `preview_helper:day:<YYYY-MM-DD>` (UTC), both read by `expired-keys`, which lists a key
  only when the whole key has one of those two shapes.
- **Guarantees relied on.** Every read in those verbs is one bounded, non-blocking read of a
  regular file (a FIFO, device, link or oversized file is "not there"); every write creates
  its temporary file `O_CREAT | O_EXCL | O_NOFOLLOW` and renames it; every directory is
  opened `O_DIRECTORY | O_NOFOLLOW` and used through that descriptor, so a pending or
  artifact directory that is, or becomes, a link is never listed, written or deleted through;
  `read-inflight` refuses a start in the future and a limit above `--max-total`;
  `record-outcome` reads a ledger file only when `lstat` says it is a regular file of at most
  128 MiB (else `ledger_unavailable`) and writes only strings from a closed set or of a fixed
  shape. The complete module listing is in #642's spec.

## The scheduler (requirements 4, 5, 7, 8)

New file `scripts/preview_helper_dispatch.sh`, sourced by `scheduler.sh` next to
`provider_env.sh`. Complete listing:

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
  ids=$(preview_helper_proxy_ids)
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
    id=$(preview_helper_proxy_ids | head -1)
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
  limit=$(_ph_entry | jq -r '.timeouts.total_seconds // empty | floor' 2>/dev/null)
  [[ "$limit" =~ ^[1-9][0-9]*$ ]] || limit="$PREVIEW_HELPER_LIMIT_CEILING"
  if ! fields=$(_ph_py read-inflight --file "$inflight" --now "$(date +%s)" --max-total "$limit" 2>/dev/null); then
    # Unreadable state (not a small regular file, a field out of shape, a start in the
    # future, a limit above the entry's): nothing can be attributed. Stop any helper of this
    # instance, remove the proxy, clear everything, count one failure. No run record: there
    # is no trustworthy run id.
    _ph_log "inflight_unreadable action=cleanup"
    docker ps --format '{{.Names}}' 2>/dev/null | grep -E -- "^${FACTORY_RUN_PREFIX}ph-[0-9]+$" \
      | while IFS= read -r name; do docker rm -f "$name" >/dev/null 2>&1 || true; done
    preview_helper_proxy_down
    _ph_clear
    increment_retry "$PREVIEW_HELPER_FAILURE_KEY" >/dev/null
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
    "ok "*) reset_retry "$PREVIEW_HELPER_FAILURE_KEY" ;;
    *)      increment_retry "$PREVIEW_HELPER_FAILURE_KEY" >/dev/null ;;
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
  _ph_write_inflight "$run_id" "$issue" "$sha" "$now" 0 \n    || { _ph_log "skip reason=inflight_write_failed"; return 1; }

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
  local day_key max key
  day_key="preview_helper:day:$(date -u +%F)"
  max="${PREVIEW_HELPER_DAILY_MAX:-$PREVIEW_HELPER_DAILY_MAX_DEFAULT}"
  [[ "$max" =~ ^[0-9]+$ ]] || max="$PREVIEW_HELPER_DAILY_MAX_DEFAULT"
  _ph_py sweep --pending-dir "$pending" --state-file "$STATE_FILE" >/dev/null 2>&1 || true
  # Stamps and day counts older than the retention leave scheduler-state.json.
  # Each line is checked again here: nothing but this lane's own two key shapes is ever reset.
  _ph_py expired-keys --state-file "$STATE_FILE" 2>/dev/null \
    | while IFS= read -r key; do
        [[ "$key" =~ ^preview_helper:(stamp:[1-9][0-9]*:[0-9a-f]{40}|day:[0-9]{4}-[0-9]{2}-[0-9]{2})$ ]] || continue
        reset_retry "$key"
      done
  if [ "$(get_retry_count "$day_key")" -ge "$max" ]; then
    _ph_log "skip reason=daily_max count=${max}"
    return 0
  fi
  if [ "$(get_retry_count "$PREVIEW_HELPER_FAILURE_KEY")" -ge "$PREVIEW_HELPER_FAILURE_STOP" ]; then
    _ph_log "skip reason=failure_stop key=${PREVIEW_HELPER_FAILURE_KEY}"
    return 0
  fi
  if [ -n "$(preview_helper_proxy_ids)" ]; then
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
  if [ "$(get_retry_count "preview_helper:stamp:${issue}:${sha}")" != "0" ]; then
    _ph_log "skip reason=already_stamped issue=#${issue}"
    _ph_clear
    return 0
  fi
  # Stamped before the dispatch: any value means this head SHA is never dispatched again.
  # The value is the epoch, so sweep can age it out with the pending file's own retention.
  STATE_FILE="$STATE_FILE" python3 "$FACTORY_CORE_CLI" breaker-set-retry \
    --key "preview_helper:stamp:${issue}:${sha}" --value "$(date +%s)"
  increment_retry "$day_key" >/dev/null
  if dispatch_preview_helper "$issue" "$sha"; then
    DISPATCHED="Classify preview (helper) #${issue}"
  fi
  return 0
}
```

`scheduler.sh` changes, all of them:

```diff
--- a/scheduler.sh
+++ b/scheduler.sh
@@ -20,6 +20,7 @@ RECHECK_STAMP_FILE="${SCHEDULER_STATE_DIR}/main-red-last-recheck"
 source "$(dirname "${BASH_SOURCE[0]:-$0}")/scripts/identity.sh"
 source "$(dirname "${BASH_SOURCE[0]:-$0}")/scripts/scheduler_lib.sh"
 source "$(dirname "${BASH_SOURCE[0]:-$0}")/scripts/provider_env.sh"
+source "$(dirname "${BASH_SOURCE[0]:-$0}")/scripts/preview_helper_dispatch.sh"
 SCHEDULER_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/scripts" && pwd)"
 source "${SCHEDULER_SCRIPTS_DIR}/run_config_env.sh"
 FACTORY_CORE_CLI="${FACTORY_CORE_CLI:-/opt/dark-factory/scripts/factory_core/cli.py}"
@@ -482,6 +483,12 @@ dispatch() {
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
@@ -1669,6 +1676,11 @@ echo "Backlog scheduler started (poll every ${POLL_INTERVAL}s)"
 
 while true; do
   DISPATCHED=""
+
+  # --- Preview-helper finalise/reap (#587): first in the poll. A helper counts toward WIP, so
+  # this must run before the capacity guard; it needs no board data, so it also runs before
+  # the rate-limit wait and the board fetch. Dispatches nothing, calls no tracker. ---
+  preview_helper_finalize
   BOT_RE=$(python3 "$FACTORY_CORE_CLI" markers-regex)
 
   # Guard against rate limit exhaustion (REST call, doesn't cost GraphQL points)
@@ -1766,6 +1778,12 @@ while true; do
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
```

**Conditions.** The stage returns at once unless `PREVIEW_HELPER_DISPATCH_ENABLED=true` and
`PREVIEW_HELPER_ENDPOINT` is set. It is called after every existing stage, only with no
session-window pause and nothing dispatched this poll; the capacity guard above it has already
ended the poll when no slot is free. One helper at a time. `config/config.yaml` gains a
documentation-only `preview_helper_dispatch:` block. No comment, issue body or label reaches
any of the lane's variables: all are read from the scheduler's environment only.

**Not `dispatch()`.** `dispatch_preview_helper` never calls `provider_env_flags` and passes no
`-e`. `PREVIEW_HELPER_ENDPOINT` is not added to `PROVIDER_PASSTHROUGH_VARS`. The instance-wide
`FACTORY_MODEL_ENDPOINT` is not used, so `cloud_endpoint_confirmed()` is unaffected (measured:
same result with the helper variables set, base and prototype).

**#634's contract.** Registry staged and digest exported by this function; base URL, served
name and limit taken from `model-endpoints show --from-source` (the scheduler's read-only
mount) under `.endpoints[]`, exactly one local entry with the id; proxy started with `up -d`
under its profile; proxy removed on every exit path by `preview_helper_finalize`; a proxy of
this project left over from before blocks the stage with a log line and no failure count.

**Readiness.** The proxy runs its registry checks before it binds and exits 1 if any fails
(measured). "Ready" is: the container exists and is still running after 3 s. An HTTP probe is
not used because it would write a ledger row under the run's pointer; the log banner is not
used because Python block-buffers it when stdout is not a terminal (measured). If the proxy is
slower than 3 s to bind, the CLI's own connection retries cover the gap.

**WIP.** The helper is started with `--name "${FACTORY_RUN_PREFIX}ph-<issue>"`.
`count_factory_running` and the run container's concurrency guard grep that prefix, so both
count it, unchanged. The proxy's name (`<project>-preview-helper-proxy-1`) does not match.
`is_issue_running` is not changed: the command carries `#<issue>`, so gated work on that issue
waits while the helper runs, bounded by the limit.

**Why the finalise step is first.** The helper counts toward WIP. At the default limit of 1
the capacity guard `continue`s the poll before any stage while a helper runs, so a reaper
inside the stage would never be reached and a hung helper would hold the only slot. It needs
no board data and calls no tracker, so it is placed at the top of the loop body, directly
after `DISPATCHED=""`: before `check_rate_limit` (which can sleep through a rate-limit window)
and before the board fetch (whose failure ends the poll early). A poll that ends early for
either reason has therefore already reaped. It dispatches nothing and returns at once when
there is no in-flight entry.

**In-flight state.** `${SCHEDULER_STATE_DIR}/preview-helper-inflight.json`, outside the
helper's mounts: run id, issue, head SHA, start epoch, limit. Written before anything is
started and removed last, so a scheduler crash at any point is finalised on the next poll.
"Exited" is "no running container with this name": `--rm` leaves nothing to inspect.

The file is on the shared volume, so nothing in it is trusted. It is read by `read-inflight`
with `--now` and `--max-total` set to the selected entry's own `timeouts.total_seconds` (3600
when the entry cannot be read). Not a small regular file, a field out of shape, **a start in
the future or a limit above the entry's** all give the same result: stop every container named
`<prefix>ph-<digits>`, remove the proxy, clear the artifact files and the entry, count one
failure, log `inflight_unreadable`. No run record is written on that path: there is no run id
to trust. Measured (second reader's B1): a forged entry naming a running container with a
start in the future and a limit of 99,999,999 s no longer holds the WIP slot.

**Wall clock, two enforcers, one value.** Inside the container (#642): every bounded call
under `timeout -k 5`. In the scheduler, every poll: a helper running longer than the limit,
counted from the moment its container was started, is stopped and removed, its proxy removed,
and the record is `abstained` / `wall_clock_exceeded`.

**The run-record write is bounded twice.** `record-outcome` reads the request ledger through
unchanged shared code that opens it plainly. First bound, in #642: a ledger file that is not
a regular file of at most 128 MiB is not read at all (`ledger_unavailable`; measured for a
FIFO, a link to `/dev/zero`, a directory, an oversized file and a bad rotation backup, each
in a second or less). Second bound, here: the verb runs under `timeout -k 5 60`, which
covers a file swapped between that check and the open; a timeout or any non-zero exit is
`record_write_failed`, counted as an abstention, with no row written.

**Stamp.** `preview_helper:stamp:<issue>:<sha>`, written through the existing
`breaker-set-retry` verb with the current epoch as its value, before the dispatch. Any value
means that SHA is not dispatched again, including after a failure. `expired-keys` lists stamps
whose value is an epoch older than 7 days (the pending file's own retention) and the stage
deletes them with `reset_retry`, so the keys do not accumulate. A stamp whose value is not an
integer is never listed: it can only suppress.

**Nothing but the lane's own keys is ever reset.** `reset_retry` is the adapter that clears an
issue's breaker key, and state keys are attacker-writable, so the key list is filtered twice:
`expired-keys` lists a key only when the whole key has one of the two shapes (#642), and the
stage checks every line again before the call:
`[[ "$key" =~ ^preview_helper:(stamp:[1-9][0-9]*:[0-9a-f]{40}|day:[0-9]{4}-[0-9]{2}-[0-9]{2})$ ]] || continue`.
Measured: with state keys such as `preview_helper:stamp:x\n123:implement` present, and
separately with `expired-keys` replaced by a function printing `123:implement` and
`preview_helper:failures`, the issue keys and the failure counter are untouched.

**The stamp is checked twice too.** The module reads at most 8 MiB of the state file and
treats a larger one as empty, which would offer a stamped SHA again. After `next-candidate`
the stage therefore asks the scheduler's own reader, `get_retry_count`, for the stamp key; a
non-zero value logs `skip reason=already_stamped`, clears the directory and dispatches
nothing. While the state file stays over 8 MiB that pending file is offered on every poll
and skipped every time, so the lane dispatches nothing and says so in the log; `sweep`
cannot see the stamp either and removes the file when it is 7 days old.

**The artifact directory.** `prepare-dir` (#642) runs before every dispatch: it requires the
directory to be a directory itself, sets its mode through the descriptor, and removes the
three artifact names whatever is planted there, so a directory planted at `input.json` or
`current-run.json` cannot leave the lane silently dead. Every way the stage can decline is
logged: `artifact_dir_not_a_directory`, `input_write_failed`, `inflight_write_failed`,
`already_stamped`, `stale_proxy`, `daily_max`, `failure_stop`. Only "no candidate" is
silent. `_ph_clear` is the module's `clear`; the file contains no `rm`, `chmod` or redirect
onto a state-volume path.

**Lane off.** With the lane off and no in-flight entry, `preview_helper_finalize` makes no
call at all: one `[ -e ]`/`[ -L ]` test (measured: 0 docker calls, 0 python calls, state
and `runs.jsonl` unchanged over three polls). With the lane off and an in-flight entry — a
real one after the operator switched the lane off, or a forged one — it finalises once:
stops a container named `<prefix>ph-<issue>` if one runs, removes this project's helper
proxy, writes one `runs.jsonl` row and adds one to `preview_helper:failures` (measured for a
forged entry: 3 docker calls, 5 python calls). The alternative considered was to do nothing
with the lane off except remove the container and proxy. It was not chosen: the switch is
the operator's stop, and a helper stopped by it must leave its `kill_switch` record like
every other dispatch; and the forged case gains nothing from the fuller path, because the
row and the counter are files the forger can already write directly, the docker calls can
only touch a helper container and this project's helper proxy, and the entry is removed so
it costs one cleanup per forged file. What was tightened instead: **with the lane off the
class is forced to `kill_switch` before any result or ledger row is read**, so nothing
forged while the lane is off can end `ok` and reset the failure counter (measured: forged
entry, forged `ok` result and forged `ok` ledger row give `abstained` / `kill_switch`, and
the counter goes from 1 to 2).

**Daily ceiling.** `preview_helper:day:<UTC date>`, incremented with `increment_retry` at each
dispatch attempt, whatever its outcome. When it has reached `PREVIEW_HELPER_DAILY_MAX`
(scheduler environment; default 20; a non-numeric value means the default; 0 dispatches
nothing) the stage logs `skip reason=daily_max` and returns. Day keys older than the retention
are deleted with the stamps. It is not an issue's key and not an existing breaker key.

**Failure counter.** `preview_helper:failures`: `increment_retry` on each `abstained`,
`reset_retry` on each `ok`. At 2 the stage logs `skip reason=failure_stop` and dispatches
nothing until an operator runs the existing `breaker-reset --key preview_helper:failures`.
Every class counts, **`input_too_large` included**: a changeset too large for one prompt is an
abstention like any other, and two in a row stop the lane. (An invalid pending file is not a
helper failure: it is deleted at intake and never dispatched on.) `reset_retry` also pops
`<key>:sig`, `:delivery`, `:loop:*`, `:cverdict`, `:cid`, `:crawlog`; none exists for these
keys. No issue-scoped key (`_make_key`) is read or written.

**Failure classes** (closed set):

| Decided by | Classes |
|---|---|
| entry script, via `result.json` | `preflight_refused` (any endpoint rule, evidence not `tested` included), `missing_input`, `input_too_large`, `local_timeout_total`, `model_call_failed`, `schema_invalid` |
| scheduler, before or instead of a result | `registry_stage_failed`, `endpoint_not_resolved`, `proxy_start_failed`, `dispatch_failed`, `wall_clock_exceeded`, `kill_switch` |
| `record-outcome`, reading the result and ledger | `helper_result_missing`, `helper_result_invalid`, `ledger_row_missing`, `ledger_unavailable`, `ledger_identity_mismatch`, `unclassified_failure` |
| the proxy's ledger rows | the members of `model_adapter.FAILURE_CLASSES` |
| scheduler, log line only (no row) | `record_write_failed` |

Never retried on cloud, never re-dispatched for the same SHA. No path calls the tracker or the
code host.

## Run record (requirement 7)

One row appended to the existing `runs.jsonl` by `record-outcome`, run by the scheduler after
the helper has gone:

```json
{"run_id": "<32 hex, chosen by the scheduler>", "issue_number": 587,
 "intent": "classify-preview-helper", "stage": "classify-preview-helper",
 "verdict": "ok", "origin": "factory", "head_sha": "<40 hex>",
 "outcome": "ok", "failure_class": null, "needs_preview": false, "category": "docs",
 "cost_usd": null,
 "model_dispatch": {"request": {"schema_version": "1.0", "payload_class": "local_only",
                                "endpoint_id": "<entry id>", "secret_reference": "FACTORY_HELPER_MODEL_SECRET",
                                "allow": true, "reason": null},
                    "result": {"schema_version": "1.0", "outcome": "ok", "endpoint_id": "<entry id>",
                               "hosting_location": "local", "role": "actor",
                               "correlation": "inferred_pointer", "request_count": 1,
                               "failure_classes_seen": []}},
 "detail": {"cli_preflight_rows": 1, "model_request_completed": true, "ledger_failure_classes": []},
 "timestamp": "2026-11-27T00:00:00Z"}
```

`cost_usd` is null, never 0. Then the scheduler removes `input.json`, `result.json`,
`current-run.json` and the in-flight entry (`_ph_clear`; if the artifact directory has been
replaced by a link, the link is removed and nothing is deleted through it).

## The changeset file (requirement 6)

**Flag.** `dispatch()` adds `-e PREVIEW_HELPER_CHANGESET_FLAG=true` to `Fix issue #N` and
`Continue issue #N` only, only while the lane is enabled (the `case` in the diff above).
Measured on nine dispatch commands with every passthrough credential set: argv and the compose
process's environment are byte-identical to the #634 end state with the switch unset, with the
switch alone, with the endpoint alone and with the switch `false`; with the lane enabled the
only difference is that flag on those two commands.

**Write.** In `push-and-pr`, directly after the `git ls-remote` push confirmation:

```bash
# >>> preview-helper changeset (#587) — shadow only. Written only when the scheduler
# passed the flag on this dispatch; in a subshell that can never fail this node.
if [ "${PREVIEW_HELPER_CHANGESET_FLAG:-}" = "true" ]; then
  (
    set +e
    PH_DIR=/var/lib/dark-factory/preview-pending
    PH_SHA=$(git rev-parse HEAD 2>/dev/null)
    printf '%s' "$PH_SHA" | grep -qE '^[0-9a-f]{40}$' || exit 0
    printf '%s' "$ISSUE" | grep -qE '^[1-9][0-9]*$' || exit 0
    mkdir -p "$PH_DIR" 2>/dev/null || exit 0
    PH_TMP=$(mktemp "$PH_DIR/.tmp.XXXXXX" 2>/dev/null) || exit 0
    git diff main...HEAD --name-only > "$PH_TMP.files" 2>/dev/null
    git diff main...HEAD --stat > "$PH_TMP.stat" 2>/dev/null
    if jq -n --argjson issue "$ISSUE" --arg sha "$PH_SHA" \
         --rawfile files "$PH_TMP.files" --rawfile stat "$PH_TMP.stat" \
         --slurpfile ij "$ARTIFACTS_DIR/issue.json" \
         '{issue_number: $issue, head_sha: $sha,
           changed_files: ($files | split("\n") | map(select(length > 0))),
           diff_stat: $stat,
           title: (($ij[0].title // "") | tostring),
           labels: [($ij[0].labels // [])[]
                    | if type == "object" then ((.name // "") | tostring) else tostring end]}' \
         > "$PH_TMP" 2>/dev/null; then
      mv -f "$PH_TMP" "$PH_DIR/${ISSUE}-${PH_SHA}.json" 2>/dev/null
    fi
    rm -f "$PH_TMP" "$PH_TMP.files" "$PH_TMP.stat" 2>/dev/null
  ) || true
fi
# <<< preview-helper changeset (#587)
```

A subshell with `set +e` and `|| true`: no failure in it can fail the node (measured under
`bash -euo pipefail`: unwritable directory, missing `issue.json`, non-numeric `ISSUE` all leave
the node at exit 0 with no file). The file is built with `jq` from files, not argv, and appears
under its final name only by rename; temporary names start with `.tmp.` and never match the
intake pattern. Without the flag nothing is written. `BENCH_MODE=stub` exits the node earlier.

**Intake and retention** are #642's `next-candidate` and `sweep`, called by the stage every
poll while the lane is enabled.

## Trust and forgery (requirement 9)

Stamps, the day count and the failure counter are in `scheduler-state.json`; pending files,
the in-flight file, `input.json`, `current-run.json`, `result.json`, the request ledger and
`runs.jsonl` are on the same volume. Every gated `dark-factory` run container mounts that
volume read-write; the helper mounts two sub-directories of it. **A compromised gated run can
forge, alter or delete any of them.** This spec does not claim otherwise.

What the design guarantees against such a run is narrower and is tested: the scheduler never
blocks on, never reads unboundedly, never writes through and never deletes through anything
planted on this path (FIFOs, devices, links, directories, oversized files, at every name the
lane reads or writes, temporary names included).

Under shadow-only a forging run can:

- suppress a helper dispatch (write a stamp, raise the failure counter to 2 or the day count
  to the ceiling);
- cause one for an issue and SHA of its choosing with content of its choosing (a well-formed
  forged pending file is not detectable). **The failure stop does not bound this**: a forged
  dispatch that ends `ok` resets the counter. **The daily ceiling bounds it only against a
  run that forges pending files and nothing else**: at most 20 dispatches per UTC day. A run
  that also rewrites `scheduler-state.json` can clear the day count, as it can any counter
  there. Against that run the remaining bounds are structural: one helper at a time, each
  holding one WIP slot for at most the entry's limit;
- end a running helper early, or make the scheduler stop a container named
  `<prefix>ph-<digits>`, which can only be a helper;
- falsify a result, a ledger row or a run record that nothing reads.

A forged in-flight file can no longer hold the WIP slot. That closes this lane's path only: a
gated run already has Docker API access through the socket proxy (`CONTAINERS=1`, `POST=1`)
and can hold a WIP slot today by starting any container whose name carries the run prefix.
This ticket neither adds nor removes that.

It cannot change a gate, merge, label, board state, or any issue's breaker or retry counter
through anything in this ticket, and it cannot make the helper hold a credential or reach
anything but the proxy.

Two plain reads on this path are existing behaviour and are not changed: the breaker verbs
read `scheduler-state.json` on every poll for every stage, and `run_record` reads the ledger.
The second is bounded by #642's pre-check and the process timeout above. The first is not
this lane's: a FIFO at `scheduler-state.json` blocks the existing `breaker-get`,
`breaker-incr` and `breaker-reset` today, lane or no lane (measured, V6).

Residual races, stated: the directory check-then-open race is closed in the module
(`dir_fd`). What remains is (1) the ledger: between #642's `lstat` and the shared reader's
open a file can be swapped, bounded by the 60 s timeout; (2) `record-outcome` opens
`preview-helper/result.json` by path with `O_NOFOLLOW` on the last component only, so a
directory swapped for a link at that instant makes it read a file named `result.json`
elsewhere — a bounded read, strictly parsed, of which only a boolean and one of six words
survive; (3) the shell's presence test on the in-flight entry, which reads nothing.

Attacker-influenced text (file names, title, labels) reaches a local model that has no tools.
The only thing taken from the model is a boolean and one of six words. No helper-produced or
model-produced string reaches a prompt, a comment, a label, a file name, or a log line: the
scheduler logs issue number, head SHA, run id, outcome and a class from the closed set.

## Activation (requirement 10)

Only inside #564's canary. The operator must:

1. run a host with Docker Engine 26 or later (`volume.subpath`);
2. place, on the human-only instance surface, a local registry entry with
   `secret_reference: FACTORY_HELPER_MODEL_SECRET` and its capability-evidence record from #562
   whose `invalidated_by` lists at least `model_digest`, `harness_version`, `registry_config`;
3. set `PREVIEW_HELPER_DISPATCH_ENABLED=true`, `PREVIEW_HELPER_ENDPOINT=<that entry's id>` and
   `FACTORY_HELPER_MODEL_SECRET` in the scheduler's environment; optionally
   `PREVIEW_HELPER_DAILY_MAX`.

Code enforces that the evidence resolves `tested` twice per dispatch: in the helper's
`endpoint-preflight` and in the proxy's own loader (#633, #634). No comment, issue body or
label can do any of the three steps. To stop: unset the switch (a running helper is stopped on
the next poll), or let two abstentions or the daily ceiling stop it.

## Every write on this path (requirement 11)

| Writer | What, where | Read by |
|---|---|---|
| gated implement run, only when flagged | `preview-pending/.tmp.XXXXXX`, renamed to `preview-pending/<issue>-<sha>.json` | scheduler |
| scheduler | `preview_helper:stamp:*`, `preview_helper:day:*`, `preview_helper:failures` in `scheduler-state.json` (existing breaker verbs) | scheduler |
| scheduler | `.preview-helper-inflight.json.tmp`, renamed to `preview-helper-inflight.json` | scheduler |
| scheduler | staged registry copy `model-registry/` (existing mechanism) | helper, helper proxy |
| scheduler | `preview-helper/.input.json.tmp` → `input.json`; `preview-helper/.current-run.json.tmp` → `current-run.json` | helper; helper proxy (pointer) |
| helper container | `preview-helper/.result.json.tmp` → `result.json`, nothing else | scheduler |
| helper proxy | rows in `request-ledger.jsonl`, Seq events (existing behaviour) | scheduler (record assembly); existing read-only rollups |
| scheduler | one row in `runs.jsonl` | existing read-only rollups (`reconcile_cost_reports.py` lists it as a stub row); #641 later |

Every scheduler temporary file is created exclusively without following a link, after its
name was unlinked; none is written with a shell redirect. The scheduler deletes: stamped or
aged entries in `preview-pending/` whose name matches the intake pattern or starts `.tmp.`;
the three artifact files; the in-flight entry. No comment, label, board move, branch, commit
or PR. Nothing in the table is read by a gate.

## Interface for #641

The run record in `runs.jsonl` (`intent: classify-preview-helper`, keyed by `issue_number` and
`head_sha`); the stamp key `preview_helper:stamp:<issue>:<sha>` (presence; it is deleted after
7 days); the validated six-field changeset (`input.json`'s shape); the counter key
`preview_helper:failures`.

## Files touched

| File | Change |
|---|---|
| `scripts/preview_helper_dispatch.sh` | new, sourced by `scheduler.sh` |
| `scheduler.sh` | one `source` line, the flag `case` in `dispatch()`, two call sites |
| `workflows/archon-dark-factory.yaml` | the marked block in `push-and-pr`; no other node |
| `config/config.yaml` | documentation-only `preview_helper_dispatch:` block (one leaf key) |
| `docs/configuration.md` | the four variables, the flag, the new config key in the Layer 1 tables, "110 leaf keys" → 111 (two places), activation under "Model endpoint registry" |
| `docs/run-lifecycle.md` | the finalise call at the top of the poll; the helper container does not run `entrypoint.sh` |
| `.github/workflows/ci.yml` | two `- run: bash tests/…` steps in `tests` |
| `tests/test_preview_helper_scheduler.sh` | new |
| `tests/test_preview_changeset_write.sh` | new |
| `tests/test_preview_helper_lane.py` | new |
| `.archon/memory/architecture.md` | two entries deleted, one corrected (already on this branch) |

Not touched: `run-compose.yml`, `scripts/preview_helper_entry.sh`,
`scripts/factory_core/preview_helper.py`, `providers/cli.py` (all #642's), `Dockerfile`,
`entrypoint.sh`, `scripts/factory_core/run_record.py`, `model_proxy.py`,
`providers/model_adapter.py`, `providers/model_endpoint.py`, `scripts/provider_env.sh`, any
`gate_*`, `breaker.py`, budgets, WIP or ceiling logic, `is_issue_running`, `deploy/**`,
`.github/workflows/publish.yml`.

## Blast radius

`scripts/gate_blast_radius.py` on the 11 paths above plus this spec:

```
STATUS: HUMAN_REQUIRED
GATE_TYPE: blast
FINDINGS_COUNT: 1
SEVERITY: critical
---
TRIGGER: migration-seed
TRIGGERED_FILES:
  - .github/workflows/ci.yml (category: migration-seed)
LINES_CHANGED: 2400
```

One trigger, `.github/workflows/ci.yml`. **The human hold for this ticket rests on that file
alone**: the same list without it gives `STATUS: PASS` (measured). `scheduler.sh` and the
workflow file are not triggers. The plan must therefore keep the two shell tests wired in
`ci.yml` and must not move them into an existing wired test to avoid the edit. **Whatever
the gate returns, the operator will require human approval of this ticket's PR**: it is the
ticket that first lets the scheduler start a local model's container, and that approval
does not depend on the gate's result.

## Existing tests this change breaks

Found by running the whole Python suite and all 56 `tests/test_*.sh` files on #642's tree and
on the whole prototype tree, in the CI-like environment. New failures of this ticket over
#642's:

1. `tests/test_docs_conformance_reference_docs.py::test_configuration_states_the_current_leaf_key_count`.
   The config block adds a leaf key. No test change: `docs/configuration.md` states 111 and
   documents the key (`scripts/docs_refs_check.py` also requires it in the Layer 1 tables;
   measured: it reports the key as undocumented on the prototype tree, which has no docs
   edit).
2. `tests/test_ci_covers_shell_tests.py::test_every_shell_test_is_wired_or_exempt`, from the
   moment the two new `tests/test_*.sh` files exist until their `ci.yml` steps do.

Measured: Python, the one new failure is 1; shell, no result changes between the two trees
(`bash -n` passes on the three files; both DAG checks pass).

## Test Plan

Hermetic throughout: no real endpoint or token, no container started; the scheduler tests
source the real `scheduler.sh` with `SCHEDULER_SOURCE_ONLY=1` and stub `docker` and the
providers CLI. *guard* marks a test that passes before the change. Names are binding.

`ci.yml`, `tests` job: `- run: bash tests/test_preview_helper_scheduler.sh`,
`- run: bash tests/test_preview_changeset_write.sh`. Both are new, separate files with their
own steps. Neither is added to `CI_EXEMPT_SHELL_TESTS`
(`tests/test_ci_covers_shell_tests.py`), and their cases are not folded into a shell test
that is already wired.

| # | Acceptance criterion | Test |
|---|---|---|
| 1 | (reworded, deviation 1) nothing from the passthrough list reaches the helper dispatch | `test_preview_helper_scheduler.sh` `helper_dispatch_argv_has_no_dash_e_and_never_calls_provider_env_flags` (every passthrough name set; recorded argv has no `-e`; the function body does not contain `provider_env_flags`) |
| 2 | switch or endpoint unset: nothing issued, dispatches byte-identical; `cloud_endpoint_confirmed()` unchanged | `test_preview_helper_scheduler.sh` `lane_off_issues_nothing`, `existing_dispatches_are_byte_identical_with_the_lane_off` (argv and compose-process environment of nine commands, for unset / switch only / endpoint only / switch false), `cloud_endpoint_confirmed_ignores_the_helper_variables` (*guard*) |
| 3 | lane on: only the flag on Fix and Continue | `test_preview_helper_scheduler.sh` `lane_on_adds_only_the_changeset_flag_to_fix_and_continue` |
| 4 | one test per scheduler failure class: `abstained`, a scheduler-written record, no tracker or code-host call, no issue-scoped key, no cloud request | `test_preview_helper_scheduler.sh` `every_scheduler_failure_class_ends_abstained_with_a_record` (registry stage, endpoint not resolved, proxy start, dispatch, wall clock, kill switch; the providers-CLI stub records zero `tracker`/`codehost` calls; `scheduler-state.json` gains only `preview_helper:` keys; the `docker` stub sees only this project's compose calls) |
| 5 | two consecutive abstentions stop; an `ok` between resets; `input_too_large` counts | `test_preview_helper_scheduler.sh` `two_abstentions_stop_the_stage_until_reset`, `an_ok_resets_the_count`, `input_too_large_counts_toward_the_stop` |
| 6 | no second dispatch for the same issue and SHA | `test_preview_helper_scheduler.sh` `a_stamped_sha_is_never_dispatched_again` (after `ok`, after `abstained`, and with a hand-written stamp value) |
| 6a | a stamped SHA is not dispatched when the state file is over the module's read cap | `test_preview_helper_scheduler.sh` `a_stamp_is_honoured_with_an_oversized_state_file` (9 MiB state file holding the stamp: `skip reason=already_stamped`, nothing dispatched, no record) |
| 7 | daily ceiling; expiry; only the lane's own keys are reset | `test_preview_helper_scheduler.sh` `the_daily_ceiling_stops_dispatch` (default, env override, non-numeric env, 0; the count rises on a failed attempt too), `expired_stamps_and_day_counts_are_deleted`, `expiry_never_resets_a_key_that_is_not_the_lanes` (forged state keys with a newline before an issue-shaped suffix: `123:implement`, `587:close`, `99:refine` keep their values; and, with the module verb replaced by a function printing `123:implement`, `preview_helper:failures` and a day key with a suffix, none of those is reset) |
| 8 | not during a pause, at capacity, or after another dispatch | `test_preview_helper_scheduler.sh` `stage_respects_pause_capacity_and_prior_dispatch` |
| 9 | name prefix; counted; proxy not | `test_preview_helper_scheduler.sh` `helper_name_counts_toward_wip_and_the_proxy_does_not` |
| 10 | over the limit: stopped, removed, proxy removed, `wall_clock_exceeded`; reaped at capacity | `test_preview_helper_scheduler.sh` `reaper_runs_at_capacity_and_records_wall_clock_exceeded`, `finalise_is_the_first_call_in_the_poll` (static: in `scheduler.sh`'s loop body the call precedes `check_rate_limit`, the board fetch and the capacity guard) |
| 11 | stale proxy: no dispatch, no count, a log line | `test_preview_helper_scheduler.sh` `a_stale_proxy_of_this_project_blocks_and_another_projects_does_not` |
| 12 | the in-flight file is not trusted | `test_preview_helper_scheduler.sh` `an_unreadable_inflight_entry_is_cleaned_up` (a FIFO, a directory, shell metacharacters in every field, a start in the future, a limit above the entry's; each: helper containers stopped, entry gone, one failure counted, no run record, no file created elsewhere, the step returns within 10 s) |
| 12a | lane off | `test_preview_helper_scheduler.sh` `lane_off_and_no_inflight_entry_makes_no_call` (three finalise calls: the `docker` and `python3` stubs record zero calls; state file and `runs.jsonl` byte-identical), `lane_off_finalises_an_entry_once_as_kill_switch` (a forged entry with a forged `ok` result and a forged `ok` ledger row: one row `abstained` / `kill_switch`, the failure counter rises and is not reset, the entry is gone, a second call makes no call) |
| 12b | planted directories do not stop the lane | `test_preview_helper_scheduler.sh` `a_directory_at_an_artifact_name_does_not_stop_the_lane` (directory trees at `input.json` and `current-run.json`: the dispatch proceeds), `every_declined_dispatch_is_logged` (each skip reason; only "no candidate" is silent) |
| 13 | the scheduler never writes or deletes through a planted link | `test_preview_helper_scheduler.sh` `planted_links_are_never_followed` (links at `.preview-helper-inflight.json.tmp`, `.current-run.json.tmp`, `.input.json.tmp` and the three final names: targets unchanged; the artifact directory replaced by a link: no dispatch, no stamp, and finalise removes the link only; `preview-pending` replaced by a link: nothing deleted behind it); the file contains no `>` redirect, `rm`, `chmod` or `mkdir` onto a state-volume path (static) |
| 14 | the run-record write is bounded | `test_preview_helper_scheduler.sh` `a_bad_ledger_ends_ledger_unavailable_at_once` (FIFO at the ledger path: `abstained` / `ledger_unavailable` within 5 s), `a_blocking_record_step_ends_record_write_failed` (the module verb replaced by a function that sleeps, bound set to 4 s: the step returns, one failure counted, the entry cleared) |
| 15 | no pending file without the flag; one valid file with it | `tests/test_preview_changeset_write.sh` `no_file_without_the_flag` (*guard*), `one_valid_file_with_the_flag` (accepted by `next-candidate`), `the_block_never_fails_the_node` |
| 16 | `preview-up` unaffected (deviation 4) | `tests/test_preview_helper_lane.py` `test_preview_up_and_classify_preview_do_not_reference_the_lane` (*guard*), `test_the_only_workflow_text_added_is_between_the_markers` |
| 17 | documentation | `tests/test_docs_conformance_reference_docs.py` (existing; 111 leaf keys), `scripts/docs_refs_check.py` (existing; the key in the Layer 1 tables) |

## Prototype evidence

Scripts and raw outputs are in the operator session's scratchpad `out587\` (`proto/` is the
tree; `proto_e2e.sh`, `proto_units.sh`, `proto_dispatch_diff.sh`, `attack2.sh`,
`stubdocker.sh`, `upstream.py` and their `.out` files) and the second reader's `out587-r2\`.

**End to end** (`proto_e2e.sh`): the real scheduler functions sourced through the real
`scheduler.sh`, real registry staging and CLI verbs, the real #634 proxy module, #642's entry
script and module, the pinned CLI, a stand-in local endpoint on loopback; `docker` stubbed to
start the proxy and the entry script as bare processes with exactly the environment the YAML
gives them. Polls follow the main loop's order (finalise, capacity guard, stage).

```
### 1. success
    state: failures=1 proxies=[] inflight=no helper_dir=[] pending=[587-1111111111 ]
    preview_helper=dispatched issue=#587 head_sha=111111… run_id=f85a35… limit=120s
    poll: dispatched=[Classify preview (helper) #587] running=1
    docker argv of the helper start: compose -f /opt/dark-factory/docker-compose.yml --profile preview-helper run --name dark-factory-dark-factory-run-ph-587 -d --rm preview-helper Classify\ preview\ \(helper\)\ #587 
    pointer: {"run_id":"f85a35…","issue_number":587,"intent":"classify-preview-helper","stage":"classify-preview-helper","role":"actor"}
    preview_helper=finished issue=#587 head_sha=111111… run_id=f85a35… outcome=ok failure_class=-
    (finalised after 2s of polling)
    record: {"issue_number":587,"head_sha":"111111","outcome":"ok","failure_class":null,"needs_preview":false,"category":"docs","cost_usd":null,"md":{"outcome":"ok","hosting_location":"local","role":"actor","correlation":"inferred_pointer","request_count":1,"failure_classes_seen":[]},"detail":{"cli…
    state: failures=0 proxies=[] inflight=no helper_dir=[] pending=[587-1111111111 ]
    upstream saw: {"model":"local-model-a","tools":[],"key":"real-local-secret-sentinel","output_config":"ABSENT"}
    stamp: 1795772389
    poll: dispatched=[] running=0
    state: failures=0 proxies=[] inflight=no helper_dir=[] pending=[]
    runs.jsonl rows: 1   (no second dispatch for the stamped SHA)
### 2. abstain: endpoint down
    preview_helper=dispatched issue=#588 head_sha=222222… run_id=965067… limit=25s
    poll: dispatched=[Classify preview (helper) #588] running=1
    preview_helper=finished issue=#588 head_sha=222222… run_id=965067… outcome=abstained failure_class=endpoint_unreachable
    (finalised after 11s of polling)
    record: {"issue_number":588,"head_sha":"222222","outcome":"abstained","failure_class":"endpoint_unreachable","needs_preview":true,"category":null,"cost_usd":null,"md":{"outcome":"failed","hosting_location":"local","role":"actor","correlation":"inferred_pointer","request_count":5,"failure_classe…
    state: failures=1 proxies=[] inflight=no helper_dir=[] pending=[588-2222222222 ]
### 3a. timeout: stalled endpoint, in-container enforcer (limit 8 s)
    preview_helper=dispatched issue=#589 head_sha=333333… run_id=f67a92… limit=8s
    poll: dispatched=[Classify preview (helper) #589] running=1
    preview_helper=finished issue=#589 head_sha=333333… run_id=f67a92… outcome=abstained failure_class=local_timeout_total
    (finalised after 3s of polling)
    wall: 15s
    record: {"issue_number":589,"head_sha":"333333","outcome":"abstained","failure_class":"local_timeout_total","needs_preview":true,"category":null,"cost_usd":null,"md":{"outcome":"unknown","hosting_location":null,"role":null,"correlation":"unknown","request_count":0,"failure_classes_seen":[]},"de…
    state: failures=1 proxies=[] inflight=no helper_dir=[] pending=[589-3333333333 ]
### 3b + 6. timeout: hung helper at WIP capacity, scheduler reaper (limit 6 s)
    preview_helper=dispatched issue=#590 head_sha=444444… run_id=e0e1f5… limit=6s
    poll: dispatched=[Classify preview (helper) #590] running=1
   t+2s
    poll: skip=factory_at_capacity running=1/1
   t+4s
    poll: skip=factory_at_capacity running=1/1
   t+6s
    preview_helper=finished issue=#590 head_sha=444444… run_id=e0e1f5… outcome=abstained failure_class=wall_clock_exceeded
    poll: dispatched=[] running=0
   t+8s
    poll: dispatched=[] running=0
    record: {"issue_number":590,"head_sha":"444444","outcome":"abstained","failure_class":"wall_clock_exceeded","needs_preview":true,"category":null,"cost_usd":null,"md":{"outcome":"unknown","hosting_location":null,"role":null,"correlation":"unknown","request_count":0,"failure_classes_seen":[]},"de…
    state: failures=1 proxies=[] inflight=no helper_dir=[] pending=[]
### failure stop: two consecutive abstentions, then an operator reset
    preview_helper=dispatched issue=#591 head_sha=555555… run_id=826fee… limit=6s
    poll: dispatched=[Classify preview (helper) #591] running=1
    preview_helper=finished issue=#591 head_sha=555555… run_id=826fee… outcome=abstained failure_class=local_timeout_total
    (finalised after 2s of polling)
    state: failures=2 proxies=[] inflight=no helper_dir=[] pending=[591-5555555555 ]
    preview_helper=skip reason=failure_stop key=preview_helper:failures
    poll: dispatched=[] running=0
    preview_helper=dispatched issue=#592 head_sha=666666… run_id=7e9785… limit=120s
    poll: dispatched=[Classify preview (helper) #592] running=1
    preview_helper=finished issue=#592 head_sha=666666… run_id=7e9785… outcome=ok failure_class=-
    (finalised after 2s of polling)
    record: {"issue_number":592,"head_sha":"666666","outcome":"ok","failure_class":null,"needs_preview":false,"category":"docs","cost_usd":null,"md":{"outcome":"ok","hosting_location":"local","role":"actor","correlation":"inferred_pointer","request_count":1,"failure_classes_seen":[]},"detail":{"cli…
    state: failures=0 proxies=[] inflight=no helper_dir=[] pending=[592-6666666666 ]
### 4. stale proxy (this project) and a proxy of another project
    preview_helper=dispatched issue=#593 head_sha=111111… run_id=456ef6… limit=120s
    poll: dispatched=[Classify preview (helper) #593] running=1
    preview_helper=finished issue=#593 head_sha=111111… run_id=456ef6… outcome=ok failure_class=-
    (finalised after 2s of polling)
    record: {"issue_number":593,"head_sha":"111111","outcome":"ok","failure_class":nu
    preview_helper=skip reason=stale_proxy
    poll: dispatched=[] running=0
    state: failures=0 proxies=[pxstale ] inflight=no helper_dir=[] pending=[594-2222222222 ]
    stamp for #594: 0   (0 = not stamped, retried next poll)
    preview_helper=dispatched issue=#594 head_sha=222222… run_id=d8a22b… limit=120s
    poll: dispatched=[Classify preview (helper) #594] running=1
    preview_helper=finished issue=#594 head_sha=222222… run_id=d8a22b… outcome=ok failure_class=-
    (finalised after 2s of polling)
    record: {"issue_number":594,"head_sha":"222222","outcome":"ok","failure_class":nu
### 5. hostile changeset files
    before: 13 entries
preview_helper=invalid_pending issue=11 action=delete
preview_helper=invalid_pending issue=8 action=delete
preview_helper=invalid_pending issue=9 action=delete
preview_helper=invalid_pending issue=12 action=delete
preview_helper=invalid_pending issue=13 action=delete
preview_helper=invalid_pending issue=14 action=delete
preview_helper=invalid_pending issue=15 action=delete
    preview_helper=dispatched issue=#16 head_sha=111111… run_id=80452d… limit=120s
    poll: dispatched=[Classify preview (helper) #16] running=1
    input.json title round-trips: yes; keys: ["changed_files","diff_stat","head_sha","issue_number","labels","title"]
    preview_helper=finished issue=#16 head_sha=111111… run_id=80452d… outcome=ok failure_class=-
    (finalised after 2s of polling)
    record: {"issue_number":16,"head_sha":"111111","outcome":"ok","failure_class":null,"needs_preview":false,"categ
    after: ..7-….json 07-….json 16-….json 7-….json 7-….json.bak 7-…g.json 
    /tmp/pwned exists: no
    state: failures=0 proxies=[] inflight=no helper_dir=[] pending=[..7-1111111111 07-11111111111 7-111111111111 7-111111111111 7-111111111111 ]
### 7. proxy refuses to start (secret missing) and kill switch
    preview_helper=finished issue=#600 head_sha=555555… run_id=bcc511… outcome=abstained failure_class=proxy_start_failed
    poll: dispatched=[] running=0
    record: {"issue_number":600,"head_sha":"555555","outcome":"abstained","failure_class":"proxy_start_failed","nee
    state: failures=1 proxies=[] inflight=no helper_dir=[] pending=[..7-1111111111 600-5555555555 ]
    preview_helper=dispatched issue=#601 head_sha=666666… run_id=defe88… limit=120s
    poll: dispatched=[Classify preview (helper) #601] running=1
    preview_helper=finished issue=#601 head_sha=666666… run_id=defe88… outcome=abstained failure_class=kill_switch
    poll: dispatched=[] running=0
    record: {"issue_number":601,"head_sha":"666666","outcome":"abstained","failure_class":"kill_switch","needs_prev
    state: failures=1 proxies=[] inflight=no helper_dir=[] pending=[..7-1111111111 601-6666666666 ]
### 8. daily ceiling
    dispatches counted today: 11
    preview_helper=skip reason=daily_max count=3
    poll: dispatched=[] running=0
    state: failures=0 proxies=[] inflight=no helper_dir=[] pending=[..7-1111111111 602-1111111111 ]
### argv check over the whole run
    compose invocations carrying -e: 0
    helper names: ph-16 ph-587 ph-588 ph-589 ph-590 ph-591 ph-592 ph-593 ph-594 ph-601 
```

**The changeset block** (`proto_units.sh`):

```
### B. the push-and-pr block (extracted from the workflow YAML between its markers)
  without the flag: rc=0 files=0
  with the flag: rc=0 entries=[42-<sha>.json ]
{"issue_number":42,"head_sha":"55ab755c0cc8cb860fcf610c88f1005198bf0381","changed_files":["docs/b.md","we ird $(x).md"],"diff_stat":" docs/b.md      | 1 +\n we ird $(x).md | 1 +\n 2 files c…
42 55ab755c0cc8cb860fcf610c88f1005198bf0381
  scheduler validation of that file: rc=0
  pending dir not writable: rc=0 (node continues) files=0
  non-numeric ISSUE: rc=0 files=0
  issue.json missing: rc=0 entries=[] pwned2=no
```

**Existing dispatches** (`proto_dispatch_diff.sh`, nine commands, every passthrough credential
set):

```
  [unset] 9 dispatches: argv and compose-process environment byte-identical (12045 bytes)
  [switch_only] 9 dispatches: argv and compose-process environment byte-identical (12396 bytes)
  [endpoint_only] 9 dispatches: argv and compose-process environment byte-identical (12414 bytes)
  [switch_false] 9 dispatches: argv and compose-process environment byte-identical (12747 bytes)
  [lane enabled] differences, base -> new:
< ARGV: […] ] [-e] [GITLAB_TOKEN] [-e] [AWS_ACCESS_KEY_ID] [-e] [AWS_SECRET_ACCESS_KEY] [-e] [AWS_SESSION_TOKEN] [-e] [GOOGLE_CLOUD_PROJECT] [-d] [--rm] [dark-factory] [Fix issue #5]
> ARGV: […] _ID] [-e] [AWS_SECRET_ACCESS_KEY] [-e] [AWS_SESSION_TOKEN] [-e] [GOOGLE_CLOUD_PROJECT] [-e] [PREVIEW_HELPER_CHANGESET_FLAG=true] [-d] [--rm] [dark-factory] [Fix issue #5]
< ARGV: […] ] [GITLAB_TOKEN] [-e] [AWS_ACCESS_KEY_ID] [-e] [AWS_SECRET_ACCESS_KEY] [-e] [AWS_SESSION_TOKEN] [-e] [GOOGLE_CLOUD_PROJECT] [-d] [--rm] [dark-factory] [Continue issue #5]
> ARGV: […] [-e] [AWS_SECRET_ACCESS_KEY] [-e] [AWS_SESSION_TOKEN] [-e] [GOOGLE_CLOUD_PROJECT] [-e] [PREVIEW_HELPER_CHANGESET_FLAG=true] [-d] [--rm] [dark-factory] [Continue issue #5]
  helper variables on any existing dispatch argv: 0
  cloud_endpoint_confirmed with the helper variables set: base=0 new=0
```

**Second reader's attacks on the scheduler functions, rerun after the fixes**
(`out587-r2\attack.sh`; before: B1 held the WIP slot, B4 overwrote both victims):

```
### B. scheduler functions (real scheduler.sh sourced; docker stubbed)
    functions/vars defined before sourcing the new file that it redefines:
      (none listed = no collision)
    sourcing the new file alone under set -euo pipefail with nothing defined:
      rc=0 ; options: ehuB
  B1. lane OFF, forged in-flight file naming a 'running' container, started in the future
[2026-11-27T09:39:52Z] preview_helper=inflight_unreadable action=cleanup
    inflight=no failures=1 runs=5
      helper 'container' still counted toward WIP: 0  (lane blocked while it lives: no)
    inflight=no failures=1 runs=5
  B2. in-flight file with shell metacharacters / path traversal in every field
      [2026-11-27T09:39:55Z] preview_helper=inflight_unreadable action=cleanup
    inflight=no failures=2 runs=0
      pwn files: 0
  B3. in-flight file reusing another (gated) run's id
      [2026-11-27T09:39:59Z] preview_helper=finished issue=#99 head_sha=111111… run_id=dddddd… outcome=abstained failure_class=ledger_identity_mismatch
      {"run_id":"dddddd…","issue_number":99,"outcome":"abstained","failure_class":"ledger_identity_mismatch"}
  B4. symlink planted at the in-flight temp name and the pointer temp name
      victim2: ORIGINAL
      victim3: {
  "a": 1
}
  B5. stamp flood: size of scheduler-state.json after 2,000 forged SHAs are stamped
      144890 bytes
### B1 redo: forged in-flight file, 'running' container, start in the future, huge limit
[2026-11-27T09:16:58Z] preview_helper=inflight_unreadable action=cleanup
  after 3 finalise calls: inflight=gone count_factory_running=0 factory_at_capacity=no
  preview_helper_check with that file present: rc=0 DISPATCHED=[]
```

**Further cases** (`attack2.sh`; in C10 the FIFO ledger is no longer read and the step returns at once
instead of at the timeout):

```
### C. scheduler functions (real scheduler.sh sourced; docker stubbed)
  C7. expired keys leave the state file when the stage runs
    keys now: [587 preview_helper:day:<today> preview_helper:failures preview_helper:stamp:1:aaaa preview_helper:stamp:2:bbbb preview_helper:stamp:3:cccc ]
  C8. in-flight entry is a FIFO, then a directory
      preview_helper=inflight_unreadable action=cleanup
    took 2s
    in-flight entry: gone failures=1 runs.jsonl rows=0
      preview_helper=inflight_unreadable action=cleanup
    in-flight entry: gone failures=1 runs.jsonl rows=0
  C9. artifact directory replaced by a link
      preview_helper=skip reason=artifact_dir_not_a_directory
    DISPATCHED=[] stamp written: false
      preview_helper=finished issue=#55 head_sha=111111… run_id=… outcome=abstained failure_class=dispatch_failed
    /tmp/away/result.json: KEEP ; link removed: yes
  C10. request ledger is a FIFO: the record write is bounded
      preview_helper=finished issue=#55 head_sha=111111… run_id=… outcome=abstained failure_class=helper_result_missing
    took 2s
    in-flight entry: gone failures=1 runs.jsonl rows=1
```

**Confirming read's lane-off case, rerun** (`out587-r2\attack3.sh`, V7):

```
### V7. lane OFF: what the finalise call at the top of the loop costs
  no in-flight file, lane off, 3 calls: docker calls=0 python calls=0 state={} runs.jsonl rows=0
  forged valid in-flight file, lane off: docker calls=3 python calls=5 state keys=["preview_helper:failures"] runs.jsonl rows=1
```

**Key filter, oversized state file, planted directories, lane off** (`attack4.sh`):

```
### D. scheduler functions (real scheduler.sh sourced; docker stubbed)
  D4. forged state keys (the reader's V4 file): what the stage resets
    keys left: ["123:implement","587:close","99:refine","preview_helper:day:2020-01-01\\n99:refine","preview_helper:stamp:\\n587:close","preview_helper:stamp:x\\n123:implement"]
  D5. the shell guard alone: expired-keys made to print hostile lines
    keys left: ["123:implement","preview_helper:day:2020-01-01:implement","preview_helper:failures"]
  D6. state file larger than the module's read cap, SHA already stamped (the reader's V5 file)
      preview_helper=skip reason=already_stamped issue=#7
    DISPATCHED=[] input.json left: no runs.jsonl rows=0
  D7. directories planted at input.json and current-run.json: the lane goes on
      preview_helper=finished issue=#7 head_sha=… run_id=… outcome=abstained failure_class=registry_stage_failed
    stamp written: true (no registry in this container, so the attempt ends at staging)
  D8. lane OFF, forged in-flight entry with a forged ok result and a forged ok ledger row
      preview_helper=finished issue=#55 head_sha=… run_id=… outcome=abstained failure_class=kill_switch
    failures=2 (was 1; an ok would have reset it to 0) row: ["abstained","kill_switch"]
  D9. the same entry with the lane ON and no container: the forged result is read
      preview_helper=finished issue=#55 head_sha=… run_id=… outcome=ok failure_class=-
```

## Not verified

- Anything against a real endpoint or model (#562's capability evidence).
- The real `docker compose up/run/rm`, `docker stop` and `docker ps` calls, service-name
  resolution on `preview-helper-net` and the subpath mounts: `docker` was a stub.
- The 3 s settle time against a real container start.
- That `PREVIEW_HELPER_CHANGESET_FLAG` set with `run -e` reaches the `push-and-pr` node's
  shell through Archon (other nodes read `BENCH_MODE` from the environment the same way).
- The main loop itself was not run: the prototype's `poll` function repeats its call order.
  The placement of `preview_helper_finalize` in `scheduler.sh` is checked by reading the diff.
- The second bound on the run-record write (the 60 s process timeout) was last measured
  before #642's ledger pre-check existed; with the pre-check a planted FIFO no longer
  reaches it, so it was not re-triggered. The named test triggers it with a sleeping stub.
- The tests in the Test Plan do not exist yet; the prototype scripts cover the same behaviour
  but are not the tests.

## Size

About 300 lines of product code (one sourced file, 18 lines in `scheduler.sh`, 30 in the
workflow, 5 in config) and three new test files: size M.
