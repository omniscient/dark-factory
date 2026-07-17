#!/usr/bin/env python3
"""
mem0_retrieve_adapter.py — Mem0-backed drop-in for eval_memory_quality.py's --retrieve-script
contract (spike #50, retrieval-quality "Mem0 raw top-k" arm).

eval_memory_quality.py always invokes its --retrieve-script with exactly:
    --phase <phase> --files <path_tag_or_empty> --memory-dir <dir>
(see scripts/eval_memory_quality.py run_eval()). --memory-dir is accepted for CLI compatibility
but unused here on purpose: the corpus lives in the Mem0 store built by mem0_import.py (located
via MEM0_SPIKE_STORE_PATH), and this arm deliberately does NOT apply the factory's own
PHASE_SOURCE_MAP/path-tag post-filtering — it exercises Mem0's own top-k relevance ranking
untouched, mirroring the "expose the full bank automatically every turn" shape the PM's
Proactive-Memory-Agent comment (issue #50) warns against. The factory's *own* scoped+capped
retrieval is the comparison baseline, run separately via memory_retrieve.py (unchanged).

Usage:
    MEM0_SPIKE_STORE_PATH=/tmp/mem0-spike-store \\
        python scripts/mem0_retrieve_adapter.py --phase implement --files "backend/app/x.py" \\
            --memory-dir .archon/memory
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mem0_spike_config import build_memory, USER_ID  # noqa: E402

TOP_K = 8


def build_query(phase, files):
    """Synthesize a free-text query from (phase, files) — eval_memory_quality.py's harness has
    no natural-language query concept, only phase+path, so this is the simplest faithful bridge."""
    files = files.strip()
    return f"{phase} lessons for {files}" if files else f"{phase} lessons"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--files", default="")
    parser.add_argument("--memory-dir", default=".archon/memory")  # accepted, unused (see docstring)
    args = parser.parse_args()

    store_path = os.environ.get("MEM0_SPIKE_STORE_PATH")
    if not store_path:
        print("mem0-retrieve-adapter: error: MEM0_SPIKE_STORE_PATH not set", file=sys.stderr)
        sys.exit(1)

    query = build_query(args.phase, args.files)

    m = build_memory(store_path)
    # mem0ai (as of the pinned 2.0.12 install, confirmed via live run issue #50) rejects
    # top-level entity kwargs on search() — user_id must go inside filters=.
    raw = m.search(query, top_k=TOP_K, filters={"user_id": USER_ID})
    hits = raw.get("results", raw) if isinstance(raw, dict) else raw

    for r in hits:
        body = r.get("memory") or r.get("text") or ""
        if not body:
            continue
        kind = (r.get("metadata") or {}).get("kind", "PATTERN")
        print(f"- [{kind}] {body}")


if __name__ == "__main__":
    main()
