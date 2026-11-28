"""post_marker_comment.sh footer-kind argument (#404).

Runs the real script with a stub `python3` executable first on PATH: the stub logs its
argv, answers `marker <kind>` with a fixed footer, copies the `--body-file` content to a
capture file (the script deletes its temp file right after the call), and exits with a
configurable code. Skips on non-POSIX hosts, like tests/test_push_gate_dag_exec.py.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "post_marker_comment.sh"
_TAG = "<!-- df-test-marker -->"
_BODY = "line one\n`$HOME` and $(date) stay literal"

pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("bash") is None,
    reason="needs a POSIX host with bash",
)

# argv is logged one call per line, fields separated by \x1f, so paths and the marker
# tag (which contain spaces) split back cleanly.
_STUB = r'''#!/usr/bin/env bash
printf '%s\x1f' "$@" >> "$STUB_LOG"; printf '\n' >> "$STUB_LOG"
case "$2" in
  marker)
    [ "${STUB_MARKER_RC:-0}" -eq 0 ] || exit "$STUB_MARKER_RC"
    printf '*Stub footer %s*\n' "$3"
    exit 0 ;;
  tracker)
    _prev=""
    for _a in "$@"; do
      if [ "$_prev" = "--body-file" ]; then cat "$_a" > "$STUB_BODY"; fi
      _prev="$_a"
    done
    exit "${STUB_TRACKER_RC:-0}" ;;
esac
exit 97
'''


def _run(tmp_path, *args, marker_rc=0, tracker_rc=0):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "python3"
    stub.write_text(_STUB, encoding="utf-8")
    stub.chmod(0o755)
    log = tmp_path / "argv.log"
    captured = tmp_path / "body.md"
    env = dict(
        os.environ,
        PATH=f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
        STUB_LOG=str(log),
        STUB_BODY=str(captured),
        STUB_MARKER_RC=str(marker_rc),
        STUB_TRACKER_RC=str(tracker_rc),
    )
    proc = subprocess.run(["bash", str(_SCRIPT), *args], env=env,
                          capture_output=True, text=True)
    calls = [line.split("\x1f")[:-1]
             for line in (log.read_text(encoding="utf-8").splitlines() if log.exists() else [])]
    posted = captured.read_text(encoding="utf-8") if captured.exists() else None
    return proc, calls, posted


def _marker_calls(calls):
    return [c[1:] for c in calls if c[1:2] == ["marker"]]


def _tracker_call(calls):
    tracker = [c for c in calls if c[1:3] == ["tracker", "comment"]]
    assert len(tracker) == 1, calls
    return tracker[0]


def test_three_args_default_to_scheduler_footer(tmp_path):
    proc, calls, _ = _run(tmp_path, "7", _TAG, _BODY)
    assert proc.returncode == 0, proc.stderr
    assert _marker_calls(calls) == [["marker", "scheduler"]]
    assert calls[0][0].endswith("factory_core/cli.py")


def test_fourth_arg_selects_factory_footer(tmp_path):
    proc, calls, _ = _run(tmp_path, "7", _TAG, _BODY, "factory")
    assert proc.returncode == 0, proc.stderr
    assert _marker_calls(calls) == [["marker", "factory"]]


@pytest.mark.parametrize("kind", [None, "factory"])
def test_body_assembly_and_marker_passthrough(tmp_path, kind):
    args = ["7", _TAG, _BODY] + ([kind] if kind else [])
    proc, calls, posted = _run(tmp_path, *args)
    assert proc.returncode == 0, proc.stderr
    assert posted == f"{_TAG}\n{_BODY}\n\n---\n*Stub footer {kind or 'scheduler'}*"
    tracker = _tracker_call(calls)
    assert tracker[0].endswith("factory_core/providers/cli.py")
    assert tracker[tracker.index("--id") + 1] == "7"
    assert tracker[tracker.index("--marker") + 1] == _TAG


def test_failed_footer_resolution_still_posts_with_empty_footer(tmp_path):
    proc, calls, posted = _run(tmp_path, "7", _TAG, _BODY, "no-such-kind", marker_rc=2)
    assert _marker_calls(calls) == [["marker", "no-such-kind"]]
    assert posted == f"{_TAG}\n{_BODY}\n\n---\n"
    assert proc.returncode == 0, proc.stderr  # the tracker CLI's code, not the marker's


def test_tracker_exit_code_propagates(tmp_path):
    proc, _, posted = _run(tmp_path, "7", _TAG, _BODY, "factory", tracker_rc=3)
    assert posted is not None
    assert proc.returncode == 3


def test_real_marker_subcommand_validates_kind():
    cli = _REPO_ROOT / "scripts" / "factory_core" / "cli.py"
    ok = subprocess.run([sys.executable, str(cli), "marker", "factory"],
                        capture_output=True, text=True, cwd=str(_REPO_ROOT))
    assert ok.returncode == 0, ok.stderr
    assert ok.stdout.strip().endswith("Dark Factory*")
    bad = subprocess.run([sys.executable, str(cli), "marker", "no-such-kind"],
                         capture_output=True, text=True, cwd=str(_REPO_ROOT))
    assert bad.returncode != 0
