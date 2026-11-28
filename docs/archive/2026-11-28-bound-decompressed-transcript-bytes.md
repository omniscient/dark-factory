# Bound Total Decompressed Transcript Bytes — Implementation Plan

**Issue:** #656

**Goal:** Bound what one `session_usage.load_transcripts()` call reads and keeps (bytes per file, bytes per call, a per-line minimum charge, (file, message) pairs and kept `cost-state` bytes), skip an over-bound file atomically, fix the existing partial-merge `KeyError` for unreadable `.gz` files on the way, and report size skips on the live run-record and operator backfill paths.

**Architecture:** `_iter_lines` gains an opt-in `budget` that charges `max(len(readline result), LINE_CHARGE_FLOOR)` per readline and raises a private `_OverBound` past `MAX_FILE_BYTES` (file-level) or `MAX_CALL_BYTES` (call-level). `load_transcripts` reads each file into a staged contribution through a new `_read_transcript` helper and merges it only once the file was read to its end; `_OverBound` (also raised for the `MAX_CALL_MESSAGES` and `MAX_CALL_COST_STATE_BYTES` bounds) is caught beside `(OSError, EOFError, zlib.error)`, the file is listed in a new `"skipped_for_size"` return key, and after a call-level breach every later file is skipped unopened. `read_node_windows` keeps calling the uncounted `_json_objects`. In `run_record.py`, `_apply_session_usage` prints one count-only stderr line and `_backfill_usage` returns the paths on its `no_match` and success results (the stdout JSON of `backfill-usage`).

**Tech Stack:** Python 3 (stdlib `gzip`, `zlib`, `json`), pytest.

**Spec:** `docs/superpowers/specs/2026-11-28-bound-decompressed-transcript-bytes-design.md` (approved at 651458c).

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `scripts/factory_core/session_usage.py` | Modify | Five bound constants; `_OverBound`, `_Budget`; `_iter_lines(path, budget=None)`; new `_json_lines`; `_json_objects` as an uncounted wrapper; new `_read_transcript`; `load_transcripts` staged merge + `"skipped_for_size"` |
| `scripts/factory_core/run_record.py` | Modify | `_apply_session_usage` (+3 lines: stderr count line) and `_backfill_usage` (+3 lines: `"skipped_for_size"` on `no_match` and success). Nothing else in the file |
| `tests/test_session_usage.py` | Modify | `import gzip`, `import zlib`; new `#656` section appended at the end of the file |
| `tests/test_run_record.py` | Modify | Three tests and one helper inserted directly after `test_backfill_usage_reads_the_gzipped_transcript_store` (not at the end of the file: #238 appends there) |
| `docs/configuration.md` | Modify | One sentence in the session-transcripts row; re-anchor `run_record.py:29,739` and `run_record.py:1274` |
| `docs/run-lifecycle.md` | Modify | One sentence after the #645 backfill paragraph; re-anchor `run_record.py:1138-1395` |
| `docs/features.md` | Modify | Re-anchor `cmd_assemble` (`:1138`) and the three subcommand parsers (`:1600`, `:1605`, `:1610`) |

No other file is touched: no `config/`, `workflows/`, `entrypoint.sh`, `ci.yml`, gate, breaker or budget change, and no new shell test.

Test command used throughout (CI sets `PYTHONPATH=scripts`): `PYTHONPATH=scripts python -m pytest <file> -v`. The commit commands below show the subject and body only; end each message with the attribution trailer your session's instructions specify.

All line numbers below are against `origin/main` at 4e2b1bf, which is identical to this branch's base for the four source/test files. Before starting, confirm: `git diff origin/main -- scripts/factory_core/session_usage.py scripts/factory_core/run_record.py tests/test_session_usage.py tests/test_run_record.py` prints nothing on the fresh `feat/issue-656-*` branch. If `run_record.py` moved (for example #238 merged first), Task 4 Step 2 recomputes every anchor with the `grep -n` command it gives instead of using the numbers printed here.

---

## Task 1: Bound constants and the opt-in byte budget

**Files:**
- Modify: `scripts/factory_core/session_usage.py` (constants after line 37; replace `_iter_lines` and `_json_objects`, lines 93-118)
- Modify: `tests/test_session_usage.py` (imports at lines 3-7; append at end of file)

- [ ] **Step 1: Add the two imports to `tests/test_session_usage.py`**

Change the import block at the top of the file from:

```python
import json
import os
import signal
import sys
from pathlib import Path
```

to:

```python
import gzip
import json
import os
import signal
import sys
import zlib
from pathlib import Path
```

- [ ] **Step 2: Append the first two failing tests at the end of `tests/test_session_usage.py`** (after `test_backfill_matches_file_by_cost`, two blank lines before the section comment)

```python
# --- #656: bounds on one load_transcripts() call ----------------------------------------

def test_bound_constants():
    assert su.MAX_FILE_BYTES == 67108864
    assert su.MAX_CALL_BYTES == 268435456
    assert su.LINE_CHARGE_FLOOR == 64
    assert su.MAX_CALL_MESSAGES == 262144
    assert su.MAX_CALL_COST_STATE_BYTES == 4194304


def test_node_windows_are_not_counted(monkeypatch):
    before = su.read_node_windows(_REAL_LOG, _REAL_NODES)
    monkeypatch.setattr(su, "MAX_FILE_BYTES", 64)    # far below the log's size
    monkeypatch.setattr(su, "MAX_CALL_BYTES", 64)
    assert su.read_node_windows(_REAL_LOG, _REAL_NODES) == before != {}
```

- [ ] **Step 3: Run them and verify they fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_session_usage.py -v -k "bound_constants or node_windows_are_not_counted"`
Expected: `2 failed`, both with `AttributeError: ... has no attribute 'MAX_FILE_BYTES'`.

- [ ] **Step 4: Add the constants to `scripts/factory_core/session_usage.py`**, directly after `MAX_LINE_BYTES = 8 * 1024 * 1024` (line 37):

```python
MAX_LINE_BYTES = 8 * 1024 * 1024
# #656: bounds on one load_transcripts() call. The two byte bounds equal the writer-side
# limits of entrypoint.sh's _keep_run_transcripts, so a kept store never trips them; a
# line counts as at least LINE_CHARGE_FLOOR bytes, which bounds the number of lines.
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_CALL_BYTES = 256 * 1024 * 1024
LINE_CHARGE_FLOOR = 64
MAX_CALL_MESSAGES = 262144
MAX_CALL_COST_STATE_BYTES = 4 * 1024 * 1024
```

(Module constants only — no `config.yaml` key, no environment variable. Tests monkeypatch them like `MAX_LINE_BYTES`; every use below reads the module global at call time, never a default argument, so monkeypatching works.)

- [ ] **Step 5: Replace `_iter_lines` and `_json_objects`** (the whole span from `def _iter_lines(path):` down to, not including, `def read_node_windows`) with:

```python
class _OverBound(Exception):
    """A load_transcripts() bound was exceeded (#656); call_level: later files are
    skipped too."""

    def __init__(self, call_level: bool):
        super().__init__()
        self.call_level = call_level


class _Budget:
    """The bytes one load_transcripts() call has read, in all and from its current file."""

    def __init__(self):
        self.call = 0
        self.file = 0

    def charge(self, n: int) -> None:
        n = max(n, LINE_CHARGE_FLOOR)
        self.call += n
        self.file += n
        if self.call > MAX_CALL_BYTES:
            raise _OverBound(call_level=True)
        if self.file > MAX_FILE_BYTES:
            raise _OverBound(call_level=False)


def _iter_lines(path, budget=None):
    """Each line of a regular file as bytes; a line longer than MAX_LINE_BYTES is skipped
    whole without ever being held in memory. A ``.gz`` path is read through gzip. With a
    budget, every readline result is charged to it, a skipped line's included."""
    cap = MAX_LINE_BYTES
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rb") as fh:
        while True:
            line = fh.readline(cap + 1)
            if not line:
                return
            if budget is not None:
                budget.charge(len(line))
            if len(line) > cap and not line.endswith(b"\n"):
                while line and not line.endswith(b"\n"):
                    line = fh.readline(cap + 1)
                    if line and budget is not None:
                        budget.charge(len(line))
                continue
            yield line


def _json_lines(path, budget=None):
    """(raw line, object) for each line of a file that is a JSON object."""
    for raw in _iter_lines(path, budget):
        try:
            obj = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        if isinstance(obj, dict):
            yield raw, obj


def _json_objects(path):
    """The JSON objects of a file, one per line; any other line is skipped."""
    for _raw, obj in _json_lines(path):
        yield obj
```

`read_node_windows` is not edited: it still calls `_json_objects(log_file)`, which passes no budget, so the Archon log is uncounted and never sees `_OverBound` (it still catches only `OSError`).

- [ ] **Step 6: Run the two tests and the whole file**

Run: `PYTHONPATH=scripts python -m pytest tests/test_session_usage.py -v`
Expected: all pass (`22 passed`: the 20 existing tests plus the two new ones), including `test_hostile_transcripts_never_raise_or_hang`, which is not modified.

- [ ] **Step 7: Commit**

```bash
git add scripts/factory_core/session_usage.py tests/test_session_usage.py
git commit -m "feat(#656): transcript read bounds and opt-in byte budget in _iter_lines"
```

---

## Task 2: Staged, atomic per-file merge with size skips in `load_transcripts`

**Files:**
- Modify: `scripts/factory_core/session_usage.py` (replace `load_transcripts`, originally lines 221-282; add `_read_transcript` before it)
- Modify: `tests/test_session_usage.py` (append after Task 1's tests)

- [ ] **Step 1: Append the helpers, the per-file bound tests and the atomicity/regression tests** at the end of `tests/test_session_usage.py`:

```python
def _gzip(path):
    """path's bytes as path + ".gz" (fixed header mtime); the plain file is removed."""
    gz = path.with_name(path.name + ".gz")
    gz.write_bytes(gzip.compress(path.read_bytes(), mtime=0))
    path.unlink()
    return gz


def _padded(mid, ms, pad):
    """An assistant line with pad extra bytes in an ignored key, far under the line cap."""
    return dict(_a(mid, ms), pad="x" * pad)


def _rel(root, path):
    return path.relative_to(root).as_posix()


def _assert_consistent(t):
    """Every file a record names was kept: what _chains needs not to raise KeyError."""
    for rec in t["messages"].values():
        assert rec["files"] <= set(t["files"])


@pytest.mark.parametrize("compress", [True, False], ids=["gz", "plain"])
def test_file_over_the_file_bound_is_skipped_and_later_files_load(tmp_path, monkeypatch,
                                                                  compress):
    monkeypatch.setattr(su, "MAX_FILE_BYTES", 4096)
    big = _write(tmp_path, "p/a.jsonl", [_padded(f"a{i}", T0 + i, 500) for i in range(20)])
    small = _write(tmp_path, "p/b.jsonl", [_a("b1", T0 + 100)])
    if compress:
        big, small = _gzip(big), _gzip(small)
    t = _load(tmp_path)
    assert list(t["files"]) == [_rel(tmp_path, small)]
    assert t["skipped_for_size"] == [_rel(tmp_path, big)]
    assert list(t["messages"]) == ["b1"]
    _assert_consistent(t)


def _readable_b(root):
    """p/b.jsonl: message s1 at T0 + 500 and the cost-state line backfill matches on. A
    skipped p/a.* (sorted first) that also holds s1, at T0, must leave no trace."""
    _write(root, "p/b.jsonl", [_a("s1", T0 + 500), _cs(T0, 1.0, {OPUS: _mu(10, 100, 0, 0, 1.0)})])


def _assert_only_b_kept(t):
    assert list(t["files"]) == ["p/b.jsonl"]
    assert t["messages"]["s1"]["files"] == {"p/b.jsonl"}
    assert t["messages"]["s1"]["ts"] == T0 + 500
    _assert_consistent(t)
    su.attribute(t, {"n": (T0 - 1, T0 + 1000)}, {"n": (10, 100)}, {"n": 1.0})   # no KeyError
    assert set(su.backfill(t, {"n": {"cost_usd": 1.0, "tokens": (10, 100)}})) == {"n"}


def test_size_skipped_file_leaves_nothing_in_the_result(tmp_path, monkeypatch):
    monkeypatch.setattr(su, "MAX_FILE_BYTES", 4096)
    _write(tmp_path, "p/a.jsonl",
           [_a("s1", T0)] + [_padded(f"a{i}", T0 + i, 500) for i in range(20)])
    _readable_b(tmp_path)
    t = _load(tmp_path)
    assert t["skipped_for_size"] == ["p/a.jsonl"]
    _assert_only_b_kept(t)


@pytest.mark.parametrize("how", ["truncated", "corrupted"])
def test_unreadable_gz_sharing_a_message_leaves_nothing_in_the_result(tmp_path, how):
    # Regression (#656): on the old code the shared record kept a.jsonl.gz in its files
    # and its earlier ts, and _chains raised KeyError.
    plain = _write(tmp_path, "p/a.jsonl",
                   [_a("s1", T0)] + [_padded(f"a{i}", T0 + i, 200) for i in range(2000)])
    data = bytearray(gzip.compress(plain.read_bytes(), mtime=0))
    plain.unlink()
    if how == "truncated":
        data = data[: len(data) // 2]
    else:
        data[len(data) // 2] ^= 0xFF
    gz = tmp_path / "p" / "a.jsonl.gz"
    gz.write_bytes(bytes(data))
    lines = su._iter_lines(gz)
    assert b'"s1"' in next(lines)   # the shared line is read before the stream fails
    lines.close()
    with pytest.raises((OSError, EOFError, zlib.error)):
        list(su._iter_lines(gz))
    _readable_b(tmp_path)
    t = _load(tmp_path)
    _assert_only_b_kept(t)   # first: on the old code this is the KeyError in _chains
    assert t["skipped_for_size"] == []
```

- [ ] **Step 2: Append the call-level bound tests and the line-floor test**:

```python
def _record_opens(monkeypatch):
    opened = []
    real = su._iter_lines

    def spy(path, *args, **kwargs):
        opened.append(Path(path).name)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(su, "_iter_lines", spy)
    return opened


def test_call_byte_bound_skips_the_crossing_file_and_never_opens_later_ones(tmp_path,
                                                                           monkeypatch):
    paths = [_write(tmp_path, f"p/{n}.jsonl", [_padded(f"{n}{i}", T0 + i, 400) for i in range(5)])
             for n in "abcd"]
    sizes = [p.stat().st_size for p in paths]   # every line is over the floor: bytes = size
    monkeypatch.setattr(su, "MAX_CALL_BYTES", sizes[0] + sizes[1] // 2)
    opened = _record_opens(monkeypatch)
    t = _load(tmp_path)
    assert list(t["files"]) == ["p/a.jsonl"]
    assert t["skipped_for_size"] == ["p/b.jsonl", "p/c.jsonl", "p/d.jsonl"]
    assert opened == ["a.jsonl", "b.jsonl"]
    assert sorted(t["messages"]) == [f"a{i}" for i in range(5)]


def test_message_pair_bound_counts_pairs_not_distinct_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(su, "MAX_CALL_MESSAGES", 7)   # 3 distinct ids, 3 pairs per file
    for n in "abcd":
        _write(tmp_path, f"p/{n}.jsonl", [_a(f"m{i}", T0 + i) for i in range(3)])
    opened = _record_opens(monkeypatch)
    t = _load(tmp_path)
    assert list(t["files"]) == ["p/a.jsonl", "p/b.jsonl"]
    assert t["skipped_for_size"] == ["p/c.jsonl", "p/d.jsonl"]
    assert opened == ["a.jsonl", "b.jsonl", "c.jsonl"]
    assert all(rec["files"] == {"p/a.jsonl", "p/b.jsonl"} for rec in t["messages"].values())


def test_cost_state_bound_counts_only_the_line_each_file_keeps(tmp_path, monkeypatch):
    monkeypatch.setattr(su, "MAX_CALL_COST_STATE_BYTES", 1000)
    short = _cs(T0, 1.0, {OPUS: _mu(1, 1, 0, 0, 1.0)})
    long_ = dict(short, pad="x" * 5000)
    _write(tmp_path, "p/a.jsonl", [_a("a1", T0), long_, short])   # keeps the short one
    _write(tmp_path, "p/b.jsonl", [_a("b1", T0), long_])
    _write(tmp_path, "p/c.jsonl", [_a("c1", T0), short])
    opened = _record_opens(monkeypatch)
    t = _load(tmp_path)
    assert list(t["files"]) == ["p/a.jsonl"]
    assert t["files"]["p/a.jsonl"]["cost_state"] == short
    assert t["skipped_for_size"] == ["p/b.jsonl", "p/c.jsonl"]
    assert opened == ["a.jsonl", "b.jsonl"]


def test_line_floor_bounds_a_file_of_empty_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(su, "MAX_FILE_BYTES", 100 * su.LINE_CHARGE_FLOOR)
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "over.jsonl").write_text("\n" * 101)    # 101 bytes, charged 6464
    (tmp_path / "p" / "under.jsonl").write_text("\n" * 100)   # charged exactly the bound
    t = _load(tmp_path)
    assert list(t["files"]) == ["p/under.jsonl"]
    assert t["skipped_for_size"] == ["p/over.jsonl"]
```

- [ ] **Step 3: Append the shipped-bounds tests** (compression bomb under the `SIGALRM` guard pattern of `test_hostile_transcripts_never_raise_or_hang`, and a normal gzipped store — two real fixture runs and the synthetic resumed chain — read exactly like its plain files):

```python
def test_compression_bomb_is_skipped_at_the_shipped_bounds(tmp_path):
    (tmp_path / "p").mkdir()
    with gzip.open(tmp_path / "p" / "bomb.jsonl.gz", "wb") as fh:   # ~70 MiB of blank lines
        for _ in range(1120):
            fh.write(b" " * 65535 + b"\n")
    assert (tmp_path / "p" / "bomb.jsonl.gz").stat().st_size < 1024 * 1024
    _write(tmp_path, "p/ok.jsonl", [_a("ok", T0)])
    previous = signal.signal(signal.SIGALRM,
                             lambda *_: (_ for _ in ()).throw(TimeoutError()))
    signal.alarm(60)
    try:
        t = _load(tmp_path)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    assert t["skipped_for_size"] == ["p/bomb.jsonl.gz"]
    assert list(t["files"]) == ["p/ok.jsonl"]
    assert list(t["messages"]) == ["ok"]


def _without_gz(t):
    strip = lambda rel: rel[:-3] if rel.endswith(".gz") else rel
    return {"files": {strip(r): info for r, info in t["files"].items()},
            "messages": {m: dict(rec, files={strip(r) for r in rec["files"]})
                         for m, rec in t["messages"].items()},
            "harness_version": t["harness_version"]}


@pytest.mark.parametrize("fixture", ["plan-run", "subagent-run", "resumed"])
def test_normal_gzipped_store_reads_like_the_plain_files(tmp_path, fixture):
    import shutil
    plain = tmp_path / "plain"
    if fixture == "resumed":
        _resumed_chain(plain, last_cost_state=True)
    else:
        shutil.copytree(FIXTURES / fixture, plain)
    store = tmp_path / "store"
    shutil.copytree(plain, store)
    for f in list(store.rglob("*.jsonl")):
        _gzip(f)
    want, got = _load(plain), _load(store)
    assert want["skipped_for_size"] == got["skipped_for_size"] == []
    assert got["files"] and _without_gz(got) == _without_gz(want)
```

No test asserts a duration or a memory figure; the bomb file is ~70 MiB decompressed but under 1 MB on disk (asserted) and is generated at test time.

- [ ] **Step 4: Run the new tests and verify they fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_session_usage.py -v`
Expected: `13 failed, 22 passed`. Every new test except `test_bound_constants` and `test_node_windows_are_not_counted` fails, with `KeyError: 'skipped_for_size'` or, for the call-level and floor tests that check `files` first, an `AssertionError` on `files`. Two of the failures are the regression tests for the existing defect: `test_unreadable_gz_sharing_a_message_leaves_nothing_in_the_result[truncated]` and `[corrupted]`. Those two must fail inside `_assert_only_b_kept` (on `rec["files"] == {"p/b.jsonl"}`, the partial merge the old code leaves behind), not on the missing `skipped_for_size` key: that is why the helper is called before the key is read.

- [ ] **Step 5: Implement.** In `scripts/factory_core/session_usage.py`, replace the whole of `def load_transcripts(...)` (down to, not including, `def _is_subagent_file`) with the new `_read_transcript` helper followed by the new `load_transcripts`:

```python
def _read_transcript(path, rel: str, budget: _Budget, pairs: int) -> tuple:
    """One file's contribution, read to its end before anything is merged (#656):
    (info, {message_id: record without "files"}, its version Counter, the length of the
    cost-state line it keeps). pairs: the (file, message) pairs already merged. Raises
    _OverBound past a bound."""
    info = {"ids": [], "cost_state": None, "first_ts": None, "first_user": None,
            "assistant_lines": 0, "sidechain_lines": 0}
    staged: dict = {}
    versions: Counter = Counter()
    cost_state_len = 0
    for lineno, (raw, obj) in enumerate(_json_lines(path, budget)):
        ts = iso_to_ms(obj.get("timestamp"))
        if ts is not None and (info["first_ts"] is None or ts < info["first_ts"]):
            info["first_ts"] = ts
        kind = obj.get("type")
        if kind == "cost-state":
            info["cost_state"], cost_state_len = obj, len(raw)
            continue
        if kind == "user":
            if info["first_user"] is None:
                text = _user_text(obj)
                if text:
                    info["first_user"] = (ts, text)
            continue
        msg = obj.get("message") if kind == "assistant" else None
        if not isinstance(msg, dict) or msg.get("model") == SYNTHETIC_MODEL:
            continue
        if isinstance(obj.get("version"), str):
            versions[obj["version"]] += 1
        side = obj.get("isSidechain") is True
        info["assistant_lines"] += 1
        info["sidechain_lines"] += side
        mid = msg.get("id")
        if not isinstance(mid, str) or not mid:
            mid = f"\x00{rel}#{lineno}"
        rec = staged.get(mid)
        if rec is None:
            info["ids"].append(mid)
            if pairs + len(info["ids"]) > MAX_CALL_MESSAGES:
                raise _OverBound(call_level=True)
            model = msg.get("model")
            staged[mid] = {"ts": ts, "model": model if isinstance(model, str) else "",
                           "usage": _message_usage(msg.get("usage")), "sidechain": side}
        elif ts is not None and (rec["ts"] is None or ts < rec["ts"]):
            rec["ts"] = ts
    return info, staged, versions, cost_state_len


def load_transcripts(root, started_at=None) -> dict:
    """Read every transcript of requirement 3's input set under root.

    Returns {"files": {rel: info}, "messages": {message_id: record}, "harness_version",
    "skipped_for_size"}. A message is kept once per message.id across all files (its
    earliest timestamp, its model, its usage, the files it appears in); a line with no
    message.id is its own record. harness_version is the most common CLI `version` on
    assistant lines, or None. A file is merged only once read to its end, so nothing of a
    skipped file is kept (#656): an unreadable one is left out; one over MAX_FILE_BYTES,
    or the one during which MAX_CALL_BYTES, MAX_CALL_MESSAGES or MAX_CALL_COST_STATE_BYTES
    is exceeded and every later file (never opened), is listed in skipped_for_size, the
    sorted relative paths only.
    """
    root = pathlib.Path(root)
    files: dict = {}
    messages: dict = {}
    versions: Counter = Counter()
    skipped: list = []
    budget = _Budget()
    pairs = cost_state_bytes = 0
    exhausted = False
    for path in _transcript_files(root, started_at):
        rel = path.relative_to(root).as_posix()
        if exhausted:
            skipped.append(rel)
            continue
        budget.file = 0
        try:
            info, staged, file_versions, cost_state_len = _read_transcript(
                path, rel, budget, pairs)
            if cost_state_bytes + cost_state_len > MAX_CALL_COST_STATE_BYTES:
                raise _OverBound(call_level=True)
        except (OSError, EOFError, zlib.error):
            continue
        except _OverBound as over:
            skipped.append(rel)
            exhausted = over.call_level
            continue
        for mid in info["ids"]:
            new = staged[mid]
            rec = messages.get(mid)
            if rec is None:
                rec = messages[mid] = dict(new, files=set())
            elif new["ts"] is not None and (rec["ts"] is None or new["ts"] < rec["ts"]):
                rec["ts"] = new["ts"]
            rec["files"].add(rel)
        files[rel] = info
        versions.update(file_versions)
        pairs += len(info["ids"])
        cost_state_bytes += cost_state_len
    version = versions.most_common(1)[0][0] if versions else None
    return {"files": files, "messages": messages, "harness_version": version,
            "skipped_for_size": sorted(skipped)}
```

Points the implementation must keep (each is asserted by a test above or by an existing test):
- `files[rel]` (`info`) keeps exactly its six keys; the cost-state line length is returned separately, never stored in `info`.
- A merged message record keeps its key order `ts, model, usage, sidechain, files` and its values come from the first merged file holding the id (same as today when nothing is skipped); `messages` insertion order is unchanged.
- The `\x00{rel}#{lineno}` fallback id still uses the per-file index of JSON-object lines (`enumerate(_json_lines(...))` yields the same indices `enumerate(_json_objects(...))` did).
- The pair bound is checked on every new id while the file is read; the cost-state bound once, after the file was read to its end and before the merge. Both are call-level.
- Bytes read from a skipped file stay counted in `budget.call` (no rollback); `budget.file` is reset before each file.
- `"skipped_for_size"` is always present, sorted, paths only; unreadable files (`OSError`, `EOFError`, `zlib.error`) are dropped silently and never listed.

- [ ] **Step 6: Run the file again**

Run: `PYTHONPATH=scripts python -m pytest tests/test_session_usage.py -v`
Expected: `35 passed`.

- [ ] **Step 7: Run the run-record tests, which use `load_transcripts` through `attribute()`/`backfill()`**

Run: `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -q`
Expected: all pass (no change in count; `attribute()`, `backfill()` and every run-record shape are unchanged).

- [ ] **Step 8: Commit**

```bash
git add scripts/factory_core/session_usage.py tests/test_session_usage.py
git commit -m "fix(#656): merge each transcript only after a full read; skip and list over-bound files

A truncated or mid-stream-corrupted .gz sharing a message.id with a readable
file used to leave a record naming a file absent from 'files', so _chains
raised KeyError and the run lost all session figures. Every skip cause now
drops the file's whole contribution."
```

---

## Task 3: Report size skips in `run_record.py`

**Files:**
- Modify: `scripts/factory_core/run_record.py` (`_apply_session_usage` at line 686; `_backfill_usage` at lines 1510-1540) — nothing else in the file; `cmd_backfill_usage` is not edited
- Modify: `tests/test_run_record.py` (insert after `test_backfill_usage_reads_the_gzipped_transcript_store`, which ends at line 3564)

- [ ] **Step 1: Insert the helper and three failing tests** between `test_backfill_usage_reads_the_gzipped_transcript_store` and `test_backfill_usage_failed_backup_write_leaves_no_partial_copy` (keep two blank lines on each side):

```python
def _u_oversized(tx, monkeypatch):
    """A transcript under tx over a monkeypatched MAX_FILE_BYTES (#656); the fixture files
    of _u_transcripts are each far under it."""
    from factory_core import session_usage
    monkeypatch.setattr(session_usage, "MAX_FILE_BYTES", 8192)
    _u_write(tx / "-workspace-target" / "zz-oversized.jsonl",
             [dict(_u_assistant(f"z{i}", _U_T0 + i, _OPUS, 1, 1), pad="x" * 500)
              for i in range(40)])
    return "-workspace-target/zz-oversized.jsonl"


def test_apply_session_usage_reports_size_skips_without_paths(tmp_path, monkeypatch, capsys):
    tx = _u_transcripts(tmp_path / "tx")
    kw = dict(transcripts_dir=str(tx), started_at=None, archon_log_file=None,
              clone_dir=str(tmp_path), hosting=None)
    rr._apply_session_usage([], {}, **kw)
    assert "over the size bound" not in capsys.readouterr().err
    _u_oversized(tx, monkeypatch)
    rr._apply_session_usage([], {}, **kw)
    err = capsys.readouterr().err
    assert ("run-record: 1 session transcript file(s) skipped (over the size bound)\n"
            in err)
    assert "zz-oversized" not in err


def test_backfill_usage_reports_files_skipped_for_size(tmp_path, monkeypatch, capsys):
    _u_durable(monkeypatch, tmp_path, _u_old_record())
    tx = _u_transcripts(tmp_path / "tx")
    rel = _u_oversized(tx, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["run_record", "backfill-usage", "--run-id", "3ff088b6",
                                      "--transcripts-dir", str(tx)])
    rr.main()
    report = json.loads(capsys.readouterr().out)
    assert report["backfilled_nodes"] == ["code-review", "conformance"]
    assert report["skipped_for_size"] == [rel]


def test_backfill_usage_skipped_for_size_on_no_match_not_on_no_record(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "SCHEDULER_STATE_DIR", tmp_path / "state")
    assert "skipped_for_size" not in rr._backfill_usage("nope", str(tmp_path))
    record = _u_old_record()
    for n in record["nodes"]:
        n["cost_usd"] = 42.0
    _u_durable(monkeypatch, tmp_path, record)
    result = rr._backfill_usage("3ff088b6", str(_u_transcripts(tmp_path / "tx")))
    assert result["reason"] == "no_match"
    assert result["skipped_for_size"] == []
```

- [ ] **Step 2: Run them and verify they fail**

Run: `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v -k "skipped_for_size or size_skips"`
Expected: `3 failed, 258 deselected` (do not filter on bare `size`: it also matches the existing `test_assemble_without_usable_log_synthesizes_nothing` cases) — `test_apply_session_usage_reports_size_skips_without_paths` (the stderr line is missing), `test_backfill_usage_reports_files_skipped_for_size` and `test_backfill_usage_skipped_for_size_on_no_match_not_on_no_record` (`KeyError: 'skipped_for_size'`).

- [ ] **Step 3: Edit `_apply_session_usage`.** Inside the existing `try`, directly after `transcripts = session_usage.load_transcripts(transcripts_dir, started_at)` (line 686), add three lines so the block reads:

```python
    try:   # never lose the record over the transcripts
        if transcripts_dir:
            transcripts = session_usage.load_transcripts(transcripts_dir, started_at)
            if transcripts["skipped_for_size"]:
                print(f"run-record: {len(transcripts['skipped_for_size'])} session transcript "
                      "file(s) skipped (over the size bound)", file=sys.stderr)
    except Exception as exc:
        print(f"run-record: session usage not read ({type(exc).__name__})", file=sys.stderr)
```

A count only — no path, no contents. The existing diagnostics are unchanged; no key is added to `run-record.json` or `runs.jsonl`.

- [ ] **Step 4: Edit `_backfill_usage`.** Replace

```python
    found = session_usage.backfill(
        session_usage.load_transcripts(transcripts_dir),
```

with

```python
    transcripts = session_usage.load_transcripts(transcripts_dir)
    found = session_usage.backfill(
        transcripts,
```

then replace

```python
        return {"run_id": run_id, "backfilled_nodes": [], "reason": "no_match"}
```

with

```python
        return {"run_id": run_id, "backfilled_nodes": [], "reason": "no_match",
                "skipped_for_size": transcripts["skipped_for_size"]}
```

and replace the final

```python
    return {"run_id": run_id, "backfilled_nodes": sorted(found)}
```

with

```python
    return {"run_id": run_id, "backfilled_nodes": sorted(found),
            "skipped_for_size": transcripts["skipped_for_size"]}
```

The `no_record` return is left as is (no `skipped_for_size`). `cmd_backfill_usage` already prints this dict as its one-line stdout JSON, so the operator report gains the field without editing it.

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=scripts python -m pytest tests/test_run_record.py -v -k "skipped_for_size or size_skips or backfill_usage"`
Expected: `8 passed, 253 deselected` (the three new tests and the five existing `backfill_usage` tests, unmodified).

Run: `PYTHONPATH=scripts python -m pytest tests/test_run_record.py tests/test_session_usage.py -q`
Expected: all pass (`296 passed`: 261 + 35).

- [ ] **Step 6: Confirm the edit footprint** (the docs anchors in Task 4 depend on it)

Run: `git diff --stat HEAD -- scripts/factory_core/run_record.py`
Expected: `1 file changed, 9 insertions(+), 3 deletions(-)` — net +3 lines after line 686 and net +3 more in `_backfill_usage`.

- [ ] **Step 7: Commit**

```bash
git add scripts/factory_core/run_record.py tests/test_run_record.py
git commit -m "feat(#656): report transcripts skipped for size in assemble stderr and backfill-usage JSON"
```

---

## Task 4: Docs — one sentence each, and re-anchor shifted `run_record.py` citations

**Files:**
- Modify: `docs/configuration.md` (row at line 519; citations at lines 527 and 756)
- Modify: `docs/run-lifecycle.md` (citation at line 1025; sentence after line 1103)
- Modify: `docs/features.md` (line 139)

- [ ] **Step 1: Add the two sentences**

In `docs/configuration.md`, session-transcripts row (line 519), replace `is not kept (one WARNING line). Getting` with:

```
is not kept (one WARNING line). The reader applies the same bounds to the bytes it reads (#656): a file over 64 MiB, and from the file that crosses 256 MiB in one read every later file, is skipped whole and reported by one stderr line in `run-record assemble` or by the `skipped_for_size` field of `backfill-usage`'s JSON; the nodes it held fall back to `transcript_messages`, `archon` or `none`. Getting
```

In `docs/run-lifecycle.md`, replace the two lines (1102-1103)

```
   record is replaced through a rename. It is an operator step; `runs.jsonl` is never
   rewritten.
```

with

```
   record is replaced through a rename. It is an operator step; `runs.jsonl` is never
   rewritten. Both readers apply the writer's 64 MiB / 256 MiB bounds to the bytes they
   read, plus bounds on the messages and `cost-state` lines one read keeps (#656): a file
   over a bound is skipped whole and reported by one stderr line (`run-record: N session
   transcript file(s) skipped (over the size bound)`) in assemble, or by the
   `skipped_for_size` field of `backfill-usage`'s JSON; a node whose transcript was
   skipped falls back to `transcript_messages`, `archon` or `none`.
```

(The next line, `3. \`post_cost_report\` ...`, is unchanged.)

- [ ] **Step 2: Print the post-edit anchor lines**

Run:

```bash
grep -n '^SEQ_URL\|MODEL_PROXY_LEDGER_PATH\|^def cmd_assemble\|loops = adapter.get\|failed to write durable record\|add_parser("issue-economics\|add_parser("backfill-economics\|add_parser("backfill-usage' scripts/factory_core/run_record.py
```

Expected (after Task 3 on 4e2b1bf):

```
29:SEQ_URL = os.environ.get("SEQ_URL", "http://seq:5341")
900:    os.environ.get("MODEL_PROXY_LEDGER_PATH", "/var/lib/dark-factory/request-ledger.jsonl")
1144:def cmd_assemble(args) -> None:
1280:        loops = adapter.get(args.clone_dir, "loops") or []
1400:            print(f"run-record: failed to write durable record {durable_path}: {exc}", file=sys.stderr)
1612:    ie = sub.add_parser("issue-economics", help="Read-only cross-run rollup for an issue")
1617:    be = sub.add_parser("backfill-economics", help="Degrade-only historical harness_economics recompute")
1622:    bu = sub.add_parser("backfill-usage",
```

`1400` is the last line of `cmd_assemble`. If any number differs, use the printed numbers in Step 3 instead of the ones written there. If #238 merged first, the "Replace" strings of Step 3 differ too (its branch already rewrites them): replace whatever number each citation then carries.

- [ ] **Step 3: Re-anchor every `run_record.py` citation below line 686** (the reference checker does not catch a citation that still resolves to some line). Each target is the line the citation describes; several were already stale on main:

| Doc | Replace | With | Target |
|---|---|---|---|
| `docs/configuration.md:527` | `` `run_record.py:29,739` `` | `` `run_record.py:29,900` `` | `SEQ_URL` and the `MODEL_PROXY_LEDGER_PATH` read (739 was a docstring line) |
| `docs/configuration.md:756` | `` `run_record.py:1274` `` | `` `run_record.py:1280` `` | `loops = adapter.get(args.clone_dir, "loops")` |
| `docs/run-lifecycle.md:1025` | `` `scripts/factory_core/run_record.py:1138-1395` `` | `` `scripts/factory_core/run_record.py:1144-1400` `` | `cmd_assemble`, first to last line |
| `docs/features.md:139` | `` `cmd_assemble` (`:1138`) `` | `` `cmd_assemble` (`:1144`) `` | `def cmd_assemble` |
| `docs/features.md:139` | `` (`run_record.py:1600`, `:1605`, `:1610`) `` | `` (`run_record.py:1612`, `:1617`, `:1622`) `` | the three subcommand parsers |

`cmd_record` (`:125`) and `emit_health_event` (`:174`) in `docs/features.md` are above the first edit and stay. No doc cites `session_usage.py` by line. Confirm nothing else cites a shifted line:

Run: `grep -rnE 'run_record\.py[`]?[ (]*[`]?:?[0-9]' docs/*.md README.md | grep -v '^docs/codeindex-hotspots.md'`
Expected: exactly four lines — `docs/configuration.md:527`, `docs/configuration.md:756`, `docs/features.md:139` and `docs/run-lifecycle.md:1025` — each now carrying the new numbers from the table (the `cmd_assemble` (`:1144`) citation is on the same `features.md:139` line).

- [ ] **Step 4: Run the docs checks CI runs**

Run: `python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md`
Expected: `Docs reference check passed for 4 doc(s).`

Run: `PYTHONPATH=scripts python -m pytest tests/ -q -k "doc or features or configuration or lifecycle"`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add docs/configuration.md docs/run-lifecycle.md docs/features.md
git commit -m "docs(#656): reader-side transcript bounds; re-anchor run_record.py citations"
```

---

## Task 5: Full verification

**Files:** none modified.

- [ ] **Step 1: Full Python suite**

Run: `PYTHONPATH=scripts python -m pytest tests/ -q`
Expected: no failure introduced by this branch — 18 more tests passed than on `origin/main` (15 in `tests/test_session_usage.py`, 3 in `tests/test_run_record.py`), and every pre-existing test, including `test_hostile_transcripts_never_raise_or_hang`, passes unmodified.

- [ ] **Step 2: Scope check** (two-dot diff, so commits main merged independently are not counted as this branch's)

Run: `git diff --stat origin/main HEAD`
Expected: exactly the spec and plan docs already on the branch plus these seven files: `scripts/factory_core/session_usage.py`, `scripts/factory_core/run_record.py`, `tests/test_session_usage.py`, `tests/test_run_record.py`, `docs/configuration.md`, `docs/run-lifecycle.md`, `docs/features.md`. No `config/`, `workflows/`, `entrypoint.sh`, `.github/`, `deploy/` or gate file.

- [ ] **Step 3: Safety re-check** (spec "Safety": nothing outside run-record code reads the affected fields)

Run: `git grep -nE 'skipped_for_size|cost_usd_deduplicated|cost_usd_session_delta|usage_reconciled|unattributed_sessions' -- scripts scheduler.sh entrypoint.sh workflows commands config | grep -v 'scripts/factory_core/\(run_record\|session_usage\)\.py'`
Expected: no output.

---

## Merge notes (from the spec)

- Both source files are blast-radius hotspots (`docs/codeindex-hotspots.md`), so expect a blast-radius hold and a human signature at merge.
- Operator decision at plan approval (2026-11-28): the issue carries `Depends on: #238`, so #238 merges first at its reviewed head and this ticket resolves the `docs/features.md` row on top of it (use the "If #238 merged first" instructions in Task 4). The reviewer's original note follows. No `Depends on:` line was recommended. Checked against #238's branch at 64e56d9 by a trial merge: `run_record.py`, `tests/test_run_record.py` and `docs/run-lifecycle.md` merge automatically and both test suites pass together. **One content conflict is expected: `docs/features.md` line 139**, the run-records table row, which #238 also rewrites (its own citations plus new text). Whichever merges second resolves it by taking the other branch's row text and then re-anchoring every `run_record.py` citation in the three docs with the Task 4 Step 2 command (for the merged file, also `^def cmd_record`, `^def emit_health_event` and `^def cmd_annotate_failure`, which #238 moves). After that merge the reference checker still passes with stale numbers, so the re-anchoring must be done by hand.
- Out of scope (spec "Open Questions"): a `timeout` around `run-record assemble`, `O_NONBLOCK | O_NOFOLLOW` opens, and a bound on the number of files.
