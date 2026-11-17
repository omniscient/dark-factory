"""Pure-stdlib token estimation helpers for Dark Factory context budget telemetry."""
from __future__ import annotations

import hashlib
import json
import os

CHARS_PER_TOKEN = 4.0


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN)


def hash_file(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except (FileNotFoundError, OSError):
        return None


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


DELIVERED_SAVINGS_FILE = "delivered-savings.jsonl"


def record_delivered(artifacts_dir: str | None, kind: str, baseline_tokens: int,
                     delivered_tokens: int, phase: str | None = None) -> None:
    """Append one measurement of what an optimizer really put into a prompt (#511).

    `kind` is "memory" (memory_retrieve.py) or "diff" (diff_rank.py); baseline is the
    uncapped size, delivered what was emitted under the cap. One JSON line per
    optimizer run, so a later phase never overwrites an earlier one's measurement
    (memory-trace.json and diff-ranking.json are overwritten per phase). Reporting
    only and best-effort: never raises, never affects the optimizer's output.
    """
    if not artifacts_dir:
        return
    try:
        os.makedirs(artifacts_dir, exist_ok=True)
        rec = {"kind": kind, "phase": phase, "baseline_tokens": int(baseline_tokens),
               "delivered_tokens": int(delivered_tokens)}
        with open(os.path.join(artifacts_dir, DELIVERED_SAVINGS_FILE), "a",
                  encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass
