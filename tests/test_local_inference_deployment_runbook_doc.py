"""Drift guard for docs/local-inference-deployment-runbook.md (#560).

The runbook's "Registering an entry" section embeds a worked YAML example that the doc's prose
and tests/fixtures/model_registry/model-endpoints.yaml's comment both claim stays byte-identical
to the `lan-ollama-qwen3-coder` fixture entry. Nothing enforced that claim, so the two could
silently drift apart. This test parses the runbook's fenced ```yaml block and compares it,
structurally, to the fixture entry actually used by the rest of the #560 test suite.
"""
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from factory_core.providers import model_endpoint as me  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = REPO_ROOT / "docs" / "local-inference-deployment-runbook.md"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "model_registry"

YAML_BLOCK_RE = re.compile(r"```yaml\n(.*?)\n```", re.DOTALL)


def _runbook_lan_ollama_entry():
    text = DOC_PATH.read_text(encoding="utf-8")
    blocks = YAML_BLOCK_RE.findall(text)
    assert blocks, "expected at least one fenced ```yaml block in the runbook"
    # The worked example is the first (and, today, only) yaml block with a lan-ollama entry.
    for block in blocks:
        parsed = yaml.safe_load(block)
        endpoints = (parsed or {}).get("endpoints") or []
        for entry in endpoints:
            if entry.get("id") == "lan-ollama-qwen3-coder":
                return entry
    raise AssertionError("no lan-ollama-qwen3-coder entry found in a runbook yaml block")


def test_runbook_worked_example_matches_the_fixture_entry():
    doc_entry = _runbook_lan_ollama_entry()
    fixture_entries = {e["id"]: e for e in me.load_registry(FIXTURES / me.REGISTRY_FILE)}
    fixture_entry = fixture_entries["lan-ollama-qwen3-coder"]
    assert doc_entry == fixture_entry
