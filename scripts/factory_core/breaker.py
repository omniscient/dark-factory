import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import identity
from .providers import get_tracker

_DEFAULT_STATE = Path(
    os.environ.get("STATE_FILE", "/var/lib/dark-factory/scheduler-state.json")
)


def get_retry_count(key: str, state_file: Path = _DEFAULT_STATE) -> int:
    if not state_file.exists():
        return 0
    try:
        return int(json.loads(state_file.read_text()).get(key, 0))
    except (json.JSONDecodeError, ValueError, OSError):
        return 0


def increment_retry(key: str, state_file: Path = _DEFAULT_STATE) -> int:
    new = get_retry_count(key, state_file) + 1
    _write_key(key, new, state_file)
    return new


def set_retry_count(key: str, value: int, state_file: Path = _DEFAULT_STATE) -> None:
    """Write an explicit retry count, bypassing the +1 that increment_retry applies.
    Used only to back-fill the normal counter when a capped `<key>:delivery` shadow
    counter (#279) reaches its ceiling, so trip_to_blocked's "attempted N time(s)"
    report reflects the true dispatch count."""
    _write_key(key, value, state_file)


def add_loop_tokens(issue_num: int, phase: str, name: str, n: int,
                     state_file: Path = _DEFAULT_STATE) -> int:
    """Adds n to the per-loop cumulative token counter. No live caller today (R7) —
    becomes live only when a future loop-dispatcher passes a populated loop_entry
    and reports run_record totals through this helper."""
    key = _loop_state_key(_make_key(issue_num, phase), name, "tokens")
    new = get_retry_count(key, state_file) + n
    _write_key(key, new, state_file)
    return new


def reset_retry(key: str, state_file: Path = _DEFAULT_STATE) -> None:
    if not state_file.exists():
        return
    try:
        data = json.loads(state_file.read_text())
        data.pop(key, None)
        # Clear the stored failure signature alongside the retry counter so the
        # "two consecutive attempts" invariant in record_failure_signature() doesn't
        # survive a reset (success, Continue-dispatch, blocked-rescue, spec/plan
        # advance) — otherwise the first post-reset failure with a matching class
        # would trip the breaker one attempt early (#33 review).
        data.pop(f"{key}:sig", None)
        # Same reasoning for the capped delivery-failure shadow counter (#279): a
        # ticket resumed from Blocked must not inherit a banked count from a prior,
        # unrelated episode.
        data.pop(f"{key}:delivery", None)
        _atomic_write(state_file, data)
    except (json.JSONDecodeError, OSError):
        pass


def _write_key(key: str, value: int, state_file: Path) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(state_file.read_text()) if state_file.exists() else {}
        data[key] = value
        _atomic_write(state_file, data)
    except (json.JSONDecodeError, OSError):
        pass


def _atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.rename(path)


def _make_key(issue_num: int, phase: str) -> str:
    return str(issue_num) if phase == "implement" else f"{issue_num}:{phase}"


@dataclass
class StopVerdict:
    """Result of evaluate_stop_condition. `reason` is one of the closed cap-class
    enum {"max_retries", "max_iterations", "deadline", "max_tokens"} or None (not
    tripped) — never a value implying a *successful* stop; that verdict class lives
    on #197's verifier seam, never here (spec R3)."""
    stopped: bool
    reason: "str | None" = None
    detail: dict = field(default_factory=dict)


def _loop_state_key(key: str, name: str, suffix: str) -> str:
    return f"{key}:loop:{name}:{suffix}"


def evaluate_stop_condition(
    loop_entry: Optional[dict],
    issue_num: int,
    phase: str,
    ceiling: int,
    state_file: Path = _DEFAULT_STATE,
    now: Optional[int] = None,
    peek: bool = False,
) -> StopVerdict:
    """Cap-class-only stop evaluator (state-file I/O only — no subprocess, no
    network; the external-predicate class lives on #197's verifier.py seam, never
    here). `loop_entry=None` is the parity path every live scheduler.sh site uses
    today: identical to the inline get_retry_count/compare/increment_retry sequence
    it replaces, with one addition — a runs.jsonl audit row on trip (R8).
    `peek=True` evaluates without advancing any counter (used only by the
    conflict-resolve site, whose own increment is deferred to its dispatch branch —
    see Task 12's note); a trip is still recorded and audited under peek.
    """
    key = _make_key(issue_num, phase)
    count = get_retry_count(key, state_file)

    reason: Optional[str] = None
    detail: dict = {}
    if count >= ceiling:
        reason, detail = "max_retries", {"count": count, "ceiling": ceiling}

    if reason is None and loop_entry is not None:
        reason, detail = _evaluate_loop_caps(loop_entry, key, ceiling, state_file, now)

    if reason is not None:
        verdict = StopVerdict(True, reason, detail)
        _append_stop_audit_row(verdict, issue_num, phase, loop_entry)
        return verdict

    if not peek:
        increment_retry(key, state_file)
        if loop_entry is not None:
            _advance_loop_counters(loop_entry, key, state_file, now)
    return StopVerdict(False)


def _evaluate_loop_caps(loop_entry, key, ceiling, state_file, now):
    name = loop_entry["name"]
    scheduling = loop_entry.get("scheduling") or {}
    budget_caps = loop_entry.get("budget_caps") or {}
    max_iterations = scheduling.get("max_iterations")
    deadline_seconds = scheduling.get("deadline_seconds")
    max_tokens = budget_caps.get("max_tokens")

    if max_iterations is not None:
        cur_iter = get_retry_count(_loop_state_key(key, name, "iter"), state_file)
        effective = min(max_iterations, ceiling)
        if cur_iter >= effective:
            return "max_iterations", {
                "iter": cur_iter, "max_iterations": max_iterations,
                "effective_ceiling": effective,
            }

    if deadline_seconds is not None:
        deadline_start = get_retry_count(_loop_state_key(key, name, "deadline_start"), state_file)
        if deadline_start:
            now_ts = now if now is not None else int(time.time())
            elapsed = now_ts - deadline_start
            if elapsed >= deadline_seconds:
                return "deadline", {"elapsed": elapsed, "deadline_seconds": deadline_seconds}

    if max_tokens is not None:
        cur_tokens = get_retry_count(_loop_state_key(key, name, "tokens"), state_file)
        if cur_tokens >= max_tokens:
            return "max_tokens", {"tokens": cur_tokens, "max_tokens": max_tokens}

    return None, {}


def _advance_loop_counters(loop_entry, key, state_file, now):
    name = loop_entry["name"]
    iter_key = _loop_state_key(key, name, "iter")
    deadline_key = _loop_state_key(key, name, "deadline_start")
    new_iter = get_retry_count(iter_key, state_file) + 1
    _write_key(iter_key, new_iter, state_file)
    if get_retry_count(deadline_key, state_file) == 0:
        now_ts = now if now is not None else int(time.time())
        _write_key(deadline_key, now_ts, state_file)


def _append_stop_audit_row(verdict: StopVerdict, issue_num: int, phase: str,
                            loop_entry: Optional[dict]) -> None:
    pass  # wired for real in Task 7


def _read_state(state_file: Path) -> dict:
    if not state_file.exists():
        return {}
    try:
        return json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_signature_key(key: str, value: str, state_file: Path) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    data = _read_state(state_file)
    data[f"{key}:sig"] = value
    _atomic_write(state_file, data)


def record_failure_signature(
    issue_num: int,
    phase: str,
    state_file: Path = _DEFAULT_STATE,
    state_dir: Optional[Path] = None,
) -> tuple:
    """Reads and consumes the drop file the container wrote via error-signature-write,
    always updates the stored signature for this issue+phase (regardless of class, so a
    later substantive repeat still compares against the right prior value), and returns
    (stuck, signature). stuck is True only when both the newly-read and previously-stored
    signature carry the "substantive:" prefix and match exactly.

    Naming note for conformance review: the spec's Requirement 5 / Brainstorming Q&A refers
    to this stored value as "last_error_signature" (one new field on scheduler-state.json,
    not a new file). This implementation stores it as a "<issue_key>:sig" entry in the same
    flat dict scheduler-state.json already is (e.g. "42:sig", "42:plan:sig") rather than a
    literal field named last_error_signature — semantically identical (one new per-key entry
    on the existing single-writer state surface; see _make_key's existing "<issue>[:phase]"
    convention, which every other key in this file already follows), just named to match the
    file's existing flat-key-per-issue+phase shape instead of introducing a differently-shaped
    nested field. Not a deviation from Requirement 5.
    """
    if state_dir is None:
        state_dir = Path(os.environ.get("SCHEDULER_STATE_DIR", "/var/lib/dark-factory"))
    drop_file = Path(state_dir) / "error-signatures" / f"{issue_num}.{phase}.sig"
    if not drop_file.exists():
        return False, ""
    try:
        new_sig = json.loads(drop_file.read_text()).get("signature", "")
    except (json.JSONDecodeError, OSError):
        new_sig = ""
    try:
        drop_file.unlink()
    except OSError:
        pass
    if not new_sig:
        return False, ""

    key = _make_key(issue_num, phase)
    prev_sig = str(_read_state(state_file).get(f"{key}:sig", ""))
    _write_signature_key(key, new_sig, state_file)

    stuck = new_sig.startswith("substantive:") and prev_sig == new_sig
    return stuck, new_sig


def trip_to_blocked(
    issue_num: int,
    phase: str,
    reason: str,
    state_file: Path = _DEFAULT_STATE,
) -> None:
    from .board import set_board_status, STATUS_BLOCKED

    key = _make_key(issue_num, phase)
    attempts = get_retry_count(key, state_file)

    retry_cmds = {
        "refine": f"Refine issue #{issue_num}",
        "plan": f"Plan issue #{issue_num}",
        "resolve": f"Deconflict issue #{issue_num}",
    }
    retry_cmd = retry_cmds.get(phase, f"Fix issue #{issue_num}")

    set_board_status(issue_num, STATUS_BLOCKED)

    # #249: routed through get_tracker(), which always targets identity.SLUG (matching
    # GitHubTracker.add_label's identity.SLUG-only argv) — the trip comment below now
    # targets the same fixed repo so label and comment can never diverge.
    tracker = get_tracker()
    for label in ("needs-discussion", "factory-regression"):
        tracker.add_label(str(issue_num), label)

    body = (
        f"## Scheduler — Circuit-Breaker Tripped (`{phase}`)\n\n"
        f"The scheduler attempted **{phase}** **{attempts} time(s)** without success "
        f"and cannot recover automatically.\n\n"
        f"**Reason:** {reason}\n\n"
        "This ticket has been moved to **Blocked** and labelled `needs-discussion` "
        "to pause automation.\n\n"
        "**To resume:**\n"
        "1. Investigate the failure comments above and fix the root cause.\n"
        "2. Remove the `needs-discussion` label — the scheduler resumes on its next poll.\n\n"
        "```bash\n"
        f"# Or re-run manually:\n"
        f'docker compose --profile factory run --rm dark-factory "{retry_cmd}"\n'
        "```\n\n"
        f"---\n{identity.marker('scheduler')}"
    )
    subprocess.run(
        ["gh", "issue", "comment", str(issue_num),
         "--repo", identity.SLUG, "--body", body],
        capture_output=True,
    )

    reset_retry(key, state_file)
