"""Shadow-model pin resolution contract (#406). The five resolution sites in the
conformance and plan commands are agent-executed Markdown, so — like
tests/test_docs_conformance_command.py — the behavioral cases extract the bash snippet
and run it against a fixture clone, and the static cases pin where the snippet sits."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONF = _REPO_ROOT / "commands" / "dark-factory-conformance.md"
_PLAN = _REPO_ROOT / "commands" / "dark-factory-plan.md"
_GUARD = 'if [ -n "${SHADOW_MODEL_PIN:-}" ]; then'


def _phase1_snippet():
    text = _CONF.read_text(encoding="utf-8")
    step = text[text.index("12. Resolve the shadow model pin"):]
    body = step.split("```bash\n", 1)[1].split("```", 1)[0]
    return "\n".join(line.strip() for line in body.splitlines())


def _resolve(tmp_path, config_text, env=None, subdir=False):
    if os.name != "posix" or any(shutil.which(t) is None for t in ("bash", "git")):
        pytest.skip("needs a POSIX host with bash and git")
    run_env = {k: v for k, v in os.environ.items()
               if k != "CONFORMANCE_SHADOW_MODEL" and not k.startswith("GIT_")}
    run_env.update(env or {})
    root = tmp_path / "clone"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=run_env)
    if config_text is not None:
        cfg = root / ".claude" / "skills" / "refinement" / "config.yaml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(config_text, encoding="utf-8")
    cwd = root / "sub" if subdir else root
    cwd.mkdir(exist_ok=True)
    block = _phase1_snippet() + '\nprintf "%s" "$SHADOW_MODEL_PIN"\n'
    proc = subprocess.run(["bash", "-c", block], cwd=str(cwd), env=run_env,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_null_shadow_model_is_off(tmp_path):
    assert _resolve(tmp_path, "conformance:\n  shadow_model: null\n") == ""


def test_empty_shadow_model_is_off(tmp_path):
    assert _resolve(tmp_path, 'conformance:\n  shadow_model: ""\n') == ""


def test_absent_key_falls_back_to_default(tmp_path):
    assert _resolve(tmp_path, "conformance:\n  enabled: true\n") == "claude-fable-5-1"


def test_explicit_value_is_used(tmp_path):
    assert _resolve(tmp_path, "conformance:\n  shadow_model: claude-opus-5-5\n") == "claude-opus-5-5"


def test_missing_config_file_falls_back_to_default(tmp_path):
    assert _resolve(tmp_path, None) == "claude-fable-5-1"


def test_explicit_empty_env_wins_over_config(tmp_path):
    cfg = "conformance:\n  shadow_model: claude-opus-5-5\n"
    assert _resolve(tmp_path, cfg, env={"CONFORMANCE_SHADOW_MODEL": ""}) == ""


def test_config_is_found_from_a_subdirectory(tmp_path):
    cfg = "conformance:\n  shadow_model: claude-opus-5-5\n"
    assert _resolve(tmp_path, cfg, subdir=True) == "claude-opus-5-5"


def _bash_blocks(path):
    """Every fenced ```bash block in *path*, as lists of whitespace-stripped lines."""
    blocks, cur = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if cur is None and s == "```bash":
            cur = []
        elif cur is not None and s == "```":
            blocks.append(cur)
            cur = None
        elif cur is not None:
            cur.append(s)
    return blocks


def _snippets(path):
    """(block, index) of each resolution snippet: the ROOT= line and the two after it."""
    found = []
    for block in _bash_blocks(path):
        for i, line in enumerate(block):
            if line.startswith("SHADOW_MODEL_DEFAULT="):
                found.append((block, i - 1))
    return found


def test_no_site_reads_the_config_relative_to_cwd():
    for path in (_CONF, _PLAN):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "shadow_model" in line:
                assert "open('.claude/skills/refinement/config.yaml')" not in line, path.name


def test_all_five_snippets_are_identical():
    conf, plan = _snippets(_CONF), _snippets(_PLAN)
    assert (len(conf), len(plan)) == (4, 1)
    texts = {"\n".join(block[i:i + 3]) for block, i in conf + plan}
    assert len(texts) == 1, texts
    first = texts.pop().splitlines()[0]
    assert first == "ROOT=$(git rev-parse --show-toplevel 2>/dev/null)"
    assert _phase1_snippet().strip() == "\n".join(conf[0][0][conf[0][1]:conf[0][1] + 3])


def test_snippet_precedes_each_guard_in_the_same_block():
    guarded = [b for b in _bash_blocks(_CONF) if _GUARD in b]
    assert len(guarded) == 3
    for block in guarded:
        root_idx = block.index("ROOT=$(git rev-parse --show-toplevel 2>/dev/null)")
        assert block[root_idx + 1].startswith("SHADOW_MODEL_DEFAULT=")
        assert block[root_idx + 2].startswith("SHADOW_MODEL_PIN=")
        assert root_idx + 2 < block.index(_GUARD)
