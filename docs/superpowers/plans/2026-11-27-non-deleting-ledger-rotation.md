# Keep Factory Stats Forever — Non-Deleting Rotation and Readers Implementation Plan

**Issue:** #652

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** No code path deletes or overwrites a rotated generation of the model-proxy request ledger or the GraphQL cost log. Older generations are gzip-compressed outside the lock. Every ledger reader reads every generation (plain or `.gz`) through one helper, takes a bounded shared lock to list and open, and per-run readers skip generations older than the run.

**Architecture:** A new module, `scripts/factory_core/rotation.py`, holds the single rotation scheme (spec W1–W10) and the generation-name pattern, and imports nothing from `providers`. `model_proxy.py` and `cost_log.py` call it. `append_ledger` rotates and appends under the existing `#634` lock and returns the number of the generation it rotated out. `_persist_ledger` submits the compress-and-recover step to the default executor as its own task. `cost_log.log_call` takes the sidecar lock with a 1-second bounded wait and compresses inline after releasing it. `run_record._iter_ledger_paths` becomes the one reader helper (R1, R2, with `since_epoch`). A new `_read_ledger` core does R3–R5: it lists and opens under `LOCK_SH`, then reads `.gz` as a bounded stream. All four readers go through it (R6), and `preview_helper.record_outcome` bounds its scan (R7).

**Tech Stack:** Python 3.12 stdlib (`os`, `fcntl`, `gzip`, `secrets`, `stat`), aiohttp's event loop executor (already used), pytest.

**Spec:** `docs/superpowers/specs/2026-11-27-durable-factory-stats-design.md` (approved at `ed33507`). Rule IDs W1–W10 and R1–R8 below refer to it.

**Memory context applied:** the plan-phase memory entries (spike/backend-evaluation AVOIDs, refine-artifact transfer, two-dot OOS diff) do not bear on this change; no memory-derived step applies. The two-dot `git diff origin/main HEAD -- <file>` pattern is used in Task 10's scope check.

---

## Ground rules for the implementer

- **Dependency gate.** `Depends on: #645` gates dispatch. #645 changes `model_proxy.py` and `run_record.py` for a different purpose (row fields). It does not change rotation or generation names. Task 0 rebases on `main` and re-checks the anchors below, because the line numbers were read at `94770a2`.
- **Scope.** Touch only the files in the File Structure table. In particular, do **not** touch `deploy/**`, `scheduler.sh`, `entrypoint.sh`, `run-compose.yml`, `workflows/**`, `scripts/factory_core/cli.py`, `scripts/reconcile_cost_reports.py`, `.github/workflows/**`, any `gate_*`, the breaker, budgets, or the stale vendored copy under `dark-factory/scripts/`.
- **No new test file and no `tests/test_*.sh`.** All tests go into the four existing pytest files, so `.github/workflows/ci.yml` stays untouched (spec "Blast radius").
- **Expected blast-radius verdict:** `HUMAN_REQUIRED`, trigger `hotspot` (`run_record.py`, `model_proxy.py`). That hold is expected; do not try to avoid it.
- **Module-attribute calls.** `run_record.py` must call `rotation.open_regular(...)` and `rotation.locked(...)` through the module, and `rotation.py` must call `_write_gz` and `secrets.token_hex` through module globals. The tests monkeypatch those names.
- **Do not add row-type filtering to the reader.** `preview_helper`'s `test_forged_ledger_rows_never_raise` relies on `row.get(...)` raising for a non-dict row (for example `[1, 2]`), so `keep(row)` stays unguarded.
- Run tests with `PYTHONPATH=scripts python -m pytest <file> -v`.

## Review amendments (2026-11-27, plan review by execution)

The plan was executed task by task on the #645 plan end state (`7fe6ca7`) and its anchors
were also applied on plain `origin/main` (`d459e67`): every edit lands in both. Three changes
came out of that, all applied to the text below:

- **A1 (Task 2, a test that could not pass).** `test_oversized_gz_is_abandoned` counted the
  word "abandoned" in stderr. The log line prints the generation's path, pytest's `tmp_path`
  contains the test's own name, and the count was 2. It now counts the log prefix.
- **A2 (Task 2, R3 with the default threshold).** The decompressed-bytes bound alone lets one
  line fill it: a 5 MB forged `.gz` holding one 1 GiB line cost the reader 444 MiB of memory
  with the default 100 MiB threshold, a sparse 190 MiB plain generation 424 MiB, and the live
  file (no cap) grows with its size. `_ledger_lines` gets `MAX_LEDGER_LINE_BYTES` (1 MiB); a
  file with a longer line is abandoned. After: 47 MiB peak (the interpreter's own baseline) for each of the three.
- **A3 (Task 7, the spec's Goal for the poll).** `log_call` opened the live cost log with a
  plain `open(..., "a")`: a FIFO planted at `graphql-costs.jsonl` blocked the scheduler's
  board fetch for good and a link there was written through, before and after the plan as
  written. The live file is now opened `O_NOFOLLOW | O_NONBLOCK` and must be regular. This is
  one step past W7's letter (which names only the lock file) and is the same rule for the
  same reason; drop A3 if the operator wants the spec's letter only.

## File Structure

| File | Change | Rules |
|---|---|---|
| `scripts/factory_core/rotation.py` | **new**: generation names and listing, `open_regular`, bounded `locked`, `rotate_locked`, `store_size`, `compress_and_recover` (compress, swap, recovery pass) | W1–W8, W10; R1's pattern |
| `scripts/factory_core/model_proxy.py` | `BACKUP_COUNT` removed; `_ledger_locked()`; `append_ledger` rotates through `rotation` and returns the rotated generation; `compress_ledger_generation`; `_persist_ledger` submits compression as its own executor task | W1–W6, W8–W10 |
| `scripts/factory_core/cost_log.py` | sidecar lock with 1 s bounded wait; numbered rotation; inline compression after the lock is released; the live file is opened `O_NOFOLLOW \| O_NONBLOCK` and must be regular (`_append_line`) | W1–W8, W10; the spec's Goal |
| `scripts/factory_core/run_record.py` | `_iter_ledger_paths(ledger_path, since_epoch=None)`; `_open_ledger_snapshot`, `_ledger_lines`, `_read_ledger`; `_read_ledger_rows(..., since_epoch=None)`; `_started_epoch`; `cmd_assemble`, `_compute_harness_economics`, `_build_issue_economics` wired | R1–R6 |
| `scripts/factory_core/preview_helper.py` | `LEDGER_SCAN_WINDOW_SECONDS`; `ledger_files_ok(ledger_path, since_epoch=None)` also checks the lock file; `_read_ledger_rows_bounded(..., since_epoch=None)`; `record_outcome` passes `since_epoch` | R7 |
| `tests/test_run_record.py` | reader tests (criterion 3, R3–R6) | |
| `tests/test_factory_model_proxy.py` | two rotation tests rewritten / replaced; ledger rotation tests (criteria 1, 2, 4; W1–W6, W8–W10) | |
| `tests/test_cost_log.py` | `test_log_call_rotates_when_over_cap` rewritten; cost-log tests (criteria 1, 2, 4; W7, W8) | |
| `tests/test_preview_helper.py` | R7 tests | |
| `docs/configuration.md` | `MODEL_PROXY_BACKUP_COUNT` documented as ignored; new "Ledger and cost-log generations (#652)" subsection | criterion 5 |
| `docs/scheduler.md` | the `graphql-costs.jsonl` state-file row describes numbered, compressed generations | |

Test-name map (the spec's names are binding):

| Spec row | Test (file) | Task |
|---|---|---|
| Criterion 3 / R1, R2 | `test_temporary_file_is_not_a_generation`, `test_plain_preferred_when_both_forms_exist` (run_record) | 1 |
| Criterion 3 / R1, R2 | `test_reader_finds_rows_in_live_plain_and_gz`, `test_generations_older_than_the_run_are_not_opened` (run_record) | 2 |
| R3 | `test_oversized_gz_is_abandoned`, `test_a_line_over_the_cap_abandons_the_file` (run_record) | 2 |
| R4 | `test_append_is_not_blocked_while_a_reader_reads` (run_record) | 2 |
| R4 | `test_concurrent_read_is_consistent` (run_record) | 6 |
| R5 | `test_reader_does_not_create_the_lock_file`, `test_reader_proceeds_when_the_lock_is_held` (run_record) | 2 |
| R6 | `test_issue_economics_reads_gz_generations`, `test_backfill_read_is_bounded_by_started_at`, `test_unparseable_started_at_means_a_full_scan` (run_record) | 3 |
| W1 | `test_rotation_never_replaces_an_existing_generation_name` (model_proxy) | 5 |
| W2 | `test_rotation_killed_between_link_and_unlink_duplicates_no_row` (model_proxy) | 5 |
| W3 | `test_two_compressors_of_one_generation_never_share_a_temporary_file`, `test_compression_failure_is_logged_not_raised` (model_proxy) | 5 |
| W3 | `test_append_ledger_returns_before_compression_finishes` (model_proxy) | 6 |
| W4 | `test_existing_gz_is_never_replaced_by_a_second_compressor` (model_proxy) | 5 |
| W5 | `test_stalled_generation_is_compressed_by_the_next_rotation`, `test_recovery_compresses_at_most_two_generations_per_rotation`, `test_stale_temporary_file_is_removed_by_the_recovery_pass` (model_proxy) | 5 |
| W6 | `test_planted_fifo_under_a_generation_name_never_blocks` (model_proxy) | 5 |
| W7 | `test_log_call_returns_within_two_seconds_when_the_lock_is_held`, `test_log_call_with_a_fifo_as_lock_file_appends_and_returns` (cost_log); `test_log_call_fails_soft_on_unwritable_dir` (*guard*) | 7 |
| Goal (the poll never waits on a planted file) | `test_log_call_never_waits_on_or_writes_through_a_planted_live_file` (cost_log) | 7 |
| W8 | `test_old_numbered_generations_are_kept_compressed_and_read` (model_proxy; cost_log) | 6; 7 |
| W9 | `test_backup_count_variable_is_ignored` (model_proxy) | 5 |
| W10 | `test_rotation_logs_the_store_size` (model_proxy) | 4 |
| Criterion 1 | `test_rotation_keeps_every_generation` (model_proxy; cost_log) | 6; 7 |
| Criterion 2 | `test_concurrent_appends_through_rotations_lose_nothing` (model_proxy; cost_log) | 6; 7 |
| Criterion 4 | `test_append_is_not_blocked_by_a_slow_compression` (model_proxy; cost_log) | 6; 7 |
| R7 | `test_record_outcome_bounds_the_ledger_scan`, `test_ledger_files_ok_rejects_a_planted_lock_file` (preview_helper); `test_ledger_directory_oversized_or_bad_backup_is_unavailable` (*guard*) | 8 |
| #634 guards | `test_append_ledger_lock_follows_the_ledger_path`, `test_ledger_persistence_does_not_block_event_loop` (model_proxy, unmodified) | 4, 6, 10 |
| Kept guard | `test_read_ledger_rows_includes_rotation_backups` (run_record, unmodified) | 1, 2 |

---

## Task 0: Sync with `main` and re-check anchors

**Files:** none modified.

- [ ] **Step 1: Rebase onto main (it carries #645 once that lands)**

```bash
git fetch origin main && git rebase origin/main
```
Expected: clean rebase. On a conflict in `model_proxy.py` or `run_record.py`, keep both sides: #645's row fields are orthogonal to rotation.

- [ ] **Step 2: Re-check the anchors this plan edits**

```bash
grep -n "BACKUP_COUNT\|def _rotate_if_needed\|def _ledger_lock_path\|def append_ledger\|async def _persist_ledger\|run_in_executor" scripts/factory_core/model_proxy.py
grep -n "def _iter_ledger_paths\|def _read_ledger_rows\|_read_ledger_rows(\|def _build_issue_economics\|for path in _iter_ledger_paths\|^from \. import" scripts/factory_core/run_record.py
grep -n "def ledger_files_ok\|def _read_ledger_rows_bounded\|def record_outcome\|LEDGER_READ_TIMEOUT_SECONDS =" scripts/factory_core/preview_helper.py
grep -n "ROTATE_BYTES\|path.replace" scripts/factory_core/cost_log.py
```
Expected: every name is found at least once. For example, `BACKUP_COUNT` appears on its definition and in `_rotate_if_needed`, `run_in_executor` twice in `_persist_ledger`, and `_read_ledger_rows(` on its `def` and at the `cmd_assemble` and `_compute_harness_economics` call sites. If #645 moved a function, apply each task's edit to the function by name, not by line.

- [ ] **Step 3: Baseline**

```bash
PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py tests/test_cost_log.py tests/test_run_record.py tests/test_preview_helper.py -q
```
Expected: all pass. Record the count.

---

## Task 1: `rotation.py` names, safe open, bounded lock; reader helper R1/R2

**Files:**
- Create: `scripts/factory_core/rotation.py`
- Modify: `scripts/factory_core/run_record.py` (imports; `_iter_ledger_paths`)
- Test: `tests/test_run_record.py`

- [ ] **Step 1: Write the failing tests** — append to the `_read_ledger_rows` section of `tests/test_run_record.py`, right after `test_read_ledger_rows_skips_malformed_lines`. Add `import gzip` to the imports at the top of the file.

```python
def _write_generation(directory, name, rows, mtime=None):
    """A ledger generation file: gzip-compressed when the name says .gz (or .gz.tmp.*)."""
    path = directory / name
    data = "".join(json.dumps(r) + "\n" for r in rows).encode()
    if name.endswith(".gz") or ".gz.tmp." in name:
        data = gzip.compress(data)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_temporary_file_is_not_a_generation(tmp_path):
    ledger = tmp_path / "request-ledger.jsonl"
    ledger.write_text(json.dumps(_ledger_row("run-1", in_tok=1)) + "\n")
    gz = _write_generation(tmp_path, "request-ledger.jsonl.1.gz", [_ledger_row("run-1", in_tok=2)])
    _write_generation(tmp_path, "request-ledger.jsonl.2.gz.tmp.0123456789abcdef",
                      [_ledger_row("run-1", in_tok=9)])
    assert list(rr._iter_ledger_paths(ledger)) == [ledger, gz]


def test_plain_preferred_when_both_forms_exist(tmp_path):
    ledger = tmp_path / "request-ledger.jsonl"
    ledger.write_text(json.dumps(_ledger_row("run-1", in_tok=1)) + "\n")
    plain = _write_generation(tmp_path, "request-ledger.jsonl.1", [_ledger_row("run-1", in_tok=2)])
    _write_generation(tmp_path, "request-ledger.jsonl.1.gz",
                      [_ledger_row("run-1", in_tok=2), _ledger_row("run-1", in_tok=7)])
    assert list(rr._iter_ledger_paths(ledger)) == [ledger, plain]
    rows, _ = rr._read_ledger_rows("run-1", ledger)
    assert [r["gen_ai.usage.input_tokens"] for r in rows] == [1, 2]
```

- [ ] **Step 2: Run them — verify they fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_run_record.py -k "temporary_file_is_not_a_generation or plain_preferred_when_both_forms_exist" -v
```
Expected: `test_temporary_file_is_not_a_generation` FAILS. The current helper lists only `.1` … `.BACKUP_COUNT` by name, so it never yields `.1.gz` and returns `[ledger]`. `test_plain_preferred_when_both_forms_exist` PASSES already, because the old helper also yields only the plain `.1`. It is a regression pin for R2 from here on.

- [ ] **Step 3: Create `scripts/factory_core/rotation.py`**

```python
"""Numbered, non-deleting rotation for the state directory's JSONL stores (#652).

Shared by the model-proxy request ledger (model_proxy.py) and the GraphQL cost log
(cost_log.py); run_record.py reads ledger generations through generations() and
open_regular(). For a live file <name>:

    <name>.lock                  sidecar lock; never rotated, renamed or removed
    <name>.<N>                   plain generation N (decimal, no leading zero)
    <name>.<N>.gz                compressed generation N; never rewritten, renamed or removed
    <name>.<N>.gz.tmp.<16 hex>   a compression in progress

The state directory is a volume every gated run container mounts read-write, so any of
these names can be planted: generations are only opened O_NOFOLLOW | O_NONBLOCK and must be
regular files of bounded size, and no lock taken here is waited on without bound. Imports
nothing from providers. Spec: docs/superpowers/specs/2026-11-27-durable-factory-stats-design.md.
"""
import contextlib
import fcntl
import os
import re
import stat
import sys
import time
from pathlib import Path

# A generation is recognised by its whole suffix after <name>; a temporary file matches neither.
_GENERATION_RE = re.compile(r"\.([1-9][0-9]*)(\.gz)?")
_TEMPORARY_RE = re.compile(r"\.[1-9][0-9]*\.gz\.tmp\.[0-9a-f]{16}")
_LOCK_POLL_SECONDS = 0.01


def log(message: str) -> None:
    print(f"factory-rotation: {message}", file=sys.stderr, flush=True)


def lock_path(path) -> Path:
    """<name>.lock beside the live file: the one lock for rotation, append and the swap."""
    path = Path(path)
    return path.with_name(path.name + ".lock")


def _names(path: Path) -> list:
    try:
        return os.listdir(path.parent)
    except OSError:
        return []


def generations(path) -> dict:
    """{N: {"plain": Path, "gz": Path}} (either key may be absent) for every entry beside the
    live file whose whole suffix after its name is .N or .N.gz. An unlistable directory reads
    as no generations. Whatever sits at a name (a FIFO, a link, a directory) is listed; the
    opener refuses it (open_regular)."""
    path = Path(path)
    found = {}
    for entry in _names(path):
        if not entry.startswith(path.name):
            continue
        match = _GENERATION_RE.fullmatch(entry[len(path.name):])
        if match:
            form = "gz" if match.group(2) else "plain"
            found.setdefault(int(match.group(1)), {})[form] = path.with_name(entry)
    return found


def temporaries(path) -> list:
    """Every <name>.<N>.gz.tmp.<16 hex> beside the live file."""
    path = Path(path)
    return [path.with_name(entry) for entry in _names(path)
            if entry.startswith(path.name) and _TEMPORARY_RE.fullmatch(entry[len(path.name):])]


def open_regular(path, max_bytes=None):
    """(fd, os.stat_result) for path opened O_RDONLY | O_NOFOLLOW | O_NONBLOCK when it is a
    regular file of at most max_bytes bytes; None otherwise, with one log line unless the name
    is simply absent. A FIFO opens without blocking and is then refused; a link is refused by
    O_NOFOLLOW. The caller closes the descriptor."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None
    except OSError as exc:
        log(f"skipped {path}: {exc.strerror or exc}")
        return None
    try:
        info = os.fstat(fd)
    except OSError as exc:
        os.close(fd)
        log(f"skipped {path}: {exc.strerror or exc}")
        return None
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        log(f"skipped {path}: not a regular file")
        return None
    if max_bytes is not None and info.st_size > max_bytes:
        os.close(fd)
        log(f"skipped {path}: {info.st_size} bytes is over {max_bytes}")
        return None
    return fd, info


@contextlib.contextmanager
def locked(lock_file, *, timeout, shared=False, create=True):
    """Yield True while flock (LOCK_EX, or LOCK_SH when shared) is held on lock_file, False
    when it was not taken within `timeout` seconds. The lock file is opened
    O_NOFOLLOW | O_NONBLOCK (O_CREAT only when create) and must be a regular file; anything
    else (absent with create=False, a FIFO, a link, an unopenable path) yields False at once.
    Never raises for a lock it cannot take and never waits past the deadline."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    if create:
        flags |= os.O_CREAT
    fd = None
    held = False
    try:
        fd = os.open(lock_file, flags, 0o666)
        if stat.S_ISREG(os.fstat(fd).st_mode):
            operation = (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, operation)
                    held = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(_LOCK_POLL_SECONDS)
    except OSError:
        pass
    try:
        yield held
    finally:
        if fd is not None:
            os.close(fd)  # closing the descriptor releases the flock
```

- [ ] **Step 4: Point `_iter_ledger_paths` at it.** In `scripts/factory_core/run_record.py`, add `from . import rotation` directly below `from . import model_proxy`. Then replace the whole `_iter_ledger_paths` function with:

```python
def _iter_ledger_paths(ledger_path: pathlib.Path, since_epoch: "float | None" = None):
    """The live ledger, then its generations by descending number (#652 R1, R2).

    Generations are found by whole suffix (rotation.generations): `.N` and `.N.gz`, never a
    `.N.gz.tmp.*` compression in progress. When both forms of one number exist only the plain
    file is yielded. With since_epoch, a generation last modified (lstat) before it is not
    yielded, because none of its rows can be newer. The live file is always yielded, present
    or not. This is the one reader-side source of generation paths:
    preview_helper.ledger_files_ok and every ledger reader here use it, and it is importable
    for scripts/reconcile_cost_reports.py, which still reads only the live ledger (#652 R8)."""
    ledger_path = pathlib.Path(ledger_path)
    yield ledger_path
    found = rotation.generations(ledger_path)
    for number in sorted(found, reverse=True):
        path = found[number].get("plain") or found[number]["gz"]
        if since_epoch is not None:
            try:
                if os.lstat(path).st_mtime < since_epoch:
                    continue
            except OSError:
                continue
        yield path
```

- [ ] **Step 5: Run — verify pass, and the kept guard**

```bash
PYTHONPATH=scripts python -m pytest tests/test_run_record.py -k "temporary_file_is_not_a_generation or plain_preferred_when_both_forms_exist or read_ledger_rows" -v
```
Expected: all PASS, including `test_read_ledger_rows_includes_rotation_backups` (unmodified guard: a plain `.1` is still a generation).

- [ ] **Step 6: Commit**

```bash
git add scripts/factory_core/rotation.py scripts/factory_core/run_record.py tests/test_run_record.py
git commit -m "feat(#652): rotation module names and the one ledger generation helper (R1, R2)"
```

---

## Task 2: Reader core — streamed `.gz`, list-and-open under `LOCK_SH`, no-lock fallback (R3, R4, R5)

**Files:**
- Modify: `scripts/factory_core/run_record.py` (imports; `_read_ledger_rows` and new helpers above it)
- Test: `tests/test_run_record.py`

- [ ] **Step 1: Write the failing tests** — append after the Task 1 tests. Add `import fcntl`, `import threading`, `import time` to the imports at the top of the file.

```python
def test_reader_finds_rows_in_live_plain_and_gz(tmp_path):
    ledger = tmp_path / "request-ledger.jsonl"
    ledger.write_text(json.dumps(_ledger_row("run-1", in_tok=1)) + "\n")
    _write_generation(tmp_path, "request-ledger.jsonl.3", [_ledger_row("run-1", in_tok=2)])
    _write_generation(tmp_path, "request-ledger.jsonl.2.gz", [_ledger_row("run-1", in_tok=3)])
    _write_generation(tmp_path, "request-ledger.jsonl.1.gz", [_ledger_row("run-1", in_tok=4),
                                                              _ledger_row("run-2", in_tok=99)])
    rows, available = rr._read_ledger_rows("run-1", ledger)
    assert available is True
    assert [r["gen_ai.usage.input_tokens"] for r in rows] == [1, 2, 3, 4]


def test_generations_older_than_the_run_are_not_opened(tmp_path, monkeypatch):
    from factory_core import rotation
    ledger = tmp_path / "request-ledger.jsonl"
    ledger.write_text(json.dumps(_ledger_row("run-1", in_tok=1)) + "\n")
    started = time.time() - 600
    old = _write_generation(tmp_path, "request-ledger.jsonl.1.gz", [_ledger_row("run-1", in_tok=2)],
                            mtime=started - 3600)
    _write_generation(tmp_path, "request-ledger.jsonl.2", [_ledger_row("run-1", in_tok=3)],
                      mtime=started + 60)
    opened = []
    original = rotation.open_regular
    monkeypatch.setattr(rotation, "open_regular",
                        lambda path, *a, **k: opened.append(Path(path)) or original(path, *a, **k))
    rows, _ = rr._read_ledger_rows("run-1", ledger, since_epoch=started)
    assert sorted(r["gen_ai.usage.input_tokens"] for r in rows) == [1, 3]
    assert old not in opened


def test_oversized_gz_is_abandoned(tmp_path, monkeypatch, capsys):
    import tracemalloc
    from factory_core import model_proxy as mp
    monkeypatch.setattr(mp, "MAX_LEDGER_BYTES", 100_000)  # bound: 200,000 decompressed bytes
    ledger = tmp_path / "request-ledger.jsonl"
    ledger.write_text(json.dumps(_ledger_row("run-1", in_tok=1)) + "\n")
    _write_generation(tmp_path, "request-ledger.jsonl.2", [_ledger_row("run-1", in_tok=2)])
    forged = tmp_path / "request-ledger.jsonl.1.gz"
    with gzip.open(forged, "wb") as fh:
        fh.write((json.dumps(_ledger_row("run-1", in_tok=3)) + "\n").encode())
        chunk = b"0" * (1 << 20)
        for _ in range(50):  # 50 MiB in one line, about 50 KB compressed
            fh.write(chunk)
    assert forged.stat().st_size < 200_000  # passes the open-time size check: R3 must stop it
    capsys.readouterr()
    tracemalloc.start()
    try:
        rows, available = rr._read_ledger_rows("run-1", ledger)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert available is True
    assert sorted(r["gen_ai.usage.input_tokens"] for r in rows) == [1, 2]  # the forged one adds nothing
    assert peak < 5 * 1024 * 1024
    assert capsys.readouterr().err.count("factory-rotation: abandoned ") == 1


@pytest.mark.parametrize("where", ["gz", "plain", "live"])
def test_a_line_over_the_cap_abandons_the_file(tmp_path, monkeypatch, capsys, where):
    # R3 with the DEFAULT threshold: the decompressed-bytes bound alone is 200 MiB, and one
    # line may fill it. The line cap keeps the reader's memory flat whatever the threshold.
    import tracemalloc
    monkeypatch.setattr(rr, "MAX_LEDGER_LINE_BYTES", 64 * 1024)
    ledger = tmp_path / "request-ledger.jsonl"
    good = (json.dumps(_ledger_row("run-1", in_tok=1)) + "\n").encode()
    long_line = good + b"0" * (8 * 1024 * 1024)  # one valid row, then 8 MiB with no newline
    if where == "live":
        ledger.write_bytes(long_line)
        _write_generation(tmp_path, "request-ledger.jsonl.1", [_ledger_row("run-1", in_tok=2)])
    else:
        ledger.write_bytes(good)
        name = "request-ledger.jsonl.1" + (".gz" if where == "gz" else "")
        (tmp_path / name).write_bytes(gzip.compress(long_line) if where == "gz" else long_line)
        _write_generation(tmp_path, "request-ledger.jsonl.2", [_ledger_row("run-1", in_tok=2)])
    capsys.readouterr()
    tracemalloc.start()
    try:
        rows, available = rr._read_ledger_rows("run-1", ledger)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert available is True
    # The file with the long line is read whole or not at all; the other file still counts.
    assert [r["gen_ai.usage.input_tokens"] for r in rows] == ([2] if where == "live" else [1, 2])
    assert peak < 2 * 1024 * 1024
    assert capsys.readouterr().err.count("factory-rotation: abandoned ") == 1


def test_append_is_not_blocked_while_a_reader_reads(tmp_path, monkeypatch):
    from factory_core import model_proxy as mp
    ledger = tmp_path / "request-ledger.jsonl"
    monkeypatch.setattr(mp, "LEDGER_PATH", ledger)
    mp.append_ledger(_ledger_row("run-1"))  # also creates request-ledger.jsonl.lock
    reading, release = threading.Event(), threading.Event()
    original = rr._ledger_lines

    def slow_lines(*args):
        reading.set()
        release.wait(10)
        yield from original(*args)

    monkeypatch.setattr(rr, "_ledger_lines", slow_lines)
    result = {}
    reader = threading.Thread(target=lambda: result.update(read=rr._read_ledger_rows("run-1", ledger)))
    reader.start()
    try:
        assert reading.wait(5)
        writer = threading.Thread(target=mp.append_ledger, args=(_ledger_row("run-1"),))
        started = time.monotonic()
        writer.start()
        writer.join(2)
        elapsed = time.monotonic() - started
    finally:
        release.set()
        reader.join(10)
    assert not writer.is_alive() and elapsed < 1.5  # it would wait the full 10 s if the read held the lock
    assert len(result["read"][0]) == 1  # the snapshot predates the second append


def test_reader_does_not_create_the_lock_file(tmp_path):
    ledger = tmp_path / "request-ledger.jsonl"
    ledger.write_text(json.dumps(_ledger_row("run-1")) + "\n")
    rows, available = rr._read_ledger_rows("run-1", ledger)
    assert (len(rows), available) == (1, True)
    assert not (tmp_path / "request-ledger.jsonl.lock").exists()


def test_reader_proceeds_when_the_lock_is_held(tmp_path):
    ledger = tmp_path / "request-ledger.jsonl"
    ledger.write_text(json.dumps(_ledger_row("run-1")) + "\n")
    expected = rr._read_ledger_rows("run-1", ledger)
    with open(tmp_path / "request-ledger.jsonl.lock", "a") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        started = time.monotonic()
        result = rr._read_ledger_rows("run-1", ledger)
        elapsed = time.monotonic() - started
    assert result == expected
    assert elapsed < 3
```

- [ ] **Step 2: Run — verify they fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_run_record.py -k "reader_finds_rows or older_than_the_run or oversized_gz or line_over_the_cap or not_blocked_while_a_reader or does_not_create_the_lock or proceeds_when_the_lock" -v
```
Expected: FAIL. The current reader `read_text`s a `.gz` (`UnicodeDecodeError`), has no `since_epoch` parameter (`TypeError`), and has no `_ledger_lines` (`AttributeError`). The two R5 tests may pass already; that is fine. (Observed on the #645 end state: four fail, the two R5 tests pass; with the line-cap test added, its three cases fail too.)

- [ ] **Step 3: Implement.** In `scripts/factory_core/run_record.py`, add `import gzip` and `import zlib` to the stdlib imports (keep alphabetical order). Then replace the whole `_read_ledger_rows` function with the code below.

The live file gets W6's opening rule (`O_NOFOLLOW | O_NONBLOCK`, regular file required) but no size cap: it is read only up to the size noted under the lock, and W6's twice-the-threshold cap is a rule about generations. The preview helper still caps the live file at `MAX_LEDGER_FILE_BYTES` through `ledger_files_ok`.

```python
READ_LOCK_WAIT_SECONDS = 2.0
# The longest ledger line the reader will hold (#652 R3). model_proxy's rows are a few
# kilobytes; a file with a longer line is abandoned, like an archive over its bound.
MAX_LEDGER_LINE_BYTES = 1024 * 1024


class _AbandonedGeneration(Exception):
    """A .gz generation decompressed past its bound (#652 R3)."""


def _generation_bound() -> int:
    # A real generation is at most the rotation threshold plus one row (#652 R3, W6).
    return 2 * model_proxy.MAX_LEDGER_BYTES


def _open_ledger_snapshot(ledger_path: pathlib.Path, since_epoch) -> list:
    """[(path, fd, size_limit, is_gz)] for every file the read will use (#652 R4, R5).

    Listing and opening happen under LOCK_SH on <ledger>.lock, which is opened without O_CREAT
    and given up after READ_LOCK_WAIT_SECONDS. In that case (R5) the read proceeds unlocked
    with the same result shape. The live file's size is noted under the lock and the read
    stops there. An open descriptor survives the rename and the removal of a plain
    generation, so the set is a consistent snapshot and the read itself holds no writer back.
    A generation on the live file's own inode (a rotation interrupted between link and
    unlink, W2) is not read twice."""
    opened, seen = [], set()
    try:
        with rotation.locked(rotation.lock_path(ledger_path), timeout=READ_LOCK_WAIT_SECONDS,
                             shared=True, create=False):
            for index, path in enumerate(_iter_ledger_paths(ledger_path, since_epoch)):
                is_live = index == 0
                result = rotation.open_regular(path, None if is_live else _generation_bound())
                if result is None:
                    continue
                fd, info = result
                if (info.st_dev, info.st_ino) in seen:
                    os.close(fd)
                    continue
                seen.add((info.st_dev, info.st_ino))
                opened.append((path, fd, info.st_size if is_live else None,
                               path.name.endswith(".gz")))
    except BaseException:
        for entry in opened:
            os.close(entry[1])
        raise
    return opened


def _ledger_lines(path, fd, size_limit, is_gz):
    """The lines of one opened ledger file. A plain file is read from its descriptor; the live
    file only up to size_limit. A .gz is decompressed as a stream, never loaded whole, and
    abandoned once more than _generation_bound() decompressed bytes have been read (R3), so
    a forged archive cannot exhaust memory."""
    def capped(line):
        # A row is a few kilobytes. A "line" that fills the cap without ending is not a row:
        # reading on would hold up to the whole file in memory (one 1 GiB line in a 5 MB
        # forged .gz cost 444 MiB with the default threshold before this cap).
        if len(line) > MAX_LEDGER_LINE_BYTES:
            raise _AbandonedGeneration(f"a line over {MAX_LEDGER_LINE_BYTES} bytes")
        return line

    with os.fdopen(fd, "rb", closefd=False) as raw:
        if not is_gz:
            remaining = size_limit
            while remaining is None or remaining > 0:
                step = MAX_LEDGER_LINE_BYTES + 1
                line = raw.readline(step if remaining is None else min(step, remaining))
                if not line:
                    return
                if remaining is not None:
                    remaining -= len(line)
                yield capped(line)
            return
        bound = _generation_bound()
        budget = bound
        with gzip.GzipFile(fileobj=raw, mode="rb") as stream:
            while True:
                line = stream.readline(min(budget, MAX_LEDGER_LINE_BYTES) + 1)
                if not line:
                    return
                budget -= len(line)
                if budget < 0:
                    raise _AbandonedGeneration(f"over {bound} decompressed bytes")
                yield capped(line)


def _read_ledger(ledger_path, since_epoch, keep) -> tuple:
    """(rows for which keep(row) is true, ledger_available) over the live ledger and every
    generation _iter_ledger_paths yields. ledger_available is True when at least one file was
    opened. A generation is read whole or not at all: an unreadable or abandoned one
    contributes no row and logs one line. keep() is deliberately unguarded: a forged row it
    raises on propagates, as before #652 (preview_helper.record_outcome relies on that)."""
    opened = _open_ledger_snapshot(pathlib.Path(ledger_path), since_epoch)
    rows = []
    try:
        for path, fd, size_limit, is_gz in opened:
            found = []
            try:
                for line in _ledger_lines(path, fd, size_limit, is_gz):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if keep(row):
                        found.append(row)
            except (_AbandonedGeneration, OSError, EOFError, zlib.error) as exc:
                rotation.log(f"abandoned {path}: {exc}")
                continue
            rows.extend(found)
    finally:
        for _, fd, _, _ in opened:
            os.close(fd)
    return rows, bool(opened)


def _read_ledger_rows(run_id: str, ledger_path: pathlib.Path,
                      since_epoch: "float | None" = None) -> tuple:
    """Scan the live ledger plus every rotated generation for rows matching run_id.

    Returns (rows, ledger_available). ledger_available distinguishes "the ledger exists and
    genuinely has zero rows for this run" from "no ledger data at all" (the opt-in
    model-proxy was never enabled). See the spec's "Graceful degradation" section. With
    since_epoch (the run's start), generations last modified before it are not opened
    (#652 R1, R6)."""
    return _read_ledger(ledger_path, since_epoch, lambda row: row.get("run_id") == run_id)
```

- [ ] **Step 4: Run — verify pass, plus the whole run_record file**

```bash
PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v
```
Expected: all PASS, including the guards `test_read_ledger_rows_missing_file_not_available`, `test_read_ledger_rows_includes_rotation_backups` and `test_read_ledger_rows_skips_malformed_lines`.

- [ ] **Step 5: Preview-helper guard still green** (it calls the shared reader)

```bash
PYTHONPATH=scripts python -m pytest tests/test_preview_helper.py -v
```
Expected: all PASS. In particular, `test_forged_ledger_rows_never_raise` still gets `ledger_unavailable`, because `keep()` still raises on `[1, 2]`.

- [ ] **Step 6: Commit**

```bash
git add scripts/factory_core/run_record.py tests/test_run_record.py
git commit -m "feat(#652): ledger reader streams .gz with a bound and lists/opens under a shared lock (R3-R5)"
```

---

## Task 3: All readers on the helper (R6)

**Files:**
- Modify: `scripts/factory_core/run_record.py` (`_started_epoch` new; `_compute_harness_economics`; `cmd_assemble`; `_build_issue_economics`)
- Test: `tests/test_run_record.py`

- [ ] **Step 1: Write the failing tests** — append to the `backfill-economics` section (after `test_backfill_economics_missing_run_record_skipped`). Add `from datetime import datetime, timezone` to the imports.

```python
def test_issue_economics_reads_gz_generations(tmp_path):
    ledger = tmp_path / "request-ledger.jsonl"
    row = {"issue_number": 235, "intent": "implement", "stage": "implement", "status": 200,
           "gen_ai.usage.input_tokens": 1, "gen_ai.usage.output_tokens": 1}
    ledger.write_text(json.dumps(dict(row, run_id="run-1")) + "\n")
    _write_generation(tmp_path, "request-ledger.jsonl.1.gz", [dict(row, run_id="run-2")])
    result = rr._build_issue_economics(235, ledger_path=ledger, artifacts_root=tmp_path / "runs")
    assert set(result["runs"]) == {"run-1", "run-2"}


def _backfill_with_generations(tmp_path, started_at):
    """A retained run-1 record plus a ledger: one live row, three rows in a generation from an
    hour before 2026-06-12T04:00:00Z, one row in a generation from a minute after it."""
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "run-record.json").write_text(json.dumps({
        "run_id": "run-1", "status": "completed",
        "started_at": started_at, "completed_at": "2026-06-12T04:01:00Z",
        "stages": [], "totals": {"gen_ai.usage.input_tokens": 1, "gen_ai.usage.output_tokens": 1},
    }))
    epoch = datetime(2026, 6, 12, 4, 0, 0, tzinfo=timezone.utc).timestamp()
    ledger = tmp_path / "request-ledger.jsonl"
    ledger.write_text(json.dumps(_ledger_row("run-1")) + "\n")
    _write_generation(tmp_path, "request-ledger.jsonl.1", [_ledger_row("run-1")] * 3, mtime=epoch - 3600)
    _write_generation(tmp_path, "request-ledger.jsonl.2", [_ledger_row("run-1")], mtime=epoch + 60)
    assert rr._backfill_run_economics("run-1", artifacts_root=tmp_path / "runs", ledger_path=ledger)
    return json.loads((run_dir / "run-record.json").read_text())["harness_economics"]


def test_backfill_read_is_bounded_by_started_at(tmp_path):
    economics = _backfill_with_generations(tmp_path, "2026-06-12T04:00:00Z")
    assert economics["ledger_rows_correlated"] == 2  # live + .2; .1 ended before the run began


def test_unparseable_started_at_means_a_full_scan(tmp_path):
    economics = _backfill_with_generations(tmp_path, "not-a-timestamp")
    assert economics["ledger_rows_correlated"] == 5


def test_assemble_read_is_bounded_by_started_at(tmp_path, monkeypatch):
    # cmd_assemble passes the run's started_at (2026-06-12T04:00:00Z in _AssembleArgs) (R6).
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    ledger = tmp_path / "request-ledger.jsonl"
    monkeypatch.setattr(rr, "LEDGER_PATH", ledger)
    epoch = datetime(2026, 6, 12, 4, 0, 0, tzinfo=timezone.utc).timestamp()
    ledger.write_text(json.dumps(_ledger_row("abc123")) + "\n")
    _write_generation(tmp_path, "request-ledger.jsonl.1", [_ledger_row("abc123")] * 3, mtime=epoch - 3600)
    out = tmp_path / "run-record.json"
    rr.cmd_assemble(_AssembleArgs(tmp_path, out))
    assert json.loads(out.read_text())["harness_economics"]["ledger_rows_correlated"] == 1
```

- [ ] **Step 2: Run — verify they fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_run_record.py -k "issue_economics_reads_gz or bounded_by_started_at or unparseable_started_at" -v
```
Expected: `test_issue_economics_reads_gz_generations` FAILS (`read_text` on a `.gz`: `UnicodeDecodeError`), the two `bounded_by_started_at` tests FAIL (5 rows / 4 rows), and `test_unparseable_started_at_means_a_full_scan` PASSES already.

- [ ] **Step 3: Implement.** In `scripts/factory_core/run_record.py`:

(a) Add directly below `_wall_clock_seconds`:

```python
def _started_epoch(started_at) -> "float | None":
    """started_at ("%Y-%m-%dT%H:%M:%SZ", UTC) as epoch seconds; None (a full ledger scan) when it
    does not parse (#652 R6)."""
    try:
        return datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except (ValueError, TypeError):
        return None
```

(b) In `_compute_harness_economics`, replace the line

```python
    rows, ledger_available = ledger if ledger is not None else _read_ledger_rows(run_id, ledger_path)
```
with

```python
    rows, ledger_available = ledger if ledger is not None else _read_ledger_rows(
        run_id, ledger_path, since_epoch=_started_epoch(started_at))
```

(c) In `cmd_assemble`, replace

```python
    ledger_rows, ledger_available = _read_ledger_rows(args.run_id, ledger_path)
```
with

```python
    ledger_rows, ledger_available = _read_ledger_rows(
        args.run_id, ledger_path, since_epoch=_started_epoch(args.started_at))
```

(d) In `_build_issue_economics`, replace the whole `for path in _iter_ledger_paths(ledger_path): ... except OSError: continue` loop (the block that builds `runs`) with:

```python
    # The full-history reader (#652 R6): no since_epoch, the same lock rule, .gz through R3.
    rows, _ = _read_ledger(ledger_path, None,
                           lambda row: int(row.get("issue_number") or 0) == issue_number)
    for row in rows:
        run_id = row.get("run_id", "unknown")
        bucket = runs.setdefault(run_id, {
            "intent": row.get("intent", "unknown"),
            "stage": row.get("stage", "unknown"),
            "rows": [],
        })
        bucket["rows"].append(row)
```
Keep `runs: dict = {}` above it and everything from `result_runs = {}` on unchanged.

- [ ] **Step 4: Update the one existing test that stubs the reader by position.** `test_assemble_reads_the_ledger_once` in `tests/test_run_record.py` replaces `_read_ledger_rows` with a two-parameter lambda. `cmd_assemble` now passes `since_epoch=` as a keyword, so that lambda would raise `TypeError`. It is not one of the spec's *guard* tests. Change only its `monkeypatch.setattr` line, from

```python
    monkeypatch.setattr(rr, "_read_ledger_rows", lambda run_id, path: reads.append(run_id) or real(run_id, path))
```
to

```python
    monkeypatch.setattr(rr, "_read_ledger_rows",
                        lambda run_id, path, **kw: reads.append(run_id) or real(run_id, path, **kw))
```
The assertion `reads == ["abc123"]` (the ledger is read once) is unchanged.

- [ ] **Step 5: Run — verify pass**

```bash
PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v
```
Expected: all PASS, including `test_assemble_reads_the_ledger_once`, `test_build_issue_economics_groups_by_run_issue_phase`, `test_backfill_economics_full_recompute` and `test_backfill_economics_ledger_rotated_away_degrades`. The live file is always read, so the existing fixtures with past `started_at` values keep their rows.

- [ ] **Step 6: Commit**

```bash
git add scripts/factory_core/run_record.py tests/test_run_record.py
git commit -m "feat(#652): assemble, backfill and issue-economics read every generation (R6)"
```

---

## Task 4: Ledger writer — numbered rotation, completion of an interrupted one, size log; `BACKUP_COUNT` gone (W1, W2, W10; W9's code)

**Files:**
- Modify: `scripts/factory_core/rotation.py` (add `store_size`, `rotate_locked`)
- Modify: `scripts/factory_core/model_proxy.py` (imports; `BACKUP_COUNT`; `_rotate_if_needed` removed; `_ledger_locked`; `append_ledger`)
- Test: `tests/test_factory_model_proxy.py`

- [ ] **Step 1: Write the failing tests.** In `tests/test_factory_model_proxy.py`:

Add `import gzip`, `import re`, `import signal`, `import stat`, `import threading` to the imports, and below the existing `from factory_core import run_record as rr` line add:

```python
from factory_core import rotation  # noqa: E402

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
```

Replace `test_append_ledger_rotates_at_max_bytes` and delete `test_append_ledger_rotation_keeps_backup_count` (it asserts that a generation is gone, which is the behaviour being removed). Put the following in their place:

```python
def _small_ledger(tmp_path, monkeypatch, max_bytes=200):
    path = tmp_path / "request-ledger.jsonl"
    monkeypatch.setattr(mp, "LEDGER_PATH", path)
    monkeypatch.setattr(mp, "MAX_LEDGER_BYTES", max_bytes)
    return path


def _over_threshold(path, rows=3, run_id="run-1"):
    """A live ledger already over a 200-byte threshold (and under twice it)."""
    path.write_text("".join(json.dumps({"run_id": run_id, "old": i, "pad": "x" * 80}) + "\n"
                            for i in range(rows)))


def _append_and_compress(row):
    """What the proxy does for one row: append (rotating when due), then compress."""
    created = mp.append_ledger(row)
    if created is not None:
        mp.compress_ledger_generation(created)
    return created


def _names(directory):
    return sorted(p.name for p in directory.iterdir())


def _generation_bytes(path, number):
    plain = path.with_name(f"{path.name}.{number}")
    if plain.exists():
        return plain.read_bytes()
    return gzip.decompress(path.with_name(f"{path.name}.{number}.gz").read_bytes())


@pytest.fixture
def deadline():
    """Fail, never hang, when an open blocks (a FIFO opened without O_NONBLOCK)."""
    def _blocked(*_):
        raise TimeoutError("blocked for 10 s")
    old = signal.signal(signal.SIGALRM, _blocked)
    signal.alarm(10)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, old)


def test_append_ledger_rotates_at_max_bytes(tmp_path, monkeypatch):
    path = _small_ledger(tmp_path, monkeypatch, max_bytes=50)
    created = [mp.append_ledger({"i": i, "pad": "x" * 20}) for i in range(10)]
    assert path.exists()
    # Numbered and never capped (#652): each rotation takes the next number, nothing is deleted.
    numbers = [n for n in created if n is not None]
    assert len(numbers) >= 3 and numbers == list(range(1, len(numbers) + 1))
    assert all((tmp_path / f"request-ledger.jsonl.{n}").exists() for n in numbers)


def test_rotation_logs_the_store_size(tmp_path, monkeypatch, capsys):
    path = _small_ledger(tmp_path, monkeypatch)
    old = tmp_path / "request-ledger.jsonl.1.gz"
    old.write_bytes(gzip.compress(b"{}\n"))
    _over_threshold(path)
    expected = path.stat().st_size + old.stat().st_size
    capsys.readouterr()
    assert mp.append_ledger({"run_id": "run-1"}) == 2
    assert (f"rotated request-ledger.jsonl to generation 2; store is {expected} bytes"
            in capsys.readouterr().err)
```

- [ ] **Step 2: Run — verify they fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py -k "rotates_at_max_bytes or logs_the_store_size" -v
```
Expected: FAIL (`append_ledger` returns `None` and logs nothing).

- [ ] **Step 3: Add `store_size` and `rotate_locked` to `rotation.py`** (after `open_regular`):

```python
def store_size(path) -> int:
    """Bytes in the live file plus every generation in either form (W10)."""
    path = Path(path)
    total = 0
    for candidate in [path, *(p for forms in generations(path).values() for p in forms.values())]:
        try:
            total += os.lstat(candidate).st_size
        except OSError:
            pass
    return total


def rotate_locked(path, threshold) -> "int | None":
    """Rotate the live file when due (W1, W2, W10). The caller holds LOCK_EX on lock_path(path).

    Returns the number of the generation that now needs compressing (compress_and_recover,
    after the lock is released), or None. Lists the directory only when a rotation is due or
    the live file has a second link. Never raises: a failure is one log line, and the caller
    still appends to the live file, which then keeps growing."""
    path = Path(path)
    try:
        try:
            live = os.lstat(path)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(live.st_mode):
            log(f"not rotating {path}: not a regular file")
            return None
        found = None
        if live.st_nlink > 1:
            # W2: a writer killed between os.link and os.unlink left the live name and the
            # highest plain generation on one inode. Complete that rotation: repeating it would
            # link the same rows to a second number and every reader would count them twice.
            found = generations(path)
            plain = [n for n, forms in found.items() if "plain" in forms]
            if plain:
                top = max(plain)
                try:
                    same = os.path.samestat(os.lstat(found[top]["plain"]), live)
                except OSError:
                    same = False
                if same:
                    os.unlink(path)
                    log(f"completed an interrupted rotation of {path.name} to generation {top}; "
                        f"store is {store_size(path)} bytes")
                    return top
        if live.st_size < threshold:
            return None
        if found is None:
            found = generations(path)
        # W1: one more than every number present in either form. os.link fails on an existing
        # name where os.rename would replace it, so no generation is ever overwritten.
        number = max(found, default=0) + 1
        os.link(path, path.with_name(f"{path.name}.{number}"), follow_symlinks=False)
        os.unlink(path)
        log(f"rotated {path.name} to generation {number}; store is {store_size(path)} bytes")
        return number
    except OSError as exc:
        log(f"rotation of {path} failed: {exc.strerror or exc}")
        return None
```

- [ ] **Step 4: Rewire `model_proxy.py`.**

(a) Imports: add `import contextlib` to the stdlib block (alphabetical), and below `from aiohttp import web` add

```python
from factory_core import rotation
```

(b) Replace the line `BACKUP_COUNT = int(os.environ.get("MODEL_PROXY_BACKUP_COUNT", "3"))` with

```python
# No backup count (#652 W9): rotation keeps every generation, numbered and compressed
# (factory_core/rotation.py); pruning is a manual operator action (docs/configuration.md).
```

(c) Delete the whole `_rotate_if_needed` function.

(d) Replace `append_ledger` (keep `_ledger_lock_path` above it unchanged) with:

```python
@contextlib.contextmanager
def _ledger_locked():
    """LOCK_EX on the sidecar lock, waited for without bound: append_ledger's wait (#634),
    unchanged by #652, which also takes it briefly for the compression swap."""
    with open(_ledger_lock_path(), "a", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            yield True
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def append_ledger(row: dict) -> "int | None":
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Rotate-and-append under one lock (#634): locking LEDGER_PATH itself only covered the
    # write, so two processes could both pass the rotate check at once and clobber/misplace
    # rows. The lock file is never rotated/renamed, so it stays a stable mutex for both steps.
    with _ledger_locked():
        created = rotation.rotate_locked(LEDGER_PATH, MAX_LEDGER_BYTES)
        with open(LEDGER_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    # #652: the generation this call rotated out, still plain. compress_ledger_generation
    # compresses it with the lock released; _persist_ledger submits that as its own task.
    return created


def compress_ledger_generation(number: int) -> None:
    """Compress ledger generation `number` and run the recovery pass (#652 W3-W5). The ledger
    lock is held only for the brief swap. Never raises; failures are logged."""
    rotation.compress_and_recover(LEDGER_PATH, number, threshold=MAX_LEDGER_BYTES,
                                  lock=_ledger_locked)
```

(e) To keep this task green before Task 5, add a temporary stub at the end of `rotation.py`. Task 5 Step 3 replaces it:

```python
def compress_and_recover(path, created, *, threshold, lock) -> None:
    """Replaced in the next task (W3-W5)."""
```

- [ ] **Step 5: Run — verify pass, plus the #634 guards and the rest of the file**

```bash
PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py -v
```
Expected: all PASS, including `test_append_ledger_rotates_at_max_bytes`, `test_rotation_logs_the_store_size` and the unmodified guards `test_append_ledger_lock_follows_the_ledger_path`, `test_append_ledger_writes_line` and `test_ledger_persistence_does_not_block_event_loop`.

```bash
grep -rn "BACKUP_COUNT" scripts/factory_core/
```
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add scripts/factory_core/rotation.py scripts/factory_core/model_proxy.py tests/test_factory_model_proxy.py
git commit -m "feat(#652): ledger rotates to the next number, completes an interrupted rotation, logs store size (W1, W2, W10)"
```

---

## Task 5: Compression outside the lock, the swap, the recovery pass, safe opens (W1–W6, W9's test)

**Files:**
- Modify: `scripts/factory_core/rotation.py` (replace the stub)
- Test: `tests/test_factory_model_proxy.py`

- [ ] **Step 1: Write the failing tests** — append after `test_rotation_logs_the_store_size`:

```python
def test_backup_count_variable_is_ignored(tmp_path):
    assert not hasattr(mp, "BACKUP_COUNT")
    ledger = tmp_path / "request-ledger.jsonl"
    env = dict(os.environ, PYTHONPATH=str(_SCRIPTS_DIR), MODEL_PROXY_LEDGER_PATH=str(ledger),
               MODEL_PROXY_MAX_BYTES="200", MODEL_PROXY_BACKUP_COUNT="1")
    code = (
        "from factory_core import model_proxy as mp\n"
        "rotations = i = 0\n"
        "while rotations < 5:\n"
        "    created = mp.append_ledger({'run_id': 'run-1', 'i': i, 'pad': 'x' * 80})\n"
        "    i += 1\n"
        "    if created is not None:\n"
        "        mp.compress_ledger_generation(created)\n"
        "        rotations += 1\n"
    )
    subprocess.run([sys.executable, "-c", code], env=env, check=True, timeout=60, capture_output=True)
    assert {f"request-ledger.jsonl.{n}.gz" for n in range(1, 6)} <= set(_names(tmp_path))


@pytest.mark.parametrize("form", ["plain", "gz"])
def test_rotation_never_replaces_an_existing_generation_name(tmp_path, monkeypatch, form):
    path = _small_ledger(tmp_path, monkeypatch)
    planted_rows = (json.dumps({"run_id": "planted"}) + "\n").encode()
    planted = tmp_path / ("request-ledger.jsonl.1" + (".gz" if form == "gz" else ""))
    planted.write_bytes(gzip.compress(planted_rows) if form == "gz" else planted_rows)
    before = planted.read_bytes()
    _over_threshold(path)
    assert _append_and_compress({"run_id": "run-1"}) == 2  # the number after the planted one
    assert _generation_bytes(path, 1) == planted_rows
    if form == "gz":
        assert planted.read_bytes() == before
    assert (tmp_path / "request-ledger.jsonl.2.gz").exists()


def test_rotation_killed_between_link_and_unlink_duplicates_no_row(tmp_path, monkeypatch):
    path = _small_ledger(tmp_path, monkeypatch)
    _over_threshold(path)  # three rows
    os.link(path, tmp_path / "request-ledger.jsonl.1")  # the killed writer's link; no unlink ran
    rows, _ = rr._read_ledger_rows("run-1", path)
    assert len(rows) == 3  # one inode, read once
    assert _append_and_compress({"run_id": "run-1", "new": True}) == 1  # completed, not repeated
    names = _names(tmp_path)
    assert "request-ledger.jsonl.2" not in names and "request-ledger.jsonl.2.gz" not in names
    rows, _ = rr._read_ledger_rows("run-1", path)
    assert len(rows) == 4


def test_two_compressors_of_one_generation_never_share_a_temporary_file(tmp_path, monkeypatch, capsys):
    _small_ledger(tmp_path, monkeypatch)
    content = b'{"run_id": "run-1"}\n'
    plain = tmp_path / "request-ledger.jsonl.1"
    plain.write_bytes(content)
    barrier = threading.Barrier(2, timeout=10)
    listings = []
    original = rotation._write_gz

    def meeting_write(src, dst):
        barrier.wait()  # both compressors hold their temporary file at once ...
        listings.append(sorted(p.name for p in tmp_path.glob("*.gz.tmp.*")))
        barrier.wait()  # ... and both have listed before either finishes and swaps
        original(src, dst)

    monkeypatch.setattr(rotation, "_write_gz", meeting_write)
    threads = [threading.Thread(target=mp.compress_ledger_generation, args=(1,)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert [len(names) for names in listings] == [2, 2]  # two distinct names
    assert gzip.decompress((tmp_path / "request-ledger.jsonl.1.gz").read_bytes()) == content
    assert not plain.exists() and not list(tmp_path.glob("*.gz.tmp.*"))

    # A temporary name already taken is never written through (O_EXCL); that compressor steps aside.
    monkeypatch.setattr(rotation, "_write_gz", original)
    monkeypatch.setattr(rotation.secrets, "token_hex", lambda n: "ab" * n)
    second = tmp_path / "request-ledger.jsonl.2"
    second.write_bytes(content)
    taken = tmp_path / ("request-ledger.jsonl.2.gz.tmp." + "ab" * 8)
    taken.write_bytes(b"another compressor's half-written output")
    capsys.readouterr()
    mp.compress_ledger_generation(2)
    assert taken.read_bytes() == b"another compressor's half-written output"
    assert second.read_bytes() == content
    assert not (tmp_path / "request-ledger.jsonl.2.gz").exists()
    assert "is taken" in capsys.readouterr().err


def test_compression_failure_is_logged_not_raised(tmp_path, monkeypatch, capsys):
    path = _small_ledger(tmp_path, monkeypatch)
    _over_threshold(path)
    assert mp.append_ledger({"run_id": "run-1"}) == 1

    def failing_write(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(rotation, "_write_gz", failing_write)
    capsys.readouterr()
    mp.compress_ledger_generation(1)  # must not raise
    assert "compression of request-ledger.jsonl.1 failed" in capsys.readouterr().err
    assert (tmp_path / "request-ledger.jsonl.1").exists()
    assert not list(tmp_path.glob("*.gz.tmp.*"))
    rows, _ = rr._read_ledger_rows("run-1", path)
    assert len(rows) == 4


def test_existing_gz_is_never_replaced_by_a_second_compressor(tmp_path, monkeypatch):
    _small_ledger(tmp_path, monkeypatch)
    content = b'{"run_id": "run-1"}\n'
    (tmp_path / "request-ledger.jsonl.1").write_bytes(content)
    gz = tmp_path / "request-ledger.jsonl.1.gz"
    first = gzip.compress(content, mtime=1)  # the other compressor's bytes (ours use mtime=0)
    original = rotation._write_gz

    def write_then_lose_the_race(src, dst):
        original(src, dst)
        gz.write_bytes(first)  # the other compressor swapped first

    monkeypatch.setattr(rotation, "_write_gz", write_then_lose_the_race)
    mp.compress_ledger_generation(1)
    assert gz.read_bytes() == first
    assert not (tmp_path / "request-ledger.jsonl.1").exists()
    assert not list(tmp_path.glob("*.gz.tmp.*"))


def test_stalled_generation_is_compressed_by_the_next_rotation(tmp_path, monkeypatch):
    path = _small_ledger(tmp_path, monkeypatch)
    (tmp_path / "request-ledger.jsonl.1").write_text(json.dumps({"run_id": "run-1", "stalled": True}) + "\n")
    _over_threshold(path)
    assert _append_and_compress({"run_id": "run-1"}) == 2
    assert _names(tmp_path) == ["request-ledger.jsonl", "request-ledger.jsonl.1.gz",
                                "request-ledger.jsonl.2.gz", "request-ledger.jsonl.lock"]
    rows, _ = rr._read_ledger_rows("run-1", path)
    assert len(rows) == 5


def test_recovery_compresses_at_most_two_generations_per_rotation(tmp_path, monkeypatch):
    path = _small_ledger(tmp_path, monkeypatch)
    for n in (1, 2, 3):
        (tmp_path / f"request-ledger.jsonl.{n}").write_text(json.dumps({"run_id": "run-1", "n": n}) + "\n")
    _over_threshold(path)
    assert _append_and_compress({"run_id": "run-1"}) == 4
    names = set(_names(tmp_path))
    assert {"request-ledger.jsonl.4.gz", "request-ledger.jsonl.3.gz", "request-ledger.jsonl.2.gz",
            "request-ledger.jsonl.1"} <= names
    assert not names & {"request-ledger.jsonl.1.gz", "request-ledger.jsonl.2", "request-ledger.jsonl.3"}


def test_stale_temporary_file_is_removed_by_the_recovery_pass(tmp_path, monkeypatch):
    path = _small_ledger(tmp_path, monkeypatch)
    stale = tmp_path / ("request-ledger.jsonl.7.gz.tmp." + "0" * 16)
    fresh = tmp_path / ("request-ledger.jsonl.7.gz.tmp." + "f" * 16)
    for temporary in (stale, fresh):
        temporary.write_bytes(b"partial")
    hour_ago = time.time() - 3700
    os.utime(stale, (hour_ago, hour_ago))
    _over_threshold(path)
    _append_and_compress({"run_id": "run-1"})
    assert not stale.exists()
    assert fresh.exists()


def test_planted_fifo_under_a_generation_name_never_blocks(tmp_path, monkeypatch, capsys, deadline):
    path = _small_ledger(tmp_path, monkeypatch)
    os.mkfifo(tmp_path / "request-ledger.jsonl.1")
    target = tmp_path / "elsewhere.jsonl"
    target.write_text("{}\n")
    (tmp_path / "request-ledger.jsonl.2").symlink_to(target)
    (tmp_path / "request-ledger.jsonl.3").mkdir()
    with open(tmp_path / "request-ledger.jsonl.4", "wb") as fh:
        fh.truncate(2 * 200 + 1)  # over twice the threshold
    _over_threshold(path)
    capsys.readouterr()
    assert _append_and_compress({"run_id": "run-1"}) == 5
    err = capsys.readouterr().err
    for n in (1, 2, 3, 4):
        assert len(re.findall(rf"skipped \S*request-ledger\.jsonl\.{n}: ", err)) == 1, err
    assert (tmp_path / "request-ledger.jsonl.5.gz").exists()
    assert stat.S_ISFIFO(os.lstat(tmp_path / "request-ledger.jsonl.1").st_mode)
    assert (tmp_path / "request-ledger.jsonl.2").is_symlink()
    assert (tmp_path / "request-ledger.jsonl.3").is_dir()
    assert target.read_text() == "{}\n"
    rows, _ = rr._read_ledger_rows("run-1", path)  # the reader does not block on them either
    assert len(rows) == 4
```

- [ ] **Step 2: Run — verify they fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py -k "never_replaces or killed_between or two_compressors or compression_failure or existing_gz or stalled_generation or at_most_two or stale_temporary or planted_fifo or backup_count_variable" -v
```
Expected: FAIL. The stub compresses nothing, and `rotation._write_gz` and `rotation.secrets` do not exist yet. (`test_rotation_killed_between_link_and_unlink_duplicates_no_row` may pass already: W2 and the reader's inode dedupe landed in Tasks 2 and 4.)

- [ ] **Step 3: Implement.** In `rotation.py`, add `import gzip`, `import secrets` and `import shutil` to the imports (alphabetical). Below `_LOCK_POLL_SECONDS` add:

```python
RECOVERY_PER_ROTATION = 2
STALE_TEMPORARY_SECONDS = 3600
_COPY_CHUNK_BYTES = 1 << 20
```

Then replace the stub `compress_and_recover` with:

```python
def _unlink_quietly(path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log(f"could not remove {path}: {exc.strerror or exc}")


def _is_regular(path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def _write_gz(src_fd: int, dst_fd: int) -> None:
    """gzip src into dst in chunks; a 100 MiB generation is never held in memory."""
    with os.fdopen(src_fd, "rb", closefd=False) as src, \
            os.fdopen(dst_fd, "wb", closefd=False) as raw, \
            gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as out:
        shutil.copyfileobj(src, out, _COPY_CHUNK_BYTES)


def _compress(path: Path, number: int, threshold: int, lock) -> bool:
    """W3 and W4 for generation `number`. Returns False when the plain file was skipped (W6:
    absent, not a regular file, or over twice the threshold) or its temporary name was taken;
    True otherwise."""
    plain = path.with_name(f"{path.name}.{number}")
    gz = path.with_name(f"{path.name}.{number}.gz")
    opened = open_regular(plain, 2 * threshold)
    if opened is None:
        return False
    src_fd, info = opened
    # W3: a random name, created exclusively. A pid is not unique here, because both ledger
    # writers are pid 1 of their own container.
    temporary = path.with_name(f"{gz.name}.tmp.{secrets.token_hex(8)}")
    try:
        try:
            dst_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                             | os.O_CLOEXEC, 0o666)
        except FileExistsError:
            log(f"skipped compressing {plain}: temporary name {temporary.name} is taken")
            return False
        try:
            try:
                _write_gz(src_fd, dst_fd)
                os.fsync(dst_fd)
                # Keep the plain file's mtime so run_record's since_epoch filter still applies.
                os.utime(dst_fd, ns=(info.st_atime_ns, info.st_mtime_ns))
            finally:
                os.close(dst_fd)
        except BaseException:
            _unlink_quietly(temporary)
            raise
    finally:
        os.close(src_fd)
    # W4: the swap takes the lock again, briefly. An existing .gz is never replaced.
    with lock() as held:
        if not held:
            _unlink_quietly(temporary)
            log(f"left {plain} uncompressed: the lock was not taken for the swap")
            return True
        if not os.path.lexists(gz):
            try:
                os.link(temporary, gz, follow_symlinks=False)
            except OSError as exc:
                log(f"could not link {gz}: {exc.strerror or exc}")
        _unlink_quietly(temporary)
        if _is_regular(gz):
            _unlink_quietly(plain)
    return True


def _recover(path: Path, created: int, threshold: int, lock) -> None:
    """W5, the recovery pass. Compresses up to RECOVERY_PER_ROTATION other plain generations
    that have no .gz (a writer was killed between rotating and swapping), highest number
    first; an entry skipped under W6 does not count. Then, under the lock, it removes each
    plain generation whose .gz is a regular file, and every temporary older than
    STALE_TEMPORARY_SECONDS. A planted non-regular .gz keeps its plain generation in place
    (readers prefer plain) and is not retried."""
    found = generations(path)
    done = 0
    for number in sorted(found, reverse=True):
        if done >= RECOVERY_PER_ROTATION:
            break
        if number == created or "plain" not in found[number] or "gz" in found[number]:
            continue
        if _compress(path, number, threshold, lock):
            done += 1
    cutoff = time.time() - STALE_TEMPORARY_SECONDS
    with lock() as held:
        if not held:
            return
        for forms in generations(path).values():
            if "plain" in forms and "gz" in forms and _is_regular(forms["gz"]):
                _unlink_quietly(forms["plain"])
        for temporary in temporaries(path):
            try:
                info = os.lstat(temporary)
            except OSError:
                continue
            if not stat.S_ISDIR(info.st_mode) and info.st_mtime < cutoff:
                _unlink_quietly(temporary)


def compress_and_recover(path, created, *, threshold, lock) -> None:
    """W3-W6 after a rotation, called with no lock held. `lock` is a zero-argument callable
    that returns a context manager yielding True while LOCK_EX on lock_path(path) is held; the
    swap and every removal take it briefly. Compresses generation `created`, then runs the
    recovery pass (W5). Nothing else triggers the pass, and there is no background task.
    Never raises: a failure is one log line and leaves the plain generation in place,
    still readable."""
    path = Path(path)
    try:
        _compress(path, created, threshold, lock)
        _recover(path, created, threshold, lock)
    except Exception as exc:  # noqa: BLE001 — compression must never fail an append
        log(f"compression of {path.name}.{created} failed: {exc!r}")
```

- [ ] **Step 4: Run — verify pass**

```bash
PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py -v
```
Expected: all PASS, including `test_backup_count_variable_is_ignored` (`MODEL_PROXY_BACKUP_COUNT=1`, five `.gz` kept).

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_core/rotation.py tests/test_factory_model_proxy.py
git commit -m "feat(#652): compress outside the lock, swap without replacing, recover stalled generations (W3-W6)"
```

---

## Task 6: Compression as its own executor task; whole-store ledger criteria (W3, W8, criteria 1, 2, 4, R4 concurrency)

**Files:**
- Modify: `scripts/factory_core/model_proxy.py` (`_persist_ledger`, `_PENDING_COMPRESSIONS`)
- Test: `tests/test_factory_model_proxy.py`, `tests/test_run_record.py`

- [ ] **Step 1: Write the failing test for W3's request path** — append to `tests/test_factory_model_proxy.py`:

```python
def test_append_ledger_returns_before_compression_finishes(tmp_path, monkeypatch):
    path = _small_ledger(tmp_path, monkeypatch)
    monkeypatch.setattr(mp, "post_seq_ledger", lambda row: None)
    _over_threshold(path)
    started, release = threading.Event(), threading.Event()

    def slow_compress(number):
        started.set()
        release.wait(10)

    monkeypatch.setattr(mp, "compress_ledger_generation", slow_compress)

    async def scenario():
        try:
            await asyncio.wait_for(mp._persist_ledger({"run_id": "run-1", "new": True}), 2)
            assert started.wait(2)  # submitted as its own executor task ...
            assert not release.is_set()  # ... still running when the ledger step returned
            assert json.loads(path.read_text().splitlines()[-1]) == {"run_id": "run-1", "new": True}
        finally:
            release.set()

    asyncio.run(scenario())
```

- [ ] **Step 2: Run — verify it fails**

```bash
PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py -k returns_before_compression -v
```
Expected: FAIL at `started.wait(2)`, because `_persist_ledger` never calls `compress_ledger_generation`.

- [ ] **Step 3: Implement.** In `model_proxy.py`, replace `_persist_ledger` with:

```python
# Compression tasks in flight (#652 W3): held so a pending future is not garbage-collected.
_PENDING_COMPRESSIONS: set = set()


async def _persist_ledger(row: dict) -> None:
    """Write the ledger row and post it to Seq off the event loop.

    append_ledger does blocking flock/write and post_seq_ledger does a blocking
    urlopen (timeout=5) — run both in the default thread executor so a slow
    disk or unreachable Seq never stalls concurrent streaming requests. A rotation's
    compression (#652 W3) is its own executor task: this request does not wait for it, and
    its failure is logged inside compress_ledger_generation, never raised here.
    """
    loop = asyncio.get_running_loop()
    created = await loop.run_in_executor(None, append_ledger, row)
    if created is not None:
        task = loop.run_in_executor(None, compress_ledger_generation, created)
        _PENDING_COMPRESSIONS.add(task)
        task.add_done_callback(_PENDING_COMPRESSIONS.discard)
    await loop.run_in_executor(None, post_seq_ledger, row)
```

- [ ] **Step 4: Run — verify pass, plus the #634 guard**

```bash
PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py -k "returns_before_compression or ledger_persistence_does_not_block_event_loop or lock_follows_the_ledger_path" -v
```
Expected: PASS.

- [ ] **Step 5: Add the whole-store ledger tests** — append to `tests/test_factory_model_proxy.py`:

```python
_LEDGER_WORKER = (
    "import sys\n"
    "from factory_core import model_proxy as mp\n"
    "writer, count = sys.argv[1], int(sys.argv[2])\n"
    "for i in range(count):\n"
    "    created = mp.append_ledger({'run_id': 'run-c', 'writer': writer, 'i': i, 'pad': 'x' * 40})\n"
    "    if created is not None:\n"
    "        mp.compress_ledger_generation(created)\n"
)


def test_rotation_keeps_every_generation(tmp_path, monkeypatch):
    path = _small_ledger(tmp_path, monkeypatch)
    seen = {}
    rows = 0
    while len(seen) < 5 or rows < 40:
        _append_and_compress({"run_id": "run-1", "i": rows, "pad": "x" * 60})
        rows += 1
        for gz in tmp_path.glob("request-ledger.jsonl.*.gz"):
            data = gz.read_bytes()
            assert seen.setdefault(gz.name, data) == data  # never rewritten by a later rotation
        assert rows < 500
    assert {f"request-ledger.jsonl.{n}.gz" for n in range(1, 6)} <= set(seen)
    assert all(gzip.decompress(data) for data in seen.values())
    got, _ = rr._read_ledger_rows("run-1", path)
    assert sorted(r["i"] for r in got) == list(range(rows))


def test_concurrent_appends_through_rotations_lose_nothing(tmp_path):
    ledger = tmp_path / "request-ledger.jsonl"
    env = dict(os.environ, PYTHONPATH=str(_SCRIPTS_DIR), MODEL_PROXY_LEDGER_PATH=str(ledger),
               MODEL_PROXY_MAX_BYTES="2000")
    writers = [subprocess.Popen([sys.executable, "-c", _LEDGER_WORKER, str(w), "400"], env=env,
                                stderr=subprocess.DEVNULL) for w in range(8)]
    assert [p.wait(timeout=180) for p in writers] == [0] * 8
    rows, _ = rr._read_ledger_rows("run-c", ledger)
    pairs = [(r["writer"], r["i"]) for r in rows]
    assert len(pairs) == 3200 and len(set(pairs)) == 3200
    leftovers = [n for n in _names(tmp_path)
                 if re.fullmatch(r"request-ledger\.jsonl\.[0-9]+(\.gz\.tmp\.[0-9a-f]{16})?", n)]
    assert leftovers == []  # no plain generation and no temporary file left


def test_append_is_not_blocked_by_a_slow_compression(tmp_path, monkeypatch):
    path = _small_ledger(tmp_path, monkeypatch)
    _over_threshold(path)
    assert mp.append_ledger({"run_id": "run-1"}) == 1
    compressing, release = threading.Event(), threading.Event()
    original = rotation._write_gz

    def slow_write(src, dst):
        compressing.set()
        release.wait(10)
        original(src, dst)

    monkeypatch.setattr(rotation, "_write_gz", slow_write)
    compressor = threading.Thread(target=mp.compress_ledger_generation, args=(1,))
    compressor.start()
    try:
        assert compressing.wait(5)
        appender = threading.Thread(target=mp.append_ledger, args=({"run_id": "run-1", "during": True},))
        started = time.monotonic()
        appender.start()
        appender.join(2)
        elapsed = time.monotonic() - started
    finally:
        release.set()
        compressor.join(10)
    assert not appender.is_alive() and elapsed < 1.5  # it would wait the full 10 s if compression held the lock
    assert (tmp_path / "request-ledger.jsonl.1.gz").exists()


def test_old_numbered_generations_are_kept_compressed_and_read(tmp_path, monkeypatch):
    path = _small_ledger(tmp_path, monkeypatch)
    for n in (1, 2, 3):  # left by the deleting rotation before #652
        (tmp_path / f"request-ledger.jsonl.{n}").write_text(json.dumps({"run_id": "run-1", "legacy": n}) + "\n")
    _over_threshold(path)
    assert _append_and_compress({"run_id": "run-1", "i": 0}) == 4  # compresses 4, then 3 and 2
    i = 1
    while _append_and_compress({"run_id": "run-1", "i": i, "pad": "x" * 80}) is None:  # until 5
        i += 1
        assert i < 50
    assert {f"request-ledger.jsonl.{n}.gz" for n in range(1, 6)} <= set(_names(tmp_path))
    assert not {f"request-ledger.jsonl.{n}" for n in range(1, 6)} & set(_names(tmp_path))
    rows, _ = rr._read_ledger_rows("run-1", path)
    assert sorted(r["legacy"] for r in rows if "legacy" in r) == [1, 2, 3]
    assert len([r for r in rows if "old" in r]) == 3
```

- [ ] **Step 6: Add the R4 concurrency reader test** — append to the reader tests in `tests/test_run_record.py`. Add `import subprocess` to its imports and, below the `rr` import, add `_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"`:

```python
_ROTATING_WRITER = (
    "import sys, time\n"
    "from factory_core import model_proxy as mp\n"
    "writer, count = sys.argv[1], int(sys.argv[2])\n"
    "for i in range(count):\n"
    "    created = mp.append_ledger({'run_id': 'run-c', 'writer': writer, 'i': i, 'pad': 'x' * 20})\n"
    "    if created is not None:\n"
    "        mp.compress_ledger_generation(created)\n"
    "    time.sleep(0.002)\n"
)


def test_concurrent_read_is_consistent(tmp_path):
    # Writers rotate about every 25 rows; every read must be a snapshot: per writer, the
    # rows 0..k with none missing and none repeated. The writers sleep 2 ms between rows so
    # the reader's LOCK_SH|LOCK_NB polling is not starved past READ_LOCK_WAIT_SECONDS (which
    # would drop it to the unlocked R5 path). On a failure here, check for starvation first.
    ledger = tmp_path / "request-ledger.jsonl"
    env = dict(os.environ, PYTHONPATH=str(_SCRIPTS_DIR), MODEL_PROXY_LEDGER_PATH=str(ledger),
               MODEL_PROXY_MAX_BYTES="2000")
    writers = [subprocess.Popen([sys.executable, "-c", _ROTATING_WRITER, str(w), "300"], env=env,
                                stderr=subprocess.DEVNULL) for w in range(4)]
    reads = 0
    try:
        while any(p.poll() is None for p in writers):
            rows, _ = rr._read_ledger_rows("run-c", ledger)
            by_writer = {}
            for row in rows:
                by_writer.setdefault(row["writer"], []).append(row["i"])
            for writer, seen in by_writer.items():
                assert sorted(seen) == list(range(len(seen))), (
                    f"read {reads}: writer {writer} has a missing or repeated row")
            reads += 1
    finally:
        for p in writers:
            p.wait(timeout=120)
    assert [p.returncode for p in writers] == [0] * 4
    assert reads >= 10
    rows, _ = rr._read_ledger_rows("run-c", ledger)
    assert len(rows) == 1200
```

- [ ] **Step 7: Run — verify all pass**

```bash
PYTHONPATH=scripts python -m pytest tests/test_factory_model_proxy.py tests/test_run_record.py -v
```
Expected: all PASS. The implementation already exists, so these confirm criteria 1, 2 and 4, W8 and R4 end to end. If `test_concurrent_read_is_consistent` fails, the shared lock or the snapshot is broken. Fix `_open_ledger_snapshot`; do not loosen the assertion.

- [ ] **Step 8: Commit**

```bash
git add scripts/factory_core/model_proxy.py tests/test_factory_model_proxy.py tests/test_run_record.py
git commit -m "feat(#652): ledger compression runs as its own executor task; whole-store and concurrency tests"
```

---

## Task 7: Cost log — sidecar lock with a bounded wait, numbered rotation, inline compression (W1–W8, W10)

**Files:**
- Modify: `scripts/factory_core/cost_log.py`
- Test: `tests/test_cost_log.py`

- [ ] **Step 1: Write the failing tests.** In `tests/test_cost_log.py`, extend the imports to:

```python
import fcntl
import gzip
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core import cost_log  # noqa: E402
from factory_core import rotation  # noqa: E402

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
RATE = {"cost": 1, "remaining": 9, "resetAt": "r"}


def _store_lines(directory):
    """Every line of the cost log: the live file, then each generation (plain or .gz)."""
    log = directory / cost_log.LOG_NAME
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    for _, forms in sorted(rotation.generations(log).items()):
        path = forms.get("plain") or forms["gz"]
        data = path.read_bytes()
        if path.name.endswith(".gz"):
            data = gzip.decompress(data)
        lines.extend(data.decode("utf-8").splitlines())
    return lines


def _fill_over(log, threshold):
    line = json.dumps({"ts": "old", "source": "scheduler", "context": "old", "cost": 1,
                       "remaining": 1, "reset_at": "r"}) + "\n"
    log.write_text(line * (threshold // len(line) + 1), encoding="utf-8")


@pytest.fixture
def deadline():
    """Fail, never hang, when an open blocks (a FIFO opened without O_NONBLOCK)."""
    def _blocked(*_):
        raise TimeoutError("blocked for 10 s")
    old = signal.signal(signal.SIGALRM, _blocked)
    signal.alarm(10)
    yield
    signal.alarm(0)
    signal.signal(signal.SIGALRM, old)
```

Replace `test_log_call_rotates_when_over_cap` with:

```python
def test_log_call_rotates_when_over_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(tmp_path))
    log = tmp_path / cost_log.LOG_NAME
    _fill_over(log, cost_log.ROTATE_BYTES)
    before = log.read_bytes()
    cost_log.log_call("board_items", {"cost": 3, "remaining": 7, "resetAt": "r"})
    # The fresh file holds only the new line. The old generation is kept, numbered and
    # compressed (#652), not overwritten.
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1
    assert not (tmp_path / (cost_log.LOG_NAME + ".1")).exists()
    assert gzip.decompress((tmp_path / (cost_log.LOG_NAME + ".1.gz")).read_bytes()) == before
```

Append (keep `test_log_call_fails_soft_on_unwritable_dir` unchanged as the guard):

```python
_COST_WORKER = (
    "import sys\n"
    "from factory_core import cost_log\n"
    "cost_log.ROTATE_BYTES = 2000\n"
    "writer, count = sys.argv[1], int(sys.argv[2])\n"
    "for i in range(count):\n"
    "    cost_log.log_call(f'{writer}:{i}', {'cost': i, 'remaining': 0, 'resetAt': 'r'})\n"
)


def test_rotation_keeps_every_generation(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cost_log, "ROTATE_BYTES", 300)
    seen = {}
    calls = 0
    while len(seen) < 5 or calls < 40:
        cost_log.log_call(f"call-{calls}", RATE)
        calls += 1
        for gz in tmp_path.glob(cost_log.LOG_NAME + ".*.gz"):
            data = gz.read_bytes()
            assert seen.setdefault(gz.name, data) == data  # never rewritten by a later rotation
        assert calls < 500
    assert {f"{cost_log.LOG_NAME}.{n}.gz" for n in range(1, 6)} <= set(seen)
    assert all(gzip.decompress(data) for data in seen.values())
    contexts = [json.loads(line)["context"] for line in _store_lines(tmp_path)]
    assert sorted(contexts) == sorted(f"call-{i}" for i in range(calls))


def test_concurrent_appends_through_rotations_lose_nothing(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(_SCRIPTS_DIR), SCHEDULER_STATE_DIR=str(tmp_path))
    writers = [subprocess.Popen([sys.executable, "-c", _COST_WORKER, str(w), "400"], env=env,
                                stderr=subprocess.DEVNULL) for w in range(8)]
    assert [p.wait(timeout=180) for p in writers] == [0] * 8
    contexts = [json.loads(line)["context"] for line in _store_lines(tmp_path)]
    assert len(contexts) == 3200 and len(set(contexts)) == 3200
    assert not any("plain" in forms for forms in rotation.generations(tmp_path / cost_log.LOG_NAME).values())
    assert rotation.temporaries(tmp_path / cost_log.LOG_NAME) == []


def test_append_is_not_blocked_by_a_slow_compression(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cost_log, "ROTATE_BYTES", 300)
    log = tmp_path / cost_log.LOG_NAME
    _fill_over(log, 300)
    compressing, release = threading.Event(), threading.Event()
    original = rotation._write_gz

    def slow_write(src, dst):
        compressing.set()
        release.wait(10)
        original(src, dst)

    monkeypatch.setattr(rotation, "_write_gz", slow_write)
    first = threading.Thread(target=cost_log.log_call, args=("first", RATE))
    first.start()
    try:
        assert compressing.wait(5)
        started = time.monotonic()
        cost_log.log_call("second", RATE)
        elapsed = time.monotonic() - started
    finally:
        release.set()
        first.join(10)
    assert elapsed < 0.9  # under the 1 s bounded wait: the lock was free during compression
    assert [json.loads(l)["context"] for l in log.read_text().splitlines()] == ["first", "second"]
    assert (tmp_path / (cost_log.LOG_NAME + ".1.gz")).exists()


def test_log_call_returns_within_two_seconds_when_the_lock_is_held(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cost_log, "ROTATE_BYTES", 300)
    log = tmp_path / cost_log.LOG_NAME
    _fill_over(log, 300)
    with open(tmp_path / (cost_log.LOG_NAME + ".lock"), "a") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        started = time.monotonic()
        cost_log.log_call("held", RATE)
        elapsed = time.monotonic() - started
    assert elapsed < 2
    assert json.loads(log.read_text().splitlines()[-1])["context"] == "held"  # the row is kept
    assert rotation.generations(log) == {}  # and no rotation happened


def test_log_call_with_a_fifo_as_lock_file_appends_and_returns(tmp_path, monkeypatch, deadline):
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cost_log, "ROTATE_BYTES", 300)
    log = tmp_path / cost_log.LOG_NAME
    _fill_over(log, 300)
    os.mkfifo(tmp_path / (cost_log.LOG_NAME + ".lock"))
    started = time.monotonic()
    cost_log.log_call("fifo", RATE)
    assert time.monotonic() - started < 2
    assert json.loads(log.read_text().splitlines()[-1])["context"] == "fifo"
    assert rotation.generations(log) == {}


def test_old_numbered_generations_are_kept_compressed_and_read(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(cost_log, "ROTATE_BYTES", 300)
    log = tmp_path / cost_log.LOG_NAME
    legacy = tmp_path / (cost_log.LOG_NAME + ".1")  # the single generation kept before #652
    legacy.write_text(json.dumps({"context": "legacy"}) + "\n", encoding="utf-8")
    _fill_over(log, 300)
    cost_log.log_call("new", RATE)
    assert {forms.get("gz").name for forms in rotation.generations(log).values()} == {
        cost_log.LOG_NAME + ".1.gz", cost_log.LOG_NAME + ".2.gz"}
    contexts = [json.loads(line)["context"] for line in _store_lines(tmp_path)]
    assert "legacy" in contexts and "new" in contexts


@pytest.mark.parametrize("planted", ["fifo", "link", "directory"])
def test_log_call_never_waits_on_or_writes_through_a_planted_live_file(tmp_path, monkeypatch, deadline, planted):
    # log_call runs in the scheduler's poll. A FIFO at the live name blocked a plain open()
    # for good; a link there was written through.
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(tmp_path))
    log = tmp_path / cost_log.LOG_NAME
    victim = tmp_path / "victim.txt"
    victim.write_text("ORIGINAL")
    if planted == "fifo":
        os.mkfifo(log)
    elif planted == "link":
        log.symlink_to(victim)
    else:
        log.mkdir()
    started = time.monotonic()
    cost_log.log_call("planted", RATE)  # returns, raises nothing
    assert time.monotonic() - started < 2
    assert victim.read_text() == "ORIGINAL"
    log.rmdir() if planted == "directory" else log.unlink()
    cost_log.log_call("after", RATE)  # the next call, on a clean name, writes again
    assert json.loads(log.read_text().splitlines()[-1])["context"] == "after"
```

- [ ] **Step 2: Run — verify they fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_cost_log.py -v
```
Expected: the rewritten rotation test and the new tests FAIL (`.1` plain, overwritten, no lock), except the `directory` case of `test_log_call_never_waits_on_or_writes_through_a_planted_live_file`, which passes already. The six tests that are not rewritten PASS.

- [ ] **Step 3: Implement.** Replace the whole of `scripts/factory_core/cost_log.py` with:

```python
"""GraphQL cost log (#607): one JSONL line per measured call under the state dir.

Written only when SCHEDULER_STATE_DIR is EXPLICITLY set in the environment (no
default): CI's pytest job leaves it unset and separately asserts /var/lib/dark-factory
stays empty during pytest, so an unset variable means no write. Fails soft — a cost
log that raises would break the very call it measures, so every failure path is
swallowed. Rotation (#652) is size-based into numbered, gzip-compressed generations that
are never deleted or overwritten (factory_core/rotation.py), under the sidecar lock
graphql-costs.jsonl.lock, which is taken with a bounded wait.

The `source` field distinguishes the writer: "scheduler" or a run id — set by
scheduler.sh / entrypoint.sh via DARK_FACTORY_COST_SOURCE.
"""
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

from factory_core import rotation

LOG_NAME = "graphql-costs.jsonl"
ROTATE_BYTES = 1_000_000  # ~1 MiB: rotate to the next numbered generation (#652)
# log_call runs inside the scheduler's board fetch on every poll, and every run container
# mounts this directory. A lock held by any process may cost the call at most this long,
# never the poll (#652 W7).
LOCK_WAIT_SECONDS = 1.0


def _append_line(path: Path, text: str) -> None:
    """Append to the live file without ever waiting on it or writing through it (#652): the
    name is opened O_NOFOLLOW | O_NONBLOCK and must be a regular file. A FIFO planted there
    (open would block until a reader came), a link or a directory loses this line and
    raises OSError, which log_call swallows; the poll goes on."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
                 | os.O_CLOEXEC, 0o666)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path} is not a regular file")
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


def log_call(context: str, rate_limit: dict | None) -> None:
    """Append one line for a measured GraphQL call. `rate_limit` is the call's own
    `rateLimit { cost remaining resetAt }` payload (each measured query selects it,
    so the line records the call's point cost, not just a budget snapshot); None
    (a GraphQL error body) records nothing. Never raises."""
    state_dir = os.environ.get("SCHEDULER_STATE_DIR")
    if not state_dir:
        return
    if not isinstance(rate_limit, dict):
        return
    try:
        path = Path(state_dir) / LOG_NAME
        line = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": os.environ.get("DARK_FACTORY_COST_SOURCE") or "unknown",
            "context": context,
            "cost": rate_limit.get("cost"),
            "remaining": rate_limit.get("remaining"),
            "reset_at": rate_limit.get("resetAt"),
        }
        lock_file = rotation.lock_path(path)
        created = None
        # W7: with the lock, rotate (W1, W2) and append. Without it (held past the wait, or
        # not a regular file), append without rotating; the row is kept either way.
        with rotation.locked(lock_file, timeout=LOCK_WAIT_SECONDS) as held:
            if held:
                created = rotation.rotate_locked(path, ROTATE_BYTES)
            _append_line(path, json.dumps(line) + "\n")
        if created is not None:
            # W3: inline (a 1 MiB generation, synchronous callers), after the lock is released.
            rotation.compress_and_recover(
                path, created, threshold=ROTATE_BYTES,
                lock=lambda: rotation.locked(lock_file, timeout=LOCK_WAIT_SECONDS))
    except Exception:
        # Fail soft by design: measurement must never break the call it measures.
        pass
```

- [ ] **Step 4: Run — verify pass**

```bash
PYTHONPATH=scripts python -m pytest tests/test_cost_log.py -v
```
Expected: all PASS, including the guards `test_log_call_fails_soft_on_unwritable_dir`, `test_log_call_no_write_when_state_dir_unset` and `test_log_call_no_write_for_missing_rate_limit`. The last one returns before any lock file is created.

- [ ] **Step 5: The GitHub tracker caller still imports cleanly**

```bash
PYTHONPATH=scripts python -c "from factory_core.providers.tracker import github; print('ok')"
```
Expected: `ok`.

- [ ] **Step 6: Commit**

```bash
git add scripts/factory_core/cost_log.py tests/test_cost_log.py
git commit -m "feat(#652): cost log keeps every generation, rotates under a bounded sidecar lock (W1-W8, W10)"
```

---

## Task 8: `preview_helper` bounds its scan and checks the lock file (R7)

**Files:**
- Modify: `scripts/factory_core/preview_helper.py`
- Test: `tests/test_preview_helper.py`

- [ ] **Step 1: Write the failing tests** — append to the "the ledger is checked before it is read" section of `tests/test_preview_helper.py`, after `test_ledger_directory_oversized_or_bad_backup_is_unavailable`:

```python
def test_record_outcome_bounds_the_ledger_scan(tmp_path, runs, monkeypatch):
    ledger = _ledger(tmp_path, CLEAN_ROW)
    now = time.time()
    old = tmp_path / "request-ledger.jsonl.1"
    old.write_text(json.dumps(_ledger_row(**HELLO)) + "\n", encoding="utf-8")
    os.utime(old, (now - 7200, now - 7200))  # last written two hours ago
    recent = tmp_path / "request-ledger.jsonl.2"
    recent.write_text(json.dumps(_ledger_row(**HELLO)) + "\n", encoding="utf-8")
    bounds = []
    original = run_record._read_ledger_rows
    monkeypatch.setattr(run_record, "_read_ledger_rows",
                        lambda *a, **k: bounds.append(k.get("since_epoch")) or original(*a, **k))
    rec = _record(tmp_path, ledger)
    assert rec["detail"]["cli_preflight_rows"] == 1  # .2 read; .1 is older than the window
    assert len(bounds) == 1
    assert now - ph.LEDGER_SCAN_WINDOW_SECONDS - 5 <= bounds[0] <= time.time() - ph.LEDGER_SCAN_WINDOW_SECONDS
    assert ph.LEDGER_SCAN_WINDOW_SECONDS == 3600 + 60


@pytest.mark.parametrize("planted", ["fifo", "dev_zero_link", "directory"])
def test_ledger_files_ok_rejects_a_planted_lock_file(tmp_path, deadline, planted):
    ledger = _ledger(tmp_path, CLEAN_ROW)
    lock = tmp_path / "request-ledger.jsonl.lock"
    assert ph.ledger_files_ok(ledger) is True  # no lock file yet
    lock.write_text("")
    assert ph.ledger_files_ok(ledger) is True  # a regular lock file
    lock.unlink()
    if planted == "fifo":
        os.mkfifo(lock)
    elif planted == "dev_zero_link":
        lock.symlink_to("/dev/zero")
    else:
        lock.mkdir()
    assert ph.ledger_files_ok(ledger) is False
```

- [ ] **Step 2: Run — verify they fail**

```bash
PYTHONPATH=scripts python -m pytest tests/test_preview_helper.py -k "bounds_the_ledger_scan or planted_lock_file" -v
```
Expected: FAIL (`ph.LEDGER_SCAN_WINDOW_SECONDS` missing; the lock file is not checked).

- [ ] **Step 3: Implement.** In `scripts/factory_core/preview_helper.py`:

(a) Replace the comment and constant

```python
# Bound on the shared ledger reader (which opens plainly, no O_NONBLOCK): a gated container
# that replaces a ledger file with a FIFO between ledger_files_ok()'s check and this read
# would otherwise block record-outcome forever (#642).
LEDGER_READ_TIMEOUT_SECONDS = 5
```
with

```python
# Bound on the shared ledger reader (#642): a gated container can replace a ledger file
# between ledger_files_ok()'s check and the read, and a store kept forever (#652) is large.
# record-outcome must not wait past this.
LEDGER_READ_TIMEOUT_SECONDS = 5
# The ledger scan's window (#652 R7): the lane's limit ceiling (3600 s) plus a margin. Older
# generations cannot hold this run's rows, and scanning months of them would exceed
# LEDGER_READ_TIMEOUT_SECONDS and turn every outcome into ledger_unavailable.
LEDGER_SCAN_WINDOW_SECONDS = 3600 + 60
```

(b) Replace `ledger_files_ok` with:

```python
def ledger_files_ok(ledger_path, since_epoch=None):
    """True when the live ledger, each generation run_record._iter_ledger_paths yields for
    since_epoch, and the sidecar lock file (#652 R7) are each, where they exist, a regular
    file itself (not a link, FIFO, device or directory) no larger than MAX_LEDGER_FILE_BYTES.
    These paths are checked before the shared reader opens them. An absent live ledger is
    left to the shared reader, which reports it as unavailable."""
    from factory_core import rotation, run_record
    try:
        paths = list(run_record._iter_ledger_paths(Path(ledger_path), since_epoch=since_epoch))
    except OSError:
        return False
    paths.append(rotation.lock_path(ledger_path))
    for path in paths:
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError:
            return False
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_LEDGER_FILE_BYTES:
            return False
    return True
```

(c) Replace `_read_ledger_rows_bounded` with:

```python
def _read_ledger_rows_bounded(run_id, ledger_path, timeout_seconds=LEDGER_READ_TIMEOUT_SECONDS,
                              since_epoch=None):
    """run_record._read_ledger_rows (shared with the main harness), run in a thread and
    bounded. A path that passed ledger_files_ok() can still be swapped before the shared
    reader reaches it, and a long-kept store is large (#642, #652). A timeout reads as no
    rows / unavailable, the same as the ledger not being there at all."""
    from factory_core import run_record
    outcome = {}

    def _target():
        try:
            outcome["value"] = run_record._read_ledger_rows(run_id, ledger_path,
                                                            since_epoch=since_epoch)
        except Exception as exc:  # noqa: BLE001 — re-raised on the caller's thread below
            outcome["error"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        return [], False
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]
```

(d) In `record_outcome`, replace

```python
        if ledger_files_ok(ledger_path):
            rows, available = _read_ledger_rows_bounded(run_id, Path(ledger_path))
```
with

```python
        since_epoch = time.time() - LEDGER_SCAN_WINDOW_SECONDS
        if ledger_files_ok(ledger_path, since_epoch=since_epoch):
            rows, available = _read_ledger_rows_bounded(run_id, Path(ledger_path),
                                                        since_epoch=since_epoch)
```

- [ ] **Step 4: Run — verify pass, with every #642 planted-file test as guard**

```bash
PYTHONPATH=scripts python -m pytest tests/test_preview_helper.py -v
```
Expected: all PASS. That includes `test_ledger_directory_oversized_or_bad_backup_is_unavailable` across all five parameters (a `.1` planted now is inside the window, so it is still checked), `test_ledger_fifo_is_unavailable_without_a_read`, `test_ledger_link_to_dev_zero_is_unavailable_without_a_read` and `test_forged_ledger_rows_never_raise`.

- [ ] **Step 5: Commit**

```bash
git add scripts/factory_core/preview_helper.py tests/test_preview_helper.py
git commit -m "feat(#652): preview helper bounds its ledger scan and checks the lock file (R7)"
```

---

## Task 9: Documentation (criterion 5)

**Files:**
- Modify: `docs/configuration.md`, `docs/scheduler.md`

- [ ] **Step 1: `docs/configuration.md`, the proxy row.** In the row that begins ``| `MODEL_PROXY_PORT` `MODEL_PROXY_UPSTREAM` ``, change the defaults cell's `100 MiB, 3, ...` to `100 MiB, 3 (ignored), ...`, then replace its last cell

```
| proxy and ledger; `run-compose.yml:79-85` sets the proxy's |
```
with

```
| proxy and ledger; `run-compose.yml:79-85` sets the proxy's. `MODEL_PROXY_BACKUP_COUNT` is **ignored** since #652: no ledger generation is deleted (see "Ledger and cost-log generations" below) |
```

- [ ] **Step 2: `docs/configuration.md`, new subsection.** Insert immediately above the line `### Model endpoint registry (#557)`:

```markdown
### Ledger and cost-log generations (#652)

The model-proxy request ledger `request-ledger.jsonl` (`scripts/factory_core/model_proxy.py`) and the GraphQL cost log `graphql-costs.jsonl` (`scripts/factory_core/cost_log.py`) rotate the same way (`scripts/factory_core/rotation.py`). For a live file `<name>`:

| Name | What it is |
|---|---|
| `<name>` | the live file |
| `<name>.lock` | the sidecar lock; never rotated, renamed or removed |
| `<name>.<N>` | plain generation `N`, until it is compressed |
| `<name>.<N>.gz` | compressed generation `N`; never rewritten, renamed or removed by any code |
| `<name>.<N>.gz.tmp.<16 hex>` | a compression in progress; a leftover is removed by a rotation once it is an hour old |

- **When:** the ledger rotates at `MODEL_PROXY_MAX_BYTES` (100 MiB), the cost log at about 1 MiB. The new generation takes one more than the highest number present, and numbers only grow.
- **Growth is unbounded by design.** No generation is deleted or overwritten; `MODEL_PROXY_BACKUP_COUNT` is ignored. Each rotation logs one `factory-rotation:` line on the writer's stderr with the store's total size (the live file plus every generation). Nothing stops the store at a size: when the state volume is full, a ledger append fails with `ENOSPC` (that row is not recorded), a compression that cannot finish is logged and leaves its plain generation in place, and the cost log drops the line. Watch the size line and prune.
- **Older generations** (`request-ledger.jsonl.1`–`.3` and `graphql-costs.jsonl.1`, left by the rotation before #652) keep their names and are compressed by later rotations. Among themselves their numbers do not mean "higher is newer".
- **Readers:** `run-record assemble`, `backfill-economics`, `issue-economics` and the preview helper read the live file and every generation, plain or `.gz`. Per-run reads skip generations last modified before the run started. `scripts/reconcile_cost_reports.py` still reads only the live ledger.
- **Pruning is a manual operator action** and the only way the store shrinks. Delete whole `<name>.<N>.gz` files, oldest first by modification time (`ls -ltr`). Never delete the live file, the `.lock` file, or a plain `<name>.<N>` that is not yet compressed. Rows in a deleted generation are gone from every report above.
```

- [ ] **Step 3: `docs/scheduler.md`, the state-file row.** In the `graphql-costs.jsonl` row:
  - replace ``| `graphql-costs.jsonl` (rotates to `.1`) |`` with ``| `graphql-costs.jsonl` (numbered `.N.gz` generations, never deleted) |``;
  - replace `Size rotation at ~1 MiB, one retained generation. Fail-soft by design (#607) |` with `Size rotation at ~1 MiB into numbered, gzip-compressed generations under the sidecar lock `graphql-costs.jsonl.lock` (bounded 1 s wait; nothing is deleted, see [configuration.md](configuration.md) "Ledger and cost-log generations", #652). Fail-soft by design (#607) |`.

- [ ] **Step 4: Verify the docs checks CI runs**

```bash
python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md
PYTHONPATH=scripts python -m pytest tests/test_docs_refs_check.py tests/test_docs_conformance_reference_docs.py -q
grep -n "MODEL_PROXY_BACKUP_COUNT" docs/configuration.md
```
Expected: `Docs reference check passed for 4 doc(s).`; pytest all pass; the grep shows the variable list and the new "ignored" text.

- [ ] **Step 5: Commit**

```bash
git add docs/configuration.md docs/scheduler.md
git commit -m "docs(#652): generations are kept and compressed; growth and manual pruning; BACKUP_COUNT ignored"
```

---

## Task 10: Full verification, scope check, follow-ups

**Files:** none modified, unless a check fails.

- [ ] **Step 1: Full test suite**

```bash
PYTHONPATH=scripts python -m pytest tests/ -q
```
Expected: all pass. Compare with the Task 0 baseline: the count rises by the new tests, minus the deleted `test_append_ledger_rotation_keeps_backup_count`. The only other existing test touched is `test_assemble_reads_the_ledger_once` (Task 3 Step 4: its stub forwards `**kw`).

- [ ] **Step 2: The #634 guards ran unmodified**

```bash
git diff origin/main HEAD -- tests/test_factory_model_proxy.py | grep -n "^-.*def test_append_ledger_lock_follows_the_ledger_path\|^-.*def test_ledger_persistence_does_not_block_event_loop"
git diff origin/main HEAD -- tests/test_cost_log.py | grep -n "^-.*def test_log_call_fails_soft_on_unwritable_dir"
git diff origin/main HEAD -- tests/test_run_record.py | grep -n "^-.*def test_read_ledger_rows_includes_rotation_backups"
```
Expected: no output, meaning the guard tests were not removed or rewritten.

- [ ] **Step 3: Scope check (two-dot diff)**

```bash
git diff --name-only origin/main HEAD
```
Expected: exactly `docs/configuration.md`, `docs/scheduler.md`, `scripts/factory_core/cost_log.py`, `scripts/factory_core/model_proxy.py`, `scripts/factory_core/preview_helper.py`, `scripts/factory_core/rotation.py`, `scripts/factory_core/run_record.py`, the four test files, plus the spec/plan/memory artifacts carried from the refine branch. Nothing under `deploy/`, `.github/`, `workflows/`, `dark-factory/`, and no `scheduler.sh`, `entrypoint.sh`, `run-compose.yml`, `scripts/factory_core/cli.py` or `scripts/reconcile_cost_reports.py`.

- [ ] **Step 4: Nothing deletes a generation**

```bash
grep -n "unlink\|\.replace(\|\.rename(\|os.remove\|rmtree" scripts/factory_core/rotation.py scripts/factory_core/model_proxy.py scripts/factory_core/cost_log.py
```
Expected: only `os.unlink(path)` on the **live** name (W1, W2); `os.unlink(path)` inside `_unlink_quietly`'s own body; and `_unlink_quietly` calls on a temporary file or on a plain generation whose `.gz` is a regular file (W4, W5). No `.replace(` or `.rename(` on a ledger or cost-log path. `sweep_raw_artifacts`'s `unlink` of raw artifact files is unrelated and unchanged.

- [ ] **Step 5: Smoke the proxy and the CLI imports**

```bash
PYTHONPATH=scripts python -c "import factory_core.model_proxy, factory_core.cost_log, factory_core.run_record, factory_core.preview_helper, factory_core.rotation; print('ok')"
grep -nE "^\s*(from|import) .*providers" scripts/factory_core/rotation.py
bash tests/test_model_proxy_smoke.sh
```
Expected: `ok`; no grep output (rotation imports nothing from `providers`); the smoke test passes.

- [ ] **Step 6: File the R8 follow-up, if none exists** (`scripts/reconcile_cost_reports.py` reads only the live ledger)

```bash
gh issue list --repo "$FACTORY_REPO_SLUG" --state open --search "reconcile_cost_reports in:title,body" --json number,title
```
If that returns `[]`:

```bash
gh issue create --repo "$FACTORY_REPO_SLUG" --label observability \
  --title "fix(observability): reconcile_cost_reports reads only the live ledger (follow-up to #652)" \
  --body "Since #652 the request ledger keeps every generation (numbered, compressed). scripts/reconcile_cost_reports.py::_load_ledger_run_ids reads only the live request-ledger.jsonl, so rows that rotate out are invisible to its advisory ledger cross-check. Wire it to run_record._iter_ledger_paths / _read_ledger (importable since #652). Spec: docs/superpowers/specs/2026-11-27-durable-factory-stats-design.md (R8)."
```

- [ ] **Step 7: File the stale rollout-doc follow-up, if none exists.** `docs/dark-factory-model-proxy-rollout.md:223` says the ledger keeps "3 rotations, oldest deleted". That doc is outside this spec's file list, so it is not edited here.

```bash
gh issue list --repo "$FACTORY_REPO_SLUG" --state open --search "dark-factory-model-proxy-rollout ledger rotation in:title,body" --json number,title
```
If that returns `[]`:

```bash
gh issue create --repo "$FACTORY_REPO_SLUG" --label documentation \
  --title "docs: model-proxy rollout doc still says the ledger deletes its oldest rotation (follow-up to #652)" \
  --body "docs/dark-factory-model-proxy-rollout.md:223 says the ledger is 'size-rotated at 100 MB, 3 rotations kept, oldest deleted'. Since #652 no generation is deleted; generations are numbered and gzip-compressed and MODEL_PROXY_BACKUP_COUNT is ignored (docs/configuration.md, 'Ledger and cost-log generations'). Update the line."
```
If a label does not exist in the repo, drop that `--label` flag.

- [ ] **Step 8: Record what remains unverified in the PR body** (the spec's "Not verified" list, updated with this plan's tests). These are now covered by tests: W8 (`test_old_numbered_generations_are_kept_compressed_and_read`), W9, W10, R3's memory bound (`test_oversized_gz_is_abandoned`, a `tracemalloc` peak), R6's wiring, R7, W5's one-hour clean-up, and the random-temp-name rule (`test_two_compressors_of_one_generation_never_share_a_temporary_file`). Still accepted and not tested: a run container that hard-links the live ledger to the next number triggers W2 early (no row lost); a process holding `graphql-costs.jsonl.lock` for good stops cost-log rotation and costs each `log_call` one second. Also state the expected `HUMAN_REQUIRED` blast-radius hold (`run_record.py`, `model_proxy.py` hotspots). Unchanged by this ticket and measured the same before and after it (plan review, factory image): a FIFO planted at the live `request-ledger.jsonl` or at `request-ledger.jsonl.lock` blocks `append_ledger` in its executor thread, a link at the live ledger is written through, and a held ledger lock blocks `append_ledger` (#634's unbounded wait). File one follow-up for those three if none exists; do not fix them here.

---

## Self-review

- **Issue line:** `**Issue:** #652` is directly under the title. ✔
- **Spec coverage:** W1 (`rotate_locked`, `os.link`), W2 (inode completion), W3 (random `O_EXCL` temp, outside the lock; ledger via executor task, cost log inline), W4 (`lexists` check, no replace), W5 (two per rotation, highest first, plain-with-gz removal, one-hour temps), W6 (`open_regular`, skip not counted), W7 (`locked(timeout=1.0)`, append without rotation), W8 (legacy numbers listed and compressed), W9 (`BACKUP_COUNT` removed, documented ignored), W10 (store-size log line). R1 (`_iter_ledger_paths(since_epoch)`), R2 (plain wins), R3 (streamed `.gz`, `2 × MAX_LEDGER_BYTES` bound), R4 (`LOCK_SH` for list+open, live size noted), R5 (no `O_CREAT`, 2 s, unlocked fallback), R6 (all four readers), R7 (window, lock-file check, planted-file guards), R8 (untouched; follow-up filed). Criteria 1–5 map to the tests in the table above. ✔
- **Every spec test name is present** in Tasks 1–8. The spec's *guard* tests are kept unmodified, and Task 10 Step 2 checks that. Existing tests edited by this plan: the three the spec names as pinning deletion (`test_append_ledger_rotates_at_max_bytes` and `test_log_call_rotates_when_over_cap` rewritten; `test_append_ledger_rotation_keeps_backup_count` replaced by `test_backup_count_variable_is_ignored`). Also one stub line in `test_assemble_reads_the_ledger_once` (Task 3 Step 4), which now forwards `**kw` and keeps the same assertion. Two tests are extra beyond the spec's table: `test_assemble_read_is_bounded_by_started_at` (R6's `cmd_assemble` wiring) and the rewritten `test_append_ledger_rotates_at_max_bytes`. ✔
- **No placeholders:** every code step has the full code, and every command has its expected output. ✔
- **Scope:** only the files in the File Structure table. `rotation.py` is the spec-sanctioned optional module and imports nothing from `providers` (checked in Task 10 Step 5). No `deploy/**`, no `ci.yml`, no new shell test. ✔
