# Dispatch Ceiling (C9) Weekly Revisit — Execution Plan for #332

**Issue:** omniscient/dark-factory#332
**Spec:** `docs/superpowers/specs/2026-08-14-dispatch-ceiling-weekly-revisit-design.md`

## Goal

Execute the third run of the recurring dispatch-ceiling keyword revisit for #332: fetch
cumulative Factory Scorecard data (`SINCE=2026-06-12` → `UNTIL`=implement-time execution date),
apply the existing decision rules, post the analysis as a comment on #332, open a PR against
`.archon/.env` only if a keyword change is warranted, conditionally file an XL-bucket code-change
issue (guarded against duplicating the still-open #331), and unconditionally file the next weekly
revisit issue (target `UNTIL + 7 days`).

## Architecture

**Operational analysis run, not a service change.** No production code, script, or config file
is created or modified by this ticket. `scripts/fetch_scorecard.py` and `scripts/ceiling_revisit.py`
are already implemented and unit-tested (`tests/test_fetch_scorecard.py`,
`tests/test_ceiling_revisit.py`), unmodified since commit `27c890b` (verified via
`git log -1 --format=%H -- scripts/fetch_scorecard.py scripts/ceiling_revisit.py`), and are invoked
exactly as they exist today. The only durable, git-tracked artifacts this ticket produces are the
spec (already committed) and this plan; every other effect (issue comment, possible `.archon/.env`
PR, possible new issues) is a GitHub-side effect produced by literally running
`commands/ceiling-revisit.md`'s five phases with this run's parameters.

**Resolved script paths (per spec Architecture): use the unprefixed paths, not the
`# TARGET-PATH`-marked lines as literally written in `commands/ceiling-revisit.md`.**

```
scripts/fetch_scorecard.py
scripts/ceiling_revisit.py
```

Not `dark-factory/scripts/...`. Verified directly this run (`entrypoint.sh:568-572,694`): the
`dark-factory/`-prefixed path *does* exist and *does* execute in a self-target clone — it's an
image-baked copy of `/opt/dark-factory/scripts` written into `$CLONE_DIR/dark-factory/scripts` at
container bootstrap and appended to `.git/info/exclude` (real, present, git-excluded, not absent).
The reason to prefer the unprefixed path is **drift-safety, not existence**: `scripts/*.py` is the
tracked canonical source a feature branch can modify, while `dark-factory/scripts/*.py` is a frozen
snapshot that (a) never reflects changes made on the current branch and (b) gets wiped outright by
`git clean -fd dark-factory/ .claude/` in the deconflict flow (`entrypoint.sh:694`). This corrects
the rationale on the existing `[PROVISIONAL]` `codebase-patterns.md` entry (line 54, source:implement,
issue:#294), which currently states the prefixed path "does not exist" — that claim is true only of
`git ls-files` (untracked ≠ absent). Re-recording that entry with the corrected rationale is
implement's normal memory-write responsibility for `codebase-patterns.md` (refine does not write to
that file) — but since this is the second confirming cycle for the underlying pattern and the whole
point of raising it here is to stop a third cycle from re-deriving it, Task 9 below makes it an
explicit, committed step rather than leaving it to the implement phase's generic memory step (whose
documented default is "most runs add zero entries").

**`UNTIL` is computed at implement time, not frozen here.** The spec's own Assumptions section is
explicit: if a multi-day gap occurs before implement runs, implement should re-derive `UNTIL` as its
own execution date rather than reusing `2026-08-14` verbatim. That gap has already materialized once
in this ticket's own lifecycle — the spec was committed 2026-08-14 but this plan is being written
2026-08-21, seven days later. Task 3 therefore computes `UNTIL`/`NEXT_DATE` from the actual UTC date
at run time (`date -u +%Y-%m-%d`), per `commands/ceiling-revisit.md`'s own Inputs contract, rather
than hardcoding `2026-08-14`/`2026-08-21`.

**Computed values are persisted across tasks, not just kept in shell variables.** Tasks 2–8 each run
as separate shell invocations, so a plain `SINCE=...`/`UNTIL=...` set in one task's shell does not
exist in the next task's shell. Task 2 and Task 3 append every value they compute to
`/tmp/ceiling-revisit-vars.sh` (`CURRENT_KEYWORDS`, `SINCE`, `UNTIL`, `NEXT_DATE`,
`KEYWORDS_TO_REMOVE`, `L_NEEDS_ISSUE`, `XL_ISSUE_ACTION`, `XL_DUPLICATE_ISSUE`), and every later task
`source`s it as its first step before using any of these values. Task 8 — the one unconditional,
durable deliverable — additionally asserts `UNTIL`/`NEXT_DATE` are non-empty before filing, so a
broken source chain fails loudly instead of filing next week's issue with an empty date.

Because no behavior changes, TDD (red→green→commit) does not apply here — there is no new code path
to pin with a failing test. Each task below states the exact command to run and the *structural*
shape of its expected output (this is a live analysis against real GitHub data, so exact
success-rate numbers cannot be predicted at plan-writing time — the decision rules in
`scripts/ceiling_revisit.py` compute them and are already covered by `tests/test_ceiling_revisit.py`).

**New this cycle — duplicate-detection guard for the L/XL-bucket issue (spec requirement).**
#294's run already found L+XL success at 100%/n=6 and filed #331 ("Revisit XL=always-above-ceiling
rule in `is_above_ceiling()` — `scheduler_lib.sh`"), confirmed still **OPEN** as of this plan
(`gh issue view 331 --json state` → `OPEN`). With another week of likely-still-high data,
`L_NEEDS_ISSUE` will probably evaluate `True` again, but `commands/ceiling-revisit.md` Phase 4 has
no duplicate-detection guard — it calls `gh issue create` unconditionally. Task 4 below adds a
plan/implement-time execution guard (not a change to the shared command file, per spec Alternative
3): before Task 7 (the Phase 4 equivalent) would file a new issue, check for an existing **open**
issue whose title contains `Revisit XL=always-above-ceiling rule`, and if found, skip filing and
instead fold the existing issue's number into the analysis comment that Task 5 posts.

**Carried forward — stale L-bucket report text (established precedent, not new to this ticket).**
`scripts/ceiling_revisit.py`'s `generate_report()` (verified still present:
`### L-Bucket Observation` / `The L=always-above-ceiling rule may be overly conservative` /
`` in `scheduler.sh`. ``) emits stale wording whenever `l_bucket_needs_issue` is `True` — stale on
two counts (XL, not L, is the always-above-ceiling bucket since commit `4feef16`; the file is now
`scheduler_lib.sh`, not `scheduler.sh`). #294's plan (Task 4) corrected the *rendered report text*
in place rather than editing the shared script (out of scope, shared infrastructure). Task 4 below
reapplies the identical correction, since the script is unmodified and the same stale text would
otherwise land in #332's comment if `L_NEEDS_ISSUE` fires again.

**Memory patterns applied** (`.archon/memory/codebase-patterns.md`):
- Issue #42: a refine-phase spec/plan approved on this `refine/issue-332-*` branch does not
  automatically transfer to the `feat/issue-332-*` branch the implement phase creates. Task 1 makes
  the implement agent copy both docs over explicitly before doing anything else.
- Issues #250/#266 (reconciled): Task 10's out-of-scope check captures the `origin/main` commit SHA
  once, in Task 1, *before* any later task (Task 6, if it runs) does its own `git fetch origin main`
  that could advance the remote-tracking ref. Diffing against that frozen SHA (`git diff "$BASE"
  HEAD`) gives correct changed-file-SET detection without the three-dot/two-dot ambiguity either
  entry warns about, because `$BASE` is an immutable commit, not a live symbolic ref.

**Archival note.** Prior cycles' spec/plan pairs ended up under `docs/archive/` (#30's and #294's).
Archiving is not this ticket's job — it is a follow-on housekeeping step conventionally done by a
*later* cycle or a separate pass, not by the run that just produced the artifact (mirrors #294's
plan, which also did not archive itself). No task below performs it.

## Tech Stack

Bash, Python 3 (`scripts/fetch_scorecard.py`, `scripts/ceiling_revisit.py` — both already on
`main`, unmodified), `gh` CLI, `jq`. No new dependencies.

## File Structure

| Path | Purpose |
|---|---|
| `docs/superpowers/specs/2026-08-14-dispatch-ceiling-weekly-revisit-design.md` | Already committed (this ticket's spec) |
| `docs/superpowers/plans/2026-08-21-dispatch-ceiling-weekly-revisit-plan.md` | This plan (committed by the plan phase) |
| `.archon/memory/codebase-patterns.md` | Corrected in place by Task 9 (rewrites the `[PROVISIONAL]` TARGET-PATH entry's rationale; no new entry added) |
| *(GitHub side effects only, below)* | Issue #332 comment; conditional PR touching `.archon/.env` on branch `chore/ceiling-revisit-<UNTIL>`; conditional new XL-bucket issue (skipped, noting #331 or its successor, if a matching open issue already exists); unconditional new weekly-revisit issue |

No other repository file is created, modified, or deleted by this ticket.

---

## Task 1 — Bring the spec and plan onto the implementation branch

**Files:** `docs/superpowers/specs/2026-08-14-dispatch-ceiling-weekly-revisit-design.md`,
`docs/superpowers/plans/2026-08-21-dispatch-ceiling-weekly-revisit-plan.md` (copied, not
re-authored)

1. On the `feat/issue-332-*` branch the implement phase creates, verify both docs exist (they were
   committed on `refine/issue-332-revisit-dispatch-ceiling-----re-measure-`, not automatically
   present on a fresh branch off `main`). The implement phase runs from a **fresh clone**, where the
   refine branch exists only as `refs/remotes/origin/refine/...`, not as a local branch — a bare
   `git show refine/issue-332-...:<path>` does not resolve there, and a `>` redirect on a failed
   `git show` would silently create an **empty** file that then gets committed. Fetch explicitly and
   reference the `origin/` remote-tracking ref, and assert non-emptiness before trusting the copy.
   Also capture the `origin/main` commit this branch was actually cut from, for Task 10's final
   out-of-scope check — Task 6 (if it runs) does its own `git fetch origin main`, which can advance
   the remote-tracking ref further and make a same-invocation `origin/main` diff surface unrelated
   files main picked up independently after this branch forked:
   ```bash
   git rev-parse origin/main > /tmp/ceiling-revisit-base-main
   REFINE_BRANCH="refine/issue-332-revisit-dispatch-ceiling-----re-measure-"
   git fetch origin "$REFINE_BRANCH"
   git show "origin/${REFINE_BRANCH}:docs/superpowers/specs/2026-08-14-dispatch-ceiling-weekly-revisit-design.md" \
     > docs/superpowers/specs/2026-08-14-dispatch-ceiling-weekly-revisit-design.md
   git show "origin/${REFINE_BRANCH}:docs/superpowers/plans/2026-08-21-dispatch-ceiling-weekly-revisit-plan.md" \
     > docs/superpowers/plans/2026-08-21-dispatch-ceiling-weekly-revisit-plan.md
   test -s docs/superpowers/specs/2026-08-14-dispatch-ceiling-weekly-revisit-design.md
   test -s docs/superpowers/plans/2026-08-21-dispatch-ceiling-weekly-revisit-plan.md
   ```
   Expected: both files now exist and are non-empty in the working tree (`git status --short` shows
   them as new/modified on the `feat/` branch; both `test -s` checks exit 0). If either `test -s`
   fails, stop — this ticket's only durable artifact would otherwise be silently lost.
2. Commit:
   ```bash
   git add docs/superpowers/specs/2026-08-14-dispatch-ceiling-weekly-revisit-design.md \
           docs/superpowers/plans/2026-08-21-dispatch-ceiling-weekly-revisit-plan.md
   git commit -m "docs: bring over approved spec/plan for issue #332"
   ```

---

## Task 2 — Pre-flight verification

**Files:** none (read-only verification)

**All variables computed in this task and Task 3 must survive into Tasks 3–8, which each run as
separate shell invocations** — plain shell variables do not persist across them. Every step below
that computes a new value appends it to a single sourceable file, `RUN_VARS=/tmp/ceiling-revisit-vars.sh`;
every later task's first step sources it. Start fresh:
```bash
RUN_VARS=/tmp/ceiling-revisit-vars.sh
rm -f "$RUN_VARS"
```

1. Determine the currently effective `ABOVE_CEILING_KEYWORDS` and capture it as `CURRENT_KEYWORDS`
   for Task 3 and Task 6 to both consume. `.archon/.env` is gitignored and is **not** part of this
   checkout in a real run — the scheduler provisions it straight to `/opt/dark-factory/.archon/.env`
   for compose's `env_file`, so an active override actually shows up as the
   `$ABOVE_CEILING_KEYWORDS` container environment variable, not as a file to grep here. Check, in
   order: the container env var, then a local `.archon/.env` (defensive fallback), then the
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
   (matches spec Assumption — verified this run: no `.archon/.env` exists in this checkout). If an
   override *is* found, `CURRENT_KEYWORDS` carries that value instead — Task 3's `--keywords` flag
   and Task 6's diff both key off this same variable (via `$RUN_VARS`), so there is no drift between
   what was analyzed and what gets modified.
2. Confirm `gh` auth and repo targeting are correct:
   ```bash
   gh auth status
   echo "$FACTORY_REPO_SLUG"
   echo "${FACTORY_PRODUCT_NAME:-<unset>}"
   ```
   Expected: authenticated to `github.com`; `FACTORY_REPO_SLUG` prints `omniscient/dark-factory`;
   `FACTORY_PRODUCT_NAME` prints a product name, not `<unset>`.
3. Confirm the labels Tasks 6–8 use already exist in the repo (`gh issue create`/`gh pr create` fail
   outright on an unknown label):
   ```bash
   for LBL in "priority: should-have" "enhancement" "size: S" "ready-for-agent"; do
     gh label list --repo "$FACTORY_REPO_SLUG" --json name --jq '.[].name' | grep -qxF "$LBL" \
       && echo "OK: $LBL" || { echo "MISSING: $LBL"; exit 1; }
   done
   ```
   Expected: `OK: <label>` for all four (verified this run — all four present). The `exit 1` on any
   `MISSING` result is a hard stop, not advisory — Task 8's `gh issue create` is unconditional and
   would otherwise fail after Tasks 5-7 have already produced their side effects.

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
   `2026-08-14`/`2026-08-21` literals verbatim; per the spec's own Assumptions, those were only
   valid if implement ran same-day as refine, which it will not have (plan alone is already a week
   after the spec). Persist them to `$RUN_VARS` immediately — Tasks 4-8 all need `SINCE`/`UNTIL`,
   and Task 8 needs `NEXT_DATE`:
   ```bash
   SINCE=2026-06-12
   UNTIL=$(date -u +%Y-%m-%d)
   NEXT_DATE=$(date -u -d "${UNTIL} +7 days" +%Y-%m-%d)
   echo "SINCE=$SINCE UNTIL=$UNTIL NEXT_DATE=$NEXT_DATE"
   { echo "SINCE=\"$SINCE\""; echo "UNTIL=\"$UNTIL\""; echo "NEXT_DATE=\"$NEXT_DATE\""; } >> "$RUN_VARS"
   ```
   Expected: `UNTIL` prints today's UTC date (`YYYY-MM-DD`), `NEXT_DATE` is exactly 7 days later.
2. Run the fetch + analysis exactly as `commands/ceiling-revisit.md` Phase 1 specifies, using the
   **unprefixed** script paths (see Architecture) and the dates computed above. Guard
   `CURRENT_KEYWORDS` first — an empty `--keywords ""` is accepted silently by `ceiling_revisit.py`
   and produces a report with zero keyword rows, degraded without any error surfaced:
   ```bash
   test -n "$CURRENT_KEYWORDS" || { echo "FATAL: CURRENT_KEYWORDS not set — check /tmp/ceiling-revisit-vars.sh (Task 2 step 1 must have run)"; exit 1; }

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
   ```
   Note: two explicit flags harden this beyond `commands/ceiling-revisit.md` Phase 1's literal
   text. `--repo "$FACTORY_REPO_SLUG"` — `fetch_scorecard.py` otherwise defaults to
   `$FACTORY_REPO_SLUG` **or `omniscient/markethawk`** if that env var is ever unset, which would
   silently analyze the wrong repo; Task 2 step 2 already confirms `$FACTORY_REPO_SLUG` resolves to
   `omniscient/dark-factory`, so passing it explicitly here removes the ambiguity rather than
   relying on the same fallback. `--keywords "$CURRENT_KEYWORDS"` — `ceiling_revisit.py` otherwise
   falls back to its own `DEFAULT_KEYWORDS` constant, byte-identical today to `config/config.yaml`'s
   default, so this is a no-op in the common case — but it keeps Task 3's analysis and Task 6's diff
   locked to the same value (`CURRENT_KEYWORDS`, captured once in Task 2) even once an
   `.archon/.env` override exists, rather than silently analyzing against a different value than
   whatever the env override changes.

   Expected: `fetch_scorecard.py` prints progress lines to stderr and ends with
   `Wrote /tmp/ceiling-revisit-scorecard.json`; `ceiling_revisit.py` writes
   `/tmp/ceiling-revisit-report.md` containing a `### Per-Bucket Triad` table (rows `S`, `M`,
   `L+XL`) and a `### Per-Keyword Analysis` table; `/tmp/ceiling-revisit-meta.txt` ends with a line
   starting `<!-- CEILING_REVISIT_JSON {"keywords_to_remove": [...], "new_keyword_candidates": [...],
   "l_bucket_needs_issue": <bool>} -->`. `fetch_scorecard.py` computes git-blame churn per merged PR
   across the full ~10-week cumulative window, so this step can take several minutes with only
   intermittent stderr progress lines — expected, not a hang.
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
   Expected: both variables print without error (empty `KEYWORDS_TO_REMOVE` and
   `L_NEEDS_ISSUE=False` is the spec's expected common case for the keyword side — matching every
   prior run in this lineage — but `L_NEEDS_ISSUE=True` is plausible again given #294 measured
   L+XL at 100%/n=6; the live data decides, not this plan). `$RUN_VARS` now carries
   `CURRENT_KEYWORDS`, `SINCE`, `UNTIL`, `NEXT_DATE`, `KEYWORDS_TO_REMOVE`, `L_NEEDS_ISSUE` for
   Tasks 4-8 to source.

No commit — all outputs are transient `/tmp` files, not repo content.

---

## Task 4 — Duplicate-detection guard + stale-text correction (conditional on `L_NEEDS_ISSUE`)

**Files:** none tracked (mutates the transient `/tmp/ceiling-revisit-report.md` only)

This task implements the spec's new requirement: guard the conditional XL-bucket issue filing
against duplicating #331 (still open — verified `gh issue view 331 --json state` → `OPEN` — or
whatever open successor issue exists by the time this runs), and folds the result into the report
before it is posted in Task 5.

0. Source the variables Tasks 2–3 persisted:
   ```bash
   source /tmp/ceiling-revisit-vars.sh
   ```
1. If `L_NEEDS_ISSUE` is `True`:
   ```bash
   if [ "$L_NEEDS_ISSUE" = "True" ]; then
     # (a) Correct the stale "L=always-above-ceiling ... scheduler.sh" text
     # scripts/ceiling_revisit.py's generate_report() still emits (unfixed since #294 — the fix
     # belongs to the shared script, out of this ticket's scope; #294's plan applied this same
     # rendered-text correction rather than editing the script).
     sed -i \
       -e 's/The L=always-above-ceiling rule may be overly conservative\./The XL=always-above-ceiling rule may be overly conservative (L already dispatches autonomously since commit 4feef16)./' \
       -e 's/in `scheduler\.sh`\./in `scripts\/scheduler_lib.sh`./' \
       /tmp/ceiling-revisit-report.md

     # (b) Duplicate-detection: search OPEN issues for an exact title-substring match,
     # client-side (jq regex against a listed page), not gh's fuzzy full-text search — avoids
     # any ambiguity from '=' or punctuation in GitHub's search tokenizer.
     XL_DUPLICATE_ISSUE=$(gh issue list --repo "$FACTORY_REPO_SLUG" --state open --limit 200 \
       --json number,title \
       --jq '.[] | select(.title | test("Revisit XL=always-above-ceiling rule")) | .number' \
       | head -1)

     if [ -n "$XL_DUPLICATE_ISSUE" ]; then
       XL_ISSUE_ACTION="skip"
       # Insert BEFORE the report's own "---" / "*Posted by ... Weekly Ceiling Revisit*" footer
       # (generate_report() always ends with that footer), not appended after it, so the note
       # reads as part of the analysis rather than trailing below the signature line.
       export XL_DUPLICATE_ISSUE
       python3 - <<'PYEOF'
import os
path = "/tmp/ceiling-revisit-report.md"
text = open(path).read()
note = """
### XL-Bucket Issue — Skipped (Duplicate Guard)

L+XL-bucket success cleared the >70%-at-n≥5 threshold again this cycle, but issue #{issue}
("Revisit XL=always-above-ceiling rule in `is_above_ceiling()` — `scheduler_lib.sh`") is already
open covering this observation. No duplicate issue was filed; see #{issue} instead.
"""
note = note.format(issue=os.environ["XL_DUPLICATE_ISSUE"])
marker = "\n---\n"
idx = text.rfind(marker)
if idx == -1:
    text = text + note
else:
    text = text[:idx] + note + text[idx:]
open(path, "w").write(text)
PYEOF
     else
       XL_ISSUE_ACTION="file"
     fi
   else
     XL_ISSUE_ACTION="skip"
     XL_DUPLICATE_ISSUE=""
   fi
   echo "XL_ISSUE_ACTION=$XL_ISSUE_ACTION XL_DUPLICATE_ISSUE=$XL_DUPLICATE_ISSUE"
   { echo "XL_ISSUE_ACTION=\"$XL_ISSUE_ACTION\""; echo "XL_DUPLICATE_ISSUE=\"$XL_DUPLICATE_ISSUE\""; } >> /tmp/ceiling-revisit-vars.sh
   ```
   Expected: if `L_NEEDS_ISSUE=False` (keyword/env side unaffected), this whole block is a no-op
   and `XL_ISSUE_ACTION="skip"` with an empty `XL_DUPLICATE_ISSUE`. If `L_NEEDS_ISSUE=True` and
   #331 (or a successor) is still open, `XL_ISSUE_ACTION="skip"`, `XL_DUPLICATE_ISSUE` holds its
   number, and the report gains the "Skipped (Duplicate Guard)" section above. If `L_NEEDS_ISSUE=True`
   and no matching open issue exists (e.g. #331 was closed in the interim), `XL_ISSUE_ACTION="file"`
   and Task 7 files a fresh issue as before.

No commit — mutates only a transient `/tmp` file.

---

## Task 5 — Phase 2: Post the analysis comment on #332

**Files:** none tracked (GitHub side effect only)

0. Source the variables Tasks 2–4 persisted:
   ```bash
   source /tmp/ceiling-revisit-vars.sh
   ```
1. Post the report generated in Task 3 (corrected/annotated in Task 4) as a comment on issue #332
   (not #30, #294, or #331). Use `--body-file` rather than `--body "$(cat ...)"` — the report is
   multi-KB markdown and `--body-file` avoids shell arg-length limits and quoting/mangling:
   ```bash
   gh issue comment 332 --repo "$FACTORY_REPO_SLUG" --body-file /tmp/ceiling-revisit-report.md
   ```
   Expected: `gh` prints the URL of the newly created comment
   (`https://github.com/omniscient/dark-factory/issues/332#issuecomment-...`).

No commit — no repository file changes.

---

## Task 6 — Phase 3: Open a PR to `.archon/.env` (conditional on `KEYWORDS_TO_REMOVE`)

**Files:** `.archon/.env` (new or modified, on a separate branch `chore/ceiling-revisit-${UNTIL}`
— not on `feat/issue-332-*`)

Only run this task if `KEYWORDS_TO_REMOVE` (from Task 3) is non-empty.

```bash
source /tmp/ceiling-revisit-vars.sh   # SINCE, UNTIL, CURRENT_KEYWORDS, KEYWORDS_TO_REMOVE

if [ -n "$KEYWORDS_TO_REMOVE" ]; then
  ENV_FILE=".archon/.env"
  ENV_BACKUP="/tmp/ceiling-revisit-env-backup-${UNTIL}"

  # CURRENT_KEYWORDS was already captured in Task 2 step 1 — reuse it rather than re-deriving,
  # so this diff can never drift from what Task 3 actually analyzed.
  NEW_KWS="$CURRENT_KEYWORDS"
  for KW in $(echo "$KEYWORDS_TO_REMOVE" | tr '|' '\n'); do
    NEW_KWS=$(echo "$NEW_KWS" | sed "s/|${KW}//g;s/${KW}|//g;s/^${KW}$//g")
  done

  # .archon/.env is gitignored/untracked (.gitignore:41) — `git checkout` does NOT save or
  # restore untracked files across a branch switch. Since this task works in the same working
  # directory (not a separate worktree), back the real file up ourselves and restore it after,
  # so a real deployment's other secret lines survive this task intact.
  if [ -f "$ENV_FILE" ]; then
    cp "$ENV_FILE" "$ENV_BACKUP"
  else
    ENV_BACKUP=""
  fi

  PR_BRANCH="chore/ceiling-revisit-${UNTIL}"
  # Cut from origin/main, NOT from feat/issue-332-* — Task 1 already committed the spec+plan
  # docs onto feat/issue-332-*, and those aren't on main yet. Branching from feat/ here would
  # drag both docs into this PR's diff.
  git fetch origin main
  git checkout -b "$PR_BRANCH" origin/main

  # Secrets guard: .archon/.env is a general gitignored local-secrets file, not a file
  # dedicated to this one variable. Do NOT git add -f whatever happens to be on disk — a real
  # deployment may have accumulated unrelated secret lines since #30's/#294's runs, and staging
  # the whole file would leak them into a public PR. Overwrite the working copy on THIS branch
  # with a minimal file containing only the one line this ticket is authorized to change.
  printf 'ABOVE_CEILING_KEYWORDS=%s\n' "$NEW_KWS" > "$ENV_FILE"

  # -f required: .archon/.env is gitignored — this is the one deliberate, intentional commit
  # of that file this command produces, and it is provably single-line per the guard above.
  git add -f "$ENV_FILE"
  git commit -m "chore(env): update ABOVE_CEILING_KEYWORDS per weekly revisit (#332)

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
    --body "Recommended by weekly dispatch ceiling analysis on issue #332.

Removes: \`${KEYWORDS_TO_REMOVE}\`

See the analysis comment on #332 for full data and decision rationale.

Related to #332 (not auto-closing — #332's own deliverable is the spec/plan landing via the
implement PR plus the analysis comment; this env change is reviewed and merged independently).

Note: this diff contains only the \`ABOVE_CEILING_KEYWORDS\` line. \`.archon/.env\` is a
gitignored local-secrets file for the self-target instance — this PR intentionally does not
carry any other content that may exist in a real deployment's copy." \
    --label "priority: should-have" \
    --base main

  git checkout -   # return to feat/issue-332-* for the remaining tasks

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
`.archon/.env` diff against `main`, containing exactly one line (`ABOVE_CEILING_KEYWORDS=...`);
`git status` on `feat/issue-332-*` after `git checkout -` shows no pending changes from this block;
the real `.archon/.env` (if one existed before this task ran) is byte-identical to its pre-task
state afterward.

No commit on `feat/issue-332-*` — the commit above belongs to the separate `chore/` branch.

---

## Task 7 — Phase 4: File the XL-bucket code-change issue (conditional on `XL_ISSUE_ACTION=file`)

**Files:** none tracked (GitHub side effect only)

Only run this task if `XL_ISSUE_ACTION` (from Task 4) is `file` — i.e. `L_NEEDS_ISSUE=True` **and**
no matching open issue already exists. If `XL_ISSUE_ACTION=skip`, this task no-ops (Task 4 already
folded the reason — no observation, or an existing duplicate — into the posted comment).

```bash
source /tmp/ceiling-revisit-vars.sh   # SINCE, UNTIL, XL_ISSUE_ACTION

if [ "$XL_ISSUE_ACTION" = "file" ]; then
  gh issue create \
    --repo "$FACTORY_REPO_SLUG" \
    --title "Revisit XL=always-above-ceiling rule in is_above_ceiling() — scheduler_lib.sh" \
    --body "## Purpose

The weekly dispatch ceiling analysis (issue #332, window ${SINCE}→${UNTIL})
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
- Prior instances of this same finding: #29, #31, #331 (verify each is still relevant before
  reusing its conclusions — #331 in particular may already cover this cycle's observation; this
  issue is only filed when the duplicate-detection guard in Task 4 found no open match).

## References

- Triggering analysis: issue #332
- Policy: docs/superpowers/specs/2026-08-14-dispatch-ceiling-weekly-revisit-design.md

---
*Filed automatically by weekly ceiling revisit*" \
    --label "enhancement" \
    --label "priority: should-have"
fi
```
Expected (only if the branch runs): `gh issue create` prints the new issue's URL. If
`XL_ISSUE_ACTION=skip`, no output — this task performs no action.

No commit — no repository file changes.

---

## Task 8 — Phase 5: File the next weekly revisit issue (unconditional)

**Files:** none tracked (GitHub side effect only)

```bash
source /tmp/ceiling-revisit-vars.sh   # SINCE, UNTIL, NEXT_DATE

# This task is unconditional and its output (a filed GitHub issue) is durable and seeds the
# next run in this lineage — assert the sourced dates are actually present before filing rather
# than letting `gh issue create` post an issue with a literal empty string for UNTIL/NEXT_DATE.
test -n "$UNTIL" && test -n "$NEXT_DATE" || { echo "FATAL: UNTIL/NEXT_DATE not set — check /tmp/ceiling-revisit-vars.sh (Task 3 step 1 must have run)"; exit 1; }

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

- Policy: docs/superpowers/specs/2026-08-14-dispatch-ceiling-weekly-revisit-design.md
- Archon command: \`commands/ceiling-revisit.md\`
- Prior revisit: #332 (comment with results)

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

# Board the new issue (operator amendment, 2026-08-21). `commands/ceiling-revisit.md` Phase 5
# labels the next revisit `ready-for-agent` but never adds it to project board 2, so the
# scheduler — which only sees board items — cannot dispatch it. #332 itself sat invisible for a
# week for exactly this reason until an operator boarded it by hand. Fail-soft: if the factory
# token lacks `project` scope in the container, warn loudly and let the operator board it.
ITEM_ID=$(gh project item-add 2 --owner omniscient --url "$NEW_URL" --format json --jq .id 2>/dev/null || true)
if [ -n "$ITEM_ID" ] && gh project item-edit --id "$ITEM_ID" --project-id PVT_kwHOAAFds84BcpWz \
     --field-id PVTSSF_lAHOAAFds84BcpWzzhXQl6I --single-select-option-id d877a5b3 >/dev/null 2>&1; then
  echo "boarded $NEW_URL as Backlog (item $ITEM_ID)"
else
  echo "WARN: could not add $NEW_URL to project 2 / set Backlog — operator must board it manually"
fi
```
Expected: `filed: <URL>` followed by `boarded <URL> as Backlog (...)` — or the `WARN:` line, which is
not a failure (the issue exists; only its board membership is missing). Uses `$UNTIL`/`$NEXT_DATE` as computed in
Task 3 (today's execution date + 7 days), not the stale `2026-08-14`/`2026-08-21` literals this
spec was written against — same "compute at run time" rule applied consistently across every phase
in this plan.

No commit — no repository file changes.

---

## Task 9 — Re-record the TARGET-PATH memory entry with the corrected rationale

**Files:** `.archon/memory/codebase-patterns.md`

The spec's Architecture section requires correcting the existing `[PROVISIONAL]` entry (line 54)
that claims the `dark-factory/`-prefixed path "does not exist as tracked content" — that claim is
true only of `git ls-files` (untracked ≠ absent; see this plan's Architecture section for the full
`entrypoint.sh:568-572,694` verification). This is the second confirming cycle for the underlying
"prefer unprefixed `scripts/...` paths" pattern (first: #294), so the entry is corrected in place
here rather than left to the implement phase's generic, easy-to-skip memory step.

1. Read the current entry to confirm line 54 is still the exact text being replaced (memory files
   can drift between plan-writing and implement-time execution):
   ```bash
   grep -n "TARGET-PATH" .archon/memory/codebase-patterns.md
   ```
   Expected: line 54 (or wherever it now lives, if the file has grown/shrunk) contains the
   `[PROVISIONAL]` entry with `issue:#294` and the `"does not exist as tracked content"` phrase. If
   the phrase is no longer present verbatim (e.g. a later cycle already corrected it), skip this
   task's edit step — the correction has already landed — and note that in the commit message
   instead of duplicating it.
2. Replace the entry's existence-based rationale with the drift-safety rationale, keeping the
   `[PROVISIONAL]` tag and its evidence/issue/date/expires/source trailer format (promotion to
   `[PATTERN]` on a second-run confirmation is the memory system's own job, not this task's — this
   task only fixes the rationale, it does not re-tag):
   ```bash
   python3 - <<'PYEOF'
path = ".archon/memory/codebase-patterns.md"
text = open(path).read()
old = (
    "- [PROVISIONAL] `commands/*.md` phase files mark vendor-relative paths with a trailing "
    "`# TARGET-PATH` comment (e.g. `commands/ceiling-revisit.md`'s "
    "`python3 dark-factory/scripts/fetch_scorecard.py`) — the `dark-factory/` prefix is for the "
    "MarketHawk instance, which vendors this factory's scripts under a `dark-factory/` "
    "subdirectory; for this self-target repo the correct resolved path has no prefix "
    "(`scripts/fetch_scorecard.py`). A plan that copies a `# TARGET-PATH`-marked command block "
    "verbatim without stripping the prefix produces a path that does not exist as tracked content "
    "in a fresh self-target clone (verified: `git ls-files` has 0 entries under `dark-factory/` in "
    "this repo; the real scripts live at `scripts/`). Strip the `dark-factory/` prefix when "
    "executing a `# TARGET-PATH` line against this repo. "
    "<!-- evidence:test-output issue:#294 date:2026-07-31 expires:2027-01-31 source:implement -->"
)
new = (
    "- [PROVISIONAL] `commands/*.md` phase files mark vendor-relative paths with a trailing "
    "`# TARGET-PATH` comment (e.g. `commands/ceiling-revisit.md`'s "
    "`python3 dark-factory/scripts/fetch_scorecard.py`) — the `dark-factory/` prefix is for the "
    "MarketHawk instance, which vendors this factory's scripts under a `dark-factory/` "
    "subdirectory; for this self-target repo the correct resolved path has no prefix "
    "(`scripts/fetch_scorecard.py`). The prefixed path is NOT absent in a self-target clone — "
    "`entrypoint.sh` (lines 568-572) copies the image-baked `/opt/dark-factory/scripts` into "
    "`$CLONE_DIR/dark-factory/scripts` at container bootstrap and excludes it via "
    "`.git/info/exclude`, so it exists on disk and executes; `git ls-files` returning 0 entries "
    "under `dark-factory/` only proves untracked, not absent. The correct reason to strip the "
    "prefix is drift-safety, not existence: `scripts/*.py` is the tracked canonical source a "
    "feature branch can modify, while `dark-factory/scripts/*.py` is a frozen snapshot that never "
    "reflects branch changes and is wiped outright by `git clean -fd dark-factory/ .claude/` in "
    "the deconflict flow (`entrypoint.sh:694`). Strip the `dark-factory/` prefix when executing a "
    "`# TARGET-PATH` line against this repo. "
    "<!-- evidence:test-output issue:#332 date:2026-08-21 expires:2027-02-21 source:implement -->"
)
if old in text:
    text = text.replace(old, new)
    open(path, "w").write(text)
    print("REPLACED")
else:
    print("SKIPPED: exact old text not found — verify manually before committing")
PYEOF
   ```
   Expected: prints `REPLACED`. If it prints `SKIPPED`, stop and manually diff the entry against
   the `old` block above before proceeding — do not force the replacement blind.
3. Commit:
   ```bash
   git add .archon/memory/codebase-patterns.md
   git commit -m "memory: correct TARGET-PATH rationale to drift-safety, not existence (#332)

Second confirming cycle (first: #294) for 'prefer unprefixed scripts/... paths' — the prior
entry's existence-based rationale was incomplete (dark-factory/ is untracked, not absent;
entrypoint.sh copies and executes it in every self-target clone)."
   ```

---

## Task 10 — Final verification

**Files:** none (verification only)

1. Run the full test suite exactly as `.github/workflows/ci.yml`'s `tests` job does — expect zero
   regressions, since no script/config under test was modified. `PYTHONPATH=scripts` is required
   (CI sets it via `env:`); CI runs the individual `tests/test_*.sh` scripts, not the root
   `smoke_gate.sh` directly:
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
   `tests/test_fetch_scorecard.py` (covered by the `pytest tests/ -v` run) unchanged from their
   `main` baseline.
2. Run both workflow DAG checks CI's `dag-check` job runs:
   ```bash
   python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
   python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
   ```
3. Confirm the `feat/issue-332-*` branch only carries the two docs from Task 1 plus the memory
   correction from Task 9. Use the `BASE` SHA Task 1 captured at the true fork point, not a live
   re-read of `origin/main` — Task 6 (if it ran) fetched `origin/main` again and may have advanced
   the remote-tracking ref past this branch's actual fork point:
   ```bash
   BASE=$(cat /tmp/ceiling-revisit-base-main)
   git diff --name-only "$BASE" HEAD
   ```
   Expected: exactly
   `docs/superpowers/specs/2026-08-14-dispatch-ceiling-weekly-revisit-design.md`,
   `docs/superpowers/plans/2026-08-21-dispatch-ceiling-weekly-revisit-plan.md`, and
   `.archon/memory/codebase-patterns.md` — nothing else. (The conditional `.archon/.env` change, if
   Task 6 ran, lives on the separate `chore/ceiling-revisit-${UNTIL}` branch/PR, not here.)
4. No further commit needed if step 3 is clean; if any stray file appears, remove it and commit
   the removal before moving on.
