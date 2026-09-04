# Dispatch Ceiling (C9) Weekly Revisit — Analysis Run for #359

**Issue:** #359

**Status:** design spec for a *recurring analysis run* (not a one-off feature build) — no code changes
**Policy origin:** #107 (closed, implemented the size/type-aware ceiling), depends on the Factory
Scorecard (#99, closed, implemented)
**Lineage:** #107 → #112/#119/#32 (duplicate weekly-revisit filings, closed) → #30 (canonical,
spec+plan landed 2026-07-17, executed) → #294 (second execution) → #332 (third execution) → #342
(fourth execution — spec landed 2026-08-21, plan landed 2026-08-28, implement ran 2026-08-28,
merged via PR #365) → **#359** (this ticket — filed automatically by #342's own implement run, per
`commands/ceiling-revisit.md` Phase 5, targeting 2026-09-04).

## Overview / Problem Statement

#107 added a size/type-aware dispatch ceiling to the scheduler: S tickets dispatch freely, M
tickets dispatch but lose the grace-window auto-advance (plus park in Blocked if the title matches
`ABOVE_CEILING_KEYWORDS`), L tickets dispatch autonomously since commit `4feef16` (2026-06-21), and
only XL tickets always park in Blocked for human pairing (`is_above_ceiling()` in
`scripts/scheduler_lib.sh`). The keyword list
(`migration|migrate|performance|perf|architectur|refactor`, `config/config.yaml:69`) is a starting
heuristic, revisited weekly as the Factory Scorecard (#99) accumulates per-bucket success data.

That revisit is a live, working, recurring process: `commands/ceiling-revisit.md` fetches
Scorecard data via `scripts/fetch_scorecard.py`, applies the decision rules in
`scripts/ceiling_revisit.py`, posts an analysis comment, opens a PR against `.archon/.env` only if
a keyword change is warranted, conditionally files an XL-bucket code-change issue (now
duplicate/policy-guarded — see below), and unconditionally files the next weekly revisit issue.
Both scripts are unmodified since commit `27c890b` (predates #294) and are unit-tested
(`tests/test_ceiling_revisit.py`, `tests/test_fetch_scorecard.py`).

**This is the fifth execution of that recurring process.** Unlike #342 — which was filed and
refined *the same day* its own predecessor (#332) posted an analysis of, functionally, the
identical window, and which needed an ad-hoc same-window duplicate guard to avoid re-measuring it
— #359 was filed by #342's implement run and then **deliberately held**: an operator comment
(2026-08-28) removed the `ready-for-agent` label specifically because, with no dispatch-time
cadence gate (#360, still open) in place, this issue "would otherwise be refined/planned/
implemented within minutes of #342 closing and re-measure the identical window." The operator
re-added `ready-for-agent` on 2026-09-04 (today), which is why this refine run is happening now,
one full week after #342's window closed. See Requirements for why the same-window duplicate
guard is still carried forward as a standing requirement despite this — the one-week gap is a
manual, discretionary act, not a structural fix.

## Corrections to the Issue Body (ground-truth reconciliation)

| Issue #359 says | Verified reality |
|---|---|
| `UNTIL` = 2026-09-04 | Matches today's date and the established rule ("`UNTIL` = the UTC date the implement phase actually runs," carried forward since #294/#332/#342). No correction needed **if** plan/implement run today; if either runs on a later date, `UNTIL` must be re-derived as that actual date, exactly as #342's plan had to do when a 7-day spec→plan gap materialized. |
| `NEXT_DATE` = `<UNTIL + 7 days>` | Resolves to **2026-09-11** if `UNTIL` stays 2026-09-04; re-derive as `UNTIL + 7` if `UNTIL` is corrected per the row above. |
| Spec reference: `docs/superpowers/specs/2026-08-21-dispatch-ceiling-weekly-revisit-design.md` | That spec has since been archived to `docs/archive/2026-08-21-dispatch-ceiling-weekly-revisit-design.md` (confirmed on `main`, part of #342's own PR #365). This spec (`2026-09-04-...`) supersedes it as the current living reference. |
| "Prior revisit: #342 (comment with results)" | Correct. #342's analysis comment (posted 2026-08-28, window `2026-06-12 → 2026-08-28`): S 100.0% (n=7), M 100.0% (n=24, M baseline 100.0%), L+XL 100.0% (n=6); no keyword changes warranted; XL-bucket issue filing skipped per the #331 operator policy decision. #342 itself was later re-verified by the operator in a follow-up comment after an orphaned-run recovery — that comment cites different raw counts (S 66.7% n=3, M 100% n=16) for what it describes as the same window. This spec does not attempt to reconcile that discrepancy: it is second-hand paraphrase in an operator note, not the authoritative posted analysis comment, and #359's own implement run will pull a fresh, live Scorecard snapshot regardless — nothing about this cycle's procedure depends on #342's exact historical numbers. Flagged as a non-blocking Open Question below in case a future cycle needs to audit it. |

Everything else in the issue body (`SINCE=2026-06-12`, the four-step review procedure, the
`ABOVE_CEILING_KEYWORDS`-via-PR recommendation) matches the live, canonical tooling exactly.

## Requirements

Standing requirements, carried forward from #30/#294/#332/#342 (unchanged):

- Fetch cumulative Scorecard data for the full policy window: `SINCE=2026-06-12` (fixed) through
  `UNTIL` (the implement agent's actual execution date), via `scripts/fetch_scorecard.py`.
- Apply the existing decision rules unchanged (`scripts/ceiling_revisit.py`): per keyword, M-size
  cohort with `n≥5` — remove if success rate ≥ M baseline, keep if rate < M baseline − 15pts,
  otherwise "insufficient data — no change." L+XL bucket: file a code-change issue if success rate
  > 70% at `n≥5`.
- Post the per-bucket triad table and per-keyword analysis as a comment on **this issue (#359)**.
- Open a PR updating `ABOVE_CEILING_KEYWORDS` in `.archon/.env` only if the decision rules actually
  warrant a change. No `.archon/.env` currently exists in this checkout — the common-case outcome
  (insufficient/matching data, per every run to date including #342's) means no PR is expected.
- File the next weekly revisit issue unconditionally, with corrected parameters
  (`ISSUE_NUM=359 SINCE=2026-06-12 UNTIL=<implement's actual run date> NEXT_DATE=UNTIL+7`).

**Already resolved since #342 — no overlay needed this cycle:**

- **XL-bucket duplicate/policy guard.** #342's plan had to hand-implement an "open-match-or-
  closed-policy" check as an execution-time overlay (per the #331 operator amendment). #361 (closed,
  merged via PR #392) baked this permanently into `commands/ceiling-revisit.md` Phase 4 — confirmed
  live on `main`: it queries `gh issue list --search "always-above-ceiling"`, branches on
  open/closed+`NOT_PLANNED`/lookup-failure, and fails closed on a broken tracker lookup. Implement
  should rely on the command's built-in guard as-is; do not re-derive a plan-level version.
- **Stale L-bucket/`scheduler.sh` report text.** #361 also fixed the source text in both
  `scripts/ceiling_revisit.py:227-229` and `commands/ceiling-revisit.md` — confirmed live on `main`:
  both now correctly read "XL=always-above-ceiling rule" and `` `scripts/scheduler_lib.sh` ``. No
  runtime text-correction overlay is needed this cycle (#342's plan needed one; this one does not).

**New / still-needed this cycle:**

- **Same-window duplicate guard (carried forward from #342, 5th application, broadened).** Before
  running Phase 1 (fetch/analyze), implement must check whether an analysis comment with this
  cycle's *exact* `SINCE`+`UNTIL` window already exists — on **both** #342 (the prior-revisit issue,
  catching a lineage collision) **and #359 itself** (catching a retry/re-dispatch of this same
  ticket after a partial prior failure). Use the established header-match convention verbatim:
  scan each issue's comments for lines matching
  `^## Dispatch Ceiling Weekly Revisit — [0-9]{4}-[0-9]{2}-[0-9]{2} → [0-9]{4}-[0-9]{2}-[0-9]{2}$`
  (two-date shape — this excludes the dateless `## Dispatch Ceiling Weekly Revisit — Same-Window
  Restatement` header a prior skip-path run may have posted, which matches the bare prefix but
  carries no dates and would otherwise null out the match), take the last (`tail -1`) match per
  issue, and extract the trailing `UNTIL` date. `SAME_WINDOW=true` if *either* issue's extracted
  window equals this cycle's own `SINCE`/`UNTIL`.
  - **Broadening rationale (self-check on #359):** unlike every prior cycle in this lineage, #359's
    cross-issue collision risk (vs. #342) is now low — the operator's deliberate one-week hold makes
    it unlikely #342's window still matches. The live risk this cycle is same-issue: #359 itself can
    be re-dispatched after a partial failure (e.g. a crashed run, mirroring the orphaned-run
    recovery #342 itself needed), and a prior-issue-only guard would not catch a second live analysis
    landing on #359. Checking #359 too is the same query shape, reusing the same regex, at the cost
    of one extra `gh issue view`.
  - **Expected outcome this cycle (see Assumptions):** the guard is expected **not** to fire — #342's
    window ended 2026-08-28, a full week before this cycle's `UNTIL` (2026-09-04 or later), and #359
    itself has no prior analysis comment. Implement should expect to run the full standing procedure
    (a real Scorecard fetch), not the skip/restatement path — this is a change from #342's own
    Assumptions, which (incorrectly, per its own later plan) expected the guard to fire.
  - If `SAME_WINDOW=true` anyway (e.g. implement retries same-day after a crash): skip Phase 1 and
    the normal Phase 2 comment; post a short, self-contained restatement comment instead (header
    `## Dispatch Ceiling Weekly Revisit — Same-Window Restatement`, pointing at whichever matched
    issue plus its headline results restated inline). Phases 3-4 become no-ops. Still run Phase 5
    unconditionally.
- **Do not re-file either of #342's two follow-up tickets.** Both are already tracked:
  `#360` ("Add a dispatch-time cadence gate for the weekly ceiling-revisit issue lineage") is still
  **OPEN**, unactioned, no comments — the duplicate-guard convention this lineage already uses for
  the XL-bucket issue (skip if an open match exists) applies here by the same logic; do not file a
  second cadence-gate ticket. `#361` ("Ceiling-revisit hygiene...") is **CLOSED**, shipped — nothing
  to re-file.
- **Post one informational tracking comment on #360** (conditional, non-fatal, duplicate-guarded):
  this is the guard's fifth consecutive cycle needing re-derivation as a plan/implement-time overlay
  (#342 was the fourth) — the same signal that led #294/#332's ad-hoc XL-bucket overlay to get
  permanently baked into `commands/ceiling-revisit.md` by #361. Record that recurrence on #360 so a
  human reviewing the cadence-gate ticket sees the accumulating cost of *not* having a real gate, and
  the cheaper alternative (baking the same-window guard into the command, mirroring #361's
  precedent). Conditions, so this stays proportionate to a `size: S` ticket:
  1. Only comment if #360 is still `OPEN` at implement time (re-check via `gh issue view 360 --json
     state`); if closed, skip and fold the observation into #359's own analysis comment instead.
  2. Duplicate-guarded: skip if #360 already carries a tracking comment citing this run's `UNTIL`.
  3. Non-fatal: a failed `gh issue comment` on #360 must not fail the phase or block Phases 2-5 —
     log and continue. #359's own analysis comment is the primary deliverable.
  - This is an informational side effect (records a signal on an already-open, already-tracked
    ticket), not a code/config change — it does not need its own reviewed ticket, unlike a source
    edit to `commands/ceiling-revisit.md` or `scheduler.sh` would.

## Architecture / Approach

No code changes. The implement agent invokes the existing, unmodified `commands/ceiling-revisit.md`
with:

```
ISSUE_NUM=359 SINCE=2026-06-12 UNTIL=2026-09-04 NEXT_DATE=2026-09-11
```

(re-deriving `UNTIL`/`NEXT_DATE` from its own actual execution date if that differs from this
refine pass's date). Its five phases already implement every standing requirement above (including,
as of #361, the XL-bucket duplicate/policy guard and correct report text). This cycle's two
additions — the broadened same-window duplicate guard (now checking #359 in addition to #342) and
the conditional #360 tracking comment — are execution-time overlays on top of the unmodified
command, exactly as #342's spec added its guard without editing `commands/ceiling-revisit.md`
itself.

Use the unprefixed `scripts/...` paths (`scripts/fetch_scorecard.py`, `scripts/ceiling_revisit.py`)
when executing this command's `# TARGET-PATH`-marked lines against this self-target repo — the
`dark-factory/` prefix in the command file is for the MarketHawk instance
(`.archon/memory/codebase-patterns.md`, corrected 2026-08-21 per #332; re-confirmed unchanged this
cycle).

**Known plan-level execution detail (established precedent, not new to this ticket):**
`.archon/.env` is gitignored (`.gitignore:41`). If a keyword removal is warranted (not expected
this cycle, matching every prior run), Phase 3's `git add "$ENV_FILE"` must run as `git add -f
"$ENV_FILE"` on a `chore/ceiling-revisit-<date>` branch cut from `origin/main`, staging only the
`ABOVE_CEILING_KEYWORDS` line — never the whole gitignored file.

## Alternatives Considered

1. **Proceed with `UNTIL=2026-09-04` and skip the same-window duplicate guard entirely this cycle,
   on the grounds that the operator's one-week hold already makes a collision very unlikely.**
   Rejected (per product-owner brainstorming) — the one-week gap is a discretionary, one-time
   operator act, not a structural fix (`#360` cadence gate remains open/unactioned); encoding "the
   operator held it back last time" as "the system is now safe" is exactly the inversion this
   lineage has been burned by twice already (#332→#342). The guard costs one cheap API call when it
   doesn't fire and prevents a ~$11-class duplicate analysis when it would.
2. **Check only the prior-revisit issue (#342), matching every previous cycle's literal
   implementation.** Rejected in favor of also checking #359 itself — the cross-issue collision this
   guard originally targeted (accidental same-day #332→#342 re-dispatch) is now the *less* likely
   risk given the operator's deliberate hold, while a same-issue risk (a retry/re-dispatch of #359
   after a partial failure, e.g. mirroring the orphaned-run crash #342 itself hit) is not covered by
   a prior-issue-only check. Same query shape, one extra API call, strictly broader coverage.
3. **File a new ticket for "bake the same-window guard into `commands/ceiling-revisit.md`
   permanently," mirroring how #361 baked the XL-bucket guard.** Rejected — `#360` is already the
   canonical open ticket for "no real cadence gate exists"; a command-level same-window guard and a
   scheduler-level cadence gate are two candidate fixes for the same underlying gap (this cycle's
   own procedure never fires prematurely), and #360 is the right place to weigh them, not a third
   overlapping ticket. Recorded as a tracking comment on #360 instead (see Requirements).
4. **Skip the #360 tracking comment as out-of-scope for a `size: S`, env/analysis-only ticket.**
   Rejected — per product-owner brainstorming, this lineage has already mandated GitHub side effects
   well beyond `commands/ceiling-revisit.md`'s built-ins (#342's plan required filing two brand-new
   tickets, #360 and #361, as an unconditional step). The scope line this lineage actually draws is
   "edits code/config" (needs its own reviewed ticket) versus "records a signal on the tracker"
   (fine to mandate directly) — a conditional, duplicate-guarded, non-fatal comment on an
   already-open ticket is on the mandate side of that line.
5. **Fix the `config/config.yaml:73` stale `# Revisit: 2026-09-12` comment as part of this ticket,
   since it's been flagged twice already (#294, #342 specs).** Rejected — still a genuine code/config
   edit, out of scope for this ticket's env/analysis-only, `size: S` boundary. Left as a non-blocking
   Open Question (backlog grooming), same disposition as the last two cycles.

## Open Questions (Non-blocking)

- **Is the broadened (self-check) same-window guard the right long-term shape, or just another
  stopgap?** This is the guard's fifth consecutive cycle needing re-derivation as a plan/implement
  overlay (fourth was #342). If a sixth cycle needs yet another ad-hoc adjustment, that is the
  signal — per #342's own spec, which already predicted this — that #360 (or a command-level
  permanent guard, per this spec's tracking comment on #360) needs to actually ship, not another
  overlay.
- **`config/config.yaml:73` cadence comment is still stale.** Still reads `# Revisit: 2026-09-12`, a
  leftover from the pre-weekly (quarterly) cadence era, flagged by #294's and #342's specs, still
  unfixed. Doc-only inconsistency, worth fixing during backlog grooming.
- **The operator's post-#342 re-verification comment cites different raw counts than #342's own
  posted analysis comment** for what it describes as the same window (see Corrections table above).
  Not investigated further here — second-hand paraphrase, not the authoritative artifact, and does
  not affect this cycle's procedure (which pulls its own live Scorecard snapshot). Worth a quick
  audit by a human if the discrepancy recurs in a future operator note.
- **#360 remains open and unactioned** as of this refine run. This spec recommends a tracking
  comment (see Requirements) rather than any code action — building the actual cadence gate is
  #360's job, not this ticket's.

## Assumptions

- No `.archon/.env` currently exists in this checkout (confirmed) — the first keyword override, if
  any, creates it fresh via the plan's `git add -f` path.
- Current effective `ABOVE_CEILING_KEYWORDS` is the `config/config.yaml` default
  (`migration|migrate|performance|perf|architectur|refactor`); no env override is active.
- **The same-window duplicate guard is expected NOT to fire this cycle** — #342's window
  (`SINCE=2026-06-12 → UNTIL=2026-08-28`) ended a full week before this cycle's `UNTIL`
  (2026-09-04 or later, per the operator's deliberate hold), and #359 has no prior analysis comment
  of its own. A reader should expect #359's implement output to be a fresh, full Scorecard-based
  analysis comment, not a skip/restatement — this is the opposite of #342's own (later-corrected)
  Assumptions, and is the correct, intended outcome here, not a bug.
- If the guard fires anyway (e.g. a same-day retry after a crash), implement should follow the
  skip/restatement path exactly as specified, and still file the next revisit issue unconditionally.
- #360 remains open through this cycle's implement run; if a human closes it in the interim, the
  tracking-comment step is skipped per its own condition (see Requirements) with no other effect on
  this ticket's procedure.

## Brainstorming Q&A

> **Q1:** Given #342's own window ended 2026-08-28 — a full week before today (2026-09-04) — the
> same-window duplicate guard is essentially certain not to fire this cycle. Should #359's spec
> still carry the same-window duplicate guard forward as a standing, must-implement requirement (as
> #342's spec did, even though its own author predicted it wouldn't fire that cycle either due to a
> plan-writing delay), or is it now safe to drop the guard from this cycle's spec entirely, given
> the operator's deliberate one-week hold makes a same-window collision far less likely than the
> accidental same-day #332→#342 collision that originally motivated it?
>
> **A1:** Keep it — carry the guard forward as a standing requirement. The one-week gap exists only
> because of a manual, discretionary operator hold, not a structural fix (#360, the real
> cadence-gate fix, is still open/unactioned); a spec that drops the guard because "the operator
> held it back last time" encodes a human's discretionary act as a system property, which is exactly
> the inversion this lineage keeps getting burned by. #359's own Phase 5 will file the next revisit
> issue with `ready-for-agent` already applied, and with `factory_wip_limit: 1` against an idle
> backlog it can be picked up minutes later — the identical #332→#342 collision shape. Cost
> asymmetry favors keeping it: one cheap `gh issue view` when it doesn't fire vs. a full ~$11-class
> duplicate Scorecard analysis if it would have fired and didn't run. Two adjustments: (a) invert the
> spec's Assumptions section to say the guard is expected NOT to fire this cycle, with the reason
> recorded; (b) broaden the guard to also check #359 itself (catches a retry/re-dispatch after a
> partial failure), not just the prior-revisit issue #342. On escalation: this is the guard's fourth
> consecutive cycle needing re-derivation (mirroring how #294/#332's ad-hoc XL-bucket guard got
> permanently baked into `commands/ceiling-revisit.md` by #361) — but do not file a new ticket for
> it (ticket-spam; #360 already exists as the canonical open ticket for "no real cadence gate");
> instead have implement post a tracking comment on #360 recording the re-derivation and suggesting
> a command-level same-window guard as a cheaper alternative to a full scheduler-level cadence gate.

> **Q2:** Two follow-ups on A1's recommendations, since both go beyond what any prior cycle in this
> lineage actually did as a standing requirement: (a) Is a comment on the unrelated open issue #360
> something #359's spec should mandate as a required implement-phase step, or does that overstep
> this ticket's `size: S`, env/analysis-only scope — should it instead just be a non-blocking Open
> Questions note left for a human to act on manually? (b) Should the spec mandate the broadened
> guard (checking both #342 and #359), or is checking only the prior-revisit issue — matching every
> previous cycle's actual implementation — sufficient, with the self-check framed as optional rather
> than required?
>
> **A2:** (a) Mandate it — a comment on #360 does not overstep scope. #342's plan already mandated
> filing two brand-new tickets (#360, #361) as a required, unconditional step, none of which is in
> `commands/ceiling-revisit.md`'s built-in side effects; one informational comment on an
> already-open ticket is strictly cheaper and lower-risk than that precedent. The scope line this
> lineage actually draws is "edits code/config" (defer to its own reviewed ticket, e.g. the
> `config/config.yaml` stale-comment deferral) versus "records a signal on the tracker" (fine to
> mandate directly) — a conditional, duplicate-guarded, non-fatal comment on an already-open ticket
> is on the mandate side. Attach three conditions to keep it size-S: conditional on #360 still being
> OPEN at run time; duplicate-guarded against a comment already citing this run's `UNTIL`; and
> non-fatal (a failed `gh issue comment` must not block Phases 2-5). (b) Mandate the broadened guard
> as one code path, not an optional enhancement — it's the same query with the issue number as a
> loop variable, reusing the same two-date header regex, at the cost of one extra `gh issue view`.
> The failure mode it covers is real: the primary side effect (posting the analysis comment) is
> non-idempotent, and a retry/continue after a partial failure re-runs Phase 1 with the prior-issue
> check passing cleanly, producing a duplicate analysis comment on #359 itself — exactly the
> duplicate the guard exists to prevent, just on the near side of the issue boundary. With the
> operator hold making the cross-issue (#342) match near-impossible this cycle, the retry path is
> now the more likely live duplicate risk, so a prior-issue-only guard would be aimed at the
> scenario least likely to actually occur. Correctness note carried into the spec: the restatement
> header (`## Dispatch Ceiling Weekly Revisit — Same-Window Restatement`) matches the bare prefix
> but carries no dates, so the two-date shape constraint in the header regex must be preserved
> verbatim — with a self-scan, a restatement comment from a previous partial run is now on the same
> issue being scanned, so a looser regex would null out the match and defeat the guard.
