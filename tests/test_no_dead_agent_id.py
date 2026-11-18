"""Guard against reintroducing the dead AGENT_ID role mechanism (#491)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_command_files_do_not_assign_agent_id():
    pat = re.compile(r"^\s*(export\s+)?AGENT_ID\w*=", re.M)
    offenders = [p.name for p in (ROOT / "commands").glob("*.md")
                 if pat.search(p.read_text(encoding="utf-8"))]
    assert not offenders, f"AGENT_ID assigned but nothing consumes it: {offenders}"


def test_agent_roles_script_stays_removed():
    assert not (ROOT / "scripts" / "agent_roles.sh").exists()
