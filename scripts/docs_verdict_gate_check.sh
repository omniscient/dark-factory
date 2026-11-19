#!/usr/bin/env bash
# Gate push-and-pr on the STATUS: verdict dark-factory-docs-conformance.md wrote to
# docs-conformance.md (Gate 2.5, #535). A sibling of verdict_gate_check.sh, not a flag on
# it: that script passes ERROR because Gate 3's code_review.fail_open contract says so,
# and this gate must never inherit that pass-through in enforce mode.
#
# Usage: docs_verdict_gate_check.sh <verdict-file> <issue-number> <gate-label> [<mode>]
#   <verdict-file>   $ARTIFACTS_DIR/docs-conformance.md
#   <issue-number>   for the live needs-discussion re-check and the silent-death comment
#   <gate-label>     human string for the comment, e.g. "Docs Conformance (Gate 2.5)"
#   <mode>           docs_conformance.mode: "enforce", or anything else (including
#                    omitted/blank) = "observe", the shipped default
#
# observe: always exit 0. Log to stderr what enforce mode would have decided. No tracker
#   call, no comment, no label check — the command's own Phase 6 already posted an
#   informational comment for a BLOCKED verdict.
# enforce: exit 0 only for STATUS: PASS or SKIPPED. BLOCKED, ERROR, missing or
#   unparseable exit 1. A live needs-discussion re-check decides messaging only:
#     - BLOCKED, or needs-discussion present: exit 1 quietly (the command already posted).
#     - otherwise (ERROR/missing/unparseable, nothing upstream explained it): post an
#       idempotent <!-- df-push-gate-failure --> marker comment, then exit 1.
#
# The exit code IS the gate signal — do not wrap this call in `|| true`.
set -uo pipefail

_USAGE="Usage: docs_verdict_gate_check.sh <verdict-file> <issue-number> <gate-label> [<mode>]"
VERDICT_FILE="${1:?$_USAGE}"
ISSUE_NUM="${2:?$_USAGE}"
GATE_LABEL="${3:?$_USAGE}"
MODE="${4:-observe}"
[ "$MODE" = "enforce" ] || MODE="observe"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_PCLI="${SCRIPT_DIR}/factory_core/providers/cli.py"

STATUS=""
if [ -f "$VERDICT_FILE" ]; then
  STATUS=$(grep -m1 '^STATUS:' "$VERDICT_FILE" 2>/dev/null | awk '{print $2}')
fi

# One STATUS-to-decision mapping for both modes, so observe logs exactly what enforce does.
case "$STATUS" in
  PASS|SKIPPED) DECISION="proceed" ;;
  *)            DECISION="block" ;;
esac

if [ "$MODE" = "observe" ]; then
  echo "docs_verdict_gate_check.sh: ${GATE_LABEL} observe mode — enforce would ${DECISION} issue #${ISSUE_NUM} (STATUS=${STATUS:-missing}); proceeding." >&2
  exit 0
fi

[ "$DECISION" = "proceed" ] && exit 0

# Guard against a non-numeric issue number reaching the tracker CLI or a comment marker —
# fail closed with no tracker call, mirroring verdict_gate_check.sh.
case "$ISSUE_NUM" in
  ''|*[!0-9]*)
    echo "docs_verdict_gate_check.sh: ${GATE_LABEL} — issue number '${ISSUE_NUM}' is not numeric; failing closed without a tracker call." >&2
    exit 1
    ;;
esac

HAS_NEEDS_DISCUSSION=$(python3 "$_PCLI" tracker get --id "$ISSUE_NUM" --fields labels 2>/dev/null \
  | jq -r '.labels[].name' 2>/dev/null \
  | grep -Fxc 'needs-discussion' || true)

if [ "$STATUS" = "BLOCKED" ] || [ "${HAS_NEEDS_DISCUSSION:-0}" -gt 0 ]; then
  echo "docs_verdict_gate_check.sh: ${GATE_LABEL} blocks issue #${ISSUE_NUM} (STATUS=${STATUS:-missing}); upstream already communicated, no comment needed." >&2
  exit 1
fi

echo "docs_verdict_gate_check.sh: ${GATE_LABEL} verdict ${STATUS:-missing} for issue #${ISSUE_NUM} and no needs-discussion label — failing closed and posting failure comment." >&2
_FOOTER=$(python3 "${SCRIPT_DIR}/factory_core/cli.py" marker factory 2>/dev/null || echo "")
_FAIL_BODY="<!-- df-push-gate-failure -->
## ${GATE_LABEL} — Blocked

No usable verdict was recorded for this run (\`${VERDICT_FILE}\` is missing, unparseable, or \`STATUS: ${STATUS:-missing}\`). \`docs_conformance.mode\` is \`enforce\`, so this blocks the PR instead of advancing silently.

\`\`\`bash
# Retry manually if needed
docker compose --profile factory run --rm dark-factory \"Continue issue #${ISSUE_NUM}\"
\`\`\`

---
${_FOOTER}"
TMPFILE=$(mktemp /tmp/docs-gate-failure-XXXXXX.md)
printf '%s' "$_FAIL_BODY" > "$TMPFILE"
python3 "$_PCLI" tracker comment --id "$ISSUE_NUM" --marker "<!-- df-push-gate-failure -->" --body-file "$TMPFILE"
rm -f "$TMPFILE"
exit 1
