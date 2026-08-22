# Dispatch Ceiling (C9) Weekly Revisit — Analysis Run for #332

**Issue:** omniscient/dark-factory#332
**Status:** design spec for a *recurring analysis run* (not a one-off feature build) — no code changes
**Policy origin:** omniscient/dark-factory#107 (closed, implemented the size/type-aware ceiling),
depends on the Factory Scorecard (#99, closed, implemented)
**Lineage:** #107 → #30 (1st weekly revisit, spec+plan archived,
`docs/archive/2026-07-17-dispatch-ceiling-weekly-revisit-design.md`) → **#294** (2nd revisit, spec
archived `docs/archive/2026-07-28-dispatch-ceiling-weekly-revisit-design.md`, plan archived
`docs/archive/2026-07-31-dispatch-ceiling-weekly-revisit-plan.md`, executed 2026-07-31, closed) →
**#332** (this ticket — filed automatically by #294's own implement run, per
`commands/ceiling-revisit.md` Phase 5, targeting 2026-08-07). Verified via
`gh issue list --search "Revisit dispatch ceiling"`: #332 is the only open issue with this title;
its `createdAt` (2026-07-31T18:30:50Z) and body match #294's implement report exactly (which used
`UNTIL=2026-07-31 NEXT_DATE=2026-08-07` to file it) — the "Prior revisit: #294" citation checks out.

---

## Overview / Problem Statement

#107 added a size/type-aware dispatch ceiling to the scheduler: S tickets dispatch freely, M
tickets dispatch but lose the grace-window auto-advance, and XL tickets always park in Blocked for
human pairing (plus M tickets with a ceiling keyword in the title) — see `is_above_ceiling()` in
`scripts/scheduler_lib.sh`. L tickets dispatch autonomously since commit 4feef16 (2026-06-21). The
keyword list (`migration|migrate|performance|perf|architectur|refactor`, `config/config.yaml`
`dispatch_ceiling.keywords`) was a starting heuristic, meant to be revisited once the Factory
Scorecard (#99) accumulates enough per-bucket success data to tell which keywords are actually
discriminative versus which just add false-positive friction.

That revisit is a live, working, recurring process: `commands/ceiling-revisit.md` fetches
Scorecard data via `scripts/fetch_scorecard.py`, applies the decision rules in
`scripts/ceiling_revisit.py`, posts an analysis comment, opens a PR against `.archon/.env` only if
a keyword change is warranted, conditionally files an L/XL-bucket code-change issue, and
unconditionally files the next weekly revisit issue. Both scripts exist, are unmodified since
commit 27c890b (well before #294's spec), and are unit-tested (`tests/test_ceiling_revisit.py`,
`tests/test_fetch_scorecard.py`).

**This is the third execution of that recurring process**, and the second to have direct,
successful precedent to mirror: #294 ran this exact procedure end-to-end on 2026-07-31 — S=100%
(n=5), M=100% (n=21, baseline), L+XL=100% (n=6), no keyword change warranted, and it filed a
conditional XL-bucket code-change issue (#331, "Revisit XL=always-above-ceiling rule in
`is_above_ceiling()`," still open) because L+XL success cleared the >70%-at-n≥5 threshold. This
spec follows #294's spec structure directly, correcting this run's parameters and folding forward
two things #294's cycle learned the hard way (see Requirements and Architecture below) so the plan
and implement phases don't have to rediscover them.

## Corrections to the Issue Body (ground-truth reconciliation)

| Issue #332 says | Verified reality |
|---|---|
| `UNTIL` = 2026-08-07 | Stale — that was the *target* date computed when #294's implement run filed this issue on 2026-07-31. `commands/ceiling-revisit.md`'s own Inputs section defines `$UNTIL` as "analysis window end (YYYY-MM-DD, today's date when the agent runs)," not a value frozen at filing time. This spec corrects `UNTIL` to **2026-08-14** (today, per this refine run) — see Requirements below. This mirrors both #30's and #294's spec, which made the identical correction to their own stale `UNTIL` values. |
| `NEXT_DATE` = `<UNTIL + 7 days>` | Resolves to **2026-08-21** once `UNTIL` is corrected to 2026-08-14. |
| Spec reference: `docs/superpowers/specs/2026-07-28-dispatch-ceiling-weekly-revisit-design.md` | That path no longer exists — archived to `docs/archive/2026-07-28-dispatch-ceiling-weekly-revisit-design.md` after #294 executed, per `docs/archive/`'s own convention. Not a broken reference in the issue, just a natural consequence of #294 having completed. |
| "Prior revisit: #294" | **Correct as filed** — see Lineage note above. No correction needed. |

Everything else in the issue body (`SINCE=2026-06-12`, the four-step review procedure, the
`ABOVE_CEILING_KEYWORDS`-via-PR recommendation) matches the live, canonical tooling exactly.

## Requirements

- Fetch cumulative Scorecard data for the full policy window: `SINCE=2026-06-12` (fixed — policy
  introduction date, never rolling) through the actual execution date, via
  `scripts/fetch_scorecard.py`.
- Apply the existing decision rules unchanged (`scripts/ceiling_revisit.py`): per keyword, M-size
  cohort with `n≥5` — remove if success rate ≥ M baseline, keep if rate < M baseline − 15pts,
  otherwise "insufficient data — no change." L+XL-bucket: file a code-change issue if success rate
  > 70% at `n≥5`.
- Post the per-bucket triad table and per-keyword analysis as a comment on **this issue (#332)**.
- Open a PR updating `ABOVE_CEILING_KEYWORDS` in `.archon/.env` only if the decision rules actually
  warrant a change. No `.archon/.env` currently exists in this checkout (verified — no override
  active), and every prior run in this lineage returned "insufficient data" for every keyword, so
  the common-case outcome (no PR) is expected again.
- File the next weekly revisit issue unconditionally, with corrected parameters.
- **New this cycle — guard the conditional L/XL-bucket issue filing against duplicating #331.**
  #294's run already found L+XL at 100%/n=6 and filed #331, which is still open. With another
  week of (very likely still-100%) data, `L_NEEDS_ISSUE` will almost certainly evaluate `True`
  again — but `commands/ceiling-revisit.md` Phase 4 has no duplicate-detection guard; it calls
  `gh issue create` unconditionally whenever the flag is set. Before executing Phase 4, the
  implement agent must check for an existing open issue matching the title pattern (e.g.
  `gh issue list --repo "$REPO" --search "Revisit XL=always-above-ceiling rule" --state open`) and,
  if one is already open, skip the filing and instead note the pre-existing issue number in the
  analysis comment on #332 rather than opening a duplicate. This is a plan/implement-time
  execution guard, not a change to `commands/ceiling-revisit.md` itself — that command-file
  hardening (a permanent duplicate-detection guard baked into Phase 4) is out of scope for this
  ticket; file it as a spillover code-change ticket if the guard proves necessary again.
- Use corrected run parameters, not the stale ones written into #332's body two weeks ago:

  | Param | Issue #332 body (stale) | This spec (corrected) |
  |---|---|---|
  | `ISSUE_NUM` | 332 | 332 |
  | `SINCE` | 2026-06-12 | 2026-06-12 (unchanged — fixed anchor) |
  | `UNTIL` | 2026-08-07 | **2026-08-14** |
  | `NEXT_DATE` | (unresolved in body) | **2026-08-21** |

  Rationale: #332 was filed 2026-07-31 with a target date of 2026-08-07; this refine phase is
  actually executing 2026-08-14 — right on the established weekly cadence, no unusual staleness,
  but still a week later than the issue's own frozen target. `SINCE` is a fixed cumulative anchor;
  `UNTIL` is meant to track actual execution per the command's own contract. Using the stale
  2026-08-07 cutoff would silently discard a week of accumulated dispatch outcomes.

## Architecture / Approach

No code changes. The implement agent invokes the existing, unmodified
`commands/ceiling-revisit.md` with:

```
ISSUE_NUM=332 SINCE=2026-06-12 UNTIL=2026-08-14 NEXT_DATE=2026-08-21
```

using the **unprefixed** script paths — `scripts/fetch_scorecard.py` and
`scripts/ceiling_revisit.py` — not the `# TARGET-PATH`-marked `dark-factory/scripts/...` lines as
literally written in the command file. Its five phases (fetch/analyze, post comment, conditional
PR, conditional L/XL-bucket issue, unconditional next-issue filing) already implement every
requirement above; this spec deliberately does not duplicate that mechanical detail — see
`commands/ceiling-revisit.md` for the authoritative procedure. The only change this ticket
produces on `main`, code-wise, is this spec document — everything else (comment, possible
`.archon/.env` PR, possible next-issue filing) happens at implement time via the existing command
exactly as designed.

**Correction to the existing `[PROVISIONAL]` memory entry on TARGET-PATH resolution.**
`.archon/memory/codebase-patterns.md` carries an entry (from #294, source:implement) claiming the
`dark-factory/`-prefixed path "does not exist as tracked content in a fresh self-target clone."
That is true of `git ls-files`, but incomplete as a runtime claim, and this spec's own
investigation (mirroring #294's diligence) found the fuller picture: `entrypoint.sh` (lines
568-572) copies the image-baked `/opt/dark-factory/scripts` into `$CLONE_DIR/dark-factory/scripts`
at container bootstrap whenever the target repo doesn't already provide that path — which for a
self-target dark-factory clone means it copies its own repo's `scripts/` into a `dark-factory/`
subdirectory that then exists on disk (git-excluded via `.git/info/exclude`, so it is real,
present, and executable — not absent). The actually correct reason to prefer the unprefixed
`scripts/...` path is **drift-safety, not existence**: `scripts/*.py` is the tracked, canonical
source a feature branch can modify; `dark-factory/scripts/*.py` is a frozen image-baked snapshot
that (a) does not reflect any changes made on the current branch, and (b) gets wiped outright by
`git clean -fd dark-factory/ .claude/` in the deconflict/conflict-resolution flow
(`entrypoint.sh:694`). The implement agent for this ticket should re-record the
`codebase-patterns.md` entry with this corrected rationale (this is implement's normal memory-write
responsibility for that file per refine's own scope rules — refine does not write to
`codebase-patterns.md`) rather than let the "path does not exist" claim stand as the reason for a
second confirming cycle.

**Known plan-level execution detail (established precedent, not new to this ticket):**
`.archon/.env` is gitignored (`.gitignore:41`). #30's plan
(`docs/archive/2026-07-17-dispatch-ceiling-weekly-revisit-plan.md`, Task 5) already discovered and
resolved this: if `KEYWORDS_TO_REMOVE` is non-empty, Phase 3's `git add "$ENV_FILE"` must run as
`git add -f "$ENV_FILE"` — the one deliberate, intentional commit of that file this command
produces, on its own `chore/ceiling-revisit-<date>` branch cut from `origin/main` (not from the
`feat/` implementation branch, to keep the PR scoped to a single-file diff). Guard: the PR step
must stage ONLY the `ABOVE_CEILING_KEYWORDS` line from `.archon/.env` (never `git add -f` the
whole file), because that file is a gitignored secrets file and a populated copy would leak into a
public PR. The plan phase for #332 should re-apply this same correction rather than rediscovering
it; the command file itself is not modified. (Not expected to fire this cycle — no `.archon/.env`
override is currently active and no prior cycle has produced a keyword change.)

## Alternatives Considered

1. **Escalate to `needs-discussion`.** Rejected — there is no genuine ambiguity: the procedure,
   tooling, and run parameters are fully determined by the issue body plus the command's own
   documented contract, and this exact ticket type has now executed successfully twice (#30,
   #294). Escalating a well-precedented recurring analysis run over routine date-staleness would
   be pure overhead.
2. **Use the issue body's stale `UNTIL=2026-08-07` / unresolved `NEXT_DATE` as written.** Rejected
   — the tooling's own contract defines `UNTIL` as the execution date; using a value a week stale
   (as of this refine run) would understate the measurement window, exactly as #30's and #294's
   specs reasoned for their own corrections.
3. **Fix `commands/ceiling-revisit.md`'s `# TARGET-PATH` lines directly (strip the `dark-factory/`
   prefix in the command file itself) rather than noting the correction in this spec.** Rejected —
   the prefix is correct for the MarketHawk instance, which vendors this factory's scripts as
   tracked content under `dark-factory/`; the command file is shared across instances and is not
   this ticket's concern. Any change to a phase command file is a hard-limit item requiring its
   own reviewed ticket per CLAUDE.md, and this remains a zero-code analysis run.
4. **Chosen:** write the spec against verified ground truth (issue and memory history confirmed
   directly), correct the stale run parameters and the TARGET-PATH rationale explicitly, add the
   #331-duplicate guard as a requirement, and leave the mechanical execution to the existing,
   unmodified `ceiling-revisit.md` command — mirroring the structure #30's and #294's specs already
   established as the working pattern for this recurring ticket type.

## Open Questions (Non-blocking)

- **Will the L/XL-bucket duplicate-detection guard (new requirement above) actually be needed this
  cycle?** Depends on whether this week's Scorecard data still shows L+XL success > 70% at n≥5.
  Given #294's run measured 100% at n=6 and the policy hasn't changed, it likely will — but the
  guard costs nothing to include either way, and correctly no-ops if #331 has since been closed or
  if the bucket no longer clears the threshold.
- **`config/config.yaml:73` cadence comment is still stale.** It reads `# Revisit: 2026-09-12`, a
  leftover from the pre-weekly-cadence era — #294's spec flagged this as a non-blocking nit and it
  remains unfixed. Still doc-only (the actual cadence is fully governed by the live
  `ceiling-revisit.md` chain), still worth a backlog-grooming fix, still not blocking this run.
- **Should the #331-duplicate guard become a permanent fix to `commands/ceiling-revisit.md`
  Phase 4 instead of a per-cycle plan/implement workaround?** Deferred per Alternative 3 above —
  if this same guard has to be re-applied a third time in a future cycle, that repetition itself
  is the signal to file a dedicated code-change ticket to bake the `gh issue list` duplicate check
  directly into the command.

## Assumptions

- No `.archon/.env` currently exists in this checkout (verified) — the first keyword override, if
  any, creates it fresh via the plan's `git add -f` path (see Architecture above).
- Current effective `ABOVE_CEILING_KEYWORDS` is the `config/config.yaml` default
  (`migration|migrate|performance|perf|architectur|refactor`); no env override is active.
- The cumulative analysis window is `SINCE=2026-06-12` through `UNTIL=2026-08-14` (~9 weeks) — the
  actual per-keyword/per-bucket determination is left to the implement-time `fetch_scorecard.py` /
  `ceiling_revisit.py` run, not asserted here.
- Issue #331 (L/XL-bucket code-change issue filed by #294) is still open as of this refine run;
  if it has since been closed by the time implement executes, the new duplicate-detection
  requirement above correctly allows a fresh issue to be filed.
- `implement` runs shortly after this spec is approved (matching this lineage's historical
  cadence); if a multi-day gap occurs before implement actually runs, the implement agent should
  re-derive `UNTIL` as its own execution date rather than reusing 2026-08-14 verbatim, per the
  command's "today's date when the agent runs" contract — exactly as #294's implement run correctly
  did (shifting the spec's 2026-07-28 literal to its own 2026-07-31 execution date).

## Brainstorming Q&A

> **Q1:** Given that the entire procedure this issue asks for is already fully implemented,
> tested, and has now succeeded twice end-to-end as `commands/ceiling-revisit.md` +
> `scripts/ceiling_revisit.py` + `scripts/fetch_scorecard.py` (no new capability needed), should
> this spec again simply document "no new code — the implement phase directly executes
> `commands/ceiling-revisit.md`'s Phases 1-5 with resolved parameters," mirroring #294's spec
> exactly? And on dates: should `UNTIL` be corrected from the issue body's stale `2026-08-07` to
> today's actual refine-execution date `2026-08-14` (with `NEXT_DATE` = `2026-08-21`), following
> the same "UNTIL = actual execution date, not the frozen literal" rule #294's spec established —
> while flagging in Assumptions that if implement runs on a later date, it should re-derive UNTIL
> as its own execution date rather than reusing this spec's literal?
>
> **A1:** Yes to both, with one addition. Zero-code spec confirmed — the implementation
> (`commands/ceiling-revisit.md` Phases 1-5, both scripts) is unmodified since 27c890b and
> unit-tested; the spec's only job is to be a single correct source of truth for this run's
> parameters plus known execution gotchas, pointing at the command file as authoritative rather
> than duplicating its mechanical detail. Keep the "Corrections to the Issue Body" table and the
> `.archon/.env` gitignored / `git add -f` guard from #294's spec — that's precedent, not
> rediscovery, and only fires if a keyword change is warranted (not the expected path, since no
> `.archon/.env` exists and every prior run found "insufficient data"). Dates confirmed:
> `ISSUE_NUM=332 SINCE=2026-06-12 UNTIL=2026-08-14 NEXT_DATE=2026-08-21`, with the Assumptions
> clause carried forward exactly as #294's implement run correctly exercised it (shifting
> 2026-07-28 → 2026-07-31 when a gap accumulated). **Addition:** #294's run already filed #331
> (L+XL at 100%/n=6, still open) for the L/XL-bucket observation, and `commands/ceiling-revisit.md`
> Phase 4 has no duplicate-detection guard — it calls `gh issue create` unconditionally whenever
> `L_NEEDS_ISSUE` is true. With another week of likely-still-100% data, this cycle will probably
> re-trigger that flag. The spec should require the implement agent to check for an existing open
> issue matching the title pattern before filing, and skip (noting the existing issue in the
> analysis comment instead) if one is already open. A permanent fix baked into the command file
> itself would be a separate spillover ticket, not this one.

> **Q2:** Given that #294's plan/implement cycle already burned effort rediscovering that
> `commands/ceiling-revisit.md`'s `# TARGET-PATH`-marked `dark-factory/scripts/...` lines resolve
> to unprefixed `scripts/...` in this self-target repo (confirmed via `git ls-files`, and already
> recorded as a `[PROVISIONAL]` memory entry pending cross-run confirmation) — should this spec's
> Architecture section explicitly state the corrected, unprefixed script paths as the paths the
> implement phase should invoke, so the plan phase doesn't copy the vendor-prefixed lines verbatim
> again? Or is it out of scope for refine to correct command-file references, and should this
> instead just be flagged as a note for implement to handle itself again?
>
> **A2:** Yes — state the unprefixed paths directly in Architecture; the plan phase is what copies
> the `# TARGET-PATH` block verbatim, and plan reads the spec, so Architecture is the only place
> that actually prevents a third repeat. Stating a resolved invocation path is documentation, not
> a command-file edit, so it's squarely in refine's scope. **But the existing `[PROVISIONAL]`
> memory entry's stated rationale is itself wrong and should not be propagated as-is**: direct
> investigation of `entrypoint.sh` (lines 568-572) shows the `dark-factory/`-prefixed path *does*
> exist and *does* execute at runtime in a self-target clone — it's the image-baked
> `/opt/dark-factory/scripts` copy, present on disk and git-excluded so it can never be committed
> back, not absent. `git ls-files` returning 0 entries under `dark-factory/` only proves
> untracked, not absent. The correct reason to prefer the unprefixed path is drift-safety, not
> nonexistence: `scripts/*.py` is the tracked canonical source a branch can modify, while
> `dark-factory/scripts/*.py` is a frozen snapshot that also gets wiped by
> `git clean -fd dark-factory/ .claude/` in the deconflict flow (`entrypoint.sh:694`). Do not have
> the spec touch `commands/ceiling-revisit.md` itself — the prefix is correct there for the
> MarketHawk instance, and any change to a phase command file needs its own reviewed ticket. On the
> memory entry: this cycle is the second confirmation of the underlying pattern (prefer unprefixed
> paths), so implement should re-record `codebase-patterns.md`'s entry with the corrected
> rationale rather than promote the current "path does not exist" wording as-is — that's a normal
> implement-phase memory-write responsibility, not a scope violation.
