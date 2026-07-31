"""Static content assertions for the conformance-gate/review-gate DAG nodes (#271),
mirroring the tests/test_push_gate_dag.py convention for testing DAG bash-node bodies
without executing them."""
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _REPO_ROOT / "workflows" / "archon-dark-factory.yaml"


def _workflow_nodes():
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return {n["id"]: n for n in data.get("nodes", []) if isinstance(n, dict) and "id" in n}


@pytest.mark.parametrize("node_id,upstream,verdict_file,label", [
    ("conformance-gate", ["conformance"], "conformance.md", "Conformance (Gate 2)"),
    ("review-gate", ["code-review", "revise-advisory"], "review.md", "Code Review (Gate 3)"),
])
class TestVerdictGateNodes:
    def test_node_exists_and_calls_script(self, node_id, upstream, verdict_file, label):
        nodes = _workflow_nodes()
        assert node_id in nodes, f"'{node_id}' node not found in workflow"
        bash = nodes[node_id]["bash"]
        assert "verdict_gate_check.sh" in bash, f"'{node_id}' must call verdict_gate_check.sh"
        assert verdict_file in bash, f"'{node_id}' must pass the '{verdict_file}' artifact path"
        assert label in bash, f"'{node_id}' must pass the gate label '{label}'"

    def test_node_depends_on_and_when(self, node_id, upstream, verdict_file, label):
        node = _workflow_nodes()[node_id]
        assert node["depends_on"] == upstream
        assert "'new'" in node["when"] and "'continue'" in node["when"]
        assert node["timeout"] == 30000
        assert "trigger_rule" not in node

    def test_node_not_wrapped_in_or_true(self, node_id, upstream, verdict_file, label):
        bash = _workflow_nodes()[node_id]["bash"]
        gate_line = next(
            line for line in bash.splitlines()
            if line.strip().startswith("bash ") and "verdict_gate_check.sh" in line
        )
        assert "|| true" not in bash, \
            f"'{node_id}': verdict_gate_check.sh's exit code IS the gate signal, must not be swallowed"
        assert gate_line  # sanity: the line was actually found


def test_push_and_pr_depends_on_conformance_gate():
    node = _workflow_nodes()["push-and-pr"]
    assert node["depends_on"] == ["conformance-gate"]


def test_status_in_review_depends_on_is_additive():
    node = _workflow_nodes()["status-in-review"]
    assert node["depends_on"] == [
        "push-and-pr", "push-resolve", "code-review", "revise-advisory", "review-gate",
    ]
    assert node["trigger_rule"] == "none_failed_min_one_success"


def test_report_depends_on_unchanged():
    """report is the node most likely to be 'helpfully' rewired during implementation —
    the spec explicitly declares it untouched (report's OR-join skips entirely, with no
    task-list edit, when conformance-gate blocks upstream)."""
    node = _workflow_nodes()["report"]
    assert node["depends_on"] == ["status-in-review", "code-review"]


def test_dag_validator_passes():
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    from check_workflow_dag import check
    errors = check(_WORKFLOW)
    assert errors == [], "\n".join(errors)


def test_dag_or_join_node_count_unchanged():
    """conformance-gate/review-gate are plain all_success nodes; adding them must not
    change the count of trigger_rule-bearing nodes check_workflow_dag.py tracks."""
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    from check_workflow_dag import REQUIRED_OR_JOIN_NODES
    nodes = _workflow_nodes()
    with_rule = [n for n in nodes.values() if "trigger_rule" in n]
    assert len(with_rule) == len(REQUIRED_OR_JOIN_NODES)
