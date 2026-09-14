#!/usr/bin/env bash
# MERGED != DEPLOYED. Read-only report of what is on main vs what is actually running.
#   bash .claude/skills/operator/scripts/deploy-check.sh
# Three layers, three activation steps:
#   run-container side (entrypoint.sh, workflows/, commands/, scripts/, config/): live after
#     `docker pull` of :latest (run containers are created from the image each dispatch)
#   scheduler side (scheduler.sh, scripts/factory_core used by the poll loop): live only after
#     the scheduler container is RECREATED (compose does not auto-pull; restart reuses the image)
#   deploy/** and publish.yml: human-only, never touched by the operator
# Recreating schedulers is NOT delegated by default; this script prints the commands only.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
git -C "$DF_REPO_DIR" fetch -q origin main 2>/dev/null || echo "WARNING: git fetch failed; using stale origin/main"
main=$(git -C "$DF_REPO_DIR" rev-parse --short origin/main)
echo "origin/main: $main  $(git -C "$DF_REPO_DIR" log -1 --format='%cI %s' origin/main | cut -c1-90)"
gh run list --repo "$DF_REPO" --workflow publish.yml --limit 1 --json headSha,status,conclusion,updatedAt,databaseId \
  --jq '.[] | "last publish: run \(.databaseId) \(.headSha[0:7]) \(.status)/\(.conclusion) \(.updatedAt)"'
reg=$(docker image inspect "$DF_IMAGE" --format '{{.Created}}' 2>/dev/null | cut -c1-19)
echo "local :latest created: ${reg:-not pulled}Z"
reg_epoch=$(date -u -d "${reg:-1970-01-01T00:00:00}Z" +%s 2>/dev/null || echo 0)
main_epoch=$(git -C "$DF_REPO_DIR" log -1 --format=%ct origin/main)
[ "$reg_epoch" -lt "$main_epoch" ] && echo "=> local :latest is OLDER than origin/main; if the publish above succeeded, 'docker pull' is needed"
echo
for s in $DF_ALL_SCHEDULERS; do
  docker inspect "$s" >/dev/null 2>&1 || { echo "$s: absent"; continue; }
  img=$(docker inspect "$s" --format '{{.Image}}'); started=$(docker inspect "$s" --format '{{.State.StartedAt}}' | cut -c1-19)
  created=$(docker image inspect "$img" --format '{{.Created}}' | cut -c1-19)
  echo "$s: started $started on image built $created paused=$(docker inspect "$s" --format '{{.State.Paused}}')"
  changes=$(git -C "$DF_REPO_DIR" log --oneline --since="$created" origin/main -- scheduler.sh scripts/scheduler_lib.sh scripts/factory_core config/config.yaml deploy/docker-compose.yml 2>/dev/null)
  if [ -n "$changes" ]; then
    echo "  scheduler-side commits merged AFTER this image was built (not live until recreate):"
    echo "$changes" | sed 's/^/    /'
  else echo "  no scheduler-side changes since its image"; fi
done
echo
echo "run-side files merged after the local :latest was built (need docker pull):"
git -C "$DF_REPO_DIR" log --oneline --since="@$reg_epoch" origin/main -- entrypoint.sh workflows commands scripts config refinement-skills Dockerfile 2>/dev/null | sed 's/^/  /' | head -20
echo
cat <<EOF
activation (only when 'run containers: none' and the queue is idle; unpause first if paused):
  docker pull $DF_IMAGE
  # self instance (scheduler recreate; state lives in the named volume and survives):
  docker compose -f $DF_REPO_DIR/deploy/docker-compose.yml --env-file $DF_REPO_DIR/deploy/instance-self.env -p dark-factory-self up -d backlog-scheduler
  # MarketHawk instance is a SEPARATE decision:
  docker compose -f $DF_REPO_DIR/deploy/docker-compose.yml --env-file $DF_REPO_DIR/deploy/instance.env -p dark-factory up -d backlog-scheduler
verify: docker exec $DF_SCHEDULER grep -c <new-symbol> /opt/dark-factory/scheduler.sh ; docker logs --tail 5 $DF_SCHEDULER
EOF
