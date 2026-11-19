"""docs_verdict_gate_check.sh (#535, Gate 2.5): observe always proceeds, enforce fails
closed on everything but PASS/SKIPPED — including ERROR, which verdict_gate_check.sh
passes for Gate 3's fail_open contract.

CI has no model access, so the five semantic scenarios from the spec (Requirement 14)
are driven by stubbed docs-conformance.md verdicts standing in for what a real reviewer
would write. These tests prove the plumbing (parsing, the mode branch, no tracker side
effects in observe mode, exit 1 = no PR in enforce mode) — not that a reviewer judges
any scenario correctly; that is measured from recorded observe-mode verdicts (#536).
The tracker CLI is replaced by the exported python3-function precedent from
tests/test_push_gate_dag_exec.py, which logs every call.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GATE = _REPO_ROOT / "scripts" / "docs_verdict_gate_check.sh"
_LABEL = "Docs Conformance (Gate 2.5)"

pytestmark = pytest.mark.skipif(
    os.name != "posix" or any(shutil.which(t) is None for t in ("bash", "jq")),
    reason="needs a POSIX host with bash and jq",
)

_PRELUDE = r'''
python3() {
  case "$*" in
    *providers/cli.py*)
      printf 'CALL %s\n' "$*" >> "$PCLI_LOG"
      case "$*" in
        *"tracker get"*)
          if [ "${NEEDS_DISCUSSION:-false}" = "true" ]; then
            printf '{"labels":[{"name":"needs-discussion"}]}\n'
          else
            printf '{"labels":[]}\n'
          fi ;;
      esac
      return 0 ;;
    *factory_core/cli.py*marker*) echo "*Posted by Test Factory*" ;;
    *) command python3 "$@" ;;
  esac
}
export -f python3
'''


def _verdict(status, findings=0, severity="none", verdict="CONFORMS"):
    return (f"STATUS: {status}\nGATE_TYPE: docs-conformance\nFINDINGS_COUNT: {findings}\n"
            f"SEVERITY: {severity}\nVERDICT: {verdict}\n\n---\n\n(stubbed reviewer dialogue)\n")


# Requirement 11/14's five semantic scenarios, each with the verdict a correct reviewer
# would have written for it.
SCENARIOS = {
    "policy-markdown-change-documented": _verdict("PASS"),
    "scheduler-config-artifact-change-undocumented": _verdict("BLOCKED", 2, "high", "MATERIAL"),
    "target-structural-change-documented": _verdict("PASS", 1, "low", "MINOR"),
    "docs-neutral-defensible-no-impact": _verdict("PASS"),
    "misleading-but-present-doc-update": _verdict("BLOCKED", 1, "high", "MATERIAL"),
}

# Fail-closed inputs: everything a reviewer crash, refusal or bad write can leave behind.
FAIL_CLOSED = {
    "missing-file": None,
    "malformed-status": "STATUS:\nnot a verdict\n",
    "no-status-line": "the reviewer refused\n",
    "error-status": "STATUS: ERROR\nREASON: reviewer timed out\n",
    "uncertain-written-as-blocked": _verdict("BLOCKED", 1, "high", "UNCERTAIN"),
    "unknown-token": "STATUS: HUMAN_REQUIRED\n",
}


def _run(tmp_path, content, mode, issue="535", needs_discussion=False, extra_args=None):
    verdict = tmp_path / "docs-conformance.md"
    if content is not None:
        verdict.write_text(content)
    log = tmp_path / "pcli.log"
    log.write_text("")
    args = [str(verdict), issue, _LABEL] + ([] if mode is None else [mode])
    if extra_args is not None:
        args = extra_args
    quoted = " ".join("'" + a.replace("'", "'\\''") + "'" for a in args)
    env = dict(os.environ, PCLI_LOG=str(log),
               NEEDS_DISCUSSION="true" if needs_discussion else "false")
    proc = subprocess.run(["bash", "-c", f"{_PRELUDE}\nbash '{_GATE}' {quoted}"],
                          env=env, capture_output=True, text=True)
    return proc.returncode, log.read_text(), proc.stderr


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_observe_mode_proceeds_for_every_scenario_with_no_side_effects(tmp_path, name):
    rc, log, err = _run(tmp_path, SCENARIOS[name], "observe")
    assert rc == 0
    assert log == "", f"observe mode must make no tracker call, got: {log}"
    assert "observe mode" in err


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_enforce_mode_blocks_exactly_the_blocked_scenarios(tmp_path, name):
    rc, log, _ = _run(tmp_path, SCENARIOS[name], "enforce")
    expect_block = SCENARIOS[name].startswith("STATUS: BLOCKED")
    assert rc == (1 if expect_block else 0), name
    assert "tracker comment" not in log, "a BLOCKED verdict already carries the command's own comment"


@pytest.mark.parametrize("name", sorted(FAIL_CLOSED))
def test_enforce_mode_fails_closed(tmp_path, name):
    rc, _, _ = _run(tmp_path, FAIL_CLOSED[name], "enforce")
    assert rc == 1, f"{name} must block push-and-pr in enforce mode"


@pytest.mark.parametrize("name", sorted(FAIL_CLOSED))
def test_observe_mode_logs_would_block_but_proceeds(tmp_path, name):
    rc, log, err = _run(tmp_path, FAIL_CLOSED[name], "observe")
    assert rc == 0
    assert log == ""
    assert "enforce would block" in err


def test_enforce_error_status_posts_marker_comment_when_unexplained(tmp_path):
    rc, log, _ = _run(tmp_path, FAIL_CLOSED["error-status"], "enforce")
    assert rc == 1
    assert "tracker comment" in log and "--marker <!-- df-push-gate-failure -->" in log


def test_enforce_missing_file_with_needs_discussion_blocks_quietly(tmp_path):
    rc, log, _ = _run(tmp_path, None, "enforce", needs_discussion=True)
    assert rc == 1
    assert "tracker comment" not in log


def test_enforce_skipped_proceeds(tmp_path):
    rc, log, _ = _run(tmp_path, "STATUS: SKIPPED\nREASON: docs_conformance.enabled=false\n", "enforce")
    assert rc == 0 and log == ""


@pytest.mark.parametrize("mode", [None, "", "Enforce", "block", "observe"])
def test_anything_but_literal_enforce_is_observe(tmp_path, mode):
    rc, log, err = _run(tmp_path, FAIL_CLOSED["error-status"], mode)
    assert rc == 0 and log == ""
    assert "observe mode" in err


def test_enforce_non_numeric_issue_fails_closed_without_tracker_call(tmp_path):
    rc, log, _ = _run(tmp_path, None, "enforce", issue="null")
    assert rc == 1 and log == ""


@pytest.mark.parametrize("argc", [0, 1, 2])
def test_usage_errors(tmp_path, argc):
    args = [str(tmp_path / "v.md"), "535", _LABEL][:argc]
    rc, _, _ = _run(tmp_path, None, None, extra_args=args)
    assert rc != 0


def test_script_is_not_verdict_gate_check():
    """ERROR must stay outside this script's accept-list (Requirement 3)."""
    text = _GATE.read_text(encoding="utf-8")
    assert "PASS|SKIPPED)" in text
    assert "PASS|SKIPPED|ERROR" not in text



# ── optional 5th argument: the branch changed during the read-only docs-conformance node ──
_TAMPER = "HEAD moved abc -> def"


def _run_tampered(tmp_path, content, mode, needs_discussion=False):
    verdict = tmp_path / "docs-conformance.md"
    return _run(tmp_path, content, mode, needs_discussion=needs_discussion,
                extra_args=[str(verdict), "535", _LABEL, mode, _TAMPER])


def test_enforce_tamper_note_blocks_a_pass_verdict_and_comments(tmp_path):
    rc, log, err = _run_tampered(tmp_path, SCENARIOS["policy-markdown-change-documented"], "enforce")
    assert rc == 1
    assert "tracker comment" in log and "--marker <!-- df-push-gate-failure -->" in log
    assert _TAMPER in err


def test_enforce_tamper_note_comments_even_for_a_blocked_verdict(tmp_path):
    """The command's own BLOCKED comment says nothing about the branch having changed."""
    rc, log, _ = _run_tampered(tmp_path, SCENARIOS["misleading-but-present-doc-update"], "enforce")
    assert rc == 1 and "tracker comment" in log


def test_enforce_tamper_note_with_needs_discussion_blocks_quietly(tmp_path):
    rc, log, _ = _run_tampered(tmp_path, SCENARIOS["policy-markdown-change-documented"], "enforce",
                               needs_discussion=True)
    assert rc == 1 and "tracker comment" not in log


def test_observe_tamper_note_logs_would_block_and_proceeds(tmp_path):
    rc, log, err = _run_tampered(tmp_path, SCENARIOS["policy-markdown-change-documented"], "observe")
    assert rc == 0 and log == ""
    assert "enforce would block" in err and _TAMPER in err


def test_empty_tamper_note_changes_nothing(tmp_path):
    verdict = tmp_path / "docs-conformance.md"
    rc, log, _ = _run(tmp_path, SCENARIOS["policy-markdown-change-documented"], "enforce",
                      extra_args=[str(verdict), "535", _LABEL, "enforce", ""])
    assert rc == 0 and log == ""
