#!/usr/bin/env bash
# Unit test for ProjectV2 pagination in scheduler.sh.
# Run: bash tests/test_scheduler_pagination.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCHED="$SCRIPT_DIR/../scheduler.sh"

docker() { return 0; }
export -f docker

export GH_TOKEN="${GH_TOKEN:-stub-token}"
export CLAUDE_CODE_OAUTH_TOKEN="${CLAUDE_CODE_OAUTH_TOKEN:-stub-token}"
export SCHEDULER_SOURCE_ONLY=1
export SCHEDULER_STATE_DIR="$(mktemp -d /tmp/sched-page-test-statedir-XXXXXX)"
export FACTORY_CORE_CLI="$SCRIPT_DIR/../scripts/factory_core/cli.py"

STUB_LOG="$(mktemp /tmp/sched-page-test-gh-XXXXXX.log)"; export STUB_LOG

# #507: off_board_ready_issues / fetch_board_items now go through providers/cli.py, which
# runs `gh` as a SUBPROCESS. An exported shell function is invisible to a non-bash child,
# so put a PATH shim in front that re-enters bash and lets the gh() function below answer.
GH_SHIM_DIR="$(mktemp -d /tmp/sched-gh-shim-XXXXXX)"
printf '#!/usr/bin/env bash\ngh "$@"\n' > "$GH_SHIM_DIR/gh"
chmod +x "$GH_SHIM_DIR/gh"
export PATH="$GH_SHIM_DIR:$PATH"
export FACTORY_PROVIDERS_CLI="$SCRIPT_DIR/../scripts/factory_core/providers/cli.py"

gh() {
  echo "gh $*" >> "$STUB_LOG"
  if echo "$*" | grep -q 'after: "CUR1"'; then
    cat <<'JSON'
{"data":{"node":{"items":{"pageInfo":{"hasNextPage":false,"endCursor":null},"nodes":[{"fieldValueByName":{"name":"Backlog"},"content":{"number":102,"title":"second page issue","labels":{"nodes":[{"name":"ready-for-agent"}]}}}]}}}}
JSON
  else
    cat <<'JSON'
{"data":{"node":{"items":{"pageInfo":{"hasNextPage":true,"endCursor":"CUR1"},"nodes":[{"fieldValueByName":{"name":"Backlog"},"content":{"number":101,"title":"first page issue","labels":{"nodes":[{"name":"ready-for-agent"}]}}}]}}}}
JSON
  fi
}
export -f gh

source "$SCHED"

BOARD_PAGE_RESULT="$(fetch_board_items)"

cleanup() {
  rm -f "$STUB_LOG"
  rm -rf "$SCHEDULER_STATE_DIR" "$GH_SHIM_DIR"
}
trap cleanup EXIT

if [ "$(echo "$BOARD_PAGE_RESULT" | jq '.items | length')" != "2" ]; then
  echo "FAIL: expected two items across paginated ProjectV2 pages" >&2
  echo "$BOARD_PAGE_RESULT" >&2
  exit 1
fi

if [ "$(grep -c 'items(first: 100' "$STUB_LOG" || true)" != "2" ]; then
  echo "FAIL: expected every ProjectV2 page request to use first: 100" >&2
  cat "$STUB_LOG" >&2
  exit 1
fi

if [ "$(grep -c 'after: "CUR1"' "$STUB_LOG" || true)" != "1" ]; then
  echo "FAIL: expected second ProjectV2 request to include cursor CUR1" >&2
  cat "$STUB_LOG" >&2
  exit 1
fi

if [ "$(grep -c '^gh api graphql -f query=' "$STUB_LOG" || true)" != "2" ]; then
  echo "FAIL: expected exactly one gh api graphql call per page (2 pages)" >&2
  cat "$STUB_LOG" >&2
  exit 1
fi

if [ "$(echo "$BOARD_PAGE_RESULT" | jq -c '.items[1]')" != '{"content":{"number":102,"title":"second page issue","type":"Issue"},"labels":["ready-for-agent"],"status":"Backlog"}' ]; then
  echo "FAIL: item shape drifted from the pre-#507 jq output" >&2
  echo "$BOARD_PAGE_RESULT" >&2
  exit 1
fi

echo "PASS: fetch_board_items paginates ProjectV2 items"
