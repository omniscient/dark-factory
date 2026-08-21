# Dispatch Ceiling (C9) Weekly Revisit — Analysis Run for #342

**Issue:** omniscient/dark-factory#342
**Status:** design spec for a *recurring analysis run* (not a one-off feature build) — no code changes
**Policy origin:** omniscient/dark-factory#107 (closed, implemented the size/type-aware ceiling),
depends on the Factory Scorecard (#99, closed, implemented)
**Lineage:** #107 → #112/#119/#32 (duplicate weekly-revisit filings, closed) → #30 (canonical,
spec+plan landed 2026-07-17, executed) → #294 (second execution, spec+plan landed 2026-07-28/07-31,
executed) → #332 (third execution, spec landed 2026-08-14, plan landed 2026-08-21, implement ran
2026-08-21 — its own feature branch `feat/issue-332-...` exists with commits but has not yet merged
to `main` as of this refine run) → **#342** (this ticket — filed automatically by #332's own
implement run, per `commands/ceiling-revisit.md` Phase 5, targeting 2026-08-28).

## Overview / Problem Statement

#107 added a size/type-aware dispatch ceiling to the scheduler: S tickets dispatch freely, M
tickets dispatch but lose the grace-window auto-advance (plus park in Blocked if the title matches
`ABOVE_CEILING_KEYWORDS`), L tickets dispatch autonomously since commit 4feef16 (2026-06-21), and
only XL tickets always park in Blocked for human pairing — see `is_above_ceiling()` in
`scripts/scheduler_lib.sh:44-53` (verified: `XL` → always true, `M` → keyword-gated, everything
else including `L` → false). The keyword list
(`migration|migrate|performance|perf|architectur|refactor`, `config/config.yaml:69`) was a starting
heuristic, meant to be revisited once the Factory Scorecard (#99) accumulates enough per-bucket
success data to tell which keywords are actually discriminative versus which just add
false-positive friction.

That revisit is a live, working, recurring process: `commands/ceiling-revisit.md` fetches
Scorecard data via `scripts/fetch_scorecard.py`, applies the decision rules in
`scripts/ceiling_revisit.py`, posts an analysis comment, opens a PR against `.archon/.env` only if
a keyword change is warranted, conditionally files an XL-bucket code-change issue, and
unconditionally files the next weekly revisit issue. Both scripts exist, are unmodified since
commit `27c890b` (predates #294), and are unit-tested (`tests/test_ceiling_revisit.py`,
`tests/test_fetch_scorecard.py`).

**This is the fourth execution of that recurring process, and it has a genuinely new wrinkle the
first three did not:** #332 — this ticket's own "Prior revisit" — posted its analysis comment at
**2026-08-21T21:08:00Z**, using window `SINCE=2026-06-12 → UNTIL=2026-08-21` (today). #342 was
created 12 seconds later (21:08:12Z) by #332's Phase 5, and this refine run started 20 seconds
after that (21:08:32Z) — **under a minute after #332's own analysis of, functionally, this exact
same window**. `config/config.yaml`'s `scheduler.factory_wip_limit: 1` (single concurrent factory
container, strictly sequential dispatch across the whole backlog) plus an evidently idle backlog is
why: nothing gates the "weekly" cadence in wall-clock terms — `Target date` in the issue body is
decorative prose, not read by `scheduler.sh`, `scripts/scheduler_lib.sh`, `scripts/factory_core/**`,
or `config/config.yaml` (verified by search — no reference anywhere in dispatch logic).

## Corrections to the Issue Body (ground-truth reconciliation)

| Issue #342 says | Verified reality |
|---|---|
| `UNTIL` = 2026-08-28 | This is the *planned* value #332's Phase 5 computed (its own `UNTIL` + 7), assuming roughly a week would elapse before this ticket got processed. It did not — refine is running the same day #342 was filed. Per the established rule ("`UNTIL` = the UTC date the implement phase actually runs," from #294's and #332's specs), this spec corrects `UNTIL` to **2026-08-21** (today). See Requirements for why this, in turn, triggers a new guard rather than a second full analysis. |
| `NEXT_DATE` = `<UNTIL + 7 days>` | With `UNTIL` corrected to 2026-08-21, resolves to **2026-08-28** — which happens to equal the *stale* value the issue body listed for `UNTIL`. Coincidence of the chain catching up with itself in a single day, not a computation to special-case (confirmed during brainstorming: the plain `UNTIL + 7` rule is correct here, no alternate arithmetic needed). |
| Spec reference: `docs/superpowers/specs/2026-08-14-dispatch-ceiling-weekly-revisit-design.md` | That path is accurate on `main` as of this refine run (#332's implement branch has not merged yet, so `main` still has #332's spec at its original, un-archived path). No correction needed, but note it may move to `docs/archive/` by the time #342's implement runs, per this lineage's own convention. |
| "Prior revisit: #332" | Correct as filed. |

Everything else in the issue body (`SINCE=2026-06-12`, the four-step review procedure, the
`ABOVE_CEILING_KEYWORDS`-via-PR recommendation) matches the live, canonical tooling exactly.

## Requirements

Standing requirements, carried forward from #30/#294/#332 (unchanged):

- Fetch cumulative Scorecard data for the full policy window: `SINCE=2026-06-12` (fixed) through
  `UNTIL` (the implement agent's actual execution date), via `scripts/fetch_scorecard.py`.
- Apply the existing decision rules unchanged (`scripts/ceiling_revisit.py`): per keyword, M-size
  cohort with `n≥5` — remove if success rate ≥ M baseline, keep if rate < M baseline − 15pts,
  otherwise "insufficient data — no change." XL-bucket (reported merged with L as "L+XL" —
  `ceiling_revisit.py`'s `build_bucket_table` merges them below a split-worthy sample size): file a
  code-change issue if success rate > 70% at `n≥5`.
- Post the per-bucket triad table and per-keyword analysis as a comment on **this issue (#342)**.
- Open a PR updating `ABOVE_CEILING_KEYWORDS` in `.archon/.env` only if the decision rules actually
  warrant a change. No `.archon/.env` currently exists in this checkout — the common-case outcome
  (insufficient data, matching every prior run including #332's just-posted one) means no PR is
  expected.
- Before filing the conditional XL-bucket code-change issue, check for an existing open issue with
  a matching title (carried forward from #332, **third application** — #331, "Revisit
  XL=always-above-ceiling rule in `is_above_ceiling()` — `scheduler_lib.sh`," is still open, so this
  guard is expected to fire again and skip filing).
- File the next weekly revisit issue unconditionally, with corrected parameters
  (`ISSUE_NUM=342 SINCE=2026-06-12 UNTIL=2026-08-21 NEXT_DATE=2026-08-28`, re-derived as today's
  actual execution date if implement runs on a later date than this refine pass).

**New this cycle:**

- **Same-window duplicate guard.** After re-deriving `UNTIL` as its own execution date, implement
  must check whether #332 (the "Prior revisit" issue) already carries a "Dispatch Ceiling Weekly
  Revisit" analysis comment whose stated window end equals that `UNTIL`. Identical `SINCE`+`UNTIL`
  against the same unmodified `fetch_scorecard.py`/`ceiling_revisit.py` is a provably identical
  computation — that is the skip criterion (window equality, **not** "ran today" as a calendar
  check, so a multi-day gap before implement runs correctly falls through to a real analysis). If
  the window matches:
  - Skip Phase 1 (fetch/analyze) and Phase 2's normal comment. Instead post a short,
    self-contained comment on #342: a pointer to #332's comment plus its headline results restated
    inline (S/M/L+XL all 100% at last measurement, no keyword change, XL issue skipped because #331
    is open) — so a later reader of #342 doesn't have to cross-reference #332 to know what happened.
  - Phases 3 and 4 are then naturally no-ops (no `KEYWORDS_TO_REMOVE` computed; #331 is still open
    regardless).
  - Still run Phase 5 unconditionally (file the next revisit issue) — the recurring chain must not
    silently die. Use the normal rule, `NEXT_DATE = UNTIL + 7`; no special-casing needed (see table
    above).
  - If the window does *not* match (implement runs on a later date and #332's window has aged out),
    proceed with the full standing-requirements procedure as normal — the guard is conditional, not
    a restructuring of the ticket type.
- **Stale-text correction (third consecutive cycle needing it — now a named requirement instead of
  a silent plan-level patch).** `ceiling_revisit.py:227` renders `"**The L=always-above-ceiling rule
  may be overly conservative.**"` plus a pointer to `is_above_ceiling()` "in `scheduler.sh`" whenever
  `l_bucket_needs_issue` is true; `commands/ceiling-revisit.md`'s Phase 4 issue-create title/body
  (lines 129, 133-134, 138, 140) carry the same two stale strings. Both predate the L-bucket
  autonomy change (commit 4feef16) and the `scheduler.sh` → `scripts/scheduler_lib.sh` split; #294's
  and #332's plans each patched this ad hoc at runtime (undocumented in either spec —
  `tests/test_ceiling_revisit.py` does not assert on this sentence, so the fix breaks nothing). **If
  `L_NEEDS_ISSUE` is true this cycle** (unlikely if the duplicate guard above skips Phase 1 entirely,
  but must still be handled correctly in the non-skip branch), implement must correct the rendered
  text before posting/filing: `"L=always-above-ceiling"` → `"XL=always-above-ceiling (L has
  dispatched autonomously since commit 4feef16, 2026-06-21)"`, and `` `scheduler.sh` `` →
  `` `scripts/scheduler_lib.sh` `` — in the report body and in the Phase 4 issue title/body if that
  issue is actually filed. This corrects generated output only; `scripts/ceiling_revisit.py` and
  `commands/ceiling-revisit.md` themselves stay unmodified, consistent with this ticket's zero-code
  scope.
- **File two follow-up tickets** (each duplicate-guarded via `gh issue list --search` on title, same
  pattern Phase 4 already uses for the XL-bucket issue), since both gaps have now recurred across
  two-plus consecutive cycles with only ad-hoc, spec-silent patches:
  1. **Cadence gate** (`scheduler.sh` / `scripts/factory_core/**` / `config/config.yaml`): propose a
     real dispatch-time gate so a weekly-revisit issue's `Target date` stops being purely decorative
     prose — e.g. don't promote a freshly-filed `Revisit dispatch ceiling` issue's `ready-for-agent`
     opt-in (or otherwise hold refine dispatch on it) until its stated target date has passed. This is
     a genuine change to dispatch/scheduling behavior — out of scope for this `size: S`,
     env/analysis-only ticket; needs its own reviewed spec per CLAUDE.md.
  2. **Ceiling-revisit hygiene** (`scripts/ceiling_revisit.py:227-231` and
     `commands/ceiling-revisit.md:129-140`): fix the stale "L=always-above-ceiling" /
     `scheduler.sh` strings at the source, and add a permanent duplicate-detection guard for the
     conditional XL-bucket issue filing directly into the command (rather than each cycle's plan
     re-deriving the same guard). Bundle these two into one ticket — they're the same conditional
     code path (Phase 4's XL-bucket filing) in the same two files, and splitting them risks two
     separate runs editing overlapping lines in `commands/ceiling-revisit.md`. Preserve the
     MarketHawk-correct `# TARGET-PATH` prefix conventions; this is a text/logic fix, not a path fix.
     This is a real code change to a phase-command file — out of scope here, needs its own reviewed
     ticket.

## Architecture / Approach

No code changes. The implement agent invokes the existing, unmodified `commands/ceiling-revisit.md`
with:

```
ISSUE_NUM=342 SINCE=2026-06-12 UNTIL=2026-08-21 NEXT_DATE=2026-08-28
```

(re-deriving `UNTIL`/`NEXT_DATE` from its own actual execution date if that differs from this
refine pass's date). Its five phases already implement every standing requirement above; this spec
does not duplicate that mechanical detail. This cycle's three additions — the same-window duplicate
guard, the stale-text correction, and the two follow-up-ticket filings — are execution-time
overlays on top of the unmodified command, exactly as #332's spec added the XL-bucket
duplicate-detection overlay without editing `commands/ceiling-revisit.md` itself.

Use the unprefixed `scripts/...` paths (`scripts/fetch_scorecard.py`, `scripts/ceiling_revisit.py`)
when executing this command's `# TARGET-PATH`-marked lines against this self-target repo — the
`dark-factory/` prefix in the command file is for the MarketHawk instance. (This correction is
already recorded in `.archon/memory/codebase-patterns.md`, and a corrected drift-safety rationale
for it was written to `architecture.md`/re-recorded to `codebase-patterns.md` by #332's implement
run — on `feat/issue-332-...`, not yet merged to `main` as of this refine run, so it isn't visible
in this checkout yet. No action needed here; nothing about that correction is specific to #342.)

**Known plan-level execution detail (established precedent, not new to this ticket):**
`.archon/.env` is gitignored (`.gitignore:41`). If `KEYWORDS_TO_REMOVE` is non-empty (not expected
this cycle), Phase 3's `git add "$ENV_FILE"` must run as `git add -f "$ENV_FILE"` — the one
deliberate, intentional commit of that file this command produces, on its own
`chore/ceiling-revisit-<date>` branch cut from `origin/main` (not from the `feat/` implementation
branch). Guard: stage only the `ABOVE_CEILING_KEYWORDS` line, never `git add -f` the whole file,
since it's a gitignored secrets file and a populated copy would leak into a public PR.

## Alternatives Considered

1. **Proceed with `UNTIL=2026-08-21` and let implement re-run the full analysis regardless,
   accepting the duplicate as the cost of a simple, uniform recurring process.** Rejected — the
   resulting comment would be near-guaranteed to restate #332's just-posted numbers verbatim (same
   `SINCE`/`UNTIL` against unmodified, deterministic scripts), at real cost (#332's refine+plan alone
   cost ~$11.21 per its own cost report) for zero new signal, and would still unconditionally file
   yet another next-revisit issue, continuing the same-day cascade indefinitely against an idle
   backlog.
2. **Add a same-day-duplicate guard keyed on calendar date ("skip if implement runs on the same UTC
   date #332's analysis posted").** Rejected in favor of keying on window equality (`SINCE`+`UNTIL`
   match) instead — a calendar-day check would incorrectly skip a legitimate re-run if, hypothetically,
   #332's window were somehow *not* identical to today's, and window equality is the actually
   defensible claim ("this is a provably identical computation"), not merely "it's the same day."
3. **Compute the skip-path `NEXT_DATE` from #342's own originally-planned target (2026-08-28) + 7 =
   2026-09-04, to avoid "wasting" a cadence slot.** Rejected — this would silently *skip* a week of
   intended cadence rather than restore it; #342 didn't consume its slot (it's the ticket asking the
   question, not the analysis that ran), so the plain `UNTIL + 7` rule is correct and requires no
   special case.
4. **Fix the recurring gaps (cadence gate, stale report text) directly in this ticket, since they
   keep recurring.** Rejected — both are genuine code changes to dispatch logic
   (`scheduler.sh`/`scripts/factory_core/**`) or a phase-command file
   (`commands/ceiling-revisit.md`, `scripts/ceiling_revisit.py`), out of scope for a `size: S`,
   env/analysis-only ticket per CLAUDE.md's scope discipline and the refine phase's own SCOPE
   BOUNDARY (specs/memory only). Filed as two duplicate-guarded follow-up tickets instead, mirroring
   how #294's run filed #331 for its own out-of-scope finding.
5. **Bundle the cadence-gate and ceiling-revisit-hygiene follow-ups into a single ticket.** Rejected
   — different code surfaces (scheduler dispatch-gating vs. a `commands/ceiling-revisit.md` +
   `ceiling_revisit.py` report/logic fix) with different risk profiles; bundling would force one
   ticket's reviewer to also review unrelated risk. Filed as two separate tickets instead.

## Open Questions (Non-blocking)

- **Is the same-window guard the right long-term shape, or just a stopgap?** It correctly prevents
  *this* cycle's duplicate, but it's still a plan/implement-time overlay re-derived (in some form)
  for the third consecutive cycle (duplicate-issue guard: #332; same-window guard: #342). If a
  fourth cycle needs yet another ad-hoc guard, that's the signal — per #332's own spec, which already
  predicted this — that the underlying gap (no real cadence gate) needs the follow-up ticket #1 above
  actually built, not another overlay.
- **`config/config.yaml:73` cadence comment is stale.** Still reads `# Revisit: 2026-09-12`, a
  leftover from the pre-weekly (quarterly) cadence era. Flagged by #294's and #332's specs; still
  unfixed as of this run. Doc-only inconsistency, worth fixing during backlog grooming.
- **#332's implement branch (`feat/issue-332-...`) has not merged to `main` as of this refine run.**
  This spec was written against `main` as it stands now, which does not yet include #332's memory
  corrections or its docs archival. If #332 merges before #342's implement runs, no action is needed
  here — the changes are additive and don't conflict with anything in this spec.

## Assumptions

- No `.archon/.env` currently exists in this checkout — the first keyword override, if any, creates
  it fresh via the plan's `git add -f` path.
- Current effective `ABOVE_CEILING_KEYWORDS` is the `config/config.yaml` default
  (`migration|migrate|performance|perf|architectur|refactor`); no env override is active.
- **The same-window duplicate guard is expected to fire this cycle** — #332's window
  (`SINCE=2026-06-12 → UNTIL=2026-08-21`) is very likely to still match at implement time, given the
  established same-day spec→implement cadence in this lineage (#30's and #332's own spec→implement
  gaps were same-day). A reader should expect #342's implement output to be a short restatement
  comment, not a fresh Scorecard fetch — that is the correct, intended outcome per this spec, not a
  bug.
- If implement instead runs on a later date (window no longer matches #332's), it should re-derive
  `UNTIL` as its own actual execution date and run the full standing-requirements procedure, exactly
  as #294's and #332's implement runs correctly did when a gap occurred.

## Brainstorming Q&A

> **Q1:** Given #332 (this ticket's own "Prior revisit") posted an analysis with UNTIL=2026-08-21
> (today) only ~30 seconds before this refine run started, applying the established "UNTIL = actual
> execution date" rule literally would set #342's UNTIL to the same 2026-08-21 — an identical
> cumulative window to what #332 already just measured, with the same unmodified, deterministic
> scripts. Should the spec (a) proceed with UNTIL=2026-08-21 as usual and accept a duplicate
> analysis, or (b) add a same-day-duplicate guard that skips the fetch/analyze/comment phases if
> #332 already carries a matching-window analysis comment, while still running Phase 5 (file next
> issue) unconditionally? If (b), how should `NEXT_DATE` be computed in the skip path?
>
> **A1:** (b), keyed on window equality (same `SINCE`+`UNTIL` against unmodified scripts = provably
> identical computation), not calendar-day — a calendar check would wrongly skip a legitimate later
> re-run if the windows somehow differed. Direct precedent: #332's own spec added exactly this shape
> of execution-time guard (the XL-bucket duplicate check) as a plan/implement overlay without
> touching `commands/ceiling-revisit.md`, explicitly framing command-file hardening as its own
> follow-up ticket — mirror that framing here. Skip path: post a short self-contained comment on
> #342 restating #332's headline results with a pointer, then run Phase 5 unconditionally with the
> plain rule `NEXT_DATE = UNTIL + 7` (= 2026-08-28 — no special-casing; using #342's own stale
> planned target + 7 = 2026-09-04 instead would silently skip a week of intended cadence, since
> #342 never actually consumed a cadence slot). Also flagged as non-blocking: `Target date` is
> decorative text nowhere read by dispatch logic, and with `factory_wip_limit: 1` plus an idle
> backlog the chain can self-refire same-day indefinitely — recommend a duplicate-guarded follow-up
> ticket proposing a real dispatch-time cadence gate, out of scope for this env-only ticket.

> **Q2:** (a) `ceiling_revisit.py`'s stale "L=always-above-ceiling ... scheduler.sh" report text
> (and the matching strings in `commands/ceiling-revisit.md`'s Phase 4 issue body) has now needed
> the same ad-hoc runtime patch for two consecutive cycles (#294, #332), always undocumented in the
> spec. Should #342 name this as an explicit requirement this time, and recommend a source-level fix
> as a separate follow-up ticket — bundled with or separate from the cadence-gate follow-up from Q1?
> (b) Given the guard from A1 means Phase 1-2 will likely be skipped entirely this cycle, should the
> spec restructure to lead with the skip path as the primary expected outcome, or keep the full
> procedure as the primary Requirements section with the skip as a conditional addition?
>
> **A2:** (a) Yes — name it explicitly this cycle (two consecutive cycles of silent plan-level
> rediscovery is the "provably recurring" bar; `tests/test_ceiling_revisit.py` doesn't assert on the
> sentence, so a source fix is safe). File the source-level fix as a **separate** ticket from the
> cadence gate — different code surfaces and risk profiles — but **bundle it with the XL-bucket
> duplicate-detection-guard follow-up** (also predicted by #332's spec) into one ticket, since both
> touch the same conditional code path (Phase 4's XL-bucket issue filing) in the same two files, and
> splitting them risks two runs editing overlapping lines in `commands/ceiling-revisit.md`.
> (b) Keep the full procedure as the primary Requirements section, with the guard folded in as a
> conditional "New this cycle" addition — matching #294's and #332's own structure (five standing
> bullets, cycle-specific additions appended), so a reader diffing this spec against the lineage sees
> the same load-bearing procedure each time. The guard is conditional and may not even fire on a
> later implement date, so it shouldn't dominate the spec's structure; note the *expected* outcome
> (a skip/restatement) in Assumptions instead, where this lineage already documents "what we expect
> to actually happen at implement time."
