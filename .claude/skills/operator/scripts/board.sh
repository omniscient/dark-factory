#!/usr/bin/env bash
# Board membership and status, by issue number. Verifies every write by reading back.
#   board.sh show N              current board status + labels + live run container
#   board.sh add N               add an open issue to the board and set Backlog
#   board.sh set N <Status>      Backlog | Refined | Ready | Blocked | "In Progress" | "In Review" | Done
# Refuses to set a status while a run container for N is alive: the run's own board write
# lands after yours and silently overrides it (seen on #418/#400). Pass --force to override.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
cmd=${1:-}; n=${2:-}; [ -n "$cmd" ] && [ -n "$n" ] || { sed -n '2,8p' "$0"; exit 2; }
force=0; for a in "$@"; do [ "$a" = "--force" ] && force=1; done

show() {
  echo "#$1 state=$(issue_state "$1") board=$(issue_board_status "$1" | sed 's/^$/OFF-BOARD/') labels=[$(issue_labels "$1")] run=$(issue_run_container "$1" | tr '\n' ' ')"
}

case "$cmd" in
  show) show "$n" ;;
  add)
    if [ -n "$(item_id_for_issue "$n")" ]; then echo "#$n already on the board"; show "$n"; exit 0; fi
    cid=$(issue_node_id "$n") || die "cannot resolve issue #$n"
    item=$(gh api graphql -f query="mutation { addProjectV2ItemById(input: {projectId: \"$(project_id)\", contentId: \"$cid\"}) { item { id } } }" --jq '.data.addProjectV2ItemById.item.id') || die "add failed"
    echo "added item $item"
    _DF_BOARD_ITEM=$item
    gh api graphql -f query="mutation { updateProjectV2ItemFieldValue(input: {projectId: \"$(project_id)\", itemId: \"$item\", fieldId: \"$(status_field_id)\", value: {singleSelectOptionId: \"$(status_option_id Backlog)\"}}) { projectV2Item { id } } }" >/dev/null || die "status write failed"
    sleep 2; show "$n" ;;
  set)
    status=${3:-}; [ -n "$status" ] || die "usage: board.sh set N <Status>"
    live=$(issue_run_container "$n")
    if [ -n "$live" ] && [ "$force" = 0 ]; then die "#$n has a live run container ($live); its board write would override yours. Wait for exit or pass --force."; fi
    before=$(issue_board_status "$n")
    item=$(set_board_status "$n" "$status") || exit 1
    sleep 2; after=$(issue_board_status "$n")
    echo "#$n board: '$before' -> '$after' (item $item)"
    [ "$after" = "$status" ] || die "read-back mismatch: expected '$status', got '$after'" ;;
  *) die "unknown command '$cmd'" ;;
esac
