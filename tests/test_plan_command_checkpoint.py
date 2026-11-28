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


# ── Phase 1: detect ──────────────────────────────────────────────────────────


@_posix
def test_detect_resumes_a_committed_draft(env):
    _commit(env["work"], _DRAFT, _plan(), f"docs(#{_ISSUE}): checkpoint draft plan")

    result, out = _run(env, "detect", SPEC_FILE=_SPEC)

    assert out["rc"] == "0", result.stderr
    assert (out["RESUMING_DRAFT"], out["PLAN_FILE"]) == ("true", _DRAFT)


@_posix
def test_detect_rewrites_in_place_when_spec_is_newer(env):
    work = env["work"]
    _commit(work, _DRAFT, _plan(), f"docs(#{_ISSUE}): checkpoint draft plan")
    _commit(work, _SPEC, f"# Spec v2\n\n**Issue:** #{_ISSUE}\n", f"docs(#{_ISSUE}): amend spec")

    _, out = _run(env, "detect", SPEC_FILE=_SPEC)

    assert (out["RESUMING_DRAFT"], out["PLAN_FILE"]) == ("false", _DRAFT)


@_posix
def test_detect_does_not_resume_an_empty_draft(env):
    # Found via push_gate_check.sh's commit-subject pass: the file itself names no issue.
    _commit(env["work"], _DRAFT, "", f"docs(#{_ISSUE}): checkpoint draft plan")

    _, out = _run(env, "detect", SPEC_FILE=_SPEC)

    assert (out["RESUMING_DRAFT"], out["PLAN_FILE"]) == ("false", _DRAFT)


@_posix
def test_detect_without_a_draft_leaves_plan_file_unset(env):
    _, out = _run(env, "detect", SPEC_FILE=_SPEC)

    assert (out["RESUMING_DRAFT"], out["PLAN_FILE"]) == ("false", "<unset>")


@_posix
def test_detect_without_spec_file_stops(env):
    _commit(env["work"], _DRAFT, _plan(), f"docs(#{_ISSUE}): checkpoint draft plan")

    result, out = _run(env, "detect")

    assert result.returncode != 0
    assert "bind SPEC_FILE" in result.stderr
    assert out == {}


@_posix
def test_detect_treats_a_context_pack_spec_as_not_newer(env):
    _commit(env["work"], _DRAFT, _plan(), f"docs(#{_ISSUE}): checkpoint draft plan")
    pack_spec = env["artifacts"] / "spec_content.md"
    pack_spec.write_text("# Spec from the context pack\n")

    _, out = _run(env, "detect", SPEC_FILE=str(pack_spec))

    assert out["RESUMING_DRAFT"] == "true"


@_posix
def test_detect_ignores_a_finished_plan(env):
    _commit(env["work"], _OLD_PLAN, _plan(), f"docs(#{_ISSUE}): add implementation plan")

    _, out = _run(env, "detect", SPEC_FILE=_SPEC)

    assert (out["RESUMING_DRAFT"], out["PLAN_FILE"]) == ("false", "<unset>")


def test_resume_still_runs_review_and_publish():
    text = " ".join(_section("## Phase 1: LOAD", "## Phase 3: ARCHITECT REVIEW").split())
    assert "Phase 3, Phase 3.5 and Phase 4 always run in full" in text
    assert "cycle counters starting at 0" in text


def test_detect_block_sits_in_phase1():
    text = _text()
    assert text.index("## Phase 1: LOAD") < text.index("<!-- plan-draft-detect:begin -->")
    assert text.index("<!-- plan-draft-detect:end -->") < text.index("## Phase 2: PLAN WRITING")


def test_checkpoint_reruns_before_both_needs_discussion_exits():
    architect_exit = _section("4. If still not approved after 3 cycles:",
                              '### If architect returns "Approved":')
    conformance_exit = _section("b. If `CONFORMANCE_CYCLE > MAX_CYCLES`:",
                                "c. Read the MATERIAL deviation descriptions")
    for exit_text in (architect_exit, conformance_exit):
        rerun = exit_text.find("Re-run the draft checkpoint block")
        assert rerun != -1, exit_text
        assert rerun < exit_text.index("--add needs-discussion"), exit_text


# ── Phase 4: promote ─────────────────────────────────────────────────────────


def _assert_promoted(env, base, final, content):
    work = env["work"]
    assert git("rev-list", "--count", f"{base}..HEAD", cwd=work).stdout.strip() == "1"
    assert _tracked(work, "docs/superpowers/plans/") == [final]
    assert (work / final).read_text() == content
    assert _tracked(work, "docs/superpowers/plan-drafts/") == []
    changed = git("diff", "--name-only", "origin/main...HEAD", cwd=work).stdout.split()
    assert changed and all(
        p.startswith(("docs/superpowers/specs/", "docs/superpowers/plans/")) for p in changed
    ), changed
    assert _oos(env, _PLAN_ALLOWLIST, "plan") == "", "the plan OOS gate must excise nothing"


@_posix
def test_promote_moves_the_edited_draft_into_plans(env):
    work = env["work"]
    _commit(work, _DRAFT, _plan(), f"docs(#{_ISSUE}): checkpoint draft plan")
    base = _head(work)
    _write(work, _DRAFT, _plan("v2 after review"))  # uncommitted reviewer fixes

    result, out = _run(env, "promote", PLAN_FILE=_DRAFT)

    assert out["rc"] == "0", result.stderr
    assert out["PLAN_FILE"] == _FINAL
    _assert_promoted(env, base, _FINAL, _plan("v2 after review"))


@_posix
def test_promote_works_when_the_checkpoint_commit_never_happened(env):
    work = env["work"]
    base = _head(work)
    _write(work, _DRAFT, _plan("never committed"))

    result, out = _run(env, "promote", PLAN_FILE=_DRAFT)

    assert out["rc"] == "0", result.stderr
    _assert_promoted(env, base, _FINAL, _plan("never committed"))


@_posix
def test_promote_reuses_an_earlier_plan_path(env):
    work = env["work"]
    _commit(work, _OLD_PLAN, _plan("rejected"), f"docs(#{_ISSUE}): add implementation plan")
    _commit(work, _DRAFT, _plan("revised"), f"docs(#{_ISSUE}): checkpoint draft plan")
    base = _head(work)

    result, out = _run(env, "promote", PLAN_FILE=_DRAFT)

    assert out["rc"] == "0", result.stderr
    assert out["PLAN_FILE"] == _OLD_PLAN
    _assert_promoted(env, base, _OLD_PLAN, _plan("revised"))


@_posix
def test_promote_without_plan_file_deletes_nothing(env):
    # Unedited draft: the case where an unguarded `git rm -r` would delete it (#409 review).
    work = env["work"]
    _commit(work, _DRAFT, _plan(), f"docs(#{_ISSUE}): checkpoint draft plan")
    base = _head(work)

    result, _ = _run(env, "promote")

    assert result.returncode != 0
    assert "PLAN_FILE is unbound or missing" in result.stdout
    assert _head(work) == base
    assert _tracked(work, "docs/superpowers/plan-drafts/") == [_DRAFT]
    assert _tracked(work, "docs/superpowers/plans/") == []


@_posix
def test_promote_stops_before_git_rm_when_the_move_fails(env):
    work = env["work"]
    _commit(work, _DRAFT, _plan(), f"docs(#{_ISSUE}): checkpoint draft plan")
    base = _head(work)
    # A file where the plans directory should be makes `git mv` fail.
    (work / "docs" / "superpowers" / "plans").write_text("not a directory\n")

    result, _ = _run(env, "promote", PLAN_FILE=_DRAFT)

    assert result.returncode != 0
    assert _head(work) == base
    assert _tracked(work, "docs/superpowers/plan-drafts/") == [_DRAFT]


@_posix
def test_gate_lookups_tell_a_draft_from_an_earlier_plan(env):
    work = env["work"]
    _commit(work, _OLD_PLAN, _plan("rejected"), f"docs(#{_ISSUE}): add implementation plan")
    _commit(work, _DRAFT, _plan("revised"), f"docs(#{_ISSUE}): checkpoint draft plan")

    assert _gate(env, "docs/superpowers/plan-drafts/") == _DRAFT
    assert _gate(env, "docs/superpowers/plans/") == _OLD_PLAN


@_posix
def test_refine_oos_gate_excises_a_draft(env):
    work = env["work"]
    _commit(work, _DRAFT, _plan(), f"docs(#{_ISSUE}): checkpoint draft plan")

    assert _oos(env, _REFINE_ALLOWLIST, "refine") == _DRAFT
    assert _tracked(work, "docs/superpowers/plan-drafts/") == []


def test_promote_precedes_the_oos_gate():
    phase4 = _section("## Phase 4: PUBLISH")
    oos_line = next(l for l in phase4.splitlines() if "oos_excise.sh" in l and "# TARGET-PATH" in l)
    assert phase4.index("<!-- plan-draft-promote:end -->") < phase4.index(oos_line)


def test_oos_allowlist_is_not_widened():
    oos_line = next(l for l in _text().splitlines() if "oos_excise.sh" in l and "# TARGET-PATH" in l)
    match = re.search(r'oos_excise\.sh"\s+"([^"]*)"', oos_line)
    assert match and set(match.group(1).split()) == set(_PLAN_ALLOWLIST.split())
