"""Pure formatting/bookkeeping for the Dark Factory cost-report comment (#182).

Extracted from entrypoint.sh's post_cost_report(). No gh, no docker, no archon, no
network calls in this module — see cli.py's cost-report subcommands for the IO seam.
"""
import math


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
