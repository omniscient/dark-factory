# Implementation Plan — Fix Cost-Report Regression and Add a Durable Run-Record Sink

**Issue:** omniscient/dark-factory#300
**Spec:** [docs/superpowers/specs/2026-07-17-cost-report-durable-sink-fix-design.md](../specs/2026-07-17-cost-report-durable-sink-fix-design.md)
**Status:** ready for architect review

## Goal

Repair the two-bug cost-report regression (bad Archon pin + a silent no-op on empty
node data), give the full assembled run record a durable sink outside the ephemeral
per-run artifacts directory, distinguish `0` from "unmeasured" everywhere in
`run_record.py`'s output, stop bash tests from writing to production state, add a
behavioral (not just static-grep) regression test wired into CI, and add lightweight
non-blocking observability (reconciliation report + health signal) for recurrence.

## Architecture

No new services. All changes land in three existing files (`Dockerfile`,
`entrypoint.sh`, `scripts/factory_core/run_record.py`), one new script
(`scripts/reconcile_cost_reports.py`), and test files. The durable sink is a new
per-run-id JSON file under the same `SCHEDULER_STATE_DIR`-derived volume `runs.jsonl`
already lives on — no new environment variable, no new Docker volume.

```
entrypoint.sh
  archon workflow cost --last --json --quiet > $ARCHON_COST_JSON 2>$ARCHON_COST_STDERR
       │ (captures exit code + stderr now, instead of discarding both)
       ▼
run_record.py cmd_assemble()
  _parse_archon_cost() ──► nodes[] + archon_cost_capture{ok, exit_code, stderr_excerpt}
       │
       ├─► $ARTIFACTS_DIR/run-record.json          (existing, ephemeral, unchanged shape+path)
       └─► $SCHEDULER_STATE_DIR/run-records/<run_id>.json   (NEW, durable)
       │
       ▼
entrypoint.sh post_cost_report()
  RUN_ROWS empty? ──► loud ERROR log line (was: silent `return`)  [+ Seq factory.cost_report.missing]
```

## Tech Stack

Bash (`entrypoint.sh`, `Dockerfile`), Python 3.12 (`scripts/factory_core/run_record.py`,
new `scripts/reconcile_cost_reports.py`), pytest for `.py` tests, plain bash assertions
for `.sh` tests (existing repo convention — no bats/shunit2 dependency).

## File Structure

| File | Change |
|---|---|
| `Dockerfile` | Re-pin Archon SHA; add build-time `archon workflow cost --help` assertion |
| `entrypoint.sh` | Capture archon-cost stderr/exit code; loud diagnostic + Seq event in `post_cost_report()`; pass capture evidence to `cmd_assemble` |
| `scripts/factory_core/run_record.py` | `JSONL_PATH` env-derived; durable per-run-id write; `archon_cost_capture` field; `0`→`null` for stage stubs and CLI defaults |
| `scripts/reconcile_cost_reports.py` | New: one-shot reconciliation report (read-only) |
| `tests/test_run_record.py` | New/updated unit tests for all `run_record.py` changes |
| `tests/test_run_record_hermetic.sh` | New: greps bash tests for `SCHEDULER_STATE_DIR` override around `run-record`/`error-signature-write` calls |
| `tests/test_entrypoint_cost_report_regression.sh` | New: behavioral test reproducing `function_rc=0 / gh_calls=0`, red on unpatched code, green after fix |
| `tests/test_reconcile_cost_reports.py` | New: unit tests for the reconciliation script |
| `.github/workflows/ci.yml` | Wire the three new/existing cost-report tests into the `tests` job |

---

## Task 1: Re-pin Archon and add build-time CLI-presence assertion

**Files:** `Dockerfile`

### Step 1.1 — Update the pin and add the assertion

`Dockerfile` currently (lines 93–107):

```dockerfile
# Archon CLI (from fork — includes workflow cost tracking).
# Pinned to an immutable commit on feat/workflow-cost-tracking instead of the
# moving branch tip, so a rebuild always fetches the reviewed code. Bump the SHA
# deliberately to pick up Archon updates.
# Deliberately NOT `bun link`: its shim lands in the invoking user's ~/.bun/bin
# (root → /root/.bun/bin), which is off PATH and unreadable for the factory
# user. Cached layers masked this for months; any --no-cache rebuild lost
# `archon` (exit 127 in entrypoint). cli.ts carries a `#!/usr/bin/env bun`
# shebang, so a plain symlink on PATH is all that's needed.
RUN git clone https://github.com/omniscient/Archon.git /opt/archon && \
    cd /opt/archon && \
    git checkout f83fb556a2a864014e12ecfe6f60c7a1d18928b9 && \
    bun install && \
    chmod +x /opt/archon/packages/cli/src/cli.ts && \
    ln -sf /opt/archon/packages/cli/src/cli.ts /usr/local/bin/archon
```

Replace with:

```dockerfile
# Archon CLI (from fork — includes workflow cost tracking).
# Pinned to an immutable commit on feat/workflow-cost-tracking instead of the
# moving branch tip, so a rebuild always fetches the reviewed code. Bump the SHA
# deliberately to pick up Archon updates.
#
# df#300: the previous pin (f83fb556a2a864014e12ecfe6f60c7a1d18928b9) predates
# the `workflow cost` feature commit despite this comment's prior claim —
# `archon workflow cost` didn't exist in that tree, every run since ~2026-07-10
# silently produced zero cost data. Re-pinned to 74372446d1c5f07101dfff61c44be8895cca30db,
# the specific reviewed commit on feat/workflow-cost-tracking that introduces the
# subcommand (verified ancestor of that branch's tip f0395f90c404a82f69abb29ba5c05789ed08b654,
# but NOT following the moving tip itself — see spec Alternative 3). The build-time
# assertion below is the actual guarantee against a repeat: whichever SHA is chosen,
# the image fails to build if the CLI surface is missing.
#
# Deliberately NOT `bun link`: its shim lands in the invoking user's ~/.bun/bin
# (root → /root/.bun/bin), which is off PATH and unreadable for the factory
# user. Cached layers masked this for months; any --no-cache rebuild lost
# `archon` (exit 127 in entrypoint). cli.ts carries a `#!/usr/bin/env bun`
# shebang, so a plain symlink on PATH is all that's needed.
RUN git clone https://github.com/omniscient/Archon.git /opt/archon && \
    cd /opt/archon && \
    git checkout 74372446d1c5f07101dfff61c44be8895cca30db && \
    bun install && \
    chmod +x /opt/archon/packages/cli/src/cli.ts && \
    ln -sf /opt/archon/packages/cli/src/cli.ts /usr/local/bin/archon

# Binding correctness guarantee (df#300): fail the build, loudly and immediately,
# if the pinned commit doesn't actually ship `workflow cost`. This is what prevents
# a future bad pin from shipping silently — not the SHA choice above.
RUN archon workflow cost --help
```

No test file for this step — it is exercised by the existing `docker-build` CI job
(`.github/workflows/ci.yml`'s `docker-build` job already runs `docker build -f
Dockerfile -t dark-factory:pr .`; a bad pin now fails that job instead of building
green and shipping a silently broken image).

### Step 1.2 — Verify

```bash
docker build -f Dockerfile -t dark-factory:pr-test . 2>&1 | tail -30
```

Expected: build succeeds, and the `archon workflow cost --help` layer's output shows
help text (not "unknown command" / non-zero exit). If the pin is wrong, the build
fails at that `RUN` line — confirming the assertion works — before any commit lands.

**Implement-phase callout (do not skip):** this refine/plan phase has no network
access to Archon's history. Before running Step 1.2 for real, `git ls-remote` /
`git log --oneline` against `https://github.com/omniscient/Archon.git` and confirm
`74372446d1c5f07101dfff61c44be8895cca30db` is an ancestor of
`feat/workflow-cost-tracking` and contains the `workflow cost` subcommand. If it does
not hold up, select the minimal commit that does (still an immutable SHA, never a
branch name), update the pin and the comment above accordingly, and record the
substitution plus your verification evidence in the commit message for this task.

### Step 1.3 — Commit

```bash
git add Dockerfile
git commit -m "fix(dockerfile): re-pin Archon to commit that actually ships workflow cost (#300)

Prior pin f83fb556 predated the workflow-cost feature commit despite the
adjacent comment's claim, silently producing zero cost/token telemetry on
every run since ~2026-07-10. Add a build-time 'archon workflow cost --help'
assertion so a future bad pin fails the build instead of shipping quietly."
```

---

## Task 2: Capture archon-cost stderr/exit code and thread through to the assembled record

**Files:** `entrypoint.sh`, `scripts/factory_core/run_record.py`, `tests/test_run_record.py`

### Step 2.1 — Write failing tests for `_parse_archon_cost` capture evidence

Add to `tests/test_run_record.py`, in the `_parse_archon_cost` section (after
`test_parse_archon_cost_empty_file`, ~line 136):

```python
def test_parse_archon_cost_capture_ok_when_nodes_present(tmp_path):
    cost_json = tmp_path / "cost.json"
    cost_json.write_text(json.dumps({
        "runId": "xyz",
        "nodes": [{"nodeId": "implement", "inputTokens": 1, "outputTokens": 1,
                   "costUsd": 0.01, "durationMs": 100, "modelUsage": {}}],
    }))
    nodes, capture = rr._parse_archon_cost_with_capture(cost_json, exit_code=0, stderr_text="")
    assert len(nodes) == 1
    assert capture == {"ok": True, "exit_code": 0, "stderr_excerpt": ""}


def test_parse_archon_cost_capture_not_ok_on_nonzero_exit(tmp_path):
    cost_json = tmp_path / "cost.json"
    cost_json.write_text("")
    nodes, capture = rr._parse_archon_cost_with_capture(
        cost_json, exit_code=127, stderr_text="archon: command not found\n"
    )
    assert nodes == []
    assert capture["ok"] is False
    assert capture["exit_code"] == 127
    assert "command not found" in capture["stderr_excerpt"]


def test_parse_archon_cost_capture_ok_valid_json_zero_nodes(tmp_path):
    # Archon ran fine and genuinely reports zero nodes (e.g. an ungated refine/plan
    # run) — this must NOT be flagged as a capture failure.
    cost_json = tmp_path / "cost.json"
    cost_json.write_text(json.dumps({"runId": "xyz", "nodes": []}))
    nodes, capture = rr._parse_archon_cost_with_capture(cost_json, exit_code=0, stderr_text="")
    assert nodes == []
    assert capture == {"ok": True, "exit_code": 0, "stderr_excerpt": ""}


def test_parse_archon_cost_capture_not_ok_on_unparseable_output(tmp_path):
    cost_json = tmp_path / "cost.json"
    cost_json.write_text("not json at all {{{")
    nodes, capture = rr._parse_archon_cost_with_capture(cost_json, exit_code=0, stderr_text="")
    assert nodes == []
    assert capture["ok"] is False
    assert capture["exit_code"] == 0
```

Run and confirm these fail (the function doesn't exist yet):

```bash
cd /workspace/dark-factory && PYTHONPATH=scripts python -m pytest tests/test_run_record.py -k parse_archon_cost_capture -v
```

Expected: 4 errors (`AttributeError: module 'factory_core.run_record' has no
attribute '_parse_archon_cost_with_capture'`).

### Step 2.2 — Implement `_parse_archon_cost_with_capture`

In `scripts/factory_core/run_record.py`, keep `_parse_archon_cost` (still used
directly by existing tests `test_parse_archon_cost_basic`,
`test_parse_archon_cost_missing_file`, `test_parse_archon_cost_empty_file` — do not
change its signature or behavior) and add a new wrapper immediately after it
(after line 211):

```python
def _parse_archon_cost_with_capture(
    path: "pathlib.Path | None", *, exit_code: int, stderr_text: str
) -> "tuple[list, dict]":
    """Like _parse_archon_cost, but distinguishes "archon ran fine, genuinely zero
    nodes" from "the command errored / its output didn't parse" (df#300).

    ok=False when: nonzero exit code, OR the file is missing/empty/unparseable.
    ok=True when: exit_code==0 AND a run object was found (nodes may still be []
    if archon itself reports no nodes — that is a legitimate zero, not a capture
    failure).
    """
    stderr_excerpt = (stderr_text or "").strip()[-2000:]
    if exit_code != 0:
        return [], {"ok": False, "exit_code": exit_code, "stderr_excerpt": stderr_excerpt}

    if path is None or not path.exists():
        return [], {"ok": False, "exit_code": exit_code, "stderr_excerpt": stderr_excerpt}

    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return [], {"ok": False, "exit_code": exit_code, "stderr_excerpt": stderr_excerpt}

    if not content:
        return [], {"ok": False, "exit_code": exit_code, "stderr_excerpt": stderr_excerpt}

    run_obj = None
    for obj in _iter_json_documents(content):
        candidates = obj if isinstance(obj, list) else [obj]
        for cand in candidates:
            if isinstance(cand, dict) and (cand.get("run_id") or cand.get("runId")):
                run_obj = cand
                break
        if run_obj is not None:
            break

    if run_obj is None:
        return [], {"ok": False, "exit_code": exit_code, "stderr_excerpt": stderr_excerpt}

    nodes = _parse_archon_cost(path)
    return nodes, {"ok": True, "exit_code": exit_code, "stderr_excerpt": stderr_excerpt}
```

Run the tests from Step 2.1 again — expect all 4 to pass:

```bash
cd /workspace/dark-factory && PYTHONPATH=scripts python -m pytest tests/test_run_record.py -k parse_archon_cost_capture -v
```

### Step 2.3 — Wire `cmd_assemble` to use the capture-aware parser and expose `archon_cost_capture`

Write a failing test first, added to `tests/test_run_record.py` after
`test_assemble_incorporates_archon_cost` (~line 468):

```python
def test_assemble_records_archon_cost_capture_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

    cost_json = tmp_path / "cost.json"
    cost_json.write_text(json.dumps({
        "runId": "abc123",
        "nodes": [{"nodeId": "implement", "inputTokens": 1, "outputTokens": 1,
                   "costUsd": 0.01, "durationMs": 1, "modelUsage": {}}],
    }))

    out = tmp_path / "run-record.json"
    args = _AssembleArgs(tmp_path, out)
    args.archon_cost_json = str(cost_json)
    args.archon_cost_exit_code = 0
    args.archon_cost_stderr_file = None
    rr.cmd_assemble(args)

    rec = json.loads(out.read_text())
    assert rec["archon_cost_capture"] == {"ok": True, "exit_code": 0, "stderr_excerpt": ""}


def test_assemble_records_archon_cost_capture_failure_when_nodes_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

    stderr_file = tmp_path / "archon-cost.stderr"
    stderr_file.write_text("archon: unknown command 'workflow cost'\n")

    out = tmp_path / "run-record.json"
    args = _AssembleArgs(tmp_path, out)
    args.archon_cost_json = str(tmp_path / "does-not-exist.json")
    args.archon_cost_exit_code = 127
    args.archon_cost_stderr_file = str(stderr_file)
    rr.cmd_assemble(args)

    rec = json.loads(out.read_text())
    assert rec["archon_cost_capture"]["ok"] is False
    assert rec["archon_cost_capture"]["exit_code"] == 127
    assert "unknown command" in rec["archon_cost_capture"]["stderr_excerpt"]
```

Note: `_AssembleArgs.__init__` only sets `artifacts_dir`/`out_file`; the two new
attributes are set directly on the instance in the test, matching the existing
`args.archon_cost_json = str(cost_json)` pattern already used at line 460 — no
`_AssembleArgs` class change is required for the tests, only for `main()`'s
argparse wiring in Step 2.4.

Run and confirm both fail (`AttributeError: 'MagicMock'... ` no —
`AttributeError: '_AssembleArgs' object has no attribute 'archon_cost_exit_code'`
is fine since we set it in the test; the real failure will be
`rec["archon_cost_capture"]` → `KeyError`):

```bash
cd /workspace/dark-factory && PYTHONPATH=scripts python -m pytest tests/test_run_record.py -k archon_cost_capture -v
```

Now implement in `cmd_assemble()` (`run_record.py:467-506`), replacing:

```python
    archon_path = pathlib.Path(args.archon_cost_json) if args.archon_cost_json else None
    nodes = _parse_archon_cost(archon_path)

    totals_in = sum(n.get("gen_ai.usage.input_tokens", 0) for n in nodes)
    totals_out = sum(n.get("gen_ai.usage.output_tokens", 0) for n in nodes)
    totals_cost = sum(n.get("cost_usd", 0) for n in nodes)

    run_record = {
        "run_id": args.run_id,
        "issue_number": args.issue,
        "intent": args.intent,
        "started_at": args.started_at or _timestamp(),
        "completed_at": _timestamp(),
        "status": args.status,
        "stages": stages,
        "nodes": nodes,
        "artifacts": artifacts,
        "totals": {
            "gen_ai.usage.input_tokens": totals_in,
            "gen_ai.usage.output_tokens": totals_out,
            "cost_usd": totals_cost,
        },
    }
```

with:

```python
    archon_path = pathlib.Path(args.archon_cost_json) if args.archon_cost_json else None
    stderr_text = ""
    stderr_file = getattr(args, "archon_cost_stderr_file", None)
    if stderr_file:
        try:
            stderr_text = pathlib.Path(stderr_file).read_text(encoding="utf-8")
        except OSError:
            stderr_text = ""
    exit_code = getattr(args, "archon_cost_exit_code", 0) or 0
    nodes, archon_cost_capture = _parse_archon_cost_with_capture(
        archon_path, exit_code=exit_code, stderr_text=stderr_text
    )

    totals_in = sum(n.get("gen_ai.usage.input_tokens", 0) for n in nodes)
    totals_out = sum(n.get("gen_ai.usage.output_tokens", 0) for n in nodes)
    totals_cost = sum(n.get("cost_usd", 0) for n in nodes)

    run_record = {
        "run_id": args.run_id,
        "issue_number": args.issue,
        "intent": args.intent,
        "started_at": args.started_at or _timestamp(),
        "completed_at": _timestamp(),
        "status": args.status,
        "stages": stages,
        "nodes": nodes,
        "artifacts": artifacts,
        "archon_cost_capture": archon_cost_capture,
        "totals": {
            "gen_ai.usage.input_tokens": totals_in,
            "gen_ai.usage.output_tokens": totals_out,
            "cost_usd": totals_cost,
        },
    }
```

Run the Step 2.3 tests again — expect both to pass. Then run the full file to
confirm no existing test broke (existing tests don't set `archon_cost_exit_code`/
`archon_cost_stderr_file` on `_AssembleArgs`, so `getattr(..., 0)` / `getattr(...,
None)` must degrade to `exit_code=0` — matching "archon ran and either found or
didn't find a run object," i.e. unaffected pre-existing behavior for tests that
don't care about capture evidence):

```bash
cd /workspace/dark-factory && PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v
```

Expected: all tests pass (previously-passing tests plus the new ones).

### Step 2.4 — Wire argparse and `entrypoint.sh` to actually capture stderr/exit code

In `run_record.py`'s `main()` (`assemble` subparser, ~line 671-681), add two
arguments:

```python
    a.add_argument("--archon-cost-json")
    a.add_argument("--archon-cost-exit-code", type=int, default=0)
    a.add_argument("--archon-cost-stderr-file")
    a.add_argument("--out-file", required=True)
```

(insert the two new lines directly after the existing `--archon-cost-json` line).

In `entrypoint.sh`, the success path (lines 951-966) currently:

```bash
# --- Capture archon cost data and assemble run record (non-fatal) ---
ARCHON_COST_JSON=$(mktemp)
archon workflow cost --last --json --quiet > "$ARCHON_COST_JSON" 2>/dev/null || true

# TARGET-PATH: cli.py resolves under dark-factory/ in the clone — target's own copy until
# P3 cleanup, baked self-contained fallback copy afterwards (df#14)
python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record assemble \
  --run-id "${RUN_ID:-unknown}" \
  --issue "$ISSUE_NUM" \
  --intent "$INTENT" \
  --started-at "${RUN_STARTED_AT:-}" \
  --artifacts-dir "$ARTIFACTS_DIR" \
  --archon-cost-json "$ARCHON_COST_JSON" \
  --out-file "$ARTIFACTS_DIR/run-record.json" || true

rm -f "$ARCHON_COST_JSON"
```

Replace with (captures exit code and stderr instead of discarding both):

```bash
# --- Capture archon cost data and assemble run record (non-fatal) ---
ARCHON_COST_JSON=$(mktemp)
ARCHON_COST_STDERR=$(mktemp)
archon workflow cost --last --json --quiet > "$ARCHON_COST_JSON" 2>"$ARCHON_COST_STDERR"
ARCHON_COST_RC=$?

# TARGET-PATH: cli.py resolves under dark-factory/ in the clone — target's own copy until
# P3 cleanup, baked self-contained fallback copy afterwards (df#14)
python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record assemble \
  --run-id "${RUN_ID:-unknown}" \
  --issue "$ISSUE_NUM" \
  --intent "$INTENT" \
  --started-at "${RUN_STARTED_AT:-}" \
  --artifacts-dir "$ARTIFACTS_DIR" \
  --archon-cost-json "$ARCHON_COST_JSON" \
  --archon-cost-exit-code "$ARCHON_COST_RC" \
  --archon-cost-stderr-file "$ARCHON_COST_STDERR" \
  --out-file "$ARTIFACTS_DIR/run-record.json" || true

rm -f "$ARCHON_COST_JSON" "$ARCHON_COST_STDERR"
```

Note `set -euo pipefail` is active in `entrypoint.sh`: the previous line ended in
`|| true` specifically to survive a nonzero `archon` exit under `set -e`. Removing
`|| true` from the `archon workflow cost` line itself would now kill the script on
a nonzero exit, which is wrong (cost capture must stay non-fatal) — so the exit
code is captured via `ARCHON_COST_RC=$?` on the line immediately after, following
the same pattern already used elsewhere in this file (e.g. `EXIT_CODE=$?` at
`on_failure`'s first line). Do this by temporarily disabling `set -e` around the
one command, since `$?` after a command that already ran under `set -e` and failed
would otherwise abort before the next line executes:

```bash
ARCHON_COST_JSON=$(mktemp)
ARCHON_COST_STDERR=$(mktemp)
set +e
archon workflow cost --last --json --quiet > "$ARCHON_COST_JSON" 2>"$ARCHON_COST_STDERR"
ARCHON_COST_RC=$?
set -e
```

Use this `set +e` / `set -e` bracketed form (not the bare form shown first above)
in the actual edit, matching how this file already brackets non-fatal external
commands elsewhere (e.g. the retry loop's `set +e`/`set -e` around the claude
invocation).

Apply the identical change to the failure path in `on_failure()` (lines 588-602):

```bash
  if [ -n "${ARTIFACTS_DIR:-}" ]; then
    local FAIL_COST_JSON FAIL_COST_STDERR FAIL_COST_RC
    FAIL_COST_JSON=$(mktemp)
    FAIL_COST_STDERR=$(mktemp)
    set +e
    archon workflow cost --last --json --quiet > "$FAIL_COST_JSON" 2>"$FAIL_COST_STDERR"
    FAIL_COST_RC=$?
    set -e
    python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record assemble \
      --run-id "${RUN_ID:-unknown}" \
      --issue "${ISSUE_NUM:-0}" \
      --intent "${INTENT:-unknown}" \
      --started-at "${RUN_STARTED_AT:-}" \
      --artifacts-dir "$ARTIFACTS_DIR" \
      --archon-cost-json "$FAIL_COST_JSON" \
      --archon-cost-exit-code "$FAIL_COST_RC" \
      --archon-cost-stderr-file "$FAIL_COST_STDERR" \
      --status failed \
      --out-file "$ARTIFACTS_DIR/run-record.json" || true
    rm -f "$FAIL_COST_JSON" "$FAIL_COST_STDERR"
  fi
```

### Step 2.5 — Verify with the repo's existing bash-sourcing test pattern

Run the full unit suite plus a manual sanity source-check that `entrypoint.sh`
still parses cleanly:

```bash
cd /workspace/dark-factory
PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v
bash -n entrypoint.sh   # syntax check only
```

Expected: all pytest cases pass; `bash -n` exits 0 (no output = valid syntax).

### Step 2.6 — Commit

```bash
git add scripts/factory_core/run_record.py entrypoint.sh tests/test_run_record.py
git commit -m "fix(run-record): capture archon-cost exit code/stderr instead of discarding them (#300)

_parse_archon_cost_with_capture() and the new archon_cost_capture field on the
assembled record distinguish 'archon ran and genuinely found zero nodes' from
'the command errored or its output didn't parse' — previously both collapsed
into an indistinguishable empty nodes[]."
```

---

## Task 3: Loud diagnostic instead of silent return in `post_cost_report()`, plus health-signal Seq event

**Files:** `entrypoint.sh`, `tests/test_cost_report_harness_economics.sh` (extend), `tests/test_entrypoint_cost_report_regression.sh` (new — see Task 6)

### Step 3.1 — Replace the silent return

`entrypoint.sh:435` currently:

```bash
  if [ -z "$RUN_ROWS" ]; then return; fi
```

Replace with:

```bash
  if [ -z "$RUN_ROWS" ]; then
    local CAPTURE_OK CAPTURE_RC CAPTURE_STDERR NODES_COUNT
    # NOTE: jq's `//` alternative operator treats `false` the same as `null` (both are
    # falsy in jq), so `.ok // "unknown"` would silently turn a genuine `ok: false` into
    # the string "unknown" — exactly the capture-failure case this diagnostic exists to
    # surface. Use `if has("ok") then .ok else "unknown" end` so a real `false` survives.
    CAPTURE_OK=$(jq -r '.archon_cost_capture | if type == "object" and has("ok") then .ok else "unknown" end' "$RUN_RECORD_FILE" 2>/dev/null || echo "unknown")
    CAPTURE_RC=$(jq -r '.archon_cost_capture.exit_code // "unknown"' "$RUN_RECORD_FILE" 2>/dev/null || echo "unknown")
    CAPTURE_STDERR=$(jq -r '.archon_cost_capture.stderr_excerpt // ""' "$RUN_RECORD_FILE" 2>/dev/null || echo "")
    NODES_COUNT=$(jq -r '(.nodes // []) | length' "$RUN_RECORD_FILE" 2>/dev/null || echo "0")
    echo "ERROR: cost report has zero node rows for run ${RUN_ID:-unknown} (issue #${ISSUE_NUM}); nodes=${NODES_COUNT}, archon_cost_capture.ok=${CAPTURE_OK}, archon_cost_exit_code=${CAPTURE_RC}, stderr=${CAPTURE_STDERR}" >&2
    python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record health-event \
      --run-id "${RUN_ID:-unknown}" \
      --issue "${ISSUE_NUM}" \
      --event "factory.cost_report.missing" \
      --detail "nodes_count=${NODES_COUNT}" "archon_cost_capture_ok=${CAPTURE_OK}" "archon_cost_exit_code=${CAPTURE_RC}" \
      2>/dev/null || true
    return
  fi
```

This stays observability, not a gate — `post_cost_report` still returns, the run
still completes, matching the spec's explicit "visible, not blocking."

### Step 3.2 — Add the `health-event` subcommand to `run_record.py`

Write a failing test first, in `tests/test_run_record.py` (new section at the end,
after the memory-trace tests):

```python
# ---------------------------------------------------------------------------
# health-event (factory.cost_report.missing and similar recurrence signals)
# ---------------------------------------------------------------------------

class _HealthEventArgs:
    run_id = "abc123"
    issue = 300
    event = "factory.cost_report.missing"
    detail = ["nodes_count=0", "archon_cost_capture_ok=False"]


def test_health_event_posts_to_seq(monkeypatch):
    posted = {}
    monkeypatch.setattr(rr, "_post_seq_raw", lambda payload: posted.update(payload))

    rr.cmd_health_event(_HealthEventArgs())

    assert posted["Events"][0]["MessageTemplate"] == "{Event} issue=#{IssueNumber} run={RunId}"
    props = posted["Events"][0]["Properties"]
    assert props["Event"] == "factory.cost_report.missing"
    assert props["IssueNumber"] == 300
    assert props["RunId"] == "abc123"
    assert props["nodes_count"] == "0"
    assert props["archon_cost_capture_ok"] == "False"


def test_health_event_nonfatal_on_seq_failure(monkeypatch):
    def _raise(payload):
        raise Exception("unreachable")
    monkeypatch.setattr(rr, "_post_seq_raw", _raise)

    rr.cmd_health_event(_HealthEventArgs())  # must not raise
```

Run and confirm failure (`AttributeError`):

```bash
cd /workspace/dark-factory && PYTHONPATH=scripts python -m pytest tests/test_run_record.py -k health_event -v
```

Implement in `run_record.py`. First, factor the existing `_post_seq()`'s
try/except-wrapped POST into a reusable `_post_seq_raw()` (refactor, not new
behavior) — replace the tail of `_post_seq()` (lines 94-103):

```python
    endpoint = f"{SEQ_URL.rstrip('/')}/api/events/raw"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        endpoint, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception:
        pass  # non-fatal: local file was already written
```

with:

```python
    _post_seq_raw(payload)


def _post_seq_raw(payload: dict) -> None:
    endpoint = f"{SEQ_URL.rstrip('/')}/api/events/raw"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        endpoint, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception:
        pass  # non-fatal: local file was already written
```

Then add `cmd_health_event()` after `cmd_record()` (after line 136):

```python
def cmd_health_event(args) -> None:
    """Lightweight, non-blocking recurrence-detection signal (df#300).

    Distinct from cmd_record's per-stage events: this is a named incident signal
    (e.g. 'factory.cost_report.missing'), not a stage verdict, so it gets its own
    MessageTemplate rather than overloading Stage/Verdict fields with an event name.
    """
    details: dict = {}
    for kv in args.detail or []:
        k, _, v = kv.partition("=")
        details[k] = v

    payload = {
        "Events": [
            {
                "Timestamp": _timestamp(),
                "Level": "Warning",
                "MessageTemplate": "{Event} issue=#{IssueNumber} run={RunId}",
                "Properties": {
                    "Event": args.event,
                    "IssueNumber": args.issue,
                    "RunId": args.run_id,
                    **details,
                },
            }
        ]
    }
    _post_seq_raw(payload)
```

Wire the subparser in `main()` (after the `record` subparser block, before `a =
sub.add_parser("assemble", ...)`):

```python
    he = sub.add_parser("health-event", help="Emit a non-blocking recurrence-detection signal")
    he.add_argument("--run-id", required=True)
    he.add_argument("--issue", type=int, required=True)
    he.add_argument("--event", required=True)
    he.add_argument("--detail", nargs="*", metavar="KEY=VAL")
```

and the dispatch:

```python
    elif parsed.cmd == "health-event":
        cmd_health_event(parsed)
```

Run the Step 3.2 tests again — expect both to pass, then the full file:

```bash
cd /workspace/dark-factory && PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v
```

### Step 3.3 — Verify entrypoint.sh syntax

```bash
bash -n entrypoint.sh
```

### Step 3.4 — Commit

```bash
git add entrypoint.sh scripts/factory_core/run_record.py tests/test_run_record.py
git commit -m "fix(cost-report): log loudly instead of silently returning on empty node rows (#300)

post_cost_report() previously returned success with no log line when
RUN_ROWS was empty — every run since the bad Archon pin landed did this.
Add an ERROR diagnostic plus a factory.cost_report.missing Seq health event
so a future silent regression is detectable without manual issue spot-checks."
```

---

## Task 4: Durable per-run-id record store

**Files:** `scripts/factory_core/run_record.py`, `entrypoint.sh`, `tests/test_run_record.py`

### Step 4.1 — Write failing tests for the durable write

Add to `tests/test_run_record.py`, after `test_assemble_builds_run_record`:

```python
def test_assemble_writes_durable_per_run_record(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")
    state_dir = tmp_path / "state"
    monkeypatch.setattr(rr, "SCHEDULER_STATE_DIR", state_dir)

    out = tmp_path / "run-record.json"
    args = _AssembleArgs(tmp_path, out)
    rr.cmd_assemble(args)

    durable_path = state_dir / "run-records" / "abc123.json"
    assert durable_path.exists()
    durable_rec = json.loads(durable_path.read_text())
    ephemeral_rec = json.loads(out.read_text())
    assert durable_rec == ephemeral_rec


def test_assemble_durable_write_upserts_same_run_id(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")
    state_dir = tmp_path / "state"
    monkeypatch.setattr(rr, "SCHEDULER_STATE_DIR", state_dir)

    out = tmp_path / "run-record.json"
    args = _AssembleArgs(tmp_path, out)
    rr.cmd_assemble(args)  # first write (e.g. on_failure path)
    args.status = "completed"
    rr.cmd_assemble(args)  # second write for the same run_id (success path overwrite)

    durable_path = state_dir / "run-records" / "abc123.json"
    durable_rec = json.loads(durable_path.read_text())
    assert durable_rec["status"] == "completed"
```

Run and confirm failure (no `SCHEDULER_STATE_DIR` attribute on `rr` yet, or the
file simply won't exist):

```bash
cd /workspace/dark-factory && PYTHONPATH=scripts python -m pytest tests/test_run_record.py -k durable -v
```

### Step 4.2 — Implement

Add the module-level `SCHEDULER_STATE_DIR` constant in `run_record.py` immediately
**before** the existing `JSONL_PATH` line (not after — Task 6 makes `JSONL_PATH`
derive from `SCHEDULER_STATE_DIR`, so it must be defined first or the module fails
to import with a `NameError`). This also covers Task 6's hermeticity fix — see
Step 6.2 for the `JSONL_PATH` derivation itself; here only add the new constant if
Task 6 hasn't landed it yet in your working tree:

```python
SCHEDULER_STATE_DIR = pathlib.Path(os.environ.get("SCHEDULER_STATE_DIR", "/var/lib/dark-factory"))
```

Add a helper near `_run_record_path` (after line 45):

```python
def _durable_run_record_path(run_id: str) -> "pathlib.Path | None":
    if not run_id or not _SAFE_RUN_ID_RE.match(run_id):
        return None
    return SCHEDULER_STATE_DIR / "run-records" / f"{run_id}.json"
```

In `cmd_assemble()`, after the existing ephemeral write (`out_file.write_text(...)`,
line 525), add:

```python
    durable_path = _durable_run_record_path(args.run_id)
    if durable_path is not None:
        durable_path.parent.mkdir(parents=True, exist_ok=True)
        durable_path.write_text(json.dumps(run_record, indent=2), encoding="utf-8")
```

Run the Step 4.1 tests again, then the full file:

```bash
cd /workspace/dark-factory && PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v
```

Expected: all pass.

### Step 4.3 — Verify no wiring change needed in entrypoint.sh

This write happens unconditionally inside `cmd_assemble()`, which is already
called from both the success path (`entrypoint.sh:957`) and `on_failure()`
(`entrypoint.sh:592`) — no `entrypoint.sh` change is required for this task; the
"both call sites get a durable record" requirement is satisfied by the shared
`cmd_assemble()` code path, not per-call-site plumbing. Confirm this by grepping:

```bash
grep -n "run-record assemble" entrypoint.sh
```

Expected: exactly 2 matches (success path + `on_failure`), both unchanged from
Task 2.

### Step 4.4 — Commit

```bash
git add scripts/factory_core/run_record.py tests/test_run_record.py
git commit -m "feat(run-record): durable per-run-id record store under SCHEDULER_STATE_DIR (#300)

cmd_assemble() now writes the full record (nodes/totals/harness_economics) to
\${SCHEDULER_STATE_DIR}/run-records/<run_id>.json in addition to the existing
ephemeral \$ARTIFACTS_DIR/run-record.json, on both the success and on_failure
paths. This closes the gap where the only durable sink for run economics was
the (previously broken) GitHub comment, and gives _build_issue_economics /
_backfill_run_economics a real durable path to read (#235)."
```

---

## Task 5: Distinguish `0` from "unmeasured" everywhere

**Files:** `scripts/factory_core/run_record.py`, `tests/test_run_record.py`

### Step 5.1 — Write failing tests for null-vs-zero

Add to `tests/test_run_record.py`, after `test_record_detail_float`:

```python
def test_record_defaults_to_null_not_zero(tmp_path, monkeypatch):
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(rr, "JSONL_PATH", jsonl)
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)

    class _Args:
        run_id = "abc"
        issue = 1
        intent = "fix"
        stage = "implement"
        verdict = "PASS"
        tokens_in = None
        tokens_out = None
        cost_usd = None
        duration_ms = None
        detail = None

    rr.cmd_record(_Args())
    rec = json.loads(jsonl.read_text().strip())
    assert rec["gen_ai.usage.input_tokens"] is None
    assert rec["gen_ai.usage.output_tokens"] is None
    assert rec["cost_usd"] is None
    assert rec["duration_ms"] is None


def test_record_explicit_zero_stays_zero_not_null(tmp_path, monkeypatch):
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(rr, "JSONL_PATH", jsonl)
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)

    class _Args:
        run_id = "abc"
        issue = 1
        intent = "fix"
        stage = "implement"
        verdict = "PASS"
        tokens_in = 0
        tokens_out = 0
        cost_usd = 0.0
        duration_ms = 0
        detail = None

    rr.cmd_record(_Args())
    rec = json.loads(jsonl.read_text().strip())
    assert rec["gen_ai.usage.input_tokens"] == 0
    assert rec["gen_ai.usage.output_tokens"] == 0
```

Add to the `assemble command` section, after `test_assemble_emits_jsonl_per_stage`:

```python
def test_assemble_stage_stub_rows_use_null_not_zero(tmp_path, monkeypatch):
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(rr, "JSONL_PATH", jsonl)
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

    (tmp_path / "validation.md").write_text("STATUS: PASS\n")

    out = tmp_path / "run-record.json"
    args = _AssembleArgs(tmp_path, out)
    rr.cmd_assemble(args)

    line = json.loads(jsonl.read_text().strip().splitlines()[0])
    assert line["gen_ai.usage.input_tokens"] is None
    assert line["gen_ai.usage.output_tokens"] is None
    assert line["cost_usd"] is None
    assert line["duration_ms"] is None
```

Run and confirm these fail (current code hardcodes `0`/`0.0`):

```bash
cd /workspace/dark-factory && PYTHONPATH=scripts python -m pytest tests/test_run_record.py -k "defaults_to_null or explicit_zero_stays or stub_rows_use_null" -v
```

### Step 5.2 — Implement

In `run_record.py`, change `cmd_record()`'s dict construction (lines 118-131):

```python
    record: dict = {
        "run_id": args.run_id,
        "issue_number": args.issue,
        "intent": args.intent,
        "stage": args.stage,
        "verdict": args.verdict,
        "gen_ai.system": "dark-factory",
        "gen_ai.operation.name": f"stage.{args.stage}",
        "gen_ai.usage.input_tokens": args.tokens_in,
        "gen_ai.usage.output_tokens": args.tokens_out,
        "cost_usd": args.cost_usd,
        "duration_ms": args.duration_ms,
        "timestamp": _timestamp(),
    }
```

(drop the `or 0` / `or 0.0` fallbacks — `args.tokens_in` etc. are already `None`
when the CLI flag is omitted, once the argparse defaults below change).

Change the `record` subparser's argument defaults in `main()` (lines 665-668):

```python
    r.add_argument("--tokens-in", type=int, default=None)
    r.add_argument("--tokens-out", type=int, default=None)
    r.add_argument("--cost-usd", type=float, default=None)
    r.add_argument("--duration-ms", type=int, default=None)
```

Change `_post_seq()`'s field reads (lines 77-89) from `record.get(field, 0)` to
`record.get(field)` (no default) so an explicit `null` in the record passes
through unchanged rather than being re-injected as `0`:

```python
                "Properties": {
                    "gen_ai.system": record.get("gen_ai.system", "dark-factory"),
                    "gen_ai.operation.name": record.get(
                        "gen_ai.operation.name",
                        f"stage.{record.get('stage', 'unknown')}",
                    ),
                    "gen_ai.usage.input_tokens": record.get("gen_ai.usage.input_tokens"),
                    "gen_ai.usage.output_tokens": record.get("gen_ai.usage.output_tokens"),
                    "Stage": record.get("stage", ""),
                    "Verdict": record.get("verdict", ""),
                    "IssueNumber": record.get("issue_number", 0),
                    "Intent": record.get("intent", ""),
                    "RunId": record.get("run_id", ""),
                    "CostUsd": record.get("cost_usd"),
                    "DurationMs": record.get("duration_ms"),
                },
```

(`gen_ai.system`/`operation.name`/`Stage`/`Verdict`/`IssueNumber`/`Intent`/`RunId`
are identity/labeling fields, not measured quantities — they keep their existing
string/int defaults; only the four numeric usage/cost/duration fields change.)

Change `cmd_assemble()`'s per-stage stub row construction (lines 528-546):

```python
    ts = _timestamp()
    for stage in stages:
        record: dict = {
            "run_id": args.run_id,
            "issue_number": args.issue,
            "intent": args.intent,
            "stage": stage["stage"],
            "verdict": stage["verdict"],
            "gen_ai.system": "dark-factory",
            "gen_ai.operation.name": f"stage.{stage['stage']}",
            "gen_ai.usage.input_tokens": None,
            "gen_ai.usage.output_tokens": None,
            "cost_usd": None,
            "duration_ms": None,
            "timestamp": ts,
        }
```

Run the Step 5.1 tests again, then the full file:

```bash
cd /workspace/dark-factory && PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v
```

Expected: all pass, including pre-existing tests — check specifically that
`test_record_writes_jsonl` (which passes explicit non-zero values) and
`test_assemble_emits_jsonl_per_stage` (which only checks `stage` names, not
token/cost fields) still pass unmodified.

### Step 5.3 — Update `entrypoint.sh`'s two `run-record record` call sites

Confirm neither existing call (`entrypoint.sh:303` inside
`_handle_session_window_pause`, `entrypoint.sh:579` inside `on_failure`) passes
`--tokens-in`/`--tokens-out`/`--cost-usd`/`--duration-ms` today:

```bash
sed -n '303,309p;579,584p' entrypoint.sh
```

Expected: neither call includes those flags (confirmed already in the spec's
"Root cause confirmed" section) — so both now correctly emit `null` for these
fields with no `entrypoint.sh` change required. If either call is found to pass
one of these flags explicitly with a literal `0` (it should not, per the grep
above), leave it as-is — an explicit `0` from a real caller is exactly the
"measured zero" case Requirement 4 says must stay distinct from unmeasured, so a
caller that already passes `0` deliberately is already correct and must not be
touched.

### Step 5.4 — Commit

```bash
git add scripts/factory_core/run_record.py tests/test_run_record.py
git commit -m "fix(run-record): distinguish 0 from unmeasured across cmd_record/cmd_assemble (#300)

cmd_record's --tokens-in/--tokens-out/--cost-usd/--duration-ms now default to
None (was 0/0.0), and cmd_assemble's per-stage stub rows now emit explicit
null for the same four fields instead of hardcoded zeros. _post_seq no longer
re-injects 0 via .get(field, 0) for these fields, so the null survives to Seq."
```

---

## Task 6: Test hermeticity — fix `JSONL_PATH`, audit bash tests, add a regression guard

**Files:** `scripts/factory_core/run_record.py`, `tests/test_run_record_hermetic.sh` (new), `tests/test_entrypoint_error_signature.sh` (audit only, likely no change), `tests/test_entrypoint_session_window.sh` (audit only, likely no change)

### Step 6.1 — Write a failing test for `JSONL_PATH` env-derivation

Add to `tests/test_run_record.py`, near the top after the imports (new section
before `_RecordArgs`):

```python
# ---------------------------------------------------------------------------
# JSONL_PATH / SCHEDULER_STATE_DIR hermeticity (df#300)
# ---------------------------------------------------------------------------

def test_jsonl_path_derives_from_scheduler_state_dir(monkeypatch):
    import importlib
    monkeypatch.setenv("SCHEDULER_STATE_DIR", "/tmp/fake-state-dir-xyz")
    reloaded = importlib.reload(rr)
    try:
        assert str(reloaded.JSONL_PATH) == "/tmp/fake-state-dir-xyz/runs.jsonl"
        assert str(reloaded.SCHEDULER_STATE_DIR) == "/tmp/fake-state-dir-xyz"
    finally:
        monkeypatch.delenv("SCHEDULER_STATE_DIR", raising=False)
        importlib.reload(rr)  # restore module-level state for subsequent tests
```

Run and confirm failure (`JSONL_PATH` is currently a hardcoded constant, ignoring
the env var):

```bash
cd /workspace/dark-factory && PYTHONPATH=scripts python -m pytest tests/test_run_record.py -k scheduler_state_dir -v
```

### Step 6.2 — Implement the `JSONL_PATH` fix

In `run_record.py`, replace line 22:

```python
JSONL_PATH = pathlib.Path("/var/lib/dark-factory/runs.jsonl")
```

with (also defines `SCHEDULER_STATE_DIR` here if Task 4 didn't already add it in
your working tree — do not duplicate the constant if Step 4.2 already landed it):

```python
SCHEDULER_STATE_DIR = pathlib.Path(os.environ.get("SCHEDULER_STATE_DIR", "/var/lib/dark-factory"))
JSONL_PATH = SCHEDULER_STATE_DIR / "runs.jsonl"
```

Run the Step 6.1 test again, then the full file:

```bash
cd /workspace/dark-factory && PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v
```

Expected: all pass. Note existing tests that do
`monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")` are unaffected —
they override the module attribute directly regardless of how it was computed at
import time.

### Step 6.3 — Add the hermeticity regression-guard test

Create `tests/test_run_record_hermetic.sh`:

```bash
#!/usr/bin/env bash
# Regression guard (df#300): a bash test that shells out to
# `cli.py run-record record|assemble` or `cli.py error-signature-write` without first
# overriding SCHEDULER_STATE_DIR to a temp directory will write to the real
# /var/lib/dark-factory path if ever run outside strict test isolation — this
# already happened once (two `test-run` stub rows landed in production runs.jsonl
# on 2026-07-17). This is a static guard over tests/*.sh, not a runtime check.
#
# Run: bash tests/test_run_record_hermetic.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FAIL=0

for f in "$SCRIPT_DIR"/test_*.sh; do
  base="$(basename "$f")"
  [ "$base" = "test_run_record_hermetic.sh" ] && continue
  if grep -qE 'run-record (record|assemble)|error-signature-write' "$f"; then
    if grep -q 'SCHEDULER_STATE_DIR' "$f"; then
      echo "  PASS: $base sets SCHEDULER_STATE_DIR before invoking run-record/error-signature-write"
    else
      echo "  FAIL: $base invokes run-record/error-signature-write without a SCHEDULER_STATE_DIR override"
      FAIL=1
    fi
  fi
done

echo ""
[ "$FAIL" -eq 0 ] && echo "OK" || echo "FAILED"
[ "$FAIL" -eq 0 ]
```

### Step 6.4 — Run and confirm it passes against the current tree

```bash
chmod +x tests/test_run_record_hermetic.sh
bash tests/test_run_record_hermetic.sh
```

Expected: `PASS` for `test_entrypoint_error_signature.sh` and
`test_entrypoint_session_window.sh` (both already set `SCHEDULER_STATE_DIR` per
the Step 1 audit in this plan's research — confirmed via `grep -n
SCHEDULER_STATE_DIR tests/test_entrypoint_error_signature.sh
tests/test_entrypoint_session_window.sh`), and the new
`tests/test_entrypoint_cost_report_regression.sh` from Task 7 (write that test
with `SCHEDULER_STATE_DIR` set from the start so this guard passes immediately,
not as a follow-up fix). Overall: `OK`.

If any test fails this check, add `SCHEDULER_STATE_DIR=$(mktemp -d ...)` to it
before the offending invocation, following the exact pattern already used in
`tests/test_entrypoint_session_window.sh:59` and
`tests/test_entrypoint_error_signature.sh:65`.

### Step 6.5 — Commit

```bash
git add scripts/factory_core/run_record.py tests/test_run_record.py tests/test_run_record_hermetic.sh
git commit -m "fix(run-record): derive JSONL_PATH from SCHEDULER_STATE_DIR; add hermeticity guard (#300)

JSONL_PATH was the one durable-state path in this repo not following the
existing SCHEDULER_STATE_DIR convention (cli.py, breaker.py, scheduler.sh,
entrypoint.sh all already use it) — bash tests that already set
SCHEDULER_STATE_DIR to a tmpdir were silently ignored by run_record.py's
'record'/'assemble' subcommands, polluting the real production runs.jsonl.
Add test_run_record_hermetic.sh so a future bash test can't reintroduce this."
```

---

## Task 7: Behavioral regression test reproducing the exact reported failure

**Files:** `tests/test_entrypoint_cost_report_regression.sh` (new)

### Step 7.1 — Write the test (red on unpatched code, green after the fix)

This test sources `entrypoint.sh` with stubbed `git`/`gh`/`archon`/`docker`/`claude`
(same pattern as `tests/test_entrypoint_session_window.sh`), then exercises the
**real** `cli.py run-record assemble` (not a hand-authored fixture) with a
simulated bad-pin archon-cost capture (empty output, exit code 127) to produce an
actual `run-record.json` with empty `nodes: []` — the exact bug scenario — and
asserts three things: (1) per the spec's Requirement 6 parenthetical, the durable
record at `${SCHEDULER_STATE_DIR}/run-records/<run_id>.json` is written even
though `nodes` is empty (Task 4's durable-sink requirement, exercised end-to-end
here rather than only via Task 4's unit tests); (2) `post_cost_report()` makes
zero `gh` calls (nothing to post, no fabricated data); (3) `post_cost_report()`
emits the loud `ERROR:` diagnostic line to stderr — the "goes green only when
missing mandatory reports [are logged, not silently skipped]" behavior the spec
requires.

Create `tests/test_entrypoint_cost_report_regression.sh`:

```bash
#!/usr/bin/env bash
# Behavioral regression test (df#300) reproducing the exact reported failure
# signature: a completed run whose run-record.json has empty nodes[] (the bad
# Archon pin's symptom) previously made post_cost_report() log
# "Posting cost report to issue #N..." and then return 0 with ZERO gh calls and
# NO diagnostic — the run looked successful while silently posting nothing.
#
# This must be RED against the pre-fix code (silent `if [ -z "$RUN_ROWS" ]; then
# return; fi`) and GREEN after Task 3's loud-diagnostic fix lands.
#
# Run: bash tests/test_entrypoint_cost_report_regression.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

export GH_TOKEN="stub-token"
export CLAUDE_CODE_OAUTH_TOKEN="stub-token"

GH_CALL_COUNT=0
git() { return 0; }
export -f git
gh() { GH_CALL_COUNT=$((GH_CALL_COUNT+1)); echo "stub-title"; return 0; }
export -f gh
docker() { return 0; }
export -f docker
claude() { echo "stub"; return 0; }
export -f claude
archon() { echo "{}"; return 0; }
export -f archon

ENTRYPOINT_SOURCE_ONLY=1 source "$SCRIPT_DIR/../entrypoint.sh"

trap - ERR
set +e; set +u; set +o pipefail

# CLONE_DIR/dark-factory resolves to REPO_ROOT — see test_entrypoint_session_window.sh
# for why this holds both in this sandbox and under GitHub Actions' checkout layout.
CLONE_DIR="$(dirname "$REPO_ROOT")"
SCHEDULER_STATE_DIR=$(mktemp -d /tmp/ep-cr-statedir-XXXXXX)
ARTIFACTS_DIR=$(mktemp -d /tmp/ep-cr-artifacts-XXXXXX)
ISSUE_NUM=300
INTENT=fix
RUN_ID=test-run-cr-1

PASSED=0; FAILED=0
assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo "  PASS: $desc"; PASSED=$((PASSED+1))
  else
    echo "  FAIL: $desc — expected='$expected' got='$actual'" >&2; FAILED=$((FAILED+1))
  fi
}

# Produce a REAL run-record.json via cli.py run-record assemble, simulating the
# exact bad-pin symptom (archon exits 127, empty stdout) rather than hand-authoring
# the fixture — this exercises Task 2's capture wiring and Task 4's durable sink
# end-to-end, not just Task 3's diagnostic in isolation.
FAIL_COST_JSON=$(mktemp /tmp/ep-cr-costjson-XXXXXX)
FAIL_COST_STDERR=$(mktemp /tmp/ep-cr-coststderr-XXXXXX)
: > "$FAIL_COST_JSON"
echo "archon: command not found" > "$FAIL_COST_STDERR"
python3 "$CLONE_DIR/dark-factory/scripts/factory_core/cli.py" run-record assemble \
  --run-id "$RUN_ID" \
  --issue "$ISSUE_NUM" \
  --intent "$INTENT" \
  --started-at "" \
  --artifacts-dir "$ARTIFACTS_DIR" \
  --archon-cost-json "$FAIL_COST_JSON" \
  --archon-cost-exit-code 127 \
  --archon-cost-stderr-file "$FAIL_COST_STDERR" \
  --out-file "$ARTIFACTS_DIR/run-record.json"
rm -f "$FAIL_COST_JSON" "$FAIL_COST_STDERR"

echo "--- Requirement 3/6: durable record written even though nodes[] is empty ---"
DURABLE_RECORD="${SCHEDULER_STATE_DIR}/run-records/${RUN_ID}.json"
if [ -f "$DURABLE_RECORD" ]; then
  echo "  PASS: durable run-record written at ${DURABLE_RECORD}"
  PASSED=$((PASSED+1))
else
  echo "  FAIL: durable run-record NOT written for an empty-nodes run" >&2
  FAILED=$((FAILED+1))
fi

echo "--- Reproduce: empty nodes[] must not silently succeed ---"
STDERR_FILE=$(mktemp /tmp/ep-cr-stderr-XXXXXX)
post_cost_report 2>"$STDERR_FILE"
RC=$?
assert_eq "post_cost_report returns 0 (non-fatal, run still completes)" "0" "$RC"
assert_eq "zero gh calls made (nothing to post)" "0" "$GH_CALL_COUNT"

if grep -q "ERROR: cost report has zero node rows" "$STDERR_FILE"; then
  echo "  PASS: loud ERROR diagnostic emitted to stderr"
  PASSED=$((PASSED+1))
else
  echo "  FAIL: no loud diagnostic found — this is the exact df#300 silent-skip bug" >&2
  FAILED=$((FAILED+1))
fi

if grep -q "archon_cost_capture.ok=False" "$STDERR_FILE" 2>/dev/null || grep -q "archon_cost_capture.ok=false" "$STDERR_FILE" 2>/dev/null; then
  echo "  PASS: diagnostic surfaces archon_cost_capture evidence"
  PASSED=$((PASSED+1))
else
  echo "  FAIL: diagnostic does not surface archon_cost_capture evidence" >&2
  FAILED=$((FAILED+1))
fi

rm -f "$STDERR_FILE"
rm -rf "$SCHEDULER_STATE_DIR" "$ARTIFACTS_DIR"

echo ""
echo "Results: ${PASSED} passed, ${FAILED} failed"
[ "$FAILED" -eq 0 ]
```

### Step 7.2 — Confirm red-then-green

First, temporarily stash Task 3's fix to prove this test catches the original bug
(do this locally, do not commit the stash step):

```bash
git stash push -- entrypoint.sh
chmod +x tests/test_entrypoint_cost_report_regression.sh
bash tests/test_entrypoint_cost_report_regression.sh; echo "exit=$?"
git stash pop
```

Expected: with Task 3's fix stashed out, the "loud ERROR diagnostic emitted"
assertion FAILs (script exits 1) — proving this test actually reproduces the bug.

Then run for real against the fixed tree:

```bash
bash tests/test_entrypoint_cost_report_regression.sh; echo "exit=$?"
```

Expected: all assertions PASS, `exit=0`.

### Step 7.3 — Run the hermeticity guard against this new file too

```bash
bash tests/test_run_record_hermetic.sh
```

Expected: `OK` — this new test doesn't call `run-record record|assemble` or
`error-signature-write` directly (it calls `post_cost_report` only), but it does
set `SCHEDULER_STATE_DIR` to a tmpdir defensively since `entrypoint.sh` is fully
sourced; the guard's grep only flags files that reference those specific
commands, so this file will not be flagged either way — confirm by re-reading the
guard's output line for this file's name (it should not appear at all, which is
correct: no false positive, no false negative).

### Step 7.4 — Commit

```bash
git add tests/test_entrypoint_cost_report_regression.sh
git commit -m "test(cost-report): add behavioral regression test for the df#300 silent-skip bug (#300)

Reproduces the exact reported failure signature (completed run, empty
nodes[], zero gh calls, no diagnostic) as a sourced-entrypoint behavioral
test rather than a static grep guard. Confirmed red against the pre-fix
silent 'return' and green after Task 3's loud-diagnostic change."
```

---

## Task 8: Reconciliation report and wire everything into CI

**Files:** `scripts/reconcile_cost_reports.py` (new), `tests/test_reconcile_cost_reports.py` (new), `.github/workflows/ci.yml`

### Step 8.1 — Write failing tests for the reconciliation script

Create `tests/test_reconcile_cost_reports.py`:

```python
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import reconcile_cost_reports as rcr


def test_recoverable_run_has_durable_record(tmp_path):
    state_dir = tmp_path / "state"
    (state_dir / "run-records").mkdir(parents=True)
    (state_dir / "run-records" / "run-1.json").write_text(json.dumps({
        "run_id": "run-1", "issue_number": 300, "status": "completed",
        "nodes": [{"node_id": "implement", "cost_usd": 0.5}],
        "totals": {"cost_usd": 0.5},
    }))
    jsonl = state_dir / "runs.jsonl"
    jsonl.write_text(json.dumps({"run_id": "run-1", "issue_number": 300, "stage": "implement"}) + "\n")

    report = rcr.build_reconciliation_report(state_dir=state_dir)

    assert report["recoverable"][0]["run_id"] == "run-1"
    assert report["recoverable"][0]["source"] == "durable_run_record"
    assert report["irrecoverable"] == []


def test_stub_only_run_is_irrecoverable_but_identified(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    jsonl = state_dir / "runs.jsonl"
    jsonl.write_text(json.dumps({
        "run_id": "run-2", "issue_number": 251, "stage": "failed", "timestamp": "2026-07-10T00:00:00Z",
    }) + "\n")

    report = rcr.build_reconciliation_report(state_dir=state_dir)

    assert report["recoverable"] == []
    assert report["irrecoverable"][0]["run_id"] == "run-2"
    assert report["irrecoverable"][0]["reason"] == "stub_only_no_durable_record"
    assert report["irrecoverable"][0]["issue_number"] == 251


def test_empty_state_dir_reports_plainly_not_as_failure(tmp_path):
    state_dir = tmp_path / "empty-state"
    state_dir.mkdir(parents=True)

    report = rcr.build_reconciliation_report(state_dir=state_dir)

    assert report["recoverable"] == []
    assert report["irrecoverable"] == []
    assert report["summary"]  # non-empty human-readable summary, not an exception


def test_irrecoverable_run_flags_ledger_rows_available(tmp_path):
    # Spec item 7 names request-ledger.jsonl/Seq as an additional scan source
    # alongside retained run-record.json files — a stub-only run whose run_id still
    # has rows in the request ledger is not fully recoverable (no node-level
    # cost/token breakdown), but the ledger DOES prove request volume/retry data
    # survived, which is worth flagging separately from "nothing at all survived."
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "runs.jsonl").write_text(json.dumps({
        "run_id": "run-3", "issue_number": 292, "stage": "failed",
    }) + "\n")
    ledger = tmp_path / "request-ledger.jsonl"
    ledger.write_text(json.dumps({"run_id": "run-3", "status": 200}) + "\n")

    report = rcr.build_reconciliation_report(state_dir=state_dir, ledger_path=ledger)

    assert report["irrecoverable"][0]["run_id"] == "run-3"
    assert report["irrecoverable"][0]["ledger_rows_available"] is True


def test_irrecoverable_run_ledger_rows_unavailable_when_no_ledger(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "runs.jsonl").write_text(json.dumps({
        "run_id": "run-4", "issue_number": 292, "stage": "failed",
    }) + "\n")

    report = rcr.build_reconciliation_report(state_dir=state_dir, ledger_path=tmp_path / "no-ledger.jsonl")

    assert report["irrecoverable"][0]["ledger_rows_available"] is False
```

Run and confirm failure (module doesn't exist yet):

```bash
cd /workspace/dark-factory && PYTHONPATH=scripts python -m pytest tests/test_reconcile_cost_reports.py -v
```

### Step 8.2 — Implement `scripts/reconcile_cost_reports.py`

```python
#!/usr/bin/env python3
"""One-shot, read-only reconciliation report for historical cost-report data (df#300).

Scans the durable run-records store and the runs.jsonl stage-event stream to
identify which historical runs are recoverable (a full durable run-record.json
exists) vs. irrecoverable (only a stage-stub row survives, or nothing at all).
Per the spec's item 7, also cross-references request-ledger.jsonl: an
irrecoverable run whose run_id still has ledger rows had its request volume/retry
data survive even though node-level cost/token breakdown did not — this is
flagged, not treated as a second recoverability tier (it's still no
node/harness_economics data). Does not gate anything, does not auto-run in
scheduler.sh/entrypoint.sh — invoke manually:
`python3 scripts/reconcile_cost_reports.py [--state-dir DIR] [--ledger-path PATH]`.
"""
import argparse
import json
import pathlib
import sys


def _load_ledger_run_ids(ledger_path: pathlib.Path) -> set:
    run_ids = set()
    if not ledger_path.exists():
        return run_ids
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        run_id = row.get("run_id")
        if run_id:
            run_ids.add(run_id)
    return run_ids


def _load_run_records(run_records_dir: pathlib.Path) -> dict:
    records = {}
    if not run_records_dir.exists():
        return records
    for path in run_records_dir.glob("*.json"):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        run_id = rec.get("run_id") or path.stem
        records[run_id] = rec
    return records


def _load_jsonl_stubs(jsonl_path: pathlib.Path) -> dict:
    stubs = {}
    if not jsonl_path.exists():
        return stubs
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        run_id = row.get("run_id")
        if not run_id:
            continue
        stubs.setdefault(run_id, []).append(row)
    return stubs


def build_reconciliation_report(*, state_dir: pathlib.Path, ledger_path: "pathlib.Path | None" = None) -> dict:
    run_records = _load_run_records(state_dir / "run-records")
    stubs = _load_jsonl_stubs(state_dir / "runs.jsonl")
    ledger_run_ids = _load_ledger_run_ids(ledger_path) if ledger_path is not None else set()

    recoverable = []
    for run_id, rec in run_records.items():
        recoverable.append({
            "run_id": run_id,
            "issue_number": rec.get("issue_number"),
            "source": "durable_run_record",
            "cost_usd": (rec.get("totals") or {}).get("cost_usd"),
        })

    irrecoverable = []
    for run_id, rows in stubs.items():
        if run_id in run_records:
            continue
        irrecoverable.append({
            "run_id": run_id,
            "issue_number": rows[0].get("issue_number"),
            "reason": "stub_only_no_durable_record",
            "stage_rows": len(rows),
            "ledger_rows_available": run_id in ledger_run_ids,
        })

    total = len(recoverable) + len(irrecoverable)
    if total == 0:
        summary = "No run history found under this state directory — nothing to reconcile."
    else:
        ledger_partial = sum(1 for r in irrecoverable if r["ledger_rows_available"])
        summary = (
            f"{len(recoverable)}/{total} historical runs recoverable via durable "
            f"run-record.json; {len(irrecoverable)}/{total} irrecoverable (stage-stub "
            "rows only — issue number/timestamp survive, node-level cost/token data does "
            f"not), of which {ledger_partial} still have request-ledger rows (request "
            "volume/retry data survives; node/harness_economics breakdown does not)."
        )

    return {"recoverable": recoverable, "irrecoverable": irrecoverable, "summary": summary}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default=None,
                         help="Defaults to $SCHEDULER_STATE_DIR or /var/lib/dark-factory")
    parser.add_argument("--ledger-path", default=None,
                         help="Defaults to $MODEL_PROXY_LEDGER_PATH or /var/lib/dark-factory/request-ledger.jsonl")
    args = parser.parse_args()

    import os
    state_dir = pathlib.Path(args.state_dir or os.environ.get("SCHEDULER_STATE_DIR", "/var/lib/dark-factory"))
    ledger_path = pathlib.Path(
        args.ledger_path or os.environ.get("MODEL_PROXY_LEDGER_PATH", "/var/lib/dark-factory/request-ledger.jsonl")
    )
    report = build_reconciliation_report(state_dir=state_dir, ledger_path=ledger_path)
    json.dump(report, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
```

Run the Step 8.1 tests again:

```bash
cd /workspace/dark-factory && PYTHONPATH=scripts python -m pytest tests/test_reconcile_cost_reports.py -v
```

Expected: all 5 pass.

### Step 8.3 — Wire all new/existing behavioral tests into CI

`.github/workflows/ci.yml`'s `tests` job currently ends (line 21):

```yaml
      - run: bash tests/test_entrypoint_current_run.sh
```

Add, immediately after:

```yaml
      - run: bash tests/test_cost_report_endpoint.sh
      - run: bash tests/test_cost_report_harness_economics.sh
      - run: bash tests/test_run_record_hermetic.sh
      - run: bash tests/test_entrypoint_cost_report_regression.sh
```

Only the two pre-existing cost-report tests the spec names (`test_cost_report_endpoint.sh`,
`test_cost_report_harness_economics.sh`) plus the two new tests from this ticket are
wired in — `tests/test_cost_report_savings.sh` is a pre-existing test the spec does not
name for this ticket's CI wiring; leave it as-is (out of scope for #300, not touched by
this plan) rather than silently expanding Requirement 6's named list.

(`tests/test_reconcile_cost_reports.py` and the extended `tests/test_run_record.py`
cases are already covered by the existing `python -m pytest tests/ -v` line — no
separate CI line needed for `.py` files.)

### Step 8.4 — Verify the full local CI-equivalent run

```bash
cd /workspace/dark-factory
pip install pytest pyyaml aiohttp -q
PYTHONPATH=scripts python -m pytest tests/ -v
bash tests/test_identity.sh
bash tests/test_hooks.sh
bash tests/test_smoke_gate.sh
bash tests/test_run_compose.sh
bash tests/test_model_proxy_compose.sh
bash tests/test_model_proxy_smoke.sh
bash tests/test_entrypoint_current_run.sh
bash tests/test_cost_report_endpoint.sh
bash tests/test_cost_report_harness_economics.sh
bash tests/test_run_record_hermetic.sh
bash tests/test_entrypoint_cost_report_regression.sh
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```

Expected: every command exits 0.

### Step 8.5 — Commit

```bash
git add scripts/reconcile_cost_reports.py tests/test_reconcile_cost_reports.py .github/workflows/ci.yml
git commit -m "feat(observability): add cost-report reconciliation script; wire behavioral tests into CI (#300)

scripts/reconcile_cost_reports.py is a one-shot, read-only report identifying
which historical runs have a recoverable durable run-record vs. only a
stage-stub row (expected: nearly the entire July window is irrecoverable,
since \$ARTIFACTS_DIR was never durable — this ticket only makes the go-forward
path durable). Wires the previously-static-only cost-report tests plus the new
hermeticity guard and behavioral regression test into ci.yml's tests job —
previously none of tests/test_cost_report_*.sh ran in CI at all."
```

---

## Verification Checklist (run before publishing)

```bash
cd /workspace/dark-factory
PYTHONPATH=scripts python -m pytest tests/ -v
for t in test_identity test_hooks test_smoke_gate test_run_compose \
         test_model_proxy_compose test_model_proxy_smoke test_entrypoint_current_run \
         test_cost_report_endpoint test_cost_report_harness_economics \
         test_run_record_hermetic test_entrypoint_cost_report_regression; do
  bash "tests/${t}.sh"
done
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
bash -n entrypoint.sh
docker build -f Dockerfile -t dark-factory:pr-verify .
```

All must exit 0. This mirrors `.github/workflows/ci.yml`'s three jobs (`tests`,
`dag-check`, `docker-build`) exactly, per this repo's `Conventions` section in
`CLAUDE.md`.

## Out of Scope (per spec — do not implement these here)

- Any mechanism blocking a ticket/run's Done/success transition on completion
  predicates (deferred to #197/#198).
- The declarative YAML completion-contract manifest.
- Idempotent delivery outboxes / `delivery_pending` board states.
- Wiring `issue-economics`/`backfill-economics` into `scheduler.sh`/`entrypoint.sh`
  production dispatch (`#235`'s scope) — this plan only ensures the durable data
  they'd read now exists.
- Any change to `deploy/instances/**` or `.github/workflows/publish.yml`.
