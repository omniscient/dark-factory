# Dispatch Ceiling (C9) Weekly Revisit — Execution Plan for #30

**Issue:** omniscient/dark-factory#30
**Spec:** `docs/superpowers/specs/2026-07-17-dispatch-ceiling-weekly-revisit-design.md`

## Goal

Execute one run of the recurring dispatch-ceiling keyword revisit for #30: fetch cumulative
Factory Scorecard data (`SINCE=2026-06-12` → `UNTIL=2026-07-17`), apply the existing decision
rules, post the analysis as a comment on #30, open a PR against `.archon/.env` only if a keyword
change is warranted, conditionally file an L-bucket code-change issue, and unconditionally file
the next weekly revisit issue (target `2026-07-24`).

## Architecture

**Operational analysis run, not a service change.** No production code, script, or config file
is created or modified by this ticket. `scripts/fetch_scorecard.py` and `scripts/ceiling_revisit.py`
are already implemented and unit-tested (`tests/test_fetch_scorecard.py`,
`tests/test_ceiling_revisit.py`) and are invoked exactly as they exist today. The only durable,
git-tracked artifacts this ticket produces are the spec (already committed) and this plan; every
other effect (issue comment, possible `.archon/.env` PR, possible new issues) is a GitHub-side
effect produced by literally running `.archon/commands/ceiling-revisit.md`'s five phases with this
run's parameters:

```
ISSUE_NUM=30 SINCE=2026-06-12 UNTIL=2026-07-17 NEXT_DATE=2026-07-24
```

Because no behavior changes, TDD (red→green→commit) does not apply here — there is no new code
path to pin with a failing test. Each task below instead states the exact command to run and the
*structural* shape of its expected output (this is a live analysis against real GitHub data, so
exact success-rate numbers cannot be predicted at plan-writing time — the decision rules in
`scripts/ceiling_revisit.py` are what compute them, and are already covered by
`tests/test_ceiling_revisit.py`).

**Memory pattern applied** (`.archon/memory/codebase-patterns.md`, issue #42): a refine-phase
spec/plan approved on this `refine/issue-30-*` branch does not automatically transfer to the
`feat/issue-30-*` branch the implement phase creates. Task 1 makes the implement agent copy both
docs over explicitly before doing anything else.

**Correction found during planning (not in the spec):** `.archon/.env` is listed in this repo's
`.gitignore` (line 41, "Local scheduler secrets for the self-target instance ... never cloned").
Verified empirically: `git add .archon/.env` exits 1 with "paths are ignored by one of your
.gitignore files." `.archon/commands/ceiling-revisit.md` Phase 3's literal `git add "$ENV_FILE"`
would therefore silently fail to stage the file if this run's data actually warrants a keyword
removal (assumed by the spec to be the common "no change" case, but not guaranteed — the window
has grown to ~5 weeks, so more keyword cohorts may cross the `n≥5` threshold than in prior runs).
Task 5 below runs Phase 3 with `git add -f "$ENV_FILE"` instead of a bare `git add` to keep this
one already-intentional, deliberate commit working — the command file itself is not modified,
this only changes how its snippet is invoked. This finding is noted in the Phase 4 architect
review request below so it's visible to the reviewer as an intentional, in-scope execution
correction rather than an unreviewed deviation.

## Tech Stack

Bash, Python 3 (`scripts/fetch_scorecard.py`, `scripts/ceiling_revisit.py` — both already on
`main`, unmodified), `gh` CLI. No new dependencies.

## File Structure

| Path | Purpose |
|---|---|
| `docs/superpowers/specs/2026-07-17-dispatch-ceiling-weekly-revisit-design.md` | Already committed (this ticket's spec) |
| `docs/superpowers/plans/2026-07-17-dispatch-ceiling-weekly-revisit-plan.md` | This plan (committed by the plan phase) |
| *(GitHub side effects only, below)* | Issue #30 comment; conditional PR touching `.archon/.env` on branch `chore/ceiling-revisit-2026-07-17`; conditional new L-bucket issue; unconditional new weekly-revisit issue |

No other repository file is created, modified, or deleted by this ticket.

---

## Task 1 — Bring the spec and plan onto the implementation branch

**Files:** `docs/superpowers/specs/2026-07-17-dispatch-ceiling-weekly-revisit-design.md`,
`docs/superpowers/plans/2026-07-17-dispatch-ceiling-weekly-revisit-plan.md` (copied, not
re-authored)

1. On the `feat/issue-30-*` branch the implement phase creates, verify both docs exist (they were
   committed on `refine/issue-30-revisit-dispatch-ceiling--c9------re-mea`, not automatically
   present on a fresh branch off `main`):
   ```bash
   git show refine/issue-30-revisit-dispatch-ceiling--c9------re-mea:docs/superpowers/specs/2026-07-17-dispatch-ceiling-weekly-revisit-design.md \
     > docs/superpowers/specs/2026-07-17-dispatch-ceiling-weekly-revisit-design.md
   git show refine/issue-30-revisit-dispatch-ceiling--c9------re-mea:docs/superpowers/plans/2026-07-17-dispatch-ceiling-weekly-revisit-plan.md \
     > docs/superpowers/plans/2026-07-17-dispatch-ceiling-weekly-revisit-plan.md
   ```
   Expected: both files now exist and are non-empty in the working tree (`git status --short`
   shows them as new/modified on the `feat/` branch).
2. Commit:
   ```bash
   git add docs/superpowers/specs/2026-07-17-dispatch-ceiling-weekly-revisit-design.md \
           docs/superpowers/plans/2026-07-17-dispatch-ceiling-weekly-revisit-plan.md
   git commit -m "docs: bring over approved spec/plan for issue #30"
   ```

---

## Task 2 — Pre-flight verification

**Files:** none (read-only verification)

1. Confirm no stale `.archon/.env` override is already active (the spec's decision rules assume
   the `config/config.yaml` default is currently effective):
   ```bash
   test -f .archon/.env && grep '^ABOVE_CEILING_KEYWORDS=' .archon/.env || echo "no override active"
   ```
   Expected: `no override active` (matches spec Assumption).
2. Confirm `gh` auth and repo targeting are correct:
   ```bash
   gh auth status
   echo "$FACTORY_REPO_SLUG"
   ```
   Expected: authenticated to `github.com` as the factory account; `FACTORY_REPO_SLUG` prints
   `omniscient/dark-factory`.
3. Confirm the labels Tasks 5–7 use already exist in the repo (`gh issue create`/`gh pr create`
   fail outright on an unknown label):
   ```bash
   gh label list --repo "$FACTORY_REPO_SLUG" --search "priority: should-have"
   gh label list --repo "$FACTORY_REPO_SLUG" --search "enhancement"
   gh label list --repo "$FACTORY_REPO_SLUG" --search "size: S"
   gh label list --repo "$FACTORY_REPO_SLUG" --search "ready-for-agent"
   ```
   Expected: all four labels are listed (they are standard labels already used by every prior
   issue in this lineage — #29, #31, #112, #119, #32).

No commit — this task only reads state.

---

## Task 3 — Phase 1: Fetch and analyze

**Files:** none tracked (writes transient `/tmp/ceiling-revisit-scorecard.json`,
`/tmp/ceiling-revisit-report.md`, `/tmp/ceiling-revisit-meta.txt`)

1. Run the fetch + analysis exactly as `.archon/commands/ceiling-revisit.md` Phase 1 specifies,
   with this run's corrected parameters:
   ```bash
   SINCE=2026-06-12
   UNTIL=2026-07-17
   SCORECARD=/tmp/ceiling-revisit-scorecard.json
   REPORT_FILE=/tmp/ceiling-revisit-report.md

   python3 dark-factory/scripts/fetch_scorecard.py \
     --since "$SINCE" \
     --until "$UNTIL" \
     --output "$SCORECARD"

   python3 dark-factory/scripts/ceiling_revisit.py \
     --since "$SINCE" \
     --until "$UNTIL" \
     --scorecard "$SCORECARD" \
     --output "$REPORT_FILE" \
     2>/tmp/ceiling-revisit-meta.txt
   ```
   Expected: `fetch_scorecard.py` prints `Fetching PRs…` / `Fetching issues…` /
   `Computing churn (git blame survival)…` progress lines to stderr and ends with
   `Wrote /tmp/ceiling-revisit-scorecard.json`; `ceiling_revisit.py` writes
   `/tmp/ceiling-revisit-report.md` containing a `### Per-Bucket Triad` table (rows `S`, `M`,
   `L+XL`) and a `### Per-Keyword Analysis` table (one row per `|`-delimited keyword in
   `migration|migrate|performance|perf|architectur|refactor`); `/tmp/ceiling-revisit-meta.txt`
   ends with a line starting `<!-- CEILING_REVISIT_JSON {"keywords_to_remove": [...],
   "new_keyword_candidates": [...], "l_bucket_needs_issue": <bool>} -->`.
2. Extract the recommendation:
   ```bash
   REC_JSON=$(grep 'CEILING_REVISIT_JSON' /tmp/ceiling-revisit-meta.txt \
     | sed 's/.*CEILING_REVISIT_JSON \(.*\) -->/\1/')
   KEYWORDS_TO_REMOVE=$(echo "$REC_JSON" | python3 -c \
     "import sys,json; d=json.load(sys.stdin); print('|'.join(d['keywords_to_remove']))")
   L_NEEDS_ISSUE=$(echo "$REC_JSON" | python3 -c \
     "import sys,json; d=json.load(sys.stdin); print(d['l_bucket_needs_issue'])")
   echo "KEYWORDS_TO_REMOVE=$KEYWORDS_TO_REMOVE"
   echo "L_NEEDS_ISSUE=$L_NEEDS_ISSUE"
   ```
   Expected: both variables print without error (empty `KEYWORDS_TO_REMOVE` and `L_NEEDS_ISSUE=False`
   is the spec's expected common case, but is not asserted here — the live data decides).

No commit — all outputs are transient `/tmp` files, not repo content.

---

## Task 4 — Phase 2: Post the analysis comment on #30

**Files:** none tracked (GitHub side effect only)

1. Post the report generated in Task 3 as a comment on issue #30 (not #112, #119, or #32):
   ```bash
   REPORT_BODY=$(cat /tmp/ceiling-revisit-report.md)
   gh issue comment 30 --repo "$FACTORY_REPO_SLUG" --body "$REPORT_BODY"
   ```
   Expected: `gh` prints the URL of the newly created comment
   (`https://github.com/omniscient/dark-factory/issues/30#issuecomment-...`).

No commit — no repository file changes.

---

## Task 5 — Phase 3: Open a PR to `.archon/.env` (conditional on `KEYWORDS_TO_REMOVE`)

**Files:** `.archon/.env` (new or modified, on a separate branch `chore/ceiling-revisit-2026-07-17`
— not on `feat/issue-30-*`)

Only run this task if `KEYWORDS_TO_REMOVE` (from Task 3) is non-empty.

```bash
if [ -n "$KEYWORDS_TO_REMOVE" ]; then
  ENV_FILE=".archon/.env"
  if [ -f "$ENV_FILE" ] && grep -q "^ABOVE_CEILING_KEYWORDS=" "$ENV_FILE"; then
    CURRENT=$(grep '^ABOVE_CEILING_KEYWORDS=' "$ENV_FILE" | cut -d= -f2-)
  else
    CURRENT="migration|migrate|performance|perf|architectur|refactor"
  fi

  NEW_KWS="$CURRENT"
  for KW in $(echo "$KEYWORDS_TO_REMOVE" | tr '|' '\n'); do
    NEW_KWS=$(echo "$NEW_KWS" | sed "s/|${KW}//g;s/${KW}|//g;s/^${KW}$//g")
  done

  if [ -f "$ENV_FILE" ] && grep -q "^ABOVE_CEILING_KEYWORDS=" "$ENV_FILE"; then
    sed -i "s|^ABOVE_CEILING_KEYWORDS=.*|ABOVE_CEILING_KEYWORDS=${NEW_KWS}|" "$ENV_FILE"
  else
    echo "ABOVE_CEILING_KEYWORDS=${NEW_KWS}" >> "$ENV_FILE"
  fi

  PR_BRANCH="chore/ceiling-revisit-2026-07-17"
  # Cut from origin/main, NOT from feat/issue-30-* — Task 1 already committed the spec+plan
  # docs onto feat/issue-30-*, and those commits aren't on main yet. Branching from feat/
  # here would drag both docs into this PR's diff, turning a single-file env change into a
  # 3-file one. Branching from origin/main keeps the PR scoped to .archon/.env alone.
  git fetch origin main
  git checkout -b "$PR_BRANCH" origin/main
  # -f required: .archon/.env is gitignored (see "Correction found during planning" above) —
  # this is the one deliberate, intentional commit of that file this command produces.
  git add -f "$ENV_FILE"
  git commit -m "chore(env): update ABOVE_CEILING_KEYWORDS per weekly revisit (#30)

Removing: ${KEYWORDS_TO_REMOVE}
New value: ${NEW_KWS}

Analysis window: 2026-06-12 → 2026-07-17
Decision: n>=5 and keyword success rate >= M_baseline (no discriminative value)."

  git push origin "$PR_BRANCH"
  gh pr create \
    --repo "$FACTORY_REPO_SLUG" \
    --title "chore(env): update ABOVE_CEILING_KEYWORDS per weekly ceiling revisit" \
    --body "Recommended by weekly dispatch ceiling analysis on issue #30.

Removes: \`${KEYWORDS_TO_REMOVE}\`

See the analysis comment on #30 for full data and decision rationale.

Related to #30 (not auto-closing — #30's own deliverable is the spec/plan landing via the
implement PR plus the analysis comment; this env change is reviewed and merged independently)." \
    --label "priority: should-have" \
    --base main

  git checkout -   # return to feat/issue-30-* for the remaining tasks
fi
```
Expected (only if the branch runs): `gh pr create` prints the new PR's URL scoped to a single
`.archon/.env` diff against `main`; `git status` on `feat/issue-30-*` after `git checkout -`
shows no pending changes from this block (they live on `chore/ceiling-revisit-2026-07-17`
instead).

No commit on `feat/issue-30-*` — the commit above belongs to the separate `chore/` branch.

---

## Task 6 — Phase 4: File the L-bucket code-change issue (conditional on `L_NEEDS_ISSUE`)

**Files:** none tracked (GitHub side effect only)

Only run this task if `L_NEEDS_ISSUE` (from Task 3) is `True`.

```bash
if [ "$L_NEEDS_ISSUE" = "True" ]; then
  gh issue create \
    --repo "$FACTORY_REPO_SLUG" \
    --title "Revisit L=always-above-ceiling rule in is_above_ceiling() — scheduler.sh" \
    --body "## Purpose

The weekly dispatch ceiling analysis (issue #30, window 2026-06-12→2026-07-17)
found the L-bucket success rate exceeds 70% at n≥5. The L=always-above-ceiling rule
in \`scheduler.sh\` may be overly conservative.

## What to review

- Inspect \`is_above_ceiling()\` in \`scheduler.sh\` (~line 213).
- Assess whether the L-bucket ceiling should be relaxed (e.g. L+keyword pattern only).
- This is a **code change** (not an env-var change) — requires PR to \`scheduler.sh\`.
- Prior instances of this same finding: #29, #31 (both closed).

## References

- Triggering analysis: issue #30
- Policy: docs/superpowers/specs/2026-07-17-dispatch-ceiling-weekly-revisit-design.md

---
*Filed automatically by weekly ceiling revisit*" \
    --label "enhancement" \
    --label "priority: should-have"
fi
```
Expected (only if the branch runs): `gh issue create` prints the new issue's URL.

No commit — no repository file changes.

---

## Task 7 — Phase 5: File the next weekly revisit issue (unconditional)

**Files:** none tracked (GitHub side effect only)

```bash
NEXT_DATE=2026-07-24
gh issue create \
  --repo "$FACTORY_REPO_SLUG" \
  --title "Revisit dispatch ceiling — re-measure success-by-size/type" \
  --body "## Purpose

Weekly revisit of the size/type-aware dispatch ceiling policy.

## What to review

1. Pull the Factory Scorecard success-by-S/M/L numbers for the latest week.
2. Compare against current ABOVE_CEILING_KEYWORDS thresholds.
3. Assess keyword false-positive rate. If high, narrow the list.
4. Recommend \`ABOVE_CEILING_KEYWORDS\` update in \`.archon/.env\` via PR if data warrants.

## References

- Policy: docs/superpowers/specs/2026-07-17-dispatch-ceiling-weekly-revisit-design.md
- Archon command: \`commands/ceiling-revisit.md\`
- Prior revisit: #30 (comment with results)

## Parameters for the agent

- \`ISSUE_NUM\` = <this issue's number>
- \`SINCE\` = 2026-06-12 (policy introduction date — always fixed)
- \`UNTIL\` = ${NEXT_DATE}
- \`NEXT_DATE\` = <UNTIL + 7 days>

## Target date

**${NEXT_DATE}** (weekly from 2026-07-17).

---
*Filed automatically by ${FACTORY_PRODUCT_NAME} weekly ceiling revisit agent*" \
  --label "enhancement" \
  --label "priority: should-have" \
  --label "size: S" \
  --label "ready-for-agent"
```
Expected: `gh issue create` prints the new issue's URL; `--label "size: S"` (the command
template already carries the `size: S` fix from #119, per the spec's "Label drift" open
question — no template edit needed here).

No commit — no repository file changes.

---

## Task 8 — Final verification

**Files:** none (verification only)

1. Run the full test suite exactly as CI does — expect zero regressions, since no script/config
   under test was modified:
   ```bash
   python -m pytest tests/ -v
   ```
   Expected: all tests pass, including `tests/test_ceiling_revisit.py` and
   `tests/test_fetch_scorecard.py` unchanged from their `main` baseline.
2. Run the workflow DAG / smoke checks CI runs alongside pytest:
   ```bash
   python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
   bash smoke_gate.sh
   ```
3. Confirm the `feat/issue-30-*` branch only carries the two docs from Task 1. Use the two-dot
   diff form, not three-dot — per `.archon/memory/codebase-patterns.md` (issue #250), three-dot
   (`origin/main...HEAD`) includes commits main merged independently after the branch forked,
   producing false-positive out-of-scope hits:
   ```bash
   git diff --name-only origin/main HEAD
   ```
   Expected: exactly
   `docs/superpowers/specs/2026-07-17-dispatch-ceiling-weekly-revisit-design.md` and
   `docs/superpowers/plans/2026-07-17-dispatch-ceiling-weekly-revisit-plan.md` — nothing else.
   (The conditional `.archon/.env` change, if Task 5 ran, lives on the separate
   `chore/ceiling-revisit-2026-07-17` branch/PR, not here.)
4. No further commit needed if step 3 is clean; if any stray file appears, remove it and commit
   the removal before moving on.
