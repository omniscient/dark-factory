# Checkpoint the draft plan to the refine branch before architect review

**Issue:** #409

## Overview / Problem statement

`commands/dark-factory-plan.md`'s Phase 2 (PLAN WRITING) writes the implementation plan to
`docs/superpowers/plans/`, but nothing commits it until Phase 4 (PUBLISH) — after Phase 3's
architect-review cycle (up to 3 cycles against an Opus subagent) and Phase 3.5's
conformance-review cycle (up to 3 more reconcile cycles) have both run to completion. Plan is the
most expensive phase in the factory (~51% of token spend), and the architect-review cycle is its
most expensive sub-step.

`entrypoint.sh` gives every run a fresh `git clone` (`rm -rf "$CLONE_DIR"` then re-clone,
`entrypoint.sh:1007-1009`) with no persistent working tree across container dispatches. A
container killed by the session-window pause anywhere during Phase 3/3.5/4 therefore loses 100%
of that run's work, and the next dispatch starts Phase 2 from scratch. This happened twice for
#200 (2026-09-08): one run 06:56Z–08:03Z, the other 02:22Z–02:56Z, both killed in Phase 3 —
roughly 100 minutes of the most expensive phase with zero durable output.

This spec commits and pushes the plan at the end of Phase 2 as a **draft at a separate path**,
so a kill during Phase 3/3.5/4 leaves a resumable artifact, and so that nothing that looks for
"a plan" can find the draft.

## Requirements

1. **The checkpoint commits *and* pushes.** Only a commit that reaches `origin` before the kill is
   recoverable. It goes to the same `refine/issue-N-slug` branch the refine phase used
   (`setup-refine-branch`, `workflows/archon-dark-factory.yaml:366-381`, already checks that
   branch out for `intent == 'plan'`; no DAG change).

2. **A checkpoint push failure is non-fatal.** Log a warning and proceed into Phase 3 on the
   locally committed draft — the same fail-open convention as `scripts/push_gate_check.sh`,
   `scripts/oos_excise.sh` and `scripts/transfer_refine_artifacts.sh`. No retry loop. The
   checkpoint must never become a new way to lose a run.

3. **A draft is distinguished from a plan by its path, not by its content.** Drafts live in
   `docs/superpowers/plan-drafts/`; a plan exists only once a file is committed under
   `docs/superpowers/plans/`. Phase 4 moves the draft to the plan path in the commit that
   publishes it. Consequences, each of which is a requirement:
   - "Is there a plan for this issue" stays exactly today's test. `scripts/push_gate_check.sh`,
     the `plan-push-and-advance` node, `scheduler.sh`, `scripts/transfer_refine_artifacts.sh`,
     `scripts/factory_core/run_record.py` and `.claude/skills/operator/**` are **not modified**.
   - No code parses a marker. Any mistake in writing the draft (wrong directory, failed commit,
     failed push) leaves either no draft or no plan — it can never produce something the gate
     takes for a finished plan.
   - `plan-pending-review` is still applied only by `plan-push-and-advance`, only after a file
     under `docs/superpowers/plans/` is committed and verifiably pushed. `CLAUDE.md`'s label rule
     stays literally true and is not edited.

   This supersedes the brainstorm answer recorded in the pipeline comment ("teach
   `plan-push-and-advance` to recognize a draft marker"). Two review rounds found fail-open cases
   in every in-file-marker variant (working-tree vs committed blob, a finished plan quoting the
   marker, a mistyped marker, an older plan file shadowing the draft); path separation removes
   the class and touches no gate-adjacent code.

4. **Resume skips plan writing only.** A re-dispatched run that finds a usable draft enters
   Phase 3. Architect review (Phase 3), plan-vs-spec conformance (Phase 3.5) and publish
   (Phase 4) always run in full, with their cycle counters starting at 0. No verdict or cycle
   count is carried over from the killed run.

5. **No change to retry accounting, breaker, budgets or `gate_*`.** A run that ends with only a
   draft is, to every other component, exactly what a run with no plan is today.

6. **Non-goal:** Phase 1 does not special-case "a finished plan is already on the branch but
   `plan-pending-review` is missing". That state is ambiguous with a human-rejected plan awaiting
   revision; it keeps today's behavior (rewrite from Phase 2). Separate ticket if ever hit.

7. **Non-goal:** checkpointing the refine phase's spec draft. The issue frames it as optional and
   refine has no evidenced failure of this kind.

## Architecture / Approach

Everything below is in `commands/dark-factory-plan.md` unless stated otherwise.

### 1. Why `docs/superpowers/plan-drafts/` (and not `plans/<name>.draft.md`)

Every consumer of "a plan exists" selects by the directory prefix `docs/superpowers/plans/`, so
a draft *inside* that directory would be matched by all of them whatever its file name:

| Consumer | Pattern (at origin/main) | Sees `plan-drafts/`? |
|---|---|---|
| `plan-push-and-advance` (applies `plan-pending-review`) | `push_gate_check.sh "docs/superpowers/plans/"` — `workflows/archon-dark-factory.yaml:624` | no |
| `push_gate_check.sh` | `git diff -z --name-only "origin/main...REF" -- "$ARTIFACT_PREFIX"` — `scripts/push_gate_check.sh:66` | only if asked for that prefix |
| `transfer_refine_artifacts.sh` (copies onto `feat/`) | `for PREFIX in docs/superpowers/specs/ docs/superpowers/plans/` — `:44` | no |
| `push-and-pr` archive step | `push_gate_check.sh "docs/superpowers/plans/"` — `workflows/archon-dark-factory.yaml:1366` | no |
| `approve.sh plan` | `git diff --name-only … -- "docs/superpowers/plans/" \| grep -E '\.md$'` — `.claude/skills/operator/scripts/approve.sh:40` | no |
| `run_record.py` | existence of `push-result.json`, written only on the node's plan-found path — `:1291-1300` | no |
| `scheduler.sh` / `epic_autopilot.py` | labels and board column only | n/a |
| Blast-radius diff, docs-conformance | exclude `docs/superpowers/**` — `workflows/archon-dark-factory.yaml:1220`, `commands/dark-factory-docs-conformance.md:102,130` | excluded |

### 2. Phase 1 (LOAD): detect a draft

After `SPEC_FILE`, `REPO_ROOT` and `ISSUE_NUM` are bound (steps 6a and 7), add:

```bash
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
```

`push_gate_check.sh` is used unchanged, as a lookup with a different prefix argument; it returns
only a file this branch added that names this issue.

- `RESUMING_DRAFT=true`: skip Phase 2's writing and enter Phase 3 with `PLAN_FILE` bound to the
  draft. Before the first architect spawn, read the comments in `issue.json` and apply to the
  draft (edit `$PLAN_FILE` in place) any human feedback, and any architect or conformance
  feedback a previous run posted when it parked on `needs-discussion`. A fresh rewrite does this
  implicitly today by reading `issue.json`; without it a parked draft would be re-reviewed
  unchanged.
- Draft found but not resumable (empty, or the spec has a commit the draft does not descend from
  — the spec was re-refined or amended after the checkpoint): run Phase 2 in full and write the
  new draft to the **same path** (`$PLAN_FILE`), so the branch never carries two drafts.
  `SPEC_SHA` is empty when the spec came from the context pack; that counts as "not newer".
- No draft: Phase 2 as below.

The guard is topological (does the draft's last commit descend from the spec's last commit), not
chronological. A history rewritten underneath the draft is not detected; that is accepted because
resume never skips review — Phase 3 and 3.5 judge the draft against the current spec.

### 3. Phase 2 (PLAN WRITING): write to the drafts directory and checkpoint

Phase 2 saves the plan to `docs/superpowers/plan-drafts/YYYY-MM-DD-<feature>.md` (or the existing
draft path bound in §2) and binds that path to `PLAN_FILE`. Everything else Phase 2 mandates is
unchanged, including the `**Issue:** #<num>` line (#382) — it is what lets §2 find the draft.
Phases 3 and 3.5 keep reading and editing `$PLAN_FILE`; no file under `docs/superpowers/plans/`
is created or modified before Phase 4.

At the end of Phase 2, after self-review and before the first architect spawn, checkpoint:

```bash
BRANCH=$(git branch --show-current)
git add -- "$PLAN_FILE"
git commit -m "docs(#${ISSUE_NUM}): checkpoint draft plan" -- "$PLAN_FILE" || true
if ! timeout 30 git push -u origin "$BRANCH"; then
  echo "WARN: draft plan checkpoint push failed for #${ISSUE_NUM} — proceeding on the locally committed draft; this run is not kill-durable until a later push succeeds."
fi
```

`git commit … -- "$PLAN_FILE"` commits that one path only. The push is the agent's first in this
command; the `plan` node runs at side-effect level 5 with `denied_tools: []`
(`workflows/archon-dark-factory.yaml:471-473`), and the target is the run's own branch.

Run the same block again immediately before each of the two `needs-discussion` clean exits
(Phase 3 step 4 and Phase 3.5 step 8b), so the revised draft — not the pre-review one — is what
the operator sees and what the next dispatch resumes. This is also what the headless contract
asks for ("persist this phase's artifact before your final turn ends").

`SCOPE BOUNDARY` gains one line: documents under `docs/superpowers/plan-drafts/` (the draft).

### 4. Phase 4 (PUBLISH): promote the draft, then run the OOS gate

Insert a promote step **before** the existing OOS-gate step (step 4):

```bash
FINAL_FILE=$(bash "${REPO_ROOT}/dark-factory/scripts/push_gate_check.sh" "docs/superpowers/plans/" "$ISSUE_NUM")  # TARGET-PATH
[ -n "$FINAL_FILE" ] || FINAL_FILE="docs/superpowers/plans/$(basename -- "$PLAN_FILE")"
mkdir -p docs/superpowers/plans
git add -- "$PLAN_FILE"
git mv -f -- "$PLAN_FILE" "$FINAL_FILE"
git rm -r -q --ignore-unmatch -- docs/superpowers/plan-drafts/
git commit -m "docs(#${ISSUE_NUM}): add implementation plan"
PLAN_FILE="$FINAL_FILE"
```

- The plan path first exists in the commit that carries the reviewed content; the draft path
  ceases to exist in the same commit. `git rm -r` removes any other draft left on the branch.
- `FINAL_FILE` reuses the path of an earlier finished plan for this issue when one is on the
  branch (a rejected plan being revised), so the branch ends with one plan file. Without this
  `push_gate_check.sh` returns the first match in diff order — the older file.
- Order matters: `oos_excise.sh` judges the committed diff `origin/main...HEAD` against the
  allowlist `docs/superpowers/plans/ docs/superpowers/specs/ .archon/memory/`
  (`commands/dark-factory-plan.md:308`). Run before the promote it would excise the draft and
  report it as spillover; run after, the draft path nets out of the diff. The allowlist is
  **not** widened (`tests/test_command_issue_context_contract.py` pins it).
- The existing "Commit the plan" step becomes "commit any remaining change to `$PLAN_FILE`
  (normally none)". The summary comment and `refinement-status.md` use the final path.

A kill between architect approval and this commit re-enters Phase 3 from the draft on the next
dispatch — bounded extra review cost, not a correctness issue (Alternatives #2).

### 5. What every other component sees (no code change)

- **Kill or #212-class misreport with only a draft committed:** `plan-push-and-advance` finds no
  file under `plans/` and takes today's "no artifact" branch
  (`workflows/archon-dark-factory.yaml:707-733`): silent if `needs-discussion` is set, otherwise
  the existing `df-refine-failure` comment. No push by the node, no label, no
  `push-result.json`, so the run record says `artifact_present: false`. The comment's sentence
  "ended without producing a committed implementation plan" remains true; the next dispatch
  resumes the draft. `stage_plan` (`scheduler.sh:1411-1453`) re-dispatches under the existing
  `${ISSUE}:plan` retry counter, failure-signature and session-window-pause rules.
- **A draft cannot reach `main`:** `feat/` branches fork from `main` and receive only what
  `transfer_refine_artifacts.sh` copies (`specs/`, `plans/`); the factory never merges a refine
  branch. An operator can open a PR from one (spike tickets,
  `.claude/skills/operator/PLAYBOOK.md:59`); that path is covered by a CI test (below) that
  fails any PR that tracks a file under `docs/superpowers/plan-drafts/`.
- **Re-refine on a branch holding a draft:** the refine phase's OOS gate (allowlist `specs/`,
  `.archon/memory/`) removes it, exactly as it removes a plan there today.
- **Pre-existing, unchanged:** if an earlier finished plan is on the branch and the revising run
  dies before Phase 4, the node finds and labels the earlier plan. That is today's behavior and
  is not made better or worse here.

### 6. Operator path for cycle-capped plans

By the command's contract a plan parked by the architect or conformance cap is posted as a
comment and labelled `needs-discussion`, and the exit steps commit nothing
(`commands/dark-factory-plan.md:162-165`, `:242-268`); the node then stays silent. The comment,
the label and the node's silence are unchanged, and it stays true that no file under
`docs/superpowers/plans/` is committed. What changes is that the exit now commits and pushes
the revised draft under `plan-drafts/` (§3). Ways forward:

- (a) Remove `needs-discussion` (with feedback in a comment or the issue body). The next dispatch
  resumes the draft, applies the feedback, and runs full review.
- (b) Take the draft as the plan by hand: `git mv` it to `docs/superpowers/plans/`, amend, push,
  add `plan-pending-review`, then `approve.sh plan N --sha <sha> --lift-discussion`.

`.claude/skills/operator/PLAYBOOK.md:54` and `approve.sh:10` describe capped plans as carrying
both labels. Where that has happened it was the agent committing a plan against the contract;
`approve.sh plan` keeps working for those and, correctly, dies for a draft-only branch ("adds no
`docs/superpowers/plans/*.md`" / no gate label). Rewording the playbook is a human-only follow-up
(`.claude/skills/` is an adapter `hard_exclude_path`) and is not part of this ticket.

### 7. Documentation

`workflows/archon-dark-factory.yaml`, `CLAUDE.md` and `docs/triage-labels.md` are not edited, so
no workflow line citation moves. `scripts/docs_refs_check.py` only range-checks, so the citations
below must be re-derived by hand after the command file is edited:

- `docs/run-lifecycle.md`, section `dark-factory-plan`: describe the drafts directory, the
  Phase 2 checkpoint (the command now pushes a draft), Phase 1 resume, the Phase 4 promote and
  its position before the OOS gate; update every `commands/dark-factory-plan.md` range there and
  in the limits table (`:44-50`, `:54-89`, `:91-112`, `:114-168`, `:170-286`, `:288-378`,
  `:154-165`, `:240-268`). In "C1. Push gate", add one sentence: a draft under `plan-drafts/` is
  not an artifact for this gate.
- `docs/configuration.md`: the `commands/dark-factory-plan.md:179`, `:297`, `:298` citations and
  any other into that file.
- `docs/features.md`: the Planning row, and the `dark-factory-plan.md` citations in the
  "Context pack" and "Prompt rendering" rows (`:132`, `:144`).
- `docs/domain.md:31`: add `docs/superpowers/plan-drafts/` — plan drafts under review on a refine
  branch; never present on `main`.

## Acceptance criteria and tests

All tests are pytest files under `tests/` (CI: `python -m pytest tests/ -v`); no new
`tests/test_*.sh`, so `.github/workflows/ci.yml` does not change. Executing tests use the
fixture shape and POSIX `skipif` of `tests/test_push_gate_dag_exec.py` (bare origin + working
tree, real `scripts/push_gate_check.sh`).

New `tests/test_plan_command_checkpoint.py` — the Phase 1, Phase 2 and Phase 4 blocks are
extracted from `commands/dark-factory-plan.md` and run for real:

| # | Branch state | Action | Expected |
|---|---|---|---|
| 1 | spec only | Phase 2 block | one new commit touching only `plan-drafts/<f>`; remote branch at `HEAD`; `push_gate_check.sh plans/` prints nothing |
| 2 | spec only, push made to fail | Phase 2 block | commit exists locally, `WARN` printed, exit status 0 |
| 3 | spec + draft | Phase 1 block | `RESUMING_DRAFT=true`, `PLAN_FILE` = draft path |
| 4 | spec + draft, then a later commit to the spec | Phase 1 block | `RESUMING_DRAFT=false`, `PLAN_FILE` = draft path (rewrite in place) |
| 5 | spec + empty draft | Phase 1 block | `RESUMING_DRAFT=false` |
| 6 | spec only | Phase 1 block | `RESUMING_DRAFT=false`, `PLAN_FILE` unset |
| 7 | spec + draft with uncommitted edits | Phase 4 block | one commit; `plans/<f>` has the edited content; `git ls-files docs/superpowers/plan-drafts/` empty; `git diff --name-only origin/main...HEAD` lists only `specs/` and `plans/` paths; `oos_excise.sh` with the command's allowlist prints nothing |
| 8 | spec + older finished plan `plans/<old>` + draft | Phase 4 block | exactly one file under `plans/` (`<old>`, new content); no draft |
| 9 | spec + older finished plan + draft | `push_gate_check.sh plan-drafts/` and `plans/` | draft path and `<old>` respectively |
| 10 | spec + draft written but never committed (checkpoint commit failed) | Phase 4 block | same as row 7 |
| 11 | spec + committed draft | `oos_excise.sh` with the refine allowlist (`specs/`, `.archon/memory/`) | prints the draft path; `git ls-files docs/superpowers/plan-drafts/` empty |

Text assertions in the same file: Phase 2 names `docs/superpowers/plan-drafts/` and does not tell
the agent to save under `docs/superpowers/plans/`; the checkpoint block precedes
`## Phase 3: ARCHITECT REVIEW` and recurs before both `needs-discussion` exits; the resume text
states that Phase 3, Phase 3.5 and Phase 4 still run; in Phase 4 the promote block precedes the
`oos_excise.sh` line; the `oos_excise.sh` allowlist is unchanged; `SCOPE BOUNDARY` lists the
drafts directory.

Added to `tests/test_push_gate_dag_exec.py` (test only; the node is not modified): a branch whose
only plan-related commit is a draft under `plan-drafts/` → no `plan-pending-review` call, no
`push-result.json`; `df-refine-failure` comment when `needs-discussion` is absent, nothing when
present.

New `tests/test_no_plan_drafts_tracked.py`: `git ls-files docs/superpowers/plan-drafts/` is empty
(CI runs on pull requests to `main`, so a draft can never merge).

Unmodified and still passing: `tests/test_plan_command_render_prompt.py`,
`tests/test_command_issue_number_mandate.py`, `tests/test_command_headless_contract.py`,
`tests/test_command_issue_context_contract.py`, `tests/test_push_gate_dag.py`,
`tests/test_push_gate_check.py`, `tests/test_transfer_refine_artifacts.py`.
`git diff origin/main -- workflows/ scripts/push_gate_check.sh scripts/transfer_refine_artifacts.sh scheduler.sh`
is empty. `python scripts/docs_refs_check.py` passes.

## Alternatives considered

1. **In-file marker (`**Status:** draft — architect review pending`) plus a marker check in
   `plan-push-and-advance`** — the issue's own example and this spec's first two drafts.
   Rejected: the check sits on the label path and fails open (any miss means "finished plan").
   Review found four distinct misses — a working-tree read after the marker was removed locally,
   a finished plan quoting the marker, a mistyped marker, an older plan file returned ahead of
   the draft — plus a `pipefail`/SIGPIPE false negative in the obvious pipeline. It also changes
   gate-adjacent workflow code and shifts every workflow line citation in the docs.
2. **Checkpoint again after architect approval / after every review cycle.** Now safe (a draft
   can never be labelled) but still needs resume state (which phase, which cycle) to pay off.
   Deferred until Phase 3.5 losses are evidenced.
3. **`docs/superpowers/plans/<name>.draft.md`.** Rejected: every consumer in §1 matches the
   directory prefix, so all of them would need a file-name filter.
4. **Widen the plan OOS allowlist to include `plan-drafts/`** instead of promoting before the OOS
   gate. Rejected: loosens a gate's allowlist and a test pins it.
5. **Checkpoint the refine phase's spec draft.** Out of scope (Requirement 7).

## Open questions (non-blocking)

- File a follow-up for the pre-existing case in §5 (an earlier finished plan labelled when the
  revising run dies)? Moving the superseded plan aside in the checkpoint commit would close it.
- Reword `.claude/skills/operator/PLAYBOOK.md:54` (human-only).

## Assumptions (flagged)

- A session-window pause kills the container, so `plan-push-and-advance` does not run after it;
  if it does run, §5's first bullet applies.
- The run container's git credentials permit the agent's own `git push` to the refine branch (the
  bash nodes push from the same clone with the same remote).
- `commands/dark-factory-plan.md` at the repository root is the canonical file;
  `.archon/commands/` and `dark-factory/scripts/` in a live clone are `.git/info/exclude`d
  fallback copies (README "Self-contained fallbacks") and are not edited.
- The `.archon/memory/architecture.md` entry this refine run added for #409 ("add a marker check
  at the DAG-node call site") describes the rejected design and must be replaced on the branch
  with: a mid-phase draft artifact goes to a sibling directory no consumer's prefix matches and
  is `git mv`ed into place by the publishing commit; never distinguish draft from final by file
  content.
