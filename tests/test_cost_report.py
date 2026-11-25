import json
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


# ---------------------------------------------------------------------------
# #608: a local-routed run records cost_usd null (hybrid contract §4.10) — the
# report must render it as unknown, never $0, in per-node rows, the Subtotal,
# the cumulative marker and header; cloud runs render exactly as before.
# ---------------------------------------------------------------------------

RUN_RECORD_LOCAL = {
    "status": "completed",
    "totals": {"cost_usd": None, "known_cost_usd": None, "cost_capture": "not_applicable",
               "gen_ai.usage.input_tokens": 20, "gen_ai.usage.output_tokens": 270},
    "nodes": [
        {"node_id": "parse-intent", "model": "", "gen_ai.usage.input_tokens": 20,
         "gen_ai.usage.output_tokens": 270, "cost_usd": None, "cost_capture": "cost_unknown",
         "duration_ms": 7800},
    ],
    "harness_economics": {"factory_cpm": None,
                          "outcome": {"state": "produced_ungated", "score": 1.0}},
    "archon_cost_capture": {"ok": True},
}


def test_local_node_row_renders_unknown_not_zero():
    row = cr.format_node_row(RUN_RECORD_LOCAL["nodes"][0])
    assert "n/a (local endpoint)" in row
    assert "$0" not in row
    # tokens (kept per §4.10) and duration still render
    assert " 20 | 270 |" in row and "7.8s" in row


def test_local_render_subtotal_header_and_marker_unknown_never_zero():
    body = cr.render(RUN_RECORD_LOCAL, prior_comment_body="",
                     timestamp="2026-11-25 12:00 UTC", intent="fix")
    subtotal_line = next(l for l in body.splitlines() if l.startswith("| **Subtotal**"))
    assert "n/a (local endpoint)" in subtotal_line
    header = next(l for l in body.splitlines() if "run(s) — Total:" in l)
    assert "n/a (local endpoint)" in header
    marker = next(l for l in body.splitlines() if l.startswith("<!-- cumulative:"))
    assert "cost=n/a" in marker
    # the CPM line renders its own n/a, and nowhere in the report is there a $0
    assert "**Factory CPM:** n/a" in body
    assert "$0" not in body
    # token figures are preserved, not lost with the cost
    assert "20 in / 270 out" in header


def test_cumulative_stays_unknown_after_a_local_run():
    """Once any run's cost is unknown the cumulative is unknown: a later cloud run
    cannot price the history, so it must never read as prior + 0."""
    prior = (
        "<!-- dark-factory-cost-report -->\n"
        "<!-- cumulative: cost=n/a in=20 out=270 -->\n"
        "## Dark Factory — Cost Report\n\n"
        "**1 run(s) — Total: n/a (local endpoint) (20 in / 270 out)**\n\n\n"
        "### Run: 2026-11-25 12:00 UTC (fix, completed)\n\n"
        "| Step | Model | In tokens | Out tokens | Cost | Duration |\n"
        "|------|-------|-----------|------------|------|----------|\n"
        "| parse-intent |  | 20 | 270 | n/a (local endpoint) | 7.8s |\n"
        "| **Subtotal** | | **20** | **270** | **n/a (local endpoint)** | |\n\n"
        "---\n"
        "*Updated by Dark Factory Dark Factory*"
    )
    cloud = {
        "status": "completed",
        "totals": {"cost_usd": 0.0207,
                   "gen_ai.usage.input_tokens": 20, "gen_ai.usage.output_tokens": 270},
        "nodes": [
            {"node_id": "parse-intent", "model": "", "gen_ai.usage.input_tokens": 20,
             "gen_ai.usage.output_tokens": 270, "cost_usd": 0.0207, "duration_ms": 7800},
        ],
        "harness_economics": {"factory_cpm": 17.4,
                              "outcome": {"state": "produced_ungated", "score": 1.0}},
        "archon_cost_capture": {"ok": True},
    }
    body = cr.render(cloud, prior_comment_body=prior,
                     timestamp="2026-11-25 13:00 UTC", intent="fix")
    header = next(l for l in body.splitlines() if "run(s) — Total:" in l)
    assert "n/a (local endpoint)" in header
    marker = next(l for l in body.splitlines() if l.startswith("<!-- cumulative:"))
    assert "cost=n/a" in marker
    # the cloud run's own Subtotal still renders its real cost
    subtotals = [l for l in body.splitlines() if l.startswith("| **Subtotal**")]
    assert "$0.0207" in subtotals[-1]


def test_parse_prior_cumulative_unknown_marker():
    parsed = cr.parse_prior_cumulative("<!-- cumulative: cost=n/a in=20 out=270 -->")
    assert parsed["prev_cost"] == "n/a"
    assert parsed["prev_in"] == 20
    assert parsed["prev_out"] == 270


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


# ---------------------------------------------------------------------------
# format_push_line / render() push outcome (#473, absent-tolerant)
# ---------------------------------------------------------------------------

def test_format_push_line_failed_renders_first_error_line():
    run_record = {"push_result": {
        "pushed": False,
        "error": "remote: rejected by test hook\nTo /tmp/bare\n ! [remote rejected] x -> x",
    }}
    assert cr.format_push_line(run_record) == "**Push:** FAILED — remote: rejected by test hook"


def test_format_push_line_absent_or_pushed_returns_empty_string():
    assert cr.format_push_line({}) == ""
    assert cr.format_push_line({"push_result": {"pushed": True, "error": ""}}) == ""
    assert cr.format_push_line({"push_result": {}}) == ""
    assert cr.format_push_line({"push_result": "not a dict"}) == ""


def test_format_push_line_empty_error_has_placeholder():
    assert cr.format_push_line({"push_result": {"pushed": False, "error": ""}}) == (
        "**Push:** FAILED — no error text captured"
    )


def test_format_push_line_caps_long_first_line():
    line = cr.format_push_line({"push_result": {"pushed": False, "error": "x" * 500}})
    assert line == "**Push:** FAILED — " + "x" * cr.PUSH_ERROR_MAX_CHARS + "…"


def test_render_push_failed_inserts_line_after_economics():
    record = dict(RUN_RECORD_A, push_result={"pushed": False, "error": "fatal: unable to access"})
    body = cr.render(record, prior_comment_body="", timestamp="2026-07-22 13:02 UTC",
                     intent="fix")
    assert body == GOLDEN_A_MULTI_NODE_FIRST_RUN.replace(
        "(score 1.0)\n\n---",
        "(score 1.0)\n\n**Push:** FAILED — fatal: unable to access\n\n---",
    )


def test_render_push_succeeded_matches_golden_unchanged():
    record = dict(RUN_RECORD_A, push_result={"pushed": True, "error": ""})
    body = cr.render(record, prior_comment_body="", timestamp="2026-07-22 13:02 UTC",
                     intent="fix")
    assert body == GOLDEN_A_MULTI_NODE_FIRST_RUN


def test_parse_prior_cumulative_counts_only_header_lines():
    """"### Run:" mentioned inside an error line must not inflate the count (#479)."""
    body = (
        "### Run: 2026-07-21 10:00 UTC (fix, completed)\n"
        "error first line mentioning ### Run: inline, from a failed step\n"
        "### Run: 2026-07-22 10:00 UTC (fix, completed)\n"
    )
    assert cr.parse_prior_cumulative(body)["run_count"] == 2


def test_format_savings_block_measured_only_not_in_headline():
    """#487: savings from undelivered features must not appear as 'Context savings'."""
    budget = {
        "schema_version": 2,
        "savings_tokens": 0,
        "savings_pct": 0.0,
        "measured_only_savings_tokens": 4200,
        "measured_only_sections": ["architecture_md"],
        "fallback_events": [],
    }
    block = cr.format_savings_block(budget)
    assert "Context savings" not in block
    assert "Measured-only (not delivered to the prompt): 4.2K tokens" in block
    assert "architecture_md" in block


def test_format_savings_block_realised_and_measured_only_separate():
    budget = {
        "schema_version": 2,
        "savings_tokens": 3000,
        "savings_pct": 15.5,
        "measured_only_savings_tokens": 1000,
        "measured_only_sections": ["architecture_md"],
        "fallback_events": [],
    }
    block = cr.format_savings_block(budget)
    assert "**Context savings: 3.0K tokens (15.5%)**" in block
    assert "Measured-only (not delivered to the prompt): 1.0K tokens" in block


# ---------------------------------------------------------------------------
# #511: savings measured from what the optimizers delivered, across scenarios
# ---------------------------------------------------------------------------

def _b(scenario, **kw):
    d = {"schema_version": 2, "scenario": scenario, "savings_tokens": 0, "savings_pct": 0.0,
         "baseline_input_tokens": 1000, "fallback_events": []}
    d.update(kw)
    return d


def test_headline_is_baseline_minus_delivered_from_deliveries():
    deliveries = [
        {"kind": "memory", "phase": "implement", "baseline_tokens": 3000, "delivered_tokens": 1500},
        {"kind": "diff", "baseline_tokens": 9000, "delivered_tokens": 6000},
        {"kind": "diff", "baseline_tokens": 9000, "delivered_tokens": 7000},
    ]
    # The pre-run budget probes claim 0 (memory dropped) -- the headline must ignore them.
    block = cr.format_savings_block([_b("implement"), _b("conformance"), _b("code-review")],
                                    deliveries)
    # saved = 1500 + 3000 + 2000 = 6500 of baseline 21000
    assert "**Context savings: 6.5K tokens (31.0%)**" in block


def test_deliveries_for_phases_outside_delivered_set_are_ignored():
    deliveries = [{"kind": "memory", "phase": "validate", "baseline_tokens": 900,
                   "delivered_tokens": 100}]
    assert "Context savings" not in cr.format_savings_block([_b("implement")], deliveries)


def test_delivered_sets_in_sync_with_context_budget():
    import context_budget as cb
    mem = {s for s, secs in cb._DELIVERED_SAVINGS.items() if "memory_context" in secs}
    diff = {s for s, secs in cb._DELIVERED_SAVINGS.items() if "diff" in secs}
    assert mem == set(cr.DELIVERED_MEMORY_PHASES)
    assert diff == {"conformance", "code-review", "docs-conformance"}


def test_architecture_stays_measured_only_across_scenarios():
    budgets = [
        _b("implement", measured_only_savings_tokens=2000, measured_only_sections=["architecture_md"]),
        _b("conformance", measured_only_savings_tokens=500, measured_only_sections=["architecture_md"]),
    ]
    block = cr.format_savings_block(budgets, [])
    assert "Context savings" not in block
    assert "Measured-only (not delivered to the prompt): 2.5K tokens (architecture_md)" in block


def test_list_without_deliveries_sums_budget_savings_legacy_path():
    block = cr.format_savings_block([_b("a", savings_tokens=1000), _b("b", savings_tokens=3000)])
    assert "Context savings: 4.0K tokens" in block


def test_trim_lines_rendered_per_scenario():
    budgets = [
        _b("implement", would_trim=True, estimated_input_tokens=10000, scenario_budget=8000,
           derived_caps={"memory": 750}),
        _b("conformance", over_budget=True, reserved_tokens=30000, scenario_budget=22000,
           derived_caps={"diff": 3000}),
    ]
    block = cr.format_savings_block(budgets, [])
    assert "Budget trim (implement)" in block
    assert "Over budget (conformance)" in block


def test_load_budgets_reads_all_scenarios_and_ignores_legacy_when_present(tmp_path):
    (tmp_path / "context-budget.implement.json").write_text(json.dumps(_b("implement")))
    (tmp_path / "context-budget.conformance.json").write_text(json.dumps(_b("conformance")))
    (tmp_path / "context-budget.json").write_text(json.dumps(_b("legacy")))
    assert [b["scenario"] for b in cr.load_budgets(tmp_path)] == ["conformance", "implement"]


def test_load_budgets_falls_back_to_legacy_single_file(tmp_path):
    (tmp_path / "context-budget.json").write_text(json.dumps(_b("legacy")))
    assert [b["scenario"] for b in cr.load_budgets(tmp_path)] == ["legacy"]
    assert cr.load_budgets(tmp_path / "missing") == []


def test_load_deliveries_absent_is_none_and_skips_bad_lines(tmp_path):
    assert cr.load_deliveries(tmp_path) is None
    (tmp_path / "delivered-savings.jsonl").write_text('{"kind":"diff"}\nnot json\n')
    assert cr.load_deliveries(tmp_path) == [{"kind": "diff"}]


def test_render_before_after_headline():
    record = {"nodes": [{"node_id": "implement", "model": "m"}], "totals": {}}
    budgets = [_b("implement"), _b("conformance")]
    before = cr.render(record, "", "2026-11-16 00:00 UTC", "new", budget=budgets)
    after = cr.render(record, "", "2026-11-16 00:00 UTC", "new", budget=budgets,
                      deliveries=[{"kind": "diff", "baseline_tokens": 8000,
                                   "delivered_tokens": 6000},
                                  {"kind": "memory", "phase": "implement",
                                   "baseline_tokens": 2000, "delivered_tokens": 1500}])
    assert "Context savings" not in before
    assert "**Context savings: 2.5K tokens (25.0%)**" in after


def test_cli_render_aggregates_artifacts_dir(tmp_path, capsys):
    """End to end: per-scenario budgets + delivered-savings.jsonl -> one headline."""
    from factory_core import cli
    from types import SimpleNamespace
    (tmp_path / "context-budget.implement.json").write_text(json.dumps(_b("implement")))
    (tmp_path / "context-budget.conformance.json").write_text(json.dumps(_b("conformance")))
    (tmp_path / "delivered-savings.jsonl").write_text(
        json.dumps({"kind": "memory", "phase": "implement", "baseline_tokens": 3000,
                    "delivered_tokens": 1500}) + "\n"
        + json.dumps({"kind": "diff", "baseline_tokens": 9000, "delivered_tokens": 6000}) + "\n")
    rr = tmp_path / "run-record.json"
    rr.write_text(json.dumps({"nodes": [{"node_id": "implement", "model": "m"}], "totals": {}}))
    cli._cost_report_render(SimpleNamespace(
        run_record_file=str(rr), prior_body_file="", timestamp="t", intent="new",
        product_name="X", budget_file="", artifacts_dir=str(tmp_path)))
    assert "**Context savings: 4.5K tokens (37.5%)**" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# cost-missing nodes and partial capture (#453)
# ---------------------------------------------------------------------------

def test_format_node_row_completed_node_unchanged():
    node = {"node_id": "parse-intent", "model": "", "gen_ai.usage.input_tokens": 20,
            "gen_ai.usage.output_tokens": 270, "cost_usd": 0.0207, "duration_ms": 7800,
            "cost_capture": "completed"}
    assert cr.format_node_row(node) == "| parse-intent |  | 20 | 270 | $0.0207 | 7.8s |"


def test_format_node_row_missing_node_never_renders_zero():
    node = {"node_id": "implement", "model": "", "gen_ai.usage.input_tokens": None,
            "gen_ai.usage.output_tokens": None, "cost_usd": None, "duration_ms": 1_555_000,
            "cost_capture": "missing", "reason": "no_usage_reported"}
    assert cr.format_node_row(node) == (
        "| implement |  | — | — | missing (no_usage_reported) | 25m 55s |"
    )


def test_format_node_row_unreported_node_without_duration():
    node = {"node_id": "validate", "model": "", "gen_ai.usage.input_tokens": None,
            "gen_ai.usage.output_tokens": None, "cost_usd": None, "duration_ms": None,
            "cost_capture": "missing", "reason": "not_in_cost_report"}
    assert cr.format_node_row(node) == (
        "| validate |  | — | — | missing (not_in_cost_report) | — |"
    )


def test_format_partial_capture_note_only_for_partial():
    assert cr.format_partial_capture_note({}) == ""
    assert cr.format_partial_capture_note({"cost_capture": "complete"}) == ""
    assert cr.format_partial_capture_note({"cost_capture": "unknown"}) == ""
    two = cr.format_partial_capture_note({
        "cost_capture": "partial", "known_cost_usd": 0.5, "known_input_tokens": 10,
        "known_output_tokens": 2000,
        "missing_nodes": [{"node_id": "implement", "reason": "no_usage_reported"},
                          {"node_id": "validate", "reason": "not_in_cost_report"}],
    })
    assert two == (
        "> ⚠️ Partial cost capture — 2 nodes unreported (implement: no_usage_reported, "
        "validate: not_in_cost_report). Known spend: $0.5 (10 in / 2.0K out); "
        "true total is higher."
    )


def test_render_complete_run_with_453_fields_matches_golden_a():
    # Byte-identical guard: the new per-node/totals fields change nothing on a
    # complete run.
    record = json.loads(json.dumps(RUN_RECORD_A))
    record["totals"].update({
        "cost_capture": "complete", "node_log": "parsed", "known_cost_usd": 0.0207,
        "known_input_tokens": 20, "known_output_tokens": 270, "missing_nodes": [],
    })
    for node in record["nodes"]:
        node["cost_capture"] = "completed"
    body = cr.render(record, prior_comment_body="", timestamp="2026-07-22 13:02 UTC",
                     intent="fix")
    assert body == GOLDEN_A_MULTI_NODE_FIRST_RUN


RUN_RECORD_PARTIAL_388 = {
    "status": "failed",
    "totals": {
        "gen_ai.usage.input_tokens": None, "gen_ai.usage.output_tokens": None,
        "cost_usd": None, "cost_capture": "partial", "node_log": "parsed",
        "known_cost_usd": 1.89, "known_input_tokens": 112, "known_output_tokens": 27400,
        "missing_nodes": [{"node_id": "implement", "reason": "no_usage_reported"}],
    },
    "nodes": [
        {"node_id": "parse-intent", "model": "", "gen_ai.usage.input_tokens": 20,
         "gen_ai.usage.output_tokens": 297, "cost_usd": 0.0097, "duration_ms": 5200,
         "cost_capture": "completed"},
        {"node_id": "implement", "model": "", "gen_ai.usage.input_tokens": None,
         "gen_ai.usage.output_tokens": None, "cost_usd": None, "duration_ms": 1_555_000,
         "cost_capture": "missing", "reason": "no_usage_reported"},
        {"node_id": "conformance", "model": "", "gen_ai.usage.input_tokens": 92,
         "gen_ai.usage.output_tokens": 27103, "cost_usd": 1.8803, "duration_ms": 300_000,
         "cost_capture": "completed"},
    ],
    "harness_economics": {"factory_cpm": None, "outcome": {"state": "failed", "score": 0.0}},
    "archon_cost_capture": {"ok": True},
}

GOLDEN_PARTIAL_388 = (
    "<!-- dark-factory-cost-report -->\n"
    "<!-- cumulative: cost=1.89 in=112 out=27400 -->\n"
    "## Dark Factory — Cost Report\n\n"
    "**1 run(s) — Total: $1.89 (112 in / 27.4K out) (lower bound)**\n\n\n"
    "### Run: 2026-09-26 17:28 UTC (fix, failed)\n\n"
    "| Step | Model | In tokens | Out tokens | Cost | Duration |\n"
    "|------|-------|-----------|------------|------|----------|\n"
    "| parse-intent |  | 20 | 297 | $0.0097 | 5.2s |\n"
    "| implement |  | — | — | missing (no_usage_reported) | 25m 55s |\n"
    "| conformance |  | 92 | 27.1K | $1.8803 | 5m 0s |\n"
    "| **Subtotal** | | **112** | **27.4K** | **$1.89** | |\n\n"
    "> ⚠️ Partial cost capture — 1 node unreported (implement: no_usage_reported). "
    "Known spend: $1.89 (112 in / 27.4K out); true total is higher.\n\n"
    "**Factory CPM:** n/a | **Outcome:** failed (score 0.0)\n\n"
    "---\n"
    "*Updated by Dark Factory Dark Factory*"
)


def test_render_partial_run_uses_known_totals_and_note():
    body = cr.render(RUN_RECORD_PARTIAL_388, prior_comment_body="",
                     timestamp="2026-09-26 17:28 UTC", intent="fix")
    assert body == GOLDEN_PARTIAL_388


def test_partial_run_cumulative_is_a_lower_bound_never_zero():
    parsed = cr.parse_prior_cumulative(GOLDEN_PARTIAL_388)
    assert (parsed["prev_cost"], parsed["prev_in"], parsed["prev_out"]) == ("1.89", 112, 27400)
    assert parsed["run_count"] == 1
    assert "missing (no_usage_reported)" in parsed["prior_runs"]
    # The next run's header total adds onto the partial run's known spend.
    nxt = cr.render(RUN_RECORD_A, prior_comment_body=GOLDEN_PARTIAL_388,
                    timestamp="2026-09-27 09:00 UTC", intent="fix")
    assert "<!-- cumulative: cost=1.9107 in=132 out=27670 -->" in nxt
    assert "**2 run(s) — Total: $1.9107" in nxt


def _header_line(body):
    return next(line for line in body.splitlines() if "run(s) — Total:" in line)


def test_partial_run_own_header_is_marked_lower_bound():
    # The partial run's own cumulative header already excludes the missing node's spend.
    body = cr.render(RUN_RECORD_PARTIAL_388, prior_comment_body="",
                     timestamp="2026-09-26 17:28 UTC", intent="fix")
    assert _header_line(body) == "**1 run(s) — Total: $1.89 (112 in / 27.4K out) (lower bound)**"


def test_run_after_a_partial_run_keeps_the_lower_bound_marker():
    nxt = cr.render(RUN_RECORD_A, prior_comment_body=GOLDEN_PARTIAL_388,
                    timestamp="2026-09-27 09:00 UTC", intent="fix")
    assert _header_line(nxt).endswith(" out) (lower bound)**")
    third = cr.render(RUN_RECORD_A, prior_comment_body=nxt,
                      timestamp="2026-09-28 09:00 UTC", intent="fix")
    assert _header_line(third).startswith("**3 run(s)")
    assert _header_line(third).endswith(" out) (lower bound)**")


def test_complete_history_header_has_no_lower_bound_marker():
    first = cr.render(RUN_RECORD_A, prior_comment_body="",
                      timestamp="2026-09-27 09:00 UTC", intent="fix")
    second = cr.render(RUN_RECORD_A, prior_comment_body=first,
                       timestamp="2026-09-28 09:00 UTC", intent="fix")
    assert "lower bound" not in first
    assert "lower bound" not in second
