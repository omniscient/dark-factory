"""Repeated-failure-signature classification (#33): categorize a failed run's captured
output into a stable class-prefixed enum the scheduler compares across consecutive
attempts to decide whether to trip the circuit breaker early. Mirrors session_window.py's
_SUBSTRING_RE keyword-match style and its write_pause_sentinel drop-file pattern.
"""
import json
import re
from pathlib import Path

from .session_window import RATE_LIMIT_RE as _RATE_LIMIT_RE

_PREVIEW_INFRA_RE = re.compile(
    r"buildkit|failed to solve|docker[- ]compose|pull access denied|manifest unknown|"
    r"no space left on device|port is already allocated|network .* not found|"
    r"preview stack|failed to build preview",
    re.IGNORECASE,
)
# Matches the two stderr lines scripts/verdict_gate_check.sh prints when a gate phase
# never wrote its verdict (#373): the "true silent death" line (verdict missing/unparseable,
# no needs-discussion label) and the quiet-branch line when it reports STATUS=missing
# (needs-discussion present). A quiet-branch STATUS=BLOCKED line is a real verdict and
# must not match. Anchored on that script's own "verdict_gate_check.sh: " log prefix and a
# literal issue number, so a transcript that merely quotes the phrase (e.g. `cat` of the
# script, whose source has "#${ISSUE_NUM}") is not mis-signed. Wording is coupled to both
# echoes; tests/test_factory_core_error_signature.py fails CI if either side drifts.
# Deliberately no re.IGNORECASE: it is our own literal.
_VERDICT_MISSING_RE = re.compile(
    r"verdict_gate_check\.sh: .{0,80}"
    r"(?:verdict missing/unparseable for issue #\d+|blocks issue #\d+ \(STATUS=missing\))"
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

# Written directly by entrypoint.sh's _handle_session_window_pause() via a dedicated CLI
# subcommand, bypassing classify() entirely — the pause classification already happened in
# session_window.py before that function returns 0. Included here (not just as a bash literal)
# so scheduler.sh's hardcoded comparison and this module's writer share one canonical source
# a reader can find by grepping the module, mirroring how "environmental:delivery_failure" is
# both classify()'s return value and retry_or_skip_delivery_failure()'s bash literal.
SESSION_WINDOW_PAUSE_SIGNATURE = "environmental:session_window_pause"
# Precedence note (#373): _VERDICT_MISSING_RE's place in classify() cannot race this path.
# entrypoint.sh's _handle_session_window_pause() runs before classify() is ever invoked
# and, on a genuine match, writes this signature directly and returns — a real
# session-window pause never reaches classify()'s regex chain. There is no ordering
# conflict between the two paths to "fix".


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
    if _RATE_LIMIT_RE.search(text):
        return "environmental:rate_limit"
    # Above _PREVIEW_INFRA_RE: the degraded-preview path every self-target run takes leaves
    # non-fatal toolchain output (docker compose, buildkit) early in the transcript, which
    # used to shadow the run-ending missing verdict (#197). Below _RATE_LIMIT_RE: a
    # rate-limit death that ends in a missing verdict stays environmental and never counts
    # toward the breaker's two-consecutive-substantive trip (operator decision, #373).
    if _VERDICT_MISSING_RE.search(text):
        return "substantive:verdict_missing"
    if _PREVIEW_INFRA_RE.search(text):
        return "environmental:preview_infra"
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
