# Audit the `commands/*.md` line citations locked at seed by #662

**Issue:** #666

## Overview / Problem statement

#662 (closed; archived at
`docs/archive/2026-11-28-content-aware-docs-citation-check-design.md`) added a
content-addressed lock, `docs/.citation-lock.yaml`, for every citation in
`docs/configuration.md`, `docs/features.md` and `docs/run-lifecycle.md` that points into
a `commands/*.md` file by line number (full path or basename form). It seeded the lock
from `main` as it stood, with `seeded: true` on all 80 entries — i.e. it recorded what
the cited lines said, not whether the citation was *correct*. `scripts/docs_refs_check.py`
has never actually read a seeded entry's content against the sentence it supports.

Spot-checks against current `main` in this refinement confirm the issue's claim that
citations were already wrong at seed time:

- `docs/configuration.md:148` (the `conformance.shadow_model` row) cites
  `commands/dark-factory-plan.md:274-276`; those lines today are inside the
  `render-prompt` memory-context block, not the shadow-model resolution the sentence
  describes.
- `docs/run-lifecycle.md:730` and `docs/features.md:129` both cite
  `commands/dark-factory-conformance.md:208`/`:210` for budget-enforcement /
  diff-ranking consumers; `commands/dark-factory-conformance.md:200-215` today holds the
  diff-rank invocation block and an annotation-ordering note, not obviously either
  claimed consumer line.

This ticket re-derives every seeded entry by content (not just by range), corrects or
drops what is wrong, and converts the 31 bare continuation spans that follow a
`commands/*.md` citation into citations the lock actually covers — so the CI check stops
reporting green over citations nobody has read.

## Requirements (from Q&A)

All requirements below restate or make explicit what issue #666's body already
specifies; the Q&A round settled the one point the issue left open — execution strategy
against the shared lock file.

1. **Per-entry audit, by content.** For every entry currently `seeded: true` (80 at
   issue filing: `docs/configuration.md` 30, `docs/features.md` 28 — 24 basename-form —
   `docs/run-lifecycle.md` 22; re-count against the branch's `main` at plan time, since
   intervening merges can shift these), read the doc sentence, name the concrete thing it
   claims is at the cited lines, read those lines, and:
   - content matches → `python scripts/docs_refs_check.py --accept <doc> <citation>`
     (clears `seeded`, re-locks at current content);
   - content is elsewhere, found at exactly one place → correct the citation's line
     numbers in the doc, cross-check with `git blame` on the doc line against the
     target's history at that commit, then `--accept`;
   - no match, more than one match, or any doubt → do not guess: drop the line number
     (citation becomes path-only), let `--rederive` remove the now-orphaned lock entry,
     and list the case in the PR body (acceptance criterion).
2. **Bare continuation spans** (31 at filing — a colon-prefixed span like `` `:312` `` or
   `` `:312-336` `` with no path, immediately following a `commands/*.md` citation):
   rewrite each as a full `path:N-M` citation using the same three-way test above, then
   `--accept` it so the lock covers it. A span that continues any target other than
   `commands/*.md` is untouched. A span where the continued file is itself uncertain is
   step-1's "any doubt" case: remove the span, list it.
3. **Widen non-unique single-line anchors.** A single-line citation whose cited text is
   not unique within its target file (a blank line, a lone `{`, a lone `fi`, etc.) must
   be widened to a range that *is* unique in that file, so a future `--rederive` can
   relocate it without needing `--base`.
4. **Keep the pre-#551 notes honest.** Where `docs/run-lifecycle.md` carries a
   `(... pre-#551 line numbers)` aside next to a citation, remove the aside once that
   citation is corrected and now points at current line numbers; leave it only where the
   citation still intentionally describes the pre-#551 layout.
5. **Execution strategy (from Q&A):** one sequential pass, docs in the order
   `docs/configuration.md` → `docs/features.md` → `docs/run-lifecycle.md`, with the
   implementing agent as the sole writer of both the docs and the lock file. Do not fan
   out the write path across parallel subagents — `docs/.citation-lock.yaml` is read,
   modified and rewritten whole by every `--accept`/`--rederive` call with no per-entry
   locking, so two writers would clobber each other, and a merge step that touched the
   lock outside the `--accept`/`--rederive` CLI would violate requirement 7 below. A
   read-only subagent may be spawned per citation (or small batch) to do the "where does
   this actually live" search and the `git blame` cross-check, matching this codebase's
   existing pattern of subagents as read-only reviewers whose findings the parent alone
   acts on (`commands/dark-factory-code-review.md:188-193`,
   `commands/dark-factory-conformance.md:294-313`,
   `commands/dark-factory-docs-conformance.md:192-197`) — but only the parent session
   ever edits a doc or invokes the CLI.
6. **Incremental commits for headless survival.** Commit (small, focused, per
   `commands/dark-factory-implement.md:159`) after finishing each doc, or more often
   within a large doc (e.g. every ~10 citations) — never one single commit at the very
   end. Per CLAUDE.md, a turn ending headlessly is a process ending; uncommitted work is
   destroyed. Committing per-doc means a retry resumes from the remaining `seeded: true`
   entries and un-rewritten bare spans instead of re-auditing already-fixed citations.
7. **The lock is only ever touched through the tool.** No hand-edits to
   `docs/.citation-lock.yaml`; every change goes through `--accept` or `--rederive`.
8. **Plan carries the full audit table.** Per the issue's acceptance criteria, the
   *plan* document (not this spec) holds one row per citation: doc line, citation, the
   claimed thing, the text actually found at the cited lines, and the action taken
   (accepted / corrected to `<X>` / line dropped). This spec does not reproduce that
   table — producing it is the plan phase's and then the implement phase's work, not a
   design decision.
9. **Scope fence.** Touch only `docs/configuration.md`, `docs/features.md`,
   `docs/run-lifecycle.md`, `docs/.citation-lock.yaml`. No change to any file under
   `commands/`, `scripts/`, `workflows/` or `.github/`. (Rejecting bare spans in the
   checker itself, and widening the content check to `scripts/**`/`scheduler.sh`/
   `entrypoint.sh`, belong to epic #538 and are explicitly out of scope here.)
10. **Exit condition.** `python scripts/docs_refs_check.py docs/run-lifecycle.md
    docs/scheduler.md docs/features.md docs/configuration.md` exits 0, with zero
    `seeded: true` entries remaining and no bare span left following a `commands/*.md`
    citation.

## Architecture / Approach

No code changes. The "architecture" here is the implement-phase execution plan:

1. **Inventory.** Before auditing, enumerate the current seeded entries
   (`docs/.citation-lock.yaml`, filter `seeded: true`) and the current bare continuation
   spans that follow a `commands/*.md` citation (grep `` `:\d `` / `` `:\d+-\d+` `` spans
   immediately after a `commands/*.md`-targeted span, per doc). This produces the plan's
   audit-table skeleton (requirement 8) and the authoritative counts for this run
   (expected close to 80 + 31, per requirement 1, but re-derived rather than assumed).
2. **Audit loop, per doc, in order (configuration.md, features.md, run-lifecycle.md):**
   for each entry/span in that doc, apply the three-way test from requirements 1–3,
   editing the doc in place and calling `--accept` (or, for a dropped citation,
   `--rederive` to clear the orphan) as each item resolves. Optionally delegate the
   "search + blame" sub-step to a read-only subagent per requirement 5; the parent
   applies the result.
3. **Commit per doc (or per ~10 items within a doc)**, per requirement 6, and push.
4. **Final verification**, once all three docs are done:
   `python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md
   docs/features.md docs/configuration.md` must exit 0 (requirement 10); `grep -c
   "seeded: true" docs/.citation-lock.yaml` must be 0.
5. **PR body** lists every citation whose line number was dropped (requirement 1's
   third branch) and every bare span removed (requirement 2's "any doubt" branch) —
   both acceptance criteria from the issue.
6. **`git diff --stat`** against the merge-base must show only the three docs and the
   lock file (acceptance criterion; also requirement 9).

No memory entry is warranted from this refinement pass (see Phase 5 write-bar check
below) — the one architectural call this round made (sequential, parent-only writes
against the shared lock) is already captured as a requirement in this spec and in the
issue itself, and does not generalize beyond "a phase that must serialize writes to one
shared, whole-file-rewritten artifact," which is not a recurring enough pattern yet to
warrant its own memory entry distinct from the existing reviewer-subagent-pattern
entries already visible in the commands themselves.

## Alternatives considered

1. **Fan out per-doc (or per-citation) write subagents in parallel, then merge.**
   Faster wall-clock, but `docs/.citation-lock.yaml` has no per-entry locking — every
   `--accept`/`--rederive` call reads, modifies and rewrites the whole file. Merging
   concurrent writers' lock changes would require either (a) serializing their
   `--accept` calls anyway (no actual concurrency gain on the part that matters) or (b)
   a bespoke merge step that touches the lock outside the tool's own CLI, which
   requirement 7 (and the issue's "the lock is changed only through the tool") forbids.
   Rejected per the Q&A answer.
2. **One single commit at the end instead of per-doc commits.** Simpler, but violates
   the headless-execution contract's intent: if the run is interrupted or retried after
   auditing two of three docs, all progress is lost and the next attempt re-audits
   everything, including items that were already correct. Rejected in favor of
   requirement 6.
3. **Skip the git-blame cross-check and just trust the search match.** Faster per item,
   but the issue explicitly asks for the blame cross-check (step 1.3) as a sanity check
   against the search being fooled by, e.g., a second near-identical block added later.
   Kept as the issue specifies.
4. **Have the audit also widen the content check to `scripts/**`, `scheduler.sh`,
   `entrypoint.sh` while already in this territory.** The issue explicitly reserves this
   for epic #538 ("almost every factory change edits those files," so the structural fix
   needs an in-run `--rederive` step first). Out of scope; not pursued.

## Open questions (non-blocking)

- The issue's known-wrong table (5 rows) and hand-sample (2 rows) are explicitly a
  "starting point" to confirm, not a pre-approved answer key — the plan/implement phase
  should still run the full three-way test on those 7 entries rather than accepting the
  issue's proposed corrected line numbers unchecked, in case `main` has moved again since
  the issue was filed (2026-11-28).
- The exact counts (80 seeded entries, 31 bare spans, the basename-form breakdown) are
  as measured by the issue author at a stated commit; this spec treats them as
  expectations to re-derive at plan/implement time (requirement 10's inventory step),
  not as fixed targets to hit exactly.

## Assumptions (flagged)

- **Assumption:** `#662` landed exactly as archived (lock schema, `--accept`/`--rederive`
  semantics) with no further changes since; confirmed by reading
  `scripts/docs_refs_check.py` directly on this branch's `main` during this refinement
  pass, not from the archived spec alone.
- **Assumption:** no other document or script outside the three audited docs references
  the lock file's `seeded` count or entry contents in a way this ticket would need to
  update; confirmed by grep (`seeded`, `citation-lock`) across `README.md` and
  `docs/*.md` finding no hits outside the three target docs themselves.
- **Assumption:** the read-only "search + blame" delegation in requirement 5 is an
  optional implementation convenience, not a requirement — a single agent doing the
  whole audit inline, with no subagent delegation at all, equally satisfies every
  requirement in this spec.
