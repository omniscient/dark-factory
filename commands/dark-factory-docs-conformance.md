---
description: Verify the branch's documentation against its final post-Gate-2 diff before PR publication (Gate 2.5 — docs vs diff)
argument-hint: (no arguments - reads $ARTIFACTS_DIR/issue.json and $ARTIFACTS_DIR/doc-impact.md)
---

# Dark Factory — Docs Conformance

**Workflow ID**: $WORKFLOW_ID

---

## Invocation Contract

This file is the sanctioned Archon command entrypoint. If the runner delivers this
canonical command text inline, execute it as the authorized phase command after
verifying you are in the target checkout.

Issue context is not assumed to be present in chat. The workflow persists it at
`$ARTIFACTS_DIR/issue.json`; read that file for `resolved_number`, `intent`, title, body,
labels, and comments.

---

<!-- headless-contract:begin -->
## Headless Execution Contract

You are running with no human attached; an ended turn ends the process.

- **Never end your turn on a question or an offer.** There is no one to answer. Decide
  per this command's instructions, act, and record any reservations in the issue comment or commit
  message instead.
- **Persist this phase's artifact before your final turn ends.** Commit (and push, where
  this command says to) any repo file this phase owns; write or post any comment, label or
  `$ARTIFACTS_DIR` artifact it owns. Work left unpersisted when the turn ends is destroyed.
- **Turn end = process end.** Scheduled wakeups do not fire (do not use `ScheduleWakeup`),
  task-notifications never arrive, and pending subagent work is destroyed — and an ended
  turn is reported as success whether or not this phase's artifact exists.
- **To wait on a background subagent, poll inside your turn** (keep issuing tool calls)
  or do the work inline — never end the turn to "wait."
<!-- headless-contract:end -->

---

## What this gate is

Gate 2.5 runs after `conformance-gate` and before `push-and-pr` (#535). It asks one
question: is the documentation on this branch true of the branch's **final** diff — the
diff after validate's fixes and Gate 2's excisions and reconcile commits? It is
**single-pass and read-only**: one reviewer spawn, one verdict, no reconcile loop. This
session makes no edits, commits or pushes. Its only outputs are
`$ARTIFACTS_DIR/docs-conformance.md` (plus the `docs_conformance_*` working files below)
and, on a BLOCKED verdict, one issue comment — and, in `enforce` mode only, a board move
and the `needs-discussion` label. A docs fix is never made here: it would land after Gate 2
already passed. Remediation is a later `Continue` run, which re-runs implement, Gate 2 and
this gate.

`docs_conformance.mode` decides only the consequence of a BLOCKED verdict; this command
always builds the diff, spawns the reviewer and writes a real verdict in both modes.
`docs-conformance-gate` (`scripts/docs_verdict_gate_check.sh`) is what lets or stops
`push-and-pr`.

## Phase 1: LOAD

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
source "${REPO_ROOT}/dark-factory/scripts/gate_lib.sh"  # TARGET-PATH
ISSUE_NUM=$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")
read -r DOCS_ENABLED DOCS_MODE < <(python3 -c "import yaml; d = yaml.safe_load(open('.claude/skills/refinement/config.yaml')) or {}; c = d.get('docs_conformance') or {}; m = c.get('mode', 'observe'); print(str(c.get('enabled', True)).lower(), m if m == 'enforce' else 'observe')" 2>/dev/null || echo "true observe")
echo "docs-conformance: enabled=${DOCS_ENABLED} mode=${DOCS_MODE}"
```

1. If `DOCS_ENABLED` is `false`, write the SKIPPED verdict and exit `0` — nothing else runs:
   ```bash
   printf "STATUS: SKIPPED\nREASON: docs_conformance.enabled=false\n" > "$ARTIFACTS_DIR/docs-conformance.md"
   ```
2. Read `/opt/refinement-skills/VERIFIER-CONTRACT.md` — the checker-invocation contract for
   the reviewer subagent spawned in Phase 3; if the file is absent (image predates it),
   continue — the inline pin is authoritative.
3. Read `$ARTIFACTS_DIR/doc-impact.md` (implement Phase 4's handoff) if it exists. A missing
   or empty file does not stop this command: Phase 2 passes that fact to the reviewer, and
   Phase 4 turns it into a BLOCKED verdict whatever the reviewer says.

## Phase 2: BUILD THE EVIDENCE

Run this as one Bash call. The diff keeps Gates 2 and 3's noise exclusions but **not**
their `docs/*.md` exclusion — this is the one gate that reads doc content. It also drops the
refine/plan artifacts under `docs/superpowers/` and `docs/archive/`: they are workflow
artifacts that `push-and-pr` archives, not documentation of the change, and would crowd the
diff budget. Policy Markdown (`commands/**`, `.claude/skills/**`, `refinement-skills/**`)
stays in the diff as behavior.

```bash
BASE_REF=$(git merge-base main HEAD)

# 2a. The final diff, ranked like Gates 2/3 (fail-open to the raw diff).
RANK_IN=$(mktemp /tmp/docs_rank_in_XXXXXX.txt)
[ -f "$ARTIFACTS_DIR/token-opt-caps.env" ] && . "$ARTIFACTS_DIR/token-opt-caps.env" || true
if ! git diff main...HEAD \
  -- ':!*.lock' ':!evals/*.md' ':!bench/*.md' \
  ':!.archon/memory/**' \
  ':!codeindex.json' ':!symbolindex.json' \
  ':!docs/superpowers/**' ':!docs/archive/**' \
  2>/tmp/docs_diff_err.txt > "$RANK_IN"; then
  # git itself failed (e.g. no local main): never read that as an empty diff.
  printf 'REVIEWER_ERROR: git diff main...HEAD failed: %s\n' "$(head -c 400 /tmp/docs_diff_err.txt)" \
    > "$ARTIFACTS_DIR/docs_conformance_review.md"
  rm -f "$RANK_IN"
  echo "docs-conformance: git diff failed — Phase 4 writes UNCERTAIN/BLOCKED"
elif [ ! -s "$RANK_IN" ]; then
  printf "STATUS: PASS\nGATE_TYPE: docs-conformance\nFINDINGS_COUNT: 0\nSEVERITY: none\nVERDICT: CONFORMS\nREASON: empty diff\n" \
    > "$ARTIFACTS_DIR/docs-conformance.md"
  rm -f "$RANK_IN"
  echo "docs-conformance: empty diff — PASS, nothing to document"
else
  # TARGET-PATH: diff_rank.py resolves under dark-factory/ in the clone.
  python3 dark-factory/scripts/diff_rank.py \
    --diff "$RANK_IN" \
    --artifacts-dir "$ARTIFACTS_DIR" \
    --config ".claude/skills/refinement/config.yaml" \
    --hotspots "docs/codeindex-hotspots.md" \
    2>/tmp/docs_rank_err.txt > "$ARTIFACTS_DIR/docs_conformance_diff.txt" \
    || { echo "diff_rank: ranking failed ($(cat /tmp/docs_rank_err.txt)) — using raw diff"; cp "$RANK_IN" "$ARTIFACTS_DIR/docs_conformance_diff.txt"; }
  rm -f "$RANK_IN"
fi

# 2b. Baseline and branch copies of every documentation file the diff touches.
DOC_PATHS=$(git diff main...HEAD --name-only -- '*.md' \
  ':!commands/**' ':!.claude/skills/**' ':!refinement-skills/**' \
  ':!evals/*.md' ':!bench/*.md' ':!.archon/memory/**' \
  ':!docs/superpowers/**' ':!docs/archive/**' 2>/dev/null)
BASE_DIR="$ARTIFACTS_DIR/docs-conformance-baseline"
rm -rf "$BASE_DIR" && mkdir -p "$BASE_DIR"
{
  echo "| Doc file | Baseline (main) copy | Branch copy |"
  echo "|---|---|---|"
  printf '%s\n' "$DOC_PATHS" | while IFS= read -r P; do
    [ -z "$P" ] && continue
    if git cat-file -e "${BASE_REF}:${P}" 2>/dev/null; then
      mkdir -p "$BASE_DIR/$(dirname "$P")"
      git show "${BASE_REF}:${P}" > "$BASE_DIR/$P"
      BASE="\`$BASE_DIR/$P\`"
    else
      BASE="(new on this branch)"
    fi
    if [ -f "$P" ]; then BR="\`$(pwd)/$P\`"; else BR="(deleted on this branch)"; fi
    echo "| \`$P\` | $BASE | $BR |"
  done
  [ -n "$DOC_PATHS" ] || echo "| (no documentation file changed on this branch) | | |"
} > "$ARTIFACTS_DIR/docs_conformance_doc_files.md"

# 2c. The doc-impact claim to audit.
if [ -s "$ARTIFACTS_DIR/doc-impact.md" ] \
   && grep -qE '^OVERALL: (UPDATED|NO_IMPACT: .+)' "$ARTIFACTS_DIR/doc-impact.md"; then
  cp "$ARTIFACTS_DIR/doc-impact.md" "$ARTIFACTS_DIR/docs_conformance_doc_impact.md"
else
  {
    echo "(doc-impact.md is missing, empty, or has no OVERALL: line — this is itself a MATERIAL mismatch.)"
    if [ -s "$ARTIFACTS_DIR/doc-impact.md" ]; then
      printf '\nRaw content:\n\n'
      cat "$ARTIFACTS_DIR/doc-impact.md"
    fi
  } > "$ARTIFACTS_DIR/docs_conformance_doc_impact.md"
fi
```

If the empty-diff branch fired, `docs-conformance.md` is already written: skip to Phase 7.
If the git-diff-failed branch fired, skip Phase 3 entirely (its `rm -f` would delete the
`REVIEWER_ERROR:` file) and go to Phase 4–5, which writes `UNCERTAIN` → BLOCKED.

## Phase 3: REVIEW

1. Resolve the rubric clone-live-first and render the prompt:
   ```bash
   if [ -f ".claude/skills/docs-conformance/RUBRIC.md" ]; then
     RUBRIC_FILE=".claude/skills/docs-conformance/RUBRIC.md"
   else
     RUBRIC_FILE="/opt/refinement-skills/docs-conformance-reviewer-prompt.md"
   fi

   rm -f "$ARTIFACTS_DIR/docs_conformance_review.md"
   # TARGET-PATH
   python3 dark-factory/scripts/factory_core/cli.py render-prompt \
     --template "$RUBRIC_FILE" \
     --set DIFF_CONTENT=@"$ARTIFACTS_DIR/docs_conformance_diff.txt" \
     --set DOC_FILES=@"$ARTIFACTS_DIR/docs_conformance_doc_files.md" \
     --set DOC_IMPACT=@"$ARTIFACTS_DIR/docs_conformance_doc_impact.md" \
     --out "$ARTIFACTS_DIR/docs_conformance_prompt.md" \
     || printf 'REVIEWER_ERROR: render-prompt failed for %s\n' "$RUBRIC_FILE" > "$ARTIFACTS_DIR/docs_conformance_review.md"
   ```
   A render failure (including a missing rubric file) is not an abort: it leaves a
   `REVIEWER_ERROR:` file, skip step 2, and Phase 4 maps it to `UNCERTAIN` → BLOCKED.
2. Spawn exactly one reviewer subagent using the Agent tool:
   - `description`: "Docs conformance review: docs vs final diff"
   - `model`: `claude-opus-5-5` (passed to the Agent tool as its `opus` alias — the tool's `model` enum is alias-only; on the current image's CLI 2.1.282 `opus` resolves to `claude-opus-5-5`, so the pin fixes the tier, not the exact snapshot) — pin and read access (Glob/Grep/Read) per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract
   - `prompt`: the verbatim contents of `$ARTIFACTS_DIR/docs_conformance_prompt.md`
   - No shadow spawn: VERIFIER-CONTRACT pairs shadows only with the two conformance reviewers.
3. Write the subagent's full response, verbatim, to `$ARTIFACTS_DIR/docs_conformance_review.md`
   with the Write tool. If the spawn errored, timed out, was refused, or returned nothing,
   write one line instead: `REVIEWER_ERROR: <the error text, or "empty response">`. Do not
   re-spawn, retry or summarize — this gate is single-pass.

## Phase 4–5: PARSE THE VERDICT AND WRITE IT

Run as one Bash call. It reads only files, so it does not depend on shell state from earlier
calls. Refusal, timeout, tool error and an unparseable response all map to `UNCERTAIN`, which
is `BLOCKED` — never `PASS` (VERIFIER-CONTRACT's refusal rule; there is no reconcile cycle to
consume first). A missing or incomplete `doc-impact.md` forces `BLOCKED` whatever the
reviewer said.

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
source "${REPO_ROOT}/dark-factory/scripts/gate_lib.sh"  # TARGET-PATH
read -r _EN DOCS_MODE < <(python3 -c "import yaml; d = yaml.safe_load(open('.claude/skills/refinement/config.yaml')) or {}; c = d.get('docs_conformance') or {}; m = c.get('mode', 'observe'); print(str(c.get('enabled', True)).lower(), m if m == 'enforce' else 'observe')" 2>/dev/null || echo "true observe")
REVIEW="$ARTIFACTS_DIR/docs_conformance_review.md"
VERDICT_LINE=$(grep -m1 '^\*\*Verdict:\*\*' "$REVIEW" 2>/dev/null || true)
MATERIAL=$(grep -c '^- \[MATERIAL\]' "$REVIEW" 2>/dev/null || true)
MINOR=$(grep -c '^- \[MINOR\]' "$REVIEW" 2>/dev/null || true)
MATERIAL=${MATERIAL:-0}; MINOR=${MINOR:-0}
REASON=""
case "$VERDICT_LINE" in
  *"⛔"*) VERDICT=MATERIAL;  STATUS=BLOCKED; COUNT=$((MATERIAL + MINOR)); SEV=high ;;
  *"⚠"*)  VERDICT=MINOR;     STATUS=PASS;    COUNT=$MINOR;              SEV=low ;;
  *"✅"*) VERDICT=CONFORMS;  STATUS=PASS;    COUNT=0;                   SEV=none ;;
  *)      VERDICT=UNCERTAIN; STATUS=BLOCKED; COUNT=1;                   SEV=high
          REASON="no parseable **Verdict:** line (refusal, timeout, tool error or bad output)" ;;
esac
DOC_IMPACT=present
if ! { [ -s "$ARTIFACTS_DIR/doc-impact.md" ] && grep -qE '^OVERALL: (UPDATED|NO_IMPACT: .+)' "$ARTIFACTS_DIR/doc-impact.md"; }; then
  DOC_IMPACT=missing
  if [ "$STATUS" = "PASS" ]; then
    STATUS=BLOCKED; COUNT=$((COUNT + 1)); SEV=high
    REASON="doc-impact.md is missing, empty, or has no OVERALL: line"
  fi
fi
{
  emit_verdict "docs-conformance" "$STATUS" "$COUNT" "$SEV"
  printf "VERDICT: %s\nMODE: %s\nDOC_IMPACT: %s\n" "$VERDICT" "$DOCS_MODE" "$DOC_IMPACT"
  [ -n "$REASON" ] && printf "REASON: %s\n" "$REASON"
  printf "\n---\n\n"
  cat "$REVIEW" 2>/dev/null || echo "(no reviewer output was recorded)"
} > "$ARTIFACTS_DIR/docs-conformance.md"
echo "docs-conformance: STATUS=${STATUS} VERDICT=${VERDICT} mode=${DOCS_MODE}"
```

If `STATUS` is `PASS`, skip to Phase 7. No comment is posted on a pass.

## Phase 6: ON BLOCKED — branch on `docs_conformance.mode`

Run as one Bash call. Each body starts with its `##` heading — `comment_digest.py` recognizes
a gate verdict only by a registered heading at the very start of the body, and the
upsert marker may sit anywhere in it. `observe` (the default) posts the same evidence as an informational
comment under its own marker and makes **no** label or board change, so a false block costs
no human touch. `enforce` posts the blocking comment, moves the board to **Blocked** and adds
`needs-discussion` — the same board/label consequence as Gate 2's BLOCKED path (no new
status or label).

```bash
ISSUE_NUM=$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")
DOCS_MODE=$(grep -m1 '^MODE:' "$ARTIFACTS_DIR/docs-conformance.md" | awk '{print $2}')
REASON=$(grep -m1 '^REASON:' "$ARTIFACTS_DIR/docs-conformance.md" | sed 's/^REASON: //')
FOOTER=$(python3 dark-factory/scripts/factory_core/cli.py marker factory)  # TARGET-PATH
REVIEW_TEXT=$(cat "$ARTIFACTS_DIR/docs_conformance_review.md" 2>/dev/null || echo "(no reviewer output was recorded)")
NEXT_STEPS="### Next Steps

- Fix the documentation (or \`doc-impact.md\`'s assessment) to match the diff, then re-run: \`docker compose --profile factory run --rm dark-factory \"Continue issue #${ISSUE_NUM}\"\`. A \`Continue\` run re-runs implementation, Gate 2 and Gate 2.5 on the new diff.
- If the reviewer is wrong, say why in a comment; a human decides."
if [ "$DOCS_MODE" = "enforce" ]; then
  MARKER="<!-- df-docs-conformance-blocked -->"
  BODY="## Docs Conformance (Gate 2.5) — Blocked
${MARKER}

The documentation on this branch does not match its final diff, so the PR was not published (\`docs_conformance.mode: enforce\`).${REASON:+

**Reason:** ${REASON}}

${REVIEW_TEXT}

${NEXT_STEPS}

---
${FOOTER}"
else
  MARKER="<!-- df-docs-conformance-observed -->"
  BODY="## Docs Conformance (Gate 2.5) — Mismatch observed (informational)
${MARKER}

\`docs_conformance.mode\` is \`observe\`: this verdict would have blocked the PR in \`enforce\` mode ([#536](https://github.com/omniscient/dark-factory/issues/536)). Nothing was blocked, and no label or board change was made.${REASON:+

**Reason:** ${REASON}}

${REVIEW_TEXT}

---
${FOOTER}"
fi
TMPFILE=$(mktemp /tmp/df-docs-conformance-XXXXXX.md)
printf '%s' "$BODY" > "$TMPFILE"
python3 dark-factory/scripts/factory_core/providers/cli.py tracker comment \
  --id "$ISSUE_NUM" --marker "$MARKER" --body-file "$TMPFILE" || echo "WARNING: docs-conformance comment post failed"  # TARGET-PATH
rm -f "$TMPFILE"
if [ "$DOCS_MODE" = "enforce" ]; then
  python3 dark-factory/scripts/factory_core/providers/cli.py \
    tracker set-status --id "$ISSUE_NUM" --status blocked  # TARGET-PATH
  python3 dark-factory/scripts/factory_core/providers/cli.py tracker label --id "$ISSUE_NUM" --add needs-discussion  # TARGET-PATH
fi
```

## Phase 7: EXIT

Exit `0` after PASS or SKIPPED, and after BLOCKED when `MODE` is `observe`; exit `1` only
after BLOCKED in `enforce` mode. The exit code is
forward-compatibility only, as in `dark-factory-conformance.md` Phase 5: a `command:` node's
internal `exit 1` does not reliably surface to the DAG executor (#212, #271). The
`docs-conformance-gate` node reads `docs-conformance.md` and decides whether `push-and-pr`
runs.
