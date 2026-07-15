# Plan: Artifact-Gate `refine-push` / `plan-push-and-advance` on Committed Spec/Plan Existence

**Issue:** #212
**Spec:** `docs/superpowers/specs/2026-07-15-refine-plan-push-artifact-gate-design.md`
**Status:** plan

## Goal

Stop `refine-push` and `plan-push-and-advance` (`workflows/archon-dark-factory.yaml`) from
pushing an empty branch and applying a gate label (`spec-pending-review` /
`plan-pending-review`) when the upstream `refine`/`plan` command node was killed mid-work but
misreported as `dag_node_completed`. Both nodes must instead check — git-aware, on commits the
branch actually made beyond `main` — whether a committed spec/plan file for the issue exists.
When it does, behavior is unchanged (push + label). When it doesn't, distinguish a
already-communicated clean abort (`needs-discussion` label live on the issue) from a true
silent death: the former exits quietly, the latter posts an idempotent
`<!-- df-refine-failure -->` marker-comment and exits 0, leaving the item unlabeled so the
scheduler's existing Priority 4/5 retry-then-`trip_to_blocked` machinery (`scheduler.sh`,
unchanged) reclaims it automatically.

**Build constraint (carried from spec):** `workflows/archon-dark-factory.yaml` is baked into
the factory image and only materializes into a fresh clone's `.archon/workflows/` when the
clone doesn't already provide its own copy. This plan's YAML edit takes effect on the next
`docker compose build` + scheduler/run image redeploy — not merely on merge. No task below
performs that redeploy; it is a deployment action outside this plan's scope (`deploy/` is
human-only per `CLAUDE.md`).

## Architecture

Today, both push nodes are unconditional:

```bash
git push -u origin "$BRANCH"
python3 "${CLONE_DIR:-.}/dark-factory/scripts/factory_core/providers/cli.py" \
  tracker label --id "$ISSUE" --add spec-pending-review
```

The fix extracts the tricky, execution-testable part — "does a committed artifact for this
issue exist on this branch's own commits beyond `main`?" — into a new standalone script,
`scripts/push_gate_check.sh`, mirroring the existing `scripts/oos_excise.sh` pattern (a shared
bash utility, invoked identically by both `refine-push` and `plan-push-and-advance` via the
same `${CLONE_DIR:-.}/dark-factory/scripts/...` TARGET-PATH convention every other DAG bash
node already uses). The git-diff + commit-count logic is pure and network-free, so it gets a
real execution-based test suite (fixture git repo, no stubbing) instead of only static
string assertions.

The two DAG node bodies then branch on that script's output, reusing the codebase's existing
`tracker get --fields labels | jq -r '.labels[].name' | grep -c '<label>' || true` idiom
(already used at `workflows/archon-dark-factory.yaml:227` in `close-preview`) for the live
`needs-discussion` check, and the codebase's existing `tracker comment --marker --body-file`
idiom for the idempotent failure-comment upsert. This part stays inline in the YAML (matching
every other DAG node — none of them delegate `tracker`/`codehost` CLI calls to a wrapper
script) and gets static content-assertion tests, mirroring `tests/test_budget_enforce_dag.py`'s
established convention for testing DAG bash-node bodies.

```
push_gate_check.sh <prefix> <issue#>
        │
        ├─ commits beyond main? ──no──► print nothing, exit 0
        │         │yes
        │         ▼
        └─ grep "#<issue#>" in committed files under <prefix> ──► print first match (or nothing)

refine-push / plan-push-and-advance:
        │
   ARTIFACT_FILE = push_gate_check.sh output
        │
   ┌────┴─────┐
   │ non-empty │ → git push, tracker label --add <gate-label>  (unchanged path)
   └────┬─────┘
   empty
        │
   needs-discussion live on issue? ──yes──► log + skip silently (no push/label/comment)
        │no
        ▼
   log + tracker comment --marker "<!-- df-refine-failure -->" --body-file <failure body>
   (no push, no label — item stays retryable via existing Priority 4/5 scheduler logic)
```

## Tech Stack

- Bash (`scripts/push_gate_check.sh`, DAG node bodies in `workflows/archon-dark-factory.yaml`)
- `scripts/factory_core/providers/cli.py` (existing `tracker get` / `tracker label` /
  `tracker comment --marker` subcommands — no CLI changes needed)
- pytest (`tests/test_push_gate_check.py` — subprocess/fixture-git execution tests, modeled on
  `tests/test_oos_excise.py`; `tests/test_push_gate_dag.py` — static YAML content assertions,
  modeled on `tests/test_budget_enforce_dag.py`)

## File Structure

| File | Change |
|---|---|
| `scripts/push_gate_check.sh` | **New.** Git-aware artifact-existence check, shared by both push nodes. |
| `tests/test_push_gate_check.py` | **New.** Execution tests against a fixture git repo (bare origin + working clone), no network/stubbing needed. |
| `workflows/archon-dark-factory.yaml` | Modify `refine-push` (~L442) and `plan-push-and-advance` (~L460) `bash:` bodies. `depends_on`/`when`/`timeout` unchanged. |
| `tests/test_push_gate_dag.py` | **New.** Static content assertions on the two modified node bodies, mirroring `test_budget_enforce_dag.py`. |

---

## Task 1: Add `scripts/push_gate_check.sh` (git-aware artifact check), test-first

**Files:** `tests/test_push_gate_check.py`, `scripts/push_gate_check.sh`

### Step 1.1 — write the failing tests

Create `tests/test_push_gate_check.py`:

```python
"""Tests for scripts/push_gate_check.sh — git-aware committed-artifact existence check
used by refine-push/plan-push-and-advance (workflows/archon-dark-factory.yaml, #212)."""
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "push_gate_check.sh"


def run_script(prefix: str, issue: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), prefix, issue],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def git(*args, cwd, **kwargs):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, **kwargs)


@pytest.fixture()
def git_repo(tmp_path):
    """Bare-origin + working-tree git fixture (same shape as test_oos_excise.py's)."""
    bare = tmp_path / "bare"
    work = tmp_path / "work"
    bare.mkdir()
    git("init", "--bare", str(bare), cwd=str(tmp_path))
    subprocess.run(
        ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
        cwd=str(bare), capture_output=True,
    )
    git("clone", str(bare), str(work), cwd=str(tmp_path))
    git("config", "user.email", "test@test.com", cwd=str(work))
    git("config", "user.name", "Test", cwd=str(work))
    (work / "README.md").write_text("root\n")
    git("add", "README.md", cwd=str(work))
    git("commit", "-m", "init", cwd=str(work))
    git("push", "origin", "HEAD:main", cwd=str(work))
    git("branch", "--set-upstream-to=origin/main", "main", cwd=str(work))
    git("checkout", "-b", "refine/issue-212-test", cwd=str(work))
    return work


class TestPushGateCheckScript:
    def test_script_exists(self):
        assert SCRIPT.exists(), f"Script not found: {SCRIPT}"

    def test_script_syntax_valid(self):
        result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        assert result.returncode == 0, f"Syntax error: {result.stderr}"

    def test_missing_prefix_arg_fails(self, tmp_path):
        result = subprocess.run(
            ["bash", str(SCRIPT)], capture_output=True, text=True, cwd=str(tmp_path)
        )
        assert result.returncode != 0

    def test_missing_issue_arg_fails(self, tmp_path):
        result = subprocess.run(
            ["bash", str(SCRIPT), "docs/superpowers/specs/"],
            capture_output=True, text=True, cwd=str(tmp_path),
        )
        assert result.returncode != 0

    def test_no_commits_beyond_main_returns_empty(self, git_repo):
        """HEAD == main (no branch-local commits): must print nothing, exit 0."""
        result = run_script("docs/superpowers/specs/", "212", git_repo)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""

    def test_committed_matching_file_found(self, git_repo):
        spec_dir = git_repo / "docs" / "superpowers" / "specs"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "2026-07-15-example-design.md"
        spec_file.write_text("# Design\n\n**Issue:** #212\n")
        git("add", "docs/superpowers/specs/2026-07-15-example-design.md", cwd=str(git_repo))
        git("commit", "-m", "spec", cwd=str(git_repo))

        result = run_script("docs/superpowers/specs/", "212", git_repo)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "docs/superpowers/specs/2026-07-15-example-design.md"

    def test_committed_file_without_issue_reference_not_found(self, git_repo):
        spec_dir = git_repo / "docs" / "superpowers" / "specs"
        spec_dir.mkdir(parents=True)
        spec_file = spec_dir / "2026-07-15-example-design.md"
        spec_file.write_text("# Design\n\nNo issue reference here.\n")
        git("add", "docs/superpowers/specs/2026-07-15-example-design.md", cwd=str(git_repo))
        git("commit", "-m", "spec", cwd=str(git_repo))

        result = run_script("docs/superpowers/specs/", "212", git_repo)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""

    def test_commits_beyond_main_but_no_artifact_reproduces_212(self, git_repo):
        """Reproduces the exact #212 failure mode: branch has commits, but none of them
        touch the artifact prefix (e.g. only a token-budget or memory-write side commit) —
        must print nothing, exit 0."""
        (git_repo / "unrelated.txt").write_text("side effect\n")
        git("add", "unrelated.txt", cwd=str(git_repo))
        git("commit", "-m", "unrelated", cwd=str(git_repo))

        result = run_script("docs/superpowers/specs/", "212", git_repo)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""

    def test_uncommitted_artifact_file_not_detected(self, git_repo):
        """A spec file written to disk but never `git commit`-ed (the mid-death case) must
        NOT be detected — this is the git-aware distinction from a bare `grep -rl` scan."""
        spec_dir = git_repo / "docs" / "superpowers" / "specs"
        spec_dir.mkdir(parents=True)
        (spec_dir / "2026-07-15-example-design.md").write_text("# Design\n\n**Issue:** #212\n")
        # Deliberately not committed.

        result = run_script("docs/superpowers/specs/", "212", git_repo)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ""
```

### Step 1.2 — verify the tests fail

```bash
python -m pytest tests/test_push_gate_check.py -v
```

Expected: `test_script_exists` and `test_script_syntax_valid` fail (file does not exist yet);
every other test errors out with a "No such file or directory" `FileNotFoundError` from
`subprocess.run` invoking a nonexistent script. All collected tests fail or error — none pass.

### Step 1.3 — implement `scripts/push_gate_check.sh`

```bash
#!/usr/bin/env bash
# Determine whether a committed spec/plan artifact for an issue exists on commits this
# branch has made beyond main. Used by refine-push/plan-push-and-advance
# (workflows/archon-dark-factory.yaml) to gate the push+label step on the artifact
# actually existing, rather than on the upstream command node merely being reported
# "completed" — a killed/parked agent can be misreported as completed (#212).
#
# Usage: push_gate_check.sh <artifact-prefix> <issue-number>
#   <artifact-prefix>  path prefix to search, e.g. "docs/superpowers/specs/"
#   <issue-number>     issue number to match via "#<issue-number>" in file content
#
# Stdout: path of the first matching committed file, or nothing if none found.
# Exit: always 0 — "no artifact" is a valid outcome for the caller to branch on, not a
# script error. `pipefail` is on below, so a `grep -l` finding no match does make the
# `git diff | xargs | grep | head` pipeline's own exit status nonzero — but that status
# is never checked (no `set -e`, and the trailing `exit 0` runs unconditionally), so the
# script still prints the correct (empty) result and exits 0.
#
# Intentionally uses `main...HEAD` (merge-base three-dot) for the file-list diff below,
# NOT the two-dot `main..HEAD` form used by push-and-pr's OOS content-identity check
# (memory: codebase-patterns.md, issue #250) — that two-dot form answers "does this
# file's content differ from main's current tip", a different question from "which
# files did this branch touch since it forked", which is what this check needs.
set -uo pipefail

ARTIFACT_PREFIX="${1:?Usage: push_gate_check.sh <artifact-prefix> <issue-number>}"
ISSUE_NUM="${2:?Usage: push_gate_check.sh <artifact-prefix> <issue-number>}"

HAS_COMMITS=$(git rev-list --count main..HEAD 2>/dev/null || echo 0)
if [ "$HAS_COMMITS" -gt 0 ]; then
  git diff --name-only main...HEAD -- "$ARTIFACT_PREFIX" 2>/dev/null \
    | xargs -r grep -l "#${ISSUE_NUM}\b" 2>/dev/null | head -1
fi
exit 0
```

```bash
chmod +x scripts/push_gate_check.sh
```

### Step 1.4 — verify the tests pass

```bash
python -m pytest tests/test_push_gate_check.py -v
```

Expected output: all 9 tests pass, e.g.:

```
tests/test_push_gate_check.py::TestPushGateCheckScript::test_script_exists PASSED
tests/test_push_gate_check.py::TestPushGateCheckScript::test_script_syntax_valid PASSED
tests/test_push_gate_check.py::TestPushGateCheckScript::test_missing_prefix_arg_fails PASSED
tests/test_push_gate_check.py::TestPushGateCheckScript::test_missing_issue_arg_fails PASSED
tests/test_push_gate_check.py::TestPushGateCheckScript::test_no_commits_beyond_main_returns_empty PASSED
tests/test_push_gate_check.py::TestPushGateCheckScript::test_committed_matching_file_found PASSED
tests/test_push_gate_check.py::TestPushGateCheckScript::test_committed_file_without_issue_reference_not_found PASSED
tests/test_push_gate_check.py::TestPushGateCheckScript::test_commits_beyond_main_but_no_artifact_reproduces_212 PASSED
tests/test_push_gate_check.py::TestPushGateCheckScript::test_uncommitted_artifact_file_not_detected PASSED
========================= 9 passed =========================
```

### Step 1.5 — commit

```bash
git add scripts/push_gate_check.sh tests/test_push_gate_check.py
git commit -m "feat(workflow): add push_gate_check.sh — git-aware spec/plan artifact check (#212)"
```

---

## Task 2: Wire `refine-push` and `plan-push-and-advance` to the gate, test-first

**Files:** `tests/test_push_gate_dag.py`, `workflows/archon-dark-factory.yaml`

### Step 2.1 — write the failing tests

Create `tests/test_push_gate_dag.py`:

```python
"""Static content assertions for the artifact-gated refine-push/plan-push-and-advance
DAG nodes (#212), mirroring the tests/test_budget_enforce_dag.py convention for testing
DAG bash-node bodies without executing them."""
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _REPO_ROOT / "workflows" / "archon-dark-factory.yaml"


def _workflow_nodes():
    data = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return {n["id"]: n for n in data.get("nodes", []) if isinstance(n, dict) and "id" in n}


@pytest.mark.parametrize("node_id,prefix,label,noun", [
    ("refine-push", "docs/superpowers/specs/", "spec-pending-review", "spec"),
    ("plan-push-and-advance", "docs/superpowers/plans/", "plan-pending-review", "plan"),
])
class TestPushGateNodes:
    def test_node_calls_push_gate_check_script(self, node_id, prefix, label, noun):
        bash = _workflow_nodes()[node_id]["bash"]
        assert "push_gate_check.sh" in bash, f"'{node_id}' must call push_gate_check.sh"
        assert prefix in bash, f"'{node_id}' must pass artifact prefix '{prefix}'"

    def test_node_checks_needs_discussion_live(self, node_id, prefix, label, noun):
        bash = _workflow_nodes()[node_id]["bash"]
        assert "needs-discussion" in bash, \
            f"'{node_id}' must check the live needs-discussion label"

    def test_node_posts_failure_marker_on_miss(self, node_id, prefix, label, noun):
        bash = _workflow_nodes()[node_id]["bash"]
        assert "df-refine-failure" in bash, \
            f"'{node_id}' must post the <!-- df-refine-failure --> marker comment on a true miss"
        assert "tracker comment" in bash and "--marker" in bash, \
            f"'{node_id}' must use the tracker comment --marker upsert primitive"

    def test_node_gates_push_and_label_behind_artifact_check(self, node_id, prefix, label, noun):
        bash = _workflow_nodes()[node_id]["bash"]
        gate_pos = bash.find("push_gate_check.sh")
        push_pos = bash.find("git push")
        label_pos = bash.find(f"--add {label}")
        assert gate_pos != -1 and push_pos != -1 and label_pos != -1
        assert gate_pos < push_pos, \
            f"'{node_id}': push_gate_check.sh must run before git push"
        assert gate_pos < label_pos, \
            f"'{node_id}': push_gate_check.sh must run before the gate label is applied"

    def test_node_depends_on_and_when_unchanged(self, node_id, prefix, label, noun):
        node = _workflow_nodes()[node_id]
        upstream = "refine" if node_id == "refine-push" else "plan"
        intent = "refine" if node_id == "refine-push" else "plan"
        assert node["depends_on"] == [upstream]
        assert intent in node["when"]
        assert node["timeout"] == 30000


def test_dag_validator_passes():
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    from check_workflow_dag import check
    errors = check(_WORKFLOW)
    assert errors == [], "\n".join(errors)
```

### Step 2.2 — verify the tests fail

```bash
python -m pytest tests/test_push_gate_dag.py -v
```

Expected: every `test_node_calls_push_gate_check_script`, `test_node_checks_needs_discussion_live`,
`test_node_posts_failure_marker_on_miss`, and `test_node_gates_push_and_label_behind_artifact_check`
case fails (the nodes are still unconditional today); `test_node_depends_on_and_when_unchanged`
and `test_dag_validator_passes` already pass (those fields aren't changing).

### Step 2.3 — implement: rewrite `refine-push`

In `workflows/archon-dark-factory.yaml`, replace the `refine-push` node body (currently at
line 442) — keep the `depends_on`/`when`/`timeout` and the three comment lines above it
unchanged, replace only the `bash:` block:

```yaml
  - id: refine-push
    bash: |
      ISSUE=$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")
      BRANCH=$(git branch --show-current)
      _PCLI="${CLONE_DIR:-.}/dark-factory/scripts/factory_core/providers/cli.py"

      SPEC_FILE=$(bash "${CLONE_DIR:-.}/dark-factory/scripts/push_gate_check.sh" "docs/superpowers/specs/" "$ISSUE")  # TARGET-PATH

      if [ -n "$SPEC_FILE" ]; then
        git push -u origin "$BRANCH"
        python3 "$_PCLI" tracker label --id "$ISSUE" --add spec-pending-review
        echo "Pushed $BRANCH for issue #$ISSUE (spec-pending-review gate applied)"
      else
        HAS_NEEDS_DISCUSSION=$(python3 "$_PCLI" tracker get --id "$ISSUE" --fields labels \
          | jq -r '.labels[].name' \
          | grep -c 'needs-discussion' || true)
        if [ "$HAS_NEEDS_DISCUSSION" -gt 0 ]; then
          echo "refine-push: no committed spec for issue #$ISSUE, but needs-discussion is already applied — clean abort, skipping silently."
        else
          echo "refine-push: no committed spec found for issue #$ISSUE and no needs-discussion label — treating as silent death."
          _FAIL_BODY="<!-- df-refine-failure -->
      ## Refinement Pipeline — Failed

      The refine agent ended without producing a committed spec (\`docs/superpowers/specs/\`) for this issue. No gate label was applied; this item remains eligible for automatic retry.

      \`\`\`bash
      # Retry manually if needed
      docker compose --profile factory run --rm dark-factory \"Refine issue #${ISSUE}\"
      \`\`\`

      ---
      *Posted by ${FACTORY_PRODUCT_NAME} Refinement Pipeline*"
          TMPFILE=$(mktemp /tmp/refine-failure-XXXXXX.md)
          printf '%s' "$_FAIL_BODY" > "$TMPFILE"
          python3 "$_PCLI" tracker comment --id "$ISSUE" --marker "<!-- df-refine-failure -->" --body-file "$TMPFILE"
          rm -f "$TMPFILE"
        fi
      fi
    depends_on: [refine]
    when: "$parse-intent.output.intent == 'refine'"
    timeout: 30000
```

(This builds the comment body as a double-quoted bash variable, then `printf '%s' > file` — the
same pattern `push-and-pr` already uses at `workflows/archon-dark-factory.yaml:1080-1100` — not
a heredoc. No `bash: |` YAML block scalar node in this workflow file uses a heredoc anywhere;
a `<<EOF`/`EOF` pair would need the closing delimiter at column 0 to match under plain `<<EOF`,
which the node's nested `if`/`else` indentation makes easy to get wrong silently. `${ISSUE}` is
escaped as `\"Refine issue #${ISSUE}\"` because the whole body is itself inside a double-quoted
bash string.)

### Step 2.4 — implement: rewrite `plan-push-and-advance`

Same shape, `docs/superpowers/plans/` prefix, `plan-pending-review` label, plan-flavored
comment body:

```yaml
  - id: plan-push-and-advance
    bash: |
      ISSUE=$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")
      BRANCH=$(git branch --show-current)
      _PCLI="${CLONE_DIR:-.}/dark-factory/scripts/factory_core/providers/cli.py"

      PLAN_FILE=$(bash "${CLONE_DIR:-.}/dark-factory/scripts/push_gate_check.sh" "docs/superpowers/plans/" "$ISSUE")  # TARGET-PATH

      if [ -n "$PLAN_FILE" ]; then
        git push -u origin "$BRANCH"
        python3 "$_PCLI" tracker label --id "$ISSUE" --add plan-pending-review
        echo "Pushed $BRANCH for issue #$ISSUE (plan-pending-review gate applied)"
      else
        HAS_NEEDS_DISCUSSION=$(python3 "$_PCLI" tracker get --id "$ISSUE" --fields labels \
          | jq -r '.labels[].name' \
          | grep -c 'needs-discussion' || true)
        if [ "$HAS_NEEDS_DISCUSSION" -gt 0 ]; then
          echo "plan-push-and-advance: no committed plan for issue #$ISSUE, but needs-discussion is already applied — clean abort, skipping silently."
        else
          echo "plan-push-and-advance: no committed plan found for issue #$ISSUE and no needs-discussion label — treating as silent death."
          _FAIL_BODY="<!-- df-refine-failure -->
      ## Refinement Pipeline — Failed

      The plan agent ended without producing a committed implementation plan (\`docs/superpowers/plans/\`) for this issue. No gate label was applied; this item remains eligible for automatic retry.

      \`\`\`bash
      # Retry manually if needed
      docker compose --profile factory run --rm dark-factory \"Plan issue #${ISSUE}\"
      \`\`\`

      ---
      *Posted by ${FACTORY_PRODUCT_NAME} Refinement Pipeline*"
          TMPFILE=$(mktemp /tmp/refine-failure-XXXXXX.md)
          printf '%s' "$_FAIL_BODY" > "$TMPFILE"
          python3 "$_PCLI" tracker comment --id "$ISSUE" --marker "<!-- df-refine-failure -->" --body-file "$TMPFILE"
          rm -f "$TMPFILE"
        fi
      fi
    depends_on: [plan]
    when: "$parse-intent.output.intent == 'plan'"
    timeout: 30000
```

### Step 2.5 — verify the tests pass

```bash
python -m pytest tests/test_push_gate_dag.py tests/test_push_gate_check.py -v
```

Expected: all tests in both files pass.

Also run the full suite to confirm no cross-test regression (workflow YAML is shared state
across many test files — `test_workflow_when.py`, `test_budget_enforce_dag.py`,
`test_workflow_code_review.py`, `test_workflow_or_join.py` all parse the same file):

```bash
python -m pytest tests/ -v
```

Expected output: all tests pass, no `FAILED` lines. `refine-push`/`plan-push-and-advance` are
not in `check_workflow_dag.py`'s `REQUIRED_OR_JOIN_NODES` set and this change adds no
`trigger_rule`, so the DAG validator's tripwire count is unaffected.

### Step 2.6 — commit

```bash
git add workflows/archon-dark-factory.yaml tests/test_push_gate_dag.py
git commit -m "fix(workflow): gate refine-push/plan-push-and-advance on committed artifact existence (#212)"
```

---

## Validation summary (maps to spec's Decision sections)

- **§1 Artifact-gate check, git-aware, committed-file only:** Task 1 (`push_gate_check.sh`,
  `git rev-list --count main..HEAD` + `git diff --name-only main...HEAD` gate) + Task 2 Steps
  2.3/2.4 (both nodes call it, branch on non-empty output).
- **§2 Distinguish clean abort from true silent death:** Task 2 Steps 2.3/2.4, the
  `HAS_NEEDS_DISCUSSION` live check (mirrors `close-preview`'s existing idiom at
  `workflows/archon-dark-factory.yaml:227`) gating whether the failure-comment branch runs at
  all.
- **§3 Idempotent marker-upsert failure comment, reused marker:** Task 2 Steps 2.3/2.4, both
  nodes post via `tracker comment --marker "<!-- df-refine-failure -->" --body-file`, wording
  adjusted per phase (spec/plan, refine/plan retry command).
- **§4 Exit code 0 on a miss:** satisfied structurally — neither node sets `-e`/`pipefail`,
  and every statement on the miss path (`echo`, `rm -f`) exits 0 on success; no task adds an
  explicit `exit` call on the miss path, matching the spec's requirement that withholding the
  label is normal, not an error.
- **Both nodes mirror identically:** Task 2 Steps 2.3/2.4 use the same shared
  `push_gate_check.sh` (Task 1) with only the prefix/label/wording varying — verified together
  by the parametrized `TestPushGateNodes` class in `tests/test_push_gate_dag.py`.

## Known limitations (carried from spec, no code action)

- The grep-based `#<issue-number>` file-content match is left as-is (shared with existing
  `push-and-pr`/`budget-plan` discovery), not hardened — a spec/plan file committed without
  the literal `#<issue-number>` text would still gate as "missing". Not fixed by this plan.
- `plan_advance_check`'s grace-timer marker mismatch (keys on the spec report marker, not a
  plan-specific one) is explicitly out of scope — different file (`scheduler.sh`), different
  mechanism; left for its own ticket.
- The `push-and-pr` (implement-phase) node has the same unconditional-push shape and the same
  symptom under issue #208 — explicitly a separate ticket, not touched here.
- The underlying executor bug (a parked/killed node reported as `dag_node_completed` instead
  of `failed`) is external `archon` DAG runtime infrastructure this repo doesn't own; this
  plan's artifact gate is the independent second layer the spec calls for, not a fix to the
  executor itself.
- This change requires an image rebuild + scheduler/run redeploy to take effect in production
  (see Goal's Build constraint) — no task in this plan performs that; it's a deployment action.
