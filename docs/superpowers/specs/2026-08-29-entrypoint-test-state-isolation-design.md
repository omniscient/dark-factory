# Stop entrypoint shell tests from writing into production factory state

**Issue:** omniscient/dark-factory#362
**Status:** initial refinement, 2026-08-29.

---

## Overview / Problem Statement

`tests/test_entrypoint_session_window.sh` and `tests/test_entrypoint_error_signature.sh` both
`ENTRYPOINT_SOURCE_ONLY=1 source entrypoint.sh` and then drive its functions (`_handle_session_window_pause`,
`_write_error_signature`, `on_failure`) directly. Those functions shell out to
`scripts/factory_core/cli.py` (`run-record record`, `error-signature-write`, `session-window-check`,
…), whose Python modules (`run_record.py`, `breaker.py`, `cli.py`) resolve the ledger location via
`os.environ.get("SCHEDULER_STATE_DIR", "/var/lib/dark-factory")` — a fresh `python3` subprocess only
sees a shell variable if it was `export`-ed.

Both tests' first `SCHEDULER_STATE_DIR=$(mktemp -d ...)` assignment (line 65 in each file) is a plain,
non-exported shell variable. Every function called before the file's later `export SCHEDULER_STATE_DIR`
line (session-window: line 152; error-signature: line 123) therefore leaks its `run-record record`
subprocess calls straight to the real `/var/lib/dark-factory` — the scheduler state volume mounted
into every factory run container. (The `error-signature-write` and `session-window-*` CLI calls are
already isolated: `entrypoint.sh:246/295/387` pass `--state-dir "${SCHEDULER_STATE_DIR:-…}"`
explicitly, which is why the error-signature test's `.sig` assertions pass on main. Only
`run-record record|assemble` — `entrypoint.sh:298/495/512/890`, no `--state-dir` flag, argv passed
through `cli.py` as REMAINDER — resolves the directory from the environment in `run_record.py:23`.) Confirmed live in this very refine container:
`/var/lib/dark-factory/runs.jsonl` and `current-run.json` are real, actively-written production files
(verified during Phase 3 context assembly), not a mock or CI fixture.

A second, distinct leak: `entrypoint.sh:117-121` writes `$CURRENT_RUN_DIR/current-run.json`
**unconditionally at source time**, before the `ENTRYPOINT_SOURCE_ONLY` early-return guard at
`entrypoint.sh:595`. Neither target test sets `CURRENT_RUN_DIR` at all (unlike `SCHEDULER_STATE_DIR`,
which is at least *set*, just not exported), so both tests default it to `/var/lib/dark-factory` and
clobber `current-run.json` on every run, exactly as the operator's 2026-08-28 addendum comment reports
(observed clobber: `{"run_id":"dae029e7…","issue_number":0,...}`). `tests/test_entrypoint_current_run.sh`
already establishes the correct pattern for this — `CURRENT_RUN_DIR=$(mktemp -d ...); export CURRENT_RUN_DIR`
placed *before* the `source` line — it is just never applied to the other two entrypoint tests.

Investigation during refinement surfaced a third contributing gap: `tests/test_run_record_hermetic.sh`
(added for a near-identical prior incident, df#300) is a CI-wired static guard that scans every
`tests/test_*.sh` file and fails it if it invokes `run-record record|assemble` / `error-signature-write`
without first setting `SCHEDULER_STATE_DIR`. Two holes: (a) its candidate filter
(`tests/test_run_record_hermetic.sh:24-26`) only inspects files that literally contain
`run-record (record|assemble)|error-signature-write` — the session-window test contains neither
string (it reaches `run-record record` only through the sourced `_handle_session_window_pause` /
`on_failure` functions), so the primary offender is *skipped*, not passed; the error-signature test
is a candidate only because of a comment on its line 4. (b) For candidates, the check
(`grep -q 'SCHEDULER_STATE_DIR' "$f"`) only tests whether the string appears *anywhere* in the
file — mention-without-export passes green. This is precisely
the hole #362 fell through, and leaving it as-is would let the same class of bug recur in any future
test. Separately, `tests/test_entrypoint_error_signature.sh` is not wired into
`.github/workflows/ci.yml` at all — it has only ever been run ad hoc by implement agents inside live
run containers (exactly the #334/#341 runs the issue's symptom section cites), so no automated check
would have caught this leak or would catch a regression of the fix.

## Requirements

Distilled from the issue body, the operator's two comments, and the brainstorming Q&A below:

1. `tests/test_entrypoint_session_window.sh` and `tests/test_entrypoint_error_signature.sh` must never
   write to the real `/var/lib/dark-factory` regardless of which code section runs first.
2. `current-run.json` must be protected the same way `runs.jsonl` / `error-signatures/*.sig` are —
   the operator's addendum calls this out explicitly as the same exposure class.
3. The fix must be verifiable, not just trusted: the existing static regression guard
   (`tests/test_run_record_hermetic.sh`) must actually catch the specific failure mode that let this
   ship (variable mentioned but not exported at first use), and the previously CI-uncovered test
   (`test_entrypoint_error_signature.sh`) must be wired into CI so this class of leak is caught
   automatically going forward.
4. No change to `scripts/factory_core/run_record.py`, `breaker.py`, or `cli.py` (the Python ledger
   writers) — confirmed out of scope in Q&A below.
5. No change to any gate/breaker/budget surface, `.factory/adapter.yaml`, or `deploy/**` (per
   CLAUDE.md hard limits and the operator's explicit scope note).
6. One-off cleanup of the live ledger's ~91 stray `test-run-1` rows and the clobbered
   `current-run.json` remains an operator task, not part of this ticket's deliverable (confirmed by
   the operator's 2026-08-28 comment).

## Architecture / Approach

All changes are confined to `tests/` and `.github/workflows/ci.yml` — no production code changes.

1. **Export `SCHEDULER_STATE_DIR` at first assignment**, not just at the later block that already
   does. In both `tests/test_entrypoint_session_window.sh:65` and
   `tests/test_entrypoint_error_signature.sh:65`, convert the plain
   `SCHEDULER_STATE_DIR=$(mktemp -d ...)` into an exported assignment. Bash's export attribute is
   sticky across later plain reassignments (`VAR=newvalue` without `export`), so a single fix at the
   first assignment in each file automatically covers every later reassignment in that file
   (session-window: lines 129/137/151/230/268/307/349/394/428 — lines 92/105 are `rm -f` uses, not
   reassignments; error-signature: lines 79/96/106/122) — no need to touch each block individually.

2. **Set and export `CURRENT_RUN_DIR` to a fresh `mktemp -d` scratch directory before the `source`
   line** in both files (`tests/test_entrypoint_session_window.sh:33`,
   `tests/test_entrypoint_error_signature.sh:35`), mirroring the established pattern at
   `tests/test_entrypoint_current_run.sh:32-33`. This must happen *before* sourcing because
   `entrypoint.sh`'s `current-run.json` write executes unconditionally at source time, ahead of the
   `ENTRYPOINT_SOURCE_ONLY` guard — setting it after sourcing (the way these two tests currently
   handle `SCHEDULER_STATE_DIR`) would be too late for this particular write.

3. **Add a lightweight isolation assertion in both tests**, addressing the issue's literal ask
   ("assert `/var/lib/dark-factory/runs.jsonl` is untouched") without depending on that path's
   existence: if `/var/lib/dark-factory/runs.jsonl` exists, count — before and after the test
   body — the lines whose `run_id` matches this test's own `RUN_ID` values (`test-run-*`; see
   session-window lines 48/157/236/274/313) and assert the delta is 0; if
   `/var/lib/dark-factory/current-run.json` exists, assert after the body that its `run_id` is not
   the `RUN_ID` `entrypoint.sh:95` generated in this process. Do **not** snapshot mtimes or absolute
   line counts: the state volume is shared with the host scheduler and any concurrent container, so
   those change under the test, and the 91 pre-existing `test-run-1` rows make an absolute check
   impossible until the operator cleanup (Requirement 6). Skip the assertion entirely when the path
   doesn't exist (bare CI runners / developer machines never have it, matching the existing
   `tests/test_entrypoint_current_run.sh` comment about that path being "unwritable on CI runners").
   This is a belt-and-suspenders check on top of full scratch-dir isolation (items 1-2), not the
   primary correctness mechanism.

4. **Tighten `tests/test_run_record_hermetic.sh`** in three ways:
   (a) **Widen the candidate set** at `tests/test_run_record_hermetic.sh:24-26`: inspect any
   `tests/test_*.sh` that matches `ENTRYPOINT_SOURCE_ONLY=1` (sourcing `entrypoint.sh` exposes
   `run-record record` at `entrypoint.sh:298/495`) in addition to files that literally mention
   `run-record (record|assemble)|error-signature-write` — otherwise the session-window test is never
   inspected at all.
   (b) **First real mention must be exported**: for each candidate, find the first *non-comment* line
   matching `SCHEDULER_STATE_DIR` (`grep -nE '^[^#]*SCHEDULER_STATE_DIR'`) and require either
   `export SCHEDULER_STATE_DIR=` on that line **or** a bare `export SCHEDULER_STATE_DIR` on the
   immediately following line. The two-line form is the repo's established pattern
   (`tests/test_entrypoint_current_run.sh:32-33`, and the CI-wired
   `tests/test_entrypoint_cost_report_regression.sh:47-48`), so a same-line-only rule would false-fail
   a green test.
   (c) **`CURRENT_RUN_DIR` guard**: for every file matching `ENTRYPOINT_SOURCE_ONLY=1`, require an
   exported `CURRENT_RUN_DIR` assignment on a line number lower than the `source .*entrypoint.sh`
   line — the `current-run.json` clobber happens at source time (`entrypoint.sh:117-121`), so an
   export after the source line is too late.
   Verify red-then-green: the tightened guard must fail against the pre-fix versions of both target
   test files, pass once items 1-2 land, and keep `tests/test_entrypoint_cost_report_regression.sh`
   and `tests/test_entrypoint_current_run.sh` green throughout.

5. **Wire `tests/test_entrypoint_error_signature.sh` into `.github/workflows/ci.yml`**, sequenced
   near `test_entrypoint_session_window.sh` (which is already wired). This is the same class of gap
   as items above: a test capable of writing to real factory state that only ever ran by hand inside
   a live container is exactly the condition that produced this incident, and CI-wiring it makes the
   whole fix self-verifying. In the same `ci.yml` job, prove the property directly: run
   `sudo install -d -m 777 /var/lib/dark-factory` before the two entrypoint tests (with
   `SCHEDULER_STATE_DIR` and `CURRENT_RUN_DIR` unset in the job env) and afterwards assert
   `test ! -e /var/lib/dark-factory/runs.jsonl && test ! -e /var/lib/dark-factory/current-run.json`.
   Without this, item 3's assertion is always skipped on CI (the path never exists there) and the
   "nothing lands in the production path" property would only ever be checked inside live factory
   containers.

## Alternatives Considered

- **Fix only the two named test files' exports (issue's minimal literal ask).** Rejected as
  insufficient alone: it would still leave `test_run_record_hermetic.sh`'s guard blind to the exact
  failure mode that let this ship (mention-without-export), and would leave
  `test_entrypoint_error_signature.sh` uncovered by CI, so a future edit could silently reintroduce
  an unexported early assignment with nothing catching it. The two-file fix is necessary but not
  sufficient to close the actual gap.

- **Defensive Python-side guard** (refuse to write when `SCHEDULER_STATE_DIR` is unset and a
  `PYTEST_CURRENT_TEST`/`DF_TEST` marker is present), offered as an option in the issue body.
  Rejected: no such test-context marker convention exists anywhere in this repo today (`grep` for
  `DF_TEST|PYTEST_CURRENT_TEST` returns zero hits), so this would introduce a brand-new cross-cutting
  convention for an S-sized test-isolation bug. It would also make `run_record.py`/`breaker.py`/
  `cli.py` behave differently under test than in production, undermining the several test sections
  (e.g. session-window test's blocks D/E) that intentionally assert against real ledger-write
  behavior through a scratch `SCHEDULER_STATE_DIR`. The repo's own precedent for this exact bug class
  — #348 (`tests/test_hooks.sh`, stub `python3` in the test) and the more recent test_scheduler.sh
  PATH-shim fix (both cited in the issue as "same class") — fixed the problem entirely at the test
  layer in both cases; neither touched production code.

## Open Questions (non-blocking)

- Item 4(b) skips comment lines, so a file whose first `SCHEDULER_STATE_DIR` mention is a comment is
  handled (it would otherwise false-*fail*, not false-pass). The remaining limitation is that the
  guard is a line-pattern heuristic, not a bash parser — an assignment split across lines with a
  backslash continuation, or one inside a function that runs before the first top-level export,
  would not be modelled. No current test does this; noted so a future edit to the guard doesn't have
  to rediscover it.

## Assumptions

- The live `/var/lib/dark-factory` state observed in this refine container (`runs.jsonl`,
  `current-run.json`, etc.) is representative of every factory run container's mounted scheduler
  state volume, per CLAUDE.md's repo map (`scripts/factory_core/run_record.py` writes to it).
- `tests/test_entrypoint_error_signature.sh` was intentionally left out of `.github/workflows/ci.yml`
  historically only as an oversight (it is one of the ~24 currently-unwired `tests/test_*.sh` files
  noted in the 2026-07-23 budget-gate-consolidation spec's Correction section), not for a deliberate
  reason — no such reason was found in git history or code comments.
