# Ceiling-revisit hygiene: fix stale L-bucket text and bake in a permanent XL-bucket duplicate/policy guard

**Issue:** #361
**Status:** spec-pending-review

## Overview

The weekly dispatch-ceiling revisit pipeline (`scripts/ceiling_revisit.py` +
`commands/ceiling-revisit.md`) has two stale-text/missing-guard gaps that have each required the
same ad-hoc, never-committed patch for three consecutive weekly cycles (#294, #332, #342):

1. `scripts/ceiling_revisit.py`'s `generate_report()` (report text emitted around line 227) still
   says *"The L=always-above-ceiling rule may be overly conservative"* and *"...in
   `scheduler.sh`"*. Both are wrong: `is_above_ceiling()` has treated **XL**, not L, as the
   unconditionally-above-ceiling bucket since commit `4feef16` (2026-06-21) — confirmed live in
   `scripts/scheduler_lib.sh:44-51`, where `L` falls through to `is_below_ceiling()` — and the
   function now lives in `scripts/scheduler_lib.sh`, not `scheduler.sh`.
2. `commands/ceiling-revisit.md` Phase 4 files an unconditional issue every time the L+XL bucket's
   success rate clears 70% at n≥5, with **no duplicate or policy check**. It carries the same
   stale "L=" / `scheduler.sh` wording in the title/body it files, and has no memory of issue
   #331 — closed 2026-08-22 by an explicit operator policy decision that XL stays
   always-above-ceiling regardless of measured success rate (verified live:
   `stateReason: NOT_PLANNED`). Every cycle since #332 has hand-rolled this exact guard as a
   plan/implement-time overlay against a transient `/tmp` file instead of it living in the command.

Both gaps sit in the same conditional code path (Phase 4's XL-bucket issue filing) across the same
two files, so this ticket fixes both together — splitting them risks two runs editing overlapping
lines in `commands/ceiling-revisit.md`.

## Requirements

### R1 — Fix the stale rule/file wording in `ceiling_revisit.py`'s rendered report

`generate_report()`'s `l_bucket_needs_issue` branch (`scripts/ceiling_revisit.py:225-231`) must
say **XL**, not L, and cite `scripts/scheduler_lib.sh`, not `scheduler.sh`. The `### L-Bucket
Observation` section header and the surrounding "L+XL success rate" prose stay unchanged — the
*measured cohort* genuinely is the merged L+XL bucket (`build_bucket_table` merges L and XL for
reporting; see `test_build_bucket_table_merges_l_and_xl`), it is only the *actionable rule name*
that is XL-specific.

### R2 — Fix the same stale wording in `commands/ceiling-revisit.md` Phase 4's filed issue

Phase 4's `gh issue create --title/--body` (currently `commands/ceiling-revisit.md:129,132-140`)
independently carries the identical stale text (used to author the *next* issue, not the report) —
same rule name, same wrong file. This must be corrected in the same ticket, not deferred:
correcting only the report (R1) while Phase 4 keeps filing "L=always-above-ceiling ... —
scheduler.sh" would leave the source of a fourth ad-hoc patch cycle standing, and — more
concretely — breaks R3's duplicate guard, whose title-pattern search must match what this Phase
itself files.

- New title: `Revisit XL=always-above-ceiling rule in is_above_ceiling() — scheduler_lib.sh`
  (matches #331's live title exactly, the established precedent).
- New body: replace `scheduler.sh` with `scripts/scheduler_lib.sh`; drop the `(~line 213)` line
  reference (stale precision — `is_above_ceiling()` is a named, greppable function; a hardcoded
  line number in static markdown will drift again).
- Change only user-facing text (title string, body prose). Do **not** rename the
  `l_bucket_needs_issue` JSON key (`ceiling_revisit.py:141,291`) or the `L_NEEDS_ISSUE` shell var
  (`commands/ceiling-revisit.md:53-54,123,126`) — that name is a stable contract between the
  script's stderr JSON and the command's parser, and it correctly names the *measured* bucket
  (L+XL), not the rule. Renaming it widens the diff for no requirement in the issue.
- `.archon/commands/ceiling-revisit.md` is a gitignored **runtime copy**, not a second source file
  (confirmed: `git ls-files -- .archon/commands/` is empty; `entrypoint.sh:626-630` copies
  `commands/` into `.archon/commands/` only when the target clone has none of its own). Edit only
  `commands/ceiling-revisit.md`.
- Preserve the `# TARGET-PATH` prefix convention on the two `python3 dark-factory/scripts/...`
  lines (Phase 1) — this is a text/logic fix, not a path fix, per the issue.

### R3 — Permanent duplicate/policy guard in Phase 4, replacing the unconditional filing

Phase 4 currently fires unconditionally whenever `L_NEEDS_ISSUE=True`. Replace this with a guard
that queries the tracker once and branches on the result, so a future cycle never needs to
re-derive this logic as a plan-time overlay again:

```bash
if [ "$L_NEEDS_ISSUE" = "True" ]; then
  # Duplicate/policy guard (#361). "always-above-ceiling" is the stable substring across both
  # the L->XL rename and the scheduler.sh->scheduler_lib.sh split, and is guaranteed to match
  # the title this Phase itself files below — the guard and the filed title can never drift
  # apart as long as both are anchored to this same substring.
  MATCHES=$(gh issue list --repo "$REPO" --state all --limit 500 \
    --json number,title,state,stateReason \
    --jq '[.[] | select(.title | test("always-above-ceiling"; "i"))] | sort_by(-.number)')

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

  if [ "$XL_ACTION" = "file" ]; then
    gh issue create \
      --repo "$REPO" \
      --title "Revisit XL=always-above-ceiling rule in is_above_ceiling() — scheduler_lib.sh" \
      --body "..." \  # corrected body per R2
      --label "enhancement" \
      --label "priority: should-have"
  else
    REASON=$([ "$XL_ACTION" = "skip-policy" ] && echo "closed by operator policy decision" \
                                                || echo "already open, covering this observation")
    gh issue comment "$ISSUE_NUM" --repo "$REPO" --body "XL-bucket success rate cleared the \
>70%-at-n>=5 threshold again this cycle, but issue #${XL_CITE} is ${REASON} — see #${XL_CITE} \
instead of filing a duplicate."
  fi
fi
```

Decision table:

| Newest title-pattern match | Action | Rationale |
|---|---|---|
| Any match is **OPEN** | `skip-duplicate`, cite it | Covers both a live successor issue and a reopened #331 — reopening *is* the reversal signal, no separate comment-text heuristic is needed |
| No open match; newest match is **CLOSED / `NOT_PLANNED`** | `skip-policy`, cite it | Declined by explicit operator policy (verified live: #331, #31, #29 are all `NOT_PLANNED`) — stop re-filing |
| No open match; newest match is **CLOSED / `COMPLETED`** | `file` | A completed cadence issue (#294/#332/#342 are `COMPLETED`) is not a policy decision on the rule — a fresh threshold breach is a genuinely new observation |
| No match at all | `file` | First-ever filing |

`#331` is never hardcoded — it falls out of the table's second row automatically as "the newest
`always-above-ceiling`-titled issue, closed as not-planned." This is what makes the guard
permanent rather than needing a new hand-edit the next time policy changes or a successor issue is
filed.

### R4 — Skip visibility

A silent skip is indistinguishable from a broken guard. Post a standalone `gh issue comment` on
`$ISSUE_NUM` (the *triggering* weekly-revisit issue, already in scope in Phase 4) naming the
action taken and the cited issue, as shown in the R3 snippet. This keeps Phase 4 self-contained
(no reordering relative to Phase 2's already-posted analysis comment).

### R5 — Test coverage

- `tests/test_ceiling_revisit.py::test_generate_report` (lines ~132-136) currently asserts
  `` "`scheduler.sh`" in report `` and `"dark-factory/scheduler.sh" not in report` as a proxy guard
  for the `# TARGET-PATH` no-prefix convention. Update both assertions to the corrected form
  (`` "`scripts/scheduler_lib.sh`" in report ``, `"dark-factory/scripts/scheduler_lib.sh" not in
  report`) and add an assertion that the report contains `"XL=always-above-ceiling"` and does
  **not** contain the stale `"L=always-above-ceiling"` string.
- Add a new test (matching the existing `tests/test_command_issue_number_mandate.py` /
  `test_refine_command_updates.py` convention of static string assertions against
  `commands/*.md` prose — there is no bash-execution test harness for command files in this repo)
  asserting `commands/ceiling-revisit.md`:
  - contains the corrected title string `"Revisit XL=always-above-ceiling rule"` and does not
    contain the stale `"Revisit L=always-above-ceiling rule"`;
  - contains the guard's search anchor `"always-above-ceiling"` used consistently for both the
    `gh issue list` filter and the filed title (a regression test for R3's "guard and filed title
    can never drift apart" property — e.g. assert the same literal substring appears in both the
    `--jq` filter expression and the `--title` string);
  - contains `stateReason` and `NOT_PLANNED` (evidence the policy-branch logic, not just prose, was
    added — a purely-textual "we now check for duplicates" comment without the actual `gh`/`jq`
    branch would not satisfy this).

## Architecture / Approach

Both fixes are surgical edits to existing conditional branches already fully described above (R1
text substitution, R2 text substitution, R3 replacing the current unconditional `if` body with the
guarded version, R4 as part of R3's non-`file` branches, R5 as accompanying test updates). No new
files, scripts, or config keys are introduced. The `gh issue list --state all --json ... --jq ...`
form (client-side filter) is used rather than `gh issue list --search "... in:title"` (server-side)
to avoid GitHub search-index tokenization/lag — the tracker has ~300 issues today, well within a
single `--limit 500` page, so no pagination is needed.

## Alternatives considered

1. **Hardcode issue #331 by number in the guard** (the shape #342's plan actually used, rejected).
   Mirrors the exact overlay logic from `docs/archive/2026-08-28-dispatch-ceiling-weekly-revisit-plan.md`
   Task 5, but baking a specific issue number into a *permanent* command file recreates the same
   maintenance debt this ticket exists to retire — the next policy issue (a genuine "successor" to
   #331) would need another hand-edit to the command file to be recognized.
2. **Fuzzy regex grep over #331's comments for a "reversal" signal** (`revers|re-?open.*polic`,
   also from the #342 overlay, rejected). Unnecessary once reopening is treated as the reversal
   signal (row 1 of R3's table already handles it via `state=="OPEN"`), and actively fragile: it
   would false-positive on any future skip-note comment that itself contains the word "reversed"
   (as #342's own overlay text did).
3. **Use the `above-ceiling` label (`config/config.yaml:68`) as a policy signal** (rejected). That
   label is applied by the scheduler to *dispatch-ceiling-parked tickets*, unrelated to policy
   decisions on the ceiling-revisit meta-issue itself; overloading it would conflate two unrelated
   concepts.
4. **Splice the skip note into the Phase 2 report comment instead of a standalone Phase 4
   comment** (the #342 overlay's approach, rejected). Requires reordering Phase 2 (post) after
   Phase 4 (decide), or deferring the whole comment post to after Phase 4 runs — a larger structural
   change to the command for a cosmetic benefit; a standalone `gh issue comment` keeps Phase 4
   self-contained, matching the issue's "add the guard to Phase 4 directly."
5. **Also correct the second, unrelated `scheduler.sh` string in `ceiling_revisit.py`'s
   `--keywords` argparse help text** (`scripts/ceiling_revisit.py:252`, "default: scheduler.sh
   default") (rejected). Outside the issue's explicitly cited range (`generate_report()`,
   lines 227-231); it is CLI `--help` text, never reaches the rendered report or a filed issue,
   and touching it adds diff surface with no requirement behind it. Left as-is; flagged below.

## Open questions (non-blocking)

- `scripts/ceiling_revisit.py:252`'s `--keywords` help string ("default: scheduler.sh default")
  has the same kind of staleness (the default now lives in `config.yaml`'s `dispatch_ceiling.keywords`
  and this script's own `DEFAULT_KEYWORDS` constant, not literally in `scheduler.sh`) but is out of
  this ticket's explicit scope (Alternative 5). Worth a follow-up hygiene ticket if it keeps
  causing confusion, but not blocking here.

## Assumptions (flagged)

- **[ASSUMPTION]** "Newest" title-pattern match is determined by sorting candidates by issue
  `number` descending (`sort_by(-.number)`), not by `gh issue list`'s own default ordering. Issue
  numbers are monotonically assigned, so this is equivalent to creation order and avoids depending
  on unspecified/undocumented default sort behavior.
- **[ASSUMPTION]** `--limit 500` is sufficient headroom (verified live: this repo currently has
  under 400 issues total) and needs no pagination; revisit if the tracker's issue count approaches
  that ceiling.
