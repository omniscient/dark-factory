"""Skill-modularized Dark Factory prompt flow evaluation — issue #48.

Mines historical PR/issue evidence via scripts/fetch_scorecard.py and builds a tier-honest
scorecard comparing current-flow vs. skill-modularized-flow. See
docs/superpowers/specs/2026-07-11-skill-modularized-prompt-flow-evaluation-design.md for the
methodology this module implements.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone

_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, _SCRIPTS_DIR)

import fetch_scorecard as fsc  # noqa: E402

# ── §1 Scenario -> tier -> mechanism map (spec §1 table) ─────────────────────
# boundary_issue: the omniscient/dark-factory issue number that introduced the modularization
#   (#43/#44/#45). boundary_pr_number: the actual merge-PR number for that issue (#220/#225/#231)
#   — kept distinct so a caller resolving "the PR that merged this boundary" doesn't fetch the
#   issue object by mistake.
SCENARIO_MAP = [
    {
        "scenario": "refine",
        "modularization": "Prose dedup (#43)",
        "tier": 2,
        "mechanism": "before/after #43 merge boundary",
        "data_source": "dark-factory self-target (qualitative, required)",
        "boundary_sha": "1d1b5d31af6bad93aa349f95fab56b128b966adf",
        "boundary_issue": 43,
        "boundary_pr_number": 220,
    },
    {
        "scenario": "plan_narrative",
        "modularization": "Prose dedup (#43)",
        "tier": 2,
        "mechanism": "before/after #43 merge boundary",
        "data_source": "dark-factory self-target (qualitative, required)",
        "boundary_sha": "1d1b5d31af6bad93aa349f95fab56b128b966adf",
        "boundary_issue": 43,
        "boundary_pr_number": 220,
    },
    {
        "scenario": "plan_phase_3_5",
        "modularization": "Clone-live RUBRIC toggle (#44)",
        "tier": 1,
        "mechanism": "toggle A/B on same issue/diff; verdict folded into conformance population",
        "data_source": "dark-factory self-target (causal, required); markethawk (verdict-rate volume, optional)",
        "boundary_sha": "f72738f8beb3e079335bc4daf9b1da85a198b2ef",
        "boundary_issue": 44,
        "boundary_pr_number": 225,
    },
    {
        "scenario": "continue",
        "modularization": "comment-digest.md injection (#45)",
        "tier": 2,
        "mechanism": "before/after #45 merge boundary",
        "data_source": "dark-factory self-target (qualitative, required)",
        "boundary_sha": "666da3db9cf035690bba0b629e550c9cc12069d9",
        "boundary_issue": 45,
        "boundary_pr_number": 231,
    },
    {
        "scenario": "conformance",
        "modularization": "Clone-live RUBRIC toggle (#44)",
        "tier": 1,
        "mechanism": "toggle A/B on same issue/diff",
        "data_source": "dark-factory self-target (causal, required); markethawk (verdict-rate volume, optional)",
        "boundary_sha": "f72738f8beb3e079335bc4daf9b1da85a198b2ef",
        "boundary_issue": 44,
        "boundary_pr_number": 225,
    },
    {
        "scenario": "code_review",
        "modularization": "Clone-live RUBRIC toggle (#44)",
        "tier": 1,
        "mechanism": "toggle A/B on same issue/diff",
        "data_source": "dark-factory self-target (causal, required); markethawk (verdict-rate volume, optional)",
        "boundary_sha": "f72738f8beb3e079335bc4daf9b1da85a198b2ef",
        "boundary_issue": 44,
        "boundary_pr_number": 225,
    },
]

# implement's new-intent path touches none of #43/#44/#45 — included here only as a documented
# exclusion, never iterated by the mining/report code below.
NOT_EVALUATED = ["implement_new"]

# ── §4 Dimension applicability (spec §4 table) ────────────────────────────────
# No durable per-run cost artifact survives past a run's ephemeral container, and no cost-report
# is posted to the issue/PR by any phase command today (see Architecture "Known gap" above).
_TIER2_TOKEN_GAP = "not measurable from mined data — no durable per-run cost artifact"
_TIER1_TOKEN_DIM = "measured directly from toggle-pair API usage only (not mineable from historical PR data)"

DIMENSION_APPLICABILITY: dict[str, dict[str, str]] = {
    "conformance": {
        "token_count": _TIER1_TOKEN_DIM,
        "tool_call_count": _TIER1_TOKEN_DIM,
        "runtime": _TIER1_TOKEN_DIM,
        "spec_plan_quality": "N/A (reviewer phase, not spec/plan-producing)",
        "implementation_correctness": "N/A (reviewer phase, not implementer)",
        "conformance_review_safety": "measured directly (toggle-pair verdict) + mined verdict-rate population (self-target primary, markethawk secondary for volume)",
        "missed_constraints": "measured directly (spot-check pair review)",
        "skill_over_under_triggering": "N/A — deterministic resolution",
    },
    "code_review": {
        "token_count": _TIER1_TOKEN_DIM,
        "tool_call_count": _TIER1_TOKEN_DIM,
        "runtime": _TIER1_TOKEN_DIM,
        "spec_plan_quality": "N/A (reviewer phase, not spec/plan-producing)",
        "implementation_correctness": "N/A (reviewer phase, not implementer)",
        "conformance_review_safety": "measured directly (toggle-pair verdict) + mined verdict-rate population (self-target primary, markethawk secondary for volume)",
        "missed_constraints": "measured directly (spot-check pair review)",
        "skill_over_under_triggering": "N/A — deterministic resolution",
    },
    "plan_phase_3_5": {
        "token_count": _TIER1_TOKEN_DIM,
        "tool_call_count": _TIER1_TOKEN_DIM,
        "runtime": _TIER1_TOKEN_DIM,
        "spec_plan_quality": "measured (plan Phase 3.5 verdict quality; folded into conformance population per spec §6)",
        "implementation_correctness": "N/A (reviewer phase, not implementer)",
        "conformance_review_safety": "measured directly (toggle-pair verdict) + mined verdict-rate population, folded into conformance",
        "missed_constraints": "measured directly (spot-check pair review)",
        "skill_over_under_triggering": "N/A — deterministic resolution",
    },
    "refine": {
        "token_count": _TIER2_TOKEN_GAP,
        "tool_call_count": _TIER2_TOKEN_GAP,
        "runtime": _TIER2_TOKEN_GAP,
        "spec_plan_quality": "measured qualitatively (self-target only)",
        "implementation_correctness": "N/A (refine does not implement)",
        "conformance_review_safety": "N/A (refine is not a review gate)",
        "missed_constraints": "measured qualitatively via label incidence (self-target only)",
        "skill_over_under_triggering": "N/A — no model-mediated skill routing",
    },
    "plan_narrative": {
        "token_count": _TIER2_TOKEN_GAP,
        "tool_call_count": _TIER2_TOKEN_GAP,
        "runtime": _TIER2_TOKEN_GAP,
        "spec_plan_quality": "measured qualitatively (self-target only)",
        "implementation_correctness": "N/A (plan does not implement)",
        "conformance_review_safety": "N/A (plan's own narrative is not a review gate)",
        "missed_constraints": "measured qualitatively via label incidence (self-target only)",
        "skill_over_under_triggering": "N/A — no model-mediated skill routing",
    },
    "continue": {
        "token_count": _TIER2_TOKEN_GAP,
        "tool_call_count": _TIER2_TOKEN_GAP,
        "runtime": _TIER2_TOKEN_GAP,
        "spec_plan_quality": "N/A (continue-intent implements against an existing plan; produces no new spec/plan)",
        "implementation_correctness": "measured for continue via post-fix test outcomes where available",
        "conformance_review_safety": "N/A (continue is not a review gate)",
        "missed_constraints": "measured qualitatively via label incidence (self-target only)",
        "skill_over_under_triggering": "N/A — no model-mediated skill routing",
    },
}
