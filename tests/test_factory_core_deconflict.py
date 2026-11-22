import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core.deconflict import tier1, tier2, hard_grep_survivors


# ---------------------------------------------------------------------------
# tier1 — allowlist files
# ---------------------------------------------------------------------------

def test_tier1_codeindex_calls_checkout_theirs(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd),
        subprocess.CompletedProcess([], 0, stdout="", stderr=""))[1])
    assert tier1("codeindex.json", "/repo") is True
    assert any("--theirs" in c for c in calls)
    assert any("codeindex.json" in c for c in calls)


def test_tier1_symbolindex_calls_checkout_theirs(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd),
        subprocess.CompletedProcess([], 0, stdout="", stderr=""))[1])
    assert tier1("symbolindex.json", "/repo") is True


def test_tier1_alembic_migration(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd),
        subprocess.CompletedProcess([], 0, stdout="", stderr=""))[1])
    assert tier1("backend/alembic/versions/abc123_add_foo.py", "/repo") is True
    assert any("--theirs" in c for c in calls)


def test_tier1_models_init_merges_imports(tmp_path):
    models_init = tmp_path / "backend" / "app" / "models" / "__init__.py"
    models_init.parent.mkdir(parents=True)
    models_init.write_text(
        "<<<<<<< HEAD\n"
        "from .foo import Foo\n"
        "=======\n"
        "from .bar import Bar\n"
        ">>>>>>> feature\n"
    )
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True)

    result = tier1("backend/app/models/__init__.py", str(tmp_path))
    assert result is True
    content = models_init.read_text()
    assert "<<<<<<<" not in content
    assert "from .foo import Foo" in content
    assert "from .bar import Bar" in content


def test_tier1_models_init_deduplicates_imports(tmp_path):
    models_init = tmp_path / "backend" / "app" / "models" / "__init__.py"
    models_init.parent.mkdir(parents=True)
    models_init.write_text(
        "<<<<<<< HEAD\n"
        "from .foo import Foo\n"
        "from .bar import Bar\n"
        "=======\n"
        "from .bar import Bar\n"
        "from .baz import Baz\n"
        ">>>>>>> feature\n"
    )
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True)

    tier1("backend/app/models/__init__.py", str(tmp_path))
    content = models_init.read_text()
    assert content.count("from .bar import Bar") == 1


def test_tier1_unknown_file_returns_false(tmp_path):
    assert tier1("src/random.py", str(tmp_path)) is False


# ---------------------------------------------------------------------------
# tier2 — sentinel contract
# ---------------------------------------------------------------------------

SENTINEL_RESPONSE = (
    "Some preamble Claude adds\n"
    "===BEGIN_RESOLVED_FILE===\n"
    "resolved content line 1\n"
    "resolved content line 2\n"
    "===END_RESOLVED_FILE===\n"
)


def test_tier2_sentinel_success(tmp_path, monkeypatch):
    f = tmp_path / "foo.py"
    f.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> feature\n")

    def fake_run(cmd, **kw):
        if "claude" in " ".join(cmd):
            return subprocess.CompletedProcess([], 0, stdout=SENTINEL_RESPONSE, stderr="")
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert tier2("foo.py", 42, "omniscient", "markethawk", str(tmp_path)) is True
    content = f.read_text()
    assert "resolved content line 1" in content
    assert "<<<<<<<" not in content


def test_tier2_no_sentinel_returns_false(tmp_path, monkeypatch):
    f = tmp_path / "foo.py"
    original = "<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> feature\n"
    f.write_text(original)

    def fake_run(cmd, **kw):
        if "claude" in " ".join(cmd):
            return subprocess.CompletedProcess([], 0,
                stdout="Here is the resolved content (no sentinels): resolved content", stderr="")
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert tier2("foo.py", 42, "omniscient", "markethawk", str(tmp_path)) is False
    assert f.read_text() == original


def test_tier2_malformed_sentinel_returns_false(tmp_path, monkeypatch):
    f = tmp_path / "foo.py"
    f.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> feature\n")

    def fake_run(cmd, **kw):
        if "claude" in " ".join(cmd):
            # Only BEGIN, no END
            return subprocess.CompletedProcess([], 0,
                stdout="===BEGIN_RESOLVED_FILE===\nresolved\n", stderr="")
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert tier2("foo.py", 42, "omniscient", "markethawk", str(tmp_path)) is False


def test_tier2_claude_error_returns_false(tmp_path, monkeypatch):
    f = tmp_path / "foo.py"
    f.write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> feature\n")

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw:
        subprocess.CompletedProcess([], 1, stdout="", stderr="claude error"))
    assert tier2("foo.py", 42, "omniscient", "markethawk", str(tmp_path)) is False


# ---------------------------------------------------------------------------
# hard_grep_survivors — exclusion rules
# ---------------------------------------------------------------------------

def test_hard_grep_survivors_finds_markers(tmp_path):
    (tmp_path / "foo.py").write_text("<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> f\n")
    result = hard_grep_survivors(str(tmp_path))
    assert "foo.py" in result


def test_hard_grep_survivors_excludes_node_modules(tmp_path):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "bar.js").write_text("<<<<<<< HEAD\n")
    result = hard_grep_survivors(str(tmp_path))
    assert not any("node_modules" in r for r in result)


def test_hard_grep_survivors_excludes_git(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "ORIG_HEAD").write_text("<<<<<<< HEAD\n")
    result = hard_grep_survivors(str(tmp_path))
    assert not any(".git" in r for r in result)


def test_hard_grep_survivors_clean_returns_empty(tmp_path):
    (tmp_path / "clean.py").write_text("def foo(): pass\n")
    assert hard_grep_survivors(str(tmp_path)) == []


def test_hard_grep_survivors_skips_unlisted_extensions(tmp_path):
    (tmp_path / "binary.exe").write_bytes(b"<<<<<<< HEAD\n")
    result = hard_grep_survivors(str(tmp_path))
    assert "binary.exe" not in result


# ---------------------------------------------------------------------------
# resolve_merge_conflicts — orchestrator
# ---------------------------------------------------------------------------

def test_resolve_clean_merge_writes_artifact(tmp_path, monkeypatch):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    def fake_run(cmd, **kw):
        cmd_str = " ".join(cmd)
        if "fetch" in cmd_str:
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")
        if "merge" in cmd_str and "origin/main" in cmd_str:
            return subprocess.CompletedProcess([], 0, stdout="Already up to date.", stderr="")
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    from factory_core.deconflict import resolve_merge_conflicts
    rc = resolve_merge_conflicts(42, str(tmp_path), "omniscient", "markethawk", str(artifact_dir))
    assert rc == 0
    artifact = (artifact_dir / "conflict_resolution.md").read_text()
    assert "CONFLICT_VERDICT=none" in artifact
    assert "ESCALATED=0" in artifact


def test_resolve_escalation_on_no_conflicted_files(tmp_path, monkeypatch):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()

    def fake_run(cmd, **kw):
        cmd_str = " ".join(cmd)
        if "fetch" in cmd_str:
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")
        if "merge" in cmd_str and "origin/main" in cmd_str:
            return subprocess.CompletedProcess([], 1, stdout="error", stderr="")
        if "diff" in cmd_str and "--diff-filter=U" in cmd_str:
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with patch("factory_core.board.set_board_status"):
        from factory_core.deconflict import resolve_merge_conflicts
        rc = resolve_merge_conflicts(42, str(tmp_path), "omniscient", "markethawk", str(artifact_dir))
    assert rc == 1
    artifact = (artifact_dir / "conflict_resolution.md").read_text()
    assert "CONFLICT_VERDICT=escalate" in artifact
    assert "ESCALATED=1" in artifact


# ---------------------------------------------------------------------------
# resolve_merge_conflicts — alembic multi-head check (#576)
# ---------------------------------------------------------------------------

_HEADS = ["python", "-m", "alembic", "heads"]
_MERGE_HEADS = ["python", "-m", "alembic", "merge", "heads"]


def _cp(rc=0, stdout=""):
    return subprocess.CompletedProcess([], rc, stdout=stdout, stderr="")


def _fake_conflicted_run(calls, conflicted=("codeindex.json",), heads_stdout="",
                         untracked=(), heads_exc=None):
    """subprocess.run fake: merge origin/main conflicts on `conflicted`; Tier 1
    resolves them; `alembic heads` prints heads_stdout (or raises heads_exc);
    each `git ls-files --others` call returns the next entry of `untracked`."""
    listings = list(untracked)

    def fake_run(cmd, **kw):
        cmd = list(cmd)
        cwd = kw.get("cwd")
        calls.append((cmd, cwd))
        if cwd is not None and not os.path.isdir(cwd):
            # real subprocess.run raises before exec on a missing cwd (the #576 crash)
            raise FileNotFoundError(2, "No such file or directory", cwd)
        if cmd[:2] == ["git", "merge"] and "origin/main" in cmd:
            return _cp(1, "CONFLICT")
        if cmd[:2] == ["git", "diff"] and "--diff-filter=U" in cmd:
            return _cp(0, "\n".join(conflicted) + "\n")
        if cmd[:4] == _HEADS:
            if heads_exc is not None:
                raise heads_exc
            return _cp(0, heads_stdout)
        if cmd[:3] == ["git", "ls-files", "--others"]:
            return _cp(0, listings.pop(0) if listings else "")
        return _cp(0)

    return fake_run


def _run_resolve(tmp_path, monkeypatch, fake_run):
    """Run resolve_merge_conflicts against tmp_path; return (rc, artifact, escalations)."""
    from factory_core import deconflict as dc
    escalations = []
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(dc, "_escalate", lambda *a: escalations.append(a))
    artifact_dir = tmp_path / "artifacts"
    rc = dc.resolve_merge_conflicts(42, str(tmp_path), "omniscient", "dark-factory",
                                    str(artifact_dir))
    artifact = (artifact_dir / "conflict_resolution.md").read_text()
    return rc, artifact, escalations


def _alembic_calls(calls):
    return [(c, cwd) for c, cwd in calls if "alembic" in c]


def _write_ini(tmp_path, rel="backend/alembic.ini"):
    ini = tmp_path / rel
    ini.parent.mkdir(parents=True, exist_ok=True)
    ini.write_text("[alembic]\nscript_location = app/alembic\n")
    return ini


def test_resolve_skips_alembic_check_when_no_backend(tmp_path, monkeypatch, capsys):
    calls = []
    rc, artifact, escalations = _run_resolve(
        tmp_path, monkeypatch, _fake_conflicted_run(calls))
    assert rc == 0
    assert _alembic_calls(calls) == []
    assert escalations == []
    assert "CONFLICT_VERDICT=tier1" in artifact
    assert any(c[:2] == ["git", "commit"] for c, _ in calls)
    assert "[deconflict] alembic check skipped: backend/alembic.ini not found" \
        in capsys.readouterr().out


def test_resolve_skips_alembic_check_when_backend_has_no_ini(tmp_path, monkeypatch, capsys):
    (tmp_path / "backend").mkdir()
    calls = []
    rc, artifact, escalations = _run_resolve(
        tmp_path, monkeypatch, _fake_conflicted_run(calls))
    assert rc == 0
    assert _alembic_calls(calls) == []
    assert escalations == []
    assert "CONFLICT_VERDICT=tier1" in artifact
    assert "alembic check skipped: backend/alembic.ini not found" in capsys.readouterr().out


def test_resolve_alembic_check_respects_adapter_override(tmp_path, monkeypatch):
    _write_ini(tmp_path, "services/api/alembic.ini")
    (tmp_path / ".factory").mkdir()
    (tmp_path / ".factory" / "adapter.yaml").write_text(
        "deconflict:\n  alembic_ini: 'services/api/alembic.ini'\n")
    calls = []
    rc, _, escalations = _run_resolve(
        tmp_path, monkeypatch,
        _fake_conflicted_run(calls, heads_stdout="abc123 (head)\n"))
    assert rc == 0
    assert escalations == []
    assert _alembic_calls(calls) == [(_HEADS, str(tmp_path / "services" / "api"))]


@pytest.mark.parametrize("exc", [
    OSError("No module named alembic"),
    subprocess.CalledProcessError(1, _HEADS),
])
def test_resolve_escalates_on_alembic_check_failure(tmp_path, monkeypatch, exc):
    _write_ini(tmp_path)
    calls = []
    rc, artifact, escalations = _run_resolve(
        tmp_path, monkeypatch, _fake_conflicted_run(calls, heads_exc=exc))
    assert rc == 1
    assert len(escalations) == 1
    assert "Alembic multi-head check failed unexpectedly" in escalations[0][3]
    assert (["git", "merge", "--abort"], str(tmp_path)) in calls
    assert not any(c[:2] == ["git", "commit"] for c, _ in calls)
    assert "CONFLICT_VERDICT=escalate" in artifact
    assert "ESCALATED=1" in artifact


def test_resolve_alembic_single_head_does_not_merge(tmp_path, monkeypatch):
    _write_ini(tmp_path)
    calls = []
    rc, _, escalations = _run_resolve(
        tmp_path, monkeypatch,
        _fake_conflicted_run(calls, heads_stdout="abc123 (head)\n"))
    assert rc == 0
    assert escalations == []
    assert _alembic_calls(calls) == [(_HEADS, str(tmp_path / "backend"))]
    assert not any(c[:3] == ["git", "add", "--"] for c, _ in calls)


def test_resolve_alembic_multi_head_merges_and_stages_generated_revision(tmp_path, monkeypatch):
    _write_ini(tmp_path)
    stray = "backend/app/alembic/versions/stray_local.py"
    rev = "backend/app/alembic/versions/ccc333_merge_branches_issue_42.py"
    calls = []
    rc, artifact, escalations = _run_resolve(
        tmp_path, monkeypatch,
        _fake_conflicted_run(calls, heads_stdout="aaa111 (head)\nbbb222 (head)\n",
                             untracked=[stray + "\n", f"{stray}\n{rev}\n"]))
    assert rc == 0
    assert escalations == []
    backend = str(tmp_path / "backend")
    merge = [(c, cwd) for c, cwd in calls if c[:5] == _MERGE_HEADS]
    assert merge == [(_MERGE_HEADS + ["-m", "merge_branches_issue_42"], backend)]
    listings = [c for c, _ in calls if c[:3] == ["git", "ls-files", "--others"]]
    assert listings and all(c[-2:] == ["--", "backend"] for c in listings)
    assert (["git", "add", "--", rev], str(tmp_path)) in calls
    assert "CONFLICT_VERDICT=tier1" in artifact


def test_resolve_escalates_when_alembic_merge_generates_no_file(tmp_path, monkeypatch):
    _write_ini(tmp_path)
    calls = []
    rc, artifact, escalations = _run_resolve(
        tmp_path, monkeypatch,
        _fake_conflicted_run(calls, heads_stdout="aaa111 (head)\nbbb222 (head)\n",
                             untracked=["", ""]))
    assert rc == 1
    assert len(escalations) == 1
    assert "produced no new revision file" in escalations[0][3]
    assert (["git", "merge", "--abort"], str(tmp_path)) in calls
    assert "CONFLICT_VERDICT=escalate" in artifact


def test_resolve_escalates_on_migration_conflict_without_alembic_ini(tmp_path, monkeypatch):
    migration = "backend/app/alembic/versions/abc123_add_col.py"
    calls = []
    rc, artifact, escalations = _run_resolve(
        tmp_path, monkeypatch, _fake_conflicted_run(calls, conflicted=(migration,)))
    assert rc == 1
    assert _alembic_calls(calls) == []
    assert len(escalations) == 1
    assert migration in escalations[0][3]
    assert "deconflict.alembic_ini" in escalations[0][3]
    assert (["git", "merge", "--abort"], str(tmp_path)) in calls
    assert "CONFLICT_VERDICT=escalate" in artifact


def test_resolve_alembic_listing_path_is_clone_relative_for_symlinked_clone(tmp_path, monkeypatch):
    # The ini path is realpath'd; the clone dir must be too, or the listing pathspec
    # becomes "../<real>/backend", which is outside the repository for git.
    real = tmp_path / "real"
    real.mkdir()
    _write_ini(real)
    link = tmp_path / "link"
    try:
        os.symlink(real, link, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not available")
    rev = "backend/app/alembic/versions/ccc333_merge_branches_issue_42.py"
    calls = []
    rc, _, escalations = _run_resolve(
        link, monkeypatch,
        _fake_conflicted_run(calls, heads_stdout="aaa111 (head)\nbbb222 (head)\n",
                             untracked=["", rev + "\n"]))
    assert rc == 0
    assert escalations == []
    listings = [c for c, _ in calls if c[:3] == ["git", "ls-files", "--others"]]
    assert listings and all(c[-2:] == ["--", "backend"] for c in listings)
    assert (["git", "add", "--", rev], str(link)) in calls
