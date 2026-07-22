import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core import cost_report as cr


# ---------------------------------------------------------------------------
# format_tokens_table (jq def fmt_tokens, entrypoint.sh:424-426 — per-node cells)
# ---------------------------------------------------------------------------

def test_format_tokens_table_sub_1000_is_raw_int():
    assert cr.format_tokens_table(0) == "0"
    assert cr.format_tokens_table(999) == "999"


def test_format_tokens_table_k_rounds_and_drops_trailing_zero():
    assert cr.format_tokens_table(1000) == "1K"        # whole → no ".0"
    assert cr.format_tokens_table(1500) == "1.5K"
    assert cr.format_tokens_table(999_500) == "999.5K"


def test_format_tokens_table_k_rounds_half_away_from_zero():
    # 59181 -> 59181/1000*10=591.81 -> round=592 -> /10=59.2 -> "59.2K"
    assert cr.format_tokens_table(59181) == "59.2K"


def test_format_tokens_table_m_threshold():
    assert cr.format_tokens_table(999_999) == "1000K"
    assert cr.format_tokens_table(1_000_000) == "1M"
    assert cr.format_tokens_table(2_000_000) == "2M"
    assert cr.format_tokens_table(1_500_000) == "1.5M"


# ---------------------------------------------------------------------------
# format_tokens_cumulative (shell/bc fmt_tokens, entrypoint.sh:490-499 — totals)
# ---------------------------------------------------------------------------

def test_format_tokens_cumulative_sub_1000_is_raw_int():
    assert cr.format_tokens_cumulative(0) == "0"
    assert cr.format_tokens_cumulative(999) == "999"


def test_format_tokens_cumulative_k_truncates_always_one_decimal():
    assert cr.format_tokens_cumulative(59451) == "59.4K"   # truncates, NOT 59.5K
    assert cr.format_tokens_cumulative(59999) == "59.9K"
    assert cr.format_tokens_cumulative(1000) == "1.0K"     # always shows ".0"


def test_format_tokens_cumulative_m_truncates():
    assert cr.format_tokens_cumulative(1_999_999) == "1.9M"
    assert cr.format_tokens_cumulative(1_000_000) == "1.0M"


def test_format_tokens_cumulative_diverges_from_table_on_same_input():
    # The exact "1K" vs "1.0K" divergence the spec calls out.
    n = 1000
    assert cr.format_tokens_table(n) == "1K"
    assert cr.format_tokens_cumulative(n) == "1.0K"


# ---------------------------------------------------------------------------
# format_duration (jq def fmt_dur, entrypoint.sh:427-429)
# ---------------------------------------------------------------------------

def test_format_duration_sub_1s_is_ms():
    assert cr.format_duration(24) == "24ms"
    assert cr.format_duration(999) == "999ms"


def test_format_duration_sub_60s_rounds_to_tenth_drops_trailing_zero():
    assert cr.format_duration(7800) == "7.8s"
    assert cr.format_duration(2000) == "2s"      # whole → no ".0"
    assert cr.format_duration(2500) == "2.5s"


def test_format_duration_60s_and_over_is_minutes_seconds():
    assert cr.format_duration(895_000) == "14m 55s"
    assert cr.format_duration(60_000) == "1m 0s"


# ---------------------------------------------------------------------------
# format_cost (jq def fmt_cost, entrypoint.sh:430 — per-node table cells only)
# ---------------------------------------------------------------------------

def test_format_cost_rounds_to_4dp_and_drops_trailing_zeros():
    assert cr.format_cost(0.020714) == "$0.0207"
    assert cr.format_cost(0.15) == "$0.15"
    assert cr.format_cost(1.0) == "$1"
    assert cr.format_cost(0) == "$0"


# ---------------------------------------------------------------------------
# format_economics_line (entrypoint.sh:409-418, absent-tolerant)
# ---------------------------------------------------------------------------

def test_format_economics_line_present():
    run_record = {
        "harness_economics": {
            "factory_cpm": 17.4,
            "outcome": {"state": "produced_ungated", "score": 1.0},
        }
    }
    assert cr.format_economics_line(run_record) == (
        "**Factory CPM:** 17 | **Outcome:** produced_ungated (score 1.0)"
    )


def test_format_economics_line_absent_returns_empty_string():
    assert cr.format_economics_line({}) == ""
    assert cr.format_economics_line({"harness_economics": {}}) == ""
    assert cr.format_economics_line(
        {"harness_economics": {"outcome": {}}}
    ) == ""


def test_format_economics_line_missing_cpm_falls_back_to_na():
    run_record = {
        "harness_economics": {"outcome": {"state": "failed", "score": 0.0}}
    }
    assert cr.format_economics_line(run_record) == (
        "**Factory CPM:** n/a | **Outcome:** failed (score 0.0)"
    )


# ---------------------------------------------------------------------------
# format_savings_block (entrypoint.sh:501-548, schema v2, best-effort)
# ---------------------------------------------------------------------------

def test_format_savings_block_none_or_v1_returns_empty():
    assert cr.format_savings_block(None) == ""
    assert cr.format_savings_block({}) == ""
    assert cr.format_savings_block({"schema_version": 1}) == ""


def test_format_savings_block_savings_line():
    budget = {
        "schema_version": 2,
        "savings_tokens": 6000,
        "savings_pct": 30.0,
        "fallback_events": [],
    }
    block = cr.format_savings_block(budget)
    assert "**Context savings: 6.0K tokens (30.0%)**" in block


def test_format_savings_block_fallbacks_line():
    budget = {
        "schema_version": 2,
        "savings_tokens": 0,
        "savings_pct": 0,
        "fallback_events": [
            {"section": "architecture_md", "reason": "safety_keyword:performance"},
        ],
    }
    block = cr.format_savings_block(budget)
    assert (
        "**Fallbacks:** architecture_md: safety_keyword:performance" in block
    )


def test_format_savings_block_over_budget_branch():
    budget = {
        "schema_version": 2,
        "over_budget": True,
        "scenario": "implement",
        "reserved_tokens": 12000,
        "scenario_budget": 8000,
        "derived_caps": {"arch": 1500, "memory": 750},
    }
    block = cr.format_savings_block(budget)
    assert "⚠️ Over budget (implement): 12.0K reserved / 8.0K budget" in block
    assert "arch→1500, memory→750" in block


def test_format_savings_block_would_trim_uses_estimated_input_tokens_not_reserved():
    # Regression (df, migrated from test_budget_line_trim.sh): would_trim must
    # render estimated_input_tokens (10000), NOT reserved_tokens (9000).
    budget = {
        "schema_version": 2,
        "scenario": "conformance",
        "over_budget": False,
        "would_trim": True,
        "estimated_input_tokens": 10000,
        "reserved_tokens": 9000,
        "scenario_budget": 8000,
        "derived_caps": {"arch": 1500, "memory": 750},
    }
    block = cr.format_savings_block(budget)
    assert "est 10.0K" in block
    assert "9.0K" not in block


def test_format_savings_block_would_trim_falls_back_to_reserved_when_estimated_absent():
    budget = {
        "schema_version": 2,
        "scenario": "conformance",
        "would_trim": True,
        "reserved_tokens": 9000,
        "scenario_budget": 8000,
        "derived_caps": {},
    }
    block = cr.format_savings_block(budget)
    assert "rsv 9.0K" in block
