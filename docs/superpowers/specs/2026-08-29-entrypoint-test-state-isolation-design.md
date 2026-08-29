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
line (session-window: line 152; error-signature: line 123) therefore leaks its `run-record`/
`error-signature-write` subprocess calls straight to the real `/var/lib/dark-factory` — the scheduler
state volume mounted into every factory run container. Confirmed live in this very refine container:
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
without first setting `SCHEDULER_STATE_DIR`. Its check (`grep -q 'SCHEDULER_STATE_DIR' "$f"`) only tests
whether the string appears *anywhere* in the file — both offending tests do mention it (just not
exported at first use), so the guard has been passing them green the entire time. This is precisely
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
   (session-window: lines 92/105/129/137/151/... ; error-signature: lines 79/96/106/122/...) — no
   need to touch each block individually.

2. **Set and export `CURRENT_RUN_DIR` to a fresh `mktemp -d` scratch directory before the `source`
   line** in both files (`tests/test_entrypoint_session_window.sh:33`,
   `tests/test_entrypoint_error_signature.sh:35`), mirroring the established pattern at
   `tests/test_entrypoint_current_run.sh:32-33`. This must happen *before* sourcing because
   `entrypoint.sh`'s `current-run.json` write executes unconditionally at source time, ahead of the
   `ENTRYPOINT_SOURCE_ONLY` guard — setting it after sourcing (the way these two tests currently
   handle `SCHEDULER_STATE_DIR`) would be too late for this particular write.

3. **Add a lightweight isolation assertion in both tests**, addressing the issue's literal ask
   ("assert `/var/lib/dark-factory/runs.jsonl` is untouched") without depending on that path's
   existence: before the test body runs, if `/var/lib/dark-factory/runs.jsonl` and
   `/var/lib/dark-factory/current-run.json` exist, snapshot their mtime (and `runs.jsonl`'s line
   count); after the test body finishes, assert both are unchanged. Skip the assertion entirely when
   the path doesn't exist (bare CI runners / developer machines never have it, matching the existing
   `tests/test_entrypoint_current_run.sh` comment about that path being "unwritable on CI runners").
   This is deliberately a belt-and-suspenders check on top of full scratch-dir isolation (items 1-2),
   not the primary correctness mechanism — asserting the real path never changes is fragile in a live
   factory container where the path is genuinely in concurrent production use, but a same-run
   before/after diff is safe since only one phase agent runs per container.

4. **Tighten `tests/test_run_record_hermetic.sh`'s check** from "does the file mention
   `SCHEDULER_STATE_DIR` anywhere" to "is the *first* line in the file matching `SCHEDULER_STATE_DIR`
   an `export`". Concretely: for each candidate test file, find the first line matching
   `SCHEDULER_STATE_DIR` and require it to also match `export`, instead of the current
   file-wide `grep -q`. Verify the tightened guard fails against the pre-fix versions of both target
   test files and passes once items 1-2 land (red-then-green), so the guard is proven to actually
   catch this failure mode rather than just changed.

5. **Wire `tests/test_entrypoint_error_signature.sh` into `.github/workflows/ci.yml`**, sequenced
   near `test_entrypoint_session_window.sh` (which is already wired). This is the same class of gap
   as items above: a test capable of writing to real factory state that only ever ran by hand inside
   a live container is exactly the condition that produced this incident, and CI-wiring it makes the
   whole fix self-verifying.

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

- `tests/test_run_record_hermetic.sh`'s tightened check (first-mention-must-be-exported) still has a
  pre-existing blind spot shared with the current guard: a file whose first `SCHEDULER_STATE_DIR`
  mention is a comment (not a real assignment) would still false-pass. No current or planned test
  does this, and hardening the guard's parsing further is not warranted for an S-sized ticket — noted
  here so a future edit to the guard doesn't have to rediscover this limitation.

## Assumptions

- The live `/var/lib/dark-factory` state observed in this refine container (`runs.jsonl`,
  `current-run.json`, etc.) is representative of every factory run container's mounted scheduler
  state volume, per CLAUDE.md's repo map (`scripts/factory_core/run_record.py` writes to it).
- `tests/test_entrypoint_error_signature.sh` was intentionally left out of `.github/workflows/ci.yml`
  historically only as an oversight (it is one of the ~24 currently-unwired `tests/test_*.sh` files
  noted in the 2026-07-23 budget-gate-consolidation spec's Correction section), not for a deliberate
  reason — no such reason was found in git history or code comments.
