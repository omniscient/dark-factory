#!/usr/bin/env bash
# Shared helpers for the operator skill scripts. Source it; do not run it.
#
# Every value can be overridden from the environment so the same scripts drive the
# MarketHawk instance (DF_REPO=omniscient/markethawk DF_PROJECT_NUM=1
# DF_SCHEDULER=dark-factory-scheduler DF_RUN_PREFIX=dark-factory-dark-factory-run-).
#
# Windows/Git Bash: MSYS_NO_PATHCONV stops the shell mangling /opt/... and /var/... paths
# passed to docker exec (each mangled path cost a run once).
export MSYS_NO_PATHCONV=1

: "${DF_REPO:=omniscient/dark-factory}"
: "${DF_PROJECT_OWNER:=omniscient}"
: "${DF_PROJECT_NUM:=2}"
: "${DF_SCHEDULER:=dark-factory-self-scheduler}"
: "${DF_RUN_PREFIX:=dark-factory-self-dark-factory-run-}"
: "${DF_IMAGE:=ghcr.io/omniscient/dark-factory:latest}"
: "${DF_STATE_DIR:=/var/lib/dark-factory}"
: "${DF_ALL_SCHEDULERS:=dark-factory-self-scheduler dark-factory-scheduler}"
# Windows python cannot open /c/... MSYS paths; prefer the native profile path.
: "${DF_CREDENTIALS:=${USERPROFILE:-$HOME}/.claude/.credentials.json}"

# Repo checkout that contains this skill (…/.claude/skills/operator/scripts → repo root).
# Mixed form (C:/git/...) because MSYS_NO_PATHCONV stops Git Bash translating /c/... for git.
DF_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
command -v cygpath >/dev/null 2>&1 && DF_SCRIPT_DIR="$(cygpath -m "$DF_SCRIPT_DIR")"
if [ -z "${DF_REPO_DIR:-}" ]; then
  DF_REPO_DIR="$(cd "$DF_SCRIPT_DIR/../../../.." && pwd)"
  command -v cygpath >/dev/null 2>&1 && DF_REPO_DIR="$(cygpath -m "$DF_REPO_DIR")"
fi

# Labels that stop dispatch. Mirror scheduler.sh SKIP_LABELS / REFINE_SKIP_LABELS.
SKIP_LABELS="needs-discussion epic ready-for-human"
REFINE_SKIP_LABELS="needs-discussion epic spec-pending-review plan-pending-review"
GATE_LABELS="spec-pending-review plan-pending-review needs-discussion above-ceiling"

log()  { printf '%s %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing tool: $1"; }
need gh; need jq; need docker; need python
# jq.exe on Windows writes CRLF; a trailing \r makes numbers never match and would smuggle
# a \r into GraphQL mutation ids. gh --jq (Go) is fine. Strip it once here for every caller.
jq() { command jq "$@" | tr -d '\r'; }

# ---- project board ---------------------------------------------------------------------
_fields_json() {
  if [ -z "${_DF_FIELDS:-}" ]; then
    _DF_FIELDS=$(gh project field-list "$DF_PROJECT_NUM" --owner "$DF_PROJECT_OWNER" --format json) || return 1
  fi
  printf '%s' "$_DF_FIELDS"
}
project_id() {
  if [ -z "${_DF_PID:-}" ]; then
    _DF_PID=$(gh project view "$DF_PROJECT_NUM" --owner "$DF_PROJECT_OWNER" --format json --jq .id) || return 1
  fi
  printf '%s' "$_DF_PID"
}
status_field_id() { _fields_json | jq -r '.fields[] | select(.name=="Status") | .id'; }
status_option_id() {
  local id
  id=$(_fields_json | jq -r --arg n "$1" '.fields[] | select(.name=="Status") | .options[] | select(.name==$n) | .id')
  [ -n "$id" ] || die "unknown board status '$1' (use the exact option name, e.g. 'In Review')"
  printf '%s' "$id"
}
# Whole board as JSON (paged by gh). ~11 GraphQL points per 100 items; do not poll this.
board_items_json() {
  gh project item-list "$DF_PROJECT_NUM" --owner "$DF_PROJECT_OWNER" --format json --limit 500
}
item_id_for_issue() {
  board_items_json | jq -r --argjson n "$1" '.items[] | select(.content.number==$n) | .id' | head -1
}
issue_node_id() { gh issue view "$1" --repo "$DF_REPO" --json id --jq .id; }
issue_board_status() {
  gh issue view "$1" --repo "$DF_REPO" --json projectItems --jq '[.projectItems[].status.name] | join("/")'
}
issue_labels() { gh issue view "$1" --repo "$DF_REPO" --json labels --jq '[.labels[].name] | join(",")'; }
issue_state()  { gh issue view "$1" --repo "$DF_REPO" --json state --jq .state; }
has_label()    { case ",$(issue_labels "$1")," in *",$2,"*) return 0;; *) return 1;; esac; }

# Set the Status field of an issue's board item. Prints the item id on success.
set_board_status() {
  local issue=$1 status=$2 item opt
  item=$(item_id_for_issue "$issue"); [ -n "$item" ] || die "#$issue is not on board $DF_PROJECT_NUM (use board.sh add $issue)"
  opt=$(status_option_id "$status") || exit 1
  gh api graphql -f query="mutation { updateProjectV2ItemFieldValue(input: {projectId: \"$(project_id)\", itemId: \"$item\", fieldId: \"$(status_field_id)\", value: {singleSelectOptionId: \"$opt\"}}) { projectV2Item { id } } }" \
    --jq '.data.updateProjectV2ItemFieldValue.projectV2Item.id'
}

# `gh issue list` returns exit 0 with an empty array on transient hiccups, so a single
# empty result is not evidence of absence. Re-query once and label disagreement.
read_label() {
  local L=$1 a b
  a=$(gh issue list --repo "$DF_REPO" --state open --label "$L" --limit 200 --json number --jq '[.[].number] | join(",")') || return 1
  if [ -z "$a" ]; then
    sleep 3
    b=$(gh issue list --repo "$DF_REPO" --state open --label "$L" --limit 200 --json number --jq '[.[].number] | join(",")') || return 1
    if [ -n "$b" ]; then printf 'UNCONFIRMED (empty then %s)' "$b"; else printf '(none, confirmed twice)'; fi
    return 0
  fi
  printf '%s' "$a"
}

# Parse `Depends on: #N` lines (plain/bold/italic/multi-ref) from an issue body.
issue_deps() {
  gh issue view "$1" --repo "$DF_REPO" --json body --jq .body \
    | grep -iE '^\s*[*_]*depends on[*_]*:' | grep -oE '#[0-9]+' | tr -d '#' | sort -un
}

# ---- docker ----------------------------------------------------------------------------
run_containers() { docker ps --filter "name=${DF_RUN_PREFIX}" --format '{{.Names}}' 2>/dev/null; }
# The run container's command is the phase text, e.g. "Fix issue #402".
container_command() { docker inspect "$1" --format '{{join .Config.Cmd " "}}' 2>/dev/null; }
issue_run_container() {
  local c
  for c in $(run_containers); do
    case "$(container_command "$c")" in *"#$1"|*"#$1 "*) echo "$c";; esac
  done
}
scheduler_paused() { [ "$(docker inspect "$1" --format '{{.State.Paused}}' 2>/dev/null)" = "true" ]; }
# Factory-token window state from the scheduler's own sentinel (authoritative; the host
# usage endpoint is a different token). Prints PAUSED until <time> | active | UNKNOWN.
window_state() {
  local ts
  if scheduler_paused "$DF_SCHEDULER"; then echo "UNKNOWN (scheduler container is docker-paused)"; return; fi
  if ts=$(docker exec "$DF_SCHEDULER" cat "$DF_STATE_DIR/session-window-paused" 2>/dev/null); then
    if [ "$ts" -eq "$ts" ] 2>/dev/null; then echo "PAUSED until $(date -u -d @"$ts" +%FT%TZ 2>/dev/null || echo "$ts")"; else echo "PAUSED ($ts)"; fi
  elif docker exec "$DF_SCHEDULER" true 2>/dev/null; then echo active
  else echo "UNKNOWN (scheduler unreachable)"; fi
}

# ---- claude usage (host token; see GOTCHAS: NOT the factory token's window) ------------
host_usage() {
  local tok
  tok=$(python -c "import json,sys;print(json.load(open(sys.argv[1]))['claudeAiOauth']['accessToken'])" "$DF_CREDENTIALS" 2>/dev/null) || { echo "usage: credentials unreadable"; return 1; }
  curl -s https://api.anthropic.com/api/oauth/usage -H "Authorization: Bearer $tok" -H "anthropic-beta: oauth-2025-04-20" \
    | python -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('usage: bad response'); sys.exit(0)
for k in ('five_hour','seven_day'):
    v=d.get(k) or {}
    print(f\"{k}: {v.get('utilization')}% resets {v.get('resets_at','?')[:16]}\")
"
}
