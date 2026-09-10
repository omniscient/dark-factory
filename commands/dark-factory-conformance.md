---
description: Verify that the implementation conforms to its approved spec (Gate 2 — code vs spec)
argument-hint: (no arguments - reads $ARTIFACTS_DIR/issue.json)
---

# Dark Factory — Conformance

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

## Phase 1: LOAD

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
source "${REPO_ROOT}/dark-factory/scripts/gate_lib.sh"  # TARGET-PATH
AGENT_ID="${AGENT_ID_DECONFLICT}"
ISSUE_NUM=$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")
```

1. Read `.claude/skills/refinement/config.yaml` and extract the `conformance` block.
2. If `conformance.enabled` is `false`:
   - Write `$ARTIFACTS_DIR/conformance.md` with content: `STATUS: SKIPPED\nREASON: conformance.enabled=false`
   - Exit cleanly (proceed to push-and-pr)
3. Read the conformance rubric, clone-live-first: `.claude/skills/conformance/RUBRIC.md`,
   falling back to `/opt/refinement-skills/conformance-reviewer-prompt.md` if the clone-live
   file is absent. Store the resolved text as `RUBRIC_CONTENT`.
4. Read `/opt/refinement-skills/VERIFIER-CONTRACT.md` — the checker-invocation contract for the conformance reviewer subagent spawned in Phase 3; if the file is absent (image predates it), continue — the inline pin is authoritative.
5. Read `$ARTIFACTS_DIR/implementation.md` for what was implemented (may be missing if validate wrote nothing — continue anyway)
6. Extract `MAX_CYCLES` from `conformance.max_reconcile_cycles` (default: 3)
7. Extract `BLOCK_ON_MATERIAL` from `conformance.block_on_material` (default: true)
8. Extract `SCOPE_ENFORCEMENT` from `conformance.scope_enforcement` (default: true)
9. Extract `EXCISE_OOS` from `conformance.excise_out_of_scope` (default: true)
10. Extract `BACKLOG_LABEL` from `conformance.backlog_label` (default: `scope-spillover`)
11. Determine `ISSUE_NUM` from `$ARTIFACTS_DIR/issue.json`; only fall back to `git branch --show-current | grep -oP 'issue-\K\d+'` if the artifact is missing or invalid.
12. Resolve the shadow model pin (Requirement 7 — explicit-empty-wins-over-unset):
    ```bash
    SHADOW_MODEL_DEFAULT=$(python3 -c "import yaml; d=yaml.safe_load(open('.claude/skills/refinement/config.yaml')); print(d.get('conformance',{}).get('shadow_model','claude-fable-5-1'))" 2>/dev/null || echo "claude-fable-5-1")
    SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}"
    ```

## Phase 2: LOCATE SPEC

Locate the approved spec using this priority order:

### 2a. Check the "Plan Generated" issue comment

```bash
gh issue view $ISSUE_NUM --json comments \
  | jq -r '[.comments[] | select(.body | test("Refinement Pipeline — Plan Generated"))] | last | .body // ""'
```

Parse the **Spec:** or **Plan:** line from that comment to find the spec file path. The Plan comment typically links the plan file; look for any linked file under `docs/superpowers/specs/`.

```bash
# Extract the first docs/superpowers/specs/ path from the "Plan Generated" comment
PLAN_COMMENT=$(gh issue view "$ISSUE_NUM" --repo "$FACTORY_REPO_SLUG" --json comments \
  | jq -r '[.comments[] | select(.body | test("Refinement Pipeline — Plan Generated"))] | last | .body // ""')
SPEC_FILE=$(printf '%s' "$PLAN_COMMENT" \
  | grep -oP 'docs/superpowers/specs/[^\s\])"]+' | head -1)
```

### 2b. Check $ARTIFACTS_DIR/refinement-status.md

```bash
cat "$ARTIFACTS_DIR/refinement-status.md" 2>/dev/null || true
```

Look for a `SPEC_PATH:` or `PLAN_PATH:` line that points to a spec.

```bash
SPEC_FILE=$(grep '^SPEC_PATH:' "$ARTIFACTS_DIR/refinement-status.md" 2>/dev/null \
  | sed 's/^SPEC_PATH: //' | head -1)
```

### 2c. Scan docs/superpowers/specs/

```bash
ls docs/superpowers/specs/ 2>/dev/null | sort -r | head -10
```

Look for a file whose name contains keywords from the issue title. Pick the most recently created file that matches.

```bash
ISSUE_KEYWORDS=$(gh issue view "$ISSUE_NUM" --repo "$FACTORY_REPO_SLUG" --json title \
  --jq '.title' | tr '[:upper:]' '[:lower:]' | tr ' ' '-')
SPEC_MATCH=$(ls docs/superpowers/specs/ 2>/dev/null | sort -r | head -10 \
  | grep -im1 "$(echo "$ISSUE_KEYWORDS" | cut -c1-20)" || true)
[ -n "$SPEC_MATCH" ] && SPEC_FILE="docs/superpowers/specs/$SPEC_MATCH" || SPEC_FILE=""
```

### 2d. No-spec fallback

If no spec file is found after all three steps:
- Set `NO_SPEC=true`
- The review will run against the issue body (advisory-only, never blocks)
- Fetch the issue body: `gh issue view $ISSUE_NUM --json body --jq '.body'`
- Log: "No spec found — running advisory-only review against issue body"

```bash
SPEC_FILE=""
```

## Phase 3: PRE-TRIAGE AND CONFORMANCE REVIEW

### Step 3.0 — Pre-triage: strip housekeeping and formatter-only Python hunks

Before feeding the diff to the reviewer, strip noise that would pollute the out-of-scope analysis.

**3.0.1 — Get the raw diff (lock files, generated artifacts, and agent memory excluded):**

```bash
RAW_DIFF=$(git diff main...HEAD \
  -- ':!*.lock' ':!docs/*.md' ':!evals/*.md' ':!bench/*.md' \
  ':!.archon/memory/**' \
  ':!codeindex.json' ':!symbolindex.json' \
  2>/dev/null)
```

**3.0.2 — Strip formatter-only hunks from .py files (hunk-level, not file-level):**

For each `.py` file in the diff, the filter script fetches the base version from `main`,
applies `ruff format` + `ruff check --fix --select I` to a throwaway copy, computes the
formatter delta, and removes from the diff any hunk whose changed lines are a strict subset
of the formatter delta. Interleaved hunks (formatter noise and feature code share the same
hunk) are left intact — Layer 2 (reviewer prompt) handles the residual.

```bash
# Extract .py files touched by the branch (one per line)
PY_FILES=$(git diff main...HEAD --name-only -- '*.py' 2>/dev/null)

TRIAGED_DIFF="$RAW_DIFF"
FILTER_ANNOTATION=""

if [ -n "$PY_FILES" ]; then
  # Write inputs to temp files
  DIFF_TMP=$(mktemp /tmp/fmt_diff_XXXXXX.txt)
  FILES_TMP=$(mktemp /tmp/fmt_files_XXXXXX.txt)
  printf '%s' "$RAW_DIFF" > "$DIFF_TMP"
  printf '%s\n' $PY_FILES > "$FILES_TMP"

  # Run the hunk filter; on script error fall back to raw diff (fail-open)
  FILTER_OUT=$(python3 dark-factory/scripts/fmt_hunk_filter.py \  # TARGET-PATH
    "$DIFF_TMP" "$FILES_TMP" 2>/tmp/fmt_filter_err.txt) \
    && TRIAGED_DIFF="$FILTER_OUT" \
    || echo "pre-triage: fmt_hunk_filter.py failed — using raw diff ($(cat /tmp/fmt_filter_err.txt))"

  rm -f "$DIFF_TMP" "$FILES_TMP"

  # Extract the [Pre-triage] annotation line if present (first line of output)
  FILTER_ANNOTATION=$(printf '%s' "$TRIAGED_DIFF" | head -1 | grep '^\[Pre-triage\]' || true)
  if [ -n "$FILTER_ANNOTATION" ]; then
    echo "pre-triage: $FILTER_ANNOTATION"
  fi
fi
```

`$TRIAGED_DIFF` is the formatter-stripped diff (or the raw diff if no .py files or ruff is
absent). `$FILTER_ANNOTATION` is the one-line informational note (empty if no stripping).

> **Annotation ordering note:** `$FILTER_ANNOTATION` is extracted from `$TRIAGED_DIFF` at the
> `head -1 | grep '^[Pre-triage]'` line *before* the ranking step runs. After ranking,
> `$TRIAGED_DIFF` is overwritten with the ranked diff (which begins with `# [diff-rank: ...]`
> and does not contain the `[Pre-triage]` line). `$FILTER_ANNOTATION` retains its value from
> the fmt-filtered diff and is independently included in `$ARTIFACT_CONTENT` in Step 3.1.2.

```bash
# Rank and chunk the fmt-filtered diff (fail-open)
RANK_IN=$(mktemp /tmp/rank_in_XXXXXX.txt)
[ -f "$ARTIFACTS_DIR/token-opt-caps.env" ] && . "$ARTIFACTS_DIR/token-opt-caps.env" || true
printf '%s' "$TRIAGED_DIFF" > "$RANK_IN"
RANKED=$(python3 dark-factory/scripts/diff_rank.py \  # TARGET-PATH
  --diff "$RANK_IN" \
  --artifacts-dir "$ARTIFACTS_DIR" \
  --config ".claude/skills/refinement/config.yaml" \
  ${SPEC_FILE:+--spec-file "$SPEC_FILE"} \
  --hotspots "docs/codeindex-hotspots.md" \
  2>/tmp/diff_rank_err.txt) \
  && TRIAGED_DIFF="$RANKED" \
  || echo "diff_rank: ranking failed ($(cat /tmp/diff_rank_err.txt)) — using fmt-filtered diff"
rm -f "$RANK_IN"
```

Also check for an `out-of-scope.md` recorded by the implement agent (preserved from original Step 3.0):
```bash
OOS_LOG=""
if [ -f "$ARTIFACTS_DIR/out-of-scope.md" ]; then
  OOS_LOG=$(cat "$ARTIFACTS_DIR/out-of-scope.md")
fi
```

### Step 3.1 — Build artifact content and run review

1. Get the pre-triaged implementation diff (Step 3.0 above).
   Also read `$ARTIFACTS_DIR/implementation.md` for the implementation summary.

2. Build `$ARTIFACT_CONTENT` and write it to a file. The heredoc below is **intentionally
   flush-left**, not indented under this list item — a `<<ARTIFACT_EOF` (no `-`) heredoc
   requires its closing delimiter to have zero leading whitespace to be recognized as the
   terminator; an indented `   ARTIFACT_EOF` would never match and the heredoc would swallow
   the rest of the script (the same reason Step 3's heredoc in `dark-factory-revise-advisory.md`
   Phase 3 is flush-left rather than nested under its surrounding prose):

```bash
cat > "$ARTIFACTS_DIR/conformance_artifact_content.md" <<ARTIFACT_EOF
### Implementation Summary
$(cat "$ARTIFACTS_DIR/implementation.md" 2>/dev/null || echo "No implementation summary found.")

### Out-of-Scope Log (from implement agent)
$(cat "$ARTIFACTS_DIR/out-of-scope.md" 2>/dev/null || echo "None recorded.")

### Diff (pre-triaged, ranked by risk tier)
$FILTER_ANNOTATION
$TRIAGED_DIFF
ARTIFACT_EOF
```

3. Set `CONFORMANCE_CYCLE=0`, `CONFORMANCE_DIALOGUE=""`, and `SHADOW_DIALOGUE=""`

3a. Resolve the spec content path (Requirement 6's `SPEC_CONTENT_PATH` — a path, not the
    `SPEC_CONTENT` template slot name) and render the conformance prompt. This `if [ -n
    "$SPEC_FILE" ]` check relies on `$SPEC_FILE` (Phase 2) still being live in this Bash
    invocation — the same pre-existing assumption every other reference to `$SPEC_FILE`,
    `$ISSUE_NUM`, and `$ARTIFACTS_DIR` across this command file already makes; this task does
    not change that assumption, only reuses it. `RUBRIC_CONTENT` (Phase 1 step 3) is prose,
    not a shell variable, so it is re-resolved here as a real `RUBRIC_FILE` path instead of
    `printf`'d (which would silently write an empty file — no earlier step assigns
    `RUBRIC_CONTENT` in bash):
    ```bash
    if [ -n "$SPEC_FILE" ]; then
      SPEC_CONTENT_PATH="$SPEC_FILE"
    else
      # NO_SPEC=true: review runs advisory-only against the issue body instead of a spec file.
      gh issue view "$ISSUE_NUM" --repo "$FACTORY_REPO_SLUG" --json body --jq '.body' \
        > "$ARTIFACTS_DIR/no_spec_issue_body.md"
      SPEC_CONTENT_PATH="$ARTIFACTS_DIR/no_spec_issue_body.md"
    fi

    if [ -f ".claude/skills/conformance/RUBRIC.md" ]; then
      RUBRIC_FILE=".claude/skills/conformance/RUBRIC.md"
    else
      RUBRIC_FILE="/opt/refinement-skills/conformance-reviewer-prompt.md"
    fi

    # TARGET-PATH
    python3 dark-factory/scripts/factory_core/cli.py render-prompt \
      --template "$RUBRIC_FILE" \
      --set ARTIFACT_KIND=IMPLEMENTATION \
      --set SPEC_CONTENT=@"$SPEC_CONTENT_PATH" \
      --set ARTIFACT_CONTENT=@"$ARTIFACTS_DIR/conformance_artifact_content.md" \
      --out "$ARTIFACTS_DIR/conformance_prompt.md" \
      || { echo "render-prompt failed — aborting conformance phase (see stderr above)"; exit 1; }
    ```

4. Spawn a conformance reviewer subagent using the Agent tool:
   - `description`: "Conformance review: code vs spec"
   - `model`: `claude-opus-4-8` (passed to the Agent tool as its `opus` alias — the tool's `model` enum is alias-only; on the current image's CLI 2.1.261 `opus` resolves to `claude-opus-5`, so the pin fixes the tier, not the exact snapshot) — pin and read access (Glob/Grep/Read) per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s checker-invocation contract (applies to every reconcile re-spawn in Phase 3.5 too)
   - `prompt`: the verbatim contents of `$ARTIFACTS_DIR/conformance_prompt.md`

5. Append the subagent's output to `CONFORMANCE_DIALOGUE`

5a. If `$SHADOW_MODEL_PIN` is non-empty, spawn a second, non-gating subagent immediately
    after, with the identical rubric/input the Opus spawn just saw:
   - `description`: "Conformance shadow (fable): code vs spec"
   - `model`: `$SHADOW_MODEL_PIN`, passed as the Agent tool's `fable` alias when the pin is
     `claude-fable-5-1` (alias-only enum); `SHADOW_MODEL:` still records the literal pin
   - `prompt`: the identical verbatim contents of `$ARTIFACTS_DIR/conformance_prompt.md` the Opus call just read in step 4
   - Read access: `Glob`/`Grep`/`Read`, per the checker-invocation contract
   - Any tool error, timeout, or refusal is caught here — it never blocks, delays, or
     retries the Opus verdict handling in step 7, and never feeds Phase 3.6's `[OOS]` scan.
   - Derive `SHADOW_MODEL`/`SHADOW_STATUS`/`SHADOW_FINDINGS_COUNT`/`SHADOW_SEVERITY` from
     the response per `/opt/refinement-skills/VERIFIER-CONTRACT.md`'s shadow verdict mapping.
     Also capture `SHADOW_VERDICT_LINE` — the raw `**Verdict:** ...` line from the shadow
     response verbatim (or `**Verdict:** (unparseable — SHADOW_STATUS: UNCERTAIN)` if none
     was found), for Requirement 5's "structured fields **and** the shadow verdict line" in
     the durable comment.
   - Append the raw response to `SHADOW_DIALOGUE` (separate from `CONFORMANCE_DIALOGUE`).
   If `$SHADOW_MODEL_PIN` is empty, skip this step entirely for this cycle.

   (Step 3.6's re-run of "Step 3.1 again" per 3.6.4 naturally re-executes 5a too — no
   separate edit needed there; Phase 3.6 itself only ever reads `$CONFORMANCE_DIALOGUE`,
   confirmed unchanged by this task.)

6. Parse the **`## Out-of-Scope Changes`** section from the reviewer output:
   - Extract each `[OOS]` bullet
   - If `SCOPE_ENFORCEMENT=true` and any `[OOS]` entries exist → go to Phase 3.6 (scope remediation) BEFORE processing the verdict
   - If `SCOPE_ENFORCEMENT=false` or no `[OOS]` entries → skip Phase 3.6

7. Parse the **Verdict** line from the step-4 (Opus) output:
   - `✅ Conforms` or `⚠️ Minor deviations` → go to Phase 4 (PASS)
   - `⛔ Material divergence`:
     - If `NO_SPEC=true` OR `BLOCK_ON_MATERIAL=false` → treat as advisory (`⚠️ Minor deviations`), go to Phase 4
     - Otherwise → go to Phase 3.5 (reconcile loop)
   - No parseable `**Verdict:**` line, a tool error/timeout, or a refusal → treat as
     `⛔ Material divergence` with the raw output (or the error text) as the deviation
     description → go to Phase 3.5 (reconcile loop). The `NO_SPEC`/`BLOCK_ON_MATERIAL`
     advisory downgrade does not apply to an unparseable verdict: an inconclusive checker
     consumes a reconcile cycle and never passes silently (refusal → `UNCERTAIN`, never
     `PASS`, per `/opt/refinement-skills/VERIFIER-CONTRACT.md`).

## Phase 3.6: SCOPE REMEDIATION (Out-of-scope changes only)

This phase runs when the reviewer found `[OOS]` entries and `SCOPE_ENFORCEMENT=true`.

### 3.6.0 — Documentation exemption (drop doc-file OOS entries first)

The factory maintains documentation as **in-scope housekeeping**: the implement agent's
Phase 4 DOCUMENT step is **required** to update the documentation map (`ARCHITECTURE.md`,
`PROJECT_STRUCTURE.md`, `ENV_VARIABLES.md`, `README.md`, `CLAUDE.md`, files under `docs/`) so it
tracks the files / endpoints / models the implementation added or changed. Those doc updates
are **never** out-of-scope and must **never** be excised or filed as backlog tickets — doing so
just churns the docs the implement agent correctly wrote (the exact failure that produced the
`scope-spillover` doc tickets this rule removes).

Before any excision (3.6.1) or ticketing (3.6.2), **drop every `[OOS]` entry whose file/area is
a documentation file** — its file/area (the text before the `—` separator) matching
`ARCHITECTURE.md`, `PROJECT_STRUCTURE.md`, `ENV_VARIABLES.md`, `README.md`, or `CLAUDE.md`, or
under `docs/`. Only non-doc (code / config / seed) OOS entries proceed to remediation. Log
each dropped doc entry:
`echo "scope-enforcement: doc change kept in-scope (not excised/ticketed): <entry>"`.

For each remaining (non-doc) `[OOS]` entry:

### 3.6.1 — Attempt excision (if `EXCISE_OOS=true`)

Try to revert the out-of-scope change from the branch:

```bash
# For a whole file change, restore from main:
git checkout main -- <file>
git add <file>
git commit -m "revert: excise out-of-scope change in <file> (scope enforcement)"

# For a partial hunk: apply a targeted reverse patch
# If excision cannot be applied cleanly (conflicts), fall back to Block (see below).
```

After excision:
- Re-run the in-scope tests to confirm the excision didn't break anything:
  ```bash
  cd backend && python -m pytest tests/ -x -q 2>/dev/null || true
  ```
- If tests pass → excision succeeded; continue to 3.6.2.
- If tests fail or revert won't apply cleanly → skip excision, note the failure, proceed to 3.6.2 anyway (backlog ticket is always created regardless of excision outcome).

### 3.6.2 — Create backlog ticket

Before filing tickets, deduplicate OOS entries against each other and against the
existing `scope-spillover` backlog.

**Populate `OOS_ENTRIES` array from `$CONFORMANCE_DIALOGUE`** (the accumulated reviewer output
set in Phase 3.1 step 5 and appended on each reconcile cycle):

```bash
OOS_ENTRIES=()
while IFS= read -r line; do
  stripped="${line#- }"
  [[ "$stripped" == \[OOS\]* ]] || continue
  # Documentation exemption (3.6.0): the factory maintains docs in-scope (implement Phase 4
  # DOCUMENT), so doc-map updates are never excised/ticketed. Match only the file/area
  # (before the em-dash) so a code finding that merely *mentions* a doc isn't dropped.
  area="${stripped%%—*}"
  if printf '%s' "$area" | grep -qiE '(^|[^a-z0-9_])(ARCHITECTURE|PROJECT_STRUCTURE|ENV_VARIABLES|README|CLAUDE)\.md([^a-z0-9]|$)|(^|[^a-z])docs/'; then
    echo "scope-enforcement: doc change kept in-scope (not excised/ticketed): $stripped"
    continue
  fi
  OOS_ENTRIES+=("$stripped")
done <<< "$CONFORMANCE_DIALOGUE"
```

Skip to 3.6.3 if `${#OOS_ENTRIES[@]} -eq 0`.

**Step A — Fetch existing open spillover issues:**

```bash
SPILLOVER_JSON=$(gh issue list \
  --repo "$FACTORY_REPO_SLUG" \
  --label "$BACKLOG_LABEL" \
  --state open \
  --json number,title,body \
  --limit 200 2>/dev/null || echo "[]")
```

**Step B — Build OOS JSON array and call `dedupe_oos.py` (fail-open):**

```bash
OOS_ENTRIES_JSON=$(python3 -c \
  "import json,sys; entries=sys.argv[1:]; print(json.dumps(entries))" \
  "${OOS_ENTRIES[@]}")

DEDUPE_OUT=$(python3 dark-factory/scripts/dedupe_oos.py \  # TARGET-PATH
  --oos "$OOS_ENTRIES_JSON" --spillovers "$SPILLOVER_JSON" 2>/tmp/dedupe_err.txt) \
  && ACTION_LIST="$DEDUPE_OUT" \
  || {
    echo "dedupe_oos.py failed ($(cat /tmp/dedupe_err.txt)) — falling back to create-per-finding"
    ACTION_LIST=$(echo "$OOS_ENTRIES_JSON" | python3 -c \
      "import json,sys; print(json.dumps([{'entry':e,'action':'create','key':''} for e in json.load(sys.stdin)]))")
  }
```

**Step C — Process actions (whether excision succeeded or not):**

Use process substitution so `SPILLOVER_TICKETS` mutations survive outside the loop:

```bash
SPILLOVER_TICKETS=""

while IFS='|' read -r ACTION ENTRY KEY; do
  case "$ACTION" in
    create)
      SPILLOVER_TITLE="<short title derived from ENTRY>"
      DEDUP_KEY_COMMENT="<!-- dedup-key: ${KEY} -->"
      SPILLOVER_BODY="## Scope spillover from #${ISSUE_NUM}

The dark factory noticed this pre-existing defect while implementing issue #${ISSUE_NUM} but did not fix it inline (scope enforcement).

**File/area:** <file from ENTRY>
**Defect:** <description from ENTRY>

${DEDUP_KEY_COMMENT}

---
*Automatically triaged by ${FACTORY_PRODUCT_NAME} Dark Factory scope enforcement.*"

      SPILLOVER_URL=$(gh issue create \
        --repo "$FACTORY_REPO_SLUG" \
        --title "$SPILLOVER_TITLE" \
        --body "$SPILLOVER_BODY" \
        --label "needs-triage,${BACKLOG_LABEL}")
      SPILLOVER_NUM=$(basename "$SPILLOVER_URL")
      # TARGET-PATH
      python3 dark-factory/scripts/factory_core/cli.py board-add \
        --issue "$SPILLOVER_NUM" --url "$SPILLOVER_URL" \
        || echo "scope-enforcement: WARNING board-add failed for spillover #${SPILLOVER_NUM}" >&2
      SPILLOVER_TICKETS="$SPILLOVER_TICKETS $SPILLOVER_NUM"
      echo "scope-enforcement: created new spillover #${SPILLOVER_NUM} (key: $KEY)"
      ;;
    comment:*)
      EXISTING_NUM="${ACTION#comment:}"
      gh issue comment "$EXISTING_NUM" \
        --repo "$FACTORY_REPO_SLUG" \
        --body "**Scope enforcement (re-observed):** This finding was re-surfaced while implementing issue #${ISSUE_NUM}.

**Entry:** ${ENTRY}

No new ticket created — deduped against this issue."
      echo "scope-enforcement: commented on existing spillover #${EXISTING_NUM} (key: $KEY)"
      ;;
    suppress)
      echo "scope-enforcement: suppressed non-actionable finding (key: $KEY): $ENTRY"
      ;;
  esac
done < <(echo "$ACTION_LIST" | python3 -c \
  "import json,sys; [print(r['action']+'|'+r['entry']+'|'+r.get('key','')) for r in json.load(sys.stdin)]")
```

Collect all created ticket numbers into `SPILLOVER_TICKETS` (space-separated list).

### 3.6.3 — Comment on origin issue

After all OOS entries are processed:

```bash
EXCISED_COUNT=<number of successfully excised changes>
TICKET_LIST=$(echo "$SPILLOVER_TICKETS" | tr ' ' '\n' | sed 's/^/#/' | tr '\n' ' ')
gh issue comment "$ISSUE_NUM" --body "**Scope enforcement:** excised ${EXCISED_COUNT} out-of-scope change(s) from this branch. Each unrelated defect has been filed as a linked backlog ticket: ${TICKET_LIST}

The branch is now clean. These tickets are ready for triage."
```

### 3.6.4 — Resume normal flow

After scope remediation (regardless of excision success/failure), re-run the conformance review with the updated diff (Step 3.1 again), then proceed to the verdict check (Step 3.1 step 7).

Store `SPILLOVER_TICKETS` so the `report` node can include it.

## Phase 3.5: RECONCILE LOOP (Material divergence only)

1. Increment `CONFORMANCE_CYCLE`
2. If `CONFORMANCE_CYCLE > MAX_CYCLES` → go to Phase 5 (BLOCKED)
3. Read the MATERIAL deviation descriptions from the conformance reviewer output
4. Fix the code to align with the spec:
   - Write a failing test that targets the missing/wrong behavior
   - Run the test to confirm it fails: `cd backend && python -m pytest <test_path> -x -v`
   - Implement the fix
   - Run the test to confirm it passes
   - Commit: `git add -A && git commit -m "fix: align implementation with spec (conformance cycle $CONFORMANCE_CYCLE)"`
5. Re-get the diff:
   ```bash
   git diff main...HEAD -- ':!*.lock' ':!docs/*.md' ':!evals/*.md' ':!bench/*.md' ':!.archon/memory/**' 2>/dev/null | head -1000 > "$ARTIFACTS_DIR/conformance_reconcile_diff.txt"
   TRIAGED_DIFF=$(cat "$ARTIFACTS_DIR/conformance_reconcile_diff.txt")
   ```
5a. Re-render the conformance prompt: rebuild `$ARTIFACTS_DIR/conformance_artifact_content.md`
    with the updated `$TRIAGED_DIFF` (same heredoc as Step 3.1 item 2), then re-run Step 3.1's
    item 3a `render-prompt` invocation unchanged — a failure here is a hard stop for the phase,
    identically to Step 3.1.
6. Re-spawn the conformance reviewer subagent with the verbatim contents of
   `$ARTIFACTS_DIR/conformance_prompt.md` produced by step 5a
7. Prepend `Cycle $CONFORMANCE_CYCLE:` header and append the new output to `CONFORMANCE_DIALOGUE` with a `---` separator
7a. If `$SHADOW_MODEL_PIN` is non-empty, re-spawn the shadow subagent too (mirroring Step
    3.1's 5a for this cycle — prompt is the verbatim contents of
    `$ARTIFACTS_DIR/conformance_prompt.md` produced by step 5a). Prepend
    `Cycle $CONFORMANCE_CYCLE:` and append its response to `SHADOW_DIALOGUE` with a `---`
    separator, mirroring `CONFORMANCE_DIALOGUE`'s cycle numbering one-to-one. Update
    `SHADOW_MODEL`/`SHADOW_STATUS`/`SHADOW_FINDINGS_COUNT`/`SHADOW_SEVERITY`/
    `SHADOW_VERDICT_LINE` from this cycle's response (best-effort `UNCERTAIN` on any error).
8. Parse verdict again → loop back to step 1

## Phase 4: PASS — Write attestation

Write the attestation to `$ARTIFACTS_DIR/conformance.md`:

```bash
{
  emit_verdict "conformance" "PASS" "${MATERIAL_COUNT:-0}" "none"
  printf "VERDICT: %s\nCYCLES: %s\nNO_SPEC: %s\nOOS_EXCISED: %s\nOOS_TICKETS: %s\n" \
    "${CONFORMANCE_VERDICT:-UNKNOWN}" "${CONFORMANCE_CYCLE:-0}" "${NO_SPEC:-false}" \
    "${OOS_EXCISED:-0}" "${OOS_TICKETS:-}"
  printf "\n---\n\n%s\n" "${CONFORMANCE_DIALOGUE}"
} > "$ARTIFACTS_DIR/conformance.md"

if [ -n "${SHADOW_MODEL_PIN:-}" ]; then
  {
    printf "SHADOW_MODEL: %s\nSHADOW_STATUS: %s\nSHADOW_FINDINGS_COUNT: %s\nSHADOW_SEVERITY: %s\n" \
      "${SHADOW_MODEL_PIN}" "${SHADOW_STATUS:-UNCERTAIN}" "${SHADOW_FINDINGS_COUNT:-0}" "${SHADOW_SEVERITY:-none}"
    printf "\n---\n\n## Shadow (Fable) Review\n\n%s\n" "${SHADOW_DIALOGUE}"
  } >> "$ARTIFACTS_DIR/conformance.md"

  FOOTER=$(python3 dark-factory/scripts/factory_core/cli.py marker factory)  # TARGET-PATH
  SHADOW_BODY="<!-- df-shadow-review -->
## Shadow (Fable) Review — conformance

${SHADOW_VERDICT_LINE:-**Verdict:** (unparseable — SHADOW_STATUS: UNCERTAIN)}

\`\`\`
SHADOW_MODEL: ${SHADOW_MODEL_PIN}
SHADOW_STATUS: ${SHADOW_STATUS:-UNCERTAIN}
SHADOW_FINDINGS_COUNT: ${SHADOW_FINDINGS_COUNT:-0}
SHADOW_SEVERITY: ${SHADOW_SEVERITY:-none}
\`\`\`

---
$FOOTER"
  TMPFILE=$(mktemp /tmp/df-shadow-review-XXXXXX.md)
  printf '%s' "$SHADOW_BODY" > "$TMPFILE"
  python3 dark-factory/scripts/factory_core/providers/cli.py tracker comment \
    --id "$ISSUE_NUM" --marker "<!-- df-shadow-review -->" --body-file "$TMPFILE" || true  # TARGET-PATH — advisory post, never blocks PASS (Requirement 3)
  rm -f "$TMPFILE"
fi
```
(This is the new durable PASS-path comment Requirement 5 calls for — Phase 4 PASS today
posts no issue comment at all, so this is additive, gated entirely on the shadow having
actually run.)

If `CONFORMANCE_CYCLE > 0` (MATERIAL violations were found and resolved in this run), extract
violation data from `$CONFORMANCE_DIALOGUE` and write memory entries:

```bash
# Memory write: only when MATERIAL violations were found and resolved (CONFORMANCE_CYCLE > 0)
# (route_memory_file and write_memory_entry are sourced from gate_lib.sh at Phase 1 LOAD)
if [ "${CONFORMANCE_CYCLE:-0}" -gt 0 ]; then

  # Extract (VIOLATION_FILE, VIOLATION_TEXT) pairs from $CONFORMANCE_DIALOGUE.
  # $CONFORMANCE_DIALOGUE is the free-form output of the conformance reviewer subagent.
  # Read the conformance rubric (RUBRIC_CONTENT, resolved in Phase 1 step 3) to understand the reviewer's
  # exact output format, then parse $CONFORMANCE_DIALOGUE to build BLOCKING_VIOLATIONS as
  # newline-separated "FILE|TEXT" pairs where:
  #   FILE — the file path of the violation (e.g. backend/app/routers/scanner.py)
  #   TEXT — a one-sentence [AVOID] lesson derived from the violation description
  # If the reviewer output does not include structured file paths, use the catch-all target
  # (codebase-patterns.md) with an empty FILE prefix.
  BLOCKING_VIOLATIONS="${BLOCKING_VIOLATIONS:-}"

  MEMORY_WRITTEN=0

  while IFS='|' read -r VIOLATION_FILE VIOLATION_TEXT; do
    [ -z "$VIOLATION_TEXT" ] && continue

    TARGET=$(route_memory_file "${VIOLATION_FILE:-}")
    PATH_PREFIX=""
    if [ -n "$VIOLATION_FILE" ]; then
      PATH_PREFIX=$(dirname "$VIOLATION_FILE")/
    fi

    write_memory_entry "$TARGET" "$PATH_PREFIX" "$VIOLATION_TEXT" conformance "${ISSUE_NUM:-unknown}"
    MEMORY_WRITTEN=$((MEMORY_WRITTEN + 1))
    echo "memory-write: wrote [AVOID] to $TARGET (path:$PATH_PREFIX)"

  done << EOF
$BLOCKING_VIOLATIONS
EOF

  if [ "$MEMORY_WRITTEN" -gt 0 ]; then
    git add .archon/memory/
    git commit -m "memory: conformance lesson from #${ISSUE_NUM:-unknown}"
    echo "memory-write: committed $MEMORY_WRITTEN new [AVOID] entr(ies)"
  else
    echo "memory-write: no novel entries — skipping commit"
  fi
fi
```

Exit `0`. The `push-and-pr` and `report` nodes will proceed normally.

## Phase 5: BLOCKED — Material divergence unresolved

This phase is only reached if reconcile failed after `MAX_CYCLES`.

1. Post a "Spec Conformance — Blocked" comment on the issue:
   ```bash
   FOOTER=$(python3 dark-factory/scripts/factory_core/cli.py marker factory)  # TARGET-PATH
   # Requirements 4/5: a real shell guard, not a prose note inside the body string — when
   # the shadow never ran (SHADOW_MODEL_PIN empty) nothing is posted, so an empty
   # SHADOW_MODEL: line can never masquerade as an UNCERTAIN shadow verdict.
   SHADOW_BLOCK=""
   if [ -n "${SHADOW_MODEL_PIN:-}" ]; then
     SHADOW_BLOCK="## Shadow (Fable) Review — conformance

   ${SHADOW_VERDICT_LINE:-**Verdict:** (unparseable — SHADOW_STATUS: UNCERTAIN)}

   \`\`\`
   SHADOW_MODEL: $SHADOW_MODEL_PIN
   SHADOW_STATUS: ${SHADOW_STATUS:-UNCERTAIN}
   SHADOW_FINDINGS_COUNT: ${SHADOW_FINDINGS_COUNT:-0}
   SHADOW_SEVERITY: ${SHADOW_SEVERITY:-none}
   \`\`\`

   $SHADOW_DIALOGUE"
   fi
   gh issue comment $ISSUE_NUM --body "## Spec Conformance — Blocked

   The implementation has material divergences from the spec that could not be resolved in $MAX_CYCLES reconcile cycle(s).

   $CONFORMANCE_DIALOGUE

   $SHADOW_BLOCK

   ### Next Steps

   Review the deviations above and either:
   - Fix the implementation to match the spec, then re-run: \`docker compose --profile factory run --rm dark-factory \"Continue issue #$ISSUE_NUM\"\`
   - Update the spec to document the deviation as intentional, then re-run.
   - Add \`needs-discussion\` if the spec itself needs revisiting.

   ---
   $FOOTER"
   ```

2. Move the issue to **Blocked** on the project board:
   ```bash
   python3 dark-factory/scripts/factory_core/providers/cli.py \
     tracker set-status --id "$ISSUE_NUM" --status blocked  # TARGET-PATH
   ```

3. Add `needs-discussion` label:
   ```bash
   python3 dark-factory/scripts/factory_core/providers/cli.py tracker label --id $ISSUE_NUM --add needs-discussion  # TARGET-PATH
   ```

4. Write blocked status to `$ARTIFACTS_DIR/conformance.md`:
   ```bash
   {
     emit_verdict "conformance" "BLOCKED" "${MATERIAL_COUNT:-0}" "critical"
     printf "VERDICT: MATERIAL\nCYCLES: %s\n" "${CONFORMANCE_CYCLE:-0}"
     printf "\n---\n\n%s\n" "${CONFORMANCE_DIALOGUE}"
   } > "$ARTIFACTS_DIR/conformance.md"

   if [ -n "${SHADOW_MODEL_PIN:-}" ]; then
     {
       printf "SHADOW_MODEL: %s\nSHADOW_STATUS: %s\nSHADOW_FINDINGS_COUNT: %s\nSHADOW_SEVERITY: %s\n" \
         "${SHADOW_MODEL_PIN}" "${SHADOW_STATUS:-UNCERTAIN}" "${SHADOW_FINDINGS_COUNT:-0}" "${SHADOW_SEVERITY:-none}"
       printf "\n---\n\n## Shadow (Fable) Review\n\n%s\n" "${SHADOW_DIALOGUE}"
     } >> "$ARTIFACTS_DIR/conformance.md"
   fi
   ```

5. Exit non-zero (`exit 1`) — kept for forward-compatibility, but the actual enforcement is the
   `conformance-gate` DAG node (`workflows/archon-dark-factory.yaml`), which reads this file's
   `STATUS:` line directly and blocks `push-and-pr` (and everything chained after it, including
   `status-in-review`) on anything other than `PASS`/`SKIPPED`/`ERROR` — a `command:` node's
   internal `exit 1` does not reliably surface as node failure to the DAG executor (#212, #271).
