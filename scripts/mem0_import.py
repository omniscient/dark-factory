#!/usr/bin/env python3
"""
mem0_import.py — Import .archon/memory/*.md into a local Mem0 instance (spike #50).

Disposable spike script: not wired into CI or any factory phase command. Every write uses
infer=False (no LLM call) — this is a literal import of the existing corpus text, not an
LLM-inferred re-summarization, matching the "no hidden LLM calls" non-goal.

Usage:
    python scripts/mem0_import.py --memory-dir .archon/memory --store-path /tmp/mem0-spike-store \\
        --report /tmp/mem0-import-report.json
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mem0_spike_config import build_memory, USER_ID  # noqa: E402

_ENTRY_RE = re.compile(
    r"^- \[(?P<tag>[^\]]+)\]\s+(?P<body>.+?)(?:\s*<!--(?P<meta>[^>]*)-->)?\s*$"
)
_TAG_RE = re.compile(r"(\w+\d*):([^\s>]+)")
_MEMORY_FILES = [
    "codebase-patterns.md",
    "architecture.md",
    "backend-patterns.md",
    "frontend-patterns.md",
    "dark-factory-ops.md",
]


def parse_meta(meta_str):
    if not meta_str:
        return {}
    return dict(_TAG_RE.findall(meta_str))


def load_entries(memory_dir: Path):
    """Parse authoritative (PATTERN/AVOID/FIX/INVALID-excluded) entries from all 5 memory files.

    Stops scanning each file at the first '---' separator, matching memory_retrieve.py's and
    eval_memory_quality.py's own convention.
    """
    entries = []
    for fname in _MEMORY_FILES:
        fpath = memory_dir / fname
        if not fpath.exists():
            continue
        for line in fpath.read_text(encoding="utf-8").splitlines():
            if line.strip() == "---":
                break
            m = _ENTRY_RE.match(line)
            if not m:
                continue
            tag = m.group("tag").upper()
            if tag == "PROVISIONAL" or tag.startswith("INVALID"):
                continue
            meta = parse_meta(m.group("meta") or "")
            entries.append({
                "source_file": fname,
                "kind": tag,
                "body": m.group("body").strip(),
                "issue": meta.get("issue", ""),
                "source": meta.get("source", ""),
                "path": meta.get("path", ""),
                "expires": meta.get("expires", ""),
            })
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-dir", default=".archon/memory")
    parser.add_argument("--store-path", required=True, help="Local Qdrant embedded store directory")
    parser.add_argument("--report", default=None, help="Write a JSON import report to this path")
    args = parser.parse_args()

    memory_dir = Path(args.memory_dir)
    entries = load_entries(memory_dir)
    if not entries:
        print("mem0-import: error: no authoritative entries found", file=sys.stderr)
        sys.exit(2)

    m = build_memory(args.store_path)

    imported, failed = [], []
    for e in entries:
        try:
            res = m.add(
                e["body"],
                user_id=USER_ID,
                infer=False,
                metadata={
                    "kind": e["kind"],
                    "source_file": e["source_file"],
                    "issue": e["issue"],
                    "source": e["source"],
                    # agent_id/path_prefix are named to match the exact filter-key shape spec
                    # Requirement 2 / Architecture §2 row 2 requires testing (memory_retrieve.py's
                    # PHASE_SOURCE_MAP + path-tag filtering uses these same names). agent_id has no
                    # separate value in the flat-file corpus, so it mirrors source (documented
                    # spike simplification, not a claim the two are semantically identical).
                    "agent_id": e["source"],
                    "path_prefix": e["path"],
                    "expires": e["expires"],
                },
            )
            mem_id = (res.get("results") or [{}])[0].get("id", "")
            imported.append({"id": mem_id, "source_file": e["source_file"], "body": e["body"][:80]})
        except Exception as exc:  # spike: record and continue, don't abort the whole import
            failed.append({"source_file": e["source_file"], "error": str(exc)})

    print(f"mem0-import: {len(imported)} imported, {len(failed)} failed, {len(entries)} total")
    for f in failed:
        print(f"  FAIL: {f['source_file']}: {f['error']}", file=sys.stderr)

    if args.report:
        Path(args.report).write_text(json.dumps({"imported": imported, "failed": failed}, indent=2))

    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
