"""Tests for gate_blast_radius.py — deterministic file classifier."""
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gate_blast_radius.py"

_DEFAULT_BLAST_CFG = {
    "enabled": True,
    "hotspot_score_floor": 5.0,
    "size_budget_lines": 400,
    "size_budget_blocks": False,
}
_CONFIG_REL = ".claude/skills/refinement/config.yaml"


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def _init_repo(root):
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")


def _write_config(root, config_extra=None):
    cfg = {"blast_radius": dict(_DEFAULT_BLAST_CFG)}
    if config_extra:
        cfg["blast_radius"].update(config_extra)
    path = root / _CONFIG_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(cfg))


def _parse(stdout: str) -> dict:
    result = {"_stdout": stdout}
    for line in stdout.splitlines():
        if ": " in line and not line.startswith("  "):
            k, v = line.split(": ", 1)
            result[k] = v
    return result


@pytest.fixture
def run_script(tmp_path, monkeypatch):
    """Run the gate against a fresh git repo whose config.yaml is committed on `main`
    each call (Requirement 8: blast_radius.* is read from --base-ref via `git show`,
    never the working tree, so a real repo is required even for the simplest case).

    `worktree_config_extra`, when given, overwrites the *working-tree* copy of
    config.yaml after the base-ref commit -- simulating a PR that edits its own local
    config.yaml in the same change -- without touching what was committed as the base
    ref, so tests can assert the kill switch follows the committed value only.
    """
    root = tmp_path
    _init_repo(root)
    # Hermetic: never read the image's real baked config from inside a test run (P1).
    monkeypatch.setenv("FACTORY_CONFIG_PATH", str(tmp_path / "absent-baked.yaml"))

    def _run(changed_files, hotspots_content="", lines_changed=50, config_extra=None,
              base_ref="main", worktree_config_extra=None):
        _write_config(root, config_extra)
        hf = root / "hotspots.md"
        hf.write_text(hotspots_content)
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "--allow-empty", "-m", "config")
        if worktree_config_extra is not None:
            _write_config(root, worktree_config_extra)
        proc = subprocess.run(
            [
                sys.executable, str(SCRIPT),
                "--changed-files-stdin",
                "--lines-changed", str(lines_changed),
                "--hotspots", str(hf),
                "--config", _CONFIG_REL,
                "--clone-dir", str(root),
                "--base-ref", base_ref,
            ],
            input="\n".join(changed_files),
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        return _parse(proc.stdout)

    return _run


def test_no_triggers_produces_pass(run_script):
    out = run_script(["frontend/src/components/Foo.tsx", "docs/some-doc.md"])
    assert out["STATUS"] == "PASS"
    assert out["GATE_TYPE"] == "blast"
    assert out["SEVERITY"] == "none"


def test_migration_file_triggers_human_required(run_script):
    out = run_script(["alembic/versions/abc123_add_col.py"])
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["SEVERITY"] == "critical"


def test_seed_sql_triggers_human_required(run_script):
    out = run_script(["dark-factory/seed/02_scanner_data.sql"])
    assert out["STATUS"] == "HUMAN_REQUIRED"


def test_auth_router_triggers_human_required(run_script):
    out = run_script(["backend/app/routers/auth.py"])
    assert out["STATUS"] == "HUMAN_REQUIRED"


def test_hotspot_file_above_floor_triggers_human_required(run_script):
    hotspots = "    7.2  backend/app/services/scanner.py  (2d / 10t)  200 loc\n"
    out = run_script(["backend/app/services/scanner.py"], hotspots_content=hotspots)
    assert out["STATUS"] == "HUMAN_REQUIRED"


def test_hotspot_file_below_floor_does_not_trigger(run_script):
    hotspots = "    3.1  backend/app/services/scanner.py  (1d / 5t)  200 loc\n"
    out = run_script(["backend/app/services/scanner.py"], hotspots_content=hotspots)
    assert out["STATUS"] == "PASS"


def test_size_blocking_when_enabled(run_script):
    out = run_script(
        ["frontend/src/components/Foo.tsx"],
        lines_changed=500,
        config_extra={"size_budget_lines": 400, "size_budget_blocks": True},
    )
    assert out["STATUS"] == "HUMAN_REQUIRED"


def test_size_advisory_only_by_default(run_script):
    out = run_script(["frontend/src/components/Foo.tsx"], lines_changed=500)
    assert out["STATUS"] == "PASS"


def test_lines_changed_in_artifact(run_script):
    out = run_script(["frontend/src/components/Foo.tsx"], lines_changed=123)
    assert out["LINES_CHANGED"] == "123"


def test_disabled_with_non_boundary_file_produces_skipped(run_script):
    out = run_script(["frontend/src/components/Foo.tsx"], config_extra={"enabled": False})
    assert out["STATUS"] == "SKIPPED"


def test_disabled_does_not_suppress_migration_seed_match(run_script):
    """Requirement 8 (F1): enabled:false must not suppress a migration-seed/floor
    match, or a PR could edit a boundary path and flip enabled:false in the same
    change to escape review."""
    out = run_script(["alembic/versions/abc.py"], config_extra={"enabled": False})
    assert out["STATUS"] == "HUMAN_REQUIRED"


def test_disabled_suppresses_hotspot_trigger(run_script):
    hotspots = "    7.2  backend/app/services/scanner.py  (2d / 10t)  200 loc\n"
    out = run_script(["backend/app/services/scanner.py"], hotspots_content=hotspots,
                      config_extra={"enabled": False})
    assert out["STATUS"] == "SKIPPED"


def test_disabled_suppresses_size_trigger(run_script):
    out = run_script(
        ["frontend/src/components/Foo.tsx"],
        lines_changed=500,
        config_extra={"enabled": False, "size_budget_lines": 400, "size_budget_blocks": True},
    )
    assert out["STATUS"] == "SKIPPED"


def test_kill_switch_reads_base_ref_not_working_tree(run_script):
    """Requirement 8 (F1, operator review): a PR that edits a floor path AND rewrites
    its own working-tree config.yaml to enabled:false must still be blocked -- the
    committed base-ref config (enabled:True, the fixture default) is what governs the
    migration-seed/floor match regardless (this floor match is unsuppressible anyway,
    per test_disabled_does_not_suppress_migration_seed_match above); this test isolates
    the kill switch itself by also proving the working-tree copy has zero effect."""
    out = run_script(
        [".factory/hooks/h.sh", ".claude/skills/refinement/config.yaml"],
        worktree_config_extra={"enabled": False},
    )
    assert out["STATUS"] == "HUMAN_REQUIRED"


def test_kill_switch_working_tree_cannot_re_enable_size_trigger(run_script):
    """Symmetric check: if the base ref itself has enabled:False (a legitimately
    disabled gate), a PR flipping its own working-tree copy to enabled:True must not
    re-enable the (base-ref-gated) size trigger -- the base ref still governs."""
    out = run_script(
        ["frontend/src/components/Foo.tsx"],
        lines_changed=500,
        config_extra={"enabled": False, "size_budget_lines": 400, "size_budget_blocks": True},
        worktree_config_extra={"enabled": True, "size_budget_lines": 400, "size_budget_blocks": True},
    )
    assert out["STATUS"] == "SKIPPED"


def test_kill_switch_working_tree_disable_does_not_suppress_hotspot(run_script):
    """Operator review P2: isolates the kill switch itself. Base ref enabled:True; the PR
    flips its working-tree copy to enabled:false; a hotspot file must still block with the
    hotspot label -- the working-tree value has no effect on the hotspot trigger."""
    hotspots = "    7.2  backend/app/services/scanner.py  (2d / 10t)  200 loc\n"
    out = run_script(["backend/app/services/scanner.py"], hotspots_content=hotspots,
                     worktree_config_extra={"enabled": False})
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "hotspot"


def test_kill_switch_falls_back_to_baked_when_base_ref_lacks_config(tmp_path, monkeypatch):
    """Operator review P1: on the self target .claude/skills/refinement/config.yaml is
    untracked (materialized at container start), so `git show <base-ref>:<path>` always
    misses. blast_radius.* must then come from the image-baked config (trusted, never the
    PR under review), not from hardcoded defaults."""
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    (root / "README.md").write_text("base\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base without config")
    baked = tmp_path / "baked.yaml"
    baked.write_text(yaml.dump({"blast_radius": {"size_budget_lines": 100, "size_budget_blocks": True}}))
    monkeypatch.setenv("FACTORY_CONFIG_PATH", str(baked))
    hf = root / "hotspots.md"
    hf.write_text("")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--changed-files-stdin", "--lines-changed", "200",
         "--hotspots", str(hf), "--config", _CONFIG_REL, "--clone-dir", str(root), "--base-ref", "main"],
        input="frontend/src/components/Foo.tsx", capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    out = _parse(proc.stdout)
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "size"


def test_kill_switch_base_ref_config_wins_over_baked(run_script, tmp_path, monkeypatch):
    """Operator review P1: a target that commits its config has the committed (base-ref)
    values win over the baked layer, key by key."""
    baked = tmp_path / "baked.yaml"
    baked.write_text(yaml.dump({"blast_radius": {"size_budget_lines": 100000, "size_budget_blocks": False}}))
    monkeypatch.setenv("FACTORY_CONFIG_PATH", str(baked))
    out = run_script(
        ["frontend/src/components/Foo.tsx"],
        lines_changed=200,
        config_extra={"size_budget_lines": 100, "size_budget_blocks": True},
    )
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "size"


def test_settings_json_triggers_skill_security(run_script):
    out = run_script([".claude/settings.json"])
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "skill-security"


def test_skill_script_triggers_skill_security(run_script):
    out = run_script([".claude/skills/code-review/scripts/foo.py"])
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "skill-security"


def test_factory_hooks_triggers_skill_security(run_script):
    out = run_script([".factory/hooks/validate"])
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "skill-security"


def test_skill_md_now_triggers_via_broadened_claude_floor(run_script):
    """Behavior change from the pre-#200 gate (intentional, per spec Requirement 3/4a
    and CLAUDE.md's '.claude/** self-modification mechanism' framing): the new
    FACTORY_OWNED_MIGRATION_SEED_FLOOR entry ^\\.claude/ is broader than DEFAULTS'
    existing migration_seed_auth_patterns (which deliberately exempt bare SKILL.md,
    spec Q2/A2 of #46) and now blocks any .claude/ path, including SKILL.md prose.
    DEFAULTS itself is unchanged -- test_skill_md_not_in_migration_seed_auth_patterns
    (tests/test_adapter.py) still passes -- this is the *floor* catching what DEFAULTS
    alone does not."""
    out = run_script([".claude/skills/code-review/SKILL.md"])
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "skill-security"


def test_migration_file_trigger_label_still_migration_seed(run_script):
    out = run_script(["alembic/versions/abc123_add_col.py"])
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "migration-seed"


def test_dark_factory_own_adapter_yaml_protects_skill_security():
    """Non-hermetic: run against this repo's real .factory/adapter.yaml (not the
    MarketHawk-parity default) to guard the A4 merge-semantics gap end to end.
    --base-ref HEAD: this repo's .claude/skills/refinement/config.yaml is itself
    untracked (see .git/info/exclude and this plan's Assumptions section), so
    `git show` misses and load_config falls back to the baked blast_radius block (or,
    outside the image, to {} / enabled=True defaults) -- irrelevant here since this test
    only exercises the floor-pattern match, not the kill switch."""
    repo_root = Path(SCRIPT).resolve().parents[1]
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as hf:
        hf.write("")
        hf.flush()
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--changed-files-stdin", "--lines-changed", "10",
             "--hotspots", hf.name, "--config", _CONFIG_REL,
             "--clone-dir", str(repo_root), "--base-ref", "HEAD"],
            input=".claude/settings.json",
            capture_output=True, text=True,
        )
    assert proc.returncode == 0, proc.stderr
    assert "STATUS: HUMAN_REQUIRED" in proc.stdout
    assert "TRIGGER: skill-security" in proc.stdout
