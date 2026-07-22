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


# ---------------------------------------------------------------------------
# check_renderable / format_missing_diagnostic (entrypoint.sh:435-458, Req 1a)
# ---------------------------------------------------------------------------

def test_check_renderable_none_when_nodes_present():
    assert cr.check_renderable({"nodes": [{"node_id": "refine"}]}) is None


def test_check_renderable_diagnostic_when_nodes_empty():
    run_record = {
        "nodes": [],
        "archon_cost_capture": {"ok": False, "exit_code": 127, "stderr_excerpt": "boom"},
    }
    diag = cr.check_renderable(run_record)
    assert diag == {
        "nodes_count": 0,
        "capture_ok": False,
        "capture_exit_code": 127,
        "capture_stderr": "boom",
    }


def test_check_renderable_diagnostic_when_nodes_absent():
    diag = cr.check_renderable({})
    assert diag["nodes_count"] == 0
    assert diag["capture_ok"] == "unknown"
    assert diag["capture_exit_code"] == "unknown"
    assert diag["capture_stderr"] == ""


def test_check_renderable_capture_ok_false_survives_jq_alt_bug():
    # Regression guard: jq's `.ok // "unknown"` would silently turn a real
    # `ok: false` into "unknown" (both are falsy in jq) — entrypoint.sh:437-440
    # works around this with `if has("ok") then .ok else "unknown" end`.
    run_record = {"nodes": [], "archon_cost_capture": {"ok": False}}
    diag = cr.check_renderable(run_record)
    assert diag["capture_ok"] is False   # NOT "unknown"


def test_format_missing_diagnostic_matches_bash_string_exactly():
    diag = {
        "nodes_count": 0,
        "capture_ok": False,
        "capture_exit_code": 127,
        "capture_stderr": "archon: command not found",
    }
    msg = cr.format_missing_diagnostic(diag, run_id="test-run-1", issue=300)
    assert msg == (
        "ERROR: cost report has zero node rows for run test-run-1 "
        "(issue #300); nodes=0, archon_cost_capture.ok=false, "
        "archon_cost_exit_code=127, stderr=archon: command not found"
    )


def test_format_missing_diagnostic_run_id_defaults_to_unknown():
    diag = {"nodes_count": 0, "capture_ok": "unknown", "capture_exit_code": "unknown",
             "capture_stderr": ""}
    msg = cr.format_missing_diagnostic(diag, run_id="", issue=300)
    assert "for run unknown " in msg


# ---------------------------------------------------------------------------
# parse_prior_cumulative (entrypoint.sh:466-478)
# ---------------------------------------------------------------------------

def test_parse_prior_cumulative_first_run_defaults():
    parsed = cr.parse_prior_cumulative("")
    assert parsed == {"prior_runs": "", "prev_cost": "0", "prev_in": 0, "prev_out": 0,
                       "run_count": 0}


def test_parse_prior_cumulative_extracts_marker_and_prior_runs():
    body = (
        "<!-- dark-factory-cost-report -->\n"
        "<!-- cumulative: cost=1.5 in=100 out=200 -->\n"
        "## Dark Factory — Cost Report\n\n"
        "**1 run(s) — Total: $1.5 (100 in / 200 out)**\n\n\n"
        "### Run: 2026-07-21 10:00 UTC (fix, completed)\n\n"
        "| Step | Model | In tokens | Out tokens | Cost | Duration |\n"
        "|------|-------|-----------|------------|------|----------|\n"
        "| refine |  | 100 | 200 | $1.5 | 5s |\n"
        "| **Subtotal** | | **100** | **200** | **$1.5** | |\n\n"
        "---\n"
        "*Updated by Dark Factory Dark Factory*"
    )
    parsed = cr.parse_prior_cumulative(body)
    # prev_cost stays a STRING (not float) — it feeds _bc_add (Task 4), which
    # needs the exact decimal text, matching bc's arbitrary-precision behavior.
    assert parsed["prev_cost"] == "1.5"
    assert parsed["prev_in"] == 100
    assert parsed["prev_out"] == 200
    assert parsed["run_count"] == 1
    assert parsed["prior_runs"] == (
        "### Run: 2026-07-21 10:00 UTC (fix, completed)\n\n"
        "| Step | Model | In tokens | Out tokens | Cost | Duration |\n"
        "|------|-------|-----------|------------|------|----------|\n"
        "| refine |  | 100 | 200 | $1.5 | 5s |\n"
        "| **Subtotal** | | **100** | **200** | **$1.5** | |"
    )
    # No trailing newline — mirrors bash's $(...) stripping ALL trailing
    # newlines from the sed/head-n--1 pipeline (verified against real bash;
    # see the "Deviations from the spec" note above item 3's sibling finding).
    assert not parsed["prior_runs"].endswith("\n")
