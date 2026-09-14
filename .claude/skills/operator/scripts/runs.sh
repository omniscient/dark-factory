#!/usr/bin/env bash
# Inspect factory runs: the ledger the scheduler keeps, and live containers. Read-only.
#   runs.sh recent [N]        last N run records (issue, intent, status, duration, cost, hot nodes)
#   runs.sh issue N           every run record for issue N, newest first
#   runs.sh live [container]  DAG node progress of a live run container (default: the first one)
#   runs.sh transcript <c>    copy the phase agent's transcript OUT of a live container (it is --rm:
#                             gone on exit) to $DF_OUT (default ./run-transcripts/<container>/)
# Run records live at $DF_STATE_DIR/run-records/*.json inside the scheduler container
# (per-node tokens/cost/duration: "agent bailed early" vs "worked but did not commit").
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cmd=${1:-recent}
PY="$DF_SCRIPT_DIR/run_summary.py"

records() {  # $1 = how many newest files; one JSON record per output line
  docker exec "$DF_SCHEDULER" sh -c "cd $DF_STATE_DIR/run-records 2>/dev/null && ls -t | head -$1 | while read f; do tr -d '\n' < \"\$f\"; echo; done"
}

case "$cmd" in
  recent) records "${2:-15}" | python "$PY" records ;;
  issue)  n=${2:?issue number}; records 400 | grep -E "\"issue_number\": *$n[,}]" | python "$PY" records ;;
  live)
    c=${2:-$(run_containers | head -1)}; [ -n "$c" ] || { echo "no live run container"; exit 0; }
    echo "$c  cmd='$(container_command "$c")'  started $(docker inspect "$c" --format '{{.State.StartedAt}}' | cut -c1-19)"
    docker logs "$c" 2>&1 | python "$PY" nodes | tail -40 ;;
  transcript)
    c=${2:?container name}; out="${DF_OUT:-./run-transcripts/$c}"; mkdir -p "$out"
    docker exec "$c" sh -c 'ls -S /home/factory/.claude/projects/*/*.jsonl 2>/dev/null | head -3' | while read -r f; do
      docker cp "$c:$f" "$out/" && echo "copied $f -> $out/"
    done
    echo "largest file = the phase agent; assistant text/tool_use blocks are under message.content" ;;
  *) sed -n '2,9p' "$0"; exit 2 ;;
esac
