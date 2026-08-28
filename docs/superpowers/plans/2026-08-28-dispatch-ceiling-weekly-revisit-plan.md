# Dispatch Ceiling (C9) Weekly Revisit — Execution Plan for #342

**Issue:** omniscient/dark-factory#342
**Spec:** `docs/superpowers/specs/2026-08-21-dispatch-ceiling-weekly-revisit-design.md`

## Goal

Execute the fourth run of the recurring dispatch-ceiling keyword revisit for #342: determine
whether #332 (the "Prior revisit") already measured this run's exact `SINCE`→`UNTIL` window and,
if so, post a short restatement instead of re-running the analysis; otherwise fetch cumulative
Factory Scorecard data and run the full standing procedure. Either way: correct the stale
L-bucket report text if it fires, never file a new XL-bucket code-change issue (policy-closed by
the #331 operator decision, 2026-08-22), file two duplicate-guarded follow-up tickets for the
recurring gaps this lineage keeps rediscovering, and unconditionally file the next weekly revisit
issue.

## Architecture

**Operational analysis run, not a service change.** No production code, script, or config file is
created or modified by this ticket. `scripts/fetch_scorecard.py` and `scripts/ceiling_revisit.py`
are already implemented, unit-tested (`tests/test_fetch_scorecard.py`,
`tests/test_ceiling_revisit.py`), and unmodified since commit `27c890b`. The only durable,
git-tracked artifacts this ticket produces are the spec (already committed) and this plan; every
other effect (issue comment, possible `.archon/.env` PR, possible new issues) is a GitHub-side
effect produced by running `commands/ceiling-revisit.md`'s phases (with the execution-time
overlays below) against this run's parameters.

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
2026-08-21 per #332).

**This plan is being written 7 days after the spec, and the spec's own core assumption about
today's run does not hold.** The spec (`2026-08-21-...-design.md`) was written assuming implement
would run same-day as refine (2026-08-21) — the established cadence in this lineage — and on that
assumption predicted the same-window duplicate guard would very likely fire this cycle (implement's
`UNTIL` == #332's `UNTIL` == 2026-08-21). **That assumption is already false by the time this plan
is written**: today is 2026-08-28, a full week later. If implement runs today or on any date after
2026-08-21, the `UNTIL` it computes (`date -u +%Y-%m-%d`) will not equal `2026-08-21`, so the
same-window guard is expected **not** to fire this cycle — implement should expect to run the full
standing-requirements procedure (Tasks 4-8 below), not the skip/restatement path (Task 6's
alternate branch), even though the spec's Assumptions section describes the skip path as the
expected outcome. This is not a contradiction of the spec — the guard is correctly *conditional on
window equality*, computed at implement time, exactly as the spec requires (Requirements, "New this
cycle" — the guard is keyed on `SINCE`+`UNTIL` equality, not "ran same day as refine"). Task 3 below
still implements both branches so a genuine same-day implement run (or a resumed/retried run) is
handled correctly either way — this note only corrects the *expected* branch, matching the
precedent set by #294's and #332's own plans each correcting the spec's date assumptions when a
plan→implement or spec→plan gap materialized.

**Operator amendment supersedes the spec's carried-forward XL-bucket duplicate-issue guard.** The
spec's Requirements section carries forward "check for an existing open issue... before filing" as
a standing requirement, but its own "Operator amendment (2026-08-27)" section overrides this: issue
#331 was closed 2026-08-22 by an explicit **policy decision** (not a resolution) that XL tickets
keep parking for human pairing, and this lineage was asked to stop re-filing the observation. Task 5
below implements the operator amendment's replacement logic (open-match-or-closed-policy check),
not the plain open-issue-only check from #294's/#332's plans.

**Computed values are persisted across tasks, not just kept in shell variables.** Tasks 2-10 each
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

**Memory patterns applied** (`.archon/memory/codebase-patterns.md`, `.archon/memory/architecture.md`):
- Issue #42: a refine-phase spec/plan approved on this `refine/issue-342-*` branch does not
  automatically transfer to the `feat/issue-342-*` branch the implement phase creates. Task 1 makes
  the implement agent copy both docs over explicitly before doing anything else.
- Issue #250: use the two-dot diff form (`git diff <base-sha> HEAD`) for the final out-of-scope
  check, not three-dot, and freeze the base SHA in Task 1 before any later task re-fetches
  `origin/main` — Task 7 (if it runs) does its own `git fetch origin main`, which can advance the
  remote-tracking ref past this branch's true fork point.
- Issue #342 (this ticket's own predicted architecture entry, recorded by #332's refine pass): a
  refine/plan on the "Revisit dispatch ceiling" lineage must check whether the "Prior revisit" issue
  already carries a matching-window analysis comment before treating a full Scorecard fetch as
  needed. Task 3 implements this check directly (not just documents it).

**Archival note.** Prior cycles' spec/plan pairs ended up under `docs/archive/` (#30's, #294's,
#332's). Archiving is not this ticket's job — a later cycle or separate housekeeping pass does it,
mirroring #294's and #332's plans, neither of which archived itself.

## Tech Stack

Bash, Python 3 (`scripts/fetch_scorecard.py`, `scripts/ceiling_revisit.py` — both on `main`,
unmodified), `gh` CLI, `jq`. No new dependencies.

## File Structure

| Path | Purpose |
|---|---|
| `docs/superpowers/specs/2026-08-21-dispatch-ceiling-weekly-revisit-design.md` | Already committed (this ticket's spec) |
| `docs/superpowers/plans/2026-08-28-dispatch-ceiling-weekly-revisit-plan.md` | This plan (committed by the plan phase) |
| *(GitHub side effects only, below)* | Issue #342 comment (full analysis or short restatement); conditional PR touching `.archon/.env` on branch `chore/ceiling-revisit-<UNTIL>`; XL-bucket code-change issue — expected **not** filed (policy-closed per #331); two duplicate-guarded follow-up tickets (cadence gate; ceiling-revisit hygiene); unconditional new weekly-revisit issue, boarded to the project's Backlog column |

No other repository file is created, modified, or deleted by this ticket.

---

## Task 1 — Bring the spec and plan onto the implementation branch

**Files:** `docs/superpowers/specs/2026-08-21-dispatch-ceiling-weekly-revisit-design.md`,
`docs/superpowers/plans/2026-08-28-dispatch-ceiling-weekly-revisit-plan.md` (copied, not
re-authored)

1. On the `feat/issue-342-*` branch the implement phase creates, verify both docs exist (they were
   committed on `refine/issue-342-revisit-dispatch-ceiling-----re-measure-`, not automatically
   present on a fresh branch off `main`). The implement phase runs from a **fresh clone**, where the
   refine branch exists only as `refs/remotes/origin/refine/...` — a bare `git show
   refine/issue-342-...:<path>` does not resolve there, and a `>` redirect on a failed `git show`
   would silently create an **empty** file that then gets committed. Fetch explicitly, reference the
   `origin/` remote-tracking ref, and assert non-emptiness. Also capture the `origin/main` commit
   this branch was actually cut from, for Task 11's final out-of-scope check:
   ```bash
   git rev-parse origin/main > /tmp/ceiling-revisit-base-main
   REFINE_BRANCH="refine/issue-342-revisit-dispatch-ceiling-----re-measure-"
   git fetch origin "$REFINE_BRANCH"
   git show "origin/${REFINE_BRANCH}:docs/superpowers/specs/2026-08-21-dispatch-ceiling-weekly-revisit-design.md" \
     > docs/superpowers/specs/2026-08-21-dispatch-ceiling-weekly-revisit-design.md
   git show "origin/${REFINE_BRANCH}:docs/superpowers/plans/2026-08-28-dispatch-ceiling-weekly-revisit-plan.md" \
     > docs/superpowers/plans/2026-08-28-dispatch-ceiling-weekly-revisit-plan.md
   test -s docs/superpowers/specs/2026-08-21-dispatch-ceiling-weekly-revisit-design.md
   test -s docs/superpowers/plans/2026-08-28-dispatch-ceiling-weekly-revisit-plan.md
   ```
   Expected: both files exist and are non-empty in the working tree (`git status --short` shows
   them as new/modified; both `test -s` checks exit 0). If either `test -s` fails, stop — this
   ticket's only durable artifact would otherwise be silently lost.
2. Commit:
   ```bash
   git add docs/superpowers/specs/2026-08-21-dispatch-ceiling-weekly-revisit-design.md \
           docs/superpowers/plans/2026-08-28-dispatch-ceiling-weekly-revisit-plan.md
   git commit -m "docs: bring over approved spec/plan for issue #342"
   ```

---

## Task 2 — Pre-flight verification

**Files:** none (read-only verification)

**All variables computed in this task and Task 3 must survive into Tasks 3-10.** Start fresh:
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
   test -n "$FACTORY_REPO_SLUG" || { echo "FATAL: FACTORY_REPO_SLUG not set"; exit 1; }
   echo "$FACTORY_REPO_SLUG"
   echo "${FACTORY_PRODUCT_NAME:-<unset>}"
   ```
   Expected: authenticated to `github.com`; `FACTORY_REPO_SLUG` prints `omniscient/dark-factory`;
   `FACTORY_PRODUCT_NAME` prints a product name, not `<unset>`. The explicit `test -n` moves a
   missing `FACTORY_REPO_SLUG` failure here rather than letting it surface later as a confusing
   `KeyError` inside Task 6's Python block or a wrong-repo `fetch_scorecard.py` run in Task 4.
3. Confirm the labels used below already exist:
   ```bash
   for LBL in "priority: should-have" "enhancement" "size: S" "ready-for-agent"; do
     gh label list --repo "$FACTORY_REPO_SLUG" --json name --jq '.[].name' | grep -qxF "$LBL" \
       && echo "OK: $LBL" || { echo "MISSING: $LBL"; exit 1; }
   done
   ```
   Expected: `OK: <label>` for all four (verified this run — all four present).
4. Confirm #331's current state and capture its closing rationale — the Operator amendment's
   citation source for Task 5's policy-skip path:
   ```bash
   XL_331_STATE=$(gh issue view 331 --repo "$FACTORY_REPO_SLUG" --json state --jq .state)
   echo "XL_331_STATE=$XL_331_STATE"
   echo "XL_331_STATE=\"$XL_331_STATE\"" >> "$RUN_VARS"
   ```
   Expected: `XL_331_STATE=CLOSED` (verified this run: closed 2026-08-22, "policy" not
   "resolution" — see #331's closing comment). If this instead prints `OPEN`, #331 has been
   reopened since this plan was written — Task 5 handles that branch explicitly.

No commit — this task only reads state.

---

## Task 3 — Compute dates and check the same-window duplicate guard

**Files:** none tracked

0. Source Task 2's variables:
   ```bash
   RUN_VARS=/tmp/ceiling-revisit-vars.sh
   source "$RUN_VARS"
   ```
1. Compute `UNTIL`/`NEXT_DATE` from the actual execution date — do **not** reuse
   `2026-08-21`/`2026-08-28` verbatim (those were the spec's/this plan's own write-time dates, not
   necessarily implement's). Persist immediately:
   ```bash
   SINCE=2026-06-12
   UNTIL=$(date -u +%Y-%m-%d)
   NEXT_DATE=$(date -u -d "${UNTIL} +7 days" +%Y-%m-%d)
   echo "SINCE=$SINCE UNTIL=$UNTIL NEXT_DATE=$NEXT_DATE"
   { echo "SINCE=\"$SINCE\""; echo "UNTIL=\"$UNTIL\""; echo "NEXT_DATE=\"$NEXT_DATE\""; } >> "$RUN_VARS"
   ```
   Expected: `UNTIL` prints today's UTC date, `NEXT_DATE` is exactly 7 days later.
2. **Same-window duplicate guard** (spec Requirements, "New this cycle"): check whether #332
   already carries an analysis comment whose window end equals this run's `UNTIL`. #332's own
   analysis comment header has the exact literal form `## Dispatch Ceiling Weekly Revisit —
   <SINCE> → <UNTIL>` (verified: `## Dispatch Ceiling Weekly Revisit — 2026-06-12 → 2026-08-21`).
   Fetch all of #332's comment bodies and extract the trailing date on that header line:
   ```bash
   PRIOR_ISSUE=332
   ALL_COMMENTS=$(gh issue view "$PRIOR_ISSUE" --repo "$FACTORY_REPO_SLUG" --json comments --jq '.comments[].body')
   PRIOR_HEADER=$(echo "$ALL_COMMENTS" | grep -E '^## Dispatch Ceiling Weekly Revisit — [0-9]{4}-[0-9]{2}-[0-9]{2} → [0-9]{4}-[0-9]{2}-[0-9]{2}$' | tail -1 || true)
   PRIOR_UNTIL=$(echo "$PRIOR_HEADER" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}$' || true)
   echo "PRIOR_HEADER=$PRIOR_HEADER"
   echo "PRIOR_UNTIL=$PRIOR_UNTIL"

   if [ -n "$PRIOR_UNTIL" ] && [ "$PRIOR_UNTIL" = "$UNTIL" ]; then
     SAME_WINDOW=true
   else
     SAME_WINDOW=false
   fi
   echo "SAME_WINDOW=$SAME_WINDOW"
   { echo "PRIOR_UNTIL=\"$PRIOR_UNTIL\""; echo "SAME_WINDOW=\"$SAME_WINDOW\""; } >> "$RUN_VARS"
   ```
   Expected (per this plan's Architecture note): given implement is very likely to run on or after
   2026-08-28 (this plan's own write date, already 7 days past #332's `UNTIL=2026-08-21`),
   `SAME_WINDOW=false` is the expected outcome this cycle — contrary to the spec's own Assumptions
   section, which predicted a same-day match. If implement happens to run same-day as this plan's
   `UNTIL` computation lands on `2026-08-21` (impossible — that date has passed) or some other
   coincidental match, `SAME_WINDOW=true` is still handled correctly by Task 6's alternate branch.
   `PRIOR_UNTIL` empty (header not found) is treated identically to a non-match — proceed with the
   full procedure rather than skip blind. The grep pattern requires the full `<SINCE-date> →
   <UNTIL-date>` shape (matching the real analysis header's literal form, verified against #332's
   posted comment: `## Dispatch Ceiling Weekly Revisit — 2026-06-12 → 2026-08-21`), then takes the
   last (`tail -1`) match — this matters because a same-window restatement's own header (Task 6:
   `## Dispatch Ceiling Weekly Revisit — Same-Window Restatement`) also matches the bare `^##
   Dispatch Ceiling Weekly Revisit — ` prefix but carries no dates at all; without the two-date
   shape constraint, a `tail -1` over both header forms would select the dateless restatement
   header and spuriously null out `PRIOR_UNTIL`. Requiring both dates excludes restatement headers
   entirely, so `tail -1` correctly selects the most recent *real* analysis header if #332 ever
   carries more than one.

No commit — all outputs are transient `/tmp` files or shell variables.

---

## Task 4 — Phase 1: Fetch and analyze (only if `SAME_WINDOW=false`)

**Files:** none tracked (writes transient `/tmp/ceiling-revisit-scorecard.json`,
`/tmp/ceiling-revisit-report.md`, `/tmp/ceiling-revisit-meta.txt`)

Skip this task entirely if `SAME_WINDOW=true` (Task 6 posts a restatement instead).

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
   `ceiling_revisit.py`'s own `DEFAULT_KEYWORDS` fallback, so Task 4's analysis and Task 7's diff
   can never drift apart once an `.archon/.env` override exists.

   Expected: `fetch_scorecard.py` ends with `Wrote /tmp/ceiling-revisit-scorecard.json`;
   `ceiling_revisit.py` writes `/tmp/ceiling-revisit-report.md` (`### Per-Bucket Triad` table with
   rows `S`, `M`, `L+XL`; `### Per-Keyword Analysis` table); `/tmp/ceiling-revisit-meta.txt` ends
   with a line starting `<!-- CEILING_REVISIT_JSON {"keywords_to_remove": [...],
   "new_keyword_candidates": [...], "l_bucket_needs_issue": <bool>} -->`. This step can take
   several minutes (git-blame churn over the full ~11-week cumulative window) — expected, not a
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
   `L_NEEDS_ISSUE=False` matches every prior run in this lineage including #332's just-posted one,
   but the live data decides, not this plan.

No commit — all outputs are transient.

---

## Task 5 — XL-bucket policy/duplicate guard + stale-text correction (only if `SAME_WINDOW=false` and `L_NEEDS_ISSUE=True`)

**Files:** none tracked (mutates the transient `/tmp/ceiling-revisit-report.md` only)

This implements the spec's stale-text-correction requirement and the Operator amendment's
replacement for the old open-issue-only duplicate guard.

0. Source variables:
   ```bash
   source /tmp/ceiling-revisit-vars.sh
   ```
1. ```bash
   if [ "$SAME_WINDOW" = "false" ] && [ "$L_NEEDS_ISSUE" = "True" ]; then
     # (a) Stale-text correction (spec requirement, named explicitly this cycle — third
     # consecutive cycle needing it). scripts/ceiling_revisit.py:227-229 is unmodified and still
     # emits "The L=always-above-ceiling rule may be overly conservative." / "...in `scheduler.sh`."
     # — stale on two counts since commit 4feef16 (L already dispatches autonomously) and the
     # scheduler.sh -> scripts/scheduler_lib.sh split. Fixing the script is out of this ticket's
     # scope (shared, unmodified infrastructure, its own follow-up ticket — Task 10 below);
     # correct the rendered report text in place instead, same pattern #294's/#332's plans used.
     sed -i \
       -e 's/The L=always-above-ceiling rule may be overly conservative\./The XL=always-above-ceiling rule may be overly conservative (L has dispatched autonomously since commit 4feef16, 2026-06-21)./' \
       -e 's/in `scheduler\.sh`\./in `scripts\/scheduler_lib.sh`./' \
       /tmp/ceiling-revisit-report.md

     # (b) Operator amendment (2026-08-27): #331 is policy-closed, not just "an open issue that
     # may exist" — replace the plain open-issue-only duplicate check with an
     # open-match-or-closed-policy check. Default action is to SKIP citing #331, unless #331 has
     # been reopened or explicitly reversed since this plan was written.
     XL_331_STATE=$(gh issue view 331 --repo "$FACTORY_REPO_SLUG" --json state --jq .state)
     XL_331_REVERSAL=$(gh issue view 331 --repo "$FACTORY_REPO_SLUG" --json comments \
       --jq '.comments[].body' | grep -iE 'revers|re-?open.*polic|polic.*re-?open' || true)

     if [ "$XL_331_STATE" = "CLOSED" ] && [ -z "$XL_331_REVERSAL" ]; then
       XL_ISSUE_ACTION="skip-policy"
     else
       # #331 reopened, or a reversal signal exists — fall back to the pre-amendment open-issue
       # duplicate check (mirrors #332's plan Task 4) before ever filing a new one.
       XL_DUP=$(gh issue list --repo "$FACTORY_REPO_SLUG" --state open --limit 200 \
         --json number,title \
         --jq '.[] | select(.title | test("XL=always-above-ceiling rule")) | .number' | head -1)
       if [ -n "$XL_DUP" ]; then
         XL_ISSUE_ACTION="skip-duplicate"
         XL_DUP_ISSUE="$XL_DUP"
       else
         XL_ISSUE_ACTION="file"
       fi
     fi
     echo "XL_ISSUE_ACTION=$XL_ISSUE_ACTION"

     # (c) Fold the outcome into the report before Task 6 posts it — insert BEFORE the report's
     # own trailing "---" / "*Posted by..." footer, not appended after it.
     export XL_ISSUE_ACTION XL_DUP_ISSUE
     python3 - <<'PYEOF'
import os
path = "/tmp/ceiling-revisit-report.md"
text = open(path).read()
action = os.environ["XL_ISSUE_ACTION"]
if action == "skip-policy":
    note = """
### XL-Bucket Issue — Skipped (Operator Policy, #331)

L+XL-bucket success cleared the >70%-at-n>=5 threshold again this cycle. Issue #331 ("Revisit
XL=always-above-ceiling rule in `is_above_ceiling()` — `scheduler_lib.sh`") was closed 2026-08-22
by an explicit operator policy decision: XL tickets keep parking for human pairing, since the
L+XL bucket's success signal is L-dominated and XL epics are where a human gate is cheapest
relative to blast radius. Per that decision, no new issue is filed for this observation; see
#331's closing comment for the full rationale.
"""
elif action == "skip-duplicate":
    note = f"""
### XL-Bucket Issue — Skipped (Duplicate Guard)

L+XL-bucket success cleared the >70%-at-n>=5 threshold again this cycle, but issue
#{os.environ.get("XL_DUP_ISSUE", "")} ("Revisit XL=always-above-ceiling rule in
`is_above_ceiling()` — `scheduler_lib.sh`") is already open covering this observation
(#331's policy-closed decision has since been reopened or reversed). No duplicate issue was
filed; see #{os.environ.get("XL_DUP_ISSUE", "")} instead.
"""
else:
    note = ""
if note:
    marker = "\n---\n"
    idx = text.rfind(marker)
    text = text[:idx] + note + text[idx:] if idx != -1 else text + note
    open(path, "w").write(text)
PYEOF
     { echo "XL_ISSUE_ACTION=\"$XL_ISSUE_ACTION\""; echo "XL_DUP_ISSUE=\"${XL_DUP_ISSUE:-}\""; } >> /tmp/ceiling-revisit-vars.sh
   else
     echo 'XL_ISSUE_ACTION="skip-not-needed"' >> /tmp/ceiling-revisit-vars.sh
   fi
   ```
   Expected: if `L_NEEDS_ISSUE=False` or `SAME_WINDOW=true`, `XL_ISSUE_ACTION=skip-not-needed` and
   no report mutation. If `L_NEEDS_ISSUE=True` (the plausible case, given #332 measured L+XL at
   100%/n=6), `XL_ISSUE_ACTION=skip-policy` is the expected branch (per Task 2 step 4's verified
   `XL_331_STATE=CLOSED` and no reversal comment on #331 as of this plan) — `skip-duplicate` and
   `file` only trigger if #331's state has changed since this plan was written.

No commit — mutates only a transient `/tmp` file.

---

## Task 6 — Post the analysis comment on #342 (branches on `SAME_WINDOW`)

**Files:** none tracked (GitHub side effect only)

0. Source variables:
   ```bash
   source /tmp/ceiling-revisit-vars.sh
   ```
1. **If `SAME_WINDOW=true`** — post a short, self-contained restatement instead of the full
   report, per the spec's skip-path requirement. Extract the relevant sections from #332's own
   comment (re-fetched here — Task 3's `ALL_COMMENTS` was a plain shell variable and does not
   survive the task-boundary shell invocation, per this plan's Architecture section) rather than
   hardcoding numbers into this plan, so the restatement can never drift from what #332 actually
   measured:
   ```bash
   if [ "$SAME_WINDOW" = "true" ]; then
     PRIOR_ISSUE=332
     export PRIOR_ISSUE UNTIL FACTORY_REPO_SLUG
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
     python3 - <<'PYEOF' && gh issue comment 342 --repo "$FACTORY_REPO_SLUG" --body-file /tmp/ceiling-revisit-restatement.md
import subprocess, json, os

issue = int(os.environ["PRIOR_ISSUE"])
until = os.environ["UNTIL"]
repo = os.environ["FACTORY_REPO_SLUG"]
comments = json.loads(subprocess.run(
    ["gh", "issue", "view", str(issue), "--repo", repo, "--json", "comments"],
    capture_output=True, text=True, check=True).stdout)["comments"]

body = next((c["body"] for c in comments if c["body"].startswith("## Dispatch Ceiling Weekly Revisit")), None)
if body is None:
    raise SystemExit("FATAL: no comment on #{} starts with the expected analysis header — check SAME_WINDOW logic/Task 3".format(issue))

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

# Live policy check, independent of whatever #332's copied section says — #332's comment may
# have been written while #331 was still open, and copying it verbatim would restate a stale
# claim. Task 5 (which would normally own this citation) never runs on this branch, so this
# is the one place the same-window restatement path applies the Operator amendment itself.
xl331 = json.loads(subprocess.run(
    ["gh", "issue", "view", "331", "--repo", repo, "--json", "state"],
    capture_output=True, text=True, check=True).stdout)
policy_note = ""
if xl331["state"] == "CLOSED":
    caveat = (
        " (The section above is copied from #{0}'s comment as posted and may predate this "
        "policy closure.)"
    ).format(issue) if xl_section else ""
    policy_note = (
        "\n\n_Current status: #331 is CLOSED by operator policy decision (2026-08-22) — XL "
        "tickets keep parking for human pairing regardless of L+XL success rate; no new "
        "XL-bucket issue is filed.{0}_"
    ).format(caveat)

restatement = f"""## Dispatch Ceiling Weekly Revisit — Same-Window Restatement

Issue #{issue}'s analysis comment already covers this exact cumulative window
(`SINCE=2026-06-12` → `UNTIL={until}`) against the same unmodified, deterministic
scripts — re-running the fetch/analysis would produce an identical result at real cost for zero
new signal (per this ticket's spec, "New this cycle" — same-window duplicate guard). See
[#{issue}](https://github.com/{repo}/issues/{issue}) for the full report. Headline results,
restated here for a reader who doesn't want to cross-reference:

{triad}

{keyword_rec}

{xl_section}{policy_note}

---
*Posted by Dark Factory Weekly Ceiling Revisit — same-window restatement, see #{issue}*
"""
open("/tmp/ceiling-revisit-restatement.md", "w").write(restatement)
print(restatement)
PYEOF
   fi
   ```
   Expected: `gh` prints the URL of the new comment on #342, containing the restated per-bucket
   table, keyword recommendation, and XL-bucket note pulled verbatim from #332's comment, plus a
   live-checked policy footnote if #331 is currently closed (expected, per Task 2 step 4).
2. **If `SAME_WINDOW=false`** — post the report generated in Task 4 (corrected/annotated in
   Task 5) as the normal Phase 2 comment:
   ```bash
   if [ "$SAME_WINDOW" = "false" ]; then
     gh issue comment 342 --repo "$FACTORY_REPO_SLUG" --body-file /tmp/ceiling-revisit-report.md
   fi
   ```
   Expected: `gh` prints the URL of the new comment on #342, containing the freshly computed
   `### Per-Bucket Triad` / `### Per-Keyword Analysis` tables and (if `L_NEEDS_ISSUE=True`) the
   corrected XL-bucket wording plus the Task 5 skip note.

No commit — no repository file changes.

---

## Task 7 — Phase 3: Open a PR to `.archon/.env` (only if `SAME_WINDOW=false` and `KEYWORDS_TO_REMOVE` non-empty)

**Files:** `.archon/.env` (new or modified, on a separate branch `chore/ceiling-revisit-${UNTIL}`
— not on `feat/issue-342-*`)

```bash
source /tmp/ceiling-revisit-vars.sh   # SINCE, UNTIL, CURRENT_KEYWORDS, KEYWORDS_TO_REMOVE, SAME_WINDOW

if [ "$SAME_WINDOW" = "false" ] && [ -n "${KEYWORDS_TO_REMOVE:-}" ]; then
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

  PR_BRANCH="chore/ceiling-revisit-${UNTIL}"
  git fetch origin main
  git checkout -b "$PR_BRANCH" origin/main

  # Secrets guard: write a fresh, minimal file containing only the one line this ticket is
  # authorized to change — a real deployment's .archon/.env may carry unrelated secret lines.
  printf 'ABOVE_CEILING_KEYWORDS=%s\n' "$NEW_KWS" > "$ENV_FILE"
  git add -f "$ENV_FILE"
  git commit -m "chore(env): update ABOVE_CEILING_KEYWORDS per weekly revisit (#342)

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
    --body "Recommended by weekly dispatch ceiling analysis on issue #342.

Removes: \`${KEYWORDS_TO_REMOVE}\`

See the analysis comment on #342 for full data and decision rationale.

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
```
Expected (only if the branch runs): `gh pr create` prints the new PR's URL scoped to a single
`.archon/.env` diff against `main`, containing exactly one line; `git status` on
`feat/issue-342-*` shows no pending changes from this block afterward. Given #332's own
just-measured window found no discriminative keyword (matching every prior cycle), this task is
expected **not** to run.

No commit on `feat/issue-342-*` — the commit above belongs to the separate `chore/` branch.

---

## Task 8 — File the XL-bucket code-change issue (only if `XL_ISSUE_ACTION=file`)

**Files:** none tracked (GitHub side effect only)

This is the rare/defensive branch — expected **not** to run this cycle (Task 5's expected outcome
is `skip-policy`, citing #331).

```bash
source /tmp/ceiling-revisit-vars.sh   # SINCE, UNTIL, XL_ISSUE_ACTION

if [ "$XL_ISSUE_ACTION" = "file" ]; then
  gh issue create \
    --repo "$FACTORY_REPO_SLUG" \
    --title "Revisit XL=always-above-ceiling rule in is_above_ceiling() — scheduler_lib.sh" \
    --body "## Purpose

The weekly dispatch ceiling analysis (issue #342, window ${SINCE}→${UNTIL})
found the combined L+XL-bucket success rate exceeds 70% at n>=5. The XL=always-above-ceiling rule
in \`scripts/scheduler_lib.sh\` (\`is_above_ceiling()\`) may be overly conservative.

Note: L tickets already dispatch autonomously as of commit 4feef16 (2026-06-21) — only XL
tickets (and M tickets with a keyword title match) currently park in Blocked. This issue is filed
only because #331's policy-closed decision (2026-08-22) has since been reopened or explicitly
reversed — verify that before proceeding (see #331's timeline).

## What to review

- Inspect \`is_above_ceiling()\` in \`scripts/scheduler_lib.sh\`.
- Assess whether the XL-bucket ceiling should be relaxed (e.g. XL+keyword pattern only, mirroring
  the existing M-bucket rule).
- This is a **code change** (not an env-var change) — requires PR to \`scripts/scheduler_lib.sh\`.
- Prior instances: #29, #31, #331 (all closed — #331 specifically as a policy decision; re-verify
  that decision was actually reversed before reusing this issue's conclusions).

## References

- Triggering analysis: issue #342
- Policy: docs/superpowers/specs/2026-08-21-dispatch-ceiling-weekly-revisit-design.md

---
*Filed automatically by weekly ceiling revisit*" \
    --label "enhancement" \
    --label "priority: should-have"
fi
```
Expected: no output/action in the common case (`XL_ISSUE_ACTION` is `skip-policy`,
`skip-duplicate`, or `skip-not-needed`).

No commit — no repository file changes.

---

## Task 9 — Phase 5: File the next weekly revisit issue (unconditional)

**Files:** none tracked (GitHub side effect only)

```bash
source /tmp/ceiling-revisit-vars.sh   # SINCE, UNTIL, NEXT_DATE

test -n "$UNTIL" && test -n "$NEXT_DATE" || { echo "FATAL: UNTIL/NEXT_DATE not set — check Task 3 step 1"; exit 1; }

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

- Policy: docs/superpowers/specs/2026-08-21-dispatch-ceiling-weekly-revisit-design.md
- Archon command: \`commands/ceiling-revisit.md\`
- Prior revisit: #342 (comment with results)

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

# Board the new issue (established precedent, #332 Task 8): commands/ceiling-revisit.md Phase 5
# labels the next revisit `ready-for-agent` but never adds it to project board 2, so the
# scheduler cannot dispatch it without this step. Fail-soft.
ITEM_ID=$(gh project item-add 2 --owner omniscient --url "$NEW_URL" --format json --jq .id 2>/dev/null || true)
if [ -n "$ITEM_ID" ] && gh project item-edit --id "$ITEM_ID" --project-id PVT_kwHOAAFds84BcpWz \
     --field-id PVTSSF_lAHOAAFds84BcpWzzhXQl6I --single-select-option-id d877a5b3 >/dev/null 2>&1; then
  echo "boarded $NEW_URL as Backlog (item $ITEM_ID)"
else
  echo "WARN: could not add $NEW_URL to project 2 / set Backlog — operator must board it manually"
fi
```
Expected: `filed: <URL>` followed by `boarded <URL> as Backlog (...)` or a non-fatal `WARN:` line.

No commit — no repository file changes.

---

## Task 10 — File two duplicate-guarded follow-up tickets (unconditional)

**Files:** none tracked (GitHub side effect only)

Per the spec's "New this cycle" requirements: two recurring gaps (no real dispatch-time cadence
gate; stale report text + XL-bucket duplicate guard not yet baked into source) have now recurred
across two-plus consecutive cycles with only ad-hoc, spec-silent patches. File both as separate,
duplicate-guarded tickets — **not boarded** to the project (mirroring #331's own precedent: it was
never added to project board 2, since it requires a human-reviewed spec before any agent can act
on it, per CLAUDE.md scope discipline). Client-side jq regex matching against `--state all` (not
just open) avoids re-filing something already resolved and avoids `gh issue list --search`'s
fuzzy full-text matching.

```bash
source /tmp/ceiling-revisit-vars.sh   # SINCE, UNTIL

# --- Follow-up 1: dispatch-time cadence gate ---
CADENCE_TITLE="Add a dispatch-time cadence gate for the weekly ceiling-revisit issue lineage"
CADENCE_DUP=$(gh issue list --repo "$FACTORY_REPO_SLUG" --state all --limit 200 \
  --json number,title \
  --jq '.[] | select(.title | test("dispatch-time cadence gate for the weekly ceiling-revisit")) | .number' \
  | head -1)

if [ -z "$CADENCE_DUP" ]; then
  gh issue create \
    --repo "$FACTORY_REPO_SLUG" \
    --title "$CADENCE_TITLE" \
    --body "## Purpose

The recurring 'Revisit dispatch ceiling' issue lineage (#107 → #30 → #294 → #332 → #342 → ...) has
no real dispatch-time cadence gate. The issue body's 'Target date' is decorative prose — not read
by \`scheduler.sh\`, \`scripts/scheduler_lib.sh\`, \`scripts/factory_core/**\`, or
\`config/config.yaml\` (verified by search, #342 refine pass). With
\`scheduler.factory_wip_limit: 1\` and an idle backlog, a freshly auto-filed next-revisit issue can
be refined/planned/implemented again within seconds of the prior cycle finishing, producing an
identical analysis window — exactly what #342's same-window duplicate guard had to work around as
a plan/implement-time overlay (the third such overlay in this lineage: #332's XL-bucket duplicate
guard, #342's same-window guard).

## What to review

- Propose a real gate so a freshly-filed 'Revisit dispatch ceiling' issue's \`ready-for-agent\`
  opt-in (or refine dispatch generally) is held until its stated 'Target date' has passed.
- Candidate surfaces: \`scheduler.sh\`, \`scripts/factory_core/**\`, \`config/config.yaml\`.
- This is a genuine change to dispatch/scheduling behavior — needs its own reviewed spec per
  CLAUDE.md scope discipline, not bundled into an env/analysis-only ticket.

## References

- Raised by: issue #342's spec (docs/superpowers/specs/2026-08-21-dispatch-ceiling-weekly-revisit-design.md)
- Prior overlays this gate would obsolete: #332 (XL-bucket duplicate guard), #342 (same-window guard)

---
*Filed automatically by weekly ceiling revisit (#342)*" \
    --label "enhancement" \
    --label "priority: should-have"
else
  echo "SKIPPED (duplicate): cadence-gate follow-up already exists as #$CADENCE_DUP"
fi

# --- Follow-up 2: ceiling-revisit hygiene (stale text + permanent XL-bucket guard), bundled ---
HYGIENE_TITLE="Ceiling-revisit hygiene: fix stale L-bucket text and add a permanent XL-bucket duplicate/policy guard"
HYGIENE_DUP=$(gh issue list --repo "$FACTORY_REPO_SLUG" --state all --limit 200 \
  --json number,title \
  --jq '.[] | select(.title | test("Ceiling-revisit hygiene")) | .number' \
  | head -1)

if [ -z "$HYGIENE_DUP" ]; then
  gh issue create \
    --repo "$FACTORY_REPO_SLUG" \
    --title "$HYGIENE_TITLE" \
    --body "## Purpose

Two gaps in the ceiling-revisit tooling have now each needed the same ad-hoc plan-time patch for
three consecutive cycles (#294, #332, #342), always applied as a rendered-output correction rather
than a source fix:

1. \`scripts/ceiling_revisit.py:227-231\` (\`generate_report()\`) still emits stale text —
   'The L=always-above-ceiling rule may be overly conservative' / '...in \`scheduler.sh\`' — even
   though XL, not L, has been the always-above-ceiling bucket since commit 4feef16 (2026-06-21),
   and the file is now \`scripts/scheduler_lib.sh\`, not \`scheduler.sh\`.
2. \`commands/ceiling-revisit.md\`'s Phase 4 (issue-create title/body, ~lines 129-140) has no
   built-in duplicate-detection guard for the conditional XL-bucket issue filing, and does not
   know about the #331 operator policy decision (2026-08-22: XL stays always-above-ceiling by
   policy, not by open finding) — every cycle since #332 has had to re-derive this guard as a
   plan/implement-time overlay instead of it living in the command itself.

## What to review

- Fix the stale strings in \`scripts/ceiling_revisit.py\`'s \`generate_report()\` at the source.
- Add a permanent duplicate/policy guard to \`commands/ceiling-revisit.md\` Phase 4 directly (check
  for an open matching issue **or** a closed-by-policy #331/successor before filing), so future
  cycles' plans stop re-deriving it.
- Bundled into one ticket (not split from the cadence-gate follow-up) because both fixes touch the
  same conditional code path (Phase 4's XL-bucket issue filing) in the same two files — splitting
  risks two runs editing overlapping lines in \`commands/ceiling-revisit.md\`.
- Preserve the \`# TARGET-PATH\` prefix convention for the MarketHawk instance; this is a
  text/logic fix, not a path fix.
- This is a real code change to a phase-command file and a script — needs its own reviewed spec
  per CLAUDE.md scope discipline, not bundled into an env/analysis-only ticket.

## References

- Raised by: issue #342's spec (docs/superpowers/specs/2026-08-21-dispatch-ceiling-weekly-revisit-design.md)
- Prior ad-hoc patches: #294 (report-text correction), #332 (report-text correction + duplicate
  guard), #342 (report-text correction + policy guard)

---
*Filed automatically by weekly ceiling revisit (#342)*" \
    --label "enhancement" \
    --label "priority: should-have"
else
  echo "SKIPPED (duplicate): ceiling-revisit-hygiene follow-up already exists as #$HYGIENE_DUP"
fi
```
Expected: two new issue URLs (or `SKIPPED (duplicate): ... already exists as #N` for either/both,
if a prior cycle already filed them). Verified at plan-writing time: no existing issue matches
either title pattern (`gh issue list --state all --search "cadence gate"` /
`"ceiling-revisit hygiene"` — neither returns a match), so both are expected to file successfully
this cycle.

No commit — no repository file changes.

---

## Task 11 — Final verification

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
   bash tests/test_cost_report_endpoint.sh
   bash tests/test_cost_report_harness_economics.sh
   bash tests/test_run_record_hermetic.sh
   bash tests/test_entrypoint_cost_report_regression.sh
   bash tests/test_budget_gate.sh
   bash tests/test_verdict_gate_check.sh
   bash tests/test_budget_context.sh
   ```
   Expected: all commands exit 0, including `tests/test_ceiling_revisit.py` and
   `tests/test_fetch_scorecard.py` unchanged from their `main` baseline.
2. Run both workflow DAG checks CI's `dag-check` job runs:
   ```bash
   python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
   python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
   ```
3. Confirm the `feat/issue-342-*` branch only carries the two docs from Task 1. Use the `BASE` SHA
   Task 1 captured at the true fork point, not a live re-read of `origin/main` (Task 7, if it ran,
   fetched `origin/main` again and may have advanced the remote-tracking ref):
   ```bash
   BASE=$(cat /tmp/ceiling-revisit-base-main)
   git diff --name-only "$BASE" HEAD
   ```
   Expected: exactly `docs/superpowers/specs/2026-08-21-dispatch-ceiling-weekly-revisit-design.md`
   and `docs/superpowers/plans/2026-08-28-dispatch-ceiling-weekly-revisit-plan.md` — nothing else.
   (The conditional `.archon/.env` change, if Task 7 ran, lives on the separate
   `chore/ceiling-revisit-${UNTIL}` branch/PR, not here.)
4. No further commit needed if step 3 is clean; if any stray file appears, remove it and commit
   the removal before moving on.
