"""Executing tests for the refine-push/plan-push-and-advance DAG nodes (#473).

tests/test_push_gate_dag.py asserts string/ordering properties of the nodes' bash text —
exactly the style of check that missed #473 (the text already "contained" `git push`
before the label call; order alone never proved the exit status was checked). These
tests run the real extracted bash against a real (local) bare origin instead:
push_gate_check.sh, `git push` and `git ls-remote` all execute for real; only the
tracker CLI (providers/cli.py) is replaced, by the `python3` shell-function override
precedent from tests/test_scheduler_comment_verdict.py, extended to log every call.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _REPO_ROOT / "workflows" / "archon-dark-factory.yaml"
_ISSUE = "473"

pytestmark = pytest.mark.skipif(
    os.name != "posix"
    or any(shutil.which(tool) is None for tool in ("bash", "git", "jq", "timeout")),
    reason="needs a POSIX host with bash, git, jq and coreutils timeout (Linux/CI-only harness)",
)

# Prepended to the node's bash in ONE `bash -c` string: an exported shell function is
# inherited only by child shells of the shell that defined it (post_marker_comment.sh,
# push_gate_check.sh), never via subprocess env=. Every providers/cli.py subcommand the
# node can reach (tracker label / comment / get) is intercepted and logged; a
# --body-file's contents are copied into the log because post_marker_comment.sh deletes
# the temp file right after the call. No `set -u`: the node's bodies reference
# $FACTORY_REPO_SLUG (a dummy is exported anyway).
_PRELUDE = r'''
python3() {
  case "$*" in
    *providers/cli.py*)
      printf 'CALL %s\n' "$*" >> "$PCLI_LOG"
      local _prev="" _a
      for _a in "$@"; do
        if [ "$_prev" = "--body-file" ]; then cat "$_a" >> "$PCLI_LOG"; printf '\n' >> "$PCLI_LOG"; fi
        _prev="$_a"
      done
      case "$*" in *"tracker get"*) printf '{"labels":[]}\n' ;; esac
      return 0 ;;
    *) command python3 "$@" ;;
  esac
}
export -f python3
'''

_NODES = [
    ("refine-push", "docs/superpowers/specs/", "spec-pending-review"),
    ("plan-push-and-advance", "docs/superpowers/plans/", "plan-pending-review"),
]


def _node_bash(node_id):
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in data.get("nodes", []) if isinstance(n, dict) and "id" in n}
    return nodes[node_id]["bash"]


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def _write_hook(bare, name, body):
    hook = bare / "hooks" / name
    hook.write_text("#!/bin/sh\n" + body)
    hook.chmod(0o755)


@pytest.fixture()
def env(tmp_path):
    """Bare origin + working tree (tests/test_push_gate_check.py's fixture shape) on a
    refine branch, plus the CLONE_DIR/ARTIFACTS_DIR layout the node reads."""
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
    git("branch", "--set-upstream-to=origin/main", "main", cwd=work)
    git("checkout", "-b", f"refine/issue-{_ISSUE}-test", cwd=work)

    # The node resolves scripts as "${CLONE_DIR:-.}/dark-factory/scripts/..." — point
    # that at this checkout explicitly rather than relying on its basename.
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / "dark-factory").symlink_to(_REPO_ROOT, target_is_directory=True)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "issue.json").write_text(json.dumps({"resolved_number": int(_ISSUE)}))
    return {"tmp": tmp_path, "bare": bare, "work": work, "clone": clone,
            "artifacts": artifacts, "log": tmp_path / "pcli.log"}


def _commit_artifact(work, prefix):
    d = work / prefix
    d.mkdir(parents=True, exist_ok=True)
    (d / f"2026-10-21-issue-{_ISSUE}-test.md").write_text(f"# Doc\n\n**Issue:** #{_ISSUE}\n")
    git("add", prefix, cwd=work)
    git("commit", "-m", f"docs: artifact (#{_ISSUE})", cwd=work)


def _run_node(env, node_id):
    run_env = dict(os.environ)
    run_env.update({
        "CLONE_DIR": str(env["clone"]),
        "ARTIFACTS_DIR": str(env["artifacts"]),
        "PCLI_LOG": str(env["log"]),
        "FACTORY_REPO_SLUG": "test/repo",
    })
    result = subprocess.run(
        ["bash", "-c", _PRELUDE + _node_bash(node_id)],
        cwd=str(env["work"]), env=run_env, capture_output=True, text=True, timeout=120,
    )
    log = env["log"].read_text() if env["log"].exists() else ""
    pr_path = env["artifacts"] / "push-result.json"
    push_result = json.loads(pr_path.read_text()) if pr_path.exists() else None
    return result, log, push_result


def _label_calls(log):
    return [l for l in log.splitlines() if l.startswith("CALL ") and "tracker label" in l]


@pytest.mark.parametrize("node_id,prefix,label", _NODES)
class TestPushVerification:
    def test_push_succeeds_applies_label_once(self, env, node_id, prefix, label):
        _commit_artifact(env["work"], prefix)
        head = git("rev-parse", "HEAD", cwd=env["work"]).stdout.strip()

        result, log, push_result = _run_node(env, node_id)

        assert result.returncode == 0, result.stderr
        calls = _label_calls(log)
        assert len(calls) == 1 and f"--add {label}" in calls[0], log
        assert "<!-- df-push-failure -->" not in log
        assert push_result == {
            "node": node_id, "branch": f"refine/issue-{_ISSUE}-test", "pushed": True,
            "local_sha": head, "remote_sha": head, "error": "",
        }

    def test_push_rejected_posts_marker_and_skips_label(self, env, node_id, prefix, label):
        _commit_artifact(env["work"], prefix)
        _write_hook(env["bare"], "pre-receive", 'echo "rejected by test hook" >&2\nexit 1\n')

        result, log, push_result = _run_node(env, node_id)

        assert result.returncode == 0, result.stderr
        assert _label_calls(log) == [], log
        assert "--marker <!-- df-push-failure -->" in log
        assert "rejected by test hook" in log, "captured push stderr must reach the comment"
        assert push_result is not None and push_result["pushed"] is False
        assert "rejected by test hook" in push_result["error"]

    def test_detached_head_posts_marker_and_skips_label(self, env, node_id, prefix, label):
        _commit_artifact(env["work"], prefix)
        git("checkout", "--detach", cwd=env["work"])

        result, log, push_result = _run_node(env, node_id)

        assert result.returncode == 0, result.stderr
        assert _label_calls(log) == [], log
        assert "--marker <!-- df-push-failure -->" in log
        assert "detached HEAD" in log
        assert push_result is not None and push_result["pushed"] is False
        assert push_result["branch"] == ""
        assert "detached HEAD" in push_result["error"]
        # nothing reached the remote
        assert git("ls-remote", "--heads", str(env["bare"]), f"refine/issue-{_ISSUE}-test",
                   cwd=env["work"]).stdout.strip() == ""

    def test_remote_sha_mismatch_posts_marker_and_skips_label(self, env, node_id, prefix, label):
        _commit_artifact(env["work"], prefix)
        head = git("rev-parse", "HEAD", cwd=env["work"]).stdout.strip()
        main_sha = git("rev-parse", "origin/main", cwd=env["work"]).stdout.strip()
        # The push is accepted (exit 0), then the ref is moved to a different, existing
        # commit — so only the ls-remote sha comparison can catch it. An out-of-band
        # divergent commit would instead make the push itself fail (non-fast-forward).
        _write_hook(env["bare"], "post-update",
                    'for r in "$@"; do git update-ref "$r" refs/heads/main; done\n')

        result, log, push_result = _run_node(env, node_id)

        assert result.returncode == 0, result.stderr
        assert _label_calls(log) == [], log
        assert "--marker <!-- df-push-failure -->" in log
        assert head in log and main_sha in log, "both shas must be shown"
        assert push_result is not None and push_result["pushed"] is False
        assert push_result["local_sha"] == head
        assert push_result["remote_sha"] == main_sha
