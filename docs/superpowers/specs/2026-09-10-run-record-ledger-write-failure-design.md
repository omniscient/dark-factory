# run-record: make a silent, unwritable `runs.jsonl` loud

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
   a stderr line naming the ledger path and the underlying OS error; a best-effort
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
   acceptable, not a bug"). `sys.exit()` raises `SystemExit`, which is **not** a subclass
   of `Exception` and would silently escape `handoff.py`'s `except Exception as exc:`
   handler (`handoff.py:502`) and any other in-process caller's generic exception
   handling — a materially different, untested failure mode. `cmd_record()` must instead
   let the natural `OSError` from `_append_jsonl()` propagate (after doing the three
   things in Requirement 1); only `main()`'s CLI dispatch for the `record` subcommand
   translates that into a non-zero process exit.

3. **Health event names follow the established `factory.<component>.<condition>`
   convention** (`factory.cost_report.missing`, `scripts/factory_core/cli.py:216`), not
   the issue body's literal bare `run_record.ledger_write_failed`. The bare
   `side_effect.denied` shim event (`scripts/shims/gh`, `scripts/shims/git`) is the
   naming outlier here, has a literal test assertion
   (`tests/test_side_effect_shims.sh:252`), and is out of scope for this ticket — do not
   rename it. New events for this ticket: `factory.run_record.ledger_write_failed`
   (Requirement 1) and `factory.run_record.ledger_not_writable` (Requirement 4).

4. **`entrypoint.sh` gets a start-of-run ledger-writability preflight**, running once
   per container run, before `git clone`, as soon as `RUN_ID`/`ISSUE_NUM` are available.
   If `${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}/runs.jsonl` exists and is not
   writable by the current user:
   - print `WARNING: ledger not writable (owner=<user:group>, path=<path>)` to stderr
     (always — visible in container logs / the captured transcript on *every* affected
     run, not just the first);
   - emit a `factory.run_record.ledger_not_writable` health event to Seq via the baked
     `/opt/dark-factory/scripts/factory_core/cli.py run-record health-event` (pre-clone,
     so it must use the baked path, mirroring `IDENTITY_SH`/`FACTORY_PROVIDERS_CLI`'s
     existing pre-clone `/opt/dark-factory/...` convention, not `$CLONE_DIR/dark-factory/...`);
   - set the warning text into a global (`LEDGER_WRITE_WARNING`), unset otherwise.
   This must never fail or block the run — a chmod mistake must degrade to "loud
   warning," not "every dispatch on the instance now fails."

5. **The warning threads into `on_failure()`'s existing failure comments, not a new
   comment.** `LEDGER_WRITE_WARNING`, when non-empty, is appended to both the
   `REFINE_FAILURE_MARKER` body (`entrypoint.sh:583-591`) and the `FACTORY_FAILURE_MARKER`
   body (`entrypoint.sh:605-617`) — the same "helper sets a global, the failure-comment
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
   - `handoff.py`'s existing OSError-arm tests (`tests/test_handoff.py:824-827`, "a
     malformed override must still produce an auditable runs.jsonl row") must still pass
     unmodified — they assert the pre-existing propagate-and-still-fail-closed behavior
     that Requirement 2 is designed to preserve, not change.
   - A new or extended `entrypoint.sh` bash test (e.g. extending
     `tests/test_entrypoint_session_window.sh`, which already sources `entrypoint.sh`
     with `ENTRYPOINT_SOURCE_ONLY=1`) exercising the preflight against a read-only
     `runs.jsonl` fixture, asserting `LEDGER_WRITE_WARNING` is set and the WARNING line
     is printed. Per `tests/test_run_record_hermetic.sh`'s static guard, this test must
     export `SCHEDULER_STATE_DIR` (and, since it sources `entrypoint.sh`,
     `CURRENT_RUN_DIR`) at their first mention, before the `source` line — the same rule
     every existing entrypoint test already follows.

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
        sys.exit(4)  # distinct from cost-report-check's existing sys.exit(3)
```

`_append_jsonl()` itself is **unchanged** — `breaker.py::_append_stop_audit_row()`
(`scripts/factory_core/breaker.py:230-256`) already calls it via
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

appended into the `REFINE_FAILURE_MARKER` and `FACTORY_FAILURE_MARKER` comment bodies at
`entrypoint.sh:583-591` and `:605-617`.

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

## Assumptions (flagged)

- Exit code `4` for `record`'s ledger-write failure is a new, arbitrary-but-documented
  value, chosen to be distinct from `cost-report-check`'s existing `sys.exit(3)`
  (`cli.py:224`). No other `run_record.py`/`cli.py` subcommand currently reserves it.
  Both existing `entrypoint.sh` callers of `run-record record` already wrap the call in
  `|| true`, so this exit code has no observable effect on them today — it exists for
  future/other callers and for the new test in Requirement 6.
- `_post_seq_raw()`'s existing unconditional `except Exception: pass` means the
  best-effort Seq post in Requirement 1 cannot itself introduce a new crash; its comment
  ("non-fatal: local file was already written") becomes stale on this new failure path
  and should be updated for clarity, not correctness.
