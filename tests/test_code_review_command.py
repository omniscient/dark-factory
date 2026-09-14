from pathlib import Path

CMD = Path(__file__).resolve().parents[1] / "commands" / "dark-factory-code-review.md"


def test_command_wires_the_contract():
    text = CMD.read_text(encoding="utf-8")
    # reads config + kill-switch
    assert "code_review" in text and "enabled" in text
    # calls the helper
    assert "code_review_payload.py" in text
    # reads the clone-live rubric first, falls back to the baked /opt path
    assert ".claude/skills/code-review/RUBRIC.md" in text
    assert "/opt/refinement-skills/code-review-reviewer-prompt.md" in text
    clone_pos = text.find(".claude/skills/code-review/RUBRIC.md")
    baked_pos = text.find("/opt/refinement-skills/code-review-reviewer-prompt.md")
    assert clone_pos < baked_pos, "clone-live path must be named before the baked fallback"
    # mirrors conformance's pre-triage diff exclusions
    assert "':!*.lock'" in text and "':!.archon/memory/**'" in text
    # blocking path routes the board-move through the shared tracker seam (#181 R1)
    assert "tracker set-status" in text and "--status blocked" in text
    # posts the review via the Pulls Reviews API
    assert "/pulls/" in text and "/reviews" in text
    # writes the artifact the report node reads
    assert "review.md" in text


def test_command_resolves_spec_file_via_push_gate_check():
    text = CMD.read_text(encoding="utf-8")
    assert 'push_gate_check.sh "docs/superpowers/specs/" "$ISSUE_NUM"' in text
    # resolved in Phase 1, before Phase 2 consumes it
    assert text.find("push_gate_check.sh") < text.find("## Phase 2")
    # Gate 3 runs after push-and-pr archived the spec (Gate 3 finding on PR #428): the
    # archive prefix must be tried, and a stale 2a/2b path re-pointed at docs/archive/.
    assert 'push_gate_check.sh "docs/archive/" "$ISSUE_NUM"' in text
    assert 'docs/archive/$(basename "$SPEC_FILE")' in text


def test_command_threads_spec_file_into_diff_rank():
    text = CMD.read_text(encoding="utf-8")
    assert '${SPEC_FILE:+--spec-file "$SPEC_FILE"}' in text
    diff_rank_pos = text.find("dark-factory/scripts/diff_rank.py")
    spec_flag_pos = text.find('${SPEC_FILE:+--spec-file "$SPEC_FILE"}')
    assert diff_rank_pos != -1 and spec_flag_pos != -1
    assert 0 < spec_flag_pos - diff_rank_pos < 400, \
        "the --spec-file flag must be part of the Phase 2 diff_rank.py invocation"


def test_command_zero_content_abort_exempt_from_fail_open():
    text = CMD.read_text(encoding="utf-8")
    # Not scoped: --check-nonempty is a brand-new flag introduced by this task, so it
    # can't be vacuously true against the unmodified file.
    assert "--check-nonempty" in text
    phase6 = text.find("## Phase 6")
    start = text.find("### If `ZERO_CONTENT=true`")
    assert phase6 != -1 and start > phase6, "zero-content branch must be a Phase 6 sub-branch"
    nxt = text.find("\n### ", start + 1)
    zero_content_section = text[start:] if nxt == -1 else text[start:nxt]
    # These three strings already exist in Phase 6's pre-existing BLOCKED branch, so they
    # must be checked scoped to the new third branch — an unscoped assertion would pass
    # even if the branch were never added at all.
    assert 'emit_verdict "code-review" "BLOCKED"' in zero_content_section
    assert "needs-discussion" in zero_content_section
    # the fail_open exemption must be stated explicitly (R5), not left implicit
    assert "fail_open" in zero_content_section and "exempt" in zero_content_section.lower()
    # the abort branch must not actually read the (nonexistent, no-subagent-ran) findings
    # file — the branch's own prose explains *why* it doesn't via the bare filename "review_
    # findings.md", so check for the operative cat-with-full-path construct instead, which
    # only appears where the file is actually read (the normal BLOCKED branch).
    assert 'cat "$ARTIFACTS_DIR/review_findings.md"' not in zero_content_section
