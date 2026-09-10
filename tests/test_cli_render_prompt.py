import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "scripts" / "factory_core" / "cli.py"


def _run(args, cwd):
    return subprocess.run(
        [sys.executable, str(CLI), "render-prompt", *args],
        cwd=cwd, capture_output=True, text=True,
    )


def test_render_prompt_literal_set_values_writes_out_file(tmp_path):
    template = tmp_path / "t.md"
    template.write_text("Hello $NAME, kind is $KIND\n", encoding="utf-8")
    out = tmp_path / "out.md"
    result = _run(
        ["--template", str(template), "--set", "NAME=World", "--set", "KIND=PLAN",
         "--out", str(out)],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert out.read_text(encoding="utf-8") == "Hello World, kind is PLAN\n"


def test_render_prompt_at_file_set_value(tmp_path):
    template = tmp_path / "t.md"
    template.write_text("Body:\n$BODY\n", encoding="utf-8")
    body_file = tmp_path / "body.md"
    body_file.write_text("large content here", encoding="utf-8")
    out = tmp_path / "out.md"
    result = _run(
        ["--template", str(template), "--set", f"BODY=@{body_file}", "--out", str(out)],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert out.read_text(encoding="utf-8") == "Body:\nlarge content here\n"


def test_render_prompt_missing_set_key_exits_nonzero_with_stderr_and_no_partial_out(tmp_path):
    template = tmp_path / "t.md"
    template.write_text("$NAME and $KIND\n", encoding="utf-8")
    out = tmp_path / "out.md"
    result = _run(
        ["--template", str(template), "--set", "NAME=World", "--out", str(out)],
        cwd=tmp_path,
    )
    assert result.returncode != 0
    assert "missing" in result.stderr.lower()
    assert not out.exists()


def test_render_prompt_brace_delimiter(tmp_path):
    template = tmp_path / "t.md"
    template.write_text("{A}-{B}\n", encoding="utf-8")
    out = tmp_path / "out.md"
    result = _run(
        ["--template", str(template), "--delimiter", "brace",
         "--set", "A=1", "--set", "B=2", "--out", str(out)],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert out.read_text(encoding="utf-8") == "1-2\n"


def test_render_prompt_prints_to_stdout_when_no_out(tmp_path):
    template = tmp_path / "t.md"
    template.write_text("$NAME\n", encoding="utf-8")
    result = _run(["--template", str(template), "--set", "NAME=World"], cwd=tmp_path)
    assert result.returncode == 0
    assert result.stdout == "World\n"
