---
description: Weekly dispatch ceiling keyword revisit — analyze success-by-size/keyword and recommend changes
argument-hint: "ceiling-revisit <issue-number> <since-date> <until-date>"
---

# Weekly Dispatch Ceiling Revisit

> **Env-driven, generic capability.** All repo/identity values are resolved from
> the factory identity env (`FACTORY_REPO_SLUG`, `FACTORY_EMAIL`,
> `FACTORY_PRODUCT_NAME`) — no target repo is hardcoded. The command is invoked
> with args `$ISSUE_NUM`, `$SINCE`, `$UNTIL`, `$NEXT_DATE` (see Inputs below).

## Purpose

Runs the weekly dispatch ceiling keyword review for the factory scheduler.
Reads Factory Scorecard data, applies deterministic decision rules, posts an analysis
comment on the given GitHub issue, optionally opens a PR for keyword changes, and
unconditionally files the next weekly revisit issue.

## Inputs (from workflow args)

- `$ISSUE_NUM` — GitHub issue number receiving the analysis comment (e.g. 355)
- `$SINCE` — analysis window start (YYYY-MM-DD, always 2026-06-12 — policy introduction date)
- `$UNTIL` — analysis window end (YYYY-MM-DD, today's date when the agent runs)
- `$NEXT_DATE` — target date for the next weekly revisit issue (UNTIL + 7 days)

## Phase 1 — Fetch and Analyze

```bash
REPO="${FACTORY_REPO_SLUG}"
SCORECARD=/tmp/ceiling-revisit-scorecard.json

# Fetch scorecard data for cumulative window since policy introduction
python3 dark-factory/scripts/fetch_scorecard.py \  # TARGET-PATH
  --since "$SINCE" \
  --until "$UNTIL" \
  --output "$SCORECARD"

# Generate analysis report and machine-readable recommendation
REPORT_FILE=/tmp/ceiling-revisit-report.md
python3 dark-factory/scripts/ceiling_revisit.py \  # TARGET-PATH
  --since "$SINCE" \
  --until "$UNTIL" \
  --scorecard "$SCORECARD" \
  --output "$REPORT_FILE" \
  2>/tmp/ceiling-revisit-meta.txt

# Extract recommendation JSON from stderr
REC_JSON=$(grep 'CEILING_REVISIT_JSON' /tmp/ceiling-revisit-meta.txt \
  | sed 's/.*CEILING_REVISIT_JSON \(.*\) -->/\1/')
KEYWORDS_TO_REMOVE=$(echo "$REC_JSON" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print('|'.join(d['keywords_to_remove']))")
L_NEEDS_ISSUE=$(echo "$REC_JSON" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d['l_bucket_needs_issue'])")
```

## Phase 2 — Post Analysis Comment

```bash
REPORT_BODY=$(cat "$REPORT_FILE")
gh issue comment "$ISSUE_NUM" --repo "$REPO" --body "$REPORT_BODY"
```

## Phase 3 — Open PR if Keyword Changes Warranted

Only execute this phase if `KEYWORDS_TO_REMOVE` is non-empty.

```bash
if [ -n "$KEYWORDS_TO_REMOVE" ]; then
  # Read effective ABOVE_CEILING_KEYWORDS from .archon/.env when the target
  # overrides it; otherwise fall back to the built-in default (config default,
  # mirrored by ceiling_revisit.py DEFAULT_KEYWORDS).
  ENV_FILE=".archon/.env"
  if [ -f "$ENV_FILE" ] && grep -q "^ABOVE_CEILING_KEYWORDS=" "$ENV_FILE"; then
    CURRENT=$(grep '^ABOVE_CEILING_KEYWORDS=' "$ENV_FILE" | cut -d= -f2-)
  else
    CURRENT="migration|migrate|performance|perf|architectur|refactor"
  fi

  # Compute new value by removing flagged keywords
  NEW_KWS="$CURRENT"
  for KW in $(echo "$KEYWORDS_TO_REMOVE" | tr '|' '\n'); do
    NEW_KWS=$(echo "$NEW_KWS" | sed "s/|${KW}//g;s/${KW}|//g;s/^${KW}$//g")
  done

  # Write .archon/.env (create if not present)
  if [ -f "$ENV_FILE" ] && grep -q "^ABOVE_CEILING_KEYWORDS=" "$ENV_FILE"; then
    sed -i "s|^ABOVE_CEILING_KEYWORDS=.*|ABOVE_CEILING_KEYWORDS=${NEW_KWS}|" "$ENV_FILE"
  else
    echo "ABOVE_CEILING_KEYWORDS=${NEW_KWS}" >> "$ENV_FILE"
  fi

  # Create PR branch and open PR
  PR_BRANCH="chore/ceiling-revisit-${UNTIL}"
  git checkout -b "$PR_BRANCH"
  git add "$ENV_FILE"
  git commit -m "chore(env): update ABOVE_CEILING_KEYWORDS per weekly revisit (#${ISSUE_NUM})

Removing: ${KEYWORDS_TO_REMOVE}
New value: ${NEW_KWS}

Analysis window: ${SINCE} → ${UNTIL}
Decision: n>=5 and keyword success rate >= M_baseline (no discriminative value)."

  git push origin "$PR_BRANCH"
  gh pr create \
    --repo "$REPO" \
    --title "chore(env): update ABOVE_CEILING_KEYWORDS per weekly ceiling revisit" \
    --body "Recommended by weekly dispatch ceiling analysis on issue #${ISSUE_NUM}.

Removes: \`${KEYWORDS_TO_REMOVE}\`

See the analysis comment on #${ISSUE_NUM} for full data and decision rationale.

Closes #${ISSUE_NUM} (if this is the actionable change)." \
    --label "priority: should-have" \
    --base main
fi
```

## Phase 4 — File L-Bucket Code-Change Issue (conditional)

Only execute if `L_NEEDS_ISSUE` is `True`.

```bash
if [ "$L_NEEDS_ISSUE" = "True" ]; then
  gh issue create \
    --repo "$REPO" \
    --title "Revisit L=always-above-ceiling rule in is_above_ceiling() — scheduler.sh" \
    --body "## Purpose

The weekly dispatch ceiling analysis (issue #${ISSUE_NUM}, window ${SINCE}→${UNTIL})
found the L-bucket success rate exceeds 70% at n≥5. The L=always-above-ceiling rule
in \`scheduler.sh\` may be overly conservative.

## What to review

- Inspect \`is_above_ceiling()\` in \`scheduler.sh\` (~line 213).
- Assess whether the L-bucket ceiling should be relaxed (e.g. L+keyword pattern only).
- This is a **code change** (not an env-var change) — requires PR to \`scheduler.sh\`.

## References

- Triggering analysis: issue #${ISSUE_NUM}
- Policy: the dispatch-ceiling revisit design (see the dispatch-ceiling design spec)

---
*Filed automatically by weekly ceiling revisit*" \
    --label "enhancement" \
    --label "priority: should-have"
fi
```

## Phase 5 — File Next Weekly Revisit Issue (unconditional)

```bash
NEXT_TITLE="Revisit dispatch ceiling (C9) — re-measure success-by-size/type"
gh issue create \
  --repo "$REPO" \
  --title "$NEXT_TITLE" \
  --body "## Purpose

Weekly revisit of the size/type-aware dispatch ceiling policy.

## What to review

1. Pull the Factory Scorecard success-by-S/M/L numbers for the latest week.
2. Compare against current ABOVE_CEILING_KEYWORDS thresholds.
3. Assess keyword false-positive rate. If high, narrow the list.
4. Recommend \`ABOVE_CEILING_KEYWORDS\` update in \`.archon/.env\` via PR if data warrants.

## References

- Policy: the dispatch-ceiling revisit design (see the dispatch-ceiling design spec)
- Archon command: \`commands/ceiling-revisit.md\`
- Prior revisit: #${ISSUE_NUM} (comment with results)

## Parameters for the agent

- \`ISSUE_NUM\` = <this issue's number>
- \`SINCE\` = 2026-06-12 (policy introduction date — always fixed)
- \`UNTIL\` = ${NEXT_DATE}
- \`NEXT_DATE\` = <UNTIL + 7 days>

## Target date

**${NEXT_DATE}** (weekly from ${UNTIL}).

---
*Filed automatically by ${FACTORY_PRODUCT_NAME} weekly ceiling revisit agent*" \
  --label "enhancement" \
  --label "priority: should-have" \
  --label "size: S" \
  --label "ready-for-agent"
```
