"""GraphQL cost log (#607): one JSONL line per measured call under the state dir.

Written only when SCHEDULER_STATE_DIR is EXPLICITLY set in the environment (no
default): CI's pytest job leaves it unset and separately asserts /var/lib/dark-factory
stays empty during pytest, so an unset variable means no write. Fails soft — a cost
log that raises would break the very call it measures, so every failure path is
swallowed. Rotation is size-based with a single retained generation (.1).

The `source` field distinguishes the writer: "scheduler" or a run id — set by
scheduler.sh / entrypoint.sh via DARK_FACTORY_COST_SOURCE.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

LOG_NAME = "graphql-costs.jsonl"
ROTATE_BYTES = 1_000_000  # ~1 MiB: rotate to a single retained generation


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
        if path.exists():
            try:
                if path.stat().st_size >= ROTATE_BYTES:
                    path.replace(str(path) + ".1")
            except OSError:
                pass
        line = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": os.environ.get("DARK_FACTORY_COST_SOURCE") or "unknown",
            "context": context,
            "cost": rate_limit.get("cost"),
            "remaining": rate_limit.get("remaining"),
            "reset_at": rate_limit.get("resetAt"),
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
    except Exception:
        # Fail soft by design: measurement must never break the call it measures.
        pass
