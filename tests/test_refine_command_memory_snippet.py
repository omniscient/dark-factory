"""Refine Phase 5 step 7a memory-write snippet (#38). The block is agent-executed
Markdown, so — like tests/test_shadow_model_resolution.py — the behavioral case extracts
it and runs it verbatim against a fixture clone, and the static cases pin its shape."""
import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_REFINE = _REPO_ROOT / "commands" / "dark-factory-refine.md"
_WRITER = _REPO_ROOT / "scripts" / "memory_write.py"
_PLACEHOLDER = ("Rejected approach: <what was rejected and the concrete reason>. "
                "Chosen approach: <what to do instead>.")
_LESSON = ("Rejected approach: expanding $HOME, $(echo x) and `date` inside \"double\" "
           "and 'single' quotes. Chosen approach: keep the lesson literal.")
_SHELL_VARS = ("ISSUE_NUM", "REPO_ROOT", "LESSON_TEXT", "MEMORY_WRITE")


def _step_7a():
    text = _REFINE.read_text(encoding="utf-8")
    start = text.index("   a. For each architectural decision made during Phase 4 Q&A")
    return text[start:text.index("   b. For any codebase convention", start)]


def _block():
    body = _step_7a().split("```bash\n", 1)[1].split("```", 1)[0]
    return textwrap.dedent(body)


def test_block_shape():
    block = _block()
    assert "--kind avoid" in block
    assert "<<'LESSON'" in block
    assert block.index("Rejected approach:") < block.index("Chosen approach:")
    assert _PLACEHOLDER in block.splitlines()
    assert "${ARTIFACTS_DIR}" not in block
    assert '"$ARTIFACTS_DIR/issue.json"' in block


def test_heredoc_terminator_has_no_leading_whitespace_in_raw_markdown():
    """The raw Markdown — not the dedented copy _block() returns — is what an agent
    pastes and runs. A terminator indented even one space breaks `<<'LESSON'`
    (bash requires an exact, unindented match), so check the un-dedented text."""
    raw_body = _step_7a().split("```bash\n", 1)[1].split("```", 1)[0]
    terminator_lines = [l for l in raw_body.splitlines() if l.strip() == "LESSON"]
    assert terminator_lines == ["LESSON"], (
        f"heredoc terminator line must be exactly 'LESSON' with no leading "
        f"whitespace in the raw Markdown, got {terminator_lines!r}"
    )


def test_tool_limitation_note_removed():
    assert "Tool limitation (#652)" not in _REFINE.read_text(encoding="utf-8")


def test_step_7b_names_kind_pattern():
    text = _REFINE.read_text(encoding="utf-8")
    start = text.index("   b. For any codebase convention")
    step_7b = text[start:text.index("   c. Do NOT write to", start)]
    assert "`--kind pattern`" in step_7b
    assert "`--kind avoid`" in step_7b


def test_block_runs_verbatim_and_keeps_lesson_literal(tmp_path):
    if os.name != "posix" or any(shutil.which(t) is None for t in ("bash", "git", "jq")):
        pytest.skip("needs a POSIX host with bash, git and jq")
    block = _block()
    assert block.count(_PLACEHOLDER) == 1
    block = block.replace(_PLACEHOLDER, _LESSON)

    run_env = {k: v for k, v in os.environ.items()
               if k not in _SHELL_VARS and not k.startswith("GIT_")}
    clone = tmp_path / "clone"
    subprocess.run(["git", "init", "-q", str(clone)], check=True, env=run_env)
    writer = clone / "dark-factory" / "scripts" / "memory_write.py"
    writer.parent.mkdir(parents=True)
    shutil.copy(_WRITER, writer)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "issue.json").write_text(json.dumps({"resolved_number": 4242}),
                                          encoding="utf-8")
    run_env["ARTIFACTS_DIR"] = str(artifacts)

    proc = subprocess.run(["bash", "-c", block], cwd=str(clone), env=run_env,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr

    lines = (clone / ".archon" / "memory" / "architecture.md").read_text(
        encoding="utf-8").splitlines()
    entries = [l for l in lines if l.startswith("- [")]
    assert len(entries) == 1, lines
    assert entries[0].startswith(f"- [AVOID] {_LESSON} <!-- issue:#4242 ")
    assert "source:refine" in entries[0]
    assert "path:.archon/commands/" in entries[0]
