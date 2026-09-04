# Dispatch Ceiling (C9) Weekly Revisit — Execution Plan for #359

**Issue:** #359

**Spec:** `docs/superpowers/specs/2026-09-04-dispatch-ceiling-weekly-revisit-design.md`

## Goal

Execute the fifth run of the recurring dispatch-ceiling keyword revisit for #359: determine
whether either #342 (the prior-revisit issue) or #359 itself already carries an analysis comment
for this run's exact `SINCE`→`UNTIL` window and, if so, post a short restatement instead of
re-running the analysis; otherwise fetch cumulative Factory Scorecard data and run the full
standing procedure exactly as `commands/ceiling-revisit.md` already implements it (its Phase 4
XL-bucket duplicate/policy guard and its report text are both correct as of #361 — no overlay
needed for either). Post one conditional, duplicate-guarded, non-fatal tracking comment on #360
noting the same-window guard's fifth consecutive cycle of needing re-derivation, and
unconditionally file the next weekly revisit issue.

## Architecture

**Operational analysis run, not a service change.** No production code, script, or config file is
created or modified by this ticket. `scripts/fetch_scorecard.py` and `scripts/ceiling_revisit.py`
are already implemented, unit-tested (`tests/test_fetch_scorecard.py`,
`tests/test_ceiling_revisit.py`), and unmodified since commit `27c890b`. `commands/ceiling-revisit.md`
is likewise unmodified since #361 merged (PR #392) and already contains the permanent XL-bucket
duplicate/policy guard and the corrected L→XL/`scheduler.sh`→`scripts/scheduler_lib.sh` report
text. The only durable, git-tracked artifacts this ticket produces are the spec (already committed)
and this plan; every other effect (issue comment, possible `.archon/.env` PR, possible new issue,
possible #360 tracking comment) is a GitHub-side effect produced by replicating
`commands/ceiling-revisit.md`'s phases (with the two execution-time overlays below) against this
run's parameters.

**Resolved script paths: use the unprefixed paths, not the `# TARGET-PATH`-marked lines as
literally written in `commands/ceiling-revisit.md`.**

```
scripts/fetch_scorecard.py
scripts/ceiling_revisit.py
```

Not `dark-factory/scripts/...` — that prefix is for the MarketHawk instance. The
`dark-factory/`-prefixed path is not *absent* in a self-target clone (`entrypoint.sh` copies it in
at bootstrap and it is git-excluded, not untracked-and-missing), but it is a frozen snapshot that
never reflects branch changes and gets wiped by the deconflict flow — the unprefixed path is
correct for drift-safety, not existence (`.archon/memory/codebase-patterns.md`, corrected
2026-08-21 per #332, re-confirmed unchanged by #361).

**Already resolved since #342 — no overlay needed this cycle.** #361 (merged) permanently baked
the XL-bucket duplicate/policy guard and the L-bucket/`scheduler.sh` report-text fix into
`commands/ceiling-revisit.md` and `scripts/ceiling_revisit.py`. Task 7 below replicates Phase 4
of `commands/ceiling-revisit.md` **verbatim** (substituting this run's parameters) rather than
hand-deriving a duplicate/policy check, per the spec's instruction to rely on the command's
built-in as-is.

**New this cycle: broadened same-window duplicate guard (self-check on #359).** Unlike every
prior cycle in this lineage, #359's cross-issue collision risk against #342 is now low — the
operator's deliberate one-week hold (removing `ready-for-agent` on 2026-08-28, re-adding it
2026-09-04) makes it unlikely #342's window (`UNTIL=2026-08-28`) still matches this cycle's
`UNTIL` (2026-09-04 or later). The live risk this cycle is same-issue: #359 itself could be
re-dispatched after a partial failure (crash, orphaned run) and re-run this same analysis a second
time. Task 3 below checks **both** #342 and #359 for a matching-window analysis comment before
treating a full Scorecard fetch as needed — same query shape as every prior cycle, with the issue
number as a loop variable, at the cost of one extra `gh issue view`.

**New this cycle: conditional tracking comment on #360.** This is the same-window guard's fifth
consecutive cycle needing re-derivation as a plan/implement-time overlay (fourth was #342) — the
same signal that led the ad-hoc XL-bucket overlay (#294/#332) to get permanently baked into
`commands/ceiling-revisit.md` by #361. Task 8 below posts one informational, duplicate-guarded,
non-fatal comment on #360 recording that recurrence, conditioned on #360 still being `OPEN` at
implement time. This is a tracker signal, not a code/config change, so it does not need its own
reviewed ticket (unlike a source edit to `commands/ceiling-revisit.md` would) — per the spec's
scope-line reasoning (Alternatives Considered #4).

**Do not re-file #360 or #361.** #360 ("Add a dispatch-time cadence gate...") is still OPEN,
unactioned — Task 8 comments on it, does not re-file it. #361 ("Ceiling-revisit hygiene...") is
CLOSED, shipped — nothing to do.

**Computed values are persisted across tasks, not just kept in shell variables.** Tasks 2-9 each
run as separate shell invocations, so a plain `SINCE=...`/`UNTIL=...` set in one task's shell does
not exist in the next task's shell. Task 2 and Task 3 append every value they compute to
`RUN_VARS=/tmp/ceiling-revisit-vars.sh`, and every later task sources it as its first step. Task 9
— the one unconditional, durable deliverable in the main chain — additionally asserts
`UNTIL`/`NEXT_DATE` are non-empty before filing, so a broken source chain fails loudly instead of
seeding the next cycle with an empty date.

Because no behavior changes, TDD (red→green→commit) does not apply — there is no new code path to
pin with a failing test. Each task states the exact command and the *structural* shape of expected
output; exact success-rate numbers cannot be predicted at plan-writing time (live GitHub data), but
the decision rules that compute them are already covered by `tests/test_ceiling_revisit.py`.

**Memory patterns applied** (`.archon/memory/codebase-patterns.md`, `.archon/memory/dark-factory-ops.md`):
- Issue #42: a refine-phase spec/plan approved on this `refine/issue-359-*` branch does not
  automatically transfer to the `feat/issue-359-*` branch the implement phase creates. Task 1 makes
  the implement agent copy both docs over explicitly before doing anything else.
- Issue #250: use the two-dot diff form (`git diff <base-sha> HEAD`) for the final out-of-scope
  check, not three-dot, and freeze the base SHA in Task 1 before any later task re-fetches
  `origin/main` — Task 6 (if it runs) does its own `git fetch origin main`, which can advance the
  remote-tracking ref past this branch's true fork point.
- Issue #332/#342 (`codebase-patterns.md`, TARGET-PATH prefix): strip the `dark-factory/` prefix
  from every `# TARGET-PATH`-marked line when executing against this self-target repo — Tasks 4,
  5, 6, 7 all use the unprefixed `scripts/...` form directly.
- Issue #342 (`dark-factory-ops.md`): a crashed prior run's GitHub-side effects (posted comments,
  filed issues, opened PRs) leave no local `git log` trace. Task 3's same-window guard already
  covers the analysis comment; Task 6 (PR) and Task 9 (next-issue filing) below each add their own
  `gh pr list`/`gh issue list` duplicate check before acting, rather than assuming a fresh run.

**Archival note.** Prior cycles' spec/plan pairs ended up under `docs/archive/` (#30's, #294's,
#332's, #342's). Archiving is not this ticket's job — a later cycle or separate housekeeping pass
does it, mirroring every prior cycle's plan, none of which archived itself.

## Tech Stack

Bash, Python 3 (`scripts/fetch_scorecard.py`, `scripts/ceiling_revisit.py` — both on `main`,
unmodified), `gh` CLI, `jq`. No new dependencies.

## File Structure

| Path | Purpose |
|---|---|
| `docs/superpowers/specs/2026-09-04-dispatch-ceiling-weekly-revisit-design.md` | Already committed (this ticket's spec) |
| `docs/superpowers/plans/2026-09-04-dispatch-ceiling-weekly-revisit-plan.md` | This plan (committed by the plan phase) |
| *(GitHub side effects only, below)* | Issue #359 comment (full analysis or short restatement); conditional PR touching `.archon/.env` on branch `chore/ceiling-revisit-<UNTIL>`; XL-bucket code-change issue — expected **not** filed (L+XL data has cleared 70% every cycle to date, but Phase 4's own built-in guard decides live); conditional tracking comment on #360; unconditional new weekly-revisit issue, boarded to the project's Backlog column |

No other repository file is created, modified, or deleted by this ticket.

---

## Task 1 — Bring the spec and plan onto the implementation branch

**Files:** `docs/superpowers/specs/2026-09-04-dispatch-ceiling-weekly-revisit-design.md`,
`docs/superpowers/plans/2026-09-04-dispatch-ceiling-weekly-revisit-plan.md` (copied, not
re-authored)

1. On the `feat/issue-359-*` branch the implement phase creates, verify both docs exist (they were
   committed on `refine/issue-359-revisit-dispatch-ceiling-----re-measure-`, not automatically
   present on a fresh branch off `main`). The implement phase runs from a **fresh clone**, where the
   refine branch exists only as `refs/remotes/origin/refine/...` — a bare `git show
   refine/issue-359-...:<path>` does not resolve there, and a `>` redirect on a failed `git show`
   would silently create an **empty** file that then gets committed. Fetch explicitly, reference the
   `origin/` remote-tracking ref, and assert non-emptiness. Also capture the `origin/main` commit
   this branch was actually cut from, for Task 10's final out-of-scope check:
   ```bash
   git rev-parse origin/main > /tmp/ceiling-revisit-base-main
   REFINE_BRANCH="refine/issue-359-revisit-dispatch-ceiling-----re-measure-"
   git fetch origin "$REFINE_BRANCH"
   git show "origin/${REFINE_BRANCH}:docs/superpowers/specs/2026-09-04-dispatch-ceiling-weekly-revisit-design.md" \
     > docs/superpowers/specs/2026-09-04-dispatch-ceiling-weekly-revisit-design.md
   git show "origin/${REFINE_BRANCH}:docs/superpowers/plans/2026-09-04-dispatch-ceiling-weekly-revisit-plan.md" \
     > docs/superpowers/plans/2026-09-04-dispatch-ceiling-weekly-revisit-plan.md
   test -s docs/superpowers/specs/2026-09-04-dispatch-ceiling-weekly-revisit-design.md
   test -s docs/superpowers/plans/2026-09-04-dispatch-ceiling-weekly-revisit-plan.md
   ```
   Expected: both files exist and are non-empty in the working tree (`git status --short` shows
   them as new/modified; both `test -s` checks exit 0). If either `test -s` fails, stop — this
   ticket's only durable artifact would otherwise be silently lost.
2. Commit:
   ```bash
   git add docs/superpowers/specs/2026-09-04-dispatch-ceiling-weekly-revisit-design.md \
           docs/superpowers/plans/2026-09-04-dispatch-ceiling-weekly-revisit-plan.md
   git commit -m "docs: bring over approved spec/plan for issue #359"
   ```

---

## Task 2 — Pre-flight verification

**Files:** none (read-only verification)

**All variables computed in this task and Task 3 must survive into Tasks 3-9.** Start fresh:
```bash
RUN_VARS=/tmp/ceiling-revisit-vars.sh
rm -f "$RUN_VARS"
```

1. Determine the currently effective `ABOVE_CEILING_KEYWORDS`:
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
   Expected: `no override active — using config/config.yaml default:
   migration|migrate|performance|perf|architectur|refactor` (matches spec Assumption — verified
   this run: no `.archon/.env` exists in this checkout).
2. Confirm `gh` auth and repo targeting:
   ```bash
   gh auth status
   test -n "${FACTORY_REPO_SLUG:-}" || { echo "FATAL: FACTORY_REPO_SLUG not set"; exit 1; }
   echo "$FACTORY_REPO_SLUG"
   echo "${FACTORY_PRODUCT_NAME:-<unset>}"
   ```
   Expected: authenticated to `github.com`; `FACTORY_REPO_SLUG` prints `omniscient/dark-factory`;
   `FACTORY_PRODUCT_NAME` prints a product name, not `<unset>`. The explicit `test -n` moves a
   missing `FACTORY_REPO_SLUG` failure here rather than letting it surface later as a confusing
   `KeyError` inside Task 4's Python block or a wrong-repo `fetch_scorecard.py` run in Task 4.
3. Confirm the labels used below already exist:
   ```bash
   for LBL in "priority: should-have" "enhancement" "size: S" "ready-for-agent"; do
     gh label list --repo "$FACTORY_REPO_SLUG" --json name --jq '.[].name' | grep -qxF "$LBL" \
       && echo "OK: $LBL" || { echo "MISSING: $LBL"; exit 1; }
   done
   ```
   Expected: `OK: <label>` for all four (verified this run — all four present).
4. Confirm #360's current state (input to Task 8's condition):
   ```bash
   ISSUE_360_STATE=$(gh issue view 360 --repo "$FACTORY_REPO_SLUG" --json state --jq .state)
   test -n "$ISSUE_360_STATE" || ISSUE_360_STATE=UNKNOWN
   echo "ISSUE_360_STATE=$ISSUE_360_STATE"
   echo "ISSUE_360_STATE=\"$ISSUE_360_STATE\"" >> "$RUN_VARS"
   ```
   Expected: `ISSUE_360_STATE=OPEN` (verified this run: still open, no comments). If this instead
   prints `CLOSED`, Task 8 skips the tracking comment per its own condition.

No commit — this task only reads state.

---

## Task 3 — Compute dates and check the same-window duplicate guard (broadened: #342 and #359)

**Files:** none tracked

0. Source Task 2's variables:
   ```bash
   RUN_VARS=/tmp/ceiling-revisit-vars.sh
   source "$RUN_VARS"
   ```
1. Compute `UNTIL`/`NEXT_DATE` from the actual execution date — do **not** reuse `2026-09-04`/
   `2026-09-11` verbatim (those are the spec's/this plan's own write-time dates, not necessarily
   implement's). Persist immediately:
   ```bash
   SINCE=2026-06-12
   UNTIL=$(date -u +%Y-%m-%d)
   NEXT_DATE=$(date -u -d "${UNTIL} +7 days" +%Y-%m-%d)
   echo "SINCE=$SINCE UNTIL=$UNTIL NEXT_DATE=$NEXT_DATE"
   { echo "SINCE=\"$SINCE\""; echo "UNTIL=\"$UNTIL\""; echo "NEXT_DATE=\"$NEXT_DATE\""; } >> "$RUN_VARS"
   ```
   Expected: `UNTIL` prints today's UTC date, `NEXT_DATE` is exactly 7 days later.
2. **Same-window duplicate guard, broadened to self-check #359** (spec Requirements, "New /
   still-needed this cycle"): check whether **either** #342 or #359 already carries an analysis
   comment whose window end equals this run's `UNTIL`. The header regex requires the full two-date
   shape (`^## Dispatch Ceiling Weekly Revisit — <SINCE> → <UNTIL>$`) — this deliberately excludes
   the dateless `## Dispatch Ceiling Weekly Revisit — Same-Window Restatement` header a prior
   skip-path run may have posted (on #359 itself, if this is a retry), which matches the bare
   prefix but carries no dates and would otherwise null out `tail -1`'s match. Check #342 first,
   then #359 — either match sets `SAME_WINDOW=true` and records which issue matched, for Task 5's
   restatement to point at:
   ```bash
   MATCHED_ISSUE=""
   SAME_WINDOW=false
   for CANDIDATE in 342 359; do
     COMMENTS_JSON=$(gh issue view "$CANDIDATE" --repo "$FACTORY_REPO_SLUG" --json comments) \
       || { echo "FATAL: gh issue view #$CANDIDATE failed — cannot safely determine SAME_WINDOW (fail closed: one cheap retry beats risking a ~\$11-class duplicate analysis, per spec cost-asymmetry rationale)"; exit 1; }
     HEADER=$(echo "$COMMENTS_JSON" | jq -r '.comments[].body' \
       | grep -E '^## Dispatch Ceiling Weekly Revisit — [0-9]{4}-[0-9]{2}-[0-9]{2} → [0-9]{4}-[0-9]{2}-[0-9]{2}$' \
       | tail -1 || true)
     CANDIDATE_UNTIL=$(echo "$HEADER" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}$' || true)
     echo "issue #$CANDIDATE: HEADER=$HEADER CANDIDATE_UNTIL=$CANDIDATE_UNTIL"
     if [ -n "$CANDIDATE_UNTIL" ] && [ "$CANDIDATE_UNTIL" = "$UNTIL" ]; then
       SAME_WINDOW=true
       MATCHED_ISSUE="$CANDIDATE"
       break
     fi
   done
   echo "SAME_WINDOW=$SAME_WINDOW MATCHED_ISSUE=$MATCHED_ISSUE"
   { echo "SAME_WINDOW=\"$SAME_WINDOW\""; echo "MATCHED_ISSUE=\"$MATCHED_ISSUE\""; } >> "$RUN_VARS"
   ```
   A `gh issue view` failure (auth/rate-limit/network) now stops the run with a `FATAL` rather than
   silently falling through to `SAME_WINDOW=false` and running a full, possibly-redundant fetch —
   fail-closed, mirroring the posture #361 already baked into the Phase 4 XL-bucket guard.
   Expected (per the spec's Assumptions — inverted from #342's own, now-corrected expectation):
   `SAME_WINDOW=false`. #342's window ended 2026-08-28, a full week before this cycle's `UNTIL`
   (2026-09-04 or later, per the operator's deliberate hold), and #359 has no prior analysis
   comment of its own as of plan-writing time. If implement runs same-day as this plan was written
   (2026-09-04) the #342 branch is still `false` (2026-08-28 ≠ 2026-09-04); `SAME_WINDOW=true` only
   fires on a genuine retry (#359 already posted this exact window) or an unexpected coincidental
   match, both handled correctly by Task 5's alternate branch.

No commit — all outputs are transient `/tmp` files or shell variables.

---

## Task 4 — Phase 1: Fetch and analyze (only if `SAME_WINDOW=false`)

**Files:** none tracked (writes transient `/tmp/ceiling-revisit-scorecard.json`,
`/tmp/ceiling-revisit-report.md`, `/tmp/ceiling-revisit-meta.txt`)

Skip this task entirely if `SAME_WINDOW=true` (Task 5 posts a restatement instead).

0. Source variables:
   ```bash
   source /tmp/ceiling-revisit-vars.sh
   ```
1. Guard `CURRENT_KEYWORDS` before running — an empty `--keywords ""` is accepted silently by
   `ceiling_revisit.py` and produces a report with zero keyword rows, degraded without error:
   ```bash
   if [ "$SAME_WINDOW" = "false" ]; then
     test -n "$CURRENT_KEYWORDS" || { echo "FATAL: CURRENT_KEYWORDS not set — check Task 2 step 1"; exit 1; }

     SCORECARD=/tmp/ceiling-revisit-scorecard.json
     REPORT_FILE=/tmp/ceiling-revisit-report.md

     python3 scripts/fetch_scorecard.py \
       --repo "$FACTORY_REPO_SLUG" \
       --since "$SINCE" \
       --until "$UNTIL" \
       --output "$SCORECARD"

     python3 scripts/ceiling_revisit.py \
       --since "$SINCE" \
       --until "$UNTIL" \
       --scorecard "$SCORECARD" \
       --keywords "$CURRENT_KEYWORDS" \
       --output "$REPORT_FILE" \
       2>/tmp/ceiling-revisit-meta.txt
   fi
   ```
   `--repo "$FACTORY_REPO_SLUG"` avoids `fetch_scorecard.py`'s own fallback (which defaults to
   `omniscient/markethawk` if the env var is ever unset) silently analyzing the wrong repo.
   `--keywords "$CURRENT_KEYWORDS"` locks the analysis to the value Task 2 captured rather than
   `ceiling_revisit.py`'s own `DEFAULT_KEYWORDS` fallback, so this task's analysis and Task 6's diff
   can never drift apart once an `.archon/.env` override exists.

   Expected: `fetch_scorecard.py` ends with `Wrote /tmp/ceiling-revisit-scorecard.json`;
   `ceiling_revisit.py` writes `/tmp/ceiling-revisit-report.md` (`### Per-Bucket Triad` table with
   rows `S`, `M`, `L+XL`; `### Per-Keyword Analysis` table); `/tmp/ceiling-revisit-meta.txt` ends
   with a line starting `<!-- CEILING_REVISIT_JSON {"keywords_to_remove": [...],
   "new_keyword_candidates": [...], "l_bucket_needs_issue": <bool>} -->`. This step can take
   several minutes (git-blame churn over the full ~12-week cumulative window) — expected, not a
   hang.
2. Extract the recommendation:
   ```bash
   if [ "$SAME_WINDOW" = "false" ]; then
     REC_JSON=$(grep 'CEILING_REVISIT_JSON' /tmp/ceiling-revisit-meta.txt \
       | sed 's/.*CEILING_REVISIT_JSON \(.*\) -->/\1/')
     KEYWORDS_TO_REMOVE=$(echo "$REC_JSON" | python3 -c \
       "import sys,json; d=json.load(sys.stdin); print('|'.join(d['keywords_to_remove']))")
     L_NEEDS_ISSUE=$(echo "$REC_JSON" | python3 -c \
       "import sys,json; d=json.load(sys.stdin); print(d['l_bucket_needs_issue'])")
     echo "KEYWORDS_TO_REMOVE=$KEYWORDS_TO_REMOVE"
     echo "L_NEEDS_ISSUE=$L_NEEDS_ISSUE"
     { echo "KEYWORDS_TO_REMOVE=\"$KEYWORDS_TO_REMOVE\""; echo "L_NEEDS_ISSUE=\"$L_NEEDS_ISSUE\""; } >> /tmp/ceiling-revisit-vars.sh
   fi
   ```
   Expected: both variables print without error. Empty `KEYWORDS_TO_REMOVE` and
   `L_NEEDS_ISSUE=False` matches every prior run in this lineage including #342's, but the live
   data decides, not this plan.

No commit — all outputs are transient.

---

## Task 5 — Post the analysis comment on #359 (branches on `SAME_WINDOW`)

**Files:** none tracked (GitHub side effect only)

0. Source variables:
   ```bash
   source /tmp/ceiling-revisit-vars.sh
   ```
1. **If `SAME_WINDOW=true`** — post a short, self-contained restatement instead of the full
   report, per the spec's skip-path requirement, pointing at `$MATCHED_ISSUE` (either #342 or #359
   itself). Extract the relevant sections from the matched issue's own comment (re-fetched here —
   Task 3's lookup was a plain shell variable and does not survive the task-boundary shell
   invocation) rather than hardcoding numbers into this plan, so the restatement can never drift
   from what was actually measured:
   ```bash
   if [ "$SAME_WINDOW" = "true" ]; then
     export MATCHED_ISSUE UNTIL FACTORY_REPO_SLUG
     # Quoted heredoc delimiter ('PYEOF') is required: an unquoted <<PYEOF lets the shell
     # perform command substitution on the body BEFORE python sees it, and this template
     # contains backtick-quoted spans (`SINCE=2026-06-12`, etc.) that the shell would try to
     # execute as commands and silently replace with empty output, corrupting the posted
     # comment. Quoting the delimiter passes the heredoc body through untouched; python reads
     # its inputs from `os.environ` instead of shell interpolation.
     #
     # The `gh issue comment` call is chained with && onto the SAME command line as the heredoc
     # (not a separate statement below it) so a mid-script failure (either `gh` subprocess call
     # inside python raising via check=True, or the explicit SystemExit below) skips posting
     # entirely rather than posting a stale/partial file. rm -f first removes any leftover file
     # from an earlier retried run, so a python failure can't leave yesterday's content behind
     # for the (skipped) gh call to find.
     rm -f /tmp/ceiling-revisit-restatement.md
     python3 - <<'PYEOF' && gh issue comment 359 --repo "$FACTORY_REPO_SLUG" --body-file /tmp/ceiling-revisit-restatement.md
import subprocess, json, os

issue = int(os.environ["MATCHED_ISSUE"])
until = os.environ["UNTIL"]
repo = os.environ["FACTORY_REPO_SLUG"]
comments = json.loads(subprocess.run(
    ["gh", "issue", "view", str(issue), "--repo", repo, "--json", "comments"],
    capture_output=True, text=True, check=True).stdout)["comments"]

dated = [c["body"] for c in comments if c["body"].startswith("## Dispatch Ceiling Weekly Revisit —") and "Same-Window Restatement" not in c["body"]]
body = dated[-1] if dated else None
if body is None:
    raise SystemExit("FATAL: no dated analysis comment on #{} found — check SAME_WINDOW logic/Task 3".format(issue))

def section(name, text):
    start = text.find(f"### {name}")
    if start == -1:
        return ""
    end_candidates = [e for e in (text.find("\n### ", start + 1), text.find("\n---\n", start + 1)) if e != -1]
    end = min(end_candidates) if end_candidates else len(text)
    return text[start:end].strip()

triad = section("Per-Bucket Triad", body)
keyword_rec = section("Keyword Change Recommendation", body)
xl_section = ""
for name in ("XL-Bucket Issue — Skipped (Operator Policy, #331)",
             "XL-Bucket Issue — Skipped (Duplicate Guard)",
             "L-Bucket Observation"):
    xl_section = section(name, body)
    if xl_section:
        break

self_note = " (this is #359's own earlier comment — a retry/re-dispatch of this same ticket)" if issue == 359 else ""

restatement = f"""## Dispatch Ceiling Weekly Revisit — Same-Window Restatement

Issue #{issue}'s analysis comment{self_note} already covers this exact cumulative window
(`SINCE=2026-06-12` → `UNTIL={until}`) against the same unmodified, deterministic
scripts — re-running the fetch/analysis would produce an identical result at real cost for zero
new signal (per this ticket's spec, "New / still-needed this cycle" — same-window duplicate
guard, broadened to self-check #359). See
[#{issue}](https://github.com/{repo}/issues/{issue}) for the full report. Headline results,
restated here for a reader who doesn't want to cross-reference:

{triad}

{keyword_rec}

{xl_section}

---
*Posted by Dark Factory Weekly Ceiling Revisit — same-window restatement, see #{issue}*
"""
open("/tmp/ceiling-revisit-restatement.md", "w").write(restatement)
print(restatement)
PYEOF
   fi
   ```
   Expected: `gh` prints the URL of the new comment on #359, containing the restated per-bucket
   table, keyword recommendation, and XL-bucket note pulled verbatim from the matched issue's
   comment.
2. **If `SAME_WINDOW=false`** — post the report generated in Task 4 as the normal analysis
   comment:
   ```bash
   if [ "$SAME_WINDOW" = "false" ]; then
     gh issue comment 359 --repo "$FACTORY_REPO_SLUG" --body-file /tmp/ceiling-revisit-report.md
   fi
   ```
   Expected (the anticipated branch this cycle — see spec Assumptions): `gh` prints the URL of the
   new comment on #359, containing the freshly computed `### Per-Bucket Triad` /
   `### Per-Keyword Analysis` tables, correct L→XL/`scripts/scheduler_lib.sh` wording (per #361,
   already baked into `scripts/ceiling_revisit.py` — no runtime text correction needed), and (if
   `L_NEEDS_ISSUE=True`) the standing XL-bucket note.

No commit — no repository file changes.

---

## Task 6 — Phase 3: Open a PR to `.archon/.env` (only if `SAME_WINDOW=false` and `KEYWORDS_TO_REMOVE` non-empty)

**Files:** `.archon/.env` (new or modified, on a separate branch `chore/ceiling-revisit-${UNTIL}`
— not on `feat/issue-359-*`)

```bash
source /tmp/ceiling-revisit-vars.sh   # SINCE, UNTIL, CURRENT_KEYWORDS, KEYWORDS_TO_REMOVE, SAME_WINDOW

if [ "$SAME_WINDOW" = "false" ] && [ -n "${KEYWORDS_TO_REMOVE:-}" ]; then
  PR_BRANCH="chore/ceiling-revisit-${UNTIL}"

  # Duplicate check (memory: dark-factory-ops.md #342 — a crashed prior run's opened PR leaves no
  # local git trace). Skip re-opening a PR this exact run already created on a retry.
  EXISTING_PR=$(gh pr list --repo "$FACTORY_REPO_SLUG" --head "$PR_BRANCH" --state all \
    --json number --jq '.[0].number // empty')
  if [ -n "$EXISTING_PR" ]; then
    echo "SKIPPED (duplicate): PR #$EXISTING_PR already exists for branch $PR_BRANCH"
  else
    ENV_FILE=".archon/.env"
    ENV_BACKUP="/tmp/ceiling-revisit-env-backup-${UNTIL}"

    NEW_KWS="$CURRENT_KEYWORDS"
    for KW in $(echo "$KEYWORDS_TO_REMOVE" | tr '|' '\n'); do
      NEW_KWS=$(echo "$NEW_KWS" | sed "s/|${KW}//g;s/${KW}|//g;s/^${KW}$//g")
    done

    # .archon/.env is gitignored/untracked (.gitignore:41) — `git checkout` does not save/restore
    # untracked files across a branch switch. Back the real file up ourselves and restore it after.
    if [ -f "$ENV_FILE" ]; then
      cp "$ENV_FILE" "$ENV_BACKUP"
    else
      ENV_BACKUP=""
    fi

    git fetch origin main
    git checkout -b "$PR_BRANCH" origin/main

    # Secrets guard: write a fresh, minimal file containing only the one line this ticket is
    # authorized to change — a real deployment's .archon/.env may carry unrelated secret lines.
    printf 'ABOVE_CEILING_KEYWORDS=%s\n' "$NEW_KWS" > "$ENV_FILE"
    git add -f "$ENV_FILE"
    git commit -m "chore(env): update ABOVE_CEILING_KEYWORDS per weekly revisit (#359)

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
      --body "Recommended by weekly dispatch ceiling analysis on issue #359.

Removes: \`${KEYWORDS_TO_REMOVE}\`

See the analysis comment on #359 for full data and decision rationale.

Note: this diff contains only the \`ABOVE_CEILING_KEYWORDS\` line. \`.archon/.env\` is a
gitignored local-secrets file for the self-target instance — this PR intentionally does not
carry any other content that may exist in a real deployment's copy." \
      --label "priority: should-have" \
      --base main

    git checkout -

    if [ -n "$ENV_BACKUP" ]; then
      cp "$ENV_BACKUP" "$ENV_FILE"
      rm -f "$ENV_BACKUP"
    else
      rm -f "$ENV_FILE"
    fi
  fi
fi
```
Expected (only if the branch runs): `gh pr create` prints the new PR's URL scoped to a single
`.archon/.env` diff against `main`, containing exactly one line; `git status` on
`feat/issue-359-*` shows no pending changes from this block afterward. Given every prior cycle's
window has found no discriminative keyword, this task is expected **not** to run.

No commit on `feat/issue-359-*` — the commit above belongs to the separate `chore/` branch.

---

## Task 7 — Phase 4: XL-bucket duplicate/policy guard (only if `SAME_WINDOW=false` and `L_NEEDS_ISSUE=True`)

**Files:** none tracked (GitHub side effect only)

Replicates `commands/ceiling-revisit.md` Phase 4 **verbatim** (its built-in guard, permanently
fixed by #361 — no plan-level re-derivation), substituting this run's `$SINCE`/`$UNTIL`.

```bash
source /tmp/ceiling-revisit-vars.sh   # SINCE, UNTIL, SAME_WINDOW, L_NEEDS_ISSUE
REPO="$FACTORY_REPO_SLUG"
ISSUE_NUM=359

if [ "$SAME_WINDOW" = "false" ] && [ "${L_NEEDS_ISSUE:-}" = "True" ]; then
  MATCHES=$(gh issue list --repo "$REPO" --state all --limit 500 \
    --json number,title,state,stateReason \
    --jq '[.[] | select(.title | test("always-above-ceiling"; "i"))] | sort_by(-.number)')

  if [ -z "$MATCHES" ] || ! echo "$MATCHES" | jq -e 'type=="array"' >/dev/null 2>&1; then
    XL_ACTION="skip-lookup-failed"
  else
    OPEN_MATCH=$(echo "$MATCHES" | jq -r '[.[] | select(.state=="OPEN")] | first.number // empty')
    NEWEST_NUM=$(echo "$MATCHES" | jq -r 'first.number // empty')
    NEWEST_REASON=$(echo "$MATCHES" | jq -r 'first.stateReason // empty')

    if [ -n "$OPEN_MATCH" ]; then
      XL_ACTION="skip-duplicate"; XL_CITE="$OPEN_MATCH"
    elif [ -n "$NEWEST_NUM" ] && [ "$NEWEST_REASON" = "NOT_PLANNED" ]; then
      XL_ACTION="skip-policy"; XL_CITE="$NEWEST_NUM"
    else
      XL_ACTION="file"
    fi
  fi

  if [ "$XL_ACTION" = "file" ]; then
    gh issue create \
      --repo "$REPO" \
      --title "Revisit XL=always-above-ceiling rule in is_above_ceiling() — scheduler_lib.sh" \
      --body "## Purpose

The weekly dispatch ceiling analysis (issue #${ISSUE_NUM}, window ${SINCE}→${UNTIL})
found the L+XL bucket success rate exceeds 70% at n≥5. The XL=always-above-ceiling rule
in \`scripts/scheduler_lib.sh\` may be overly conservative.

## What to review

- Inspect \`is_above_ceiling()\` in \`scripts/scheduler_lib.sh\`.
- Assess whether the XL-bucket ceiling should be relaxed (e.g. XL+keyword pattern only).
- This is a **code change** (not an env-var change) — requires PR to \`scripts/scheduler_lib.sh\`.

## References

- Triggering analysis: issue #${ISSUE_NUM}
- Policy: the dispatch-ceiling revisit design (see the dispatch-ceiling design spec)

---
*Filed automatically by weekly ceiling revisit*" \
      --label "enhancement" \
      --label "priority: should-have"
  elif [ "$XL_ACTION" = "skip-lookup-failed" ]; then
    gh issue comment "$ISSUE_NUM" --repo "$REPO" --body "XL-bucket success rate cleared the \
>70%-at-n>=5 threshold again this cycle, but the duplicate/policy tracker lookup (\`gh issue \
list\`) failed, so no issue was filed this cycle to avoid risking a duplicate. Re-run the \
weekly revisit, or file the XL-bucket issue manually if this recurs."
  else
    REASON=$([ "$XL_ACTION" = "skip-policy" ] && echo "closed by operator policy decision" \
                                                || echo "already open, covering this observation")
    gh issue comment "$ISSUE_NUM" --repo "$REPO" --body "XL-bucket success rate cleared the \
>70%-at-n>=5 threshold again this cycle, but issue #${XL_CITE} is ${REASON} — see #${XL_CITE} \
instead of filing a duplicate."
  fi
  echo "XL_ISSUE_ACTION=$XL_ACTION"
  { echo "XL_ISSUE_ACTION=\"$XL_ACTION\""; } >> /tmp/ceiling-revisit-vars.sh
else
  echo 'XL_ISSUE_ACTION="skip-not-needed"' >> /tmp/ceiling-revisit-vars.sh
fi
```
Expected: given #342's just-measured L+XL success rate (100%, n=6) and every prior cycle's data,
`L_NEEDS_ISSUE=True` is plausible; the tracker currently has no existing `always-above-ceiling`
issue open or closed (#331, the only prior one, has since been closed — re-check its
`stateReason` live), so `XL_ACTION=file` or `skip-policy` depending on #331's current
`stateReason`. The guard decides live; this plan does not predict which branch fires.

No commit — no repository file changes.

---

## Task 8 — Post conditional tracking comment on #360 (informational, duplicate-guarded, non-fatal)

**Files:** none tracked (GitHub side effect only)

Per the spec's "New / still-needed this cycle" requirement: this is the same-window duplicate
guard's fifth consecutive cycle needing re-derivation (#342 was the fourth) — record that
recurrence on #360 (the open cadence-gate ticket) so a human reviewing it sees the accumulating
cost of not having a real gate. Three conditions keep this proportionate to the ticket's `size: S`
scope: only if #360 is still `OPEN`, duplicate-guarded against an existing matching comment, and
non-fatal.

```bash
source /tmp/ceiling-revisit-vars.sh   # ISSUE_360_STATE, UNTIL

if [ "$ISSUE_360_STATE" = "OPEN" ]; then
  # `gh --jq` takes exactly one expression argument and has no --arg passthrough — piping to a
  # separate `jq --arg` call (not `gh ... --jq --arg ...`) is required, or `gh` errors on the
  # extra positional args and the `2>/dev/null || echo 0` fallback silently defeats the guard.
  DUP=$(gh issue view 360 --repo "$FACTORY_REPO_SLUG" --json comments 2>/dev/null \
    | jq --arg until "$UNTIL" '[.comments[].body | select(contains("same-window duplicate guard") and contains($until))] | length' 2>/dev/null || echo 0)

  if [ "${DUP:-0}" -gt 0 ]; then
    echo "SKIPPED (duplicate): #360 already carries a tracking comment for UNTIL=$UNTIL"
  else
    if ! gh issue comment 360 --repo "$FACTORY_REPO_SLUG" --body "**Tracking note from the weekly ceiling-revisit lineage (#359, window ending ${UNTIL}):**

The same-window duplicate guard (checking whether a prior-revisit issue already analyzed this
exact window) has now needed re-derivation as a plan/implement-time overlay for a **fifth**
consecutive cycle in this lineage (#30 → #294 → #332 → #342 → #359), and #359's cycle additionally had to *broaden* the
guard to self-check #359 itself, not just the prior-revisit issue. This mirrors how the ad-hoc
XL-bucket duplicate/policy guard recurred across #294/#332/#342 before being permanently baked
into \`commands/ceiling-revisit.md\` by #361.

Cheaper alternative worth weighing alongside a full scheduler-level cadence gate: bake the
same-window guard directly into \`commands/ceiling-revisit.md\` (mirroring #361's precedent for
the XL-bucket guard), so future cycles' plans stop re-deriving it. Not filing a new ticket for
this — #360 is already the canonical open ticket for \"no real cadence gate exists.\"

---
*Posted by Dark Factory Weekly Ceiling Revisit (#359)*" 2>/tmp/ceiling-revisit-360-comment-err.txt; then
      echo "WARN: failed to post tracking comment on #360 — see /tmp/ceiling-revisit-360-comment-err.txt (non-fatal, continuing)"
    else
      echo "posted tracking comment on #360"
    fi
  fi
elif [ "$ISSUE_360_STATE" = "UNKNOWN" ]; then
  echo "SKIPPED: #360 state could not be determined (Task 2 lookup failed) — skipping both the tracking comment and the fallback note rather than posting a claim that might be wrong"
else
  # Fallback per spec condition 1: #360 not OPEN → fold the observation into a note on #359
  # itself instead. Task 5 (which posts #359's machine-generated analysis comment) already ran
  # by this point in the chain, so this is posted as its own small follow-up comment on #359
  # rather than an edit to the earlier one — non-fatal, matching Task 8's OPEN-branch posture.
  if ! gh issue comment 359 --repo "$FACTORY_REPO_SLUG" --body "**Note (weekly ceiling-revisit, #359):** #360 (the open dispatch-time-cadence-gate ticket) is no longer OPEN as of this run (state=${ISSUE_360_STATE}), so the usual tracking comment about the same-window duplicate guard's recurrence is folded in here instead: this is the guard's fifth consecutive cycle needing re-derivation in this lineage (#30 → #294 → #332 → #342 → #359).

---
*Posted by Dark Factory Weekly Ceiling Revisit (#359)*" 2>/tmp/ceiling-revisit-360-fallback-err.txt; then
    echo "WARN: failed to post #360-closed fallback note on #359 — see /tmp/ceiling-revisit-360-fallback-err.txt (non-fatal, continuing)"
  else
    echo "SKIPPED: #360 is not OPEN (state=$ISSUE_360_STATE) — posted fallback note on #359 instead"
  fi
fi
```
Expected (per Task 2 step 4's verified `ISSUE_360_STATE=OPEN` and no existing comments): the
comment posts successfully, printing `posted tracking comment on #360`. A `gh issue comment`
failure here must not stop this task chain — the `if ! gh ... ; then ... fi` wrapper ensures a
`WARN:` line and continuation rather than a script abort, per the spec's non-fatal condition.

No commit — no repository file changes.

---

## Task 9 — Phase 5: File the next weekly revisit issue (unconditional)

**Files:** none tracked (GitHub side effect only)

```bash
source /tmp/ceiling-revisit-vars.sh   # SINCE, UNTIL, NEXT_DATE

test -n "${UNTIL:-}" && test -n "${NEXT_DATE:-}" || { echo "FATAL: UNTIL/NEXT_DATE not set — check Task 3 step 1"; exit 1; }

# Duplicate check (memory: dark-factory-ops.md #342 — a crashed prior run's filed issue leaves no
# local git trace). An OPEN issue with the exact next-revisit title exists on every cycle
# (whichever issue this lineage is currently working, e.g. #359 itself right now) — narrow the
# match to one that also cites "#359" as its prior revisit, which only the issue THIS run's Phase
# 5 would create can satisfy.
EXISTING_NEXT=$(gh issue list --repo "$FACTORY_REPO_SLUG" --state open --limit 200 \
  --json number,title,body \
  --jq '[.[] | select(.title == "Revisit dispatch ceiling — re-measure success-by-size/type") | select(.body | contains("Prior revisit: #359"))] | first.number // empty')

if [ -n "$EXISTING_NEXT" ]; then
  echo "SKIPPED (duplicate): next revisit issue already filed as #$EXISTING_NEXT"
  NEW_URL="https://github.com/${FACTORY_REPO_SLUG}/issues/${EXISTING_NEXT}"
else
  NEW_URL=$(gh issue create \
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

- Policy: see the dispatch-ceiling design spec
- Archon command: \`commands/ceiling-revisit.md\`
- Prior revisit: #359 (comment with results)

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
    --label "ready-for-agent")
  echo "filed: $NEW_URL"

  # Board the new issue (established precedent, #342 Task 9): commands/ceiling-revisit.md Phase 5
  # labels the next revisit `ready-for-agent` but never adds it to project board 2, so the
  # scheduler cannot dispatch it without this step. Fail-soft.
  ITEM_ID=$(gh project item-add 2 --owner omniscient --url "$NEW_URL" --format json --jq .id 2>/dev/null || true)
  if [ -n "$ITEM_ID" ] && gh project item-edit --id "$ITEM_ID" --project-id PVT_kwHOAAFds84BcpWz \
       --field-id PVTSSF_lAHOAAFds84BcpWzzhXQl6I --single-select-option-id d877a5b3 >/dev/null 2>&1; then
    echo "boarded $NEW_URL as Backlog (item $ITEM_ID)"
  else
    echo "WARN: could not add $NEW_URL to project 2 / set Backlog — operator must board it manually"
  fi
fi
```
Expected: `filed: <URL>` followed by `boarded <URL> as Backlog (...)` or a non-fatal `WARN:` line
— or, on a retry, `SKIPPED (duplicate): next revisit issue already filed as #N`.

No commit — no repository file changes.

---

## Task 10 — Final verification

**Files:** none (verification only)

1. Run the full test suite exactly as `.github/workflows/ci.yml`'s `tests` job does — expect zero
   regressions, since no script/config under test was modified:
   ```bash
   PYTHONPATH=scripts python -m pytest tests/ -v
   bash tests/test_identity.sh
   bash tests/test_hooks.sh
   bash tests/test_smoke_gate.sh
   bash tests/test_run_compose.sh
   bash tests/test_model_proxy_compose.sh
   bash tests/test_model_proxy_smoke.sh
   bash tests/test_entrypoint_current_run.sh
   bash tests/test_entrypoint_session_window.sh
   bash tests/test_entrypoint_error_signature.sh
   bash tests/test_cost_report_endpoint.sh
   bash tests/test_cost_report_harness_economics.sh
   bash tests/test_run_record_hermetic.sh
   bash tests/test_entrypoint_cost_report_regression.sh
   bash tests/test_budget_gate.sh
   bash tests/test_verdict_gate_check.sh
   bash tests/test_budget_context.sh
   ```
   Do **not** wrap the two `test_entrypoint_*` lines with CI's `.github/workflows/ci.yml:22-29`
   `sudo install -d -m 777 /var/lib/dark-factory` / `test -z "$(ls -A ...)"` / `sudo rm -rf
   /var/lib/dark-factory` trio — that is a CI-runner-only leak-detector for an ephemeral box with
   no pre-existing `/var/lib/dark-factory`. In an implement-phase container, `/var/lib/dark-factory`
   is the live mounted `scheduler_state` volume (`run-compose.yml`) holding real cross-run state
   (`runs.jsonl`, `run-records/`, `error-signatures/`, `scheduler-state.json`); `sudo` is not even
   installed in this image (`Dockerfile` runs as `USER factory`), so the `install`/`rm -rf` calls
   would either no-op-fail or, if they somehow resolved, destroy the factory's only durable
   cross-run store. Both tests are already hermetic without it — `test_entrypoint_session_window.sh`
   and `test_entrypoint_error_signature.sh` export their own `CURRENT_RUN_DIR`/`SCHEDULER_STATE_DIR`
   to fresh `mktemp -d` scratch dirs before sourcing anything (statically enforced by
   `tests/test_run_record_hermetic.sh` since #362) — run them plain.
   Expected: all commands exit 0, including `tests/test_ceiling_revisit.py` and
   `tests/test_fetch_scorecard.py` unchanged from their `main` baseline.
2. Run both workflow DAG checks CI's `dag-check` job runs:
   ```bash
   python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
   python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
   ```
3. Confirm the `feat/issue-359-*` branch only carries the two docs from Task 1. Use the `BASE` SHA
   Task 1 captured at the true fork point, not a live re-read of `origin/main` (Task 6, if it ran,
   fetched `origin/main` again and may have advanced the remote-tracking ref):
   ```bash
   BASE=$(cat /tmp/ceiling-revisit-base-main)
   git diff --name-only "$BASE" HEAD
   ```
   Expected: exactly `docs/superpowers/specs/2026-09-04-dispatch-ceiling-weekly-revisit-design.md`
   and `docs/superpowers/plans/2026-09-04-dispatch-ceiling-weekly-revisit-plan.md` — nothing else.
   (The conditional `.archon/.env` change, if Task 6 ran, lives on the separate
   `chore/ceiling-revisit-${UNTIL}` branch/PR, not here.)
4. No further commit needed if step 3 is clean; if any stray file appears, remove it and commit
   the removal before moving on.
