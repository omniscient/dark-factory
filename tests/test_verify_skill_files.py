from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / ".claude" / "skills" / "verify"


def test_skill_md_exists_with_required_frontmatter():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: verify" in text
    assert "allowed-tools:" in text
    for tool in (
        "Bash(PYTHONPATH=scripts python -m pytest tests/:*)",
        "Bash(python -m pytest tests/:*)",
        "Bash(python scripts/check_workflow_dag.py:*)",
        "Bash(python scripts/check_workflow_when.py:*)",
        "Bash(bash tests/test_identity.sh:*)",
        "Bash(bash tests/test_hooks.sh:*)",
        "Bash(bash tests/test_smoke_gate.sh:*)",
        "Bash(bash tests/test_run_compose.sh:*)",
        "Bash(docker compose -f run-compose.yml config:*)",
        "Bash(command -v docker:*)",
        "Bash(docker compose version:*)",
    ):
        assert tool in text, f"missing allowed-tools entry: {tool}"


def test_skill_bans_bare_bash_and_family_wildcards():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "Bash(*)" not in text
    assert "Bash(gh:*)" not in text
    assert "Bash(python:*)" not in text
    assert "Bash(docker:*)" not in text
    assert "Bash(docker compose:*)" not in text


def test_documents_test_suite_recipe_both_invocations():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "PYTHONPATH=scripts python -m pytest tests/ -v" in text
    assert "python -m pytest tests/ -q" in text
    assert ".factory/hooks/smoke-gate" in text
    assert ".factory/hooks/validate" in text


def test_documents_workflow_gates():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "scripts/check_workflow_dag.py" in text
    assert "scripts/check_workflow_when.py" in text


def test_documents_ci_parity_checks():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    for f in (
        "test_identity.sh",
        "test_hooks.sh",
        "test_smoke_gate.sh",
        "test_run_compose.sh",
    ):
        assert f in text


def test_documents_run_compose_probe_is_cli_only_tier():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "docker compose version" in text
    assert "CLI-only" in text


def test_documents_docker_build_as_ci_only():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "docker build" in text
    assert "CI-only" in text


def test_documents_docker_tier_table():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "live daemon" in text.lower() or "live-daemon" in text.lower()
    assert "CLI-only" in text


def test_documents_evaluation_note():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "cost-report" in text or "cost report" in text
    assert "run-record" in text
