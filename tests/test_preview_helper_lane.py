"""Regression guards for the preview-helper shadow lane (#587) in the workflow DAG.

preview-up must keep reading only the implement run's in-process classify-preview node, so
neither node (nor any phase command) may mention the lane, and the only workflow text the lane
adds is the marked changeset block in push-and-pr (spec deviation 4: a text-reference check,
not a byte comparison of the rendered nodes)."""
import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _REPO_ROOT / "workflows" / "archon-dark-factory.yaml"
_LANE = re.compile(r"preview[-_]helper|preview-pending|PREVIEW_HELPER", re.IGNORECASE)
_BEGIN = "# >>> preview-helper changeset (#587)"
_END = "# <<< preview-helper changeset (#587)"


def _nodes():
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return {n["id"]: n for n in data.get("nodes", []) if isinstance(n, dict) and "id" in n}


def _text(node):
    return "\n".join(str(node.get(k, "")) for k in ("bash", "prompt", "command", "when"))


def test_preview_up_and_classify_preview_do_not_reference_the_lane():
    nodes = _nodes()
    for node_id in ("preview-up", "classify-preview", "preview-changeset"):
        assert not _LANE.search(_text(nodes[node_id])), f"{node_id} mentions the lane"
    for command in sorted((_REPO_ROOT / "commands").glob("*.md")):
        assert not _LANE.search(command.read_text(encoding="utf-8")), f"{command.name} mentions the lane"


def test_the_only_workflow_text_added_is_between_the_markers():
    nodes = _nodes()
    for node_id, node in nodes.items():
        text = _text(node)
        if node_id != "push-and-pr":
            assert _BEGIN not in text and not _LANE.search(text), f"{node_id} mentions the lane"
            continue
        assert text.count(_BEGIN) == 1 and text.count(_END) == 1
        before, rest = text.split(_BEGIN, 1)
        block, after = rest.split(_END, 1)
        assert not _LANE.search(before) and not _LANE.search(after), "lane text outside the markers"
        assert 'if [ "${PREVIEW_HELPER_CHANGESET_FLAG:-}" = "true" ]; then' in block
        # After the confirmed push, before the PR is looked up or opened.
        assert "git ls-remote --exit-code --heads origin" in before
        assert "codehost find-change" in after and "codehost open-change" in after
