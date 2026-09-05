import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core import side_effect

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONFIG = _REPO_ROOT / "config/config.yaml"
_WORKFLOW = _REPO_ROOT / "workflows/archon-dark-factory.yaml"

_PHASE_NODES = ("refine", "plan", "implement", "validate", "conformance", "code-review",
                 "revise-advisory")


def _config():
    return yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))


def _workflow_nodes():
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return {n["id"]: n for n in data.get("nodes", []) if isinstance(n, dict) and "id" in n}


@pytest.mark.parametrize("node_id", _PHASE_NODES)
def test_phase_node_declares_denied_tools(node_id):
    nodes = _workflow_nodes()
    assert node_id in nodes, f"DAG node '{node_id}' not found"
    assert "denied_tools" in nodes[node_id], (
        f"phase node '{node_id}' must declare denied_tools explicitly (#196/R4) — "
        f"its absence must be detectable, not silently mean 'nothing removed'"
    )


@pytest.mark.parametrize("node_id", _PHASE_NODES)
def test_phase_node_denied_tools_matches_configured_level(node_id):
    nodes = _workflow_nodes()
    phase_levels = _config()["side_effect"]["phase_levels"]
    config_key = node_id.replace("-", "_")
    level = side_effect.effective_level(phase_levels[config_key])
    expected = side_effect.profile_for(level).denied_tools
    assert nodes[node_id]["denied_tools"] == expected, (
        f"'{node_id}' denied_tools must equal profile_for(level {level}).denied_tools "
        f"({expected}); the DAG is static YAML and must be kept honest against config.yaml"
    )
