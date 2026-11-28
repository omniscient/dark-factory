# docs-refs: revert the advisory hook's `--rederive` base to `HEAD`, fix the blank-line message, add missing tests

**Issue:** #669

## Overview / Problem Statement

PR #667's advisory-fix commit (`15b786e`) edited `scripts/docs_refs_check.py` and
`commands/dark-factory-revise-advisory.md` beyond the reviewed plan end state for #662,
with no new tests. Four problems, all found by executing the merged tool rather than by CI
(none of this turns `main` red today):

1. **Hook base regression.** `commands/dark-factory-revise-advisory.md` Phase 4 step 2
   (lines 155-165) now computes `REDERIVE_BASE` (`origin/main` when that ref exists, else
   `HEAD`) and passes it to `--rederive --base`. See "Why `HEAD`" below: on a branch whose
   earlier commit already re-derived, this leaves citations of repeated content ambiguous
   (exit 1, swallowed by the fail-open hook), and that same PR's CI then goes red.
2. **Stale doc text.** `docs/run-lifecycle.md:970-971` (D4 paragraph) cites the hook file's
   tail as the bare span `` `:60-202` `` and says the hook runs with `--base HEAD`. The
   file is 207 lines today (the regression added 5).
3. **Misleading error for blank citations.** `check()` (via `_content_errors()`,
   `scripts/docs_refs_check.py:498-499`) tells the author to run the repair command for
   any unlocked citation, including one whose target range is all blank. `--seed` and
   `--rederive` never lock a blank range (lines 539-542, 735-737), so the command cannot
   fix it.
4. **No tests** for the behaviour the advisory commit added to
   `scripts/docs_refs_check.py`.

### Why `HEAD` (derived from how the tool computes the diff)

`_base_position()` (`scripts/docs_refs_check.py:555-590`) runs
`git merge-base <base> HEAD`, then `git diff -U0 <merge-base> -- <target>`. That diff is
**the working tree against the merge-base**, and its hunks are applied as a line delta to
the **line numbers stored in the lock**. The result is only correct when the lock's line
numbers describe the file as it is at the merge-base.

In Phase 4 the hook (step 2) runs after step 1 has established that the fix agent left
uncommitted changes, and before `git add -u` (step 3) and the commit (step 4). So at the
hook, `HEAD` is the branch tip before the advisory fix, `merge-base HEAD HEAD` is `HEAD`,
and the diff is exactly the uncommitted (staged or unstaged) advisory edit.

- With `--base HEAD` the delta is the advisory edit, applied to the lock committed at
  `HEAD`. Correct whenever that lock matches `HEAD`, which the run guarantees: validation
  runs `python -m pytest tests/`, including
  `test_repo_docs_pass_the_reference_check_with_the_citation_lock`, before code review.
- With `--base origin/main` the delta is implement edit + advisory edit, applied to a
  lock that an earlier commit on the branch already re-derived, so the implement shift is
  counted twice and the computed position is not among the candidates.

Reproduced on an export of `origin/main` (63060b6): insert 3 lines at line 12 of
`commands/dark-factory-conformance.md`, `--rederive --base origin/main` (23 parts moved,
exit 0), commit; insert 2 more lines (uncommitted); run the hook command. `--base
origin/main`: exit 1, 6 parts ambiguous. `--base HEAD`: exit 0, all 23 moved.

**Known limit (accepted, not fixed here):** if the lock committed at `HEAD` is itself
stale (an earlier commit shifted a command file without re-deriving), `--base HEAD` is the
worse base: same reproduction without the first `--rederive` gives 6 ambiguous with
`HEAD` and 0 with `origin/main`. That state already fails the repo-level pytest and CI
before the advisory phase, and the hook is fail-open, so the outcome is a red check that
names the repair command, never a wrong lock. No fallback chain is added.

## Requirements

### R1. Revert the hook to unconditional `--base HEAD`

In `commands/dark-factory-revise-advisory.md`, replace the current block (lines 155-165):

```bash
if [ -f docs/.citation-lock.yaml ] && [ -f scripts/docs_refs_check.py ]; then
  if git rev-parse --verify origin/main >/dev/null 2>&1; then
    REDERIVE_BASE=origin/main
  else
    REDERIVE_BASE=HEAD
  fi
  python3 scripts/docs_refs_check.py --rederive --base "$REDERIVE_BASE" docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md \
    || echo "revise-advisory: WARNING — citation re-derive did not pass (continuing; see its output)"
else
  echo "revise-advisory: no docs/.citation-lock.yaml in this clone — citation re-derive skipped"
fi
```

with the pre-regression form (byte-identical to what `15b786e` replaced; check with
`git show 15b786e -- commands/dark-factory-revise-advisory.md`):

```bash
if [ -f docs/.citation-lock.yaml ] && [ -f scripts/docs_refs_check.py ]; then
  python3 scripts/docs_refs_check.py --rederive --base HEAD docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md \
    || echo "revise-advisory: WARNING — citation re-derive did not pass (continuing; see its output)"
else
  echo "revise-advisory: no docs/.citation-lock.yaml in this clone — citation re-derive skipped"
fi
```

Net -5 lines: the file goes from 207 to 202 lines, `Exit 0.` on line 202. No other line of
the file changes. The step stays free of `$`: the edit removes `$REDERIVE_BASE` and adds
no `$` or brace-form variable anywhere, in particular none above line 8 (the file's first
Archon vocabulary token).

**Do not touch:** the module docstring example (`scripts/docs_refs_check.py:32`) and
`REPAIR_COMMAND` (lines 317-319). Both say `--base origin/main` on purpose: they are the
repair invocation for a branch whose lock still matches `origin/main` (a CI failure on a
PR), which is the case the "Why `HEAD`" rule sends to `origin/main`.

### R2. The doc citation and the lock

At `origin/main`, `docs/run-lifecycle.md:970-971`'s `` `:37-56` `` and `` `:60-202` `` are
bare continuation spans. The tool neither checks nor shifts them
(`scripts/docs_refs_check.py:21-22`) and `docs/.citation-lock.yaml` has no entry whose
target is `commands/dark-factory-revise-advisory.md`. After R1 the file is 202 lines
again, so `:60-202` and the "`--base HEAD`" prose are correct with no doc edit.

The plain check cannot confirm a bare span, so verify by hand:

```bash
test "$(wc -l < commands/dark-factory-revise-advisory.md)" -eq 202
sed -n '202p' commands/dark-factory-revise-advisory.md      # Exit 0.
grep -c -- '--rederive --base HEAD' commands/dark-factory-revise-advisory.md   # 1
grep -c 'origin/main\|REDERIVE_BASE' commands/dark-factory-revise-advisory.md  # 0
```

If the count is not 202, correct the bare span in `docs/run-lifecycle.md` by hand.

**If #666 has merged first** (it rewrites these bare spans as full
`commands/dark-factory-revise-advisory.md:N-M` citations and locks them), R1 changes
content inside a locked range. Then, after R1:

1. Run `python scripts/docs_refs_check.py --rederive --base origin/main docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md`.
2. For each citation it reports as "the cited range was edited": re-read the sentence,
   set the range by hand (the tail range ends at 202), and lock it with
   `python scripts/docs_refs_check.py --accept docs/run-lifecycle.md <citation>`, one
   explicit citation per call. Never `--seed`.
3. Commit the doc and `docs/.citation-lock.yaml` changes with the hook edit.

In both cases the plain check
(`python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md`)
must exit 0 and the two repo-level tests
(`test_repo_docs_pass_the_reference_check_with_the_citation_lock`,
`test_repo_citation_lock_exists_and_covers_every_commands_citation`) must pass,
unmodified. The lock is changed only through the tool.

### R3. Fix the blank-citation message in `_content_errors()`

`scripts/docs_refs_check.py:498-499` currently reads:

```python
if entry is None:
    errors.append(f"{where} has no entry in {LOCK_FILE}; run: {REPAIR_COMMAND}")
```

Change to:

```python
if entry is None:
    if (targets.digest(p.target, p.first, p.last) is not None
            and targets.blank(p.target, p.first, p.last)):
        errors.append(f"{where} is blank; pick a non-blank anchor "
                      f"(a blank range is never locked, so no repair command applies)")
    else:
        errors.append(f"{where} has no entry in {LOCK_FILE}; run: {REPAIR_COMMAND}")
```

Exact new message, after the existing `{where}` prefix:

`is blank; pick a non-blank anchor (a blank range is never locked, so no repair command applies)`

- It shares `is blank` and `pick a non-blank anchor` with `accept()`'s refusal (lines
  798-800). It must contain neither `run:` nor `--rederive` nor `REPAIR_COMMAND`'s text.
- The `digest(...) is not None` guard is required: `_Targets.blank()` slices, so for an
  out-of-range citation it returns `True` on an empty slice. Without the guard an
  out-of-range unlocked citation would be reported as blank. Out-of-range keeps today's
  message.
- Message only. No other branch changes: the "no longer holds the locked content" branch
  (lines 500-504) and the orphan branch keep their text. Exit codes and what is locked do
  not change.

### R4. Tests

All in the existing `tests/test_docs_refs_check.py`, as flat `def test_*` functions using
its helpers (`_lock_tree`, `_seeded`, `_doc`, `_content`, `_insert`, `_git_tree`). No new
test file, no shell test, no `.github/workflows/ci.yml` or `workflows/` edit: CI's
`python -m pytest tests/ -v` step already collects them.

1. **`seed()` never locks an all-blank range.** A doc citing a blank line and a non-blank
   line: one entry added, none for the blank range.
2. **`rederive()` new-prose path never locks an all-blank range.** Lock gains no entry for
   it (contrast `test_new_prose_is_locked_without_seeded`).
3. **`accept()` refuses a blank citation.** `printed == []`, the failure contains
   `is blank` and `pick a non-blank anchor`, the lock bytes are unchanged.
4. **`check()` message for an unlocked blank citation (R3).** The error contains
   `is blank; pick a non-blank anchor` and contains none of `run:`, `--rederive`,
   `REPAIR_COMMAND`.
5. **An unlocked out-of-range citation is not reported as blank (R3 guard).** Its content
   error still contains `has no entry` and `REPAIR_COMMAND`; none contains `is blank`.
6. **An unlocked non-blank citation keeps the old message** (already asserted by
   `test_missing_entry_and_orphan_entry`; keep it passing unmodified).
7. **A CRLF doc round-trips through `rederive()`.** Write the doc with `\r\n` in binary
   mode, shift the target, re-derive: the citation is rewritten and the doc bytes contain
   no bare `\n` (every line still ends `\r\n`).
8. **A `--base` starting with `-` is refused without calling git.** Monkeypatch
   `docs_refs_check._git` to fail the test if called; `_base_position(root, "-x", ...)`
   returns `(None, reason)` with `starts with '-'` in the reason.
9. **A base-resolution failure is named in the ambiguous message.** In a `_git_tree`
   repo, an ambiguous citation re-derived with `base="no-such-ref"`: the failure contains
   `ambiguous`, `the --base diff does not pick one` and `could not resolve or diff --base`;
   lock and doc bytes unchanged. Same assertion with `base="-x"` and `starts with '-'`.
10. **`--accept DOC CITATION` plus an extra positional is rejected.**
    `main(["--accept", doc, "commands/c.md:3", "extra.md", "--repo-root", root])` raises
    `SystemExit` with code 2, stderr contains `unexpected extra argument(s): extra.md`,
    lock bytes unchanged.
11. **`accept()` prints a line of the citation it accepted.** `accept()` de-duplicates
    parts by key (`{p.key: p for p in parts}`), so a citation written on two doc lines
    prints **one** line per distinct part, not one per occurrence. Doc: an unrelated
    citation on line 1, `commands/c.md:3,9` on lines 3 and 4. Assert exactly two printed
    lines (parts 3 and 9), each naming doc line 3 or 4 and neither naming line 1. Do not
    assert which of the two lines is named, and do not change `accept()` to make a
    stricter assertion pass.
12. **The lock is written before the docs.** Monkeypatch `docs_refs_check.open`
    (`monkeypatch.setattr(docs_refs_check, "open", fake, raising=False)`) with a wrapper
    that raises `OSError` for mode `"w"` and delegates otherwise (`_write_lock` uses
    `Path.write_text`, so it is not affected). `rederive()` raises; the lock on disk
    already holds the new line numbers; the doc bytes are unchanged.
13. **Hook base, behaviour.** In a `_git_tree` repo with a target holding a repeated line:
    seed and commit (commit A); insert lines above, `rederive(base=<A>)`, commit (B);
    insert more lines above, uncommitted. `rederive(base="HEAD")` on this tree: no
    failures, citation at the right line. On an identical second tree,
    `rederive(base=<A sha>)`: one `ambiguous` failure, lock and doc bytes unchanged.
14. **Hook base, text.** `commands/dark-factory-revise-advisory.md` contains
    `--rederive --base HEAD` exactly once and contains neither `REDERIVE_BASE` nor
    `origin/main`.

**Invariants.** These existing tests cover them and must stay unmodified and passing; a
change that needs one edited is out of scope:

| Invariant | Test |
|---|---|
| `--rederive` never locks changed content | `test_edited_range_fails_rederive_then_accept_locks_it`, `test_zero_matches_fails_and_changes_nothing` |
| `--rederive` changes nothing when ambiguous | `test_repeated_line_is_ambiguous_without_base_and_relocated_with_it`, `test_base_diff_touching_the_range_is_ambiguous`, new tests 9 and 13 |
| `--accept` takes explicit keys only | `test_accept_adds_a_missing_entry_and_refuses_unknown_citations`, new test 10 |
| Without a lock, behaviour is as before #662 | `test_no_lock_file_means_range_only` |
| Seeded entries are marked `seeded` | `test_seed_writes_sorted_seeded_entries_and_is_byte_stable`, `test_hand_shifted_citation_is_rekeyed_and_keeps_seeded` |

## Scope

Files this ticket may change: `commands/dark-factory-revise-advisory.md` (R1 block only),
`scripts/docs_refs_check.py` (`_content_errors()` message only),
`tests/test_docs_refs_check.py`, and, only in the "#666 merged first" case of R2,
`docs/run-lifecycle.md` and `docs/.citation-lock.yaml`. Nothing under `.github/`,
`workflows/` or `deploy/`.

**Ordering with #666.** This ticket should merge first: at `origin/main` it needs no doc
or lock change, and #666 then audits the final 202-line hook file. #666 should carry
`Depends on: #669`.

## Architecture / Approach

A revert, a message-only fix that reuses `_Targets.blank`, and tests. No new abstraction,
no change to the lock format or the CLI surface.

## Alternatives Considered

1. **Keep `origin/main` when available.** Rejected: see "Why `HEAD`"; it double-counts
   the implement shift.
2. **Try `HEAD`, then `origin/main` for what is still ambiguous.** Covers the stale-lock
   limit, but a second base can pick a different candidate of identical content, and the
   issue asks for a revert. Not done.
3. **A generic blank message.** Rejected: `accept()` already has the specific wording.
4. **Apply the blank check to the "no longer holds the locked content" branch.**
   Rejected: there the locked content was non-blank and has moved, which `--rederive`
   can repair, so the existing message is right.

## Open Questions (non-blocking)

- None.

## Assumptions

- R1 lands as a byte-identical revert of the block `15b786e` changed.
- Validation runs the repo-level pytest before the code-review and revise-advisory
  phases, so the lock committed at `HEAD` matches `HEAD` when the hook runs.
