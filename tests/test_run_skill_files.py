from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / ".claude" / "skills" / "run"


def test_skill_md_exists_with_required_frontmatter():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: run" in text
    assert "allowed-tools:" in text
    for tool in (
        "Bash(command -v docker:*)",
        "Bash(docker info:*)",
        "Bash(docker compose -f deploy/docker-compose.yml up:*)",
        "Bash(docker compose -f deploy/docker-compose.yml logs:*)",
    ):
        assert tool in text, f"missing allowed-tools entry: {tool}"


def test_skill_bans_bare_bash_and_family_wildcards():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "Bash(*)" not in text
    assert "Bash(docker:*)" not in text
    assert "Bash(docker compose:*)" not in text


def test_documents_launch_recipe():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "docker compose -f deploy/docker-compose.yml up -d" in text
    assert "deploy/instance.env.example" in text
    assert "deploy/instances/" in text  # hard-exclusion note, per CLAUDE.md


def test_documents_daemon_probe_before_launch():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "docker info" in text
    assert "live daemon" in text.lower() or "live-daemon" in text.lower()


def test_documents_not_applicable_recipes():
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "docker-compose.preview.yml" in text
    assert "run-compose.yml" in text
    assert "not applicable" in text.lower()
