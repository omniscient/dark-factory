"""Tests for dark-factory/scripts/mem0_import.py and mem0_retrieve_adapter.py (issue #50 spike).

mem0 is never imported at module level by either script under test (see mem0_spike_config.
build_memory's deferred import) — these tests run without mem0ai installed by monkeypatching
build_memory directly, matching the tests/test_memory_retrieve.py convention of no live I/O.
"""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mem0_import as mi  # noqa: E402
import mem0_retrieve_adapter as mra  # noqa: E402


def _write(tmpdir, fname, content):
    p = Path(tmpdir) / fname
    p.write_text(content, encoding="utf-8")
    return p


def test_load_entries_parses_pattern_and_avoid():
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(
            tmpdir, "backend-patterns.md",
            "- [PATTERN] Use selectinload. <!-- issue:#1 date:2026-01-01 source:implement -->\n"
            "- [AVOID] Never use joinedload. <!-- issue:#2 date:2026-01-01 source:implement -->\n",
        )
        entries = mi.load_entries(Path(tmpdir))
    assert len(entries) == 2
    assert entries[0]["kind"] == "PATTERN"
    assert entries[0]["issue"] == "#1"
    assert entries[1]["kind"] == "AVOID"


def test_load_entries_skips_provisional_and_invalid():
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(
            tmpdir, "codebase-patterns.md",
            "- [PROVISIONAL] Maybe true. <!-- issue:#3 date:2026-01-01 source:refine -->\n"
            "- [INVALID: superseded] Old lesson. <!-- issue:#4 date:2026-01-01 source:refine -->\n"
            "- [PATTERN] Real lesson. <!-- issue:#5 date:2026-01-01 source:refine -->\n",
        )
        entries = mi.load_entries(Path(tmpdir))
    assert len(entries) == 1
    assert entries[0]["body"] == "Real lesson."


def test_load_entries_stops_at_delimiter():
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(
            tmpdir, "architecture.md",
            "- [PATTERN] Above the line. <!-- issue:#6 date:2026-01-01 source:refine -->\n"
            "---\n"
            "- [PATTERN] Below the line, must not be loaded. <!-- issue:#7 date:2026-01-01 -->\n",
        )
        entries = mi.load_entries(Path(tmpdir))
    assert len(entries) == 1
    assert entries[0]["body"] == "Above the line."


def test_main_writes_report_with_mocked_memory(monkeypatch, capsys):
    with tempfile.TemporaryDirectory() as tmpdir:
        memdir = Path(tmpdir) / "memdir"
        memdir.mkdir()
        _write(
            memdir, "backend-patterns.md",
            "- [PATTERN] One lesson. <!-- issue:#8 date:2026-01-01 source:implement -->\n",
        )
        report_path = Path(tmpdir) / "report.json"

        fake_memory = MagicMock()
        fake_memory.add.return_value = {"results": [{"id": "abc123"}]}
        monkeypatch.setattr(mi, "build_memory", lambda store_path: fake_memory)
        monkeypatch.setattr(
            sys, "argv",
            [
                "mem0_import.py",
                "--memory-dir", str(memdir),
                "--store-path", str(Path(tmpdir) / "store"),
                "--report", str(report_path),
            ],
        )

        with pytest.raises(SystemExit) as exc:
            mi.main()
        assert exc.value.code == 0

        fake_memory.add.assert_called_once()
        _, kwargs = fake_memory.add.call_args
        assert kwargs["infer"] is False
        assert kwargs["metadata"]["kind"] == "PATTERN"

        import json
        report = json.loads(report_path.read_text())
        assert len(report["imported"]) == 1
        assert report["imported"][0]["id"] == "abc123"


def test_query_construction_with_files():
    assert mra.build_query("implement", "backend/app/x.py") == \
        "implement lessons for backend/app/x.py"


def test_query_construction_without_files():
    assert mra.build_query("refine", "") == "refine lessons"


def test_main_prints_hits_from_mocked_search(monkeypatch, capsys):
    fake_memory = MagicMock()
    fake_memory.search.return_value = {
        "results": [
            {"memory": "Use selectinload, not joinedload.", "metadata": {"kind": "AVOID"}},
            {"memory": "", "metadata": {"kind": "PATTERN"}},  # empty body must be skipped
        ]
    }
    monkeypatch.setattr(mra, "build_memory", lambda store_path: fake_memory)
    monkeypatch.setenv("MEM0_SPIKE_STORE_PATH", "/tmp/whatever-store")
    monkeypatch.setattr(
        sys, "argv",
        ["mem0_retrieve_adapter.py", "--phase", "implement", "--files", "backend/app/x.py",
         "--memory-dir", ".archon/memory"],
    )

    mra.main()
    out = capsys.readouterr().out
    assert "- [AVOID] Use selectinload, not joinedload." in out
    assert out.count("- [") == 1  # the empty-body result must not print a second line


def test_main_errors_without_store_path_env(monkeypatch, capsys):
    monkeypatch.delenv("MEM0_SPIKE_STORE_PATH", raising=False)
    monkeypatch.setattr(
        sys, "argv",
        ["mem0_retrieve_adapter.py", "--phase", "implement", "--files", "", "--memory-dir", "."],
    )
    with pytest.raises(SystemExit) as exc:
        mra.main()
    assert exc.value.code == 1
