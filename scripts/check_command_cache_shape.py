"""Lint the prompt cache shape of Archon phase command files (#236).

Archon reads ``commands/<name>.md`` verbatim and substitutes a fixed vocabulary of bare
``$NAME`` tokens (and ``$<node-id>.output`` references) across the whole text before
sending it as a command node's first user message. The substituted values are per-run,
so a rendered command differs between runs from its first such token onward. Provider
prompt caching is prefix-based, so only the position of that first token matters.

Contract (see docs/prompt-cache-shape.md):
  - stable_prefix: the bytes before the first bare token or node-output reference
  - volatile_tail: everything from that token to the end of the file
  - cache_busting_fields: the bare names in the file, with count and first line

Rules, applied to every ``commands/*.md``:
  - R1: no brace form ``${NAME`` of a vocabulary name. Archon never substitutes it, and
    in a command node's shell it resolves to the entrypoint's ARTIFACTS_DIR, not the
    workflow run's directory, so the phase would read and write the wrong place.
  - R2: ``first_volatile_line`` must be >= the file's committed baseline (a ratchet).
  - R3: the baseline has exactly one entry per command file.

Usage (CI)::

    python scripts/check_command_cache_shape.py commands
    python scripts/check_command_cache_shape.py --json commands   # prompt-shape metadata

API::

    from check_command_cache_shape import check, shape
    errors = check(commands_dir)   # returns [] on success
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Optional, Union

# Mirrors substituteWorkflowVariables() (packages/workflows/src/executor-shared.ts) and
# substituteNodeOutputRefs() (packages/workflows/src/dag-executor.ts) at the Archon
# commit the Dockerfile pins (74372446). Re-check these constants if the pin moves.
PLAIN_NAMES = (
    "WORKFLOW_ID",
    "ARTIFACTS_DIR",
    "BASE_BRANCH",
    "DOCS_DIR",
    "USER_MESSAGE",
    "ARGUMENTS",
    "LOOP_USER_INPUT",
    "REJECTION_REASON",
    "LOOP_PREV_OUTPUT",
)
CONTEXT_NAMES = ("CONTEXT", "EXTERNAL_CONTEXT", "ISSUE_CONTEXT")
VOCABULARY = PLAIN_NAMES + CONTEXT_NAMES
NODE_OUTPUT = "NODE_OUTPUT"

# Plain names carry no word-boundary guard in Archon ($ARTIFACTS_DIRX is substituted);
# the context names carry a negative lookahead ($CONTEXTS is not).
TOKEN_RE = re.compile(
    r"\$(?:"
    r"(?P<plain>" + "|".join(PLAIN_NAMES) + r")"
    r"|(?P<context>" + "|".join(CONTEXT_NAMES) + r")(?![A-Za-z0-9_])"
    r"|(?P<node>[a-zA-Z_][a-zA-Z0-9_-]*\.output)"
    r")"
)
# The identifier must end at the name, so ${CONTEXT_PACK} or ${OTHER} are not flagged.
BRACE_RE = re.compile(r"\$\{(?P<name>" + "|".join(VOCABULARY) + r")(?![A-Za-z0-9_])")

DEFAULT_BASELINE = Path(__file__).resolve().parent / "command_cache_shape_baseline.json"


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def shape(path: Union[str, Path]) -> dict:
    """Return the static prompt-shape metadata for one command file."""
    path = Path(path)
    data = path.read_bytes()
    text = data.decode("utf-8")
    matches = list(TOKEN_RE.finditer(text))
    if matches:
        start = matches[0].start()
        first_volatile_line = _line_of(text, start)
    else:
        start = len(text)
        first_volatile_line = len(text.splitlines()) + 1
    prefix = text[:start].encode("utf-8")
    tail_bytes = len(data) - len(prefix)

    fields: dict[str, dict] = {}
    for m in matches:
        name = m.group("plain") or m.group("context") or NODE_OUTPUT
        entry = fields.setdefault(
            name, {"name": name, "count": 0, "first_line": _line_of(text, m.start())}
        )
        entry["count"] += 1

    return {
        "command": path.stem,
        "prefix_hash": hashlib.sha256(prefix).hexdigest(),
        "stable_prefix_bytes": len(prefix),
        "volatile_tail_bytes": tail_bytes,
        "volatile_tail_tokens_est": tail_bytes // 4,  # estimate: bytes / 4
        "first_volatile_line": first_volatile_line,
        "cache_busting_fields": list(fields.values()),
    }
