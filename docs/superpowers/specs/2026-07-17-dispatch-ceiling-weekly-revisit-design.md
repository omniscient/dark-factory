# Dispatch Ceiling (C9) Weekly Revisit — Analysis Run for #30

**Issue:** omniscient/dark-factory#30
**Status:** design spec for a *recurring analysis run* (not a one-off feature build) — no code changes
**Policy origin:** omniscient/dark-factory#107 (closed), which depends on Factory Scorecard
omniscient/dark-factory#99 (closed, implemented)
**Duplicate tickets in the same lineage, already closed:** #112, #119, #32 — #32's closing comment
("Duplicate of #30 ... #30 is the older ticket and carries the fuller spec. Closed during 2026-07-07
backlog grooming.") designates #30 as the canonical survivor of a duplicate-filing bug that produced
four parallel "Revisit dispatch ceiling" tickets for the same recurring task.

---

## Overview / Problem Statement

omniscient/dark-factory#107 added a size/type-aware dispatch ceiling to the scheduler: S tickets
dispatch freely, M tickets dispatch but lose the grace-window auto-advance, and L tickets (plus M
tickets whose title matches `ABOVE_CEILING_KEYWORDS`) are parked in Blocked for human pairing. The
keyword list (`migration|migrate|performance|perf|architectur|refactor`, see `config/config.yaml`
`dispatch_ceiling.keywords`) was a starting heuristic, not a measured one — #107 explicitly calls for
periodic revisits once the Factory Scorecard (#99) accumulates enough per-bucket success data to
tell which keywords are actually discriminative versus which just add false-positive friction.

That revisit is now a live, working, recurring process: `.archon/commands/ceiling-revisit.md`
("Weekly Dispatch Ceiling Revisit") fetches Scorecard data via `scripts/fetch_scorecard.py`, applies
the decision rules in `scripts/ceiling_revisit.py`, posts an analysis comment, opens a PR against
`.archon/.env` only if a keyword change is warranted, conditionally files an L-bucket code-change
issue, and unconditionally files the next weekly revisit issue. Both scripts exist and work today —
the "dangling seam" that once made this tooling non-functional (closed issues #179 and #186) has
already been fixed.

This ticket (#30) is one execution of that recurring process. This spec exists because no design
doc for the recurring revisit has ever actually landed on `main` — three earlier attempts (refine
runs against #112, #119, and #32) each generated a spec on their own branch, but none of those
branches were merged, so `docs/superpowers/specs/` currently has no ceiling-revisit doc at all. This
spec fills that gap and gives the implement agent a single, correct source of truth for this run's
parameters.

## Corrections to the Issue Body (ground-truth reconciliation)

Issue #30's body, as filed, contains several references that do not match the live repo or GitHub
history. These were checked directly (`gh issue view`, file reads) rather than assumed, and are
corrected here rather than carried forward into the analysis or the next auto-filed issue:

| Issue #30 says | Verified reality |
|---|---|
| Factory Scorecard is `#331` | `#331` does not exist in this repo. The real Scorecard issue is **#99** (closed, implemented), which #107's and #112's own bodies cite correctly. |
| Spec: `docs/superpowers/specs/2026-06-13-dispatch-ceiling-quarterly-revisit-design.md` | Does not exist anywhere in the repo (checked `docs/superpowers/specs/` and `docs/archive/`). This spec (this file) is the first one to actually land. |
| Architecture review: `docs/dark-factory-architecture-review-2026-06-11.html` | Does not exist anywhere in the repo. Not required for this run — the decision rules are fully specified in `scripts/ceiling_revisit.py` and don't depend on the review doc. |
| "Prior revisit: #112 (week-1 analysis, 2026-06-12 → 2026-06-20)" | #112's own body and comments are framed entirely as a **quarterly** revisit (target 2026-09-12) and never mention a "week-1" window. The actual "week 1" conversion (quarterly → weekly cadence, decided 2026-06-20) happened on **#119**, not #112, per #119's own refinement comment. |
| Footer: "Filed automatically by **MarketHawk** weekly ceiling revisit" | This ticket lives in `omniscient/dark-factory`, a distinct self-hosted instance (per this repo's `CLAUDE.md`: "A separate instance targets MarketHawk"). `scripts/ceiling_revisit.py` and `scripts/factory_core/identity.py` both hardcode `FACTORY_PRODUCT_NAME`'s *default* to `"MarketHawk"`; this and other tickets in the same lineage (#32 has the identical footer) suggest that env var wasn't set correctly for whichever run filed them. Flagged below as a non-blocking open question, not fixed by this ticket. |

A brainstorming product-owner review (see Q&A below) confirmed: these are citation-level defects,
not a corruption of the actual measurement. The ticket's core ask — run the analysis, assess
keyword false-positive rate, recommend a keyword change only if the data warrants it — is exactly
what the live tooling already implements, and #30 is the human-designated canonical ticket for it.
Escalating to `needs-discussion` over stale citations would strand a validated, working recurring
task; `needs-discussion` is reserved for if the analysis itself turns up a genuine policy question
(e.g. data that argues for loosening a safety-relevant keyword).

## Requirements

- Fetch cumulative Scorecard data for the full policy window: `SINCE=2026-06-12` (fixed —
  policy-introduction date, never rolling) through the actual execution date, via
  `scripts/fetch_scorecard.py`.
- Apply the existing decision rules unchanged (`scripts/ceiling_revisit.py`): per keyword, M-size
  cohort with `n≥5` — remove if success rate ≥ M baseline, keep if rate < M baseline − 15pts,
  otherwise "insufficient data — no change." L-bucket: file a code-change issue if success rate
  > 70% at `n≥5` (this rule already produced #29/#31, both since closed).
- Post the per-bucket triad table and per-keyword analysis as a comment on **this issue (#30)**,
  not #112 or any other ticket in the lineage.
- Open a PR updating `ABOVE_CEILING_KEYWORDS` in `.archon/.env` (not `config/config.yaml`, which is
  a doc-only mirror per the original #107 design) only if the decision rules actually warrant a
  change. No `.archon/.env` exists today, so the common-case outcome (insufficient data for most/all
  keywords) means no PR is expected, matching every prior run in this lineage.
- File the next weekly revisit issue unconditionally, with corrected parameters (see below) and
  correct product branding.
- Use corrected run parameters, **not** the stale ones written into #30's body three weeks ago:

  | Param | Issue #30 body (stale) | This spec (corrected) |
  |---|---|---|
  | `ISSUE_NUM` | 30 | 30 |
  | `SINCE` | 2026-06-12 | 2026-06-12 (unchanged — fixed anchor) |
  | `UNTIL` | 2026-06-27 | **2026-07-17** |
  | `NEXT_DATE` | 2026-07-04 | **2026-07-24** |

  Rationale: #30 was filed 2026-06-20 and sat un-refined while a duplicate (#32) was filed and later
  closed (2026-07-07 backlog grooming); this refine phase is actually executing 2026-07-17, three
  weeks past the ticket's stated `UNTIL`. `.archon/commands/ceiling-revisit.md` defines `$UNTIL` as
  "today's date when the agent runs," not a date fixed at filing time — using the stale 2026-06-27
  cutoff would silently discard three weeks of accumulated dispatch outcomes and defeat the purpose
  of a *re-measurement*. `NEXT_DATE` advances 7 days from the corrected `UNTIL`, keeping the weekly
  cadence anchored to actual execution rather than compounding the staleness forward.

## Architecture / Approach

No code changes. The implement agent invokes the existing, unmodified
`.archon/commands/ceiling-revisit.md` with:

```
ISSUE_NUM=30 SINCE=2026-06-12 UNTIL=2026-07-17 NEXT_DATE=2026-07-24
```

Its five phases (fetch/analyze, post comment, conditional PR, conditional L-bucket issue,
unconditional next-issue filing) already implement every requirement above; this spec deliberately
does not duplicate that mechanical detail (see `.archon/commands/ceiling-revisit.md` for the
authoritative procedure). The only change this ticket produces on `main`, code-wise, is this spec
document — everything else (comment, possible `.archon/.env` PR, possible next-issue filing) happens
at implement time via the existing command exactly as designed.

## Alternatives Considered

1. **Escalate to `needs-discussion` given the factual errors in the issue body.** Rejected — a
   product-owner brainstorming pass concluded these are citation-level defects in a well-supported,
   tooling-backed recurring task, not a reason to strand it (see Corrections section).
2. **Use the issue body's stale `UNTIL=2026-06-27` / `NEXT_DATE=2026-07-04` as-is, to match what was
   literally written.** Rejected — the tooling's own contract defines `UNTIL` as the execution date;
   silently under-measuring by three weeks defeats the point of the revisit and would compound
   staleness into the next auto-filed issue.
3. **Chosen:** write the spec against verified ground truth, correct the stale run parameters
   explicitly (with rationale, so the divergence from the issue body reads as an intentional
   correction rather than a transcription error), and leave the mechanical execution to the
   existing, unmodified `ceiling-revisit.md` command.

## Open Questions (Non-blocking)

- **Product-name branding default.** `scripts/factory_core/identity.py` and
  `scripts/ceiling_revisit.py` both default `FACTORY_PRODUCT_NAME` to `"MarketHawk"` when the env var
  isn't set. Multiple tickets in this lineage (#30, #32) carry a "Filed automatically by MarketHawk
  weekly ceiling revisit" footer despite living in the self-hosted `omniscient/dark-factory` repo.
  Worth a small follow-up ticket to check why `FACTORY_PRODUCT_NAME` wasn't resolving to the
  self-hosting instance's own identity for those specific runs.
- **Duplicate-issue-filing history.** This lineage has produced duplicate tickets twice: the weekly
  revisit ticket itself (#112, #119, #32, #30 all filed for the same recurring task) and the L-bucket
  code-change ticket (#29 and #31, identical titles, both closed). `.archon/commands/ceiling-revisit.md`
  Phases 4 and 5 create issues unconditionally with no check for an existing open one with a matching
  title. Worth a small follow-up ticket to add a `gh issue list --search "<title>" --state open` guard
  before each `gh issue create` call, mirroring the dedup pattern `dedupe_oos.py` already uses
  elsewhere in this repo.
- **Label drift.** #30 carries `size: M`, but the command template's Phase 5 (line ~193) files the
  next weekly issue as `size: S` — a fix that #119's own brainstorming Q&A explicitly called for
  ("the revisit task is mechanical... `size: M` in the command template is stale"). #30 predates that
  fix. Not blocking this analysis run; worth noting during backlog grooming.

## Assumptions

- No `.archon/.env` currently exists in this checkout — the first keyword override, if any, creates
  it fresh via the `>>`-append path in `ceiling-revisit.md` Phase 3.
- Current effective `ABOVE_CEILING_KEYWORDS` is the `config/config.yaml` default
  (`migration|migrate|performance|perf|architectur|refactor`); no env override is active.
- The cumulative window has grown to ~5 weeks (2026-06-12 → 2026-07-17) versus the ~1–2 weeks
  available to prior runs in this lineage, so more keyword cohorts may cross the `n≥5` threshold than
  in earlier "insufficient data" outcomes — the actual determination is left to the implement-time
  `fetch_scorecard.py` / `ceiling_revisit.py` run, not asserted here.
- Factory Scorecard is issue **#99** (closed, implemented); no issue `#331` exists in this repo.

## Brainstorming Q&A

> **Q1:** Given issue #30's body contains several factually incorrect/fabricated references
> (nonexistent Scorecard issue #331 instead of the real #99; nonexistent spec and
> architecture-review file paths; a mischaracterization of #112 as a "week-1" analysis that doesn't
> match #112's real content), should the spec-writing agent (A) escalate via `needs-discussion`
> instead of writing a spec, (B) write the spec grounded in verified ground truth while explicitly
> documenting/correcting the errors, or (C) something else?
>
> **A1:** Option (B), with the corrections captured explicitly. The issue's core ask — run the
> weekly ceiling-keyword revisit, assess the keyword false-positive rate, and PR an
> `ABOVE_CEILING_KEYWORDS` change only if data warrants — is exactly what the live, canonical
> `.archon/commands/ceiling-revisit.md` and the already-fixed `fetch_scorecard.py`/`ceiling_revisit.py`
> implement, so the ticket is well-supported by current tooling and is the human-designated canonical
> survivor (#32 was closed as a duplicate of it); escalating via `needs-discussion` would strand a
> validated recurring task over citation-level defects. The fabricated references are most likely
> cross-contamination from the MarketHawk instance's own parallel revisit process, not a corruption
> of the actual analysis. Reserve `needs-discussion` for if executing the analysis surfaces a genuine
> policy question (e.g. data that argues for loosening a safety-relevant keyword).

> **Q2:** Given the ticket's stated `UNTIL=2026-06-27` is now three weeks stale relative to actual
> execution (2026-07-17), and the tooling's own contract says `UNTIL` should be "today's date when
> the agent runs," what `UNTIL`/`NEXT_DATE` should this spec bake in — the stale ones from the issue
> body, or updated ones reflecting the real elapsed window? Should the correction be flagged
> explicitly or applied silently?
>
> **A2:** Bake in the updated values (`UNTIL=2026-07-17`, `NEXT_DATE=2026-07-24`) and flag the
> correction explicitly. `SINCE` is a fixed cumulative anchor and `UNTIL` is meant to track actual
> execution, so using the stale cutoff would silently discard three weeks of accumulated dispatch
> outcomes and defeat the purpose of the re-measurement. `NEXT_DATE` should advance one week past
> the real `UNTIL` to keep the weekly cadence rolling forward from actual execution rather than from
> an already-past date. The spec should state the correction and its rationale so the implement
> agent and any human reviewer understand why the baked literals diverge from the issue body, rather
> than assuming a transcription error.
