# `report` node: `grep -m1` header reads — Implementation Plan

**Issue:** #661

**Goal:** Make the `report` DAG node read the nine verdict header fields of
`conformance.md` and `review.md` with `grep -m1`, matching the first-match semantics of
`scripts/verdict_gate_check.sh:38`, so a duplicated column-0 header line can no longer
drop a section from (or split a count across lines in) the posted run comment.

**Spec:** `docs/superpowers/specs/2026-11-28-report-node-grep-m1-design.md`

**Architecture:** Line-local edit inside the `report` node's `bash:` block in
`workflows/archon-dark-factory.yaml` — ` -m1` inserted after `grep` on 9 lines, nothing
else. Proven by a new executing pytest file that extracts the node's real bash from the
parsed YAML and runs it (with `python3` stubbed as a shell function) against crafted
artifacts, comparing it to a "legacy" copy of the same bash with `grep -m1 ` put back to
`grep `. The precedent is `tests/test_push_gate_dag_exec.py` (#473).

**Tech Stack:** bash, GNU grep/cut, Python 3 + pytest + PyYAML.

**Expected hold:** `workflows/` is in `.factory/adapter.yaml`'s
`safety.critical_diff_paths`, so the blast-radius gate will hold the PR for the owner's
signature. That is expected; do not try to avoid it.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `tests/test_report_node_header_reads_exec.py` | Create | Executing tests T1-T4 (duplicated headers, single-header byte-identity, CRLF), structural confinement T5, DAG checks T6 |
| `workflows/archon-dark-factory.yaml` | Modify (9 lines, `report` node only) | `grep '^FIELD:'` → `grep -m1 '^FIELD:'` on the 6 `conformance.md` + 3 `review.md` reads |

No other file changes. No `.github/workflows/ci.yml` edit: `tests/*.py` is auto-discovered
by `python -m pytest tests/ -v`. The file stays at 1800 lines, so no `file:line` citation in
`docs/` shifts. Not touched: gate scripts, `scripts/factory_core/run_record.py`, the
`report` node's `blast.md` and `conflict_resolution.md` reads, any other node, and the
`dark-factory/scripts/` TARGET-PATH copies.

## Spec → test mapping

| Spec item | Test |
|---|---|
| T1 duplicated `STATUS:` renders from first match; legacy drops both sections | `TestDuplicatedHeaders::test_duplicated_status_renders_from_first_match` |
| T2 duplicated `BLOCKERS:`/`ADVISORY:` render on one line (BLOCKED and PASS) | `test_duplicated_blocker_count_renders_on_one_line`, `test_duplicated_advisory_count_renders_on_one_line` |
| T3 single-header artifacts: real == legacy (body + exit status), 8 cases incl. missing field, zero-reviewable-content, `ERROR`, no `STATUS:` line, no artifact | `TestSingleHeaderUnchanged::test_real_equals_legacy[*]` |
| T3 direct wording: BLOCKED review → `⛔ Blocked — 2 blocking finding(s).`; BLOCKED conformance → no `### Spec Conformance` | `TestSingleHeaderUnchanged::test_blocked_wording` |
| T4 CRLF: real == legacy, no render assertion | `TestSingleHeaderUnchanged::test_crlf_unchanged` |
| T5(a) `-m1` on exactly the 9 sites; `blast.md` / `conflict_resolution.md` reads have none | `TestConfinement::test_m1_on_exactly_the_nine_sites` |
| T5(b) only `report.bash` differs from the base, and only by `-m1`; same node list, same line count; at the raw-text level exactly 9 lines differ (so a comment-only edit elsewhere in the file is caught too) | `TestConfinement::test_only_report_bash_differs_from_merge_base` |
| T6 `check_workflow_dag.check()` returns `[]`; both DAG check scripts exit 0 | `test_dag_validators_pass` |

**T5(b) base ref — deliberate refinement of the spec's "load the workflow at
`origin/main`".** The test compares against `git merge-base HEAD origin/main` rather than
the `origin/main` tip, and skips when the base's `report` bash already contains
`grep -m1 `. Reasons: (1) if other tickets land workflow edits on `main` while this branch
is open, a tip comparison would fail on nodes this branch never touched — the merge-base
is exactly the commit this branch's diff is measured from; (2) after merge, `main` itself
carries `-m1`, so a literal tip comparison would fail forever on `main` and on every later
branch (the smoke gate runs `pytest tests/` on every dispatch). On this branch before
merge, merge-base and `origin/main` are the same commit (378b426) unless `main` moves; the
assertion is otherwise exactly the spec's. It still skips when `origin/main` is not
resolvable (CI's depth-1 checkout), as the spec says; Task 3 runs it un-skipped.

---

## Task 1: Add the executing test file (red)

**Files:**
- Create: `tests/test_report_node_header_reads_exec.py`

- [ ] **Step 1: Create the test file** with exactly this content:

```python
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
```

Notes baked into this file (do not "simplify" them away):
- `_PRELUDE` is prepended to the node's bash in the **same** `bash -c` string — a shell
  function passed via `env=` would not be seen (the `test_push_gate_dag_exec.py`
  precedent). Its `*)` branch fails loudly (exit 97 on the call) so an unexpected
  `python3` call cannot silently reach a real CLI or the network.
- The node's Archon placeholder `$parse-intent.output.intent` is replaced with `new`
  before running; there is no `set -u` (the node references unset `PREVIEW_*` /
  `PR_NUM`-style vars on several paths).
- Fixtures are written with `write_bytes` so the CRLF case keeps `\r\n` exactly.
- The conformance attestation renders as `### Spec Conformance\n\n\nATTESTATION-661`
  (three newlines): the node's `sed -n '/^---$/,$ p' | tail -n +2` keeps the blank line the
  writer puts after `---` (`commands/dark-factory-conformance.md:604`), and `$(...)` only
  strips *trailing* newlines.
- The fixture header shape matches the real writers: `emit_verdict`
  (`scripts/gate_lib.sh:55-60`: `STATUS`, `GATE_TYPE`, `FINDINGS_COUNT`, `SEVERITY`), then
  the writer-specific fields, a blank line, `---`, a blank line, then free text.
- `TestConfinement` and `test_dag_validators_pass` are pure Python and are deliberately
  **not** under `_posix_only` (spec: T5/T6 must not be skipped by the bash/jq guard).

- [ ] **Step 2: Run it against the unchanged workflow and confirm it fails for the right
  reason**

```bash
PYTHONPATH=scripts python -m pytest tests/test_report_node_header_reads_exec.py -v
```

Expected: `5 failed, 11 passed`. Exactly these five fail:
- `TestDuplicatedHeaders::test_duplicated_status_renders_from_first_match` (no
  `### Spec Conformance` in the body — the bug)
- `TestDuplicatedHeaders::test_duplicated_blocker_count_renders_on_one_line`
- `TestDuplicatedHeaders::test_duplicated_advisory_count_renders_on_one_line`
- `TestConfinement::test_m1_on_exactly_the_nine_sites` (no `grep -m1` lines yet)
- `TestConfinement::test_only_report_bash_differs_from_merge_base` (0 raw lines differ
  from the base, 9 expected; it reports `skipped` instead if `origin/main` is not fetched,
  giving `4 failed, 11 passed, 1 skipped`)

All `TestSingleHeaderUnchanged` cases pass trivially (real == legacy before the edit),
and `test_dag_validators_pass` passes. If any *other* test fails, stop and fix
the test file before touching the workflow. Do not commit yet.

## Task 2: Add `-m1` to the nine reads (green)

**Files:**
- Modify: `workflows/archon-dark-factory.yaml` (`report` node only; lines 1658, 1661-1665,
  1709-1711 at 378b426 — locate by content, not number)

- [ ] **Step 1: Apply the edit.** These are the only nine lines in the whole file that
  contain `$(grep '^` together with `"$ARTIFACTS_DIR/conformance.md" | cut` or
  `"$ARTIFACTS_DIR/review.md" | cut` (verified at 378b426), so this scripted edit is
  exact; it asserts the count:

```bash
python3 - <<'EOF'
from pathlib import Path
p = Path("workflows/archon-dark-factory.yaml")
lines = p.read_text(encoding="utf-8").split("\n")
n = 0
for i, line in enumerate(lines):
    if "$(grep '^" in line and ('"$ARTIFACTS_DIR/conformance.md" | cut' in line
                                 or '"$ARTIFACTS_DIR/review.md" | cut' in line):
        lines[i] = line.replace("$(grep '^", "$(grep -m1 '^", 1)
        n += 1
assert n == 9, n
p.write_text("\n".join(lines), encoding="utf-8")
print("edited", n)
EOF
```

Expected output: `edited 9`. (Equivalent by hand: in each of the nine lines below, insert
` -m1` after `grep`; change nothing else on the line.)

Resulting lines, in order (indentation unchanged from the file):

```
        CONFORMANCE_STATUS=$(grep -m1 '^STATUS:' "$ARTIFACTS_DIR/conformance.md" | cut -d' ' -f2)
            CONFORMANCE_VERDICT=$(grep -m1 '^VERDICT:' "$ARTIFACTS_DIR/conformance.md" | cut -d' ' -f2)
            CONFORMANCE_CYCLES=$(grep -m1 '^CYCLES:' "$ARTIFACTS_DIR/conformance.md" | cut -d' ' -f2)
            NO_SPEC=$(grep -m1 '^NO_SPEC:' "$ARTIFACTS_DIR/conformance.md" | cut -d' ' -f2)
            OOS_EXCISED=$(grep -m1 '^OOS_EXCISED:' "$ARTIFACTS_DIR/conformance.md" | cut -d' ' -f2)
            OOS_TICKETS=$(grep -m1 '^OOS_TICKETS:' "$ARTIFACTS_DIR/conformance.md" | cut -d' ' -f2-)
        CR_STATUS=$(grep -m1 '^STATUS:' "$ARTIFACTS_DIR/review.md" | cut -d' ' -f2)
        CR_BLOCKERS=$(grep -m1 '^BLOCKERS:' "$ARTIFACTS_DIR/review.md" | cut -d' ' -f2)
        CR_ADVISORY=$(grep -m1 '^ADVISORY:' "$ARTIFACTS_DIR/review.md" | cut -d' ' -f2)
```

The `BLAST_STATUS`/`BLAST_TRIGGER` reads (`blast.md`) and the four `conflict_resolution.md`
reads in the same node stay plain `grep`.

- [ ] **Step 2: Confirm the diff is exactly nine one-line hunks inside `report`**

```bash
git diff --stat -- workflows/archon-dark-factory.yaml
git diff -U0 -- workflows/archon-dark-factory.yaml | grep '^@@'
wc -l < workflows/archon-dark-factory.yaml
```

Expected:
```
 workflows/archon-dark-factory.yaml | 18 +++++++++---------
 1 file changed, 9 insertions(+), 9 deletions(-)
@@ -1658 +1658 @@ nodes:
@@ -1661,5 +1661,5 @@ nodes:
@@ -1709,3 +1709,3 @@ nodes:
1800
```

(Line numbers may shift if `main` moved; the shape — 9 changed lines in three hunks inside
the `report` node, 1800 lines total at 378b426 — must hold.)

- [ ] **Step 3: Run the new test file — all green**

```bash
PYTHONPATH=scripts python -m pytest tests/test_report_node_header_reads_exec.py -v
```

Expected: `16 passed` (with `origin/main` fetched; with it absent,
`15 passed, 1 skipped` — Task 3 Step 1 removes that skip).

- [ ] **Step 4: Commit**

```bash
git add tests/test_report_node_header_reads_exec.py workflows/archon-dark-factory.yaml
git commit -m "fix(report): read verdict header fields with grep -m1 (#661)

Matches the gate's first-match semantics (verdict_gate_check.sh:38): a
duplicated column-0 STATUS/BLOCKERS/ADVISORY/... line no longer drops a
section from, or splits a count in, the run comment. report node only;
single-header artifacts render byte-identically (pinned by
tests/test_report_node_header_reads_exec.py)."
```

## Task 3: Verify confinement un-skipped, DAG checks, full suite

**Files:** none modified (verification only).

- [ ] **Step 1: Run T5(b) un-skipped** (spec: the implement and conformance phases must
  run it un-skipped and record the result in the PR body)

```bash
git rev-parse --verify origin/main >/dev/null 2>&1 || git fetch origin main:refs/remotes/origin/main
git merge-base HEAD origin/main
PYTHONPATH=scripts python -m pytest \
  "tests/test_report_node_header_reads_exec.py::TestConfinement::test_only_report_bash_differs_from_merge_base" -v -rs
```

Expected: `1 passed` — **not** `skipped`. If it reports `skipped`, the reason is printed
by `-rs`: fix the fetch and re-run; do not proceed on a skip. (The factory clone is a full
`git clone`, `entrypoint.sh:1009`. In a shallow checkout the fetch above is not enough,
because `git merge-base` needs the branch's own history: run `git fetch --unshallow origin`
first.) Record the merge-base SHA and
the `PASSED` line in `$ARTIFACTS_DIR/implementation.md` (it feeds the PR body), e.g.
`T5(b) confinement vs merge-base <sha>: PASSED (49 nodes, only report.bash differs, by -m1 only; 1800 lines)`.

- [ ] **Step 2: Run both workflow DAG checks exactly as CI does**

```bash
python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml
python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml
```

Expected:
```
DAG trigger_rule check passed for 1 workflow file(s).
when: expression lint passed for 1 workflow file(s).
```
both with exit status 0.

- [ ] **Step 3: Run the full suite**

```bash
PYTHONPATH=scripts python -m pytest tests/ -q
```

Expected: all pass, 0 failed (4646 passed at 378b426 with this change applied, in about
4 minutes). In particular `tests/test_report_code_review_section.py`,
`tests/test_verdict_gate_dag.py`, `tests/test_workflow_or_join.py` and
`tests/test_workflow_when.py` are unaffected — none asserts on the exact text of the nine
lines.

- [ ] **Step 4: Confirm the PR touches only the two files**

```bash
git diff --name-only "$(git merge-base HEAD origin/main)" HEAD
```

Expected: `tests/test_report_node_header_reads_exec.py`,
`workflows/archon-dark-factory.yaml`, plus only the factory's own
`docs/superpowers/specs/…`/`docs/superpowers/plans/…`/`.archon/memory/…` artifacts for
#661. No `ci.yml`, no `scripts/`, no `dark-factory/scripts/`, no `commands/`. Nothing to
commit in this task.

## Self-review

- `**Issue:** #661` line present under the title.
- No placeholders: the full test file, the exact edit script, the nine resulting lines and
  every command's expected output are given. The test file and edit were executed against
  a scratch copy of this branch (378b426 + spec commits) while writing this plan:
  5 failed / 11 passed before the edit, 16 passed after, mutation checks (a stray
  `timeout:` change in `report`, a stray `-m1` on `BLAST_STATUS`, an edit inside another
  node's bash, a comment-only edit outside any node body, one of the nine sites left
  without `-m1`) each caught by T5, full
  suite 4646 passed, both DAG checks exit 0.
- Scope: exactly the two files in the File Structure table; gate scripts, `run_record.py`,
  `blast.md`/`conflict_resolution.md` reads and every other node untouched.
