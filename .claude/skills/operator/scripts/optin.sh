#!/usr/bin/env bash
# Opt an issue into refinement safely: on the board, Backlog, ready-for-agent, deps checked.
#   optin.sh N [--direct-to-pr] [--label <extra>]...
# Checks first: capacity (both windows), open deps, gate/halt labels already present,
# board membership (adds if missing; REST-filed issues are never on the board), an existing
# refine branch (stale branches are REUSED by setup-refine-branch, so delete or accept).
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
n=${1:-}; [ -n "$n" ] || { sed -n '2,7p' "$0"; exit 2; }; shift
extra=""; d2p=0
while [ $# -gt 0 ]; do case "$1" in --direct-to-pr) d2p=1; shift;; --label) extra="$extra,$2"; shift 2;; *) die "unknown arg $1";; esac; done

echo "--- capacity"; host_usage; echo "factory window: $(window_state)"
echo "running: $(run_containers | tr '\n' ' ')"
echo "--- #$n"
[ "$(issue_state "$n")" = "OPEN" ] || die "#$n is not open"
labels=$(issue_labels "$n"); echo "labels=[$labels]"
for l in needs-discussion epic spec-pending-review plan-pending-review ready-for-human above-ceiling; do
  case ",$labels," in *",$l,"*) echo "WARNING: #$n carries $l; the scheduler will NOT dispatch it until that is cleared";; esac
done
for d in $(issue_deps "$n"); do
  st=$(issue_state "$d"); echo "depends on #$d: $st"; [ "$st" = CLOSED ] || echo "WARNING: implement dispatch gated until #$d closes (refine/plan still run)"
done
b=$(git -C "$DF_REPO_DIR" ls-remote --heads origin "refine/issue-$n-*" | awk '{print $2}')
[ -z "$b" ] || echo "WARNING: existing refine branch will be REUSED, not forked fresh: $b"
if [ -z "$(item_id_for_issue "$n")" ]; then
  echo "#$n is off-board; adding"; bash "$(dirname "${BASH_SOURCE[0]}")/board.sh" add "$n" || exit 1
else
  cur=$(issue_board_status "$n"); [ "$cur" = Backlog ] || echo "NOTE: board status is '$cur' (not Backlog); refine only dispatches from Backlog"
fi
add="ready-for-agent"; [ "$d2p" = 1 ] && add="$add,direct-to-pr"; add="$add$extra"
gh issue edit "$n" --repo "$DF_REPO" --add-label "$add" >/dev/null || die "label add failed"
sleep 2; echo "#$n now: board=$(issue_board_status "$n") labels=[$(issue_labels "$n")]"
echo "expect 'dispatch command=\"Refine issue #$n\"' in the scheduler log within one poll"
