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
