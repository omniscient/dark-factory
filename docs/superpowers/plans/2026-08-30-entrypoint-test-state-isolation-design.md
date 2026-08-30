# Implementation Plan: Stop entrypoint shell tests from writing into production factory state

**Issue:** omniscient/dark-factory#362
**Spec:** `docs/superpowers/specs/2026-08-29-entrypoint-test-state-isolation-design.md`
**Related:** #348 (same-class prior fix, test-layer only), #300 (origin of `test_run_record_hermetic.sh`)

---

## Goal

Make `tests/test_entrypoint_session_window.sh` and `tests/test_entrypoint_error_signature.sh`
fully isolated from the real `/var/lib/dark-factory` ledger regardless of code-path order, then
close the detection gap that let this ship: tighten `tests/test_run_record_hermetic.sh` so it
actually catches "mentioned but not exported at first use" and inspects both target files, and
wire the previously CI-uncovered `tests/test_entrypoint_error_signature.sh` into
`.github/workflows/ci.yml` with a direct proof that nothing lands in `/var/lib/dark-factory`.
All changes are confined to `tests/` and `.github/workflows/ci.yml` — no production code changes.

## Architecture

Four files change:

- `tests/test_run_record_hermetic.sh` — tightened first (this is the "test" in the TDD sense for
  this ticket: a static guard whose correctness is proven red against the current buggy files,
  then green once they're fixed). Two independent checks per candidate file:
  1. **SCHEDULER_STATE_DIR**: candidate = literally mentions `run-record (record|assemble)` /
     `error-signature-write`, OR sources entrypoint.sh (`ENTRYPOINT_SOURCE_ONLY=1`) AND calls
     `on_failure` / `_handle_session_window_pause` / `_write_error_signature` (the three functions
     that shell out to those CLI verbs). Check: the file's first non-comment
     `SCHEDULER_STATE_DIR`-matching line must itself be `export SCHEDULER_STATE_DIR=...`, or a
     bare `export SCHEDULER_STATE_DIR` must appear on the very next line (the established
     two-line form).
  2. **CURRENT_RUN_DIR**: candidate = sources entrypoint.sh AND calls one of the same three
     functions. Check: `CURRENT_RUN_DIR` must be exported (same-line or two-line form) on a line
     number strictly before the `source .../entrypoint.sh` line.

  The CURRENT_RUN_DIR candidate set is deliberately narrower than the naive "every
  `ENTRYPOINT_SOURCE_ONLY=1` file" reading — see Design Decision 1, which found this would have
  false-failed two out-of-scope, currently-green files.

- `tests/test_entrypoint_session_window.sh` — export `SCHEDULER_STATE_DIR` at its first assignment
  (line 65); export a scratch `CURRENT_RUN_DIR` before the `source` line (line 33); add an
  isolation-snapshot assertion at the end.
- `tests/test_entrypoint_error_signature.sh` — the same two export changes (lines 35 / 65) plus one
  more this file needs and `test_entrypoint_session_window.sh` doesn't: it has no fallback for
  `entrypoint.sh`'s hardcoded `/opt/dark-factory/scripts/*` defaults, so it must also gain the
  `IDENTITY_SH`/`FACTORY_PROVIDERS_CLI` repo-checkout redirect before it can run on a bare CI
  runner (see Task 3, step 1).
- `.github/workflows/ci.yml` — add the `test_entrypoint_error_signature.sh` run step next to
  `test_entrypoint_session_window.sh`, plus a direct proof step that creates
  `/var/lib/dark-factory` (absent by default on CI runners) and asserts neither `runs.jsonl` nor
  `current-run.json` exists after both tests run.

## Tech Stack

Bash (`set -uo pipefail`, no new dependencies), `python3 -c` one-liners for JSON field reads
(already the established pattern in these files), GitHub Actions YAML.

---

## File Structure

| File | Change |
|---|---|
| `tests/test_run_record_hermetic.sh` | Widen candidate detection; add first-mention-exported check for `SCHEDULER_STATE_DIR`; add position-before-source check for `CURRENT_RUN_DIR` |
| `tests/test_entrypoint_session_window.sh` | Export `SCHEDULER_STATE_DIR` at line 65; export scratch `CURRENT_RUN_DIR` before line 33; add end-of-file isolation assertion |
| `tests/test_entrypoint_error_signature.sh` | Add `IDENTITY_SH`/`FACTORY_PROVIDERS_CLI` bare-CI-runner redirect (missing today, unlike the session-window test); export `SCHEDULER_STATE_DIR` at line 65; export scratch `CURRENT_RUN_DIR` before line 35; add end-of-file isolation assertion |
| `.github/workflows/ci.yml` | Add `test_entrypoint_error_signature.sh` step; add `/var/lib/dark-factory` create + assert-empty wrapper around the two entrypoint tests |

---

## Tasks

### Task 1 — Tighten `tests/test_run_record_hermetic.sh` (write the guard first; verify RED)

**Files:** `tests/test_run_record_hermetic.sh`

1. Confirm the current guard is blind to the primary offender (baseline, before any edit):

   ```bash
   cd /workspace/dark-factory && bash tests/test_run_record_hermetic.sh
   ```

   Expected: exits 0 (`OK`) with **no** line at all for `test_entrypoint_session_window.sh` (not
   even a PASS) — it's silently skipped by the current candidate filter, which is the actual hole
   #362 fell through.

2. Replace the full contents of `tests/test_run_record_hermetic.sh`:

   ```bash
   #!/usr/bin/env bash
   # Regression guard (df#300, tightened df#362): a bash test that shells out to
   # `cli.py run-record record|assemble` or `cli.py error-signature-write` -- directly,
   # or indirectly by sourcing entrypoint.sh and calling on_failure() /
   # _handle_session_window_pause() / _write_error_signature() -- without first
   # exporting SCHEDULER_STATE_DIR at its first mention will write to the real
   # /var/lib/dark-factory path if ever run outside strict test isolation. A file that
   # sources entrypoint.sh (ENTRYPOINT_SOURCE_ONLY=1) also inherits its unconditional,
   # source-time current-run.json write (entrypoint.sh:117-121), so any such file that
   # calls one of those three functions must export CURRENT_RUN_DIR before the `source`
   # line too. This already happened once (two `test-run` stub rows landed in production
   # runs.jsonl on 2026-07-17; #362 found ~91 more plus a clobbered current-run.json).
   # This is a static guard over tests/*.sh, not a runtime check.
   #
   # Run: bash tests/test_run_record_hermetic.sh
   set -uo pipefail

   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
   FAIL=0

   # A bare mention of one of these names inside a test file is always an actual
   # invocation, never a redefinition -- their bodies live in entrypoint.sh, sourced,
   # not redeclared by any test. Comment-only mentions (e.g. a header describing what
   # the file verifies) are excluded by requiring the match to start before any '#'.
   _calls_trigger_fn() {
     grep -qE '^[^#]*(on_failure|_handle_session_window_pause|_write_error_signature)' "$1"
   }

   _is_state_dir_candidate() {
     local f="$1"
     grep -qE 'run-record (record|assemble)|error-signature-write' "$f" && return 0
     grep -q 'ENTRYPOINT_SOURCE_ONLY=1' "$f" && _calls_trigger_fn "$f" && return 0
     return 1
   }

   _is_current_run_dir_candidate() {
     local f="$1"
     grep -q 'ENTRYPOINT_SOURCE_ONLY=1' "$f" && _calls_trigger_fn "$f"
   }

   # Finds the first non-comment line matching $2 in file $1 and checks it's exported
   # either on that same line (`export $2=...`) or via a bare `export $2` on the very
   # next line -- the two-line form used by tests/test_entrypoint_current_run.sh:32-33
   # and tests/test_entrypoint_cost_report_regression.sh:47-48.
   _first_mention_is_exported() {
     local f="$1" var="$2"
     local first
     first=$(grep -nE "^[^#]*${var}" "$f" | head -1)
     [ -z "$first" ] && return 1
     local lineno="${first%%:*}" content="${first#*:}"
     echo "$content" | grep -qE "export[[:space:]]+${var}=" && return 0
     local next
     next=$(sed -n "$((lineno + 1))p" "$f")
     echo "$next" | grep -qE "^[[:space:]]*export[[:space:]]+${var}[[:space:]]*\$" && return 0
     return 1
   }

   # CURRENT_RUN_DIR must be exported strictly before the `source .../entrypoint.sh`
   # line -- entrypoint.sh's current-run.json write runs at source time, so an export
   # after sourcing is too late.
   _current_run_dir_exported_before_source() {
     local f="$1"
     local src
     src=$(grep -nE 'source[[:space:]]+.*entrypoint\.sh' "$f" | head -1)
     [ -z "$src" ] && return 1
     local src_line="${src%%:*}"
     local first
     first=$(grep -nE '^[^#]*CURRENT_RUN_DIR' "$f" | head -1)
     [ -z "$first" ] && return 1
     local lineno="${first%%:*}"
     _first_mention_is_exported "$f" "CURRENT_RUN_DIR" && [ "$lineno" -lt "$src_line" ]
   }

   for f in "$SCRIPT_DIR"/test_*.sh; do
     base="$(basename "$f")"
     [ "$base" = "test_run_record_hermetic.sh" ] && continue

     # A file merely mentioning "run-record assemble" in a comment/echo (e.g. a static
     # source-text guard like test_cost_report_harness_economics.sh, which greps
     # entrypoint.sh's text but never executes it) poses no pollution risk. Only a test
     # that actually executes code capable of writing state -- by sourcing entrypoint.sh
     # (ENTRYPOINT_SOURCE_ONLY=1) or invoking cli.py directly -- needs the override.
     grep -qE 'ENTRYPOINT_SOURCE_ONLY=1|cli\.py' "$f" || continue

     if _is_state_dir_candidate "$f"; then
       if _first_mention_is_exported "$f" "SCHEDULER_STATE_DIR"; then
         echo "  PASS: $base exports SCHEDULER_STATE_DIR at its first mention"
       else
         echo "  FAIL: $base's first SCHEDULER_STATE_DIR mention is not exported (mention-without-export leaks to /var/lib/dark-factory)"
         FAIL=1
       fi
     fi

     if _is_current_run_dir_candidate "$f"; then
       if _current_run_dir_exported_before_source "$f"; then
         echo "  PASS: $base exports CURRENT_RUN_DIR before sourcing entrypoint.sh"
       else
         echo "  FAIL: $base does not export CURRENT_RUN_DIR before sourcing entrypoint.sh (current-run.json clobber risk at source time)"
         FAIL=1
       fi
     fi
   done

   echo ""
   [ "$FAIL" -eq 0 ] && echo "OK" || echo "FAILED"
   [ "$FAIL" -eq 0 ]
   ```

3. Verify RED against the still-unfixed target files:

   ```bash
   cd /workspace/dark-factory && bash tests/test_run_record_hermetic.sh
   ```

   Expected: two `FAIL` lines for `test_entrypoint_session_window.sh` (SCHEDULER_STATE_DIR and
   CURRENT_RUN_DIR) and two `FAIL` lines for `test_entrypoint_error_signature.sh`; exits 1
   (`FAILED`). This is the genuine red state — the tightened guard now inspects both files (fixing
   the "skipped, not passed" hole) and correctly flags both real bugs.

4. Verify no regression on files that must stay green throughout (Design Decision 1):

   ```bash
   cd /workspace/dark-factory && bash tests/test_run_record_hermetic.sh 2>&1 | grep -E "current_run|cost_report_regression|431_telemetry"
   ```

   Expected: either no output (these files aren't candidates for the new/changed checks and are
   silently skipped, same as today) or `PASS` lines only — never a new `FAIL` for any of these
   three files.

5. Commit:

   ```bash
   git add tests/test_run_record_hermetic.sh
   git commit -m "test(hermetic): catch SCHEDULER_STATE_DIR mention-without-export and unguarded CURRENT_RUN_DIR (#362)"
   ```

### Task 2 — Fix `tests/test_entrypoint_session_window.sh`

**Files:** `tests/test_entrypoint_session_window.sh`

1. Replace line 33 (the lone `source` line) with the CURRENT_RUN_DIR export, isolation-snapshot
   setup, and the source call plus a capture of the source-time `RUN_ID`:

   Before:
   ```bash
   ENTRYPOINT_SOURCE_ONLY=1 source "$SCRIPT_DIR/../entrypoint.sh"
   ```

   After:
   ```bash
   # #362: entrypoint.sh's current-run.json write happens unconditionally at source
   # time (entrypoint.sh:117-121), before the ENTRYPOINT_SOURCE_ONLY guard returns --
   # export a scratch CURRENT_RUN_DIR before sourcing so this test can never clobber
   # the real /var/lib/dark-factory/current-run.json (mirrors
   # tests/test_entrypoint_current_run.sh:32-33).
   CURRENT_RUN_DIR=$(mktemp -d /tmp/ep-sw-rundir-XXXXXX)
   export CURRENT_RUN_DIR

   # #362 isolation snapshot: capture the real ledger's state before sourcing so the
   # end-of-file assertions can prove this test never wrote to it, without depending
   # on absolute counts (the shared production path already carries unrelated rows).
   BEFORE_TESTRUN_COUNT=0
   [ -f /var/lib/dark-factory/runs.jsonl ] && BEFORE_TESTRUN_COUNT=$(grep -c '"run_id": "test-run-' /var/lib/dark-factory/runs.jsonl 2>/dev/null)

   ENTRYPOINT_SOURCE_ONLY=1 source "$SCRIPT_DIR/../entrypoint.sh"
   SOURCE_TIME_RUN_ID="$RUN_ID"
   ```

2. Replace line 65 (the first `SCHEDULER_STATE_DIR` assignment):

   Before:
   ```bash
   SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-sw-statedir-XXXXXX)
   ```

   After:
   ```bash
   SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-sw-statedir-XXXXXX)
   export SCHEDULER_STATE_DIR
   ```

3. At the end of the file, insert a new section before the final `Results:` block:

   Before:
   ```bash
   echo ""
   echo "Results: ${PASSED} passed, ${FAILED} failed"
   [ "$FAILED" -eq 0 ]
   ```

   After:
   ```bash
   echo ""
   echo "--- I: isolation -- the real /var/lib/dark-factory ledger was never touched (#362) ---"
   if [ -f /var/lib/dark-factory/runs.jsonl ]; then
     AFTER_TESTRUN_COUNT=$(grep -c '"run_id": "test-run-' /var/lib/dark-factory/runs.jsonl 2>/dev/null)
     assert_eq "runs.jsonl test-run-* row count unchanged" "$BEFORE_TESTRUN_COUNT" "$AFTER_TESTRUN_COUNT"
   fi
   if [ -f /var/lib/dark-factory/current-run.json ]; then
     AFTER_CURRENT_RUN_ID=$(python3 -c "import json; print(json.load(open('/var/lib/dark-factory/current-run.json')).get('run_id',''))" 2>/dev/null || echo "")
     assert_true "current-run.json was not overwritten with this run's source-time run_id" \
       "[ '$AFTER_CURRENT_RUN_ID' != '$SOURCE_TIME_RUN_ID' ]"
   fi
   rm -rf "$CURRENT_RUN_DIR"

   echo ""
   echo "Results: ${PASSED} passed, ${FAILED} failed"
   [ "$FAILED" -eq 0 ]
   ```

4. Verify the file's own functional suite still passes (regression check — the export/scratch-dir
   changes must not alter any existing assertion):

   ```bash
   cd /workspace/dark-factory && bash tests/test_entrypoint_session_window.sh
   ```

   Expected: `Results: N passed, 0 failed` (same pass count as before, plus the one or two new
   isolation assertions from step 3 — each only runs if the corresponding real path exists, so on
   a bare dev machine/CI runner they're silently skipped and don't add to the count).

5. Verify the hermetic guard now shows this file green (error-signature is still red — expected,
   fixed next task):

   ```bash
   cd /workspace/dark-factory && bash tests/test_run_record_hermetic.sh 2>&1 | grep session_window
   ```

   Expected: two `PASS` lines for `test_entrypoint_session_window.sh`, zero `FAIL` lines for it.

6. Commit:

   ```bash
   git add tests/test_entrypoint_session_window.sh
   git commit -m "fix(test): isolate test_entrypoint_session_window.sh from the real /var/lib/dark-factory ledger (#362)"
   ```

### Task 3 — Fix `tests/test_entrypoint_error_signature.sh`

**Files:** `tests/test_entrypoint_error_signature.sh`

1. This file has no fallback for `entrypoint.sh`'s hardcoded `/opt/dark-factory/scripts/*`
   defaults (`entrypoint.sh:5-9`: `IDENTITY_SH="${IDENTITY_SH:-/opt/dark-factory/scripts/identity.sh}"`
   then `source "$IDENTITY_SH"`, followed by a `FACTORY_PROVIDERS_CLI` preflight call) — unlike
   `tests/test_entrypoint_session_window.sh:15-20` and
   `tests/test_entrypoint_cost_report_regression.sh:20-25`, which both already redirect these two
   vars at the repo checkout so the file runs without the built factory image present. Because
   this file has never run in CI before (that's what Task 4 fixes), this gap has been silently
   masked on every real run so far (`/opt/dark-factory` exists inside every factory container).
   On a bare `ubuntu-latest` CI runner it does not exist, so sourcing `entrypoint.sh` would abort
   immediately under `set -euo pipefail` — confirmed by forcing the pre-fix file's default path to
   a nonexistent one:

   ```bash
   cd /workspace/dark-factory && IDENTITY_SH=/nonexistent/identity.sh bash tests/test_entrypoint_error_signature.sh
   ```

   Expected (pre-fix): `entrypoint.sh: line 6: /nonexistent/identity.sh: No such file or directory`

   Fix it before touching anything else in this file. Replace lines 9-13:

   Before:
   ```bash
   SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
   REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

   export GH_TOKEN="stub-token"
   export CLAUDE_CODE_OAUTH_TOKEN="stub-token"
   ```

   After:
   ```bash
   SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
   REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

   # entrypoint.sh hardcodes /opt/dark-factory/scripts/* for identity and the providers
   # CLI, which only exists in the factory image. Point both at the repo checkout so this
   # test runs on a bare CI runner (mirrors tests/test_entrypoint_session_window.sh).
   _REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
   export IDENTITY_SH="${IDENTITY_SH:-$_REPO_DIR/scripts/identity.sh}"
   export FACTORY_PROVIDERS_CLI="${FACTORY_PROVIDERS_CLI:-$_REPO_DIR/scripts/factory_core/providers/cli.py}"

   export GH_TOKEN="stub-token"
   export CLAUDE_CODE_OAUTH_TOKEN="stub-token"
   ```

   Verify: with `IDENTITY_SH`/`FACTORY_PROVIDERS_CLI` unset in the caller's environment (the CI
   condition), the file must resolve its own repo-relative default instead of ever touching
   `/opt/dark-factory`:

   ```bash
   cd /workspace/dark-factory && env -u IDENTITY_SH -u FACTORY_PROVIDERS_CLI bash tests/test_entrypoint_error_signature.sh
   ```

   Expected: exits 0, `Results: N passed, 0 failed` — no "No such file or directory" error, proving
   the fallback resolves correctly independent of whether `/opt/dark-factory` exists on the host.

2. Replace the lone `source` line (now a few lines further down the file after step 1's insert —
   anchor on the exact text, not a line number):

   Before:
   ```bash
   ENTRYPOINT_SOURCE_ONLY=1 source "$SCRIPT_DIR/../entrypoint.sh"
   ```

   After:
   ```bash
   # #362: see tests/test_entrypoint_session_window.sh for why CURRENT_RUN_DIR must be
   # exported before sourcing.
   CURRENT_RUN_DIR=$(mktemp -d /tmp/ep-es-rundir-XXXXXX)
   export CURRENT_RUN_DIR

   BEFORE_TESTRUN_COUNT=0
   [ -f /var/lib/dark-factory/runs.jsonl ] && BEFORE_TESTRUN_COUNT=$(grep -c '"run_id": "test-run-' /var/lib/dark-factory/runs.jsonl 2>/dev/null)

   ENTRYPOINT_SOURCE_ONLY=1 source "$SCRIPT_DIR/../entrypoint.sh"
   SOURCE_TIME_RUN_ID="$RUN_ID"
   ```

3. Replace the first `SCHEDULER_STATE_DIR` assignment (originally line 65, now shifted down by
   step 1's insert — anchor on the exact text):

   Before:
   ```bash
   SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-es-statedir-XXXXXX)
   ```

   After:
   ```bash
   SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-es-statedir-XXXXXX)
   export SCHEDULER_STATE_DIR
   ```

4. At the end of the file, insert the isolation section before the final `Results:` block:

   Before:
   ```bash
   echo ""
   echo "Results: ${PASSED} passed, ${FAILED} failed"
   [ "$FAILED" -eq 0 ]
   ```

   After:
   ```bash
   echo ""
   echo "--- G: isolation -- the real /var/lib/dark-factory ledger was never touched (#362) ---"
   if [ -f /var/lib/dark-factory/runs.jsonl ]; then
     AFTER_TESTRUN_COUNT=$(grep -c '"run_id": "test-run-' /var/lib/dark-factory/runs.jsonl 2>/dev/null)
     assert_eq "runs.jsonl test-run-* row count unchanged" "$BEFORE_TESTRUN_COUNT" "$AFTER_TESTRUN_COUNT"
   fi
   if [ -f /var/lib/dark-factory/current-run.json ]; then
     AFTER_CURRENT_RUN_ID=$(python3 -c "import json; print(json.load(open('/var/lib/dark-factory/current-run.json')).get('run_id',''))" 2>/dev/null || echo "")
     assert_true "current-run.json was not overwritten with this run's source-time run_id" \
       "[ '$AFTER_CURRENT_RUN_ID' != '$SOURCE_TIME_RUN_ID' ]"
   fi
   rm -rf "$CURRENT_RUN_DIR"

   echo ""
   echo "Results: ${PASSED} passed, ${FAILED} failed"
   [ "$FAILED" -eq 0 ]
   ```

5. Verify the file's own functional suite still passes:

   ```bash
   cd /workspace/dark-factory && bash tests/test_entrypoint_error_signature.sh
   ```

   Expected: `Results: N passed, 0 failed` (same pass count as before, plus 0-2 new isolation
   assertions depending on whether the real paths exist on this machine).

6. Verify the hermetic guard is now fully green:

   ```bash
   cd /workspace/dark-factory && bash tests/test_run_record_hermetic.sh
   ```

   Expected: all `PASS` lines, zero `FAIL` lines, exits 0 (`OK`).

7. Commit:

   ```bash
   git add tests/test_entrypoint_error_signature.sh
   git commit -m "fix(test): isolate test_entrypoint_error_signature.sh from the real /var/lib/dark-factory ledger (#362)"
   ```

### Task 4 — Wire `test_entrypoint_error_signature.sh` into CI with a direct isolation proof

**Files:** `.github/workflows/ci.yml`

1. Edit the `tests` job's step list. Replace:

   ```yaml
         - run: bash tests/test_entrypoint_current_run.sh
         - run: bash tests/test_entrypoint_session_window.sh
         - run: bash tests/test_cost_report_endpoint.sh
   ```

   with:

   ```yaml
         - run: bash tests/test_entrypoint_current_run.sh
         - run: sudo install -d -m 777 /var/lib/dark-factory
         - run: bash tests/test_entrypoint_session_window.sh
         - run: bash tests/test_entrypoint_error_signature.sh
         - name: Assert neither entrypoint test touched /var/lib/dark-factory
           run: test -z "$(ls -A /var/lib/dark-factory)"
         - name: Remove the /var/lib/dark-factory scratch dir
           if: always()
           run: sudo rm -rf /var/lib/dark-factory
         - run: bash tests/test_cost_report_endpoint.sh
   ```

   The assertion checks the directory is empty rather than naming just `runs.jsonl` and
   `current-run.json` (the spec's literal wording) — the install step creates it empty
   immediately beforehand, so an empty-directory check is a strictly stronger, equally cheap
   superset of the spec's two-file check that also catches a stray `error-signatures/` or
   `run-records/` write neither test should ever produce here.

   `/var/lib/dark-factory` doesn't exist by default on a bare `ubuntu-latest` runner, which is
   exactly why the belt-and-suspenders isolation assertions added inside the two test files
   (Tasks 2-3, in each file's end-of-file isolation section) are always skipped on CI today —
   creating it here first makes the "never writes to the real
   path" property directly provable in CI instead of only inside a live factory container. The
   cleanup step runs `if: always()` so `/var/lib/dark-factory` never leaks into
   `test_entrypoint_cost_report_regression.sh` (ci.yml, later in the same job) even if the assert
   step itself fails — that test does not itself set `CURRENT_RUN_DIR` and is intentionally not
   touched by this ticket (see Design Decision 1), so leaving the directory around on a failure
   path would let it silently start writing there instead of staying inert as it does today.
   `SCHEDULER_STATE_DIR` and `CURRENT_RUN_DIR` are not set anywhere in the job env, so both
   entrypoint tests resolve their defaults exactly as they would inside a real factory container
   that forgot to isolate them.

2. Verify the workflow YAML still parses (this repo's CI has no dedicated GitHub Actions linter,
   so a plain YAML load is the available static check):

   ```bash
   cd /workspace/dark-factory && python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('ok')"
   ```

   Expected output: `ok`

3. Commit:

   ```bash
   git add .github/workflows/ci.yml
   git commit -m "ci: wire test_entrypoint_error_signature.sh and prove neither entrypoint test touches /var/lib/dark-factory (#362)"
   ```

### Task 5 — Full suite regression pass

**Files:** none (verification only)

1. Run every touched or adjacent shell test explicitly:

   ```bash
   cd /workspace/dark-factory
   bash tests/test_run_record_hermetic.sh
   bash tests/test_entrypoint_session_window.sh
   bash tests/test_entrypoint_error_signature.sh
   bash tests/test_entrypoint_current_run.sh
   bash tests/test_entrypoint_cost_report_regression.sh
   ```

   Expected: all five exit 0, and none of the latter two show any new `FAIL` output relative to
   before this ticket (Design Decision 1's no-regression guarantee).

2. Run the full suite per CLAUDE.md's stated CI command:

   ```bash
   cd /workspace/dark-factory && python -m pytest tests/ -v
   ```

   Expected: all tests pass, zero failures/errors (no Python files were touched, so this is a pure
   regression check). If `pytest`/`pyyaml`/`aiohttp` aren't already installed in this container,
   install them first exactly as `.github/workflows/ci.yml:12` does:
   `pip install pytest pyyaml aiohttp`.

3. No commit for this task (verification only, no file changes expected). If any failure surfaces,
   fix it under the task that owns the affected file and re-run this step before proceeding.

---

## Design Decisions

1. **Both guard candidate sets — `SCHEDULER_STATE_DIR` (item 4a) and `CURRENT_RUN_DIR` (item 4c) —
   are narrowed to "sources entrypoint.sh AND calls a trigger function," not "sources
   entrypoint.sh"/"matches the literal string" alone.** The spec's Architecture section describes
   4(a) as widening to "any file that matches `ENTRYPOINT_SOURCE_ONLY=1`" and 4(c) as applying to
   "every file matching `ENTRYPOINT_SOURCE_ONLY=1`," but direct inspection during planning found
   both readings are internally inconsistent with the spec's own must-stay-green constraint in the
   same item: under the literal 4(c) rule, `tests/test_entrypoint_cost_report_regression.sh` and
   `tests/test_431_telemetry_isolation.sh` (both source `entrypoint.sh`, neither sets
   `CURRENT_RUN_DIR`) would newly FAIL; under the literal 4(a) rule, `tests/test_entrypoint_current_run.sh`
   (no `SCHEDULER_STATE_DIR` mention at all) would newly FAIL too — yet item 4 explicitly requires
   the guard to "keep `tests/test_entrypoint_cost_report_regression.sh` and
   `tests/test_entrypoint_current_run.sh` green throughout." None of these three files call
   `on_failure`/`_handle_session_window_pause`/`_write_error_signature` — the functions whose
   invocation is what these two target tests actually exercise the source-time write through — so
   scoping both candidate sets to "sources entrypoint.sh *and* calls one of those three functions"
   resolves the inconsistency in favor of the spec's explicit constraint, and still exactly targets
   the two ticket files without widening scope to fix (or false-failing) the two out-of-scope ones.
   Residual honesty note: `entrypoint.sh:117-121`'s current-run.json write is unconditional at
   source time, not actually gated by which function runs afterward — the three functions are only
   what make its effect *observable* in these two tests' assertions, not a structural trigger. A
   future test that sources entrypoint.sh without calling any of the three would still carry the
   same latent clobber risk and would not be caught by this guard; that gap is the same one already
   named for `test_entrypoint_cost_report_regression.sh`/`test_431_telemetry_isolation.sh` in Out
   of Scope, not a new one. This keeps the guard's actual purpose (catch the bug class in a way
   `tests/test_run_record_hermetic.sh` is trusted to be always-green in CI) intact without silently
   expanding this S-sized ticket's blast radius.
2. **Hermetic-guard tightening (Task 1) is sequenced before the two test-file fixes (Tasks 2-3),
   not after.** This makes the guard itself the thing under TDD: it's proven red against the real,
   still-broken files (no scratch-copy machinery needed), then each subsequent task's fix is
   independently provable via the same guard turning that file's two lines green, ending in a
   fully green run after Task 3 — mirroring this repo's established red/verify-fail/implement/
   verify-pass/commit convention at the guard-file granularity instead of within a single task.
3. **CI proof creates and destroys `/var/lib/dark-factory` around only the two entrypoint tests
   this ticket targets**, not around every `ENTRYPOINT_SOURCE_ONLY=1` test in the job. Widening it
   would incidentally start exercising `test_entrypoint_cost_report_regression.sh`'s pre-existing,
   out-of-scope `CURRENT_RUN_DIR` gap inside the same CI run (see Design Decision 1) — not a test
   failure (no assertion covers it), but an avoidable, unrelated behavior change. Scoping the
   directory's lifetime tightly and removing it immediately after the assertion keeps every other
   step in the job byte-for-byte unaffected.

## Out of Scope (per spec)

- `scripts/factory_core/run_record.py`, `breaker.py`, `cli.py` (the Python ledger writers) — no
  test-context marker convention introduced, per spec Alternatives Considered.
- `tests/test_entrypoint_cost_report_regression.sh` and `tests/test_431_telemetry_isolation.sh` —
  both have the same latent `CURRENT_RUN_DIR` gap (see Design Decision 1) but are not named in the
  spec's Requirements; flagged here as a candidate follow-up ticket, not fixed in this S-sized
  change.
- One-off cleanup of the live ledger's ~91 stray `test-run-1` rows and the clobbered
  `current-run.json` — operator task per the issue's own scope note, not a code change.
- Any gate/breaker/budget surface, `.factory/adapter.yaml`, `deploy/**` — untouched.
- Copying this plan and its spec onto the eventual `feat/issue-362-*` implementation branch — per
  the accumulated codebase pattern, that copy-and-commit step is the *implement* phase's own
  responsibility once it starts from this refine branch, not something the plan phase (scoped to
  `docs/superpowers/plans/` only) does or can do.
