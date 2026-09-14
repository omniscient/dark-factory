"""Shape factory run data for runs.sh. Read-only; stdin in, text out.

  python run_summary.py records   # stdin: one run-record JSON per line
  python run_summary.py nodes     # stdin: raw `docker logs` of a run container
"""
import json
import re
import sys


def records() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        nodes = d.get("nodes") or []
        cost = sum((n.get("cost_usd") or 0) for n in nodes)
        ms = sum((n.get("duration_ms") or 0) for n in nodes)
        hot = sorted(nodes, key=lambda n: -(n.get("cost_usd") or 0))[:3]
        hot_s = ", ".join("%s=$%.2f" % (n.get("node_id"), n.get("cost_usd") or 0) for n in hot)
        print(
            "#%-5s %-9s %-10s %-16s %5.1fmin $%6.2f nodes=%-2d run=%s  %s"
            % (
                d.get("issue_number"),
                d.get("intent", "?"),
                d.get("status", "?"),
                str(d.get("started_at", ""))[:16],
                ms / 60000,
                cost,
                len(nodes),
                str(d.get("run_id", ""))[:8],
                hot_s,
            )
        )


def nodes() -> None:
    wanted = re.compile(
        r"dag_node_(started|completed|failed)|side_effect_level=|side-effect guard:"
        r"|rate_limit_event|dag_workflow_finished"
    )
    for line in sys.stdin:
        if not wanted.search(line):
            continue
        # archon logs a rate_limit_event with status "allowed" on every call; only the
        # rejected/paused ones are events (the "allowed" flood hid the node lines).
        if "rate_limit_event" in line and '"status":"allowed"' in line.replace(" ", ""):
            continue
        m = re.search(r"\{.*\}", line)
        d = None
        if m:
            try:
                d = json.loads(m.group(0))
            except json.JSONDecodeError:
                d = None
        if d and "nodeId" in d:
            print("%-19s %-22s %s" % (str(d.get("time", ""))[:19], str(d.get("msg", ""))[:22], d.get("nodeId", "")))
        else:
            print(line.strip()[:160])


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "records"
    {"records": records, "nodes": nodes}[mode]()
