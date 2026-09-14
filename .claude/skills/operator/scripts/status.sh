#!/usr/bin/env bash
# One-shot factory snapshot. Read-only. Run at session start and before any decision.
#   bash .claude/skills/operator/scripts/status.sh [--quick]
# --quick skips the board dump and dispatchability analysis (cheap on GraphQL).
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
QUICK=0; [ "${1:-}" = "--quick" ] && QUICK=1

echo "=== $(date -u +%FT%TZ)  repo=$DF_REPO board=$DF_PROJECT_NUM scheduler=$DF_SCHEDULER"

echo; echo "--- windows"
host_usage | sed 's/^/host  /'
echo "factory window: $(window_state)"

echo; echo "--- containers"
for s in $DF_ALL_SCHEDULERS; do
  st=$(docker inspect "$s" --format '{{.State.Status}} paused={{.State.Paused}} since={{.State.StartedAt}}' 2>/dev/null || echo "absent")
  echo "$s: $st"
done
rc=$(run_containers)
if [ -n "$rc" ]; then for c in $rc; do echo "RUN $c  cmd='$(container_command "$c")'  up=$(docker inspect "$c" --format '{{.State.StartedAt}}')"; done
else echo "run containers: none"; fi

echo; echo "--- image (merged != deployed until the scheduler is recreated)"
latest=$(docker image inspect "$DF_IMAGE" --format '{{.Created}}' 2>/dev/null | cut -c1-19)
echo "local :latest created $latest"
for s in $DF_ALL_SCHEDULERS; do
  img=$(docker inspect "$s" --format '{{.Image}}' 2>/dev/null) || continue
  echo "$s runs image created $(docker image inspect "$img" --format '{{.Created}}' | cut -c1-19)"
done
gh run list --repo "$DF_REPO" --workflow publish.yml --limit 1 --json headSha,status,conclusion,updatedAt \
  --jq '.[] | "last publish: \(.headSha[0:7]) \(.status)/\(.conclusion) \(.updatedAt)"' 2>/dev/null

echo; echo "--- scheduler (last poll, last dispatch, last trouble)"
logs=$(docker logs --tail 400 "$DF_SCHEDULER" 2>&1)
echo "$logs" | grep -E 'backlog=' | tail -1
echo "$logs" | grep -E 'dispatch' | grep -v 'action=skip' | tail -1 | sed 's/^/last dispatch: /'
echo "$logs" | grep -iE 'fail|blocked|breaker|trip|rescue|orphan|window_gate' | grep -viE 'session_window_paused=true|main_red=false' | tail -3 | sed 's/^/trouble: /'
echo "retry counters (issue:phase=count; pauses do not consume retries since #341): $(docker exec "$DF_SCHEDULER" cat "$DF_STATE_DIR/scheduler-state.json" 2>/dev/null | jq -c 'with_entries(select(.key | endswith(":sig") | not))' 2>/dev/null | cut -c1-400)"

echo; echo "--- gate labels (open issues; empty results re-confirmed)"
for L in $GATE_LABELS; do printf '%-20s %s\n' "$L:" "$(read_label "$L" || echo 'QUERY FAILED')"; done

echo; echo "--- open PRs (factory PRs are drafts until gh pr ready)"
gh pr list --repo "$DF_REPO" --state open --json number,title,isDraft,mergeStateStatus,headRefName \
  --jq '.[] | "PR #\(.number) draft=\(.isDraft) \(.mergeStateStatus) \(.headRefName)  \(.title[0:60])"' 2>/dev/null || echo "QUERY FAILED"

echo; echo "--- main workflow runs (last 4)"
gh run list --repo "$DF_REPO" --branch main --limit 4 --json conclusion,status,displayTitle,headSha,workflowName \
  --jq '.[] | "\(.workflowName[0:10])\t\(.status)/\(.conclusion // "-") \(.headSha[0:7]) \(.displayTitle[0:55])"' 2>/dev/null

[ "$QUICK" = 1 ] && exit 0

echo; echo "--- board (non-Done)"
board=$(board_items_json) || { echo "board query failed"; exit 1; }
echo "$board" | jq -r '.items[] | select(.status != "Done") | "\(.status // "-")\t#\(.content.number // "?")\t\((.labels // []) | join(","))\t\(.content.title // "" | .[0:60])"' \
  | sort | awk -F'\t' '{printf "%-12s %-6s %-48s %s\n",$1,$2,substr($3,1,48),$4}'

echo; echo "--- dispatchability (what the scheduler WILL pick up; board label cache may lag)"
cands=$(echo "$board" | jq -r '
  def has(l): ((.labels // []) | index(l)) != null;
  .items[] | select(.content.number != null) |
  if .status=="Backlog" and has("ready-for-agent") and (has("needs-discussion") or has("epic") or has("spec-pending-review") or has("plan-pending-review") or has("ready-for-human") | not) then "refine\t\(.content.number)"
  elif .status=="Refined" and (has("needs-discussion") or has("epic") or has("spec-pending-review") or has("plan-pending-review") | not) then "plan\t\(.content.number)"
  elif .status=="Ready" and (has("needs-discussion") or has("epic") or has("ready-for-human") | not) then "implement\t\(.content.number)"
  elif .status=="Blocked" and (has("needs-discussion") or has("epic") | not) then "retry?\t\(.content.number)"
  else empty end')
if [ -z "$cands" ]; then
  echo "QUIESCENT: nothing dispatchable. Safe to leave unattended; the window cannot climb."
else
  while IFS=$'\t' read -r kind n; do
    live=$(issue_labels "$n")
    deps=""; for d in $(issue_deps "$n"); do [ "$(issue_state "$d")" = "CLOSED" ] || deps="$deps #$d(open)"; done
    printf '%-10s #%-4s labels=%s%s\n' "$kind" "$n" "$live" "${deps:+  BLOCKED-BY:$deps}"
  done <<<"$cands"
fi

echo; echo "--- off-board ready-for-agent issues (invisible to the scheduler; board.sh add N)"
onboard=$(echo "$board" | jq -r '.items[].content.number // empty' | sort -u)
off=$(gh issue list --repo "$DF_REPO" --state open --label ready-for-agent --limit 200 --json number --jq '.[].number' | sort -u | comm -23 - <(echo "$onboard"))
echo "${off:-none}" | tr '\n' ' '; echo
