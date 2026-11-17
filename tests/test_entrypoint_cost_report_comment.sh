#!/usr/bin/env bash
# Behavioral test (#507) for post_cost_report()'s comment upsert now that it goes through
# `providers/cli.py tracker find-comment | update-comment | add-comment` instead of inline
# `gh api` / `gh issue comment`. The provider CLI runs `gh` as a SUBPROCESS, so `gh` is a
# PATH shim here (a shell-function stub would not intercept it). Asserts, per case, the exact
# gh argv, that the prior comment body reaches the renderer, and that failures keep the old
# non-fatal WARNING behaviour.
# Run: bash tests/test_entrypoint_cost_report_comment.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export GH_TOKEN="stub-token"
export CLAUDE_CODE_OAUTH_TOKEN="stub-token"
export IDENTITY_SH="$REPO_ROOT/scripts/identity.sh"
export FACTORY_PROVIDERS_CLI="$REPO_ROOT/scripts/factory_core/providers/cli.py"
# identity.SLUG (python) and FACTORY_REPO_SLUG (bash) both derive from these two.
export FACTORY_OWNER=acme FACTORY_REPO=widgets

_REAL_PY3="$(command -v python3)"
git() { return 0; }
export -f git
docker() { return 0; }
export -f docker
claude() { echo "stub"; return 0; }
export -f claude
archon() { echo "{}"; return 0; }
export -f archon

CURRENT_RUN_DIR=$(mktemp -d /tmp/ep-crc-rundir-XXXXXX)
export CURRENT_RUN_DIR

ENTRYPOINT_SOURCE_ONLY=1 source "$SCRIPT_DIR/../entrypoint.sh"

trap - ERR
set +e; set +u; set +o pipefail

WORK=$(mktemp -d /tmp/ep-crc-work-XXXXXX)
SHIM_DIR="$WORK/bin"; mkdir -p "$SHIM_DIR"
export GH_LOG="$WORK/gh.log" GH_PATCHED_BODY="$WORK/patched-body.txt" GH_POSTED_BODY="$WORK/posted-body.txt"
cat > "$SHIM_DIR/gh" <<'SHIM'
#!/usr/bin/env bash
echo "$*" >> "$GH_LOG"
case "$*" in
  "api repos/"*"/issues/"*"/comments --jq "*) printf '%s' "${GH_LIST_OUT:-}"; exit "${GH_LIST_RC:-0}" ;;
  "api repos/"*"/issues/comments/"*" --jq .body") printf '%s' "${GH_BODY_OUT:-}"; exit 0 ;;
  "api repos/"*"/issues/comments/"*" --method PATCH -F body=@"*)
    last="${!#}"; cp "${last#body=@}" "$GH_PATCHED_BODY"; exit "${GH_PATCH_RC:-0}" ;;
  "issue comment "*) exit "${GH_POST_RC:-0}" ;;
esac
exit 0
SHIM
chmod +x "$SHIM_DIR/gh"
export PATH="$SHIM_DIR:$PATH"

# Real provider CLI; only the two cost-report CLI calls are stubbed (render records the
# prior body it was handed and prints a fixed report).
PRIOR_SEEN="$WORK/prior-seen.txt"
python3() {
  case "$*" in
    *cost-report-check*) return 0 ;;
    *cost-report-render*)
      local prior="" prev="" a
      for a in "$@"; do [ "$prev" = "--prior-body-file" ] && prior="$a"; prev="$a"; done
      [ -n "$prior" ] && cat "$prior" > "$PRIOR_SEEN"
      echo "RENDERED REPORT"; return 0 ;;
    *) "$_REAL_PY3" "$@" ;;
  esac
}

ARTIFACTS_DIR="$WORK/artifacts"; mkdir -p "$ARTIFACTS_DIR"
echo '{}' > "$ARTIFACTS_DIR/run-record.json"
CLONE_DIR="$WORK/clone"
ISSUE_NUM=507
INTENT=fix
RUN_ID=run-crc-1
SLUG="acme/widgets"

PASSED=0; FAILED=0
assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else echo "  FAIL: $desc -- expected='$expected' got='$actual'" >&2; FAILED=$((FAILED+1)); fi
}
reset() { : > "$GH_LOG"; rm -f "$PRIOR_SEEN" "$GH_PATCHED_BODY"; unset GH_LIST_OUT GH_LIST_RC GH_BODY_OUT GH_PATCH_RC GH_POST_RC; }
MARKER_JQ='[.[] | select(.body | contains("'"$COST_MARKER"'"))] | last | .id // empty'

echo "--- A: no prior cost-report comment -> new comment, no prior body ---"
reset
OUT=$(post_cost_report 2>&1); RC=$?
assert_eq "A: returns 0" "0" "$RC"
assert_eq "A: gh call 1 is the marker list (exact argv)" \
  "api repos/$SLUG/issues/$ISSUE_NUM/comments --jq $MARKER_JQ" "$(sed -n 1p "$GH_LOG")"
assert_eq "A: gh call 2 posts the rendered report" \
  "issue comment $ISSUE_NUM --repo $SLUG --body RENDERED REPORT" "$(sed -n 2p "$GH_LOG")"
assert_eq "A: exactly two gh calls" "2" "$(grep -cE "^(api|issue) " "$GH_LOG")"
assert_eq "A: renderer was not handed a prior body" "no" "$([ -f "$PRIOR_SEEN" ] && echo yes || echo no)"

echo "--- B: prior comment exists -> body fetched, passed to renderer, PATCHed in place ---"
reset
export GH_LIST_OUT=777 GH_BODY_OUT="PRIOR REPORT BODY"
OUT=$(post_cost_report 2>&1); RC=$?
assert_eq "B: returns 0" "0" "$RC"
assert_eq "B: gh call 2 reads the single-comment endpoint (no issue number in the path)" \
  "api repos/$SLUG/issues/comments/777 --jq .body" "$(sed -n 2p "$GH_LOG")"
assert_eq "B: renderer received the prior body" "PRIOR REPORT BODY" "$(cat "$PRIOR_SEEN" 2>/dev/null)"
assert_eq "B: gh call 3 is the PATCH on the same endpoint" \
  "1" "$(sed -n 3p "$GH_LOG" | grep -c "^api repos/$SLUG/issues/comments/777 --method PATCH -F body=@")"
assert_eq "B: PATCH carried the rendered report" "RENDERED REPORT" "$(cat "$GH_PATCHED_BODY" 2>/dev/null)"
assert_eq "B: no new comment was created" "0" "$(grep -c '^issue comment' "$GH_LOG")"

echo "--- C: PATCH fails -> still non-fatal, WARNING printed ---"
reset
export GH_LIST_OUT=777 GH_PATCH_RC=1
OUT=$(post_cost_report 2>&1); RC=$?
assert_eq "C: returns 0" "0" "$RC"
assert_eq "C: warns about the failed update" "1" "$(echo "$OUT" | grep -c 'WARNING: Could not update cost report comment 777')"

echo "--- D: post fails -> still non-fatal, WARNING printed ---"
reset
export GH_POST_RC=1
OUT=$(post_cost_report 2>&1); RC=$?
assert_eq "D: returns 0" "0" "$RC"
assert_eq "D: warns about the failed post" "1" "$(echo "$OUT" | grep -c 'WARNING: Could not post cost report')"

echo "--- E: marker lookup fails -> treated as 'no prior comment' (old '|| true') ---"
reset
export GH_LIST_RC=1 GH_LIST_OUT=777
OUT=$(post_cost_report 2>&1); RC=$?
assert_eq "E: returns 0" "0" "$RC"
assert_eq "E: falls through to a new comment" "1" "$(grep -c '^issue comment' "$GH_LOG")"

rm -rf "$WORK" "$CURRENT_RUN_DIR"
echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
