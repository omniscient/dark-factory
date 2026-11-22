"""#535 acceptance: the gate and its failure/recovery path are documented in the existing
reference docs. Presence checks only — whether the prose is *true* is Gate 2.5's own job."""
import re
import sys
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DOCS = _REPO_ROOT / "docs"


def _read(name):
    return (_DOCS / name).read_text(encoding="utf-8")


def test_run_lifecycle_documents_nodes_gate_and_recovery():
    text = _read("run-lifecycle.md")
    for token in ("`budget-docs-conformance`", "`enforce-budget-docs-conformance`",
                  "`docs-conformance`", "`docs-conformance-gate`", "**D2.5. Docs conformance, Gate 2.5**",
                  "scripts/docs_verdict_gate_check.sh", "`doc-impact.md`", "`docs-conformance.md`",
                  "df-docs-conformance-observed", "df-docs-conformance-blocked", "**Recovery:**",
                  "| `push-and-pr` | bash, 180 s | `new` or `continue` | `conformance-gate`, `docs-conformance-gate` |"):
        assert token in text, token


def test_configuration_documents_both_keys_and_budget_scenario():
    text = _read("configuration.md")
    for token in ("### `docs_conformance`", "`docs_conformance.enabled`", "`docs_conformance.mode`",
                  "`token_optimization.budgets.docs-conformance`",
                  "`token_optimization.enforce.docs-conformance`"):
        assert token in text, token


def test_configuration_states_the_current_leaf_key_count():
    """The total is derived from config/config.yaml, not pinned here: a later change that
    adds a key only has to update the number in the doc, not this test."""
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    from docs_refs_check import _config_leaves
    config = yaml.safe_load((_REPO_ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    expected = len(list(_config_leaves(config)))
    stated = [int(n) for n in re.findall(r"\b(\d+) leaf keys\b", _read("configuration.md"))]
    assert stated, "configuration.md no longer states the leaf-key total"
    assert set(stated) == {expected}, f"configuration.md says {stated}, config/config.yaml has {expected}"


def test_features_lists_the_gate():
    text = _read("features.md")
    assert "**Docs conformance gate (Gate 2.5).**" in text
    assert "`docs_conformance.enabled` (true), `.mode` (`observe`)" in text
