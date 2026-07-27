#!/usr/bin/env bash
# Sourceable predicate library for scheduler.sh — pure, side-effect-free item-blob
# predicates only (mirrors scripts/gate_lib.sh's shape). Do NOT add dispatch()/
# set_board_status()/gh-mutating logic here; spec_advance_check/plan_advance_check/
# end_gate_check stay in scheduler.sh because they dispatch and mutate board state.
# Do NOT add set -euo pipefail: this file is sourced and must not alter caller shell options.

SCHEDULER_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

has_refine_skip_label() {
  local item="$1"
  local labels
  labels=$(echo "$item" | jq -r '.labels[]?' 2>/dev/null)
  IFS=',' read -ra SKIP_ARRAY <<< "$REFINE_SKIP_LABELS"
  for skip in "${SKIP_ARRAY[@]}"; do
    if echo "$labels" | grep -qi "$skip"; then
      return 0
    fi
  done
  return 1
}

has_opt_in_refine_label() {
  local item="$1"
  local labels
  labels=$(echo "$item" | jq -r '.labels[]?' 2>/dev/null)
  echo "$labels" | grep -qi "ready-for-agent"
}

has_direct_to_pr_label() {
  local item="$1"
  echo "$item" | jq -r '.labels[]?' 2>/dev/null | grep -qi "$DIRECT_TO_PR_LABEL"
}

# --- Dispatch ceiling classification (#339) ---
# Returns "S", "M", "L", or "" from the item's labels
get_size_label() {
  echo "$1" | jq -r '.labels[]?' 2>/dev/null | grep -oiE 'size: ?(xl|[sml])' | awk '{print toupper($NF)}' | head -1
}

# True (returns 0) if item is above the dispatch ceiling: size XL always, or size M
# when the title matches an ABOVE_CEILING_KEYWORDS pattern (escalation only — the
# keyword heuristic never demotes).
is_above_ceiling() {
  local item="$1" title size
  title=$(echo "$item" | jq -r '.content.title // ""' 2>/dev/null)
  size=$(get_size_label "$item")
  case "$size" in
    XL) return 0 ;;
    M) echo "$title" | grep -qiE "${ABOVE_CEILING_KEYWORDS}" && return 0 || return 1 ;;
    *) return 1 ;;
  esac
}

# True if item already carries the above-ceiling label (board-fetch snapshot)
has_above_ceiling_label() {
  echo "$1" | jq -r '.labels[]?' 2>/dev/null | grep -qi "^${ABOVE_CEILING_LABEL}$"
}

# True if item is S- or L-size, or has no size label (unlabelled is treated as S per spec)
is_below_ceiling() {
  local size
  size=$(get_size_label "$1")
  case "$size" in S|L|"") return 0 ;; *) return 1 ;; esac
}

# Returns minutes elapsed since the last comment matching $marker_re on the given issue.
# Returns "" if no matching comment exists or if the timestamp cannot be parsed.
elapsed_minutes_since_marker() {
  local issue_num="$1"
  local marker_re="$2"
  local comments
  comments=$(python3 "$FACTORY_PROVIDERS_CLI" tracker get-comments --id "$issue_num" 2>/dev/null) \
    || { echo ""; return; }
  local created_at
  created_at=$(echo "$comments" | jq -r --arg m "$marker_re" \
    '[.[] | select(.body | test($m))] | last | .createdAt // ""')
  [ -z "$created_at" ] && { echo ""; return; }
  local marker_epoch now_epoch
  marker_epoch=$(date -u -d "$created_at" +%s 2>/dev/null) || { echo ""; return; }
  now_epoch=$(date -u +%s)
  echo $(( (now_epoch - marker_epoch) / 60 ))
}

has_new_comment_after_report() {
  local issue_num="$1"
  local report_marker="$2"
  local comments
  comments=$(python3 "$FACTORY_PROVIDERS_CLI" tracker get-comments --id "$issue_num" 2>/dev/null) \
    || { echo "no"; return; }

  # A comment counts as reviewer feedback only if it appears AFTER the last spec report
  # AND is not one of our own automated comments. The dark factory posts its cost report
  # after the spec on the success path (entrypoint.sh post_cost_report), and the scheduler
  # posts pipeline-status comments — none are feedback, so re-running the spec on them
  # loops the pipeline (issue #124: cost report -> spurious second spec). Match on
  # footer/marker, NOT author: every comment is authored by the same PAT account.
  # BOT_RE is computed once per poll cycle (see the `while true` loop top) via
  # `factory_core/cli.py markers-regex`, sourced from identity.detection_patterns() — not
  # hand-listed here, so it can't drift from identity.py (#181).
  local has_human
  has_human=$(echo "$comments" | jq --arg marker "$report_marker" --arg bot "$BOT_RE" '
    (to_entries | map(select(.value.body | test($marker))) | last | .key // -1) as $ridx
    | if $ridx == -1 then false
      else (to_entries | any(.key > $ridx and (.value.body | test($bot) | not)))
      end')

  if [ "$has_human" = "true" ]; then echo "yes"; else echo "no"; fi
}
