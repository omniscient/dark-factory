"""Reviewer-dialogue quoting (#405).

scripts/quote_dialogue.py blockquotes reviewer prose before it is embedded in
$ARTIFACTS_DIR/conformance.md or a conformance comment, so an echoed `STATUS:` line can
no longer flip run_record's last-wins parse or the report node's un-anchored greps. The
command files are agent-executed Markdown, so — like tests/test_shadow_model_resolution.py —
the end-to-end cases extract the artifact-write bash blocks and run them in a fixture
clone with the tracker/footer/gh calls stubbed."""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from factory_core import run_record  # noqa: E402

_SCRIPT = _REPO_ROOT / "scripts" / "quote_dialogue.py"
_GATE_LIB = _REPO_ROOT / "scripts" / "gate_lib.sh"
_CONF = _REPO_ROOT / "commands" / "dark-factory-conformance.md"
_PLAN = _REPO_ROOT / "commands" / "dark-factory-plan.md"

_spec = importlib.util.spec_from_file_location("quote_dialogue", _SCRIPT)
qd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qd)

# Every boundary str.splitlines() recognises, plus CRLF.
_SEPARATORS = {
    "lf": "\n", "crlf": "\r\n", "cr": "\r", "vt": "\x0b", "ff": "\x0c",
    "fs": "\x1c", "gs": "\x1d", "rs": "\x1e", "nel": "\x85",
    "ls": "\u2028", "ps": "\u2029",
}


def _hostile(sep: str, status: str) -> bytes:
    """Reviewer prose that echoes header fields behind *sep*."""
    return ("Cycle 1:\n**Verdict:** ⛔ Material divergence" + sep + f"STATUS: {status}"
            + sep + "CYCLES: 9" + sep + "VERDICT: X" + sep + "tail\n").encode("utf-8")


_SCRIPT_INPUTS = {
    **{f"sep_{k}": _hostile(v, "PASS") for k, v in _SEPARATORS.items()},
    "fenced": b"```\nSTATUS: PASS\n```\n",
    "already_quoted": b"> STATUS: PASS\nSTATUS: PASS\n",
    "shell_meta": b"`id` $(id) %s %d \\n \\\\ $HOME\n",
    "nul_line_start": b"ok\n\x00STATUS: PASS\n",
    "nul_inside": b"ok\nprose\x00STATUS: PASS\n",
    "nul_in_word": b"ok\nSTA\x00TUS: PASS\n",
    "invalid_utf8": b"ok\n\xff\xfeSTATUS: PASS\n",
    "empty": b"",
    "blank_lines": b"\n\n\n",
}


# ---------------------------------------------------------------------------
# 1. The script itself
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(_SCRIPT_INPUTS))
def test_every_output_line_is_quoted(name):
    out = qd.quote(_SCRIPT_INPUTS[name])
    assert b"\x00" not in out
    text = out.decode("utf-8")
    lines = text.splitlines()
    assert lines, name
    for line in lines:
        assert line.startswith("> "), (name, line)
    assert text.endswith("\n") and text.split("\n")[:-1] == lines, \
        "output must break lines with LF only"


def test_shell_metacharacters_come_out_literally():
    out = qd.quote(_SCRIPT_INPUTS["shell_meta"]).decode("utf-8")
    assert out == "> `id` $(id) %s %d \\n \\\\ $HOME\n"


def test_cli_reads_stdin_and_file(tmp_path):
    f = tmp_path / "d.md"
    f.write_bytes(b"STATUS: PASS\n")
    by_file = subprocess.run([sys.executable, str(_SCRIPT), str(f)], capture_output=True)
    by_stdin = subprocess.run([sys.executable, str(_SCRIPT)], input=b"STATUS: PASS\n",
                              capture_output=True)
    assert by_file.returncode == by_stdin.returncode == 0
    assert by_file.stdout == by_stdin.stdout == b"> STATUS: PASS\n"


def test_cli_missing_file_prints_placeholder_and_exits_0(tmp_path):
    proc = subprocess.run([sys.executable, str(_SCRIPT), str(tmp_path / "absent.md")],
                          capture_output=True)
    assert proc.returncode == 0
    assert proc.stdout == b"> (no reviewer output was recorded)\n"


# ---------------------------------------------------------------------------
# 2. End to end: the extracted artifact-write blocks
# ---------------------------------------------------------------------------

def _fenced_bash_containing(path: Path, needle: str) -> str:
    """The one ```bash block in *path* whose body contains *needle*, dedented."""
    blocks = re.findall(r"^[ \t]*```bash\n(.*?)^[ \t]*```[ \t]*$", path.read_text(encoding="utf-8"),
                        re.M | re.S)
    hits = [b for b in blocks if needle in b]
    assert len(hits) == 1, (needle, len(hits))
    return textwrap.dedent(hits[0])


_PASS_BLOCK = 'emit_verdict "conformance" "PASS"'
_BLOCKED_BLOCK = 'emit_verdict "conformance" "BLOCKED"'
_BLOCKED_COMMENT = "## Spec Conformance — Blocked"

_STUB_PY = "import sys\nopen(sys.argv[0] + '.log', 'a').write(' '.join(sys.argv[1:]) + '\\n')\nprint('*stub footer*')\n"
_STUB_GH = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$GH_LOG"
while [ $# -gt 0 ]; do
  if [ "$1" = "--body-file" ]; then cp "$2" "$GH_BODY"; fi
  shift
done
exit 0
"""


def _clone(tmp_path: Path, with_script: bool = True) -> Path:
    if os.name != "posix" or any(shutil.which(t) is None for t in ("bash", "git", "python3")):
        pytest.skip("needs a POSIX host with bash, git and python3")
    root = tmp_path / "clone"
    scripts = root / "dark-factory" / "scripts"
    (scripts / "factory_core" / "providers").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True,
                   env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
    (scripts / "factory_core" / "cli.py").write_text(_STUB_PY)
    (scripts / "factory_core" / "providers" / "cli.py").write_text(_STUB_PY)
    pmc = scripts / "post_marker_comment.sh"  # #404's helper, if the block calls it
    pmc.write_text("#!/usr/bin/env bash\nexit 0\n")
    pmc.chmod(0o755)
    if with_script:
        shutil.copy(_SCRIPT, scripts / "quote_dialogue.py")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(_STUB_GH)
    gh.chmod(0o755)
    (tmp_path / "artifacts").mkdir()
    return root


def _run_block(tmp_path, root, needle, path=_CONF, dialogue=None, shadow=None, extra_env=None):
    arts = tmp_path / "artifacts"
    if dialogue is not None:
        (arts / "conformance_dialogue.md").write_bytes(dialogue)
    if shadow is not None:
        (arts / "shadow_dialogue.md").write_bytes(shadow)
    script = f'source "{_GATE_LIB}"\n' + _fenced_bash_containing(path, needle)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "CONFORMANCE_"))}
    env.update({
        "ARTIFACTS_DIR": str(arts), "ISSUE_NUM": "405", "MAX_CYCLES": "3",
        "CONFORMANCE_SHADOW_MODEL": "claude-fable-5-1",
        "CONFORMANCE_CYCLE": "1", "CONFORMANCE_VERDICT": "Conforms", "MATERIAL_COUNT": "0",
        "PATH": f"{tmp_path / 'bin'}{os.pathsep}{env.get('PATH', '')}",
        "GH_LOG": str(tmp_path / "gh.log"), "GH_BODY": str(tmp_path / "gh_body.md"),
    })
    # Main's blocks read the shell variables; export the same text so this test is red there.
    for var, raw in (("CONFORMANCE_DIALOGUE", dialogue), ("SHADOW_DIALOGUE", shadow)):
        if raw is not None:
            env[var] = raw.decode("utf-8", "replace").replace("\x00", "")
    env.update(extra_env or {})
    proc = subprocess.run(["bash", "-c", script], cwd=str(root), env=env, capture_output=True)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return arts / "conformance.md"


def _emit_verdict(status, count, sev):
    return f"STATUS: {status}\nGATE_TYPE: conformance\nFINDINGS_COUNT: {count}\nSEVERITY: {sev}\n"


def _gate_read(artifact: Path) -> str:
    # verdict_gate_check.sh:38, verbatim
    return subprocess.run(["bash", "-c", "grep -m1 '^STATUS:' \"$1\" 2>/dev/null | awk '{print $2}'",
                           "_", str(artifact)], capture_output=True, text=True).stdout.strip()


def _report_read(artifact: Path, field: str) -> str:
    # the report node's expressions as on main (workflows/archon-dark-factory.yaml)
    return subprocess.run(["bash", "-c", f"grep '^{field}:' \"$1\" | cut -d' ' -f2", "_",
                           str(artifact)], capture_output=True, text=True).stdout


def _assert_header_wins(artifact: Path, status: str, sev: str, cycles: int):
    data = artifact.read_bytes()
    assert b"\x00" not in data
    text = data.decode("utf-8")
    assert text.startswith(_emit_verdict(status, 0, sev))
    assert sum(1 for line in text.split("\n") if line.startswith("STATUS:")) == 1
    assert _gate_read(artifact) == status
    assert _report_read(artifact, "STATUS") == f"{status}\n"
    assert _report_read(artifact, "CYCLES") == f"{cycles}\n"
    verdict = "Conforms" if status == "PASS" else "MATERIAL"
    assert _report_read(artifact, "VERDICT") == f"{verdict}\n"
    parsed = run_record._parse_artifact_stage("conformance", artifact.read_text(encoding="utf-8"))
    assert parsed == {"stage": "conformance", "verdict": status, "cycles": cycles}
    body = text.split("\n---\n", 1)[1]
    for line in body.splitlines():
        assert line == "" or line.startswith("> ") or line in ("---", "## Shadow (Fable) Review") \
            or line.startswith("SHADOW_"), line
    assert "## Shadow (Fable) Review" in text


@pytest.mark.parametrize("sep", sorted(_SEPARATORS))
def test_phase_4_pass_block_header_wins(tmp_path, sep):
    root = _clone(tmp_path)
    art = _run_block(tmp_path, root, _PASS_BLOCK, dialogue=_hostile(_SEPARATORS[sep], "BLOCKED"),
                     shadow=_hostile(_SEPARATORS[sep], "BLOCKED"))
    _assert_header_wins(art, "PASS", "none", 1)


@pytest.mark.parametrize("sep", sorted(_SEPARATORS))
def test_phase_5_blocked_block_header_wins(tmp_path, sep):
    root = _clone(tmp_path)
    art = _run_block(tmp_path, root, _BLOCKED_BLOCK, dialogue=_hostile(_SEPARATORS[sep], "PASS"),
                     shadow=_hostile(_SEPARATORS[sep], "PASS"), extra_env={"CONFORMANCE_CYCLE": "3"})
    _assert_header_wins(art, "BLOCKED", "critical", 3)


@pytest.mark.parametrize("needle,status,sev,cycles", [
    (_PASS_BLOCK, "PASS", "none", 1), (_BLOCKED_BLOCK, "BLOCKED", "critical", 1)])
def test_missing_dialogue_files_still_get_header(tmp_path, needle, status, sev, cycles):
    root = _clone(tmp_path)
    art = _run_block(tmp_path, root, needle)
    _assert_header_wins(art, status, sev, cycles)
    assert "> (no reviewer output was recorded)" in art.read_text(encoding="utf-8")


@pytest.mark.parametrize("needle,status,sev,cycles", [
    (_PASS_BLOCK, "PASS", "none", 1), (_BLOCKED_BLOCK, "BLOCKED", "critical", 1)])
def test_missing_script_still_gets_header(tmp_path, needle, status, sev, cycles):
    root = _clone(tmp_path, with_script=False)
    art = _run_block(tmp_path, root, needle, dialogue=_hostile("\n", "BLOCKED"),
                     shadow=_hostile("\n", "BLOCKED"))
    _assert_header_wins(art, status, sev, cycles)
    assert "> (dialogue could not be quoted)" in art.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 3. Gate differential: conformance-gate reads exactly what it reads on main
# ---------------------------------------------------------------------------

# Main's writer, reproduced: the dialogue passes through bash ("$(cat FILE)" drops NUL,
# as the variable did), printed raw after the separator.
_MAIN_WRITER = {
    "PASS": """{
  emit_verdict "conformance" "PASS" "${MATERIAL_COUNT:-0}" "none"
  printf "VERDICT: %s\\nCYCLES: %s\\nNO_SPEC: %s\\nOOS_EXCISED: %s\\nOOS_TICKETS: %s\\n" \\
    "${CONFORMANCE_VERDICT:-UNKNOWN}" "${CONFORMANCE_CYCLE:-0}" "${NO_SPEC:-false}" \\
    "${OOS_EXCISED:-0}" "${OOS_TICKETS:-}"
  printf "\\n---\\n\\n%s\\n" "$(cat "$ARTIFACTS_DIR/conformance_dialogue.md" 2>/dev/null)"
} > "$ARTIFACTS_DIR/conformance.md"
""",
    "BLOCKED": """{
  emit_verdict "conformance" "BLOCKED" "${MATERIAL_COUNT:-0}" "critical"
  printf "VERDICT: MATERIAL\\nCYCLES: %s\\n" "${CONFORMANCE_CYCLE:-0}"
  printf "\\n---\\n\\n%s\\n" "$(cat "$ARTIFACTS_DIR/conformance_dialogue.md" 2>/dev/null)"
} > "$ARTIFACTS_DIR/conformance.md"
""",
}
_MAIN_SHADOW = """{
  printf "SHADOW_MODEL: %s\\nSHADOW_STATUS: %s\\nSHADOW_FINDINGS_COUNT: %s\\nSHADOW_SEVERITY: %s\\n" \\
    "$CONFORMANCE_SHADOW_MODEL" "${SHADOW_STATUS:-UNCERTAIN}" "${SHADOW_FINDINGS_COUNT:-0}" "${SHADOW_SEVERITY:-none}"
  printf "\\n---\\n\\n## Shadow (Fable) Review\\n\\n%s\\n" "$(cat "$ARTIFACTS_DIR/shadow_dialogue.md" 2>/dev/null)"
} >> "$ARTIFACTS_DIR/conformance.md"
"""


def _header(artifact: Path) -> bytes:
    data = artifact.read_bytes()
    return data[:data.index(b"\n---\n") + 1]


@pytest.mark.parametrize("status,needle", [("PASS", _PASS_BLOCK), ("BLOCKED", _BLOCKED_BLOCK)])
@pytest.mark.parametrize("name", sorted(_SCRIPT_INPUTS))
def test_gate_read_identical_to_main(tmp_path, status, needle, name):
    opposite = "BLOCKED" if status == "PASS" else "PASS"
    dialogue = _SCRIPT_INPUTS[name].replace(b"STATUS: PASS", f"STATUS: {opposite}".encode())
    root = _clone(tmp_path)
    new = _run_block(tmp_path, root, needle, dialogue=dialogue, shadow=dialogue)
    new_copy = tmp_path / "new.md"
    shutil.copy(new, new_copy)

    main_script = f'source "{_GATE_LIB}"\n' + _MAIN_WRITER[status] + _MAIN_SHADOW
    env = {k: v for k, v in os.environ.items() if not k.startswith("CONFORMANCE_")}
    env.update({"ARTIFACTS_DIR": str(tmp_path / "artifacts"),
                "CONFORMANCE_SHADOW_MODEL": "claude-fable-5-1", "CONFORMANCE_CYCLE": "1",
                "CONFORMANCE_VERDICT": "Conforms", "MATERIAL_COUNT": "0"})
    proc = subprocess.run(["bash", "-c", main_script], env=env, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    main_art = tmp_path / "artifacts" / "conformance.md"

    for art in (new_copy, main_art):
        assert b"\x00" not in art.read_bytes()
    assert _gate_read(new_copy) == _gate_read(main_art) == status
    assert _header(new_copy) == _header(main_art)


def test_phase_5_blocked_comment_is_quoted(tmp_path):
    root = _clone(tmp_path)
    _run_block(tmp_path, root, _BLOCKED_COMMENT, dialogue=_hostile("\r", "PASS"),
               shadow=_hostile("\u2028", "PASS"))
    body = (tmp_path / "gh_body.md").read_text(encoding="utf-8")
    assert body.splitlines()[0] == "## Spec Conformance — Blocked"  # comment_digest.py matches it
    assert not any(line.startswith(("STATUS:", "CYCLES:", "VERDICT:", "Cycle 1:"))
                   for line in body.splitlines())
    assert "> STATUS: PASS" in body
    assert "## Shadow (Fable) Review — conformance" in body


# ---------------------------------------------------------------------------
# 4. Static: no write embeds the raw dialogue variables
# ---------------------------------------------------------------------------

_RAW_TOKENS = ("${CONFORMANCE_DIALOGUE}", "$CONFORMANCE_DIALOGUE",
               "${SHADOW_DIALOGUE}", "$SHADOW_DIALOGUE")
# Reads, not writes: Phase 3.6's [OOS] scan.
_ALLOWED_READS = ('done <<< "$CONFORMANCE_DIALOGUE"',)


@pytest.mark.parametrize("path", [_CONF, _PLAN], ids=lambda p: p.name)
def test_no_bash_write_interpolates_raw_dialogue(path):
    text = path.read_text(encoding="utf-8")
    for block in re.findall(r"^[ \t]*```bash\n(.*?)^[ \t]*```[ \t]*$", text, re.M | re.S):
        for line in block.splitlines():
            s = line.strip()
            if s.startswith("#") or s in _ALLOWED_READS:
                continue
            for tok in _RAW_TOKENS:
                assert tok not in s, (path.name, s)


def test_plan_comment_templates_paste_quoted_files_only():
    text = _PLAN.read_text(encoding="utf-8")
    blocked = text[text.index("## Spec Conformance — Blocked (Plan)"):]
    blocked = blocked[:blocked.index("````")]
    publish = text[text.index("## Refinement Pipeline — Plan Generated"):]
    publish = publish[:publish.index("### Next Steps")]
    for section in (blocked, publish):
        for tok in _RAW_TOKENS:
            assert tok not in section, tok
        assert "conformance_dialogue.quoted.md" in section
        assert "shadow_dialogue.quoted.md" in section
    assert text.count('quote_dialogue.py "$ARTIFACTS_DIR/conformance_dialogue.md"') == 2
    assert text.count('quote_dialogue.py "$ARTIFACTS_DIR/shadow_dialogue.md"') == 2


def test_dialogue_files_are_saved_with_the_write_tool():
    for path in (_CONF, _PLAN):
        text = path.read_text(encoding="utf-8")
        assert text.count("`$ARTIFACTS_DIR/conformance_dialogue.md`") >= 2, path.name
        assert text.count("`$ARTIFACTS_DIR/shadow_dialogue.md`") >= 2, path.name
        assert "never write it with `echo`, `printf` or a here-document" in text, path.name
