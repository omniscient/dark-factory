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
