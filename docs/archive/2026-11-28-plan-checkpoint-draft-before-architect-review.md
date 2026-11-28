# Checkpoint the draft plan before architect review — Implementation Plan

**Issue:** #409
**Spec:** `docs/superpowers/specs/2026-11-28-plan-checkpoint-draft-before-architect-review-design.md` (operator-approved at `2f32222`)

---

**Goal:** A session-window pause that kills a plan run during architect review, plan-vs-spec
conformance or publish no longer throws away the plan. Phase 2 commits and pushes the plan as a
draft at a separate path, `docs/superpowers/plan-drafts/`. The next dispatch finds the draft and
resumes at Phase 3. Phase 4 moves the reviewed draft into `docs/superpowers/plans/` in the
publishing commit, before the OOS gate.

**Architecture:** Drafts and plans are told apart by directory, never by file content. Every
consumer of "a plan exists" selects by the prefix `docs/superpowers/plans/` (spec §1 table), so a
draft in the sibling `plan-drafts/` is invisible to all of them, with no code change.
`scripts/push_gate_check.sh` is reused unchanged as the draft lookup, with the drafts prefix.
Only `commands/dark-factory-plan.md` changes behavior. The rest of the change is tests and doc
citations. The `plan-push-and-advance` node, `push_gate_check.sh`, `scheduler.sh`,
`transfer_refine_artifacts.sh`, `run_record.py`, `CLAUDE.md`, `docs/triage-labels.md` and
`.claude/skills/**` are **not modified** (spec Requirement 3).

**Tech Stack:** Markdown phase command with fenced bash blocks; pytest tests that extract and run
those blocks against a local bare origin (the fixture shape of `tests/test_push_gate_dag_exec.py`).

**Verified before publishing this plan:** every command-file edit, test file and doc edit below
was applied to a scratch copy of the repo and run. Per-task red/green counts are given in each
task. `python scripts/docs_refs_check.py …` passed.

**Base:** the line numbers and doc citations in Tasks 6–7 are for `origin/main` at `2429106`
(after #406, which added one line to Phase 3.5 step 4a of `commands/dark-factory-plan.md` and
re-derived `docs/configuration.md`). The plan was first written against `9696eac`; an operator
review re-executed every task on an export of `2429106` and corrected Task 6 Step 5, Task 7 and
Task 8 Step 1 accordingly. The command-file old → new replacements in Tasks 3–6 apply unchanged
on both bases. Full suite on `2429106` with this plan applied: `4444 passed, with tests/test_provider_model_adapter.py excluded (that host's aiohttp is too old to import it; unrelated to this change)`.

### Lessons baked into the steps

- Memory (`architecture.md`, #409): never tell a draft from a final artifact by file content.
  Use the sibling directory and promote it with `git mv` in the publishing commit. No step below
  writes, reads or parses an in-file marker.
- Memory (`architecture.md`, #409): edit the repo-root `commands/`, `scripts/` and `workflows/`.
  The `.archon/commands/` and `dark-factory/scripts/` copies in a live clone are git-excluded
  fallbacks. The command blocks still *call* `${REPO_ROOT}/dark-factory/scripts/...` (that is the
  runtime path). Tests point it at the checkout with a `clone/dark-factory → repo root` symlink,
  as `tests/test_push_gate_dag_exec.py` does.
- Found while verifying this plan (not in the spec): `scripts/docs_refs_check.py` fails on a
  backticked `docs/superpowers/plan-drafts/` in the four checked reference docs. That directory
  never exists in a checkout, so the check reports "no such file or directory in the repo". In
  `docs/run-lifecycle.md`, `docs/features.md` and `docs/configuration.md`, write the short form
  `plan-drafts/`, which the checker does not treat as a repo path. `docs/domain.md` is not
  checked and keeps the full path. The checker itself is not modified.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `commands/dark-factory-plan.md` | modify | SCOPE BOUNDARY gains the drafts dir. New Phase 1 step 9 detects a draft. Phase 2 writes to `plan-drafts/`, handles resume, and runs a `### Draft checkpoint` block. Both `needs-discussion` exits re-run the checkpoint. Phase 4 gains a promote step (new step 4) before the OOS gate (now step 5). Steps are renumbered 5→6 and 6→7. |
| `tests/test_plan_command_checkpoint.py` | create | Extracts the three `<!-- plan-draft-{detect,checkpoint,promote}:begin/end -->` blocks and runs them for real (spec test table rows 1–11). Also asserts where they sit in the command text. |
| `tests/test_push_gate_dag_exec.py` | modify | `_run_node` takes an optional `prelude`. New `TestDraftOnlyBranch` pins that the unchanged node gives a draft-only branch no label, no push and no `push-result.json`. |
| `tests/test_no_plan_drafts_tracked.py` | create | CI guard: `git ls-files docs/superpowers/plan-drafts/` must be empty. |
| `docs/run-lifecycle.md` | modify | `dark-factory-plan` section (draft, checkpoint, resume, promote) plus every `dark-factory-plan.md` range. One sentence in C1. C3 limit rows. |
| `docs/configuration.md` | modify | Re-derived `commands/dark-factory-plan.md:N` citations. |
| `docs/features.md` | modify | Planning row. Re-derived `dark-factory-plan.md:N` citations (OOS, Context pack, Prompt rendering rows). |
| `docs/domain.md` | modify | Names `docs/superpowers/plan-drafts/`. |

Not modified (asserted in Task 8): `workflows/`, `scripts/push_gate_check.sh`,
`scripts/transfer_refine_artifacts.sh`, `scheduler.sh`, `CLAUDE.md`, `docs/triage-labels.md`,
`.github/workflows/ci.yml` (no new `tests/test_*.sh`).

Conventions for every task: run tests with `PYTHONPATH=scripts python -m pytest <file> -q`
from the repo root. Edits to `commands/dark-factory-plan.md` are exact old → new replacements.
Each old string occurs exactly once in the file at that point in the sequence, and the tasks
must be applied in order. Old/new blocks are shown in `~~~~` fences, so the inner
```` ``` ```` fences are literal file content.

---

## Task 1: Pin the unchanged `plan-push-and-advance` node on a draft-only branch

Characterization tests (spec "Added to `tests/test_push_gate_dag_exec.py`"). The node is not
modified, so these pass on the first run. They exist so that a future node change which starts
labelling or pushing a draft fails CI.

**Files:** modify `tests/test_push_gate_dag_exec.py`

- [ ] **Step 1: Let `_run_node` take a prelude.** Replace

~~~~python
def _run_node(env, node_id):
    run_env = dict(os.environ)
~~~~

with

~~~~python
def _run_node(env, node_id, prelude=_PRELUDE):
    run_env = dict(os.environ)
~~~~

and, in the same function, replace

~~~~python
        ["bash", "-c", _PRELUDE + _node_bash(node_id)],
~~~~

with

~~~~python
        ["bash", "-c", prelude + _node_bash(node_id)],
~~~~

- [ ] **Step 2: Append the draft-only tests** to the end of the file (two blank lines before):

~~~~python
# #409: the plan command checkpoints a draft under docs/superpowers/plan-drafts/ before
# architect review. The node is unchanged; these pin that a branch whose only plan-related
# commit is such a draft takes the node's existing "no artifact" path.
_DRAFT_PREFIX = "docs/superpowers/plan-drafts/"
_PRELUDE_NEEDS_DISCUSSION = _PRELUDE.replace(
    '{"labels":[]}', '{"labels":[{"name":"needs-discussion"}]}')


def _remote_branch(env):
    return git("ls-remote", "--heads", str(env["bare"]), f"refine/issue-{_ISSUE}-test",
               cwd=env["work"]).stdout.strip()


class TestDraftOnlyBranch:
    def test_draft_only_posts_failure_comment_and_no_label(self, env):
        _commit_artifact(env["work"], _DRAFT_PREFIX)

        result, log, push_result = _run_node(env, "plan-push-and-advance")

        assert result.returncode == 0, result.stderr
        assert _label_calls(log) == [], log
        assert push_result is None
        assert "--marker <!-- df-refine-failure -->" in log
        assert _remote_branch(env) == "", "the node must not push a draft-only branch"

    def test_draft_only_with_needs_discussion_is_silent(self, env):
        _commit_artifact(env["work"], _DRAFT_PREFIX)

        result, log, push_result = _run_node(env, "plan-push-and-advance",
                                             prelude=_PRELUDE_NEEDS_DISCUSSION)

        assert result.returncode == 0, result.stderr
        assert _label_calls(log) == [], log
        assert [l for l in log.splitlines() if "tracker comment" in l] == [], log
        assert push_result is None
        assert _remote_branch(env) == ""
~~~~

- [ ] **Step 3: Run.** `PYTHONPATH=scripts python -m pytest tests/test_push_gate_dag_exec.py -q`
  Expected: `12 passed` (10 existing + 2 new).
- [ ] **Step 4: Commit.**
  `git add tests/test_push_gate_dag_exec.py && git commit -m "test(#409): pin plan-push-and-advance on a draft-only branch"`

## Task 2: CI guard — no draft may be tracked

**Files:** create `tests/test_no_plan_drafts_tracked.py`

- [ ] **Step 1: Write the test.**

~~~~python
"""No draft plan may reach main (#409).

commands/dark-factory-plan.md checkpoints a draft plan under docs/superpowers/plan-drafts/
on its refine branch and moves it into docs/superpowers/plans/ when it publishes. Refine
branches are never merged by the factory, but an operator can open a PR from one; CI runs
this suite on every pull request to main, so a tracked draft fails the PR.
"""
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_no_file_is_tracked_under_plan_drafts():
    result = subprocess.run(
        ["git", "ls-files", "--", "docs/superpowers/plan-drafts/"],
        cwd=str(_REPO_ROOT), capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"not a git checkout: {result.stderr.strip()}")
    assert result.stdout.strip() == "", (
        "draft plans must not be committed outside a refine branch — move the draft into "
        f"docs/superpowers/plans/ or delete it:\n{result.stdout}"
    )
~~~~

- [ ] **Step 2: Verify it fails on a tracked draft.** This is one command, and it removes the
  probe from the index and the working tree whatever pytest returns, so a staged draft is never
  left behind:

~~~~bash
mkdir -p docs/superpowers/plan-drafts && echo x > docs/superpowers/plan-drafts/x.md \
  && git add docs/superpowers/plan-drafts/x.md; \
  PYTHONPATH=scripts python -m pytest tests/test_no_plan_drafts_tracked.py -q; \
  git rm -q --cached --ignore-unmatch docs/superpowers/plan-drafts/x.md; rm -rf docs/superpowers/plan-drafts
~~~~

  Expected: `1 failed`.
- [ ] **Step 3: Verify it passes.**
  `PYTHONPATH=scripts python -m pytest tests/test_no_plan_drafts_tracked.py -q`
  Expected: `1 passed`. `git status --short` shows only `?? tests/test_no_plan_drafts_tracked.py`.
- [ ] **Step 4: Commit.**
  `git add tests/test_no_plan_drafts_tracked.py && git commit -m "test(#409): fail any PR that tracks a draft plan"`

## Task 3: Phase 2 writes to `plan-drafts/` and checkpoints (commit + push, non-fatal)

Spec Requirements 1–3, §3. Test rows 1–2, plus the "checkpoint re-run is a no-op" property that
the two `needs-discussion` re-runs (Task 5) rely on.

**Files:** create `tests/test_plan_command_checkpoint.py`, modify `commands/dark-factory-plan.md`

- [ ] **Step 1: Write the test file** (helpers, checkpoint tests, Phase 2 text tests).
  Tasks 4–6 append to it.

~~~~python
"""Draft-plan checkpoint in commands/dark-factory-plan.md (#409).

The plan command marks three bash blocks with <!-- plan-draft-<name>:begin/end -->:
Phase 1's draft detection, Phase 2's checkpoint commit+push and Phase 4's promote. These
tests extract each block and run it for real against a bare origin + working tree
(tests/test_push_gate_dag_exec.py's fixture shape), with the real
scripts/push_gate_check.sh and scripts/oos_excise.sh. The text tests pin where the
blocks sit relative to Phase 3, the needs-discussion exits and the OOS gate.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CMD = _REPO_ROOT / "commands" / "dark-factory-plan.md"
_ISSUE = "409"
_BRANCH = f"refine/issue-{_ISSUE}-test"
_SPEC = "docs/superpowers/specs/2026-11-28-test-design.md"
_DRAFT = "docs/superpowers/plan-drafts/2026-11-28-test.md"
_FINAL = "docs/superpowers/plans/2026-11-28-test.md"
_OLD_PLAN = "docs/superpowers/plans/2026-11-01-old.md"
_PLAN_ALLOWLIST = "docs/superpowers/plans/ docs/superpowers/specs/ .archon/memory/"
_REFINE_ALLOWLIST = "docs/superpowers/specs/ .archon/memory/"

_posix = pytest.mark.skipif(
    os.name != "posix"
    or any(shutil.which(tool) is None for tool in ("bash", "git", "jq", "timeout")),
    reason="needs a POSIX host with bash, git, jq and coreutils timeout (Linux/CI-only harness)",
)


def _text():
    return _CMD.read_text(encoding="utf-8")


def _section(start, end=None):
    text = _text()
    i = text.index(start)
    return text[i:text.index(end, i)] if end else text[i:]


def _block(name):
    m = re.search(
        rf"<!-- plan-draft-{name}:begin -->\s*```bash\n(.*?)\n[ \t]*```\s*<!-- plan-draft-{name}:end -->",
        _text(), re.S,
    )
    assert m, f"no ```bash block between <!-- plan-draft-{name}:begin/end --> in {_CMD.name}"
    return m.group(1)


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def _write(work, rel, content):
    path = work / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _commit(work, rel, content, msg):
    _write(work, rel, content)
    git("add", "--", rel, cwd=work)
    assert git("commit", "-m", msg, cwd=work).returncode == 0


def _head(work):
    return git("rev-parse", "HEAD", cwd=work).stdout.strip()


def _tracked(work, prefix):
    return git("ls-files", "--", prefix, cwd=work).stdout.split()


def _plan(body="v1"):
    return f"# Plan\n\n**Issue:** #{_ISSUE}\n\n{body}\n"


@pytest.fixture()
def env(tmp_path):
    """Bare origin + working tree on a refine branch that already carries the spec, plus
    the REPO_ROOT/ARTIFACTS_DIR layout the blocks read."""
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
    git("checkout", "-b", _BRANCH, cwd=work)
    _commit(work, _SPEC, f"# Spec\n\n**Issue:** #{_ISSUE}\n", f"docs(#{_ISSUE}): add spec")

    # The blocks resolve scripts as "${REPO_ROOT}/dark-factory/scripts/..." — point that
    # at this checkout (the run container stages a copy there).
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / "dark-factory").symlink_to(_REPO_ROOT, target_is_directory=True)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "issue.json").write_text(json.dumps({"resolved_number": int(_ISSUE)}))
    return {"bare": bare, "work": work, "clone": clone, "artifacts": artifacts}


def _run(env, name, **preset):
    """Run block `name` in a fresh bash with the variables earlier steps bind pre-set;
    return the result and the block's exit status plus RESUMING_DRAFT/PLAN_FILE after it."""
    preset = {"REPO_ROOT": str(env["clone"]), "ISSUE_NUM": _ISSUE, **preset}
    prelude = "".join(f"{k}={shlex.quote(v)}\n" for k, v in preset.items())
    epilogue = ('\n__rc=$?\nprintf "\\n__rc=%s\\n__RESUMING_DRAFT=%s\\n__PLAN_FILE=%s\\n" '
                '"$__rc" "${RESUMING_DRAFT-<unset>}" "${PLAN_FILE-<unset>}"\n')
    result = subprocess.run(
        ["bash", "-c", prelude + _block(name) + epilogue],
        cwd=str(env["work"]), env=dict(os.environ, ARTIFACTS_DIR=str(env["artifacts"])),
        capture_output=True, text=True, timeout=120,
    )
    return result, dict(re.findall(r"^__(\w+)=(.*)$", result.stdout, re.M))


def _gate(env, prefix):
    result = subprocess.run(
        ["bash", str(_REPO_ROOT / "scripts" / "push_gate_check.sh"), prefix, _ISSUE],
        cwd=str(env["work"]), capture_output=True, text=True, timeout=60,
    )
    return result.stdout.strip()


def _oos(env, allowlist, noun):
    result = subprocess.run(
        ["bash", str(_REPO_ROOT / "scripts" / "oos_excise.sh"), allowlist, noun],
        cwd=str(env["work"]), capture_output=True, text=True, timeout=60,
        env=dict(os.environ, ARTIFACTS_DIR=str(env["artifacts"]), ISSUE_NUM=_ISSUE),
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


# ── Phase 2: checkpoint ──────────────────────────────────────────────────────


@_posix
def test_checkpoint_commits_and_pushes_only_the_draft(env):
    work = env["work"]
    base = _head(work)
    _write(work, _DRAFT, _plan())
    _write(work, "unrelated.txt", "must not be committed\n")

    result, out = _run(env, "checkpoint", PLAN_FILE=_DRAFT)

    assert out["rc"] == "0", result.stderr
    assert git("rev-list", "--count", f"{base}..HEAD", cwd=work).stdout.strip() == "1"
    assert git("show", "--name-only", "--format=", "HEAD", cwd=work).stdout.split() == [_DRAFT]
    remote = git("ls-remote", "--heads", str(env["bare"]), _BRANCH, cwd=work).stdout.split()
    assert remote and remote[0] == _head(work), "the checkpoint must reach origin"
    assert _gate(env, "docs/superpowers/plans/") == "", "a draft must never look like a plan"


@_posix
def test_checkpoint_push_failure_is_non_fatal(env):
    work = env["work"]
    hook = env["bare"] / "hooks" / "pre-receive"
    hook.write_text('#!/bin/sh\necho "rejected by test hook" >&2\nexit 1\n')
    hook.chmod(0o755)
    _write(work, _DRAFT, _plan())

    result, out = _run(env, "checkpoint", PLAN_FILE=_DRAFT)

    assert out["rc"] == "0", result.stderr
    assert "WARN: draft plan checkpoint push failed" in result.stdout
    assert git("log", "-1", "--format=%s", cwd=work).stdout.strip() == \
        f"docs(#{_ISSUE}): checkpoint draft plan"
    assert git("ls-remote", "--heads", str(env["bare"]), _BRANCH, cwd=work).stdout.strip() == ""


@_posix
def test_checkpoint_rerun_on_unchanged_draft_is_a_noop(env):
    work = env["work"]
    _write(work, _DRAFT, _plan())
    _run(env, "checkpoint", PLAN_FILE=_DRAFT)
    first = _head(work)

    result, out = _run(env, "checkpoint", PLAN_FILE=_DRAFT)

    assert out["rc"] == "0", result.stderr
    assert _head(work) == first


@_posix
def test_checkpoint_without_plan_file_stops(env):
    work = env["work"]
    base = _head(work)

    result, _ = _run(env, "checkpoint")

    assert result.returncode != 0
    assert "PLAN_FILE is unbound or missing" in result.stdout
    assert _head(work) == base
    assert git("ls-remote", "--heads", str(env["bare"]), _BRANCH, cwd=work).stdout.strip() == ""


# ── Command text ─────────────────────────────────────────────────────────────


def test_scope_boundary_lists_the_drafts_directory():
    scope = _section("## SCOPE BOUNDARY", "## Phase 1: LOAD")
    assert "`docs/superpowers/plans/`" in scope
    assert "`docs/superpowers/plan-drafts/`" in scope


def test_phase2_saves_to_the_drafts_directory():
    phase2 = _section("## Phase 2: PLAN WRITING", "## Phase 3: ARCHITECT REVIEW")
    assert "Save to `docs/superpowers/plan-drafts/" in phase2
    assert "Save to `docs/superpowers/plans/" not in phase2


def test_checkpoint_is_defined_once_before_phase3():
    text = _text()
    assert text.count("<!-- plan-draft-checkpoint:begin -->") == 1
    assert text.index("<!-- plan-draft-checkpoint:end -->") < text.index("## Phase 3: ARCHITECT REVIEW")
~~~~

- [ ] **Step 2: Verify it fails.**
  `PYTHONPATH=scripts python -m pytest tests/test_plan_command_checkpoint.py -q`
  Expected: `7 failed`. The block tests fail with `no ```bash block between <!-- plan-draft-checkpoint:begin/end -->`.
- [ ] **Step 3: SCOPE BOUNDARY.** In `commands/dark-factory-plan.md` replace

~~~~markdown
- Documents under `docs/superpowers/plans/` (the plan file)
~~~~

with

~~~~markdown
- Documents under `docs/superpowers/plans/` (the plan file)
- Documents under `docs/superpowers/plan-drafts/` (the draft plan: checkpointed before
  review, moved into `docs/superpowers/plans/` by Phase 4)
~~~~

- [ ] **Step 4: Save path.** Replace

~~~~markdown
- Save to `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`, and bind that path to `PLAN_FILE`
  — used by Phase 3's and Phase 3.5's `render-prompt` calls below
~~~~

with

~~~~markdown
- Save to `docs/superpowers/plan-drafts/YYYY-MM-DD-<feature>.md` (or, when Phase 1 step 9
  bound `PLAN_FILE` to a stale draft, that same path), and bind that path to `PLAN_FILE`
  — used by Phase 3's and Phase 3.5's `render-prompt` calls below
~~~~

  (Phase 1 step 9 is added in Task 4.)
- [ ] **Step 5: Draft stays in `plan-drafts/` until Phase 4.** Replace

~~~~markdown
  separate re-copy step — just re-run the same `render-prompt` invocation after saving the
  revised plan.
~~~~

with

~~~~markdown
  separate re-copy step — just re-run the same `render-prompt` invocation after saving the
  revised plan. The plan stays under `docs/superpowers/plan-drafts/` through Phases 3 and
  3.5; nothing under `docs/superpowers/plans/` is created or modified before Phase 4 moves
  the reviewed draft there.
~~~~

- [ ] **Step 6: Issue-number line note.** Replace

~~~~markdown
  associates it via commit subject instead
~~~~

with

~~~~markdown
  associates it via commit subject instead. It is also what lets Phase 1 step 9 find the
  draft on a resume
~~~~

- [ ] **Step 7: Draft checkpoint subsection.** Replace

~~~~markdown
- Self-review before publishing: confirm the issue-number line is present, alongside
  the existing no-placeholders check

## Phase 3: ARCHITECT REVIEW
~~~~

with

~~~~markdown
- Self-review before publishing: confirm the issue-number line is present, alongside
  the existing no-placeholders check

### Draft checkpoint

After self-review (or, on a resume, after applying feedback) and before the first architect
spawn, commit the draft and push this run's branch, so a kill during Phase 3, 3.5 or 4 (a
session-window pause) leaves a resumable draft instead of nothing. The commit touches
`$PLAN_FILE` only. A push failure is a warning, not an error: proceed into Phase 3 on the
locally committed draft, with no retry loop — the checkpoint must never become a new way to
lose a run. Bind `PLAN_FILE` in the same Bash call; the block stops if it is unbound or
missing, and falls back to `issue.json` for `ISSUE_NUM`:

<!-- plan-draft-checkpoint:begin -->
```bash
[ -n "${PLAN_FILE:-}" ] && [ -f "$PLAN_FILE" ] || { echo "checkpoint: PLAN_FILE is unbound or missing — bind it in this Bash call"; exit 1; }
ISSUE_NUM=${ISSUE_NUM:-$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")}
BRANCH=$(git branch --show-current)
git add -- "$PLAN_FILE"
git commit -m "docs(#${ISSUE_NUM}): checkpoint draft plan" -- "$PLAN_FILE" || true
if ! timeout 30 git push -u origin "$BRANCH"; then
  echo "WARN: draft plan checkpoint push failed for #${ISSUE_NUM} — proceeding on the locally committed draft; this run is not kill-durable until a later push succeeds."
fi
```
<!-- plan-draft-checkpoint:end -->

Re-run this block unchanged immediately before each `needs-discussion` exit (Phase 3 step 4
under "Issues Found", Phase 3.5 step 8b), so the revised draft — not the pre-review one — is
what the operator sees and what the next dispatch resumes. On an unchanged draft the commit is
a no-op. The block never applies a label: `plan-pending-review` is applied only by the
`plan-push-and-advance` node, and only to a file under `docs/superpowers/plans/`.

## Phase 3: ARCHITECT REVIEW
~~~~

- [ ] **Step 8: Verify it passes.**
  `PYTHONPATH=scripts python -m pytest tests/test_plan_command_checkpoint.py tests/test_plan_command_render_prompt.py tests/test_command_issue_number_mandate.py -q`
  Expected: all pass (`7 passed` for the new file).
- [ ] **Step 9: Commit.**
  `git add commands/dark-factory-plan.md tests/test_plan_command_checkpoint.py && git commit -m "feat(#409): plan Phase 2 checkpoints a draft under plan-drafts/ before review"`

## Task 4: Phase 1 detects a draft. Phase 2 resumes from it

Spec Requirements 4 and 6, §2. Test rows 3–6, the context-pack case, and "a finished plan is
not a resume signal" (Requirement 6).

**Files:** modify `tests/test_plan_command_checkpoint.py`, `commands/dark-factory-plan.md`

- [ ] **Step 1: Append the detect tests** to `tests/test_plan_command_checkpoint.py`:

~~~~python


# ── Phase 1: detect ──────────────────────────────────────────────────────────


@_posix
def test_detect_resumes_a_committed_draft(env):
    _commit(env["work"], _DRAFT, _plan(), f"docs(#{_ISSUE}): checkpoint draft plan")

    result, out = _run(env, "detect", SPEC_FILE=_SPEC)

    assert out["rc"] == "0", result.stderr
    assert (out["RESUMING_DRAFT"], out["PLAN_FILE"]) == ("true", _DRAFT)


@_posix
def test_detect_rewrites_in_place_when_spec_is_newer(env):
    work = env["work"]
    _commit(work, _DRAFT, _plan(), f"docs(#{_ISSUE}): checkpoint draft plan")
    _commit(work, _SPEC, f"# Spec v2\n\n**Issue:** #{_ISSUE}\n", f"docs(#{_ISSUE}): amend spec")

    _, out = _run(env, "detect", SPEC_FILE=_SPEC)

    assert (out["RESUMING_DRAFT"], out["PLAN_FILE"]) == ("false", _DRAFT)


@_posix
def test_detect_does_not_resume_an_empty_draft(env):
    # Found via push_gate_check.sh's commit-subject pass: the file itself names no issue.
    _commit(env["work"], _DRAFT, "", f"docs(#{_ISSUE}): checkpoint draft plan")

    _, out = _run(env, "detect", SPEC_FILE=_SPEC)

    assert (out["RESUMING_DRAFT"], out["PLAN_FILE"]) == ("false", _DRAFT)


@_posix
def test_detect_without_a_draft_leaves_plan_file_unset(env):
    _, out = _run(env, "detect", SPEC_FILE=_SPEC)

    assert (out["RESUMING_DRAFT"], out["PLAN_FILE"]) == ("false", "<unset>")


@_posix
def test_detect_without_spec_file_stops(env):
    _commit(env["work"], _DRAFT, _plan(), f"docs(#{_ISSUE}): checkpoint draft plan")

    result, out = _run(env, "detect")

    assert result.returncode != 0
    assert "bind SPEC_FILE" in result.stderr
    assert out == {}


@_posix
def test_detect_treats_a_context_pack_spec_as_not_newer(env):
    _commit(env["work"], _DRAFT, _plan(), f"docs(#{_ISSUE}): checkpoint draft plan")
    pack_spec = env["artifacts"] / "spec_content.md"
    pack_spec.write_text("# Spec from the context pack\n")

    _, out = _run(env, "detect", SPEC_FILE=str(pack_spec))

    assert out["RESUMING_DRAFT"] == "true"


@_posix
def test_detect_ignores_a_finished_plan(env):
    _commit(env["work"], _OLD_PLAN, _plan(), f"docs(#{_ISSUE}): add implementation plan")

    _, out = _run(env, "detect", SPEC_FILE=_SPEC)

    assert (out["RESUMING_DRAFT"], out["PLAN_FILE"]) == ("false", "<unset>")


def test_resume_still_runs_review_and_publish():
    text = " ".join(_section("## Phase 1: LOAD", "## Phase 3: ARCHITECT REVIEW").split())
    assert "Phase 3, Phase 3.5 and Phase 4 always run in full" in text
    assert "cycle counters starting at 0" in text


def test_detect_block_sits_in_phase1():
    text = _text()
    assert text.index("## Phase 1: LOAD") < text.index("<!-- plan-draft-detect:begin -->")
    assert text.index("<!-- plan-draft-detect:end -->") < text.index("## Phase 2: PLAN WRITING")
~~~~

- [ ] **Step 2: Verify they fail.**
  `PYTHONPATH=scripts python -m pytest tests/test_plan_command_checkpoint.py -q`
  Expected: `9 failed, 7 passed`.
- [ ] **Step 3: Add Phase 1 step 9 and the Phase 2 resume paragraph.** Replace

~~~~markdown
   about the `__init__.py` import requirement, the plan's "add model" task must explicitly
   include an `__init__.py` import step.

## Phase 2: PLAN WRITING

Write a full implementation plan following these conventions:
~~~~

with

~~~~markdown
   about the `__init__.py` import requirement, the plan's "add model" task must explicitly
   include an `__init__.py` import step.

9. Detect a draft plan checkpointed by an earlier run of this phase (Phase 2's draft
   checkpoint). Drafts live under `docs/superpowers/plan-drafts/`, never under
   `docs/superpowers/plans/`, so nothing that looks for a plan can find one.
   `push_gate_check.sh` is reused unchanged as the lookup, with the drafts prefix. Bind
   `SPEC_FILE` (step 6a) in the same Bash call; the block stops if it is unbound, and falls
   back to `git rev-parse` and `issue.json` for `REPO_ROOT` and `ISSUE_NUM`:

<!-- plan-draft-detect:begin -->
```bash
: "${SPEC_FILE:?bind SPEC_FILE (Phase 1 step 6a) in this Bash call}"
REPO_ROOT=${REPO_ROOT:-$(git rev-parse --show-toplevel)}
ISSUE_NUM=${ISSUE_NUM:-$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")}
RESUMING_DRAFT=false
DRAFT_FILE=$(bash "${REPO_ROOT}/dark-factory/scripts/push_gate_check.sh" "docs/superpowers/plan-drafts/" "$ISSUE_NUM")  # TARGET-PATH
if [ -n "$DRAFT_FILE" ]; then
  PLAN_FILE="$DRAFT_FILE"
  # Stale-draft guard: a draft written against an older spec is not resumed.
  DRAFT_SHA=$(git log -1 --format=%H -- "$DRAFT_FILE")
  SPEC_SHA=$(git log -1 --format=%H -- "$SPEC_FILE" 2>/dev/null || true)
  if [ -s "$DRAFT_FILE" ] && { [ -z "$SPEC_SHA" ] || git merge-base --is-ancestor "$SPEC_SHA" "$DRAFT_SHA"; }; then
    RESUMING_DRAFT=true
  fi
fi
echo "RESUMING_DRAFT=$RESUMING_DRAFT PLAN_FILE=${PLAN_FILE:-<unset>}"
```
<!-- plan-draft-detect:end -->

   - `RESUMING_DRAFT=true`: resume. Phase 2 does not write a new plan (see its first
     paragraph) and `PLAN_FILE` stays bound to the draft. Only plan writing is skipped:
     Phase 3, Phase 3.5 and Phase 4 always run in full, with their cycle counters starting
     at 0. No verdict or cycle count from the earlier run is carried over.
   - Draft found, `RESUMING_DRAFT=false` (the draft is empty, or the spec has a commit the
     draft does not descend from — it was re-refined or amended after the checkpoint): run
     Phase 2 in full and write the new draft to the same path (`$PLAN_FILE`), so the branch
     never carries two drafts. `SPEC_SHA` is empty when the spec came from the context pack;
     that counts as "not newer".
   - No draft (`PLAN_FILE` unset): run Phase 2 in full.

   A finished plan already under `docs/superpowers/plans/` (for example a rejected plan
   awaiting revision) is not a resume signal: it keeps today's behavior, a rewrite from
   Phase 2.

## Phase 2: PLAN WRITING

If Phase 1 set `RESUMING_DRAFT=true`, do not write a new plan. Read `$PLAN_FILE` (the draft),
then read the comments in `$ARTIFACTS_DIR/issue.json` and apply to the draft (edit
`$PLAN_FILE` in place) any human feedback, and any architect or conformance feedback an
earlier run posted when it parked on `needs-discussion`. A fresh rewrite picks that feedback
up implicitly; without this step a parked draft would be re-reviewed unchanged. Then run the
draft checkpoint at the end of this phase and continue to Phase 3.

Otherwise, write a full implementation plan following these conventions:
~~~~

- [ ] **Step 4: Verify it passes.**
  `PYTHONPATH=scripts python -m pytest tests/test_plan_command_checkpoint.py tests/test_plan_command_render_prompt.py tests/test_plan_command_context_pack.py -q`
  Expected: all pass (`16 passed` for the new file).
- [ ] **Step 5: Commit.**
  `git add commands/dark-factory-plan.md tests/test_plan_command_checkpoint.py && git commit -m "feat(#409): plan Phase 1 resumes from a checkpointed draft"`

## Task 5: Re-run the checkpoint before both `needs-discussion` exits

Spec §3, last paragraphs, and §6. A plan parked by the architect cap or the conformance cap
leaves its *revised* draft on the branch, and no file under `docs/superpowers/plans/`.

**Files:** modify `tests/test_plan_command_checkpoint.py`, `commands/dark-factory-plan.md`

- [ ] **Step 1: Append the test:**

~~~~python


def test_checkpoint_reruns_before_both_needs_discussion_exits():
    architect_exit = _section("4. If still not approved after 3 cycles:",
                              '### If architect returns "Approved":')
    conformance_exit = _section("b. If `CONFORMANCE_CYCLE > MAX_CYCLES`:",
                                "c. Read the MATERIAL deviation descriptions")
    for exit_text in (architect_exit, conformance_exit):
        rerun = exit_text.find("Re-run the draft checkpoint block")
        assert rerun != -1, exit_text
        assert rerun < exit_text.index("--add needs-discussion"), exit_text
~~~~

- [ ] **Step 2: Verify it fails.**
  `PYTHONPATH=scripts python -m pytest tests/test_plan_command_checkpoint.py -q`
  Expected: `1 failed, 16 passed`.
- [ ] **Step 3: Architect-cap exit (Phase 3).** Replace

~~~~markdown
4. If still not approved after 3 cycles:
   - Post the plan + architect feedback as an issue comment
~~~~

with

~~~~markdown
4. If still not approved after 3 cycles:
   - Re-run the draft checkpoint block (end of Phase 2) unchanged, so the revised draft is
     on the branch
   - Post the plan + architect feedback as an issue comment, naming the draft path
     (`$PLAN_FILE`, under `docs/superpowers/plan-drafts/`)
~~~~

- [ ] **Step 4: Phase 3.5 step 4 wording** (`PLAN_FILE` may now come from Phase 1). Replace

~~~~markdown
4. The artifact under review is the plan document on disk at `$PLAN_FILE` (bound in Phase 2).
~~~~

with

~~~~markdown
4. The artifact under review is the plan document on disk at `$PLAN_FILE` (bound in Phase 1
   step 9 or Phase 2 — the draft under `docs/superpowers/plan-drafts/`).
~~~~

- [ ] **Step 5: Conformance-cap exit (Phase 3.5 step 8b).** Replace

~~~~markdown
   b. If `CONFORMANCE_CYCLE > MAX_CYCLES`:
      - Post the conformance dialogue as an issue comment (fetch the footer first via
~~~~

with

~~~~markdown
   b. If `CONFORMANCE_CYCLE > MAX_CYCLES`:
      - Re-run the draft checkpoint block (end of Phase 2) unchanged, so the revised draft
        is on the branch, and name its path (`$PLAN_FILE`, under
        `docs/superpowers/plan-drafts/`) in the comment below
      - Post the conformance dialogue as an issue comment (fetch the footer first via
~~~~

- [ ] **Step 6: Verify it passes.**
  `PYTHONPATH=scripts python -m pytest tests/test_plan_command_checkpoint.py tests/test_plan_command_shadow_conformance.py tests/test_plan_command_conformance_rubric_fallback.py tests/test_comment_digest.py -q`
  Expected: all pass (`17 passed` for the new file).
- [ ] **Step 7: Commit.**
  `git add commands/dark-factory-plan.md tests/test_plan_command_checkpoint.py && git commit -m "feat(#409): re-checkpoint the revised draft before needs-discussion exits"`

## Task 6: Phase 4 promotes the draft into `plans/`, before the OOS gate

Spec §4. Test rows 7–11, plus "the promote block precedes the OOS gate" and "the OOS
allowlist is unchanged". Two guard tests come from architect review: an unbound `PLAN_FILE`, or a
failed `git mv`, must leave the draft tracked and add no commit. Without the guards,
`git rm -r` would delete an unedited reviewed draft and commit the deletion. Three of the appended tests are characterization tests of unchanged
scripts (`test_gate_lookups_tell_a_draft_from_an_earlier_plan`,
`test_refine_oos_gate_excises_a_draft`, `test_oos_allowlist_is_not_widened`). They pass before
the edit.

**Files:** modify `tests/test_plan_command_checkpoint.py`, `commands/dark-factory-plan.md`

- [ ] **Step 1: Append the promote tests:**

~~~~python


# ── Phase 4: promote ─────────────────────────────────────────────────────────


def _assert_promoted(env, base, final, content):
    work = env["work"]
    assert git("rev-list", "--count", f"{base}..HEAD", cwd=work).stdout.strip() == "1"
    assert _tracked(work, "docs/superpowers/plans/") == [final]
    assert (work / final).read_text() == content
    assert _tracked(work, "docs/superpowers/plan-drafts/") == []
    changed = git("diff", "--name-only", "origin/main...HEAD", cwd=work).stdout.split()
    assert changed and all(
        p.startswith(("docs/superpowers/specs/", "docs/superpowers/plans/")) for p in changed
    ), changed
    assert _oos(env, _PLAN_ALLOWLIST, "plan") == "", "the plan OOS gate must excise nothing"


@_posix
def test_promote_moves_the_edited_draft_into_plans(env):
    work = env["work"]
    _commit(work, _DRAFT, _plan(), f"docs(#{_ISSUE}): checkpoint draft plan")
    base = _head(work)
    _write(work, _DRAFT, _plan("v2 after review"))  # uncommitted reviewer fixes

    result, out = _run(env, "promote", PLAN_FILE=_DRAFT)

    assert out["rc"] == "0", result.stderr
    assert out["PLAN_FILE"] == _FINAL
    _assert_promoted(env, base, _FINAL, _plan("v2 after review"))


@_posix
def test_promote_works_when_the_checkpoint_commit_never_happened(env):
    work = env["work"]
    base = _head(work)
    _write(work, _DRAFT, _plan("never committed"))

    result, out = _run(env, "promote", PLAN_FILE=_DRAFT)

    assert out["rc"] == "0", result.stderr
    _assert_promoted(env, base, _FINAL, _plan("never committed"))


@_posix
def test_promote_reuses_an_earlier_plan_path(env):
    work = env["work"]
    _commit(work, _OLD_PLAN, _plan("rejected"), f"docs(#{_ISSUE}): add implementation plan")
    _commit(work, _DRAFT, _plan("revised"), f"docs(#{_ISSUE}): checkpoint draft plan")
    base = _head(work)

    result, out = _run(env, "promote", PLAN_FILE=_DRAFT)

    assert out["rc"] == "0", result.stderr
    assert out["PLAN_FILE"] == _OLD_PLAN
    _assert_promoted(env, base, _OLD_PLAN, _plan("revised"))


@_posix
def test_promote_without_plan_file_deletes_nothing(env):
    # Unedited draft: the case where an unguarded `git rm -r` would delete it (#409 review).
    work = env["work"]
    _commit(work, _DRAFT, _plan(), f"docs(#{_ISSUE}): checkpoint draft plan")
    base = _head(work)

    result, _ = _run(env, "promote")

    assert result.returncode != 0
    assert "PLAN_FILE is unbound or missing" in result.stdout
    assert _head(work) == base
    assert _tracked(work, "docs/superpowers/plan-drafts/") == [_DRAFT]
    assert _tracked(work, "docs/superpowers/plans/") == []


@_posix
def test_promote_stops_before_git_rm_when_the_move_fails(env):
    work = env["work"]
    _commit(work, _DRAFT, _plan(), f"docs(#{_ISSUE}): checkpoint draft plan")
    base = _head(work)
    # A file where the plans directory should be makes `git mv` fail.
    (work / "docs" / "superpowers" / "plans").write_text("not a directory\n")

    result, _ = _run(env, "promote", PLAN_FILE=_DRAFT)

    assert result.returncode != 0
    assert _head(work) == base
    assert _tracked(work, "docs/superpowers/plan-drafts/") == [_DRAFT]


@_posix
def test_gate_lookups_tell_a_draft_from_an_earlier_plan(env):
    work = env["work"]
    _commit(work, _OLD_PLAN, _plan("rejected"), f"docs(#{_ISSUE}): add implementation plan")
    _commit(work, _DRAFT, _plan("revised"), f"docs(#{_ISSUE}): checkpoint draft plan")

    assert _gate(env, "docs/superpowers/plan-drafts/") == _DRAFT
    assert _gate(env, "docs/superpowers/plans/") == _OLD_PLAN


@_posix
def test_refine_oos_gate_excises_a_draft(env):
    work = env["work"]
    _commit(work, _DRAFT, _plan(), f"docs(#{_ISSUE}): checkpoint draft plan")

    assert _oos(env, _REFINE_ALLOWLIST, "refine") == _DRAFT
    assert _tracked(work, "docs/superpowers/plan-drafts/") == []


def test_promote_precedes_the_oos_gate():
    phase4 = _section("## Phase 4: PUBLISH")
    oos_line = next(l for l in phase4.splitlines() if "oos_excise.sh" in l and "# TARGET-PATH" in l)
    assert phase4.index("<!-- plan-draft-promote:end -->") < phase4.index(oos_line)


def test_oos_allowlist_is_not_widened():
    oos_line = next(l for l in _text().splitlines() if "oos_excise.sh" in l and "# TARGET-PATH" in l)
    match = re.search(r'oos_excise\.sh"\s+"([^"]*)"', oos_line)
    assert match and set(match.group(1).split()) == set(_PLAN_ALLOWLIST.split())

~~~~

- [ ] **Step 2: Verify they fail.**
  `PYTHONPATH=scripts python -m pytest tests/test_plan_command_checkpoint.py -q`
  Expected: `6 failed, 20 passed`.
- [ ] **Step 3: Plan link note (Phase 4 step 2).** Replace

~~~~markdown
   - Plan link: `https://github.com/${FACTORY_REPO_SLUG}/blob/$BRANCH/<plan-file-path>`
~~~~

with

~~~~markdown
   - Plan link: `https://github.com/${FACTORY_REPO_SLUG}/blob/$BRANCH/<plan-file-path>`
     (`<plan-file-path>` is the final path under `docs/superpowers/plans/` bound by step 4)
~~~~

- [ ] **Step 4: Promote step, renumbered OOS/commit/comment steps.** Replace

~~~~markdown
4. Run the OOS gate — detect and revert any files committed outside the plan
   allowlist. `docs/superpowers/specs/` and `.archon/memory/` are included because
   the refine phase legitimately commits to those prefixes earlier on this same
   branch (#293); they remain outside this command's own `SCOPE BOUNDARY`, which is
   still only `docs/superpowers/plans/`:
   ```bash
   OOS_FILES=$(bash "${REPO_ROOT}/dark-factory/scripts/oos_excise.sh" "docs/superpowers/plans/ docs/superpowers/specs/ .archon/memory/" plan)  # TARGET-PATH
   ```
5. Commit the plan
6. Post a summary comment on the issue:
~~~~

with

~~~~markdown
4. Promote the draft into `docs/superpowers/plans/` — **before** the OOS gate (step 5). The
   plan path first exists in the commit that carries the reviewed content, and the draft
   path ceases to exist in that same commit. `FINAL_FILE` reuses the path of an earlier
   finished plan for this issue when one is on the branch (a rejected plan being revised),
   so the branch ends with one plan file; `git rm -r` removes any other draft left on the
   branch. Order matters: `oos_excise.sh` judges the committed diff `origin/main...HEAD`, so
   run before this step it would excise the draft and report it as spillover; run after
   it, the draft path nets out of the diff. The OOS allowlist is not widened. Bind
   `PLAN_FILE` to the draft in the same Bash call. The block stops before touching anything
   if `PLAN_FILE` is unbound or missing, and stops before `git rm -r` if the move fails, so a
   mistake here can never delete the reviewed draft:
   <!-- plan-draft-promote:begin -->
   ```bash
   [ -n "${PLAN_FILE:-}" ] && [ -f "$PLAN_FILE" ] || { echo "promote: PLAN_FILE is unbound or missing — bind it to the draft in this Bash call"; exit 1; }
   REPO_ROOT=${REPO_ROOT:-$(git rev-parse --show-toplevel)}
   ISSUE_NUM=${ISSUE_NUM:-$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")}
   FINAL_FILE=$(bash "${REPO_ROOT}/dark-factory/scripts/push_gate_check.sh" "docs/superpowers/plans/" "$ISSUE_NUM")  # TARGET-PATH
   [ -n "$FINAL_FILE" ] || FINAL_FILE="docs/superpowers/plans/$(basename -- "$PLAN_FILE")"
   mkdir -p docs/superpowers/plans
   git add -- "$PLAN_FILE"
   git mv -f -- "$PLAN_FILE" "$FINAL_FILE" || exit 1
   git rm -r -q --ignore-unmatch -- docs/superpowers/plan-drafts/
   git commit -m "docs(#${ISSUE_NUM}): add implementation plan"
   PLAN_FILE="$FINAL_FILE"
   echo "PLAN_FILE=$PLAN_FILE"
   ```
   <!-- plan-draft-promote:end -->
   From here on `PLAN_FILE` is the final path; the summary comment (step 7) and
   `refinement-status.md` (step 8) use it.
5. Run the OOS gate — detect and revert any files committed outside the plan
   allowlist. `docs/superpowers/specs/` and `.archon/memory/` are included because
   the refine phase legitimately commits to those prefixes earlier on this same
   branch (#293); they remain outside this command's own `SCOPE BOUNDARY`
   (`docs/superpowers/plans/`, plus `docs/superpowers/plan-drafts/`, which step 4 has
   already emptied):
   ```bash
   OOS_FILES=$(bash "${REPO_ROOT}/dark-factory/scripts/oos_excise.sh" "docs/superpowers/plans/ docs/superpowers/specs/ .archon/memory/" plan)  # TARGET-PATH
   ```
6. Commit any remaining change to `$PLAN_FILE` (normally none — step 4 committed the
   reviewed plan)
7. Post a summary comment on the issue:
~~~~

  The existing step `8. Write status to …refinement-status.md` keeps its number. The old
  list skipped 7, and the summary comment now takes it.
- [ ] **Step 5: Verify it passes.**
  `PYTHONPATH=scripts python -m pytest tests/test_plan_command_checkpoint.py tests/test_command_issue_context_contract.py -q`
  Expected: all pass (`26 passed` for the new file).
  `wc -l commands/dark-factory-plan.md` → `502 commands/dark-factory-plan.md`.
- [ ] **Step 6: Commit.**
  `git add commands/dark-factory-plan.md tests/test_plan_command_checkpoint.py && git commit -m "feat(#409): plan Phase 4 promotes the draft into plans/ before the OOS gate"`

## Task 7: Re-derive doc citations and describe the drafts directory

Spec §7. `scripts/docs_refs_check.py` only range-checks citations, so each new number is
computed from the edited command file, not guessed.

**Files:** modify `docs/run-lifecycle.md`, `docs/configuration.md`, `docs/features.md`,
`docs/domain.md`

- [ ] **Step 1: Compute the new citation targets.**

~~~~bash
python3 - <<'EOF'
from pathlib import Path
L = Path("commands/dark-factory-plan.md").read_text(encoding="utf-8").splitlines()
def at(s, start=0):
    return next(i + 1 for i, l in enumerate(L) if i >= start and s in l)
def upto(nxt):  # line before the heading `nxt`
    return at(nxt) - 1
scope = (at("## SCOPE BOUNDARY"), at("Implementation belongs to the `Fix issue #N`"))
load = (at("## Phase 1: LOAD"), upto("## Phase 2: PLAN WRITING") - 1)
plan = (at("## Phase 2: PLAN WRITING"), upto("## Phase 3: ARCHITECT REVIEW") - 1)
arch = (at("## Phase 3: ARCHITECT REVIEW"), upto("## Phase 3.5: CONFORMANCE REVIEW") - 1)
conf = (at("## Phase 3.5: CONFORMANCE REVIEW"), upto("## Phase 4: PUBLISH") - 1)
pub = (at("## Phase 4: PUBLISH"), len(L))
h = at('### If architect returns "Issues Found":')
arch_cap = (h, at("- Exit cleanly", h))
r = at("**Reconcile loop** (only if MATERIAL)")
conf_cap = (r, at("Exit cleanly (do not abort", r))
print("run-lifecycle  scope=%d-%d load=%d-%d plan=%d-%d architect=%d-%d conformance=%d-%d publish=%d-%d" % (*scope, *load, *plan, *arch, *conf, *pub))
print("run-lifecycle  limits: architect-cap=%d-%d conformance-cap=%d-%d" % (*arch_cap, *conf_cap))
print("configuration  conformance.enabled=%d max_reconcile_cycles=%d shadow_model=%d-%d direct-to-pr=%d plan_grace=%d" % (
    at("If `conformance.enabled` is `false`"), at("Determine `MAX_CYCLES`"),
    at("ROOT=$(git rev-parse --show-toplevel 2>/dev/null)"), at('SHADOW_MODEL_PIN="${CONFORMANCE_SHADOW_MODEL-'),
    at('grep -q "direct-to-pr"'), at("PLAN_GRACE=$(")))
print("features       oos_excise=%d context-pack=%d render-prompt=%d" % (
    next(i + 1 for i, l in enumerate(L) if "oos_excise.sh" in l and "# TARGET-PATH" in l),
    at("No DAG node currently produces `context-pack.md`"),
    at("python3 dark-factory/scripts/factory_core/cli.py render-prompt")))
EOF
~~~~

  Expected output (exactly this if Tasks 3–6 were applied verbatim):

~~~~text
run-lifecycle  scope=44-52 load=56-133 plan=135-196 architect=198-255 conformance=257-378 publish=380-502
run-lifecycle  limits: architect-cap=238-252 conformance-cap=329-360
configuration  conformance.enabled=261 max_reconcile_cycles=266 shadow_model=274-276 direct-to-pr=390 plan_grace=391
features       oos_excise=431 context-pack=62 render-prompt=217
~~~~

  If any number differs, use the printed value in place of the matching number in Step 2's
  script. Each printed key maps one-to-one to a replacement there. The *old* strings in Step 2
  are the citations the docs carry at `2429106`. If one of Step 2's count assertions fails,
  `main` has moved again: nothing has been written (each file is written only after all its
  assertions pass), so read the doc's current citation for that key, use it as the old string,
  and re-run.
- [ ] **Step 2: Apply the doc edits.** Every replacement asserts its expected occurrence
  count, so a drifted doc fails loudly instead of being half-edited:

~~~~bash
python3 - <<'EOF'
from pathlib import Path
def sub(path, pairs):
    p = Path(path); t = p.read_text(encoding="utf-8")
    for old, new, n in pairs:
        assert t.count(old) == n, (path, old, t.count(old)); t = t.replace(old, new)
    p.write_text(t, encoding="utf-8")

sub("docs/configuration.md", [
    ("`commands/dark-factory-plan.md:299`", "`commands/dark-factory-plan.md:391`", 1),
    ("`commands/dark-factory-plan.md:298`", "`commands/dark-factory-plan.md:390`", 2),
    ("`commands/dark-factory-plan.md:174`", "`commands/dark-factory-plan.md:261`", 1),
    ("`commands/dark-factory-plan.md:179`", "`commands/dark-factory-plan.md:266`", 1),
    ("`commands/dark-factory-plan.md:186-188`", "`commands/dark-factory-plan.md:274-276`", 1),
])
sub("docs/features.md", [
    ("| **Planning.** Writes an implementation plan, has an architect subagent review it, then checks the plan against the spec with the conformance rubric. Applies `plan-pending-review` once the plan is on the branch. |",
     "| **Planning.** Writes an implementation plan as a draft (in `plan-drafts/`, next to `docs/superpowers/plans/`) and pushes it before review, so a re-dispatch after a kill resumes from the draft. Has an architect subagent review it, then checks the plan against the spec with the conformance rubric, then moves it into `docs/superpowers/plans/`. Applies `plan-pending-review` once the plan is on the branch. |", 1),
    # `:308` is what the doc says at 2429106 (already one line stale after #406).
    ("`dark-factory-plan.md:308`", "`dark-factory-plan.md:431`", 1),
    ("`dark-factory-plan.md:60`", "`dark-factory-plan.md:62`", 1),
    ("`dark-factory-plan.md:133`", "`dark-factory-plan.md:217`", 1),
])
sub("docs/domain.md", [
    ("holds **in-flight** implementation plans. Both are write-once artifacts",
     "holds **in-flight** implementation plans. `docs/superpowers/plan-drafts/` holds plan drafts under review on a refine branch; a draft is moved into `plans/` when the plan is published and is never present on `main`. Specs and plans are write-once artifacts", 1),
])
sub("docs/run-lifecycle.md", [
    ("""- **Scope**: may only write under `docs/superpowers/plans/` (`commands/dark-factory-plan.md:44-50`).
- **Load** (`:54-89`): `issue.json`, the architect prompt, the spec, memory.
- **Plan** (`:91-112`): `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`, with an
  `**Issue:** #N` line, a file-structure table and TDD task steps.
- **Architect review** (`:114-168`):""",
     """- **Scope**: may only write under `docs/superpowers/plans/`, and under its sibling
  `plan-drafts/` for the draft (`commands/dark-factory-plan.md:44-52`).
- **Load** (`:56-133`): `issue.json`, the architect prompt, the spec, memory. Then looks for
  a draft that an earlier, killed run checkpointed (`push_gate_check.sh` with the
  `plan-drafts/` prefix). A non-empty draft whose last commit descends from
  the spec's last commit is resumed: plan writing is skipped, feedback posted on the issue is
  applied to the draft, and architect review, plan-vs-spec conformance and publish run in
  full with fresh cycle counters. An empty or stale draft is rewritten at the same path. A
  finished plan already under `docs/superpowers/plans/` is not a resume signal.
- **Plan** (`:135-196`): `plan-drafts/YYYY-MM-DD-<feature>.md`, with an
  `**Issue:** #N` line, a file-structure table and TDD task steps. Before the first
  architect spawn the command commits the draft and pushes the branch (`timeout 30`); a
  failed push prints a warning and the run continues on the local commit. The checkpoint is
  re-run before each `needs-discussion` exit below, so a capped plan leaves its revised
  draft on the branch and no file under `docs/superpowers/plans/`.
- **Architect review** (`:198-255`):""", 1),
    ("- **Plan-vs-spec conformance** (`:170-286`):", "- **Plan-vs-spec conformance** (`:257-378`):", 1),
    ("""- **Publish** (`:288-378`): out-of-scope gate (allowlist: plans, specs, memory), commit,""",
     """- **Publish** (`:380-502`): moves the draft into `docs/superpowers/plans/` with `git mv` in
  one commit (reusing the path of an earlier plan for the issue on the branch, if any). This
  runs before the out-of-scope gate, so the draft path nets out of the diff the gate judges.
  Then out-of-scope gate (allowlist: plans, specs, memory), commit,""", 1),
    ("""  move the issue to **Ready** to approve.

### `dark-factory-implement`""",
     """  move the issue to **Ready** to approve.

The command pushes only the draft checkpoint. `plan-push-and-advance` pushes the published
plan and applies `plan-pending-review`.

### `dark-factory-implement`""", 1),
    ("""prints nothing if there is none, and always exits 0 (`scripts/push_gate_check.sh:56-115`).
""",
     """prints nothing if there is none, and always exits 0 (`scripts/push_gate_check.sh:56-115`).
A draft plan under `plan-drafts/` is not an artifact for this gate: a plan
run that ends with only a draft takes the "No artifact" rows below.
""", 1),
    ("| `commands/dark-factory-plan.md:154-165` |", "| `commands/dark-factory-plan.md:238-252` |", 1),
    ("| `commands/dark-factory-plan.md:240-268` |", "| `commands/dark-factory-plan.md:329-360` |", 1),
])
print("docs edited")
EOF
~~~~

  Expected: `docs edited`.
- [ ] **Step 3: Verify.**
  `python scripts/docs_refs_check.py docs/run-lifecycle.md docs/scheduler.md docs/features.md docs/configuration.md`
  Expected: `Docs reference check passed for 4 doc(s).`
  `PYTHONPATH=scripts python -m pytest tests/test_docs_refs_check.py tests/test_docs_conformance_reference_docs.py -q` → all pass.
  `grep -n "docs/superpowers/plan-drafts" docs/run-lifecycle.md docs/features.md docs/configuration.md docs/scheduler.md`
  → no output. Only the short form `plan-drafts/` appears in the checked docs (see "Lessons").
- [ ] **Step 4: Commit.**
  `git add docs/run-lifecycle.md docs/configuration.md docs/features.md docs/domain.md && git commit -m "docs(#409): describe the plan draft checkpoint; re-derive dark-factory-plan.md citations"`

## Task 8: Full verification and scope check

**Files:** none modified

- [ ] **Step 1: Full suite.** `PYTHONPATH=scripts python -m pytest tests/ -q`
  Expected: all pass, the pre-change count plus 29 (26 + 2 + 1). On `2429106` under Linux
  (WSL Ubuntu) the review run gave `4444 passed, with tests/test_provider_model_adapter.py excluded (that host's aiohttp is too old to import it; unrelated to this change)`. On a non-POSIX host the executing
  tests skip.
- [ ] **Step 2: Untouched surfaces.**
  `git diff --stat origin/main -- workflows/ scripts/push_gate_check.sh scripts/transfer_refine_artifacts.sh scheduler.sh CLAUDE.md docs/triage-labels.md .github/ .claude/`
  Expected: no output (spec acceptance criterion).
- [ ] **Step 3: Workflow DAG checks** (CI parity; the workflow is unchanged, so these must still pass).
  `python scripts/check_workflow_dag.py workflows/archon-dark-factory.yaml` → `DAG trigger_rule check passed for 1 workflow file(s).`
  `python scripts/check_workflow_when.py workflows/archon-dark-factory.yaml` → `When: expression lint passed for 1 workflow file(s).`
- [ ] **Step 4: Changed-file set.** `git diff --name-only origin/main...HEAD` lists only the eight
  files in the File Structure table, plus the spec and plan that `transfer_refine_artifacts.sh`
  copied from the refine branch.

## Spec coverage

| Spec item | Task |
|---|---|
| Req 1 (commit *and* push to the refine branch) | 3 (row 1) |
| Req 2 (push failure non-fatal, no retry loop) | 3 (row 2) |
| Req 3 (draft told apart by path; gate code untouched; `CLAUDE.md` unedited) | 3, 6, 1, 8 |
| Req 4 (resume skips writing only; fresh counters) | 4 |
| Req 5 (no change to retry accounting/breaker/budgets/`gate_*`) | 1, 8 |
| Req 6 (non-goal: finished plan is not a resume signal) | 4 (`test_detect_ignores_a_finished_plan`) |
| Req 7 (non-goal: refine spec drafts) | not touched |
| §2 stale-draft guard, context-pack spec, rewrite in place | 4 (rows 4–5, context-pack test) |
| §2 apply posted feedback on resume | 4 (Phase 2 resume paragraph) |
| §3 checkpoint before both `needs-discussion` exits | 5 |
| §4 promote before OOS gate, reuse earlier plan path, remove other drafts | 6 (rows 7–10) |
| §5 refine OOS gate excises a draft | 6 (row 11) |
| §5 draft cannot reach `main` | 2 |
| §7 docs | 7 |
