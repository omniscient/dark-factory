import os
import subprocess
import sys, pathlib

# .factory/hooks/{validate,smoke-gate} run `python -m pytest tests/ -q` with no
# PYTHONPATH=scripts — self-insert so this file collects there too, not only in CI.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

import pytest

from factory_core import verifier

_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "verifiers"


def _env(tmp_path):
    return {**os.environ, "CLONE_DIR": str(tmp_path), "ARTIFACTS_DIR": str(tmp_path),
            "ISSUE_NUM": "197", "LOOP_NAME": "test-loop", "FACTORY_REPO_SLUG": "omniscient/dark-factory"}


def test_resolve_verifier_joins_clone_dir():
    resolved = verifier.resolve_verifier("/clone", "scripts/my-verifier.sh")
    assert resolved == "/clone/scripts/my-verifier.sh"


def test_resolve_verifier_rejects_absolute_path(tmp_path):
    with pytest.raises(verifier.VerifierError, match="absolute"):
        verifier.resolve_verifier(str(tmp_path), str(_FIXTURES / "structured_pass.sh"))


def test_resolve_verifier_rejects_parent_escape(tmp_path):
    with pytest.raises(verifier.VerifierError, match="escapes"):
        verifier.resolve_verifier(str(tmp_path), "../outside.sh")


def test_resolve_verifier_in_tree_path_resolves(tmp_path):
    (tmp_path / "scripts").mkdir()
    target = tmp_path / "scripts" / "verify.sh"
    target.write_text("#!/usr/bin/env bash\nexit 0\n")
    resolved = verifier.resolve_verifier(str(tmp_path), "./scripts/../scripts/verify.sh")
    assert resolved == os.path.realpath(str(target))


def test_run_verifier_structured_pass(tmp_path):
    exit_code, stdout = verifier.run_verifier(str(_FIXTURES / "structured_pass.sh"), _env(tmp_path))
    assert exit_code == 0
    assert stdout.startswith("STATUS: PASS")


def test_run_verifier_missing_path_raises(tmp_path):
    with pytest.raises(verifier.VerifierError):
        verifier.run_verifier(str(tmp_path / "does-not-exist.sh"), _env(tmp_path))


def test_run_verifier_non_executable_path_raises(tmp_path):
    p = tmp_path / "not-executable.sh"
    p.write_text("#!/usr/bin/env bash\nexit 0\n")
    with pytest.raises(verifier.VerifierError):
        verifier.run_verifier(str(p), _env(tmp_path))


def test_run_verifier_timeout_raises(tmp_path):
    with pytest.raises(verifier.VerifierError):
        verifier.run_verifier(str(_FIXTURES / "sleeper.sh"), _env(tmp_path), timeout=1)


def test_normalize_verdict_structured_blocked_with_exit_0_stays_blocked_and_renamespaces_gate_type():
    stdout = "STATUS: BLOCKED\nGATE_TYPE: whatever\nFINDINGS_COUNT: 2\nSEVERITY: high\n"
    text = verifier.normalize_verdict(exit_code=0, stdout=stdout, gate_type="loop:my-loop")
    assert text == "STATUS: BLOCKED\nGATE_TYPE: loop:my-loop\nFINDINGS_COUNT: 2\nSEVERITY: high\n"


def test_normalize_verdict_bare_exit_0_synthesizes_pass():
    text = verifier.normalize_verdict(exit_code=0, stdout="", gate_type="loop:my-loop")
    assert text == "STATUS: PASS\nGATE_TYPE: loop:my-loop\nFINDINGS_COUNT: 0\nSEVERITY: none\n"


def test_normalize_verdict_bare_nonzero_synthesizes_blocked_high():
    text = verifier.normalize_verdict(exit_code=1, stdout="", gate_type="loop:my-loop")
    assert text == "STATUS: BLOCKED\nGATE_TYPE: loop:my-loop\nFINDINGS_COUNT: 1\nSEVERITY: high\n"


def test_normalize_verdict_structured_error_is_not_pass_through():
    # Requirement 4: ERROR is reserved for "the verifier self-reported it could
    # not complete" and is explicitly NOT auto-pass-through for target verifiers
    # (unlike code_review.fail_open's advisory-on-error convention) -- verbatim
    # ERROR would sail through verdict_gate_check.sh's PASS/SKIPPED/ERROR proceed
    # set, defeating AC3's "missing/failing cannot hand off" default.
    stdout = "STATUS: ERROR\nGATE_TYPE: whatever\nFINDINGS_COUNT: 0\nSEVERITY: none\n"
    text = verifier.normalize_verdict(exit_code=0, stdout=stdout, gate_type="loop:my-loop")
    assert text == (
        "STATUS: BLOCKED\nGATE_TYPE: loop:my-loop\nFINDINGS_COUNT: 1\nSEVERITY: high\n"
        "REASON: verifier self-reported ERROR\n"
    )


def test_normalize_verdict_structured_error_keeps_reason_line():
    stdout = "STATUS: ERROR\nGATE_TYPE: whatever\nFINDINGS_COUNT: 0\nSEVERITY: none\n"
    text = verifier.normalize_verdict(exit_code=0, stdout=stdout, gate_type="loop:my-loop")
    assert text.startswith("STATUS: BLOCKED\nGATE_TYPE: loop:my-loop\n")
    assert "REASON: verifier self-reported ERROR\n" in text


def test_normalize_verdict_clamps_bogus_severity_from_structured_stdout():
    stdout = "STATUS: BLOCKED\nGATE_TYPE: whatever\nFINDINGS_COUNT: -2\nSEVERITY: bogus\n"
    text = verifier.normalize_verdict(exit_code=0, stdout=stdout, gate_type="loop:my-loop")
    assert text == "STATUS: BLOCKED\nGATE_TYPE: loop:my-loop\nFINDINGS_COUNT: 0\nSEVERITY: none\n"


def test_normalize_verdict_structured_pass_with_nonzero_exit_blocks():
    # Fail-closed reading (Requirement 4): a verifier that prints PASS (or SKIPPED)
    # and then exits non-zero has failed -- the non-zero exit wins over the
    # proceed-status, so a crash after a premature PASS cannot hand off.
    stdout = "STATUS: PASS\nGATE_TYPE: whatever\nFINDINGS_COUNT: 0\nSEVERITY: none\n"
    text = verifier.normalize_verdict(exit_code=1, stdout=stdout, gate_type="loop:my-loop")
    assert text == "STATUS: BLOCKED\nGATE_TYPE: loop:my-loop\nFINDINGS_COUNT: 1\nSEVERITY: high\n"


def _loop_entry(**overrides):
    entry = {
        "name": "nightly-scan-triage",
        "purpose": "nightly scan triage",
        "discovery": {"trigger": "cron:0 6 * * *", "inputs": ["scripts/scanner.py"]},
        "handoff": {"outputs": ["artifacts/scan-report.md"], "manifest": "artifacts/manifest.json"},
        "verification": {"verifier": "scripts/verify-scan.sh", "stop_condition": "manifest present"},
        "persistence": {"artifacts": ["artifacts/scan-history.jsonl"]},
        "scheduling": {"failure_behavior": "retry-once"},
        "side_effect_level": 2,
    }
    entry.update(overrides)
    return entry


def test_assert_verifier_independent_passes_when_disjoint():
    verifier.assert_verifier_independent(_loop_entry())  # no raise


def test_assert_verifier_independent_rejects_manifest_collision():
    entry = _loop_entry()
    entry["verification"]["verifier"] = entry["handoff"]["manifest"]
    with pytest.raises(verifier.VerifierError):
        verifier.assert_verifier_independent(entry)


def test_assert_verifier_independent_rejects_outputs_collision():
    entry = _loop_entry()
    entry["verification"]["verifier"] = entry["handoff"]["outputs"][0]
    with pytest.raises(verifier.VerifierError):
        verifier.assert_verifier_independent(entry)


def test_assert_verifier_independent_rejects_persistence_artifacts_collision():
    entry = _loop_entry()
    entry["verification"]["verifier"] = entry["persistence"]["artifacts"][0]
    with pytest.raises(verifier.VerifierError):
        verifier.assert_verifier_independent(entry)


def test_assert_verifier_independent_normalizes_paths_before_comparing():
    entry = _loop_entry()
    entry["verification"]["verifier"] = "./artifacts/../artifacts/manifest.json"
    with pytest.raises(verifier.VerifierError):
        verifier.assert_verifier_independent(entry)


def test_resolve_and_run_env_contract(tmp_path, monkeypatch):
    verifier_script = tmp_path / "verifier.sh"
    verifier_script.write_text((_FIXTURES / "env_check.sh").read_text())
    verifier_script.chmod(0o755)
    dump_path = tmp_path / "envdump.txt"
    monkeypatch.setenv("ENV_DUMP_PATH", str(dump_path))
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    verifier.resolve_and_run(
        clone_dir=str(tmp_path), loop_name="test-loop", verifier_path="verifier.sh",
        issue_num="197", factory_repo_slug="omniscient/dark-factory", side_effect_level=1,
    )
    dumped = dump_path.read_text()
    assert f"CLONE_DIR={tmp_path}" in dumped
    assert f"ARTIFACTS_DIR={tmp_path}" in dumped
    assert "ISSUE_NUM=197" in dumped
    assert "LOOP_NAME=test-loop" in dumped
    assert "FACTORY_REPO_SLUG=omniscient/dark-factory" in dumped


def test_resolve_and_run_gate_type_namespaced_to_loop(tmp_path):
    verifier_script = tmp_path / "verifier.sh"
    verifier_script.write_text((_FIXTURES / "structured_pass.sh").read_text())
    verifier_script.chmod(0o755)
    text = verifier.resolve_and_run(
        clone_dir=str(tmp_path), loop_name="my-loop", verifier_path="verifier.sh", side_effect_level=1,
    )
    assert "GATE_TYPE: loop:my-loop" in text
    assert "ignored-by-normalize_verdict" not in text


def test_resolve_and_run_fails_closed_on_missing_verifier(tmp_path):
    text = verifier.resolve_and_run(
        clone_dir=str(tmp_path), loop_name="my-loop", verifier_path="does-not-exist.sh", side_effect_level=1,
    )
    assert "STATUS: BLOCKED" in text
    assert "GATE_TYPE: loop:my-loop" in text


def test_resolve_and_run_fails_closed_on_escaping_verifier_path(tmp_path):
    outside = tmp_path.parent / "escaped-verifier.sh"
    outside.write_text((_FIXTURES / "structured_pass.sh").read_text())
    outside.chmod(0o755)
    try:
        text = verifier.resolve_and_run(
            clone_dir=str(tmp_path), loop_name="my-loop",
            verifier_path="../escaped-verifier.sh", side_effect_level=1,
        )
    finally:
        outside.unlink()
    assert text.startswith("STATUS: BLOCKED\n")
    assert "GATE_TYPE: loop:my-loop" in text


def test_resolve_and_run_fails_closed_on_absolute_verifier_path(tmp_path):
    text = verifier.resolve_and_run(
        clone_dir=str(tmp_path), loop_name="my-loop",
        verifier_path=str(_FIXTURES / "structured_pass.sh"), side_effect_level=1,
    )
    assert text.startswith("STATUS: BLOCKED\n")


def test_resolve_and_run_fails_closed_when_side_effect_level_undetermined(tmp_path):
    text = verifier.resolve_and_run(
        clone_dir=str(tmp_path), loop_name="my-loop", verifier_path="anything.sh", side_effect_level=None,
    )
    assert "STATUS: BLOCKED" in text


def test_resolve_and_run_fails_closed_for_factory_owned_level(tmp_path):
    verifier_script = tmp_path / "verifier.sh"
    verifier_script.write_text((_FIXTURES / "structured_pass.sh").read_text())
    verifier_script.chmod(0o755)
    text = verifier.resolve_and_run(
        clone_dir=str(tmp_path), loop_name="my-loop", verifier_path="verifier.sh", side_effect_level=4,
    )
    assert "STATUS: BLOCKED" in text
    assert "factory-owned level requires #196 profile enforcement" in text


def test_resolve_and_run_records_required_profile_level_1(tmp_path):
    verifier_script = tmp_path / "verifier.sh"
    verifier_script.write_text((_FIXTURES / "structured_pass.sh").read_text())
    verifier_script.chmod(0o755)
    text = verifier.resolve_and_run(
        clone_dir=str(tmp_path), loop_name="my-loop", verifier_path="verifier.sh", side_effect_level=1,
    )
    assert "REQUIRED_PROFILE: level-1" in text


def test_resolve_and_run_records_side_effect_level_on_success(tmp_path):
    # Requirement 6(a): side_effect_level is recorded on the verdict so a future
    # #196 enforcement layer has something to check against.
    verifier_script = tmp_path / "verifier.sh"
    verifier_script.write_text((_FIXTURES / "structured_pass.sh").read_text())
    verifier_script.chmod(0o755)
    text = verifier.resolve_and_run(
        clone_dir=str(tmp_path), loop_name="my-loop", verifier_path="verifier.sh", side_effect_level=2,
    )
    assert "SIDE_EFFECT_LEVEL: 2" in text


def test_resolve_and_run_records_side_effect_level_when_factory_owned(tmp_path):
    text = verifier.resolve_and_run(
        clone_dir=str(tmp_path), loop_name="my-loop", verifier_path="anything.sh", side_effect_level=5,
    )
    assert "SIDE_EFFECT_LEVEL: 5" in text


def test_cli_default_timeout_is_300():
    assert verifier.DEFAULT_TIMEOUT_SECONDS == 300


def test_cli_refuses_reserved_out_basenames(tmp_path):
    verifier_script = tmp_path / "verifier.sh"
    verifier_script.write_text((_FIXTURES / "structured_pass.sh").read_text())
    verifier_script.chmod(0o755)
    out_path = tmp_path / "review.md"
    result = subprocess.run(
        [sys.executable, "-m", "factory_core.verifier",
         "--clone-dir", str(tmp_path), "--loop-name", "my-loop",
         "--verifier-path", "verifier.sh", "--side-effect-level", "1",
         "run", "--out", str(out_path)],
        cwd=str(pathlib.Path(__file__).parent.parent / "scripts"),
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert not out_path.exists()


def test_cli_writes_verdict_to_out_path(tmp_path):
    verifier_script = tmp_path / "verifier.sh"
    verifier_script.write_text((_FIXTURES / "structured_pass.sh").read_text())
    verifier_script.chmod(0o755)
    out_path = tmp_path / "loop-verdict.md"
    result = subprocess.run(
        [sys.executable, "-m", "factory_core.verifier",
         "--clone-dir", str(tmp_path), "--loop-name", "my-loop",
         "--verifier-path", "verifier.sh", "--side-effect-level", "1",
         "run", "--out", str(out_path)],
        cwd=str(pathlib.Path(__file__).parent.parent / "scripts"),
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "STATUS: PASS" in out_path.read_text()
    assert "GATE_TYPE: loop:my-loop" in out_path.read_text()


def test_cli_timeout_override(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "factory_core.verifier",
         "--clone-dir", str(_FIXTURES), "--loop-name", "my-loop",
         "--verifier-path", "sleeper.sh", "--side-effect-level", "1", "--timeout", "1",
         "run", "--out", str(tmp_path / "out.md")],
        cwd=str(pathlib.Path(__file__).parent.parent / "scripts"),
        capture_output=True, text=True,
    )
    assert result.returncode == 0  # CLI itself succeeds; the timeout is recorded as BLOCKED
    assert "STATUS: BLOCKED" in (tmp_path / "out.md").read_text()


import json
from pathlib import Path
from factory_core.verifier import resolve_verifier, run_verifier, normalize_verdict

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_run_verifier_forwards_clone_dir_to_child_env(tmp_path):
    """Probes the actual #197 behavior this task's whole test-seam design depends
    on, rather than assuming it. If this fails, run_verifier only forwards a fixed
    whitelist that excludes a caller-supplied CLONE_DIR override — in which case
    every _fixture_env-based test above needs a different seam (e.g. writing the
    fixture file into the *real*, resolved CLONE_DIR that resolve_verifier used,
    rather than a caller-chosen override path), and this plan's design note is wrong
    and must be revised before continuing Task 17."""
    env = {"CLONE_DIR": str(tmp_path), "ISSUE_NUM": "1", "PATH": os.environ["PATH"]}
    probe = tmp_path / "probe.py"
    # #197's run_verifier raises VerifierError (verified: os.access(X_OK) guard) on
    # a path that exists but isn't executable; a shebang + the executable bit are
    # required here or this probe fails for the wrong reason (not-executable), not
    # the thing it's actually meant to test (env forwarding).
    probe.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys; sys.stdout.write(os.environ.get('CLONE_DIR', 'MISSING'))\n"
    )
    probe.chmod(0o755)
    exit_code, stdout = run_verifier(str(probe), env)
    assert str(tmp_path) in stdout


def _fixture_env(tmp_path, issue_num, comments):
    """Reuses Task 16's explicit-env-var JSON fixture seam (not a PYTHONPATH/module
    swap), so it survives the real subprocess boundary here. Deliberately not a
    CLONE_DIR-relative filename: CLONE_DIR is the agent-writable working clone in
    production. #197's resolve_and_run() forwards the factory process environment
    (dict(os.environ) + CLONE_DIR/ARTIFACTS_DIR/ISSUE_NUM/FACTORY_REPO_SLUG/
    LOOP_NAME overlays) and run_verifier() passes the env it is given verbatim
    (test_run_verifier_forwards_clone_dir_to_child_env), so
    COST_REPORT_MARKER_CHECK_TEST_FIXTURE_PATH is honoured only when set in the
    factory process env, which is trusted; it is not reachable from agent-writable
    clone files."""
    clone_dir = tmp_path / "clone"
    clone_dir.mkdir()
    fixture = tmp_path / ".cost_report_marker_check_test_fixture.json"
    fixture.write_text(json.dumps({"comments": comments}))
    return {
        "ISSUE_NUM": str(issue_num),
        "CLONE_DIR": str(clone_dir),
        "COST_REPORT_MARKER_CHECK_TEST_FIXTURE_PATH": str(fixture),
        "PATH": os.environ["PATH"],
    }


def test_cost_report_marker_predicate_blocked_when_absent(tmp_path):
    env = _fixture_env(tmp_path, 300, [{"body": "unrelated comment"}])
    resolved = resolve_verifier(str(REPO_ROOT), "scripts/cost_report_marker_check.py")
    exit_code, stdout = run_verifier(resolved, env)
    verdict = normalize_verdict(exit_code, stdout, gate_type="stop_condition")
    assert "STATUS: BLOCKED" in verdict


def test_cost_report_marker_predicate_passes_when_present_end_of_run(tmp_path):
    comments = [{"body": "unrelated"}, {"body": "## Cost Report\n<!-- dark-factory-cost-report -->"}]
    env = _fixture_env(tmp_path, 300, comments)
    resolved = resolve_verifier(str(REPO_ROOT), "scripts/cost_report_marker_check.py")
    exit_code, stdout = run_verifier(resolved, env)
    verdict = normalize_verdict(exit_code, stdout, gate_type="stop_condition")
    assert "STATUS: PASS" in verdict


def test_cost_report_marker_predicate_passes_when_present_updated_in_place(tmp_path):
    """#311's own invariant: 'posted early, updated in place under the same marker'
    must PASS identically to 'posted once at run end' — the predicate checks marker
    presence, not the path that produced it. Deliberately a single comment (not the
    two-comments-marker-last shape of the prior case) so this is a structurally
    different fixture, not the same list twice."""
    comments = [{"body": "## Cost Report\n<!-- dark-factory-cost-report -->\n(updated)"}]
    env = _fixture_env(tmp_path, 300, comments)
    resolved = resolve_verifier(str(REPO_ROOT), "scripts/cost_report_marker_check.py")
    exit_code, stdout = run_verifier(resolved, env)
    verdict = normalize_verdict(exit_code, stdout, gate_type="stop_condition")
    assert "STATUS: PASS" in verdict
