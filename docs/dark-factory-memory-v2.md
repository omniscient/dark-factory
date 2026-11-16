# Dark Factory Memory v2 — Rollout & Fallback Operator Guide

**Status:** shipped  
**Date:** 2026-06-30 (paths, commands and status re-checked against the code 2026-11-16)  
**Issue:** #655 | **Epic:** #643  
**Author:** MarketHawk Refinement Pipeline

---

## §1 — Overview

Dark Factory memory v2 is the flat-file accumulation system that lets pipeline agents learn from past runs. At Phase 1 LOAD, the refine, plan, implement and validate agents read distilled lessons from `.archon/memory/` — patterns and avoidances scoped to the files being changed. Refine, implement, conformance and code-review write new lessons back to the store. The result is a continuously improving context layer that does not require any external service.

The flat-file store (`.archon/memory/*.md`) is the authoritative source of truth. It is committed to the repository, human-readable, and diffable in code review. The schema, lifecycle rules, writer-role matrix, and tag vocabulary are defined and maintained in **[`docs/dark-factory-memory-contract.md`](dark-factory-memory-contract.md)** — treat that doc as the stable interface reference. This document is the operator runbook: it covers rollout status, fallback behaviour, rollback steps, failure modes, maintenance CLI usage, cost/performance, and security.

---

## §2 — Component Map

Paths are relative to this repo. Inside a run, the commands invoke the scripts at `<clone>/dark-factory/scripts/…`: the image bakes `scripts/` to `/opt/dark-factory/scripts`, and `entrypoint.sh` copies that directory into the clone when the target repo has no `dark-factory/scripts` of its own.

| Component | File | Role | Used by |
|---|---|---|---|
| Retriever | `scripts/memory_retrieve.py` | Reads area memory files for a phase + file set; emits `### Memory:` block | Refine, plan, implement (through `scripts/load_memory_context.sh`); validate (called directly) |
| Writer | `scripts/memory_write.py` | Writes new `[AVOID]` lessons to the markdown files with dedup/reinforcement, expiry cleanup and the 30-entry cap (evicts the oldest entries by `date:` to make room); does not write `index.jsonl` | Conformance and code-review via `gate_lib.sh::write_memory_entry()`; refine directly |
| Index | `.archon/memory/index.jsonl` | Compact summary of entries; written only by `memory_import.py`; the retriever uses only rows that carry `id` and `source_file` | `memory_retrieve.py` |
| Records | `.archon/memory/records/<id>.json` | Full entry content; read when an index row refers to a record | `memory_retrieve.py` |
| Importer | `scripts/memory_import.py` | Builds `records/` and retriever-compatible index rows from the markdown files | Manual operator invocation |
| Memory files | `.archon/memory/*.md` | Human-readable authoritative store; the retrieval path actually in use in this repo | `memory_retrieve.py`, `memory_write.py`, `memory_maintain.py` |
| Maintenance CLI | `scripts/memory_maintain.py` | Expire + promote provisional + dedup + invalidate, on the `.md` files only | Manual operator invocation; nothing schedules it |

### Who reads and writes

| Phase | Reads | Writes |
|---|---|---|
| Refine | `load_memory_context.sh refine` | `memory_write.py` (`[AVOID]` only) |
| Plan | `load_memory_context.sh plan` | no |
| Implement | `load_memory_context.sh implement` | Shell appends by the agent (`[PATTERN]`/`[AVOID]`/`[FIX]`/`[PROVISIONAL]`), not `memory_write.py` |
| Validate | `memory_retrieve.py --phase validate` | no |
| Conformance | no | `write_memory_entry()` → `memory_write.py` |
| Code-review | no | `write_memory_entry()` → `memory_write.py` |
| Revise-advisory | no | no |

The retriever also accepts `--phase review`; no command uses it.

### Index status in this repo

The index is not the effective retrieval path here. Earlier versions of `memory_write.py` appended index rows without `id` or `source_file`; the retriever skips any row lacking them, so every such row was ignored (the writer no longer appends them, and this repo's `index.jsonl`, which held only such rows, was deleted) and there is no `records/` directory. Retrieval therefore uses the markdown scan. That scan applies the same ranking and top-k / token cap as the index path: entries are ranked by `path:` tag specificity, issue-label boost and `date:` recency, and the block is cut at `token_optimization.memory.max_entries` entries (default 8) or `max_tokens` (default 1 500), whichever comes first. Only `memory_import.py` produces rows the retriever accepts. Part of [#486](https://github.com/omniscient/dark-factory/issues/486).

---

## §3 — Configuration

**No feature flag exists.** The equivalent of "enabling memory v2" is the calls to `load_memory_context.sh`, `memory_retrieve.py` and `memory_write.py` in the phase commands under `commands/`. The system is always on when those commands are in use.

**Default safe mode — fail-open retriever.** If `index.jsonl` is absent, unreadable, or yields no usable rows, the retriever falls through to a direct scan of the `.archon/memory/*.md` files. Missing or malformed memory files yield an empty block. If the memory directory itself is missing the retriever exits 1 with an error on stderr; every caller masks that with `|| true`, so the phase proceeds with empty memory context. Nothing in any gate aborts on a memory read failure.

**No env vars are required.** No credentials, API keys, or external service addresses are needed. Arguments and optional environment:

- `memory_retrieve.py`: `--phase` (required), `--files`, `--issue`, `--labels`, `--memory-dir` (default `.archon/memory`, relative to the current directory), `--emit-trace-to`, `--clone-dir`.
- `load_memory_context.sh <phase>`: needs `ARTIFACTS_DIR`; resolves the repo root with `git rev-parse --show-toplevel` unless `REPO_ROOT` is set, and the store with `MEMORY_DIR` (default `<repo root>/.archon/memory`).
- `TOKEN_OPTIMIZATION_MEMORY_ENABLED` and `TOKEN_OPTIMIZATION_MEMORY_MAX_TOKENS` control the top-k cap, which applies on both the index path and the markdown scan; `token_optimization.memory.max_entries` and the other `memory` keys are read from the config of the clone passed with `--clone-dir` (`load_memory_context.sh` passes `CLONE_DIR`, default the repo root) (see the [token optimization guide](dark-factory-token-optimization.md)).
- `memory_maintain.py` resolves `.archon/memory` relative to the current directory: run it from the repo root.
- `memory_import.py` defaults to `<repo root>/.archon/memory` derived from its own location; `--memory-dir` overrides it.

---

## §4 — Rollout Status

Memory v2 is **deployed**: the retriever, writer, importer and maintenance scripts ship in the image, and the phase commands call them as listed in §2. The flat files in `.archon/memory/` are the authoritative store and are committed to the repository.

| Part | Status |
|---|---|
| Retriever / writer scripts and command integration | shipped, in use |
| Markdown retrieval path | shipped, and the path in use in this repo |
| Index + records retrieval path | partly done: the code exists and only `memory_import.py` produces rows the retriever reads; `memory_write.py` no longer writes index rows ([#486](https://github.com/omniscient/dark-factory/issues/486)) |
| Maintenance CLI | shipped, manual only |

No partial rollout or feature gate exists. Memory v2 cannot be partially enabled per-gate except by editing the command files (see §5 rollback).

---

## §5 — Fallback and Rollback

### Fallback (memory read failure at runtime)

The retriever is fail-open in practice. A missing or unusable index falls back to the markdown scan; an unreadable store results in empty memory context because callers append `|| true`. The phase proceeds with no memory context. There is no pipeline abort and no notification. See §3 default-safe-mode and §6 failure mode table for specific scenarios.

### Rollback (reverting the v2 gate integration)

If the command-file calls to the memory scripts need to be reverted — for example, to isolate a regression — the procedure is:

1. **Revert retrieval:** In each affected `commands/dark-factory-*.md`, replace the `load_memory_context.sh` / `memory_retrieve.py` call block with the pre-v2 inline bash pattern. Recover the reference implementation from git history. The file moved during extraction, so follow renames and use the path it had at the commit you pick:
   ```bash
   git log --oneline --follow --name-only -- scripts/gate_lib.sh | head -40
   git show <pre-v2-sha>:<path-at-that-commit>/gate_lib.sh | grep -A20 "load_memory"
   ```

2. **Revert writes:** In each gate command, replace `memory_write.py` calls (invoked via `write_memory_entry()`) with the pre-v2 direct `awk`/`sed` write pattern. Recover from git history:
   ```bash
   git show <pre-v2-sha>:<path-at-that-commit>/gate_lib.sh | grep -A20 "write_memory"
   ```

3. **Commit the reverted command files:**
   ```bash
   git add commands/
   git commit -m "revert: memory v2 gate integration (debug)"
   ```

4. **An image rebuild and publish is required.** `commands/` and `scripts/` are baked into the Dark Factory image (`/opt/dark-factory/commands`, `/opt/dark-factory/scripts`). `entrypoint.sh` copies the baked commands into the clone whenever the target repo has no `.archon/commands` directory, which is the case for this repo, so a commit to `commands/` on `main` changes nothing until a new image is published and the scheduler runs it. A target that commits its own `.archon/commands` uses that copy instead and picks up a revert on its next run.

### Rollback scope

The `.archon/memory/*.md` files themselves do **not** need to be reverted. They remain the source of truth in both the v2 path and the pre-v2 (inline bash) path. Only the gate tooling (Python scripts vs. inline bash) is being swapped. Accumulated lessons are preserved through the rollback.

---

## §6 — Memory Read/Write Failure Modes

| Failure | Behaviour | Recovery |
|---|---|---|
| `index.jsonl` absent | Retriever falls through to markdown-file scan | No action needed |
| `index.jsonl` present but rows lack `id`/`source_file` (the former state of this repo) | Rows are skipped; retriever falls through to the capped markdown scan | None needed for correctness; see [#486](https://github.com/omniscient/dark-factory/issues/486) |
| `.archon/memory/*.md` absent | Retriever returns empty block; writer creates the directory and file on demand | No action needed |
| `.archon/memory/` directory absent on read | Retriever exits 1; callers mask it with `\|\| true` and proceed with empty context | No action needed |
| Malformed JSONL line in index | Line is skipped; rest of index proceeds normally | Remove the line by hand. `memory_maintain.py` never touches the index, and `memory_import.py` only appends |
| Cap reached (30 entries/file) | Writer writes the new entry and evicts the oldest authoritative entries by `date:` (logged); no abort | None needed; `memory_maintain.py run --no-dry-run` additionally expires old entries and drops near-duplicates |
| Dedup match (identical lesson exists) | Writer reinforces the existing entry (`date:`/`expires:` updated in place); no duplicate written | No action needed |

---

## §7 — Maintenance

Nothing runs maintenance automatically. Run it monthly. Run all commands from the repo root.

### Routine maintenance

```bash
# Dry-run (the default) — print the diff of what would change, write nothing
python scripts/memory_maintain.py run

# Apply: expire old entries, promote provisional to active, dedup near-duplicates
python scripts/memory_maintain.py run --no-dry-run

# Commit any changes
git add .archon/memory/
git commit -m "chore: memory maintenance (expire + promote + dedup)"
```

`run` without `--no-dry-run` never writes. Review the dedup part of the diff before applying: dedup removes the older of two near-identical entries (similarity ≥ 0.90, older by `date:`) and keeps the newer one; it does not retag it `[INVALID]`, because supersession is not invalidation ([#486](https://github.com/omniscient/dark-factory/issues/486)). Note that the file is re-rendered positionally, so section headers can shift relative to the entries after a removal. `run` edits the `.md` files only; it does not read or rewrite `index.jsonl`.

### Invalidate a wrong entry

Use this when a gate wrote a lesson that is factually incorrect or outdated. `--file` takes the bare file name; the script joins it to `.archon/memory/`:

```bash
python scripts/memory_maintain.py invalidate \
  --file architecture.md \
  --match "the exact substring to match" \
  --reason "why this entry is factually wrong"
git add .archon/memory/ && git commit -m "memory: invalidate stale entry"
```

### Build index rows from the markdown files

`memory_import.py` writes one `records/<id>.json` per entry and appends an index row for every entry not already indexed by `id`. It is append-only: it never removes or rewrites existing rows, including the incompatible rows `memory_write.py` appended.

```bash
python scripts/memory_import.py --dry-run  # preview what would be written
python scripts/memory_import.py            # write records/ + append to index.jsonl
git add .archon/memory/ && git commit -m "memory: import markdown entries into index"
```

Running the import changes retrieval behaviour: once the index holds usable rows for the files in scope, the retriever uses the index path instead of the markdown scan, and entries added to the markdown afterwards (which `memory_write.py` does not index) stop being retrievable until the import is re-run. Both paths apply the same cap. Decide that deliberately; [#486](https://github.com/omniscient/dark-factory/issues/486) tracks which path should be the real one.

---

## §8 — Cost and Performance Impact

**Memory read overhead per gate run:** The retriever reads the local markdown files in scope and optionally parses `index.jsonl`. There is no network call, no vector DB, no embedding model, no external service. Runtime is dominated by Python process startup.

**Prompt cost:** the block is capped on both paths (8 entries / 1 500 tokens by default), so it no longer grows with the store; see the memory top-k row in the [token optimization guide](dark-factory-token-optimization.md).

**Memory write overhead:** One file read + one file write per lesson written. Most gate runs write zero lessons — the write bar (defined in the contract doc §3) requires a sufficiently novel finding.

**Storage footprint:** `.archon/memory/*.md` files are committed to git and count against repository size. As of 2026-11-16 this repo's store was about 77 KB: three markdown files of 23–29 KB each (the 34 KB `index.jsonl` of unreadable rows was deleted in [#486](https://github.com/omniscient/dark-factory/issues/486)). The 30-entry cap per file bounds growth; the three files currently exceed it and are trimmed to 30 the next time `memory_write.py` writes to each.

---

## §9 — Security

**Flat-file memory v2 has no runtime security surface.** There are no secrets, no env vars containing credentials, no network endpoints, no external services, and no in-process authentication. The original acceptance criteria for #655 referencing `AGENTMEMORY_SECRET`, localhost-only mode, and a local viewer were written under the discarded agentmemory/vector-DB design (spike #644) and do not apply to the shipped flat-file system.

The two security properties that do apply:

1. **Access control is git-level.** The `.archon/memory/` directory is committed to the repository. Read and write access is governed by the repository's existing branch and PR permissions — nothing memory-specific. Within the pipeline, _logical_ write authority per file is governed by the writer-role matrix in [`docs/dark-factory-memory-contract.md`](dark-factory-memory-contract.md) §4. Plan and validate are read-only consumers; they never call `memory_write.py`. Revise-advisory neither reads nor writes memory.

2. **Entries are committed to git history permanently.** Authors (human or gate agent) must never embed live secrets, credentials, customer data, or PII in lesson bodies. There is no scrubbing layer — once committed, an entry persists in git history even if later invalidated. Entries must contain only distilled engineering lessons about the codebase itself (patterns, avoidances, fixes). The contract doc §3 "Always prohibited" list already enforces this and is the authoritative reference.

No additional security hardening is required.

---

## §10 — Related Docs

- [`docs/dark-factory-memory-contract.md`](dark-factory-memory-contract.md) — authoritative schema, lifecycle rules, writer-role matrix, and migration path to a structured backend
- [`docs/dark-factory-token-optimization.md`](dark-factory-token-optimization.md) — memory top-k cap and the other context-optimization features
- [`CLAUDE.md`](../CLAUDE.md) — repo map and conventions for this repository
