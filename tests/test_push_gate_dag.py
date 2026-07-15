"""Static content assertions for the artifact-gated refine-push/plan-push-and-advance
DAG nodes (#212), mirroring the tests/test_budget_enforce_dag.py convention for testing
DAG bash-node bodies without executing them."""
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _REPO_ROOT / "workflows" / "archon-dark-factory.yaml"


def _workflow_nodes():
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return {n["id"]: n for n in data.get("nodes", []) if isinstance(n, dict) and "id" in n}


@pytest.mark.parametrize("node_id,prefix,label,noun", [
    ("refine-push", "docs/superpowers/specs/", "spec-pending-review", "spec"),
    ("plan-push-and-advance", "docs/superpowers/plans/", "plan-pending-review", "plan"),
])
class TestPushGateNodes:
    def test_node_calls_push_gate_check_script(self, node_id, prefix, label, noun):
        bash = _workflow_nodes()[node_id]["bash"]
        assert "push_gate_check.sh" in bash, f"'{node_id}' must call push_gate_check.sh"
        assert prefix in bash, f"'{node_id}' must pass artifact prefix '{prefix}'"

    def test_node_checks_needs_discussion_live(self, node_id, prefix, label, noun):
        bash = _workflow_nodes()[node_id]["bash"]
        assert "needs-discussion" in bash, \
            f"'{node_id}' must check the live needs-discussion label"

    def test_node_posts_failure_marker_on_miss(self, node_id, prefix, label, noun):
        bash = _workflow_nodes()[node_id]["bash"]
        assert "df-refine-failure" in bash, \
            f"'{node_id}' must post the <!-- df-refine-failure --> marker comment on a true miss"
        assert "tracker comment" in bash and "--marker" in bash, \
            f"'{node_id}' must use the tracker comment --marker upsert primitive"

    def test_node_gates_push_and_label_behind_artifact_check(self, node_id, prefix, label, noun):
        bash = _workflow_nodes()[node_id]["bash"]
        gate_pos = bash.find("push_gate_check.sh")
        push_pos = bash.find("git push")
        label_pos = bash.find(f"--add {label}")
        assert gate_pos != -1 and push_pos != -1 and label_pos != -1
        assert gate_pos < push_pos, \
            f"'{node_id}': push_gate_check.sh must run before git push"
        assert gate_pos < label_pos, \
            f"'{node_id}': push_gate_check.sh must run before the gate label is applied"

    def test_node_depends_on_and_when_unchanged(self, node_id, prefix, label, noun):
        node = _workflow_nodes()[node_id]
        upstream = "refine" if node_id == "refine-push" else "plan"
        intent = "refine" if node_id == "refine-push" else "plan"
        assert node["depends_on"] == [upstream]
        assert intent in node["when"]
        assert node["timeout"] == 30000


def test_dag_validator_passes():
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    from check_workflow_dag import check
    errors = check(_WORKFLOW)
    assert errors == [], "\n".join(errors)
