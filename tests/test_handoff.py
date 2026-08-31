import json
import os
import pathlib
import re
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


def _loop_entry(**overrides):
    """Mirrors tests/test_verifier.py::_loop_entry -- the minimal valid A1.5 loop shape."""
    entry = {
        "name": "nightly-scan-triage",
        "purpose": "nightly scan triage",
        "discovery": {"trigger": "cron:0 6 * * *", "inputs": ["scripts/scanner.py"]},
        "handoff": {"outputs": ["artifacts/scan-report.md"], "manifest": "artifacts/manifest.yaml"},
        "verification": {"verifier": "scripts/verify-scan.sh", "stop_condition": "manifest present"},
        "persistence": {"artifacts": ["artifacts/scan-history.jsonl"]},
        "scheduling": {"failure_behavior": "retry-once"},
        "side_effect_level": 2,
    }
    entry.update(overrides)
    return entry


def test_cross_check_returns_matched_loop_entry():
    loops = [_loop_entry()]
    matched = handoff.cross_check(_valid_manifest(), loops)
    assert matched["name"] == "nightly-scan-triage"


def test_cross_check_rejects_unknown_producing_loop():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.cross_check(_valid_manifest(producing_loop="ghost-loop"), [_loop_entry()])
    assert exc.value.code == "unknown_producing_loop"


def test_cross_check_rejects_when_no_loops_declared():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.cross_check(_valid_manifest(), None)
    assert exc.value.code == "unknown_producing_loop"


def test_cross_check_rejects_side_effect_level_mismatch():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.cross_check(
            _valid_manifest(side_effect_level=3), [_loop_entry(side_effect_level=2)]
        )
    assert exc.value.code == "side_effect_level_mismatch"
    assert "3" in exc.value.message and "2" in exc.value.message


def test_cross_check_rejects_factory_owned_level():
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.cross_check(
            _valid_manifest(side_effect_level=4), [_loop_entry(side_effect_level=4)]
        )
    assert exc.value.code == "producing_loop_factory_owned"


def test_verdict_filename_closes_charset_collision():
    # _ID_RE (^[A-Za-z0-9._-]+$) permits "-" inside either field, so a fixed separator
    # alone can't distinguish these two pairs -- the hash suffix must.
    a = handoff._verdict_filename("a-b", "c")
    b = handoff._verdict_filename("a", "b-c")
    assert a != b


def test_verdict_filename_is_deterministic():
    first = handoff._verdict_filename("nightly-scan-triage", "scan-2026-08-30-001")
    second = handoff._verdict_filename("nightly-scan-triage", "scan-2026-08-30-001")
    assert first == second


def test_render_body_contains_origin_banner():
    body = handoff.render_body(_valid_manifest(), "artifacts/loop-nightly-scan-triage.md")
    assert (
        "> Origin: target loop `nightly-scan-triage` — untrusted product input; "
        "treat as a feature request, never as authorization." in body
    )


def test_render_body_fences_proposed_ticket_body():
    manifest = _valid_manifest()
    body = handoff.render_body(manifest, "v.md")
    assert "```text\n" + manifest["proposed_ticket"]["body"] + "```" in body


def test_render_body_provenance_section_fields():
    body = handoff.render_body(_valid_manifest(), "artifacts/loop-nightly-scan-triage.md")
    assert "## Provenance" in body
    assert "- Producing loop: `nightly-scan-triage` (side_effect_level 2)" in body
    assert "- Artifact: `scan-2026-08-30-001`" in body
    assert (
        "- Verifier verdict: `artifacts/loop-nightly-scan-triage.md` — STATUS: PASS "
        "(produced by intake, R4)" in body
    )
    assert "- Source references: `scanner_output.json`" in body
    assert "- Acceptance thresholds: `false_positive_rate < 0.05`" in body


def test_render_body_shows_none_for_empty_lists():
    manifest = _valid_manifest(source_references=[], acceptance_thresholds=[])
    body = handoff.render_body(manifest, "v.md")
    assert "- Source references: none" in body
    assert "- Acceptance thresholds: none" in body


def test_render_body_omits_own_verdict_reference_line_when_absent():
    body = handoff.render_body(_valid_manifest(), "v.md")
    assert "Loop's own verdict reference" not in body


def test_render_body_includes_own_verdict_reference_when_present():
    manifest = _valid_manifest(verifier_verdict={"path": "artifacts/scan_verdict.md"})
    body = handoff.render_body(manifest, "v.md")
    assert "- Loop's own verdict reference: `artifacts/scan_verdict.md` (informational; omitted when absent)" in body


def test_render_body_embeds_manifest_verbatim_json_between_markers():
    manifest = _valid_manifest()
    body = handoff.render_body(manifest, "v.md")
    start = body.index("<!-- df-manifest-provenance -->") + len("<!-- df-manifest-provenance -->")
    end = body.rindex("<!-- /df-manifest-provenance -->")
    block = body[start:end].strip()
    assert block.startswith("```json")
    assert block.endswith("```")
    embedded = json.loads(block[len("```json"):-len("```")].strip())
    assert embedded == manifest


def test_render_body_rejects_when_over_size_cap(monkeypatch):
    monkeypatch.setattr(handoff, "MAX_RENDERED_BODY_LEN", 100)
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.render_body(_valid_manifest(), "v.md")
    assert exc.value.code == "body_too_large"


from factory_core import run_record as _run_record
from factory_core import verifier as _verifier

_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "verifiers"


@pytest.fixture(autouse=True)
def _hermetic_run_record(tmp_path, monkeypatch):
    """Every test in this file exercises intake(), and from Task 8 onward intake()
    unconditionally calls run_record.cmd_record (R6) on both the accept and reject
    path. Autouse + per-test tmp_path keeps every test in this file off the real
    SCHEDULER_STATE_DIR and off the network (R6's Hermetic-test statement), without
    each test needing its own monkeypatch boilerplate. A test that needs to inspect
    the written jsonl content overrides JSONL_PATH again with its own path (Task 8) —
    monkeypatch stacking makes that safe."""
    monkeypatch.setattr(_run_record, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(_run_record, "_post_seq", lambda r: None)
    # #362 rule: every test that can write a ledger row pins SCHEDULER_STATE_DIR (attr and
    # env) to tmp_path, so neither this process nor any child can reach /var/lib/dark-factory.
    monkeypatch.setattr(_run_record, "SCHEDULER_STATE_DIR", tmp_path / "scheduler-state")
    monkeypatch.setenv("SCHEDULER_STATE_DIR", str(tmp_path))


def _write_manifest_file(clone_dir, manifest, name="manifest.yaml"):
    import yaml as _yaml
    path = pathlib.Path(clone_dir) / name
    path.write_text(_yaml.safe_dump(manifest), encoding="utf-8")
    return name


def _stub_create_issue(issue_id="4242"):
    calls = []

    def _create(title, body, labels):
        calls.append({"title": title, "body": body, "labels": labels})
        return issue_id

    _create.calls = calls
    return _create


def test_intake_accepts_and_creates_issue(tmp_path):
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest = _valid_manifest()
    manifest_name = _write_manifest_file(clone_dir, manifest)

    loop = _loop_entry(verification={"verifier": "verify.sh", "stop_condition": "n/a"})
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_pass.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)

    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    create_issue = _stub_create_issue()

    result = handoff.intake(
        str(clone_dir), manifest_name, artifacts_dir=str(artifacts_dir),
        create_issue=create_issue,
        run_verifier=lambda **kw: _verifier.resolve_and_run(**kw),
        adapter_loops=[loop],
    )

    assert result.accepted is True
    assert result.issue_id == "4242"
    assert len(create_issue.calls) == 1
    call = create_issue.calls[0]
    assert call["title"] == "[intake] Triage: 3 new findings in payments module"
    assert call["labels"] == "needs-triage,manifest-intake"
    assert "df-manifest-provenance" in call["body"]
    # Filename includes artifact_id (not just producing_loop) so a second manifest from
    # the same loop can't silently overwrite this verdict file (advisory finding fix).
    matches = list(artifacts_dir.glob("loop-nightly-scan-triage-scan-2026-08-30-001-*.md"))
    assert len(matches) == 1
    verdict_path = matches[0]
    assert re.fullmatch(
        r"loop-nightly-scan-triage-scan-2026-08-30-001-[0-9a-f]{16}\.md", verdict_path.name
    )
    assert "STATUS: PASS" in verdict_path.read_text()


def test_intake_rejects_verifier_undeclared(tmp_path):
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    loop = _loop_entry(verification={"stop_condition": "n/a"})  # no 'verifier' key

    with pytest.raises(handoff.HandoffError) as exc:
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
            create_issue=_stub_create_issue(), adapter_loops=[loop],
        )
    assert exc.value.code == "verifier_undeclared"


def test_intake_rejects_verdict_not_passing(tmp_path):
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    loop = _loop_entry(verification={"verifier": "verify.sh", "stop_condition": "n/a"})
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_blocked.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)

    with pytest.raises(handoff.HandoffError) as exc:
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
            create_issue=_stub_create_issue(),
            run_verifier=lambda **kw: _verifier.resolve_and_run(**kw),
            adapter_loops=[loop],
        )
    assert exc.value.code == "verdict_not_passing"
    assert "BLOCKED" in exc.value.message


def test_intake_rejects_issue_create_failed_on_empty_return(tmp_path):
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    loop = _loop_entry(verification={"verifier": "verify.sh", "stop_condition": "n/a"})
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_pass.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)

    with pytest.raises(handoff.HandoffError) as exc:
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
            create_issue=_stub_create_issue(issue_id=""),
            run_verifier=lambda **kw: _verifier.resolve_and_run(**kw),
            adapter_loops=[loop],
        )
    assert exc.value.code == "issue_create_failed"


def test_intake_manifest_label_env_override(tmp_path, monkeypatch):
    monkeypatch.setattr(handoff, "FACTORY_MANIFEST_LABEL", "custom-intake")
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    loop = _loop_entry(verification={"verifier": "verify.sh", "stop_condition": "n/a"})
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_pass.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)
    create_issue = _stub_create_issue()

    handoff.intake(
        str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
        create_issue=create_issue,
        run_verifier=lambda **kw: _verifier.resolve_and_run(**kw),
        adapter_loops=[loop],
    )
    assert create_issue.calls[0]["labels"] == "needs-triage,custom-intake"


@pytest.mark.parametrize("label", [
    "ready-for-agent",
    "READY-FOR-AGENT",
    "spec-pending-review",
    "plan-pending-review",
    "triage-pending-review",  # same *-pending-review shape, not one of today's two literals
    "manifest-intake-ready-for-agent",  # substring containment, not exact match (Gate-3 finding 1)
    "xx-pending-review-yy",  # containment anywhere in the string, not just a suffix (finding 1)
    "direct-to-pr",  # Gate-3 finding 2
    "DIRECT-TO-PR",
    "manifest-intake-direct-to-pr",  # substring containment of direct-to-pr
])
def test_intake_rejects_gate_shaped_manifest_label_override(tmp_path, monkeypatch, label):
    monkeypatch.setattr(handoff, "FACTORY_MANIFEST_LABEL", label)
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    loop = _loop_entry(verification={"verifier": "verify.sh", "stop_condition": "n/a"})
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_pass.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)
    create_issue = _stub_create_issue()

    with pytest.raises(handoff.HandoffError) as exc:
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
            create_issue=create_issue,
            run_verifier=lambda **kw: _verifier.resolve_and_run(**kw),
            adapter_loops=[loop],
        )
    assert exc.value.code == "internal_error"
    assert create_issue.calls == []


def test_intake_rejects_manifest_label_matching_renamed_direct_to_pr_env(tmp_path, monkeypatch):
    """Gate-3 finding 2: an operator-renamed DIRECT_TO_PR_LABEL must also be denied,
    not just the canonical literal."""
    monkeypatch.setenv("DIRECT_TO_PR_LABEL", "ship-it")
    monkeypatch.setattr(handoff, "FACTORY_MANIFEST_LABEL", "ship-it")
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    loop = _loop_entry(verification={"verifier": "verify.sh", "stop_condition": "n/a"})
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_pass.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)
    create_issue = _stub_create_issue()

    with pytest.raises(handoff.HandoffError) as exc:
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
            create_issue=create_issue,
            run_verifier=lambda **kw: _verifier.resolve_and_run(**kw),
            adapter_loops=[loop],
        )
    assert exc.value.code == "internal_error"
    assert create_issue.calls == []


def test_intake_still_denies_canonical_direct_to_pr_after_env_rename(tmp_path, monkeypatch):
    """A DIRECT_TO_PR_LABEL rename must not un-deny the canonical literal 'direct-to-pr'."""
    monkeypatch.setenv("DIRECT_TO_PR_LABEL", "ship-it")
    monkeypatch.setattr(handoff, "FACTORY_MANIFEST_LABEL", "direct-to-pr")
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    loop = _loop_entry(verification={"verifier": "verify.sh", "stop_condition": "n/a"})
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_pass.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)
    create_issue = _stub_create_issue()

    with pytest.raises(handoff.HandoffError) as exc:
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
            create_issue=create_issue,
            run_verifier=lambda **kw: _verifier.resolve_and_run(**kw),
            adapter_loops=[loop],
        )
    assert exc.value.code == "internal_error"
    assert create_issue.calls == []


def test_intake_blank_direct_to_pr_label_env_does_not_reject_default_override(tmp_path, monkeypatch):
    """An empty/whitespace-only DIRECT_TO_PR_LABEL must not contribute an empty-string
    needle that vacuously matches (and rejects) every override, including the default
    'manifest-intake'."""
    monkeypatch.setenv("DIRECT_TO_PR_LABEL", "   ")
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    loop = _loop_entry(verification={"verifier": "verify.sh", "stop_condition": "n/a"})
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_pass.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)
    create_issue = _stub_create_issue()

    result = handoff.intake(
        str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
        create_issue=create_issue,
        run_verifier=lambda **kw: _verifier.resolve_and_run(**kw),
        adapter_loops=[loop],
    )
    assert result.accepted is True
    assert create_issue.calls[0]["labels"] == "needs-triage,manifest-intake"


def test_intake_nonmatching_direct_to_pr_label_env_does_not_reject_default_override(tmp_path, monkeypatch):
    """Symmetric to the blank-env case above: a DIRECT_TO_PR_LABEL that is set but does
    not appear in the override must not over-match and reject a normal override either."""
    monkeypatch.setenv("DIRECT_TO_PR_LABEL", "ship-it")
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    loop = _loop_entry(verification={"verifier": "verify.sh", "stop_condition": "n/a"})
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_pass.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)
    create_issue = _stub_create_issue()

    result = handoff.intake(
        str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
        create_issue=create_issue,
        run_verifier=lambda **kw: _verifier.resolve_and_run(**kw),
        adapter_loops=[loop],
    )
    assert result.accepted is True
    assert create_issue.calls[0]["labels"] == "needs-triage,manifest-intake"


@pytest.mark.parametrize("label", ["needs,extra", "has space", ""])
def test_intake_rejects_malformed_manifest_label_override_as_internal_error(tmp_path, monkeypatch, label):
    monkeypatch.setattr(handoff, "FACTORY_MANIFEST_LABEL", label)
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    loop = _loop_entry(verification={"verifier": "verify.sh", "stop_condition": "n/a"})
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_pass.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)
    create_issue = _stub_create_issue()

    with pytest.raises(handoff.HandoffError) as exc:
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
            create_issue=create_issue,
            run_verifier=lambda **kw: _verifier.resolve_and_run(**kw),
            adapter_loops=[loop],
        )
    assert exc.value.code == "internal_error"
    assert create_issue.calls == []

    # R6: a malformed override must still produce an auditable runs.jsonl row, not just
    # a raised exception -- the autouse _hermetic_run_record fixture already points
    # JSONL_PATH at tmp_path / "runs.jsonl", mirroring test_intake_records_runs_jsonl_row_on_reject.
    lines = (tmp_path / "runs.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["verdict"] == "REJECTED"
    assert rec["detail"]["reject_reason"] == "internal_error"
    assert rec["detail"]["created_issue"] == ""


def test_intake_validates_manifest_label_before_reading_manifest(tmp_path, monkeypatch):
    """Gate-3 finding 3: the override check must run before read_manifest(), so a
    misconfigured override is caught without paying manifest/verifier work. Proven by
    pointing manifest_path at a file that doesn't exist: if validation ran after
    read_manifest (the old order), the missing-file check (schema_invalid) would fire
    first instead of the label check (internal_error)."""
    monkeypatch.setattr(handoff, "FACTORY_MANIFEST_LABEL", "ready-for-agent")
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()

    artifacts_dir = tmp_path / "artifacts"
    with pytest.raises(handoff.HandoffError) as exc:
        handoff.intake(
            str(clone_dir), "does-not-exist.yaml", artifacts_dir=str(artifacts_dir),
            create_issue=_stub_create_issue(),
        )
    assert exc.value.code == "internal_error"
    assert "ready-for-agent" in exc.value.message
    assert "pending-review" in exc.value.message
    assert "direct-to-pr" in exc.value.message
    # Gate-3 finding 3's actual harm: no verdict file orphaned on the artifacts mount,
    # because the rejection fires before run_verifier() ever writes one.
    assert not artifacts_dir.exists()


def test_intake_records_internal_error_for_unwritable_artifacts_dir(tmp_path):
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    loop = _loop_entry(verification={"verifier": "verify.sh", "stop_condition": "n/a"})
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_pass.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)

    # A file (not a directory) sitting at the artifacts-dir path makes
    # os.makedirs(..., exist_ok=True) raise FileExistsError (an OSError) -- the same
    # failure shape as a genuinely unwritable/read-only ARTIFACTS_DIR mount.
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.write_text("not a directory")

    with pytest.raises(handoff.HandoffError) as exc:
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(artifacts_dir),
            create_issue=_stub_create_issue(),
            run_verifier=lambda **kw: _verifier.resolve_and_run(**kw),
            adapter_loops=[loop],
        )
    assert exc.value.code == "internal_error"

    # R6, same as above: the OSError arm must still write a runs.jsonl row. This file
    # lives under tmp_path directly (not under the unwritable artifacts_dir), so the
    # write is unaffected by the failure being tested.
    lines = (tmp_path / "runs.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["verdict"] == "REJECTED"
    assert rec["detail"]["reject_reason"] == "internal_error"


def test_default_create_issue_timeout_expired_fails_closed(monkeypatch):
    def fake_run(argv, **kw):
        raise handoff.subprocess.TimeoutExpired(cmd=argv, timeout=kw.get("timeout", 300))

    monkeypatch.setattr(handoff.subprocess, "run", fake_run)
    assert handoff._default_create_issue("t", "b", "needs-triage,manifest-intake") == ""


def test_default_create_issue_argv_and_fail_closed(monkeypatch, tmp_path):
    calls = []

    class _R:
        def __init__(self, rc, out):
            self.returncode, self.stdout = rc, out

    def fake_run(argv, **kw):
        calls.append(argv)
        return _R(0, "123\n") if len(calls) == 1 else _R(1, "stray text")

    monkeypatch.setattr(handoff.subprocess, "run", fake_run)
    assert handoff._default_create_issue("t", "b", "needs-triage,manifest-intake") == "123"
    argv = calls[0]
    assert argv[0] == sys.executable and argv[1].endswith(os.path.join("providers", "cli.py"))
    assert argv[2:4] == ["tracker", "create"]
    assert "--title" in argv and "--body-file" in argv
    assert argv[argv.index("--labels") + 1] == "needs-triage,manifest-intake"
    assert handoff._default_create_issue("t", "b", "x") == ""  # rc != 0 -> fail closed


def test_intake_records_runs_jsonl_row_on_accept(tmp_path, monkeypatch):
    import json as _json
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(_run_record, "JSONL_PATH", jsonl)
    monkeypatch.setattr(_run_record, "_post_seq", lambda r: None)

    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    loop = _loop_entry(verification={"verifier": "verify.sh", "stop_condition": "n/a"})
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_pass.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)

    handoff.intake(
        str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
        create_issue=_stub_create_issue(issue_id="99"),
        run_verifier=lambda **kw: _verifier.resolve_and_run(**kw),
        adapter_loops=[loop],
    )

    lines = jsonl.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = _json.loads(lines[0])
    assert rec["intent"] == "intake"
    assert rec["stage"] == "manifest_intake"
    assert rec["verdict"] == "ACCEPTED"
    assert rec["issue_number"] == 99
    assert rec["origin"] == "target-loop:nightly-scan-triage"
    assert rec["detail"]["artifact_id"] == "scan-2026-08-30-001"
    assert rec["detail"]["created_issue"] == 99
    assert rec["detail"]["reject_reason"] == ""


def test_intake_records_runs_jsonl_row_on_reject(tmp_path, monkeypatch):
    import json as _json
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(_run_record, "JSONL_PATH", jsonl)
    monkeypatch.setattr(_run_record, "_post_seq", lambda r: None)

    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest(producing_loop="ghost-loop"))

    with pytest.raises(handoff.HandoffError):
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
            create_issue=_stub_create_issue(), adapter_loops=[_loop_entry()],
        )

    lines = jsonl.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = _json.loads(lines[0])
    assert rec["verdict"] == "REJECTED"
    assert rec["issue_number"] == 0
    assert rec["origin"] == "target-loop:ghost-loop"
    assert rec["detail"]["reject_reason"] == "unknown_producing_loop"
    assert rec["detail"]["created_issue"] == ""


def test_intake_records_origin_factory_when_producing_loop_unreadable(tmp_path, monkeypatch):
    import json as _json
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(_run_record, "JSONL_PATH", jsonl)
    monkeypatch.setattr(_run_record, "_post_seq", lambda r: None)

    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    bad_manifest = _valid_manifest()
    del bad_manifest["producing_loop"]  # R2 schema_invalid fires before producing_loop is read
    manifest_name = _write_manifest_file(clone_dir, bad_manifest)

    with pytest.raises(handoff.HandoffError) as exc:
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
            create_issue=_stub_create_issue(), adapter_loops=[_loop_entry()],
        )
    assert exc.value.code == "schema_invalid"

    rec = _json.loads(jsonl.read_text().strip())
    assert rec["origin"] == "factory"
    assert rec["detail"]["artifact_id"] == "scan-2026-08-30-001"  # read before producing_loop


def test_intake_run_id_defaults_to_intake_artifact_id(tmp_path, monkeypatch):
    import json as _json
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(_run_record, "JSONL_PATH", jsonl)
    monkeypatch.setattr(_run_record, "_post_seq", lambda r: None)
    monkeypatch.delenv("RUN_ID", raising=False)

    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest(producing_loop="ghost-loop"))

    with pytest.raises(handoff.HandoffError):
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
            create_issue=_stub_create_issue(), adapter_loops=[_loop_entry()],
        )
    rec = _json.loads(jsonl.read_text().strip())
    assert rec["run_id"] == "intake-scan-2026-08-30-001"


def test_intake_run_id_uses_env_when_set(tmp_path, monkeypatch):
    import json as _json
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(_run_record, "JSONL_PATH", jsonl)
    monkeypatch.setattr(_run_record, "_post_seq", lambda r: None)
    monkeypatch.setenv("RUN_ID", "abc-999")

    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest(producing_loop="ghost-loop"))

    with pytest.raises(handoff.HandoffError):
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
            create_issue=_stub_create_issue(), adapter_loops=[_loop_entry()],
        )
    rec = _json.loads(jsonl.read_text().strip())
    assert rec["run_id"] == "abc-999"


def test_intake_records_reject_row_for_malformed_adapter_yaml(tmp_path, monkeypatch):
    """Reachable purely from target-controlled input: the target authors its own
    .factory/adapter.yaml. adapter.get() raises AdapterError (not HandoffError) on a
    malformed file -- this must still produce a runs.jsonl reject row (R6/AC2), not
    an uncaught traceback that leaves no audit trail."""
    import json as _json
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(_run_record, "JSONL_PATH", jsonl)
    monkeypatch.setattr(_run_record, "_post_seq", lambda r: None)

    clone_dir = tmp_path / "clone"
    (clone_dir / ".factory").mkdir(parents=True)
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    (clone_dir / ".factory" / "adapter.yaml").write_text("key: [unclosed")  # unparseable

    with pytest.raises(handoff.HandoffError) as exc:
        handoff.intake(
            str(clone_dir), manifest_name, artifacts_dir=str(tmp_path / "artifacts"),
            create_issue=_stub_create_issue(),  # adapter_loops NOT passed -- exercises the real _adapter.get() path
        )
    assert exc.value.code == "unknown_producing_loop"

    rec = _json.loads(jsonl.read_text().strip())
    assert rec["verdict"] == "REJECTED"
    assert rec["detail"]["reject_reason"] == "unknown_producing_loop"


def test_cli_validate_ok(tmp_path, capsys):
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    handoff.main([
        "--clone-dir", str(clone_dir), "validate", "--manifest-path", manifest_name,
    ])
    assert "manifest OK" in capsys.readouterr().out


def test_cli_validate_invalid_exits_nonzero(tmp_path, capsys):
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    manifest = _valid_manifest()
    del manifest["artifact_id"]
    manifest_name = _write_manifest_file(clone_dir, manifest)
    with pytest.raises(SystemExit) as exc:
        handoff.main([
            "--clone-dir", str(clone_dir), "validate", "--manifest-path", manifest_name,
        ])
    assert exc.value.code == 1
    assert "schema_invalid" in capsys.readouterr().err


def test_cli_intake_end_to_end_with_real_adapter_yaml(tmp_path, monkeypatch, capsys):
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(_run_record, "JSONL_PATH", jsonl)
    monkeypatch.setattr(_run_record, "_post_seq", lambda r: None)

    clone_dir = tmp_path / "clone"
    (clone_dir / ".factory").mkdir(parents=True)
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest())
    (clone_dir / "verify.sh").write_text((_FIXTURES / "handoff_pass.sh").read_text())
    (clone_dir / "verify.sh").chmod(0o755)

    import yaml as _yaml
    adapter_doc = {"schema_version": 2, "loops": [_loop_entry(
        verification={"verifier": "verify.sh", "stop_condition": "n/a"},
    )]}
    (clone_dir / ".factory" / "adapter.yaml").write_text(_yaml.safe_dump(adapter_doc))

    artifacts_dir = tmp_path / "artifacts"
    calls = []
    monkeypatch.setattr(
        handoff, "_default_create_issue",
        lambda title, body, labels: (calls.append((title, body, labels)), "5150")[1],
    )

    handoff.main([
        "--clone-dir", str(clone_dir), "intake",
        "--manifest-path", manifest_name, "--artifacts-dir", str(artifacts_dir),
    ])
    assert "intake OK" in capsys.readouterr().out
    assert len(calls) == 1


def test_cli_intake_rejects_and_exits_nonzero(tmp_path, monkeypatch, capsys):
    jsonl = tmp_path / "runs.jsonl"
    monkeypatch.setattr(_run_record, "JSONL_PATH", jsonl)
    monkeypatch.setattr(_run_record, "_post_seq", lambda r: None)

    clone_dir = tmp_path / "clone"
    (clone_dir / ".factory").mkdir(parents=True)
    manifest_name = _write_manifest_file(clone_dir, _valid_manifest(producing_loop="ghost-loop"))

    import yaml as _yaml
    adapter_doc = {"schema_version": 2, "loops": [_loop_entry()]}
    (clone_dir / ".factory" / "adapter.yaml").write_text(_yaml.safe_dump(adapter_doc))

    with pytest.raises(SystemExit) as exc:
        handoff.main([
            "--clone-dir", str(clone_dir), "intake",
            "--manifest-path", manifest_name, "--artifacts-dir", str(tmp_path / "artifacts"),
        ])
    assert exc.value.code == 1
    assert "unknown_producing_loop" in capsys.readouterr().err
