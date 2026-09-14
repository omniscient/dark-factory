#!/usr/bin/env bash
# Change-only watch loop for the Monitor tool. Prints a line only when something changed.
#   bash .claude/skills/operator/scripts/watch.sh [issue ...]
# Watches: gate labels (empty re-confirmed), open PRs + CI rollup, factory window state,
# run containers, and scheduler-log trouble lines (case-insensitive) for the given issues.
# Query failures are reported as UNKNOWN after 5 misses, never as "clear".
# Env: DF_WATCH_INTERVAL (default 90s).
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
ISSUES="$*"; INTERVAL="${DF_WATCH_INTERVAL:-90}"
prev=""; fails=0; since=$(date -u +%FT%TZ)
echo "$(date -u +%H:%MZ) watching repo=$DF_REPO issues=${ISSUES:-all} every ${INTERVAL}s"
while true; do
  ok=1; cur=""
  for L in spec-pending-review plan-pending-review needs-discussion; do
    v=$(read_label "$L") || { ok=0; v="?"; }
    cur="$cur$L: $v"$'\n'
  done
  prs=$(gh pr list --repo "$DF_REPO" --state open --json number,isDraft,mergeStateStatus,statusCheckRollup,headRefName \
        --jq '.[] | "PR #\(.number) draft=\(.isDraft) \(.mergeStateStatus) checks=" + ([.statusCheckRollup[]? | (.conclusion // .status // .state)] | group_by(.) | map("\(.[0])x\(length)") | join(",")) + " \(.headRefName[0:40])"') || { ok=0; prs="?"; }
  cur="${cur}prs: ${prs:-none}"$'\n'
  cur="${cur}window: $(window_state)"$'\n'
  runs=$(run_containers | sed "s/^${DF_RUN_PREFIX}//" | sort | tr '\n' ',')
  cur="${cur}runs: ${runs:-none}"$'\n'
  if [ -n "$ISSUES" ]; then
    pat=$(printf '#%s\\b|' $ISSUES); pat="${pat%|}"
    docker logs --since "$since" "$DF_SCHEDULER" 2>&1 | grep -E "$pat" | grep -iE 'dispatch|fail|blocked|breaker|trip|rescue|orphan|promot|merged|window_gate' \
      | grep -v 'action=skip' | tail -5 | sed 's/^/scheduler: /'
    since=$(date -u +%FT%TZ)
  fi
  if [ "$ok" = 0 ]; then
    fails=$((fails+1)); [ "$fails" -ge 5 ] && { echo "$(date -u +%H:%MZ) WATCH DEGRADED: $fails consecutive gh failures; gate state UNKNOWN, not clear"; fails=0; }
  else
    fails=0
    if [ "$cur" != "$prev" ]; then
      echo "$(date -u +%H:%MZ) ---"; printf '%s' "$cur"
      prev="$cur"
    fi
  fi
  sleep "$INTERVAL"
done
