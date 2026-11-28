"""factory_core.session_usage (#645): node windows from Archon's log, per-node usage from
Claude Code session transcripts, cost-state figures and reconciliation."""
import gzip
import json
import os
import signal
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core import session_usage as su

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "session_usage"
T0 = 1_795_806_798_000
OPUS, HAIKU, SONNET = "claude-opus-5-5", "claude-haiku-4-5-20251001", "claude-sonnet-5"


def _a(mid, ms, model=OPUS, tin=10, tout=100, cr=0, cc=0, side=False):
    return {"type": "assistant", "timestamp": su.ms_to_iso(ms), "isSidechain": side,
            "version": "2.1.282",
            "message": {"id": mid, "model": model,
                        "usage": {"input_tokens": tin, "output_tokens": tout,
                                  "cache_read_input_tokens": cr,
                                  "cache_creation_input_tokens": cc}}}


def _u(text, ms):
    return {"type": "user", "timestamp": su.ms_to_iso(ms), "isSidechain": False,
            "message": {"role": "user", "content": text}}


def _mu(tin, tout, cr, cc, cost):
    return {"inputTokens": tin, "outputTokens": tout, "cacheReadInputTokens": cr,
            "cacheCreationInputTokens": cc, "costUSD": cost}


def _cs(start, total, usage):
    return {"type": "cost-state", "startTime": start, "totalCostUSD": total, "modelUsage": usage}


def _write(root, rel, lines):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join((l if isinstance(l, str) else json.dumps(l)) + "\n" for l in lines))
    return path


def _load(root):
    return su.load_transcripts(root, started_at=None)


# --- requirement 2: node windows -------------------------------------------------------

_REAL_LOG = FIXTURES / "archon-log-plan-run.txt"
_REAL_NODES = ("parse-intent", "fetch-issue", "digest-comments", "setup-refine-branch",
               "bench-mode-probe", "plan")


def _log(**kw):
    line = {"level": 30, "pid": 1, "hostname": "scrubbed", "module": su.LOG_MODULE}
    line.update(kw)
    return json.dumps(line)


def test_windows_from_real_log_cut():
    w = su.read_node_windows(_REAL_LOG, _REAL_NODES)
    assert w["parse-intent"] == (1795810393681, 1795810397203)   # model node
    assert w["fetch-issue"] == (1795810397206, 1795810398811)    # bash node
    assert w["setup-refine-branch"] == (1795810398816, 1795810399228)
    assert w["bench-mode-probe"] == (1795810398816, 1795810398833)
    assert "digest-comments" not in w                             # skipped: no started line
    # plan started and the cut has no later line and no finished line: upper bound.
    assert w["plan"] == (1795810399585, 1795810399585)


def test_windows_ignore_bad_lines_and_end_failed_node(tmp_path):
    hostile = [
        _log(time=1795810393000, nodeId="fetch-issue", msg="dag_node_started"),      # before run
        _log(time=1795810393690, nodeId="parse-intent", msg="dag_node_started",
             module="workflow.other"),                                                 # wrong module
        _log(time="1795810393690", nodeId="parse-intent", msg="dag_node_started"),     # str time
        _log(time=1795810393690.5, nodeId="parse-intent", msg="dag_node_started"),     # float time
    ]
    tail = [
        _log(time=1795810396000, nodeId="parse-intent", msg="dag_node_started"),       # 2nd start
        _log(time=1795810399590, nodeId="not-a-node", msg="dag_node_started"),         # unknown id
        _log(time=1795810399590, nodeId=["plan"], msg="dag_node_started"),             # non-str id
        # Shape of Archon 0.3.10 dag-executor.ts's bash-node failure log line.
        _log(level=50, time=1795810400100, nodeId="plan", nodeType="bash", isTimeout=False,
             msg="dag_node_failed"),
        _log(time=1795810400500, nodeCount=49, anyCompleted=True, anyFailed=True,
             msg="dag_workflow_finished"),
        _log(time=1795810400900, nodeId="fetch-issue", msg="dag_node_completed"),      # after run
    ]
    real = _REAL_LOG.read_text().splitlines()
    log = tmp_path / "out.log"
    log.write_text("\n".join(hostile + real + tail) + "\n")
    w = su.read_node_windows(log, _REAL_NODES)
    assert w["parse-intent"] == (1795810393681, 1795810397203)
    assert w["fetch-issue"] == (1795810397206, 1795810398811)
    assert w["plan"] == (1795810399585, 1795810400100)
    assert "not-a-node" not in w


def test_window_of_unfinished_node_ends_at_next_accepted_line_or_finished(tmp_path):
    log = tmp_path / "out.log"
    log.write_text("\n".join([
        _log(time=T0, msg="dag_workflow_starting"),
        _log(time=T0 + 10, nodeId="a", msg="dag_node_started"),
        _log(time=T0 + 20, nodeId="b", msg="dag_node_started"),
        _log(time=T0 + 30, nodeId="b", msg="dag_node_completed"),
        _log(time=T0 + 40, nodeId="c", msg="dag_node_started"),
        _log(time=T0 + 90, msg="dag_workflow_finished"),
    ]) + "\n")
    w = su.read_node_windows(log, ["a", "b", "c"])
    assert w["a"] == (T0 + 10, T0 + 20)
    assert w["c"] == (T0 + 40, T0 + 90)


def test_windows_absent_log(tmp_path):
    assert su.read_node_windows(None, ["a"]) == {}
    assert su.read_node_windows(tmp_path / "missing.log", ["a"]) == {}
    assert su.read_node_windows(tmp_path, ["a"]) == {}


# --- requirement 3: messages, chains, attribution --------------------------------------

def test_three_block_message_counted_once(tmp_path):
    line = _a("msg_1", T0 + 10, tin=3, tout=7, cr=100, cc=20)
    _write(tmp_path, "proj/s1.jsonl", [_u("hi", T0), line, line, line])
    t = _load(tmp_path)
    assert list(t["messages"]) == ["msg_1"]
    res = su.attribute(t, {"implement": (T0, T0 + 100)}, {"implement": (3, 7)}, {})
    node = res["nodes"]["implement"]
    assert node["usage_source"] == "transcript_messages"
    assert node["models"] == [{"model": OPUS, "input_tokens": 3, "output_tokens": 7,
                               "cache_read_input_tokens": 100,
                               "cache_creation_input_tokens": 20}]
    assert node["usage_reconciled"] is True
    assert res["unattributed_sessions"] == []


_WINDOWS = {"conformance": (T0 + 1000, T0 + 1999), "code-review": (T0 + 2000, T0 + 2999),
            "revise-advisory": (T0 + 4000, T0 + 4999)}
_TOKENS = {n: (20, 200) for n in _WINDOWS}


def _resumed_chain(root, *, cost_states=True, last_cost_state=False):
    msgs = [_a(f"c{i}", T0 + ms, cr=500, cc=25) for i, ms in
            enumerate((1100, 1200, 2100, 2200, 4100, 4200), start=1)]
    cs_a = _cs(T0 + 1000, 1.0, {OPUS: _mu(20, 200, 1000, 50, 0.9), HAIKU: _mu(5, 5, 0, 0, 0.1)})
    cs_b = _cs(T0 + 1000, 2.1, {OPUS: _mu(40, 400, 3000, 80, 2.0), HAIKU: _mu(5, 5, 0, 0, 0.1)})
    cs_c = _cs(T0 + 1000, 3.1, {OPUS: _mu(60, 600, 4000, 130, 3.0), HAIKU: _mu(5, 5, 0, 0, 0.1)})
    _write(root, "p/a.jsonl", [_u("conformance prompt", T0 + 1001)] + msgs[:2]
           + ([cs_a] if cost_states else []))
    _write(root, "p/b.jsonl", msgs[:4] + ([cs_b] if cost_states else []))
    _write(root, "p/c.jsonl", msgs[:6] + ([cs_c] if last_cost_state else []))


def test_resumed_chain_each_node_gets_only_its_own_messages(tmp_path):
    _resumed_chain(tmp_path, cost_states=False)
    res = su.attribute(_load(tmp_path), _WINDOWS, _TOKENS, {})
    for node in _WINDOWS:
        fig = res["nodes"][node]
        assert fig["usage_source"] == "transcript_messages"
        assert fig["models"] == [{"model": OPUS, "input_tokens": 20, "output_tokens": 200,
                                  "cache_read_input_tokens": 1000,
                                  "cache_creation_input_tokens": 50}]
        assert fig["usage_reconciled"] is True
    total_out = sum(res["nodes"][n]["models"][0]["output_tokens"] for n in _WINDOWS)
    assert total_out == 6 * 100   # the distinct-message sum, not the 2+4+6 line sum
    assert res["unattributed_sessions"] == []


def test_cost_state_primary_first_resumed_and_absent(tmp_path):
    _resumed_chain(tmp_path)
    costs = {"conformance": 1.0, "code-review": 2.1, "revise-advisory": 3.1}
    res = su.attribute(_load(tmp_path), _WINDOWS, _TOKENS, costs)
    first = res["nodes"]["conformance"]
    assert first["usage_source"] == "cli_cost_state"
    assert first["first_of_chain"] is True
    assert first["models"] == [   # haiku appears only in cost-state, not in any message
        {"model": HAIKU, "input_tokens": 5, "output_tokens": 5, "cache_read_input_tokens": 0,
         "cache_creation_input_tokens": 0, "cost_usd": 0.1},
        {"model": OPUS, "input_tokens": 20, "output_tokens": 200,
         "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 50, "cost_usd": 0.9},
    ]
    assert first["cost_usd_session_delta"] == pytest.approx(1.0)
    assert first["usage_reconciled"] is True
    resumed = res["nodes"]["code-review"]
    assert resumed["usage_source"] == "cli_cost_state"
    assert resumed["first_of_chain"] is False
    assert resumed["models"] == [   # haiku's unchanged figures drop out of the difference
        {"model": OPUS, "input_tokens": 20, "output_tokens": 200,
         "cache_read_input_tokens": 2000, "cache_creation_input_tokens": 30,
         "cost_usd": pytest.approx(1.1)},
    ]
    assert resumed["cost_usd_session_delta"] == pytest.approx(1.1)   # Archon says 2.1
    assert resumed["usage_reconciled"] is True
    killed = res["nodes"]["revise-advisory"]   # last file has no cost-state line
    assert killed["usage_source"] == "transcript_messages"
    assert killed["first_of_chain"] is False
    assert "cost_usd_session_delta" not in killed
    assert all("cost_usd" not in m for m in killed["models"])


def test_cost_state_unavailable_when_previous_file_lacks_it_or_differs(tmp_path):
    _resumed_chain(tmp_path, cost_states=False, last_cost_state=True)
    res = su.attribute(_load(tmp_path), _WINDOWS, _TOKENS, {})
    assert res["nodes"]["revise-advisory"]["usage_source"] == "transcript_messages"


def test_reconciliation_flags_token_and_cost_mismatch(tmp_path):
    _resumed_chain(tmp_path)
    tokens = dict(_TOKENS, conformance=(21, 200))
    res = su.attribute(_load(tmp_path), _WINDOWS, tokens,
                       {"conformance": 1.0, "code-review": 2.1})
    assert res["nodes"]["conformance"]["usage_source"] == "cli_cost_state"   # sole chain kept
    assert res["nodes"]["conformance"]["usage_reconciled"] is False          # token mismatch
    res = su.attribute(_load(tmp_path), _WINDOWS, _TOKENS, {"conformance": 0.5})
    assert res["nodes"]["conformance"]["usage_reconciled"] is False          # cost mismatch
    assert res["nodes"]["code-review"]["usage_reconciled"] is True           # delta, not cost


def test_message_outside_every_window_or_in_two_is_unattributed(tmp_path):
    _write(tmp_path, "p/x.jsonl", [_u("x", T0 + 690), _a("x1", T0 + 700)])
    _write(tmp_path, "p/y.jsonl", [_u("y", T0 + 4990), _a("y1", T0 + 5000)])
    windows = {"a": (T0, T0 + 1000), "b": (T0 + 500, T0 + 1500)}
    res = su.attribute(_load(tmp_path), windows, {"a": (10, 100), "b": (10, 100)}, {})
    assert res["nodes"] == {}
    entries = {e["first_prompt"]: e for e in res["unattributed_sessions"]}
    assert set(entries) == {"x", "y"}
    assert entries["x"]["window_node"] is None   # held equally by a and b
    assert entries["y"]["window_node"] is None   # held by no window


@pytest.mark.parametrize("tokens", [(99, 99), (10, 100)], ids=["neither", "both"])
def test_ambiguous_window_gives_no_chain(tmp_path, tokens):
    _write(tmp_path, "p/one.jsonl", [_u("one", T0 + 5), _a("o1", T0 + 10)])
    _write(tmp_path, "p/two.jsonl", [_u("two", T0 + 15), _a("t1", T0 + 20)])
    res = su.attribute(_load(tmp_path), {"implement": (T0, T0 + 100)},
                       {"implement": tokens}, {})
    assert "implement" not in res["nodes"]
    assert sorted(e["first_prompt"] for e in res["unattributed_sessions"]) == ["one", "two"]
    assert all(e["window_node"] == "implement" for e in res["unattributed_sessions"])


def test_real_title_session_is_unattributed_and_added_to_no_node():
    t = _load(FIXTURES / "plan-run")
    windows = {"parse-intent": (1795810393681, 1795810397203)}
    res = su.attribute(t, windows, {"parse-intent": (20, 294)}, {"parse-intent": 0.0096158})
    fig = res["nodes"]["parse-intent"]
    assert fig["usage_source"] == "cli_cost_state"
    assert fig["models"] == [{"model": HAIKU, "input_tokens": 1040, "output_tokens": 307,
                              "cache_read_input_tokens": 15408,
                              "cache_creation_input_tokens": 2750, "thinking_tokens": 193,
                              "web_search_requests": 0, "cost_usd": 0.0096158}]
    assert fig["usage_reconciled"] is True
    assert res["unattributed_sessions"] == [{
        "first_prompt": "Generate a concise conversation title (3-6 words) for this u",
        "started_at": "2026-11-27T20:13:14.620Z",
        "models": [{"model": SONNET, "input_tokens": 2, "output_tokens": 10,
                    "cache_read_input_tokens": 8421, "cache_creation_input_tokens": 2864}],
        "cost_usd": 0.0142752,
        "window_node": "parse-intent",
    }]
    assert t["harness_version"] == "2.1.282"


def test_real_multi_model_cost_state_payload():
    # The title session's real cost-state line carries two models, one of which
    # (haiku) appears in none of its assistant lines.
    t = _load(FIXTURES / "plan-run")
    windows = {"title": (1795810393681, 1795810397203)}
    res = su.attribute(t, windows, {"title": (2, 10)}, {"title": 0.0142752})
    # Two chains in the window: only the title chain matches (2, 10).
    fig = res["nodes"]["title"]
    assert [m["model"] for m in fig["models"]] == [HAIKU, SONNET]
    assert fig["models"][1]["cache_creation_input_tokens"] == 2864
    assert fig["cost_usd_session_delta"] == pytest.approx(0.0142752)
    assert fig["usage_reconciled"] is True


def test_real_subagent_transcript_joins_its_node_by_time():
    t = _load(FIXTURES / "subagent-run")
    assert len(t["files"]) == 2   # the .meta.json is not a transcript
    start = su.iso_to_ms("2026-11-27T20:24:00.000Z")
    windows = {"plan": (start, start + 60_000)}
    res = su.attribute(t, windows, {"plan": (6, 4047)}, {})
    fig = res["nodes"]["plan"]
    assert fig["usage_source"] == "transcript_messages"   # parent session still running
    assert fig["usage_reconciled"] is True                # sub-agent not in the token match
    assert fig["models"] == [
        {"model": HAIKU, "input_tokens": 10, "output_tokens": 46,
         "cache_read_input_tokens": 0, "cache_creation_input_tokens": 13243},
        {"model": OPUS, "input_tokens": 6, "output_tokens": 4047,
         "cache_read_input_tokens": 558300, "cache_creation_input_tokens": 14867},
    ]
    assert res["unattributed_sessions"] == []


def test_subagent_chain_outside_every_window_is_listed():
    t = _load(FIXTURES / "subagent-run")
    res = su.attribute(t, {}, {}, {})
    prompts = sorted(e["first_prompt"] for e in res["unattributed_sessions"])
    assert prompts == ["Reply with exactly the single word: ok. Do not use any tools",
                       "scrubbed"]


def test_mtime_floor_and_unparseable_started_at(tmp_path):
    old = _write(tmp_path, "p/old.jsonl", [_a("o1", T0)])
    _write(tmp_path, "p/new.jsonl", [_a("n1", T0)])
    os.utime(old, (1_000_000_000, 1_000_000_000))
    assert set(su.load_transcripts(tmp_path, "2020-01-01T00:00:00Z")["files"]) == {"p/new.jsonl"}
    assert su.load_transcripts(tmp_path, "not-a-time")["files"] == {}
    assert su.load_transcripts(tmp_path / "missing", None)["files"] == {}


def test_hostile_transcripts_never_raise_or_hang(tmp_path, monkeypatch):
    monkeypatch.setattr(su, "MAX_LINE_BYTES", 1024)
    _write(tmp_path, "p/bad.jsonl", [
        "not json", "[1, 2]", "\"a string\"", "{\"type\": \"assistant\", \"message\": 3}",
        json.dumps(_a("big", T0, tin=1)) + " " * 5000,
        json.dumps(_a("ok", T0 + 1, tin=2, tout=3)),
        "[" * 100_000,
    ])
    (tmp_path / "p" / "dir.jsonl").mkdir()
    os.mkfifo(tmp_path / "p" / "fifo.jsonl")
    (tmp_path / "p" / "link.jsonl").symlink_to(tmp_path / "p" / "fifo.jsonl")
    previous = signal.signal(signal.SIGALRM,
                             lambda *_: (_ for _ in ()).throw(TimeoutError()))
    signal.alarm(10)
    try:
        t = _load(tmp_path)
        res = su.attribute(t, {"n": (T0 - 1, T0 + 10)}, {"n": (2, 3)}, {})
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    assert list(t["messages"]) == ["ok"]
    assert res["nodes"]["n"]["usage_reconciled"] is True


def test_synthetic_messages_are_not_a_model(tmp_path):
    _write(tmp_path, "p/s.jsonl", [_a("s1", T0, model=su.SYNTHETIC_MODEL, tin=0, tout=0),
                                   _a("s2", T0 + 1)])
    assert list(_load(tmp_path)["messages"]) == ["s2"]


# --- requirement 8: backfill matching ---------------------------------------------------

def test_backfill_matches_file_by_cost(tmp_path):
    _resumed_chain(tmp_path)
    t = su.load_transcripts(tmp_path)
    res = su.backfill(t, {
        "conformance": {"cost_usd": 1.0, "tokens": (20, 200)},
        "code-review": {"cost_usd": 2.1, "tokens": (20, 200)},
        "revise-advisory": {"cost_usd": 3.1, "tokens": (20, 200)},   # no file costs 3.1
        "fetch-issue": {"cost_usd": 0, "tokens": (0, 0)},
    })
    assert set(res) == {"conformance", "code-review"}
    assert res["code-review"]["cost_usd_session_delta"] == pytest.approx(1.1)
    assert res["code-review"]["usage_reconciled"] is True


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
