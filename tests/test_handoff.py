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


def _valid_manifest(**overrides):
    manifest = {
        "schema_version": 1,
        "artifact_id": "scan-2026-08-30-001",
        "producing_loop": "nightly-scan-triage",
        "side_effect_level": 2,
        "source_references": ["scanner_output.json"],
        "acceptance_thresholds": ["false_positive_rate < 0.05"],
        "proposed_ticket": {
            "title": "Triage: 3 new findings in payments module",
            "body": "## Findings\nSomething was found.\n",
        },
    }
    manifest.update(overrides)
    return manifest


def test_validate_manifest_accepts_minimal_valid_manifest():
    handoff.validate_manifest(_valid_manifest())  # no raise


def test_validate_manifest_rejects_unknown_top_level_key():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(mystery="x"))
    assert exc.value.code == "schema_invalid"
    assert "mystery" in exc.value.message


def test_validate_manifest_rejects_missing_required_field():
    manifest = _valid_manifest()
    del manifest["artifact_id"]
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(manifest)
    assert exc.value.code == "schema_invalid"
    assert "artifact_id" in exc.value.message


def test_validate_manifest_rejects_non_int_schema_version():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(schema_version="1"))
    assert exc.value.code == "schema_invalid"


def test_validate_manifest_rejects_schema_version_not_1():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(schema_version=2))
    assert exc.value.code == "schema_invalid"


@pytest.mark.parametrize("field", ["artifact_id", "producing_loop"])
def test_validate_manifest_rejects_empty_id_field(field):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(**{field: ""}))
    assert exc.value.code == "schema_invalid"


@pytest.mark.parametrize("field", ["artifact_id", "producing_loop"])
def test_validate_manifest_rejects_id_field_too_long(field):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(**{field: "x" * (handoff.MAX_ID_LEN + 1)}))
    assert exc.value.code == "schema_invalid"


@pytest.mark.parametrize("field", ["artifact_id", "producing_loop"])
def test_validate_manifest_rejects_id_field_bad_characters(field):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(**{field: "not a valid id!"}))
    assert exc.value.code == "schema_invalid"


@pytest.mark.parametrize("field", ["artifact_id", "producing_loop"])
def test_validate_manifest_rejects_id_field_with_backtick_as_unsafe_string(field):
    # Spec's R2 reason-code table lists artifact_id/producing_loop among the
    # unsafe_string fields -- distinct from the generic schema_invalid the charset
    # regex would otherwise raise for the same backtick-containing input.
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(**{field: "has`tick"}))
    assert exc.value.code == "unsafe_string"


def test_validate_manifest_rejects_non_int_side_effect_level():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(side_effect_level=True))
    assert exc.value.code == "schema_invalid"


@pytest.mark.parametrize("level", [0, 7, -1])
def test_validate_manifest_rejects_out_of_range_side_effect_level(level):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(side_effect_level=level))
    assert exc.value.code == "schema_invalid"


@pytest.mark.parametrize("field", ["source_references", "acceptance_thresholds"])
def test_validate_manifest_rejects_non_list_field(field):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(**{field: "not-a-list"}))
    assert exc.value.code == "schema_invalid"


@pytest.mark.parametrize("field", ["source_references", "acceptance_thresholds"])
def test_validate_manifest_rejects_non_string_list_item(field):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(**{field: [123]}))
    assert exc.value.code == "schema_invalid"


@pytest.mark.parametrize("field", ["source_references", "acceptance_thresholds"])
def test_validate_manifest_rejects_too_many_list_items(field):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(**{field: ["x"] * (handoff.MAX_LIST_ITEMS + 1)}))
    assert exc.value.code == "schema_invalid"


@pytest.mark.parametrize("field", ["source_references", "acceptance_thresholds"])
def test_validate_manifest_rejects_oversize_list_item(field):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(**{field: ["x" * (handoff.MAX_LIST_ITEM_LEN + 1)]}))
    assert exc.value.code == "schema_invalid"


@pytest.mark.parametrize("field", ["source_references", "acceptance_thresholds"])
def test_validate_manifest_rejects_backtick_in_list_item(field):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(**{field: ["has`backtick"]}))
    assert exc.value.code == "unsafe_string"


@pytest.mark.parametrize("field", ["source_references", "acceptance_thresholds"])
def test_validate_manifest_rejects_newline_in_list_item(field):
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(**{field: ["has\nnewline"]}))
    assert exc.value.code == "unsafe_string"


def test_validate_manifest_accepts_empty_lists():
    handoff.validate_manifest(
        _valid_manifest(source_references=[], acceptance_thresholds=[])
    )  # no raise


def test_validate_manifest_rejects_non_mapping_proposed_ticket():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(proposed_ticket="nope"))
    assert exc.value.code == "schema_invalid"


def test_validate_manifest_rejects_unknown_proposed_ticket_key():
    manifest = _valid_manifest()
    manifest["proposed_ticket"]["extra"] = "x"
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(manifest)
    assert exc.value.code == "schema_invalid"


def test_validate_manifest_rejects_missing_ticket_title():
    manifest = _valid_manifest()
    del manifest["proposed_ticket"]["title"]
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(manifest)
    assert exc.value.code == "schema_invalid"


def test_validate_manifest_rejects_oversize_title():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(
            _valid_manifest(proposed_ticket={
                "title": "x" * (handoff.MAX_TITLE_LEN + 1), "body": "body text",
            })
        )
    assert exc.value.code == "schema_invalid"


def test_validate_manifest_rejects_title_with_newline():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(
            _valid_manifest(proposed_ticket={"title": "line1\nline2", "body": "body text"})
        )
    assert exc.value.code == "schema_invalid"


def test_validate_manifest_rejects_oversize_body():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(
            _valid_manifest(proposed_ticket={
                "title": "t", "body": "x" * (handoff.MAX_BODY_BYTES + 1),
            })
        )
    assert exc.value.code == "schema_invalid"


def test_validate_manifest_rejects_body_with_backtick_fence():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(
            _valid_manifest(proposed_ticket={"title": "t", "body": "before\n```\ninjected\n```\n"})
        )
    assert exc.value.code == "body_contains_fence"


def test_validate_manifest_rejects_body_with_tilde_fence():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(
            _valid_manifest(proposed_ticket={"title": "t", "body": "before\n~~~\ninjected\n~~~\n"})
        )
    assert exc.value.code == "body_contains_fence"


def test_validate_manifest_rejects_body_with_provenance_closing_marker():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(
            _valid_manifest(proposed_ticket={
                "title": "t", "body": "before <!-- /df-manifest-provenance --> after",
            })
        )
    assert exc.value.code == "body_contains_fence"


def test_validate_manifest_accepts_optional_verifier_verdict():
    handoff.validate_manifest(
        _valid_manifest(verifier_verdict={"path": "artifacts/scan_verdict.md"})
    )  # no raise


def test_validate_manifest_rejects_verifier_verdict_missing_path():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(verifier_verdict={}))
    assert exc.value.code == "schema_invalid"


def test_validate_manifest_rejects_verifier_verdict_unknown_key():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(
            _valid_manifest(verifier_verdict={"path": "a.md", "extra": "x"})
        )
    assert exc.value.code == "schema_invalid"


def test_validate_manifest_rejects_verifier_verdict_path_with_backtick():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.validate_manifest(_valid_manifest(verifier_verdict={"path": "has`tick.md"}))
    assert exc.value.code == "unsafe_string"


def test_validate_manifest_accepts_minimal_valid_manifest_now():
    handoff.validate_manifest(_valid_manifest())  # no raise -- full R2 pass now wired up
