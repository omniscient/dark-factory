# Implementation Plan: Skill-Modularized Dark Factory Prompt Flow Evaluation

**Issue:** omniscient/dark-factory#48
**Spec:** `docs/superpowers/specs/2026-07-11-skill-modularized-prompt-flow-evaluation-design.md`
**Depends on:** omniscient/dark-factory#43, #44, #45 (all merged)
**Supersedes:** `docs/superpowers/plans/2026-07-11-skill-modularized-prompt-flow-evaluation.md`, which
exhausted its 3 architect-review cycles with 3 unverified post-cycle-3 bash-runner fixes. Per the
operator's 2026-07-12 scope-decision comment on #48 ("regenerate the plan fresh rather than
resuming it"), this document is a fresh draft, not a patch of that file — the old plan file was
removed directly on this branch during this plan phase (see the note at the top of Task 1).

---

## Goal

Produce a tier-honest scorecard comparing current-flow vs. skill-modularized-flow across all six
named scenarios (refine, plan's own narrative, plan's Phase 3.5 check, implement's continue-intent,
conformance, code-review), plus a rollout recommendation, by building: a scenario→tier→mechanism
map, a mining harness that reuses `scripts/fetch_scorecard.py` to pull and classify historical
PR/issue evidence, a small budget-capped live toggle spot-check for the two Tier 1 gates with a
real runtime-swappable artifact (conformance, code-review — folding in plan's Phase 3.5 check per
spec §6), and a committed scorecard document. Per the operator's narrowing comment, this stays
scoped to the original question (token count, tool calls, runtime, quality/safety, trigger
accuracy) — whole-harness "Harness Effect" economics belongs to #240/Epic #234, not here. This is
a **spec + script + scorecard** deliverable (mirrors #161/#672), not a standing service.

## Architecture

Two-tier hybrid, per the spec:

- **Tier 1 (conformance, code-review, plan's Phase 3.5 check):** a small (3–5 pair) live
  budget-capped toggle A/B — same historical issue/diff, `.claude/skills/{conformance,code-review}/RUBRIC.md`
  present vs. forced-absent — plus mined historical verdicts (`## Spec Conformance — Blocked` /
  `## Code Review — Blocked` issue-comment headers) bucketed before/after the #44 merge boundary
  (`f72738f8beb3e079335bc4daf9b1da85a198b2ef`, PR #225, merged 2026-07-10T11:57:54-04:00) as
  corroboration.
- **Tier 2 (refine, plan's own narrative, implement's continue-intent):** observational
  before/after the relevant merge boundary — #43 (`1d1b5d31af6bad93aa349f95fab56b128b966adf`,
  PR #220, merged 2026-07-10T07:23:41-04:00) for refine/plan-narrative, #45
  (`666da3db9cf035690bba0b629e550c9cc12069d9`, PR #231, merged 2026-07-10T17:54:05-04:00) for
  continue. Tier 2's qualitative proxy is mined **label incidence** (`factory-regression`,
  `scope-spillover`, `needs-discussion`) per before/after bucket.

**Two corrections vs. the superseded draft, made explicit here so they aren't silently
reintroduced:**

1. **Population source split is per-mechanism, not per-repo blend.** Spec Requirement #3 assigns
   qualitative/causal judgments to the `omniscient/dark-factory` self-target (required, always
   reachable — we're running inside it) and quantitative *volume* to `omniscient/markethawk`
   (optional, wrapped so an unreachable second repo degrades to `"population": "unavailable"`
   rather than crashing the harness — see Task 5). But **token/tool-call/runtime figures are not
   retrievable from either repo's mined history** — `$ARTIFACTS_DIR/*.md` does not survive past its
   container, and no phase command posts a cost-report comment (verified: `commands/dark-factory-*.md`
   post status/verdict comments, not cost breakdowns). Only the live Tier 1 spot-check pairs
   (Task 11, direct API `usage` from `claude -p --output-format json`) measure token/tool-call/runtime
   directly. Cross-repo mining widens the **verdict-rate** sample only. This is reported as
   `"not measurable from mined data — no durable per-run cost artifact"` for Tier 2 and disclosed
   precisely (not "measured + mined") for Tier 1, rather than overclaiming what mining can prove.
2. **The live spot-check runner is plain Python (`subprocess.run` with argv lists + stdin), not
   bash string interpolation.** The superseded draft's bash runner accumulated three real bugs from
   this exact pattern (a `pipefail`-triggered abort on `head -c | > file`, untrusted model output
   embedded into a Python source string via `bash -c`, and an unset `$FACTORY_REPO_SLUG` under
   `set -u`) across 3 architect cycles. `scripts/factory_core/main_red_fixer.py`'s `_run()` helper
   already establishes the safe pattern this codebase uses for `claude -p` calls (argv list, prompt
   via `stdin=`, no shell) — Task 11 follows it directly, which removes the entire bug class by
   construction rather than patching each instance.

Verdicts are mined from GitHub, not from ephemeral per-run artifacts. The mining functions key off
the same durable signals `fetch_scorecard.py` already uses — PR/issue comments and labels:

- Conformance MATERIAL/BLOCKED ⇒ an issue comment whose body starts with `## Spec Conformance —
  Blocked` (see `commands/dark-factory-conformance.md` Phase 5 and `commands/dark-factory-plan.md`
  Phase 3.5 step 8b) plus the `needs-discussion` label. Absence ⇒ CONFORMS or MINOR (conformance is
  silent on PASS).
- Code-review BLOCKED ⇒ an issue comment whose body starts with `## Code Review — Blocked` (see
  `commands/dark-factory-code-review.md` Phase 6) plus `needs-discussion`. Absence ⇒ PASS.

## Tech Stack

Python (`evals/skill_flow_eval.py`, `evals/skill_flow_scorecard.py`, `evals/skill_flow_spotcheck.py`),
reusing `scripts/fetch_scorecard.py` as an importable module (same `sys.path.insert(scripts/)`
pattern as `tests/test_fetch_scorecard.py`) — **including reassigning its module globals
(`fsc.REPO`/`fsc._OWNER_REPO`/`fsc.FACTORY_EMAIL`) before calling `fsc.fetch_prs()`**, exactly as
`fetch_scorecard.py`'s own `__main__` block does at its `--repo` handling (lines ~392-400), since
`fetch_prs()`/`fetch_issues()` read those globals rather than taking a repo argument. `pytest` for
all new Python; `evals/skill_flow_spotcheck.py --dry-run` is the executable smoke path (no bash
test needed since there is no bash runner in this plan).

---

## File Structure

| File | Change |
|---|---|
| `evals/skill_flow_eval.py` | New — scenario map, dimension applicability, verdict classifiers, boundary bucketing, Tier 1 + Tier 2 population mining, CLI `main()` |
| `tests/test_skill_flow_eval.py` | New — TDD for the above (mocked `gh`/`git`, no network) |
| `evals/skill_flow_scorecard.py` | New — scorecard schema, tier-gated rollout logic, markdown report renderer |
| `tests/test_skill_flow_scorecard.py` | New — TDD for schema/renderer/tier-gate |
| `evals/skill_flow_spotchecks.json` | New — Tier 1 live spot-check manifest (4 real closed dark-factory issue/PR pairs) |
| `evals/skill_flow_spotcheck.py` | New — live RUBRIC-toggle runner for the spot-check manifest (Python, argv+stdin subprocess) |
| `tests/test_skill_flow_spotcheck.py` | New — TDD for prompt rendering / verdict extraction / budget cap (mocked subprocess) |
| `evals/reports/skill-modularization-scorecard-2026-07-12.md` | New — generated scorecard + rollout recommendation (committed deliverable) |
| `evals/results/skill-flow-population-dark-factory-2026-07-12.json` | New — generated dark-factory-population mining output (Tier 1 + Tier 2), gitignored per `evals/.gitignore` |
| `evals/results/skill-flow-spotcheck-2026-07-12.json` | New — generated Tier 1 live spot-check results, gitignored per `evals/.gitignore` |
| `docs/superpowers/plans/2026-07-11-skill-modularized-prompt-flow-evaluation.md` | Removed — superseded draft (removed directly on this branch during the plan phase, not an implementation task) |

Note: `evals/.gitignore` ignores `results/`; only the scorecard report under `evals/reports/` is a
committed deliverable, matching `token-opt-scorecard-*.md` convention.

---

## Memory Context Applied

- **`.archon/memory/architecture.md` [AVOID] (issue #48, refine):** #43/#44/#45's modularization is
  asymmetric — conformance/code-review/plan's-Phase-3.5 got a real clone-live-vs-baked toggle
  artifact; refine/plan-narrative got prose dedup only (no swappable artifact); continue got a
  context-injection swap (also no toggle). This plan's Task 1 `SCENARIO_MAP` encodes exactly this
  split as data (tier 1 vs tier 2 per scenario), and Task 9's rollout-gate test asserts a Tier 2
  scenario can never receive a `default-on` recommendation regardless of its mined numbers.
- **`.archon/memory/architecture.md` [AVOID] (issue #48, refine):** split data sources by dimension,
  not by a single blended repo pool — self-target for qualitative/causal, markethawk+bench for
  quantitative volume — and prefer mining over a full paid live-replay campaign, reserving live
  replay for a small hard-budget-capped spot-check set. Task 4's self-target mining, Task 5's
  markethawk-widening (wrapped for graceful unavailability), and Task 11's 4-pair budget-capped
  spot-check runner implement this split directly; Task 11 never dispatches a full `archon workflow
  run` (no full paid replay) — only a single non-tool-using `claude -p` completion per arm.
- **`.archon/memory/codebase-patterns.md` [PATTERN] (issue #42):** this plan's spec and this plan
  file do not transfer automatically from this `refine/issue-48-*` branch to the
  `feat/issue-48-*` implementation branch — the implement-phase agent that picks this plan up must
  itself copy `docs/superpowers/specs/2026-07-11-skill-modularized-prompt-flow-evaluation-design.md`
  and this plan onto the feat branch and commit them before starting Task 1 (standard implement-phase
  behavior, not a step enumerated in this plan). The design spec stays at its durable
  `docs/superpowers/specs/` path afterward (it is a living methodology reference, not just a
  completed-workflow artifact); only the *plan* file is a candidate for a later `docs/archive/` move.
- **`.archon/memory/codebase-patterns.md` [PATTERN] (issue #149):** Tasks 4/5/6/8 load only the
  memory files relevant to this evals/scripts area, matching the plan phase's own selective-load
  convention — not applied literally by this plan's own scripts (they don't load `.archon/memory/`
  at all), but noted so no later task adds an unscoped full-memory-file read.
- **`.archon/memory/codebase-patterns.md` [PATTERN] (issue #250):** any two-dot vs. three-dot
  diff/log comparisons this plan's scripts perform against `main` use the two-dot form
  (`git log main`, not `main...HEAD`) — not directly exercised here since the harness reads merge
  commits by SHA (`merge_boundary_date`) and PR `mergedAt`/`createdAt` timestamps, not branch diffs,
  but noted so no task accidentally introduces a three-dot comparison later.

---

## Task 1: Scenario map + dimension applicability data

**Note on the superseded draft:** the old `docs/superpowers/plans/2026-07-11-skill-modularized-prompt-flow-evaluation.md`
was removed directly on the `refine/issue-48-*` branch during this plan phase (not as a task
here) — `feat/issue-48-*` branches fork fresh from `origin/main` (`workflows/archon-dark-factory.yaml`'s
`setup-branch` step), so that file was never present on `main` and would never exist on the
implementation branch for a `git rm` task to act on. Task 1 below is where implementation starts.

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
    # is the actual merge-PR number (#220/#225/#231) — a field holding the issue number where a
    # caller expects a PR number is a real defect class (found and fixed once already on this
    # ticket's superseded draft), so this is asserted explicitly.
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
    assert sfe.DIMENSION_APPLICABILITY["refine"]["token_count"] == sfe._TIER2_TOKEN_GAP
    assert sfe.DIMENSION_APPLICABILITY["continue"]["token_count"] == sfe._TIER2_TOKEN_GAP


def test_dimension_applicability_tier1_token_dims_disclose_spot_check_only_not_mined():
    # Regression guard: mined PR history cannot supply token/tool-call/runtime for Tier 1 either —
    # only the live spot-check pairs measure it directly. Do not claim "+ mined population" here.
    for dim in ("token_count", "tool_call_count", "runtime"):
        val = sfe.DIMENSION_APPLICABILITY["conformance"][dim]
        assert "spot-check" in val or "toggle-pair" in val
        assert "mined" not in val


def test_dimension_applicability_spec_plan_quality_is_scenario_specific_not_uniform():
    # continue produces no spec/plan (it's implement's continue-intent path) — N/A, not "measured".
    assert sfe.DIMENSION_APPLICABILITY["continue"]["spec_plan_quality"].startswith("N/A")
    assert sfe.DIMENSION_APPLICABILITY["refine"]["spec_plan_quality"].startswith("measured")
    assert sfe.DIMENSION_APPLICABILITY["plan_narrative"]["spec_plan_quality"].startswith("measured")


def test_dimension_applicability_implementation_correctness_scenario_specific():
    assert sfe.DIMENSION_APPLICABILITY["continue"]["implementation_correctness"].startswith("measured")
    assert sfe.DIMENSION_APPLICABILITY["refine"]["implementation_correctness"].startswith("N/A")
    assert sfe.DIMENSION_APPLICABILITY["plan_narrative"]["implementation_correctness"].startswith("N/A")
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
```

4. Run tests, verify pass:

```bash
python -m pytest tests/test_skill_flow_eval.py -v
```

Expected: `9 passed`

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


def test_classify_verdict_ignores_body_none():
    # gh's REST comment objects always have a body string, but be defensive against a None value
    # rather than crashing the mining pass on one malformed comment.
    comments = [{"body": None}, {"body": "## Spec Conformance — Blocked\n..."}]
    assert sfe.classify_conformance_verdict(comments) == "MATERIAL_BLOCKED"
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
    BLOCKED (commands/dark-factory-code-review.md Phase 6) — the issue-comment header is the cheap
    durable signal; PASS-with-advisory is not distinguished from PASS-clean here."""
    for c in comments:
        if _CODE_REVIEW_BLOCKED_RE.search(c.get("body") or ""):
            return "BLOCKED"
    return "PASS"
```

4. Run, verify pass:

```bash
python -m pytest tests/test_skill_flow_eval.py -v -k classify
```

Expected: `6 passed`

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

Expected: 2 failed (`AttributeError: module 'skill_flow_eval' has no attribute 'bucket_prs_by_boundary'`
/ `'merge_boundary_date'`), 1 passed — the same `-k boundary` selection also re-collects Task 1's
already-passing `test_scenario_map_boundary_issue_and_pr_number_are_distinct`, which is unrelated
to this task's new functions.

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

Expected: `3 passed` (the 2 new tests plus Task 1's `test_scenario_map_boundary_issue_and_pr_number_are_distinct`,
which also matches "boundary")

5. Commit:

```bash
git add evals/skill_flow_eval.py tests/test_skill_flow_eval.py
git commit -m "test+feat(skill-flow-eval): before/after boundary bucketing (#48)"
```

---

## Task 4: Tier 1 + Tier 2 self-target population mining

**Files:** `evals/skill_flow_eval.py`, `tests/test_skill_flow_eval.py`

This is the **required** mining pass (`omniscient/dark-factory`, always reachable — we're running
inside it) for both Tier 1 verdict-rate and Tier 2 label-incidence, per spec Requirement #3's
qualitative/causal data-source assignment.

1. Add failing tests:

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


def test_mine_code_review_population_classifies_and_buckets(monkeypatch):
    prs = [{"number": 1, "headRefName": "feat/issue-46-x", "mergedAt": "2026-07-10T18:00:00Z", "state": "MERGED", "commits": [{"authors": [{"email": "factory@dark-factory"}]}], "labels": []}]
    monkeypatch.setattr(sfe.fsc, "FACTORY_EMAIL", "factory@dark-factory")
    monkeypatch.setattr(sfe, "_fetch_issue_comments", lambda repo, num: [{"body": "## Code Review — Blocked\n..."}])

    boundary = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
    result = sfe.mine_code_review_population("omniscient/dark-factory", prs, boundary)
    assert result["after"] == {"n": 1, "blocked": 1}


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
python -m pytest tests/test_skill_flow_eval.py -v -k "mine_conformance_population or mine_code_review_population or mine_label_incidence"
```

Expected: `AttributeError: module 'skill_flow_eval' has no attribute 'mine_conformance_population'`

3. Implement (append):

```python
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
```

4. Run, verify pass:

```bash
python -m pytest tests/test_skill_flow_eval.py -v -k "mine_conformance_population or mine_code_review_population or mine_label_incidence"
```

Expected: `3 passed`

5. Commit:

```bash
git add evals/skill_flow_eval.py tests/test_skill_flow_eval.py
git commit -m "test+feat(skill-flow-eval): self-target Tier 1 + Tier 2 population mining (#48)"
```

---

## Task 5: Cross-repo verdict-rate widening (markethawk, graceful degrade)

**Files:** `evals/skill_flow_eval.py`, `tests/test_skill_flow_eval.py`

Spec Requirement #3 sends quantitative *volume* to `omniscient/markethawk`. Since token/tool-call/
runtime are not mineable from either repo (Task 1's disclosed gap), the only mineable
quantitative-volume signal is the same verdict-rate mining from Task 4, run a second time against
`omniscient/markethawk`, widening N for the conformance/code-review blocked-rate. Per the memory
AVOID entry (issue #48) and spec §Assumptions, the harness must not crash if a second repo is
unreachable (no credentials/network in some environments) — it must record `"population":
"unavailable"` and continue.

1. Add failing tests:

```python
# tests/test_skill_flow_eval.py (append)
def test_mine_cross_repo_population_widens_conformance(monkeypatch):
    dfx_prs = [{"number": 1, "headRefName": "feat/issue-46-x", "mergedAt": "2026-07-10T18:00:00Z", "state": "MERGED", "commits": [{"authors": [{"email": "factory@dark-factory"}]}], "labels": []}]
    mh_prs = [{"number": 5, "headRefName": "feat/issue-9-y", "mergedAt": "2026-07-10T19:00:00Z", "state": "MERGED", "commits": [{"authors": [{"email": "factory@markethawk"}]}], "labels": []}]

    def fake_fetch_prs():
        return mh_prs if sfe.fsc.REPO == "omniscient/markethawk" else dfx_prs

    # Self-target arm: is_factory_pr reads fsc.FACTORY_EMAIL at call time, and
    # mine_cross_repo_verdict_population's self_target mining runs with whatever email is
    # ambient *before* the cross-repo reassignment — set it explicitly per the hermetic-test
    # convention tests/test_fetch_scorecard.py's own header documents (never depend on ambient env).
    # tests/conftest.py scrubs every FACTORY_* env var before any test imports, so fsc.REPO
    # otherwise defaults to fetch_scorecard's own "omniscient/markethawk" default (not
    # dark-factory) — set both explicitly so the "restored after" assertion below is meaningful.
    monkeypatch.setattr(sfe.fsc, "FACTORY_EMAIL", "factory@dark-factory")
    monkeypatch.setattr(sfe.fsc, "REPO", "omniscient/dark-factory")
    monkeypatch.setattr(sfe.fsc, "_OWNER_REPO", "omniscient/dark-factory")
    monkeypatch.setattr(sfe.fsc, "fetch_prs", fake_fetch_prs)
    monkeypatch.setattr(sfe, "_fetch_issue_comments", lambda repo, num: [{"body": "no findings"}])

    boundary = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
    result = sfe.mine_cross_repo_verdict_population(
        "omniscient/dark-factory", dfx_prs, "omniscient/markethawk", boundary
    )

    assert result["self_target"]["conformance"]["after"]["n"] == 1
    assert result["cross_repo"]["conformance"]["after"]["n"] == 1
    assert sfe.fsc.REPO == "omniscient/dark-factory"  # restored after the cross-repo fetch


def test_mine_cross_repo_population_degrades_gracefully_on_failure(monkeypatch):
    dfx_prs = []

    def raising_fetch_prs():
        if sfe.fsc.REPO == "omniscient/markethawk":
            raise RuntimeError("gh: no credentials for omniscient/markethawk")
        return dfx_prs

    monkeypatch.setattr(sfe.fsc, "REPO", "omniscient/dark-factory")
    monkeypatch.setattr(sfe.fsc, "_OWNER_REPO", "omniscient/dark-factory")
    monkeypatch.setattr(sfe.fsc, "fetch_prs", raising_fetch_prs)
    boundary = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)

    result = sfe.mine_cross_repo_verdict_population(
        "omniscient/dark-factory", dfx_prs, "omniscient/markethawk", boundary
    )

    assert result["cross_repo"] == "unavailable"
    assert sfe.fsc.REPO == "omniscient/dark-factory"  # restored even on failure
```

2. Verify fail:

```bash
python -m pytest tests/test_skill_flow_eval.py -v -k mine_cross_repo
```

Expected: `AttributeError: module 'skill_flow_eval' has no attribute 'mine_cross_repo_verdict_population'`

3. Implement (append):

```python
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
```

4. Run, verify pass:

```bash
python -m pytest tests/test_skill_flow_eval.py -v -k mine_cross_repo
```

Expected: `2 passed`

5. Run the full module so far to confirm no regressions:

```bash
python -m pytest tests/test_skill_flow_eval.py -v
```

Expected: all tests passed (22 total)

6. Commit:

```bash
git add evals/skill_flow_eval.py tests/test_skill_flow_eval.py
git commit -m "test+feat(skill-flow-eval): cross-repo verdict-rate widening with graceful degrade (#48)"
```

---

## Task 6: CLI wiring — `main()` / `run()` in `skill_flow_eval.py`

**Files:** `evals/skill_flow_eval.py`, `tests/test_skill_flow_eval.py`

`run()` must reassign `fsc`'s module globals before calling `fsc.fetch_prs()` for the self-target
repo (mirroring `fetch_scorecard.py`'s own `--repo` handling), and must window PRs by `createdAt`
via `fsc.in_window(...)` — an unwindowed fetch silently ignores `--since`/`--until`.

1. Add failing tests:

```python
# tests/test_skill_flow_eval.py (append)
def test_build_arg_parser_defaults():
    parser = sfe.build_arg_parser()
    args = parser.parse_args([])
    assert args.repo == "omniscient/dark-factory"
    assert args.cross_repo == "omniscient/markethawk"
    assert args.output_dir == "evals"


def test_build_arg_parser_overrides():
    parser = sfe.build_arg_parser()
    args = parser.parse_args(["--repo", "omniscient/other", "--output-dir", "/tmp/out"])
    assert args.repo == "omniscient/other"
    assert args.output_dir == "/tmp/out"


def test_run_reassigns_fetch_scorecard_repo_globals_before_fetching(monkeypatch):
    # Regression guard: run() must reassign fsc.REPO/_OWNER_REPO/FACTORY_EMAIL before
    # fetch_prs()/fetch_issues() (which read those globals, not a repo argument) — otherwise
    # --repo silently fetches from fetch_scorecard's own default instead.
    seen_repo_at_fetch = {}

    def fake_fetch_prs():
        seen_repo_at_fetch["repo"] = sfe.fsc.REPO
        seen_repo_at_fetch["owner_repo"] = sfe.fsc._OWNER_REPO
        return []

    monkeypatch.setattr(sfe.fsc, "fetch_prs", fake_fetch_prs)
    monkeypatch.setattr(sfe.fsc, "_git", lambda repo_root, *a: "2026-07-10T11:57:54-04:00\n")

    parser = sfe.build_arg_parser()
    args = parser.parse_args(["--repo", "omniscient/dark-factory", "--no-cross-repo"])
    sfe.run(args)

    assert seen_repo_at_fetch["repo"] == "omniscient/dark-factory"
    assert seen_repo_at_fetch["owner_repo"] == "omniscient/dark-factory"
    assert sfe.fsc.FACTORY_EMAIL == "factory@dark-factory"


def test_run_windows_prs_by_created_at(monkeypatch):
    in_window_prs = [{"number": 1, "createdAt": "2026-07-05T00:00:00Z", "headRefName": "feat/issue-46-x",
                       "mergedAt": "2026-07-05T00:00:00Z", "state": "MERGED",
                       "commits": [{"authors": [{"email": "factory@dark-factory"}]}], "labels": []}]
    out_of_window_prs = [{"number": 2, "createdAt": "2026-06-01T00:00:00Z", "headRefName": "feat/issue-47-y",
                           "mergedAt": "2026-06-01T00:00:00Z", "state": "MERGED",
                           "commits": [{"authors": [{"email": "factory@dark-factory"}]}], "labels": []}]

    monkeypatch.setattr(sfe.fsc, "FACTORY_EMAIL", "factory@dark-factory")
    monkeypatch.setattr(sfe.fsc, "fetch_prs", lambda: in_window_prs + out_of_window_prs)
    monkeypatch.setattr(sfe.fsc, "_git", lambda repo_root, *a: "2026-07-01T00:00:00+00:00\n")
    monkeypatch.setattr(sfe, "_fetch_issue_comments", lambda repo, num: [{"body": "no findings"}])

    parser = sfe.build_arg_parser()
    args = parser.parse_args(["--since", "2026-07-01", "--until", "2026-07-10", "--no-cross-repo"])
    result = sfe.run(args)

    assert result["conformance"]["before"]["n"] + result["conformance"]["after"]["n"] == 1


def test_run_includes_all_six_scenarios(monkeypatch):
    prs = [{"number": 1, "headRefName": "feat/issue-46-x", "createdAt": "2026-07-10T05:00:00Z",
            "mergedAt": "2026-07-10T05:00:00Z", "state": "MERGED",
            "commits": [{"authors": [{"email": "factory@dark-factory"}]}], "labels": []}]
    monkeypatch.setattr(sfe.fsc, "FACTORY_EMAIL", "factory@dark-factory")
    monkeypatch.setattr(sfe.fsc, "fetch_prs", lambda: prs)
    monkeypatch.setattr(sfe.fsc, "_git", lambda repo_root, *a: "2026-07-01T00:00:00+00:00\n")
    monkeypatch.setattr(sfe, "_fetch_issue_comments", lambda repo, num: [{"body": "no findings"}])

    parser = sfe.build_arg_parser()
    args = parser.parse_args(["--since", "2026-07-01", "--no-cross-repo"])
    result = sfe.run(args)

    for scenario in ("refine", "plan_narrative", "continue", "conformance", "code_review"):
        assert scenario in result
    # plan_phase_3_5 shares conformance's population per spec §6 — no separate top-level key.
    assert "plan_phase_3_5" not in result


def test_run_writes_results_json(monkeypatch, tmp_path):
    monkeypatch.setattr(sfe.fsc, "fetch_prs", lambda: [])
    monkeypatch.setattr(sfe.fsc, "_git", lambda repo_root, *a: "2026-07-01T00:00:00+00:00\n")

    parser = sfe.build_arg_parser()
    args = parser.parse_args(["--output-dir", str(tmp_path), "--no-cross-repo"])
    sfe.run(args, write_json=True)

    written = list((tmp_path / "results").glob("skill-flow-population-*.json"))
    assert len(written) == 1
```

2. Verify fail:

```bash
python -m pytest tests/test_skill_flow_eval.py -v -k "build_arg_parser or run_reassigns or run_windows or run_includes or run_writes"
```

Expected: `AttributeError: module 'skill_flow_eval' has no attribute 'build_arg_parser'`

3. Implement (append to `evals/skill_flow_eval.py`):

```python
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
            report[scenario] = mine_label_incidence(windowed_prs, boundary)

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
```

4. Run, verify pass:

```bash
python -m pytest tests/test_skill_flow_eval.py -v -k "build_arg_parser or run_reassigns or run_windows or run_includes or run_writes"
```

Expected: `6 passed`

5. Run the full module to confirm no regressions before moving on:

```bash
python -m pytest tests/test_skill_flow_eval.py -v
```

Expected: all tests passed (28 total)

6. Commit:

```bash
git add evals/skill_flow_eval.py tests/test_skill_flow_eval.py
git commit -m "feat(skill-flow-eval): CLI entrypoint — repo-scoped fetch, date-windowing, JSON output (#48)"
```

---

## Task 7: Scorecard schema + markdown renderer

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
        {"scenario": "conformance", "tier": 1, "mechanism": "toggle A/B", "rollout": "default-on", "confounds": ""},
        {"scenario": "refine", "tier": 2, "mechanism": "before/after #43", "rollout": "advisory-readiness", "confounds": "different issues/complexity"},
    ]
    md = sfs.render_report(rows, generated_at="2026-07-12T00:00:00+00:00")
    assert "# Skill-Modularization Scorecard" in md
    assert "| conformance | 1 |" in md
    assert "| refine | 2 |" in md
    assert "default-on" in md
    assert "advisory-readiness" in md
    assert "different issues/complexity" in md


def test_render_report_footer_credits_script():
    md = sfs.render_report([], generated_at="2026-07-12T00:00:00+00:00")
    assert "evals/skill_flow_eval.py" in md


def test_render_report_out_of_scope_note_present():
    # The operator's 2026-07-12 scope-decision comment on #48 excludes whole-harness economics
    # (that belongs to #240/Epic #234) — the report should say so rather than silently drift scope.
    md = sfs.render_report([], generated_at="2026-07-12T00:00:00+00:00")
    assert "#240" in md
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
        "**Scope:** current-flow vs. skill-modularized prompt flow (token count, tool calls, "
        "runtime, quality/safety, trigger accuracy) only. Whole-harness \"Harness Effect\" "
        "economics is out of scope for this ticket — see omniscient/dark-factory#240 in Epic #234.",
        "",
        "---",
        "",
        "## Scenario → Tier → Mechanism → Rollout",
        "",
        "| Scenario | Tier | Mechanism | Rollout Recommendation | Confounds |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['scenario']} | {row['tier']} | {row['mechanism']} | {row['rollout']} | {row.get('confounds', '')} |"
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

Expected: `3 passed`

5. Commit:

```bash
git add evals/skill_flow_scorecard.py tests/test_skill_flow_scorecard.py
git commit -m "test+feat(skill-flow-scorecard): markdown report renderer (#48)"
```

---

## Task 8: Tier-gated rollout recommendation logic

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


def test_recommend_rollout_handles_zero_denominator_population():
    # An empty before/after bucket (n=0) must not raise ZeroDivisionError upstream — the caller
    # passes rate=0.0 for an empty bucket; recommend_rollout itself just consumes floats and must
    # not special-case NaN/inf.
    rollout = sfs.recommend_rollout(tier=1, before_blocked_rate=0.0, after_blocked_rate=0.0)
    assert rollout == "advisory-only"


def test_blocked_rate_from_population_handles_zero_n():
    assert sfs.blocked_rate({"n": 0, "blocked": 0}) == 0.0
    assert sfs.blocked_rate({"n": 4, "blocked": 1}) == 0.25
```

2. Verify fail:

```bash
python -m pytest tests/test_skill_flow_scorecard.py -v
```

Expected: `AttributeError: module 'skill_flow_scorecard' has no attribute 'recommend_rollout'` (most of the
6 new tests reference `recommend_rollout`/`blocked_rate` directly and fail this way; the file's 3
pre-existing Task 7 tests still pass)

3. Implement (append to `evals/skill_flow_scorecard.py`):

```python
# ── Tier-gated rollout ladder (spec §6, Requirement #6) ───────────────────────
_DEFAULT_ON_IMPROVEMENT = 0.10  # after_rate must drop by >= 10pp vs before to earn default-on
_ADVISORY_FLAT_BAND = 0.05      # within 5pp either way counts as "flat" (advisory-only)


def blocked_rate(population: dict) -> float:
    """population: {'n': int, 'blocked': int} (Task 4's _bucket_verdict_stats shape). n=0 -> 0.0,
    not a ZeroDivisionError — an empty bucket has no signal, not a 0% blocked rate claim, but
    recommend_rollout treats 'no signal' the same as 'flat' (advisory-only), which is the
    conservative default when a bucket is empty."""
    n = population.get("n", 0)
    return population.get("blocked", 0) / n if n else 0.0


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
python -m pytest tests/test_skill_flow_scorecard.py -v
```

Expected: `9 passed` (3 from Task 7 + 6 new)

5. Commit:

```bash
git add evals/skill_flow_scorecard.py tests/test_skill_flow_scorecard.py
git commit -m "test+feat(skill-flow-scorecard): tier-gated rollout recommendation ladder (#48)"
```

---

## Task 9: Build scorecard rows from mined population + spot-check results

**Files:** `evals/skill_flow_scorecard.py`, `tests/test_skill_flow_scorecard.py`

1. Add failing test:

```python
# tests/test_skill_flow_scorecard.py (append)
def test_build_rows_folds_plan_phase_3_5_into_conformance():
    population = {
        "conformance": {"before": {"n": 10, "blocked": 3}, "after": {"n": 10, "blocked": 1}},
        "code_review": {"before": {"n": 10, "blocked": 2}, "after": {"n": 10, "blocked": 2}},
        "refine": {"before": {"n": 5, "factory_regression": 2, "scope_spillover": 0, "needs_discussion": 1},
                   "after": {"n": 5, "factory_regression": 0, "scope_spillover": 0, "needs_discussion": 0}},
        "plan_narrative": {"before": {"n": 5, "factory_regression": 0, "scope_spillover": 0, "needs_discussion": 0},
                            "after": {"n": 5, "factory_regression": 0, "scope_spillover": 0, "needs_discussion": 0}},
        "continue": {"before": {"n": 5, "factory_regression": 0, "scope_spillover": 0, "needs_discussion": 0},
                     "after": {"n": 5, "factory_regression": 0, "scope_spillover": 0, "needs_discussion": 0}},
    }
    rows = sfs.build_rows(population)
    scenarios = {r["scenario"] for r in rows}
    assert "plan_phase_3_5" not in scenarios  # folded into conformance per spec §6
    assert "conformance" in scenarios

    conformance_row = next(r for r in rows if r["scenario"] == "conformance")
    assert conformance_row["tier"] == 1
    assert conformance_row["rollout"] == "default-on"  # 30% -> 10% is a >=10pp improvement

    refine_row = next(r for r in rows if r["scenario"] == "refine")
    assert refine_row["tier"] == 2
    assert refine_row["rollout"] == "advisory-readiness"
    assert "confounded" in refine_row["confounds"].lower()
```

2. Verify fail:

```bash
python -m pytest tests/test_skill_flow_scorecard.py -v -k build_rows
```

Expected: `AttributeError: module 'skill_flow_scorecard' has no attribute 'build_rows'`

3. Implement (append to `evals/skill_flow_scorecard.py`):

```python
# ── Assemble scorecard rows from a skill_flow_eval.run() population report ────
_TIER1_MECHANISM = "toggle A/B on same issue/diff (live spot-check) + mined verdict-rate before/after boundary"
_TIER2_MECHANISM = "before/after merge-boundary commit, mined label incidence (confounded — see Confounds)"
_TIER2_CONFOUND_NOTE = (
    "Observational, confounded: different issues, complexity, and unrelated intervening commits "
    "landed in the same before/after window; see evals/factory-failures.jsonl for known one-off "
    "incidents that could skew a bucket."
)


def build_rows(population: dict) -> list[dict]:
    """population is skill_flow_eval.run()'s report dict (self-target only; cross_repo_widening,
    if present, is not consumed here — it only widens the N used upstream in a future iteration,
    not the per-scenario row shape). plan_phase_3_5 is folded into conformance's row per spec §6."""
    rows = []
    for scenario in ("conformance", "code_review"):
        pop = population[scenario]
        before_rate = blocked_rate(pop["before"])
        after_rate = blocked_rate(pop["after"])
        rows.append({
            "scenario": scenario,
            "tier": 1,
            "mechanism": _TIER1_MECHANISM,
            "rollout": recommend_rollout(1, before_rate, after_rate),
            "confounds": "",
        })
    for scenario in ("refine", "plan_narrative", "continue"):
        pop = population[scenario]
        rows.append({
            "scenario": scenario,
            "tier": 2,
            "mechanism": _TIER2_MECHANISM,
            "rollout": recommend_rollout(2, 0.0, 0.0),  # Tier 2 always advisory-readiness regardless of rates
            "confounds": _TIER2_CONFOUND_NOTE,
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

Expected: all tests passed (10 total)

6. Commit:

```bash
git add evals/skill_flow_scorecard.py tests/test_skill_flow_scorecard.py
git commit -m "test+feat(skill-flow-scorecard): build_rows folds plan_phase_3_5 into conformance (#48)"
```

---

## Task 10: Tier 1 live spot-check manifest

**Files:** `evals/skill_flow_spotchecks.json` (new), `tests/test_skill_flow_eval.py`

4 real, already-closed, already-merged dark-factory PRs, all merged after the #44 RUBRIC-toggle
boundary (`f72738f8...`, 2026-07-10T11:57:54-04:00, i.e. 2026-07-10T15:57:54Z) so
`.claude/skills/{conformance,code-review}/RUBRIC.md` exists on `main` at each pair's diff time and
can be meaningfully forced-absent for the "current-flow" arm. All four were verified live via `gh
pr view` during planning (merge SHAs and merge timestamps confirmed against real GitHub state, not
carried over from the superseded draft without re-checking).

1. Write the manifest:

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
git commit -m "test+feat(skill-flow-eval): Tier 1 live spot-check manifest, 4 verified pairs (#48)"
```

---

## Task 11: Tier 1 live spot-check runner (prompt rendering + verdict extraction)

**Files:** `evals/skill_flow_spotcheck.py` (new), `tests/test_skill_flow_spotcheck.py` (new)

This is a genuinely fresh mechanism vs. the superseded draft's bash runner: `subprocess.run` with
an argv list and `input=` (stdin), the exact pattern `scripts/factory_core/main_red_fixer.py`'s
`_run()` already establishes for `claude -p` calls in this codebase — no shell string
interpolation, so the entire bug class the superseded draft's 3 architect cycles found (a
`pipefail` abort, untrusted-output-into-source-string injection, an unset `$FACTORY_REPO_SLUG`
under `set -u`) cannot recur here by construction.

1. Write failing tests:

```python
# tests/test_skill_flow_spotcheck.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import skill_flow_spotcheck as sfsc  # noqa: E402


def test_render_conformance_prompt_substitutes_real_rubric_placeholders():
    rubric = "Kind: $ARTIFACT_KIND\nSpec:\n$SPEC_CONTENT\nArtifact:\n$ARTIFACT_CONTENT"
    prompt = sfsc.render_conformance_prompt(
        rubric, artifact_kind="IMPLEMENTATION", spec_content="the spec", artifact_content="the diff"
    )
    assert prompt == "Kind: IMPLEMENTATION\nSpec:\nthe spec\nArtifact:\nthe diff"


def test_render_code_review_prompt_substitutes_real_rubric_placeholders():
    rubric = "Issue:\n$ISSUE_CONTEXT\nDiff:\n$DIFF_CONTENT"
    prompt = sfsc.render_code_review_prompt(rubric, issue_context="issue body", diff_content="the diff")
    assert prompt == "Issue:\nissue body\nDiff:\nthe diff"


def test_extract_conformance_verdict_parses_material():
    text = "## Spec Conformance — Implementation\n\n**Verdict:** ⛔ Material divergence\n**Spec:** foo\n"
    assert sfsc.extract_conformance_verdict(text) == "MATERIAL_BLOCKED"


def test_extract_conformance_verdict_parses_conforms():
    text = "## Spec Conformance — Implementation\n\n**Verdict:** ✅ Conforms\n**Spec:** foo\n"
    assert sfsc.extract_conformance_verdict(text) == "CONFORMS_OR_MINOR"


def test_extract_conformance_verdict_missing_verdict_line_is_unparseable():
    # A response that doesn't follow the RUBRIC's required output format must not be silently
    # counted as a pass — it's a distinct outcome from a real CONFORMS verdict.
    assert sfsc.extract_conformance_verdict("no verdict line here") == "UNPARSEABLE"


def test_extract_code_review_verdict_blocked_on_high_severity():
    text = "| 1 | high | security | x.py:1 | sql injection |\n| 2 | low | naming | y.py:2 | rename |"
    assert sfsc.extract_code_review_verdict(text) == "BLOCKED"


def test_extract_code_review_verdict_pass_when_no_high_or_critical():
    text = "| 1 | low | naming | y.py:2 | rename |\n| 2 | medium | edge-case | z.py:3 | guard missing |"
    assert sfsc.extract_code_review_verdict(text) == "PASS"


def test_extract_code_review_verdict_pass_on_no_findings():
    assert sfsc.extract_code_review_verdict("No findings.") == "PASS"
```

2. Verify fail:

```bash
python -m pytest tests/test_skill_flow_spotcheck.py -v
```

Expected: `ModuleNotFoundError: No module named 'skill_flow_spotcheck'`

3. Implement `evals/skill_flow_spotcheck.py` (prompt rendering + verdict extraction only for this
   task; the subprocess runner and CLI land in Task 12):

```python
"""Tier 1 live toggle spot-check runner — issue #48.

For each pair in evals/skill_flow_spotchecks.json, calls `claude -p` twice per gate (RUBRIC.md
present vs. forced-absent) with the real conformance/code-review RUBRIC content and real
issue/diff context, and records the parsed verdict + real API usage (token count, duration, cost)
from `claude -p --output-format json`. Uses subprocess.run with an argv list and stdin, the same
safe pattern scripts/factory_core/main_red_fixer.py's _run() already uses for claude -p calls in
this codebase — no shell string interpolation.
"""
from __future__ import annotations

import re
import string
import subprocess

# ── Prompt rendering (real RUBRIC.md placeholders — see .claude/skills/{conformance,code-review}/RUBRIC.md) ──
class _SafeTemplate(string.Template):
    delimiter = "$"


def render_conformance_prompt(rubric_text: str, artifact_kind: str, spec_content: str, artifact_content: str) -> str:
    """Substitutes the real placeholders documented in .claude/skills/conformance/RUBRIC.md:
    $ARTIFACT_KIND, $SPEC_CONTENT, $ARTIFACT_CONTENT."""
    return _SafeTemplate(rubric_text).safe_substitute(
        ARTIFACT_KIND=artifact_kind, SPEC_CONTENT=spec_content, ARTIFACT_CONTENT=artifact_content
    )


def render_code_review_prompt(rubric_text: str, issue_context: str, diff_content: str) -> str:
    """Substitutes the real placeholders documented in .claude/skills/code-review/RUBRIC.md:
    $ISSUE_CONTEXT, $DIFF_CONTENT."""
    return _SafeTemplate(rubric_text).safe_substitute(ISSUE_CONTEXT=issue_context, DIFF_CONTENT=diff_content)


# ── Verdict extraction from the reviewer's own structured output ─────────────
_VERDICT_LINE_RE = re.compile(r"\*\*Verdict:\*\*\s*(.+)")
_FINDINGS_ROW_RE = re.compile(r"^\|\s*\d+\s*\|\s*(critical|high|medium|low)\s*\|", re.IGNORECASE | re.MULTILINE)
_BLOCKING_SEVERITIES = {"critical", "high"}


def extract_conformance_verdict(response_text: str) -> str:
    """Parses the **Verdict:** line the conformance RUBRIC's Output Format section mandates
    (.claude/skills/conformance/RUBRIC.md). Returns UNPARSEABLE (not a silent PASS) if the
    response didn't follow the required format — distinguishing 'reviewer said CONFORMS' from
    'reviewer's output couldn't be parsed' matters for verdict-rate accuracy."""
    m = _VERDICT_LINE_RE.search(response_text)
    if not m:
        return "UNPARSEABLE"
    verdict_line = m.group(1)
    if "Material divergence" in verdict_line or "⛔" in verdict_line:
        return "MATERIAL_BLOCKED"
    return "CONFORMS_OR_MINOR"


def extract_code_review_verdict(response_text: str) -> str:
    """Parses the pipe-delimited findings table the code-review RUBRIC's Output Format section
    mandates (.claude/skills/code-review/RUBRIC.md) — critical/high block, medium/low are
    advisory-only (RUBRIC.md 'Severity' section)."""
    for m in _FINDINGS_ROW_RE.finditer(response_text):
        if m.group(1).lower() in _BLOCKING_SEVERITIES:
            return "BLOCKED"
    return "PASS"
```

4. Run, verify pass:

```bash
python -m pytest tests/test_skill_flow_spotcheck.py -v
```

Expected: `8 passed`

5. Commit:

```bash
git add evals/skill_flow_spotcheck.py tests/test_skill_flow_spotcheck.py
git commit -m "test+feat(skill-flow-spotcheck): RUBRIC prompt rendering + verdict extraction (#48)"
```

---

## Task 12: Tier 1 live spot-check runner — subprocess execution + budget cap + CLI

**Files:** `evals/skill_flow_spotcheck.py`, `tests/test_skill_flow_spotcheck.py`

Mirrors `bench/run_suite.sh`'s conventions (soft dollar-budget cap via an env var, `--dry-run`
mode, results written under `evals/results/`) rather than inventing new ones, per the operator's
"keep the eval harness within the existing evals/ + bench/run_suite.sh machinery" note. Real
per-call token/cost/duration comes from `claude -p --output-format json`'s `usage`/`total_cost_usd`/
`duration_ms` fields (verified live against the actual CLI during planning — see plan Architecture
note on Task 11/12).

1. Add failing tests:

```python
# tests/test_skill_flow_spotcheck.py (append)
import json
import subprocess


def test_run_one_arm_parses_usage_from_output_json(monkeypatch):
    fake_response = json.dumps({
        "type": "result", "is_error": False, "result": "**Verdict:** ✅ Conforms\n",
        "duration_ms": 4200, "total_cost_usd": 0.0123,
        "usage": {"input_tokens": 5000, "output_tokens": 300},
    })

    def fake_run(cmd, input=None, capture_output=True, text=True, timeout=None):
        assert cmd[:2] == ["claude", "-p"]
        assert "--output-format" in cmd and "json" in cmd
        assert input is not None  # prompt goes via stdin, never interpolated into cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=fake_response, stderr="")

    monkeypatch.setattr(sfsc.subprocess, "run", fake_run)

    result = sfsc.run_one_arm(prompt="the prompt", gate="conformance", model="claude-opus-4-8")

    assert result["verdict"] == "CONFORMS_OR_MINOR"
    assert result["input_tokens"] == 5000
    assert result["output_tokens"] == 300
    assert result["duration_ms"] == 4200
    assert result["cost_usd"] == 0.0123
    assert result["error"] is None


def test_run_one_arm_records_error_without_raising(monkeypatch):
    def fake_run(cmd, input=None, capture_output=True, text=True, timeout=None):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({
            "type": "result", "is_error": True, "result": "Not logged in", "duration_ms": 100,
            "total_cost_usd": 0, "usage": {"input_tokens": 0, "output_tokens": 0},
        }), stderr="")

    monkeypatch.setattr(sfsc.subprocess, "run", fake_run)
    result = sfsc.run_one_arm(prompt="p", gate="conformance", model="claude-opus-4-8")
    assert result["error"] == "Not logged in"
    assert result["verdict"] == "UNPARSEABLE"


def test_run_one_arm_handles_non_json_stdout_without_raising(monkeypatch):
    def fake_run(cmd, input=None, capture_output=True, text=True, timeout=None):
        return subprocess.CompletedProcess(cmd, 1, stdout="not json", stderr="boom")

    monkeypatch.setattr(sfsc.subprocess, "run", fake_run)
    result = sfsc.run_one_arm(prompt="p", gate="conformance", model="claude-opus-4-8")
    assert result["error"] is not None
    assert result["verdict"] == "UNPARSEABLE"


def test_budget_tracker_stops_after_cap():
    tracker = sfsc.BudgetTracker(cap_usd=1.00)
    assert tracker.check(0.40) is True   # 0.40 <= 1.00, ok
    assert tracker.check(0.55) is True   # 0.95 <= 1.00, ok
    assert tracker.check(0.10) is False  # 1.05 > 1.00, over cap


def test_build_spotcheck_arg_parser_defaults():
    parser = sfsc.build_arg_parser()
    args = parser.parse_args([])
    assert args.manifest == "evals/skill_flow_spotchecks.json"
    assert args.budget_usd == 5.00
    assert args.dry_run is False


def test_dry_run_prints_plan_without_calling_subprocess(monkeypatch, capsys):
    called = {"n": 0}

    def fake_run(*a, **k):
        called["n"] += 1
        raise AssertionError("must not be called in --dry-run")

    monkeypatch.setattr(sfsc.subprocess, "run", fake_run)
    manifest = {"pairs": [{"issue": 46, "pr": 229, "merge_sha": "abc123", "title": "t"}]}
    sfsc.run_spotcheck(manifest, dry_run=True, budget_usd=5.00, model="claude-opus-4-8", repo_root=".")
    out = capsys.readouterr().out
    assert called["n"] == 0
    assert "#46" in out
```

2. Verify fail:

```bash
python -m pytest tests/test_skill_flow_spotcheck.py -v -k "run_one_arm or budget_tracker or spotcheck_arg_parser or dry_run"
```

Expected: `AttributeError: module 'skill_flow_spotcheck' has no attribute 'run_one_arm'`

3. Implement (append to `evals/skill_flow_spotcheck.py`):

```python
# ── Subprocess execution (argv + stdin, never shell-interpolated) ────────────
import json as _json
import os
import sys


def run_one_arm(prompt: str, gate: str, model: str, timeout: int = 300) -> dict:
    """One claude -p completion, no tools granted (the reviewer only needs the supplied text —
    matches how commands/dark-factory-conformance.md / dark-factory-code-review.md dispatch a
    reviewer subagent via the Agent tool with content already substituted). Never raises: a
    subprocess or JSON-parse failure is recorded in result['error'] with verdict UNPARSEABLE, so
    one bad pair can't abort the whole spot-check run."""
    cmd = ["claude", "-p", "--model", model, "--output-format", "json"]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout)
        payload = _json.loads(proc.stdout)
    except Exception as e:  # noqa: BLE001 — covers TimeoutExpired/JSONDecodeError/anything else; one bad pair must not abort the run
        return {
            "verdict": "UNPARSEABLE", "input_tokens": 0, "output_tokens": 0,
            "duration_ms": 0, "cost_usd": 0.0, "error": str(e),
        }

    if payload.get("is_error"):
        return {
            "verdict": "UNPARSEABLE", "input_tokens": payload.get("usage", {}).get("input_tokens", 0),
            "output_tokens": payload.get("usage", {}).get("output_tokens", 0),
            "duration_ms": payload.get("duration_ms", 0), "cost_usd": payload.get("total_cost_usd", 0.0),
            "error": payload.get("result", "unknown error"),
        }

    response_text = payload.get("result", "")
    extract = extract_conformance_verdict if gate == "conformance" else extract_code_review_verdict
    return {
        "verdict": extract(response_text),
        "input_tokens": payload.get("usage", {}).get("input_tokens", 0),
        "output_tokens": payload.get("usage", {}).get("output_tokens", 0),
        "duration_ms": payload.get("duration_ms", 0),
        "cost_usd": payload.get("total_cost_usd", 0.0),
        "error": None,
    }


class BudgetTracker:
    """Soft dollar-budget cap, mirroring bench/run_suite.sh's BENCH_TOKEN_BUDGET convention."""

    def __init__(self, cap_usd: float):
        self.cap_usd = cap_usd
        self.spent_usd = 0.0

    def check(self, additional_usd: float) -> bool:
        self.spent_usd += additional_usd
        return self.spent_usd <= self.cap_usd


# ── Real issue/diff context fetching (gh/git via argv, no shell) ─────────────
def fetch_pair_context(repo_root: str, issue: int, merge_sha: str, repo: str) -> dict:
    issue_json = subprocess.run(
        ["gh", "issue", "view", str(issue), "--repo", repo, "--json", "title,body"],
        capture_output=True, text=True,
    )
    issue_context = issue_json.stdout if issue_json.returncode == 0 else ""
    diff = subprocess.run(
        ["git", "-C", repo_root, "diff", f"{merge_sha}^1", f"{merge_sha}^2"],
        capture_output=True, text=True,
    )
    # A squash-merged PR's merge commit may not have a second parent (^2 doesn't exist) — fall
    # back to a single-parent diff rather than aborting the pair.
    diff_content = diff.stdout if diff.returncode == 0 else subprocess.run(
        ["git", "-C", repo_root, "show", merge_sha],
        capture_output=True, text=True,
    ).stdout
    return {"issue_context": issue_context, "diff_content": diff_content[:200_000]}


# ── CLI ────────────────────────────────────────────────────────────────────────
def build_arg_parser():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="evals/skill_flow_spotchecks.json")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--repo", default=None, help="default: `gh repo view` in --repo-root")
    parser.add_argument("--output-dir", default="evals")
    parser.add_argument("--budget-usd", type=float, default=5.00)
    parser.add_argument("--model", default="claude-opus-4-8")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _resolve_repo(repo_root: str) -> str:
    r = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        cwd=repo_root, capture_output=True, text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else "omniscient/dark-factory"


_GATES = {
    # (clone-live RUBRIC path, baked-fallback path, prompt renderer) — mirrors the real resolution
    # order commands/dark-factory-conformance.md / dark-factory-code-review.md use: clone-live
    # first, falling back to the baked /opt/refinement-skills/*-reviewer-prompt.md copy only if the
    # clone-live file is absent (spec §2 step 2). The "current_flow" arm below forces that baked
    # fallback rather than an empty prompt, so the toggle is a genuine present-vs-baked contrast.
    "conformance": (".claude/skills/conformance/RUBRIC.md", "/opt/refinement-skills/conformance-reviewer-prompt.md", render_conformance_prompt),
    "code_review": (".claude/skills/code-review/RUBRIC.md", "/opt/refinement-skills/code-review-reviewer-prompt.md", render_code_review_prompt),
}


def _read_if_exists(path: str) -> str:
    return open(path, encoding="utf-8").read() if os.path.exists(path) else ""


def run_spotcheck(manifest: dict, dry_run: bool, budget_usd: float, model: str, repo_root: str, repo: str | None = None) -> dict:
    pairs = manifest["pairs"]
    if dry_run:
        print(f"=== DRY RUN — {len(pairs)} pairs, budget=${budget_usd:.2f}, model={model} ===")
        for p in pairs:
            print(f"  issue #{p['issue']} / pr #{p['pr']} — {p['title'][:60]} ({p['merge_sha'][:8]})")
        return {"dry_run": True, "pairs": [p["issue"] for p in pairs]}

    resolved_repo = repo or _resolve_repo(repo_root)
    tracker = BudgetTracker(budget_usd)
    results = []
    for pair in pairs:
        ctx = fetch_pair_context(repo_root, pair["issue"], pair["merge_sha"], resolved_repo)
        for gate, (rubric_path, baked_path, render) in _GATES.items():
            rubric_text = _read_if_exists(os.path.join(repo_root, rubric_path))
            baked_text = _read_if_exists(baked_path)
            for arm, active_rubric in (("skill_modularized", rubric_text), ("current_flow", baked_text)):
                if not tracker.check(0.0):  # cap already exceeded by a prior call this run
                    print(f"[skill-flow-spotcheck] budget cap (${budget_usd:.2f}) reached — skipping remaining pairs", file=sys.stderr)
                    break
                if gate == "conformance":
                    prompt = render(active_rubric, artifact_kind="IMPLEMENTATION", spec_content="", artifact_content=ctx["diff_content"])
                else:
                    prompt = render(active_rubric, issue_context=ctx["issue_context"], diff_content=ctx["diff_content"])
                arm_result = run_one_arm(prompt, gate=gate, model=model)
                tracker.check(arm_result["cost_usd"])
                results.append({"issue": pair["issue"], "pr": pair["pr"], "gate": gate, "arm": arm, **arm_result})

    return {"repo": resolved_repo, "budget_usd": budget_usd, "spent_usd": tracker.spent_usd, "results": results}


def main() -> None:
    args = build_arg_parser().parse_args()
    manifest = _json.loads(open(args.manifest, encoding="utf-8").read())
    output = run_spotcheck(manifest, args.dry_run, args.budget_usd, args.model, args.repo_root, args.repo)
    if not args.dry_run:
        from datetime import datetime, timezone

        os.makedirs(os.path.join(args.output_dir, "results"), exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out_path = os.path.join(args.output_dir, "results", f"skill-flow-spotcheck-{date_str}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            _json.dump(output, f, indent=2)
        print(f"Wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
```

4. Run, verify pass:

```bash
python -m pytest tests/test_skill_flow_spotcheck.py -v
```

Expected: `14 passed`

5. Commit:

```bash
git add evals/skill_flow_spotcheck.py tests/test_skill_flow_spotcheck.py
git commit -m "feat(skill-flow-spotcheck): budget-capped subprocess runner + dry-run CLI (#48)"
```

---

## Task 13: End-to-end scorecard generation, commit the report

**Files:** `evals/reports/skill-modularization-scorecard-2026-07-12.md` (new, committed),
`evals/results/skill-flow-population-dark-factory-2026-07-12.json` (new, gitignored),
`evals/results/skill-flow-spotcheck-2026-07-12.json` (new, gitignored)

This task runs the harness for real against `omniscient/dark-factory` (the implement-phase agent
executing this task has `gh`/`git` access to this repo) and commits only the rendered report, per
`evals/.gitignore`'s existing `results/` exclusion.

1. Run the population mining CLI:

```bash
python evals/skill_flow_eval.py --repo omniscient/dark-factory --since 2026-05-01 --output-dir evals
```

Expected: `Wrote evals/results/skill-flow-population-dark-factory-2026-07-12.json` on stderr, exit 0.
If `--no-cross-repo` was not passed and `omniscient/markethawk` is unreachable in this environment,
the JSON's `cross_repo_widening.cross_repo` key is `"unavailable"` — this is expected, not a
failure (see Task 5).

2. Run the live spot-check (budget-capped, real `claude -p` calls):

```bash
python evals/skill_flow_spotcheck.py --manifest evals/skill_flow_spotchecks.json --repo-root . --budget-usd 5.00 --output-dir evals
```

Expected: `Wrote evals/results/skill-flow-spotcheck-2026-07-12.json` on stderr, exit 0. If the
environment has no `ANTHROPIC_API_KEY`/login (as observed during planning — `is_error: true,
"Not logged in"`), every pair records `error` and `verdict: "UNPARSEABLE"` rather than crashing;
the scorecard's Tier 1 section must then say so explicitly (step 4) rather than reporting a
fabricated verdict-rate delta.

3. Write a small script-free assembly step to build the final markdown (this is the only place in
   this task that isn't itself a `.py` module — a short inline `python3 -c` matching the
   `bench/run_suite.sh` convention of using `python3 -c` for one-off JSON→text glue):

```bash
python3 -c "
import json, sys
sys.path.insert(0, 'evals')
import skill_flow_scorecard as sfs

population = json.load(open('evals/results/skill-flow-population-dark-factory-2026-07-12.json'))
rows = sfs.build_rows(population)
md = sfs.render_report(rows, generated_at='2026-07-12T00:00:00+00:00')

# Cross-repo widening (Task 5/6) is corroboration, deliberately NOT blended into build_rows'
# self-target blocked-rate — per the architecture.md memory AVOID entry (issue #48), a single
# blended pool across repos understates one side or the other. Surface it as its own section
# instead of leaving collected data unused in the final deliverable.
widening = population.get('cross_repo_widening', {})
cross_repo_pop = widening.get('cross_repo')
if cross_repo_pop and cross_repo_pop != 'unavailable':
    md += '\n## Cross-Repo Verdict-Rate Widening (corroboration only, not blended)\n\n'
    md += '| Repo | Gate | Before n/blocked | After n/blocked |\n|---|---|---|---|\n'
    for gate in ('conformance', 'code_review'):
        b, a = cross_repo_pop[gate]['before'], cross_repo_pop[gate]['after']
        md += f\"| omniscient/markethawk | {gate} | {b['n']}/{b['blocked']} | {a['n']}/{a['blocked']} |\n\"
else:
    md += '\n## Cross-Repo Verdict-Rate Widening (corroboration only, not blended)\n\n'
    md += '_Unavailable in this run (no network/credentials for omniscient/markethawk) — self-target-only figures above._\n'

spotcheck = json.load(open('evals/results/skill-flow-spotcheck-2026-07-12.json'))
unusable = spotcheck.get('dry_run') or all(r.get('error') for r in spotcheck.get('results', []))
note = (
    '\n## Tier 1 Live Spot-Check\n\n'
    + ('**Not usable this run** — every spot-check call errored (see evals/results/skill-flow-spotcheck-2026-07-12.json '
       'for per-pair errors, e.g. missing ANTHROPIC_API_KEY/login in this environment). The Tier 1 rollout rows above '
       'rely on mined verdict-rate history only for this generation; re-run evals/skill_flow_spotcheck.py in an '
       'authenticated environment for the causal toggle-pair confirmation this ticket calls for.\n'
       if unusable else
       f\"Spent \${spotcheck['spent_usd']:.2f} of \${spotcheck['budget_usd']:.2f} budget across {len(spotcheck['results'])} arm-runs.\n\")
)
open('evals/reports/skill-modularization-scorecard-2026-07-12.md', 'w').write(md + note)
print('Wrote evals/reports/skill-modularization-scorecard-2026-07-12.md')
"
```

Expected: `Wrote evals/reports/skill-modularization-scorecard-2026-07-12.md`

4. Assert the placeholder-free guard — the report must not still contain an unresolved marker:

```bash
grep -c "TBD\|TODO\|<.*>" evals/reports/skill-modularization-scorecard-2026-07-12.md || true
```

Expected: `0` (no unresolved placeholders in the committed report). If non-zero, fix
`skill_flow_scorecard.render_report` or the assembly step above before continuing — a skipped
fill-in must not ship silently.

5. Run the full test suite for this ticket's new modules one final time:

```bash
python -m pytest tests/test_skill_flow_eval.py tests/test_skill_flow_scorecard.py tests/test_skill_flow_spotcheck.py -v
```

Expected: all tests passed (53 total across the three new test files: 29 in test_skill_flow_eval.py,
10 in test_skill_flow_scorecard.py, 14 in test_skill_flow_spotcheck.py)

6. Commit the report only (results/ JSON stays gitignored per `evals/.gitignore`):

```bash
git add evals/reports/skill-modularization-scorecard-2026-07-12.md
git commit -m "docs(skill-flow-eval): committed scorecard + rollout recommendation for #48"
```
