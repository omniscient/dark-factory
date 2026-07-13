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


# ── Verdict mining from durable GitHub signals ────────────────────────────────
_CONFORMANCE_BLOCKED_RE = re.compile(r"^## Spec Conformance — Blocked", re.MULTILINE)
_CODE_REVIEW_BLOCKED_RE = re.compile(r"^## Code Review — Blocked", re.MULTILINE)


def classify_conformance_verdict(comments: list[dict]) -> str:
    """Conformance is silent on PASS/MINOR (commands/dark-factory-conformance.md Phase 4); the
    only durable signal is the Phase 5 / plan-Phase-3.5 'Blocked' comment header."""
    for c in comments:
        if _CONFORMANCE_BLOCKED_RE.search(c.get("body") or ""):
            return "MATERIAL_BLOCKED"
    return "CONFORMS_OR_MINOR"


def classify_code_review_verdict(comments: list[dict]) -> str:
    """code-review posts an inline PR review on any finding but only comments on the issue when
    BLOCKED (commands/dark-factory-code-review.md Phase 6) — the issue-comment header is the cheap
    durable signal; PASS-with-advisory is not distinguished from PASS-clean here."""
    for c in comments:
        if _CODE_REVIEW_BLOCKED_RE.search(c.get("body") or ""):
            return "BLOCKED"
    return "PASS"


# ── Before/after-commit boundary bucketing (Tier 1 corroboration + Tier 2) ───
def merge_boundary_date(repo_root: str, sha: str) -> datetime:
    out = fsc._git(repo_root, "log", "-1", "--format=%cI", sha).strip()
    return datetime.fromisoformat(out)


def bucket_prs_by_boundary(prs: list[dict], boundary: datetime) -> dict[str, list[dict]]:
    """Split factory PRs into before/after the boundary commit's merge date, by PR mergedAt.
    Unmerged PRs (open/closed-without-merge) go to 'unmerged' — excluded from before/after
    deltas, same denominator convention as fetch_scorecard.build_scorecard's merged_in_window."""
    buckets: dict[str, list[dict]] = {"before": [], "after": [], "unmerged": []}
    for pr in prs:
        merged_at = fsc._dt(pr.get("mergedAt"))
        if merged_at is None:
            buckets["unmerged"].append(pr)
        elif merged_at < boundary:
            buckets["before"].append(pr)
        else:
            buckets["after"].append(pr)
    return buckets


# ── Tier 1 verdict-rate population mining (issue #48) ─────────────────────────
def _fetch_issue_comments(repo: str, issue_number: int) -> list[dict]:
    """Thin wrapper over gh so tests can monkeypatch without touching the network."""
    raw = fsc._gh("api", f"repos/{repo}/issues/{issue_number}/comments?per_page=100", paginate=True)
    return [{"body": c.get("body", "")} for c in raw]


def _bucket_verdict_stats(prs: list[dict], repo: str, classify, blocked_values: tuple[str, ...]) -> dict:
    n = len(prs)
    blocked = 0
    for pr in prs:
        issue = fsc.linked_issue_number(pr.get("headRefName", ""))
        if issue is None:
            continue
        comments = _fetch_issue_comments(repo, issue)
        if classify(comments) in blocked_values:
            blocked += 1
    return {"n": n, "blocked": blocked}


def mine_conformance_population(repo: str, prs: list[dict], boundary: datetime) -> dict:
    factory_prs = [pr for pr in prs if fsc.is_factory_pr(pr)]
    buckets = bucket_prs_by_boundary(factory_prs, boundary)
    return {
        "before": _bucket_verdict_stats(buckets["before"], repo, classify_conformance_verdict, ("MATERIAL_BLOCKED",)),
        "after": _bucket_verdict_stats(buckets["after"], repo, classify_conformance_verdict, ("MATERIAL_BLOCKED",)),
    }


def mine_code_review_population(repo: str, prs: list[dict], boundary: datetime) -> dict:
    factory_prs = [pr for pr in prs if fsc.is_factory_pr(pr)]
    buckets = bucket_prs_by_boundary(factory_prs, boundary)
    return {
        "before": _bucket_verdict_stats(buckets["before"], repo, classify_code_review_verdict, ("BLOCKED",)),
        "after": _bucket_verdict_stats(buckets["after"], repo, classify_code_review_verdict, ("BLOCKED",)),
    }


# ── Tier 2 population mining: label incidence (issue #48) ────────────────────
_TIER2_LABELS = {
    "factory_regression": fsc.REGRESSION_LABEL,
    "scope_spillover": "scope-spillover",
    "needs_discussion": "needs-discussion",
}


def _label_counts(prs: list[dict]) -> dict:
    counts = {"n": len(prs), "factory_regression": 0, "scope_spillover": 0, "needs_discussion": 0}
    for pr in prs:
        names = {lbl["name"] for lbl in pr.get("labels", [])}
        for key, label_name in _TIER2_LABELS.items():
            if label_name in names:
                counts[key] += 1
    return counts


def mine_label_incidence(prs: list[dict], boundary: datetime) -> dict:
    """Tier 2 qualitative proxy: factory-regression / scope-spillover / needs-discussion label
    incidence before vs. after a merge boundary. This is the only Tier-2 signal that is both
    durable (survives past the ephemeral per-run container) and cheaply mineable via gh — token/
    tool-call/runtime are not (see DIMENSION_APPLICABILITY's _TIER2_TOKEN_GAP)."""
    factory_prs = [pr for pr in prs if fsc.is_factory_pr(pr)]
    buckets = bucket_prs_by_boundary(factory_prs, boundary)
    return {
        "before": _label_counts(buckets["before"]),
        "after": _label_counts(buckets["after"]),
    }


# ── Cross-repo verdict-rate widening (issue #48, spec Requirement #3) ────────
def _mine_verdict_population(repo: str, boundary: datetime) -> dict:
    """Fetch + mine one repo's conformance/code-review verdict-rate population. Reassigns
    fsc.REPO/_OWNER_REPO/FACTORY_EMAIL for the duration of the fetch, exactly as
    fetch_scorecard.py's own --repo handling does, then restores them — callers must not see a
    changed fsc.REPO after this returns, win or lose."""
    prev_repo, prev_owner_repo, prev_email = fsc.REPO, fsc._OWNER_REPO, fsc.FACTORY_EMAIL
    try:
        fsc.REPO = fsc._OWNER_REPO = repo
        fsc.FACTORY_EMAIL = f"factory@{repo.split('/')[-1]}"
        prs = fsc.fetch_prs()
        return {
            "conformance": mine_conformance_population(repo, prs, boundary),
            "code_review": mine_code_review_population(repo, prs, boundary),
        }
    finally:
        fsc.REPO, fsc._OWNER_REPO, fsc.FACTORY_EMAIL = prev_repo, prev_owner_repo, prev_email


def mine_cross_repo_verdict_population(
    self_repo: str, self_prs: list[dict], cross_repo: str, boundary: datetime,
    precomputed_self_target: dict | None = None,
) -> dict:
    """self_target is required (always reachable — we run inside self_repo); cross_repo widens the
    verdict-rate sample for quantitative volume and degrades to 'unavailable' rather than raising
    if the second repo can't be reached (spec Assumptions: harness must not crash on an
    unreachable target population). precomputed_self_target lets a caller that already mined the
    self-target population (e.g. run()'s per-scenario loop) pass it in rather than mining it a
    second time here."""
    result: dict = {
        "self_target": precomputed_self_target or {
            "conformance": mine_conformance_population(self_repo, self_prs, boundary),
            "code_review": mine_code_review_population(self_repo, self_prs, boundary),
        }
    }
    try:
        result["cross_repo"] = _mine_verdict_population(cross_repo, boundary)
    except Exception as e:
        print(f"[skill-flow-eval] cross-repo population unavailable ({cross_repo}): {e}", file=sys.stderr)
        result["cross_repo"] = "unavailable"
    return result
