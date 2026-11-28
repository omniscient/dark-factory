"""
Unit tests for memory_write.py — markdown write pipeline.
Tests are pure filesystem: no mocking, no network.
Run from repo root: pytest tests/test_memory_write.py -v
"""
import os
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

    def test_31st_entry_evicts_oldest_and_writes_new(self, md_at_cap):
        run("--target", str(md_at_cap), "--path-prefix", "dark-factory/",
            "--text", "entry 30", "--source", "conformance", "--issue", "648")
        result = run("--target", str(md_at_cap), "--path-prefix", "dark-factory/",
                     "--text", "entry 31 - completely unique text here", "--source", "conformance",
                     "--issue", "648")
        assert result.returncode == 0
        text = md_at_cap.read_text()
        assert text.count("[AVOID]") == 30
        assert "entry 31 - completely unique text here" in text
        # All fixture entries share a date; the tie evicts the earliest in the file.
        assert "[AVOID] Entry 0 " not in text
        assert "[AVOID] Entry 1 " in text

    def test_evicts_oldest_by_date_not_file_position(self, tmp_path):
        f = tmp_path / "dark-factory-ops.md"
        lines = ["# Ops\n\n"]
        for i in range(30):
            d = "2025-01-01" if i == 17 else "2026-03-01"
            lines.append(f"- [AVOID] Entry {i} <!-- issue:#1 date:{d} expires:2099-01-01 source:refine -->\n")
        lines.append("---\n<!-- PROVISIONAL -->\n")
        f.write_text("".join(lines))
        result = run("--target", str(f), "--path-prefix", "x/", "--text", "brand new lesson",
                     "--source", "refine", "--issue", "9")
        assert result.returncode == 0
        text = f.read_text()
        assert "[AVOID] Entry 17 " not in text
        assert "[AVOID] Entry 0 " in text
        assert "brand new lesson" in text

    def test_undated_entry_is_oldest(self, tmp_path):
        f = tmp_path / "dark-factory-ops.md"
        lines = ["# Ops\n\n"]
        for i in range(30):
            if i == 5:
                lines.append("- [PATTERN] Entry 5 legacy undated\n")
            else:
                lines.append(f"- [AVOID] Entry {i} <!-- issue:#1 date:2020-01-01 expires:2099-01-01 source:refine -->\n")
        lines.append("---\n<!-- PROVISIONAL -->\n")
        f.write_text("".join(lines))
        run("--target", str(f), "--path-prefix", "x/", "--text", "fresh", "--source", "refine", "--issue", "9")
        text = f.read_text()
        assert "legacy undated" not in text
        assert "fresh" in text

    def test_overfull_file_is_trimmed_to_cap(self, tmp_path):
        f = tmp_path / "dark-factory-ops.md"
        lines = ["# Ops\n\n"]
        for i in range(34):
            lines.append(f"- [AVOID] Entry {i} <!-- issue:#1 date:2026-01-{i + 1:02d} expires:2099-01-01 source:refine -->\n")
        lines.append("---\n<!-- PROVISIONAL -->\n")
        f.write_text("".join(lines))
        run("--target", str(f), "--path-prefix", "x/", "--text", "fresh", "--source", "refine", "--issue", "9")
        text = f.read_text()
        assert len(re.findall(r"^- \[(?:PATTERN|AVOID|FIX)\]", text, re.M)) == 30
        assert "fresh" in text
        assert "[AVOID] Entry 5 " in text and "[AVOID] Entry 4 " not in text

    def test_provisional_and_invalid_not_evicted(self, tmp_path):
        f = tmp_path / "dark-factory-ops.md"
        lines = ["# Ops\n\n- [INVALID: wrong] tombstone <!-- date:2000-01-01 -->\n"]
        for i in range(30):
            lines.append(f"- [AVOID] Entry {i} <!-- issue:#1 date:2026-01-01 expires:2099-01-01 source:refine -->\n")
        lines.append("---\n- [PROVISIONAL] prov <!-- date:2000-01-01 -->\n")
        f.write_text("".join(lines))
        run("--target", str(f), "--path-prefix", "x/", "--text", "fresh", "--source", "refine", "--issue", "9")
        text = f.read_text()
        assert "tombstone" in text and "[PROVISIONAL] prov" in text

    def test_pattern_entries_count_toward_cap(self, md_at_cap_mixed):
        # 29 [PATTERN] entries are already at the floor; the 30th write (AVOID) must succeed.
        run("--target", str(md_at_cap_mixed), "--path-prefix", "backend/app/",
            "--text", "entry 30", "--source", "conformance", "--issue", "648")
        assert md_at_cap_mixed.read_text().count("[AVOID]") == 1

        # 31st write evicts the oldest ([PATTERN]) entry; the total stays at 30.
        result = run("--target", str(md_at_cap_mixed), "--path-prefix", "backend/app/",
                     "--text", "entry 31 — completely unique", "--source", "conformance",
                     "--issue", "648")
        assert result.returncode == 0
        text = md_at_cap_mixed.read_text()
        assert text.count("[AVOID]") == 2
        assert text.count("[PATTERN]") == 28


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


# ── index.jsonl is not written (#486) ───────────────────────────────────────

class TestNoIndexWrite:
    """memory_write.py used to append index rows without `id`/`source_file`, which
    the retriever skips. It now writes the markdown only; readable index rows come
    from memory_import.py."""

    def test_write_does_not_create_index(self, md_empty):
        result = run("--target", str(md_empty), "--path-prefix", "backend/app/",
                     "--text", "avoid mocks", "--source", "conformance", "--issue", "648")
        assert result.returncode == 0, result.stderr
        assert "[AVOID] avoid mocks" in md_empty.read_text()
        assert not (md_empty.parent / "index.jsonl").exists()

    def test_existing_index_is_left_untouched(self, md_empty):
        index = md_empty.parent / "index.jsonl"
        index.write_text('{"id": "abc"}\n')
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", "avoid mocks", "--source", "conformance", "--issue", "648")
        assert index.read_text() == '{"id": "abc"}\n'

    def test_written_entry_is_readable_by_retriever(self, md_empty):
        """What the writer produces must be visible to retrieval."""
        sys.path.insert(0, str(SCRIPT.parent))
        import memory_retrieve as mr
        run("--target", str(md_empty), "--path-prefix", "backend/app/",
            "--text", "avoid mocks", "--source", "implement", "--issue", "648")
        out = mr.retrieve_memory(md_empty.parent, "implement", ["backend/app/x.py"])
        assert "avoid mocks" in out


class TestDirectoryCreation:
    def test_fresh_target_creates_dir_and_markdown(self, tmp_path):
        mem_dir = tmp_path / "fresh" / ".archon" / "memory"
        target = mem_dir / "backend-patterns.md"
        assert not mem_dir.exists()
        result = run("--target", str(target), "--path-prefix", "backend/app/",
                     "--text", "avoid mocks", "--source", "refine", "--issue", "444")
        assert result.returncode == 0, result.stderr
        assert "[AVOID] avoid mocks" in target.read_text()
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


# ── --kind tag selection (#38) ──────────────────────────────────────────────

def _entry_lines(md):
    """Authoritative [PATTERN]/[AVOID]/[FIX] lines of *md*, in file order."""
    return [l for l in md.read_text().splitlines()
            if re.match(r"^- \[(PATTERN|AVOID|FIX)\] ", l)]


_KIND_ARGS = ("--path-prefix", "backend/app/", "--text", "prefer fixtures over mocks",
              "--source", "refine", "--issue", "38")


class TestKind:
    @pytest.mark.parametrize("kind,tag", [("pattern", "PATTERN"), ("fix", "FIX"),
                                          ("avoid", "AVOID")])
    def test_kind_selects_tag_with_unchanged_metadata(self, md_empty, kind, tag):
        result = run("--target", str(md_empty), *_KIND_ARGS, "--kind", kind)
        assert result.returncode == 0, result.stderr
        [line] = _entry_lines(md_empty)
        today = date.today().strftime("%Y-%m-%d")
        assert line.startswith(
            f"- [{tag}] prefer fixtures over mocks <!-- issue:#38 date:{today} expires:")
        assert line.endswith(
            " source:refine agent:refine scope:backend path:backend/app/ -->")

    def test_omitted_kind_is_byte_identical_to_kind_avoid(self, tmp_path):
        seed = "# Backend Patterns\n\n---\n<!-- PROVISIONAL -->\n"
        default_md = tmp_path / "default" / "backend-patterns.md"
        explicit_md = tmp_path / "explicit" / "backend-patterns.md"
        for md in (default_md, explicit_md):
            md.parent.mkdir()
            md.write_text(seed)
        assert run("--target", str(default_md), *_KIND_ARGS).returncode == 0
        assert run("--target", str(explicit_md), *_KIND_ARGS,
                   "--kind", "avoid").returncode == 0
        assert default_md.read_bytes() == explicit_md.read_bytes()
        [line] = _entry_lines(default_md)
        assert line.startswith("- [AVOID] prefer fixtures over mocks <!-- ")

    @pytest.mark.parametrize("bad", ["AVOID] injected", "AVOID", "provisional", ""])
    def test_invalid_kind_exits_2_and_leaves_target_untouched(self, md_empty, bad):
        before = md_empty.read_bytes()
        result = run("--target", str(md_empty), *_KIND_ARGS, "--kind", bad)
        assert result.returncode == 2
        assert "invalid choice" in result.stderr
        assert md_empty.read_bytes() == before

    def test_invalid_kind_creates_no_directory(self, tmp_path):
        mem_dir = tmp_path / "fresh" / ".archon" / "memory"
        result = run("--target", str(mem_dir / "backend-patterns.md"), *_KIND_ARGS,
                     "--kind", "AVOID] injected")
        assert result.returncode == 2
        assert not (tmp_path / "fresh").exists()

    def test_pattern_write_reinforces_existing_avoid_without_retagging(self, md_empty):
        assert run("--target", str(md_empty), *_KIND_ARGS,
                   "--kind", "avoid").returncode == 0
        content = re.sub(r"date:\d{4}-\d{2}-\d{2}", "date:2025-01-01",
                         md_empty.read_text())
        md_empty.write_text(content)
        result = run("--target", str(md_empty), "--path-prefix", "backend/app/",
                     "--text", "Prefer  Fixtures over MOCKS.", "--source", "refine",
                     "--issue", "39", "--kind", "pattern")
        assert result.returncode == 0, result.stderr
        assert "reinforced existing entry" in result.stdout
        [line] = _entry_lines(md_empty)
        assert line.startswith("- [AVOID] prefer fixtures over mocks <!-- issue:#38 ")
        assert f"date:{date.today().strftime('%Y-%m-%d')}" in line

    def test_cap_evicts_oldest_by_date_regardless_of_kind(self, tmp_path):
        md = tmp_path / "architecture.md"
        kinds = ("PATTERN", "AVOID", "FIX")
        lines = ["# Architecture\n\n"]
        for i in range(30):
            day = "2025-12-31" if i == 12 else f"2026-03-{i + 1:02d}"
            lines.append(f"- [{kinds[i % 3]}] Old {i:02d} "
                         f"<!-- issue:#1 date:{day} expires:2027-01-01 source:refine -->\n")
        lines.append("---\n<!-- PROVISIONAL -->\n")
        md.write_text("".join(lines))
        result = run("--target", str(md), "--path-prefix", "docs/", "--text", "new fix",
                     "--source", "refine", "--issue", "38", "--kind", "fix")
        assert result.returncode == 0, result.stderr
        entries = _entry_lines(md)
        assert len(entries) == 30
        assert not any("Old 12 " in l for l in entries)  # the oldest, a [PATTERN] line
        assert sum(l.startswith("- [FIX] new fix <!-- ") for l in entries) == 1
        assert sum(l.startswith("- [PATTERN] ") for l in entries) == 9
        assert sum(l.startswith("- [AVOID] ") for l in entries) == 10


def test_gate_lib_write_memory_entry_still_writes_avoid(md_empty):
    """gate_lib.sh passes no --kind; the default must keep the gate path [AVOID]."""
    if os.name != "posix" or shutil.which("bash") is None:
        pytest.skip("needs a POSIX host with bash")
    gate_lib = SCRIPT.parent / "gate_lib.sh"
    proc = subprocess.run(
        ["bash", "-c",
         'source "$1" && write_memory_entry "$2" dark-factory/scripts/ '
         '"avoid bad pattern" conformance 38',
         "bash", str(gate_lib), str(md_empty)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    [line] = _entry_lines(md_empty)
    assert line.startswith("- [AVOID] avoid bad pattern <!-- issue:#38 ")
