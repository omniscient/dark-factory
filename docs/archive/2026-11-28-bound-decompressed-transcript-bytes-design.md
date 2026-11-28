# Bound Total Decompressed Size Reading Gzipped Transcripts

**Issue:** #656

---

## Overview / Problem Statement

`scripts/factory_core/session_usage.py::load_transcripts` reads every `*.jsonl` and
`*.jsonl.gz` file under a transcript directory through `_iter_lines`, which caps a single
*line* at `MAX_LINE_BYTES` (8 MiB, `session_usage.py:37`). There is no cap on the *total*
decompressed bytes read from one file or across one `load_transcripts()` call, on the
number of lines, or on what the call keeps in memory. A file whose lines are each under
the line cap can therefore make run-record assembly slow and memory-hungry. Measured on
the code at `origin/main` (7255793):

| Input (all lines under the line cap) | Kept heap | Note |
|---|---|---|
| 9.3 MB of minimal assistant lines, 200 000 distinct ids (0.5 MB gzipped) | 135 MB, about 15x the bytes, about 680 B per record | the `messages` dict |
| 88 MB: the same 1 000 ids in 2 000 files (5.3 MB gzipped) | 237 MB for 1 000 records, about 120 B per (file, message) pair | `info["ids"]` lists and `rec["files"]` sets |
| 34 MB: one 8 MiB `cost-state` line in each of 4 files (6.2 MB gzipped) | 372 MB, about 11x the bytes | `info["cost_state"]`, one object kept per file |
| 24 MiB of empty lines | none | 74 s to read: about 3 microseconds per line |

Two callers:

- **Live assembly**: `entrypoint.sh:1347-1361` passes
  `--transcripts-dir "${HOME}/.claude/projects"` to `run-record assemble`. That directory
  holds the CLI's **plain** `*.jsonl` files and is writable by the run's agent, which can
  also plant a `*.jsonl.gz` there (`_transcript_files` accepts both suffixes). The call is
  `|| true` and `_apply_session_usage` (`run_record.py:670`) catches every exception, so
  the record is not lost to an exception; **no timeout wraps the call** (the only
  `timeout` in `entrypoint.sh` is the per-file `gzip` at `:642`).
- **Operator backfill**: `run-record backfill-usage --transcripts-dir <dir>`
  (`_backfill_usage`, `cmd_backfill_usage` in `run_record.py`), normally pointed at the
  gzipped store `${SCHEDULER_STATE_DIR}/transcripts/<run_id>/`. Not wrapped in a `try`.

A run only ever reads its own transcript files, so the effect is confined to that run's
record-keeping. This is hardening filed from the review of
[PR #654](https://github.com/omniscient/dark-factory/pull/654) (#645), not an incident.

**An existing defect is fixed on the way (requirement 3).** At `origin/main`,
`load_transcripts` merges a file's messages line by line and only afterwards learns that
the file is unreadable. A truncated `.gz` (`EOFError`) or one corrupted mid-stream
(`BadGzipFile`/`zlib.error`) that shares a `message.id` with a readable file, which is
the normal shape of a resumed session, leaves a record whose `files` names a file absent
from the result's `files`. `_chains` then raises `KeyError` (`session_usage.py:303`):
`attribute()` fails, so live assembly prints
`run-record: session transcripts not attributed (KeyError)` and every node loses its
session figures, and `backfill-usage` exits 1 with a traceback. The skipped file can also
lower a kept record's `ts`. Both variants were reproduced. A line over `MAX_LINE_BYTES`
does not reach this state: it drops the line, not the file.

Out of scope (per the issue): any change to what is captured, to the ledger, or to gates
and budgets.

## Requirements

1. **Module constants in `session_usage.py`**, declared next to `MAX_LINE_BYTES`:
   - `MAX_FILE_BYTES = 67108864` (64 MiB): bound on one file's counted bytes.
   - `MAX_CALL_BYTES = 268435456` (256 MiB): bound on the counted bytes across every file
     of one `load_transcripts()` call.
   - `LINE_CHARGE_FLOOR = 64`: the least a line is counted as (requirement 2).
   - `MAX_CALL_MESSAGES = 262144` and `MAX_CALL_COST_STATE_BYTES = 4194304` (4 MiB):
     bounds on what a call keeps (requirement 4).

   The two byte bounds equal the writer-side limits of
   `entrypoint.sh::_keep_run_transcripts` (`file_max=67108864 run_max=268435456`,
   `entrypoint.sh:625`), which are applied to the plain source files before compression
   and documented in `docs/configuration.md`'s session-transcripts row. A real file or run
   the writer keeps therefore never trips the reader. Five retained real runs measure
   0.6-4.3 MB, 3-8 files, 18-132 distinct messages, 18-247 (file, message) pairs, and
   20-33 KB per message; their shortest line is 83 bytes and their longest `cost-state`
   line 1 093 bytes. All are module constants, monkeypatchable like `MAX_LINE_BYTES`; no
   `config.yaml` key and no environment variable is added.

2. **What is counted.** Each non-empty result of a `readline` call in `_iter_lines` adds
   `max(len(result), LINE_CHARGE_FLOOR)` to the file's count and to the call's count (for
   a plain file: bytes read; for a `.gz`: decompressed bytes). This includes the bytes of
   a line dropped for exceeding `MAX_LINE_BYTES`, including those read while draining it.
   The count is never taken from `st_size` or from the gzip trailer. The check runs after
   every `readline`, so a file is abandoned at most `MAX_LINE_BYTES + 1` bytes past a
   bound and no more than one line chunk is ever held. The bound applies to `.jsonl` and
   `.jsonl.gz` alike.

   The floor bounds the number of lines, and with it the time: without it 256 MiB of
   empty lines is 268 million iterations (measured: about 13 minutes); with it a call
   parses at most 4.2 million lines (measured: about 3 s for a file of empty lines, which
   trips `MAX_FILE_BYTES` after 1 048 576 lines). No real transcript line is shorter than
   the floor, so real files are counted byte for byte.

   The counting is opt-in for `_iter_lines` / `_json_objects` (a parameter or a small
   wrapper used only by `load_transcripts`). `read_node_windows`, which reads the Archon
   log through the same helpers and catches only `OSError`, keeps today's behavior
   exactly: uncounted, and it never sees the private exception.

3. **Over a bound: the file is skipped, atomically.** Reading stops through a private
   exception caught beside `(OSError, EOFError, zlib.error)` in `load_transcripts`.
   - A file whose own count exceeds `MAX_FILE_BYTES` is skipped; later files are still
     read.
   - A file during which a call-level bound is exceeded (`MAX_CALL_BYTES`, or either
     bound of requirement 4) is skipped, and every remaining file is skipped without
     being opened.
   - Bytes read from a skipped file still count toward the call total.

   **Nothing read from a skipped file may appear in the result**: no `messages` entry, no
   file name in any record's `files` set, no lowered `ts`, no `harness_version` vote, no
   entry in `files`. `load_transcripts` collects a file's contribution separately and
   merges it only after the file was read to its end. This applies to every skip cause,
   including today's `OSError`/`EOFError`/`zlib.error`, which fixes the existing defect
   described above; a size skip must not add a new way to reach that state. After any
   call, every name in a record's `files` is a key of the result's `files`.

4. **Bounds on what is kept.** The byte bounds do not bound memory (table above), so:
   - `MAX_CALL_MESSAGES` bounds the number of **(file, message) pairs** kept by one call:
     the sum, over merged files, of each file's distinct message ids (`len(info["ids"])`).
     It is checked while a file is read (pairs already merged plus this file's distinct
     ids so far), never only at the end of the file. Counting pairs, not distinct
     records, is deliberate: the same ids repeated across many files keep few records but
     many list and set entries.
   - `MAX_CALL_COST_STATE_BYTES` bounds the sum, over merged files, of the raw length of
     the one `cost-state` line each file keeps (its last). It is checked when a file has
     been read to its end, before the merge.

   Either bound exceeded is a call-level skip under requirement 3. With them a call keeps
   at most about 180 MB of message records and about 46 MB of `cost-state` objects. Two
   smaller costs stay bounded only by `MAX_CALL_BYTES`, at about one heap byte per input
   byte: the `first_user` text kept per file and the message id strings. The number of
   files is deliberately not bounded: an empty file costs one `lstat`, one open and about
   0.4 KB kept (measured: 100 000 empty files, 42 MB), linear in files the run created on
   its own disk.

5. **`load_transcripts()` returns one new key, `"skipped_for_size"`**: the sorted list of
   relative paths skipped under requirements 3 and 4, including files never opened (not
   files skipped as unreadable), `[]` when none, always present. Paths only: no contents,
   no byte counts. The docstring lists it. `attribute()` and `backfill()` do not read it
   and their output shape is unchanged.

6. **Reporting, in `run_record.py` only** (`session_usage.py` never prints). Two
   functions are touched and nothing else in the file:
   - `_apply_session_usage`: after a successful `load_transcripts()`, inside the same
     `try`, when the list is non-empty print one line to `sys.stderr`:
     `run-record: N session transcript file(s) skipped (over the size bound)`. A count
     only: no paths, no contents. `entrypoint.sh` already merges this stderr into the run
     log. The existing diagnostics are unchanged.
   - `_backfill_usage`: its returned dict gains `"skipped_for_size"` (the paths) on the
     `no_match` and success returns, not on `no_record`. `cmd_backfill_usage` is not
     edited: it already prints that dict as the one-line stdout JSON, which is the
     operator's report; the new field is additive.
   - **No new key is added to `run-record.json` or `runs.jsonl`.** The record already
     shows the loss where it matters: a model-calling node whose own transcript was
     skipped carries no cost-state figures (its `usage_source` falls back to
     `transcript_messages`, `archon` or `none`) and
     `totals.cost_usd_deduplicated_complete` is `false`. A skipped sub-agent or
     unattributed file changes no node cost and shows only in the stderr line.

7. **Docs.** `docs/configuration.md` (session-transcripts row, `:519`) and
   `docs/run-lifecycle.md` (the #645 paragraph, `:1060-1100`) each gain one sentence: the
   reader applies the same 64 MiB / 256 MiB bounds, a skipped file is reported by one
   stderr line (live) or the `skipped_for_size` field (backfill), and its nodes fall back
   as above. Every line citation into `run_record.py` below the first edit (about line
   686) is re-anchored in the same commit; at `origin/main` these are
   `docs/run-lifecycle.md:1025` (`run_record.py:1138-1395`), `docs/configuration.md:756`
   (`run_record.py:1274`), `docs/configuration.md:527` (the `739` of
   `run_record.py:29,739`) and `docs/features.md:139` (`run_record.py:1600`, `:1605`,
   `:1610`, already stale: the three subcommand parsers are at 1606, 1611 and 1616; set
   them to the parsers' lines after the edit). The reference checker does not catch a citation that still
   resolves to some line. No doc cites `session_usage.py` by line.

8. **Tests** (pytest only: `tests/test_session_usage.py`, `tests/test_run_record.py`; no
   new shell test, no `ci.yml` change). Fixtures are generated at test time and are small
   on disk; bounds are monkeypatched down except where stated. No test asserts a duration
   or a memory figure. New tests in `tests/test_run_record.py` are inserted directly after
   `test_backfill_usage_reads_the_gzipped_transcript_store`, not appended at the end of
   the file (see Scope and merge).
   - Per-file bound: a gzipped file over `MAX_FILE_BYTES` whose lines are all under
     `MAX_LINE_BYTES` is absent from `files` and present in `skipped_for_size`; a later
     file still loads; no `messages` record comes from it. The same for a plain `.jsonl`.
   - Atomicity: an over-bound file that shares a `message.id` with a readable file and
     carries an earlier timestamp for it; then `attribute()` and `backfill()` return
     without raising, the shared record's `files` holds only the readable file and its
     `ts` is the readable file's. The same assertions for a truncated `.gz` and for a
     `.gz` corrupted after its first lines, with `skipped_for_size == []` (regression
     tests for the `KeyError`; both fail on today's code).
   - Per-call bound: files in sorted order, the one that crosses `MAX_CALL_BYTES` and all
     later ones are skipped; a later file is never opened (assert by recording the paths
     passed to the reader).
   - `MAX_CALL_MESSAGES`: the same few ids in several files, with the bound below the
     number of pairs and above the number of distinct ids; the crossing file and later
     files are skipped.
   - `MAX_CALL_COST_STATE_BYTES`: a file whose last `cost-state` line is small is kept
     although an earlier one was long; the file that crosses the bound and later files
     are skipped.
   - Line floor: a file of empty lines is skipped once its line count times
     `LINE_CHARGE_FLOOR` passes a monkeypatched `MAX_FILE_BYTES`.
   - Compression bomb at the shipped bounds: a `.gz` of blank 64 KiB lines written at test
     time (about 70 MiB decompressed, under 1 MB on disk; measured 75 KB and 0.3 s) is
     skipped and a small file beside it loads, under the `SIGALRM` guard pattern of
     `test_hostile_transcripts_never_raise_or_hang` with a generous alarm.
   - A normal gzipped store at the shipped bounds is unaffected:
     `skipped_for_size == []`, and the result equals the one read from the same files
     uncompressed (apart from the `.gz` in the names).
   - `read_node_windows` on a log larger than a monkeypatched `MAX_FILE_BYTES` and
     `MAX_CALL_BYTES` returns the same windows as before.
   - `_apply_session_usage` prints the stderr line only when the list is non-empty, and
     the line holds no path.
   - `_backfill_usage`: `skipped_for_size` holds the path on the success return, is `[]`
     on a `no_match` return, and is absent on `no_record`.
   - `test_hostile_transcripts_never_raise_or_hang` and every other existing test pass
     unmodified.

## Safety

- No budget, breaker, `gate_*` or scheduler decision reads what `load_transcripts` feeds.
  `cost_usd_deduplicated`, `cost_usd_deduplicated_complete`, `cost_usd_session_delta`,
  `usage_reconciled`, `usage_source`, `models` and `unattributed_sessions` are written by
  `run_record.py` and read nowhere else under `scripts/`, `scheduler.sh`, `entrypoint.sh`,
  `workflows/`, `commands/` or `config/` (`git grep` at 4e2b1bf: no match outside
  `run_record.py` and `session_usage.py`). Budget and breaker decisions use Archon's cost
  and the ledger.
- A skip never lowers a recorded cost. Executed on a two-node resumed session (true
  deduplicated cost 0.80): skipping either file gives `cost_usd_deduplicated` 1.30 and
  `cost_usd_deduplicated_complete: false`. A node without cost-state figures keeps
  Archon's `cost_usd`, which for a resumed session is cumulative; dropping the earlier
  file of a chain makes the later node's delta larger, not smaller.
- No change to gates, breaker, budgets, tool lists, `config/`, `workflows/`,
  `entrypoint.sh` or `deploy/**`.

## Scope and merge

- Files: `scripts/factory_core/session_usage.py`, `scripts/factory_core/run_record.py`
  (`_apply_session_usage` and `_backfill_usage` only, about ten lines), the two test
  files, and three docs (`docs/configuration.md`, `docs/run-lifecycle.md`, and the
  citation fix in `docs/features.md`).
- Both source files are blast-radius hotspots (`docs/codeindex-hotspots.md`: 5.5 and 5.0),
  so a blast-radius hold and a human signature are expected at merge.
- **No `Depends on:` line.** #238's branch edits `run_record.py` at lines 16-34, 1008,
  1399-1416 and in `main()`, `docs/run-lifecycle.md` at `:1196-1358`, and appends its
  tests at the end of `tests/test_run_record.py`; this ticket edits `run_record.py` near
  686 and 1499-1540, `docs/run-lifecycle.md` near `:1025` and `:1060-1100`, and inserts
  its tests near line 3566. No hunk overlaps, and #238 is held on a human signature with
  `needs-discussion`, so a dependency would strand this ticket for no gain. Whichever
  merges second re-anchors the line citations of requirement 7.
- #652 (queued behind #238) edits `_read_ledger_rows` (`run_record.py:918`) and the ledger
  readers, which this ticket does not touch; it needs no dependency on this ticket either.

## Architecture / Approach

```
load_transcripts(root, started_at)
  call_bytes = 0, pairs = 0, cost_state_bytes = 0, exhausted = False
  for each file (sorted):
    if exhausted: skipped_for_size += rel; continue          (never opened)
    staged = contribution of this file only
    try:   read lines; each readline charges max(len, 64) to file and call;
           a new id checks pairs + len(staged ids); at end of file check cost-state bytes
    except (OSError, EOFError, zlib.error): continue          (staged dropped)
    except _OverBound: skipped_for_size += rel                (staged dropped)
                       exhausted = (a call-level bound was exceeded); continue
    merge staged into files / messages / versions; add to pairs and cost_state_bytes
  returns {"files", "messages", "harness_version", "skipped_for_size"}
       |
       +--> run_record.py::_apply_session_usage -> stderr count line
       +--> run_record.py::_backfill_usage      -> "skipped_for_size" in the stdout JSON
```

A prototype of exactly this design passes the existing `tests/test_session_usage.py` and
`tests/test_run_record.py` (one unrelated test needs a file outside the exported tree)
and ten new checks covering requirement 8 except the line-floor test (the floor was
exercised by a separate timing script).

## Alternatives Considered

- **Bound `.gz` files only.** Rejected: live assembly reads the agent-writable plain
  `*.jsonl` under `~/.claude/projects`, and the memory growth comes from kept data, not
  from decompression.
- **One shared byte constant.** Rejected: one large file and many small files are
  different shapes, and the writer already names two limits.
- **New byte values.** Rejected: reusing the writer's limits means a kept store cannot
  trip the reader.
- **Keep partial data of a skipped file (today's `continue`).** Rejected: reproduced
  `KeyError` in `_chains`, which loses the whole run's attribution and crashes the
  backfill.
- **Bound distinct `messages` records instead of (file, message) pairs.** Rejected:
  1 000 ids repeated in 2 000 files stay far under such a bound and keep 237 MB;
  scaled to 256 MiB that is about 0.7 GB.
- **Byte bounds only, no bound on what is kept.** Rejected: about 15x the bytes for
  message records and 11x for `cost-state` objects leaves 3-4 GiB reachable inside the
  byte bounds.
- **A separate line-count bound, or a wall-clock deadline, instead of the 64-byte floor.**
  Rejected: the floor reuses the two byte bounds and the same skip paths, needs no clock
  and no extra skip cause.
- **A stderr line in `cmd_backfill_usage` as well.** Rejected: the stdout JSON already
  carries the paths, and it would be one more edit in a hotspot file.
- **A new run-record key for the skip count.** Rejected: the record already fails closed
  (`cost_usd_deduplicated_complete: false`, `usage_source` fallback).
- **Byte counts in `skipped_for_size`.** Rejected: nothing consumes them.

## Open Questions (non-blocking)

- A `timeout` around `run-record assemble` in `entrypoint.sh` is left to a separate
  ticket: none exists today. With the bounds above the read is bounded without one
  (measured worst shapes: about 3 s per file of empty or junk lines, so about 13 s for
  the four such files a call can read; 17 s for 256 MiB of minimal assistant lines).
- `_transcript_files` and `_regular_file` reject symlinks, FIFOs and devices by `lstat`
  before the open. A file replaced by a FIFO between that check and the open would block
  the read; this needs a process still running in the container at assembly time and is
  not addressed here. The fix would be to open with `O_NONBLOCK | O_NOFOLLOW` and check
  the descriptor with `fstat`.
- A bound on the number of files (see requirement 4).

## Assumptions (flagged)

- If the writer-side limits change, the reader constants do not follow automatically.
- `MAX_CALL_MESSAGES = 262144` is about 1 000 times the largest of five measured runs
  (247 pairs); `MAX_CALL_COST_STATE_BYTES` is about 480 times the cost-state bytes of the
  largest (8 files of about 1.1 KB). Larger real runs than these five were not measured.
- Heap and time figures were measured with CPython 3.12 on one host and are orders of
  magnitude, not guarantees.
- "Skipped and counted the same way an unreadable file is today" is read as: same
  `except`, file absent from `files`, no crash. No diagnostic exists today for an
  unreadable file, and this ticket adds none for that case.
