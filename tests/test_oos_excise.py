"""Tests for dark-factory/scripts/oos_excise.sh."""
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "oos_excise.sh"


def run_script(
    allowed_prefixes: str,
    commit_noun: str,
    env: dict,
    work_dir: Path,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), allowed_prefixes, commit_noun],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(work_dir),
    )


def git(*args, cwd, **kwargs):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        **kwargs,
    )


def base_env(artifacts_dir: Path) -> dict:
    e = os.environ.copy()
    e["ARTIFACTS_DIR"] = str(artifacts_dir)
    e["ISSUE_NUM"] = "670"
    return e


@pytest.fixture()
def git_repo(tmp_path):
    """Bare-origin + working-tree git fixture.

    Sets up:
      bare/   — bare origin repo  (HEAD -> main)
      work/   — working clone with 'origin/main' as default branch
    """
    bare = tmp_path / "bare"
    work = tmp_path / "work"

    bare.mkdir()
    git("init", "--bare", str(bare), cwd=str(tmp_path))
    # Ensure bare HEAD points to main so clones get the right default branch
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

    return work


class TestOosExciseScript:
    def test_script_exists(self):
        assert SCRIPT.exists(), f"Script not found: {SCRIPT}"

    def test_script_syntax_valid(self):
        result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        assert result.returncode == 0, f"Syntax error: {result.stderr}"

    def test_no_oos_files_exits_zero(self, git_repo, tmp_path):
        """When all tracked files are within allowed prefixes, script exits 0, stdout empty."""
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        (git_repo / "docs").mkdir(parents=True, exist_ok=True)
        (git_repo / "docs" / "spec.md").write_text("spec\n")
        git("add", "docs/spec.md", cwd=str(git_repo))
        git("commit", "-m", "add spec", cwd=str(git_repo))

        env = base_env(artifacts)
        result = run_script("docs/ .archon/memory/", "refine", env, git_repo)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "", f"Expected empty stdout, got: {result.stdout!r}"

    def test_new_oos_file_is_excised(self, git_repo, tmp_path):
        """A new file outside allowed prefixes should be removed and its name in stdout."""
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        (git_repo / "docs").mkdir(exist_ok=True)
        (git_repo / "docs" / "spec.md").write_text("spec\n")
        git("add", "docs/spec.md", cwd=str(git_repo))
        git("commit", "-m", "spec", cwd=str(git_repo))
        oos_file = git_repo / "backend" / "surprise.py"
        oos_file.parent.mkdir(exist_ok=True)
        oos_file.write_text("oops\n")
        git("add", str(oos_file), cwd=str(git_repo))
        git("commit", "-m", "oos", cwd=str(git_repo))

        env = base_env(artifacts)
        result = run_script("docs/ .archon/memory/", "refine", env, git_repo)
        assert result.returncode == 0, result.stderr
        assert "backend/surprise.py" in result.stdout
        assert not oos_file.exists(), "OOS file was not removed"

    def test_existing_oos_file_restored_from_origin(self, git_repo, tmp_path):
        """A file that exists in origin/main but was modified OOS should be restored."""
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        orig_file = git_repo / "docs" / "existing.md"
        orig_file.parent.mkdir(exist_ok=True)
        orig_file.write_text("original content\n")
        git("add", str(orig_file), cwd=str(git_repo))
        git("commit", "-m", "original", cwd=str(git_repo))
        git("push", "origin", "main", cwd=str(git_repo))

        oos_file = git_repo / "backend" / "oops.py"
        oos_file.parent.mkdir(exist_ok=True)
        oos_file.write_text("modified\n")
        git("add", str(oos_file), cwd=str(git_repo))
        git("commit", "-m", "oos-modify", cwd=str(git_repo))

        env = base_env(artifacts)
        result = run_script("docs/", "plan", env, git_repo)
        assert result.returncode == 0, result.stderr
        assert "backend/oops.py" in result.stdout

    def test_writes_out_of_scope_md(self, git_repo, tmp_path):
        """out-of-scope.md should be written with an entry for each excised file."""
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        oos_file = git_repo / "backend" / "bad.py"
        oos_file.parent.mkdir(exist_ok=True)
        oos_file.write_text("bad\n")
        git("add", str(oos_file), cwd=str(git_repo))
        git("commit", "-m", "bad", cwd=str(git_repo))

        env = base_env(artifacts)
        result = run_script("docs/", "refine", env, git_repo)
        assert result.returncode == 0, result.stderr
        oos_md = artifacts / "out-of-scope.md"
        assert oos_md.exists(), "out-of-scope.md was not created"
        content = oos_md.read_text()
        assert "backend/bad.py" in content

    def test_makes_allow_empty_commit(self, git_repo, tmp_path):
        """Script must commit even when the excised file was the only change (--allow-empty)."""
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        oos_file = git_repo / "backend" / "oops.py"
        oos_file.parent.mkdir(exist_ok=True)
        oos_file.write_text("oops\n")
        git("add", str(oos_file), cwd=str(git_repo))
        git("commit", "-m", "oos", cwd=str(git_repo))
        before = git("rev-list", "--count", "HEAD", cwd=str(git_repo)).stdout.strip()

        env = base_env(artifacts)
        result = run_script("docs/", "refine", env, git_repo)
        assert result.returncode == 0, result.stderr
        after = git("rev-list", "--count", "HEAD", cwd=str(git_repo)).stdout.strip()
        assert int(after) > int(before), "No commit was made after excision"

    def test_commit_message_contains_noun_and_issue(self, git_repo, tmp_path):
        """Commit message should embed the commit-noun and issue number."""
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        oos_file = git_repo / "backend" / "oops.py"
        oos_file.parent.mkdir(exist_ok=True)
        oos_file.write_text("oops\n")
        git("add", str(oos_file), cwd=str(git_repo))
        git("commit", "-m", "oos", cwd=str(git_repo))

        env = base_env(artifacts)
        result = run_script("docs/", "refine", env, git_repo)
        assert result.returncode == 0, result.stderr
        log = git("log", "--oneline", "-1", cwd=str(git_repo)).stdout.strip()
        assert "refine" in log, f"Commit noun not in message: {log}"
        assert "670" in log, f"Issue number not in message: {log}"

    def test_log_line_goes_to_stderr_not_stdout(self, git_repo, tmp_path):
        """The 'OOS gate: excising...' log line must appear on stderr, not stdout."""
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        oos_file = git_repo / "backend" / "oops.py"
        oos_file.parent.mkdir(exist_ok=True)
        oos_file.write_text("oops\n")
        git("add", str(oos_file), cwd=str(git_repo))
        git("commit", "-m", "oos", cwd=str(git_repo))

        env = base_env(artifacts)
        result = run_script("docs/", "refine", env, git_repo)
        assert result.returncode == 0, result.stderr
        assert "OOS gate" in result.stderr, "Log line not on stderr"
        for line in result.stdout.strip().splitlines():
            assert "OOS gate" not in line, f"Log line leaked to stdout: {line!r}"

    def test_stdout_contains_only_filenames(self, git_repo, tmp_path):
        """stdout must contain only bare filenames, one per line."""
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        for name in ["backend/a.py", "backend/b.py"]:
            f = git_repo / name
            f.parent.mkdir(exist_ok=True)
            f.write_text("x\n")
        git("add", ".", cwd=str(git_repo))
        git("commit", "-m", "two oos", cwd=str(git_repo))

        env = base_env(artifacts)
        result = run_script("docs/", "plan", env, git_repo)
        assert result.returncode == 0, result.stderr
        names = [l for l in result.stdout.strip().splitlines() if l]
        for n in names:
            assert n.startswith("backend/"), f"Unexpected stdout line: {n!r}"

    def test_main_only_new_file_after_fork_not_flagged(self, git_repo, tmp_path):
        """A file added by an independent commit pushed to origin/main after the
        branch forked (simulating another ticket merging) must not be flagged as
        OOS, even though it now differs between origin/main's tip and HEAD."""
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()

        # Simulate another ticket merging to main after this branch forked: a
        # second clone of the same bare origin adds a file outside the allowed
        # prefix and pushes it directly to main.
        bare_url = git("remote", "get-url", "origin", cwd=str(git_repo)).stdout.strip()
        other = tmp_path / "other_clone_add"
        git("clone", bare_url, str(other), cwd=str(tmp_path))
        git("config", "user.email", "other@test.com", cwd=str(other))
        git("config", "user.name", "Other", cwd=str(other))
        other_file = other / "scripts" / "factory_core" / "providers" / "new_provider.py"
        other_file.parent.mkdir(parents=True, exist_ok=True)
        other_file.write_text("new provider\n")
        git("add", "scripts/factory_core/providers/new_provider.py", cwd=str(other))
        git("commit", "-m", "unrelated: add provider", cwd=str(other))
        git("push", "origin", "main", cwd=str(other))

        # The working branch fetches the moved origin/main ref (as the container
        # does) but makes no commit touching the new file itself.
        git("fetch", "origin", cwd=str(git_repo))

        # The branch's own in-scope commit, unrelated to the new file.
        (git_repo / "docs").mkdir(exist_ok=True)
        (git_repo / "docs" / "spec.md").write_text("spec\n")
        git("add", "docs/spec.md", cwd=str(git_repo))
        git("commit", "-m", "spec", cwd=str(git_repo))

        env = base_env(artifacts)
        result = run_script("docs/", "refine", env, git_repo)
        assert result.returncode == 0, result.stderr
        assert "scripts/factory_core/providers/new_provider.py" not in result.stdout
        assert not (
            git_repo / "scripts" / "factory_core" / "providers" / "new_provider.py"
        ).exists(), "File added only on origin/main should not be pulled into the branch"

    def test_main_only_deletion_after_fork_not_excised_from_branch(self, git_repo, tmp_path):
        """A file inherited unchanged at the branch's fork point, later deleted by
        an independent origin/main-only commit, must not be deleted from the
        working branch. Regression test for the #251/#266 providers/* incident."""
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()

        # File exists at the fork point (committed and pushed before the branch
        # or main move any further).
        inherited = git_repo / "scripts" / "factory_core" / "providers" / "existing_provider.py"
        inherited.parent.mkdir(parents=True, exist_ok=True)
        inherited.write_text("existing provider\n")
        git("add", "scripts/factory_core/providers/existing_provider.py", cwd=str(git_repo))
        git("commit", "-m", "add existing provider", cwd=str(git_repo))
        git("push", "origin", "main", cwd=str(git_repo))

        # A separate, later-merging ticket removes the file from main.
        bare_url = git("remote", "get-url", "origin", cwd=str(git_repo)).stdout.strip()
        other = tmp_path / "other_clone_delete"
        git("clone", bare_url, str(other), cwd=str(tmp_path))
        git("config", "user.email", "other@test.com", cwd=str(other))
        git("config", "user.name", "Other", cwd=str(other))
        git("rm", "scripts/factory_core/providers/existing_provider.py", cwd=str(other))
        git("commit", "-m", "unrelated: remove provider", cwd=str(other))
        git("push", "origin", "main", cwd=str(other))

        git("fetch", "origin", cwd=str(git_repo))

        # The working branch's own commit, unrelated to that file, outside the
        # allowed prefix (so the gate has something to legitimately excise too).
        (git_repo / "docs").mkdir(exist_ok=True)
        (git_repo / "docs" / "spec.md").write_text("spec\n")
        git("add", "docs/spec.md", cwd=str(git_repo))
        git("commit", "-m", "spec", cwd=str(git_repo))

        env = base_env(artifacts)
        result = run_script("docs/", "plan", env, git_repo)
        assert result.returncode == 0, result.stderr
        assert "scripts/factory_core/providers/existing_provider.py" not in result.stdout
        assert inherited.exists(), "Inherited file was wrongly deleted from the working branch"

    def test_missing_allowed_prefixes_arg_fails(self, tmp_path):
        """Script must fail when called with no arguments."""
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        env = base_env(artifacts)
        result = subprocess.run(
            ["bash", str(SCRIPT)],
            capture_output=True, text=True, env=env, cwd=str(tmp_path),
        )
        assert result.returncode != 0

    def test_missing_commit_noun_arg_fails(self, tmp_path):
        """Script must fail when called with only one argument."""
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        env = base_env(artifacts)
        result = subprocess.run(
            ["bash", str(SCRIPT), "docs/"],
            capture_output=True, text=True, env=env, cwd=str(tmp_path),
        )
        assert result.returncode != 0
