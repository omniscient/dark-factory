"""Parser fixtures for scheduler.sh::parse_comment_verdict (#402).

Drives the bash function through a subprocess exactly as tests/test_scheduler.sh loads
the scheduler, minus the stubs parse_comment_verdict itself never needs (it makes no
external calls) — except two hazards that fire unconditionally at *source* time, before
the SCHEDULER_SOURCE_ONLY guard (scheduler.sh:1331):
- `python3 "$FACTORY_PROVIDERS_CLI" preflight` (scheduler.sh:103) — short-circuited by a
  `python3` shell-function override, same technique tests/test_scheduler.sh already uses.
- `mkdir -p "$SCHEDULER_STATE_DIR"` + a `$STATE_FILE` write (scheduler.sh:120-124),
  defaulting to /var/lib/dark-factory — redirected by overriding SCHEDULER_STATE_DIR to
  a pytest tmp_path (scheduler.sh:11 derives STATE_FILE from it unconditionally, so
  setting STATE_FILE directly would be redundant/inert — SCHEDULER_STATE_DIR is what
  actually matters); tests/test_scheduler.sh:65-72 guards the same hazard.
A third source-time block (scheduler.sh:111-117, copying /workspace/project/.archon/.env
if present) is a no-op here: that path doesn't exist in a bare checkout or in CI, only
inside a live factory run container, where it already exists and is a no-op copy.
"""
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEDULER = REPO_ROOT / "scheduler.sh"

_HARNESS = '''
set -uo pipefail
python3() {
  case "$*" in
    *providers/cli.py*) return 0 ;;
    *) command python3 "$@" ;;
  esac
}
export -f python3
SCHEDULER_SOURCE_ONLY=1 source "$1"
parse_comment_verdict "$2"
'''


def _parse(reply: str, state_dir: Path) -> str:
    env = dict(os.environ)
    env["SCHEDULER_STATE_DIR"] = str(state_dir)
    env["STATE_FILE"] = str(state_dir / "scheduler-state.json")
    result = subprocess.run(
        ["bash", "-c", _HARNESS, "_", str(SCHEDULER), reply],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env,
    )
    return result.stdout.strip()


CASES = [
    ("SKIP", "SKIP"),
    ("skip", "SKIP"),
    ("**SKIP**", "SKIP"),
    ("`SKIP`", "SKIP"),
    ("- SKIP", "SKIP"),
    ("> SKIP", "SKIP"),
    ("SKIP.", "SKIP"),
    ("SKIP:", "SKIP"),
    ("SKIP Both comments are from automated systems", "SKIP"),
    ("CONTINUE — the reviewer wants a merge later", "CONTINUE"),
    ("MERGE", "MERGE"),
    ("**MERGE**", "MERGE"),
    ("merge.", "MERGE"),
    ("MERGE — approved", ""),
    ("MERGE is not appropriate", ""),
    ("SKIP — but MERGE would be reasonable", ""),
    ("MERGE? No — SKIP", ""),
    ("Merge-ready, ship it", ""),
    ("MERGED already", ""),
    ("Skipping this", ""),
    ("skip_this", ""),
    ("Verdict: SKIP", ""),
    # Operator plan gate (F-1): a bare MERGE on line 1 must not survive a lowercase
    # contradiction further down — the ambiguity scan is case-sensitive, so
    # whole-response MERGE-strict is the only thing standing between this reply and
    # an unguarded `Close issue #N` dispatch.
    ("MERGE\nActually, skip this — the tests fail", ""),
    ("MERGE\nAlso CONTINUE maybe", ""),
    ("MERGE\n", "MERGE"),
    ("", ""),
]


@pytest.mark.parametrize("reply,expected", CASES)
def test_parse_comment_verdict(reply, expected, tmp_path):
    assert _parse(reply, tmp_path) == expected
