import pathlib
import sys

# .factory/hooks/{validate,smoke-gate} run `python -m pytest tests/ -q` with no
# PYTHONPATH=scripts -- self-insert so this file collects there too, not only in CI.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import pytest

from factory_core import handoff


def _write_manifest(tmp_path, name="manifest.yaml", text=""):
    (tmp_path / ".factory").mkdir(exist_ok=True)
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_read_manifest_rejects_absolute_path(tmp_path):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.read_manifest(str(tmp_path), "/etc/passwd")
    assert exc.value.code == "schema_invalid"
    assert "absolute" in exc.value.message


def test_read_manifest_rejects_escaping_path(tmp_path):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.read_manifest(str(tmp_path), "../outside.yaml")
    assert exc.value.code == "schema_invalid"
    assert "escapes" in exc.value.message


def test_read_manifest_rejects_missing_file(tmp_path):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.read_manifest(str(tmp_path), "does-not-exist.yaml")
    assert exc.value.code == "schema_invalid"


def test_read_manifest_rejects_oversize_file_before_parsing(tmp_path):
    _write_manifest(tmp_path, text="artifact_id: " + ("x" * (handoff.MAX_MANIFEST_BYTES + 1)))
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.read_manifest(str(tmp_path), "manifest.yaml")
    assert exc.value.code == "schema_invalid"
    assert "byte" in exc.value.message.lower()


def test_read_manifest_rejects_non_mapping_top_level(tmp_path):
    _write_manifest(tmp_path, text="- just\n- a\n- list\n")
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.read_manifest(str(tmp_path), "manifest.yaml")
    assert exc.value.code == "schema_invalid"
    assert "mapping" in exc.value.message


def test_read_manifest_rejects_unparseable_yaml(tmp_path):
    _write_manifest(tmp_path, text="key: [unclosed")
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.read_manifest(str(tmp_path), "manifest.yaml")
    assert exc.value.code == "schema_invalid"


def test_read_manifest_returns_parsed_dict(tmp_path):
    _write_manifest(tmp_path, text="artifact_id: scan-001\nside_effect_level: 2\n")
    data = handoff.read_manifest(str(tmp_path), "manifest.yaml")
    assert data == {"artifact_id": "scan-001", "side_effect_level": 2}


def test_read_manifest_rejects_undecodable_bytes(tmp_path):
    path = tmp_path / "manifest.yaml"
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(handoff.HandoffError) as excinfo:
        handoff.read_manifest(str(tmp_path), "manifest.yaml")
    assert excinfo.value.code == "schema_invalid"
