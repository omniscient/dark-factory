"""Tests for the replay benchmark suite (issue #335).

Covers:
- suite.json schema validation
- pass^k formula correctness
- BENCH_MODE workflow stub behavior in archon-dark-factory.yaml
- find_eligible.py module importability
"""

import json
import sys
from pathlib import Path

import pytest
import yaml

_BENCH_DIR = Path(__file__).resolve().parents[1] / "bench"
_SUITE_FILE = _BENCH_DIR / "suite.json"
_WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / "workflows" / "archon-dark-factory.yaml"
)
_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# suite.json schema
# ---------------------------------------------------------------------------

class TestSuiteJson:
    def test_suite_file_exists(self):
        assert _SUITE_FILE.exists(), f"suite.json not found at {_SUITE_FILE}"

    def test_suite_loads_as_json(self):
        data = json.loads(_SUITE_FILE.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_suite_has_tasks_key(self):
        data = json.loads(_SUITE_FILE.read_text(encoding="utf-8"))
        assert "tasks" in data, "suite.json must have a 'tasks' key"

    def test_suite_has_at_least_ten_tasks(self):
        data = json.loads(_SUITE_FILE.read_text(encoding="utf-8"))
        assert len(data["tasks"]) >= 10, (
            f"Suite must have ≥10 tasks, got {len(data['tasks'])}"
        )

    def test_each_task_has_required_fields(self):
        required = {"issue", "title", "size", "pre_pr_sha", "golden_pr", "oracle_tests", "oracle_cmd"}
        data = json.loads(_SUITE_FILE.read_text(encoding="utf-8"))
        for task in data["tasks"]:
            missing = required - set(task.keys())
            assert not missing, f"Task #{task.get('issue', '?')} missing fields: {missing}"

    def test_each_task_size_is_valid(self):
        valid_sizes = {"S", "M", "L"}
        data = json.loads(_SUITE_FILE.read_text(encoding="utf-8"))
        for task in data["tasks"]:
            assert task["size"] in valid_sizes, (
                f"Task #{task['issue']} has invalid size: {task['size']!r}"
            )

    def test_each_task_has_oracle_tests(self):
        data = json.loads(_SUITE_FILE.read_text(encoding="utf-8"))
        for task in data["tasks"]:
            assert len(task["oracle_tests"]) >= 1, (
                f"Task #{task['issue']} must have at least one oracle test"
            )

    def test_each_task_oracle_cmd_is_valid(self):
        valid_cmds = {"pytest", "bash", "jest"}
        data = json.loads(_SUITE_FILE.read_text(encoding="utf-8"))
        for task in data["tasks"]:
            assert task["oracle_cmd"] in valid_cmds, (
                f"Task #{task['issue']} has invalid oracle_cmd: {task['oracle_cmd']!r}"
            )

    def test_each_task_pre_pr_sha_is_hex(self):
        data = json.loads(_SUITE_FILE.read_text(encoding="utf-8"))
        for task in data["tasks"]:
            sha = task["pre_pr_sha"]
            assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha), (
                f"Task #{task['issue']} pre_pr_sha is not a 40-char hex SHA: {sha!r}"
            )

    def test_issue_numbers_are_unique(self):
        data = json.loads(_SUITE_FILE.read_text(encoding="utf-8"))
        issues = [t["issue"] for t in data["tasks"]]
        assert len(issues) == len(set(issues)), f"Duplicate issue numbers in suite.json"

    def test_results_gitignore_exists(self):
        gitignore = _BENCH_DIR / ".gitignore"
        assert gitignore.exists(), ".gitignore not found in bench/"
        content = gitignore.read_text(encoding="utf-8")
        assert "results/*.json" in content, ".gitignore must exclude results/*.json"


# ---------------------------------------------------------------------------
# pass^k formula
# ---------------------------------------------------------------------------

class TestPassK:
    def _pass_k(self, c: int, n: int, k: int) -> float:
        return round((c / n) ** k, 4) if n > 0 else 0.0

    def test_perfect_score(self):
        assert self._pass_k(3, 3, 3) == 1.0

    def test_zero_passes(self):
        assert self._pass_k(0, 3, 3) == 0.0

    def test_single_pass_out_of_three(self):
        # (1/3)^3 ≈ 0.037
        result = self._pass_k(1, 3, 3)
        assert abs(result - round((1 / 3) ** 3, 4)) < 1e-6

    def test_seventy_percent_single_run_honest_ceiling(self):
        # 70% single-run success → only ~34% for 3 clean runs
        c = 2  # 2/3 ≈ 67% (close to 70%)
        n = 3
        k = 3
        result = self._pass_k(c, n, k)
        # (2/3)^3 ≈ 0.2963 — under 34%, confirming the harsh pass^k metric
        assert result < 0.40

    def test_n_equals_zero_returns_zero(self):
        assert self._pass_k(0, 0, 3) == 0.0

    def test_k_equals_one_equals_pass_rate(self):
        # pass^1 = c/n (just the single-run pass rate)
        assert abs(self._pass_k(2, 3, 1) - round(2 / 3, 4)) < 1e-6


# ---------------------------------------------------------------------------
# Workflow BENCH_MODE stub behavior
# ---------------------------------------------------------------------------

class TestBenchModeWorkflow:
    def _load_workflow(self) -> dict:
        return yaml.safe_load(_WORKFLOW_PATH.read_text(encoding="utf-8"))

    def _get_node(self, workflow: dict, node_id: str) -> dict:
        for node in workflow.get("nodes", []):
            if node.get("id") == node_id:
                return node
        pytest.fail(f"Node '{node_id}' not found in workflow")

    def test_preview_up_has_bench_mode_guard(self):
        wf = self._load_workflow()
        node = self._get_node(wf, "preview-up")
        bash = node.get("bash", "")
        assert "BENCH_MODE" in bash, (
            "preview-up must check BENCH_MODE to stub preview stack for bench runs"
        )

    def test_preview_up_bench_stub_exits_zero(self):
        wf = self._load_workflow()
        node = self._get_node(wf, "preview-up")
        bash = node.get("bash", "")
        # Stub path must: check stub, write preview_env.sh, exit 0
        assert "stub" in bash, "preview-up must handle BENCH_MODE=stub"
        assert "write_preview_env" in bash or "preview_env.sh" in bash, (
            "preview-up stub must write preview_env.sh for downstream nodes"
        )
        assert "exit 0" in bash, "preview-up stub path must exit 0"

    def test_push_and_pr_has_bench_mode_guard(self):
        wf = self._load_workflow()
        node = self._get_node(wf, "push-and-pr")
        bash = node.get("bash", "")
        assert "BENCH_MODE" in bash, (
            "push-and-pr must check BENCH_MODE to skip push/PR creation in bench runs"
        )

    def test_push_and_pr_bench_stub_exits_zero(self):
        wf = self._load_workflow()
        node = self._get_node(wf, "push-and-pr")
        bash = node.get("bash", "")
        assert "stub" in bash, "push-and-pr must handle BENCH_MODE=stub"
        assert "exit 0" in bash, "push-and-pr stub path must exit 0"

    def test_classify_preview_skipped_in_bench_mode(self):
        """classify-preview must not fire its LLM call when BENCH_MODE=stub.

        The spec (Architecture §2) requires classify-preview to be gated so no
        Haiku call fires during bench runs. Implemented via a bench-mode-probe
        dependency whose output is checked in the when condition.
        """
        wf = self._load_workflow()
        node = self._get_node(wf, "classify-preview")
        when_cond = node.get("when", "")
        deps = node.get("depends_on", [])
        assert "bench-mode-probe" in when_cond, (
            "classify-preview must gate on bench-mode-probe output to prevent "
            "LLM calls during BENCH_MODE=stub replay runs"
        )
        assert "bench-mode-probe" in deps, (
            "classify-preview must depend on bench-mode-probe"
        )

    def test_bench_mode_probe_node_exists(self):
        """bench-mode-probe bash node must exist and output 'stub' when BENCH_MODE=stub."""
        wf = self._load_workflow()
        node = self._get_node(wf, "bench-mode-probe")
        bash = node.get("bash", "")
        assert "BENCH_MODE" in bash, "bench-mode-probe must check BENCH_MODE env var"
        assert "stub" in bash, "bench-mode-probe must output 'stub' for BENCH_MODE=stub"

    def test_gate_nodes_unchanged(self):
        """validate, conformance, code-review, status-in-review, report must not have BENCH_MODE guards."""
        wf = self._load_workflow()
        gate_nodes = ["validate", "conformance", "code-review", "status-in-review", "report"]
        for nid in gate_nodes:
            node = self._get_node(wf, nid)
            # Gate nodes can be 'bash' or 'command' type
            content = node.get("bash", "") + node.get("command", "") + node.get("prompt", "")
            assert "BENCH_MODE" not in content, (
                f"Gate node '{nid}' must NOT have BENCH_MODE guards — it must run unchanged in bench mode"
            )

    def test_or_join_nodes_still_present(self):
        """OR-join nodes must still be present and have correct trigger_rules after our BENCH_MODE additions."""
        from check_workflow_dag import check  # noqa: PLC0415
        errors = check(_WORKFLOW_PATH)
        assert errors == [], (
            f"OR-join check failed after BENCH_MODE modifications:\n" + "\n".join(errors)
        )


# ---------------------------------------------------------------------------
# find_eligible.py importability
# ---------------------------------------------------------------------------

def test_find_eligible_importable():
    """find_eligible.py must be importable without errors."""
    bench_dir = Path(__file__).resolve().parents[1] / "bench"
    if str(bench_dir) not in sys.path:
        sys.path.insert(0, str(bench_dir))
    import find_eligible  # noqa: F401
    assert hasattr(find_eligible, "get_pre_pr_sha")
    assert hasattr(find_eligible, "compute_pass_k" if hasattr(find_eligible, "compute_pass_k") else "verify_fail_pass")


# ---------------------------------------------------------------------------
# --baseline Haiku prose generation
# ---------------------------------------------------------------------------

class TestBaseline:
    _RUN_SUITE = _BENCH_DIR / "run_suite.sh"

    def _non_comment_lines(self) -> list[str]:
        """Return non-comment, non-empty lines from run_suite.sh."""
        lines = []
        for line in self._RUN_SUITE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                lines.append(stripped)
        return lines

    def test_baseline_flag_triggers_prose_generation(self):
        """--baseline must invoke Haiku to generate per-task prose, not just parse the flag."""
        code_lines = self._non_comment_lines()
        code = '\n'.join(code_lines)
        # There must be executable code (not just comments) that branches on BASELINE
        assert '[ "$BASELINE"' in code or '[ "${BASELINE' in code or '"$BASELINE" = "true"' in code or "$BASELINE" in code.replace('BASELINE=true', '').replace('BASELINE=false', ''), (
            "run_suite.sh must use BASELINE variable in executable code"
        )
        # The script must contain actual LLM invocation code (not just comments mentioning it)
        has_claude_invocation = (
            'claude -p' in code or
            'claude --print' in code or
            'anthropic.ai' in code or
            'ANTHROPIC_API_KEY' in code.replace('ANTHROPIC_API_KEY   Required', '')  # not just usage comment
        )
        assert has_claude_invocation, (
            "--baseline must contain executable Haiku invocation (claude -p or Anthropic API call), "
            "not just a comment. The $BASELINE=true code path must actually call the model."
        )

    def test_baseline_writes_to_baseline_md(self):
        """--baseline prose must be written/appended to baseline.md in executable code."""
        code_lines = self._non_comment_lines()
        code = '\n'.join(code_lines)
        # Must write to baseline.md in non-comment code
        assert 'baseline.md' in code, (
            "--baseline must write per-task prose to dark-factory/bench/baseline.md "
            "in executable code (not just a comment)"
        )


def test_find_eligible_has_required_functions():
    bench_dir = Path(__file__).resolve().parents[1] / "bench"
    if str(bench_dir) not in sys.path:
        sys.path.insert(0, str(bench_dir))
    import find_eligible
    for fn in ("get_pre_pr_sha", "get_pr_test_files", "get_size_label", "fetch_closed_issues_with_prs"):
        assert hasattr(find_eligible, fn), f"find_eligible.py missing function: {fn}"


# ---------------------------------------------------------------------------
# run-record wiring (issue #240)
# ---------------------------------------------------------------------------

import os
import subprocess


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
