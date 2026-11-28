#!/usr/bin/env bash
# The push-and-pr changeset block (#587): the text between its two markers in
# workflows/archon-dark-factory.yaml, run for real in a scratch git repository with its
# pending directory pointed at a temp dir. No push, no network.
# Run: bash tests/test_preview_changeset_write.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
WF="$REPO/workflows/archon-dark-factory.yaml"
WORK=$(mktemp -d /tmp/ph-changeset-XXXXXX)
trap 'chmod -R u+w "$WORK" 2>/dev/null; rm -rf "$WORK"' EXIT

PASSED=0; FAILED=0
check() { local desc="$1"; shift; if "$@"; then echo "  PASS: $desc"; PASSED=$((PASSED+1)); else echo "  FAIL: $desc" >&2; FAILED=$((FAILED+1)); fi; }

# The node's bash, YAML-decoded, then the marked block with its fixed path redirected.
NODE="$WORK/node.sh"
python3 - "$WF" > "$NODE" <<'PY'
import sys, yaml
nodes = {n["id"]: n for n in yaml.safe_load(open(sys.argv[1]))["nodes"]}
sys.stdout.write(nodes["push-and-pr"]["bash"])
PY
BLOCK="$WORK/block.sh"
sed -n '/^# >>> preview-helper changeset (#587)/,/^# <<< preview-helper changeset (#587)/p' "$NODE" \
  | sed "s#PH_DIR=/var/lib/dark-factory/preview-pending#PH_DIR=\"\$PH_TEST_PENDING\"#" > "$BLOCK"
check "the block is present between its markers and names the fixed pending path" \
  bash -c 'grep -q "^# <<< preview-helper changeset (#587)" "$0" && grep -qF "PH_DIR=/var/lib/dark-factory/preview-pending" "$1"' "$BLOCK" "$NODE"

# A scratch repository: main plus one feature commit with an awkward file name.
GIT="$WORK/repo"; mkdir -p "$GIT"
git -C "$GIT" init -q -b main
git -C "$GIT" -c user.email=t@t -c user.name=t commit -q --allow-empty -m base
git -C "$GIT" checkout -q -b feat/x
mkdir -p "$GIT/docs"; echo a > "$GIT/docs/b.md"; echo b > "$GIT/we ird \$(x).md"
git -C "$GIT" add -A
git -C "$GIT" -c user.email=t@t -c user.name=t commit -q -m feat
HEAD_SHA=$(git -C "$GIT" rev-parse HEAD)
ART="$WORK/artifacts"; mkdir -p "$ART"
jq -n '{resolved_number: 42, title: "A title with \"quotes\" and $(x)",
        labels: [{name: "enhancement"}, {name: "size: L"}]}' > "$ART/issue.json"

run_block() {  # flag issue pending-dir -> exit code of the whole node fragment
  ( cd "$GIT" && set -euo pipefail
    ISSUE="$2" ARTIFACTS_DIR="$ART" PH_TEST_PENDING="$3" PREVIEW_HELPER_CHANGESET_FLAG="$1"
    export ISSUE ARTIFACTS_DIR PH_TEST_PENDING PREVIEW_HELPER_CHANGESET_FLAG
    source "$BLOCK"
    echo node-continues )
}

echo "--- 15: the changeset file ---"
P1="$WORK/p1"
out=$(run_block "" 42 "$P1"); rc=$?
check "no_file_without_the_flag (guard)" bash -c '[ "$0" = 0 ] && [ "$1" = node-continues ] && [ ! -e "$2" ]' "$rc" "$out" "$P1"
out=$(run_block false 42 "$P1"); rc=$?
check "  ... nor with the flag set to anything but true" test ! -e "$P1"

P2="$WORK/p2"
out=$(run_block true 42 "$P2"); rc=$?
F="$P2/42-$HEAD_SHA.json"
check "one_valid_file_with_the_flag (one file, final name only)" \
  bash -c '[ "$0" = 0 ] && [ "$(ls -A "$1")" = "$(basename "$2")" ]' "$rc" "$P2" "$F"
check "  ... issue_number is a JSON integer and the six fields are exact" \
  test "$(jq -c '[(.issue_number | type), (keys)]' "$F")" = '["number",["changed_files","diff_stat","head_sha","issue_number","labels","title"]]'
check "  ... file names, title and labels round-trip" \
  test "$(jq -c '[.changed_files, .title, .labels]' "$F")" = '[["docs/b.md","we ird $(x).md"],"A title with \"quotes\" and $(x)",["enhancement","size: L"]]'
STATE="$WORK/state.json"; echo '{}' > "$STATE"; OUTJ="$WORK/input.json"
cand=$(python3 "$REPO/scripts/factory_core/preview_helper.py" next-candidate --pending-dir "$P2" \
  --state-file "$STATE" --out "$OUTJ"); rc=$?
check "  ... accepted by the intake (next-candidate)" test "$rc:$cand" = "0:42 $HEAD_SHA"

P3="$WORK/p3"; mkdir -p "$P3"; chmod 0555 "$P3"
out=$(run_block true 42 "$P3"); rc=$?
ok3=$([ "$rc" = 0 ] && [ "$out" = node-continues ] && echo 0 || echo 1)
P4="$WORK/p4"
out=$(run_block true "abc" "$P4"); rc=$?
ok4=$([ "$rc" = 0 ] && [ "$out" = node-continues ] && [ -z "$(ls -A "$P4" 2>/dev/null)" ] && echo 0 || echo 1)
mv "$ART/issue.json" "$ART/issue.json.away"
P5="$WORK/p5"
out=$(run_block true 42 "$P5"); rc=$?
ok5=$([ "$rc" = 0 ] && [ "$out" = node-continues ] && [ -z "$(ls -A "$P5" 2>/dev/null)" ] && echo 0 || echo 1)
mv "$ART/issue.json.away" "$ART/issue.json"
check "the_block_never_fails_the_node (unwritable dir, non-numeric ISSUE, missing issue.json; set -euo pipefail)" \
  test "$ok3$ok4$ok5" = "000"
if [ "$(id -u)" = 0 ]; then echo "  (as root the unwritable-dir case writes; the node still continues)"; fi

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
