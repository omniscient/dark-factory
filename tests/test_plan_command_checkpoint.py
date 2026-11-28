"""Draft-plan checkpoint in commands/dark-factory-plan.md (#409).

The plan command marks three bash blocks with <!-- plan-draft-<name>:begin/end -->:
Phase 1's draft detection, Phase 2's checkpoint commit+push and Phase 4's promote. These
tests extract each block and run it for real against a bare origin + working tree
(tests/test_push_gate_dag_exec.py's fixture shape), with the real
scripts/push_gate_check.sh and scripts/oos_excise.sh. The text tests pin where the
blocks sit relative to Phase 3, the needs-discussion exits and the OOS gate.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CMD = _REPO_ROOT / "commands" / "dark-factory-plan.md"
_ISSUE = "409"
_BRANCH = f"refine/issue-{_ISSUE}-test"
_SPEC = "docs/superpowers/specs/2026-11-28-test-design.md"
_DRAFT = "docs/superpowers/plan-drafts/2026-11-28-test.md"
_FINAL = "docs/superpowers/plans/2026-11-28-test.md"
_OLD_PLAN = "docs/superpowers/plans/2026-11-01-old.md"
_PLAN_ALLOWLIST = "docs/superpowers/plans/ docs/superpowers/specs/ .archon/memory/"
_REFINE_ALLOWLIST = "docs/superpowers/specs/ .archon/memory/"

_posix = pytest.mark.skipif(
    os.name != "posix"
    or any(shutil.which(tool) is None for tool in ("bash", "git", "jq", "timeout")),
    reason="needs a POSIX host with bash, git, jq and coreutils timeout (Linux/CI-only harness)",
)


def _text():
    return _CMD.read_text(encoding="utf-8")


def _section(start, end=None):
    text = _text()
    i = text.index(start)
    return text[i:text.index(end, i)] if end else text[i:]


def _block(name):
    m = re.search(
        rf"<!-- plan-draft-{name}:begin -->\s*```bash\n(.*?)\n[ \t]*```\s*<!-- plan-draft-{name}:end -->",
        _text(), re.S,
    )
    assert m, f"no ```bash block between <!-- plan-draft-{name}:begin/end --> in {_CMD.name}"
    return m.group(1)


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def _write(work, rel, content):
    path = work / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _commit(work, rel, content, msg):
    _write(work, rel, content)
    git("add", "--", rel, cwd=work)
    assert git("commit", "-m", msg, cwd=work).returncode == 0


def _head(work):
    return git("rev-parse", "HEAD", cwd=work).stdout.strip()


def _tracked(work, prefix):
    return git("ls-files", "--", prefix, cwd=work).stdout.split()


def _plan(body="v1"):
    return f"# Plan\n\n**Issue:** #{_ISSUE}\n\n{body}\n"


@pytest.fixture()
def env(tmp_path):
    """Bare origin + working tree on a refine branch that already carries the spec, plus
    the REPO_ROOT/ARTIFACTS_DIR layout the blocks read."""
    bare = tmp_path / "bare"
    work = tmp_path / "work"
    bare.mkdir()
    git("init", "--bare", str(bare), cwd=tmp_path)
    git("symbolic-ref", "HEAD", "refs/heads/main", cwd=bare)
    git("clone", str(bare), str(work), cwd=tmp_path)
    git("config", "user.email", "test@test.com", cwd=work)
    git("config", "user.name", "Test", cwd=work)
    (work / "README.md").write_text("root\n")
    git("add", "README.md", cwd=work)
    git("commit", "-m", "init", cwd=work)
    git("push", "origin", "HEAD:main", cwd=work)
    git("checkout", "-b", _BRANCH, cwd=work)
    _commit(work, _SPEC, f"# Spec\n\n**Issue:** #{_ISSUE}\n", f"docs(#{_ISSUE}): add spec")

    # The blocks resolve scripts as "${REPO_ROOT}/dark-factory/scripts/..." — point that
    # at this checkout (the run container stages a copy there).
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / "dark-factory").symlink_to(_REPO_ROOT, target_is_directory=True)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "issue.json").write_text(json.dumps({"resolved_number": int(_ISSUE)}))
    return {"bare": bare, "work": work, "clone": clone, "artifacts": artifacts}


def _run(env, name, **preset):
    """Run block `name` in a fresh bash with the variables earlier steps bind pre-set;
    return the result and the block's exit status plus RESUMING_DRAFT/PLAN_FILE after it."""
    preset = {"REPO_ROOT": str(env["clone"]), "ISSUE_NUM": _ISSUE, **preset}
    prelude = "".join(f"{k}={shlex.quote(v)}\n" for k, v in preset.items())
    epilogue = ('\n__rc=$?\nprintf "\\n__rc=%s\\n__RESUMING_DRAFT=%s\\n__PLAN_FILE=%s\\n" '
                '"$__rc" "${RESUMING_DRAFT-<unset>}" "${PLAN_FILE-<unset>}"\n')
    result = subprocess.run(
        ["bash", "-c", prelude + _block(name) + epilogue],
        cwd=str(env["work"]), env=dict(os.environ, ARTIFACTS_DIR=str(env["artifacts"])),
        capture_output=True, text=True, timeout=120,
    )
    return result, dict(re.findall(r"^__(\w+)=(.*)$", result.stdout, re.M))


def _gate(env, prefix):
    result = subprocess.run(
        ["bash", str(_REPO_ROOT / "scripts" / "push_gate_check.sh"), prefix, _ISSUE],
        cwd=str(env["work"]), capture_output=True, text=True, timeout=60,
    )
    return result.stdout.strip()


def _oos(env, allowlist, noun):
    result = subprocess.run(
        ["bash", str(_REPO_ROOT / "scripts" / "oos_excise.sh"), allowlist, noun],
        cwd=str(env["work"]), capture_output=True, text=True, timeout=60,
        env=dict(os.environ, ARTIFACTS_DIR=str(env["artifacts"]), ISSUE_NUM=_ISSUE),
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


# ── Phase 2: checkpoint ──────────────────────────────────────────────────────


@_posix
def test_checkpoint_commits_and_pushes_only_the_draft(env):
    work = env["work"]
    base = _head(work)
    _write(work, _DRAFT, _plan())
    _write(work, "unrelated.txt", "must not be committed\n")

    result, out = _run(env, "checkpoint", PLAN_FILE=_DRAFT)

    assert out["rc"] == "0", result.stderr
    assert git("rev-list", "--count", f"{base}..HEAD", cwd=work).stdout.strip() == "1"
    assert git("show", "--name-only", "--format=", "HEAD", cwd=work).stdout.split() == [_DRAFT]
    remote = git("ls-remote", "--heads", str(env["bare"]), _BRANCH, cwd=work).stdout.split()
    assert remote and remote[0] == _head(work), "the checkpoint must reach origin"
    assert _gate(env, "docs/superpowers/plans/") == "", "a draft must never look like a plan"


@_posix
def test_checkpoint_push_failure_is_non_fatal(env):
    work = env["work"]
    hook = env["bare"] / "hooks" / "pre-receive"
    hook.write_text('#!/bin/sh\necho "rejected by test hook" >&2\nexit 1\n')
    hook.chmod(0o755)
    _write(work, _DRAFT, _plan())

    result, out = _run(env, "checkpoint", PLAN_FILE=_DRAFT)

    assert out["rc"] == "0", result.stderr
    assert "WARN: draft plan checkpoint push failed" in result.stdout
    assert git("log", "-1", "--format=%s", cwd=work).stdout.strip() == \
        f"docs(#{_ISSUE}): checkpoint draft plan"
    assert git("ls-remote", "--heads", str(env["bare"]), _BRANCH, cwd=work).stdout.strip() == ""


@_posix
def test_checkpoint_rerun_on_unchanged_draft_is_a_noop(env):
    work = env["work"]
    _write(work, _DRAFT, _plan())
    _run(env, "checkpoint", PLAN_FILE=_DRAFT)
    first = _head(work)

    result, out = _run(env, "checkpoint", PLAN_FILE=_DRAFT)

    assert out["rc"] == "0", result.stderr
    assert _head(work) == first


@_posix
def test_checkpoint_without_plan_file_stops(env):
    work = env["work"]
    base = _head(work)

    result, _ = _run(env, "checkpoint")

    assert result.returncode != 0
    assert "PLAN_FILE is unbound or missing" in result.stdout
    assert _head(work) == base
    assert git("ls-remote", "--heads", str(env["bare"]), _BRANCH, cwd=work).stdout.strip() == ""


# ── Command text ─────────────────────────────────────────────────────────────


def test_scope_boundary_lists_the_drafts_directory():
    scope = _section("## SCOPE BOUNDARY", "## Phase 1: LOAD")
    assert "`docs/superpowers/plans/`" in scope
    assert "`docs/superpowers/plan-drafts/`" in scope


def test_phase2_saves_to_the_drafts_directory():
    phase2 = _section("## Phase 2: PLAN WRITING", "## Phase 3: ARCHITECT REVIEW")
    assert "Save to `docs/superpowers/plan-drafts/" in phase2
    assert "Save to `docs/superpowers/plans/" not in phase2


def test_checkpoint_is_defined_once_before_phase3():
    text = _text()
    assert text.count("<!-- plan-draft-checkpoint:begin -->") == 1
    assert text.index("<!-- plan-draft-checkpoint:end -->") < text.index("## Phase 3: ARCHITECT REVIEW")
