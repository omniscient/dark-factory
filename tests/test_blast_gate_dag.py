"""Static assertions for the #551 blast-radius enforcement points in the workflow DAG:
the blast-gate node, the nodes re-pointed behind it, the push-and-pr re-check and the
close-preview merge check (tests/test_verdict_gate_dag.py convention)."""
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _REPO_ROOT / "workflows" / "archon-dark-factory.yaml"


def _nodes():
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return {n["id"]: n for n in data.get("nodes", []) if isinstance(n, dict) and "id" in n}


def _index(text: str, needle: str) -> int:
    assert needle in text, f"{needle!r} not found"
    return text.index(needle)


def test_blast_gate_is_a_bash_node_after_validate():
    node = _nodes()["blast-gate"]
    assert "bash" in node and "command" not in node
    assert node["depends_on"] == ["validate"]
    assert "trigger_rule" not in node
    for intent in ("'new'", "'continue'", "'resolve'"):
        assert intent in node["when"]


def test_blast_gate_runs_the_checker_in_run_mode_with_hold():
    bash = _nodes()["blast-gate"]["bash"]
    assert "blast_gate_check.sh" in bash
    assert "--mode run" in bash and "--hold" in bash
    assert '--out "$ARTIFACTS_DIR/blast.md"' in bash
    assert "verdict_gate_check.sh" not in bash, "blast-gate must not trust an agent-written blast.md"
    assert "|| true" not in bash


def test_nodes_behind_blast_gate():
    nodes = _nodes()
    assert nodes["budget-conformance"]["depends_on"] == ["blast-gate"]
    assert nodes["conformance"]["depends_on"] == ["enforce-budget-conformance", "blast-gate"]
    assert nodes["push-resolve"]["depends_on"] == ["blast-gate"]


def test_push_and_pr_rechecks_after_archive_and_before_push():
    bash = _nodes()["push-and-pr"]["bash"]
    archive = _index(bash, 'git commit -m "docs: archive spec/plan')
    check = _index(bash, 'bash "$_BGC" --issue "$ISSUE" --mode run')
    push = _index(bash, 'git push -u origin "$BRANCH"')
    create = _index(bash, "codehost open-change")
    assert archive < check < push < create
    stub_exit = _index(bash, 'if [ "${BENCH_MODE:-}" = "stub" ]')
    assert stub_exit < check, "BENCH_MODE=stub skips the re-check along with the push"


def test_close_preview_checks_before_merge_and_pins_the_head():
    bash = _nodes()["close-preview"]["bash"]
    refusal = _index(bash, "BLOCKED — needs-discussion label present")
    check = _index(bash, "--mode close")
    merge = _index(bash, "codehost merge --id")
    assert refusal < check < merge
    assert '--match-head "$BLAST_HEAD"' in bash
    assert 'grep -c \'needs-discussion\'' in bash, "the existing label refusal stays"


def test_close_preview_deletes_the_record_after_merge():
    bash = _nodes()["close-preview"]["bash"]
    merged = _index(bash, 'echo "PR #$PR_NUM merged successfully"')
    cleanup = _index(bash, 'rm -f "$_BA/${ISSUE}.json"')
    assert merged < cleanup


def test_timeouts_fit_the_checker():
    """A node timeout is not catchable: it would end a hold halfway (plan decision 4)."""
    nodes = _nodes()
    assert nodes["blast-gate"]["timeout"] == 120000
    assert nodes["close-preview"]["timeout"] == 180000
    assert nodes["push-and-pr"]["timeout"] == 180000


def test_checker_prefers_the_baked_copy_everywhere():
    nodes = _nodes()
    for node_id in ("blast-gate", "push-and-pr", "close-preview"):
        bash = nodes[node_id]["bash"]
        assert "_BGC=/opt/dark-factory/scripts/blast_gate_check.sh" in bash, node_id
        assert '[ -f "$_BGC" ] || _BGC=' in bash, node_id
