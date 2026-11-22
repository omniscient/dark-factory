# Run Record: Classify Zero-Usage AI Nodes as Cost-Missing — Implementation Plan

**Issue:** #453

Revised: 2026-11-22 (operator amendment after plan gate)

**Goal:** A `command:`/`prompt:` node that Archon reports with zero cost and tokens (MarketHawk
#388's `implement`: `| implement |  | 0 | 0 | $0 | 25m 55s |`) is recorded as
`cost_capture: "missing"` (`reason: "no_usage_reported"`, null cost/tokens) instead of a genuine
`$0`. A node Archon's log shows as started but that is absent from the cost report is recorded as
`missing` (`reason: "not_in_cost_report"`). Totals, the cost comment and a health event make the gap
visible. A healthy run's comment stays byte-identical.

**Architecture:** All classification lives in `scripts/factory_core/run_record.py::cmd_assemble`.
Three new pure helpers handle it: a workflow-YAML loader (`node_id -> type`, `log token -> node_id`,
where a `command:` node's token is its command name), a line-anchored parser for the tee'd
`archon workflow run` log (`$TMP_OUT`), and a per-node classifier. A fourth helper,
`_capture_totals`, builds the capture-aware `totals` dict and emits health events.
`cost_report.render` renders missing rows and a partial-capture note. It uses `known_*` totals when
capture is partial. `entrypoint.sh` changes at its two **existing** assemble call sites only: they
pass `--archon-log-file`, `on_failure` gains `--clone-dir`, and the success path deletes `$TMP_OUT`
after assemble. There is no new assemble call site.

**Tech Stack:** Python 3.12 (stdlib + PyYAML, already a CI dependency), bash, pytest.

**Spec:** `docs/superpowers/specs/2026-11-22-run-record-node-cost-capture-design.md` (operator-amended, A1–A7)

---

## Verified facts this plan builds on

Each was re-checked on this branch before planning.

- Archon's CLI (`/opt/archon/packages/cli/src/commands/workflow.ts:133-146`) writes
  `[${nodeName}] Started`, `[..] Completed (<dur>)`, `[..] Failed: <error>`, `[..] Skipped (<reason>)`
  to stderr. `nodeName` is `node.command ?? node.id` (`dag-executor.ts:612,1061,1106,1149`). So the
  log token for `implement` is `dark-factory-implement`, and for `parse-intent`/`fetch-issue` it is
  the id. **`Failed` is followed by a colon**, not whitespace. The regex therefore ends in `\b`, not
  `\s`.
- `workflows/archon-dark-factory.yaml` has 48 nodes: 37 `bash`, 8 `command`, 3 `prompt`
  (`parse-intent`, `summarize-feedback`, `classify-preview`). No two nodes share a command name.
- `.archon/workflows/` is **not tracked in git**. It is git-excluded, but it does exist inside the
  factory container. Existing `cmd_assemble` tests run with `clone_dir="."`, so they behave
  differently by environment:
  - In CI, `./.archon/workflows/...` is absent and the autouse fixture points the baked path at a
    missing file. They take the "unknown" path, with totals unchanged.
  - In the container, they load the real DAG and classify, and each emits one
    `node_log_unavailable` event, which the patched `SEQ_URL` swallows.
  
  Both pass, because every existing fixture's nodes report nonzero usage. New tests never rely on
  `clone_dir="."`: they always build a tmp clone with an explicit YAML.
- The prototype showed that new health events in existing assemble tests try to reach
  `http://seq:5341`. That made `tests/test_run_record.py` take ~58s instead of ~10s, and inside the
  factory container `seq` can resolve to the real Seq. So Task 1 points the autouse fixture's
  `rr.SEQ_URL` at `http://127.0.0.1:9` (connection refused at once) and points
  `rr.BAKED_WORKFLOW_PATH` at a missing tmp file.
- `tests/test_run_record_hermetic.sh` flags any `tests/test_*.sh` that mentions `cli.py` or
  `ENTRYPOINT_SOURCE_ONLY=1` together with `run-record assemble`. The new static `.sh` test must not
  contain the literal string `cli.py`.
- `_compute_harness_economics` already derives `tokens_per_task`/`factory_cpm` as `None` when the
  totals token fields are `None`. It needs no code change. Task 5 only adds the regression test.
- `reconcile_cost_reports.py` reads `totals.cost_usd` verbatim. It needs no code change. Task 5 adds
  a regression test.

Memory lessons baked into the tasks below:
- **Join log lines on the command name, never the node id** (`.archon/memory/architecture.md`,
  #453). Task 1 builds the `command -> id` map and tests it against the shipped DAG. Task 2 tests
  `[dark-factory-implement]` resolving to `implement`.
- **Present-with-zero is the #388 shape, not absence** (`architecture.md`, #453). Task 3's core
  case is a *present* node with all-zero usage and a real duration.
- **Re-derive `entrypoint.sh` locations from current text, not cited line numbers**
  (operator practice; no memory entry). Task 8 anchors every edit on exact unique text.

## File Structure

| Path | Change | Responsibility |
|---|---|---|
| `scripts/factory_core/run_record.py` | modify | YAML node-map loader, log parser, classifier, `_capture_totals`; `cmd_assemble` uses them; `assemble --archon-log-file` |
| `scripts/factory_core/cost_report.py` | modify | `format_node_row`, `format_partial_capture_note`; `render` uses `known_*` on partial |
| `entrypoint.sh` | modify | thread `$TMP_OUT` into both existing assemble calls; `on_failure` gains `--clone-dir`; success path deletes `$TMP_OUT` after assemble |
| `tests/test_run_record.py` | modify | fixtures (#388 shape, log sample, healthy run, absent nodes), unit + integration + scoring regression + byte-identical tests |
| `tests/test_cost_report.py` | modify | missing-row, note-line, partial golden, cumulative lower-bound, complete-run-unchanged tests |
| `tests/test_reconcile_cost_reports.py` | modify | partial run reads null `cost_usd` like a failed-capture run |
| `tests/test_entrypoint_cost_capture_log_thread.sh` | **create** | static guard over `entrypoint.sh` text |
| `.github/workflows/ci.yml` | modify | list the new `.sh` test |
| `docs/run-lifecycle.md` | modify | re-map shifted `run_record.py`/`entrypoint.sh` citations; §7 prose (Task 8b) |
| `docs/features.md` | modify | re-map shifted citations (Task 8b) |
| `docs/configuration.md` | modify | re-map shifted citations (Task 8b) |

No other file changes. No changes under `gate_*`, breaker, budget nodes, `deploy/**`,
`.github/workflows/publish.yml`, `workflows/archon-dark-factory.yaml`, or
`reconcile_cost_reports.py`.

All commands run from the repo root. Python tests: `PYTHONPATH=scripts python -m pytest ...` (CI
form).

---

## Task 1: Workflow node-type / log-token map

**Files:** `tests/test_run_record.py`, `scripts/factory_core/run_record.py`

- [ ] **Step 1 — Write failing tests.** Append to the end of `tests/test_run_record.py`. This block
  holds the shared #453 fixtures used by Tasks 1–7 plus the Task 1 tests:

```python


# ---------------------------------------------------------------------------
# per-node cost capture (#453)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]

_WORKFLOW_FIXTURE = """\
name: archon-dark-factory
nodes:
  - id: parse-intent
    prompt: classify the request
  - id: fetch-issue
    bash: echo fetch
  - id: digest-comments
    bash: echo digest
  - id: implement
    command: dark-factory-implement
  - id: classify-preview
    prompt: classify the preview
  - id: validate
    command: dark-factory-validate
  - id: conformance
    command: dark-factory-conformance
  - id: push-and-pr
    bash: echo push
"""


def _clone_with_workflow(root, text=_WORKFLOW_FIXTURE):
    wf_dir = root / "clone" / ".archon" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "archon-dark-factory.yaml").write_text(text)
    return root / "clone"


def _cost_node(node_id, tin=0, tout=0, cost=0, ms=1000):
    return {"nodeId": node_id, "inputTokens": tin, "outputTokens": tout,
            "costUsd": cost, "durationMs": ms, "modelUsage": {}}


# MarketHawk #388 (2026-09-26 17:28Z, fix, failed): `implement` completed in 25m 55s
# with zero reported usage; the four other AI nodes account for the whole $1.89.
_COST_388 = {
    "runId": "wf388",
    "nodes": [
        _cost_node("parse-intent", 20, 297, 0.0097, 5200),
        _cost_node("fetch-issue", ms=1800),
        _cost_node("implement", ms=1_555_000),
        _cost_node("classify-preview", 40, 600, 0.0203, 6000),
        _cost_node("validate", 30, 9000, 0.62, 240_000),
        _cost_node("conformance", 22, 17800, 1.24, 300_000),
    ],
}

# Archon names command: nodes by command name, everything else by id (#453).
_LOG_388 = """\
Starting dark factory: Fix issue #388
[parse-intent] Started
[parse-intent] Completed (5.2s)
[fetch-issue] Started
[fetch-issue] Completed (1.8s)
[digest-comments] Skipped (when_condition)
[dark-factory-implement] Started
  agent output quoting [dark-factory-plan] Started mid-line
[not-a-node] Started
[dark-factory-implement] Completed (25m 55s)
[classify-preview] Started
[classify-preview] Completed (6.0s)
[dark-factory-validate] Started
[dark-factory-validate] Completed (4m 0s)
[dark-factory-conformance] Started
[dark-factory-conformance] Completed (5m 0s)
"""

_COST_HEALTHY = {
    "runId": "wfok",
    "nodes": [
        _cost_node("parse-intent", 20, 270, 0.0207, 7800),
        _cost_node("fetch-issue", ms=1800),
        _cost_node("implement", 300, 52000, 4.1, 1_200_000),
    ],
}

_LOG_HEALTHY = """\
[parse-intent] Started
[parse-intent] Completed (7.8s)
[fetch-issue] Started
[fetch-issue] Completed (1.8s)
[digest-comments] Skipped (when_condition)
[dark-factory-implement] Started
[dark-factory-implement] Completed (20m 0s)
"""


@pytest.fixture
def health_events(monkeypatch):
    events = []
    monkeypatch.setattr(
        rr, "emit_health_event",
        lambda event, issue, run_id, detail: events.append((event, detail)),
    )
    return events


def _assemble_with_capture(root, monkeypatch, cost_obj, *, log_text=None, clone=None,
                           status="completed", exit_code=0):
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(rr, "JSONL_PATH", root / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", root / "no-ledger.jsonl")
    cost_json = root / "cost.json"
    cost_json.write_text(json.dumps(cost_obj))
    out = root / "run-record.json"
    args = _AssembleArgs(root, out)
    args.intent = "fix"
    args.status = status
    args.archon_cost_json = str(cost_json)
    args.archon_cost_exit_code = exit_code
    args.clone_dir = str(clone if clone is not None else _clone_with_workflow(root))
    if log_text is not None:
        log = root / "archon-run.log"
        log.write_text(log_text)
        args.archon_log_file = str(log)
    rr.cmd_assemble(args)
    return json.loads(out.read_text())


def _by_id(rec):
    return {n["node_id"]: n for n in rec["nodes"]}


def test_node_maps_from_clone_workflow(tmp_path):
    clone = _clone_with_workflow(tmp_path)
    node_types, log_tokens = rr._load_workflow_node_maps(clone)
    assert node_types["implement"] == "command"
    assert node_types["parse-intent"] == "prompt"
    assert node_types["fetch-issue"] == "bash"
    assert log_tokens["dark-factory-implement"] == "implement"
    assert log_tokens["parse-intent"] == "parse-intent"


def test_node_maps_real_workflow_keys_command_nodes_by_command_name(tmp_path):
    # Guards the rejected draft's bug: Archon logs `[dark-factory-implement]`, the cost
    # report says `implement` — the map must join the two for the shipped DAG.
    real = (_REPO_ROOT / "workflows" / "archon-dark-factory.yaml").read_text()
    node_types, log_tokens = rr._load_workflow_node_maps(_clone_with_workflow(tmp_path, real))
    assert log_tokens["dark-factory-implement"] == "implement"
    assert log_tokens["dark-factory-conformance"] == "conformance"
    assert node_types["implement"] == "command"
    assert node_types["classify-preview"] == "prompt"
    assert node_types["fetch-issue"] == "bash"


def test_node_maps_fall_back_to_baked_workflow(tmp_path, monkeypatch):
    baked = tmp_path / "baked.yaml"
    baked.write_text(_WORKFLOW_FIXTURE)
    monkeypatch.setattr(rr, "BAKED_WORKFLOW_PATH", baked)
    (tmp_path / "bare-clone").mkdir()
    node_types, _ = rr._load_workflow_node_maps(tmp_path / "bare-clone")
    assert node_types["implement"] == "command"


def test_node_maps_none_when_no_workflow_loads(tmp_path):
    assert rr._load_workflow_node_maps(tmp_path / "nowhere") is None
    assert rr._load_workflow_node_maps(_clone_with_workflow(tmp_path, "nodes: [broken")) is None


def test_node_maps_drop_ambiguous_log_token(tmp_path):
    text = (
        "nodes:\n"
        "  - id: a\n    command: shared-cmd\n"
        "  - id: b\n    command: shared-cmd\n"
    )
    _, log_tokens = rr._load_workflow_node_maps(_clone_with_workflow(tmp_path, text))
    assert "shared-cmd" not in log_tokens
    assert log_tokens["a"] == "a"
```

- [ ] **Step 2 — Verify fail.**
  `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q -k node_maps`
  Expected: `5 failed`, each with
  `AttributeError: module 'factory_core.run_record' has no attribute '_load_workflow_node_maps'`
  (or `... has no attribute 'BAKED_WORKFLOW_PATH'` for the fallback test).

- [ ] **Step 3 — Implement.** In `scripts/factory_core/run_record.py`, insert this block
  immediately **before** the line `def _parse_artifact_stage(name: str, content: str) -> "dict | None":`
  (that is, directly after `_parse_archon_cost_with_capture`'s final `return`), leaving two blank
  lines on each side:

```python
# --- Per-node cost capture (#453) -------------------------------------------------
# The workflow YAML Archon actually executes: the clone's own copy, else the baked one
# (entrypoint.sh copies the baked workflows into a target clone that has none).
WORKFLOW_YAML_REL = pathlib.Path(".archon") / "workflows" / "archon-dark-factory.yaml"
BAKED_WORKFLOW_PATH = pathlib.Path("/opt/dark-factory/workflows/archon-dark-factory.yaml")
_NODE_TYPE_KEYS = ("command", "prompt", "bash")


def _load_workflow_node_maps(clone_dir) -> "tuple[dict, dict] | None":
    """(node_id -> "command"|"prompt"|"bash", log token -> node_id), or None.

    Archon names a command: node by its command: value in its log lines
    (`[dark-factory-implement] Started` for id `implement`) and every other node by its
    id:, so both are log tokens. A token two nodes claim is dropped as ambiguous. None
    when neither the clone's nor the baked workflow YAML loads — node types are then
    unknown and nothing may be classified (an unknown type is never treated as bash).
    """
    try:
        import yaml
    except ImportError:
        return None
    for path in (pathlib.Path(clone_dir or ".") / WORKFLOW_YAML_REL, BAKED_WORKFLOW_PATH):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        wf_nodes = doc.get("nodes") if isinstance(doc, dict) else None
        if not isinstance(wf_nodes, list):
            continue
        node_types: dict = {}
        log_tokens: dict = {}
        ambiguous: set = set()
        for n in wf_nodes:
            if not isinstance(n, dict) or not isinstance(n.get("id"), str):
                continue
            node_id = n["id"]
            kind = next((k for k in _NODE_TYPE_KEYS if k in n), None)
            if kind:
                node_types[node_id] = kind
            names = [node_id]
            if isinstance(n.get("command"), str):
                names.append(n["command"])
            for name in names:
                if log_tokens.get(name, node_id) != node_id:
                    ambiguous.add(name)
                log_tokens[name] = node_id
        for name in ambiguous:
            log_tokens.pop(name, None)
        if node_types:
            return node_types, log_tokens
    return None
```

  Then harden the existing autouse fixture `_hermetic_scheduler_state_dir` in
  `tests/test_run_record.py`. Replace its single body line
  `    monkeypatch.setattr(rr, "SCHEDULER_STATE_DIR", tmp_path / "scheduler-state")` (the one
  directly after its docstring) with:

```python
    monkeypatch.setattr(rr, "SCHEDULER_STATE_DIR", tmp_path / "scheduler-state")
    # #453: cmd_assemble now emits health events and falls back to the baked workflow
    # YAML — keep both off the real Seq endpoint and the image's /opt copy.
    monkeypatch.setattr(rr, "SEQ_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(rr, "BAKED_WORKFLOW_PATH", tmp_path / "no-baked-workflow.yaml")
```

  (`test_post_seq_is_nonfatal` sets its own `SEQ_URL` afterwards and still wins. The direct
  `emit_health_event`/`cmd_health_event` tests patch `_post_seq_raw` themselves and are unaffected.)

- [ ] **Step 4 — Verify pass.**
  `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q`
  Expected: `147 passed` (142 existing + 5 new).

- [ ] **Step 5 — Commit.**
  `git add scripts/factory_core/run_record.py tests/test_run_record.py && git commit -m "feat(run-record #453): load workflow node types and command-name log tokens"`

---

## Task 2: Archon node-log parser

**Files:** `tests/test_run_record.py`, `scripts/factory_core/run_record.py`

- [ ] **Step 1 — Write failing tests.** Append:

```python


# --- _parse_node_log ---

_TOKENS = {"parse-intent": "parse-intent", "dark-factory-implement": "implement",
           "implement": "implement", "digest-comments": "digest-comments",
           "dark-factory-validate": "validate", "validate": "validate"}


def test_parse_node_log_maps_command_names_and_ignores_noise(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(_LOG_388)
    events = rr._parse_node_log(str(log), _TOKENS)
    assert events == {
        "parse-intent": "Completed",
        "digest-comments": "Skipped",
        "implement": "Completed",
        "validate": "Completed",
    }
    assert list(events) == ["parse-intent", "digest-comments", "implement", "validate"]


def test_parse_node_log_reads_failed_with_colon(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("[dark-factory-implement] Started\n"
                   "[dark-factory-implement] Failed: blast radius HUMAN_REQUIRED\n")
    assert rr._parse_node_log(str(log), _TOKENS) == {"implement": "Failed"}


def test_parse_node_log_none_when_unusable(tmp_path):
    assert rr._parse_node_log(None, _TOKENS) is None
    assert rr._parse_node_log("", _TOKENS) is None
    assert rr._parse_node_log(str(tmp_path / "absent.log"), _TOKENS) is None
    noise = tmp_path / "noise.log"
    noise.write_text("hello\n  [dark-factory-implement] Started\n[unknown] Started\n")
    assert rr._parse_node_log(str(noise), _TOKENS) is None
```

  (`_TOKENS` leaves out `fetch-issue`, `classify-preview` and `conformance`, so the parser must drop
  their lines too. The mid-line `[dark-factory-plan]` and `[not-a-node]` lines must not match.)

- [ ] **Step 2 — Verify fail.**
  `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q -k parse_node_log`
  Expected: `3 failed`: `AttributeError: ... has no attribute '_parse_node_log'`.

- [ ] **Step 3 — Implement.** In `run_record.py`, add the regex directly under the
  `_NODE_TYPE_KEYS = ...` line from Task 1:

```python
# Archon's `archon workflow run` progress line: `[<command ?? id>] Started|Completed (..)|
# Failed: ..|Skipped (..)`. Anchored at line start; the token must be a known name.
_NODE_LOG_RE = re.compile(r"^\[([A-Za-z0-9_-]+)\]\s+(Started|Completed|Failed|Skipped)\b")
```

  Then add this function directly after `_load_workflow_node_maps`:

```python
def _parse_node_log(log_file, log_tokens: dict) -> "dict | None":
    """node_id -> last Started/Completed/Failed/Skipped event from the tee'd
    `archon workflow run` output ($TMP_OUT), in first-seen order. None when the file is
    absent/unreadable or no line names a known node."""
    if not log_file:
        return None
    try:
        text = pathlib.Path(log_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    events: dict = {}
    for line in text.splitlines():
        m = _NODE_LOG_RE.match(line)
        if m and m.group(1) in log_tokens:
            events[log_tokens[m.group(1)]] = m.group(2)
    return events or None
```

- [ ] **Step 4 — Verify pass.**
  `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q`
  Expected: `150 passed`.

- [ ] **Step 5 — Commit.**
  `git add scripts/factory_core/run_record.py tests/test_run_record.py && git commit -m "feat(run-record #453): parse Archon per-node log lines by command name"`

---

## Task 3: Per-node cost-capture classifier

**Files:** `tests/test_run_record.py`, `scripts/factory_core/run_record.py`

- [ ] **Step 1 — Write failing tests.** Append:

```python


# --- _classify_node_capture ---

_TYPES = {"parse-intent": "prompt", "fetch-issue": "bash", "digest-comments": "bash",
          "implement": "command", "validate": "command", "push-and-pr": "bash"}


def _rec_node(node_id, tin=0, tout=0, cost=0, ms=1000):
    return {"node_id": node_id, "model": "", "gen_ai.usage.input_tokens": tin,
            "gen_ai.usage.output_tokens": tout, "cost_usd": cost, "duration_ms": ms}


def test_classify_bash_zero_stays_completed():
    nodes, missing = rr._classify_node_capture([_rec_node("fetch-issue")], _TYPES, None)
    assert nodes[0]["cost_capture"] == "completed"
    assert nodes[0]["cost_usd"] == 0
    assert missing == []


def test_classify_ai_node_with_usage_is_completed():
    nodes, missing = rr._classify_node_capture(
        [_rec_node("implement", 10, 20, 0.5)], _TYPES, None)
    assert nodes[0]["cost_capture"] == "completed"
    assert "reason" not in nodes[0]
    assert missing == []


def test_classify_zero_command_node_is_missing_no_usage_reported():
    # The #388 shape: present in the cost report, real duration, all-zero usage.
    nodes, missing = rr._classify_node_capture(
        [_rec_node("implement", ms=1_555_000)], _TYPES, None)
    node = nodes[0]
    assert node["cost_capture"] == "missing"
    assert node["reason"] == "no_usage_reported"
    assert node["cost_usd"] is None
    assert node["gen_ai.usage.input_tokens"] is None
    assert node["gen_ai.usage.output_tokens"] is None
    assert node["duration_ms"] == 1_555_000
    assert missing == [{"node_id": "implement", "reason": "no_usage_reported"}]


def test_classify_zero_prompt_node_is_missing():
    nodes, _ = rr._classify_node_capture([_rec_node("parse-intent")], _TYPES, None)
    assert nodes[0]["cost_capture"] == "missing"


def test_classify_node_not_in_workflow_is_not_flagged():
    nodes, missing = rr._classify_node_capture([_rec_node("target-loop-x")], _TYPES, None)
    assert nodes[0]["cost_capture"] == "completed"
    assert missing == []


@pytest.mark.parametrize("event", ["Started", "Completed", "Failed"])
def test_classify_ran_but_absent_node_is_missing_not_in_cost_report(event):
    nodes, missing = rr._classify_node_capture(
        [_rec_node("parse-intent", 1, 1, 0.01)], _TYPES,
        {"parse-intent": "Completed", "validate": event})
    assert [n["node_id"] for n in nodes] == ["parse-intent", "validate"]
    added = nodes[1]
    assert added["cost_capture"] == "missing"
    assert added["reason"] == "not_in_cost_report"
    assert added["cost_usd"] is None
    assert added["duration_ms"] is None
    assert missing == [{"node_id": "validate", "reason": "not_in_cost_report"}]


def test_classify_skipped_node_is_omitted():
    nodes, missing = rr._classify_node_capture(
        [_rec_node("parse-intent", 1, 1, 0.01)], _TYPES, {"digest-comments": "Skipped"})
    assert [n["node_id"] for n in nodes] == ["parse-intent"]
    assert missing == []


def test_classify_failed_bash_node_absent_from_report_is_not_missing():
    nodes, missing = rr._classify_node_capture(
        [_rec_node("parse-intent", 1, 1, 0.01)], _TYPES, {"push-and-pr": "Failed"})
    assert [n["node_id"] for n in nodes] == ["parse-intent"]
    assert missing == []


def test_classify_does_not_mutate_input_nodes():
    original = [_rec_node("implement")]
    rr._classify_node_capture(original, _TYPES, None)
    assert original == [_rec_node("implement")]
```

- [ ] **Step 2 — Verify fail.**
  `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q -k classify`
  Expected: `11 failed`: `AttributeError: ... has no attribute '_classify_node_capture'`.

- [ ] **Step 3 — Implement.** Add `_AI_NODE_TYPES = ("command", "prompt")` on the line after
  `_NODE_TYPE_KEYS = ...`. Then add directly after `_parse_node_log`:

```python
def _classify_node_capture(nodes: list, node_types: dict, log_events: "dict | None") -> "tuple[list, list]":
    """Mark each node cost_capture completed/missing (#453). Returns (nodes, missing).

    A reported command:/prompt: node with all-zero usage lost its usage in Archon: it is
    missing/no_usage_reported with null cost/tokens (duration kept). A bash: zero is real.
    With a parsed log, a command:/prompt: node that started but is absent from the cost
    report is appended as missing/not_in_cost_report; a Skipped node never ran and is not
    emitted, and an absent bash: node spent nothing and is not emitted either.
    """
    out: list = []
    missing: list = []
    for node in nodes:
        node = dict(node)
        zero = not any(
            node.get(k)
            for k in ("cost_usd", "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens")
        )
        if node_types.get(node.get("node_id")) in _AI_NODE_TYPES and zero:
            node.update({
                "gen_ai.usage.input_tokens": None,
                "gen_ai.usage.output_tokens": None,
                "cost_usd": None,
                "cost_capture": "missing",
                "reason": "no_usage_reported",
            })
            missing.append({"node_id": node.get("node_id", ""), "reason": "no_usage_reported"})
        else:
            node["cost_capture"] = "completed"
        out.append(node)
    reported = {n.get("node_id") for n in nodes}
    for node_id, event in (log_events or {}).items():
        if node_id in reported or event == "Skipped" or node_types.get(node_id) not in _AI_NODE_TYPES:
            continue
        out.append({
            "node_id": node_id,
            "model": "",
            "gen_ai.usage.input_tokens": None,
            "gen_ai.usage.output_tokens": None,
            "cost_usd": None,
            "duration_ms": None,
            "cost_capture": "missing",
            "reason": "not_in_cost_report",
        })
        missing.append({"node_id": node_id, "reason": "not_in_cost_report"})
    return out, missing
```

  Plan decision (spec is silent): a reported node whose id is **not in the workflow YAML** (e.g. a
  target-loop node) has no known AI type. Requirement 1 flags only `command:`/`prompt:` types, so it
  stays `completed`. A node with no evidence of an AI type is never flagged missing.

- [ ] **Step 4 — Verify pass.**
  `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q`
  Expected: `161 passed`.

- [ ] **Step 5 — Commit.**
  `git add scripts/factory_core/run_record.py tests/test_run_record.py && git commit -m "feat(run-record #453): classify zero-usage AI nodes and unreported started nodes as cost-missing"`

---

## Task 4: Wire classification into `cmd_assemble` (totals, health events, `--archon-log-file`)

**Files:** `tests/test_run_record.py`, `scripts/factory_core/run_record.py`

- [ ] **Step 1 — Write failing tests.** Append:

```python


# --- cmd_assemble integration ---

def test_assemble_388_shape_marks_implement_missing(tmp_path, monkeypatch, health_events):
    rec = _assemble_with_capture(tmp_path, monkeypatch, _COST_388, log_text=_LOG_388,
                                 status="failed")
    nodes = _by_id(rec)
    assert list(nodes) == ["parse-intent", "fetch-issue", "implement",
                           "classify-preview", "validate", "conformance"]
    assert nodes["implement"]["cost_capture"] == "missing"
    assert nodes["implement"]["reason"] == "no_usage_reported"
    assert nodes["implement"]["cost_usd"] is None
    assert nodes["implement"]["duration_ms"] == 1_555_000
    assert nodes["fetch-issue"]["cost_capture"] == "completed"
    totals = rec["totals"]
    assert totals["cost_capture"] == "partial"
    assert totals["node_log"] == "parsed"
    assert totals["cost_usd"] is None
    assert totals["gen_ai.usage.input_tokens"] is None
    assert totals["gen_ai.usage.output_tokens"] is None
    assert totals["known_cost_usd"] == pytest.approx(1.89)
    assert totals["known_input_tokens"] == 112
    assert totals["known_output_tokens"] == 27697
    assert totals["missing_nodes"] == [{"node_id": "implement", "reason": "no_usage_reported"}]
    assert health_events == [
        ("factory.cost_report.node_missing", {"node_id": "implement", "reason": "no_usage_reported"}),
    ]


def test_assemble_healthy_run_stays_complete(tmp_path, monkeypatch, health_events):
    rec = _assemble_with_capture(tmp_path, monkeypatch, _COST_HEALTHY, log_text=_LOG_HEALTHY)
    totals = rec["totals"]
    assert totals["cost_capture"] == "complete"
    assert totals["node_log"] == "parsed"
    assert totals["missing_nodes"] == []
    assert totals["cost_usd"] == 0.0207 + 0 + 4.1  # same sum, same order as before #453
    assert totals["gen_ai.usage.input_tokens"] == 320
    assert totals["gen_ai.usage.output_tokens"] == 52270
    assert totals["known_cost_usd"] == totals["cost_usd"]
    assert [n["cost_capture"] for n in rec["nodes"]] == ["completed"] * 3
    assert health_events == []


def test_assemble_started_but_unreported_node_is_missing(tmp_path, monkeypatch, health_events):
    log = _LOG_HEALTHY + "[dark-factory-validate] Started\n"
    rec = _assemble_with_capture(tmp_path, monkeypatch, _COST_HEALTHY, log_text=log,
                                 status="failed")
    validate = _by_id(rec)["validate"]
    assert validate["cost_capture"] == "missing"
    assert validate["reason"] == "not_in_cost_report"
    assert rec["totals"]["cost_capture"] == "partial"
    assert rec["totals"]["known_cost_usd"] == pytest.approx(4.1207)
    assert ("factory.cost_report.node_missing",
            {"node_id": "validate", "reason": "not_in_cost_report"}) in health_events


def test_assemble_failed_node_absent_from_report_is_missing(tmp_path, monkeypatch, health_events):
    cost = {"runId": "wff", "nodes": [_cost_node("parse-intent", 20, 297, 0.0097, 5200)]}
    log = ("[parse-intent] Started\n[parse-intent] Completed (5.2s)\n"
           "[dark-factory-implement] Started\n"
           "[dark-factory-implement] Failed: blast radius HUMAN_REQUIRED\n")
    rec = _assemble_with_capture(tmp_path, monkeypatch, cost, log_text=log, status="failed")
    assert _by_id(rec)["implement"]["reason"] == "not_in_cost_report"
    assert rec["totals"]["missing_nodes"] == [{"node_id": "implement", "reason": "not_in_cost_report"}]


@pytest.mark.parametrize("log_text", [None, "", "no node lines here\n"])
def test_assemble_without_usable_log_synthesizes_nothing(tmp_path, monkeypatch, health_events, log_text):
    rec = _assemble_with_capture(tmp_path, monkeypatch, _COST_388, log_text=log_text,
                                 status="failed")
    assert "validate" in _by_id(rec)  # reported nodes only — nothing appended
    assert len(rec["nodes"]) == len(_COST_388["nodes"])
    assert rec["totals"]["node_log"] == "unavailable"
    # Requirement 1 still applies without a log.
    assert _by_id(rec)["implement"]["reason"] == "no_usage_reported"
    assert [e for e, _ in health_events] == [
        "factory.cost_report.node_log_unavailable",
        "factory.cost_report.node_missing",
    ]


def test_assemble_empty_log_path_from_on_failure_is_tolerated(tmp_path, monkeypatch, health_events):
    # on_failure passes "${TMP_OUT:-}" — empty when it fires before the workflow loop.
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")
    cost_json = tmp_path / "cost.json"
    cost_json.write_text(json.dumps(_COST_HEALTHY))
    args = _AssembleArgs(tmp_path, tmp_path / "run-record.json")
    args.archon_cost_json = str(cost_json)
    args.clone_dir = str(_clone_with_workflow(tmp_path))
    args.archon_log_file = ""
    rr.cmd_assemble(args)
    rec = json.loads((tmp_path / "run-record.json").read_text())
    assert rec["totals"]["node_log"] == "unavailable"
    assert rec["totals"]["cost_capture"] == "complete"


def test_assemble_capture_failure_skips_classification(tmp_path, monkeypatch, health_events):
    rec = _assemble_with_capture(tmp_path, monkeypatch, _COST_388, log_text=_LOG_388,
                                 status="failed", exit_code=1)
    assert rec["nodes"] == []
    assert rec["totals"]["cost_capture"] == "unavailable"
    assert rec["totals"]["cost_usd"] is None
    assert rec["totals"]["known_cost_usd"] is None
    assert health_events == []


def test_assemble_unloadable_workflow_classifies_nothing(tmp_path, monkeypatch, health_events):
    (tmp_path / "bare-clone").mkdir()
    rec = _assemble_with_capture(tmp_path, monkeypatch, _COST_388, log_text=_LOG_388,
                                 clone=tmp_path / "bare-clone", status="failed")
    assert all("cost_capture" not in n for n in rec["nodes"])
    assert _by_id(rec)["implement"]["cost_usd"] == 0  # never guessed to be bash or missing
    totals = rec["totals"]
    assert totals["cost_capture"] == "unknown"
    assert totals["cost_usd"] == pytest.approx(1.89)  # today's value, unchanged
    assert totals["gen_ai.usage.input_tokens"] == 112
    assert totals["known_cost_usd"] is None
    assert [e for e, _ in health_events] == ["factory.cost_report.node_types_unavailable"]


def test_assemble_cli_accepts_archon_log_file(monkeypatch):
    seen = {}
    monkeypatch.setattr(rr, "cmd_assemble", lambda a: seen.update(vars(a)))
    monkeypatch.setattr(sys, "argv", [
        "run_record", "assemble", "--run-id", "r1", "--issue", "1", "--intent", "fix",
        "--artifacts-dir", "/tmp/a", "--out-file", "/tmp/a/run-record.json",
        "--archon-log-file", "/tmp/a/archon-run.log",
    ])
    rr.main()
    assert seen["archon_log_file"] == "/tmp/a/archon-run.log"
```

- [ ] **Step 2 — Verify fail.**
  `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q -k "assemble_388 or assemble_healthy or assemble_started or assemble_failed_node or assemble_without_usable or assemble_empty_log or assemble_capture_failure_skips or assemble_unloadable or assemble_cli_accepts"`
  Expected: `11 failed`. The ten integration cases fail with `KeyError` (`'cost_capture'`,
  `'node_log'`, or `'implement'` for the not-yet-appended node), because `cmd_assemble` doesn't
  classify yet. The CLI test fails with `SystemExit: 2`
  (`unrecognized arguments: --archon-log-file`).

- [ ] **Step 3 — Implement.** In `run_record.py`:

  (a) Add directly after `_classify_node_capture`:

```python
def _sum_usage(nodes: list) -> "tuple":
    return (
        sum(n.get("gen_ai.usage.input_tokens", 0) for n in nodes),
        sum(n.get("gen_ai.usage.output_tokens", 0) for n in nodes),
        sum(n.get("cost_usd", 0) for n in nodes),
    )


def _capture_totals(nodes: list, *, capture_ok: bool, clone_dir, archon_log_file,
                    issue: int, run_id: str) -> "tuple[list, dict]":
    """Classify per-node cost capture and build run-record totals (#453).

    totals.cost_usd/token totals keep their fully-measured meaning: null when capture
    failed (df#300) or is partial. known_* sum only completed nodes. Emits one
    non-blocking health event per missing node, plus one when the workflow YAML or the
    node log is unavailable.
    """
    totals = {
        "gen_ai.usage.input_tokens": None,
        "gen_ai.usage.output_tokens": None,
        "cost_usd": None,
        "cost_capture": "unavailable",
        "node_log": "unavailable",
        "known_cost_usd": None,
        "known_input_tokens": None,
        "known_output_tokens": None,
        "missing_nodes": [],
    }
    if not capture_ok:
        return nodes, totals

    maps = _load_workflow_node_maps(clone_dir)
    if maps is None:
        emit_health_event(
            "factory.cost_report.node_types_unavailable", issue, run_id,
            {"clone_dir": str(clone_dir)},
        )
        tin, tout, cost = _sum_usage(nodes)
        totals.update({
            "gen_ai.usage.input_tokens": tin,
            "gen_ai.usage.output_tokens": tout,
            "cost_usd": cost,
            "cost_capture": "unknown",
        })
        return nodes, totals

    node_types, log_tokens = maps
    log_events = _parse_node_log(archon_log_file, log_tokens)
    if log_events is None:
        emit_health_event(
            "factory.cost_report.node_log_unavailable", issue, run_id,
            {"archon_log_file": str(archon_log_file or "")},
        )
    nodes, missing = _classify_node_capture(nodes, node_types, log_events)
    for entry in missing:
        emit_health_event("factory.cost_report.node_missing", issue, run_id, dict(entry))

    known_in, known_out, known_cost = _sum_usage(
        [n for n in nodes if n.get("cost_capture") == "completed"]
    )
    complete = not missing
    totals.update({
        "gen_ai.usage.input_tokens": known_in if complete else None,
        "gen_ai.usage.output_tokens": known_out if complete else None,
        "cost_usd": known_cost if complete else None,
        "cost_capture": "complete" if complete else "partial",
        "node_log": "unavailable" if log_events is None else "parsed",
        "known_cost_usd": known_cost,
        "known_input_tokens": known_in,
        "known_output_tokens": known_out,
        "missing_nodes": missing,
    })
    return nodes, totals
```

  `emit_health_event` is defined earlier in the module, so the module-level name resolves at call
  time (and the tests' `monkeypatch.setattr(rr, "emit_health_event", ...)` intercepts it).

  (b) In `cmd_assemble`, replace the whole existing block that starts
  `    if archon_cost_capture.get("ok"):` and ends with `        totals_cost = None`. That block
  is the `totals_in/totals_out/totals_cost` sum-or-null `if/else` with its two comments. Replace it
  with:

```python
    # Capture failed -> totals null, not zero (df#300); otherwise classify every node's
    # cost capture (#453) — a measured zero (no AI nodes) still totals 0.
    nodes, totals = _capture_totals(
        nodes,
        capture_ok=bool(archon_cost_capture.get("ok")),
        clone_dir=getattr(args, "clone_dir", None) or ".",
        archon_log_file=getattr(args, "archon_log_file", None),
        issue=args.issue,
        run_id=args.run_id,
    )
```

  (c) In the `run_record = {...}` literal in `cmd_assemble`, replace

```python
        "totals": {
            "gen_ai.usage.input_tokens": totals_in,
            "gen_ai.usage.output_tokens": totals_out,
            "cost_usd": totals_cost,
        },
```

  with `        "totals": totals,`.

  (d) In `main()`, directly after `    a.add_argument("--archon-cost-stderr-file")`, add:

```python
    a.add_argument("--archon-log-file", default=None,
                   help="tee'd `archon workflow run` output, for per-node status (#453)")
```

  Existing key order is preserved: `totals` still opens with `gen_ai.usage.input_tokens`,
  `gen_ai.usage.output_tokens`, `cost_usd`, then the new keys. On a complete run
  `cost_usd`/token values are the same sums in the same node order as before (Requirement 6's
  "byte-identical" on `totals.cost_usd`). The "unknown" path sums every node exactly as today's
  code did.

- [ ] **Step 4 — Verify pass.**
  `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q`
  Expected: `172 passed` (all pre-existing assemble tests included — they never use zero-usage AI
  nodes).

- [ ] **Step 5 — Commit.**
  `git add scripts/factory_core/run_record.py tests/test_run_record.py && git commit -m "feat(run-record #453): capture-aware totals, missing-node health events, --archon-log-file"`

---

## Task 5: Regression guards — outcome scoring and reconcile untouched

**Files:** `tests/test_run_record.py`, `tests/test_reconcile_cost_reports.py`

These add guard tests only, with no production code change. They are expected to **pass on first
run**. If either fails, that is a real regression from Task 4: fix the code, not the test.

- [ ] **Step 1 — Write tests.** Append to `tests/test_run_record.py`:

```python


def test_partial_capture_leaves_outcome_scoring_untouched(tmp_path, monkeypatch, health_events):
    # #447/#453: outcome.state/score read stages + status only; a partial token sum must
    # never feed factory_cpm or tokens_per_task.
    partial = _assemble_with_capture(tmp_path / "partial", monkeypatch, _COST_388,
                                     log_text=_LOG_388)
    healthy = _assemble_with_capture(tmp_path / "healthy", monkeypatch, _COST_HEALTHY,
                                     log_text=_LOG_HEALTHY)
    he = partial["harness_economics"]
    assert he["outcome"] == healthy["harness_economics"]["outcome"]
    assert he["outcome"]["state"] == "produced_ungated"
    assert he["factory_cpm"] is None
    assert he["tokens_per_task"] is None
    assert he["cost_per_task"] is None
    assert healthy["harness_economics"]["factory_cpm"] is not None
```

  Append to `tests/test_reconcile_cost_reports.py`:

```python


def test_partial_capture_run_reads_null_cost_like_failed_capture(tmp_path):
    # #453: a partial run's totals.cost_usd is null exactly like an
    # archon_cost_capture.ok == false run's — no reconcile change needed.
    state_dir = tmp_path / "state"
    (state_dir / "run-records").mkdir(parents=True)
    (state_dir / "run-records" / "run-p.json").write_text(json.dumps({
        "run_id": "run-p", "issue_number": 388, "status": "failed",
        "nodes": [{"node_id": "implement", "cost_usd": None, "cost_capture": "missing",
                   "reason": "no_usage_reported"}],
        "totals": {"cost_usd": None, "cost_capture": "partial", "known_cost_usd": 1.89,
                   "missing_nodes": [{"node_id": "implement", "reason": "no_usage_reported"}]},
    }))
    (state_dir / "run-records" / "run-u.json").write_text(json.dumps({
        "run_id": "run-u", "issue_number": 388, "status": "failed",
        "nodes": [], "archon_cost_capture": {"ok": False},
        "totals": {"cost_usd": None},
    }))

    report = rcr.build_reconciliation_report(state_dir=state_dir)

    by_id = {r["run_id"]: r for r in report["recoverable"]}
    assert by_id["run-p"]["cost_usd"] is None
    assert by_id["run-p"]["cost_usd"] == by_id["run-u"]["cost_usd"]
```

- [ ] **Step 2 — Run.**
  `PYTHONPATH=scripts python -m pytest tests/test_run_record.py tests/test_reconcile_cost_reports.py -q`
  Expected: `179 passed` (173 + 6).

- [ ] **Step 3 — Commit.**
  `git add tests/test_run_record.py tests/test_reconcile_cost_reports.py && git commit -m "test(run-record #453): partial capture leaves scoring and reconcile unchanged"`

---

## Task 6: Cost-report rendering of missing nodes and partial runs

**Files:** `tests/test_cost_report.py`, `scripts/factory_core/cost_report.py`

- [ ] **Step 1 — Write failing tests.** Append to `tests/test_cost_report.py`:

```python


# ---------------------------------------------------------------------------
# cost-missing nodes and partial capture (#453)
# ---------------------------------------------------------------------------

def test_format_node_row_completed_node_unchanged():
    node = {"node_id": "parse-intent", "model": "", "gen_ai.usage.input_tokens": 20,
            "gen_ai.usage.output_tokens": 270, "cost_usd": 0.0207, "duration_ms": 7800,
            "cost_capture": "completed"}
    assert cr.format_node_row(node) == "| parse-intent |  | 20 | 270 | $0.0207 | 7.8s |"


def test_format_node_row_missing_node_never_renders_zero():
    node = {"node_id": "implement", "model": "", "gen_ai.usage.input_tokens": None,
            "gen_ai.usage.output_tokens": None, "cost_usd": None, "duration_ms": 1_555_000,
            "cost_capture": "missing", "reason": "no_usage_reported"}
    assert cr.format_node_row(node) == (
        "| implement |  | — | — | missing (no_usage_reported) | 25m 55s |"
    )


def test_format_node_row_unreported_node_without_duration():
    node = {"node_id": "validate", "model": "", "gen_ai.usage.input_tokens": None,
            "gen_ai.usage.output_tokens": None, "cost_usd": None, "duration_ms": None,
            "cost_capture": "missing", "reason": "not_in_cost_report"}
    assert cr.format_node_row(node) == (
        "| validate |  | — | — | missing (not_in_cost_report) | — |"
    )


def test_format_partial_capture_note_only_for_partial():
    assert cr.format_partial_capture_note({}) == ""
    assert cr.format_partial_capture_note({"cost_capture": "complete"}) == ""
    assert cr.format_partial_capture_note({"cost_capture": "unknown"}) == ""
    two = cr.format_partial_capture_note({
        "cost_capture": "partial", "known_cost_usd": 0.5, "known_input_tokens": 10,
        "known_output_tokens": 2000,
        "missing_nodes": [{"node_id": "implement", "reason": "no_usage_reported"},
                          {"node_id": "validate", "reason": "not_in_cost_report"}],
    })
    assert two == (
        "> ⚠️ Partial cost capture — 2 nodes unreported (implement: no_usage_reported, "
        "validate: not_in_cost_report). Known spend: $0.5 (10 in / 2.0K out); "
        "true total is higher."
    )


def test_render_complete_run_with_453_fields_matches_golden_a():
    # Byte-identical guard: the new per-node/totals fields change nothing on a
    # complete run.
    record = json.loads(json.dumps(RUN_RECORD_A))
    record["totals"].update({
        "cost_capture": "complete", "node_log": "parsed", "known_cost_usd": 0.0207,
        "known_input_tokens": 20, "known_output_tokens": 270, "missing_nodes": [],
    })
    for node in record["nodes"]:
        node["cost_capture"] = "completed"
    body = cr.render(record, prior_comment_body="", timestamp="2026-07-22 13:02 UTC",
                     intent="fix")
    assert body == GOLDEN_A_MULTI_NODE_FIRST_RUN


RUN_RECORD_PARTIAL_388 = {
    "status": "failed",
    "totals": {
        "gen_ai.usage.input_tokens": None, "gen_ai.usage.output_tokens": None,
        "cost_usd": None, "cost_capture": "partial", "node_log": "parsed",
        "known_cost_usd": 1.89, "known_input_tokens": 112, "known_output_tokens": 27400,
        "missing_nodes": [{"node_id": "implement", "reason": "no_usage_reported"}],
    },
    "nodes": [
        {"node_id": "parse-intent", "model": "", "gen_ai.usage.input_tokens": 20,
         "gen_ai.usage.output_tokens": 297, "cost_usd": 0.0097, "duration_ms": 5200,
         "cost_capture": "completed"},
        {"node_id": "implement", "model": "", "gen_ai.usage.input_tokens": None,
         "gen_ai.usage.output_tokens": None, "cost_usd": None, "duration_ms": 1_555_000,
         "cost_capture": "missing", "reason": "no_usage_reported"},
        {"node_id": "conformance", "model": "", "gen_ai.usage.input_tokens": 92,
         "gen_ai.usage.output_tokens": 27103, "cost_usd": 1.8803, "duration_ms": 300_000,
         "cost_capture": "completed"},
    ],
    "harness_economics": {"factory_cpm": None, "outcome": {"state": "failed", "score": 0.0}},
    "archon_cost_capture": {"ok": True},
}

GOLDEN_PARTIAL_388 = (
    "<!-- dark-factory-cost-report -->\n"
    "<!-- cumulative: cost=1.89 in=112 out=27400 -->\n"
    "## Dark Factory — Cost Report\n\n"
    "**1 run(s) — Total: $1.89 (112 in / 27.4K out)**\n\n\n"
    "### Run: 2026-09-26 17:28 UTC (fix, failed)\n\n"
    "| Step | Model | In tokens | Out tokens | Cost | Duration |\n"
    "|------|-------|-----------|------------|------|----------|\n"
    "| parse-intent |  | 20 | 297 | $0.0097 | 5.2s |\n"
    "| implement |  | — | — | missing (no_usage_reported) | 25m 55s |\n"
    "| conformance |  | 92 | 27.1K | $1.8803 | 5m 0s |\n"
    "| **Subtotal** | | **112** | **27.4K** | **$1.89** | |\n\n"
    "> ⚠️ Partial cost capture — 1 node unreported (implement: no_usage_reported). "
    "Known spend: $1.89 (112 in / 27.4K out); true total is higher.\n\n"
    "**Factory CPM:** n/a | **Outcome:** failed (score 0.0)\n\n"
    "---\n"
    "*Updated by Dark Factory Dark Factory*"
)


def test_render_partial_run_uses_known_totals_and_note():
    body = cr.render(RUN_RECORD_PARTIAL_388, prior_comment_body="",
                     timestamp="2026-09-26 17:28 UTC", intent="fix")
    assert body == GOLDEN_PARTIAL_388


def test_partial_run_cumulative_is_a_lower_bound_never_zero():
    parsed = cr.parse_prior_cumulative(GOLDEN_PARTIAL_388)
    assert (parsed["prev_cost"], parsed["prev_in"], parsed["prev_out"]) == ("1.89", 112, 27400)
    assert parsed["run_count"] == 1
    assert "missing (no_usage_reported)" in parsed["prior_runs"]
    # The next run's header total adds onto the partial run's known spend.
    nxt = cr.render(RUN_RECORD_A, prior_comment_body=GOLDEN_PARTIAL_388,
                    timestamp="2026-09-27 09:00 UTC", intent="fix")
    assert "<!-- cumulative: cost=1.9107 in=132 out=27670 -->" in nxt
    assert "**2 run(s) — Total: $1.9107" in nxt
```

  The "—" in token cells is U+2014 EM DASH, the same character the existing header
  `## Dark Factory — Cost Report` uses. It is a plan decision for the spec's "nulled token
  cells": a visible non-number, never `0`.

- [ ] **Step 2 — Verify fail.**
  `PYTHONPATH=scripts python -m pytest tests/test_cost_report.py -q`
  Expected: `5 failed, 56 passed` (measured against unmodified `cost_report.py`). The four
  `format_node_row`/`format_partial_capture_note` tests give `AttributeError`.
  `test_render_partial_run_uses_known_totals_and_note` fails because `render` prints the missing
  row as `| 0 | 0 | $0 |` and the Subtotal as `$0`. Two of the new tests pass already, which is
  expected: they are guards that must *stay* green through Step 3.
  `test_render_complete_run_with_453_fields_matches_golden_a` is the complete-run byte-identical
  guard. `test_partial_run_cumulative_is_a_lower_bound_never_zero` parses the golden literal with
  the existing parser.

- [ ] **Step 3 — Implement.** In `scripts/factory_core/cost_report.py`:

  (a) Directly after the line `COST_MARKER = "<!-- dark-factory-cost-report -->"`, add:

```python

# Token/duration cell for a node whose usage Archon did not report (#453) — never "0".
MISSING_CELL = "—"


def format_node_row(node: dict) -> str:
    """One per-node table row. A cost-missing node (#453) shows its reason in the cost
    cell and MISSING_CELL for its token cells (and for a duration it never reported),
    never a misleading $0/0/0; every other node renders exactly as before."""
    if node.get("cost_capture") == "missing":
        duration_ms = node.get("duration_ms")
        duration = MISSING_CELL if duration_ms is None else format_duration(duration_ms)
        return (
            f"| {node.get('node_id', '')} | {node.get('model') or ''} | "
            f"{MISSING_CELL} | {MISSING_CELL} | "
            f"missing ({node.get('reason') or 'unknown'}) | {duration} |"
        )
    return (
        f"| {node.get('node_id', '')} | {node.get('model') or ''} | "
        f"{format_tokens_table(node.get('gen_ai.usage.input_tokens', 0) or 0)} | "
        f"{format_tokens_table(node.get('gen_ai.usage.output_tokens', 0) or 0)} | "
        f"{format_cost(node.get('cost_usd', 0) or 0)} | "
        f"{format_duration(node.get('duration_ms', 0) or 0)} |"
    )


def format_partial_capture_note(totals: dict) -> str:
    """Partial-capture warning line under the Subtotal row (#453); "" unless
    totals.cost_capture == "partial", so a complete run's body is unchanged."""
    if totals.get("cost_capture") != "partial":
        return ""
    missing = totals.get("missing_nodes") or []
    noun = "node" if len(missing) == 1 else "nodes"
    listed = ", ".join(f"{m.get('node_id', '')}: {m.get('reason', '')}" for m in missing)
    return (
        f"> ⚠️ Partial cost capture — {len(missing)} {noun} unreported ({listed}). "
        f"Known spend: {format_cost(totals.get('known_cost_usd') or 0)} "
        f"({format_tokens_cumulative(totals.get('known_input_tokens') or 0)} in / "
        f"{format_tokens_cumulative(totals.get('known_output_tokens') or 0)} out); "
        f"true total is higher."
    )
```

  (b) In `render`, replace these six lines:

```python
    total_cost_raw = totals.get("cost_usd")
    if total_cost_raw is None:
        total_cost_raw = 0
    total_cost_str = _passthrough_num(total_cost_raw)
    total_in = totals.get("gen_ai.usage.input_tokens") or 0
    total_out = totals.get("gen_ai.usage.output_tokens") or 0
```

  with:

```python
    if totals.get("cost_capture") == "partial":
        # A partial run's fully-measured totals are null (#453): its Subtotal, header
        # total and cumulative marker carry the known_* lower bound, never 0.
        total_cost_raw = totals.get("known_cost_usd")
        total_in = totals.get("known_input_tokens") or 0
        total_out = totals.get("known_output_tokens") or 0
    else:
        total_cost_raw = totals.get("cost_usd")
        total_in = totals.get("gen_ai.usage.input_tokens") or 0
        total_out = totals.get("gen_ai.usage.output_tokens") or 0
    if total_cost_raw is None:
        total_cost_raw = 0
    total_cost_str = _passthrough_num(total_cost_raw)
    partial_note = format_partial_capture_note(totals)
    partial_segment = f"\n\n{partial_note}" if partial_note else ""
```

  (c) In `render`, replace the `row_lines = []` / `for node in ...: row_lines.append(...)` /
  `run_rows = "\n".join(row_lines)` block (10 lines) with:

```python
    run_rows = "\n".join(format_node_row(node) for node in run_record.get("nodes") or [])
```

  (d) In the `body = f"""..."""` template, change the Subtotal line's ending from
  `**${total_cost_str}** | |` to `**${total_cost_str}** | |{partial_segment}`. Leave the next line
  `{economics_segment}{push_segment}` unchanged. `partial_segment` is `""` on every non-partial
  run, so the template output for those runs is unchanged character for character.

- [ ] **Step 4 — Verify pass.**
  `PYTHONPATH=scripts python -m pytest tests/test_cost_report.py -q`
  Expected: `61 passed`. All five existing goldens A–E and the push goldens stay green, which is the
  byte-identical guard for Requirement 7.

- [ ] **Step 5 — Commit.**
  `git add scripts/factory_core/cost_report.py tests/test_cost_report.py && git commit -m "feat(cost-report #453): render cost-missing nodes and partial-capture note with known totals"`

---

## Task 7: End-to-end healthy-run byte-identical guard

**Files:** `tests/test_run_record.py`

This guard should pass once Tasks 4 and 6 have landed. It checks the assemble→render path as a
whole: the record `cmd_assemble` writes for a healthy run must render exactly like the same record
with every #453 field removed (the pre-#453 shape).

- [ ] **Step 1 — Write test.** Append to `tests/test_run_record.py`:

```python


def test_healthy_run_comment_is_byte_identical_to_pre_453_shape(tmp_path, monkeypatch, health_events):
    from factory_core import cost_report as cr

    rec = _assemble_with_capture(tmp_path, monkeypatch, _COST_HEALTHY, log_text=_LOG_HEALTHY)
    legacy = json.loads(json.dumps(rec))  # the record as cmd_assemble wrote it before #453
    for key in ("cost_capture", "node_log", "known_cost_usd", "known_input_tokens",
                "known_output_tokens", "missing_nodes"):
        del legacy["totals"][key]
    for node in legacy["nodes"]:
        del node["cost_capture"]
    render = lambda r: cr.render(r, prior_comment_body="", timestamp="2026-11-22 12:00 UTC",
                                 intent="fix")
    assert render(rec) == render(legacy)
    assert "| **Subtotal** | | **320** | **52.2K** |" in render(rec)
```

- [ ] **Step 2 — Run.**
  `PYTHONPATH=scripts python -m pytest tests/test_run_record.py tests/test_cost_report.py tests/test_reconcile_cost_reports.py -q`
  Expected: `241 passed` (174 + 61 + 6).

- [ ] **Step 3 — Commit.**
  `git add tests/test_run_record.py && git commit -m "test(run-record #453): healthy run's cost comment is byte-identical end to end"`

---

## Task 8: Thread `$TMP_OUT` into both existing assemble call sites

**Files:** `tests/test_entrypoint_cost_capture_log_thread.sh` (new), `.github/workflows/ci.yml`,
`entrypoint.sh`

- [ ] **Step 1 — Write failing test.** Create `tests/test_entrypoint_cost_capture_log_thread.sh`.
  It must **not** contain the literal string `cli.py`, which would make
  `test_run_record_hermetic.sh` treat it as a state-writing test:

```bash
#!/usr/bin/env bash
# Static guard (#453): both `run-record assemble` call sites in entrypoint.sh hand the
# tee'd `archon workflow run` log ($TMP_OUT) to assemble for per-node status, the
# on_failure call resolves the workflow YAML from the clone like the success path does,
# and the success path deletes $TMP_OUT only after assembling. Text-only — this test
# never sources or executes entrypoint.sh.
#
# Run: bash tests/test_entrypoint_cost_capture_log_thread.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENTRYPOINT="${SCRIPT_DIR}/../entrypoint.sh"
FAIL=0

pass() { echo "  PASS: $1"; }
fail() { echo "  FAIL: $1"; FAIL=1; }

# Same on_failure extraction as test_cost_report_harness_economics.sh (the range runs to
# _resolve_merge_conflicts and so also spans the small helpers defined between them).
ON_FAILURE_BODY=$(sed -n '/^on_failure() {$/,/^_resolve_merge_conflicts() {$/p' "$ENTRYPOINT")

if echo "$ON_FAILURE_BODY" | grep -qF -- '--archon-log-file "${TMP_OUT:-}"'; then
  pass 'on_failure assemble passes --archon-log-file "${TMP_OUT:-}"'
else
  fail 'on_failure assemble does not pass --archon-log-file "${TMP_OUT:-}"'
fi

if echo "$ON_FAILURE_BODY" | grep -qF -- '--clone-dir "${CLONE_DIR:-.}"'; then
  pass 'on_failure assemble passes --clone-dir "${CLONE_DIR:-.}"'
else
  fail 'on_failure assemble does not pass --clone-dir "${CLONE_DIR:-.}"'
fi

SUCCESS_START=$(grep -n '^# --- Capture archon cost data and assemble run record' "$ENTRYPOINT" | head -1 | cut -d: -f1)
if [ -z "$SUCCESS_START" ]; then
  fail "success-path cost-capture block not found"
else
  SUCCESS_TAIL=$(tail -n +"$SUCCESS_START" "$ENTRYPOINT")
  if echo "$SUCCESS_TAIL" | grep -qF -- '--archon-log-file "$TMP_OUT"'; then
    pass 'success-path assemble passes --archon-log-file "$TMP_OUT"'
  else
    fail 'success-path assemble does not pass --archon-log-file "$TMP_OUT"'
  fi
  ASSEMBLE_LINE=$(echo "$SUCCESS_TAIL" | grep -n 'run-record assemble' | head -1 | cut -d: -f1)
  RM_LINE=$(echo "$SUCCESS_TAIL" | grep -nE 'rm -f .*"\$TMP_OUT"' | head -1 | cut -d: -f1)
  if [ -n "$ASSEMBLE_LINE" ] && [ -n "$RM_LINE" ] && [ "$RM_LINE" -gt "$ASSEMBLE_LINE" ]; then
    pass 'success path deletes $TMP_OUT after assemble'
  else
    fail 'success path does not delete $TMP_OUT after assemble'
  fi
fi

# The workflow-run loop's success exit must no longer delete $TMP_OUT right before
# `break` — assemble would then read a deleted log.
if awk 'prev ~ /^  rm -f "\$TMP_OUT"$/ && $0 ~ /^  break$/ {found=1} {prev=$0} END {exit !found}' "$ENTRYPOINT"; then
  fail 'workflow-run loop still deletes $TMP_OUT before break'
else
  pass 'workflow-run loop keeps $TMP_OUT through break'
fi

echo ""
[ "$FAIL" -eq 0 ] && echo "OK" || echo "FAILED"
[ "$FAIL" -eq 0 ]
```

  In `.github/workflows/ci.yml`, add this line directly after
  `      - run: bash tests/test_entrypoint_cost_report_comment.sh`:

```yaml
      - run: bash tests/test_entrypoint_cost_capture_log_thread.sh
```

- [ ] **Step 2 — Verify fail.** `bash tests/test_entrypoint_cost_capture_log_thread.sh; echo rc=$?`
  Expected: all five lines `FAIL: ...`, then `FAILED`, `rc=1`.

- [ ] **Step 3 — Implement.** Four exact-text edits in `entrypoint.sh`. Each `old` string is unique
  in the file; locate it by text, not by line number:

  (a) `on_failure`'s assemble call. Replace

```bash
      --status failed \
      --out-file "$ARTIFACTS_DIR/run-record.json" \
      --side-effect-level "${FACTORY_SIDE_EFFECT_LEVEL:-1}" \
```

  with

```bash
      --status failed \
      --out-file "$ARTIFACTS_DIR/run-record.json" \
      --archon-log-file "${TMP_OUT:-}" \
      --clone-dir "${CLONE_DIR:-.}" \
      --side-effect-level "${FACTORY_SIDE_EFFECT_LEVEL:-1}" \
```

  `"${TMP_OUT:-}"` matches the existing optional-variable convention in `on_failure`. It is empty
  when the trap fires before the workflow loop sets `TMP_OUT`, and assemble then takes the no-log
  path.

  (b) The workflow-run loop's success exit. Replace

```bash
    rm -f "$TMP_OUT"
    exit "$EXIT_CODE"
  fi
  rm -f "$TMP_OUT"
  break
done
```

  with

```bash
    rm -f "$TMP_OUT"
    exit "$EXIT_CODE"
  fi
  # $TMP_OUT (Archon's per-node log) is kept for run-record assemble below (#453).
  break
done
```

  The dead inline nonzero-exit branch above it, including its own `rm -f "$TMP_OUT"` before
  `exit "$EXIT_CODE"`, is left **unchanged** (spec "Out of scope").

  (c) The success-path assemble call. Replace

```bash
  --archon-cost-stderr-file "$ARCHON_COST_STDERR" \
  --out-file "$ARTIFACTS_DIR/run-record.json" \
```

  with

```bash
  --archon-cost-stderr-file "$ARCHON_COST_STDERR" \
  --archon-log-file "$TMP_OUT" \
  --out-file "$ARTIFACTS_DIR/run-record.json" \
```

  (d) Replace the cleanup line `rm -f "$ARCHON_COST_JSON" "$ARCHON_COST_STDERR"` with
  `rm -f "$ARCHON_COST_JSON" "$ARCHON_COST_STDERR" "$TMP_OUT"`.

  No assemble call is added. `git diff entrypoint.sh` must show exactly: +2 lines in `on_failure`,
  one `rm` line replaced by a comment in the loop, +1 line in the success-path assemble, one `rm`
  line extended.

- [ ] **Step 4 — Verify pass.**
  `bash -n entrypoint.sh && bash tests/test_entrypoint_cost_capture_log_thread.sh && bash tests/test_cost_report_harness_economics.sh && bash tests/test_run_record_hermetic.sh | tail -1`
  Expected: five `PASS:` lines then `OK`, then the harness-economics guard's `OK`, then the hermetic
  guard's `OK`. The new file must not appear in the hermetic guard's FAIL list.

- [ ] **Step 5 — Commit.**
  `git add entrypoint.sh tests/test_entrypoint_cost_capture_log_thread.sh .github/workflows/ci.yml && git commit -m "feat(entrypoint #453): pass Archon's node log to both existing assemble call sites"`

---

## Task 8b: Re-map shifted citations in the reference docs

**Files:** `docs/run-lifecycle.md`, `docs/features.md`, `docs/configuration.md`

Tasks 1-8 insert 196 lines into `run_record.py` and 3 into `entrypoint.sh`.
`docs_refs_check.py` only checks that a cited line is in range, so CI stays green while the
citations point at the wrong code. Every number below is an **expected value to be
re-derived**, measured with this plan applied directly on `4584159`. #573 (plan approved,
implementing now) will probably land first and also re-maps `docs/features.md:139`,
`docs/configuration.md:555` and `docs/run-lifecycle.md:929`, so locate each citation by its
surrounding prose, not by the doc line numbers or old values given here.

- [ ] **Step 1 — Re-derive, do not trust the numbers below blindly.**
  `grep -n '^def cmd_assemble\|^def _build_issue_economics\|adapter.get(args.clone_dir, "loops")\|"issue-economics", help\|^LEDGER_PATH' scripts/factory_core/run_record.py; grep -n 'run-record assemble\|post_cost_report || true\|^trap on_failure\|^# --- Capture archon' entrypoint.sh`
  Expected when this branch sits directly on `4584159`: `run_record.py` 690 (`LEDGER_PATH`),
  886 (`cmd_assemble`), 929 (`loops`), 1041 (`_build_issue_economics`), 1193
  (`issue-economics`); `entrypoint.sh` 559 and 1092 (assemble), 533, 635 and 1110
  (`post_cost_report`), 637 (trap), 1082 (capture header). If they differ (for example #573
  landed first), use the grep output.

- [ ] **Step 2 — Re-map every citation in the three docs, including bare continuation spans
  such as `:633`.** Compute each offset against the pre-change file (`git show
  origin/main:<file>`), then apply:
  `entrypoint.sh`: old line 570–1098 → +2; old line ≥ 1099 → +3; ≤ 569 unchanged.
  `run_record.py`: old line 330–719 → +196; 732–778 → +194; 784–994 → +190; ≥ 995 → +192;
  ≤ 329 unchanged.
  Spot checks: `run_record.py:690-848` → `:886-1038`; `:735` → `:929`; `:28,494` → `:28,690`;
  `:1001` → `:1193`; `entrypoint.sh:1080-1107` → `:1082-1110`; `:1030-1075` → `:1032-1077`;
  `:1071-1074` → `:1073-1076`; `:1090` → `:1092`; `:633` → `:635`.

- [ ] **Step 3 — Prose.** In `docs/run-lifecycle.md` §7 step 2, after "adds per-node token
  and cost figures from the Archon cost JSON", add: "classifies each node's `cost_capture` (a
  `command:`/`prompt:` node reported with zero usage is `missing`, never `$0`; #453)".
  Replace "`TMP_OUT` exists only inside the workflow loop" with "`TMP_OUT` is created inside
  the workflow loop and, on success, kept until `run-record assemble` has read it".

- [ ] **Step 4 — Verify.**
  `python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md`
  Expected: `Docs reference check passed for 4 doc(s).`

- [ ] **Step 5 — Commit.**
  `git add docs/run-lifecycle.md docs/features.md docs/configuration.md && git commit -m "docs(#453): re-map citations shifted by the cost-capture change"`

---

## Task 9: Full verification and PR notes

**Files:** none (verification only)

- [ ] **Step 1 — Full Python suite.** `PYTHONPATH=scripts python -m pytest tests/ -q`
  Expected: no failures beyond those already failing on `origin/main` in the same environment.
  If anything fails, run the same command on a clean `origin/main` checkout. Any failure not
  present there is this change's to fix.

- [ ] **Step 2 — Cost/run-record shell tests from CI.**
  `for t in test_cost_report_endpoint test_cost_report_harness_economics test_run_record_hermetic test_entrypoint_cost_report_regression test_entrypoint_cost_report_comment test_entrypoint_cost_capture_log_thread; do echo "== $t"; bash tests/$t.sh | tail -1; done`
  Expected: same results as on `origin/main` for the five existing tests, and `OK` for the new one.
  Note: in the factory container, `test_entrypoint_cost_report_regression.sh` already reports
  `2 passed, 3 failed` on unmodified `main` (environment-specific). Compare against that baseline
  rather than expecting green there; CI is the authority.

- [ ] **Step 3 — Scope check.**
  `git diff --stat origin/main...HEAD -- . ':!docs/superpowers' ':!.archon/memory'`
  Expected: exactly the 11 files in the File Structure table (the 8 code/test/CI files
  plus `docs/run-lifecycle.md`, `docs/features.md` and `docs/configuration.md`).

- [ ] **Step 4 — PR description follow-ups.** Record these in the PR body only; do not implement
  them or file issues from the implement phase:
  - Archon upstream: a `node_completed` event is written without `cost_usd`/tokens when no result
    message arrived (`dag-executor.ts`), `buildCostEntries` coerces an absent cost to `0`
    (`workflow.ts`), and `node_failed` nodes are filtered out of `archon workflow cost`.
  - `entrypoint.sh`'s inline nonzero-exit branch in the workflow-run loop is unreachable (the ERR
    trap fires first). Its rate-limit retry never runs. Remove or repair it in its own ticket.
  - The session-window pause path in `on_failure` returns before assembling a run record. That is
    unchanged here and out of this spec's two-call-site scope.
  - `$TMP_OUT` now lives from the workflow loop's `break` until the success-path cleanup `rm`. If
    the ERR trap fired inside that window, `on_failure` would hand `_handle_session_window_pause` a
    successful run's log. Every step in the window is `set +e`/`|| true`-guarded, so the risk is
    negligible.

---

## Requirement → task traceability

| Spec requirement | Task(s) |
|---|---|
| R1 per-node classification, bash zero stays completed, duration never nulled | 3, 4 |
| R2 ran-but-absent `command:`/`prompt:` node → `not_in_cost_report`; Skipped and absent `bash:` nodes omitted | 3, 4 |
| R3 command-name join, line-anchored, known names only | 1, 2 |
| R4 no-log fallback: nothing synthesized, `node_log: unavailable`, one event; `ok == false` → no classification | 4 |
| R5 entrypoint: two existing call sites, `--archon-log-file`, `on_failure --clone-dir`, `rm` after assemble, dead branch untouched | 8 |
| R6 totals `cost_capture`/`node_log`/`known_*`/`missing_nodes`; `cost_usd` null on partial; `unknown` path | 4, 5 |
| R7 missing rows, partial note, `known_*` subtotal/header/cumulative, complete run byte-identical | 6, 7 |
| R8 no `TypeError` on nulled nodes | 3, 4 (`_sum_usage` over completed nodes only) |
| R9 `node_missing` / `node_types_unavailable` / `node_log_unavailable` health events | 4 |
| R10 no gate/breaker/budget/scoring change; `factory_cpm` null on partial | 5 (no production change outside the files above) |
| Operator fixtures (a)–(e) | (a) `_COST_388`; (b) `_LOG_388`; (c) `_COST_HEALTHY` + Task 6/7 guards; (d) `test_assemble_started_but_unreported_node_is_missing`; (e) `test_assemble_failed_node_absent_from_report_is_missing` |
