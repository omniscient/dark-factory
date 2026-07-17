# Implementation Plan: Mem0 Memory-v2 Spike — Evaluation Harness, Live Benchmark, Verdict

**Issue:** omniscient/dark-factory#50
**Spec:** `docs/superpowers/specs/2026-07-17-mem0-memory-v2-spike-design.md`

---

## Goal

Build the runnable evaluation harness the spec defines (`scripts/eval_mem0.sh` + two helper
scripts), execute it for real against a pinned local, non-cloud Mem0 install, fill in the spec's
8-row benchmark table with actually-measured results, apply a deterministic decision rule to reach
a verdict (`no-go` / `idea-only` / `optional backend` / `self-hosted service candidate`), and
record that verdict in `.archon/memory` — the deliverable the prior `agentmemory` spike (#644/#661)
claimed in its commit message but never actually committed (flagged in the spec's Assumptions and in
`.archon/memory/codebase-patterns.md`'s `[AVOID]` entry for issue #50).

This plan produces exactly the four implement-phase deliverables named in the spec's Architecture
§1 table — nothing more:

1. `scripts/eval_mem0.sh` — runnable harness (mirrors `scripts/eval_agentmemory.sh`'s shape).
2. Pinned `mem0ai` install + local backend config (disposable, non-default).
3. Filled-in benchmark table + verdict, appended to the spec file (per the spec's Open Questions
   default: same file as #644/#661 did).
4. A `.archon/memory` verdict entry — actually committed this time.

`scripts/mem0_spike_config.py` and `scripts/mem0_retrieve_adapter.py` are internal plumbing *of*
deliverable #1 (the harness needs them to run), not a general-purpose `memory_adapter.py` production
boundary — per spec Architecture §3, that broader adapter is sketched narratively only and is
explicitly out of scope for this spike's committed code.

## Architecture

```
scripts/mem0_spike_config.py   shared local-only Mem0 config (Qdrant-embedded vector store,
                                local HuggingFace embedder, infer=False-only — no LLM call, no
                                cloud API key read at runtime)
        |
        +-- scripts/mem0_import.py            imports .archon/memory/*.md into the Mem0 store
        |                                      (infer=False raw writes, full metadata)
        |
        +-- scripts/mem0_retrieve_adapter.py  CLI-compatible with memory_retrieve.py's
                                               (--phase, --files, --memory-dir) contract, so
                                               eval_memory_quality.py can run its existing
                                               recall methodology against Mem0 unmodified via
                                               --retrieve-script

scripts/eval_mem0.sh   orchestrates all of the above: pinned install, footprint/latency/
                        durability/telemetry/filter checks (rows 1-7), invokes
                        eval_memory_quality.py twice (baseline + Mem0 arm, row 8), applies the
                        decision rule below, prints the filled benchmark table
```

Both `mem0_import.py` and `mem0_retrieve_adapter.py` import `mem0` lazily, inside
`mem0_spike_config.build_memory()`, not at module top level — this keeps `tests/
test_mem0_spike_scripts.py` runnable in CI (which installs only `pytest pyyaml aiohttp`, per
`.github/workflows/ci.yml` line 12) by monkeypatching `build_memory` instead of requiring `mem0ai`
to be installed. `scripts/eval_mem0.sh` itself is **not** wired into `tests/` or `ci.yml` — same
precedent as `scripts/eval_agentmemory.sh` (verified via `git log`: never referenced in `ci.yml`;
confirmed again for this plan).

### Decision rule (mechanical, applied by `eval_mem0.sh`'s final step)

The spec names four possible verdicts (from the issue's own evaluation checklist) but leaves the
mapping from benchmark results to verdict undefined. This plan defines it so the verdict is
computed, not asserted:

- Rows 1-6 are the "operational" rows (install footprint, role/path filters, latency, durability,
  zero-egress/no-API-key, stable IDs). Each gets a PASS/FAIL against the pass bar already stated in
  the spec's Architecture §2 table.
- `FAIL_COUNT` = count of rows 1-6 marked FAIL.
- Row 8 (`recall_delta` = Mem0-arm recall − baseline recall from `eval_memory_quality.py`):

| Condition | Verdict |
|---|---|
| `FAIL_COUNT == 0` and `recall_delta >= -0.10` | `optional backend` |
| `FAIL_COUNT == 0` and `recall_delta < -0.10` | `idea-only` |
| `FAIL_COUNT` in 1-2, row 1 (install) PASS, row 5 (zero-egress) PASS | `idea-only` |
| Otherwise (`FAIL_COUNT >= 3`, or row 1 or row 5 itself FAILs) | `no-go` |

`self-hosted service candidate` is reserved for the specific, mechanically-detectable case where
Qdrant's embedded (no-server) local mode locks its storage directory to a single process — a
documented Qdrant behavior ("storage folder ... is already accessed by another instance") that
surfaces when a second process (the Row 4/6 simulated-restart step, Task 3) opens the same
`STORE_PATH` after the import process closed. If that specific error string appears, embedded mode
cannot support the factory's actual multi-process usage (one process per phase invocation) and a
standalone Qdrant server would be required instead — `eval_mem0.sh` greps for that exact string in
the Row 4/6 step (see Task 3) and overrides the decision-rule table above when it fires, since
that's a distinct, actionable verdict from a flat `no-go`.

## Tech Stack

Python 3 (harness helper scripts, using only the standard library at import time plus a lazily
imported `mem0` package), Bash (orchestrator, mirrors `eval_agentmemory.sh`'s `header`/`ok`/`fail`/
`note` helper convention), pytest (unit tests for the two helper scripts' non-Mem0 logic).

---

## File Structure

| File | Change |
|---|---|
| `scripts/mem0_spike_config.py` | New — shared local-only Mem0 config + `build_memory()` |
| `scripts/mem0_import.py` | New — imports `.archon/memory/*.md` into the Mem0 store, `infer=False` |
| `scripts/mem0_retrieve_adapter.py` | New — `--retrieve-script`-compatible Mem0 search adapter |
| `scripts/eval_mem0.sh` | New — orchestrator harness (install, footprint checks, verdict) |
| `tests/test_mem0_spike_scripts.py` | New — unit tests for the two helper scripts (mocked `mem0`) |
| `scripts/requirements-mem0-spike.txt` | New — pinned dependency lockfile, written by `eval_mem0.sh`'s install step (Task 3), committed as evidence of exactly what was installed |
| `docs/superpowers/specs/2026-07-17-mem0-memory-v2-spike-design.md` | Modified — append `## Live Evaluation Results (implement phase)` section with the filled benchmark table and verdict |
| `.archon/memory/architecture.md` | Modified — verdict entry (`[AVOID]` via `memory_write.py`, or hand-authored `[PATTERN]` if the verdict is positive — see Task 5) |

---

## Memory Context Applied

- `.archon/memory/codebase-patterns.md` `[AVOID]` (issue #50): the prior agentmemory spike's
  commit message claimed a spec file and memory entry that were never actually committed. Task 5
  ends with `git show --stat` verification that the memory entry landed in the actual commit, not
  just a claimed intention — this is the explicit fix for that failure mode.
- `.archon/memory/codebase-patterns.md` `[PATTERN]` (issue #250): use `git diff origin/main HEAD --
  <file>` (two-dot) to test scope, not the three-dot form. Task 4's diff-review step uses this form.
- `.archon/memory/architecture.md` `[AVOID]` (issue #50, ×2): don't re-propose already-shipped Part
  B ideas, and reuse the agentmemory spike's four operational-footprint criteria as named rows. Both
  are already honored by this plan's Goal (scoped to the spec's 4-row deliverable table only) and
  Architecture (rows 1-6 explicitly re-derive, not re-propose, the four `agentmemory` criteria).
- `.archon/memory/dark-factory-ops.md`: no entries matched (checked — none target Python spike
  scripts or Mem0-shaped dependencies).

---

## Task 1: Shared Mem0 config + corpus import script

**Files:** `scripts/mem0_spike_config.py` (new), `scripts/mem0_import.py` (new),
`tests/test_mem0_spike_scripts.py` (new)

1. Write the failing test first (`tests/test_mem0_spike_scripts.py`), covering only
   `mem0_import.load_entries()` — pure parsing logic, no `mem0` dependency:

```python
"""Tests for dark-factory/scripts/mem0_import.py and mem0_retrieve_adapter.py (issue #50 spike).

mem0 is never imported at module level by either script under test (see mem0_spike_config.
build_memory's deferred import) — these tests run without mem0ai installed by monkeypatching
build_memory directly, matching the tests/test_memory_retrieve.py convention of no live I/O.
"""
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mem0_import as mi  # noqa: E402


def _write(tmpdir, fname, content):
    p = Path(tmpdir) / fname
    p.write_text(content, encoding="utf-8")
    return p


def test_load_entries_parses_pattern_and_avoid():
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(
            tmpdir, "backend-patterns.md",
            "- [PATTERN] Use selectinload. <!-- issue:#1 date:2026-01-01 source:implement -->\n"
            "- [AVOID] Never use joinedload. <!-- issue:#2 date:2026-01-01 source:implement -->\n",
        )
        entries = mi.load_entries(Path(tmpdir))
    assert len(entries) == 2
    assert entries[0]["kind"] == "PATTERN"
    assert entries[0]["issue"] == "#1"
    assert entries[1]["kind"] == "AVOID"


def test_load_entries_skips_provisional_and_invalid():
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(
            tmpdir, "codebase-patterns.md",
            "- [PROVISIONAL] Maybe true. <!-- issue:#3 date:2026-01-01 source:refine -->\n"
            "- [INVALID: superseded] Old lesson. <!-- issue:#4 date:2026-01-01 source:refine -->\n"
            "- [PATTERN] Real lesson. <!-- issue:#5 date:2026-01-01 source:refine -->\n",
        )
        entries = mi.load_entries(Path(tmpdir))
    assert len(entries) == 1
    assert entries[0]["body"] == "Real lesson."


def test_load_entries_stops_at_delimiter():
    with tempfile.TemporaryDirectory() as tmpdir:
        _write(
            tmpdir, "architecture.md",
            "- [PATTERN] Above the line. <!-- issue:#6 date:2026-01-01 source:refine -->\n"
            "---\n"
            "- [PATTERN] Below the line, must not be loaded. <!-- issue:#7 date:2026-01-01 -->\n",
        )
        entries = mi.load_entries(Path(tmpdir))
    assert len(entries) == 1
    assert entries[0]["body"] == "Above the line."


def test_main_writes_report_with_mocked_memory(monkeypatch, capsys):
    with tempfile.TemporaryDirectory() as tmpdir:
        memdir = Path(tmpdir) / "memdir"
        memdir.mkdir()
        _write(
            memdir, "backend-patterns.md",
            "- [PATTERN] One lesson. <!-- issue:#8 date:2026-01-01 source:implement -->\n",
        )
        report_path = Path(tmpdir) / "report.json"

        fake_memory = MagicMock()
        fake_memory.add.return_value = {"results": [{"id": "abc123"}]}
        monkeypatch.setattr(mi, "build_memory", lambda store_path: fake_memory)
        monkeypatch.setattr(
            sys, "argv",
            [
                "mem0_import.py",
                "--memory-dir", str(memdir),
                "--store-path", str(Path(tmpdir) / "store"),
                "--report", str(report_path),
            ],
        )

        with pytest.raises(SystemExit) as exc:
            mi.main()
        assert exc.value.code == 0

        fake_memory.add.assert_called_once()
        _, kwargs = fake_memory.add.call_args
        assert kwargs["infer"] is False
        assert kwargs["metadata"]["kind"] == "PATTERN"

        import json
        report = json.loads(report_path.read_text())
        assert len(report["imported"]) == 1
        assert report["imported"][0]["id"] == "abc123"
```

2. Verify it fails (neither `mem0_spike_config.py` nor `mem0_import.py` exists yet):

```bash
python -m pytest tests/test_mem0_spike_scripts.py -v
```

Expected output: `ModuleNotFoundError: No module named 'mem0_import'` (collection error), non-zero
exit code.

3. Implement `scripts/mem0_spike_config.py`:

```python
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
```

4. Implement `scripts/mem0_import.py`:

```python
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
```

5. Verify it passes:

```bash
python -m pytest tests/test_mem0_spike_scripts.py -v
```

Expected output: 4 passed (all `test_load_entries_*` and `test_main_writes_report_with_mocked_memory`),
exit code `0`.

6. Commit:

```bash
git add scripts/mem0_spike_config.py scripts/mem0_import.py tests/test_mem0_spike_scripts.py
git commit -m "feat(mem0-spike): add local-only Mem0 config and corpus import script (issue #50)"
```

---

## Task 2: Mem0-backed `--retrieve-script` adapter

**Files:** `scripts/mem0_retrieve_adapter.py` (new), `tests/test_mem0_spike_scripts.py` (modified)

1. Add the failing test to `tests/test_mem0_spike_scripts.py` (append below the Task 1 tests):

```python
import os

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mem0_retrieve_adapter as mra  # noqa: E402


def test_query_construction_with_files():
    assert mra.build_query("implement", "backend/app/x.py") == \
        "implement lessons for backend/app/x.py"


def test_query_construction_without_files():
    assert mra.build_query("refine", "") == "refine lessons"


def test_main_prints_hits_from_mocked_search(monkeypatch, capsys):
    fake_memory = MagicMock()
    fake_memory.search.return_value = {
        "results": [
            {"memory": "Use selectinload, not joinedload.", "metadata": {"kind": "AVOID"}},
            {"memory": "", "metadata": {"kind": "PATTERN"}},  # empty body must be skipped
        ]
    }
    monkeypatch.setattr(mra, "build_memory", lambda store_path: fake_memory)
    monkeypatch.setenv("MEM0_SPIKE_STORE_PATH", "/tmp/whatever-store")
    monkeypatch.setattr(
        sys, "argv",
        ["mem0_retrieve_adapter.py", "--phase", "implement", "--files", "backend/app/x.py",
         "--memory-dir", ".archon/memory"],
    )

    mra.main()
    out = capsys.readouterr().out
    assert "- [AVOID] Use selectinload, not joinedload." in out
    assert out.count("\n- [") == 1  # the empty-body result must not print a second line


def test_main_errors_without_store_path_env(monkeypatch, capsys):
    monkeypatch.delenv("MEM0_SPIKE_STORE_PATH", raising=False)
    monkeypatch.setattr(
        sys, "argv",
        ["mem0_retrieve_adapter.py", "--phase", "implement", "--files", "", "--memory-dir", "."],
    )
    with pytest.raises(SystemExit) as exc:
        mra.main()
    assert exc.value.code == 1
```

2. Verify it fails:

```bash
python -m pytest tests/test_mem0_spike_scripts.py -v
```

Expected output: 4 new collection/attribute errors (`mem0_retrieve_adapter` doesn't exist yet),
non-zero exit code.

3. Implement `scripts/mem0_retrieve_adapter.py`:

```python
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
    raw = m.search(query, user_id=USER_ID, limit=TOP_K)
    hits = raw.get("results", raw) if isinstance(raw, dict) else raw

    for r in hits:
        body = r.get("memory") or r.get("text") or ""
        if not body:
            continue
        kind = (r.get("metadata") or {}).get("kind", "PATTERN")
        print(f"- [{kind}] {body}")


if __name__ == "__main__":
    main()
```

4. Verify it passes:

```bash
python -m pytest tests/test_mem0_spike_scripts.py -v
```

Expected output: 8 passed total (4 from Task 1 + 4 from this task), exit code `0`.

5. Commit:

```bash
git add scripts/mem0_retrieve_adapter.py tests/test_mem0_spike_scripts.py
git commit -m "feat(mem0-spike): add Mem0-backed eval_memory_quality.py retrieve adapter (issue #50)"
```

---

## Task 3: Orchestrator harness — `scripts/eval_mem0.sh`

**Files:** `scripts/eval_mem0.sh` (new)

1. Write the script:

```bash
#!/usr/bin/env bash
# Evaluation harness for the mem0ai spike (#50). Mirrors scripts/eval_agentmemory.sh's
# header/ok/fail/note convention. NOT wired into tests/ or ci.yml — same precedent as
# eval_agentmemory.sh (a spike harness, not a regression test; live network + pip install
# make it unsuitable for CI).
#
# Usage:
#   bash scripts/eval_mem0.sh
#
# Requires: network access (PyPI install + one-time HuggingFace model download), no API keys.
set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
SPIKE_DIR="$(mktemp -d /tmp/mem0-spike.XXXXXX)"
VENV_DIR="$SPIKE_DIR/venv"
STORE_PATH="$SPIKE_DIR/store"
LOCKFILE="$REPO_ROOT/scripts/requirements-mem0-spike.txt"
MEMORY_DIR="$REPO_ROOT/.archon/memory"

PASS=0
FAIL=0
declare -A ROW_RESULT

header() { echo ""; echo "=== $* ==="; }
ok()     { echo "  PASS: $*"; PASS=$((PASS+1)); }
fail()   { echo "  FAIL: $*"; FAIL=$((FAIL+1)); }
note()   { echo "  NOTE: $*"; }

# ── Row 1: install footprint ─────────────────────────────────────────────────
header "Row 1: install footprint"
python3 -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# Single source of truth for the embedder model name: read it from mem0_spike_config.py rather
# than duplicating the constant in bash (mem0_spike_config's top-level imports are stdlib-only,
# so this works even before mem0ai is installed).
EMBED_MODEL=$(python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/scripts')
from mem0_spike_config import EMBED_MODEL as m
print(m)
")

PINNED_VERSION=$(pip index versions mem0ai 2>&1 | head -1 | sed -E 's/^mem0ai \(([0-9][^)]*)\).*/\1/')
if [ -z "$PINNED_VERSION" ]; then
  fail "could not resolve latest mem0ai version from PyPI"
  ROW_RESULT[1]="FAIL"
else
  note "resolved mem0ai==$PINNED_VERSION"
  T0=$(date +%s)
  if pip install "mem0ai==$PINNED_VERSION" qdrant-client sentence-transformers >"$SPIKE_DIR/install.log" 2>&1; then
    T1=$(date +%s)
    ok "pip install mem0ai==$PINNED_VERSION + qdrant-client + sentence-transformers ($((T1-T0))s)"
    pip freeze > "$LOCKFILE"
    note "wrote $LOCKFILE ($(wc -l < "$LOCKFILE") packages)"
    ROW_RESULT[1]="PASS"
  else
    fail "pip install failed — see $SPIKE_DIR/install.log"
    tail -20 "$SPIKE_DIR/install.log" >&2
    ROW_RESULT[1]="FAIL"
    ROW_RESULT[1_REASON]="install-failed"
  fi
fi

if [ "${ROW_RESULT[1]:-FAIL}" != "PASS" ]; then
  echo ""
  echo "FAIL: cannot proceed without a working mem0ai install."
  echo "Verdict: no-go (row 1 install footprint failed)."
  deactivate 2>/dev/null || true
  exit 1
fi

# ── Row 5 (partial: telemetry env) + import (rows 2, 6, 7 groundwork) ────────
header "Row 5: telemetry / zero network egress (config check)"
# Asserting our own os.environ.setdefault() succeeded would be a tautology (we set the value,
# then check we set it) — instead grep the INSTALLED mem0ai package source to confirm
# MEM0_TELEMETRY is actually load-bearing in its telemetry code path for this pinned version,
# not merely assumed from public docs (per spec Assumptions: "the implement phase's live run
# is what actually confirms or refutes this").
MEM0_PKG_DIR=$(python3 -c "import mem0, os; print(os.path.dirname(mem0.__file__))" 2>/dev/null)
if [ -n "$MEM0_PKG_DIR" ] && grep -rl "MEM0_TELEMETRY" "$MEM0_PKG_DIR" >/dev/null 2>&1; then
  TELEMETRY_SITE=$(grep -rl "MEM0_TELEMETRY" "$MEM0_PKG_DIR" | head -1)
  ok "MEM0_TELEMETRY is referenced in the installed mem0ai source ($TELEMETRY_SITE) — env var is load-bearing, not a no-op"
  ROW_RESULT[5]="PASS"
else
  fail "MEM0_TELEMETRY not found anywhere in the installed mem0ai package ($MEM0_PKG_DIR) — the opt-out may be a no-op for this pinned version; do not claim telemetry is verifiably off"
  ROW_RESULT[5]="FAIL"
fi
note "one-time HuggingFace model download for $EMBED_MODEL is a documented install-time"
note "exception to zero-egress, not a per-query network call — see mem0_spike_config.py"

header "Import: representative corpus (.archon/memory/*.md, infer=False)"
IMPORT_REPORT="$SPIKE_DIR/import-report.json"
T0=$(date +%s)
if python3 "$REPO_ROOT/scripts/mem0_import.py" \
    --memory-dir "$MEMORY_DIR" --store-path "$STORE_PATH" --report "$IMPORT_REPORT"; then
  T1=$(date +%s)
  ok "corpus imported in $((T1-T0))s"
else
  fail "corpus import reported failures — see $IMPORT_REPORT"
fi

# ── Row 6: stable IDs + Row 4: durability across restart ─────────────────────
header "Row 6 / Row 4: stable record ID + durability across restart"
FIRST_ID=$(python3 -c "
import json
d = json.load(open('$IMPORT_REPORT'))
print(d['imported'][0]['id'] if d['imported'] else '')
")
if [ -z "$FIRST_ID" ]; then
  fail "no imported record ID to test durability against"
  ROW_RESULT[4]="FAIL"; ROW_RESULT[6]="FAIL"
else
  # Simulate a restart: this is a NEW python process opening the same STORE_PATH the import
  # process (also exited by now) wrote to — no process state is reused, only disk contents.
  RESTART_LOG="$SPIKE_DIR/restart-check.log"
  if python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/scripts')
from mem0_spike_config import build_memory
m = build_memory('$STORE_PATH')
rec = m.get(memory_id='$FIRST_ID')
assert rec is not None, 'record not found after simulated restart'
print('record survived restart:', rec.get('id'))
" >"$RESTART_LOG" 2>&1; then
    ok "record ID $FIRST_ID resolvable after simulated restart (no re-import)"
    ROW_RESULT[4]="PASS"; ROW_RESULT[6]="PASS"
  else
    cat "$RESTART_LOG" >&2
    if grep -qi "already accessed by another instance" "$RESTART_LOG"; then
      fail "record ID $FIRST_ID NOT resolvable — Qdrant embedded mode locks STORE_PATH to a single process (see $RESTART_LOG)"
      note "embedded (no-server) vector store cannot support the factory's actual multi-process usage — a standalone Qdrant server would be required"
      ROW_RESULT[1_REASON]="embedded-store-inadequate"
    else
      fail "record ID $FIRST_ID NOT resolvable after simulated restart (see $RESTART_LOG)"
    fi
    ROW_RESULT[4]="FAIL"; ROW_RESULT[6]="FAIL"
  fi
fi

# ── Row 3: retrieval latency at factory scale ─────────────────────────────────
header "Row 3: retrieval latency (~28-34 entry corpus scale)"
LATENCY_MS=$(MEM0_SPIKE_STORE_PATH="$STORE_PATH" python3 -c "
import sys, time
sys.path.insert(0, '$REPO_ROOT/scripts')
from mem0_spike_config import build_memory, USER_ID
m = build_memory('$STORE_PATH')
t0 = time.time()
m.search('implement lessons', user_id=USER_ID, limit=8)
print(int((time.time() - t0) * 1000))
")
note "search latency: ${LATENCY_MS}ms"
if [ "${LATENCY_MS:-99999}" -lt 500 ]; then
  ok "latency ${LATENCY_MS}ms — no material regression vs current sub-100ms baseline"
  ROW_RESULT[3]="PASS"
else
  fail "latency ${LATENCY_MS}ms — materially slower than current approach"
  ROW_RESULT[3]="FAIL"
fi

# ── Row 2: role/path metadata filter support ──────────────────────────────────
# Tests the "issue" key specifically — one of the exact filter keys spec Requirement 2 /
# Architecture §2 row 2 names (agent_id/path_prefix/issue/source/kind/expires), matching
# memory_retrieve.py's PHASE_SOURCE_MAP + path-tag filtering shape, not an arbitrary key.
header "Row 2: role/path filter support (issue key, per PHASE_SOURCE_MAP shape)"
FILTER_OK=$(MEM0_SPIKE_STORE_PATH="$STORE_PATH" python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/scripts')
from mem0_spike_config import build_memory, USER_ID
m = build_memory('$STORE_PATH')
unfiltered = m.search('lessons', user_id=USER_ID, limit=50)
u = unfiltered.get('results', unfiltered)
issues = sorted({
    (r.get('metadata') or {}).get('issue') for r in u if (r.get('metadata') or {}).get('issue')
})
if not issues:
    print('no-issue-metadata-found')
    sys.exit(0)
target_issue = issues[0]
filtered = m.search('lessons', user_id=USER_ID, limit=50, filters={'issue': target_issue})
f = filtered.get('results', filtered)
constrained = 0 < len(f) < len(u) and all(
    (r.get('metadata') or {}).get('issue') == target_issue for r in f
)
print('yes' if constrained else 'no')
" 2>/dev/null || echo "error")
if [ "$FILTER_OK" = "yes" ]; then
  ok "metadata filter on 'issue' actually constrains results (not a silent no-op)"
  ROW_RESULT[2]="PASS"
else
  fail "metadata filter on 'issue' did not constrain results, errored, or no issue metadata was found ($FILTER_OK) — silently unimplemented, same failure shape as agentmemory #644"
  ROW_RESULT[2]="FAIL"
fi

# ── Row 7: infer=False availability (static; infer=True NOT executed live) ────
header "Row 7: infer=False raw writes vs infer=True (static check only)"
note "infer=False path already exercised throughout this run via mem0_import.py — PASS by construction"
ROW_RESULT[7]="PASS"
note "infer=True is NOT exercised live in this spike (would require a real LLM API key,"
note "violating the 'no Mem0 Cloud by default' / 'no hidden LLM calls' non-goals) — documented"
note "as a known limitation, not a benchmark failure."

# ── Row 8: retrieval quality — Mem0 top-k vs factory scoped+capped ───────────
header "Row 8: retrieval quality (eval_memory_quality.py methodology)"
BASELINE_REPORT="$REPO_ROOT/evals/memory-quality-report.md"
MEM0_REPORT="$REPO_ROOT/evals/mem0-quality-report.md"

python3 "$REPO_ROOT/scripts/eval_memory_quality.py" \
  --memory-dir "$MEMORY_DIR" \
  --retrieve-script "$REPO_ROOT/scripts/memory_retrieve.py" \
  --output "$BASELINE_REPORT" 2>"$SPIKE_DIR/baseline-eval.log"
BASELINE_RECALL=$(grep -oE '^Recall: [0-9.]+%' "$SPIKE_DIR/baseline-eval.log" | grep -oE '[0-9.]+' | head -1)

MEM0_SPIKE_STORE_PATH="$STORE_PATH" python3 "$REPO_ROOT/scripts/eval_memory_quality.py" \
  --memory-dir "$MEMORY_DIR" \
  --retrieve-script "$REPO_ROOT/scripts/mem0_retrieve_adapter.py" \
  --output "$MEM0_REPORT" 2>"$SPIKE_DIR/mem0-eval.log"
MEM0_RECALL=$(grep -oE '^Recall: [0-9.]+%' "$SPIKE_DIR/mem0-eval.log" | grep -oE '[0-9.]+' | head -1)

note "baseline (factory scoped+capped) recall: ${BASELINE_RECALL:-N/A}%"
note "Mem0 top-k-every-turn recall: ${MEM0_RECALL:-N/A}%"
if [ -n "${BASELINE_RECALL:-}" ] && [ -n "${MEM0_RECALL:-}" ]; then
  RECALL_DELTA=$(python3 -c "print(f'{($MEM0_RECALL - $BASELINE_RECALL) / 100:.4f}')")
  note "recall_delta: $RECALL_DELTA"
else
  RECALL_DELTA="unknown"
  fail "could not compute recall_delta — one or both eval runs did not produce a Recall line"
fi

# ── Decision rule ──────────────────────────────────────────────────────────
header "Verdict"
FAIL_COUNT=0
for row in 1 2 3 4 5 6; do
  [ "${ROW_RESULT[$row]:-FAIL}" = "FAIL" ] && FAIL_COUNT=$((FAIL_COUNT+1))
done
note "operational FAIL_COUNT (rows 1-6): $FAIL_COUNT"

if [ "$FAIL_COUNT" -eq 0 ]; then
  if [ "$RECALL_DELTA" != "unknown" ] && python3 -c "exit(0 if $RECALL_DELTA >= -0.10 else 1)"; then
    VERDICT="optional backend"
  else
    VERDICT="idea-only"
  fi
elif [ "$FAIL_COUNT" -le 2 ] && [ "${ROW_RESULT[1]}" = "PASS" ] && [ "${ROW_RESULT[5]}" = "PASS" ]; then
  VERDICT="idea-only"
else
  VERDICT="no-go"
fi

if [ "${ROW_RESULT[1_REASON]:-}" = "embedded-store-inadequate" ]; then
  VERDICT="self-hosted service candidate"
fi

echo ""
echo "VERDICT: $VERDICT"
echo "Rows: $(for row in 1 2 3 4 5 6 7; do echo -n "$row=${ROW_RESULT[$row]:-FAIL} "; done)"
echo "recall_delta: $RECALL_DELTA"
echo ""
echo "============================="
echo "Results: ${PASS} passed, ${FAIL} failed"
echo "============================="

deactivate 2>/dev/null || true
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
```

2. Verify the script is syntactically valid without needing network access or mem0ai installed:

```bash
bash -n scripts/eval_mem0.sh
```

Expected output: no output, exit code `0`.

3. Verify the two Python helper scripts it shells out to still pass their unit tests (no
   regression from Task 1/2):

```bash
python -m pytest tests/test_mem0_spike_scripts.py -v
```

Expected output: 8 passed, exit code `0`.

4. Make it executable and commit:

```bash
chmod +x scripts/eval_mem0.sh
git add scripts/eval_mem0.sh
git commit -m "feat(mem0-spike): add eval_mem0.sh orchestrator harness with decision rule (issue #50)"
```

---

## Task 4: Execute the harness and record the filled benchmark table

**Files:** `docs/superpowers/specs/2026-07-17-mem0-memory-v2-spike-design.md` (modified),
`scripts/requirements-mem0-spike.txt` (new, produced by Task 3's Row 1 step)

1. Run the harness for real:

```bash
bash scripts/eval_mem0.sh 2>&1 | tee /tmp/mem0-eval-output.txt
```

Expected: the script prints `=== Row N: ... ===` sections ending in a `VERDICT: <one of the four
categories>` line and a `Results: N passed, M failed` summary. Exit code `0` if `FAIL_COUNT` (rows
1-6) treats every row as PASS *and* row 8's recall comparison ran; non-zero otherwise — a non-zero
exit is expected and valid data (e.g. `no-go`), not a step failure to "fix": do not retry-until-green.

2. If Row 1 (install) failed outright, stop here — per the script's own early-exit, the verdict is
   already `no-go` and Task 5 records that. Otherwise continue.

3. Append the actual output as a new section at the end of the spec file (after "## Assumptions"),
   replacing no existing content:

```markdown

---

## Live Evaluation Results (implement phase, <ACTUAL RUN DATE>)

Harness: `bash scripts/eval_mem0.sh` (issue #50). Full output: see commit for this section — the
table below is a transcription of the harness's own `Row N` / `VERDICT` lines, not independently
re-derived.

| # | Criterion | Precedent (agentmemory, #644) | Mem0 result | Pass bar | Result |
|---|---|---|---|---|---|
| 1 | Install/deploy footprint | No prebuilt image/npm package; 3-process source build | <ACTUAL: pinned version + install time from Row 1> | Single-process, no source build, no extra long-running service | <PASS/FAIL> |
| 2 | Role/path filter support | Silently unimplemented | <ACTUAL: Row 2 result> | Filters must actually constrain results | <PASS/FAIL> |
| 3 | Retrieval latency (~28-34 entries) | BM25 37× slower than grep | <ACTUAL: Row 3 latency ms> | Must not regress materially vs. sub-100ms baseline | <PASS/FAIL> |
| 4 | State durability across restarts | In-memory; full re-import required | <ACTUAL: Row 4 result> | Must persist without full re-import | <PASS/FAIL> |
| 5 | Zero network egress / no cloud API key | N/A | <ACTUAL: Row 5 result + HF download caveat> | Must be verifiably off | <PASS/FAIL> |
| 6 | Stable, dereferenceable record ID | N/A | <ACTUAL: Row 6 result> | ID stable across restart | <PASS/FAIL> |
| 7 | `infer=False` raw writes vs. LLM-inferred extraction | N/A | `infer=False` verified working; `infer=True` not executed live (would require a real LLM API key) | Raw writes must be available with no hidden LLM call | PASS (by construction) |
| 8 | Retrieval quality: Mem0 top-k vs. factory scoped+capped | N/A | <ACTUAL: baseline recall %, Mem0 recall %, delta> | Report delta; informs verdict, doesn't gate alone | <informational> |

**Verdict: `<ACTUAL VERDICT STRING FROM Row "Verdict" SECTION>`**

<One paragraph restating the harness's own decision-rule reasoning for this verdict, quoting the
FAIL_COUNT and recall_delta values it printed — not a new post-hoc rationalization.>

### Candidate follow-up tickets (recommended only, not created — per spec Requirement 9)

<Copy spec Architecture §5's list verbatim, UNLESS the verdict is `optional backend` or
`self-hosted service candidate`, in which case also un-comment the spec's conditional last bullet
("a Mem0-backed optional retrieval backend behind the adapter") since its condition is now met.>
```

4. Confirm the diff is scoped to exactly this appended section plus the new lockfile (two-dot form,
   per `.archon/memory/codebase-patterns.md`'s `[PATTERN]` issue #250):

```bash
git diff origin/main HEAD -- docs/superpowers/specs/2026-07-17-mem0-memory-v2-spike-design.md
```

Expected output: a diff that only adds lines after the `## Assumptions` section — no existing line
changes.

5. Commit:

```bash
git add docs/superpowers/specs/2026-07-17-mem0-memory-v2-spike-design.md scripts/requirements-mem0-spike.txt
git commit -m "docs(mem0-spike): record live evaluation results and verdict (issue #50)"
```

---

## Task 5: Record the verdict in `.archon/memory`

**Files:** `.archon/memory/architecture.md` (modified)

1. Branch on the verdict recorded in Task 4:

   **If verdict is `no-go` or `idea-only`** (the two outcomes where Mem0 itself should not become a
   backend), write an `[AVOID]` entry using the existing write-through tool:

```bash
python3 scripts/memory_write.py \
  --target .archon/memory/architecture.md \
  --path-prefix "dark-factory/scripts/" \
  --text "Do not adopt Mem0 (mem0ai) as a Dark Factory memory backend as of the #50 spike — verdict '<ACTUAL VERDICT>' on FAIL_COUNT=<N>/6 operational rows (see docs/superpowers/specs/2026-07-17-mem0-memory-v2-spike-design.md Live Evaluation Results for the full table). Retrieval-quality/design ideas from the spike (append-only events, entity extraction, procedural handoff memory, retrieval explanations) remain valid follow-ups independent of this verdict." \
  --source implement \
  --issue 50
```

   **If verdict is `optional backend` or `self-hosted service candidate`** (Mem0 cleared the
   operational bar), `memory_write.py`'s CLI only emits `[AVOID]`-tagged entries — hand-author a
   `[PATTERN]` entry instead, appended immediately before the `---` delimiter in
   `.archon/memory/architecture.md`, using the exact same tag-comment format `memory_write.py`'s
   Step 4 produces (so `memory_retrieve.py`/`memory_import.py` parse it identically):

```
- [PATTERN] Mem0 (mem0ai) cleared the #50 spike's operational bar (FAIL_COUNT=<N>/6, verdict
  '<ACTUAL VERDICT>') — viable as an opt-in retrieval backend behind a future memory_adapter.py
  boundary (spec Architecture §3), never the default. See
  docs/superpowers/specs/2026-07-17-mem0-memory-v2-spike-design.md Live Evaluation Results for
  the full benchmark table. <!-- issue:#50 date:<ACTUAL DATE> expires:<DATE+6mo> source:implement
  agent:implement scope:architecture path:dark-factory/scripts/ -->
```

2. Verify the entry actually landed (the explicit fix for `.archon/memory/codebase-patterns.md`'s
   `[AVOID]` issue #50 entry — a prior spike's commit message claimed this and it never landed):

```bash
grep -n "issue:#50" .archon/memory/architecture.md
git show --stat HEAD | grep "architecture.md"
```

Expected output: at least one matching line from `grep`, and `architecture.md` listed in the
commit's changed-file stat.

3. Commit (if `memory_write.py` was used, its own write already modified the working tree — this
   commits that change; if hand-authored, this is the entry's only commit):

```bash
git add .archon/memory/architecture.md
git commit -m "memory(mem0-spike): record #50 verdict in architecture.md (issue #50)"
```

---

## Out of Scope (explicitly, per spec)

- A general-purpose `memory_adapter.py` `add`/`search` dispatcher spanning both the flat-file and
  Mem0 backends — spec Architecture §3 scopes this spike to *sketching* the adapter boundary
  narratively in the results doc, not shipping it as the new default retrieval path for any
  existing phase command. `mem0_retrieve_adapter.py` (Task 2) is Mem0-specific harness plumbing,
  not that adapter.
- Any change to `memory_retrieve.py`, `memory_write.py`, `memory_maintain.py`,
  `load_memory_context.sh`, or any `commands/*.md` phase command — the existing flat-file retrieval
  path is untouched regardless of this spike's verdict (spec Requirement 7 / Architecture §3).
- A live `infer=True` LLM-inferred-extraction run — would require a real LLM API key, violating the
  "no Mem0 Cloud by default" / "no hidden LLM calls without token/cost telemetry" non-goals. Row 7
  is scored on `infer=False` availability alone (see Task 3's Row 7 step).
- Prototyping the Proactive Memory Agent paper's Phase-2 intervention-timing (targeted reminder /
  explicit silence) layer — spec Requirement 5/7 explicitly forbids giving epic #241 a Mem0-shaped
  starting point.
- Creating or labeling any follow-up child issue — spec Requirement 9; Task 4 only transcribes the
  spec's existing candidate-follow-up list (plus, conditionally, un-commenting one already-written
  bullet), it does not open new GitHub issues.
- Any change to `deploy/instances/**`, `.github/workflows/publish.yml`, `.claude/skills/**`,
  `.claude/settings*.json`, or `.mcp.json` — hard-excluded paths per `.factory/adapter.yaml`; none
  of this plan's tasks touch them.
