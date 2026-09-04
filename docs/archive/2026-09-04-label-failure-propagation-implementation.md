# Implementation Plan: Propagate add_label/remove_label failure through Tracker/cli.py/DAG gate nodes

**Issue:** omniscient/dark-factory#358
**Spec:** [docs/superpowers/specs/2026-09-04-label-failure-propagation-design.md](../specs/2026-09-04-label-failure-propagation-design.md)

## Goal

`GitHubTracker.add_label`/`remove_label` currently run `gh issue edit` and never check the
return code, so `cli.py:_tracker_label` always exits 0 even when a label never reached
GitHub — this silently broke the `spec-pending-review`/`plan-pending-review` gate labels
under API rate exhaustion (#342, #334, #341) and drove the scheduler into a re-dispatch
loop. This plan mirrors the already-shipped `set_status` fix (#335/PR #352) through the
`Tracker` ABC → `GitHubTracker`/`JiraTracker` → `cli.py` chain, and adds a warn-advisory
`if`/`else` branch (log + durable marker comment, `|| true`-guarded, no retry) to the two
DAG nodes that call it.

## Architecture

- `Tracker.add_label`/`remove_label` ABC signatures widen `None` → `bool`.
- `GitHubTracker` implementations check `gh issue edit`'s returncode, print stderr on
  failure, return the bool.
- `JiraTracker` implementations return `True` after their existing `_request` call
  succeeds (an HTTP failure still raises `RuntimeError` via `_request`, unchanged).
- `cli.py:_tracker_label` attempts every requested add/remove, tracks a running `ok`
  flag, exits 1 with `ERROR: ...` if any failed; catches `RuntimeError` the same way
  `_tracker_set_status` already does.
- `breaker.py`/`epic_autopilot.py` fire-and-forget callers: zero-diff, the widened
  return is simply unused.
- `workflows/archon-dark-factory.yaml`'s `refine-push`/`plan-push-and-advance` nodes:
  the label call becomes `if`/`else`; failure path logs a `WARNING` and upserts a new
  `<!-- df-gate-label-failure -->` marker comment (`|| true`-guarded); the node does
  **not** `exit 1` on this failure path.

## Tech Stack

Python 3 (stdlib `subprocess`/`urllib`), pytest, YAML (PyYAML) for the workflow DAG,
bash for the DAG node bodies. No new dependencies.

## File Structure

| File | Change |
|---|---|
| `scripts/factory_core/providers/tracker/github.py` | `add_label`/`remove_label` → `bool`, capture+print stderr |
| `scripts/factory_core/providers/tracker/base.py` | ABC signatures + docstrings widen to `bool` |
| `scripts/factory_core/providers/tracker/jira.py` | `add_label`/`remove_label` → `return True` |
| `scripts/factory_core/providers/cli.py` | `_tracker_label` checks return values, exits 1 on any failure |
| `workflows/archon-dark-factory.yaml` | `refine-push`/`plan-push-and-advance` nodes gain `if`/`else` + marker comment |
| `tests/test_provider_tracker_parity.py` | extend 2 tests, add 2 new tests |
| `tests/test_provider_tracker_jira.py` | extend 2 tests |
| `tests/test_tracker_contract.py` | extend 1 test |
| `tests/test_provider_cli.py` | add 3 new tests |
| `tests/test_push_gate_dag.py` | add 1 parametrized test |

## Out of Scope

Per the spec's Q&A and Open Questions, explicitly not addressed by this plan (no task
files either — they are follow-up ticket candidates the spec defers, not this ticket's
job to file):
- `board.post_or_update_comment`/`Tracker.upsert_comment`'s swallow-failure bug,
  including the swallowed-GET duplicate-comment hazard at `board.py:87`.
- A scheduler-side reconciliation check ("Refined/Planned + committed artifact + missing
  gate label → re-apply the label") as a durable alternative to this ticket's
  warn-advisory comment.
- The remaining swallow-failure `cli.py` verbs (`_tracker_resolve`, `_codehost_*`).

---

## Task 0: Copy this ticket's spec and plan onto the implementation branch

**Files:** `docs/superpowers/specs/2026-09-04-label-failure-propagation-design.md`,
`docs/superpowers/plans/2026-09-04-label-failure-propagation-implementation.md`

Per the `[PATTERN]` memory lesson (issue #42) — the same Task 0 that #381, #382 and #384's
plans needed, and the transfer the cycle-2 architect review flagged as a "phase-workflow
reminder": the implement phase's `feat/issue-358-...` branch forks from `main`, so this
ticket's own spec and this plan file (both refine-branch-only, not on `main`) do **not**
transfer automatically. Without them, Gate 2 (conformance) falls back to `NO_SPEC=true`
advisory-only review. Copy both files onto the feat branch and commit them before starting
Task 1.

### Steps

1. Copy the two files from the refine branch (name derivation mirrors
   `workflows/archon-dark-factory.yaml`'s `setup-refine-branch` step):

```bash
ISSUE=358
SLUG=$(jq -r '.title // "feature"' "$ARTIFACTS_DIR/issue.json" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g' | head -c 40)
REFINE_BRANCH="refine/issue-${ISSUE}-${SLUG}"
git fetch origin "$REFINE_BRANCH"
git checkout "origin/$REFINE_BRANCH" -- \
  docs/superpowers/specs/2026-09-04-label-failure-propagation-design.md \
  docs/superpowers/plans/2026-09-04-label-failure-propagation-implementation.md
```

   If the computed `REFINE_BRANCH` doesn't exist on origin (slug drift), fall back to:

```bash
git fetch origin
git checkout "origin/$(git branch -r | grep -oE 'origin/refine/issue-358-[a-z0-9-]+' | head -1 | sed 's#origin/##')" -- \
  docs/superpowers/specs/2026-09-04-label-failure-propagation-design.md \
  docs/superpowers/plans/2026-09-04-label-failure-propagation-implementation.md
```

2. Verify both files landed, then commit:

```bash
test -f docs/superpowers/specs/2026-09-04-label-failure-propagation-design.md && \
test -f docs/superpowers/plans/2026-09-04-label-failure-propagation-implementation.md && echo OK
git add docs/superpowers/specs/2026-09-04-label-failure-propagation-design.md \
  docs/superpowers/plans/2026-09-04-label-failure-propagation-implementation.md
git commit -m "docs(#358): copy spec/plan onto the implementation branch"
```

---

## Task 1: `GitHubTracker.add_label`/`remove_label` return `bool`, print stderr on failure

**Files:** `tests/test_provider_tracker_parity.py`, `scripts/factory_core/providers/tracker/github.py`

### Step 1.1 — Write failing tests

Edit `tests/test_provider_tracker_parity.py`. Extend the two existing tests to assert a
`True` return, and add two new failure-path tests immediately after them:

```python
def test_add_label_matches_breaker_trip_to_blocked(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    result = GitHubTracker().add_label("42", "needs-discussion")
    assert calls[0] == [
        "gh", "issue", "edit", "42", "--repo", identity.SLUG,
        "--add-label", "needs-discussion",
    ]
    assert result is True


def test_remove_label_matches_scheduler_advance_path(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (calls.append(cmd), _ok())[1])
    result = GitHubTracker().remove_label("42", "spec-pending-review")
    assert calls[0] == [
        "gh", "issue", "edit", "42", "--repo", identity.SLUG,
        "--remove-label", "spec-pending-review",
    ]
    assert result is True


def test_add_label_returns_false_and_prints_stderr_on_gh_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="rate limited"),
    )
    result = GitHubTracker().add_label("42", "needs-discussion")
    assert result is False
    err = capsys.readouterr().err
    assert "42" in err
    assert "needs-discussion" in err
    assert "rate limited" in err


def test_remove_label_returns_false_and_prints_stderr_on_gh_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="rate limited"),
    )
    result = GitHubTracker().remove_label("42", "spec-pending-review")
    assert result is False
    err = capsys.readouterr().err
    assert "42" in err
    assert "spec-pending-review" in err
    assert "rate limited" in err
```

Also extend the golden-argv opaque-id test block (`GitHubTracker().add_label(opaque_id,
"some-label")` / `remove_label(...)` around line 300) — no assertion change needed there,
just leave it as-is since it only checks argv shape, which is unchanged.

### Step 1.2 — Verify fail

```bash
python -m pytest tests/test_provider_tracker_parity.py -v -k "add_label or remove_label"
```
Expected: the two extended tests fail on `assert result is True` (current methods return
`None`); the two new tests fail with `AttributeError`/assertion errors since stderr is
never printed and the return value is `None`.

### Step 1.3 — Implement

Edit `scripts/factory_core/providers/tracker/github.py:152-162`:

```python
def add_label(self, id: str, name: str) -> bool:
    r = subprocess.run(
        ["gh", "issue", "edit", id, "--repo", identity.SLUG, "--add-label", name],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"github: add-label {name!r} failed for #{id}: {r.stderr.strip()}", file=sys.stderr)
    return r.returncode == 0

def remove_label(self, id: str, name: str) -> bool:
    r = subprocess.run(
        ["gh", "issue", "edit", id, "--repo", identity.SLUG, "--remove-label", name],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"github: remove-label {name!r} failed for #{id}: {r.stderr.strip()}", file=sys.stderr)
    return r.returncode == 0
```

(`sys` is already imported at the top of `github.py`.)

### Step 1.4 — Verify pass

```bash
python -m pytest tests/test_provider_tracker_parity.py -v
```
Expected: all tests in the file pass, including the 4 touched above.

### Step 1.5 — Commit

```bash
git add scripts/factory_core/providers/tracker/github.py tests/test_provider_tracker_parity.py
git commit -m "fix(tracker): GitHubTracker.add_label/remove_label return bool, print gh stderr on failure"
```

---

## Task 2: Widen `Tracker.add_label`/`remove_label` ABC contract to `bool`

**Files:** `scripts/factory_core/providers/tracker/base.py`

No test changes — `tests/test_provider_tracker_base.py` only checks method names are
abstract and that a bare `pass`-bodied override satisfies the ABC, which is unaffected by
a return-type annotation change.

### Step 2.1 — Implement

Edit `scripts/factory_core/providers/tracker/base.py:41-47`:

```python
    @abstractmethod
    def add_label(self, id: str, name: str) -> bool:
        """True iff the label was applied (the underlying gh/API call succeeded).
        False on failure; never raises for a transport failure at this layer
        (JiraTracker's `_request` RuntimeError on HTTP errors is the one
        documented exception, same distinction as `set_status`)."""

    @abstractmethod
    def remove_label(self, id: str, name: str) -> bool:
        """True iff the label was removed; False on failure, never raises for a
        transport failure at this layer (see add_label docstring)."""
```

### Step 2.2 — Verify pass

```bash
python -m pytest tests/test_provider_tracker_base.py tests/test_adapter_authoring_guide.py -v
```
Expected: both files pass unchanged (no assertions reference the return-type annotation).

### Step 2.3 — Commit

```bash
git add scripts/factory_core/providers/tracker/base.py
git commit -m "fix(tracker): widen Tracker.add_label/remove_label ABC contract to bool"
```

---

## Task 3: `JiraTracker.add_label`/`remove_label` return `True` on success

**Files:** `tests/test_provider_tracker_jira.py`, `scripts/factory_core/providers/tracker/jira.py`

### Step 3.1 — Write failing tests

Edit `tests/test_provider_tracker_jira.py`. Extend the two existing tests to assert a
`True` return:

```python
def test_add_label_reads_then_puts_merged_labels(monkeypatch):
    from factory_core.providers.tracker.jira import JiraTracker

    _set_jira_env(monkeypatch)
    tracker = JiraTracker()
    calls = []

    def fake_request(self, method, path, params=None, json_body=None):
        calls.append((method, path, params, json_body))
        if method == "GET":
            return {"fields": {"labels": ["existing-label"]}}
        return {}

    monkeypatch.setattr(JiraTracker, "_request", fake_request)
    result = tracker.add_label("PROJ-1", "needs-discussion")

    get_call, put_call = calls
    assert get_call[:3] == ("GET", "/issue/PROJ-1", {"fields": "labels"})
    assert put_call[0] == "PUT"
    assert put_call[1] == "/issue/PROJ-1"
    assert set(put_call[3]["fields"]["labels"]) == {"existing-label", "needs-discussion"}
    assert result is True


def test_remove_label_reads_then_puts_without_it(monkeypatch):
    from factory_core.providers.tracker.jira import JiraTracker

    _set_jira_env(monkeypatch)
    tracker = JiraTracker()
    calls = []

    def fake_request(self, method, path, params=None, json_body=None):
        calls.append((method, json_body))
        if method == "GET":
            return {"fields": {"labels": ["spec-pending-review", "keep-me"]}}
        return {}

    monkeypatch.setattr(JiraTracker, "_request", fake_request)
    result = tracker.remove_label("PROJ-1", "spec-pending-review")

    _, put_call = calls
    assert put_call[1]["fields"]["labels"] == ["keep-me"]
    assert result is True
```

### Step 3.2 — Verify fail

```bash
python -m pytest tests/test_provider_tracker_jira.py -v -k "add_label or remove_label"
```
Expected: both fail on `assert result is True` (current methods return `None`).

### Step 3.3 — Implement

Edit `scripts/factory_core/providers/tracker/jira.py:174-186`:

```python
    def add_label(self, id: str, name: str) -> bool:
        """Read-then-write the full labels array (Jira Server/DC v2 has no atomic
        add-one-label operation via `fields`). Last writer wins: a label change made by
        another actor between the GET and PUT here is silently overwritten."""
        labels = self._current_labels(id)
        labels.add(name)
        self._request("PUT", f"/issue/{id}", json_body={"fields": {"labels": sorted(labels)}})
        return True

    def remove_label(self, id: str, name: str) -> bool:
        """Same read-then-write, last-writer-wins caveat as `add_label` above."""
        labels = self._current_labels(id)
        labels.discard(name)
        self._request("PUT", f"/issue/{id}", json_body={"fields": {"labels": sorted(labels)}})
        return True
```

### Step 3.4 — Verify pass

```bash
python -m pytest tests/test_provider_tracker_jira.py -v
```
Expected: all tests pass.

### Step 3.5 — Commit

```bash
git add scripts/factory_core/providers/tracker/jira.py tests/test_provider_tracker_jira.py
git commit -m "fix(tracker): JiraTracker.add_label/remove_label return True on success"
```

---

## Task 4: Extend the tracker-contract round-trip test to assert `True` returns

**Files:** `tests/test_tracker_contract.py`

Depends on Tasks 1 and 3 (both trackers must already return `bool`).

### Step 4.1 — Write failing test

Edit `tests/test_tracker_contract.py`, `test_label_add_and_remove_round_trip` (around
line 248):

```python
def test_label_add_and_remove_round_trip(tracker_and_controller):
    tracker, controller = tracker_and_controller
    id1 = "1" if isinstance(tracker, GitHubTracker) else "PROJ-1"
    controller.seed_item(id1, labels=[])

    assert tracker.add_label(id1, "needs-discussion") is True
    assert "needs-discussion" in controller.items[id1]["labels"]

    assert tracker.remove_label(id1, "needs-discussion") is True
    assert "needs-discussion" not in controller.items[id1]["labels"]
```

### Step 4.2 — Regression-lock check (not a red step)

This task adds coverage, not new behavior: Tasks 1 and 3 already landed the
add_label/remove_label → `True` transition and its own red→green TDD cycle. There is no
failing state to reproduce here. Run the test once to confirm it exercises the new
assertions and is green:

```bash
python -m pytest tests/test_tracker_contract.py -v -k test_label_add_and_remove_round_trip
```
Expected: passes immediately for both the GitHub and Jira fixture parametrizations.

### Step 4.3 — Implement

No production code change needed (covered by Tasks 1 and 3). This step is test-only.

### Step 4.4 — Verify pass

```bash
python -m pytest tests/test_tracker_contract.py -v
```
Expected: all tests pass.

### Step 4.5 — Commit

```bash
git add tests/test_tracker_contract.py
git commit -m "test(tracker): assert add_label/remove_label return True in contract round-trip"
```

---

## Task 5: `refine-push`/`plan-push-and-advance` DAG nodes degrade to warn-advisory on label failure

**Files:** `tests/test_push_gate_dag.py`, `workflows/archon-dark-factory.yaml`

Landed before Task 6 (`cli.py`'s `exit 1`) deliberately: today `add_label`/`remove_label`
always return truthy-equivalent (the CLI never exits non-zero), so wrapping the call in
`if`/`else` here is a no-op change in the node's observed behavior — the `if` branch always
taken, `git push` unaffected. Landing `cli.py`'s `exit 1` first would leave a commit where
the DAG node calls a CLI that now exits 1 on failure but the node still runs it
unconditionally; if the node's bash body runs under `errexit`, that turns a label miss
into a failed node (the "loudly strand it" outcome the issue forbids) for the one commit
in between. This ordering never has that intermediate state.

### Step 5.1 — Write failing test

Edit `tests/test_push_gate_dag.py`, add a new test method to the existing
`TestPushGateNodes` class (after `test_node_gates_push_and_label_behind_artifact_check`,
before `test_node_depends_on_and_when_unchanged`):

```python
    def test_node_guards_label_call_and_warns_on_failure(self, node_id, prefix, label, noun):
        bash = _workflow_nodes()[node_id]["bash"]
        guard = f'if python3 "$_PCLI" tracker label --id "$ISSUE" --add {label}'
        assert guard in bash, f"'{node_id}': the gate-label call must be guarded by an if/else"

        marker_call = 'tracker comment --id "$ISSUE" --marker "<!-- df-gate-label-failure -->"'
        assert marker_call in bash, \
            f"'{node_id}' must post the <!-- df-gate-label-failure --> marker comment on label failure"

        # the label-failure branch is the `if`'s else-clause, not the artifact-miss else-clause:
        # it must appear between the guard and that guard's own closing `fi`, and it must not
        # contain the artifact-miss marker (spec R6: the two markers are distinct and unrelated).
        # Match "else"/"fi" as whole stripped lines, not bare substrings — a substring search
        # for "fi" would falsely match inside "marker refinement" (the _FOOTER line), which
        # appears before the real closing fi and would truncate the branch too early.
        lines = bash.split("\n")
        guard_line_idx = next(i for i, l in enumerate(lines) if guard in l)
        else_line_idx = next(
            i for i in range(guard_line_idx, len(lines)) if lines[i].strip() == "else"
        )
        fi_line_idx = next(
            i for i in range(else_line_idx, len(lines)) if lines[i].strip() == "fi"
        )
        label_failure_branch = "\n".join(lines[else_line_idx:fi_line_idx])
        assert "df-gate-label-failure" in label_failure_branch, \
            f"'{node_id}': the label-failure branch must post the df-gate-label-failure marker"
        assert "df-refine-failure" not in label_failure_branch, \
            f"'{node_id}': the label-failure branch must not reuse the df-refine-failure marker " \
            "(that marker means 'no artifact, retry safe' and must not be overloaded)"

        # the label-failure branch warns (log echo) and does not exit 1 (push already succeeded)
        assert "WARNING:" in label_failure_branch, \
            f"'{node_id}': the label-failure branch must log a WARNING echo"
        assert "exit 1" not in label_failure_branch, \
            f"'{node_id}': the label-failure branch must not exit 1 — the push already " \
            "succeeded and is the node's load-bearing side effect"

        # the warn-advisory comment upsert is || true-guarded
        comment_pos = label_failure_branch.index(marker_call)
        comment_line_start = label_failure_branch.rfind("\n", 0, comment_pos) + 1
        comment_line_end = label_failure_branch.find("\n", comment_pos)
        comment_line = label_failure_branch[comment_line_start:comment_line_end]
        assert "|| true" in comment_line, \
            f"'{node_id}': the gate-label-failure marker comment must be || true-guarded"
```

### Step 5.2 — Verify fail

```bash
python -m pytest tests/test_push_gate_dag.py -v -k test_node_guards_label_call_and_warns_on_failure
```
Expected: fails for both parametrized cases — the current node body calls
`tracker label --add ...` unconditionally with no `if`, and `df-gate-label-failure` does
not appear anywhere in the file.

### Step 5.3 — Implement

Edit `workflows/archon-dark-factory.yaml`, `refine-push` node (lines 426-468), replacing
the unconditional label line with an `if`/`else`:

```yaml
  - id: refine-push
    bash: |
      ISSUE=$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")
      BRANCH=$(git branch --show-current)
      _PCLI="${CLONE_DIR:-.}/dark-factory/scripts/factory_core/providers/cli.py"
      _PCLI_FACTORY_CORE="${CLONE_DIR:-.}/dark-factory/scripts/factory_core/cli.py"

      SPEC_FILE=$(bash "${CLONE_DIR:-.}/dark-factory/scripts/push_gate_check.sh" "docs/superpowers/specs/" "$ISSUE")  # TARGET-PATH

      if [ -n "$SPEC_FILE" ]; then
        git push -u origin "$BRANCH"
        if python3 "$_PCLI" tracker label --id "$ISSUE" --add spec-pending-review; then
          echo "Pushed $BRANCH for issue #$ISSUE (spec-pending-review gate applied)"
        else
          echo "WARNING: spec-pending-review failed to apply for #$ISSUE — spec pushed to $BRANCH but gate label missing; check board state manually"
          _FOOTER=$(python3 "$_PCLI_FACTORY_CORE" marker refinement 2>/dev/null || echo "")
          _WARN_BODY="<!-- df-gate-label-failure -->
      ## Refinement Pipeline — Gate Label Missing

      The spec was pushed to \`$BRANCH\` but the \`spec-pending-review\` label failed to apply (likely a transient GitHub API/rate-limit failure). This issue will not auto-advance until the label is applied.

      **Remediation:** \`gh issue edit $ISSUE --add-label spec-pending-review\`

      ---
      ${_FOOTER}"
          TMPFILE=$(mktemp /tmp/gate-label-failure-XXXXXX.md)
          printf '%s' "$_WARN_BODY" > "$TMPFILE"
          python3 "$_PCLI" tracker comment --id "$ISSUE" --marker "<!-- df-gate-label-failure -->" --body-file "$TMPFILE" || true
          rm -f "$TMPFILE"
        fi
      else
        HAS_NEEDS_DISCUSSION=$(python3 "$_PCLI" tracker get --id "$ISSUE" --fields labels \
          | jq -r '.labels[].name' \
          | grep -Fxc 'needs-discussion' || true)
        if [ "$HAS_NEEDS_DISCUSSION" -gt 0 ]; then
          echo "refine-push: no committed spec for issue #$ISSUE, but needs-discussion is already applied — clean abort, skipping silently."
        else
          echo "refine-push: no committed spec found for issue #$ISSUE and no needs-discussion label — treating as silent death."
          _FOOTER=$(python3 "$_PCLI_FACTORY_CORE" marker refinement 2>/dev/null || echo "")
          _FAIL_BODY="<!-- df-refine-failure -->
      ## Refinement Pipeline — Failed

      The refine agent ended without producing a committed spec (\`docs/superpowers/specs/\`) for this issue. No gate label was applied; this item remains eligible for automatic retry.

      \`\`\`bash
      # Retry manually if needed
      docker compose --profile factory run --rm dark-factory \"Refine issue #${ISSUE}\"
      \`\`\`

      ---
      ${_FOOTER}"
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

Apply the identical shape to `plan-push-and-advance` (lines 475-517): substitute
`plan-pending-review` for `spec-pending-review`, `docs/superpowers/plans/` for
`docs/superpowers/specs/`, "plan" for "spec", and `PLAN_FILE` for `SPEC_FILE` throughout.
The `<!-- df-gate-label-failure -->` marker name itself does **not** change — it is the one
shared marker name used by both nodes (spec Requirement 6: "a marker distinct from
`<!-- df-refine-failure -->`", singular, not a per-node marker). The existing
`df-refine-failure` marker on the artifact-miss branch is unchanged on both nodes, since
#212 established it as the shared "no artifact, retry safe" signal for both refine and
plan:

```yaml
  - id: plan-push-and-advance
    bash: |
      ISSUE=$(jq -r '.resolved_number' "$ARTIFACTS_DIR/issue.json")
      BRANCH=$(git branch --show-current)
      _PCLI="${CLONE_DIR:-.}/dark-factory/scripts/factory_core/providers/cli.py"
      _PCLI_FACTORY_CORE="${CLONE_DIR:-.}/dark-factory/scripts/factory_core/cli.py"

      PLAN_FILE=$(bash "${CLONE_DIR:-.}/dark-factory/scripts/push_gate_check.sh" "docs/superpowers/plans/" "$ISSUE")  # TARGET-PATH

      if [ -n "$PLAN_FILE" ]; then
        git push -u origin "$BRANCH"
        if python3 "$_PCLI" tracker label --id "$ISSUE" --add plan-pending-review; then
          echo "Pushed $BRANCH for issue #$ISSUE (plan-pending-review gate applied)"
        else
          echo "WARNING: plan-pending-review failed to apply for #$ISSUE — plan pushed to $BRANCH but gate label missing; check board state manually"
          _FOOTER=$(python3 "$_PCLI_FACTORY_CORE" marker refinement 2>/dev/null || echo "")
          _WARN_BODY="<!-- df-gate-label-failure -->
      ## Refinement Pipeline — Gate Label Missing

      The plan was pushed to \`$BRANCH\` but the \`plan-pending-review\` label failed to apply (likely a transient GitHub API/rate-limit failure). This issue will not auto-advance until the label is applied.

      **Remediation:** \`gh issue edit $ISSUE --add-label plan-pending-review\`

      ---
      ${_FOOTER}"
          TMPFILE=$(mktemp /tmp/gate-label-failure-XXXXXX.md)
          printf '%s' "$_WARN_BODY" > "$TMPFILE"
          python3 "$_PCLI" tracker comment --id "$ISSUE" --marker "<!-- df-gate-label-failure -->" --body-file "$TMPFILE" || true
          rm -f "$TMPFILE"
        fi
      else
        HAS_NEEDS_DISCUSSION=$(python3 "$_PCLI" tracker get --id "$ISSUE" --fields labels \
          | jq -r '.labels[].name' \
          | grep -Fxc 'needs-discussion' || true)
        if [ "$HAS_NEEDS_DISCUSSION" -gt 0 ]; then
          echo "plan-push-and-advance: no committed plan for issue #$ISSUE, but needs-discussion is already applied — clean abort, skipping silently."
        else
          echo "plan-push-and-advance: no committed plan found for issue #$ISSUE and no needs-discussion label — treating as silent death."
          _FOOTER=$(python3 "$_PCLI_FACTORY_CORE" marker refinement 2>/dev/null || echo "")
          _FAIL_BODY="<!-- df-refine-failure -->
      ## Refinement Pipeline — Failed

      The plan agent ended without producing a committed implementation plan (\`docs/superpowers/plans/\`) for this issue. No gate label was applied; this item remains eligible for automatic retry.

      \`\`\`bash
      # Retry manually if needed
      docker compose --profile factory run --rm dark-factory \"Plan issue #${ISSUE}\"
      \`\`\`

      ---
      ${_FOOTER}"
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

### Step 5.4 — Verify pass

```bash
python -m pytest tests/test_push_gate_dag.py -v
```
Expected: all tests pass, including
`test_node_guards_label_call_and_warns_on_failure` for both `node_id` values, and
`test_dag_validator_passes` still passes (the DAG validator doesn't constrain `bash:`
block content beyond YAML well-formedness, per the spec's Assumptions).

### Step 5.5 — Commit

```bash
git add workflows/archon-dark-factory.yaml tests/test_push_gate_dag.py
git commit -m "fix(dag): refine-push/plan-push-and-advance degrade to warn-advisory on gate-label failure"
```

---

## Task 6: `cli.py:_tracker_label` propagates failure, exits 1

**Files:** `tests/test_provider_cli.py`, `scripts/factory_core/providers/cli.py`

Depends on Tasks 1–3 (both tracker implementations must return `bool` for the fakes below
to be representative, though the CLI test itself uses a `_FakeTracker` so it's independently
testable) and Task 5 (lands the DAG-node `if`/`else` guard first — see Task 5's ordering
note).

### Step 6.1 — Write failing tests

Edit `tests/test_provider_cli.py`, add after the `test_tracker_set_status_*` trio
(after line ~313):

```python
def test_tracker_label_exits_0_on_success(monkeypatch):
    import factory_core.providers.cli as cli_mod

    class _FakeTracker:
        def add_label(self, id, name):
            return True
        def remove_label(self, id, name):
            return True
    monkeypatch.setattr(cli_mod, "get_tracker", lambda: _FakeTracker())
    monkeypatch.setattr(
        sys, "argv",
        ["cli.py", "tracker", "label", "--id", "42", "--add", "plan-pending-review"],
    )
    cli_mod.main()  # must not raise / must not SystemExit


def test_tracker_label_prints_error_and_exits_1_on_any_failure(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod

    calls = []

    class _FakeTracker:
        def add_label(self, id, name):
            calls.append(("add", name))
            return name != "spec-pending-review"
        def remove_label(self, id, name):
            calls.append(("remove", name))
            return True
    monkeypatch.setattr(cli_mod, "get_tracker", lambda: _FakeTracker())
    monkeypatch.setattr(
        sys, "argv",
        ["cli.py", "tracker", "label", "--id", "42",
         "--add", "spec-pending-review", "--remove", "needs-discussion"],
    )
    with pytest.raises(SystemExit) as exc:
        cli_mod.main()
    assert exc.value.code == 1
    # both operations were attempted despite the first failing
    assert calls == [("add", "spec-pending-review"), ("remove", "needs-discussion")]
    err = capsys.readouterr().err
    assert "ERROR:" in err
    assert "42" in err


def test_tracker_label_catches_runtime_error_and_exits_1(monkeypatch, capsys):
    import factory_core.providers.cli as cli_mod

    class _FakeTracker:
        def add_label(self, id, name):
            raise RuntimeError("jira: PUT /issue/42 failed (500): boom")
    monkeypatch.setattr(cli_mod, "get_tracker", lambda: _FakeTracker())
    monkeypatch.setattr(
        sys, "argv",
        ["cli.py", "tracker", "label", "--id", "42", "--add", "plan-pending-review"],
    )
    with pytest.raises(SystemExit) as exc:
        cli_mod.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "ERROR: jira: PUT /issue/42 failed (500): boom" in err
```

### Step 6.2 — Verify fail

```bash
python -m pytest tests/test_provider_cli.py -v -k tracker_label
```
Expected: `test_tracker_label_exits_0_on_success` passes trivially (current code never
raises), but `test_tracker_label_prints_error_and_exits_1_on_any_failure` and
`test_tracker_label_catches_runtime_error_and_exits_1` fail — current `_tracker_label`
never checks a return value or exits non-zero.

### Step 6.3 — Implement

Edit `scripts/factory_core/providers/cli.py:58-63`:

```python
def _tracker_label(args):
    tracker = get_tracker()
    ok = True
    try:
        for name in (args.add or []):
            if not tracker.add_label(args.id, name):
                ok = False
        for name in (args.remove or []):
            if not tracker.remove_label(args.id, name):
                ok = False
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    if not ok:
        print(f"ERROR: one or more label operations failed for issue {args.id}", file=sys.stderr)
        sys.exit(1)
```

### Step 6.4 — Verify pass

```bash
python -m pytest tests/test_provider_cli.py -v
```
Expected: all tests pass, including the 3 new ones.

### Step 6.5 — Commit

```bash
git add scripts/factory_core/providers/cli.py tests/test_provider_cli.py
git commit -m "fix(cli): _tracker_label attempts every add/remove, exits 1 on any failure"
```

---

## Task 7: Full suite + smoke gate

### Step 7.1 — Run

```bash
python -m pytest tests/ -v
bash smoke_gate.sh
```

Expected: full green. `breaker.py`/`epic_autopilot.py` are untouched (Requirement 5,
zero-diff) — no test changes expected there; confirm with:

```bash
git diff --stat origin/main -- scripts/factory_core/breaker.py scripts/factory_core/epic_autopilot.py
```
Expected: empty output (no diff).

### Step 7.2 — Commit (only if smoke_gate.sh or full-suite run surfaced fixups)

If Step 7.1 is fully green with no code changes needed, skip this step — nothing to commit.
