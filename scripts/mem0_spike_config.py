#!/usr/bin/env python3
"""
mem0_spike_config.py — Shared local-only Mem0 configuration for the #50 spike.

Zero network egress after first-run model download: vector store is Qdrant in embedded
(on-disk, no server process) mode; embedder is a local sentence-transformers model pulled
once from the Hugging Face Hub, then cached under HF_HOME — a one-time install-time cost,
not a per-query network call (tracked separately in the benchmark table's row 5).

No real LLM call is ever made by this spike: every write goes through mem0_import.py with
infer=False, which (per Mem0's public docs) skips the LLM-based fact-extraction path entirely.
The "llm" block below is present only because Mem0's config schema requires one to be
declared; api_key is a syntactically-shaped placeholder that is never sent over the network
on the infer=False path — eval_mem0.sh's Task-3 live run is what confirms this assumption
holds for the actually-installed mem0ai version (spec Assumptions: "based on Mem0's public
documentation... the implement phase's live run is what actually confirms or refutes this").
"""
import os

USER_ID = "dark-factory-spike"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Set before the first `import mem0` so the opt-out takes effect at import time.
os.environ.setdefault("MEM0_TELEMETRY", "False")


def build_memory(store_path: str):
    """Construct a local-only Mem0 Memory instance rooted at store_path."""
    from mem0 import Memory  # deferred: keeps this module importable without mem0ai installed

    config = {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "path": store_path,
                "collection_name": "dark_factory_spike",
            },
        },
        "embedder": {
            "provider": "huggingface",
            "config": {
                "model": EMBED_MODEL,
            },
        },
        "llm": {
            "provider": "openai",
            "config": {
                "api_key": "sk-mem0-spike-unused-infer-false-only",
                "model": "gpt-4o-mini",
            },
        },
        "version": "v1.1",
    }
    return Memory.from_config(config)
