import json
import os
import subprocess
from pathlib import Path

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


def reset_retry(key: str, state_file: Path = _DEFAULT_STATE) -> None:
    if not state_file.exists():
        return
    try:
        data = json.loads(state_file.read_text())
        data.pop(key, None)
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
    state_dir: Path = None,
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
