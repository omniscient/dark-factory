#!/usr/bin/env python3
"""
Dark factory run record — writes per-stage verdicts to runs.jsonl and Seq.

Two subcommands:
  record   — write one event line (called from entrypoint or on_failure trap)
  assemble — end-of-run aggregation from artifact files + archon cost JSON
"""

import argparse
import fcntl
import json
import os
import pathlib
import re
import subprocess
import sys
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone

from . import model_proxy
from . import verdict as _verdict

SCHEDULER_STATE_DIR = pathlib.Path(os.environ.get("SCHEDULER_STATE_DIR", "/var/lib/dark-factory"))
JSONL_PATH = SCHEDULER_STATE_DIR / "runs.jsonl"
SEQ_URL = os.environ.get("SEQ_URL", "http://seq:5341")

POLICY_VERSION = "1.1"
GATE_STAGE_NAMES = ("validation", "conformance", "review")
_BLOCKING_VERDICTS = {
    "validation": {"FAIL"},
    "conformance": {"BLOCKED"},
    "review": {"BLOCKED"},
}
CONFORMANCE_CYCLE_PENALTY = 0.10
REVIEW_ADVISORY_PENALTY = 0.05
SCORE_FLOOR = 0.25

# run_id is embedded in an on-disk path (artifacts_root / run_id / "run-record.json");
# reject anything that isn't a plain path segment before joining, so a crafted run_id
# (e.g. containing "..", "/") can't traverse outside artifacts_root.
_SAFE_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _run_record_path(artifacts_root: pathlib.Path, run_id: str) -> "pathlib.Path | None":
    if not run_id or not _SAFE_RUN_ID_RE.match(run_id):
        return None
    return artifacts_root / run_id / "run-record.json"


def _durable_run_record_path(run_id: str) -> "pathlib.Path | None":
    if not run_id or not _SAFE_RUN_ID_RE.match(run_id):
        return None
    return SCHEDULER_STATE_DIR / "run-records" / f"{run_id}.json"


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_jsonl(record: dict) -> None:
    JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(JSONL_PATH, "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.write(json.dumps(record) + "\n")
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def append_stop_record(record: dict) -> None:
    """Public wrapper for a breaker stop-condition audit row (#198 R8) — writes to
    runs.jsonl only, no Seq post (unlike cmd_record/_post_seq, which are for actual
    run verdicts, not breaker decisions)."""
    _append_jsonl(record)


def _post_seq(record: dict) -> None:
    payload = {
        "Events": [
            {
                "Timestamp": record.get("timestamp", _timestamp()),
                "Level": "Information",
                "MessageTemplate": (
                    "factory.stage.{Stage} verdict={Verdict} issue=#{IssueNumber}"
                ),
                "Properties": {
                    "gen_ai.system": record.get("gen_ai.system", "dark-factory"),
                    "gen_ai.operation.name": record.get(
                        "gen_ai.operation.name",
                        f"stage.{record.get('stage', 'unknown')}",
                    ),
                    "gen_ai.usage.input_tokens": record.get("gen_ai.usage.input_tokens"),
                    "gen_ai.usage.output_tokens": record.get("gen_ai.usage.output_tokens"),
                    "Stage": record.get("stage", ""),
                    "Verdict": record.get("verdict", ""),
                    "IssueNumber": record.get("issue_number", 0),
                    "Intent": record.get("intent", ""),
                    "RunId": record.get("run_id", ""),
                    "CostUsd": record.get("cost_usd"),
                    "DurationMs": record.get("duration_ms"),
                },
            }
        ]
    }
    _post_seq_raw(payload)


def _post_seq_raw(payload: dict) -> None:
    endpoint = f"{SEQ_URL.rstrip('/')}/api/events/raw"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        endpoint, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception:
        pass  # non-fatal: Seq is always best-effort, whether or not the local ledger write succeeded


def cmd_record(args) -> None:
    details: dict = {}
    for kv in args.detail or []:
        k, _, v = kv.partition("=")
        if v.isdigit():
            details[k] = int(v)
        else:
            try:
                details[k] = float(v)
            except ValueError:
                details[k] = v

    record: dict = {
        "run_id": args.run_id,
        "issue_number": args.issue,
        "intent": args.intent,
        "stage": args.stage,
        "verdict": args.verdict,
        # Who produced this row: "factory" (default, every existing caller) or
        # "target-loop:<name>" for rows written on behalf of a target loop (#199/#378).
        # getattr so callers that build a bare Namespace without the flag keep working.
        "origin": getattr(args, "origin", None) or "factory",
        "side_effect_level": getattr(args, "side_effect_level", None) or 1,
        "side_effect_profile": getattr(args, "side_effect_profile", None) or "unknown",
        "gen_ai.system": "dark-factory",
        "gen_ai.operation.name": f"stage.{args.stage}",
        "gen_ai.usage.input_tokens": args.tokens_in,
        "gen_ai.usage.output_tokens": args.tokens_out,
        "cost_usd": args.cost_usd,
        "duration_ms": args.duration_ms,
        "timestamp": _timestamp(),
    }
    if details:
        record["detail"] = details

    try:
        _append_jsonl(record)
    except OSError as exc:
        print(f"run-record: ledger append failed ({JSONL_PATH}): {exc}", file=sys.stderr)
        _post_seq(record)  # best-effort — the only remaining place this verdict can land
        emit_health_event(
            "factory.run_record.ledger_write_failed",
            record["issue_number"], record["run_id"],
            {"stage": record["stage"], "path": str(JSONL_PATH), "error": str(exc)[:500]},
        )
        raise
    _post_seq(record)


def emit_health_event(event: str, issue: int, run_id: str, detail: dict) -> None:
    """Non-blocking recurrence-detection signal (df#300), callable in-process.

    Used by both the `run-record health-event` CLI subcommand (cmd_health_event,
    unchanged behavior) and cli.py's `cost-report-check` handler (#182).
    """
    payload = {
        "Events": [
            {
                "Timestamp": _timestamp(),
                "Level": "Warning",
                "MessageTemplate": "{Event} issue=#{IssueNumber} run={RunId}",
                "Properties": {
                    "Event": event,
                    "IssueNumber": issue,
                    "RunId": run_id,
                    **detail,
                },
            }
        ]
    }
    try:
        _post_seq_raw(payload)
    except Exception:
        pass  # non-fatal: this is best-effort observability, not a gate


def cmd_health_event(args) -> None:
    """Lightweight, non-blocking recurrence-detection signal (df#300).

    Distinct from cmd_record's per-stage events: this is a named incident signal
    (e.g. 'factory.cost_report.missing'), not a stage verdict, so it gets its own
    MessageTemplate rather than overloading Stage/Verdict fields with an event name.
    """
    details: dict = {}
    for kv in args.detail or []:
        k, _, v = kv.partition("=")
        details[k] = v
    emit_health_event(args.event, args.issue, args.run_id, details)


def _iter_json_documents(text: str):
    """Yield each top-level JSON value in text (mirrors `jq -s`).

    `archon workflow cost --json` emits the run as a single PRETTY-PRINTED
    (multi-line, indent=2) object; `--quiet` suppresses its pino log lines.
    Parsing line-by-line breaks on the indented object — every fragment is
    invalid JSON — so decode the stream value-by-value with raw_decode and
    resync to the next line on any non-JSON noise (e.g. a leaked pino line).
    """
    decoder = json.JSONDecoder()
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n":
            i += 1
        if i >= n:
            break
        try:
            obj, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            nl = text.find("\n", i)
            if nl == -1:
                break
            i = nl + 1
            continue
        yield obj
        i = end


def _find_run_obj(content: str) -> "dict | None":
    """Scan already-read JSON document text for the first object carrying a run id."""
    for obj in _iter_json_documents(content):
        candidates = obj if isinstance(obj, list) else [obj]
        for cand in candidates:
            if isinstance(cand, dict) and (cand.get("run_id") or cand.get("runId")):
                return cand
    return None


def _nodes_from_run_obj(run_obj: dict) -> list:
    """Map a run object's nodes to OTel field names."""
    nodes = []
    for n in run_obj.get("nodes") or []:
        model_usage = n.get("modelUsage") or n.get("model_usage") or {}
        raw_model = next(iter(model_usage.keys()), "")
        if raw_model.startswith("claude-"):
            raw_model = raw_model[len("claude-"):]
        if "-20" in raw_model:
            raw_model = raw_model[: raw_model.index("-20")]
        nodes.append(
            {
                "node_id": n.get("nodeId") or n.get("node_id", ""),
                "model": raw_model,
                "gen_ai.usage.input_tokens": (
                    n.get("inputTokens") or n.get("input_tokens") or 0
                ),
                "gen_ai.usage.output_tokens": (
                    n.get("outputTokens") or n.get("output_tokens") or 0
                ),
                "cost_usd": n.get("costUsd") or n.get("cost_usd") or 0,
                "duration_ms": n.get("durationMs") or n.get("duration_ms") or 0,
            }
        )
    return nodes


def _parse_archon_cost(path: pathlib.Path) -> list:
    """Map archon workflow cost JSON nodes to OTel field names."""
    if path is None or not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return []
        run_obj = _find_run_obj(content)
        if run_obj is None:
            return []
        return _nodes_from_run_obj(run_obj)
    except Exception:
        return []


def _parse_archon_cost_with_capture(
    path: "pathlib.Path | None", *, exit_code: int, stderr_text: str
) -> "tuple[list, dict]":
    """Like _parse_archon_cost, but distinguishes "archon ran fine, genuinely zero
    nodes" from "the command errored / its output didn't parse" (df#300).

    ok=False when: nonzero exit code, OR the file is missing/empty/unparseable.
    ok=True when: exit_code==0 AND a run object was found (nodes may still be []
    if archon itself reports no nodes — that is a legitimate zero, not a capture
    failure).
    """
    stderr_excerpt = (stderr_text or "").strip()[-2000:]
    if exit_code != 0:
        return [], {"ok": False, "exit_code": exit_code, "stderr_excerpt": stderr_excerpt}

    if path is None or not path.exists():
        return [], {"ok": False, "exit_code": exit_code, "stderr_excerpt": stderr_excerpt}

    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return [], {"ok": False, "exit_code": exit_code, "stderr_excerpt": stderr_excerpt}

    if not content:
        return [], {"ok": False, "exit_code": exit_code, "stderr_excerpt": stderr_excerpt}

    run_obj = _find_run_obj(content)

    if run_obj is None:
        return [], {"ok": False, "exit_code": exit_code, "stderr_excerpt": stderr_excerpt}

    nodes = _nodes_from_run_obj(run_obj)
    return nodes, {"ok": True, "exit_code": exit_code, "stderr_excerpt": stderr_excerpt}


# --- Per-node cost capture (#453) -------------------------------------------------
# The workflow YAML Archon actually executes: the clone's own copy, else the baked one
# (entrypoint.sh copies the baked workflows into a target clone that has none).
WORKFLOW_YAML_REL = pathlib.Path(".archon") / "workflows" / "archon-dark-factory.yaml"
BAKED_WORKFLOW_PATH = pathlib.Path("/opt/dark-factory/workflows/archon-dark-factory.yaml")
_NODE_TYPE_KEYS = ("command", "prompt", "bash")
_AI_NODE_TYPES = ("command", "prompt")
# Archon's `archon workflow run` progress line: `[<command ?? id>] Started|Completed (..)|
# Failed: ..|Skipped (..)`. Anchored at line start; the token must be a known name. Each
# event word is also anchored to its exact trailing shape (bare end-of-line for Started,
# "(" for Completed/Skipped, ":" for Failed) so a streamed agent/tool line that merely
# starts with e.g. "[implement] Started <more text>" is not mistaken for Archon's own
# progress line (df#453 advisory).
_NODE_LOG_RE = re.compile(
    r"^\[([A-Za-z0-9_-]+)\]\s+"
    r"(Started(?=\s*$)|Completed(?=\s*\()|Failed(?=:)|Skipped(?=\s*\())"
)


def _load_workflow_node_maps(clone_dir) -> "tuple[dict, dict] | None":
    """(node_id -> "command"|"prompt"|"bash", log token -> node_id), or None.

    Archon names a command: node by its command: value in its log lines
    (`[dark-factory-implement] Started` for id `implement`) and every other node by its
    id:, so both are log tokens. A token two nodes claim is dropped as ambiguous. None
    when neither the clone's nor the baked workflow YAML loads — node types are then
    unknown and nothing may be classified (an unknown type is never treated as bash).
    """
    try:
        import yaml
    except ImportError:
        return None
    for path in (pathlib.Path(clone_dir or ".") / WORKFLOW_YAML_REL, BAKED_WORKFLOW_PATH):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        wf_nodes = doc.get("nodes") if isinstance(doc, dict) else None
        if not isinstance(wf_nodes, list):
            continue
        node_types: dict = {}
        log_tokens: dict = {}
        ambiguous: set = set()
        for n in wf_nodes:
            if not isinstance(n, dict) or not isinstance(n.get("id"), str):
                continue
            node_id = n["id"]
            kind = next((k for k in _NODE_TYPE_KEYS if k in n), None)
            if kind:
                node_types[node_id] = kind
            names = [node_id]
            if isinstance(n.get("command"), str):
                names.append(n["command"])
            for name in names:
                if log_tokens.get(name, node_id) != node_id:
                    ambiguous.add(name)
                log_tokens[name] = node_id
        for name in ambiguous:
            log_tokens.pop(name, None)
        if node_types:
            return node_types, log_tokens
    return None


def _parse_node_log(log_file, log_tokens: dict) -> "dict | None":
    """node_id -> last Started/Completed/Failed/Skipped event from the tee'd
    `archon workflow run` output ($TMP_OUT), in first-seen order. None when the file is
    absent/unreadable or no line names a known node."""
    if not log_file:
        return None
    try:
        text = pathlib.Path(log_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    events: dict = {}
    for line in text.splitlines():
        m = _NODE_LOG_RE.match(line)
        if m and m.group(1) in log_tokens:
            events[log_tokens[m.group(1)]] = m.group(2)
    return events or None


def _classify_node_capture(nodes: list, node_types: dict, log_events: "dict | None") -> "tuple[list, list]":
    """Mark each node cost_capture completed/missing (#453). Returns (nodes, missing).

    A reported command:/prompt: node with all-zero usage lost its usage in Archon: it is
    missing/no_usage_reported with null cost/tokens (duration kept). A bash: zero is real.
    With a parsed log, a command:/prompt: node that started but is absent from the cost
    report is appended as missing/not_in_cost_report; a Skipped node never ran and is not
    emitted, and an absent bash: node spent nothing and is not emitted either. A node that
    IS reported (present in the cost report, e.g. a `when:`-gated node Archon still lists
    with zero usage) but whose last log event is Skipped never ran either, so its zero is
    genuine, not a missing-capture — it is left as completed rather than flagged missing.
    """
    out: list = []
    missing: list = []
    for node in nodes:
        node = dict(node)
        zero = not any(
            node.get(k)
            for k in ("cost_usd", "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens")
        )
        skipped = (log_events or {}).get(node.get("node_id")) == "Skipped"
        if node_types.get(node.get("node_id")) in _AI_NODE_TYPES and zero and not skipped:
            node.update({
                "gen_ai.usage.input_tokens": None,
                "gen_ai.usage.output_tokens": None,
                "cost_usd": None,
                "cost_capture": "missing",
                "reason": "no_usage_reported",
            })
            missing.append({"node_id": node.get("node_id", ""), "reason": "no_usage_reported"})
        else:
            node["cost_capture"] = "completed"
        out.append(node)
    reported = {n.get("node_id") for n in nodes}
    for node_id, event in (log_events or {}).items():
        if node_id in reported or event == "Skipped" or node_types.get(node_id) not in _AI_NODE_TYPES:
            continue
        out.append({
            "node_id": node_id,
            "model": "",
            "gen_ai.usage.input_tokens": None,
            "gen_ai.usage.output_tokens": None,
            "cost_usd": None,
            "duration_ms": None,
            "cost_capture": "missing",
            "reason": "not_in_cost_report",
        })
        missing.append({"node_id": node_id, "reason": "not_in_cost_report"})
    return out, missing


def _sum_usage(nodes: list) -> "tuple":
    return (
        sum(n.get("gen_ai.usage.input_tokens", 0) for n in nodes),
        sum(n.get("gen_ai.usage.output_tokens", 0) for n in nodes),
        sum(n.get("cost_usd", 0) for n in nodes),
    )


def _capture_totals(nodes: list, *, capture_ok: bool, clone_dir, archon_log_file,
                    issue: int, run_id: str) -> "tuple[list, dict]":
    """Classify per-node cost capture and build run-record totals (#453).

    totals.cost_usd/token totals keep their fully-measured meaning: null when capture
    failed (df#300) or is partial. known_* sum only completed nodes. Emits one
    non-blocking health event per missing node, plus one when the workflow YAML or the
    node log is unavailable.
    """
    totals = {
        "gen_ai.usage.input_tokens": None,
        "gen_ai.usage.output_tokens": None,
        "cost_usd": None,
        "cost_capture": "unavailable",
        "node_log": "unavailable",
        "known_cost_usd": None,
        "known_input_tokens": None,
        "known_output_tokens": None,
        "missing_nodes": [],
    }
    if not capture_ok:
        return nodes, totals

    maps = _load_workflow_node_maps(clone_dir)
    if maps is None:
        emit_health_event(
            "factory.cost_report.node_types_unavailable", issue, run_id,
            {"clone_dir": str(clone_dir)},
        )
        tin, tout, cost = _sum_usage(nodes)
        totals.update({
            "gen_ai.usage.input_tokens": tin,
            "gen_ai.usage.output_tokens": tout,
            "cost_usd": cost,
            "cost_capture": "unknown",
        })
        return nodes, totals

    node_types, log_tokens = maps
    log_events = _parse_node_log(archon_log_file, log_tokens)
    if log_events is None:
        emit_health_event(
            "factory.cost_report.node_log_unavailable", issue, run_id,
            {"archon_log_file": str(archon_log_file or "")},
        )
    nodes, missing = _classify_node_capture(nodes, node_types, log_events)
    for entry in missing:
        emit_health_event("factory.cost_report.node_missing", issue, run_id, dict(entry))

    known_in, known_out, known_cost = _sum_usage(
        [n for n in nodes if n.get("cost_capture") == "completed"]
    )
    complete = not missing
    totals.update({
        "gen_ai.usage.input_tokens": known_in if complete else None,
        "gen_ai.usage.output_tokens": known_out if complete else None,
        "cost_usd": known_cost if complete else None,
        "cost_capture": "complete" if complete else "partial",
        "node_log": "unavailable" if log_events is None else "parsed",
        "known_cost_usd": known_cost,
        "known_input_tokens": known_in,
        "known_output_tokens": known_out,
        "missing_nodes": missing,
    })
    return nodes, totals


def _parse_artifact_stage(name: str, content: str) -> "dict | None":
    """Extract stage verdict and metadata from a verdict artifact .md file.

    Thin per-name wrapper over verdict.parse_verdict's generic STATUS/GATE_TYPE/
    FINDINGS_COUNT/SEVERITY parse for the names whose original loop already had
    last-STATUS-line-wins semantics (conformance/review), each keeping its own
    extra-detail overlay (cycles/blockers/advisory) and its own pre-existing
    loose-fallback heuristic, byte-identical to before this refactor.

    validation's original loop instead `break`s on the *first* STATUS: match
    (first-wins) -- a different precedence than the shared generic parser's
    last-wins scan -- so it keeps its own bespoke first-wins loop rather than
    delegating, to avoid silently changing behavior for hypothetical multi-STATUS
    content. conflict_resolution never used the STATUS: schema at all
    (CONFLICT_VERDICT=/**Status:** instead) and stays fully independent, exactly
    as before. A name this function has no bespoke overlay for (e.g. a target
    loop's own GATE_TYPE) falls through to the generic parser directly — new
    capability, not exercised by cmd_assemble's fixed artifact_names today.
    """
    if not content.strip():
        return None

    lines = content.splitlines()

    if name == "validation":
        verdict = None
        for line in lines:
            if line.startswith("STATUS:"):
                verdict = line.split(":", 1)[1].strip()
                break
        if verdict is None:
            verdict = (
                "PASS" if "PASS" in content else ("FAIL" if "FAIL" in content else None)
            )
        if verdict is None:
            return None
        return {"stage": name, "verdict": verdict}

    if name == "conflict_resolution":
        verdict = None
        for line in lines:
            if line.startswith("CONFLICT_VERDICT="):
                verdict = line.split("=", 1)[1].strip()
                break
            if "**Status:**" in line:
                verdict = line.split("**Status:**", 1)[1].strip().strip("*").strip()
                break
        if verdict is None:
            verdict = "RESOLVED" if "RESOLVED" in content else "none"
        return {"stage": name, "verdict": verdict}

    # conformance, review, and any name with no bespoke overlay share the generic
    # parser's last-STATUS-line-wins semantics -- matching conformance/review's
    # original no-break loops exactly.
    parsed = _verdict.parse_verdict(content) or {}
    verdict = parsed.get("status")
    detail: dict = {}

    if name == "conformance":
        for line in lines:
            if line.startswith("CYCLES:"):
                try:
                    detail["cycles"] = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
        if verdict is None:
            if "⛔" in content:
                verdict = "BLOCKED"
            elif "Conforms" in content or "Minor" in content or "PASS" in content:
                verdict = "PASS"

    elif name == "review":
        for line in lines:
            if line.startswith("BLOCKERS:"):
                try:
                    detail["blockers"] = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
            elif line.startswith("ADVISORY:"):
                try:
                    detail["advisory"] = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
        if verdict is None:
            verdict = (
                "PASS" if "PASS" in content else ("BLOCKED" if "BLOCKED" in content else None)
            )

    # else: no bespoke overlay for this name — verdict/detail stay exactly what the
    # generic parser produced (the new fallback path).

    if verdict is None:
        return None

    result: dict = {"stage": name, "verdict": verdict}
    result.update(detail)
    return result


def _compute_outcome(status: str, stages: list, *, artifact_check: "dict | None" = None) -> dict:
    """Deterministic outcome.state / outcome.score policy (policy_version 1.1).

    failed/blocked/clean_abort always score 0.0 regardless of token spend — the
    mechanical enforcement of "don't reward raw token reduction over correctness." See
    docs/archive/2026-07-16-harness-economics-ledger-cpm-design.md "Outcome-score
    policy" (1.0) and docs/superpowers/specs/2026-11-22-run-record-artifact-scoring-design.md
    (1.1).

    artifact_check is passed by cmd_assemble for refine/plan runs only (#447). With zero
    gate stages, a run that committed no spec/plan is clean_abort when needs-discussion
    is on the issue and failed otherwise (including an unknown label state). None keeps
    the pre-1.1 unconditional produced_ungated scoring for every other intent.
    """
    stage_by_name = {s["stage"]: s for s in stages if s.get("stage") in GATE_STAGE_NAMES}
    gate_stages = [stage_by_name[name] for name in GATE_STAGE_NAMES if name in stage_by_name]
    cycles = stage_by_name.get("conformance", {}).get("cycles") or 0
    advisory = stage_by_name.get("review", {}).get("advisory") or 0

    if status != "completed":
        state = "failed"
    elif any(
        stage_by_name.get(name, {}).get("verdict") in _BLOCKING_VERDICTS[name]
        for name in GATE_STAGE_NAMES
    ):
        state = "blocked"
    elif not gate_stages:
        if artifact_check is None or artifact_check.get("artifact_present"):
            state = "produced_ungated"
        elif artifact_check.get("needs_discussion"):
            state = "clean_abort"
        else:
            state = "failed"
    else:
        state = "delivered_with_findings" if (cycles or advisory) else "delivered_clean"

    penalties = []
    if state in ("failed", "blocked", "clean_abort"):
        score = 0.0
    elif state == "produced_ungated":
        score = 1.0
    else:
        score = 1.0
        if cycles:
            delta = round(-CONFORMANCE_CYCLE_PENALTY * cycles, 4)
            score += delta
            penalties.append({"reason": "conformance_cycles", "count": cycles, "delta": delta})
        if advisory:
            delta = round(-REVIEW_ADVISORY_PENALTY * advisory, 4)
            score += delta
            penalties.append({"reason": "review_advisory", "count": advisory, "delta": delta})
        score = max(SCORE_FLOOR, min(1.0, round(score, 4)))

    evidence = {
        "status": status,
        "gate_stages": gate_stages,
        "penalties": penalties,
        "ungated": state == "produced_ungated",
    }
    if artifact_check is not None:
        evidence["artifact_check"] = artifact_check

    return {"state": state, "score": score, "evidence": evidence}


LEDGER_PATH = pathlib.Path(
    os.environ.get("MODEL_PROXY_LEDGER_PATH", "/var/lib/dark-factory/request-ledger.jsonl")
)


def _wall_clock_seconds(started_at: str, completed_at: str) -> int:
    try:
        start = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ")
        end = datetime.strptime(completed_at, "%Y-%m-%dT%H:%M:%SZ")
        return max(0, int((end - start).total_seconds()))
    except (ValueError, TypeError):
        return 0


def _iter_ledger_paths(ledger_path: pathlib.Path):
    yield ledger_path
    for i in range(1, model_proxy.BACKUP_COUNT + 1):
        backup = ledger_path.with_suffix(ledger_path.suffix + f".{i}")
        if backup.exists():
            yield backup


def _read_ledger_rows(run_id: str, ledger_path: pathlib.Path) -> tuple:
    """Scan the live ledger plus rotation backups for rows matching run_id.

    Returns (rows, ledger_available) — ledger_available distinguishes "the ledger
    exists and genuinely has zero rows for this run" from "no ledger data at all"
    (opt-in model-proxy was never enabled). See the spec's "Graceful degradation"
    section.
    """
    rows = []
    available = False
    for path in _iter_ledger_paths(ledger_path):
        if not path.exists():
            continue
        available = True
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("run_id") == run_id:
                    rows.append(row)
        except OSError:
            continue
    return rows, available


def _compute_retry_spend(rows: list) -> dict:
    """Ledger retry_count is always 0 (single-pass forwarder) — retries are
    reconstructed from rows with a failure status instead. See model_proxy.py's
    build_ledger_row docstring.
    """
    failed = [r for r in rows if isinstance(r.get("status"), int) and r["status"] >= 400]
    tokens = sum(
        r.get("gen_ai.usage.input_tokens", 0) + r.get("gen_ai.usage.output_tokens", 0)
        for r in failed
    )
    return {"tokens": tokens, "request_count": len(failed)}


def _compute_ledger_mechanics(rows: list) -> dict:
    total_input = sum(r.get("gen_ai.usage.input_tokens", 0) for r in rows)
    total_cache_read = sum(r.get("gen_ai.usage.cache_read_input_tokens", 0) for r in rows)
    total_cache_creation = sum(r.get("gen_ai.usage.cache_creation_input_tokens", 0) for r in rows)
    cache_denominator = total_input + total_cache_read + total_cache_creation
    cache_hit_ratio = (total_cache_read / cache_denominator) if cache_denominator else None
    tool_schema_overhead_bytes = sum(r.get("tool_bytes", 0) for r in rows)
    system_bytes_vals = [r.get("system_bytes", 0) for r in rows if r.get("system_bytes")]
    largest_tools = []
    for r in rows:
        largest_tools.extend(r.get("largest_tools") or [])
    largest_tools = sorted(largest_tools, key=lambda t: t.get("bytes", 0), reverse=True)[:5]
    return {
        "cache_hit_ratio": cache_hit_ratio,
        "tool_schema_overhead_bytes": tool_schema_overhead_bytes,
        "system_prompt_bytes": max(system_bytes_vals) if system_bytes_vals else 0,
        "largest_tools": largest_tools,
    }


def _compute_harness_economics(
    *, run_id: str, status: str, stages: list, totals: dict,
    started_at: str, completed_at: str, ledger_path: pathlib.Path,
    artifact_check: "dict | None" = None,
) -> dict:
    tokens_in = totals.get("gen_ai.usage.input_tokens")
    tokens_out = totals.get("gen_ai.usage.output_tokens")
    tokens_per_task = None if tokens_in is None or tokens_out is None else tokens_in + tokens_out
    cost_per_task = totals.get("cost_usd")

    outcome = _compute_outcome(status, stages, artifact_check=artifact_check)
    factory_cpm = (
        outcome["score"] * 1_000_000 / tokens_per_task if tokens_per_task else None
    )

    rows, ledger_available = _read_ledger_rows(run_id, ledger_path)
    if ledger_available:
        retry_spend = _compute_retry_spend(rows)
        ledger_mechanics = _compute_ledger_mechanics(rows)
    else:
        retry_spend = {"tokens": None, "request_count": None}
        ledger_mechanics = None

    if outcome["state"] in ("failed", "blocked"):
        failure_spend = {"tokens": tokens_per_task, "basis": "whole_run"}
    else:
        failure_spend = {"tokens": retry_spend["tokens"] or 0, "basis": "retry_only"}

    return {
        "policy_version": POLICY_VERSION,
        "cost_per_task": cost_per_task,
        "tokens_per_task": tokens_per_task,
        "wall_clock_seconds": _wall_clock_seconds(started_at, completed_at),
        "outcome": outcome,
        "factory_cpm": factory_cpm,
        "retry_spend": retry_spend,
        "failure_spend": failure_spend,
        "ledger_available": ledger_available,
        "ledger_rows_correlated": len(rows),
        "ledger_mechanics": ledger_mechanics,
    }


NEEDS_DISCUSSION_LABEL = "needs-discussion"
LABEL_LOOKUP_TIMEOUT_SECONDS = 30
# Same providers CLI the refine-push/plan-push-and-advance DAG nodes shell out to
# ($CLONE_DIR/dark-factory/scripts/factory_core/providers/cli.py — this module's sibling).
_PROVIDERS_CLI = pathlib.Path(__file__).resolve().parent / "providers" / "cli.py"


def _check_needs_discussion(issue) -> tuple:
    """Live needs-discussion lookup for a completed, artifact-less refine/plan run (#447).

    Returns (needs_discussion, lookup_ok). Fails closed: a timeout, non-zero exit,
    unparseable output, or a response without a "labels" key (GitHubTracker.get_item
    returns {} when gh fails) is (False, False) — an unknown label state scores as a
    failure, never as a clean abort, matching the DAG nodes' own `|| true` direction.
    """
    try:
        result = subprocess.run(
            [sys.executable, str(_PROVIDERS_CLI), "tracker", "get",
             "--id", str(issue), "--fields", "labels"],
            capture_output=True, text=True, timeout=LABEL_LOOKUP_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            return False, False
        labels = json.loads(result.stdout)["labels"]
        names = {label.get("name") if isinstance(label, dict) else label for label in labels}
    except Exception:
        return False, False
    return NEEDS_DISCUSSION_LABEL in names, True


def _artifact_is_fresh(path: pathlib.Path, started_at: str) -> bool:
    """True if path's mtime is not older than this run's --started-at (#447 advisory, #573).

    `archon workflow cost --last` can hand back an earlier workflow run's id when the
    current run was never recorded there (see _archon_workflow_run_id) — without this
    check, that earlier run's push-result.json or gate-stage .md file in the sibling
    directory would be mistaken for this run's artifact. Unparseable started_at or an
    unreadable mtime fails closed (not fresh): an unverifiable timestamp must not be
    treated as evidence (a stale review.md could score this run blocked, a stale
    push-result.json a spurious produced_ungated/1.0).
    """
    try:
        started = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return False
    return mtime >= started


def _archon_workflow_run_id(archon_cost_path: "pathlib.Path | None") -> "str | None":
    """Workflow run id from `archon workflow cost --last --json` output, or None.

    Archon gives every DAG node its own $ARTIFACTS_DIR, runs/<workflow run id> (see
    FACTORY_RUN_CONFIG_JSON in entrypoint.sh), a sibling of the entrypoint's runs/<RUN_ID>
    that cmd_assemble receives as --artifacts-dir — so refine-push/plan-push-and-advance's
    push-result.json (#447) and the validate/conformance/code-review nodes' gate-stage
    .md files (#573) live under this id, not under args.artifacts_dir.
    """
    if archon_cost_path is None or not archon_cost_path.exists():
        return None
    try:
        run_obj = _find_run_obj(archon_cost_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    run_id = (run_obj or {}).get("run_id") or (run_obj or {}).get("runId")
    if not isinstance(run_id, str) or not _SAFE_RUN_ID_RE.match(run_id):
        return None
    return run_id


def cmd_assemble(args) -> None:
    artifacts_dir = pathlib.Path(args.artifacts_dir)
    out_file = pathlib.Path(args.out_file)

    # Resolved once, shared by the gate-stage loop and the push-result.json block (#573).
    archon_path = pathlib.Path(args.archon_cost_json) if args.archon_cost_json else None
    workflow_run_id = _archon_workflow_run_id(archon_path)
    workflow_dir = artifacts_dir.parent / workflow_run_id if workflow_run_id is not None else None
    # #573 advisory: when the resolved workflow run is this entrypoint's own run,
    # workflow_dir and artifacts_dir are the same directory — the "sibling" probe below
    # would compare a gate file's mtime against itself and could log it as both own_dir
    # and stale. Skip the probe in that case; the plain own_dir lookup already covers it.
    workflow_dir_is_own_dir = (
        workflow_dir is not None and workflow_dir.resolve() == artifacts_dir.resolve()
    )

    stages = []
    artifacts: dict = {}
    artifact_names = ["validation", "conformance", "review", "conflict_resolution"]

    # #573: the validate, conformance and code-review DAG nodes write validation.md,
    # conformance.md and review.md into archon's per-workflow-run $ARTIFACTS_DIR, a
    # sibling of args.artifacts_dir (see _archon_workflow_run_id), never into the
    # entrypoint's own directory. Same resolution as #447's push-result.json below: the
    # own-dir path is the default and a fresh sibling copy wins. A stale sibling (an
    # earlier workflow run's leftover) is never read: its review.md could score this run
    # blocked. conflict_resolution.md is not a gate stage and keeps its own-dir lookup.
    gate_artifact_lookup = {
        "workflow_run_id": workflow_run_id,
        "workflow_dir_exists": workflow_dir is not None and workflow_dir.is_dir(),
        "found": [],
        "stale": [],
        "own_dir": [],
    }
    for name in artifact_names:
        md_path = artifacts_dir / f"{name}.md"
        source = "own_dir"
        if name in GATE_STAGE_NAMES and workflow_dir is not None and not workflow_dir_is_own_dir:
            sibling = workflow_dir / f"{name}.md"
            if sibling.exists():
                if _artifact_is_fresh(sibling, args.started_at):
                    md_path, source = sibling, "found"
                else:
                    gate_artifact_lookup["stale"].append(name)
        if md_path.exists():
            if name in GATE_STAGE_NAMES:
                gate_artifact_lookup[source].append(name)
            content = md_path.read_text(encoding="utf-8")
            artifacts[name] = content
            stage = _parse_artifact_stage(name, content)
            if stage:
                stages.append(stage)

    if (
        args.status == "completed"
        and args.intent in ("fix", "continue")
        and not any(s["stage"] in GATE_STAGE_NAMES for s in stages)
    ):
        print(
            "WARNING: run-record: no gate-stage artifacts found "
            f"(workflow_run_id={workflow_run_id}, "
            f"workflow_dir_exists={gate_artifact_lookup['workflow_dir_exists']})",
            file=sys.stderr,
        )

    stderr_text = ""
    stderr_file = getattr(args, "archon_cost_stderr_file", None)
    if stderr_file:
        try:
            stderr_text = pathlib.Path(stderr_file).read_text(encoding="utf-8")
        except OSError:
            stderr_text = ""
    exit_code = getattr(args, "archon_cost_exit_code", 0) or 0
    nodes, archon_cost_capture = _parse_archon_cost_with_capture(
        archon_path, exit_code=exit_code, stderr_text=stderr_text
    )

    # Capture failed -> totals null, not zero (df#300); otherwise classify every node's
    # cost capture (#453) — a measured zero (no AI nodes) still totals 0.
    nodes, totals = _capture_totals(
        nodes,
        capture_ok=bool(archon_cost_capture.get("ok")),
        clone_dir=getattr(args, "clone_dir", None) or ".",
        archon_log_file=getattr(args, "archon_log_file", None),
        issue=args.issue,
        run_id=args.run_id,
    )

    from . import adapter
    try:
        loops = adapter.get(args.clone_dir, "loops") or []
    except Exception:
        loops = []

    # #447: a refine/plan run's whole deliverable is one committed spec/plan doc.
    # refine-push/plan-push-and-advance write push-result.json only when
    # push_gate_check.sh found it — into archon's per-workflow-run $ARTIFACTS_DIR, a
    # sibling of args.artifacts_dir (see _archon_workflow_run_id). On a completed run its
    # absence means no artifact; then the live needs-discussion label separates a clean
    # abort from a silent death. Never looked up on the failure-trap path (--status
    # failed scores failed anyway).
    artifact_check = None
    artifact_push_path = artifacts_dir / "push-result.json"
    if args.intent in ("refine", "plan"):
        if workflow_dir is not None:
            workflow_push_path = workflow_dir / "push-result.json"
            if workflow_push_path.exists() and _artifact_is_fresh(
                workflow_push_path, args.started_at
            ):
                artifact_push_path = workflow_push_path
        if artifact_push_path.exists():
            artifact_check = {"artifact_present": True}
        elif args.status == "completed":
            needs_discussion, lookup_ok = _check_needs_discussion(args.issue)
            artifact_check = {
                "artifact_present": False,
                "needs_discussion": needs_discussion,
                "label_lookup": "ok" if lookup_ok else "error",
                "workflow_run_id": workflow_run_id,
            }

    run_record = {
        "run_id": args.run_id,
        "issue_number": args.issue,
        "intent": args.intent,
        "started_at": args.started_at or _timestamp(),
        "completed_at": _timestamp(),
        "status": args.status,
        "stages": stages,
        "nodes": nodes,
        "artifacts": artifacts,
        "loops": loops,
        "archon_cost_capture": archon_cost_capture,
        "totals": totals,
    }
    if artifact_check is not None:
        run_record["artifact_check"] = artifact_check  # replayed by backfill-economics
    # Scoring-neutral lookup evidence (#573): never passed to _compute_outcome. It is what
    # separates post-fix records from the historical blind-scored ones (no policy bump).
    run_record["gate_artifact_lookup"] = gate_artifact_lookup
    run_record["harness_economics"] = _compute_harness_economics(
        run_id=args.run_id,
        status=run_record["status"],
        stages=stages,
        totals=run_record["totals"],
        started_at=run_record["started_at"],
        completed_at=run_record["completed_at"],
        ledger_path=pathlib.Path(args.ledger_path) if args.ledger_path else LEDGER_PATH,
        artifact_check=artifact_check,
    )

    trace_path = artifacts_dir / "memory-trace.json"
    if trace_path.exists():
        try:
            run_record["memory_trace"] = json.loads(trace_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Written by refine-push/plan-push-and-advance (#473): the verified push outcome
    # (pushed, branch, local/remote sha, captured error). Same optional, schema-free
    # fold as memory-trace.json — these are plain DAG bash steps, not verdict gates.
    # Folded from artifact_push_path: for refine/plan, the fresh sibling copy in archon's
    # workflow-run directory when the artifact_check block above found one (#573); every
    # other intent reads --artifacts-dir only (no implement-path node writes the file).
    if artifact_push_path.exists():
        try:
            run_record["push_result"] = json.loads(artifact_push_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(run_record, indent=2), encoding="utf-8")

    ts = _timestamp()
    for stage in stages:
        record: dict = {
            "run_id": args.run_id,
            "issue_number": args.issue,
            "intent": args.intent,
            "stage": stage["stage"],
            "verdict": stage["verdict"],
            "side_effect_level": getattr(args, "side_effect_level", None) or 1,
            "side_effect_profile": getattr(args, "side_effect_profile", None) or "unknown",
            "gen_ai.system": "dark-factory",
            "gen_ai.operation.name": f"stage.{stage['stage']}",
            "gen_ai.usage.input_tokens": None,
            "gen_ai.usage.output_tokens": None,
            "cost_usd": None,
            "duration_ms": None,
            "timestamp": ts,
        }
        extra = {k: v for k, v in stage.items() if k not in ("stage", "verdict")}
        if extra:
            record["detail"] = extra
        _append_jsonl(record)
        _post_seq(record)

    durable_path = _durable_run_record_path(args.run_id)
    if durable_path is not None:
        try:
            durable_path.parent.mkdir(parents=True, exist_ok=True)
            durable_path.write_text(json.dumps(run_record, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"run-record: failed to write durable record {durable_path}: {exc}", file=sys.stderr)


def _build_issue_economics(issue_number: int, *, ledger_path: pathlib.Path, artifacts_root: pathlib.Path) -> dict:
    """Read-only rollup over request-ledger.jsonl, grouped by run/issue/phase.

    Overlays dollar/outcome figures from each run's own retained run-record.json —
    never recomputes them independently (see spec's "Cost source" section). Produces
    no new persisted file.
    """
    runs: dict = {}
    for path in _iter_ledger_paths(ledger_path):
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if int(row.get("issue_number") or 0) != issue_number:
                    continue
                run_id = row.get("run_id", "unknown")
                bucket = runs.setdefault(run_id, {
                    "intent": row.get("intent", "unknown"),
                    "stage": row.get("stage", "unknown"),
                    "rows": [],
                })
                bucket["rows"].append(row)
        except OSError:
            continue

    result_runs = {}
    for run_id, bucket in runs.items():
        rows = bucket["rows"]
        entry = {
            "intent": bucket["intent"],
            "stage": bucket["stage"],
            "request_count": len(rows),
            "retry_spend": _compute_retry_spend(rows),
            "ledger_mechanics": _compute_ledger_mechanics(rows),
            "cost_usd": None,
            "outcome_state": None,
            "factory_cpm": None,
        }
        record_path = _run_record_path(artifacts_root, run_id)
        if record_path is not None and record_path.exists():
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
                entry["cost_usd"] = record.get("totals", {}).get("cost_usd")
                he = record.get("harness_economics") or {}
                entry["outcome_state"] = he.get("outcome", {}).get("state")
                entry["factory_cpm"] = he.get("factory_cpm")
            except (json.JSONDecodeError, OSError):
                pass
        result_runs[run_id] = entry

    return {"issue_number": issue_number, "runs": result_runs}


def cmd_issue_economics(args) -> None:
    result = _build_issue_economics(
        args.issue,
        ledger_path=pathlib.Path(args.ledger_path) if args.ledger_path else LEDGER_PATH,
        artifacts_root=pathlib.Path(args.artifacts_root),
    )
    print(json.dumps(result, indent=2))


def _backfill_run_economics(run_id: str, *, artifacts_root: pathlib.Path, ledger_path: pathlib.Path) -> bool:
    """Best-effort, degrade-only recompute of harness_economics for a retained run.

    Returns False (skipped, not an error) when the run's run-record.json directory
    no longer exists — bounded by artifact retention, per the spec's backfill section.
    """
    record_path = _run_record_path(artifacts_root, run_id)
    if record_path is None or not record_path.exists():
        return False
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    record["harness_economics"] = _compute_harness_economics(
        run_id=run_id,
        status=record.get("status", "completed"),
        stages=record.get("stages", []),
        totals=record.get("totals", {}),
        started_at=record.get("started_at", ""),
        completed_at=record.get("completed_at", ""),
        ledger_path=ledger_path,
        # Replay cmd_assemble's recorded refine/plan input (#447) — never a new lookup.
        # Absent (pre-1.1 records, other intents) -> None -> legacy scoring.
        artifact_check=record.get("artifact_check"),
    )
    record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return True


def cmd_backfill_economics(args) -> None:
    ok = _backfill_run_economics(
        args.run_id,
        artifacts_root=pathlib.Path(args.artifacts_root),
        ledger_path=pathlib.Path(args.ledger_path) if args.ledger_path else LEDGER_PATH,
    )
    print(json.dumps({"run_id": args.run_id, "backfilled": ok}))


def main() -> None:
    parser = argparse.ArgumentParser(description="Dark factory run record")
    sub = parser.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="Write one stage event")
    r.add_argument("--run-id", required=True)
    r.add_argument("--issue", type=int, required=True)
    r.add_argument("--intent", required=True)
    r.add_argument("--stage", required=True)
    r.add_argument("--verdict", required=True)
    r.add_argument("--origin", default="factory",
                   help='row producer: "factory" (default) or "target-loop:<loop_name>"')
    r.add_argument("--side-effect-level", type=int, default=None)
    r.add_argument("--side-effect-profile", default=None)
    r.add_argument("--tokens-in", type=int, default=None)
    r.add_argument("--tokens-out", type=int, default=None)
    r.add_argument("--cost-usd", type=float, default=None)
    r.add_argument("--duration-ms", type=int, default=None)
    r.add_argument("--detail", nargs="*", metavar="KEY=VAL")

    he = sub.add_parser("health-event", help="Emit a non-blocking recurrence-detection signal")
    he.add_argument("--run-id", required=True)
    he.add_argument("--issue", type=int, required=True)
    he.add_argument("--event", required=True)
    he.add_argument("--detail", nargs="*", metavar="KEY=VAL")

    a = sub.add_parser("assemble", help="Assemble end-of-run record from artifacts")
    a.add_argument("--run-id", required=True)
    a.add_argument("--issue", type=int, required=True)
    a.add_argument("--intent", required=True)
    a.add_argument("--started-at", default="")
    a.add_argument("--artifacts-dir", required=True)
    a.add_argument("--archon-cost-json")
    a.add_argument("--archon-cost-exit-code", type=int, default=0)
    a.add_argument("--archon-cost-stderr-file")
    a.add_argument("--archon-log-file", default=None,
                   help="tee'd `archon workflow run` output, for per-node status (#453)")
    a.add_argument("--out-file", required=True)
    a.add_argument("--status", default="completed")
    a.add_argument("--ledger-path", default=None)
    a.add_argument("--clone-dir", default=os.environ.get("CLONE_DIR", "."))
    a.add_argument("--side-effect-level", type=int, default=None)
    a.add_argument("--side-effect-profile", default=None)

    ie = sub.add_parser("issue-economics", help="Read-only cross-run rollup for an issue")
    ie.add_argument("--issue", type=int, required=True)
    ie.add_argument("--artifacts-root", required=True)
    ie.add_argument("--ledger-path", default=None)

    be = sub.add_parser("backfill-economics", help="Degrade-only historical harness_economics recompute")
    be.add_argument("--run-id", required=True)
    be.add_argument("--artifacts-root", required=True)
    be.add_argument("--ledger-path", default=None)

    parsed = parser.parse_args()
    if parsed.cmd == "record":
        try:
            cmd_record(parsed)
        except OSError:
            traceback.print_exc()   # the CLI path is the interactive/debug one
            sys.exit(4)
    elif parsed.cmd == "health-event":
        cmd_health_event(parsed)
    elif parsed.cmd == "assemble":
        cmd_assemble(parsed)
    elif parsed.cmd == "issue-economics":
        cmd_issue_economics(parsed)
    elif parsed.cmd == "backfill-economics":
        cmd_backfill_economics(parsed)


if __name__ == "__main__":
    main()
