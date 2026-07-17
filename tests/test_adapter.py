import re
import sys
sys.path.insert(0, "scripts")
import pytest
import yaml
from factory_core import adapter, adapter_defaults


def test_no_adapter_file_returns_defaults(tmp_path):
    merged = adapter.load(str(tmp_path))
    assert merged == adapter_defaults.DEFAULTS


def test_adapter_overrides_deep_merge(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "schema_version: 1\nsafety:\n  sensitive_keywords: 'payments|pii'\n")
    merged = adapter.load(str(tmp_path))
    assert merged["safety"]["sensitive_keywords"] == "payments|pii"
    # untouched siblings survive the merge
    assert merged["safety"]["dispatch_ceiling_keywords"] == \
        adapter_defaults.DEFAULTS["safety"]["dispatch_ceiling_keywords"]


def test_invalid_yaml_raises_adapter_error(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text("{broken: [")
    with pytest.raises(adapter.AdapterError):
        adapter.load(str(tmp_path))


def test_wrong_type_raises_adapter_error(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text("schema_version: 1\nsafety: 'not-a-map'\n")
    with pytest.raises(adapter.AdapterError):
        adapter.load(str(tmp_path))


def test_unknown_keys_warn_not_fail(tmp_path, capsys):
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text("schema_version: 1\nfuture_feature: {a: 1}\n")
    merged = adapter.load(str(tmp_path))
    assert "future_feature" in merged            # carried through
    assert "unknown adapter key" in capsys.readouterr().err


def test_dotted_get(tmp_path):
    assert adapter.get(str(tmp_path), "deconflict.migrations_dir") == "alembic/versions/"


def test_loops_default_is_empty_list(tmp_path):
    """Absent adapter file merges to loops: [] (additive parity default)."""
    merged = adapter.load(str(tmp_path))
    assert merged["loops"] == []


def test_schema_version_1_without_loops_merges_to_empty_list(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text("schema_version: 1\n")
    merged = adapter.load(str(tmp_path))
    assert merged["loops"] == []


def test_repo_board_labels_now_warn_not_error(tmp_path, capsys):
    """repo/board/labels are no longer known keys — they fall through to the
    generic unknown-top-level-key warn-and-carry path, not AdapterError."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "repo: 'org/name'\nboard: 'Project X'\nlabels: ['a', 'b']\n")
    merged = adapter.load(str(tmp_path))
    assert merged["repo"] == "org/name"
    assert merged["board"] == "Project X"
    assert merged["labels"] == ["a", "b"]
    err = capsys.readouterr().err
    assert "unknown adapter key 'repo'" in err
    assert "unknown adapter key 'board'" in err
    assert "unknown adapter key 'labels'" in err


_VALID_LOOP_ENTRY = """
loops:
  - name: nightly-scan-triage
    purpose: Triage overnight scanner false positives
    trigger: 'cron:0 6 * * *'
    inputs: ["scanner_output.json"]
    outputs: ["triage_report.md"]
    artifacts: [".factory/state/triage.json"]
    verifier: verifiers/triage_verifier.py
    stop_condition: stop_conditions/triage_stop.py
    failure_behavior: escalate_to_human
    side_effect_level: 2
    handoff: handoffs/triage_handoff.py
"""


def test_valid_loop_entry_parses(tmp_path, capsys):
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(_VALID_LOOP_ENTRY)
    merged = adapter.load(str(tmp_path))
    assert len(merged["loops"]) == 1
    assert merged["loops"][0]["name"] == "nightly-scan-triage"
    assert merged["loops"][0]["side_effect_level"] == 2
    assert "unknown adapter key 'loops'" not in capsys.readouterr().err


def test_loops_independent_of_schema_version(tmp_path):
    """A schema_version: 1 file with a valid loops: entry still parses —
    schema_version never gates loops:."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text("schema_version: 1\n" + _VALID_LOOP_ENTRY)
    merged = adapter.load(str(tmp_path))
    assert len(merged["loops"]) == 1


def test_loop_entry_not_a_mapping_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text("loops:\n  - 'not-a-mapping'\n")
    with pytest.raises(adapter.AdapterError, match=r"loops\[0\] must be a mapping"):
        adapter.load(str(tmp_path))


def test_loops_not_a_list_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text("loops:\n  name: not-a-list\n")
    with pytest.raises(adapter.AdapterError, match=r"loops.*must be a list"):
        adapter.load(str(tmp_path))


def test_loop_entry_missing_required_field_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    entry = _VALID_LOOP_ENTRY.replace("    purpose: Triage overnight scanner false positives\n", "")
    (d / "adapter.yaml").write_text(entry)
    with pytest.raises(adapter.AdapterError, match=r"missing required field 'purpose'"):
        adapter.load(str(tmp_path))


def test_loop_entry_unknown_field_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    entry = _VALID_LOOP_ENTRY + "    extra_typo_field: oops\n"
    (d / "adapter.yaml").write_text(entry)
    with pytest.raises(adapter.AdapterError, match=r"unknown field 'extra_typo_field'"):
        adapter.load(str(tmp_path))


@pytest.mark.parametrize("field", sorted(adapter._LOOP_STRING_FIELDS))
def test_loop_entry_string_field_wrong_type_raises(tmp_path, field):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0][field] = 42
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError, match=re.escape(f"field '{field}' must be a non-empty string")):
        adapter.load(str(tmp_path))


@pytest.mark.parametrize("field", sorted(adapter._LOOP_LIST_FIELDS))
def test_loop_entry_list_field_wrong_type_raises(tmp_path, field):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0][field] = "not-a-list"
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError, match=re.escape(f"field '{field}' must be a list of strings")):
        adapter.load(str(tmp_path))


@pytest.mark.parametrize("bad_level", [0, 7, -1, 100])
def test_loop_entry_side_effect_level_out_of_range_raises(tmp_path, bad_level):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["side_effect_level"] = bad_level
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError, match="side_effect_level' must be an int between 1 and 6"):
        adapter.load(str(tmp_path))


def test_loop_entry_side_effect_level_non_int_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["side_effect_level"] = "two"
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError, match="side_effect_level' must be an int between 1 and 6"):
        adapter.load(str(tmp_path))


def test_loop_entry_memory_intervention_reserved_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    parsed = yaml.safe_load(_VALID_LOOP_ENTRY)
    parsed["loops"][0]["memory_intervention"] = {"policy": "whatever"}
    (d / "adapter.yaml").write_text(yaml.dump(parsed))
    with pytest.raises(adapter.AdapterError, match=r"reserved for epic #241"):
        adapter.load(str(tmp_path))


def test_mechanism_candidates_top_level_reserved_raises(tmp_path):
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "mechanism_candidates:\n  - id: mc-1\n    target_loop: nightly-scan-triage\n")
    with pytest.raises(adapter.AdapterError, match=r"mechanism_candidates.*reserved"):
        adapter.load(str(tmp_path))


# ── Parity tests: pin verbatim copies to their source constants ────────────────

def test_components_parity():
    """adapter_defaults.DEFAULTS['components'] must equal COMPONENT_SECTION_MAP verbatim."""
    sys.path.insert(0, "scripts")
    from architecture_slice import COMPONENT_SECTION_MAP
    assert adapter_defaults.DEFAULTS["components"] == COMPONENT_SECTION_MAP


def test_critical_diff_paths_parity():
    """adapter_defaults critical_diff_paths must match diff_rank.SAFETY_PATH_PATTERNS strings."""
    sys.path.insert(0, "scripts")
    from diff_rank import SAFETY_PATH_PATTERNS
    expected = [p.pattern for p in SAFETY_PATH_PATTERNS]
    assert adapter_defaults.DEFAULTS["safety"]["critical_diff_paths"] == expected


# ── Consumer 1: architecture_slice._component_section_map ─────────────────────

def test_component_section_map_default_parity(tmp_path):
    """Without adapter file, _component_section_map returns DEFAULTS['components']."""
    sys.path.insert(0, "scripts")
    import architecture_slice as a
    assert a._component_section_map(str(tmp_path)) == adapter_defaults.DEFAULTS["components"]


def test_component_section_map_adapter_override(tmp_path):
    """With adapter file overriding components, merged result is returned."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "components:\n  api: ['Overview', 'API Layer']\n")
    sys.path.insert(0, "scripts")
    import architecture_slice as a
    m = a._component_section_map(str(tmp_path))
    assert m["api"] == ["Overview", "API Layer"]
    assert "backend" in m  # defaults still merged in


# ── Consumer 6: adapter CLI --format keyvalue (gate_lib.sh support) ───────────

def test_adapter_cli_keyvalue_format(tmp_path, capsys):
    """--format keyvalue emits tab-separated key\\tvalue lines for dict values."""
    import sys as _sys
    old_argv = _sys.argv[:]
    try:
        _sys.argv = ["adapter", "--clone-dir", str(tmp_path),
                     "--get", "memory_routing", "--format", "keyvalue"]
        adapter.main()
    except SystemExit:
        pass
    finally:
        _sys.argv = old_argv
    out = capsys.readouterr().out
    # Default memory_routing has at least one entry; each line must be key<TAB>value
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) > 0
    for line in lines:
        assert "\t" in line, f"Expected tab-separated line, got: {line!r}"


def test_adapter_cli_keyvalue_format_override(tmp_path, capsys):
    """--format keyvalue reflects adapter.yaml override for dict values."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "memory_routing:\n  custom/path/*: '.archon/memory/custom.md'\n")
    import sys as _sys
    old_argv = _sys.argv[:]
    try:
        _sys.argv = ["adapter", "--clone-dir", str(tmp_path),
                     "--get", "memory_routing", "--format", "keyvalue"]
        adapter.main()
    except SystemExit:
        pass
    finally:
        _sys.argv = old_argv
    out = capsys.readouterr().out
    assert "custom/path/*\t.archon/memory/custom.md" in out


# ── Consumer 2: diff_rank._safety_path_patterns ────────────────────────────────

def test_safety_path_patterns_default_parity(tmp_path):
    """Without adapter file, _safety_path_patterns returns compiled patterns from DEFAULTS."""
    sys.path.insert(0, "scripts")
    import diff_rank as dr
    patterns = dr._safety_path_patterns(str(tmp_path))
    assert [p.pattern for p in patterns] == adapter_defaults.DEFAULTS["safety"]["critical_diff_paths"]


def test_safety_path_patterns_adapter_override(tmp_path):
    """With adapter override, returns overridden compiled patterns (deep-merged)."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "safety:\n  critical_diff_paths:\n    - '^custom/path/'\n")
    sys.path.insert(0, "scripts")
    import diff_rank as dr
    patterns = dr._safety_path_patterns(str(tmp_path))
    pattern_strings = [p.pattern for p in patterns]
    assert "^custom/path/" in pattern_strings


# ── Consumer 3: gate_blast_radius._migration_seed_auth_patterns ────────────────

def test_migration_seed_auth_patterns_default_parity(tmp_path):
    """Without adapter file, _migration_seed_auth_patterns returns DEFAULTS patterns."""
    sys.path.insert(0, "scripts")
    import gate_blast_radius as gbr
    patterns = gbr._migration_seed_auth_patterns(str(tmp_path))
    assert [p.pattern for p in patterns] == adapter_defaults.DEFAULTS["safety"]["migration_seed_auth_patterns"]


def test_migration_seed_auth_patterns_adapter_override(tmp_path):
    """With adapter override, returns overridden compiled patterns."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "safety:\n  migration_seed_auth_patterns:\n    - '^custom/migrations/'\n")
    sys.path.insert(0, "scripts")
    import gate_blast_radius as gbr
    patterns = gbr._migration_seed_auth_patterns(str(tmp_path))
    pattern_strings = [p.pattern for p in patterns]
    assert "^custom/migrations/" in pattern_strings


# ── Consumer 4: epic_autopilot._hard_exclude_paths + _sensitive_keywords ───────

def test_hard_exclude_paths_default_parity(tmp_path):
    """Without adapter file, _hard_exclude_paths returns DEFAULTS safety.hard_exclude_paths."""
    sys.path.insert(0, "scripts")
    from factory_core import epic_autopilot as ap
    assert ap._hard_exclude_paths(str(tmp_path)) == adapter_defaults.DEFAULTS["safety"]["hard_exclude_paths"]


def test_hard_exclude_paths_adapter_override(tmp_path):
    """With adapter override, returns overridden list (deep-merged)."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "safety:\n  hard_exclude_paths:\n    - 'custom/excluded/'\n")
    sys.path.insert(0, "scripts")
    from factory_core import epic_autopilot as ap
    paths = ap._hard_exclude_paths(str(tmp_path))
    assert "custom/excluded/" in paths


def test_sensitive_keywords_default_parity(tmp_path):
    """Without adapter file, _sensitive_keywords returns DEFAULTS safety.sensitive_keywords."""
    sys.path.insert(0, "scripts")
    from factory_core import epic_autopilot as ap
    assert ap._sensitive_keywords(str(tmp_path)) == adapter_defaults.DEFAULTS["safety"]["sensitive_keywords"]


def test_sensitive_keywords_adapter_override(tmp_path):
    """With adapter override, returns overridden sensitive_keywords string."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "safety:\n  sensitive_keywords: 'payments|pci'\n")
    sys.path.insert(0, "scripts")
    from factory_core import epic_autopilot as ap
    kw = ap._sensitive_keywords(str(tmp_path))
    assert kw == "payments|pci"


# ── Consumer 5: main_red_fixer._main_red_allowed_paths ─────────────────────────

def test_main_red_allowed_paths_default_parity(tmp_path):
    """Without adapter file, _main_red_allowed_paths returns DEFAULTS safety.main_red_allowed_paths."""
    sys.path.insert(0, "scripts")
    from factory_core import main_red_fixer as mf
    assert mf._main_red_allowed_paths(str(tmp_path)) == adapter_defaults.DEFAULTS["safety"]["main_red_allowed_paths"]


def test_main_red_allowed_paths_adapter_override(tmp_path):
    """With adapter override, returns overridden allowed_paths list."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "safety:\n  main_red_allowed_paths:\n    - 'custom/'\n    - 'other/'\n")
    sys.path.insert(0, "scripts")
    from factory_core import main_red_fixer as mf
    paths = mf._main_red_allowed_paths(str(tmp_path))
    assert "custom/" in paths
    assert "other/" in paths


# ── Consumer 7: deconflict._deconflict_models_init + _deconflict_migrations_dir ─

def test_deconflict_models_init_default_parity(tmp_path):
    """Without adapter file, _deconflict_models_init returns DEFAULTS deconflict.models_init."""
    sys.path.insert(0, "scripts")
    from factory_core import deconflict as dc
    assert dc._deconflict_models_init(str(tmp_path)) == adapter_defaults.DEFAULTS["deconflict"]["models_init"]


def test_deconflict_models_init_adapter_override(tmp_path):
    """With adapter override, returns overridden models_init path."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "deconflict:\n  models_init: 'src/models/__init__.py'\n")
    sys.path.insert(0, "scripts")
    from factory_core import deconflict as dc
    assert dc._deconflict_models_init(str(tmp_path)) == "src/models/__init__.py"


def test_deconflict_migrations_dir_default_parity(tmp_path):
    """Without adapter file, _deconflict_migrations_dir returns DEFAULTS deconflict.migrations_dir."""
    sys.path.insert(0, "scripts")
    from factory_core import deconflict as dc
    assert dc._deconflict_migrations_dir(str(tmp_path)) == adapter_defaults.DEFAULTS["deconflict"]["migrations_dir"]


def test_deconflict_migrations_dir_adapter_override(tmp_path):
    """With adapter override, returns overridden migrations_dir path."""
    d = tmp_path / ".factory"; d.mkdir()
    (d / "adapter.yaml").write_text(
        "deconflict:\n  migrations_dir: 'db/migrations/'\n")
    sys.path.insert(0, "scripts")
    from factory_core import deconflict as dc
    assert dc._deconflict_migrations_dir(str(tmp_path)) == "db/migrations/"


# ── Skill-security safety globs (#46) ──────────────────────────────────────

def test_skill_security_globs_in_defaults_hard_exclude_paths():
    paths = adapter_defaults.DEFAULTS["safety"]["hard_exclude_paths"]
    assert any(".claude/skills/" in p for p in paths)
    assert any("settings.json" in p for p in paths)
    assert any(".mcp.json" in p for p in paths)
    assert any(".claude/plugins/" in p for p in paths)
    assert any(".claude-plugin/" in p for p in paths)
    assert any(".factory/hooks/" in p for p in paths)


def test_skill_security_globs_in_defaults_critical_diff_paths():
    import re
    patterns = adapter_defaults.DEFAULTS["safety"]["critical_diff_paths"]
    for p in patterns:
        re.compile(p)  # every entry must be a valid regex
    joined = "|".join(patterns)
    assert "claude/skills" in joined
    assert re.search(r"settings\\?\.json", joined)  # dot is regex-escaped in these patterns
    assert "factory/hooks" in joined
    assert any("SKILL" in p for p in patterns), "SKILL.md must appear (visibility only)"


def test_skill_md_not_in_migration_seed_auth_patterns():
    """SKILL.md must never be a path-level HUMAN_REQUIRED trigger — see spec Q2/A2."""
    patterns = adapter_defaults.DEFAULTS["safety"]["migration_seed_auth_patterns"]
    assert not any("SKILL" in p for p in patterns)


def test_skill_scripts_and_settings_in_migration_seed_auth_patterns():
    import re
    patterns = [re.compile(p) for p in adapter_defaults.DEFAULTS["safety"]["migration_seed_auth_patterns"]]
    assert any(p.search(".claude/skills/code-review/scripts/foo.py") for p in patterns)
    assert any(p.search(".claude/settings.json") for p in patterns)
    assert any(p.search(".factory/hooks/validate") for p in patterns)


def test_dark_factory_own_adapter_yaml_has_skill_security_globs():
    """Guards the A4 merge-semantics gap: .factory/adapter.yaml list-replaces DEFAULTS,
    so it must carry the skill-security globs independently, not just inherit them."""
    import re
    import yaml
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[1]
    data = yaml.safe_load((repo_root / ".factory" / "adapter.yaml").read_text())
    for key in ("hard_exclude_paths", "critical_diff_paths", "migration_seed_auth_patterns"):
        joined = "|".join(data["safety"][key])
        assert ".claude/skills" in joined, f"{key} missing .claude/skills glob"
        # dot is regex-escaped in the two pattern-based lists but not in hard_exclude_paths
        assert re.search(r"settings\\?\.json", joined), f"{key} missing settings.json glob"
        assert "factory/hooks" in joined, f"{key} missing .factory/hooks glob"
    assert not any("SKILL" in p for p in data["safety"]["migration_seed_auth_patterns"])
