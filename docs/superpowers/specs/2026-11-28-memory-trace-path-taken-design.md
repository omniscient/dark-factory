# memory-trace: reflect the actual retrieval path (index.jsonl vs markdown)

**Issue:** #39

Follow-up from #144's PR (markethawk#683, the memory-trace feature) and #649/#678/#646
(the index.jsonl primary retrieval path). Flagged high-sev by the factory's reviewer on
the original PR and merged as best-effort v1 since the trace is non-blocking.

---

## Overview / Problem Statement

`scripts/memory_retrieve.py --emit-trace-to` writes `memory-trace.json`, a best-effort
observability artifact consumed verbatim (schema-free fold, no validation) by
`scripts/factory_core/run_record.py::cmd_assemble` as `run_record["memory_trace"]`. It
exists so an operator or a later analysis pass can answer "what memory actually entered
context for this phase, and how."

Since #678/#646, `retrieve_memory()` (`scripts/memory_retrieve.py:530-552`) has two
retrieval paths:
- **Index path**: if `.archon/memory/index.jsonl` exists and `scan_index()`
  (`:365-432`) returns at least one candidate, `format_index_output()` renders it and
  `retrieve_memory()` returns early (`:542-543`). This is authoritative when live
  (per #649) — it reads `index.jsonl` + `records/*.json`, not the `.md` files.
- **Markdown path**: otherwise, `scan_markdown_files()` (`:300-327`) scans the `.md`
  files directly, and `retrieve_memory()` sets `_cap_out["fallback_used"] = True`
  (`:551`).

But `emit_memory_trace()` (`:581-646`), called separately from `main()` (`:711-717`)
after `retrieve_memory()` already ran, does not know which path actually ran. For every
file in `area_files` it unconditionally calls `_count_entries()` (`:557-578`), which
**re-opens and re-scans the `.md` file from scratch** to compute
`entries_total`/`entries_included`/`entries_filtered_out`. Two concrete consequences,
verified by reading the code and by `tests/test_memory_retrieve.py`'s existing
`test_cli_cap_counts_in_trace_via_index_path` (~line 1453 — a memory dir with only
`index.jsonl` + `records/`, no `.md` files at all):

1. **`fallback_used` is wrong.** `_count_entries` returns `None` when the `.md` file
   doesn't exist (`:598`), which sets `fallback_used = True` (`:600`) — even though the
   index path is what actually served the request. The field's entire purpose (signal
   "did retrieval fall back to markdown?") is inverted in exactly the case it matters
   most: a corpus that has migrated to index.jsonl.
2. **`files_loaded` doesn't reflect what was retrieved.** When `.md` files are absent
   or have diverged from the index (stale entries not yet reindexed, or vice versa),
   the trace's per-file counts describe the `.md` file's current contents, not the
   index rows that actually produced the context block.

The mechanism label itself, `retrieval_mechanism: "flatfile-pathtag"` (`:616`), is
**not** part of this bug — both paths are flat-file (no embeddings/vector DB), so the
label stays correct regardless of which path ran. This is a precision fix to the
trace's provenance and count sub-fields, not a relabeling.

---

## Requirements

1. Add a new top-level trace field, `path_taken: "index" | "markdown"`, recording which
   of the two `retrieve_memory()` branches actually produced the output for this
   invocation.
2. When `path_taken == "index"`, `files_loaded`'s per-file
   `entries_total`/`entries_included`/`entries_filtered_out` must be derived from the
   actual `index.jsonl` scan that `scan_index()` performed for this invocation — not
   from an independent `.md` re-read. `entries_selected`/`entries_dropped_by_cap`
   (sourced from `cap_counts["per_file_selected"/"per_file_dropped"]`) are already
   correct for both paths today and keep their existing meaning.
3. When `path_taken == "markdown"`, `files_loaded`'s per-file counts keep today's
   `_count_entries()`-based behavior unchanged — that scan already matches what was
   retrieved on this path.
4. `fallback_used` stops being forced `True` merely because a `.md` file is absent
   while the index path is live. It remains `True` whenever `retrieve_memory()` itself
   took the markdown branch (`cap_counts["fallback_used"]`, already set at `:551`), and
   — only on the markdown path — whenever a specific area file's `.md` file is missing,
   exactly as today.
5. `retrieval_mechanism` stays the literal `"flatfile-pathtag"` for both paths. No new
   mechanism value is introduced.
6. No change to `memory-trace.json`'s consumption: `run_record.py::cmd_assemble`
   already folds the file in verbatim with no schema check, so the new field and the
   corrected semantics of existing fields are backward compatible by construction.
7. Existing tests pinning the markdown path's `emit_memory_trace()` behavior
   (`tests/test_memory_retrieve.py` ~line 854 class) must keep passing unmodified —
   this fix must not change markdown-path output.
8. `tests/test_memory_retrieve.py::test_cli_cap_counts_in_trace_via_index_path`
   (~line 1453) is extended to assert `path_taken == "index"`, `fallback_used is
   False`, and correct `files_loaded` counts. A second new test covers an
   `index.jsonl` that exists but whose every row gets filtered out for the current
   phase/files (expect `path_taken == "markdown"`, since `retrieve_memory()` falls
   through to the markdown branch in that case today and continues to).

---

## Architecture / Approach

All changes are in `scripts/memory_retrieve.py` (+ `tests/test_memory_retrieve.py`).
`dark-factory/scripts/memory_retrieve.py` is a git-ignored runtime self-clone mirror,
not a separate source — it is never edited directly.

### 1. `scan_index()` gains an optional counts accumulator

`scan_index()` (`:365-432`) already filters strictly by `source_file in area_set`
before any other check runs (today at `:393-395`). Move that check to the top of the
loop body (ahead of the `status`/`is_expired` checks it currently follows), and add an
optional `_counts_out` dict param that tracks, per `source_file`, how many rows were
considered (`total`) vs. survived every filter and were actually appended as a
candidate (`included`). Reordering the checks does not change which candidates are
returned — all the filters are ANDed — it only changes when per-file bookkeeping can
be attributed to a `source_file` bucket.

```python
def scan_index(memory_dir, area_files, files, allowed_sources, _counts_out=None):
    ...
    for raw in index_path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue

        source_file = entry.get("source_file", "")
        if source_file not in area_set:
            continue
        counts = _counts_out.setdefault(source_file, {"total": 0, "included": 0}) \
            if _counts_out is not None else None
        if counts is not None:
            counts["total"] += 1

        if entry.get("status") in ("provisional", "invalid"):
            continue
        if is_expired(entry.get("expires_at", ""), today):
            continue

        path_prefixes = entry.get("path_prefixes") or []
        if not passes_path_filter(path_prefixes, files):
            continue

        if source_file not in GLOBAL_FILES:
            agent_id = entry.get("agent_id") or ""
            if agent_id and agent_id not in allowed_sources:
                continue

        entry_id = entry.get("id")
        if not entry_id:
            continue
        # ... unchanged: record read, text/candidate build ...

        if counts is not None:
            counts["included"] += 1
        candidates.append({...})

    return candidates
```

`included` is incremented at the same point the entry is appended to `candidates`, so
"included" means exactly "made it into the ranked output" (same semantics as the
markdown path's `passes_line_filters()`). A row with a `source_file` outside
`area_files` is not counted at all, matching today's behavior where `scan_index` never
considers it in the first place and the trace only ever loops over `area_files`.

### 2. `retrieve_memory()` records which path ran, in the existing `cap_counts` dict

`cap_counts` (passed through `main()` as `_cap_out`, `:706-707`, then re-passed as-is
into `emit_memory_trace()`, `:712-717`) already carries path-relevant telemetry
(`fallback_used`, the cap-count fields). No new parameter is needed on
`emit_memory_trace()` — `retrieve_memory()` writes two more keys into the same dict:

```python
def retrieve_memory(memory_dir, phase, files, labels=None, _cap_out=None, clone_dir=None):
    allowed_sources = PHASE_SOURCE_MAP.get(phase, set())
    area_files = select_area_files(files)
    memory_dir = Path(memory_dir)

    index_path = memory_dir / "index.jsonl"
    if index_path.exists():
        index_counts: dict = {}
        try:
            candidates = scan_index(memory_dir, area_files, files, allowed_sources,
                                     _counts_out=index_counts)
        except (OSError, ValueError):
            candidates = []
        if candidates:
            output = format_index_output(candidates, labels=labels, _cap_out=_cap_out,
                                          clone_dir=clone_dir)
            if _cap_out is not None:
                _cap_out["path_taken"] = "index"
                _cap_out["index_counts"] = index_counts
            return output

    results = scan_markdown_files(memory_dir, area_files, files, allowed_sources)
    candidates = markdown_candidates(results, files)
    output = format_index_output(candidates, labels=labels, _cap_out=_cap_out, clone_dir=clone_dir)
    if _cap_out is not None:
        _cap_out["fallback_used"] = True
        _cap_out["path_taken"] = "markdown"
    return output
```

`path_taken` is set in `retrieve_memory()`, not in `format_index_output()` —
`format_index_output()` runs for both paths and has no way to tell them apart;
`retrieve_memory()` is the only place that knows which branch it is in.

### 3. `emit_memory_trace()` reads `path_taken` and switches counting strategy

```python
def emit_memory_trace(trace_path, phase, files, memory_dir, area_files, allowed_sources,
                       issue=0, agent_id=None, cap_counts=None):
    try:
        memory_dir = Path(memory_dir)
        files_loaded = []
        fallback_used = False
        path_taken = (cap_counts or {}).get("path_taken", "markdown")

        per_file_selected = (cap_counts or {}).get("per_file_selected", {})
        per_file_dropped = (cap_counts or {}).get("per_file_dropped", {})

        if path_taken == "index":
            index_counts = (cap_counts or {}).get("index_counts", {})
            for fname in area_files:
                counts = index_counts.get(fname)
                if counts is None:
                    continue
                files_loaded.append({
                    "path": str(memory_dir / fname),
                    "entries_total": counts["total"],
                    "entries_included": counts["included"],
                    "entries_filtered_out": counts["total"] - counts["included"],
                    "entries_selected": per_file_selected.get(fname, 0),
                    "entries_dropped_by_cap": per_file_dropped.get(fname, 0),
                })
        else:
            for fname in area_files:
                fpath = memory_dir / fname
                counts = _count_entries(fpath, files, allowed_sources, fname) if fpath.exists() else None
                if counts is None:
                    fallback_used = True
                    continue
                files_loaded.append({
                    "path": str(fpath),
                    "entries_total": counts["total"],
                    "entries_included": counts["included"],
                    "entries_filtered_out": counts["total"] - counts["included"],
                    "entries_selected": per_file_selected.get(fname, 0),
                    "entries_dropped_by_cap": per_file_dropped.get(fname, 0),
                })

        if cap_counts and cap_counts.get("fallback_used"):
            fallback_used = True

        trace = {
            "schema_version": 1,
            "retrieval_mechanism": "flatfile-pathtag",
            "path_taken": path_taken,
            "issue": issue or 0,
            "phase": phase,
            "agent_id": agent_id or PHASE_AGENT_ID.get(phase, phase + "-agent"),
            "project": _project(),
            "affected_files": list(files),
            "files_loaded": files_loaded,
            "fallback_used": fallback_used,
        }
        # ... unchanged: cap-count root fields, write, record_delivered ...
    except Exception:
        pass
```

`path_taken` defaults to `"markdown"` when `cap_counts` is `None`/empty/doesn't carry
the key — this preserves the existing direct-call unit tests
(`tests/test_memory_retrieve.py` ~line 854 class), which build `area_files`/
`allowed_sources` by hand and never go through `retrieve_memory()`, so they never see
`cap_counts["path_taken"]`.

On the index branch, a `fname` with no entry in `index_counts` (zero rows for that
`source_file` existed in `index.jsonl`) is simply omitted from `files_loaded` — it is
not treated as a fallback condition. That concept (a specific area file's `.md` being
absent forces `fallback_used`) only applies to the markdown path, where the per-file
scan target really is a single file on disk that either exists or doesn't.

### 4. Docstring corrections

`format_index_output()`'s `_cap_out` docstring (`:446-449`) and `emit_memory_trace()`'s
`cap_counts` docstring (`:584-586`) are updated to list `path_taken` and `index_counts`
alongside the existing cap-telemetry keys, and to drop the now-doubly-wrong line "Empty
dict or None means markdown fallback ran" (already inaccurate today — `cap_counts` is
always populated when a non-`None` dict is passed in, on both paths).

---

## Scope: Knowingly Deferred (Not Spillover)

- **`eval_memory_quality.py`'s `check_hit()` (issue #150) is not touched.** A comment on
  this issue flags an analogous index-vs-markdown representation gap there (it
  substring-matches the full `.md` body rather than what the index path actually
  returns — the record `summary` or a `summary_snippet`). That comment is an unsigned
  `omniscient` FYI, not a scope-expanding "Hermes Agent"-signed instruction, and #39's
  own Fix section names only the memory-trace block. `check_hit()`'s fix is a different
  kind of change (alters eval scoring, has its own tests, risks moving recall numbers)
  and nothing is currently broken (#150 still reports 100% recall on the live corpus).
  **A separate follow-up issue should be filed** ("eval_memory_quality.py: check_hit
  should compare against what the index path actually returns") and linked from #39;
  this spec does not design that fix.
- **No backfill or reconciliation between stale `.md` files and `index.jsonl`.** This
  ticket only changes what the trace *reports*; it does not touch
  `memory_import.py`/`memory_maintain.py` or attempt to keep the two corpora in sync.
- **No `docs/dark-factory-memory-v2.md` / `docs/dark-factory-memory-contract.md`
  update is required by this spec** — neither doc currently documents the trace's
  internal schema (only the CLI flags and the retrieval dual-path), so there is no
  existing claim this change makes stale. Documenting the trace schema for the first
  time is a nice-to-have left to the implementer's judgment, not a requirement.

---

## Alternatives Considered

1. **A standalone `_count_index_entries()` helper mirroring `_count_entries()`'s shape,
   doing a second independent pass over `index.jsonl`.** Rejected: it would have to
   duplicate every one of `scan_index()`'s five filters (status, expiry, path-prefix,
   agent_id/GLOBAL_FILES exemption, missing-`id` skip). `_count_entries()` can safely
   mirror the markdown path's filtering because both call the *same* shared
   `passes_line_filters()`; the index path's filters are written inline in
   `scan_index()` with no equivalent shared function. Two independently-maintained
   copies of the same filter logic is exactly the kind of drift that produces silently
   wrong trace numbers — the class of bug this issue exists to fix. Accumulating counts
   inside `scan_index()`'s existing single pass avoids the duplication entirely and
   reads `index.jsonl` once instead of twice.
2. **Thread `path_taken` as an explicit new parameter on `emit_memory_trace()`,
   decided by `main()`.** Rejected in favor of carrying it in `cap_counts`:
   `cap_counts` is already the exact channel `retrieve_memory()` uses to tell
   `emit_memory_trace()` things about how retrieval went (`fallback_used`, the cap
   counts), and it is already threaded through `main()` unchanged
   (`cap_counts=cap_counts if cap_counts else None`, `:716`). Adding a parallel
   parameter would mean `main()` reads a key out of `cap_counts` only to hand it back
   in through a different channel, for no gain in clarity.
3. **Derive `path_taken` in `emit_memory_trace()` by checking whether `index.jsonl`
   exists on disk, independent of `cap_counts`.** Rejected: existence of
   `index.jsonl` does not imply the index path was actually used — `retrieve_memory()`
   falls through to markdown whenever `scan_index()` raises or returns zero candidates
   even though the file exists (`:538-541`). Only `retrieve_memory()` itself knows
   which branch it took.

---

## Open Questions (Non-blocking)

- Whether a future pass should also reconcile `uncapped_tokens`/`selected_tokens`
  (already sourced correctly from `cap_counts` on both paths, `:626-634`) with a
  per-file token breakdown. Not requested by this issue; left as-is.

---

## Assumptions (Flagged)

- `cap_counts` is always a fresh, per-invocation dict (`main()` creates `cap_counts: dict
  = {}` once per CLI call, `:706`) — there is no cross-call state leakage to account
  for when reading `cap_counts.get("path_taken", ...)`.
- The reordering inside `scan_index()` (moving the `source_file`/`area_set` check ahead
  of the `status`/`is_expired` checks) is behavior-preserving for the returned
  `candidates` list — verified by inspection (all checks are independent `continue`
  guards ANDed together; reordering independent AND-ed guards cannot change which rows
  survive all of them). The existing `TestScanIndex` test class in
  `tests/test_memory_retrieve.py` is the regression guard for this during
  implementation.
