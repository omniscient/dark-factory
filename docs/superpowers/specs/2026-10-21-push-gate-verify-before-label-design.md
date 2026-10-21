# Verify the push before applying spec/plan-pending-review

**Issue:** #473

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
artifact was committed on the local branch before the push+label step runs at all. This ticket adds
the second, complementary guarantee that same effort intentionally left for a dedicated ticket: that
the committed branch actually reached the remote before the label — the promise the label makes to
a human reviewer — is applied.

## Requirements

Distilled from the issue's acceptance criteria and the Q&A below:

1. An empty `$BRANCH` (detached HEAD) must fail closed: no push attempt, no label, and the failure
   must reach the issue as a marker comment — never silently fall through to `git push`'s own
   generic stderr.
2. `git push -u origin "$BRANCH"`'s exit status must gate the label call. On a non-zero exit, the
   push's captured stderr reaches the issue via a new `<!-- df-push-failure -->` marker comment,
   and the gate label is never applied.
3. Even on a zero exit, the label is applied only after independently verifying the remote actually
   carries the pushed commit: `git ls-remote --exit-code --heads origin "$BRANCH"`'s reported sha
   must equal `git rev-parse HEAD`. A mismatch (or a non-zero `ls-remote` exit) is treated exactly
   like a push failure (`df-push-failure`, no label).
4. The push outcome (`pushed: true/false`, branch, local/remote sha, captured error) is persisted to
   `$ARTIFACTS_DIR/push-result.json` and folded into `run-record.json`, and surfaced in the
   operator `runs.sh` one-line view.
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
7. A new, *executing* test (not a static string/ordering assertion) proves the fix: with `git push`
   made to fail, no `tracker label` call is logged and the `df-push-failure` marker is posted.

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
  PUSH_ERR=$(git push -u origin "$BRANCH" 2>&1)
  PUSH_RC=$?
  if [ "$PUSH_RC" -eq 0 ]; then
    REMOTE_SHA=$(git ls-remote --exit-code --heads origin "$BRANCH" 2>/dev/null | awk '{print $1}')
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

`.claude/skills/operator/scripts/run_summary.py`'s `records()` (the one-liner view backing both
`runs.sh recent` and `runs.sh issue N`, confirmed at `runs.sh` lines 20-21) gets one small addition:
when `d.get("push_result")` is present and `pushed` is `false`, append a `push=FAILED` token to the
printed line; print nothing extra when the key is absent, so existing/older run records are
unaffected. `runs.sh` itself needs no changes — it already pipes through `run_summary.py`.

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
  Extended here to additionally append each `tracker label`/`tracker comment` invocation's argv to a
  log file under `tmp_path`, so assertions can check exactly which calls were (or weren't) made,
  without needing a real GitHub token or a fake `python3` binary on `PATH`.

Extract `node["bash"]` for `refine-push`/`plan-push-and-advance` from the parsed YAML (same
`_workflow_nodes()` helper `test_push_gate_dag.py` already defines) and run it with
`subprocess.run(["bash", "-c", bash_text], cwd=work, env={...})`.

Parametrize both nodes over four cases:

| Case | Setup | Expected |
|---|---|---|
| Push fails | point `origin` at a missing/unwritable path, or install a `pre-receive` hook on the bare repo that exits 1 | no `tracker label` call logged; `df-push-failure` posted; `push-result.json` has `pushed: false` |
| Detached HEAD | `git checkout --detach` before running the node's bash | same as above, with the detached-HEAD error text |
| Remote sha mismatch | push a *different* commit to the same branch name on the bare origin out-of-band before running the node | same as above, with both shas shown |
| Push succeeds | ordinary fixture, real push against the bare origin | `tracker label --add spec-pending-review`/`plan-pending-review` call logged exactly once; no `df-push-failure`; `push-result.json` has `pushed: true` and matching shas |

Force push failure via the remote (missing path / pre-receive hook), not by shadowing the `git`
binary itself — this keeps the test exercising the real `git push`/`git ls-remote` exit-status and
sha-comparison logic rather than a mocked stand-in for it.

`tests/test_push_gate_dag.py` stays purely static but gains a couple of cheap additional assertions:
the `df-push-failure` marker string is present in both nodes' bash, and `git ls-remote` appears
before the label call.

Both new/changed `.py` test files are automatically picked up by CI's `python -m pytest tests/ -v`
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
4. **Extend `runs.sh` directly** instead of `run_summary.py`. Rejected: both `runs.sh recent` and
   `runs.sh issue N` already pipe through `run_summary.py records` (verified directly), so a
   `runs.sh`-level change would be redundant with, and could drift from, a `run_summary.py`-level one.

## Open Questions (non-blocking)

- Whether the cost-report comment (a separate consumer mentioned alongside `runs.sh` in the issue's
  fix item 3) needs its own explicit change, or whether it already renders arbitrary `run-record.json`
  top-level keys generically, is left to the implement phase to confirm against
  `scripts/factory_core/cli.py marker`/cost-report rendering code — no evidence surfaced during
  refinement that it does anything *other* than generically render whatever's in the record.

## Assumptions (flagged)

- `git ls-remote --exit-code --heads origin "$BRANCH" | awk '{print $1}'` reliably yields a single
  40-char sha for a normal (non-ambiguous) branch name; implement should confirm no `awk` edge case
  (e.g. a branch name that's also a prefix of another ref) produces multiple lines.
- No node-level `set -e`/`set -uo pipefail` prologue is added to either DAG node's bash block as part
  of this fix — the existing explicit `$?`/`PUSH_RC` capture pattern is kept consistent with the rest
  of the file's un-set-e'd style, rather than introducing a new shell-option convention scoped to
  just these two nodes.
