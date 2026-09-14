#!/usr/bin/env bash
# Claude capacity check before opting work in. Read-only.
#   bash .claude/skills/operator/scripts/usage.sh
# Two different windows: the HOST token (this session + peer operator sessions) and the
# FACTORY token (.archon/.env, its own 5h window). The factory's is read from its own
# pause sentinel and recent paused rows, never from the host percentage.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
echo "--- host token (operator sessions share it)"
host_usage
echo "--- factory token"
echo "window: $(window_state)"
echo "recent pauses (runs.jsonl stage=paused, last 5):"
docker exec "$DF_SCHEDULER" sh -c "grep '\"stage\": *\"paused\"' $DF_STATE_DIR/runs.jsonl 2>/dev/null | tail -5" 2>/dev/null \
  | python -c "
import sys,json
for l in sys.stdin:
    try: r=json.loads(l)
    except Exception: continue
    print(' ', r.get('timestamp') or r.get('ts') or '?', 'issue', r.get('issue') or r.get('issue_number') or '?', r.get('command') or r.get('intent') or '')
" 2>/dev/null
echo "pause lines in the last 400 scheduler log lines: $(docker logs --tail 400 "$DF_SCHEDULER" 2>&1 | grep -c 'session_window_gate=active')"
echo "ledger ownership (must be factory, not root):"
docker exec "$DF_SCHEDULER" ls -la "$DF_STATE_DIR/runs.jsonl" 2>/dev/null | awk '{print "  "$3":"$4"  "$5" bytes  "$6" "$7" "$8}'
echo
echo "rule of thumb: one refine ~8-10% of the factory window; refine+plan+implement ~50%;"
echo "two runs in parallel drain it in about an hour. Opt in one ticket at a time while a plan runs."
