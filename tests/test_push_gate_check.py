"""Tests for scripts/push_gate_check.sh — git-aware committed-artifact existence check
used by refine-push/plan-push-and-advance (workflows/archon-dark-factory.yaml, #212)."""
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "push_gate_check.sh"


def run_script(prefix: str, issue: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), prefix, issue],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def git(*args, cwd, **kwargs):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, **kwargs)


@pytest.fixture()
def git_repo(tmp_path):
    """Bare-origin + working-tree git fixture (same shape as test_oos_excise.py's)."""
    bare = tmp_path / "bare"
    work = tmp_path / "work"
    bare.mkdir()
    git("init", "--bare", str(bare), cwd=str(tmp_path))
    subprocess.run(
        ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
        cwd=str(bare), capture_output=True,
    )
    git("clone", str(bare), str(work), cwd=str(tmp_path))
    git("config", "user.email", "test@test.com", cwd=str(work))
    git("config", "user.name", "Test", cwd=str(work))
    (work / "README.md").write_text("root\n")
    git("add", "README.md", cwd=str(work))
    git("commit", "-m", "init", cwd=str(work))
    git("push", "origin", "HEAD:main", cwd=str(work))
    git("branch", "--set-upstream-to=origin/main", "main", cwd=str(work))
    git("checkout", "-b", "refine/issue-212-test", cwd=str(work))
    return work


class TestPushGateCheckScript:
    def test_script_exists(self):
        assert SCRIPT.exists(), f"Script not found: {SCRIPT}"

    def test_script_syntax_valid(self):
        result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        assert result.returncode == 0, f"Syntax error: {result.stderr}"

    def test_missing_prefix_arg_fails(self, tmp_path):
        result = subprocess.run(
            ["bash", str(SCRIPT)], capture_output=True, text=True, cwd=str(tmp_path)
        )
        assert result.returncode != 0

    def test_missing_issue_arg_fails(self, tmp_path):
        result = subprocess.run(
            ["bash", str(SCRIPT), "docs/superpowers/specs/"],
            capture_output=True, text=True, cwd=str(tmp_path),
        )
        assert result.returncode != 0

    def test_no_commits_beyond_main_returns_empty(self, git_repo):
        """HEAD == main (no branch-local commits): must print nothing, exit 0."""
        result = run_script("docs/superpowers/specs/", "212", git_repo)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""

    def test_committed_matching_file_found(self, git_repo):
        spec_dir = git_repo / "docs" / "superpowers" / "specs"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "2026-07-15-example-design.md"
        spec_file.write_text("# Design\n\n**Issue:** #212\n")
        git("add", "docs/superpowers/specs/2026-07-15-example-design.md", cwd=str(git_repo))
        git("commit", "-m", "spec", cwd=str(git_repo))

        result = run_script("docs/superpowers/specs/", "212", git_repo)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "docs/superpowers/specs/2026-07-15-example-design.md"

    def test_committed_file_without_issue_reference_not_found(self, git_repo):
        spec_dir = git_repo / "docs" / "superpowers" / "specs"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "2026-07-15-example-design.md"
        spec_file.write_text("# Design\n\nNo issue reference here.\n")
        git("add", "docs/superpowers/specs/2026-07-15-example-design.md", cwd=str(git_repo))
        git("commit", "-m", "spec", cwd=str(git_repo))

        result = run_script("docs/superpowers/specs/", "212", git_repo)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""

    def test_commits_beyond_main_but_no_artifact_reproduces_212(self, git_repo):
        """Reproduces the exact #212 failure mode: branch has commits, but none of them
        touch the artifact prefix (e.g. only a token-budget or memory-write side commit) —
        must print nothing, exit 0."""
        (git_repo / "unrelated.txt").write_text("side effect\n")
        git("add", "unrelated.txt", cwd=str(git_repo))
        git("commit", "-m", "unrelated", cwd=str(git_repo))

        result = run_script("docs/superpowers/specs/", "212", git_repo)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""

    def test_uncommitted_artifact_file_not_detected(self, git_repo):
        """A spec file written to disk but never `git commit`-ed (the mid-death case) must
        NOT be detected — this is the git-aware distinction from a bare `grep -rl` scan."""
        spec_dir = git_repo / "docs" / "superpowers" / "specs"
        spec_dir.mkdir(parents=True)
        (spec_dir / "2026-07-15-example-design.md").write_text("# Design\n\n**Issue:** #212\n")
        # Deliberately not committed.

        result = run_script("docs/superpowers/specs/", "212", git_repo)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""
