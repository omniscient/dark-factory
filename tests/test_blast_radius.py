"""Tests for gate_blast_radius.py — deterministic file classifier."""
import json
import os
import shutil
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


def test_blast_approvers_key_is_a_hard_trigger(run_script):
    """#551 Requirement 9: changing the approver key set always needs a human."""
    out = run_script([".factory/blast-approvers.asc"])
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert ".factory/blast-approvers.asc (category: migration-seed)" in out["_stdout"]


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


# ── Boundary-escalation semantic diff (#200/A6, Requirement 5) ─────────────────

_BASE_LOOP = {
    "name": "refine-loop",
    "purpose": "test loop",
    "side_effect_level": 2,
    "discovery": {"trigger": "label", "inputs": ["issue"]},
    "handoff": {"manifest": "manifest.json", "outputs": ["out.md"]},
    "verification": {"verifier": "scripts/verify.sh", "stop_condition": "scripts/stop.sh"},
    "persistence": {"artifacts": ["out.md"]},
    "scheduling": {"failure_behavior": "retry"},
}


def _adapter_doc(safety=None, loops=None):
    return {"schema_version": 1, "safety": safety or {}, "loops": loops or []}


@pytest.fixture
def adapter_diff_run(tmp_path, monkeypatch):
    """Commit a base `.factory/adapter.yaml` on `main`, branch, then hand back a
    callable that commits the PR's version on that branch and invokes the gate -- the
    semantic diff compares --base-ref vs. HEAD (Requirement 5), so both sides must be
    real commits."""
    root = tmp_path
    _init_repo(root)
    monkeypatch.setenv("FACTORY_CONFIG_PATH", str(tmp_path / "absent-baked.yaml"))  # hermetic (P1)

    def _setup(base_adapter):
        _write_config(root)
        (root / "hotspots.md").write_text("")
        d = root / ".factory"; d.mkdir(exist_ok=True)
        text = base_adapter if isinstance(base_adapter, str) else yaml.dump(base_adapter)
        (d / "adapter.yaml").write_text(text)
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "base")
        _git(root, "checkout", "-q", "-b", "pr")

        def _run(new_adapter, changed_files=None):
            text = new_adapter if isinstance(new_adapter, str) else yaml.dump(new_adapter)
            (d / "adapter.yaml").write_text(text)
            _git(root, "add", "-A")
            _git(root, "commit", "-q", "--allow-empty", "-m", "pr")
            files = changed_files if changed_files is not None else [".factory/adapter.yaml"]
            proc = subprocess.run(
                [
                    sys.executable, str(SCRIPT),
                    "--changed-files-stdin",
                    "--lines-changed", "10",
                    "--hotspots", str(root / "hotspots.md"),
                    "--config", _CONFIG_REL,
                    "--clone-dir", str(root),
                    "--base-ref", "main",
                ],
                input="\n".join(files),
                capture_output=True, text=True,
            )
            assert proc.returncode == 0, proc.stderr
            return _parse(proc.stdout)

        return _run

    return _setup


def test_boundary_diff_no_op_no_false_positive(adapter_diff_run):
    base = _adapter_doc(loops=[dict(_BASE_LOOP)])
    run = adapter_diff_run(base)
    out = run(_adapter_doc(loops=[dict(_BASE_LOOP)]))
    assert out["STATUS"] == "PASS"
    assert "boundary-escalation" not in out["_stdout"]


def test_boundary_diff_skipped_when_adapter_not_changed(adapter_diff_run):
    run = adapter_diff_run(_adapter_doc(loops=[dict(_BASE_LOOP)]))
    out = run(_adapter_doc(safety={"x": "y"}), changed_files=["frontend/src/Foo.tsx"])
    assert out["STATUS"] == "PASS"


def test_boundary_diff_safety_block_change_flags(adapter_diff_run):
    run = adapter_diff_run(_adapter_doc(safety={"critical_diff_paths": ["^a/"]}))
    out = run(_adapter_doc(safety={"critical_diff_paths": ["^a/", "^b/"]}))
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["TRIGGER"] == "boundary-escalation"
    assert "safety: block changed" in out["_stdout"]


def test_boundary_diff_side_effect_level_increase_flags(adapter_diff_run):
    run = adapter_diff_run(_adapter_doc(loops=[dict(_BASE_LOOP, side_effect_level=2)]))
    out = run(_adapter_doc(loops=[dict(_BASE_LOOP, side_effect_level=3)]))
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert "side_effect_level increased 2 -> 3" in out["_stdout"]


def test_boundary_diff_side_effect_level_decrease_not_flagged(adapter_diff_run):
    run = adapter_diff_run(_adapter_doc(loops=[dict(_BASE_LOOP, side_effect_level=3)]))
    out = run(_adapter_doc(loops=[dict(_BASE_LOOP, side_effect_level=2)]))
    assert out["STATUS"] == "PASS"


def test_boundary_diff_verifier_change_flags(adapter_diff_run):
    run = adapter_diff_run(_adapter_doc(loops=[dict(_BASE_LOOP)]))
    new_loop = dict(_BASE_LOOP,
                     verification={"verifier": "scripts/other.sh", "stop_condition": "scripts/stop.sh"})
    out = run(_adapter_doc(loops=[new_loop]))
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert "verification.verifier changed" in out["_stdout"]


def test_boundary_diff_stop_condition_change_flags(adapter_diff_run):
    run = adapter_diff_run(_adapter_doc(loops=[dict(_BASE_LOOP)]))
    new_loop = dict(_BASE_LOOP,
                     verification={"verifier": "scripts/verify.sh", "stop_condition": "scripts/other_stop.sh"})
    out = run(_adapter_doc(loops=[new_loop]))
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert "verification.stop_condition changed" in out["_stdout"]


def test_boundary_diff_new_factory_owned_loop_flags(adapter_diff_run):
    run = adapter_diff_run(_adapter_doc(loops=[]))
    new_loop = dict(_BASE_LOOP, name="new-loop", side_effect_level=4,
                     budget_caps={"max_tokens": 1000}, human_checkpoint="required")
    out = run(_adapter_doc(loops=[new_loop]))
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert "new loop declares side_effect_level 4" in out["_stdout"]


def test_boundary_diff_new_low_level_loop_not_flagged(adapter_diff_run):
    run = adapter_diff_run(_adapter_doc(loops=[]))
    new_loop = dict(_BASE_LOOP, name="new-loop", side_effect_level=2)
    out = run(_adapter_doc(loops=[new_loop]))
    assert out["STATUS"] == "PASS"


def test_boundary_diff_factory_owned_loop_entry_change_flags(adapter_diff_run):
    """F7: any change to a >=4-level loop's entry is a finding, not just sel/verifier."""
    old_loop = dict(_BASE_LOOP, name="lvl4", side_effect_level=4,
                     budget_caps={"max_tokens": 1000}, human_checkpoint="required")
    run = adapter_diff_run(_adapter_doc(loops=[old_loop]))
    new_loop = dict(old_loop, purpose="changed purpose")
    out = run(_adapter_doc(loops=[new_loop]))
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert "factory-owned loop" in out["_stdout"]


def test_boundary_diff_factory_owned_loop_removal_flags(adapter_diff_run):
    old_loop = dict(_BASE_LOOP, name="lvl4", side_effect_level=4,
                     budget_caps={"max_tokens": 1000}, human_checkpoint="required")
    run = adapter_diff_run(_adapter_doc(loops=[old_loop]))
    out = run(_adapter_doc(loops=[]))
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert "removed" in out["_stdout"]


def test_boundary_diff_duplicate_loop_name_fails_closed(adapter_diff_run):
    """A duplicate loop name would silently collapse in the {name: loop} comparison
    maps -- adapter.py::load()'s own duplicate check must also gate this snapshot."""
    run = adapter_diff_run(_adapter_doc(loops=[dict(_BASE_LOOP)]))
    dup = [dict(_BASE_LOOP), dict(_BASE_LOOP, side_effect_level=3)]
    out = run(_adapter_doc(loops=dup))
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert "unparseable" in out["_stdout"]


def test_boundary_diff_reads_head_not_the_working_tree(tmp_path, monkeypatch):
    """An escalation that is committed on the PR branch but reverted in the working
    tree is still what gets merged. Reading the tree for the 'new' side reported
    PASS/0 findings on exactly that shape (operator review of PR #410)."""
    root = tmp_path
    _init_repo(root)
    monkeypatch.setenv("FACTORY_CONFIG_PATH", str(tmp_path / "absent-baked.yaml"))
    _write_config(root)
    (root / "hotspots.md").write_text("")
    d = root / ".factory"; d.mkdir()
    base_text = yaml.dump(_adapter_doc(loops=[dict(_BASE_LOOP, name="lvl4", side_effect_level=4,
                                                    budget_caps={"max_tokens": 1000},
                                                    human_checkpoint="required")]))
    (d / "adapter.yaml").write_text(base_text)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "checkout", "-q", "-b", "pr")
    (d / "adapter.yaml").write_text(yaml.dump(_adapter_doc(loops=[
        dict(_BASE_LOOP, name="lvl4", side_effect_level=5,
             budget_caps={"max_tokens": 1000}, human_checkpoint="required")])))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "escalate to level 5")
    (d / "adapter.yaml").write_text(base_text)  # working tree hides the escalation
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--changed-files-stdin", "--lines-changed", "10",
         "--hotspots", str(root / "hotspots.md"), "--config", _CONFIG_REL,
         "--clone-dir", str(root), "--base-ref", "main"],
        input=".factory/adapter.yaml", capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "STATUS: HUMAN_REQUIRED" in proc.stdout
    assert "side_effect_level increased 4 -> 5" in proc.stdout


def test_boundary_diff_unparseable_head_fails_closed(adapter_diff_run):
    run = adapter_diff_run(_adapter_doc(loops=[dict(_BASE_LOOP)]))
    out = run("{broken: [\n")
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert "unparseable" in out["_stdout"]


def test_boundary_diff_unparseable_base_fails_closed(tmp_path):
    root = tmp_path
    _init_repo(root)
    _write_config(root)
    (root / "hotspots.md").write_text("")
    d = root / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text("{broken: [\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    (d / "adapter.yaml").write_text(yaml.dump(_adapter_doc(loops=[dict(_BASE_LOOP)])))
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--changed-files-stdin", "--lines-changed", "10",
         "--hotspots", str(root / "hotspots.md"), "--config", _CONFIG_REL,
         "--clone-dir", str(root), "--base-ref", "main"],
        input=".factory/adapter.yaml", capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "STATUS: HUMAN_REQUIRED" in proc.stdout
    assert "unparseable" in proc.stdout


def test_boundary_diff_unresolvable_base_ref_fails_closed(tmp_path):
    """Requirement 5's fail-closed rule extends to a base ref that doesn't resolve at
    all (e.g. a shallow clone without `main` reachable) -- distinct from "the file is
    simply absent at a ref that does resolve", which is a valid, non-error state."""
    root = tmp_path
    _init_repo(root)
    _write_config(root)
    (root / "hotspots.md").write_text("")
    d = root / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(yaml.dump(_adapter_doc(loops=[dict(_BASE_LOOP)])))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--changed-files-stdin", "--lines-changed", "10",
         "--hotspots", str(root / "hotspots.md"), "--config", _CONFIG_REL,
         "--clone-dir", str(root), "--base-ref", "totally-bogus-ref-xyz"],
        input=".factory/adapter.yaml", capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "STATUS: HUMAN_REQUIRED" in proc.stdout


def test_boundary_diff_file_absent_at_resolvable_base_ref_is_not_an_error(tmp_path):
    """The base ref resolving fine but simply not having .factory/adapter.yaml at all
    yet (e.g. this PR is the one introducing it) is a valid 'no prior adapter' state,
    not a parse failure -- exercises the git-show stderr-inspection branch in
    _adapter_snapshot directly (the base commit below has no .factory/ directory at
    all, so `git show main:.factory/adapter.yaml` genuinely exits non-zero with "does
    not exist in" rather than succeeding on an empty-but-tracked file)."""
    root = tmp_path
    _init_repo(root)
    _write_config(root)
    (root / "hotspots.md").write_text("")
    (root / "README.md").write_text("no adapter.yaml at this ref\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base, no adapter.yaml yet")
    _git(root, "checkout", "-q", "-b", "pr")
    d = root / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(yaml.dump(_adapter_doc(loops=[])))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "introduce adapter.yaml")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--changed-files-stdin", "--lines-changed", "10",
         "--hotspots", str(root / "hotspots.md"), "--config", _CONFIG_REL,
         "--clone-dir", str(root), "--base-ref", "main"],
        input=".factory/adapter.yaml", capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "STATUS: PASS" in proc.stdout


# ── Signed, content-bound approval (#551) ─────────────────────────────────────
# Throwaway keys are generated here; the operator's real key is never needed. The
# tests fail (not skip) without gpg -- the spec requires it.

import gate_blast_radius as gbr  # noqa: E402

_APPROVAL_REPO = "omniscient/dark-factory"
_APPROVAL_BRANCH = "feat/issue-551-x"
_TRUSTED = "refs/blast-gate/trusted-main"
_SKILL = ".claude/skills/x/run.sh"
_MIGRATION = "alembic/versions/a.py"


def _gpg(home, *args, input=None):
    return subprocess.run(["gpg", "--homedir", str(home), "--batch", *args],
                          capture_output=True, input=input, check=True)


@pytest.fixture(scope="module")
def keys():
    """`op` (the approver), `other` (never in the trusted blob), `old` (created in 2020
    with a one-day expiry, so it is expired now)."""
    assert shutil.which("gpg"), "gpg is required for the #551 approval tests"
    base = Path(tempfile.mkdtemp(prefix="bgk-"))
    homes = {}
    for name in ("op", "other", "old"):
        home = base / name
        home.mkdir(mode=0o700)
        faked = ["--faked-system-time", "20200101T000000"] if name == "old" else []
        _gpg(home, *faked, "--passphrase", "", "--quick-gen-key",
             f"{name} <{name}@example.test>", "ed25519", "sign", "1d" if name == "old" else "1y")
        homes[name] = home
    yield homes
    for home in homes.values():
        subprocess.run(["gpgconf", "--homedir", str(home), "--kill", "all"], capture_output=True)
    shutil.rmtree(base, ignore_errors=True)


def _pubkey(home) -> bytes:
    return _gpg(home, "--armor", "--export").stdout


def _sign(home, data: bytes, faked=None) -> bytes:
    extra = ["--faked-system-time", faked] if faked else []
    return _gpg(home, *extra, "--detach-sign", "-o", "-", input=data).stdout


class _ApprovalRepo:
    """main (with the trusted key blob) -> feat/issue-551-x editing two floor paths."""

    def __init__(self, root: Path, keys, key_blob: bytes, commit_key=True):
        self.root, self.keys = root, keys
        self.approvals = root.parent / f"{root.name}-approvals"
        self.approvals.mkdir()
        _init_repo(root)
        _write_config(root)
        for rel, text in ((_SKILL, "echo v0\n"), (_MIGRATION, "v0 = 0\n"),
                          ("src/app.py", "print(0)\n"), (".claude/skills/x/old.sh", "old\n")):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        key_path = root / ".factory" / "blast-approvers.asc"
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(key_blob)
        if not commit_key:
            _git(root, "add", "-A", "--", ".", ":!.factory/blast-approvers.asc")
        else:
            _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "main")
        _git(root, "update-ref", _TRUSTED, "main")
        _git(root, "checkout", "-q", "-b", _APPROVAL_BRANCH)
        self.edit({_SKILL: "echo v1\n", _MIGRATION: "v0 = 1\n"}, "feat")

    def edit(self, files: dict, msg: str):
        for rel, text in files.items():
            (self.root / rel).write_text(text)
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", msg)

    def git(self, *args) -> str:
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def base(self) -> str:
        return self.git("merge-base", _TRUSTED, "HEAD")

    def record(self, paths=(_SKILL, _MIGRATION), **overrides) -> dict:
        base = self.base()
        rec = {
            "schema": 1, "issue": 551, "repo": _APPROVAL_REPO, "branch": _APPROVAL_BRANCH,
            "head_sha": self.git("rev-parse", "HEAD"), "base_sha": base,
            "files": {p: {"base": gbr.blob_entry(str(self.root), base, p),
                          "head": gbr.blob_entry(str(self.root), "HEAD", p)} for p in paths},
            "size": None, "trigger": "skill-security", "approved_at": "2026-11-21T14:03:00Z",
            "approved_by": "Test Operator", "approvals_count": 1, "previous_head_sha": None,
        }
        rec.update(overrides)
        return rec

    def write(self, rec, signer="op", data=None, sign_data=None, faked=None, sig=True):
        data = gbr.canonical_bytes(rec) if data is None else data
        (self.approvals / "551.json").write_bytes(data)
        if sig:
            sig_bytes = _sign(self.keys[signer], data if sign_data is None else sign_data, faked)
            (self.approvals / "551.json.sig").write_bytes(sig_bytes)

    def run(self, issue=551, repo=_APPROVAL_REPO, branch=_APPROVAL_BRANCH, lines=10,
            approval_args=True) -> dict:
        base = self.base()
        changed = self.git("diff", "--name-only", base, "HEAD")
        hot = self.root.parent / f"{self.root.name}-hot.md"
        hot.write_text("")
        cmd = [sys.executable, str(SCRIPT), "--changed-files-stdin",
               "--lines-changed", str(lines), "--hotspots", str(hot),
               "--config", _CONFIG_REL, "--clone-dir", str(self.root), "--base-ref", base]
        if approval_args:
            cmd += ["--issue", str(issue), "--repo", repo, "--branch", branch,
                    "--trusted-ref", _TRUSTED, "--head-ref", "HEAD",
                    "--approvals-dir", str(self.approvals)]
        proc = subprocess.run(cmd, input=changed, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        return _parse(proc.stdout)


@pytest.fixture
def arepo(tmp_path, keys, monkeypatch):
    monkeypatch.setenv("FACTORY_CONFIG_PATH", str(tmp_path / "absent-baked.yaml"))
    root = tmp_path / "repo"
    root.mkdir()
    return _ApprovalRepo(root, keys, _pubkey(keys["op"]) + _pubkey(keys["old"]))


def test_approval_matched_turns_human_required_into_pass(arepo):
    arepo.write(arepo.record())
    out = arepo.run()
    assert out["STATUS"] == "PASS"
    assert out["SEVERITY"] == "none"
    head = arepo.git("rev-parse", "HEAD")
    assert out["APPROVAL"] == f"matched (Test Operator at 2026-11-21T14:03:00Z, head {head})"
    assert out["TRIGGER"] == "skill-security"
    assert f"  - {_SKILL} (category: skill-security)" in out["_stdout"]
    assert sorted(json.loads(out["TRIGGERED_PATHS"])) == sorted([_MIGRATION, _SKILL])


def test_approval_no_record_is_none(arepo):
    out = arepo.run()
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["APPROVAL"] == "none"


def test_approval_without_issue_is_byte_identical_and_ignores_records(arepo):
    arepo.write(arepo.record())
    out = arepo.run(approval_args=False)
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert "APPROVAL" not in out and "TRIGGERED_PATHS" not in out
    assert out["_stdout"].splitlines()[-1].startswith("LINES_CHANGED: ")


def test_approval_missing_sig_is_signature_error(arepo):
    arepo.write(arepo.record(), sig=False)
    out = arepo.run()
    assert (out["STATUS"], out["APPROVAL"]) == ("HUMAN_REQUIRED", "error (signature)")


def test_approval_signed_by_untrusted_key_is_signature_error(arepo):
    arepo.write(arepo.record(), signer="other")
    out = arepo.run()
    assert (out["STATUS"], out["APPROVAL"]) == ("HUMAN_REQUIRED", "error (signature)")


def test_approval_tampered_record_is_signature_error(arepo):
    good = gbr.canonical_bytes(arepo.record())
    tampered = gbr.canonical_bytes(arepo.record(approved_by="Mallory"))
    arepo.write(None, data=tampered, sign_data=good)
    out = arepo.run()
    assert (out["STATUS"], out["APPROVAL"]) == ("HUMAN_REQUIRED", "error (signature)")


def test_approval_expired_key_is_signature_error(arepo):
    arepo.write(arepo.record(), signer="old", faked="20200101T010000")
    out = arepo.run()
    assert (out["STATUS"], out["APPROVAL"]) == ("HUMAN_REQUIRED", "error (signature)")


def test_approval_key_only_in_working_tree_is_signature_error(tmp_path, keys, monkeypatch):
    monkeypatch.setenv("FACTORY_CONFIG_PATH", str(tmp_path / "absent-baked.yaml"))
    root = tmp_path / "repo"
    root.mkdir()
    repo = _ApprovalRepo(root, keys, _pubkey(keys["op"]), commit_key=False)
    assert (root / ".factory" / "blast-approvers.asc").is_file()
    repo.write(repo.record())
    out = repo.run()
    assert (out["STATUS"], out["APPROVAL"]) == ("HUMAN_REQUIRED", "error (signature)")


def test_approval_non_canonical_bytes_is_record_error(arepo):
    pretty = (json.dumps(arepo.record(), indent=2, sort_keys=True) + "\n").encode()
    arepo.write(None, data=pretty)
    out = arepo.run()
    assert (out["STATUS"], out["APPROVAL"]) == ("HUMAN_REQUIRED", "error (record)")


def test_approval_wrong_schema_is_record_error(arepo):
    arepo.write(arepo.record(schema=2))
    assert arepo.run()["APPROVAL"] == "error (record)"


@pytest.mark.parametrize("field,value", [
    ("issue", 552), ("repo", "omniscient/markethawk"), ("branch", "feat/issue-551-other"),
])
def test_approval_identity_mismatch_is_stale(arepo, field, value):
    arepo.write(arepo.record(**{field: value}))
    out = arepo.run()
    assert (out["STATUS"], out["APPROVAL"]) == ("HUMAN_REQUIRED", "stale (identity)")


def test_approval_new_trigger_is_stale(arepo):
    arepo.write(arepo.record(paths=(_SKILL,)))
    out = arepo.run()
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["APPROVAL"] == f"stale (new trigger: {_MIGRATION})"


def test_approval_may_list_more_paths_than_currently_triggered(arepo):
    arepo.write(arepo.record(paths=(_SKILL, _MIGRATION, ".claude/skills/x/old.sh")))
    assert arepo.run()["STATUS"] == "PASS"


def test_approval_head_blob_changed_is_stale(arepo):
    arepo.write(arepo.record())
    arepo.edit({_SKILL: "echo v2\n"}, "later edit")
    out = arepo.run()
    assert out["STATUS"] == "HUMAN_REQUIRED"
    assert out["APPROVAL"] == f"stale (changed since approval: {_SKILL})"


def test_approval_base_blob_changed_is_stale(arepo):
    """main changes the triggered file underneath; the branch keeps its own content."""
    arepo.write(arepo.record())
    arepo.git("checkout", "-q", "main")
    arepo.edit({_SKILL: "echo main-v1\n"}, "main moves")
    arepo.git("update-ref", _TRUSTED, "main")
    arepo.git("checkout", "-q", _APPROVAL_BRANCH)
    arepo.git("merge", "-q", "-s", "ours", "-m", "merge main", "main")
    assert (arepo.root / _SKILL).read_text() == "echo v1\n"
    out = arepo.run()
    assert out["APPROVAL"] == f"stale (changed since approval: {_SKILL})"


def test_approval_unrelated_later_commit_still_matches(arepo):
    arepo.write(arepo.record())
    arepo.edit({"src/app.py": "print(1)\n"}, "unrelated")
    assert arepo.run()["STATUS"] == "PASS"


def test_approval_mode_only_change_is_stale(arepo):
    arepo.write(arepo.record())
    os.chmod(arepo.root / _SKILL, 0o755)
    _git(arepo.root, "add", "-A")
    _git(arepo.root, "commit", "-q", "-m", "chmod +x")
    assert arepo.run()["APPROVAL"] == f"stale (changed since approval: {_SKILL})"


def test_approval_deleted_file_binds_absent(arepo):
    old = ".claude/skills/x/old.sh"
    _git(arepo.root, "rm", "-q", old)
    _git(arepo.root, "commit", "-q", "-m", "delete")
    rec = arepo.record(paths=(_SKILL, _MIGRATION, old))
    assert rec["files"][old]["head"] == "absent"
    assert rec["files"][old]["base"].startswith("100644 ")
    arepo.write(rec)
    assert arepo.run()["STATUS"] == "PASS"
    arepo.write(arepo.record(paths=(_SKILL, _MIGRATION)))
    assert arepo.run()["APPROVAL"] == f"stale (new trigger: {old})"


def test_approval_reindented_line_is_stale(arepo):
    """#551 review: a patch-id ignores whitespace; the blob binding must not."""
    arepo.edit({_SKILL: "if x; then\n  echo v1\nfi\n"}, "inside if")
    arepo.write(arepo.record())
    arepo.edit({_SKILL: "if x; then\necho v1\nfi\n"}, "dedent")
    assert arepo.run()["APPROVAL"] == f"stale (changed since approval: {_SKILL})"


def test_approval_relocated_hunk_is_stale(arepo):
    """#551 review: the same hunk in another block has the same patch-id."""
    arepo.edit({_SKILL: "job_a:\n  run: build\njob_b:\n  run: test\n  extra: 1\n"}, "hunk in job_b")
    arepo.write(arepo.record())
    arepo.edit({_SKILL: "job_a:\n  run: build\n  extra: 1\njob_b:\n  run: test\n"}, "hunk in job_a")
    assert arepo.run()["APPROVAL"] == f"stale (changed since approval: {_SKILL})"


def test_approval_boundary_escalation_requires_adapter_entry(tmp_path, keys, monkeypatch):
    monkeypatch.setenv("FACTORY_CONFIG_PATH", str(tmp_path / "absent-baked.yaml"))
    root = tmp_path / "repo"
    root.mkdir()
    repo = _ApprovalRepo(root, keys, _pubkey(keys["op"]))
    (root / ".factory" / "adapter.yaml").write_text(yaml.dump({"safety": {"x": 1}}))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "escalate")
    repo.write(repo.record())
    out = repo.run()
    assert out["TRIGGER"] == "boundary-escalation"
    assert out["APPROVAL"] == "stale (new trigger: .factory/adapter.yaml)"
    repo.write(repo.record(paths=(_SKILL, _MIGRATION, ".factory/adapter.yaml")))
    assert repo.run()["STATUS"] == "PASS"


def _size_repo(tmp_path, keys, monkeypatch):
    monkeypatch.setenv("FACTORY_CONFIG_PATH", str(tmp_path / "absent-baked.yaml"))
    root = tmp_path / "repo"
    root.mkdir()
    repo = _ApprovalRepo(root, keys, _pubkey(keys["op"]))
    _write_config(root, {"size_budget_blocks": True, "size_budget_lines": 5})
    _git(root, "checkout", "-q", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "size blocks")
    _git(root, "update-ref", _TRUSTED, "main")
    _git(root, "checkout", "-q", _APPROVAL_BRANCH)
    _git(root, "merge", "-q", "-m", "merge main", "main")
    return repo


def _size_of(repo, lines=50):
    return {"lines_changed": lines,
            "head_tree": repo.git("rev-parse", "HEAD^{tree}"),
            "base_tree": repo.git("rev-parse", f"{repo.base()}^{{tree}}")}


def test_approval_size_trigger_matches_both_trees(tmp_path, keys, monkeypatch):
    repo = _size_repo(tmp_path, keys, monkeypatch)
    repo.write(repo.record(size=_size_of(repo)))
    out = repo.run(lines=50)
    assert "[size] 50 lines > 5 budget" in out["_stdout"]
    assert out["STATUS"] == "PASS"


@pytest.mark.parametrize("mutate", [
    lambda s: None,
    lambda s: {**s, "lines_changed": 49},
    lambda s: {**s, "head_tree": "0" * 40},
    lambda s: {**s, "base_tree": "0" * 40},
])
def test_approval_size_trigger_mismatch_is_stale(tmp_path, keys, monkeypatch, mutate):
    repo = _size_repo(tmp_path, keys, monkeypatch)
    repo.write(repo.record(size=mutate(_size_of(repo))))
    out = repo.run(lines=50)
    assert (out["STATUS"], out["APPROVAL"]) == ("HUMAN_REQUIRED", "stale (size)")


def test_approval_never_consulted_for_pass(arepo, tmp_path):
    """A PASS diff is never looked up -- not even an APPROVAL: none line."""
    _git(arepo.root, "checkout", "-q", "main")
    _git(arepo.root, "checkout", "-q", "-b", "clean")
    arepo.edit({"src/app.py": "print(2)\n"}, "clean change")
    out = arepo.run(branch="clean")
    assert out["STATUS"] == "PASS"
    assert "APPROVAL" not in out


def test_evaluate_approval_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(gbr, "_match_approval", boom)
    assert gbr.evaluate_approval(None, [], False, 0) == "error (RuntimeError)"
