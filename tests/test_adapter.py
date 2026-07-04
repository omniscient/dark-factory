import sys
sys.path.insert(0, "scripts")
import pytest
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
