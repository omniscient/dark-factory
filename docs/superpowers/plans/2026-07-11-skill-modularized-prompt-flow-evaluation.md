# Implementation Plan: Skill-Modularized Dark Factory Prompt Flow Evaluation

**Issue:** omniscient/dark-factory#48
**Spec:** `docs/superpowers/specs/2026-07-11-skill-modularized-prompt-flow-evaluation-design.md`
**Depends on:** omniscient/dark-factory#43, #44, #45 (all merged)

---

## Goal

Produce a tier-honest scorecard comparing current-flow vs. skill-modularized-flow across all
six named scenarios (refine, plan, implement, continue, conformance, review), plus a rollout
recommendation, by building: a scenario→tier→mechanism map, a mining harness that reuses
`fetch_scorecard.py` to pull and classify historical PR/issue evidence for both tiers, a small
budget-capped live toggle spot-check for the two scenarios with a real runtime-swappable
artifact (conformance, code-review — folding in plan's Phase 3.5 check), and a committed
scorecard document. This is a **spec + script + scorecard** deliverable (mirrors #161/#672),
not a standing service.

## Architecture

Two-tier hybrid, per the spec:

- **Tier 1 (conformance, code-review, plan's Phase 3.5 check):** controlled toggle A/B — a
  small live spot-check set (`.claude/skills/{conformance,code-review}/RUBRIC.md` present vs.
  forced-absent, same diff) plus mined historical verdicts bucketing PRs before/after the #44
  merge boundary (`f72738f8beb3e079335bc4daf9b1da85a198b2ef`, PR #225, merged
  2026-07-10T11:57:54-04:00) as corroboration.
- **Tier 2 (refine, plan's own narrative, implement/continue):** observational before/after
  the relevant merge boundary — #43 (`1d1b5d31af6bad93aa349f95fab56b128b966adf`, PR #220,
  merged 2026-07-10T07:23:41-04:00) for refine/plan-narrative, #45
  (`666da3db9cf035690bba0b629e550c9cc12069d9`, PR #231, merged 2026-07-10T17:54:05-04:00) for
  continue. Tier 2's qualitative proxy is mined **label incidence** (`factory-regression`,
  `scope-spillover`, `needs-discussion`) per before/after bucket — the only Tier-2 signal that
  is actually durable and mineable via `gh`. **Known gap, disclosed rather than silently
  overclaimed:** per-run token/tool-call/runtime figures are not retrievable for historical
  runs — `$ARTIFACTS_DIR/*.md` does not survive past its container, and no cost-report comment
  is posted to the issue/PR by the current pipeline (verified: `commands/dark-factory-*.md`
  post status/verdict comments, not cost breakdowns). Tier 2's token/tool-call/runtime
  dimension is therefore reported as `"not measurable from mined data — no durable per-run cost
  artifact"` rather than fabricated; this is flagged as a follow-up (a cost-report-comment
  miner) in the scorecard's Open Questions, not solved in this ticket.

Verdicts are mined from GitHub, not from ephemeral per-run artifacts. The mining functions key
off the same durable signals `fetch_scorecard.py` already uses — PR/issue comments and labels:

- Conformance MATERIAL/BLOCKED ⇒ an issue comment whose body starts with
  `## Spec Conformance — Blocked` (see `commands/dark-factory-conformance.md` Phase 5 and
  `commands/dark-factory-plan.md` Phase 3.5 step 8b) plus the `needs-discussion` label. Absence
  ⇒ CONFORMS or MINOR (conformance is silent on PASS).
- Code-review BLOCKED ⇒ an issue comment whose body starts with `## Code Review — Blocked`
  (see `commands/dark-factory-code-review.md` Phase 6) plus `needs-discussion`. Absence ⇒ PASS
  (code-review posts an inline PR review on any finding, but only comments on the issue when
  BLOCKED — the issue-comment signal is what's cheaply mineable without walking every PR's
  review objects).

Population source split by dimension (spec Requirement 3): qualitative/causal ⇒
`omniscient/dark-factory` self-target; quantitative volume ⇒ `omniscient/markethawk` +
`bench/suite.json`. The harness must not crash if a target population is unreachable (no
credentials/network for a second repo in some environments) — it records
`"population": "unavailable"` for that source and continues.

## Tech Stack

Python (`evals/skill_flow_eval.py`, `evals/skill_flow_scorecard.py`), reusing
`scripts/fetch_scorecard.py` as an importable module (same `sys.path.insert(scripts/)` pattern
as `tests/test_fetch_scorecard.py`) — **including reassigning its module globals
(`fsc.REPO`/`fsc._OWNER_REPO`/`fsc.FACTORY_EMAIL`) before calling `fsc.fetch_prs()`**, exactly
as `fetch_scorecard.py`'s own `__main__` block does at its `--repo` handling (lines ~380-396),
since `fetch_prs()`/`fetch_issues()` read those globals rather than taking a repo argument.
Bash for the live toggle spot-check runner (`evals/run_skill_toggle_spotcheck.sh`), mirroring
`bench/run_suite.sh` conventions (budget cap env var, results JSON under `evals/results/`).
`pytest` for all new Python; a bash smoke test for the spot-check runner's `--dry-run` mode.

---

## File Structure

| File | Change |
|---|---|
| `evals/skill_flow_scorecard.py` | New — scorecard schema, tier-gated rollout logic, markdown report renderer |
| `tests/test_skill_flow_scorecard.py` | New — TDD for schema/renderer/tier-gate |
| `evals/skill_flow_eval.py` | New — verdict classifiers, boundary bucketing, Tier 1 + Tier 2 population mining, CLI `main()` |
| `tests/test_skill_flow_eval.py` | New — TDD for classifiers/bucketing/mining (mocked `gh`/`git`) |
| `evals/skill_flow_spotchecks.json` | New — Tier 1 live spot-check manifest (4 real closed dark-factory issue/PR pairs) |
| `evals/run_skill_toggle_spotcheck.sh` | New — live RUBRIC-toggle runner for the spot-check manifest |
| `tests/test_run_skill_toggle_spotcheck.sh` | New — bash smoke test for `--dry-run` |
| `evals/reports/skill-modularization-scorecard-2026-07-11.md` | New — generated scorecard + rollout recommendation (committed deliverable) |
| `evals/results/skill-flow-population-dark-factory-2026-07-11.json` | New — generated dark-factory-population mining output (Tier 1 + Tier 2) |
| `evals/results/skill-flow-spotcheck-2026-07-11.json` | New — generated Tier 1 live spot-check results |

---

## Memory Context Applied

- **`.archon/memory/architecture.md` [AVOID] (issue #48, refine):** this plan treats
  conformance/code-review/plan's-Phase-3.5 as Tier 1 (controlled toggle) and
  refine/plan-narrative/continue as Tier 2 (confounded before/after) throughout — Task 1's
  `SCENARIO_MAP` encodes this split as data, and Task 7's rollout-gate test asserts a Tier 2
  scenario can never receive a `default-on` recommendation regardless of how positive its mined
  numbers look.
- **`.archon/memory/architecture.md` [AVOID] (issue #48, refine):** Task 4's Tier 1
  `mine_conformance_population()`/`mine_code_review_population()`, Task 5's Tier 2
  `mine_label_incidence()`, and Task 8's CLI all source qualitative/causal dimensions from
  `omniscient/dark-factory` and volume dimensions from `omniscient/markethawk` + `bench/suite.json`
  — never a single blended pool. Task 13 runs the live spot-checks as a small (4-pair)
  budget-capped set, not a full paid replay campaign.
- **`.archon/memory/codebase-patterns.md` [PATTERN] (issue #42):** this plan's spec and this
  plan file do not transfer automatically from this `refine/issue-48-*` branch to the
  `feat/issue-48-*` implementation branch — the implement-phase agent that picks this plan up
  must itself copy `docs/superpowers/specs/2026-07-11-skill-modularized-prompt-flow-evaluation-design.md`
  and this plan onto the feat branch and commit them before starting Task 1 (standard
  implement-phase behavior, not a step enumerated in this plan, per spec Requirement #8's
  "the methodology (this spec)" deliverable).
- **`.archon/memory/codebase-patterns.md` [PATTERN] (issue #149):** Task 4/5/8 load only the
  `dark-factory-ops.md`/`codebase-patterns.md`/`architecture.md` memory files relevant to this
  evals/scripts area — not the full memory set — matching the plan phase's own `$MEMORY_CONTEXT`
  selective-load convention.
- **`.archon/memory/codebase-patterns.md` [PATTERN] (issue #250):** any two-dot vs. three-dot
  diff/log comparisons this plan's scripts perform against `main` use the two-dot form
  (`git log main`, not `main...HEAD`) — not directly exercised here since the harness reads
  merge commits by SHA, not branch diffs, but noted so no task accidentally introduces a
  three-dot comparison later.

---

## Task 1: Scenario map + dimension applicability data

**Files:** `evals/skill_flow_eval.py` (new), `tests/test_skill_flow_eval.py` (new)

1. Write the failing test:

```python
# tests/test_skill_flow_eval.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import skill_flow_eval as sfe  # noqa: E402

SIX_SCENARIOS = {"refine", "plan_narrative", "plan_phase_3_5", "continue", "conformance", "code_review"}


def test_scenario_map_covers_all_six_named_scenarios():
    keys = {row["scenario"] for row in sfe.SCENARIO_MAP}
    assert SIX_SCENARIOS <= keys


def test_scenario_map_tier_assignment_matches_spec():
    by_scenario = {row["scenario"]: row for row in sfe.SCENARIO_MAP}
    assert by_scenario["conformance"]["tier"] == 1
    assert by_scenario["code_review"]["tier"] == 1
    assert by_scenario["plan_phase_3_5"]["tier"] == 1
    assert by_scenario["refine"]["tier"] == 2
    assert by_scenario["plan_narrative"]["tier"] == 2
    assert by_scenario["continue"]["tier"] == 2


def test_scenario_map_boundary_issue_and_pr_number_are_distinct():
    # boundary_issue is the omniscient/dark-factory issue number (#43/#44/#45); boundary_pr_number
    # is the actual merge-PR number (#220/#225/#231) — regression guard for the architect-review
    # finding that a field named boundary_pr previously held the issue number, not a PR number.
    by_scenario = {row["scenario"]: row for row in sfe.SCENARIO_MAP}
    assert by_scenario["conformance"]["boundary_issue"] == 44
    assert by_scenario["conformance"]["boundary_pr_number"] == 225
    assert by_scenario["refine"]["boundary_issue"] == 43
    assert by_scenario["refine"]["boundary_pr_number"] == 220
    assert by_scenario["continue"]["boundary_issue"] == 45
    assert by_scenario["continue"]["boundary_pr_number"] == 231


def test_implement_new_intent_excluded_not_evaluated():
    keys = {row["scenario"] for row in sfe.SCENARIO_MAP}
    assert "implement_new" not in keys


def test_dimension_applicability_marks_triggering_na_for_deterministic_scenarios():
    assert sfe.DIMENSION_APPLICABILITY["conformance"]["skill_over_under_triggering"] == "N/A — deterministic resolution"
    assert sfe.DIMENSION_APPLICABILITY["code_review"]["skill_over_under_triggering"] == "N/A — deterministic resolution"
    assert sfe.DIMENSION_APPLICABILITY["refine"]["skill_over_under_triggering"] == "N/A — no model-mediated skill routing"


def test_dimension_applicability_discloses_tier2_token_gap_rather_than_overclaiming():
    # Tier 2 token/tool-call/runtime cannot actually be mined (no durable per-run cost artifact) —
    # this must say so explicitly, not claim "measured from mined population" when nothing measures it.
    assert sfe.DIMENSION_APPLICABILITY["refine"]["token_count"] == "not measurable from mined data — no durable per-run cost artifact"
```

2. Verify it fails (module doesn't exist yet):

```bash
python -m pytest tests/test_skill_flow_eval.py -v
```

Expected: `ModuleNotFoundError: No module named 'skill_flow_eval'`

3. Implement `evals/skill_flow_eval.py` (module header + data tables only for this task):

```python
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
        "data_source": "dark-factory self-target (qualitative); token/runtime deltas where available",
        "boundary_sha": "1d1b5d31af6bad93aa349f95fab56b128b966adf",
        "boundary_issue": 43,
        "boundary_pr_number": 220,
    },
    {
        "scenario": "plan_narrative",
        "modularization": "Prose dedup (#43)",
        "tier": 2,
        "mechanism": "before/after #43 merge boundary",
        "data_source": "dark-factory self-target",
        "boundary_sha": "1d1b5d31af6bad93aa349f95fab56b128b966adf",
        "boundary_issue": 43,
        "boundary_pr_number": 220,
    },
    {
        "scenario": "plan_phase_3_5",
        "modularization": "Clone-live RUBRIC toggle (#44)",
        "tier": 1,
        "mechanism": "toggle A/B on same issue/diff",
        "data_source": "dark-factory self-target (causal), markethawk+bench (volume)",
        "boundary_sha": "f72738f8beb3e079335bc4daf9b1da85a198b2ef",
        "boundary_issue": 44,
        "boundary_pr_number": 225,
    },
    {
        "scenario": "continue",
        "modularization": "comment-digest.md injection (#45)",
        "tier": 2,
        "mechanism": "before/after #45 merge boundary",
        "data_source": "dark-factory self-target (qualitative), markethawk+bench (volume)",
        "boundary_sha": "666da3db9cf035690bba0b629e550c9cc12069d9",
        "boundary_issue": 45,
        "boundary_pr_number": 231,
    },
    {
        "scenario": "conformance",
        "modularization": "Clone-live RUBRIC toggle (#44)",
        "tier": 1,
        "mechanism": "toggle A/B on same issue/diff",
        "data_source": "dark-factory self-target (causal), markethawk+bench (volume)",
        "boundary_sha": "f72738f8beb3e079335bc4daf9b1da85a198b2ef",
        "boundary_issue": 44,
        "boundary_pr_number": 225,
    },
    {
        "scenario": "code_review",
        "modularization": "Clone-live RUBRIC toggle (#44)",
        "tier": 1,
        "mechanism": "toggle A/B on same issue/diff",
        "data_source": "dark-factory self-target (causal), markethawk+bench (volume)",
        "boundary_sha": "f72738f8beb3e079335bc4daf9b1da85a198b2ef",
        "boundary_issue": 44,
        "boundary_pr_number": 225,
    },
]

# implement's new-intent path touches none of #43/#44/#45 — included here only as a documented
# exclusion, never iterated by the mining/report code below.
NOT_EVALUATED = ["implement_new"]

# ── §4 Dimension applicability (spec §4 table) ────────────────────────────────
_TIER1_SCENARIOS = ("conformance", "code_review", "plan_phase_3_5")
_TIER2_SCENARIOS = ("refine", "plan_narrative", "continue")

# No durable per-run cost artifact survives past a run's ephemeral container, and no
# cost-report is posted to the issue/PR by any phase command today — see Architecture section
# "Known gap, disclosed rather than silently overclaimed."
_TIER2_TOKEN_GAP = "not measurable from mined data — no durable per-run cost artifact"

DIMENSION_APPLICABILITY: dict[str, dict[str, str]] = {}
for _s in _TIER1_SCENARIOS:
    DIMENSION_APPLICABILITY[_s] = {
        "token_count": "measured directly (toggle pairs) + mined population",
        "tool_call_count": "measured directly + mined",
        "runtime": "measured directly + mined",
        "spec_plan_quality": "N/A" if _s != "plan_phase_3_5" else "measured (plan-3.5 verdict quality)",
        "implementation_correctness": "N/A (reviewer phase, not implementer)",
        "conformance_review_safety": "measured directly (verdict deltas)",
        "missed_constraints": "measured directly (spot-check review)",
        "skill_over_under_triggering": "N/A — deterministic resolution",
    }
for _s in _TIER2_SCENARIOS:
    DIMENSION_APPLICABILITY[_s] = {
        "token_count": _TIER2_TOKEN_GAP,
        "tool_call_count": _TIER2_TOKEN_GAP,
        "runtime": _TIER2_TOKEN_GAP,
        "spec_plan_quality": "measured qualitatively (self-target only)",
        "implementation_correctness": "N/A" if _s != "continue" else "measured for continue via post-fix test outcomes where available",
        "conformance_review_safety": "N/A",
        "missed_constraints": "measured qualitatively via label incidence (self-target only)",
        "skill_over_under_triggering": "N/A — no model-mediated skill routing",
    }
```

4. Run tests, verify pass:

```bash
python -m pytest tests/test_skill_flow_eval.py -v
```

Expected: `6 passed`

5. Commit:

```bash
git add evals/skill_flow_eval.py tests/test_skill_flow_eval.py
git commit -m "test+feat(skill-flow-eval): scenario map + dimension applicability tables (#48)"
```

---

## Task 2: Verdict classifiers (conformance / code-review)

**Files:** `evals/skill_flow_eval.py`, `tests/test_skill_flow_eval.py`

1. Add failing tests:

```python
# tests/test_skill_flow_eval.py (append)
def test_classify_conformance_verdict_blocked():
    comments = [
        {"body": "some other comment"},
        {"body": "## Spec Conformance — Blocked\n\nmaterial divergence..."},
    ]
    assert sfe.classify_conformance_verdict(comments) == "MATERIAL_BLOCKED"


def test_classify_conformance_verdict_plan_variant_blocked():
    comments = [{"body": "## Spec Conformance — Blocked (Plan)\n\n..."}]
    assert sfe.classify_conformance_verdict(comments) == "MATERIAL_BLOCKED"


def test_classify_conformance_verdict_pass_when_silent():
    comments = [{"body": "🧠 Refinement Pipeline — Starting"}]
    assert sfe.classify_conformance_verdict(comments) == "CONFORMS_OR_MINOR"


def test_classify_code_review_verdict_blocked():
    comments = [{"body": "## Code Review — Blocked\n\nThe AI code reviewer found 2 blocking issue(s)"}]
    assert sfe.classify_code_review_verdict(comments) == "BLOCKED"


def test_classify_code_review_verdict_pass_when_silent():
    comments = [{"body": "unrelated"}]
    assert sfe.classify_code_review_verdict(comments) == "PASS"
```

2. Verify fail:

```bash
python -m pytest tests/test_skill_flow_eval.py -v -k classify
```

Expected: `AttributeError: module 'skill_flow_eval' has no attribute 'classify_conformance_verdict'`

3. Implement (append to `evals/skill_flow_eval.py`):

```python
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
    BLOCKED (commands/dark-factory-code-review.md Phase 6) — the issue-comment header is the
    cheap durable signal; PASS-with-advisory is not distinguished from PASS-clean here."""
    for c in comments:
        if _CODE_REVIEW_BLOCKED_RE.search(c.get("body") or ""):
            return "BLOCKED"
    return "PASS"
```

4. Run, verify pass:

```bash
python -m pytest tests/test_skill_flow_eval.py -v -k classify
```

Expected: `5 passed`

5. Commit:

```bash
git add evals/skill_flow_eval.py tests/test_skill_flow_eval.py
git commit -m "test+feat(skill-flow-eval): verdict classifiers for conformance/code-review (#48)"
```

---

## Task 3: Boundary bucketing (shared by Tier 1 and Tier 2)

**Files:** `evals/skill_flow_eval.py`, `tests/test_skill_flow_eval.py`

1. Add failing tests:

```python
# tests/test_skill_flow_eval.py (append)
from datetime import datetime, timezone


def test_bucket_prs_by_boundary_splits_before_after():
    boundary = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
    prs = [
        {"number": 1, "mergedAt": "2026-07-10T10:00:00Z"},
        {"number": 2, "mergedAt": "2026-07-10T14:00:00Z"},
        {"number": 3, "mergedAt": None},
    ]
    buckets = sfe.bucket_prs_by_boundary(prs, boundary)
    assert [p["number"] for p in buckets["before"]] == [1]
    assert [p["number"] for p in buckets["after"]] == [2]
    assert [p["number"] for p in buckets["unmerged"]] == [3]


def test_merge_boundary_date_reads_commit_date(monkeypatch, tmp_path):
    def fake_git(repo_root, *args):
        assert args[:2] == ("log", "-1")
        return "2026-07-10T11:57:54-04:00\n"

    monkeypatch.setattr(sfe.fsc, "_git", fake_git)
    dt = sfe.merge_boundary_date(str(tmp_path), "f72738f8beb3e079335bc4daf9b1da85a198b2ef")
    assert dt == datetime.fromisoformat("2026-07-10T11:57:54-04:00")
```

2. Verify fail:

```bash
python -m pytest tests/test_skill_flow_eval.py -v -k "bucket or boundary"
```

Expected: `AttributeError: module 'skill_flow_eval' has no attribute 'bucket_prs_by_boundary'`

3. Implement (append):

```python
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
```

4. Run, verify pass:

```bash
python -m pytest tests/test_skill_flow_eval.py -v -k "bucket or boundary"
```

Expected: `2 passed`

5. Commit:

```bash
git add evals/skill_flow_eval.py tests/test_skill_flow_eval.py
git commit -m "test+feat(skill-flow-eval): before/after boundary bucketing (#48)"
```

---

## Task 4: Tier 1 population mining (conformance / code-review)

**Files:** `evals/skill_flow_eval.py`, `tests/test_skill_flow_eval.py`

1. Add failing test:

```python
# tests/test_skill_flow_eval.py (append)
def test_mine_conformance_population_classifies_and_buckets(monkeypatch):
    prs = [
        {"number": 1, "headRefName": "feat/issue-46-x", "mergedAt": "2026-07-10T18:00:00Z", "state": "MERGED", "commits": [{"authors": [{"email": "factory@dark-factory"}]}], "labels": []},
        {"number": 2, "headRefName": "feat/issue-47-y", "mergedAt": "2026-07-10T21:00:00Z", "state": "MERGED", "commits": [{"authors": [{"email": "factory@dark-factory"}]}], "labels": []},
    ]
    comments_by_issue = {
        46: [{"body": "## Spec Conformance — Blocked\n..."}],
        47: [{"body": "no findings"}],
    }

    monkeypatch.setattr(sfe.fsc, "FACTORY_EMAIL", "factory@dark-factory")
    monkeypatch.setattr(sfe, "_fetch_issue_comments", lambda repo, num: comments_by_issue[num])

    boundary = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
    result = sfe.mine_conformance_population("omniscient/dark-factory", prs, boundary)

    assert result["before"]["n"] == 0
    assert result["after"]["n"] == 2
    assert result["after"]["blocked"] == 1
```

2. Verify fail:

```bash
python -m pytest tests/test_skill_flow_eval.py -v -k mine_conformance_population
```

Expected: `AttributeError: module 'skill_flow_eval' has no attribute 'mine_conformance_population'`

3. Implement (append):

```python
# ── Tier 1 population mining (issue #48) ──────────────────────────────────────
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
```

4. Run, verify pass:

```bash
python -m pytest tests/test_skill_flow_eval.py -v -k mine_conformance_population
```

Expected: `1 passed`

5. Commit:

```bash
git add evals/skill_flow_eval.py tests/test_skill_flow_eval.py
git commit -m "test+feat(skill-flow-eval): Tier 1 population mining for conformance/code-review (#48)"
```

---

## Task 5: Tier 2 population mining (label incidence)

**Files:** `evals/skill_flow_eval.py`, `tests/test_skill_flow_eval.py`

This task fills the gap the architect review flagged: the spec's Tier 2 mechanism (§3 step 3)
requires comparing "qualitative outcomes ... `factory-regression`/`scope-spillover`/
`needs-discussion` label incidence between the two populations." Token/tool-call/runtime are
NOT measurable this way (see Task 1's `_TIER2_TOKEN_GAP`) — only label incidence is.

1. Add failing test:

```python
# tests/test_skill_flow_eval.py (append)
def test_mine_label_incidence_counts_regression_and_needs_discussion(monkeypatch):
    prs = [
        {"number": 1, "mergedAt": "2026-07-10T05:00:00Z", "state": "MERGED",
         "commits": [{"authors": [{"email": "factory@dark-factory"}]}],
         "labels": [{"name": "factory-regression"}]},
        {"number": 2, "mergedAt": "2026-07-10T09:00:00Z", "state": "MERGED",
         "commits": [{"authors": [{"email": "factory@dark-factory"}]}],
         "labels": [{"name": "needs-discussion"}, {"name": "scope-spillover"}]},
        {"number": 3, "mergedAt": "2026-07-10T09:30:00Z", "state": "MERGED",
         "commits": [{"authors": [{"email": "factory@dark-factory"}]}],
         "labels": []},
    ]
    monkeypatch.setattr(sfe.fsc, "FACTORY_EMAIL", "factory@dark-factory")
    boundary = datetime(2026, 7, 10, 8, 0, 0, tzinfo=timezone.utc)

    result = sfe.mine_label_incidence(prs, boundary)

    assert result["before"] == {"n": 1, "factory_regression": 1, "scope_spillover": 0, "needs_discussion": 0}
    assert result["after"] == {"n": 2, "factory_regression": 0, "scope_spillover": 1, "needs_discussion": 1}
```

2. Verify fail:

```bash
python -m pytest tests/test_skill_flow_eval.py -v -k mine_label_incidence
```

Expected: `AttributeError: module 'skill_flow_eval' has no attribute 'mine_label_incidence'`

3. Implement (append to `evals/skill_flow_eval.py`):

```python
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
```

4. Run, verify pass:

```bash
python -m pytest tests/test_skill_flow_eval.py -v -k mine_label_incidence
```

Expected: `1 passed`

5. Commit:

```bash
git add evals/skill_flow_eval.py tests/test_skill_flow_eval.py
git commit -m "test+feat(skill-flow-eval): Tier 2 label-incidence mining, closes architect-review gap (#48)"
```

---

## Task 6: Scorecard schema + markdown renderer

**Files:** `evals/skill_flow_scorecard.py` (new), `tests/test_skill_flow_scorecard.py` (new)

1. Write failing test:

```python
# tests/test_skill_flow_scorecard.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import skill_flow_scorecard as sfs  # noqa: E402


def test_render_report_includes_scenario_table_and_tiers():
    rows = [
        {"scenario": "conformance", "tier": 1, "mechanism": "toggle A/B", "rollout": "default-on"},
        {"scenario": "refine", "tier": 2, "mechanism": "before/after #43", "rollout": "advisory-readiness"},
    ]
    md = sfs.render_report(rows, generated_at="2026-07-11T00:00:00+00:00")
    assert "# Skill-Modularization Scorecard" in md
    assert "| conformance | 1 |" in md
    assert "| refine | 2 |" in md
    assert "default-on" in md
    assert "advisory-readiness" in md


def test_render_report_footer_credits_script():
    md = sfs.render_report([], generated_at="2026-07-11T00:00:00+00:00")
    assert "evals/skill_flow_eval.py" in md
```

2. Verify fail:

```bash
python -m pytest tests/test_skill_flow_scorecard.py -v
```

Expected: `ModuleNotFoundError: No module named 'skill_flow_scorecard'`

3. Implement `evals/skill_flow_scorecard.py`:

```python
"""Markdown scorecard renderer for the skill-modularized prompt flow evaluation — issue #48.

Consumes rows produced by skill_flow_eval.py's mining/spot-check functions and renders the
committed report under evals/reports/, following the evals/reports/token-opt-scorecard-*.md
naming and section conventions.
"""
from __future__ import annotations


def render_report(rows: list[dict], generated_at: str) -> str:
    lines = [
        "# Skill-Modularization Scorecard",
        "",
        "**Issue:** [#48](https://github.com/omniscient/dark-factory/issues/48)",
        "**Script:** `evals/skill_flow_eval.py`",
        f"**Generated:** {generated_at}",
        "",
        "---",
        "",
        "## Scenario → Tier → Mechanism → Rollout",
        "",
        "| Scenario | Tier | Mechanism | Rollout Recommendation |",
        "|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['scenario']} | {row['tier']} | {row['mechanism']} | {row['rollout']} |"
        )
    lines += [
        "",
        "---",
        "",
        "*Generated by `evals/skill_flow_eval.py`*",
    ]
    return "\n".join(lines) + "\n"
```

4. Run, verify pass:

```bash
python -m pytest tests/test_skill_flow_scorecard.py -v
```

Expected: `2 passed`

5. Commit:

```bash
git add evals/skill_flow_scorecard.py tests/test_skill_flow_scorecard.py
git commit -m "test+feat(skill-flow-scorecard): markdown report renderer (#48)"
```

---

## Task 7: Tier-gated rollout recommendation logic

**Files:** `evals/skill_flow_scorecard.py`, `tests/test_skill_flow_scorecard.py`

1. Add failing tests:

```python
# tests/test_skill_flow_scorecard.py (append)
def test_tier1_scenario_can_reach_default_on():
    rollout = sfs.recommend_rollout(tier=1, before_blocked_rate=0.30, after_blocked_rate=0.05)
    assert rollout == "default-on"


def test_tier2_scenario_capped_at_advisory_even_with_strong_signal():
    # Even a dramatic improvement cannot license default-on for a Tier 2 scenario — spec
    # Requirement #6: only Tier 1 (conformance, code-review, plan's Phase 3.5) can reach it.
    rollout = sfs.recommend_rollout(tier=2, before_blocked_rate=0.90, after_blocked_rate=0.01)
    assert rollout == "advisory-readiness"


def test_tier1_no_go_when_after_worse_than_before():
    rollout = sfs.recommend_rollout(tier=1, before_blocked_rate=0.05, after_blocked_rate=0.30)
    assert rollout == "no-go"


def test_tier1_advisory_only_when_flat():
    rollout = sfs.recommend_rollout(tier=1, before_blocked_rate=0.10, after_blocked_rate=0.09)
    assert rollout == "advisory-only"
```

2. Verify fail:

```bash
python -m pytest tests/test_skill_flow_scorecard.py -v -k recommend_rollout
```

Expected: `AttributeError: module 'skill_flow_scorecard' has no attribute 'recommend_rollout'`

3. Implement (append to `evals/skill_flow_scorecard.py`):

```python
# ── Tier-gated rollout ladder (spec §6, Requirement #6) ───────────────────────
_DEFAULT_ON_IMPROVEMENT = 0.10  # after_rate must drop by >= 10pp vs before to earn default-on
_ADVISORY_FLAT_BAND = 0.05      # within 5pp either way counts as "flat" (advisory-only)


def recommend_rollout(tier: int, before_blocked_rate: float, after_blocked_rate: float) -> str:
    """Tier 2 scenarios (refine, plan's own narrative, implement/continue) can never return
    'default-on' from this evaluation — the confounded before/after-commit comparison cannot
    license it (spec Requirement #6). Tier 1 (conformance, code-review, plan's Phase 3.5) is the
    only ladder that reaches it, gated on the delta between mined before/after blocked rates."""
    if tier != 1:
        return "advisory-readiness"
    delta = before_blocked_rate - after_blocked_rate
    if delta < -_ADVISORY_FLAT_BAND:
        return "no-go"
    if delta >= _DEFAULT_ON_IMPROVEMENT:
        return "default-on"
    return "advisory-only"
```

4. Run, verify pass:

```bash
python -m pytest tests/test_skill_flow_scorecard.py -v -k recommend_rollout
```

Expected: `4 passed`

5. Commit:

```bash
git add evals/skill_flow_scorecard.py tests/test_skill_flow_scorecard.py
git commit -m "test+feat(skill-flow-scorecard): tier-gated rollout recommendation ladder (#48)"
```

---

## Task 8: CLI wiring — `main()` in `skill_flow_eval.py`

**Files:** `evals/skill_flow_eval.py`, `tests/test_skill_flow_eval.py`

`run()` must reassign `fsc`'s module globals before calling `fsc.fetch_prs()`/`fsc.fetch_issues()`
— those functions read `fsc.REPO`/`fsc._OWNER_REPO`/`fsc.FACTORY_EMAIL` rather than taking a
repo argument (`scripts/fetch_scorecard.py` lines ~22-26, ~230, ~267). This mirrors exactly what
`fetch_scorecard.py`'s own `__main__` block does for its `--repo` flag. `run()` must also
window PRs by `createdAt` via `fsc.in_window(...)` — the spec calls this "date-windowed PR
mining" (§4 step 2, §Tier-2-methodology step 2) and an unwindowed fetch silently ignores
`--since`/`--until`.

1. Add failing tests (argparse wiring + repo-scoping regression guard, no network):

```python
# tests/test_skill_flow_eval.py (append)
def test_build_arg_parser_defaults():
    parser = sfe.build_arg_parser()
    args = parser.parse_args([])
    assert args.repo == "omniscient/dark-factory"
    assert args.output_dir == "evals"


def test_build_arg_parser_overrides():
    parser = sfe.build_arg_parser()
    args = parser.parse_args(["--repo", "omniscient/markethawk", "--output-dir", "/tmp/out"])
    assert args.repo == "omniscient/markethawk"
    assert args.output_dir == "/tmp/out"


def test_run_reassigns_fetch_scorecard_repo_globals_before_fetching(monkeypatch):
    # Regression guard for the architect-review finding: run() previously called
    # fsc.fetch_prs()/fetch_issues() without reassigning fsc.REPO/_OWNER_REPO/FACTORY_EMAIL,
    # so --repo silently fetched from fetch_scorecard's markethawk default instead.
    seen_repo_at_fetch = {}

    def fake_fetch_prs():
        seen_repo_at_fetch["repo"] = sfe.fsc.REPO
        seen_repo_at_fetch["owner_repo"] = sfe.fsc._OWNER_REPO
        return []

    def fake_git(repo_root, *args):
        return "2026-07-10T11:57:54-04:00\n"

    monkeypatch.setattr(sfe.fsc, "fetch_prs", fake_fetch_prs)
    monkeypatch.setattr(sfe.fsc, "_git", fake_git)

    parser = sfe.build_arg_parser()
    args = parser.parse_args(["--repo", "omniscient/dark-factory"])
    sfe.run(args)

    assert seen_repo_at_fetch["repo"] == "omniscient/dark-factory"
    assert seen_repo_at_fetch["owner_repo"] == "omniscient/dark-factory"
    assert sfe.fsc.FACTORY_EMAIL == "factory@dark-factory"


def test_run_windows_prs_by_created_at(monkeypatch):
    in_window_prs = [{"number": 1, "createdAt": "2026-07-05T00:00:00Z", "headRefName": "x", "mergedAt": None, "state": "MERGED", "commits": [], "labels": []}]
    out_of_window_prs = [{"number": 2, "createdAt": "2026-06-01T00:00:00Z", "headRefName": "y", "mergedAt": None, "state": "MERGED", "commits": [], "labels": []}]

    monkeypatch.setattr(sfe.fsc, "fetch_prs", lambda: in_window_prs + out_of_window_prs)
    monkeypatch.setattr(sfe.fsc, "_git", lambda repo_root, *a: "2026-07-01T00:00:00+00:00\n")
    monkeypatch.setattr(sfe, "mine_conformance_population", lambda repo, prs, boundary: {"n_windowed": len(prs)})
    monkeypatch.setattr(sfe, "mine_code_review_population", lambda repo, prs, boundary: {"n_windowed": len(prs)})

    parser = sfe.build_arg_parser()
    args = parser.parse_args(["--since", "2026-07-01", "--until", "2026-07-10"])
    result = sfe.run(args)

    assert result["conformance"]["n_windowed"] == 1
```

2. Verify fail:

```bash
python -m pytest tests/test_skill_flow_eval.py -v -k "build_arg_parser or run_reassigns or run_windows"
```

Expected: `AttributeError: module 'skill_flow_eval' has no attribute 'build_arg_parser'`

3. Implement (append to `evals/skill_flow_eval.py`):

```python
# ── CLI ────────────────────────────────────────────────────────────────────────
def build_arg_parser():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="omniscient/dark-factory")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-dir", default="evals")
    parser.add_argument("--since", default="2026-05-01")
    parser.add_argument("--until", default=None)
    return parser


def run(args) -> dict:
    """Mine both Tier 1 (conformance/code-review) populations for one repo, date-windowed and
    boundary-bucketed. Returns the JSON-serializable population report; never raises on a
    missing/unreachable repo at the caller level — callers combining dark-factory + markethawk
    populations must catch per-repo and mark that source 'unavailable' (spec: harness must
    degrade gracefully)."""
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

    report = {"repo": args.repo, "since": since.isoformat(), "until": until.isoformat()}
    for row in SCENARIO_MAP:
        if row["scenario"] not in ("conformance", "code_review"):
            continue
        boundary = merge_boundary_date(args.repo_root, row["boundary_sha"])
        miner = mine_conformance_population if row["scenario"] == "conformance" else mine_code_review_population
        report[row["scenario"]] = miner(args.repo, windowed_prs, boundary)
    return report


if __name__ == "__main__":
    import json

    parsed = build_arg_parser().parse_args()
    output = run(parsed)
    os.makedirs(os.path.join(parsed.output_dir, "results"), exist_ok=True)
    out_path = os.path.join(
        parsed.output_dir, "results", f"skill-flow-population-{parsed.repo.split('/')[-1]}.json"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"Wrote {out_path}", file=sys.stderr)
```

4. Run, verify pass:

```bash
python -m pytest tests/test_skill_flow_eval.py -v -k "build_arg_parser or run_reassigns or run_windows"
```

Expected: `4 passed`

5. Run the full new test module to confirm no regressions before moving on:

```bash
python -m pytest tests/test_skill_flow_eval.py tests/test_skill_flow_scorecard.py -v
```

Expected: all tests passed (25 total across both files)

6. Commit:

```bash
git add evals/skill_flow_eval.py tests/test_skill_flow_eval.py
git commit -m "fix(skill-flow-eval): CLI entrypoint — repo-scope fetch_scorecard globals, date-window PRs (#48)"
```

---

## Task 9: Wire Tier 2 mining into the CLI

**Files:** `evals/skill_flow_eval.py`, `tests/test_skill_flow_eval.py`

1. Add failing test:

```python
# tests/test_skill_flow_eval.py (append)
def test_run_includes_tier2_label_incidence(monkeypatch):
    prs = [{"number": 1, "headRefName": "feat/issue-46-x", "createdAt": "2026-07-10T05:00:00Z",
            "mergedAt": "2026-07-10T05:00:00Z", "state": "MERGED",
            "commits": [{"authors": [{"email": "factory@dark-factory"}]}], "labels": []}]
    monkeypatch.setattr(sfe.fsc, "fetch_prs", lambda: prs)
    monkeypatch.setattr(sfe.fsc, "_git", lambda repo_root, *a: "2026-07-01T00:00:00+00:00\n")
    monkeypatch.setattr(sfe, "mine_conformance_population", lambda repo, prs, boundary: {})
    monkeypatch.setattr(sfe, "mine_code_review_population", lambda repo, prs, boundary: {})

    parser = sfe.build_arg_parser()
    args = parser.parse_args(["--since", "2026-07-01"])
    result = sfe.run(args)

    for scenario in ("refine", "plan_narrative", "continue"):
        assert scenario in result
        assert "before" in result[scenario] and "after" in result[scenario]
```

2. Verify fail:

```bash
python -m pytest tests/test_skill_flow_eval.py -v -k run_includes_tier2
```

Expected: `AssertionError` (refine/plan_narrative/continue keys absent from `result`)

3. Update `run()` in `evals/skill_flow_eval.py` — replace the scenario loop's `continue`-skip
   with a branch that also mines Tier 2 label incidence for the three Tier 2 scenarios:

```python
    report = {"repo": args.repo, "since": since.isoformat(), "until": until.isoformat()}
    for row in SCENARIO_MAP:
        boundary = merge_boundary_date(args.repo_root, row["boundary_sha"])
        if row["scenario"] == "conformance":
            report[row["scenario"]] = mine_conformance_population(args.repo, windowed_prs, boundary)
        elif row["scenario"] == "code_review":
            report[row["scenario"]] = mine_code_review_population(args.repo, windowed_prs, boundary)
        elif row["tier"] == 2:
            report[row["scenario"]] = mine_label_incidence(windowed_prs, boundary)
        # plan_phase_3_5 (Tier 1) shares conformance's population — no separate mining call.
    return report
```

4. Run, verify pass:

```bash
python -m pytest tests/test_skill_flow_eval.py -v -k run_includes_tier2
```

Expected: `1 passed`

5. Run the full module once more:

```bash
python -m pytest tests/test_skill_flow_eval.py tests/test_skill_flow_scorecard.py -v
```

Expected: all tests passed (26 total across both files)

6. Commit:

```bash
git add evals/skill_flow_eval.py tests/test_skill_flow_eval.py
git commit -m "feat(skill-flow-eval): wire Tier 2 label-incidence mining into run(), closes architect-review gap (#48)"
```

---

## Task 10: Tier-1 live spot-check manifest

**Files:** `evals/skill_flow_spotchecks.json` (new)

1. Write the manifest — 4 real, already-closed, already-merged dark-factory PRs, all merged
   after the #44 RUBRIC-toggle boundary (`f72738f8...`, 2026-07-10T11:57:54-04:00) so
   `.claude/skills/{conformance,code-review}/RUBRIC.md` exists on each PR's branch and can be
   meaningfully forced-absent for the "current-flow" arm:

```json
{
  "version": 1,
  "description": "Tier 1 live toggle spot-check pairs for issue #48 — RUBRIC.md present (skill-modularized arm) vs. forced-absent (current-flow arm), same diff held constant.",
  "pairs": [
    {"issue": 46, "pr": 229, "merge_sha": "bfc02393832c9ae29d52d9e9db1b6f73207f47be", "title": "Add factory review guardrails for Claude Skills"},
    {"issue": 45, "pr": 231, "merge_sha": "666da3db9cf035690bba0b629e550c9cc12069d9", "title": "Wire Claude Skills dynamic context injection to #36"},
    {"issue": 47, "pr": 233, "merge_sha": "88b233be3bb0248034f9af93e6ecf91f2e06fbac", "title": "Teach Dark Factory run and verify recipes"},
    {"issue": 204, "pr": 228, "merge_sha": "06f21e16e58abf7f7273269f93153e8e4bcd9891", "title": "scheduler: dependencies_met() misses bold Depends on"}
  ]
}
```

2. Add a structural test:

```python
# tests/test_skill_flow_eval.py (append)
import json


def test_spotcheck_manifest_has_3_to_5_pairs_all_post_boundary():
    manifest = json.loads((Path(__file__).resolve().parents[1] / "evals" / "skill_flow_spotchecks.json").read_text())
    pairs = manifest["pairs"]
    assert 3 <= len(pairs) <= 5
    for pair in pairs:
        assert {"issue", "pr", "merge_sha", "title"} <= pair.keys()
```

3. Run, verify pass:

```bash
python -m pytest tests/test_skill_flow_eval.py -v -k spotcheck_manifest
```

Expected: `1 passed`

4. Commit:

```bash
git add evals/skill_flow_spotchecks.json tests/test_skill_flow_eval.py
git commit -m "data(skill-flow-eval): Tier 1 live spot-check manifest, 4 post-#44 pairs (#48)"
```

---

## Task 11: Verdict-capturing prompt builders + live toggle spot-check runner

**Files:** `evals/skill_flow_eval.py`, `tests/test_skill_flow_eval.py`,
`evals/run_skill_toggle_spotcheck.sh` (new), `tests/test_run_skill_toggle_spotcheck.sh` (new)

Architect-review finding (cycle 2): the spot-check runner must actually capture a verdict per
arm — `DIMENSION_APPLICABILITY` claims conformance/code-review safety is "measured directly
(verdict deltas)," but nothing fed the reviewer real spec/issue/diff content or parsed its
verdict. Fix: substitute the RUBRIC's real `$VAR` placeholders (§ per
`.claude/skills/conformance/RUBRIC.md` lines 8-10, `.claude/skills/code-review/RUBRIC.md` lines
9-11) with real per-pair data, and parse the reviewer's own **Verdict:** line / findings-table
severities from the response — not the phase command's later posted-comment header (that header
is only used by Task 2/4's mined-population classifiers, a different data source).

### 11.1 — Prompt builders (pure functions, TDD)

1. Add failing tests:

```python
# tests/test_skill_flow_eval.py (append)
def test_render_conformance_prompt_substitutes_placeholders():
    rubric = "Kind: $ARTIFACT_KIND\nSpec:\n$SPEC_CONTENT\nArtifact:\n$ARTIFACT_CONTENT"
    out = sfe.render_conformance_prompt(rubric, spec_or_issue_body="the spec text", diff_content="the diff text")
    assert "Kind: IMPLEMENTATION" in out
    assert "the spec text" in out
    assert "the diff text" in out
    assert "$ARTIFACT_KIND" not in out and "$SPEC_CONTENT" not in out and "$ARTIFACT_CONTENT" not in out


def test_render_code_review_prompt_substitutes_placeholders():
    rubric = "Issue:\n$ISSUE_CONTEXT\nDiff:\n$DIFF_CONTENT"
    out = sfe.render_code_review_prompt(rubric, issue_context="# Title\nbody", diff_content="diff --git a b")
    assert "# Title\nbody" in out
    assert "diff --git a b" in out
    assert "$ISSUE_CONTEXT" not in out and "$DIFF_CONTENT" not in out


def test_extract_conformance_verdict_material():
    text = "## Conformance Review\n\n**Verdict:** ⛔ Material divergence\n\nDetails..."
    assert sfe.extract_conformance_verdict(text) == "MATERIAL"


def test_extract_conformance_verdict_conforms():
    text = "**Verdict:** ✅ Conforms\n"
    assert sfe.extract_conformance_verdict(text) == "CONFORMS"


def test_extract_conformance_verdict_minor():
    text = "**Verdict:** ⚠️ Minor deviations\n"
    assert sfe.extract_conformance_verdict(text) == "MINOR"


def test_extract_code_review_verdict_blocked_on_high_severity():
    text = """## Code Review

| # | Severity | Category | Location | Finding |
|---|----------|----------|----------|---------|
| 1 | high | security | backend/app/routers/x.py:42 | SQL built via f-string |
"""
    assert sfe.extract_code_review_verdict(text) == "BLOCKED"


def test_extract_code_review_verdict_pass_on_no_findings():
    text = "## Code Review\n\nNo findings.\n"
    assert sfe.extract_code_review_verdict(text) == "PASS"


def test_extract_code_review_verdict_pass_on_low_only():
    text = """## Code Review

| # | Severity | Category | Location | Finding |
|---|----------|----------|----------|---------|
| 1 | low | naming | frontend/src/foo.ts:88 | rename tmp |
"""
    assert sfe.extract_code_review_verdict(text) == "PASS"
```

2. Verify fail:

```bash
python -m pytest tests/test_skill_flow_eval.py -v -k "render_conformance_prompt or render_code_review_prompt or extract_conformance_verdict or extract_code_review_verdict"
```

Expected: `AttributeError: module 'skill_flow_eval' has no attribute 'render_conformance_prompt'`

3. Implement (append to `evals/skill_flow_eval.py`):

```python
# ── Live spot-check prompt building + verdict extraction (issue #48) ─────────
def render_conformance_prompt(rubric_content: str, spec_or_issue_body: str, diff_content: str) -> str:
    """Fills .claude/skills/conformance/RUBRIC.md's $ARTIFACT_KIND/$SPEC_CONTENT/$ARTIFACT_CONTENT
    placeholders exactly as commands/dark-factory-conformance.md Phase 3.1 step 4 does — spot-checks
    always review IMPLEMENTATION (a merged diff), and use the issue body as $SPEC_CONTENT when no
    formal spec file exists for the pair (NO_SPEC=true fallback, same as the phase command)."""
    return (
        rubric_content
        .replace("$ARTIFACT_KIND", "IMPLEMENTATION")
        .replace("$SPEC_CONTENT", spec_or_issue_body)
        .replace("$ARTIFACT_CONTENT", diff_content)
    )


def render_code_review_prompt(rubric_content: str, issue_context: str, diff_content: str) -> str:
    """Fills .claude/skills/code-review/RUBRIC.md's $ISSUE_CONTEXT/$DIFF_CONTENT placeholders."""
    return (
        rubric_content
        .replace("$ISSUE_CONTEXT", issue_context)
        .replace("$DIFF_CONTENT", diff_content)
    )


_VERDICT_LINE_RE = re.compile(r"\*\*Verdict:\*\*\s*(✅|⚠️|⛔)")
_VERDICT_SYMBOL_MAP = {"✅": "CONFORMS", "⚠️": "MINOR", "⛔": "MATERIAL"}
_FINDINGS_ROW_RE = re.compile(r"^\|\s*\d+\s*\|\s*(critical|high|medium|low)\s*\|", re.MULTILINE | re.IGNORECASE)
_BLOCKING_SEVERITIES = {"critical", "high"}


def extract_conformance_verdict(result_text: str) -> str:
    m = _VERDICT_LINE_RE.search(result_text)
    return _VERDICT_SYMBOL_MAP[m.group(1)] if m else "UNKNOWN"


def extract_code_review_verdict(result_text: str) -> str:
    severities = {m.group(1).lower() for m in _FINDINGS_ROW_RE.finditer(result_text)}
    return "BLOCKED" if severities & _BLOCKING_SEVERITIES else "PASS"
```

4. Run, verify pass:

```bash
python -m pytest tests/test_skill_flow_eval.py -v -k "render_conformance_prompt or render_code_review_prompt or extract_conformance_verdict or extract_code_review_verdict"
```

Expected: `8 passed`

5. Commit:

```bash
git add evals/skill_flow_eval.py tests/test_skill_flow_eval.py
git commit -m "feat(skill-flow-eval): verdict-capturing prompt builders for the live spot-check, closes architect-review gap (#48)"
```

### 11.2 — Runner script

1. Write the script. It checks out each pair's merge SHA into a scratch git worktree, fetches
   real per-pair `$ISSUE_CONTEXT` (via `gh issue view`) and `$DIFF_CONTENT`/`$ARTIFACT_CONTENT`
   (via `git diff <sha>^1 <sha>^2` — the two parents of a "Merge pull request #N" commit),
   resolves `$SPEC_CONTENT` from `docs/superpowers/specs/` if a matching file exists on that
   worktree or falls back to the issue body (NO_SPEC=true convention), resolves `RUBRIC_CONTENT`
   exactly as the phase commands do (clone-live-first, baked fallback), builds each arm's prompt
   via Task 11.1's pure functions, and records token/turn/duration/verdict from `claude -p
   --output-format json`:

```bash
#!/usr/bin/env bash
# run_skill_toggle_spotcheck.sh — Tier 1 live RUBRIC-toggle spot-check runner (issue #48)
#
# Usage:
#   evals/run_skill_toggle_spotcheck.sh [--dry-run] [--budget-usd N]
#
# Reads evals/skill_flow_spotchecks.json, and for each pair + each gate
# (conformance, code-review) runs two arms holding the diff constant:
#   arm A "skill-modularized": .claude/skills/<gate>/RUBRIC.md present (default state)
#   arm B "current-flow":      RUBRIC.md moved aside so the baked
#                               /opt/refinement-skills/<gate>-reviewer-prompt.md is used
#
# Output: evals/results/skill-flow-spotcheck-<UTC-date>.json (one record per pair x gate x arm,
# including a 'verdict' field parsed from the reviewer's own response)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MANIFEST="$REPO_ROOT/evals/skill_flow_spotchecks.json"
RESULTS_DIR="$REPO_ROOT/evals/results"
BUDGET_USD="${BUDGET_USD:-5.00}"
DRY_RUN=false
# build_prompt()'s `gh issue view` call needs this; unlike a normal phase command run (where
# entrypoint.sh always sets it), a standalone script invocation may not have it in the
# environment — derive it from the current checkout rather than failing under `set -u`.
FACTORY_REPO_SLUG="${FACTORY_REPO_SLUG:-$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || echo omniscient/dark-factory)}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --budget-usd) BUDGET_USD="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

mkdir -p "$RESULTS_DIR"
OUT_FILE="$RESULTS_DIR/skill-flow-spotcheck-$(date -u +%Y-%m-%d).json"

log() { echo "[spotcheck] $*" >&2; }

resolve_rubric() {
  # $1 = worktree dir, $2 = gate (conformance|code-review); prints resolved content to stdout
  local wt="$1" gate="$2"
  local live="$wt/.claude/skills/$gate/RUBRIC.md"
  local baked="$wt/refinement-skills/${gate}-reviewer-prompt.md"
  if [ -f "$live" ]; then cat "$live"; else cat "$baked"; fi
}

build_prompt() {
  # $1 = worktree dir, $2 = gate, $3 = issue, $4 = merge sha; prints the filled prompt to stdout.
  # Writes each substitution field to its own temp file — more robust than passing large diff/
  # spec text through shell variables or a single delimited stdin stream.
  local wt="$1" gate="$2" issue="$3" sha="$4"
  local tmp_rubric tmp_a tmp_b tmp_c spec_file

  tmp_rubric=$(mktemp) tmp_a=$(mktemp) tmp_b=$(mktemp) tmp_c=$(mktemp)
  resolve_rubric "$wt" "$gate" > "$tmp_rubric"
  gh issue view "$issue" --repo "$FACTORY_REPO_SLUG" --json title,body \
    --jq '"# " + .title + "\n\n" + .body' > "$tmp_a"
  # `|| true` is required: under `set -o pipefail`, if git's output exceeds 200000 bytes, head
  # closes its input early, git receives SIGPIPE and exits 141, and pipefail would otherwise
  # abort the whole script on precisely the large diffs this truncation exists to handle.
  git -C "$wt" diff "${sha}^1" "${sha}^2" -- . 2>/dev/null | head -c 200000 > "$tmp_c" || true

  if [ "$gate" = "code-review" ]; then
    python3 -c "
import sys; sys.path.insert(0, '$REPO_ROOT/evals')
import skill_flow_eval as sfe
rubric, issue_context, diff_content = (open(p, encoding='utf-8').read() for p in sys.argv[1:4])
print(sfe.render_code_review_prompt(rubric, issue_context, diff_content))
" "$tmp_rubric" "$tmp_a" "$tmp_c"
  else
    spec_file=$(find "$wt/docs/superpowers/specs" -iname "*issue-${issue}-*" 2>/dev/null | head -1)
    if [ -n "$spec_file" ]; then cp "$spec_file" "$tmp_b"; else cp "$tmp_a" "$tmp_b"; fi
    python3 -c "
import sys; sys.path.insert(0, '$REPO_ROOT/evals')
import skill_flow_eval as sfe
rubric, spec_content, diff_content = (open(p, encoding='utf-8').read() for p in sys.argv[1:4])
print(sfe.render_conformance_prompt(rubric, spec_content, diff_content))
" "$tmp_rubric" "$tmp_b" "$tmp_c"
  fi
  rm -f "$tmp_rubric" "$tmp_a" "$tmp_b" "$tmp_c"
}

extract_verdict() {
  # $1 = gate, $2 = result text — result text passed as a single arg (bounded by claude's own
  # response size, unlike the diff/spec inputs above which can be large).
  python3 -c "
import sys; sys.path.insert(0, '$REPO_ROOT/evals')
import skill_flow_eval as sfe
gate, text = sys.argv[1], sys.argv[2]
print(sfe.extract_code_review_verdict(text) if gate == 'code-review' else sfe.extract_conformance_verdict(text))
" "$1" "$2"
}

run_arm() {
  # $1 = worktree dir, $2 = gate, $3 = arm ("modularized"|"current-flow"), $4 = issue, $5 = sha
  local wt="$1" gate="$2" arm="$3" issue="$4" sha="$5"
  local live="$wt/.claude/skills/$gate/RUBRIC.md"
  local moved="$wt/.claude/skills/$gate/RUBRIC.md.spotcheck-hidden"

  if [ "$arm" = "current-flow" ] && [ -f "$live" ]; then
    mv "$live" "$moved"
  fi

  local prompt
  prompt=$(build_prompt "$wt" "$gate" "$issue" "$sha")

  local result_json="$RESULTS_DIR/.tmp-${gate}-${arm}.json"
  if [ "$DRY_RUN" = "true" ]; then
    echo '{"total_cost_usd": 0, "num_turns": 0, "duration_ms": 0, "result": "dry-run"}' > "$result_json"
  else
    claude -p "$prompt" --model claude-opus-4-8 --output-format json > "$result_json"
  fi

  if [ "$arm" = "current-flow" ] && [ -f "$moved" ]; then
    mv "$moved" "$live"
  fi
  cat "$result_json"
}

log "Loading manifest: $MANIFEST"
PAIRS=$(python3 -c "import json; print(json.dumps(json.load(open('$MANIFEST'))['pairs']))")
PAIR_COUNT=$(python3 -c "import json,sys; print(len(json.loads(sys.argv[1])))" "$PAIRS")
log "Loaded $PAIR_COUNT pairs"

if [ "$DRY_RUN" = "true" ]; then
  python3 -c "
import json, sys
for p in json.loads(sys.argv[1]):
    print(f\"  #{p['issue']} PR#{p['pr']} ({p['merge_sha'][:8]}) — {p['title'][:60]}\")
" "$PAIRS"
  echo "DRY RUN — no worktrees created, no gh/claude invocations made" >&2
  exit 0
fi

RECORDS="[]"
for row in $(python3 -c "import json,sys; [print(json.dumps(p)) for p in json.loads(sys.argv[1])]" "$PAIRS"); do
  PAIR=$(python3 -c "import json,base64; print(json.dumps(json.loads('$row')))")
  ISSUE=$(python3 -c "import json; print(json.loads('$PAIR')['issue'])")
  SHA=$(python3 -c "import json; print(json.loads('$PAIR')['merge_sha'])")
  WT="$REPO_ROOT/.spotcheck-wt-$ISSUE"

  git -C "$REPO_ROOT" worktree remove --force "$WT" 2>/dev/null || true
  git -C "$REPO_ROOT" worktree add --detach "$WT" "$SHA" >/dev/null

  # build_prompt() diffs against ${SHA}^2 (the feature-branch parent of a two-parent merge
  # commit) — guard against a future manifest entry that isn't a merge commit (e.g. a
  # squash-merged PR has no second parent) rather than aborting the whole run under set -e.
  if ! git -C "$REPO_ROOT" rev-parse "${SHA}^2" >/dev/null 2>&1; then
    log "issue #$ISSUE: $SHA is not a two-parent merge commit — skipping pair"
    git -C "$REPO_ROOT" worktree remove --force "$WT" 2>/dev/null || true
    continue
  fi

  for GATE in conformance code-review; do
    for ARM in modularized current-flow; do
      log "issue #$ISSUE gate=$GATE arm=$ARM"
      RESULT=$(run_arm "$WT" "$GATE" "$ARM" "$ISSUE" "$SHA")
      RESULT_TEXT=$(python3 -c "import json,sys; print(json.loads(sys.argv[1]).get('result',''))" "$RESULT")
      VERDICT=$(extract_verdict "$GATE" "$RESULT_TEXT")
      # $RESULT is claude's raw JSON output, whose 'result' field is arbitrary model free-text —
      # pass it as argv, never interpolate it into a python source string (it could contain
      # backticks, $(...), or unmatched quotes that break out of an embedded literal).
      RECORD=$(python3 -c "
import json, sys
r = json.loads(sys.argv[1])
print(json.dumps({'issue': int(sys.argv[2]), 'gate': sys.argv[3], 'arm': sys.argv[4], 'verdict': sys.argv[5],
                   'total_cost_usd': r.get('total_cost_usd', 0),
                   'num_turns': r.get('num_turns', 0),
                   'duration_ms': r.get('duration_ms', 0)}))
" "$RESULT" "$ISSUE" "$GATE" "$ARM" "$VERDICT")
      RECORDS=$(python3 -c "
import json
recs = json.loads('$RECORDS'); recs.append(json.loads('$RECORD')); print(json.dumps(recs))
")
      TOTAL_COST=$(python3 -c "
import json
recs = json.loads('$RECORDS')
print(sum(r['total_cost_usd'] for r in recs))
")
      python3 -c "
import sys
if float('$TOTAL_COST') > float('$BUDGET_USD'):
    print('BUDGET EXCEEDED: total \$$TOTAL_COST > cap \$$BUDGET_USD', file=sys.stderr); sys.exit(1)
"
    done
  done

  git -C "$REPO_ROOT" worktree remove --force "$WT" 2>/dev/null || true
done

echo "$RECORDS" > "$OUT_FILE"
log "Wrote $OUT_FILE"
```

2. Add the smoke test (dry-run only — no network, no `gh`/`claude` invocation):

```bash
#!/usr/bin/env bash
# tests/test_run_skill_toggle_spotcheck.sh
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

OUTPUT=$("$REPO_ROOT/evals/run_skill_toggle_spotcheck.sh" --dry-run 2>&1)

echo "$OUTPUT" | grep -q "DRY RUN" || { echo "FAIL: missing DRY RUN marker"; exit 1; }
echo "$OUTPUT" | grep -q "#46 PR#229" || { echo "FAIL: missing pair #46/PR229"; exit 1; }
echo "$OUTPUT" | grep -q "#45 PR#231" || { echo "FAIL: missing pair #45/PR231"; exit 1; }
echo "$OUTPUT" | grep -q "#47 PR#233" || { echo "FAIL: missing pair #47/PR233"; exit 1; }
echo "$OUTPUT" | grep -q "#204 PR#228" || { echo "FAIL: missing pair #204/PR228"; exit 1; }

echo "PASS: run_skill_toggle_spotcheck.sh --dry-run"
```

3. Make both executable, verify the smoke test fails before the script has correct permissions,
   then passes:

```bash
chmod +x evals/run_skill_toggle_spotcheck.sh tests/test_run_skill_toggle_spotcheck.sh
bash tests/test_run_skill_toggle_spotcheck.sh
```

Expected: `PASS: run_skill_toggle_spotcheck.sh --dry-run`

4. Commit:

```bash
git add evals/run_skill_toggle_spotcheck.sh tests/test_run_skill_toggle_spotcheck.sh
git commit -m "feat(skill-flow-eval): live RUBRIC-toggle spot-check runner with real diff/issue context + verdict capture (#48)"
```

---

## Task 12: Scorecard row assembly (`build_rows`) + generate the draft scorecard

**Files:** `evals/skill_flow_scorecard.py`, `tests/test_skill_flow_scorecard.py`,
`evals/results/skill-flow-population-dark-factory-2026-07-11.json` (generated),
`evals/reports/skill-modularization-scorecard-2026-07-11.md` (generated)

Architect-review finding (cycle 2): the population→rows→report assembly step must be an actual
committed, tested function, not free-hand prose — and `plan_phase_3_5`'s rate sourcing (spec
§6: "folded into the conformance verdict since it shares the same RUBRIC.md artifact") must be
explicit rather than implied.

### 12.1 — `build_rows()` (TDD)

1. Add failing test:

```python
# tests/test_skill_flow_scorecard.py (append)
def test_build_rows_folds_plan_phase_3_5_into_conformance_and_caps_tier2():
    population = {
        "conformance": {"before": {"n": 10, "blocked": 3}, "after": {"n": 10, "blocked": 0}},
        "code_review": {"before": {"n": 10, "blocked": 2}, "after": {"n": 10, "blocked": 2}},
        "refine": {"before": {"n": 5, "needs_discussion": 2}, "after": {"n": 5, "needs_discussion": 0}},
        "plan_narrative": {"before": {"n": 5, "needs_discussion": 1}, "after": {"n": 5, "needs_discussion": 1}},
        "continue": {"before": {"n": 3, "needs_discussion": 0}, "after": {"n": 3, "needs_discussion": 0}},
    }
    rows = sfs.build_rows(population)
    by_scenario = {r["scenario"]: r for r in rows}

    # conformance: before=30% blocked, after=0% blocked -> >=10pp improvement -> default-on
    assert by_scenario["conformance"]["rollout"] == "default-on"
    # plan_phase_3_5 has no own population entry -> must reuse conformance's rates/rollout
    assert by_scenario["plan_phase_3_5"]["rollout"] == by_scenario["conformance"]["rollout"]
    # code_review: flat (20% both) -> advisory-only
    assert by_scenario["code_review"]["rollout"] == "advisory-only"
    # every Tier 2 scenario is capped regardless of its label-incidence delta
    for scenario in ("refine", "plan_narrative", "continue"):
        assert by_scenario[scenario]["rollout"] == "advisory-readiness"
    assert len(rows) == 6  # all SCENARIO_MAP entries represented, including plan_phase_3_5
```

2. Verify fail:

```bash
python -m pytest tests/test_skill_flow_scorecard.py -v -k build_rows
```

Expected: `AttributeError: module 'skill_flow_scorecard' has no attribute 'build_rows'`

3. Implement (append to `evals/skill_flow_scorecard.py`):

```python
# ── Population -> scorecard rows (issue #48) ──────────────────────────────────
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
import skill_flow_eval as sfe  # noqa: E402


def _blocked_rate(bucket: dict) -> float:
    n = bucket.get("n", 0)
    blocked = bucket.get("blocked", 0)
    return blocked / n if n else 0.0


def build_rows(population: dict) -> list[dict]:
    """population: the dict Task 8/9's run() writes (one key per scenario in SCENARIO_MAP,
    except plan_phase_3_5, which has no separate mining call — it reuses conformance's
    population per spec §6, "folded into the conformance verdict since it shares the same
    RUBRIC.md artifact")."""
    rows = []
    for entry in sfe.SCENARIO_MAP:
        scenario = entry["scenario"]
        pop_key = "conformance" if scenario == "plan_phase_3_5" else scenario
        pop = population.get(pop_key, {})
        before, after = pop.get("before", {}), pop.get("after", {})

        if entry["tier"] == 1:
            rollout = recommend_rollout(1, _blocked_rate(before), _blocked_rate(after))
        else:
            rollout = recommend_rollout(2, 0.0, 0.0)  # rate unused for tier != 1; see recommend_rollout

        rows.append({
            "scenario": scenario,
            "tier": entry["tier"],
            "mechanism": entry["mechanism"],
            "rollout": rollout,
        })
    return rows
```

4. Run, verify pass:

```bash
python -m pytest tests/test_skill_flow_scorecard.py -v -k build_rows
```

Expected: `1 passed`

5. Run the full scorecard test module:

```bash
python -m pytest tests/test_skill_flow_scorecard.py -v
```

Expected: `7 passed`

6. Commit:

```bash
git add evals/skill_flow_scorecard.py tests/test_skill_flow_scorecard.py
git commit -m "feat(skill-flow-scorecard): build_rows() population-to-report assembly, closes architect-review gap (#48)"
```

### 12.2 — Mine the real dark-factory population and generate the draft scorecard

**Files:** `evals/results/skill-flow-population-dark-factory-2026-07-11.json` (generated),
`evals/reports/skill-modularization-scorecard-2026-07-11.md` (generated)

1. Run the harness against the self-target repo (always reachable — this is the running
   clone):

```bash
python3 evals/skill_flow_eval.py --repo omniscient/dark-factory --repo-root . --output-dir evals
```

Expected output (stderr): `Wrote evals/results/skill-flow-population-dark-factory.json`

2. Copy to the dated snapshot recorded in this plan's File Structure table (Task 8's `run()`
   names the file by repo, not date):

```bash
cp evals/results/skill-flow-population-dark-factory.json \
   evals/results/skill-flow-population-dark-factory-2026-07-11.json
```

3. Attempt the markethawk volume population; if `gh`/network cannot reach it in this
   environment, the run still succeeds for dark-factory and the report notes markethawk as
   unavailable:

```bash
python3 evals/skill_flow_eval.py --repo omniscient/markethawk --repo-root . --output-dir evals \
  || echo "markethawk population unavailable in this environment — noted in report" >&2
```

4. Generate the draft report using Task 12.1's `build_rows()`:

```bash
python3 -c "
import json, sys
sys.path.insert(0, 'evals')
import skill_flow_scorecard as sfs

population = json.load(open('evals/results/skill-flow-population-dark-factory-2026-07-11.json'))
rows = sfs.build_rows(population)
report = sfs.render_report(rows, generated_at='2026-07-11T00:00:00+00:00')
open('evals/reports/skill-modularization-scorecard-2026-07-11.md', 'w').write(report)
print('Wrote evals/reports/skill-modularization-scorecard-2026-07-11.md', file=sys.stderr)
"
```

5. Hand-edit the generated file to add the two narrative sections required by the spec's
   Deliverables (§5) — these are prose/data synthesis, not mechanically derivable from
   `build_rows()`'s row list:
   - A **"Confounds"** subsection under each Tier 2 scenario, listing what's disclosed
     (different issues, complexity, and any `evals/factory-failures.jsonl` entries in the
     boundary window) plus the Tier 2 token/tool-call/runtime gap disclosed in this plan's
     Architecture section.
   - A **"Tier 1 Corroboration"** subsection summarizing the mined before/after #44 verdict
     counts (from the JSON written in step 1) plus an explicit note that `plan_phase_3_5`'s row
     reuses conformance's rates (per Task 12.1's `build_rows()`), left with a placeholder note
     "(live spot-check results pending — see Task 13)" to be filled in by Task 13.

6. Commit the generated artifacts:

```bash
git add evals/results/skill-flow-population-dark-factory-2026-07-11.json \
        evals/reports/skill-modularization-scorecard-2026-07-11.md
git commit -m "chore(skill-flow-eval): mine dark-factory population, draft scorecard (#48)"
```

---

## Task 13: Run the live Tier-1 spot-checks and fold results into the scorecard

**Files:** `evals/results/skill-flow-spotcheck-2026-07-11.json` (generated),
`evals/reports/skill-modularization-scorecard-2026-07-11.md` (updated)

1. Run the live toggle spot-check runner for real, budget-capped at $5 total (spec:
   "hard-budget-capped set of live toggle spot-checks"):

```bash
BUDGET_USD=5.00 evals/run_skill_toggle_spotcheck.sh
```

Expected output (stderr): `[spotcheck] Wrote evals/results/skill-flow-spotcheck-2026-07-11.json`
(the runner exits non-zero with `BUDGET EXCEEDED` if the cap is hit mid-run — if that happens,
record which pairs completed and note the truncation explicitly in the scorecard rather than
silently reporting partial coverage as complete, per the "no silent caps" principle applied
elsewhere in this repo's eval tooling).

2. Replace Task 12.2 step 5's "(live spot-check results pending — see Task 13)" placeholder in
   the "Tier 1 Corroboration" subsection with the per-pair token/turn/duration deltas and the
   captured `verdict` field (per pair, per gate, per arm — from Task 11.1's
   `extract_conformance_verdict`/`extract_code_review_verdict`) between the `modularized` and
   `current-flow` arms for each of conformance and code-review, and state the final rollout
   recommendation for conformance, code-review, and plan's Phase 3.5 check explicitly (per spec
   §6: "already live on `main` since #44/#45 merged — default-on here means confirming the
   existing default is safe to keep").

3. Verify the placeholder was actually replaced before committing (a skipped fill-in must not
   ship silently):

```bash
grep -q "live spot-check results pending" evals/reports/skill-modularization-scorecard-2026-07-11.md \
  && { echo "FAIL: placeholder still present in scorecard"; exit 1; } || echo "OK: placeholder replaced"
```

Expected: `OK: placeholder replaced`

4. Commit:

```bash
git add evals/results/skill-flow-spotcheck-2026-07-11.json \
        evals/reports/skill-modularization-scorecard-2026-07-11.md
git commit -m "chore(skill-flow-eval): live Tier 1 spot-check results + final rollout recommendation (#48)"
```

---

## Task 14: Full test suite regression check

**Files:** none (verification only)

1. Run the full suite to confirm no regressions:

```bash
python -m pytest tests/ -v
bash smoke_gate.sh
```

Expected: all tests pass; `smoke_gate.sh` exits 0.

2. If any pre-existing failure is unrelated to this ticket's files, note it in the PR
   description rather than fixing it here (scope discipline — conformance gate would excise an
   unrelated fix anyway).
