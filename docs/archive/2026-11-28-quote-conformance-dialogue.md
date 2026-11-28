# Quote checker dialogue in `conformance.md` and the conformance comments — Implementation Plan

**Issue:** #405

**Goal:** No line of conformance reviewer prose (Opus or Fable shadow) can start at column 0
in `$ARTIFACTS_DIR/conformance.md` or in a conformance comment. So an echoed `STATUS:` /
`CYCLES:` / `VERDICT:` line can no longer flip `run_record._parse_artifact_stage`'s last-wins
parse or the `report` node's un-`-m1` greps. `conformance-gate`'s decision stays exactly what
it is on main for every input.

**Architecture:** A new standalone script `scripts/quote_dialogue.py` blockquotes (`> `) every
`str.splitlines()` line of a file and drops NUL bytes. The conformance and plan commands save
each accumulated dialogue verbatim with the Write tool to `$ARTIFACTS_DIR/conformance_dialogue.md`
/ `shadow_dialogue.md`. At all seven embed sites the raw `${CONFORMANCE_DIALOGUE}` /
`${SHADOW_DIALOGUE}` interpolation becomes a `quote_dialogue.py FILE || printf '> (dialogue
could not be quoted)\n'` call. The `emit_verdict` header lines stay first and byte-identical.
The working variables stay raw for every reader (Phase 3.6 `[OOS]` scan, verdict parse,
memory-write extraction). No gate script, parser, workflow or `gate_lib.sh` edit.

**Tech Stack:** Python 3 (stdlib only), bash, agent-executed Markdown commands, pytest.

**Spec:** `docs/superpowers/specs/2026-11-28-quote-conformance-dialogue-design.md`

---

## Before you start: dependency order and anchors

- The issue carries `Depends on: #404`, and #404 carries `Depends on: #409`. Implementation is
  dispatched only after #404 is Done, so your `main` should contain both. Check by content,
  not by commit subject. `git log | grep '#404'` gives a false hit: unrelated commit 3bfaa04
  "test: fix 5 pre-existing failures on main (#404)". `scripts/post_marker_comment.sh` also
  exists on main already; what #404 adds is the Phase 4 *call* to it. Run:
  `grep -c 'post_marker_comment.sh' commands/dark-factory-conformance.md` (≥ 1 once #404 has
  landed) and `grep -c 'Re-run the draft checkpoint block' commands/dark-factory-plan.md`
  (≥ 1 once #409 has landed). Either way the plan works: every
  edit below is anchored on **content** (the exact `printf` / prose strings), never on line
  numbers or step numbers. Known effects of the two:
  - #404 replaces the inline `FOOTER=` / `mktemp` / `tracker comment` / `rm` sequence in
    conformance Phase 4 with a `bash dark-factory/scripts/post_marker_comment.sh … factory || true`
    call. That is in the same fenced block as sites 1-2 but **does not touch** the lines this
    plan edits. The test stubs `post_marker_comment.sh` so it works on either shape. If #404
    changed `tests/test_conformance_command_shadow_review.py::test_phase_4_pass_emits_shadow_fields_and_marker_comment`,
    leave its new form as is.
  - #409 renumbers plan Phase 4 (the publish comment moves from step `6.` to step `7.`), and
    inserts a "Re-run the draft checkpoint block" bullet directly under Phase 3.5 step 8b's
    `b. If \`CONFORMANCE_CYCLE > MAX_CYCLES\`:` line. The edits below anchor on the step
    *text* ("Post a summary comment on the issue", "- Post the conformance dialogue as an
    issue comment"), so keep whatever step number you find. The 8b quoting bullet goes
    **immediately before** "- Post the conformance dialogue …", i.e. after #409's
    checkpoint bullet if that bullet is present.
- All prototype code in this plan has been run against `main` at 7255793, which contains
  #409 and #404: every anchor below matches exactly once, and the full suite gains 94 tests
  (all in `tests/test_quote_dialogue.py`) with no other test changing result.
- **Memory lessons baked in:** the loaded `.archon/memory/` entries are not specific to these
  files. One applies: Task 8's zero-diff checks compare the working tree to `origin/main`
  two-dot style (`git diff origin/main -- …`), never three-dot (`origin/main...HEAD`), so
  commits main merged independently cannot show up as false hits (#250 `[PATTERN]`). Repo
  conventions are applied directly: `# TARGET-PATH` on every `dark-factory/scripts/` call
  line, the #406 shadow-pin snippet left byte-identical, and no new `tests/*.sh`.
- **Rollout note:** in a factory run both `commands/` and `scripts/` come from the same image
  (`entrypoint.sh` copies `/opt/dark-factory/{scripts,commands}` into the clone's
  `dark-factory/scripts` and `.archon/commands`). So the new command text and
  `quote_dialogue.py` ship together. The `|| printf` arm covers only a missing interpreter or
  script.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `scripts/quote_dialogue.py` | Create (mode 100644) | Blockquote a file/stdin under `str.splitlines()`; drop NUL; never print an unquoted line; always exit 0 |
| `tests/test_quote_dialogue.py` | Create | Script unit tests; extracted-block end-to-end tests (Phase 4, Phase 5 step 4, Phase 5 step 1); static no-raw-interpolation tests; gate differential vs main's writer |
| `commands/dark-factory-conformance.md` | Modify | Step 3.1 steps 5/5a + Phase 3.5 steps 7/7a: save dialogues with the Write tool. Phase 4 intro sentence. Sites 1-4 (artifact writes) and site 5 (BLOCKED comment → `--body-file`) |
| `commands/dark-factory-plan.md` | Modify | Phase 3.5 steps 6/6a/8f/8f2: save dialogues. Sites 6 (8b comment) and 7 (Phase 4 publish comment): one quoting Bash call each + template text |
| `tests/fixtures/verdicts/conformance__pass_quoted_dialogue.md` + `.expected.json` | Create | Golden fixture: PASS header, hostile quoted prose |
| `tests/fixtures/verdicts/conformance__blocked_quoted_dialogue.md` + `.expected.json` | Create | Golden fixture: BLOCKED header, hostile quoted prose |
| `tests/test_verdict.py` | Modify | `test_golden_corpus_byte_compat` fixture count +2 (19 → 21 on today's main) |
| `refinement-skills/VERIFIER-CONTRACT.md` | Modify | One paragraph: conformance dialogue is embedded only through `quote_dialogue.py`; `review.md` not yet |
| `docs/run-lifecycle.md`, `docs/configuration.md`, `docs/features.md` | Modify | Re-point line citations into the two command files that the edits shift; one wording touch in run-lifecycle's D2 PASS/BLOCKED rows |

**Not touched (acceptance criterion 3; check in Task 8):** `scripts/verdict_gate_check.sh`,
`scripts/docs_verdict_gate_check.sh`, `scripts/gate_lib.sh`, `scripts/factory_core/**`,
`workflows/**`, `commands/dark-factory-code-review.md`,
`commands/dark-factory-docs-conformance.md`, `.github/workflows/ci.yml`, `deploy/**`, `tests/*.sh`.

---

## Task 1: `scripts/quote_dialogue.py` and its unit tests

**Files:**
- Create: `tests/test_quote_dialogue.py`
- Create: `scripts/quote_dialogue.py`

- [ ] **Step 1: Write the failing test file (sections header + 1).** Create
  `tests/test_quote_dialogue.py` with exactly:

```python
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
```

- [ ] **Step 2: Verify it fails.**
  Run: `PYTHONPATH=scripts python -m pytest tests/test_quote_dialogue.py -q`
  Expected: collection error `FileNotFoundError` … `scripts/quote_dialogue.py` (module
  load at import time).

- [ ] **Step 3: Implement.** Create `scripts/quote_dialogue.py` (mode 100644, like the other
  27 of 28 `scripts/*.py`) with exactly the spec R2 text:

```python
#!/usr/bin/env python3
"""Blockquote reviewer prose so no line of it can be read as a verdict header field (#405).

Usage: quote_dialogue.py [FILE]   (stdin when FILE is omitted)

Lines are split with str.splitlines(), the splitter factory_core/verdict.py::parse_verdict
uses, so the "> " prefix lands after every boundary that reader recognises (LF, CRLF, lone
CR, VT, FF, 0x1c-0x1e, NEL, U+2028, U+2029), not only after LF. NUL bytes are dropped (bash
drops them from a variable today; one NUL makes GNU grep treat the whole artifact as
binary). Never prints an unquoted line and always exits 0: a missing or unreadable FILE
prints a quoted placeholder.
"""
import sys


def quote(data: bytes) -> bytes:
    text = data.decode("utf-8", "replace").replace("\x00", "")
    out = "".join("> " + line + "\n" for line in text.splitlines()) or "> (empty)\n"
    return out.encode("utf-8")


def main(argv: list) -> int:
    try:
        if len(argv) > 1:
            with open(argv[1], "rb") as fh:
                data = fh.read()
        else:
            data = sys.stdin.buffer.read()
        out = quote(data)
    except Exception:
        out = b"> (no reviewer output was recorded)\n"
    sys.stdout.buffer.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
```

- [ ] **Step 4: Verify it passes.**
  Run: `PYTHONPATH=scripts python -m pytest tests/test_quote_dialogue.py -q`
  Expected: `23 passed`.

- [ ] **Step 5: Commit.**
```bash
git add scripts/quote_dialogue.py tests/test_quote_dialogue.py
git commit -m "feat(#405): scripts/quote_dialogue.py — blockquote reviewer prose under splitlines()"
```

---

## Task 2: Conformance artifact writes (sites 1-4) + end-to-end and gate-differential tests

**Files:**
- Modify: `tests/test_quote_dialogue.py` (append sections 2 and 4)
- Modify: `commands/dark-factory-conformance.md` (Phase 4 and Phase 5 step 4 write blocks)

- [ ] **Step 1: Append the failing end-to-end tests.** Append to `tests/test_quote_dialogue.py`:

```python
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
# 4. Gate differential: conformance-gate reads exactly what it reads on main
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
```

- [ ] **Step 2: Verify it fails.**
  Run: `PYTHONPATH=scripts python -m pytest tests/test_quote_dialogue.py -q -k "header_wins or still_get"`
  Expected: 26 selected, 26 failed: `test_phase_4_pass_block_header_wins`,
  `test_phase_5_blocked_block_header_wins` (e.g. `assert ... == 1` on the `STATUS:` count, or
  `_parse_artifact_stage` returning the dialogue's verdict), and both `missing_*` tests (no
  placeholder in the body). `test_gate_read_identical_to_main` passes before *and* after the
  edit. It pins the zero-delta requirement, it does not detect the bug.

Steps 3-6 match **whole lines, including leading indentation**. The 2-space Phase 4 line is a
substring of the 5-space Phase 5 line, and the 4-space shadow line is a substring of the
7-space one. With the Edit tool, include the preceding line in `old_string`, or do Steps 5-6
(Phase 5) before Steps 3-4. Each `old_string` must match exactly once.

- [ ] **Step 3: Edit site 1 (Phase 4 PASS, conformance dialogue).** In
  `commands/dark-factory-conformance.md`, `## Phase 4: PASS — Write attestation`, inside the
  `{ … } > "$ARTIFACTS_DIR/conformance.md"` group, replace the single line (2-space indent)

```bash
  printf "\n---\n\n%s\n" "${CONFORMANCE_DIALOGUE}"
```
  with

```bash
  printf "\n---\n\n"
  python3 dark-factory/scripts/quote_dialogue.py "$ARTIFACTS_DIR/conformance_dialogue.md" || printf '> (dialogue could not be quoted)\n'  # TARGET-PATH
```
  The `emit_verdict "conformance" "PASS" …` line and the `VERDICT/CYCLES/NO_SPEC/OOS_*`
  `printf` before it stay byte-identical (R3 invariant 1).

- [ ] **Step 4: Edit site 2 (Phase 4 PASS, shadow dialogue).** In the same block, inside
  `if [ -n "${SHADOW_MODEL_PIN:-}" ]; then` → `{ … } >> "$ARTIFACTS_DIR/conformance.md"`,
  replace (4-space indent)

```bash
    printf "\n---\n\n## Shadow (Fable) Review\n\n%s\n" "${SHADOW_DIALOGUE}"
```
  with

```bash
    printf "\n---\n\n## Shadow (Fable) Review\n\n"
    python3 dark-factory/scripts/quote_dialogue.py "$ARTIFACTS_DIR/shadow_dialogue.md" || printf '> (dialogue could not be quoted)\n'  # TARGET-PATH
```
  Do not touch the `ROOT=` / `SHADOW_MODEL_DEFAULT=` / `SHADOW_MODEL_PIN=` lines above the
  guard (#406 invariant 2), the `SHADOW_*` `printf`, or anything from `FOOTER=` /
  `SHADOW_BODY=` / `post_marker_comment.sh` onward.

- [ ] **Step 5: Edit site 3 (Phase 5 step 4 BLOCKED, conformance dialogue).** In
  `## Phase 5: BLOCKED`, step "4. Write blocked status to `$ARTIFACTS_DIR/conformance.md`",
  replace (5-space indent)

```bash
     printf "\n---\n\n%s\n" "${CONFORMANCE_DIALOGUE}"
```
  with

```bash
     printf "\n---\n\n"
     python3 dark-factory/scripts/quote_dialogue.py "$ARTIFACTS_DIR/conformance_dialogue.md" || printf '> (dialogue could not be quoted)\n'  # TARGET-PATH
```

- [ ] **Step 6: Edit site 4 (Phase 5 step 4 BLOCKED, shadow dialogue).** In the same block,
  replace (7-space indent)

```bash
       printf "\n---\n\n## Shadow (Fable) Review\n\n%s\n" "${SHADOW_DIALOGUE}"
```
  with

```bash
       printf "\n---\n\n## Shadow (Fable) Review\n\n"
       python3 dark-factory/scripts/quote_dialogue.py "$ARTIFACTS_DIR/shadow_dialogue.md" || printf '> (dialogue could not be quoted)\n'  # TARGET-PATH
```

- [ ] **Step 7: Edit the Phase 4 intro sentence.** Replace the line
  `Write the attestation to \`$ARTIFACTS_DIR/conformance.md\`:` with:

```markdown
Write the attestation to `$ARTIFACTS_DIR/conformance.md`. The dialogue is blockquoted from
the files saved in Step 3.1/Phase 3.5 (#405), so no reviewer line can start at column 0 and
be read as a header field. Do not check for, or wait on, those files first: a missing file
still gets a quoted placeholder, and the header lines are always written:
```

- [ ] **Step 8: Verify it passes, and the pinned neighbours stay green.**
  Run: `PYTHONPATH=scripts python -m pytest tests/test_quote_dialogue.py tests/test_shadow_model_resolution.py tests/test_conformance_command_shadow_review.py tests/test_conformance_command_artifact_guard.py -q`
  Expected: all pass. `test_shadow_model_resolution.py` still finds 4 conformance snippets +
  1 plan snippet, all identical, and 3 guarded blocks. `test_quote_dialogue.py` now has
  23 + 22 + 4 + 40 = 89 tests.

- [ ] **Step 9: Commit, with the gate-safety statement (spec R5) in the message body.** This
  commit edits the gate command, so it carries the statement the blast-gate reviewer needs:
```bash
git add commands/dark-factory-conformance.md tests/test_quote_dialogue.py
git commit -F - <<'MSG'
fix(#405): blockquote reviewer dialogue in conformance.md (Phase 4 and Phase 5 writes)

Gate safety (spec R5):
- scripts/verdict_gate_check.sh, scripts/gate_lib.sh, scripts/factory_core/verdict.py,
  scripts/factory_core/run_record.py and workflows/ are unchanged. conformance-gate reads
  the first ^STATUS: line, which emit_verdict still writes first, byte-identical, before
  any dialogue byte.
- Zero gate-visible delta: tests/test_quote_dialogue.py::test_gate_read_identical_to_main
  runs main's writer against the new one for every corpus dialogue (NUL at line start,
  inside a line and inside STATUS included) under both headers. The gate's
  grep -m1 '^STATUS:' read and every byte up to the first --- are identical, and neither
  artifact contains a NUL.
- run_record's recorded verdict changes only for artifacts that contained a prose STATUS:
  line, and only towards the header (gate) value.
- Out of scope (spec Non-goals / R4): review.md, docs-conformance.md, the report node's -m1.
MSG
```

---

## Task 3: Conformance BLOCKED comment (site 5) and saving the dialogue files

**Files:**
- Modify: `tests/test_quote_dialogue.py` (append the comment test and section 3's bash static test)
- Modify: `commands/dark-factory-conformance.md` (Phase 5 step 1; Step 3.1 steps 5/5a; Phase 3.5 steps 7/7a)

- [ ] **Step 1: Append the failing tests.** Append to `tests/test_quote_dialogue.py`:

```python
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
# 3. Static: no write embeds the raw dialogue variables
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
```

- [ ] **Step 2: Verify it fails.**
  Run: `PYTHONPATH=scripts python -m pytest tests/test_quote_dialogue.py -q -k "blocked_comment or raw_dialogue"`
  Expected: `test_phase_5_blocked_comment_is_quoted` fails (`FileNotFoundError` on
  `gh_body.md`, because main posts with `--body`), and
  `test_no_bash_write_interpolates_raw_dialogue[dark-factory-conformance.md]` fails on the
  `$SHADOW_DIALOGUE"` / `$CONFORMANCE_DIALOGUE` lines of Phase 5 step 1. The plan-file case
  already passes (its dialogue sites are agent-composed templates, not bash).

- [ ] **Step 3: Edit site 5, shadow part.** In `## Phase 5: BLOCKED` step 1's bash block,
  insert this line directly **after** `   if [ -n "${SHADOW_MODEL_PIN:-}" ]; then` (the one
  followed by `SHADOW_BLOCK="## Shadow (Fable) Review — conformance`), at 5-space indent:

```bash
     SHADOW_QUOTED=$(python3 dark-factory/scripts/quote_dialogue.py "$ARTIFACTS_DIR/shadow_dialogue.md" || printf '> (dialogue could not be quoted)\n')  # TARGET-PATH
```
  and replace the line `   $SHADOW_DIALOGUE"` (the last line of the `SHADOW_BLOCK` string)
  with `   $SHADOW_QUOTED"`. Do not insert anything between `ROOT=`, `SHADOW_MODEL_DEFAULT=`
  and `SHADOW_MODEL_PIN=`, or between them and the guard (#406).

- [ ] **Step 4: Edit site 5, body and transport.** In the same block replace

```bash
   gh issue comment $ISSUE_NUM --body "## Spec Conformance — Blocked
```
  with

```bash
   CONFORMANCE_QUOTED=$(python3 dark-factory/scripts/quote_dialogue.py "$ARTIFACTS_DIR/conformance_dialogue.md" || printf '> (dialogue could not be quoted)\n')  # TARGET-PATH
   BODY="## Spec Conformance — Blocked
```
  replace the body line `   $CONFORMANCE_DIALOGUE` with `   $CONFORMANCE_QUOTED`, and after
  the string's closing line `   $FOOTER"` (still inside the fence) add:

```bash
   TMPFILE=$(mktemp /tmp/df-conformance-blocked-XXXXXX.md)
   printf '%s\n' "$BODY" > "$TMPFILE"
   gh issue comment $ISSUE_NUM --body-file "$TMPFILE"
   rm -f "$TMPFILE"
```
  Every other body line, including the `### Next Steps` text, stays unchanged. The first
  body line stays exactly `## Spec Conformance — Blocked` (`scripts/comment_digest.py:102`).
  Keep every backtick in the body escaped as `` \` ``. A bare ` ``` ` line would end the
  fenced block for `_bash_blocks()` in `test_shadow_model_resolution.py`. The resulting
  block must read:

```bash
   FOOTER=$(python3 dark-factory/scripts/factory_core/cli.py marker factory)  # TARGET-PATH
   # Requirements 4/5: a real shell guard, not a prose note inside the body string — when
   # the shadow never ran (SHADOW_MODEL_PIN empty) nothing is posted, so an empty
   # SHADOW_MODEL: line can never masquerade as an UNCERTAIN shadow verdict.
   ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
   SHADOW_MODEL_DEFAULT=$(python3 -c "import yaml; d=yaml.safe_load(open('$ROOT/.claude/skills/refinement/config.yaml')) or {}; v=(d.get('conformance') or {}).get('shadow_model','claude-fable-5-1'); print(v if v else '')" 2>/dev/null || echo "claude-fable-5-1")
   SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-$SHADOW_MODEL_DEFAULT}"
   SHADOW_BLOCK=""
   if [ -n "${SHADOW_MODEL_PIN:-}" ]; then
     SHADOW_QUOTED=$(python3 dark-factory/scripts/quote_dialogue.py "$ARTIFACTS_DIR/shadow_dialogue.md" || printf '> (dialogue could not be quoted)\n')  # TARGET-PATH
     SHADOW_BLOCK="## Shadow (Fable) Review — conformance

   ${SHADOW_VERDICT_LINE:-**Verdict:** (unparseable — SHADOW_STATUS: UNCERTAIN)}

   \`\`\`
   SHADOW_MODEL: $SHADOW_MODEL_PIN
   SHADOW_STATUS: ${SHADOW_STATUS:-UNCERTAIN}
   SHADOW_FINDINGS_COUNT: ${SHADOW_FINDINGS_COUNT:-0}
   SHADOW_SEVERITY: ${SHADOW_SEVERITY:-none}
   \`\`\`

   $SHADOW_QUOTED"
   fi
   CONFORMANCE_QUOTED=$(python3 dark-factory/scripts/quote_dialogue.py "$ARTIFACTS_DIR/conformance_dialogue.md" || printf '> (dialogue could not be quoted)\n')  # TARGET-PATH
   BODY="## Spec Conformance — Blocked

   The implementation has material divergences from the spec that could not be resolved in $MAX_CYCLES reconcile cycle(s).

   $CONFORMANCE_QUOTED

   $SHADOW_BLOCK

   ### Next Steps

   Review the deviations above and either:
   - Fix the implementation to match the spec, then re-run: \`docker compose --profile factory run --rm dark-factory \"Continue issue #$ISSUE_NUM\"\`
   - Update the spec to document the deviation as intentional, then re-run.
   - Add \`needs-discussion\` if the spec itself needs revisiting.

   ---
   $FOOTER"
   TMPFILE=$(mktemp /tmp/df-conformance-blocked-XXXXXX.md)
   printf '%s\n' "$BODY" > "$TMPFILE"
   gh issue comment $ISSUE_NUM --body-file "$TMPFILE"
   rm -f "$TMPFILE"
```

- [ ] **Step 5: Save the dialogue files (Step 3.1 step 5).** Replace the line
  `5. Append the subagent's output to \`CONFORMANCE_DIALOGUE\`` with:

```markdown
5. Append the subagent's output to `CONFORMANCE_DIALOGUE`, then save the whole accumulated
   `CONFORMANCE_DIALOGUE`, verbatim, with the Write tool to
   `$ARTIFACTS_DIR/conformance_dialogue.md` (overwrite it with the full accumulated text each
   time; never write it with `echo`, `printf` or a here-document). Phase 4 and Phase 5 embed
   that file in `conformance.md` and the BLOCKED comment only through
   `scripts/quote_dialogue.py` (#405); the `CONFORMANCE_DIALOGUE` working value stays raw for
   Phase 3.6's `[OOS]` scan, the verdict parse and Phase 4's memory-write extraction.
```

- [ ] **Step 6: Step 3.1 step 5a.** Replace
  `   - Append the raw response to \`SHADOW_DIALOGUE\` (separate from \`CONFORMANCE_DIALOGUE\`).`
  with:

```markdown
   - Append the raw response to `SHADOW_DIALOGUE` (separate from `CONFORMANCE_DIALOGUE`),
     then save the whole accumulated `SHADOW_DIALOGUE`, verbatim, with the Write tool to
     `$ARTIFACTS_DIR/shadow_dialogue.md` (same rule as step 5).
```

- [ ] **Step 7: Phase 3.5 step 7.** Replace the line
  `` 7. Prepend `Cycle $CONFORMANCE_CYCLE:` header and append the new output to `CONFORMANCE_DIALOGUE` with a `---` separator ``
  with:

```markdown
7. Prepend `Cycle $CONFORMANCE_CYCLE:` header and append the new output to `CONFORMANCE_DIALOGUE` with a `---` separator,
   then re-save the whole accumulated `CONFORMANCE_DIALOGUE` to
   `$ARTIFACTS_DIR/conformance_dialogue.md` with the Write tool, per Step 3.1 step 5
```

- [ ] **Step 8: Phase 3.5 step 7a.** Directly after the line
  ``    `SHADOW_VERDICT_LINE` from this cycle's response (best-effort `UNCERTAIN` on any error).``
  insert:

```markdown
    Then re-save the whole accumulated `SHADOW_DIALOGUE` to
    `$ARTIFACTS_DIR/shadow_dialogue.md` with the Write tool, per Step 3.1 step 5a.
```
  Phase 3.6 (`## Phase 3.6: SCOPE REMEDIATION`) is not edited and must still not mention
  `SHADOW_DIALOGUE` (`test_phase_3_6_oos_scan_reads_conformance_dialogue_only`).

- [ ] **Step 9: Verify it passes.**
  Run: `PYTHONPATH=scripts python -m pytest tests/test_quote_dialogue.py tests/test_shadow_model_resolution.py tests/test_conformance_command_shadow_review.py tests/test_conformance_command_artifact_guard.py tests/test_verifier_contract_doc_referenced.py -q`
  Expected: all pass (`test_quote_dialogue.py`: 92).

- [ ] **Step 10: Commit.**
```bash
git add commands/dark-factory-conformance.md tests/test_quote_dialogue.py
git commit -m "fix(#405): quote dialogue in the conformance BLOCKED comment; save dialogue files"
```

---

## Task 4: Plan command comments (sites 6, 7) and saving the dialogue files

**Files:**
- Modify: `tests/test_quote_dialogue.py` (append two static tests)
- Modify: `commands/dark-factory-plan.md` (Phase 3.5 steps 6, 6a, 8b, 8f, 8f2; Phase 4 publish comment)

- [ ] **Step 1: Append the failing tests.** Append to `tests/test_quote_dialogue.py`:

```python
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
```

- [ ] **Step 2: Verify it fails.**
  Run: `PYTHONPATH=scripts python -m pytest tests/test_quote_dialogue.py -q -k "plan_comment or write_tool"`
  Expected: both fail. The template still contains `$CONFORMANCE_DIALOGUE`, and
  `dark-factory-plan.md` has no `` `$ARTIFACTS_DIR/conformance_dialogue.md` `` mention.

- [ ] **Step 3: Step 6 (save).** In `## Phase 3.5: CONFORMANCE REVIEW`, replace
  `6. Append the subagent's output to \`CONFORMANCE_DIALOGUE\`` with:

```markdown
6. Append the subagent's output to `CONFORMANCE_DIALOGUE`, then save the whole accumulated
   `CONFORMANCE_DIALOGUE`, verbatim, with the Write tool to
   `$ARTIFACTS_DIR/conformance_dialogue.md` (overwrite it with the full accumulated text each
   time; never write it with `echo`, `printf` or a here-document). The comments in step 8b
   and Phase 4 embed that file only through `scripts/quote_dialogue.py` (#405); the
   `CONFORMANCE_DIALOGUE` working value stays raw for the verdict parse in step 7.
```

- [ ] **Step 4: Step 6a (save).** Replace the two lines

```markdown
   - Append the raw response to `SHADOW_DIALOGUE` (separate from `CONFORMANCE_DIALOGUE` —
     never merged; `CONFORMANCE_DIALOGUE` alone drives the verdict check in step 7).
```
  with

```markdown
   - Append the raw response to `SHADOW_DIALOGUE` (separate from `CONFORMANCE_DIALOGUE` —
     never merged; `CONFORMANCE_DIALOGUE` alone drives the verdict check in step 7), then
     save the whole accumulated `SHADOW_DIALOGUE`, verbatim, with the Write tool to
     `$ARTIFACTS_DIR/shadow_dialogue.md` (same rule as step 6).
```

- [ ] **Step 5: Site 6 quoting call (8b).** Directly **before** the bullet line
  `      - Post the conformance dialogue as an issue comment (fetch the footer first via`
  (after #409's "Re-run the draft checkpoint block" bullet, if present), insert:

````markdown
      - Quote the saved dialogues for the comment (#405) in one self-contained Bash call; it
        reads only files, so no reviewer text passes through shell quoting:
        ```bash
        { python3 dark-factory/scripts/quote_dialogue.py "$ARTIFACTS_DIR/conformance_dialogue.md" || printf '> (dialogue could not be quoted)\n'; } > "$ARTIFACTS_DIR/conformance_dialogue.quoted.md"  # TARGET-PATH
        { python3 dark-factory/scripts/quote_dialogue.py "$ARTIFACTS_DIR/shadow_dialogue.md" || printf '> (dialogue could not be quoted)\n'; } > "$ARTIFACTS_DIR/shadow_dialogue.quoted.md"  # TARGET-PATH
        ```
````
  Do not add a `source …/gate_lib.sh` line anywhere in the plan command. Do not add another
  shadow-pin snippet (`test_all_five_snippets_are_identical` expects exactly 1 in this file).

- [ ] **Step 6: Site 6 template (8b).** Inside the ```` ```` ```` "Spec Conformance — Blocked
  (Plan)" template, replace the line `        $CONFORMANCE_DIALOGUE` with
  `        <contents of $ARTIFACTS_DIR/conformance_dialogue.quoted.md, pasted verbatim>`. Then
  replace
  `        <full $SHADOW_DIALOGUE, with the same Cycle N: headers as $CONFORMANCE_DIALOGUE above>`
  with
  `        <contents of $ARTIFACTS_DIR/shadow_dialogue.quoted.md, pasted verbatim (it keeps the same Cycle N: headers as the conformance dialogue above)>`.
  The `### Shadow (Fable) Review` heading in this template stays
  (`test_reconcile_loop_mirrors_shadow_spawn`).

- [ ] **Step 7: Step 8f (save).** Replace
  ``   f. Append the new output to `CONFORMANCE_DIALOGUE` with a `---` separator and `Cycle N:` header``
  with:

```markdown
   f. Append the new output to `CONFORMANCE_DIALOGUE` with a `---` separator and `Cycle N:` header,
      then re-save the whole accumulated `CONFORMANCE_DIALOGUE` to
      `$ARTIFACTS_DIR/conformance_dialogue.md` with the Write tool, per step 6
```

- [ ] **Step 8: Step 8f2 (save).** Directly after the line
  ``       `SHADOW_SEVERITY` from this cycle's response (best-effort `UNCERTAIN` on any error).``
  insert:

```markdown
       Then re-save the whole accumulated `SHADOW_DIALOGUE` to
       `$ARTIFACTS_DIR/shadow_dialogue.md` with the Write tool, per step 6a.
```
  Keep the literal `f2. If` (`test_reconcile_loop_mirrors_shadow_spawn`).

- [ ] **Step 9: Site 7 quoting call (Phase 4).** In `## Phase 4: PUBLISH`, replace the step
  line `N. Post a summary comment on the issue:` (N is `6` on today's main, `7` after #409;
  keep the number you find) with:

````markdown
N. Post a summary comment on the issue. Unless `CONFORMANCE_SKIPPED=true`, first quote the
   saved dialogues (#405) in one self-contained Bash call; it reads only files, so no
   reviewer text passes through shell quoting:
   ```bash
   { python3 dark-factory/scripts/quote_dialogue.py "$ARTIFACTS_DIR/conformance_dialogue.md" || printf '> (dialogue could not be quoted)\n'; } > "$ARTIFACTS_DIR/conformance_dialogue.quoted.md"  # TARGET-PATH
   { python3 dark-factory/scripts/quote_dialogue.py "$ARTIFACTS_DIR/shadow_dialogue.md" || printf '> (dialogue could not be quoted)\n'; } > "$ARTIFACTS_DIR/shadow_dialogue.quoted.md"  # TARGET-PATH
   ```

   Then compose the comment:
````
  (with `N` replaced by the actual number). The comment template that follows is unchanged
  except for Steps 10-11.

- [ ] **Step 10: Site 7 template, conformance.** In the publish template's `## Spec
  Conformance` section, replace the line

```markdown
   (Otherwise, include the full conformance reviewer output from Phase 3.5 — the final attestation table and verdict. If a reconcile loop ran, include the full dialogue with cycle headers.)
```
  with

```markdown
   (Otherwise, paste the contents of `$ARTIFACTS_DIR/conformance_dialogue.quoted.md` verbatim — the full conformance reviewer output from Phase 3.5, blockquoted, including the final attestation table and verdict and, if a reconcile loop ran, every cycle with its header. Never paste the raw `CONFORMANCE_DIALOGUE`.)
```

- [ ] **Step 11: Site 7 template, shadow.** Replace

```markdown
   <full $SHADOW_DIALOGUE, with the same Cycle N: headers as the Architect/Conformance
   sections above>
```
  with

```markdown
   <contents of $ARTIFACTS_DIR/shadow_dialogue.quoted.md, pasted verbatim (same Cycle N:
   headers as the Architect/Conformance sections above)>
```
  The fenced `SHADOW_MODEL:` … `SHADOW_SEVERITY:` lines and the `### Shadow (Fable) Review`
  heading stay (`test_publish_comment_includes_shadow_subsection`).

- [ ] **Step 12: Verify it passes.**
  Run: `PYTHONPATH=scripts python -m pytest tests/test_quote_dialogue.py tests/test_plan_command_shadow_conformance.py tests/test_shadow_model_resolution.py tests/test_command_issue_context_contract.py tests/test_verifier_contract_doc_referenced.py -q`
  Expected: all pass (`test_quote_dialogue.py`: 94). If #409 has landed, also run
  `tests/test_plan_command_checkpoint.py` and `tests/test_no_plan_drafts_tracked.py`;
  expected: pass unmodified.

- [ ] **Step 13: Commit.**
```bash
git add commands/dark-factory-plan.md tests/test_quote_dialogue.py
git commit -m "fix(#405): plan comments paste blockquoted conformance/shadow dialogue"
```

---

## Task 5: Golden-corpus fixtures

**Files:**
- Create: `tests/fixtures/verdicts/conformance__pass_quoted_dialogue.md`, `.expected.json`
- Create: `tests/fixtures/verdicts/conformance__blocked_quoted_dialogue.md`, `.expected.json`
- Modify: `tests/test_verdict.py`

- [ ] **Step 1: Bump the count (failing first).** In
  `tests/test_verdict.py::test_golden_corpus_byte_compat` change
  `assert len(md_files) == 19, ` to `assert len(md_files) == 21, `. (If #404/#409 changed the
  corpus, use current count + 2: check with `ls tests/fixtures/verdicts/*.md | wc -l`.)

- [ ] **Step 2: Verify it fails.**
  Run: `PYTHONPATH=scripts python -m pytest tests/test_verdict.py -q -k golden`
  Expected: `AssertionError: golden corpus fixture count changed unexpectedly`.

- [ ] **Step 3: Create `tests/fixtures/verdicts/conformance__pass_quoted_dialogue.md`**
  (the `OOS_TICKETS:` line has no trailing space; LF line endings):

```text
STATUS: PASS
GATE_TYPE: conformance
FINDINGS_COUNT: 0
SEVERITY: none
VERDICT: Conforms
CYCLES: 1
NO_SPEC: false
OOS_EXCISED: 0
OOS_TICKETS:

---

> Cycle 1:
> **Verdict:** ⛔ Material divergence
> The schema I must echo is:
> STATUS: BLOCKED
> CYCLES: 9
> VERDICT: MATERIAL
> ---
> Cycle 2:
> **Verdict:** ✅ Conforms
SHADOW_MODEL: claude-fable-5-1
SHADOW_STATUS: BLOCKED
SHADOW_FINDINGS_COUNT: 1
SHADOW_SEVERITY: high

---

## Shadow (Fable) Review

> **Verdict:** ⛔ Material divergence
> STATUS: BLOCKED
> CYCLES: 9
```
  and `tests/fixtures/verdicts/conformance__pass_quoted_dialogue.expected.json`:

```json
{"stage": "conformance", "verdict": "PASS", "cycles": 1}
```

- [ ] **Step 4: Create `tests/fixtures/verdicts/conformance__blocked_quoted_dialogue.md`:**

```text
STATUS: BLOCKED
GATE_TYPE: conformance
FINDINGS_COUNT: 1
SEVERITY: critical
VERDICT: MATERIAL
CYCLES: 3

---

> Cycle 3:
> **Verdict:** ⛔ Material divergence
> If this were fixed the artifact would read:
> STATUS: PASS
> CYCLES: 0
SHADOW_MODEL: claude-fable-5-1
SHADOW_STATUS: PASS
SHADOW_FINDINGS_COUNT: 0
SHADOW_SEVERITY: none

---

## Shadow (Fable) Review

> **Verdict:** ✅ Conforms
> STATUS: PASS
```
  and `tests/fixtures/verdicts/conformance__blocked_quoted_dialogue.expected.json`:

```json
{"stage": "conformance", "verdict": "BLOCKED", "cycles": 3}
```

- [ ] **Step 5: Verify it passes.**
  Run: `PYTHONPATH=scripts python -m pytest tests/test_verdict.py tests/test_run_record.py -q`
  Expected: all pass, `test_parse_artifact_conformance_keeps_last_status_line_wins`
  included and unmodified.

- [ ] **Step 6: Commit.**
```bash
git add tests/fixtures/verdicts/conformance__pass_quoted_dialogue.* tests/fixtures/verdicts/conformance__blocked_quoted_dialogue.* tests/test_verdict.py
git commit -m "test(#405): golden fixtures — quoted STATUS:-shaped dialogue keeps the header verdict"
```

---

## Task 6: `VERIFIER-CONTRACT.md` paragraph

**Files:**
- Modify: `refinement-skills/VERIFIER-CONTRACT.md`

- [ ] **Step 1: Add the paragraph.** In `## Verdict schema`, directly after the paragraph that
  ends `` `SEVERITY` are optional on parse, required on emit. `SEVERITY` ∈ `` /
  `` `{none, low, medium, high, critical}`. `` (before `## Target-verifier registration
  contract`), insert a blank line and:

```markdown
Conformance reviewer prose never starts a line in `conformance.md`. That artifact and the
conformance comments (`dark-factory-conformance.md` Phase 5, `dark-factory-plan.md`
Phase 3.5 step 8b and Phase 4) embed a reviewer's dialogue only through
`scripts/quote_dialogue.py`, which prefixes every line — under `str.splitlines()`, the
splitter `parse_verdict` uses — with `> `, so an echoed `STATUS:` or `CYCLES:` line
cannot be read as a header field by `run_record.py`'s last-wins parse or the `report`
node's greps (#405). `review.md` (`dark-factory-code-review.md`) does not do this yet.
```

- [ ] **Step 2: Verify.**
  Run: `PYTHONPATH=scripts python -m pytest tests/test_verifier_contract_doc_referenced.py tests/test_hybrid_execution_contract_doc.py -q`
  Expected: all pass.

- [ ] **Step 3: Commit.**
```bash
git add refinement-skills/VERIFIER-CONTRACT.md
git commit -m "docs(#405): VERIFIER-CONTRACT — conformance dialogue is embedded only via quote_dialogue.py"
```

---

## Task 7: Re-point doc line citations (R7)

**Files:**
- Modify: `docs/run-lifecycle.md`, `docs/configuration.md`, `docs/features.md` (only lines whose
  citations shifted)

Nothing checks more than range (`docs_refs_check.py`), so this is a manual mapping. Scope:
the living reference docs under `docs/`. `docs/archive/` is excluded by spec R7, and
`docs/superpowers/` (dated specs/plans) is excluded on purpose. Those are historical workflow
artifacts whose citations describe the tree they were written on. For example,
`docs/superpowers/plans/2026-07-22-github-board-comment-label-seam-design.md:1252` cites
`dark-factory-plan.md:216` and is left as is. Use the
base commit of your branch (`BASE=$(git merge-base HEAD origin/main)`) as the "old" file.

- [ ] **Step 1: Build the old→new line map** (scratch script under `/tmp`, not committed):

```bash
cat > /tmp/linemap.py <<'EOF'
"""Print old->new line numbers for every changed region of a file (post-edit vs a base rev)."""
import difflib, subprocess, sys
path, base = sys.argv[1], sys.argv[2]
old = subprocess.run(["git", "show", f"{base}:{path}"], capture_output=True, text=True, check=True).stdout.splitlines()
new = open(path, encoding="utf-8").read().splitlines()
sm = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
for tag, i1, i2, j1, j2 in sm.get_opcodes():
    if tag == "equal":
        print(f"old {i1+1}-{i2} -> new {j1+1}-{j2} (shift {j1-i1:+d})")
    else:
        print(f"{tag}: old {i1+1}-{i2} -> new {j1+1}-{j2}")
EOF
BASE=$(git merge-base HEAD origin/main)
python3 /tmp/linemap.py commands/dark-factory-conformance.md "$BASE"
python3 /tmp/linemap.py commands/dark-factory-plan.md "$BASE"
```
  Expected on main at 7255793 (with #409 and #404): the conformance map starts
  `old 1-304 -> new 1-304 (shift +0)`, then `replace: old 305-305 -> new 305-311`, and ends
  `old 746-796 -> new 771-821 (shift +25)`. The plan map starts `old 1-319 -> new 1-319`
  and ends `old 496-520 -> new 521-545 (shift +25)`.

- [ ] **Step 2: List every citation into the two files** (scratch script, not committed; it
  also catches continuation spans such as `` `:306`-`:328` `` after a cited path on the
  same line):

```bash
cat > /tmp/cites.py <<'EOF'
import re, subprocess
FILES = ("dark-factory-conformance.md", "dark-factory-plan.md")
TOK = re.compile(r"(?:(?:commands/)?(dark-factory-[a-z0-9-]+\.md|[\w./-]+\.(?:py|sh|ya?ml|md|json)))?:(\d+(?:[-,]\d+)*)")
docs = subprocess.run(["git", "ls-files", "docs/*.md", "docs/**/*.md"], capture_output=True, text=True).stdout.split()
for doc in sorted(set(docs)):
    if doc.startswith(("docs/archive/", "docs/superpowers/")):
        continue
    for n, line in enumerate(open(doc, encoding="utf-8"), 1):
        current = None
        for m in TOK.finditer(line):
            path, span = m.group(1), m.group(2)
            if path:
                current = path if path in FILES else None
            if current:
                print(f"{doc}:{n}\t{current}\t:{span}")
EOF
python3 /tmp/cites.py
```
  On main at 7255793, the hits at or past the first edited line (conformance ≥ 305, plan ≥ 320)
  are: `configuration.md:129` (plan `:391`), `:132` (plan `:390`), `:148` (conformance
  `:594-596,682-684,738-740`), `:772` (plan `:390`); `features.md:105` (plan `:431`), `:107`
  (conformance `:306`, `:328`, `:593`, `:607`), `:111` (`:586`, `:733`), `:134` (`:652`);
  `run-lifecycle.md:731` (plan `:329-360`), `:840` and `:1340` (conformance `:750-754`),
  `:1415` (`:587-589`). If main has moved again, take the list from your tree.

- [ ] **Step 3: Also cover the bare spans in run-lifecycle's D2 section.** The
  `**D2. Conformance, Gate 2** (\`commands/dark-factory-conformance.md\` …)` section of
  `docs/run-lifecycle.md` cites the command with bare spans (no path). List them:

```bash
start=$(grep -n '^\*\*D2\. Conformance, Gate 2\*\*' docs/run-lifecycle.md | cut -d: -f1)
end=$(grep -n '^\*\*The `conformance-gate` node enforces the result\*\*' docs/run-lifecycle.md | cut -d: -f1)
awk -v s="$start" -v e="${end:-$((start+60))}" 'NR>=s && NR<e' docs/run-lifecycle.md \
  | grep -noE '`:[0-9][0-9,-]*`' | awk -F: -v s="$start" '{print $1+s-1": "$2":"$3}'
```
  On main at 7255793: `(\`:293-332\`)`, `(\`:351-525\`)`, `(\`:339-349\`)`, `(\`:527-578\`)`,
  `` `:756-796` `` (Phase 6), `(\`:581-670\`)` (PASS row), `(\`:672-754\`)` (BLOCKED row).
  Spans that end before line 305 (`:59-62`, `:80-140`, `:144-220`) do not move. The window
  stops at the "**The `conformance-gate` node enforces the result**" paragraph. The bare spans
  after it cite `scripts/verdict_gate_check.sh` and other files, not the command, so leave
  them alone.

- [ ] **Step 4: Re-point.** For each citation from Steps 2-3, map each start and end through
  the Step 1 map (`new = old + shift` of the region containing `old`). Edit only the numbers.
  Leave the rest of each row as found. If a citation was already stale before this ticket
  (its lines do not hold what the prose describes, e.g. `run-lifecycle.md:819`/`:1319`
  `:748-752`, meant to cover the Phase 5 step 5 "Exit non-zero … conformance-gate"
  paragraph), point it at the lines that hold the described text now. Find them with
  `grep -n` on a phrase from the prose. Section spans (`:580-674` = Phase 4, `:676-752` =
  Phase 5, `:754-794` = Phase 6) run from the section's `## Phase …` heading to the line
  before the next heading (or the file's last line for Phase 6). Recompute them from
  `grep -n '^## Phase' commands/dark-factory-conformance.md`.

  Result on main at 7255793 with Tasks 2-4 applied (verified: each new span holds the text
  the doc describes). Five plan-command citations are already wrong on main, by 16 to 35
  lines, so shifting them is not enough; they are marked "stale":

  | Doc line | Old | New | Note |
  |---|---|---|---|
  | `configuration.md:129` | plan `:391` | `:424` | stale: the `PLAN_GRACE=` line |
  | `configuration.md:132`, `:772` | plan `:390` | `:423` | stale: the `grep -q "direct-to-pr"` line |
  | `configuration.md:148` | conformance `:594-596,682-684,738-740` | `:610-612,699-701,762-764` | shift |
  | `features.md:105` | plan `:431` | `:466` | stale: the `oos_excise.sh` call |
  | `features.md:107` | conformance `:306`-`:328`, `:593`-`:607` | `:312`-`:336`, `:609`-`:624` | shift |
  | `features.md:111` | conformance `:586`, `:733` | `:602`, `:756` | `:586` was one line short of `emit_verdict` |
  | `features.md:134` | conformance `:652` | `:669` | shift |
  | `run-lifecycle.md:731` | plan `:329-360` | `:352-389` | stale: step 8 through the end of 8b |
  | `run-lifecycle.md:840`, `:1340` | conformance `:750-754` | `:775-779` | shift |
  | `run-lifecycle.md:1415` | conformance `:587-589` | `:602-604` | shift |
  | `run-lifecycle.md:795` | `:293-332` | `:293-340` | D2 bare span |
  | `run-lifecycle.md:798` | `:351-525` | `:359-533` | D2 bare span |
  | `run-lifecycle.md:806` | `:339-349` | `:347-357` | D2 bare span |
  | `run-lifecycle.md:810` | `:527-578` | `:535-590` | D2 bare span |
  | `run-lifecycle.md:815` | `:756-796` | `:781-821` | Phase 6 |
  | `run-lifecycle.md:835` | `:581-670` | `:593-688` | Phase 4 |
  | `run-lifecycle.md:836` | `:672-754` | `:689-780` | Phase 5 |

  If your map from Step 1 differs from the one shown there, main has moved: redo the mapping
  instead of using this table.

- [ ] **Step 5: Wording touch in run-lifecycle's D2 result table.** In the PASS row, replace
  `` `OOS_TICKETS` and the reviewer dialogue. `` with
  `` `OOS_TICKETS` and the reviewer dialogue, blockquoted (`> `) by `scripts/quote_dialogue.py` (#405). ``
  In the BLOCKED row, replace `"Spec Conformance — Blocked" comment with the dialogue` with
  `"Spec Conformance — Blocked" comment with the blockquoted dialogue`. Change nothing else
  in either row.

- [ ] **Step 6: Verify.**
  Run: `python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md && PYTHONPATH=scripts python -m pytest tests/test_docs_refs_check.py -q`
  Expected: `Docs reference check passed for 4 doc(s).` and all pass. Then spot-check three
  re-pointed citations with `sed -n 'A,Bp' commands/…`. Each must show the text the doc
  describes.

- [ ] **Step 7: Commit.**
```bash
git add docs/run-lifecycle.md docs/configuration.md docs/features.md
git commit -m "docs(#405): re-point conformance/plan command line citations after dialogue quoting"
```

---

## Task 8: Full verification and the gate-safety statement

**Files:** none modified (verification only)

- [ ] **Step 1: Full suite.**
  Run: `PYTHONPATH=scripts python -m pytest tests/ -q`
  Expected: all pass. The count is main's plus 94 (all in `tests/test_quote_dialogue.py`).

- [ ] **Step 2: DAG checks.**
  Run: `python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml && python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml`
  Expected: exit 0 for both.

- [ ] **Step 3: Zero-diff surfaces (acceptance criteria 3-4).**
  Run:
```bash
git diff --stat origin/main -- scripts/verdict_gate_check.sh scripts/docs_verdict_gate_check.sh scripts/gate_lib.sh scripts/factory_core/ workflows/ commands/dark-factory-code-review.md commands/dark-factory-docs-conformance.md .github/ deploy/
git diff --name-only --diff-filter=A origin/main -- 'tests/*.sh'
```
  Expected: both print nothing.

- [ ] **Step 4: Header invariants.**
  Run: `git diff origin/main -- commands/dark-factory-conformance.md | grep -E '^[-+].*(emit_verdict|VERDICT: |SHADOW_MODEL_DEFAULT=|SHADOW_MODEL_PIN=|ROOT=\$\(git)'`
  Expected: no output (no `emit_verdict` line, header `printf` or #406 snippet line changed).

- [ ] **Step 5: Put the gate-safety statement where the reviewer sees it.** The PR body is
  not written from commits: `push-and-pr` builds it from a fixed template
  (`workflows/archon-dark-factory.yaml`, the `_PR_BODY=` assignment), and the implement agent
  does not open or edit the PR. Do not run `gh pr edit`. The statement reaches the reviewer
  two ways:
  - It is in Task 2's commit message body (Task 2 Step 9). Check with
    `git log --format=%B -1 --grep='blockquote reviewer dialogue in conformance.md'`. Expected:
    the "Gate safety (spec R5)" block is printed.
  - Put the same block, verbatim, under a `## Gate safety (#405, spec R5)` heading at the
    **top** of `$ARTIFACTS_DIR/implementation.md` when you write the implementation summary.
    The `report` node includes that file in the run report (`CHANGES=$(cat
    "$ARTIFACTS_DIR/implementation.md")`), and conformance reads it as the implementation
    summary.

  Copying the statement into the PR description itself, if wanted, is a human step after
  the PR opens, not part of this plan.

- [ ] **Step 6: Commit any fix-ups.** If Steps 1-4 needed a fix, commit it as
  `fix(#405): <one-line description of the fix>`, filling in the real description, and
  re-run Steps 1-4. If nothing needed fixing, there is nothing to commit.
