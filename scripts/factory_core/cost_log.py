"""GraphQL cost log (#607): one JSONL line per measured call under the state dir.

Written only when SCHEDULER_STATE_DIR is EXPLICITLY set in the environment (no
default): CI's pytest job leaves it unset and separately asserts /var/lib/dark-factory
stays empty during pytest, so an unset variable means no write. Fails soft — a cost
log that raises would break the very call it measures, so every failure path is
swallowed. Rotation (#652) is size-based into numbered, gzip-compressed generations that
are never deleted or overwritten (factory_core/rotation.py), under the sidecar lock
graphql-costs.jsonl.lock, which is taken with a bounded wait.

The `source` field distinguishes the writer: "scheduler" or a run id — set by
scheduler.sh / entrypoint.sh via DARK_FACTORY_COST_SOURCE.
"""
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

from factory_core import rotation

LOG_NAME = "graphql-costs.jsonl"
ROTATE_BYTES = 1_000_000  # ~1 MiB: rotate to the next numbered generation (#652)
# log_call runs inside the scheduler's board fetch on every poll, and every run container
# mounts this directory. A lock held by any process may cost the call at most this long,
# never the poll (#652 W7).
LOCK_WAIT_SECONDS = 1.0


def _append_line(path: Path, text: str) -> None:
    """Append to the live file without ever waiting on it or writing through it (#652): the
    name is opened O_NOFOLLOW | O_NONBLOCK and must be a regular file. A FIFO planted there
    (open would block until a reader came), a link or a directory loses this line and
    raises OSError, which log_call swallows; the poll goes on."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
                 | os.O_CLOEXEC, 0o666)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path} is not a regular file")
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


def log_call(context: str, rate_limit: dict | None) -> None:
    """Append one line for a measured GraphQL call. `rate_limit` is the call's own
    `rateLimit { cost remaining resetAt }` payload (each measured query selects it,
    so the line records the call's point cost, not just a budget snapshot); None
    (a GraphQL error body) records nothing. Never raises."""
    state_dir = os.environ.get("SCHEDULER_STATE_DIR")
    if not state_dir:
        return
    if not isinstance(rate_limit, dict):
        return
    try:
        path = Path(state_dir) / LOG_NAME
        line = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": os.environ.get("DARK_FACTORY_COST_SOURCE") or "unknown",
            "context": context,
            "cost": rate_limit.get("cost"),
            "remaining": rate_limit.get("remaining"),
            "reset_at": rate_limit.get("resetAt"),
        }
        lock_file = rotation.lock_path(path)
        created = None
        # W7: with the lock, rotate (W1, W2) and append. Without it (held past the wait, or
        # not a regular file), append without rotating; the row is kept either way.
        with rotation.locked(lock_file, timeout=LOCK_WAIT_SECONDS) as held:
            if held:
                created = rotation.rotate_locked(path, ROTATE_BYTES)
            _append_line(path, json.dumps(line) + "\n")
        if created is not None:
            # W3: inline (a 1 MiB generation, synchronous callers), after the lock is released.
            rotation.compress_and_recover(
                path, created, threshold=ROTATE_BYTES,
                lock=lambda: rotation.locked(lock_file, timeout=LOCK_WAIT_SECONDS))
    except Exception:
        # Fail soft by design: measurement must never break the call it measures.
        pass
