#!/usr/bin/env bash
# Sign a blast-radius approval for a held branch (#551). HUMAN-RUN on the operator host.
#   approve-blast.sh N --sha <head> [--yes] [--by "<name>"]
# Without --yes it stops after printing what would be signed: the triggered paths, each
# path's diff, and the record. Read that, then re-run with --yes to sign and lift the hold.
#
# What it does, in order (spec: docs/archive/2026-11-21-blast-radius-durable-approval-design.md):
#   1. Preconditions: needs-discussion present; board Blocked or In Review; no live run
#      container; this checkout's origin is $DF_REPO; exactly one feat/issue-N-* branch on
#      origin whose head equals --sha.
#   2. Recomputes the trigger set with the gate BAKED INTO THE FACTORY IMAGE at --sha
#      (hotspots, config and approver key from origin/main), unions it with the factory's
#      advisory request file, and builds the canonical record.
#   3. Signs it on the host with your private key (never copied anywhere), writes
#      .sig then .json into the scheduler's state volume as the factory user, reads both
#      back, and has the baked gate verify the result end to end against origin/main's key.
#   4. Posts an audit comment (no authority) and removes needs-discussion LAST.
# The signing key: $DF_BLAST_KEY (fingerprint), else the first key in origin/main's
# .factory/blast-approvers.asc for which this host holds the secret key.
# Reviewing the diff is YOUR job; this script only binds your sign-off to exact content.
set -uo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
need git; need gpg

n=${1:-}; shift 2>/dev/null || { sed -n '2,7p' "$0"; exit 2; }
sha=""; yes=0; by=""
while [ $# -gt 0 ]; do case "$1" in
  --sha) sha=${2:-}; shift 2;;
  --yes) yes=1; shift;;
  --by) by=${2:-}; shift 2;;
  *) die "unknown arg $1";;
esac; done
case "$n" in ''|*[!0-9]*) die "issue number must be numeric";; esac
printf '%s' "$sha" | grep -qE '^[0-9a-f]{40}$' || die "--sha must be the full 40-hex head from the hold comment"
[ -n "$by" ] || by=$(git -C "$DF_REPO_DIR" config user.name) || true
[ -n "$by" ] || die "no approver name (pass --by or set git user.name)"

G() { git -C "$DF_REPO_DIR" "$@"; }
APPROVALS="$DF_STATE_DIR/blast-approvals"

# ---- 1. preconditions -------------------------------------------------------------------
labels=$(issue_labels "$n"); board=$(issue_board_status "$n")
echo "#$n before: board=$board labels=[$labels]"
case ",$labels," in *",needs-discussion,"*) ;; *) die "#$n does not carry needs-discussion; nothing is held";; esac
case "$board" in Blocked|"In Review") ;; *) die "#$n board is '$board', expected Blocked or In Review";; esac
live=$(issue_run_container "$n"); [ -z "$live" ] || die "#$n has a live run container ($live)"
origin=$(G remote get-url origin) || die "no origin remote in $DF_REPO_DIR"
case "$origin" in *"$DF_REPO"|*"$DF_REPO.git") ;; *) die "origin is $origin, not $DF_REPO (set DF_REPO_DIR to the right checkout)";; esac

heads=$(G ls-remote --heads origin "refs/heads/feat/issue-$n-*" | awk -v p="refs/heads/feat/issue-$n-" 'index($2,p)==1{print $2}')
[ "$(printf '%s' "$heads" | grep -c .)" -eq 1 ] || die "expected exactly one feat/issue-$n-* branch on origin, found: ${heads:-none}"
branch=${heads#refs/heads/}
# Explicit remote-tracking refs: FETCH_HEAD is repointed by every fetch and cannot be trusted.
G fetch -q --no-tags origin "+refs/heads/main:refs/remotes/origin/main" "+refs/heads/$branch:refs/remotes/origin/$branch" || die "fetch failed"
head=$(G rev-parse --verify -q "refs/remotes/origin/$branch^{commit}")
[ "$head" = "$sha" ] || die "origin/$branch is at $head, not $sha; the branch moved since the hold comment. Review the new head."
G cat-file -e "refs/remotes/origin/main:.factory/blast-approvers.asc" 2>/dev/null \
  || die "origin/main has no .factory/blast-approvers.asc; merge the approver public key first (PLAYBOOK 'Blast-radius hold')"

# ---- signing key ------------------------------------------------------------------------
KH=$(mktemp -d); OUT=$(mktemp -d); chmod 700 "$KH"
trap 'rm -rf "$KH" "$OUT"' EXIT
G show "refs/remotes/origin/main:.factory/blast-approvers.asc" > "$KH/approvers.asc"
gpg --homedir "$KH" --batch --quiet --import "$KH/approvers.asc" 2>/dev/null || die "origin/main's approver key file does not import"
trusted_fprs=$(gpg --homedir "$KH" --batch --with-colons --list-keys 2>/dev/null | awk -F: '$1=="pub"{p=1;next} p&&$1=="fpr"{print $10;p=0}')
key=${DF_BLAST_KEY:-}
if [ -z "$key" ]; then
  for f in $trusted_fprs; do gpg --batch --list-secret-keys "$f" >/dev/null 2>&1 && { key=$f; break; }; done
fi
[ -n "$key" ] || die "this host holds no secret key matching origin/main's approver keys (set DF_BLAST_KEY or see the key runbook)"
echo "signing key: $key"

# ---- 2. recompute with the baked gate, build the record -----------------------------------
docker exec "$DF_SCHEDULER" cat "$APPROVALS/$n.request.json" > "$OUT/request.json" 2>/dev/null || rm -f "$OUT/request.json"
docker exec "$DF_SCHEDULER" cat "$APPROVALS/$n.json" > "$OUT/previous.json" 2>/dev/null || rm -f "$OUT/previous.json"
mix() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
gitdir=$(mix "$(cd "$DF_REPO_DIR" && cd "$(git rev-parse --git-common-dir)" && pwd)")
in_image() {  # in_image <extra docker args...> -- <blast_record.py args...>
  local dargs=()
  while [ "$1" != "--" ]; do dargs+=("$1"); shift; done; shift
  docker run --rm --network none --entrypoint python3 \
    -v "$gitdir:/src:ro" -v "$(mix "$DF_SCRIPT_DIR"):/tool:ro" "${dargs[@]}" \
    "$DF_IMAGE" /tool/blast_record.py "$@" --src /src --issue "$n" --repo "$DF_REPO" --branch "$branch" --sha "$sha"
}
echo "--- gate verdict at $sha (baked gate, image $DF_IMAGE)"
in_image -v "$(mix "$OUT"):/out" -- build --out /out --approved-by "$by" --request /out/request.json --previous /out/previous.json
rc=$?
[ "$rc" -eq 0 ] || die "could not build a record (exit $rc)"
[ -s "$OUT/$n.json" ] || die "no record was written"
base=$(cat "$OUT/base_sha.txt")

echo; echo "--- diff of every path this approval covers ($base..$sha)"
while IFS= read -r p; do
  [ -n "$p" ] || continue
  echo "=== $p"
  G --no-pager diff --no-renames "$base" "$sha" -- "$p"
done < "$OUT/paths.txt"
echo; echo "--- record to be signed"; cat "$OUT/$n.json"

if [ "$yes" != 1 ]; then
  echo; echo "Nothing signed. Review the diff above, then re-run with --yes:"
  echo "  approve-blast.sh $n --sha $sha --yes"
  exit 3
fi

# ---- 3. sign on the host, write into the state volume, verify -----------------------------
gpg --batch --yes --local-user "$key" --detach-sign --output "$OUT/$n.json.sig" "$OUT/$n.json" || die "signing failed"
# Host-side check with the spec's exact invocation, against origin/main's key only.
st=$(gpg --homedir "$KH" --batch --status-fd 1 --verify "$OUT/$n.json.sig" "$OUT/$n.json" 2>/dev/null) || die "signature does not verify against origin/main's approver key"
[ "$(printf '%s\n' "$st" | grep -c '^\[GNUPG:\] GOODSIG ')" -eq 1 ] && printf '%s\n' "$st" | grep -q '^\[GNUPG:\] VALIDSIG ' \
  && ! printf '%s\n' "$st" | grep -qE '^\[GNUPG:\] (EXPSIG|EXPKEYSIG|REVKEYSIG|BADSIG|ERRSIG) ' \
  || die "signature status is not a clean GOODSIG+VALIDSIG (expired or revoked key?)"

put() {  # put <local file> <name in the approvals dir>; temp file + mv, as the factory user
  docker exec -i -u factory "$DF_SCHEDULER" sh -c \
    "umask 022; mkdir -p '$APPROVALS' && cat > '$APPROVALS/.$2.tmp' && mv '$APPROVALS/.$2.tmp' '$APPROVALS/$2'" < "$1" \
    || die "could not write $2 into $DF_SCHEDULER:$APPROVALS"
}
put "$OUT/$n.json.sig" "$n.json.sig"   # signature first: a record without one is never trusted
put "$OUT/$n.json" "$n.json"
mkdir "$OUT/back"
for f in "$n.json" "$n.json.sig"; do
  docker exec "$DF_SCHEDULER" cat "$APPROVALS/$f" > "$OUT/back/$f" || die "read-back of $f failed"
  cmp -s "$OUT/$f" "$OUT/back/$f" || die "read-back of $f differs from what was signed"
done
echo; echo "--- end-to-end check: the baked gate with the stored approval"
in_image -v "$(mix "$OUT/back"):/approvals:ro" -- verify --approvals /approvals \
  || die "the gate does not accept the stored approval; needs-discussion NOT removed"

# ---- 4. audit comment, then lift the hold last --------------------------------------------
{
  echo "<!-- df-blast-approval -->"
  echo "## Blast-Radius Gate — approval recorded"
  echo
  echo "Signed approval written for \`$branch\` at \`$sha\` (base \`$base\`), by **$by**, key \`$key\`."
  echo "Approval #$(python -c "import json,sys;print(json.load(open(sys.argv[1]))['approvals_count'])" "$(mix "$OUT/$n.json")") for this issue. Covered paths:"
  echo
  sed 's/^/- `/; s/$/`/' "$OUT/paths.txt"
  echo
  echo "This comment is an audit note and carries no authority; the gate reads only the signed record."
} > "$OUT/comment.md"
gh issue comment "$n" --repo "$DF_REPO" --body-file "$(mix "$OUT/comment.md")" >/dev/null || echo "WARNING: audit comment failed to post"
gh issue edit "$n" --repo "$DF_REPO" --remove-label needs-discussion >/dev/null \
  || die "approval is stored and verified, but removing needs-discussion failed; remove it by hand"
sleep 2; echo "#$n after:  board=$(issue_board_status "$n") labels=[$(issue_labels "$n")]"
echo "the next run for #$n recomputes the gate and passes on this approval"
