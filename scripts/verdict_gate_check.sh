#!/usr/bin/env bash
# Gate a downstream DAG node on the STATUS: verdict a gate command already wrote
# (scripts/gate_lib.sh's emit_verdict()) to conformance.md/review.md, instead of
# trusting the upstream command: node's own completion status — a command: node's
# internal `exit 1` does not reliably surface to the Archon executor (#212, reproduced
# again one gate later for push-and-pr/status-in-review in #271).
#
# Usage: verdict_gate_check.sh <verdict-file> <issue-number> <gate-label>
#   <verdict-file>   e.g. $ARTIFACTS_DIR/conformance.md or $ARTIFACTS_DIR/review.md
#   <issue-number>   for the live needs-discussion re-check and the silent-death comment
#   <gate-label>     human string for the comment, e.g. "Conformance (Gate 2)"
#
# Exit 0 (proceed): the file exists and its STATUS: line is PASS, SKIPPED, or ERROR
#   (ERROR only appears in review.md — code_review.fail_open's contract is "never
#   block", per commands/dark-factory-code-review.md).
# Exit 1 (block): STATUS: is BLOCKED, or the file is missing/unparseable. A live
#   needs-discussion re-check decides messaging only, never the block decision:
#     - BLOCKED, or missing+needs-discussion-present: exit 1 quietly — the
#       originating phase (conformance/code-review/validate's blast-radius gate)
#       already posted its own comment.
#     - missing+needs-discussion-absent (true silent death, nothing upstream
#       explained anything): post an idempotent <!-- df-push-gate-failure --> marker
#       comment, then exit 1.
#
# The exit code IS the gate signal for the caller (unlike push_gate_check.sh, which
# always exits 0 and lets its caller branch on stdout) — do not wrap this call in `|| true`.
set -uo pipefail

VERDICT_FILE="${1:?Usage: verdict_gate_check.sh <verdict-file> <issue-number> <gate-label>}"
ISSUE_NUM="${2:?Usage: verdict_gate_check.sh <verdict-file> <issue-number> <gate-label>}"
GATE_LABEL="${3:?Usage: verdict_gate_check.sh <verdict-file> <issue-number> <gate-label>}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_PCLI="${SCRIPT_DIR}/factory_core/providers/cli.py"

STATUS=""
if [ -f "$VERDICT_FILE" ]; then
  STATUS=$(grep -m1 '^STATUS:' "$VERDICT_FILE" 2>/dev/null | awk '{print $2}')
fi

case "$STATUS" in
  PASS|SKIPPED|ERROR)
    exit 0
    ;;
esac

# Guard against a non-numeric issue number (e.g. a stringified "null" from a bad
# tracker lookup) reaching the tracker CLI or a comment marker — fail closed with no
# tracker call, mirroring push_gate_check.sh's identical guard.
case "$ISSUE_NUM" in
  ''|*[!0-9]*)
    echo "verdict_gate_check.sh: ${GATE_LABEL} — issue number '${ISSUE_NUM}' is not numeric; failing closed without a tracker call." >&2
    exit 1
    ;;
esac

# Blocking path: STATUS is BLOCKED, or missing/unparseable. Live-check needs-discussion
# for messaging only — this never changes the block decision itself.
HAS_NEEDS_DISCUSSION=$(python3 "$_PCLI" tracker get --id "$ISSUE_NUM" --fields labels 2>/dev/null \
  | jq -r '.labels[].name' 2>/dev/null \
  | grep -Fxc 'needs-discussion' || true)

if [ "$STATUS" = "BLOCKED" ] || [ "${HAS_NEEDS_DISCUSSION:-0}" -gt 0 ]; then
  echo "verdict_gate_check.sh: ${GATE_LABEL} blocks issue #${ISSUE_NUM} (STATUS=${STATUS:-missing}); upstream already communicated, no comment needed." >&2
  exit 1
fi

echo "verdict_gate_check.sh: ${GATE_LABEL} verdict missing/unparseable for issue #${ISSUE_NUM} and no needs-discussion label — true silent death, posting failure comment." >&2
_FOOTER=$(python3 "${SCRIPT_DIR}/factory_core/cli.py" marker factory 2>/dev/null || echo "")
_FAIL_BODY="<!-- df-push-gate-failure -->
## ${GATE_LABEL} — Blocked

No verdict was recorded for this run (\`${VERDICT_FILE}\` is missing or unparseable). Treating this as a hard block rather than advancing silently.

\`\`\`bash
# Retry manually if needed
docker compose --profile factory run --rm dark-factory \"Continue issue #${ISSUE_NUM}\"
\`\`\`

---
${_FOOTER}"
TMPFILE=$(mktemp /tmp/push-gate-failure-XXXXXX.md)
printf '%s' "$_FAIL_BODY" > "$TMPFILE"
python3 "$_PCLI" tracker comment --id "$ISSUE_NUM" --marker "<!-- df-push-gate-failure -->" --body-file "$TMPFILE"
rm -f "$TMPFILE"
exit 1
