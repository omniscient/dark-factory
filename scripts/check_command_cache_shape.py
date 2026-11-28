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


def _load_baseline(baseline: Path) -> tuple[dict[str, int], list[str]]:
    try:
        data = json.loads(baseline.read_text(encoding="utf-8"))
    except OSError as exc:
        return {}, [f"{baseline}: cannot read baseline: {exc}"]
    except json.JSONDecodeError as exc:
        return {}, [f"{baseline}: invalid JSON: {exc}"]
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
        for k, v in data.items()
    ):
        return {}, [f"{baseline}: must be a JSON object mapping command name to line number"]
    return data, []


def check(
    commands_dir: Union[str, Path], baseline_path: Optional[Union[str, Path]] = None
) -> list[str]:
    """Apply R1-R3 to every ``*.md`` in *commands_dir*.

    Returns a (possibly empty) list of human-readable error strings.
    Returns ``[]`` when every file passes.
    """
    commands_dir = Path(commands_dir)
    baseline = Path(baseline_path) if baseline_path is not None else DEFAULT_BASELINE
    if not commands_dir.is_dir():
        return [f"{commands_dir}: not a directory"]
    floors, errors = _load_baseline(baseline)
    if errors:
        return errors

    files = sorted(commands_dir.glob("*.md"))
    for path in files:
        text = path.read_text(encoding="utf-8")
        for m in BRACE_RE.finditer(text):
            errors.append(
                f"{path}:{_line_of(text, m.start())}: R1: brace form ${{{m.group('name')} "
                f"is forbidden - Archon substitutes only the bare ${m.group('name')}; the "
                "brace form reaches the agent unsubstituted and resolves to the wrong "
                "directory in its shell (see docs/prompt-cache-shape.md)"
            )
        first = shape(path)["first_volatile_line"]
        name = path.stem
        if name not in floors:
            errors.append(
                f"{path}: R3: no entry in {baseline} - add \"{name}\": {first} to "
                f"{baseline} in the same commit"
            )
        elif first < floors[name]:
            errors.append(
                f"{path}: R2: first volatile token is on line {first}, earlier than the "
                f"baseline line {floors[name]} in {baseline} - move the token back below "
                "the stable prefix (lowering the baseline needs its own reviewed ticket)"
            )

    names = {p.stem for p in files}
    for orphan in sorted(set(floors) - names):
        errors.append(
            f"{baseline}: R3: entry \"{orphan}\" has no {commands_dir}/{orphan}.md - "
            "remove the entry in the same commit"
        )
    return errors


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lint the prompt cache shape of Archon phase command files."
    )
    parser.add_argument("commands_dir")
    parser.add_argument("--baseline", default=None, help="baseline JSON (default: next to this script)")
    parser.add_argument("--json", action="store_true", help="print per-command prompt-shape metadata")
    args = parser.parse_args(argv)

    if args.json and Path(args.commands_dir).is_dir():
        metadata = [shape(p) for p in sorted(Path(args.commands_dir).glob("*.md"))]
        print(json.dumps(metadata, indent=2))

    errors = check(args.commands_dir, args.baseline)
    if errors:
        print("Command cache-shape lint failed:", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        return 1

    if not args.json:
        print(f"Command cache-shape lint passed for {args.commands_dir}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
