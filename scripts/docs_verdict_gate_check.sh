#!/usr/bin/env bash
# Gate push-and-pr on the STATUS: verdict dark-factory-docs-conformance.md wrote to
# docs-conformance.md (Gate 2.5, #535). A sibling of verdict_gate_check.sh, not a flag on
# it: that script passes ERROR because Gate 3's code_review.fail_open contract says so,
# and this gate must never inherit that pass-through in enforce mode.
#
# Usage: docs_verdict_gate_check.sh <verdict-file> <issue-number> <gate-label> [<mode> [<changed-note> [<changed-kind>]]]
#   <verdict-file>   $ARTIFACTS_DIR/docs-conformance.md
#   <issue-number>   for the live needs-discussion re-check and the silent-death comment
#   <gate-label>     human string for the comment, e.g. "Docs Conformance (Gate 2.5)"
#   <mode>           docs_conformance.mode: "enforce", or anything else (including
#                    omitted/blank) = "observe", the shipped default
#   <changed-note>   non-empty when docs-conformance-gate found that HEAD or `git status`
#                    changed during the read-only docs-conformance node; says what changed.
#                    The decision is then "block" whatever STATUS: says.
#   <changed-kind>   "head" when the note is about HEAD having moved: push-and-pr would
#                    push a commit Gate 2 never saw, so this blocks in EVERY mode. It is
#                    a tripwire protecting Gate 2, not a docs verdict. Anything else
#                    (e.g. "status"): the note is governed by <mode>, like a verdict.
#
# observe: the docs verdict never blocks — exit 0 for every STATUS: and for a note that is
#   not kind "head". Log to stderr what enforce mode would have decided. No tracker
#   call, no comment, no label check — the command's own Phase 6 already posted an
#   informational comment for a BLOCKED verdict. A kind-"head" note takes the enforce
#   path below instead.
# enforce: exit 0 only for STATUS: PASS or SKIPPED. BLOCKED, ERROR, missing or
#   unparseable exit 1. A live needs-discussion re-check decides messaging only:
#     - BLOCKED with no <changed-note>, or needs-discussion present: exit 1 quietly (the
#       command already posted).
#     - otherwise (ERROR/missing/unparseable, or a <changed-note>, which nothing upstream
#       explained): post an idempotent <!-- df-push-gate-failure --> marker comment, then
#       exit 1.
#
# The exit code IS the gate signal — do not wrap this call in `|| true`.
set -uo pipefail

_USAGE="Usage: docs_verdict_gate_check.sh <verdict-file> <issue-number> <gate-label> [<mode> [<changed-note> [<changed-kind>]]]"
VERDICT_FILE="${1:?$_USAGE}"
ISSUE_NUM="${2:?$_USAGE}"
GATE_LABEL="${3:?$_USAGE}"
MODE="${4:-observe}"
[ "$MODE" = "enforce" ] || MODE="observe"
CHANGED_NOTE="${5:-}"
CHANGED_KIND="${6:-}"

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
_WHY="STATUS=${STATUS:-missing}"
HEAD_MOVED=0
if [ -n "$CHANGED_NOTE" ]; then
  DECISION="block"
  _WHY="${_WHY}; the branch changed during the read-only docs-conformance node: ${CHANGED_NOTE}"
  [ "$CHANGED_KIND" = "head" ] && HEAD_MOVED=1
fi

if [ "$MODE" = "observe" ] && [ "$HEAD_MOVED" -eq 0 ]; then
  echo "docs_verdict_gate_check.sh: ${GATE_LABEL} observe mode — enforce would ${DECISION} issue #${ISSUE_NUM} (${_WHY}); proceeding." >&2
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

if { [ "$STATUS" = "BLOCKED" ] && [ -z "$CHANGED_NOTE" ]; } || [ "${HAS_NEEDS_DISCUSSION:-0}" -gt 0 ]; then
  echo "docs_verdict_gate_check.sh: ${GATE_LABEL} blocks issue #${ISSUE_NUM} (${_WHY}); upstream already communicated, no comment needed." >&2
  exit 1
fi

echo "docs_verdict_gate_check.sh: ${GATE_LABEL} blocks issue #${ISSUE_NUM} (${_WHY}) and no needs-discussion label — failing closed and posting failure comment." >&2
_MODE_WHY="\`docs_conformance.mode\` is \`enforce\`, so this blocks the PR instead of advancing silently."
_FAIL_WHY="No usable verdict was recorded for this run (\`${VERDICT_FILE}\` is missing, unparseable, or \`STATUS: ${STATUS:-missing}\`). ${_MODE_WHY}"
if [ "$HEAD_MOVED" -eq 1 ]; then
  _FAIL_WHY="A commit was made during the read-only docs-conformance node (${CHANGED_NOTE}). Gate 2 never reviewed it, so the push is stopped whatever \`docs_conformance.mode\` is."
elif [ -n "$CHANGED_NOTE" ]; then
  _FAIL_WHY="The working tree changed during the read-only docs-conformance node (${CHANGED_NOTE}). ${_MODE_WHY}"
fi
_FOOTER=$(python3 "${SCRIPT_DIR}/factory_core/cli.py" marker factory 2>/dev/null || echo "")
_FAIL_BODY="<!-- df-push-gate-failure -->
## ${GATE_LABEL} — Blocked

${_FAIL_WHY}

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
