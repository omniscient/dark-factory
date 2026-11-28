"""Executing tests for the report node's verdict header reads (#661).

The report node reads STATUS/VERDICT/CYCLES/NO_SPEC/OOS_EXCISED/OOS_TICKETS from
conformance.md and STATUS/BLOCKERS/ADVISORY from review.md. With a plain
`grep '^FIELD:'`, a second column-0 match made the value two lines: a duplicated STATUS
fell through to `*)` and dropped the section from the run comment, while the gate
(scripts/verdict_gate_check.sh) acts on the first match. These tests run the node's real
extracted bash (tests/test_push_gate_dag_exec.py's precedent) against crafted artifacts,
and compare it with the "legacy" script (the same bash with `grep -m1 ` put back to
`grep `). Only the providers/cli.py and marker calls are stubbed; no network.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW_REL = "workflows/archon-dark-factory.yaml"
_WORKFLOW = _REPO_ROOT / _WORKFLOW_REL
_ISSUE = 661

_posix_only = pytest.mark.skipif(
    os.name != "posix" or any(shutil.which(tool) is None for tool in ("bash", "git", "jq")),
    reason="needs a POSIX host with bash, git and jq (Linux/CI-only harness)",
)

# Prepended to the node's bash in ONE `bash -c` string. The node makes three python3
# calls: codehost find-change (no PR), marker factory (footer), tracker add-comment
# (the run comment, whose inline --body=... is logged). Anything else fails loudly.
_PRELUDE = r'''
python3() {
  case "$*" in
    *"codehost find-change"*) return 0 ;;
    *"marker factory"*) printf 'FOOTER-661\n'; return 0 ;;
    *"tracker add-comment"*) printf '%s' "$*" > "$PCLI_LOG"; return 0 ;;
    *) printf 'unexpected python3 call: %s\n' "$*" >&2; return 97 ;;
  esac
}
'''

# The nine in-scope reads: (variable, field, artifact, cut fields).
_SITES = [
    ("CONFORMANCE_STATUS", "STATUS", "conformance.md", "2"),
    ("CONFORMANCE_VERDICT", "VERDICT", "conformance.md", "2"),
    ("CONFORMANCE_CYCLES", "CYCLES", "conformance.md", "2"),
    ("NO_SPEC", "NO_SPEC", "conformance.md", "2"),
    ("OOS_EXCISED", "OOS_EXCISED", "conformance.md", "2"),
    ("OOS_TICKETS", "OOS_TICKETS", "conformance.md", "2-"),
    ("CR_STATUS", "STATUS", "review.md", "2"),
    ("CR_BLOCKERS", "BLOCKERS", "review.md", "2"),
    ("CR_ADVISORY", "ADVISORY", "review.md", "2"),
]
_SITE_LINES = {
    f"""{var}=$(grep -m1 '^{field}:' "$ARTIFACTS_DIR/{artifact}" | cut -d' ' -f{cut})"""
    for var, field, artifact, cut in _SITES
}

_CONF_PASS = (
    "STATUS: PASS\nGATE_TYPE: conformance\nFINDINGS_COUNT: 0\nSEVERITY: none\n"
    "VERDICT: CONFORMS\nCYCLES: 1\nNO_SPEC: false\nOOS_EXCISED: 0\nOOS_TICKETS: \n"
    "\n---\n\nATTESTATION-661\n"
)
_CONF_BLOCKED = (
    "STATUS: BLOCKED\nGATE_TYPE: conformance\nFINDINGS_COUNT: 2\nSEVERITY: critical\n"
    "VERDICT: MATERIAL\nCYCLES: 3\n\n---\n\n> dialogue\n"
)
_REVIEW_PASS = (
    "STATUS: PASS\nGATE_TYPE: code-review\nFINDINGS_COUNT: 0\nSEVERITY: none\n"
    "BLOCKERS: 0\nADVISORY: 3\nTHRESHOLD: high\n\n---\n\nfindings\n"
)
_REVIEW_BLOCKED = (
    "STATUS: BLOCKED\nGATE_TYPE: code-review\nFINDINGS_COUNT: 2\nSEVERITY: high\n"
    "BLOCKERS: 2\nADVISORY: 1\nTHRESHOLD: high\n\n---\n\nfindings\n"
)
_SKIPPED = "STATUS: SKIPPED\nREASON: disabled in config\n"

_CR_PASS_3 = "✅ Passed — 3 advisory finding(s), 0 blocking."
_CR_BLOCKED_2 = "⛔ Blocked — 2 blocking finding(s)."


def _nodes(text):
    data = yaml.safe_load(text)
    return {n["id"]: n for n in data.get("nodes", []) if isinstance(n, dict) and "id" in n}


def _report_bash():
    return _nodes(_WORKFLOW.read_text(encoding="utf-8"))["report"]["bash"]


def _legacy(bash):
    return bash.replace("grep -m1 ", "grep ")


@pytest.fixture()
def env(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", f"refs/heads/feat/issue-{_ISSUE}-test"],
                   cwd=str(work), check=True)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "issue.json").write_text(json.dumps({"resolved_number": _ISSUE}))
    return {"tmp": tmp_path, "work": work, "artifacts": artifacts, "log": tmp_path / "pcli.log"}


def _write(env, name, content):
    path = env["artifacts"] / name
    if content is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(content.encode("utf-8"))  # bytes: keep \r\n exactly as given


def _run(env, bash):
    env["log"].unlink(missing_ok=True)
    script = _PRELUDE + bash.replace("$parse-intent.output.intent", "new")
    run_env = dict(os.environ)
    run_env.update({
        "CLONE_DIR": str(env["tmp"]),
        "ARTIFACTS_DIR": str(env["artifacts"]),
        "PCLI_LOG": str(env["log"]),
    })
    result = subprocess.run(["bash", "-c", script], cwd=str(env["work"]), env=run_env,
                            capture_output=True, text=True, timeout=60)
    body = env["log"].read_text(encoding="utf-8") if env["log"].exists() else ""
    return result.returncode, body, result.stderr


def _run_both(env, conformance, review):
    _write(env, "conformance.md", conformance)
    _write(env, "review.md", review)
    bash = _report_bash()
    return _run(env, bash), _run(env, _legacy(bash))


@_posix_only
class TestDuplicatedHeaders:
    def test_duplicated_status_renders_from_first_match(self, env):
        (code, body, err), (_, legacy_body, _) = _run_both(
            env,
            _CONF_PASS + "STATUS: BLOCKED\n",
            _REVIEW_PASS + "STATUS: BLOCKED\n",
        )
        assert code == 0, err
        assert "--body=## Dark Factory Run" in body, body
        assert "### Spec Conformance\n\n\nATTESTATION-661" in body, body
        assert f"### Code Review\n\n{_CR_PASS_3}" in body, body
        # The legacy reads drop both sections: proves this fixture detects the bug.
        assert "--body=## Dark Factory Run" in legacy_body, legacy_body
        assert "### Spec Conformance" not in legacy_body, legacy_body
        assert "### Code Review" not in legacy_body, legacy_body

    def test_duplicated_blocker_count_renders_on_one_line(self, env):
        (code, body, err), (_, legacy_body, _) = _run_both(
            env, None, _REVIEW_BLOCKED + "BLOCKERS: 7\nADVISORY: 9\n")
        assert code == 0, err
        assert _CR_BLOCKED_2 in body, body
        assert "⛔ Blocked — 2\n7 blocking" in legacy_body, legacy_body

    def test_duplicated_advisory_count_renders_on_one_line(self, env):
        (code, body, err), (_, legacy_body, _) = _run_both(
            env, None, _REVIEW_PASS + "BLOCKERS: 7\nADVISORY: 9\n")
        assert code == 0, err
        assert _CR_PASS_3 in body, body
        assert "✅ Passed — 3\n9 advisory" in legacy_body, legacy_body


_SINGLE_HEADER_CASES = {
    "pass_pass": (_CONF_PASS, _REVIEW_PASS),
    "blocked_blocked": (_CONF_BLOCKED, _REVIEW_BLOCKED),
    "skipped_both": (_SKIPPED, _SKIPPED),
    "zero_reviewable_content": (
        _CONF_PASS,
        "STATUS: BLOCKED\nGATE_TYPE: code-review\nFINDINGS_COUNT: 0\nSEVERITY: none\n"
        "BLOCKERS: 0\nADVISORY: 0\n"
        "REASON: diff_rank summarized the entire diff to zero reviewable content\n",
    ),
    "review_error": (_CONF_PASS, "STATUS: ERROR\nREASON: reviewer timed out\n"),
    "no_status_line": ("VERDICT: CONFORMS\nCYCLES: 1\n", "BLOCKERS: 1\nADVISORY: 2\n"),
    "empty_files": ("", ""),
    "no_artifacts": (None, None),
}


@_posix_only
class TestSingleHeaderUnchanged:
    @pytest.mark.parametrize("case", sorted(_SINGLE_HEADER_CASES))
    def test_real_equals_legacy(self, env, case):
        conformance, review = _SINGLE_HEADER_CASES[case]
        (code, body, err), (legacy_code, legacy_body, _) = _run_both(env, conformance, review)
        assert code == legacy_code == 0, err
        assert "--body=## Dark Factory Run" in body, body
        assert body == legacy_body

    def test_blocked_wording(self, env):
        (code, body, err), _ = _run_both(env, _CONF_BLOCKED, _REVIEW_BLOCKED)
        assert code == 0, err
        assert f"### Code Review\n\n{_CR_BLOCKED_2}" in body, body
        # No BLOCKED branch for conformance in the report node: no section, today and after.
        assert "### Spec Conformance" not in body, body

    def test_crlf_unchanged(self, env):
        (code, body, err), (legacy_code, legacy_body, _) = _run_both(
            env, _CONF_PASS.replace("\n", "\r\n"), _REVIEW_PASS.replace("\n", "\r\n"))
        assert code == legacy_code, err
        assert body == legacy_body


class TestConfinement:
    def test_m1_on_exactly_the_nine_sites(self):
        lines = [line.strip() for line in _report_bash().splitlines()]
        assert {line for line in lines if "grep -m1" in line} == _SITE_LINES
        assert sum("grep -m1" in line for line in lines) == len(_SITES)
        for artifact in ("blast.md", "conflict_resolution.md"):
            reads = [line for line in lines if f'"$ARTIFACTS_DIR/{artifact}"' in line]
            assert reads, f"expected {artifact} reads in the report node"
            assert not any("-m1" in line for line in reads), reads

    def test_only_report_bash_differs_from_merge_base(self):
        # Diffs against the branch's fork point, not origin/main's tip, so unrelated
        # workflow changes landing on main do not fail it. Self-retires once the fix
        # is on the base (on main after merge, and on every later branch).
        def git(*args):
            return subprocess.run(["git", *args], cwd=str(_REPO_ROOT),
                                  capture_output=True, text=True)

        if shutil.which("git") is None:
            pytest.skip("git not available")
        base = git("merge-base", "HEAD", "origin/main")
        if base.returncode != 0:
            pytest.skip("origin/main not resolvable (e.g. CI's depth-1 checkout)")
        shown = git("show", f"{base.stdout.strip()}:{_WORKFLOW_REL}")
        assert shown.returncode == 0, shown.stderr
        base_text = shown.stdout
        work_text = _WORKFLOW.read_text(encoding="utf-8")
        base_nodes, work_nodes = _nodes(base_text), _nodes(work_text)
        if "grep -m1 " in base_nodes["report"]["bash"]:
            pytest.skip("base already carries the #661 reads; nothing to diff against")

        assert list(work_nodes) == list(base_nodes)
        for node_id in base_nodes:
            if node_id != "report":
                assert work_nodes[node_id] == base_nodes[node_id], node_id
        work_report, base_report = dict(work_nodes["report"]), dict(base_nodes["report"])
        assert _legacy(work_report.pop("bash")) == base_report.pop("bash")
        assert work_report == base_report
        # Byte level too: parsed-YAML equality cannot see a comment or whitespace edit
        # outside the node bodies, and the spec's rule is "touch nothing else".
        base_lines, work_lines = base_text.splitlines(), work_text.splitlines()
        assert len(work_lines) == len(base_lines)
        changed = [(b, w) for b, w in zip(base_lines, work_lines) if b != w]
        assert len(changed) == len(_SITES), changed
        assert all(_legacy(w) == b for b, w in changed), changed


def test_dag_validators_pass():
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    from check_workflow_dag import check as check_dag  # noqa: PLC0415
    assert check_dag(_WORKFLOW) == []
    for script in ("check_workflow_dag.py", "check_workflow_when.py"):
        result = subprocess.run(
            [sys.executable, str(_REPO_ROOT / "scripts" / script), str(_WORKFLOW)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"{script}: {result.stdout}{result.stderr}"
