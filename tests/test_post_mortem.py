import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from factory_core import post_mortem as pm


def _write_issue_json(run_dir: Path, resolved_number: int):
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "issue.json").write_text(json.dumps({"resolved_number": resolved_number}))


def test_gather_evidence_finds_most_recent_matching_run_dir(tmp_path):
    base = tmp_path / "runs"
    old_dir = base / "run-old"
    new_dir = base / "run-new"
    _write_issue_json(old_dir, 182)
    _write_issue_json(new_dir, 182)
    (new_dir / "plan.md").write_text("plan content")
    import os, time
    os.utime(old_dir / "issue.json", (1000, 1000))
    os.utime(new_dir / "issue.json", (2000, 2000))

    evidence = pm.gather_evidence(str(base), issue_num=182, transcript_file=None)
    assert "plan content" in evidence["artifacts_context"]


def test_gather_evidence_no_matching_run_dir_returns_empty_context(tmp_path):
    evidence = pm.gather_evidence(str(tmp_path / "nope"), issue_num=999, transcript_file=None)
    assert evidence["artifacts_context"] == ""
    assert evidence["transcript_tail"] == ""


def test_gather_evidence_reads_transcript_tail(tmp_path):
    transcript = tmp_path / "t.log"
    transcript.write_text("\n".join(f"line{i}" for i in range(300)))
    evidence = pm.gather_evidence(str(tmp_path), issue_num=1, transcript_file=str(transcript))
    lines = evidence["transcript_tail"].splitlines()
    assert len(lines) == 200
    assert lines[-1] == "line299"


def test_gather_evidence_reads_only_known_artifact_files(tmp_path):
    run_dir = tmp_path / "runs" / "r1"
    _write_issue_json(run_dir, 5)
    (run_dir / "implementation.md").write_text("impl")
    (run_dir / "unrelated.md").write_text("should not appear")
    evidence = pm.gather_evidence(str(tmp_path / "runs"), issue_num=5, transcript_file=None)
    assert "impl" in evidence["artifacts_context"]
    assert "should not appear" not in evidence["artifacts_context"]


def test_build_prompt_includes_exit_code_and_transcript():
    evidence = {"transcript_tail": "some tail", "artifacts_context": ""}
    prompt = pm.build_prompt(exit_code=1, intent="fix", issue_num=42, evidence=evidence)
    assert "issue #42" in prompt
    assert "Exit code: 1" in prompt
    assert "some tail" in prompt


def test_build_prompt_no_transcript_placeholder():
    evidence = {"transcript_tail": "", "artifacts_context": ""}
    prompt = pm.build_prompt(exit_code=1, intent="fix", issue_num=1, evidence=evidence)
    assert "<no transcript available>" in prompt


def test_render_comment_shape():
    body = pm.render_comment(
        post_mortem_text="It broke because X.",
        exit_code=1, intent="fix", promoted_at="2026-07-22T12:00:00Z",
        product_name="Dark Factory",
    )
    assert body.startswith("<!-- df-post-mortem -->")
    assert "It broke because X." in body
    assert "**Exit code:** 1 | **Phase:** fix | **Timestamp:** 2026-07-22T12:00:00Z" in body
    assert body.endswith("*Posted by Dark Factory Dark Factory*")


def test_render_comment_product_name_is_a_parameter_not_a_literal_token():
    # Same class of bug as cost_report.render()'s footer (see "Deviations from
    # the spec" item 1) — entrypoint.sh's ${FACTORY_PRODUCT_NAME} is bash-side
    # expansion; a Python f-string literal token would regress to unexpanded
    # text once captured via $(...). MarketHawk is the *other* Dark Factory
    # instance's actual product name (see CLAUDE.md) — a concrete, meaningful
    # non-default value, not an arbitrary placeholder.
    body = pm.render_comment("text", 1, "fix", "2026-07-22T12:00:00Z", product_name="MarketHawk")
    assert "*Posted by MarketHawk Dark Factory*" in body
    assert "FACTORY_PRODUCT_NAME" not in body


def test_build_failure_record_truncates_excerpt_and_collapses_newlines():
    long_text = "a\nb\n" + ("x" * 600)
    record = pm.build_failure_record(
        issue_num=7, title="Some Title", intent="fix", exit_code=2,
        post_mortem_text=long_text, promoted_at="2026-07-22T12:00:00Z",
    )
    assert record["issue"] == 7
    assert record["title"] == "Some Title"
    assert len(record["postmortem"]) == 500
    assert "\n" not in record["postmortem"]


def test_build_failure_record_title_defaults_to_unknown():
    record = pm.build_failure_record(
        issue_num=7, title="", intent="fix", exit_code=1,
        post_mortem_text="x", promoted_at="2026-07-22T12:00:00Z",
    )
    assert record["title"] == "unknown"


def test_append_failure_record_writes_jsonl_line(tmp_path):
    record = {"issue": 1, "title": "t", "phase": "fix", "exit_code": 1,
              "postmortem": "x", "promoted_at": "2026-07-22T12:00:00Z"}
    pm.append_failure_record(record, artifacts_dir=str(tmp_path))
    jsonl = tmp_path / "factory-failures.jsonl"
    lines = jsonl.read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == record


def test_append_failure_record_appends_not_overwrites(tmp_path):
    record = {"issue": 1, "title": "t", "phase": "fix", "exit_code": 1,
              "postmortem": "x", "promoted_at": "2026-07-22T12:00:00Z"}
    pm.append_failure_record(record, artifacts_dir=str(tmp_path))
    pm.append_failure_record(record, artifacts_dir=str(tmp_path))
    lines = (tmp_path / "factory-failures.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
