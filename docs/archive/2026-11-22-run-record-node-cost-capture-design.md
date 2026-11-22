# Run Record: Classify Zero-Usage AI Nodes as Cost-Missing (#453)

**Issue:** #453

**Revised:** 2026-11-22 (re-refined after operator spec-gate rejection of commit
`88e0817` — the first pass's root cause was wrong on three counts; see "Why this spec
replaces the rejected draft" below). Re-verified against `main` at the start of this
branch (`refine/issue-453-run-record--implement-node-cost-tokens-n`).

Revised: 2026-11-22 (operator amendment after spec gate)

## Why this spec replaces the rejected draft

The first spec (deleted from this branch by the operator) assumed MarketHawk #388's
`implement` node was **absent** from the cost report, and designed around diffing the
DAG's expected node set against Archon's reported node set. The operator's review
established the real shape, verified against `main` and the incident:

1. **The row exists.** #388's cost report contains
   `| implement |  | 0 | 0 | $0 | 25m 55s |`. `implement` ran to completion (its
   duration is real — 25m 55s matches "orchestrator completed all 16 plan tasks and
   pushed the branch" from the issue body) and **is** in the report, with cost and
   token fields reported as zero. The defect is "a completed AI node with no usage is
   rendered as `$0`," not "the node is missing a row."
2. **`entrypoint.sh`'s failure path already assembles a run record.** `on_failure`
   (the `ERR` trap, `entrypoint.sh:504-634`) already calls `run-record assemble
   --status failed` (`:559-571`) and `post_cost_report` (`:533`, `:633`) on every path
   that reaches it. The rejected draft's claim that "the failure path never assembles"
   was false and would have produced a duplicate assemble call or dead code.
3. **The log/node-id join key was wrong.** Archon's `archon workflow run` stderr prints
   `[dark-factory-implement] Started` for the node whose `id:` in
   `workflows/archon-dark-factory.yaml` is `implement` — the bracketed token is the
   node's `command:` value, not its `id:`. A parser that joins on the id directly would
   silently fail to match any `command:` node and misclassify every healthy run.

This pass keeps the parts of the rejected draft's design that the operator did not
dispute (the `totals.cost_capture`/`known_*` schema shape, the health-event signal, the
no-op on `reconcile_cost_reports.py`/harness-economics) and replaces the parts that
were wrong (incident shape, entrypoint change, log-join key).

## Overview / Problem Statement

`entrypoint.sh` captures `archon workflow cost --last --json` once per run and
`scripts/factory_core/run_record.py::cmd_assemble` folds it into `run-record.json`'s
`nodes` list verbatim — one dict per node Archon reports, with `cost_usd`/
`gen_ai.usage.{input,output}_tokens` defaulted to `0` whenever Archon's own JSON omits
the field (`_nodes_from_run_obj`, `run_record.py:252-276`, the `n.get(...) or 0`
pattern). `cost_report.py::render` then prints that `0` into the Markdown table cell
exactly like a genuine zero-cost step (e.g. a `bash:` node that does no AI work).

For a `command:`/`prompt:` node — a node whose entire job is to run (or spawn) an AI
agent — a `0`/`0`/`$0` row is never a legitimate outcome; it means Archon's own
accounting lost that node's usage (confirmed separately as an Archon-side data-loss
bug Archon's CLI/dag-executor exhibits on at least some completed nodes (#388's
`implement` completed; the cause of the missing result is not established) — out of
this ticket's reach, see "Out of scope"). Today that loss is silently indistinguishable
from a `bash:` step that really did cost nothing, which under-reports run cost by
whatever the lost AI node actually spent (#388: at least the $14.11-order spend its
earlier `plan` phase logged, vs. the $1.89 total the run's four *other* nodes
accounted for).

## Requirements

1. **Per-node classification.** Every node in `run-record.json`'s `nodes` list gets a
   `cost_capture` field: `"completed"` or `"missing"` (+ `reason`). A node is
   `"missing"` with `reason: "no_usage_reported"` when its workflow-YAML type is
   `command:` or `prompt:` **and** its reported `cost_usd`, input tokens, and output
   tokens are all zero. A `bash:` node with all-zero fields stays `"completed"` — zero
   is its legitimate steady state. A node's `duration_ms` is never nulled (it is a real,
   independently-measured wall-clock figure); only its cost/token fields are nulled when
   `"missing"`.
2. **Ran-but-absent nodes.** A node that Archon's own per-node log line
   (`[<command>] Started`, see Requirement 3) shows as `Started`, `Completed` or
   `Failed`, but that never appears at all in
   `archon workflow cost`'s node list, is also `"missing"` — `reason:
   "not_in_cost_report"`. This applies to `command:`/`prompt:` nodes only. A `bash:`
   node absent from the cost report spent nothing: it is not emitted and never counted
   as missing. A node the log marks `Skipped` is not reported at all (it
   never ran; this is the DAG's own conditional-edge behavior, not a data-loss case —
   it must never appear as `"missing"` nor affect `totals.cost_capture`).
3. **Node-id resolution from the log.** `entrypoint.sh` already `tee`s
   `archon workflow run`'s output (stderr merged in via `2>&1`) to `$TMP_OUT` and
   discards it once the run reaches its cost-capture step. Per-node status lines in
   that stream are keyed by command name for `command:` nodes
   (`[dark-factory-implement] Started`, not `[implement]`) — derive a
   `command_name -> node_id` map from the workflow YAML (resolved as described under
   Architecture; the `command:` field when present, else the node's own `id:`) and
   match log lines
   anchored at line start against only the names that map resolves, so agent-generated
   text that happens to contain `[something] Started` mid-line or for an unknown name
   can never be mistaken for a real node-status line.
4. **No-log fallback.** When `$TMP_OUT` is unavailable or no recognizable line parses
   from it, Requirement 2 is not applied: no node is synthesized from the static DAG.
   Requirement 1 still applies (it needs only the YAML and the cost report).
   `totals.node_log` is `"unavailable"` and one
   `factory.cost_report.node_log_unavailable` health event is emitted, so a parser
   regression is visible without marking skipped nodes missing. When
   `archon_cost_capture.ok == false`, no classification runs at all.
5. **Entrypoint threads the log file into both existing assemble call sites** — no new
   call site, no duplicate assembly (see "Why this spec replaces the rejected draft" #2):
   - The success path (`entrypoint.sh:1076-1102`) currently deletes `$TMP_OUT`
     (`rm -f "$TMP_OUT"`) *before* calling `run-record assemble`. Move that deletion to
     after the assemble call, and pass the path to assemble.
   - `on_failure`'s own assemble call (`entrypoint.sh:559-571`) does not reference
     `$TMP_OUT` today. Pass it there too (`"${TMP_OUT:-}"`, matching the existing
     optional-variable convention at `:507`, `:587`) — `on_failure` can fire before the
     workflow-run loop ever sets `TMP_OUT` (e.g. a pre-clone failure), so the new
     argument must tolerate an empty/absent value exactly like `run_post_mortem` and
     `_write_error_signature` already do. The same call also gains
     `--clone-dir "${CLONE_DIR:-.}"`, which it lacks today (the success path already
     passes `--clone-dir` at `:1102`), so the workflow YAML resolves the same way on
     both paths (see Architecture).
   - The main loop's own inline nonzero-exit branch (`:1030-1075`, bare `exit
     "$EXIT_CODE"`) is unchanged — see "Out of scope."
6. **Totals gain capture-aware fields**, alongside the existing (unchanged-semantics)
   `totals.cost_usd` / `totals."gen_ai.usage.*_tokens"`:
   - `totals.cost_capture`: `"complete"` (no missing nodes), `"partial"` (at least one
     `"missing"` node), or `"unavailable"` (today's pre-existing case — the
     `archon workflow cost` call itself failed, `archon_cost_capture.ok == false`,
     `nodes == []`; behavior here is unchanged), or `"unknown"` (the workflow YAML
     could not be loaded, so node types are unknown and no node was classified — see
     Architecture).
   - `totals.node_log`: `"parsed"` or `"unavailable"` (Requirement 4).
   - `totals.known_cost_usd`, `totals.known_input_tokens`, `totals.known_output_tokens`:
     sums over `"completed"` nodes only — always numeric when `cost_capture != 
     "unavailable"`, even when partial (`null` when `"unknown"`).
   - `totals.missing_nodes`: list of `{node_id, reason}` for every `"missing"` node.
   - `totals.cost_usd` and the two token totals keep their existing meaning (a fully
     measured total) and are `null` whenever `totals.cost_capture == "partial"` —
     this is the same null-on-any-gap rule `_compute_harness_economics` and
     `reconcile_cost_reports.py` already apply to the `archon_cost_capture.ok == false`
     case, so neither needs a code change (see the Test Plan's regression cases).
7. **Cost-report rendering.** `cost_report.py::render` prints a `"missing"` node's row
   with `missing (<reason>)` in place of its cost cell (and nulled token cells) instead
   of a bare `$0`/`0`/`0`, and — only when `totals.cost_capture == "partial"` — adds one
   note line below the Subtotal row surfacing `known_cost_usd`/`known_*_tokens` and the
   missing node list, e.g.:
   `> ⚠️ Partial cost capture — 1 node unreported (implement: no_usage_reported). Known
   spend: $1.89 (112 in / 27.4K out); true total is higher.`
   On a partial run the Subtotal row, the header total and the
   `<!-- cumulative: ... -->` accumulator use `known_cost_usd`/`known_*_tokens` (never
   `0`), so `parse_prior_cumulative` keeps a monotonic lower bound.
   A fully-`"complete"` run's rendered Markdown — including the Subtotal/Total lines —
   stays byte-identical to today's output (the Test Plan's healthy-run fixture is the
   guard).
8. **`run-record assemble` must not crash on a null node field.** The existing
   `nodes` summation (`run_record.py:723-725`) sums every node's `cost_usd`/token
   fields unconditionally; once a `"missing"` node's fields are nulled per Requirement
   1, that sum must skip `"missing"` nodes (it already needs to, to produce
   `known_cost_usd` per Requirement 6) rather than raising `TypeError` on `None + int`.
9. **Non-blocking signal.** `cmd_assemble` calls the existing in-process
   `emit_health_event` helper (`run_record.py:173-197`, already used by
   `cmd_record`'s ledger-write-failure path and `cli.py`'s `cost-report-check`) once per
   `"missing"` node classified, event name `factory.cost_report.node_missing`, detail
   `{node_id, reason}` — non-blocking, matching every existing health-event call site
   (never raises, never gates). Two further single-shot events use the same helper:
   `factory.cost_report.node_types_unavailable` (workflow YAML not loadable, see
   Architecture) and `factory.cost_report.node_log_unavailable` (Requirement 4).
10. **No gate/breaker/budget/scoring changes.** No code under `gate_*`, breaker,
    budget nodes, or `deploy/**`. `harness_economics.outcome.score`/`state` and #447's
    `needs_discussion`/clean-abort scoring are untouched — Requirement 6 keeps
    `totals.cost_usd`'s existing null-on-gap contract exactly, which is the only input
    those computations read.

## Architecture / Approach

**`workflows/archon-dark-factory.yaml` as the node-type/command-name source.** A new
helper (likely in `run_record.py`, since `cmd_assemble` already loads `adapter.py` for
`loops`) parses the workflow YAML once per assemble call — resolved as
`<--clone-dir>/.archon/workflows/archon-dark-factory.yaml` (the copy Archon executes,
`entrypoint.sh:786-788`), falling back to
`/opt/dark-factory/workflows/archon-dark-factory.yaml` — and builds two
maps: `node_id -> type` (`"command"` if a `command:` key is present, `"prompt"` if a
`prompt:` key is present, `"bash"` if a `bash:` key is present) and
`command_name -> node_id` (only for
`command:` nodes — `dark-factory-implement -> implement`, etc.). `prompt:`/`bash:`
nodes have no distinct command name; their log token (Requirement 3) is their own
`id:`.

Requirement 5 also adds `--clone-dir "${CLONE_DIR:-.}"` to `on_failure`'s assemble
call, which lacks it today. If neither file loads, node types are unknown: no node is
classified, `totals.cost_capture` is `"unknown"`, `totals.cost_usd` and the token
totals keep today's values, and one `factory.cost_report.node_types_unavailable`
health event is emitted. An unknown type is never treated as `bash`.

**Log parsing.** A line-start-anchored regex over `$TMP_OUT`'s lines,
`^\[([A-Za-z0-9_-]+)\]\s+(Started|Completed|Failed|Skipped)\b`, resolved against the
combined `{node_id, command_name} -> node_id` lookup — any bracketed token that isn't a
known name is ignored (this is deliberately conservative: agent transcript output
piped into the same stream could coincidentally contain a line matching the pattern,
and an unknown name is the cheap, unambiguous way to reject it). The parser produces,
per node id, the last event seen (`Started`/`Completed`/`Failed`/`Skipped`) — `tee`
writes lines in real time, so a well-formed stream has at most one terminal event per
node.

**Classification order** (Requirements 1-2, applied per node id in
`node_id -> type` once nodes + log events + cost-report entries are all resolved):
1. Node appears in the cost-report JSON, type is `bash` → `"completed"` (zero is
   normal).
2. Node appears in the cost-report JSON, type is `command`/`prompt`, usage nonzero →
   `"completed"`.
3. Node appears in the cost-report JSON, type is `command`/`prompt`, usage all-zero →
   `"missing"`, `reason: "no_usage_reported"`.
4. Node does not appear in the cost-report JSON, log shows `Skipped` → omitted
   entirely (not emitted as a node, not counted toward `missing_nodes`).
5. Node does not appear in the cost-report JSON, type is `command`/`prompt`, log shows
   `Started`/`Completed`/`Failed` (ran or started running) → `"missing"`,
   `reason: "not_in_cost_report"`. A `bash` node in the same position is not emitted.
6. Node neither in the cost report nor the log is simply not emitted — there is no
   evidence it was ever expected for this run's intent. With no parseable log
   (Requirement 4), steps 4-5 are not applied and only steps 1-3 run.

**Entrypoint change is two call-site edits, not new logic** (Requirement 5): thread
`--archon-log-file "$TMP_OUT"` (success path) / `--archon-log-file "${TMP_OUT:-}"`
(`on_failure`) into the two existing `run-record assemble` invocations, add
`--clone-dir "${CLONE_DIR:-.}"` to the `on_failure` one, and reorder the
success path's `rm -f "$TMP_OUT"` to run after assemble instead of before it.
`run_record.py`'s `assemble` subcommand gains an optional `--archon-log-file` argument;
absent or unreadable, Requirement 4's no-log fallback applies (never a hard failure —
matches every other `|| true`-guarded step around these call sites).

## Alternatives Considered

1. **Patch Archon's own dag-executor/CLI to persist usage on every `node_completed`
   event, stop coercing an absent cost to `0`, and stop filtering
   `archon workflow cost` to `node_completed`-only events.** This is the
   actual upstream root cause of why a node's usage is lost in the first
   place. Rejected for this ticket: Archon is a separate installed package
   (`/opt/archon`-equivalent at runtime) outside this repo's reach, and this ticket's
   authorized outputs are a spec doc and optional memory entries only. Filed as a
   follow-up (see "Open Questions").
2. **Diff the static DAG against the cost report unconditionally (the rejected
   draft's approach), without the log stream.** Simpler, but produces false positives
   on every run whose intent skips nodes via conditional DAG edges (e.g. a `refine`
   run never reaches `implement`) — exactly the class of regression Requirement 4 exists
   to avoid, which is why it is not used, even as a fallback.
3. **Read raw SDK transcripts / maintain an independent pricing table to recover the
   real spend of a `"missing"` node.** Rejected — out of scope per the harness-economics
   design's existing cost-source policy (compute from Archon's own reported numbers,
   never re-derive a parallel pricing path); a `"missing"` node's true cost stays
   genuinely unknown, which is exactly what `cost_capture: "missing"` communicates.

## Open Questions (non-blocking)

- File an Archon-upstream issue for the actual data-loss root cause (a
  `node_completed` event is written without `cost_usd`/`tokens` when no result message
  arrived — `dag-executor.ts:1174` — and `buildCostEntries` coerces the absent value to
  `0`, `workflow.ts:1166`; separately, `node_failed` nodes are filtered out of the
  report) — tracked as a follow-up, not blocking this ticket.
- The main loop's own inline nonzero-exit branch (`entrypoint.sh:1030-1075`) is
  unreachable in production: the ERR trap fires on the `archon workflow run | tee`
  pipeline first, and `on_failure`'s `set -e` (`:558`) ends the script when the trap
  returns. Follow-up ticket: remove or repair that branch (its rate-limit retry never
  runs).

## Assumptions (flagged)

- The log-line format is confirmed against a live run log for all three node types:
  `[dark-factory-refine] Started` (`command:` node, keyed by command name),
  `[parse-intent] Started` (`prompt:` node, keyed by `id:`),
  `[fetch-issue] Completed (1.8s)` (`bash:` node, keyed by `id:`) and
  `[digest-comments] Skipped (when_condition)`. The format is still not a documented
  stable Archon interface; Requirement 4's no-log fallback bounds the blast radius if
  it changes (Requirement 2 stops applying and one health event fires, never a crash
  or a synthesized node).
- "Expected node" for Requirement 2 means "the log
  shows this node as started/run for this specific intent's path through the DAG," not
  "every node any intent could ever reach."

## Out of Scope

- Any change inside Archon itself (its dag-executor, its CLI's `node_completed`
  filtering) — a separate installed package this repo does not control.
- `entrypoint.sh`'s main-loop inline nonzero-exit branch (`:1030-1075`) — dead code
  after `on_failure`; untouched here.
- Any change to `gate_*`, breaker, budget nodes, or `deploy/**`.
- Any change to `harness_economics.outcome.score`/`state` computation or #447's
  `needs_discussion`/clean-abort scoring logic.

## Test Plan

- `tests/test_run_record.py`: per-node classification (bash-zero stays `"completed"`;
  command/prompt-zero → `"missing"`/`no_usage_reported`; log-`Started`-but-absent →
  `"missing"`/`not_in_cost_report`; log-`Skipped` → omitted; no parseable log →
  Requirement 2 not applied, no synthesized node, `totals.node_log == "unavailable"`,
  one `factory.cost_report.node_log_unavailable` event;
  `archon_cost_capture.ok == false` → no classification; unloadable workflow YAML → no
  node classified, `totals.cost_capture == "unknown"`, totals keep today's values, one
  `factory.cost_report.node_types_unavailable` event, no node treated as `bash`);
  `totals.cost_capture`/`known_*`/`missing_nodes`
  on a partial run; `totals.cost_usd` stays non-null and byte-identical on a fully
  complete run; no `TypeError` summing a run with a nulled node.
- `tests/test_cost_report.py`: a `"missing"` node renders `missing (<reason>)` in place
  of its cost cell; the partial-run note line renders only when
  `cost_capture == "partial"`; on a partial run the Subtotal row, header total and
  `<!-- cumulative: ... -->` accumulator carry `known_*` (never `0`) and
  `parse_prior_cumulative` reads them back; a complete run's full rendered body is
  byte-identical to today's golden output (regression guard for Requirement 7's "no
  change on a healthy run" claim).
- New `tests/test_entrypoint_cost_capture_log_thread.sh` (listed in
  `.github/workflows/ci.yml` alongside the existing `test_entrypoint_cost_report_*.sh`
  tests): asserts both `run-record assemble` call sites in `entrypoint.sh` pass
  `--archon-log-file`, that `on_failure`'s call passes `--clone-dir`, and that the
  success path's `rm -f "$TMP_OUT"` now appears after the assemble call, not before
  it. `.sh` tests run only in CI (they cannot run on the Windows operator host), and
  this one is a static assertion on `entrypoint.sh` text — the behavior itself is
  covered by the pytest cases above.
- `tests/test_reconcile_cost_reports.py`: existing behavior unchanged (no code change
  to that script) — add one case confirming it still reads a partial run's (null)
  `totals.cost_usd` the same way it already reads an `archon_cost_capture.ok == false`
  run's null total.
- Regression test (new, in `test_run_record.py` or a harness-economics-focused test
  file): `outcome.score`/`outcome.state` are unchanged by `cost_capture: "partial"`;
  `factory_cpm` and `tokens_per_task` are `null` (rendered `n/a`), never computed from
  a partial token sum.
- Required fixtures (per the operator's constraints): (a) the real #388 shape —
  `implement` present, `0`/`0`/`$0`, `25m 55s` duration, type `command`; (b) a log
  sample containing `[dark-factory-implement] Started`; (c) a fully healthy run
  (all nodes report nonzero or are legitimately-zero `bash:` nodes) whose assembled
  record and rendered comment are byte-identical to pre-change output; (d) a node that
  ran (log `Started`, no `Completed`/`Failed`) but never appears in the cost-report
  JSON at all; (e) the same with a `Failed` log line — the operator constraints'
  "failed node absent from the report", as a pytest fixture.
