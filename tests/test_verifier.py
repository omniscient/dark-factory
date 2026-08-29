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
    assert text == "STATUS: BLOCKED\nGATE_TYPE: loop:my-loop\nFINDINGS_COUNT: 1\nSEVERITY: high\n"


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
