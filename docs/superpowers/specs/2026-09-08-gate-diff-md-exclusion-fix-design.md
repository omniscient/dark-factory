# Fix Gate 2/3 diff construction: `*.md` blanket exclusion hides executable-policy changes

**Issue:** #399

---

## Overview / Problem Statement

`commands/dark-factory-conformance.md` (Gate 2) builds the reviewer's diff artifact with a
blanket `':!*.md'` pathspec exclusion. For #394, three substantive files
(`commands/dark-factory-conformance.md`, `commands/dark-factory-plan.md`,
`refinement-skills/VERIFIER-CONTRACT.md`) were all `.md` and were entirely absent from
`$TRIAGED_DIFF` / `$ARTIFACT_CONTENT` — the conformance reviewer never saw them. Verified
independently while writing this spec: `commands/dark-factory-code-review.md` (Gate 3) carries
the exact same `':!*.md'` exclusion — its own inline comment even says "Build the review diff
with the SAME pre-triage exclusions the conformance gate uses" — so Gate 3 has the identical
blind spot, not just a suspected one.

Command files (`commands/*.md`), refinement-skill prompts (`refinement-skills/*.md`), Claude
Skills (`.claude/skills/**`), factory workflow config (`.archon/**` minus generated memory), and
the DAG (`workflows/**`) are all `.md`/YAML-shaped **executable policy** — phase agents follow
them verbatim. A blanket `.md` exclusion makes exactly the changes most in need of conformance
and code review invisible to both gates.

Confirmed empirically (not just asserted) while researching this spec: the issue's literal
suggested fix — "keep the exclusion only for prose... and always include `commands/**`,
`refinement-skills/**`, etc. regardless of extension" — **cannot be expressed as a single git
pathspec.** Git's exclude-magic (`:!pattern`) unconditionally removes a matching path from the
diff's selected set regardless of any positive pathspec that also matches it:

```
$ git diff --cached --name-only -- ':!*.md' 'commands/**'
(empty — commands/x.md is dropped even though it matches the positive pathspec too)
```

So "blanket-exclude `.md`, then re-include five policy directories" is not achievable as written;
the fix has to change what gets excluded in the first place, not add a carve-out on top.

## Requirements

1. Gate 2 (`commands/dark-factory-conformance.md`) and Gate 3 (`commands/dark-factory-code-review.md`)
   both stop excluding `commands/**`, `refinement-skills/**`, `.claude/skills/**`, `.archon/**`
   (except `.archon/memory/**`, which stays excluded — it's agent-written churn, not reviewable
   policy), and `workflows/**` `.md`/config content from their reviewer diff artifacts, regardless
   of extension.
2. The `.md` exclusion is retained only for genuine prose/generated-report content: `docs/**`
   (specs, plans, archive, hotspots/schema docs — already separately excluded and now subsumed),
   `evals/**` (scorecards, baselines, rubrics — measurement artifacts, not phase-agent policy),
   and `bench/**` (benchmark baselines — same category).
3. All three existing exclusion call sites are fixed identically (same pathspec logic in all
   three); fixing one and not the others reintroduces exactly the blind spot this ticket exists to
   close:
   - `commands/dark-factory-conformance.md:126` (Step 3.0.1, `RAW_DIFF`)
   - `commands/dark-factory-conformance.md:460` (Phase 3.5 reconcile-loop diff refresh)
   - `commands/dark-factory-code-review.md:63` (Phase 2, `review_diff.txt`)
4. Conformance's Step 3.6.0 doc-exemption guard (`commands/dark-factory-conformance.md:337`,
   `grep -qiE '\.md([^a-z0-9]|$)|(^|[^a-z])docs/'`) is narrowed so it no longer treats *any* `.md`
   file as an automatically-in-scope "doc change." See Requirement 4 rationale below — this is a
   necessary companion to Requirement 1, not scope creep.
5. A new test proves the issue's literal acceptance criterion: a diff touching only
   `commands/*.md` yields a non-empty reviewer artifact under the new pathspec. A second,
   cheaper test statically locks the pathspec tokens in all three command-file call sites so a
   future edit can't silently reintroduce `':!*.md'`.
6. No change to `.archon/commands/` (the gitignored, per-run fallback copy seeded from the baked
   image at container start — see Assumptions) and no new shared script/`gate_lib.sh` addition —
   the fix is a literal pathspec/regex edit at each existing call site, matching how the two gates'
   duplicated exclusion logic is maintained today.

### Requirement 4 rationale (why the Step 3.6.0 exemption must be narrowed too)

Step 3.6.0 exists to stop the factory from excising or filing spillover tickets for the
routine doc-map updates that `dark-factory-implement.md`'s Phase 4 (DOCUMENT) makes as a normal
side effect of code changes (`ARCHITECTURE.md`, `PROJECT_STRUCTURE.md`, `ENV_VARIABLES.md`,
`CLAUDE.md` — the exact table at `commands/dark-factory-implement.md:204-215`). Its current regex
is not scoped to that table; it exempts *any* path containing `.md`. Shipping Requirement 1 alone
would mean: the conformance reviewer can now see an out-of-scope edit to
`commands/dark-factory-plan.md` and correctly emit `[OOS] commands/dark-factory-plan.md — ...`,
but Step 3.6.0 would then silently classify it as an exempt "doc change" and never excise it or
file a spillover ticket — a detected-but-discarded finding, which is a worse and more confusing
state than today's "never detected at all." Narrowing the regex to the actual doc-map filename set
plus `docs/` is a small, targeted change that makes the two mechanisms (visibility, enforcement)
consistent again. This *strengthens* scope enforcement (fewer paths are exempt from
excision/ticketing) — under CLAUDE.md's hard limit against weakening safety gates, a strengthening
change is the opposite concern and does not need a separate ticket; it's the same policy-visibility
bug manifesting a second time in an adjacent mechanism, so it belongs in this ticket.

---

## Brainstorming Q&A

> **Q:** I've verified `commands/dark-factory-code-review.md` (Gate 3) has the identical `':!*.md'`
> bug as Gate 2, confirmed by reading the file, not just suspected as the issue's "check whether..."
> phrasing implies. Given this ticket is labeled size: S, should the fix cover both Gate 2 and Gate 3
> in one spec/implementation, or should Gate 3 be spun out into its own follow-up ticket?
>
> **A:** Cover both in this ticket. The issue body already scopes the Gate 3 check in ("Check
> whether... has the same exclusion (Gate 3)"), and closing this ticket having *confirmed* an
> identical blind spot in the gate that actually blocks/advises PRs, without fixing it, would be
> worse than not having checked. It stays size: S because the fix is the same pathspec edit applied
> to a handful of literal blocks — not two designs. `dark-factory-code-review.md`'s own comment
> ("the SAME pre-triage exclusions the conformance gate uses") declares Gate 2 the source of truth;
> editing one and not the other actively breaks that stated invariant.

> **Q:** [Verification round, prompted by an initial answer's claim that `.archon/commands/` is a
> second live copy needing edits too.] I independently checked this against the repo: `git ls-files
> .archon/commands/` returns nothing (untracked); `git check-ignore -v` traces the exclusion to
> `.git/info/exclude` (a local, non-versioned file), not any committed `.gitignore`; `entrypoint.sh`'s
> own comment on that block reads "Self-contained fallbacks: target repos without in-repo factory
> files... Copy baked factory pieces into the clone ONLY where the target repo does not provide
> them... appended to `.git/info/exclude` so it can never be committed back"; `Dockerfile:139` shows
> `COPY commands/ /opt/dark-factory/commands/` (baked image built directly from this repo's tracked
> `commands/`); and `CLAUDE.md` names `commands/*.md` only, with no mention of `.archon/commands/`
> anywhere. Is the correct, complete fix scope editing `commands/dark-factory-conformance.md` and
> `commands/dark-factory-code-review.md` at repo root only, with no `.archon/` edits or sync test?
>
> **A:** Confirmed — the initial claim inverted cause and effect. `.archon/commands/` exists in a
> running container only because this exact fallback block created it (the `[ ! -d ]` guard is true
> on every fresh clone since the directory is untracked); it is recreated from the baked image every
> run, so it is the fix's *output*, never its *input*. The propagation path is
> `commands/*.md` → `Dockerfile:139` → baked image → `entrypoint.sh`'s fallback copy →
> `.archon/commands/`, and `.github/workflows/publish.yml` rebuilds the image on merge to `main`, so
> editing `commands/*.md` is both necessary and sufficient — nothing is inert. A sync test between
> `commands/` and `.archon/commands/` would be actively wrong: a CI checkout has no `.archon/commands/`
> at all (untracked), so the test would either no-op or fail spuriously, encoding the same
> misconception. Also corrected: the conformance file has **two** occurrences of the exclusion
> (`:126` and a second, later one at `:460` in the Phase 3.5 reconcile-loop diff refresh), not one —
> missing `:460` would leave a partial fix in the very gate the ticket is about.

> **Q:** I ran a real git experiment confirming a blanket `':!*.md'` exclude cannot be selectively
> overridden by a positive pathspec for specific directories (git's exclude-magic wins unconditionally).
> Given no new shared script (per your earlier answer — keep edits literal, no `gate_lib.sh` addition),
> should the fix be (A) a dynamic per-run computation that lists the diff's changed `.md` files and
> filters out the ones under the five policy directories named in the issue, passing the remainder as
> individual excludes, or (B) a small static list of known non-policy `.md` locations (`docs/`, etc.)
> to exclude instead of the blanket token?
>
> **A:** Neither as literally framed — (A) is provably redundant (a second pathspec list listing the
> policy directories as *positive* patterns, unioned with the existing exclude-only diff, produces the
> identical byte set with zero bash logic, so the dynamic computation buys nothing), and more
> importantly **both (A) and a naive 5-directory allowlist share the exact failure mode #399 exists to
> fix**: silent invisibility for anything outside the enumerated allowlist. Concretely,
> `tests/fixtures/verdicts/` holds 18 `.md` files that are live, behavior-bearing test fixtures
> (loaded by `tests/test_verdict.py:78`, `_FIXTURES_DIR`) — not policy, not prose, but also not under
> any of the five named directories. An allowlist misses this category on day one; it will miss the
> next one too. Recommendation: invert to a **blacklist of the repo's actual generated/prose trees**
> — `':!docs/*.md' ':!evals/*.md' ':!bench/*.md'` — which is a single static token per tree (git's
> default pathspec magic lets `*` match `/`, so `docs/*.md` matches arbitrarily nested paths, e.g.
> `docs/superpowers/specs/foo.md`), subsumes the two existing docs-specific excludes
> (`docs/codeindex-hotspots.md`, `docs/database-schema.md`), and its failure direction is now
> "a future prose directory leaks into the diff as noise" (visible, self-correcting) instead of
> "a future policy file goes invisible" (the actual bug class). Spot-checked `bench/baseline.md` and
> two `evals/**/*.md` files directly — confirmed generated report/scorecard content, not authored
> policy, same category as the already-excluded `docs/database-schema.md`.

---

## Architecture / Approach

**Pathspec change**, applied identically at all three call sites:

Replace
```
':!*.lock' ':!*.md'
```
with
```
':!*.lock' ':!docs/*.md' ':!evals/*.md' ':!bench/*.md'
```

At `commands/dark-factory-conformance.md:126` this also drops the now-redundant
`':!docs/codeindex-hotspots.md'` / `':!docs/database-schema.md'` tokens (subsumed by
`':!docs/*.md'`); `':!.archon/memory/**'`, `':!codeindex.json'`, `':!symbolindex.json'` are
unaffected and stay as-is. At `:460` and `commands/dark-factory-code-review.md:63`, the same
three-token substitution applies in place of the bare `':!*.md'`.

Net effect, verified against the current repo tree (`git ls-files`): of 225 tracked `.md` files,
171 sit under `docs/**` (specs/plans/archive/etc., correctly stay excluded) and the remaining ~54
now become visible to both gates, including every `commands/*.md`, `refinement-skills/*.md`,
`.claude/skills/**/*.md`, `.archon/memory/*.md` (this one intentionally re-excluded separately),
root `CLAUDE.md`/`README.md`, and the `tests/fixtures/verdicts/*.md` fixtures the allowlist
approach would have missed.

**Step 3.6.0 exemption narrowing** (`commands/dark-factory-conformance.md:337`), replacing
```
grep -qiE '\.md([^a-z0-9]|$)|(^|[^a-z])docs/'
```
with
```
grep -qiE '(^|/)(ARCHITECTURE|PROJECT_STRUCTURE|ENV_VARIABLES|CLAUDE)\.md([^a-z0-9]|$)|(^|[^a-z])docs/'
```
This keeps `docs/**` (specs/plans, always in-scope per existing convention) and the four
implement-Phase-4 doc-map targets exempt from excision/ticketing, while an OOS finding against
`commands/*.md`, `refinement-skills/*.md`, `.claude/skills/**`, or `README.md` now flows through
to excision/spillover-ticket handling like any other code file.

**No shared script.** Both gates already duplicate this exclusion list inline and are documented
as intentionally kept in sync by comment (`dark-factory-code-review.md`'s "the SAME pre-triage
exclusions" note); this fix preserves that pattern rather than introducing a new shared helper.
`scripts/gate_lib.sh` explicitly reserves itself for "only the three shared primitives" — adding
pathspec-construction logic there was considered and rejected (see Alternatives).

**Tests:**

- New `tests/test_gate_diff_md_visibility.sh` (behavioral, matching the `tests/test_close_preview_teardown.sh`-style pattern of exercising real `git` commands against a temp repo): builds a
  throwaway git repo, commits a baseline, then modifies only a `commands/*.md`-analog file plus a
  `docs/*.md`-analog file, and asserts `git diff main...HEAD -- ':!*.lock' ':!docs/*.md'
  ':!evals/*.md' ':!bench/*.md'` (the new pathspec) is non-empty and contains the commands-file
  change but not the docs-file change — directly proving the issue's stated acceptance criterion.
  Wired into `.github/workflows/ci.yml`'s explicit per-file list (new safety/observability-relevant
  coverage should not join the existing pile of unwired `.sh` tests, matching the precedent set in
  `docs/superpowers/specs/2026-07-23-budget-gate-consolidation-design.md`).
- New static assertions (extending `tests/test_conformance_formatter_step.py` or a sibling
  `tests/test_gate_diff_pathspec_tokens.py`, auto-discovered by `python -m pytest tests/ -v`, no
  `ci.yml` change needed): assert `':!*.md'` no longer appears literally in
  `commands/dark-factory-conformance.md` or `commands/dark-factory-code-review.md`, and that
  `':!docs/*.md'` appears at all three call sites — a cheap regression lock against a future edit
  silently reintroducing the blanket token.
- One static assertion that the narrowed Step 3.6.0 regex no longer bare-matches an arbitrary
  `.md` filename (e.g. `commands/dark-factory-plan.md` should not match the exemption pattern,
  while `ARCHITECTURE.md` and `docs/superpowers/specs/foo.md` should).

---

## Alternatives Considered

1. **Blanket-exclude `.md`, then positive-pathspec re-include the five policy directories** (the
   issue's literal wording). Rejected — proven empirically impossible: git's exclude-magic
   (`:!pattern`) unconditionally removes a matching path from the diff's selected set regardless of
   any positive pathspec also matching it (`git diff -- ':!*.md' 'commands/**'` returns empty, not
   the policy files). No such single pathspec exists.
2. **Dynamic per-run allowlist**: at each call site, list the diff's changed `.md` files, filter to
   those under the five policy prefixes, and pass the remainder as individual `':!<path>'` excludes.
   Rejected — mathematically equivalent to a second static pathspec listing the policy directories
   as positive patterns (same byte-identical result, zero added logic needed), and, more
   importantly, shares the enumerated-allowlist failure mode: any `.md` content outside the five
   named directories (e.g. `tests/fixtures/verdicts/*.md`, live test fixtures) stays invisible,
   reproducing the exact bug class #399 exists to close.
3. **Static five-directory positive allowlist** (`commands/**`, `refinement-skills/**`,
   `.claude/skills/**`, `.archon/**`, `workflows/**`) as the sole always-visible set. Rejected for
   the same reason as #2 — confirmed via `git ls-files` that `tests/fixtures/verdicts/` (18
   behavior-bearing `.md` fixtures) sits outside all five directories and would remain invisible.
4. **Extract the exclusion pathspec into a new shared script** (e.g. `scripts/diff_pathspec.sh`)
   called by both `dark-factory-conformance.md` and `dark-factory-code-review.md`, so a future fix
   can't apply to only one gate again. Rejected for this ticket — `scripts/gate_lib.sh`'s header
   comment explicitly reserves it for three named primitives, and introducing new shared gate
   plumbing is a larger, separately-reviewable change than a size: S bug fix calls for. The existing
   pattern (duplicated inline, kept in sync by an explicit code comment) is preserved; consolidating
   it is left as an optional follow-up if the duplication proves troublesome again.
5. **Fix only the pathspec (Requirement 1-3), leave Step 3.6.0's exemption regex untouched.**
   Rejected — this would ship visibility without enforceability: the conformance reviewer would
   correctly flag an out-of-scope `commands/*.md` edit as `[OOS]`, but the unchanged exemption guard
   would silently drop that finding before excision/ticketing, producing a detected-but-discarded
   gap that is more confusing than today's never-detected status quo.

---

## Open Questions (Non-blocking)

- Whether `evals/*.md` and `bench/*.md` should eventually get more granular treatment (e.g. keeping
  `evals/behavioral-state/rubric.md` visible since a rubric change is closer to policy than a
  generated scorecard) — deferred; today both directories hold only generated
  reports/scorecards/baselines, so a single-token exclusion for each is proportionate, and this can
  be revisited if evals/bench ever grows authored, reviewable prose.
- Whether the `.archon/commands/` vs. `commands/` split (fallback-copy convenience for target repos
  lacking their own `commands/` dir) deserves its own documentation note in `CLAUDE.md`'s repo map —
  useful context surfaced during this ticket's research, but orthogonal to the diff-exclusion fix
  itself.

---

## Assumptions

- `.archon/commands/` is never a fix target: it is an untracked, `.git/info/exclude`-gitignored,
  per-container-run fallback copy that `entrypoint.sh` seeds from the baked image (itself built from
  this repo's tracked `commands/*.md` via `Dockerfile:139`) only when a clone lacks its own
  `.archon/commands/` — which is always true on a fresh checkout of this repo, since these files are
  untracked. Editing `commands/*.md` and merging is the complete, sufficient fix path;
  `.github/workflows/publish.yml` rebuilds the image on merge to `main`, and the next dispatched run's
  fallback copy carries the fix forward.
- `evals/**` and `bench/**` contain only generated/measurement content today (spot-checked
  `bench/baseline.md` and two files under `evals/`) — if either directory later gains authored,
  agent-followed policy prose, the blanket per-directory exclusion would need to be revisited (see
  Open Questions).
- The existing `workflows/archon-dark-factory.yaml` budget-telemetry steps
  (`budget-conformance` ~line 966, `budget-code-review` ~line 1188) already run an unrestricted
  `git diff main...HEAD` with no `.md` exclusion at all for token estimation — so widening the
  reviewer diff to include more `.md` content cannot newly exceed a budget threshold that this
  telemetry wasn't already accounting for; no `budget_gate.sh` regression risk from this change.
