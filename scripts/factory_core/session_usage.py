"""Per-node model and token usage from Claude Code session transcripts (#645).

Archon relays no ``modelUsage`` (``archon workflow cost --json`` reports null on every
node), so the run record reads the Claude Code CLI's own session transcripts under
``$HOME/.claude/projects/``:

* one record per ``message.id`` across the whole run: a message is one transcript line
  per content block, each repeating the message's whole usage, and a resumed session
  copies its earlier messages into a new file;
* files that share a ``message.id`` are one session chain;
* a message belongs to the node whose Archon-log window holds its own timestamp;
* a node's figures prefer the CLI's closing ``cost-state`` line (sub-agent and
  small-model spend included), differenced against the chain's previous file.

``cost-state`` and the transcript layout (a sub-agent writes
``<session-id>/subagents/agent-<id>.jsonl``, every line ``isSidechain: true``) are
internals of the pinned CLI (2.1.282), not a documented interface: when neither shape is
found nothing is guessed. Pure functions over files: never imports run_record, never
raises on malformed input, and never opens anything but a regular file.
"""

import gzip
import json
import os
import pathlib
import stat
import unicodedata
import zlib
from collections import Counter
from datetime import datetime, timedelta, timezone

LOG_MODULE = "workflow.dag-executor"
_STARTED, _COMPLETED = "dag_node_started", "dag_node_completed"
# Archon 0.3.10 (packages/workflows/src/dag-executor.ts) logs this, with nodeId, when an
# AI, bash or script node fails; when present it ends the node's window (requirement 2).
_FAILED = "dag_node_failed"
MAX_LINE_BYTES = 8 * 1024 * 1024
# #656: bounds on one load_transcripts() call. The two byte bounds equal the writer-side
# limits of entrypoint.sh's _keep_run_transcripts, so a kept store never trips them for
# real transcripts (lines over the floor); a line counts as at least LINE_CHARGE_FLOOR
# bytes, which bounds the number of lines.
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_CALL_BYTES = 256 * 1024 * 1024
LINE_CHARGE_FLOOR = 64
MAX_CALL_MESSAGES = 262144
MAX_CALL_COST_STATE_BYTES = 4 * 1024 * 1024
COST_EPSILON = 0.000001
ARCHON_COST_EPSILON = 0.01
FIRST_PROMPT_CHARS = 60
# The CLI writes this for its own placeholder messages (e.g. an API error); not a model.
SYNTHETIC_MODEL = "<synthetic>"
TOKEN_KEYS = (
    "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
)
# cost-state modelUsage key -> models-entry key. The four token keys are always written;
# the others only when the cost-state line carries them.
COST_STATE_KEYS = (
    ("inputTokens", "input_tokens"),
    ("outputTokens", "output_tokens"),
    ("cacheReadInputTokens", "cache_read_input_tokens"),
    ("cacheCreationInputTokens", "cache_creation_input_tokens"),
    ("thinkingTokens", "thinking_tokens"),
    ("webSearchRequests", "web_search_requests"),
    ("costUSD", "cost_usd"),
)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def iso_to_ms(value) -> "int | None":
    """Epoch milliseconds of an ISO-8601 timestamp (a trailing Z allowed), else None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - _EPOCH) // timedelta(milliseconds=1)


def ms_to_iso(ms: int) -> str:
    return (_EPOCH + timedelta(milliseconds=ms)).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z")


def _regular_file(path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


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


def read_node_windows(log_file, node_ids) -> dict:
    """node_id -> (start_ms, end_ms) from Archon's JSON log lines (requirement 2).

    A line counts only with module == LOG_MODULE, an integer time inside
    [dag_workflow_starting, dag_workflow_finished] (no finished line: the largest time of
    that module), a nodeId among node_ids, and msg exactly dag_node_started or
    dag_node_completed. The first started and first completed line of a node win. A node
    that started and never completed ends at its first dag_node_failed line, else at the
    next accepted line of any node, else at the upper bound. {} when the file is absent,
    not a regular file or unreadable. _parse_node_log's text-line parsing is separate.
    """
    if not log_file or not _regular_file(log_file):
        return {}
    known = {n for n in node_ids if isinstance(n, str)}
    try:
        module = [o for o in _json_objects(log_file)
                  if o.get("module") == LOG_MODULE and _is_int(o.get("time"))]
    except OSError:
        return {}
    lower = next((o["time"] for o in module if o.get("msg") == "dag_workflow_starting"), None)
    upper = next((o["time"] for o in module if o.get("msg") == "dag_workflow_finished"), None)
    if lower is not None:
        module = [o for o in module if o["time"] >= lower]
    if upper is not None:
        module = [o for o in module if o["time"] <= upper]
    else:
        upper = max((o["time"] for o in module), default=None)
    events = [o for o in module if isinstance(o.get("nodeId"), str) and o["nodeId"] in known]
    accepted = [o for o in events if o.get("msg") in (_STARTED, _COMPLETED)]
    first: dict = {}
    for i, o in enumerate(accepted):
        first.setdefault((o["nodeId"], o["msg"]), (i, o["time"]))
    failed: dict = {}
    for o in events:
        if o.get("msg") == _FAILED:
            failed.setdefault(o["nodeId"], o["time"])
    windows = {}
    for (node, msg), (i, start) in first.items():
        if msg != _STARTED:
            continue
        end = first.get((node, _COMPLETED), (None, None))[1]
        if end is None:
            end = failed.get(node)
        if end is None:
            end = accepted[i + 1]["time"] if i + 1 < len(accepted) else upper
        if end is not None and end >= start:
            windows[node] = (start, end)
    return windows


def _transcript_files(root: pathlib.Path, started_at) -> list:
    """Regular *.jsonl or *.jsonl.gz files under root, recursively, modified at or after
    started_at (None: no time floor; unparseable: nothing). Symlinks, FIFOs and
    directories are never opened."""
    if not root.is_dir():
        return []
    floor = None
    if started_at is not None:
        floor_ms = iso_to_ms(started_at)
        if floor_ms is None:
            return []
        floor = floor_ms / 1000
    out = []
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            if not (name.endswith(".jsonl") or name.endswith(".jsonl.gz")):
                continue
            path = pathlib.Path(dirpath) / name
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode) or (floor is not None and st.st_mtime < floor):
                continue
            out.append(path)
    return sorted(out)


def _user_text(obj: dict) -> "str | None":
    msg = obj.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if (isinstance(block, dict) and block.get("type") == "text"
                    and isinstance(block.get("text"), str)):
                return block["text"]
    return None


def _clean_prompt(text: str) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cc")[:FIRST_PROMPT_CHARS]


def _message_usage(usage) -> dict:
    usage = usage if isinstance(usage, dict) else {}
    return {k: usage[k] if _is_int(usage.get(k)) and usage[k] >= 0 else 0 for k in TOKEN_KEYS}


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


def _is_subagent_file(info: dict) -> bool:
    return info["assistant_lines"] > 0 and info["sidechain_lines"] == info["assistant_lines"]


def _chains(files: dict, messages: dict) -> list:
    """Session chains: files sharing any message.id, each chain's files ordered by their
    distinct-message count. A chain is a sub-agent chain when all its files are."""
    parent = {rel: rel for rel in files}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for rec in messages.values():
        members = sorted(rec["files"])
        for other in members[1:]:
            parent[find(other)] = find(members[0])
    groups: dict = {}
    for rel in sorted(files):
        groups.setdefault(find(rel), []).append(rel)
    chains = []
    for members in groups.values():
        ids = {m for rel in members for m in files[rel]["ids"]}
        if not ids:
            continue
        members.sort(key=lambda r: (len(files[r]["ids"]), r))
        chains.append({"files": members, "ids": ids,
                       "subagent": all(_is_subagent_file(files[r]) for r in members)})
    return chains


def _owner(ts, windows: dict):
    """The one node whose window holds ts; None for no window or several."""
    if ts is None:
        return None
    hits = [node for node, (start, end) in windows.items() if start <= ts <= end]
    return hits[0] if len(hits) == 1 else None


def _token_pair(messages: dict, ids) -> tuple:
    main = [m for m in ids if not messages[m]["sidechain"]]
    return (sum(messages[m]["usage"]["input_tokens"] for m in main),
            sum(messages[m]["usage"]["output_tokens"] for m in main))


def _sum_models(messages: dict, ids) -> list:
    acc: dict = {}
    for m in ids:
        rec = messages[m]
        if not rec["model"]:
            continue
        entry = acc.setdefault(rec["model"], {"model": rec["model"], **{k: 0 for k in TOKEN_KEYS}})
        for k in TOKEN_KEYS:
            entry[k] += rec["usage"][k]
    return [acc[k] for k in sorted(acc)]


def _main_files(chain: dict, files: dict) -> list:
    return [r for r in chain["files"] if not _is_subagent_file(files[r])]


def _last_ts(info: dict, messages: dict):
    stamps = [messages[m]["ts"] for m in info["ids"] if messages[m]["ts"] is not None]
    return max(stamps) if stamps else None


def _node_file(chain: dict, files: dict, messages: dict, window: tuple):
    """The chain file whose last message falls in the window, the latest such."""
    best = None
    for rel in _main_files(chain, files):
        last = _last_ts(files[rel], messages)
        if last is not None and window[0] <= last <= window[1] and (best is None or last > best[0]):
            best = (last, rel)
    return best[1] if best else None


def _prev_file(chain: dict, files: dict, rel: str):
    """The chain file with the next-smaller distinct-message count, or None (first file)."""
    count = len(files[rel]["ids"])
    smaller = [r for r in _main_files(chain, files) if len(files[r]["ids"]) < count]
    return max(smaller, key=lambda r: (len(files[r]["ids"]), r)) if smaller else None


def _valid_cost_state(line) -> bool:
    return (isinstance(line, dict) and isinstance(line.get("modelUsage"), dict)
            and _is_num(line.get("totalCostUSD")))


def _cost_state_figures(files: dict, file_rel: str, prev_rel):
    """(models, delta) from the node file's cost-state line minus the previous file's line
    (same startTime) when there is a previous file; None when the source is unavailable:
    no line, a previous file without one or with another startTime, or a negative
    difference in any field."""
    cur = files[file_rel]["cost_state"]
    if not _valid_cost_state(cur):
        return None
    base = None
    if prev_rel is not None:
        base = files[prev_rel]["cost_state"]
        if (not _valid_cost_state(base) or not _is_int(cur.get("startTime"))
                or base.get("startTime") != cur.get("startTime")):
            return None
    base_usage = base["modelUsage"] if base else {}
    models = []
    for model in sorted(set(cur["modelUsage"]) | set(base_usage)):
        if not model:
            continue
        now = cur["modelUsage"].get(model)
        now = now if isinstance(now, dict) else {}
        before = base_usage.get(model)
        before = before if isinstance(before, dict) else {}
        entry = {"model": model}
        for src, dst in COST_STATE_KEYS:
            if dst not in TOKEN_KEYS and src not in now and src not in before:
                continue
            a, b = now.get(src, 0), before.get(src, 0)
            if not _is_num(a) or not _is_num(b):
                return None
            diff = a - b if _is_int(a) and _is_int(b) else round(a - b, 10)
            if diff < 0:
                return None
            entry[dst] = diff
        if any(v for k, v in entry.items() if k != "model"):
            models.append(entry)
    delta = round(cur["totalCostUSD"] - (base["totalCostUSD"] if base else 0), 10)
    if delta < 0 or not models:
        return None
    return models, delta


def _figures(files, messages, file_rel, prev_rel, fallback_ids, matched, node_cost):
    """One node's figures: cli_cost_state, else transcript_messages, else None."""
    fig = _cost_state_figures(files, file_rel, prev_rel) if file_rel else None
    if fig is not None:
        models, delta = fig
        ok = abs(sum(m.get("cost_usd", 0) for m in models) - delta) <= COST_EPSILON
        if prev_rel is None:
            ok = ok and _is_num(node_cost) and abs(delta - node_cost) <= COST_EPSILON
        return {"usage_source": "cli_cost_state", "models": models,
                "cost_usd_session_delta": delta, "usage_reconciled": bool(matched and ok),
                "first_of_chain": prev_rel is None}
    models = _sum_models(messages, fallback_ids)
    if not models:
        return None
    return {"usage_source": "transcript_messages", "models": models,
            "usage_reconciled": bool(matched),
            "first_of_chain": None if file_rel is None else prev_rel is None}


def _session_entry(chain: dict, files: dict, messages: dict, windows: dict) -> dict:
    """An unattributed_sessions entry (requirement 3, rule 5)."""
    rels = chain["files"]
    firsts = [files[r]["first_ts"] for r in rels if files[r]["first_ts"] is not None]
    users = sorted((files[r]["first_user"] for r in rels if files[r]["first_user"]),
                   key=lambda u: (u[0] is None, u[0] or 0))
    last = (_main_files(chain, files) or rels)[-1]
    cost_state = files[last]["cost_state"]
    held: Counter = Counter()
    for m in chain["ids"]:
        ts = messages[m]["ts"]
        for node, (start, end) in windows.items():
            if ts is not None and start <= ts <= end:
                held[node] += 1
    top = held.most_common(2)
    window_node = top[0][0] if top and (len(top) == 1 or top[0][1] > top[1][1]) else None
    return {
        "first_prompt": _clean_prompt(users[0][1]) if users else "",
        "started_at": ms_to_iso(min(firsts)) if firsts else None,
        "models": _sum_models(messages, chain["ids"]),
        "cost_usd": cost_state["totalCostUSD"] if _valid_cost_state(cost_state) else None,
        "window_node": window_node,
    }


def attribute(transcripts: dict, windows: dict, archon_tokens: dict, node_costs: dict) -> dict:
    """Requirement 3 over a load_transcripts() result.

    windows: node_id -> (start_ms, end_ms) for model-calling nodes only. archon_tokens:
    node_id -> (input, output) as Archon reported them (None when unknown). node_costs:
    node_id -> Archon's cost_usd. Returns {"nodes": {node_id: figures},
    "unattributed_sessions": [...]}; a node absent from "nodes" has no transcript figures.
    """
    files, messages = transcripts["files"], transcripts["messages"]
    chains = _chains(files, messages)
    owner = {mid: _owner(rec["ts"], windows) for mid, rec in messages.items()}
    candidates: dict = {}
    for ci, chain in enumerate(chains):
        if chain["subagent"]:
            continue
        for node in sorted({owner[m] for m in chain["ids"]} - {None}):
            candidates.setdefault(node, []).append(ci)
    node_chain: dict = {}
    matched: dict = {}
    for node, cis in candidates.items():
        want = tuple(archon_tokens.get(node) or (None, None))
        hits = [ci for ci in cis
                if _token_pair(messages, [m for m in chains[ci]["ids"] if owner[m] == node]) == want]
        if len(hits) == 1:
            node_chain[node], matched[node] = hits[0], True
        elif len(cis) == 1 and not hits:
            node_chain[node], matched[node] = cis[0], False
    results = {}
    for node, ci in node_chain.items():
        chain = chains[ci]
        own = [m for m in sorted(chain["ids"]) if owner[m] == node]
        sub = [m for c in chains if c["subagent"] for m in sorted(c["ids"]) if owner[m] == node]
        file_rel = _node_file(chain, files, messages, windows[node])
        prev_rel = _prev_file(chain, files, file_rel) if file_rel else None
        fig = _figures(files, messages, file_rel, prev_rel, own + sub, matched[node],
                       node_costs.get(node))
        if fig is not None:
            results[node] = fig
    taken = set(node_chain.values())
    unattributed = [
        _session_entry(chain, files, messages, windows)
        for ci, chain in enumerate(chains)
        if ci not in taken
        and not (chain["subagent"] and any(owner[m] in node_chain for m in chain["ids"]))
    ]
    unattributed.sort(key=lambda e: (e["started_at"] or "", e["first_prompt"]))
    return {"nodes": results, "unattributed_sessions": unattributed}


def backfill(transcripts: dict, nodes: dict) -> dict:
    """Requirement 8: figures for a record whose Archon log is gone.

    nodes: node_id -> {"cost_usd": x, "tokens": (input, output)}. A node's file is the one
    main-thread file whose cost-state totalCostUSD equals its recorded cost_usd within
    COST_EPSILON; its messages are that file's messages not in the chain's previous file.
    A node with no such file, or several, is absent from the result.
    """
    files, messages = transcripts["files"], transcripts["messages"]
    chain_of = {rel: c for c in _chains(files, messages) if not c["subagent"]
                for rel in _main_files(c, files)}
    candidates: dict = {}
    for node, info in nodes.items():
        cost = info.get("cost_usd")
        if not _is_num(cost) or cost <= 0:
            continue
        hits = [rel for rel in chain_of
                if _valid_cost_state(files[rel]["cost_state"])
                and abs(files[rel]["cost_state"]["totalCostUSD"] - cost) <= COST_EPSILON]
        if len(hits) != 1:
            continue
        candidates[node] = hits[0]
    by_file: dict = {}
    for node, rel in candidates.items():
        by_file.setdefault(rel, []).append(node)
    results = {}
    for node, rel in candidates.items():
        if len(by_file[rel]) != 1:
            # Several nodes share this recorded cost and all matched the same file —
            # ambiguous, so none of them get it.
            continue
        info = nodes[node]
        prev = _prev_file(chain_of[rel], files, rel)
        earlier = set(files[prev]["ids"]) if prev else set()
        own = [m for m in files[rel]["ids"] if m not in earlier]
        matched = _token_pair(messages, own) == tuple(info.get("tokens") or (None, None))
        fig = _figures(files, messages, rel, prev, own, matched, info.get("cost_usd"))
        if fig is not None:
            results[node] = fig
    return results
