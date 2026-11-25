"""Pure formatting/bookkeeping for the Dark Factory cost-report comment (#182).

Extracted from entrypoint.sh's post_cost_report(). No gh, no docker, no archon, no
network calls in this module — see cli.py's cost-report subcommands for the IO seam.
"""
import json
import math
import re
from decimal import Decimal
from pathlib import Path


def _round_half_away_from_zero(x: float) -> int:
    """Matches jq's `round` (C round()), NOT Python's banker's-rounding builtin."""
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


def _trim_decimal(value: float) -> str:
    """jq's number-to-string: whole results print without a trailing '.0'."""
    if value == int(value):
        return str(int(value))
    return f"{value:.1f}"


def format_tokens_table(n: int) -> str:
    """Mirrors the jq `fmt_tokens` def used for per-node table cells (:424-426)."""
    if n >= 1_000_000:
        return f"{_trim_decimal(_round_half_away_from_zero(n / 1_000_000 * 10) / 10)}M"
    if n >= 1000:
        return f"{_trim_decimal(_round_half_away_from_zero(n / 1000 * 10) / 10)}K"
    return str(n)


def format_tokens_cumulative(n: int) -> str:
    """Mirrors the shell `fmt_tokens` bash function used for cumulative/subtotal/
    savings lines (:490-499) — `bc scale=1` truncates and always shows 1 decimal."""
    if n >= 1_000_000:
        tenths = (n * 10) // 1_000_000
        return f"{tenths // 10}.{tenths % 10}M"
    if n >= 1000:
        tenths = (n * 10) // 1000
        return f"{tenths // 10}.{tenths % 10}K"
    return str(n)


def format_duration(ms: int) -> str:
    """Mirrors the jq `fmt_dur` def (:427-429)."""
    if ms < 1000:
        return f"{ms}ms"
    if ms < 60_000:
        return f"{_trim_decimal(_round_half_away_from_zero(ms / 100) / 10)}s"
    minutes = ms // 60_000
    seconds = _round_half_away_from_zero((ms % 60_000) / 1000)
    return f"{minutes}m {seconds}s"


def format_cost(usd) -> str:
    """Mirrors the jq `fmt_cost` def (:430) — per-node table cells only; the
    Subtotal/Total lines use the RAW pass-through value, not this formatter
    (see render()'s `_passthrough_num`, Task 4).

    NOTE: uses an integer-numerator formatter, not `_trim_decimal` (which is
    fixed at 1 decimal place for tokens/duration) — cost needs up to 4 decimal
    places with trailing zeros stripped, e.g. $0.0207, verified against real
    captured output in Task 4."""
    numerator = _round_half_away_from_zero(usd * 10000)
    sign = "-" if numerator < 0 else ""
    numerator = abs(numerator)
    whole, frac = divmod(numerator, 10000)
    if frac == 0:
        return f"{sign}${whole}"
    frac_str = f"{frac:04d}".rstrip("0")
    return f"{sign}${whole}.{frac_str}"


def _jq_alt(value, default):
    """jq's `//` alternative operator: only null/false trigger the fallback."""
    return default if value is None or value is False else value


def format_economics_line(run_record: dict) -> str:
    """Mirrors the harness_economics extraction at entrypoint.sh:409-418.

    Absent-tolerant: older run-record.json files predate harness_economics.
    """
    he = run_record.get("harness_economics") or {}
    outcome = he.get("outcome") or {}
    state = outcome.get("state")
    if not state:
        return ""
    cpm = he.get("factory_cpm")
    cpm_fmt = "n/a" if cpm is None else f"{round(cpm)}"
    score = _jq_alt(outcome.get("score"), "n/a")
    return f"**Factory CPM:** {cpm_fmt} | **Outcome:** {state} (score {score})"


PUSH_ERROR_MAX_CHARS = 200


def format_push_line(run_record: dict) -> str:
    """One-line push outcome for the cost-report comment (#473).

    Absent-tolerant: older run-record.json files predate push_result, and a
    verified push prints nothing — only a failed/unverified push is surfaced.
    """
    push = run_record.get("push_result")
    if not isinstance(push, dict) or push.get("pushed") is not False:
        return ""
    error = str(push.get("error") or "").strip()
    first = error.splitlines()[0].strip() if error else "no error text captured"
    if len(first) > PUSH_ERROR_MAX_CHARS:
        first = first[:PUSH_ERROR_MAX_CHARS] + "…"
    return f"**Push:** FAILED — {first}"


# Optimizer deliveries that count toward the headline "Context savings" (#511). Mirrors
# scripts/context_budget.py _DELIVERED_SAVINGS (a test keeps the two in sync): memory
# is delivered to the refine/plan/implement(+continue) prompts, the ranked diff to
# conformance/code-review/docs-conformance (diff_rank.py records no phase, every record counts).
DELIVERED_MEMORY_PHASES = frozenset({"refine", "plan", "implement", "continue"})
DELIVERED_SAVINGS_FILE = "delivered-savings.jsonl"
BUDGET_LEGACY_FILE = "context-budget.json"


def load_budgets(artifacts_dir: "str | Path") -> "list[dict]":
    """All per-scenario budget artifacts of a run (`context-budget.<scenario>.json`),
    sorted by name. Falls back to the legacy single `context-budget.json` only when no
    per-scenario file exists (artifacts written before #511)."""
    d = Path(artifacts_dir)
    files = sorted(d.glob("context-budget.*.json"))
    if not files and (d / BUDGET_LEGACY_FILE).is_file():
        files = [d / BUDGET_LEGACY_FILE]
    out = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def load_deliveries(artifacts_dir: "str | Path") -> "list[dict] | None":
    """Optimizer-measured deliveries (memory_retrieve.py / diff_rank.py append one line
    per run), or None when the run predates them (caller falls back to the budget
    artifacts' own savings figures)."""
    f = Path(artifacts_dir) / DELIVERED_SAVINGS_FILE
    if not f.is_file():
        return None
    out = []
    try:
        lines = f.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _delivered_savings(deliveries: "list[dict]") -> "tuple[int, int]":
    """(saved, baseline) summed over deliveries that reached a prompt."""
    saved = baseline = 0
    for rec in deliveries:
        kind = rec.get("kind")
        if kind == "memory" and rec.get("phase") not in DELIVERED_MEMORY_PHASES:
            continue
        if kind not in ("memory", "diff"):
            continue
        try:
            base = int(rec.get("baseline_tokens", 0))
            got = int(rec.get("delivered_tokens", 0))
        except (TypeError, ValueError):
            continue
        baseline += base
        saved += max(0, base - got)
    return saved, baseline


def _trim_line(budget: dict) -> str:
    over_budget = budget.get("over_budget")
    would_trim = budget.get("would_trim")
    caps_str = ", ".join(
        f"{k}→{v}" for k, v in (budget.get("derived_caps") or {}).items()
    )
    scenario = budget.get("scenario", "unknown")
    scenario_budget = budget.get("scenario_budget", 0)
    if over_budget is True:
        reserved = budget.get("reserved_tokens", 0)
        return (
            f"**⚠️ Over budget ({scenario}): "
            f"{format_tokens_cumulative(reserved)} reserved / "
            f"{format_tokens_cumulative(scenario_budget)} budget — "
            f"trimmed: {caps_str}**"
        )
    if would_trim is True:
        estimated = budget.get("estimated_input_tokens")
        if estimated:
            label, value = "est", estimated
        else:
            label, value = "rsv", budget.get("reserved_tokens", 0)
        return (
            f"**Budget trim ({scenario}): {label} "
            f"{format_tokens_cumulative(value)} / "
            f"{format_tokens_cumulative(scenario_budget)} budget — "
            f"capped: {caps_str}**"
        )
    return ""


def format_savings_block(budget: "dict | list | None",
                         deliveries: "list[dict] | None" = None) -> str:
    """Savings/budget lines for the cost report (context-budget schema v2).

    `budget` is one context-budget artifact or the list of a run's per-scenario
    artifacts (#511). When `deliveries` is given (the optimizers' own append-only
    records), the headline "Context savings" is baseline-minus-delivered for the text
    that really went into the prompts, summed over every scenario of the run; without
    it the budget artifacts' own savings_tokens are summed (older runs).
    """
    if not budget:
        return ""
    budgets = budget if isinstance(budget, list) else [budget]
    valid = []
    for b in budgets:
        if not isinstance(b, dict) or not b:
            continue
        try:
            if int(b.get("schema_version", 1)) < 2:
                continue
        except (TypeError, ValueError):
            continue
        valid.append(b)
    if not valid:
        return ""

    lines = []

    if deliveries is not None:
        savings_tokens, baseline = _delivered_savings(deliveries)
        savings_pct = round(savings_tokens / baseline * 100, 1) if baseline > 0 else 0
    elif len(valid) == 1:
        savings_tokens = valid[0].get("savings_tokens", 0) or 0
        savings_pct = valid[0].get("savings_pct", 0)
    else:
        savings_tokens = sum(b.get("savings_tokens", 0) or 0 for b in valid)
        baseline = sum(b.get("baseline_input_tokens", 0) or 0 for b in valid)
        savings_pct = round(savings_tokens / baseline * 100, 1) if baseline > 0 else 0
    if savings_tokens > 0:
        lines.append(
            f"**Context savings: {format_tokens_cumulative(savings_tokens)} "
            f"tokens ({savings_pct}%)**"
        )

    # Savings that were computed but never reached a prompt (#487): shown separately,
    # never added to the headline figure above. Summed across the run's scenarios.
    measured_only = sum(b.get("measured_only_savings_tokens", 0) or 0 for b in valid)
    if measured_only > 0:
        names = []
        for b in valid:
            for n in b.get("measured_only_sections") or []:
                if n not in names:
                    names.append(n)
        suffix = f" ({', '.join(names)})" if names else ""
        lines.append(
            f"Measured-only (not delivered to the prompt): "
            f"{format_tokens_cumulative(measured_only)} tokens{suffix}"
        )

    fallback_events = []
    for b in valid:
        fallback_events.extend(b.get("fallback_events") or [])
    if fallback_events:
        parts = [f"{ev['section']}: {ev['reason']}" for ev in fallback_events]
        lines.append("**Fallbacks:** " + ", ".join(parts))

    for b in valid:
        trim = _trim_line(b)
        if trim:
            lines.append(trim)

    if not lines:
        return ""
    return "\n" + "\n".join(lines)


def check_renderable(run_record: dict) -> "dict | None":
    """Requirement 1a's guard — the ONLY place `.nodes` length is inspected.

    Returns None when there's something to render, else a diagnostic dict.
    """
    nodes = run_record.get("nodes") or []
    if nodes:
        return None
    capture = run_record.get("archon_cost_capture")
    capture = capture if isinstance(capture, dict) else {}
    capture_ok = capture["ok"] if "ok" in capture else "unknown"
    return {
        "nodes_count": len(nodes),
        "capture_ok": capture_ok,
        "capture_exit_code": _jq_alt(capture.get("exit_code"), "unknown"),
        "capture_stderr": _jq_alt(capture.get("stderr_excerpt"), ""),
    }


def _jqstr(value) -> str:
    """jq -r's raw text rendering: lowercase booleans, everything else via str()."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def format_missing_diagnostic(diagnostic: dict, run_id: str, issue: int) -> str:
    """Reproduces entrypoint.sh:445 byte-for-byte — the string the regression
    test greps for."""
    return (
        f"ERROR: cost report has zero node rows for run {run_id or 'unknown'} "
        f"(issue #{issue}); nodes={diagnostic['nodes_count']}, "
        f"archon_cost_capture.ok={_jqstr(diagnostic['capture_ok'])}, "
        f"archon_cost_exit_code={_jqstr(diagnostic['capture_exit_code'])}, "
        f"stderr={diagnostic['capture_stderr']}"
    )


_CUMULATIVE_MARKER_RE = re.compile(
    r"<!-- cumulative: cost=([0-9.]+|n/a) in=(\d+) out=(\d+) -->"
)


def parse_prior_cumulative(prior_comment_body: str) -> dict:
    """Reproduces the sed/grep -oP parsing at entrypoint.sh:474-477.

    The `gh api` fetch that produces prior_comment_body stays bash-side; this
    function only parses the already-fetched string.
    """
    if not prior_comment_body:
        return {"prior_runs": "", "prev_cost": "0", "prev_in": 0, "prev_out": 0,
                 "run_count": 0}

    lines = prior_comment_body.splitlines()
    prior_run_lines = []
    in_run_block = False
    for line in lines:
        if line.startswith("### Run:"):
            in_run_block = True
        if in_run_block:
            if line.strip() == "---":
                break
            prior_run_lines.append(line)
    # Mirrors bash's $(...) command substitution, which strips ALL trailing
    # newlines (not just one) from the sed '/^### Run:/,/^---$/p' | head -n -1
    # pipeline's output — verified against real bash (see "Deviations" above).
    prior_runs = "\n".join(prior_run_lines).rstrip("\n")

    match = _CUMULATIVE_MARKER_RE.search(prior_comment_body)
    if match:
        # prev_cost stays a STRING — it feeds _bc_add (render(), Task 4),
        # which needs the exact decimal text bc originally produced, not a
        # float (float arithmetic can diverge from bc's arbitrary-precision
        # decimal addition — verified case: bc's "0 + 0.0207" -> ".0207").
        prev_cost, prev_in, prev_out = match.group(1), int(match.group(2)), int(match.group(3))
    else:
        prev_cost, prev_in, prev_out = "0", 0, 0

    # Only header lines count: an error first line mentioning "### Run:" inline
    # must not inflate the "N run(s)" header (#479).
    run_count = sum(
        1 for line in prior_comment_body.splitlines() if line.startswith("### Run:")
    )

    return {
        "prior_runs": prior_runs,
        "prev_cost": prev_cost,
        "prev_in": prev_in,
        "prev_out": prev_out,
        "run_count": run_count,
    }


def _decimal_scale(s: str) -> int:
    return len(s.split(".", 1)[1]) if "." in s else 0


def _bc_add(a: str, b: str) -> str:
    """Reproduces bc's default-scale decimal addition (entrypoint.sh:482):
    result scale = max(scale of the two operands); a leading '0' before the
    decimal point is dropped for magnitude < 1 — verified directly against
    `bc` for whole-number, matching-scale, mismatched-scale, and sub-1 cases."""
    scale = max(_decimal_scale(a), _decimal_scale(b))
    total = Decimal(a) + Decimal(b)
    quant = Decimal(1).scaleb(-scale) if scale > 0 else Decimal(1)
    total = total.quantize(quant)
    s = format(total, "f")
    if s.startswith("0."):
        s = s[1:]
    elif s.startswith("-0."):
        s = "-" + s[2:]
    return s


def _passthrough_num(value) -> str:
    """jq -r on a field that passes through UNMODIFIED (no arithmetic applied,
    e.g. `.totals.cost_usd // 0`) preserves the original JSON decimal literal
    text exactly (jq >=1.7's decimal-literal preservation) -- a JSON `2.0`
    prints "2.0", not "2". This is the OPPOSITE convention from a *computed*
    value (e.g. fmt_cost's round(...)), which always drops a trailing ".0" —
    keep this separate from _trim_decimal, don't unify them. Verified:
    `echo '{"x":2.0}' | jq -r '.x'` -> "2.0". Python's json.loads preserves
    the int-vs-float distinction from the source text, and float repr()
    reproduces the shortest round-tripping decimal, which matches jq's
    preserved text for the values exercised here."""
    if isinstance(value, int):
        return str(value)
    return repr(value)


COST_MARKER = "<!-- dark-factory-cost-report -->"

# Token/duration cell for a node whose usage Archon did not report (#453) — never "0".
MISSING_CELL = "—"

# #608: a local-routed run records cost_usd null (hybrid contract §4.10) — rendered as
# unknown, never $0. The cumulative marker carries the short parseable token so the
# next run's parse_prior_cumulative keeps working; the human-facing lines carry the
# full phrase.
UNKNOWN_COST = "n/a (local endpoint)"
UNKNOWN_COST_MARKER = "n/a"


def format_node_row(node: dict) -> str:
    """One per-node table row. A cost-missing node (#453) shows its reason in the cost
    cell and MISSING_CELL for its token cells (and for a duration it never reported),
    never a misleading $0/0/0; every other node renders exactly as before."""
    if node.get("cost_capture") == "missing":
        duration_ms = node.get("duration_ms")
        duration = MISSING_CELL if duration_ms is None else format_duration(duration_ms)
        return (
            f"| {node.get('node_id', '')} | {node.get('model') or ''} | "
            f"{MISSING_CELL} | {MISSING_CELL} | "
            f"missing ({node.get('reason') or 'unknown'}) | {duration} |"
        )
    # #608: a null cost (local endpoint) renders as unknown, never $0 — a measured
    # zero (e.g. a bash: node) is 0, not None, and still renders $0.
    cost_cell = (
        UNKNOWN_COST if node.get("cost_usd") is None
        else format_cost(node.get("cost_usd", 0) or 0)
    )
    return (
        f"| {node.get('node_id', '')} | {node.get('model') or ''} | "
        f"{format_tokens_table(node.get('gen_ai.usage.input_tokens', 0) or 0)} | "
        f"{format_tokens_table(node.get('gen_ai.usage.output_tokens', 0) or 0)} | "
        f"{cost_cell} | "
        f"{format_duration(node.get('duration_ms', 0) or 0)} |"
    )


def format_partial_capture_note(totals: dict) -> str:
    """Partial-capture warning line under the Subtotal row (#453); "" unless
    totals.cost_capture == "partial", so a complete run's body is unchanged."""
    if totals.get("cost_capture") != "partial":
        return ""
    missing = totals.get("missing_nodes") or []
    noun = "node" if len(missing) == 1 else "nodes"
    listed = ", ".join(f"{m.get('node_id', '')}: {m.get('reason', '')}" for m in missing)
    return (
        f"> ⚠️ Partial cost capture — {len(missing)} {noun} unreported ({listed}). "
        f"Known spend: {format_cost(totals.get('known_cost_usd') or 0)} "
        f"({format_tokens_cumulative(totals.get('known_input_tokens') or 0)} in / "
        f"{format_tokens_cumulative(totals.get('known_output_tokens') or 0)} out); "
        f"true total is higher."
    )


def render(run_record: dict, prior_comment_body: str, timestamp: str, intent: str,
           product_name: str = "Dark Factory", budget: "dict | list | None" = None,
           deliveries: "list[dict] | None" = None) -> str:
    """Top-level entry point — assembles the full comment body.

    Callers must have already confirmed `check_renderable(run_record) is None`;
    this function does not itself special-case empty `nodes`. `intent` and
    `budget` are read from the bash caller's environment/`context-budget.json`
    respectively, not from `run_record` — see "Deviations from the spec" above.
    """
    status = run_record.get("status") or "completed"
    totals = run_record.get("totals") or {}
    is_partial = totals.get("cost_capture") == "partial"
    if is_partial:
        # A partial run's fully-measured totals are null (#453): its Subtotal, header
        # total and cumulative marker carry the known_* lower bound, never 0.
        total_cost_raw = totals.get("known_cost_usd")
        total_in = totals.get("known_input_tokens") or 0
        total_out = totals.get("known_output_tokens") or 0
    else:
        total_cost_raw = totals.get("cost_usd")
        total_in = totals.get("gen_ai.usage.input_tokens") or 0
        total_out = totals.get("gen_ai.usage.output_tokens") or 0
    # #608: an unknown cost (a local-routed run records cost_usd null — contract §4.10)
    # is never coerced to 0; it renders as unknown in the Subtotal and, through the
    # cumulative, in the header. A measured zero is 0, not None, and renders $0.
    cost_unknown = total_cost_raw is None
    if cost_unknown:
        total_cost_str = UNKNOWN_COST
        total_cost_display = UNKNOWN_COST
    else:
        total_cost_str = _passthrough_num(total_cost_raw)
        total_cost_display = f"${total_cost_str}"
    partial_note = format_partial_capture_note(totals)
    partial_segment = f"\n\n{partial_note}" if partial_note else ""

    economics_line = format_economics_line(run_record)
    economics_segment = f"\n{economics_line}" if economics_line else ""
    push_line = format_push_line(run_record)
    push_segment = f"\n\n{push_line}" if push_line else ""

    run_rows = "\n".join(format_node_row(node) for node in run_record.get("nodes") or [])

    prior = parse_prior_cumulative(prior_comment_body)
    # #608: once any run's cost is unknown — this one, or one in the parsed prior
    # history (marker cost=n/a) — the cumulative is unknown: it can't be recomputed,
    # and must never read as prior + 0. The marker keeps the short parseable token.
    cum_unknown = cost_unknown or prior["prev_cost"] == UNKNOWN_COST_MARKER
    if cum_unknown:
        cum_cost = UNKNOWN_COST_MARKER
        cum_display = UNKNOWN_COST
    else:
        cum_cost = _bc_add(prior["prev_cost"], total_cost_str)
        cum_display = f"${cum_cost}"
    cum_in = prior["prev_in"] + total_in
    cum_out = prior["prev_out"] + total_out
    run_count = prior["run_count"] + 1

    # A partial run's cumulative header total is a lower bound, and so is every later
    # one (#453 advisory). The partial-capture note under that run's Subtotal is the only
    # durable record of it in the comment thread, so its presence anywhere in the prior
    # history carries the caveat forward onto later headers.
    lower_bound_suffix = (
        " (lower bound)" if is_partial or "Partial cost capture" in prior_comment_body else ""
    )

    savings_block = format_savings_block(budget, deliveries)

    # NOTE: `{prior['prior_runs']}` and `{savings_block}` are each on their OWN
    # source line here, matching entrypoint.sh's heredoc structure exactly —
    # this is load-bearing for blank-line placement (verified against real
    # captured output; see the golden tests above and "Deviations" item 3).
    body = f"""{COST_MARKER}
<!-- cumulative: cost={cum_cost} in={cum_in} out={cum_out} -->
## Dark Factory — Cost Report

**{run_count} run(s) — Total: {cum_display} ({format_tokens_cumulative(cum_in)} in / {format_tokens_cumulative(cum_out)} out){lower_bound_suffix}**

{prior['prior_runs']}
### Run: {timestamp} ({intent}, {status})
{savings_block}
| Step | Model | In tokens | Out tokens | Cost | Duration |
|------|-------|-----------|------------|------|----------|
{run_rows}
| **Subtotal** | | **{format_tokens_cumulative(total_in)}** | **{format_tokens_cumulative(total_out)}** | **{total_cost_display}** | |{partial_segment}
{economics_segment}{push_segment}

---
*Updated by {product_name} Dark Factory*"""
    return body
