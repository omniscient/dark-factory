# Operator playbook

Procedures in the order a ticket meets them. `S=.claude/skills/operator/scripts`. Every write
script reads back and fails loudly; a script error means stop and look, not retry.

## How the factory moves a ticket

```
Backlog + ready-for-agent ──Refine──▶ spec on refine/issue-N-* + spec-pending-review
   (approve: board→Refined, drop label)
Refined ──Plan──▶ plan on the same branch + plan-pending-review   [S/L + direct-to-pr: grace auto-advance]
   (approve: board→Ready, drop label)
Ready ──Fix──▶ feat/issue-N-* → conformance (Gate 2) → code review (Gate 3) → DRAFT PR → In Review
   (operator: review, gh pr ready, merge)  [direct-to-pr: end-gate auto-merge]
Blocked ◀── any failure / orphan sweep; stage_blocked_retry re-dispatches (max 3) unless needs-discussion
```

The scheduler reads the **board**, never the issue list. It dispatches on board status alone;
gate labels are the only skip. `needs-discussion` halts everything for that ticket, including
retries and rescue. `Depends on: #N` lines gate implement dispatch only.

## Opt a ticket in

```
bash $S/usage.sh                      # both windows; factory window from its own sentinel
bash $S/optin.sh N [--direct-to-pr]   # board membership, Backlog, deps, stale refine branch, labels
```
- Pace: one refine ≈ 8-10% of the factory window, a full cycle ≈ 50%, two parallel runs drain
  it in about an hour. During a plan run, opt in nothing else.
- `direct-to-pr` only for S/L tickets you are willing to have auto-advance and auto-merge.
- M tickets titled `refactor|migration|perf|architectur…` park `above-ceiling` (trust
  `is_above_ceiling()` in scheduler.sh, not the config comment). XL always parks.
- Issues filed via REST or by phase commands are **not on the board**; `optin.sh` adds them.
- Do not comment on a ticket you want to leave parked at a gate: a newer comment re-triggers
  the phase ("Re-running with new feedback") and reuses the existing refine branch.

## Gate review (spec or plan)

1. Confirm the artifact exists: `git fetch origin 'refs/heads/refine/issue-N-*'`, list
   `docs/superpowers/specs|plans` on it. A gate label with no artifact is a stranded ticket:
   delete the label, let the phase re-run.
2. Read it yourself in a fresh worktree, then **`git reset --hard origin/<branch>`** in that
   worktree (a stale local branch ref silently shows your own earlier commit).
3. Launch a read-only reviewer subagent (REVIEWERS.md). For a plan, ask it to execute the plan's
   own commands/tests on a scratch copy. Run a citation fact-check for specs that quote code.
4. Verify every claim you are about to promote into an amendment against `origin/main` in the
   same breath: symbols exist, shell variables have an actual `=` assignment, labels are real.
5. Amend on the refine branch (REVIEWERS.md "Amendments"), push, note the SHA.
6. Approve — board first, label second, verified:
   ```
   bash $S/approve.sh spec N --sha <amend-sha>
   bash $S/approve.sh plan N --sha <amend-sha> [--lift-discussion]
   ```
   `--lift-discussion` for plans parked by the architect 3-cycle cap (both labels set).
7. Reject-and-re-refine instead: post the disposition, **append the constraint to the issue
   body** (authoritative input; an operator comment is not a trusted signature), remove the gate
   label, keep `ready-for-agent` and Backlog.
8. Spike tickets (spec is the deliverable): amend → `git mv` the spec to `docs/archive/` → PR
   "Closes #N" from the refine branch → merge → board Done → drop both labels → disposition.

## PR review and merge

- Factory PRs are **drafts**. A Gate-3 block leaves a draft PR; a validate-stage block leaves
  none until push-and-pr runs (it always does — see "Blast-radius block").
- Check `gh pr view N --json mergeStateStatus` first: `DIRTY` means GitHub ran **no CI at all**.
  Resolve by merging main into the feat branch in a worktree.
- Review = independent reviewer standing in for Gates 2+3 (REVIEWERS.md "PR"), plus your own
  read of any `commands/*.md` diff (Gate 3 has had blind spots there).
- Hotspot files (`scripts/factory_core/{adapter,verdict,verifier,breaker,run_record}.py`,
  `gate_*.py`, `.github/workflows/ci.yml`, `.claude/**`, hooks) put the PR on the
  **operator-review path**: your review is the merge gate; write the disposition on the issue.
- Fixes on the branch: operator commit citing the finding. Out-of-scope root causes become
  their own tickets with the reviewer's traces; the disposition maps every finding to
  fixed-here / deferred-to-#N.
- Merge: `gh pr ready N && gh pr merge N --merge --delete-branch`. Post disposition comments
  with `gh issue comment` **before** anything closes the issue (`Closes #N` + a later
  `gh issue close --comment` silently drops the comment).
- Never open a second PR from a branch the factory may still push to (squash bases diverge and
  renames degrade into add-without-delete; that was PR #421's cleanup).

## Close a ticket

Wait for `docker ps` to show no run container for N, then: board Done (`board.sh set N Done`),
drop `ready-for-agent`/`direct-to-pr`/`needs-discussion`, delete the refine branch, disposition
comment with follow-ups. Verify the board **after** the run exits; the run's own board write
lands after yours.

## Blast-radius block (`needs-discussion` + "Blast-Radius Gate — BLOCKED")

Traced end to end (2026-09-10): validate's `exit 1` does not stop the DAG; the run continues
through conformance, code review, archive and push-and-pr and creates a draft PR. Enforcement is
the close/auto-merge node, which refuses while `needs-discussion` is present. So: **do nothing
but review**. Review the PR, post the sign-off on the issue, remove `needs-discussion`, then
merge or dispatch `Close issue #N`. Never hand-create a PR, never re-run Validate (the gate is
stateless; #374).

## Recovery

| Symptom | Read | Do |
|---|---|---|
| Gate label + "Failed" comment contradicting "Generated" | branch contents | believe the branch; artifact without `#N` in body/filename = push gate false negative (#382) |
| "Plan Generated" + pushed plan + NO gate label + repeated "Starting plan" | API budget | hand-apply the gate label (label writes swallow gh failures under exhaustion) |
| Ticket "never dispatches" | `gh issue view N --json projectItems` | `[]` = off-board; `board.sh add N`. Then labels, then breaker counters |
| Continue runs bouncing, PR already pushed | `runs.sh issue N` | `needs-discussion` + `docker stop` the Continue + evidence comment; open/merge by hand |
| Run died at push-and-pr (GraphQL blocked) | scheduler `graphql=N/5000` (used/limit) | open the PR by hand from the finished branch; retries re-implement from scratch |
| Conformance `STATUS=missing` / verdict never persisted | run record nodes | factory glitch (#373), not a finding; draft-PR + continue recipe |
| Pause comment "resumes at" exactly +30 min | run logs | false pause = hidden real failure (fixed by #344; still check) |
| Ledger quiet, no paused rows | `usage.sh` ownership line | `runs.jsonl` root-owned → chown factory; never root-shell into the state volume |
| `df-factory-failure` unseen for hours | your monitor pattern | case-insensitive `fail\|blocked\|breaker\|trip\|rescue\|orphan`; `gh issue list` needs `--limit` |

Diagnosing a failing phase for real: run the same dispatch **attached** when the factory is
idle (the entrypoint WIP guard vetoes otherwise, before any pipeline runs):
```
MSYS_NO_PATHCONV=1 docker exec dark-factory-self-scheduler docker compose -f /opt/dark-factory/docker-compose.yml --profile factory run --rm dark-factory "Refine issue #N" > diag.log 2>&1
```
Judge by `dag_workflow_finished anyFailed:false`, not the docker exit code (attached runs can
exit 125 on a recovered EOF). Pull transcripts from a live container with `runs.sh transcript`.

## Window pauses and usage breaks

- The factory pauses itself (`session-window-paused` sentinel, `session_window_gate=active`,
  `stage: paused` rows). Pauses do not consume retries (#341). Gate-parked tickets do not
  self-resume after a pause if `needs-discussion` is on; act the same hour.
- Operator pause for a usage break: `docker pause dark-factory-self-scheduler` (and
  `dark-factory-scheduler`); in-flight run containers keep going; `docker exec` fails while
  paused. `docker unpause` after the reset. Frank decides tier/weekly trade-offs.
- Safe to leave unattended when `status.sh` says QUIESCENT: the pipeline self-bounds.

## Image activation (merged → deployed)

`bash $S/deploy-check.sh` says which layer lags. Run-side files (entrypoint, workflows,
commands, scripts, config) go live on `docker pull`; scheduler-side files only when the scheduler
container is recreated (compose does not auto-pull; a restart reuses the cached image). Recreate
only when idle, with the human-authored deploy files unchanged, and only when Frank has asked;
the script prints the exact commands. Publish can fail transiently on ghcr (`unknown blob`) or
apt 520: `gh run rerun <id> --failed`. MarketHawk's scheduler is a separate decision.

## Peer sessions

Two operator sessions can run at once. `ListAgents`; if a peer exists, agree a split per ticket
(one session owns a ticket's gates, branch and thread end to end; the other forwards findings)
and say which lane a newly filed ticket belongs to. After a peer restart or a long gap, re-check
board/PR state before assuming ownership. Budget half the host window for the peer.
