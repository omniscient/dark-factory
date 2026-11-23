# Scheduler: Test `is_issue_running`'s Exact-Match Guard, Re-Map Doc Citations After #414

**Issue:** #579

Revised: 2026-11-22 (operator amendment after spec gate)

## Overview / Problem Statement

PR #578 (#414, the Close-dispatch breaker guard) landed a review-fixup commit
(`d8d074f`) that changed `scheduler.sh`'s duplicate-dispatch guard,
`is_issue_running()` (currently `scheduler.sh:115-121`), from a plain substring
`grep -q "#${issue_num}"` to an anchored `grep -qE "#${issue_num}([^0-9]|$)"`. The
anchor fixes a real bug — without it, a running `Close issue #711` makes
`is_issue_running 71` return true (false positive: `#71` substring-matches inside
`#711`), which would wrongly suppress dispatch for ticket #71. The fix is believed
correct and is kept as-is; this ticket does not touch its logic.

Three gaps are left over from that fixup landing outside the normal spec/plan/review
path:

1. **No test exercises the real function.** `is_issue_running` has 12 call
   sites in `scheduler.sh` gating dispatch, retry, rescue and the orphan sweep, but
   every existing test (`tests/test_scheduler.sh`, `tests/test_159_regression.sh`)
   stubs it out (`is_issue_running() { return N; }`) rather than sourcing and calling
   the real implementation — and neither of those two files is in CI's test list
   (`.github/workflows/ci.yml`) anyway. A regression in the anchor regex would ship
   silently.
2. **Stale line citations in four live docs.** The same commit added a two-line
   comment inside the function body and made other changes further down, a net shift
   of five lines by `dispatch_close` (cited `:363`, actual 368), so every citation
   below the function drifted. `docs/scheduler.md`, `docs/run-lifecycle.md`,
   `docs/features.md` and `docs/configuration.md` cite `scheduler.sh:N` /
   `scheduler.sh:N-M` roughly 137 times combined as path-prefixed spans, and more
   often still as bare continuation spans (`` `:363` ``) that inherit `scheduler.sh`
   from an earlier span (see Requirement 2); `docs_refs_check.py` (CI-enforced) only
   validates that a path-prefixed range is well-formed and inside the file's line
   count, not that it still points at the symbol it names, and does not check bare
   continuation spans at all, so CI stayed green through the drift. `scheduler.sh` is
   unchanged between `d8d074f` and `4584159`, so the issue's "actual" numbers hold
   today, but `main` may move before implementation — every citation must be
   re-checked against `main` as it stands when this work is done, not against a
   fixed offset.
3. **One doc row is substantively wrong, not just stale.** `docs/configuration.md`'s
   `scheduler.max_retries` row still describes the ceiling as covering only "implement
   and conflict-resolve." Since #414 it also caps Close/merge dispatches (the
   `N:close` counter in `dispatch_close`), and that row's reader-citation list and
   `docs/features.md`'s circuit-breaker row ("used by stages at") both omit
   `dispatch_close` entirely.

This is a test-coverage and documentation-accuracy ticket. It depends on #414 (merged,
PR #578, now on `main` at `4584159`).

## Requirements

1. Add a new test, `tests/test_is_issue_running.py`, that sources the real
   `is_issue_running` from `scheduler.sh` (not a stub) and drives it through a stubbed
   `docker` command. It must run in CI with no `.github/workflows/ci.yml` edit (see
   Architecture — CI already runs `python -m pytest tests/ -v`).
   - Core case from the issue: stub `docker` so `docker ps --no-trunc --format
     '{{.Command}}'` prints a line containing `entrypoint.sh 'Close issue #711'`;
     assert `is_issue_running 711` reports running (returns `0`) and
     `is_issue_running 71` reports not running (returns `1`).
   - Also cover: a numeric near-miss that shares a prefix but isn't a true substring
     boundary case (e.g. `7111` → not running), and no running containers at all (empty
     `docker ps` output → not running for any issue number). Also: the reverse
     direction (fixture `#71`, query `711` → not running); two containers (`#71` and
     `#711` both running → both queries running); and the end-of-line alternative of
     the regex (fixture line ending in `#711` with nothing after it → `711` running,
     `71` not).
   - The plan must include a red step: with the regex temporarily reverted to
     `grep -q "#${issue_num}"`, the `71`-against-`#711` case must fail; record that in
     the PR.
2. Re-map every line citation that resolves to `scheduler.sh` in exactly
   `docs/scheduler.md`, `docs/run-lifecycle.md`, `docs/features.md` and
   `docs/configuration.md` against `scheduler.sh` on `main` as it stands at
   implementation time. This covers three forms: (a) path-prefixed spans
   `scheduler.sh:N` / `:N-M` (~137 at spec time); (b) every number in a comma list
   such as `scheduler.sh:1074,1086,1093,1230,1241`; and (c) bare continuation spans
   (`` `:363` ``, `` `:1125-1158` ``) whose nearest preceding path-bearing citation in
   the same sentence, table row or paragraph is `scheduler.sh`. Form (c) is the
   majority of citations, is not validated by `docs_refs_check.py`, and includes the
   issue's own examples (`docs/scheduler.md` `dispatch_close` `` `:363` ``,
   `stage_review_triage` `` `:1125-1158` ``). Bare spans whose antecedent is another
   file (`entrypoint.sh`, `workflow`, `scripts/**`) are out of scope and must not be
   touched. The plan must enumerate the in-scope citations per doc before editing.
   For each citation: identify the function/constant/
   behavior it names, locate it in the current file, and correct the number or range
   if it has drifted; leave it unchanged if it is still correct. No other doc (notably
   nothing under `docs/archive/`, which CLAUDE.md documents as frozen historical
   artifacts, and not `docs/adapter-authoring-guide.md` or `docs/superpowers/{specs,
   plans}/**`) is in scope. `docs_refs_check.py`'s own range-only validation logic is
   not touched — the gap it leaves (it can't tell a range is attached to the wrong
   symbol) is the reason this citation drift shipped unnoticed, not a defect this
   ticket is asked to fix.
3. In `docs/configuration.md`'s `scheduler.max_retries` row: change the description
   from "per-issue retry ceiling for implement and conflict-resolve before the breaker
   trips to Blocked" to also name Close/merge dispatches (the `N:close` counter via
   `dispatch_close`), and add a citation for where `dispatch_close` reads
   `MAX_RETRIES` (currently the `evaluate_stop "$issue_num" "close" "$MAX_RETRIES"`
   call inside `dispatch_close`, `scheduler.sh:372` — re-verify against `main` per
   Requirement 2's method, since this line may itself have moved by the time this is
   implemented). In `docs/features.md`'s "Circuit breaker" row, add `dispatch_close`'s
   call site to the existing "used by stages at" citation list (currently four
   non-close sites only).
   - Also correct the one stale description of the function itself. In
     `docs/scheduler.md`, entry 4 of "Open questions / oddities" ("Container matching is
     by substring and host-wide") still says in its first bullet that `is_issue_running`
     uses `grep -q "#${issue_num}"`, "so issue 12 is considered running while a container
     for issue 123 exists" (currently lines 811-812; locate it by the sentence, not the
     line number). Rewrite that bullet to describe the anchored match
     (`grep -qE "#${issue_num}([^0-9]|$)"`): issue 12 is **not** considered running
     while only a container for issue 123 exists, resolved since #414 / `d8d074f`. This
     is the only prose change to that entry: its number, its heading and its host-wide
     bullet stay as they are, so the numbering of later entries does not shift. The
     bullet keeps its citation on the same line and stays two lines long, so no doc
     line below it moves.

## Architecture / Approach

### Test (Requirement 1)

Follow the existing pattern in `tests/test_scheduler_comment_verdict.py` — the prior
art, together with `tests/test_scheduler_deps_audit.py`, which already stubs `docker()`
after `source`, for unit-testing one `scheduler.sh` bash function from Python
without booting the whole scheduler loop or wiring a new CI step:

- Drive the function through a `bash -c <harness>` subprocess
  (`subprocess.run(["bash", "-c", HARNESS, "_", str(SCHEDULER), ...], ...)`), where the
  harness does `SCHEDULER_SOURCE_ONLY=1 source scheduler.sh`, then
  `declare -F is_issue_running >/dev/null || { echo HARNESS_BROKEN; exit 97; }`, then
  `if is_issue_running "$2"; then echo RESULT=running; else echo RESULT=not-running; fi`.
  `scheduler.sh:2` leaves `set -e` active after `source`, so a bare call followed by
  an echo of its status exits the shell before the echo on every negative case. The
  test asserts on the exact `RESULT=` token and on `returncode == 0`, never on the
  exit code alone, so a harness that died cannot pass as "not running".
- `scheduler.sh` runs top-to-bottom when sourced until the `SCHEDULER_SOURCE_ONLY`
  guard near the end of the file (currently `scheduler.sh:1465`); at source time it
  unconditionally runs `python3 "$FACTORY_PROVIDERS_CLI" preflight`
  (`scheduler.sh:45`) and creates `$SCHEDULER_STATE_DIR` and `$STATE_FILE`. Reuse
  `test_scheduler_comment_verdict.py`'s two guards verbatim: a `python3` shell-function
  override that short-circuits any call whose args contain `providers/cli.py`, and
  `SCHEDULER_STATE_DIR` (and `STATE_FILE`) pointed at pytest's `tmp_path` so nothing
  touches `/var/lib/dark-factory`.
- Add a `docker()` shell-function override (exported, defined inside the harness
  after `source`) that prints the fixture's canned output (one `printf '%s\n'` of an
  env-supplied string) only when called as `ps --no-trunc --format '{{.Command}}'`,
  and otherwise prints nothing and returns 1. `--no-trunc` is load-bearing (a
  truncated Command column drops the issue number), so the stub must fail the
  positive cases if the invocation changes.
- Parametrize over the fixture lines and issue numbers listed in Requirement 1 the
  same way `test_scheduler_comment_verdict.py` parametrizes over reply strings:
  `@pytest.mark.parametrize("docker_ps_output,issue_num,expected_result", CASES)`.
- This file requires **no** `.github/workflows/ci.yml` change: CI's `python -m pytest
  tests/ -v` step already discovers any `tests/test_*.py` file. This was the deciding
  factor over adding a new `.sh` test (see Alternatives).

### Doc re-map (Requirements 2–3)

This is mechanical, per-citation verification work, not a design decision — the
method is:

1. For each in-scope citation (all three forms in Requirement 2), read the citation's
   surrounding prose/table cell to identify what it claims to point at (a function
   name, a constant, a specific behavior, a line range for a described step).
2. Grep `scheduler.sh` on `main` for that function/constant name (`grep -n
   "^function_name\|^function_name()"` for a function definition, or the specific
   call site the prose describes for an inline reference) to get the current true
   line number or range.
3. If the cited number/range differs from the true one, correct it in place. If it
   already matches, leave the line untouched — this is a correction pass, not a
   wholesale rewrite, so an unaffected citation should produce no diff. Edit only
   the `scheduler.sh` numbers inside a row; leave `entrypoint.sh` and `workflow`
   citations in the same row byte-identical, since PR #571 is open and re-maps those.
   After any rebase or merge of `main` into the branch, and before the PR is marked
   ready, re-run the verification for any citation whose target moved in
   `git diff <old-base>..origin/main -- scheduler.sh`.
4. Requirement 3's `dispatch_close`/`MAX_RETRIES` additions are new citations, not
   corrections, and go through the same grep-and-verify step before being written
   (the line number in this spec, `scheduler.sh:372`, is this spec's own
   point-in-time reading and must be re-confirmed at implementation time, per
   Requirement 2).

Because `main` may move before this ticket is implemented, no citation in this spec
— including the one concrete number named in Requirement 3 — should be trusted as still correct without
re-verification; this spec intentionally does not enumerate the other corrected
values, since doing so now would itself go stale before implementation.

### Effort note

The issue carries `size: S` (< 1 hour), sized against its three worked examples. The
actual Requirement 2 citation count (~137 path-prefixed spans plus the bare
continuation spans that inherit `scheduler.sh`, which the plan must count) is larger
than that label implies. Running the full exhaustive pass anyway — rather than only
the examples the issue happens to spell out — is this spec's explicit choice (see
Requirements and Alternatives); the size label should not be read as capping scope,
and a conformance reviewer should not treat the full pass as scope creep.

## Alternatives Considered

1. **New `.sh` test file + `.github/workflows/ci.yml` edit**, matching
   `tests/test_159_regression.sh`'s / `tests/test_scheduler_lib.sh`'s bash-native
   style instead of a pytest subprocess harness. Rejected: it requires touching CI
   config for no behavioral gain, and the exact failure mode this ticket exists to fix
   — a shell test that quietly never runs because it isn't wired into `ci.yml` — is
   the whole reason `tests/test_159_regression.sh` and `tests/test_scheduler.sh` don't
   already cover this. A `tests/test_*.py` file cannot have the same failure mode: it
   is discovered automatically.
2. **Narrow the doc re-map to only the citations the issue names as examples**
   (`dispatch_close`, `stage_review_triage`, the `MAX_RETRIES` readers). Rejected: the
   issue's own text ("Re-map **every** `scheduler.sh` citation... against current
   `main`") is explicit that the named examples illustrate the drift, not bound the
   work, and a partial pass would leave `docs_refs_check.py`'s blind spot — ranges
   that are well-formed but point at the wrong symbol — exploitable by the same class
   of fixup-after-gate change that caused this ticket.
3. **Extend `docs_refs_check.py` to validate that a citation's range still contains
   the named symbol**, closing the gap that let this drift ship unnoticed. Not
   adopted here: the issue frames the checker's range-only behavior as an explanation
   for why CI didn't catch the problem, not as a deliverable, and a checker change is
   a distinct, separately reviewable piece of work (new failure mode, new false-positive
   surface) from a one-time doc correction. Left as a natural follow-up ticket, not
   drafted here.

## Open Questions (non-blocking)

- Whether `docs_refs_check.py` should eventually validate symbol-level accuracy (not
  just range well-formedness) is worth a separate ticket given this is the second
  time citation drift has shipped silently through it — left to the owner.

## Assumptions

- `is_issue_running`'s only external dependency is `docker ps --no-trunc --format
  '{{.Command}}'`; no other command needs stubbing to exercise it in isolation.
  (Verified by reading the function body, `scheduler.sh:115-121`.)
- The four doc files named in the issue are the complete set of *live* (non-archived)
  docs citing `scheduler.sh` line numbers; `docs/archive/**` and
  `docs/superpowers/{specs,plans}/**` entries that also cite `scheduler.sh:N` are
  point-in-time historical artifacts and are correctly excluded from this ticket's
  scope.
- `scheduler.sh:372` (the `evaluate_stop` call inside `dispatch_close` that reads
  `$MAX_RETRIES`) is the correct citation target for Requirement 3 as of this spec's
  writing (commit `4584159`); per the Architecture section, this must be re-grepped,
  not trusted, at implementation time.
