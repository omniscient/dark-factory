#!/usr/bin/env bash
# Approve a spec or plan gate IN THE ONLY SAFE ORDER: board first, gate label second.
#   approve.sh spec N [--sha <commit>] [--lift-discussion]
#   approve.sh plan N [--sha <commit>] [--lift-discussion]
# spec: Backlog -> Refined, then remove spec-pending-review  (REFINED loop dispatches "Plan issue #N")
# plan: Refined -> Ready,   then remove plan-pending-review  (READY loop dispatches "Fix issue #N")
# Why the order: the scheduler dispatches on board status alone and the gate label is the only
# thing that skips it. Label-first opens a window in which a poll fires a redundant run.
# --sha: refuse unless that commit is on the refine branch at origin (use after amendments).
# --lift-discussion: also remove needs-discussion (architect-cap plans carry both labels).
# Reviewing the artifact is YOUR job before running this; see PLAYBOOK.md "Gate review".
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
kind=${1:-}; n=${2:-}; shift 2 2>/dev/null || { sed -n '2,12p' "$0"; exit 2; }
sha=""; lift=0
while [ $# -gt 0 ]; do case "$1" in --sha) sha=$2; shift 2;; --lift-discussion) lift=1; shift;; *) die "unknown arg $1";; esac; done
case "$kind" in
  spec) from=Backlog; to=Refined; label=spec-pending-review ;;
  plan) from=Refined; to=Ready;   label=plan-pending-review ;;
  *) die "kind must be spec or plan" ;;
esac

labels=$(issue_labels "$n"); board=$(issue_board_status "$n")
echo "#$n before: board=$board labels=[$labels]"
case ",$labels," in *",$label,"*) ;; *) die "#$n does not carry $label; nothing to approve (or already approved)";; esac
[ "$board" = "$from" ] || die "#$n board is '$board', expected '$from'"
live=$(issue_run_container "$n"); [ -z "$live" ] || die "#$n has a live run container ($live); approving now races its board write"

branch=$(git -C "$DF_REPO_DIR" ls-remote --heads origin "refine/issue-$n-*" | awk '{print $2}' | sed 's#refs/heads/##' | head -1)
[ -n "$branch" ] || die "no refine/issue-$n-* branch on origin; the artifact the gate label claims does not exist"
echo "refine branch: $branch @ $(git -C "$DF_REPO_DIR" ls-remote origin "refs/heads/$branch" | cut -c1-7)"
# Explicit remote-tracking refs: FETCH_HEAD is repointed by every fetch and cannot be trusted.
git -C "$DF_REPO_DIR" fetch -q origin "+refs/heads/main:refs/remotes/origin/main" "+refs/heads/$branch:refs/remotes/origin/$branch" || die "fetch failed"
REF="origin/$branch"
if [ -n "$sha" ]; then
  git -C "$DF_REPO_DIR" merge-base --is-ancestor "$sha" "$REF" || die "commit $sha is NOT on $REF; push the amendment first"
  echo "amendment $sha confirmed on $REF"
fi
# Only the artifacts THIS branch added or changed relative to main (not every file on the branch).
added=$(git -C "$DF_REPO_DIR" diff --name-only "origin/main...$REF" -- "docs/superpowers/${kind}s/" | grep -E '\.md$')
[ -n "$added" ] || die "the branch adds no docs/superpowers/${kind}s/*.md relative to main; the gate label is stranded"
echo "$kind file(s) on the branch:"; echo "$added" | sed 's/^/  /'
for f in $added; do
  git -C "$DF_REPO_DIR" show "$REF:$f" | grep -qE "#$n\b" || echo "WARNING: $f does not mention #$n; downstream SPEC_FILE/PLAN_FILE resolution may go blind (see #382)"
done

item=$(set_board_status "$n" "$to") || exit 1
sleep 2; after=$(issue_board_status "$n"); [ "$after" = "$to" ] || die "board read-back is '$after', not '$to'; label NOT removed"
gh issue edit "$n" --repo "$DF_REPO" --remove-label "$label" >/dev/null || die "label removal failed (API budget?) - board is already $to; remove $label by hand"
[ "$lift" = 1 ] && { gh issue edit "$n" --repo "$DF_REPO" --remove-label needs-discussion >/dev/null || echo "WARNING: needs-discussion removal failed"; }
sleep 2; echo "#$n after:  board=$(issue_board_status "$n") labels=[$(issue_labels "$n")]"
echo "next poll dispatches: $([ "$kind" = spec ] && echo "Plan issue #$n" || echo "Fix issue #$n")"
