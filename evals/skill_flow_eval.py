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
# entrypoint.sh's post_cost_report() (added 2026-05-27) posts a durable, cumulative
# `<!-- dark-factory-cost-report -->` comment with a per-node token/cost/duration table to every
# completed run's GitHub issue — mine_cost_report_population() mines it for token_count/runtime.
# Coverage is real but incomplete: omniscient/dark-factory#64 means some runs never get a
# cost-report comment posted at all, so this must never be presented as 100%-reliable. The
# cost-report table has no tool-call/step-count column at all (Step | Model | In tokens |
# Out tokens | Cost | Duration) — that dimension genuinely has no durable mined artifact.
_TIER2_TOKEN_MINED = (
    "measured from mined cost-report comments (before/after merge boundary); "
    "coverage may be partial — see #64"
)
_TIER2_TOKEN_GAP = (
    "not measurable from mined data — cost-report comments record tokens/cost/duration per node, "
    "not tool-call/step counts; no durable per-run artifact captures that dimension"
)
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
        "token_count": _TIER2_TOKEN_MINED,
        "tool_call_count": _TIER2_TOKEN_GAP,
        "runtime": _TIER2_TOKEN_MINED,
        "spec_plan_quality": "measured qualitatively (self-target only)",
        "implementation_correctness": "N/A (refine does not implement)",
        "conformance_review_safety": "N/A (refine is not a review gate)",
        "missed_constraints": "measured qualitatively via label incidence (self-target only)",
        "skill_over_under_triggering": "N/A — no model-mediated skill routing",
    },
    "plan_narrative": {
        "token_count": _TIER2_TOKEN_MINED,
        "tool_call_count": _TIER2_TOKEN_GAP,
        "runtime": _TIER2_TOKEN_MINED,
        "spec_plan_quality": "measured qualitatively (self-target only)",
        "implementation_correctness": "N/A (plan does not implement)",
        "conformance_review_safety": "N/A (plan's own narrative is not a review gate)",
        "missed_constraints": "measured qualitatively via label incidence (self-target only)",
        "skill_over_under_triggering": "N/A — no model-mediated skill routing",
    },
    "continue": {
        "token_count": _TIER2_TOKEN_MINED,
        "tool_call_count": _TIER2_TOKEN_GAP,
        "runtime": _TIER2_TOKEN_MINED,
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


_COST_MARKER = "<!-- dark-factory-cost-report -->"
_COST_RUN_HEADER_RE = re.compile(
    r"^### Run: .*?\((?P<intent>[^,()]+),\s*(?P<status>[^)]+)\)\s*$", re.MULTILINE
)
_COST_ROW_RE = re.compile(
    r"^\|\s*(?P<step>[^|]+?)\s*\|\s*(?P<model>[^|]*?)\s*\|\s*(?P<intok>[^|]+?)\s*\|"
    r"\s*(?P<outtok>[^|]+?)\s*\|\s*(?P<cost>[^|]+?)\s*\|\s*(?P<dur>[^|]+?)\s*\|\s*$",
    re.MULTILINE,
)


def _parse_fmt_tokens(raw: str) -> float:
    """Reverse entrypoint.sh post_cost_report's jq fmt_tokens: '332' -> 332, '20.3K' -> 20300,
    '1.2M' -> 1200000."""
    s = raw.strip()
    if s.endswith("M"):
        return float(s[:-1]) * 1_000_000
    if s.endswith("K"):
        return float(s[:-1]) * 1_000
    return float(s) if s else 0.0


def _parse_fmt_dur(raw: str) -> float:
    """Reverse entrypoint.sh post_cost_report's jq fmt_dur: '953ms' -> 953, '6.6s' -> 6600,
    '6m 48s' -> 408000 (all in milliseconds)."""
    s = raw.strip()
    m = re.match(r"^(\d+)m\s+(\d+)s$", s)
    if m:
        return int(m.group(1)) * 60_000 + int(m.group(2)) * 1_000
    if s.endswith("ms"):
        return float(s[:-2])
    if s.endswith("s"):
        return float(s[:-1]) * 1_000
    return float(s) if s else 0.0


def _parse_fmt_cost(raw: str) -> float:
    """Reverse entrypoint.sh post_cost_report's jq fmt_cost: '$0.0166' -> 0.0166, '$0' -> 0.0."""
    s = raw.strip().lstrip("$")
    return float(s) if s else 0.0


def _cost_report_sections(body: str) -> list[tuple[str, str]]:
    """Split a cost-report comment body into (intent, section_text) pairs, one per
    '### Run: <date> (<intent>, <status>)' header — the comment is cumulative and may hold
    multiple Run sections (one per historical run of that issue)."""
    matches = list(_COST_RUN_HEADER_RE.finditer(body))
    sections = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections.append((m.group("intent").strip(), body[m.end():end]))
    return sections


def _cost_report_node_rows(body: str, node_id: str, intent_filter: str | None) -> list[dict]:
    """Parse every Step-table row for node_id from every Run section (optionally filtered to
    intent_filter) in one cost-report comment body. Returns one dict per matching row with raw
    (non-formatted) numeric values. The header row, the dashed separator row, and the bolded
    **Subtotal** rollup row are all skipped — they are not per-node data."""
    if _COST_MARKER not in body:
        return []
    rows: list[dict] = []
    for intent, section in _cost_report_sections(body):
        if intent_filter is not None and intent != intent_filter:
            continue
        for m in _COST_ROW_RE.finditer(section):
            step = m.group("step").strip()
            if not step or step == "Step" or step.startswith("**") or set(step) <= {"-"}:
                continue
            if step != node_id:
                continue
            rows.append({
                "input_tokens": _parse_fmt_tokens(m.group("intok")),
                "output_tokens": _parse_fmt_tokens(m.group("outtok")),
                "cost_usd": _parse_fmt_cost(m.group("cost")),
                "duration_ms": _parse_fmt_dur(m.group("dur")),
            })
    return rows


def _cost_report_pr_stats(prs: list[dict], repo: str, node_id: str, intent_filter: str | None) -> dict:
    """Mine one before/after bucket's cost-report population for node_id. Degrades gracefully:
    a PR with no linked issue, no cost-report comment, or no parseable node_id row is silently
    skipped (not crashed on) — coverage is reported honestly via n_with_data vs n_total rather
    than assumed complete (see omniscient/dark-factory#64: cost reports don't post for every run)."""
    n_total = len(prs)
    per_pr_avgs: list[dict] = []
    for pr in prs:
        issue = fsc.linked_issue_number(pr.get("headRefName", ""))
        if issue is None:
            continue
        comments = _fetch_issue_comments(repo, issue)
        cost_comment = next((c for c in comments if _COST_MARKER in (c.get("body") or "")), None)
        if cost_comment is None:
            continue
        rows = _cost_report_node_rows(cost_comment["body"], node_id, intent_filter)
        if not rows:
            continue
        per_pr_avgs.append({
            "input_tokens": sum(r["input_tokens"] for r in rows) / len(rows),
            "output_tokens": sum(r["output_tokens"] for r in rows) / len(rows),
            "cost_usd": sum(r["cost_usd"] for r in rows) / len(rows),
            "duration_ms": sum(r["duration_ms"] for r in rows) / len(rows),
        })

    n_with_data = len(per_pr_avgs)
    if n_with_data == 0:
        return {
            "n_total": n_total, "n_with_data": 0,
            "avg_input_tokens": None, "avg_output_tokens": None,
            "avg_duration_ms": None, "avg_cost_usd": None,
        }
    return {
        "n_total": n_total,
        "n_with_data": n_with_data,
        "avg_input_tokens": sum(p["input_tokens"] for p in per_pr_avgs) / n_with_data,
        "avg_output_tokens": sum(p["output_tokens"] for p in per_pr_avgs) / n_with_data,
        "avg_duration_ms": sum(p["duration_ms"] for p in per_pr_avgs) / n_with_data,
        "avg_cost_usd": sum(p["cost_usd"] for p in per_pr_avgs) / n_with_data,
    }


def mine_cost_report_population(
    repo: str, prs: list[dict], boundary: datetime, node_id: str, intent_filter: str | None = None,
) -> dict:
    """Tier 2 quantitative population mining (issue #48, spec Requirement #3): before/after
    merge-boundary token/cost/duration deltas for a workflow node_id (e.g. 'refine', 'plan',
    'implement'), mined from the durable `<!-- dark-factory-cost-report -->` comment
    entrypoint.sh's post_cost_report() posts to every completed run's GitHub issue. intent_filter
    restricts which Run sections count (e.g. 'continue', so the continue scenario's numbers come
    only from continue-intent runs' implement rows, not fix/feat runs sharing the same node_id).
    Coverage is real but incomplete (omniscient/dark-factory#64: cost reports don't post for every
    run) — callers must read n_with_data/n_total, not assume 100% coverage."""
    factory_prs = [pr for pr in prs if fsc.is_factory_pr(pr)]
    buckets = bucket_prs_by_boundary(factory_prs, boundary)
    return {
        "before": _cost_report_pr_stats(buckets["before"], repo, node_id, intent_filter),
        "after": _cost_report_pr_stats(buckets["after"], repo, node_id, intent_filter),
    }


# scenario -> (workflow node_id, intent_filter) for mine_cost_report_population's run() wiring.
# node_ids match workflows/archon-dark-factory.yaml's `- id: <name>` entries: refine's own node,
# plan's own node, and continue-intent runs of implement (continue does not get its own workflow
# node — commands/dark-factory-implement.md handles both 'fix' and 'continue' intents inside the
# same `implement` node, hence the intent_filter to isolate continue-intent runs' rows).
_TIER2_COST_REPORT_NODE: dict[str, tuple[str, str | None]] = {
    "refine": ("refine", None),
    "plan_narrative": ("plan", None),
    "continue": ("implement", "continue"),
}


def mine_label_incidence(prs: list[dict], boundary: datetime) -> dict:
    """Tier 2 qualitative proxy: factory-regression / scope-spillover / needs-discussion label
    incidence before vs. after a merge boundary. Durable and cheaply mineable via gh's PR labels
    directly (no per-issue comment fetch needed). Token count and runtime have their own mined
    quantitative signal via mine_cost_report_population (cost-report comments); tool-call/step
    count still has no durable mined artifact (see DIMENSION_APPLICABILITY's _TIER2_TOKEN_GAP)."""
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


# ── CLI ────────────────────────────────────────────────────────────────────────
def build_arg_parser():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="omniscient/dark-factory")
    parser.add_argument("--cross-repo", default="omniscient/markethawk")
    parser.add_argument("--no-cross-repo", action="store_true",
                         help="skip the cross-repo verdict-rate widening pass (Task 5)")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-dir", default="evals")
    parser.add_argument("--since", default="2026-05-01")
    parser.add_argument("--until", default=None)
    return parser


def run(args, write_json: bool = False) -> dict:
    """Mine the self-target population (required) for all six scenarios, date-windowed and
    boundary-bucketed, plus the cross-repo verdict-rate widening pass (Task 5) unless
    --no-cross-repo. Never raises on a missing/unreachable cross-repo population — that source is
    marked 'unavailable' instead (spec: harness must degrade gracefully)."""
    since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
    until = (
        datetime.fromisoformat(args.until).replace(tzinfo=timezone.utc)
        if args.until
        else datetime.now(timezone.utc)
    )

    # fetch_scorecard's fetch_prs()/fetch_issues() read module globals, not an argument —
    # reassign them exactly as fetch_scorecard.py's own __main__ --repo handling does.
    fsc.REPO = fsc._OWNER_REPO = args.repo
    fsc.FACTORY_EMAIL = f"factory@{args.repo.split('/')[-1]}"

    all_prs = fsc.fetch_prs()
    windowed_prs = [pr for pr in all_prs if fsc.in_window(pr.get("createdAt"), since, until)]

    report: dict = {"repo": args.repo, "since": since.isoformat(), "until": until.isoformat()}
    for row in SCENARIO_MAP:
        scenario = row["scenario"]
        if scenario == "plan_phase_3_5":
            continue  # folded into conformance's population per spec §6 — no separate key
        boundary = merge_boundary_date(args.repo_root, row["boundary_sha"])
        if scenario == "conformance":
            report[scenario] = mine_conformance_population(args.repo, windowed_prs, boundary)
        elif scenario == "code_review":
            report[scenario] = mine_code_review_population(args.repo, windowed_prs, boundary)
        else:  # tier 2: refine, plan_narrative, continue
            node_id, intent_filter = _TIER2_COST_REPORT_NODE[scenario]
            report[scenario] = {
                "labels": mine_label_incidence(windowed_prs, boundary),
                "quantitative": mine_cost_report_population(
                    args.repo, windowed_prs, boundary, node_id, intent_filter=intent_filter
                ),
            }

    if not args.no_cross_repo:
        conformance_boundary = merge_boundary_date(
            args.repo_root, next(r["boundary_sha"] for r in SCENARIO_MAP if r["scenario"] == "conformance")
        )
        # Reuse the conformance/code_review populations the loop above already mined for
        # self_repo — mining them a second time inside mine_cross_repo_verdict_population would
        # double the gh API calls for no new information (Task 5's precomputed_self_target param
        # exists exactly for this caller).
        report["cross_repo_widening"] = mine_cross_repo_verdict_population(
            args.repo, windowed_prs, args.cross_repo, conformance_boundary,
            precomputed_self_target={"conformance": report["conformance"], "code_review": report["code_review"]},
        )

    if write_json:
        import json

        results_dir = os.path.join(args.output_dir, "results")
        os.makedirs(results_dir, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out_path = os.path.join(results_dir, f"skill-flow-population-{args.repo.split('/')[-1]}-{date_str}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"Wrote {out_path}", file=sys.stderr)

    return report


def main() -> None:
    args = build_arg_parser().parse_args()
    run(args, write_json=True)


if __name__ == "__main__":
    main()
