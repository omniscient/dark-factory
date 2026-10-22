# Push Gate: Verify the Push Before Applying spec/plan-pending-review — Implementation Plan

**Issue:** #473
**Revised:** 2026-10-22 (operator amendment after plan gate — POSIX skip guard, push transcript on success, partial output kept on timeout)

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:executing-plans (or
> superpowers:subagent-driven-development) to work through this plan task by task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** `refine-push` and `plan-push-and-advance` apply `spec-pending-review`/`plan-pending-review`
only after `git push` exits 0 **and** `git ls-remote` shows that the remote branch sha equals
`git rev-parse HEAD`. A detached HEAD, a rejected or timed-out push, or a sha mismatch posts a
`<!-- df-push-failure -->` marker comment with the captured git error and writes
`push-result.json` (folded into `run_record["push_result"]`, rendered as one `**Push:** FAILED`
line in the cost report). The label is never applied in those cases.

**Architecture:** This is an inline bash change inside the two existing DAG nodes in
`workflows/archon-dark-factory.yaml`, in the `if [ -n "$SPEC_FILE" ]` / `if [ -n "$PLAN_FILE" ]`
branch only. The no-artifact `df-refine-failure` path and its `needs-discussion` silent-skip are
untouched. The nodes write `$ARTIFACTS_DIR/push-result.json` via `jq -n` before any label or
comment call. `scripts/factory_core/run_record.py::cmd_assemble` folds that file the same
optional, schema-free way it already folds `memory-trace.json`.
`scripts/factory_core/cost_report.py::render` gains one absent-tolerant line. Behavior is proven
by a new **executing** test that runs the real extracted node bash against a real local bare
origin. Only the tracker CLI is replaced, through the `python3` shell-function override.

**Tech Stack:** bash (coreutils `timeout`, `jq`, `awk`, git), Python 3, pytest, PyYAML.

**Spec:** `docs/superpowers/specs/2026-10-21-issue-473-push-gate-verify-before-label-design.md`
(operator-amended at a344da0; amendments F1–F8 are reflected in every task below).

**Pre-validated:** this plan was dry-run in a scratch copy at plan time. With the exact code
below applied: the new exec test gives 8 passed, `tests/test_push_gate_dag.py` gives 32 passed,
`tests/test_cost_report.py` + `tests/test_run_record.py` give 131 passed, and
`check_workflow_dag.py` / `check_workflow_when.py` both pass. The same tests against the
*unmodified* workflow fail exactly as the "verify fail" steps state. In those runs the old
YAML's failure cases log a `tracker label --add …-pending-review` call, which is the #473 bug
reproduced.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `tests/test_push_gate_dag_exec.py` | Create | Executing test: runs each node's real bash against a bare origin, in four cases × two nodes |
| `tests/test_push_gate_dag.py` | Modify | Three cheap static assertions: exact `df-push-failure` call site, `timeout`-bounded push → `ls-remote` → label order, `push-result.json` written first |
| `workflows/archon-dark-factory.yaml` | Modify | `refine-push` + `plan-push-and-advance`: verified push gates the label; `df-push-failure` marker; `push-result.json` |
| `tests/test_run_record.py` | Modify | `cmd_assemble` picks up / ignores-absent / tolerates-malformed `push-result.json` |
| `scripts/factory_core/run_record.py` | Modify | Fold `push-result.json` into `run_record["push_result"]` |
| `tests/test_cost_report.py` | Modify | `format_push_line` unit cases plus two `render` golden cases |
| `scripts/factory_core/cost_report.py` | Modify | `format_push_line` + one absent-tolerant `**Push:** FAILED — …` line in `render` |

Explicitly **not** touched:
- `.claude/skills/operator/scripts/run_summary.py` / `runs.sh`: `.claude/skills/` is in
  `hard_exclude_paths`, and the spec lists it as an operator follow-up.
- `scripts/post_marker_comment.sh`, `scripts/push_gate_check.sh`: used as-is.
- `.github/workflows/ci.yml`: new `.py` tests are collected by `python -m pytest tests/`.
- The node `timeout: 60000`, `depends_on`, `when`: unchanged, and
  `test_node_depends_on_and_when_unchanged` asserts this.

**Environment note:** if `python -m pytest` reports `No module named pytest` (or
`No module named 'aiohttp'`), first run `pip install pytest pyyaml aiohttp`, the same command CI
uses at `.github/workflows/ci.yml:12`. `aiohttp` is imported directly only by
`scripts/factory_core/model_proxy.py` (and `tests/test_factory_model_proxy.py`), but
`scripts/factory_core/run_record.py:22` does `from . import model_proxy`, so collecting
`tests/test_run_record.py` (Task 2) needs it too; `tests/test_cost_report.py` does not import
`run_record` and collects without it.

---

## Task 1: Verified push in both DAG nodes (executing test + static assertions + YAML)

**Files:**
- Create: `tests/test_push_gate_dag_exec.py`
- Modify: `tests/test_push_gate_dag.py` (insert three methods into `class TestPushGateNodes`)
- Modify: `workflows/archon-dark-factory.yaml` (`refine-push` ~line 465, `plan-push-and-advance` ~line 528)

- [ ] **Step 1: Write the failing executing test**

Create `tests/test_push_gate_dag_exec.py` with exactly this content:

```python
"""Executing tests for the refine-push/plan-push-and-advance DAG nodes (#473).

tests/test_push_gate_dag.py asserts string/ordering properties of the nodes' bash text —
exactly the style of check that missed #473 (the text already "contained" `git push`
before the label call; order alone never proved the exit status was checked). These
tests run the real extracted bash against a real (local) bare origin instead:
push_gate_check.sh, `git push` and `git ls-remote` all execute for real; only the
tracker CLI (providers/cli.py) is replaced, by the `python3` shell-function override
precedent from tests/test_scheduler_comment_verdict.py, extended to log every call.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _REPO_ROOT / "workflows" / "archon-dark-factory.yaml"
_ISSUE = "473"

pytestmark = pytest.mark.skipif(
    os.name != "posix"
    or any(shutil.which(tool) is None for tool in ("bash", "git", "jq", "timeout")),
    reason="needs a POSIX host with bash, git, jq and coreutils timeout (Linux/CI-only harness)",
)

# Prepended to the node's bash in ONE `bash -c` string: an exported shell function is
# inherited only by child shells of the shell that defined it (post_marker_comment.sh,
# push_gate_check.sh), never via subprocess env=. Every providers/cli.py subcommand the
# node can reach (tracker label / comment / get) is intercepted and logged; a
# --body-file's contents are copied into the log because post_marker_comment.sh deletes
# the temp file right after the call. No `set -u`: the node's bodies reference
# $FACTORY_REPO_SLUG (a dummy is exported anyway).
_PRELUDE = r'''
python3() {
  case "$*" in
    *providers/cli.py*)
      printf 'CALL %s\n' "$*" >> "$PCLI_LOG"
      local _prev="" _a
      for _a in "$@"; do
        if [ "$_prev" = "--body-file" ]; then cat "$_a" >> "$PCLI_LOG"; printf '\n' >> "$PCLI_LOG"; fi
        _prev="$_a"
      done
      case "$*" in *"tracker get"*) printf '{"labels":[]}\n' ;; esac
      return 0 ;;
    *) command python3 "$@" ;;
  esac
}
export -f python3
'''

_NODES = [
    ("refine-push", "docs/superpowers/specs/", "spec-pending-review"),
    ("plan-push-and-advance", "docs/superpowers/plans/", "plan-pending-review"),
]


def _node_bash(node_id):
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in data.get("nodes", []) if isinstance(n, dict) and "id" in n}
    return nodes[node_id]["bash"]


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def _write_hook(bare, name, body):
    hook = bare / "hooks" / name
    hook.write_text("#!/bin/sh\n" + body)
    hook.chmod(0o755)


@pytest.fixture()
def env(tmp_path):
    """Bare origin + working tree (tests/test_push_gate_check.py's fixture shape) on a
    refine branch, plus the CLONE_DIR/ARTIFACTS_DIR layout the node reads."""
    bare = tmp_path / "bare"
    work = tmp_path / "work"
    bare.mkdir()
    git("init", "--bare", str(bare), cwd=tmp_path)
    git("symbolic-ref", "HEAD", "refs/heads/main", cwd=bare)
    git("clone", str(bare), str(work), cwd=tmp_path)
    git("config", "user.email", "test@test.com", cwd=work)
    git("config", "user.name", "Test", cwd=work)
    (work / "README.md").write_text("root\n")
    git("add", "README.md", cwd=work)
    git("commit", "-m", "init", cwd=work)
    git("push", "origin", "HEAD:main", cwd=work)
    git("branch", "--set-upstream-to=origin/main", "main", cwd=work)
    git("checkout", "-b", f"refine/issue-{_ISSUE}-test", cwd=work)

    # The node resolves scripts as "${CLONE_DIR:-.}/dark-factory/scripts/..." — point
    # that at this checkout explicitly rather than relying on its basename.
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / "dark-factory").symlink_to(_REPO_ROOT, target_is_directory=True)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "issue.json").write_text(json.dumps({"resolved_number": int(_ISSUE)}))
    return {"tmp": tmp_path, "bare": bare, "work": work, "clone": clone,
            "artifacts": artifacts, "log": tmp_path / "pcli.log"}


def _commit_artifact(work, prefix):
    d = work / prefix
    d.mkdir(parents=True, exist_ok=True)
    (d / f"2026-10-21-issue-{_ISSUE}-test.md").write_text(f"# Doc\n\n**Issue:** #{_ISSUE}\n")
    git("add", prefix, cwd=work)
    git("commit", "-m", f"docs: artifact (#{_ISSUE})", cwd=work)


def _run_node(env, node_id):
    run_env = dict(os.environ)
    run_env.update({
        "CLONE_DIR": str(env["clone"]),
        "ARTIFACTS_DIR": str(env["artifacts"]),
        "PCLI_LOG": str(env["log"]),
        "FACTORY_REPO_SLUG": "test/repo",
    })
    result = subprocess.run(
        ["bash", "-c", _PRELUDE + _node_bash(node_id)],
        cwd=str(env["work"]), env=run_env, capture_output=True, text=True, timeout=120,
    )
    log = env["log"].read_text() if env["log"].exists() else ""
    pr_path = env["artifacts"] / "push-result.json"
    push_result = json.loads(pr_path.read_text()) if pr_path.exists() else None
    return result, log, push_result


def _label_calls(log):
    return [l for l in log.splitlines() if l.startswith("CALL ") and "tracker label" in l]


@pytest.mark.parametrize("node_id,prefix,label", _NODES)
class TestPushVerification:
    def test_push_succeeds_applies_label_once(self, env, node_id, prefix, label):
        _commit_artifact(env["work"], prefix)
        head = git("rev-parse", "HEAD", cwd=env["work"]).stdout.strip()

        result, log, push_result = _run_node(env, node_id)

        assert result.returncode == 0, result.stderr
        calls = _label_calls(log)
        assert len(calls) == 1 and f"--add {label}" in calls[0], log
        assert "<!-- df-push-failure -->" not in log
        assert push_result == {
            "node": node_id, "branch": f"refine/issue-{_ISSUE}-test", "pushed": True,
            "local_sha": head, "remote_sha": head, "error": "",
        }

    def test_push_rejected_posts_marker_and_skips_label(self, env, node_id, prefix, label):
        _commit_artifact(env["work"], prefix)
        _write_hook(env["bare"], "pre-receive", 'echo "rejected by test hook" >&2\nexit 1\n')

        result, log, push_result = _run_node(env, node_id)

        assert result.returncode == 0, result.stderr
        assert _label_calls(log) == [], log
        assert "--marker <!-- df-push-failure -->" in log
        assert "rejected by test hook" in log, "captured push stderr must reach the comment"
        assert push_result is not None and push_result["pushed"] is False
        assert "rejected by test hook" in push_result["error"]

    def test_detached_head_posts_marker_and_skips_label(self, env, node_id, prefix, label):
        _commit_artifact(env["work"], prefix)
        git("checkout", "--detach", cwd=env["work"])

        result, log, push_result = _run_node(env, node_id)

        assert result.returncode == 0, result.stderr
        assert _label_calls(log) == [], log
        assert "--marker <!-- df-push-failure -->" in log
        assert "detached HEAD" in log
        assert push_result is not None and push_result["pushed"] is False
        assert push_result["branch"] == ""
        assert "detached HEAD" in push_result["error"]
        # nothing reached the remote
        assert git("ls-remote", "--heads", str(env["bare"]), f"refine/issue-{_ISSUE}-test",
                   cwd=env["work"]).stdout.strip() == ""

    def test_remote_sha_mismatch_posts_marker_and_skips_label(self, env, node_id, prefix, label):
        _commit_artifact(env["work"], prefix)
        head = git("rev-parse", "HEAD", cwd=env["work"]).stdout.strip()
        main_sha = git("rev-parse", "origin/main", cwd=env["work"]).stdout.strip()
        # The push is accepted (exit 0), then the ref is moved to a different, existing
        # commit — so only the ls-remote sha comparison can catch it. An out-of-band
        # divergent commit would instead make the push itself fail (non-fast-forward).
        _write_hook(env["bare"], "post-update",
                    'for r in "$@"; do git update-ref "$r" refs/heads/main; done\n')

        result, log, push_result = _run_node(env, node_id)

        assert result.returncode == 0, result.stderr
        assert _label_calls(log) == [], log
        assert "--marker <!-- df-push-failure -->" in log
        assert head in log and main_sha in log, "both shas must be shown"
        assert push_result is not None and push_result["pushed"] is False
        assert push_result["local_sha"] == head
        assert push_result["remote_sha"] == main_sha
```

Harness notes (each one comes from spec "Test harness details"):
- Everything runs in **one** `bash -c` string (prelude + node bash), because an exported function
  is not inherited through `env=`.
- `CLONE_DIR` is a symlink `tmp/clone/dark-factory → repo root`. The test does not depend on
  the checkout's basename, so it works in worktrees.
- `issue.json` is written into `ARTIFACTS_DIR`. A module-level `skipif` skips on any
  non-POSIX host (`os.name != "posix"`, i.e. Windows) and on POSIX hosts without
  `bash`/`git`/`jq`/`timeout`. The tool check alone is not enough: a Windows host with Git
  for Windows and a winget `jq` resolves all four, and the fixture's `symlink_to` then errors
  with WinError 1314 instead of skipping (plan-gate review).
- No `set -u`. A dummy `FACTORY_REPO_SLUG` is exported anyway.
- The override intercepts every `providers/cli.py` subcommand (`tracker label`/`comment`/`get`).
  `factory_core/cli.py marker scheduler` (the footer, called from `post_marker_comment.sh`) runs
  for real. It is offline-safe and prints `*Posted by <product> Backlog Scheduler*`, where the
  product name comes from the environment. No test asserts the footer text.
- Push failure is forced through the remote (a `pre-receive` hook), never by shadowing `git`.
  The sha-mismatch case uses a `post-update` hook (spec F1).
- `_run_node` returns `push_result=None` when the file is absent. On the unfixed YAML the failure
  cases therefore fail on the meaningful "label was applied" assertion, not on a
  `FileNotFoundError`.

- [ ] **Step 2: Verify it fails against the current workflow**

Run: `python -m pytest tests/test_push_gate_dag_exec.py -q`

Expected: `8 failed`. The six push-failure/detached/mismatch cases fail on
`assert ['CALL …--add spec-pending-review'] == []` / `…plan-pending-review…`, because today's
node applies the label after a failed push (#473). The two success cases fail on
`assert None == {...}` (no `push-result.json` yet).

- [ ] **Step 3: Add the static assertions**

In `tests/test_push_gate_dag.py`, insert these three methods inside `class TestPushGateNodes`,
**immediately before** the existing `def test_node_depends_on_and_when_unchanged(self, node_id, prefix, label, noun):`
line (same 4-space method indentation):

```python
    def test_node_posts_exact_push_failure_marker(self, node_id, prefix, label, noun):
        bash = _workflow_nodes()[node_id]["bash"]
        # Exact call-site match (#473): the unrelated <!-- df-push-gate-failure --> marker
        # (scripts/verdict_gate_check.sh, push-and-pr verdict gate) must not satisfy this.
        marker_call = 'post_marker_comment.sh" "$ISSUE" "<!-- df-push-failure -->"'
        assert marker_call in bash, \
            f"'{node_id}' must post the <!-- df-push-failure --> marker when the push is unverified"
        comment_pos = bash.index(marker_call)
        assert "|| true" in bash[comment_pos:], \
            f"'{node_id}': the push-failure marker comment must be || true-guarded"

    def test_node_verifies_remote_sha_before_label(self, node_id, prefix, label, noun):
        bash = _workflow_nodes()[node_id]["bash"]
        push_pos = bash.find("timeout 30 git push -u origin")
        ls_remote_pos = bash.find("timeout 10 git ls-remote --exit-code --heads origin")
        label_pos = bash.find(f"--add {label}")
        assert push_pos != -1, f"'{node_id}': git push must be bounded by timeout 30"
        assert ls_remote_pos != -1, f"'{node_id}': git ls-remote must be bounded by timeout 10"
        assert push_pos < ls_remote_pos < label_pos, \
            f"'{node_id}': push, then ls-remote verification, then the gate label"
        assert "PUSH_RC=$?" in bash, f"'{node_id}': git push's exit status must be captured"
        assert 'if [ -z "$BRANCH" ]; then' in bash, \
            f"'{node_id}': an empty BRANCH (detached HEAD) must fail closed"

    def test_node_writes_push_result_before_label_and_marker(self, node_id, prefix, label, noun):
        bash = _workflow_nodes()[node_id]["bash"]
        result_pos = bash.find('> "$ARTIFACTS_DIR/push-result.json"')
        assert result_pos != -1, f"'{node_id}' must write $ARTIFACTS_DIR/push-result.json"
        assert result_pos < bash.find(f"--add {label}"), \
            f"'{node_id}': push-result.json must be written before the label call"
        assert result_pos < bash.find("<!-- df-push-failure -->"), \
            f"'{node_id}': push-result.json must be written before the push-failure marker"
```

- [ ] **Step 4: Verify the static assertions fail**

Run: `python -m pytest tests/test_push_gate_dag.py -q`

Expected: `6 failed, 26 passed`. The three new methods fail for both nodes; every pre-existing
assertion still passes.

- [ ] **Step 5: Replace the `refine-push` artifact-found branch**

In `workflows/archon-dark-factory.yaml`, node `- id: refine-push`, replace this exact block,
from the `if [ -n "$SPEC_FILE" ]; then` line through the `else` line that directly precedes
`HAS_NEEDS_DISCUSSION=`:

```yaml
      if [ -n "$SPEC_FILE" ]; then
        git push -u origin "$BRANCH"
        if python3 "$_PCLI" tracker label --id "$ISSUE" --add spec-pending-review; then
          echo "Pushed $BRANCH for issue #$ISSUE (spec-pending-review gate applied)"
        else
          echo "WARNING: spec-pending-review failed to apply for #$ISSUE — spec pushed to $BRANCH but gate label missing; check board state manually"
          bash "${CLONE_DIR:-.}/dark-factory/scripts/post_marker_comment.sh" "$ISSUE" "<!-- df-gate-label-failure -->" \
      "## Refinement Pipeline — Gate Label Missing

      The spec was pushed to \`$BRANCH\` but the \`spec-pending-review\` label failed to apply (likely a transient GitHub API/rate-limit failure). This issue will not auto-advance until the label is applied.

      **Remediation:** \`gh issue edit $ISSUE --add-label spec-pending-review --repo $FACTORY_REPO_SLUG\`" || true
        fi
      else
```

with this block (YAML indentation preserved: code at 6/8/10/12 spaces, comment-body lines at
6 spaces, which the literal block strips to column 0, exactly like the existing
`df-gate-label-failure` body):

```yaml
      if [ -n "$SPEC_FILE" ]; then
        # #473: the gate label follows a *verified* push, not just a committed artifact.
        # A detached HEAD, a failed/timed-out push, or a remote sha that differs from HEAD
        # posts df-push-failure instead and leaves spec-pending-review unapplied.
        LOCAL_SHA=$(git rev-parse HEAD)
        REMOTE_SHA=""
        if [ -z "$BRANCH" ]; then
          PUSH_OK=false
          PUSH_ERR="detached HEAD: no branch name to push (git branch --show-current returned empty)"
        else
          PUSH_ERR=$(timeout 30 git push -u origin "$BRANCH" 2>&1)
          PUSH_RC=$?
          if [ "$PUSH_RC" -eq 124 ]; then
            PUSH_OK=false
            PUSH_ERR=$(printf '%s\n%s' "push timed out after 30s" "$PUSH_ERR")
          elif [ "$PUSH_RC" -eq 0 ]; then
            printf '%s\n' "$PUSH_ERR"
            REMOTE_SHA=$(timeout 10 git ls-remote --exit-code --heads origin "refs/heads/$BRANCH" 2>/dev/null \
              | awk -v r="refs/heads/$BRANCH" '$2==r{print $1; exit}')
            if [ -n "$REMOTE_SHA" ] && [ "$REMOTE_SHA" = "$LOCAL_SHA" ]; then
              PUSH_OK=true
              PUSH_ERR=""
            else
              PUSH_OK=false
              PUSH_ERR="post-push verification failed: origin/$BRANCH is ${REMOTE_SHA:-<unreachable>}, expected $LOCAL_SHA"
            fi
          else
            PUSH_OK=false
          fi
        fi
        PUSH_ERR=$(printf '%s\n' "$PUSH_ERR" | tail -n 20 | tail -c 2000)
        # Persisted before any label/comment call so a comment failure or node timeout
        # still leaves the outcome for run_record.py's cmd_assemble (run_record["push_result"]).
        jq -n --arg node "refine-push" --arg branch "$BRANCH" --argjson pushed "$PUSH_OK" \
          --arg local_sha "$LOCAL_SHA" --arg remote_sha "$REMOTE_SHA" --arg error "$PUSH_ERR" \
          '{node: $node, branch: $branch, pushed: $pushed, local_sha: $local_sha, remote_sha: $remote_sha, error: $error}' \
          > "$ARTIFACTS_DIR/push-result.json" || true

        if [ "$PUSH_OK" = true ]; then
          if python3 "$_PCLI" tracker label --id "$ISSUE" --add spec-pending-review; then
            echo "Pushed $BRANCH for issue #$ISSUE (spec-pending-review gate applied)"
          else
            echo "WARNING: spec-pending-review failed to apply for #$ISSUE — spec pushed to $BRANCH but gate label missing; check board state manually"
            bash "${CLONE_DIR:-.}/dark-factory/scripts/post_marker_comment.sh" "$ISSUE" "<!-- df-gate-label-failure -->" \
      "## Refinement Pipeline — Gate Label Missing

      The spec was pushed to \`$BRANCH\` but the \`spec-pending-review\` label failed to apply (likely a transient GitHub API/rate-limit failure). This issue will not auto-advance until the label is applied.

      **Remediation:** \`gh issue edit $ISSUE --add-label spec-pending-review --repo $FACTORY_REPO_SLUG\`" || true
          fi
        else
          echo "refine-push: push verification failed for #$ISSUE — spec-pending-review NOT applied."
          printf '%s\n' "$PUSH_ERR"
          bash "${CLONE_DIR:-.}/dark-factory/scripts/post_marker_comment.sh" "$ISSUE" "<!-- df-push-failure -->" \
      "## Refinement Pipeline — Push Failed

      The spec was committed locally but could not be verified on the remote (\`${BRANCH:-<detached HEAD>}\`), so the \`spec-pending-review\` label was **not** applied. This item remains eligible for automatic retry.

      ~~~
      $PUSH_ERR
      ~~~

      **Remediation:** investigate the push failure above, then retry:
      \`\`\`bash
      docker compose --profile factory run --rm dark-factory \"Refine issue #${ISSUE}\"
      \`\`\`" || true
        fi
      else
```

Details that must survive verbatim. The tests pin them, and Assumptions/Requirements in the spec
explain why:
- `LOCAL_SHA` and `REMOTE_SHA=""` are set before the branch check, so `push-result.json` always
  has both keys.
- `timeout 30` on the push and `timeout 10` on `ls-remote` (spec F3). The run image's uutils
  `timeout` 0.8.0 honors exit 124 (operator note on the issue).
- The `ls-remote` call queries the exact `refs/heads/$BRANCH`, and awk matches `$2==r` exactly
  (spec F6).
- On rc 0 the captured transcript is echoed (`printf '%s\n' "$PUSH_ERR"`) before the
  `ls-remote` check, so the node log keeps the `* [new branch] …` / `set up to track` lines it
  shows today; `PUSH_ERR=""` is then reset on a verified push, because `git push` writes
  progress to stderr even on success.
- On rc 124 the `push timed out after 30s` line is prepended to whatever git printed before the
  kill rather than replacing it, so a transfer that stalled mid-way still shows how far it got
  (the `tail -n 20 | tail -c 2000` cap still applies). The join uses `printf '%s\n%s'` rather
  than bash ANSI-C `$'\n'` quoting so the line is shell-agnostic (the image's `/bin/sh` is
  dash, where `$'\n'` is a literal). No exec case asserts the timeout text
  (see "Known coverage gap"); any future one must assert the prefix (`startswith`), not the
  whole string.
- `PUSH_ERR` is capped with `tail -n 20 | tail -c 2000` before both the JSON write and the
  comment. In the comment it is fenced with `~~~`, not triple backticks.
- `push-result.json` is written **before** the `if [ "$PUSH_OK" = true ]` dispatch (Requirement 4).
- The push-failure marker posts unconditionally (no `needs-discussion` check) and is
  `|| true`-guarded, so the node exits 0 (Requirement 8).
- No `set -e`/`set -u` prologue is added (spec Assumptions).
- The first literal `git push` in the node's bash must stay after `push_gate_check.sh`
  (`test_node_gates_push_and_label_behind_artifact_check` uses `bash.find("git push")`). The
  comment lines in the new block deliberately never contain the substring `git push`.
- The comment lines also avoid a bare `else`/`fi` line. `test_node_guards_label_call_and_warns_on_failure`
  finds the first stripped `else`/`fi` lines after the guard, which are the label-failure
  `else` and its closing `fi`.

- [ ] **Step 6: Replace the `plan-push-and-advance` artifact-found branch**

In the same file, node `- id: plan-push-and-advance`, replace this exact block:

```yaml
      if [ -n "$PLAN_FILE" ]; then
        git push -u origin "$BRANCH"
        if python3 "$_PCLI" tracker label --id "$ISSUE" --add plan-pending-review; then
          echo "Pushed $BRANCH for issue #$ISSUE (plan-pending-review gate applied)"
        else
          echo "WARNING: plan-pending-review failed to apply for #$ISSUE — plan pushed to $BRANCH but gate label missing; check board state manually"
          bash "${CLONE_DIR:-.}/dark-factory/scripts/post_marker_comment.sh" "$ISSUE" "<!-- df-gate-label-failure -->" \
      "## Refinement Pipeline — Gate Label Missing

      The plan was pushed to \`$BRANCH\` but the \`plan-pending-review\` label failed to apply (likely a transient GitHub API/rate-limit failure). This issue will not auto-advance until the label is applied.

      **Remediation:** \`gh issue edit $ISSUE --add-label plan-pending-review --repo $FACTORY_REPO_SLUG\`" || true
        fi
      else
```

with:

```yaml
      if [ -n "$PLAN_FILE" ]; then
        # #473: the gate label follows a *verified* push, not just a committed artifact.
        # A detached HEAD, a failed/timed-out push, or a remote sha that differs from HEAD
        # posts df-push-failure instead and leaves plan-pending-review unapplied.
        LOCAL_SHA=$(git rev-parse HEAD)
        REMOTE_SHA=""
        if [ -z "$BRANCH" ]; then
          PUSH_OK=false
          PUSH_ERR="detached HEAD: no branch name to push (git branch --show-current returned empty)"
        else
          PUSH_ERR=$(timeout 30 git push -u origin "$BRANCH" 2>&1)
          PUSH_RC=$?
          if [ "$PUSH_RC" -eq 124 ]; then
            PUSH_OK=false
            PUSH_ERR=$(printf '%s\n%s' "push timed out after 30s" "$PUSH_ERR")
          elif [ "$PUSH_RC" -eq 0 ]; then
            printf '%s\n' "$PUSH_ERR"
            REMOTE_SHA=$(timeout 10 git ls-remote --exit-code --heads origin "refs/heads/$BRANCH" 2>/dev/null \
              | awk -v r="refs/heads/$BRANCH" '$2==r{print $1; exit}')
            if [ -n "$REMOTE_SHA" ] && [ "$REMOTE_SHA" = "$LOCAL_SHA" ]; then
              PUSH_OK=true
              PUSH_ERR=""
            else
              PUSH_OK=false
              PUSH_ERR="post-push verification failed: origin/$BRANCH is ${REMOTE_SHA:-<unreachable>}, expected $LOCAL_SHA"
            fi
          else
            PUSH_OK=false
          fi
        fi
        PUSH_ERR=$(printf '%s\n' "$PUSH_ERR" | tail -n 20 | tail -c 2000)
        # Persisted before any label/comment call so a comment failure or node timeout
        # still leaves the outcome for run_record.py's cmd_assemble (run_record["push_result"]).
        jq -n --arg node "plan-push-and-advance" --arg branch "$BRANCH" --argjson pushed "$PUSH_OK" \
          --arg local_sha "$LOCAL_SHA" --arg remote_sha "$REMOTE_SHA" --arg error "$PUSH_ERR" \
          '{node: $node, branch: $branch, pushed: $pushed, local_sha: $local_sha, remote_sha: $remote_sha, error: $error}' \
          > "$ARTIFACTS_DIR/push-result.json" || true

        if [ "$PUSH_OK" = true ]; then
          if python3 "$_PCLI" tracker label --id "$ISSUE" --add plan-pending-review; then
            echo "Pushed $BRANCH for issue #$ISSUE (plan-pending-review gate applied)"
          else
            echo "WARNING: plan-pending-review failed to apply for #$ISSUE — plan pushed to $BRANCH but gate label missing; check board state manually"
            bash "${CLONE_DIR:-.}/dark-factory/scripts/post_marker_comment.sh" "$ISSUE" "<!-- df-gate-label-failure -->" \
      "## Refinement Pipeline — Gate Label Missing

      The plan was pushed to \`$BRANCH\` but the \`plan-pending-review\` label failed to apply (likely a transient GitHub API/rate-limit failure). This issue will not auto-advance until the label is applied.

      **Remediation:** \`gh issue edit $ISSUE --add-label plan-pending-review --repo $FACTORY_REPO_SLUG\`" || true
          fi
        else
          echo "plan-push-and-advance: push verification failed for #$ISSUE — plan-pending-review NOT applied."
          printf '%s\n' "$PUSH_ERR"
          bash "${CLONE_DIR:-.}/dark-factory/scripts/post_marker_comment.sh" "$ISSUE" "<!-- df-push-failure -->" \
      "## Refinement Pipeline — Push Failed

      The plan was committed locally but could not be verified on the remote (\`${BRANCH:-<detached HEAD>}\`), so the \`plan-pending-review\` label was **not** applied. This item remains eligible for automatic retry.

      ~~~
      $PUSH_ERR
      ~~~

      **Remediation:** investigate the push failure above, then retry:
      \`\`\`bash
      docker compose --profile factory run --rm dark-factory \"Plan issue #${ISSUE}\"
      \`\`\`" || true
        fi
      else
```

(It is structurally identical to Step 5. The only differences are `PLAN_FILE`,
`plan-pending-review`, the `"plan-push-and-advance"` node name in the JSON and echo, "plan"
wording, and `"Plan issue #${ISSUE}"` in the remediation command.)

- [ ] **Step 7: Verify the executing and static tests pass, and the DAG validators are clean**

Run: `python -m pytest tests/test_push_gate_dag_exec.py tests/test_push_gate_dag.py -q`
Expected: `40 passed` (8 exec + 32 static).

Run: `python3 scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml && python3 scripts/check_workflow_when.py workflows/archon-dark-factory.yaml`
Expected:
```
DAG trigger_rule check passed for 1 workflow file(s).
when: expression lint passed for 1 workflow file(s).
```

Run: `python3 -c "import yaml; yaml.safe_load(open('workflows/archon-dark-factory.yaml'))" && echo YAML_OK`
Expected: `YAML_OK`

- [ ] **Step 8: Commit**

```bash
git add tests/test_push_gate_dag_exec.py tests/test_push_gate_dag.py workflows/archon-dark-factory.yaml
git commit -m "fix(workflow): verify push before applying spec/plan-pending-review (#473)

refine-push and plan-push-and-advance now apply the gate label only after
git push exits 0 and git ls-remote reports the remote branch sha == HEAD.
A detached HEAD, a rejected/timed-out push, or a sha mismatch posts a
<!-- df-push-failure --> marker with the captured git error instead, and
push-result.json is written before any label/comment call.

Adds tests/test_push_gate_dag_exec.py, which executes the real node bash
against a bare origin (push ok / rejected / detached / sha mismatch)."
```

---

## Task 2: Fold `push-result.json` into the run record

**Files:**
- Modify: `tests/test_run_record.py` (append at end of file)
- Modify: `scripts/factory_core/run_record.py` (`cmd_assemble`, directly after the `memory-trace.json` fold, ~line 686-691)

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_run_record.py`, separated from the current last line by two
blank lines. It reuses the module's existing
`_AssembleArgs` (~line 618), `rr` and `json` imports, mirroring
`test_assemble_picks_up_memory_trace` (~line 1089):

```python
# ── push_result: refine-push/plan-push-and-advance outcome (issue #473) ────

def test_assemble_picks_up_push_result(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

    push = {
        "node": "refine-push",
        "branch": "refine/issue-473-x",
        "pushed": False,
        "local_sha": "a" * 40,
        "remote_sha": "",
        "error": "remote: rejected",
    }
    (tmp_path / "push-result.json").write_text(json.dumps(push))

    out = tmp_path / "run-record.json"
    args = _AssembleArgs(tmp_path, out)
    rr.cmd_assemble(args)

    rec = json.loads(out.read_text())
    assert rec["push_result"] == push


def test_assemble_no_push_result_key_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

    out = tmp_path / "run-record.json"
    args = _AssembleArgs(tmp_path, out)
    rr.cmd_assemble(args)

    rec = json.loads(out.read_text())
    assert "push_result" not in rec


def test_assemble_tolerates_malformed_push_result(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "JSONL_PATH", tmp_path / "runs.jsonl")
    monkeypatch.setattr(rr, "_post_seq", lambda r: None)
    monkeypatch.setattr(rr, "LEDGER_PATH", tmp_path / "no-ledger.jsonl")

    (tmp_path / "push-result.json").write_text("not valid json {{{")

    out = tmp_path / "run-record.json"
    args = _AssembleArgs(tmp_path, out)
    rr.cmd_assemble(args)  # must not raise

    rec = json.loads(out.read_text())
    assert "push_result" not in rec
```

- [ ] **Step 2: Verify the pickup test fails**

Run: `python -m pytest tests/test_run_record.py -q -k push_result`
Expected: `1 failed, 2 passed`. `test_assemble_picks_up_push_result` fails with
`KeyError: 'push_result'`. The absent and malformed cases already pass, and they guard the
fold's tolerance once it exists.

- [ ] **Step 3: Implement the fold**

In `scripts/factory_core/run_record.py::cmd_assemble`, directly after this existing block:

```python
    trace_path = artifacts_dir / "memory-trace.json"
    if trace_path.exists():
        try:
            run_record["memory_trace"] = json.loads(trace_path.read_text(encoding="utf-8"))
        except Exception:
            pass
```

insert (one blank line before it, one blank line before the existing
`out_file.parent.mkdir(...)` line):

```python
    # Written by refine-push/plan-push-and-advance (#473): the verified push outcome
    # (pushed, branch, local/remote sha, captured error). Same optional, schema-free
    # fold as memory-trace.json — these are plain DAG bash steps, not verdict gates.
    push_path = artifacts_dir / "push-result.json"
    if push_path.exists():
        try:
            run_record["push_result"] = json.loads(push_path.read_text(encoding="utf-8"))
        except Exception:
            pass
```

Do **not** add `push-result` to `cmd_assemble`'s `artifact_names` list or to any verdict schema
(spec Alternative 1).

- [ ] **Step 4: Verify pass**

Run: `python -m pytest tests/test_run_record.py -q`
Expected: all pass, `0 failed`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_run_record.py scripts/factory_core/run_record.py
git commit -m "feat(run-record): fold push-result.json into run_record.push_result (#473)"
```

---

## Task 3: Render a failed push in the cost report

**Files:**
- Modify: `tests/test_cost_report.py` (append at end of file)
- Modify: `scripts/factory_core/cost_report.py` (new `format_push_line` after `format_economics_line`; two lines in `render`)

- [ ] **Step 1: Write the failing tests**

Append to the end of `tests/test_cost_report.py`, separated from the current last line by two
blank lines. The tests reuse that module's existing
`RUN_RECORD_A` / `GOLDEN_A_MULTI_NODE_FIRST_RUN` fixtures:

```python
# ---------------------------------------------------------------------------
# format_push_line / render() push outcome (#473, absent-tolerant)
# ---------------------------------------------------------------------------

def test_format_push_line_failed_renders_first_error_line():
    run_record = {"push_result": {
        "pushed": False,
        "error": "remote: rejected by test hook\nTo /tmp/bare\n ! [remote rejected] x -> x",
    }}
    assert cr.format_push_line(run_record) == "**Push:** FAILED — remote: rejected by test hook"


def test_format_push_line_absent_or_pushed_returns_empty_string():
    assert cr.format_push_line({}) == ""
    assert cr.format_push_line({"push_result": {"pushed": True, "error": ""}}) == ""
    assert cr.format_push_line({"push_result": {}}) == ""
    assert cr.format_push_line({"push_result": "not a dict"}) == ""


def test_format_push_line_empty_error_has_placeholder():
    assert cr.format_push_line({"push_result": {"pushed": False, "error": ""}}) == (
        "**Push:** FAILED — no error text captured"
    )


def test_format_push_line_caps_long_first_line():
    line = cr.format_push_line({"push_result": {"pushed": False, "error": "x" * 500}})
    assert line == "**Push:** FAILED — " + "x" * cr.PUSH_ERROR_MAX_CHARS + "…"


def test_render_push_failed_inserts_line_after_economics():
    record = dict(RUN_RECORD_A, push_result={"pushed": False, "error": "fatal: unable to access"})
    body = cr.render(record, prior_comment_body="", timestamp="2026-07-22 13:02 UTC",
                     intent="fix")
    assert body == GOLDEN_A_MULTI_NODE_FIRST_RUN.replace(
        "(score 1.0)\n\n---",
        "(score 1.0)\n\n**Push:** FAILED — fatal: unable to access\n\n---",
    )


def test_render_push_succeeded_matches_golden_unchanged():
    record = dict(RUN_RECORD_A, push_result={"pushed": True, "error": ""})
    body = cr.render(record, prior_comment_body="", timestamp="2026-07-22 13:02 UTC",
                     intent="fix")
    assert body == GOLDEN_A_MULTI_NODE_FIRST_RUN
```

- [ ] **Step 2: Verify fail**

Run: `python -m pytest tests/test_cost_report.py -q -k push`
Expected: `5 failed, 1 passed`. The four `format_push_line` tests fail with
`AttributeError: module 'factory_core.cost_report' has no attribute 'format_push_line'`
(`PUSH_ERROR_MAX_CHARS` likewise), and `test_render_push_failed_inserts_line_after_economics`
fails because there is no Push line. `test_render_push_succeeded_matches_golden_unchanged`
passes already and pins the unchanged golden.

- [ ] **Step 3: Add `format_push_line`**

In `scripts/factory_core/cost_report.py`, directly after `format_economics_line` (which ends
`return f"**Factory CPM:** {cpm_fmt} | **Outcome:** {state} (score {score})"`) and before
`def format_savings_block`, insert (two blank lines between top-level definitions, as in the
rest of the file):

```python
PUSH_ERROR_MAX_CHARS = 200


def format_push_line(run_record: dict) -> str:
    """One-line push outcome for the cost-report comment (#473).

    Absent-tolerant: older run-record.json files predate push_result, and a
    verified push prints nothing — only a failed/unverified push is surfaced.
    """
    push = run_record.get("push_result")
    if not isinstance(push, dict) or push.get("pushed") is not False:
        return ""
    error = str(push.get("error") or "").strip()
    first = error.splitlines()[0].strip() if error else "no error text captured"
    if len(first) > PUSH_ERROR_MAX_CHARS:
        first = first[:PUSH_ERROR_MAX_CHARS] + "…"
    return f"**Push:** FAILED — {first}"
```

- [ ] **Step 4: Wire it into `render`**

In `render`, directly after the existing two lines:

```python
    economics_line = format_economics_line(run_record)
    economics_segment = f"\n{economics_line}" if economics_line else ""
```

add:

```python
    push_line = format_push_line(run_record)
    push_segment = f"\n\n{push_line}" if push_line else ""
```

and in the `body = f"""..."""` template change the line

```
{economics_segment}
```

to

```
{economics_segment}{push_segment}
```

(`\n\n` rather than `\n`: the Push line gets its own markdown paragraph instead of merging into
the Factory CPM line. An empty segment leaves every existing golden byte-identical. The line sits
inside the `### Run:` block above `---`, so `parse_prior_cumulative` carries it into later runs'
history like the rest of the run block.)

- [ ] **Step 5: Verify pass, including all existing goldens**

Run: `python -m pytest tests/test_cost_report.py -q`
Expected: all pass, `0 failed` (goldens A–E unchanged).

- [ ] **Step 6: Commit**

```bash
git add tests/test_cost_report.py scripts/factory_core/cost_report.py
git commit -m "feat(cost-report): render a failed push outcome from run_record.push_result (#473)"
```

---

## Task 4: Full verification (CI parity)

**Files:** none (verification only; nothing to commit unless something fails)

- [ ] **Step 1: Full pytest suite, exactly as CI runs it**

Run: `python -m pytest tests/ -v`
Expected: `0 failed`. This includes the new `tests/test_push_gate_dag_exec.py` (8 passed) and
`tests/test_push_gate_dag.py::test_dag_validator_passes`.

- [ ] **Step 2: Smoke gate**

Run: `bash tests/test_smoke_gate.sh`
Expected: exits 0 (unchanged by this ticket, run for CI parity).

- [ ] **Step 3: CI shell tests that exercise cost-report / run-record code**

Run:
```bash
bash tests/test_cost_report_endpoint.sh && bash tests/test_cost_report_harness_economics.sh \
  && bash tests/test_run_record_hermetic.sh && bash tests/test_entrypoint_cost_report_regression.sh
```
Expected: each exits 0. Run them from the real checkout:
`test_entrypoint_cost_report_regression.sh` resolves paths through a directory named
`dark-factory`.

- [ ] **Step 4: Scope check**

Run: `git diff --name-only origin/main...HEAD`
Expected: exactly the spec, this plan, and the seven files in the File Structure table:
```
docs/superpowers/plans/2026-10-22-issue-473-push-gate-verify-before-label.md
docs/superpowers/specs/2026-10-21-issue-473-push-gate-verify-before-label-design.md
scripts/factory_core/cost_report.py
scripts/factory_core/run_record.py
tests/test_cost_report.py
tests/test_push_gate_dag.py
tests/test_push_gate_dag_exec.py
tests/test_run_record.py
workflows/archon-dark-factory.yaml
```
Nothing under `.claude/skills/`, `deploy/`, or `.github/workflows/`.

---

## Acceptance mapping

| Issue acceptance criterion / spec requirement | Covered by |
|---|---|
| A failed or skipped push never results in `spec-pending-review`/`plan-pending-review` (R1–R3) | Task 1: exec cases rejected / detached / mismatch assert zero `tracker label` calls; static `test_node_verifies_remote_sha_before_label` |
| Push error text reaches the issue (marker comment) and the run record (R2, R4) | Task 1: exec asserts the hook's stderr / detached text / both shas in the posted body and in `push-result.json`; Task 2 fold; Task 3 cost-report line |
| `push-result.json` written before the marker / label (R4) | Task 1: `test_node_writes_push_result_before_label_and_marker` |
| Existing success path unchanged (R5) | Task 1: exec success case asserts exactly one `--add <label>` call; pre-existing `df-gate-label-failure` static assertions still pass unchanged |
| Markers distinct; exact `<!-- df-push-failure -->`, not `df-push-gate-failure` (R6) | Task 1: `test_node_posts_exact_push_failure_marker` (exact call-site string) |
| Executing (not static) test (R7) | Task 1: `tests/test_push_gate_dag_exec.py` |
| Node exits 0 on push failure; timeout stays 60000 (R8) | Task 1: exec asserts `returncode == 0` in every case; `test_node_depends_on_and_when_unchanged` |

**Known coverage gap (record it in the PR body):** the `PUSH_RC -eq 124` push-timeout branch,
and an `ls-remote` that fails outright, have no dedicated exec case (a future timeout case must
assert `push_result["error"].startswith("push timed out after 30s")`, since the partial git
output follows that line). A slow-hook timeout case
would add 30 s or more to CI. The shared `${REMOTE_SHA:-<unreachable>}` failure wording and the
`PUSH_OK=false` → no-label dispatch are exercised by the mismatch and rejected cases.

## Self-review

- `**Issue:** #473` line present under the title.
- No placeholders: every code step gives the full code, and every command gives its expected
  output.
- Memory context checked: no `[AVOID]` entry applies to this change. The `#387`
  artifact-transfer `[PATTERN]` concerns `setup-branch`, which this plan does not touch.
