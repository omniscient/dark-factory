# Fix Cost-Report Regression and Add a Durable Run-Record Sink

**Issue:** omniscient/dark-factory#300
**Status:** draft — pending review
**Related:** omniscient/dark-factory#235 (harness economics — currently starved by this
regression), #292 (failure-comment noise), #14 (suspected origin of the path reorganization),
#197 (generalize the completion/verifier abstraction — NOT this ticket), #198 (generalize
declarative stop/success conditions — NOT this ticket), #190 (governed persistent state /
outbox — NOT this ticket)

---

## Overview / Problem Statement

No `<!-- dark-factory-cost-report -->` comment has posted or updated on any ticket since
~2026-07-10. Every run since then has silently produced zero node-level cost/token telemetry,
`#235`'s `harness_economics` scorecard has been computing into the void, and the only surviving
durable record (`/var/lib/dark-factory/runs.jsonl`) holds nothing but failure/pause stubs. Two
independent, additive bugs cause this, and a third makes the factory's own tests corrupt
production state investigating it:

1. **Bad Archon pin.** `Dockerfile` clones `omniscient/Archon` and checks out
   `f83fb556a2a864014e12ecfe6f60c7a1d18928b9`, with a comment claiming the pin "includes workflow
   cost tracking." It does not: the `workflow cost` feature commit
   (`74372446d1c5f07101dfff61c44be8895cca30db`, on `feat/workflow-cost-tracking`) is not an
   ancestor of the pinned SHA. `entrypoint.sh` runs `archon workflow cost --last --json --quiet >
   "$ARCHON_COST_JSON" 2>/dev/null || true` — the command fails, the error is swallowed, the temp
   file stays empty, `_parse_archon_cost()` returns `[]`, and `cmd_assemble()` writes a nominally
   `"completed"` `run-record.json` with zero nodes and zero totals.
2. **Silent no-op on empty nodes.** `post_cost_report()` (`entrypoint.sh:395`) builds `RUN_ROWS`
   from `run-record.json`'s `nodes[]` and does `if [ -z "$RUN_ROWS" ]; then return; fi`
   (`entrypoint.sh:435`) — no log line, no error, the function returns success. Combined with (1),
   every run since the bad pin landed has posted "Posting cost report to issue #N..." and then
   silently done nothing.
3. **`runs.jsonl` was never going to hold real data, independent of (1).** `cmd_assemble()`
   (`run_record.py:467-547`) writes the *full* assembled record — `nodes[]`, `totals`,
   `harness_economics` — only to `$ARTIFACTS_DIR/run-record.json`. `$ARTIFACTS_DIR` resolves to
   `${HOME}/.archon/workspaces/${FACTORY_REPO_SLUG}/artifacts/runs/${RUN_ID}`
   (`entrypoint.sh:97`), which is **not** a durable Docker volume in `deploy/docker-compose.yml`
   (only `/var/lib/dark-factory`, the `dark_factory_state` volume, is). Separately,
   `cmd_assemble()` appends one stub row per gate *stage* (parsed from
   `validation.md`/`conformance.md`/`review.md`/`conflict_resolution.md`) to the durable
   `runs.jsonl`, but each stub row **hardcodes** `cost_usd: 0.0`, `input_tokens: 0`,
   `output_tokens: 0`, `duration_ms: 0` — never the real `nodes[]`/`totals`. So even with the
   Archon pin fixed, `runs.jsonl` would still hold nothing but zeroed stage stubs, and the one
   place full run economics ever lands durably is the (currently broken) GitHub comment.
4. **Test-induced production pollution.** `JSONL_PATH` (`run_record.py:22`) is a hardcoded
   module-level constant, `/var/lib/dark-factory/runs.jsonl`, with no environment override —
   the sole path in this repo that doesn't follow the existing `SCHEDULER_STATE_DIR` convention
   (`scheduler.sh:9`, `entrypoint.sh:30`, `cli.py:132`, `breaker.py:106`). Bash tests such as
   `tests/test_entrypoint_session_window.sh` already set `SCHEDULER_STATE_DIR=$(mktemp -d ...)`
   to be hermetic, but `run_record.py` ignores that variable and writes straight to the real host
   path when its `cli.py run-record record` subcommand is exercised as a real subprocess. Two
   `test-run` stub rows landed in production `runs.jsonl` on 2026-07-17 from exactly this path.
5. **The regression shipped invisibly.** `tests/test_cost_report_endpoint.sh` and
   `tests/test_cost_report_harness_economics.sh` are static grep guards over `entrypoint.sh`
   source text (they assert the GitHub API path shape, not runtime behavior) and are **not**
   invoked by `.github/workflows/ci.yml`. Nothing exercises the actual runtime path end-to-end.

## Scope

This ticket is the "urgent regression repair plus the first mandatory cost/report completion
contract" per the issue's own backlog-ownership section. It is **not** the general completion-
contract/verifier framework, and it does **not** add anything that blocks a ticket/run's
Done/success transition — see [Out of Scope](#out-of-scope-deferred-to-197198).

## Requirements

1. Pin an Archon commit that actually contains `workflow cost`, and make the build fail loudly
   if it doesn't.
2. `post_cost_report()` and `cmd_assemble()` must never silently swallow a missing/empty
   mandatory cost report — log loudly instead.
3. The full assembled run record (`nodes[]`, `totals`, `harness_economics`) gets a durable
   append/upsert outside the GitHub comment, so the comment is a view, not the only sink.
4. `0` and "unmeasured" must be distinct values everywhere in `run_record.py`'s output.
5. No test may write the real `/var/lib/dark-factory/runs.jsonl` (or the new durable run-record
   path).
6. The cost-report/completion-repair behavior gets a behavioral (not just static-grep) test,
   wired into CI.
7. Lightweight, non-blocking observability for recurrence: a reconciliation report for the
   already-lost historical runs, and a health signal so a future silent regression like this one
   is detectable without manual issue spot-checks.

## Architecture / Approach

### 1. Archon pin

Re-pin `Dockerfile`'s `git checkout` (currently line 104) from `f83fb556a2a864014e12ecfe6f60c7a1d18928b9`
to `74372446d1c5f07101dfff61c44be8895cca30db` — the specific reviewed feature commit that
introduces `workflow cost` on `feat/workflow-cost-tracking`, not the moving branch tip
(`f0395f90c404a82f69abb29ba5c05789ed08b654`, which the issue's own acceptance criteria explicitly
forbid following implicitly: "do not follow the branch tip implicitly").

**ASSUMPTION** (flagged per Q&A — this refine phase has no network access to verify Archon's
history directly): `74372446d1c5f07101dfff61c44be8895cca30db` is the reviewed commit that
introduces `archon workflow cost`, per issue #300's reproduced ancestry check
(`cost commit in feature tip f0395f90: yes`, run 2026-07-17). The implement phase MUST
independently re-verify ancestry against live Archon history before committing this pin. If
`74372446…` doesn't hold up (fails to build in isolation, or ancestry re-check disagrees),
implement selects the minimal reviewed commit that both contains `workflow cost` and passes the
build-time assertion below, records the substitution and its evidence in the commit message, and
still pins an immutable SHA — never a branch name.

**Binding correctness guarantee (not the SHA choice):** add a build step (`Dockerfile`, right
after the `archon` symlink is created) that runs `archon workflow cost --help` (or equivalent)
and fails the build (non-zero exit, no `|| true`) if the subcommand doesn't exist. This is what
actually prevents a future bad pin from shipping silently — whichever SHA is chosen, the image
build itself proves the CLI surface is present. This is a build/publish verification, not a
Done-transition gate (see [Out of Scope](#out-of-scope-deferred-to-197198)).

### 2. Fail loud, don't silently return

- `post_cost_report()` (`entrypoint.sh:435`): replace the bare `if [ -z "$RUN_ROWS" ]; then
  return; fi` with a loud diagnostic path — log an explicit `ERROR: cost report has zero node
  rows for run ${RUN_ID} (issue #${ISSUE_NUM}); nodes=${nodes_count}, archon_cost_rc=...` line to
  stderr (so it surfaces in the run's own logs/post-mortem) before returning. This stays
  observability, not a gate: `post_cost_report` still returns, the run still completes: the
  requirement is that the failure is *loud*, not that it *blocks* anything.
- `cmd_assemble()` / `_parse_archon_cost()` (`run_record.py:167-211`): currently swallows all
  archon-cost-JSON parse failures via a bare `except Exception: return []`. Change this to
  distinguish "archon produced valid JSON with zero nodes" from "archon's output didn't parse /
  the command errored" — retain the archon command's stderr (entrypoint.sh already redirects it
  to `/dev/null`; capture it to a file instead and pass its path/exit code through to
  `cmd_assemble` so a parse failure is visible in the assembled record, e.g. a new
  `archon_cost_capture: {ok: bool, exit_code: int, stderr_excerpt: str}` field) rather than a
  silent `[]`.
- A run that is known to have executed AI nodes (i.e. `status == "completed"` and at least one
  gate-stage artifact exists) but assembles with `nodes: []` must have this condition visible in
  the assembled record (`archon_cost_capture.ok == false` or equivalent) and in the loud log line
  above — visible, not blocking.

### 3. Durable full-record sink

Write/upsert the full assembled record to a durable per-run-id path under the same volume
`runs.jsonl` already lives on, derived from the existing `SCHEDULER_STATE_DIR` convention (not a
new env var):

```
${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}/run-records/<run_id>.json
```

`cmd_assemble()` writes here in addition to (not instead of) the existing
`$ARTIFACTS_DIR/run-record.json` (which remains useful as the in-run, human-inspectable copy).
`runs.jsonl` itself is left as-is — the small stage-event stream it already is; the full record
(which embeds entire artifact `.md` file contents plus `nodes[]`/`harness_economics`, multi-KB
per run) is the wrong shape to append as extra rows into a lightweight event log, and inventing a
`record_type` discriminator for `runs.jsonl` would serve zero current readers (confirmed: nothing
reads `runs.jsonl` today except `tests/test_run_record.py`, which monkeypatches the path).

This closes a second, previously-unnoticed gap: `_build_issue_economics()` and
`_backfill_run_economics()` (`run_record.py:550-644`) already assume a durable
`artifacts_root/<run_id>/run-record.json`, but every caller today points `artifacts_root` at the
non-durable `$ARTIFACTS_DIR` parent — so these functions have been no-ops in production
regardless of this ticket. This ticket does not wire `issue-economics`/`backfill-economics` into
scheduler.sh or entrypoint.sh (they remain unused-outside-tests today; production wiring is
`#235`'s concern) — it only makes the durable path they need actually exist, so a future wiring
ticket has real data to read.

Both writes (`$ARTIFACTS_DIR/run-record.json` and the new durable path) happen from the same
`cmd_assemble()` call on both the success path (`entrypoint.sh:957`) and the failure path
(`entrypoint.sh:592`), so `on_failure` runs get a durable record too.

### 4. `0` vs `unmeasured`

- `cmd_assemble()`'s per-stage stub rows (`run_record.py:527-547`, currently hardcoding
  `gen_ai.usage.input_tokens: 0`, `output_tokens: 0`, `cost_usd: 0.0`, `duration_ms: 0`) change to
  explicit `null` for all four fields — these are genuinely unmeasured at stage granularity (only
  the run/node level ever has real Archon-sourced cost data).
- `cmd_record()`'s CLI argument defaults (`run_record.py:106-136`, the `--tokens-in`/
  `--tokens-out`/`--cost-usd`/`--duration-ms` flags used by `run-record record`, currently
  defaulting to `0`/`0.0` when the flag is omitted) change to default `None` and write through as
  `null` when absent, so a genuinely-measured `0` (if a future caller passes one explicitly) stays
  distinct from "not measured." `entrypoint.sh`'s two existing `run-record record` calls
  (`entrypoint.sh:303`, `:579`) don't pass these flags today, so they'll now correctly emit `null`
  instead of false-precision zeros.
- Use explicit `null` (not omitting the key): `_post_seq()` reads fields via `record.get(field,
  0)` (`run_record.py:77-89`); omitting the key would re-inject `0` and lose the unmeasured signal
  on the way to Seq, while an explicit `null` value propagates correctly through `.get()`.
- `cmd_assemble()`'s run-level `totals` (currently `sum(n.get(field, 0) for n in nodes)`,
  `run_record.py:487-489`) must also distinguish "Archon returned nodes summing to real zero" from
  "Archon capture failed, `nodes == []`" — covered by the `archon_cost_capture.ok` field from
  item 2 above, read alongside `totals` by any consumer (including the cost-report comment
  renderer).

### 5. Test hermeticity

`JSONL_PATH` (`run_record.py:22`) changes from a hardcoded constant to:

```python
JSONL_PATH = pathlib.Path(os.environ.get("SCHEDULER_STATE_DIR", "/var/lib/dark-factory")) / "runs.jsonl"
```

read once at module load, mirroring the exact pattern already used in `cli.py:132` and
`breaker.py:106`. The new durable run-record path (item 3) derives from the same
`SCHEDULER_STATE_DIR` root, so hermeticity has a single lever, not two. This makes the
`SCHEDULER_STATE_DIR=$(mktemp -d ...)` override that `tests/test_entrypoint_session_window.sh`
*already sets* (but which `run_record.py` currently ignores) immediately effective, with no
per-test changes needed there. Audit all state-writing bash tests
(`test_entrypoint_session_window.sh`, `test_entrypoint_error_signature.sh`, and any other test
that shells out to `cli.py run-record ...` or `cli.py error-signature-write`) to confirm each
sets `SCHEDULER_STATE_DIR` to a temp directory before invoking entrypoint.sh code paths; add it
wherever missing. `LEDGER_PATH`/`MODEL_PROXY_LEDGER_PATH` and `SEQ_URL` already have their own
working env overrides and don't need a mechanism change — only add exports at specific test call
sites that actually trigger those writes.

Add a regression assertion (e.g. a small addition to an existing test or a new
`test_run_record_hermetic.sh`) that greps for any bash test lacking a `SCHEDULER_STATE_DIR`
override around a `run-record`/`error-signature-write` invocation, so a future test can't
reintroduce the pollution silently.

### 6. Wire the behavioral tests into CI

`tests/test_cost_report_endpoint.sh` and `tests/test_cost_report_harness_economics.sh` (static
guards) plus a **new** behavioral test that reproduces the exact reported failure signature —
`function_rc=0`, zero `gh` calls, cost report silently skipped — and asserts it goes **red** on
unpatched code and **green** after this ticket's fix (i.e., asserts the loud diagnostic fires and
the durable record is written when `nodes` is empty), get added to `.github/workflows/ci.yml`'s
existing `tests` job alongside the other `bash tests/test_*.sh` lines already there
(`test_identity.sh`, `test_hooks.sh`, `test_smoke_gate.sh`, etc.).

### 7. Reconciliation report and health signal (lightweight, non-blocking)

- **Reconciliation/backfill report**: a one-shot, read-only script
  (`scripts/reconcile_cost_reports.py` or similar) that scans retained `run-record.json` files
  (wherever still present) plus `request-ledger.jsonl`/Seq, and emits a report of which
  historical runs (since the 2026-06-20 last-known-good comment) are recoverable vs.
  irrecoverable. It may reuse `_build_issue_economics`/`_backfill_run_economics`'s read helpers
  but stays a standalone tool — it does not auto-run in scheduler.sh or entrypoint.sh, and does
  not gate anything. Given `$ARTIFACTS_DIR` is non-durable (item 3), expect the report to find
  most of the July window irrecoverable except the durable `runs.jsonl` stubs (issue numbers /
  timestamps only) — the report should say so plainly rather than fail.
- **Health signal**: a log line / Seq event emitted from the existing `post_cost_report()` /
  `cmd_assemble()` completion path when a completed run's cost report was skipped or its nodes
  were empty (reusing the loud diagnostic from item 2 — this is the same signal, just also framed
  as "detectable without manually inspecting tickets," e.g. a `factory.cost_report.missing`
  Seq event). No new gate, no counter store, no Done-blocking — purely so recurrence of *this*
  incident is observable going forward.

Both items stay inside this ticket per Q&A: they're cheap given the durable sink already exists
here, and they're recurrence-detection for this specific incident, not the general completion-
contract framework (which stays with #197/#198/#235).

## Alternatives Considered

1. **Append the full run record as a new row type in `runs.jsonl`, discriminated by a
   `record_type` field, vs. a separate durable per-run-id path.** Rejected: the full record
   (embedded artifact markdown, `nodes[]`, `harness_economics`) is multi-KB per run — mixing it
   into the small stage-event stream bloats every consumer of that stream (including `_post_seq`,
   which mirrors it row-for-row) for a discriminator with zero current readers. A per-run-id file
   under the same durable volume gives `_build_issue_economics`/`_backfill_run_economics` the
   exact shape they already expect, closing a second real gap for free.
2. **A new `DARK_FACTORY_RUNS_JSONL` env var vs. deriving `JSONL_PATH` from the existing
   `SCHEDULER_STATE_DIR`.** Rejected the new var: `SCHEDULER_STATE_DIR` is already the repo's
   one canonical override for this exact durable-state root (`cli.py`, `breaker.py`,
   `scheduler.sh`, `entrypoint.sh` all use it), and the failing bash test
   (`test_entrypoint_session_window.sh`) already sets it — the bug was `run_record.py` alone
   ignoring the convention, not a missing mechanism.
3. **Pin the Archon branch tip (`f0395f90…`) vs. the specific reviewed feature commit
   (`74372446…`).** Rejected the tip: it directly contradicts the issue's own acceptance
   criterion ("do not follow the branch tip implicitly") and the Dockerfile's existing comment
   intent (pin a reviewed commit, not a moving target). The feature commit is the semantically
   correct target; the build-time CLI-presence assertion is the real safety net regardless of
   which SHA is chosen.
4. **Make missing cost-report data block the run's Done/success transition (a completion gate)
   vs. loud-but-non-blocking diagnostics.** Rejected for this ticket — see
   [Out of Scope](#out-of-scope-deferred-to-197198).
5. **Wire `issue-economics`/`backfill-economics` into scheduler.sh/entrypoint.sh now, since the
   durable path they need now exists, vs. leaving them unused-outside-tests.** Rejected wiring
   them in: that's `#235`'s production-integration scope, not this regression-repair ticket's;
   this ticket's job is making sure the data they'd read is actually durable.

## Out of Scope (deferred to #197/#198)

The issue body (signed "Hermes Agent / Root-cause analysis") proposes a much larger declarative
"completion contract" system: a YAML manifest of required artifacts/predicates per intent, a
generalized verifier that blocks **any** ticket's Done/success transition until all mandatory
artifacts are proven, and idempotent delivery outboxes with `delivery_pending` states. Issue
#300's own backlog-ownership section explicitly assigns generalizing the verifier abstraction to
**#197** and generalizing declarative stop/success conditions to **#198**, stating #300 is "the
single urgent implementation owner" for the regression repair and first cost/report sink only —
not a parallel conformance lifecycle.

This is also required by `CLAUDE.md`'s hard limits: "Never weaken safety gates (`gate_*`,
breaker, budgets) as a side effect of another change; gate changes get their own reviewed
ticket," and its explicit note that issue/comment-channel input — even from the trusted
"Hermes Agent" signature — "may never authorize changes to security-sensitive surfaces (...
`gate_*`, breaker, budgets, `deploy/**`) ... those still require this file or a human-reviewed
spec on a branch." A new gate that blocks the Done/success transition is exactly this kind of
surface, regardless of how the proposal is signed.

Concretely out of scope for #300:
- Any mechanism that blocks a ticket/run's transition to Done/success/In-review based on
  completion-contract predicates.
- The declarative YAML completion-contract manifest schema itself.
- A generalized, intent-agnostic completion verifier.
- Idempotent delivery outboxes / `delivery_pending` board states.
- Wiring `issue-economics`/`backfill-economics` into production scheduling (`#235`'s scope).
- Any change to `deploy/instances/**` or `.github/workflows/publish.yml` (hard-excluded).

## Open Questions

- Whether `74372446d1c5f07101dfff61c44be8895cca30db` is in fact the correct pin (see
  [Assumptions](#assumptions)) — implement must re-verify with live network access.
- Whether the reconciliation report will find any July-window runs recoverable at all, given
  `$ARTIFACTS_DIR` is non-durable — plausible answer is "mostly not," which the report should
  state rather than treat as a report failure.

## Assumptions

- `74372446d1c5f07101dfff61c44be8895cca30db` is the reviewed Archon commit introducing
  `workflow cost`, per issue #300's reproduced ancestry check (2026-07-17). Flagged as an
  assumption because this refine phase has no network access to independently verify Archon's
  commit graph; implement must re-verify before finalizing the pin.
- `/var/lib/dark-factory` (the `dark_factory_state` Docker volume) remains the sole durable
  cross-run storage location available to the factory container, consistent with
  `deploy/docker-compose.yml`.
- `cmd_issue_economics`/`cmd_backfill_economics` remaining unused outside unit tests today is
  intentional pre-`#235`-wiring scaffolding, not a bug this ticket needs to fix.
