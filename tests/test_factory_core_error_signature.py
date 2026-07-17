import json
import subprocess
import sys
import sys as _sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core.error_signature import classify, write_signature


def _classify(text="", exit_code=1, elapsed=999, commits=1, dirty=False, artifact=True, **kw):
    return classify(
        text, exit_code,
        elapsed_seconds=elapsed,
        commits_since_start=commits,
        worktree_dirty=dirty,
        artifact_present=artifact,
        **kw,
    )


def test_delivery_failure_conjunction():
    sig = _classify(text="anything", elapsed=5, commits=0, dirty=False, artifact=False)
    assert sig == "environmental:delivery_failure"


def test_delivery_failure_requires_all_four_conjuncts():
    # elapsed under threshold but a commit landed — not delivery_failure
    assert _classify(text="", elapsed=5, commits=1, dirty=False, artifact=False) != \
        "environmental:delivery_failure"
    # elapsed under threshold, zero commits, clean tree, but an artifact exists
    assert _classify(text="", elapsed=5, commits=0, dirty=False, artifact=True) != \
        "environmental:delivery_failure"
    # elapsed at/over threshold
    assert _classify(text="", elapsed=30, commits=0, dirty=False, artifact=False) != \
        "environmental:delivery_failure"


def test_delivery_failure_max_seconds_is_tunable():
    sig = _classify(text="", elapsed=45, commits=0, dirty=False, artifact=False,
                     delivery_failure_max_seconds=60)
    assert sig == "environmental:delivery_failure"


def test_preview_infra_checked_before_build_failure():
    # Contains both a toolchain string and build-ish language; must classify preview_infra.
    sig = _classify(text="failed to solve: process /bin/sh -c npm ERR! build failed")
    assert sig == "environmental:preview_infra"


def test_rate_limit():
    sig = _classify(text="Error: you have hit your usage limit for this session", exit_code=1)
    assert sig == "environmental:rate_limit"


def test_oos_files():
    sig = _classify(text="OOS gate: excising out-of-scope files: foo.py", exit_code=1)
    assert sig == "substantive:oos_files:1"


def test_build_failure():
    sig = _classify(text="npm ERR! code ELIFECYCLE\nnpm ERR! Exit status 1", exit_code=1)
    assert sig == "substantive:build_failure:1"


def test_test_failure():
    sig = _classify(text="FAILED tests/test_foo.py::test_bar - AssertionError", exit_code=1)
    assert sig == "substantive:test_failure:1"


def test_unknown_fallback():
    sig = _classify(text="some completely unrelated stack trace", exit_code=3)
    assert sig == "substantive:unknown:3"


def test_environmental_signatures_have_no_exit_code_suffix():
    assert ":" not in _classify(text="", elapsed=5, commits=0, dirty=False,
                                 artifact=False).split("environmental:")[1]
    assert _classify(text="rate limit exceeded", elapsed=120) == "environmental:rate_limit"


def test_write_signature_atomic(tmp_path):
    write_signature(42, "implement", "substantive:test_failure:1", 1, tmp_path)
    sig_file = tmp_path / "error-signatures" / "42.implement.sig"
    assert sig_file.exists()
    data = json.loads(sig_file.read_text())
    assert data == {"signature": "substantive:test_failure:1", "phase": "implement", "exit_code": 1}


def test_write_signature_creates_parent_dirs(tmp_path):
    target = tmp_path / "nested" / "state"
    write_signature(7, "plan", "environmental:rate_limit", 1, target)
    assert (target / "error-signatures" / "7.plan.sig").exists()


CLI = str(Path(__file__).resolve().parents[1] / "scripts" / "factory_core" / "cli.py")


def test_cli_error_signature_write_end_to_end(tmp_path):
    text_file = tmp_path / "out.txt"
    text_file.write_text("FAILED tests/test_foo.py::test_bar - AssertionError")
    result = subprocess.run(
        [_sys.executable, CLI, "error-signature-write",
         "--issue", "9", "--phase", "implement", "--exit-code", "1",
         "--text-file", str(text_file),
         "--elapsed-seconds", "120", "--commits-since-start", "0",
         "--state-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "signature=substantive:test_failure:1" in result.stdout
    sig_file = tmp_path / "error-signatures" / "9.implement.sig"
    assert json.loads(sig_file.read_text())["signature"] == "substantive:test_failure:1"


def test_cli_error_signature_write_missing_text_file_is_empty_text(tmp_path):
    result = subprocess.run(
        [_sys.executable, CLI, "error-signature-write",
         "--issue", "9", "--phase", "plan", "--exit-code", "1",
         "--text-file", str(tmp_path / "nonexistent.txt"),
         "--elapsed-seconds", "5", "--commits-since-start", "0",
         "--state-dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "signature=environmental:delivery_failure" in result.stdout
