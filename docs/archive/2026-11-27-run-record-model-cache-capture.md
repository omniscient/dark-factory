# Run Records: Model, Cache-Token and Cost Capture — Implementation Plan

**Issue:** #645

> **For the implementing agent:** you run headless. Follow the tasks in order, test first,
> commit after every task, and push before your turn ends. Record any reservation in the
> commit message or the PR body, never as a question. Every code block below was run in a
> scratch copy of this repository at `e9b2f38` during planning: the full pytest suite (4305
> tests), the CI shell tests that touch `entrypoint.sh`, the two workflow DAG checks and
> `scripts/docs_refs_check.py` all passed with exactly this code.

**Goal:** every node that called a model gets its full model ID(s), cache-read and
cache-write tokens, and (from the CLI's own `cost-state` line) a per-session cost delta,
captured from the Claude Code session transcripts, plus a deduplicated run total, a
backfill sub-command for the five retained runs, and a state-volume copy of each run's
transcripts. Additive only: no existing record key changes value or meaning (except
`model`, which was always `""`). Nothing under `deploy/**` is touched.

**Architecture:** a new stdlib-only module, `scripts/factory_core/session_usage.py`, reads
Archon's JSON log lines (`$TMP_OUT`, already passed as `--archon-log-file`) into per-node
`[start_ms, end_ms]` windows and reads every transcript under `~/.claude/projects`
modified since the run started: one record per `message.id` across the run, files sharing
an id form a session chain, a message belongs to the node whose window holds its own
timestamp, and a node's figures prefer the chain file's `cost-state` line (differenced
against the chain's previous file). `run_record.py` calls it from `cmd_assemble` through a
new optional `--transcripts-dir`, fixes `_nodes_from_run_obj`'s Archon `modelUsage`
fallback, adds the new node/run/totals keys, and gains `backfill-usage`. `entrypoint.sh`
passes `--transcripts-dir` on both `assemble` calls and gzip-copies the transcripts to
`$SCHEDULER_STATE_DIR/transcripts/<run_id>/` on every exit path once the workflow ran.

**Tech Stack:** Python 3.12 (CI) / 3.14 (image), stdlib only in the new module; bash;
pytest.

**Spec:** `docs/superpowers/specs/2026-11-27-run-record-model-cache-and-durable-stats-design.md`
(approved at `e9b2f38`). Requirement numbers below are the spec's.

---

## Facts verified while planning (2026-11-27, factory image, Claude Code 2.1.282, Archon 0.3.10)

These close items the spec lists under "Not verified", or pin shapes the code relies on.

1. **Archon's log format.** This plan run's own `$TMP_OUT` holds the JSON lines the spec
   quotes (fact 9): `dag_workflow_starting`, then per node `dag_node_started` (`provider`
   on AI nodes, `type: "bash"` on bash nodes) and `dag_node_completed`; skipped nodes log
   `dag_node_skipped_condition`/`dag_node_skipped` and no started line; bash stderr appears
   as `bash_node_stderr` lines with the same `nodeId`. Task 1's log fixture is a scrubbed cut
   of that file (`hostname` and the stderr text replaced with `"scrubbed"`).
2. **Failing-node event.** Archon 0.3.10's `packages/workflows/src/dag-executor.ts` logs
   `dag_node_failed` (level 50, with `nodeId`) when an AI node (line 1229), a bash node
   (line 1396) or a script node (line 1660) fails, and `dag_workflow_finished` (with
   `anyFailed`) at the end of a run (line 3072). Requirement 2's "dedicated failure event"
   therefore exists and ends the failed node's window. It was **not captured from a real
   failing run**: running `archon workflow run` inside a run container would make the
   entrypoint's own `archon workflow cost --last` read that run instead of the real one, so
   neither the planner nor the implementer may do it. The test's failure line is built
   from the source's shape. The PR body says so.
3. **Sub-agent transcripts (spec requirement 3, rule 7).** The planner ran one real
   `Agent`-tool session in the factory image. The CLI writes
   `<project-slug>/<parent-session-id>/subagents/agent-<agentId>.jsonl` plus a
   `agent-<agentId>.meta.json`. Every line of the `.jsonl` has `isSidechain: true`, the
   parent's `sessionId` and an `agentId`; its `message.id`s are not in the parent's file,
   and one message is still several lines. The parent's file has no `cost-state` line while
   the session runs. Task 1's `subagent-run` fixture is that directory, trimmed and
   scrubbed: the sub-agent fixture is real, not hand-built.
4. **Title session and main-thread tokens.** In this plan run, parse-intent's session has
   two distinct messages (input 10+10, output 135+159 = 20/294, the shape of the spec's fact
   7), and Archon's title-generation session (`claude-sonnet-5`) starts inside
   parse-intent's window (fact 8). The title session's real `cost-state` line carries two
   models, one of which (`claude-haiku-4-5-20251001`) appears in none of its assistant
   lines. Both files, trimmed, are Task 1's `plan-run` fixture: a real Claude Code
   multi-model `modelUsage` payload.
5. **CLI version.** Every transcript line carries `"version": "2.1.282"`; it is the source
   of the run record's `harness.version` (no new argument needed).
6. **Synthetic messages.** The CLI writes `"model": "<synthetic>"` on its own placeholder
   messages (e.g. an API error); they are skipped, never reported as a model.
7. **Test runner.** The factory image has no `pytest`. Create a throwaway venv (Task 0).
8. `find -newermt` accepts `RUN_STARTED_AT`'s `2026-11-27T20:13:14Z` form; `mv -n` and
   `gzip -c` are GNU coreutils 9.7 / gzip 1.14 in the image.

## Decisions where the spec leaves room

Each is a reading of the spec, not a scope change; the PR body repeats them.

- **D1 Which nodes "called a model".** A `command:`/`prompt:` node (types from the workflow
  YAML, as `_load_workflow_node_maps` already resolves them) that has a log window, a
  reported usage, or a `cost_capture: missing` mark. With no workflow YAML: any node with a
  reported usage. Every other node gets `model: ""`, `usage_source: "none"` and nothing else.
- **D2 Windows used for attribution** are those of model-calling nodes only. A bash node
  running beside a model node cannot own a message and must not make the model node's
  messages "in two windows".
- **D3 Upper bound when `dag_workflow_finished` is absent:** the largest integer `time` of
  any line of the `workflow.dag-executor` module, so a failed run's later lines (its
  `dag_node_failed`) count, not only started/completed lines.
- **D4 A previous file with a `cost-state` line but another `startTime`** makes
  `cli_cost_state` unavailable for the node (nothing is guessed); it falls to
  `transcript_messages`.
- **D5 `cost_usd_session_delta`** is the file's `totalCostUSD` minus the previous file's.
  Reconciliation (b) then compares the per-model `costUSD` sum against it, which is the only
  reading under which check (b) is not a tautology.
- **D6 Sub-agent chains** (every file `isSidechain`) are never a node's chain and never take
  part in rule 4's token match. Their messages are attributed by time and join the owning
  node's `transcript_messages` figures. Such a chain is listed in `unattributed_sessions`
  only when no node that has a chain owns any of its messages.
- **D7 Local-routed runs.** Contract §4.10 (already enforced by `cmd_assemble`) says a local
  run's cost is unknown, never a Claude-priced figure. So on a local run the per-model
  `cost_usd` values and `cost_usd_session_delta` are omitted; tokens are kept.
- **D8 Transcript-copy call sites.** "Every exit path after the Archon run started" covers
  success, the `on_failure` failure path, the session-window pause (both the `on_failure`
  branch and the run loop's own branch) and the blast-radius hold (both branches, since a
  hold is the expected outcome of this very ticket's PR). A guard variable keeps it to one
  copy and one size line per run.
- **D9 `test_parse_archon_cost_basic`.** Its placeholder fixture (`{"claude-sonnet-4-6-20251101": 1}`)
  is kept and still parses. Its assertion `model == "sonnet-4-6"` pinned the shortening that
  requirement 6 removes ("IDs are never shortened"), so it becomes the unmodified ID.
- **D10 Byte-equality test.** "Every pre-existing node key" excludes `model`, whose value is
  meant to change from `""`.
- **D11 Backfill.** A node with `cost_usd <= 0` is never matched (a bash node must not match
  a zero-cost file). The `.pre-645.json` copy and the rewritten record keep the record's
  original mtime, so the operator's `runs.sh` (`ls -t`) order does not change.
  `reconcile_cost_reports.py` globs `run-records/*.json` and so also reads the copy; it keys
  records by `run_id`, and the copy has the same `run_id` and `totals.cost_usd`, so its
  report is unchanged.
- **D12 `first_prompt`.** Unicode control characters (category `Cc`) are removed, then the
  text is cut at 60 characters.
- **D13 Fail closed on time.** An unparseable `--started-at` reads no transcripts (it could
  otherwise mix in files from before the run).
- **D14 `totals.cost_usd_deduplicated_complete` fails closed** (architect review, cycle 1).
  The spec makes it `false` when a node whose file is not first of its chain has no
  `cost_usd_session_delta`. A node with no transcript figures at all (no
  `--transcripts-dir`, ambiguous chains, a fall-back to `archon`/`none`) might be exactly
  such a node. So the flag is `true` only when every model-calling node either has
  `cost_usd_session_delta` or is known to be the first file of its chain. Otherwise a run
  whose transcripts were not read would show Archon's overstated total marked "complete",
  the one case the flag exists to catch.
- **D15 Both `run-record assemble` calls go through `_cli_with_hosting_fallback`**
  (architect review, cycle 1). Both calls run the clone's `cli.py`, and a target may track an
  older copy of it (`entrypoint.sh` copies the baked scripts in only when the target has
  none). An older `run_record.py` rejects `--transcripts-dir` with argparse exit 2, and the
  `|| true` would hide that the run got no `run-record.json` at all. #633's helper already
  reruns the baked `/opt/dark-factory/scripts/factory_core/cli.py` with the identical argv on
  exit 2 and returns any other status unchanged; its behaviour is covered by
  `tests/test_entrypoint_hosting_fallback.sh`. The helper's comment now names both uses; its
  code is unchanged. Through the helper, assemble's stdout and stderr are printed together on
  stdout after it exits (harmless for the container log).
- **D16 Hermetic harnesses.** `tests/test_entrypoint_error_signature.sh`,
  `tests/test_entrypoint_session_window.sh` and `tests/test_entrypoint_blast_hold.sh` (its
  section E) drive `on_failure`, which now calls
  `_keep_run_transcripts` against the real `$HOME`. In a factory container that would copy
  the container's own transcripts (tool output) into the test's temp state dir. All three
  harnesses stub the function to a no-op; its behaviour is tested in
  `tests/test_entrypoint_cost_capture_log_thread.sh`.

- **D17 Transcript-copy bounds (plan review by execution, 2026-11-27).** Executing this plan
  literally showed `_keep_run_transcripts` had no size or time bound: a 600 MB file placed
  under `~/.claude/projects` (the agent has a shell there) was stored whole (586,056 KiB) on
  the state volume, which also holds `scheduler-state.json`, and a link planted at
  `transcripts/<run_id>` was followed. The function now skips a file over 64 MiB and
  anything past 256 MiB of source per run (reported by the existing WARNING line, exit
  status unchanged), bounds each `gzip` with `timeout 120`, and refuses a link at the store,
  the run directory or an output name. Three assertions are added to Step 5.2. The largest
  real retained run is 4.2 MiB, so no real transcript is affected. `docs/configuration.md`'s
  row states the two bounds.

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/session_usage.py` | **new**: Archon log windows, transcript reading with a per-line cap, message dedup, chains, attribution, `cost-state` figures, reconciliation, unattributed sessions, backfill matching. No import of `run_record` |
| `scripts/factory_core/run_record.py` | `_archon_models`/`_models_field` + `_nodes_from_run_obj` (req. 6); `_called_model`, `_with_usage_keys`, `_apply_session_usage` (req. 1, 3-5); `cmd_assemble` calls it and adds the run keys; `--transcripts-dir`; `_backfill_usage`, `cmd_backfill_usage`, `backfill-usage` sub-command (req. 8) |
| `entrypoint.sh` | `_keep_run_transcripts` (req. 7) and its calls; `--transcripts-dir "${HOME}/.claude/projects"` on both `run-record assemble` calls, which now go through `_cli_with_hosting_fallback` (D15; one comment sentence added to that helper) |
| `tests/test_session_usage.py` | **new** (req. 9) |
| `tests/test_run_record.py` | one assertion updated (D9); #645 tests appended |
| `tests/test_entrypoint_cost_capture_log_thread.sh` | already in `ci.yml`: `--transcripts-dir`, the exit-2 fallback, log-level pin, call sites, copy behaviour |
| `tests/test_entrypoint_error_signature.sh`, `tests/test_entrypoint_session_window.sh`, `tests/test_entrypoint_blast_hold.sh` | already in `ci.yml`: stub `_keep_run_transcripts` to a no-op (D16) |
| `tests/fixtures/session_usage/**` | **new**: real Archon log cut, real trimmed transcripts (plan run, sub-agent run), SDK-shaped cost JSON |
| `docs/run-lifecycle.md` | §7 step 2: the new keys, which cost field to prefer, the transcript copy, the backfill |
| `docs/configuration.md` | a `(path) session transcripts` row: location, modes, unbounded growth, manual pruning |

`scripts/factory_core/cli.py` needs **no change**: its `run-record` sub-command forwards
every argument to `run_record.main()` (`argparse.REMAINDER`), where both the new argument
and the new sub-command are added. `.claude/skills/operator/scripts/run_summary.py` needs
**no change**: Task 3's compatibility test shows it renders a new-shape record exactly like
the old one. Not touched: `deploy/**`, `run-compose.yml`, `scheduler.sh`, `model_proxy.py`,
`cost_log.py`, `cost_report.py`, `reconcile_cost_reports.py`, `workflows/**`, `config/**`,
`.github/workflows/**`, any `gate_*` script, the breaker, budgets, tool lists.

---

## Task 0: Setup and baseline

**Files:** none.

- [ ] **Step 0.1: Check out the feature branch state.** You are on `feat/issue-645-*`, forked
  from `main` with the spec copied over by `setup-branch`. Confirm the spec is present:

```bash
test -f docs/superpowers/specs/2026-11-27-run-record-model-cache-and-durable-stats-design.md && echo spec-ok
```

  Expected: `spec-ok`.

- [ ] **Step 0.2: Get a test runner** (the image has no pytest):

```bash
python -m pytest --version 2>/dev/null || {
  python3 -m venv /tmp/venv && /tmp/venv/bin/pip install -q pytest pyyaml aiohttp
}
PY=$(python -m pytest --version >/dev/null 2>&1 && echo python || echo /tmp/venv/bin/python)
echo "$PY"
```

  Use `$PY` (re-derive it in each new shell) wherever this plan says `$PY`. All pytest
  commands run with `PYTHONPATH=scripts`, as CI does.

- [ ] **Step 0.3: Baseline.**

```bash
PYTHONPATH=scripts $PY -m pytest tests/test_run_record.py tests/test_cost_report.py -q 2>&1 | tail -1
bash tests/test_entrypoint_cost_capture_log_thread.sh | tail -1
```

  Expected: `... passed` with no failures, and `OK`.

- [ ] **Step 0.4: Never run `archon workflow run` (or anything that starts an Archon
  workflow) in this container** — see verified fact 2.

---

## Task 1: Fixtures

**Files:** create `tests/fixtures/session_usage/archon-log-plan-run.txt`,
`tests/fixtures/session_usage/plan-run/**`, `tests/fixtures/session_usage/subagent-run/**`,
`tests/fixtures/session_usage/archon-cost-sdk-model-usage.json`.

All four are scrubbed: no tool output, no repository content, no prompt text beyond the
first 60 characters (the parent session's prompt is replaced with `"scrubbed"`).

- [ ] **Step 1.1: Write the Archon log cut** (real lines from this plan run's `$TMP_OUT`,
  `hostname` and the stderr text scrubbed, progress lines kept to prove they are ignored):

```bash
mkdir -p tests/fixtures/session_usage
cat > tests/fixtures/session_usage/archon-log-plan-run.txt <<'EOF'
Running workflow: archon-dark-factory
{"level":30,"time":1795810393677,"pid":195,"hostname":"scrubbed","module":"workflow.dag-executor","workflowName":"archon-dark-factory","nodeCount":49,"layerCount":29,"hasIssueContext":false,"issueContextLength":0,"msg":"dag_workflow_starting"}
{"level":30,"time":1795810393681,"pid":195,"hostname":"scrubbed","module":"workflow.dag-executor","nodeId":"parse-intent","provider":"claude","msg":"dag_node_started"}
[parse-intent] Started
{"level":20,"time":1795810397201,"pid":195,"hostname":"scrubbed","module":"workflow.dag-executor","nodeId":"parse-intent","streamingMode":"batch","msg":"dag.structured_output_override"}
{"level":30,"time":1795810397203,"pid":195,"hostname":"scrubbed","module":"workflow.dag-executor","nodeId":"parse-intent","durationMs":3522,"msg":"dag_node_completed"}
[parse-intent] Completed (3.5s)
{"level":30,"time":1795810397206,"pid":195,"hostname":"scrubbed","module":"workflow.dag-executor","nodeId":"fetch-issue","type":"bash","msg":"dag_node_started"}
[fetch-issue] Started
{"level":30,"time":1795810398811,"pid":195,"hostname":"scrubbed","module":"workflow.dag-executor","nodeId":"fetch-issue","durationMs":1605,"msg":"dag_node_completed"}
[fetch-issue] Completed (1.6s)
{"level":30,"time":1795810398816,"pid":195,"hostname":"scrubbed","module":"workflow.dag-executor","nodeId":"digest-comments","when":"$parse-intent.output.intent == 'continue'","msg":"dag_node_skipped_condition"}
{"level":30,"time":1795810398816,"pid":195,"hostname":"scrubbed","module":"workflow.dag-executor","nodeId":"setup-refine-branch","type":"bash","msg":"dag_node_started"}
{"level":30,"time":1795810398816,"pid":195,"hostname":"scrubbed","module":"workflow.dag-executor","nodeId":"bench-mode-probe","type":"bash","msg":"dag_node_started"}
{"level":30,"time":1795810398833,"pid":195,"hostname":"scrubbed","module":"workflow.dag-executor","nodeId":"bench-mode-probe","durationMs":16,"msg":"dag_node_completed"}
{"level":40,"time":1795810399225,"pid":195,"hostname":"scrubbed","module":"workflow.dag-executor","nodeId":"setup-refine-branch","stderr":"scrubbed","msg":"bash_node_stderr"}
{"level":30,"time":1795810399228,"pid":195,"hostname":"scrubbed","module":"workflow.dag-executor","nodeId":"setup-refine-branch","durationMs":412,"msg":"dag_node_completed"}
{"level":30,"time":1795810399585,"pid":195,"hostname":"scrubbed","module":"workflow.dag-executor","nodeId":"plan","provider":"claude","msg":"dag_node_started"}
EOF
```

- [ ] **Step 1.2: Write the transcript fixtures and the SDK-shaped cost JSON** with this
  one-off generator (not committed). `plan-run/` is parse-intent's and the title
  session's real transcripts from this plan run, trimmed to the lines the capture reads:
  every assistant line keeps its real `message.id`, `model`, `usage` counts and
  `timestamp`, and drops its content; the `cost-state` lines are verbatim. `subagent-run/`
  is the real `Agent`-tool session of verified fact 3, trimmed the same way.
  `archon-cost-sdk-model-usage.json` is an `archon workflow cost --json` node in the Claude
  Agent SDK's `ModelUsage` shape (`sdk.d.ts`: `inputTokens`, `outputTokens`,
  `cacheReadInputTokens`, `cacheCreationInputTokens`, `webSearchRequests`, `costUSD`,
  `contextWindow`, `maxOutputTokens`) with two models. Archon never fills it today (spec
  fact 1), so it cannot be captured. Its `claude-sonnet-5` figures are the spec's fact 5
  example; the `claude-opus-5-5` figures are built so the two `costUSD` values sum to the
  node's `costUsd`.

```bash
python3 - <<'PYEOF'
import json
import os

BASE = "tests/fixtures/session_usage"


def w(rel, objs):
    path = os.path.join(BASE, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write("".join(json.dumps(o) + "\n" for o in objs))


def a(ts, sid, mid, model, u, side=False, agent=None):
    o = {"type": "assistant", "timestamp": ts, "isSidechain": side, "version": "2.1.282",
         "sessionId": sid}
    if agent:
        o["agentId"] = agent
    o["message"] = {"id": mid, "model": model, "usage": dict(zip(
        ("input_tokens", "output_tokens", "cache_read_input_tokens",
         "cache_creation_input_tokens"), u))}
    return o


def user(ts, sid, text, side=False, agent=None):
    o = {"type": "user", "timestamp": ts, "isSidechain": side, "version": "2.1.282",
         "sessionId": sid}
    if agent:
        o["agentId"] = agent
    o["message"] = {"role": "user", "content": [{"type": "text", "text": text}]}
    return o


HAIKU, SONNET, OPUS = "claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-opus-5-5"
P, Q = "7dc707d7-072c-4bc3-876f-926114d03d0d", "976e53e1-047b-420a-bd97-12e5fbfa8714"
w(f"plan-run/-workspace-dark-factory/{P}.jsonl", [
    {"type": "queue-operation", "operation": "enqueue", "timestamp": "2026-11-27T20:13:14.527Z", "sessionId": P},
    user("2026-11-27T20:13:14.557Z", P, "Parse this command and extract two things:\n1. The GitHub iss"),
    a("2026-11-27T20:13:15.844Z", P, "msg_011ChU3WMBek7fxqcv4cHoFS", HAIKU, (10, 135, 6415, 2578)),
    a("2026-11-27T20:13:15.909Z", P, "msg_011ChU3WMBek7fxqcv4cHoFS", HAIKU, (10, 135, 6415, 2578)),
    a("2026-11-27T20:13:17.018Z", P, "msg_011ChU3WSmkqbgGY81i1UAqQ", HAIKU, (10, 159, 8993, 172)),
    a("2026-11-27T20:13:17.176Z", P, "msg_011ChU3WSmkqbgGY81i1UAqQ", HAIKU, (10, 159, 8993, 172)),
    {"type": "cost-state", "sessionId": P, "totalCostUSD": 0.0096158, "totalAPIDuration": 3293,
     "totalAPIDurationWithoutRetries": 3276, "totalToolDuration": 1, "totalLinesAdded": 0,
     "totalLinesRemoved": 0, "totalDuration": 3506, "startTime": 1795810393702,
     "modelUsage": {HAIKU: {"inputTokens": 1040, "outputTokens": 307, "thinkingTokens": 193,
                            "cacheReadInputTokens": 15408, "cacheCreationInputTokens": 2750,
                            "webSearchRequests": 0, "costUSD": 0.0096158}},
     "hasUnknownModelCost": False},
])
w(f"plan-run/-workspace-dark-factory/{Q}.jsonl", [
    {"type": "queue-operation", "operation": "enqueue", "timestamp": "2026-11-27T20:13:14.620Z", "sessionId": Q},
    user("2026-11-27T20:13:14.661Z", Q, "Generate a concise conversation title (3-6 words) for this u"),
    a("2026-11-27T20:13:16.741Z", Q, "msg_011ChU3WMcT6aa69NzPABjEJ", SONNET, (2, 10, 8421, 2864)),
    {"type": "cost-state", "sessionId": Q, "totalCostUSD": 0.0142752, "totalAPIDuration": 2827,
     "totalAPIDurationWithoutRetries": 2811, "totalToolDuration": 0, "totalLinesAdded": 0,
     "totalLinesRemoved": 0, "totalDuration": 3096, "startTime": 1795810393691,
     "modelUsage": {HAIKU: {"inputTokens": 966, "outputTokens": 13, "thinkingTokens": 0,
                            "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
                            "webSearchRequests": 0, "costUSD": 0.001031},
                    SONNET: {"inputTokens": 2, "outputTokens": 10, "thinkingTokens": 0,
                             "cacheReadInputTokens": 8421, "cacheCreationInputTokens": 2864,
                             "webSearchRequests": 0, "costUSD": 0.0132442}},
     "hasUnknownModelCost": False},
])
S, AG = "a436f69c-9e9a-415f-bd4a-6a0661723229", "a64d56181415ae97a"
w(f"subagent-run/-workspace-dark-factory/{S}.jsonl", [
    user("2026-11-27T20:13:19.990Z", S, "scrubbed"),
    a("2026-11-27T20:24:14.009Z", S, "msg_011ChU4KKhRJVRYXrHWmXCyh", OPUS, (2, 2268, 178709, 8881)),
    a("2026-11-27T20:24:26.278Z", S, "msg_011ChU4LxfgNf67m1XuHBNdA", OPUS, (2, 1312, 187590, 4411)),
    a("2026-11-27T20:24:26.281Z", S, "msg_011ChU4LxfgNf67m1XuHBNdA", OPUS, (2, 1312, 187590, 4411)),
    a("2026-11-27T20:24:27.227Z", S, "msg_011ChU4LxfgNf67m1XuHBNdA", OPUS, (2, 1312, 187590, 4411)),
    a("2026-11-27T20:24:33.704Z", S, "msg_011ChU4N1UsfTqaudYg4z7gc", OPUS, (2, 467, 192001, 1575)),
])
w(f"subagent-run/-workspace-dark-factory/{S}/subagents/agent-{AG}.jsonl", [
    user("2026-11-27T20:24:27.232Z", S, "Reply with exactly the single word: ok. Do not use any tools.", side=True, agent=AG),
    a("2026-11-27T20:24:28.159Z", S, "msg_011ChU4MwR4HgirzLcJLt7LM", HAIKU, (10, 46, 0, 13243), side=True, agent=AG),
    a("2026-11-27T20:24:28.160Z", S, "msg_011ChU4MwR4HgirzLcJLt7LM", HAIKU, (10, 46, 0, 13243), side=True, agent=AG),
])
with open(os.path.join(BASE, f"subagent-run/-workspace-dark-factory/{S}/subagents/agent-{AG}.meta.json"), "w") as fh:
    fh.write('{"agentType":"general-purpose","description":"Trivial probe subagent","spawnDepth":1,'
             '"requestShape":"foreground","requestNonInteractive":true,"model":"haiku"}\n')
with open(os.path.join(BASE, "archon-cost-sdk-model-usage.json"), "w") as fh:
    json.dump({"run_id": "wf-sdk", "nodes": [{
        "nodeId": "conformance", "inputTokens": 90, "outputTokens": 22298,
        "costUsd": 2.5850786, "durationMs": 300000,
        "modelUsage": {
            SONNET: {"inputTokens": 90, "outputTokens": 22298, "cacheReadInputTokens": 4283508,
                     "cacheCreationInputTokens": 121337, "webSearchRequests": 0,
                     "costUSD": 1.5652096, "contextWindow": 200000, "maxOutputTokens": 64000},
            OPUS: {"inputTokens": 12, "outputTokens": 4100, "cacheReadInputTokens": 610000,
                   "cacheCreationInputTokens": 30500, "webSearchRequests": 0,
                   "costUSD": 1.019869, "contextWindow": 200000, "maxOutputTokens": 64000}}}]},
        fh, indent=2)
    fh.write("\n")
PYEOF
find tests/fixtures/session_usage -type f | sort
```

  Expected output:

```
tests/fixtures/session_usage/archon-cost-sdk-model-usage.json
tests/fixtures/session_usage/archon-log-plan-run.txt
tests/fixtures/session_usage/plan-run/-workspace-dark-factory/7dc707d7-072c-4bc3-876f-926114d03d0d.jsonl
tests/fixtures/session_usage/plan-run/-workspace-dark-factory/976e53e1-047b-420a-bd97-12e5fbfa8714.jsonl
tests/fixtures/session_usage/subagent-run/-workspace-dark-factory/a436f69c-9e9a-415f-bd4a-6a0661723229.jsonl
tests/fixtures/session_usage/subagent-run/-workspace-dark-factory/a436f69c-9e9a-415f-bd4a-6a0661723229/subagents/agent-a64d56181415ae97a.jsonl
tests/fixtures/session_usage/subagent-run/-workspace-dark-factory/a436f69c-9e9a-415f-bd4a-6a0661723229/subagents/agent-a64d56181415ae97a.meta.json
```

- [ ] **Step 1.3: Commit.**

```bash
git add tests/fixtures/session_usage
git commit -m "test(#645): scrubbed Archon log, transcript and SDK modelUsage fixtures"
```

---

## Task 2: `session_usage.py` — windows, messages, chains, attribution, `cost-state`

**Files:** create `tests/test_session_usage.py`, `scripts/factory_core/session_usage.py`.

Covers spec requirements 2, 3, 4 (the per-node part), 5 (`cost_usd_session_delta`), 8
(matching) and the requirement 9 rows: one count per message, resumed chain,
message-time attribution, ambiguous window, title session, `cost-state` primary/absent,
reconciliation, node windows, hostile transcript.

- [ ] **Step 2.1: Write the failing tests.** Create `tests/test_session_usage.py`:

```python
"""factory_core.session_usage (#645): node windows from Archon's log, per-node usage from
Claude Code session transcripts, cost-state figures and reconciliation."""
import json
import os
import signal
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core import session_usage as su

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "session_usage"
T0 = 1_795_806_798_000
OPUS, HAIKU, SONNET = "claude-opus-5-5", "claude-haiku-4-5-20251001", "claude-sonnet-5"


def _a(mid, ms, model=OPUS, tin=10, tout=100, cr=0, cc=0, side=False):
    return {"type": "assistant", "timestamp": su.ms_to_iso(ms), "isSidechain": side,
            "version": "2.1.282",
            "message": {"id": mid, "model": model,
                        "usage": {"input_tokens": tin, "output_tokens": tout,
                                  "cache_read_input_tokens": cr,
                                  "cache_creation_input_tokens": cc}}}


def _u(text, ms):
    return {"type": "user", "timestamp": su.ms_to_iso(ms), "isSidechain": False,
            "message": {"role": "user", "content": text}}


def _mu(tin, tout, cr, cc, cost):
    return {"inputTokens": tin, "outputTokens": tout, "cacheReadInputTokens": cr,
            "cacheCreationInputTokens": cc, "costUSD": cost}


def _cs(start, total, usage):
    return {"type": "cost-state", "startTime": start, "totalCostUSD": total, "modelUsage": usage}


def _write(root, rel, lines):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join((l if isinstance(l, str) else json.dumps(l)) + "\n" for l in lines))
    return path


def _load(root):
    return su.load_transcripts(root, started_at=None)


# --- requirement 2: node windows -------------------------------------------------------

_REAL_LOG = FIXTURES / "archon-log-plan-run.txt"
_REAL_NODES = ("parse-intent", "fetch-issue", "digest-comments", "setup-refine-branch",
               "bench-mode-probe", "plan")


def _log(**kw):
    line = {"level": 30, "pid": 1, "hostname": "scrubbed", "module": su.LOG_MODULE}
    line.update(kw)
    return json.dumps(line)


def test_windows_from_real_log_cut():
    w = su.read_node_windows(_REAL_LOG, _REAL_NODES)
    assert w["parse-intent"] == (1795810393681, 1795810397203)   # model node
    assert w["fetch-issue"] == (1795810397206, 1795810398811)    # bash node
    assert w["setup-refine-branch"] == (1795810398816, 1795810399228)
    assert w["bench-mode-probe"] == (1795810398816, 1795810398833)
    assert "digest-comments" not in w                             # skipped: no started line
    # plan started and the cut has no later line and no finished line: upper bound.
    assert w["plan"] == (1795810399585, 1795810399585)


def test_windows_ignore_bad_lines_and_end_failed_node(tmp_path):
    hostile = [
        _log(time=1795810393000, nodeId="fetch-issue", msg="dag_node_started"),      # before run
        _log(time=1795810393690, nodeId="parse-intent", msg="dag_node_started",
             module="workflow.other"),                                                 # wrong module
        _log(time="1795810393690", nodeId="parse-intent", msg="dag_node_started"),     # str time
        _log(time=1795810393690.5, nodeId="parse-intent", msg="dag_node_started"),     # float time
    ]
    tail = [
        _log(time=1795810396000, nodeId="parse-intent", msg="dag_node_started"),       # 2nd start
        _log(time=1795810399590, nodeId="not-a-node", msg="dag_node_started"),         # unknown id
        _log(time=1795810399590, nodeId=["plan"], msg="dag_node_started"),             # non-str id
        # Shape of Archon 0.3.10 dag-executor.ts's bash-node failure log line.
        _log(level=50, time=1795810400100, nodeId="plan", nodeType="bash", isTimeout=False,
             msg="dag_node_failed"),
        _log(time=1795810400500, nodeCount=49, anyCompleted=True, anyFailed=True,
             msg="dag_workflow_finished"),
        _log(time=1795810400900, nodeId="fetch-issue", msg="dag_node_completed"),      # after run
    ]
    real = _REAL_LOG.read_text().splitlines()
    log = tmp_path / "out.log"
    log.write_text("\n".join(hostile + real + tail) + "\n")
    w = su.read_node_windows(log, _REAL_NODES)
    assert w["parse-intent"] == (1795810393681, 1795810397203)
    assert w["fetch-issue"] == (1795810397206, 1795810398811)
    assert w["plan"] == (1795810399585, 1795810400100)
    assert "not-a-node" not in w


def test_window_of_unfinished_node_ends_at_next_accepted_line_or_finished(tmp_path):
    log = tmp_path / "out.log"
    log.write_text("\n".join([
        _log(time=T0, msg="dag_workflow_starting"),
        _log(time=T0 + 10, nodeId="a", msg="dag_node_started"),
        _log(time=T0 + 20, nodeId="b", msg="dag_node_started"),
        _log(time=T0 + 30, nodeId="b", msg="dag_node_completed"),
        _log(time=T0 + 40, nodeId="c", msg="dag_node_started"),
        _log(time=T0 + 90, msg="dag_workflow_finished"),
    ]) + "\n")
    w = su.read_node_windows(log, ["a", "b", "c"])
    assert w["a"] == (T0 + 10, T0 + 20)
    assert w["c"] == (T0 + 40, T0 + 90)


def test_windows_absent_log(tmp_path):
    assert su.read_node_windows(None, ["a"]) == {}
    assert su.read_node_windows(tmp_path / "missing.log", ["a"]) == {}
    assert su.read_node_windows(tmp_path, ["a"]) == {}


# --- requirement 3: messages, chains, attribution --------------------------------------

def test_three_block_message_counted_once(tmp_path):
    line = _a("msg_1", T0 + 10, tin=3, tout=7, cr=100, cc=20)
    _write(tmp_path, "proj/s1.jsonl", [_u("hi", T0), line, line, line])
    t = _load(tmp_path)
    assert list(t["messages"]) == ["msg_1"]
    res = su.attribute(t, {"implement": (T0, T0 + 100)}, {"implement": (3, 7)}, {})
    node = res["nodes"]["implement"]
    assert node["usage_source"] == "transcript_messages"
    assert node["models"] == [{"model": OPUS, "input_tokens": 3, "output_tokens": 7,
                               "cache_read_input_tokens": 100,
                               "cache_creation_input_tokens": 20}]
    assert node["usage_reconciled"] is True
    assert res["unattributed_sessions"] == []


_WINDOWS = {"conformance": (T0 + 1000, T0 + 1999), "code-review": (T0 + 2000, T0 + 2999),
            "revise-advisory": (T0 + 4000, T0 + 4999)}
_TOKENS = {n: (20, 200) for n in _WINDOWS}


def _resumed_chain(root, *, cost_states=True, last_cost_state=False):
    msgs = [_a(f"c{i}", T0 + ms, cr=500, cc=25) for i, ms in
            enumerate((1100, 1200, 2100, 2200, 4100, 4200), start=1)]
    cs_a = _cs(T0 + 1000, 1.0, {OPUS: _mu(20, 200, 1000, 50, 0.9), HAIKU: _mu(5, 5, 0, 0, 0.1)})
    cs_b = _cs(T0 + 1000, 2.1, {OPUS: _mu(40, 400, 3000, 80, 2.0), HAIKU: _mu(5, 5, 0, 0, 0.1)})
    cs_c = _cs(T0 + 1000, 3.1, {OPUS: _mu(60, 600, 4000, 130, 3.0), HAIKU: _mu(5, 5, 0, 0, 0.1)})
    _write(root, "p/a.jsonl", [_u("conformance prompt", T0 + 1001)] + msgs[:2]
           + ([cs_a] if cost_states else []))
    _write(root, "p/b.jsonl", msgs[:4] + ([cs_b] if cost_states else []))
    _write(root, "p/c.jsonl", msgs[:6] + ([cs_c] if last_cost_state else []))


def test_resumed_chain_each_node_gets_only_its_own_messages(tmp_path):
    _resumed_chain(tmp_path, cost_states=False)
    res = su.attribute(_load(tmp_path), _WINDOWS, _TOKENS, {})
    for node in _WINDOWS:
        fig = res["nodes"][node]
        assert fig["usage_source"] == "transcript_messages"
        assert fig["models"] == [{"model": OPUS, "input_tokens": 20, "output_tokens": 200,
                                  "cache_read_input_tokens": 1000,
                                  "cache_creation_input_tokens": 50}]
        assert fig["usage_reconciled"] is True
    total_out = sum(res["nodes"][n]["models"][0]["output_tokens"] for n in _WINDOWS)
    assert total_out == 6 * 100   # the distinct-message sum, not the 2+4+6 line sum
    assert res["unattributed_sessions"] == []


def test_cost_state_primary_first_resumed_and_absent(tmp_path):
    _resumed_chain(tmp_path)
    costs = {"conformance": 1.0, "code-review": 2.1, "revise-advisory": 3.1}
    res = su.attribute(_load(tmp_path), _WINDOWS, _TOKENS, costs)
    first = res["nodes"]["conformance"]
    assert first["usage_source"] == "cli_cost_state"
    assert first["first_of_chain"] is True
    assert first["models"] == [   # haiku appears only in cost-state, not in any message
        {"model": HAIKU, "input_tokens": 5, "output_tokens": 5, "cache_read_input_tokens": 0,
         "cache_creation_input_tokens": 0, "cost_usd": 0.1},
        {"model": OPUS, "input_tokens": 20, "output_tokens": 200,
         "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 50, "cost_usd": 0.9},
    ]
    assert first["cost_usd_session_delta"] == pytest.approx(1.0)
    assert first["usage_reconciled"] is True
    resumed = res["nodes"]["code-review"]
    assert resumed["usage_source"] == "cli_cost_state"
    assert resumed["first_of_chain"] is False
    assert resumed["models"] == [   # haiku's unchanged figures drop out of the difference
        {"model": OPUS, "input_tokens": 20, "output_tokens": 200,
         "cache_read_input_tokens": 2000, "cache_creation_input_tokens": 30,
         "cost_usd": pytest.approx(1.1)},
    ]
    assert resumed["cost_usd_session_delta"] == pytest.approx(1.1)   # Archon says 2.1
    assert resumed["usage_reconciled"] is True
    killed = res["nodes"]["revise-advisory"]   # last file has no cost-state line
    assert killed["usage_source"] == "transcript_messages"
    assert killed["first_of_chain"] is False
    assert "cost_usd_session_delta" not in killed
    assert all("cost_usd" not in m for m in killed["models"])


def test_cost_state_unavailable_when_previous_file_lacks_it_or_differs(tmp_path):
    _resumed_chain(tmp_path, cost_states=False, last_cost_state=True)
    res = su.attribute(_load(tmp_path), _WINDOWS, _TOKENS, {})
    assert res["nodes"]["revise-advisory"]["usage_source"] == "transcript_messages"


def test_reconciliation_flags_token_and_cost_mismatch(tmp_path):
    _resumed_chain(tmp_path)
    tokens = dict(_TOKENS, conformance=(21, 200))
    res = su.attribute(_load(tmp_path), _WINDOWS, tokens,
                       {"conformance": 1.0, "code-review": 2.1})
    assert res["nodes"]["conformance"]["usage_source"] == "cli_cost_state"   # sole chain kept
    assert res["nodes"]["conformance"]["usage_reconciled"] is False          # token mismatch
    res = su.attribute(_load(tmp_path), _WINDOWS, _TOKENS, {"conformance": 0.5})
    assert res["nodes"]["conformance"]["usage_reconciled"] is False          # cost mismatch
    assert res["nodes"]["code-review"]["usage_reconciled"] is True           # delta, not cost


def test_message_outside_every_window_or_in_two_is_unattributed(tmp_path):
    _write(tmp_path, "p/x.jsonl", [_u("x", T0 + 690), _a("x1", T0 + 700)])
    _write(tmp_path, "p/y.jsonl", [_u("y", T0 + 4990), _a("y1", T0 + 5000)])
    windows = {"a": (T0, T0 + 1000), "b": (T0 + 500, T0 + 1500)}
    res = su.attribute(_load(tmp_path), windows, {"a": (10, 100), "b": (10, 100)}, {})
    assert res["nodes"] == {}
    entries = {e["first_prompt"]: e for e in res["unattributed_sessions"]}
    assert set(entries) == {"x", "y"}
    assert entries["x"]["window_node"] is None   # held equally by a and b
    assert entries["y"]["window_node"] is None   # held by no window


@pytest.mark.parametrize("tokens", [(99, 99), (10, 100)], ids=["neither", "both"])
def test_ambiguous_window_gives_no_chain(tmp_path, tokens):
    _write(tmp_path, "p/one.jsonl", [_u("one", T0 + 5), _a("o1", T0 + 10)])
    _write(tmp_path, "p/two.jsonl", [_u("two", T0 + 15), _a("t1", T0 + 20)])
    res = su.attribute(_load(tmp_path), {"implement": (T0, T0 + 100)},
                       {"implement": tokens}, {})
    assert "implement" not in res["nodes"]
    assert sorted(e["first_prompt"] for e in res["unattributed_sessions"]) == ["one", "two"]
    assert all(e["window_node"] == "implement" for e in res["unattributed_sessions"])


def test_real_title_session_is_unattributed_and_added_to_no_node():
    t = _load(FIXTURES / "plan-run")
    windows = {"parse-intent": (1795810393681, 1795810397203)}
    res = su.attribute(t, windows, {"parse-intent": (20, 294)}, {"parse-intent": 0.0096158})
    fig = res["nodes"]["parse-intent"]
    assert fig["usage_source"] == "cli_cost_state"
    assert fig["models"] == [{"model": HAIKU, "input_tokens": 1040, "output_tokens": 307,
                              "cache_read_input_tokens": 15408,
                              "cache_creation_input_tokens": 2750, "thinking_tokens": 193,
                              "web_search_requests": 0, "cost_usd": 0.0096158}]
    assert fig["usage_reconciled"] is True
    assert res["unattributed_sessions"] == [{
        "first_prompt": "Generate a concise conversation title (3-6 words) for this u",
        "started_at": "2026-11-27T20:13:14.620Z",
        "models": [{"model": SONNET, "input_tokens": 2, "output_tokens": 10,
                    "cache_read_input_tokens": 8421, "cache_creation_input_tokens": 2864}],
        "cost_usd": 0.0142752,
        "window_node": "parse-intent",
    }]
    assert t["harness_version"] == "2.1.282"


def test_real_multi_model_cost_state_payload():
    # The title session's real cost-state line carries two models, one of which
    # (haiku) appears in none of its assistant lines.
    t = _load(FIXTURES / "plan-run")
    windows = {"title": (1795810393681, 1795810397203)}
    res = su.attribute(t, windows, {"title": (2, 10)}, {"title": 0.0142752})
    # Two chains in the window: only the title chain matches (2, 10).
    fig = res["nodes"]["title"]
    assert [m["model"] for m in fig["models"]] == [HAIKU, SONNET]
    assert fig["models"][1]["cache_creation_input_tokens"] == 2864
    assert fig["cost_usd_session_delta"] == pytest.approx(0.0142752)
    assert fig["usage_reconciled"] is True


def test_real_subagent_transcript_joins_its_node_by_time():
    t = _load(FIXTURES / "subagent-run")
    assert len(t["files"]) == 2   # the .meta.json is not a transcript
    start = su.iso_to_ms("2026-11-27T20:24:00.000Z")
    windows = {"plan": (start, start + 60_000)}
    res = su.attribute(t, windows, {"plan": (6, 4047)}, {})
    fig = res["nodes"]["plan"]
    assert fig["usage_source"] == "transcript_messages"   # parent session still running
    assert fig["usage_reconciled"] is True                # sub-agent not in the token match
    assert fig["models"] == [
        {"model": HAIKU, "input_tokens": 10, "output_tokens": 46,
         "cache_read_input_tokens": 0, "cache_creation_input_tokens": 13243},
        {"model": OPUS, "input_tokens": 6, "output_tokens": 4047,
         "cache_read_input_tokens": 558300, "cache_creation_input_tokens": 14867},
    ]
    assert res["unattributed_sessions"] == []


def test_subagent_chain_outside_every_window_is_listed():
    t = _load(FIXTURES / "subagent-run")
    res = su.attribute(t, {}, {}, {})
    prompts = sorted(e["first_prompt"] for e in res["unattributed_sessions"])
    assert prompts == ["Reply with exactly the single word: ok. Do not use any tools",
                       "scrubbed"]


def test_mtime_floor_and_unparseable_started_at(tmp_path):
    old = _write(tmp_path, "p/old.jsonl", [_a("o1", T0)])
    _write(tmp_path, "p/new.jsonl", [_a("n1", T0)])
    os.utime(old, (1_000_000_000, 1_000_000_000))
    assert set(su.load_transcripts(tmp_path, "2020-01-01T00:00:00Z")["files"]) == {"p/new.jsonl"}
    assert su.load_transcripts(tmp_path, "not-a-time")["files"] == {}
    assert su.load_transcripts(tmp_path / "missing", None)["files"] == {}


def test_hostile_transcripts_never_raise_or_hang(tmp_path, monkeypatch):
    monkeypatch.setattr(su, "MAX_LINE_BYTES", 1024)
    _write(tmp_path, "p/bad.jsonl", [
        "not json", "[1, 2]", "\"a string\"", "{\"type\": \"assistant\", \"message\": 3}",
        json.dumps(_a("big", T0, tin=1)) + " " * 5000,
        json.dumps(_a("ok", T0 + 1, tin=2, tout=3)),
        "[" * 100_000,
    ])
    (tmp_path / "p" / "dir.jsonl").mkdir()
    os.mkfifo(tmp_path / "p" / "fifo.jsonl")
    (tmp_path / "p" / "link.jsonl").symlink_to(tmp_path / "p" / "fifo.jsonl")
    previous = signal.signal(signal.SIGALRM,
                             lambda *_: (_ for _ in ()).throw(TimeoutError()))
    signal.alarm(10)
    try:
        t = _load(tmp_path)
        res = su.attribute(t, {"n": (T0 - 1, T0 + 10)}, {"n": (2, 3)}, {})
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    assert list(t["messages"]) == ["ok"]
    assert res["nodes"]["n"]["usage_reconciled"] is True


def test_synthetic_messages_are_not_a_model(tmp_path):
    _write(tmp_path, "p/s.jsonl", [_a("s1", T0, model=su.SYNTHETIC_MODEL, tin=0, tout=0),
                                   _a("s2", T0 + 1)])
    assert list(_load(tmp_path)["messages"]) == ["s2"]


# --- requirement 8: backfill matching ---------------------------------------------------

def test_backfill_matches_file_by_cost(tmp_path):
    _resumed_chain(tmp_path)
    t = su.load_transcripts(tmp_path)
    res = su.backfill(t, {
        "conformance": {"cost_usd": 1.0, "tokens": (20, 200)},
        "code-review": {"cost_usd": 2.1, "tokens": (20, 200)},
        "revise-advisory": {"cost_usd": 3.1, "tokens": (20, 200)},   # no file costs 3.1
        "fetch-issue": {"cost_usd": 0, "tokens": (0, 0)},
    })
    assert set(res) == {"conformance", "code-review"}
    assert res["code-review"]["cost_usd_session_delta"] == pytest.approx(1.1)
    assert res["code-review"]["usage_reconciled"] is True
```

- [ ] **Step 2.2: Run them; they fail.**

```bash
PYTHONPATH=scripts $PY -m pytest tests/test_session_usage.py -q 2>&1 | tail -3
```

  Expected: a collection error, `ImportError: cannot import name 'session_usage'`.

- [ ] **Step 2.3: Implement.** Create `scripts/factory_core/session_usage.py`:

```python
"""Per-node model and token usage from Claude Code session transcripts (#645).

Archon relays no ``modelUsage`` (``archon workflow cost --json`` reports null on every
node), so the run record reads the Claude Code CLI's own session transcripts under
``$HOME/.claude/projects/``:

* one record per ``message.id`` across the whole run: a message is one transcript line
  per content block, each repeating the message's whole usage, and a resumed session
  copies its earlier messages into a new file;
* files that share a ``message.id`` are one session chain;
* a message belongs to the node whose Archon-log window holds its own timestamp;
* a node's figures prefer the CLI's closing ``cost-state`` line (sub-agent and
  small-model spend included), differenced against the chain's previous file.

``cost-state`` and the transcript layout (a sub-agent writes
``<session-id>/subagents/agent-<id>.jsonl``, every line ``isSidechain: true``) are
internals of the pinned CLI (2.1.282), not a documented interface: when neither shape is
found nothing is guessed. Pure functions over files: never imports run_record, never
raises on malformed input, and never opens anything but a regular file.
"""

import json
import os
import pathlib
import stat
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone

LOG_MODULE = "workflow.dag-executor"
_STARTED, _COMPLETED = "dag_node_started", "dag_node_completed"
# Archon 0.3.10 (packages/workflows/src/dag-executor.ts) logs this, with nodeId, when an
# AI, bash or script node fails; when present it ends the node's window (requirement 2).
_FAILED = "dag_node_failed"
MAX_LINE_BYTES = 8 * 1024 * 1024
COST_EPSILON = 0.000001
ARCHON_COST_EPSILON = 0.01
FIRST_PROMPT_CHARS = 60
# The CLI writes this for its own placeholder messages (e.g. an API error); not a model.
SYNTHETIC_MODEL = "<synthetic>"
TOKEN_KEYS = (
    "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
)
# cost-state modelUsage key -> models-entry key. The four token keys are always written;
# the others only when the cost-state line carries them.
COST_STATE_KEYS = (
    ("inputTokens", "input_tokens"),
    ("outputTokens", "output_tokens"),
    ("cacheReadInputTokens", "cache_read_input_tokens"),
    ("cacheCreationInputTokens", "cache_creation_input_tokens"),
    ("thinkingTokens", "thinking_tokens"),
    ("webSearchRequests", "web_search_requests"),
    ("costUSD", "cost_usd"),
)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def iso_to_ms(value) -> "int | None":
    """Epoch milliseconds of an ISO-8601 timestamp (a trailing Z allowed), else None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - _EPOCH) // timedelta(milliseconds=1)


def ms_to_iso(ms: int) -> str:
    return (_EPOCH + timedelta(milliseconds=ms)).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z")


def _regular_file(path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _iter_lines(path):
    """Each line of a regular file as bytes; a line longer than MAX_LINE_BYTES is skipped
    whole without ever being held in memory."""
    cap = MAX_LINE_BYTES
    with open(path, "rb") as fh:
        while True:
            line = fh.readline(cap + 1)
            if not line:
                return
            if len(line) > cap and not line.endswith(b"\n"):
                while line and not line.endswith(b"\n"):
                    line = fh.readline(cap + 1)
                continue
            yield line


def _json_objects(path):
    """The JSON objects of a file, one per line; any other line is skipped."""
    for raw in _iter_lines(path):
        try:
            obj = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        if isinstance(obj, dict):
            yield obj


def read_node_windows(log_file, node_ids) -> dict:
    """node_id -> (start_ms, end_ms) from Archon's JSON log lines (requirement 2).

    A line counts only with module == LOG_MODULE, an integer time inside
    [dag_workflow_starting, dag_workflow_finished] (no finished line: the largest time of
    that module), a nodeId among node_ids, and msg exactly dag_node_started or
    dag_node_completed. The first started and first completed line of a node win. A node
    that started and never completed ends at its first dag_node_failed line, else at the
    next accepted line of any node, else at the upper bound. {} when the file is absent,
    not a regular file or unreadable. _parse_node_log's text-line parsing is separate.
    """
    if not log_file or not _regular_file(log_file):
        return {}
    known = {n for n in node_ids if isinstance(n, str)}
    try:
        module = [o for o in _json_objects(log_file)
                  if o.get("module") == LOG_MODULE and _is_int(o.get("time"))]
    except OSError:
        return {}
    lower = next((o["time"] for o in module if o.get("msg") == "dag_workflow_starting"), None)
    upper = next((o["time"] for o in module if o.get("msg") == "dag_workflow_finished"), None)
    if lower is not None:
        module = [o for o in module if o["time"] >= lower]
    if upper is not None:
        module = [o for o in module if o["time"] <= upper]
    else:
        upper = max((o["time"] for o in module), default=None)
    events = [o for o in module if isinstance(o.get("nodeId"), str) and o["nodeId"] in known]
    accepted = [o for o in events if o.get("msg") in (_STARTED, _COMPLETED)]
    first: dict = {}
    for i, o in enumerate(accepted):
        first.setdefault((o["nodeId"], o["msg"]), (i, o["time"]))
    failed: dict = {}
    for o in events:
        if o.get("msg") == _FAILED:
            failed.setdefault(o["nodeId"], o["time"])
    windows = {}
    for (node, msg), (i, start) in first.items():
        if msg != _STARTED:
            continue
        end = first.get((node, _COMPLETED), (None, None))[1]
        if end is None:
            end = failed.get(node)
        if end is None:
            end = accepted[i + 1]["time"] if i + 1 < len(accepted) else upper
        if end is not None and end >= start:
            windows[node] = (start, end)
    return windows


def _transcript_files(root: pathlib.Path, started_at) -> list:
    """Regular *.jsonl files under root, recursively, modified at or after started_at
    (None: no time floor; unparseable: nothing). Symlinks, FIFOs and directories are
    never opened."""
    if not root.is_dir():
        return []
    floor = None
    if started_at is not None:
        floor_ms = iso_to_ms(started_at)
        if floor_ms is None:
            return []
        floor = floor_ms / 1000
    out = []
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            if not name.endswith(".jsonl"):
                continue
            path = pathlib.Path(dirpath) / name
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or (floor is not None and st.st_mtime < floor):
                continue
            out.append(path)
    return sorted(out)


def _user_text(obj: dict) -> "str | None":
    msg = obj.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if (isinstance(block, dict) and block.get("type") == "text"
                    and isinstance(block.get("text"), str)):
                return block["text"]
    return None


def _clean_prompt(text: str) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cc")[:FIRST_PROMPT_CHARS]


def _message_usage(usage) -> dict:
    usage = usage if isinstance(usage, dict) else {}
    return {k: usage[k] if _is_int(usage.get(k)) and usage[k] >= 0 else 0 for k in TOKEN_KEYS}


def load_transcripts(root, started_at=None) -> dict:
    """Read every transcript of requirement 3's input set under root.

    Returns {"files": {rel: info}, "messages": {message_id: record}, "harness_version"}.
    A message is kept once per message.id across all files (its earliest timestamp, its
    model, its usage, the files it appears in); a line with no message.id is its own
    record. harness_version is the most common CLI `version` on assistant lines, or None.
    """
    root = pathlib.Path(root)
    files: dict = {}
    messages: dict = {}
    versions: Counter = Counter()
    for path in _transcript_files(root, started_at):
        rel = path.relative_to(root).as_posix()
        info = {"ids": [], "cost_state": None, "first_ts": None, "first_user": None,
                "assistant_lines": 0, "sidechain_lines": 0}
        seen: set = set()
        try:
            for lineno, obj in enumerate(_json_objects(path)):
                ts = iso_to_ms(obj.get("timestamp"))
                if ts is not None and (info["first_ts"] is None or ts < info["first_ts"]):
                    info["first_ts"] = ts
                kind = obj.get("type")
                if kind == "cost-state":
                    info["cost_state"] = obj
                    continue
                if kind == "user":
                    if info["first_user"] is None:
                        text = _user_text(obj)
                        if text:
                            info["first_user"] = (ts, text)
                    continue
                msg = obj.get("message") if kind == "assistant" else None
                if not isinstance(msg, dict) or msg.get("model") == SYNTHETIC_MODEL:
                    continue
                if isinstance(obj.get("version"), str):
                    versions[obj["version"]] += 1
                side = obj.get("isSidechain") is True
                info["assistant_lines"] += 1
                info["sidechain_lines"] += side
                mid = msg.get("id")
                if not isinstance(mid, str) or not mid:
                    mid = f"\x00{rel}#{lineno}"
                rec = messages.get(mid)
                if rec is None:
                    model = msg.get("model")
                    rec = messages[mid] = {
                        "ts": ts, "model": model if isinstance(model, str) else "",
                        "usage": _message_usage(msg.get("usage")), "sidechain": side,
                        "files": set(),
                    }
                elif ts is not None and (rec["ts"] is None or ts < rec["ts"]):
                    rec["ts"] = ts
                rec["files"].add(rel)
                if mid not in seen:
                    seen.add(mid)
                    info["ids"].append(mid)
        except OSError:
            continue
        files[rel] = info
    version = versions.most_common(1)[0][0] if versions else None
    return {"files": files, "messages": messages, "harness_version": version}


def _is_subagent_file(info: dict) -> bool:
    return info["assistant_lines"] > 0 and info["sidechain_lines"] == info["assistant_lines"]


def _chains(files: dict, messages: dict) -> list:
    """Session chains: files sharing any message.id, each chain's files ordered by their
    distinct-message count. A chain is a sub-agent chain when all its files are."""
    parent = {rel: rel for rel in files}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for rec in messages.values():
        members = sorted(rec["files"])
        for other in members[1:]:
            parent[find(other)] = find(members[0])
    groups: dict = {}
    for rel in sorted(files):
        groups.setdefault(find(rel), []).append(rel)
    chains = []
    for members in groups.values():
        ids = {m for rel in members for m in files[rel]["ids"]}
        if not ids:
            continue
        members.sort(key=lambda r: (len(files[r]["ids"]), r))
        chains.append({"files": members, "ids": ids,
                       "subagent": all(_is_subagent_file(files[r]) for r in members)})
    return chains


def _owner(ts, windows: dict):
    """The one node whose window holds ts; None for no window or several."""
    if ts is None:
        return None
    hits = [node for node, (start, end) in windows.items() if start <= ts <= end]
    return hits[0] if len(hits) == 1 else None


def _token_pair(messages: dict, ids) -> tuple:
    main = [m for m in ids if not messages[m]["sidechain"]]
    return (sum(messages[m]["usage"]["input_tokens"] for m in main),
            sum(messages[m]["usage"]["output_tokens"] for m in main))


def _sum_models(messages: dict, ids) -> list:
    acc: dict = {}
    for m in ids:
        rec = messages[m]
        if not rec["model"]:
            continue
        entry = acc.setdefault(rec["model"], {"model": rec["model"], **{k: 0 for k in TOKEN_KEYS}})
        for k in TOKEN_KEYS:
            entry[k] += rec["usage"][k]
    return [acc[k] for k in sorted(acc)]


def _main_files(chain: dict, files: dict) -> list:
    return [r for r in chain["files"] if not _is_subagent_file(files[r])]


def _last_ts(info: dict, messages: dict):
    stamps = [messages[m]["ts"] for m in info["ids"] if messages[m]["ts"] is not None]
    return max(stamps) if stamps else None


def _node_file(chain: dict, files: dict, messages: dict, window: tuple):
    """The chain file whose last message falls in the window, the latest such."""
    best = None
    for rel in _main_files(chain, files):
        last = _last_ts(files[rel], messages)
        if last is not None and window[0] <= last <= window[1] and (best is None or last > best[0]):
            best = (last, rel)
    return best[1] if best else None


def _prev_file(chain: dict, files: dict, rel: str):
    """The chain file with the next-smaller distinct-message count, or None (first file)."""
    count = len(files[rel]["ids"])
    smaller = [r for r in _main_files(chain, files) if len(files[r]["ids"]) < count]
    return max(smaller, key=lambda r: (len(files[r]["ids"]), r)) if smaller else None


def _valid_cost_state(line) -> bool:
    return (isinstance(line, dict) and isinstance(line.get("modelUsage"), dict)
            and _is_num(line.get("totalCostUSD")))


def _cost_state_figures(files: dict, file_rel: str, prev_rel):
    """(models, delta) from the node file's cost-state line minus the previous file's line
    (same startTime) when there is a previous file; None when the source is unavailable:
    no line, a previous file without one or with another startTime, or a negative
    difference in any field."""
    cur = files[file_rel]["cost_state"]
    if not _valid_cost_state(cur):
        return None
    base = None
    if prev_rel is not None:
        base = files[prev_rel]["cost_state"]
        if (not _valid_cost_state(base) or not _is_int(cur.get("startTime"))
                or base.get("startTime") != cur.get("startTime")):
            return None
    base_usage = base["modelUsage"] if base else {}
    models = []
    for model in sorted(set(cur["modelUsage"]) | set(base_usage)):
        if not model:
            continue
        now = cur["modelUsage"].get(model)
        now = now if isinstance(now, dict) else {}
        before = base_usage.get(model)
        before = before if isinstance(before, dict) else {}
        entry = {"model": model}
        for src, dst in COST_STATE_KEYS:
            if dst not in TOKEN_KEYS and src not in now and src not in before:
                continue
            a, b = now.get(src, 0), before.get(src, 0)
            if not _is_num(a) or not _is_num(b):
                return None
            diff = a - b if _is_int(a) and _is_int(b) else round(a - b, 10)
            if diff < 0:
                return None
            entry[dst] = diff
        if any(v for k, v in entry.items() if k != "model"):
            models.append(entry)
    delta = round(cur["totalCostUSD"] - (base["totalCostUSD"] if base else 0), 10)
    if delta < 0 or not models:
        return None
    return models, delta


def _figures(files, messages, file_rel, prev_rel, fallback_ids, matched, node_cost):
    """One node's figures: cli_cost_state, else transcript_messages, else None."""
    fig = _cost_state_figures(files, file_rel, prev_rel) if file_rel else None
    if fig is not None:
        models, delta = fig
        ok = abs(sum(m.get("cost_usd", 0) for m in models) - delta) <= COST_EPSILON
        if prev_rel is None:
            ok = ok and _is_num(node_cost) and abs(delta - node_cost) <= COST_EPSILON
        return {"usage_source": "cli_cost_state", "models": models,
                "cost_usd_session_delta": delta, "usage_reconciled": bool(matched and ok),
                "first_of_chain": prev_rel is None}
    models = _sum_models(messages, fallback_ids)
    if not models:
        return None
    return {"usage_source": "transcript_messages", "models": models,
            "usage_reconciled": bool(matched),
            "first_of_chain": None if file_rel is None else prev_rel is None}


def _session_entry(chain: dict, files: dict, messages: dict, windows: dict) -> dict:
    """An unattributed_sessions entry (requirement 3, rule 5)."""
    rels = chain["files"]
    firsts = [files[r]["first_ts"] for r in rels if files[r]["first_ts"] is not None]
    users = sorted((files[r]["first_user"] for r in rels if files[r]["first_user"]),
                   key=lambda u: (u[0] is None, u[0] or 0))
    last = (_main_files(chain, files) or rels)[-1]
    cost_state = files[last]["cost_state"]
    held: Counter = Counter()
    for m in chain["ids"]:
        ts = messages[m]["ts"]
        for node, (start, end) in windows.items():
            if ts is not None and start <= ts <= end:
                held[node] += 1
    top = held.most_common(2)
    window_node = top[0][0] if top and (len(top) == 1 or top[0][1] > top[1][1]) else None
    return {
        "first_prompt": _clean_prompt(users[0][1]) if users else "",
        "started_at": ms_to_iso(min(firsts)) if firsts else None,
        "models": _sum_models(messages, chain["ids"]),
        "cost_usd": cost_state["totalCostUSD"] if _valid_cost_state(cost_state) else None,
        "window_node": window_node,
    }


def attribute(transcripts: dict, windows: dict, archon_tokens: dict, node_costs: dict) -> dict:
    """Requirement 3 over a load_transcripts() result.

    windows: node_id -> (start_ms, end_ms) for model-calling nodes only. archon_tokens:
    node_id -> (input, output) as Archon reported them (None when unknown). node_costs:
    node_id -> Archon's cost_usd. Returns {"nodes": {node_id: figures},
    "unattributed_sessions": [...]}; a node absent from "nodes" has no transcript figures.
    """
    files, messages = transcripts["files"], transcripts["messages"]
    chains = _chains(files, messages)
    owner = {mid: _owner(rec["ts"], windows) for mid, rec in messages.items()}
    candidates: dict = {}
    for ci, chain in enumerate(chains):
        if chain["subagent"]:
            continue
        for node in sorted({owner[m] for m in chain["ids"]} - {None}):
            candidates.setdefault(node, []).append(ci)
    node_chain: dict = {}
    matched: dict = {}
    for node, cis in candidates.items():
        want = tuple(archon_tokens.get(node) or (None, None))
        hits = [ci for ci in cis
                if _token_pair(messages, [m for m in chains[ci]["ids"] if owner[m] == node]) == want]
        if len(hits) == 1:
            node_chain[node], matched[node] = hits[0], True
        elif len(cis) == 1 and not hits:
            node_chain[node], matched[node] = cis[0], False
    results = {}
    for node, ci in node_chain.items():
        chain = chains[ci]
        own = [m for m in sorted(chain["ids"]) if owner[m] == node]
        sub = [m for c in chains if c["subagent"] for m in sorted(c["ids"]) if owner[m] == node]
        file_rel = _node_file(chain, files, messages, windows[node])
        prev_rel = _prev_file(chain, files, file_rel) if file_rel else None
        fig = _figures(files, messages, file_rel, prev_rel, own + sub, matched[node],
                       node_costs.get(node))
        if fig is not None:
            results[node] = fig
    taken = set(node_chain.values())
    unattributed = [
        _session_entry(chain, files, messages, windows)
        for ci, chain in enumerate(chains)
        if ci not in taken
        and not (chain["subagent"] and any(owner[m] in node_chain for m in chain["ids"]))
    ]
    unattributed.sort(key=lambda e: (e["started_at"] or "", e["first_prompt"]))
    return {"nodes": results, "unattributed_sessions": unattributed}


def backfill(transcripts: dict, nodes: dict) -> dict:
    """Requirement 8: figures for a record whose Archon log is gone.

    nodes: node_id -> {"cost_usd": x, "tokens": (input, output)}. A node's file is the one
    main-thread file whose cost-state totalCostUSD equals its recorded cost_usd within
    COST_EPSILON; its messages are that file's messages not in the chain's previous file.
    A node with no such file, or several, is absent from the result.
    """
    files, messages = transcripts["files"], transcripts["messages"]
    chain_of = {rel: c for c in _chains(files, messages) if not c["subagent"]
                for rel in _main_files(c, files)}
    results = {}
    for node, info in nodes.items():
        cost = info.get("cost_usd")
        if not _is_num(cost) or cost <= 0:
            continue
        hits = [rel for rel in chain_of
                if _valid_cost_state(files[rel]["cost_state"])
                and abs(files[rel]["cost_state"]["totalCostUSD"] - cost) <= COST_EPSILON]
        if len(hits) != 1:
            continue
        rel = hits[0]
        prev = _prev_file(chain_of[rel], files, rel)
        earlier = set(files[prev]["ids"]) if prev else set()
        own = [m for m in files[rel]["ids"] if m not in earlier]
        matched = _token_pair(messages, own) == tuple(info.get("tokens") or (None, None))
        fig = _figures(files, messages, rel, prev, own, matched, cost)
        if fig is not None:
            results[node] = fig
    return results
```

- [ ] **Step 2.4: Run them; they pass.**

```bash
PYTHONPATH=scripts $PY -m pytest tests/test_session_usage.py -q 2>&1 | tail -1
```

  Expected: `20 passed`.

- [ ] **Step 2.5: Commit.**

```bash
git add scripts/factory_core/session_usage.py tests/test_session_usage.py
git commit -m "feat(#645): session_usage — per-node usage from Claude Code transcripts"
```

---

## Task 3: `run_record.py` — Archon fallback fix, new keys, totals, `--transcripts-dir`

**Files:** modify `scripts/factory_core/run_record.py`, `tests/test_run_record.py`.

Covers requirements 1, 4, 5, 6 and the requirement 9 rows: real `modelUsage` payload with
several models, nodes with no model call, reconciliation (Archon path), deduplicated total,
reader compatibility, existing totals unchanged.

- [ ] **Step 3.1: Update the one assertion that pinned the shortened ID (D9).** In
  `tests/test_run_record.py`, `test_parse_archon_cost_basic`, replace

```python
    assert nodes[0]["model"] == "sonnet-4-6"
```

  with

```python
    # #645: IDs are never shortened; the placeholder value still parses (ID alone).
    assert nodes[0]["model"] == "claude-sonnet-4-6-20251101"
    assert nodes[0]["models"] == [{"model": "claude-sonnet-4-6-20251101"}]
```

- [ ] **Step 3.2: Append the new tests** to the end of `tests/test_run_record.py` (they reuse
  the file's existing `_cost_node`, `_clone_with_workflow`, `_assemble_with_capture`,
  `_AssembleArgs`, `_by_id` and `_REPO_ROOT`):

```python
# ---------------------------------------------------------------------------
# #645: model, cache-token and cost capture
# ---------------------------------------------------------------------------

_USAGE_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "session_usage"
_U_T0 = 1_795_806_798_000
_OPUS, _HAIKU, _SONNET = "claude-opus-5-5", "claude-haiku-4-5-20251001", "claude-sonnet-5"
_PRE_645_NODE_KEYS = ("node_id", "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
                      "cost_usd", "duration_ms", "cost_capture")
_NEW_645_NODE_KEYS = ("models", "gen_ai.usage.cache_read_input_tokens",
                      "gen_ai.usage.cache_creation_input_tokens", "usage_source",
                      "usage_reconciled", "cost_usd_session_delta", "hosting_location")
_NEW_645_RUN_KEYS = ("unattributed_sessions", "factory_instance", "target_repo", "harness")
_NEW_645_TOTALS_KEYS = ("gen_ai.usage.cache_read_input_tokens",
                        "gen_ai.usage.cache_creation_input_tokens",
                        "cost_usd_deduplicated", "cost_usd_deduplicated_complete")

_USAGE_WORKFLOW = """\
name: archon-dark-factory
nodes:
  - id: parse-intent
    prompt: classify the request
  - id: fetch-issue
    bash: echo fetch
  - id: conformance
    command: dark-factory-conformance
  - id: code-review
    command: dark-factory-code-review
  - id: revise-advisory
    command: dark-factory-revise-advisory
"""


def _u_iso(ms):
    from factory_core import session_usage
    return session_usage.ms_to_iso(ms)


def _u_assistant(mid, ms, model, tin, tout, cr=0, cc=0):
    return {"type": "assistant", "timestamp": _u_iso(ms), "isSidechain": False,
            "version": "2.1.282",
            "message": {"id": mid, "model": model,
                        "usage": {"input_tokens": tin, "output_tokens": tout,
                                  "cache_read_input_tokens": cr,
                                  "cache_creation_input_tokens": cc}}}


def _u_user(text, ms):
    return {"type": "user", "timestamp": _u_iso(ms), "isSidechain": False,
            "message": {"role": "user", "content": text}}


def _u_mu(tin, tout, cr, cc, cost):
    return {"inputTokens": tin, "outputTokens": tout, "cacheReadInputTokens": cr,
            "cacheCreationInputTokens": cc, "costUSD": cost}


def _u_cs(start, total, usage):
    return {"type": "cost-state", "startTime": start, "totalCostUSD": total, "modelUsage": usage}


def _u_write(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))


def _u_log_line(ms, msg, node=None, **kw):
    line = {"level": 30, "time": ms, "pid": 1, "hostname": "scrubbed",
            "module": "workflow.dag-executor", "msg": msg}
    if node:
        line["nodeId"] = node
    line.update(kw)
    return json.dumps(line)


def _u_log():
    t = _U_T0
    lines = [
        _u_log_line(t, "dag_workflow_starting"),
        _u_log_line(t + 1, "dag_node_started", "parse-intent", provider="claude"),
        _u_log_line(t + 900, "dag_node_completed", "parse-intent", durationMs=899),
        _u_log_line(t + 901, "dag_node_started", "fetch-issue", type="bash"),
        _u_log_line(t + 950, "dag_node_completed", "fetch-issue", durationMs=49),
        _u_log_line(t + 1000, "dag_node_started", "conformance", provider="claude"),
        _u_log_line(t + 1999, "dag_node_completed", "conformance"),
        _u_log_line(t + 2000, "dag_node_started", "code-review", provider="claude"),
        _u_log_line(t + 2999, "dag_node_completed", "code-review"),
        _u_log_line(t + 4000, "dag_node_started", "revise-advisory", provider="claude"),
        _u_log_line(t + 4999, "dag_node_completed", "revise-advisory"),
        _u_log_line(t + 5000, "dag_workflow_finished"),
    ]
    return "Running workflow: archon-dark-factory\n[parse-intent] Started\n" + "\n".join(lines) + "\n"


_U_COST = {"runId": "wf645", "nodes": [
    _cost_node("parse-intent", 20, 294, 0.0096, 899),
    _cost_node("fetch-issue", ms=49),
    _cost_node("conformance", 20, 200, 1.0, 999),
    _cost_node("code-review", 20, 200, 2.1, 999),
    _cost_node("revise-advisory", 20, 200, 3.1, 999),
]}


def _u_transcripts(root, *, last_cost_state=False):
    """parse-intent's session, Archon's title session, and one session resumed across
    conformance -> code-review -> revise-advisory (each file repeats the earlier ones)."""
    t = _U_T0
    proj = root / "-workspace-target"
    _u_write(proj / "p.jsonl", [
        _u_user("Parse this command", t + 40),
        _u_assistant("p1", t + 100, _HAIKU, 20, 294),
        _u_cs(t + 2, 0.0096, {_HAIKU: _u_mu(1040, 307, 15408, 2750, 0.0096)}),
    ])
    _u_write(proj / "q.jsonl", [
        _u_user("Generate a concise conversation title for this request", t + 50),
        _u_assistant("q1", t + 60, _SONNET, 2, 10),
        _u_cs(t + 3, 0.0143, {_SONNET: _u_mu(2, 10, 0, 0, 0.0133),
                              _HAIKU: _u_mu(966, 13, 0, 0, 0.001)}),
    ])
    msgs = [_u_assistant(f"c{i}", t + ms, _OPUS, 10, 100, 500, 25)
            for i, ms in enumerate((1100, 1200, 2100, 2200, 4100, 4200), start=1)]
    start = t + 1000
    _u_write(proj / "a.jsonl", [_u_user("conformance", t + 1001)] + msgs[:2] + [
        _u_cs(start, 1.0, {_OPUS: _u_mu(20, 200, 1000, 50, 0.9), _HAIKU: _u_mu(5, 5, 0, 0, 0.1)})])
    _u_write(proj / "b.jsonl", msgs[:4] + [
        _u_cs(start, 2.1, {_OPUS: _u_mu(40, 400, 3000, 80, 2.0), _HAIKU: _u_mu(5, 5, 0, 0, 0.1)})])
    _u_write(proj / "c.jsonl", msgs[:6] + ([
        _u_cs(start, 3.1, {_OPUS: _u_mu(60, 600, 4000, 130, 3.0), _HAIKU: _u_mu(5, 5, 0, 0, 0.1)})]
        if last_cost_state else []))
    return root


def _u_assemble(tmp_path, monkeypatch, *, transcripts=None, name="run"):
    root = tmp_path / name
    clone = _clone_with_workflow(root, _USAGE_WORKFLOW)
    monkeypatch.setattr(rr, "_check_needs_discussion", lambda issue: (False, True))
    rec = _assemble_with_capture(root, monkeypatch, _U_COST, log_text=_u_log(), clone=clone)
    if transcripts is None:
        return rec
    args = _AssembleArgs(root, root / "run-record.json")
    args.intent = "fix"
    args.archon_cost_json = str(root / "cost.json")
    args.archon_cost_exit_code = 0
    args.archon_log_file = str(root / "archon-run.log")
    args.clone_dir = str(clone)
    args.transcripts_dir = str(transcripts)
    rr.cmd_assemble(args)
    return json.loads((root / "run-record.json").read_text())


def test_nodes_from_run_obj_real_sdk_model_usage_two_models():
    obj = json.loads((_USAGE_FIXTURES / "archon-cost-sdk-model-usage.json").read_text())
    node = rr._nodes_from_run_obj(obj)[0]
    assert node["model"] == "claude-opus-5-5,claude-sonnet-5"   # full IDs, sorted, joined
    assert node["models"] == [
        {"model": "claude-opus-5-5", "input_tokens": 12, "output_tokens": 4100,
         "cache_read_input_tokens": 610000, "cache_creation_input_tokens": 30500,
         "cost_usd": 1.019869},
        {"model": "claude-sonnet-5", "input_tokens": 90, "output_tokens": 22298,
         "cache_read_input_tokens": 4283508, "cache_creation_input_tokens": 121337,
         "cost_usd": 1.5652096},
    ]
    assert node["gen_ai.usage.input_tokens"] == 90   # Archon's node-level numbers unchanged


def test_nodes_from_run_obj_null_model_usage_is_empty():
    node = rr._nodes_from_run_obj({"nodes": [{"nodeId": "x", "modelUsage": None}]})[0]
    assert node["model"] == "" and "models" not in node


def test_archon_fallback_sets_usage_source_and_reconciles_on_cost():
    obj = json.loads((_USAGE_FIXTURES / "archon-cost-sdk-model-usage.json").read_text())
    node = rr._with_usage_keys(rr._nodes_from_run_obj(obj)[0], True, None, None)
    assert node["usage_source"] == "archon"
    assert node["usage_reconciled"] is True   # 1.019869 + 1.5652096 == 2.5850786
    assert node["gen_ai.usage.cache_read_input_tokens"] == 4893508
    assert node["hosting_location"] == "cloud"


def test_node_with_no_figures_gets_none_and_no_numeric_key():
    node = rr._with_usage_keys({"node_id": "implement", "model": "", "cost_usd": 1.0},
                               True, None, None)
    assert node["usage_source"] == "none" and node["model"] == ""
    for key in ("models", "gen_ai.usage.cache_read_input_tokens",
                "gen_ai.usage.cache_creation_input_tokens", "usage_reconciled",
                "cost_usd_session_delta"):
        assert key not in node


def test_local_node_never_carries_a_claude_priced_cost():
    figures = {"usage_source": "cli_cost_state", "usage_reconciled": False,
               "cost_usd_session_delta": 1.0,
               "models": [{"model": _OPUS, "input_tokens": 1, "output_tokens": 2,
                           "cache_read_input_tokens": 3, "cache_creation_input_tokens": 4,
                           "cost_usd": 1.0}]}
    node = rr._with_usage_keys({"node_id": "implement", "cost_usd": None}, True, figures, "local")
    assert node["hosting_location"] == "local"
    assert "cost_usd_session_delta" not in node
    assert all("cost_usd" not in m for m in node["models"])


def test_assemble_records_model_cache_and_session_delta(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_INSTANCE", "dark-factory-self")
    monkeypatch.setenv("FACTORY_REPO_SLUG", "omniscient/dark-factory")
    rec = _u_assemble(tmp_path, monkeypatch, transcripts=_u_transcripts(tmp_path / "tx"))
    by = _by_id(rec)
    assert by["parse-intent"]["model"] == _HAIKU
    assert by["parse-intent"]["usage_source"] == "cli_cost_state"
    assert by["parse-intent"]["usage_reconciled"] is True
    assert by["parse-intent"]["gen_ai.usage.cache_read_input_tokens"] == 15408
    assert by["conformance"]["model"] == f"{_HAIKU},{_OPUS}"
    assert by["conformance"]["cost_usd_session_delta"] == pytest.approx(1.0)
    assert by["code-review"]["model"] == _OPUS
    assert by["code-review"]["cost_usd"] == pytest.approx(2.1)            # Archon's, unchanged
    assert by["code-review"]["cost_usd_session_delta"] == pytest.approx(1.1)
    assert by["revise-advisory"]["usage_source"] == "transcript_messages"
    assert "cost_usd_session_delta" not in by["revise-advisory"]
    assert by["fetch-issue"]["model"] == "" and by["fetch-issue"]["usage_source"] == "none"
    assert not any(k in by["fetch-issue"] for k in _NEW_645_NODE_KEYS if k != "usage_source")
    assert all(n["hosting_location"] == "cloud" for n in rec["nodes"] if n["node_id"] != "fetch-issue")
    assert rec["totals"]["gen_ai.usage.cache_read_input_tokens"] == 15408 + 1000 + 2000 + 1000
    assert rec["totals"]["gen_ai.usage.cache_creation_input_tokens"] == 2750 + 50 + 30 + 50
    assert [s["first_prompt"] for s in rec["unattributed_sessions"]] == [
        "Generate a concise conversation title for this request"]
    assert rec["unattributed_sessions"][0]["window_node"] == "parse-intent"
    assert rec["unattributed_sessions"][0]["cost_usd"] == pytest.approx(0.0143)
    assert rec["factory_instance"] == "dark-factory-self"
    assert rec["target_repo"] == "omniscient/dark-factory"
    assert rec["harness"] == {"name": "claude-code", "version": "2.1.282"}
    assert all(isinstance(n["model"], str) for n in rec["nodes"])


@pytest.mark.parametrize("last_cost_state,dedup,complete", [
    (False, 0.0096 + 1.0 + 1.1 + 3.1, False),   # revise-advisory falls back to Archon's 3.1
    (True, 0.0096 + 1.0 + 1.1 + 1.0, True),
], ids=["last-file-killed", "every-delta"])
def test_assemble_deduplicated_total(tmp_path, monkeypatch, last_cost_state, dedup, complete):
    rec = _u_assemble(tmp_path, monkeypatch,
                      transcripts=_u_transcripts(tmp_path / "tx", last_cost_state=last_cost_state))
    assert rec["totals"]["cost_usd"] == pytest.approx(6.2096)   # unchanged meaning
    assert rec["totals"]["cost_usd_deduplicated"] == pytest.approx(dedup)
    assert rec["totals"]["cost_usd_deduplicated_complete"] is complete


def test_assemble_existing_values_unchanged_by_transcripts(tmp_path, monkeypatch):
    plain = _u_assemble(tmp_path, monkeypatch, name="plain")
    rich = _u_assemble(tmp_path, monkeypatch, transcripts=_u_transcripts(tmp_path / "tx"),
                       name="rich")
    for key in ("cost_usd", "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens",
                "known_cost_usd", "cost_capture"):
        assert json.dumps(plain["totals"][key]) == json.dumps(rich["totals"][key]), key
    for a, b in zip(plain["nodes"], rich["nodes"]):
        for key in _PRE_645_NODE_KEYS:
            assert json.dumps(a.get(key)) == json.dumps(b.get(key)), (a["node_id"], key)
    assert plain["unattributed_sessions"] == []
    assert plain["totals"]["cost_usd_deduplicated_complete"] is False
    assert plain["harness"] == {"name": "claude-code", "version": None}
    assert {n["usage_source"] for n in plain["nodes"]} == {"none"}


def test_assemble_without_log_or_transcripts_still_writes_new_keys(tmp_path, monkeypatch):
    root = tmp_path / "bare"
    rec = _assemble_with_capture(root, monkeypatch, _U_COST,
                                 clone=_clone_with_workflow(root, _USAGE_WORKFLOW))
    assert all(k in rec for k in _NEW_645_RUN_KEYS)
    assert all(k in rec["totals"] for k in ("cost_usd_deduplicated",
                                            "cost_usd_deduplicated_complete"))
    assert "gen_ai.usage.cache_read_input_tokens" not in rec["totals"]   # unknown, never 0
    # No transcript figures: a resumed node's cumulative cost may still be in the total.
    assert rec["totals"]["cost_usd_deduplicated_complete"] is False


def _strip_645(record):
    rec = json.loads(json.dumps(record))
    for key in _NEW_645_RUN_KEYS:
        rec.pop(key, None)
    for key in _NEW_645_TOTALS_KEYS:
        rec["totals"].pop(key, None)
    for node in rec["nodes"]:
        for key in _NEW_645_NODE_KEYS:
            node.pop(key, None)
    return rec


def test_new_record_shape_renders_like_before_in_every_reader(tmp_path, monkeypatch, capsys):
    import importlib.util
    import reconcile_cost_reports
    from factory_core import cost_report
    rec = _u_assemble(tmp_path, monkeypatch, transcripts=_u_transcripts(tmp_path / "tx"))
    old = _strip_645(rec)
    assert all(isinstance(n["model"], str) for n in rec["nodes"])
    assert cost_report.check_renderable(rec) is None
    assert (cost_report.render(rec, "", "2026-11-27 18:48 UTC", "fix")
            == cost_report.render(old, "", "2026-11-27 18:48 UTC", "fix"))

    spec = importlib.util.spec_from_file_location(
        "run_summary", _REPO_ROOT / ".claude" / "skills" / "operator" / "scripts" / "run_summary.py")
    run_summary = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run_summary)
    outputs = []
    for record in (rec, old):
        monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps(record) + "\n"))
        run_summary.records()
        outputs.append(capsys.readouterr().out)
    assert outputs[0] == outputs[1] and outputs[0]

    reports = []
    for i, record in enumerate((rec, old)):
        state = tmp_path / f"state{i}"
        (state / "run-records").mkdir(parents=True)
        (state / "run-records" / "abc123.json").write_text(json.dumps(record))
        reports.append(reconcile_cost_reports.build_reconciliation_report(state_dir=state))
    assert reports[0] == reports[1]
    assert reports[0]["recoverable"][0]["cost_usd"] == pytest.approx(6.2096)
```

- [ ] **Step 3.3: Run them; they fail.**

```bash
PYTHONPATH=scripts $PY -m pytest tests/test_run_record.py -q 2>&1 | tail -3
```

  Expected: failures in `test_parse_archon_cost_basic` and the new tests
  (`AttributeError: ... has no attribute '_with_usage_keys'`, `KeyError: 'models'`,
  `KeyError: 'unattributed_sessions'`, ...).

- [ ] **Step 3.4: Import the new module.** In `scripts/factory_core/run_record.py`, replace

```python
from . import model_proxy
from . import verdict as _verdict
```

  with

```python
from . import model_proxy
from . import session_usage
from . import verdict as _verdict
```

- [ ] **Step 3.5: Fix the Archon `modelUsage` fallback (requirement 6).** Replace the whole
  `def _nodes_from_run_obj(run_obj: dict) -> list:` function (it reads the first key only,
  strips `claude-`, cuts at `-20`, never reads cache tokens) with:

```python
# The Claude Agent SDK's ModelUsage keys (#645) -> a node's `models` entry keys.
_ARCHON_MODEL_USAGE_KEYS = (
    ("inputTokens", "input_tokens"),
    ("outputTokens", "output_tokens"),
    ("cacheReadInputTokens", "cache_read_input_tokens"),
    ("cacheCreationInputTokens", "cache_creation_input_tokens"),
    ("costUSD", "cost_usd"),
)


def _archon_models(model_usage) -> list:
    """One `models` entry per modelUsage key, the model ID unmodified (#645 requirement 6).
    A value that is not an object (the placeholder 1 in older fixtures) yields the ID alone."""
    if not isinstance(model_usage, dict):
        return []
    out = []
    for model_id in sorted(k for k in model_usage if isinstance(k, str) and k):
        usage = model_usage[model_id]
        entry = {"model": model_id}
        if isinstance(usage, dict):
            for src, dst in _ARCHON_MODEL_USAGE_KEYS:
                value = usage.get(src)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    entry[dst] = value
        out.append(entry)
    return out


def _models_field(models) -> str:
    """A node's `model` string: the full ID, several sorted and joined with ",", or ""."""
    return ",".join(sorted({m["model"] for m in models or [] if m.get("model")}))


def _nodes_from_run_obj(run_obj: dict) -> list:
    """Map a run object's nodes to OTel field names."""
    nodes = []
    for n in run_obj.get("nodes") or []:
        models = _archon_models(n.get("modelUsage") or n.get("model_usage") or {})
        node = {
            "node_id": n.get("nodeId") or n.get("node_id", ""),
            "model": _models_field(models),
            "gen_ai.usage.input_tokens": (
                n.get("inputTokens") or n.get("input_tokens") or 0
            ),
            "gen_ai.usage.output_tokens": (
                n.get("outputTokens") or n.get("output_tokens") or 0
            ),
            "cost_usd": _node_cost(n),
            "duration_ms": n.get("durationMs") or n.get("duration_ms") or 0,
        }
        if models:
            node["models"] = models   # the Archon fallback (#645); usage_source set later
        nodes.append(node)
    return nodes
```

- [ ] **Step 3.6: Add the capture helpers.** Insert, immediately before
  `def _parse_artifact_stage(name: str, content: str) -> "dict | None":`:

```python
# --- Model, cache-token and cost capture (#645) -------------------------------------
_USAGE_CACHE_KEYS = ("cache_read_input_tokens", "cache_creation_input_tokens")


def _called_model(node: dict, node_types: "dict | None", windows: dict) -> bool:
    """Whether a node called a model: a command:/prompt: node that ran (a log window, a
    reported usage, or a missing-capture mark). With no workflow YAML, any reported usage."""
    reported = bool(
        node.get("gen_ai.usage.input_tokens") or node.get("gen_ai.usage.output_tokens")
        or node.get("cost_usd") or node.get("models")
    )
    if node_types is None:
        return reported
    node_id = node.get("node_id")
    return node_types.get(node_id) in _AI_NODE_TYPES and (
        reported or node_id in windows or node.get("cost_capture") == "missing"
    )


def _archon_reconciled(models: list, cost) -> bool:
    costs = [m["cost_usd"] for m in models if isinstance(m.get("cost_usd"), (int, float))]
    return (bool(costs) and isinstance(cost, (int, float))
            and abs(sum(costs) - cost) <= session_usage.ARCHON_COST_EPSILON)


def _with_usage_keys(node: dict, called: bool, figures: "dict | None", hosting) -> dict:
    """A node with #645's additive keys. Pre-existing keys keep their values; `model`
    becomes the full ID(s) of `models`. figures: session_usage's result for the node."""
    node = dict(node)
    if not called:
        node.pop("models", None)
        node["model"] = ""
        node["usage_source"] = "none"
        return node
    if figures:
        models, source = [dict(m) for m in figures["models"]], figures["usage_source"]
    elif node.get("models"):
        models, source = [dict(m) for m in node["models"]], "archon"
    else:
        models, source = [], "none"
    local = hosting == "local"
    if local:
        # Contract §4.10: a local-routed node's cost is unknown, never a Claude-priced figure.
        for m in models:
            m.pop("cost_usd", None)
    node["model"] = _models_field(models)
    if models:
        node["models"] = models
    else:
        node.pop("models", None)
    for key in _USAGE_CACHE_KEYS:
        values = [m[key] for m in models if isinstance(m.get(key), int)]
        if values:
            node[f"gen_ai.usage.{key}"] = sum(values)
    node["usage_source"] = source
    if source == "archon":
        node["usage_reconciled"] = _archon_reconciled(models, node.get("cost_usd"))
    elif source != "none":
        node["usage_reconciled"] = figures["usage_reconciled"]
    if source == "cli_cost_state" and not local:
        node["cost_usd_session_delta"] = figures["cost_usd_session_delta"]
    node["hosting_location"] = "local" if local else "cloud"
    return node


def _apply_session_usage(nodes: list, totals: dict, *, transcripts_dir, started_at,
                         archon_log_file, clone_dir, hosting) -> "tuple[list, dict, dict]":
    """#645: per-node model/cache/cost-delta keys, the new totals, and the run-level
    unattributed sessions and harness version. Without transcripts_dir only the Archon
    modelUsage fallback can fill a node."""
    maps = _load_workflow_node_maps(clone_dir)
    node_types = maps[0] if maps else None
    all_windows: dict = {}
    transcripts = None
    try:   # never lose the record over the log or the transcripts
        all_windows = session_usage.read_node_windows(
            archon_log_file, [n.get("node_id") for n in nodes])
        if transcripts_dir:
            transcripts = session_usage.load_transcripts(transcripts_dir, started_at)
    except Exception as exc:
        print(f"run-record: session usage not read ({type(exc).__name__})", file=sys.stderr)
    called = {n.get("node_id") for n in nodes if _called_model(n, node_types, all_windows)}
    windows = {k: v for k, v in all_windows.items() if k in called}
    attributed: dict = {"nodes": {}, "unattributed_sessions": []}
    harness_version = transcripts["harness_version"] if transcripts else None
    if transcripts:
        try:
            attributed = session_usage.attribute(
                transcripts, windows,
                {n.get("node_id"): (n.get("gen_ai.usage.input_tokens"),
                                    n.get("gen_ai.usage.output_tokens"))
                 for n in nodes if n.get("node_id") in called},
                {n.get("node_id"): n.get("cost_usd") for n in nodes if n.get("node_id") in called},
            )
        except Exception as exc:
            print(f"run-record: session transcripts not attributed ({type(exc).__name__})",
                  file=sys.stderr)
    nodes = [
        _with_usage_keys(n, n.get("node_id") in called,
                         attributed["nodes"].get(n.get("node_id")), hosting)
        for n in nodes
    ]
    totals = dict(totals)
    for key in _USAGE_CACHE_KEYS:
        values = [n[f"gen_ai.usage.{key}"] for n in nodes if f"gen_ai.usage.{key}" in n]
        if values:
            totals[f"gen_ai.usage.{key}"] = sum(values)
    totals["cost_usd_deduplicated"] = None if totals.get("cost_usd") is None else round(sum(
        n["cost_usd_session_delta"] if "cost_usd_session_delta" in n else (n.get("cost_usd") or 0)
        for n in nodes
    ), 10)
    # Fail closed: complete only when every model-calling node has its session delta or is
    # known to be the first file of its chain (so its Archon cost is not cumulative). A node
    # with no transcript figures could be a resumed one whose cost is still overstated.
    totals["cost_usd_deduplicated_complete"] = all(
        "cost_usd_session_delta" in n
        or attributed["nodes"].get(n.get("node_id"), {}).get("first_of_chain") is True
        for n in nodes if n.get("node_id") in called
    )
    run_keys = {"unattributed_sessions": attributed["unattributed_sessions"],
                "harness_version": harness_version}
    return nodes, totals, run_keys
```

- [ ] **Step 3.7: Call it from `cmd_assemble`.** In `cmd_assemble`, directly after the
  `if local_run:` block, which ends with

```python
        nodes = [dict(n, cost_usd=None) for n in nodes]
        totals = dict(totals, cost_usd=None, known_cost_usd=None, cost_capture="not_applicable")
```

  insert (after one blank line):

```python
    model_dispatch = _compute_model_dispatch(ledger_rows)
    # #645: additive model/cache/cost-delta capture; no pre-existing value changes.
    nodes, totals, usage_run = _apply_session_usage(
        nodes, totals,
        transcripts_dir=getattr(args, "transcripts_dir", None),
        started_at=args.started_at,
        archon_log_file=getattr(args, "archon_log_file", None),
        clone_dir=getattr(args, "clone_dir", None) or ".",
        hosting=model_dispatch["result"]["hosting_location"],
    )
```

- [ ] **Step 3.8: Add the run-level keys.** In the `run_record = {...}` literal of
  `cmd_assemble`, replace

```python
        "archon_cost_capture": archon_cost_capture,
        "totals": totals,
    }
```

  with

```python
        "archon_cost_capture": archon_cost_capture,
        "totals": totals,
        "unattributed_sessions": usage_run["unattributed_sessions"],
        "factory_instance": os.environ.get("FACTORY_INSTANCE", ""),
        "target_repo": os.environ.get("FACTORY_REPO_SLUG", ""),
        "harness": {"name": "claude-code", "version": usage_run["harness_version"]},
    }
```

  and replace

```python
    run_record["model_dispatch"] = _compute_model_dispatch(ledger_rows)
```

  with

```python
    run_record["model_dispatch"] = model_dispatch
```

  (computed once in Step 3.7, same value).

- [ ] **Step 3.9: Add the argument.** In `main()`, after
  `a.add_argument("--side-effect-profile", default=None)` of the `assemble` parser, add:

```python
    a.add_argument("--transcripts-dir", default=None,
                   help="Claude Code session transcripts root, for per-node usage (#645)")
```

- [ ] **Step 3.10: Run the tests; they pass.**

```bash
PYTHONPATH=scripts $PY -m pytest tests/test_run_record.py tests/test_session_usage.py tests/test_cost_report.py -q 2>&1 | tail -1
```

  Expected: all passed, no failures (`test_backfill_usage*` do not exist yet).

- [ ] **Step 3.11: Commit.**

```bash
git add scripts/factory_core/run_record.py tests/test_run_record.py
git commit -m "feat(#645): record full model IDs, cache tokens and session cost deltas per node"
```

---

## Task 4: `run-record backfill-usage` (requirement 8)

**Files:** modify `scripts/factory_core/run_record.py`, `tests/test_run_record.py`.

- [ ] **Step 4.1: Append the failing tests** to the end of `tests/test_run_record.py`:

```python
# --- backfill-usage (#645 requirement 8) ---------------------------------------------

def _u_durable(monkeypatch, tmp_path, record):
    state = tmp_path / "state"
    monkeypatch.setattr(rr, "SCHEDULER_STATE_DIR", state)
    path = state / "run-records" / f"{record['run_id']}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record, indent=2))
    return path


def _u_old_record():
    return {"run_id": "3ff088b6", "nodes": [
        {"node_id": "conformance", "model": "", "gen_ai.usage.input_tokens": 20,
         "gen_ai.usage.output_tokens": 200, "cost_usd": 1.0, "duration_ms": 1},
        {"node_id": "code-review", "model": "", "gen_ai.usage.input_tokens": 20,
         "gen_ai.usage.output_tokens": 200, "cost_usd": 2.1, "duration_ms": 1},
        {"node_id": "revise-advisory", "model": "", "gen_ai.usage.input_tokens": 20,
         "gen_ai.usage.output_tokens": 200, "cost_usd": 9.99, "duration_ms": 1},
    ], "totals": {"cost_usd": 13.09}, "model_dispatch": {"result": {"hosting_location": None}}}


def test_backfill_usage_matches_by_cost_and_keeps_the_original(tmp_path, monkeypatch):
    path = _u_durable(monkeypatch, tmp_path, _u_old_record())
    original = path.read_bytes()
    tx = _u_transcripts(tmp_path / "tx")
    os.utime(path, (1_700_000_000, 1_700_000_000))
    renames = []
    real_replace = os.replace
    monkeypatch.setattr(rr.os, "replace", lambda a, b: (renames.append((a, b)), real_replace(a, b)))

    result = rr._backfill_usage("3ff088b6", str(tx))

    assert result["backfilled_nodes"] == ["code-review", "conformance"]
    by = {n["node_id"]: n for n in json.loads(path.read_text())["nodes"]}
    assert by["conformance"]["backfilled"] is True
    assert by["conformance"]["model"] == f"{_HAIKU},{_OPUS}"
    assert by["code-review"]["cost_usd_session_delta"] == pytest.approx(1.1)
    assert by["code-review"]["cost_usd"] == pytest.approx(2.1)
    assert by["revise-advisory"] == _u_old_record()["nodes"][2]   # no matching file: untouched
    pre = path.with_name("3ff088b6.pre-645.json")
    assert pre.read_bytes() == original
    assert [Path(b).name for _, b in renames] == ["3ff088b6.json"]
    assert path.stat().st_mtime == 1_700_000_000

    pre.write_text("kept")
    rr._backfill_usage("3ff088b6", str(tx))
    assert pre.read_text() == "kept"   # the first copy is never overwritten
    assert not (rr.SCHEDULER_STATE_DIR / "runs.jsonl").exists()


def test_backfill_usage_no_record_or_no_match(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "SCHEDULER_STATE_DIR", tmp_path / "state")
    assert rr._backfill_usage("nope", str(tmp_path))["reason"] == "no_record"
    record = _u_old_record()
    for n in record["nodes"]:
        n["cost_usd"] = 42.0
    path = _u_durable(monkeypatch, tmp_path, record)
    before = path.read_bytes()
    assert rr._backfill_usage("3ff088b6", str(_u_transcripts(tmp_path / "tx")))["reason"] == "no_match"
    assert path.read_bytes() == before
    assert not path.with_name("3ff088b6.pre-645.json").exists()


def test_backfill_usage_cli(tmp_path, monkeypatch, capsys):
    _u_durable(monkeypatch, tmp_path, _u_old_record())
    tx = _u_transcripts(tmp_path / "tx")
    monkeypatch.setattr(sys, "argv", ["run_record", "backfill-usage", "--run-id", "3ff088b6",
                                      "--transcripts-dir", str(tx)])
    rr.main()
    assert json.loads(capsys.readouterr().out)["backfilled_nodes"] == ["code-review", "conformance"]
    monkeypatch.setattr(sys, "argv", ["run_record", "backfill-usage", "--run-id", "3ff088b6",
                                      "--transcripts-dir", str(tmp_path / "missing")])
    with pytest.raises(SystemExit) as exc:
        rr.main()
    assert exc.value.code == 2
```

- [ ] **Step 4.2: Run them; they fail.**

```bash
PYTHONPATH=scripts $PY -m pytest tests/test_run_record.py -q -k backfill_usage 2>&1 | tail -3
```

  Expected: 3 failed (`AttributeError: ... has no attribute '_backfill_usage'`, and
  `invalid choice: 'backfill-usage'`).

- [ ] **Step 4.3: Implement.** Insert, immediately before
  `def cmd_backfill_economics(args) -> None:`:

```python
def _backfill_usage(run_id: str, transcripts_dir) -> dict:
    """#645 requirement 8: add the usage keys to an existing durable run record from
    retained transcripts. The original is first copied to <run_id>.pre-645.json (never
    overwritten); the record is then replaced through a temporary file and a rename.
    runs.jsonl is never rewritten. A node with no cost-matched file is left untouched."""
    path = _durable_run_record_path(run_id)
    if path is None or not path.is_file():
        return {"run_id": run_id, "backfilled_nodes": [], "reason": "no_record"}
    original = path.read_bytes()
    record = json.loads(original)
    nodes = record.get("nodes") or []
    found = session_usage.backfill(
        session_usage.load_transcripts(transcripts_dir),
        {n["node_id"]: {"cost_usd": n.get("cost_usd"),
                        "tokens": (n.get("gen_ai.usage.input_tokens"),
                                   n.get("gen_ai.usage.output_tokens"))}
         for n in nodes if isinstance(n.get("node_id"), str)},
    )
    if not found:
        return {"run_id": run_id, "backfilled_nodes": [], "reason": "no_match"}
    hosting = ((record.get("model_dispatch") or {}).get("result") or {}).get("hosting_location")
    record["nodes"] = [
        dict(_with_usage_keys(n, True, found[n["node_id"]], hosting), backfilled=True)
        if n.get("node_id") in found else n
        for n in nodes
    ]
    stat_before = path.stat()
    pre = path.with_name(f"{run_id}.pre-645.json")
    try:
        with open(pre, "xb") as fh:
            fh.write(original)
        os.utime(pre, (stat_before.st_atime, stat_before.st_mtime))
    except FileExistsError:
        pass
    tmp = path.with_name(f".{run_id}.json.tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    os.utime(path, (stat_before.st_atime, stat_before.st_mtime))
    return {"run_id": run_id, "backfilled_nodes": sorted(found)}


def cmd_backfill_usage(args) -> None:
    if not pathlib.Path(args.transcripts_dir).is_dir():
        print(f"run-record: --transcripts-dir {args.transcripts_dir} is not a directory",
              file=sys.stderr)
        sys.exit(2)
    print(json.dumps(_backfill_usage(args.run_id, args.transcripts_dir)))
```

- [ ] **Step 4.4: Register the sub-command.** In `main()`, after the `backfill-economics`
  parser's `be.add_argument("--ledger-path", default=None)`, add (after one blank line):

```python
    bu = sub.add_parser("backfill-usage",
                        help="Add model/cache usage to a durable run record from retained transcripts")
    bu.add_argument("--run-id", required=True)
    bu.add_argument("--transcripts-dir", required=True)
```

  and in the dispatch chain, after

```python
    elif parsed.cmd == "backfill-economics":
        cmd_backfill_economics(parsed)
```

  add

```python
    elif parsed.cmd == "backfill-usage":
        cmd_backfill_usage(parsed)
```

- [ ] **Step 4.5: Run them; they pass.**

```bash
PYTHONPATH=scripts $PY -m pytest tests/test_run_record.py -q 2>&1 | tail -1
python3 scripts/factory_core/cli.py run-record backfill-usage --help | head -2
```

  Expected: all passed; the help starts `usage: run_record backfill-usage [-h] --run-id RUN_ID`
  and continues `--transcripts-dir TRANSCRIPTS_DIR` on the next line.

- [ ] **Step 4.6: Commit.**

```bash
git add scripts/factory_core/run_record.py tests/test_run_record.py
git commit -m "feat(#645): run-record backfill-usage for retained transcripts"
```

---

## Task 5: `entrypoint.sh` — pass the transcripts, keep them (requirements 2, 3, 7)

**Files:** modify `entrypoint.sh`, `tests/test_entrypoint_cost_capture_log_thread.sh`.

The behaviour assertions eval only the function body in a child bash that runs like
`entrypoint.sh` (`set -euo pipefail` plus an `ERR` trap), so they prove a failure never
reaches `on_failure`.

- [ ] **Step 5.1: Update the test's header comment.** In
  `tests/test_entrypoint_cost_capture_log_thread.sh`, replace

```bash
# and the success path deletes $TMP_OUT only after assembling. Text-only — this test
# never sources or executes entrypoint.sh.
```

  with

```bash
# and the success path deletes $TMP_OUT only after assembling. #645: both calls also pass
# --transcripts-dir, the Archon run keeps its JSON log lines (no --quiet, no LOG_LEVEL),
# and _keep_run_transcripts runs on every exit path after the workflow started. This
# test never sources or executes entrypoint.sh; it evals only the _keep_run_transcripts
# function body, in a child bash, against a temporary HOME and state directory.
```

- [ ] **Step 5.2: Add the failing assertions.** In the same file, insert immediately before
  the closing lines

```bash
echo ""
[ "$FAIL" -eq 0 ] && echo "OK" || echo "FAILED"
```

  this block:

```bash
# --- #645: transcripts reach assemble, and the log keeps its JSON node events ---------
if echo "$ON_FAILURE_BODY" | grep -qF -- '--transcripts-dir "${HOME}/.claude/projects"'; then
  pass 'on_failure assemble passes --transcripts-dir'
else
  fail 'on_failure assemble does not pass --transcripts-dir'
fi
if [ -n "${SUCCESS_TAIL:-}" ] && echo "$SUCCESS_TAIL" | grep -qF -- '--transcripts-dir "${HOME}/.claude/projects"'; then
  pass 'success-path assemble passes --transcripts-dir'
else
  fail 'success-path assemble does not pass --transcripts-dir'
fi
RUN_LINE=$(grep -E '^\s*archon workflow run ' "$ENTRYPOINT")
if [ -n "$RUN_LINE" ] && ! echo "$RUN_LINE" | grep -q -- '--quiet'; then
  pass 'archon workflow run is not --quiet (dag_node_* lines stay in $TMP_OUT)'
else
  fail 'archon workflow run is --quiet or missing'
fi
if grep -q 'LOG_LEVEL' "$ENTRYPOINT"; then
  fail 'entrypoint.sh sets LOG_LEVEL (would drop the info-level node events)'
else
  pass 'entrypoint.sh sets no LOG_LEVEL'
fi

# A target's older clone copy of the CLI rejects --transcripts-dir (exit 2): both calls go
# through the baked-copy fallback (#633's helper) or the run would get no record at all.
if echo "$ON_FAILURE_BODY" | grep -qE '^    _cli_with_hosting_fallback run-record assemble \\$'; then
  pass 'on_failure assemble goes through _cli_with_hosting_fallback'
else
  fail 'on_failure assemble bypasses the exit-2 fallback'
fi
if [ -n "${SUCCESS_TAIL:-}" ] && echo "$SUCCESS_TAIL" | grep -qE '^_cli_with_hosting_fallback run-record assemble \\$'; then
  pass 'success-path assemble goes through _cli_with_hosting_fallback'
else
  fail 'success-path assemble bypasses the exit-2 fallback'
fi

# --- #645: _keep_run_transcripts is called on every exit path after the workflow ran ---
calls_in() { echo "$1" | grep -c '_keep_run_transcripts || true'; }
PAUSE_BRANCH=$(echo "$ON_FAILURE_BODY" | sed -n '/_handle_session_window_pause "\$TMP_OUT"; then$/,/^    return$/p')
HOLD_BRANCH=$(echo "$ON_FAILURE_BODY" | sed -n '/&& _handle_blast_hold; then$/,/^    exit 0$/p')
FAIL_PATH=$(echo "$ON_FAILURE_BODY" | sed -n '/rm -f "\$FAIL_COST_JSON" "\$FAIL_COST_STDERR"/,/_keep_run_transcripts/p')
LOOP=$(sed -n '/^while true; do$/,/^done$/p' "$ENTRYPOINT")
LOOP_PAUSE=$(echo "$LOOP" | sed -n '/if _handle_session_window_pause "\$TMP_OUT"; then/,/exit 0/p')
LOOP_HOLD=$(echo "$LOOP" | sed -n '/if _handle_blast_hold; then/,/exit 0/p')
[ "$(calls_in "$PAUSE_BRANCH")" = 1 ] && pass 'on_failure pause branch keeps transcripts' || fail 'on_failure pause branch does not keep transcripts'
[ "$(calls_in "$HOLD_BRANCH")" = 1 ] && pass 'on_failure blast-hold branch keeps transcripts' || fail 'on_failure blast-hold branch does not keep transcripts'
echo "$FAIL_PATH" | grep -qF 'if [ -n "${TMP_OUT:-}" ]; then _keep_run_transcripts || true; fi' \
  && pass 'on_failure failure path keeps transcripts once the workflow ran' \
  || fail 'on_failure failure path does not keep transcripts'
[ "$(calls_in "$LOOP_PAUSE")" = 1 ] && pass 'run-loop pause branch keeps transcripts' || fail 'run-loop pause branch does not keep transcripts'
[ "$(calls_in "$LOOP_HOLD")" = 1 ] && pass 'run-loop blast-hold branch keeps transcripts' || fail 'run-loop blast-hold branch does not keep transcripts'
if [ -n "${SUCCESS_TAIL:-}" ]; then
  KEEP_LINE=$(echo "$SUCCESS_TAIL" | grep -n '^_keep_run_transcripts || true$' | head -1 | cut -d: -f1)
  if [ -n "$KEEP_LINE" ] && [ -n "$RM_LINE" ] && [ "$KEEP_LINE" -lt "$RM_LINE" ] && [ "$KEEP_LINE" -gt "$ASSEMBLE_LINE" ]; then
    pass 'success path keeps transcripts after assemble, before deleting $TMP_OUT'
  else
    fail 'success path does not keep transcripts between assemble and rm'
  fi
fi

# --- #645: _keep_run_transcripts behaviour (function body only, in a child bash) -------
KEEP_FN=$(sed -n '/^_keep_run_transcripts() {$/,/^}$/p' "$ENTRYPOINT")
if [ -z "$KEEP_FN" ]; then
  fail '_keep_run_transcripts() not found'
else
  T=$(mktemp -d /tmp/ep-keep-XXXXXX)
  P="$T/home/.claude/projects/-workspace-x"
  mkdir -p "$P/sess/subagents" "$P/dir.jsonl"
  echo '{"type":"assistant"}' > "$P/sess.jsonl"
  echo '{"type":"assistant","isSidechain":true}' > "$P/sess/subagents/agent-a1.jsonl"
  echo '{}' > "$P/sess/subagents/agent-a1.meta.json"
  echo '{"old":true}' > "$P/old.jsonl"; touch -d '2000-01-01' "$P/old.jsonl"
  mkfifo "$P/fifo.jsonl"
  keep() {  # $1 = state dir; runs like entrypoint.sh: set -euo pipefail, an ERR trap
    HOME="$T/home" SCHEDULER_STATE_DIR="$1" RUN_ID=run645 RUN_STARTED_AT=2026-01-01T00:00:00Z \
      timeout 20 bash -c 'set -euo pipefail; trap "echo ERR_TRAP_FIRED" ERR; eval "$1"; _keep_run_transcripts; echo "rc=$?"' _ "$KEEP_FN" 2>&1
  }
  OUT=$(keep "$T/state")
  D="$T/state/transcripts/run645/-workspace-x"
  { [ -f "$D/sess.jsonl.gz" ] && [ "$(zcat "$D/sess/subagents/agent-a1.jsonl.gz")" = '{"type":"assistant","isSidechain":true}' ]; } \
    && pass 'gzip copies kept with their path under ~/.claude/projects' || fail 'transcript copies missing'
  { [ ! -e "$D/old.jsonl.gz" ] && [ ! -e "$D/fifo.jsonl.gz" ] && [ ! -e "$D/sess/subagents/agent-a1.meta.json.gz" ] && [ ! -e "$D/dir.jsonl.gz" ]; } \
    && pass 'only regular *.jsonl files modified since the run started are kept' || fail 'kept a file outside the input set'
  [ "$(stat -c %a "$D/sess.jsonl.gz")" = 600 ] && [ "$(stat -c %a "$T/state/transcripts/run645")" = 700 ] \
    && [ "$(stat -c %a "$D/sess/subagents")" = 700 ] && pass 'files 0600, directories 0700' || fail 'wrong modes on kept transcripts'
  echo "$OUT" | grep -q '^rc=0$' && ! echo "$OUT" | grep -q ERR_TRAP_FIRED \
    && pass 'returns 0 without tripping the ERR trap' || fail "unexpected status: $OUT"
  [ "$(echo "$OUT" | grep -c 'Session transcripts store:')" = 1 ] && pass 'logs the store size once' || fail 'size line missing'
  echo "$OUT" | grep -q '"type"' && fail 'transcript content leaked to the log' || pass 'no transcript content in the log'
  echo sentinel | gzip > "$D/sess.jsonl.gz"
  keep "$T/state" >/dev/null
  [ "$(zcat "$D/sess.jsonl.gz")" = sentinel ] && pass 'never overwrites an existing copy' || fail 'overwrote an existing copy'
  touch "$T/not-a-dir"
  OUT=$(keep "$T/not-a-dir/state")
  echo "$OUT" | grep -q '^rc=0$' && echo "$OUT" | grep -q '^WARNING: could not keep' \
    && ! echo "$OUT" | grep -q ERR_TRAP_FIRED \
    && pass 'a failure logs one warning and leaves the exit status unchanged' || fail "failure path: $OUT"
  # Plan review: bounds and planted links.
  truncate -s 65M "$P/huge.jsonl"
  OUT=$(keep "$T/state2")
  { [ ! -e "$T/state2/transcripts/run645/-workspace-x/huge.jsonl.gz" ] && [ -f "$T/state2/transcripts/run645/-workspace-x/sess.jsonl.gz" ] \
      && echo "$OUT" | grep -q '^WARNING: could not keep' && echo "$OUT" | grep -q '^rc=0$'; } \
    && pass 'a transcript over 64 MiB is skipped with a warning; the others are kept' || fail "oversize: $OUT"
  rm -f "$P/huge.jsonl"
  mkdir -p "$T/state3/transcripts" "$T/elsewhere"; ln -s "$T/elsewhere" "$T/state3/transcripts/run645"
  OUT=$(keep "$T/state3")
  { [ -z "$(ls -A "$T/elsewhere")" ] && echo "$OUT" | grep -q '^WARNING: could not keep' && echo "$OUT" | grep -q '^rc=0$'; } \
    && pass 'a link planted at the run directory is refused, never followed' || fail "planted link: $OUT"
  mkdir -p "$T/state4"; ln -s "$T/elsewhere" "$T/state4/transcripts"
  OUT=$(keep "$T/state4")
  { [ -z "$(ls -A "$T/elsewhere")" ] && echo "$OUT" | grep -q '^rc=0$'; } \
    && pass 'a link planted at the store is refused' || fail "planted store link: $OUT"
  rm -rf "$T"
fi
```

- [ ] **Step 5.3: Run it; it fails.**

```bash
bash tests/test_entrypoint_cost_capture_log_thread.sh | grep -c 'FAIL'
```

  Expected: `12` (eleven `FAIL:` lines — the two `--transcripts-dir` checks, the two
  fallback checks, the six call-site checks and `_keep_run_transcripts() not found` — plus
  the final `FAILED`).

- [ ] **Step 5.4: Add the function.** In `entrypoint.sh`, insert immediately before
  `post_cost_report() {` (it is above the `ENTRYPOINT_SOURCE_ONLY` return, like every
  other helper):

```bash
# --- Keep this run's Claude Code session transcripts (#645 requirement 7) ---
# run-record assemble reads them inside this --rm container (--transcripts-dir); the
# backfill and any later re-derivation need them afterwards. Every *.jsonl under
# ~/.claude/projects modified since RUN_STARTED_AT is gzip-copied to
# $SCHEDULER_STATE_DIR/transcripts/<run_id>/, path kept relative to ~/.claude/projects,
# dirs 0700, files 0600. Never overwrites, never deletes, never changes the caller's exit
# status (any failure logs one WARNING line). Transcripts hold tool output: they are never
# posted, logged or added to a run record. The store grows without bound until an
# operator prunes it (docs/configuration.md); its size is logged once per run, guarded by
# _RUN_TRANSCRIPTS_KEPT because several exit paths call this. find -newermt keeps files
# strictly newer than RUN_STARTED_AT where session_usage reads "at or after"; at one-second
# resolution the two sets differ only for a file written in the run's first instant.
_keep_run_transcripts() {
  [ -n "${_RUN_TRANSCRIPTS_KEPT:-}" ] && return 0
  _RUN_TRANSCRIPTS_KEPT=1
  local src="${HOME}/.claude/projects"
  local store="${SCHEDULER_STATE_DIR:-/var/lib/dark-factory}/transcripts"
  local dest="${store}/${RUN_ID:-unknown}"
  [ -d "$src" ] || return 0
  # Bounds (plan review): the agent can write any file under ~/.claude/projects, and the
  # store shares a volume with scheduler-state.json. A file over 64 MiB, or anything past
  # 256 MiB of source per run, is skipped and reported by the WARNING line; each gzip is
  # time-bounded. The largest retained real run holds 4.2 MiB.
  local file_max=67108864 run_max=268435456
  if ! (
    set +e
    umask 077
    # A link planted at the store or at this run's directory is refused, never followed.
    [ ! -L "$store" ] && mkdir -p "$dest" && [ ! -L "$dest" ] && chmod 700 "$store" "$dest" || exit 1
    rc=0
    total=0
    while IFS= read -r -d '' f; do
      size=$(stat -c %s -- "$f" 2>/dev/null) || { rc=1; continue; }
      if [ "$size" -gt "$file_max" ] || [ $((total + size)) -gt "$run_max" ]; then
        rc=1
        continue
      fi
      total=$((total + size))
      out="${dest}/${f#"$src"/}.gz"
      { [ -e "$out" ] || [ -L "$out" ]; } && continue
      if mkdir -p "${out%/*}" && timeout 120 gzip -c -- "$f" > "${out}.tmp.$$" \
          && chmod 600 "${out}.tmp.$$" && mv -n -- "${out}.tmp.$$" "$out"; then
        :
      else
        rc=1
      fi
      rm -f -- "${out}.tmp.$$"
    done < <(find "$src" -type f -name '*.jsonl' -newermt "${RUN_STARTED_AT:-1970-01-01T00:00:00Z}" -print0 2>/dev/null)
    exit "$rc"
  ); then
    echo "WARNING: could not keep every session transcript of run ${RUN_ID:-unknown} under ${dest}" >&2
  fi
  echo "Session transcripts store: $(du -sk "$store" 2>/dev/null | cut -f1) KiB under ${store}"
  return 0
}
```

- [ ] **Step 5.4b: Route `assemble` through the exit-2 fallback (D15).** Update the helper's
  comment so no later reader "fixes" the cloud use away. Replace its first line

```bash
# Verified-local runs only (#633). Deliberately broad: ANY exit code 2 from the clone's cli.py
```

  with

```bash
# Verified-local runs (#633), and both `run-record assemble` calls on every run (#645).
# Deliberately broad: ANY exit code 2 from the clone's cli.py
```

  and replace its last comment line plus the function header

```bash
# attempt whose status is returned is printed, stderr merged into stdout.
_cli_with_hosting_fallback() {
```

  with

```bash
# attempt whose status is returned is printed, stderr merged into stdout. #645: a target's
# older clone copy rejects assemble's --transcripts-dir with exit 2; the baked copy must
# then write the record.
_cli_with_hosting_fallback() {
```

  In `on_failure`'s failure-path assemble call, replace

```bash
    python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record assemble \
      --run-id "${RUN_ID:-unknown}" \
```

  with

```bash
    _cli_with_hosting_fallback run-record assemble \
      --run-id "${RUN_ID:-unknown}" \
```

  and in the success-path assemble call, replace

```bash
python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record assemble \
  --run-id "${RUN_ID:-unknown}" \
  --issue "$ISSUE_NUM" \
```

  with

```bash
_cli_with_hosting_fallback run-record assemble \
  --run-id "${RUN_ID:-unknown}" \
  --issue "$ISSUE_NUM" \
```

- [ ] **Step 5.5: `on_failure`, session-window pause branch.** Replace

```bash
${SUMMARY_LINE}"
    fi
    post_cost_report || true
    return
  fi
```

  with

```bash
${SUMMARY_LINE}"
    fi
    _keep_run_transcripts || true
    post_cost_report || true
    return
  fi
```

- [ ] **Step 5.6: `on_failure`, blast-radius hold branch.** Replace

```bash
  if [ -n "${TMP_OUT:-}" ] && [ -f "$TMP_OUT" ] && _handle_blast_hold; then
    rm -f "${TMP_OUT:-}" 2>/dev/null || true
    exit 0
  fi
```

  with

```bash
  if [ -n "${TMP_OUT:-}" ] && [ -f "$TMP_OUT" ] && _handle_blast_hold; then
    _keep_run_transcripts || true
    rm -f "${TMP_OUT:-}" 2>/dev/null || true
    exit 0
  fi
```

- [ ] **Step 5.7: `on_failure`, failure path.** In the failure-path `run-record assemble`
  call, replace

```bash
      --archon-log-file "${TMP_OUT:-}" \
      --clone-dir "${CLONE_DIR:-.}" \
```

  with

```bash
      --archon-log-file "${TMP_OUT:-}" \
      --transcripts-dir "${HOME}/.claude/projects" \
      --clone-dir "${CLONE_DIR:-.}" \
```

  and replace the end of that block

```bash
    rm -f "$FAIL_COST_JSON" "$FAIL_COST_STDERR"
  fi
```

  with

```bash
    rm -f "$FAIL_COST_JSON" "$FAIL_COST_STDERR"
  fi
  # #645: only once the workflow ran (TMP_OUT is set by the run loop).
  if [ -n "${TMP_OUT:-}" ]; then _keep_run_transcripts || true; fi
```

- [ ] **Step 5.8: Run loop, pause and hold exits.** In the `while true; do` loop, replace

```bash
    if _handle_session_window_pause "$TMP_OUT"; then
      rm -f "$TMP_OUT"
      exit 0
    fi
    if _handle_blast_hold; then
      rm -f "$TMP_OUT"
      exit 0
    fi
```

  with

```bash
    if _handle_session_window_pause "$TMP_OUT"; then
      _keep_run_transcripts || true
      rm -f "$TMP_OUT"
      exit 0
    fi
    if _handle_blast_hold; then
      _keep_run_transcripts || true
      rm -f "$TMP_OUT"
      exit 0
    fi
```

- [ ] **Step 5.9: Success path.** In the success-path `run-record assemble` call, replace

```bash
  --archon-log-file "$TMP_OUT" \
  --out-file "$ARTIFACTS_DIR/run-record.json" \
```

  with

```bash
  --archon-log-file "$TMP_OUT" \
  --transcripts-dir "${HOME}/.claude/projects" \
  --out-file "$ARTIFACTS_DIR/run-record.json" \
```

  and replace

```bash
  --clone-dir "$CLONE_DIR" || true

rm -f "$ARCHON_COST_JSON" "$ARCHON_COST_STDERR" "$TMP_OUT"
```

  with

```bash
  --clone-dir "$CLONE_DIR" || true

_keep_run_transcripts || true
rm -f "$ARCHON_COST_JSON" "$ARCHON_COST_STDERR" "$TMP_OUT"
```

- [ ] **Step 5.9b: Keep the three `on_failure` harnesses hermetic (D16).** In each of
  `tests/test_entrypoint_error_signature.sh`, `tests/test_entrypoint_session_window.sh` and
  `tests/test_entrypoint_blast_hold.sh` (whose section E runs the real `on_failure` with
  `TMP_OUT` set), replace

```bash
trap - ERR
set +e; set +u; set +o pipefail
```

  with

```bash
trap - ERR
set +e; set +u; set +o pipefail
# #645: on_failure keeps the run's session transcripts from $HOME/.claude/projects. This
# harness is not about that (tests/test_entrypoint_cost_capture_log_thread.sh is) and must
# never copy the machine's own transcripts, so the copy is a no-op here.
_keep_run_transcripts() { :; }
```

- [ ] **Step 5.10: Run it; it passes, and nothing else that sources `entrypoint.sh` broke.**

```bash
bash -n entrypoint.sh && echo syntax-ok
bash tests/test_entrypoint_cost_capture_log_thread.sh | tail -1
for t in test_entrypoint_session_window test_entrypoint_blast_hold test_entrypoint_error_signature \
         test_cost_report_harness_economics test_run_record_hermetic test_entrypoint_cost_report_regression \
         test_entrypoint_cost_report_comment test_entrypoint_current_run test_entrypoint_hosting_fallback \
         test_entrypoint_preflight test_cost_report_endpoint test_entrypoint_conflict_escalate; do
  PATH="$(dirname "$(command -v $PY)"):$PATH" bash tests/$t.sh >/tmp/$t.log 2>&1; echo "$t rc=$?"
done
PYTHONPATH=scripts $PY -m pytest tests/test_entrypoint_undefined_functions.py -q 2>&1 | tail -1
```

  Expected: `syntax-ok`, `OK`, every `rc=0`, and the pytest line passes. These shell
  tests find the scripts at `$(dirname "$REPO_ROOT")/dark-factory/scripts`, so run them
  from a checkout whose directory is named `dark-factory` (the factory clone is).

- [ ] **Step 5.11: Commit.**

```bash
git add entrypoint.sh tests/test_entrypoint_cost_capture_log_thread.sh \
  tests/test_entrypoint_error_signature.sh tests/test_entrypoint_session_window.sh \
  tests/test_entrypoint_blast_hold.sh
git commit -m "feat(#645): pass session transcripts to run-record and keep them on the state volume"
```

---

## Task 6: Documentation

**Files:** modify `docs/run-lifecycle.md`, `docs/configuration.md`.

- [ ] **Step 6.1: `docs/run-lifecycle.md`, §7 step 2.** After the line

```
   scores an artifact-less run `failed` or `clean_abort`. Failure is ignored.
```

  insert:

```markdown
   **Model, cache tokens and cost per node (#645).** Archon's cost JSON carries no
   `modelUsage`, so assemble also reads the Claude Code CLI's own session transcripts
   (`--transcripts-dir "${HOME}/.claude/projects"`: every `*.jsonl` modified since the run
   started, sub-agent files included) through `scripts/factory_core/session_usage.py`. Each
   API message counts once per `message.id` across the run and belongs to the node whose
   window, its `dag_node_started`/`dag_node_completed` JSON lines in `$TMP_OUT`, holds the
   message's own timestamp. A node's figures come, in order of preference, from the CLI's
   closing `cost-state` line (`usage_source: cli_cost_state`; sub-agent and small-model
   spend included, differenced against the previous file of a resumed session), from the
   transcript messages (`transcript_messages`, no cost), from Archon's `modelUsage`
   (`archon`), or from nothing (`none`). A node that called a model then carries `models`
   (one entry per model, full IDs), `model` (those IDs joined with `,`, still a string),
   `gen_ai.usage.cache_read_input_tokens` and `gen_ai.usage.cache_creation_input_tokens`,
   `usage_reconciled`, `hosting_location` and, from `cost-state`, `cost_usd_session_delta`.
   The run record adds `unattributed_sessions` (sessions no node owns, such as Archon's
   title generation; added to no node and no total), `factory_instance`, `target_repo`,
   `harness` and the two cache totals. No existing key changes value or meaning except
   `model`, which was always `""` before. The capture depends on `cost-state` and the
   transcript layout of the pinned CLI (2.1.282), which are not a documented interface;
   when neither shape is found a node records `usage_source: none` and nothing is guessed.

   **Which cost to read.** `cost_usd` and `totals.cost_usd` stay Archon's figures, and
   every reader (the cost report, the scheduler's economics) still uses them. On a node
   that resumes a session (conformance, code-review, revise-advisory) Archon's `cost_usd`
   is cumulative, so `totals.cost_usd` overstates the run. For actual spend prefer
   `totals.cost_usd_deduplicated` (each node's `cost_usd_session_delta` where present, else
   its `cost_usd`) when `totals.cost_usd_deduplicated_complete` is `true`. When it is
   `false`, at least one node's cost may still be Archon's cumulative figure (it has no
   session delta and is not known to be the first file of its chain), so the deduplicated
   total is an upper bound. Switching the readers is a separate ticket.

   On every exit once the workflow has run (success, failure, session-window pause,
   blast-radius hold) `_keep_run_transcripts` gzip-copies the same transcripts to
   `$SCHEDULER_STATE_DIR/transcripts/<run_id>/` (see `docs/configuration.md`).
   `run-record backfill-usage --transcripts-dir <dir> --run-id <id>` applies the capture to
   an existing durable record from retained transcripts: the record has no Archon log, so
   each node is matched to the transcript whose `cost-state` total equals its recorded
   `cost_usd`, the original is first copied to `run-records/<run_id>.pre-645.json`, and the
   record is replaced through a rename. It is an operator step; `runs.jsonl` is never
   rewritten.
```

- [ ] **Step 6.2: `docs/configuration.md`.** In the "Variables not derived from a config
  key" table, directly after the row that starts `| (path) blast-radius approvals |`,
  insert this row:

```markdown
| (path) session transcripts | `${SCHEDULER_STATE_DIR}/transcripts/<run_id>/` — each run's Claude Code session transcripts (every `*.jsonl` under `~/.claude/projects` modified during the run, sub-agent files included), gzip-compressed, paths kept | written by `entrypoint.sh` (`_keep_run_transcripts`) on every exit once the workflow ran; read back by `run-record backfill-usage` | dirs `0700`, files `0600`. They hold tool output, so they are never posted, logged or added to a run record. Nothing in the factory deletes them: the store grows without bound (0.6–4.2 MB of transcripts per run before compression, measured on five runs) until an operator prunes it by hand; each run logs the store's size. A file over 64 MiB, and anything past 256 MiB of source per run, is not kept (one WARNING line). Getting them onto the host is the follow-up ticket's stats sync (#645) |
```

- [ ] **Step 6.3: Check the references.**

```bash
$PY scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
PYTHONPATH=scripts $PY -m pytest tests/ -q -k "docs" 2>&1 | tail -1
```

  Expected: `Docs reference check passed for 4 doc(s).` and the docs tests pass.

- [ ] **Step 6.4: Commit.**

```bash
git add docs/run-lifecycle.md docs/configuration.md
git commit -m "docs(#645): run-record usage keys, which cost to prefer, transcript store"
```

---

## Task 7: Full verification and PR

**Files:** none new.

- [ ] **Step 7.1: Full suite and CI parity.**

```bash
PYTHONPATH=scripts $PY -m pytest tests/ -q 2>&1 | tail -1
$PY scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
$PY scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```

  Expected: no failures (the scratch run had 4305 passed before Task 6), and both DAG
  checks exit 0 (no workflow file is changed).

- [ ] **Step 7.2: Scope check** (two-dot diff, per the factory's memory pattern, so files
  `main` already carries are not reported):

```bash
git diff --name-only origin/main HEAD
```

  Expected: exactly the files of the File Structure table (plus the spec, carried over by
  `setup-branch`). Nothing under `deploy/`, `.github/`, `workflows/`, `config/`, and
  neither `cli.py`, `cost_report.py` nor `run_summary.py`.

- [ ] **Step 7.3: Expected gate outcome.** `scripts/gate_blast_radius.py` should return
  **HUMAN_REQUIRED** with one trigger, `scripts/factory_core/run_record.py` (hotspot). That
  hold is the intended outcome; do not try to avoid it.

- [ ] **Step 7.4: PR body must state:**
  - The capture depends on `cost-state` and the transcript layout of the pinned Claude Code
    CLI (2.1.282), which are internals, not a documented interface. When neither shape is
    found a node records `usage_source: "none"` and nothing is guessed.
  - The sub-agent fixture is a real session captured in the factory image during planning
    (verified fact 3). The failing-node log line is built from Archon 0.3.10's source shape
    (`dag-executor.ts:1229/1396/1660`), not captured: an Archon run inside a run container
    would hijack the entrypoint's `archon workflow cost --last`.
  - Decisions D1-D16 of the plan, including that assemble's output is now printed after it
    exits (D15), and that `tests/test_entrypoint_hosting_fallback.sh`'s header ("A cloud run
    never uses the helper") still describes only its own cases.
  - **Operator step (not run by the factory):** backfill the five retained runs, for each of
    `3ff088b6…` (instance `dark-factory`) and `ca74156e…`, `c368c654…`, `76ea5ef3…`,
    `67279dee…` (instance `dark-factory-self`), inside that instance's scheduler container
    with the run's retained transcripts copied in:

    ```bash
    python3 /opt/dark-factory/scripts/factory_core/cli.py run-record backfill-usage \
      --run-id <full run id> --transcripts-dir <dir holding that run's transcripts>
    ```

    It prints `{"run_id": ..., "backfilled_nodes": [...]}`, keeps the original as
    `run-records/<run_id>.pre-645.json`, and never touches `runs.jsonl`. Every other
    historical record has no retained source and stays as it is.
  - Follow-ups (not filed by the factory): `omniscient/Archon` — relay `modelUsage`,
    cumulative `cost_usd` on resumed nodes, a `session_id` on node events; dark-factory —
    switch `cost_report` and the scheduler's economics to `cost_usd_session_delta` /
    `totals.cost_usd_deduplicated`. Durability (rotation, the host stats mount, migration,
    the `docker compose down -v` check, mirroring `transcripts/`) is #652.

- [ ] **Step 7.5: Push** the branch before the turn ends.

---

## Acceptance criteria of #645 covered by this plan

| Criterion | Where |
|---|---|
| 1. Non-empty `model` (full ID) and cache tokens for every node that called a model; a test with a real multi-model payload | Tasks 2-3: `test_real_multi_model_cost_state_payload` (the CLI's real two-model `cost-state` `modelUsage`), `test_nodes_from_run_obj_real_sdk_model_usage_two_models` (SDK shape), `test_assemble_records_model_cache_and_session_delta` |
| 2. Tokens reconcile with `cost_usd` | Task 2 `test_reconciliation_flags_token_and_cost_mismatch`, `test_cost_state_primary_first_resumed_and_absent`; Task 3 `test_archon_fallback_sets_usage_source_and_reconciles_on_cost` |
| 3-5. Rotation, host mount, `down -v` check | #652. Here: the backfill keeps the original record and the transcript copy never overwrites (Tasks 4-5) |
| 6. Best-effort backfill | Task 4 and the operator step of 7.4 |
