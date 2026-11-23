"""Fixtures for scheduler.sh::is_issue_running's anchored match (#579).

PR #578's review fixup (d8d074f) changed the shared duplicate-dispatch guard from a
substring `grep -q "#N"` to `grep -qE "#N([^0-9]|$)"`, so a running `Close issue #711`
no longer makes #71 look running. Every other scheduler test stubs is_issue_running out;
this file sources the real function and replaces only `docker`.

Source-time hazards are handled as in tests/test_scheduler_comment_verdict.py: a
`python3` override short-circuits the providers preflight, and SCHEDULER_STATE_DIR /
STATE_FILE point at pytest's tmp_path. scheduler.sh's own `set -e` stays active after
`source`, so the harness calls the function inside `if` and reports an explicit RESULT=
token; the test asserts on that token and on a zero exit, so a harness that died cannot
pass as "not running".

The `docker` stub answers only the exact `ps --no-trunc --format '{{.Command}}'` call
the guard makes (anything else prints nothing and fails): `--no-trunc` is load-bearing,
since a truncated Command column can drop the issue number, so a changed invocation
must fail the positive cases rather than pass silently.
"""
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEDULER = REPO_ROOT / "scheduler.sh"

_HARNESS = r'''
set -uo pipefail
python3() {
  case "$*" in
    *providers/cli.py*) return 0 ;;
    *) command python3 "$@" ;;
  esac
}
export -f python3
SCHEDULER_SOURCE_ONLY=1 source "$1"
docker() {
  if [ "$#" -eq 4 ] && [ "$1" = "ps" ] && [ "$2" = "--no-trunc" ] \
     && [ "$3" = "--format" ] && [ "$4" = "{{.Command}}" ]; then
    if [ -n "${DOCKER_PS_OUTPUT:-}" ]; then printf '%s\n' "$DOCKER_PS_OUTPUT"; fi
    return 0
  fi
  return 1
}
export -f docker
declare -F is_issue_running >/dev/null || { echo HARNESS_BROKEN; exit 97; }
if is_issue_running "$2"; then echo RESULT=running; else echo RESULT=not-running; fi
'''

CLOSE_711 = "entrypoint.sh 'Close issue #711'"
CLOSE_71 = "entrypoint.sh 'Close issue #71'"
CLOSE_7111 = "entrypoint.sh 'Close issue #7111'"
EOL_711 = "entrypoint.sh Close issue #711"  # issue number is the last thing on the line

CASES = [
    # The case from the issue: #711 running must not make #71 look running.
    (CLOSE_711, "711", "running"),
    (CLOSE_711, "71", "not-running"),
    # Numeric near-misses in both directions.
    (CLOSE_711, "7111", "not-running"),
    (CLOSE_7111, "711", "not-running"),
    (CLOSE_71, "711", "not-running"),
    # Two containers: each issue is found on its own line.
    (CLOSE_71 + "\n" + CLOSE_711, "71", "running"),
    (CLOSE_71 + "\n" + CLOSE_711, "711", "running"),
    # The `$` alternative of the anchor: nothing after the number.
    (EOL_711, "711", "running"),
    (EOL_711, "71", "not-running"),
    # No running containers at all.
    ("", "71", "not-running"),
    ("", "711", "not-running"),
]


def _run(docker_ps_output: str, issue_num: str, state_dir: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["SCHEDULER_STATE_DIR"] = str(state_dir)
    env["STATE_FILE"] = str(state_dir / "scheduler-state.json")
    env["DOCKER_PS_OUTPUT"] = docker_ps_output
    return subprocess.run(
        ["bash", "-c", _HARNESS, "_", str(SCHEDULER), issue_num],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env,
    )


@pytest.mark.parametrize("docker_ps_output,issue_num,expected_result", CASES)
def test_is_issue_running(docker_ps_output, issue_num, expected_result, tmp_path):
    result = _run(docker_ps_output, issue_num, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    tokens = [line for line in result.stdout.splitlines() if line.startswith("RESULT=")]
    assert tokens == [f"RESULT={expected_result}"], result.stdout + result.stderr
