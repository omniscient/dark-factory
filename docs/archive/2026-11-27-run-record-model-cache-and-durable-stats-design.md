# Run Records: Correct Capture of Model, Cache Tokens and Cost (first half of #645)

**Issue:** #645

**Scope of this spec:** the capture half of #645 only. The durability half (rotation, the host
stats mount) moves to a follow-up ticket; see "Moved to the follow-up ticket". This spec touches
nothing under `deploy/**`.

File and line citations are against `main` at `a2129ea`.

## Owner decisions (2026-11-27)

Decided by the owner in the operator session on 2026-11-27. They bind this spec.

1. **Durable storage** is a scheduler-only stats mount with a per-poll sync (the scheduler
   mirrors; run containers never see the mount). It is built in the follow-up ticket, not here.
2. **Overstated costs** (see "Facts", item 6): add `cost_usd_session_delta` per node and a
   deduplicated run total, additively. Every existing field keeps its value and meaning.
3. **#645 is split in two.** This spec is the first half: correct capture of model, cache
   tokens and cost. The second half is the follow-up ticket.

## Problem

Run records are the only long-term record of what the factory spends. On every node of every
record measured in the issue (410 records), `model` is `""` and no cache tokens are recorded.
Sub-agent and small-model spend is not recorded at all, and for some nodes the recorded cost
is too high.

## Facts this design rests on

All measured, on the five runs whose transcripts the operator retained (20 transcript files),
on those runs' run records, and on Archon 0.3.10 in the factory image. None is inferred.

1. **Archon never supplies `modelUsage`.** `archon workflow cost --json` reports
   `"modelUsage": null` on every node, model-calling or not. `_nodes_from_run_obj`
   (`run_record.py:265-289`) reads `model` from that key, so `model` is always `""`. The same
   function also keeps only the first model, cuts the ID at `-20`, strips `claude-`, and never
   reads cache tokens. The cause of the `null` is in `omniscient/Archon` and is not fixable
   from this repository.
2. **The CLI writes a transcript per session** under `$HOME/.claude/projects/<slug>/` in the
   run container (`/home/factory`). Each `assistant` line carries `message.id`,
   `message.model` (the full ID) and `message.usage` (`input_tokens`, `output_tokens`,
   `cache_read_input_tokens`, `cache_creation_input_tokens`), plus a `timestamp`.
3. **One API message is several lines.** A transcript has one `assistant` line per content
   block, and every line of one message repeats the message's whole `usage`. In one refine
   session: 146 lines, 70 distinct `message.id`; summing lines gives cache-read 24,537,382
   where the true figure is 12,420,802. All copies of an id carry identical usage in all 20
   files.
4. **A resumed session is copied into a new file.** Archon resumes one session across
   conformance → code-review → revise-advisory. The CLI writes a new transcript file each
   time, and the new file repeats the earlier messages. In the `fix` run for #473 the three
   files hold 45, 70 and 103 distinct ids; all 45 and all 70 reappear in the next file.
5. **The CLI records each session's complete usage itself.** When a session ends, the CLI
   appends one line of this shape to its transcript:

   ```json
   {"type": "cost-state", "startTime": 1792629404055, "totalCostUSD": 2.5850786,
    "modelUsage": {"claude-sonnet-5": {"inputTokens": 90, "outputTokens": 22298,
      "thinkingTokens": 7231, "cacheReadInputTokens": 4283508,
      "cacheCreationInputTokens": 121337, "webSearchRequests": 0, "costUSD": 1.5652096},
     "claude-opus-5-5": {"...": "..."}}}
   ```

   - It includes models that never appear in the session's own `assistant` lines: sub-agent
     models and the CLI's own small-model calls.
   - Its `totalCostUSD` equals the node's recorded `cost_usd` to the last digit for every
     node that did not resume a session (parse-intent, implement, classify-preview, validate,
     refine, plan).
   - On a resumed chain it is cumulative: files of one chain share `startTime`, and each
     file's line includes everything before it.
   - 17 of the 20 retained files have the line. The other three were copied from a container
     that was still running. A killed or timed-out session has none.
6. **Archon's `cost_usd` is cumulative on a node that resumes a session; its tokens are
   not.** In the `fix` run for #473: conformance $2.585, code-review $3.865 (the session's
   own delta is $1.280), revise-advisory $5.650 (delta $1.785). The run total is recorded as
   $13.11; the sessions' own totals give $6.66. This is one run, from 2026-10-22; whether the
   current image still does it is not verified.
7. **Archon's node-level tokens are the main thread only.** They equal the sum, over the
   node's distinct main-thread messages, of `input_tokens` and `output_tokens`. Sub-agent
   and small-model tokens are not in them.
8. **Not every session is a node.** Each run has a session, "Generate a concise conversation
   title…" (`claude-sonnet-5`, $0.014–$0.045), that starts within 50 ms of parse-intent. It is
   Archon's own call and is in no node's cost today.
9. **Archon's log has node times.** `archon workflow run`'s combined output, already captured
   to `$TMP_OUT` (`entrypoint.sh:1209`) and passed to `run-record assemble` as
   `--archon-log-file` (`entrypoint.sh:748`, `:1290`), holds one JSON line per event when
   stdout is not a terminal:

   ```
   {"level":30,"time":1795806798411,"pid":68,"hostname":"…","module":"workflow.dag-executor","nodeId":"first","type":"bash","msg":"dag_node_started"}
   {"level":30,"time":1795806799421,"pid":68,"hostname":"…","module":"workflow.dag-executor","nodeId":"first","durationMs":1011,"msg":"dag_node_completed"}
   {"level":30,"time":1795806799429,"pid":68,"hostname":"…","module":"workflow.dag-executor","nodeId":"skipped","when":"…","msg":"dag_node_skipped_condition"}
   ```

   `time` is epoch milliseconds. The run is bracketed by `dag_workflow_starting` and
   `dag_workflow_finished` lines from the same module. Nodes with no model call emit
   started/completed like any other. A bash node's stdout is not streamed into this log; a
   model's text output is. These are not the `[<node>] Started` progress lines that
   `_NODE_LOG_RE` (`run_record.py:356-359`) matches; those carry no time.
10. **No price table exists** anywhere under `scripts/`. Every `cost_usd` in a record is the
    number Archon reports.

With the rules of requirement 3 below, the `fix` run for #473 reproduces Archon's node-level
input/output tokens exactly for parse-intent (20/695), implement (26/6,795), classify-preview
(18/2,201), validate (22/4,471), conformance (90/22,298) and code-review (50/9,788). A rule
that assigns a whole file to the node holding most of its timestamps gave conformance
436/103,255 and gave code-review and revise-advisory nothing; it must not be used.

## Requirements

### 1. Record shape: additive only

`model` stays a **string**. `cost_report.py:443,454` prints it into a table cell, and
`reconcile_cost_reports.py` and the operator's `run_summary.py` read node dicts.

Per node, existing keys are unchanged in value and meaning: `node_id`, `model` (see below),
`gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` (Archon's node-level numbers),
`cost_usd` (Archon's number), `duration_ms`.

New keys per node, all optional for readers:

| Key | Type | Meaning |
|---|---|---|
| `models` | list | one entry per model the node used: `{model, input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens}` and, when the source has them, `thinking_tokens`, `web_search_requests`, `cost_usd`. Sorted by `model`. Absent when unknown |
| `gen_ai.usage.cache_read_input_tokens` | int | sum over `models`. Absent when unknown, never 0 for unknown |
| `gen_ai.usage.cache_creation_input_tokens` | int | same |
| `usage_source` | string | `cli_cost_state`, `transcript_messages`, `archon` or `none` |
| `usage_reconciled` | bool | requirement 4. Absent when `usage_source` is `none` |
| `cost_usd_session_delta` | number | requirement 5. Present only with `cli_cost_state` |
| `hosting_location` | string | `local` when the run's `model_dispatch` result says so, else `cloud`. Only on nodes that called a model |

`model` is: the full model ID when `models` has one entry; the IDs joined with `,` (sorted)
when it has several; `""` when unknown. IDs are never shortened or altered.

The cache key names are the request ledger's (`model_proxy.py:141-142`).

New keys on the run record:

| Key | Meaning |
|---|---|
| `unattributed_sessions` | list, requirement 3. Always present; `[]` when none |
| `totals.cost_usd_deduplicated`, `totals.cost_usd_deduplicated_complete` | requirement 5 |
| `totals.gen_ai.usage.cache_read_input_tokens`, `totals.gen_ai.usage.cache_creation_input_tokens` | sums over nodes that have the key; absent when no node has it |
| `factory_instance` | `FACTORY_INSTANCE`, or `""` |
| `target_repo` | `FACTORY_REPO_SLUG` (`entrypoint.sh:102`), or `""` |
| `harness` | `{"name": "claude-code", "version": <CLI version or null>}` |

A node with no model call (17 of 25 in a `fix` run) gets `model: ""`, `usage_source: "none"`
and none of the other new keys.

### 2. Node windows from the Archon log

A new parser reads the file given as `--archon-log-file` and returns, per node id,
`[start_ms, end_ms]`.

- A line counts only if it parses as a JSON object with `module ==
  "workflow.dag-executor"`, an integer `time`, a `nodeId` that is one of the run's node ids,
  and `msg` exactly `dag_node_started` or `dag_node_completed`.
- A line whose `time` is outside `[dag_workflow_starting.time, dag_workflow_finished.time]`
  is ignored. If the finished line is absent (a failed or killed run), the upper bound is the
  largest `time` of any accepted line of that module.
- The **first** started line and the first completed line of a node win; later ones are
  ignored.
- A node with a start and no completion (a failed node): its window ends at the next accepted
  line's `time` for any node, or at the upper bound. The plan captures one failing run's log
  as a fixture; if it shows a dedicated failure event for the node, that event's `time` is
  the end instead.
- A node with no accepted start line has no window.
- Model text streamed into the log could contain a look-alike line with every field valid.
  This parser cannot exclude that. The consequence is a wrong attribution, which requirement 4
  reports as `usage_reconciled: false`; no gate reads these numbers.
- The existing `_parse_node_log` behaviour is unchanged.

The lines exist only when Archon logs at level `info` or lower. A test pins that
`entrypoint.sh` runs `archon workflow run` without `--quiet` and sets no `LOG_LEVEL`.

### 3. Usage per node from the session transcripts

Input: every `*.jsonl` file under `$HOME/.claude/projects/`, recursively, modified at or
after the run's `started_at`. Each file is read line by line with a per-line size cap; a line
that is not a JSON object is skipped. Nothing raises on a malformed file.

1. **Messages.** From every file, collect `assistant` lines. Keep **one** record per
   `message.id` across the whole run: `(timestamp, model, usage, files it appears in)`. A
   line with no `message.id` is its own record.
2. **Chains.** Files that share any `message.id` are one *session chain*. A file sharing none
   is a chain of one.
3. **Attribution by message time.** A message belongs to the node whose window contains its
   `timestamp`. A message in no window, or in two (parallel nodes), is unattributed.
4. **The node's chain.** Among the chains with at least one message in a node's window:
   - the node's chain is the one whose messages in the window sum to exactly Archon's
     node-level `inputTokens` and `outputTokens` (main-thread lines only, `isSidechain` not
     `true`);
   - if exactly one chain has messages in the window and it does not match, it is still the
     node's chain, and the node is unreconciled;
   - if several chains have messages there and none matches, or more than one matches, the
     node has no chain: `usage_source` falls to `archon`/`none` and all of them are reported
     as unattributed.
5. **`unattributed_sessions`.** Every chain that is no node's chain is listed on the run
   record: `{"first_prompt": <first 60 characters of the chain's first user text, control
   characters removed>, "started_at": <ISO time of its first line>, "models": [...same shape
   as a node's...], "cost_usd": <its cost-state total, or null>, "window_node": <node whose
   window held most of its messages, or null>}`. It is added to no node and to no existing
   total. This is where the title-generation session goes.
6. **Figures for the node, in this order of preference:**
   - **`cli_cost_state`.** The node's *file* is the file of its chain whose last message
     timestamp falls in the node's window and is the latest such. If that file has a
     `cost-state` line: the node's per-model figures are that line's `modelUsage` minus the
     `modelUsage` of the chain's previous file (the file with the next-smaller message count)
     when that previous file also has a `cost-state` line and the same `startTime`; with no
     previous file they are the line's own. A negative difference for any field, or a previous
     file without the line, means this source is unavailable for the node.
   - **`transcript_messages`.** Per-model sums over the node's chain's messages in the
     window, sub-agent files included when they are part of the input. No cost.
   - **`archon`.** Requirement 6.
   - **`none`.**
7. **Sub-agent transcripts.** None of the 20 retained files is one (`isSidechain` is `false`
   on every line; the operator's copy tool took only top-level files), so where the pinned
   CLI writes them and how their ids relate to the parent's is **not verified**. The plan
   must run one real session that uses the `Agent` tool in the factory image, and commit a
   trimmed, scrubbed fixture of the resulting directory. Rules that must hold whatever the
   layout: a sub-agent file's messages are attributed by time like any other; a sub-agent
   file whose messages fall in a node's window belongs to that node's chain for the
   `transcript_messages` source; it never takes part in the token match of rule 4. If the
   implement container cannot run a real session, the fixture is hand-built from the
   documented shape, the PR body says so, and `cli_cost_state` remains the only verified
   source of sub-agent spend.
8. `cost-state` and the transcript layout are internals of the pinned CLI (2.1.282), not a
   documented interface. When neither shape is found, the node records `usage_source:
   "none"` (or `archon`) and nothing is guessed. The PR body states this dependency.

The parser lives in a new module, `scripts/factory_core/session_usage.py`, with no import of
`run_record`. `run-record assemble` gains one optional argument, `--transcripts-dir`, and
calls it. Without the argument, or when the directory is missing, assembly behaves as it does
today plus requirement 6.

### 4. Reconciliation (acceptance criterion 2)

`usage_reconciled` is `true` only when both hold:

- (a) the node's chain's main-thread `input_tokens` and `output_tokens` in the window equal
  Archon's node-level tokens;
- (b) with `cli_cost_state`: the per-model `cost_usd` values sum to
  `cost_usd_session_delta` within $0.000001, and, for a node whose file is the first of its
  chain, `cost_usd_session_delta` equals the node's `cost_usd` within $0.000001.

With `transcript_messages`, only (a) applies. With `archon`, the per-model `costUSD` sum
must equal `cost_usd` within $0.01. Sub-agent and small-model tokens are never compared with
Archon's node-level tokens. A mismatch changes no number; it only sets the flag.

No price table is added.

### 5. Session cost delta and the deduplicated total (owner decision 2)

- `cost_usd_session_delta` on a node: the sum of its per-model `cost_usd` under
  `cli_cost_state`, which is its file's `totalCostUSD` minus the previous file's in the same
  chain.
- `totals.cost_usd_deduplicated`: over the nodes, the sum of `cost_usd_session_delta` where
  present and `cost_usd` otherwise. It is `null` when `totals.cost_usd` is `null`.
- `totals.cost_usd_deduplicated_complete` (bool): `false` when any node whose file is not the
  first of its chain has no `cost_usd_session_delta`. Such a node contributes Archon's
  cumulative `cost_usd`, so the total is still overstated. This happens in the measured run:
  the last file of the #473 chain (revise-advisory) has no `cost-state` line. No difference
  of Archon's own `cost_usd` values is ever substituted.
- `cost_usd` and `totals.cost_usd` are not changed, and no existing reader is switched to the
  new fields in this ticket. `docs/run-lifecycle.md` states which field to prefer and why.

### 6. The Archon `modelUsage` path, fixed, as fallback

In `_nodes_from_run_obj`: iterate every key of `modelUsage`/`model_usage`; keep each model
ID unmodified; read `inputTokens`, `outputTokens`, `cacheReadInputTokens`,
`cacheCreationInputTokens`, `costUSD` (the SDK's `ModelUsage` shape) into a `models` entry.
A value that is not an object (the placeholder `1` in today's test fixtures) yields an entry
with the model ID and no numbers. This path sets `usage_source: "archon"` and is used only
when requirement 3 yields nothing for the node. It produces nothing today (fact 1) and exists
for the day Archon is fixed.

### 7. Keep the transcripts the capture read

The capture needs the transcripts only inside the run container, but the backfill
(requirement 8) and any later re-derivation need them afterwards, and the container is
`--rm`.

- One shell function in `entrypoint.sh`, called on every exit path after the Archon run
  started: the success path (before the `rm -f … "$TMP_OUT"` at `entrypoint.sh:1296`), the
  failure path (`entrypoint.sh:728-750`) and the session-window pause path.
- It gzip-compresses every transcript file of requirement 3's input set into
  `$SCHEDULER_STATE_DIR/transcripts/<run_id>/`, keeping each file's path relative to
  `$HOME/.claude/projects/`. Directory mode 0700, files 0600. It never overwrites an existing
  file. A failure logs one line and never changes the run's exit status.
- Transcripts hold tool output, which can include file contents. They are never posted,
  logged or added to a run record.
- This stores them on the state volume only. Getting them onto the host is the follow-up
  ticket's sync.
- Nothing deletes them (the issue asks to keep them), so this grows the state volume without a
  bound until an operator prunes. Measured: the five retained runs hold 0.6-4.2 MB of
  transcripts each before compression. `docs/configuration.md` states the growth and that
  pruning is a manual operator action; the function logs the directory's total size once per
  run.

### 8. Backfill of the five retained runs

A sub-command `run-record backfill-usage --transcripts-dir <dir> --run-id <id>` that applies
requirement 3 to an existing `run-records/<run_id>.json`.

- The retained transcripts come without their Archon log, so there are no windows. For
  backfill only, a file is matched to a node by cost: the file whose
  `cost-state.totalCostUSD` equals the node's recorded `cost_usd` within $0.000001 is the
  node's file. This held for every node of the five runs that has a `cost-state` line. A node
  with no such file is left untouched.
- A backfilled node gets the keys of requirement 1 and `backfilled: true`.
- The original record is first copied to `run-records/<run_id>.pre-645.json` (never
  overwritten if it exists), then the record is rewritten through a temporary file and a
  rename. `runs.jsonl` is not rewritten.
- The five runs: `3ff088b6…` (instance `dark-factory`), and `ca74156e…`, `c368c654…`,
  `76ea5ef3…`, `67279dee…` (instance `dark-factory-self`). Running it is an operator step,
  documented in the PR body; the factory does not run it.
- Every other historical record has no retained source and stays as it is.

### 9. Tests

All hermetic; fixtures are trimmed and scrubbed (no tool output, no repository content, no
prompt text beyond the first 60 characters) and committed under `tests/fixtures/`.

| Requirement | Test |
|---|---|
| Real `modelUsage` payload, several models (criterion 1) | `_nodes_from_run_obj` on a fixture in the SDK shape with two models: both kept, IDs unmodified, cache tokens read; the placeholder shape still parses |
| One count per message | a transcript fixture with a three-block message: counted once |
| Resumed chain | three files where each repeats the previous one's messages: each node gets only its own messages; totals equal the distinct-message sum |
| Message-time attribution | a message outside every window, and one inside two overlapping windows, are unattributed |
| Ambiguous window (rule 4) | two chains in one node's window, neither matching Archon's tokens; and two chains both matching: the node gets no chain, `usage_source` is `archon` or `none`, no new numeric key is written for it, and both chains are in `unattributed_sessions` |
| Deduplicated total | a resumed chain whose last file has no `cost-state` line: `cost_usd_deduplicated_complete` is `false`; with every delta present it is `true` |
| Title session | a second chain inside the first node's window that does not match its tokens is listed in `unattributed_sessions` and added to no node |
| `cost-state` primary | first-of-chain node: figures equal the line's; resumed node: figures equal the difference; a model present only in `cost-state` appears in `models` |
| `cost-state` absent | fallback to `transcript_messages`, no cost keys |
| Reconciliation | match, token mismatch, cost mismatch, and a resumed node whose `cost_usd` is cumulative: `cost_usd_session_delta` is the difference and `totals.cost_usd_deduplicated` uses it |
| Node windows | a fixture cut from a real `$TMP_OUT`: bash node, model node, skipped node, failed node; a line with a wrong module, a non-integer time, an unknown node id, or a time outside the run is ignored; a second started line is ignored |
| Log level pinned | `entrypoint.sh` runs `archon workflow run` without `--quiet` and without `LOG_LEVEL` |
| Nodes with no model call | `model: ""`, `usage_source: "none"`, no other new key |
| Hostile transcript | a non-JSON line, a non-object line, an oversized line, a directory and a FIFO in the transcripts directory: no exception, no hang (10 s alarm) |
| Reader compatibility | a new-shape record renders through `cost_report`, `reconcile_cost_reports.py` and the operator's `run_summary.py` with the same output as before for the existing columns; `model` is a string in every case |
| Existing totals unchanged | for one fixture, `totals.cost_usd`, the token totals and every pre-existing node key are byte-equal with and without `--transcripts-dir` |
| Transcript copy | the function is called on the three exit paths; it writes 0600 files, never overwrites, and a failure leaves the exit status unchanged |
| Backfill | cost match; a node with no matching file untouched; the `.pre-645.json` copy made once; rename, not in-place write |

`tests/test_run_record.py`'s existing `modelUsage` placeholder fixtures stay green.

New tests are pytest, or additions to shell tests already listed in
`.github/workflows/ci.yml`. No new `tests/test_*.sh` file, so `ci.yml` is not edited.

## Files touched

| File | Change |
|---|---|
| `scripts/factory_core/session_usage.py` | new: log windows, transcript reading, chains, attribution, `cost-state` |
| `scripts/factory_core/run_record.py` | `_nodes_from_run_obj` (requirement 6); `cmd_assemble` gains `--transcripts-dir`, the new keys and totals; `backfill-usage` |
| `scripts/factory_core/cli.py` | the new argument and sub-command, if the parser lives there |
| `entrypoint.sh` | pass `--transcripts-dir` on both `assemble` calls; the transcript-copy function on three exit paths |
| `tests/test_session_usage.py` (new), `tests/test_run_record.py`, `tests/fixtures/**` | requirement 9 |
| an existing CI-wired entrypoint shell test | the transcript-copy and log-level assertions |
| `docs/run-lifecycle.md`, `docs/configuration.md` | the new record keys, which cost field to prefer, where transcripts are kept |
| `.claude/skills/operator/scripts/run_summary.py` | only if the compatibility test shows it needs a change; otherwise untouched |

Not touched: anything under `deploy/**`, `run-compose.yml`, `scheduler.sh`,
`scripts/factory_core/model_proxy.py`, `scripts/factory_core/cost_log.py`, `workflows/**`,
`config/**`, `.github/workflows/**`, any `gate_*` script, the breaker, budgets, tool lists.

## Blast radius

Expected `scripts/gate_blast_radius.py` verdict: **HUMAN_REQUIRED**, one trigger:
`scripts/factory_core/run_record.py` (hotspot, score 5.5). `entrypoint.sh` is on the
adapter's `critical_diff_paths`, which orders the review diff and does not block.
`.github/workflows/ci.yml` would be a second trigger; requirement 9 avoids editing it.
`.claude/skills/operator/scripts/run_summary.py` matches the adapter's skill-script pattern
and would be a third; it is edited only if the compatibility test requires it. A new module
has no dependents and is not a hotspot. Stopping at the hold for human review is the intended
outcome.

## Moved to the follow-up ticket

Not in this spec, and not to be implemented under it:

- Model-proxy ledger rotation: never delete a generation; numbered, gzip-compressed
  generations whose numbers only grow; rotation and the compress swap under the ledger lock.
- `run_record._read_ledger_rows` reading `.gz` generations under a shared lock, bounded to
  the run's time range.
- `cost_log.py`: the same rotation, and the lock it does not have today.
- Durable storage (owner decision 1): the stats mount on the scheduler service, the
  per-poll `stats-sync`, migration of the existing records, growth logging, and every edit
  under `deploy/**` (`deploy/docker-compose.yml`, `deploy/instance.env.example`, a placeholder
  directory).
- Mirroring `transcripts/` (requirement 7) to the host.
- Acceptance criteria 3, 4 and 5 of #645 in full, and the "keep each run's session
  transcripts … in the same store" line beyond requirement 7's state-volume copy.

## Follow-ups outside both tickets

- `omniscient/Archon`: `modelUsage` is never relayed (fact 1); `cost_usd` is cumulative on a
  resumed node (fact 6); a `session_id` on node events would replace the time windows with an
  exact join. The operator files these.
- Switching `cost_report` and the scheduler's economics to `cost_usd_session_delta` /
  `totals.cost_usd_deduplicated`: its own ticket, once this has produced records.
- The model-proxy ledger's own cache fields are hard-coded to 0 (`model_proxy.py:400`). Not
  changed here; the proxy is opt-in and was not the source of the measured records.

## Acceptance criteria of #645 covered here

| Criterion | Status in this spec |
|---|---|
| 1. Non-empty `model` (full ID) and cache tokens for every node that called a model; a test with a real multi-model payload | Covered (requirements 1, 3, 6, 9). A node whose session was killed and left no `cost-state` line gets them from `transcript_messages`; a node with no transcript at all stays `""` and says `usage_source: "none"` |
| 2. Tokens reconcile with `cost_usd` | Covered as requirement 4, without a price table |
| 3. Nothing deletes or overwrites the stats; rotation keeps all generations | Follow-up. Here: the backfill keeps the original record (requirement 8) and the transcript copy never overwrites |
| 4. Host bind mount, documented, history migrated | Follow-up |
| 5. `docker compose down -v` check | Follow-up |
| 6. Best-effort backfill | Covered (requirement 8), five runs |

## Not verified

- Sub-agent transcript files (requirement 3, rule 7).
- A failing node's log events (requirement 2).
- Fact 6 on the current image.
- The node windows of the five retained runs: their Archon logs were not kept, so the
  attribution was tested with windows built from each node's first prompt time and its
  recorded duration, written in the real log format.
- Parallel DAG nodes: none of the five runs has two model nodes running at once. Rule 3
  leaves messages in overlapping windows unattributed rather than guessing.
