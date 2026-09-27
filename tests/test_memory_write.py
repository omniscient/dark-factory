"""
Unit tests for memory_write.py — markdown write pipeline.
Tests are pure filesystem: no mocking, no network.
Run from repo root: pytest tests/test_memory_write.py -v
"""
import json
import re
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "memory_write.py"


def run(*args):
    """Run memory_write.py and return CompletedProcess."""
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + list(args),
        capture_output=True,
        text=True,
    )


requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _git_repo(path, origin=None):
    """Create a real git repo at *path*, optionally with an origin remote."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, capture_output=True)
    if origin is not None:
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", origin],
                       check=True, capture_output=True)
    return path


def _project_of(target):
    """The project field of the single index.jsonl row next to *target*."""
    lines = (target.parent / "index.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1, lines
    return json.loads(lines[0])["project"]


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def md_empty(tmp_path):
    """Markdown file with PROVISIONAL delimiter, no entries."""
    f = tmp_path / "backend-patterns.md"
    f.write_text("# Backend Patterns\n\n---\n<!-- PROVISIONAL -->\n")
    return f


@pytest.fixture
def md_at_cap(tmp_path):
    """Markdown file with exactly 29 existing [AVOID] entries."""
    f = tmp_path / "dark-factory-ops.md"
    lines = ["# Dark Factory Ops\n\n"]
    for i in range(29):
        lines.append(
            f"- [AVOID] Entry {i} "
            f"<!-- issue:#1 date:2026-01-01 expires:2027-01-01 source:refine -->\n"
        )
    lines.append("---\n<!-- PROVISIONAL -->\n")
    f.write_text("".join(lines))
    return f


@pytest.fixture
def md_at_cap_mixed(tmp_path):
    """Markdown file with 29 [PATTERN] entries (cap must count PATTERN/AVOID/FIX)."""
    f = tmp_path / "backend-patterns.md"
    lines = ["# Backend Patterns\n\n"]
    for i in range(29):
        lines.append(
            f"- [PATTERN] Pattern {i} "
            f"<!-- issue:#1 date:2026-01-01 expires:2027-01-01 source:refine -->\n"
        )
    lines.append("---\n<!-- PROVISIONAL -->\n")
    f.write_text("".join(lines))
    return f


@pytest.fixture
def md_with_expired(tmp_path):
    """Markdown file with one expired entry and one valid entry."""
    f = tmp_path / "codebase-patterns.md"
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    f.write_text(
        f"# Codebase Patterns\n\n"
        f"- [AVOID] stale lesson "
        f"<!-- issue:#1 date:2025-01-01 expires:{yesterday} source:refine -->\n"
        f"- [AVOID] valid lesson "
        f"<!-- issue:#2 date:2026-01-01 expires:2027-01-01 source:refine -->\n"
        f"---\n<!-- PROVISIONAL -->\n"
    )
    return f


# ── scope derivation ────────────────────────────────────────────────────────

class TestScopeDerivation:
    def test_backend_patterns(self, tmp_path):
        md = tmp_path / "backend-patterns.md"
        md.write_text("# Backend Patterns\n\n---\n")
        run("--target", str(md), "--path-prefix", "backend/app/",
            "--text", "avoid this", "--source", "conformance", "--issue", "648")
        assert "scope:backend" in md.read_text()

    def test_dark_factory_ops(self, tmp_path):
        md = tmp_path / "dark-factory-ops.md"
        md.write_text("# Dark Factory Ops\n\n---\n")
        run("--target", str(md), "--path-prefix", "dark-factory/scripts/",
            "--text", "avoid this", "--source", "code-review", "--issue", "648")
        assert "scope:dark-factory" in md.read_text()

    def test_frontend_patterns(self, tmp_path):
        md = tmp_path / "frontend-patterns.md"
        md.write_text("# Frontend Patterns\n\n---\n")
        run("--target", str(md), "--path-prefix", "frontend/src/",
            "--text", "avoid this", "--source", "refine", "--issue", "648")
        assert "scope:frontend" in md.read_text()

    def test_architecture(self, tmp_path):
        md = tmp_path / "architecture.md"
        md.write_text("# Architecture\n\n---\n")
        run("--target", str(md), "--path-prefix", "ARCHITECTURE.md",
            "--text", "avoid this", "--source", "implement", "--issue", "648")
        assert "scope:architecture" in md.read_text()

    def test_codebase_patterns(self, tmp_path):
        md = tmp_path / "codebase-patterns.md"
        md.write_text("# Codebase Patterns\n\n---\n")
        run("--target", str(md), "--path-prefix", "docs/",
            "--text", "avoid this", "--source", "implement", "--issue", "648")
        assert "scope:codebase" in md.read_text()


# ── markdown write ──────────────────────────────────────────────────────────

class TestMarkdownWrite:
    def test_exits_0_on_success(self, md_empty):
        result = run("--target", str(md_empty), "--path-prefix", "backend/app/",
                     "--text", "avoid mocks in tests", "--source", "conformance", "--issue", "648")
        assert result.returncode == 0

    def test_entry_inserted_before_provisional_delimiter(self, md_empty):
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", "avoid mocks in tests", "--source", "conformance", "--issue", "648")
        lines = md_empty.read_text().splitlines()
        avoid_idx = next(i for i, l in enumerate(lines) if "[AVOID]" in l)
        delim_idx = next(i for i, l in enumerate(lines) if l == "---")
        assert avoid_idx < delim_idx

    def test_entry_has_all_required_tags(self, md_empty):
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", "avoid mocks in tests", "--source", "conformance", "--issue", "648")
        content = md_empty.read_text()
        today = date.today().strftime("%Y-%m-%d")
        assert "scope:backend" in content
        assert "path:backend/app/" in content
        assert "source:conformance" in content
        assert "agent:conformance" in content
        assert "issue:#648" in content
        assert f"date:{today}" in content

    def test_expires_is_approximately_6_months_out(self, md_empty):
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", "avoid mocks", "--source", "conformance", "--issue", "648")
        content = md_empty.read_text()
        m = re.search(r"expires:(\d{4}-\d{2}-\d{2})", content)
        assert m, "expires: tag missing"
        expires = date.fromisoformat(m.group(1))
        delta = (expires - date.today()).days
        assert 179 <= delta <= 185, f"expires should be ~6 months out, got {delta} days"

    def test_append_when_no_delimiter(self, tmp_path):
        md = tmp_path / "codebase-patterns.md"
        md.write_text("# Codebase Patterns\n\n")
        run("--target", str(md), "--path-prefix", "docs/",
            "--text", "some lesson", "--source", "implement", "--issue", "10")
        assert "[AVOID]" in md.read_text()

    def test_exit_1_on_empty_text(self, md_empty):
        result = run("--target", str(md_empty), "--path-prefix", "backend/app/",
                     "--text", "", "--source", "conformance", "--issue", "648")
        assert result.returncode == 1


# ── normalized dedup ────────────────────────────────────────────────────────

class TestNormalizedDedup:
    def test_exact_duplicate_skipped(self, md_empty):
        text = "avoid using mocks in tests"
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", text, "--source", "conformance", "--issue", "648")
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", text, "--source", "conformance", "--issue", "648")
        assert md_empty.read_text().count("[AVOID]") == 1

    def test_case_and_whitespace_normalized(self, md_empty):
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", "avoid using mocks in tests", "--source", "conformance", "--issue", "648")
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", "Avoid  Using  Mocks  In  Tests.", "--source", "conformance", "--issue", "648")
        assert md_empty.read_text().count("[AVOID]") == 1

    def test_distinct_text_is_not_deduped(self, md_empty):
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", "avoid using mocks in tests", "--source", "conformance", "--issue", "648")
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", "avoid global state in celery tasks", "--source", "conformance", "--issue", "648")
        assert md_empty.read_text().count("[AVOID]") == 2

    def test_dedup_exits_0(self, md_empty):
        text = "avoid using mocks"
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", text, "--source", "conformance", "--issue", "648")
        result = run("--target", str(md_empty), "--path-prefix", "backend/app/",
                     "--text", text, "--source", "conformance", "--issue", "648")
        assert result.returncode == 0


# ── reinforcement ───────────────────────────────────────────────────────────

class TestReinforcement:
    def test_reinforce_updates_date_and_expires_in_place(self, md_empty):
        text = "avoid mocks in integration tests"
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", text, "--source", "conformance", "--issue", "648")
        # Backdate the written entry
        content = md_empty.read_text()
        content = re.sub(r"date:\d{4}-\d{2}-\d{2}", "date:2025-01-01", content)
        content = re.sub(r"expires:\d{4}-\d{2}-\d{2}", "expires:2025-07-01", content)
        md_empty.write_text(content)

        # Normalized duplicate — should reinforce
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", "Avoid  Mocks  In  Integration  Tests.", "--source", "conformance", "--issue", "700")

        result_content = md_empty.read_text()
        # Still exactly one entry
        assert result_content.count("[AVOID]") == 1
        # date updated to today
        today = date.today().strftime("%Y-%m-%d")
        assert f"date:{today}" in result_content

    def test_reinforce_exits_0(self, md_empty):
        text = "avoid this pattern"
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", text, "--source", "conformance", "--issue", "648")
        result = run("--target", str(md_empty), "--path-prefix", "backend/app/",
                     "--text", "Avoid  This  Pattern.", "--source", "conformance", "--issue", "700")
        assert result.returncode == 0


# ── cap enforcement ─────────────────────────────────────────────────────────

class TestCapEnforcement:
    def test_30th_entry_succeeds(self, md_at_cap):
        result = run("--target", str(md_at_cap), "--path-prefix", "dark-factory/",
                     "--text", "entry 30", "--source", "conformance", "--issue", "648")
        assert result.returncode == 0
        assert md_at_cap.read_text().count("[AVOID]") == 30

    def test_31st_entry_skipped(self, md_at_cap):
        run("--target", str(md_at_cap), "--path-prefix", "dark-factory/",
            "--text", "entry 30", "--source", "conformance", "--issue", "648")
        result = run("--target", str(md_at_cap), "--path-prefix", "dark-factory/",
                     "--text", "entry 31 — completely unique text here", "--source", "conformance",
                     "--issue", "648")
        assert result.returncode == 0
        assert md_at_cap.read_text().count("[AVOID]") == 30

    def test_pattern_entries_count_toward_cap(self, md_at_cap_mixed):
        # 29 [PATTERN] entries are already at the floor; the 30th write (AVOID) must succeed.
        run("--target", str(md_at_cap_mixed), "--path-prefix", "backend/app/",
            "--text", "entry 30", "--source", "conformance", "--issue", "648")
        assert md_at_cap_mixed.read_text().count("[AVOID]") == 1

        # 31st write must be rejected — total authoritative entries ([PATTERN]+[AVOID]) == 30.
        result = run("--target", str(md_at_cap_mixed), "--path-prefix", "backend/app/",
                     "--text", "entry 31 — completely unique", "--source", "conformance",
                     "--issue", "648")
        assert result.returncode == 0
        assert md_at_cap_mixed.read_text().count("[AVOID]") == 1  # still only 1 AVOID written


# ── expiry cleanup ──────────────────────────────────────────────────────────

class TestExpiryCleanup:
    def test_expired_entries_removed_before_new_write(self, md_with_expired):
        run("--target", str(md_with_expired), "--path-prefix", "docs/",
            "--text", "new lesson", "--source", "implement", "--issue", "648")
        content = md_with_expired.read_text()
        assert "stale lesson" not in content
        assert "valid lesson" in content
        assert "new lesson" in content

    def test_only_expired_entries_removed(self, md_with_expired):
        run("--target", str(md_with_expired), "--path-prefix", "docs/",
            "--text", "new lesson", "--source", "implement", "--issue", "648")
        content = md_with_expired.read_text()
        assert content.count("[AVOID]") == 2  # valid + new


# ── index.jsonl ─────────────────────────────────────────────────────────────

class TestIndexJsonl:
    def test_successful_write_creates_index_next_to_target(self, md_empty):
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", "avoid mocks", "--source", "conformance", "--issue", "648")
        index = md_empty.parent / "index.jsonl"
        assert index.exists(), "index.jsonl should be created on first write"

    def test_index_record_has_required_fields(self, md_empty, monkeypatch):
        monkeypatch.setenv("FACTORY_REPO", "jobfinder")
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", "avoid mocks", "--source", "conformance", "--issue", "648")
        record = json.loads((md_empty.parent / "index.jsonl").read_text().strip())
        assert record["agent_id"] == "conformance"
        assert record["scope"] == "backend"
        assert record["content"] == "avoid mocks"
        assert record["issue_number"] == 648
        assert record["files"] == ["backend/app/"]
        assert record["project"] == "jobfinder"
        assert record["type"] == "avoidance"
        assert record["status"] == "active"

    def test_second_write_appends_second_record(self, md_empty):
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", "avoid mocks", "--source", "conformance", "--issue", "648")
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", "avoid global state", "--source", "conformance", "--issue", "648")
        lines = (md_empty.parent / "index.jsonl").read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["content"] == "avoid mocks"
        assert json.loads(lines[1])["content"] == "avoid global state"

    def test_dedup_skip_does_not_append_to_index(self, md_empty):
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", "avoid mocks", "--source", "conformance", "--issue", "648")
        first = (md_empty.parent / "index.jsonl").read_text()
        # normalized duplicate
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", "AVOID MOCKS.", "--source", "conformance", "--issue", "648")
        assert (md_empty.parent / "index.jsonl").read_text() == first

    def test_cap_skip_does_not_append_to_index(self, md_at_cap):
        # md_at_cap has 29 entries; write 30th (should succeed and append to index)
        run("--target", str(md_at_cap), "--path-prefix", "dark-factory/",
            "--text", "entry 30", "--source", "conformance", "--issue", "648")
        first = (md_at_cap.parent / "index.jsonl").read_text()
        # 31st write — cap hit, no index append
        run("--target", str(md_at_cap), "--path-prefix", "dark-factory/",
            "--text", "entry 31 unique text that does not exist yet", "--source", "conformance",
            "--issue", "648")
        assert (md_at_cap.parent / "index.jsonl").read_text() == first

    def test_index_io_error_is_nonfatal(self, md_empty):
        """index.jsonl I/O failure must not set exit 1."""
        index = md_empty.parent / "index.jsonl"
        index.write_text("")
        index.chmod(0o000)
        try:
            result = run("--target", str(md_empty), "--path-prefix", "backend/app/",
                         "--text", "avoid mocks", "--source", "conformance", "--issue", "648")
            assert result.returncode == 0
            # Markdown was still written
            assert "[AVOID]" in md_empty.read_text()
            # Warning emitted to stderr
            assert "index.jsonl" in result.stderr
        finally:
            index.chmod(0o644)


# ── first-run directory creation (#444) ─────────────────────────────────────

class TestDirectoryCreation:
    def test_fresh_target_creates_dir_markdown_and_index(self, tmp_path):
        mem_dir = tmp_path / "fresh" / ".archon" / "memory"
        target = mem_dir / "backend-patterns.md"
        assert not mem_dir.exists()
        result = run("--target", str(target), "--path-prefix", "backend/app/",
                     "--text", "avoid mocks", "--source", "refine", "--issue", "444")
        assert result.returncode == 0, result.stderr
        assert "[AVOID] avoid mocks" in target.read_text()
        assert (mem_dir / "index.jsonl").exists()
        assert f"memory-write: created {mem_dir.resolve()}" in result.stdout

    def test_existing_dir_prints_no_created_line(self, md_empty):
        result = run("--target", str(md_empty), "--path-prefix", "backend/app/",
                     "--text", "avoid mocks", "--source", "refine", "--issue", "444")
        assert result.returncode == 0
        assert "memory-write: created" not in result.stdout

    def test_empty_text_does_not_create_dir(self, tmp_path):
        mem_dir = tmp_path / "fresh" / ".archon" / "memory"
        result = run("--target", str(mem_dir / "backend-patterns.md"),
                     "--path-prefix", "backend/app/", "--text", "",
                     "--source", "refine", "--issue", "444")
        assert result.returncode == 1
        assert not mem_dir.exists()
        assert not (tmp_path / "fresh").exists()

    def test_mkdir_failure_is_one_stderr_line_exit_1(self, tmp_path):
        # .archon/memory exists as a FILE: exist_ok=True does not swallow FileExistsError.
        archon = tmp_path / ".archon"
        archon.mkdir()
        (archon / "memory").write_text("not a directory\n")
        result = run("--target", str(archon / "memory" / "backend-patterns.md"),
                     "--path-prefix", "backend/app/", "--text", "avoid mocks",
                     "--source", "refine", "--issue", "444")
        assert result.returncode == 1
        assert "Traceback" not in result.stderr
        lines = result.stderr.strip().splitlines()
        assert len(lines) == 1, result.stderr
        assert lines[0].startswith(f"memory-write: error: cannot create {archon / 'memory'}: ")


# ── project resolution (#444) ───────────────────────────────────────────────

MISMATCH = "memory-write: WARNING: project '"
UNRESOLVED = (
    "memory-write: WARNING: project unresolved (FACTORY_REPO/FACTORY_PRODUCT_NAME "
    "unset, no git origin) - writing project:unknown"
)


def _write(target, text="avoid mocks"):
    return run("--target", str(target), "--path-prefix", "backend/app/",
               "--text", text, "--source", "refine", "--issue", "444")


class TestProjectResolution:
    # ── env rungs: tmp_path is typically not a git worktree (git lookup → None) ──

    def test_factory_repo_wins(self, md_empty, monkeypatch):
        monkeypatch.setenv("FACTORY_REPO", "jobfinder")
        monkeypatch.setenv("FACTORY_PRODUCT_NAME", "MarketHawk")
        result = _write(md_empty)
        assert result.returncode == 0
        assert _project_of(md_empty) == "jobfinder"

    def test_product_name_used_as_is_when_repo_unset(self, md_empty, monkeypatch):
        monkeypatch.delenv("FACTORY_REPO", raising=False)
        monkeypatch.setenv("FACTORY_PRODUCT_NAME", "MarketHawk")
        result = _write(md_empty)
        assert result.returncode == 0
        assert _project_of(md_empty) == "MarketHawk"

    def test_blank_factory_repo_falls_through(self, md_empty, monkeypatch):
        monkeypatch.setenv("FACTORY_REPO", "   ")
        monkeypatch.setenv("FACTORY_PRODUCT_NAME", "MarketHawk")
        _write(md_empty)
        assert _project_of(md_empty) == "MarketHawk"

    # ── git-origin rung ──

    @requires_git
    def test_git_origin_https(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FACTORY_REPO", raising=False)
        monkeypatch.delenv("FACTORY_PRODUCT_NAME", raising=False)
        repo = _git_repo(tmp_path / "repo", "https://github.com/omniscient/jobfinder.git")
        target = repo / ".archon" / "memory" / "backend-patterns.md"
        result = _write(target)
        assert result.returncode == 0, result.stderr
        assert _project_of(target) == "jobfinder"
        assert "WARNING" not in result.stderr

    @requires_git
    def test_git_origin_scp_style_without_path_slash(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FACTORY_REPO", raising=False)
        monkeypatch.delenv("FACTORY_PRODUCT_NAME", raising=False)
        repo = _git_repo(tmp_path / "repo", "git@github.com:jobfinder.git")
        target = repo / ".archon" / "memory" / "backend-patterns.md"
        _write(target)
        assert _project_of(target) == "jobfinder"

    # ── terminal rung ──

    @requires_git
    def test_unknown_when_no_env_and_no_origin(self, tmp_path, monkeypatch):
        # A fresh repo with NO origin: `git remote get-url origin` exits non-zero
        # regardless of any git ancestry the host has.
        monkeypatch.delenv("FACTORY_REPO", raising=False)
        monkeypatch.delenv("FACTORY_PRODUCT_NAME", raising=False)
        repo = _git_repo(tmp_path / "repo")
        target = repo / ".archon" / "memory" / "backend-patterns.md"
        result = _write(target)
        assert result.returncode == 0
        assert _project_of(target) == "unknown"
        assert "Traceback" not in result.stderr
        assert result.stderr.strip().splitlines() == [UNRESOLVED]

    # ── mismatch warning (Requirement 6) ──

    @requires_git
    def test_env_vs_origin_mismatch_warns_once_and_env_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FACTORY_REPO", "markethawk")
        monkeypatch.delenv("FACTORY_PRODUCT_NAME", raising=False)
        repo = _git_repo(tmp_path / "repo", "https://github.com/omniscient/jobfinder.git")
        target = repo / ".archon" / "memory" / "backend-patterns.md"
        result = _write(target)
        assert result.returncode == 0
        assert _project_of(target) == "markethawk"
        assert result.stderr.strip().splitlines() == [
            "memory-write: WARNING: project 'markethawk' does not match git origin "
            "'jobfinder' (FACTORY_REPO defaulted?)"
        ]

    @requires_git
    def test_case_only_difference_does_not_warn(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FACTORY_REPO", raising=False)
        monkeypatch.setenv("FACTORY_PRODUCT_NAME", "MarketHawk")
        repo = _git_repo(tmp_path / "repo", "https://github.com/omniscient/markethawk.git")
        target = repo / ".archon" / "memory" / "backend-patterns.md"
        result = _write(target)
        assert result.returncode == 0
        assert _project_of(target) == "MarketHawk"
        assert MISMATCH not in result.stderr

    @requires_git
    def test_no_mismatch_warning_on_dedup_skip(self, tmp_path, monkeypatch):
        # Resolver runs only when an index row is written; a reinforce is silent.
        monkeypatch.setenv("FACTORY_REPO", "markethawk")
        monkeypatch.delenv("FACTORY_PRODUCT_NAME", raising=False)
        repo = _git_repo(tmp_path / "repo", "https://github.com/omniscient/jobfinder.git")
        target = repo / ".archon" / "memory" / "backend-patterns.md"
        _write(target)
        result = _write(target, text="AVOID MOCKS.")
        assert result.returncode == 0
        assert MISMATCH not in result.stderr

    # ── security rider: never echo the remote URL (token lives in it) ──

    @requires_git
    def test_token_in_origin_url_never_printed(self, tmp_path, monkeypatch):
        secret = "ghp_SECRETTOKEN444"
        monkeypatch.setenv("FACTORY_REPO", "markethawk")  # force the mismatch diagnostic
        monkeypatch.delenv("FACTORY_PRODUCT_NAME", raising=False)
        repo = _git_repo(tmp_path / "repo",
                         f"https://{secret}@github.com/omniscient/jobfinder.git")
        target = repo / ".archon" / "memory" / "backend-patterns.md"
        result = _write(target)
        assert result.returncode == 0
        assert "'jobfinder'" in result.stderr
        assert secret not in result.stdout
        assert secret not in result.stderr
        assert secret not in (target.parent / "index.jsonl").read_text()

    @requires_git
    def test_token_in_origin_url_not_recorded_via_git_rung(self, tmp_path, monkeypatch):
        secret = "ghp_SECRETTOKEN444"
        monkeypatch.delenv("FACTORY_REPO", raising=False)
        monkeypatch.delenv("FACTORY_PRODUCT_NAME", raising=False)
        repo = _git_repo(tmp_path / "repo",
                         f"https://{secret}@github.com/omniscient/jobfinder.git")
        target = repo / ".archon" / "memory" / "backend-patterns.md"
        result = _write(target)
        assert _project_of(target) == "jobfinder"
        assert secret not in result.stdout + result.stderr


# ── sanitization ────────────────────────────────────────────────────────────

class TestSanitization:
    def test_arrow_in_text_does_not_break_comment(self, md_empty):
        """text containing --> must not close the HTML comment early."""
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", "avoid --> pattern in code", "--source", "conformance", "--issue", "648")
        content = md_empty.read_text()
        # The full comment must still be closed properly — scope:/path: must all appear.
        assert "scope:" in content
        assert "path:" in content
        # Exactly one entry written (not multiple fragments)
        assert content.count("[AVOID]") == 1

    def test_newline_in_text_is_collapsed_to_space(self, md_empty):
        """text containing \\n must be collapsed to a single line before writing."""
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", "avoid this\npattern", "--source", "conformance", "--issue", "648")
        content = md_empty.read_text()
        # Entry must appear on a single line so _ENTRY_RE matches it correctly.
        entry_line = next((l for l in content.splitlines() if "[AVOID]" in l), None)
        assert entry_line is not None, "entry line not found"
        assert "avoid this pattern" in entry_line

    def test_newline_text_dedup_still_works(self, md_empty):
        """Two writes with equivalent text differing only by embedded newline must dedup."""
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", "avoid this pattern", "--source", "conformance", "--issue", "648")
        # Second write has embedded newline — after collapse it equals first write.
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", "avoid this\npattern", "--source", "conformance", "--issue", "648")
        assert md_empty.read_text().count("[AVOID]") == 1

    def test_arrow_in_path_prefix_is_stripped(self, md_empty):
        """path-prefix containing --> must be stripped so the comment closes exactly once."""
        run("--target", str(md_empty), "--path-prefix", "dark-->factory/",
            "--text", "avoid this", "--source", "conformance", "--issue", "648")
        entry_line = next(
            (l for l in md_empty.read_text().splitlines() if "[AVOID]" in l), None
        )
        assert entry_line is not None
        comment_start = entry_line.find("<!--")
        assert comment_start != -1
        # The comment portion must contain exactly one --> (the closing one at the end).
        comment_part = entry_line[comment_start:]
        assert comment_part.count("-->") == 1, (
            f"comment was closed early; found {comment_part.count('-->')} occurrences: {comment_part!r}"
        )

    def test_arrow_in_source_is_stripped(self, md_empty):
        """source containing mid-value --> must be stripped so the comment closes exactly once."""
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", "avoid this", "--source", "conform-->ance", "--issue", "648")
        entry_line = next(
            (l for l in md_empty.read_text().splitlines() if "[AVOID]" in l), None
        )
        assert entry_line is not None
        comment_start = entry_line.find("<!--")
        assert comment_start != -1
        comment_part = entry_line[comment_start:]
        assert comment_part.count("-->") == 1, (
            f"comment was closed early; found {comment_part.count('-->')} occurrences: {comment_part!r}"
        )
