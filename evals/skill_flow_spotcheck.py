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
