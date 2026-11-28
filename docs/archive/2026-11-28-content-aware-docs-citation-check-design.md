# Content-aware `commands/*.md` line citations: lock file, `--rederive`, advisory-fix hook

**Issue:** #662

Depends on: #38

**Revised:** 2026-11-28 (operator amendment after spec-gate review and an independent second
read with a prototype run on tonight's history: relocation now uses a git base to choose
between identical candidates, `--rederive` never locks changed content, the `audited` flag is
replaced by a `seeded` marker, a missing lock file disables the content check instead of
failing, lock entries are keyed by resolved target and lines, and an in-run pytest check is
added so agents see the failure before the PR; third read: merge order and in-flight
branches, the `docs/run-lifecycle.md` D4 paragraph, entry count, `--accept` visibility)

## Overview / Problem Statement

`docs/configuration.md`, `docs/features.md` and `docs/run-lifecycle.md` cite `commands/*.md`
by backtick path + line number/range (e.g. `` `commands/dark-factory-plan.md:424` ``).
`scripts/docs_refs_check.py` (CI job `dag-check`, `.github/workflows/ci.yml:69`) only verifies
that a cited path exists and the cited line(s) are within the file's current line count. Every
edit to a `commands/*.md` file that inserts or removes lines before a citation's target
silently invalidates that citation, and the range-only check keeps passing.

It has happened repeatedly: #406 (PR #657), #409 (PR #658, where the advisory-fix commit
`d9f118a` re-staled the citations that commit `6968a40` had just fixed) and #405 (PR #664).
Each plan needed a hand-derived "old -> new" citation table.

### Measured state at `origin/main` 4e2b1bf

The checker passes (exit 0) on all four docs. Counts, using the checker's own span parsing and
path resolution:

| Doc | `commands/*.md` citations with a path | of which basename form | bare continuation spans after a `commands/*.md` citation |
|---|---|---|---|
| `docs/configuration.md` | 30 | 0 | 0 |
| `docs/features.md` | 28 | 24 (e.g. `` `dark-factory-plan.md:217` ``) | 8 |
| `docs/run-lifecycle.md` | 22 | 0 | 23 |
| `docs/scheduler.md` | 0 | 0 | 0 |
| **Total** | **80** (84 line spans, counting each part of a comma chain) | 24 | **31** |

All nine `commands/*.md` files are cited. A grep for the literal `commands/` prefix finds only
56 of the 80: the 24 basename citations in `docs/features.md` resolve to `commands/` through
the checker's suffix search and must be covered too.

For context only (out of scope here): the four docs hold 624 path-carrying line citations in
total and 514 colon-prefixed bare continuation spans. A hand check of 55 sampled citations
found 17 pointing at the wrong content (31%): 3 of 26 for `commands/*.md`, 5 of 9 for
`scheduler.sh`, 6 of 7 for `entrypoint.sh`. Widening is a follow-up under epic #538.

### What a prototype measured on tonight's history

A prototype of the relocation rule was run over the four merges 2429106 (#406), 02e99e9
(#409), 7255793 (#404) and 4e2b1bf (#405): docs as they stood before each merge, command
files before and after.

| | citation spans |
|---|---|
| into a command file the PR changed | 108 |
| content unchanged at the same lines (nothing to do) | 55 |
| needed a change | 53 |
| relocated by a unique hash-window match alone | 35 (66%) |
| relocated when a git diff from the base picks between identical candidates | 47 (89%) |
| content inside the range was edited: a person or agent must re-read | 6 (11%) |
| relocated to a place whose content differs from the locked content | 0 |

The 12 that a hash alone cannot place are single lines or short blocks that occur several
times in the file (a `{` line, three identical three-line blocks cited as one comma chain).
For the single advisory-fix commit `d9f118a` the figures are 12 spans, 2 unchanged, 10 moved,
all 10 relocated with the git base (9 without).

Replaying the tool from the two points tonight where citations had just been re-derived by
hand, through to 4e2b1bf, and comparing with the hand-derived docs at 4e2b1bf:

- `commands/dark-factory-plan.md` (12 spans from `6968a40`): 6 equal to the hand result; 5
  where the tool is right and `main` is stale today (`261`, `266`, `274-276`, `217`,
  `238-252` should be `277`, `282`, `290-292`, `233`, `254-268`); 1 edited range left for a
  reader.
- `commands/dark-factory-conformance.md` (25 spans from `58d5800`): 24 equal; 1 where the tool
  faithfully follows a citation that was already off by one line before it started (`586`).

So the mechanism reproduces or beats the hand tables, and it carries an existing wrong
citation along unchanged rather than fixing it. Both facts shape the requirements below.

## Requirements

1. **Lock file.** A checked-in `docs/.citation-lock.yaml`, written by the tool only. One
   entry per distinct `(doc, target, first line, last line)`, where `target` is the resolved
   repo path of a citation into `commands/*.md` (basename forms included) and each part of a
   comma chain is its own entry. An entry stores, in this key order: `doc`, `target`,
   `lines: [first, last]`, `hash` (sha256 of the cited lines, each stripped of leading and
   trailing whitespace, joined with `\n`), `excerpt` (stripped first line, truncated to 80
   characters, for messages only) and, on entries written by `--seed` only, `seeded: true`.
   Entries are sorted by `(doc, target, first, last)`; strings are written as JSON-quoted
   scalars; the file ends with one newline. Two runs on the same tree write the same bytes.
2. **Content check.** `check()` keeps its positional signature and return shape and gains an
   optional `lock_path` keyword. After the unchanged range check:
   - **no lock file at the lock path: the content check is skipped** and behaviour is exactly
     today's. The image carries a copy of the script for target repos that have no lock, and
     callers that pass a bare tree must not start failing. This repo's own lock is protected
     by the test in requirement 9, not by this rule;
   - lock file present: every covered citation part must have an entry (else error), the
     entry's `hash` must equal the current content at the cited lines (else error), and every
     entry must still be cited in its doc (else error: orphan).
   Each error is one line naming doc, doc line, the citation as written, and the exact repair
   command: `python scripts/docs_refs_check.py --rederive --base origin/main <the four docs>`.
   A content mismatch also prints the entry's `excerpt`. The words "verified" and "audited"
   do not appear in any message. The lock path defaults to
   `<repo-root>/docs/.citation-lock.yaml`; `--lock-file` overrides it. Citations into any
   other target stay on the range-only check.
3. **`--rederive [--base REF]` mode** (same script; no model call). The single invariant:
   **`--rederive` only ever writes an entry whose `hash` is unchanged, or an entry for a
   citation that is new prose. It never locks content that differs from what was locked.**
   Per failing citation part:
   - **Moved, unchanged.** Slide a window of the entry's line count over the current target
     and compare hashes. Exactly one match: rewrite that part of every occurrence of the
     citation in that doc to the new line numbers (keeping the path form, full or basename,
     and the rest of a comma chain) and update `lines`. `seeded` is preserved.
   - **Several matches, with `--base REF`.** Take `git diff -U0` of the target from
     `git merge-base REF HEAD` to the working tree. If no hunk touches the locked range, the
     shifted position is the locked first line plus the net line delta of the hunks above it.
     If that position is one of the hash matches, use it. Otherwise, and whenever git or the
     base is unavailable, the citation is ambiguous.
   - **Hand-shifted.** A citation part with no entry, where an orphan entry of the same doc
     and target has a `hash` equal to the content at the cited lines: move the entry to the
     new lines.
   - **New prose.** Citation parts with no entry in a `(doc, target)` group that has no
     orphan left after the steps above: add entries (no `seeded` key).
   - **Removed prose.** Orphans in a `(doc, target)` group that has no unlocked citation
     left: remove them.
   - **Everything else changes nothing and fails**: zero matches; several matches that the
     base cannot separate; a `(doc, target)` group holding both an unlocked citation and an
     orphan (somebody re-pointed a citation by hand at content that is not what was locked).
     For each, print the doc line, the citation, the `excerpt`, and, when the locked content
     is found at exactly one place, `locked content is now at <target>:<lines>`. Exit 1.
   `--rederive` is idempotent, writes docs and lock only after all decisions are made, prints
   each rewritten citation as `old -> new`, and exits 0 only when the check passes afterwards.
4. **`--accept DOC CITATION` mode.** Locks the named citation (all its parts) at the current
   content of its cited lines, adding the entry if there is none, and removes `seeded`. This
   is the only way content that differs from the locked content gets into the lock. There is
   no accept-all. Nothing stops an agent from accepting without reading; what the tool
   guarantees is that every such decision is one explicit command per citation and one changed
   `hash` line in the PR diff, where Gate 2.5 (#535) and the reviewer can see it. To keep
   that visible: `--accept` prints one line per entry it writes
   (`accepted <doc>:<doc line> <citation>: <old excerpt> -> <new excerpt>`), it does nothing
   and exits 1 when the named citation is not in the doc, no other mode and no phase command
   calls it (the Phase 4 step in requirement 8 never does), and a PR that used it lists the
   accepted citations in its body.
5. **`--seed` mode**, used once in this ticket: write an entry with `seeded: true` for every
   covered citation part that has none, at current content. It never overwrites an entry.
   While any entry has `seeded: true` the check prints
   `N citation(s) were locked at seed and have not been read against their target` on stdout;
   that is a note, not a failure. `seeded` means only "content pinned, nobody has checked the
   sentence". It is removed by `--accept` and by nothing else.
6. **No citation is re-derived by hand in this ticket.** The docs' citation numbers are not
   changed here. The lock is seeded from `main` as it stands, so the stale citations are
   pinned with `seeded: true` and stop drifting further; correcting them is the follow-up
   audit ticket.
7. **Bare continuation spans are unchanged in this ticket**: not locked, not rewritten, not
   newly rejected. Consequence, stated plainly: when `--rederive` shifts a path-carrying
   citation, a bare span that continues it (31 exist) is not shifted and goes stale exactly as
   it does today. The audit ticket rewrites them to full citations.
8. **`commands/dark-factory-revise-advisory.md` Phase 4**, between the "no changes" check and
   `git add -u`: when `docs/.citation-lock.yaml` exists in the clone, run
   `python3 scripts/docs_refs_check.py --rederive --base HEAD docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md`.
   The step is fail-open (a non-zero exit is logged and the phase continues to its commit),
   edits only the four docs and the lock file, and is skipped for any clone without the lock
   file (the MarketHawk instance, and any branch cut before this ticket). Its changes are
   tracked files, so they ride in the phase's existing `git add -u` commit. It never edits a
   file under `commands/`, so it does not reopen the post-Gate-2 policy-edit concern recorded
   in #536. The inserted text contains no `$` token at all, so it adds no Archon variable in
   either form and, sitting in Phase 4, cannot move the file's first substituted token
   (`$WORKFLOW_ID` on line 8), which the cache-shape lint from #236 ratchets. It runs after the conformance and docs-conformance gates, so no scope gate sees
   its edits; and files under `docs/` are exempt from conformance scope remediation in any
   case.
9. **In-run signal: a repo-level pytest.** `tests/test_docs_refs_check.py` gains a test that
   runs `check()` on the four real docs with the real lock and asserts the result is `[]`,
   with the checker's own error lines (which carry the repair command) as the assertion
   message, and a second test that asserts the real lock file exists and covers every covered
   citation part. Nothing in a factory run executes the checker today; it runs only in CI
   after the PR exists. The test suite is what implement and validate do run, so this is how
   an agent that edits a command file learns, before the PR, that it must run `--rederive`.
10. **Docs.** Update the checker's module docstring and the CI row of `docs/features.md`
    (`docs/features.md:168`) to name the content check and the `--rederive` command. Update
    the D4 paragraph of `docs/run-lifecycle.md` (`docs/run-lifecycle.md:964-971`): one
    sentence for the new Phase 4 step, and its bare span `` `:60-189` ``, which runs to the
    last line of the command file, is set to the file's new last line. No path-carrying
    citation points into `commands/dark-factory-revise-advisory.md`, so this is the only
    citation number the hook's own insertion moves, and the only one this ticket changes.

## Rollout

- **Main is green on day one by construction.** The lock is generated by `--seed` from the
  PR's own head, so the check passes on the PR and on the merge commit. The implement phase
  runs the CI command locally as its last step and does not hand-edit the lock.
- **No `ci.yml` edit.** The existing `dag-check` line runs the script on the four docs; the
  content check turns on because the lock file exists at the default path. Default exit codes
  are unchanged for a tree without a lock.
- **Image copy.** `Dockerfile` copies `scripts/` and `commands/` into the image. If runs
  take the phase command from the image, the hook takes effect at the next image rebuild and
  nothing changes until then (not verified during refinement). The hook calls
  the clone's `scripts/docs_refs_check.py`, never the image copy, and is skipped when the
  clone has no lock.
- **Scope is what keeps reviewed branches green.** The lock covers the 80 distinct
  `(doc, target, lines)` entries into `commands/*.md` and nothing else. A branch that changes
  no `commands/*.md` file and no covered citation cannot be failed by this ticket, whatever
  else it edits (`scripts/**`, `entrypoint.sh`, `scheduler.sh`, `workflows/**`, other docs).
- **Checked at 4e2b1bf.** The merge trees of the two implemented branches, #236 (head
  `ebe8241`) and #238 (head `64e56d9`, which edits `docs/features.md` and
  `docs/run-lifecycle.md`), hold exactly the same 80 entries with the same hashes as `main`.
  Neither can go red from this ticket in either merge order, so neither needs a new commit or
  a new signature. #652 and #656 have no implementation branch; their spec or plan names no
  `commands/*.md` file.
- **#38 first.** #38's plan edits `commands/dark-factory-refine.md` and shifts six citations
  by hand. It must be merged before this ticket is implemented (`Depends on: #38`), so the
  seed is taken from a `main` that already contains it.
- **Merge precondition for this ticket.** (a) The PR head contains current `main`, and the
  CI command and the test suite pass on that head. If `main` gained a `commands/*.md` change
  after the seed, merge `main` in and run `--rederive --base origin/main`; never delete and
  re-seed. (b) No open PR under a signed hold changes a `commands/*.md` file or a covered
  citation. If one does, it merges first.
- **In-flight branches.** A branch cut before this lands that edits a `commands/*.md` file
  fails CI on its merge ref with the message from requirement 2. One run of the named command
  repairs the two common cases: citations the branch did not touch are relocated, and
  citations it shifted by hand correctly are re-keyed. A branch that shifted a citation by hand to the
  wrong lines (five such citations merged tonight) fails with
  `locked content is now at ...`; that is the check doing its job, and the fix is to write
  those numbers. Nobody but the operator repairs a branch whose run has already ended.
- **Two green PRs that go red together.** PR A shifts a command file; PR B, cut earlier,
  adds a citation into it. Both pass alone and `main` fails after the second merge.
  `--rederive` is global, so the next run of it on any branch repairs `main`'s citations too.
  The main-red fixer cannot do this repair (`main_red_allowed_paths` in `.factory/adapter.yaml`
  has no `docs/`); it is one operator command. The range check has the same exposure today.
- **Merge conflicts in the lock file.** Entries are multi-line and sorted by target and line,
  so branches that edit different command files touch different regions. Two branches that
  shift the same file conflict in the docs and in the lock at the same citations. Resolution:
  take one side for both the doc lines and the lock, then run `--rederive --base origin/main`;
  relocation is by content, so the result does not depend on which side was taken.
- **Hold expectation.** This ticket edits `scripts/docs_refs_check.py` and
  `commands/dark-factory-revise-advisory.md`. If the blast-radius gate returns
  `HUMAN_REQUIRED` for either, that hold is expected and accepted; do not restructure the
  change to avoid it. No file under `workflows/`, no `gate_*` script, no docs-conformance
  gate file, no breaker or budget surface, no `deploy/**`, no `.github/**`, no `CLAUDE.md`.
- **Size.** One implementation run is expected. If the plan cannot fit it, the separable
  piece is requirement 8 (the hook): everything else stands without it.

## Architecture / Approach

**Chosen: a tool-written content-hash lock checked in CI and in the test suite, with
relocation by hash window and a git diff used only to choose between identical candidates.**
The docs keep their citation syntax, so there is no migration and no new authoring syntax.

Why both a lock and a diff. A diff alone relocates as many citations as the lock with a base
(47 of 53 in the measurement) and needs no stored state. It was not chosen as the whole
mechanism because it cannot detect anything: CI would need a base ref and history (a `ci.yml`
change), a stateless shift is not idempotent (running it twice shifts twice, and it cannot
tell that a doc line was already corrected by hand), and it would have passed the five wrong
hand-derived citations that merged tonight. The lock is what makes a wrong position a red
check. The diff is only ever a tie-break among positions the hash has already confirmed, so a
wrong or missing base costs an "ambiguous" message, never a wrong citation.

A whole-range hash fails on any edit inside the range. That is intended: the prose describes
that range, so an edit inside it is a reason to re-read the sentence. In the measurement this
was 6 of 108 citation spans per PR-sized change. First-and-last-line anchors would pass a
rewritten middle, and a short excerpt would pass an edit below its first line.

Known limits: bare continuation spans (requirement 7); a lock seeded from a wrong citation
keeps it wrong until the audit; `--accept` can be run without reading; when the same lines
are cited twice in one doc and only one occurrence is re-pointed by hand, no orphan is left,
so the re-pointed one is taken as new prose.

## Alternatives Considered

1. **Named anchors** (`<!-- anchor: slug -->` plus `path#slug`). Rejected: needs a marker in
   the command files for each citation, many of which point at one line inside a bash block,
   and rewrites every citation in the docs.
2. **Hand-written quoted snippet at each citation.** Rejected: moves the manual upkeep rather
   than removing it, and clutters dense tables.
3. **Symbol citations (`path::function`).** Not applicable to `commands/*.md`. Worth
   evaluating for `scripts/**` and `scheduler.sh` when the check is widened.
4. **`git diff` hunk shifting with no lock.** Same automatic-fix rate, no state, no seeding
   question, no lock conflicts. Rejected as the whole mechanism for the three reasons under
   "Why both"; kept as the tie-break.
5. **Hash context (k lines either side) instead of a git base to separate identical
   candidates.** Measured: 39 to 40 of 53 against 47 with the base, because the neighbouring
   lines are often what the PR edited.
6. **Seed nothing for unread citations.** Rejected: the "every covered citation has an entry"
   rule would need an exemption list, and unread citations would keep drifting, which makes
   the audit harder.
7. **Audit all citations in this ticket.** Rejected: 84 hand re-derivations plus 31 span
   attributions in the same run as the checker change. Tool first, audit second.

## Acceptance Criteria

- [ ] `python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md`
      exits 0 on the PR head and prints the locked-at-seed note.
- [ ] `docs/.citation-lock.yaml` has an entry for each distinct covered citation part (80
      entries for 84 parts at 4e2b1bf, four parts being repeats within a doc; the implement
      phase reports the counts it found), all `seeded: true`. Running `--seed`
      again changes no byte.
- [ ] Inserting five lines at the top of `commands/dark-factory-plan.md` makes the check exit
      1 with one error per affected citation, each naming the `--rederive` command;
      `--rederive --base HEAD` then exits 0, every affected citation in the docs is shifted by
      five, and no other byte of the docs changed. A second `--rederive` changes nothing.
- [ ] A cited single line that occurs several times in its target is relocated with
      `--base`, and reported as ambiguous (docs and lock untouched, exit 1) without it.
- [ ] Editing a line inside a cited range makes the check exit 1; `--rederive` exits 1 and
      leaves that citation and its entry untouched; `--accept` on it makes the check exit 0
      and removes `seeded` from that entry only.
- [ ] Re-pointing a citation by hand at lines whose content is not the locked content makes
      `--rederive` exit 1 with `locked content is now at ...`; the lock is unchanged. The
      test reproduces tonight's case: lock five citations, insert 16 lines above them in the
      target, leave the five doc citations at their old numbers plus a wrong delta; all five
      are reported with the correct new lines and none is locked.
- [ ] Removing one entry, or leaving an orphan entry, exits 1. Deleting the lock file makes
      the checker behave as it does today (range only) and makes the requirement 9 test fail.
- [ ] A citation into `scripts/**`, `scheduler.sh`, `entrypoint.sh` or `workflow:N` behaves
      exactly as before, with or without a lock file.
- [ ] `git diff origin/main -- docs/` shows the `docs/features.md` CI row, the D4 paragraph
      of `docs/run-lifecycle.md` and the new lock file only; no path-carrying citation number
      changed.
- [ ] The lines added to `commands/dark-factory-revise-advisory.md` contain no `$` character.
- [ ] `.github/**`, `workflows/**` and `CLAUDE.md` are unchanged.

## Tests

In `tests/test_docs_refs_check.py` (TDD), on `tmp_path` trees: hash match; mismatch; missing
entry; no lock file means range-only; orphan; basename-form citation covered; comma-chain
citation locked per part and rewritten per part; non-`commands/` citation ignored by the
content check; `--seed` writes `seeded: true`, does not overwrite, and is byte-stable;
`--rederive` for moved-unchanged, several matches with and without `--base` (a `tmp_path` git
repo), base pointing at a diff that touches the range, git unavailable, hand-shifted, new
prose, removed prose, mixed group (unlocked citation plus orphan) fails and writes nothing,
zero matches, idempotence, the same citation twice in one doc, and a failing citation
alongside a fixable one (the fixable one is still fixed, exit 1); `--accept` on a changed
range and on a citation with no entry; CRLF and re-indented target lines hash the same; CLI
exit codes for each mode. The existing `pass` and `fail` fixture trees have no lock and are
left as they are. The two repo-level tests of requirement 9.

## Assumptions

- #404 (PR #663) and #405 (PR #664) are merged; `main` is at or after 4e2b1bf.
- The self-target implement and validate phases run `python -m pytest tests/` (the convention
  in `CLAUDE.md`); requirement 9 relies on it. If a run skips the suite, the first signal is
  CI on the PR, as it is today for every other check.
- The self-target clone has `scripts/docs_refs_check.py` at its root and `origin/main` as a
  ref, so the Phase 4 step and the repair command run as written.
- Whether either edited file crosses `blast_radius.hotspot_score_floor` was not computed
  during refinement; see "Hold expectation".
- The prototype figures come from four merges on one night and two command files.

## Non-goals

- Correcting any existing citation (other than the one bare span in requirement 10), and rewriting or rejecting bare continuation spans
  (follow-up audit ticket, depends on this one).
- Content checking for `scripts/**`, `scheduler.sh`, `entrypoint.sh`, `workflow:N` or any
  other target (follow-up under epic #538).
- A `--rederive` step in `commands/dark-factory-implement.md` or in the workflow DAG
  (separate ticket; requirement 9 covers the gap meanwhile).
- The `configuration.md` config-key check and the `features.md` workflow-node check.
- Any `gate_*`, breaker or budget surface, `deploy/**`, `.github/**`.
