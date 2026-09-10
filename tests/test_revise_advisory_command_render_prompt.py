import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from factory_core.prompt_render import _segments, _TOKEN_RE

REPO_ROOT = Path(__file__).resolve().parents[1]
CMD = REPO_ROOT / "commands" / "dark-factory-revise-advisory.md"


def test_phase3_uses_render_prompt_with_brace_delimiter():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("## Phase 3: SPAWN FIX AGENT")
    phase3 = text[idx:text.find("## Phase 4: COMMIT AND PUSH")]
    assert "Replace `{FINDINGS_TEXT}` with `$FINDINGS_TEXT`" not in phase3
    assert "render-prompt" in phase3
    assert "--delimiter brace" in phase3
    assert '--set FINDINGS_TEXT=@"$ARTIFACTS_DIR/revise_advisory_findings.md"' in phase3
    assert '--set DIFF_CONTENT=@"$ARTIFACTS_DIR/revise_advisory_diff.md"' in phase3
    assert "revise_advisory_prompt.md" in phase3
    assert "render-prompt failed" in phase3


def test_phase3_heredoc_uses_quoted_delimiter():
    text = CMD.read_text(encoding="utf-8")
    assert "<<'PROMPT_EOF'" in text


def test_phase3_outer_fence_is_four_backticks_not_three():
    text = CMD.read_text(encoding="utf-8")
    idx = text.find("## Phase 3: SPAWN FIX AGENT")
    phase3 = text[idx:text.find("## Phase 4: COMMIT AND PUSH")]
    assert "````bash" in phase3


def test_embedded_template_bare_slots_match_expected_drift_guard():
    text = CMD.read_text(encoding="utf-8")
    start_marker = "<<'PROMPT_EOF'\n"
    start = text.index(start_marker) + len(start_marker)
    end = text.index("\nPROMPT_EOF", start)
    template_text = text[start:end]

    token_re = _TOKEN_RE["brace"]
    bare = set()
    for segment_text, protected in _segments(template_text):
        if not protected:
            bare.update(m.group(1) for m in token_re.finditer(segment_text))
    assert bare == {"FINDINGS_TEXT", "DIFF_CONTENT"}
