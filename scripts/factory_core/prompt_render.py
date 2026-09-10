"""Single-pass, backtick-excluding $NAME / {NAME} substitution for reviewer/architect/
product-owner/fix-agent prompt templates (#400).

Chained independent substitution -- fill $SPEC_CONTENT, then separately fill $ARTIFACT_CONTENT
into the already-spec-inlined result -- re-scans the spec text the first pass just inserted; if
the spec quotes $ARTIFACT_CONTENT in prose, the second pass matches inside it too and splices the
artifact into the middle of the spec (#394). A single combined-pass substitution over the
*original* template text avoids that.

That alone is not sufficient against the real templates: .claude/skills/conformance/RUBRIC.md
and .claude/skills/code-review/RUBRIC.md document their own placeholders in an `## Input` legend,
quoting the token itself in backticks. A blind single-pass substitute (e.g.
string.Template.safe_substitute) still replaces the legend along with the real slot. This module
additionally treats any single-line, backtick-quoted span as protected text and never substitutes
inside it -- verified by inspection to separate every real (bare) slot from every legend/prose
occurrence in all five templates this module renders.
"""
import re


class PromptRenderError(ValueError):
    """Raised when the template's bare (non-backticked) slots and the supplied `values` keys
    don't match exactly. A mismatch always means either a typo'd key or a template slot nobody
    filled -- this feeds a merge-gating reviewer prompt, so it must fail loud, never substitute
    partially or pass through a literal $NAME/{NAME} left in the rendered text."""


_TOKEN_RE = {
    "dollar": re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)"),
    "brace": re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}"),
}
# Single-line inline-code spans only (matches how both real RUBRIC.md legends quote their
# placeholders) -- not general markdown parsing, not triple-backtick fences.
_BACKTICK_SPAN_RE = re.compile(r"`[^`\n]*`")


def _segments(template_text: str):
    """Splits template_text into (text, protected) pairs -- protected=True for backtick-quoted
    spans, which are copied through untouched and never scanned for tokens."""
    pos = 0
    for span in _BACKTICK_SPAN_RE.finditer(template_text):
        yield template_text[pos:span.start()], False
        yield span.group(0), True
        pos = span.end()
    yield template_text[pos:], False


def render(template_text: str, values: dict, delimiter: str = "dollar") -> str:
    token_re = _TOKEN_RE[delimiter]
    segments = list(_segments(template_text))

    bare_names = set()
    for text, protected in segments:
        if not protected:
            bare_names.update(m.group(1) for m in token_re.finditer(text))

    missing = bare_names - values.keys()
    unused = values.keys() - bare_names
    if missing or unused:
        raise PromptRenderError(
            f"template/--set mismatch (delimiter={delimiter}): "
            f"missing={sorted(missing)} unused={sorted(unused)}"
        )

    return "".join(
        text if protected else token_re.sub(lambda m: values[m.group(1)], text)
        for text, protected in segments
    )
