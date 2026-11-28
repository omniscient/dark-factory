# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CLAUDE.md`** at the repo root — repo map, conventions and hard limits.
- **`docs/adr/`** — Architecture Decision Records for past design choices.
- **`CONTEXT.md`** at the repo root, if present — project glossary and domain language. It does not exist yet.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The producer skill (`/grill-with-docs`) creates them lazily when terms or decisions actually get resolved.

## File structure

Single-context repo:

```
/
├── CLAUDE.md, README.md
├── scheduler.sh, entrypoint.sh   ← poll loop and per-run container entry
├── workflows/, commands/         ← the run DAG and the phase agent instructions
├── scripts/, config/             ← deterministic gates, factory_core, policy knobs
├── .archon/memory/               ← factory memory files
├── docs/                         ← you are here (living docs)
│   ├── adr/                      ← 0008, 0011 (numbering inherited from MarketHawk)
│   ├── superpowers/              ← feature specs and implementation plans
│   └── archive/                  ← shipped specs and plans
└── tests/
```

Note: `docs/superpowers/specs/` holds **in-flight** feature specifications; `docs/superpowers/plans/` holds **in-flight** implementation plans. `docs/superpowers/plan-drafts/` holds plan drafts under review on a refine branch; a draft is moved into `plans/` when the plan is published and is never present on `main`. Specs and plans are write-once artifacts — shipped specs/plans are archived to `docs/archive/` automatically when an implementation PR is created. These are *not* ADRs; ADRs record architectural decisions and their rationale.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md` once it exists. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/grill-with-docs`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0008 (ephemeral run containers) — but worth reopening because…_

## Memory Isolation and Agent Role Scoping

The memory schema and lifecycle are specified in [`dark-factory-memory-contract.md`](dark-factory-memory-contract.md); the operator runbook is [`dark-factory-memory-v2.md`](dark-factory-memory-v2.md). This section covers only how entries are tagged and filtered.

`scripts/memory_write.py` tags each markdown entry with `source:` and `agent:` (both hold the `--source` value, i.e. the writing phase: `refine`, `conformance`, `code-review`, …; `agent` is not a separate role identity), plus `scope:` and `path:`. It does not write `index.jsonl`. At the 30-entry cap per file it writes the new lesson and evicts the oldest authoritative entry by `date:`.

### Cross-phase read filter

`scripts/memory_retrieve.py` filters area-file entries by the reading phase through `PHASE_SOURCE_MAP`; the global files (`codebase-patterns.md`, `architecture.md`) are exempt:

| Reading phase | Sees entries written by |
|---|---|
| `refine`, `plan` | `refine` |
| `implement` | `implement` |
| `validate` | `conformance` |
| `review` | `code-review` |

Refine, plan and implement load memory through `scripts/load_memory_context.sh`; validate calls `memory_retrieve.py --phase validate` directly, so it sees conformance entries and not implementation reasoning. Conformance and code-review do not read memory; they write it through `write_memory_entry` in `scripts/gate_lib.sh`.

### Role constants (removed)

`scripts/agent_roles.sh` used to declare 13 `AGENT_ID_*` role constants and `MEMORY_PROJECT`. They were dead: nothing consumed `$AGENT_ID`, `write_memory_entry` never passed a role to `memory_write.py`, and retrieval used its own vocabulary (`PHASE_AGENT_ID` in `memory_retrieve.py`). The file and the `AGENT_ID=` assignments in the conformance and code-review commands were removed in [#491](https://github.com/omniscient/dark-factory/issues/491). Memory tagging and filtering go through `--source` and `PHASE_SOURCE_MAP` only.
