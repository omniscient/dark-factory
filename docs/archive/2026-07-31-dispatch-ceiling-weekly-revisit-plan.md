# Dispatch Ceiling (C9) Weekly Revisit — Execution Plan for #294

**Issue:** omniscient/dark-factory#294
**Spec:** `docs/superpowers/specs/2026-07-28-dispatch-ceiling-weekly-revisit-design.md`

## Goal

Execute the second run of the recurring dispatch-ceiling keyword revisit for #294: fetch
cumulative Factory Scorecard data (`SINCE=2026-06-12` → `UNTIL`=implement-time execution date),
apply the existing decision rules, post the analysis as a comment on #294, open a PR against
`.archon/.env` only if a keyword change is warranted, conditionally file an XL-bucket code-change
issue, and unconditionally file the next weekly revisit issue (target `UNTIL + 7 days`).

## Architecture

**Operational analysis run, not a service change.** No production code, script, or config file
is created or modified by this ticket. `scripts/fetch_scorecard.py` and `scripts/ceiling_revisit.py`
are already implemented and unit-tested (`tests/test_fetch_scorecard.py`,
`tests/test_ceiling_revisit.py`) and are invoked exactly as they exist today. The only durable,
git-tracked artifacts this ticket produces are the spec (already committed) and this plan; every
other effect (issue comment, possible `.archon/.env` PR, possible new issues) is a GitHub-side
effect produced by literally running `commands/ceiling-revisit.md`'s five phases with this run's
parameters.

**`UNTIL` is computed at implement time, not frozen here.** The spec's own Assumptions section is
explicit: "if a multi-day gap occurs before implement actually runs, the implement agent should
re-derive `UNTIL` as its own execution date rather than reusing 2026-07-28 verbatim." That gap has
already materialized once in this ticket's own lifecycle — the spec was committed 2026-07-28 but
this plan is being written 2026-07-31, three days later. Task 3 therefore computes `UNTIL` and
`NEXT_DATE` from the actual UTC date at run time (`date -u +%Y-%m-%d`), per
`commands/ceiling-revisit.md`'s own Inputs contract ("`$UNTIL` — ... today's date when the agent
runs"), rather than hardcoding `2026-07-28`/`2026-08-04`.

**Computed values are persisted across tasks, not just kept in shell variables.** Tasks 2–7 each
run as separate shell invocations, so a plain `SINCE=...`/`UNTIL=...` set in one task's shell does
not exist in the next task's shell. Task 2 and Task 3 append every value they compute to
`/tmp/ceiling-revisit-vars.sh` (`CURRENT_KEYWORDS`, `SINCE`, `UNTIL`, `NEXT_DATE`,
`KEYWORDS_TO_REMOVE`, `L_NEEDS_ISSUE`), and Tasks 3–7 each `source` it as their first step before
using any of these values. Task 7 — the one unconditional, durable deliverable — additionally
asserts `UNTIL`/`NEXT_DATE` are non-empty before filing, so a broken source chain fails loudly
instead of filing next week's issue with an empty date and corrupting this lineage's own seed.

Because no behavior changes, TDD (red→green→commit) does not apply here — there is no new code
path to pin with a failing test. Each task below instead states the exact command to run and the
*structural* shape of its expected output (this is a live analysis against real GitHub data, so
exact success-rate numbers cannot be predicted at plan-writing time — the decision rules in
`scripts/ceiling_revisit.py` are what compute them, and are already covered by
`tests/test_ceiling_revisit.py`).

**Memory patterns applied** (`.archon/memory/codebase-patterns.md`):
- Issue #42: a refine-phase spec/plan approved on this `refine/issue-294-*` branch does not
  automatically transfer to the `feat/issue-294-*` branch the implement phase creates. Task 1
  makes the implement agent copy both docs over explicitly before doing anything else.
- Issue #250: use the two-dot diff form (`git diff origin/main HEAD`) for out-of-scope checks,
  not three-dot — three-dot includes commits main merged independently after the branch diverged,
  producing false-positive OOS hits. Task 8 uses two-dot.

**Three corrections carried forward from the human-reviewed spec (operator spec review,
2026-07-31, commit `3b365ad`) — not rediscovered here, applied directly:**

1. **Command path:** the authoritative command lives at `commands/ceiling-revisit.md` (the
   `.archon/commands/ceiling-revisit.md` mirror is byte-identical but the spec standardizes on the
   non-prefixed path). Tasks below reference `commands/ceiling-revisit.md`.
2. **XL, not L, is the always-above-ceiling bucket.** `scripts/scheduler_lib.sh`'s
   `is_above_ceiling()` (verified in this checkout) shows only `XL` unconditionally returns
   above-ceiling; `L` falls through to the default (below-ceiling) case since commit `4feef16`
   (2026-06-21). Only `M` tickets are keyword-gated. `scripts/ceiling_revisit.py` already reports
   the merged `L+XL` bucket correctly (`build_bucket_table`, `l_bucket_needs_issue`) — no code
   fix needed. What needed fixing was prose: `commands/ceiling-revisit.md` Phase 4's conditional
   issue-filing text still says "Revisit L=always-above-ceiling rule," which is stale and would
   mislead whoever picks up that filed issue into thinking a rule exists that was already relaxed
   in #185-lineage work. Fixing the command template itself is out of this ticket's scope (the
   SCOPE BOUNDARY forbids editing anything outside `docs/superpowers/plans/`, and the command is a
   shared, multi-ticket artifact that deserves its own reviewed change). Task 6 below instead
   overrides the issue title/body inline with corrected XL-accurate wording when invoking that
   phase — the same "apply a correction without editing the shared command file" pattern #30's
   plan already used for the `.env` `-f` flag.
3. **`.env` secrets-staging guard.** `.archon/.env` is a general-purpose, gitignored local-secrets
   file for the self-target instance (`.gitignore:41`) — not a file dedicated solely to
   `ABOVE_CEILING_KEYWORDS`. A real deployment may have accumulated other secret lines in it
   (API tokens, credentials) since #30's run. Blindly `git add -f .archon/.env` (as #30's Task 5
   did, when the file was still empty) would stage and leak *every* line currently in that file
   into a public PR, not just the keyword change. Task 5 below writes a **fresh, minimal**
   `.archon/.env` on the throwaway `chore/` branch containing only the single
   `ABOVE_CEILING_KEYWORDS=...` line before staging it, so the committed/PR'd content is
   provably limited to that one variable regardless of what the real working copy contains.

## Tech Stack

Bash, Python 3 (`scripts/fetch_scorecard.py`, `scripts/ceiling_revisit.py` — both already on
`main`, unmodified), `gh` CLI. No new dependencies.

## File Structure

| Path | Purpose |
|---|---|
| `docs/superpowers/specs/2026-07-28-dispatch-ceiling-weekly-revisit-design.md` | Already committed (this ticket's spec) |
| `docs/superpowers/plans/2026-07-31-dispatch-ceiling-weekly-revisit-plan.md` | This plan (committed by the plan phase) |
| *(GitHub side effects only, below)* | Issue #294 comment; conditional PR touching `.archon/.env` on branch `chore/ceiling-revisit-<UNTIL>`; conditional new XL-bucket issue; unconditional new weekly-revisit issue |

No other repository file is created, modified, or deleted by this ticket.

---

## Task 1 — Bring the spec and plan onto the implementation branch

**Files:** `docs/superpowers/specs/2026-07-28-dispatch-ceiling-weekly-revisit-design.md`,
`docs/superpowers/plans/2026-07-31-dispatch-ceiling-weekly-revisit-plan.md` (copied, not
re-authored)

1. On the `feat/issue-294-*` branch the implement phase creates, verify both docs exist (they were
   committed on `refine/issue-294-revisit-dispatch-ceiling-----re-measure-`, not automatically
   present on a fresh branch off `main`). The implement phase runs from a **fresh clone**
   (`entrypoint.sh`), where the refine branch exists only as `refs/remotes/origin/refine/...`, not
   as a local branch — a bare `git show refine/issue-294-...:<path>` does not resolve there (git's
   revision DWIM checks `refs/remotes/<name>`, not `refs/remotes/origin/<name>`), and the `>`
   redirect would silently create an **empty** file that then gets committed. Fetch explicitly and
   reference the `origin/` remote-tracking ref, and assert non-emptiness before trusting the copy:
   Also capture the `origin/main` commit this branch was actually cut from, for Task 8's final
   out-of-scope check — Task 5 (if it runs) does its own `git fetch origin main`, which can
   advance the remote-tracking ref further and make a same-invocation `origin/main` diff surface
   unrelated files main picked up independently after this branch forked:
   ```bash
   git rev-parse origin/main > /tmp/ceiling-revisit-base-main
   REFINE_BRANCH="refine/issue-294-revisit-dispatch-ceiling-----re-measure-"
   git fetch origin "$REFINE_BRANCH"
   git show "origin/${REFINE_BRANCH}:docs/superpowers/specs/2026-07-28-dispatch-ceiling-weekly-revisit-design.md" \
     > docs/superpowers/specs/2026-07-28-dispatch-ceiling-weekly-revisit-design.md
   git show "origin/${REFINE_BRANCH}:docs/superpowers/plans/2026-07-31-dispatch-ceiling-weekly-revisit-plan.md" \
     > docs/superpowers/plans/2026-07-31-dispatch-ceiling-weekly-revisit-plan.md
   test -s docs/superpowers/specs/2026-07-28-dispatch-ceiling-weekly-revisit-design.md
   test -s docs/superpowers/plans/2026-07-31-dispatch-ceiling-weekly-revisit-plan.md
   ```
   Expected: both files now exist and are non-empty in the working tree (`git status --short`
   shows them as new/modified on the `feat/` branch; both `test -s` checks exit 0). If either
   `test -s` fails, stop — this ticket's only durable artifact would otherwise be silently lost.
2. Commit:
   ```bash
   git add docs/superpowers/specs/2026-07-28-dispatch-ceiling-weekly-revisit-design.md \
           docs/superpowers/plans/2026-07-31-dispatch-ceiling-weekly-revisit-plan.md
   git commit -m "docs: bring over approved spec/plan for issue #294"
   ```

---

## Task 2 — Pre-flight verification

**Files:** none (read-only verification)

**All variables computed in this task and Task 3 must survive into Tasks 3–7, which each run as
separate shell invocations** — plain shell variables do not persist across them. Every step below
that computes a new value appends it to a single sourceable file,
`RUN_VARS=/tmp/ceiling-revisit-vars.sh`; every later task's first step sources it. Start fresh:
```bash
RUN_VARS=/tmp/ceiling-revisit-vars.sh
rm -f "$RUN_VARS"
```

1. Determine the currently effective `ABOVE_CEILING_KEYWORDS` and capture it as `CURRENT_KEYWORDS`
   for Task 3 and Task 5 to both consume. `.archon/.env` is gitignored and is **not** part of this
   checkout in a real run — the scheduler provisions it straight to
   `/opt/dark-factory/.archon/.env` for compose's `env_file` (`scheduler.sh:107-116`), so an active
   override actually shows up as the `$ABOVE_CEILING_KEYWORDS` container environment variable, not
   as a file to grep here. Check, in order: the container env var, then a local `.archon/.env`
   (defensive fallback, in case this ever runs outside the normal container flow), then the
   `config/config.yaml` default:
   ```bash
   if [ -n "${ABOVE_CEILING_KEYWORDS:-}" ]; then
     CURRENT_KEYWORDS="$ABOVE_CEILING_KEYWORDS"
     echo "override active (container env): $CURRENT_KEYWORDS"
   elif [ -f .archon/.env ] && grep -q '^ABOVE_CEILING_KEYWORDS=' .archon/.env; then
     CURRENT_KEYWORDS=$(grep '^ABOVE_CEILING_KEYWORDS=' .archon/.env | cut -d= -f2-)
     echo "override active (local .archon/.env): $CURRENT_KEYWORDS"
   else
     CURRENT_KEYWORDS=$(python3 -c "import yaml; print(yaml.safe_load(open('config/config.yaml'))['dispatch_ceiling']['keywords'])")
     echo "no override active — using config/config.yaml default: $CURRENT_KEYWORDS"
   fi
   echo "CURRENT_KEYWORDS=\"$CURRENT_KEYWORDS\"" >> "$RUN_VARS"
   ```
   Expected: `no override active — using config/config.yaml default: migration|migrate|performance|perf|architectur|refactor`
   (matches spec Assumption). If an override *is* found via either path, `CURRENT_KEYWORDS` carries
   that value instead — Task 3's `--keywords` flag and Task 5's diff both key off this same
   variable (via `$RUN_VARS`), so there is no drift between what was analyzed and what gets
   modified.
2. Confirm `gh` auth and repo targeting are correct:
   ```bash
   gh auth status
   echo "$FACTORY_REPO_SLUG"
   echo "${FACTORY_PRODUCT_NAME:-<unset>}"
   ```
   Expected: authenticated to `github.com` as the factory account; `FACTORY_REPO_SLUG` prints
   `omniscient/dark-factory`; `FACTORY_PRODUCT_NAME` prints a product name, not `<unset>` (Task 7's
   footer degrades gracefully either way, but flag it here if unset since it means
   `scripts/identity.sh` wasn't sourced for this shell).
3. Confirm the labels Tasks 5–7 use already exist in the repo (`gh issue create`/`gh pr create`
   fail outright on an unknown label). Use an exact-match check that actually fails on a missing
   label — `gh label list --search` exits 0 even with zero matches, so a bare visual read of its
   output is not a real assertion:
   ```bash
   for LBL in "priority: should-have" "enhancement" "size: S" "ready-for-agent"; do
     gh label list --repo "$FACTORY_REPO_SLUG" --json name --jq '.[].name' | grep -qxF "$LBL" \
       && echo "OK: $LBL" || echo "MISSING: $LBL"
   done
   ```
   Expected: `OK: <label>` for all four (they are standard labels already used by every prior
   issue in this lineage — #29, #31, #112, #119, #30, #32). Stop and escalate if any prints
   `MISSING` — Task 7's `gh issue create` is unconditional and would otherwise fail after Tasks
   4-6 have already produced their side effects.

No commit — this task only reads state.

---

## Task 3 — Phase 1: Compute dates, fetch, and analyze

**Files:** none tracked (writes transient `/tmp/ceiling-revisit-scorecard.json`,
`/tmp/ceiling-revisit-report.md`, `/tmp/ceiling-revisit-meta.txt`)

0. Source the variables Task 2 persisted (`CURRENT_KEYWORDS`):
   ```bash
   RUN_VARS=/tmp/ceiling-revisit-vars.sh
   source "$RUN_VARS"
   ```
1. Compute `UNTIL`/`NEXT_DATE` from the actual execution date — do **not** reuse the spec's
   `2026-07-28`/`2026-08-04` literals verbatim; per the spec's own Assumptions, those were only
   valid if implement ran same-day as refine, which it did not (see Architecture above). Persist
   them to `$RUN_VARS` immediately — Tasks 4-7 all need `SINCE`/`UNTIL`, and Task 7 needs
   `NEXT_DATE`:
   ```bash
   SINCE=2026-06-12
   UNTIL=$(date -u +%Y-%m-%d)
   NEXT_DATE=$(date -u -d "${UNTIL} +7 days" +%Y-%m-%d)
   echo "SINCE=$SINCE UNTIL=$UNTIL NEXT_DATE=$NEXT_DATE"
   { echo "SINCE=\"$SINCE\""; echo "UNTIL=\"$UNTIL\""; echo "NEXT_DATE=\"$NEXT_DATE\""; } >> "$RUN_VARS"
   ```
   Expected: `UNTIL` prints today's UTC date (`YYYY-MM-DD`), `NEXT_DATE` is exactly 7 days later.
2. Run the fetch + analysis exactly as `commands/ceiling-revisit.md` Phase 1 specifies, with the
   dates computed above. Guard `CURRENT_KEYWORDS` first — unlike a missing `SINCE`/`UNTIL` (which
   `fetch_scorecard.py` rejects loudly with an isoformat error), an empty `--keywords ""` is
   accepted silently and produces a report with zero keyword rows, the ticket's primary
   deliverable, degraded without any error surfaced:
   ```bash
   test -n "$CURRENT_KEYWORDS" || { echo "FATAL: CURRENT_KEYWORDS not set — check /tmp/ceiling-revisit-vars.sh (Task 2 step 1 must have run)"; exit 1; }

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
     --keywords "$CURRENT_KEYWORDS" \
     --output "$REPORT_FILE" \
     2>/tmp/ceiling-revisit-meta.txt
   ```
   `--keywords "$CURRENT_KEYWORDS"` uses the value captured in Task 2 step 1 — without it,
   `ceiling_revisit.py` silently falls back to its own built-in `DEFAULT_KEYWORDS` constant
   (`scripts/ceiling_revisit.py:20,251`), which would analyze the wrong keyword set if an
   `.archon/.env` override is ever active in a future run. In the common case (no override,
   matching every prior run in this lineage) `CURRENT_KEYWORDS` equals `DEFAULT_KEYWORDS` anyway,
   so this is a no-op today but keeps the run correct once an override exists.

   Expected: `fetch_scorecard.py` prints progress lines to stderr and ends with
   `Wrote /tmp/ceiling-revisit-scorecard.json`; `ceiling_revisit.py` writes
   `/tmp/ceiling-revisit-report.md` containing a `### Per-Bucket Triad` table (rows `S`, `M`,
   `L+XL`) and a `### Per-Keyword Analysis` table (one row per `|`-delimited keyword in the
   currently effective `ABOVE_CEILING_KEYWORDS`, default
   `migration|migrate|performance|perf|architectur|refactor`); `/tmp/ceiling-revisit-meta.txt`
   ends with a line starting `<!-- CEILING_REVISIT_JSON {"keywords_to_remove": [...],
   "new_keyword_candidates": [...], "l_bucket_needs_issue": <bool>} -->`. `fetch_scorecard.py`
   computes git-blame churn per merged PR across the full ~7-week cumulative window, so this step
   can take several minutes with only intermittent stderr progress lines — that is expected, not
   a hang.
3. Extract the recommendation:
   ```bash
   REC_JSON=$(grep 'CEILING_REVISIT_JSON' /tmp/ceiling-revisit-meta.txt \
     | sed 's/.*CEILING_REVISIT_JSON \(.*\) -->/\1/')
   KEYWORDS_TO_REMOVE=$(echo "$REC_JSON" | python3 -c \
     "import sys,json; d=json.load(sys.stdin); print('|'.join(d['keywords_to_remove']))")
   L_NEEDS_ISSUE=$(echo "$REC_JSON" | python3 -c \
     "import sys,json; d=json.load(sys.stdin); print(d['l_bucket_needs_issue'])")
   echo "KEYWORDS_TO_REMOVE=$KEYWORDS_TO_REMOVE"
   echo "L_NEEDS_ISSUE=$L_NEEDS_ISSUE"
   { echo "KEYWORDS_TO_REMOVE=\"$KEYWORDS_TO_REMOVE\""; echo "L_NEEDS_ISSUE=\"$L_NEEDS_ISSUE\""; } >> "$RUN_VARS"
   ```
   Expected: both variables print without error (empty `KEYWORDS_TO_REMOVE` and `L_NEEDS_ISSUE=False`
   is the spec's expected common case — matching every prior run in this lineage — but is not
   asserted here; the live data decides). `$RUN_VARS` now carries `CURRENT_KEYWORDS`, `SINCE`,
   `UNTIL`, `NEXT_DATE`, `KEYWORDS_TO_REMOVE`, `L_NEEDS_ISSUE` for Tasks 4-7 to source.

No commit — all outputs are transient `/tmp` files, not repo content.

---

## Task 4 — Phase 2: Post the analysis comment on #294

**Files:** none tracked (GitHub side effect only)

0. Source the variables Tasks 2 and 3 persisted (`L_NEEDS_ISSUE`, among others):
   ```bash
   source /tmp/ceiling-revisit-vars.sh
   ```
1. If `L_NEEDS_ISSUE` is `True`, correct a stale claim in the generated report before posting it.
   `scripts/ceiling_revisit.py`'s `generate_report()` (lines 225-231) still writes "The
   L=always-above-ceiling rule may be overly conservative ... in `scheduler.sh`" into the
   `### L-Bucket Observation` section whenever this condition fires — stale on two counts (XL,
   not L, is the always-above-ceiling bucket since commit `4feef16`; the file is now
   `scheduler_lib.sh`, not `scheduler.sh`). The spec's own Architecture section explicitly
   cautions "any L-bucket analysis output from this run must not propose loosening an L rule that
   no longer exists" — fixing the script's string is out of this ticket's scope (shared,
   unmodified infrastructure), so correct the rendered report text in place instead, the same
   "apply a correction without editing the shared file" pattern Task 6 uses for the issue
   title/body:
   ```bash
   if [ "$L_NEEDS_ISSUE" = "True" ]; then
     sed -i \
       -e 's/The L=always-above-ceiling rule may be overly conservative\./The XL=always-above-ceiling rule may be overly conservative (L already dispatches autonomously since commit 4feef16)./' \
       -e 's/in `scheduler\.sh`\./in `scheduler_lib.sh`./' \
       /tmp/ceiling-revisit-report.md
   fi
   ```
   Expected: no output on success; if `L_NEEDS_ISSUE=False` (the common case) this step is a
   no-op since the stale phrase is never emitted in the first place.
2. Post the report generated in Task 3 (and possibly corrected in step 1) as a comment on issue
   #294 (not #30, #112, #119, or #32). Use `--body-file` rather than `--body "$(cat ...)"` — the
   report is multi-KB markdown and `--body-file` avoids shell arg-length limits and
   quoting/mangling of its content:
   ```bash
   gh issue comment 294 --repo "$FACTORY_REPO_SLUG" --body-file /tmp/ceiling-revisit-report.md
   ```
   Expected: `gh` prints the URL of the newly created comment
   (`https://github.com/omniscient/dark-factory/issues/294#issuecomment-...`).

No commit — no repository file changes.

---

## Task 5 — Phase 3: Open a PR to `.archon/.env` (conditional on `KEYWORDS_TO_REMOVE`)

**Files:** `.archon/.env` (new or modified, on a separate branch `chore/ceiling-revisit-${UNTIL}`
— not on `feat/issue-294-*`)

Only run this task if `KEYWORDS_TO_REMOVE` (from Task 3) is non-empty.

```bash
source /tmp/ceiling-revisit-vars.sh   # SINCE, UNTIL, CURRENT_KEYWORDS, KEYWORDS_TO_REMOVE

if [ -n "$KEYWORDS_TO_REMOVE" ]; then
  ENV_FILE=".archon/.env"
  ENV_BACKUP="/tmp/ceiling-revisit-env-backup-${UNTIL}"

  # CURRENT_KEYWORDS was already captured in Task 2 step 1 (via $RUN_VARS) — reuse it rather
  # than re-deriving, so Task 5's diff can never drift from what Task 3 actually analyzed.
  NEW_KWS="$CURRENT_KEYWORDS"
  for KW in $(echo "$KEYWORDS_TO_REMOVE" | tr '|' '\n'); do
    NEW_KWS=$(echo "$NEW_KWS" | sed "s/|${KW}//g;s/${KW}|//g;s/^${KW}$//g")
  done

  # .archon/.env is gitignored/untracked (.gitignore:41) — `git checkout` does NOT save or
  # restore untracked files across a branch switch the way it does tracked ones. Since this
  # task works in the same working directory (not a separate worktree), back the real file up
  # ourselves before touching it and restore it after, so a real deployment's other secret
  # lines survive this task intact.
  if [ -f "$ENV_FILE" ]; then
    cp "$ENV_FILE" "$ENV_BACKUP"
  else
    ENV_BACKUP=""
  fi

  PR_BRANCH="chore/ceiling-revisit-${UNTIL}"
  # Cut from origin/main, NOT from feat/issue-294-* — Task 1 already committed the spec+plan
  # docs onto feat/issue-294-*, and those commits aren't on main yet. Branching from feat/
  # here would drag both docs into this PR's diff, turning a single-file env change into a
  # 3-file one. Branching from origin/main keeps the PR scoped to .archon/.env alone.
  git fetch origin main
  git checkout -b "$PR_BRANCH" origin/main

  # Secrets guard (operator spec review, 2026-07-31): .archon/.env is a general gitignored
  # local-secrets file, not a file dedicated to this one variable. Do NOT git add -f whatever
  # happens to be on disk — a real deployment may have accumulated unrelated secret lines
  # (API tokens, credentials) since #30's run, and staging the whole file would leak them into
  # a public PR. Overwrite the working copy on THIS branch with a minimal file containing only
  # the one line this ticket is authorized to change, so what actually gets staged and
  # committed is provably limited to ABOVE_CEILING_KEYWORDS. (The backup above is what makes
  # this safe to do in-place rather than requiring a separate worktree.)
  printf 'ABOVE_CEILING_KEYWORDS=%s\n' "$NEW_KWS" > "$ENV_FILE"

  # -f required: .archon/.env is gitignored (see "Correction found during planning" above) —
  # this is the one deliberate, intentional commit of that file this command produces, and it
  # is provably single-line per the guard above.
  git add -f "$ENV_FILE"
  git commit -m "chore(env): update ABOVE_CEILING_KEYWORDS per weekly revisit (#294)

Removing: ${KEYWORDS_TO_REMOVE}
New value: ${NEW_KWS}

Analysis window: ${SINCE} → ${UNTIL}
Decision: n>=5 and keyword success rate >= M_baseline (no discriminative value).

Note: this commit contains ONLY the ABOVE_CEILING_KEYWORDS line, not the full local .env —
.archon/.env is a general gitignored secrets file and this PR must not leak unrelated entries."

  git push origin "$PR_BRANCH"
  gh pr create \
    --repo "$FACTORY_REPO_SLUG" \
    --title "chore(env): update ABOVE_CEILING_KEYWORDS per weekly ceiling revisit" \
    --body "Recommended by weekly dispatch ceiling analysis on issue #294.

Removes: \`${KEYWORDS_TO_REMOVE}\`

See the analysis comment on #294 for full data and decision rationale.

Related to #294 (not auto-closing — #294's own deliverable is the spec/plan landing via the
implement PR plus the analysis comment; this env change is reviewed and merged independently).

Note: this diff contains only the \`ABOVE_CEILING_KEYWORDS\` line. \`.archon/.env\` is a
gitignored local-secrets file for the self-target instance — this PR intentionally does not
carry any other content that may exist in a real deployment's copy." \
    --label "priority: should-have" \
    --base main

  git checkout -   # return to feat/issue-294-* for the remaining tasks

  # Restore the real working copy exactly as it was before this task touched it.
  if [ -n "$ENV_BACKUP" ]; then
    cp "$ENV_BACKUP" "$ENV_FILE"
    rm -f "$ENV_BACKUP"
  else
    rm -f "$ENV_FILE"
  fi
fi
```
Expected (only if the branch runs): `gh pr create` prints the new PR's URL scoped to a single
`.archon/.env` diff against `main`, and that diff contains exactly one line
(`ABOVE_CEILING_KEYWORDS=...`); `git status` on `feat/issue-294-*` after `git checkout -`
shows no pending changes from this block (they live on `chore/ceiling-revisit-${UNTIL}`
instead); the real `.archon/.env` (if one existed before this task ran) is byte-identical to
its pre-task state afterward.

No commit on `feat/issue-294-*` — the commit above belongs to the separate `chore/` branch.

---

## Task 6 — Phase 4: File the XL-bucket code-change issue (conditional on `L_NEEDS_ISSUE`)

**Files:** none tracked (GitHub side effect only)

Only run this task if `L_NEEDS_ISSUE` (from Task 3) is `True`. Note the variable name
(`l_bucket_needs_issue`) is `scripts/ceiling_revisit.py`'s existing, unmodified field name for
the merged `L+XL` reporting bucket — not renamed here, since that's a code change out of this
ticket's scope. Only the human-facing issue title/body below are corrected to say XL, per the
"XL, not L" correction in Architecture above.

```bash
source /tmp/ceiling-revisit-vars.sh   # SINCE, UNTIL, L_NEEDS_ISSUE

if [ "$L_NEEDS_ISSUE" = "True" ]; then
  gh issue create \
    --repo "$FACTORY_REPO_SLUG" \
    --title "Revisit XL=always-above-ceiling rule in is_above_ceiling() — scheduler_lib.sh" \
    --body "## Purpose

The weekly dispatch ceiling analysis (issue #294, window ${SINCE}→${UNTIL})
found the combined L+XL-bucket success rate exceeds 70% at n≥5. The XL=always-above-ceiling rule
in \`scripts/scheduler_lib.sh\` (\`is_above_ceiling()\`) may be overly conservative.

Note: L tickets already dispatch autonomously as of commit 4feef16 (2026-06-21) — only XL
tickets (and M tickets with a keyword title match) currently park in Blocked. This issue is about
XL specifically; the analysis reports L and XL as one merged bucket because sample sizes are too
small to split them (see \`scripts/ceiling_revisit.py\` \`build_bucket_table\`).

## What to review

- Inspect \`is_above_ceiling()\` in \`scripts/scheduler_lib.sh\`.
- Assess whether the XL-bucket ceiling should be relaxed (e.g. XL+keyword pattern only, mirroring
  the existing M-bucket rule).
- This is a **code change** (not an env-var change) — requires PR to \`scripts/scheduler_lib.sh\`.
- Prior instances of this same finding: #29, #31 (both closed, both pre-date the L-bucket relaxation
  in commit 4feef16 — re-verify their conclusions still apply before reusing them).

## References

- Triggering analysis: issue #294
- Policy: docs/superpowers/specs/2026-07-28-dispatch-ceiling-weekly-revisit-design.md

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
source /tmp/ceiling-revisit-vars.sh   # SINCE, UNTIL, NEXT_DATE

# This task is unconditional and its output (a filed GitHub issue) is durable and seeds the
# next run in this lineage — assert the sourced dates are actually present before filing rather
# than letting `gh issue create` post an issue with a literal empty string for UNTIL/NEXT_DATE.
test -n "$UNTIL" && test -n "$NEXT_DATE" || { echo "FATAL: UNTIL/NEXT_DATE not set — check /tmp/ceiling-revisit-vars.sh (Task 3 step 1 must have run)"; exit 1; }

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

- Policy: docs/superpowers/specs/2026-07-28-dispatch-ceiling-weekly-revisit-design.md
- Archon command: \`commands/ceiling-revisit.md\`
- Prior revisit: #294 (comment with results)

## Parameters for the agent

- \`ISSUE_NUM\` = <this issue's number>
- \`SINCE\` = 2026-06-12 (policy introduction date — always fixed)
- \`UNTIL\` = ${NEXT_DATE}
- \`NEXT_DATE\` = <UNTIL + 7 days>

## Target date

**${NEXT_DATE}** (weekly from ${UNTIL}).

---
*Filed automatically by ${FACTORY_PRODUCT_NAME:-Dark Factory} weekly ceiling revisit agent*" \
  --label "enhancement" \
  --label "priority: should-have" \
  --label "size: S" \
  --label "ready-for-agent"
```
Expected: `gh issue create` prints the new issue's URL. Uses `$UNTIL`/`$NEXT_DATE` as computed
in Task 3 (today's execution date + 7 days), not the stale `2026-07-28`/`2026-08-04` literals
this spec was written against — same "compute at run time" rule applied consistently across
every phase in this plan.

No commit — no repository file changes.

---

## Task 8 — Final verification

**Files:** none (verification only)

1. Run the full test suite exactly as `.github/workflows/ci.yml`'s `tests` job does — expect zero
   regressions, since no script/config under test was modified. Note: `PYTHONPATH=scripts` is
   required (CI sets it via `env:`); the root `smoke_gate.sh` is not itself a test (it's sourced
   by `entrypoint.sh` and runs `npx tsc` against a frontend directory this repo doesn't have if
   invoked directly) — CI actually runs `tests/test_smoke_gate.sh`, not the root script:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/ -v
   bash tests/test_identity.sh
   bash tests/test_hooks.sh
   bash tests/test_smoke_gate.sh
   bash tests/test_run_compose.sh
   bash tests/test_model_proxy_compose.sh
   bash tests/test_model_proxy_smoke.sh
   bash tests/test_entrypoint_current_run.sh
   bash tests/test_cost_report_endpoint.sh
   bash tests/test_cost_report_harness_economics.sh
   bash tests/test_run_record_hermetic.sh
   bash tests/test_entrypoint_cost_report_regression.sh
   bash tests/test_budget_gate.sh
   ```
   Expected: all commands exit 0, including `tests/test_ceiling_revisit.py` and
   `tests/test_fetch_scorecard.py` (covered by the `pytest tests/ -v` run) unchanged from their
   `main` baseline.
2. Run both workflow DAG checks CI's `dag-check` job runs (not just one):
   ```bash
   python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
   python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
   ```
3. Confirm the `feat/issue-294-*` branch only carries the two docs from Task 1. Use the two-dot
   diff form, not three-dot — per `.archon/memory/codebase-patterns.md` (issue #250), three-dot
   (`origin/main...HEAD`) includes commits main merged independently after the branch forked,
   producing false-positive out-of-scope hits. Diff against the `origin/main` commit Task 1
   captured, not a live re-read of `origin/main` — Task 5 (if it ran) fetched `origin/main` again
   and may have advanced the remote-tracking ref past this branch's actual fork point:
   ```bash
   BASE=$(cat /tmp/ceiling-revisit-base-main)
   git diff --name-only "$BASE" HEAD
   ```
   Expected: exactly
   `docs/superpowers/specs/2026-07-28-dispatch-ceiling-weekly-revisit-design.md` and
   `docs/superpowers/plans/2026-07-31-dispatch-ceiling-weekly-revisit-plan.md` — nothing else.
   (The conditional `.archon/.env` change, if Task 5 ran, lives on the separate
   `chore/ceiling-revisit-${UNTIL}` branch/PR, not here.)
4. No further commit needed if step 3 is clean; if any stray file appears, remove it and commit
   the removal before moving on.
