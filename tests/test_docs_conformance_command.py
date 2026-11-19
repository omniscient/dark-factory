"""Static contract for Gate 2.5's command, rubric and skill files (#535). The command is
agent-executed Markdown, so — like tests/test_conformance_command_render_prompt.py — these
tests pin the load-bearing text: the diff that can see docs, the single Opus spawn, the
UNCERTAIN -> BLOCKED mapping, and the observe/enforce split of Phase 6's side effects."""
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CMD = _REPO_ROOT / "commands" / "dark-factory-docs-conformance.md"
_BAKED = _REPO_ROOT / "refinement-skills" / "docs-conformance-reviewer-prompt.md"


def _cmd():
    return _CMD.read_text(encoding="utf-8")


def _phase(title_prefix):
    text = _cmd()
    start = text.index(f"## {title_prefix}")
    nxt = text.find("\n## Phase", start + 1)
    return text[start:] if nxt == -1 else text[start:nxt]


def test_diff_sees_docs_but_keeps_noise_exclusions():
    phase2 = _phase("Phase 2")
    assert "':!docs/*.md'" not in _cmd(), "Gate 2.5 is the one gate that must see docs/*.md"
    assert "':!*.md'" not in _cmd()
    for token in ("':!*.lock'", "':!evals/*.md'", "':!bench/*.md'", "':!.archon/memory/**'",
                  "':!codeindex.json'", "':!symbolindex.json'"):
        assert token in phase2, token
    assert "git diff main...HEAD" in phase2
    assert "git diff main...HEAD failed" in phase2, "a git failure must not be read as an empty diff"


def test_policy_markdown_is_not_excluded_from_the_reviewed_diff():
    diff_block = _phase("Phase 2").split("# 2b.")[0]
    for path in ("commands/", ".claude/skills/", "refinement-skills/"):
        assert f"':!{path}" not in diff_block, f"{path} policy Markdown is behavior and must be reviewed"


def test_baseline_and_branch_doc_copies_are_collected():
    phase2 = _phase("Phase 2")
    assert "git merge-base main HEAD" in phase2
    assert 'git show "${BASE_REF}:${P}"' in phase2
    assert "docs_conformance_doc_files.md" in phase2


def test_single_opus_spawn_no_shadow_no_reconcile():
    text = _cmd()
    assert text.count("Spawn exactly one reviewer subagent") == 1
    assert "`claude-opus-5-5`" in text and "`opus` alias" in text
    assert "Glob/Grep/Read" in text
    assert "No shadow spawn" in text
    assert "max_reconcile_cycles" not in text and "RECONCILE" not in text
    assert "Do not\n   re-spawn, retry" in text or "Do not re-spawn" in text


def test_unparseable_maps_to_uncertain_blocked_never_pass():
    p45 = _phase("Phase 4")
    assert re.search(r'\*\)\s+VERDICT=UNCERTAIN; STATUS=BLOCKED', p45)
    assert 'VERDICT=MATERIAL;  STATUS=BLOCKED' in p45
    assert "STATUS=ERROR" not in _cmd(), "this gate never writes ERROR (enforce would block it anyway)"


def test_missing_doc_impact_forces_blocked():
    p45 = _phase("Phase 4")
    assert "DOC_IMPACT=missing" in p45
    assert "OVERALL: (UPDATED|NO_IMPACT: .+)" in p45
    assert 'emit_verdict "docs-conformance"' in p45


def test_observe_mode_has_no_label_or_board_side_effect():
    p6 = _phase("Phase 6")
    assert "<!-- df-docs-conformance-observed -->" in p6
    assert "<!-- df-docs-conformance-blocked -->" in p6
    side_effects = p6.split('if [ "$DOCS_MODE" = "enforce" ]; then')[-1]
    assert "--add needs-discussion" in side_effects and "--status blocked" in side_effects
    assert p6.count("--add needs-discussion") == 1 and p6.count("--status blocked") == 1, \
        "needs-discussion/board move must appear only in the final enforce-only branch"


def test_disabled_writes_skipped():
    assert "STATUS: SKIPPED\\nREASON: docs_conformance.enabled=false" in _phase("Phase 1")


def test_mode_parse_defaults_to_observe():
    # Both read sites (Phase 1, Phase 4-5) delegate to the single shared normalizer
    # (factory_core/cli.py docs-conformance-config, #535) instead of duplicating the
    # enabled/mode parsing inline, so the two call sites can't drift from each other.
    assert _cmd().count("docs-conformance-config") == 2
    assert _cmd().count('|| echo "true observe"') == 2


def test_no_commit_or_push_in_this_command():
    text = _cmd()
    assert "git commit" not in text and "git push" not in text and "git add" not in text


def test_rubric_renders_with_exactly_the_three_slots(tmp_path):
    import sys
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    from factory_core.prompt_render import render
    out = render(_BAKED.read_text(encoding="utf-8"),
                 {"DIFF_CONTENT": "D", "DOC_FILES": "F", "DOC_IMPACT": "I"})
    assert "**Verdict:**" in out
    assert "--set DIFF_CONTENT=@" in _cmd() and "--set DOC_FILES=@" in _cmd() and "--set DOC_IMPACT=@" in _cmd()


def test_rubric_asks_the_three_questions_and_audits_not_trusts():
    rubric = _BAKED.read_text(encoding="utf-8")
    assert "Form your own impact view first" in rubric
    assert "Check every doc claim" in rubric
    assert "Find omissions" in rubric
    assert "claim to audit" in rubric and "never evidence" in rubric
    assert "Never flag a doc change as out of scope" in rubric


def test_rubric_resolves_clone_live_first_then_baked():
    """The lookup follows VERIFIER-CONTRACT's clone-live-first pattern; #535 ships only the
    baked copy (no file under the human-reviewed .claude/skills/ surface)."""
    p3 = _phase("Phase 3")
    live = p3.index('[ -f ".claude/skills/docs-conformance/RUBRIC.md" ]')
    baked = p3.index('RUBRIC_FILE="/opt/refinement-skills/docs-conformance-reviewer-prompt.md"')
    assert live < baked
    assert not (_REPO_ROOT / ".claude" / "skills" / "docs-conformance").exists()



# ── executing the Phase 4–5 block: a verdict line that disagrees with its findings ──────

def _run_phase45(tmp_path, review):
    import os
    import shutil
    import subprocess

    import pytest
    if os.name != "posix" or any(shutil.which(t) is None for t in ("bash", "git")):
        pytest.skip("needs a POSIX host with bash and git")
    root = tmp_path / "clone"
    (root / "dark-factory").mkdir(parents=True)
    (root / "dark-factory" / "scripts").symlink_to(_REPO_ROOT / "scripts")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    arts = tmp_path / "artifacts"
    arts.mkdir()
    (arts / "docs_conformance_review.md").write_text(review, encoding="utf-8")
    (arts / "doc-impact.md").write_text("- `x` -> UPDATED `README.md#y`\n\nOVERALL: UPDATED\n")
    block = _phase("Phase 4").split("```bash\n", 1)[1].split("\n```", 1)[0]
    proc = subprocess.run(["bash", "-c", block], cwd=str(root),
                          env=dict(os.environ, ARTIFACTS_DIR=str(arts)),
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    head = (arts / "docs-conformance.md").read_text(encoding="utf-8").split("\n---\n", 1)[0]
    return dict(line.split(": ", 1) for line in head.strip().splitlines())


_MATERIAL_BULLETS = ("- [MATERIAL] `docs/a.md#b` — false claim\n"
                     "- [MATERIAL] `docs/c.md#d` — missing\n"
                     "- [MINOR] `docs/e.md#f` — wording\n")


def test_material_verdict_with_material_findings_has_no_contradiction_reason(tmp_path):
    v = _run_phase45(tmp_path, "**Verdict:** ⛔ Material divergence\n\n" + _MATERIAL_BULLETS)
    assert v["STATUS"] == "BLOCKED" and v["VERDICT"] == "MATERIAL"
    assert v["FINDINGS_COUNT"] == "3"
    assert "REASON" not in v, "a consistent ⛔ verdict must not be reported as a contradiction"


def test_conforms_verdict_with_material_findings_is_blocked_with_recomputed_count(tmp_path):
    v = _run_phase45(tmp_path, "**Verdict:** ✅ Conforms\n\n" + _MATERIAL_BULLETS)
    assert v["STATUS"] == "BLOCKED" and v["VERDICT"] == "MATERIAL" and v["SEVERITY"] == "high"
    assert v["FINDINGS_COUNT"] == "3"
    assert v["REASON"] == "verdict line contradicts [MATERIAL] findings"


def test_conforms_verdict_without_findings_passes(tmp_path):
    v = _run_phase45(tmp_path, "**Verdict:** ✅ Conforms\n\nNo deviations found.\n")
    assert v["STATUS"] == "PASS" and v["FINDINGS_COUNT"] == "0" and "REASON" not in v
