"""Tests for scripts/transfer_refine_artifacts.sh — copies a ticket's refine-branch
spec/plan onto the freshly forked feat branch (#387)."""
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "transfer_refine_artifacts.sh"


def run_script(issue: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), issue],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def git(*args, cwd, **kwargs):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, **kwargs)


@pytest.fixture()
def git_repo(tmp_path):
    """Bare-origin + working-tree fixture, same shape as test_push_gate_check.py's,
    except the working tree starts on a fresh feat branch with no refine branch yet."""
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
    git("checkout", "-b", "feat/issue-212-test", cwd=str(work))
    return work


def _push_refine_branch(work, tmp_path, issue, slug, spec=True, plan=True, committer_offset=None):
    """Clones the same bare origin into a scratch dir, commits a refine branch with an
    optional spec/plan, and pushes it. Returns the branch name."""
    origin_url = git("remote", "get-url", "origin", cwd=str(work)).stdout.strip()
    scratch = tmp_path / f"refine_push_{issue}_{slug}"
    git("clone", origin_url, str(scratch), cwd=str(tmp_path))
    git("config", "user.email", "refine@test.com", cwd=str(scratch))
    git("config", "user.name", "Refine", cwd=str(scratch))
    branch = f"refine/issue-{issue}-{slug}"
    git("checkout", "-b", branch, cwd=str(scratch))
    env = None
    if committer_offset is not None:
        import os
        env = os.environ.copy()
        env["GIT_COMMITTER_DATE"] = committer_offset
        env["GIT_AUTHOR_DATE"] = committer_offset
    if spec:
        d = scratch / "docs" / "superpowers" / "specs"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"2026-09-05-{slug}-design.md").write_text(f"# Design\n\n**Issue:** #{issue}\n")
        git("add", ".", cwd=str(scratch))
        git("commit", "-m", f"docs(#{issue}): spec", cwd=str(scratch), env=env)
    if plan:
        d = scratch / "docs" / "superpowers" / "plans"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"2026-09-05-{slug}-plan.md").write_text(f"# Plan\n\n**Issue:** #{issue}\n")
        git("add", ".", cwd=str(scratch))
        git("commit", "-m", f"docs(#{issue}): plan", cwd=str(scratch), env=env)
    git("push", "origin", f"HEAD:{branch}", cwd=str(scratch))
    return branch


class TestTransferRefineArtifactsScript:
    def test_script_exists(self):
        assert SCRIPT.exists(), f"Script not found: {SCRIPT}"

    def test_script_syntax_valid(self):
        result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        assert result.returncode == 0, f"Syntax error: {result.stderr}"

    def test_bad_issue_arg_is_noop_exit_zero(self, tmp_path):
        result = subprocess.run(
            ["bash", str(SCRIPT), "not-a-number"], capture_output=True, text=True, cwd=str(tmp_path)
        )
        assert result.returncode == 0, result.stderr

    def test_no_refine_branch_prints_none_and_exits_zero(self, git_repo):
        result = run_script("212", git_repo)
        assert result.returncode == 0, result.stderr
        assert "SPEC_TRANSFER: none" in result.stdout
        assert "no refine/issue-212-* branch" in result.stdout

    def test_copies_both_spec_and_plan_when_both_exist(self, git_repo, tmp_path):
        _push_refine_branch(git_repo, tmp_path, "212", "test")
        result = run_script("212", git_repo)
        assert result.returncode == 0, result.stderr
        assert "SPEC_TRANSFER: 2 file(s)" in result.stdout
        spec = git_repo / "docs" / "superpowers" / "specs" / "2026-09-05-test-design.md"
        plan = git_repo / "docs" / "superpowers" / "plans" / "2026-09-05-test-plan.md"
        assert spec.exists() and plan.exists()
        status = git("status", "--porcelain", cwd=str(git_repo)).stdout
        assert status.strip() == "", f"working tree not clean after commit: {status}"
        log = git("log", "--oneline", "-1", cwd=str(git_repo)).stdout
        assert "docs(#212): copy spec/plan onto the implementation branch" in log

    def test_copies_via_content_association_when_commit_subject_has_no_issue_number(self, git_repo, tmp_path):
        """Spec Requirement 2's *primary* association mechanism is the file's own
        '#N' content match (push_gate_check.sh pass 1), with commit-subject
        association (pass 2) only as a fallback. This must work even when the
        refine-branch commit subject carries no issue number at all — the case
        that exposed the pass-1 working-tree-vs-ref bug in Architect Review Cycle 2."""
        origin_url = git("remote", "get-url", "origin", cwd=str(git_repo)).stdout.strip()
        scratch = tmp_path / "refine_content_only_212"
        git("clone", origin_url, str(scratch), cwd=str(tmp_path))
        git("config", "user.email", "refine@test.com", cwd=str(scratch))
        git("config", "user.name", "Refine", cwd=str(scratch))
        git("checkout", "-b", "refine/issue-212-test", cwd=str(scratch))
        d = scratch / "docs" / "superpowers" / "specs"
        d.mkdir(parents=True, exist_ok=True)
        (d / "2026-09-05-test-design.md").write_text("# Design\n\n**Issue:** #212\n")
        git("add", ".", cwd=str(scratch))
        git("commit", "-m", "docs: add design spec", cwd=str(scratch))  # no #212 in subject
        git("push", "origin", "HEAD:refine/issue-212-test", cwd=str(scratch))

        result = run_script("212", git_repo)
        assert result.returncode == 0, result.stderr
        assert "SPEC_TRANSFER: 1 file(s)" in result.stdout
        assert (git_repo / "docs" / "superpowers" / "specs" / "2026-09-05-test-design.md").exists()

    def test_copies_only_spec_when_plan_missing(self, git_repo, tmp_path):
        _push_refine_branch(git_repo, tmp_path, "212", "test", spec=True, plan=False)
        result = run_script("212", git_repo)
        assert result.returncode == 0, result.stderr
        assert "SPEC_TRANSFER: 1 file(s)" in result.stdout
        assert (git_repo / "docs" / "superpowers" / "specs" / "2026-09-05-test-design.md").exists()
        assert not (git_repo / "docs" / "superpowers" / "plans" / "2026-09-05-test-plan.md").exists()

    def test_copies_only_plan_when_spec_missing(self, git_repo, tmp_path):
        _push_refine_branch(git_repo, tmp_path, "212", "test", spec=False, plan=True)
        result = run_script("212", git_repo)
        assert result.returncode == 0, result.stderr
        assert "SPEC_TRANSFER: 1 file(s)" in result.stdout
        assert not (git_repo / "docs" / "superpowers" / "specs" / "2026-09-05-test-design.md").exists()
        assert (git_repo / "docs" / "superpowers" / "plans" / "2026-09-05-test-plan.md").exists()

    def test_refine_branch_with_neither_file_is_noop(self, git_repo, tmp_path):
        _push_refine_branch(git_repo, tmp_path, "212", "test", spec=False, plan=False)
        result = run_script("212", git_repo)
        assert result.returncode == 0, result.stderr
        assert "SPEC_TRANSFER: none" in result.stdout
        assert "no matching spec/plan found" in result.stdout

    def test_multiple_refine_branches_picks_most_recent(self, git_repo, tmp_path):
        _push_refine_branch(
            git_repo, tmp_path, "212", "older",
            committer_offset="2026-08-01T00:00:00",
        )
        _push_refine_branch(
            git_repo, tmp_path, "212", "newer",
            committer_offset="2026-09-01T00:00:00",
        )
        result = run_script("212", git_repo)
        assert result.returncode == 0, result.stderr
        assert "SPEC_TRANSFER: 2 file(s) from origin/refine/issue-212-newer" in result.stdout
        assert (git_repo / "docs" / "superpowers" / "specs" / "2026-09-05-newer-design.md").exists()
        assert not (git_repo / "docs" / "superpowers" / "specs" / "2026-09-05-older-design.md").exists()

    def test_missing_issue_arg_is_noop_exit_zero(self, tmp_path):
        result = subprocess.run(
            ["bash", str(SCRIPT)], capture_output=True, text=True, cwd=str(tmp_path)
        )
        assert result.returncode == 0, result.stderr

    def test_file_identical_to_branch_is_not_falsely_reported_as_staged(self, git_repo, tmp_path):
        """Architect Review Cycle 1: if the refine-branch file is byte-identical to what
        the fresh fork already inherited from main (e.g. re-run after a partial prior
        transfer), checkout+add produces no real diff — the script must not claim a
        commit happened. No new commit, and the reported count must be 0/none."""
        spec_dir = git_repo / "docs" / "superpowers" / "specs"
        spec_dir.mkdir(parents=True)
        (spec_dir / "2026-09-05-test-design.md").write_text("# Design\n\n**Issue:** #212\n")
        git("add", "docs/superpowers/specs/2026-09-05-test-design.md", cwd=str(git_repo))
        git("commit", "-m", "docs(#212): spec already present on this branch", cwd=str(git_repo))
        before = git("rev-list", "--count", "HEAD", cwd=str(git_repo)).stdout.strip()

        _push_refine_branch(git_repo, tmp_path, "212", "test", spec=True, plan=False)
        # Overwrite the refine branch's copy with byte-identical content so the diff is empty.
        origin_url = git("remote", "get-url", "origin", cwd=str(git_repo)).stdout.strip()
        scratch = tmp_path / "refine_identical_212_test"
        git("clone", origin_url, str(scratch), cwd=str(tmp_path))
        git("config", "user.email", "refine@test.com", cwd=str(scratch))
        git("config", "user.name", "Refine", cwd=str(scratch))
        git("checkout", "refine/issue-212-test", cwd=str(scratch))
        (scratch / "docs" / "superpowers" / "specs" / "2026-09-05-test-design.md").write_text(
            "# Design\n\n**Issue:** #212\n"
        )
        git("add", ".", cwd=str(scratch))
        git("commit", "--allow-empty", "-m", "docs(#212): identical content", cwd=str(scratch))
        git("push", "origin", "HEAD:refine/issue-212-test", cwd=str(scratch))

        result = run_script("212", git_repo)
        assert result.returncode == 0, result.stderr
        assert "SPEC_TRANSFER: none" in result.stdout
        after = git("rev-list", "--count", "HEAD", cwd=str(git_repo)).stdout.strip()
        assert after == before, "no commit should be made when nothing actually changed"

    def test_skips_file_already_archived_on_main(self, git_repo, tmp_path):
        """Architect Review Cycle 1: a redispatch that forks a brand new feat branch
        after the previous one was merged (spec already archived under docs/archive/
        on main) must not resurrect the pre-archive path — that would collide with
        push-and-pr's next git mv attempt."""
        # Content must match what _push_refine_branch below actually pushes for this
        # issue/slug — the resurrection guard now compares blob content (not just
        # basename) so it doesn't mask a same-named-but-different sibling artifact.
        archived = git_repo / "docs" / "archive" / "2026-09-05-test-design.md"
        archived.parent.mkdir(parents=True, exist_ok=True)
        archived.write_text("# Design\n\n**Issue:** #212\n")
        git("add", "docs/archive/2026-09-05-test-design.md", cwd=str(git_repo))
        git("commit", "-m", "docs: archive spec/plan for issue #212", cwd=str(git_repo))
        git("push", "origin", "HEAD:main", cwd=str(git_repo))

        _push_refine_branch(git_repo, tmp_path, "212", "test", spec=True, plan=False)

        result = run_script("212", git_repo)
        assert result.returncode == 0, result.stderr
        assert "SPEC_TRANSFER: none" in result.stdout
        assert not (git_repo / "docs" / "superpowers" / "specs" / "2026-09-05-test-design.md").exists()
