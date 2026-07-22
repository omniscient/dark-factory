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


# ---------------------------------------------------------------------------
# render() — execution-captured goldens (captured from pre-refactor entrypoint.sh
# via the harness above, per the #182 operator's 2026-07-22 guidance — NOT
# hand-derived). Verified byte-for-byte against real bash output.
# ---------------------------------------------------------------------------

GOLDEN_A_MULTI_NODE_FIRST_RUN = (
    "<!-- dark-factory-cost-report -->\n"
    "<!-- cumulative: cost=.0207 in=20 out=270 -->\n"
    "## Dark Factory — Cost Report\n\n"
    "**1 run(s) — Total: $.0207 (20 in / 270 out)**\n\n\n"
    "### Run: 2026-07-22 13:02 UTC (fix, completed)\n\n"
    "| Step | Model | In tokens | Out tokens | Cost | Duration |\n"
    "|------|-------|-----------|------------|------|----------|\n"
    "| parse-intent |  | 20 | 270 | $0.0207 | 7.8s |\n"
    "| **Subtotal** | | **20** | **270** | **$0.0207** | |\n\n"
    "**Factory CPM:** 17 | **Outcome:** produced_ungated (score 1.0)\n\n"
    "---\n"
    "*Updated by Dark Factory Dark Factory*"
)

RUN_RECORD_A = {
    "status": "completed",
    "totals": {"cost_usd": 0.0207, "gen_ai.usage.input_tokens": 20, "gen_ai.usage.output_tokens": 270},
    "nodes": [
        {"node_id": "parse-intent", "model": "", "gen_ai.usage.input_tokens": 20,
         "gen_ai.usage.output_tokens": 270, "cost_usd": 0.0207, "duration_ms": 7800},
    ],
    "harness_economics": {"factory_cpm": 17.4, "outcome": {"state": "produced_ungated", "score": 1.0}},
    "archon_cost_capture": {"ok": True},
}


def test_render_matches_golden_a_multi_node_first_run():
    body = cr.render(RUN_RECORD_A, prior_comment_body="", timestamp="2026-07-22 13:02 UTC",
                      intent="fix")
    assert body == GOLDEN_A_MULTI_NODE_FIRST_RUN


GOLDEN_B_PRIOR_COMMENT_CUMULATIVE = (
    "<!-- dark-factory-cost-report -->\n"
    "<!-- cumulative: cost=1.2707 in=520 out=12270 -->\n"
    "## Dark Factory — Cost Report\n\n"
    "**2 run(s) — Total: $1.2707 (520 in / 12.2K out)**\n\n"
    "### Run: 2026-07-22 13:02 UTC (fix, completed)\n\n"
    "| Step | Model | In tokens | Out tokens | Cost | Duration |\n"
    "|------|-------|-----------|------------|------|----------|\n"
    "| parse-intent |  | 20 | 270 | $0.0207 | 7.8s |\n"
    "| **Subtotal** | | **20** | **270** | **$0.0207** | |\n\n"
    "**Factory CPM:** 17 | **Outcome:** produced_ungated (score 1.0)\n"
    "### Run: 2026-07-22 13:03 UTC (fix, completed)\n\n"
    "| Step | Model | In tokens | Out tokens | Cost | Duration |\n"
    "|------|-------|-----------|------------|------|----------|\n"
    "| plan | claude-opus-4-8 | 300 | 8K | $0.9 | 45s |\n"
    "| review | claude-sonnet-5 | 200 | 4K | $0.35 | 12s |\n"
    "| **Subtotal** | | **500** | **12.0K** | **$1.25** | |\n\n"
    "**Factory CPM:** 23 | **Outcome:** produced_ungated (score 0.95)\n\n"
    "---\n"
    "*Updated by Dark Factory Dark Factory*"
)

RUN_RECORD_B = {
    "status": "completed",
    "totals": {"cost_usd": 1.25, "gen_ai.usage.input_tokens": 500, "gen_ai.usage.output_tokens": 12000},
    "nodes": [
        {"node_id": "plan", "model": "claude-opus-4-8", "gen_ai.usage.input_tokens": 300,
         "gen_ai.usage.output_tokens": 8000, "cost_usd": 0.9, "duration_ms": 45000},
        {"node_id": "review", "model": "claude-sonnet-5", "gen_ai.usage.input_tokens": 200,
         "gen_ai.usage.output_tokens": 4000, "cost_usd": 0.35, "duration_ms": 12000},
    ],
    "harness_economics": {"factory_cpm": 22.9, "outcome": {"state": "produced_ungated", "score": 0.95}},
    "archon_cost_capture": {"ok": True},
}


def test_render_matches_golden_b_prior_comment_cumulative():
    body = cr.render(RUN_RECORD_B, prior_comment_body=GOLDEN_A_MULTI_NODE_FIRST_RUN,
                      timestamp="2026-07-22 13:03 UTC", intent="fix")
    assert body == GOLDEN_B_PRIOR_COMMENT_CUMULATIVE


GOLDEN_C_ECONOMICS_ABSENT = (
    "<!-- dark-factory-cost-report -->\n"
    "<!-- cumulative: cost=.5 in=100 out=2000 -->\n"
    "## Dark Factory — Cost Report\n\n"
    "**1 run(s) — Total: $.5 (100 in / 2.0K out)**\n\n\n"
    "### Run: 2026-07-22 13:04 UTC (fix, completed)\n\n"
    "| Step | Model | In tokens | Out tokens | Cost | Duration |\n"
    "|------|-------|-----------|------------|------|----------|\n"
    "| implement | claude-sonnet-5 | 100 | 2K | $0.5 | 30s |\n"
    "| **Subtotal** | | **100** | **2.0K** | **$0.5** | |\n\n\n"
    "---\n"
    "*Updated by Dark Factory Dark Factory*"
)

RUN_RECORD_C = {
    "status": "completed",
    "totals": {"cost_usd": 0.5, "gen_ai.usage.input_tokens": 100, "gen_ai.usage.output_tokens": 2000},
    "nodes": [
        {"node_id": "implement", "model": "claude-sonnet-5", "gen_ai.usage.input_tokens": 100,
         "gen_ai.usage.output_tokens": 2000, "cost_usd": 0.5, "duration_ms": 30000},
    ],
    "archon_cost_capture": {"ok": True},
    # NOTE: no "harness_economics" key at all — the absent-tolerant case.
}


def test_render_matches_golden_c_economics_absent():
    body = cr.render(RUN_RECORD_C, prior_comment_body="", timestamp="2026-07-22 13:04 UTC",
                      intent="fix")
    assert body == GOLDEN_C_ECONOMICS_ABSENT


GOLDEN_D_OVER_BUDGET = (
    "<!-- dark-factory-cost-report -->\n"
    "<!-- cumulative: cost=2.0 in=5000 out=9000 -->\n"
    "## Dark Factory — Cost Report\n\n"
    "**1 run(s) — Total: $2.0 (5.0K in / 9.0K out)**\n\n\n"
    "### Run: 2026-07-22 13:04 UTC (fix, completed)\n\n"
    "**⚠️ Over budget (implement): 12.0K reserved / 8.0K budget — trimmed: arch→1500, memory→750**\n"
    "| Step | Model | In tokens | Out tokens | Cost | Duration |\n"
    "|------|-------|-----------|------------|------|----------|\n"
    "| implement | claude-sonnet-5 | 5K | 9K | $2 | 1m 0s |\n"
    "| **Subtotal** | | **5.0K** | **9.0K** | **$2.0** | |\n\n\n"
    "---\n"
    "*Updated by Dark Factory Dark Factory*"
)

RUN_RECORD_D = {
    "status": "completed",
    "totals": {"cost_usd": 2.0, "gen_ai.usage.input_tokens": 5000, "gen_ai.usage.output_tokens": 9000},
    "nodes": [
        {"node_id": "implement", "model": "claude-sonnet-5", "gen_ai.usage.input_tokens": 5000,
         "gen_ai.usage.output_tokens": 9000, "cost_usd": 2.0, "duration_ms": 60000},
    ],
    "archon_cost_capture": {"ok": True},
}

BUDGET_D = {
    "schema_version": 2,
    "over_budget": True,
    "scenario": "implement",
    "reserved_tokens": 12000,
    "scenario_budget": 8000,
    "derived_caps": {"arch": 1500, "memory": 750},
}


def test_render_matches_golden_d_over_budget():
    body = cr.render(RUN_RECORD_D, prior_comment_body="", timestamp="2026-07-22 13:04 UTC",
                      intent="fix", budget=BUDGET_D)
    assert body == GOLDEN_D_OVER_BUDGET


GOLDEN_E_WOULD_TRIM = (
    "<!-- dark-factory-cost-report -->\n"
    "<!-- cumulative: cost=.3 in=800 out=1500 -->\n"
    "## Dark Factory — Cost Report\n\n"
    "**1 run(s) — Total: $.3 (800 in / 1.5K out)**\n\n\n"
    "### Run: 2026-07-22 13:05 UTC (fix, completed)\n\n"
    "**Context savings: 3.0K tokens (15.5%)**\n"
    "**Fallbacks:** architecture_md: safety_keyword:performance\n"
    "**Budget trim (conformance): est 10.0K / 8.0K budget — capped: arch→1500, memory→750**\n"
    "| Step | Model | In tokens | Out tokens | Cost | Duration |\n"
    "|------|-------|-----------|------------|------|----------|\n"
    "| conformance | claude-opus-4-8 | 800 | 1.5K | $0.3 | 9s |\n"
    "| **Subtotal** | | **800** | **1.5K** | **$0.3** | |\n\n\n"
    "---\n"
    "*Updated by Dark Factory Dark Factory*"
)

RUN_RECORD_E = {
    "status": "completed",
    "totals": {"cost_usd": 0.3, "gen_ai.usage.input_tokens": 800, "gen_ai.usage.output_tokens": 1500},
    "nodes": [
        {"node_id": "conformance", "model": "claude-opus-4-8", "gen_ai.usage.input_tokens": 800,
         "gen_ai.usage.output_tokens": 1500, "cost_usd": 0.3, "duration_ms": 9000},
    ],
    "archon_cost_capture": {"ok": True},
}

BUDGET_E = {
    "schema_version": 2,
    "scenario": "conformance",
    "over_budget": False,
    "would_trim": True,
    "estimated_input_tokens": 10000,
    "reserved_tokens": 9000,
    "scenario_budget": 8000,
    "derived_caps": {"arch": 1500, "memory": 750},
    "savings_tokens": 3000,
    "savings_pct": 15.5,
    "fallback_events": [{"section": "architecture_md", "reason": "safety_keyword:performance"}],
}


def test_render_matches_golden_e_would_trim():
    body = cr.render(RUN_RECORD_E, prior_comment_body="", timestamp="2026-07-22 13:05 UTC",
                      intent="fix", budget=BUDGET_E)
    assert body == GOLDEN_E_WOULD_TRIM
