import pathlib

REPO_ROOT = pathlib.Path(__file__).parent.parent

_COMMAND_FILES = [
    "commands/dark-factory-refine.md",
    "commands/dark-factory-plan.md",
    "commands/dark-factory-conformance.md",
    "commands/dark-factory-code-review.md",
]


def test_every_command_file_references_verifier_contract_doc():
    for rel_path in _COMMAND_FILES:
        content = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
        assert "/opt/refinement-skills/VERIFIER-CONTRACT.md" in content, (
            f"{rel_path} does not reference the shared checker-invocation contract doc"
        )


def test_plan_command_references_contract_doc_at_both_pin_sites():
    content = (REPO_ROOT / "commands/dark-factory-plan.md").read_text(encoding="utf-8")
    assert content.count("/opt/refinement-skills/VERIFIER-CONTRACT.md") >= 3  # Phase 1 read + 2 pin sites


# Inline pin count per command file: refine 1, plan 2 (Phase 3 + Phase 3.5),
# conformance 1, code-review 1. VERIFIER-CONTRACT.md exists only in an image built
# after this merges, so the inline literal is the pin that actually binds until the
# rebuild (Requirement 3) — the doc reference must never replace it.
_INLINE_PIN_COUNTS = {
    "commands/dark-factory-refine.md": 1,
    "commands/dark-factory-plan.md": 2,
    "commands/dark-factory-conformance.md": 1,
    "commands/dark-factory-code-review.md": 1,
}


def test_every_command_file_keeps_inline_model_pin():
    for rel_path, n in _INLINE_PIN_COUNTS.items():
        content = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
        assert content.count("claude-opus-4-8") >= n, (
            f"{rel_path} lost its inline claude-opus-4-8 pin (expected >= {n})"
        )


def test_gate_lib_header_references_verdict_schema_docs():
    content = (REPO_ROOT / "scripts/gate_lib.sh").read_text(encoding="utf-8")
    assert "verdict.py" in content
    assert "VERIFIER-CONTRACT.md" in content


def test_verifier_contract_doc_exists_and_documents_env_contract():
    content = (REPO_ROOT / "refinement-skills/VERIFIER-CONTRACT.md").read_text(encoding="utf-8")
    for token in ("CLONE_DIR", "ARTIFACTS_DIR", "ISSUE_NUM", "FACTORY_REPO_SLUG", "LOOP_NAME",
                  "ORIGIN:", "target-loop:"):
        assert token in content


def test_verifier_contract_has_per_checker_pin_table():
    content = (REPO_ROOT / "refinement-skills/VERIFIER-CONTRACT.md").read_text(encoding="utf-8")
    assert "| Checker pair | Gating model (pin) | Shadow model" in content
    assert "${CONFORMANCE_SHADOW_MODEL-claude-fable-5-1}" in content


def test_verifier_contract_has_refusal_to_uncertain_clause():
    content = (REPO_ROOT / "refinement-skills/VERIFIER-CONTRACT.md").read_text(encoding="utf-8")
    assert "maps to `UNCERTAIN`, never `PASS`" in content


def test_verifier_contract_documents_shadow_verdict_mapping():
    content = (REPO_ROOT / "refinement-skills/VERIFIER-CONTRACT.md").read_text(encoding="utf-8")
    for token in ("SHADOW_MODEL", "SHADOW_STATUS", "SHADOW_FINDINGS_COUNT", "SHADOW_SEVERITY"):
        assert token in content
    assert "Material divergence" in content and "BLOCKED" in content
