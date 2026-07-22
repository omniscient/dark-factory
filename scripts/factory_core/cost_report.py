"""Pure formatting/bookkeeping for the Dark Factory cost-report comment (#182).

Extracted from entrypoint.sh's post_cost_report(). No gh, no docker, no archon, no
network calls in this module — see cli.py's cost-report subcommands for the IO seam.
"""
import math
import re


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
    cpm_fmt = "n/a" if cpm is None else f"{_round_half_away_from_zero(cpm)}"
    score = _jq_alt(outcome.get("score"), "n/a")
    return f"**Factory CPM:** {cpm_fmt} | **Outcome:** {state} (score {score})"


def format_savings_block(budget: "dict | None") -> str:
    """Mirrors the context-budget.json (schema v2) block at entrypoint.sh:501-548."""
    if not budget:
        return ""
    schema_version = budget.get("schema_version", 1)
    if not isinstance(schema_version, int) or schema_version < 2:
        return ""

    lines = []

    savings_tokens = budget.get("savings_tokens", 0) or 0
    if savings_tokens > 0:
        savings_pct = budget.get("savings_pct", 0)
        lines.append(
            f"**Context savings: {format_tokens_cumulative(savings_tokens)} "
            f"tokens ({savings_pct}%)**"
        )

    fallback_events = budget.get("fallback_events") or []
    if fallback_events:
        parts = [f"{ev['section']}: {ev['reason']}" for ev in fallback_events]
        lines.append("**Fallbacks:** " + ", ".join(parts))

    over_budget = budget.get("over_budget")
    would_trim = budget.get("would_trim")
    caps_str = ", ".join(
        f"{k}→{v}" for k, v in (budget.get("derived_caps") or {}).items()
    )
    scenario = budget.get("scenario", "unknown")
    scenario_budget = budget.get("scenario_budget", 0)
    if over_budget is True:
        reserved = budget.get("reserved_tokens", 0)
        lines.append(
            f"**⚠️ Over budget ({scenario}): "
            f"{format_tokens_cumulative(reserved)} reserved / "
            f"{format_tokens_cumulative(scenario_budget)} budget — "
            f"trimmed: {caps_str}**"
        )
    elif would_trim is True:
        estimated = budget.get("estimated_input_tokens")
        if estimated:
            label, value = "est", estimated
        else:
            label, value = "rsv", budget.get("reserved_tokens", 0)
        lines.append(
            f"**Budget trim ({scenario}): {label} "
            f"{format_tokens_cumulative(value)} / "
            f"{format_tokens_cumulative(scenario_budget)} budget — "
            f"capped: {caps_str}**"
        )

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
    r"<!-- cumulative: cost=([0-9.]+) in=(\d+) out=(\d+) -->"
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

    run_count = prior_comment_body.count("### Run:")

    return {
        "prior_runs": prior_runs,
        "prev_cost": prev_cost,
        "prev_in": prev_in,
        "prev_out": prev_out,
        "run_count": run_count,
    }
