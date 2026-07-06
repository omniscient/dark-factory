# Raise doc-slicing component-resolution hit-rate (issue #18)

## Overview

Architecture-slice component resolution (`scripts/architecture_slice.py::infer_component`) only
resolves a component for **22.7%** of in-scope scenario-results in the 2026-07-03 eval corpus
(15/66 across `refine`/`plan`/`implement`; `conformance`/`code-review` are unrouted today and
are covered by sibling issue #19, out of scope here). Every unresolved case falls back to the
full `ARCHITECTURE.md`, so the token-optimization savings that architecture slicing exists to
deliver (~50-62% on the sections that do resolve) never materialize for roughly 3 out of 4
issues.

Today `infer_component()` checks three signals, in order: (1) `changed_files` path prefixes,
(2) `spec_file` basename keywords, (3) issue `labels`. At `refine` and `plan` time there is no
diff yet and (per this ticket's investigation) no consumer of the pipeline currently threads a
spec-declared component through, so in practice only the label signal ever fires — and most
issues aren't labelled with a component. The eval harness (`evals/token_opt_eval.py`) mirrors
this: `eval_issue_scenario()` never passes `changed_files` or `spec_file`/`spec_component` to
the resolver for any scenario, only `labels`. This ticket adds a fourth signal — component
inference from the issue's own title/body text — since manual inspection of the corpus shows
most unresolved issues literally name a path or component keyword in their title even when
unlabelled (e.g. "...(frontend/src/utils/indic...)", "...into app/uti[ls]...",
"cleanup(grafana): ...").

## Requirements

Distilled from the issue and Q&A (see the published comment for the full dialogue):

1. Add a new, lowest-confidence inference signal to `infer_component()`: derive a component
   from the issue title + body text. This is the only candidate (of the issue's four) that can
   move the number the acceptance criterion actually measures, because the eval harness only
   ever exercises `labels` and issue JSON (title/body) today.
2. The new signal must rank **after** `changed_files`, `spec_file`, and `labels` in the
   resolution order — those signals are unchanged and still win when present.
3. The new signal is itself two ordered sub-tiers, evaluated on the lowercased `title + "\n" +
   body`:
   - **Tier A (path-shaped):** literal substring match against the exact prefixes already in
     `_FILE_PREFIX_MAP` (`backend/app/`, `frontend/src/`, `dark-factory/`, `docker-compose`) —
     the same list `changed_files` matching uses, so the two signals cannot diverge.
   - **Tier B (bare keyword):** only consulted if Tier A matches zero components. Tokenize the
     text (word-boundary split, consistent with the existing `_SPEC_KEYWORD_MAP` tokenization
     approach used for spec-filename slugs) and intersect the token set against the existing
     `_SPEC_KEYWORD_MAP` frozensets. No new keyword vocabulary — reusing the existing map keeps
     this inside candidate #1's scope (label-vocabulary enrichment is a separate, deferred
     candidate).
4. **Ambiguity rule:** within a tier, if the match set spans more than one distinct component,
   that tier resolves to "no match" (fall through to the next tier, or to full-doc if it was
   Tier B) — never guess and never break the tie by falling through to a weaker tier. This is a
   deliberate departure from the existing signals' first-match ordering: those signals read
   structured, ordered inputs where order carries intent; free-form issue prose has no such
   ordering, so a text mention of two components is genuinely ambiguous.
5. Safety invariants are unchanged: the existing safety-keyword/safety-path fallback check
   (`_check_safety_fallback`) still runs after component resolution (regardless of which signal
   resolved it) and can still force a full-doc fallback. Section-presence safety verdicts must
   stay 100% pass and `section_at_risk` must stay 0% at current budgets.
6. Plumb the issue title/body text through the existing call chain so the eval harness (and
   real DAG runs) actually exercise it: `infer_component()` → `slice_architecture()` →
   `assemble_pack()` (`context_pack.py`) → `context_budget.py` CLI → `evals/token_opt_eval.py`.
   `assemble_pack()` already reads the issue body separately (for the `issue_context` section)
   from the same `issue_json` path already passed in on every call site — no new CLI inputs are
   required at the DAG or eval-harness level, only routing the value that's already loaded.
7. Acceptance: component-resolution hit-rate ≥60% on the eval corpus (from 22.7%), measured by
   re-running `evals/token_opt_eval.py`, with safety verdicts unchanged (100% pass / 0%
   `section_at_risk`).

## Architecture / Approach

### Resolution order (new)

```
changed_files prefixes → spec_file keywords → labels → issue-text paths (Tier A)
  → issue-text keywords (Tier B) → None (full-doc fallback)
```

### Code changes

- **`scripts/architecture_slice.py`**
  - Add `infer_component_from_text(text: str) -> str | None` implementing Tiers A/B and the
    ambiguity rule, reusing `_FILE_PREFIX_MAP` and `_SPEC_KEYWORD_MAP` verbatim.
  - Extend `infer_component()`'s signature with an `issue_text: str | None = None` parameter;
    call the new helper as step 4, after the existing label check, only when steps 1-3 found
    nothing.
  - Extend `slice_architecture()` with the same `issue_text` parameter, threaded into its
    `infer_component()` call. The safety-fallback check (step 2 in `slice_architecture`,
    currently after component resolution) is untouched — it already runs regardless of how
    `component` was resolved.
  - Update the CLI (`main()`) with an `--issue-text` argument (or `--issue-title`/`--issue-body`
    — pick whichever is more consistent with existing flag naming in this file; either way, the
    value flows into the same parameter).
- **`scripts/context_pack.py`** (`assemble_pack()`): the function already parses `issue_json`
  for the `issue_context` section via `_read_issue_context`. Extract `title` + `body` once at
  the top of `assemble_pack()` (or reuse the existing body-read helper) and pass the combined
  text into the `architecture_md` section's `aslice.slice_architecture(...)` call as the new
  `issue_text` argument.
- **`scripts/context_budget.py`**: thread the same value through in the same way (it wraps
  `context_pack`/`architecture_slice` similarly — check the existing `spec_component`/`labels`
  plumbing added by the adapter-driven components work for the exact pattern to mirror).
- **`evals/token_opt_eval.py`**: `_run_assemble()` / `eval_issue_scenario()` already have the
  full issue dict (`fetch_issue()` pulls `number,title,body,labels`) and already serialize
  `title`+`body` into `issue.json` via `build_issue_json()`. No new GitHub calls are needed —
  `assemble_pack()` will pick up title/body from the same `issue_json` path already passed in.
  Verify (via a test or a manual eval dry-run) that the "optimized" run's `component` field
  actually changes for previously-unresolved corpus issues.
- **Tests**: extend `tests/test_architecture_slice.py` with cases for Tier A (path substring),
  Tier B (keyword token match), the ambiguity fallback (two components mentioned → `None`), and
  precedence (existing `changed_files`/`labels` signals still win over text when both present).

### Non-goals (explicit, deferred to follow-up tickets)

- **Label vocabulary enrichment** (candidate #2 in the issue) — adding new component labels to
  the triage vocabulary and teaching `refine` to self-apply them. Valuable long-term
  (self-reinforcing) but doesn't move this ticket's eval-measured acceptance number, since it
  would require re-labelling the existing eval corpus.
- **Spec-stage `spec_component` propagation** (candidate #3) — the `spec_component` parameter
  already exists end-to-end (`architecture_slice.py`, `context_budget.py`, `context_pack.py`,
  `token_opt_eval.py` all accept it), but nothing populates it: no spec written in this repo has
  a component header field yet, and no DAG node passes `--spec-component`. Wiring this requires
  a spec-format change plus per-stage read logic — a materially larger, separate change that
  also would not move today's eval measurement.
- **Multi-component union slices** (candidate #4) — issues touching two components currently
  fall back entirely; a union slice would still beat full-doc but adds complexity beyond a
  contained, single-family-of-files change. Deferred.
- **Linked-PR scanning** — the issue's candidate #1 wording mentions "issue body / linked PRs /
  spec." Following `fixes #N` / `PR #N` references and fetching that PR's diff or description
  is a materially different shape of change (`infer_component()` is currently a pure function
  with no I/O; this would require new `gh api` calls and plumbing to fetch PR data before
  resolution runs). Deferred — likely the single strongest follow-up, since a linked PR's real
  diff is a `changed_files`-equivalent signal, but out of scope for this M-sized ticket.
- **Writing this repo's own `ARCHITECTURE.md`** — the self-target adapter
  (`.factory/adapter.yaml`) has `components: {}` because no `ARCHITECTURE.md` exists yet for
  dark-factory itself, so slicing will keep falling back to full-doc for dark-factory's own
  future issues regardless of this ticket. The eval corpus being moved by this ticket is
  MarketHawk's (which does have an `ARCHITECTURE.md` and populated components map). Writing
  dark-factory's own architecture doc + components map is an unrelated prerequisite for a future
  ticket, not something this one can address.

## Alternatives Considered

1. **Chosen: issue-text tiered inference (Tier A path / Tier B keyword), lowest confidence,
   ambiguity → fallback.** Only candidate that moves the eval-measured number without also
   rewriting the eval harness or re-labelling history; contained to one family of files;
   preserves the "fail toward more context" safety invariant via the ambiguity rule.
2. **Label vocabulary enrichment first.** Rejected for this ticket: doesn't affect the existing
   22-issue eval corpus (their labels are fixed history), so it can't clear this ticket's
   acceptance gate on its own. Good complementary follow-up for *future* issues.
3. **Full `spec_component` propagation.** Rejected for this ticket: the wiring exists but is
   unpopulated; populating it needs a spec-format change (component header) and per-stage read
   logic in `plan`/`implement`/`conformance` — a larger, separate change, and it doesn't touch
   `refine`-time resolution at all (no spec exists yet at refine time).
4. **Flat single-tier text matching (no path/keyword split, first-match-wins like the existing
   signals).** Rejected: conflates a strong signal (an unambiguous literal path) with a weak one
   (a common English word also in `_SPEC_KEYWORD_MAP`), and reusing first-match-wins on
   unordered prose text would silently pick a semi-arbitrary "first" component whenever two are
   mentioned — violating "wrong-component resolution must fail toward MORE context, never less."

## Open Questions (non-blocking)

- **Bare `app/` paths:** a spot-check simulation against 10 of the 22 corpus issues (the
  `bench/suite.json` subset; the other 12 supplemental issue numbers no longer resolve via `gh`
  in either `omniscient/markethawk` or `omniscient/dark-factory` — likely renumbered or moved
  during the P3 extraction) showed the tiered text signal lifting resolution from 1/10 (10%) to
  4/10 (40%) on that subsample. Two of the six still-unresolved issues in the subsample
  (`#286`, `#632`) name backend paths as bare `app/utils/...` rather than `backend/app/...`
  (the literal `_FILE_PREFIX_MAP` prefix) — MarketHawk issue prose commonly omits the
  `backend/` service-root prefix. Reusing the existing prefix list verbatim (per this spec's
  scope decision) will under-catch these. A fast, low-risk follow-up tweak (add a bare `app/`
  → backend variant to Tier A) could close much of this gap, but is deferred here to keep the
  matching signal identical to what `changed_files` uses, per this ticket's explicit scope
  decision. Recommend re-running `token_opt_eval.py` after landing this ticket and revisiting
  this specific gap immediately if the full 22-issue hit-rate lands short of 60%.
- Whether the CLI flag added to `architecture_slice.py`/`context_budget.py` should be named
  `--issue-text` (single combined value) or separate `--issue-title`/`--issue-body` flags —
  left to implementation, should follow the nearest existing naming convention in each file.

## Assumptions (flagged)

- The full 22-issue eval corpus could not be completely re-verified against this design during
  refinement: 10 of 22 issue numbers (the `bench/suite.json` subset) were fetched and simulated
  directly; the other 12 (`SUPPLEMENTAL_SCOPE_SPILLOVER` + `SUPPLEMENTAL_FACTORY_REGRESSION`
  issue numbers) no longer resolve via `gh issue view` in either `omniscient/markethawk` or
  `omniscient/dark-factory` as of this writing, so their titles/bodies were not available for
  simulation. The design decision is based on the resolvable subsample plus manual inspection of
  titles recorded in the 2026-07-03 eval results JSON for the rest.
- `_read_issue_context`'s existing blank-line-prefix guard (to prevent untrusted issue body
  content from injecting a spurious `## ` section header into the assembled prompt) is assumed
  to be irrelevant to the new signal, since the text is only used for substring/token matching
  inside `infer_component_from_text()`, never re-emitted verbatim into a prompt section by this
  code path.
- No changes to `.factory/adapter.yaml` or any adapter-driven `components:` map are needed —
  this ticket only changes *how* a component is inferred, not the component→section map itself.
