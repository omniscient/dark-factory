"""#490: filter_hotspots drops rows for run-time staged (git-excluded) files."""
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "filter_hotspots.py"

RAW = (
    "Files with blast score ≥ 5.0  (4 found)\n\n"
    "    10.5  dark-factory/scripts/a.py  (7d / 7t)  141 loc\n"
    "    10.5  scripts/a.py  (7d / 7t)  141 loc\n"
    "     9.0  dark-factory/scripts/b.py  (6d / 6t)  39 loc\n"
    "     9.0  scripts/b.py  (6d / 6t)  39 loc\n"
)


def _git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *a],
                   check=True, capture_output=True)


def _run(cwd, text):
    return subprocess.run([sys.executable, str(SCRIPT), "--repo", str(cwd)], input=text,
                          capture_output=True, text=True, check=True,
                          encoding="utf-8").stdout


def test_drops_git_excluded_staged_rows(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "a.py").write_text("x")
    _git(tmp_path, "add", "-A")
    (tmp_path / ".git" / "info" / "exclude").write_text("dark-factory/scripts/\n")
    out = _run(tmp_path, RAW)
    assert "dark-factory/" not in out
    assert "scripts/a.py" in out and "scripts/b.py" in out
    assert "(2 found)" in out


def test_keeps_vendored_tracked_dark_factory(tmp_path):
    """A target that genuinely vendors dark-factory/scripts keeps its rows."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "dark-factory" / "scripts").mkdir(parents=True)
    (tmp_path / "dark-factory" / "scripts" / "a.py").write_text("x")
    _git(tmp_path, "add", "-A")
    out = _run(tmp_path, RAW)
    assert out.count("dark-factory/scripts/") == 2
    assert "(4 found)" in out


def test_fail_open_outside_git_repo(tmp_path):
    out = _run(tmp_path, RAW)
    assert out == RAW
