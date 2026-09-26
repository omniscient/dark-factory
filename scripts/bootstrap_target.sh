#!/usr/bin/env bash
# bootstrap_target.sh — prepare a GitHub repo + Projects v2 board for a new Dark Factory target.
#
# Idempotent: safe to re-run. It
#   1. finds (by title) or creates the Projects v2 board and links it to the repo,
#   2. sets the board's Status field to the 7 options the scheduler expects,
#   3. creates or updates every label the factory applies or reads,
#   4. prints the FACTORY_* identity block to paste into deploy/instance.env.
#
# Usage:
#   scripts/bootstrap_target.sh OWNER/REPO [--title "Board title"] [--force-status]
#
# Requires: gh (authenticated with repo + project scope), jq.
# The Status options are REPLACED when they differ from the expected set; on a board
# that already holds items this resets their status, so it refuses unless --force-status.
set -euo pipefail

usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

SLUG="" TITLE="" FORCE_STATUS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --title) TITLE="$2"; shift 2 ;;
    --force-status) FORCE_STATUS=1; shift ;;
    -h|--help) usage 0 ;;
    */*) SLUG="$1"; shift ;;
    *) echo "unknown argument: $1" >&2; usage 1 ;;
  esac
done
[ -n "$SLUG" ] || usage 1
OWNER="${SLUG%%/*}"
REPO="${SLUG##*/}"
TITLE="${TITLE:-$REPO}"

command -v gh >/dev/null || { echo "gh CLI not found" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq not found" >&2; exit 1; }
gh repo view "$SLUG" --json name >/dev/null || { echo "repo $SLUG not found (push it to GitHub first)" >&2; exit 1; }

# Order matters only for display on the board.
STATUSES=("Backlog|GRAY" "Refined|PURPLE" "Ready|BLUE" "In Progress|YELLOW" "In Review|ORANGE" "Blocked|RED" "Done|GREEN")

# name|color|description — every label the scheduler, gates or phase agents apply or read.
LABELS=(
  "ready-for-agent|aaaaaa|Triaged: fully specified, opt-in for scheduler auto-refinement"
  "spec-pending-review|0075ca|Refinement spec generated, awaiting human review"
  "plan-pending-review|0052cc|Implementation plan generated, awaiting human review"
  "spec-approved|aaaaaa|Refinement spec approved; ready for planning"
  "direct-to-pr|0075ca|Opt-in: autonomous spec->plan->implement->PR->merge with grace windows"
  "needs-discussion|d93f0b|Requires human discussion before agent dispatch"
  "ready-for-human|1d76db|Triaged: requires human implementation"
  "needs-triage|e11d48|Issue requires triage before work can begin"
  "needs-info|fbca04|Waiting on reporter for more information"
  "epic|ededed|Umbrella issue grouping sub-issues"
  "above-ceiling|B60205|Above the autonomous dispatch ceiling: human pairs on implementation"
  "no-autopilot|cccccc|Per-ticket veto for epic autopilot"
  "scope-spillover|fbca04|Change was out-of-scope and filed as a backlog ticket"
  "regression|c2410c|Worked before; broke after a change"
  "factory-regression|B60205|Fixes something a factory PR broke"
  "cost-report-lost|fbca04|Dark Factory cost/token data could not be recorded"
  "size: S|C2E0C6|< 1 hour"
  "size: M|FEF2C0|1-4 hours"
  "size: L|F9D0C4|4+ hours"
  "size: XL|D93F0B|Multi-week effort"
  "priority: must-have|B60205|MoSCoW: Must Have"
  "priority: should-have|E99695|MoSCoW: Should Have"
  "priority: could-have|C5DEF5|MoSCoW: Could Have"
)

# --- 1. Board ---
NUMBER=$(gh project list --owner "$OWNER" --limit 100 --format json \
  | jq -r --arg t "$TITLE" '[.projects[] | select(.title == $t)][0].number // empty')
if [ -z "$NUMBER" ]; then
  NUMBER=$(gh project create --owner "$OWNER" --title "$TITLE" --format json | jq -r '.number')
  echo "[board] created project #$NUMBER \"$TITLE\"" >&2
else
  echo "[board] found project #$NUMBER \"$TITLE\"" >&2
fi
PROJECT_ID=$(gh project view "$NUMBER" --owner "$OWNER" --format json | jq -r '.id')
gh project link "$NUMBER" --owner "$OWNER" --repo "$SLUG" >/dev/null 2>&1 || true

# --- 2. Status options ---
status_json() {
  gh api graphql -f query='query($id: ID!) { node(id: $id) { ... on ProjectV2 {
    items(first: 1) { totalCount }
    field(name: "Status") { ... on ProjectV2SingleSelectField { id options { id name } } } } } }' \
    -f id="$PROJECT_ID"
}
S=$(status_json)
FIELD_ID=$(echo "$S" | jq -r '.data.node.field.id')
HAVE=$(echo "$S" | jq -r '[.data.node.field.options[].name] | sort | join("|")')
WANT=$(printf '%s\n' "${STATUSES[@]}" | cut -d'|' -f1 | sort | paste -sd'|' -)
if [ "$HAVE" != "$WANT" ]; then
  ITEMS=$(echo "$S" | jq -r '.data.node.items.totalCount')
  if [ "$ITEMS" -gt 0 ] && [ "$FORCE_STATUS" -ne 1 ]; then
    echo "[board] Status options are [$HAVE], expected [$WANT]; the board has items, so" >&2
    echo "        replacing them would reset every item's status. Re-run with --force-status." >&2
    exit 1
  fi
  # Array variables cannot be passed with -f/-F, so send the whole request body as JSON.
  printf '%s\n' "${STATUSES[@]}" \
    | jq -R 'split("|") | {name: .[0], color: .[1], description: ""}' \
    | jq -s --arg f "$FIELD_ID" '{
        query: "mutation($f: ID!, $o: [ProjectV2SingleSelectFieldOptionInput!]) { updateProjectV2Field(input: {fieldId: $f, singleSelectOptions: $o}) { projectV2Field { ... on ProjectV2SingleSelectField { id } } } }",
        variables: {f: $f, o: .}}' \
    | gh api graphql --input - >/dev/null
  echo "[board] Status options set" >&2
  S=$(status_json)
else
  echo "[board] Status options already correct" >&2
fi
opt() { echo "$S" | jq -r --arg n "$1" '.data.node.field.options[] | select(.name == $n) | .id'; }

# --- 3. Labels ---
for spec in "${LABELS[@]}"; do
  IFS='|' read -r name color desc <<<"$spec"
  gh label create "$name" --repo "$SLUG" --color "$color" --description "$desc" --force >/dev/null
done
echo "[labels] ${#LABELS[@]} labels created/updated on $SLUG" >&2

# --- 4. instance.env block ---
cat <<EOF

# ---- paste into deploy/instance.env ----
FACTORY_INSTANCE=$REPO
FACTORY_OWNER=$OWNER
FACTORY_REPO=$REPO
FACTORY_PROJECT_ID=$PROJECT_ID
FACTORY_PROJECT_NUMBER=$NUMBER
FACTORY_STATUS_FIELD=$FIELD_ID
FACTORY_STATUS_READY=$(opt "Ready")
FACTORY_STATUS_IN_PROGRESS=$(opt "In Progress")
FACTORY_STATUS_IN_REVIEW=$(opt "In Review")
FACTORY_STATUS_BLOCKED=$(opt "Blocked")
FACTORY_STATUS_DONE=$(opt "Done")
FACTORY_STATUS_BACKLOG=$(opt "Backlog")
FACTORY_STATUS_REFINED=$(opt "Refined")
FACTORY_CLONE_DIR=/workspace/$REPO
FACTORY_RUN_PREFIX=$REPO-dark-factory-run-
EOF
