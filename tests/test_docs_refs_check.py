"""Tests for docs_refs_check.py — deterministic reference-doc checks (#537).

The pass/fail fixture trees under tests/fixtures/docs_refs_check/ mirror the repo layout
(docs/, scripts/, config/, workflows/) so check() runs on them with its defaults; fail/
carries exactly one broken reference per check category.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from docs_refs_check import check, main  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "docs_refs_check"


def _fixture_docs(name: str) -> tuple[Path, list[Path]]:
    root = _FIXTURES / name
    return root, [root / "docs" / "configuration.md", root / "docs" / "features.md"]


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


def _paths_only(root: Path, doc_text: str) -> list[str]:
    """Run check() on a doc named notes.md, so only the path/line check applies."""
    doc = root / "notes.md"
    doc.write_text(doc_text, encoding="utf-8")
    return check([doc], root, root / "none.yaml", root / "none.yaml")


# ---------------------------------------------------------------------------
# Path and line references
# ---------------------------------------------------------------------------

def test_line_ranges_and_comma_chains(tmp_path):
    root = _tree(tmp_path, {"pkg/f.sh": "a\nb\nc\nd\n"})
    assert _paths_only(root, "`pkg/f.sh:1,3-4` `pkg/f.sh:4`\n") == []
    errors = _paths_only(root, "`pkg/f.sh:2,5` `pkg/f.sh:3-2` `pkg/f.sh:0`\n")
    assert len(errors) == 3, errors
    assert any(
        e.endswith("notes.md:1: `pkg/f.sh:2,5`: line 5 is out of range (pkg/f.sh has 4 lines)")
        for e in errors
    )


def test_directory_refs_and_directory_with_line_number(tmp_path):
    root = _tree(tmp_path, {"pkg/sub/f.py": "x\n"})
    assert _paths_only(root, "`pkg/sub/` `pkg/`\n") == []
    errors = _paths_only(root, "`pkg/sub:1`\n")
    assert errors == ["%s:1: `pkg/sub:1`: pkg/sub is a directory, not a file" % (root / "notes.md")]


def test_basename_and_suffix_resolution(tmp_path):
    root = _tree(tmp_path, {"a/x.py": "1\n", "b/x.py": "1\n", "pkg/sub/y.py": "1\n"})
    assert _paths_only(root, "`sub/y.py:1` `y.py:1` `a/x.py:1`\n") == []
    errors = _paths_only(root, "`x.py:1`\n")
    assert len(errors) == 1 and "is ambiguous (a/x.py, b/x.py)" in errors[0]


def test_workflow_alias(tmp_path):
    root = _tree(tmp_path, {"workflows/archon-dark-factory.yaml": "nodes: []\n"})
    assert _paths_only(root, "`workflow:1`\n") == []
    assert len(_paths_only(root, "`workflow:2`\n")) == 1
    assert _paths_only(root, "the `workflow` word alone\n") == []


def test_missing_paths(tmp_path):
    root = _tree(tmp_path, {"scripts/real.sh": "x\n"})
    errors = _paths_only(root, "`scripts/gone.sh` `gone.py:3` `pkg/gone.py`\n")
    assert len(errors) == 3, errors


def test_spans_that_are_not_repo_paths_are_skipped(tmp_path):
    root = _tree(tmp_path, {"scripts/real.sh": "x\n"})
    doc = (
        "`implementation.md` `/opt/refinement-skills/config.yaml` `origin/main` "
        "`frontend/` `.archon/.env` `deploy/instance.env` `.archon/commands` "
        "`../README.md` `:586-591` `scripts/*.sh` `${ARTIFACTS_DIR}/plan.md` `poll_interval`\n"
    )
    assert _paths_only(root, doc) == []


def test_nested_dark_factory_dir_and_fixtures_are_not_searched(tmp_path):
    root = _tree(tmp_path, {
        "scripts/factory_core/adapter.py": "1\n",
        "dark-factory/scripts/factory_core/adapter.py": "1\n",
        "tests/fixtures/t/scripts/factory_core/adapter.py": "1\n",
        "deploy/instances/dark-factory/instance.env": "X=1\n",
    })
    assert _paths_only(root, "`adapter.py:1` `deploy/instances/dark-factory/instance.env`\n") == []


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_index_ignores_ignored_and_deleted_files(tmp_path):
    root = _tree(tmp_path, {
        ".gitignore": "staged/\n",
        "scripts/x.py": "1\n",
        "staged/x.py": "1\n",
        "scripts/deleted.py": "1\n",
    })
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    (root / "scripts" / "deleted.py").unlink()
    assert _paths_only(root, "`x.py:1`\n") == []
    assert len(_paths_only(root, "`scripts/deleted.py`\n")) == 1
