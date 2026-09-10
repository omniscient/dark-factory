import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pytest

from factory_core.prompt_render import render, PromptRenderError, _segments, _TOKEN_RE

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_single_pass_prevents_chained_splice():
    # #394's exact failure shape: the spec text itself quotes the later placeholder.
    template = "### Spec\n$SPEC_CONTENT\n\n### Artifact\n$ARTIFACT_CONTENT\n"
    spec_text = "The bug occurs when a spec quotes $ARTIFACT_CONTENT in prose."
    artifact_text = "diff --git a/x.py b/x.py\n+ real artifact content"
    result = render(template, {"SPEC_CONTENT": spec_text, "ARTIFACT_CONTENT": artifact_text})
    assert result.count(spec_text) == 1
    assert result.count(artifact_text) == 1


def test_backtick_quoted_legend_untouched_bare_slot_substituted():
    # Mirrors the real RUBRIC.md files' `## Input` legend shape.
    template = (
        "## Input\n"
        "- `$SPEC_CONTENT`: the full spec text\n"
        "- `$ARTIFACT_CONTENT`: the artifact text\n\n"
        "### Spec\n$SPEC_CONTENT\n\n### Artifact\n$ARTIFACT_CONTENT\n"
    )
    result = render(template, {"SPEC_CONTENT": "SPEC-BODY", "ARTIFACT_CONTENT": "ARTIFACT-BODY"})
    assert "- `$SPEC_CONTENT`: the full spec text" in result
    assert "- `$ARTIFACT_CONTENT`: the artifact text" in result
    assert "### Spec\nSPEC-BODY" in result
    assert "### Artifact\nARTIFACT-BODY" in result


def test_single_pass_prevents_chained_splice_brace_delimiter():
    template = "Findings:\n{FINDINGS_TEXT}\n\nDiff:\n{DIFF_CONTENT}\n"
    findings_text = "The diff below quotes {DIFF_CONTENT} verbatim."
    diff_text = "+ real diff line"
    result = render(
        template, {"FINDINGS_TEXT": findings_text, "DIFF_CONTENT": diff_text}, delimiter="brace"
    )
    assert result.count(findings_text) == 1
    assert result.count(diff_text) == 1


def test_backtick_quoted_legend_untouched_bare_slot_substituted_brace_delimiter():
    template = (
        "## Input\n- `{FINDINGS_TEXT}`: the findings text\n\nFindings:\n{FINDINGS_TEXT}\n"
    )
    result = render(template, {"FINDINGS_TEXT": "FINDINGS-BODY"}, delimiter="brace")
    assert "- `{FINDINGS_TEXT}`: the findings text" in result
    assert "Findings:\nFINDINGS-BODY" in result


def test_missing_set_key_raises():
    template = "$SPEC_CONTENT and $ARTIFACT_CONTENT"
    with pytest.raises(PromptRenderError):
        render(template, {"SPEC_CONTENT": "x"})


def test_unused_set_key_raises():
    template = "$SPEC_CONTENT"
    with pytest.raises(PromptRenderError):
        render(template, {"SPEC_CONTENT": "x", "ARTIFACT_CONTENT": "y"})


def test_render_order_independent_of_dict_iteration():
    template = "$A / $B / $C"
    values_1 = {"A": "1", "B": "2", "C": "3"}
    values_2 = {"C": "3", "A": "1", "B": "2"}
    assert render(template, values_1) == render(template, values_2) == "1 / 2 / 3"


# --- Requirement 11: drift guard pinning each template's bare-slot set ---
# (site 5's embedded {}-delimiter chunk is guarded in test_revise_advisory_command_render_prompt.py
# — Task 7 — since it doesn't exist as a standalone file until that command-file edit lands.)
EXPECTED_BARE_SLOTS = [
    (REPO_ROOT / ".claude/skills/conformance/RUBRIC.md", "dollar",
     {"ARTIFACT_KIND", "SPEC_CONTENT", "ARTIFACT_CONTENT"}),
    (REPO_ROOT / ".claude/skills/code-review/RUBRIC.md", "dollar",
     {"ISSUE_CONTEXT", "DIFF_CONTENT"}),
    (REPO_ROOT / "refinement-skills/architect-prompt.md", "dollar",
     {"SPEC_CONTENT", "PLAN_CONTENT"}),
    (REPO_ROOT / "refinement-skills/product-owner-prompt.md", "dollar",
     {"ISSUE_CONTEXT", "QA_HISTORY", "QUESTION"}),
]


def _bare_names(text, delimiter):
    token_re = _TOKEN_RE[delimiter]
    names = set()
    for segment_text, protected in _segments(text):
        if not protected:
            names.update(m.group(1) for m in token_re.finditer(segment_text))
    return names


@pytest.mark.parametrize("path,delimiter,expected", EXPECTED_BARE_SLOTS)
def test_real_template_bare_slot_sets_match_expected(path, delimiter, expected):
    assert _bare_names(path.read_text(encoding="utf-8"), delimiter) == expected
