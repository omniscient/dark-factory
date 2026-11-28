# Keep Factory Stats Forever — Non-Deleting Rotation and Readers for Every Generation

**Issue:** #652
**Depends on:** #645 (open; it changes the same two files, `model_proxy.py` and
`run_record.py`, for a different purpose). See "Sequencing with #645".

**Scope decision (operator, 2026-11-27).** #652 was cut in two. This ticket keeps rotation
and the readers. The host mirror (the `deploy/` mount, the stats folder and the `stats-sync`
scheduler step) moved to its own ticket; see "Moved to the mirror ticket". Nothing under
`deploy/**` is touched here.

---

## Problem

Verified against `main`:

1. **The model-proxy ledger deletes history.** `request-ledger.jsonl` rotates at
   `MODEL_PROXY_MAX_BYTES` (default 100 MiB) and unlinks the oldest of
   `MODEL_PROXY_BACKUP_COUNT` (default 3) generations
   (`scripts/factory_core/model_proxy.py::_rotate_if_needed`, `:168-179`).
2. **The cost log overwrites history and takes no lock.** `graphql-costs.jsonl` keeps one
   rotated generation and replaces it on every rotation
   (`scripts/factory_core/cost_log.py:32-38`, `path.replace(str(path) + ".1")`), although the
   scheduler and every run container append to the file.
3. **Readers see only plain generations `.1` … `.BACKUP_COUNT` and take no lock.**
   `run_record.py::_iter_ledger_paths` (`:753-758`) lists exactly those names;
   `_read_ledger_rows` (`:761`) and `_build_issue_economics` (`:1236`) read them while
   `append_ledger` may be rotating.

## Goal

No code path deletes or overwrites a rotated generation of the ledger or of the cost log,
every generation stays readable by every reader, and none of this makes a model request, a
scheduler poll or a preview-helper outcome wait on a file that a run container can plant or
a lock that a run container can hold.

## Threat model

The state directory (`$SCHEDULER_STATE_DIR`) is a volume that every gated run container
mounts read-write. A run container can therefore create any name there (a FIFO, a link, a
directory, a huge or forged `.gz`), hold `flock` on any lock file for as long as it lives,
and be killed at any instruction. The rules below assume all of that. A run container can
already append to or delete the live files; this ticket does not change that and does not
claim to.

## Names

For a live file `<name>` (`request-ledger.jsonl`, `graphql-costs.jsonl`):

| Name | What it is |
|---|---|
| `<name>` | the live file |
| `<name>.lock` | the sidecar lock; never rotated, renamed or removed |
| `<name>.<N>` | plain generation `N` (decimal, no leading zero) |
| `<name>.<N>.gz` | compressed generation `N`; once it exists it is never rewritten, renamed or removed by any code |
| `<name>.<N>.gz.tmp.<16 hex>` | a compression in progress |

A generation is recognised by the **whole** suffix: `^\.[1-9][0-9]*$` or
`^\.[1-9][0-9]*\.gz$` after `<name>`. A temporary file matches neither.

## Write rules (ledger and cost log, one shared scheme)

Thresholds are unchanged: `MAX_LEDGER_BYTES` (`model_proxy.py:60`) and `ROTATE_BYTES`
(`cost_log.py:18`).

- **W1 — Rotate under the lock, never onto an existing name.** Holding `LOCK_EX` on
  `<name>.lock`, when the live file has reached the threshold: `N` = highest generation
  number present in either form, plus one. `os.link(<name>, <name>.<N>)`, then
  `os.unlink(<name>)`. `os.link` fails rather than replace; `os.rename` would silently
  replace and is not used. The row is then appended to a new live file, still under the
  lock. Numbers only grow and are never reused.
- **W2 — An interrupted rotation is completed, not repeated.** A writer killed between the
  link and the unlink leaves the live name and `<name>.<N>` on one inode; the next writer
  would link the same content to `<name>.<N+1>` and every row of that generation would be
  read twice. Before rotating, under the lock: if the live file's link count is above one
  and it is the same inode as the highest plain generation, the live name is removed and no
  new generation is made.
- **W3 — Compression runs outside the lock and off the request path.** After the lock is
  released, `<name>.<N>` is gzip-compressed to `<name>.<N>.gz.tmp.<16 random hex>`, created
  with `O_CREAT | O_EXCL`. A pid is not a unique suffix: both ledger writers are the
  entrypoint of their container (`run-compose.yml:78`, `:104`), so both are pid 1.
  - In `model_proxy.py`, `append_ledger` returns as soon as the row is appended. The
    compress-and-swap step is submitted as its own task to the default executor (where
    `append_ledger` already runs, `model_proxy.py:313`); its failure is logged and never
    reaches a request. Compressing 100 MiB inside the request's ledger step would hold that
    one request for seconds.
  - In `cost_log.py` (1 MiB threshold, synchronous callers) compression runs inline, after
    the lock is released.
- **W4 — The swap takes the lock again, briefly.** Under `LOCK_EX`: if `<name>.<N>.gz`
  does not exist, `os.link(<temp>, <name>.<N>.gz)`; the temporary name is removed either
  way; if `<name>.<N>.gz` now exists, the plain `<name>.<N>` is removed. A generation that
  is briefly present in both forms is harmless (readers prefer the plain one, R2).
- **W5 — Recovery pass.** Each rotation, after compressing the generation it created,
  also compresses plain generations that have no `.gz` (a writer was killed between W1 and
  W4): at most two per rotation, highest number first, by W3 and W4. It also removes, under
  the lock, a plain generation whose `.gz` exists, and temporary files older than one hour.
  Nothing else triggers it; there is no background task.
- **W6 — Only regular files are ever opened, and never blockingly.** Rotation,
  compression and the recovery pass open a generation with `O_RDONLY | O_NOFOLLOW |
  O_NONBLOCK`, require `fstat` to report a regular file no larger than twice the rotation
  threshold, and skip anything else with one log line. A skipped entry does not count
  towards W5's two. (A FIFO planted under a generation name blocked the writer in a
  prototype without this.)
- **W7 — `cost_log.py` gets the sidecar lock, with a bounded wait.** `log_call` opens
  `graphql-costs.jsonl.lock` with `O_NOFOLLOW | O_NONBLOCK` (regular file required) and
  takes `LOCK_EX | LOCK_NB`, retried for at most one second. With the lock it rotates (W1,
  W2) and appends. Without it — the lock is held, or the lock file cannot be opened as a
  regular file — it appends the line and does **not** rotate. `log_call` runs inside the
  scheduler's board fetch on every poll (`providers/tracker/github.py:310`); a blocking
  `LOCK_EX` would let any process holding that lock stop the poll. `log_call` still never
  raises. `append_ledger`'s own wait on `<ledger>.lock` (#634) is not changed by this
  ticket.
- **W8 — Generations from before this change are kept.** The ledger's `.1` … `.3` and the
  cost log's `.1` keep their names and are compressed by the recovery pass like any other
  plain generation. Among themselves their numbers do not mean "higher is newer"; no reader
  depends on generation order for correctness.
- **W9 — `MODEL_PROXY_BACKUP_COUNT` is ignored.** The variable is no longer read by any
  code path; the module keeps no `BACKUP_COUNT` that a reader could use as an upper bound.
  It stays documented, as ignored.
- **W10 — Growth is logged, nothing prunes.** Each rotation logs one line with the
  store's total size: the live file plus every generation in either form. Pruning is a
  manual operator action.

## Read rules

- **R1 — One generation helper.** `run_record._iter_ledger_paths(ledger_path,
  since_epoch=None)` yields the live file, then generations by descending number, found by
  the whole-suffix patterns above. With `since_epoch` it yields only generations whose
  modification time is not earlier than it. It is an importable function; nothing else in
  the repository derives generation names on its own.
- **R2 — Plain wins.** When both forms of one number exist, the plain file is read and
  the `.gz` is not.
- **R3 — `.gz` is read as a bounded stream.** A `.gz` generation is decompressed line by
  line, never loaded whole, and abandoned with one log line once twice the rotation
  threshold of decompressed bytes has been read. A real generation is at most the threshold
  plus one row; a forged archive must not exhaust memory.
- **R4 — The shared lock covers listing and opening, not reading.** The reader opens
  `<ledger>.lock` **without** `O_CREAT` (`O_NOFOLLOW | O_NONBLOCK`, regular file required)
  and takes `LOCK_SH | LOCK_NB`, retried for at most two seconds. Under the lock it lists
  the generations, opens every file it will read (as in W6) and notes the live file's size;
  it then releases the lock and reads from those descriptors, the live file only up to the
  noted size. An open descriptor survives the rename and the removal of a plain generation,
  so the set is a consistent snapshot, and a long read holds no writer back.
- **R5 — No lock, same result shape.** When the lock file does not exist, is not a
  regular file, or is still held after two seconds, the reader proceeds without it. The
  caller's result has the same shape; a read concurrent with a rotation can then miss or
  repeat rows, as every read can today.
- **R6 — All four readers use the helper.**
  - `_read_ledger_rows(run_id, ledger_path, since_epoch=None)` (`run_record.py:761`) passes
    `since_epoch` through.
  - `cmd_assemble` (`:1086`) passes the run's `started_at`.
  - `_compute_harness_economics` (`:840`), when it is given no rows (the
    `_backfill_run_economics` path), passes the record's `started_at`. A `started_at` that
    does not parse gives `since_epoch=None`, the full scan.
  - `_build_issue_economics` (`:1236`) is the full-history reader: no `since_epoch`, the
    same lock rule, `.gz` through R3. Today it uses `read_text` on each path and would lose
    every compressed generation.
- **R7 — `preview_helper.py` (#642) keeps working.**
  - `record_outcome` (`:598`) passes `since_epoch = now - 3600 - 60` (the lane's limit
    ceiling plus a margin) to both `ledger_files_ok` and the bounded read. Unbounded, a
    store that has grown for months would exceed the five-second read limit
    (`_read_ledger_rows_bounded`, `:574`) and turn every helper outcome into
    `ledger_unavailable`.
  - `ledger_files_ok` (`:552`) keeps its rule — each listed path is a regular file itself
    (not a link, FIFO, device or directory) of at most `MAX_LEDGER_FILE_BYTES` — and
    applies it to `<ledger>.lock` as well when that exists.
  - #642's planted-file tests stay: a FIFO, a link to `/dev/zero` and a directory at the
    live name and at a backup name. `request-ledger.jsonl.1` is still a valid plain
    generation name, so the backup case needs no rename.
- **R8 — `scripts/reconcile_cost_reports.py` is not changed.** It reads only the live
  ledger today (`_load_ledger_run_ids`, `:21-36`), is a manual report with no automated
  caller, and falls outside "code that checks ledger backups by name". Rows that rotate out
  become invisible to it; R1's helper is importable so a follow-up can wire it. File that
  follow-up at implementation time if none exists.

No code in the repository reads cost-log generations (`graphql-costs.jsonl` is referenced
only by `cost_log.py`); the cost log gets the write rules and no reader.

## Files touched

| File | Change |
|---|---|
| `scripts/factory_core/model_proxy.py` | W1–W6, W8–W10: numbered rotation, completion of an interrupted one, compress-and-swap as its own executor task, recovery pass, size log; `BACKUP_COUNT` removed |
| `scripts/factory_core/cost_log.py` | W1–W8, W10: the sidecar lock with the bounded wait, the same rotation, inline compression |
| `scripts/factory_core/run_record.py` | R1–R6: the helper with `since_epoch`, streamed `.gz` reading, list-and-open under the shared lock; all four readers on it |
| `scripts/factory_core/preview_helper.py` | R7: `record_outcome` passes `since_epoch`; `ledger_files_ok` takes it and checks the lock file |
| `docs/configuration.md` | `MODEL_PROXY_BACKUP_COUNT` documented as ignored (`:510`); a short "Ledger and cost-log generations" note: names, never deleted, unbounded growth by design, manual pruning is the only reduction |
| `docs/scheduler.md` | the `graphql-costs.jsonl` row (`:440`, "rotates to `.1`") describes numbered, compressed generations |
| `tests/test_factory_model_proxy.py`, `tests/test_cost_log.py`, `tests/test_run_record.py`, `tests/test_preview_helper.py` | below |

Not touched: everything under `deploy/**`, `scheduler.sh`, `entrypoint.sh`,
`run-compose.yml`, `workflows/**`, `scripts/factory_core/cli.py`,
`scripts/reconcile_cost_reports.py`, every `gate_*`, the breaker, budgets,
`.github/workflows/**`.

Whether the write rules live in one new helper module shared by `model_proxy.py` and
`cost_log.py` or are written twice is the plan's choice. If a module is added it is
`scripts/factory_core/rotation.py`, it imports nothing from `providers`, and it is added to
this list by the plan.

## Blast radius

Expected `gate_blast_radius.py` verdict: **HUMAN_REQUIRED**, trigger `hotspot`:
`scripts/factory_core/run_record.py` (5.5) and `scripts/factory_core/model_proxy.py` (5.0).
No `deploy/` path and no workflow file is in the list. All new tests are pytest in the four
existing test files, so `.github/workflows/ci.yml` is not touched; a new `tests/test_*.sh`
would add it as a second trigger and is to be avoided.

## Test plan

Hermetic; names are binding. *guard* marks a test that exists today and must pass
unmodified.

**Tests on `main` that pin the behaviour being removed** and are rewritten:
`tests/test_factory_model_proxy.py::test_append_ledger_rotates_at_max_bytes` (`:152`) and
`::test_append_ledger_rotation_keeps_backup_count` (`:167`, asserts that a generation is
gone); `tests/test_cost_log.py::test_log_call_rotates_when_over_cap` (`:59`, reads `.1` as
plain text). `tests/test_run_record.py::test_read_ledger_rows_includes_rotation_backups`
(`:569`) stays valid and is kept as a *guard* for a plain `.1`.

| Rule / acceptance criterion | Test |
|---|---|
| Criterion 1: nothing deletes or overwrites a generation (W1, W4) | `test_rotation_keeps_every_generation` (ledger and cost log: five rotations; every `.gz` present, readable, and byte-identical after later rotations) |
| Criterion 2: concurrent writers (W1, W3, W4) | `test_concurrent_appends_through_rotations_lose_nothing` (8 processes, ledger and cost log: no missing row, no duplicate, no temporary file and no plain generation left) |
| W1 | `test_rotation_never_replaces_an_existing_generation_name` (a file planted at the next number is intact; the rotation takes the number after it) |
| W2 | `test_rotation_killed_between_link_and_unlink_duplicates_no_row` |
| W3 | `test_two_compressors_of_one_generation_never_share_a_temporary_file`; `test_append_ledger_returns_before_compression_finishes`; `test_compression_failure_is_logged_not_raised` |
| Criterion 4: compression outside the lock (W3) | `test_append_is_not_blocked_by_a_slow_compression` (ledger and cost log) |
| W4 | `test_existing_gz_is_never_replaced_by_a_second_compressor` |
| W5 | `test_stalled_generation_is_compressed_by_the_next_rotation`; `test_recovery_compresses_at_most_two_generations_per_rotation`; `test_stale_temporary_file_is_removed_by_the_recovery_pass` (older than one hour removed, a fresh one kept) |
| W6 | `test_planted_fifo_under_a_generation_name_never_blocks` (under a 10 s alarm; also a link, a directory and a file over twice the threshold: skipped, one log line each) |
| W7 | `test_log_call_returns_within_two_seconds_when_the_lock_is_held` (the row is appended, no rotation happens); `test_log_call_with_a_fifo_as_lock_file_appends_and_returns`; `tests/test_cost_log.py::test_log_call_fails_soft_on_unwritable_dir` (*guard*) |
| W8 | `test_old_numbered_generations_are_kept_compressed_and_read` (ledger `.1`–`.3`, cost log `.1`, present before the first new rotation) |
| W9 | `test_backup_count_variable_is_ignored` (set to 1: five rotations keep five generations) |
| W10 | `test_rotation_logs_the_store_size` |
| Criterion 3: reader (R1, R2) | `test_reader_finds_rows_in_live_plain_and_gz`; `test_temporary_file_is_not_a_generation`; `test_plain_preferred_when_both_forms_exist`; `test_generations_older_than_the_run_are_not_opened` |
| R3 | `test_oversized_gz_is_abandoned` (at twice the threshold, read as a stream; peak memory bounded) |
| R4 | `test_concurrent_read_is_consistent` (writers rotating every ~25 rows: no read with a missing or repeated row); `test_append_is_not_blocked_while_a_reader_reads` |
| R5 | `test_reader_does_not_create_the_lock_file`; `test_reader_proceeds_when_the_lock_is_held` (returns within three seconds, same result shape) |
| R6 | `test_issue_economics_reads_gz_generations`; `test_backfill_read_is_bounded_by_started_at`; `test_unparseable_started_at_means_a_full_scan` |
| R7 | `tests/test_preview_helper.py` stays green, including `::test_ledger_directory_oversized_or_bad_backup_is_unavailable` (*guard*); `test_record_outcome_bounds_the_ledger_scan`; `test_ledger_files_ok_rejects_a_planted_lock_file` |
| #634 unchanged | `tests/test_factory_model_proxy.py::test_append_ledger_lock_follows_the_ledger_path` and `::test_ledger_persistence_does_not_block_event_loop` (*guard*) |
| Docs | the existing docs reference checks |

## Acceptance criteria of #652 that stay with this ticket

1. No code path deletes or overwrites a rotated generation of the ledger or the cost log; a
   test rotates several times and asserts every generation is still present and readable.
2. Concurrent writers: several processes appending through rotations lose no row and
   duplicate none, for the ledger and for the cost log.
3. `_read_ledger_rows` returns rows split across the live file, a plain generation and
   `.gz` generations; a temporary file is ignored; a read concurrent with rotations is
   consistent; generations older than the run are not opened.
4. Compression does not run while the lock is held.
5. (The rotation part of the issue's documentation criterion.) Growth and manual pruning
   are documented in `docs/configuration.md`; `MODEL_PROXY_BACKUP_COUNT` is documented as
   ignored.

## Moved to the mirror ticket

Out of this ticket, with everything the earlier drafts of this spec said about it:

- The `deploy/docker-compose.yml` mount line, the `FACTORY_STATS_DIR` variable in
  `deploy/instance.env.example`, the placeholder directory, and the owner's `deploy/**`
  authorisation, which now applies to that ticket only.
- The `stats-sync` step, `scripts/factory_core/stats_sync.py`, its `cli.py` sub-command and
  its call in `scheduler.sh`.
- The mirror's documentation: the variable, the sync, the loss window, and the edits to
  `docs/scheduler.md:420-423` and `docs/run-lifecycle.md:105` that stop describing the
  named volume as the only copy.

Acceptance criteria of #652 that move with it:

- `docker compose config` renders differ from `main` by exactly the one placeholder mount;
  `run-compose.yml` renders byte-identical.
- `stats-sync` copies new write-once files and never overwrites one, refreshes the growing
  files, does nothing when the variable is empty, never stops the poll loop, and changes
  nothing on a second run.
- After the first sync the stats directory holds every record that is in the volume.
- The variable, the sync and the loss window are documented; `docs/scheduler.md` and
  `docs/run-lifecycle.md` no longer describe the named volume as the only copy.
- The operator's one-time check after `docker compose down -v`.

**What this leaves open.** After this ticket the state volume is still the only copy of
every stat. Removing the volume still erases all of it. This ticket stops the two losses
that happen inside the volume.

## Sequencing with #645

#645 adds fields to ledger rows and reads them in `run_record.py`; it does not change
rotation or generation names. This ticket does not change the row schema. The plan rebases
on whichever lands first. The issue's `Depends on: #645` line gates implementation
dispatch, not this spec.

## Alternatives considered

- **Keep `os.rename` with a prior existence check.** Rejected: the check and the rename
  are two steps; `os.link` is one and fails on an existing name.
- **Compress under the lock.** Rejected: every append would wait for a 100 MiB compression.
- **Leave stalled plain generations for manual clean-up.** Rejected: readers handle them,
  but the recovery pass costs one directory listing on an already rare path.
- **Hold the shared lock for the whole read.** Rejected: a full-history read would hold
  every `append_ledger` call back for its duration, and the descriptor snapshot is as
  consistent.
- **Fold `reconcile_cost_reports.py` in.** Rejected: dormant, advisory, and not covered by
  the issue's criterion (R8).

## Not verified

What was run, by whom, and what rests on reasoning. "Prototype" means a stand-alone script
of the rule, not this repository's code.

- **Run twice** (second reviewer, and again in the confirming read with the same script,
  factory image, uid 1000):
  - W1/W3/W4, 8 writers × 400 rows, a rotation every ~2,000 bytes: 3,200 of 3,200 rows, no
    duplicate, no bad `.gz`, no plain or temporary file left.
  - W2: a writer killed between link and unlink gave 28 and 29 duplicated rows without the
    rule, 0 with it.
  - W5: a writer killed between the rename and the compression; the next rotation
    compressed the generation, nothing left behind.
  - W7: with a blocking `LOCK_EX` the append was still blocked after 8 s; bounded, it
    returned after 1.2 s and 1.1 s with the row appended.
  - R4/R5: reads concurrent with rotations — no inconsistent read with the shared lock,
    whole-read or list-and-open; without a lock 3 of 4,747 and 2 of 2,024 reads were
    inconsistent (up to 28 rows missing or repeated).
- **Not reproduced.** The second reviewer measured, for W3, a generation left unreadable
  with its plain copy removed in 20 of 20 runs when two compressors shared one temporary
  name. The confirming read ran the same script and a slowed variant: 0 of 20 and 0 of 6.
  The rule is kept on reasoning — two pid-1 writers given a pid-named file do write through
  one name, and a random exclusive name removes the question — but the measured failure is
  one reviewer's, on one run.
- **A temporary file left by a killed compressor was observed** in both runs (a writer
  killed after writing its temporary `.gz`): nothing removed it. W5's one-hour clean-up is
  the rule for it; the clean-up itself was not run.
- **Prototyped by the first review only:** the FIFO that blocked the recovery pass (W6).
- **Reasoned, never run:** W8 (old generations), W9, W10, R3's memory bound, R6's
  `_build_issue_economics` and backfill wiring, and all of R7 (the five-second limit
  against a large store is arithmetic, not a measurement).
- **Known, accepted:** a run container can hard-link the live ledger to the next
  generation number; W2 then removes the live name early. No row is lost. A process that
  holds `graphql-costs.jsonl.lock` for good stops cost-log rotation (the file grows) and
  costs each `log_call` one second; it cannot stop the append.
- Line numbers were read on the branch at `94770a2`; the plan re-checks them after #645.
