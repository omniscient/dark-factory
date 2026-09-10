# run-record: make a silent, unwritable `runs.jsonl` loud

**Operator spec gate:** 2026-09-10 — approved with amendments from an independent read-only
review that re-resolved every citation against `origin/main`. Four blocking findings (the stderr
channel, the preflight/test collision, the hermetic-guard rule, and two drifted line ranges) and
seven advisories are folded in below.

**Issue:** #395

## Overview / Problem statement

`scripts/factory_core/run_record.py`'s `_append_jsonl()` writes every per-stage verdict
(`paused`, `failed`, `validation`, `conformance`, `review`, `manifest_intake`, …) to
`SCHEDULER_STATE_DIR / "runs.jsonl"` — the machine-readable audit ledger consumed by
`reconcile_cost_reports.py`, `run_record._build_issue_economics()`, and the breaker's
stop-condition audit trail. A root-shell backup on 2026-08-30 left the live `runs.jsonl`
owned by `root:root`; every run container executes as `factory`, so every append since
then raised `PermissionError` — and nothing surfaced it. `cmd_record()` has no
try/except around `_append_jsonl()`, so the exception propagates to `main()` and Python
prints a raw traceback to stderr and exits non-zero — but every `entrypoint.sh` call site
already runs the CLI with `|| true` (lines 341-349, 540-547), so that non-zero exit and
traceback are swallowed and the run reports success. Five days of `stage: paused` /
per-stage-verdict rows were lost before an operator noticed by hand.

This is the same silent-failure class as #358 (gate labels) and #382 (push gate): a
write to a durable side-channel fails, the failure is caught by a blanket `|| true`
meant for a different purpose (letting the *run* continue), and nothing distinguishes
"nothing to record" from "tried and failed to record."

## Requirements (from Q&A)

Q&A was conducted with two product-owner passes over the codebase (full history in the
refinement pipeline comment). The resolutions below are load-bearing:

1. **`cmd_record()`'s ledger-append failure must produce three things, in this order:**
   a stderr line naming the ledger path and the underlying OS error (**a dev/interactive
   affordance only — see Requirement 4a**); a best-effort
   `_post_seq(record)` call (Seq is the *only* remaining place the stage verdict can
   land once the local jsonl append has failed — skipping it would throw away exactly
   the recoverable data #395 exists to stop losing); and a `factory.run_record.ledger_write_failed`
   health event carrying `stage`, the ledger path, and the error text. The failure is
   then re-raised (not swallowed) as the original `OSError`.

2. **`cmd_record()` itself must never call `sys.exit()`.** It is not CLI-only:
   `handoff.py::_record_intake()` (`scripts/factory_core/handoff.py:530-558`) calls
   `run_record.cmd_record(ns)` **in-process**, from inside two `except` blocks
   (`handoff.py:493-513`) whose own comments document that a raised exception from this
   call is expected to replace the one being handled ("fail-closed but rowless;
   acceptable, not a bug").

   **Corrected justification.** An earlier draft said `SystemExit` "would silently escape
   `handoff.py`'s `except Exception as exc:` handler (`:502`)". That is not what happens: the
   `_record_intake` calls at `:496` and `:509` are *inside* `except` clauses, and an exception
   raised inside an `except` clause propagates out of the whole `try` statement — it is never
   caught by a sibling `except`. Today's `OSError` already escapes `:502`. The real difference is
   at the top: `handoff.main()` catches only `HandoffError` (`:579`, `:586`), so a `SystemExit(4)`
   would terminate the process with exit 4 and **no message**, where an `OSError` produces a
   traceback naming the ledger. The conclusion stands and remains the requirement — a function
   callable in-process must not `sys.exit()` — but it holds because of caller-visible
   diagnostics, not handler bypass. (The `ACCEPTED` path at `handoff.py:523` calls `_record_intake`
   outside any `except` and is the cleaner illustration.) `cmd_record()` must instead
   let the natural `OSError` from `_append_jsonl()` propagate (after doing the three
   things in Requirement 1); only `main()`'s CLI dispatch for the `record` subcommand
   translates that into a non-zero process exit.

3. **Health event names follow the `factory.<component>.<condition>` shape**
   (`factory.cost_report.missing`; the literal is at `scripts/factory_core/cli.py:217`, the
   `emit_health_event` call begins at `:216`). Calling this an *established convention* overstates
   it — a repo-wide sweep finds exactly **two** health-event names in existence, so the precedent
   is n=1 plus the outlier below. It is still the right shape for new events; it is just not a
   settled rule being followed. Use it, not
   the issue body's literal bare `run_record.ledger_write_failed`. The bare
   `side_effect.denied` shim event (`scripts/shims/gh`, `scripts/shims/git`) is the
   naming outlier here, has a literal test assertion
   (`tests/test_side_effect_shims.sh:251`), and is out of scope for this ticket — do not
   rename it. New events for this ticket: `factory.run_record.ledger_write_failed`
   (Requirement 1) and `factory.run_record.ledger_not_writable` (Requirement 4).

4. **`entrypoint.sh` gets a start-of-run ledger-writability preflight**, running once
   per container run, before `git clone`, as soon as `RUN_ID`/`ISSUE_NUM` are available.
   If `${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}/runs.jsonl` exists and is not
   writable by the current user:
   - print `WARNING: ledger not writable (owner=<user:group>, path=<path>)` to stderr
     (on every affected run, not just the first — but see Requirement 4a: this is **not** a
     durable channel);
   - emit a `factory.run_record.ledger_not_writable` health event to Seq via the baked
     `/opt/dark-factory/scripts/factory_core/cli.py run-record health-event` (pre-clone,
     so it must use the baked path, mirroring `IDENTITY_SH`/`FACTORY_PROVIDERS_CLI`'s
     existing pre-clone `/opt/dark-factory/...` convention, not `$CLONE_DIR/dark-factory/...`);
   - set the warning text into a global (`LEDGER_WRITE_WARNING`), unset otherwise.
   This must never fail or block the run — a chmod mistake must degrade to "loud
   warning," not "every dispatch on the instance now fails." Call it as
   `_check_ledger_writable || true`: `entrypoint.sh:2` is `set -euo pipefail` and the `ERR` trap is
   installed at `:626`, *after* this preflight, so a non-zero return here would kill the container
   **without** `on_failure()` running — precisely the outcome this requirement forbids. For the
   same reason `[ -n "${LEDGER_WRITE_WARNING:-}" ] && LEDGER_NOTE=...` must not be a function's last
   statement, and reads use `${VAR:-}` to match the file's `set -u` style (cf. `:516`).

4a. **stderr is NOT a durable channel on the production path, and this spec must not pretend it
   is.** `scheduler.sh:378` dispatches with `-d --rm`, and `entrypoint.sh:308` says so in its own
   words: *"Not the system of record (stderr is discarded under production `-d --rm` dispatch)."*
   `TMP_OUT` (`entrypoint.sh:877-878`) `tee`s only `archon workflow run` output, so a pre-clone
   stderr line at `~:142` reaches no transcript either. **The durable signals are the Seq health
   event (Requirement 4) and the failure-comment thread-through (Requirement 5); the stderr line is
   a dev/interactive affordance.** This correction matters more than it looks: a fix for a silent
   failure whose headline signal is itself discarded would reproduce #395's own bug class one layer
   up.

   Consequence, stated plainly rather than papered over: if Seq is unreachable during the same
   incident (`_post_seq_raw` swallows every failure, `run_record.py:118-119`) and the run does not
   fail, this ticket produces **no durable signal at all**. Closing that residual gap means a local
   health-event spool, which is out of scope here — but it must be named.

5. **The warning threads into `on_failure()`'s existing failure comments, not a new
   comment.** `LEDGER_WRITE_WARNING`, when non-empty, is appended to both the
   `REFINE_FAILURE_MARKER` body (`entrypoint.sh:584-595`) and the `FACTORY_FAILURE_MARKER`
   body (`entrypoint.sh:606-618`) — the same "helper sets a global, the failure-comment
   builder reads it back" pattern `_handle_session_window_pause` already uses for
   `SESSION_WINDOW_MATCHED_PATTERN` (`entrypoint.sh:308-332`, read back at `:516-522`).
   A proactive new issue comment was considered and rejected (see Alternatives): the
   unwritable ledger is a host/instance-level condition (one file shared by every run),
   not an issue-level one, so a new marker would post duplicate, unrelated-looking
   comments onto whatever ticket each subsequent run happens to be dispatched against —
   exactly the noise `post_or_update_comment`'s per-issue marker dedup exists to avoid.

6. **Test coverage:**
   - `tests/test_run_record.py`: a read-only (`chmod 0o444`) `runs.jsonl` fixture
     (`JSONL_PATH` monkeypatched to it, per the file's existing hermetic convention) →
     `cmd_record()` raises `OSError`, `_post_seq`/Seq-post mock was still called once
     with the stage-verdict payload, and the health-event mock was called with
     `factory.run_record.ledger_write_failed` and the stage/path/error detail.
   - A CLI-level test (`tests/test_run_record.py` or `tests/test_factory_core_cli.py`,
     whichever the implementer finds already exercises `main()`'s dispatch table) that
     `record` on an unwritable ledger exits non-zero with the documented code.
   - `handoff.py`'s existing OSError-arm test (`tests/test_handoff.py:824-831`) must still pass
     unmodified. **Corrected characterisation:** it asserts that on a *writable* ledger the OSError
     arm still writes one row with `verdict == "REJECTED"` / `reject_reason == "internal_error"`.
     It does **not** exercise ledger-write failure, so it does not prove the
     "propagate-and-still-fail-closed" behavior an earlier draft credited it with. (The quoted
     phrase "a malformed override must still produce an auditable runs.jsonl row" belongs to a
     different test, at `:765`.) It will still pass unmodified — that part was right — but
     nothing in the existing suite covers Requirement 2's actual hazard, which is why the new
     CLI-level test is load-bearing rather than belt-and-braces.
   - A new `entrypoint.sh` bash test exercising the preflight against a read-only
     `runs.jsonl` fixture, asserting `LEDGER_WRITE_WARNING` is set and the WARNING line is
     printed. **`tests/test_entrypoint_preflight.sh` is the better home than
     `tests/test_entrypoint_session_window.sh`** (it already owns preflight ordering assertions).

     **The test cannot simply source and assert.** `_check_ledger_writable` sits at `~:142`, above
     the `ENTRYPOINT_SOURCE_ONLY` early return (`entrypoint.sh:644`), so it runs *during*
     `ENTRYPOINT_SOURCE_ONLY=1 source entrypoint.sh` — at which point the test's scratch
     `SCHEDULER_STATE_DIR` is not yet exported, so the preflight reads the real
     `/var/lib/dark-factory/runs.jsonl` and fixes `LEDGER_WRITE_WARNING` before any fixture exists.
     The test must therefore **export a scratch `SCHEDULER_STATE_DIR` and re-invoke
     `_check_ledger_writable` explicitly after the source line** (the function is in scope
     post-source). Do not instead hoist the export above the `source`: that would disturb the
     per-block state dirs at `tests/test_entrypoint_session_window.sh:80, 167, 246, 284, 323, 365,
     410, 444`.

     **The hermetic guard's real rule** (`tests/test_run_record_hermetic.sh`), stated correctly:
     `_current_run_dir_exported_before_source()` (`:64-75`) requires `CURRENT_RUN_DIR` to be
     exported strictly *before* the `source` line; `_first_mention_is_exported()` (`:48-59`,
     applied at `:88-95`) requires only that `SCHEDULER_STATE_DIR` be **exported at its first
     mention** — there is no before-`source` ordering constraint on it.
     `tests/test_entrypoint_session_window.sh` sources at `:47` and first exports
     `SCHEDULER_STATE_DIR` at `:80-81`, which is why the earlier "same rule every existing
     entrypoint test already follows" framing was wrong.

     **Fixture caveat:** `chmod 0o444` does nothing when the test runs as root (it works on CI,
     which runs `ubuntu-latest` non-root). Guard the test with a root check that skips, or make the
     *directory* unwritable / monkeypatch `open` instead.

     **Placement caveat:** `tests/test_entrypoint_preflight.sh:19-22` does
     `grep -n 'preflight' "$ep" | head -1` and asserts that line precedes `git clone` (`:651`).
     Placing the new block at `~:142` is safe; introducing a comment containing the word
     "preflight" above `entrypoint.sh:10` would break that test.

## Architecture / Approach

**`scripts/factory_core/run_record.py` — `cmd_record()`:**

```python
def cmd_record(args) -> None:
    ...  # record dict construction unchanged
    try:
        _append_jsonl(record)
    except OSError as exc:
        print(f"run-record: ledger append failed ({JSONL_PATH}): {exc}", file=sys.stderr)
        _post_seq(record)  # best-effort — the only remaining place this verdict can land
        emit_health_event(
            "factory.run_record.ledger_write_failed",
            args.issue, args.run_id,
            {"stage": args.stage, "path": str(JSONL_PATH), "error": str(exc)[:500]},
        )
        raise
    _post_seq(record)
```

`main()`'s dispatch is the only place a process exit is decided:

```python
elif parsed.cmd == "record":
    try:
        cmd_record(parsed)
    except OSError:
        traceback.print_exc()   # the CLI path is the interactive/debug one
        sys.exit(4)             # distinct from cost-report-check's existing sys.exit(3)
```

Keep the traceback. Today an unwritable ledger produces a full Python traceback, and that traceback
is the only reason #395 was diagnosable by hand at all; replacing it with a bare exit code would be
a regression in exactly the dimension this ticket exists to improve.

Two notes on that snippet from the gate review. The `raise` short-circuits, so `_post_seq(record)`
runs exactly **once** on both the success and failure paths — there is no double-post. And prefer
reading `record["stage"]` over `args.stage`: `handoff.py:548-557` builds an `argparse.Namespace` by
hand with no guarantee of `side_effect_level`/`origin` (both reached via `getattr` today), so the
`record` dict is the reliable source for any field the failure branch reports.

`_append_jsonl()` itself is **unchanged** — `breaker.py::_append_stop_audit_row()`
(`scripts/factory_core/breaker.py:230-257`) already calls it via
`append_stop_record()` with its own tailored `except OSError` (print-and-swallow,
deliberately, because it runs on `scheduler.sh`'s live `set -euo pipefail` poll loop and
must never turn one bad chmod into a dead scheduler). That call path is out of scope and
must keep behaving exactly as it does today; the new loud-failure logic lives in
`cmd_record()` only, not in the shared low-level `_append_jsonl()` helper.

**`entrypoint.sh` — preflight, placed after `RUN_ID`/`ISSUE_NUM` are set (~line 142),
before `git clone`:**

```bash
LEDGER_WRITE_WARNING=""
_check_ledger_writable() {
  local ledger="${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}/runs.jsonl"
  [ -e "$ledger" ] || return 0
  [ -w "$ledger" ] && return 0
  local owner
  owner=$(stat -c '%U:%G' "$ledger" 2>/dev/null || echo "unknown")
  LEDGER_WRITE_WARNING="WARNING: ledger not writable (owner=${owner}, path=${ledger})"
  echo "$LEDGER_WRITE_WARNING" >&2
  python3 /opt/dark-factory/scripts/factory_core/cli.py run-record health-event \
    --run-id "${RUN_ID:-unknown}" --issue "${ISSUE_NUM:-0}" \
    --event factory.run_record.ledger_not_writable \
    --detail "owner=${owner}" "path=${ledger}" 2>/dev/null || true
}
_check_ledger_writable
```

`on_failure()`'s two failure-comment bodies each gain a conditional append, mirroring
`SESSION_WINDOW_MATCHED_PATTERN`'s existing `SUMMARY_LINE` pattern:

```bash
LEDGER_NOTE=""
[ -n "$LEDGER_WRITE_WARNING" ] && LEDGER_NOTE="

> ⚠️ ${LEDGER_WRITE_WARNING}"
```

appended into the `REFINE_FAILURE_MARKER` and `FACTORY_FAILURE_MARKER` comment bodies. **Real
ranges, re-resolved against `origin/main`:** the `post_or_update_comment "$REFINE_FAILURE_MARKER"`
call is at `entrypoint.sh:583` and its body string spans **`:584-595`**; the
`FACTORY_FAILURE_MARKER` call is at `:605` with its body spanning **`:606-618`**. Both bodies end
at `${FOOTER}"` — append `${LEDGER_NOTE}` immediately before that, *after* the fenced retry
block. (An earlier draft cited `:583-591` / `:605-617`, which would have spliced the note into the
middle of the retry block.)

## Alternatives considered

- **Wrap `_append_jsonl()` itself instead of `cmd_record()`.** Rejected: this function
  is shared with `breaker.py::append_stop_record()`, whose caller already has its own
  deliberate print-and-swallow posture tuned for the scheduler's live poll loop
  (`set -euo pipefail`); adding a Seq health-event call or re-raise semantics at that
  layer would either duplicate health events or change the poll-loop's failure mode.
  Scoping the change to `cmd_record()` leaves the breaker path untouched.
- **Call `sys.exit()` directly inside `cmd_record()`.** Rejected — see Requirement 2:
  `handoff.py` calls `cmd_record()` in-process and depends on a regular exception
  propagating, not `SystemExit`.
- **Proactively post a new GitHub issue comment from the entrypoint preflight**
  (mirroring `post_cost_report()`'s always-on comment). Rejected — see Requirement 5:
  an unwritable ledger is an instance-level condition, and a per-issue marker comment
  gives no cross-issue dedup, so an extended outage would spam N unrelated tickets.
  The Seq health event is the correct instance-level channel; the failure-comment
  thread-through covers the "this run's own issue should know" case for free when the
  run also happens to fail.

## Open questions (non-blocking)

- `cmd_assemble()`'s per-stage `_append_jsonl()` calls (`run_record.py:685-707`) and its
  durable `run-records/<run_id>.json` write (`:709-715`) share the same silent-failure
  shape but are not named in the issue's fix list (scoped to the `record` subcommand
  only). Worth a follow-up ticket if the same class of failure is observed there.
- Migrating `side_effect.denied`'s bare event name to the `factory.` prefix convention
  for consistency is out of scope here (it has a literal test assertion) but was flagged
  during Q&A as a possible follow-up.

## Not verifiable from the repo (gate review — recorded rather than assumed)

- The 2026-08-30 root-shell backup, the `root:root` ownership, and the "five days of lost rows" are
  operational claims about live instance state. Nothing in `origin/main` confirms or refutes them.
  The fix stands on its own merits either way.
- Whether Seq actually ingests and can be queried for these events. `SEQ_URL` defaults to
  `http://seq:5341` (`run_record.py:26`) and `_post_seq_raw` swallows every failure (`:118-119`),
  so "Seq is the correct instance-level channel" is an assumption this repo cannot test. See
  Requirement 4a for the consequence.
- `post_or_update_comment`'s per-issue marker dedup semantics, cited in Requirement 5's rejection
  rationale. The call sites were read; the function body was not audited.
- Whether `stat -c '%U:%G'` is present in the final image layer (base is `ubuntu:26.04`, so it is
  near-certain; the `|| echo "unknown"` fallback covers it regardless).

## Assumptions (flagged)

- Exit code `4` for `record`'s ledger-write failure is a new, arbitrary-but-documented
  value, chosen to be distinct from `cost-report-check`'s existing `sys.exit(3)`
  (`cli.py:224`, verified — and exit code `4` is genuinely free: `run_record.py` has zero
  `sys.exit` calls, and `cli.py`'s only others are `:35`, `:85`, `:97`). No other `run_record.py`/`cli.py` subcommand currently reserves it.
  Both existing `entrypoint.sh` callers of `run-record record` already wrap the call in
  `|| true`, so this exit code has no observable effect on them today — it exists for
  future/other callers and for the new test in Requirement 6.
- `_post_seq_raw()`'s existing unconditional `except Exception: pass` means the
  best-effort Seq post in Requirement 1 cannot itself introduce a new crash; its comment
  ("non-fatal: local file was already written") becomes stale on this new failure path
  and should be updated for clarity, not correctness.
