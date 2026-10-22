# Verify the push before applying spec/plan-pending-review

**Issue:** #473
**Revised:** 2026-10-21 (operator amendment after spec gate — see review findings F1–F8)

---

## Overview / Problem Statement

`workflows/archon-dark-factory.yaml`'s `refine-push` and `plan-push-and-advance` DAG nodes each
run, once `push_gate_check.sh` confirms a committed spec/plan artifact exists on the branch:

```bash
git push -u origin "$BRANCH"
if python3 "$_PCLI" tracker label --id "$ISSUE" --add spec-pending-review; then
  ...
```

`git push`'s exit status is never checked — no `set -e` is active in this bash block, so control
falls straight through to the label call regardless of whether the push succeeded. A detached HEAD
(`$BRANCH` empty), a rejected push, or a transient network failure all silently produce the same
outcome as success: the gate label is applied, the "Refinement Pipeline — Spec Generated" comment
has already been posted by the agent phase, and the artifact itself never reaches the remote. This
is exactly what happened on markethawk #872 (run `a7829d5f`): a 1.9s `refine-push` run applied
`spec-pending-review` to a branch that returns 404 on GitHub, stranding the ticket behind a gate
label with no reviewable artifact once the container (and its only copy of the spec) was gone.

`push_gate_check.sh` already closes a different, independent hole (#212): it verifies a real
artifact was committed on the local branch before the push+label step runs at all. The push itself
was not addressed by #212 (whose explicit deferrals were the grace-timer marker, the `push-and-pr`
node — #208 — and the executor bug); this ticket adds the second, complementary guarantee: that the
committed branch actually reached the remote before the label — the promise the label makes to a
human reviewer — is applied.

## Requirements

Distilled from the issue's acceptance criteria and the Q&A below:

1. An empty `$BRANCH` (detached HEAD) must fail closed: no push attempt, no label, and the failure
   must reach the issue as a marker comment — never silently fall through to `git push`'s own
   generic stderr.
2. `git push -u origin "$BRANCH"`'s exit status must gate the label call. On a non-zero exit, the
   push's captured stderr reaches the issue via a new `<!-- df-push-failure -->` marker comment,
   and the gate label is never applied.
3. Even on a zero exit, the label is applied only after independently verifying the remote actually
   carries the pushed commit: `git ls-remote --exit-code --heads origin "refs/heads/$BRANCH"`'s
   reported sha must equal `git rev-parse HEAD`. A mismatch (or a non-zero `ls-remote` exit) is
   treated exactly like a push failure (`df-push-failure`, no label).
4. The push outcome (`pushed: true/false`, branch, local/remote sha, captured error) is persisted to
   `$ARTIFACTS_DIR/push-result.json`, folded into `run-record.json` as `push_result`, and surfaced
   in the cost-report comment (`scripts/factory_core/cost_report.py::render`) as one absent-tolerant
   line when `pushed` is `false`. `push-result.json` is written immediately after `PUSH_OK` is
   decided and **before** `post_marker_comment.sh` runs, so a comment failure or a node timeout
   still leaves the outcome in the run record.
5. The existing successful path is unchanged: once remote sha == local HEAD, the label is applied
   exactly as today (including the existing `df-gate-label-failure` advisory path if the *label
   API call itself* fails after a verified-good push).
6. The three marker comments stay semantically distinct and non-overlapping (confirmed by the
   existing `tests/test_push_gate_dag.py` assertions for the first two):
   - `<!-- df-refine-failure -->` — no committed artifact at all; retry-safe, no label. Unchanged.
   - `<!-- df-gate-label-failure -->` — artifact verified on the remote, but the label API call
     itself failed (e.g. rate limit). Unchanged.
   - `<!-- df-push-failure -->` (new) — artifact exists locally but never verifiably reached the
     remote (empty branch, failed push, or sha mismatch). No label; this is the gap this ticket
     closes.
   - `<!-- df-push-gate-failure -->` (pre-existing, unrelated) — posted by
     `scripts/verdict_gate_check.sh:70` on the `push-and-pr` verdict gate when a gate verdict file
     is missing. It is not touched by this ticket and must not be confused with `df-push-failure`;
     the static test asserts the new marker string exactly (`<!-- df-push-failure -->`), not a
     substring that would also match the older marker.
7. A new, *executing* test (not a static string/ordering assertion) proves the fix: with `git push`
   made to fail, no `tracker label` call is logged and the `df-push-failure` marker is posted.
8. The node exits 0 on push failure (parity with the `df-refine-failure` path: the item stays
   retry-eligible, and the push-failure branch is `|| true`-guarded like the existing advisory
   paths). Stated honestly: completed runs write no error signature (`entrypoint.sh`'s
   `_write_error_signature` fires only on failed runs), so the scheduler's same-signature breaker
   does not see a persistent push failure and only `REFINE_MAX_RETRIES` bounds the re-dispatch
   cost. A persistent-failure signal is explicitly out of scope for this ticket.

## Architecture / Approach

### 1. Both DAG nodes gain an inner push-verification block

Inside the existing `if [ -n "$SPEC_FILE" ]; then` / `if [ -n "$PLAN_FILE" ]; then` branch (i.e.
strictly *after* `push_gate_check.sh` has already confirmed a real artifact — the no-artifact path,
including its `needs-discussion` silent-skip, is untouched):

```bash
if [ -z "$BRANCH" ]; then
  PUSH_OK=false
  PUSH_ERR="detached HEAD: no branch name to push (git branch --show-current returned empty)"
else
  PUSH_ERR=$(timeout 30 git push -u origin "$BRANCH" 2>&1)
  PUSH_RC=$?
  if [ "$PUSH_RC" -eq 124 ]; then
    PUSH_OK=false
    PUSH_ERR="push timed out after 30s"
  elif [ "$PUSH_RC" -eq 0 ]; then
    REMOTE_SHA=$(timeout 10 git ls-remote --exit-code --heads origin "refs/heads/$BRANCH" 2>/dev/null \
      | awk -v r="refs/heads/$BRANCH" '$2==r{print $1; exit}')
    LOCAL_SHA=$(git rev-parse HEAD)
    if [ -n "$REMOTE_SHA" ] && [ "$REMOTE_SHA" = "$LOCAL_SHA" ]; then
      PUSH_OK=true
    else
      PUSH_OK=false
      PUSH_ERR="post-push verification failed: origin/$BRANCH is ${REMOTE_SHA:-<unreachable>}, expected $LOCAL_SHA"
    fi
  else
    PUSH_OK=false
  fi
fi
```

Both network calls are bounded with coreutils `timeout` (present in the run image): the push-failure
path fires exactly when the network is bad, and an unbounded `git push` against an unreachable host
can block past the node's timeout — the runner would then kill the node with no comment, no
`push-result.json` and no label (fail-closed, but silent). `timeout` exit 124 on the push becomes
`PUSH_ERR="push timed out after 30s"`; on `ls-remote` it leaves `REMOTE_SHA` empty, which the
existing `${REMOTE_SHA:-<unreachable>}` wording already reports. The exact-ref pattern plus
`$2==r` match makes `REMOTE_SHA` single-line by construction (see Assumptions).

Capture/cap/fence discipline (this is the first marker-comment body to embed external command
output, per the Q&A below): truncate `PUSH_ERR` with `tail -n 20 | tail -c 2000` before use, and
wrap it in a `~~~`-fenced block (not `` ``` ``) inside the comment body so a stray triple-backtick in
git's own output can't break the comment's markdown rendering. Direct interpolation of the
(truncated) captured text into `post_marker_comment.sh`'s double-quoted body-string argument is
shell-safe as-is — bash does not re-evaluate `` ` ``/`$()` sequences already present inside an
already-expanded variable's value, and `post_marker_comment.sh` writes the body via `printf '%s'`
into a temp file passed as `--body-file`, with no further shell re-parsing.

Then:

```bash
if [ "$PUSH_OK" = true ]; then
  if python3 "$_PCLI" tracker label --id "$ISSUE" --add spec-pending-review; then
    ...   # unchanged success path
  else
    ...   # unchanged df-gate-label-failure path
  fi
else
  echo "refine-push: push verification failed for #$ISSUE — no gate label applied."
  bash ".../post_marker_comment.sh" "$ISSUE" "<!-- df-push-failure -->" \
    "## Refinement Pipeline — Push Failed

The spec was committed locally but could not be verified on the remote (\`$BRANCH\`), so the
\`spec-pending-review\` label was **not** applied. This item remains eligible for automatic retry.

~~~
$PUSH_ERR
~~~

**Remediation:** investigate the push failure above, then retry:
\`\`\`bash
docker compose --profile factory run --rm dark-factory \"Refine issue #${ISSUE}\"
\`\`\`" || true
fi
```

The `df-push-failure` marker always posts regardless of `needs-discussion` — unlike the no-artifact
branch, a real artifact exists here and is actively being lost with the container; the
`needs-discussion`-gated silence that's appropriate for "nothing to review yet" does not apply to
"something was built and is about to be silently dropped."

`plan-push-and-advance` gets the identical treatment (`PLAN_FILE`/`plan-pending-review`/"plan"
wording), keeping the two nodes structurally parallel as they are today.

Ordering and exit code (Requirements 4 and 8): `push-result.json` is written as soon as `PUSH_OK` is
decided — before `post_marker_comment.sh` is invoked on the failure path and before the label call on
the success path — and the push-failure branch ends with `|| true`, so the node exits 0 either way.
The node timeout stays at 60000 ms (`tests/test_push_gate_dag.py::test_node_depends_on_and_when_unchanged`
asserts it); the budget fits: 30 s push cap + 10 s `ls-remote` cap + one marker comment (the same
`marker scheduler` + `tracker comment` upsert the existing `df-gate-label-failure` path was sized
for).

### 2. Push-result persistence (`push-result.json` -> `run_record["push_result"]`)

Each node writes `$ARTIFACTS_DIR/push-result.json` unconditionally (both the success and failure
paths) with:

```json
{
  "node": "refine-push",
  "branch": "refine/issue-473-...",
  "pushed": true,
  "local_sha": "...",
  "remote_sha": "...",
  "error": ""
}
```

`scripts/factory_core/run_record.py`'s `cmd_assemble` reads this file, if present, exactly the way
it already optionally loads `memory-trace.json` into `run_record["memory_trace"]` (existing code,
~line 686-691) — a direct, schema-free JSON fold, added as `run_record["push_result"]`. This is
deliberately **not** added to `cmd_assemble`'s `artifact_names` list or routed through the
STATUS/GATE_TYPE/FINDINGS_COUNT/SEVERITY verdict schema (`scripts/factory_core/verdict.py`,
`scripts/gate_lib.sh::emit_verdict`): `refine-push`/`plan-push-and-advance` are plain DAG bash
steps with no agent verdict to parse, not gate-verdict stages like validation/conformance/review.

`scripts/factory_core/cost_report.py::render` reads named keys only (`status`, `totals`, `nodes`,
`harness_economics`, `archon_cost_capture`) — it does not render arbitrary top-level record keys, and
`memory_trace` has no reader at all — so an explicit render line is required for the push outcome to
reach the issue. `render` gains one absent-tolerant line, in the style of `format_economics_line`:
when `run_record.get("push_result", {}).get("pushed") is False`, emit
`**Push:** FAILED — <error, first line, capped>`; print nothing when the key is absent (older run
records) or `pushed` is `true`. This puts the push outcome on the issue through a second, independent
comment path (the cost-report comment is posted by `entrypoint.sh` at end of run, not by the DAG
node), so a failed `post_marker_comment.sh` call still leaves a visible trace.

The operator one-liner view (`.claude/skills/operator/scripts/run_summary.py`) is **not** touched:
`.claude/skills/` is a `safety.hard_exclude_paths` entry in `.factory/adapter.yaml` (human-reviewed
surface). See "Operator follow-up" below.

### 3. Test: execute the real bash, not another static assertion

`tests/test_push_gate_dag.py` is a static test — it `yaml.safe_load`s the workflow file and asserts
string/ordering properties of the `bash:` text, which is exactly the style of check that missed
this bug (the current text already "contains" `git push` before the label call; order alone doesn't
prove the exit status is checked). The fix needs behavioral coverage instead.

New file: `tests/test_push_gate_dag_exec.py`. Reuses two precedents already in the repo rather than
inventing new test infrastructure:

- The bare-origin + working-tree git fixture from `tests/test_push_gate_check.py` (`tmp_path/bare`,
  `tmp_path/work`), so `push_gate_check.sh`, `git push`, and `git ls-remote` all run for real against
  a real (local) remote.
- The `python3` shell-function override from `tests/test_scheduler_comment_verdict.py` (lines
  29-35): `python3() { case "$*" in *providers/cli.py*) return 0 ;; *) command python3 "$@" ;; esac; }; export -f python3`.
  Extended here to additionally append every `providers/cli.py` invocation's argv (`tracker label`,
  `tracker comment`, and `tracker get` alike — the override pattern must cover every subcommand the
  node can reach) to a log file under `tmp_path`, so assertions can check exactly which calls were
  (or weren't) made, without needing a real GitHub token or a fake `python3` binary on `PATH`.

Extract `node["bash"]` for `refine-push`/`plan-push-and-advance` from the parsed YAML (same
`_workflow_nodes()` helper `test_push_gate_dag.py` already defines) and run it with
`subprocess.run(["bash", "-c", bash_text], cwd=work, env={...})`.

Parametrize both nodes over four cases:

| Case | Setup | Expected |
|---|---|---|
| Push fails | point `origin` at a missing/unwritable path, or install a `pre-receive` hook on the bare repo that exits 1 | no `tracker label` call logged; `df-push-failure` posted; `push-result.json` has `pushed: false` |
| Detached HEAD | `git checkout --detach` before running the node's bash | same as above, with the detached-HEAD error text |
| Remote sha mismatch | install `bare/hooks/post-update` that `git update-ref`s the branch to a different, pre-existing commit after the push is accepted — the push exits 0, `ls-remote` reports the other sha | same as above, with both shas shown |
| Push succeeds | ordinary fixture, real push against the bare origin | `tracker label --add spec-pending-review`/`plan-pending-review` call logged exactly once; no `df-push-failure`; `push-result.json` has `pushed: true` and matching shas |

Force push failure via the remote (missing path / pre-receive hook), not by shadowing the `git`
binary itself — this keeps the test exercising the real `git push`/`git ls-remote` exit-status and
sha-comparison logic rather than a mocked stand-in for it. The sha-mismatch case cannot be set up
with an out-of-band divergent commit on the bare origin: that makes the node's own `git push` get
rejected as non-fast-forward (rc=1), which exercises the push-failure path a second time and leaves
the `REMOTE_SHA`/`LOCAL_SHA` comparison untested — hence the `post-update` hook, which lets the push
succeed and then moves the ref.

#### Test harness details

- **One `bash -c` script.** The prelude (the `python3` override, `export -f python3`, and the argv
  logging) is concatenated with the extracted node bash into a single string passed to
  `subprocess.run(["bash", "-c", prelude + bash_text], ...)`. An exported shell function is not
  inherited through `env=` from Python; it is visible only to child shells of the shell that defined
  it — which is exactly what `bash .../post_marker_comment.sh` and `bash .../push_gate_check.sh` are.
- **`CLONE_DIR` layout.** The node resolves scripts as `"${CLONE_DIR:-.}/dark-factory/scripts/..."`.
  Create `tmp_path/clone/dark-factory` as a symlink to the repo root (or a copy of the needed
  scripts) and pass `CLONE_DIR=tmp_path/clone`; relying on `dirname(REPO_ROOT)` (as some `.sh`
  tests do) only works when the checkout's basename is `dark-factory`, which a worktree is not.
- **Node inputs.** The node reads `$ARTIFACTS_DIR/issue.json` (`.resolved_number`) via `jq`, so the
  fixture writes that file and the test is Linux/CI-only: `pytest.skip` with a clear reason when
  `jq` (or `bash`) is absent, rather than failing on a Windows host.
- **No `set -u` in the prelude.** The existing `df-gate-label-failure` body references
  `$FACTORY_REPO_SLUG`, which is unset in the test; either omit `set -u` from the prelude (the
  precedent's `set -uo pipefail` is not copied) or export a dummy `FACTORY_REPO_SLUG`.
- **Override coverage.** The `python3` override's `case` pattern must intercept every
  `providers/cli.py` subcommand the node can reach — `tracker label`, `tracker comment` and
  `tracker get` — so no case can accidentally hit a real tracker call.

`tests/test_push_gate_dag.py` stays purely static but gains a couple of cheap additional assertions:
the exact `<!-- df-push-failure -->` marker string is present in both nodes' bash (an exact match,
so the pre-existing `df-push-gate-failure` marker cannot satisfy it), and `git ls-remote` appears
before the label call.

Two further `.py` tests complete the list:

- `tests/test_run_record.py`: `cmd_assemble` picks up `push-result.json` into
  `run_record["push_result"]`, mirroring `test_assemble_picks_up_memory_trace` (~line 1089), plus the
  absent-file case (no `push_result` key, nothing else changed).
- `tests/test_cost_report.py`: `render` emits the `**Push:** FAILED — ...` line when `pushed` is
  `false`, and emits nothing extra when `pushed` is `true` or the key is absent.

All new/changed `.py` test files are automatically picked up by CI's `python -m pytest tests/ -v`
(CLAUDE.md Conventions) — no separate CI wiring step is needed (unlike a `.sh` file, which would
require an explicit addition to `.github/workflows/ci.yml`'s named-file list).

## Alternatives Considered

1. **Route push-result through the existing verdict/`artifact_names` machinery** (validation/
   conformance/review's STATUS/GATE_TYPE schema). Rejected: these nodes have no agent verdict to
   report; forcing a bash-only push outcome through a schema designed for agent-verdict gates adds
   indirection without benefit, and risks conflating "the agent had an opinion" with "a mechanical
   step succeeded or failed." The lightweight `memory-trace.json`-style direct-fold precedent
   already exists for exactly this shape of data.
2. **Check `$BRANCH` emptiness ahead of `push_gate_check.sh`** (short-circuit before the artifact
   check even runs). Rejected: `push_gate_check.sh` is called with `REF=HEAD` by default and
   resolves correctly regardless of branch name, so the no-artifact path (including its
   `needs-discussion` silent-skip) must stay exactly as-is; moving the branch-emptiness check earlier
   would risk silently skipping a real, already-committed artifact under `needs-discussion` instead
   of surfacing it via the new `df-push-failure` marker.
3. **Shadow/stub the `git` binary itself in the new test** instead of forcing failure through a real
   (if degenerate) remote. Rejected: stubbing `git` would only prove the bash branches on *some*
   exit code, not that the actual `git push`/`git ls-remote` semantics this fix depends on
   (exit-status propagation, sha comparison against a real ref) work end-to-end.
4. **Surface the push outcome in the operator one-liner (`.claude/skills/operator/scripts/run_summary.py`
   or `runs.sh`)** instead of the cost report. Rejected: `.claude/skills/` is a
   `safety.hard_exclude_paths` entry in `.factory/adapter.yaml` — a human-reviewed surface that a
   factory-authored PR must not touch — whereas `scripts/factory_core/cost_report.py` is in scope and
   gives the push outcome a second, independent comment path on the issue itself (posted by
   `entrypoint.sh` at end of run, not by the DAG node), which also survives a failed
   `post_marker_comment.sh` call. The issue's fix item 3 names the cost report explicitly.

## Operator follow-up (out of scope)

- A 3-line `push=FAILED` token in `.claude/skills/operator/scripts/run_summary.py::records()` (the
  one-liner view behind `runs.sh recent` / `runs.sh issue N`, `runs.sh` lines 20-21) would make the
  outcome visible there too. That path is human-only (`hard_exclude_paths`); it is a hand edit for the
  operator, not part of this ticket.

## Assumptions (flagged)

- `git ls-remote --heads` patterns tail-match whole path components, so a bare `"$BRANCH"` pattern
  could in principle also match `refs/heads/<anything>/$BRANCH`. The block queries the full ref
  (`"refs/heads/$BRANCH"`) and selects with `awk -v r="refs/heads/$BRANCH" '$2==r{print $1; exit}'`,
  so `REMOTE_SHA` is a single 40-char sha (or empty) by construction — no multi-line edge case is left
  for implement to confirm.
- No node-level `set -e`/`set -uo pipefail` prologue is added to either DAG node's bash block as part
  of this fix — the existing explicit `$?`/`PUSH_RC` capture pattern is kept consistent with the rest
  of the file's un-set-e'd style, rather than introducing a new shell-option convention scoped to
  just these two nodes.
