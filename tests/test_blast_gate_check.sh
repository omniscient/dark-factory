#!/usr/bin/env bash
# Covers #551: scripts/blast_gate_check.sh — exit 0/1/2 mapping (ERROR and empty output
# are 2), fetch failure is 2, run vs close head resolution, ambiguous branch refused,
# hotspots source per mode, a signed approval passing end to end, and the --hold steps.
# Tracker calls are stubbed with an exported python3 function (tests/test_verdict_gate_check.sh
# convention); git runs for real against a local bare "origin".
# Run: bash tests/test_blast_gate_check.sh
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK="${REPO_ROOT}/scripts/blast_gate_check.sh"

WORK=$(mktemp -d /tmp/bgc-test-XXXXXX)
export GNUPGHOME="$WORK/gnupg"
cleanup() {
  gpgconf --homedir "$GNUPGHOME" --kill all >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

command -v gpg >/dev/null || { echo "FAIL: gpg is required"; exit 1; }

PASSED=0; FAILED=0
ok()   { echo "  PASS: $1"; PASSED=$((PASSED+1)); }
bad()  { echo "  FAIL: $1" >&2; FAILED=$((FAILED+1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

export STUB_LOG="$WORK/stub.log"
_REAL_PY3="$(command -v python3)"
export _REAL_PY3
python3() {
  case "$*" in
    *providers/cli.py*tracker\ label*)
      # Records whether the hold marker already exists when the first side effect runs.
      [ -f "${FACTORY_BLAST_HOLD_FILE:-$ARTIFACTS_DIR/blast-hold.json}" ] && echo "marker-before-label" >> "$STUB_LOG"
      echo "providers $*" >> "$STUB_LOG"; echo "tracker-stdout-noise"; return 0 ;;
    *providers/cli.py*) echo "providers $*" >> "$STUB_LOG"; echo "tracker-stdout-noise"; return 0 ;;
    *factory_core/cli.py*marker*) echo "*Posted by Test*" ;;
    *) "$_REAL_PY3" "$@" ;;
  esac
}
export -f python3

# Never inherit a live run's hold-marker path (#551 architect cycle 2): a factory run that
# executes this test must not get a fake hold marker written into its own artifacts.
unset FACTORY_BLAST_HOLD_FILE
export FACTORY_REPO_SLUG="test/repo"
export SCHEDULER_STATE_DIR="$WORK/state"
export ARTIFACTS_DIR="$WORK/artifacts"
export FACTORY_CONFIG_PATH="$WORK/absent-baked.yaml"
export BLAST_GATE_BAKED_DIR="$WORK/no-baked-gate"
mkdir -p "$SCHEDULER_STATE_DIR" "$ARTIFACTS_DIR"

g() { git -C "$CLONE_DIR" "$@"; }

# --- Fixture: origin with main (trusted key) + feature branches --------------------
mkdir -m 700 "$GNUPGHOME"
gpg --batch --passphrase '' --quick-gen-key 'Op <op@example.test>' ed25519 sign 1y >/dev/null 2>&1
git init -q --bare -b main "$WORK/origin.git"
export CLONE_DIR="$WORK/clone"
git clone -q "$WORK/origin.git" "$CLONE_DIR" 2>/dev/null
g config user.email t@example.test; g config user.name T
mkdir -p "$CLONE_DIR/.claude/skills/refinement" "$CLONE_DIR/.claude/skills/x" "$CLONE_DIR/.factory" "$CLONE_DIR/src"
printf 'blast_radius:\n  enabled: true\n  hotspot_score_floor: 5.0\n  size_budget_lines: 400\n  size_budget_blocks: false\n' \
  > "$CLONE_DIR/.claude/skills/refinement/config.yaml"
echo "echo v0" > "$CLONE_DIR/.claude/skills/x/run.sh"
echo "print(0)" > "$CLONE_DIR/src/app.py"
gpg --batch --armor --export > "$CLONE_DIR/.factory/blast-approvers.asc"
g add -A; g commit -qm main; g push -q origin main 2>/dev/null

g checkout -qb feat/issue-77-risky
echo "echo v1" > "$CLONE_DIR/.claude/skills/x/run.sh"; g commit -qam risky
g push -q origin feat/issue-77-risky 2>/dev/null
RISKY_SHA=$(g rev-parse HEAD)
g checkout -q main; g checkout -qb feat/issue-78-clean
echo "print(1)" > "$CLONE_DIR/src/app.py"; g commit -qam clean
g push -q origin feat/issue-78-clean 2>/dev/null
for b in feat/issue-79-a feat/issue-79-b feat/issue-770-other; do g push -q origin "HEAD:refs/heads/$b" 2>/dev/null; done

_run() {  # _run <args...>  -> sets RC, OUT_TXT
  : > "$STUB_LOG"
  OUT_TXT=$(bash "$CHECK" "$@" 2>"$WORK/stderr.log"); RC=$?
}

echo "--- run mode: verdict mapping ---"
g checkout -q feat/issue-78-clean
_run --issue 78 --mode run --out "$WORK/b.md"
check "PASS → exit 0" '[ "$RC" = 0 ]'
check "PASS → HEAD_SHA printed" '[ "$OUT_TXT" = "HEAD_SHA=$(g rev-parse HEAD)" ]'
check "PASS → --out written" 'grep -qx "STATUS: PASS" "$WORK/b.md"'
check "PASS → no tracker calls" '[ ! -s "$STUB_LOG" ]'
check "trusted ref created by the checker" 'g rev-parse -q --verify refs/blast-gate/trusted-main >/dev/null'

g checkout -q feat/issue-77-risky
_run --issue 77 --mode run --out "$WORK/b.md"
check "HUMAN_REQUIRED → exit 1" '[ "$RC" = 1 ]'
check "HUMAN_REQUIRED → APPROVAL: none" 'grep -qx "APPROVAL: none" "$WORK/b.md"'
check "no --hold → no tracker calls" '[ ! -s "$STUB_LOG" ]'

echo "--- hotspots: working tree (plus trusted main) in run mode, trusted main in close mode ---"
# Operator review of PR #571: run mode must keep triggering on the regenerated
# working-tree hotspot report exactly as validate Phase 0 always did (spec B.4).
g checkout -q feat/issue-78-clean
mkdir -p "$CLONE_DIR/docs"; echo "  9.0  src/app.py  (1d / 1t)  1 loc" > "$CLONE_DIR/docs/codeindex-hotspots.md"
_run --issue 78 --mode run --out "$WORK/b.md"
check "working-tree hotspot → exit 1 in run mode" '[ "$RC" = 1 ]'
check "working-tree hotspot → HUMAN_REQUIRED" 'grep -qx "STATUS: HUMAN_REQUIRED" "$WORK/b.md"'
_run --issue 78 --mode close
check "close mode reads main's hotspots (none) → exit 0" '[ "$RC" = 0 ]'
rm -rf "$CLONE_DIR/docs"

echo "--- path triggers survive a non-ASCII name and a rename ---"
# Operator review of PR #571: `git diff --name-only` C-quotes a non-ASCII path, so no
# anchored pattern matched it, and rename detection reported only the destination, so
# moving a triggered file out of its directory passed.
g checkout -q main; g checkout -qb feat/issue-80-nonascii
printf 'echo x\n' > "$CLONE_DIR/.claude/skills/x/$(printf '\303\251').sh"
g add -A; g commit -qm nonascii
_run --issue 80 --mode run --out "$WORK/b.md"
check "non-ASCII path under a triggered directory → exit 1" '[ "$RC" = 1 ]'
check "non-ASCII path → HUMAN_REQUIRED" 'grep -qx "STATUS: HUMAN_REQUIRED" "$WORK/b.md"'
g checkout -q main; g checkout -qb feat/issue-81-moved
mkdir -p "$CLONE_DIR/elsewhere"; g mv .claude/skills/x/run.sh elsewhere/run.sh; g commit -qm moved
_run --issue 81 --mode run --out "$WORK/b.md"
check "triggered file moved out of its directory → exit 1" '[ "$RC" = 1 ]'
check "moved file → HUMAN_REQUIRED" 'grep -qx "STATUS: HUMAN_REQUIRED" "$WORK/b.md"'
# Round 2: a path is judged and bound exactly as git names it. Stripping it made
# "run.sh " (trailing space) ride an approval for run.sh; a name git still C-quotes
# (tab, quote, newline) matched no trigger, so the checker fails closed on it.
g checkout -q main; g checkout -qb feat/issue-82-space
printf 'echo evil\n' > "$CLONE_DIR/.claude/skills/x/run.sh "
g add -A; g commit -qm space
_run --issue 82 --mode run --out "$WORK/b.md"
check "trailing-space sibling → exit 1" '[ "$RC" = 1 ]'
check "trailing-space sibling is listed under its own name" 'grep -qF "run.sh \"" "$WORK/b.md"'
g checkout -q main; g checkout -qb feat/issue-83-tab
printf 'echo x\n' > "$CLONE_DIR/.claude/skills/x/a$(printf '\t')b.sh"
g add -A; g commit -qm tab
_run --issue 83 --mode run --out "$WORK/b.md"
check "path git must quote (tab) → checker failure, exit 2" '[ "$RC" = 2 ]'
check "quoted path → fail-closed HUMAN_REQUIRED stub" 'grep -qx "STATUS: HUMAN_REQUIRED" "$WORK/b.md"'

echo "--- signed approval passes end to end ---"
g checkout -q feat/issue-77-risky
mkdir -p "$SCHEDULER_STATE_DIR/blast-approvals"
BASE=$(g merge-base refs/blast-gate/trusted-main HEAD)
REC="$SCHEDULER_STATE_DIR/blast-approvals/77.json"
PYTHONPATH="$REPO_ROOT/scripts" "$_REAL_PY3" - "$CLONE_DIR" "$BASE" "$REC" <<'EOF'
import sys, gate_blast_radius as g
clone, base, out = sys.argv[1:]
p = ".claude/skills/x/run.sh"
rec = {"schema": 1, "issue": 77, "repo": "test/repo", "branch": "feat/issue-77-risky",
       "head_sha": "x", "base_sha": base, "size": None, "trigger": "skill-security",
       "files": {p: {"base": g.blob_entry(clone, base, p), "head": g.blob_entry(clone, "HEAD", p)}},
       "approved_at": "2026-11-21T00:00:00Z", "approved_by": "Op", "approvals_count": 1,
       "previous_head_sha": None}
open(out, "wb").write(g.canonical_bytes(rec))
EOF
gpg --batch --yes --detach-sign -o "$REC.sig" "$REC" 2>/dev/null
_run --issue 77 --mode run --out "$WORK/b.md"
check "matching signed approval → exit 0" '[ "$RC" = 0 ]'
check "matching signed approval → APPROVAL: matched" 'grep -q "^APPROVAL: matched (Op at " "$WORK/b.md"'
_run --issue 77 --mode close
check "same approval honoured in close mode" '[ "$RC" = 0 ]'
rm -f "$REC" "$REC.sig"

echo "--- exit 2 cases ---"
_run --issue abc --mode run
check "non-numeric issue → exit 2" '[ "$RC" = 2 ]'
_run --issue 77 --mode sideways
check "bad mode → exit 2" '[ "$RC" = 2 ]'
mkdir -p "$WORK/fake-gate"
printf 'print("STATUS: ERROR")\n' > "$WORK/fake-gate/gate_blast_radius.py"
BLAST_GATE_BAKED_DIR="$WORK/fake-gate" _run --issue 77 --mode run --out "$WORK/b.md"
check "STATUS: ERROR → exit 2 (not a pass value)" '[ "$RC" = 2 ]'
check "baked gate preferred over the clone copy" 'grep -qx "STATUS: ERROR" "$WORK/b.md"'
printf '' > "$WORK/fake-gate/gate_blast_radius.py"
BLAST_GATE_BAKED_DIR="$WORK/fake-gate" _run --issue 77 --mode run
check "empty gate output → exit 2" '[ "$RC" = 2 ]'
g remote set-url origin "$WORK/missing.git"
echo "STATUS: PASS" > "$WORK/stale.md"
_run --issue 77 --mode run --out "$WORK/stale.md"
check "fetch failure → exit 2" '[ "$RC" = 2 ]'
check "fetch failure → stale --out replaced by a fail-closed stub" 'grep -qx "STATUS: HUMAN_REQUIRED" "$WORK/stale.md" && grep -qx "TRIGGER: checker-error" "$WORK/stale.md"'
g remote set-url origin "$WORK/origin.git"

echo "--- close mode: head resolution ---"
g checkout -q main
_run --issue 77 --mode close --out "$WORK/b.md"
check "close resolves origin's feat/issue-77-* (not local HEAD) → exit 1" '[ "$RC" = 1 ]'
check "close prints the origin head" '[ "$OUT_TXT" = "HEAD_SHA=$RISKY_SHA" ]'
check "close ignores feat/issue-770-*" '! grep -q "found" "$WORK/stderr.log"'
check "temporary worktree removed" '[ "$(g worktree list | wc -l)" = 1 ]'
_run --issue 79 --mode close
check "two feat/issue-79-* branches → exit 2" '[ "$RC" = 2 ]'
_run --issue 80 --mode close
check "no feat/issue-80-* branch → exit 2" '[ "$RC" = 2 ]'

echo "--- --hold, run mode ---"
g checkout -q feat/issue-77-risky
echo "echo v2" > "$CLONE_DIR/.claude/skills/x/run.sh"; g commit -qam "local only"
LOCAL_SHA=$(g rev-parse HEAD)
rm -f "$ARTIFACTS_DIR/blast-hold.json"
_run --issue 77 --mode run --out "$WORK/b.md" --hold
check "hold keeps exit 1" '[ "$RC" = 1 ]'
check "hold stdout is only the HEAD_SHA line" '[ "$OUT_TXT" = "HEAD_SHA=$LOCAL_SHA" ]'
check "marker written before the first tracker call" 'grep -q "marker-before-label" "$STUB_LOG"'
check "hold pushed the branch (no PR)" '[ "$(git -C "$WORK/origin.git" rev-parse refs/heads/feat/issue-77-risky)" = "$LOCAL_SHA" ]'
REQ="$SCHEDULER_STATE_DIR/blast-approvals/77.request.json"
check "request written" '[ "$(jq -r .mode "$REQ")" = run ] && [ "$(jq -r .head_sha "$REQ")" = "$LOCAL_SHA" ]'
check "request lists the triggered path" '[ "$(jq -c .triggered_paths "$REQ")" = "[\".claude/skills/x/run.sh\"]" ]'
check "needs-discussion added" 'grep -q "tracker label --id 77 --add needs-discussion" "$STUB_LOG"'
check "board set to Blocked" 'grep -q "tracker set-status --id 77 --status blocked" "$STUB_LOG"'
check "df-blast-hold comment posted" 'grep -q "tracker comment --id 77 --marker <!-- df-blast-hold -->" "$STUB_LOG"'
check "no PR call" '! grep -q "codehost" "$STUB_LOG"'
check "blast-hold.json written" '[ "$(jq -c . "$ARTIFACTS_DIR/blast-hold.json")" = "{\"issue\":77,\"head_sha\":\"$LOCAL_SHA\",\"exit\":1}" ]'

echo "--- --hold writes the marker to FACTORY_BLAST_HOLD_FILE (entrypoint's directory) ---"
mkdir -p "$WORK/entrypoint-artifacts"
rm -f "$ARTIFACTS_DIR/blast-hold.json"
FACTORY_BLAST_HOLD_FILE="$WORK/entrypoint-artifacts/blast-hold.json" _run --issue 77 --mode run --hold
check "marker at FACTORY_BLAST_HOLD_FILE" '[ "$(jq -r .head_sha "$WORK/entrypoint-artifacts/blast-hold.json")" = "$LOCAL_SHA" ]'
check "nothing in the node's own ARTIFACTS_DIR" '[ ! -f "$ARTIFACTS_DIR/blast-hold.json" ]'

echo "--- --hold under BENCH_MODE=stub does not push ---"
echo "echo v3" > "$CLONE_DIR/.claude/skills/x/run.sh"; g commit -qam "bench local"
BENCH_MODE=stub _run --issue 77 --mode run --hold
check "bench hold exit 1" '[ "$RC" = 1 ]'
check "bench hold did not push" '[ "$(git -C "$WORK/origin.git" rev-parse refs/heads/feat/issue-77-risky)" = "$LOCAL_SHA" ]'

echo "--- --hold, close mode ---"
rm -f "$ARTIFACTS_DIR/blast-hold.json"
g checkout -q main
_run --issue 77 --mode close --hold
check "close hold exit 1" '[ "$RC" = 1 ]'
check "close request mode" '[ "$(jq -r .mode "$REQ")" = close ]'
check "close hold labels" 'grep -q "tracker label --id 77 --add needs-discussion" "$STUB_LOG"'
check "close hold does not move the board" '! grep -q "set-status" "$STUB_LOG"'
check "close hold writes no blast-hold.json" '[ ! -f "$ARTIFACTS_DIR/blast-hold.json" ]'

echo "--- --hold on a checker failure (exit 2) ---"
g checkout -q feat/issue-77-risky
BLAST_GATE_BAKED_DIR="$WORK/fake-gate" _run --issue 77 --mode run --hold
check "checker failure hold keeps exit 2" '[ "$RC" = 2 ]'
check "checker failure still labels" 'grep -q "add needs-discussion" "$STUB_LOG"'
check "checker failure blast-hold.json exit 2" '[ "$(jq -r .exit "$ARTIFACTS_DIR/blast-hold.json")" = 2 ]'

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
