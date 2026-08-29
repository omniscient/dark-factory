# Harness-Swap Replay Benchmark — Implementation Plan

**Issue:** omniscient/dark-factory#240
**Spec:** [docs/superpowers/specs/2026-08-28-harness-swap-replay-benchmark-design.md](../specs/2026-08-28-harness-swap-replay-benchmark-design.md)
**Status:** ready for implementation

## Goal

Wire `run_record.py`'s existing `harness_economics` computation into the existing `bench/`
replay path (Tier A), and extend `evals/skill_flow_eval.py`'s existing retrospective mining
with the same economics columns (Tier B) — closing the gap where `bench/run_suite.sh` bypasses
`entrypoint.sh` and therefore never produces a `run-record.json`. No new replay or economics
machinery; this joins two systems that already exist.

## Architecture

Two independent deliverable tracks over the shared `bench/suite.json` fixture population:

- **Tier A (controlled replay):** `bench/run_suite.sh` gains run-record wiring;
  `bench/compare_variants.py` (new) reads paired arms' run-records and renders the
  promotion/rollback table; `bench/variants.example.yaml` (new) declares the
  `token_optimization.enforce_budgets` worked example.
- **Tier B (retrospective mining):** `evals/skill_flow_eval.py` gains an `--economics-boundary`
  flag and three new economics columns on its existing cost-report mining.

Both tiers report `harness_economics`'s exact field set (`cost_per_task`, `tokens_per_task`,
`wall_clock_seconds`/`wall_clock`, `outcome`, `factory_cpm`) so a reviewer reads one joined
report, not two independently-shaped ones.

## Tech Stack

Python 3 (stdlib + PyYAML, already a test dependency), bash, pytest. No new dependencies.

## File Structure

| File | Change |
|---|---|
| `bench/run_suite.sh` | Modified — wire `run-record assemble`, fix silent-zero cost bug |
| `bench/compare_variants.py` | New — variant-pair comparison CLI |
| `bench/variants.example.yaml` | New — worked-example variant declaration |
| `evals/skill_flow_eval.py` | Modified — economics columns + `--economics-boundary` |
| `tests/test_bench_suite.py` | Modified — run-record wiring tests |
| `tests/test_bench_compare.py` | New — `compare_variants.py` unit tests |
| `tests/fixtures/bench/budget-enforce-off-sample-run-record.json` | New — hand-written run-record fixture |
| `tests/fixtures/bench/budget-enforce-on-sample-run-record.json` | New — hand-written run-record fixture |
| `tests/test_skill_flow_eval.py` | Modified — economics column + boundary tests |

## Preamble note for the implement phase (memory patterns, issues #42 and #242)

This plan and the spec live on the `refine/issue-240-...` branch. Per the recorded
`[PATTERN]` from issue #42: the implement phase must copy
`docs/superpowers/plans/2026-08-29-harness-swap-replay-benchmark-implementation.md` and
`docs/superpowers/specs/2026-08-28-harness-swap-replay-benchmark-design.md` onto the
`feat/issue-240-...` branch and commit them itself — they do not transfer automatically
(`setup-branch` checks `feat/issue-N-slug` out fresh off `main`, per the more specific
`[PATTERN]` recorded under issue #242). See Task 5 Step 5.2 for the exact `git show
origin/refine/...` mechanism (not a plain `git checkout <branch> -- <paths>`, which fails
against a remote-tracking-only branch). The spec stays at its durable
`docs/superpowers/specs/` path (living reference deliverable, per the spec's own header);
only the plan is later renamed into `docs/archive/` once #240 ships.

---

## Task 1 — Fix `bench/run_suite.sh`: wire in `harness_economics`, kill the silent-zero cost bug

### Files
- `bench/run_suite.sh`
- `tests/test_bench_suite.py`

### Step 1.1 — Write failing test: run-record file is produced with a stubbed `archon`

Append to `tests/test_bench_suite.py`:

```python
# ---------------------------------------------------------------------------
# run-record wiring (issue #240)
# ---------------------------------------------------------------------------

import os
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform == "win32", reason="bash/fcntl subprocess test — Linux CI and the factory image only")
class TestRunRecordWiring:
    """Exercises bench/run_suite.sh end-to-end against a stubbed `archon` binary on
    PATH. Requires the `python3` that bash resolves to have `pyyaml` (adapter.py import) and
    `aiohttp` (model_proxy.py, imported by run_record.py) — true on CI (setup-python first on
    PATH) and in the factory image; a red run here is an interpreter mismatch before anything else.
    PATH — the PATH-shim pattern from tests/test_scheduler.sh (PR #366), adapted to a
    Python subprocess test since run_suite.sh is invoked as a real bash subprocess here
    (unlike test_scheduler.sh, which sources scheduler.sh in-process).

    Note: run_suite.sh:61 runs `git config --global --add safe.directory "$REPO_ROOT"` for
    every invocation (Docker host-mount ownership workaround) — this mutates the test
    runner's global gitconfig as a side effect. Harmless (idempotent, additive-only) but
    real; not scoped to the temp repo."""

    def _write_archon_stub(self, bin_dir: Path, *, archon_rc: int = 0) -> None:
        stub = bin_dir / "archon"
        stub.write_text(f"""#!/usr/bin/env bash
set -e
if [ "$1 $2" = "workflow run" ]; then
  ISSUE_ARG="$4"
  ISSUE_NUM=$(echo "$ISSUE_ARG" | grep -oE '[0-9]+')
  git branch "feat/issue-${{ISSUE_NUM}}-bench-stub" 2>/dev/null || true
  exit {archon_rc}
elif [ "$1 $2" = "workflow cost" ]; then
  cat <<'EOF'
{{"run_id": "stub-run", "nodes": [
  {{"nodeId": "implement", "modelUsage": {{"claude-sonnet-4-5-20250929": {{}}}},
   "inputTokens": 100, "outputTokens": 2000, "costUsd": 0.05, "durationMs": 1000}}
]}}
EOF
  exit 0
fi
exit 0
""")
        stub.chmod(0o755)

    def _make_repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        (repo / "bench").mkdir()
        suite = {
            "version": 1,
            "tasks": [{
                "issue": 99999, "title": "stub task", "size": "S",
                "pre_pr_sha": "0" * 40, "golden_pr": 1,
                "oracle_tests": ["tests/does_not_exist.py"], "oracle_cmd": "pytest",
            }],
        }
        (repo / "bench" / "suite.json").write_text(json.dumps(suite))
        (repo / "bench" / ".gitignore").write_text("results/*.json\n__pycache__/\n*.pyc\n")
        run_suite_src = _BENCH_DIR / "run_suite.sh"
        (repo / "bench" / "run_suite.sh").write_text(run_suite_src.read_text())
        (repo / "bench" / "run_suite.sh").chmod(0o755)
        # run-record assemble needs scripts/factory_core/ available at REPO_ROOT
        import shutil
        shutil.copytree(
            Path(__file__).resolve().parents[1] / "scripts",
            repo / "scripts",
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
        # pre_pr_sha must resolve — amend to point at HEAD so `git checkout -f <sha>` works
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()
        suite["tasks"][0]["pre_pr_sha"] = sha
        (repo / "bench" / "suite.json").write_text(json.dumps(suite))
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "pin sha"], cwd=repo, check=True)
        return repo

    def test_run_produces_run_record_with_harness_economics(self, tmp_path):
        repo = self._make_repo(tmp_path)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        self._write_archon_stub(bin_dir)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
               "BENCH_MODE": "stub", "BENCH_TARGET_DIR": str(repo),
               # Hermetic: `run-record assemble` also writes a durable copy to
               # SCHEDULER_STATE_DIR/run-records/ (default /var/lib/dark-factory) — inside a
               # factory container that is the mounted production state volume (#300/#362 class).
               "SCHEDULER_STATE_DIR": str(tmp_path / "state"),
               "MODEL_PROXY_LEDGER_PATH": str(tmp_path / "no-ledger.jsonl"),
               "SEQ_URL": "http://127.0.0.1:9"}
        subprocess.run(
            ["bash", str(repo / "bench" / "run_suite.sh"), "--n", "1", "--issues", "99999",
             "--variant-id", "budget-enforce-on"],
            cwd=repo, env=env, check=True, capture_output=True, text=True,
        )
        records = list((repo / "bench" / "results").glob("*-run-record.json"))
        assert records, "no *-run-record.json written by run_suite.sh"
        data = json.loads(records[0].read_text())
        assert "harness_economics" in data
        assert data["harness_economics"]["cost_per_task"] == pytest.approx(0.05)
        assert data["harness_economics"]["tokens_per_task"] == 2100

        agg = list((repo / "bench" / "results").glob("*-run.json"))
        assert agg, "no aggregate *-run.json written"
        agg_data = json.loads(agg[0].read_text())
        run_entry = agg_data["tasks"][0]["runs"][0]
        assert run_entry["variant_id"] == "budget-enforce-on", (
            "aggregate run entry must carry variant_id verbatim (not parsed from run_id) so "
            "compare_variants.py can join without prefix-collision risk"
        )

    def test_run_status_failed_when_archon_exits_nonzero(self, tmp_path):
        repo = self._make_repo(tmp_path)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        self._write_archon_stub(bin_dir, archon_rc=1)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
               "BENCH_MODE": "stub", "BENCH_TARGET_DIR": str(repo),
               # Hermetic: `run-record assemble` also writes a durable copy to
               # SCHEDULER_STATE_DIR/run-records/ (default /var/lib/dark-factory) — inside a
               # factory container that is the mounted production state volume (#300/#362 class).
               "SCHEDULER_STATE_DIR": str(tmp_path / "state"),
               "MODEL_PROXY_LEDGER_PATH": str(tmp_path / "no-ledger.jsonl"),
               "SEQ_URL": "http://127.0.0.1:9"}
        subprocess.run(
            ["bash", str(repo / "bench" / "run_suite.sh"), "--n", "1", "--issues", "99999"],
            cwd=repo, env=env, check=True, capture_output=True, text=True,
        )
        records = list((repo / "bench" / "results").glob("*-run-record.json"))
        assert records, "no *-run-record.json written on the failure path"
        data = json.loads(records[0].read_text())
        assert data["status"] == "failed"
        assert data["harness_economics"]["outcome"]["state"] == "failed"
        assert data["harness_economics"]["outcome"]["score"] == 0.0

    def test_cost_unavailable_never_coerced_to_zero(self, tmp_path):
        repo = self._make_repo(tmp_path)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "archon"
        stub.write_text("""#!/usr/bin/env bash
if [ "$1 $2" = "workflow run" ]; then
  ISSUE_NUM=$(echo "$4" | grep -oE '[0-9]+')
  git branch "feat/issue-${ISSUE_NUM}-bench-stub" 2>/dev/null || true
  exit 0
elif [ "$1 $2" = "workflow cost" ]; then
  echo "not json" >&2
  exit 1
fi
exit 0
""")
        stub.chmod(0o755)
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
               "BENCH_MODE": "stub", "BENCH_TARGET_DIR": str(repo),
               # Hermetic: `run-record assemble` also writes a durable copy to
               # SCHEDULER_STATE_DIR/run-records/ (default /var/lib/dark-factory) — inside a
               # factory container that is the mounted production state volume (#300/#362 class).
               "SCHEDULER_STATE_DIR": str(tmp_path / "state"),
               "MODEL_PROXY_LEDGER_PATH": str(tmp_path / "no-ledger.jsonl"),
               "SEQ_URL": "http://127.0.0.1:9"}
        subprocess.run(
            ["bash", str(repo / "bench" / "run_suite.sh"), "--n", "1", "--issues", "99999"],
            cwd=repo, env=env, check=True, capture_output=True, text=True,
        )
        run_json = list((repo / "bench" / "results").glob("*-run.json"))
        assert run_json
        data = json.loads(run_json[0].read_text())
        run_entry = data["tasks"][0]["runs"][0]
        assert run_entry["cost_unavailable"] is True
        assert run_entry["cost_cents"] is None


def test_run_suite_syntax_is_valid():
    rc = subprocess.run(["bash", "-n", str(_BENCH_DIR / "run_suite.sh")])
    assert rc.returncode == 0
```

Run: `python -m pytest tests/test_bench_suite.py -k RunRecordWiring -v` → all three tests
**fail** (no `*-run-record.json` is written yet; `variant_id`/`cost_unavailable` keys don't
exist; `status` is never `"failed"`).

### Step 1.2 — Implement: export `ARTIFACTS_DIR`/`RUN_ID`, wire `run-record assemble`, fix the cost bug

Edit `bench/run_suite.sh`:

1. Add `--variant-id` to the header's `# Options:` block, argument parsing, and defaults
   (default empty — non-breaking for existing callers; `compare_variants.py` requires it to
   be set on both arms it compares, see Task 2):

```bash
# Options:
#   --tasks FILE      Path to suite manifest (default: bench/suite.json)
#   --n N             Runs per task (default: 3)
#   --k K             Exponent for pass^k formula (default: same as --n)
#   --baseline        After collecting results, generate Haiku prose summaries and
#                     write/update bench/baseline.md
#   --issues LIST     Comma-separated issue numbers to run (default: all tasks)
#   --dry-run         Print plan without running archon
#   --variant-id ID   Tag this invocation's run-records/aggregate-run rows with a variant_id
#                     (df#240) so bench/compare_variants.py can join two arms without a
#                     run_id-prefix collision. Omit for a plain (non-comparison) bench run.
```
```bash
# Defaults
SUITE_FILE="$BENCH_DIR/suite.json"
N=3
K=""
BASELINE=false
DRY_RUN=false
ISSUE_FILTER=""
VARIANT_ID=""
```
```bash
    --dry-run)  DRY_RUN=true; shift ;;
    --variant-id) VARIANT_ID="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
```

2. Delete `get_last_run_cost_cents()` entirely (lines 139-153) — superseded by
   `harness_economics.cost_per_task`.

3. Replace the per-run body (lines ~208-217, from `# Invoke archon with BENCH_MODE` through
   `check_budget "$COST_CENTS"`) with:

```bash
    # Invoke archon with BENCH_MODE
    RUN_TS_NOW=$(date -u +%Y%m%dT%H%M%S)
    RUN_ID="${VARIANT_ID:+${VARIANT_ID}-}${RUN_TS_NOW}-issue${ISSUE}-r${RUN_IDX}"
    export RUN_ID
    RUN_STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    export RUN_STARTED_AT
    ARTIFACTS_DIR="/tmp/artifacts/bench-${RUN_ID}"
    export ARTIFACTS_DIR
    mkdir -p "$ARTIFACTS_DIR"

    RUN_START=$(date +%s)
    ARCHON_RC=0
    BENCH_MODE="$BENCH_MODE" archon workflow run archon-dark-factory "Fix issue #${ISSUE}" 2>&1 | \
      tee /tmp/bench_archon_${ISSUE}_${RUN_IDX}.log || ARCHON_RC=$?
    RUN_END=$(date +%s)
    DURATION=$(( RUN_END - RUN_START ))

    # --- Capture archon cost data and assemble a run-record (non-fatal) ---
    ARCHON_COST_JSON=$(mktemp)
    ARCHON_COST_STDERR=$(mktemp)
    set +e
    archon workflow cost --last --json --quiet > "$ARCHON_COST_JSON" 2>"$ARCHON_COST_STDERR"
    ARCHON_COST_RC=$?
    set -e

    RUN_RECORD_FILE="$RESULTS_DIR/${RUN_ID}-run-record.json"
    RUN_STATUS="failed"; [ "$ARCHON_RC" -eq 0 ] && RUN_STATUS="completed"
    python3 "$REPO_ROOT/scripts/factory_core/cli.py" run-record assemble \
      --run-id "$RUN_ID" \
      --issue "$ISSUE" \
      --intent implement \
      --started-at "$RUN_STARTED_AT" \
      --artifacts-dir "$ARTIFACTS_DIR" \
      --archon-cost-json "$ARCHON_COST_JSON" \
      --archon-cost-exit-code "$ARCHON_COST_RC" \
      --archon-cost-stderr-file "$ARCHON_COST_STDERR" \
      --out-file "$RUN_RECORD_FILE" \
      --status "$RUN_STATUS" \
      --clone-dir "$REPO_ROOT" || true
    rm -f "$ARCHON_COST_JSON" "$ARCHON_COST_STDERR"

    # cost_per_task is null when archon's cost capture failed — surface that as
    # cost_unavailable, never silently coerce to 0 (df#240; the bug this replaces).
    read -r COST_UNAVAILABLE COST_CENTS < <(python3 -c "
import json
try:
    d = json.load(open('$RUN_RECORD_FILE'))
    c = d.get('harness_economics', {}).get('cost_per_task')
except Exception:
    c = None
if c is None:
    print('true 0')
else:
    print(f'false {int(round(c * 100))}')
" 2>/dev/null || echo "true 0")

    if [ "$COST_UNAVAILABLE" = "true" ]; then
      log "    WARNING: cost unavailable for run $RUN_ID (archon cost capture failed) — not counted toward budget"
    else
      check_budget "$COST_CENTS"
    fi
```

4. Replace the `RUN_RESULT` python3 block (lines ~239-249) so `cost_cents`/`cost_unavailable`
   are real (never a fabricated 0) and `run_id` is carried for `compare_variants.py` to join on:

```bash
    RUN_RESULT=$(python3 -c "
import json
cost_unavailable = '$COST_UNAVAILABLE' == 'true'
cost_cents = None if cost_unavailable else $COST_CENTS
print(json.dumps({
    'run': $RUN_IDX,
    'passed': bool($PASSED),
    'archon_exit': $ARCHON_RC,
    'duration_secs': $DURATION,
    'cost_cents': cost_cents,
    'cost_unavailable': cost_unavailable,
    'result_branch': '${RESULT_BRANCH:-}',
    'run_id': '$RUN_ID',
    'variant_id': '$VARIANT_ID',
}))
")
```

`variant_id` is carried as its own field (not parsed back out of `run_id`'s `${VARIANT_ID}-`
prefix) precisely so `compare_variants.py` can join on an exact field match — a prefix-based
join (`run_id.startswith(f"{vid}-")`) would silently misattribute runs whenever one variant_id
is itself a prefix of another (e.g. `budget-enforce` vs. `budget-enforce-on`).

5. Update the module docstring's `# Output:` block to mention the new file:

```bash
# Output:
#   Per-run JSON: bench/results/YYYY-MM-DD-HH-run.json
#   Per-invocation run-record: bench/results/<run_id>-run-record.json (harness_economics)
#   Summary table: stdout
```

Run: `python -m pytest tests/test_bench_suite.py -k RunRecordWiring -v` → **passes**.
`bash -n bench/run_suite.sh` → exits 0.

### Step 1.3 — Verify full suite still green

```bash
python -m pytest tests/test_bench_suite.py -v
```
All prior tests (suite schema, pass^k, BENCH_MODE workflow guards, `--baseline`) still pass —
none of them exercise the per-run body this task changed.

### Step 1.4 — Commit

```bash
git add bench/run_suite.sh tests/test_bench_suite.py
git commit -m "fix(bench): wire harness_economics into run_suite.sh, kill silent-zero cost bug"
```

---

## Task 2 — `bench/compare_variants.py`: variant-pair comparison CLI

### Files
- `bench/compare_variants.py` (new)
- `tests/test_bench_compare.py` (new)
- `tests/fixtures/bench/*.json` (new)

### Step 2.1 — Write fixtures

Create `tests/fixtures/bench/budget-enforce-off-sample-run-record.json`:

```json
{
  "run_id": "budget-enforce-off-20260829T000000-issue224-r1",
  "issue_number": 224,
  "intent": "implement",
  "started_at": "2026-08-29T00:00:00Z",
  "completed_at": "2026-08-29T00:05:00Z",
  "status": "completed",
  "harness_economics": {
    "policy_version": "1.0",
    "cost_per_task": 0.40,
    "tokens_per_task": 42000,
    "wall_clock_seconds": 300,
    "outcome": {"state": "produced_ungated", "score": 1.0, "evidence": {"status": "completed", "gate_stages": [], "penalties": [], "ungated": true}},
    "factory_cpm": 23.8,
    "retry_spend": {"tokens": null, "request_count": null},
    "failure_spend": {"tokens": 0, "basis": "retry_only"},
    "ledger_available": false,
    "ledger_rows_correlated": 0,
    "ledger_mechanics": null
  }
}
```

Create `tests/fixtures/bench/budget-enforce-on-sample-run-record.json` (same shape, worse
economics — this is the "before" arm, current default):

```json
{
  "run_id": "budget-enforce-on-20260829T000100-issue224-r1",
  "issue_number": 224,
  "intent": "implement",
  "started_at": "2026-08-29T00:01:00Z",
  "completed_at": "2026-08-29T00:07:30Z",
  "status": "completed",
  "harness_economics": {
    "policy_version": "1.0",
    "cost_per_task": 0.55,
    "tokens_per_task": 58000,
    "wall_clock_seconds": 450,
    "outcome": {"state": "produced_ungated", "score": 1.0, "evidence": {"status": "completed", "gate_stages": [], "penalties": [], "ungated": true}},
    "factory_cpm": 17.2,
    "retry_spend": {"tokens": null, "request_count": null},
    "failure_spend": {"tokens": 0, "basis": "retry_only"},
    "ledger_available": false,
    "ledger_rows_correlated": 0,
    "ledger_mechanics": null
  }
}
```

These two are loaded and cloned (with `issue_number`/`run_id` suffix bumped) by the test's
`_build_paired_results()` helper below to synthesize the "two arms x 3 tasks" population the
spec asks for — hand-written shape, programmatically varied values, avoiding six near-identical
checked-in files.

### Step 2.2 — Write failing tests

Create `tests/test_bench_compare.py`:

```python
import copy
import json
import sys
from pathlib import Path

import pytest

_BENCH_DIR = Path(__file__).resolve().parents[1] / "bench"
sys.path.insert(0, str(_BENCH_DIR))
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "bench"

import compare_variants as cv  # noqa: E402


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURES / name).read_text())


def _build_paired_results(tmp_path: Path) -> Path:
    """Three issues x two arms, derived from the two hand-written fixtures by varying
    issue_number/run_id/cost so paired-median has a real distribution to compute over."""
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    off_base = _load_fixture("budget-enforce-off-sample-run-record.json")
    on_base = _load_fixture("budget-enforce-on-sample-run-record.json")
    issues = [224, 332, 289]
    cost_deltas_off = [0.40, 0.10, 0.25]
    cost_deltas_on = [0.55, 0.15, 0.30]
    for idx, (issue, coff, con) in enumerate(zip(issues, cost_deltas_off, cost_deltas_on), start=1):
        off = copy.deepcopy(off_base)
        off["issue_number"] = issue
        off["run_id"] = f"budget-enforce-off-2026082{idx}T000000-issue{issue}-r1"
        off["harness_economics"]["cost_per_task"] = coff
        (results_dir / f"{off['run_id']}-run-record.json").write_text(json.dumps(off))

        on = copy.deepcopy(on_base)
        on["issue_number"] = issue
        on["run_id"] = f"budget-enforce-on-2026082{idx}T000100-issue{issue}-r1"
        on["harness_economics"]["cost_per_task"] = con
        (results_dir / f"{on['run_id']}-run-record.json").write_text(json.dumps(on))

        agg = {
            "tasks": [{
                "issue": issue, "size": "S", "n": 1, "k": 1, "passes": 1, "pass_k": 1.0,
                "runs": [
                    {"run": 1, "passed": True, "run_id": off["run_id"],
                     "variant_id": "budget-enforce-off",
                     "cost_cents": int(coff * 100), "cost_unavailable": False},
                ],
            }],
        }
        (results_dir / f"2026082{idx}T0000-off-run.json").write_text(json.dumps(agg))
        agg2 = copy.deepcopy(agg)
        agg2["tasks"][0]["runs"][0] = {
            "run": 1, "passed": True, "run_id": on["run_id"], "variant_id": "budget-enforce-on",
            "cost_cents": int(con * 100), "cost_unavailable": False,
        }
        (results_dir / f"2026082{idx}T0001-on-run.json").write_text(json.dumps(agg2))
    return results_dir


def _variants_yaml(tmp_path: Path, *, dimension_b: str = "economics") -> Path:
    path = tmp_path / "variants.yaml"
    path.write_text(json.dumps({  # YAML is a JSON superset — valid input for yaml.safe_load
        "variants": [
            {"variant_id": "budget-enforce-on", "dimension": "economics",
             "fixture_set": "bench/suite.json", "env": {}},
            {"variant_id": "budget-enforce-off", "dimension": dimension_b,
             "fixture_set": "bench/suite.json",
             "env": {"TOKEN_OPTIMIZATION_ENFORCE_BUDGETS": "false"}},
        ]
    }))
    return path


def test_paired_median_cost_delta(tmp_path):
    results_dir = _build_paired_results(tmp_path)
    variants = cv.load_variants(_variants_yaml(tmp_path))
    joined = cv.join_variant_results(variants, results_dir)
    delta = cv.paired_median_delta(joined, "cost_per_task")
    # off - on for each paired issue: (0.40-0.55), (0.10-0.15), (0.25-0.30) -> median -0.05
    assert delta == pytest.approx(-0.05)


def test_multiple_runs_per_issue_not_overwritten(tmp_path):
    """bench/run_suite.sh --n 3 (variants.example.yaml's own worked-example usage) produces
    multiple runs per issue per arm. Joining on issue alone would let run 2 silently overwrite
    run 1's entry — join on (issue, run) so both survive as distinct paired data points."""
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    off_base = _load_fixture("budget-enforce-off-sample-run-record.json")
    on_base = _load_fixture("budget-enforce-on-sample-run-record.json")
    off_runs, on_runs = [], []
    for run_idx, (coff, con) in enumerate([(0.40, 0.55), (0.42, 0.50)], start=1):
        off = copy.deepcopy(off_base)
        off["run_id"] = f"budget-enforce-off-ts{run_idx}-issue224-r{run_idx}"
        off["harness_economics"]["cost_per_task"] = coff
        (results_dir / f"{off['run_id']}-run-record.json").write_text(json.dumps(off))
        off_runs.append({"run": run_idx, "passed": True, "run_id": off["run_id"],
                          "variant_id": "budget-enforce-off", "cost_cents": 1, "cost_unavailable": False})

        on = copy.deepcopy(on_base)
        on["run_id"] = f"budget-enforce-on-ts{run_idx}-issue224-r{run_idx}"
        on["harness_economics"]["cost_per_task"] = con
        (results_dir / f"{on['run_id']}-run-record.json").write_text(json.dumps(on))
        on_runs.append({"run": run_idx, "passed": True, "run_id": on["run_id"],
                         "variant_id": "budget-enforce-on", "cost_cents": 1, "cost_unavailable": False})

    (results_dir / "off-run.json").write_text(json.dumps({"tasks": [
        {"issue": 224, "size": "S", "n": 2, "k": 2, "passes": 2, "pass_k": 1.0, "runs": off_runs}
    ]}))
    (results_dir / "on-run.json").write_text(json.dumps({"tasks": [
        {"issue": 224, "size": "S", "n": 2, "k": 2, "passes": 2, "pass_k": 1.0, "runs": on_runs}
    ]}))

    variants = cv.load_variants(_variants_yaml(tmp_path))
    joined = cv.join_variant_results(variants, results_dir)
    assert len(joined["budget-enforce-off"]) == 2, (
        "both runs for issue 224 must survive the join, not just the last one written"
    )
    pairs = cv.paired_values(joined, "cost_per_task")
    assert len(pairs) == 2


def test_cost_unavailable_excluded_not_zeroed(tmp_path):
    results_dir = _build_paired_results(tmp_path)
    # Corrupt one arm's cost to unavailable
    record = next(results_dir.glob("budget-enforce-off-*issue224*-run-record.json"))
    data = json.loads(record.read_text())
    data["harness_economics"]["cost_per_task"] = None
    record.write_text(json.dumps(data))

    variants = cv.load_variants(_variants_yaml(tmp_path))
    joined = cv.join_variant_results(variants, results_dir)
    pairs = cv.paired_values(joined, "cost_per_task")
    # 3 paired issues total, 1 excluded for missing data -> 2 remain
    assert len(pairs) == 2
    delta = cv.paired_median_delta(joined, "cost_per_task")
    assert delta is not None  # still computable from the remaining 2 pairs, never treated as 0


def test_reserved_dimension_raises_named_error(tmp_path):
    results_dir = _build_paired_results(tmp_path)
    variants = cv.load_variants(_variants_yaml(tmp_path, dimension_b="memory_intervention"))
    with pytest.raises(NotImplementedError, match="#241"):
        cv.join_variant_results(variants, results_dir)


def test_contract_trajectory_dimension_names_311(tmp_path):
    results_dir = _build_paired_results(tmp_path)
    variants = cv.load_variants(_variants_yaml(tmp_path, dimension_b="contract_trajectory"))
    with pytest.raises(NotImplementedError, match="#311"):
        cv.join_variant_results(variants, results_dir)


def test_mismatched_non_economics_config_overlay_refuses(tmp_path):
    """A config_overlay top-level key outside token_optimization (e.g. a gate surface) must
    refuse the compare outright, regardless of what the other arm declares."""
    path = tmp_path / "variants.yaml"
    path.write_text(json.dumps({
        "variants": [
            {"variant_id": "a", "dimension": "economics", "fixture_set": "bench/suite.json",
             "config_overlay": {"token_optimization": {"enforce_budgets": True}}},
            {"variant_id": "b", "dimension": "economics", "fixture_set": "bench/suite.json",
             "config_overlay": {"gate_conformance": {"enabled": False}}},
        ]
    }))
    variants = cv.load_variants(path)
    with pytest.raises(ValueError, match="non-economics config"):
        cv.assert_only_economics_keys_differ(variants)


def test_cross_arm_architecture_sub_key_mismatch_refuses(tmp_path):
    """Requirement #4 / Gate criteria: architecture/memory/comments/diff must be held at
    committed defaults in BOTH arms. This only surfaces from a cross-arm comparison — a
    per-arm-only check (the bug the architect review caught) would miss it entirely."""
    path = tmp_path / "variants.yaml"
    path.write_text(json.dumps({
        "variants": [
            {"variant_id": "a", "dimension": "economics", "fixture_set": "bench/suite.json",
             "config_overlay": {"token_optimization": {
                 "enforce_budgets": True, "architecture": {"max_tokens": 3000}}}},
            {"variant_id": "b", "dimension": "economics", "fixture_set": "bench/suite.json",
             "config_overlay": {"token_optimization": {
                 "enforce_budgets": False, "architecture": {"max_tokens": 6000}}}},
        ]
    }))
    variants = cv.load_variants(path)
    with pytest.raises(ValueError, match="architecture"):
        cv.assert_only_economics_keys_differ(variants)


def test_cross_arm_asymmetric_architecture_key_refuses(tmp_path):
    """One arm overrides token_optimization.architecture; the other omits it (implicitly the
    committed default) — this must still refuse, since an intersection-only check would miss
    it (the key isn't in *both* overlays' key sets, only one)."""
    path = tmp_path / "variants.yaml"
    path.write_text(json.dumps({
        "variants": [
            {"variant_id": "a", "dimension": "economics", "fixture_set": "bench/suite.json",
             "config_overlay": {"token_optimization": {
                 "enforce_budgets": True, "architecture": {"max_tokens": 3000}}}},
            {"variant_id": "b", "dimension": "economics", "fixture_set": "bench/suite.json",
             "config_overlay": {"token_optimization": {"enforce_budgets": False}}},
        ]
    }))
    variants = cv.load_variants(path)
    with pytest.raises(ValueError, match="architecture"):
        cv.assert_only_economics_keys_differ(variants)


def test_cross_arm_matching_architecture_sub_key_passes(tmp_path):
    """Same architecture sub-key value in both arms (committed default, held constant) must
    not be flagged — only enforce_budgets (the intended lever) is allowed to differ."""
    path = tmp_path / "variants.yaml"
    path.write_text(json.dumps({
        "variants": [
            {"variant_id": "a", "dimension": "economics", "fixture_set": "bench/suite.json",
             "config_overlay": {"token_optimization": {
                 "enforce_budgets": True, "architecture": {"max_tokens": 3000}}}},
            {"variant_id": "b", "dimension": "economics", "fixture_set": "bench/suite.json",
             "config_overlay": {"token_optimization": {
                 "enforce_budgets": False, "architecture": {"max_tokens": 3000}}}},
        ]
    }))
    variants = cv.load_variants(path)
    cv.assert_only_economics_keys_differ(variants)  # must not raise


def test_fixture_set_mismatch_refuses(tmp_path):
    path = tmp_path / "variants.yaml"
    path.write_text(json.dumps({
        "variants": [
            {"variant_id": "a", "dimension": "economics", "fixture_set": "bench/suite.json"},
            {"variant_id": "b", "dimension": "economics", "fixture_set": "evals/behavioral-state/fixtures"},
        ]
    }))
    variants = cv.load_variants(path)
    with pytest.raises(ValueError, match="fixture_set"):
        cv.assert_only_economics_keys_differ(variants)


def test_non_economics_env_var_mismatch_refuses(tmp_path):
    path = tmp_path / "variants.yaml"
    path.write_text(json.dumps({
        "variants": [
            {"variant_id": "a", "dimension": "economics", "fixture_set": "bench/suite.json",
             "env": {"TOKEN_OPTIMIZATION_ENFORCE_BUDGETS": "true"}},
            {"variant_id": "b", "dimension": "economics", "fixture_set": "bench/suite.json",
             "env": {"TOKEN_OPTIMIZATION_ENFORCE_BUDGETS": "false", "SOME_UNRELATED_VAR": "x"}},
        ]
    }))
    variants = cv.load_variants(path)
    with pytest.raises(ValueError, match="non-economics"):
        cv.assert_only_economics_keys_differ(variants)


def test_join_raises_when_variant_has_no_matching_runs(tmp_path):
    """A variant_id with zero matching aggregate rows (e.g. --variant-id omitted or wrong
    --results-dir) must raise, not silently produce an empty joined population — an empty
    report reads as 'ran and found nothing', which is a materially different, misleading
    signal from 'never ran'."""
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    variants = cv.load_variants(_variants_yaml(tmp_path))
    with pytest.raises(ValueError, match="no matching run"):
        cv.join_variant_results(variants, results_dir)


def test_rollback_tier_zero_for_env_kill_switch():
    variant = {"variant_id": "budget-enforce-off", "env": {"TOKEN_OPTIMIZATION_ENFORCE_BUDGETS": "false"}}
    assert cv.determine_rollback_tier(variant) == "0"


def test_rollback_tier_none_for_image_only_variant():
    variant = {"variant_id": "image-swap", "image": "ghcr.io/omniscient/dark-factory:candidate"}
    assert cv.determine_rollback_tier(variant) == "none"


def test_render_report_includes_all_table_columns(tmp_path):
    results_dir = _build_paired_results(tmp_path)
    variants = cv.load_variants(_variants_yaml(tmp_path))
    joined = cv.join_variant_results(variants, results_dir)
    report = cv.build_report(variants, joined)
    md = cv.render_markdown(report)
    for col in ("variant_id", "outcome delta", "economics delta", "gate verdict",
                "promotion stage", "rollback_tier"):
        assert col in md


def test_build_report_carries_stub_mode_score_and_cpm_not_gate_bearing(tmp_path):
    """Gate-criteria section: outcome.score/factory_cpm are reported ALONGSIDE pass^k but
    flagged stub-mode/not-gate-bearing — must be present in the report and rendered, but must
    never influence gate_verdict (which stays pass^k-only even when both are supplied)."""
    results_dir = _build_paired_results(tmp_path)
    variants = cv.load_variants(_variants_yaml(tmp_path))
    joined = cv.join_variant_results(variants, results_dir)
    report = cv.build_report(variants, joined)
    assert report["outcome_score_after_median"] == pytest.approx(1.0)  # fixtures are produced_ungated
    assert report["factory_cpm_after_median"] == pytest.approx(23.8)  # off-fixture's factory_cpm
    md = cv.render_markdown(report)
    assert "outcome.score" in md
    assert "factory_cpm" in md
    assert "not gate-bearing" in md
```

Run: `python -m pytest tests/test_bench_compare.py -v` → **fails** (`compare_variants` module
doesn't exist yet).

### Step 2.3 — Implement `bench/compare_variants.py`

```python
#!/usr/bin/env python3
"""Compare two harness-swap replay variants over bench/'s run-records — issue #240.

Loads a `--variants variants.yaml` declaration (two arms) plus every `*-run-record.json`
under `--results-dir` (bench/run_suite.sh's per-invocation harness_economics output, wired
in by df#240 Task 1), joins on (issue, run) per variant_id via each aggregate `*-run.json`'s
`runs[].variant_id`/`runs[].run`/`runs[].run_id`, and renders the promotion/rollback table.
Joining on the explicit `variant_id` field (not a `run_id` prefix match) avoids
misattribution when one variant_id is itself a prefix of another; joining on (issue, run)
rather than issue alone keeps every run of a multi-run (`--n > 1`) arm as its own paired data
point instead of collapsing them.

Only `dimension: economics` variants are runnable by this ticket. `memory_intervention`
(#241) and `contract_trajectory` (#311 follow-up) are reserved schema values that must
fail loudly, not silently no-op — see the spec's Reserved dimensions section.
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import yaml

_RESERVED_DIMENSIONS = {
    "memory_intervention": "reserved for #241 (proactive-memory epic — no code yet)",
    "contract_trajectory": "reserved for #311 follow-up (contract/trajectory evalset)",
}

_ECONOMICS_METRICS = ("cost_per_task", "tokens_per_task", "wall_clock_seconds")

# The one env var allowed to differ between arms — everything else must match, or the compare
# is no longer isolating a single economics lever (spec Requirement #4).
_ECONOMICS_ENV_ALLOWLIST = {"TOKEN_OPTIMIZATION_ENFORCE_BUDGETS"}
# The one token_optimization sub-key allowed to differ — architecture/memory/comments/diff/
# budgets etc. must be identical in both arms per the Gate-criteria section.
_ECONOMICS_CONFIG_KEY_ALLOWLIST = {"enforce_budgets"}


def load_variants(path: Path) -> list[dict]:
    data = yaml.safe_load(Path(path).read_text())
    variants = data["variants"]
    if len(variants) != 2:
        raise ValueError(f"compare_variants.py compares exactly 2 arms, got {len(variants)}")
    return variants


def assert_only_economics_keys_differ(variants: list[dict]) -> None:
    """Refuse to compare unless the two arms differ ONLY in the intended economics lever —
    the mechanical enforcement of spec Requirement #4 ('without changing task/model inputs
    unnecessarily') and the Gate-criteria section's instruction to hold architecture/memory/
    comments/diff at committed defaults in both arms. This is a genuine cross-arm comparison,
    not a per-arm allowlist check — two arms can each individually look economics-only while
    still differing from EACH OTHER on an untested axis (e.g. both touch config_overlay.
    token_optimization, but one also changes .architecture) — that must still refuse."""
    if len(variants) != 2:
        raise ValueError("assert_only_economics_keys_differ expects exactly 2 variants")
    a, b = variants

    if a.get("fixture_set") != b.get("fixture_set"):
        raise ValueError(
            f"variants '{a['variant_id']}'/'{b['variant_id']}' use different fixture_set "
            f"values ({a.get('fixture_set')!r} vs {b.get('fixture_set')!r}) — compare refuses"
        )
    if a.get("image") != b.get("image"):
        raise ValueError(
            f"variants '{a['variant_id']}'/'{b['variant_id']}' declare different images — "
            f"compare refuses (image-swap variants are a separate, reserved comparison)"
        )

    for v in (a, b):
        overlay = v.get("config_overlay") or {}
        bad = set(overlay.keys()) - {"token_optimization"}
        if bad:
            raise ValueError(
                f"variant '{v['variant_id']}' config_overlay touches non-economics config "
                f"keys {sorted(bad)} — compare refuses (spec: without changing task/model "
                f"inputs unnecessarily)"
            )
        bad_env = set((v.get("env") or {}).keys()) - _ECONOMICS_ENV_ALLOWLIST
        if bad_env:
            raise ValueError(
                f"variant '{v['variant_id']}' env touches non-economics vars {sorted(bad_env)} "
                f"— compare refuses"
            )

    overlay_a = (a.get("config_overlay") or {}).get("token_optimization", {})
    overlay_b = (b.get("config_overlay") or {}).get("token_optimization", {})
    # Union, not intersection: a key present in ONLY one arm's overlay (the other arm implicitly
    # holds it at the committed default) must still be checked against that default, not skipped.
    # An intersection-only check would miss e.g. arm A overriding .architecture while arm B
    # omits it — silently passing a case the Gate-criteria section requires refused.
    for key in (set(overlay_a) | set(overlay_b)) - _ECONOMICS_CONFIG_KEY_ALLOWLIST:
        val_a, val_b = overlay_a.get(key), overlay_b.get(key)
        if val_a != val_b:
            raise ValueError(
                f"variants '{a['variant_id']}'/'{b['variant_id']}' differ on non-economics "
                f"token_optimization.{key} ({val_a!r} vs {val_b!r}) — compare refuses "
                f"(architecture/memory/comments/diff must be held at committed defaults in "
                f"both arms per spec Gate criteria)"
            )


def determine_rollback_tier(variant: dict) -> str:
    """Tier 0/1/2/none per docs/dark-factory-token-optimization.md's ladder. This ticket's
    worked example only ever produces Tier 0 (env kill-switch) — the Tier 1/2 config_overlay
    branches below are exercised by no fixture in this plan's test suite; they exist so a
    follow-up config_overlay-based variant doesn't need a new function, not because this
    ticket validates their heuristic against a real case."""
    env = variant.get("env") or {}
    if "TOKEN_OPTIMIZATION_ENFORCE_BUDGETS" in env:
        return "0"
    if variant.get("image") and not env:
        # image/deploy-swap variants have no Tier 0 — deploy/** is human-only.
        return "none"
    overlay = (variant.get("config_overlay") or {}).get("token_optimization", {})
    if "enforce" in overlay and len(overlay) == 1 and len(overlay["enforce"]) == 1:
        return "2"  # single-scenario enforce.<x> revert
    if overlay:
        return "1"  # master config revert
    return "none"


def _load_run_records(results_dir: Path) -> dict[str, dict]:
    records = {}
    for f in Path(results_dir).glob("*-run-record.json"):
        data = json.loads(f.read_text())
        records[data["run_id"]] = data
    return records


def _load_aggregate_runs(results_dir: Path) -> list[dict]:
    """Every {issue, run_id, variant_id, passed, pass_k}-shaped row across all *-run.json
    summaries. variant_id comes from the row itself (bench/run_suite.sh's RUN_RESULT block
    writes it verbatim from --variant-id) — never parsed back out of run_id, which would be
    fragile against one variant_id being a prefix of another."""
    rows = []
    for f in Path(results_dir).glob("*-run.json"):
        data = json.loads(f.read_text())
        for task in data.get("tasks", []):
            for run in task.get("runs", []):
                rows.append({
                    "issue": task["issue"],
                    "pass_k": task.get("pass_k"),
                    "run": run["run"],
                    "run_id": run.get("run_id"),
                    "variant_id": run.get("variant_id"),
                    "passed": run.get("passed"),
                })
    return rows


def join_variant_results(variants: list[dict], results_dir: Path) -> dict[str, dict]:
    for v in variants:
        dim = v.get("dimension", "economics")
        if dim in _RESERVED_DIMENSIONS:
            raise NotImplementedError(
                f"dimension '{dim}' is {_RESERVED_DIMENSIONS[dim]} — not runnable by this "
                f"compare_variants.py yet"
            )
        if dim != "economics":
            raise NotImplementedError(f"unknown dimension '{dim}'")
    assert_only_economics_keys_differ(variants)

    run_records = _load_run_records(results_dir)
    agg_rows = _load_aggregate_runs(results_dir)

    joined: dict[str, dict] = {}
    for v in variants:
        vid = v["variant_id"]
        # Key on (issue, run) — not issue alone. bench/run_suite.sh's --n produces multiple
        # runs per issue (variants.example.yaml's own worked-example usage is --n 3); an
        # issue-only key would let run 2 and run 3 silently overwrite run 1's entry, discarding
        # 2/3 of the paired data spec Decisions' "n ≤ 10 paired same-issue runs" methodology
        # expects to see.
        by_run: dict[tuple[int, int], dict] = {}
        for row in agg_rows:
            if row.get("variant_id") != vid:
                continue
            record = run_records.get(row.get("run_id") or "")
            if record is None:
                continue
            by_run[(row["issue"], row["run"])] = {
                "pass_k": row.get("pass_k"),
                "passed": row.get("passed"),
                "harness_economics": record["harness_economics"],
            }
        if not by_run:
            raise ValueError(
                f"no matching runs found for variant_id '{vid}' under {results_dir} — check "
                f"bench/run_suite.sh was invoked with --variant-id {vid!r} and --results-dir "
                f"points at that invocation's output"
            )
        joined[vid] = by_run
    return joined


def paired_values(joined: dict[str, dict], metric: str) -> list[tuple[float, float]]:
    """One (before, after) tuple per (issue, run) key present in both arms with a non-null
    metric — each run is its own paired data point (spec Decisions: 'n <= 10 paired
    same-issue/same-pre_pr_sha runs'), not collapsed to one point per issue.
    variants[0] is 'before' (baseline/current default); variants[1] is 'after'."""
    (before_id, before), (after_id, after) = list(joined.items())[:2]
    pairs = []
    for key, before_row in before.items():
        after_row = after.get(key)
        if after_row is None:
            continue
        b = before_row["harness_economics"].get(metric)
        a = after_row["harness_economics"].get(metric)
        if b is None or a is None:
            continue  # cost_unavailable etc. — never coerced to 0, excluded from this metric only
        pairs.append((b, a))
    return pairs


def paired_median_delta(joined: dict[str, dict], metric: str) -> "float | None":
    pairs = paired_values(joined, metric)
    if not pairs:
        return None
    deltas = [a - b for b, a in pairs]
    return statistics.median(deltas)


def build_report(variants: list[dict], joined: dict[str, dict],
                  outcome_bound: "float | None" = None,
                  improvement_threshold: "float | None" = None) -> dict:
    before_id, after_id = [v["variant_id"] for v in variants][:2]
    economics_deltas = {m: paired_median_delta(joined, m) for m in _ECONOMICS_METRICS}
    outcome_pairs = [
        (joined[before_id][k]["pass_k"], joined[after_id][k]["pass_k"])
        for k in joined[before_id] if k in joined[after_id]
        and joined[before_id][k]["pass_k"] is not None
        and joined[after_id][k]["pass_k"] is not None
    ]
    outcome_delta = (
        statistics.median([a - b for b, a in outcome_pairs]) if outcome_pairs else None
    )

    # Gate-criteria section: outcome.score/factory_cpm are reported ALONGSIDE pass^k (the
    # actual gate metric) but explicitly flagged stub-mode/not-gate-bearing — under
    # BENCH_MODE=stub, outcome is always produced_ungated/1.0 or failed/0.0, so a factory_cpm
    # delta here reflects token spend, not a real quality signal, and must never be read as one.
    def _after_median(getter) -> "float | None":
        vals = [getter(row["harness_economics"]) for row in joined[after_id].values()]
        vals = [v for v in vals if v is not None]
        return statistics.median(vals) if vals else None

    outcome_score_after_median = _after_median(lambda he: he.get("outcome", {}).get("score"))
    factory_cpm_after_median = _after_median(lambda he: he.get("factory_cpm"))

    gate_verdict = "ungated (thresholds not pinned)"
    if outcome_bound is not None and improvement_threshold is not None and outcome_delta is not None:
        cost_delta = economics_deltas.get("cost_per_task")
        gate_verdict = "pass" if (
            outcome_delta >= outcome_bound and cost_delta is not None and cost_delta <= -improvement_threshold
        ) else "fail"

    return {
        "variant_id": after_id,
        "baseline_variant_id": before_id,
        "outcome_delta_pass_k": outcome_delta,
        "economics_delta": economics_deltas,
        # Stub-mode, not gate-bearing — see Gate-criteria section. Reported for visibility only;
        # never fed into gate_verdict above (that uses outcome_delta_pass_k, the real quality
        # metric, exactly as the spec requires).
        "outcome_score_after_median": outcome_score_after_median,
        "factory_cpm_after_median": factory_cpm_after_median,
        "gate_verdict": gate_verdict,
        # Comment 2 ladder: replay -> shadow -> advisory -> blocking. Every report this ticket's
        # compare_variants.py can produce is replay-tier evidence by construction (bench/'s
        # stub-mode replay is the only data source wired in); advancing a variant past "replay"
        # requires shadow/advisory run data this ticket does not collect, so the stage is fixed,
        # not computed.
        "promotion_stage": "replay",
        "rollback_tier": determine_rollback_tier(
            next(v for v in variants if v["variant_id"] == after_id)
        ),
    }


def render_markdown(report: dict) -> str:
    econ = report["economics_delta"]
    econ_str = "; ".join(
        f"{k}: {v:+.4g}" if v is not None else f"{k}: n/a" for k, v in econ.items()
    )
    outcome = (
        f"{report['outcome_delta_pass_k']:+.4f}"
        if report["outcome_delta_pass_k"] is not None else "n/a"
    )
    score = report.get("outcome_score_after_median")
    cpm = report.get("factory_cpm_after_median")
    stub_note = (
        "\n\n> Economics are agent-phase only — `BENCH_MODE=stub` skips `preview-up`/"
        "`push-and-pr`, so absolute `cost_per_task`/`tokens_per_task` are a lower bound "
        "relative to a production run. The delta between the two arms above (both run under "
        "the identical stub configuration) remains valid; do not compare these absolute "
        "figures to a production `dark-factory-cost-report` total without a documented offset.\n"
        f">\n> **outcome.score (after, median):** {score if score is not None else 'n/a'} — "
        f"**factory_cpm (after, median):** {cpm if cpm is not None else 'n/a'} — both stub-mode, "
        "not gate-bearing; the gate verdict above is computed from `pass^k` alone.\n"
    )
    return (
        "| variant_id | outcome delta (pass^k) | economics delta | gate verdict | "
        "promotion stage | rollback_tier |\n"
        "|---|---|---|---|---|---|\n"
        f"| {report['variant_id']} | {outcome} | {econ_str} | {report['gate_verdict']} | "
        f"{report['promotion_stage']} | {report['rollback_tier']} |\n"
        f"{stub_note}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variants", required=True, type=Path)
    parser.add_argument("--results-dir", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--outcome-non-inferiority-bound", type=float, default=None)
    parser.add_argument("--improvement-threshold", type=float, default=None)
    args = parser.parse_args()

    variants = load_variants(args.variants)
    joined = join_variant_results(variants, args.results_dir)
    report = build_report(
        variants, joined,
        outcome_bound=args.outcome_non_inferiority_bound,
        improvement_threshold=args.improvement_threshold,
    )

    if args.out.suffix == ".json":
        args.out.write_text(json.dumps(report, indent=2))
    else:
        args.out.write_text(render_markdown(report))
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
```

Run: `python -m pytest tests/test_bench_compare.py -v` → **passes**.

### Step 2.4 — Commit

```bash
git add bench/compare_variants.py tests/test_bench_compare.py tests/fixtures/bench/
git commit -m "feat(bench): add compare_variants.py — paired-median economics/outcome delta CLI"
```

---

## Task 3 — `bench/variants.example.yaml`: the worked example

### Files
- `bench/variants.example.yaml` (new)

### Step 3.1 — Write the file

```yaml
# Worked example for bench/compare_variants.py — issue #240.
# Two arms, one suite, one env var: token_optimization.enforce_budgets on vs. off.
# Arm B (off) is the only reachable "disabled" arm — TOKEN_OPTIMIZATION_ENFORCE_BUDGETS is a
# kill-only override (scripts/budget_gate.sh:50-51); it cannot force enforcement on, so Arm A
# (on) is unset env / committed config as-is, not a second explicit override.
#
# Fixture-health caveat (spec Architecture §Tier A item 5): ~6/10 bench/suite.json tasks
# currently score "expected-fail-both" on outcome for unrelated pre-extraction path/tooling
# reasons (docs/parity-p2.md §4a), leaving ~3-4 S-bucket tasks with real outcome-discriminating
# power. Economics (tokens/cost/wall-clock) remains measurable on all 10 regardless — only the
# outcome/quality delta should be read against the known-good subset, or re-locked first via
# bench/find_eligible.py.
#
# Usage (operator-run execution follow-up, not run in CI):
#   bench/run_suite.sh --variant-id budget-enforce-on  --issues 224,332,289 --n 3
#   TOKEN_OPTIMIZATION_ENFORCE_BUDGETS=false bench/run_suite.sh --variant-id budget-enforce-off --issues 224,332,289 --n 3
#   python3 bench/compare_variants.py --variants bench/variants.example.yaml \
#     --results-dir bench/results --out bench/results/enforce-budgets-report.md
variants:
  - variant_id: budget-enforce-on
    dimension: economics
    fixture_set: bench/suite.json
    env: {}
  - variant_id: budget-enforce-off
    dimension: economics
    fixture_set: bench/suite.json
    env:
      TOKEN_OPTIMIZATION_ENFORCE_BUDGETS: "false"
```

### Step 3.2 — Test the file loads and validates

Add to `tests/test_bench_compare.py`:

```python
def test_variants_example_yaml_loads_and_validates():
    example = _BENCH_DIR / "variants.example.yaml"
    variants = cv.load_variants(example)
    assert [v["variant_id"] for v in variants] == ["budget-enforce-on", "budget-enforce-off"]
    cv.assert_only_economics_keys_differ(variants)  # must not raise
    assert cv.determine_rollback_tier(variants[1]) == "0"
```

Run: `python -m pytest tests/test_bench_compare.py -k variants_example -v` → **passes**
(this is a smoke test on a real file, not a TDD-red step — `load_variants`/
`assert_only_economics_keys_differ` already exist from Task 2).

### Step 3.3 — Commit

```bash
git add bench/variants.example.yaml tests/test_bench_compare.py
git commit -m "feat(bench): add variants.example.yaml — enforce_budgets on/off worked example"
```

---

## Task 4 — `evals/skill_flow_eval.py`: economics columns + `--economics-boundary`

### Files
- `evals/skill_flow_eval.py`
- `tests/test_skill_flow_eval.py`

(The scorecard renderer `evals/skill_flow_scorecard.py` is NOT touched: the spec's Deliverables
name only the mined columns and the `--economics-boundary` flag, and there is no call site for a
rendered economics section yet. Rendering is a follow-up when a consumer exists.)

### Step 4.1 — Write failing test: new columns on `_cost_report_pr_stats`

`mine_cost_report_population` filters to factory PRs first (`fsc.is_factory_pr()`, which reads
`pr["commits"][].authors[].email` against `fsc.FACTORY_EMAIL`) — reuse the file's existing
`_factory_pr(number, issue, merged_at)` helper and `FACTORY_EMAIL` monkeypatch (see
`test_mine_cost_report_population_parses_real_example` a few lines above) rather than a
hand-rolled PR dict, or the PR is filtered out before it ever reaches the cost-report parser and
`avg_input_tokens`/`avg_output_tokens` stay `None`.

Also fix the two **pre-existing** tests this change breaks: both
`test_mine_cost_report_population_parses_real_example` and
`test_mine_cost_report_population_skips_pr_with_no_cost_comment` assert the full
`_cost_report_pr_stats()` dict with `==` against a literal that will no longer match once the
three new keys are added. Extend both literals in the same edit — otherwise Step 4.6's full-suite
run goes red on tests this task didn't intend to touch.

Append to `tests/test_skill_flow_eval.py`:

```python
def test_cost_report_pr_stats_includes_economics_columns(monkeypatch):
    """mine_cost_report_population must carry the same cost_per_task/tokens_per_task/
    wall_clock columns Tier A's harness_economics produces (issue #240 Tier B), computed
    from the same avg_* fields _cost_report_pr_stats already returns."""
    prs = [_factory_pr(1, 73, "2026-07-10T18:00:00Z")]
    monkeypatch.setattr(sfe.fsc, "FACTORY_EMAIL", "factory@dark-factory")
    monkeypatch.setattr(sfe, "_fetch_issue_comments", lambda repo, num: [{"body": _REAL_COST_REPORT_BODY}])

    boundary = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
    result = sfe.mine_cost_report_population("omniscient/dark-factory", prs, boundary, "implement")
    stats = result["after"]
    assert stats["n_with_data"] == 1  # sanity: the PR must actually be counted, not filtered out
    assert stats["tokens_per_task"] == pytest.approx(stats["avg_input_tokens"] + stats["avg_output_tokens"])
    assert stats["cost_per_task"] == pytest.approx(stats["avg_cost_usd"])
    assert stats["wall_clock"] == pytest.approx(stats["avg_duration_ms"] / 1000)


def test_cost_report_pr_stats_economics_columns_null_when_no_data(monkeypatch):
    prs = [_factory_pr(1, 73, "2026-07-10T18:00:00Z")]
    monkeypatch.setattr(sfe.fsc, "FACTORY_EMAIL", "factory@dark-factory")
    monkeypatch.setattr(sfe, "_fetch_issue_comments", lambda repo, num: [{"body": "no cost report here"}])

    boundary = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
    result = sfe.mine_cost_report_population("omniscient/dark-factory", prs, boundary, "implement")
    stats = result["after"]
    assert stats["n_with_data"] == 0  # sanity: this is the "PR has no parseable cost data" path
    assert stats["tokens_per_task"] is None
    assert stats["cost_per_task"] is None
    assert stats["wall_clock"] is None
```

Update the two pre-existing exact-equality assertions in place:

```python
# test_mine_cost_report_population_parses_real_example — extend the "before" literal:
    assert result["before"] == {
        "n_total": 0, "n_with_data": 0,
        "avg_input_tokens": None, "avg_output_tokens": None,
        "avg_duration_ms": None, "avg_cost_usd": None,
        "tokens_per_task": None, "cost_per_task": None, "wall_clock": None,
    }

# test_mine_cost_report_population_skips_pr_with_no_cost_comment — extend the "after" literal:
    assert result["after"] == {
        "n_total": 1, "n_with_data": 0,
        "avg_input_tokens": None, "avg_output_tokens": None,
        "avg_duration_ms": None, "avg_cost_usd": None,
        "tokens_per_task": None, "cost_per_task": None, "wall_clock": None,
    }
```

Run: `python -m pytest tests/test_skill_flow_eval.py -k "economics_columns or parses_real_example or skips_pr_with_no_cost_comment" -v`
→ the two new tests **fail** with `KeyError: 'tokens_per_task'`; the two pre-existing tests
**fail** with a dict-equality mismatch (extra keys not yet produced by the literal's assumed
shape — i.e. they're red for the right reason, not yet passing).

### Step 4.2 — Implement: add the three columns to `_cost_report_pr_stats`

Edit `evals/skill_flow_eval.py`, in `_cost_report_pr_stats` (both return branches):

```python
    n_with_data = len(per_pr_avgs)
    if n_with_data == 0:
        return {
            "n_total": n_total, "n_with_data": 0,
            "avg_input_tokens": None, "avg_output_tokens": None,
            "avg_duration_ms": None, "avg_cost_usd": None,
            "tokens_per_task": None, "cost_per_task": None, "wall_clock": None,
        }
    avg_input = sum(p["input_tokens"] for p in per_pr_avgs) / n_with_data
    avg_output = sum(p["output_tokens"] for p in per_pr_avgs) / n_with_data
    avg_duration = sum(p["duration_ms"] for p in per_pr_avgs) / n_with_data
    avg_cost = sum(p["cost_usd"] for p in per_pr_avgs) / n_with_data
    return {
        "n_total": n_total,
        "n_with_data": n_with_data,
        "avg_input_tokens": avg_input,
        "avg_output_tokens": avg_output,
        "avg_duration_ms": avg_duration,
        "avg_cost_usd": avg_cost,
        # Same semantics as run_record.py's harness_economics (df#240 Tier A): tokens_per_task
        # = input+output, cost_per_task = cost_usd, wall_clock = duration in seconds (not ms).
        "tokens_per_task": avg_input + avg_output,
        "cost_per_task": avg_cost,
        "wall_clock": avg_duration / 1000,
    }
```

Run: `python -m pytest tests/test_skill_flow_eval.py -k economics_columns -v` → **passes**.

### Step 4.3 — Write failing test: `--economics-boundary` flag exists and is optional

```python
def test_build_arg_parser_has_economics_boundary_default_none():
    args = sfe.build_arg_parser().parse_args([])
    assert args.economics_boundary is None


def test_build_arg_parser_economics_boundary_override():
    args = sfe.build_arg_parser().parse_args(["--economics-boundary", "abc123"])
    assert args.economics_boundary == "abc123"
```

Run → **fails** (`unrecognized arguments: --economics-boundary`).

### Step 4.4 — Implement the flag and `run()` wiring

Add to `build_arg_parser()`:

```python
    parser.add_argument(
        "--economics-boundary", default=None,
        help=(
            "Commit SHA bracketing an enforce_budgets enforcement-live boundary "
            "(df#240 Tier B). Run once per boundary — refine/plan share the T3b commit "
            "(#733), conformance/code-review share the T6 commit (see config.yaml's enforce block comments); this flag takes "
            "one SHA per invocation, not a pair, so the operator runs the script twice."
        ),
    )
```

Add a node map and mining call in `run()`, just before `if not args.no_cross_repo:`:

```python
    # ── df#240 Tier B: economics-boundary mining (optional; never runs unless requested,
    # and never in CI — this hits the live gh REST API like everything else in run()). ──
    _ECONOMICS_NODES: dict[str, tuple[str, str | None]] = {
        "refine": ("refine", None),
        "plan": ("plan", None),
        "implement": ("implement", None),
        "conformance": ("conformance", None),
        "code-review": ("code-review", None),
    }
    if args.economics_boundary:
        econ_boundary = merge_boundary_date(args.repo_root, args.economics_boundary)
        report["economics"] = {
            scenario: mine_cost_report_population(
                args.repo, windowed_prs, econ_boundary, node_id, intent_filter=intent_filter
            )
            for scenario, (node_id, intent_filter) in _ECONOMICS_NODES.items()
        }
```

(Module-level constant placement: move `_ECONOMICS_NODES` above `run()`, next to the existing
`_TIER2_COST_REPORT_NODE` table, rather than inline — mirrors that table's placement.)

Run: `python -m pytest tests/test_skill_flow_eval.py -k economics_boundary -v` → **passes**.

### Step 4.5 — Write failing test: `run()` populates `report["economics"]` only when requested

```python
def test_run_omits_economics_key_when_boundary_not_passed(monkeypatch, tmp_path):
    monkeypatch.setattr(sfe.fsc, "fetch_prs", lambda: [])
    monkeypatch.setattr(sfe, "mine_conformance_population", lambda *a, **k: {"before": {}, "after": {}})
    monkeypatch.setattr(sfe, "mine_code_review_population", lambda *a, **k: {"before": {}, "after": {}})
    monkeypatch.setattr(sfe, "mine_cost_report_population", lambda *a, **k: {"before": {}, "after": {}})
    monkeypatch.setattr(sfe, "mine_label_incidence", lambda *a, **k: {"before": {}, "after": {}})
    monkeypatch.setattr(sfe, "merge_boundary_date", lambda *a, **k: datetime(2026, 1, 1, tzinfo=timezone.utc))
    args = sfe.build_arg_parser().parse_args(["--no-cross-repo", "--repo-root", str(tmp_path)])
    report = sfe.run(args)
    assert "economics" not in report


def test_run_includes_economics_key_when_boundary_passed(monkeypatch, tmp_path):
    monkeypatch.setattr(sfe.fsc, "fetch_prs", lambda: [])
    monkeypatch.setattr(sfe, "mine_conformance_population", lambda *a, **k: {"before": {}, "after": {}})
    monkeypatch.setattr(sfe, "mine_code_review_population", lambda *a, **k: {"before": {}, "after": {}})
    monkeypatch.setattr(sfe, "mine_cost_report_population", lambda *a, **k: {"before": {}, "after": {}})
    monkeypatch.setattr(sfe, "mine_label_incidence", lambda *a, **k: {"before": {}, "after": {}})
    monkeypatch.setattr(sfe, "merge_boundary_date", lambda *a, **k: datetime(2026, 1, 1, tzinfo=timezone.utc))
    args = sfe.build_arg_parser().parse_args(
        ["--no-cross-repo", "--repo-root", str(tmp_path), "--economics-boundary", "deadbeef"]
    )
    report = sfe.run(args)
    assert set(report["economics"].keys()) == {"refine", "plan", "implement", "conformance", "code-review"}
```

Run → these should already **pass** after Step 4.4 (they pin the behavior down; if either
fails, it means the wiring landed in the wrong place relative to `report[...]` assembly —
fix by ensuring the economics block is added to the same `report` dict returned at the end
of `run()`).

### Step 4.6 — Verify full eval suite green

```bash
python -m pytest tests/test_skill_flow_eval.py -v
```

### Step 4.7 — Commit

```bash
git add evals/skill_flow_eval.py tests/test_skill_flow_eval.py
git commit -m "feat(evals): extend skill_flow_eval.py with economics columns + --economics-boundary (df#240 Tier B)"
```

---

## Task 5 — Full-suite verification

### Step 5.1 — Run the complete test suite plus bash suites (CI parity)

Exact commands from `.github/workflows/ci.yml`'s `tests` and `dag-check` jobs (root-relative —
this repo's own `scripts/`, not the `$CLONE_DIR/dark-factory/scripts/` container-clone TARGET-PATH
used inside `entrypoint.sh`/DAG bash nodes):

```bash
PYTHONPATH=scripts python -m pytest tests/ -v
python -m pytest tests/ -q
bash -n bench/run_suite.sh
bash tests/test_smoke_gate.sh
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```

Expected: all pytest tests pass (existing + new); `bash -n bench/run_suite.sh` clean (covered by
Task 1's `test_run_suite_syntax_is_valid`); `check_workflow_dag.py`/`check_workflow_when.py`
report no violations (this plan does not touch `workflows/archon-dark-factory.yaml`).

### Step 5.2 — Copy the spec and plan onto the `feat/issue-240-...` branch and commit

Per the recorded `[PATTERN]` from issue #242 (`.archon/memory/dark-factory-ops.md`):
`setup-branch` for a `new`-intent implement run checks out `feat/issue-N-slug` fresh off
`main` — it does **not** inherit commits from the `refine/issue-N-slug` branch, so a plain
`git checkout <branch> -- <paths>` fails (`refine/issue-240-...` exists only as
`refs/remotes/origin/refine/...` in this checkout, and `checkout <tree-ish> -- <pathspec>`
does not resolve a bare local branch name the way `git checkout <branch>` alone does). Use
`git show` against the remote-tracking ref instead, per the #242 precedent (commit `fc9ca0c`):

```bash
git show origin/refine/issue-240-test-economics---harness-swap-replay-ben:docs/superpowers/specs/2026-08-28-harness-swap-replay-benchmark-design.md \
  > docs/superpowers/specs/2026-08-28-harness-swap-replay-benchmark-design.md
git show origin/refine/issue-240-test-economics---harness-swap-replay-ben:docs/superpowers/plans/2026-08-29-harness-swap-replay-benchmark-implementation.md \
  > docs/superpowers/plans/2026-08-29-harness-swap-replay-benchmark-implementation.md
git add docs/superpowers/specs/2026-08-28-harness-swap-replay-benchmark-design.md \
  docs/superpowers/plans/2026-08-29-harness-swap-replay-benchmark-implementation.md
git commit -m "docs(#240): bring over approved spec + plan for issue #240"
```

### Step 5.3 — Confirm no out-of-scope files touched

Three-dot (merge-base) form, per the recorded `[PATTERN]` from issue #266 — this specific
refinement of the #250 entry applies to changed-file-**set** detection (deciding which files
this branch itself touched), as opposed to #250's single-file content-equality use case.
Two-dot here would flag/miss files `main` changed independently after this branch forked
(the exact #266/`oos_excise.sh` regression); every other gate command in this repo
(`scripts/oos_excise.sh`, `scripts/push_gate_check.sh`) already uses three-dot for this
reason:

```bash
git diff --stat origin/main...HEAD
```

Expected file set: the eleven files in the File Structure table above, plus the two docs
copied in Step 5.2 (thirteen files total).

---

## Non-goals (carried from spec — do not implement here)

- No new DAG node, gate, breaker, budget, or `config/config.yaml` change.
- No live n≥1 bench execution or filled-in results table — that is an operator-run follow-up
  (`bench/variants.example.yaml`'s usage comment documents the exact commands).
- No `dimension: memory_intervention` or `dimension: contract_trajectory` implementation —
  both must raise `NotImplementedError` naming their tracking ticket (Task 2 covers this).
- No changes to `evals/token_opt_eval.py` — the "optional validity check" comparing Tier A's
  measured delta against `simulate_enforcement()`'s offline prediction is explicitly optional
  in the spec and out of scope for this plan's deliverables.
