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
