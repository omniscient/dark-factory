"""Static wiring checks for the dependency-health loop (#484): entrypoint routing,
the deps-fix CLI subcommand, and the documented config.yaml block."""
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
EP = (REPO_ROOT / "entrypoint.sh").read_text()


def test_entrypoint_maps_audit_deps_intent():
    assert '"Audit deps"|"audit deps") INTENT="audit-deps" ;;' in EP
    assert "fix-main|audit-deps|recheck) RUN_STAGE" in EP


def test_entrypoint_audit_deps_route_is_kill_switched_and_before_smoke_gate():
    route = EP.index('if [ "$INTENT" = "audit-deps" ]; then')
    block = EP[route:EP.index("\nfi\n", route)]
    assert '"${DEPS_AUDIT_ENABLED:-false}" != "true"' in block
    assert "/opt/dark-factory/scripts/factory_core/cli.py deps-fix --once" in block
    assert route < EP.index("run_hook --gate smoke-gate")


def test_cli_has_deps_fix_subcommand():
    r = subprocess.run([sys.executable, str(REPO_ROOT / "scripts/factory_core/cli.py"),
                        "deps-fix", "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "--once" in r.stdout


def test_config_documents_deps_audit_block_shipping_off():
    cfg = yaml.safe_load((REPO_ROOT / "config/config.yaml").read_text())
    block = cfg["deps_audit"]
    assert set(block) == {"enabled", "interval_hours", "model", "max_attempts",
                          "ci_wait_minutes", "auto_merge"}
    assert block["enabled"] is False and block["auto_merge"] is False
    assert block["interval_hours"] == 24 and block["model"] == "claude-opus-5-5"
    assert block["max_attempts"] == 3 and block["ci_wait_minutes"] == 20
    assert cfg["side_effect"]["phase_levels"]["audit_deps"] == 5
