"""Tests for docs_refs_check.py — deterministic reference-doc checks (#537, #662).

The pass/fail fixture trees under tests/fixtures/docs_refs_check/ mirror the repo layout
(docs/, scripts/, config/, workflows/) so check() runs on them with its defaults; fail/
carries exactly one broken reference per check category.
"""

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest
import yaml

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from docs_refs_check import LOCK_FILE, REPAIR_COMMAND, check, main, rederive, seed  # noqa: E402

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


# ---------------------------------------------------------------------------
# Config keys (configuration.md only)
# ---------------------------------------------------------------------------

_CONFIG_DOC = (
    "# Config\n\n## Layer 1: `config/config.yaml`\n\n"
    "| Key | Default |\n|---|---|\n"
    "| `a.b` | `1` |\n| `a.items` | `[x]` |\n| `budgets.code-review` | `2` |\n"
    "\n## Layer 2: env\n\n| Key | Default |\n|---|---|\n| `env.only` | `1` |\n"
)


def _config_check(tmp_path: Path, doc: str, config: str) -> list[str]:
    root = _tree(tmp_path, {"docs/configuration.md": doc, "config/config.yaml": config})
    return check([root / "docs" / "configuration.md"], root)


def test_config_keys_round_trip(tmp_path):
    config = "a:\n  b: 1\n  items: [x, y]\nbudgets:\n  code-review: 2\n"
    assert _config_check(tmp_path, _CONFIG_DOC, config) == []


def test_config_key_documented_but_removed(tmp_path):
    errors = _config_check(tmp_path, _CONFIG_DOC, "a:\n  items: [x]\nbudgets:\n  code-review: 2\n")
    assert len(errors) == 1 and "configuration.md:7: config key `a.b` is documented but is not in" in errors[0]


def test_config_key_added_without_docs(tmp_path):
    config = "a:\n  b: 1\n  c: 3\n  items: [x]\nbudgets:\n  code-review: 2\n"
    errors = _config_check(tmp_path, _CONFIG_DOC, config)
    assert len(errors) == 1 and "config key `a.c`" in errors[0] and "is not documented" in errors[0]


# ---------------------------------------------------------------------------
# Workflow nodes (features.md only)
# ---------------------------------------------------------------------------

_WORKFLOW = "nodes:\n  - id: alpha\n  - id: beta-step\n  - id: gamma\n"


def _node_check(tmp_path: Path, doc: str, name: str = "features.md") -> list[str]:
    root = _tree(tmp_path, {f"docs/{name}": doc, "workflows/archon-dark-factory.yaml": _WORKFLOW})
    return check([root / "docs" / name], root)


def test_node_lists_and_asides(tmp_path):
    doc = (
        "Nodes `alpha` (`workflow:1`; `mark-ready` `:2`), `beta-step` and `gamma`.\n"
        "workflow node `alpha`. Per-node `model:` pins. The `smoke-gate` hook.\n"
    )
    assert _node_check(tmp_path, doc) == []


def test_unknown_node_is_reported(tmp_path):
    errors = _node_check(tmp_path, "Nodes `alpha` (`workflow:1`) and `delta`.\n")
    assert len(errors) == 1 and "features.md:1: workflow node `delta` is not a node id" in errors[0]


def test_node_check_only_runs_on_features_md(tmp_path):
    assert _node_check(tmp_path, "Node `delta`.\n", name="scheduler.md") == []


# ---------------------------------------------------------------------------
# Fixture trees: one pass, one fail with one break per check
# ---------------------------------------------------------------------------

def test_pass_fixture_has_no_errors():
    root, docs = _fixture_docs("pass")
    assert check(docs, root) == []


def test_fail_fixture_reports_one_error_per_check():
    root, docs = _fixture_docs("fail")
    errors = check(docs, root)
    assert len(errors) == 4, errors
    joined = "\n".join(errors)
    assert "features.md:5: `scripts/missing.sh`: no such file or directory" in joined
    assert "features.md:6: `scripts/tool.sh:1,4-9`: line 4-9 is out of range" in joined
    assert "config key `scheduler.max_retries`" in joined and "is not documented" in joined
    assert "features.md:5: workflow node `deploy-step` is not a node id" in joined


def test_cli_exit_codes(capsys):
    root, docs = _fixture_docs("pass")
    assert main([*map(str, docs), "--repo-root", str(root)]) == 0
    root, docs = _fixture_docs("fail")
    assert main([*map(str, docs), "--repo-root", str(root)]) == 1
    assert "deploy-step" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Citation lock: content check for commands/*.md citations (#662)
# ---------------------------------------------------------------------------

_CMD = "".join(f"line {i}\n" for i in range(1, 21))  # 20 distinct lines


def _lock_tree(tmp_path: Path, doc: str, cmd: str = _CMD, extra: Optional[dict] = None) -> Path:
    files = {"commands/c.md": cmd, "docs/notes.md": doc, **(extra or {})}
    return _tree(tmp_path, files)


def _doc(root: Path) -> Path:
    return root / "docs" / "notes.md"


def _content(root: Path) -> list[str]:
    """check() on docs/notes.md only, with config/workflow checks pointed at nothing."""
    return check([_doc(root)], root, root / "none.yaml", root / "none.yaml")


def _seeded(root: Path) -> Path:
    seed([_doc(root)], root)
    return root / LOCK_FILE


def _insert(root: Path, at: int, lines: list[str], rel: str = "commands/c.md") -> None:
    p = root / rel
    old = p.read_text(encoding="utf-8").splitlines(keepends=True)
    p.write_text("".join(old[:at] + [f"{x}\n" for x in lines] + old[at:]), encoding="utf-8")


def test_no_lock_file_means_range_only(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3`\n")
    _insert(root, 0, ["new"])
    assert _content(root) == []
    assert not (root / LOCK_FILE).exists()


def test_seed_writes_sorted_seeded_entries_and_is_byte_stable(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:7-8` and `commands/c.md:3`\n")
    lock = _seeded(root)
    text = lock.read_text(encoding="utf-8")
    assert text.endswith("seeded: true\n") and not text.endswith("\n\n")
    data = yaml.safe_load(text)
    assert [e["lines"] for e in data["entries"]] == [[3, 3], [7, 8]]
    assert list(data["entries"][0]) == ["doc", "target", "lines", "hash", "excerpt", "seeded"]
    assert data["entries"][0]["doc"] == "docs/notes.md"
    assert data["entries"][0]["target"] == "commands/c.md"
    assert data["entries"][0]["excerpt"] == "line 3"
    assert '  - doc: "docs/notes.md"\n' in text
    assert _content(root) == []
    seed([_doc(root)], root)
    assert lock.read_text(encoding="utf-8") == text


def test_seed_never_overwrites_an_entry(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3`\n")
    lock = _seeded(root)
    before = lock.read_text(encoding="utf-8")
    (root / "commands" / "c.md").write_text(_CMD.replace("line 3\n", "edited\n"), encoding="utf-8")
    seed([_doc(root)], root)
    assert lock.read_text(encoding="utf-8") == before


def test_hash_match_and_mismatch(tmp_path):
    root = _lock_tree(tmp_path, "intro\n`commands/c.md:3-4`\n")
    _seeded(root)
    assert _content(root) == []
    (root / "commands" / "c.md").write_text(_CMD.replace("line 4\n", "edited\n"), encoding="utf-8")
    errors = _content(root)
    assert errors == [
        'docs/notes.md:2: `commands/c.md:3-4`: commands/c.md:3-4 no longer holds the locked '
        f'content (locked: "line 3"); run: {REPAIR_COMMAND}'
    ]


def test_messages_never_claim_verification(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3`\n")
    _seeded(root)
    _insert(root, 0, ["new"])
    errors = _content(root)
    assert errors and not any("verified" in e or "audited" in e for e in errors)


def test_missing_entry_and_orphan_entry(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3`\n")
    _seeded(root)
    _doc(root).write_text("`commands/c.md:3` `commands/c.md:9`\n", encoding="utf-8")
    errors = _content(root)
    assert len(errors) == 1 and "commands/c.md:9 has no entry in docs/.citation-lock.yaml" in errors[0]
    assert errors[0].endswith(REPAIR_COMMAND)
    _doc(root).write_text("no citations left\n", encoding="utf-8")
    errors = _content(root)
    assert len(errors) == 1
    assert 'lock entry commands/c.md:3 ("line 3") is no longer cited' in errors[0]


def test_crlf_and_reindented_target_lines_hash_the_same(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3-4`\n")
    _seeded(root)
    crlf = _CMD.replace("line 3\n", "    line 3\t\n").replace("\n", "\r\n")
    (root / "commands" / "c.md").write_bytes(crlf.encode("utf-8"))
    assert _content(root) == []


def test_basename_citation_is_covered(tmp_path):
    root = _lock_tree(tmp_path, "`c.md:3`\n")
    lock = _seeded(root)
    assert "commands/c.md" in lock.read_text(encoding="utf-8")
    _insert(root, 0, ["new"])
    assert len(_content(root)) == 1


def test_non_commands_citations_stay_range_only(tmp_path):
    files = {
        "scripts/s.py": "a\nb\n", "scheduler.sh": "a\nb\n", "entrypoint.sh": "a\nb\n",
        "workflows/archon-dark-factory.yaml": "nodes: []\nx: 1\n", "commands/sub/n.md": "a\nb\n",
    }
    doc = "`commands/c.md:3` `scripts/s.py:2` `scheduler.sh:2` `entrypoint.sh:1` `workflow:2` `commands/sub/n.md:2`\n"
    root = _lock_tree(tmp_path, doc, extra=files)
    lock = _seeded(root)
    assert len(yaml.safe_load(lock.read_text(encoding="utf-8"))["entries"]) == 1
    for rel in files:
        _insert(root, 0, ["new"], rel)
    assert _content(root) == []


def test_comma_chain_locked_and_rewritten_per_part(tmp_path):
    root = _lock_tree(tmp_path, "see `commands/c.md:3,7-8,12`\n")
    lock = _seeded(root)
    assert len(yaml.safe_load(lock.read_text(encoding="utf-8"))["entries"]) == 3
    (root / "commands" / "c.md").write_text(
        _CMD.replace("line 10\n", "line 10\nnew a\nnew b\n"), encoding="utf-8")
    assert len(_content(root)) == 1
    printed, failures = rederive([_doc(root)], root)
    assert failures == []
    assert _doc(root).read_text(encoding="utf-8") == "see `commands/c.md:3,7-8,14`\n"
    assert printed[0] == "docs/notes.md:1: `commands/c.md:3,7-8,12` -> `commands/c.md:3,7-8,14`"


def _git_tree(tmp_path: Path, doc: str, cmd: str) -> Path:
    root = _lock_tree(tmp_path, doc, cmd)
    _seeded(root)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qm", "base"], check=True)
    return root


_BRACES = "a\n{\nb\n{\nc\n{\nd\n"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_repeated_line_is_ambiguous_without_base_and_relocated_with_it(tmp_path):
    root = _git_tree(tmp_path, "`commands/c.md:4`\n", _BRACES)
    lock = root / LOCK_FILE
    _insert(root, 0, ["new"])  # the cited `{` is now one of lines 3, 5 and 7
    doc_before, lock_before = _doc(root).read_text(encoding="utf-8"), lock.read_text(encoding="utf-8")
    printed, failures = rederive([_doc(root)], root)
    assert printed == [] and len(failures) == 1
    assert "ambiguous, the locked content occurs at commands/c.md:3, 5, 7" in failures[0]
    assert (_doc(root).read_text(encoding="utf-8"), lock.read_text(encoding="utf-8")) == (doc_before, lock_before)
    printed, failures = rederive([_doc(root)], root, base="HEAD")
    assert failures == []
    assert _doc(root).read_text(encoding="utf-8") == "`commands/c.md:5`\n"


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_base_diff_touching_the_range_is_ambiguous(tmp_path):
    root = _git_tree(tmp_path, "`commands/c.md:4-5`\n", "a\n{\nx\n{\nx\n{\nx\n")
    # Edit line 5: the locked pair still occurs at 2-3 and 6-7, but the hunk touches 4-5.
    (root / "commands" / "c.md").write_text("a\n{\nx\n{\ny\n{\nx\n", encoding="utf-8")
    printed, failures = rederive([_doc(root)], root, base="HEAD")
    assert printed == [] and len(failures) == 1 and "the --base diff does not pick one" in failures[0]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_unavailable_is_ambiguous(tmp_path, monkeypatch):
    root = _git_tree(tmp_path, "`commands/c.md:4`\n", _BRACES)
    _insert(root, 0, ["new"])
    import docs_refs_check

    def no_git(*args, **kwargs):
        raise OSError("git not found")
    monkeypatch.setattr(docs_refs_check.subprocess, "run", no_git)
    printed, failures = rederive([_doc(root)], root, base="HEAD")
    assert printed == [] and len(failures) == 1 and "does not pick one" in failures[0]


def test_hand_shifted_citation_is_rekeyed_and_keeps_seeded(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3` `commands/c.md:5`\n")
    lock = _seeded(root)
    _insert(root, 0, ["new"])
    # Citation 3 was shifted by hand, correctly; citation 5 was not touched.
    _doc(root).write_text("`commands/c.md:4` `commands/c.md:5`\n", encoding="utf-8")
    printed, failures = rederive([_doc(root)], root)
    assert failures == [] and _content(root) == []
    assert _doc(root).read_text(encoding="utf-8") == "`commands/c.md:4` `commands/c.md:6`\n"
    entries = yaml.safe_load(lock.read_text(encoding="utf-8"))["entries"]
    assert [(e["lines"], e.get("seeded")) for e in entries] == [([4, 4], True), ([6, 6], True)]


def test_new_prose_is_locked_without_seeded(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3`\n")
    lock = _seeded(root)
    _doc(root).write_text("`commands/c.md:3` `commands/c.md:12`\n", encoding="utf-8")
    assert rederive([_doc(root)], root)[1] == []
    assert _content(root) == []
    entries = {tuple(e["lines"]): e for e in yaml.safe_load(lock.read_text(encoding="utf-8"))["entries"]}
    assert entries[(3, 3)].get("seeded") is True and "seeded" not in entries[(12, 12)]


def test_removed_prose_alone_removes_the_orphan(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3` `commands/c.md:5`\n")
    lock = _seeded(root)
    _doc(root).write_text("`commands/c.md:3`\n", encoding="utf-8")
    assert rederive([_doc(root)], root)[1] == []
    assert [e["lines"] for e in yaml.safe_load(lock.read_text(encoding="utf-8"))["entries"]] == [[3, 3]]


def test_mixed_group_fails_and_writes_nothing(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3`\n")
    lock = _seeded(root)
    _doc(root).write_text("`commands/c.md:9`\n", encoding="utf-8")
    before = lock.read_text(encoding="utf-8")
    printed, failures = rederive([_doc(root)], root)
    assert printed == [] and len(failures) == 1
    assert "commands/c.md:9 is not the locked content" in failures[0]
    assert "locked content is now at commands/c.md:3" in failures[0]
    assert lock.read_text(encoding="utf-8") == before
    assert _doc(root).read_text(encoding="utf-8") == "`commands/c.md:9`\n"


def test_wrong_hand_delta_reports_correct_lines_and_locks_nothing(tmp_path):
    # Tonight's case: five citations, 16 lines inserted above, doc shifted by 15 instead.
    root = _lock_tree(tmp_path, "`commands/c.md:3` `:x` `commands/c.md:5` `commands/c.md:7-8`\n"
                                "`commands/c.md:10` `commands/c.md:12`\n")
    lock = _seeded(root)
    _insert(root, 0, [f"inserted {i}" for i in range(16)])
    wrong = "`commands/c.md:18` `:x` `commands/c.md:20` `commands/c.md:22-23`\n`commands/c.md:25` `commands/c.md:27`\n"
    _doc(root).write_text(wrong, encoding="utf-8")
    before = lock.read_text(encoding="utf-8")
    printed, failures = rederive([_doc(root)], root)
    assert printed == [] and len(failures) == 5
    for now in ("19", "21", "23-24", "26", "28"):
        assert any(f"locked content is now at commands/c.md:{now}" in f for f in failures), now
    assert lock.read_text(encoding="utf-8") == before
    assert _doc(root).read_text(encoding="utf-8") == wrong


def test_zero_matches_fails_and_changes_nothing(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3`\n")
    lock = _seeded(root)
    (root / "commands" / "c.md").write_text(_CMD.replace("line 3\n", ""), encoding="utf-8")
    before = lock.read_text(encoding="utf-8")
    printed, failures = rederive([_doc(root)], root)
    assert printed == [] and len(failures) == 1 and lock.read_text(encoding="utf-8") == before


def test_same_citation_twice_in_one_doc(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3`\nagain `commands/c.md:3`\n")
    lock = _seeded(root)
    assert len(yaml.safe_load(lock.read_text(encoding="utf-8"))["entries"]) == 1
    _insert(root, 0, ["new"])
    assert len(_content(root)) == 2
    printed, failures = rederive([_doc(root)], root)
    assert failures == [] and len(printed) == 3  # two rewrites + lock updated
    assert _doc(root).read_text(encoding="utf-8") == "`commands/c.md:4`\nagain `commands/c.md:4`\n"


def test_failing_citation_alongside_a_fixable_one(tmp_path):
    root = _lock_tree(tmp_path, "`commands/c.md:3` `commands/c.md:9`\n")
    _seeded(root)
    (root / "commands" / "c.md").write_text("new\n" + _CMD.replace("line 9\n", "edited\n"), encoding="utf-8")
    printed, failures = rederive([_doc(root)], root)
    assert len(failures) == 1 and "commands/c.md:9" in failures[0]
    assert _doc(root).read_text(encoding="utf-8") == "`commands/c.md:4` `commands/c.md:9`\n"


def test_moved_content_never_lands_on_lines_locked_for_other_content(tmp_path):
    # Line 3 moves onto line 4, whose citation was edited in place: must not lock it.
    root = _lock_tree(tmp_path, "`commands/c.md:3` `commands/c.md:4`\n")
    lock = _seeded(root)
    (root / "commands" / "c.md").write_text(_CMD.replace("line 4\n", "").replace("line 3\n", "x\nline 3\n"),
                                            encoding="utf-8")
    before = lock.read_text(encoding="utf-8")
    printed, failures = rederive([_doc(root)], root)
    assert len(failures) == 2 and any("but those lines are locked for other content" in f for f in failures)
    assert lock.read_text(encoding="utf-8") == before
