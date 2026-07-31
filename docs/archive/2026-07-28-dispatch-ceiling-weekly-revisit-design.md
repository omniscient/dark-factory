# Dispatch Ceiling (C9) Weekly Revisit — Analysis Run for #294

**Issue:** omniscient/dark-factory#294
**Status:** design spec for a *recurring analysis run* (not a one-off feature build) — no code changes
**Policy origin:** omniscient/dark-factory#107 (closed, implemented the size/type-aware ceiling),
depends on the Factory Scorecard (#99, closed, implemented)
**Lineage:** #107 → #112/#119/#32 (duplicate weekly-revisit filings, closed) → #30 (canonical,
spec+plan landed 2026-07-17, executed) → **#294** (this ticket — filed automatically by #30's own
implement run, per `commands/ceiling-revisit.md` Phase 5, targeting 2026-07-24). Verified
via `gh issue list --search "Revisit dispatch ceiling"`: #294 is the only open issue with this
title, and its `createdAt` (2026-07-17T12:18:32Z) matches #30's implement run — so, unlike #30's
own body, #294's "Prior revisit: #30" citation checks out and needs no correction.

---

## Overview / Problem Statement

#107 added a size/type-aware dispatch ceiling to the scheduler: S tickets dispatch freely, M
tickets dispatch but lose the grace-window auto-advance, and on current main only XL tickets
always park in Blocked for human pairing (plus M tickets with a ceiling keyword in the title) —
see `scripts/scheduler_lib.sh` `is_above_ceiling`. L tickets dispatch autonomously since commit
4feef16 (2026-06-21). Caution: any L-bucket analysis output from this run must not propose
loosening an L rule that no longer exists. The keyword list
(`migration|migrate|performance|perf|architectur|refactor`, `config/config.yaml`
`dispatch_ceiling.keywords`) was a starting heuristic, meant to be revisited once the Factory
Scorecard (#99) accumulates enough per-bucket success data to tell which keywords are actually
discriminative versus which just add false-positive friction.

That revisit is a live, working, recurring process: `commands/ceiling-revisit.md` fetches
Scorecard data via `scripts/fetch_scorecard.py`, applies the decision rules in
`scripts/ceiling_revisit.py`, posts an analysis comment, opens a PR against `.archon/.env` only if
a keyword change is warranted, conditionally files an L-bucket code-change issue, and
unconditionally files the next weekly revisit issue. Both scripts exist, are unmodified since #30,
and are unit-tested (`tests/test_ceiling_revisit.py`, 17 assertions; `tests/test_fetch_scorecard.py`).

**This is the second execution of that recurring process**, and the first to actually reach a
committed spec on its own merits — two prior refine attempts on this exact issue (#294) ran and
both failed to commit anything under `docs/superpowers/specs/` (one aborted immediately after
$0.13 spend; one spent $1.63 / 23.5K output tokens and still produced nothing committed). This spec
exists to break that cycle: it gives the implement agent a single, correct source of truth for this
run's parameters, mirroring the structure `docs/archive/2026-07-17-dispatch-ceiling-weekly-revisit-design.md`
established for #30 (the only prior instance in this lineage that successfully executed end-to-end).

## Corrections to the Issue Body (ground-truth reconciliation)

| Issue #294 says | Verified reality |
|---|---|
| `UNTIL` = 2026-07-24 | Stale — that was the *target* date computed when #30's implement run filed this issue on 2026-07-17. `commands/ceiling-revisit.md`'s own Inputs section defines `$UNTIL` as "analysis window end (YYYY-MM-DD, today's date when the agent runs)," not a value frozen at filing time. This spec corrects `UNTIL` to **2026-07-28** (today, per this refine run) — see Requirements below for rationale, mirroring the identical correction #30's spec made to its own stale `UNTIL`. |
| `NEXT_DATE` = `<UNTIL + 7 days>` | Resolves to **2026-08-04** once `UNTIL` is corrected to 2026-07-28. |
| Spec reference: `docs/superpowers/specs/2026-07-17-dispatch-ceiling-weekly-revisit-design.md` | That path no longer exists — it was archived to `docs/archive/2026-07-17-dispatch-ceiling-weekly-revisit-design.md` after #30 executed (per `docs/archive/`'s own convention: archive completed workflow artifacts, keep referencing docs/tests green). Not a broken reference in the issue, just a natural consequence of #30 having already completed; noted here so the implement agent doesn't go looking for a spec that has moved. |
| "Prior revisit: #30" | **Correct as filed** — see Lineage note above. No correction needed. |

Everything else in the issue body (`SINCE=2026-06-12`, the four-step review procedure, the
`ABOVE_CEILING_KEYWORDS`-via-PR recommendation) matches the live, canonical tooling exactly.

## Requirements

- Fetch cumulative Scorecard data for the full policy window: `SINCE=2026-06-12` (fixed — policy
  introduction date, never rolling) through the actual execution date, via
  `scripts/fetch_scorecard.py`.
- Apply the existing decision rules unchanged (`scripts/ceiling_revisit.py`): per keyword, M-size
  cohort with `n≥5` — remove if success rate ≥ M baseline, keep if rate < M baseline − 15pts,
  otherwise "insufficient data — no change." L-bucket: file a code-change issue if success rate
  > 70% at `n≥5`.
- Post the per-bucket triad table and per-keyword analysis as a comment on **this issue (#294)**.
- Open a PR updating `ABOVE_CEILING_KEYWORDS` in `.archon/.env` only if the decision rules actually
  warrant a change. No `.archon/.env` currently exists in this checkout (verified — no override
  active), so the common-case outcome (insufficient data for most/all keywords, matching every
  prior run in this lineage) means no PR is expected.
- File the next weekly revisit issue unconditionally, with corrected parameters.
- Use corrected run parameters, not the stale ones written into #294's body eleven days ago:

  | Param | Issue #294 body (stale) | This spec (corrected) |
  |---|---|---|
  | `ISSUE_NUM` | 294 | 294 |
  | `SINCE` | 2026-06-12 | 2026-06-12 (unchanged — fixed anchor) |
  | `UNTIL` | 2026-07-24 | **2026-07-28** |
  | `NEXT_DATE` | 2026-07-31 | **2026-08-04** |

  Rationale: #294 was filed 2026-07-17 and sat un-refined for eleven days (two refine attempts
  failed to commit anything, as noted above); this refine phase is actually executing 2026-07-28.
  `SINCE` is a fixed cumulative anchor; `UNTIL` is meant to track actual execution per the
  command's own contract. Using the stale 2026-07-24 cutoff would silently discard four days of
  accumulated dispatch outcomes and misrepresent the analysis as more current than it is.
  `NEXT_DATE` advances 7 days from the corrected `UNTIL`, keeping the weekly cadence anchored to
  actual execution rather than compounding staleness forward into the next auto-filed issue.

## Architecture / Approach

No code changes. The implement agent invokes the existing, unmodified
`commands/ceiling-revisit.md` with:

```
ISSUE_NUM=294 SINCE=2026-06-12 UNTIL=2026-07-28 NEXT_DATE=2026-08-04
```

Its five phases (fetch/analyze, post comment, conditional PR, conditional L-bucket issue,
unconditional next-issue filing) already implement every requirement above; this spec deliberately
does not duplicate that mechanical detail — see `commands/ceiling-revisit.md` for the
authoritative procedure. The only change this ticket produces on `main`, code-wise, is this spec
document — everything else (comment, possible `.archon/.env` PR, possible next-issue filing)
happens at implement time via the existing command exactly as designed.

**Known plan-level execution detail (established precedent, not new to this ticket):**
`.archon/.env` is gitignored (`.gitignore:41`). #30's plan
(`docs/archive/2026-07-17-dispatch-ceiling-weekly-revisit-plan.md`, Task 5) already discovered and
resolved this: if `KEYWORDS_TO_REMOVE` is non-empty, Phase 3's `git add "$ENV_FILE"` must run as
`git add -f "$ENV_FILE"` — the one deliberate, intentional commit of that file this command
produces, on its own `chore/ceiling-revisit-<date>` branch cut from `origin/main` (not from the
`feat/` implementation branch, to keep the PR scoped to a single-file diff). Guard: the PR step
must stage ONLY the `ABOVE_CEILING_KEYWORDS` line from `.archon/.env` (never `git add -f` the
whole file), because that file is a gitignored secrets file and a populated copy would leak into
a public PR. The plan phase for
#294 should re-apply this same correction rather than rediscovering it; the command file itself is
not modified.

## Alternatives Considered

1. **Escalate to `needs-discussion` given two prior failed refine attempts.** Rejected — the two
   failures produced no comment explaining *why* they stalled (no `UNCERTAIN:` marker, no partial
   spec), and direct investigation here found no actual ambiguity: the procedure, tooling, and
   even the run parameters are fully determined by the issue body plus the command's own documented
   contract. Escalating would strand a validated, working recurring task over what looks like prior
   agent execution difficulty, not a genuine open question.
2. **Use the issue body's stale `UNTIL=2026-07-24` / `NEXT_DATE=2026-07-31` as written.** Rejected
   — the tooling's own contract defines `UNTIL` as the execution date; using a value four days
   stale (as of this refine run) would understate the measurement window and misrepresent how
   current the analysis is, exactly as #30's spec reasoned for its own correction.
3. **Chosen:** write the spec against verified ground truth (issue history confirmed via
   `gh issue list`), correct the stale run parameters explicitly with rationale, and leave the
   mechanical execution to the existing, unmodified `ceiling-revisit.md` command — mirroring the
   structure and reasoning #30's spec already established as the working pattern for this
   recurring ticket type.

## Open Questions (Non-blocking)

- **Why did two prior refine runs on #294 fail to commit a spec?** The first aborted almost
  immediately (~$0.13); the second spent $1.63 / 23.5K output tokens and still produced nothing on
  the branch. Neither left a diagnostic comment. Not investigated further here since this run
  succeeded once actually attempted with full context assembly — but if the pattern recurs on
  future weekly-revisit tickets, it may be worth a small follow-up ticket to add cost/attempt
  telemetry (or a token-budget floor check) around refine's Phase 4 brainstorming loop for this
  specific recurring ticket type, so a stuck run fails loud instead of silently burning budget.
- **`config/config.yaml:73` cadence comment is stale.** It still reads `# Revisit: 2026-09-12`, a
  leftover from before the cadence changed from quarterly to weekly (decided during #119's
  refinement, implemented via #355-lineage PRs). Doc-only inconsistency (the actual cadence is
  fully governed by the live `ceiling-revisit.md` chain, not this comment) — worth fixing during
  backlog grooming, not blocking this analysis run.

## Assumptions

- No `.archon/.env` currently exists in this checkout (verified) — the first keyword override, if
  any, creates it fresh via the plan's `git add -f` path (see Architecture above).
- Current effective `ABOVE_CEILING_KEYWORDS` is the `config/config.yaml` default
  (`migration|migrate|performance|perf|architectur|refactor`); no env override is active.
- The cumulative analysis window is `SINCE=2026-06-12` through `UNTIL=2026-07-28` (~6.5 weeks) —
  the actual per-keyword/per-bucket determination is left to the implement-time
  `fetch_scorecard.py` / `ceiling_revisit.py` run, not asserted here.
- `implement` runs shortly after this spec is approved (matching this lineage's historical
  cadence — #30's own spec→implement gap was same-day); if a multi-day gap occurs before
  implement actually runs, the implement agent should re-derive `UNTIL` as its own execution date
  rather than reusing 2026-07-28 verbatim, per the command's "today's date when the agent runs"
  contract.

## Brainstorming Q&A

> **Q1:** Given that the entire procedure this issue asks for is already fully implemented and
> tested as `commands/ceiling-revisit.md` + `scripts/ceiling_revisit.py` (no new capability,
> script, or architecture is needed), should the design spec for this ticket simply document "no
> new code — the implement phase directly executes `commands/ceiling-revisit.md`'s Phases 1-5 with
> resolved parameters," rather than proposing any new design/architecture? Is there any reason to
> believe this ticket needs actual new code changes beyond invoking the existing command?
>
> **A1:** Yes — spec it as a zero-code analysis run. There is direct precedent: the prior revisit
> (#30) produced exactly that spec, now at
> `docs/archive/2026-07-17-dispatch-ceiling-weekly-revisit-design.md`, whose header reads "design
> spec for a *recurring analysis run* (not a one-off feature build) — no code changes" and whose
> Architecture section is just the resolved parameter line against the unmodified command. Mirror
> that structure for #294. On `UNTIL`: prefer stating the *rule* ("`UNTIL` = the UTC date the
> implement phase runs, `NEXT_DATE` = `UNTIL` + 7") over freezing a literal where possible, since
> refine and implement may not run the same day — the issue body's stale value should be rejected
> on the same grounds #30's spec rejected its own stale value: the command defines `$UNTIL` as
> "today's date when the agent runs," and under-measuring silently discards accumulated dispatch
> outcomes. `scripts/ceiling_revisit.py` and `scripts/fetch_scorecard.py` are both present,
> unmodified since #30, and unit-tested — no reason to believe new code is needed. One optional
> non-blocking nit: `config/config.yaml:73`'s `# Revisit: 2026-09-12` comment is a stale leftover
> from the quarterly-cadence era, worth a note rather than an inline fix.

(A candidate second question — whether Phase 3's `.archon/.env` PR target needed reconsidering
given the file is gitignored — was pre-empted by direct evidence: #30's own plan doc
(`docs/archive/2026-07-17-dispatch-ceiling-weekly-revisit-plan.md`, Task 5) already investigated
this exact question, verified `git add .archon/.env` fails without `-f`, and established
`git add -f` on a dedicated `chore/` branch as the correct, already-reviewed resolution. Re-asking
it would have relitigated settled precedent rather than surfaced new information, so this spec
carries that resolution forward directly in the Architecture section instead of a second Q&A round.)
