"""Tests for dark-factory/scripts/load_memory_context.sh."""
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "load_memory_context.sh"


def run_script(phase: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), phase],
        capture_output=True,
        text=True,
        env=env,
    )


def _make_minimal_memory_dir(tmp_path: Path) -> Path:
    """Create a minimal .archon/memory-style dir so memory_retrieve.py can run."""
    mem = tmp_path / "memory"
    mem.mkdir()
    (mem / "codebase-patterns.md").write_text("# Codebase Patterns\n")
    return mem


def base_env(tmp_path: Path) -> dict:
    import os
    e = os.environ.copy()
    e["ARTIFACTS_DIR"] = str(tmp_path)
    e["REPO_ROOT"] = str(REPO_ROOT)
    # Provide a writable memory dir so memory_retrieve.py can run and emit the trace
    e["MEMORY_DIR"] = str(_make_minimal_memory_dir(tmp_path))
    e.pop("ISSUE_NUM", None)
    return e


class TestLoadMemoryContextScript:
    def test_script_exists(self):
        assert SCRIPT.exists(), f"Script not found: {SCRIPT}"

    def test_script_is_executable_or_runnable_via_bash(self):
        result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        assert result.returncode == 0, f"Syntax error: {result.stderr}"

    def test_happy_path_exits_zero(self, tmp_path):
        env = base_env(tmp_path)
        result = run_script("implement", env)
        assert result.returncode == 0, f"Script failed: {result.stderr}"

    def test_outputs_to_stdout(self, tmp_path):
        env = base_env(tmp_path)
        result = run_script("implement", env)
        assert result.returncode == 0
        # stdout may be empty if no relevant memory — that's fine; the key is it exits 0
        # and the stdout is the memory context string (may be empty)
        assert isinstance(result.stdout, str)

    def test_writes_memory_context_md(self, tmp_path):
        env = base_env(tmp_path)
        result = run_script("implement", env)
        assert result.returncode == 0
        ctx_file = tmp_path / "memory-context.md"
        assert ctx_file.exists(), "memory-context.md was not created"

    def test_memory_context_md_matches_stdout(self, tmp_path):
        env = base_env(tmp_path)
        result = run_script("implement", env)
        assert result.returncode == 0
        ctx_file = tmp_path / "memory-context.md"
        # The file content should match stdout (stdout has trailing newline from printf)
        file_content = ctx_file.read_text()
        # stdout may have an extra trailing newline from printf '%s\n'
        assert file_content.rstrip("\n") == result.stdout.rstrip("\n")

    def test_writes_memory_trace_json(self, tmp_path):
        env = base_env(tmp_path)
        result = run_script("implement", env)
        assert result.returncode == 0
        trace_file = tmp_path / "memory-trace.json"
        assert trace_file.exists(), "memory-trace.json was not created"

    def test_trace_json_is_valid_json(self, tmp_path):
        import json
        env = base_env(tmp_path)
        result = run_script("implement", env)
        assert result.returncode == 0
        trace_file = tmp_path / "memory-trace.json"
        data = json.loads(trace_file.read_text())
        assert "schema_version" in data

    def test_different_phases_accepted(self, tmp_path):
        for phase in ("implement", "plan", "refine"):
            tp = tmp_path / phase
            tp.mkdir()
            env = base_env(tp)
            result = run_script(phase, env)
            assert result.returncode == 0, f"Failed for phase={phase}: {result.stderr}"

    def test_issue_num_passed_when_set(self, tmp_path):
        import json
        env = base_env(tmp_path)
        env["ISSUE_NUM"] = "670"
        result = run_script("implement", env)
        assert result.returncode == 0
        trace_file = tmp_path / "memory-trace.json"
        data = json.loads(trace_file.read_text())
        # memory_retrieve.py stores issue as an integer in the trace
        assert data.get("issue") == 670

    def test_fail_soft_when_memory_retrieve_unavailable(self, tmp_path):
        """Script must not abort when memory_retrieve.py fails (fail-soft guarantee)."""
        import os
        env = base_env(tmp_path)
        # Point REPO_ROOT to a temp dir with no memory_retrieve.py → python3 call will fail
        fake_root = tmp_path / "fake_root"
        fake_root.mkdir()
        # Create a minimal git repo so git commands work
        subprocess.run(["git", "init", str(fake_root)], capture_output=True)
        subprocess.run(
            ["git", "remote", "add", "origin", str(fake_root)],
            cwd=str(fake_root), capture_output=True,
        )
        env["REPO_ROOT"] = str(fake_root)
        env["ARTIFACTS_DIR"] = str(tmp_path / "artifacts")
        (tmp_path / "artifacts").mkdir()
        result = run_script("implement", env)
        # Must exit 0 (fail-soft) even when memory_retrieve.py is absent
        assert result.returncode == 0, f"Script aborted instead of failing soft: {result.stderr}"

    def test_creates_artifacts_dir_if_missing(self, tmp_path):
        env = base_env(tmp_path)
        missing_dir = tmp_path / "not_yet_created"
        env["ARTIFACTS_DIR"] = str(missing_dir)
        result = run_script("implement", env)
        assert result.returncode == 0
        assert missing_dir.exists(), "ARTIFACTS_DIR was not created by the script"

    def test_missing_phase_arg_fails(self, tmp_path):
        import os
        env = base_env(tmp_path)
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0, "Script should fail when phase arg is missing"


class TestCloneDirAndCaps:
    """load_memory_context.sh must pass --clone-dir and honour the cap (#486)."""

    def _store(self, tmp_path, n=30):
        mem = tmp_path / "store"
        mem.mkdir()
        (mem / "codebase-patterns.md").write_text(
            "\n".join(f"- [PATTERN] Lesson {i}. <!-- expires:2099-12-31 -->" for i in range(n)) + "\n"
        )
        return mem

    def _clone(self, tmp_path, cfg):
        d = tmp_path / "clone" / ".claude" / "skills" / "refinement"
        d.mkdir(parents=True)
        (d / "config.yaml").write_text("token_optimization:\n  memory:\n" + cfg)
        return tmp_path / "clone"

    def _env(self, tmp_path, mem, clone):
        env = base_env(tmp_path)
        env["MEMORY_DIR"] = str(mem)
        env["CLONE_DIR"] = str(clone)
        env.pop("TOKEN_OPTIMIZATION_MEMORY_ENABLED", None)
        env.pop("TOKEN_OPTIMIZATION_MEMORY_MAX_TOKENS", None)
        return env

    def test_default_cap_applies_to_markdown_store(self, tmp_path):
        mem = self._store(tmp_path)
        clone = self._clone(tmp_path, "    max_entries: 8\n")
        r = run_script("implement", self._env(tmp_path, mem, clone))
        assert r.returncode == 0, r.stderr
        assert r.stdout.count("- [PATTERN]") == 8

    def test_target_config_reaches_retriever(self, tmp_path):
        mem = self._store(tmp_path)
        clone = self._clone(tmp_path, "    enabled: false\n")
        r = run_script("implement", self._env(tmp_path, mem, clone))
        assert r.returncode == 0, r.stderr
        assert r.stdout.count("- [PATTERN]") == 30

    def test_target_config_max_entries_reaches_retriever(self, tmp_path):
        mem = self._store(tmp_path)
        clone = self._clone(tmp_path, "    max_entries: 3\n")
        r = run_script("implement", self._env(tmp_path, mem, clone))
        assert r.stdout.count("- [PATTERN]") == 3

    def test_caps_env_is_honoured(self, tmp_path):
        mem = self._store(tmp_path)
        clone = self._clone(tmp_path, "    max_entries: 8\n")
        # Same `export VAR=N` format budget_enforce.py emits.
        (tmp_path / "token-opt-caps.env").write_text(
            "export TOKEN_OPTIMIZATION_MEMORY_ENABLED=false\n")
        r = run_script("implement", self._env(tmp_path, mem, clone))
        assert r.stdout.count("- [PATTERN]") == 30


class TestRealCapsFileReachesChildProcess:
    """#511: caps written by the REAL budget_enforce.py must reach the Python
    optimizers started by each consumer, i.e. be exported, not just sourced."""

    CAP_VAR = "TOKEN_OPTIMIZATION_MEMORY_MAX_TOKENS"

    def _produce_caps(self, tmp_path):
        import json
        art = tmp_path / "artifacts"
        art.mkdir(exist_ok=True)
        cb = {"scenario": "plan", "sections": {
            "claude_md": {"status": "loaded", "tokens": 500},
            "issue_context": {"status": "loaded", "tokens": 300},
            "architecture_md": {"status": "dropped"},
            "memory_context": {"status": "loaded", "tokens": 1500},
            "comments": {"status": "dropped"},
            "diff": {"status": "dropped"}}}
        (art / "context-budget.json").write_text(json.dumps(cb))
        cfg = tmp_path / "budget-config.yaml"
        cfg.write_text("token_optimization:\n  memory:\n    max_tokens: 1500\n    min_tokens: 100\n")
        out = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "budget_enforce.py"),
             "--context-budget-json", str(art / "context-budget.json"),
             "--budget-tokens", "2600", "--mode", "enforce", "--config", str(cfg)],
            capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        (art / "token-opt-caps.env").write_text(out.stdout)
        return art

    def test_memory_script_child_sees_derived_cap(self, tmp_path):
        art = self._produce_caps(tmp_path)
        store = tmp_path / "store"
        store.mkdir()
        (store / "codebase-patterns.md").write_text(
            "\n".join(f"- [PATTERN] Lesson {i} " + "x" * 200 + " <!-- expires:2099-12-31 -->"
                      for i in range(60)) + "\n")
        clone = tmp_path / "clone" / ".claude" / "skills" / "refinement"
        clone.mkdir(parents=True)
        (clone / "config.yaml").write_text(
            "token_optimization:\n  memory:\n    max_entries: 60\n    max_tokens: 1500\n")
        env = base_env(tmp_path)
        env.update(ARTIFACTS_DIR=str(art), MEMORY_DIR=str(store),
                   CLONE_DIR=str(tmp_path / "clone"))
        env.pop(self.CAP_VAR, None)
        capped = run_script("implement", env).stdout.count("- [PATTERN]")
        (art / "token-opt-caps.env").unlink()
        uncapped = run_script("implement", env).stdout.count("- [PATTERN]")
        assert uncapped > 10
        assert capped < uncapped, "derived memory cap never reached memory_retrieve.py"

    @pytest.mark.parametrize("cmd", ["dark-factory-conformance.md", "dark-factory-code-review.md"])
    def test_command_source_line_exports_to_child(self, tmp_path, cmd):
        art = self._produce_caps(tmp_path)
        text = (REPO_ROOT / "commands" / cmd).read_text(encoding="utf-8")
        line = next(l for l in text.splitlines()
                    if "token-opt-caps.env" in l and l.lstrip().startswith("[ -f"))
        env = base_env(tmp_path)
        env["ARTIFACTS_DIR"] = str(art)
        probe = "import os;print(os.environ.get('%s',''))" % self.CAP_VAR
        r = subprocess.run(["bash", "-c", line + '\npython3 -c "' + probe + '"'],
                           capture_output=True, text=True, env=env)
        assert r.stdout.strip() == "100", r.stdout + r.stderr
