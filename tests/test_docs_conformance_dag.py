"""DAG wiring for Gate 2.5 (#535): budget-docs-conformance -> enforce-budget-docs-conformance
-> docs-conformance -> docs-conformance-gate sit between conformance-gate and push-and-pr,
new/continue only, and push-and-pr (a plain all_success node) depends on both gates.

The executing tests run the real docs-conformance-gate bash body against a stub clone, so
"no PR is created" is proven the same way #271 proved it for conformance-gate: the gate
node exits 1, and push-and-pr — which has no trigger_rule — never runs after a failed
upstream.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _REPO_ROOT / "workflows" / "archon-dark-factory.yaml"
_NEW_OR_CONTINUE = "$parse-intent.output.intent == 'new' || $parse-intent.output.intent == 'continue'"


def _nodes():
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return {n["id"]: n for n in data.get("nodes", []) if isinstance(n, dict) and "id" in n}


def test_chain_order_and_dependencies():
    n = _nodes()
    assert n["budget-docs-conformance"]["depends_on"] == ["conformance-gate"]
    assert n["enforce-budget-docs-conformance"]["depends_on"] == ["budget-docs-conformance"]
    assert n["docs-conformance"]["depends_on"] == ["enforce-budget-docs-conformance", "conformance-gate"]
    assert n["docs-conformance-gate"]["depends_on"] == ["docs-conformance"]


def test_push_and_pr_requires_both_gates_with_all_success():
    node = _nodes()["push-and-pr"]
    assert node["depends_on"] == ["conformance-gate", "docs-conformance-gate"]
    assert "trigger_rule" not in node, "a skip-tolerant rule would let a PR open past a failed gate"


@pytest.mark.parametrize("node_id", [
    "budget-docs-conformance", "enforce-budget-docs-conformance",
    "docs-conformance", "docs-conformance-gate",
])
def test_new_continue_only_and_no_trigger_rule(node_id):
    node = _nodes()[node_id]
    assert node["when"] == _NEW_OR_CONTINUE
    assert "trigger_rule" not in node


def test_docs_conformance_command_node():
    node = _nodes()["docs-conformance"]
    assert node["command"] == "dark-factory-docs-conformance"
    assert node["denied_tools"] == [], "Requirement 16: no tool-restriction change in this ticket"
    assert node["idle_timeout"] == 600000
    assert (_REPO_ROOT / "commands" / "dark-factory-docs-conformance.md").is_file()


def test_gate_node_calls_strict_script_not_shared_one():
    bash = _nodes()["docs-conformance-gate"]["bash"]
    assert "docs_verdict_gate_check.sh" in bash
    assert "/verdict_gate_check.sh" not in bash, "verdict_gate_check.sh passes ERROR (Requirement 3)"
    assert "docs-conformance.md" in bash and "Docs Conformance (Gate 2.5)" in bash
    # Mode read delegates to the single shared normalizer (factory_core/cli.py
    # docs-conformance-config, #535) instead of duplicating the enabled/mode parsing
    # inline — the same helper the command file's two read sites call.
    assert "docs-conformance-config" in bash and 'echo "true observe"' in bash
    assert "|| true" not in bash, "the gate script's exit code IS the gate signal"
    assert _nodes()["docs-conformance-gate"]["timeout"] == 30000


def test_budget_nodes_use_docs_conformance_scenario():
    n = _nodes()
    assert "--scenario docs-conformance" in n["budget-docs-conformance"]["bash"]
    assert "--doc-impact-file" in n["budget-docs-conformance"]["bash"]
    assert "budget_gate.sh\" docs-conformance || true" in n["enforce-budget-docs-conformance"]["bash"]


def test_resolve_path_unchanged():
    assert _nodes()["push-resolve"]["depends_on"] == ["validate"]


def test_dag_validators_pass():
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    from check_workflow_dag import check as check_dag
    from check_workflow_when import check as check_when
    assert check_dag(_WORKFLOW) == []
    assert check_when(_WORKFLOW) == []


# ── executing the real gate node body ────────────────────────────────────────

_PRELUDE = r'''
python3() {
  case "$*" in
    *providers/cli.py*)
      printf 'CALL %s\n' "$*" >> "$PCLI_LOG"
      case "$*" in *"tracker get"*) printf '{"labels":[]}\n' ;; esac
      return 0 ;;
    *factory_core/cli.py*marker*) echo "*footer*" ;;
    *) command python3 "$@" ;;
  esac
}
export -f python3
'''


@pytest.fixture()
def clone(tmp_path):
    if os.name != "posix" or any(shutil.which(t) is None for t in ("bash", "jq")):
        pytest.skip("needs a POSIX host with bash and jq")
    root = tmp_path / "clone"
    (root / "dark-factory").mkdir(parents=True)
    (root / "dark-factory" / "scripts").symlink_to(_REPO_ROOT / "scripts")
    (root / ".claude" / "skills" / "refinement").mkdir(parents=True)
    arts = tmp_path / "artifacts"
    arts.mkdir()
    (arts / "issue.json").write_text('{"resolved_number": 535}')
    return root, arts


def _run_gate(clone, mode_yaml, verdict):
    root, arts = clone
    cfg = root / ".claude" / "skills" / "refinement" / "config.yaml"
    if mode_yaml is None:
        cfg.unlink(missing_ok=True)
    else:
        cfg.write_text(mode_yaml)
    vf = arts / "docs-conformance.md"
    vf.unlink(missing_ok=True)
    if verdict is not None:
        vf.write_text(verdict)
    log = arts / "pcli.log"
    log.write_text("")
    env = dict(os.environ, CLONE_DIR=str(root), ARTIFACTS_DIR=str(arts), PCLI_LOG=str(log))
    bash = _nodes()["docs-conformance-gate"]["bash"]
    proc = subprocess.run(["bash", "-c", _PRELUDE + "\n" + bash], cwd=str(root),
                          env=env, capture_output=True, text=True)
    return proc.returncode, log.read_text()


_ENFORCE = "docs_conformance:\n  enabled: true\n  mode: enforce\n"
_OBSERVE = "docs_conformance:\n  enabled: true\n  mode: observe\n"


@pytest.mark.parametrize("verdict", [
    None, "garbage\n", "STATUS: ERROR\n", "STATUS: BLOCKED\nGATE_TYPE: docs-conformance\n",
])
def test_enforce_config_blocks_push_and_pr(clone, verdict):
    rc, _ = _run_gate(clone, _ENFORCE, verdict)
    assert rc == 1, "gate exit 1 skips push-and-pr: no PR is created"


@pytest.mark.parametrize("verdict", ["STATUS: PASS\n", "STATUS: SKIPPED\n"])
def test_enforce_config_passes_clean_verdicts(clone, verdict):
    rc, _ = _run_gate(clone, _ENFORCE, verdict)
    assert rc == 0


@pytest.mark.parametrize("cfg", [_OBSERVE, "docs_conformance:\n  enabled: true\n", "{}\n", ": not yaml [\n", None])
def test_observe_default_never_blocks(clone, cfg):
    rc, log = _run_gate(clone, cfg, "STATUS: BLOCKED\n")
    assert rc == 0, "observe (and every config-read failure) must not withhold the PR"
    assert log == ""



# ── read-only baseline: HEAD and `git status` before vs after the docs-conformance node ──
# budget-docs-conformance records both; docs-conformance-gate compares them. A tree that was
# already dirty before the node (regen-codeindex leaves codeindex.json, symbolindex.json,
# .codeindex/ and a modified hotspots file behind on every run) is the baseline, not a
# violation.

def _git(root, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          cwd=str(root), capture_output=True, text=True, check=True).stdout


@pytest.fixture()
def gitclone(clone):
    if shutil.which("git") is None:
        pytest.skip("needs git")
    root, arts = clone
    _git(root, "init", "-q", "-b", "main")
    (root / "docs").mkdir()
    (root / "docs" / "codeindex-hotspots.md").write_text("old\n")
    (root / "README.md").write_text("x\n")
    # entrypoint.sh git-excludes what it stages into the clone.
    with open(root / ".git" / "info" / "exclude", "a") as fh:
        fh.write("dark-factory/scripts\n.claude/skills/refinement/config.yaml\n")
    _git(root, "add", "README.md", "docs/codeindex-hotspots.md")
    _git(root, "commit", "-q", "-m", "base")
    # What regen-codeindex leaves behind before Gate 2.5 runs.
    (root / "codeindex.json").write_text("{}\n")
    (root / "symbolindex.json").write_text("{}\n")
    (root / "docs" / "codeindex-hotspots.md").write_text("regenerated\n")
    return root, arts


def _record_baseline(gitclone):
    root, arts = gitclone
    env = dict(os.environ, CLONE_DIR=str(root), ARTIFACTS_DIR=str(arts))
    bash = _nodes()["budget-docs-conformance"]["bash"]
    proc = subprocess.run(["bash", "-c", bash], cwd=str(root), env=env,
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def _run_gate_err(clone, mode_yaml, verdict):
    root, arts = clone
    (root / ".claude" / "skills" / "refinement" / "config.yaml").write_text(mode_yaml)
    (arts / "docs-conformance.md").write_text(verdict)
    log = arts / "pcli.log"
    log.write_text("")
    env = dict(os.environ, CLONE_DIR=str(root), ARTIFACTS_DIR=str(arts), PCLI_LOG=str(log))
    bash = _nodes()["docs-conformance-gate"]["bash"]
    proc = subprocess.run(["bash", "-c", _PRELUDE + "\n" + bash], cwd=str(root),
                          env=env, capture_output=True, text=True)
    return proc.returncode, log.read_text(), proc.stderr


def test_budget_node_records_head_and_status_baseline(gitclone):
    root, arts = gitclone
    _record_baseline(gitclone)
    assert (arts / "docs-conformance-pre-head.txt").read_text().strip() == _git(root, "rev-parse", "HEAD").strip()
    status = (arts / "docs-conformance-pre-status.txt").read_text()
    assert "codeindex.json" in status and "docs/codeindex-hotspots.md" in status


@pytest.mark.parametrize("cfg", [_OBSERVE, _ENFORCE])
def test_pre_existing_dirty_tree_is_the_baseline_not_a_violation(gitclone, cfg):
    _record_baseline(gitclone)
    rc, log, err = _run_gate_err(gitclone, cfg, "STATUS: PASS\n")
    assert rc == 0, f"a tree that was dirty before docs-conformance must not block: {err}"
    assert log == ""


def _move_head(root):
    (root / "README.md").write_text("changed by the read-only node\n")
    _git(root, "commit", "-q", "-am", "unreviewed")


def _change_status(root):
    (root / "stray.txt").write_text("left by the read-only node\n")


@pytest.mark.parametrize("tamper", [_move_head, _change_status])
def test_enforce_blocks_and_comments_when_branch_changed_during_review(gitclone, tamper):
    root, _ = gitclone
    _record_baseline(gitclone)
    tamper(root)
    rc, log, err = _run_gate_err(gitclone, _ENFORCE, "STATUS: PASS\n")
    assert rc == 1, "a commit made after Gate 2 must not reach push-and-pr in enforce mode"
    assert "tracker comment" in log and "--marker <!-- df-push-gate-failure -->" in log
    assert "read-only" in err


@pytest.mark.parametrize("tamper", [_move_head, _change_status])
def test_observe_logs_but_never_blocks_when_branch_changed_during_review(gitclone, tamper):
    root, _ = gitclone
    _record_baseline(gitclone)
    tamper(root)
    rc, log, err = _run_gate_err(gitclone, _OBSERVE, "STATUS: PASS\n")
    assert rc == 0, "Requirement 13: observe never blocks"
    assert log == "", "observe makes no tracker call"
    assert "read-only" in err and "enforce would block" in err


def test_missing_baseline_skips_the_comparison(gitclone):
    """No pre-head file (budget node failed to write it): nothing to compare against."""
    rc, log, _ = _run_gate_err(gitclone, _ENFORCE, "STATUS: PASS\n")
    assert rc == 0 and log == ""
