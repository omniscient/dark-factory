"""#535 acceptance: the gate and its failure/recovery path are documented in the existing
reference docs. Presence checks only — whether the prose is *true* is Gate 2.5's own job."""
from pathlib import Path

_DOCS = Path(__file__).resolve().parents[1] / "docs"


def _read(name):
    return (_DOCS / name).read_text(encoding="utf-8")


def test_run_lifecycle_documents_nodes_gate_and_recovery():
    text = _read("run-lifecycle.md")
    for token in ("`budget-docs-conformance`", "`enforce-budget-docs-conformance`",
                  "`docs-conformance`", "`docs-conformance-gate`", "**D2.5. Docs conformance, Gate 2.5**",
                  "scripts/docs_verdict_gate_check.sh", "`doc-impact.md`", "`docs-conformance.md`",
                  "df-docs-conformance-observed", "df-docs-conformance-blocked", "**Recovery:**",
                  "| `push-and-pr` | bash, 30 s | `new` or `continue` | `conformance-gate`, `docs-conformance-gate` |"):
        assert token in text, token


def test_configuration_documents_both_keys_and_budget_scenario():
    text = _read("configuration.md")
    for token in ("### `docs_conformance`", "`docs_conformance.enabled`", "`docs_conformance.mode`",
                  "`token_optimization.budgets.docs-conformance`",
                  "`token_optimization.enforce.docs-conformance`", "99 leaf keys"):
        assert token in text, token


def test_features_lists_the_gate():
    text = _read("features.md")
    assert "**Docs conformance gate (Gate 2.5).**" in text
    assert "`docs_conformance.enabled` (true), `.mode` (`observe`)" in text
