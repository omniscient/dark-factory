"""Repeated-failure-signature classification (#33): categorize a failed run's captured
output into a stable class-prefixed enum the scheduler compares across consecutive
attempts to decide whether to trip the circuit breaker early. Mirrors session_window.py's
_SUBSTRING_RE keyword-match style and its write_pause_sentinel drop-file pattern.
"""
import json
import re
from pathlib import Path

from .session_window import _SUBSTRING_RE as _RATE_LIMIT_RE

_PREVIEW_INFRA_RE = re.compile(
    r"buildkit|failed to solve|docker[- ]compose|pull access denied|manifest unknown|"
    r"no space left on device|port is already allocated|network .* not found|"
    r"preview stack|failed to build preview",
    re.IGNORECASE,
)
_OOS_FILES_RE = re.compile(r"OOS gate|out-of-scope", re.IGNORECASE)
_BUILD_FAILURE_RE = re.compile(
    r"npm err!|build failed|compilation error|modulenotfounderror|importerror|syntaxerror",
    re.IGNORECASE,
)
_TEST_FAILURE_RE = re.compile(
    r"assertionerror|failed \(errors=|failed \(failures=|pytest.*failed|tsc.*error ts\d+",
    re.IGNORECASE,
)


def classify(
    text: str,
    exit_code: int,
    *,
    elapsed_seconds: int,
    commits_since_start: int,
    worktree_dirty: bool,
    artifact_present: bool,
    delivery_failure_max_seconds: int = 30,
) -> str:
    if (
        elapsed_seconds < delivery_failure_max_seconds
        and commits_since_start == 0
        and not worktree_dirty
        and not artifact_present
    ):
        return "environmental:delivery_failure"
    if _PREVIEW_INFRA_RE.search(text):
        return "environmental:preview_infra"
    if _RATE_LIMIT_RE.search(text):
        return "environmental:rate_limit"
    if _OOS_FILES_RE.search(text):
        return f"substantive:oos_files:{exit_code}"
    if _BUILD_FAILURE_RE.search(text):
        return f"substantive:build_failure:{exit_code}"
    if _TEST_FAILURE_RE.search(text):
        return f"substantive:test_failure:{exit_code}"
    return f"substantive:unknown:{exit_code}"


def write_signature(issue_num: int, phase: str, signature: str, exit_code: int, state_dir) -> None:
    state_dir = Path(state_dir)
    sig_dir = state_dir / "error-signatures"
    sig_dir.mkdir(parents=True, exist_ok=True)
    path = sig_dir / f"{issue_num}.{phase}.sig"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"signature": signature, "phase": phase, "exit_code": exit_code}))
    tmp.rename(path)
