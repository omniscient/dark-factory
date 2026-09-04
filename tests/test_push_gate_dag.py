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

    def test_node_guards_label_call_and_warns_on_failure(self, node_id, prefix, label, noun):
        bash = _workflow_nodes()[node_id]["bash"]
        guard = f'if python3 "$_PCLI" tracker label --id "$ISSUE" --add {label}'
        assert guard in bash, f"'{node_id}': the gate-label call must be guarded by an if/else"

        marker_call = 'tracker comment --id "$ISSUE" --marker "<!-- df-gate-label-failure -->"'
        assert marker_call in bash, \
            f"'{node_id}' must post the <!-- df-gate-label-failure --> marker comment on label failure"

        # the label-failure branch is the `if`'s else-clause, not the artifact-miss else-clause:
        # it must appear between the guard and that guard's own closing `fi`, and it must not
        # contain the artifact-miss marker (spec R6: the two markers are distinct and unrelated).
        # Match "else"/"fi" as whole stripped lines, not bare substrings — a substring search
        # for "fi" would falsely match inside "marker refinement" (the _FOOTER line), which
        # appears before the real closing fi and would truncate the branch too early.
        lines = bash.split("\n")
        guard_line_idx = next(i for i, l in enumerate(lines) if guard in l)
        else_line_idx = next(
            i for i in range(guard_line_idx, len(lines)) if lines[i].strip() == "else"
        )
        fi_line_idx = next(
            i for i in range(else_line_idx, len(lines)) if lines[i].strip() == "fi"
        )
        label_failure_branch = "\n".join(lines[else_line_idx:fi_line_idx])
        assert "df-gate-label-failure" in label_failure_branch, \
            f"'{node_id}': the label-failure branch must post the df-gate-label-failure marker"
        assert "df-refine-failure" not in label_failure_branch, \
            f"'{node_id}': the label-failure branch must not reuse the df-refine-failure marker " \
            "(that marker means 'no artifact, retry safe' and must not be overloaded)"

        # the label-failure branch warns (log echo) and does not exit 1 (push already succeeded)
        assert "WARNING:" in label_failure_branch, \
            f"'{node_id}': the label-failure branch must log a WARNING echo"
        assert "exit 1" not in label_failure_branch, \
            f"'{node_id}': the label-failure branch must not exit 1 — the push already " \
            "succeeded and is the node's load-bearing side effect"

        # the warn-advisory comment upsert is || true-guarded
        comment_pos = label_failure_branch.index(marker_call)
        comment_line_start = label_failure_branch.rfind("\n", 0, comment_pos) + 1
        comment_line_end = label_failure_branch.find("\n", comment_pos)
        comment_line = label_failure_branch[comment_line_start:comment_line_end]
        assert "|| true" in comment_line, \
            f"'{node_id}': the gate-label-failure marker comment must be || true-guarded"

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
